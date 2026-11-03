"""Disk-backed enumerated questions: real reader, broker, HTTP handlers and reconciliation."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import fixtures as F
import pytest

from _helpers_a import fail_bounded_read, stamp_human_turn, truncate_audit
from test_e2e_gate import (  # noqa: F401 - fixture dependencies
    INTENT, STAGE, QUESTIONS_REL, REQUIREMENTS_REL, _finish_the_turn, _gate_state, _get, _queue, _record_dir,
    _report, _rescan, _row, _shard, _submit, _tree_digest, scene,
)
from test_handlers_auth import common, routes, sv  # noqa: F401 - fixtures

PROMPT = "Learnings: anything to add?"
NOTE_PROMPT = "Learnings note: what should I record for next time?"
LABELS = ["Nothing to add", "Add a note", "Other"]
ASKED = "2026-09-04T09:59:30Z"
ANSWERED = "2026-09-04T10:00:20Z"
FILE_QUESTION = "## Q1. File-owned question\n\nA. File choice\n\n[Answer]:\n"


def start_audit():
    return F.audit_text(
        F.audit_block("SESSION_STARTED", "2026-09-04T09:00:00Z"),
        F.audit_block("HUMAN_TURN", "2026-09-04T09:00:01Z"),
        F.audit_block("STAGE_STARTED", "2026-09-04T09:00:02Z", Stage=STAGE),
    )


def decision(ts=ASKED, **fields):
    return F.audit_block("DECISION_RECORDED", ts,
                         **{"Stage": STAGE, "Decision": PROMPT, "Options": ",".join(LABELS), **fields})


@pytest.fixture
def repo_dir(repo_builder):
    return repo_builder.with_engine("payload").with_workspace().with_intent(
        INTENT, state=_gate_state().replace(f"- [?] {STAGE}", f"- [-] {STAGE}"), audit=start_audit() + decision(),
        directive={"version": 1, "stage": STAGE, "state_sha256": "AUTO"},
        artifacts={REQUIREMENTS_REL: "# Requirements\n\n## Review\n\n**Verdict**: READY\n"},
    ).with_git().build()


def snap(sv, scene):
    return sv.projection.snapshot(sv.repos.get(scene.repo_id), "default", INTENT)


def append(scene, *blocks, shard=None):
    path = _record_dir(scene.root) / "audit" / shard if shard else _shard(scene.root)
    path.write_text((path.read_text() if path.exists() else "") + F.audit_text(*blocks))
    return path


def rewrite_audit(scene, *blocks: str, start: bool = True) -> None:
    """Replace the shard with a fresh attempt (``start_audit``) followed by ``blocks``."""
    _shard(scene.root).write_text((start_audit() if start else "") + "".join(blocks))


def write_questions(scene, text: str) -> Path:
    path = _record_dir(scene.root) / QUESTIONS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def questions_view(sv, routes, fake_host, repo_id):
    """The intent's questions as its detail page (and every question card) carries them."""
    status, body = _get(sv, routes, fake_host, f"/repos/{repo_id}/intents/{INTENT}")
    assert status == 200, body
    return body["intent"]["questions"]


def question_cards(sv, routes, fake_host, scene):
    _rescan(sv, routes, fake_host, scene)
    return [card for card in _queue(sv, routes, fake_host) if card["type"] == "question"]


def question_card(sv, routes, fake_host, scene):
    cards = question_cards(sv, routes, fake_host, scene)
    assert len(cards) == 1, cards
    return cards[0]


def answer_payload(letter="A", **overrides):
    return {"decision": "answers", "answers": [{"index": 1, "option_letters": [letter], **overrides}]}


def submit_ok(sv, routes, fake_host, card, wire=LABELS[0], payload=None):
    status, receipt = _submit(sv, routes, fake_host, card, payload=payload or answer_payload(), wire_text=wire)
    assert status == 200, receipt
    return receipt


def turn_reported(sv, routes, fake_host, scene, card, receipt, wire=LABELS[0]):
    """The host ran the turn and the browser reported the delivery."""
    _finish_the_turn(fake_host, scene.slot_key, wire_text=wire)
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200


def reconcile(sv, card, times=3) -> dict:
    for _ in range(times):
        asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    return _row(sv, card["action_id"])


def answer_on_disk(scene, **fields):
    append(scene, F.audit_block("HUMAN_TURN", ANSWERED),
           F.audit_block("QUESTION_ANSWERED", ANSWERED, **{"Stage": STAGE, "Details": LABELS[0], **fields}))
    stamp_human_turn(_record_dir(scene.root), ANSWERED)


@contextlib.contextmanager
def _text_label_off(sv, monkeypatch):
    """The legacy reader that did not know free-text placeholders (``is_audit_text_label`` always False)."""
    reader_module = __import__(sv.reader.__class__.__module__, fromlist=["is_audit_text_label"])
    with monkeypatch.context() as old:
        old.setattr(reader_module, "is_audit_text_label", lambda label: False)
        yield


@pytest.mark.parametrize("label", ["Free-text note", "Free text", "FREE TEXT NOTE"])
def test_explicit_audit_text_placeholder_requires_content(sv, routes, fake_host, scene, label):
    rewrite_audit(scene, decision(Decision=NOTE_PROMPT, Options=label))
    card = question_card(sv, routes, fake_host, scene)
    assert card["evidence"]["questions"]["questions"][0]["options"][0] == {"letter": "A", "text": label, "is_other": True}
    before = _tree_digest(scene.root)
    for body in [None, "", "   ", label]:
        status, result = _submit(sv, routes, fake_host, card, payload=answer_payload(free_text=body), wire_text=label)
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
    rewrite_audit(scene, decision(Decision=NOTE_PROMPT, Options="Free-text note"))
    card = question_card(sv, routes, fake_host, scene)
    original = _tree_digest(scene.root)
    receipt = submit_ok(sv, routes, fake_host, card, note, answer_payload(free_text=note))
    assert receipt["wire_text"] == note
    assert _tree_digest(scene.root) == original
    turn_reported(sv, routes, fake_host, scene, card, receipt, note)
    answer_on_disk(scene, Details=re.sub(re.escape(str(scene.root)) + r"(?=$|[/\\\s])", "<project-dir>", note))
    answered = _tree_digest(scene.root)
    row = reconcile(sv, card, times=1)
    assert row["status"] == "ResolvedNoTransition"
    assert row["resolution_json"]["reason"] == "question_answered"
    assert not sv.scheduler.live()
    assert _tree_digest(scene.root) == answered


@pytest.mark.parametrize("restart", [False, True])
def test_legacy_placeholder_reply_releases_lease_but_keeps_the_question_open(
    sv, routes, fake_host, scene, monkeypatch, restart,
):
    rewrite_audit(scene, decision(Decision=NOTE_PROMPT, Options="Free-text note"))
    with _text_label_off(sv, monkeypatch):
        card = question_card(sv, routes, fake_host, scene)
        receipt = submit_ok(sv, routes, fake_host, card, "Free-text note")
    fake_host.get_slot(scene.slot_key).running = True
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    append(scene, F.audit_block("HUMAN_TURN", ANSWERED))
    stamp_human_turn(_record_dir(scene.root), ANSWERED)
    assert reconcile(sv, card, times=1)["status"] == "Delivered"
    assert sv.scheduler.live()
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
    assert not sv.scheduler.live()
    new_card = question_card(sv, routes, fake_host, scene)
    assert new_card["action_id"] != card["action_id"]
    assert new_card["evidence"]["questions"]["pending_count"] == 1
    assert new_card["evidence"]["questions"]["questions"][0]["options"][0]["is_other"]
    assert _tree_digest(scene.root) == before


@pytest.mark.parametrize("case", ["new_question", "missing_presence", "ordinary_choice"])
def test_label_only_recovery_requires_same_text_prompt_and_proven_human_turn(
    sv, routes, fake_host, scene, monkeypatch, case,
):
    label = "Add a note" if case == "ordinary_choice" else "Free-text note"
    rewrite_audit(scene, decision(Options=label))
    with _text_label_off(sv, monkeypatch):
        card = question_card(sv, routes, fake_host, scene)
        receipt = submit_ok(sv, routes, fake_host, card, label)
    turn_reported(sv, routes, fake_host, scene, card, receipt, label)
    if case != "missing_presence":
        append(scene, F.audit_block("HUMAN_TURN", ANSWERED))
        stamp_human_turn(_record_dir(scene.root), ANSWERED)
    if case == "new_question":
        append(scene, decision("2026-09-04T10:00:30Z", Decision="A different note?", Options=label))
    before = _tree_digest(scene.root)
    row = reconcile(sv, card, times=1)
    if case == "missing_presence":  # without the human turn, settling is refused and a human must look
        assert row["status"] == "ReconciliationRequired"
    else:  # the turn ended on AI-DLC's own reply and nothing on disk records this answer
        assert (row["status"], row["resolution_json"]["reason"]) == ("ResolvedNoTransition", "answer_not_recorded")
    assert (row.get("resolution_json") or {}).get("reason") != "answer_requires_text"
    assert bool(sv.scheduler.live()) == (row["status"] == "Delivered")  # only in flight holds the repo
    assert _tree_digest(scene.root) == before


