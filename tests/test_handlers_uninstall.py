"""The uninstall preview and 202 transport connected to the real transaction implementation."""
import asyncio

import pytest

import conftest as CT
from test_handlers_auth import call, common, routes, sv  # noqa: F401


@pytest.fixture
def owned_repo(sv, tmp_path):
    root = tmp_path / "owned"
    root.mkdir()
    (root / "README.md").write_text("user work\n")
    (root / "AGENTS.md").write_text("user instructions\n")
    repo = sv.repos.add(str(root))
    plan = sv.installer.preview(repo, "install")
    result = asyncio.run(sv.installer.run(repo, "install", plan_digest=plan.digest()))
    assert result.status == "committed"
    return sv.repos.get(repo.repo_id)


def test_uninstall_preview_acceptance_and_completion(sv, routes, fake_host, owned_repo, monkeypatch):
    repo = owned_repo
    path = f"/repos/{repo.repo_id}/uninstall"
    status, preview = call(sv, routes, "POST", path + "/preview",
                          CT.owner_request("POST", path + "/preview", host=fake_host, body={}))
    assert status == 200 and preview["plan"]["kind"] == "uninstall", preview
    assert not preview["plan"]["blocking"]
    original = sv.installer.run
    delegated = []

    async def record_run(repo_arg, kind, *, plan_digest, transaction_id=None):
        delegated.append((repo_arg, kind, plan_digest, transaction_id))

    monkeypatch.setattr(sv.installer, "run", record_run)
    status, accepted = call(sv, routes, "POST", path,
                           CT.owner_request("POST", path, host=fake_host,
                                            body={"plan_digest": preview["plan_digest"]}))
    assert status == 202, accepted
    assert accepted["status"] == "staged"
    assert sv.installer.transaction(accepted["transaction_id"]).kind == "uninstall"
    assert len(delegated) == 1
    same_repo, kind, digest, txid = delegated[0]
    result = asyncio.run(original(same_repo, kind, plan_digest=digest, transaction_id=txid))
    assert result.status == "committed", result
    assert sv.installer.current_receipt(repo.repo_id) is None
    from pathlib import Path
    root = Path(repo.canonical_path)
    assert (root / "README.md").read_text() == "user work\n"
    # The safe removal may leave the blank separator introduced before the fenced block.
    assert (root / "AGENTS.md").read_text().rstrip("\n") == "user instructions"
    assert (root / "aidlc").is_dir()
    assert not (root / ".kiro/tools/aidlc-utility.ts").exists()


def test_uninstall_rejects_stale_preview_before_acceptance(sv, routes, fake_host, owned_repo):
    path = f"/repos/{owned_repo.repo_id}/uninstall"
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={"plan_digest": "stale"}))
    assert status == 409 and body["code"] == "install_conflict", body
    assert all(tx.kind != "uninstall" for tx in sv.installer.transactions(owned_repo.repo_id))


def test_transaction_read_cannot_name_another_repository(sv, routes, fake_host, owned_repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    second = sv.repos.add(str(other))
    tx = sv.installer.transactions(owned_repo.repo_id)[0]
    path = f"/repos/{second.repo_id}/transactions/{tx.transaction_id}"
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 404 and body["code"] == "transaction_not_found"
