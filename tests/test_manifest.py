"""``app.json``, ``agents/advisor.json`` and the payload manifest (§4.3).

Almost every manifest field is a security decision: ``permissions.api`` is the exact set of host paths an app token
may reach (a third prefix widens it, R13/C27); ``permissions.events`` must equal what ``events.py`` publishes (the host
bus raises on an undeclared name); ``network`` and ``cron`` stay off (no internet, no unattended lane, S12). Every
version-derived value is asserted against the payload manifest, never a literal (C21/P03), and validation is the
host's own ``AppManifest.validate``, so a field the gateway would reject fails here rather than on a user's machine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent
APP_JSON = APP_ROOT / "app.json"
ADVISOR_JSON = APP_ROOT / "agents" / "advisor.json"
PAYLOAD_MANIFEST = APP_ROOT / "payload" / "manifest.json"

#: v2 installs beside the 1.x Studio (``aidlc-studio``) instead of replacing it, so it has a name of its own:
#: KiroCrew refuses to install a folder whose manifest names an app that is already installed.
APP_NAME = "aidlc-studio-v2"
# §4.3 verbatim, compared as equality: an added prefix is as much a finding as a missing one
EXPECTED_API_PREFIXES = [f"/api/apps/{APP_NAME}", "/api/chat"]
EXPECTED_EVENTS = [f"{APP_NAME}:action", f"{APP_NAME}:repo", f"{APP_NAME}:transaction"]


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(APP_JSON.read_text("utf-8"))


@pytest.fixture(scope="module")
def payload_manifest(studio):
    return studio.installer.PayloadManifest.load(PAYLOAD_MANIFEST)


# --- the host's own verdict ---
def test_the_host_accepts_the_manifest_with_no_complaints(raw):
    """``validate`` is what runs at install time; anything it returns is an install that fails."""
    from kiro_crew.apps.manifest import AppManifest

    assert AppManifest.from_dict(raw).validate(app_root=APP_ROOT) == []


def test_identity_matches_the_constants_module(raw, studio):
    C = studio.constants
    assert (raw["name"], raw["version"], raw["minKiroCrewVersion"]) == (C.APP_NAME, C.APP_VERSION,
                                                                        C.MIN_KIROCREW_VERSION)
    assert C.MIN_KIROCREW_VERSION == "0.8.0"


def test_the_kirocrew_floor_is_the_loader_the_entry_points_need(raw):
    """0.8 registers the app's parent packages, which is what ``from . import studio`` relies on."""
    assert "from . import studio" in (APP_ROOT / "backend" / "routes.py").read_text("utf-8")
    assert raw["minKiroCrewVersion"] == "0.8.0"


# --- hooks and UI entry ---
def test_the_three_backend_hooks_point_at_files_that_exist(raw):
    hooks = raw["backend"]["hooks"]
    assert hooks == {"routes": "backend.routes:register_routes", "on_startup": "backend.hooks:on_startup",
                     "on_shutdown": "backend.hooks:on_shutdown"}
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
    assert len(pages) == 1 and pages[0]["route"] == f"/apps/{APP_NAME}"
    assert (APP_ROOT / "ui" / pages[0]["iconUrl"]).is_file()


# --- permissions ---
def test_the_api_permission_is_exactly_two_prefixes(raw):
    """Studio's own namespace and ``/api/chat``. Nothing else (review R13/C27)."""
    assert raw["permissions"]["api"] == EXPECTED_API_PREFIXES


def test_no_api_prefix_uses_a_wildcard(raw):
    """``/api/apps/aidlc-studio/*`` would also match a *sibling* app whose name shares the prefix."""
    for prefix in raw["permissions"]["api"]:
        assert "*" not in prefix, prefix


def test_the_declared_events_are_the_ones_the_backend_publishes(raw, studio):
    """An undeclared name makes ``ctx.events.publish`` raise, so the dashboard silently stops updating."""
    C = studio.constants
    assert raw["permissions"]["events"] == EXPECTED_EVENTS
    assert [C.HOST_EVENT_ACTION, C.HOST_EVENT_REPO, C.HOST_EVENT_TRANSACTION] == EXPECTED_EVENTS


def test_storage_and_spawn_are_on_and_network_and_cron_are_off(raw):
    permissions = raw["permissions"]
    assert permissions["storage"] is True and permissions["spawn"] is True  # the SQLite store; the advisor subagent
    assert permissions["network"] is False  # Studio reads local files and talks to the local host only
    assert permissions["cron"] is False  # no unattended lane in v1 (there is no machine lane)
    assert permissions["mcpTools"] == []


# --- the bundled payload: three files that must agree ---
def test_the_declared_engine_version_is_the_payloads_own(raw, payload_manifest, studio):
    """Asserted against the manifest, not a literal: a bad payload bump must fail, not pass (C21)."""
    assert raw["extra"]["bundledAidlc"]["engineVersion"] == payload_manifest.engine_version
    assert studio.constants.BUNDLED_ENGINE_VERSION == payload_manifest.engine_version


def test_the_bundled_engine_writes_state_version_8_over_33_stages(payload_manifest, studio):
    """The parsers' newest state version and the stage count every fixture assumes."""
    assert payload_manifest.stage_count == 33
    assert max(payload_manifest.compatible_state_versions) == 8 == max(studio.constants.SUPPORTED_STATE_VERSIONS)


