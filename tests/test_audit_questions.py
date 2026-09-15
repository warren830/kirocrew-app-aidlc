"""Disk-backed enumerated questions: real reader, broker, HTTP handlers and reconciliation."""

from __future__ import annotations

import asyncio
import json
import os
import re
from types import SimpleNamespace

import fixtures as F
import pytest

from test_e2e_gate import (  # noqa: F401 - fixture dependencies
    INTENT, STAGE, QUESTIONS_REL, REQUIREMENTS_REL,
    _finish_the_turn, _gate_state, _get, _post, _queue, _record_dir, _report,
    _rescan, _row, _shard, _submit, _tree_digest, scene,
)
from test_handlers_auth import common, routes, sv  # noqa: F401 - fixtures


PROMPT = "Learnings: anything to add?"
LABELS = ["Nothing to add", "Add a note", "Other"]
ASKED = "2026-09-04T09:59:30Z"
ANSWERED = "2026-09-04T10:00:20Z"


def start_audit():
    return F.audit_text(
        F.audit_block("SESSION_STARTED", "2026-09-04T09:00:00Z"),
        F.audit_block("HUMAN_TURN", "2026-09-04T09:00:01Z"),
        F.audit_block("STAGE_STARTED", "2026-09-04T09:00:02Z", Stage=STAGE),
    )


def decision(ts=ASKED, **fields):
    return F.audit_block(
        "DECISION_RECORDED", ts,
        **{"Stage": STAGE, "Decision": PROMPT, "Options": ",".join(LABELS), **fields},
    )


@pytest.fixture
def repo_dir(repo_builder):
    return (
        repo_builder.with_engine("payload").with_workspace().with_intent(
            INTENT,
            state=_gate_state().replace(f"- [?] {STAGE}", f"- [-] {STAGE}"),
            audit=start_audit() + decision(),
            directive={"version": 1, "stage": STAGE, "state_sha256": "AUTO"},
            artifacts={REQUIREMENTS_REL: "# Requirements\n\n## Review\n\n**Verdict**: READY\n"},
        ).with_git().build()
    )


def snap(sv, scene):
    return sv.projection.snapshot(sv.repos.get(scene.repo_id), "default", INTENT)


def append(scene, *blocks, shard=None):
    path = _record_dir(scene.root) / "audit" / shard if shard else _shard(scene.root)
    path.write_text((path.read_text() if path.exists() else "") + F.audit_text(*blocks))
    return path


def question_card(sv, routes, fake_host, scene):
    _rescan(sv, routes, fake_host, scene)
    cards = [card for card in _queue(sv, routes, fake_host) if card["type"] == "question"]
    assert len(cards) == 1, cards
    return cards[0]


def answer_payload(letter="A", **overrides):
    return {"decision": "answers", "answers": [{"index": 1, "option_letters": [letter], **overrides}]}


def answer_on_disk(scene, **fields):
    append(
        scene,
        F.audit_block("HUMAN_TURN", ANSWERED),
        F.audit_block("QUESTION_ANSWERED", ANSWERED, **{"Stage": STAGE, "Details": LABELS[0], **fields}),
    )
    marker = _record_dir(scene.root) / ".aidlc-human-turn"
    marker.write_text(ANSWERED + "\n")
    os.utime(marker, ns=(2_000_000_000, 2_000_000_000))


@pytest.mark.parametrize("label", ["Free-text note", "Free text", "FREE TEXT NOTE"])
def test_explicit_audit_text_placeholder_requires_content(sv, routes, fake_host, scene, label):
    _shard(scene.root).write_text(start_audit() + decision(
        Decision="Learnings note: what should I record for next time?", Options=label,
    ))
    card = question_card(sv, routes, fake_host, scene)
    option = card["evidence"]["questions"]["questions"][0]["options"][0]
    assert option == {"letter": "A", "text": label, "is_other": True}
    before = _tree_digest(scene.root)
    for body in [None, "", "   ", label]:
        payload = answer_payload(free_text=body)
        status, result = _submit(sv, routes, fake_host, card, payload=payload, wire_text=label)
        assert status == 400 and result["code"] == "answers_incomplete", result
    assert _row(sv, card["action_id"])["delivery_id"] is None
    assert _tree_digest(scene.root) == before


@pytest.mark.parametrize("note", [
    "每条需求都带可判定的验收标准。",
    'Keep punctuation, "quotes", 中文.\nKeep the second line too.',
    r"Keep the literal \n and a real newline." + "\n**Stage**: this is note content",
    "Read {project}/README.md; leave {project}-other unchanged.",
])
def test_audit_text_content_is_sent_verbatim_and_reconciled(sv, routes, fake_host, scene, note):
    note = note.replace("{project}", str(scene.root))
    _shard(scene.root).write_text(start_audit() + decision(
        Decision="Learnings note: what should I record for next time?", Options="Free-text note",
    ))
    card = question_card(sv, routes, fake_host, scene)
    original = _tree_digest(scene.root)
    status, receipt = _submit(
        sv, routes, fake_host, card, payload=answer_payload(free_text=note), wire_text=note,
    )
    assert status == 200, receipt
    assert receipt["wire_text"] == note
    assert _tree_digest(scene.root) == original
    _finish_the_turn(fake_host, scene.slot_key, wire_text=note)
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    recorded = re.sub(re.escape(str(scene.root)) + r"(?=$|[/\\\s])", "<project-dir>", note)
    answer_on_disk(scene, Details=recorded)
    answered = _tree_digest(scene.root)
    asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    assert _row(sv, card["action_id"])["status"] == "ResolvedNoTransition"
    assert _row(sv, card["action_id"])["resolution_json"]["reason"] == "question_answered"
    assert not sv.storage.lease_list()
    assert _tree_digest(scene.root) == answered


@pytest.mark.parametrize("restart", [False, True])
def test_legacy_placeholder_reply_releases_lease_but_keeps_the_question_open(
    sv, routes, fake_host, scene, monkeypatch, restart,
):
    reader_module = __import__(sv.reader.__class__.__module__, fromlist=["is_audit_text_label"])
    _shard(scene.root).write_text(start_audit() + decision(
        Decision="Learnings note: what should I record for next time?", Options="Free-text note",
    ))
    with monkeypatch.context() as old:
        old.setattr(reader_module, "is_audit_text_label", lambda label: False)
        card = question_card(sv, routes, fake_host, scene)
        status, receipt = _submit(
            sv, routes, fake_host, card, payload=answer_payload(), wire_text="Free-text note",
        )
        assert status == 200, receipt
    fake_host.get_slot(scene.slot_key).running = True
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    append(scene, F.audit_block("HUMAN_TURN", ANSWERED))
    marker = _record_dir(scene.root) / ".aidlc-human-turn"
    marker.write_text(ANSWERED + "\n")
    os.utime(marker, ns=(2_000_000_000, 2_000_000_000))
    asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    assert _row(sv, card["action_id"])["status"] == "Processing"
    assert sv.storage.lease_list()
    _finish_the_turn(fake_host, scene.slot_key, wire_text="Free-text note")
    before = _tree_digest(scene.root)
    if restart:
        sv.boot_id = "new-boot-after-placeholder"
        asyncio.run(sv.reconciler.startup())
    else:
        asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    old_row = _row(sv, card["action_id"])
    assert old_row["status"] == "ResolvedNoTransition"
    assert old_row["resolution_json"]["reason"] == "answer_requires_text"
    assert old_row["wire_text"] == "Free-text note"
    assert not sv.storage.lease_list()
    new_card = question_card(sv, routes, fake_host, scene)
    assert new_card["action_id"] != card["action_id"]
    assert new_card["evidence"]["questions"]["pending_count"] == 1
    assert new_card["evidence"]["questions"]["questions"][0]["options"][0]["is_other"]
    assert _tree_digest(scene.root) == before


@pytest.mark.parametrize("case", ["new_question", "missing_presence", "ordinary_choice"])
def test_label_only_recovery_requires_same_text_prompt_and_proven_human_turn(
    sv, routes, fake_host, scene, monkeypatch, case,
):
    reader_module = __import__(sv.reader.__class__.__module__, fromlist=["is_audit_text_label"])
    label = "Add a note" if case == "ordinary_choice" else "Free-text note"
    _shard(scene.root).write_text(start_audit() + decision(Options=label))
    with monkeypatch.context() as old:
        old.setattr(reader_module, "is_audit_text_label", lambda _: False)
        card = question_card(sv, routes, fake_host, scene)
        status, receipt = _submit(sv, routes, fake_host, card, payload=answer_payload(), wire_text=label)
        assert status == 200, receipt
    _finish_the_turn(fake_host, scene.slot_key, wire_text=label)
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    if case != "missing_presence":
        append(scene, F.audit_block("HUMAN_TURN", ANSWERED))
        marker = _record_dir(scene.root) / ".aidlc-human-turn"
        marker.write_text(ANSWERED + "\n")
        os.utime(marker, ns=(2_000_000_000, 2_000_000_000))
    if case == "new_question":
        append(scene, decision("2026-09-04T10:00:30Z", Decision="A different note?", Options=label))
    before = _tree_digest(scene.root)
    asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    row = _row(sv, card["action_id"])
    assert row["status"] in ("Processing", "DeliveryUncertain", "ReconciliationRequired")
    assert (row.get("resolution_json") or {}).get("reason") != "answer_requires_text"
    assert sv.storage.lease_list()
    assert _tree_digest(scene.root) == before


