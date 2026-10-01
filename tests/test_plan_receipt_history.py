"""Historical consent can settle its delivery without approving a changed plan."""

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import fixtures as F
import pytest
from test_audit_questions import question_card
from test_e2e_gate import (  # noqa: F401
    INTENT, _finish_the_turn, _record_dir, _report, _row, _shard, _submit,
    _tree_digest, scene,
)
from test_handlers_auth import common, routes, sv  # noqa: F401

STAGE = "code-generation"
QUESTIONS = "construction/code-generation/code-generation-questions.md"
OLD_FINGERPRINT = "sha256:" + "1" * 64
NEW_FINGERPRINT = "sha256:" + "2" * 64
EPOCH = "sha256:" + "3" * 64
PLAN = f"""# Code Generation Questions

Stage: `code-generation` · Zero-Unit run

## Plan Approval

[Approval Fingerprint]: {OLD_FINGERPRINT}

- Approve Plan
- Request Changes

[Answer]:
"""


def plan_fields():
    return {
        "Stage": STAGE, "Checkpoint": "Code Generation Plan Approval",
        "Plan Target": "stage:code-generation", "Intent": INTENT,
        "Directive Epoch": EPOCH, "Run floor": "STAGE_STARTED:2026-09-04T09:00:02Z#1",
        "Approval Fingerprint": OLD_FINGERPRINT,
        "Questions File": f"aidlc/spaces/default/intents/{INTENT}/{QUESTIONS}",
        "Questions SHA-256": F.sha256(PLAN), "Prompt SHA-256": F.sha256(PLAN),
        "Session": "test-codegen-session",
    }


@pytest.fixture
def repo_dir(repo_builder):
    state = F.state_text(
        F.DEVDELTA_POC, marks={STAGE: "-"},
        fields={"Current Stage": STAGE, "Status": "Running", "Scope": "bugfix",
                "Lifecycle Phase": "CONSTRUCTION"},
    )
    return repo_builder.with_engine("payload").with_workspace().with_intent(
        INTENT, state=state, scope="bugfix",
        audit=F.audit_text(
            F.audit_block("STAGE_STARTED", "2026-09-04T09:00:02Z", Stage=STAGE),
            F.audit_block("DECISION_RECORDED", "2026-09-04T09:59:00Z",
                          Decision="Approve this exact plan?", Options="Approve Plan,Request Changes",
                          **plan_fields()),
        ),
        directive={"version": 2, "kind": "run-stage", "stage": STAGE, "state_sha256": "AUTO"},
        questions={QUESTIONS: PLAN},
    ).with_git().build()


@pytest.mark.parametrize("case", [
    "changed_plan", "reset_same_question", "wrong_prompt", "wrong_fingerprint",
    "wrong_session", "wrong_intent", "wrong_target", "wrong_unit", "wrong_epoch",
    "missing_receipt", "intervening_decision", "missing_presence", "still_running",
    "missing_approved_hash", "same_approved_hash", "wrong_workflow",
])
def test_recorded_plan_approval_settles_only_the_original_reply(
    sv, routes, fake_host, scene, case,
):
    card = question_card(sv, routes, fake_host, scene)
    # Use the actual registry UUID, rather than an assumed fixture identifier.
    intent_uuid = card["intent"]["uuid"]
    shard = _shard(scene.root)
    shard.write_text(shard.read_text().replace(f"**Intent**: {INTENT}", f"**Intent**: {intent_uuid}"))
    card = question_card(sv, routes, fake_host, scene)
    status, receipt = _submit(sv, routes, fake_host, card,
                              payload={"decision": "approve_plan"}, wire_text="Approve Plan")
    assert status == 200, receipt
    _finish_the_turn(fake_host, scene.slot_key, wire_text="Approve Plan")
    fake_host.get_slot(scene.slot_key).messages[-1]["content"] = "The plan changed; approval is needed again."
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    fields = {**plan_fields(), "Intent": intent_uuid, "Details": "Approve Plan",
              "Questions SHA-256": F.sha256(PLAN.replace("[Answer]:", "[Answer]: Approve Plan"))}
    field_changes = {
        "wrong_prompt": ("Prompt SHA-256", "4" * 64),
        "wrong_fingerprint": ("Approval Fingerprint", NEW_FINGERPRINT),
        "wrong_session": ("Session", "another-session"),
        "wrong_intent": ("Intent", "another-intent"),
        "wrong_target": ("Plan Target", "unit:another-unit"),
        "wrong_unit": ("Unit", "another-unit"),
        "wrong_epoch": ("Directive Epoch", "sha256:" + "4" * 64),
        "missing_approved_hash": ("Questions SHA-256", ""),
        "same_approved_hash": ("Questions SHA-256", F.sha256(PLAN)),
        "wrong_workflow": ("Workflow", "another-workflow"),
    }
    if case in field_changes:
        key, value = field_changes[case]
        fields[key] = value
    blocks = []
    if case != "missing_presence":
        blocks.append(F.audit_block("HUMAN_TURN", "2026-09-04T10:00:20Z"))
        marker = _record_dir(scene.root) / ".aidlc-human-turn"
        marker.write_text("2026-09-04T10:00:20Z\n")
        os.utime(marker, ns=(2_000_000_000, 2_000_000_000))
    if case == "intervening_decision":
        blocks.append(F.audit_block("DECISION_RECORDED", "2026-09-04T10:00:20Z",
                                    Decision="A different prompt", Options="Approve Plan,Request Changes",
                                    **{**plan_fields(), "Intent": intent_uuid}))
    if case != "missing_receipt":
        blocks.append(F.audit_block("PLAN_APPROVAL_RECORDED", "2026-09-04T10:00:21Z", **fields))
    shard.write_text(shard.read_text() + F.audit_text(*blocks))
    current = PLAN if case == "reset_same_question" else PLAN.replace(OLD_FINGERPRINT, NEW_FINGERPRINT)
    (_record_dir(scene.root) / QUESTIONS).write_text(current)
    if case == "still_running":
        fake_host.get_slot(scene.slot_key).running = True
    before = _tree_digest(scene.root)
    asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    row = _row(sv, card["action_id"])
    if case in ("changed_plan", "reset_same_question"):
        assert row["status"] == "ResolvedNoTransition"
        assert row["resolution_json"]["reason"] == "plan_approval_recorded_before_reset"
        assert row["resolution_json"]["evidence"]["current_plan_requires_approval"] is True
        assert not sv.storage.lease_list()
    else:
        assert row["status"] in ("Processing", "DeliveryUncertain", "ReconciliationRequired")
        assert sv.storage.lease_list()
    assert len(fake_host.get_slot(scene.slot_key).messages) == 2
    assert _tree_digest(scene.root) == before


