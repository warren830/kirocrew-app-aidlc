"""Tests for ``backend/studio/settings.py``.

Three properties matter more than the rest, and most of the file is about them:

* **The defaults are the contract.** ``DEFAULTS`` is compared against the literal from contracts §1.21,
  not against itself, so a "harmless" default change has to be made in the contract too.
* **A machine-lane feature can never be switched on** — not by a ``PUT`` (409, nothing stored), and not
  by editing the SQLite row by hand (forced back to the default on read). Both paths are exercised.
* **Validation names the key.** Every rejection is asserted on ``details.key`` and ``details.reason``,
  because "invalid settings" with no key is what the UI cannot act on.

Async methods run through ``asyncio.run`` (the convention the rest of this suite uses; no async plugin
is configured). The store is the real ``Storage`` — the module under test is ``settings``, and mocking
the row it writes would test the mock's idea of a preferences row.
"""

from __future__ import annotations

import asyncio
import copy
import json
import threading
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def M(studio):
    module = studio.settings
    assert module is not None, getattr(studio, "settings__error", "settings.py did not import")
    return module


@pytest.fixture
def K(studio):
    return studio.constants


@pytest.fixture
def store(studio, fake_ctx, K, clock, ids):
    st = studio.storage.Storage(Path(fake_ctx.data_dir) / K.DB_FILENAME, clock=clock, ids=ids)
    st.open()
    yield st
    st.close()


class Cap:
    """A ``HostCapability``-shaped value (attributes), which is what ``HostBridge`` returns."""

    def __init__(self, available: bool, reason: str | None = None) -> None:
        self.available = available
        self.reason = reason


class Bridge:
    """The slice of ``HostBridge`` (§1.11) ``SettingsService`` is allowed to touch."""

    def __init__(self, caps: dict | None = None, *, attached: bool = True, dict_form: bool = False) -> None:
        self._caps = {"slack": Cap(True), "subagents": Cap(True)} if caps is None else caps
        self._attached = attached
        self._dict_form = dict_form

    def attached(self) -> bool:
        return self._attached

    def capabilities(self) -> dict:
        if self._dict_form:
            return {k: {"available": v.available, "reason": v.reason} for k, v in self._caps.items()}
        return dict(self._caps)


class BrokenBridge:
    """A host whose shape Studio does not recognise (an older gateway, or a renamed attribute)."""

    def capabilities(self) -> dict:
        raise AttributeError("state has no attribute 'subagents'")


