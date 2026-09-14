"""Literal model-entry ownership, using temporary payloads, repositories and SQLite receipts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_installer import do, insert_repo, make_harness, reload_repo, run, store, tree


CLI = ".kiro/settings/cli.json"
MAP = "chat.modelDefaults"
MODEL = "claude-opus-4.8"
USER_MODEL = "claude-opus-5"
OWNED_MODELS = {MODEL: {"output_config": {"effort": "xhigh"}}}
USER_MODELS = {USER_MODEL: {"output_config": {"effort": "high"}}}
SPEC = {
    "strategy": "json-managed-keys",
    "managedKeys": ["chat.defaultAgent", MAP],
    "managedMapEntries": {MAP: [MODEL]},
}


def bundle(root, *, revision="old", legacy=False, version="2.7.1", defaults=None, agent="aidlc"):
    defaults = OWNED_MODELS if defaults is None else defaults
    files = {
        ".kiro/tools/aidlc-version.ts": f'export const AIDLC_VERSION = "{version}";\n',
        ".kiro/tools/aidlc-utility.ts": "// utility\n",
        ".kiro/tools/aidlc-state.ts": "// state\n",
        ".kiro/tools/aidlc-progress.ts": f"// fingerprint {revision}\n",
        CLI: json.dumps({"chat.defaultAgent": agent, MAP: defaults}) + "\n",
        "aidlc/active-space": "default\n",
    }
    rows, digest = [], hashlib.sha256()
    for rel, text in sorted(files.items()):
        path = root / "aidlc-kiro" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode()
        path.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        ownership = "merge" if rel == CLI else "shell" if rel.startswith("aidlc/") else "framework"
        rows.append({"path": rel, "sha256": sha, "size": len(data), "ownership": ownership})
        digest.update(f"{rel}\0{sha}\n".encode())
    spec = {"strategy": "json-managed-keys", "managedKeys": ["chat.defaultAgent", MAP]}
    if not legacy:
        spec["managedMapEntries"] = {MAP: sorted(defaults) if isinstance(defaults, dict) else [MODEL]}
    manifest = {
        "schema": "aidlc-studio-payload/1", "harness": "kiro", "engineVersion": version,
        "source": {}, "compatibleStateVersions": [8], "stageCount": 0,
        "payloadDigest": digest.hexdigest(), "fileCount": len(rows),
        "mergeTargets": {CLI: spec}, "files": rows,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest) + "\n")
    return root / "aidlc-kiro", manifest_path


@pytest.fixture
def case(studio, store, make_harness, tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "README.md").write_bytes(b"# User project\n")
    state = root / "aidlc/spaces/default/intents/260910-model-defaults/aidlc-state.md"
    state.parent.mkdir(parents=True)
    state.write_bytes(b"# AI-DLC State\n\n- **State Version**: 8\n")
    st = root.stat()
    repo = insert_repo(studio, store, root, resolved_identity=f"{st.st_dev}:{st.st_ino}")
    serial = 0

    def payload(**kwargs):
        nonlocal serial
        serial += 1
        h = make_harness(bundle(tmp_path / f"bundle-{serial}", **kwargs))
        assert h.installer.verify_payload().ok
        return h

    def write_cli(obj):
        path = root / CLI
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(obj, indent=4, ensure_ascii=True) + "\n").encode())
        return path.read_bytes()

    def install(*, legacy=False, preexisting=False):
        h = payload(legacy=legacy)
        if preexisting:
            write_cli({"chat.defaultAgent": "aidlc", MAP: OWNED_MODELS})
        result = do(h, repo, "install")
        assert result.status == "committed", result.error
        return h, h.installer.current_receipt(repo.repo_id)

    return SimpleNamespace(
        root=root, repo=repo, store=store, payload=payload, write_cli=write_cli, install=install,
    )


def cli_entry(plan):
    return next(entry for entry in plan.entries if entry.path == CLI)


@pytest.mark.parametrize("legacy", [True, False])
def test_refresh_preserves_extra_model_bytes_and_records_only_owned_entries(case, legacy):
    _, old = case.install(legacy=legacy)
    before = case.write_cli({
        "chat.defaultAgent": "aidlc", MAP: {**OWNED_MODELS, **USER_MODELS},
        "user.setting": "preserve \u4e2d\u6587 and escapes",
    })
    snapshot = tree(case.root)
    up = case.payload(revision="fixed")
    plan = up.installer.preview(case.repo, "upgrade")
    assert not plan.blocking
    assert cli_entry(plan).action == "merge_identical"
    assert cli_entry(plan).payload_sha256 == old.by_path()[CLI].canonical_digest
    result = run(up.installer.run(case.repo, "upgrade", plan_digest=plan.digest()))
    assert result.status == "committed", result.error
    assert (case.root / CLI).read_bytes() == before
    after = tree(case.root)
    assert {p: h for p, h in after.items() if p != ".kiro/tools/aidlc-progress.ts"} == {
        p: h for p, h in snapshot.items() if p != ".kiro/tools/aidlc-progress.ts"
    }
    receipt = up.installer.current_receipt(case.repo.repo_id)
    meta = receipt.by_path()[CLI].uninstall
    assert meta["spec"]["managedMapEntries"] == {MAP: [MODEL]}
    assert meta["map_entries"] == {MAP: [MODEL]}
    assert MAP not in meta["keys"]
    assert receipt.by_path()[CLI].canonical_digest == old.by_path()[CLI].canonical_digest
    assert not up.installer.drift(case.repo, receipt)


def test_fresh_install_preserves_unrelated_literal_model_entries(case):
    unknown = {**USER_MODELS, "claude-opus-4": {"8": {"effort": "user"}}}
    case.write_cli({MAP: unknown, "user.setting": "untouched"})
    h = case.payload()
    plan = h.installer.preview(case.repo, "install")
    assert not plan.blocking
    assert cli_entry(plan).action == "merge_update"
    result = do(h, case.repo, "install")
    assert result.status == "committed", result.error
    current = json.loads((case.root / CLI).read_bytes())
    assert current == {
        MAP: {**unknown, **OWNED_MODELS}, "user.setting": "untouched", "chat.defaultAgent": "aidlc",
    }
    meta = h.installer.current_receipt(case.repo.repo_id).by_path()[CLI].uninstall
    assert meta["spec"]["managedMapEntries"] == {MAP: [MODEL]}
    assert meta["map_entries"] == {MAP: [MODEL]}


@pytest.mark.parametrize("legacy", [True, False])
@pytest.mark.parametrize("changed", ["model", "agent"])
def test_changed_owned_preferences_still_conflict(case, studio, legacy, changed):
    _, receipt = case.install(legacy=legacy)
    models = {**OWNED_MODELS, **USER_MODELS}
    if changed == "model":
        models[MODEL] = {"output_config": {"effort": "low"}}
    case.write_cli({"chat.defaultAgent": "other" if changed == "agent" else "aidlc", MAP: models})
    before = tree(case.root)
    up = case.payload(revision="fixed")
    plan = up.installer.preview(case.repo, "upgrade")
    assert cli_entry(plan).action == "merge_conflict"
    with pytest.raises(studio.errors.StudioError) as exc:
        run(up.installer.begin(case.repo, "upgrade", plan_digest=plan.digest()))
    assert exc.value.code == "install_conflict"
    assert tree(case.root) == before
    assert up.installer.current_receipt(case.repo.repo_id) == receipt


@pytest.mark.parametrize("live_matches_new", [False, True])
def test_legacy_entry_derivation_requires_exact_old_fragment_digest(case, studio, live_matches_new):
    case.install(legacy=True)
    desired = {MODEL: {"output_config": {"effort": "medium"}}}
    case.write_cli({"chat.defaultAgent": "aidlc", MAP: desired if live_matches_new else OWNED_MODELS})
    before = tree(case.root)
    up = case.payload(revision="fixed", defaults=desired)
    plan = up.installer.preview(case.repo, "upgrade")
    assert cli_entry(plan).action == "merge_conflict"
    with pytest.raises(studio.errors.StudioError):
        run(up.installer.begin(case.repo, "upgrade", plan_digest=plan.digest()))
    assert tree(case.root) == before


@pytest.mark.parametrize("legacy", [True, False])
@pytest.mark.parametrize("preexisting", [False, True])
def test_uninstall_preserves_extras_and_only_removes_introduced_models(case, legacy, preexisting):
    case.install(legacy=legacy, preexisting=preexisting)
    case.write_cli({"chat.defaultAgent": "aidlc", MAP: {**OWNED_MODELS, **USER_MODELS}, "user": 42})
    up = case.payload(revision="fixed")
    result = do(up, case.repo, "upgrade")
    assert result.status == "committed", result.error
    # Uninstall must use the committed entry keys even if a later bundle ships different models.
    later = case.payload(revision="future", version="2.8.0", defaults=USER_MODELS)
    plan = later.installer.preview(case.repo, "uninstall")
    assert not plan.blocking
    result = run(later.installer.uninstall(case.repo, confirm_digest=plan.digest()))
    assert result.status == "committed", result.error
    expected = {MAP: {**OWNED_MODELS, **USER_MODELS}, "chat.defaultAgent": "aidlc", "user": 42}
    if not preexisting:
        expected = {MAP: USER_MODELS, "user": 42}
    assert json.loads((case.root / CLI).read_bytes()) == expected


def test_failed_upgrade_restores_whole_file_and_receipt(case):
    _, receipt = case.install()
    before = case.write_cli({"chat.defaultAgent": "aidlc", MAP: {**OWNED_MODELS, **USER_MODELS}})
    snapshot = tree(case.root)
    up = case.payload(
        revision="fixed", version="2.8.0", defaults={MODEL: {"output_config": {"effort": "medium"}}},
    )
    up.fault.at("after:merge_fragments")
    result = do(up, case.repo, "upgrade")
    assert result.status == "rolled_back", result.error
    assert (case.root / CLI).read_bytes() == before
    assert tree(case.root) == snapshot
    assert up.installer.current_receipt(case.repo.repo_id) == receipt
    assert case.store.lease_list() == []


def test_drift_uses_receipted_entry_keys_when_bundle_changes(case):
    h, receipt = case.install()
    case.write_cli({"chat.defaultAgent": "aidlc", MAP: {**OWNED_MODELS, **USER_MODELS}})
    later = case.payload(revision="future", defaults=USER_MODELS)
    assert not h.installer.drift(case.repo, receipt)
    assert not later.installer.drift(case.repo, receipt)


def test_new_payload_model_keys_cannot_hide_drift_in_previous_owned_model(case, studio):
    case.install()
    case.write_cli({
        "chat.defaultAgent": "aidlc",
        MAP: {MODEL: {"output_config": {"effort": "user-modified"}}, **USER_MODELS},
    })
    before = tree(case.root)
    up = case.payload(revision="future", defaults=USER_MODELS)
    plan = up.installer.preview(case.repo, "upgrade")
    assert cli_entry(plan).action == "merge_conflict"
    with pytest.raises(studio.errors.StudioError):
        run(up.installer.begin(case.repo, "upgrade", plan_digest=plan.digest()))
    assert tree(case.root) == before


@pytest.mark.parametrize("changed", ["model", "agent", "other_model"])
def test_receipt_commit_rechecks_owned_preferences_and_preserves_user_edits(case, changed):
    _, receipt = case.install(legacy=True)
    case.write_cli({"chat.defaultAgent": "aidlc", MAP: {**OWNED_MODELS, **USER_MODELS}})
    up = case.payload(revision="fixed")
    final_cli = []

    def user_edit():
        content = json.loads((case.root / CLI).read_bytes())
        if changed == "agent":
            content["chat.defaultAgent"] = "user-agent"
        else:
            model = USER_MODEL if changed == "other_model" else MODEL
            content[MAP][model] = {"output_config": {"effort": "user-changed"}}
        final_cli.append(case.write_cli(content))

    up.fault.on("before:commit_receipt", user_edit)
    result = do(up, case.repo, "upgrade")
    assert (case.root / CLI).read_bytes() == final_cli[0]
    if changed == "other_model":
        assert result.status == "committed", result.error
    else:
        assert result.status == "rolled_back" and result.error == "install_conflict"
        assert up.installer.current_receipt(case.repo.repo_id) == receipt
        assert (case.root / ".kiro/tools/aidlc-progress.ts").read_bytes() == b"// fingerprint old\n"


def test_proven_legacy_uninstall_preserves_other_models_without_refresh(case):
    case.install(legacy=True)
    case.write_cli({"chat.defaultAgent": "aidlc", MAP: {**OWNED_MODELS, **USER_MODELS}})
    up = case.payload(revision="fixed")
    plan = up.installer.preview(case.repo, "uninstall")
    assert not plan.blocking
    result = run(up.installer.uninstall(case.repo, confirm_digest=plan.digest()))
    assert result.status == "committed", result.error
    assert json.loads((case.root / CLI).read_bytes()) == {MAP: USER_MODELS}


def test_legacy_receipt_without_introduction_evidence_cannot_delete_preferences(case):
    _, receipt = case.install(legacy=True)
    rows = [rf.to_json() for rf in receipt.files]
    next(rf for rf in rows if rf["path"] == CLI).pop("uninstall")
    case.store.update("install_receipts", receipt.receipt_id, {"files_json": rows})
    before = case.write_cli({"chat.defaultAgent": "aidlc", MAP: {**OWNED_MODELS, **USER_MODELS}})
    up = case.payload(revision="fixed")
    assert do(up, case.repo, "upgrade").status == "committed"
    meta = up.installer.current_receipt(case.repo.repo_id).by_path()[CLI].uninstall
    assert meta["map_entries"] == {MAP: []}
    plan = up.installer.preview(case.repo, "uninstall")
    assert not plan.blocking
    result = run(up.installer.uninstall(case.repo, confirm_digest=plan.digest()))
    assert result.status == "committed", result.error
    assert (case.root / CLI).read_bytes() == before


def test_nested_map_spelling_keeps_model_ids_literal(case):
    case.write_cli({"chat": {"modelDefaults": USER_MODELS}, "user": "keep"})
    h = case.payload()
    result = do(h, case.repo, "install")
    assert result.status == "committed", result.error
    current = json.loads((case.root / CLI).read_bytes())
    assert MAP not in current
    assert current["chat"]["modelDefaults"] == {**USER_MODELS, **OWNED_MODELS}
    plan = h.installer.preview(case.repo, "uninstall")
    assert not plan.blocking
    assert run(h.installer.uninstall(case.repo, confirm_digest=plan.digest())).status == "committed"
    assert json.loads((case.root / CLI).read_bytes()) == {
        "chat": {"modelDefaults": USER_MODELS}, "user": "keep",
    }


def test_version_rollback_uses_receipt_model_keys_and_preserves_user_models(case, studio):
    _, target = case.install()
    case.write_cli({"chat.defaultAgent": "aidlc", MAP: {**OWNED_MODELS, **USER_MODELS}})
    up = case.payload(
        revision="fixed", version="2.8.0", defaults={MODEL: {"output_config": {"effort": "medium"}}},
    )
    assert do(up, case.repo, "upgrade").status == "committed"
    repo = reload_repo(studio, case.store)
    plan = up.installer.preview(repo, "rollback")
    assert not plan.blocking, [(e.path, e.reason) for e in plan.blockers]
    result = run(up.installer.rollback(repo, confirm_digest=plan.digest()))
    assert result.status == "committed", result.error
    assert json.loads((case.root / CLI).read_bytes()) == {
        "chat.defaultAgent": "aidlc", MAP: {**OWNED_MODELS, **USER_MODELS},
    }
    assert up.installer.current_receipt(repo.repo_id).engine_version == target.engine_version


@pytest.fixture
def model_ownership_shift(case, studio):
    _, target = case.install()
    case.write_cli({"chat.defaultAgent": "aidlc", MAP: {**OWNED_MODELS, **USER_MODELS}})
    up = case.payload(revision="model-scope-changed", version="2.8.0", defaults=USER_MODELS)
    assert cli_entry(up.installer.preview(case.repo, "upgrade")).action == "merge_identical"
    assert do(up, case.repo, "upgrade").status == "committed"
    repo = reload_repo(studio, case.store)
    current = up.installer.current_receipt(repo.repo_id)
    assert current.by_path()[CLI].uninstall["spec"]["managedMapEntries"] == {MAP: [USER_MODEL]}
    return SimpleNamespace(case=case, up=up, repo=repo, target=target, current=current)


@pytest.mark.parametrize("spelling", ["flat", "nested"])
@pytest.mark.parametrize("edited", [
    {"output_config": {"effort": "user-edited"}}, None, ["user preference"],
])
def test_version_rollback_preserves_edits_to_model_no_longer_owned(
    model_ownership_shift, studio, spelling, edited,
):
    scene = model_ownership_shift
    models = {MODEL: edited, **USER_MODELS}
    content = {"chat.defaultAgent": "aidlc", "user.setting": "latest"}
    content.update({MAP: models} if spelling == "flat" else {"chat": {"modelDefaults": models}})
    cli_bytes = scene.case.write_cli(content)
    before = tree(scene.case.root)
    transactions = scene.up.installer.transactions(scene.repo.repo_id)
    # The edited model is outside current ownership, so the current receipt is still intact.
    assert not scene.up.installer.drift(scene.repo, scene.current)
    plan = scene.up.installer.preview(scene.repo, "rollback")
    assert cli_entry(plan).action == "rollback_conflict"
    assert cli_entry(plan).blocking and plan.blocking
    with pytest.raises(studio.errors.StudioError) as exc:
        run(scene.up.installer.rollback(scene.repo, confirm_digest=plan.digest()))
    assert exc.value.code == "install_conflict"
    assert (scene.case.root / CLI).read_bytes() == cli_bytes
    assert tree(scene.case.root) == before
    assert scene.up.installer.current_receipt(scene.repo.repo_id) == scene.current
    assert scene.up.installer.transactions(scene.repo.repo_id) == transactions
    assert scene.case.store.lease_list() == []


@pytest.mark.parametrize("old_entry", ["absent", "unchanged"])
def test_version_rollback_restores_unowned_model_only_when_absent_or_unchanged(
    model_ownership_shift, old_entry,
):
    scene = model_ownership_shift
    extras = {**USER_MODELS, "custom.local-1.2": {"effort": "latest-user-setting"}}
    models = {**extras, **OWNED_MODELS} if old_entry == "unchanged" else extras
    scene.case.write_cli({"chat.defaultAgent": "aidlc", MAP: models})
    plan = scene.up.installer.preview(scene.repo, "rollback")
    assert not plan.blocking, [(entry.path, entry.reason) for entry in plan.blockers]
    result = run(scene.up.installer.rollback(scene.repo, confirm_digest=plan.digest()))
    assert result.status == "committed", result.error
    assert json.loads((scene.case.root / CLI).read_bytes()) == {
        "chat.defaultAgent": "aidlc", MAP: {**OWNED_MODELS, **extras},
    }
    restored = scene.up.installer.current_receipt(scene.repo.repo_id)
    assert restored.engine_version == scene.target.engine_version
    assert restored.by_path()[CLI].uninstall["spec"]["managedMapEntries"] == {MAP: [MODEL]}
    assert scene.case.store.lease_list() == []


def test_version_rollback_rejects_model_edit_after_preview(model_ownership_shift, studio):
    scene = model_ownership_shift
    plan = scene.up.installer.preview(scene.repo, "rollback")
    assert not plan.blocking
    cli_bytes = scene.case.write_cli({
        "chat.defaultAgent": "aidlc",
        MAP: {MODEL: {"output_config": {"effort": "edited-after-preview"}}, **USER_MODELS},
    })
    before = tree(scene.case.root)
    with pytest.raises(studio.errors.StudioError) as exc:
        run(scene.up.installer.rollback(scene.repo, confirm_digest=plan.digest()))
    assert exc.value.code == "install_conflict"
    assert (scene.case.root / CLI).read_bytes() == cli_bytes
    assert tree(scene.case.root) == before
    assert scene.up.installer.current_receipt(scene.repo.repo_id) == scene.current
    assert scene.case.store.lease_list() == []


@pytest.mark.parametrize("bad_map", [None, [], "user", {MODEL: None}, {MODEL: ["user"]}])
def test_non_map_values_fail_closed(case, studio, bad_map):
    case.write_cli({"chat.defaultAgent": "aidlc", MAP: bad_map})
    before = tree(case.root)
    h = case.payload()
    plan = h.installer.preview(case.repo, "install")
    assert cli_entry(plan).action == "merge_conflict"
    with pytest.raises(studio.errors.StudioError):
        run(h.installer.begin(case.repo, "install", plan_digest=plan.digest()))
    assert tree(case.root) == before


@pytest.mark.parametrize("ambiguous", ["duplicate", "flat_and_nested"])
def test_ambiguous_model_map_fails_closed(case, ambiguous):
    case.write_cli({"chat.defaultAgent": "aidlc", MAP: OWNED_MODELS})
    path = case.root / CLI
    if ambiguous == "duplicate":
        path.write_text(
            '{"chat.defaultAgent":"aidlc","chat.modelDefaults":'
            + json.dumps(USER_MODELS) + ',"chat.modelDefaults":' + json.dumps(OWNED_MODELS) + "}\n"
        )
    else:
        case.write_cli({
            "chat.defaultAgent": "aidlc", MAP: OWNED_MODELS, "chat": {"modelDefaults": USER_MODELS},
        })
    h = case.payload()
    assert cli_entry(h.installer.preview(case.repo, "install")).action == "merge_conflict"


def test_mcp_server_env_is_not_deep_merged(studio):
    handler = studio.installer._JsonManagedKeys(
        ".kiro/settings/mcp.json", {"strategy": "json-managed-keys", "managedKeys": ["mcpServers.x"]},
        b'{"mcpServers":{"x":{"command":"tool","env":{"TOKEN":"bundled"}}}}',
    )
    live = b'{"mcpServers":{"x":{"command":"tool","env":{"TOKEN":"bundled","USER":"private"}}}}'
    assert handler.live_fragment(live).conflict


@pytest.fixture
def generator():
    path = Path(__file__).resolve().parents[1] / "scripts/build_payload_manifest.py"
    spec = importlib.util.spec_from_file_location("model_defaults_manifest_generator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generator_emits_explicit_literal_entry_metadata(generator, monkeypatch, tmp_path):
    files, manifest = bundle(tmp_path / "payload", legacy=True)
    monkeypatch.setattr(generator, "PAYLOAD_DIR", files)
    monkeypatch.setattr(generator, "MANIFEST", manifest)
    assert generator.main(["--version", "2.7.1", "--source-commit", "fixture", "--state-versions", "8"]) == 0
    targets = json.loads(manifest.read_bytes())["mergeTargets"]
    assert targets[CLI] == SPEC
    assert all("managedMapEntries" not in spec for path, spec in targets.items() if path != CLI)


@pytest.mark.parametrize("bad_map", [None, [], "user", {MODEL: None}])
def test_generator_refuses_non_map_payload(generator, monkeypatch, tmp_path, bad_map):
    files, manifest = bundle(tmp_path / "payload")
    (files / CLI).write_text(json.dumps({"chat.defaultAgent": "aidlc", MAP: bad_map}))
    before = manifest.read_bytes()
    monkeypatch.setattr(generator, "PAYLOAD_DIR", files)
    monkeypatch.setattr(generator, "MANIFEST", manifest)
    assert generator.main(["--version", "2.7.1", "--source-commit", "fixture"]) == 2
    assert manifest.read_bytes() == before


def test_map_entry_metadata_is_restricted_to_cli_model_defaults(studio, case):
    h = case.payload()
    manifest = h.installer.manifest
    manifest.merge_targets[CLI]["managedMapEntries"] = {"chat.defaultAgent": ["aidlc"]}
    assert h.installer.verify_payload().ok is False


@pytest.mark.parametrize("damage", ["missing", "incomplete", "wrong_receipt_entries"])
def test_model_entry_metadata_fails_closed_when_incomplete(case, damage):
    h, receipt = case.install()
    if damage == "missing":
        (h.installer.payload_root / CLI).unlink()
        assert h.installer.verify_payload().ok is False
    elif damage == "incomplete":
        h.installer.manifest.merge_targets[CLI]["managedMapEntries"] = {MAP: []}
        assert h.installer.verify_payload().ok is False
    else:
        rows = [rf.to_json() for rf in receipt.files]
        meta = next(rf for rf in rows if rf["path"] == CLI)["uninstall"]
        meta["map_entries"] = {MAP: [USER_MODEL]}
        case.store.update("install_receipts", receipt.receipt_id, {"files_json": rows})
        case.write_cli({"chat.defaultAgent": "aidlc", MAP: {**OWNED_MODELS, **USER_MODELS}})
        assert cli_entry(h.installer.preview(case.repo, "uninstall")).blocking