@pytest.mark.parametrize("letter,label", [("A", LABELS[0]), ("C", "Other")])
def test_api_audit_question_submit_answer_reconcile_without_host_pending(sv, routes, fake_host, scene, letter, label):
    original = _tree_digest(scene.root)
    view = questions_view(sv, routes, fake_host, scene.repo_id)
    assert view["host_card"] is None and view["mode"] == "structured"
    assert view["origin"]["kind"] == "audit"
    assert view["relpath"].endswith("/audit/" + _shard(scene.root).name)
    assert view["questions"][0]["prompt"] == PROMPT
    assert [o["text"] for o in view["questions"][0]["options"]] == LABELS
    assert all(not o["is_other"] for o in view["questions"][0]["options"])
    card = question_card(sv, routes, fake_host, scene)
    assert card["evidence"]["questions"]["mode"] == "structured"
    assert card["captured"]["question_digest"] == view["sha256"]
    receipt = submit_ok(sv, routes, fake_host, card, label, answer_payload(letter))
    assert receipt["lane"] == "human_lane" and receipt["wire_text"] == label
    assert _row(sv, card["action_id"])["status"] == "Delivering"
    assert fake_host.get_slot(scene.slot_key).messages == []
    assert _tree_digest(scene.root) == original
    status, repeated = _submit(sv, routes, fake_host, card, payload=answer_payload(letter), wire_text=label)
    assert status == 409, repeated
    turn_reported(sv, routes, fake_host, scene, card, receipt, label)
    answer_on_disk(scene, Details=label)
    answered_tree = _tree_digest(scene.root)
    assert reconcile(sv, card)["status"] == "ResolvedNoTransition"
    assert not question_cards(sv, routes, fake_host, scene)
    assert questions_view(sv, routes, fake_host, scene.repo_id) is None
    assert _tree_digest(scene.root) == answered_tree
    transitions = sv.storage.select("action_transitions", {"action_id": card["action_id"]})
    assert sum(t["to_status"] == "Delivering" for t in transitions) == 1


@pytest.mark.parametrize("fields", [
    {"Options": "[protected exact choices]"}, {"Options": ""}, {"Options": "yes,,no"}, {"Options": "yes,yes"},
    {"Decision": ""}, {"Stage": "user-stories"}, {"Unit": "another-unit"}, {"Workflow": f"single-stage:{STAGE}"},
    {"Attempt Generation": "unknown"},
])
def test_unprovable_or_foreign_decision_never_becomes_an_action(sv, scene, fields):
    rewrite_audit(scene, decision(**fields))
    assert snap(sv, scene).questions is None


@pytest.mark.parametrize("event,fields,expected_pending", [
    ("QUESTION_ANSWERED", {}, False), ("SUMMARY_CONFIRMATION_RECORDED", {}, False),
    ("PLAN_APPROVAL_RECORDED", {}, False), ("GATE_APPROVED", {}, False), ("STAGE_COMPLETED", {}, False),
    ("STAGE_STARTED", {}, False), ("STAGE_REVISING", {}, False), ("WORKFLOW_STARTED", {}, False),
    ("STAGE_JUMPED", {"Stage": "user-stories"}, False), ("QUESTION_ANSWERED", {"Stage": "user-stories"}, True),
    ("QUESTION_ANSWERED", {"Unit": "another-unit"}, True),
    ("QUESTION_ANSWERED", {"Workflow": f"single-stage:{STAGE}"}, True),
    ("STAGE_STARTED", {"Workflow": f"single-stage:{STAGE}"}, True),
])
def test_pending_question_is_scoped_to_current_main_attempt(sv, scene, event, fields, expected_pending):
    append(scene, F.audit_block(event, "2026-09-04T09:59:40Z", **{"Stage": STAGE, **fields}))
    assert (snap(sv, scene).questions is not None) is expected_pending


@pytest.mark.parametrize("event", ["QUESTION_ANSWERED", "DECISION_RECORDED", "STAGE_STARTED"])
@pytest.mark.parametrize("shard", ["000-other.md", "zz-other.md"])
def test_same_second_cross_shard_cannot_authorize_a_question(sv, scene, event, shard):
    append(scene, F.audit_block(event, ASKED, Stage=STAGE, Decision="Another prompt", Options="yes,no"), shard=shard)
    assert snap(sv, scene).questions is None


def test_same_shard_append_order_allows_a_reopened_question(sv, scene):
    append(scene, F.audit_block("QUESTION_ANSWERED", ASKED, Stage=STAGE), decision(Decision="Again?"))
    questions = snap(sv, scene).questions
    assert questions is not None
    assert questions.questions[0].prompt == "Again?"


def test_structured_file_has_priority_and_unreadable_file_is_not_absence(sv, scene, monkeypatch):
    path = write_questions(scene, FILE_QUESTION)
    questions = snap(sv, scene).questions
    assert questions.questions[0].prompt == "File-owned question"
    assert getattr(questions, "origin", None) is None
    fail_bounded_read(monkeypatch, sv.reader, path)
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


def closed_sheet_receipt(scene, timestamp=CLOSED_AT, *, scope="confirmed-content-v2", **fields):
    values = {"Stage": STAGE, "Details": "Looks correct", "Checkpoint": "Consolidated Summary Confirmation",
              "Questions File": str((_record_dir(scene.root) / QUESTIONS_REL).relative_to(scene.root)),
              "Questions SHA-256": CLOSED_CONTENT_SHA if scope else CLOSED_SHEET_SHA}
    if scope is not None:
        values["Hash Scope"] = scope
    return F.audit_block("SUMMARY_CONFIRMATION_RECORDED", timestamp, **{**values, **fields})


def install_closed_sheet(scene, *, scope="confirmed-content-v2"):
    path = write_questions(scene, ANSWERED_H3_QUESTIONS)
    assert F.sha256(ANSWERED_H3_QUESTIONS) == CLOSED_SHEET_SHA
    rewrite_audit(scene, closed_sheet_receipt(scene, scope=scope), decision())
    return path


@pytest.fixture
def closed_sheet_scene(repo_builder, studio, clock):
    root = repo_builder.with_workspace().with_intent(
        INTENT, state=_gate_state().replace(f"- [?] {STAGE}", f"- [-] {STAGE}"), audit=start_audit(),
        directive={"version": 1, "stage": STAGE, "state_sha256": "AUTO"},
    ).build()
    scene = SimpleNamespace(root=root, reader=studio.aidlc_reader.AidlcReader(clock))
    install_closed_sheet(scene)
    return scene


def closed_sheet_questions(scene):
    return scene.reader.stable_boundary_inputs(scene.root, "default", INTENT).questions


def assert_not_audit_owned(questions):
    assert questions is None or questions.origin is None


@pytest.mark.parametrize("scope", ["confirmed-content-v2", "confirmed-content-v1", None],
                         ids=["scoped", "scoped_v1", "legacy_whole_file"])
def test_closed_sheet_yields_to_newer_audit_through_api_submit_and_reconcile(sv, routes, fake_host, scene, scope):
    path = install_closed_sheet(scene, scope=scope)
    original = _tree_digest(scene.root)
    card = question_card(sv, routes, fake_host, scene)
    assert card["evidence"]["questions"]["origin"]["kind"] == "audit"
    assert card["evidence"]["questions"]["questions"][0]["prompt"] == PROMPT
    receipt = submit_ok(sv, routes, fake_host, card)
    assert receipt["lane"] == "human_lane" and receipt["wire_text"] == LABELS[0]
    assert _tree_digest(scene.root) == original
    assert _submit(sv, routes, fake_host, card, payload=answer_payload(), wire_text=LABELS[0])[0] == 409
    turn_reported(sv, routes, fake_host, scene, card, receipt)
    answer_on_disk(scene)
    assert reconcile(sv, card)["status"] == "ResolvedNoTransition"
    assert not question_cards(sv, routes, fake_host, scene)
    assert path.read_text() == ANSWERED_H3_QUESTIONS


@pytest.mark.parametrize("text", [
    ANSWERED_H3_QUESTIONS.replace("\n", "\r\n"), ANSWERED_H3_QUESTIONS.rstrip() + " \t\n\ufeff",
], ids=["crlf", "ecmascript_trailing_whitespace"])
def test_closed_sheet_scoped_hash_uses_engine_normalization(closed_sheet_scene, text):
    (_record_dir(closed_sheet_scene.root) / QUESTIONS_REL).write_bytes(text.encode("utf-8"))
    assert F.sha256(text) != CLOSED_SHEET_SHA
    assert closed_sheet_questions(closed_sheet_scene).origin["kind"] == "audit"


@pytest.mark.parametrize("fields", [
    {"Questions File": "another-intent/requirements-analysis-questions.md"}, {"Questions SHA-256": "0" * 64},
    {"Questions SHA-256": CLOSED_SHEET_SHA}, {"Hash Scope": "confirmed-content-v3"}, {"Details": "Request changes"},
    {"Checkpoint": "Plan Approval"}, {"Stage": "user-stories"}, {"Unit": "unit-other"},
    {"Workflow": f"single-stage:{STAGE}"}, {"Attempt Generation": "2"},
])
def test_closed_sheet_requires_a_matching_closure_receipt(closed_sheet_scene, fields):
    rewrite_audit(closed_sheet_scene, closed_sheet_receipt(closed_sheet_scene, **fields), decision())
    assert closed_sheet_questions(closed_sheet_scene).origin is None


@pytest.mark.parametrize("case", [
    "missing_receipt", "old_attempt_receipt", "newer_bad_receipt", "same_second_cross_shard", "ambiguous_receipts",
    "edited_file", "pending_question", "pending_checkpoint", "rejected_checkpoint", "scoped_excluded_section",
    "answered", "new_attempt", "partial", "unreadable_file", "unreadable_second_read", "changed_second_read",
    "protected",
])
def test_closed_sheet_never_resurrects_unproven_audit(closed_sheet_scene, monkeypatch, case):
    scene = closed_sheet_scene
    path = _record_dir(scene.root) / QUESTIONS_REL
    if case == "missing_receipt":
        rewrite_audit(scene, decision())
    elif case == "old_attempt_receipt":
        rewrite_audit(scene, closed_sheet_receipt(scene, "2026-09-04T08:59:59Z"), start_audit(), decision(), start=False)
    elif case == "newer_bad_receipt":
        rewrite_audit(scene, closed_sheet_receipt(scene), closed_sheet_receipt(
            scene, "2026-09-04T09:59:20Z", **{"Questions SHA-256": "0" * 64}), decision())
    elif case == "same_second_cross_shard":
        rewrite_audit(scene, decision())
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
            rewrite_audit(scene, closed_sheet_receipt(scene, scope=None, **{"Questions SHA-256": F.sha256(text)}),
                          decision())
    elif case == "scoped_excluded_section":
        path.write_text(ANSWERED_H3_QUESTIONS + "## Assumption Confirmation\n\n[Answer]: Confirmed\n")
    elif case == "answered":
        append(scene, F.audit_block("QUESTION_ANSWERED", ANSWERED, Stage=STAGE, Details=LABELS[0]))
    elif case == "new_attempt":
        append(scene, F.audit_block("STAGE_STARTED", ANSWERED, Stage=STAGE))
    elif case == "partial":
        append(scene, F.audit_block("NOTE", ASKED, Details="x" * 2000), shard="zz-large.md")
        truncate_audit(monkeypatch, scene.reader)
    elif case == "protected":
        rewrite_audit(scene, closed_sheet_receipt(scene), decision(Options="[protected exact choices]"))
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
    assert_not_audit_owned(closed_sheet_questions(scene))


