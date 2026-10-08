"""Only temporary app data is removed; transaction/receipt metadata and repository bytes survive."""

import asyncio
import base64
import json
import os
import shutil
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

import conftest as CT
from _helpers_c import after
from test_handlers_auth import call, common, routes, sv  # noqa: F401


@pytest.fixture
def maintenance():
    return CT.studio_module("maintenance")


@pytest.fixture
def repo(sv, repo_builder):
    root = repo_builder.with_git().build()
    (root / "keep.txt").write_text("repository bytes")
    return sv.repos.add(str(root), "cleanup proof")


@pytest.fixture
def outside(tmp_path):
    """A directory no cleanup may ever reach."""
    path = tmp_path / "outside"
    path.mkdir()
    return path


@pytest.fixture
def api(sv, routes, fake_host, repo):
    """The two maintenance routes for ``repo``, called as the owner."""
    def request(suffix, body):
        path = f"/repos/{repo.repo_id}/maintenance/{suffix}"
        return call(sv, routes, "POST", path, CT.owner_request("POST", path, host=fake_host, body=body))

    def preview(ids):
        status, answer = request("preview", {"entry_ids": ids})
        assert status == 200, answer
        return answer

    def apply(ids, answer):
        return request("cleanup", {"entry_ids": ids, "plan_digest": answer["plan_digest"]})

    return SimpleNamespace(request=request, preview=preview, apply=apply)


def transaction(sv, repo, n, *, status="failed", category="failed", prior=None, finished=True):
    txid = f"tx_{n:016x}"
    root = Path(sv.ctx.data_dir)
    sv.storage.insert("install_transactions", {
        "transaction_id": txid, "repo_id": repo.repo_id, "resolved_repo_identity": repo.resolved_identity,
        "kind": "upgrade", "status": status, "studio_version": "1.0.0", "engine_version": "2.7.1",
        "payload_digest": "a" * 64, "started_at": "2026-09-10T00:00:00Z",
        "finished_at": "2026-09-10T00:01:00Z" if finished else None, "backup_dir": str(root / "backups" / txid),
        "failed_dir": str(root / "failed" / txid), "prior_receipt_id": prior,
        "steps_json": [{"name": "write_files", "ok": False}], "error": "original failure",
    })
    directory = root / category / txid
    directory.mkdir(parents=True)
    (directory / "payload.bin").write_bytes(b"reviewed bytes")
    (directory / "transaction.json").write_text(json.dumps({
        "transaction_id": txid, "backups": {"old": "hash"}, "created": ["new"], "error": "proof"}))
    return txid, directory, f"{txid}:{category}"


def receipt(sv, repo, n, txid, *, status="current", prior=None):
    rid = f"rc_{n:016x}"
    sv.storage.insert("install_receipts", {
        "receipt_id": rid, "repo_id": repo.repo_id, "resolved_repo_identity": repo.resolved_identity,
        "studio_version": "1.0.0", "engine_version": "2.7.1", "payload_digest": "b" * 64,
        "committed_at": "2026-09-10T00:02:00Z", "prior_receipt_id": prior,
        "transaction_id": txid, "files_json": [], "status": status,
    })
    if status == "current":
        sv.storage.update("repos", repo.repo_id, {"receipt_version": rid, "install_status": "installed"})
    return rid


def test_reviewed_cleanup_removes_only_selected_data_and_retains_metadata(sv, api, repo):
    txid, directory, eid = transaction(sv, repo, 1)
    _, untouched, _ = transaction(sv, repo, 2)
    before = sv.storage.get("install_transactions", txid)
    answer = api.preview([eid])
    assert directory.is_dir()
    assert answer["totals"]["selected_entries"] == 1
    status, result = api.apply([eid], answer)
    assert status == 200 and result["ok"], result
    assert result["deleted"] == [eid]
    assert not directory.exists() and untouched.exists()
    assert sv.storage.get("install_transactions", txid) == before
    record = sv.storage.pref_get(result["record_key"])
    assert record["status"] == "complete" and record["evidence"][eid]["transaction.json"]
    assert (Path(repo.canonical_path) / "keep.txt").read_text() == "repository bytes"
    assert asyncio.run(sv.scheduler.list()) == []