@pytest.mark.parametrize("complete", [True, False])
def test_unit_scoped_history_uses_the_captured_file_and_target(studio, complete):
    reader = studio.aidlc_reader
    path = f"aidlc/spaces/default/intents/{INTENT}/construction/u1/{STAGE}/{STAGE}-questions.md"
    fields = {**plan_fields(), "Unit": "u1", "Plan Target": "unit:u1", "Questions File": path}
    text = F.audit_text(
        F.audit_block("DECISION_RECORDED", "2026-09-04T09:59:00Z",
                      Decision="Approve this plan?", Options="Approve Plan,Request Changes", **fields),
        F.audit_block("PLAN_APPROVAL_RECORDED", "2026-09-04T10:00:21Z", **{
            **fields, "Details": "Approve Plan", "Questions SHA-256": "5" * 64,
        }),
    )
    events = reader.parse_audit_shard(text, "test.md", 0)
    decision = events[0]
    rec = SimpleNamespace(
        unit="u1", intent_uuid=INTENT, delivering_at="2026-09-04T10:00:00Z",
        captured={"question_digest": F.sha256(PLAN)},
        evidence={"questions": {"relpath": path}, "audit": {"boundary_event": {
            "shard": "test.md", "pos": decision.pos,
        }}},
    )
    audit = SimpleNamespace(
        events=events, complete=complete,
        shards=[SimpleNamespace(relpath="audit/test.md")],
    )
    questions = reader.parse_questions_file(
        PLAN.replace(OLD_FINGERPRINT, NEW_FINGERPRINT), path, "6" * 64,
    )
    reconciler = object.__new__(studio.reconciler.Reconciler)
    found = reconciler._historical_plan_approval(
        rec, SimpleNamespace(audit=audit), STAGE, questions,
    )
    assert (found is not None) is complete


