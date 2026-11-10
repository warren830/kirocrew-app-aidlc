"""At-most-once for the human lane (§11.5 D27, C24): a decision recorded as sent is never declared "never sent"."""

from __future__ import annotations

import asyncio

from _helpers_a import aio, dispatches, refused
from test_actions import E, SLOT, delivering, report, repo_options, world  # noqa: F401 - fixtures
from test_e2e_gate import (  # noqa: F401 - fixtures
    APPROVE, INTENT, _get, _post, _report, _row, _submit, repo_dir, scene, submitted,
)
from test_handlers_auth import common, routes, sv  # noqa: F401 - fixtures


def test_a_confirmed_delivery_can_never_be_recorded_as_never_sent(sv, routes, fake_host, scene, studio, clock,
                                                                  submitted):
    """The host accepted, the report was lost: ``mark_not_delivered`` (followed by Retry) must refuse any receipt."""
    row = _row(sv, submitted.action_id)
    # The user row the host writes for a prompt it accepted, with Studio's own delivery meta.
    fake_host.get_slot(scene.slot_key).messages.append({
        "role": "user", "content": APPROVE, "cls": "msg msg-u", "ts": row["delivering_at"],
        "meta": {"studio_delivery_id": submitted.receipt["delivery_id"]}})
    fake_host.get_slot(scene.slot_key).running = True  # the conductor is working on it
    # No report ever arrives: two ticks past the ack deadline is the whole crash.
    clock.advance(studio.constants.DELIVERY_ACK_DEADLINE_SECS + 30)
    asyncio.run(sv.reconciler.tick())
    asyncio.run(sv.reconciler.tick())
    row = _row(sv, submitted.action_id)
    evidence = dict(row["evidence_json"] or {})
    assert row["status"] == "ReconciliationRequired"
    assert evidence["delivery_confirmed"] is True, evidence
    assert evidence["transcript_row"] is not None

    status, body = _post(sv, routes, fake_host, f"/actions/{submitted.action_id}/resolve", {
        "decision": "mark_not_delivered",
        "payload": {"evidence": {"http_status": 409, "receipt": {"error": "slot agent mismatch"}}}})
    assert status == 409, body
    assert body["code"] == "not_delivered_unproven"
    assert _row(sv, submitted.action_id)["status"] == "ReconciliationRequired"
    assert dispatches(sv.storage, submitted.action_id) == 1


def test_a_late_delivered_report_closes_submit_and_not_only_retry(sv, routes, fake_host, submitted):
    """A proven non-delivery contradicted by a later delivered report: ``submit`` must refuse like ``retry``."""
    action_id, receipt = submitted.action_id, submitted.receipt
    # The host's own 4xx, with all four proofs of absence holding.
    status, proven = _report(sv, routes, fake_host, action_id, receipt, outcome="not_delivered", http_status=409,
                             body={"error": "slot agent mismatch"})
    assert status == 200, proven
    assert _row(sv, action_id)["status"] == "NotDelivered"
    # A second, later report says it landed after all (a retried POST, a service worker replay).
    status, late = _report(sv, routes, fake_host, action_id, receipt, outcome="delivered")
    assert status == 200, late
    assert dict(_row(sv, action_id)["evidence_json"] or {})["delivery_confirmed"] is True

    fresh = [c for c in _get(sv, routes, fake_host, "/actions")[1]["actions"] if c["action_id"] == action_id]
    status, again = _submit(sv, routes, fake_host, fresh[0] if fresh else submitted.card)
    assert status == 409, again
    assert again["code"] in ("action_not_submittable", "not_delivered_unproven"), again
    assert dispatches(sv.storage, action_id) == 1


