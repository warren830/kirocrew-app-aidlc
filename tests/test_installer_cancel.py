"""Cancellation is a durable request followed by a proved transactional rollback."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from test_installer import (
    I, do, h, insert_repo, make_harness, payload_v1, payload_v2, reload_repo, run, store, tree,
)
from test_installer_uninstall import OWNED, installed


async def cancel_during(harness, repo, kind, method, monkeypatch, *, edit=None):
    reached, resume = threading.Event(), threading.Event()
    original = getattr(harness.installer, method)

    def paused(*args):
        value = original(*args)
        reached.set()
        assert resume.wait(15), "cancellation test worker timed out"
        return value

    monkeypatch.setattr(harness.installer, method, paused)
    plan = harness.installer.preview(repo, kind)
    pending = await harness.installer.begin(repo, kind, plan_digest=plan.digest())
    worker = asyncio.create_task(harness.installer.run(
        repo, kind, plan_digest=plan.digest(), transaction_id=pending.transaction_id,
    ))
    try:
        assert await asyncio.to_thread(reached.wait, 15)
        ack = await harness.installer.cancel(repo, transaction_id=pending.transaction_id)
        assert not worker.done() and not worker.cancelled()
        assert ack.error == "cancel_requested"
        assert harness.store.get("install_transactions", pending.transaction_id)["error"] == "cancel_requested"
        if edit:
            edit()
    finally:
        resume.set()
    return await asyncio.wait_for(worker, 15)


@pytest.mark.parametrize("method", [
    "_stage_payload", "_backup", "_write_files", "_merge_fragments", "_post_write_validate",
    "_commit_receipt",
])
def test_running_upgrade_cancel_restores_bytes_receipt_and_releases_lease(
    studio, h, installed, make_harness, payload_v2, monkeypatch, method,
):
    root, repo = installed
    before = tree(root)
    receipt = h.installer.current_receipt(repo.repo_id)
    upgrade = make_harness(payload_v2)
    result = run(cancel_during(upgrade, repo, "upgrade", method, monkeypatch))
    assert result.error == "cancelled"
    assert result.status == ("failed" if method == "_stage_payload" else "rolled_back")
    assert tree(root) == before
    assert upgrade.installer.current_receipt(repo.repo_id).receipt_id == receipt.receipt_id
    assert upgrade.store.lease_get("admin", repo.resolved_identity) is None
    again = run(upgrade.installer.cancel(repo, transaction_id=result.transaction_id))
    assert again == result


def test_cancel_before_worker_starts_writes_no_repository_bytes(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    st = root.stat()
    repo = insert_repo(studio, h.store, root, resolved_identity=f"{st.st_dev}:{st.st_ino}")
    before = tree(root)
    plan = h.installer.preview(repo, "install")
    pending = run(h.installer.begin(repo, "install", plan_digest=plan.digest()))
    ack = run(h.installer.cancel(repo, transaction_id=pending.transaction_id))
    assert ack.status == "staged" and ack.error == "cancel_requested"
    result = run(h.installer.run(
        repo, "install", plan_digest=plan.digest(), transaction_id=pending.transaction_id,
    ))
    assert result.status == "failed" and result.error == "cancelled"
    assert tree(root) == before and h.installer.current_receipt(repo.repo_id) is None
    assert run(h.installer.run(
        repo, "install", plan_digest=plan.digest(), transaction_id=pending.transaction_id,
    )) == result
    assert tree(root) == before


def test_cancel_checks_transaction_scope_and_does_not_cancel_committed_install(studio, h, installed):
    root, repo = installed
    committed = h.installer.transactions(repo.repo_id)[0]
    before = tree(root)
    assert run(h.installer.cancel(repo, transaction_id=committed.transaction_id)) == committed
    with pytest.raises(studio.errors.StudioError):
        run(h.installer.cancel(replace(repo, repo_id="other"), transaction_id=committed.transaction_id))
    assert tree(root) == before


def test_cancellation_cannot_overwrite_a_user_edit_after_installer_write(
    h, installed, make_harness, payload_v2, monkeypatch,
):
    root, repo = installed
    upgrade = make_harness(payload_v2)
    result = run(cancel_during(
        upgrade, repo, "upgrade", "_write_files", monkeypatch,
        edit=lambda: (root / OWNED).write_bytes(b"user edited during install"),
    ))
    assert result.status == "recovery_required" and result.error == "cancelled"
    assert (root / OWNED).read_bytes() == b"user edited during install"
    assert upgrade.store.get("repos", repo.repo_id)["install_status"] == "recovery_required"


def test_cancel_rollback_failure_keeps_evidence_and_requires_recovery(
    h, installed, make_harness, payload_v2, monkeypatch,
):
    _, repo = installed
    upgrade = make_harness(payload_v2)
    upgrade.fault.at("before:restore_backups")
    result = run(cancel_during(upgrade, repo, "upgrade", "_write_files", monkeypatch))
    assert result.status == "recovery_required" and result.error == "cancelled"
    assert (Path(result.failed_dir) / "transaction.json").is_file()
    assert Path(upgrade.store.get("install_transactions", result.transaction_id)["backup_dir"]).is_dir()
    assert upgrade.installer.current_receipt(repo.repo_id) is not None
    assert upgrade.store.lease_get("admin", repo.resolved_identity) is None


def test_cancel_restores_original_file_permissions(h, installed, make_harness, payload_v2, monkeypatch):
    root, repo = installed
    (root / OWNED).chmod(0o751)
    upgrade = make_harness(payload_v2)
    result = run(cancel_during(upgrade, repo, "upgrade", "_write_files", monkeypatch))
    assert result.status == "rolled_back"
    assert (root / OWNED).stat().st_mode & 0o777 == 0o751


def test_process_death_during_cancel_keeps_recovery_evidence(
    studio, h, installed, make_harness, payload_v2, clock,
):
    _, repo = installed
    upgrade = make_harness(payload_v2)
    plan = upgrade.installer.preview(repo, "upgrade")
    pending = run(upgrade.installer.begin(repo, "upgrade", plan_digest=plan.digest()))

    def child():
        upgrade.store.close()
        upgrade.store.open()

        def stop():
            upgrade.installer._request_cancel(repo, pending.transaction_id)
            os._exit(77)

        upgrade.fault.on("after:write_files", stop)
        run(upgrade.installer.run(
            repo, "upgrade", plan_digest=plan.digest(), transaction_id=pending.transaction_id,
        ))

    process = multiprocessing.get_context("fork").Process(target=child)
    process.start()
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join()
        pytest.fail("isolated cancellation process did not exit")
    assert process.exitcode == 77
    assert upgrade.store.get("install_transactions", pending.transaction_id)["error"] == "cancel_requested"
    upgrade.installer._boot_id = upgrade.installer._scheduler._boot_id = "restarted-cancel"
    assert run(upgrade.installer.settle_abandoned()) == [pending.transaction_id]
    outcome = upgrade.installer.transaction(pending.transaction_id)
    assert outcome.status == "recovery_required"
    assert (Path(outcome.failed_dir) / "transaction.json").is_file()
    assert upgrade.store.get("repos", repo.repo_id)["install_status"] == "recovery_required"
    clock.advance(studio.constants.LEASE_STALE_SECS + 1)
    assert run(upgrade.installer._scheduler.startup_sweep()) == []


def test_cancel_cannot_restore_under_a_replaced_lease(h, installed, make_harness, payload_v2, monkeypatch):
    root, repo = installed
    upgrade = make_harness(payload_v2)

    def replace_lease():
        held = upgrade.store.lease_get("admin", repo.resolved_identity)
        upgrade.store.lease_release("admin", repo.resolved_identity, held.generation)
        upgrade.store.lease_acquire("execution", repo.resolved_identity, {
            "repo_id": repo.repo_id, "action_id": "new-owner",
        })

    result = run(cancel_during(
        upgrade, repo, "upgrade", "_write_files", monkeypatch, edit=replace_lease,
    ))
    assert result.status == "recovery_required" and result.error == "cancelled"
    assert not (root / OWNED).exists()  # restoration would write under the new execution owner
    assert upgrade.store.lease_get("execution", repo.resolved_identity).action_id == "new-owner"
