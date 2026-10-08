"""Regression evidence for open defects 2.3, 2.4 and 2.6: failed-transaction evidence and abandoned staging."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from _helpers_c import die_in_child
from conftest import RepoBuilder
from test_installer import TS, I, do, fresh, h, insert_repo, make_harness, payload_v1, run, store, tree

ODD_BYTES = b"\x00\xffpartial candidate\r\n"
FILE = ".kiro/tools/aidlc-utility.ts"


@pytest.fixture
def repo(fresh):
    return fresh[1]


@pytest.fixture
def outside(tmp_path):
    """A directory outside the app data holding a ``sentinel`` that nothing may touch."""
    path = tmp_path / "outside"
    path.mkdir()
    (path / "sentinel").write_bytes(ODD_BYTES)
    return path


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


def deny_candidate_unlink(monkeypatch_context) -> None:
    """Make the best-effort cleanup of a committed install's staging fail on one file."""
    unlink = os.unlink

    def deny(path, *args, **kwargs):
        if path == "aidlc-utility.ts" and kwargs.get("dir_fd") is not None:
            raise PermissionError("temporary staging cleanup failure")
        return unlink(path, *args, **kwargs)

    monkeypatch_context.setattr(os, "unlink", deny)


@pytest.mark.parametrize("status,outcome", [
    ("staged", "failed"), ("leased", "failed"), ("backed_up", "recovery_required"), ("written", "recovery_required"),
    ("merged", "recovery_required"), ("validated", "recovery_required"), ("rolling_back", "recovery_required"),
])
@pytest.mark.parametrize("candidate", ["absent", "changed", "identical"])
def test_abandoned_has_own_evidence_and_no_orphan(h, fresh, status, outcome, candidate):
    root, repo = fresh
    before = tree(root)
    staging, steps = abandoned(h, repo, status=status, candidate=candidate)
    backup = h.data_dir / "backups" / "tx_dead" / "original"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"pre-install bytes")
    other = h.data_dir / "failed" / "tx_other"  # an unrelated old failure must never become this one's evidence
    other.mkdir(parents=True)
    (other / "transaction.json").write_text('{"transaction_id":"tx_other"}\n')
    other_before = tree(other)

    assert run(h.installer.settle_abandoned()) == ["tx_dead"]
    result = h.installer.transaction("tx_dead")
    assert result.status == outcome and result.error == "process_exited"
    assert result.finished_at
    assert result.repo_install_status == ("recovery_required" if outcome == "recovery_required" else "not_installed")
    failed, log = failure_log(h, "tx_dead")
    assert log["error"] == "process_exited" and log["steps"] == steps
    assert not staging.exists(), "settling must remove this transaction's orphan"
    if candidate == "changed":
        assert (failed / "staging" / FILE).read_bytes() == ODD_BYTES
        assert log["candidate"]["status"] == "retained"
    else:
        assert not (failed / "staging").exists()
        assert log["candidate"]["status"] == ("absent" if candidate == "absent" else "identical_to_payload")
    assert tree(root) == before and tree(other) == other_before
    assert backup.read_bytes() == b"pre-install bytes"
    evidence_before = tree(failed)
    assert run(h.installer.settle_abandoned()) == []
    assert tree(failed) == evidence_before


@pytest.mark.parametrize("marker", ["after:stage_payload", "before:acquire_admin_lease", "before:write_files"])
def test_identical_failures_keep_only_a_log(h, repo, marker):
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


def _replace_with_link(staging: Path, outside: Path) -> None:
    (staging / FILE).unlink()
    (staging / FILE).symlink_to(outside / "sentinel")


