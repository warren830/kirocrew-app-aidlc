"""Cleanup of Studio's own transaction leftovers for one repository (``MaintenanceService``).

Entries are ids (``tx_<16hex>:backups``, ``…:failed``), never client paths. Preview takes the selected
``entry_ids`` (none: list only) and answers a ``plan_digest``; cleanup takes the same ids and that digest, and
a partial filesystem failure is an explicit ``ok: false`` whose remainder needs another preview.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from ..errors import StudioError
from ..maintenance import MaintenanceService
from .common import json_ok, read_json, repo_or_404


async def preview(services: Any, request: web.Request) -> web.Response:
    body = await _body(request, ("entry_ids",))
    repo = await repo_or_404(services, request.match_info["repo_id"])
    return json_ok(await MaintenanceService(services).preview(repo, body.get("entry_ids", [])))


async def cleanup(services: Any, request: web.Request) -> web.Response:
    body = await _body(request, ("entry_ids", "plan_digest"), required=True)
    repo = await repo_or_404(services, request.match_info["repo_id"])
    return json_ok(await MaintenanceService(services).cleanup(repo, body["entry_ids"], body["plan_digest"]))


async def _body(request: web.Request, allowed: tuple[str, ...], *, required: bool = False) -> dict[str, Any]:
    """Unknown fields are refused rather than ignored: a path smuggled in beside the ids must not look accepted."""
    body = await read_json(request, required=allowed if required else ())
    if set(body) - set(allowed):
        raise StudioError("bad_body", "unknown maintenance request fields",
                          details={"keys": sorted(set(body) - set(allowed))})
    return body