def test_default_preview_is_read_only_and_never_selects_orphans_or_staging(sv, api, repo):
    _, directory, eid = transaction(sv, repo, 1)
    orphan = Path(sv.ctx.data_dir) / "backups" / "tx_ffffffffffffffff"
    staging = Path(sv.ctx.data_dir) / "staging" / eid.split(":")[0]
    for path, text in ((orphan, "untracked backup"), (staging, "staging")):
        path.mkdir(parents=True)
        (path / "keep").write_text(text)
    status, answer = api.request("preview", {})
    assert status == 200 and answer["entry_ids"] == [] and not answer["can_cleanup"]
    assert [entry["id"] for entry in answer["entries"]] == [eid]
    status, _ = api.apply([], answer)
    assert status == 400
    status, result = api.apply([eid], api.preview([eid]))
    assert status == 200 and result["ok"] and not directory.exists()
    assert (orphan / "keep").exists() and (staging / "keep").exists()


def test_nested_reviewed_tree_deletes_and_reports_logical_totals(sv, api, repo):
    _, directory, eid = transaction(sv, repo, 1)
    nested = directory / "staging/.kiro/tools"
    nested.mkdir(parents=True)
    (nested / "engine.js").write_bytes(b"bundled engine")
    (directory / "empty").mkdir()
    expected_size = sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())
    answer = api.preview([eid])
    assert answer["totals"]["selected_bytes"] == expected_size
    assert answer["totals"]["selected_files"] == 3
    status, result = api.apply([eid], answer)
    assert status == 200 and result["ok"] and not directory.exists()


def test_current_rollback_backup_is_protected_but_reviewed_ancestors_can_be_discarded(sv, api, repo):
    a, old, old_id = transaction(sv, repo, 1, status="committed", category="backups")
    b, older, older_id = transaction(sv, repo, 2, status="committed", category="backups")
    c, current, current_id = transaction(sv, repo, 3, status="committed", category="backups")
    prior = receipt(sv, repo, 1, a, status="superseded")
    middle = receipt(sv, repo, 2, b, status="superseded", prior=prior)
    receipt(sv, repo, 3, c, prior=middle)
    before = sv.storage.select("install_receipts", {"repo_id": repo.repo_id})
    answer = api.preview([old_id, older_id])
    entries = {item["id"]: item for item in answer["entries"]}
    assert entries[current_id]["reason"] == "current_receipt"
    assert entries[current_id]["protected"]
    for eid in (old_id, older_id):
        assert not entries[eid]["protected"] and entries[eid]["reason"] == "older_restore_point"
        assert "discards an older restore point" in entries[eid]["consequence"]
    assert answer["can_cleanup"] and answer["totals"]["selected_entries"] == 2
    status, result = api.apply([old_id, older_id], answer)
    assert status == 200 and result["ok"]
    assert not old.exists() and not older.exists() and current.exists()
    assert sv.storage.select("install_receipts", {"repo_id": repo.repo_id}) == before


def test_new_current_receipt_invalidates_old_backup_confirmation(sv, api, repo):
    txid, directory, eid = transaction(sv, repo, 1, status="committed", category="backups")
    answer = api.preview([eid])
    receipt(sv, repo, 1, txid)
    status, result = api.apply([eid], answer)
    assert status == 409 and result["details"]["reason"] == "plan_digest_mismatch"
    assert not api.preview([eid])["can_cleanup"] and directory.exists()


@pytest.mark.parametrize("state,reason", [
    ("staged", "active_transaction"), ("written", "active_transaction"),
    ("rolling_back", "active_transaction"), ("recovery_required", "recovery_required"),
    ("future_status", "active_transaction"),
])
def test_only_terminal_finished_transactions_are_eligible(sv, api, repo, state, reason):
    _, directory, eid = transaction(sv, repo, 1, status=state)
    answer = api.preview([eid])
    assert answer["entries"][0]["reason"] == reason
    assert not answer["can_cleanup"]
    status, result = api.apply([eid], answer)
    assert status == 409 and result["details"]["reason"] == "protected_entries", result
    assert directory.is_dir() and (directory / "payload.bin").exists()
    assert asyncio.run(sv.scheduler.list()) == []


