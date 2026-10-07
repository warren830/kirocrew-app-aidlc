"""Notifications: the two courtesy channels, and the door that stays shut.

The interesting assertions are the negative ones. A notification must not carry evidence, must not become
a way to decide, and must not be able to fail the operation that produced it.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import fixtures as F
from test_handlers_auth import sv  # noqa: F401 - fixture


def _mod(studio):
    assert studio.notifications is not None, getattr(studio, "notifications__error", "module missing")
    return studio.notifications


class FakeHostBridge:
    """Only the two writes NotificationAdapter is allowed to make."""

    def __init__(self, *, notify_ok: bool = True, slack_ts: str | None = "ts-1") -> None:
        self.notify_ok = notify_ok
        self.slack_ts = slack_ts
        self.notified: list[dict[str, Any]] = []
        self.slack_calls: list[tuple[list[dict], str]] = []
        self.raise_on_notify = False
        self.raise_on_slack = False

    def notify(self, title: str, body: str, *, url: str, meta: dict | None = None) -> bool:
        if self.raise_on_notify:
            raise RuntimeError("host bus exploded")
        self.notified.append({"title": title, "body": body, "url": url, "meta": meta})
        return self.notify_ok

    async def slack_dm(self, blocks: list[dict], text: str) -> str | None:
        if self.raise_on_slack:
            raise RuntimeError("slack exploded")
        self.slack_calls.append((blocks, text))
        return self.slack_ts


class FakeSettings:
    def __init__(self, *, enabled: bool = True, muted: tuple[str, ...] = (), dashboard: bool = True) -> None:
        self._slack = {"enabled": enabled, "muted_repo_ids": list(muted)}
        self._notifications = {"dashboard": dashboard}

    def slack(self) -> dict[str, Any]:
        return dict(self._slack)

    def notifications(self) -> dict[str, Any]:
        return dict(self._notifications)


class FakeActivity:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> int:
        self.rows.append(kwargs)
        return len(self.rows)


class MemoryPrefs:
    """Just the preference half of Storage, which is all this module touches."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.fail = False

    def pref_get(self, key: str) -> Any:
        if self.fail:
            raise RuntimeError("storage down")
        return self.values.get(key)

    def pref_set(self, key: str, value: Any) -> None:
        if self.fail:
            raise RuntimeError("storage down")
        self.values[key] = value


def _adapter(studio, clock, *, host=None, settings=None, storage=None, activity=None):
    mod = _mod(studio)
    return mod.NotificationAdapter(
        host or FakeHostBridge(),
        storage or MemoryPrefs(),
        settings or FakeSettings(),
        activity or FakeActivity(),
        clock,
    )


def _card(**over: Any) -> dict[str, Any]:
    card = {
        "action_id": "a_0000000000000001",
        "type": "gate",
        "queue_type": "gate",
        "status": "Queued",
        "severity": "blocking",
        "repo": {"repo_id": "r_000000000001", "label": "ledger-export", "canonical_path": "/home/dev/ledger"},
        "space": "default",
        "intent": {"intent_dir": "260904-ledger-export", "intent_key": "260904-ledger-export", "slug": "ledger-export"},
        "stage": {"slug": "requirements-analysis", "phase": "inception"},
        "waiting_since": "2026-09-05T04:00:00Z",
        "headline": {"key": "action.gate.headline", "params": {}},
        "primary": {"decision": "approve", "label_key": "decision.approve.label"},
        "failure": None,
    }
    card.update(over)
    return card


def test_deep_link_identifies_and_authorises_nothing(studio, clock):
    adapter = _adapter(studio, clock)
    link = adapter.deep_link("a_1")
    assert link == "/apps/aidlc-studio?view=actions&action=a_1"
    # No token, no secret, no signature: a link cannot be an authorisation.
    assert "token" not in link and "secret" not in link


class RegisteredOnlyActivity(FakeActivity):
    """Refuses what the real projector refuses: a kind missing from ``activity.ACTIVITY_KINDS``."""

    def __init__(self, kinds: frozenset[str]) -> None:
        super().__init__()
        self.kinds = kinds

    async def record(self, **kwargs: Any) -> int:
        assert kwargs["kind"] in self.kinds, f"unregistered activity kind {kwargs['kind']!r}"
        return await super().record(**kwargs)