#: damage -> (change the verified staging tree, what the retained candidate must still show).
CANDIDATE_DAMAGE = {
    "changed": (lambda s, o, same: (s / FILE).write_bytes(ODD_BYTES),
                lambda c, same: (c / FILE).read_bytes() == ODD_BYTES),
    "same_size": (lambda s, o, same: (s / FILE).write_bytes(same), lambda c, same: (c / FILE).read_bytes() == same),
    "missing": (lambda s, o, same: (s / FILE).unlink(), lambda c, same: not (c / FILE).exists()),
    "extra": (lambda s, o, same: (s / "unlisted.bin").write_bytes(ODD_BYTES),
              lambda c, same: (c / "unlisted.bin").read_bytes() == ODD_BYTES),
    "empty_directory": (lambda s, o, same: (s / "unlisted-empty").mkdir(),
                        lambda c, same: (c / "unlisted-empty").is_dir()),
    "symlink": (lambda s, o, same: _replace_with_link(s, o), lambda c, same: (c / FILE).is_symlink()),
    "directory_symlink": (lambda s, o, same: (s / "unlisted-link").symlink_to(o, target_is_directory=True),
                          lambda c, same: (c / "unlisted-link").is_symlink()),
    "fifo": (lambda s, o, same: os.mkfifo(s / "unlisted-pipe"), lambda c, same: (c / "unlisted-pipe").is_fifo()),
}


@pytest.mark.parametrize("damage", list(CANDIDATE_DAMAGE))
def test_informative_candidates_survive_even_after_successful_verification(h, repo, outside, damage):
    change, check = CANDIDATE_DAMAGE[damage]
    original = (h.installer.payload_root / FILE).read_bytes()
    same_size = bytes([original[0] ^ 1]) + original[1:]
    h.fault.on("before:acquire_admin_lease",
               lambda: change(next((h.data_dir / "staging").iterdir()), outside, same_size))
    h.fault.at("before:acquire_admin_lease")
    result = do(h, repo, "install")
    failed, log = failure_log(h, result.transaction_id)
    candidate = failed / "staging"
    assert candidate.is_dir()
    assert check(candidate, same_size), damage
    assert (outside / "sentinel").read_bytes() == ODD_BYTES
    assert not (h.data_dir / "staging" / result.transaction_id).exists()


def test_verify_staging_failure_preserves_exact_abnormal_bytes(h, fresh):
    root, repo = fresh
    before = tree(root)
    h.fault.on("before:verify_staging",
               lambda: (next((h.data_dir / "staging").iterdir()) / FILE).write_bytes(ODD_BYTES))
    result = do(h, repo, "install")
    assert result.status == "failed" and result.error == "payload_degraded"
    assert result.steps[-1].name == "verify_staging" and result.steps[-1].ok is not True
    failed, log = failure_log(h, result.transaction_id)
    assert (failed / "staging" / FILE).read_bytes() == ODD_BYTES
    assert tree(root) == before and h.lease() is None


def test_verify_staging_failure_is_retained_even_if_bytes_now_match(h, repo):
    h.fault.at("after:verify_staging")
    failed, _log = failure_log(h, do(h, repo, "install").transaction_id)
    assert tree(failed / "staging") == tree(h.installer.payload_root)


@pytest.mark.parametrize("cause", ["other_payload", "unreadable", "incomplete_walk"])
def test_an_unproven_identical_candidate_is_retained(h, I, repo, monkeypatch, cause):
    """Deduplication needs proof: another payload's digest, an unreadable file or a capped walk keep the copy."""
    staging, _ = abandoned(h, repo, candidate="identical")
    if cause == "other_payload":
        h.store.update("install_transactions", "tx_dead", {"payload_digest": "different-old-payload"})
    elif cause == "incomplete_walk":
        monkeypatch.setattr(I, "_MAX_PAYLOAD_WALK", 1)
    with monkeypatch.context() as patch:
        if cause == "unreadable":
            original_open = Path.open

            def unreadable(path, *args, **kwargs):
                if path == staging / FILE:
                    raise PermissionError("candidate unreadable")
                return original_open(path, *args, **kwargs)

            patch.setattr(Path, "open", unreadable)
        run(h.installer.settle_abandoned())
    failed, log = failure_log(h, "tx_dead")
    assert tree(failed / "staging") == tree(h.installer.payload_root)
    assert log["candidate"]["status"] == "retained"


@pytest.mark.parametrize("candidate", ["changed", "identical", "absent"])
def test_settlement_reentry_after_archive_before_database_update(h, repo, monkeypatch, candidate):
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