ASSUMPTION_SECTION = (
    "## Assumption Confirmation\n\n- The demo covers the extra features [assumption]\n\n"
    "A. Accept assumptions\nB. Convert to follow-up questions\n\n[Answer]: A. Accept assumptions\n"
)
ASSUMPTION_ANSWERED_AT = "2026-09-04T09:59:20Z"


def assumption_answer(ts=ASSUMPTION_ANSWERED_AT, **fields):
    return F.audit_block("QUESTION_ANSWERED", ts, **{"Stage": STAGE, "Details": "A. Accept assumptions", **fields})


def test_summary_receipt_binds_the_sheet_before_a_later_assumption_section(closed_sheet_scene):
    """AI-DLC 2.10 appends Assumption Confirmation after hashing the confirmed summary (A37)."""
    scene = closed_sheet_scene
    (_record_dir(scene.root) / QUESTIONS_REL).write_text(ANSWERED_H3_QUESTIONS + ASSUMPTION_SECTION)
    rewrite_audit(scene, closed_sheet_receipt(scene), assumption_answer(), decision())
    questions = closed_sheet_questions(scene)
    assert questions.origin["kind"] == "audit"
    assert questions.questions[0].prompt == PROMPT
    assert [r.event for r in questions.closed_file_receipts] == ["SUMMARY_CONFIRMATION_RECORDED", "QUESTION_ANSWERED"]


@pytest.mark.parametrize("case", [
    "no_answer_receipt", "answer_mismatch", "answer_after_decision", "answer_before_summary",
    "section_not_last", "prefix_edited", "blank_tag", "unknown_answer",
])
def test_assumption_section_keeps_file_ownership_without_proof(closed_sheet_scene, case):
    scene = closed_sheet_scene
    receipt, start = closed_sheet_receipt(scene), start_audit()
    text = ANSWERED_H3_QUESTIONS + ASSUMPTION_SECTION
    audit = start + receipt + assumption_answer() + decision()
    if case == "no_answer_receipt":
        audit = start + receipt + decision()
    elif case == "answer_mismatch":
        audit = start + receipt + assumption_answer(Details="B. Convert to follow-up questions") + decision()
    elif case == "answer_after_decision":
        audit = start + receipt + decision() + assumption_answer("2026-09-04T09:59:40Z")
    elif case == "answer_before_summary":
        audit = start + assumption_answer("2026-09-04T09:59:05Z") + receipt + decision()
    elif case == "section_not_last":
        text = ANSWERED_H3_QUESTIONS + ASSUMPTION_SECTION + "\n## Q9. Follow-up?\nA. Yes\n[Answer]: A. Yes\n"
    elif case == "prefix_edited":
        text = ANSWERED_H3_QUESTIONS.replace("[Answer]: A", "[Answer]: B", 1) + ASSUMPTION_SECTION
    elif case == "blank_tag":
        text = ANSWERED_H3_QUESTIONS + ASSUMPTION_SECTION.replace("[Answer]: A. Accept assumptions", "[Answer]:")
    elif case == "unknown_answer":
        text = ANSWERED_H3_QUESTIONS + ASSUMPTION_SECTION.replace("A. Accept assumptions\n", "Confirmed\n", 1)
        audit = start + receipt + assumption_answer(Details="Confirmed") + decision()
    (_record_dir(scene.root) / QUESTIONS_REL).write_text(text)
    rewrite_audit(scene, audit, start=False)
    assert_not_audit_owned(closed_sheet_questions(scene))


@pytest.fixture
def C(studio):
    return studio.constants


RULES_PROMPT = "Learnings: which judgments become rules?"


def install_same_turn_questions(scene):
    install_closed_sheet(scene)
    rewrite_audit(scene, closed_sheet_receipt(scene), decision(Decision=RULES_PROMPT, Options="c1,c2"), decision())


def batch_payload(rules, add, **other):
    return {"decision": "answers", "answers": [
        {"index": 1, "option_letters": rules, **other}, {"index": 2, "option_letters": add}]}


def grouped(*lines, C):
    return "\n".join(lines) + C.WIRE_AUDIT_GROUPED_ANSWER_SUFFIX


def test_questions_asked_in_one_turn_are_answered_together_and_resolve(sv, routes, fake_host, scene, C):
    """Two decision rows written back to back take one reply and one receipt (A37)."""
    install_same_turn_questions(scene)
    card = question_card(sv, routes, fake_host, scene)
    view = card["evidence"]["questions"]
    assert view["origin"]["kind"] == "audit" and view["origin"]["batch_size"] == 2
    assert view["pending_count"] == 2 and view["unsupported_pending_count"] == 0
    rules, add = view["questions"]
    assert (rules["prompt"], rules["multi_select"]) == (RULES_PROMPT, True)
    assert [o["text"] for o in rules["options"]] == ["c1", "c2", C.AUDIT_NONE_LABEL, "Other"]
    assert rules["options"][-1]["is_other"] is True
    assert (add["prompt"], add["multi_select"]) == (PROMPT, False)
    assert [o["text"] for o in add["options"]] == ["Nothing to add", "Add a note", "Other"]
    assert [d["decision"] for d in card["decisions"]] == ["answers"]
    # "None of these" stands alone; one engine label per single-choice question.
    for bad in (batch_payload(["A", "C"], ["A"]), batch_payload(["A"], ["A", "B"])):
        status, body = _submit(sv, routes, fake_host, card, payload=bad, wire_text="x")
        assert status == 400, body
    wire = grouped("Q1: c1, c2", "Q2: Nothing to add", C=C)
    receipt = submit_ok(sv, routes, fake_host, card, wire, batch_payload(["A", "B"], ["A"]))
    assert receipt["wire_text"] == wire
    turn_reported(sv, routes, fake_host, scene, card, receipt, wire)
    answer_on_disk(scene, Details="Q1: c1, c2\\nQ2: Nothing to add")
    row = reconcile(sv, card)
    assert row["status"] == "ResolvedNoTransition"
    assert row["resolution_json"]["reason"] == "question_answered", row["resolution_json"]["reason"]
    assert not question_cards(sv, routes, fake_host, scene)


def test_a_same_turn_other_answer_carries_the_human_words(sv, routes, fake_host, scene, C):
    install_same_turn_questions(scene)
    card = question_card(sv, routes, fake_host, scene)
    wire = grouped(f"Q1: {C.AUDIT_NONE_LABEL}", "Q2: Keep it short", C=C)
    payload = {"decision": "answers", "answers": [
        {"index": 1, "option_letters": ["C"]}, {"index": 2, "option_letters": ["C"], "free_text": "Keep it short"}]}
    status, receipt = _submit(sv, routes, fake_host, card, payload=payload, wire_text=wire)
    assert status == 200 and receipt["wire_text"] == wire, receipt


def test_ui_mirrors_the_audit_none_label(C):
    source = (Path(__file__).resolve().parents[1] / "ui/src/actions/useSubmit.ts").read_text()
    assert f"export const AUDIT_NONE_LABEL = '{C.AUDIT_NONE_LABEL}'" in source


KEEP_PROMPT, ADD_PROMPT = "Which notes should be kept for next time?", "Anything to add for next time?"


def real_ritual(stage: str) -> list[str]:
    """The ritual's two rows as v2-full logged them for ``stage``, moved to this scene's stage and second."""
    return [re.sub(r"\*\*Timestamp\*\*: \S+", f"**Timestamp**: {ASKED}", row.replace(
        f"**Stage**: {stage}\n", f"**Stage**: {STAGE}\n")) for row in F.learnings_ritual_rows(stage)]


@pytest.mark.parametrize("stage, chat_words", [
    ("requirements-analysis", "I saved your 6 answers and wrote the requirements."),
    ("build-and-test", "Build and Test is done"),
    ("intent-capture", "I saved your 5 answers."),
])
def test_the_ritual_is_asked_as_the_chat_asked_it_whatever_order_it_was_logged(closed_sheet_scene, stage, chat_words):
    """v2-full: the keep question then "Anything to add?", in every chat; the rows were logged in either order."""
    chat = F.learnings_ritual_chat(chat_words)
    assert chat.index("Which notes should I keep for next time?") < chat.index(ADD_PROMPT)
    rows = real_ritual(stage)
    logged = [re.search(r"\*\*Decision\*\*: (.*)", row).group(1) for row in rows]
    assert sorted(logged) == sorted([KEEP_PROMPT, ADD_PROMPT])
    rewrite_audit(closed_sheet_scene, closed_sheet_receipt(closed_sheet_scene), *rows)
    questions = closed_sheet_questions(closed_sheet_scene)
    assert questions.origin["kind"] == "audit" and questions.origin["batch_size"] == 2
    assert [q.prompt for q in questions.questions] == [KEEP_PROMPT, ADD_PROMPT]
    assert [q.index for q in questions.questions] == [1, 2]