@pytest.mark.parametrize("notification", ["gate", "completion", "approval"])
def test_every_notification_records_a_registered_activity_kind(studio, clock, notification):
    """The fake above used to accept any kind, which hid that the real projector dropped them all."""
    activity = RegisteredOnlyActivity(frozenset(studio.activity.ACTIVITY_KINDS))
    adapter = _adapter(studio, clock, activity=activity)
    if notification == "gate":
        asyncio.run(adapter.notify("action", _card()))
    elif notification == "completion":
        asyncio.run(adapter.notify("completion", SimpleNamespace(
            repo_id="r_1", repo_label="ledger", intent_key="260904-x", intent_dir="260904-x", slug="x",
        )))
    else:
        asyncio.run(adapter.notify(
            "tool_approval", repo_id="r_1", repo_label="ledger", intent_key="260904-x", intent_label="x",
            slot_key="slot-1", approval={"tool": "Read", "tool_input": "cat a", "request_id": "q1"},
        ))
    assert [(row["kind"], row["params"]["kind"]) for row in activity.rows] == [
        ("notification.sent", notification),
    ]


def test_a_notification_lands_in_the_real_activity_projector(studio, clock, tmp_path):
    store = studio.storage.Storage(tmp_path / "studio.sqlite3", clock=clock, ids=None)
    store.open()
    try:
        projector = studio.activity.ActivityProjector(store, clock)
        adapter = _adapter(studio, clock, activity=projector)
        asyncio.run(adapter.notify("action", _card()))
        rows = store.select("activity")
        # Storage decodes the JSON columns on read.
        assert [(row["kind"], row["params_json"]["kind"]) for row in rows] == [
            ("notification.sent", "gate"),
        ]
    finally:
        store.close()


def test_a_gate_notifies_both_channels_once(studio, clock):
    host, activity, prefs = FakeHostBridge(), FakeActivity(), MemoryPrefs()
    adapter = _adapter(studio, clock, host=host, activity=activity, storage=prefs)
    first = asyncio.run(adapter.notify("action", _card()))
    assert first.dashboard is True and first.slack == "sent" and first.dedupe_hit is False
    assert len(host.notified) == 1 and len(host.slack_calls) == 1
    assert activity.rows and activity.rows[0]["kind"] == "notification.sent"
    assert activity.rows[0]["params"]["kind"] == "gate"

    second = asyncio.run(adapter.notify("action", _card()))
    assert second.dedupe_hit is True
    assert len(host.notified) == 1, "a card that stays open must not notify on every tick"
    assert len(host.slack_calls) == 1


def test_delivery_uncertainty_is_its_own_notification_class(studio, clock):
    host, prefs = FakeHostBridge(), MemoryPrefs()
    adapter = _adapter(studio, clock, host=host, storage=prefs)
    asyncio.run(adapter.notify("action", _card()))
    # The same action becoming uncertain is new information, so it notifies again — but only once.
    asyncio.run(adapter.notify("action", _card(status="ReconciliationRequired", queue_type="delivery_uncertain")))
    asyncio.run(adapter.notify("action", _card(status="ReconciliationRequired", queue_type="delivery_uncertain")))
    assert len(host.notified) == 2


@pytest.mark.parametrize("card_type", ["revision", "run", "resume", "prepare_commit", "budget_stop"])
def test_non_blocking_card_types_stay_silent(studio, clock, card_type):
    host = FakeHostBridge()
    adapter = _adapter(studio, clock, host=host)
    result = asyncio.run(adapter.notify("action", _card(type=card_type, queue_type=card_type)))
    assert result.dashboard is False and result.slack == "ineligible"
    assert host.notified == [] and host.slack_calls == []


def test_slack_respects_enablement_and_per_repo_mute(studio, clock):
    host = FakeHostBridge()
    off = _adapter(studio, clock, host=host, settings=FakeSettings(enabled=False))
    assert asyncio.run(off.notify("action", _card())).slack == "disabled"
    assert host.slack_calls == []

    muted = _adapter(studio, clock, host=host, settings=FakeSettings(enabled=True, muted=("r_000000000001",)))
    assert asyncio.run(muted.notify("action", _card())).slack == "muted"
    assert host.slack_calls == []


def test_a_slack_outage_never_fails_the_notification(studio, clock):
    host = FakeHostBridge(slack_ts=None)
    adapter = _adapter(studio, clock, host=host)
    result = asyncio.run(adapter.notify("action", _card()))
    assert result.dashboard is True and result.slack == "unavailable"


