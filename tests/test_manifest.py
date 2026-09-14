"""``app.json``, ``agents/advisor.json`` and the payload manifest (§4.3).

The manifest is the only thing the gateway reads before it will run a single line of Studio's code, and
almost every field in it is a security decision rather than metadata:

* ``permissions.api`` is the exact set of host paths an app *token* may reach. Two prefixes are
  correct — Studio's own routes and ``/api/chat``, which the **browser** uses for the human lane. A
  third prefix (the reviewed draft had ``/api/ask-question``) would widen the token's reach for a
  feature the backend serves itself (review R13/C27).
* ``permissions.events`` must equal the names ``events.py`` publishes, because the host's event bus
  raises ``PermissionError`` for an undeclared name — the app would look fine and simply stop
  notifying the dashboard.
* ``spawn`` and ``storage`` are the advisor and the durable store; ``network`` and ``cron`` are false
  because Studio has no reason to reach the internet and no unattended lane in v1 (there is no machine
  lane, S12).
* Every version-derived value is asserted **against the payload manifest**, never against a literal in
  the test. A test that hard-codes ``"2.6.2"`` passes forever after a bad payload bump; a test that
  compares three files to each other fails the moment they disagree (C21/review P03).

The validation itself is the host's own ``AppManifest.validate``, so a field the gateway would reject
at install time fails here instead of on a user's machine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent
APP_JSON = APP_ROOT / "app.json"
ADVISOR_JSON = APP_ROOT / "agents" / "advisor.json"
PAYLOAD_MANIFEST = APP_ROOT / "payload" / "manifest.json"

#: §4.3, verbatim. Compared as equality, not containment: an added prefix is as much a finding as a
#: missing one, because each entry widens what an app token can call.
EXPECTED_API_PREFIXES = ["/api/apps/aidlc-studio", "/api/chat"]
EXPECTED_EVENTS = ["aidlc-studio:action", "aidlc-studio:repo", "aidlc-studio:transaction"]


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(APP_JSON.read_text("utf-8"))


@pytest.fixture(scope="module")
def payload_manifest(studio):
    return studio.installer.PayloadManifest.load(PAYLOAD_MANIFEST)


# --------------------------------------------------------------------------- #
# the host's own verdict
# --------------------------------------------------------------------------- #


def test_the_host_accepts_the_manifest_with_no_complaints(raw):
    """``validate`` is what runs at install time; anything it returns is an install that fails."""
    from kiro_crew.apps.manifest import AppManifest

    assert AppManifest.from_dict(raw).validate(app_root=APP_ROOT) == []


def test_identity_matches_the_constants_module(raw, studio):
    assert raw["name"] == studio.constants.APP_NAME
    assert raw["version"] == studio.constants.APP_VERSION
    assert raw["minKiroCrewVersion"] == studio.constants.MIN_KIROCREW_VERSION
    assert studio.constants.MIN_KIROCREW_VERSION == "0.3.0"


def test_the_kirocrew_floor_is_the_one_the_bootstrap_supports(raw):
    """0.3.0 is only honest because ``backend/routes.py`` registers its own namespace (C26/A10).

    If the floor is ever raised, that bootstrap becomes dead weight; if the bootstrap is ever removed,
    this floor becomes a lie and the app fails to load on 0.3.0 with a ``ModuleNotFoundError``.
    """
    text = (APP_ROOT / "backend" / "routes.py").read_text("utf-8")
    assert "_aidlc_studio_backend" in text
    assert raw["minKiroCrewVersion"] == "0.3.0"


# --------------------------------------------------------------------------- #
# hooks and UI entry
# --------------------------------------------------------------------------- #


def test_the_three_backend_hooks_point_at_files_that_exist(raw):
    hooks = raw["backend"]["hooks"]
    assert hooks == {
        "routes": "backend.routes:register_routes",
        "on_startup": "backend.hooks:on_startup",
        "on_shutdown": "backend.hooks:on_shutdown",
    }
    for spec in hooks.values():
        dotted, _, name = spec.partition(":")
        path = APP_ROOT / (dotted.replace(".", "/") + ".py")
        assert path.is_file(), path
        assert f"def {name}" in path.read_text("utf-8"), spec


def test_the_routes_hook_declares_no_routes_of_its_own(raw):
    """Routes come from ``register_routes`` at enable time, so a static ``routes`` list would shadow it."""
    assert "routes" not in raw or isinstance(raw.get("routes"), (list, type(None)))
    assert raw["backend"].get("routes") in (None, [], {})


def test_the_ui_entry_is_the_committed_bundle(raw):
    assert raw["ui"]["entry"] == "dist/index.mjs"
    bundle = APP_ROOT / "ui" / raw["ui"]["entry"]
    assert bundle.is_file(), "ui/dist/index.mjs must be committed (D7); run the Vite build"
    assert bundle.stat().st_size > 0


def test_the_single_ui_page_is_the_apps_own_route(raw):
    pages = raw["ui"]["pages"]
    assert len(pages) == 1
    assert pages[0]["route"] == "/apps/aidlc-studio"
    assert (APP_ROOT / "ui" / pages[0]["iconUrl"]).is_file()


# --------------------------------------------------------------------------- #
# permissions
# --------------------------------------------------------------------------- #


def test_the_api_permission_is_exactly_two_prefixes(raw):
    """Studio's own namespace and ``/api/chat``. Nothing else (review R13/C27)."""
    assert raw["permissions"]["api"] == EXPECTED_API_PREFIXES


