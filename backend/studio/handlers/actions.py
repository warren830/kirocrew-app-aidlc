"""The Action Center (§2.4): the queue and the two-phase human lane.

The refusals, the compare-and-submit set, the delivery proof and the transition graph all live in
``HumanActionBroker``; a second copy here would be a second opinion about whether a decision may be sent.
``submit`` returns the browser's next call and never makes it: the send happens from the user's own
dashboard session, so no gateway-private dispatch symbol appears in this package.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

from .. import constants as C
from ..errors import StudioError
from ..projection import ORGANIZE_MODES, Projection
from .common import card_of, cards_for, json_ok, parse_intent_key, query_int, query_str, read_json, user_of, \
    without_archived

#: Priority band → its header in the UI catalogue (FR-ACT-003, ``queue.group.*``).
GROUP_LABEL_KEYS = {1: "queue.group.recovery", 2: "queue.group.blocking", 3: "queue.group.attention",
                    4: "queue.group.info"}

#: No real registry pages (64 repositories with a card per intent is ~256); a runaway derivation cannot
#: produce a 10 MB answer.
MAX_ACTIONS = 500


async def list_actions(services: Any, request: web.Request) -> web.Response:
    """Sorted and grouped server-side as ``ui/src/lib/sort.ts`` does (the comparators are pinned against each
    other), so a client that has not loaded its locale yet shows the same order as one that has."""
    organize = query_str(request, "organize", "priority", choices=ORGANIZE_MODES) or "priority"
    repo_id = query_str(request, "repo")
    space = query_str(request, "space")
    intent = query_str(request, "intent")
    type_filter = query_str(request, "type", choices=C.ACTION_TYPE)
    status_filter = query_str(request, "status", choices=C.ACTION_STATUS)
    include = query_str(request, "include")
    limit = query_int(request, "limit", MAX_ACTIONS, lo=1, hi=MAX_ACTIONS) or MAX_ACTIONS

    intent_dir = None
    if intent:
        intent_space, intent_dir = parse_intent_key(intent)
        space = space or intent_space
    records = await services.actions.list_live(repo_id=repo_id, space=space, intent_dir=intent_dir,
                                               include_revision=include == "revision")
    if not intent_dir:
        # An archived intent's cards are set aside with it (§11.2); naming the intent still shows them.
        records = await without_archived(services, records)
    records = [rec for rec in records if (not type_filter or rec.type == type_filter)
               and (not status_filter or rec.status == status_filter)]
    cards = Projection.sort_actions(await cards_for(services, records[:limit]), organize)

    groups: list[dict[str, Any]] = []
    counts = {"total": len(cards), "critical": 0, "blocking": 0, "attention": 0, "info": 0}
    for card in cards:
        key = Projection.group_key(card, organize)
        if not groups or groups[-1]["key"] != key:
            groups.append({"key": key, "label_key": _label_key(key, organize, card), "action_ids": []})
        groups[-1]["action_ids"].append(card["action_id"])
        severity = str(card.get("severity") or "info")
        if severity in counts:
            counts[severity] += 1
    return json_ok({
        "actions": cards,
        "organize": organize,
        "groups": groups,
        "counts": counts,
        "approval_waits": await _in_scope(services, await services.sessions.waiting_approvals(), repo_id, space,
                                          intent_dir),
        "stopped_turns": await _in_scope(services, await services.sessions.stopped_turns(), repo_id, space,
                                         intent_dir),
        "generated_at": services.clock.iso(),
    })


async def _in_scope(services: Any, waits: list[dict[str, Any]], repo_id: str | None, space: str | None,
                    intent_dir: str | None) -> list[dict[str, Any]]:
    """Conversations parked on a host tool approval, or stopped on a host error, in the queue's filter, with
    their repository's label. Not cards: the host answers an approval in the conversation (A31), and a stopped
    turn waits on no decision; they are listed so a run that stopped is seen with every other wait."""
    out: list[dict[str, Any]] = []
    for wait in waits:
        if (repo_id and wait["repo_id"] != repo_id) or (space and wait["space"] != space) \
                or (intent_dir and wait["intent_dir"] != intent_dir):
            continue
        try:
            repo = await asyncio.to_thread(services.repos.get, wait["repo_id"])
        except StudioError:
            continue
        out.append({**wait, "repo_label": repo.label})
    return out


async def get_action(services: Any, request: web.Request) -> web.Response:
    """The card, its transition history verbatim (the generation numbers explain a refused CAS) and its
    advisor drafts."""
    action_id = request.match_info["action_id"]
    rec = await services.actions.get(action_id)
    rows = await asyncio.to_thread(services.storage.select, "action_transitions", {"action_id": action_id},
                                   order_by="id ASC")
    drafts = await services.advisor.list_for_action(action_id)
    return json_ok({
        "action": await card_of(services, rec),
        "transitions": [
            {"from_status": row.get("from_status"), "to_status": row.get("to_status"),
             "generation": int(row.get("generation") or 0), "at": row.get("at"), "reason": row.get("reason"),
             "evidence": row.get("evidence_json") or {}}
            for row in rows
        ],
        "drafts": [draft.to_json() for draft in drafts],
    })


async def submit(services: Any, request: web.Request) -> web.Response:
    """Phase one: the ``Delivering`` record is durable before the answer, so the browser cannot send a
    decision nobody can account for if the gateway dies in between (PRD §11.5)."""
    body = await read_json(request, required=("payload",))
    captured = body.get("captured") or {}
    if not isinstance(captured, dict):
        raise StudioError("bad_body", "captured must be an object", details={"reason": "not_an_object"})
    payload = body.get("payload")
    if not isinstance(payload, dict) or not payload.get("decision"):
        raise StudioError("bad_body", "payload must name a decision", details={"missing": ["decision"]})
    receipt = await services.actions.submit(request.match_info["action_id"], captured=captured, payload=payload,
                                            client_wire_text=str(body.get("client_wire_text") or ""),
                                            user=user_of(request))
    return json_ok(receipt.to_json())


async def delivery(services: Any, request: web.Request) -> web.Response:
    """Phase three: what the browser observed. ``not_delivered`` is never taken on the client's word — the
    broker re-proves absence and answers ``not_delivered_unproven`` when it cannot (C24)."""
    body = await read_json(request, required=("delivery_id", "outcome"))
    rec, idempotent = await services.actions.record_delivery(
        request.match_info["action_id"],
        delivery_id=str(body["delivery_id"]),
        outcome=str(body["outcome"]),
        http_status=_optional_int(body.get("http_status")),
        receipt=body.get("receipt") if isinstance(body.get("receipt"), dict) else None,
    )
    return json_ok({"ok": True, "action": await card_of(services, rec), "idempotent": bool(idempotent)})


async def resolve(services: Any, request: web.Request) -> web.Response:
    """The studio-only lane (acknowledge, mark undelivered against proof, reconcile, resubmit, switch the
    cursor): none of them sends a prompt, which is why they are not decision kinds of ``submit``."""
    body = await read_json(request, required=("decision",))
    payload = body.get("payload") or {}
    if not isinstance(payload, dict):
        raise StudioError("bad_body", "payload must be an object", details={"reason": "not_an_object"})
    rec, created = await services.actions.resolve(request.match_info["action_id"], decision=str(body["decision"]),
                                                  payload=payload, user=user_of(request))
    return json_ok({"ok": True, "action": await card_of(services, rec),
                    "created_action_id": created.action_id if created is not None else None})


def _label_key(key: str, organize: str, card: dict) -> str:
    """Only the priority bands have their own headers; a ``repo`` or ``type`` group is named by its ``key``.
    A type heading uses the generated enum catalogue, the one the UI renders type headings from."""
    if organize == "priority":
        return GROUP_LABEL_KEYS.get(int(card.get("priority_group") or 4), GROUP_LABEL_KEYS[4])
    if organize == "repo":
        return "queue.group.repo"
    if organize == "type":
        return f"enum.actionType.{key}"
    return "queue.group.oldest"


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise StudioError("bad_body", "http_status must be an integer", details={"reason": "http_status"}) from None