@pytest.mark.parametrize("letter,label", [("A", LABELS[0]), ("C", "Other")])
def test_api_audit_question_submit_answer_reconcile_without_host_pending(
    sv, routes, fake_host, scene, letter, label
):
    original = _tree_digest(scene.root)
    path = f"/repos/{scene.repo_id}/intents/{INTENT}/questions"
    status, body = _get(sv, routes, fake_host, path)
    assert status == 200
    assert body["host_cards"] == []
    assert body["mode"] == "structured"
    view = body["questions"]
    assert view["origin"]["kind"] == "audit"
    assert view["relpath"].endswith("/audit/" + _shard(scene.root).name)
    assert view["questions"][0]["prompt"] == PROMPT
    assert [o["text"] for o in view["questions"][0]["options"]] == LABELS
    assert all(not o["is_other"] for o in view["questions"][0]["options"])
    card = question_card(sv, routes, fake_host, scene)
    assert card["evidence"]["questions"]["mode"] == "structured"
    assert card["captured"]["question_digest"] == view["sha256"]
    status, receipt = _submit(
        sv, routes, fake_host, card, payload=answer_payload(letter), wire_text=label
    )
    assert status == 200, receipt
    assert receipt["lane"] == "human_lane"
    assert receipt["wire_text"] == label
    assert _row(sv, card["action_id"])["status"] == "Delivering"
    assert fake_host.get_slot(scene.slot_key).messages == []
    assert _tree_digest(scene.root) == original

    status, repeated = _submit(
        sv, routes, fake_host, card, payload=answer_payload(letter), wire_text=label
    )
    assert status == 409, repeated
    _finish_the_turn(fake_host, scene.slot_key, wire_text=label)
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    answer_on_disk(scene, Details=label)
    answered_tree = _tree_digest(scene.root)
    for _ in range(3):
        asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    assert _row(sv, card["action_id"])["status"] == "ResolvedNoTransition"
    _rescan(sv, routes, fake_host, scene)
    assert not [c for c in _queue(sv, routes, fake_host) if c["type"] == "question"]
    assert _get(sv, routes, fake_host, path)[1]["questions"] is None
    assert _tree_digest(scene.root) == answered_tree
    transitions = sv.storage.select("action_transitions", {"action_id": card["action_id"]})
    assert sum(t["to_status"] == "Delivering" for t in transitions) == 1


@pytest.mark.parametrize(
    "fields",
    [
        {"Options": "[protected exact choices]"},
        {"Options": ""},
        {"Options": "yes,,no"},
        {"Options": "yes,yes"},
        {"Decision": ""},
        {"Stage": "user-stories"},
        {"Unit": "another-unit"},
        {"Workflow": f"single-stage:{STAGE}"},
        {"Attempt Generation": "unknown"},
    ],
)
def test_unprovable_or_foreign_decision_never_becomes_an_action(sv, scene, fields):
    _shard(scene.root).write_text(start_audit() + decision(**fields))
    assert snap(sv, scene).questions is None


@pytest.mark.parametrize(
    "event,fields,expected_pending",
    [
        ("QUESTION_ANSWERED", {}, False),
        ("SUMMARY_CONFIRMATION_RECORDED", {}, False),
        ("PLAN_APPROVAL_RECORDED", {}, False),
        ("GATE_APPROVED", {}, False),
        ("STAGE_COMPLETED", {}, False),
        ("STAGE_STARTED", {}, False),
        ("STAGE_REVISING", {}, False),
        ("WORKFLOW_STARTED", {}, False),
        ("STAGE_JUMPED", {"Stage": "user-stories"}, False),
        ("QUESTION_ANSWERED", {"Stage": "user-stories"}, True),
        ("QUESTION_ANSWERED", {"Unit": "another-unit"}, True),
        ("QUESTION_ANSWERED", {"Workflow": f"single-stage:{STAGE}"}, True),
        ("STAGE_STARTED", {"Workflow": f"single-stage:{STAGE}"}, True),
    ],
)
def test_pending_question_is_scoped_to_current_main_attempt(sv, scene, event, fields, expected_pending):
    append(scene, F.audit_block(event, "2026-09-04T09:59:40Z", **{"Stage": STAGE, **fields}))
    assert (snap(sv, scene).questions is not None) is expected_pending


@pytest.mark.parametrize("event", ["QUESTION_ANSWERED", "DECISION_RECORDED", "STAGE_STARTED"])
@pytest.mark.parametrize("shard", ["000-other.md", "zz-other.md"])
def test_same_second_cross_shard_cannot_authorize_a_question(sv, scene, event, shard):
    append(
        scene,
        F.audit_block(event, ASKED, Stage=STAGE, Decision="Another prompt", Options="yes,no"),
        shard=shard,
    )
    assert snap(sv, scene).questions is None


def test_same_shard_append_order_allows_a_reopened_question(sv, scene):
    append(scene, F.audit_block("QUESTION_ANSWERED", ASKED, Stage=STAGE), decision(Decision="Again?"))
    questions = snap(sv, scene).questions
    assert questions is not None
    assert questions.questions[0].prompt == "Again?"


def test_structured_file_has_priority_and_unreadable_file_is_not_absence(sv, scene, monkeypatch):
    path = _record_dir(scene.root) / QUESTIONS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("## Q1. File-owned question\n\nA. File choice\n\n[Answer]:\n")
    questions = snap(sv, scene).questions
    assert questions.questions[0].prompt == "File-owned question"
    assert getattr(questions, "origin", None) is None
    security = __import__(sv.reader.__module__.rsplit(".", 1)[0] + ".security", fromlist=["bounded_read"])
    read = security.bounded_read
    monkeypatch.setattr(security, "bounded_read", lambda p, cap: None if p == path else read(p, cap))
    assert snap(sv, scene).questions is None


