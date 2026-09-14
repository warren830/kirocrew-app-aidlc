"""The route table and the authorisation gate (§1.24, §2.10).

This file is the one that would catch a whole class of quiet disasters, so it tests properties rather
than examples:

1. **The table is exactly §2.10, in order.** Registration order is not cosmetic — the host tries
   parameterised routes in the order they were registered, so ``/repos/preflight`` after
   ``/repos/{repo_id}`` would make "add a repository" resolve to "show repository ``preflight``".
2. **Every route is authenticated and every mutation is owner-only**, proved from the markers on the
   wrapped handlers rather than from a sample of requests: a new route that forgets the flag fails here
   the day it is added.
3. **Every mutation refuses an app token**, even one whose manifest scope covers the path. A token is
   an application, and no application decides for a human.
4. **No unknown error code can leak.** A handler that raises something unexpected produces
   ``500 {"code": "internal_error"}`` with a fixed message, because the exception text may quote a path,
   a row, or a credential.

The harness lives here and the other four ``test_handlers_*`` files import it; ``conftest.py`` does not
provide a ``services`` fixture yet (reported to the orchestrator).
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest

import conftest as CT

APP_ROOT = Path(__file__).resolve().parent.parent
BOOT = "boot-test-0001"
OWNER = "owner-1"

#: Methods that change something. Everything here must be owner-only and must refuse an app token.
MUTATING = ("POST", "PUT", "DELETE", "PATCH")

#: ``GET /events`` is the one route that never returns on its own: it is a live SSE stream, so the
#: whole-table sweeps below skip it and ``test_handlers_misc.py`` exercises it with a bounded reader.
STREAMING = (("GET", "/events"),)


# --------------------------------------------------------------------------- #
# harness (shared with the other handler test files)
# --------------------------------------------------------------------------- #


@pytest.fixture
def sv(studio, fake_ctx, fake_host, clock, ids, fake_bun, fake_git):
    """A real ``Services`` over the fake host, with the store open and the reconciler NOT running.

    Everything is real except the host, ``bun``, ``git`` and the ``bun --version`` probe: a handler test
    that stubbed a Studio module would stop proving that the route and the module agree, which is most
    of what these files are for.
    """
    services = studio.services.Services.build(
        fake_ctx,
        clock=clock,
        ids=ids,
        bun_path=fake_bun,
        git_path=fake_git,
        app_root=APP_ROOT,
        boot_id=BOOT,
        # Never shells out: `fake_bun` answers `--version` with a policy refusal, and a handler test must
        # not be the reason a denied-invocation assertion elsewhere finds a line in the log.
        bun_version=lambda path: "1.1.42",
    )
    services.storage.open()
    services.settings.load()
    services.host.attach(fake_host)
    studio.services.register(services)
    yield services
    studio.services.discard(fake_ctx.name)
    services.storage.close()


@pytest.fixture(scope="session")
def common():
    """``handlers.common``. Not on the ``studio`` fixture: it globs ``studio/*.py`` and skips packages."""
    import importlib

    CT.load_backend()
    return importlib.import_module(f"{CT.STUDIO_NS}.studio.handlers.common")


@pytest.fixture
def routes(sv, common):
    return common.all_routes(sv)


def run(coro):
    return asyncio.run(coro)


def dispatch(routes: list[Any], method: str, path: str, request: Any) -> tuple[int, Any]:
    """Route a request exactly as ``kiro_crew.apps.route_registry`` does: exact match, then params.

    Reimplemented here rather than mocked so the two ordering constraints of §2.10 are actually
    exercised: a table in the wrong order fails these tests for the same reason it would fail in the
    gateway.
    """
    for entry in routes:
        if entry.method == method.upper() and entry.path == path and "{" not in entry.path:
            return _call(entry, request)
    for entry in routes:
        if entry.method != method.upper() or "{" not in entry.path:
            continue
        pattern, names = _compile(entry.path)
        match = pattern.match(path)
        if match:
            for name, value in zip(names, match.groups()):
                request.match_info[name] = value
            return _call(entry, request)
    return 404, {"error": "not found"}


def _compile(path: str) -> tuple[re.Pattern[str], list[str]]:
    names: list[str] = []
    parts: list[str] = []
    last = 0
    for match in re.finditer(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", path):
        parts.append(re.escape(path[last : match.start()]))
        names.append(match.group(1))
        parts.append(r"([^/]+)")
        last = match.end()
    parts.append(re.escape(path[last:]))
    return re.compile("^" + "".join(parts) + "$"), names


def _call(entry: Any, request: Any) -> tuple[int, Any]:
    response = run(entry.handler(request, request.app.get("ctx")))
    body: Any = None
    raw = getattr(response, "body", None)
    if raw is not None:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
        try:
            body = json.loads(text)
        except ValueError:
            body = text
    return response.status, body


def call(sv: Any, routes: list[Any], method: str, path: str, request: Any) -> tuple[int, Any]:
    request.app["ctx"] = sv.ctx
    return dispatch(routes, method, path, request)


def owner(method: str, path: str, host: Any, **kwargs: Any) -> Any:
    return CT.owner_request(method, path, host=host, **kwargs)


# --------------------------------------------------------------------------- #
# the table
# --------------------------------------------------------------------------- #


def test_all_routes_is_exactly_the_contract_table_in_order(routes, common):
    assert [(entry.method, entry.path) for entry in routes] == list(common.ROUTE_ORDER)


def test_every_route_is_registered_exactly_once(routes):
    pairs = [(entry.method, entry.path) for entry in routes]
    assert len(pairs) == len(set(pairs))


def test_static_paths_are_registered_before_the_parameterised_ones_that_would_shadow_them(routes):
    order = [(entry.method, entry.path) for entry in routes]
    assert order.index(("POST", "/repos/preflight")) < order.index(("GET", "/repos/{repo_id}"))
    assert order.index(("POST", "/repos/{repo_id}/intents/plan/preview")) < order.index(
        ("GET", "/repos/{repo_id}/intents/{intent}")
    )
    # `plan` is a valid intent-directory spelling, so the card-less advisor route has the same hazard.
    assert order.index(("POST", "/repos/{repo_id}/intents/plan/advise")) < order.index(
        ("GET", "/repos/{repo_id}/intents/{intent}")
    )


def test_no_route_collides_with_a_host_owned_app_management_path(routes):
    reserved = {
        "config", "manifest", "enable", "disable", "update", "uninstall", "open", "dev", "token",
        "migrate-cleanup",
    }
    for entry in routes:
        first = entry.path.strip("/").split("/")[0]
        assert first not in reserved, entry.path


def test_every_handler_carries_the_authenticated_marker(routes):
    for entry in routes:
        assert getattr(entry.handler, "__kirocrew_authenticated__", False) is True, entry.path


def test_every_mutation_carries_the_owner_only_marker_and_no_read_does(routes):
    for entry in routes:
        owner_only = getattr(entry.handler, "__kirocrew_owner_only__", None)
        assert owner_only is (entry.method in MUTATING), f"{entry.method} {entry.path}"


def test_the_slack_callback_is_owner_only_like_every_other_mutation(routes):
    entry = next(e for e in routes if e.path == "/slack/actions/callback")
    assert entry.method == "POST"
    assert getattr(entry.handler, "__kirocrew_owner_only__") is True


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #


def test_anonymous_is_401_on_a_read(sv, routes, fake_host):
    status, body = call(sv, routes, "GET", "/health", CT.anon_request("GET", "/health", host=fake_host))
    assert status == 401 and body["code"] == "unauthorized"


def test_an_internal_secret_header_alone_is_still_401(sv, routes, fake_host):
    """There is no internal-secret auth mode (review R09): the header authorises nothing here."""
    request = CT.internal_request("POST", "/slack/actions/callback", host=fake_host,
                                  body={"action_id": "a_1"})
    status, body = call(sv, routes, "POST", "/slack/actions/callback", request)
    assert status == 401 and body["code"] == "unauthorized"


@pytest.mark.parametrize("method,path", [
    ("PUT", "/settings"),
    ("POST", "/calibration/clear"),
    ("POST", "/migration/apply"),
    ("POST", "/actions/a_0000000000000001/submit"),
    ("POST", "/actions/a_0000000000000001/delivery"),
    ("POST", "/advisor/draft"),
    ("POST", "/slack/actions/callback"),
    ("POST", "/repos"),
    ("POST", "/repos/preflight"),
    ("DELETE", "/repos/r_000000000001"),
    ("POST", "/repos/r_000000000001/rescan"),
    ("POST", "/repos/r_000000000001/install"),
    ("POST", "/repos/r_000000000001/intents"),
    ("POST", "/repos/r_000000000001/intents/plan/advise"),
    ("POST", "/repos/r_000000000001/intents/demo/run"),
    ("POST", "/repos/r_000000000001/intents/demo/keep-moving"),
    ("POST", "/repos/r_000000000001/intents/demo/session/bind"),
])
def test_a_non_owner_gets_403_owner_required_on_every_mutation(sv, routes, fake_host, method, path):
    request = CT.user_request(method, path, host=fake_host, body={})
    status, body = call(sv, routes, method, path, request)
    assert status == 403 and body["code"] == "owner_required", path


@pytest.mark.parametrize("method,path", [
    ("PUT", "/settings"),
    ("POST", "/calibration/clear"),
    ("POST", "/migration/preview"),
    ("POST", "/actions/a_0000000000000001/retry"),
    ("POST", "/advisor/draft"),
    ("POST", "/slack/actions/callback"),
    ("POST", "/repos"),
    ("DELETE", "/repos/r_000000000001"),
    ("POST", "/repos/r_000000000001/doctor"),
    ("POST", "/repos/r_000000000001/upgrade"),
    ("POST", "/repos/r_000000000001/intents/plan/advise"),
    ("POST", "/repos/r_000000000001/intents/demo/force-stop"),
    ("POST", "/repos/r_000000000001/intents/demo/archive"),
])
def test_an_app_token_gets_403_app_token_forbidden_on_every_mutation(sv, routes, fake_host, method, path):
    request = CT.app_request(method, path, host=fake_host, body={})
    status, body = call(sv, routes, method, path, request)
    assert status == 403 and body["code"] == "app_token_forbidden", path


def test_every_mutation_in_the_table_refuses_an_app_token(sv, routes, fake_host):
    """The parametrised cases above are examples; this one is the property, over the whole table."""
    for entry in routes:
        if entry.method not in MUTATING:
            continue
        path = _concrete(entry.path)
        request = CT.app_request(entry.method, path, host=fake_host, body={})
        status, body = call(sv, routes, entry.method, path, request)
        assert status == 403 and body["code"] == "app_token_forbidden", entry.path


def test_reads_accept_an_app_token(sv, routes, fake_host):
    status, body = call(
        sv, routes, "GET", "/health", CT.app_request("GET", "/health", host=fake_host)
    )
    assert status == 200 and body["app"] == "aidlc-studio"

    status, _ = call(
        sv, routes, "GET", "/settings", CT.app_request("GET", "/settings", host=fake_host)
    )
    assert status == 200


def test_the_owner_is_let_through(sv, routes, fake_host):
    status, body = call(
        sv, routes, "GET", "/leases", CT.owner_request("GET", "/leases", host=fake_host)
    )
    assert status == 200 and body["leases"] == []


def test_a_user_who_is_not_the_configured_owner_can_still_read(sv, routes, fake_host):
    status, _ = call(sv, routes, "GET", "/leases", CT.user_request("GET", "/leases", host=fake_host))
    assert status == 200


# --------------------------------------------------------------------------- #
# error mapping
# --------------------------------------------------------------------------- #


def test_a_studio_error_becomes_its_own_code_and_status(sv, routes, fake_host):
    request = CT.owner_request("GET", "/repos/r_nope", host=fake_host, match_info={"repo_id": "r_nope"})
    status, body = call(sv, routes, "GET", "/repos/r_nope", request)
    assert status == 404
    assert body == {"error": body["error"], "code": "repo_not_found", "details": body["details"]}
    assert set(body) == {"error", "code", "details"}


def test_an_unknown_exception_becomes_500_internal_error_and_leaks_nothing(sv, routes, fake_host,
                                                                          monkeypatch, caplog):
    secret = "/Users/someone/private/repo and a token sk-abcdef"

    def explode(*_a, **_k):
        raise RuntimeError(secret)

    monkeypatch.setattr(sv.storage, "pragmas", explode)
    monkeypatch.setattr(sv, "health", explode)
    with caplog.at_level("ERROR"):
        status, body = call(
            sv, routes, "GET", "/health", CT.owner_request("GET", "/health", host=fake_host)
        )
    assert status == 500
    assert body == {"error": "internal error", "code": "internal_error", "details": {}}
    assert secret not in json.dumps(body)
    # The traceback belongs in the operator's log, where only they can read it.
    assert any(secret in record.getMessage() or secret in str(record.exc_info) for record in caplog.records)


def test_no_route_can_answer_with_a_code_outside_the_table(sv, routes, fake_host, studio):
    """Every code a handler can produce has an HTTP status, so a 5xx is never a lookup miss."""
    known = set(studio.errors.ERROR_CODES)
    for entry in routes:
        if (entry.method, entry.path) in STREAMING:
            continue
        path = _concrete(entry.path)
        request = CT.owner_request(entry.method, path, host=fake_host, body={})
        status, body = call(sv, routes, entry.method, path, request)
        if status >= 400 and isinstance(body, dict):
            assert body["code"] in known, f"{entry.method} {path} -> {body}"
            assert set(body) == {"error", "code", "details"}
            if status == 500:
                assert body["code"] == "internal_error"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

_PLACEHOLDERS = {
    "repo_id": "r_000000000001",
    "intent": "260904-demo",
    "action_id": "a_0000000000000001",
    "draft_id": "d_0000000000000001",
    "transaction_id": "tx_0000000000000001",
    "artifact_id": "0" * 40,
}


def _concrete(path: str) -> str:
    out = path
    for name, value in _PLACEHOLDERS.items():
        out = out.replace(f"{{{name}}}", value)
    assert "{" not in out, path
    return out
