"""The route table, the wrapper every route goes through, and the request helpers the handlers share.

* **No handler chooses an HTTP status.** Handlers raise ``StudioError`` and the wrapper maps the code
  through ``ERROR_CODES``, so two routes cannot answer one failure differently.
* **No unknown exception reaches the client.** Anything else is ``500 internal_error`` with a fixed
  message: an ``OSError`` or a ``sqlite3`` error can quote a user's path or a row. The traceback is logged.
* **Every mutation is owner-only and no read is**, decided here from the method and recorded on the wrapped
  handler, so a test proves it for the whole table instead of trusting each handler to remember.
* **``ROUTES`` is the registration order.** The host tries parameterised routes in that order, so
  ``/repos/preflight`` before ``/repos/{repo_id}`` and ``…/intents/plan/*`` before ``…/intents/{intent}``
  (``plan`` is a valid intent directory) are correctness requirements.
"""

from __future__ import annotations

import asyncio
import functools
import importlib
import json
import logging
from typing import Any, Coroutine, Mapping, Sequence

from aiohttp import web

from .. import constants as C
from ..errors import ERROR_CODES, StudioError

_LOG = logging.getLogger("kirocrew.app.aidlc-studio")

#: Feedback and answers, the largest legitimate bodies, are capped at 8 000 characters by ``constants``.
MAX_BODY_BYTES = 1 << 20

#: A 500's whole message: the exception's own text may quote a path, a row or a credential.
INTERNAL_ERROR_MESSAGE = "internal error"

_REPO = "/repos/{repo_id}"
_INTENT = f"{_REPO}/intents/{{intent}}"

#: ``(method, path, "module.handler")`` in registration order (§2.10).
ROUTES: tuple[tuple[str, str, str], ...] = (
    ("GET", "/health", "diagnostics.health"),
    ("GET", "/diagnostics", "diagnostics.bundle"),
    ("GET", "/settings", "settings.get_settings"),
    ("PUT", "/settings", "settings.put_settings"),
    ("PUT", "/tools/bun", "settings.put_bun"),
    ("POST", "/tools/bun/probe", "settings.probe_bun"),
    ("GET", "/events", "events.stream"),
    ("GET", "/events/poll", "events.poll"),
    ("GET", "/activity", "events.activity"),
    ("GET", "/actions", "actions.list_actions"),
    ("GET", "/actions/{action_id}", "actions.get_action"),
    ("POST", "/actions/{action_id}/submit", "actions.submit"),
    ("POST", "/actions/{action_id}/delivery", "actions.delivery"),
    ("POST", "/actions/{action_id}/resolve", "actions.resolve"),
    ("POST", "/advisor/draft", "advisor.request_draft"),
    ("GET", "/advisor/drafts/{draft_id}", "advisor.get_draft"),
    ("GET", "/repos", "repos.list_repos"),
    ("POST", "/repos", "repos.add_repo"),
    ("POST", "/repos/preflight", "repos.preflight"),
    ("POST", "/directories", "repos.directories"),
    ("GET", _REPO, "repos.get_repo"),
    ("PUT", f"{_REPO}/metadata", "repos.update_metadata"),
    ("DELETE", _REPO, "repos.remove_repo"),
    ("POST", f"{_REPO}/rebind", "repos.rebind"),
    ("POST", f"{_REPO}/rescan", "repos.rescan"),
    ("POST", f"{_REPO}/doctor", "repos.doctor"),
    ("GET", f"{_REPO}/git", "repos.repo_git"),
    ("POST", f"{_REPO}/transactions/preview", "repos.preview_transaction"),
    ("POST", f"{_REPO}/transactions", "repos.begin_transaction"),
    ("GET", f"{_REPO}/transactions/{{transaction_id}}", "repos.get_transaction"),
    ("POST", f"{_REPO}/transactions/{{transaction_id}}/cancel", "repos.cancel_transaction"),
    ("POST", f"{_REPO}/maintenance/preview", "maintenance.preview"),
    ("POST", f"{_REPO}/maintenance/cleanup", "maintenance.cleanup"),
    ("GET", f"{_REPO}/spaces", "workspace.list_spaces"),
    ("POST", f"{_REPO}/spaces", "workspace.create_space"),
    ("POST", f"{_REPO}/spaces/switch", "workspace.switch_space"),
    ("GET", f"{_REPO}/intents", "intents.list_intents"),
    ("POST", f"{_REPO}/intents", "intents.create_intent"),
    ("POST", f"{_REPO}/intents/plan/preview", "intents.plan_preview"),
    ("POST", f"{_REPO}/intents/plan/advise", "intents.plan_advise"),
    ("GET", _INTENT, "intents.get_intent"),
    ("GET", f"{_INTENT}/map", "intents.get_map"),
    ("GET", f"{_INTENT}/artifacts", "intents.list_artifacts"),
    ("GET", f"{_INTENT}/artifacts/{{artifact_id}}", "intents.get_artifact"),
    ("GET", f"{_INTENT}/review", "intents.get_review"),
    ("POST", f"{_INTENT}/recompose/preview", "intents.recompose_preview"),
    ("POST", f"{_INTENT}/recompose", "intents.recompose"),
    ("POST", f"{_INTENT}/settings/preview", "workspace.settings_preview"),
    ("POST", f"{_INTENT}/settings", "workspace.change_settings"),
    ("POST", f"{_INTENT}/runtime/compile", "runtime.compile_runtime"),
    ("POST", f"{_INTENT}/run", "intents.run"),
    ("POST", f"{_INTENT}/pause", "intents.pause"),
    ("POST", f"{_INTENT}/archive", "intents.archive"),
    ("POST", f"{_INTENT}/force-stop", "intents.force_stop"),
    ("POST", f"{_INTENT}/session", "intents.set_session"),
    ("POST", f"{_INTENT}/session/takeover/preview", "intents.takeover_preview"),
)

