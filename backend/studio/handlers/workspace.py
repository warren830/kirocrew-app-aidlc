"""Owner-operated space and existing-intent settings controls. No host dispatch or direct writes."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import security
from ..errors import StudioError
from ..sessions import busy_reasons
from .common import json_ok, read_json, repo_or_404, route, snapshot_or_404

# The engine slugifies to 48 characters and prefixes non-letter starts with "intent-".
# Accept only names it will preserve byte-for-byte, and none of its reserved routing words.
SPACE_NAME_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
RESERVED_SPACE_NAMES = frozenset({"help", "list", "switch", "create", "archive", "rename", "show", "birth"})


def _name(body: dict[str, Any]) -> str:
    name = body.get("name")
    if (not isinstance(name, str) or len(name) > 48 or not SPACE_NAME_RE.fullmatch(name)
            or name in RESERVED_SPACE_NAMES):
        raise StudioError("bad_body", "use a lowercase kebab-case space name (1–48 characters)",
                          details={"key": "name"})
    return name


def _idle(services: Any, repo: Any) -> None:
    """Also catch host turns that were started outside Studio and therefore hold no Studio lease."""
    root = Path(repo.canonical_path).resolve()
    for slot in services.host.slots_by_prefix(""):
        if slot.project and Path(slot.project).resolve() == root and busy_reasons(slot):
            raise StudioError("repo_busy", "a session is executing in this repository",
                              details={"slot_key": slot.key})


async def _finish(operation: Any) -> Any:
    """A disconnected HTTP caller must not release a lease while its subprocess is still writing."""
    task = asyncio.create_task(operation)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


def _spaces(services: Any, repo: Any) -> dict[str, Any]:
    root = Path(repo.canonical_path)
    return {"spaces": services.reader.list_spaces(root),
            "active_space": services.reader.active_space(root)}


async def list_spaces(services: Any, request: web.Request) -> web.Response:
    repo = await repo_or_404(services, request.match_info["repo_id"])
    services.plan._require_ready(repo)
    return json_ok(await asyncio.to_thread(_spaces, services, repo))


async def _space_change(services: Any, repo: Any, name: str, *, create: bool) -> dict[str, Any]:
    services.plan._require_ready(repo)
    # Both operations alter the workspace used by a cursor. Reuse the established admin operation;
    # engine.run records the exact distinct verb in activity, without extending the lease vocabulary.
    grant = await services.scheduler.acquire_admin(repo, operation_type="cursor_switch", transaction_id=None)
    try:
        await asyncio.to_thread(services.repos.require_current_identity, repo)
        _idle(services, repo)
        root = Path(repo.canonical_path)
        before = await asyncio.to_thread(_spaces, services, repo)
        target = security.resolve_inside(root, f"aidlc/spaces/{name}")
        if create and (target.exists() or (root / f"aidlc/spaces/{name}").is_symlink()):
            raise StudioError("bad_param", "space already exists", details={"name": name})
        if not create and name not in before["spaces"]:
            raise StudioError("bad_param", "space does not exist", details={"name": name})
        verb = "utility.space_create" if create else "utility.space_switch"
        await asyncio.to_thread(services.repos.require_current_identity, repo)
        _idle(services, repo)
        result = await services.engine.run(
            verb, root, repo.engine_dir, slug=name, lease_generation=grant.generation, repo_id=repo.repo_id
        )
        services.plan._require_engine_ok(result)
        after = await asyncio.to_thread(_spaces, services, repo)
        if create:
            verified = (name in after["spaces"] and (target / "memory/org.md").is_file()
                        and (target / "intents").is_dir()
                        and after["active_space"] == before["active_space"])
        else:
            cursor = security.bounded_text(security.resolve_inside(root, "aidlc/active-space"), 256)
            verified = after["active_space"] == name and cursor is not None and cursor.strip() == name
        if not verified:
            raise StudioError("state_inconsistent", "the space operation did not match the disk state")
    finally:
        await services.scheduler.release(grant)
    await services.events.publish("repo.updated", {"repo_id": repo.repo_id})
    return {**after, "ok": True, "result": result.to_json()}


async def create_space(services: Any, request: web.Request) -> web.Response:
    name = _name(await read_json(request, required=("name",)))
    repo = await repo_or_404(services, request.match_info["repo_id"])
    return json_ok(await _finish(_space_change(services, repo, name, create=True)), status=201)


async def switch_space(services: Any, request: web.Request) -> web.Response:
    name = _name(await read_json(request, required=("name",)))
    repo = await repo_or_404(services, request.match_info["repo_id"])
    return json_ok(await _finish(_space_change(services, repo, name, create=False)))


async def settings_preview(services: Any, request: web.Request) -> web.Response:
    changes = await read_json(request)
    repo = await repo_or_404(services, request.match_info["repo_id"])
    snap = await snapshot_or_404(services, repo, request.match_info["intent"])
    answer = await asyncio.to_thread(services.plan.settings_preview, repo, snap, changes)
    return json_ok(answer)


async def change_settings(services: Any, request: web.Request) -> web.Response:
    body = await read_json(request, required=("proposal_digest",))
    digest = body.pop("proposal_digest")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise StudioError("bad_body", "proposal_digest must be a SHA-256 digest")
    repo = await repo_or_404(services, request.match_info["repo_id"])
    snap = await snapshot_or_404(services, repo, request.match_info["intent"])

    async def apply() -> dict[str, Any]:
        _idle(services, repo)
        return await services.plan.change_settings(repo, snap, body, proposal_digest=digest)

    return json_ok(await _finish(apply()))


def ROUTES(services: Any) -> list[Any]:
    """Self-contained registrations; the common route table must include ROUTE_ORDER below."""
    handlers = (list_spaces, create_space, switch_space, settings_preview, change_settings)
    return [route(method, path, handler, owner=method != "GET", services=services)
            for (method, path), handler in zip(ROUTE_ORDER, handlers)]


_REPO = "/repos/{repo_id}"
_INTENT = f"{_REPO}/intents/{{intent}}"
ROUTE_ORDER = (
    ("GET", f"{_REPO}/spaces"),
    ("POST", f"{_REPO}/spaces"),
    ("POST", f"{_REPO}/spaces/switch"),
    ("POST", f"{_INTENT}/settings/preview"),
    ("POST", f"{_INTENT}/settings"),
)