def test_no_api_prefix_uses_a_wildcard(raw):
    """``/api/apps/aidlc-studio/*`` would also match a *sibling* app whose name shares the prefix."""
    for prefix in raw["permissions"]["api"]:
        assert "*" not in prefix, prefix


def test_the_declared_events_are_the_ones_the_backend_publishes(raw, studio):
    """An undeclared name makes ``ctx.events.publish`` raise, so the dashboard silently stops updating."""
    assert raw["permissions"]["events"] == EXPECTED_EVENTS
    assert list(studio.constants.HOST_EVENTS) == EXPECTED_EVENTS


def test_storage_and_spawn_are_on_and_network_and_cron_are_off(raw):
    permissions = raw["permissions"]
    assert permissions["storage"] is True    # the SQLite store
    assert permissions["spawn"] is True      # the advisor subagent
    assert permissions["network"] is False   # Studio reads local files and talks to the local host only
    assert permissions["cron"] is False      # no unattended lane in v1 (there is no machine lane)
    assert permissions["mcpTools"] == []


# --------------------------------------------------------------------------- #
# the bundled payload — three files that must agree
# --------------------------------------------------------------------------- #


def test_the_declared_engine_version_is_the_payloads_own(raw, payload_manifest, studio):
    """Asserted against the manifest, not a literal: a bad payload bump must fail, not pass (C21)."""
    declared = raw["extra"]["bundledAidlc"]["engineVersion"]
    assert declared == payload_manifest.engine_version
    assert studio.constants.BUNDLED_ENGINE_VERSION == payload_manifest.engine_version


def test_the_state_version_and_stage_count_constants_are_the_payloads_own(payload_manifest, studio):
    assert studio.constants.BUNDLED_STAGE_COUNT == payload_manifest.stage_count
    assert studio.constants.BUNDLED_STATE_VERSION in payload_manifest.compatible_state_versions
    assert max(payload_manifest.compatible_state_versions) == studio.constants.BUNDLED_STATE_VERSION


def test_the_parsers_accept_more_state_versions_than_the_payload_writes(payload_manifest, studio):
    """A repository on an older AI-DLC must stay *readable* even though it cannot be upgraded (C21)."""
    supported = set(studio.constants.SUPPORTED_STATE_VERSIONS)
    assert set(payload_manifest.compatible_state_versions) <= supported
    assert supported - set(payload_manifest.compatible_state_versions), "nothing older would be readable"


def test_the_manifest_path_and_harness_in_app_json_are_real(raw, payload_manifest):
    bundled = raw["extra"]["bundledAidlc"]
    assert (APP_ROOT / bundled["manifest"]).is_file()
    assert bundled["harness"] == payload_manifest.harness
    assert (APP_ROOT / "payload" / f"aidlc-{payload_manifest.harness}").is_dir()


def test_the_scope_grid_is_a_merge_target_keyed_on_the_scopes_the_payload_ships(payload_manifest):
    """A composed scope lives in the *installed* copy of this file, so Studio owns rows and not bytes.

    AI-DLC's composer agent appends the new scope's column to
    ``.kiro/tools/data/scope-grid.json`` inside the user's repository — the harvested install at
    ``tests/fixtures/aidlc-2.6.2-devdelta`` carries a tenth key the payload never shipped. Classified
    ``framework`` that drift is ``owned_modified``: blocking, with no force flag, so a composed
    repository could never be installed into or upgraded again. The managed key list is the payload
    file's own top-level scope names, read here rather than written out, so a payload that ships one
    more scope is a generator run and not an edit to this test (FR-INST-006, C21).
    """
    rel = ".kiro/tools/data/scope-grid.json"
    row = next(f for f in payload_manifest.files if f.path == rel)
    assert row.ownership == "merge"
    spec = payload_manifest.merge_targets[rel]
    assert spec["strategy"] == "json-managed-keys"
    shipped = json.loads((APP_ROOT / "payload" / "aidlc-kiro" / rel).read_text("utf-8"))
    assert spec["managedKeys"] == sorted(shipped)
    assert "feature" in spec["managedKeys"], "the grid is no longer keyed by scope name"
    # The composer's key is the user's: it must never appear on Studio's list, or the next upgrade
    # would decide the composed scope was its own to rewrite.
    assert "review-major-remediation" not in spec["managedKeys"]