def test_the_ritual_offers_exactly_the_engines_choices(closed_sheet_scene, C):
    """§13 step 3: the keep question opens with the engine's own "Keep none of these" and takes any subset; "Anything to
    add?" is one pick of "Nothing to add" / "Add a note", with the harness's free-text escape. Shot 113 offered
    checkboxes for both, and "None of these" / "Other" beside "Keep none of these"."""
    rewrite_audit(closed_sheet_scene, closed_sheet_receipt(closed_sheet_scene), *real_ritual("build-and-test"))
    keep, add = closed_sheet_questions(closed_sheet_scene).questions
    assert keep.multi_select is True
    assert [(o.letter, o.text, o.is_other) for o in keep.options] == [
        ("A", "Keep none of these", False), ("B", "c1", False)]
    assert add.multi_select is False
    assert [(o.text, o.is_other) for o in add.options] == [
        ("Nothing to add", False), ("Add a note", False), (C.AUDIT_OTHER_LABEL, True)]
    assert C.AUDIT_NONE_LABEL not in {o.text for q in (keep, add) for o in q.options}


def test_the_ritual_card_sends_its_answers_in_the_order_asked(sv, routes, fake_host, scene, C):
    """build-and-test logged "Anything to add?" first; the reply's Q1 is still the keep question the chat asked first."""
    install_closed_sheet(scene)
    rewrite_audit(scene, closed_sheet_receipt(scene), *real_ritual("build-and-test"))
    card = question_card(sv, routes, fake_host, scene)
    assert [q["prompt"] for q in card["evidence"]["questions"]["questions"]] == [KEEP_PROMPT, ADD_PROMPT]
    # "Keep none of these" may sit beside a kept note (§13); "Anything to add?" takes one choice.
    status, body = _submit(sv, routes, fake_host, card, payload=batch_payload(["A"], ["A", "B"]), wire_text="x")
    assert status == 400, body
    wire = grouped("Q1: Keep none of these, c1", "Q2: Nothing to add", C=C)
    receipt = submit_ok(sv, routes, fake_host, card, wire, batch_payload(["A", "B"], ["A"]))
    assert receipt["wire_text"] == wire


def test_a_row_between_two_decisions_leaves_only_the_newest_asked(closed_sheet_scene):
    scene = closed_sheet_scene
    rewrite_audit(scene, closed_sheet_receipt(scene),
                  decision("2026-09-04T09:59:20Z", Decision="Older question?", Options="Yes,No"),
                  F.audit_block("ARTIFACT_UPDATED", "2026-09-04T09:59:25Z", Stage=STAGE), decision())
    questions = closed_sheet_questions(scene)
    assert [q.prompt for q in questions.questions] == [PROMPT]
    assert questions.unsupported_pending_count == 0


def test_closed_sheet_same_shard_order_proves_freshness_within_one_second(closed_sheet_scene):
    rewrite_audit(closed_sheet_scene, closed_sheet_receipt(closed_sheet_scene, ASKED), decision())
    assert closed_sheet_questions(closed_sheet_scene).origin["kind"] == "audit"


@pytest.mark.parametrize("change", ["receipt_removed", "receipt_replaced", "file_edited"])
def test_closed_sheet_submit_rechecks_closure_evidence(sv, routes, fake_host, scene, change):
    path = install_closed_sheet(scene)
    card = question_card(sv, routes, fake_host, scene)
    if change == "file_edited":
        path.write_text(ANSWERED_H3_QUESTIONS.replace("[Answer]: A", "[Answer]: B", 1))
    else:  # keep the decision at the same append position while modifying its closure evidence
        text = _shard(scene.root).read_text()
        _shard(scene.root).write_text(text.replace(
            "SUMMARY_CONFIRMATION_RECORDED" if change == "receipt_removed" else CLOSED_AT,
            "NOTE" if change == "receipt_removed" else "2026-09-04T09:59:11Z"))
    status, body = _submit(sv, routes, fake_host, card, payload=answer_payload(), wire_text=LABELS[0])
    assert status == 409 and body["code"] == "action_stale", body
    assert _row(sv, card["action_id"])["status"] == "Queued"
    assert fake_host.get_slot(scene.slot_key).messages == []


@pytest.mark.parametrize("checkpoint, accepted", [
    ("Plan Approval", True), ("Code Generation Plan Approval", True), ("Unrelated Plan Approval", False),
])
def test_closed_sheet_plan_checkpoint_requires_its_own_receipt(closed_sheet_scene, checkpoint, accepted):
    scene = closed_sheet_scene
    text = "## Plan Approval\n\n- Approve Plan\n- Request changes\n\n[Answer]: Approve Plan\n"
    path = write_questions(scene, text)
    assert closed_sheet_questions(scene).origin is None
    rewrite_audit(scene, F.audit_block("PLAN_APPROVAL_RECORDED", CLOSED_AT, **{
        "Stage": STAGE, "Details": "Approve Plan", "Checkpoint": checkpoint,
        "Questions File": str(path.relative_to(scene.root)), "Questions SHA-256": F.sha256(text)}), decision())
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
    state = F.state_text(F.DEVDELTA_POC, marks={CODEGEN_STAGE: "-"},
                         fields={"Current Stage": CODEGEN_STAGE, "Status": "Running", "Scope": "bugfix"})
    receipt = F.audit_block("PLAN_APPROVAL_RECORDED", CLOSED_AT, **{
        "Stage": CODEGEN_STAGE, "Details": "Approve Plan", "Checkpoint": "Code Generation Plan Approval",
        "Questions File": f"aidlc/spaces/default/intents/{INTENT}/{CODEGEN_QUESTIONS_REL}",
        "Questions SHA-256": F.sha256(CODEGEN_CLOSED_PLAN)})
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
    assert boundary.questions is not None and boundary.questions.origin is not None
    assert boundary.questions.origin["kind"] == "audit"
    assert boundary.questions.origin["stage"] == CODEGEN_STAGE
    assert boundary.questions.origin["unit"] is None
    assert boundary.questions.questions[0].prompt == PROMPT
    assert [option.text for option in boundary.questions.questions[0].options] == LABELS[:2]
    assert boundary.questions.pending_count == 1 and boundary.questions.pending_checkpoint is None
    assert _tree_digest(scene.root) == original


@pytest.mark.parametrize("marker_fields", [
    None, {"version": 1}, {"state_sha256": "0" * 64}, {"kind": "ask"}, {"kind": "invoke-swarm", "units": ["unit-a"]},
    {"unit": None}, {"unit": ""}, {"unit": 7}, {"units": []}, {"units": "unknown"}, {"stage": "functional-design"},
], ids=["no-directive", "legacy-directive", "stale-directive", "not-executing", "swarm", "null-unit", "empty-unit",
        "malformed-unit", "empty-units", "malformed-units", "other-stage"])
def test_codegen_unknown_unit_scope_never_authorizes_audit_learnings(zero_unit_codegen_scene, marker_fields):
    scene = zero_unit_codegen_scene
    path = _record_dir(scene.root) / ".aidlc-active-directive.json"
    if marker_fields is None:
        path.unlink()
    else:
        path.write_text(json.dumps({**json.loads(path.read_text()), **marker_fields}))
    assert_not_audit_owned(closed_sheet_questions(scene))


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
        truncate_audit(monkeypatch, scene.reader)
    elif failure == "unreadable-shard":
        (_record_dir(scene.root) / "audit/unreadable.md").mkdir()
    elif failure == "unreadable-sheet":
        fail_bounded_read(monkeypatch, scene.reader, path)
    elif failure in ("bad-receipt", "missing-receipt"):
        before, after = ((F.sha256(CODEGEN_CLOSED_PLAN), "0" * 64) if failure == "bad-receipt"
                         else ("PLAN_APPROVAL_RECORDED", "NOTE"))
        _shard(scene.root).write_text(_shard(scene.root).read_text().replace(before, after))
    elif failure == "pending-plan":
        path.write_text(CODEGEN_CLOSED_PLAN.replace("[Answer]: Approve Plan", "[Answer]:"))
    elif failure in ("answered", "ambiguous-answer"):
        append(scene, F.audit_block("QUESTION_ANSWERED", ANSWERED if failure == "answered" else ASKED,
                                    Stage=CODEGEN_STAGE, Details=LABELS[0]),
               shard="zz-answer.md" if failure == "ambiguous-answer" else None)
    else:
        state_path = _record_dir(scene.root) / "aidlc-state.md"
        state = state_path.read_text()
        state = (state.replace(f"- [-] {CODEGEN_STAGE}", f"- [x] {CODEGEN_STAGE}") if failure == "finished-stage"
                 else F.insert_runtime_field(state, "Active Unit", "unit-a"))
        state_path.write_text(state)
        marker_path = _record_dir(scene.root) / ".aidlc-active-directive.json"
        marker_path.write_text(json.dumps({**json.loads(marker_path.read_text()), "state_sha256": F.sha256(state)}))
    assert_not_audit_owned(closed_sheet_questions(scene))


def test_zero_unit_codegen_learnings_reach_the_intent_detail(sv, routes, fake_host, repo_builder,
                                                             zero_unit_codegen_scene):
    scene = zero_unit_codegen_scene
    repo_builder.with_engine("payload")
    repo = sv.repos.add(str(scene.root), "zero-unit codegen")
    original = _tree_digest(scene.root)
    view = questions_view(sv, routes, fake_host, repo.repo_id)
    assert view["mode"] == "structured"
    assert view["origin"]["kind"] == "audit" and view["origin"]["stage"] == CODEGEN_STAGE
    assert view["questions"][0]["prompt"] == PROMPT
    assert _tree_digest(scene.root) == original


REAL_PLAN_OFF_SHEET = (F.LEARNINGS_RITUAL / "code-generation-questions.md").read_text("utf-8")


