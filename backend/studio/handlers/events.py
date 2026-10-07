"""Live updates (§2.5/§2.12) and the merged timeline (§2.6).

The stream and the cursor rules are ``EventLog``'s; the handlers only parse the cursor and hand over, because
a second implementation of "did this client lose history" would eventually disagree with the first.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from aiohttp import web

from .. import constants as C
from ..activity import TimelineEntry
from ..storage import ActivityQuery
from .common import json_ok, parse_intent_key, query_int, query_str, repo_or_404, route


async def stream_events(services: Any, request: web.Request) -> web.StreamResponse:
    """``GET /events`` — SSE, replaying from ``cursor`` (or ``Last-Event-ID``) when it can be resumed."""
    return await services.events.stream(request, cursor=query_int(request, "cursor", None, lo=0))


async def poll_events(services: Any, request: web.Request) -> web.Response:
    """``GET /events/poll`` — the same ring for a client without ``EventSource``."""
    cursor = query_int(request, "cursor", None, lo=0)
    limit = query_int(request, "limit", None, lo=1, hi=C.EVENTS_RING_SIZE)
    return json_ok(await services.events.poll(cursor, limit or 500))


async def get_activity(services: Any, request: web.Request) -> web.Response:
    """``GET /activity`` — Studio's rows, or the merged timeline when a repo and an intent are named.

    The merge is per intent because the AI-DLC half comes from that intent's own audit shards, and it has no
    cursor: only one of its two authorities has stable row ids, so the client pages by narrowing ``until``.
    """
    # Every parameter is validated up front, so a bad filter is a 400 on either path.
    q = ActivityQuery(
        repo_id=query_str(request, "repo"),
        space=query_str(request, "space"),
        source=query_str(request, "source", choices=C.SOURCE),
        stage=query_str(request, "stage"),
        kind=query_str(request, "kind"),
        action_type=query_str(request, "type", choices=C.ACTION_TYPE),
        severity=query_str(request, "severity", choices=C.SEVERITY),
        since=query_str(request, "since"),
        until=query_str(request, "until"),
        cursor=query_str(request, "cursor"),
        limit=query_int(request, "limit", C.ACTIVITY_DEFAULT_LIMIT, lo=1, hi=C.ACTIVITY_MAX_LIMIT)
        or C.ACTIVITY_DEFAULT_LIMIT,
    )
    intent = query_str(request, "intent")
    if intent:
        intent_space, intent_dir = parse_intent_key(intent)
        q = dataclasses.replace(q, space=q.space or intent_space, intent_dir=intent_dir)

    if q.repo_id and q.intent_dir:
        repo = await repo_or_404(services, q.repo_id)
        snap = await asyncio.to_thread(services.projection.snapshot, repo, q.space or C.DEFAULT_SPACE, q.intent_dir)
        entries = await services.activity.timeline(snap, limit=q.limit, sources=[q.source] if q.source else None)
        return json_ok({"items": [entry.to_json() for entry in entries], "next_cursor": None})

    rows, next_cursor = await services.activity.query(q)
    return json_ok({"items": [TimelineEntry.of_row(row).to_json() for row in rows], "next_cursor": next_cursor})


def ROUTES(services: Any) -> list[Any]:
    return [
        route("GET", "/events", stream_events, services=services),
        route("GET", "/events/poll", poll_events, services=services),
        route("GET", "/activity", get_activity, services=services),
    ]
