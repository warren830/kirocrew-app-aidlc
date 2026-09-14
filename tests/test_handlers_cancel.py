"""Cancellation names an existing repository transaction and keeps its eventual result observable."""
import asyncio

import conftest as CT
from test_handlers_auth import call, common, routes, sv  # noqa: F401


def test_cancel_before_worker_starts_then_worker_settles_without_installing(sv, routes, fake_host, tmp_path):
    root = tmp_path / "cancel-before-install"
    root.mkdir()
    (root / "README.md").write_text("user content")
    repo = sv.repos.add(str(root))
    plan = sv.installer.preview(repo, "install")
    tx = asyncio.run(sv.installer.begin(repo, "install", plan_digest=plan.digest()))
    path = f"/repos/{repo.repo_id}/transactions/{tx.transaction_id}/cancel"
    status, body = call(sv, routes, "POST", path, CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 202 and body["transaction"]["error"] == "cancel_requested", body
    result = asyncio.run(sv.installer.run(repo, "install", plan_digest=plan.digest(),
                                         transaction_id=tx.transaction_id))
    assert result.status in {"failed", "rolled_back"} and result.error == "cancelled", result
    assert not (root / ".kiro/tools/aidlc-utility.ts").exists()
    assert (root / "README.md").read_text() == "user content"
    assert sv.installer.current_receipt(repo.repo_id) is None
    assert asyncio.run(sv.scheduler.list()) == []


def test_cancel_cannot_cross_repository_transaction_identity(sv, routes, fake_host, tmp_path):
    roots = [tmp_path / "one", tmp_path / "two"]
    for root in roots:
        root.mkdir()
    first, second = [sv.repos.add(str(root)) for root in roots]
    plan = sv.installer.preview(first, "install")
    tx = asyncio.run(sv.installer.begin(first, "install", plan_digest=plan.digest()))
    path = f"/repos/{second.repo_id}/transactions/{tx.transaction_id}/cancel"
    status, body = call(sv, routes, "POST", path, CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 404 and body["code"] == "transaction_not_found", body
    assert sv.installer.transaction(tx.transaction_id).error is None
