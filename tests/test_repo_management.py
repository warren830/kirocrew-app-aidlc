"""Reversible registry metadata and explicit, bounded gateway directory selection."""
import asyncio
from pathlib import Path

import pytest

import conftest as CT
from test_handlers_auth import call, common, routes, sv  # noqa: F401


def invoke(sv, routes, host, method, path, body=None, **kwargs):
    return call(sv, routes, method, path,
                CT.owner_request(method, path, host=host, body=body, **kwargs))


def test_rename_archive_restore_preserve_identity_history_and_disk(sv, routes, fake_host, gate_repo):
    repo = sv.repos.add(str(gate_repo), "before")
    before = {str(p.relative_to(gate_repo)): p.read_bytes()
              for p in gate_repo.rglob("*") if p.is_file()}
    path = f"/repos/{repo.repo_id}/metadata"
    status, body = invoke(sv, routes, fake_host, "PUT", path,
                          {"label": " New label ", "archived": True})
    assert status == 200
    assert body["repo"]["label"] == "New label"
    assert body["repo"]["archived"] is True
    assert sv.repos.list() == []
    archived = sv.repos.list(include_archived=True)
    assert len(archived) == 1
    assert archived[0].resolved_identity == repo.resolved_identity
    assert archived[0].canonical_path == repo.canonical_path
    status, _ = invoke(sv, routes, fake_host, "PUT", path, {"archived": False})
    assert status == 200
    assert sv.repos.list()[0].repo_id == repo.repo_id
    assert sv.repos.list()[0].label == "New label"
    assert before == {str(p.relative_to(gate_repo)): p.read_bytes()
                      for p in gate_repo.rglob("*") if p.is_file()}


@pytest.mark.parametrize("body", [{}, {"label": ""}, {"label": 1}, {"archived": "yes"},
                                      {"canonical_path": "/tmp/other"}, {"label": "a\x00b"}])
def test_metadata_rejects_invalid_or_identity_changes(sv, routes, fake_host, gate_repo, body):
    repo = sv.repos.add(str(gate_repo), "original")
    status, _ = invoke(sv, routes, fake_host, "PUT", f"/repos/{repo.repo_id}/metadata", body)
    assert status == 400
    assert sv.repos.get(repo.repo_id).label == "original"
    assert not sv.repos.get(repo.repo_id).archived


def test_archive_refuses_live_lease_but_rename_is_available(sv, routes, fake_host, gate_repo):
    repo = sv.repos.add(str(gate_repo))
    grant = asyncio.run(sv.scheduler.acquire_admin(repo, operation_type="doctor", transaction_id=None))
    path = f"/repos/{repo.repo_id}/metadata"
    try:
        status, _ = invoke(sv, routes, fake_host, "PUT", path, {"archived": True})
        assert status == 409
        assert not sv.repos.get(repo.repo_id).archived
        status, _ = invoke(sv, routes, fake_host, "PUT", path, {"label": "Live work"})
        assert status == 200
    finally:
        asyncio.run(sv.scheduler.release(grant))


def test_directory_picker_lists_only_directories_without_registration(sv, routes, fake_host, tmp_path):
    root = tmp_path / "choose"
    root.mkdir()
    (root / "repo two").mkdir()
    (root / "repo one").mkdir()
    (root / "a-file.txt").write_text("private content")
    (root / "repo one" / "nested").mkdir()
    status, body = invoke(sv, routes, fake_host, "POST", "/directories", {"path": str(root)})
    assert status == 200
    assert body["path"] == str(root.resolve())
    assert body["parent"] == str(root.parent.resolve())
    assert [d["name"] for d in body["directories"]] == ["repo one", "repo two"]
    assert all(set(d) == {"name", "path"} for d in body["directories"])
    assert sv.repos.list(include_archived=True) == []
    assert body["truncated"] is False


@pytest.mark.parametrize("value", ["relative", "\x00", 42, "/path/that/does/not/exist", "~no-such-aidlc-user"])
def test_directory_picker_refuses_bad_paths(sv, routes, fake_host, value):
    status, _ = invoke(sv, routes, fake_host, "POST", "/directories", {"path": value})
    assert status == 400


def test_directory_picker_is_bounded(sv, routes, fake_host, tmp_path):
    root = tmp_path / "many"
    root.mkdir()
    for i in range(205):
        (root / f"d{i:03}").mkdir()
    status, body = invoke(sv, routes, fake_host, "POST", "/directories", {"path": str(root)})
    assert status == 200
    assert len(body["directories"]) == 200
    assert body["truncated"] is True