def test_the_parsers_accept_more_state_versions_than_the_payload_writes(payload_manifest, studio):
    """A repository on an older AI-DLC must stay *readable* even though it cannot be upgraded (C21)."""
    supported = set(studio.constants.SUPPORTED_STATE_VERSIONS)
    assert set(payload_manifest.compatible_state_versions) <= supported
    assert supported - set(payload_manifest.compatible_state_versions), "nothing older would be readable"


def test_the_manifest_path_and_harness_in_app_json_are_real(raw, payload_manifest):
    bundled = raw["extra"]["bundledAidlc"]
    assert (APP_ROOT / bundled["manifest"]).is_file() and bundled["harness"] == payload_manifest.harness
    assert (APP_ROOT / "payload" / f"aidlc-{payload_manifest.harness}").is_dir()


def merge_spec(payload_manifest, rel: str) -> tuple[dict, list[str]]:
    """``rel``'s merge-target spec (its file row must be ``merge``) and the payload file's own sorted top-level keys."""
    assert next(f for f in payload_manifest.files if f.path == rel).ownership == "merge"
    spec = payload_manifest.merge_targets[rel]
    assert spec["strategy"] == "json-managed-keys"
    return spec, sorted(json.loads((APP_ROOT / "payload" / "aidlc-kiro" / rel).read_text("utf-8")))


def test_the_scope_grid_is_a_merge_target_keyed_on_the_scopes_the_payload_ships(payload_manifest):
    """A composed scope adds a key to the installed grid; as ``framework`` that blocks every upgrade (FR-INST-006)."""
    spec, shipped = merge_spec(payload_manifest, ".kiro/tools/data/scope-grid.json")
    assert spec["managedKeys"] == shipped  # read from the payload, so one more shipped scope is a generator run
    assert "feature" in spec["managedKeys"], "the grid is no longer keyed by scope name"
    # the composer's key is the user's: on Studio's list, the next upgrade would rewrite the composed scope
    assert "review-major-remediation" not in spec["managedKeys"]


def test_the_harness_json_merge_target_manages_the_identity_keys_and_nothing_else(payload_manifest):
    """``/aidlc plugin select`` appends ``plugins``; the literal list makes a newly shipped key a bump to read first."""
    spec, shipped = merge_spec(payload_manifest, ".kiro/tools/data/harness.json")
    # 2.10.0 added four distribution-descriptor keys; none is operator-edited, all are refreshed by the engine
    assert spec["managedKeys"] == shipped == ["configNextStep", "distribution", "harnessDir", "name", "productName",
                                              "rulesSubdir", "schemaVersion"]
    assert "plugins" not in spec["managedKeys"]


def test_every_merge_target_is_declared_by_a_merge_ownership_file(payload_manifest):
    """One decision: a target without a file, or a merge file without a target, installs no fragment (P10)."""
    assert set(payload_manifest.merge_targets) == {f.path for f in payload_manifest.files if f.ownership == "merge"}


def test_every_file_the_payload_manifest_lists_exists_and_matches_its_digest(payload_manifest, studio):
    """The manifest decides which bytes land in a user's repository; drift is an install hazard."""
    root = APP_ROOT / "payload" / f"aidlc-{payload_manifest.harness}"
    assert payload_manifest.file_count == len(payload_manifest.files)
    missing = [f.path for f in payload_manifest.files if not (root / f.path).is_file()]
    assert missing == [], missing[:5]
    for entry in list(payload_manifest.files)[:8] + list(payload_manifest.files)[-8:]:
        assert studio.security.sha256_bytes((root / entry.path).read_bytes()) == entry.sha256, entry.path


# --- the advisor agent ---
def test_the_agent_file_app_json_declares_is_the_one_on_disk(raw):
    assert raw["agents"] == ["agents/advisor.json"] and ADVISOR_JSON.is_file()


def test_the_advisor_declares_the_name_the_spawn_uses(studio):
    """``SpawnSDK`` matches the declared name (C25/P20/R14): a mismatch leaves the advisor silently unavailable."""
    agent = json.loads(ADVISOR_JSON.read_text("utf-8"))
    assert agent["name"] == studio.constants.ADVISOR_AGENT_NAME == f"{APP_NAME}-advisor"
    assert studio.advisor.ADVISOR_AGENT_NAME == studio.constants.ADVISOR_AGENT_NAME


def test_the_advisor_has_exactly_one_tool_and_it_cannot_touch_anything(studio):
    """Read-only by construction: with a filesystem or shell tool the advisor could act on the repository."""
    agent = json.loads(ADVISOR_JSON.read_text("utf-8"))
    assert agent["tools"] == ["thinking"] and agent.get("allowedTools") == ["thinking"]
    assert agent.get("resources") in (None, [])


def test_the_advisor_prompt_forbids_treating_evidence_as_instructions():
    """Artifacts and audit rows are untrusted text; the prompt is where that is enforced."""
    prompt = json.loads(ADVISOR_JSON.read_text("utf-8"))["prompt"]
    assert "untrusted" in prompt.lower() and "Never decide for the user" in prompt
    # a plan draft quotes the README and the manifest names (§1.7), so the untrusted-DATA list must name them
    assert "repository file excerpt" in prompt


# --- assets ---
@pytest.mark.parametrize("key", ["iconPath", "heroImage", "heroImageDark", "heroImageDetail", "heroImageDetailDark"])
def test_every_declared_asset_exists(raw, key):
    """A registry listing with a broken image is a listing nobody installs (08 §2.2)."""
    path = APP_ROOT / raw[key]
    assert path.is_file(), raw[key]
    assert path.stat().st_size > 0


def test_the_tags_include_the_two_the_registry_categorises_on(raw):
    assert {"developer-tools", "workflows"} <= set(raw["tags"])