# Exact unanswered sheet harvested from the precision E2E; its header is display prose.
H3_QUESTIONS = """# Requirements Analysis — Questions

Stage: `requirements-analysis` (2.3) · Intent: `260910-precision-fix` · Depth: Minimal · Test Strategy: Minimal

Grounding for these questions comes from the code knowledge base
(`aidlc/spaces/default/codekb/aidlc-precision-screenshots-CHDuUU/`), the authoritative project
description, and the observed behavior of `converter.py` as it stands today:
`--precision` is parsed and passed correctly, validated at `converter.py:5-6`, then discarded because
`converter.py:7` returns `f"{value:.2f}"`.

## Questions

### Q1 — When `--precision` is outside 0-6, what should the command-line behavior be?

The bounds check already exists and raises `ValueError`, which the project description says to preserve.
What is undecided is the *presentation*: today the exception is uncaught, so the command prints a Python
traceback and exits non-zero. Whether that traceback is part of the CLI's promised behavior determines
whether the regression tests are allowed to pin it, so it has to be settled before the tests are written.

- A. Preserve today's behavior exactly: `format_number` raises `ValueError`, `main` does not catch it, and the command exits non-zero with a traceback. Tests assert the `ValueError` from `format_number` directly and deliberately do not pin the traceback text or the exact exit status. (Recommended)
- B. Keep the `ValueError` in `format_number`, but catch it in `main` and print a short one-line error message to stderr with a non-zero exit. Tests pin that message.
- C. Move the range check into `argparse` (for example `choices=range(0, 7)`), so an out-of-range value fails as a usage error with exit status 2. `format_number` still raises `ValueError` when called directly with a bad value.
- X. Other (please specify)

[Answer]:

### Q2 — How should the requested precision be rendered?

This fixes the expected output strings the tests assert. Option A is what `f"{value:.{precision}f}"`
produces and matches the README example (`1.2345 --precision 3` prints `1.234`); the two options differ
only when the requested precision exceeds the digits actually present.

- A. Fixed-point, keeping trailing zeros: `1.2345 --precision 6` prints `1.234500`, and `--precision 0` prints `1`. (Recommended)
- B. Strip trailing zeros: `1.2345 --precision 6` prints `1.2345`, and `--precision 0` prints `1`.
- X. Other (please specify)

[Answer]:

### Q3 — Where should the new tests live and how should they be run?

The repository has no test directory, no test runner configuration, and no third-party packages, and
`AGENTS.md` restricts tests to the Python standard library. This choice sets what Build and Test executes.

- A. One `test_converter.py` at the repository root, run with `python3 -m unittest test_converter`. CLI cases run the real command through `subprocess`. (Recommended)
- B. One `test_converter.py` at the repository root, but exercise the CLI in-process by patching `sys.argv` and capturing stdout, with no subprocess.
- C. A `tests/test_converter.py` package layout, run with `python3 -m unittest discover`.
- X. Other (please specify)

[Answer]:
"""
UNPARSED_QUESTIONS = """# Questions

Question one — How should the command behave?
- A. Preserve the current behavior.
- B. Change the presentation.

[Answer]:
"""
QUESTION_NOTES = "# Questions\n\nChoose how to answer in the conversation.\n"
ROUTING_PROMPT = "How would you like to answer the 3 requirements questions?"
ROUTING_LABELS = ["Guide me", "I'll edit the file", "Chat"]
BATCH_OPTIONS = (
    "Q1: A preserve today's traceback / B catch and print message / C argparse usage error / X Other; "
    "Q2: A fixed-point trailing zeros / B strip trailing zeros / X Other; "
    "Q3: A root file with subprocess CLI tests / B root file with in-process CLI tests / C tests package with discover / X Other"
)
ANSWERED_H3_QUESTIONS = (
    H3_QUESTIONS.replace("Test Strategy: Minimal", "Test Strategy: Standard").replace("[Answer]:", "[Answer]: A")
    + """
## Consolidated Summary Confirmation

- Q1 — Out-of-range `--precision` preserves today's CLI behavior exactly: `format_number` raises `ValueError`, `main` leaves it uncaught, and the command exits non-zero with a traceback. Tests assert the `ValueError` from `format_number` directly and do not pin traceback wording or the exact exit status.
- Q2 — Fixed-point formatting with the requested number of decimal places, trailing zeros kept: `1.2345 --precision 6` prints `1.234500`, `--precision 0` prints `1`.
- Q3 — One `test_converter.py` at the repository root, run with `python3 -m unittest test_converter`, exercising the real CLI through `subprocess`, standard library only.
- Existing contract preserved unchanged: default precision 2, inclusive bounds 0 through 6, `ValueError` raised outside that range.
- Configuration in force from `aidlc-state.md`: Depth Minimal, Test Strategy Standard. The Minimal test-strategy label in this file's original header was stale and has been corrected to Standard; Standard carries forward into requirements, code generation, and build and test.

Does this all look correct before I generate the requirements artifact?

- Looks correct
- Request changes

[Answer]: Looks correct

"""
)
# Pinned to the real engine receipt and saved sheet: these are deliberately different hash scopes.
CLOSED_SHEET_SHA = "d39610aafad7ed5d41279d5e43cabc7bf4373661b01b41315238917c17085bd0"
CLOSED_CONTENT_SHA = "57380ab3e835228c56dae5993bc4dceab9993a4b7ea0cbeafd41bef53543f26c"
CLOSED_AT = "2026-09-04T09:59:10Z"


def closed_sheet_receipt(scene, timestamp=CLOSED_AT, *, scope="confirmed-content-v1", **fields):
    values = {
        "Stage": STAGE, "Details": "Looks correct", "Checkpoint": "Consolidated Summary Confirmation",
        "Questions File": str((_record_dir(scene.root) / QUESTIONS_REL).relative_to(scene.root)),
        "Questions SHA-256": CLOSED_CONTENT_SHA if scope else CLOSED_SHEET_SHA,
    }
    if scope is not None:
        values["Hash Scope"] = scope
    values.update(fields)
    return F.audit_block("SUMMARY_CONFIRMATION_RECORDED", timestamp, **values)


def install_closed_sheet(scene, *, scope="confirmed-content-v1"):
    path = _record_dir(scene.root) / QUESTIONS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ANSWERED_H3_QUESTIONS)
    assert F.sha256(ANSWERED_H3_QUESTIONS) == CLOSED_SHEET_SHA
    _shard(scene.root).write_text(start_audit() + closed_sheet_receipt(scene, scope=scope) + decision())
    return path


@pytest.fixture
def closed_sheet_scene(repo_builder, studio, clock):
    root = repo_builder.with_workspace().with_intent(
        INTENT,
        state=_gate_state().replace(f"- [?] {STAGE}", f"- [-] {STAGE}"),
        audit=start_audit(),
        directive={"version": 1, "stage": STAGE, "state_sha256": "AUTO"},
    ).build()
    scene = SimpleNamespace(root=root, reader=studio.aidlc_reader.AidlcReader(clock))
    install_closed_sheet(scene)
    return scene


def closed_sheet_questions(scene):
    return scene.reader.stable_boundary_inputs(scene.root, "default", INTENT).questions


@pytest.mark.parametrize("scope", ["confirmed-content-v1", None], ids=["scoped", "legacy_whole_file"])
def test_closed_sheet_yields_to_newer_audit_through_api_submit_and_reconcile(
    sv, routes, fake_host, scene, scope
):
    path = install_closed_sheet(scene, scope=scope)
    original = _tree_digest(scene.root)
    card = question_card(sv, routes, fake_host, scene)
    assert card["evidence"]["questions"]["origin"]["kind"] == "audit"
    assert card["evidence"]["questions"]["questions"][0]["prompt"] == PROMPT
    status, receipt = _submit(sv, routes, fake_host, card, payload=answer_payload(), wire_text=LABELS[0])
    assert status == 200, receipt
    assert receipt["lane"] == "human_lane" and receipt["wire_text"] == LABELS[0]
    assert _tree_digest(scene.root) == original
    assert _submit(sv, routes, fake_host, card, payload=answer_payload(), wire_text=LABELS[0])[0] == 409
    _finish_the_turn(fake_host, scene.slot_key, wire_text=LABELS[0])
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    answer_on_disk(scene)
    for _ in range(3):
        asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    assert _row(sv, card["action_id"])["status"] == "ResolvedNoTransition"
    _rescan(sv, routes, fake_host, scene)
    assert not [c for c in _queue(sv, routes, fake_host) if c["type"] == "question"]
    assert path.read_text() == ANSWERED_H3_QUESTIONS


@pytest.mark.parametrize("text", [
    ANSWERED_H3_QUESTIONS.replace("\n", "\r\n"),
    ANSWERED_H3_QUESTIONS.rstrip() + " \t\n\ufeff",
], ids=["crlf", "ecmascript_trailing_whitespace"])
def test_closed_sheet_scoped_hash_uses_engine_normalization(closed_sheet_scene, text):
    path = _record_dir(closed_sheet_scene.root) / QUESTIONS_REL
    path.write_bytes(text.encode("utf-8"))
    assert F.sha256(text) != CLOSED_SHEET_SHA
    assert closed_sheet_questions(closed_sheet_scene).origin["kind"] == "audit"


@pytest.mark.parametrize("fields", [
    {"Questions File": "another-intent/requirements-analysis-questions.md"},
    {"Questions SHA-256": "0" * 64},
    {"Questions SHA-256": CLOSED_SHEET_SHA},
    {"Hash Scope": "unknown-v2"},
    {"Details": "Request changes"},
    {"Checkpoint": "Plan Approval"},
    {"Stage": "user-stories"},
    {"Unit": "unit-other"},
    {"Workflow": f"single-stage:{STAGE}"},
    {"Attempt Generation": "2"},
])
def test_closed_sheet_requires_a_matching_closure_receipt(closed_sheet_scene, fields):
    scene = closed_sheet_scene
    _shard(scene.root).write_text(start_audit() + closed_sheet_receipt(scene, **fields) + decision())
    assert closed_sheet_questions(scene).origin is None