def test_storage_failure_degrades_to_a_duplicate_not_an_error(studio, clock):
    prefs = MemoryPrefs()
    prefs.fail = True
    adapter = _adapter(studio, clock, storage=prefs)
    # The dedupe ledger is advisory; losing it may cost a duplicate but must never raise.
    assert asyncio.run(adapter.notify("action", _card())).dashboard is True
    assert asyncio.run(adapter.notify("action", _card())).dedupe_hit is False


def test_the_dashboard_switch_silences_the_toast_and_nothing_else(studio, clock):
    host = FakeHostBridge()
    adapter = _adapter(studio, clock, host=host, settings=FakeSettings(dashboard=False))
    result = asyncio.run(adapter.notify("action", _card()))
    assert result.dashboard is False and host.notified == []
    assert result.slack == "sent" and len(host.slack_calls) == 1


def test_slack_blocks_carry_a_link_and_never_an_options_trailer(studio, clock):
    host = FakeHostBridge()
    adapter = _adapter(studio, clock, host=host)
    asyncio.run(adapter.notify("action", _card()))
    [(blocks, text)] = host.slack_calls
    encoded = json.dumps(blocks)
    # An [OPTIONS:] trailer would be turned into buttons whose clicks the host re-dispatches as user
    # turns on the AI-DLC session — a decision with none of Studio's guards behind it.
    assert "[OPTIONS" not in encoded and "action::" not in encoded
    assert "actions" in encoded and "a_0000000000000001" in encoded
    assert [b["type"] for b in blocks] == ["header", "context", "section", "section"]
    assert blocks[1]["elements"][0]["text"] == "ledger-export · requirements-analysis · waiting since 2026-09-05T04:00:00Z"
    assert text == "Approval needed: ledger-export" and "\n" not in text


def test_no_evidence_no_paths_and_no_secrets_reach_a_message(studio, clock):
    host = FakeHostBridge()
    adapter = _adapter(studio, clock, host=host)
    card = _card(
        failure={"summary": "ACP transport closed while token=abcdef1234567890 was in flight"},
        evidence={"artifacts": [{"relpath": "inception/requirements-analysis/requirements.md"}]},
        human_text="Approve",
    )
    asyncio.run(adapter.notify("action", card))
    encoded = json.dumps(host.notified) + json.dumps(host.slack_calls)
    assert "abcdef1234567890" not in encoded, "a credential-looking string must be redacted"
    assert "/home/dev/ledger" not in encoded, "the repository path is not a notification's business"
    assert "requirements.md" not in encoded, "artifact contents and names stay in Studio"
    assert "human_text" not in encoded


def test_a_host_that_raises_is_absorbed(studio, clock):
    host = FakeHostBridge()
    host.raise_on_notify = True
    adapter = _adapter(studio, clock, host=host)
    # HostBridge.notify swallows host failures itself; this proves the adapter does not reintroduce one.
    with pytest.raises(RuntimeError):
        host.notify("t", "b", url="/apps/aidlc-studio")
    host.raise_on_notify = False
    assert asyncio.run(adapter.notify("action", _card())).dashboard is True


def test_completion_notifies_once_and_links_to_the_intent(studio, clock):
    host, prefs = FakeHostBridge(), MemoryPrefs()
    adapter = _adapter(studio, clock, host=host, storage=prefs)
    summary = SimpleNamespace(
        repo_id="r_000000000001",
        repo_label="ledger-export",
        intent_key="260904-ledger-export",
        intent_dir="260904-ledger-export",
        slug="ledger-export",
    )
    first = asyncio.run(adapter.notify("completion", summary))
    assert first.dashboard is True and "view=intents" in first.deep_link
    assert asyncio.run(adapter.notify("completion", summary)).dedupe_hit is True
    assert len(host.notified) == 1


