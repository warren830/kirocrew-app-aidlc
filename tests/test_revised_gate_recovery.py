"""A completed revision must not depend on observing its transient R row."""

import asyncio
import os

import fixtures as F
import pytest
import conftest as CT
from test_e2e_gate import (  # noqa: F401
    STAGE, _finish_the_turn, _gate_card, _gate_state, _record_dir, _report,
    _rescan, _row, _shard, _submit, _tree_digest, repo_dir, scene,
)
from test_handlers_auth import common, routes, sv  # noqa: F401


@pytest.mark.parametrize("case", [
    "completed_revision", "same_second", "crosses_old_tail_limit", "missing_rejection", "missing_revising",
    "missing_gate", "old_gate", "gate_before_revision", "wrong_stage", "wrong_unit",
    "wrong_workflow", "same_revision", "wrong_revision", "missing_presence",
    "still_running", "not_at_gate", "missing_boundary",
])
def test_revision_return_uses_ordered_audit_proof_and_releases_only_its_lease(
    sv, routes, fake_host, scene, case,
):
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    wire = "Request Changes: Spell out the criteria."
    before_revision = card["evidence"]["state"]["revision_count"]
    revision = before_revision + 1
    status, receipt = _submit(
        sv, routes, fake_host, card,
        payload={"decision": "request_changes", "feedback": "Spell out the criteria."},
        wire_text=wire,
    )
    assert status == 200
    _finish_the_turn(fake_host, scene.slot_key, wire_text=wire)
    fake_host.get_slot(scene.slot_key).messages[-1]["content"] = "Revision ready for review."
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    assert sv.storage.lease_list()

    blocks = []
    if case == "crosses_old_tail_limit":
        blocks.append(F.audit_block(
            "ARTIFACT_UPDATED", "2026-09-04T10:00:19Z",
            Details="x" * CT.studio_module("constants").MAX_AUDIT_TAIL_BYTES,
        ))
    if case != "missing_presence":
        blocks.append(F.audit_block("HUMAN_TURN", "2026-09-04T10:00:20Z"))
        marker = _record_dir(scene.root) / ".aidlc-human-turn"
        marker.write_text("2026-09-04T10:00:20Z\n")
        os.utime(marker, ns=(2_000_000_000, 2_000_000_000))
    rejected = F.audit_block(
        "GATE_REJECTED", "2026-09-04T10:00:20Z", Stage=STAGE,
        Feedback="Spell out the criteria.",
    )
    revising = F.audit_block(
        "STAGE_REVISING", "2026-09-04T10:00:20Z", Stage=STAGE,
        Revision_count=str(revision + (case == "wrong_revision")),
    )
    gate = F.audit_block(
        "STAGE_AWAITING_APPROVAL",
        "2026-09-04T09:59:00Z" if case == "old_gate"
        else "2026-09-04T10:00:20Z" if case == "same_second"
        else "2026-09-04T10:00:21Z",
        Stage="domain-design" if case == "wrong_stage" else STAGE,
        **({"Unit": "other-unit"} if case == "wrong_unit"
           else {"Workflow": "other-workflow"} if case == "wrong_workflow" else {}),
    )
    if case == "gate_before_revision":
        blocks.append(gate)
    if case != "missing_rejection":
        blocks.append(rejected)
    if case != "missing_revising":
        blocks.append(revising)
    if case not in ("missing_gate", "gate_before_revision"):
        blocks.append(gate)
    shard = _shard(scene.root)
    shard.write_text(shard.read_text() + F.audit_text(*blocks))
    # No reconciler tick ever observes R; it sees only the returned approval gate.
    state = _gate_state(**{
        "Revision Count": str(before_revision if case == "same_revision" else revision),
    })
    if case == "not_at_gate":
        state = state.replace("[?]", "[ ]")
    (_record_dir(scene.root) / "aidlc-state.md").write_text(state)
    if case == "still_running":
        fake_host.get_slot(scene.slot_key).running = True
    if case == "missing_boundary":
        row = _row(sv, card["action_id"])
        evidence = dict(row["evidence_json"])
        evidence["audit"] = {**evidence["audit"], "boundary_event": None}
        sv.storage.cas_update("actions", row["action_id"], row["status_generation"],
                              {"evidence_json": evidence})
    before = _tree_digest(scene.root)
    asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    row = _row(sv, card["action_id"])
    if case in ("completed_revision", "same_second", "crosses_old_tail_limit"):
        assert row["status"] == "StateChanged"
        assert row["resolution_json"]["reason"] == "gate_revision_returned"
        assert row["resolution_json"]["evidence"]["presence"]["ok"] is True
        assert not sv.storage.lease_list()
    else:
        assert row["status"] in ("Processing", "DeliveryUncertain", "ReconciliationRequired")
        assert sv.storage.lease_list()
    assert len(fake_host.get_slot(scene.slot_key).messages) == 2
    assert row["wire_text"] == wire
    assert _tree_digest(scene.root) == before