@pytest.mark.parametrize("case", [
    "missing_receipt", "old_attempt_receipt", "newer_bad_receipt",
    "same_second_cross_shard", "ambiguous_receipts", "edited_file",
    "pending_question", "pending_checkpoint", "rejected_checkpoint",
    "scoped_excluded_section", "answered", "new_attempt", "partial",
    "unreadable_file", "unreadable_second_read", "changed_second_read", "protected",
])
def test_closed_sheet_never_resurrects_unproven_audit(closed_sheet_scene, monkeypatch, case):
    scene = closed_sheet_scene
    path = _record_dir(scene.root) / QUESTIONS_REL
    if case == "missing_receipt":
        _shard(scene.root).write_text(start_audit() + decision())
    elif case == "old_attempt_receipt":
        _shard(scene.root).write_text(
            closed_sheet_receipt(scene, "2026-09-04T08:59:59Z") + start_audit() + decision()
        )
    elif case == "newer_bad_receipt":
        _shard(scene.root).write_text(
            start_audit() + closed_sheet_receipt(scene)
            + closed_sheet_receipt(scene, "2026-09-04T09:59:20Z", **{"Questions SHA-256": "0" * 64})
            + decision()
        )
    elif case == "same_second_cross_shard":
        _shard(scene.root).write_text(start_audit() + decision())
        append(scene, closed_sheet_receipt(scene, ASKED), shard="zz-receipt.md")
    elif case == "ambiguous_receipts":
        append(scene, closed_sheet_receipt(scene), shard="zz-receipt.md")
    elif case in ("edited_file", "pending_question", "pending_checkpoint", "rejected_checkpoint"):
        old, new = {
            "edited_file": ("[Answer]: A", "[Answer]: B"),
            "pending_question": ("[Answer]: A", "[Answer]:"),
            "pending_checkpoint": ("[Answer]: Looks correct", "[Answer]:"),
            "rejected_checkpoint": ("[Answer]: Looks correct", "[Answer]: Request changes"),
        }[case]
        text = ANSWERED_H3_QUESTIONS.replace(old, new, 1)
        path.write_text(text)
        if case != "edited_file":
            # Even a receipt bound to these bytes cannot close a still-pending/rejected sheet.
            _shard(scene.root).write_text(
                start_audit() + closed_sheet_receipt(scene, scope=None, **{"Questions SHA-256": F.sha256(text)})
                + decision()
            )
    elif case == "scoped_excluded_section":
        path.write_text(ANSWERED_H3_QUESTIONS + "## Assumption Confirmation\n\n[Answer]: Confirmed\n")
    elif case == "answered":
        append(scene, F.audit_block("QUESTION_ANSWERED", ANSWERED, Stage=STAGE, Details=LABELS[0]))
    elif case == "new_attempt":
        append(scene, F.audit_block("STAGE_STARTED", ANSWERED, Stage=STAGE))
    elif case == "partial":
        append(scene, F.audit_block("NOTE", ASKED, Details="x" * 2000), shard="zz-large.md")
        read = scene.reader.read_audit
        monkeypatch.setattr(scene.reader, "read_audit", lambda *a, **kw: read(*a, tail_bytes=1500))
    elif case == "protected":
        _shard(scene.root).write_text(
            start_audit() + closed_sheet_receipt(scene) + decision(Options="[protected exact choices]")
        )
    else:
        security = __import__(scene.reader.__module__.rsplit(".", 1)[0] + ".security", fromlist=["bounded_read"])
        read = security.bounded_read
        calls = 0

        def bounded_read(candidate, cap):
            nonlocal calls
            if candidate == path:
                calls += 1
                if case == "unreadable_file" or calls > 1:
                    return b"changed bytes" if case == "changed_second_read" else None
            return read(candidate, cap)

        monkeypatch.setattr(security, "bounded_read", bounded_read)
    questions = closed_sheet_questions(scene)
    assert questions is None or questions.origin is None


def test_closed_sheet_same_shard_order_proves_freshness_within_one_second(closed_sheet_scene):
    scene = closed_sheet_scene
    _shard(scene.root).write_text(start_audit() + closed_sheet_receipt(scene, ASKED) + decision())
    assert closed_sheet_questions(scene).origin["kind"] == "audit"


@pytest.mark.parametrize("change", ["receipt_removed", "receipt_replaced", "file_edited"])
def test_closed_sheet_submit_rechecks_closure_evidence(sv, routes, fake_host, scene, change):
    path = install_closed_sheet(scene)
    card = question_card(sv, routes, fake_host, scene)
    if change == "file_edited":
        path.write_text(ANSWERED_H3_QUESTIONS.replace("[Answer]: A", "[Answer]: B", 1))
    else:
        # Keep the decision at the same append position while modifying its closure evidence.
        text = _shard(scene.root).read_text()
        text = text.replace(
            "SUMMARY_CONFIRMATION_RECORDED" if change == "receipt_removed" else CLOSED_AT,
            "NOTE" if change == "receipt_removed" else "2026-09-04T09:59:11Z",
        )
        _shard(scene.root).write_text(text)
    status, body = _submit(sv, routes, fake_host, card, payload=answer_payload(), wire_text=LABELS[0])
    assert status == 409 and body["code"] == "action_stale", body
    assert _row(sv, card["action_id"])["status"] == "Queued"
    assert fake_host.get_slot(scene.slot_key).messages == []


@pytest.mark.parametrize("checkpoint, accepted", [
    ("Plan Approval", True),
    ("Code Generation Plan Approval", True),
    ("Unrelated Plan Approval", False),
])
def test_closed_sheet_plan_checkpoint_requires_its_own_receipt(closed_sheet_scene, checkpoint, accepted):
    scene = closed_sheet_scene
    path = _record_dir(scene.root) / QUESTIONS_REL
    text = "## Plan Approval\n\n- Approve Plan\n- Request changes\n\n[Answer]: Approve Plan\n"
    path.write_text(text)
    assert closed_sheet_questions(scene).origin is None
    receipt = F.audit_block("PLAN_APPROVAL_RECORDED", CLOSED_AT, **{
        "Stage": STAGE, "Details": "Approve Plan", "Checkpoint": checkpoint,
        "Questions File": str(path.relative_to(scene.root)), "Questions SHA-256": F.sha256(text),
    })
    _shard(scene.root).write_text(start_audit() + receipt + decision())
    assert (closed_sheet_questions(scene).origin is not None) is accepted


CODEGEN_STAGE = "code-generation"
CODEGEN_QUESTIONS_REL = "construction/code-generation/code-generation-questions.md"
CODEGEN_CLOSED_PLAN = """# Code Generation Questions

Stage: `code-generation` · Zero-Unit run

## Plan Approval

[Approval Fingerprint]: sha256:1111111111111111111111111111111111111111111111111111111111111111

- Approve Plan
- Request Changes

[Answer]: Approve Plan
"""


@pytest.fixture
def zero_unit_codegen_scene(repo_builder, studio, clock):
    """Actual per-unit stage slug, with the v2 directive's explicit zero-unit run shape."""
    state = F.state_text(
        F.DEVDELTA_POC, marks={CODEGEN_STAGE: "-"},
        fields={"Current Stage": CODEGEN_STAGE, "Status": "Running", "Scope": "bugfix"},
    )
    receipt = F.audit_block("PLAN_APPROVAL_RECORDED", CLOSED_AT, **{
        "Stage": CODEGEN_STAGE, "Details": "Approve Plan", "Checkpoint": "Code Generation Plan Approval",
        "Questions File": f"aidlc/spaces/default/intents/{INTENT}/{CODEGEN_QUESTIONS_REL}",
        "Questions SHA-256": F.sha256(CODEGEN_CLOSED_PLAN),
    })
    root = repo_builder.with_workspace().with_intent(
        INTENT, state=state, scope="bugfix",
        audit=start_audit().replace(STAGE, CODEGEN_STAGE) + receipt
        + decision(Stage=CODEGEN_STAGE, Options="Nothing to add,Add a note"),
        directive={"version": 2, "kind": "run-stage", "stage": CODEGEN_STAGE, "state_sha256": "AUTO"},
        questions={CODEGEN_QUESTIONS_REL: CODEGEN_CLOSED_PLAN},
    ).build()
    return SimpleNamespace(root=root, reader=studio.aidlc_reader.AidlcReader(clock))


@pytest.mark.parametrize("sheet", ["closed", "missing", "unparsed"])
def test_zero_unit_codegen_exposes_pending_learnings_from_complete_audit(zero_unit_codegen_scene, sheet):
    scene = zero_unit_codegen_scene
    path = _record_dir(scene.root) / CODEGEN_QUESTIONS_REL
    if sheet == "missing":
        path.unlink()
    elif sheet == "unparsed":
        path.write_text("# Code generation notes\n")
    original = _tree_digest(scene.root)
    boundary = scene.reader.stable_boundary_inputs(scene.root, "default", INTENT)
    assert boundary.stable and boundary.audit.complete and not boundary.unit_ambiguous
    assert boundary.stage == CODEGEN_STAGE and boundary.state.active_unit is None
    assert boundary.directive.kind == "run-stage" and boundary.directive.matches_state
    assert boundary.questions is not None
    assert boundary.questions.origin is not None
    assert boundary.questions.origin["kind"] == "audit"
    assert boundary.questions.origin["stage"] == CODEGEN_STAGE
    assert boundary.questions.origin["unit"] is None
    assert boundary.questions.questions[0].prompt == PROMPT
    assert [option.text for option in boundary.questions.questions[0].options] == LABELS[:2]
    assert boundary.questions.pending_count == 1 and boundary.questions.pending_checkpoint is None
    assert _tree_digest(scene.root) == original