class ThreadRecordingStore:
    """Delegates to the real ``Storage`` and records which thread each call arrived on."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.threads: list[str] = []

    def pref_get(self, key):
        self.threads.append(threading.current_thread().name)
        return self._inner.pref_get(key)

    def pref_set(self, key, value):
        self.threads.append(threading.current_thread().name)
        return self._inner.pref_set(key, value)


@pytest.fixture
def svc(M, store, clock):
    return M.SettingsService(store, Bridge(), clock)


def run(coro):
    return asyncio.run(coro)


def stored(store, M):
    """The raw ``preferences`` row, exactly as another process would read it."""
    return store.pref_get(M.PREF_KEY)


# --------------------------------------------------------------------------- #
# defaults
# --------------------------------------------------------------------------- #


#: Pasted from contracts §1.21. Kept as a literal so a default change fails here rather than passing.
CONTRACT_DEFAULTS = {
    "locale": "auto",
    "density": "compact",
    "queue_organize": "priority",
    "global_concurrency_cap": 2,
    "slack": {"enabled": False, "muted_repo_ids": []},
    "night_window": {
        "enabled": False,
        "start_local": "22:00",
        "end_local": "06:00",
        "turn_cap": 40,
        "credit_cap": None,
    },
    "diagnostics": {"retention_days": 30, "export_include_human_text": False},
    "human_text_retention_days": 30,
    "advisor": {"enabled": True, "auto_draft_repo_ids": []},
    "installer": {"run_doctor_after_install": False},
    "notifications": {"dashboard": True},
}


def test_defaults_match_the_contract(M):
    assert M.DEFAULTS == CONTRACT_DEFAULTS


def test_schema_covers_every_default_leaf_and_nothing_else(M):
    """Every leaf is storable and every storable key exists in the defaults.

    A key in one table but not the other is either an unreachable validator or a value the user can see
    and never change.
    """

    def leaves(node, prefix=""):
        for key, value in node.items():
            dotted = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                yield from leaves(value, dotted)
            else:
                yield dotted

    assert sorted(leaves(M.DEFAULTS)) == sorted(M.SCHEMA)
    assert set(M.GROUPS) == {k for k, v in M.DEFAULTS.items() if isinstance(v, dict)}


def test_get_on_a_fresh_store_returns_the_defaults(svc, M):
    settings = run(svc.get())
    assert settings.values == M.DEFAULTS
    assert settings.updated_at is None


def test_returned_values_never_alias_the_module_defaults(svc, M):
    before = copy.deepcopy(M.DEFAULTS)
    settings = run(svc.get())
    settings.values["locale"] = "zh-CN"
    settings.values["slack"]["muted_repo_ids"].append("r_000000000001")
    assert M.DEFAULTS == before
    assert run(svc.get()).values == before
    assert svc.values()["slack"]["muted_repo_ids"] == []


def test_to_json_is_the_wire_body(svc):
    body = run(svc.get()).to_json()
    assert sorted(body) == ["capabilities", "settings", "updated_at", "versions"]
    body["settings"]["density"] = "comfortable"
    assert run(svc.get()).values["density"] == "compact"


def test_versions_block(svc, K):
    import kiro_crew

    assert run(svc.get()).versions == {
        "studio": K.APP_VERSION,
        "bundled_engine": K.BUNDLED_ENGINE_VERSION,
        "min_kirocrew": K.MIN_KIROCREW_VERSION,
        "host": kiro_crew.__version__,
    }


# --------------------------------------------------------------------------- #
# deep merge and persistence
# --------------------------------------------------------------------------- #


def test_put_deep_merges_and_keeps_siblings(svc, M):
    settings = run(svc.put({"slack": {"enabled": True}}))
    assert settings.values["slack"] == {"enabled": True, "muted_repo_ids": []}
    settings = run(svc.put({"slack": {"muted_repo_ids": ["r_000000000001"]}}))
    assert settings.values["slack"] == {"enabled": True, "muted_repo_ids": ["r_000000000001"]}
    # untouched groups still hold their defaults
    assert settings.values["diagnostics"] == M.DEFAULTS["diagnostics"]


def test_put_persists_only_the_overrides(svc, store, M, clock):
    run(svc.put({"locale": "zh-CN", "diagnostics": {"retention_days": 7}}))
    row = stored(store, M)
    assert row["values"] == {"locale": "zh-CN", "diagnostics": {"retention_days": 7}}
    assert row["updated_at"] == clock.iso()


def test_list_values_replace_rather_than_merge(svc):
    run(svc.put({"slack": {"muted_repo_ids": ["r_000000000001", "r_000000000002"]}}))
    settings = run(svc.put({"slack": {"muted_repo_ids": ["r_000000000003"]}}))
    assert settings.values["slack"]["muted_repo_ids"] == ["r_000000000003"]


def test_the_auto_draft_grant_round_trips_and_is_empty_on_a_fresh_install(svc):
    """``[]`` is a value, not a missing one: with no ids the Advisor runs only on a click (FR-ADV-001).

    So unchecking the last repository has to be storable and readable as an empty list. If ``[]`` fell
    back to the previous list — or to "no override, keep drafting" — a revoked grant would keep spawning
    subagents the owner has just said no to.
    """
    assert run(svc.get()).values["advisor"]["auto_draft_repo_ids"] == []
    ids = ["r_000000000001", "r_000000000002"]
    granted = run(svc.put({"advisor": {"auto_draft_repo_ids": ids}}))
    assert granted.values["advisor"]["auto_draft_repo_ids"] == ids
    assert run(svc.get()).values["advisor"]["auto_draft_repo_ids"] == ids
    revoked = run(svc.put({"advisor": {"auto_draft_repo_ids": []}}))
    assert revoked.values["advisor"]["auto_draft_repo_ids"] == []
    assert run(svc.get()).values["advisor"]["auto_draft_repo_ids"] == []


def test_the_auto_draft_grant_and_the_advisor_switch_are_written_independently(svc):
    """Two different decisions, so the per-leaf merge has to hold for both directions.

    The grant is toggled from a checkbox beside one repository and the switch from the Advisor section;
    either control re-sending the other's value from stale UI state would silently re-arm — or disarm —
    a feature its user never touched in that request.
    """
    run(svc.put({"advisor": {"enabled": False}}))
    settings = run(svc.put({"advisor": {"auto_draft_repo_ids": ["r_000000000001"]}}))
    assert settings.values["advisor"] == {"enabled": False, "auto_draft_repo_ids": ["r_000000000001"]}
    settings = run(svc.put({"advisor": {"enabled": True}}))
    assert settings.values["advisor"] == {"enabled": True, "auto_draft_repo_ids": ["r_000000000001"]}


def test_updated_at_moves_only_on_a_real_change(svc, clock):
    first = run(svc.put({"density": "comfortable"}))
    assert first.updated_at == clock.iso()
    clock.advance(60)
    again = run(svc.put({"density": "comfortable"}))
    assert again.updated_at == first.updated_at
    clock.advance(60)
    changed = run(svc.put({"density": "compact"}))
    assert changed.updated_at == clock.iso() != first.updated_at


def test_empty_patch_is_a_no_op(svc, store, M):
    settings = run(svc.put({}))
    assert settings.values == M.DEFAULTS
    assert stored(store, M) is None


def test_get_rereads_a_row_changed_out_of_band(svc, store, M):
    store.pref_set(M.PREF_KEY, {"values": {"locale": "en-US"}, "updated_at": "2026-01-01T00:00:00Z"})
    settings = run(svc.get())
    assert settings.values["locale"] == "en-US"
    assert settings.updated_at == "2026-01-01T00:00:00Z"


def test_a_second_service_on_the_same_store_sees_the_write(M, store, clock):
    writer = M.SettingsService(store, Bridge(), clock)
    reader = M.SettingsService(store, Bridge(), clock)
    run(writer.put({"global_concurrency_cap": 5}))
    assert run(reader.get()).values["global_concurrency_cap"] == 5


@pytest.mark.parametrize(
    "row,expect",
    [
        ("not-an-object", {}),
        (17, {}),
        ({"values": "not-an-object"}, {}),
        # the invalid neighbour is dropped, the valid one is kept
        ({"values": {"locale": "klingon", "density": "comfortable"}}, {"density": "comfortable"}),
        ({"values": {"global_concurrency_cap": 99}}, {}),
        ({"values": {"unknown_key": 1, "human_text_retention_days": 5}}, {"human_text_retention_days": 5}),
        # a bare override map (no "values" wrapper) is still read
        ({"locale": "en-US"}, {"locale": "en-US"}),
    ],
)
def test_unreadable_stored_shapes_degrade_to_defaults(svc, store, M, row, expect):
    """A row of the wrong shape, or with a value no longer valid, must not break the read path."""
    store.pref_set(M.PREF_KEY, row)
    values = run(svc.get()).values
    assert values == {**M.DEFAULTS, **expect}
    assert "unknown_key" not in values


def test_a_put_rewrites_a_dirty_row_clean(svc, store, M):
    store.pref_set(M.PREF_KEY, {"values": {"stale_key": True, "locale": "en-US"}, "updated_at": "x"})
    run(svc.put({"density": "comfortable"}))
    assert stored(store, M)["values"] == {"locale": "en-US", "density": "comfortable"}


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #


def err(excinfo):
    return excinfo.value.code, excinfo.value.details


@pytest.mark.parametrize(
    "patch,key,reason",
    [
        ({"nope": 1}, "nope", "unknown_key"),
        ({"slack": {"nope": 1}}, "slack.nope", "unknown_key"),
        ({"slack": True}, "slack", "type"),
        ({"locale": "klingon"}, "locale", "not_allowed"),
        ({"locale": 5}, "locale", "type"),
        ({"density": "cosy"}, "density", "not_allowed"),
        ({"queue_organize": "newest"}, "queue_organize", "not_allowed"),
        ({"global_concurrency_cap": 0}, "global_concurrency_cap", "range"),
        ({"global_concurrency_cap": 9}, "global_concurrency_cap", "range"),
        ({"global_concurrency_cap": True}, "global_concurrency_cap", "type"),
        ({"global_concurrency_cap": "2"}, "global_concurrency_cap", "type"),
        ({"global_concurrency_cap": 2.5}, "global_concurrency_cap", "type"),
        ({"human_text_retention_days": 0}, "human_text_retention_days", "range"),
        ({"human_text_retention_days": 31}, "human_text_retention_days", "range"),
        ({"diagnostics": {"retention_days": 90}}, "diagnostics.retention_days", "range"),
        ({"diagnostics": {"export_include_human_text": "yes"}}, "diagnostics.export_include_human_text", "type"),
        ({"advisor": {"enabled": 1}}, "advisor.enabled", "type"),
        ({"installer": {"run_doctor_after_install": None}}, "installer.run_doctor_after_install", "type"),
        ({"notifications": {"dashboard": "on"}}, "notifications.dashboard", "type"),
        ({"night_window": {"start_local": "9:00"}}, "night_window.start_local", "format"),
        ({"night_window": {"start_local": "24:00"}}, "night_window.start_local", "format"),
        ({"night_window": {"end_local": "22:00:00"}}, "night_window.end_local", "format"),
        ({"night_window": {"end_local": 2200}}, "night_window.end_local", "type"),
        ({"night_window": {"turn_cap": 0}}, "night_window.turn_cap", "range"),
        ({"night_window": {"turn_cap": 100000}}, "night_window.turn_cap", "range"),
        ({"slack": {"muted_repo_ids": "r_1"}}, "slack.muted_repo_ids", "type"),
        ({"slack": {"muted_repo_ids": [1]}}, "slack.muted_repo_ids", "type"),
        ({"slack": {"muted_repo_ids": [""]}}, "slack.muted_repo_ids", "type"),
        ({"advisor": {"auto_draft_repo_ids": "r_000000000001"}}, "advisor.auto_draft_repo_ids", "type"),
        ({"advisor": {"auto_draft_repo_ids": [1]}}, "advisor.auto_draft_repo_ids", "type"),
        ({"advisor": {"auto_draft_repo_ids": [""]}}, "advisor.auto_draft_repo_ids", "type"),
    ],
)
def test_put_rejects_bad_values_by_key(svc, patch, key, reason):
    with pytest.raises(Exception) as excinfo:
        run(svc.put(patch))
    code, details = err(excinfo)
    assert code == "bad_body"
    assert excinfo.value.status == 400
    assert details["key"] == key
    assert details["reason"] == reason


def test_muted_repo_ids_is_bounded_by_max_repos(svc, K):
    ok = [f"r_{i:012x}" for i in range(K.MAX_REPOS)]
    assert run(svc.put({"slack": {"muted_repo_ids": ok}})).values["slack"]["muted_repo_ids"] == ok
    with pytest.raises(Exception) as excinfo:
        run(svc.put({"slack": {"muted_repo_ids": ok + ["r_ffffffffffff"]}}))
    assert err(excinfo)[1] == {
        "key": "slack.muted_repo_ids",
        "reason": "too_many",
        "problems": ["slack.muted_repo_ids: too_many"],
        "expected": f"list of at most {K.MAX_REPOS} repo ids",
    }


def test_the_auto_draft_grant_is_bounded_by_max_repos(svc, K):
    """A grant per registered repository is the ceiling. A longer list is a client bug, and one that
    matters here more than for a mute list: every extra id is a repository whose cards may spawn
    subagents nobody asked for."""
    ok = [f"r_{i:012x}" for i in range(K.MAX_REPOS)]
    granted = run(svc.put({"advisor": {"auto_draft_repo_ids": ok}}))
    assert granted.values["advisor"]["auto_draft_repo_ids"] == ok
    with pytest.raises(Exception) as excinfo:
        run(svc.put({"advisor": {"auto_draft_repo_ids": ok + ["r_ffffffffffff"]}}))
    assert err(excinfo)[1] == {
        "key": "advisor.auto_draft_repo_ids",
        "reason": "too_many",
        "problems": ["advisor.auto_draft_repo_ids: too_many"],
        "expected": f"list of at most {K.MAX_REPOS} repo ids",
    }


def test_a_rejected_grant_leaves_the_advisor_switch_beside_it_unchanged(svc, store, M):
    """All-or-nothing, on the request a Settings pane is most likely to send as one group.

    The pane sends ``advisor`` as a whole, so a bad id in the list arrives next to a perfectly valid
    ``enabled``. Storing the good half would turn the Advisor off *and* answer 400, leaving the owner with
    no way to tell which half Studio kept.
    """
    with pytest.raises(Exception) as excinfo:
        run(svc.put({"advisor": {"enabled": False, "auto_draft_repo_ids": ["r_000000000001", ""]}}))
    assert err(excinfo)[1]["key"] == "advisor.auto_draft_repo_ids"
    assert stored(store, M) is None
    assert run(svc.get()).values["advisor"] == {"enabled": True, "auto_draft_repo_ids": []}


def test_rejected_put_stores_nothing(svc, store, M):
    with pytest.raises(Exception):
        run(svc.put({"density": "comfortable", "locale": "klingon"}))
    assert stored(store, M) is None
    assert run(svc.get()).values["density"] == "compact"


def test_details_report_every_problem_in_request_order(svc):
    with pytest.raises(Exception) as excinfo:
        run(svc.put({"locale": "klingon", "global_concurrency_cap": 99, "nope": True}))
    details = err(excinfo)[1]
    assert details["key"] == "locale"
    assert details["problems"] == [
        "locale: not_allowed",
        "global_concurrency_cap: range",
        "nope: unknown_key",
    ]
    assert details["expected"] == "one of: auto, en-US, zh-CN"


def test_non_object_patch(svc):
    with pytest.raises(Exception) as excinfo:
        run(svc.put(["locale", "zh-CN"]))
    code, details = err(excinfo)
    assert code == "bad_body"
    assert details == {"reason": "not_an_object"}


def test_validate_is_pure_and_reports_dotted_keys(svc, M):
    assert svc.validate(M.DEFAULTS) == []
    assert svc.validate({"locale": "zh-CN", "slack": {"enabled": True}}) == []
    assert svc.validate({"slack": {"muted_repo_ids": [2]}, "bogus": 1}) == [
        "slack.muted_repo_ids: type",
        "bogus: unknown_key",
    ]
    assert svc.validate("nope") == ["settings: not_an_object"]
    assert svc.validate({"night_window": {"enabled": True, "credit_cap": 100}}) == [
        "night_window.enabled: machine_lane_unavailable",
        "night_window.credit_cap: credits_unobservable",
    ]


def test_valid_values_round_trip(svc, M):
    patch = {
        "locale": "zh-CN",
        "density": "comfortable",
        "queue_organize": "repo",
        "global_concurrency_cap": 8,
        "slack": {"enabled": True, "muted_repo_ids": ["r_000000000001"]},
        "night_window": {"enabled": False, "start_local": "00:00", "end_local": "23:59", "turn_cap": 1, "credit_cap": None},
        "diagnostics": {"retention_days": 1, "export_include_human_text": True},
        "human_text_retention_days": 30,
        "advisor": {"enabled": False, "auto_draft_repo_ids": ["r_000000000001"]},
        "installer": {"run_doctor_after_install": True},
        "notifications": {"dashboard": False},
    }
    assert run(svc.put(patch)).values == patch
    assert run(svc.get()).values == patch


# --------------------------------------------------------------------------- #
# machine-lane refusal
# --------------------------------------------------------------------------- #


def test_enabling_the_night_window_is_refused_with_409(svc, store, M):
    with pytest.raises(Exception) as excinfo:
        run(svc.put({"night_window": {"enabled": True}}))
    code, details = err(excinfo)
    assert (code, excinfo.value.status) == ("machine_lane_unavailable", 409)
    assert details == {
        "key": "night_window.enabled",
        "capability": "night_window",
        "reason": M.REASON_MACHINE_LANE,
    }
    assert stored(store, M) is None
    assert run(svc.get()).values["night_window"]["enabled"] is False


def test_a_refused_patch_stores_none_of_its_other_keys(svc, store, M):
    with pytest.raises(Exception):
        run(svc.put({"density": "comfortable", "night_window": {"turn_cap": 5, "enabled": True}}))
    assert stored(store, M) is None
    values = run(svc.get()).values
    assert values["density"] == "compact"
    assert values["night_window"]["turn_cap"] == 40


def test_setting_a_credit_cap_is_refused_with_409(svc, store, M):
    with pytest.raises(Exception) as excinfo:
        run(svc.put({"night_window": {"credit_cap": 100}}))
    code, details = err(excinfo)
    assert (code, excinfo.value.status) == ("machine_lane_unavailable", 409)
    assert details == {
        "key": "night_window.credit_cap",
        "capability": "credit_cap",
        "reason": M.REASON_CREDITS,
    }
    assert stored(store, M) is None


def test_truthy_lookalikes_are_refused_too(svc):
    """``0 == False`` in Python; "off" must be identity, not equality."""
    for value in (1, "true", [], {}, 0.0):
        with pytest.raises(Exception) as excinfo:
            run(svc.put({"night_window": {"enabled": value}}))
        assert excinfo.value.code == "machine_lane_unavailable"


def test_the_capability_refusal_beats_a_validation_error(svc):
    with pytest.raises(Exception) as excinfo:
        run(svc.put({"locale": "klingon", "night_window": {"enabled": True}}))
    assert excinfo.value.code == "machine_lane_unavailable"


def test_writing_the_safe_value_explicitly_is_allowed(svc, store, M):
    settings = run(svc.put({"night_window": {"enabled": False, "credit_cap": None, "turn_cap": 12}}))
    assert settings.values["night_window"] == {
        "enabled": False,
        "start_local": "22:00",
        "end_local": "06:00",
        "turn_cap": 12,
        "credit_cap": None,
    }
    assert stored(store, M)["values"]["night_window"]["turn_cap"] == 12


def test_locked_keys_are_forced_even_if_an_override_slips_past_the_reader(M):
    """The second line of defence, asserted directly.

    Every reader in the process goes through ``_effective``, so "the night window is off" must not
    depend on the decoder having dropped the value first — one future caller that merges an override
    dict some other way would otherwise re-open the machine lane.
    """
    values = M._effective({"night_window": {"enabled": True, "credit_cap": 9, "turn_cap": 3}})
    assert values["night_window"]["enabled"] is False
    assert values["night_window"]["credit_cap"] is None
    assert values["night_window"]["turn_cap"] == 3


def test_a_hand_edited_row_cannot_arm_the_machine_lane(svc, store, M):
    """The refusal is not only in ``put``: ``sqlite3`` is on every developer's machine."""
    store.pref_set(
        M.PREF_KEY,
        {"values": {"night_window": {"enabled": True, "credit_cap": 500, "turn_cap": 9}}, "updated_at": "x"},
    )
    night = run(svc.get()).values["night_window"]
    assert night["enabled"] is False
    assert night["credit_cap"] is None
    assert night["turn_cap"] == 9  # the harmless neighbour is kept
    # and the next write drops the forbidden keys from the row for good
    run(svc.put({"density": "comfortable"}))
    assert stored(store, M)["values"] == {"night_window": {"turn_cap": 9}, "density": "comfortable"}


