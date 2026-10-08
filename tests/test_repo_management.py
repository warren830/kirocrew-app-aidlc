"""Reversible registry metadata and explicit, bounded gateway directory selection."""
import asyncio

import pytest

import conftest as CT
from test_handlers_auth import call, common, routes, sv  # noqa: F401


def invoke(sv, routes, host, method, path, body=None, **kwargs):
    return call(sv, routes, method, path, CT.owner_request(method, path, host=host, body=body, **kwargs))


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_rename_archive_restore_preserve_identity_history_and_disk(sv, routes, fake_host, gate_repo):
    repo = sv.repos.add(str(gate_repo), "before")
    before = tree(gate_repo)
    path = f"/repos/{repo.repo_id}/metadata"
    status, body = invoke(sv, routes, fake_host, "PUT", path, {"label": " New label ", "archived": True})
    assert status == 200 and body["repo"]["label"] == "New label" and body["repo"]["archived"] is True
    assert sv.repos.list() == []
    archived = sv.repos.list(include_archived=True)
    assert len(archived) == 1
    assert (archived[0].resolved_identity, archived[0].canonical_path) == (repo.resolved_identity, repo.canonical_path)
    assert invoke(sv, routes, fake_host, "PUT", path, {"archived": False})[0] == 200
    assert (sv.repos.list()[0].repo_id, sv.repos.list()[0].label) == (repo.repo_id, "New label")
    assert tree(gate_repo) == before


@pytest.mark.parametrize("body", [{}, {"label": ""}, {"label": 1}, {"archived": "yes"},
                                  {"canonical_path": "/tmp/other"}, {"label": "a\x00b"}])
def test_metadata_rejects_invalid_or_identity_changes(sv, routes, fake_host, gate_repo, body):
    repo = sv.repos.add(str(gate_repo), "original")
    assert invoke(sv, routes, fake_host, "PUT", f"/repos/{repo.repo_id}/metadata", body)[0] == 400
    assert sv.repos.get(repo.repo_id).label == "original"
    assert not sv.repos.get(repo.repo_id).archived


def test_archive_refuses_live_lease_but_rename_is_available(sv, routes, fake_host, gate_repo):
    repo = sv.repos.add(str(gate_repo))
    grant = asyncio.run(sv.scheduler.acquire_admin(repo, operation_type="doctor", transaction_id=None))
    path = f"/repos/{repo.repo_id}/metadata"
    try:
        assert invoke(sv, routes, fake_host, "PUT", path, {"archived": True})[0] == 409
        assert not sv.repos.get(repo.repo_id).archived
        assert invoke(sv, routes, fake_host, "PUT", path, {"label": "Live work"})[0] == 200
    finally:
        asyncio.run(sv.scheduler.release(grant))


def test_directory_picker_lists_only_directories_without_registration(sv, routes, fake_host, tmp_path):
    root = tmp_path / "choose"
    for name in ("repo two", "repo one", "repo one/nested"):
        (root / name).mkdir(parents=True)
    (root / "a-file.txt").write_text("private content")
    status, body = invoke(sv, routes, fake_host, "POST", "/directories", {"path": str(root)})
    assert status == 200
    assert (body["path"], body["parent"]) == (str(root.resolve()), str(root.parent.resolve()))
    assert [d["name"] for d in body["directories"]] == ["repo one", "repo two"]
    assert all(set(d) == {"name", "path"} for d in body["directories"])
    assert sv.repos.list(include_archived=True) == [] and body["truncated"] is False


@pytest.mark.parametrize("value", ["relative", "\x00", 42, "/path/that/does/not/exist", "~no-such-aidlc-user"])
def test_directory_picker_refuses_bad_paths(sv, routes, fake_host, value):
    assert invoke(sv, routes, fake_host, "POST", "/directories", {"path": value})[0] == 400


def test_directory_picker_is_bounded(sv, routes, fake_host, tmp_path):
    for i in range(205):
        (tmp_path / "many" / f"d{i:03}").mkdir(parents=True)
    status, body = invoke(sv, routes, fake_host, "POST", "/directories", {"path": str(tmp_path / "many")})
    assert status == 200 and len(body["directories"]) == 200 and body["truncated"] is True