def test_unfinished_terminal_row_and_repo_recovery_are_protected(sv, api, repo):
    _, _, unfinished = transaction(sv, repo, 1, finished=False)
    _, path, eligible = transaction(sv, repo, 2)
    answer = api.preview([])
    assert next(e for e in answer["entries"] if e["id"] == unfinished)["reason"] == "unfinished_transaction"
    sv.storage.update("repos", repo.repo_id, {"install_status": "recovery_required"})
    answer = api.preview([eligible])
    assert not answer["can_cleanup"]
    status, result = api.apply([eligible], answer)
    assert status == 409 and result["code"] == "install_recovery_required"
    assert path.exists()


def test_startup_finishes_committed_cleanup_before_maintenance_review(sv, api, repo):
    txid, directory, eid = transaction(sv, repo, 1, status="committed", category="backups", finished=False)
    before = api.preview([eid])
    assert not before["can_cleanup"]
    assert before["entries"][0]["reason"] == "unfinished_transaction"
    assert asyncio.run(sv.installer.settle_abandoned()) == [txid]
    row = sv.storage.get("install_transactions", txid)
    assert row["status"] == "committed" and row["finished_at"]
    assert directory.is_dir()
    after_settle = api.preview([eid])
    assert after_settle["can_cleanup"]
    status, result = api.apply([eid], after_settle)
    assert status == 200 and result["ok"]
    assert not directory.exists()
    assert (Path(repo.canonical_path) / "keep.txt").read_text() == "repository bytes"


def test_staging_cleanup_error_is_reported_without_aborting_startup(sv, repo, monkeypatch):
    txid, staging, _ = transaction(sv, repo, 1, status="committed", category="staging", finished=False)
    rmtree = shutil.rmtree

    def deny_cleanup(path, *args, **kwargs):
        if Path(path) == staging:
            raise PermissionError("temporary staging cleanup failure")
        return rmtree(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "rmtree", deny_cleanup)
        report = asyncio.run(sv.reconciler.startup())
    assert "settle_abandoned:storage_error" in report.issues
    assert report.finished_at and staging.exists()
    assert not sv.storage.get("install_transactions", txid)["finished_at"]
    assert asyncio.run(sv.installer.settle_abandoned()) == [txid]
    assert not staging.exists()
    assert (Path(repo.canonical_path) / "keep.txt").read_text() == "repository bytes"


def test_unsettled_transaction_protects_its_prior_receipt_even_without_current(sv, api, repo):
    ancestor, old_dir, old_id = transaction(sv, repo, 3, status="committed", category="backups")
    ancestor_rid = receipt(sv, repo, 3, ancestor, status="superseded")
    txid, directory, eid = transaction(sv, repo, 1, status="committed", category="backups")
    rid = receipt(sv, repo, 1, txid, status="superseded", prior=ancestor_rid)
    transaction(sv, repo, 2, status="recovery_required", prior=rid)
    answer = api.preview([eid])
    assert next(e for e in answer["entries"] if e["id"] == eid)["reason"] == "receipt_restoration"
    assert not answer["can_cleanup"] and directory.exists()
    old_entry = next(e for e in answer["entries"] if e["id"] == old_id)
    assert not old_entry["protected"] and old_entry["reason"] == "older_restore_point"
    assert old_dir.exists()


def test_missing_receipt_reference_blocks_cleanup_conservatively(sv, api, repo):
    _, directory, eid = transaction(sv, repo, 1)
    sv.storage.update("repos", repo.repo_id, {"receipt_version": "rc_missing"})
    answer = api.preview([eid])
    assert answer["entries"][0]["reason"] == "receipt_reference_missing"
    assert not answer["can_cleanup"] and directory.exists()


def test_old_unreferenced_receipt_history_is_retained_when_its_backup_is_cleaned(sv, api, repo):
    txid, directory, eid = transaction(sv, repo, 1, status="committed", category="backups")
    rid = receipt(sv, repo, 1, txid, status="superseded")
    before = sv.storage.get("install_receipts", rid)
    status, result = api.apply([eid], api.preview([eid]))
    assert status == 200 and result["ok"] and not directory.exists()
    assert sv.storage.get("install_receipts", rid) == before


