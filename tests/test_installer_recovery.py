"""The existing application recovery flow dispatches receipt-driven interrupted transactions."""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from test_installer import (
    I, do, h, make_harness, payload_v1, payload_v2, reload_repo, run, store, tree,
)
from test_installer_uninstall import installed
from test_installer_rollback import upgraded


@pytest.fixture(params=["uninstall", "rollback"])
def interrupted(request, installed, upgraded):
    # Both fixtures share the store: upgraded supplies a current v2 receipt for either operation.
    root, repo, harness, _ = upgraded
    before = tree(root)
    kind = request.param
    plan = harness.installer.preview(repo, kind)
    harness.fault.at(
        "after:remove_files" if kind == "uninstall" else "after:restore_version",
        "before:restore_backups",
    )
    failed = run(harness.installer.run(repo, kind, plan_digest=plan.digest()))
    assert failed.status == "recovery_required"
    harness.fault.markers.clear()
    return root, repo, harness, before, failed


def test_generic_recovery_preview_begin_run_restores_original_transaction(interrupted, tmp_path):
    root, repo, harness, before, failed = interrupted
    harness.installer._payload_root = tmp_path / "bundle-is-unavailable"
    plan = harness.installer.preview(repo, "recovery")
    assert not plan.blocking
    assert plan.kind == "recovery" and plan.recovery_transaction_id == failed.transaction_id
    assert plan.recovery_kind == failed.kind
    assert plan.to_json()["recovery_transaction_id"] == failed.transaction_id
    pending = run(harness.installer.begin(repo, "recovery", plan_digest=plan.digest()))
    assert pending.transaction_id == failed.transaction_id and pending.status == "staged"
    recovered = run(harness.installer.run(
        repo, "recovery", plan_digest=plan.digest(), transaction_id=pending.transaction_id,
    ))
    assert recovered.status == "rolled_back" and tree(root) == before
    assert harness.store.get("repos", repo.repo_id)["install_status"] == "installed"
    assert harness.store.lease_get("admin", repo.resolved_identity) is None
    assert run(harness.installer.run(
        repo, "recovery", plan_digest=plan.digest(), transaction_id=pending.transaction_id,
    )) == recovered


def test_generic_recovery_blocks_corrupt_evidence_instead_of_installing_bundle(interrupted):
    root, repo, harness, _, failed = interrupted
    path = harness.data_dir / "backups" / failed.transaction_id / f"{failed.kind}-journal.json"
    snapshot = json.loads(path.read_bytes())
    snapshot["files"] = []
    path.write_text(json.dumps(snapshot))
    before = tree(root)
    plan = harness.installer.preview(repo, "recovery")
    assert plan.blocking and plan.recovery_transaction_id == failed.transaction_id
    assert any(e.reason == "recovery_evidence_invalid" for e in plan.blockers)
    assert tree(root) == before


@pytest.mark.parametrize("valid_before_image", [False, True])
def test_generic_recovery_confirmation_is_rechecked_under_the_new_lease(
    studio, interrupted, valid_before_image,
):
    root, repo, harness, _, failed = interrupted
    plan = harness.installer.preview(repo, "recovery")
    pending = run(harness.installer.begin(repo, "recovery", plan_digest=plan.digest()))
    target = next(e.path for e in plan.entries if e.action == "restore_backup")
    changed = ((harness.data_dir / "backups" / failed.transaction_id / target).read_bytes()
               if valid_before_image else b"user's new work")

    def edit():
        (root / target).parent.mkdir(parents=True, exist_ok=True)
        (root / target).write_bytes(changed)

    harness.fault.on("after:acquire_admin_lease", edit)
    result = run(harness.installer.run(
        repo, "recovery", plan_digest=plan.digest(), transaction_id=pending.transaction_id,
    ))
    assert result.status == "recovery_required"
    assert (root / target).read_bytes() == changed
    assert harness.store.lease_get("admin", repo.resolved_identity) is None


def test_generic_recovery_stale_preview_is_refused_before_reservation(studio, interrupted):
    root, repo, harness, _, failed = interrupted
    plan = harness.installer.preview(repo, "recovery")
    target = next(e.path for e in plan.entries if e.action == "restore_backup")
    (root / target).parent.mkdir(parents=True, exist_ok=True)
    (root / target).write_bytes(b"new user data")
    changed = harness.installer.preview(repo, "recovery")
    assert changed.blocking
    assert any(e.reason == "user_changed_recovery_target" for e in changed.blockers)
    with pytest.raises(studio.errors.StudioError):
        run(harness.installer.begin(repo, "recovery", plan_digest=plan.digest()))
    assert harness.installer.transaction(failed.transaction_id).status == "recovery_required"