@pytest.mark.parametrize("prior_log", [None, {"transaction_id": "tx_dead",
                                              "candidate": {"status": "identical_to_payload"}}])
def test_reentry_after_move_keeps_the_archived_candidate(h, repo, prior_log):
    """The process died after moving staging into the archive, before (or without) explaining it truthfully."""
    staging, _ = abandoned(h, repo)
    failed = h.data_dir / "failed" / "tx_dead"
    failed.mkdir(parents=True)
    if prior_log is not None:
        (failed / "transaction.json").write_text(json.dumps(prior_log))
    staging.rename(failed / "staging")
    run(h.installer.settle_abandoned())
    failed, log = failure_log(h, "tx_dead")
    assert (failed / "staging" / FILE).read_bytes() == ODD_BYTES
    assert log["candidate"]["status"] == "retained"


def test_reentry_never_overwrites_existing_evidence(h, repo):
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


def test_abandoned_ignores_paths_recorded_in_database(h, repo, outside):
    staging, _ = abandoned(h, repo)
    outside_before = tree(outside)
    h.store.update("install_transactions", "tx_dead", {
        "staging_dir": str(outside), "backup_dir": str(outside), "failed_dir": str(outside),
    })
    run(h.installer.settle_abandoned())
    failed, _ = failure_log(h, "tx_dead")
    assert (failed / "staging" / FILE).read_bytes() == ODD_BYTES
    assert not staging.exists() and tree(outside) == outside_before
    assert (outside / "sentinel").read_bytes() == ODD_BYTES


@pytest.mark.parametrize("txid", ["../escaped", "../../escaped", "/absolute-escape", ".", "..", "nested/escape",
                                  "nested\\escape"])
def test_unsafe_transaction_ids_cannot_be_settled(studio, h, repo, txid):
    abandoned(h, repo, txid=txid, candidate="absent")
    with pytest.raises(studio.errors.StudioError) as exc:
        run(h.installer.settle_abandoned())
    assert exc.value.code == "bad_path"
    assert h.store.get("install_transactions", txid)["status"] == "written"


@pytest.mark.parametrize("link_at", ["staging", "staging/tx_dead", "failed", "failed/tx_dead",
                                     "failed/tx_dead/staging", "failed/tx_dead/transaction.json"])
def test_archive_refuses_symlink_boundaries_without_touching_target(studio, h, repo, outside, link_at):
    abandoned(h, repo, candidate="absent")
    sentinel = outside / "sentinel"
    link = h.data_dir / link_at
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(sentinel if link.name == "transaction.json" else outside)
    with pytest.raises(studio.errors.StudioError) as exc:
        run(h.installer.settle_abandoned())
    assert exc.value.code == "bad_path"
    assert sentinel.read_bytes() == ODD_BYTES
    assert list(outside.iterdir()) == [sentinel] and link.is_symlink()
    assert h.store.get("install_transactions", "tx_dead")["status"] == "written"


@pytest.mark.parametrize("status", ["written", "committed"])
def test_live_transaction_keeps_its_staging(h, repo, status):
    staging, _ = abandoned(h, repo, status=status)
    grant = run(h.scheduler.acquire_admin(repo, operation_type="install", transaction_id="tx_dead"))
    assert run(h.installer.settle_abandoned()) == []
    assert (staging / FILE).read_bytes() == ODD_BYTES
    assert not (h.data_dir / "failed" / "tx_dead").exists()
    run(h.scheduler.release(grant))