# --------------------------------------------------------------------------- #
# capabilities
# --------------------------------------------------------------------------- #


def test_capability_block_shape_and_order(svc, M):
    caps = run(svc.get()).capabilities
    assert list(caps) == list(M.CAPABILITY_NAMES)
    for entry in caps.values():
        assert sorted(entry) == ["available", "reason"]


def test_machine_lane_capabilities_are_always_unavailable(svc, M):
    caps = run(svc.get()).capabilities
    assert caps["night_window"] == {"available": False, "reason": "machine_lane_unavailable"}
    assert caps["credit_cap"] == {"available": False, "reason": "credits_unobservable"}
    assert caps["slack_quick_actions"] == {"available": False, "reason": "host_seam_unavailable"}
    assert caps["grouped_answers"] == {"available": True, "reason": None}


def test_grouped_answers_is_verified_and_only_a_constant_can_disable_it(M, K, store, clock, monkeypatch):
    assert K.GROUPED_ANSWERS_VERIFIED is True
    svc = M.SettingsService(store, Bridge(), clock)
    assert svc.capabilities()["grouped_answers"] == {"available": True, "reason": None}
    monkeypatch.setattr(K, "GROUPED_ANSWERS_VERIFIED", False)
    assert svc.capabilities()["grouped_answers"] == {"available": False, "reason": "s1_s2_unverified"}
    # it is not a preference: no key in the schema can reach it
    assert not [k for k in M.SCHEMA if "grouped" in k]


