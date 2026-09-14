"""One-hop engine rollback uses receipt-linked backups and never rolls back workflow data."""

from __future__ import annotations

import json
import multiprocessing
import os
import shutil
from pathlib import Path

import pytest

from test_installer import (
    I, do, h, make_harness, payload_v1, payload_v2, reload_repo, run, store, tree,
)
from test_installer_uninstall import OWNED, installed


@pytest.fixture
def upgraded(studio, h, installed, make_harness, payload_v2):
    root, repo = installed
    target = h.installer.current_receipt(repo.repo_id)
    upgrade = make_harness(payload_v2)
    assert do(upgrade, repo, "upgrade").status == "committed"
    return root, reload_repo(studio, h.store), upgrade, target


def rollback(harness, repo, **kwargs):
    plan = harness.installer.preview(repo, "rollback")
    return run(harness.installer.rollback(repo, confirm_digest=plan.digest(), **kwargs))


def test_one_hop_preview_is_read_only_and_binds_both_receipts(upgraded):
    root, repo, up, target = upgraded
    before = tree(root)
    current = up.installer.current_receipt(repo.repo_id)
    plan = up.installer.preview(repo, "rollback")
    assert not plan.blocking, [(e.path, e.reason) for e in plan.blockers]
    assert plan.kind == "rollback"
    assert plan.engine_from == "2.7.0" and plan.engine_to == "2.6.2"
    assert plan.receipt_id == current.receipt_id and plan.target_receipt_id == target.receipt_id
    assert plan.backup_transaction_id == current.transaction_id
    assert plan.to_json()["target_receipt_id"] == target.receipt_id
    assert not any(e.path.startswith("aidlc/") for e in plan.entries)
    assert tree(root) == before
    assert up.installer.preview(repo, "rollback", target_receipt_id=target.receipt_id).digest() == plan.digest()


def test_rollback_restores_old_engine_keeps_user_data_and_can_upgrade_again(studio, upgraded):
    root, repo, up, target = upgraded
    (root / "aidlc/spaces/default/memory/project.md").write_bytes(b"\xffcurrent workflow memory\n")
    (root / ".kiro/other-harness-note").write_bytes(b"user note")
    (root / "AGENTS.md").write_bytes(b"New user instructions\n" + (root / "AGENTS.md").read_bytes())
    cli = root / ".kiro/settings/cli.json"
    cli.write_text(json.dumps({**json.loads(cli.read_bytes()), "new.user.setting": "preserve"}))
    workflow = tree(root / "aidlc")
    old_files = {rf.path: rf.sha256 for rf in target.files if rf.kind == "file"}
    result = rollback(up, repo)
    assert result.kind == "rollback" and result.status == "committed", result
    assert result.engine_version == target.engine_version
    current = up.installer.current_receipt(repo.repo_id)
    assert current.receipt_id != target.receipt_id and current.engine_version == target.engine_version
    assert {path: tree(root)[path] for path in old_files} == old_files
    assert (root / "AGENTS.md").read_bytes().startswith(b"New user instructions\n")
    assert json.loads(cli.read_bytes())["new.user.setting"] == "preserve"
    assert (root / ".kiro/other-harness-note").read_bytes() == b"user note"
    assert tree(root / "aidlc") == workflow
    assert do(up, reload_repo(studio, up.store), "upgrade").status == "committed"
    assert tree(root / "aidlc") == workflow


def test_rollback_begin_run_and_terminal_reentry(upgraded):
    root, repo, up, target = upgraded
    plan = up.installer.preview(repo, "rollback")
    pending = run(up.installer.begin(
        repo, "rollback", plan_digest=plan.digest(), target_receipt_id=target.receipt_id,
    ))
    result = run(up.installer.run(
        repo, "rollback", plan_digest=plan.digest(), transaction_id=pending.transaction_id,
    ))
    assert result.status == "committed"
    before = tree(root)
    assert run(up.installer.rollback(
        repo, confirm_digest=plan.digest(), transaction_id=pending.transaction_id,
    )) == result
    assert tree(root) == before


@pytest.mark.parametrize("damage", ["missing", "corrupt", "symlink"])
def test_rollback_missing_or_corrupt_backup_blocks_without_repo_writes(studio, upgraded, tmp_path, damage):
    root, repo, up, _ = upgraded
    current = up.installer.current_receipt(repo.repo_id)
    source = up.data_dir / "backups" / current.transaction_id / OWNED
    if damage == "corrupt":
        source.write_bytes(b"not the old engine")
    else:
        source.unlink()
        if damage == "symlink":
            outside = tmp_path / "outside"
            outside.write_bytes(b"external")
            source.symlink_to(outside)
    before = tree(root)
    plan = up.installer.preview(repo, "rollback")
    assert plan.blocking and any(e.path == OWNED and e.reason for e in plan.blockers)
    with pytest.raises(studio.errors.StudioError):
        run(up.installer.rollback(repo, confirm_digest=plan.digest()))
    assert tree(root) == before


