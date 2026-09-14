"""The Action Center routes (§2.4) — the queue, and the two-phase human lane.

Every route here is a thin adapter over ``HumanActionBroker``: the refusals, the compare-and-submit
set, the delivery proof and the transition graph all live in that module, and duplicating any of them
here would create a second opinion about whether a decision may be sent. What the handlers own is the
HTTP shape, and one rule that is theirs alone:

**``submit`` returns the browser's next call; it never makes it.** The backend commits the durable
``Delivering`` record and hands back ``receipt.host``. The send happens from the user's own dashboard
session, so no gateway-private dispatch symbol appears anywhere in this package.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

from .. import constants as C
from ..errors import StudioError
from ..projection import ORGANIZE_MODES, Projection
from .common import cards_for, json_ok, parse_intent_key, query_int, query_str, read_json, route

#: Priority band → i18n label. The four bands are the queue's own vocabulary (FR-ACT-003) and the UI
#: catalogue has one entry each (`queue.group.*`, §3.3).
GROUP_LABEL_KEYS = {1: "queue.group.recovery", 2: "queue.group.blocking", 3: "queue.group.attention",
                    4: "queue.group.info"}

#: Cap for one queue page. High enough that no real registry pages (a 64-repo install with a card per
#: intent is ~256), low enough that a runaway derivation cannot produce a 10 MB response.
MAX_ACTIONS = 500


async def list_actions(services: Any, request: web.Request) -> web.Response:
    """``GET /actions`` — the queue, sorted and grouped exactly as ``ui/src/lib/sort.ts`` will.

    Sorted server-side even though the client re-sorts: the two comparators are pinned against each
    other by tests, and a client that has not loaded its locale yet must still show the same order as
    one that has.
    """
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

    records = await services.actions.list_live(
        repo_id=repo_id,
        space=space,
        intent_dir=intent_dir,
        include_revision=include == "revision",
    )
    if type_filter:
        records = [rec for rec in records if rec.type == type_filter]
    if status_filter:
        records = [rec for rec in records if rec.status == status_filter]

    cards = Projection.sort_actions(await cards_for(services, records[:limit]), organize)

    groups: list[dict[str, Any]] = []
    for card in cards:
        key = Projection.group_key(card, organize)
        if not groups or groups[-1]["key"] != key:
            groups.append({"key": key, "label_key": _label_key(key, organize, card), "action_ids": []})
        groups[-1]["action_ids"].append(card["action_id"])

    counts = {"total": len(cards), "critical": 0, "blocking": 0, "attention": 0, "info": 0}
    for card in cards:
        severity = str(card.get("severity") or "info")
        if severity in counts:
            counts[severity] += 1
    return json_ok(
        {
            "actions": cards,
            "organize": organize,
            "groups": groups,
            "counts": counts,
            "generated_at": services.clock.iso(),
        }
    )


async def get_action(services: Any, request: web.Request) -> web.Response:
    """``GET /actions/{action_id}`` — the card, its transition history and its advisor drafts.

    The history is the audit trail of Studio's own decisions about this action, which is what a user
    reads when a delivery went uncertain. It is served verbatim from ``action_transitions`` — a derived
    summary would hide the generation numbers that explain why a CAS was refused.
    """
    action_id = request.match_info["action_id"]
    rec = await services.actions.get(action_id)
    cards = await cards_for(services, [rec])
    rows = await asyncio.to_thread(
        services.storage.select, "action_transitions", {"action_id": action_id}, order_by="id ASC"
    )
    drafts = await services.advisor.list_for_action(action_id)
    return json_ok(
        {
            "action": cards[0],
            "transitions": [
                {
                    "from_status": row.get("from_status"),
                    "to_status": row.get("to_status"),
                    "generation": int(row.get("generation") or 0),
                    "at": row.get("at"),
                    "reason": row.get("reason"),
                    "evidence": row.get("evidence_json") or {},
                }
                for row in rows
            ],
            "drafts": [draft.to_json() for draft in drafts],
        }
    )


async def submit_action(services: Any, request: web.Request) -> web.Response:
    """``POST /actions/{action_id}/submit`` — phase one of the human lane.

    Returns 200 with the ``Delivering`` record already durable, because the browser must not be able to
    send before the record exists (PRD §11.5): if the gateway died between the two, an action with no
    row would be a decision nobody can account for.
    """
    action_id = request.match_info["action_id"]
    body = await read_json(request, required=("payload",))
    captured = body.get("captured") or {}
    if not isinstance(captured, dict):
        raise StudioError("bad_body", "captured must be an object", details={"reason": "not_an_object"})
    payload = body.get("payload")
    if not isinstance(payload, dict) or not payload.get("decision"):
        raise StudioError("bad_body", "payload must name a decision", details={"missing": ["decision"]})
    receipt = await services.actions.submit(
        action_id,
        captured=captured,
        payload=payload,
        client_wire_text=str(body.get("client_wire_text") or ""),
        user=str(request.get("user") or ""),
    )
    return json_ok(receipt.to_json())


async def report_delivery(services: Any, request: web.Request) -> web.Response:
    """``POST /actions/{action_id}/delivery`` — phase three: what the browser observed.

    ``not_delivered`` is not taken on the client's word here or anywhere: the broker re-proves absence
    from its own evidence and answers ``not_delivered_unproven`` when it cannot (C24).
    """
    action_id = request.match_info["action_id"]
    body = await read_json(request, required=("delivery_id", "outcome"))
    rec, idempotent = await services.actions.record_delivery(
        action_id,
        delivery_id=str(body["delivery_id"]),
        outcome=str(body["outcome"]),
        http_status=_optional_int(body.get("http_status")),
        receipt=body.get("receipt") if isinstance(body.get("receipt"), dict) else None,
    )
    cards = await cards_for(services, [rec])
    return json_ok({"ok": True, "action": cards[0], "idempotent": bool(idempotent)})


async def retry_action(services: Any, request: web.Request) -> web.Response:
    """``POST /actions/{action_id}/retry`` — re-queue a proven-undelivered decision, or start afresh."""
    rec = await services.actions.retry(
        request.match_info["action_id"], user=str(request.get("user") or "")
    )
    cards = await cards_for(services, [rec])
    return json_ok({"ok": True, "action": cards[0]})


async def reconcile_action(services: Any, request: web.Request) -> web.Response:
    """``POST /actions/{action_id}/reconcile`` — run the reconciler against one row now.

    Exists so a user staring at an uncertain delivery does not have to wait for the next tick. It
    decides nothing itself: the reconciler applies the same resolution contracts it would have applied
    five seconds later.
    """
    action_id = request.match_info["action_id"]
    await services.actions.get(action_id)  # 404 before any work
    await services.reconciler.reconcile_now(action_id)
    rec = await services.actions.get(action_id)
    cards = await cards_for(services, [rec])
    return json_ok({"ok": True, "action": cards[0]})


async def cancel_action(services: Any, request: web.Request) -> web.Response:
    """``POST /actions/{action_id}/cancel`` — withdraw a card, where withdrawing loses nothing."""
    await read_json(request)
    rec = await services.actions.cancel(
        request.match_info["action_id"], user=str(request.get("user") or "")
    )
    cards = await cards_for(services, [rec])
    return json_ok({"ok": True, "action": cards[0]})


async def resolve_action(services: Any, request: web.Request) -> web.Response:
    """``POST /actions/{action_id}/resolve`` — a decision that never leaves Studio.

    The studio-only lane: acknowledge a recovery, mark a delivery undelivered (against proof),
    resubmit after reconciliation, switch the cursor. None of them sends a prompt, which is why they
    are a separate route from ``submit`` rather than a decision kind inside it.
    """
    body = await read_json(request, required=("decision",))
    payload = body.get("payload") or {}
    if not isinstance(payload, dict):
        raise StudioError("bad_body", "payload must be an object", details={"reason": "not_an_object"})
    rec, created = await services.actions.resolve(
        request.match_info["action_id"],
        decision=str(body["decision"]),
        payload=payload,
        user=str(request.get("user") or ""),
    )
    cards = await cards_for(services, [rec])
    return json_ok(
        {
            "ok": True,
            "action": cards[0],
            "created_action_id": created.action_id if created is not None else None,
        }
    )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _label_key(key: str, organize: str, card: dict) -> str:
    """The i18n key for a group header.

    Only the priority bands have their own catalogue entries; ``repo`` and ``type`` groups are named by
    data the card already carries, so the key points the UI at the generic header and the ``key`` field
    supplies the value.

    A ``type`` group is named by the shared action-type enum rather than a private ``action.<type>.label``
    namespace. Two reasons: the enum catalogue is generated from ``ACTION_TYPE`` so it cannot drift out of
    coverage, and ``ui/src/actions/QueueGroups.tsx`` already renders type headings from it. Sending a key
    the catalogue does not own would put a raw dotted string in front of any client that trusts it.
    """
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
        raise StudioError(
            "bad_body", "http_status must be an integer", details={"reason": "http_status"}
        ) from None


def ROUTES(services: Any) -> list[Any]:
    return [
        route("GET", "/actions", list_actions, services=services),
        route("GET", "/actions/{action_id}", get_action, services=services),
        route("POST", "/actions/{action_id}/submit", submit_action, owner=True, services=services),
        route("POST", "/actions/{action_id}/delivery", report_delivery, owner=True, services=services),
        route("POST", "/actions/{action_id}/retry", retry_action, owner=True, services=services),
        route("POST", "/actions/{action_id}/reconcile", reconcile_action, owner=True, services=services),
        route("POST", "/actions/{action_id}/cancel", cancel_action, owner=True, services=services),
        route("POST", "/actions/{action_id}/resolve", resolve_action, owner=True, services=services),
    ]
