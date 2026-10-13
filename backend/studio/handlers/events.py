"""Live updates (§2.5, §2.12) and the timeline (§2.6).

The stream and its cursor rules are ``EventLog``'s; a second implementation of "did this client lose
history" here would eventually disagree with the first.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from aiohttp import web

from .. import constants as C
from ..activity import TimelineEntry
from ..storage import ActivityQuery
from .common import json_ok, parse_intent_key, query_int, query_str, repo_or_404


async def stream(services: Any, request: web.Request) -> web.StreamResponse:
    """SSE, replaying from ``cursor`` (or ``Last-Event-ID``) when it can be resumed."""
    return await services.events.stream(request, cursor=query_int(request, "cursor", None, lo=0))


async def poll(services: Any, request: web.Request) -> web.Response:
    """The same ring, for a client without ``EventSource``."""
    cursor = query_int(request, "cursor", None, lo=0)
    limit = query_int(request, "limit", None, lo=1, hi=C.EVENTS_RING_SIZE)
    return json_ok(await services.events.poll(cursor, limit or 500))


async def activity(services: Any, request: web.Request) -> web.Response:
    """Studio's rows, or one intent's merged timeline when ``repo`` and ``intent`` are named.

    The merge reads that intent's own audit shards, and it has no cursor: only one of its two authorities
    has stable row ids, so the client pages by narrowing ``until``. Every filter is validated first, so a
    bad one is a 400 in either mode.
    """
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
        return json_ok({"items": await _named(services, [entry.to_json() for entry in entries]), "next_cursor": None})

    rows, next_cursor = await services.activity.query(q)
    return json_ok({"items": await _named(services, [TimelineEntry.of_row(row).to_json() for row in rows]),
                    "next_cursor": next_cursor})


async def _named(services: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Name each row's repository, and drop links to actions that are gone.

    Unregistering a repository deletes its actions and keeps its Activity rows: such a row then names no
    repository label, and offers no link to a card that can no longer open.
    """
    repo_ids = sorted({item["refs"]["repo"]["repo_id"] for item in items if "repo" in item["refs"]})
    action_ids = sorted({item["refs"]["action_id"] for item in items if "action_id" in item["refs"]})
    repos = await asyncio.to_thread(services.storage.select, "repos", {"id": repo_ids}) if repo_ids else []
    actions = await asyncio.to_thread(services.storage.select, "actions", {"action_id": action_ids}) if action_ids else []
    labels = {str(row.get("id")): row.get("label") for row in repos}
    live = {str(row.get("action_id")) for row in actions}
    for item in items:
        refs = item["refs"]
        if "repo" in refs:
            refs["repo"]["label"] = labels.get(refs["repo"]["repo_id"])
        if "action_id" in refs and refs["action_id"] not in live:
            refs.pop("action_id")
    return items