@pytest.mark.parametrize("kind", ["digest", "selection", "added_file", "replaced_inode", "same_size"])
def test_tampering_or_filesystem_drift_refuses_before_any_deletion(sv, api, repo, kind):
    _, directory, eid = transaction(sv, repo, 1)
    _, other, other_id = transaction(sv, repo, 2)
    answer = api.preview([eid])
    ids = [eid]
    payload = directory / "payload.bin"
    if kind == "digest":
        answer["plan_digest"] = "0" * 64
    elif kind == "selection":
        ids = [other_id]
    elif kind == "added_file":
        (directory / "new-evidence").write_text("not reviewed")
    elif kind == "replaced_inode":
        content = payload.read_bytes()
        payload.unlink()
        payload.write_bytes(content)
    else:
        st = payload.stat()
        payload.write_bytes(b"x" * st.st_size)
        os.utime(payload, ns=(st.st_atime_ns, st.st_mtime_ns))
    status, result = api.apply(ids, answer)
    assert status == 409 and result["details"]["reason"] == "plan_digest_mismatch", result
    assert directory.exists() and other.exists()
    assert (directory / "transaction.json").exists()
    assert asyncio.run(sv.scheduler.list()) == []


@pytest.mark.parametrize("when", ["before_request", "after_lease", "after_metadata_commit"])
def test_new_recovery_row_invalidates_confirmation(sv, api, repo, monkeypatch, when):
    _, directory, eid = transaction(sv, repo, 1)
    answer = api.preview([eid])

    def recovery():
        transaction(sv, repo, 2, status="recovery_required")

    if when == "before_request":
        recovery()
    elif when == "after_lease":
        after(monkeypatch, sv.scheduler, "acquire_admin", recovery)
    else:
        original = sv.storage.pref_set
        fired = False

        def prepared(key, value):
            nonlocal fired
            original(key, value)
            if value.get("status") == "prepared" and not fired:
                fired = True
                recovery()

        monkeypatch.setattr(sv.storage, "pref_set", prepared)
    status, result = api.apply([eid], answer)
    assert status == 409 and result["details"]["reason"] == "plan_digest_mismatch", result
    assert directory.is_dir() and (directory / "payload.bin").exists()
    assert asyncio.run(sv.scheduler.list()) == []


@pytest.mark.parametrize("kind", ["entry", "nested", "category", "hardlink", "fifo"])
def test_indirect_or_special_files_are_never_followed(sv, api, repo, outside, kind):
    _, directory, eid = transaction(sv, repo, 1)
    secret = outside / "valuable"
    secret.write_bytes(b"never remove")
    if kind == "entry":
        directory.rename(directory.with_name("saved"))
        directory.symlink_to(outside, target_is_directory=True)
    elif kind == "nested":
        (directory / "linked").symlink_to(outside, target_is_directory=True)
    elif kind == "category":
        category = directory.parent
        category.rename(category.with_name("saved-failed"))
        category.symlink_to(outside, target_is_directory=True)
    elif kind == "hardlink":
        os.link(secret, directory / "linked-file")
    else:
        os.mkfifo(directory / "pipe")
    if kind == "category":
        status, result = api.request("preview", {"entry_ids": [eid]})
        assert status == 400 and result["code"] == "bad_path", result
    else:
        answer = api.preview([eid])
        assert not answer["can_cleanup"] and answer["entries"][0]["protected"]
        status, _ = api.apply([eid], answer)
        assert status == 409
    assert secret.read_bytes() == b"never remove"


def test_path_swap_after_preview_is_rejected_without_following_replacement(sv, api, repo, outside):
    _, directory, eid = transaction(sv, repo, 1)
    answer = api.preview([eid])
    old = directory.with_name("saved")
    directory.rename(old)
    (outside / "payload.bin").write_bytes(b"outside")
    directory.symlink_to(outside, target_is_directory=True)
    status, result = api.apply([eid], answer)
    assert status == 409 and result["details"]["reason"] == "plan_digest_mismatch"
    assert (outside / "payload.bin").read_bytes() == b"outside"
    assert (old / "payload.bin").read_bytes() == b"reviewed bytes"


