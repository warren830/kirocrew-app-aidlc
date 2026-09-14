"""The route wrapper, the auth gate, the request/response plumbing, and the pinned route table.

Four decisions here shape every handler in this package.

* **No handler chooses an HTTP status.** They raise ``StudioError`` and this module maps the code
  through ``ERROR_CODES``, so two routes cannot answer the same failure differently and adding a code
  is one table edit.
* **No unknown exception reaches the client.** Anything that is not a ``StudioError`` becomes
  ``500 internal_error`` with a fixed message and a logged traceback: an ``OSError`` carrying an
  absolute path, or a ``sqlite3`` error quoting a row, would otherwise leak a user's filesystem into a
  response body.
* **The owner gate lives in the wrapper, not in the handlers.** Every mutation is owner-only by
  construction — the flag is recorded on the wrapped function so a test can prove it for every
  ``POST``/``PUT``/``DELETE`` rather than trusting sixty handlers to remember.
* **The route order is pinned as data.** ``ROUTE_ORDER`` is §2.10 verbatim and ``all_routes`` emits
  exactly it, refusing to register when a module offers a route the table does not list (or omits one
  it does). The host tries parameterised routes in registration order, so ``/repos/preflight`` before
  ``/repos/{repo_id}`` and ``…/intents/plan/preview`` before ``…/intents/{intent}`` are correctness
  requirements, not cosmetics.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import json
import logging
from typing import Any, Awaitable, Callable, Mapping, Sequence, TypeVar

from aiohttp import web

from .. import constants as C
from ..errors import ERROR_CODES, StudioError
from ..sessions import HostBridge

_LOG = logging.getLogger("kirocrew.app.aidlc-studio")

T = TypeVar("T")

#: What a handler module writes: it receives the container and the request. The ``AppRoute`` the host
#: sees carries the *wrapped* form (``(request, ctx)``), which is what ``route`` produces.
Handler = Callable[[Any, web.Request], Awaitable[web.Response]]

#: Default body cap. Feedback and answers are the largest legitimate bodies and both are capped at
#: 8 000 characters by ``constants``, so a megabyte is generous by three orders of magnitude.
MAX_BODY_BYTES = 1 << 20

_TRUE = ("1", "true")
_FALSE = ("0", "false")

#: The message a 500 carries. Fixed text: the exception's own message may quote a path, a row or a
#: credential, and the traceback goes to the log where only the operator can read it.
INTERNAL_ERROR_MESSAGE = "internal error"


# --------------------------------------------------------------------------- #
# responses
# --------------------------------------------------------------------------- #


def json_ok(data: Mapping[str, Any], status: int = 200) -> web.Response:
    """A JSON body with non-ASCII kept as-is.

    ``ensure_ascii=False`` because AI-DLC question text is routinely Chinese (FORMAT-NOTES §7):
    escaping it would triple the size of a questions payload and make a diff unreadable.
    """
    return web.json_response(
        data, status=status, dumps=lambda obj: json.dumps(obj, ensure_ascii=False, default=str)
    )


def json_error(err: StudioError) -> web.Response:
    """The one non-2xx shape (§0.4), with the status taken from the code table."""
    return json_ok(err.to_json(), status=ERROR_CODES.get(err.code, 500))


# --------------------------------------------------------------------------- #
# request parsing
# --------------------------------------------------------------------------- #


async def read_json(
    request: web.Request, *, required: Sequence[str] = (), max_bytes: int = MAX_BODY_BYTES
) -> dict:
    """The request body as a dict, or ``bad_body``.

    A missing body is an empty object rather than an error: most mutations take ``{}`` and the browser
    is free to send nothing at all. ``required`` is what turns that leniency off for the fields a
    decision cannot be inferred without.
    """
    length = request.content_length
    if length is not None and int(length) > int(max_bytes):
        raise StudioError(
            "bad_body", "request body is too large",
            details={"reason": "body_too_large", "limit": int(max_bytes)},
        )
    try:
        body = await request.json()
    except Exception:
        body = None
    if body is None:
        body = {}
    if not isinstance(body, Mapping):
        raise StudioError("bad_body", "request body must be a JSON object",
                          details={"reason": "not_an_object"})
    data = dict(body)
    missing = [name for name in required if data.get(name) in (None, "")]
    if missing:
        raise StudioError("bad_body", "request body is missing required fields",
                          details={"missing": missing})
    return data


def query_str(
    request: web.Request,
    name: str,
    default: str | None = None,
    *,
    pattern: Any | None = None,
    choices: Sequence[str] | None = None,
) -> str | None:
    """One query parameter, validated. An empty value is treated as absent."""
    raw = request.query.get(name)
    if raw is None or raw == "":
        return default
    if choices is not None and raw not in choices:
        raise StudioError("bad_param", f"{name} must be one of {', '.join(choices)}",
                          details={"param": name, "choices": list(choices)})
    if pattern is not None and not pattern.match(raw):
        raise StudioError("bad_param", f"{name} is malformed", details={"param": name})
    return raw


def query_int(
    request: web.Request,
    name: str,
    default: int | None = None,
    *,
    lo: int | None = None,
    hi: int | None = None,
) -> int | None:
    raw = request.query.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise StudioError("bad_param", f"{name} must be an integer",
                          details={"param": name}) from None
    if lo is not None and value < lo:
        raise StudioError("bad_param", f"{name} must be >= {lo}", details={"param": name, "min": lo})
    if hi is not None and value > hi:
        raise StudioError("bad_param", f"{name} must be <= {hi}", details={"param": name, "max": hi})
    return value


def query_bool(request: web.Request, name: str, default: bool = False) -> bool:
    """``1``/``true``/``0``/``false`` only.

    Deliberately not "anything non-empty is true": ``?include_archived=no`` reading as *true* is the
    kind of silent surprise that makes a user think Studio ignored them.
    """
    raw = request.query.get(name)
    if raw is None or raw == "":
        return default
    lowered = raw.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise StudioError("bad_param", f"{name} must be 1, 0, true or false", details={"param": name})


def parse_intent_key(raw: str) -> tuple[str, str]:
    """``intent_dir`` or ``space~intent_dir`` → ``(space, intent_dir)`` (§0.1, C02).

    Both halves are validated against the grammar, which is what makes the ``~`` split unambiguous:
    the separator cannot occur inside either name, so exactly one reading of the path exists.
    """
    text = str(raw or "")
    if C.INTENT_KEY_SEPARATOR in text:
        space, _, intent_dir = text.partition(C.INTENT_KEY_SEPARATOR)
    else:
        space, intent_dir = C.DEFAULT_SPACE, text
    if not C.SPACE_RE.match(space) or not C.INTENT_DIR_RE.match(intent_dir):
        raise StudioError("bad_param", "malformed intent key", details={"param": "intent", "value": text})
    return space, intent_dir


def encode_cursor(value: int) -> str:
    """``base64url(str(key))`` without padding — the same spelling ``Storage`` uses."""
    return base64.urlsafe_b64encode(str(int(value)).encode("ascii")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> int:
    raw = str(cursor or "").strip()
    try:
        padded = raw + "=" * (-len(raw) % 4)
        return int(base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii"))
    except Exception:
        raise StudioError("bad_param", "malformed cursor", details={"param": "cursor"}) from None


def paginate(
    items: Sequence[T], cursor: str | None, limit: int, key: Callable[[T], int]
) -> tuple[list[T], str | None]:
    """One page of a descending-by-``key`` sequence, plus the cursor for the next.

    Keyed on an integer rather than on a timestamp: Studio's stamps are second-precision, so several
    rows routinely share one and a time cursor would either repeat or skip them.
    """
    bounded = max(1, int(limit))
    rows = list(items)
    if cursor:
        after = decode_cursor(cursor)
        rows = [row for row in rows if key(row) < after]
    page = rows[:bounded]
    next_cursor = encode_cursor(key(page[-1])) if len(rows) > bounded and page else None
    return page, next_cursor


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #

#: A detached bridge is enough for the owner question: the host's own predicate reads
#: ``request.app["state"]``, so it answers correctly without the bridge holding a host reference. It
#: exists so this module never names ``is_owner_dashboard_request`` itself — one place resolves host
#: symbols, and that place is ``sessions.py``.
_FALLBACK_HOST: HostBridge | None = None


def _fallback_host() -> HostBridge:
    global _FALLBACK_HOST
    if _FALLBACK_HOST is None:
        from ..services import SystemClock

        _FALLBACK_HOST = HostBridge(SystemClock(), _LOG)
    return _FALLBACK_HOST


def require_user(request: web.Request) -> str:
    """The authenticated subject, or ``401``.

    The gateway's middleware has already decided who is asking; a handler that reached this point with
    no subject is being called outside that middleware, which is exactly when refusing matters.
    """
    user = request.get("user")
    if user is None or str(user) == "":
        raise StudioError("unauthorized", "authentication required")
    return str(user)


def require_owner(request: web.Request, host: Any = None) -> str:
    """The dashboard owner, or ``403``. Every mutation goes through here.

    Three refusals in a fixed order, because the useful answer is the most specific one: not
    authenticated at all, then authenticated *as an app* (a token can never mutate, whatever its
    manifest scope says), then authenticated as a different human.
    """
    user = require_user(request)
    if str(request.get("app", "") or "") != "":
        raise StudioError(
            "app_token_forbidden",
            "app tokens cannot perform this operation",
            details={"app": str(request.get("app"))},
        )
    bridge = host if host is not None else _fallback_host()
    if not bridge.is_owner_request(request):
        raise StudioError("owner_required", "only the dashboard owner can perform this operation")
    return user


# --------------------------------------------------------------------------- #
# the wrapper
# --------------------------------------------------------------------------- #


def route(
    method: str,
    path: str,
    handler: Handler,
    *,
    owner: bool = False,
    services: Any = None,
) -> Any:
    """Wrap ``handler`` into the ``AppRoute`` the host registers.

    Order inside the wrapper is the contract's: attach the host first (it is how the owner check and
    every session read find the gateway — C08), then authenticate, then run the handler, then map
    failures. Attaching before authenticating matters because the owner predicate reads the attached
    state; authenticating before running matters because a handler must never observe an anonymous
    caller.

    ``services`` is additive to §1.24: ``all_routes`` passes the container it was given so the wrapper
    cannot end up talking to a second one. Left out, the wrapper resolves the process container from
    the context, which is what a hand-registered route in a dev harness needs.
    """
    from kiro_crew.apps.route_registry import AppRoute

    async def wrapped(request: web.Request, ctx: Any) -> web.Response:
        svc = services
        if svc is None:
            from .. import services as services_module

            svc = services_module.get_or_build(ctx)
        try:
            svc.attach_host(request.app.get("state"))
            if owner:
                require_owner(request, svc.host)
            else:
                require_user(request)
            return await handler(svc, request)
        except StudioError as exc:
            return json_error(exc)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception("aidlc-studio: unhandled error in %s %s", method, path)
            return json_ok(
                {"error": INTERNAL_ERROR_MESSAGE, "code": "internal_error", "details": {}}, status=500
            )

    functools.update_wrapper(wrapped, handler)
    wrapped.__kirocrew_authenticated__ = True  # type: ignore[attr-defined]
    wrapped.__kirocrew_owner_only__ = bool(owner)  # type: ignore[attr-defined]
    return AppRoute(method=method.upper(), path=path, handler=wrapped)


# --------------------------------------------------------------------------- #
# shared lookups
# --------------------------------------------------------------------------- #


async def repo_or_404(services: Any, repo_id: str) -> Any:
    """The registry row, or ``repo_not_found``."""
    return await asyncio.to_thread(services.repos.get, str(repo_id))


async def snapshot_or_404(services: Any, repo: Any, intent_key: str) -> Any:
    """One coherent read of the intent named by an ``intent_key``.

    ``repo_unavailable`` comes first: a moved or unreadable repository produces an *empty* snapshot,
    and reporting that as ``intent_not_found`` would tell the user their intent is gone when the
    directory is merely somewhere else.
    """
    space, intent_dir = parse_intent_key(intent_key)
    if str(C.attr(repo, "availability", "")) != "available":
        raise StudioError(
            "repo_unavailable",
            "this repository cannot be read right now",
            details={
                "repo_id": C.attr(repo, "repo_id"),
                "availability": C.attr(repo, "availability"),
                "detail": C.attr(repo, "availability_detail"),
            },
        )
    snap = await asyncio.to_thread(services.projection.snapshot, repo, space, intent_dir)
    if snap.row is None and snap.state_snap is None:
        raise StudioError(
            "intent_not_found",
            "no such intent in this repository",
            details={"intent_key": C.intent_key_for(space, intent_dir)},
        )
    return snap


async def breakers_for(services: Any, repo_id: str, intent_key: str) -> list[dict]:
    """Breaker rows for one intent. Same prefix rule the reconciler uses (§0.1 breaker key)."""
    prefix = f"{repo_id}:{intent_key}:"
    rows = await asyncio.to_thread(services.storage.select, "breakers")
    return [row for row in rows if str(row.get("key") or "").startswith(prefix)]


async def intent_view(
    services: Any,
    repo: Any,
    snap: Any,
    *,
    install: Any = None,
    leases: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Everything a read route needs about one intent, without writing anything.

    The reconciler assembles the same bundle and then *persists* what it derived (cards, boundary,
    events). A read route must not: a GET that minted an action card would make the queue depend on who
    was looking at it. So this mirrors the reconciler's assembly and stops before its writes.
    """
    from ..consistency import RepoFacts

    repo_id = str(C.attr(repo, "repo_id"))
    binding = await services.sessions.get(repo_id, snap.space, snap.intent_dir)
    slot = services.host.slot(binding.slot_key) if binding.slot_key else None
    live = await services.actions.list_live(
        repo_id=repo_id, space=snap.space, intent_dir=snap.intent_dir, include_revision=True
    )
    breakers = await breakers_for(services, repo_id, snap.intent_key)
    if install is None:
        install = await asyncio.to_thread(services.repos.detect_install, repo)
    if leases is None:
        leases = await services.scheduler.list()
    facts = RepoFacts(
        now=services.clock.iso(),
        repo=repo,
        install=install,
        binding=binding,
        slot=slot,
        live_actions=live,
        leases=list(leases),
        first_unreadable_at=snap.first_unreadable_at,
        layout=snap.layout,
    )
    findings = await asyncio.to_thread(services.consistency.evaluate_intent, snap, facts)
    return {
        "binding": binding,
        "slot": slot,
        "live_actions": live,
        "breakers": breakers,
        "breaker_open": any(row.get("opened_at") for row in breakers),
        "install": install,
        "leases": list(leases),
        "findings": findings,
        "session": await services.sessions.session_ref(binding),
    }


