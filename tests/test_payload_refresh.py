"""Same-version bundle refreshes through real temporary repositories and SQLite receipts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import conftest as CT
from test_handlers_auth import call, common, routes, sv  # noqa: F401 - shared route fixtures


VERSION = "2.7.1"
OWNED = ".kiro/tools/aidlc-progress.ts"
STATE = "aidlc/spaces/default/intents/260910-refresh/aidlc-state.md"


def _payload(root: Path, revision: str, version: str = VERSION) -> Path:
    """Build independent payload bytes; never change the shipped payload or shared fixtures."""
    contents = {
        ".kiro/tools/aidlc-version.ts": f'export const AIDLC_VERSION = "{version}";\n',
        ".kiro/tools/aidlc-utility.ts": "// utility entry point\n",
        ".kiro/tools/aidlc-state.ts": "// state entry point\n",
        ".kiro/tools/aidlc-lib.ts": "export const CURRENT_STATE_VERSION = 8;\n",
        OWNED: f"// plan-progress fingerprint {revision}\n",
        ".kiro/settings/cli.json": '{"chat.defaultAgent": "aidlc"}\n',
        "aidlc/active-space": "default\n",
        "aidlc/spaces/default/memory/project.md": "# Initial project rules\n",
    }
    rows = []
    digest = hashlib.sha256()
    for rel, text in sorted(contents.items()):
        data = text.encode()
        path = root / "aidlc-kiro" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        ownership = "shell" if rel.startswith("aidlc/") else "framework"
        if rel == ".kiro/settings/cli.json":
            ownership = "merge"
        rows.append({"path": rel, "sha256": sha, "size": len(data), "ownership": ownership})
        digest.update(f"{rel}\0{sha}\n".encode())
    manifest = {
        "schema": "aidlc-studio-payload/1",
        "harness": "kiro",
        "engineVersion": version,
        "source": {"upstreamEngineVersion": version},
        "compatibleStateVersions": [8],
        "stageCount": 0,
        "payloadDigest": digest.hexdigest(),
        "fileCount": len(rows),
        "mergeTargets": {
            ".kiro/settings/cli.json": {
                "strategy": "json-managed-keys", "managedKeys": ["chat.defaultAgent"],
            },
        },
        "files": rows,
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest) + "\n")
    return path


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def refresh_env(sv, studio, tmp_path, routes, fake_host):
    def bundle(revision, version=VERSION):
        manifest_path = _payload(tmp_path / f"bundle-{revision}-{version}", revision, version)
        sv.payload = studio.installer.PayloadManifest.load(manifest_path)
        sv.installer = studio.installer.Installer(
            sv.storage, sv.repos, sv.reader, sv.engine, sv.scheduler, sv.activity,
            sv.events, sv.clock, sv.ids, payload_dir=manifest_path.parent,
            manifest=sv.payload, data_dir=Path(sv.ctx.data_dir), settings=sv.settings,
            boot_id=sv.boot_id,
        )
        sv.payload_status = sv.installer.verify_payload()
        assert sv.payload_status.ok
        return sv.installer

    def request(method, path, body=None):
        return call(sv, routes, method, path, CT.owner_request(
            method, path, host=fake_host, body=body or {},
        ))

    def setup(version=VERSION):
        root = tmp_path / "repository"
        root.mkdir()
        (root / "README.md").write_text("# User project\n")
        cli = root / ".kiro/settings/cli.json"
        cli.parent.mkdir(parents=True)
        cli.write_text('{\n  "user.setting": "keep exactly"\n}\n')
        repo = sv.repos.add(str(root), "refresh")
        installer = bundle("old", version)
        plan = installer.preview(repo, "install")
        result = asyncio.run(installer.run(repo, "install", plan_digest=plan.digest()))
        assert result.status == "committed", result.error
        repo = sv.repos.get(repo.repo_id)
        old_receipt = installer.current_receipt(repo.repo_id)
        state = root / STATE
        state.parent.mkdir(parents=True)
        state.write_text("# AI-DLC State\n\n- **State Version**: 8\n- **Current Stage**: code-generation\n")
        (root / "aidlc/spaces/default/memory/project.md").write_text("# User-edited rules\n")
        (root / ".kiro/tools/user-tool.ts").write_text("// User's unowned helper\n")
        return SimpleNamespace(
            services=sv, root=root, repo=repo, old_receipt=old_receipt,
            bundle=bundle, request=request, before=_tree(root), routes=routes, host=fake_host,
        )

    return setup


def _install_json(env):
    status, body = env.request("GET", "/repos")
    assert status == 200
    return next(r["install"] for r in body["repos"] if r["repo_id"] == env.repo.repo_id)


def test_same_version_refresh_is_offered_and_commits_through_api(refresh_env):
    env = refresh_env()
    installer = env.bundle("fixed")
    assert env.old_receipt.engine_version == installer.manifest.engine_version == VERSION
    assert env.old_receipt.payload_digest != installer.manifest.payload_digest
    assert _install_json(env)["upgrade_available"] is True

    prefix = f"/repos/{env.repo.repo_id}/upgrade"
    status, preview = env.request("POST", prefix + "/preview")
    assert status == 200, preview
    assert preview["plan"]["same_version"] is False
    assert preview["plan"]["blocking"] is False
    assert preview["plan"]["state_versions_found"] == [8]
    assert preview["plan"]["requires_admin_lease"] is True

    async def apply_and_wait():
        entry = next(r for r in env.routes if r.method == "POST" and r.path == "/repos/{repo_id}/upgrade")
        request = CT.owner_request("POST", prefix, host=env.host, body={
            "plan_digest": preview["plan_digest"],
        })
        request.match_info["repo_id"] = env.repo.repo_id
        request.app["ctx"] = env.services.ctx
        response = await entry.handler(request, env.services.ctx)
        assert response.status == 202, response.body
        transaction_id = json.loads(response.body)["transaction_id"]
        await asyncio.gather(*tuple(env.services.background))
        return installer.transaction(transaction_id)

    result = asyncio.run(apply_and_wait())
    assert result.status == "committed", result.error
    after = _tree(env.root)
    assert after[OWNED] == b"// plan-progress fingerprint fixed\n"
    assert {p: b for p, b in after.items() if p != OWNED} == {
        p: b for p, b in env.before.items() if p != OWNED
    }
    current = installer.current_receipt(env.repo.repo_id)
    assert current.receipt_id != env.old_receipt.receipt_id
    assert current.prior_receipt_id == env.old_receipt.receipt_id
    assert current.payload_digest == installer.manifest.payload_digest
    assert current.engine_version == VERSION
    assert current.by_path()[OWNED].sha256 == hashlib.sha256(after[OWNED]).hexdigest()
    assert STATE not in current.by_path()
    assert installer.receipt(env.old_receipt.receipt_id).status == "superseded"
    assert env.services.storage.get("repos", env.repo.repo_id)["receipt_version"] == current.receipt_id
    assert env.services.storage.lease_list() == []
    assert _install_json(env)["upgrade_available"] is False
    assert env.request("POST", prefix + "/preview")[1]["code"] == "same_version_installed"


@pytest.mark.parametrize("entrypoint", ["begin", "run"])
def test_unchanged_bundle_is_a_refused_noop_at_every_entrypoint(refresh_env, studio, entrypoint):
    env = refresh_env()
    installer = env.services.installer
    plan = installer.preview(env.repo, "upgrade")
    assert plan.same_version is True
    assert _install_json(env)["upgrade_available"] is False
    status, body = env.request("POST", f"/repos/{env.repo.repo_id}/upgrade/preview")
    assert status == 409 and body["code"] == "same_version_installed"
    transactions = installer.transactions(env.repo.repo_id)
    with pytest.raises(studio.errors.StudioError) as exc:
        asyncio.run(getattr(installer, entrypoint)(env.repo, "upgrade", plan_digest=plan.digest()))
    assert exc.value.code == "same_version_installed"
    assert installer.transactions(env.repo.repo_id) == transactions
    assert installer.current_receipt(env.repo.repo_id) == env.old_receipt
    assert _tree(env.root) == env.before


@pytest.mark.parametrize("change", [
    "missing_receipt", "empty_digest", "invalid_digest", "wrong_identity",
    "missing_identity", "wrong_version", "superseded",
])
def test_refresh_requires_a_trusted_current_receipt(refresh_env, studio, change):
    env = refresh_env()
    installer = env.bundle("fixed")
    receipt_id = env.old_receipt.receipt_id
    if change == "missing_receipt":
        env.services.storage.delete("install_receipts", receipt_id)
    else:
        mutation = {
            "empty_digest": {"payload_digest": ""},
            "invalid_digest": {"payload_digest": "not-a-payload-digest"},
            "wrong_identity": {"resolved_repo_identity": "some-other-repository"},
            "missing_identity": {"resolved_repo_identity": None},
            "wrong_version": {"engine_version": "2.6.2"},
            "superseded": {"status": "superseded"},
        }[change]
        env.services.storage.update("install_receipts", receipt_id, mutation)
    assert _install_json(env)["upgrade_available"] is False
    status, body = env.request("POST", f"/repos/{env.repo.repo_id}/upgrade/preview")
    assert status == 409 and body["code"] == "same_version_installed"
    plan = installer.preview(env.repo, "upgrade")
    assert plan.same_version is True
    with pytest.raises(studio.errors.StudioError):
        asyncio.run(installer.begin(env.repo, "upgrade", plan_digest=plan.digest()))
    assert _tree(env.root) == env.before


def test_newer_installed_version_still_blocks_refresh(refresh_env, studio):
    env = refresh_env("2.8.0")
    installer = env.bundle("fixed")
    install = _install_json(env)
    assert install["upgrade_available"] is False and install["newer_installed"] is True
    status, body = env.request("POST", f"/repos/{env.repo.repo_id}/upgrade/preview")
    assert status == 409 and body["code"] == "newer_installed"
    plan = installer.preview(env.repo, "upgrade")
    with pytest.raises(studio.errors.StudioError) as exc:
        asyncio.run(installer.run(env.repo, "upgrade", plan_digest=plan.digest()))
    assert exc.value.code == "newer_installed"
    assert _tree(env.root) == env.before


def test_foreign_harness_does_not_gain_refresh_authority(refresh_env, studio):
    env = refresh_env()
    (env.root / ".kiro").rename(env.root / ".claude")
    before = _tree(env.root)
    installer = env.bundle("fixed")
    install = _install_json(env)
    assert install["engine_version"] == VERSION
    assert install["own_engine_version"] is None
    assert install["upgrade_available"] is False
    status, body = env.request("POST", f"/repos/{env.repo.repo_id}/upgrade/preview")
    assert status == 409 and body["code"] == "not_installed"
    assert _tree(env.root) == before


def test_owned_tampering_still_blocks_refresh(refresh_env, studio):
    env = refresh_env()
    installer = env.bundle("fixed")
    (env.root / OWNED).write_text("// User changed an owned file\n")
    before = _tree(env.root)
    plan = installer.preview(env.repo, "upgrade")
    assert plan.blocking and any(e.path == OWNED for e in plan.blockers)
    with pytest.raises(studio.errors.StudioError) as exc:
        asyncio.run(installer.begin(env.repo, "upgrade", plan_digest=plan.digest()))
    assert exc.value.code == "install_conflict"
    assert installer.current_receipt(env.repo.repo_id) == env.old_receipt
    assert _tree(env.root) == before


def test_incompatible_state_still_blocks_refresh(refresh_env, studio):
    env = refresh_env()
    installer = env.bundle("fixed")
    (env.root / STATE).write_text("# AI-DLC State\n\n- **State Version**: 7\n")
    before = _tree(env.root)
    plan = installer.preview(env.repo, "upgrade")
    assert plan.state_version_blocked
    status, body = env.request("POST", f"/repos/{env.repo.repo_id}/upgrade/preview")
    assert status == 409 and body["code"] == "state_version_migration_unconfirmed"
    with pytest.raises(studio.errors.StudioError) as exc:
        asyncio.run(installer.begin(env.repo, "upgrade", plan_digest=plan.digest()))
    assert exc.value.code == "state_version_migration_unconfirmed"
    assert _tree(env.root) == before


def test_admin_lease_still_blocks_refresh(refresh_env, studio):
    env = refresh_env()
    installer = env.bundle("fixed")
    plan = installer.preview(env.repo, "upgrade")

    async def attempt():
        grant = await env.services.scheduler.acquire_admin(
            env.repo, operation_type="doctor", transaction_id=None,
        )
        try:
            with pytest.raises(studio.errors.StudioError) as exc:
                await installer.begin(env.repo, "upgrade", plan_digest=plan.digest())
            assert exc.value.code == "repo_busy"
        finally:
            await env.services.scheduler.release(grant)

    asyncio.run(attempt())
    assert installer.current_receipt(env.repo.repo_id) == env.old_receipt
    assert _tree(env.root) == env.before


def test_reserved_refresh_rechecks_receipt_before_staging(refresh_env):
    env = refresh_env()
    installer = env.bundle("fixed")
    plan = installer.preview(env.repo, "upgrade")

    async def attempt():
        pending = await installer.begin(env.repo, "upgrade", plan_digest=plan.digest())
        env.services.storage.update("install_receipts", env.old_receipt.receipt_id, {
            "payload_digest": "",
        })
        return await installer.run(
            env.repo, "upgrade", plan_digest=plan.digest(), transaction_id=pending.transaction_id,
        )

    result = asyncio.run(attempt())
    assert result.status == "failed" and "install_conflict" in result.error
    assert not any(s.name == "stage_payload" for s in result.steps)
    assert _tree(env.root) == env.before
    assert env.services.storage.lease_list() == []


@pytest.mark.parametrize("boundary", ["after:verify_staging", "after:acquire_admin_lease"])
def test_refresh_rechecks_receipt_under_lease_before_writing(refresh_env, boundary):
    env = refresh_env()
    installer = env.bundle("fixed")
    plan = installer.preview(env.repo, "upgrade")

    def revoke_receipt(marker):
        if marker == boundary:
            env.services.storage.update("install_receipts", env.old_receipt.receipt_id, {
                "payload_digest": "",
            })

    installer.fault_injector = revoke_receipt
    result = asyncio.run(installer.run(env.repo, "upgrade", plan_digest=plan.digest()))
    assert result.status == "failed" and "install_conflict" in result.error
    assert not any(s.name == "backup" for s in result.steps)
    assert _tree(env.root) == env.before
    assert installer.current_receipt(env.repo.repo_id).receipt_id == env.old_receipt.receipt_id
    assert env.services.storage.lease_list() == []


def test_refresh_rechecks_repository_identity_after_staging(refresh_env):
    env = refresh_env()
    installer = env.bundle("fixed")
    plan = installer.preview(env.repo, "upgrade")

    def replace_root(marker):
        if marker == "after:verify_staging":
            moved = env.root.with_name("original-repository")
            env.root.rename(moved)
            shutil.copytree(moved, env.root)

    installer.fault_injector = replace_root
    result = asyncio.run(installer.run(env.repo, "upgrade", plan_digest=plan.digest()))
    assert result.status == "failed" and "repo_unavailable" in result.error
    assert not any(s.name == "backup" for s in result.steps)
    assert _tree(env.root) == env.before
    assert _tree(env.root.with_name("original-repository")) == env.before
    assert installer.current_receipt(env.repo.repo_id) == env.old_receipt
    assert env.services.storage.lease_list() == []


def test_missing_manifest_digest_cannot_offer_a_refresh(refresh_env):
    env = refresh_env()
    installer = env.bundle("fixed")
    env.services.payload = replace(installer.manifest, payload_digest="")
    installer._manifest = env.services.payload
    assert _install_json(env)["upgrade_available"] is False
    assert installer.preview(env.repo, "upgrade").same_version is True