@pytest.mark.parametrize("marker", ["before:release_lease", "after:release_lease", "before:finish"])
def test_restart_finishes_committed_install_without_orphaning_staging(h, fresh, marker):
    root, repo = fresh
    plan = h.installer.preview(repo, "install")
    txid = run(h.installer.begin(repo, "install", plan_digest=plan.digest())).transaction_id

    def child():
        h.store.close()
        h.store.open()
        if marker == "before:finish":
            async def exit_before_finish(*args):
                os._exit(77)
            h.installer._finish = exit_before_finish
        else:
            h.fault.on(marker, lambda: os._exit(77))
        run(h.installer.run(repo, "install", plan_digest=plan.digest(), transaction_id=txid))

    die_in_child(child)
    committed = h.installer.transaction(txid)
    assert committed.status == "committed" and not committed.finished_at
    staging = h.data_dir / "staging" / txid
    assert staging.is_dir() == (marker != "before:finish")
    repository = tree(root)
    backups = tree(h.data_dir / "backups")
    receipts = h.store.select("install_receipts")
    repo_row = h.store.get("repos", repo.repo_id)
    assert repo_row["install_status"] == "installed"

    assert run(h.installer.settle_abandoned()) == [txid]
    finished = h.installer.transaction(txid)
    assert finished.status == "committed" and finished.finished_at
    assert finished.error is None and finished.failed_dir is None
    assert not staging.exists()
    assert tree(root) == repository and tree(h.data_dir / "backups") == backups
    assert h.store.select("install_receipts") == receipts
    assert h.store.get("repos", repo.repo_id) == repo_row
    assert run(h.installer.settle_abandoned()) == []
    # Whatever the child held died with it.
    run(h.scheduler.release(run(h.scheduler.acquire_admin(repo, operation_type="recovery", transaction_id=None))))


def test_restart_retries_ignored_cleanup_errors_from_a_finished_commit(h, fresh, monkeypatch, clock):
    root, repo = fresh
    with monkeypatch.context() as patch:
        deny_candidate_unlink(patch)
        result = do(h, repo, "install")
    assert result.status == "committed" and result.finished_at
    staging = h.data_dir / "staging" / result.transaction_id
    assert (staging / FILE).is_file()
    repository = tree(root)
    backups = tree(h.data_dir / "backups")
    row = h.store.get("install_transactions", result.transaction_id)
    clock.advance(1)
    assert run(h.installer.settle_abandoned()) == [result.transaction_id]
    assert not staging.exists()
    assert h.store.get("install_transactions", result.transaction_id) == row
    assert tree(root) == repository and tree(h.data_dir / "backups") == backups
    assert run(h.installer.settle_abandoned()) == []


def test_committed_cleanup_error_does_not_strand_another_repos_interrupted_install(studio, h, fresh, tmp_path,
                                                                                    monkeypatch, clock):
    root, repo = fresh
    with monkeypatch.context() as patch:  # a successful older install whose best-effort cleanup left staging
        deny_candidate_unlink(patch)
        committed = do(h, repo, "install")
    assert committed.status == "committed" and committed.finished_at
    staging = h.data_dir / "staging" / committed.transaction_id
    assert (staging / FILE).is_file()
    committed_row = h.store.get("install_transactions", committed.transaction_id)
    committed_repo = h.store.get("repos", repo.repo_id)

    clock.advance(1)
    other_root = RepoBuilder(tmp_path / "other-repo").with_git().build()
    other = insert_repo(studio, h.store, other_root, repo_id="r_2", resolved_identity="1:101")
    plan = h.installer.preview(other, "install")
    pending = run(h.installer.begin(other, "install", plan_digest=plan.digest()))

    def child():
        h.store.close()
        h.store.open()
        h.fault.on("before:merge_fragments", lambda: os._exit(77))
        run(h.installer.run(other, "install", plan_digest=plan.digest(), transaction_id=pending.transaction_id))

    die_in_child(child)
    interrupted = h.installer.transaction(pending.transaction_id)
    assert interrupted.status == "written" and not interrupted.finished_at
    assert h.lease() is None, "the child's lock died with it"
    assert (other_root / FILE).is_file()
    repositories = (tree(root), tree(other_root))
    backups = tree(h.data_dir / "backups")
    receipts = h.store.select("install_receipts")
    rmtree = shutil.rmtree

    def deny_committed_cleanup(path, *args, **kwargs):
        if Path(path) == staging:
            raise PermissionError("committed staging is still locked")
        return rmtree(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "rmtree", deny_committed_cleanup)
        with pytest.raises(studio.errors.StudioError) as exc:
            run(h.installer.settle_abandoned())
    assert exc.value.code == "storage_error"
    recovered = h.installer.transaction(pending.transaction_id)
    assert recovered.status == "recovery_required" and recovered.finished_at
    assert recovered.repo_install_status == "recovery_required"
    assert recovered.error == "process_exited" and recovered.failed_dir
    assert not (h.data_dir / "staging" / pending.transaction_id).exists()
    assert exc.value.details["transaction_id"] == committed.transaction_id
    assert exc.value.details["settled_transaction_ids"] == [pending.transaction_id]
    assert exc.value.details["committed_cleanup_failures"] == [
        {"transaction_id": committed.transaction_id, "code": "storage_error"}]
    run(h.scheduler.release(run(h.scheduler.acquire_admin(other, operation_type="recovery", transaction_id=None))))
    assert staging.exists()
    assert h.store.get("install_transactions", committed.transaction_id) == committed_row
    assert h.store.get("repos", repo.repo_id) == committed_repo
    assert (tree(root), tree(other_root)) == repositories
    assert tree(h.data_dir / "backups") == backups
    assert h.store.select("install_receipts") == receipts
    assert run(h.installer.settle_abandoned()) == [committed.transaction_id]
    assert not staging.exists()
    assert run(h.installer.settle_abandoned()) == []