def test_generic_recovery_digest_binds_an_otherwise_safe_before_image(studio, interrupted):
    root, repo, harness, _, failed = interrupted
    plan = harness.installer.preview(repo, "recovery")
    target = next(e.path for e in plan.entries if e.action == "restore_backup")
    (root / target).parent.mkdir(parents=True, exist_ok=True)
    (root / target).write_bytes((harness.data_dir / "backups" / failed.transaction_id / target).read_bytes())
    assert not harness.installer.preview(repo, "recovery").blocking
    with pytest.raises(studio.errors.StudioError):
        run(harness.installer.begin(repo, "recovery", plan_digest=plan.digest()))


def test_generic_recovery_preview_checks_backup_bytes(interrupted):
    _, repo, harness, _, failed = interrupted
    plan = harness.installer.preview(repo, "recovery")
    target = next(e.path for e in plan.entries if e.action == "restore_backup")
    (harness.data_dir / "backups" / failed.transaction_id / target).write_bytes(b"damaged backup")
    blocked = harness.installer.preview(repo, "recovery")
    assert blocked.blocking
    assert any(e.reason == "recovery_backup_invalid" for e in blocked.blockers)


def test_reserved_generic_recovery_rejects_the_wrong_confirmation(studio, interrupted):
    root, repo, harness, _, _ = interrupted
    plan = harness.installer.preview(repo, "recovery")
    pending = run(harness.installer.begin(repo, "recovery", plan_digest=plan.digest()))
    before = tree(root)
    with pytest.raises(studio.errors.StudioError):
        run(harness.installer.run(
            repo, "recovery", plan_digest="wrong", transaction_id=pending.transaction_id,
        ))
    assert tree(root) == before


def test_reserved_recovery_cannot_resume_the_destructive_operation(studio, interrupted):
    root, repo, harness, _, failed = interrupted
    plan = harness.installer.preview(repo, "recovery")
    pending = run(harness.installer.begin(repo, "recovery", plan_digest=plan.digest()))
    original = next(s.detail["confirm_digest"] for s in failed.steps if s.name == f"confirm_{failed.kind}")
    before = tree(root)
    with pytest.raises(studio.errors.StudioError):
        run(harness.installer.run(repo, failed.kind, plan_digest=original, transaction_id=pending.transaction_id))
    assert tree(root) == before
    assert harness.installer.transaction(failed.transaction_id).status == "staged"


def test_generic_recovery_records_late_evidence_failure_without_staying_staged(interrupted):
    root, repo, harness, _, failed = interrupted
    plan = harness.installer.preview(repo, "recovery")
    pending = run(harness.installer.begin(repo, "recovery", plan_digest=plan.digest()))
    path = harness.data_dir / "backups" / failed.transaction_id / f"{failed.kind}-journal.json"
    path.write_bytes(b"corrupt after confirmation")
    before = tree(root)
    result = run(harness.installer.run(
        repo, "recovery", plan_digest=plan.digest(), transaction_id=pending.transaction_id,
    ))
    assert result.status == "recovery_required" and tree(root) == before


def test_generic_recovery_lease_race_returns_a_retryable_outcome(interrupted):
    root, repo, harness, _, failed = interrupted
    plan = harness.installer.preview(repo, "recovery")
    pending = run(harness.installer.begin(repo, "recovery", plan_digest=plan.digest()))
    harness.store.lease_acquire("execution", repo.resolved_identity, {
        "repo_id": repo.repo_id, "action_id": "new-owner",
    })
    before = tree(root)
    result = run(harness.installer.run(
        repo, "recovery", plan_digest=plan.digest(), transaction_id=pending.transaction_id,
    ))
    assert result.status == "recovery_required" and result.error == "repo_busy"
    assert tree(root) == before


def test_process_death_during_generic_recovery_can_use_the_same_application_flow(
    studio, interrupted, clock,
):
    root, repo, harness, before, failed = interrupted
    plan = harness.installer.preview(repo, "recovery")
    pending = run(harness.installer.begin(repo, "recovery", plan_digest=plan.digest()))

    def child():
        harness.store.close()
        harness.store.open()
        harness.fault.on("after:acquire_admin_lease", lambda: os._exit(77))
        run(harness.installer.run(
            repo, "recovery", plan_digest=plan.digest(), transaction_id=pending.transaction_id,
        ))

    process = multiprocessing.get_context("fork").Process(target=child)
    process.start()
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join()
        pytest.fail("isolated recovery process did not exit")
    assert process.exitcode == 77
    harness.installer._boot_id = harness.installer._scheduler._boot_id = "recovery-restarted"
    assert run(harness.installer.settle_abandoned()) == [pending.transaction_id]
    assert harness.installer.transaction(failed.transaction_id).status == "recovery_required"
    clock.advance(studio.constants.LEASE_STALE_SECS + 1)
    assert run(harness.installer._scheduler.startup_sweep()) == []
    again = harness.installer.preview(repo, "recovery")
    result = run(harness.installer.run(repo, "recovery", plan_digest=again.digest()))
    assert result.status == "rolled_back" and result.transaction_id == failed.transaction_id
    assert tree(root) == before
