"""The uninstall preview and 202 transport connected to the real transaction implementation."""
import asyncio
from pathlib import Path

import pytest

from _helpers_c import capture_run
from test_handlers_auth import call, common, ocall, routes, sv  # noqa: F401


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


def test_uninstall_preview_acceptance_and_completion(sv, ocall, owned_repo, monkeypatch):
    repo = owned_repo
    path = f"/repos/{repo.repo_id}/transactions"
    status, preview = ocall("POST", path + "/preview", body={"kind": "uninstall"})
    assert status == 200 and preview["plan"]["kind"] == "uninstall", preview
    assert not preview["plan"]["blocking"]
    original, delegated = capture_run(monkeypatch, sv.installer)
    status, accepted = ocall("POST", path, body={"kind": "uninstall", "plan_digest": preview["plan_digest"]})
    assert status == 202, accepted
    assert accepted["status"] == "staged"
    assert sv.installer.transaction(accepted["transaction_id"]).kind == "uninstall"
    assert len(delegated) == 1
    same_repo, kind, digest, txid = delegated[0]
    result = asyncio.run(original(same_repo, kind, plan_digest=digest, transaction_id=txid))
    assert result.status == "committed", result
    assert sv.installer.current_receipt(repo.repo_id) is None
    root = Path(repo.canonical_path)
    assert (root / "README.md").read_text() == "user work\n"
    # The safe removal may leave the blank separator introduced before the fenced block.
    assert (root / "AGENTS.md").read_text().rstrip("\n") == "user instructions"
    assert (root / "aidlc").is_dir()
    assert not (root / ".kiro/tools/aidlc-utility.ts").exists()


def test_uninstall_rejects_stale_preview_before_acceptance(sv, ocall, owned_repo):
    status, body = ocall("POST", f"/repos/{owned_repo.repo_id}/transactions",
                         body={"kind": "uninstall", "plan_digest": "stale"})
    assert status == 409 and body["code"] == "install_conflict", body
    assert all(tx.kind != "uninstall" for tx in sv.installer.transactions(owned_repo.repo_id))


def test_transaction_read_cannot_name_another_repository(sv, ocall, owned_repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    second = sv.repos.add(str(other))
    tx = sv.installer.transactions(owned_repo.repo_id)[0]
    status, body = ocall("GET", f"/repos/{second.repo_id}/transactions/{tx.transaction_id}")
    assert status == 404 and body["code"] == "transaction_not_found"