@pytest.mark.parametrize("kind", ["category", "entry", "file"])
def test_swap_after_final_scan_is_refused_before_unlink(sv, api, repo, maintenance, monkeypatch, outside, kind):
    _, directory, eid = transaction(sv, repo, 1)
    answer = api.preview([eid])
    secret = outside / "secret"
    secret.write_text("external bytes")
    original = maintenance.MaintenanceService._delete
    moved = None

    def swap(service, *args):
        nonlocal moved
        if kind == "file":
            (directory / "payload.bin").unlink()
            (directory / "payload.bin").symlink_to(secret)
        else:
            target = directory.parent if kind == "category" else directory
            moved = target.with_name("saved")
            target.rename(moved)
            target.symlink_to(outside, target_is_directory=True)
        return original(service, *args)

    monkeypatch.setattr(maintenance.MaintenanceService, "_delete", swap)
    status, result = api.apply([eid], answer)
    assert status == 200 and not result["ok"] and result["deleted"] == []
    assert result["failures"][0]["reason"] == "path_changed"
    assert secret.read_text() == "external bytes"
    if moved is not None:
        preserved = moved / directory.name if kind == "category" else moved
        assert (preserved / "payload.bin").read_bytes() == b"reviewed bytes"
    else:
        assert (directory / "transaction.json").exists()


def test_stored_absolute_path_cannot_authorize_deletion_elsewhere(sv, api, repo, outside):
    txid, directory, eid = transaction(sv, repo, 1)
    (outside / "keep").write_text("safe")
    sv.storage.update("install_transactions", txid, {"failed_dir": str(outside)})
    answer = api.preview([eid])
    assert answer["entries"][0]["reason"] == "path_mismatch"
    assert not answer["can_cleanup"]
    status, _ = api.apply([eid], answer)
    assert status == 409 and directory.exists() and (outside / "keep").exists()


@pytest.mark.parametrize("body", [
    {"entry_ids": ["../repo"]}, {"entry_ids": ["tx_0000000000000001:staging"]},
    {"entry_ids": ["tx_0000000000000001:failed", "tx_0000000000000001:failed"]},
    {"entry_ids": "all"}, {"entry_ids": [4]}, {"path": "/tmp"},
])
def test_client_can_supply_only_bounded_generated_selection(api, body):
    status, result = api.request("preview", body)
    assert status == 400 and result["code"] == "bad_body", result


def test_foreign_repo_selection_is_refused(sv, api, tmp_path):
    other = sv.repos.add(str(CT.RepoBuilder(tmp_path / "other").with_git().build()), "other repository")
    _, directory, eid = transaction(sv, other, 1)
    status, answer = api.request("preview", {"entry_ids": [eid]})
    assert status == 409 and answer["details"]["reason"] == "selection_changed"
    assert (directory / "payload.bin").exists()


@pytest.mark.parametrize("kind", ["nodes", "depth", "evidence", "history"])
def test_scan_limits_refuse_deletion(sv, api, repo, maintenance, monkeypatch, kind):
    _, directory, eid = transaction(sv, repo, 1)
    if kind == "depth":
        nested = directory / "a/b"
        nested.mkdir(parents=True)
        (nested / "x").write_text("keep")
    limit, value = {"nodes": ("MAX_NODES", 1), "depth": ("MAX_DEPTH", 1), "evidence": ("MAX_METADATA_BYTES", 5),
                    "history": ("MAX_HISTORY", 0)}[kind]
    monkeypatch.setattr(maintenance, limit, value)
    status, answer = api.request("preview", {"entry_ids": [eid]})
    if kind == "history":
        assert status == 413 and answer["code"] == "too_large"
    else:
        assert status == 200 and not answer["can_cleanup"]
        assert answer["entries"][0]["reason"] in {"scan_limit", "evidence_limit"}
    assert directory.exists()


@pytest.mark.parametrize("kind", ["execution", "admin"])
def test_cleanup_serializes_with_installer_and_execution(sv, api, repo, kind):
    _, directory, eid = transaction(sv, repo, 1)
    answer = api.preview([eid])
    if kind == "execution":
        lease = sv.scheduler.acquire_execution(repo, intent_uuid=None, session_key=None, action_id="a_other")
    else:
        lease = sv.scheduler.acquire_admin(repo, operation_type="upgrade", transaction_id=None)
    grant = asyncio.run(lease)
    try:
        status, result = api.apply([eid], answer)
        assert status == 409 and result["code"] == "repo_busy"
        assert directory.is_dir()
    finally:
        asyncio.run(sv.scheduler.release(grant))