def test_host_backed_capabilities_follow_the_bridge(M, store, clock):
    svc = M.SettingsService(store, Bridge(), clock)
    caps = svc.capabilities()
    assert caps["slack"] == {"available": True, "reason": None}
    assert caps["advisor"] == {"available": True, "reason": None}

    svc = M.SettingsService(store, Bridge({"slack": Cap(False, "slack_not_configured"), "subagents": Cap(False)}), clock)
    caps = svc.capabilities()
    assert caps["slack"] == {"available": False, "reason": "slack_not_configured"}
    assert caps["advisor"] == {"available": False, "reason": M.REASON_HOST_UNAVAILABLE}


def test_dict_shaped_capabilities_are_accepted(M, store, clock):
    svc = M.SettingsService(store, Bridge(dict_form=True), clock)
    assert svc.capabilities()["slack"] == {"available": True, "reason": None}


def test_no_host_and_detached_host(M, store, clock):
    for host in (None, Bridge(attached=False)):
        caps = M.SettingsService(store, host, clock).capabilities()
        assert caps["slack"] == {"available": False, "reason": M.REASON_HOST_NOT_ATTACHED}
        assert caps["advisor"] == {"available": False, "reason": M.REASON_HOST_NOT_ATTACHED}


def test_an_unrecognised_host_shape_never_raises(M, store, clock):
    caps = M.SettingsService(store, BrokenBridge(), clock).capabilities()
    assert caps["slack"] == {"available": False, "reason": M.REASON_HOST_UNKNOWN}
    # a bridge that simply has no entry for the capability is the same answer
    caps = M.SettingsService(store, Bridge({"slack": Cap(True)}), clock).capabilities()
    assert caps["slack"]["available"] is True
    assert caps["advisor"] == {"available": False, "reason": M.REASON_HOST_UNKNOWN}
    # and so is a bridge whose entry is neither a mapping nor a HostCapability
    caps = M.SettingsService(store, Bridge({"slack": "yes", "subagents": None}), clock).capabilities()
    assert caps["slack"] == {"available": False, "reason": M.REASON_HOST_UNKNOWN}


