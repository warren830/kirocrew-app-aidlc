"""An ended reply can yield a new gate or a follow-up without a faithful answer receipt."""

import asyncio
import os

import fixtures as F
import pytest
from test_audit_questions import (  # noqa: F401
    repo_dir, question_card, answer_payload, append, start_audit, decision, activate_unit,
)
from test_e2e_gate import (  # noqa: F401
    INTENT, STAGE, _finish_the_turn, _gate_state, _record_dir, _report,
    _row, _shard, _submit, _tree_digest, scene,
)
from test_handlers_auth import common, routes, sv  # noqa: F401

#: The receipt alone proves AI-DLC consumed this question; the next boundary only names the outcome.
AT_GATE = ("mismatch_at_gate", "solo_unit_gate")
WITHOUT_GATE = ("no_gate", "follow_up", "old_gate", "other_unit", "other_workflow")


@pytest.mark.parametrize("case", [
    "mismatch_at_gate", "exact_at_gate", "no_gate", "follow_up", "old_gate", "other_unit",
    "solo_unit_gate", "missing_presence", "still_running", "no_receipt", "other_workflow",
])
def test_unverified_reply_releases_only_after_a_scoped_receipt(
    sv, routes, fake_host, scene, case,
):
    wire = "Persist none"
    unit = {"Unit": "unit-a", "Attempt_Generation": "2"} if case == "solo_unit_gate" else {}
    if unit:
        activate_unit(sv, scene)
    _shard(scene.root).write_text(start_audit() + decision(
        Decision="Persist learning candidates?", Options="Persist none,Persist selected candidates",
        **unit,
    ))
    card = question_card(sv, routes, fake_host, scene)
    assert _row(sv, card["action_id"])["unit"] == ("unit-a" if unit else None)
    status, receipt = _submit(
        sv, routes, fake_host, card, payload=answer_payload(), wire_text=wire,
    )
    assert status == 200
    _finish_the_turn(fake_host, scene.slot_key, wire_text=wire)
    fake_host.get_slot(scene.slot_key).messages[-1]["content"] = "Review the next gate."
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    if case != "missing_presence":
        append(scene, F.audit_block("HUMAN_TURN", "2026-09-04T10:00:20Z"))
        marker = _record_dir(scene.root) / ".aidlc-human-turn"
        marker.write_text("2026-09-04T10:00:20Z\n")
        os.utime(marker, ns=(2_000_000_000, 2_000_000_000))
    if case != "no_receipt":
        append(scene, F.audit_block(
            "QUESTION_ANSWERED", "2026-09-04T10:00:20Z",
            Stage=STAGE, Details=wire if case == "exact_at_gate" else "Nothing to add; persist none",
            **unit,
        ))
    if case == "follow_up":
        append(scene, decision(
            "2026-09-04T10:00:21Z", Decision="Which candidates should persist?",
            Options="Persist all,Persist selected candidates",
        ))
    elif case not in ("no_gate",):
        # A solo gate row carries no Unit even when the stage runs one (`aidlc-state.ts` gate-start).
        append(scene, F.audit_block(
            "STAGE_AWAITING_APPROVAL",
            "2026-09-04T09:59:00Z" if case == "old_gate" else "2026-09-04T10:00:21Z",
            Stage=STAGE, **(
                {"Unit": "another-unit"} if case == "other_unit"
                else {"Workflow": "another-workflow"} if case == "other_workflow" else {}
            ),
        ))
        state = _gate_state()
        if unit:
            state = F.insert_runtime_field(state, "Active Unit", "unit-a")
        (_record_dir(scene.root) / "aidlc-state.md").write_text(state)
    if case == "still_running":
        fake_host.get_slot(scene.slot_key).running = True
    before = _tree_digest(scene.root)
    asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    row = _row(sv, card["action_id"])
    if case in ("exact_at_gate", *AT_GATE, *WITHOUT_GATE):
        assert row["status"] == "ResolvedNoTransition"
        expected = (
            "question_answered" if case == "exact_at_gate"
            else "answer_not_verified_at_gate" if case in AT_GATE else "answer_not_verified"
        )
        assert row["resolution_json"]["reason"] == expected
        assert not sv.storage.lease_list()
        if case != "exact_at_gate":
            evidence = row["resolution_json"]["evidence"]
            assert evidence["answer_verified"] is False
            assert evidence["recorded_answer"]["fields"]["Details"] != wire
            assert (evidence["next_gate"] is not None) is (case in AT_GATE)
    else:
        assert row["status"] in ("Processing", "DeliveryUncertain", "ReconciliationRequired")
        assert sv.storage.lease_list()
    assert row["wire_text"] == wire
    assert len(fake_host.get_slot(scene.slot_key).messages) == 2
    assert _tree_digest(scene.root) == before