@pytest.mark.parametrize("operation", ["cleanup", "bookkeeping"])
def test_committed_settlement_retries_interrupted_cleanup(studio, h, repo, monkeypatch, operation):
    staging, _ = abandoned(h, repo, status="committed", candidate="identical")
    update = h.store.update

    def fail_cleanup(*args, **kwargs):
        raise OSError("cleanup interrupted")

    def fail_bookkeeping(table, key, values, *args, **kwargs):
        if table == "install_transactions" and values.get("finished_at"):
            raise OSError("bookkeeping interrupted")
        return update(table, key, values, *args, **kwargs)

    with monkeypatch.context() as patch:
        if operation == "cleanup":
            patch.setattr(shutil, "rmtree", fail_cleanup)
        else:
            patch.setattr(h.store, "update", fail_bookkeeping)
        with pytest.raises(studio.errors.StudioError if operation == "cleanup" else OSError) as exc:
            run(h.installer.settle_abandoned())
        if operation == "cleanup":
            assert exc.value.code == "storage_error"
        else:
            assert str(exc.value) == "bookkeeping interrupted"
    result = h.installer.transaction("tx_dead")
    assert result.status == "committed" and not result.finished_at
    assert staging.exists() == (operation == "cleanup")
    assert run(h.installer.settle_abandoned()) == ["tx_dead"]
    assert not staging.exists()
    assert h.installer.transaction("tx_dead").finished_at


@pytest.mark.parametrize("link_at", ["staging", "staging/tx_dead"])
@pytest.mark.parametrize("finished", [False, True])
def test_committed_settlement_refuses_staging_symlinks(studio, h, repo, outside, link_at, finished):
    abandoned(h, repo, status="committed", candidate="absent")
    if finished:
        h.store.update("install_transactions", "tx_dead", {"finished_at": TS})
    link = h.data_dir / link_at
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(studio.errors.StudioError) as exc:
        run(h.installer.settle_abandoned())
    assert exc.value.code == "bad_path"
    assert (outside / "sentinel").read_bytes() == ODD_BYTES and link.is_symlink()
    assert bool(h.installer.transaction("tx_dead").finished_at) == finished


@pytest.mark.parametrize("candidate", ["identical", "changed"])
@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_archive_write_failure_is_retryable_without_losing_candidate(h, I, repo, monkeypatch, candidate, operation):
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


def test_archive_does_not_follow_predictable_temporary_symlink(h, repo, tmp_path):
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


@pytest.mark.parametrize("prior_log", ['{"transaction_id":"tx_other"}', "{broken"])
def test_unrelated_or_corrupt_log_cannot_be_overwritten(studio, h, repo, prior_log):
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
