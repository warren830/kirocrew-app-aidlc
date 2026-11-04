"""A plan the conductor asked as an ordinary question, on the enterprise run of 2026-11-04.

Units Generation logged its plan as a plain decision ("Units Generation plan approval: …", Options "Approve Plan,
Revise Plan,X. Other (please specify)", no Checkpoint), listed the choices as ``A. Approve Plan`` lines under
``## Plan Approval``, wrote ``[Answer]: A`` and recorded Studio's reply with ``log answer`` (``QUESTION_ANSWERED``,
Details ``Approve Plan``). The stage went on approved; the card waited for a ``PLAN_APPROVAL_RECORDED`` that never
comes, turned ReconciliationRequired at its deadline and held back the Learnings card that followed.
"""

import asyncio
import os

import fixtures as F
import pytest

from test_run_checkpoints import (  # noqa: F401
    INTENT, STAGE, QUESTIONS_REL, _finish_the_turn, _record_dir, _report, _rescan,
    _shard, _submit, _queue, common, repo_dir, routes, scene, sv,
)

PLAN = (
    "# Questions\n\n## Plan Approval\n\nThe plan: two units, U1 the first slice and U2 the rest.\n\n"
    "Do you approve the plan?\n\nA. Approve Plan\nB. Revise Plan\nX. Other (please specify)\n\n[Answer]:\n"
)


def _asked(scene):
    record = _record_dir(scene.root)
    path = record / QUESTIONS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PLAN)
    with _shard(scene.root).open("a") as file:
        file.write(F.audit_text(F.audit_block(
            "DECISION_RECORDED", "2026-09-04T09:59:30Z", Stage=STAGE,
            Decision="Units Generation plan approval: two units", Options="Approve Plan,Revise Plan,X. Other (please specify)")))
    return path


@pytest.mark.parametrize(("details", "settled"), [("Approve Plan", True), ("Revise Plan", False)])
def test_the_engines_answer_receipt_settles_a_plan_asked_as_a_question(sv, routes, fake_host, scene, details, settled):
    path = _asked(scene)
    _rescan(sv, routes, fake_host, scene)
    card = next(a for a in _queue(sv, routes, fake_host) if a["type"] == "question")
    assert card["evidence"]["questions"]["pending_checkpoint"] == "plan_approval"
    status, receipt = _submit(sv, routes, fake_host, card, payload={"decision": "approve_plan"}, wire_text="Approve Plan")
    assert status == 200, receipt
    fake_host.get_slot(scene.slot_key).running = True
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    # The conductor writes the letter alone, and records the reply as an ordinary answer.
    path.write_text(PLAN.replace("[Answer]:\n", "[Answer]: A\n"))
    with _shard(scene.root).open("a") as file:
        file.write(F.audit_text(
            F.audit_block("HUMAN_TURN", "2026-09-04T10:00:20Z"),
            F.audit_block("QUESTION_ANSWERED", "2026-09-04T10:00:21Z", Stage=STAGE, Details=details,
                          Person_Reply="Approve Plan"),
        ))
    marker = _record_dir(scene.root) / ".aidlc-human-turn"
    marker.write_text("2026-09-04T10:00:20Z\n")
    os.utime(marker, ns=(2_000_000_000, 2_000_000_000))
    _finish_the_turn(fake_host, scene.slot_key, wire_text="Approve Plan")

    async def settle():
        await sv.reconciler.reconcile_now(card["action_id"])
        return await sv.actions.get(card["action_id"])

    after = asyncio.run(settle())
    if settled:
        assert (after.status, after.resolution["reason"]) == ("ResolvedNoTransition", "plan_approved")
        assert after.resolution["evidence"]["question_answered"]["fields"]["Details"] == "Approve Plan"
    else:
        assert after.status not in ("ResolvedNoTransition", "StateChanged"), after.resolution
