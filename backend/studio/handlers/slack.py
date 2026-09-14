"""``POST /slack/actions/callback`` (§2.9) — a correlation hint, and nothing else.

This route authorises nothing and changes no action status. It exists because the UI opens a Slack
deep link and can then tell Studio which thread the conversation ended up in, so the Evidence drawer
can point back at it.

It is owner-only like every other mutation. There is deliberately no internal-secret mode: the host
honours ``X-Internal-Secret`` only for its own internal paths and never for ``/api/apps/*``, so an
"internal caller" leg would be unreachable in production and would only widen the attack surface
(review R09, C24). If a Slack click ever needs to reach a decision, it will need the S7 host seam and
the whole two-phase record — not this route.
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
    """Record the Slack thread a card was discussed in.

    The action is fetched first so an unknown id is a 404 rather than an activity row about nothing,
    and the evidence write goes through the action CAS because ``actions`` is a contended row — a
    blind write here could clobber a delivery receipt landing at the same moment.
    """
    body = await read_json(request, required=("action_id",))
    action_id = str(body["action_id"])
    rec = await services.actions.get(action_id)

    hint = {
        "slot_key": _text(body.get("slot_key")),
        "channel": _text(body.get("channel")),
        "thread_ts": _text(body.get("thread_ts")),
        "message_ts": _text(body.get("message_ts")),
        "selection": _text(body.get("selection")) or None,
        "recorded_at": services.clock.iso(),
    }
    evidence = dict(rec.evidence)
    evidence["slack"] = hint
    try:
        await asyncio.to_thread(
            services.storage.cas_update,
            "actions",
            action_id,
            rec.status_generation,
            {"evidence_json": evidence},
        )
    except StaleGeneration:
        # Somebody moved the action while this hint was in flight. A correlation hint is not worth
        # refusing the request over, and the row that won the CAS is at least as new as this one.
        pass

    try:
        await services.activity.record(
            kind=ACTIVITY_KIND,
            severity="info",
            source="slack",
            repo_id=rec.repo_id,
            space=rec.space,
            intent_dir=rec.intent_dir,
            stage=rec.stage,
            action_id=action_id,
            params={key: value for key, value in hint.items() if value},
        )
    except StudioError as exc:
        # The durable record of this hint is the action's own evidence, written above; losing the
        # timeline row must not fail a request that already succeeded at the thing it exists for. Kept
        # after the kind was added to ``ACTIVITY_KINDS`` because the projector can also refuse for
        # reasons that have nothing to do with the vocabulary (a storage error, a redaction refusal).
        _LOG.warning("aidlc-studio: slack callback not recorded in activity: %s", exc.code)
    return json_ok({"ok": True, "recorded": True})


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def ROUTES(services: Any) -> list[Any]:
    return [
        route("POST", "/slack/actions/callback", record_callback, owner=True, services=services),
    ]