ROUTE_ORDER: tuple[tuple[str, str], ...] = tuple((method, path) for method, path, _ in ROUTES)


def all_routes(services: Any) -> list[Any]:
    """Every route in ``ROUTES`` order, wrapped around this container."""
    from kiro_crew.apps.route_registry import AppRoute

    out = []
    for method, path, target in ROUTES:
        module, _, name = target.partition(".")
        handler = getattr(importlib.import_module(f".{module}", __package__), name)
        out.append(AppRoute(method=method, path=path, handler=_wrap(services, method, path, handler)))
    return out


def _wrap(services: Any, method: str, path: str, handler: Any) -> Any:
    """Attach the host first (the owner check reads the attached state, C08), then authenticate, then run."""
    owner = method != "GET"

    async def wrapped(request: web.Request, ctx: Any) -> web.StreamResponse:
        try:
            services.attach_host(request.app.get("state"))
            if owner:
                require_owner(request, services.host)
            else:
                require_user(request)
            return await handler(services, request)
        except StudioError as exc:
            return json_ok(exc.to_json(), status=ERROR_CODES.get(exc.code, 500))
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.exception("aidlc-studio: unhandled error in %s %s", method, path)
            return json_ok({"error": INTERNAL_ERROR_MESSAGE, "code": "internal_error", "details": {}}, status=500)

    functools.update_wrapper(wrapped, handler)
    wrapped.__kirocrew_authenticated__ = True  # type: ignore[attr-defined]
    wrapped.__kirocrew_owner_only__ = owner  # type: ignore[attr-defined]
    return wrapped


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #


def require_user(request: web.Request) -> str:
    """The subject the gateway middleware authenticated; none means a call from outside it, so ``401``."""
    user = request.get("user")
    if user is None or str(user) == "":
        raise StudioError("unauthorized", "authentication required")
    return str(user)


def require_owner(request: web.Request, host: Any) -> str:
    """The most specific refusal first: anonymous, then an app token (which never mutates, whatever its
    manifest scope says), then a different human."""
    user = require_user(request)
    if str(request.get("app", "") or "") != "":
        raise StudioError("app_token_forbidden", "app tokens cannot perform this operation",
                          details={"app": str(request.get("app"))})
    if not host.is_owner_request(request):
        raise StudioError("owner_required", "only the dashboard owner can perform this operation")
    return user


def user_of(request: web.Request) -> str:
    return str(request.get("user") or "")


# --------------------------------------------------------------------------- #
# request and response
# --------------------------------------------------------------------------- #


def json_ok(data: Mapping[str, Any], status: int = 200) -> web.Response:
    """Non-ASCII kept as-is: AI-DLC question text is routinely Chinese (FORMAT-NOTES §7)."""
    return web.json_response(data, status=status, dumps=lambda obj: json.dumps(obj, ensure_ascii=False, default=str))


