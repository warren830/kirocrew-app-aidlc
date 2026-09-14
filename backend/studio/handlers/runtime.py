"""Repair a created intent's derived graph without creating an intent or dispatching a prompt.

Main registers ROUTE_ORDER and exposes POST …/runtime/compile with an empty body. Success is
{ok: true, intent_key: string, runtime_graph_present: true}; ordinary StudioError responses retain
the existing intent, so the caller can retry the same endpoint.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import constants as C
from ..errors import StudioError
from .common import json_ok, read_json, repo_or_404, route, snapshot_or_404


def _signature(snap: Any) -> tuple[str, str | None]:
    if not snap.stable or snap.state is None or not snap.state_sha256:
        raise StudioError("unstable_read", "the intent state cannot be read consistently")
    if snap.row is None:
        raise StudioError("state_inconsistent", "the intent is missing from its registry")
    return snap.state_sha256, C.attr(snap.row, "uuid")


async def _guard(services: Any, repo: Any, grant: Any) -> None:
    await asyncio.to_thread(services.repos.require_current_identity, repo)
    current = await repo_or_404(services, repo.repo_id)
    services.plan._require_ready(current)
    if current.engine_dir != repo.engine_dir:
        raise StudioError("state_inconsistent", "the installed engine changed")
    services.scheduler.require_host_idle(repo)
    await services.scheduler.heartbeat(grant)


async def _compile(services: Any, repo: Any, snap: Any) -> dict[str, Any]:
    expected = _signature(snap)
    grant = await services.scheduler.acquire_admin(
        repo, operation_type="cursor_switch", transaction_id=None,
    )
    try:
        await _guard(services, repo, grant)
        fresh = await snapshot_or_404(services, repo, snap.intent_key)
        if _signature(fresh) != expected:
            raise StudioError("unstable_read", "the intent changed before runtime compilation")
        root = Path(repo.canonical_path)
        engine_dir = services.plan._engine_dir(repo, fresh)
        await _guard(services, repo, grant)
        cursor = await asyncio.to_thread(
            services.engine.switch_cursor_sync, root, engine_dir,
            fresh.space, fresh.intent_dir, expected[1],
            lease_generation=grant.generation, repo_id=repo.repo_id,
        )
        await services.engine.drain_activity()
        if not cursor.ok or any(not result.ok for result in cursor.results):
            raise StudioError("cursor_mismatch", "the selected intent could not be activated",
                              details={"mismatch": list(cursor.mismatch)})
        if cursor.state_sha256 != expected[0]:
            raise StudioError("unstable_read", "the state changed while activating the intent")

        # The compile verb resolves the calling session/cursor. Verify that selection first, then
        # guard the identity, host and state again immediately before invoking the writing tool.
        await _guard(services, repo, grant)
        active = await snapshot_or_404(services, repo, snap.intent_key)
        if _signature(active) != expected:
            raise StudioError("unstable_read", "the state changed before runtime compilation")
        if not active.is_active or active.dangling_cursor:
            raise StudioError("cursor_mismatch", "the active intent changed before compilation")
        result = await services.engine.run(
            "runtime.compile", root, engine_dir,
            lease_generation=grant.generation, repo_id=repo.repo_id,
        )
        services.plan._require_engine_ok(result)

        await _guard(services, repo, grant)
        observed = await snapshot_or_404(services, repo, snap.intent_key)
        if _signature(observed) != expected:
            raise StudioError("unstable_read", "the state changed during runtime compilation")
        if not observed.is_active or observed.dangling_cursor:
            raise StudioError("cursor_mismatch", "the active intent changed during compilation")
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
        # Cursor changes can succeed even if compilation fails. Refresh that visible selection too.
        await services.events.publish("repo.updated", {"repo_id": repo.repo_id})
    await services.events.publish("intent.updated", {"repo_id": repo.repo_id, "intent_key": snap.intent_key})
    return {"ok": True, "intent_key": snap.intent_key, "runtime_graph_present": True}


async def _finish(operation: Any) -> Any:
    # Like workspace._finish, with continued shielding if shutdown/disconnect cancels us repeatedly.
    # The operation owns the lease; cancelling its to_thread await would not stop its subprocess.
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


async def compile_runtime(services: Any, request: web.Request) -> web.Response:
    if await read_json(request):
        raise StudioError("bad_body", "runtime compilation accepts an empty body")
    repo = await repo_or_404(services, request.match_info["repo_id"])
    services.plan._require_ready(repo)
    snap = await snapshot_or_404(services, repo, request.match_info["intent"])
    return json_ok(await _finish(_compile(services, repo, snap)))


ROUTE_ORDER = (("POST", "/repos/{repo_id}/intents/{intent}/runtime/compile"),)


def ROUTES(services: Any) -> list[Any]:
    return [route(*ROUTE_ORDER[0], compile_runtime, owner=True, services=services)]
