"""A completed revision must not depend on observing its transient R row."""

import asyncio
import json
from types import SimpleNamespace

import fixtures as F
import pytest
import conftest as CT
from _helpers_a import stamp_human_turn
from test_e2e_gate import (  # noqa: F401
    NEXT_STAGE, STAGE, _finish_the_turn, _gate_card, _gate_state, _record_dir, _report,
    _rescan, _row, _shard, _submit, _tree_digest, repo_dir, scene,
)
from test_handlers_auth import common, routes, sv  # noqa: F401

FEEDBACK = "Spell out the criteria."
WIRE = f"Request Changes: {FEEDBACK}"
RETURNED = ("completed_revision", "same_second", "crosses_old_tail_limit", "approved_after_return")


def _request_changes(sv, routes, fake_host, scene, card, reply=None):
    """Deliver "Request Changes" for ``card`` and end its turn (``reply`` rewrites the agent's last row)."""
    status, receipt = _submit(sv, routes, fake_host, card, wire_text=WIRE,
                              payload={"decision": "request_changes", "feedback": FEEDBACK})
    assert status == 200
    _finish_the_turn(fake_host, scene.slot_key, wire_text=WIRE)
    if reply is not None:
        fake_host.get_slot(scene.slot_key).messages[-1]["content"] = reply
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200


def _append(scene, *blocks):
    shard = _shard(scene.root)
    shard.write_text(shard.read_text() + F.audit_text(*blocks))


def _unresolved(sv, row):
    assert row["status"] in ("Delivered", "ReconciliationRequired")
    assert bool(sv.scheduler.live()) == (row["status"] == "Delivered")  # only in flight holds the repo


@pytest.mark.parametrize("case", [
    *RETURNED, "missing_rejection", "missing_revising", "missing_gate", "old_gate", "gate_before_revision",
    "wrong_stage", "wrong_unit", "wrong_workflow", "same_revision", "wrong_revision", "missing_presence",
    "still_running", "not_at_gate", "missing_boundary",
])
def test_revision_return_uses_ordered_audit_proof_and_releases_only_its_lease(sv, routes, fake_host, scene, case):
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    before_revision = card["evidence"]["state"]["revision_count"]
    revision = before_revision + 1
    _request_changes(sv, routes, fake_host, scene, card, reply="Revision ready for review.")
    assert sv.scheduler.live()

    blocks = []
    if case == "crosses_old_tail_limit":
        blocks.append(F.audit_block("ARTIFACT_UPDATED", "2026-09-04T10:00:19Z",
                                    Details="x" * CT.studio_module("constants").MAX_AUDIT_TAIL_BYTES))
    if case != "missing_presence":
        blocks.append(F.audit_block("HUMAN_TURN", "2026-09-04T10:00:20Z"))
        stamp_human_turn(_record_dir(scene.root), "2026-09-04T10:00:20Z")
    rejected = F.audit_block("GATE_REJECTED", "2026-09-04T10:00:20Z", Stage=STAGE, Feedback=FEEDBACK)
    revising = F.audit_block("STAGE_REVISING", "2026-09-04T10:00:20Z", Stage=STAGE,
                             Revision_count=str(revision + (case == "wrong_revision")))
    gate = F.audit_block(
        "STAGE_AWAITING_APPROVAL",
        {"old_gate": "2026-09-04T09:59:00Z", "same_second": "2026-09-04T10:00:20Z"}.get(case, "2026-09-04T10:00:21Z"),
        Stage="domain-design" if case == "wrong_stage" else STAGE,
        **{"wrong_unit": {"Unit": "other-unit"}, "wrong_workflow": {"Workflow": "other-workflow"}}.get(case, {}))
    if case == "gate_before_revision":
        blocks.append(gate)
    if case != "missing_rejection":
        blocks.append(rejected)
    if case != "missing_revising":
        blocks.append(revising)
    if case not in ("missing_gate", "gate_before_revision"):
        blocks.append(gate)
    _append(scene, *blocks)
    # No reconciler tick ever observes R; it sees only the returned approval gate.
    state = _gate_state(**{"Revision Count": str(before_revision if case == "same_revision" else revision)})
    if case == "not_at_gate":
        state = state.replace("[?]", "[ ]")
    elif case == "approved_after_return":  # two polls missed the R row and the returned gate; approved natively
        state = F.state_text(marks={STAGE: "x"}, fields={
            "Current Stage": NEXT_STAGE, "Status": "Running", "Next Stage": "domain-model",
            "Lifecycle Phase": "INCEPTION", "Revision Count": str(revision)})
    (_record_dir(scene.root) / "aidlc-state.md").write_text(state)
    if case == "still_running":
        fake_host.get_slot(scene.slot_key).running = True
    if case == "missing_boundary":
        row = _row(sv, card["action_id"])
        evidence = {**row["evidence_json"], "audit": {**row["evidence_json"]["audit"], "boundary_event": None}}
        sv.storage.cas_update("actions", row["action_id"], row["status_generation"], {"evidence_json": evidence})
    before = _tree_digest(scene.root)
    asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    row = _row(sv, card["action_id"])
    if case in RETURNED:
        assert row["status"] == "StateChanged"
        assert row["resolution_json"]["reason"] == "gate_revision_returned"
        assert row["resolution_json"]["evidence"]["presence"]["ok"] is True
        assert not sv.scheduler.live()
    else:
        _unresolved(sv, row)
    assert len(fake_host.get_slot(scene.slot_key).messages) == 2
    assert row["wire_text"] == WIRE
    assert _tree_digest(scene.root) == before