@pytest.mark.parametrize("marker_fields", [
    None,
    {"version": 1},
    {"state_sha256": "0" * 64},
    {"kind": "ask"},
    {"kind": "invoke-swarm", "units": ["unit-a"]},
    {"unit": None},
    {"unit": ""},
    {"unit": 7},
    {"units": []},
    {"units": "unknown"},
    {"stage": "functional-design"},
], ids=[
    "no-directive", "legacy-directive", "stale-directive", "not-executing", "swarm",
    "null-unit", "empty-unit", "malformed-unit", "empty-units", "malformed-units", "other-stage",
])
def test_codegen_unknown_unit_scope_never_authorizes_audit_learnings(zero_unit_codegen_scene, marker_fields):
    scene = zero_unit_codegen_scene
    path = _record_dir(scene.root) / ".aidlc-active-directive.json"
    if marker_fields is None:
        path.unlink()
    else:
        path.write_text(json.dumps({**json.loads(path.read_text()), **marker_fields}))
    questions = closed_sheet_questions(scene)
    assert questions is None or questions.origin is None


def test_zero_unit_codegen_does_not_select_historical_unit_question_files(zero_unit_codegen_scene):
    scene = zero_unit_codegen_scene
    for unit in ("unit-a", "unit-b"):
        path = _record_dir(scene.root) / f"construction/{unit}/code-generation/code-generation-questions.md"
        path.parent.mkdir(parents=True)
        path.write_text("## Q1. Historical unit question\n\nA. Unit choice\n\n[Answer]:\n")
    assert scene.reader.list_units(scene.root, "default", INTENT) == ["unit-a", "unit-b"]
    boundary = scene.reader.stable_boundary_inputs(scene.root, "default", INTENT)
    assert not boundary.unit_ambiguous
    assert boundary.questions is not None and boundary.questions.origin is not None
    assert boundary.questions.origin["unit"] is None
    assert boundary.questions.questions[0].prompt == PROMPT


@pytest.mark.parametrize("failure", [
    "partial", "unreadable-shard", "unreadable-sheet", "bad-receipt", "missing-receipt",
    "pending-plan", "answered", "ambiguous-answer", "finished-stage", "active-unit",
])
def test_zero_unit_codegen_preserves_audit_and_receipt_guards(zero_unit_codegen_scene, monkeypatch, failure):
    scene = zero_unit_codegen_scene
    path = _record_dir(scene.root) / CODEGEN_QUESTIONS_REL
    if failure == "partial":
        append(scene, F.audit_block("NOTE", ASKED, Details="x" * 2000), shard="zz-large.md")
        read = scene.reader.read_audit
        monkeypatch.setattr(scene.reader, "read_audit", lambda *a, **kw: read(*a, tail_bytes=1500))
    elif failure == "unreadable-shard":
        (_record_dir(scene.root) / "audit/unreadable.md").mkdir()
    elif failure == "unreadable-sheet":
        security = __import__(scene.reader.__module__.rsplit(".", 1)[0] + ".security", fromlist=["bounded_read"])
        read = security.bounded_read
        monkeypatch.setattr(security, "bounded_read", lambda p, cap: None if p == path else read(p, cap))
    elif failure in ("bad-receipt", "missing-receipt"):
        before, after = (
            (F.sha256(CODEGEN_CLOSED_PLAN), "0" * 64)
            if failure == "bad-receipt" else ("PLAN_APPROVAL_RECORDED", "NOTE")
        )
        _shard(scene.root).write_text(_shard(scene.root).read_text().replace(before, after))
    elif failure == "pending-plan":
        path.write_text(CODEGEN_CLOSED_PLAN.replace("[Answer]: Approve Plan", "[Answer]:"))
    elif failure in ("answered", "ambiguous-answer"):
        append(scene, F.audit_block(
            "QUESTION_ANSWERED", ANSWERED if failure == "answered" else ASKED,
            Stage=CODEGEN_STAGE, Details=LABELS[0],
        ), shard="zz-answer.md" if failure == "ambiguous-answer" else None)
    else:
        state_path = _record_dir(scene.root) / "aidlc-state.md"
        state = state_path.read_text()
        state = (
            state.replace(f"- [-] {CODEGEN_STAGE}", f"- [x] {CODEGEN_STAGE}")
            if failure == "finished-stage" else F.insert_runtime_field(state, "Active Unit", "unit-a")
        )
        state_path.write_text(state)
        marker_path = _record_dir(scene.root) / ".aidlc-active-directive.json"
        marker_path.write_text(json.dumps({**json.loads(marker_path.read_text()), "state_sha256": F.sha256(state)}))
    questions = closed_sheet_questions(scene)
    assert questions is None or questions.origin is None


def test_zero_unit_codegen_learnings_reach_questions_api(sv, routes, fake_host, repo_builder, zero_unit_codegen_scene):
    scene = zero_unit_codegen_scene
    repo_builder.with_engine("payload")
    repo = sv.repos.add(str(scene.root), "zero-unit codegen")
    original = _tree_digest(scene.root)
    status, body = _get(sv, routes, fake_host, f"/repos/{repo.repo_id}/intents/{INTENT}/questions")
    assert status == 200, body
    assert body["mode"] == "structured"
    assert body["questions"]["origin"]["kind"] == "audit"
    assert body["questions"]["origin"]["stage"] == CODEGEN_STAGE
    assert body["questions"]["questions"][0]["prompt"] == PROMPT
    assert _tree_digest(scene.root) == original


@pytest.mark.parametrize("change", ["none", "old_receipt_generation", "claim_advanced", "claim_unreadable"])
def test_closed_sheet_unit_receipt_must_match_current_claim(sv, scene, change):
    install_closed_sheet(scene)
    generations, key = activate_unit(sv, scene)
    generation = "1" if change == "old_receipt_generation" else "2"
    _shard(scene.root).write_text(
        start_audit() + closed_sheet_receipt(scene, Unit="unit-a", Attempt_Generation=generation)
        + decision(Unit="unit-a", Attempt_Generation="2")
    )
    if change == "claim_advanced":
        generations.write_text(json.dumps({key: 3}))
    elif change == "claim_unreadable":
        generations.write_text("{broken")
    questions = snap(sv, scene).questions
    assert (questions is not None and questions.origin is not None) is (change == "none")


def test_h3_question_parser_keeps_all_three_pending_boundaries(studio):
    parsed = studio.aidlc_reader.parse_questions_file(H3_QUESTIONS, "questions.md", "0" * 64)
    assert parsed.pending_count == 3
    assert [question.index for question in parsed.questions] == [1, 2, 3]
    assert [question.prompt for question in parsed.questions] == [
        "When `--precision` is outside 0-6, what should the command-line behavior be?",
        "How should the requested precision be rendered?",
        "Where should the new tests live and how should they be run?",
    ]
    assert [[option.letter for option in question.options] for question in parsed.questions] == [
        ["A", "B", "C", "X"], ["A", "B", "X"], ["A", "B", "C", "X"],
    ]
    assert all(not question.answered and question.answer is None for question in parsed.questions)
    assert all("### Q" not in question.raw.split("\n", 1)[1] for question in parsed.questions)


@pytest.mark.parametrize("heading", ["# Appendix", "## Sources", "### Explanation", "#### Rationale"])
def test_h3_question_parser_generic_headings_cannot_supply_options_or_answers(studio, heading):
    text = (
        "## Q1. First question\n\n- A. First choice\n\n"
        f"{heading}\n\n- B. Explanatory example, not a choice\n\n[Answer]: Not a real answer\n\n"
        "### Q2 — Second question\n\n- C. Second choice\n\n[Answer]:\n"
    )
    parsed = studio.aidlc_reader.parse_questions_file(text, "questions.md", "0" * 64)
    assert [question.index for question in parsed.questions] == [1, 2]
    assert parsed.pending_count == 2
    assert [[option.text for option in question.options] for question in parsed.questions] == [
        ["First choice"], ["Second choice"],
    ]
    assert all(question.answer is None for question in parsed.questions)