def test_partial_delete_retains_committed_evidence_and_requires_fresh_preview(sv, api, repo, maintenance,
                                                                             monkeypatch):
    _, directory, eid = transaction(sv, repo, 1)
    metadata = (directory / "transaction.json").read_bytes()
    answer = api.preview([eid])
    original = os.unlink
    calls = 0

    def fail_second(path, *args, **kwargs):
        nonlocal calls
        if kwargs.get("dir_fd") is not None:
            calls += 1
            if calls == 2:
                raise PermissionError(13, "injected failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(maintenance.os, "unlink", fail_second)
    status, result = api.apply([eid], answer)
    assert status == 200 and not result["ok"] and result["requires_preview"], result
    assert result["remaining"] == [eid] and result["failures"][0]["reason"] == "delete_failed"
    record = sv.storage.pref_get(result["record_key"])
    assert record["status"] == "partial"
    assert base64.b64decode(record["evidence"][eid]["transaction.json"]) == metadata
    monkeypatch.setattr(maintenance.os, "unlink", original)
    status, stale = api.apply([eid], answer)
    assert status == 409 and stale["details"]["reason"] == "plan_digest_mismatch"
    status, result = api.apply([eid], api.preview([eid]))
    assert status == 200 and result["ok"] and not directory.exists()
    assert asyncio.run(sv.scheduler.list()) == []


def test_crash_after_an_unlink_leaves_prepared_evidence_and_retryable_remainder(sv, api, repo, maintenance,
                                                                               monkeypatch):
    _, directory, eid = transaction(sv, repo, 1)
    metadata = (directory / "transaction.json").read_bytes()
    answer = api.preview([eid])
    original = os.unlink

    def crash(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if kwargs.get("dir_fd") is not None:
            raise RuntimeError("simulated process interruption")
        return result

    monkeypatch.setattr(maintenance.os, "unlink", crash)
    status, _ = api.apply([eid], answer)
    assert status == 500
    record = sv.storage.pref_get(f"maintenance:{repo.repo_id}:{answer['plan_digest']}")
    assert record["status"] == "prepared"
    assert base64.b64decode(record["evidence"][eid]["transaction.json"]) == metadata
    monkeypatch.setattr(maintenance.os, "unlink", original)
    status, result = api.apply([eid], api.preview([eid]))
    assert status == 200 and result["ok"] and not directory.exists()


def test_metadata_commit_failure_prevents_all_deletion(sv, api, repo, monkeypatch):
    _, directory, eid = transaction(sv, repo, 1)
    answer = api.preview([eid])

    def fail(*args):
        raise OSError("database cannot persist evidence")

    monkeypatch.setattr(sv.storage, "pref_set", fail)
    status, _ = api.apply([eid], answer)
    assert status == 500
    assert (directory / "payload.bin").exists() and (directory / "transaction.json").exists()
    assert asyncio.run(sv.scheduler.list()) == []


@pytest.mark.parametrize("cancel_count", [1, 2])
def test_cancellation_does_not_release_lease_while_worker_deletes(sv, repo, maintenance, monkeypatch, cancel_count):
    _, directory, eid = transaction(sv, repo, 1)

    async def scenario():
        service = maintenance.MaintenanceService(sv)
        answer = await service.preview(repo, [eid])
        started, finish = asyncio.Event(), Event()
        loop = asyncio.get_running_loop()
        original = service._delete

        def waiting(*args):
            loop.call_soon_threadsafe(started.set)
            assert finish.wait(5), "test did not release worker"
            return original(*args)

        monkeypatch.setattr(service, "_delete", waiting)
        task = asyncio.create_task(service.cleanup(repo, [eid], answer["plan_digest"]))
        await asyncio.wait_for(started.wait(), 3)
        for _ in range(cancel_count):
            task.cancel()
            await asyncio.sleep(0)
        # The paused worker holds the SQLite deletion transaction: do not query it; release cannot run yet.
        assert not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await sv.scheduler.get(repo.resolved_identity) is None
        assert not directory.exists()

    asyncio.run(scenario())


@pytest.mark.parametrize("factory", [CT.anon_request, CT.user_request, CT.app_request])
def test_both_routes_are_owner_only(sv, routes, fake_host, factory):
    maintenance = [entry for entry in routes if "/maintenance/" in entry.path]
    assert len(maintenance) == 2
    for entry in maintenance:
        path = entry.path.replace("{repo_id}", "r_one")
        status, _ = call(sv, routes, "POST", path, factory("POST", path, host=fake_host, body={}))
        assert status in (401, 403)
