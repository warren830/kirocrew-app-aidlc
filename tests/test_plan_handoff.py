"""A new Learnings prompt must not hide the receipt for the plan just approved."""

import asyncio
import os
from dataclasses import replace

import fixtures as F
import pytest

from test_run_checkpoints import (  # noqa: F401
    INTENT, STAGE, QUESTIONS_REL, _finish_the_turn, _record_dir, _report, _rescan,
    _shard, _submit, _tree_digest, _queue, checkpoint, common, repo_dir, routes, scene, sv,
)


def approved_then_learnings(
    sv, routes, fake_host, scene, *, receipt_at="2026-09-04T10:00:20Z", wrong_prompt=False,
):
    checkpoint(scene, "plan")
    _rescan(sv, routes, fake_host, scene)
    card = next(a for a in _queue(sv, routes, fake_host) if a["type"] == "question")
    status, receipt = _submit(
        sv, routes, fake_host, card, payload={"decision": "approve_plan"}, wire_text="Approve Plan",
    )
    assert status == 200, receipt
    fake_host.get_slot(scene.slot_key).running = True
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    record = _record_dir(scene.root)
    path = record / QUESTIONS_REL
    path.write_text(path.read_text().replace("[Answer]:", "[Answer]: Approve Plan"))
    with _shard(scene.root).open("a") as file:
        file.write(F.audit_text(
            F.audit_block("HUMAN_TURN", "2026-09-04T10:00:20Z"),
            F.audit_block("PLAN_APPROVAL_RECORDED", receipt_at, **{
                "Stage": STAGE, "Details": "Approve Plan", "Checkpoint": "Plan Approval",
                "Questions File": str(path.relative_to(scene.root)),
                "Questions SHA-256": F.sha256(path.read_text()),
                "Prompt SHA-256": "0" * 64 if wrong_prompt else card["captured"]["question_digest"],
            }),
            F.audit_block("DECISION_RECORDED", "2026-09-04T10:00:30Z", Stage=STAGE,
                          Decision="Learnings: anything to add?", Options="Nothing to add,Add a note"),
        ))
    marker = record / ".aidlc-human-turn"
    marker.write_text("2026-09-04T10:00:20Z\n")
    os.utime(marker, ns=(2_000_000_000, 2_000_000_000))
    return card, receipt


@pytest.mark.parametrize("uncertain", [False, True])
def test_plan_settles_after_questions_handoff_without_answering_learnings(
    sv, routes, fake_host, scene, uncertain,
):
    card, receipt = approved_then_learnings(sv, routes, fake_host, scene)
    repo = sv.repos.get(scene.repo_id)
    snap = sv.projection.snapshot(repo, "default", INTENT)
    assert snap.questions.origin["kind"] == "audit"
    assert snap.questions.questions[0].prompt == "Learnings: anything to add?"

    async def settle():
        rec = await sv.actions.get(card["action_id"])
        if uncertain:
            for status in ("DeliveryUncertain", "ReconciliationRequired"):
                rec = await sv.actions.transition(
                    rec.action_id, status, expected_generation=rec.status_generation,
                    reason="test_deadline", evidence={"delivery_confirmed": True},
                )
        await sv.reconciler.reconcile_now(rec.action_id)
        assert (await sv.actions.get(rec.action_id)).status == rec.status
        assert sv.storage.lease_list(), "a plan receipt cannot end a still-running turn"
        _finish_the_turn(fake_host, scene.slot_key, wire_text="Approve Plan")
        before = _tree_digest(scene.root)
        await sv.reconciler.reconcile_now(rec.action_id)
        after = await sv.actions.get(rec.action_id)
        assert after.status == "ResolvedNoTransition"
        assert after.resolution["reason"] == "plan_approved"
        assert after.delivery_id == receipt["delivery_id"]
        assert not sv.storage.lease_list()
        assert _tree_digest(scene.root) == before

    asyncio.run(settle())
    _rescan(sv, routes, fake_host, scene)
    live = [a for a in _queue(sv, routes, fake_host) if a["type"] == "question"]
    assert len(live) == 1 and live[0]["status"] == "Queued"
    assert live[0]["evidence"]["questions"]["questions"][0]["prompt"] == "Learnings: anything to add?"
    assert live[0]["delivery"]["delivery_id"] is None