def _run_unit(root, unit, **fields):
    """The state of a stage running one unit. The directive marker follows the new digest."""
    state = F.insert_runtime_field(_gate_state(**fields), "Active Unit", unit)
    (_record_dir(root) / "aidlc-state.md").write_text(state)
    (_record_dir(root) / ".aidlc-active-directive.json").write_text(
        json.dumps({"version": 1, "stage": STAGE, "state_sha256": F.sha256(state)}))


@pytest.mark.parametrize("case", ["solo_rows", "team_rows_then_other_unit", "other_unit_active"])
def test_unit_revision_return_accepts_solo_rows_and_ignores_other_units(sv, routes, fake_host, scene, case):
    _run_unit(scene.root, "unit-a")
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    assert _row(sv, card["action_id"])["unit"] == "unit-a"
    revision = card["evidence"]["state"]["revision_count"] + 1
    _request_changes(sv, routes, fake_host, scene, card)
    scope = {"Unit": "unit-a"} if case == "team_rows_then_other_unit" else {}  # solo rows name no Unit; team rows do
    _append(scene, F.audit_block("HUMAN_TURN", "2026-09-04T10:00:20Z"),
            F.audit_block("GATE_REJECTED", "2026-09-04T10:00:20Z", Stage=STAGE, Feedback=FEEDBACK, **scope),
            F.audit_block("STAGE_REVISING", "2026-09-04T10:00:20Z", Stage=STAGE, Revision_count=str(revision), **scope),
            F.audit_block("STAGE_AWAITING_APPROVAL", "2026-09-04T10:00:21Z", Stage=STAGE, **scope),
            *([F.audit_block("STAGE_AWAITING_APPROVAL", "2026-09-04T10:00:22Z", Stage=STAGE, Unit="unit-b")]
              if case == "team_rows_then_other_unit" else []))
    stamp_human_turn(_record_dir(scene.root), "2026-09-04T10:00:20Z")
    _run_unit(scene.root, "unit-b" if case == "other_unit_active" else "unit-a", **{"Revision Count": str(revision)})
    asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    row = _row(sv, card["action_id"])
    if case == "other_unit_active":  # blank-Unit rows belong to whichever unit runs now: no longer this action's
        _unresolved(sv, row)
    else:
        assert row["status"] == "StateChanged"
        assert row["resolution_json"]["reason"] == "gate_revision_returned"
        assert row["resolution_json"]["evidence"]["returned_gate"]["timestamp"] == "2026-09-04T10:00:21Z"
        assert not sv.scheduler.live()


@pytest.mark.parametrize("truncated", ["never", "now", "at_capture"])
def test_a_truncated_boundary_shard_drops_only_its_position_floor(studio, truncated):
    """Tail windows renumber blocks, so a position from one cannot bound rows from another read."""
    reconciler = object.__new__(studio.reconciler.Reconciler)
    rec = SimpleNamespace(delivering_at="2026-09-04T10:00:00Z", evidence={"audit": {
        "boundary_event": {"shard": "b.md", "pos": 42},
        "shards": [{"relpath": "x/audit/b.md", "size": 1, "truncated": truncated == "at_capture"}],
    }})

    def row(shard, index, pos, ts="2026-09-04T10:00:05Z"):
        return SimpleNamespace(event="GATE_APPROVED", fields={"Stage": STAGE}, timestamp=ts,
                               shard=shard, shard_index=index, pos=pos, sort_key=(ts, index, pos))

    earlier_shard, renumbered, stale = row("a.md", 0, 99), row("b.md", 1, 3), row("b.md", 1, 1, "2026-09-04T09:00:00Z")
    snap = SimpleNamespace(audit=SimpleNamespace(
        events=[earlier_shard, stale, renumbered],
        shards=[SimpleNamespace(relpath="x/audit/a.md", truncated=False),
                SimpleNamespace(relpath="x/audit/b.md", truncated=truncated == "now")]))
    new = reconciler._new_events(rec, snap, ("GATE_APPROVED",), STAGE)
    if truncated == "never":
        assert reconciler._boundary_order(rec, snap) == (1, 42)
        assert new == []
    else:
        assert reconciler._boundary_order(rec, snap) == (1, -1)
        assert new == [renumbered]    # the timestamp rule still refuses the old row