async def summarize_intent(services: Any, repo: Any, snap: Any, view: Mapping[str, Any]) -> Any:
    """``IntentSummary`` from an ``intent_view`` bundle."""
    return await asyncio.to_thread(
        functools.partial(
            services.projection.summarize,
            snap,
            binding=view["binding"],
            live_actions=[a for a in view["live_actions"] if C.attr(a, "type") != "revision"],
            breaker_open=view["breaker_open"],
            host=view["session"],
            findings=view["findings"],
            slot=view["slot"],
        )
    )


async def cards_for(services: Any, records: Sequence[Any]) -> list[dict[str, Any]]:
    """``ActionCard`` JSON for a batch of records, snapshotting each intent at most once.

    The snapshot cache is what keeps ``GET /actions`` from re-reading one intent's state file once per
    card: a repository waiting at three boundaries would otherwise pay for three identical reads.
    """
    cache: dict[tuple[str, str, str], Any] = {}
    repos: dict[str, Any] = {}
    out: list[dict[str, Any]] = []
    for rec in records:
        key = (rec.repo_id, rec.space, rec.intent_dir)
        if key not in cache:
            snap = None
            try:
                repo = repos.get(rec.repo_id)
                if repo is None:
                    repo = await repo_or_404(services, rec.repo_id)
                    repos[rec.repo_id] = repo
                if str(C.attr(repo, "availability", "")) == "available":
                    snap = await asyncio.to_thread(
                        services.projection.snapshot, repo, rec.space, rec.intent_dir
                    )
            except StudioError:
                # A card outlives its repository row (a delete cascades, a moved repo cannot be read).
                # The stored evidence is still the record of what a human decided, so the card is
                # rendered from it rather than dropped.
                snap = None
            cache[key] = snap
        out.append(await services.actions.card(rec, cache[key]))
    return out