def install_plan_off_sheet(scene, *, receipt_edit=lambda row: row) -> Path:
    """v2-full's code generation with plan approval off: the engine's record-only sheet and its skip row, then the
    ritual, each moved to this scene's intent and seconds."""
    path = _record_dir(scene.root) / CODEGEN_QUESTIONS_REL
    path.write_text(REAL_PLAN_OFF_SHEET)
    [skip] = F.learnings_ritual_rows(CODEGEN_STAGE, event="PLAN_APPROVAL_SKIPPED")
    skip = re.sub(r"\*\*Timestamp\*\*: \S+", f"**Timestamp**: {CLOSED_AT}", skip)
    skip = skip.replace("intents/261102-add-an-optional/", f"intents/{INTENT}/")
    ritual = [re.sub(r"\*\*Timestamp\*\*: \S+", f"**Timestamp**: {ASKED}", row)
              for row in F.learnings_ritual_rows(CODEGEN_STAGE)]
    rewrite_audit(scene, start_audit().replace(STAGE, CODEGEN_STAGE), receipt_edit(skip), *ritual, start=False)
    return path


def test_a_plan_built_without_asking_hands_code_generation_to_its_later_decisions(zero_unit_codegen_scene):
    """v2-full asked the test command and the learnings in chat after a plan built with plan approval off; with the
    sheet in charge Studio raised each only as a conversation reply, unlike every other stage's learnings."""
    scene = zero_unit_codegen_scene
    install_plan_off_sheet(scene)
    assert F.sha256(REAL_PLAN_OFF_SHEET) == "269b7bed4ab335b96bee23bc0143cef97e1d10f4fdcf0af645f2644464fadd62"
    questions = closed_sheet_questions(scene)
    assert questions.origin["kind"] == "audit" and questions.origin["stage"] == CODEGEN_STAGE
    assert [q.prompt for q in questions.questions] == [KEEP_PROMPT, ADD_PROMPT]
    assert questions.closed_file.relpath.endswith(CODEGEN_QUESTIONS_REL)
    assert [e.event for e in questions.closed_file_receipts] == ["PLAN_APPROVAL_SKIPPED"]


@pytest.mark.parametrize("change", ["no_skip_row", "other_sheet", "other_bytes", "approve_details", "edited_sheet"])
def test_a_plan_built_without_asking_needs_its_own_skip_row(zero_unit_codegen_scene, change):
    scene = zero_unit_codegen_scene
    edit = {
        "no_skip_row": lambda row: row.replace("PLAN_APPROVAL_SKIPPED", "NOTE"),
        "other_sheet": lambda row: row.replace("code-generation-questions.md", "other-questions.md"),
        "other_bytes": lambda row: row.replace("269b7bed", "00000000"),
        # An approval's receipt does not close a sheet that says it was never asked.
        "approve_details": lambda row: row.replace("PLAN_APPROVAL_SKIPPED", "PLAN_APPROVAL_RECORDED").replace(
            "**Details**: Plan approval off", "**Details**: Approve Plan"),
    }.get(change, lambda row: row)
    path = install_plan_off_sheet(scene, receipt_edit=edit)
    if change == "edited_sheet":
        path.write_text(REAL_PLAN_OFF_SHEET + "\n")
    assert_not_audit_owned(closed_sheet_questions(scene))


@pytest.mark.parametrize("change", ["none", "old_receipt_generation", "claim_advanced", "claim_unreadable"])
def test_closed_sheet_unit_receipt_must_match_current_claim(sv, scene, change):
    install_closed_sheet(scene)
    generations, key = activate_unit(sv, scene)
    generation = "1" if change == "old_receipt_generation" else "2"
    rewrite_audit(scene, closed_sheet_receipt(scene, Unit="unit-a", Attempt_Generation=generation),
                  decision(Unit="unit-a", Attempt_Generation="2"))
    if change == "claim_advanced":
        generations.write_text(json.dumps({key: 3}))
    elif change == "claim_unreadable":
        generations.write_text("{broken")
    questions = snap(sv, scene).questions
    assert (questions is not None and questions.origin is not None) is (change == "none")


def parse(studio, text, sha="0" * 64):
    return studio.aidlc_reader.parse_questions_file(text, "questions.md", sha)


def test_h3_question_parser_keeps_all_three_pending_boundaries(studio):
    parsed = parse(studio, H3_QUESTIONS)
    assert parsed.pending_count == 3
    assert [question.index for question in parsed.questions] == [1, 2, 3]
    assert [question.prompt for question in parsed.questions] == [
        "When `--precision` is outside 0-6, what should the command-line behavior be?",
        "How should the requested precision be rendered?",
        "Where should the new tests live and how should they be run?",
    ]
    assert [[option.letter for option in question.options] for question in parsed.questions] == [
        ["A", "B", "C", "X"], ["A", "B", "X"], ["A", "B", "C", "X"]]
    assert all(not question.answered and question.answer is None for question in parsed.questions)
    assert all("### Q" not in question.raw.split("\n", 1)[1] for question in parsed.questions)


@pytest.mark.parametrize("heading", ["# Appendix", "## Sources", "### Explanation", "#### Rationale"])
def test_h3_question_parser_generic_headings_cannot_supply_options_or_answers(studio, heading):
    parsed = parse(studio, "## Q1. First question\n\n- A. First choice\n\n"
                           f"{heading}\n\n- B. Explanatory example, not a choice\n\n[Answer]: Not a real answer\n\n"
                           "### Q2 — Second question\n\n- C. Second choice\n\n[Answer]:\n")
    assert [question.index for question in parsed.questions] == [1, 2]
    assert parsed.pending_count == 2
    assert [[option.text for option in q.options] for q in parsed.questions] == [["First choice"], ["Second choice"]]
    assert all(question.answer is None for question in parsed.questions)


@pytest.mark.parametrize("opening,closing", [
    ("```markdown", "```"), ("~~~markdown", "~~~"), ("````markdown", "`````"), ("~~~~markdown", "~~~~~"),
    ("   ```markdown", "  ```\t"), ("  ~~~markdown", "   ~~~~  "),
])
def test_question_fences_do_not_reopen_an_answered_h2_question(studio, opening, closing):
    text = f"## Q1. Keep this output?\nA. Keep it\n\n{opening}\n# Generated report\n{closing}\n\n[Answer]: A. Keep it"
    parsed = parse(studio, text, "f" * 64)
    assert parsed.pending_count == 0 and len(parsed.questions) == 1
    assert parsed.questions[0].answer == "A. Keep it"
    assert parsed.questions[0].raw == text
    assert parsed.sha256 == "f" * 64


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_question_fences_do_not_create_example_questions_or_checkpoints(studio, fence):
    parsed = parse(studio, f"{fence}markdown\n## Q99. H2 example\nA. Example choice\n[Answer]:\n\n"
                           "### Q1 — Example prompt\nA. Example choice\n[Answer]:\n\n"
                           f"## Plan Approval\n- Approve Plan\n[Answer]:\n{fence}\n\n"
                           "### Q2 — Real question\nA. Real choice\n[Answer]:\n")
    assert [question.index for question in parsed.questions] == [2]
    assert parsed.pending_count == 1
    assert parsed.plan_approval is None and parsed.pending_checkpoint is None
    assert [option.text for option in parsed.questions[0].options] == ["Real choice"]


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_question_fences_cannot_supply_options_or_answers(studio, fence):
    parsed = parse(studio, "## Q1. Keep this output?\nA. Keep it\n\n"
                           f"{fence}markdown\nB. Example option\n[Answer]: Example answer\n{fence}\n\n[Answer]:\n")
    assert parsed.pending_count == 1
    assert parsed.questions[0].answer is None
    assert [option.text for option in parsed.questions[0].options] == ["Keep it"]


@pytest.mark.parametrize("opening,invalid_close,closing", [
    ("````", "```", "````"), ("~~~~", "~~~", "~~~~"), ("```", "~~~", "```"), ("~~~", "```", "~~~"),
    ("```", "    ```", "```"), ("~~~", "\t~~~", "~~~"), ("```", "``` trailing text", "```"),
    ("~~~", "~~~ trailing text", "~~~"),
])
def test_question_fences_stay_open_until_a_valid_close(studio, opening, invalid_close, closing):
    parsed = parse(studio, "## Q1. First question\nA. Keep it\n[Answer]: A. Keep it\n\n"
                           f"{opening}markdown\n{invalid_close}\n"
                           f"### Q99 — Still an example\nA. Example choice\n[Answer]:\n{closing}\n\n"
                           "### Q2 — Real question\nB. Real choice\n[Answer]:\n")
    assert [question.index for question in parsed.questions] == [1, 2]
    assert parsed.questions[0].answer == "A. Keep it"
    assert parsed.pending_count == 1
    assert [option.text for option in parsed.questions[1].options] == ["Real choice"]


@pytest.mark.parametrize("fence", ["```", "~~~"])
@pytest.mark.parametrize("answered", [True, False])
def test_question_fences_ignore_an_unclosed_remainder_without_changing_prior_answers(studio, fence, answered):
    answer = "[Answer]: A. Keep it\n" if answered else ""
    parsed = parse(studio, f"## Q1. Keep this output?\nA. Keep it\n{answer}\n{fence}markdown\n"
                           "# Generated report\n[Answer]: Example answer\n\n"
                           "### Q2 — Example prompt\nB. Example choice\n[Answer]:\n"
                           "## Plan Approval\n- Approve Plan\n[Answer]:\n")
    assert [question.index for question in parsed.questions] == [1]
    assert parsed.pending_count == (0 if answered else 1)
    assert parsed.questions[0].answer == ("A. Keep it" if answered else None)
    assert parsed.plan_approval is None


@pytest.mark.parametrize("not_a_fence", ["``markdown", "~~markdown", "    ```markdown", "\t~~~markdown", "```lang`name"])
def test_question_fences_do_not_start_from_invalid_openers(studio, not_a_fence):
    parsed = parse(studio, f"{not_a_fence}\n### Q1 — Real question\nA. Real choice\n[Answer]:\n")
    assert [question.index for question in parsed.questions] == [1]
    assert parsed.pending_count == 1


