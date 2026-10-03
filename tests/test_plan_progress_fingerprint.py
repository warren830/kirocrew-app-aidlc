"""Real Bun contracts for the bundled engine's Code Generation plan-approval fingerprint.

Studio used to patch ``aidlc-testing-posture.ts`` so that ticking a plan's task boxes did not void the
human's approval. AI-DLC 2.10.0 does this itself: ``approvalFingerprint`` hashes
``projectPlanApprovalContent(plan)``, which drops a terminal ``## Review`` appendix, resets task
markers and normalises editor whitespace, and binds everything else. The patch was retired with the
2.10.0 payload; these tests pin the upstream behaviour Studio's gate cards rely on, so an engine
refresh that changes it fails here instead of in a live approval.

Only exported, pure functions are called. Nothing touches a repository, a hook or an approval file.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "payload/aidlc-kiro/.kiro/tools"
AUTHORITY = {
    "targetId": "stage:code-generation",
    "intentId": "fixture-intent",
    "runFloor": "fixture-run-1",
}
CONTRACT = "sha256:" + "c" * 64
INSTRUCTIONS = "# Unit tests\n\nRun the precision and rounding assertions.\n"
PLAN = "# Code Generation Plan\n\n- [ ] Add precision support.\n- [ ] Test rounding.\n"


@pytest.fixture(scope="module")
def fingerprint():
    bun = shutil.which("bun")
    if bun is None:
        pytest.skip("real Bun is required for the engine fingerprint contract")
    source = f"""
      import {{
        approvalFingerprint, approvalFingerprintIsCurrentFormat,
      }} from {json.dumps(str(TOOLS / "aidlc-testing-posture.ts"))};
      const cases = JSON.parse(await Bun.stdin.text());
      console.log(JSON.stringify(cases.map((c) => {{
        const value = approvalFingerprint(c.plan, c.instructions, c.contract, c.authority);
        return {{ value, current: approvalFingerprintIsCurrentFormat(value) }};
      }})));
    """

    def run(*plans, instructions=INSTRUCTIONS, contract=CONTRACT, authority=None):
        cases = [
            {"plan": plan, "instructions": instructions, "contract": contract,
             "authority": AUTHORITY if authority is None else authority}
            for plan in plans
        ]
        done = subprocess.run(
            [bun, "-e", source], input=json.dumps(cases), text=True, capture_output=True,
            timeout=30, check=False,
        )
        assert done.returncode == 0, done.stderr
        results = json.loads(done.stdout)
        assert all(item["current"] for item in results), results
        return [item["value"] for item in results]

    return run


TASK_SHAPES = [
    pytest.param("- [{mark}] Add precision.\n", id="dash"),
    pytest.param("* [{mark}] Add precision.\n", id="asterisk"),
    pytest.param("+ [{mark}] Add precision.\n", id="plus"),
    pytest.param("1. [{mark}] Add precision.\n", id="ordered-dot"),
    pytest.param("2) [{mark}] Add precision.\n", id="ordered-parenthesis"),
    pytest.param("- Parent\n  - [{mark}] Add precision.\n", id="nested"),
    pytest.param("- [{mark}] Run `precision --digits 2`.\n", id="inline-code"),
]


@pytest.mark.parametrize("template", TASK_SHAPES)
def test_task_progress_does_not_void_the_approval(fingerprint, template):
    blank, lower, upper, skipped = fingerprint(
        *(template.format(mark=mark) for mark in (" ", "x", "X", "-"))
    )
    assert blank == lower == upper == skipped


def test_a_whole_plan_worked_through_keeps_its_fingerprint(fingerprint):
    steps = [f"- [{{}}] Step {n}.\n" for n in range(1, 9)]
    before = "# Plan\n\n" + "".join(step.format(" ") for step in steps)
    after = "# Plan\n\n" + "".join(step.format("x") for step in steps)
    assert fingerprint(before, after)[0] == fingerprint(before, after)[1]


@pytest.mark.parametrize("changed", [
    pytest.param(PLAN.replace("Add precision support.", "Add precision support and caching."), id="reworded"),
    pytest.param(PLAN + "- [ ] Delete the legacy module.\n", id="step-added"),
    pytest.param(PLAN.replace("- [ ] Test rounding.\n", ""), id="step-removed"),
    pytest.param("# Code Generation Plan\n\n- [ ] Test rounding.\n- [ ] Add precision support.\n", id="reordered"),
    pytest.param(PLAN.replace("# Code Generation Plan", "# Code Generation Plan v2"), id="heading"),
])
def test_a_change_to_the_work_voids_the_approval(fingerprint, changed):
    original, edited = fingerprint(PLAN, changed)
    assert original != edited


@pytest.mark.parametrize("template", [
    pytest.param("```\n- [{mark}] example in a fence\n```\n", id="fenced"),
    pytest.param("<!--\n- [{mark}] example in a comment\n-->\n", id="html-comment"),
])
def test_checkboxes_that_are_not_tasks_stay_bound(fingerprint, template):
    blank, ticked = fingerprint(*(PLAN + "\n" + template.format(mark=mark) for mark in (" ", "x")))
    assert blank != ticked


def test_a_terminal_review_appendix_is_not_part_of_the_approval(fingerprint):
    appended = PLAN + "\n## Review\n\nVerdict: approve. No findings.\n"
    assert len(set(fingerprint(PLAN, appended))) == 1


_MID_PLAN_REVIEW = "# Plan\n\n## Review\n\nVerdict: approve.\n\n## Steps\n\n- [ ] Ship.\n"
_LOWER_CASE_REVIEW = PLAN + "\n## review\n\nVerdict: approve.\n"
_FENCED_REVIEW = PLAN + "\n```\n## Review\nVerdict: approve.\n```\n"


@pytest.mark.parametrize("plan", [
    pytest.param(_MID_PLAN_REVIEW, id="mid-plan"),
    pytest.param(_LOWER_CASE_REVIEW, id="lower-case"),
    pytest.param(_FENCED_REVIEW, id="inside-fence"),
])
def test_a_review_heading_that_is_not_a_terminal_appendix_stays_bound(fingerprint, plan):
    edited = plan.replace("Verdict: approve.", "Verdict: reject.")
    assert len(set(fingerprint(plan, edited))) == 2


def test_editor_whitespace_is_not_content(fingerprint):
    crlf = PLAN.replace("\n", "\r\n")
    trailing = PLAN.replace("support.", "support.   ")
    extra_blank = PLAN.replace("\n\n", "\n\n\n")
    assert len(set(fingerprint(PLAN, crlf, trailing, extra_blank))) == 1


@pytest.mark.parametrize("changed", [
    pytest.param(INSTRUCTIONS + "Also check negative zero.\n", id="added-line"),
    pytest.param(INSTRUCTIONS.replace("precision", "Precision"), id="case"),
    pytest.param(INSTRUCTIONS + "- [x] already run\n", id="ticked-checkbox"),
])
def test_unit_test_instructions_are_byte_bound(fingerprint, changed):
    (original,) = fingerprint(PLAN)
    (edited,) = fingerprint(PLAN, instructions=changed)
    assert original != edited


@pytest.mark.parametrize("field", list(AUTHORITY))
def test_every_authority_component_is_bound(fingerprint, field):
    (original,) = fingerprint(PLAN)
    (other,) = fingerprint(PLAN, authority={**AUTHORITY, field: AUTHORITY[field] + "-other"})
    assert original != other


def test_the_testing_contract_hash_is_bound(fingerprint):
    (original,) = fingerprint(PLAN)
    (other,) = fingerprint(PLAN, contract="sha256:" + "e" * 64)
    assert original != other