# --------------------------------------------------------------------------- #
# the route table (§2.10, verbatim and in order)
# --------------------------------------------------------------------------- #

_INTENT = "/repos/{repo_id}/intents/{intent}"

ROUTE_ORDER: tuple[tuple[str, str], ...] = (
    ("GET", "/health"),
    ("GET", "/payload"),
    ("GET", "/leases"),
    ("GET", "/diagnostics"),
    ("GET", "/settings"),
    ("PUT", "/settings"),
    ("GET", "/tools/bun"),
    ("PUT", "/tools/bun"),
    ("POST", "/tools/bun/probe"),
    ("GET", "/calibration"),
    ("POST", "/calibration/clear"),
    ("GET", "/migration/status"),
    ("POST", "/migration/preview"),
    ("POST", "/migration/apply"),
    ("GET", "/events"),
    ("GET", "/events/poll"),
    ("GET", "/activity"),
    ("GET", "/actions"),
    ("GET", "/actions/{action_id}"),
    ("POST", "/actions/{action_id}/submit"),
    ("POST", "/actions/{action_id}/delivery"),
    ("POST", "/actions/{action_id}/retry"),
    ("POST", "/actions/{action_id}/reconcile"),
    ("POST", "/actions/{action_id}/cancel"),
    ("POST", "/actions/{action_id}/resolve"),
    ("POST", "/advisor/draft"),
    ("GET", "/advisor/drafts/{draft_id}"),
    ("POST", "/slack/actions/callback"),
    ("GET", "/repos"),
    ("POST", "/repos"),
    ("POST", "/repos/preflight"),
    ("POST", "/directories"),
    ("GET", "/repos/{repo_id}"),
    ("PUT", "/repos/{repo_id}/metadata"),
    ("GET", "/repos/{repo_id}/spaces"),
    ("POST", "/repos/{repo_id}/spaces"),
    ("POST", "/repos/{repo_id}/spaces/switch"),
    ("POST", "/repos/{repo_id}/maintenance/preview"),
    ("POST", "/repos/{repo_id}/maintenance/cleanup"),
    ("DELETE", "/repos/{repo_id}"),
    ("POST", "/repos/{repo_id}/rebind"),
    ("POST", "/repos/{repo_id}/rescan"),
    ("POST", "/repos/{repo_id}/doctor"),
    ("POST", "/repos/{repo_id}/install/preview"),
    ("POST", "/repos/{repo_id}/install"),
    ("POST", "/repos/{repo_id}/upgrade/preview"),
    ("POST", "/repos/{repo_id}/upgrade"),
    ("POST", "/repos/{repo_id}/uninstall/preview"),
    ("POST", "/repos/{repo_id}/uninstall"),
    ("POST", "/repos/{repo_id}/rollback/preview"),
    ("POST", "/repos/{repo_id}/rollback"),
    ("POST", "/repos/{repo_id}/install/recovery/preview"),
    ("POST", "/repos/{repo_id}/install/recovery"),
    ("GET", "/repos/{repo_id}/transactions/{transaction_id}"),
    ("POST", "/repos/{repo_id}/transactions/{transaction_id}/cancel"),
    ("GET", "/repos/{repo_id}/receipts"),
    ("GET", "/repos/{repo_id}/git"),
    ("GET", "/repos/{repo_id}/intents"),
    ("POST", "/repos/{repo_id}/intents"),
    ("POST", "/repos/{repo_id}/intents/plan/preview"),
    ("POST", "/repos/{repo_id}/intents/plan/advise"),
    ("GET", _INTENT),
    ("POST", f"{_INTENT}/runtime/compile"),
    ("GET", f"{_INTENT}/map"),
    ("GET", f"{_INTENT}/artifacts"),
    ("GET", f"{_INTENT}/artifacts/{{artifact_id}}"),
    ("GET", f"{_INTENT}/questions"),
    ("GET", f"{_INTENT}/review"),
    ("GET", f"{_INTENT}/git"),
    ("GET", f"{_INTENT}/activity"),
    ("POST", f"{_INTENT}/recompose/preview"),
    ("POST", f"{_INTENT}/recompose"),
    ("POST", f"{_INTENT}/run"),
    ("POST", f"{_INTENT}/resume"),
    ("POST", f"{_INTENT}/pause"),
    ("POST", f"{_INTENT}/force-stop"),
    ("POST", f"{_INTENT}/prepare-commit"),
    ("POST", f"{_INTENT}/keep-moving"),
    ("POST", f"{_INTENT}/session/bind"),
    ("POST", f"{_INTENT}/session/unbind"),
    ("POST", f"{_INTENT}/session/takeover/preview"),
    ("POST", f"{_INTENT}/session/takeover"),
    ("POST", f"{_INTENT}/archive"),
    ("POST", f"{_INTENT}/restore"),
    ("POST", f"{_INTENT}/settings/preview"),
    ("POST", f"{_INTENT}/settings"),
)