def test_the_harness_json_merge_target_manages_the_identity_keys_and_nothing_else(payload_manifest):
    """``/aidlc plugin select`` appends ``plugins`` to the installed ``harness.json``.

    It is a documented verb that works in a stock install with no plugins at all, so a user can reach
    ``owned_modified`` — blocking, no force path — on a 75-byte file they never opened. As a merge target
    Studio keeps the three identity keys every harness probe reads and leaves the rest of the file to
    AI-DLC and to the operator. The literal list is deliberate: a payload that started shipping
    ``documentExtractors`` (a key humans edit) would silently hand it to Studio, and that is a bump to
    read before it ships, not one to wave through.
    """
    rel = ".kiro/tools/data/harness.json"
    row = next(f for f in payload_manifest.files if f.path == rel)
    assert row.ownership == "merge"
    spec = payload_manifest.merge_targets[rel]
    assert spec["strategy"] == "json-managed-keys"
    shipped = json.loads((APP_ROOT / "payload" / "aidlc-kiro" / rel).read_text("utf-8"))
    assert spec["managedKeys"] == sorted(shipped) == ["harnessDir", "name", "rulesSubdir"]
    assert "plugins" not in spec["managedKeys"]


def test_every_merge_target_is_declared_by_a_merge_ownership_file(payload_manifest):
    """The two lists are one decision: a target with no file, or a ``merge`` file with no target,
    installs as "no fragment written" — a success that left the fragment unset (review P10)."""
    assert set(payload_manifest.merge_targets) == {
        f.path for f in payload_manifest.files if f.ownership == "merge"
    }


def test_every_file_the_payload_manifest_lists_exists_and_matches_its_digest(payload_manifest, studio):
    """The manifest is what decides which bytes land in a user's repository; drift is an install hazard."""
    root = APP_ROOT / "payload" / f"aidlc-{payload_manifest.harness}"
    assert payload_manifest.file_count == len(payload_manifest.files)
    missing = [f.path for f in payload_manifest.files if not (root / f.path).is_file()]
    assert missing == [], missing[:5]
    sample = list(payload_manifest.files)[:8] + list(payload_manifest.files)[-8:]
    for entry in sample:
        digest = studio.security.sha256_bytes((root / entry.path).read_bytes())
        assert digest == entry.sha256, entry.path


# --------------------------------------------------------------------------- #
# the advisor agent
# --------------------------------------------------------------------------- #


def test_the_agent_file_app_json_declares_is_the_one_on_disk(raw):
    assert raw["agents"] == ["agents/advisor.json"]
    assert ADVISOR_JSON.is_file()


def test_the_advisor_declares_the_name_the_spawn_uses(studio):
    """``SpawnSDK`` matches the *declared* name among this app's own files (C25/review P20/R14).

    A mismatch here is not a warning: ``spawn.run`` refuses, and the advisor becomes permanently
    ``advisor_unavailable`` with nothing in the logs pointing at a JSON field.
    """
    agent = json.loads(ADVISOR_JSON.read_text("utf-8"))
    assert agent["name"] == studio.constants.ADVISOR_AGENT_NAME == "aidlc-studio-advisor"
    assert studio.advisor.ADVISOR_AGENT_NAME == studio.constants.ADVISOR_AGENT_NAME


def test_the_advisor_has_exactly_one_tool_and_it_cannot_touch_anything(studio):
    """Read-only by construction: the advisor sees an evidence package and returns prose.

    ``thinking`` alone is the whole point — an agent with a filesystem or shell tool could act on the
    repository it is advising about, which is a decision no advisor is allowed to make.
    """
    agent = json.loads(ADVISOR_JSON.read_text("utf-8"))
    assert agent["tools"] == ["thinking"]
    assert agent.get("allowedTools") == ["thinking"]
    assert agent.get("resources") in (None, [])


def test_the_advisor_prompt_forbids_treating_evidence_as_instructions():
    """Artifacts and audit rows are untrusted text; the prompt is where that is enforced."""
    prompt = json.loads(ADVISOR_JSON.read_text("utf-8"))["prompt"]
    assert "untrusted" in prompt.lower()
    assert "Never decide for the user" in prompt
    # A plan draft quotes the README and the manifest names (§1.7): the one prompt-injection vector
    # the wizard newly opens, so the untrusted-DATA list must name it.
    assert "repository file excerpt" in prompt


# --------------------------------------------------------------------------- #
# assets
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "key",
    ["iconPath", "heroImage", "heroImageDark", "heroImageDetail", "heroImageDetailDark"],
)
def test_every_declared_asset_exists(raw, key):
    """A registry listing with a broken image is a listing nobody installs (08 §2.2)."""
    relative = raw[key]
    path = APP_ROOT / relative
    assert path.is_file(), relative
    assert path.stat().st_size > 0


def test_the_tags_include_the_two_the_registry_categorises_on(raw):
    assert {"developer-tools", "workflows"} <= set(raw["tags"])