def test_question_fences_cannot_displace_a_pending_audit_decision(sv, scene):
    write_questions(scene, "```markdown\n## Q1. Example\nA. Example choice\n[Answer]:\n```\n")
    original = _tree_digest(scene.root)
    questions = snap(sv, scene).questions
    assert questions.origin is not None and questions.origin["kind"] == "audit"
    assert questions.questions[0].prompt == PROMPT
    assert [option.text for option in questions.questions[0].options] == LABELS
    assert _tree_digest(scene.root) == original


def unparsed_questions(scene, text=UNPARSED_QUESTIONS):
    path = write_questions(scene, text)
    rewrite_audit(scene, decision(Decision=ROUTING_PROMPT, Options=",".join(ROUTING_LABELS)))
    return path


@pytest.mark.parametrize("letter,label", zip("ABC", ROUTING_LABELS))
def test_question_notes_without_pending_tags_use_audited_routing_through_api_and_reconcile(
    sv, routes, fake_host, scene, letter, label
):
    path = unparsed_questions(scene, QUESTION_NOTES)
    original = _tree_digest(scene.root)
    view = questions_view(sv, routes, fake_host, scene.repo_id)
    assert view["host_card"] is None and view["mode"] == "structured"
    assert view["origin"]["kind"] == "audit" and view["pending_count"] == 1
    assert view["questions"][0]["prompt"] == ROUTING_PROMPT
    assert [option["text"] for option in view["questions"][0]["options"]] == ROUTING_LABELS
    card = question_card(sv, routes, fake_host, scene)
    receipt = submit_ok(sv, routes, fake_host, card, label, answer_payload(letter))
    assert receipt["lane"] == "human_lane" and receipt["wire_text"] == label
    assert _tree_digest(scene.root) == original
    assert fake_host.get_slot(scene.slot_key).messages == []
    assert _submit(sv, routes, fake_host, card, payload=answer_payload(letter), wire_text=label)[0] == 409
    turn_reported(sv, routes, fake_host, scene, card, receipt, label)
    answer_on_disk(scene, Details=label)
    assert reconcile(sv, card)["status"] == "ResolvedNoTransition"
    remaining = question_cards(sv, routes, fake_host, scene)
    assert all(c["action_id"] != card["action_id"] and not c["evidence"]["questions"].get("origin") for c in remaining)
    after = questions_view(sv, routes, fake_host, scene.repo_id)
    assert after["mode"] == "degraded" and after["pending_count"] == 0 and after["questions"] == []
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
        truncate_audit(monkeypatch, sv.reader)
    elif failure == "read_failed":
        fail_bounded_read(monkeypatch, sv.reader, path)
    elif failure == "protected":
        rewrite_audit(scene, decision(Options="[protected exact choices]"))
    elif failure == "answered":
        append(scene, F.audit_block("QUESTION_ANSWERED", ANSWERED, Stage=STAGE, Details="Guide me"))
    elif failure == "new_attempt":
        append(scene, F.audit_block("STAGE_STARTED", ANSWERED, Stage=STAGE))
    elif failure == "same_second":
        append(scene, F.audit_block("QUESTION_ANSWERED", ASKED, Stage=STAGE), shard="zz-answer.md")
    else:
        rewrite_audit(scene)
    questions = snap(sv, scene).questions
    assert questions is None or (questions.origin is None and questions.pending_count == 0)


@pytest.mark.parametrize("change", ["file_edit", "structured_replacement", "read_failed"])
def test_question_notes_audit_submit_rechecks_the_file(sv, routes, fake_host, scene, monkeypatch, change):
    path = unparsed_questions(scene, QUESTION_NOTES)
    card = question_card(sv, routes, fake_host, scene)
    if change == "file_edit":
        path.write_text(QUESTION_NOTES + "\nA changed question description.\n")
    elif change == "structured_replacement":
        path.write_text(FILE_QUESTION)
    else:
        fail_bounded_read(monkeypatch, sv.reader, path)
    status, body = _submit(sv, routes, fake_host, card, payload=answer_payload(), wire_text=ROUTING_LABELS[0])
    assert status == 409 and body["code"] == "action_stale", body
    assert _row(sv, card["action_id"])["status"] == "Queued"
    assert fake_host.get_slot(scene.slot_key).messages == []


@pytest.mark.parametrize("text,pending", [
    (UNPARSED_QUESTIONS, 1), ((F.FIXTURES / "question-fallback/domain-followups.md").read_text(), 3),
], ids=["legacy_unparsed", "domain_followups"])
def test_unsupported_pending_answers_keep_conversation_fallback_over_audit_routing(
    sv, routes, fake_host, scene, text, pending,
):
    path = unparsed_questions(scene, text)
    original = _tree_digest(scene.root)
    view = questions_view(sv, routes, fake_host, scene.repo_id)
    assert view["mode"] == "degraded" and not view.get("origin")
    assert view["unsupported_pending_count"] == view["pending_count"] == pending
    assert all(q["answered"] for q in view["questions"])
    card = question_card(sv, routes, fake_host, scene)
    assert card["evidence"]["questions"] == view
    assert card["primary"] is None and card["decisions"] == []
    status, result = _submit(sv, routes, fake_host, card, payload=answer_payload(), wire_text=ROUTING_LABELS[0])
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
    unparsed_questions(scene).write_text(UNPARSED_QUESTIONS + "\n" + structured)
    questions = snap(sv, scene).questions
    assert questions is not None and questions.origin is None
    assert questions.relpath.endswith(QUESTIONS_REL)


@pytest.mark.parametrize("options", [",".join(ROUTING_LABELS), BATCH_OPTIONS], ids=["answer_mode", "combined_batch"])
def test_h3_file_owns_the_actual_three_questions_despite_audit_options(sv, routes, fake_host, scene, options):
    unparsed_questions(scene).write_text(H3_QUESTIONS)
    rewrite_audit(scene, decision(Options=options))
    original = _tree_digest(scene.root)
    view = questions_view(sv, routes, fake_host, scene.repo_id)
    assert not view.get("origin") and view["pending_count"] == 3
    assert [q["index"] for q in view["questions"]] == [1, 2, 3]
    assert [len(q["options"]) for q in view["questions"]] == [4, 3, 4]
    assert view["sha256"] == F.sha256(H3_QUESTIONS)
    card = question_card(sv, routes, fake_host, scene)
    assert card["captured"]["question_digest"] == F.sha256(H3_QUESTIONS)
    assert len(card["evidence"]["questions"]["questions"]) == 3
    assert _tree_digest(scene.root) == original


@pytest.mark.parametrize("failure", ["partial", "unreadable_shard", "unreadable_directory"])
def test_incomplete_audit_is_never_an_actionable_question(sv, scene, monkeypatch, failure):
    if failure == "partial":  # the decision/attempt evidence is complete; another shard may hide its answer
        append(scene, F.audit_block("NOTE", ASKED, Details="x" * 2000), shard="zz-large.md")
        truncate_audit(monkeypatch, sv.reader)
    elif failure == "unreadable_shard":
        path = append(scene, F.audit_block("QUESTION_ANSWERED", ANSWERED, Stage=STAGE), shard="zz-answer.md")
        fail_bounded_read(monkeypatch, sv.reader, path)
    else:
        read = Path.iterdir

        def fail(path):
            if path == _record_dir(scene.root) / "audit":
                raise PermissionError("audit is unreadable")
            return read(path)

        monkeypatch.setattr(Path, "iterdir", fail)
    assert snap(sv, scene).questions is None


@pytest.mark.parametrize("change", ["answered", "reopened", "attempt_reset"])
def test_stale_audit_card_disappears_and_cannot_be_submitted(sv, routes, fake_host, scene, change):
    card = question_card(sv, routes, fake_host, scene)
    if change == "reopened":
        append(scene, decision("2026-09-04T09:59:40Z"))
    else:
        append(scene, F.audit_block("QUESTION_ANSWERED" if change == "answered" else "STAGE_STARTED",
                                    "2026-09-04T09:59:40Z", Stage=STAGE))
    status, body = _submit(sv, routes, fake_host, card, payload=answer_payload(), wire_text=LABELS[0])
    assert status == 409 and body["code"] == "action_stale", body
    cards = question_cards(sv, routes, fake_host, scene)
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
    assert asyncio.run(sv.scheduler.get(sv.repos.get(scene.repo_id).resolved_identity)) is None


@pytest.mark.parametrize("fields", [
    {"Unit": "another-unit"}, {"Attempt Generation": "2"}, {"Workflow": f"single-stage:{STAGE}"},
    {"Details": "An answer the user never sent"},
])
def test_reconcile_cannot_consume_a_foreign_or_different_answer(sv, routes, fake_host, scene, fields):
    card = question_card(sv, routes, fake_host, scene)
    turn_reported(sv, routes, fake_host, scene, card, submit_ok(sv, routes, fake_host, card))
    answer_on_disk(scene, **fields)
    row = reconcile(sv, card)
    if "Details" in fields:  # this question's own receipt with other text: the turn is over, the reply unverified
        assert row["status"] == "ResolvedNoTransition"
        assert row["resolution_json"]["reason"] == "answer_not_verified"
        assert row["resolution_json"]["evidence"]["answer_verified"] is False
    else:
        assert row["status"] == "Delivered"


def test_host_options_cannot_replace_disk_labels_or_enable_protected_choices(sv, fake_host, scene):
    fake_host.get_slot(scene.slot_key)._question_pending["host-q"] = {"question": "Host override",
                                                                      "options": ["Wrong choice"]}
    questions = snap(sv, scene).questions
    assert questions is not None
    assert [o.text for o in questions.questions[0].options] == LABELS
    rewrite_audit(scene, decision(Options="[protected exact choices]"))
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
        json.dumps({"version": 1, "stage": STAGE, "state_sha256": F.sha256(state)}))
    rewrite_audit(scene, decision(Unit="unit-a", Attempt_Generation=generation))
    uuid = sv.reader.registry(scene.root, "default")[0].uuid
    generations = scene.root / "aidlc" / ".aidlc-claim-generations.json"
    generations.write_text(json.dumps({f"default/{uuid}/unit-a": int(generation)}))
    return generations, f"default/{uuid}/unit-a"


