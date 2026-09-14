"""Crash-injection and at-most-once proofs for the human lane (PRD §11.5 D27, C24).

Written from the adversary's side: each test constructs a state a real crash or a lost report leaves
behind and then asks Studio for the one answer it must never give — "this decision never reached the
conversation" — for a decision it has itself recorded as reaching the conversation.

The three properties are the ones the other suites do not cover:

1. ``mark_not_delivered`` is checked against Studio's own evidence, not against the receipt the client
   echoed back at it. A row whose ``evidence.delivery_confirmed`` is ``True`` (the reconciler saw the
   host's transcript row, or a new ``HUMAN_TURN`` on disk) can never become ``NotDelivered``:
   ``resubmit`` already refuses exactly that, and the reconciler's own ``NotDelivered`` path is guarded
   by the same flag.
2. ``NotDelivered`` is not a licence to dispatch when a later report confirmed delivery. ``retry``
   refuses; ``submit`` is the other door into ``Delivering`` and must refuse for the same reason.
3. An unreadable host slot table is missing evidence, never proof of absence — the rule
   ``reconciler._delivery_evidence`` applies (``transcript_row_absent`` is ``False`` when the slots
   cannot be read) has to hold in the broker's four proofs too.
"""

from __future__ import annotations

import asyncio

import pytest

import conftest as CT
from test_actions import A, C, captured_of, delivering, repo_options, world  # noqa: F401 - fixtures
from test_actions import SLOT
from test_e2e_gate import (  # noqa: F401 - fixtures
    APPROVE,
    INTENT,
    _gate_card,
    _get,
    _post,
    _report,
    _rescan,
    _row,
    _submit,
    repo_dir,
    scene,
)
from test_handlers_auth import APP_ROOT, BOOT, call, common, routes, sv  # noqa: F401 - fixtures


def run(coro):
    return asyncio.run(coro)


def _host_accepted(fake_host, slot_key: str, row: dict, delivery_id: str) -> None:
    """The user row the host writes for a prompt it accepted, with Studio's own delivery meta.

    This is the only host-side evidence a delivery leaves, and it is what the reconciler matches on.
    """
    fake_host.get_slot(slot_key).messages.append(
        {
            "role": "user",
            "content": APPROVE,
            "cls": "msg msg-u",
            "ts": row["delivering_at"],
            "meta": {"studio_delivery_id": delivery_id},
        }
    )


# --------------------------------------------------------------------------- #
# 1. a confirmed delivery can never be recorded as never sent
# --------------------------------------------------------------------------- #


def test_a_confirmed_delivery_can_never_be_recorded_as_never_sent(
    sv, routes, fake_host, scene, studio, clock
):
    """The browser sent, the host accepted, the report was lost. The decision is delivered.

    The reconciler says so on the row (``delivery_confirmed``), so the only honest exits are
    ``reconcile`` and reading the evidence. ``mark_not_delivered`` must refuse whatever receipt the
    caller claims to hold, because it is followed by ``Retry`` — and a replayed approval is a decision
    the human never made twice.
    """
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    status, receipt = _submit(sv, routes, fake_host, card)
    assert status == 200, receipt

    row = _row(sv, card["action_id"])
    _host_accepted(fake_host, scene.slot_key, row, receipt["delivery_id"])
    fake_host.get_slot(scene.slot_key).running = True  # the conductor is working on it

    # No report ever arrives. Two ticks past the ack deadline is the whole crash.
    clock.advance(studio.constants.DELIVERY_ACK_DEADLINE_SECS + 30)
    run(sv.reconciler.tick())
    run(sv.reconciler.tick())
    row = _row(sv, card["action_id"])
    evidence = dict(row["evidence_json"] or {})
    assert row["status"] == "ReconciliationRequired"
    assert evidence["delivery_confirmed"] is True, evidence
    assert evidence["transcript_row"] is not None

    status, body = _post(
        sv,
        routes,
        fake_host,
        f"/actions/{card['action_id']}/resolve",
        {
            "decision": "mark_not_delivered",
            "payload": {"evidence": {"http_status": 409, "receipt": {"error": "slot agent mismatch"}}},
        },
    )
    assert status == 409, body
    assert body["code"] == "not_delivered_unproven"
    assert _row(sv, card["action_id"])["status"] == "ReconciliationRequired"

    # And nothing was dispatched a second time.
    dispatches = [
        dict(t)
        for t in sv.storage.select("action_transitions", {"action_id": card["action_id"]})
        if dict(t)["to_status"] == "Delivering"
    ]
    assert len(dispatches) == 1, dispatches


