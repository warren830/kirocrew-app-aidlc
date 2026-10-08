"""Owner-operated spaces and existing-intent settings, through the engine's own verbs; no host dispatch and no
direct write."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import security
from ..errors import StudioError
from ..sessions import busy_reasons
from .common import finish_shielded, json_ok, read_json, repo_and_snapshot, repo_or_404

# The engine slugifies to 48 characters and prefixes a non-letter start with "intent-": accept only names it
# keeps byte-for-byte, and none of its routing words.
SPACE_NAME_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
RESERVED_SPACE_NAMES = frozenset({"help", "list", "switch", "create", "archive", "rename", "show", "birth"})


async def list_spaces(services: Any, request: web.Request) -> web.Response:
    repo = await repo_or_404(services, request.match_info["repo_id"])
    services.plan.require_ready(repo)
    return json_ok(await asyncio.to_thread(_spaces, services, repo))


async def create_space(services: Any, request: web.Request) -> web.Response:
    name = _name(await read_json(request, required=("name",)))
    repo = await repo_or_404(services, request.match_info["repo_id"])
    return json_ok(await finish_shielded(_space_change(services, repo, name, create=True)), status=201)


async def switch_space(services: Any, request: web.Request) -> web.Response:
    name = _name(await read_json(request, required=("name",)))
    repo = await repo_or_404(services, request.match_info["repo_id"])
    return json_ok(await finish_shielded(_space_change(services, repo, name, create=False)))


async def settings_preview(services: Any, request: web.Request) -> web.Response:
    changes = await read_json(request)
    repo, snap = await repo_and_snapshot(services, request)
    return json_ok(await asyncio.to_thread(services.plan.settings_preview, repo, snap, changes))


async def change_settings(services: Any, request: web.Request) -> web.Response:
    body = await read_json(request, required=("proposal_digest",))
    digest = body.pop("proposal_digest")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise StudioError("bad_body", "proposal_digest must be a SHA-256 digest")
    repo, snap = await repo_and_snapshot(services, request)

    async def apply() -> dict[str, Any]:
        _idle(services, repo)
        return await services.plan.change_settings(repo, snap, body, proposal_digest=digest)

    return json_ok(await finish_shielded(apply()))


def _name(body: dict[str, Any]) -> str:
    name = body.get("name")
    if (not isinstance(name, str) or len(name) > 48 or not SPACE_NAME_RE.fullmatch(name)
            or name in RESERVED_SPACE_NAMES):
        raise StudioError("bad_body", "use a lowercase kebab-case space name (1–48 characters)", details={"key": "name"})
    return name


def _idle(services: Any, repo: Any) -> None:
    """Also catches host turns started outside Studio, which hold no Studio lease."""
    root = Path(repo.canonical_path).resolve()
    for slot in services.host.slots_by_prefix(""):
        if slot.project and Path(slot.project).resolve() == root and busy_reasons(slot):
            raise StudioError("repo_busy", "a session is executing in this repository", details={"slot_key": slot.key})


def _spaces(services: Any, repo: Any) -> dict[str, Any]:
    root = Path(repo.canonical_path)
    return {"spaces": services.reader.list_spaces(root), "active_space": services.reader.active_space(root)}


async def _space_change(services: Any, repo: Any, name: str, *, create: bool) -> dict[str, Any]:
    """Under the established ``cursor_switch`` admin lease (both verbs move the workspace a cursor uses);
    ``engine.run`` records the exact verb in Activity. Verified against the disk afterwards."""
    services.plan.require_ready(repo)
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
        await asyncio.to_thread(services.repos.require_current_identity, repo)
        _idle(services, repo)
        result = await services.engine.run("utility.space_create" if create else "utility.space_switch", root,
                                           repo.engine_dir, slug=name, lease_generation=grant.generation,
                                           repo_id=repo.repo_id)
        services.plan.require_engine_ok(result)
        after = await asyncio.to_thread(_spaces, services, repo)
        if create:
            verified = (name in after["spaces"] and (target / "memory/org.md").is_file()
                        and (target / "intents").is_dir() and after["active_space"] == before["active_space"])
        else:
            cursor = security.bounded_text(security.resolve_inside(root, "aidlc/active-space"), 256)
            verified = after["active_space"] == name and cursor is not None and cursor.strip() == name
        if not verified:
            raise StudioError("state_inconsistent", "the space operation did not match the disk state")
    finally:
        await services.scheduler.release(grant)
    await services.events.publish("repo.updated", {"repo_id": repo.repo_id})
    return {**after, "ok": True, "result": result.to_json()}
