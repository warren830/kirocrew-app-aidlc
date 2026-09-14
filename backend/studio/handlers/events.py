"""Live updates (§2.5/§2.12) and the merged timeline (§2.6).

``GET /events`` is the only long-lived response Studio serves. The stream itself is ``EventLog``'s (it
owns the ring, the cursor semantics and the reset frame); the handler's whole job is to parse the
cursor and hand the request over, because a second implementation of "did this client lose history"
would eventually disagree with the first.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

from .. import constants as C
from ..activity import TimelineEntry
# The refs rule for a Studio row lives in one place; a second copy here would drift from the merged
# timeline's version and the Evidence drawer would then show different links for the same row
# depending on which route fetched it.
from ..activity import studio_refs
from ..storage import ActivityQuery
from .common import json_ok, parse_intent_key, query_int, query_str, repo_or_404, route


async def stream_events(services: Any, request: web.Request) -> web.StreamResponse:
    """``GET /events`` — SSE, replaying from ``cursor`` (or ``Last-Event-ID``) when it can be honoured."""
    cursor = query_int(request, "cursor", None, lo=0)
    return await services.events.stream(request, cursor=cursor)


async def poll_events(services: Any, request: web.Request) -> web.Response:
    """``GET /events/poll`` — the same ring for a client without ``EventSource``.

    A missing cursor is a cold start and returns no backlog: the client is about to fetch every
    resource anyway, and replaying 10 000 events into a page that has no state yet is pure cost.
    """
    cursor = query_int(request, "cursor", None, lo=0)
    limit = query_int(request, "limit", None, lo=1, hi=C.EVENTS_RING_SIZE)
    body = await services.events.poll(cursor, limit or 500)
    return json_ok(body)


async def get_activity(services: Any, request: web.Request) -> web.Response:
    """``GET /activity`` — Studio's rows, or the merged intent timeline when a repo and intent are named.

    The merge only happens for one intent because the AI-DLC half comes from that intent's own audit
    shards: merging across a registry would mean reading every repository on the event loop's behalf for
    a list nobody asked to be complete.
    """
    repo_id = query_str(request, "repo")
    intent = query_str(request, "intent")
    space = query_str(request, "space")
    source = query_str(request, "source", choices=C.SOURCE)
    stage = query_str(request, "stage")
    kind = query_str(request, "kind")
    action_type = query_str(request, "type", choices=C.ACTION_TYPE)
    severity = query_str(request, "severity", choices=C.SEVERITY)
    since = query_str(request, "since")
    until = query_str(request, "until")
    cursor = query_str(request, "cursor")
    limit = query_int(
        request, "limit", C.ACTIVITY_DEFAULT_LIMIT, lo=1, hi=C.ACTIVITY_MAX_LIMIT
    ) or C.ACTIVITY_DEFAULT_LIMIT

    intent_dir = None
    if intent:
        intent_space, intent_dir = parse_intent_key(intent)
        space = space or intent_space

    if repo_id and intent_dir:
        repo = await repo_or_404(services, repo_id)
        snap = await asyncio.to_thread(
            services.projection.snapshot, repo, space or C.DEFAULT_SPACE, intent_dir
        )
        entries = await services.activity.timeline(
            snap, limit=limit, sources=[source] if source else None
        )
        # No cursor: the merged view has two authorities and only one of them has stable row ids, so a
        # cursor would be a promise this route cannot keep. The client pages by narrowing `until`.
        return json_ok({"items": [entry.to_json() for entry in entries], "next_cursor": None})

    rows, next_cursor = await services.activity.query(
        ActivityQuery(
            repo_id=repo_id,
            space=space,
            intent_dir=intent_dir,
            source=source,
            stage=stage,
            kind=kind,
            action_type=action_type,
            severity=severity,
            since=since,
            until=until,
            cursor=cursor,
            limit=limit,
        )
    )
    return json_ok(
        {"items": [_entry_of(row).to_json() for row in rows], "next_cursor": next_cursor}
    )


def _entry_of(row: Any) -> TimelineEntry:
    """One Studio activity row as a ``TimelineEntry``, so both shapes of ``/activity`` agree.

    ``stage`` is lifted into ``params`` for the same reason the merged timeline does it: it is a column
    rather than a param, and a translation that mentions the stage must work identically either way.
    """
    params = dict(row.params)
    if row.stage and "stage" not in params:
        params["stage"] = row.stage
    return TimelineEntry(
        at=row.at,
        source=row.source,
        kind=row.kind,
        message_key=row.message_key,
        params=params,
        severity=row.severity,
        refs=studio_refs(row),
        raw=None,
        id=row.id,
    )


def ROUTES(services: Any) -> list[Any]:
    return [
        route("GET", "/events", stream_events, services=services),
        route("GET", "/events/poll", poll_events, services=services),
        route("GET", "/activity", get_activity, services=services),
    ]
