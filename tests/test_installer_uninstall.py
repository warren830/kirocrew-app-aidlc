"""Receipt-driven uninstall: real temporary repositories, SQLite and admin leases."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from _helpers_c import die_in_child
from test_installer import (  # noqa: F401 - fixtures
    I, build_upgrade_payload, do, entry_of, h, insert_repo, make_harness, payload_v1, payload_v2, reload_repo, run,
    store, tree,
)

OWNED = ".kiro/tools/aidlc-utility.ts"
CLI = ".kiro/settings/cli.json"


def register(studio, h, root: Path):
    """Insert ``root`` under its real device/inode identity, which uninstall re-proves before every write."""
    st = root.stat()
    return insert_repo(studio, h.store, root, resolved_identity=f"{st.st_dev}:{st.st_ino}")


@pytest.fixture
def installed(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    (root / "README.md").write_bytes(b"User project\n")
    (root / "AGENTS.md").write_bytes(b"# User instructions\n")
    (root / ".gitignore").write_bytes(b"user-ignore\n")
    (root / ".kiro/settings").mkdir(parents=True)
    (root / CLI).write_text('{"user.setting": "keep me"}\n')
    assert do(h, register(studio, h, root), "install").status == "committed"
    return root, reload_repo(studio, h.store)


def uninstall(h, repo, **kwargs):
    plan = h.installer.preview(repo, kind="uninstall")
    return run(h.installer.uninstall(repo, confirm_digest=plan.digest(), **kwargs))


def strip_uninstall_metadata(h, receipt) -> None:
    """Rewrite ``receipt`` the way a pre-uninstall build stored it: no per-file ``uninstall`` provenance."""
    files = [{key: value for key, value in f.to_json().items() if key != "uninstall"} for f in receipt.files]
    h.store.update("install_receipts", receipt.receipt_id, {"files_json": files})


def interrupted_uninstall(h, repo):
    """``(plan, failed)``: an uninstall whose removal and then its own restore failed."""
    plan = h.installer.preview(repo, "uninstall")
    h.fault.at("after:remove_files", "before:restore_backups")
    failed = run(h.installer.uninstall(repo, confirm_digest=plan.digest()))
    h.fault.markers.clear()
    return plan, failed


def test_preview_uninstall_is_receipt_based_and_read_only(h, installed):
    root, repo = installed
    before = tree(root)
    plan = h.installer.preview(repo, kind="uninstall")
    assert plan.kind == "uninstall" and not plan.blocking
    assert plan.receipt_id == h.installer.current_receipt(repo.repo_id).receipt_id
    entries = {entry.path: entry for entry in plan.entries}
    assert entries[OWNED].action == "remove"
    assert entries["AGENTS.md"].action == "remove_fragment"
    assert not any(path.startswith("aidlc/") for path in entries)
    assert tree(root) == before
    assert h.installer.preview(repo, "uninstall").digest() == plan.digest()


def test_uninstall_preserves_user_data_and_allows_reinstall(studio, h, installed):
    root, repo = installed
    (root / "aidlc/spaces/default/memory/project.md").write_bytes(b"\xffworkflow memory\n")
    (root / ".kiro/user-tool.txt").write_bytes(b"another tool")
    workflow = tree(root / "aidlc")
    current = h.installer.current_receipt(repo.repo_id)
    result = uninstall(h, repo)
    assert result.kind == "uninstall" and result.status == "committed"
    assert result.prior_receipt_id == current.receipt_id and result.receipt_id is None
    assert h.installer.current_receipt(repo.repo_id) is None
    assert h.installer.receipt(current.receipt_id).status == "uninstalled"
    row = h.store.get("repos", repo.repo_id)
    assert row["install_status"] == "not_installed"
    assert row["receipt_version"] is None and row["installed_engine_version"] is None
    assert not (root / OWNED).exists()
    assert (root / ".kiro/user-tool.txt").read_bytes() == b"another tool"
    assert (root / "README.md").read_bytes() == b"User project\n"
    assert b"# User instructions\n" in (root / "AGENTS.md").read_bytes()
    assert b"aidlc-studio:managed" not in (root / "AGENTS.md").read_bytes()
    assert (root / ".gitignore").read_bytes() == b"user-ignore\n"
    assert json.loads((root / CLI).read_bytes()) == {"user.setting": "keep me"}
    assert tree(root / "aidlc") == workflow
    assert do(h, reload_repo(studio, h.store), "install").status == "committed"
    assert tree(root / "aidlc") == workflow


@pytest.mark.parametrize("change", ["format", "space_resources", "user_content"])
@pytest.mark.parametrize("rollback", [False, True])
def test_uninstall_preserves_changed_mutable_config(h, installed, change, rollback):
    root, repo = installed
    target = root / ".kiro/agents/aidlc.json"
    content = json.loads(target.read_bytes())
    if change == "format":
        changed = json.dumps(content, indent=2).encode() + b"\n"
    elif change == "space_resources":
        changed = json.dumps({**content, "resources": ["aidlc/spaces/team/memory"]}).encode() + b"\n"
    else:
        changed = b"User-maintained agent configuration\n"
    assert changed != target.read_bytes()
    target.write_bytes(changed)
    before = tree(root)
    workflow = tree(root / "aidlc")
    receipt = h.installer.current_receipt(repo.repo_id)
    plan = h.installer.preview(repo, "uninstall")
    preserved = entry_of(plan, ".kiro/agents/aidlc.json")
    assert not plan.blocking and not preserved.blocking
    assert preserved.action == "preserve" and preserved.reason == "engine_mutable_changed"
    assert tree(root) == before
    if rollback:
        h.fault.at("after:remove_files")
    assert uninstall(h, repo).status == ("rolled_back" if rollback else "committed")
    assert target.read_bytes() == changed
    assert tree(root / "aidlc") == workflow
    assert (root / "README.md").read_bytes() == b"User project\n"
    if rollback:
        assert tree(root) == before
        assert h.installer.current_receipt(repo.repo_id).receipt_id == receipt.receipt_id
    else:
        assert not (root / OWNED).exists()
        assert h.installer.current_receipt(repo.repo_id) is None


@pytest.mark.parametrize("path", [OWNED, "AGENTS.md", CLI])
def test_modified_owned_content_blocks_without_touching_anything(studio, h, installed, path):
    root, repo = installed
    target = root / path
    if path == CLI:
        target.write_text(json.dumps({**json.loads(target.read_bytes()), "chat.defaultAgent": "user-agent"}))
    else:
        target.write_bytes(b"user changed this owned content\n")
    before = tree(root)
    plan = h.installer.preview(repo, "uninstall")
    assert plan.blocking and path in {e.path for e in plan.blockers}
    with pytest.raises(studio.errors.StudioError) as exc:
        run(h.installer.uninstall(repo, confirm_digest=plan.digest()))
    assert exc.value.code == "install_conflict"
    assert tree(root) == before and h.installer.current_receipt(repo.repo_id)


def test_no_receipt_cannot_authorize_uninstall(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    (root / "AGENTS.md").write_bytes(b"unowned")
    with pytest.raises(studio.errors.StudioError) as exc:
        h.installer.preview(register(studio, h, root), "uninstall")
    assert exc.value.code == "not_installed"
    assert (root / "AGENTS.md").read_bytes() == b"unowned"


@pytest.mark.parametrize("marker", [f"{side}:{step}" for side in ("before", "after") for step in (
    "acquire_admin_lease", "backup", "remove_files", "remove_fragments", "post_uninstall_validate",
    "commit_uninstall", "release_lease")])
def test_failure_at_every_uninstall_step_is_atomic(studio, h, installed, marker):
    root, repo = installed
    before = tree(root)
    receipt = h.installer.current_receipt(repo.repo_id)
    h.fault.at(marker)
    result = uninstall(h, repo)
    assert marker in h.fault.seen
    if marker in {"after:commit_uninstall", "before:release_lease", "after:release_lease"}:
        assert result.status == "committed"
        assert h.installer.current_receipt(repo.repo_id) is None
        assert not (root / OWNED).exists()
    else:
        assert result.status in {"failed", "rolled_back"}
        assert tree(root) == before
        assert h.installer.current_receipt(repo.repo_id).receipt_id == receipt.receipt_id
        assert h.store.get("repos", repo.repo_id)["receipt_version"] == receipt.receipt_id
        assert (Path(result.failed_dir) / "transaction.json").is_file()
    if h.lease() is not None:
        assert result.status in studio.leases.TRANSACTION_SETTLED_STATUS


def test_mid_file_removal_failure_restores_bytes_and_modes(h, installed):
    root, repo = installed
    (root / OWNED).chmod(0o751)
    before = tree(root)
    h.fault.at(f"after:uninstall_path:{OWNED}")
    result = uninstall(h, repo)
    assert result.status == "rolled_back" and tree(root) == before
    assert (root / OWNED).stat().st_mode & 0o777 == 0o751


def test_begin_run_and_terminal_retry_are_stable_interfaces(h, installed):
    root, repo = installed
    plan = h.installer.preview(repo, "uninstall")
    pending = run(h.installer.begin(repo, "uninstall", plan_digest=plan.digest()))
    assert pending.kind == "uninstall" and pending.status == "staged"
    result = run(h.installer.run(repo, "uninstall", plan_digest=plan.digest(), transaction_id=pending.transaction_id))
    assert result.status == "committed" and result.transaction_id == pending.transaction_id
    after = tree(root)
    again = run(h.installer.uninstall(repo, confirm_digest=plan.digest(), transaction_id=pending.transaction_id))
    assert again == result and tree(root) == after


def test_uninstall_never_runs_engine_or_needs_current_payload(h, installed, tmp_path, monkeypatch):
    h.installer._payload_root = tmp_path / "nonexistent-payload"

    def forbidden(*args, **kwargs):
        pytest.fail("uninstall must not run an engine or verify today's payload")

    monkeypatch.setattr(h.installer._engine, "run_sync", forbidden)
    monkeypatch.setattr(h.installer, "verify_payload", forbidden)
    assert uninstall(h, installed[1]).status == "committed"


def test_later_bundle_uses_receipt_fragment_definitions(h, installed, make_harness, payload_v2):
    root, repo = installed
    assert uninstall(make_harness(payload_v2), repo).status == "committed"
    assert (root / ".gitignore").read_bytes() == b"user-ignore\n"
    assert json.loads((root / CLI).read_bytes()) == {"user.setting": "keep me"}


def test_preexisting_matching_keys_and_lines_are_preserved(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    cli = root / CLI
    cli.parent.mkdir(parents=True)
    shipped = json.loads((h.installer.payload_root / CLI).read_bytes())
    original = {"chat.defaultAgent": shipped["chat.defaultAgent"], "user": "keep"}
    cli.write_text(json.dumps(original))
    line = (h.installer.payload_root / ".gitignore").read_bytes().splitlines(keepends=True)[0]
    (root / ".gitignore").write_bytes(b"my-line\n" + line)
    repo = register(studio, h, root)
    assert do(h, repo, "install").status == "committed"
    assert uninstall(h, repo).status == "committed"
    assert json.loads(cli.read_bytes()) == original
    assert (root / ".gitignore").read_bytes() == b"my-line\n" + line


def test_json_removal_preserves_user_escapes_and_formatting(h, installed):
    root, repo = installed
    cli = root / CLI
    literal = b'"user.secret" : "\\u0061\\u0062", "user.object" : { "nested" : [1, 2] }'
    cli.write_bytes(cli.read_bytes().rstrip()[:-1] + b",\n  " + literal + b"\n}\n")
    assert uninstall(h, repo).status == "committed"
    assert literal in cli.read_bytes()
    assert json.loads(cli.read_bytes()) == {
        "user.setting": "keep me", "user.secret": "ab", "user.object": {"nested": [1, 2]}}


@pytest.mark.parametrize("body,keys,parents,expected", [
    (b'{"a":1,"keep":"\\u0061","b":2}', ["a", "b"], [], {"keep": "a"}),
    (b'{"keep":1,"a":{"b":2,"c":3}}', ["a.b"], [], {"keep": 1, "a": {"c": 3}}),
    (b'{"keep":1,"a":{"b":2}}', ["a.b"], [["a"]], {"keep": 1}),
    (b'{"a.b":2,"a":{"b":3},"keep":1}', ["a.b"], [], {"a": {"b": 3}, "keep": 1}),
])
def test_json_member_removal_has_no_collateral_keys(I, body, keys, parents, expected):
    after = I._remove_json_keys(body, keys, parents)
    assert json.loads(after) == expected
    if b"\\u0061" in body:
        assert b"\\u0061" in after


@pytest.mark.parametrize("damage", ["duplicate_json_key", "duplicate_block", "changed_ignore_line"])
def test_ambiguous_or_modified_fragments_block(h, installed, damage):
    root, repo = installed
    if damage == "duplicate_json_key":
        (root / CLI).write_bytes((root / CLI).read_bytes().rstrip()[:-1] + b',"user.setting":"other"}')
    elif damage == "duplicate_block":
        (root / "AGENTS.md").write_bytes((root / "AGENTS.md").read_bytes() * 2)
    else:
        ignore = root / ".gitignore"
        ignore.write_bytes(ignore.read_bytes().replace(b"aidlc/.aidlc-clone-id\n", b" aidlc/.aidlc-clone-id\n"))
    before = tree(root)
    assert h.installer.preview(repo, "uninstall").blocking
    assert tree(root) == before


def test_foreign_harness_and_workflow_survive_even_a_bad_receipt(studio, h, installed):
    root, repo = installed
    protected = {
        ".claude/tools/aidlc-version.ts": b'export const AIDLC_VERSION = "2.6.2";\n',
        "aidlc/spaces/custom/intents/item/state.md": b"\xffuser workflow\n",
        "README.md": b"User project\n",
    }
    for rel, data in protected.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    receipt = h.installer.current_receipt(repo.repo_id)
    files = [f.to_json() for f in receipt.files] + [
        {"path": rel, "ownership": "framework", "kind": "file", "sha256": studio.security.sha256_bytes(data),
         "adopted": False} for rel, data in protected.items()]
    h.store.update("install_receipts", receipt.receipt_id, {"files_json": files})
    for entry in h.installer.preview(repo, "uninstall").entries:
        if entry.path in protected:
            assert entry.action == "preserve" and entry.reason == "protected_path"
    assert uninstall(h, repo).status == "committed"
    for rel, data in protected.items():
        assert (root / rel).read_bytes() == data
    row = h.store.get("repos", repo.repo_id)
    assert row["receipt_version"] is None and row["engine_dir"] == ".claude"
    assert row["install_status"] == "installed"


def test_missing_owned_files_are_honestly_listed(h, installed):
    root, repo = installed
    (root / OWNED).unlink()
    assert entry_of(h.installer.preview(repo, "uninstall"), OWNED).action == "already_absent"
    assert uninstall(h, repo).status == "committed"


def test_legacy_receipt_preserves_unprovable_fragments(h, installed):
    root, repo = installed
    strip_uninstall_metadata(h, h.installer.current_receipt(repo.repo_id))
    cli = (root / CLI).read_bytes()
    ignore = (root / ".gitignore").read_bytes()
    plan = h.installer.preview(repo, "uninstall")
    assert not plan.blocking
    for entry in plan.entries:
        if entry.path in {CLI, ".gitignore"}:
            assert entry.action == "preserve" and entry.reason == "legacy_fragment_ownership_unknown"
    assert uninstall(h, repo).status == "committed"
    assert (root / CLI).read_bytes() == cli
    assert (root / ".gitignore").read_bytes() == ignore
    assert b"aidlc-studio:managed" not in (root / "AGENTS.md").read_bytes()


def test_stale_digest_and_transaction_identity_are_rejected(studio, h, installed):
    root, repo = installed
    plan = h.installer.preview(repo, "uninstall")
    edited = {**json.loads((root / CLI).read_bytes()), "user.setting": "edited after preview"}
    (root / CLI).write_text(json.dumps(edited))
    before = tree(root)
    with pytest.raises(studio.errors.StudioError) as exc:
        run(h.installer.uninstall(repo, confirm_digest=plan.digest()))
    assert exc.value.code == "install_conflict" and tree(root) == before
    plan = h.installer.preview(repo, "uninstall")
    pending = run(h.installer.begin(repo, "uninstall", plan_digest=plan.digest()))
    with pytest.raises(studio.errors.StudioError):
        run(h.installer.uninstall(repo, confirm_digest="wrong", transaction_id=pending.transaction_id))
    assert tree(root) == before


@pytest.mark.parametrize("kind", ["execution", "admin"])
def test_uninstall_never_preempts_a_lease(studio, h, installed, kind):
    root, repo = installed
    before = tree(root)
    if kind == "admin":
        held = run(h.scheduler.acquire_admin(repo, operation_type="install", transaction_id=None))
    else:
        held = run(h.scheduler.acquire_execution(repo, intent_uuid=None, session_key=None, action_id="busy"))
    with pytest.raises(studio.errors.LeaseHeld):
        uninstall(h, repo)
    assert tree(root) == before
    assert run(h.scheduler.get(repo.resolved_identity)).generation == held.generation
    run(h.scheduler.release(held))


def test_uninstall_uses_a_real_admin_lease_through_commit(h, installed):
    observed = []

    def check():
        lease = h.lease()
        assert lease is not None and lease.operation_type == "recovery"
        assert h.store.get("install_transactions", lease.transaction_id)["kind"] == "uninstall"
        observed.append(lease.generation)

    for marker in ("before:backup", "before:remove_files", "before:remove_fragments",
                   "before:post_uninstall_validate", "before:commit_uninstall"):
        h.fault.on(marker, check)
    assert uninstall(h, installed[1]).status == "committed"
    assert len(observed) == 5 and len(set(observed)) == 1
    assert h.lease() is None


def test_user_edit_after_backup_is_never_removed_or_rolled_over(h, installed):
    root, repo = installed
    h.fault.on("before:remove_files", lambda: (root / OWNED).write_bytes(b"concurrent user edit"))
    assert uninstall(h, repo).status == "recovery_required"
    assert (root / OWNED).read_bytes() == b"concurrent user edit"
    assert h.installer.current_receipt(repo.repo_id) is not None


def test_record_commit_is_atomic_on_database_error(h, installed, monkeypatch):
    root, repo = installed
    before = tree(root)
    original = h.store.update
    fired = False

    def fail(table, key, values):
        nonlocal fired
        if table == "repos" and values.get("receipt_version", "absent") is None and not fired:
            fired = True
            raise OSError("between receipt and repo update")
        return original(table, key, values)

    monkeypatch.setattr(h.store, "update", fail)
    result = uninstall(h, repo)
    assert fired and result.status == "rolled_back" and tree(root) == before
    receipt = h.installer.current_receipt(repo.repo_id)
    assert receipt and receipt.status == "current"
    assert h.store.get("repos", repo.repo_id)["receipt_version"] == receipt.receipt_id


def test_failed_rollback_can_be_recovered_then_uninstalled(h, installed):
    root, repo = installed
    before = tree(root)
    plan, failed = interrupted_uninstall(h, repo)
    assert failed.status == "recovery_required"
    recovered = run(h.installer.recover_uninstall(repo, transaction_id=failed.transaction_id,
                                                  confirm_digest=plan.digest()))
    assert recovered.status == "rolled_back" and tree(root) == before
    assert h.store.get("repos", repo.repo_id)["install_status"] == "installed"
    assert uninstall(h, repo).status == "committed"


def test_real_process_death_settlement_and_recovery(h, installed):
    root, repo = installed
    before = tree(root)
    plan = h.installer.preview(repo, "uninstall")
    reserved = run(h.installer.begin(repo, "uninstall", plan_digest=plan.digest()))

    def child():
        h.store.close()
        h.store.open()
        h.fault.on("after:remove_files", lambda: os._exit(77))
        run(h.installer.uninstall(repo, confirm_digest=plan.digest(), transaction_id=reserved.transaction_id))

    die_in_child(child)
    assert not (root / OWNED).exists()
    assert run(h.installer.settle_abandoned()) == [reserved.transaction_id]
    dead = h.installer.transaction(reserved.transaction_id)
    assert dead.status == "recovery_required"
    assert (Path(dead.failed_dir) / "transaction.json").is_file()
    # The child died holding the lock; the recovery can take it only because the kernel let go.
    recovered = run(h.installer.recover_uninstall(repo, transaction_id=dead.transaction_id,
                                                  confirm_digest=plan.digest()))
    assert recovered.status == "rolled_back" and tree(root) == before
    assert run(h.installer.settle_abandoned()) == []


@pytest.mark.parametrize("damage", ["backup", "journal_path", "journal_omission", "journal_repo_fields"])
def test_corrupt_recovery_evidence_cannot_claim_success(studio, h, installed, damage):
    root, repo = installed
    plan, failed = interrupted_uninstall(h, repo)
    directory = h.data_dir / "backups" / failed.transaction_id
    journal_path = directory / "uninstall-journal.json"
    journal = json.loads(journal_path.read_bytes())
    if damage == "backup":
        (directory / OWNED).write_bytes(b"corrupt")
    elif damage == "journal_path":
        journal["files"][0]["path"] = "../outside"
    elif damage == "journal_omission":
        journal["files"] = []
    else:
        journal["prior_repo"]["canonical_path"] = "/must-not-be-rebound"
    if damage != "backup":
        journal_path.write_text(json.dumps(journal))
    try:
        result = run(h.installer.recover_uninstall(repo, transaction_id=failed.transaction_id,
                                                   confirm_digest=plan.digest()))
    except studio.errors.StudioError:
        pass
    else:
        assert result.status == "recovery_required"
    assert h.store.get("repos", repo.repo_id)["canonical_path"] == str(root)
    assert h.installer.current_receipt(repo.repo_id) is not None


@pytest.mark.parametrize("location", ["file", "parent", "backup"])
def test_uninstall_rejects_symlink_boundaries(studio, h, installed, tmp_path, location):
    root, repo = installed
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"do not touch")
    if location == "file":
        (root / OWNED).unlink()
        (root / OWNED).symlink_to(sentinel)
    elif location == "parent":
        (root / ".kiro").rename(outside / "kiro")
        (root / ".kiro").symlink_to(outside / "kiro")
    if location == "backup":
        plan = h.installer.preview(repo, "uninstall")
        pending = run(h.installer.begin(repo, "uninstall", plan_digest=plan.digest()))
        (h.data_dir / "backups" / pending.transaction_id).symlink_to(outside)
        with pytest.raises(studio.errors.StudioError):
            run(h.installer.uninstall(repo, confirm_digest=plan.digest(), transaction_id=pending.transaction_id))
    else:
        assert h.installer.preview(repo, "uninstall").blocking
    assert sentinel.read_bytes() == b"do not touch"
    assert h.lease() is None


def test_root_replacement_at_a_file_boundary_cannot_delete_new_repository(h, installed, tmp_path):
    root, repo = installed
    moved = tmp_path / "original-root"
    replacement = {}

    def swap():
        root.rename(moved)
        shutil.copytree(moved, root)
        replacement.update(tree(root))

    first = next(f.path for f in h.installer.current_receipt(repo.repo_id).files if f.kind == "file")
    h.fault.on(f"before:uninstall_path:{first}", swap)
    assert uninstall(h, repo).status == "recovery_required"
    assert tree(root) == replacement


def test_adopted_markerless_document_is_explicitly_preserved(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    original = (h.installer.payload_root / "AGENTS.md").read_bytes()
    (root / "AGENTS.md").write_bytes(original)
    repo = register(studio, h, root)
    assert do(h, repo, "install").status == "committed"
    plan = h.installer.preview(repo, "uninstall")
    preserved = entry_of(plan, "AGENTS.md")
    assert not plan.blocking and preserved.action == "preserve" and preserved.reason == "preexisting_fragment"
    assert uninstall(h, repo).status == "committed"
    assert (root / "AGENTS.md").read_bytes() == original


def fenced_block(body: bytes) -> bytes:
    """A user document holding a Studio-shaped block that the user, not Studio, put there."""
    return (b"# User rules\n<!-- aidlc-studio:managed:start -->\n" + body
            + b"\n<!-- aidlc-studio:managed:end -->\nKeep this tail.\n")


@pytest.mark.parametrize("origin", [
    "preexisting_fenced", "preexisting_markerless", "studio_added", "legacy_fenced", "legacy_markerless",
])
def test_block_removal_ownership_survives_upgrade_and_recovery(studio, h, repo_builder, make_harness, payload_v2,
                                                               origin, tmp_path):
    root = repo_builder.with_git().build()
    body = (h.installer.payload_root / "AGENTS.md").read_bytes().strip(b"\n")
    if origin in {"preexisting_fenced", "legacy_fenced"}:
        original = fenced_block(body)
    elif origin in {"preexisting_markerless", "legacy_markerless"}:
        original = body + b"\n"
    else:
        original = b"# User rules\n"
    agents = root / "AGENTS.md"
    agents.write_bytes(original)
    repo = register(studio, h, root)
    assert do(h, repo, "install").status == "committed"
    studio_added = origin == "studio_added"
    legacy = origin.startswith("legacy_")
    assert h.installer.current_receipt(repo.repo_id).by_path()["AGENTS.md"].uninstall["block"] is studio_added
    if legacy:
        strip_uninstall_metadata(h, h.installer.current_receipt(repo.repo_id))
    # A legacy JSON fragment must stay unchanged: a new model without entry metadata fails the CLI ownership guard.
    later = make_harness(build_upgrade_payload(tmp_path / "legacy-upgrade", change_model_defaults=False)
                         if legacy else payload_v2)
    if legacy:
        cli = entry_of(later.installer.preview(repo, "upgrade"), CLI)
        assert cli.action == "merge_identical" and cli.payload_sha256 == cli.receipt_sha256
    assert do(later, reload_repo(studio, h.store), "upgrade").status == "committed"
    upgraded_bytes = agents.read_bytes()
    assert b"# AI-DLC v2" in upgraded_bytes and upgraded_bytes != original
    # Re-receipting identical content must inherit provenance as well as an actual version rewrite.
    assert do(later, reload_repo(studio, h.store), "recovery").status == "committed"
    receipt = later.installer.current_receipt(repo.repo_id)
    repo = reload_repo(studio, h.store)
    plan = later.installer.preview(repo, "uninstall")
    assert not plan.blocking
    assert uninstall(later, repo).status == "committed"
    if studio_added:
        assert b"aidlc-studio:managed" not in agents.read_bytes()
        assert agents.read_bytes().startswith(original)
    else:
        assert agents.read_bytes() == upgraded_bytes
    removal = entry_of(plan, "AGENTS.md")
    assert removal.action == ("remove_fragment" if studio_added else "preserve")
    assert receipt.by_path()["AGENTS.md"].uninstall["block"] is studio_added
    if not studio_added:
        assert removal.reason == "preexisting_fragment"


@pytest.mark.parametrize("later_edit", [False, True])
def test_legacy_adopted_fenced_block_has_no_removal_authority(studio, h, repo_builder, later_edit):
    root = repo_builder.with_git().build()
    body = (h.installer.payload_root / "AGENTS.md").read_bytes().strip(b"\n")
    original = fenced_block(body)
    (root / "AGENTS.md").write_bytes(original)
    repo = register(studio, h, root)
    assert do(h, repo, "install").status == "committed"
    receipt = h.installer.current_receipt(repo.repo_id)
    assert receipt.by_path()["AGENTS.md"].adopted is True
    strip_uninstall_metadata(h, receipt)
    if later_edit:
        original = original.replace(body, b"User changed this pre-existing block.\xff")
        (root / "AGENTS.md").write_bytes(original)
    plan = h.installer.preview(repo, "uninstall")
    preserved = entry_of(plan, "AGENTS.md")
    assert not plan.blocking
    assert preserved.action == "preserve" and preserved.reason == "legacy_fragment_ownership_unknown"
    assert uninstall(h, repo).status == "committed"
    assert (root / "AGENTS.md").read_bytes() == original


def test_stale_confirmation_is_refused_before_reserving_a_transaction(studio, h, installed):
    root, repo = installed
    plan = h.installer.preview(repo, "uninstall")
    (root / "AGENTS.md").write_bytes(b"New user prefix\n" + (root / "AGENTS.md").read_bytes())
    before = tree(root)
    txids = {t.transaction_id for t in h.installer.transactions(repo.repo_id)}
    with pytest.raises(studio.errors.StudioError):
        run(h.installer.begin(repo, "uninstall", plan_digest=plan.digest()))
    assert tree(root) == before
    assert {t.transaction_id for t in h.installer.transactions(repo.repo_id)} == txids


def test_direct_uninstall_refuses_recovery_required_even_with_a_stale_repo(studio, h, installed):
    root, repo = installed
    plan = h.installer.preview(repo, "uninstall")
    h.store.update("repos", repo.repo_id, {"install_status": "recovery_required"})
    before = tree(root)
    with pytest.raises(studio.errors.StudioError) as exc:
        run(h.installer.uninstall(repo, confirm_digest=plan.digest()))
    assert exc.value.code == "install_recovery_required"
    assert tree(root) == before
    assert h.installer.current_receipt(repo.repo_id) is not None
    assert h.store.get("repos", repo.repo_id)["install_status"] == "recovery_required"


def test_uninstall_does_not_rescan_the_whole_receipt_per_path(h, installed, monkeypatch):
    scans = 0
    original = h.installer._registry.preflight

    def counted(*args, **kwargs):
        nonlocal scans
        scans += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(h.installer._registry, "preflight", counted)
    assert uninstall(h, installed[1]).status == "committed"
    assert scans <= 4  # preview/begin/under-lease revalidation; independent of receipt size


def test_two_late_invocations_of_one_reserved_id_cannot_overwrite_commit(h, installed, monkeypatch):
    repo = installed[1]
    plan = h.installer.preview(repo, "uninstall")
    pending = run(h.installer.begin(repo, "uninstall", plan_digest=plan.digest()))
    original_refuse = h.installer._refuse_if_busy
    original_acquire = h.installer._scheduler.acquire_admin

    async def race():
        arrived = 0
        acquiring = 0
        both = asyncio.Event()
        first_done = asyncio.Event()

        async def barrier(record):
            nonlocal arrived
            await original_refuse(record)
            arrived += 1
            if arrived == 2:
                both.set()
            await both.wait()

        async def delayed(record, **kwargs):
            nonlocal acquiring
            acquiring += 1
            if acquiring == 2:
                await first_done.wait()
            return await original_acquire(record, **kwargs)

        monkeypatch.setattr(h.installer, "_refuse_if_busy", barrier)
        monkeypatch.setattr(h.installer._scheduler, "acquire_admin", delayed)

        async def invoke():
            result = await h.installer.uninstall(repo, confirm_digest=plan.digest(),
                                                 transaction_id=pending.transaction_id)
            first_done.set()
            return result

        return await asyncio.wait_for(asyncio.gather(invoke(), invoke()), timeout=10)

    assert [r.status for r in run(race())] == ["committed", "committed"]
    assert h.installer.transaction(pending.transaction_id).status == "committed"