@pytest.mark.parametrize("opening,closing", [
    ("```markdown", "```"),
    ("~~~markdown", "~~~"),
    ("````markdown", "`````"),
    ("~~~~markdown", "~~~~~"),
    ("   ```markdown", "  ```\t"),
    ("  ~~~markdown", "   ~~~~  "),
])
def test_question_fences_do_not_reopen_an_answered_h2_question(studio, opening, closing):
    text = (
        "## Q1. Keep this output?\nA. Keep it\n\n"
        f"{opening}\n# Generated report\n{closing}\n\n[Answer]: A. Keep it"
    )
    parsed = studio.aidlc_reader.parse_questions_file(text, "questions.md", "f" * 64)
    assert parsed.pending_count == 0
    assert len(parsed.questions) == 1
    assert parsed.questions[0].answer == "A. Keep it"
    assert parsed.questions[0].raw == text
    assert parsed.sha256 == "f" * 64


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_question_fences_do_not_create_example_questions_or_checkpoints(studio, fence):
    text = (
        f"{fence}markdown\n"
        "## Q99. H2 example\nA. Example choice\n[Answer]:\n\n"
        "### Q1 — Example prompt\nA. Example choice\n[Answer]:\n\n"
        "## Plan Approval\n- Approve Plan\n[Answer]:\n"
        f"{fence}\n\n"
        "### Q2 — Real question\nA. Real choice\n[Answer]:\n"
    )
    parsed = studio.aidlc_reader.parse_questions_file(text, "questions.md", "0" * 64)
    assert [question.index for question in parsed.questions] == [2]
    assert parsed.pending_count == 1
    assert parsed.plan_approval is None
    assert parsed.pending_checkpoint is None
    assert [option.text for option in parsed.questions[0].options] == ["Real choice"]


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_question_fences_cannot_supply_options_or_answers(studio, fence):
    text = (
        "## Q1. Keep this output?\nA. Keep it\n\n"
        f"{fence}markdown\nB. Example option\n[Answer]: Example answer\n{fence}\n\n[Answer]:\n"
    )
    parsed = studio.aidlc_reader.parse_questions_file(text, "questions.md", "0" * 64)
    assert parsed.pending_count == 1
    assert parsed.questions[0].answer is None
    assert [option.text for option in parsed.questions[0].options] == ["Keep it"]


@pytest.mark.parametrize("opening,invalid_close,closing", [
    ("````", "```", "````"),
    ("~~~~", "~~~", "~~~~"),
    ("```", "~~~", "```"),
    ("~~~", "```", "~~~"),
    ("```", "    ```", "```"),
    ("~~~", "\t~~~", "~~~"),
    ("```", "``` trailing text", "```"),
    ("~~~", "~~~ trailing text", "~~~"),
])
def test_question_fences_stay_open_until_a_valid_close(studio, opening, invalid_close, closing):
    text = (
        "## Q1. First question\nA. Keep it\n[Answer]: A. Keep it\n\n"
        f"{opening}markdown\n{invalid_close}\n"
        "### Q99 — Still an example\nA. Example choice\n[Answer]:\n"
        f"{closing}\n\n"
        "### Q2 — Real question\nB. Real choice\n[Answer]:\n"
    )
    parsed = studio.aidlc_reader.parse_questions_file(text, "questions.md", "0" * 64)
    assert [question.index for question in parsed.questions] == [1, 2]
    assert parsed.questions[0].answer == "A. Keep it"
    assert parsed.pending_count == 1
    assert [option.text for option in parsed.questions[1].options] == ["Real choice"]


@pytest.mark.parametrize("fence", ["```", "~~~"])
@pytest.mark.parametrize("answered", [True, False])
def test_question_fences_ignore_an_unclosed_remainder_without_changing_prior_answers(studio, fence, answered):
    answer = "[Answer]: A. Keep it\n" if answered else ""
    text = (
        f"## Q1. Keep this output?\nA. Keep it\n{answer}\n{fence}markdown\n"
        "# Generated report\n[Answer]: Example answer\n\n"
        "### Q2 — Example prompt\nB. Example choice\n[Answer]:\n"
        "## Plan Approval\n- Approve Plan\n[Answer]:\n"
    )
    parsed = studio.aidlc_reader.parse_questions_file(text, "questions.md", "0" * 64)
    assert [question.index for question in parsed.questions] == [1]
    assert parsed.pending_count == (0 if answered else 1)
    assert parsed.questions[0].answer == ("A. Keep it" if answered else None)
    assert parsed.plan_approval is None


@pytest.mark.parametrize("not_a_fence", [
    "``markdown", "~~markdown", "    ```markdown", "\t~~~markdown", "```lang`name",
])
def test_question_fences_do_not_start_from_invalid_openers(studio, not_a_fence):
    text = f"{not_a_fence}\n### Q1 — Real question\nA. Real choice\n[Answer]:\n"
    parsed = studio.aidlc_reader.parse_questions_file(text, "questions.md", "0" * 64)
    assert [question.index for question in parsed.questions] == [1]
    assert parsed.pending_count == 1


def test_question_fences_cannot_displace_a_pending_audit_decision(sv, scene):
    path = _record_dir(scene.root) / QUESTIONS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "```markdown\n## Q1. Example\nA. Example choice\n[Answer]:\n```\n"
    path.write_text(text)
    original = _tree_digest(scene.root)
    questions = snap(sv, scene).questions
    assert questions.origin is not None and questions.origin["kind"] == "audit"
    assert questions.questions[0].prompt == PROMPT
    assert [option.text for option in questions.questions[0].options] == LABELS
    assert _tree_digest(scene.root) == original


def unparsed_questions(scene, text=UNPARSED_QUESTIONS):
    path = _record_dir(scene.root) / QUESTIONS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    _shard(scene.root).write_text(start_audit() + decision(
        Decision=ROUTING_PROMPT, Options=",".join(ROUTING_LABELS),
    ))
    return path


@pytest.mark.parametrize("letter,label", zip("ABC", ROUTING_LABELS))
def test_question_notes_without_pending_tags_use_audited_routing_through_api_and_reconcile(
    sv, routes, fake_host, scene, letter, label
):
    path = unparsed_questions(scene, QUESTION_NOTES)
    original = _tree_digest(scene.root)
    endpoint = f"/repos/{scene.repo_id}/intents/{INTENT}/questions"
    status, body = _get(sv, routes, fake_host, endpoint)
    assert status == 200
    assert body["host_cards"] == []
    assert body["mode"] == "structured"
    view = body["questions"]
    assert view["origin"]["kind"] == "audit"
    assert view["pending_count"] == 1
    assert view["questions"][0]["prompt"] == ROUTING_PROMPT
    assert [option["text"] for option in view["questions"][0]["options"]] == ROUTING_LABELS
    card = question_card(sv, routes, fake_host, scene)
    status, receipt = _submit(
        sv, routes, fake_host, card, payload=answer_payload(letter), wire_text=label,
    )
    assert status == 200, receipt
    assert receipt["lane"] == "human_lane"
    assert receipt["wire_text"] == label
    assert _tree_digest(scene.root) == original
    assert fake_host.get_slot(scene.slot_key).messages == []
    assert _submit(sv, routes, fake_host, card, payload=answer_payload(letter), wire_text=label)[0] == 409
    _finish_the_turn(fake_host, scene.slot_key, wire_text=label)
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    answer_on_disk(scene, Details=label)
    for _ in range(3):
        asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    assert _row(sv, card["action_id"])["status"] == "ResolvedNoTransition"
    _rescan(sv, routes, fake_host, scene)
    remaining = [c for c in _queue(sv, routes, fake_host) if c["type"] == "question"]
    assert all(c["action_id"] != card["action_id"] and not c["evidence"]["questions"].get("origin") for c in remaining)
    after = _get(sv, routes, fake_host, endpoint)[1]
    assert after["mode"] == "degraded"
    assert after["questions"]["pending_count"] == 0
    assert after["questions"]["questions"] == []
    assert path.read_text() == QUESTION_NOTES
    transitions = sv.storage.select("action_transitions", {"action_id": card["action_id"]})
    assert sum(t["to_status"] == "Delivering" for t in transitions) == 1


@pytest.mark.parametrize(
    "failure", ["partial", "read_failed", "protected", "answered", "new_attempt", "same_second", "no_audit"],
)
def test_question_notes_cannot_relax_audit_evidence_guards(sv, scene, monkeypatch, failure):
    path = unparsed_questions(scene, QUESTION_NOTES)
    if failure == "partial":
        append(scene, F.audit_block("NOTE", ASKED, Details="x" * 2000), shard="zz-large.md")
        read = sv.reader.read_audit
        monkeypatch.setattr(sv.reader, "read_audit", lambda *a, **kw: read(*a, tail_bytes=1500))
    elif failure == "read_failed":
        security = __import__(sv.reader.__module__.rsplit(".", 1)[0] + ".security", fromlist=["bounded_read"])
        read = security.bounded_read
        monkeypatch.setattr(security, "bounded_read", lambda p, cap: None if p == path else read(p, cap))
    elif failure == "protected":
        _shard(scene.root).write_text(start_audit() + decision(Options="[protected exact choices]"))
    elif failure == "answered":
        append(scene, F.audit_block("QUESTION_ANSWERED", ANSWERED, Stage=STAGE, Details="Guide me"))
    elif failure == "new_attempt":
        append(scene, F.audit_block("STAGE_STARTED", ANSWERED, Stage=STAGE))
    elif failure == "same_second":
        append(scene, F.audit_block("QUESTION_ANSWERED", ASKED, Stage=STAGE), shard="zz-answer.md")
    else:
        _shard(scene.root).write_text(start_audit())
    questions = snap(sv, scene).questions
    assert questions is None or (questions.origin is None and questions.pending_count == 0)


