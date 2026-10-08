"""The route table and the authorisation gate (§1.24, §2.10), tested as properties over the whole table.

1. The table is exactly §2.10, in order: the host tries parameterised routes in registration order.
2. Every route is authenticated and every mutation is owner-only, proved from the wrapped handlers' markers.
3. Every mutation refuses an app token, even one whose manifest scope covers the path.
4. No unknown error code can leak: an unexpected exception is ``500 internal_error`` with a fixed message.

The harness lives here and the other handler test files import it.
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

#: ``GET /events`` never returns on its own (a live SSE stream), so the whole-table sweeps skip it and
#: ``test_handlers_misc.py`` exercises it with a bounded reader.
STREAMING = (("GET", "/events"),)


# ---- harness (shared with the other handler test files) ---- #


@pytest.fixture
def sv(studio, fake_ctx, fake_host, clock, ids, fake_bun, fake_git):
    """A real ``Services`` over the fake host, store open, reconciler NOT running; only host/bun/git are fakes."""
    services = studio.services.Services.build(
        fake_ctx, clock=clock, ids=ids, bun_path=fake_bun, git_path=fake_git, app_root=APP_ROOT, boot_id=BOOT,
        # Never shells out: `fake_bun` answers `--version` with a policy refusal another test may assert on.
        bun_version=lambda path: "1.1.42",
    )
    services.storage.open()
    services.settings.load()
    services.host.attach(fake_host)
    studio.services._REGISTRY[fake_ctx.name] = services
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
    """Route exactly as ``kiro_crew.apps.route_registry`` does (exact match, then params), so order is exercised."""
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


@pytest.fixture
def ocall(sv, routes, fake_host):
    """``ocall(method, path, **request_kwargs)`` dispatches as the owner and returns ``(status, body)``."""
    def go(method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        return call(sv, routes, method, path, CT.owner_request(method, path, host=fake_host, **kwargs))

    return go


# ---- the table ---- #


def test_all_routes_is_exactly_the_contract_table_in_order(routes, common):
    assert [(entry.method, entry.path) for entry in routes] == list(common.ROUTE_ORDER)


def test_every_route_is_registered_exactly_once(routes):
    pairs = [(entry.method, entry.path) for entry in routes]
    assert len(pairs) == len(set(pairs))


def test_static_paths_are_registered_before_the_parameterised_ones_that_would_shadow_them(routes):
    order = [(entry.method, entry.path) for entry in routes]
    intent = order.index(("GET", "/repos/{repo_id}/intents/{intent}"))
    assert order.index(("POST", "/repos/preflight")) < order.index(("GET", "/repos/{repo_id}"))
    assert order.index(("POST", "/repos/{repo_id}/intents/plan/preview")) < intent
    # `plan` is a valid intent-directory spelling, so the card-less advisor route has the same hazard.
    assert order.index(("POST", "/repos/{repo_id}/intents/plan/advise")) < intent


def test_no_route_collides_with_a_host_owned_app_management_path(routes):
    reserved = {"config", "manifest", "enable", "disable", "update", "uninstall", "open", "dev", "token",
                "migrate-cleanup"}
    for entry in routes:
        assert entry.path.strip("/").split("/")[0] not in reserved, entry.path


def test_every_handler_carries_the_authenticated_marker(routes):
    for entry in routes:
        assert getattr(entry.handler, "__kirocrew_authenticated__", False) is True, entry.path


def test_every_mutation_carries_the_owner_only_marker_and_no_read_does(routes):
    for entry in routes:
        owner_only = getattr(entry.handler, "__kirocrew_owner_only__", None)
        assert owner_only is (entry.method in MUTATING), f"{entry.method} {entry.path}"


# ---- the gate ---- #


def test_anonymous_is_401_on_a_read(sv, routes, fake_host):
    status, body = call(sv, routes, "GET", "/health", CT.anon_request("GET", "/health", host=fake_host))
    assert status == 401 and body["code"] == "unauthorized"


def test_an_internal_secret_header_alone_is_still_401(sv, routes, fake_host):
    """There is no internal-secret auth mode (review R09): the header authorises nothing here."""
    request = CT.internal_request("POST", "/actions/a_1/submit", host=fake_host,
                                  body={"payload": {"decision": "approve"}})
    status, body = call(sv, routes, "POST", "/actions/a_1/submit", request)
    assert status == 401 and body["code"] == "unauthorized"


@pytest.mark.parametrize("method,path", [
    ("PUT", "/settings"), ("POST", "/actions/a_0000000000000001/submit"),
    ("POST", "/actions/a_0000000000000001/delivery"), ("POST", "/advisor/draft"), ("POST", "/repos"),
    ("POST", "/repos/preflight"), ("DELETE", "/repos/r_000000000001"), ("POST", "/repos/r_000000000001/rescan"),
    ("POST", "/repos/r_000000000001/transactions"), ("POST", "/repos/r_000000000001/intents"),
    ("POST", "/repos/r_000000000001/intents/plan/advise"), ("POST", "/repos/r_000000000001/intents/demo/run"),
    ("POST", "/repos/r_000000000001/intents/demo/session"),
])
def test_a_non_owner_gets_403_owner_required_on_every_mutation(sv, routes, fake_host, method, path):
    status, body = call(sv, routes, method, path, CT.user_request(method, path, host=fake_host, body={}))
    assert status == 403 and body["code"] == "owner_required", path


@pytest.mark.parametrize("method,path", [
    ("PUT", "/settings"), ("POST", "/actions/a_0000000000000001/resolve"), ("POST", "/advisor/draft"),
    ("POST", "/repos"), ("DELETE", "/repos/r_000000000001"), ("POST", "/repos/r_000000000001/doctor"),
    ("POST", "/repos/r_000000000001/transactions/preview"), ("POST", "/repos/r_000000000001/intents/plan/advise"),
    ("POST", "/repos/r_000000000001/intents/demo/force-stop"), ("POST", "/repos/r_000000000001/intents/demo/archive"),
])
def test_an_app_token_gets_403_app_token_forbidden_on_every_mutation(sv, routes, fake_host, method, path):
    status, body = call(sv, routes, method, path, CT.app_request(method, path, host=fake_host, body={}))
    assert status == 403 and body["code"] == "app_token_forbidden", path


def test_every_mutation_in_the_table_refuses_an_app_token(sv, routes, fake_host):
    """The parametrised cases above are examples; this one is the property, over the whole table."""
    for entry in routes:
        if entry.method not in MUTATING:
            continue
        path = _concrete(entry.path)
        status, body = call(sv, routes, entry.method, path, CT.app_request(entry.method, path, host=fake_host, body={}))
        assert status == 403 and body["code"] == "app_token_forbidden", entry.path


def test_reads_accept_an_app_token(sv, routes, fake_host):
    status, body = call(sv, routes, "GET", "/health", CT.app_request("GET", "/health", host=fake_host))
    assert status == 200 and body["app"] == "aidlc-studio"
    status, _ = call(sv, routes, "GET", "/settings", CT.app_request("GET", "/settings", host=fake_host))
    assert status == 200


def test_the_owner_is_let_through(ocall):
    status, body = ocall("GET", "/repos")
    assert status == 200 and body["repos"] == []


def test_a_user_who_is_not_the_configured_owner_can_still_read(sv, routes, fake_host):
    status, _ = call(sv, routes, "GET", "/repos", CT.user_request("GET", "/repos", host=fake_host))
    assert status == 200


# ---- error mapping ---- #


def test_a_studio_error_becomes_its_own_code_and_status(ocall):
    status, body = ocall("GET", "/repos/r_nope", match_info={"repo_id": "r_nope"})
    assert status == 404
    assert body == {"error": body["error"], "code": "repo_not_found", "details": body["details"]}
    assert set(body) == {"error", "code", "details"}


def test_an_unknown_exception_becomes_500_internal_error_and_leaks_nothing(sv, ocall, monkeypatch, caplog):
    secret = "/Users/someone/private/repo and a token sk-abcdef"

    def explode(*_a, **_k):
        raise RuntimeError(secret)

    monkeypatch.setattr(sv.storage, "pragmas", explode)
    monkeypatch.setattr(sv, "health", explode)
    with caplog.at_level("ERROR"):
        status, body = ocall("GET", "/health")
    assert status == 500
    assert body == {"error": "internal error", "code": "internal_error", "details": {}}
    assert secret not in json.dumps(body)
    # The traceback belongs in the operator's log, where only they can read it.
    assert any(secret in record.getMessage() or secret in str(record.exc_info) for record in caplog.records)


def test_no_route_can_answer_with_a_code_outside_the_table(routes, ocall, studio):
    """Every code a handler can produce has an HTTP status, so a 5xx is never a lookup miss."""
    known = set(studio.errors.ERROR_CODES)
    for entry in routes:
        if (entry.method, entry.path) in STREAMING:
            continue
        path = _concrete(entry.path)
        status, body = ocall(entry.method, path, body={})
        if status >= 400 and isinstance(body, dict):
            assert body["code"] in known, f"{entry.method} {path} -> {body}"
            assert set(body) == {"error", "code", "details"}
            if status == 500:
                assert body["code"] == "internal_error"


# ---- helpers ---- #

_PLACEHOLDERS = {"repo_id": "r_000000000001", "intent": "260904-demo", "action_id": "a_0000000000000001",
                 "draft_id": "d_0000000000000001", "transaction_id": "tx_0000000000000001", "artifact_id": "0" * 40}


def _concrete(path: str) -> str:
    out = path
    for name, value in _PLACEHOLDERS.items():
        out = out.replace(f"{{{name}}}", value)
    assert "{" not in out, path
    return out