def test_advisor_capability_is_not_the_advisor_preference(M, store, clock):
    """Turning the Advisor off is a choice; the capability describes whether the seam exists at all."""
    svc = M.SettingsService(store, Bridge(), clock)
    settings = run(svc.put({"advisor": {"enabled": False}}))
    assert settings.values["advisor"]["enabled"] is False
    assert settings.capabilities["advisor"]["available"] is True


def test_the_real_dashboard_state_is_not_consulted_directly(M, store, clock, fake_host):
    """A ``DashboardState`` is not a ``HostBridge``: it has no ``capabilities()``, so every host-backed
    capability must read as unknown rather than crash a settings read."""
    caps = M.SettingsService(store, fake_host, clock).capabilities()
    assert caps["slack"] == {"available": False, "reason": M.REASON_HOST_UNKNOWN}


# --------------------------------------------------------------------------- #
# sync accessors and threading
# --------------------------------------------------------------------------- #


def test_sync_accessors_serve_defaults_before_load_then_the_stored_values(M, store, clock):
    store.pref_set(
        M.PREF_KEY,
        {
            "values": {
                "global_concurrency_cap": 4,
                "human_text_retention_days": 3,
                "slack": {"enabled": True, "muted_repo_ids": ["r_000000000001"]},
                "advisor": {"auto_draft_repo_ids": ["r_000000000001"]},
                "installer": {"run_doctor_after_install": True},
            },
            "updated_at": "2026-09-04T10:00:00Z",
        },
    )
    svc = M.SettingsService(store, Bridge(), clock)
    assert svc.loaded() is False
    assert svc.global_concurrency_cap() == 2
    assert svc.human_text_retention_days() == 30
    # The reconciler reads the auto-draft grant through this accessor on the loop, and a tick can land
    # before `load()` has run. Serving the default there means "no grant yet" — a pre-draft delayed by
    # one tick, never one spawned for a repository whose owner may not have granted anything.
    assert svc.advisor() == {"enabled": True, "auto_draft_repo_ids": []}

    run(svc.get())
    assert svc.loaded() is True
    assert svc.global_concurrency_cap() == 4
    assert svc.human_text_retention_days() == 3
    assert svc.slack() == {"enabled": True, "muted_repo_ids": ["r_000000000001"]}
    assert svc.installer() == {"run_doctor_after_install": True}
    assert svc.advisor() == {"enabled": True, "auto_draft_repo_ids": ["r_000000000001"]}
    assert svc.diagnostics() == {"retention_days": 30, "export_include_human_text": False}
    assert svc.notifications() == {"dashboard": True}
    assert svc.locale() == "auto"


