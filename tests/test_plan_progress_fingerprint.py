"""Real Bun contracts for plan progress, without hooks or approval-writing APIs.

The Python raw-hash oracle pins the existing envelope independently of the engine's
normalizer. All state, directives, artifacts, audit rows and synthetic receipt JSON
are confined to pytest's temporary directory. Reads must preserve every fixture byte.
Tests without a receipt still require the separate protected-receipt check to fail.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from fixtures import audit_block, audit_text


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "payload/aidlc-kiro/.kiro/tools"
AUTHORITY = {
    "targetId": "stage:code-generation",
    "intentId": "fixture-intent",
    "directiveEpoch": "sha256:" + "d" * 64,
    "runFloor": "fixture-run-1",
    "sourceFloor": "a" * 64,
}
CONTRACT = "sha256:" + "c" * 64
INSTRUCTIONS = "# Unit tests\n\nRun the precision and rounding assertions.\n"
PLAN = "# Code Generation Plan\n\n- [ ] Add precision support.\n- [ ] Test rounding.\n"


def raw_fingerprint(plan, instructions=INSTRUCTIONS, contract=CONTRACT, authority=None):
    """The pre-change fingerprint format, including every original byte of plan text."""
    authority = AUTHORITY if authority is None else authority
    envelope = {
        "plan": plan,
        "instructions": instructions,
        "testing_contract": contract,
        "target": authority["targetId"],
        "intent": authority["intentId"],
        "directive_epoch": authority["directiveEpoch"],
        "run_floor": authority["runFloor"],
        "source_floor": authority["sourceFloor"],
    }
    encoded = json.dumps(envelope, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@pytest.fixture
def engine(tmp_path):
    bun = shutil.which("bun")
    if bun is None:
        pytest.skip("real Bun is required for the engine fingerprint contract")
    source = f"""
      import {{
        approvalFingerprint, evaluateCodeGenerationApproval, resolveCodeGenerationAuthority,
        resolveTestingPosture, renderTestingContract, codeGenerationPlanApprovalQuestionEvidence,
      }} from {json.dumps(str(TOOLS / "aidlc-testing-posture.ts"))};
      import {{
        visibleMarkdownLines, docsRoot, sessionsDir, findStageBySlug, existingReviewAppendixOffset,
      }} from {json.dumps(str(TOOLS / "aidlc-lib.ts"))};
      const input = JSON.parse(await Bun.stdin.text());
      let result;
      if (input.op === "context") {{
        const contract = resolveTestingPosture(input.project);
        result = {{
          contract, renderedContract: renderTestingContract(contract),
          authority: resolveCodeGenerationAuthority(input.project, {{ unit: null }}),
          record: docsRoot(input.project),
          sessions: sessionsDir(input.project),
          reviewer: findStageBySlug("code-generation")?.reviewer,
        }};
      }} else if (input.op === "evaluate") {{
        result = evaluateCodeGenerationApproval(input.project, {{ unit: null }});
      }} else if (input.op === "review-offset") {{
        result = existingReviewAppendixOffset(Buffer.from(input.plan, "utf-8"));
      }} else if (input.op === "question-evidence") {{
        try {{
          result = {{ ok: true, evidence: codeGenerationPlanApprovalQuestionEvidence(
            input.project, {{ unit: null }}, input.questions, input.answer,
          ) }};
        }} catch (error) {{
          result = {{ ok: false, reason: String(error) }};
        }}
      }} else {{
        result = input.cases.map((item) => ({{
          fingerprint: approvalFingerprint(item.plan, item.instructions, item.contract, item.authority),
          visible: visibleMarkdownLines(item.plan),
        }}));
      }}
      console.log(JSON.stringify(result));
    """

    def run(*plans, instructions=INSTRUCTIONS, contract=CONTRACT, authority=None, op=None):
        request = op or {
            "cases": [
                {
                    "plan": plan, "instructions": instructions, "contract": contract,
                    "authority": AUTHORITY if authority is None else authority,
                }
                for plan in plans
            ],
        }
        completed = subprocess.run(
            [bun, "-e", source], input=json.dumps(request), text=True, capture_output=True,
            cwd=tmp_path, timeout=20, check=False,
        )
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)

    return run


VISIBLE_TASKS = [
    pytest.param("- [{mark}] Add precision.\n", id="dash"),
    pytest.param("* [{mark}] Add precision.\n", id="asterisk"),
    pytest.param("+ [{mark}] Add precision.\n", id="plus"),
    pytest.param("1. [{mark}] Add precision.\n", id="ordered-dot"),
    pytest.param("2) [{mark}] Add precision.\n", id="ordered-parenthesis"),
    pytest.param("   - [{mark}] Add precision.\n", id="three-space-indent"),
    pytest.param("- Parent\n  - [{mark}] Add precision.\n", id="nested-list"),
    pytest.param("- Parent\n  - Child\n    - [{mark}] Add precision.\n", id="deep-nested-list"),
    pytest.param("- [{mark}] Run `precision --digits 2`.\n", id="task-with-inline-code"),
    pytest.param("- [{mark}] Add precision. <!-- implementation note -->\n", id="task-with-comment"),
    pytest.param("\ufeff# Plan\r\n\r\n- [{mark}] Add precision.\r\n", id="bom-crlf"),
    pytest.param("# Plan\r\r- [{mark}] Add precision.\r", id="cr-only"),
    pytest.param("# Plan\n\n- [{mark}] Add precision.", id="no-final-newline"),
]


@pytest.mark.parametrize("template", VISIBLE_TASKS)
def test_visible_task_progress_changes_only_the_status_character(engine, template):
    blank, lower, upper = (template.format(mark=mark) for mark in (" ", "x", "X"))
    results = engine(blank, lower, upper)
    # Independently authored blank text is the expected canonical input, not a second normalizer.
    expected = raw_fingerprint(blank)
    assert [result["fingerprint"] for result in results] == [expected] * 3


@pytest.mark.parametrize("mark", ["x", "X"])
def test_eight_completed_steps_keep_the_original_fingerprint(engine, mark):
    blank = "# Plan\n\n" + "".join(f"- [ ] Step {number}: verify precision.\n" for number in range(1, 9))
    progress = blank.replace("[ ]", f"[{mark}]")
    before, after = engine(blank, progress)
    assert after["fingerprint"] == before["fingerprint"] == raw_fingerprint(blank)


EXCLUDED_TASKS = [
    pytest.param("```md\n- [{mark}] Example.\n```\n", id="backtick-fence"),
    pytest.param("~~~md\n- [{mark}] Example.\n~~~\n", id="tilde-fence"),
    pytest.param("````md\n```\n- [{mark}] Example.\n````\n", id="long-backtick-short-inner"),
    pytest.param("~~~~md\n~~~\n- [{mark}] Example.\n~~~~\n", id="long-tilde-short-inner"),
    pytest.param("```md\n~~~\n- [{mark}] Example.\n```\n", id="mismatched-fence"),
    pytest.param("`````md\n- [{mark}] Example.\n```\n", id="unclosed-long-fence"),
    pytest.param("    - [{mark}] Example.\n", id="indented-code"),
    pytest.param("\t- [{mark}] Example.\n", id="tab-indented-code"),
    pytest.param("<!--\n- [{mark}] Example.\n-->\n", id="comment-block"),
    pytest.param("<!-- - [{mark}] Example. -->\n", id="comment-inline"),
    pytest.param("<!-- hidden prefix -->- [{mark}] Example.\n", id="comment-before-prefix"),
    pytest.param("- <!-- hidden prefix -->[{mark}] Example.\n", id="comment-in-prefix"),
    pytest.param("<pre>\n- [{mark}] Example.\n</pre>\n", id="raw-pre"),
    pytest.param("<script>\n- [{mark}] Example.\n</script>\n", id="raw-script"),
    pytest.param("<style>\n- [{mark}] Example.\n</style>\n", id="raw-style"),
    pytest.param("<textarea>\n- [{mark}] Example.\n</textarea>\n", id="raw-textarea"),
    pytest.param("<div>\n- [{mark}] Example.\n</div>\n\n", id="raw-div"),
    pytest.param('<div title="\n- [{mark}] Example.\n">\n</div>\n', id="html-attribute"),
    pytest.param("> - [{mark}] Example.\n", id="blockquote"),
    pytest.param("> > - [{mark}] Example.\n", id="nested-blockquote"),
    pytest.param("- > - [{mark}] Example.\n", id="quoted-list-example"),
    pytest.param("`- [{mark}] Example.`\n", id="inline-code"),
    pytest.param('" - [{mark}] Example."\n', id="quoted-prose"),
    pytest.param("\\- [{mark}] Example.\n", id="escaped-list-marker"),
    pytest.param("Use - [{mark}] as an example.\n", id="prose-checkbox"),
    pytest.param("- [{mark}]Example without separating whitespace.\n", id="not-a-task-item"),
]


@pytest.mark.parametrize("template", EXCLUDED_TASKS)
def test_example_or_invisible_checkbox_changes_remain_bound(engine, template):
    blank, complete = (template.format(mark=mark) for mark in (" ", "x"))
    results = engine(blank, complete)
    assert [result["fingerprint"] for result in results] == [
        raw_fingerprint(blank), raw_fingerprint(complete),
    ]
    assert results[0]["fingerprint"] != results[1]["fingerprint"]


def test_hidden_checkbox_before_same_line_comment_close_remains_bound(engine):
    blank = "<!--\n- [ ] hidden -->- [x] visible"
    hidden_changed = blank.replace("[ ]", "[x]", 1)
    before, after = engine(blank, hidden_changed)
    assert after["fingerprint"] != before["fingerprint"]


def test_visibility_is_checked_at_the_same_line_not_elsewhere_in_the_plan(engine):
    blank = "# Plan\n\n- [ ] Same text.\n\n```md\n- [ ] Same text.\n```\n"
    example_changed = blank.replace("```md\n- [ ]", "```md\n- [x]")
    progress_changed = blank.replace("- [ ] Same text.\n\n```", "- [X] Same text.\n\n```")
    before, example, progress = engine(blank, example_changed, progress_changed)
    assert example["fingerprint"] != before["fingerprint"]
    assert progress["fingerprint"] == before["fingerprint"]


@pytest.mark.parametrize("changed", [
    PLAN.replace("precision support", "unchecked output"),
    PLAN + "- [ ] Remove validation.\n",
    PLAN.replace("- [ ] Test rounding.\n", ""),
    "# Code Generation Plan\n\n- [ ] Test rounding.\n- [ ] Add precision support.\n",
    PLAN.replace("- [ ] Add", "+ [ ] Add"),
    PLAN.replace("- [ ] Add", " - [ ] Add"),
    PLAN.replace("support.\n", "support.  \n"),
    PLAN.replace("[ ]", "[]"),
    PLAN.replace("[ ]", "[?]"),
    PLAN.replace("[ ]", "[✓]"),
    PLAN + "\nAdditional required validation.\n",
], ids=[
    "substantive-text", "add-task", "remove-task", "reorder-tasks", "list-marker",
    "indentation", "trailing-spaces", "empty-brackets", "question-status", "checkmark-status", "extra-prose",
])
def test_nonprogress_plan_changes_still_change_fingerprint(engine, changed):
    before, after = engine(PLAN, changed)
    assert after["fingerprint"] != before["fingerprint"]


REVIEW = "\n## Review\n\nVerdict: PASS\nPrecision behavior was verified.\n"


@pytest.mark.parametrize(("before_plan", "after_plan"), [
    (PLAN, PLAN + REVIEW),
    (PLAN + REVIEW, PLAN + REVIEW.replace("PASS", "CHANGES_REQUESTED")),
    (PLAN + REVIEW, PLAN + REVIEW.replace("was verified", "needs additional validation")),
    (PLAN + REVIEW, PLAN),
], ids=["add-review", "edit-verdict", "edit-review-text", "remove-review"])
def test_public_fingerprint_still_binds_review_sections(engine, before_plan, after_plan):
    # Contextual post-execution reviewer handling must not weaken this public pure hash.
    before, after = engine(before_plan, after_plan)
    assert before["fingerprint"] == raw_fingerprint(before_plan)
    assert after["fingerprint"] == raw_fingerprint(after_plan)
    assert after["fingerprint"] != before["fingerprint"]


def test_public_fingerprint_binds_review_checkboxes_and_resumes_progress_after_review(engine):
    plans = [PLAN + f"\n## Review\n\n- [{mark}] Resolve the precision finding.\n" for mark in (" ", "x", "X")]
    results = engine(*plans)
    assert [result["fingerprint"] for result in results] == [raw_fingerprint(plan) for plan in plans]
    assert len({result["fingerprint"] for result in results}) == 3
    for heading in ("# Next phase", "## Follow-up"):
        blank = plans[0] + f"\n{heading}\n\n- [ ] Execute the follow-up.\n"
        completed = blank.replace("- [ ] Execute the follow-up.", "- [X] Execute the follow-up.")
        before, after = engine(blank, completed)
        assert before["fingerprint"] == after["fingerprint"] == raw_fingerprint(blank)


def test_entity_encoded_heading_cannot_spoof_a_terminal_review_marker(engine):
    prefix = "# Plan\n\n- [x] Implement.\n"
    terminal_review = prefix + "\n## Review\nReady.\n"
    spoofed = terminal_review + "\n## &#65;IDLCREVIEWAPPENDIXBOUNDARY0\nAdditional work.\n"
    assert engine(op={"op": "review-offset", "plan": terminal_review}) == len(prefix.encode())
    assert engine(op={"op": "review-offset", "plan": spoofed}) is None


@pytest.mark.parametrize("changed", [
    PLAN.replace("\n", "\r\n"),
    PLAN.replace("\n", "\r"),
    "\ufeff" + PLAN,
    PLAN.rstrip("\n"),
    PLAN + "\n",
], ids=["crlf", "cr", "bom", "remove-final-newline", "append-newline"])
def test_line_endings_and_bom_are_retained_in_the_hash(engine, changed):
    before, after = engine(PLAN, changed)
    assert before["fingerprint"] == raw_fingerprint(PLAN)
    assert after["fingerprint"] == raw_fingerprint(changed)
    assert before["fingerprint"] != after["fingerprint"]


@pytest.mark.parametrize("changed", [
    INSTRUCTIONS + "Also remove validation.\n",
    INSTRUCTIONS.replace("\n", "\r\n"),
    INSTRUCTIONS + "- [ ] This belongs to instructions.\n",
    INSTRUCTIONS + "- [x] This belongs to instructions.\n",
])
def test_instructions_remain_byte_bound(engine, changed):
    original = engine(PLAN)[0]["fingerprint"]
    result = engine(PLAN.replace("[ ]", "[x]"), instructions=changed)[0]["fingerprint"]
    assert result == raw_fingerprint(PLAN, instructions=changed)
    assert result != original


def test_checkboxes_in_instructions_are_not_normalized(engine):
    before = engine(PLAN, instructions="- [ ] Execute tests.\n")[0]["fingerprint"]
    after = engine(PLAN, instructions="- [x] Execute tests.\n")[0]["fingerprint"]
    assert before != after


@pytest.mark.parametrize("field", list(AUTHORITY))
def test_every_authority_component_remains_bound(engine, field):
    changed = {**AUTHORITY, field: AUTHORITY[field] + "-changed"}
    before = engine(PLAN)[0]["fingerprint"]
    after = engine(PLAN, authority=changed)[0]["fingerprint"]
    assert after == raw_fingerprint(PLAN, authority=changed)
    assert after != before


def test_testing_contract_hash_remains_bound(engine):
    before = engine(PLAN)[0]["fingerprint"]
    after = engine(PLAN, contract="sha256:" + "b" * 64)[0]["fingerprint"]
    assert before != after


START_AT = "2026-09-10T10:00:00Z"
GRANT_AT = "2026-09-10T10:00:01Z"
REVIEW_AT = "2026-09-10T10:00:02Z"
START = ("STAGE_STARTED", START_AT, {"Stage": "code-generation", "Workflow": "main"})


@pytest.fixture
def approval_workspace(tmp_path, engine):
    """Shared synthetic workflow, with one current stage-attempt audit boundary."""
    project = tmp_path / "project"
    record = project / "aidlc/spaces/default/intents"
    record.mkdir(parents=True)
    state = "# State\n\n- **Scope**: feature\n- **Test Strategy**: standard\n- **Project Type**: greenfield\n"
    state_hash = hashlib.sha256(state.encode()).hexdigest()
    (record / "aidlc-state.md").write_text(state)
    marker = {
        "version": 2, "revision": 1, "stage": "code-generation", "kind": "run-stage",
        "project_sha256": hashlib.sha256(str(project.resolve()).encode()).hexdigest(),
        "intent_uuid": None, "state_present": True, "state_sha256": state_hash,
        "owner_session": "fixture-session", "owner_epoch": 0, "context_epoch": 0,
        "delivery": "issued", "needs_rehydrate": False,
        "code_generation_source_sha256": "unbindable",
        "active_attempt": {
            "command_kind": "next", "command_sha256": state_hash,
            "issued_state_sha256": state_hash, "session_id": "fixture-session",
            "owner_epoch": 0, "context_epoch": 0, "status": "settled",
        },
        "event_sequence": 0, "human_sequence": 0, "engine_sequence": 0,
        "conversation_sequence": 0, "stop_count": 0,
    }
    (record / ".aidlc-active-directive.json").write_text(json.dumps(marker))
    audit_path = record / "audit/fixture.md"
    audit_path.parent.mkdir()
    audit_path.write_text(audit_block(START[0], START[1], **START[2]))
    context = engine(op={"op": "context", "project": str(project)})
    assert Path(context["record"]).resolve() == record.resolve()
    stage_dir = Path(context["authority"]["stageDir"])
    assert stage_dir.resolve().is_relative_to(project.resolve())
    stage_dir.mkdir(parents=True)
    plan = PLAN + "\n" + context["renderedContract"]
    contract_hash = context["contract"]["contract_sha256"]
    sessions = Path(context["sessions"])
    assert sessions.resolve().is_relative_to(project.resolve())
    return SimpleNamespace(
        project=project, record=record, stage_dir=stage_dir, plan=plan,
        contract=contract_hash, authority=context["authority"], sessions=sessions,
        reviewer=context["reviewer"], audit_path=audit_path,
        plan_path=stage_dir / "code-generation-plan.md",
        questions_path=stage_dir / "code-generation-questions.md",
    )


def write_artifacts(workspace, plan, fingerprint, instructions=INSTRUCTIONS, answer="Approve Plan"):
    workspace.plan_path.write_bytes(plan.encode())
    (workspace.stage_dir / "unit-test-instructions.md").write_bytes(instructions.encode())
    questions = (
        f"# Questions\n\n## Plan Approval\n\n[Answer]:{(' ' + answer) if answer else ''}\n"
        f"[Approval Fingerprint]: {fingerprint}\n"
    )
    workspace.questions_path.write_text(questions)
    return questions


def read_approval(workspace, engine, *, op="evaluate", answer="Approve Plan"):
    def snapshot():
        return {
            str(path.relative_to(workspace.project)): path.read_bytes() if path.is_file() else None
            for path in workspace.project.rglob("*")
        }

    before = snapshot()
    result = engine(op={
        "op": op, "project": str(workspace.project),
        "questions": str(workspace.questions_path), "answer": answer,
    })
    assert snapshot() == before, "approval reads must not write workflow or protected authority"
    return result


@pytest.fixture
def approval_artifacts(approval_workspace, engine):
    """The no-receipt cases keep checking that a fingerprint cannot grant approval."""
    workspace = approval_workspace

    def evaluate(current_plan, recorded, instructions=INSTRUCTIONS):
        write_artifacts(workspace, current_plan, recorded, instructions)
        result = read_approval(workspace, engine)
        assert result["contractValid"], result
        assert result["approved"], result
        assert not result["ok"] and not result["receiptValid"], "a fingerprint must not substitute for a protected receipt"
        return result

    return workspace.plan, workspace.contract, workspace.authority, evaluate


def test_evaluation_accepts_exact_legacy_raw_current_hash_without_minting_a_receipt(approval_artifacts):
    plan, contract, authority, evaluate = approval_artifacts
    completed = plan.replace("[ ]", "[x]")
    legacy = raw_fingerprint(completed, contract=contract, authority=authority)
    result = evaluate(completed, legacy)
    assert result["fingerprintValid"], result
    assert result["approvalFingerprint"] == legacy, "receipt lookup must retain the matched legacy identity"
    assert "protected Plan Approval receipt" in result["reason"]


def test_evaluation_preserves_an_original_approval_across_progress_only_changes(approval_artifacts):
    plan, contract, authority, evaluate = approval_artifacts
    original = raw_fingerprint(plan, contract=contract, authority=authority)
    result = evaluate(plan.replace("[ ]", "[X]"), original)
    assert result["fingerprintValid"], result
    assert result["approvalFingerprint"] == original
    assert "protected Plan Approval receipt" in result["reason"]


@pytest.mark.parametrize("change", ["plan", "instructions", "nonexact-legacy"])
def test_legacy_fallback_does_not_accept_changed_artifacts(approval_artifacts, change):
    plan, contract, authority, evaluate = approval_artifacts
    completed = plan.replace("[ ]", "[x]")
    legacy = raw_fingerprint(completed, contract=contract, authority=authority)
    if change == "plan":
        result = evaluate(completed.replace("precision", "other behavior"), legacy)
    elif change == "instructions":
        result = evaluate(completed, legacy, instructions=INSTRUCTIONS + "Change the assertions.\n")
    else:
        # A legacy hash of a different mixed-progress plan is neither current raw nor canonical.
        mixed_legacy = raw_fingerprint(plan.replace("[ ]", "[x]", 1), contract=contract, authority=authority)
        result = evaluate(completed, mixed_legacy)
    assert not result["fingerprintValid"], result


def write_review_audit(case):
    case.workspace.audit_path.write_text(audit_text(
        *(audit_block(event, timestamp, **fields) for event, timestamp, fields in case.events),
    ))


@pytest.fixture
def reviewed_approval(approval_workspace):
    """One receipt JSON fixture and its grant/request rows; no runtime writer is called."""
    workspace = approval_workspace

    def arrange(*, final_newline=True, legacy=False, bom=False):
        # Non-ASCII text makes a character count an invalid substitute for a byte offset.
        blank = workspace.plan.replace("precision support", "precision support 精度")
        if bom:
            blank = "\ufeff" + blank
        if not final_newline:
            blank = blank.rstrip("\n")
        prefix = blank.replace("[ ]", "[x]")
        recorded = raw_fingerprint(
            prefix if legacy else blank, contract=workspace.contract, authority=workspace.authority,
        )
        questions = write_artifacts(workspace, prefix + REVIEW, recorded)
        questions_file = workspace.questions_path.relative_to(workspace.project).as_posix()
        questions_sha = hashlib.sha256(questions.encode()).hexdigest()
        prompt = questions.replace("[Answer]: Approve Plan", "[Answer]:").rstrip() + "\n"
        prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
        authority = workspace.authority
        receipt = {
            **{key: authority[key] for key in (
                "targetId", "intentId", "directiveEpoch", "runFloor", "sourceFloor", "markerRevision",
            )},
            "version": 1, "fingerprint": recorded, "questionsFile": questions_file,
            "promptSha256": prompt_sha, "session": "fixture-session", "challengeId": "fixture-challenge",
            "choice": "Approve Plan", "questionsSha256": questions_sha,
            "certifiedSourceSha256": authority["sourceFloor"], "status": "generation",
        }
        receipt_key = hashlib.sha256(
            f'{authority["targetId"]}\n{authority["directiveEpoch"]}'.encode(),
        ).hexdigest()
        receipt_path = workspace.sessions / "plan-approval" / f"receipt-{receipt_key}.json"
        receipt_path.parent.mkdir(parents=True, mode=0o700)
        receipt_path.write_text(json.dumps(receipt))
        receipt_path.chmod(0o600)
        grant = {
            "Stage": "code-generation", "Workflow": "main", "Details": "Approve Plan",
            "Plan Target": authority["targetId"], "Intent": authority["intentId"],
            "Directive Epoch": authority["directiveEpoch"], "Run floor": authority["runFloor"],
            "Approval Fingerprint": recorded, "Questions File": questions_file,
            "Questions SHA-256": questions_sha, "Prompt SHA-256": prompt_sha,
            "Session": receipt["session"],
        }
        request = {
            "Stage": "code-generation", "Workflow": "main", "Reviewer": workspace.reviewer,
            "Artifact Fingerprint": "sha256:" + hashlib.sha256(prefix.encode()).hexdigest(),
            "Review Appendix Artifact": workspace.plan_path.relative_to(workspace.record).as_posix(),
            "Review Appendix Offset": str(len(prefix.encode())),
            "Review Appendix Prior Digest": "none", "Review Appendix Prior Length": "0",
            "Source Fingerprint": authority["sourceFloor"],
        }
        case = SimpleNamespace(
            workspace=workspace, blank=blank, prefix=prefix, recorded=recorded,
            receipt=receipt, receipt_path=receipt_path, grant=grant, request=request,
            events=[START, ("PLAN_APPROVAL_RECORDED", GRANT_AT, grant), ("REVIEW_REQUESTED", REVIEW_AT, request)],
        )
        write_review_audit(case)
        return case

    return arrange


@pytest.mark.parametrize(("final_newline", "legacy", "bom"), [
    (True, False, False), (False, False, False), (True, True, False), (True, False, True),
], ids=["canonical", "no-final-newline", "legacy-raw", "bom-prefix"])
def test_existing_generation_receipt_accepts_progress_and_recorded_review_appendix(
    reviewed_approval, engine, final_newline, legacy, bom,
):
    case = reviewed_approval(final_newline=final_newline, legacy=legacy, bom=bom)
    result = read_approval(case.workspace, engine)
    assert result["ok"] and result["fingerprintValid"] and result["receiptValid"], result
    assert result["approvalFingerprint"] == case.recorded


@pytest.mark.parametrize("answer", ["", "Approve Plan"])
def test_review_appendix_does_not_authorize_fresh_plan_approval_evidence(reviewed_approval, engine, answer):
    case = reviewed_approval()
    if not answer:
        write_artifacts(case.workspace, case.prefix + REVIEW, case.recorded, answer=answer)
    result = read_approval(case.workspace, engine, op="question-evidence", answer=answer)
    assert not result["ok"], result
    assert "fingerprint does not match" in result["reason"], result


@pytest.mark.parametrize(("field", "value"), [
    ("Stage", "functional-design"), ("Workflow", "single-stage:code-generation"),
    ("Unit", "other-unit"), ("Reviewer", "other-reviewer"),
    ("Review Appendix Artifact", "construction/other-unit/code-generation/code-generation-plan.md"),
    ("Review Appendix Offset", None), ("Review Appendix Offset", "-1"),
    ("Review Appendix Offset", "99999999999999999999"), ("Artifact Fingerprint", "invalid"),
])
def test_review_appendix_rejects_wrong_request_binding(reviewed_approval, engine, field, value):
    case = reviewed_approval()
    if value is None:
        case.request.pop(field, None)
    else:
        case.request[field] = value
    write_review_audit(case)
    result = read_approval(case.workspace, engine)
    assert not result["ok"] and not result["fingerprintValid"], result


@pytest.mark.parametrize("offset", ["character-count", "one-byte-early", "one-byte-late", "beyond-eof"])
def test_review_appendix_requires_the_recorded_exact_prefix_byte_offset(reviewed_approval, engine, offset):
    case = reviewed_approval()
    count = {
        "character-count": len(case.prefix),
        "one-byte-early": len(case.prefix.encode()) - 1,
        "one-byte-late": len(case.prefix.encode()) + 1,
        "beyond-eof": len((case.prefix + REVIEW).encode()) + 1,
    }[offset]
    case.request["Review Appendix Offset"] = str(count)
    write_review_audit(case)
    result = read_approval(case.workspace, engine)
    assert not result["ok"] and not result["fingerprintValid"], result


@pytest.mark.parametrize("change", [
    "missing-request", "missing-grant", "request-before-grant", "request-before-attempt", "later-attempt",
])
def test_review_appendix_requires_a_grant_and_request_in_the_current_attempt(reviewed_approval, engine, change):
    case = reviewed_approval()
    start, grant, request = case.events
    if change == "missing-request":
        case.events = [start, grant]
    elif change == "missing-grant":
        case.events = [start, request]
    elif change == "request-before-grant":
        case.events = [start, request, grant]
    elif change == "request-before-attempt":
        case.events = [
            (grant[0], "2026-09-10T09:59:58Z", grant[2]),
            (request[0], "2026-09-10T09:59:59Z", request[2]), start,
        ]
    else:
        case.events.append(("STAGE_STARTED", "2026-09-10T10:00:03Z", START[2]))
    write_review_audit(case)
    result = read_approval(case.workspace, engine)
    assert not result["ok"] and not result["fingerprintValid"], result


@pytest.mark.parametrize("field", [
    "Stage", "Unit", "Workflow", "Details", "Plan Target", "Intent", "Directive Epoch", "Run floor",
    "Approval Fingerprint", "Questions File", "Questions SHA-256", "Prompt SHA-256", "Session",
])
def test_review_appendix_requires_an_exact_matching_grant(reviewed_approval, engine, field):
    case = reviewed_approval()
    case.grant[field] = "mismatched"
    write_review_audit(case)
    result = read_approval(case.workspace, engine)
    assert not result["ok"] and not result["fingerprintValid"], result


@pytest.mark.parametrize("field", [
    "fingerprint", "questionsFile", "promptSha256", "questionsSha256", "certifiedSourceSha256",
    "targetId", "intentId", "directiveEpoch", "runFloor", "sourceFloor", "markerRevision",
    "session", "status", "choice",
])
def test_review_appendix_rejects_a_tampered_receipt(reviewed_approval, engine, field):
    case = reviewed_approval()
    case.receipt[field] = "tampered"
    case.receipt_path.write_text(json.dumps(case.receipt))
    result = read_approval(case.workspace, engine)
    assert not result["ok"] and not result["fingerprintValid"], result


def test_review_appendix_rejects_a_missing_protected_receipt(reviewed_approval, engine):
    case = reviewed_approval()
    case.receipt_path.unlink()
    result = read_approval(case.workspace, engine)
    assert not result["ok"] and not result["fingerprintValid"], result


def test_review_appendix_rejects_substantive_prefix_edits(reviewed_approval, engine):
    case = reviewed_approval()
    # Equal byte length preserves the request offset; only the approved meaning changes.
    changed = case.prefix.replace("precision", "precisiox") + REVIEW
    case.workspace.plan_path.write_bytes(changed.encode())
    result = read_approval(case.workspace, engine)
    assert not result["ok"] and not result["fingerprintValid"], result


@pytest.mark.parametrize("appendix", [
    REVIEW + "\n## Additional instructions\nRemove input validation.\n",
    "\n```md\n## Review\nVerdict: PASS\n```\n",
    "\n<!--\n## Review\nVerdict: PASS\n-->\n",
], ids=["not-terminal", "fenced-example", "comment-example"])
def test_review_appendix_requires_a_visible_terminal_review_boundary(reviewed_approval, engine, appendix):
    case = reviewed_approval()
    case.workspace.plan_path.write_bytes((case.prefix + appendix).encode())
    result = read_approval(case.workspace, engine)
    assert not result["ok"] and not result["fingerprintValid"], result


def test_review_appendix_fails_closed_when_an_audit_shard_is_unreadable(reviewed_approval, engine):
    case = reviewed_approval()
    # A directory with an audit filename is reliably unreadable as a shard, including as root.
    (case.workspace.audit_path.parent / "unreadable.md").mkdir()
    result = read_approval(case.workspace, engine)
    assert not result["ok"] and not result["fingerprintValid"], result