# --------------------------------------------------------------------------- #
# 2. NotDelivered plus a confirmed late report is not submittable
# --------------------------------------------------------------------------- #


def test_a_late_delivered_report_closes_submit_and_not_only_retry(
    sv, routes, fake_host, scene, studio, clock
):
    """The host refused, the proof held, and then a delivered report contradicted it.

    ``retry`` already refuses this row (``retry_not_allowed``). ``submit`` is the other edge into
    ``Delivering`` and it accepts the same row from the same status, so the guard has to live on both.
    """
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    status, receipt = _submit(sv, routes, fake_host, card)
    assert status == 200, receipt

    # The host's own 4xx, and the four proofs of absence all hold: nothing is in the transcript, the
    # slot is idle, the disk has not moved and this is the same process.
    status, proven = _report(
        sv,
        routes,
        fake_host,
        card["action_id"],
        receipt,
        outcome="not_delivered",
        http_status=409,
        body={"error": "slot agent mismatch"},
    )
    assert status == 200, proven
    assert _row(sv, card["action_id"])["status"] == "NotDelivered"

    # A second, later report says it landed after all (a retried POST, a service worker replay).
    status, late = _report(sv, routes, fake_host, card["action_id"], receipt, outcome="delivered")
    assert status == 200, late
    assert dict(_row(sv, card["action_id"])["evidence_json"] or {})["delivery_confirmed"] is True

    status, refused = _post(sv, routes, fake_host, f"/actions/{card['action_id']}/retry")
    assert status == 409 and refused["code"] == "retry_not_allowed", refused

    fresh = [
        c
        for c in _get(sv, routes, fake_host, "/actions")[1]["actions"]
        if c["action_id"] == card["action_id"]
    ]
    status, again = _submit(sv, routes, fake_host, fresh[0] if fresh else card)
    assert status == 409, again
    assert again["code"] in ("action_not_submittable", "not_delivered_unproven"), again
    dispatches = [
        dict(t)
        for t in sv.storage.select("action_transitions", {"action_id": card["action_id"]})
        if dict(t)["to_status"] == "Delivering"
    ]
    assert len(dispatches) == 1, dispatches


# --------------------------------------------------------------------------- #
# 3. an unreadable slot table is not proof of absence
# --------------------------------------------------------------------------- #


class DriftedHost:
    """A gateway whose slot table cannot be read (architecture §7.0 signature drift).

    Everything else about the host still works, which is the point: one renamed attribute must cost a
    capability, never a proof.
    """

    def __init__(self, real) -> None:
        self.sessions = real.sessions
        self.owner_id = real.owner_id
        self.slack_client = None
        self.subagents = None

    def notify(self, *args, **kwargs) -> None:  # pragma: no cover - never called here
        pass


def test_an_unreadable_slot_table_is_never_proof_that_nothing_was_sent(world, A, studio):
    """Same disk, same message in the host's transcript. Only Studio's ability to look changed.

    With the slots readable the claim is refused because the row is right there. With them unreadable
    the four proofs must still refuse: ``row_absent`` and ``slot_idle`` are then answers about
    Studio's eyesight, not about the host's queue (the rule ``reconciler._delivery_evidence`` applies).
    """

    async def scenario():
        rec, receipt = await delivering(world, A)
        world.fake_host.get_slot(SLOT).messages.append(
            {
                "role": "user",
                "content": rec.wire_text,
                "cls": "msg msg-u",
                "ts": rec.delivering_at,
                "meta": {"studio_delivery_id": receipt.delivery_id},
            }
        )
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.record_delivery(
                rec.action_id,
                delivery_id=receipt.delivery_id,
                outcome="not_delivered",
                http_status=409,
                receipt={"code": "slot_missing", "error": "no such slot"},
            )
        assert exc.value.code == "not_delivered_unproven"
        assert exc.value.details["checks"]["row_absent"] is False

        uncertain = await world.broker.get(rec.action_id)
        escalated = await world.broker.transition(
            uncertain.action_id,
            "ReconciliationRequired",
            expected_generation=uncertain.status_generation,
            reason="ack deadline",
            evidence={},
        )
        world.host.attach(DriftedHost(world.fake_host))
        assert world.host.capabilities()["slots"].available is False

        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.record_delivery(
                escalated.action_id,
                delivery_id=receipt.delivery_id,
                outcome="not_delivered",
                http_status=409,
                receipt={"code": "slot_missing", "error": "no such slot"},
            )
        assert exc.value.code == "not_delivered_unproven"
        assert exc.value.details["checks"]["row_absent"] is False, "unreadable is not absent"
        assert (await world.broker.get(escalated.action_id)).status != "NotDelivered"

    run(scenario())