def test_accessors_hand_out_copies(svc):
    run(svc.get())
    svc.slack()["muted_repo_ids"].append("r_000000000001")
    svc.values()["locale"] = "zh-CN"
    assert svc.slack()["muted_repo_ids"] == []
    assert svc.locale() == "auto"


def test_put_refreshes_the_cache_for_sync_readers(svc):
    run(svc.put({"global_concurrency_cap": 6}))
    assert svc.global_concurrency_cap() == 6


def test_storage_is_only_touched_from_a_worker_thread(M, store, clock):
    recorder = ThreadRecordingStore(store)
    svc = M.SettingsService(recorder, Bridge(), clock)
    run(svc.get())
    run(svc.put({"locale": "en-US"}))
    assert recorder.threads, "settings never read the preferences row"
    assert threading.current_thread().name not in recorder.threads


def test_concurrent_puts_do_not_lose_a_key(M, store, clock):
    """Two owners saving different keys at the same time must both survive the read-modify-write."""
    svc = M.SettingsService(store, Bridge(), clock)

    async def both():
        await asyncio.gather(
            svc.put({"locale": "zh-CN"}),
            svc.put({"density": "comfortable"}),
            svc.put({"global_concurrency_cap": 7}),
        )
        return await svc.get()

    values = run(both()).values
    assert (values["locale"], values["density"], values["global_concurrency_cap"]) == ("zh-CN", "comfortable", 7)


def test_the_row_is_plain_json(svc, store, M, fake_ctx, K):
    """Whatever is stored must survive a JSON round trip: the row is read by other processes too."""
    run(svc.put({"locale": "zh-CN", "slack": {"muted_repo_ids": ["r_000000000001"]}}))
    row = stored(store, M)
    assert json.loads(json.dumps(row)) == row
    assert (Path(fake_ctx.data_dir) / K.DB_FILENAME).exists()