@pytest.mark.parametrize("path", [OWNED, ".kiro/skills/aidlc/SKILL.md"])
def test_rollback_rejects_drift_and_stale_confirmation(studio, upgraded, path):
    root, repo, up, _ = upgraded
    plan = up.installer.preview(repo, "rollback")
    (root / path).write_bytes(b"user edit")
    before = tree(root)
    assert up.installer.preview(repo, "rollback").blocking
    with pytest.raises(studio.errors.StudioError):
        run(up.installer.rollback(repo, confirm_digest=plan.digest()))
    assert tree(root) == before


def test_rollback_only_accepts_the_immediate_prior_receipt(studio, upgraded):
    root, repo, up, _ = upgraded
    before = tree(root)
    with pytest.raises(studio.errors.StudioError):
        up.installer.preview(repo, "rollback", target_receipt_id="not-the-prior-receipt")
    assert tree(root) == before


def test_rollback_requires_known_state_compatibility(upgraded):
    _, repo, up, target = upgraded
    files = [f.to_json() for f in target.files]
    for f in files:
        f.pop("engine_state_versions", None)
    up.store.update("install_receipts", target.receipt_id, {"files_json": files})
    plan = up.installer.preview(repo, "rollback")
    assert plan.blocking
    assert any(e.reason == "target_compatibility_unknown" for e in plan.blockers)


@pytest.mark.parametrize("marker", [
    "before:backup", "after:backup", "before:restore_version", "after:restore_version",
    "before:post_rollback_validate", "after:post_rollback_validate", "before:commit_rollback",
    "after:commit_rollback",
])
def test_rollback_faults_restore_the_current_engine_or_commit_atomically(upgraded, marker):
    root, repo, up, _ = upgraded
    before = tree(root)
    receipt = up.installer.current_receipt(repo.repo_id)
    up.fault.at(marker)
    result = rollback(up, repo)
    if marker == "after:commit_rollback":
        assert result.status == "committed"
    else:
        assert result.status in {"failed", "rolled_back"}, result
        assert tree(root) == before
        assert up.installer.current_receipt(repo.repo_id).receipt_id == receipt.receipt_id
    assert up.store.lease_get("admin", repo.resolved_identity) is None


def test_rollback_rechecks_unchanged_owned_files_before_commit(upgraded):
    root, repo, up, target = upgraded
    plan = up.installer.preview(repo, "rollback")
    files = {f.path for f in target.files if f.kind == "file"}
    unchanged = next(e.path for e in plan.entries if e.action == "identical" and e.path in files)
    up.fault.on("before:commit_rollback", lambda: (root / unchanged).write_bytes(b"concurrent user edit"))
    result = rollback(up, repo)
    assert result.status == "recovery_required"
    assert (root / unchanged).read_bytes() == b"concurrent user edit"
    assert up.installer.current_receipt(repo.repo_id).engine_version == "2.7.0"


def test_rollback_keeps_a_post_backup_user_edit(upgraded):
    root, repo, up, _ = upgraded
    plan = up.installer.preview(repo, "rollback")
    changed = next(e.path for e in plan.entries if e.action == "restore_version")
    up.fault.on(f"after:rollback_path:{changed}",
                lambda: (root / changed).write_bytes(b"new user content"))
    result = rollback(up, repo)
    assert result.status == "recovery_required"
    assert (root / changed).read_bytes() == b"new user content"


def test_rollback_does_not_overwrite_an_edit_before_the_first_write(upgraded):
    root, repo, up, _ = upgraded
    plan = up.installer.preview(repo, "rollback")
    changed = next(e.path for e in plan.entries if e.action == "restore_version")

    def edit():
        (root / changed).parent.mkdir(parents=True, exist_ok=True)
        (root / changed).write_bytes(b"new user content")

    up.fault.on(f"before:rollback_path:{changed}", edit)
    result = rollback(up, repo)
    assert result.status == "recovery_required"
    assert (root / changed).read_bytes() == b"new user content"


def test_rollback_does_not_need_the_bundled_payload(upgraded, tmp_path):
    _, repo, up, _ = upgraded
    up.installer._payload_root = tmp_path / "bundle-not-present"
    assert rollback(up, repo).status == "committed"