def test_unit_question_uses_active_unit_and_current_claim_generation(sv, scene):
    generations, key = activate_unit(sv, scene)
    questions = snap(sv, scene).questions
    assert questions is not None
    assert questions.origin["unit"] == "unit-a" and questions.origin["attempt_generation"] == "2"
    append(scene, F.audit_block("QUESTION_ANSWERED", ANSWERED, Stage=STAGE, Unit="unit-a", Attempt_Generation="1",
                                Details=LABELS[0]))
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


def test_a_unit_claim_change_alone_invalidates_reconciliation(sv, routes, fake_host, scene):
    generations, key = activate_unit(sv, scene)
    card = question_card(sv, routes, fake_host, scene)
    turn_reported(sv, routes, fake_host, scene, card, submit_ok(sv, routes, fake_host, card))
    generations.write_text(json.dumps({key: 3}))
    answer_on_disk(scene, Unit="unit-a", Attempt_Generation="2")
    assert reconcile(sv, card)["status"] == "Delivered"


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
    write_questions(scene, FILE_QUESTION)
    (scene.root / "aidlc" / ".aidlc-unit-scope.json").symlink_to(scene.root.parent / "outside.json")
    questions = snap(sv, scene).questions
    assert questions is not None
    assert questions.questions[0].prompt == "File-owned question" and questions.origin is None


@pytest.mark.parametrize("case", ["different_shard", "different_prompt", "new_attempt", "partial"])
def test_answer_must_follow_the_captured_question_unambiguously(sv, routes, fake_host, scene, monkeypatch, case):
    # The prompt and delivery share a second, which is normal for fast human responses.
    rewrite_audit(scene, decision("2026-09-04T10:00:00Z"))
    card = question_card(sv, routes, fake_host, scene)
    turn_reported(sv, routes, fake_host, scene, card, submit_ok(sv, routes, fake_host, card))
    if case == "different_prompt":
        append(scene, decision("2026-09-04T10:00:01Z", Decision="A different question"))
    elif case == "new_attempt":
        append(scene, F.audit_block("STAGE_STARTED", "2026-09-04T10:00:01Z", Stage=STAGE))
    if case == "different_shard":
        append(scene, F.audit_block("QUESTION_ANSWERED", "2026-09-04T10:00:00Z", Stage=STAGE, Details=LABELS[0]),
               shard="zz-answer.md")
        append(scene, F.audit_block("HUMAN_TURN", ANSWERED))
        (_record_dir(scene.root) / ".aidlc-human-turn").write_text(ANSWERED)
    else:
        answer_on_disk(scene)
    if case == "partial":
        append(scene, F.audit_block("NOTE", ASKED, Details="x" * 2000), shard="zz-large.md")
        truncate_audit(monkeypatch, sv.reader)
    assert reconcile(sv, card)["status"] == "Delivered"


def test_an_answer_before_the_next_question_still_settles_the_original(sv, routes, fake_host, scene):
    card = question_card(sv, routes, fake_host, scene)
    turn_reported(sv, routes, fake_host, scene, card, submit_ok(sv, routes, fake_host, card))
    answer_on_disk(scene)
    append(scene, decision("2026-09-04T10:00:21Z", Decision="Next question"))
    assert reconcile(sv, card)["status"] == "ResolvedNoTransition"
    assert snap(sv, scene).questions.questions[0].prompt == "Next question"


@pytest.mark.parametrize("mark,pending", [("-", True), ("r", True), ("x", False), ("s", False)])
def test_finished_stage_cannot_reopen_an_audit_question(sv, scene, mark, pending):
    record = _record_dir(scene.root)
    state = (record / "aidlc-state.md").read_text().replace(f"- [-] {STAGE}", f"- [{mark}] {STAGE}")
    (record / "aidlc-state.md").write_text(state)
    (record / ".aidlc-active-directive.json").write_text(
        json.dumps({"version": 1, "stage": STAGE, "state_sha256": F.sha256(state)}))
    assert (snap(sv, scene).questions is not None) is pending


MEMORY = """<!-- INVARIANT: examples are single-line HTML comments -->
> kept up to date automatically

## Interpretations
<!-- example: 2026-05-29T10:14:32Z — chose REST over GraphQL -->
- 2026-09-04T09:30:00Z — Read "overdue" as strictly before today; the brief never said

## Deviations
- 2026-09-04T09:31:00Z — Skipped the coverage run; pytest is not installed here

## Open questions
- 2026-09-04T09:32:00Z — Confirm the retention window
"""


def _with_memory(scene):
    memory = _record_dir(scene.root) / "inception" / STAGE / "memory.md"
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text(MEMORY)
    return memory


def test_learnings_candidate_ids_are_explained_from_the_stage_diary(sv, routes, fake_host, scene, studio):
    """The ritual often asks "keep c1/c2?" and explains the ids only in chat; the card reads the diary."""
    _with_memory(scene)
    append(scene, decision(
        ts="2026-09-04T09:59:40Z", Decision="Learnings ritual: keep candidates c1/c2? Anything to add?",
        Options="c1,c2,Nothing to add,Add a note",
    ))
    card = question_card(sv, routes, fake_host, scene)
    # Asked back to back with the fixture's own "anything to add?", so the two arrive as one group.
    questions = card["evidence"]["questions"]["questions"]
    question = next(q for q in questions if q["prompt"].startswith("Learnings ritual"))
    by_text = {o["text"]: o for o in question["options"]}
    assert by_text["c1"]["detail"].startswith('Read "overdue" as strictly before today')
    assert by_text["c2"]["detail"].startswith("Skipped the coverage run")
    assert "detail" not in by_text["Nothing to add"]
    assert [c["id"] for c in question["learning_candidates"]] == ["c1", "c2"]
    assert question["learnings_memory"].endswith(f"inception/{STAGE}/memory.md")
    # The wire text is still the engine's own label.
    assert [o["text"] for o in question["options"]][:4] == ["c1", "c2", "Nothing to add", "Add a note"]


def test_the_anything_to_add_question_lists_the_candidates_it_is_about(sv, routes, fake_host, scene):
    _with_memory(scene)
    card = question_card(sv, routes, fake_host, scene)
    question = card["evidence"]["questions"]["questions"][0]
    assert question["prompt"] == PROMPT
    assert [c["id"] for c in question["learning_candidates"]] == ["c1", "c2"]
    assert all("detail" not in o for o in question["options"])


def test_the_keep_question_shows_its_notes_once_when_the_notes_are_the_options(sv, routes, fake_host, scene):
    """2.11 asks "Which notes should I keep for next time?" with the notes themselves as options, beside "Anything
    to add for next time?". Neither starts with "Learnings", so the card showed AI-DLC's notes to itself with no
    word on what keeping one does (local-s3, 2026-11-03)."""
    _with_memory(scene)
    note = 'Read "overdue" as strictly before today'  # the entry's summary, as surface gives it
    rewrite_audit(scene, decision(Decision="Anything to add for next time?", Options="Nothing to add,Add a note"),
                  decision(ts="2026-09-04T09:59:31Z", Decision="Which notes should I keep for next time?",
                           Options=f"Keep none of these,{note}"))
    card = question_card(sv, routes, fake_host, scene)
    keep, add = card["evidence"]["questions"]["questions"]
    assert keep["prompt"] == "Which notes should I keep for next time?" and keep["multi_select"] is True
    assert [c["id"] for c in keep["learning_candidates"]] == ["c1"]
    assert keep["learnings_memory"].endswith(f"inception/{STAGE}/memory.md")
    assert [o["text"] for o in keep["options"]][:2] == ["Keep none of these", note]
    # Asked beside it, "anything to add" does not list the same notes again.
    assert add["prompt"] == "Anything to add for next time?" and "learning_candidates" not in add


def test_a_question_without_a_diary_is_left_alone(sv, routes, fake_host, scene):
    card = question_card(sv, routes, fake_host, scene)
    question = card["evidence"]["questions"]["questions"][0]
    assert "learning_candidates" not in question


def test_candidate_numbering_matches_the_engines_surface(studio):
    """Open questions are parked, never numbered; fenced, quoted and comment lines are not entries."""
    parsed = studio.aidlc_reader.parse_learning_candidates(MEMORY + "\n## Tradeoffs\n```\n- not an entry\n```\n- 2026-09-04T09:33:00Z — Picked JSON over SQLite; small data\n")
    assert [(c.id, c.heading) for c in parsed] == [("c1", "Interpretations"), ("c2", "Deviations"), ("c3", "Tradeoffs")]
    assert parsed[2].summary == "Picked JSON over SQLite" and parsed[2].context == "small data"


PLAIN_SHEET = H3_QUESTIONS.replace("[Answer]:", "[Answer]: A")
SHEET_ANSWERED_AT = "2026-09-04T09:59:10Z"


def install_plain_sheet(scene, *audit_tail):
    path = _record_dir(scene.root) / QUESTIONS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PLAIN_SHEET)
    _shard(scene.root).write_text(start_audit() + "".join(audit_tail))
    return path


def sheet_answer(ts=SHEET_ANSWERED_AT, **fields):
    return F.audit_block("QUESTION_ANSWERED", ts, **{"Stage": STAGE, "Details": "Q1: A | Q2: A | Q3: A", **fields})


def test_an_answered_plain_sheet_hands_the_learnings_ritual_to_the_queue(closed_sheet_scene):
    """Practices/requirements sheets close with a plain QUESTION_ANSWERED; the ritual follows as audit."""
    scene = closed_sheet_scene
    install_plain_sheet(scene, sheet_answer(), decision())
    questions = closed_sheet_questions(scene)
    assert questions.origin["kind"] == "audit"
    assert questions.questions[0].prompt == PROMPT
    assert questions.closed_file is not None and questions.closed_file.relpath.endswith(QUESTIONS_REL)