@pytest.mark.parametrize("change", ["file_edit", "structured_replacement", "read_failed"])
def test_question_notes_audit_submit_rechecks_the_file(sv, routes, fake_host, scene, monkeypatch, change):
    path = unparsed_questions(scene, QUESTION_NOTES)
    card = question_card(sv, routes, fake_host, scene)
    if change == "file_edit":
        path.write_text(QUESTION_NOTES + "\nA changed question description.\n")
    elif change == "structured_replacement":
        path.write_text("## Q1. File-owned question\n\nA. File choice\n\n[Answer]:\n")
    else:
        security = __import__(sv.reader.__module__.rsplit(".", 1)[0] + ".security", fromlist=["bounded_read"])
        read = security.bounded_read
        monkeypatch.setattr(security, "bounded_read", lambda p, cap: None if p == path else read(p, cap))
    status, body = _submit(
        sv, routes, fake_host, card, payload=answer_payload(), wire_text=ROUTING_LABELS[0],
    )
    assert status == 409 and body["code"] == "action_stale", body
    assert _row(sv, card["action_id"])["status"] == "Queued"
    assert fake_host.get_slot(scene.slot_key).messages == []


@pytest.mark.parametrize("text,pending", [
    (UNPARSED_QUESTIONS, 1),
    ((F.FIXTURES / "question-fallback/domain-followups.md").read_text(), 3),
], ids=["legacy_unparsed", "domain_followups"])
def test_unsupported_pending_answers_keep_conversation_fallback_over_audit_routing(
    sv, routes, fake_host, scene, text, pending,
):
    path = unparsed_questions(scene, text)
    original = _tree_digest(scene.root)
    endpoint = f"/repos/{scene.repo_id}/intents/{INTENT}/questions"
    status, body = _get(sv, routes, fake_host, endpoint)
    assert status == 200 and body["mode"] == "degraded"
    view = body["questions"]
    assert not view.get("origin")
    assert view["unsupported_pending_count"] == view["pending_count"] == pending
    assert all(q["answered"] for q in view["questions"])
    card = question_card(sv, routes, fake_host, scene)
    assert card["evidence"]["questions"] == view
    assert card["primary"] is None and card["decisions"] == []
    status, result = _submit(
        sv, routes, fake_host, card, payload=answer_payload(), wire_text=ROUTING_LABELS[0],
    )
    assert status == 400 and result["code"] == "invalid_decision"
    assert result["details"]["reason"] == "unsupported_question_format"
    assert _row(sv, card["action_id"])["delivery_id"] is None
    assert fake_host.get_slot(scene.slot_key).messages == []
    assert path.read_text() == text and _tree_digest(scene.root) == original


@pytest.mark.parametrize("structured", [
    "## Q1. File-owned question\n\nA. File choice\n\n[Answer]: File choice\n",
    H3_QUESTIONS,
    H3_QUESTIONS.replace("[Answer]:", "[Answer]: A. Recorded answer"),
    "## Plan Approval\n\n- Approve Plan\n\n[Answer]:\n",
    "## Consolidated Summary Confirmation\n\n- Looks correct\n\n[Answer]: Looks correct\n",
], ids=["h2_answered", "h3_pending", "h3_answered", "plan_approval", "summary_confirmed"])
def test_unparsed_sections_do_not_displace_recognized_file_questions_or_checkpoints(sv, scene, structured):
    path = unparsed_questions(scene)
    path.write_text(UNPARSED_QUESTIONS + "\n" + structured)
    questions = snap(sv, scene).questions
    assert questions is not None
    assert questions.origin is None
    assert questions.relpath.endswith(QUESTIONS_REL)


@pytest.mark.parametrize("options", [",".join(ROUTING_LABELS), BATCH_OPTIONS], ids=["answer_mode", "combined_batch"])
def test_h3_file_owns_the_actual_three_questions_despite_audit_options(
    sv, routes, fake_host, scene, options
):
    path = unparsed_questions(scene)
    path.write_text(H3_QUESTIONS)
    _shard(scene.root).write_text(start_audit() + decision(Options=options))
    original = _tree_digest(scene.root)
    status, body = _get(sv, routes, fake_host, f"/repos/{scene.repo_id}/intents/{INTENT}/questions")
    assert status == 200
    view = body["questions"]
    assert not view.get("origin")
    assert view["pending_count"] == 3
    assert [q["index"] for q in view["questions"]] == [1, 2, 3]
    assert [len(q["options"]) for q in view["questions"]] == [4, 3, 4]
    assert view["sha256"] == F.sha256(H3_QUESTIONS)
    card = question_card(sv, routes, fake_host, scene)
    assert card["captured"]["question_digest"] == F.sha256(H3_QUESTIONS)
    assert len(card["evidence"]["questions"]["questions"]) == 3
    assert _tree_digest(scene.root) == original


@pytest.mark.parametrize("failure", ["partial", "unreadable_shard", "unreadable_directory"])
def test_incomplete_audit_is_never_an_actionable_question(sv, scene, monkeypatch, failure):
    if failure == "partial":
        # Keep the complete decision/attempt evidence; a different shard may hide its answer.
        append(scene, F.audit_block("NOTE", ASKED, Details="x" * 2000), shard="zz-large.md")
        read = sv.reader.read_audit
        monkeypatch.setattr(sv.reader, "read_audit", lambda *a, **kw: read(*a, tail_bytes=1500))
    elif failure == "unreadable_shard":
        path = append(scene, F.audit_block("QUESTION_ANSWERED", ANSWERED, Stage=STAGE), shard="zz-answer.md")
        security = __import__(sv.reader.__module__.rsplit(".", 1)[0] + ".security", fromlist=["bounded_read"])
        read = security.bounded_read
        monkeypatch.setattr(security, "bounded_read", lambda p, cap: None if p == path else read(p, cap))
    else:
        from pathlib import Path
        read = Path.iterdir
        def fail(path):
            if path == _record_dir(scene.root) / "audit":
                raise PermissionError("audit is unreadable")
            return read(path)
        monkeypatch.setattr(Path, "iterdir", fail)
    assert snap(sv, scene).questions is None


@pytest.mark.parametrize("change", ["answered", "reopened", "attempt_reset"])
def test_stale_audit_card_disappears_and_cannot_be_submitted(
    sv, routes, fake_host, scene, change
):
    card = question_card(sv, routes, fake_host, scene)
    if change == "reopened":
        append(scene, decision("2026-09-04T09:59:40Z"))
    else:
        append(scene, F.audit_block(
            "QUESTION_ANSWERED" if change == "answered" else "STAGE_STARTED",
            "2026-09-04T09:59:40Z", Stage=STAGE,
        ))
    status, body = _submit(sv, routes, fake_host, card, payload=answer_payload(), wire_text=LABELS[0])
    assert status == 409 and body["code"] == "action_stale", body
    _rescan(sv, routes, fake_host, scene)
    cards = [c for c in _queue(sv, routes, fake_host) if c["type"] == "question"]
    assert all(c["action_id"] != card["action_id"] for c in cards)
    assert len(cards) == (1 if change == "reopened" else 0)
    assert _row(sv, card["action_id"])["status"] == "Cancelled"


def test_audit_evidence_is_rechecked_after_cursor_switch(sv, routes, fake_host, scene, monkeypatch):
    card = question_card(sv, routes, fake_host, scene)
    switch = sv.engine.switch_cursor_sync
    def answer_during_switch(*args, **kwargs):
        result = switch(*args, **kwargs)
        append(scene, F.audit_block("QUESTION_ANSWERED", ANSWERED, Stage=STAGE, Details=LABELS[0]))
        return result
    monkeypatch.setattr(sv.engine, "switch_cursor_sync", answer_during_switch)
    status, body = _submit(sv, routes, fake_host, card, payload=answer_payload(), wire_text=LABELS[0])
    assert status == 409 and body["code"] == "action_stale", body
    assert _row(sv, card["action_id"])["status"] == "Queued"
    repo = sv.repos.get(scene.repo_id)
    assert asyncio.run(sv.scheduler.get(repo.resolved_identity)) is None


