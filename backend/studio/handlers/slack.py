"""``POST /slack/actions/callback`` (§2.9) — a correlation hint, and nothing else.

It authorises nothing and changes no action status: the UI reports which Slack thread a card was discussed in
so the Evidence drawer can point back at it. Owner-only like every mutation, with no internal-secret mode —
the host honours ``X-Internal-Secret`` only on its own paths, never ``/api/apps/*`` (review R09, C24). A Slack
click that should decide something would need the S7 host seam and the whole two-phase record, not this route.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from ..errors import StaleGeneration, StudioError
from .common import json_ok, read_json, route

_LOG = logging.getLogger("kirocrew.app.aidlc-studio")

#: §2.9's kind, and a member of ``activity.ACTIVITY_KINDS``.
ACTIVITY_KIND = "slack.callback"


async def record_callback(services: Any, request: web.Request) -> web.Response:
    """Record the thread on the action (through its CAS: ``actions`` is contended, and a blind write could
    clobber a delivery receipt landing at the same moment) and on the timeline. An unknown id is a 404."""
    body = await read_json(request, required=("action_id",))
    action_id = str(body["action_id"])
    rec = await services.actions.get(action_id)

    hint = {key: _text(body.get(key)) for key in ("slot_key", "channel", "thread_ts", "message_ts")}
    hint["selection"] = _text(body.get("selection")) or None
    hint["recorded_at"] = services.clock.iso()
    try:
        await asyncio.to_thread(
            services.storage.cas_update, "actions", action_id, rec.status_generation,
            {"evidence_json": {**rec.evidence, "slack": hint}},
        )
    except StaleGeneration:
        pass  # the row that won the CAS is at least as new as this hint, which is not worth a refusal

    try:
        await services.activity.record(
            kind=ACTIVITY_KIND, severity="info", source="slack", repo_id=rec.repo_id, space=rec.space,
            intent_dir=rec.intent_dir, stage=rec.stage, action_id=action_id,
            params={key: value for key, value in hint.items() if value},
        )
    except StudioError as exc:
        # The durable record is the action's own evidence, already written; a lost timeline row must not fail
        # a request that succeeded at the thing it exists for.
        _LOG.warning("aidlc-studio: slack callback not recorded in activity: %s", exc.code)
    return json_ok({"ok": True, "recorded": True})


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def ROUTES(services: Any) -> list[Any]:
    return [
        route("POST", "/slack/actions/callback", record_callback, owner=True, services=services),
    ]
