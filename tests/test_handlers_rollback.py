"""The version rollback route uses the reviewed receipts and the actual installer."""

import asyncio
from pathlib import Path

import pytest

import conftest as CT
from test_handlers_auth import call, common, routes, sv  # noqa: F401
from test_installer import FaultInjector, payload_v1, payload_v2, tree  # noqa: F401


@pytest.fixture
def upgraded_repo(studio, sv, tmp_path, payload_v1, payload_v2):
    root = tmp_path / "versioned"
    root.mkdir()
    (root / "README.md").write_text("user work\n")
    repo = sv.repos.add(str(root))
    for kind, payload in (("install", payload_v1), ("upgrade", payload_v2)):
        manifest = studio.installer.PayloadManifest.load(payload[1])
        sv.installer._manifest = manifest
        sv.installer._payload_root = payload[0]
        sv.payload = manifest
        plan = sv.installer.preview(repo, kind)
        result = asyncio.run(sv.installer.run(repo, kind, plan_digest=plan.digest()))
        assert result.status == "committed", result
        repo = sv.repos.get(repo.repo_id)
    return repo


def test_rollback_preview_acceptance_completion_and_target_disappears(
    sv, routes, fake_host, upgraded_repo, monkeypatch,
):
    repo = upgraded_repo
    detail = f"/repos/{repo.repo_id}"
    status, body = call(sv, routes, "GET", detail, CT.owner_request("GET", detail, host=fake_host))
    assert status == 200, body
    assert body["repo"]["install"]["rollback_target"]["engine_version"] == "2.6.2"

    path = detail + "/rollback"
    status, preview = call(sv, routes, "POST", path + "/preview",
                          CT.owner_request("POST", path + "/preview", host=fake_host, body={}))
    assert status == 200 and preview["plan"]["kind"] == "rollback", preview
    assert not preview["plan"]["blocking"]
    assert preview["plan"]["engine_from"] == "2.7.0" and preview["plan"]["engine_to"] == "2.6.2"
    original = sv.installer.run
    delegated = []

    async def record_run(repo_arg, kind, *, plan_digest, transaction_id=None):
        delegated.append((repo_arg, kind, plan_digest, transaction_id))

    monkeypatch.setattr(sv.installer, "run", record_run)
    status, accepted = call(sv, routes, "POST", path, CT.owner_request(
        "POST", path, host=fake_host, body={"plan_digest": preview["plan_digest"]},
    ))
    assert status == 202, accepted
    assert len(delegated) == 1
    same_repo, kind, digest, txid = delegated[0]
    assert kind == "rollback" and digest == preview["plan_digest"]
    result = asyncio.run(original(same_repo, kind, plan_digest=digest, transaction_id=txid))
    assert result.status == "committed" and result.engine_version == "2.6.2", result
    assert (Path(repo.canonical_path) / "README.md").read_text() == "user work\n"
    status, body = call(sv, routes, "GET", detail, CT.owner_request("GET", detail, host=fake_host))
    assert status == 200 and body["repo"]["install"]["rollback_target"] is None


def test_changed_files_block_rollback_and_stale_confirmation_creates_no_transaction(
    sv, routes, fake_host, upgraded_repo,
):
    repo = upgraded_repo
    path = f"/repos/{repo.repo_id}/rollback"
    plan = sv.installer.preview(repo, "rollback")
    changed = Path(repo.canonical_path) / ".kiro/tools/aidlc-utility.ts"
    changed.write_text("user modification\n")
    status, preview = call(sv, routes, "POST", path + "/preview",
                          CT.owner_request("POST", path + "/preview", host=fake_host, body={}))
    assert status == 200 and preview["plan"]["blocking"], preview
    status, body = call(sv, routes, "POST", path, CT.owner_request(
        "POST", path, host=fake_host, body={"plan_digest": plan.digest()},
    ))
    assert status == 409 and body["code"] == "install_conflict", body
    assert changed.read_text() == "user modification\n"
    assert not any(tx.kind == "rollback" for tx in sv.installer.transactions(repo.repo_id))


@pytest.mark.parametrize("kind", ["uninstall", "rollback"])
def test_application_recovery_restores_interrupted_receipt_transaction_without_payload(
    studio, sv, routes, fake_host, upgraded_repo, monkeypatch, tmp_path, kind,
):
    repo = upgraded_repo
    root = Path(repo.canonical_path)
    before = tree(root)
    fault = FaultInjector(studio.installer.InjectedFault)
    fault.at("after:remove_files" if kind == "uninstall" else "after:restore_version",
             "before:restore_backups")
    sv.installer.fault_injector = fault
    plan = sv.installer.preview(repo, kind)
    failed = asyncio.run(sv.installer.run(repo, kind, plan_digest=plan.digest()))
    assert failed.status == "recovery_required"
    fault.markers.clear()
    sv.installer._payload_root = tmp_path / "payload-not-available"
    handler = CT.studio_module("handlers.repos")

    async def no_payload(*_):
        pytest.fail("receipt recovery must not depend on the bundled payload")

    monkeypatch.setattr(handler, "_require_payload", no_payload)
    path = f"/repos/{repo.repo_id}/install/recovery"
    status, preview = call(sv, routes, "POST", path + "/preview",
                          CT.owner_request("POST", path + "/preview", host=fake_host, body={}))
    assert status == 200 and not preview["plan"]["blocking"], preview
    assert preview["plan"]["recovery_transaction_id"] == failed.transaction_id
    assert preview["plan"]["recovery_kind"] == kind
    original = sv.installer.run
    delegated = []

    async def record_run(repo_arg, kind_arg, *, plan_digest, transaction_id=None):
        delegated.append((repo_arg, kind_arg, plan_digest, transaction_id))

    monkeypatch.setattr(sv.installer, "run", record_run)
    status, accepted = call(sv, routes, "POST", path, CT.owner_request(
        "POST", path, host=fake_host, body={"plan_digest": preview["plan_digest"]},
    ))
    assert status == 202 and accepted["transaction_id"] == failed.transaction_id, accepted
    assert len(delegated) == 1
    same_repo, recovery, digest, txid = delegated[0]
    result = asyncio.run(original(same_repo, recovery, plan_digest=digest, transaction_id=txid))
    assert result.status == "rolled_back" and tree(root) == before, result
    assert sv.repos.get(repo.repo_id).install_status == "installed"