@pytest.mark.parametrize("tail", ["no_receipt", "receipt_after_decision", "other_stage", "old_attempt", "empty"])
def test_a_plain_sheet_keeps_ownership_without_its_answer_receipt(closed_sheet_scene, tail):
    scene = closed_sheet_scene
    blocks = {
        "no_receipt": (decision(),),
        # An answer written after the decision is that decision's answer, not the sheet's closure.
        "receipt_after_decision": (decision(), sheet_answer(ANSWERED)),
        "other_stage": (sheet_answer(Stage="user-stories"), decision()),
        "old_attempt": (sheet_answer(**{"Attempt Generation": "2"}), decision()),
        "empty": (sheet_answer(Details=" "), decision()),
    }[tail]
    install_plain_sheet(scene, *blocks)
    questions = closed_sheet_questions(scene)
    assert questions is None or questions.origin is None


MODE_ASKED, MODE_ANSWERED, BATCH_ASKED = "2026-09-04T09:30:00Z", "2026-09-04T09:58:00Z", "2026-09-04T09:58:30Z"


def mode_question(ts=MODE_ASKED, **fields):
    return F.audit_block("DECISION_RECORDED", ts, **{
        "Stage": STAGE, "Decision": "How would you like to answer the questions?", "Options": ",".join(ROUTING_LABELS),
        **fields})


@pytest.mark.parametrize("asked", [
    {}, {"Decision": ROUTING_PROMPT},
    # The engine's own test: the three ways decide whatever the wording, as the agent copied them.
    {"Decision": "I've created 3 questions. Which way suits you?", "Options": "Chat, I’ll edit the file,guide me"},
], ids=["protocol_wording", "agent_wording", "three_ways"])
def test_the_mode_answer_alone_leaves_an_answered_sheet_its_round(closed_sheet_scene, asked):
    """The real runs' shape: the reply carrying the sheet's answers was recorded as the mode ("Chat"), and a decision
    logged after it is the sheet's questions again. The sheet's answers close it; no card."""
    install_plain_sheet(closed_sheet_scene, mode_question(**asked), sheet_answer(MODE_ANSWERED, Details="Chat"),
                        decision(Decision="Q1-Q3 requirements questions", Options="Q1,Q2,Q3"))
    questions = closed_sheet_questions(closed_sheet_scene)
    assert questions is not None and questions.origin is None and questions.pending_count == 0


@pytest.mark.parametrize("tail", ["answers_after_the_mode", "answer_to_a_batch", "checkpoint_wording"])
def test_the_sheets_own_answer_after_the_mode_answer_still_hands_on_the_ritual(closed_sheet_scene, tail):
    blocks = {
        # A second answer from the same reply, nothing asked in between: the engine records it unpaired.
        "answers_after_the_mode": (mode_question(), sheet_answer(MODE_ANSWERED, Details="Chat"), sheet_answer()),
        # The batch decision answered at last (the practices run at 10:16:27), then the ritual.
        "answer_to_a_batch": (mode_question(), sheet_answer(MODE_ANSWERED, Details="Chat"),
                              decision(BATCH_ASKED, Decision="Q1-Q3 requirements questions", Options="Q1,Q2,Q3"),
                              sheet_answer()),
        # A checkpoint is never the mode question, whatever it says.
        "checkpoint_wording": (mode_question(Checkpoint="Consolidated Summary Confirmation"), sheet_answer()),
    }[tail]
    install_plain_sheet(closed_sheet_scene, *blocks, decision())
    questions = closed_sheet_questions(closed_sheet_scene)
    assert questions.origin["kind"] == "audit" and questions.questions[0].prompt == PROMPT


def test_an_answer_whose_question_cannot_be_ordered_closes_nothing(closed_sheet_scene):
    """The mode question in another shard in the answer's own second: what the answer closed is not provable."""
    install_plain_sheet(closed_sheet_scene, sheet_answer(), decision())
    append(closed_sheet_scene, mode_question(SHEET_ANSWERED_AT), shard="zz-mode.md")
    assert_not_audit_owned(closed_sheet_questions(closed_sheet_scene))


def test_a_checkpoint_receipt_closes_its_sheet_after_a_mode_answer_too(closed_sheet_scene):
    """Protected checkpoints keep their own proof: the summary receipt binds the sheet, the mode answer is beside it."""
    rewrite_audit(closed_sheet_scene, mode_question(), sheet_answer("2026-09-04T09:40:00Z", Details="Chat"),
                  closed_sheet_receipt(closed_sheet_scene), decision())
    questions = closed_sheet_questions(closed_sheet_scene)
    assert questions.origin["kind"] == "audit" and questions.questions[0].prompt == PROMPT


def test_the_mode_question_and_closing_rows_mirror_the_bundled_engine(C, studio):
    lib = (Path(__file__).resolve().parents[1] / "payload/aidlc-kiro/.kiro/tools/aidlc-lib.ts").read_text("utf-8")
    assert f'export const ANSWER_MODE_QUESTION_PREFIX = "{C.ANSWER_MODE_QUESTION_PREFIX}";' in lib
    start = lib.index("export const ANSWER_MODE_LABELS")
    assert re.findall(r'"([^"]+)"', lib[start:lib.index("};", start)]) == list(C.ANSWER_MODE_LABELS)
    start = lib.index("export const DECISION_CLOSING_EVENTS")
    engine = set(re.findall(r'"([A-Z_]+)"', lib[start:lib.index("]);", start)]))
    assert engine and studio.aidlc_reader._DECISION_CLOSING_EVENTS == engine


def test_an_answered_plain_sheet_with_a_newer_ritual_reaches_the_api(sv, routes, fake_host, scene):
    install_plain_sheet(scene, sheet_answer(), decision())
    card = question_card(sv, routes, fake_host, scene)
    assert card["evidence"]["questions"]["origin"]["kind"] == "audit"
    assert card["evidence"]["questions"]["questions"][0]["prompt"] == PROMPT


def test_an_answer_the_engine_refused_settles_instead_of_stranding_the_queue(sv, routes, fake_host, scene):
    """A protected checkpoint that did not see the exact choice logs ERROR_LOGGED and records nothing.

    The delivery is settled (no answer claimed), so the conductor's re-ask is not stuck behind a
    delivery-uncertain block that could only clear when the refused answer appeared.
    """
    card = question_card(sv, routes, fake_host, scene)
    label = LABELS[0]
    status, receipt = _submit(sv, routes, fake_host, card, payload=answer_payload("A"), wire_text=label)
    assert status == 200, receipt
    _finish_the_turn(fake_host, scene.slot_key, wire_text=label)
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    append(
        scene,
        F.audit_block("HUMAN_TURN", ANSWERED),
        F.audit_block(
            "ERROR_LOGGED", ANSWERED, Tool="aidlc-log",
            Command=f"aidlc-log answer --stage {STAGE} --checkpoint verification-command --details {label}",
            Error="verification-command requires the actual offered choice",
        ),
    )
    stamp_human_turn(_record_dir(scene.root), ANSWERED)
    for _ in range(3):
        asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    row = _row(sv, card["action_id"])
    assert row["status"] == "ResolvedNoTransition"
    assert row["resolution_json"]["reason"] == "answer_refused"
    assert row["resolution_json"]["evidence"]["answer_verified"] is False


def test_a_turn_that_ends_asking_something_else_settles_the_unrecorded_answer(sv, routes, fake_host, scene):
    """The answer went out, a host tool prompt was declined, and the conductor now asks in chat."""
    card = question_card(sv, routes, fake_host, scene)
    label = LABELS[0]
    status, receipt = _submit(sv, routes, fake_host, card, payload=answer_payload("A"), wire_text=label)
    assert status == 200, receipt
    _finish_the_turn(fake_host, scene.slot_key, wire_text=label)
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    append(scene, F.audit_block("HUMAN_TURN", ANSWERED))
    stamp_human_turn(_record_dir(scene.root), ANSWERED)
    fake_host.set_running(scene.slot_key, True)
    asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    assert _row(sv, card["action_id"])["status"] == "Delivered", "the turn still runs: keep waiting"

    fake_host.set_running(scene.slot_key, False)
    options = ["Go on and save the command", "Choose the command later"]
    fake_host.get_slot(scene.slot_key).options = options
    for _ in range(3):
        asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    row = _row(sv, card["action_id"])
    assert row["status"] == "ResolvedNoTransition"
    assert row["resolution_json"]["reason"] == "answer_not_recorded"
    # The decision the answer went to is not offered again: the conversation's question is.
    _rescan(sv, routes, fake_host, scene)
    [reply] = _queue(sv, routes, fake_host)
    assert (reply["type"], reply["headline"]["params"]["reason"]) == ("missing_input", "conversation_reply")
    assert reply["headline"]["params"]["options"] == options


def test_summary_hash_scopes_mirror_the_bundled_engine(C):
    """The 2.11 engine writes confirmed-content-v2. Missing it, every stage with a summary confirmation kept its
    answered sheet in charge, and the learnings ritual asked after it reached the queue only as a conversation reply
    built from the chat's quick replies (local-s3, 2026-11-03)."""
    lib = (Path(__file__).resolve().parent.parent / "payload" / "aidlc-kiro" / ".kiro" / "tools" / "aidlc-lib.ts").read_text("utf-8")
    current = re.search(r'SUMMARY_CONFIRMATION_HASH_SCOPE = "([^"]+)"', lib).group(1)
    legacy = re.findall(r'"([^"]+)"', re.search(r"LEGACY_SUMMARY_CONFIRMATION_HASH_SCOPES[^=]*= \[([^\]]*)\]", lib).group(1))
    assert set(C.SUMMARY_CONFIRMATION_HASH_SCOPES) == {current, *legacy}