def test_a_tool_approval_notifies_once_per_approval_and_links_to_the_conversation(studio, clock):
    host, prefs = FakeHostBridge(), MemoryPrefs()
    adapter = _adapter(studio, clock, host=host, storage=prefs)
    wait = dict(repo_id="r_1", repo_label="devlake", intent_key="261001-servicenow-plugin",
                intent_label="servicenow-plugin", slot_key="aidlc-studio-r_1-261001-servicenow-plugin")
    approval = {"tool": "Locate the load-steering instructions", "tool_input": "grep -n steering SKILL.md",
                "request_id": "req-1"}
    first = asyncio.run(adapter.notify("tool_approval", **wait, approval=approval))
    assert first.dashboard is True
    assert "view=intents" in first.deep_link and "intent=261001-servicenow-plugin" in first.deep_link
    sent = host.notified[0]
    assert sent["title"] == "AI-DLC is waiting for your approval: servicenow-plugin"
    assert "grep -n steering SKILL.md" in sent["body"] and sent["meta"]["kind"] == "approval"
    assert asyncio.run(adapter.notify("tool_approval", **wait, approval=approval)).dedupe_hit is True
    # The next approval in the same conversation is a new wait.
    asyncio.run(adapter.notify("tool_approval", **wait, approval={**approval, "request_id": "req-2"}))
    assert len(host.notified) == 2


def test_an_undescribed_approval_notifies_again_in_a_later_turn(studio, clock):
    """No id and no text: the turn keeps one slot's approvals from collapsing into one key forever."""
    host = FakeHostBridge()
    adapter = _adapter(studio, clock, host=host, storage=MemoryPrefs())
    wait = dict(repo_id="r_1", repo_label="devlake", intent_key="261001-servicenow-plugin",
                intent_label="servicenow-plugin", slot_key="slot-1",
                approval={"tool": "", "tool_input": "", "request_id": ""})
    asyncio.run(adapter.notify("tool_approval", **wait, turn="2026-10-03T10:00:00Z"))
    assert asyncio.run(adapter.notify("tool_approval", **wait, turn="2026-10-03T10:00:00Z")).dedupe_hit is True
    asyncio.run(adapter.notify("tool_approval", **wait, turn="2026-10-03T10:05:00Z"))
    assert len(host.notified) == 2


def test_the_dedupe_ledger_is_bounded(studio, clock):
    mod = _mod(studio)
    prefs = MemoryPrefs()
    adapter = _adapter(studio, clock, storage=prefs)
    for index in range(mod.NotificationAdapter._DEDUPE_MAX + 25):
        clock.advance(1)
        asyncio.run(adapter.notify("action", _card(action_id=f"a_{index:016d}")))
    stored = prefs.values[mod.NotificationAdapter._DEDUPE_PREF]
    assert len(stored) <= mod.NotificationAdapter._DEDUPE_MAX


def test_announced_answers_whether_an_open_card_was_notified(studio, clock):
    adapter = _adapter(studio, clock, storage=MemoryPrefs())
    assert asyncio.run(adapter.announced("a_0000000000000001")) is False
    asyncio.run(adapter.notify("action", _card()))
    assert asyncio.run(adapter.announced("a_0000000000000001")) is True
    assert asyncio.run(adapter.announced("a_0000000000000002")) is False


# --------------------------------------------------------------------------- #
# what a scan announces: the reconciler through the real services
# --------------------------------------------------------------------------- #

GATE_INTENT = "260904-gate-demo"


def _scan(sv: Any, repo: Any) -> None:
    asyncio.run(sv.reconciler.scan_repo(sv.repos.get(repo.repo_id)))


def _record(root: Path) -> Path:
    return root / "aidlc" / "spaces" / "default" / "intents" / GATE_INTENT


def _announced(fake_host: Any, kind: str | None = None) -> list[tuple[str, str | None]]:
    """``(kind, action_id)`` of every dashboard toast so far, in order."""
    return [(note["meta"]["kind"], note["meta"].get("action_id")) for note in fake_host.notifications
            if kind is None or note["meta"]["kind"] == kind]


def _finished_repo(repo_builder: Any, status: str) -> Path:
    """An intent at the end of its plan (no next stage): whether it is finished is the state file's `Status`."""
    state = F.state_text(fields={"Status": status})
    return (repo_builder.with_engine("payload").with_workspace()
            .with_intent(GATE_INTENT, state=state, audit=F.gate_approved_audit("requirements-analysis")).build())


def _complete(root: Path) -> None:
    state = _record(root) / "aidlc-state.md"
    state.write_text(re.sub(r"^- \*\*Status\*\*:.*$", "- **Status**: Completed", state.read_text(), flags=re.M))