#: ECMAScript ``String.prototype.trimEnd`` whitespace.
JS_TRIM = (
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
ENGINE = Path(__file__).resolve().parents[1] / "payload/aidlc-kiro/.kiro/tools/aidlc-testing-posture.ts"
ENGINE_PROMPT_EXPRESSION = r'.replace(/^\[Answer\]:[ \t]*.*$/gm, "[Answer]:")'


def engine_prompt_sha(text):
    """``Prompt SHA-256`` the way the engine writes it: every answer blanked, trimEnd, one LF."""
    blanked = re.sub(r"(?m)^\[Answer\]:[ \t]*[^\n\r\u2028\u2029]*", "[Answer]:", text)
    return F.sha256(blanked.rstrip(JS_TRIM) + "\n")


def approve(text):
    head, _, tail = text.rpartition("[Answer]:")
    return head + "[Answer]: Approve Plan" + tail.lstrip(" \t")


SHAPES = {
    "answered_question": PLAN.replace(
        "## Plan Approval",
        "## Q1. Which test runner should the plan use?\n\nA. pytest\nB. unittest\nX. Other\n\n"
        "[Answer]: A\n\n## Plan Approval",
    ),
    "trailing_blank_line": PLAN + "\n",
    "blank_answer_with_space": PLAN.replace("[Answer]:\n", "[Answer]: \n"),
    "crlf": PLAN.replace("\n", "\r\n"),
}


def test_prompt_digest_mirror_matches_the_engine_expression():
    source = ENGINE.read_text()
    assert source.count(ENGINE_PROMPT_EXPRESSION) >= 2, "the engine changed how it digests a prompt"
    bun = shutil.which("bun")
    if bun is None:
        pytest.skip("real Bun is required to evaluate the engine's expression")
    script = (
        "const t = await Bun.stdin.text();"
        "const h = new Bun.CryptoHasher('sha256');"
        f"h.update(`${{t{ENGINE_PROMPT_EXPRESSION}.trimEnd()}}\\n`);"
        "console.log(h.digest('hex'));"
    )
    for text in [PLAN, *SHAPES.values(), approve(SHAPES["answered_question"])]:
        done = subprocess.run([bun, "-e", script], input=text.encode(), capture_output=True,
                              timeout=20, check=True)
        assert done.stdout.decode().strip() == engine_prompt_sha(text)


@pytest.mark.parametrize("shape", [*SHAPES, "boundary_elsewhere"])
def test_recorded_plan_approval_uses_the_engines_two_digests(sv, routes, fake_host, scene, shape):
    """The prompt's whole-file digest binds the capture; its blanked digest binds the receipt."""
    text = SHAPES.get(shape, PLAN)
    assert shape == "boundary_elsewhere" or engine_prompt_sha(text) != F.sha256(text)
    assert engine_prompt_sha(approve(text)) == engine_prompt_sha(text)
    intent_uuid = sv.reader.registry(scene.root, "default")[0].uuid
    (_record_dir(scene.root) / QUESTIONS).write_bytes(text.encode())
    prompt = {**plan_fields(), "Intent": intent_uuid,
              "Questions SHA-256": F.sha256(text), "Prompt SHA-256": engine_prompt_sha(text)}
    shard = _shard(scene.root)
    shard.write_text(F.audit_text(
        F.audit_block("STAGE_STARTED", "2026-09-04T09:00:02Z", Stage=STAGE),
        F.audit_block("DECISION_RECORDED", "2026-09-04T09:59:00Z", Decision="Approve this exact plan?",
                      Options="[protected exact choices]", **prompt),
    ))
    if shape == "boundary_elsewhere":
        # A newer boundary row from another clone becomes the card's captured boundary.
        (shard.parent / "000-other-clone.md").write_text(F.audit_text(F.audit_block(
            "QUESTION_ANSWERED", "2026-09-04T09:59:30Z", Stage="functional-design", Details="A",
        )))
    card = question_card(sv, routes, fake_host, scene)
    assert card["captured"]["question_digest"] == F.sha256(text)
    if shape == "boundary_elsewhere":
        assert card["evidence"]["audit"]["boundary_event"]["shard"] == "000-other-clone.md"
    status, receipt = _submit(sv, routes, fake_host, card,
                              payload={"decision": "approve_plan"}, wire_text="Approve Plan")
    assert status == 200, receipt
    _finish_the_turn(fake_host, scene.slot_key, wire_text="Approve Plan")
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    marker = _record_dir(scene.root) / ".aidlc-human-turn"
    marker.write_text("2026-09-04T10:00:20Z\n")
    os.utime(marker, ns=(2_000_000_000, 2_000_000_000))
    shard.write_text(shard.read_text() + F.audit_text(
        F.audit_block("HUMAN_TURN", "2026-09-04T10:00:20Z"),
        F.audit_block("PLAN_APPROVAL_RECORDED", "2026-09-04T10:00:21Z", **{
            **prompt, "Details": "Approve Plan", "Questions SHA-256": F.sha256(approve(text)),
        }),
    ))
    (_record_dir(scene.root) / QUESTIONS).write_bytes(
        text.replace(OLD_FINGERPRINT, NEW_FINGERPRINT).encode()
    )
    asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    row = _row(sv, card["action_id"])
    assert row["status"] == "ResolvedNoTransition", row["resolution_json"]
    assert row["resolution_json"]["reason"] == "plan_approval_recorded_before_reset"
    assert row["resolution_json"]["evidence"]["current_plan_requires_approval"] is True
    assert not sv.storage.lease_list()
    assert len(fake_host.get_slot(scene.slot_key).messages) == 2
