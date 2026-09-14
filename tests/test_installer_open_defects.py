"""Regression evidence for open-defects 2.3, 2.4 and 2.6.

All repositories, staging trees and SQLite databases belong to pytest's temporary
directory. The shipped payload is only read by the existing miniature fixtures.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from test_installer import (
    BOOT,
    IDENTITY,
    TS,
    I,
    do,
    h,
    insert_repo,
    make_harness,
    payload_v1,
    run,
    store,
    tree,
)


ODD_BYTES = b"\x00\xffpartial candidate\r\n"
FILE = ".kiro/tools/aidlc-utility.ts"


def abandoned(h, repo, *, txid="tx_dead", status="written", candidate="changed"):
    h.installer._insert_transaction(txid, repo, "install")
    steps = [
        {"name": "stage_payload", "started_at": TS, "finished_at": TS, "ok": True, "detail": {}},
        {"name": "verify_staging", "started_at": TS, "finished_at": TS, "ok": True,
         "detail": {"verified": h.installer.manifest.file_count}},
    ]
    h.store.update("install_transactions", txid, {"status": status, "steps_json": steps})
    staging = h.data_dir / "staging" / txid
    if candidate != "absent":
        shutil.copytree(h.installer.payload_root, staging)
        if candidate == "changed":
            (staging / FILE).write_bytes(ODD_BYTES)
    return staging, steps


def failure_log(h, txid):
    result = h.installer.transaction(txid)
    assert result.failed_dir, "each failed transaction must point to its own evidence"
    failed = Path(result.failed_dir)
    assert failed.resolve() == (h.data_dir / "failed" / txid).resolve()
    log = json.loads((failed / "transaction.json").read_bytes())
    assert log["transaction_id"] == txid
    return failed, log


@pytest.mark.parametrize(
    "status,outcome",
    [("staged", "failed"), ("leased", "failed"), ("backed_up", "recovery_required"),
     ("written", "recovery_required"), ("merged", "recovery_required"),
     ("validated", "recovery_required"), ("rolling_back", "recovery_required")],
)
@pytest.mark.parametrize("candidate", ["absent", "changed", "identical"])
def test_abandoned_has_own_evidence_and_no_orphan(
    studio, h, repo_builder, status, outcome, candidate,
):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    before = tree(root)
    staging, steps = abandoned(h, repo, status=status, candidate=candidate)
    backup = h.data_dir / "backups" / "tx_dead" / "original"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"pre-install bytes")

    # An unrelated old failure must never become this transaction's evidence.
    other = h.data_dir / "failed" / "tx_other"
    other.mkdir(parents=True)
    (other / "transaction.json").write_text('{"transaction_id":"tx_other"}\n')
    other_before = tree(other)

    assert run(h.installer.settle_abandoned()) == ["tx_dead"]
    result = h.installer.transaction("tx_dead")
    assert result.status == outcome and result.error == "process_exited"
    assert result.finished_at
    assert result.repo_install_status == (
        "recovery_required" if outcome == "recovery_required" else "not_installed"
    )
    failed, log = failure_log(h, "tx_dead")
    assert log["error"] == "process_exited" and log["steps"] == steps
    assert not staging.exists(), "settling must remove this transaction's orphan"
    if candidate == "changed":
        assert (failed / "staging" / FILE).read_bytes() == ODD_BYTES
        assert log["candidate"]["status"] == "retained"
    else:
        assert not (failed / "staging").exists()
        assert log["candidate"]["status"] == (
            "absent" if candidate == "absent" else "identical_to_payload"
        )
    assert tree(root) == before and tree(other) == other_before
    assert backup.read_bytes() == b"pre-install bytes"
    evidence_before = tree(failed)
    assert run(h.installer.settle_abandoned()) == []
    assert tree(failed) == evidence_before


@pytest.mark.parametrize("marker", ["after:stage_payload", "before:acquire_admin_lease",
                                    "before:write_files"])
def test_identical_failures_keep_only_a_log(studio, h, repo_builder, marker):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    h.fault.at(marker)
    for _ in range(3):
        result = do(h, repo, "install")
        assert result.status in {"failed", "rolled_back"}
        failed, log = failure_log(h, result.transaction_id)
        assert not (failed / "staging").exists(), "identical payload copies add no evidence"
        assert log["candidate"]["status"] == "identical_to_payload"
        assert log["candidate"]["payload_digest"] == h.installer.manifest.payload_digest
        assert not (h.data_dir / "staging" / result.transaction_id).exists()
    assert len(list((h.data_dir / "failed").glob("*/transaction.json"))) == 3
    assert len(tree(h.data_dir / "failed")) == 3


@pytest.mark.parametrize("damage", ["changed", "same_size", "missing", "extra", "empty_directory",
                                   "symlink", "directory_symlink", "fifo"])
def test_informative_candidates_survive_even_after_successful_verification(
    studio, h, repo_builder, tmp_path, damage,
):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_bytes(ODD_BYTES)
    original = (h.installer.payload_root / FILE).read_bytes()
    same_size = bytes([original[0] ^ 1]) + original[1:]

    def change_staging():
        staging = next((h.data_dir / "staging").iterdir())
        if damage == "changed":
            (staging / FILE).write_bytes(ODD_BYTES)
        elif damage == "same_size":
            (staging / FILE).write_bytes(same_size)
        elif damage == "missing":
            (staging / FILE).unlink()
        elif damage == "extra":
            (staging / "unlisted.bin").write_bytes(ODD_BYTES)
        elif damage == "empty_directory":
            (staging / "unlisted-empty").mkdir()
        elif damage == "symlink":
            (staging / FILE).unlink()
            (staging / FILE).symlink_to(outside / "sentinel")
        elif damage == "directory_symlink":
            (staging / "unlisted-link").symlink_to(outside, target_is_directory=True)
        else:
            os.mkfifo(staging / "unlisted-pipe")

    h.fault.on("before:acquire_admin_lease", change_staging).at("before:acquire_admin_lease")
    result = do(h, repo, "install")
    failed, log = failure_log(h, result.transaction_id)
    candidate = failed / "staging"
    assert candidate.is_dir()
    if damage == "changed":
        assert (candidate / FILE).read_bytes() == ODD_BYTES
    elif damage == "same_size":
        assert (candidate / FILE).read_bytes() == same_size
    elif damage == "missing":
        assert not (candidate / FILE).exists()
    elif damage == "extra":
        assert (candidate / "unlisted.bin").read_bytes() == ODD_BYTES
    elif damage == "empty_directory":
        assert (candidate / "unlisted-empty").is_dir()
    elif damage == "symlink":
        assert (candidate / FILE).is_symlink()
    elif damage == "directory_symlink":
        assert (candidate / "unlisted-link").is_symlink()
    else:
        assert (candidate / "unlisted-pipe").is_fifo()
    assert (outside / "sentinel").read_bytes() == ODD_BYTES
    assert not (h.data_dir / "staging" / result.transaction_id).exists()


def test_verify_staging_failure_preserves_exact_abnormal_bytes(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    before = tree(root)

    def corrupt():
        staging = next((h.data_dir / "staging").iterdir())
        (staging / FILE).write_bytes(ODD_BYTES)

    h.fault.on("before:verify_staging", corrupt)
    result = do(h, repo, "install")
    assert result.status == "failed" and result.error == "payload_degraded"
    assert result.steps[-1].name == "verify_staging" and result.steps[-1].ok is not True
    failed, log = failure_log(h, result.transaction_id)
    assert (failed / "staging" / FILE).read_bytes() == ODD_BYTES
    assert tree(root) == before and h.store.lease_get("admin", IDENTITY) is None


def test_verify_staging_failure_is_retained_even_if_bytes_now_match(studio, h, repo_builder):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    h.fault.at("after:verify_staging")
    result = do(h, repo, "install")
    failed, log = failure_log(h, result.transaction_id)
    assert tree(failed / "staging") == tree(h.installer.payload_root)


def test_abandoned_other_payload_is_not_deduplicated(studio, h, repo_builder):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    abandoned(h, repo, candidate="identical")
    h.store.update("install_transactions", "tx_dead", {"payload_digest": "different-old-payload"})
    run(h.installer.settle_abandoned())
    failed, log = failure_log(h, "tx_dead")
    assert tree(failed / "staging") == tree(h.installer.payload_root)
    assert log["candidate"]["status"] == "retained"


@pytest.mark.parametrize("candidate", ["changed", "identical", "absent"])
def test_settlement_reentry_after_archive_before_database_update(
    studio, h, repo_builder, monkeypatch, candidate,
):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    staging, _ = abandoned(h, repo, candidate=candidate)
    update = h.store.update

    def interrupt(table, key, values, *args, **kwargs):
        if table == "install_transactions" and values.get("status") == "recovery_required":
            raise OSError("simulated process loss after archive")
        return update(table, key, values, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(h.store, "update", interrupt)
        with pytest.raises(OSError, match="simulated process loss"):
            run(h.installer.settle_abandoned())
    failed = h.data_dir / "failed" / "tx_dead"
    assert (failed / "transaction.json").is_file()
    evidence = tree(failed)
    assert not staging.exists()
    assert h.store.get("install_transactions", "tx_dead")["status"] == "written"
    assert run(h.installer.settle_abandoned()) == ["tx_dead"]
    assert tree(failed) == evidence, "reentry must retain the previous evidence and its explanation"
    failure_log(h, "tx_dead")


def test_reentry_after_move_preserves_already_archived_candidate(studio, h, repo_builder):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    staging, _ = abandoned(h, repo)
    failed = h.data_dir / "failed" / "tx_dead"
    failed.mkdir(parents=True)
    staging.rename(failed / "staging")  # process died before transaction.json was written
    run(h.installer.settle_abandoned())
    failed, log = failure_log(h, "tx_dead")
    assert (failed / "staging" / FILE).read_bytes() == ODD_BYTES
    assert log["candidate"]["status"] == "retained"


def test_reentry_never_overwrites_existing_evidence(studio, h, repo_builder):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    staging, _ = abandoned(h, repo)
    failed = h.data_dir / "failed" / "tx_dead"
    prior = failed / "staging"
    prior.mkdir(parents=True)
    (prior / "first.bin").write_bytes(b"first crash")
    (failed / "transaction.json").write_text(json.dumps({
        "transaction_id": "tx_dead", "backups": {"original": "saved-digest"},
        "created": ["new-file"], "retired": ["old-file"],
    }))
    run(h.installer.settle_abandoned())
    failed, log = failure_log(h, "tx_dead")
    assert (prior / "first.bin").read_bytes() == b"first crash"
    assert any(p.read_bytes() == ODD_BYTES for p in failed.rglob("aidlc-utility.ts"))
    assert not staging.exists()
    assert log["backups"] == {"original": "saved-digest"}
    assert log["created"] == ["new-file"] and log["retired"] == ["old-file"]


def test_abandoned_ignores_paths_recorded_in_database(studio, h, repo_builder, tmp_path):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    staging, _ = abandoned(h, repo)
    outside = tmp_path / "other-transaction"
    outside.mkdir()
    (outside / "sentinel").write_bytes(ODD_BYTES)
    outside_before = tree(outside)
    h.store.update("install_transactions", "tx_dead", {
        "staging_dir": str(outside), "backup_dir": str(outside), "failed_dir": str(outside),
    })
    run(h.installer.settle_abandoned())
    failed, _ = failure_log(h, "tx_dead")
    assert (failed / "staging" / FILE).read_bytes() == ODD_BYTES
    assert not staging.exists() and tree(outside) == outside_before
    assert (outside / "sentinel").read_bytes() == ODD_BYTES


@pytest.mark.parametrize("txid", ["../escaped", "../../escaped", "/absolute-escape", ".", "..",
                                 "nested/escape", "nested\\escape"])
def test_unsafe_transaction_ids_cannot_be_settled(studio, h, repo_builder, txid):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    abandoned(h, repo, txid=txid, candidate="absent")
    with pytest.raises(studio.errors.StudioError) as exc:
        run(h.installer.settle_abandoned())
    assert exc.value.code == "bad_path"
    assert h.store.get("install_transactions", txid)["status"] == "written"


@pytest.mark.parametrize("link_at", ["staging", "staging/tx_dead", "failed", "failed/tx_dead",
                                    "failed/tx_dead/staging", "failed/tx_dead/transaction.json"])
def test_archive_refuses_symlink_boundaries_without_touching_target(
    studio, h, repo_builder, tmp_path, link_at,
):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    abandoned(h, repo, candidate="absent")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(ODD_BYTES)
    link = h.data_dir / link_at
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(sentinel if link.name == "transaction.json" else outside)
    with pytest.raises(studio.errors.StudioError) as exc:
        run(h.installer.settle_abandoned())
    assert exc.value.code == "bad_path"
    assert sentinel.read_bytes() == ODD_BYTES
    assert list(outside.iterdir()) == [sentinel] and link.is_symlink()
    assert h.store.get("install_transactions", "tx_dead")["status"] == "written"


def test_live_transaction_keeps_its_staging(studio, h, repo_builder):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    staging, _ = abandoned(h, repo)
    h.store.lease_acquire("admin", IDENTITY, {
        "repo_id": repo.repo_id, "operation_type": "install",
        "transaction_id": "tx_dead", "boot_id": BOOT,
    })
    assert run(h.installer.settle_abandoned()) == []
    assert (staging / FILE).read_bytes() == ODD_BYTES
    assert not (h.data_dir / "failed" / "tx_dead").exists()


@pytest.mark.parametrize("candidate", ["identical", "changed"])
@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_archive_write_failure_is_retryable_without_losing_candidate(
    studio, h, I, repo_builder, monkeypatch, candidate, operation,
):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    staging, _ = abandoned(h, repo, candidate=candidate)
    expected = tree(staging)

    def fail(*args, **kwargs):
        raise OSError("archive write interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(I.os, operation, fail)
        with pytest.raises(OSError, match="archive write interrupted"):
            run(h.installer.settle_abandoned())
    assert h.store.get("install_transactions", "tx_dead")["status"] == "written"
    failed = h.data_dir / "failed" / "tx_dead"
    if candidate == "identical":
        assert tree(staging) == expected, "deletion must follow the durable explanation"
    else:
        assert tree(staging) == expected or tree(failed / "staging") == expected
    assert run(h.installer.settle_abandoned()) == ["tx_dead"]
    failed, _ = failure_log(h, "tx_dead")
    assert not staging.exists()
    if candidate == "changed":
        assert tree(failed / "staging") == expected
    assert not list(failed.glob("*.tmp"))


def test_candidate_read_failure_prevents_deduplication(
    studio, h, repo_builder, monkeypatch,
):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    staging, _ = abandoned(h, repo, candidate="identical")
    original_open = Path.open

    def unreadable(path, *args, **kwargs):
        if path == staging / FILE:
            raise PermissionError("candidate unreadable")
        return original_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", unreadable)
        run(h.installer.settle_abandoned())
    failed, log = failure_log(h, "tx_dead")
    assert tree(failed / "staging") == tree(h.installer.payload_root)
    assert log["candidate"]["status"] == "retained"


def test_archive_does_not_follow_predictable_temporary_symlink(
    studio, h, repo_builder, tmp_path,
):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    abandoned(h, repo)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(ODD_BYTES)
    failed = h.data_dir / "failed" / "tx_dead"
    failed.mkdir(parents=True)
    trap = failed / ".transaction.json.aidlc-studio.tmp"
    trap.symlink_to(outside)
    run(h.installer.settle_abandoned())
    failure_log(h, "tx_dead")
    assert outside.read_bytes() == ODD_BYTES and trap.is_symlink()


def test_restart_after_move_does_not_reuse_an_old_identical_label(studio, h, repo_builder):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    staging, _ = abandoned(h, repo)
    failed = h.data_dir / "failed" / "tx_dead"
    failed.mkdir(parents=True)
    (failed / "transaction.json").write_text(json.dumps({
        "transaction_id": "tx_dead", "candidate": {"status": "identical_to_payload"},
    }))
    staging.rename(failed / "staging")
    run(h.installer.settle_abandoned())
    failed, log = failure_log(h, "tx_dead")
    assert (failed / "staging" / FILE).read_bytes() == ODD_BYTES
    assert log["candidate"]["status"] == "retained"


def test_incomplete_comparison_keeps_candidate(studio, h, I, repo_builder, monkeypatch):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    abandoned(h, repo, candidate="identical")
    monkeypatch.setattr(I, "_MAX_PAYLOAD_WALK", 1)
    run(h.installer.settle_abandoned())
    failed, log = failure_log(h, "tx_dead")
    assert tree(failed / "staging") == tree(h.installer.payload_root)
    assert log["candidate"]["status"] == "retained"


@pytest.mark.parametrize("prior_log", ['{"transaction_id":"tx_other"}', "{broken"])
def test_unrelated_or_corrupt_log_cannot_be_overwritten(studio, h, repo_builder, prior_log):
    repo = insert_repo(studio, h.store, repo_builder.with_git().build())
    staging, _ = abandoned(h, repo)
    failed = h.data_dir / "failed" / "tx_dead"
    failed.mkdir(parents=True)
    log_path = failed / "transaction.json"
    log_path.write_text(prior_log)
    with pytest.raises(studio.errors.StudioError) as exc:
        run(h.installer.settle_abandoned())
    assert exc.value.code == "bad_path"
    assert (staging / FILE).read_bytes() == ODD_BYTES
    assert log_path.read_text() == prior_log
    assert h.store.get("install_transactions", "tx_dead")["status"] == "written"
