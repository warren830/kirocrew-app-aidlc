"""Error-burst lifecycle evidence through the real reader and projection, without live writes."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import fixtures as F
import pytest

from test_projection import ActionStub, BreakerStub, SlotStub, proj_mod, projection, repo_record  # noqa: F401

INTENT = "260910-error-lifecycle"
STAGE = "code-generation"
NEXT = "build-and-test"
FINAL = "deployment-execution"


def event(kind, second, *, shard="one.md", **fields):
    timestamp = (datetime(2026, 9, 10, tzinfo=timezone.utc) + timedelta(seconds=second)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"kind": kind, "timestamp": timestamp, "shard": shard, "fields": fields}


def errors(*, seconds=(10, 20, 30), stage=None, unit=None, shard="one.md", **fields):
    scope = {**({"Stage": stage} if stage else {}), **({"Unit": unit} if unit else {}), **fields}
    return [event("ERROR_LOGGED", second, shard=shard, Error="Command refused", **scope) for second in seconds]


@pytest.fixture
def snapshot(repo_builder, projection, repo_record):
    def build(rows, *, stage=STAGE, unit=None, status="Running", complete=True, marks=None):
        state = F.state_text(marks=marks or {stage: "-"}, fields={"Current Stage": stage, "Status": status})
        if unit:
            state = F.insert_runtime_field(state, "Active Unit", unit)
        directive = {"version": 2, "kind": "run-stage", "stage": stage, "state_sha256": "AUTO"}
        root = repo_builder.with_workspace().with_intent(
            INTENT, state=state, directive={**directive, **({"unit": unit} if unit else {})}).build()
        grouped = {}
        for row in rows:
            grouped.setdefault(row["shard"], []).append(F.audit_block(row["kind"], row["timestamp"], **row["fields"]))
        for name, blocks in grouped.items():
            (root / "aidlc/spaces/default/intents" / INTENT / "audit" / name).write_text(F.audit_text(*blocks))
        snap = projection.snapshot(repo_record(root), "default", INTENT)
        assert snap.stable and snap.audit.complete
        return snap if complete else replace(snap, audit=replace(snap.audit, complete=False))

    return build


def cards(projection, snap, **overrides):
    return projection.derive_cards(snap, **{"findings": (), "binding": None, "install": None, "breakers": (),
                                            "live_actions": (), "slot": SlotStub(), **overrides})


def failure(projection, snap, **overrides):
    return next((card for card in cards(projection, snap, **overrides) if card.type == "failure"), None)


@pytest.mark.parametrize("bound_stage", [None, STAGE], ids=["fieldless", "stage-bound"])
@pytest.mark.parametrize("kind", [
    "WORKFLOW_STARTED", "WORKFLOW_COMPLETED", "STAGE_STARTED", "STAGE_REVISING",
    "STAGE_COMPLETED", "GATE_APPROVED", "STAGE_SKIPPED", "STAGE_JUMPED",
])
def test_proven_lifecycle_boundary_retires_an_older_burst(snapshot, projection, kind, bound_stage):
    barrier = event(kind, 40, **({} if kind.startswith("WORKFLOW_") else {"Stage": STAGE}))
    snap = snapshot([event("STAGE_STARTED", 0, Stage=STAGE), *errors(stage=bound_stage), barrier])
    assert failure(projection, snap) is None


@pytest.mark.parametrize("kind", ["STAGE_STARTED", "STAGE_COMPLETED", "WORKFLOW_COMPLETED"])
def test_new_errors_after_a_boundary_remain_a_failure(snapshot, projection, kind):
    snap = snapshot([*errors(), event(kind, 35, **({} if kind.startswith("WORKFLOW_") else {"Stage": STAGE})),
                     *errors(seconds=(40, 50, 60), stage=STAGE)],
                    status="Completed" if kind == "WORKFLOW_COMPLETED" else "Running")
    card = failure(projection, snap)
    assert card is not None and card.reason == "error_burst"
    assert card.waiting_since == event("ERROR_LOGGED", 40)["timestamp"]


def test_active_burst_does_not_expire_by_wall_clock(snapshot, projection):
    snap = snapshot([event("STAGE_STARTED", 0, Stage=STAGE), *errors(stage=STAGE)])
    before = failure(projection, snap)
    later = failure(projection, replace(snap, taken_at="2030-01-01T00:00:00Z"))
    assert before is not None and later is not None
    assert before.waiting_since == later.waiting_since


@pytest.mark.parametrize("boundary_last", [True, False])
def test_same_shard_append_order_resolves_same_second_boundaries(snapshot, projection, boundary_last):
    burst = errors(seconds=(10, 10, 10))
    barrier = event("STAGE_COMPLETED", 10, Stage=STAGE)
    snap = snapshot([*burst, barrier] if boundary_last else [barrier, *burst])
    assert (failure(projection, snap) is None) is boundary_last


@pytest.mark.parametrize("error_shard,barrier_shard", [("a.md", "z.md"), ("z.md", "a.md")])
def test_cross_shard_boundary_tie_cannot_prove_a_burst_resolved(snapshot, projection, error_shard, barrier_shard):
    snap = snapshot([*errors(seconds=(8, 9, 10), shard=error_shard),
                     event("STAGE_COMPLETED", 10, shard=barrier_shard, Stage=STAGE)])
    assert failure(projection, snap) is not None


@pytest.mark.parametrize("kind", ["STAGE_STARTED", "STAGE_COMPLETED", "GATE_APPROVED", "WORKFLOW_COMPLETED"])
@pytest.mark.parametrize("selected_unit", ["unit-a", None])
def test_another_units_progress_does_not_resolve_unit_a_errors(snapshot, projection, kind, selected_unit):
    snap = snapshot([*errors(stage=STAGE, unit="unit-a"), event(kind, 40, Stage=STAGE, Unit="unit-b")],
                    unit=selected_unit)
    assert failure(projection, snap) is not None


UNIT_A = {"stage": STAGE, "unit": "unit-a"}


@pytest.mark.parametrize("rows,options,resolved", [
    pytest.param([event("STAGE_STARTED", 0, Stage=STAGE), *errors(), event("STAGE_COMPLETED", 40, Stage=STAGE),
                  event("STAGE_STARTED", 41, Stage=NEXT)], {"stage": NEXT}, True,
                 id="fieldless_errors_do_not_follow_successful_stage_advance"),
    pytest.param([*errors(seconds=(10, 20)), event("STAGE_STARTED", 25, Stage=STAGE), *errors(seconds=(30,))], {},
                 True, id="a_window_cannot_combine_errors_from_separate_attempts"),
    pytest.param(errors(stage=STAGE), {"status": "Completed", "marks": {STAGE: "x"}}, False,
                 id="completed_state_alone_is_not_proof_that_observed_errors_were_resolved"),
    pytest.param([*errors(shard="a.md"), event("WORKFLOW_COMPLETED", 40, shard="z.md")], {}, True,
                 id="strict_cross_shard_order_can_prove_resolution"),
    pytest.param([*errors(**UNIT_A), event("STAGE_COMPLETED", 40, Stage=STAGE, Unit="unit-a")], {"unit": "unit-a"},
                 True, id="matching_unit_completion_resolves_only_its_own_burst"),
    pytest.param(errors(**UNIT_A), {"unit": "unit-b"}, True,
                 id="unrelated_unit_errors_are_not_attributed_to_the_selected_unit"),
    pytest.param([*errors(seconds=(10,), **UNIT_A), *errors(seconds=(20, 30), stage=STAGE, unit="unit-b")], {}, True,
                 id="errors_from_different_units_do_not_combine_into_one_burst"),
    pytest.param([*errors(stage=STAGE), event("STAGE_COMPLETED", 40, Stage=NEXT)], {}, False,
                 id="another_stage_completion_does_not_resolve_explicit_current_stage_errors"),
    pytest.param(errors(stage=STAGE, Workflow=f"single-stage:{STAGE}"), {}, True,
                 id="single_stage_workflow_errors_do_not_create_a_main_workflow_failure"),
    pytest.param([*errors(stage=STAGE), event("WORKFLOW_COMPLETED", 40, Workflow=f"single-stage:{STAGE}")], {}, False,
                 id="single_stage_workflow_completion_does_not_clear_main_errors"),
    pytest.param([*errors(), event("WORKFLOW_COMPLETED", 40)], {"complete": False}, False,
                 id="partial_audit_cannot_prove_historical_errors_resolved"),
    pytest.param([*errors(), {**event("WORKFLOW_COMPLETED", 40), "timestamp": "unreadable-time"}], {}, False,
                 id="invalid_boundary_timestamp_cannot_resolve_errors"),
])
def test_a_burst_resolves_only_through_its_own_proven_boundary(snapshot, projection, rows, options, resolved):
    assert (failure(projection, snapshot(rows, **options)) is None) is resolved


def test_terminal_completion_keeps_explicit_action_failures_and_circuit_cards(snapshot, projection):
    snap = snapshot([*errors(), event("WORKFLOW_COMPLETED", 40)], status="Completed")
    failed = ActionStub(type="run", status="Failed", retry_fingerprint="guard.refusing-to-record:99aa11")
    result = cards(projection, snap, live_actions=[failed], breakers=[BreakerStub()])
    card = next(card for card in result if card.type == "failure")
    assert card.reason == "action_failed" and card.related_action_id == failed.action_id
    assert card.evidence["error_burst_at"] is None
    assert any(card.type == "circuit_breaker" for card in result)


def test_old_queued_failure_does_not_recreate_itself_after_workflow_completion(snapshot, projection):
    snap = snapshot([*errors(), event("WORKFLOW_COMPLETED", 40)], stage=FINAL, status="Completed")
    old_card = ActionStub(type="failure", status="Queued", stage=FINAL)
    assert failure(projection, snap, live_actions=[old_card]) is None


@pytest.mark.parametrize("status", ["ReconciliationRequired"])
def test_terminal_completion_keeps_uncertain_delivery_semantics(snapshot, projection, proj_mod, status):
    snap = snapshot([event("WORKFLOW_COMPLETED", 40)], status="Completed")
    uncertain = ActionStub(status=status)
    assert proj_mod.queue_type_for("gate", status) == "delivery_uncertain"
    assert projection.operational_state(snap, live_actions=[uncertain]) == "ReconciliationRequired"


def test_completed_map_keeps_last_stage_and_truthful_executed_skipped_counts(snapshot, projection):
    completed = ["workspace-scaffold", "workspace-detection", "state-init", "reverse-engineering",
                 "requirements-analysis", STAGE, NEXT]
    marks = {**{slug: " " for slug in F.stage_marks(F.real_state())}, **{slug: "x" for slug in completed},
             **{slug: "S" for slug in ["deployment-pipeline", FINAL]}}
    snap = snapshot([*errors(), event("STAGE_COMPLETED", 40, Stage=NEXT), event("STAGE_STARTED", 41, Stage=FINAL),
                     event("WORKFLOW_COMPLETED", 42)], stage=FINAL, status="Completed", marks=marks)
    inputs = projection.map_inputs(snap)
    rows = inputs["rows"]
    assert snap.stage == snap.state.current_stage == FINAL
    # The cursor is sent as it is; `buildMap` drops it for a `Completed` status (buildMap.test.ts).
    assert (inputs["status"], inputs["current_stage"], inputs["directive_stage"]) == ("Completed", FINAL, FINAL)
    assert next(row for row in rows if row["slug"] == FINAL)["state"] == "skipped"
    assert len(rows) == 33
    assert sum(row["state"] == "completed" for row in rows) == 7
    assert sum(row["state"] == "skipped" for row in rows) == 2
    assert failure(projection, snap) is None
