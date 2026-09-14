"""``GET /leases`` (§2.1) — who currently holds what, and how close the cap is.

Read-only by construction: a lease is released by the operation that took it or reclaimed against
positive proof (FR-SES-007), never by a user pressing something in a list. So there is no route here
that ends a lease.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

from .. import constants as C
from ..leases import lease_view
from .common import json_ok, route


async def get_leases(services: Any, request: web.Request) -> web.Response:
    """Every live lease with its intent key, plus the cap the scheduler is enforcing.

    A lease is held per repository *identity* — that is the mutual exclusion AI-DLC needs — so which
    intent it is for is a fact about the action that took it. Hence the join: the execution lease names
    an action, and the action names the intent. An admin lease names none, and its ``intent_key`` is
    honestly ``null`` rather than guessed from whatever the repository last worked on.
    """
    records = await services.scheduler.list()
    views = []
    live_execution = 0
    for rec in records:
        if rec.kind == "execution":
            live_execution += 1
        views.append(
            lease_view(
                rec,
                intent_key=await intent_key_for_lease(services, rec),
                orphaned=services.scheduler.is_stale(rec),
            )
        )
    return json_ok(
        {
            "leases": views,
            "global_concurrency_cap": int(services.settings.global_concurrency_cap()),
            "live_execution": live_execution,
        }
    )


async def intent_key_for_lease(services: Any, rec: Any) -> str | None:
    """The intent a lease is working on, from its action first and its intent uuid second.

    Shared with ``/diagnostics`` so both views resolve it the same way. The action row is preferred
    because it is the authority: the uuid on a lease is a copy taken when it was acquired, and an intent
    can be renamed on disk while a turn is in flight.
    """
    action_id = getattr(rec, "action_id", None)
    if action_id:
        row = await asyncio.to_thread(services.storage.get, "actions", str(action_id))
        if row is not None:
            return C.intent_key_for(
                str(row.get("space") or C.DEFAULT_SPACE), str(row.get("intent_dir") or "")
            )
    uuid = getattr(rec, "intent_uuid", None)
    if uuid:
        rows = await asyncio.to_thread(
            services.storage.select, "intent_bindings", {"intent_uuid": str(uuid)}, limit=1
        )
        if rows:
            return C.intent_key_for(
                str(rows[0].get("space") or C.DEFAULT_SPACE), str(rows[0].get("intent_dir") or "")
            )
    return None


def ROUTES(services: Any) -> list[Any]:
    return [route("GET", "/leases", get_leases, services=services)]
