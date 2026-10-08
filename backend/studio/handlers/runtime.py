"""Repair a created intent's runtime graph (``runtime/compile``) without creating an intent or sending a prompt.

The compile verb resolves the calling cursor, so the intent is activated first, and identity, host and state
are re-checked around every engine call under one admin lease. A refusal leaves the intent as it was, so the
caller can retry the same route.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import constants as C
from ..errors import StudioError
from .common import finish_shielded, json_ok, read_json, repo_or_404, snapshot_or_404


async def compile_runtime(services: Any, request: web.Request) -> web.Response:
    if await read_json(request):
        raise StudioError("bad_body", "runtime compilation accepts an empty body")
    repo = await repo_or_404(services, request.match_info["repo_id"])
    services.plan.require_ready(repo)
    snap = await snapshot_or_404(services, repo, request.match_info["intent"])
    return json_ok(await finish_shielded(_compile(services, repo, snap)))


def _signature(snap: Any) -> tuple[str, str | None]:
    if not snap.stable or snap.state is None or not snap.state_sha256:
        raise StudioError("unstable_read", "the intent state cannot be read consistently")
    if snap.row is None:
        raise StudioError("state_inconsistent", "the intent is missing from its registry")
    return snap.state_sha256, C.attr(snap.row, "uuid")


async def _guard(services: Any, repo: Any) -> None:
    await asyncio.to_thread(services.repos.require_current_identity, repo)
    current = await repo_or_404(services, repo.repo_id)
    services.plan.require_ready(current)
    if current.engine_dir != repo.engine_dir:
        raise StudioError("state_inconsistent", "the installed engine changed")
    services.scheduler.require_host_idle(repo)


async def _settled(services: Any, repo: Any, snap: Any, expected: tuple[str, str | None], when: str) -> Any:
    """Re-guard, re-read, and refuse unless the intent is unchanged and still the active one."""
    await _guard(services, repo)
    fresh = await snapshot_or_404(services, repo, snap.intent_key)
    if _signature(fresh) != expected:
        raise StudioError("unstable_read", f"the state changed {when} runtime compilation")
    if not fresh.is_active or fresh.dangling_cursor:
        raise StudioError("cursor_mismatch", f"the active intent changed {when} compilation")
    return fresh


async def _compile(services: Any, repo: Any, snap: Any) -> dict[str, Any]:
    expected = _signature(snap)
    grant = await services.scheduler.acquire_admin(repo, operation_type="cursor_switch", transaction_id=None)
    try:
        await _guard(services, repo)
        fresh = await snapshot_or_404(services, repo, snap.intent_key)
        if _signature(fresh) != expected:
            raise StudioError("unstable_read", "the intent changed before runtime compilation")
        root = Path(repo.canonical_path)
        engine_dir = services.plan.engine_dir(repo, fresh)
        await _guard(services, repo)
        cursor = await asyncio.to_thread(
            services.engine.switch_cursor_sync, root, engine_dir, fresh.space, fresh.intent_dir, expected[1],
            lease_generation=grant.generation, repo_id=repo.repo_id,
        )
        if not cursor.ok or any(not result.ok for result in cursor.results):
            raise StudioError("cursor_mismatch", "the selected intent could not be activated",
                              details={"mismatch": list(cursor.mismatch)})
        if cursor.state_sha256 != expected[0]:
            raise StudioError("unstable_read", "the state changed while activating the intent")

        await _settled(services, repo, snap, expected, "before")
        result = await services.engine.run("runtime.compile", root, engine_dir,
                                           lease_generation=grant.generation, repo_id=repo.repo_id)
        services.plan.require_engine_ok(result)
        await _settled(services, repo, snap, expected, "during")

        rel = f"{services.reader.record_rel(fresh.space, fresh.intent_dir)}/runtime-graph.json"
        output = result.json
        if not isinstance(output, dict) or output.get("written") != str(root / rel):
            raise StudioError("state_inconsistent", "runtime compilation did not write the selected graph")
        artifact = await asyncio.to_thread(services.reader.read_artifact, root, rel)
        try:
            graph = json.loads(artifact.bytes) if artifact is not None and not artifact.unstable else None
        except (ValueError, UnicodeDecodeError):
            graph = None
        if not isinstance(graph, dict) or not isinstance(graph.get("stages"), list):
            raise StudioError("state_inconsistent", "runtime compilation did not produce a readable graph",
                              details={"relpath": rel})
    finally:
        await services.scheduler.release(grant)
        # A cursor switch can succeed even when compilation fails, so the visible selection is refreshed too.
        await services.events.publish("repo.updated", {"repo_id": repo.repo_id})
    await services.events.publish("intent.updated", {"repo_id": repo.repo_id, "intent_key": snap.intent_key})
    return {"ok": True, "intent_key": snap.intent_key, "runtime_graph_present": True}