def test_a_scan_announces_each_new_gate_and_question_once(sv, fake_host, gate_repo, monkeypatch):
    repo = sv.repos.add(str(gate_repo), "ledger")
    _scan(sv, repo)
    first = _announced(fake_host)
    assert sorted(kind for kind, _ in first) == ["gate", "question"]
    assert all(action_id for _, action_id in first)

    rendered: list[str] = []
    render = sv.actions.card

    async def counting(rec, snap):
        rendered.append(rec.action_id)
        return await render(rec, snap)

    monkeypatch.setattr(sv.actions, "card", counting)
    _scan(sv, repo)
    assert _announced(fake_host) == first, "a card that stays open is announced once"
    assert rendered == [], "the ledger is read first, so an announced card is not even rendered"

    # The engine moves on: the next stage opens its own gate and asks its own question.
    record = _record(gate_repo)
    (record / "aidlc-state.md").write_text(F.state_text(
        marks={"requirements-analysis": "x", "user-stories": "?"},
        fields={"Current Stage": "user-stories", "Status": "Running", "Next Stage": "refined-mockups"},
    ))
    shard = next((record / "audit").glob("*.md"))
    shard.write_text(shard.read_text() + F.audit_block(
        "STAGE_AWAITING_APPROVAL", "2026-09-04T11:00:00Z", Stage="user-stories"))
    questions = record / "inception" / "user-stories" / "user-stories-questions.md"
    questions.parent.mkdir(parents=True, exist_ok=True)
    questions.write_text(F.questions_text(stage="user-stories", questions=[("Which store?", ["One", "Two"])]))
    _scan(sv, repo)
    _scan(sv, repo)
    later = _announced(fake_host)[len(first):]
    assert sorted(kind for kind, _ in later) == ["gate", "question"]
    assert not {action_id for _, action_id in later} & {action_id for _, action_id in first}


def test_an_archived_intents_cards_are_not_announced(sv, fake_host, gate_repo):
    repo = sv.repos.add(str(gate_repo), "ledger")
    asyncio.run(sv.sessions.set_archived(repo.repo_id, "default", GATE_INTENT, True))
    _scan(sv, repo)
    assert {rec.type for rec in asyncio.run(sv.actions.list_live(repo_id=repo.repo_id))} >= {"gate", "question"}
    assert _announced(fake_host) == []


def test_a_completion_is_announced_once_and_not_again_after_a_restart(sv, fake_host, repo_builder, studio):
    root = _finished_repo(repo_builder, "Running")
    repo = sv.repos.add(str(root), "ledger")
    _scan(sv, repo)
    assert _announced(fake_host) == []
    _complete(root)
    _scan(sv, repo)
    _scan(sv, repo)
    assert _announced(fake_host) == [("completion", None)]
    assert fake_host.notifications[0]["title"] == "AI-DLC finished: gate-demo"

    sv.reconciler = studio.reconciler.Reconciler(sv)  # a restart keeps the store and forgets the rest
    _scan(sv, repo)
    assert _announced(fake_host) == [("completion", None)]


def test_an_intent_already_completed_when_first_scanned_is_never_announced(sv, fake_host, repo_builder, studio):
    repo = sv.repos.add(str(_finished_repo(repo_builder, "Completed")), "ledger")
    _scan(sv, repo)
    _scan(sv, repo)
    sv.reconciler = studio.reconciler.Reconciler(sv)
    _scan(sv, repo)
    assert _announced(fake_host) == []


def test_restoring_an_archived_finished_intent_announces_nothing(sv, fake_host, repo_builder):
    """`Archived` → `Completed` is a change of view, not news: nobody watched this intent finish."""
    repo = sv.repos.add(str(_finished_repo(repo_builder, "Completed")), "ledger")
    asyncio.run(sv.sessions.set_archived(repo.repo_id, "default", GATE_INTENT, True))
    _scan(sv, repo)
    asyncio.run(sv.sessions.set_archived(repo.repo_id, "default", GATE_INTENT, False))
    _scan(sv, repo)
    assert _announced(fake_host) == []


@pytest.mark.parametrize("muted", [False, True])
def test_a_scan_announces_through_the_switches_it_is_given(sv, fake_host, gate_repo, muted):
    repo = sv.repos.add(str(gate_repo), "ledger")
    asyncio.run(sv.settings.put(
        {"notifications": {"dashboard": False}, "slack": {"enabled": True, "muted_repo_ids": [repo.repo_id]}}
        if muted else {"slack": {"enabled": True}}
    ))
    _scan(sv, repo)
    if muted:
        assert fake_host.notifications == [] and fake_host.slack_client.posts == []
    else:
        assert len(fake_host.notifications) == 2 and len(fake_host.slack_client.posts) == 2
