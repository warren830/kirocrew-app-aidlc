"""Application-level bun selection must persist and update every consumer without a restart."""

import pytest

import conftest as CT
from test_handlers_auth import APP_ROOT, call, common, routes, sv  # noqa: F401


def request(sv, routes, host, method, path, body=None):
    return call(sv, routes, method, path, CT.owner_request(method, path, host=host, body=body))


def executable(path, script="#!/bin/sh\nexit 0\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script)
    path.chmod(0o755)
    return path


def test_custom_bun_updates_health_runner_preflight_and_persists(sv, routes, fake_host, tmp_path, repo_builder):
    binary = executable(tmp_path / "custom location" / "bun", "#!/bin/sh\nprintf '1.3.0\\n'\n")
    status, body = request(sv, routes, fake_host, "PUT", "/tools/bun", {"path": str(binary)})
    assert status == 200, body
    assert body["tool"]["path"] == body["tool"]["configured_path"] == str(binary)
    assert sv.engine.bun_path == str(binary)
    assert sv.health()["tools"]["bun"]["path"] == str(binary)
    root = repo_builder.with_git().build()
    status, body = request(sv, routes, fake_host, "POST", "/repos/preflight", {"path": str(root)})
    assert status == 200, body
    assert body["preflight"]["bun"]["path"] == str(binary)
    assert sv.storage.pref_get("runtime.bun") == {"path": str(binary)}


@pytest.mark.parametrize("path", ["relative/bun", "/missing/bun", 3, "", "/tmp/bun\0suffix"])
def test_invalid_bun_never_replaces_working_configuration(sv, routes, fake_host, path):
    previous = sv.engine.bun_path
    status, body = request(sv, routes, fake_host, "PUT", "/tools/bun", {"path": path})
    assert status in (400, 503), body
    assert sv.engine.bun_path == previous and sv.storage.pref_get("runtime.bun") is None


def test_reprobe_discovers_new_install_without_restart(sv, routes, fake_host, monkeypatch, tmp_path, studio):
    new = str(tmp_path / "new-bun")
    discovered = studio.engine.BunLocation(path=new, version="1.3.1", source="install_location", searched=(new,))
    monkeypatch.setattr(studio.services, "find_bun", lambda **_: discovered)
    status, body = request(sv, routes, fake_host, "POST", "/tools/bun/probe", {})
    assert status == 200, body
    assert body["tool"]["path"] == sv.engine.bun_path == discovered.path
    assert sv.health()["tools"]["bun"]["version"] == "1.3.1"


def test_reprobe_missing_configured_binary_reports_failure_and_can_clear(sv, routes, fake_host, tmp_path, studio,
                                                                         monkeypatch):
    binary = executable(tmp_path / "bun")
    assert request(sv, routes, fake_host, "PUT", "/tools/bun", {"path": str(binary)})[0] == 200
    binary.unlink()
    status, body = request(sv, routes, fake_host, "POST", "/tools/bun/probe", {})
    assert status == 200, body
    assert body["tool"]["found"] is False and body["tool"]["configured_path"] == str(binary)
    assert sv.engine.bun_path is None
    found = studio.engine.BunLocation("/another/bun", "1.2.0", "path", ("/another/bun",))
    monkeypatch.setattr(studio.services, "find_bun", lambda **_: found)
    status, body = request(sv, routes, fake_host, "PUT", "/tools/bun", {"path": None})
    assert status == 200, body
    assert body["tool"]["configured_path"] is None and sv.engine.bun_path == "/another/bun"


def test_bun_configuration_is_owner_only(sv, routes, fake_host, tmp_path):
    for method, path in (("PUT", "/tools/bun"), ("POST", "/tools/bun/probe")):
        app_request = CT.app_request(method, path, host=fake_host, body={"path": str(tmp_path / "bun")})
        assert call(sv, routes, method, path, app_request)[0] == 403


def test_bun_startup_loads_saved_configuration(sv, routes, fake_host, tmp_path):
    binary = executable(tmp_path / "bun")
    sv.storage.pref_set("runtime.bun", {"path": str(binary)})
    sv.restore_bun_configuration()  # the same startup seam must restore both runtime and preflight consumers
    assert sv.engine.bun_path == str(binary)
    assert sv.health()["tools"]["bun"]["configured_path"] == str(binary)