async def read_json(request: web.Request, *, required: Sequence[str] = ()) -> dict:
    """The body as a dict, or ``bad_body``. No body is ``{}`` (most mutations take nothing); ``required``
    names the fields that may not be absent or empty."""
    length = request.content_length
    if length is not None and int(length) > MAX_BODY_BYTES:
        raise StudioError("bad_body", "request body is too large",
                          details={"reason": "body_too_large", "limit": MAX_BODY_BYTES})
    try:
        body = await request.json()
    except Exception:
        body = None
    if body is None:
        body = {}
    if not isinstance(body, Mapping):
        raise StudioError("bad_body", "request body must be a JSON object", details={"reason": "not_an_object"})
    data = dict(body)
    missing = [name for name in required if data.get(name) in (None, "")]
    if missing:
        raise StudioError("bad_body", "request body is missing required fields", details={"missing": missing})
    return data


def body_bool(body: Mapping[str, Any], name: str) -> bool:
    value = body.get(name)
    if not isinstance(value, bool):
        raise StudioError("bad_body", f"{name} must be a boolean", details={"missing": [name]})
    return value


def body_choice(body: Mapping[str, Any], name: str, choices: Sequence[str], default: str | None = None) -> str:
    value = body.get(name, default)
    if value not in choices:
        raise StudioError("bad_body", f"{name} must be one of {', '.join(choices)}",
                          details={"key": name, "choices": list(choices)})
    return str(value)


def query_str(request: web.Request, name: str, default: str | None = None, *,
              pattern: Any | None = None, choices: Sequence[str] | None = None) -> str | None:
    """One query parameter, validated; an empty value is absent."""
    raw = request.query.get(name)
    if raw is None or raw == "":
        return default
    if choices is not None and raw not in choices:
        raise StudioError("bad_param", f"{name} must be one of {', '.join(choices)}",
                          details={"param": name, "choices": list(choices)})
    if pattern is not None and not pattern.match(raw):
        raise StudioError("bad_param", f"{name} is malformed", details={"param": name})
    return raw


def query_int(request: web.Request, name: str, default: int | None = None, *,
              lo: int | None = None, hi: int | None = None) -> int | None:
    raw = request.query.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise StudioError("bad_param", f"{name} must be an integer", details={"param": name}) from None
    if lo is not None and value < lo:
        raise StudioError("bad_param", f"{name} must be >= {lo}", details={"param": name, "min": lo})
    if hi is not None and value > hi:
        raise StudioError("bad_param", f"{name} must be <= {hi}", details={"param": name, "max": hi})
    return value


def query_bool(request: web.Request, name: str, default: bool = False) -> bool:
    """``1``/``true``/``0``/``false`` only: ``?include_archived=no`` reading as true would be a silent surprise."""
    raw = request.query.get(name)
    if raw is None or raw == "":
        return default
    lowered = raw.lower()
    if lowered in ("1", "true"):
        return True
    if lowered in ("0", "false"):
        return False
    raise StudioError("bad_param", f"{name} must be 1, 0, true or false", details={"param": name})


def parse_intent_key(raw: str) -> tuple[str, str]:
    """``intent_dir`` or ``space~intent_dir`` (§0.1). ``~`` is outside both grammars, so one reading exists."""
    text = str(raw or "")
    if C.INTENT_KEY_SEPARATOR in text:
        space, _, intent_dir = text.partition(C.INTENT_KEY_SEPARATOR)
    else:
        space, intent_dir = C.DEFAULT_SPACE, text
    if not C.SPACE_RE.match(space) or not C.INTENT_DIR_RE.match(intent_dir):
        raise StudioError("bad_param", "malformed intent key", details={"param": "intent", "value": text})
    return space, intent_dir


async def finish_shielded(operation: Coroutine[Any, Any, Any]) -> Any:
    """Run ``operation`` to the end even if the request is cancelled, then re-raise the cancellation.

    The operation holds an admin lease around an engine subprocess; a disconnected caller (or a shutdown
    that cancels repeatedly) must not release the lease while the subprocess is still writing.
    """
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        try:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            task.result()
        finally:
            raise cancelled


# --------------------------------------------------------------------------- #
# shared reads
# --------------------------------------------------------------------------- #


async def repo_or_404(services: Any, repo_id: str) -> Any:
    return await asyncio.to_thread(services.repos.get, str(repo_id))


async def snapshot_or_404(services: Any, repo: Any, intent_key: str) -> Any:
    """``repo_unavailable`` before ``intent_not_found``: a moved repository reads as empty, and calling that
    "no such intent" would tell the user their intent is gone when the directory is merely elsewhere."""
    space, intent_dir = parse_intent_key(intent_key)
    if str(C.attr(repo, "availability", "")) != "available":
        raise StudioError("repo_unavailable", "this repository cannot be read right now", details={
            "repo_id": C.attr(repo, "repo_id"), "availability": C.attr(repo, "availability"),
            "detail": C.attr(repo, "availability_detail"),
        })
    snap = await asyncio.to_thread(services.projection.snapshot, repo, space, intent_dir)
    if snap.row is None and snap.state_snap is None:
        raise StudioError("intent_not_found", "no such intent in this repository",
                          details={"intent_key": C.intent_key_for(space, intent_dir)})
    return snap