@pytest.mark.parametrize(
    "fields",
    [{"Unit": "another-unit"}, {"Attempt Generation": "2"}, {"Workflow": f"single-stage:{STAGE}"},
     {"Details": "An answer the user never sent"}],
)
def test_reconcile_cannot_consume_a_foreign_or_different_answer(sv, routes, fake_host, scene, fields):
    card = question_card(sv, routes, fake_host, scene)
    status, receipt = _submit(sv, routes, fake_host, card, payload=answer_payload(), wire_text=LABELS[0])
    assert status == 200, receipt
    _finish_the_turn(fake_host, scene.slot_key, wire_text=LABELS[0])
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    answer_on_disk(scene, **fields)
    for _ in range(3):
        asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    assert _row(sv, card["action_id"])["status"] == "Processing"


def test_host_options_cannot_replace_disk_labels_or_enable_protected_choices(
    sv, routes, fake_host, scene
):
    slot = fake_host.get_slot(scene.slot_key)
    slot._question_pending["host-q"] = {"question": "Host override", "options": ["Wrong choice"]}
    questions = snap(sv, scene).questions
    assert questions is not None
    assert [o.text for o in questions.questions[0].options] == LABELS
    _shard(scene.root).write_text(start_audit() + decision(Options="[protected exact choices]"))
    assert snap(sv, scene).questions is None


def test_audit_answer_must_select_an_offered_label(sv, routes, fake_host, scene):
    card = question_card(sv, routes, fake_host, scene)
    payload = {"decision": "answers", "answers": [{"index": 1, "option_letters": [], "free_text": "Invented"}]}
    status, body = _submit(sv, routes, fake_host, card, payload=payload, wire_text="Invented")
    assert status == 400 and body["code"] == "invalid_decision", body
    assert _row(sv, card["action_id"])["status"] == "Queued"


def activate_unit(sv, scene, generation="2"):
    record = _record_dir(scene.root)
    state = (record / "aidlc-state.md").read_text()
    state, count = re.subn(r"^- \*\*Active Unit\*\*:.*$", "- **Active Unit**: unit-a", state, flags=re.M)
    if not count:
        state = F.insert_runtime_field(state, "Active Unit", "unit-a")
    (record / "aidlc-state.md").write_text(state)
    (record / ".aidlc-active-directive.json").write_text(
        json.dumps({"version": 1, "stage": STAGE, "state_sha256": F.sha256(state)})
    )
    _shard(scene.root).write_text(start_audit() + decision(Unit="unit-a", Attempt_Generation=generation))
    uuid = sv.reader.registry(scene.root, "default")[0].uuid
    generations = scene.root / "aidlc" / ".aidlc-claim-generations.json"
    generations.write_text(json.dumps({f"default/{uuid}/unit-a": int(generation)}))
    return generations, f"default/{uuid}/unit-a"


def test_unit_question_uses_active_unit_and_current_claim_generation(sv, scene):
    generations, key = activate_unit(sv, scene)
    questions = snap(sv, scene).questions
    assert questions is not None
    assert questions.origin["unit"] == "unit-a"
    assert questions.origin["attempt_generation"] == "2"
    append(scene, F.audit_block("QUESTION_ANSWERED", ANSWERED, Stage=STAGE, Unit="unit-a",
                               Attempt_Generation="1", Details=LABELS[0]))
    assert snap(sv, scene).questions is not None
    generations.write_text(json.dumps({key: 3}))
    assert snap(sv, scene).questions is None


def test_a_unit_claim_change_alone_invalidates_submit(sv, routes, fake_host, scene):
    generations, key = activate_unit(sv, scene)
    card = question_card(sv, routes, fake_host, scene)
    generations.write_text(json.dumps({key: 3}))
    status, body = _submit(sv, routes, fake_host, card, payload=answer_payload(), wire_text=LABELS[0])
    assert status == 409 and body["code"] == "action_stale", body
    assert fake_host.get_slot(scene.slot_key).messages == []
    _rescan(sv, routes, fake_host, scene)
    assert _row(sv, card["action_id"])["status"] == "Cancelled"


def test_a_unit_claim_change_alone_invalidates_reconciliation(
    sv, routes, fake_host, scene
):
    generations, key = activate_unit(sv, scene)
    card = question_card(sv, routes, fake_host, scene)
    status, receipt = _submit(sv, routes, fake_host, card, payload=answer_payload(), wire_text=LABELS[0])
    assert status == 200, receipt
    _finish_the_turn(fake_host, scene.slot_key, wire_text=LABELS[0])
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    generations.write_text(json.dumps({key: 3}))
    answer_on_disk(scene, Unit="unit-a", Attempt_Generation="2")
    for _ in range(3):
        asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    assert _row(sv, card["action_id"])["status"] == "Processing"


def test_unreadable_claim_generation_is_not_a_legacy_attempt(sv, scene):
    generations, _ = activate_unit(sv, scene)
    generations.write_text("{broken")
    assert snap(sv, scene).questions is None


def test_claim_metadata_without_intent_identity_cannot_authorize_a_question(sv, scene):
    activate_unit(sv, scene)
    (_record_dir(scene.root).parent / "intents.json").write_text("{broken")
    assert snap(sv, scene).questions is None


def test_unsafe_claim_path_does_not_override_structured_questions(sv, scene):
    activate_unit(sv, scene)
    path = _record_dir(scene.root) / QUESTIONS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("## Q1. File-owned question\n\nA. File choice\n\n[Answer]:\n")
    (scene.root / "aidlc" / ".aidlc-unit-scope.json").symlink_to(scene.root.parent / "outside.json")
    questions = snap(sv, scene).questions
    assert questions is not None
    assert questions.questions[0].prompt == "File-owned question"
    assert questions.origin is None


@pytest.mark.parametrize("case", ["different_shard", "different_prompt", "new_attempt", "partial"])
def test_answer_must_follow_the_captured_question_unambiguously(
    sv, routes, fake_host, scene, monkeypatch, case
):
    # The prompt and delivery share a second, which is normal for fast human responses.
    _shard(scene.root).write_text(start_audit() + decision("2026-09-04T10:00:00Z"))
    card = question_card(sv, routes, fake_host, scene)
    status, receipt = _submit(sv, routes, fake_host, card, payload=answer_payload(), wire_text=LABELS[0])
    assert status == 200, receipt
    _finish_the_turn(fake_host, scene.slot_key, wire_text=LABELS[0])
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    if case == "different_prompt":
        append(scene, decision("2026-09-04T10:00:01Z", Decision="A different question"))
    elif case == "new_attempt":
        append(scene, F.audit_block("STAGE_STARTED", "2026-09-04T10:00:01Z", Stage=STAGE))
    if case == "different_shard":
        append(scene, F.audit_block("QUESTION_ANSWERED", "2026-09-04T10:00:00Z",
                                   Stage=STAGE, Details=LABELS[0]), shard="zz-answer.md")
        append(scene, F.audit_block("HUMAN_TURN", ANSWERED))
        marker = _record_dir(scene.root) / ".aidlc-human-turn"
        marker.write_text(ANSWERED)
    else:
        answer_on_disk(scene)
    if case == "partial":
        append(scene, F.audit_block("NOTE", ASKED, Details="x" * 2000), shard="zz-large.md")
        read = sv.reader.read_audit
        monkeypatch.setattr(sv.reader, "read_audit", lambda *a, **kw: read(*a, tail_bytes=1500))
    for _ in range(3):
        asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    assert _row(sv, card["action_id"])["status"] == "Processing"


def test_an_answer_before_the_next_question_still_settles_the_original(sv, routes, fake_host, scene):
    card = question_card(sv, routes, fake_host, scene)
    status, receipt = _submit(sv, routes, fake_host, card, payload=answer_payload(), wire_text=LABELS[0])
    assert status == 200, receipt
    _finish_the_turn(fake_host, scene.slot_key, wire_text=LABELS[0])
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    answer_on_disk(scene)
    append(scene, decision("2026-09-04T10:00:21Z", Decision="Next question"))
    for _ in range(3):
        asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    assert _row(sv, card["action_id"])["status"] == "ResolvedNoTransition"
    assert snap(sv, scene).questions.questions[0].prompt == "Next question"


@pytest.mark.parametrize("mark,pending", [("-", True), ("r", True), ("x", False), ("s", False)])
def test_finished_stage_cannot_reopen_an_audit_question(sv, scene, mark, pending):
    record = _record_dir(scene.root)
    path = record / "aidlc-state.md"
    state = path.read_text().replace(f"- [-] {STAGE}", f"- [{mark}] {STAGE}")
    path.write_text(state)
    (record / ".aidlc-active-directive.json").write_text(
        json.dumps({"version": 1, "stage": STAGE, "state_sha256": F.sha256(state)})
    )
    assert (snap(sv, scene).questions is not None) is pending