@pytest.mark.parametrize("kind", ["execution", "admin"])
def test_rollback_never_preempts_an_existing_lease(studio, upgraded, kind):
    root, repo, up, _ = upgraded
    before = tree(root)
    fields = {"repo_id": repo.repo_id, **({"operation_type": "repo_metadata"} if kind == "admin"
                                        else {"action_id": "active"})}
    grant = up.store.lease_acquire(kind, repo.resolved_identity, fields)
    with pytest.raises(studio.errors.LeaseHeld):
        rollback(up, repo)
    assert tree(root) == before
    assert up.store.lease_get(kind, repo.resolved_identity).generation == grant.generation


def test_rollback_record_commit_is_atomic(upgraded, monkeypatch):
    root, repo, up, target = upgraded
    before = tree(root)
    current = up.installer.current_receipt(repo.repo_id)
    ids = {r.receipt_id for r in up.installer.receipts(repo.repo_id)}
    update = up.store.update
    triggered = False

    def fail(table, pk, values):
        nonlocal triggered
        if table == "repos" and values.get("installed_engine_version") == target.engine_version and not triggered:
            triggered = True
            raise OSError("database fault after receipt insert")
        return update(table, pk, values)

    monkeypatch.setattr(up.store, "update", fail)
    result = rollback(up, repo)
    assert triggered and result.status == "rolled_back"
    assert tree(root) == before
    assert up.installer.current_receipt(repo.repo_id).receipt_id == current.receipt_id
    assert {r.receipt_id for r in up.installer.receipts(repo.repo_id)} == ids


def test_failed_version_rollback_can_be_recovered_and_retried(upgraded):
    root, repo, up, _ = upgraded
    before = tree(root)
    plan = up.installer.preview(repo, "rollback")
    up.fault.at("after:restore_version", "before:restore_backups")
    failed = rollback(up, repo)
    assert failed.status == "recovery_required"
    up.fault.markers.clear()
    result = run(up.installer.recover_rollback(
        repo, transaction_id=failed.transaction_id, confirm_digest=plan.digest(),
    ))
    assert result.status == "rolled_back" and tree(root) == before
    assert rollback(up, repo).status == "committed"


def test_real_process_death_during_version_rollback_recovers(studio, upgraded, clock):
    root, repo, up, _ = upgraded
    before = tree(root)
    plan = up.installer.preview(repo, "rollback")
    pending = run(up.installer.begin(repo, "rollback", plan_digest=plan.digest()))

    def child():
        up.store.close()
        up.store.open()
        up.fault.on("after:restore_version", lambda: os._exit(77))
        run(up.installer.run(
            repo, "rollback", plan_digest=plan.digest(), transaction_id=pending.transaction_id,
        ))

    process = multiprocessing.get_context("fork").Process(target=child)
    process.start()
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join()
        pytest.fail("isolated rollback process did not exit")
    assert process.exitcode == 77
    up.installer._boot_id = up.installer._scheduler._boot_id = "rollback-restarted"
    assert run(up.installer.settle_abandoned()) == [pending.transaction_id]
    dead = up.installer.transaction(pending.transaction_id)
    assert dead.status == "recovery_required" and (Path(dead.failed_dir) / "transaction.json").is_file()
    clock.advance(studio.constants.LEASE_STALE_SECS + 1)
    assert run(up.installer._scheduler.startup_sweep()) == []
    result = run(up.installer.recover_rollback(
        repo, transaction_id=dead.transaction_id, confirm_digest=plan.digest(),
    ))
    assert result.status == "rolled_back" and tree(root) == before
    assert run(up.installer.settle_abandoned()) == []


@pytest.mark.parametrize("damage", ["journal_omission", "journal_path", "backup"])
def test_version_rollback_recovery_rejects_damaged_evidence(studio, upgraded, damage):
    root, repo, up, _ = upgraded
    plan = up.installer.preview(repo, "rollback")
    up.fault.at("after:restore_version", "before:restore_backups")
    result = rollback(up, repo)
    assert result.status == "recovery_required"
    directory = up.data_dir / "backups" / result.transaction_id
    journal_path = directory / "rollback-journal.json"
    journal = json.loads(journal_path.read_bytes())
    if damage == "journal_omission":
        journal["files"] = []
    elif damage == "journal_path":
        journal["files"][0]["path"] = "../outside"
    else:
        file = next(f for f in journal["files"] if f["before"] is not None)
        (directory / file["path"]).write_bytes(b"corrupt backup")
    if damage != "backup":
        journal_path.write_text(json.dumps(journal))
    up.fault.markers.clear()
    try:
        recovered = run(up.installer.recover_rollback(
            repo, transaction_id=result.transaction_id, confirm_digest=plan.digest(),
        ))
    except studio.errors.StudioError:
        pass
    else:
        assert recovered.status == "recovery_required"
    assert up.store.get("repos", repo.repo_id)["install_status"] == "recovery_required"