def test_a_force_stop_a_late_report_confirmed_settles_as_the_interruption_it_was(sv, routes, fake_host, scene):
    """The host refused the stop, then its own late ok said it took it after all: the stop settles as a delivered stop
    does, and the intent records the interruption a manual takeover needs (C28), instead of a "Not sent" card."""
    path = f"/repos/{scene.repo_id}/intents/{INTENT}/force-stop"
    status, receipt = _post(sv, routes, fake_host, path, {"confirm": True})
    assert status == 201, receipt
    action_id = receipt["action_id"]
    status, body = _report(sv, routes, fake_host, action_id, receipt, outcome="not_delivered", http_status=404,
                           body={"error": "stop refused"})
    assert status == 200 and _row(sv, action_id)["status"] == "NotDelivered", body
    status, body = _report(sv, routes, fake_host, action_id, receipt, body={"ok": True})
    assert status == 200 and body["action"]["status"] == "ReconciliationRequired", body
    asyncio.run(sv.reconciler.tick())
    row = _row(sv, action_id)
    assert row["status"] == "ResolvedNoTransition", row["status"]
    assert asyncio.run(sv.sessions.get(scene.repo_id, "default", INTENT)).interrupted_at is not None
    assert dispatches(sv.storage, action_id) == 1


def test_force_stop_pressed_again_while_the_next_queued_turn_runs_reaches_the_host(sv, routes, fake_host, scene):
    """The host took the stop and its queue's next message started at once (KiroCrew keeps the queue on a stop), so
    the slot never reads idle. Pressing again must still reach the host as its own second press does: a new delivery
    in the force form, one stop call for the one press, and the first stop settled as the interruption it was."""
    slot = fake_host.get_slot(scene.slot_key)
    slot.running = True
    path = f"/repos/{scene.repo_id}/intents/{INTENT}/force-stop"
    status, first = _post(sv, routes, fake_host, path, {"confirm": True})
    assert status == 201, first
    status, body = _report(sv, routes, fake_host, first["action_id"], first, body={"ok": True})
    assert status == 200, body
    for _ in range(3):
        asyncio.run(sv.reconciler.tick())
    assert _row(sv, first["action_id"])["status"] == "Delivered"
    status, again = _post(sv, routes, fake_host, path, {"confirm": True})
    assert status == 201, again
    assert again["action_id"] != first["action_id"] and again["delivery_id"] != first["delivery_id"]
    assert again["host"]["path"] == f"/api/chat/slots/{scene.slot_key}/stop?force=true"
    settled = _row(sv, first["action_id"])
    assert (settled["status"], settled["resolution_json"]["reason"]) == ("ResolvedNoTransition", "stopped_again")
    assert asyncio.run(sv.sessions.get(scene.repo_id, "default", INTENT)).interrupted_at is not None
    assert dispatches(sv.storage, first["action_id"]) == 1


class DriftedHost:
    """A gateway whose slot table cannot be read (§7.0 drift): a renamed attribute costs a capability, not a proof."""

    def __init__(self, real) -> None:
        self.sessions = real.sessions
        self.owner_id = real.owner_id
        self.slack_client = None
        self.subagents = None

    def notify(self, *args, **kwargs) -> None:  # pragma: no cover - never called here
        pass


@aio
async def test_an_unreadable_slot_table_is_never_proof_that_nothing_was_sent(world, E):
    """Same disk, same transcript row: only Studio's eyesight changes, so the four proofs must still refuse."""
    rec, receipt = await delivering(world)
    world.fake_host.get_slot(SLOT).messages.append({
        "role": "user", "content": rec.wire_text, "cls": "msg msg-u", "ts": rec.delivering_at,
        "meta": {"studio_delivery_id": receipt.delivery_id}})
    with refused(E, "not_delivered_unproven") as exc:
        await report(world, rec, receipt, "not_delivered", 409, {"code": "slot_missing", "error": "no such slot"})
    assert exc.value.details["checks"]["row_absent"] is False

    # The refused claim already handed the decision to a human; a late claim meets the same proofs.
    escalated = await world.broker.get(rec.action_id)
    assert escalated.status == "ReconciliationRequired"
    world.host.attach(DriftedHost(world.fake_host))
    assert world.host.capabilities()["slots"].available is False
    with refused(E, "not_delivered_unproven") as exc:
        await report(world, escalated, receipt, "not_delivered", 409, {"code": "slot_missing", "error": "no such slot"})
    assert exc.value.details["checks"]["row_absent"] is False, "unreadable is not absent"
    assert (await world.broker.get(escalated.action_id)).status != "NotDelivered"
