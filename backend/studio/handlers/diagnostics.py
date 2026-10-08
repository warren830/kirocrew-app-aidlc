"""``/health`` and the support bundle (§2.1).

Both are reads a user in trouble makes: nothing here mutates, and everything that leaves ``/diagnostics``
is credential-redacted and path-scrubbed, because the bundle exists to be pasted into an issue.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

from .. import constants as C
from .. import security
from ..leases import lease_view
from ..storage import ActivityQuery
from .common import json_ok, query_bool

#: Bounded: an unbounded dump would be unreadable and defeat the redaction review it exists for.
DIAGNOSTICS_ACTIVITY_ROWS = 200
DIAGNOSTICS_TRANSACTIONS = 20


async def health(services: Any, request: web.Request) -> web.Response:
    """The status strip's lease counts come from here too (``counts.execution_leases``/``admin_leases``)."""
    return json_ok(await asyncio.to_thread(services.health))  # SQLite reads: off the event loop (§0.6)


async def bundle(services: Any, request: web.Request) -> web.Response:
    """Verbatim decision text needs both the standing setting and this export's flag, so neither switch
    alone can leave it in a file the user is about to paste somewhere."""
    export = query_bool(request, "export", False)
    include_human_text = query_bool(request, "include_human_text", False) and bool(
        services.settings.diagnostics().get("export_include_human_text"))

    repos = await asyncio.to_thread(services.repos.list, include_archived=True)
    roots = [str(repo.canonical_path) for repo in repos]
    transactions: list[dict[str, Any]] = []
    for repo in repos:
        rows = await asyncio.to_thread(services.installer.transactions, repo.repo_id, DIAGNOSTICS_TRANSACTIONS)
        transactions.extend(tx.to_json() for tx in rows)
    transactions.sort(key=lambda tx: str(tx.get("started_at") or ""), reverse=True)
    activity = await services.activity.export(ActivityQuery(limit=DIAGNOSTICS_ACTIVITY_ROWS),
                                              include_human_text=include_human_text, allowed_roots=roots)
    body: dict[str, Any] = {
        "generated_at": services.clock.iso(),
        "health": await asyncio.to_thread(services.health),
        "settings": services.settings.values(),
        "repos": [repo.to_json() for repo in repos],
        "live_actions": [await services.actions.card(rec, None)
                         for rec in await services.actions.list_live(include_revision=True)],
        "leases": [lease_view(rec, intent_key=await _lease_intent(services, rec))
                   for rec in await services.scheduler.list()],
        "recent_transactions": transactions[:DIAGNOSTICS_TRANSACTIONS],
        "recent_activity": activity["rows"],
        "breakers": [
            {"key": str(row.get("key") or ""), "count": int(row.get("count") or 0),
             "opened_at": row.get("opened_at"), "fingerprint_class": row.get("fingerprint_class")}
            for row in await asyncio.to_thread(services.storage.select, "breakers")
        ],
        "redacted": True,
    }
    response = json_ok(await asyncio.to_thread(_redact, body, roots))
    if export:
        stamp = services.clock.iso().replace(":", "").replace("-", "")
        response.headers["Content-Disposition"] = f'attachment; filename="aidlc-studio-diagnostics-{stamp}.json"'
    return response


async def _lease_intent(services: Any, rec: Any) -> str | None:
    """The lease's action names the intent (the authority: the uuid on a lease is a copy taken at acquire
    time, and an intent can be renamed mid-turn); an admin lease names none, so ``None`` rather than a guess."""
    if rec.action_id:
        row = await asyncio.to_thread(services.storage.get, "actions", str(rec.action_id))
        if row is not None:
            return C.intent_key_for(str(row.get("space") or C.DEFAULT_SPACE), str(row.get("intent_dir") or ""))
    if rec.intent_uuid:
        rows = await asyncio.to_thread(services.storage.select, "intent_bindings",
                                       {"intent_uuid": str(rec.intent_uuid)}, limit=1)
        if rows:
            return C.intent_key_for(str(rows[0].get("space") or C.DEFAULT_SPACE), str(rows[0].get("intent_dir") or ""))
    return None


def _redact(value: Any, roots: list[str]) -> Any:
    """Secrets out of every string, and every absolute path outside a registered root becomes ``<path>``:
    a bundle about one repository must not carry the layout of the user's whole disk (architecture §10)."""
    if isinstance(value, str):
        return security.scrub_repo_paths(security.redact(value), roots)
    if isinstance(value, dict):
        return {key: _redact(item, roots) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item, roots) for item in value]
    return value
