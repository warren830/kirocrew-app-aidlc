"""Health, payload provenance and the support bundle (§2.1).

The three routes here answer "is this app working", "what exactly would it install", and "what was it
doing" — and every one of them is a read a user in trouble makes. So: nothing here mutates anything,
and everything that leaves ``/diagnostics`` goes through redaction and path scrubbing, because the
whole point of a support bundle is that it can be pasted into an issue.
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any

from aiohttp import web

from .. import constants as C
from .. import security
from ..errors import StudioError
from ..leases import lease_view
from ..storage import ActivityQuery
from .common import json_ok, query_bool, route
from .leases import intent_key_for_lease

#: How much history the bundle carries. Bounded because the bundle is downloaded and pasted: an
#: unbounded activity dump would be unreadable and would defeat the redaction review it exists for.
DIAGNOSTICS_ACTIVITY_ROWS = 200
DIAGNOSTICS_TRANSACTIONS = 20


async def get_health(services: Any, request: web.Request) -> web.Response:
    """``GET /health`` — the whole envelope in one object.

    Computed in a worker thread because it reads SQLite (five counts plus a ``quick_check``), which
    §0.6 forbids on the event loop.
    """
    return json_ok(await asyncio.to_thread(services.health))


async def get_payload(services: Any, request: web.Request) -> web.Response:
    """``GET /payload`` — what the bundled engine is, straight from ``payload/manifest.json``.

    Every number is the manifest's own (C21): a literal here would let code and payload disagree after
    a payload bump, and the file list is served only on request because it is hundreds of entries long.
    """
    body = dict(services.payload.to_json())
    body["status"] = (await _payload_status(services)).to_json()
    if query_bool(request, "files", False):
        body["files"] = [f.to_json() for f in services.payload.files]
    return json_ok(body)


async def get_diagnostics(services: Any, request: web.Request) -> web.Response:
    """``GET /diagnostics`` — the support bundle, redacted, optionally as a download.

    ``human_text`` needs both the setting and the query flag. Two switches rather than one because the
    setting is a standing policy decision and the flag is a per-export one; either alone leaving verbatim
    decision text in a file the user is about to paste somewhere is the failure this prevents.
    """
    export = query_bool(request, "export", False)
    include_human_text = query_bool(request, "include_human_text", False) and bool(
        services.settings.diagnostics().get("export_include_human_text")
    )

    repos = await asyncio.to_thread(functools.partial(services.repos.list, include_archived=True))
    roots = [str(C.attr(repo, "canonical_path", "")) for repo in repos]

    health = await asyncio.to_thread(services.health)
    live = await services.actions.list_live(include_revision=True)
    cards = [await services.actions.card(rec, None) for rec in live]
    leases = await services.scheduler.list()
    transactions: list[dict[str, Any]] = []
    for repo in repos:
        rows = await asyncio.to_thread(
            functools.partial(
                services.installer.transactions, str(C.attr(repo, "repo_id")), DIAGNOSTICS_TRANSACTIONS
            )
        )
        transactions.extend(tx.to_json() for tx in rows)
    transactions.sort(key=lambda tx: str(tx.get("started_at") or ""), reverse=True)

    activity = await services.activity.export(
        ActivityQuery(limit=DIAGNOSTICS_ACTIVITY_ROWS),
        include_human_text=include_human_text,
        allowed_roots=roots,
    )
    breakers = await asyncio.to_thread(services.storage.select, "breakers")

    body: dict[str, Any] = {
        "generated_at": services.clock.iso(),
        "health": health,
        "settings": services.settings.values(),
        "repos": [repo.to_json() for repo in repos],
        "live_actions": cards,
        "leases": [
            lease_view(
                rec,
                intent_key=await intent_key_for_lease(services, rec),
                orphaned=services.scheduler.is_stale(rec),
            )
            for rec in leases
        ],
        "recent_transactions": transactions[:DIAGNOSTICS_TRANSACTIONS],
        "recent_activity": activity["rows"],
        "breakers": [
            {
                "key": str(row.get("key") or ""),
                "count": int(row.get("count") or 0),
                "opened_at": row.get("opened_at"),
                "fingerprint_class": row.get("fingerprint_class"),
            }
            for row in breakers
        ],
        "redacted": True,
    }

    scrubbed = await asyncio.to_thread(_redact_bundle, body, roots)
    response = json_ok(scrubbed)
    if export:
        stamp = services.clock.iso().replace(":", "").replace("-", "")
        response.headers["Content-Disposition"] = (
            f'attachment; filename="aidlc-studio-diagnostics-{stamp}.json"'
        )
    return response


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


async def _payload_status(services: Any) -> Any:
    """The verified payload status, computing it once if startup has not.

    Cached on the container because verifying re-hashes every file in the payload: a dashboard
    polling ``/payload`` would otherwise pay for that on every poll.
    """
    if services.payload_status is not None:
        return services.payload_status
    if services.payload_error is not None:
        raise StudioError(
            "payload_degraded",
            "the bundled payload manifest is unreadable",
            details={"reason": services.payload_error},
        )
    services.payload_status = await asyncio.to_thread(services.installer.verify_payload)
    return services.payload_status


def _redact_bundle(value: Any, roots: list[str]) -> Any:
    """Credential-redact and path-scrub every string in the bundle. Sync (it walks the whole body).

    Two passes with different jobs: ``redact`` removes secrets wherever they appear, and
    ``scrub_repo_paths`` replaces every absolute path that is *not* under a registered repository root
    with ``<path>`` — so a bundle about one repository cannot carry the layout of the user's whole disk
    (architecture §10: repo paths appear only for the registered repo itself).
    """
    if isinstance(value, str):
        return security.scrub_repo_paths(security.redact(value), roots)
    if isinstance(value, dict):
        return {key: _redact_bundle(item, roots) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_bundle(item, roots) for item in value]
    return value


def ROUTES(services: Any) -> list[Any]:
    return [
        route("GET", "/health", get_health, services=services),
        route("GET", "/payload", get_payload, services=services),
        route("GET", "/diagnostics", get_diagnostics, services=services),
    ]