async def repo_and_snapshot(services: Any, request: web.Request) -> tuple[Any, Any]:
    repo = await repo_or_404(services, request.match_info["repo_id"])
    return repo, await snapshot_or_404(services, repo, request.match_info["intent"])


async def intent_view(services: Any, repo: Any, snap: Any, *, install: Any = None,
                      leases: Sequence[Any] | None = None) -> dict[str, Any]:
    """What a read needs about one intent: the reconciler's assembly, stopped before its writes — a GET that
    minted a card would make the queue depend on who was looking at it."""
    from ..consistency import RepoFacts

    repo_id = str(C.attr(repo, "repo_id"))
    binding = await services.sessions.get(repo_id, snap.space, snap.intent_dir)
    slot = services.host.slot(binding.slot_key) if binding.slot_key else None
    live = await services.actions.list_live(repo_id=repo_id, space=snap.space, intent_dir=snap.intent_dir,
                                            include_revision=True)
    # The reconciler's breaker key rule (§0.1): `<repo_id>:<intent_key>:…`.
    prefix = f"{repo_id}:{snap.intent_key}:"
    breakers = [row for row in await asyncio.to_thread(services.storage.select, "breakers")
                if str(row.get("key") or "").startswith(prefix)]
    if install is None:
        install = await asyncio.to_thread(services.repos.detect_install, repo)
    if leases is None:
        leases = await services.scheduler.list()
    facts = RepoFacts(now=services.clock.iso(), repo=repo, install=install, binding=binding, slot=slot,
                      live_actions=live, leases=list(leases), first_unreadable_at=snap.first_unreadable_at,
                      layout=snap.layout)
    return {
        "binding": binding,
        "slot": slot,
        "live_actions": live,
        "breaker_open": any(row.get("opened_at") for row in breakers),
        "findings": await asyncio.to_thread(services.consistency.evaluate_intent, snap, facts),
        "session": await services.sessions.session_ref(binding),
    }


def summary_args(view: Mapping[str, Any]) -> dict[str, Any]:
    """The ``Projection.summarize``/``detail`` inputs from an ``intent_view`` (revision cards stay out, C18)."""
    return {
        "binding": view["binding"],
        "live_actions": [a for a in view["live_actions"] if C.attr(a, "type") != "revision"],
        "breaker_open": view["breaker_open"],
        "host": view["session"],
        "findings": view["findings"],
        "slot": view["slot"],
    }


async def summarize_intent(services: Any, snap: Any, view: Mapping[str, Any]) -> Any:
    return await asyncio.to_thread(functools.partial(services.projection.summarize, snap, **summary_args(view)))


async def without_archived(services: Any, records: list[Any]) -> list[Any]:
    """Drop the cards of archived intents: the queue's and the repository counts' view (§11.2)."""
    archived = await services.sessions.archived_intents()
    if not archived:
        return records
    return [rec for rec in records if (C.attr(rec, "repo_id"), C.attr(rec, "space") or C.DEFAULT_SPACE,
                                       C.attr(rec, "intent_dir")) not in archived]


async def cards_for(services: Any, records: Sequence[Any]) -> list[dict[str, Any]]:
    """``ActionCard`` JSON, snapshotting each intent once: three cards on one intent must not cost three reads
    of its state file."""
    cache: dict[tuple[str, str, str], Any] = {}
    repos: dict[str, Any] = {}
    out: list[dict[str, Any]] = []
    for rec in records:
        key = (rec.repo_id, rec.space, rec.intent_dir)
        if key not in cache:
            snap = None
            try:
                if rec.repo_id not in repos:
                    repos[rec.repo_id] = await repo_or_404(services, rec.repo_id)
                repo = repos[rec.repo_id]
                if str(C.attr(repo, "availability", "")) == "available":
                    snap = await asyncio.to_thread(services.projection.snapshot, repo, rec.space, rec.intent_dir)
            except StudioError:
                # A card outlives its repository row (deleted, or moved and unreadable); its stored evidence is
                # still the record of what a human decided, so it renders from that.
                snap = None
            cache[key] = snap
        out.append(await services.actions.card(rec, cache[key]))
    return out


async def card_of(services: Any, rec: Any) -> dict[str, Any]:
    return (await cards_for(services, [rec]))[0]
