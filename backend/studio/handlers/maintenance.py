"""Two owner-only cleanup routes; main registers ROUTE_ORDER in handlers.common.

POST …/maintenance/preview: {"entry_ids": ["tx_<16hex>:backups", "tx_<16hex>:failed"]}.
Omit entry_ids (or use []) to list candidates without selecting anything.
POST …/maintenance/cleanup: the same entry_ids plus the exact preview plan_digest.

Preview returns entries, totals, blocked, can_cleanup and plan_digest. Cleanup returns ok, deleted,
failures, remaining, record_key and requires_preview. A partial filesystem failure is an explicit
ok:false result; remaining bytes require another preview. No client-supplied path is accepted.

Entry reason is a stable code, for both eligible and protected entries; eligibility is determined by
protected, not by a null reason. Display consequence before confirmation, especially for eligible
older_restore_point backups. These can be discarded without deleting current one-hop rollback bytes
or receipt records. Current transaction backups and immediate unsettled restoration points stay
protected; older receipt ancestors are not recursively protected. Sizes are logical file bytes.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from ..errors import StudioError
from ..maintenance import MaintenanceService
from .common import json_ok, read_json, repo_or_404, route


async def _body(request: web.Request, *, apply: bool) -> dict[str, Any]:
    required = ("entry_ids", "plan_digest") if apply else ()
    body = await read_json(request, required=required)
    allowed = {"entry_ids", "plan_digest"} if apply else {"entry_ids"}
    if set(body) - allowed:
        raise StudioError("bad_body", "unknown maintenance request fields",
                          details={"keys": sorted(set(body) - allowed)})
    return body


async def preview(services: Any, request: web.Request) -> web.Response:
    body = await _body(request, apply=False)
    repo = await repo_or_404(services, request.match_info["repo_id"])
    return json_ok(await MaintenanceService(services).preview(repo, body.get("entry_ids", [])))


async def cleanup(services: Any, request: web.Request) -> web.Response:
    body = await _body(request, apply=True)
    repo = await repo_or_404(services, request.match_info["repo_id"])
    result = await MaintenanceService(services).cleanup(repo, body["entry_ids"], body["plan_digest"])
    return json_ok(result)


ROUTE_ORDER = (
    ("POST", "/repos/{repo_id}/maintenance/preview"),
    ("POST", "/repos/{repo_id}/maintenance/cleanup"),
)


def ROUTES(services: Any) -> list[Any]:
    return [route(method, path, handler, owner=True, services=services)
            for (method, path), handler in zip(ROUTE_ORDER, (preview, cleanup))]