def all_routes(services: Any) -> list[Any]:
    """Every route, in the order §2.10 pins, or a registration-time failure.

    Built by looking each documented route up in what the handler modules offered, rather than by
    concatenating them: the two ordering constraints (``/repos/preflight`` before ``/repos/{repo_id}``,
    ``…/intents/plan/preview`` and ``…/intents/plan/advise`` before ``…/intents/{intent}`` — ``plan`` is
    a valid intent-directory spelling) are then satisfied by construction, and a
    module that forgets a route — or invents one — fails the enable loudly instead of shipping a 404
    the UI discovers later.
    """
    from . import actions, advisor, diagnostics, events, intents, leases, maintenance, repos, runtime, settings, slack, workspace

    offered: dict[tuple[str, str], Any] = {}
    for module in (diagnostics, leases, settings, events, actions, advisor, slack, repos, intents, workspace, maintenance, runtime):
        for entry in module.ROUTES(services):
            key = (entry.method.upper(), entry.path)
            if key in offered:
                raise RuntimeError(f"aidlc-studio: duplicate route {key[0]} {key[1]}")
            offered[key] = entry
    missing = [key for key in ROUTE_ORDER if key not in offered]
    extra = [key for key in offered if key not in ROUTE_ORDER]
    if missing or extra:
        raise RuntimeError(
            f"aidlc-studio: route table drift — missing={missing} unexpected={extra}"
        )
    return [offered[key] for key in ROUTE_ORDER]