@pytest.mark.parametrize("mismatch", ["old_receipt", "different_file", "different_unit", "different_prompt"])
def test_handoff_receipt_must_belong_to_the_submitted_plan(sv, routes, fake_host, scene, mismatch):
    card, _ = approved_then_learnings(
        sv, routes, fake_host, scene,
        receipt_at="2026-09-04T09:59:00Z" if mismatch == "old_receipt" else "2026-09-04T10:00:20Z",
        wrong_prompt=mismatch == "different_prompt",
    )
    _finish_the_turn(fake_host, scene.slot_key, wire_text="Approve Plan")
    rec = asyncio.run(sv.actions.get(card["action_id"]))
    if mismatch == "different_file":
        rec = replace(rec, evidence={**rec.evidence, "questions": {
            **rec.evidence["questions"], "relpath": "aidlc/another-plan-questions.md",
        }})
    elif mismatch == "different_unit":
        rec = replace(rec, unit="another-unit")
    snap = sv.projection.snapshot(sv.repos.get(scene.repo_id), "default", INTENT)
    assert snap.questions.origin["kind"] == "audit"
    resolution = sv.reconciler.evaluate_resolution(rec, snap, sv.host.slot(scene.slot_key))
    assert not resolution.resolved and resolution.reason == "plan_not_approved"


def test_handoff_receipt_repeats_the_engines_blanked_prompt_digest(sv, routes, fake_host, scene):
    """The receipt carries the prompt's blanked digest; Studio captured the file's own digest."""
    from test_plan_receipt_history import approve, engine_prompt_sha

    record = _record_dir(scene.root)
    path = record / QUESTIONS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    text = F.questions_text(
        stage=STAGE, questions=[("Which store?", ["SQLite", "Postgres"])], answers={1: "A"},
    ) + "\n## Plan Approval\n\n[Answer]:\n"
    path.write_text(text)
    relpath = str(path.relative_to(scene.root))
    prompt = {
        "Stage": STAGE, "Checkpoint": "Code Generation Plan Approval", "Questions File": relpath,
        "Questions SHA-256": F.sha256(text), "Prompt SHA-256": engine_prompt_sha(text),
    }
    assert prompt["Questions SHA-256"] != prompt["Prompt SHA-256"]
    with _shard(scene.root).open("a") as file:
        file.write(F.audit_text(F.audit_block(
            "DECISION_RECORDED", "2026-09-04T09:59:00Z", Decision="Approve this exact plan?",
            Options="[protected exact choices]", **prompt,
        )))
    _rescan(sv, routes, fake_host, scene)
    card = next(a for a in _queue(sv, routes, fake_host) if a["type"] == "question")
    assert card["captured"]["question_digest"] == prompt["Questions SHA-256"]
    status, receipt = _submit(
        sv, routes, fake_host, card, payload={"decision": "approve_plan"}, wire_text="Approve Plan",
    )
    assert status == 200, receipt
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    path.write_text(approve(text))
    with _shard(scene.root).open("a") as file:
        file.write(F.audit_text(
            F.audit_block("HUMAN_TURN", "2026-09-04T10:00:20Z"),
            F.audit_block("PLAN_APPROVAL_RECORDED", "2026-09-04T10:00:20Z", **{
                **prompt, "Details": "Approve Plan", "Questions SHA-256": F.sha256(approve(text)),
            }),
            F.audit_block("DECISION_RECORDED", "2026-09-04T10:00:30Z", Stage=STAGE,
                          Decision="Learnings: anything to add?", Options="Nothing to add,Add a note"),
        ))
    marker = record / ".aidlc-human-turn"
    marker.write_text("2026-09-04T10:00:20Z\n")
    os.utime(marker, ns=(2_000_000_000, 2_000_000_000))
    _finish_the_turn(fake_host, scene.slot_key, wire_text="Approve Plan")
    asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    after = asyncio.run(sv.actions.get(card["action_id"]))
    assert after.status == "ResolvedNoTransition", after.resolution
    assert after.resolution["reason"] == "plan_approved"
    assert not sv.storage.lease_list()
