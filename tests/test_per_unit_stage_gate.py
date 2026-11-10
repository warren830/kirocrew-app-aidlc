"""A per-unit stage's one gate over every Unit: stage-major Construction, a swarm with human approval, unit-major with
checkpoints off. The directive is stale by the time the gate opens and ``unit complete`` dropped the Active Unit, so
nothing names a Unit, and the engine's gate reads every ``construction/<unit>/<stage>/`` (``producesDirsForStage``)."""

from __future__ import annotations

import pytest

import conftest as CT
import fixtures as F
from test_projection import proj_mod, projection, repo_record  # noqa: F401

INTENT = "261110-per-unit-gate"
STAGE = "nfr-requirements"
UNITS = ("u1-orders", "u2-billing")
REQUIRED = {"security-requirements": "md", "tech-stack-decisions": "md", "traceability": "json"}
GATE_STATE = F.state_text(marks={STAGE: "?"}, fields={
    "Current Stage": STAGE, "Status": "Running", "Next Stage": "nfr-design"})
STALE_DIRECTIVE = {"version": 2, "kind": "run-stage", "stage": STAGE, "unit": UNITS[-1], "state_sha256": "0" * 64}
ANSWERED = "# Questions\n\n## Question 1\nWhich store?\n\n- A. Postgres\n- B. SQLite\n\n[Answer]: A\n"
PENDING = "# Questions\n\n## Question 1\nWhich store?\n\n- A. Postgres\n- B. SQLite\n\n[Answer]:\n"


def gate(repo_builder, projection, repo_record, *, missing=(), questions=ANSWERED, state=GATE_STATE):
    files = {f"construction/{STAGE}/memory.md": "# memory\n"}
    for unit in UNITS:
        files.update({f"construction/{unit}/{STAGE}/{name}.{ext}": f"# {name}\n" for name, ext in REQUIRED.items()
                      if (unit, name) not in missing})
    root = repo_builder.with_engine().with_workspace().with_intent(
        INTENT, state=state, audit=F.gate_open_audit(STAGE), directive=STALE_DIRECTIVE, artifacts=files,
        questions={f"construction/{unit}/{STAGE}/{STAGE}-questions.md": questions for unit in UNITS},
    ).with_git().build()
    snap = projection.snapshot(repo_record(root, installed_engine_version="2.11.2"), "default", INTENT)
    facts = CT.studio_module("consistency").RepoFacts(now=snap.taken_at, repo=None, install=None)
    return snap, projection._consistency.evaluate_intent(snap, facts)


def test_a_whole_stage_gate_reads_every_units_outputs_and_blocks_nothing(repo_builder, projection, repo_record):
    snap, findings = gate(repo_builder, projection, repo_record)
    assert [f.code for f in findings if f.severity == "blocking"] == []
    shown = {meta.relpath.split("/construction/", 1)[1] for meta in snap.stage_artifacts}
    assert {f"{unit}/{STAGE}/security-requirements.md" for unit in UNITS} <= shown


def test_a_whole_stage_gate_still_names_the_one_units_missing_output(repo_builder, projection, repo_record):
    snap, findings = gate(repo_builder, projection, repo_record, missing={(UNITS[1], "tech-stack-decisions")})
    blocking = [(f.code, f.params.get("artifact"), f.params.get("stage_dir", "").rsplit("/", 2)[-2])
                for f in findings if f.severity == "blocking"]
    assert blocking == [("missing_required_artifact", "tech-stack-decisions", UNITS[1])]


@pytest.mark.parametrize("case", ["units_owe_answers", "stage_still_running", "team_owned_units"])
def test_without_a_whole_stage_gate_an_unnamed_unit_stays_ambiguous(repo_builder, projection, repo_record, case):
    """Two Units each owing an answer could get the wrong one's; a running stage has no gate yet; team-owned Units
    each have a gate of their own."""
    over = {"units_owe_answers": {"questions": PENDING},
            "stage_still_running": {"state": F.state_text(marks={STAGE: "-"}, fields={
                "Current Stage": STAGE, "Status": "Running", "Next Stage": "nfr-design"})},
            "team_owned_units": {"state": F.insert_runtime_field(GATE_STATE, "Unit Ownership", "team")}}[case]
    snap, findings = gate(repo_builder, projection, repo_record, **over)
    assert snap.gate_units == ()
    assert "unit_ambiguous" in [f.code for f in findings if f.severity == "blocking"]


def test_a_gate_missing_an_output_still_offers_request_changes_beside_the_recovery_card(
    repo_builder, projection, repo_record,
):
    """The blocking finding raises a recovery card, and the gate card stays with its own decisions: Request Changes
    is how the person asks AI-DLC to write the missing output."""
    snap, findings = gate(repo_builder, projection, repo_record, missing={(UNITS[1], "tech-stack-decisions")})
    seeds = {seed.type: seed for seed in projection.derive_cards(
        snap, findings=findings, binding=None, install=None, breakers=[], live_actions=[])}
    assert seeds["recovery"].evidence["recovery_codes"] == ["missing_required_artifact"]
    assert seeds["gate"].stage == STAGE and "request_changes" in seeds["gate"].decisions