def test_rollback_confirmation_changes_when_only_user_prose_changes(studio, upgraded):
    root, repo, up, _ = upgraded
    plan = up.installer.preview(repo, "rollback")
    agents = root / "AGENTS.md"
    agents.write_bytes(b"New user introduction\n" + agents.read_bytes())
    assert not up.installer.preview(repo, "rollback").blocking
    before = tree(root)
    with pytest.raises(studio.errors.StudioError):
        run(up.installer.begin(repo, "rollback", plan_digest=plan.digest()))
    assert tree(root) == before


def test_rollback_rejects_a_mismatched_reserved_confirmation(studio, upgraded):
    root, repo, up, target = upgraded
    plan = up.installer.preview(repo, "rollback")
    pending = run(up.installer.begin(repo, "rollback", plan_digest=plan.digest()))
    before = tree(root)
    with pytest.raises(studio.errors.StudioError):
        run(up.installer.rollback(repo, confirm_digest="wrong", transaction_id=pending.transaction_id))
    with pytest.raises(studio.errors.StudioError):
        run(up.installer.rollback(
            repo, confirm_digest=plan.digest(), target_receipt_id="other",
            transaction_id=pending.transaction_id,
        ))
    assert tree(root) == before


def test_rollback_lease_loss_stops_before_any_version_write(upgraded):
    root, repo, up, _ = upgraded
    before = tree(root)

    def replace_lease():
        held = up.store.lease_get("admin", repo.resolved_identity)
        up.store.lease_release("admin", repo.resolved_identity, held.generation)
        up.store.lease_acquire("execution", repo.resolved_identity, {
            "repo_id": repo.repo_id, "action_id": "new-owner",
        })

    up.fault.on("before:restore_version", replace_lease)
    result = rollback(up, repo)
    assert result.status == "recovery_required" and tree(root) == before
    assert up.store.lease_get("execution", repo.resolved_identity).action_id == "new-owner"


def test_rollback_rejects_root_replacement_at_a_file_boundary(upgraded, tmp_path):
    root, repo, up, _ = upgraded
    plan = up.installer.preview(repo, "rollback")
    path = next(e.path for e in plan.entries if e.action == "restore_version")
    replacement = {}

    def replace_root():
        original = tmp_path / "original-repo"
        root.rename(original)
        shutil.copytree(original, root)
        replacement.update(tree(root))

    up.fault.on(f"before:rollback_path:{path}", replace_root)
    result = rollback(up, repo)
    assert result.status == "recovery_required" and tree(root) == replacement


def test_rollback_refuses_incompatible_workflow_state(upgraded, repo_builder):
    import fixtures as F

    root, repo, up, _ = upgraded
    repo_builder.with_intent("260901-ledger-export", state=F.state_path(F.PAYLOAD_230, "260901-ledger-export"))
    before = tree(root / "aidlc")
    plan = up.installer.preview(repo, "rollback")
    assert plan.blocking and plan.state_version_blocked and plan.state_versions_found == (7,)
    assert tree(root / "aidlc") == before


@pytest.mark.parametrize("invalid", ["8", ["8"], [True], {"8": True}])
def test_malformed_compatibility_metadata_is_not_a_proof(upgraded, invalid):
    _, repo, up, target = upgraded
    files = [f.to_json() for f in target.files]
    files[0]["engine_state_versions"] = invalid
    up.store.update("install_receipts", target.receipt_id, {"files_json": files})
    plan = up.installer.preview(repo, "rollback")
    assert plan.blocking
    assert any(e.reason == "target_compatibility_unknown" for e in plan.blockers)


def test_rollback_receipt_can_be_uninstalled(studio, upgraded):
    root, repo, up, _ = upgraded
    assert rollback(up, repo).status == "committed"
    repo = reload_repo(studio, up.store)
    plan = up.installer.preview(repo, "uninstall")
    assert not plan.blocking
    assert run(up.installer.uninstall(repo, confirm_digest=plan.digest())).status == "committed"
    assert (root / "README.md").read_bytes() == b"User project\n"
