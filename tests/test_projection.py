"""Golden fixtures for the read model: cards, precedence, evidence and the queue order.

Every scenario is a real AI-DLC workspace laid out on disk by ``RepoBuilder`` from the harvested
fixtures, because the card derivation and the operational-state ladder are the two places where a
plausible-looking shortcut produces a wrong queue: a gate that never appears, a `[?]` row hidden behind
a spinner, a card whose evidence no longer describes what the human is being shown. So the assertions
here are on bytes-in / model-out, not on internal calls.

Seven repositories carry the load, one per hazard the design calls out:

* ``gate_repo`` (conftest) — an open gate with a blank question group, i.e. the case that legitimately
  yields BOTH a gate card and a question card (FORMAT-NOTES §7).
* ``revising_repo`` — a `[R]` row: informational, never a queue item (C18).
* ``question_repo`` — a `[-]` stage with six blank `[Answer]:` tags and no gate (the real 2.1.1 case).
* ``parked_repo`` — a parked workflow.
* ``dangling_repo`` — ``active-intent`` naming a directory with no state file.
* ``burst_repo`` — three ``ERROR_LOGGED`` rows inside ten minutes.
* ``missing_repo`` — a registered path that is not there.

``IntentSnapshot`` is projection's own dataclass, so tests that need an unstable read or an injected
finding build one with ``dataclasses.replace``. That is a value, not a mock: nothing in the module under
test is replaced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import fixtures as F
import pytest

GATE_DIR = "260904-gate-demo"
GATE_STAGE = "requirements-analysis"
QUESTIONS_REL = "inception/requirements-analysis/requirements-analysis-questions.md"
REPO_ID = "r_000000000001"


# --------------------------------------------------------------------------- #
# module fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def proj_mod(studio):
    module = getattr(studio, "projection", None)
    assert module is not None, getattr(studio, "projection__error", "projection.py did not import")
    return module


@pytest.fixture
def C(studio):
    return studio.constants


@pytest.fixture
def projection(studio, proj_mod, clock):
    reader = studio.aidlc_reader.AidlcReader(clock)
    return proj_mod.Projection(reader, studio.consistency.ConsistencyEngine(), clock)


@pytest.fixture
def repo_record(studio, clock):
    def build(path: Path, *, repo_id: str = REPO_ID, label: str = "demo", **over: Any):
        fields = dict(
            repo_id=repo_id,
            label=label,
            canonical_path=str(path),
            resolved_identity="1:2",
            git_common_dir_identity=None,
            platform="darwin",
            added_at=clock.iso(),
            last_seen=None,
            availability="available",
            availability_detail=None,
            archived=False,
            legacy_console_id=None,
            install_status="installed",
            installed_engine_version="2.6.2",
            engine_dir=".kiro",
            receipt_version=None,
        )
        fields.update(over)
        return studio.repo_registry.RepoRecord(**fields)

    return build


# --------------------------------------------------------------------------- #
# stubs for objects other modules own (§1.11 / §1.12 shapes)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BindingStub:
    slot_key: str | None = None
    session_key: str | None = None
    archive_state: str = "active"
    paused: bool = False
    interrupted_at: str | None = None
    last_stable_boundary: Any | None = None


@dataclass(frozen=True)
class SlotStub:
    key: str = "aidlc-studio-r_000000000001-260904-gate-demo"
    running: bool = False
    stopping: bool = False
    stop_state: str = "idle"
    queue_depth: int = 0
    needs_input: bool = False
    last_turn_ts: str = ""
    session_key: str = "dashboard:aidlc-studio-r_000000000001-260904-gate-demo"
    busy_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class SessionStub:
    slot_key: str = "aidlc-studio-r_000000000001-260904-gate-demo"
    session_key: str = "dashboard:aidlc-studio-r_000000000001-260904-gate-demo"
    running: bool = False
    bound_at: str = "2026-09-04T09:00:00Z"


@dataclass(frozen=True)
class ActionStub:
    action_id: str = "a_0000000000000001"
    type: str = "gate"
    status: str = "Queued"
    stage: str | None = GATE_STAGE
    created_at: str = "2026-09-04T09:00:00Z"
    updated_at: str = "2026-09-04T09:00:00Z"
    resolved_at: str | None = None
    waiting_since: str = "2026-09-04T09:00:00Z"
    evidence: dict | None = None
    retry_fingerprint: str | None = None


@dataclass(frozen=True)
class FindingStub:
    code: str
    severity: str

    @property
    def message_key(self) -> str:
        return f"finding.{self.code}"

    def to_json(self) -> dict:
        return {"code": self.code, "severity": self.severity, "message_key": self.message_key,
                "params": {}, "evidence": []}


@dataclass(frozen=True)
class BreakerStub:
    key: str = f"{REPO_ID}:{GATE_DIR}:transport.stream-closed:ab12cd"
    count: int = 3
    opened_at: str = "2026-09-04T09:30:00Z"
    reason: str = "transport"
    fingerprint: str = "transport.stream-closed:ab12cd"
    fingerprint_class: str = "transport"

    def to_json(self) -> dict:
        return {"key": self.key, "count": self.count, "opened_at": self.opened_at,
                "fingerprint": self.fingerprint, "fingerprint_class": self.fingerprint_class}


@dataclass(frozen=True)
class InstallStub:
    status: str = "recovery_required"
    engine_dir: str = ".kiro"
    engine_version: str = "2.6.2"

    def to_json(self) -> dict:
        return {"status": self.status, "engine_dir": self.engine_dir, "engine_version": self.engine_version}


# --------------------------------------------------------------------------- #
# golden repositories
# --------------------------------------------------------------------------- #


def _builder(repo_builder, name: str):
    """A builder rooted in its OWN directory.

    ``repo_builder`` is one function-scoped object at ``tmp_path/"repo"``, so two golden-repo fixtures
    that both used it would silently overwrite each other and a test that asks for both would assert
    against whichever was built last.
    """
    return type(repo_builder)(repo_builder.root.parent / f"repo-{name}")


def _gate_state(**fields: str) -> str:
    base = {"Current Stage": GATE_STAGE, "Status": "Running", "Next Stage": "user-stories",
            "Lifecycle Phase": "INCEPTION"}
    base.update(fields)
    return F.state_text(marks={GATE_STAGE: "?"}, fields=base)


@pytest.fixture
def revising_repo(repo_builder) -> Path:
    """A `[R]` row: the engine is revising after a rejected gate."""
    state = F.state_text(
        marks={GATE_STAGE: "R"},
        fields={"Current Stage": GATE_STAGE, "Status": "Running", "Revision Count": "1",
                "Lifecycle Phase": "INCEPTION"},
    )
    return (
        _builder(repo_builder, "revising")
        .with_engine("payload")
        .with_workspace()
        .with_intent(GATE_DIR, state=state, audit=F.gate_rejected_audit(GATE_STAGE))
        .build()
    )


@pytest.fixture
def question_repo(repo_builder) -> Path:
    """A `[-]` stage whose questions file has six blank tags and no gate — the real devlake case."""
    text = F.read(
        F.OLDER_INTENTS / F.OLDER_INTENT / "ideation" / "feasibility" / "feasibility-questions.md"
    )
    state = F.state_text(
        marks={"feasibility": "-"},
        fields={"Current Stage": "feasibility", "Status": "Running", "Lifecycle Phase": "IDEATION"},
    )
    return (
        _builder(repo_builder, "questions")
        .with_engine("payload")
        .with_workspace()
        .with_intent(
            GATE_DIR,
            state=state,
            audit=F.audit_text(F.audit_block("STAGE_STARTED", "2026-09-04T09:00:02Z", Stage="feasibility")),
            questions={"ideation/feasibility/feasibility-questions.md": text},
        )
        .build()
    )


@pytest.fixture
def parked_repo(repo_builder) -> Path:
    state = F.insert_runtime_field(
        F.insert_runtime_field(_gate_state(), "Parked", "2026-09-03T12:00:00Z"),
        "Parked At Stage",
        GATE_STAGE,
    )
    return (
        _builder(repo_builder, "parked")
        .with_engine("payload")
        .with_workspace()
        .with_intent(GATE_DIR, state=state, audit=F.gate_open_audit(GATE_STAGE))
        .build()
    )


@pytest.fixture
def dangling_repo(repo_builder) -> Path:
    """`active-intent` naming a directory that has no state file (the engine would fall back silently)."""
    root = (
        _builder(repo_builder, "dangling")
        .with_engine("payload")
        .with_workspace()
        .with_intent(GATE_DIR, state=_gate_state(), audit=F.gate_open_audit(GATE_STAGE))
        .build()
    )
    cursor = root / "aidlc" / "spaces" / "default" / "intents" / "active-intent"
    (cursor.parent / "260905-ghost").mkdir(exist_ok=True)
    cursor.write_text("260905-ghost\n")
    return root


@pytest.fixture
def burst_repo(repo_builder) -> Path:
    return (
        _builder(repo_builder, "burst")
        .with_engine("payload")
        .with_workspace()
        .with_intent(
            GATE_DIR,
            state=F.state_text(
                marks={GATE_STAGE: "-"},
                fields={"Current Stage": GATE_STAGE, "Status": "Running", "Lifecycle Phase": "INCEPTION"},
            ),
            audit=F.audit_text(
                F.audit_block("STAGE_STARTED", "2026-09-04T08:50:00Z", Stage=GATE_STAGE),
                F.error_burst_audit(ts="2026-09-04T09:00:00Z", count=3),
            ),
        )
        .build()
    )


@pytest.fixture
def missing_repo(tmp_path) -> Path:
    """A registered path that is simply not on disk any more."""
    return tmp_path / "gone"


@pytest.fixture
def gate_snap(projection, repo_record, gate_repo):
    return projection.snapshot(repo_record(gate_repo), "default", GATE_DIR)


# --------------------------------------------------------------------------- #
# snapshot
# --------------------------------------------------------------------------- #


def test_snapshot_reads_one_coherent_moment(gate_snap, gate_repo, C):
    snap = gate_snap
    assert snap.stable is True
    assert snap.is_active is True
    assert snap.dangling_cursor is False
    assert snap.stage == GATE_STAGE
    assert snap.intent_key == GATE_DIR                       # default space → bare dir name (C02)
    assert snap.state is not None and snap.state.state_version == 8
    assert snap.state_sha256 == F.sha256((Path(gate_repo) / "aidlc" / "spaces" / "default" / "intents"
                                          / GATE_DIR / "aidlc-state.md").read_text("utf-8"))
    # Read off the live payload, so the version literal belongs to `constants`, not to this assertion.
    assert snap.engine is not None and snap.engine.engine_version == C.BUNDLED_ENGINE_VERSION
    assert len(snap.graph) == C.BUNDLED_STAGE_COUNT == 33
    assert snap.layout == "spaces"
    assert snap.stage_dir_rel and snap.stage_dir_rel.endswith(f"inception/{GATE_STAGE}")


def test_snapshot_always_hashes_the_card_stage_artifacts(gate_snap):
    """Review P14: the evidence set is what the human looked at, so it is always digested."""
    names = {a.name: a for a in gate_snap.stage_artifacts}
    assert set(names) == {"requirements.md", f"{GATE_STAGE}-questions.md"}
    assert all(a.sha256 and len(a.sha256) == 64 for a in gate_snap.stage_artifacts)


def test_snapshot_parses_the_review_section_of_the_primary_artifact(
    projection, repo_record, repo_builder
):
    artifact = (
        "# Requirements\n\nOne requirement.\n\n"
        "## Review\n\n**Verdict**: NOT-READY\n\n### Blocker\n\n- Acceptance criteria are missing\n"
    )
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(
            GATE_DIR,
            state=_gate_state(),
            audit=F.gate_open_audit(GATE_STAGE),
            artifacts={"inception/requirements-analysis/requirements.md": artifact},
        )
        .build()
    )
    snap = projection.snapshot(repo_record(root), "default", GATE_DIR)
    assert snap.review is not None
    verdict, findings = snap.review
    assert verdict == "NOT-READY"
    assert [f.level for f in findings] == ["blocker"]


#: The four stages where 2.7.1's `review_artifact` is NOT `produces[0]` — `{slug: (reviewed, produced)}`.
#: The reviewer protocol allows the `## Review` appendix in the reviewed file only, so on these four a
#: `produces[0]` guess reads a file that can never hold a verdict.
REVIEW_ARTIFACT_DISAGREEMENTS = {
    "functional-design": ("functional-spec", "entities"),
    "nfr-requirements": ("security-requirements", "performance-requirements"),
    "nfr-design": ("security-design", "performance-design"),
    "infrastructure-design": ("cicd-pipeline", "infrastructure-specification"),
}


@pytest.mark.parametrize("slug", sorted(REVIEW_ARTIFACT_DISAGREEMENTS))
def test_review_comes_from_the_reviewed_artifact_not_the_first_produced_one(
    projection, repo_record, repo_builder, slug
):
    """The verdict must be read out of the graph's `review_artifact`, on all four stages that disagree.

    Both files carry a `## Review`, and they disagree, because that is the failure the guess produces: a
    stale or unrelated verdict shown against another artifact's evidence digest — a human approving work
    they never looked at. The `node` assertion keeps this test honest if the payload's graph ever moves.
    """
    reviewed, first_produced = REVIEW_ARTIFACT_DISAGREEMENTS[slug]
    state = F.state_text(marks={slug: "?"}, fields={"Current Stage": slug, "Status": "Running"})
    root = (
        _builder(repo_builder, f"review-{slug}")
        .with_engine("payload")
        .with_workspace()
        .with_intent(
            GATE_DIR,
            state=state,
            audit=F.gate_open_audit(slug),
            artifacts={
                f"construction/{slug}/{first_produced}.md":
                    "# Produced first\n\n## Review\n\n**Verdict**: NOT-READY\n",
                f"construction/{slug}/{reviewed}.md":
                    "# Reviewed\n\n## Review\n\n**Verdict**: READY\n",
            },
        )
        .build()
    )
    snap = projection.snapshot(repo_record(root), "default", GATE_DIR)
    node = snap.node(slug)
    assert node is not None
    assert (node.review_artifact, node.produces[0]) == (reviewed, first_produced)
    assert snap.review is not None
    assert snap.review[0] == "READY"


def test_snapshot_of_a_missing_repo_degrades_instead_of_raising(projection, repo_record, missing_repo):
    """One unavailable repository must not abort the scan of the other 63."""
    snap = projection.snapshot(
        repo_record(missing_repo, availability="unavailable"), "default", GATE_DIR
    )
    assert snap.state is None
    assert snap.state_snap is None
    assert snap.stable is False
    assert snap.stage is None
    assert snap.engine is None
    assert snap.graph == ()
    captured = projection.captured(snap, None)
    assert captured["state_hash"] is None and captured["boundary_token"] is None
    assert captured["stable"] is False


def test_snapshot_of_a_missing_repo_still_summarises(projection, repo_record, missing_repo):
    snap = projection.snapshot(
        repo_record(missing_repo, availability="unavailable"), "default", GATE_DIR
    )
    summary = projection.summarize(snap)
    body = summary.to_json()
    assert body["unstable"] is True
    assert body["disk"]["status"] is None
    assert body["counts"]["total"] == 0
    assert body["operational_state"] == "Idle"


def test_snapshot_reports_a_dangling_cursor(projection, repo_record, dangling_repo):
    snap = projection.snapshot(repo_record(dangling_repo), "default", GATE_DIR)
    assert snap.dangling_cursor is True
    assert snap.is_active is False                     # the cursor names the ghost, not this intent
    assert snap.cursor == "260905-ghost"               # reported verbatim, so the card can name it


# --------------------------------------------------------------------------- #
# captured / evidence stability
# --------------------------------------------------------------------------- #


def test_captured_is_the_full_compare_and_submit_set(projection, gate_snap):
    captured = projection.captured(gate_snap, GATE_STAGE)
    assert set(captured) == {
        "state_hash", "boundary_token", "question_digest", "stage_attempt",
        "evidence_digest", "is_active", "captured_at", "stable",
    }
    assert all(captured[key] for key in ("state_hash", "boundary_token", "question_digest", "evidence_digest"))
    assert captured["stage_attempt"] == 1
    assert captured["is_active"] is True
    assert captured["stable"] is True


def test_captured_is_stable_across_two_reads_of_an_untouched_repo(
    projection, repo_record, gate_repo, clock
):
    """A card must not go stale because time passed — only because disk moved (FR-ACT-010)."""
    first = projection.captured(projection.snapshot(repo_record(gate_repo), "default", GATE_DIR), GATE_STAGE)
    clock.advance(120)
    second = projection.captured(projection.snapshot(repo_record(gate_repo), "default", GATE_DIR), GATE_STAGE)
    compare = ("state_hash", "boundary_token", "question_digest", "stage_attempt",
               "evidence_digest", "is_active")
    assert {k: first[k] for k in compare} == {k: second[k] for k in compare}
    assert first["captured_at"] != second["captured_at"]


def test_evidence_digest_moves_when_a_stage_artifact_changes(projection, repo_record, gate_repo):
    before = projection.captured(projection.snapshot(repo_record(gate_repo), "default", GATE_DIR), GATE_STAGE)
    record = Path(gate_repo) / "aidlc/spaces/default/intents" / GATE_DIR
    artifact = record / "inception/requirements-analysis/requirements.md"
    artifact.write_text("# Requirements\n\nOne requirement, revised.\n")
    after = projection.captured(projection.snapshot(repo_record(gate_repo), "default", GATE_DIR), GATE_STAGE)
    assert before["state_hash"] == after["state_hash"]          # the state file did not move
    assert before["evidence_digest"] != after["evidence_digest"]  # what the human reads did


def test_captured_null_vs_non_null_is_a_difference(projection, repo_record, gate_repo):
    """Review P26.3: removing the questions file must invalidate a card captured with its digest."""
    with_file = projection.captured(projection.snapshot(repo_record(gate_repo), "default", GATE_DIR), GATE_STAGE)
    (Path(gate_repo) / "aidlc/spaces/default/intents" / GATE_DIR / QUESTIONS_REL).unlink()
    without = projection.captured(projection.snapshot(repo_record(gate_repo), "default", GATE_DIR), GATE_STAGE)
    assert with_file["question_digest"] is not None
    assert without["question_digest"] is None
    assert with_file != without


def test_captured_has_no_stage_evidence_for_a_stageless_card(projection, gate_snap):
    stageless = projection.captured(gate_snap, None)
    assert stageless["evidence_digest"] is None
    assert stageless["question_digest"] is None
    assert stageless["stage_attempt"] is None
    assert stageless["state_hash"] == projection.captured(gate_snap, GATE_STAGE)["state_hash"]


def test_evidence_snapshot_carries_the_full_card_shape(projection, gate_snap):
    body = projection.evidence(
        gate_snap, findings=[FindingStub("completed_count_mismatch", "warn")],
        binding=BindingStub(slot_key="slot-1", session_key="dashboard:slot-1"), slot=SlotStub(),
    ).to_json()
    assert set(body) == {
        "state", "audit", "directive", "artifacts", "review", "acceptance_criteria", "findings",
        "session", "questions", "git", "markers", "presence_baseline", "presence", "cursor_readback",
    }
    assert body["state"]["sha256"] == gate_snap.state_sha256
    assert body["state"]["stable"] is True
    assert body["audit"]["complete"] is True
    assert body["audit"]["boundary_event"]["type"] == "STAGE_AWAITING_APPROVAL"
    assert body["directive"] == {
        "stage": GATE_STAGE, "unit": None, "units": [], "matches_state": True,
        "version": 1, "kind": None, "state_sha256": gate_snap.state_sha256,
    }
    assert [a["name"] for a in body["artifacts"]] == [f"{GATE_STAGE}-questions.md", "requirements.md"]
    assert body["findings"][0]["code"] == "completed_count_mismatch"
    assert body["session"]["slot_key"] == "slot-1"
    assert body["questions"]["mode"] == "structured"  # supported persisted group supplies its controls
    # the three reconciler-owned keys are present and null so the wire shape never changes shape later
    assert body["presence_baseline"] is None and body["presence"] is None and body["cursor_readback"] is None


def test_evidence_never_claims_an_acceptance_criterion_is_met(projection, gate_snap):
    body = projection.evidence(gate_snap).to_json()
    assert all(row["met"] is None for row in body["acceptance_criteria"])


def test_supported_file_questions_do_not_require_a_native_widget(projection, gate_snap, studio, monkeypatch):
    monkeypatch.setattr(studio.constants, "GROUPED_ANSWERS_VERIFIED", True)
    assert projection.questions_view(gate_snap).mode == "structured"
    assert projection.questions_view(gate_snap, host_card={"ask_id": "active-native-wait"}).mode == "degraded"
    assert projection.questions_view(replace(gate_snap, stable=False)).mode == "degraded"
    question = gate_snap.questions.questions[0]
    ambiguous = replace(gate_snap.questions, questions=(question, question))
    assert projection.questions_view(replace(gate_snap, questions=ambiguous)).mode == "degraded"
    assert projection.questions_view(replace(gate_snap, questions=ambiguous),
                                     host_card={"card_id": "nonblocking"}).mode == "degraded"
    monkeypatch.setattr(studio.constants, "GROUPED_ANSWERS_VERIFIED", False)
    assert projection.questions_view(gate_snap).mode == "degraded"


def test_evidence_markers_on_a_card_omit_volatile_hook_health(projection, gate_snap):
    card = projection.evidence(gate_snap).to_json()["markers"]
    assert set(card) == {"human_turn_at", "engine_touch_at", "turn_counter", "goal_stop_present", "recovery"}


# --------------------------------------------------------------------------- #
# unstable reads cannot authorise a decision
# --------------------------------------------------------------------------- #


def test_unstable_snapshot_renders_refreshing_and_cannot_authorise(projection, studio, gate_snap):
    """FR-ACT-009: a read that spanned a write is evidence of nothing."""
    unstable = replace(gate_snap, stable=False)

    # 1. the consistency engine calls it unstable → the card renders `Refreshing`
    assert studio.consistency.ConsistencyEngine().unstable(unstable) is True
    codes = [
        f.code
        for f in studio.consistency.ConsistencyEngine().evaluate_intent(
            unstable,
            studio.consistency.RepoFacts(
                now=unstable.taken_at,
                repo=None,
                install=None,
            ),
        )
    ]
    assert "unstable_read" in codes

    # 2. the summary says so
    assert projection.summarize(unstable).to_json()["unstable"] is True

    # 3. every captured set taken from it is marked unstable, so `submit` refuses (409 unstable_read)
    assert projection.captured(unstable, GATE_STAGE)["stable"] is False
    seeds = projection.derive_cards(
        unstable, findings=[], binding=None, install=None, breakers=[], live_actions=[]
    )
    assert seeds, "the boundary is still real — the card must exist, it just cannot be submitted"
    assert all(seed.captured["stable"] is False for seed in seeds)

    # 4. and the boundary is not stable, naming the reason
    boundary = projection.stable_boundary(unstable)
    assert boundary.stable is False
    assert "state_unstable" in boundary.reasons


def test_stable_boundary_names_every_unmet_condition(projection, proj_mod, gate_snap):
    good = projection.stable_boundary(gate_snap)
    assert good.stable is True and good.reasons == ()
    assert good.marker == "[?]" and good.stage == GATE_STAGE
    assert good.boundary_token == projection.captured(gate_snap, GATE_STAGE)["boundary_token"]

    busy = projection.stable_boundary(
        replace(gate_snap, stable=False, is_active=False, cursor="260905-other"),
        live_actions=[ActionStub(status="Processing")],
        busy_reasons=("running",),
        lease_active=True,
    )
    assert busy.stable is False
    assert set(busy.reasons) == {"state_unstable", "action_in_flight", "session_busy", "cursor_mismatch"}
    assert set(busy.reasons) <= set(proj_mod.BOUNDARY_REASONS)


def test_stable_boundary_reports_not_at_boundary(projection, repo_record, burst_repo):
    snap = projection.snapshot(repo_record(burst_repo), "default", GATE_DIR)
    boundary = projection.stable_boundary(snap)
    assert boundary.marker is None
    assert boundary.reasons == ("not_at_boundary",)


# --------------------------------------------------------------------------- #
# operational state — every rule, in precedence order
# --------------------------------------------------------------------------- #


def test_rule_01_archived_wins_over_everything(projection, gate_snap):
    """Even an unresolved delivery on an archived intent shows as Archived: nothing is dispatchable."""
    assert (
        projection.operational_state(
            gate_snap,
            binding=BindingStub(archive_state="archived", interrupted_at="2026-09-04T09:00:00Z"),
            live_actions=[ActionStub(status="ReconciliationRequired")],
            breaker_open=True,
            findings=[FindingStub("dangling_cursor", "blocking")],
        )
        == "Archived"
    )


def test_rule_02_blocking_finding_or_uncertain_action(projection, gate_snap):
    assert (
        projection.operational_state(gate_snap, findings=[FindingStub("gate_without_audit_row", "blocking")])
        == "ReconciliationRequired"
    )
    assert (
        projection.operational_state(gate_snap, live_actions=[ActionStub(status="DeliveryUncertain")])
        == "ReconciliationRequired"
    )
    assert (
        projection.operational_state(gate_snap, live_actions=[ActionStub(status="ReconciliationRequired")])
        == "ReconciliationRequired"
    )
    # a warn finding is not blocking and must not shadow the real state
    assert (
        projection.operational_state(gate_snap, findings=[FindingStub("receipt_drift", "warn")])
        == "WaitingForYou"
    )


def test_rule_03_interrupted_only_while_the_stop_is_in_flight(projection, gate_snap):
    """P17 / C28: `Interrupted → Queued` must be unreachable in one click."""
    binding = BindingStub(slot_key="slot-1", interrupted_at="2026-09-04T09:40:00Z")
    assert (
        projection.operational_state(gate_snap, binding=binding, host=SessionStub(running=True))
        == "Interrupted"
    )
    assert (
        projection.operational_state(gate_snap, binding=binding, slot=SlotStub(stop_state="killing"))
        == "Interrupted"
    )
    # once the slot has stopped, `interrupted_session` (blocking) takes over through rule 2
    assert (
        projection.operational_state(
            gate_snap,
            binding=binding,
            host=SessionStub(running=False),
            findings=[FindingStub("interrupted_session", "blocking")],
        )
        == "ReconciliationRequired"
    )
    # and after `acknowledge` cleared interrupted_at, a queued run is simply Queued
    assert (
        projection.operational_state(
            gate_snap,
            binding=BindingStub(slot_key="slot-1"),
            live_actions=[ActionStub(type="run", status="Queued")],
        )
        == "Queued"
    )


def test_rule_04_breaker_open(projection, gate_snap):
    assert projection.operational_state(gate_snap, breaker_open=True) == "CircuitOpen"
    # but a blocking finding still outranks it
    assert (
        projection.operational_state(
            gate_snap, breaker_open=True, findings=[FindingStub("lease_conflict", "blocking")]
        )
        == "ReconciliationRequired"
    )


def test_rule_05_completed_and_rule_06_parked(projection, repo_record, parked_repo, repo_builder):
    parked = projection.snapshot(repo_record(parked_repo), "default", GATE_DIR)
    assert parked.state is not None and parked.state.parked_at == "2026-09-03T12:00:00Z"
    assert projection.operational_state(parked) == "Parked"

    completed = replace(
        parked,
        state=replace(parked.state, status="Completed"),
    )
    assert projection.operational_state(completed) == "Completed"   # rule 5 precedes rule 6


def test_rule_07_running_from_the_slot_or_an_in_flight_turn(projection, gate_snap):
    assert projection.operational_state(gate_snap, host=SessionStub(running=True)) == "Running"
    assert projection.operational_state(gate_snap, live_actions=[ActionStub(status="Delivered")]) == "Running"
    assert projection.operational_state(gate_snap, live_actions=[ActionStub(status="Processing")]) == "Running"


def test_rule_08_only_command_actions_make_an_intent_queued(projection, gate_snap):
    """Review P08: a derived gate card sitting in `Queued` is not a queued turn."""
    def state(kind: str, status: str) -> str:
        return projection.operational_state(
            gate_snap, live_actions=[ActionStub(type=kind, status=status)]
        )

    assert state("gate", "Queued") == "WaitingForYou"
    assert state("run", "Queued") == "Queued"
    assert state("resume", "Queued") == "Queued"
    assert state("prepare_commit", "Delivering") == "Queued"
    assert state("force_stop", "Queued") == "Queued"
    # anything of any type in Delivering is a dispatch in flight
    assert state("gate", "Delivering") == "Queued"


def test_rule_09_waiting_for_you_from_the_gate_mark(
    projection, gate_snap, repo_record, revising_repo, question_repo
):
    assert projection.operational_state(gate_snap) == "WaitingForYou"
    revising = projection.snapshot(repo_record(revising_repo), "default", GATE_DIR)
    assert revising.state is not None
    assert F.stage_marks(revising.state_snap.text)[GATE_STAGE] == "R"
    assert projection.operational_state(revising) == "WaitingForYou"
    questions = projection.snapshot(repo_record(question_repo), "default", GATE_DIR)
    assert questions.questions is not None and questions.questions.pending_count == 6
    assert projection.operational_state(questions) == "WaitingForYou"


def test_rule_09_waiting_for_you_from_a_pending_checkpoint(projection, repo_record, repo_builder):
    text = (
        "# Plan\n\n## Plan Approval\n\n- Approve Plan\n- Request Changes\n\n[Answer]:\n"
    )
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(
            GATE_DIR,
            state=F.state_text(
                marks={GATE_STAGE: "-"},
                fields={"Current Stage": GATE_STAGE, "Status": "Running", "Lifecycle Phase": "INCEPTION"},
            ),
            audit=F.audit_text(F.audit_block("STAGE_STARTED", "2026-09-04T09:00:00Z", Stage=GATE_STAGE)),
            questions={QUESTIONS_REL: text},
        )
        .build()
    )
    snap = projection.snapshot(repo_record(root), "default", GATE_DIR)
    assert snap.questions is not None
    assert snap.questions.pending_count == 0
    assert snap.questions.pending_checkpoint == "plan_approval"
    assert projection.operational_state(snap) == "WaitingForYou"


def test_rule_10_newest_failed_action_is_retry_eligible_or_failed(projection, repo_record, burst_repo):
    snap = projection.snapshot(repo_record(burst_repo), "default", GATE_DIR)
    assert projection.operational_state(snap) == "Idle"

    transient = ActionStub(type="run", status="Failed", updated_at="2026-09-04T09:10:00Z",
                           retry_fingerprint="transport.stream-closed:ab12cd")
    assert projection.operational_state(snap, live_actions=[transient]) == "RetryEligible"
    # an open breaker on the same intent means retrying is not offered
    assert projection.operational_state(snap, live_actions=[transient], breaker_open=True) == "CircuitOpen"

    deterministic = replace(transient, retry_fingerprint="guard.refusing-to-record:99aa11")
    assert projection.operational_state(snap, live_actions=[deterministic]) == "Failed"
    # an unclassifiable failure is treated as deterministic: never invite a replay Studio cannot vouch for
    unknown = replace(transient, retry_fingerprint=None)
    assert projection.operational_state(snap, live_actions=[unknown]) == "Failed"
    # the *newest* action decides
    older = ActionStub(type="run", status="Failed", updated_at="2026-09-04T08:00:00Z",
                       retry_fingerprint="guard.x:1", action_id="a_0000000000000002")
    assert projection.operational_state(snap, live_actions=[older, transient]) == "RetryEligible"


def test_rule_11_paused_and_rule_12_idle(projection, repo_record, burst_repo):
    snap = projection.snapshot(repo_record(burst_repo), "default", GATE_DIR)
    assert projection.operational_state(snap, binding=BindingStub(paused=True)) == "Paused"
    assert projection.operational_state(snap, binding=BindingStub()) == "Idle"
    # pause never hides an open gate: rule 9 is above rule 11
    gate = projection.snapshot(repo_record(burst_repo), "default", GATE_DIR)
    assert projection.operational_state(
        replace(gate, state=replace(gate.state, stages=[
            replace(row, mark="?", state="awaiting_approval") if row.slug == GATE_STAGE else row
            for row in gate.state.stages
        ])),
        binding=BindingStub(paused=True),
    ) == "WaitingForYou"


def test_every_derived_state_is_a_canonical_enum_value(projection, C, gate_snap):
    seen = {
        projection.operational_state(gate_snap),
        projection.operational_state(gate_snap, binding=BindingStub(archive_state="archived")),
        projection.operational_state(gate_snap, breaker_open=True),
        projection.operational_state(gate_snap, host=SessionStub(running=True)),
        projection.operational_state(gate_snap, findings=[FindingStub("dangling_cursor", "blocking")]),
        projection.operational_state(gate_snap, live_actions=[ActionStub(type="run", status="Queued")]),
    }
    assert seen <= set(C.INTENT_STATE)


# --------------------------------------------------------------------------- #
# card derivation
# --------------------------------------------------------------------------- #


def _seeds(projection, snap, **over):
    kwargs = dict(findings=[], binding=None, install=None, breakers=[], live_actions=[])
    kwargs.update(over)
    return projection.derive_cards(snap, **kwargs)


def _by_type(seeds):
    return {seed.type: seed for seed in seeds}


def test_gate_and_question_cards_coexist_for_one_stage(projection, gate_snap):
    """FORMAT-NOTES §7: a `[?]` row with a pending `## Q…` legitimately yields both cards."""
    seeds = _seeds(projection, gate_snap)
    assert [seed.type for seed in seeds] == ["gate", "question"]
    gate, question = seeds
    assert gate.severity == "blocking" and gate.risk_class == "human_lane"
    assert gate.stage == GATE_STAGE
    assert gate.decisions == ("approve", "request_changes")
    assert gate.headline_key == "action.gate.headline"
    assert gate.consequence_key == "action.gate.consequence.approve_unlocks"
    assert gate.waiting_since == "2026-09-04T09:00:03Z"       # the STAGE_AWAITING_APPROVAL row, not now
    assert question.decisions == ("answers", "confirm_summary")


def test_a_version_2_directive_still_carries_the_question_card_of_a_per_unit_stage(
    projection, repo_record, repo_builder
):
    """2.7.1 writes `version: 2` markers; refusing them made the pending question invisible.

    The blast radius is only cosmetic while the marker names the stage the state file already names. The
    load-bearing case is this one: a per-unit Construction stage under way while `Current Stage` still
    says something else. With no marker the questions file is looked for at `Current Stage`, is not there,
    and `snap.questions` is `None` — so no question or plan-approval card is derived at all and the intent
    reads Idle while a person is being waited on.
    """
    state = F.state_text(
        marks={"code-generation": "-"},
        fields={"Current Stage": "build-and-test", "Status": "Running",
                "Lifecycle Phase": "CONSTRUCTION"},
    )
    root = (
        _builder(repo_builder, "directive-v2")
        .with_engine("payload")
        .with_workspace()
        .with_intent(
            GATE_DIR,
            state=state,
            audit=F.audit_text(
                F.audit_block("STAGE_STARTED", "2026-09-04T09:00:02Z", Stage="code-generation")
            ),
            directive={"version": 2, "kind": "run-stage", "stage": "code-generation",
                       "unit": "authentication", "state_sha256": "AUTO"},
            questions={
                "construction/authentication/code-generation/code-generation-questions.md":
                    F.blank_answers(F.real_questions())
            },
        )
        .build()
    )
    snap = projection.snapshot(repo_record(root), "default", GATE_DIR)
    assert snap.directive is not None
    assert (snap.directive.version, snap.directive.kind) == (2, "run-stage")
    assert snap.directive.matches_state is True
    assert snap.stage == "code-generation"                 # the marker outranks `Current Stage`
    assert snap.questions is not None and snap.questions.pending_count > 0
    seeds = _seeds(projection, snap)
    assert [seed.type for seed in seeds] == ["question"]
    assert seeds[0].stage == "code-generation"
    assert "answers" in seeds[0].decisions
    assert projection.detail(snap).active_directive_stage == "code-generation"


def test_dedupe_key_shape_is_type_repo_intent_stage_token(projection, proj_mod, gate_snap):
    gate = _by_type(_seeds(projection, gate_snap))["gate"]
    parts = gate.dedupe_key.split(":")
    assert len(parts) == 5
    assert parts[:4] == ["gate", REPO_ID, GATE_DIR, GATE_STAGE]
    assert parts[4] == projection.captured(gate_snap, GATE_STAGE)["boundary_token"]
    assert gate.dedupe_key == proj_mod.dedupe_key("gate", REPO_ID, GATE_DIR, GATE_STAGE, parts[4])
    # a stageless card uses "-" for the stage segment
    assert proj_mod.dedupe_key("install_conflict", REPO_ID, GATE_DIR, None, None) == \
        f"install_conflict:{REPO_ID}:{GATE_DIR}:-:-"


def test_dedupe_key_follows_the_boundary(projection, repo_record, gate_repo):
    before = _by_type(_seeds(projection, projection.snapshot(repo_record(gate_repo), "default", GATE_DIR)))["gate"]
    state = Path(gate_repo) / "aidlc/spaces/default/intents" / GATE_DIR / "aidlc-state.md"
    state.write_text(state.read_text().replace("**Revision Count**: 0", "**Revision Count**: 1"))
    after = _by_type(_seeds(projection, projection.snapshot(repo_record(gate_repo), "default", GATE_DIR)))["gate"]
    assert before.dedupe_key != after.dedupe_key


def test_accept_as_is_is_offered_only_from_the_fourth_attempt(projection, repo_record, repo_builder, C):
    """C29 / review P24: the conductor adds `Accept as-is` after three rejection cycles."""
    def build(revisions: int) -> Any:
        audit = F.gate_open_audit(GATE_STAGE) + "".join(
            F.audit_block("STAGE_REVISING", f"2026-09-04T09:1{i}:00Z", Stage=GATE_STAGE,
                          Revision_count=str(i + 1))
            for i in range(revisions)
        )
        root = (
            _builder(repo_builder, f"attempt-{revisions}")
            .with_engine("payload")
            .with_workspace()
            .with_intent(GATE_DIR, state=_gate_state(), audit=audit)
            .build()
        )
        return projection.snapshot(repo_record(root), "default", GATE_DIR)

    third = _by_type(_seeds(projection, build(2)))["gate"]
    assert third.captured["stage_attempt"] == 3
    assert "accept_as_is" not in third.decisions

    fourth = _by_type(_seeds(projection, build(3)))["gate"]
    assert fourth.captured["stage_attempt"] == C.ACCEPT_AS_IS_MIN_ATTEMPT == 4
    assert fourth.decisions == ("approve", "request_changes", "accept_as_is")


def test_revision_card_is_informational(projection, repo_record, revising_repo):
    seeds = _seeds(projection, projection.snapshot(repo_record(revising_repo), "default", GATE_DIR))
    revision = _by_type(seeds)["revision"]
    assert revision.severity == "info"
    assert revision.decisions == ()                       # nothing to decide (C18)
    assert revision.risk_class == "read"
    assert revision.waiting_since == "2026-09-04T09:00:11Z"


def test_question_card_decisions_follow_the_pending_checkpoint(projection, repo_record, repo_builder):
    cases = {
        "summary_confirmation": (
            "# Q\n\n## Consolidated Summary Confirmation\n\n- Looks correct\n- Request changes\n\n[Answer]:\n",
            ("confirm_summary",),
        ),
        "plan_approval": (
            "# Q\n\n## Plan Approval\n\n- Approve Plan\n- Request Changes\n\n[Answer]:\n",
            ("approve_plan", "request_plan_changes"),
        ),
    }
    for checkpoint, (text, expected) in cases.items():
        root = (
            _builder(repo_builder, checkpoint)
            .with_engine("payload")
            .with_workspace()
            .with_intent(
                GATE_DIR,
                state=F.state_text(
                    marks={GATE_STAGE: "-"},
                    fields={"Current Stage": GATE_STAGE, "Status": "Running", "Lifecycle Phase": "INCEPTION"},
                ),
                audit=F.audit_text(F.audit_block("STAGE_STARTED", "2026-09-04T09:00:00Z", Stage=GATE_STAGE)),
                questions={QUESTIONS_REL: text},
            )
            .build()
        )
        snap = projection.snapshot(repo_record(root), "default", GATE_DIR)
        question = _by_type(_seeds(projection, snap))["question"]
        assert question.decisions == expected, checkpoint
        assert question.reason == checkpoint
        assert question.headline_key == f"action.question.{checkpoint}.headline"
        assert question.consequence_key == f"action.question.consequence.{checkpoint}"
        assert question.dedupe_key.endswith(snap.questions.sha256)


def test_blank_question_group_yields_one_atomic_card(projection, repo_record, question_repo):
    """FR-Q-002/D13: one group is one card, never one card per question."""
    snap = projection.snapshot(repo_record(question_repo), "default", GATE_DIR)
    seeds = _seeds(projection, snap)
    assert [seed.type for seed in seeds] == ["question"]
    question = seeds[0]
    assert question.decisions == ("answers",)
    assert question.headline_params["pending"] == 6
    assert question.evidence["questions"]["pending_count"] == 6
    assert len(question.evidence["questions"]["questions"]) == 6
    # the boundary time is the file, never "now" (C20)
    assert question.waiting_since != snap.taken_at


def test_question_card_from_a_host_needs_input_signal(projection, repo_record, repo_builder):
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(
            GATE_DIR,
            state=F.state_text(
                marks={GATE_STAGE: "-"},
                fields={"Current Stage": GATE_STAGE, "Status": "Running", "Lifecycle Phase": "INCEPTION"},
            ),
            audit=F.audit_text(F.audit_block("STAGE_STARTED", "2026-09-04T09:00:00Z", Stage=GATE_STAGE)),
            questions={QUESTIONS_REL: "# Q\n\n## Q1. Which?\n\n- A. One\n\n[Answer]: A\n"},
        )
        .build()
    )
    snap = projection.snapshot(repo_record(root), "default", GATE_DIR)
    assert snap.questions is not None and snap.questions.pending_count == 0
    assert _seeds(projection, snap) == []
    seeds = _seeds(projection, snap, slot=SlotStub(needs_input=True))
    assert [seed.type for seed in seeds] == ["question"]
    assert seeds[0].reason == "needs_input"


def test_missing_input_card_for_a_dangling_cursor_is_admin_lane_and_space_scoped(
    projection, repo_record, dangling_repo
):
    """C22: picking the active intent is an engine verb under an admin lease, never a prompt."""
    snap = projection.snapshot(repo_record(dangling_repo), "default", GATE_DIR)
    seeds = _by_type(_seeds(projection, snap))
    card = seeds["missing_input"]
    assert card.decisions == ("pick_intent",)
    assert card.risk_class == "admin"
    assert card.stage is None
    assert card.reason == "dangling_cursor"
    # space-scoped key: every intent in the space derives it, the live-dedupe index collapses them
    assert card.dedupe_key == f"missing_input:{REPO_ID}:default~*:-:dangling_cursor"


def test_missing_input_card_for_an_empty_scope_is_human_lane(projection, repo_record, repo_builder):
    state = F.state_text(marks={GATE_STAGE: "-"}, fields={"Scope": "", "Current Stage": GATE_STAGE})
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(GATE_DIR, state=state, audit=F.gate_open_audit(GATE_STAGE))
        .build()
    )
    snap = projection.snapshot(repo_record(root), "default", GATE_DIR)
    card = _by_type(_seeds(projection, snap))["missing_input"]
    assert card.decisions == ("provide_input",)
    assert card.risk_class == "human_lane"
    assert card.reason == "missing_scope"
    assert card.dedupe_key == f"missing_input:{REPO_ID}:{GATE_DIR}:-:missing_scope"


def test_recovery_card_is_one_card_for_every_blocking_finding(projection, gate_snap):
    seeds = _by_type(
        _seeds(
            projection,
            gate_snap,
            findings=[
                FindingStub("gate_without_audit_row", "blocking"),
                FindingStub("state_version_unsupported", "blocking"),
                FindingStub("receipt_drift", "warn"),
            ],
        )
    )
    card = seeds["recovery"]
    assert card.severity == "critical"
    assert card.risk_class == "studio_only"
    assert card.decisions == ("rebind_session", "mark_not_delivered", "acknowledge")
    assert card.evidence["recovery_codes"] == ["gate_without_audit_row", "state_version_unsupported"]
    assert card.reason == "gate_without_audit_row"


def test_recovery_card_skips_the_two_findings_that_already_have_a_card(projection, gate_snap):
    """`delivery_uncertain_action` IS the uncertain action; `lease_conflict` clears itself."""
    seeds = _seeds(
        projection,
        gate_snap,
        findings=[FindingStub("delivery_uncertain_action", "blocking"), FindingStub("lease_conflict", "blocking")],
    )
    assert "recovery" not in _by_type(seeds)


def test_recovery_card_for_a_session_lost_mid_stage(projection, repo_record, burst_repo):
    snap = projection.snapshot(repo_record(burst_repo), "default", GATE_DIR)
    bound = BindingStub(slot_key="slot-1", session_key="dashboard:slot-1")
    lost = _by_type(_seeds(projection, snap, binding=bound, slot=None))
    assert lost["recovery"].evidence["recovery_codes"] == ["session_lost_mid_stage"]
    alive = _by_type(_seeds(projection, snap, binding=bound, slot=SlotStub()))
    assert "recovery" not in alive
    unbound = _by_type(_seeds(projection, snap, binding=BindingStub(), slot=None))
    assert "recovery" not in unbound          # not bound is not lost


def test_no_seed_is_derived_for_an_uncertain_delivery(projection, gate_snap, proj_mod):
    """The card IS that action, re-typed for the queue — a seed would duplicate a live decision."""
    seeds = _seeds(
        projection,
        gate_snap,
        live_actions=[ActionStub(status="DeliveryUncertain")],
        findings=[FindingStub("delivery_uncertain_action", "blocking")],
    )
    assert "delivery_uncertain" not in _by_type(seeds)
    assert proj_mod.queue_type_for("gate", "DeliveryUncertain") == "delivery_uncertain"
    assert proj_mod.queue_type_for("gate", "ReconciliationRequired") == "delivery_uncertain"
    assert proj_mod.queue_type_for("gate", "Queued") == "gate"


def test_failure_card_from_an_error_burst(projection, repo_record, burst_repo):
    snap = projection.snapshot(repo_record(burst_repo), "default", GATE_DIR)
    card = _by_type(_seeds(projection, snap))["failure"]
    assert card.severity == "attention"
    assert card.decisions == ("retry_now", "keep_paused")
    assert card.reason == "error_burst"
    assert card.waiting_since == "2026-09-04T09:00:00Z"
    assert card.evidence["error_burst_at"] == "2026-09-04T09:00:00Z"


def test_isolated_error_rows_never_raise_a_failure_card(projection, repo_record, repo_builder):
    """FORMAT-NOTES §6: single `ERROR_LOGGED` rows are the conductor correcting itself."""
    audit = F.audit_text(
        F.audit_block("STAGE_STARTED", "2026-09-04T08:00:00Z", Stage=GATE_STAGE),
        F.audit_block("ERROR_LOGGED", "2026-09-04T08:10:00Z", Tool="aidlc-state.ts",
                      Error="Unknown subcommand: set-status."),
        F.audit_block("ERROR_LOGGED", "2026-09-04T09:00:00Z", Tool="aidlc-state.ts",
                      Error="Unknown subcommand: set-status."),
    )
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(
            GATE_DIR,
            state=F.state_text(marks={GATE_STAGE: "-"},
                               fields={"Current Stage": GATE_STAGE, "Status": "Running"}),
            audit=audit,
        )
        .build()
    )
    snap = projection.snapshot(repo_record(root), "default", GATE_DIR)
    assert "failure" not in _by_type(_seeds(projection, snap))


def test_failure_card_from_a_deterministic_failed_action_only(projection, repo_record, revising_repo):
    snap = projection.snapshot(repo_record(revising_repo), "default", GATE_DIR)
    deterministic = ActionStub(type="run", status="Failed", updated_at="2026-09-04T09:20:00Z",
                              resolved_at="2026-09-04T09:20:00Z",
                              retry_fingerprint="guard.refusing-to-record:99aa11")
    card = _by_type(_seeds(projection, snap, live_actions=[deterministic]))["failure"]
    assert card.reason == "action_failed"
    assert card.related_action_id == deterministic.action_id
    assert card.waiting_since == "2026-09-04T09:20:00Z"
    transient = replace(deterministic, retry_fingerprint="transport.stream-closed:ab12cd")
    assert "failure" not in _by_type(_seeds(projection, snap, live_actions=[transient]))


def test_circuit_breaker_card_is_keyed_on_the_fingerprint(projection, gate_snap):
    card = _by_type(_seeds(projection, gate_snap, breakers=[BreakerStub()]))["circuit_breaker"]
    assert card.severity == "attention"
    assert card.decisions == ("retry_now", "keep_paused")
    assert card.waiting_since == "2026-09-04T09:30:00Z"
    assert card.dedupe_key.endswith(":transport.stream-closed:ab12cd")
    assert card.evidence["breaker"]["fingerprint_class"] == "transport"
    # a closed breaker row raises nothing
    assert "circuit_breaker" not in _by_type(
        _seeds(projection, gate_snap, breakers=[replace(BreakerStub(), opened_at=None, count=1)])
    )


def test_install_conflict_card_is_navigation_only(projection, gate_snap):
    card = _by_type(_seeds(projection, gate_snap, install=InstallStub()))["install_conflict"]
    assert card.decisions == ()
    assert card.severity == "attention"        # nothing was written (FR-ACT-008)
    assert card.risk_class == "read"
    assert card.stage is None
    assert card.evidence["install"]["status"] == "recovery_required"
    assert "install_conflict" not in _by_type(
        _seeds(projection, gate_snap, install=replace(InstallStub(), status="installed"))
    )


def test_budget_stop_is_never_derived(projection, gate_snap, proj_mod, C):
    """C16: reserved so the UI template set is stable; no unattended turns exist in v1."""
    seeds = _seeds(
        projection,
        gate_snap,
        breakers=[BreakerStub()],
        install=InstallStub(),
        findings=[FindingStub("dangling_cursor", "blocking")],
        live_actions=[ActionStub(type="run", status="Failed", retry_fingerprint="guard.x:1")],
    )
    assert "budget_stop" not in _by_type(seeds)
    assert "budget_stop" in proj_mod.PRIORITY_GROUP and "budget_stop" in C.ACTION_TYPE


def test_every_seed_type_severity_and_risk_class_is_a_canonical_enum(projection, gate_snap, C):
    seeds = _seeds(
        projection,
        gate_snap,
        breakers=[BreakerStub()],
        install=InstallStub(),
        findings=[FindingStub("gate_without_audit_row", "blocking")],
        live_actions=[ActionStub(type="run", status="Failed", retry_fingerprint="guard.x:1")],
    )
    assert {s.type for s in seeds} == {"recovery", "gate", "question", "failure", "circuit_breaker",
                                       "install_conflict"}
    for seed in seeds:
        assert seed.type in C.ACTION_TYPE
        assert seed.severity in C.SEVERITY
        assert seed.risk_class in C.RISK_CLASS
        for decision in seed.decisions:
            assert decision in C.DECISION_KIND
        assert seed.intent_uuid == "01a00000-0000-7000-8000-000000000001"
        json.dumps(seed.to_json())            # every seed must survive the SQLite json column


def test_seeds_come_back_in_queue_order(projection, gate_snap, proj_mod):
    seeds = _seeds(
        projection,
        gate_snap,
        breakers=[BreakerStub()],
        install=InstallStub(),
        findings=[FindingStub("gate_without_audit_row", "blocking")],
    )
    groups = [proj_mod.priority_group_for(seed.type) for seed in seeds]
    assert groups == sorted(groups)
    assert seeds[0].type == "recovery"


def test_a_card_carries_the_snapshot_evidence_it_was_derived_from(projection, gate_snap):
    seed = _by_type(_seeds(projection, gate_snap))["gate"]
    assert seed.evidence["state"]["sha256"] == gate_snap.state_sha256
    assert seed.captured["evidence_digest"] == projection.captured(gate_snap, GATE_STAGE)["evidence_digest"]
    assert {a["relpath"] for a in seed.evidence["artifacts"]} == {a.relpath for a in gate_snap.stage_artifacts}
    assert seed.stage_ref == {"slug": GATE_STAGE, "number": "2.3", "name": "Requirements Analysis",
                              "phase": "inception", "unit": None}


# --------------------------------------------------------------------------- #
# sort_actions
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CardStub:
    action_id: str
    queue_type: str
    severity: str
    waiting_since: str
    repo: dict


def _queue() -> list[CardStub]:
    """One card per priority band, deliberately inserted in the wrong order."""
    zed = {"repo_id": "r_z", "label": "Zulu", "canonical_path": "/z"}
    alfa = {"repo_id": "r_a", "label": "alfa", "canonical_path": "/a"}
    return [
        CardStub("a_08", "run", "info", "2026-09-04T08:00:00Z", zed),
        CardStub("a_07", "revision", "info", "2026-09-04T07:00:00Z", alfa),
        CardStub("a_06", "install_conflict", "attention", "2026-09-04T06:00:00Z", zed),
        CardStub("a_05", "failure", "attention", "2026-09-04T05:00:00Z", alfa),
        CardStub("a_04", "question", "blocking", "2026-09-04T04:00:00Z", zed),
        CardStub("a_03", "gate", "blocking", "2026-09-04T03:00:00Z", alfa),
        CardStub("a_02", "delivery_uncertain", "critical", "2026-09-04T02:00:00Z", zed),
        CardStub("a_01", "recovery", "critical", "2026-09-04T01:00:00Z", alfa),
    ]


def test_sort_actions_priority_golden_order(projection):
    order = [card.action_id for card in projection.sort_actions(_queue(), "priority")]
    assert order == ["a_01", "a_02", "a_03", "a_04", "a_05", "a_06", "a_07", "a_08"]


def test_sort_actions_equal_priority_is_oldest_first_then_action_id(projection):
    cards = [
        CardStub("a_bb", "gate", "blocking", "2026-09-04T03:00:00Z", {"repo_id": "r", "label": "r"}),
        CardStub("a_aa", "gate", "blocking", "2026-09-04T03:00:00Z", {"repo_id": "r", "label": "r"}),
        CardStub("a_cc", "gate", "blocking", "2026-09-04T01:00:00Z", {"repo_id": "r", "label": "r"}),
    ]
    assert [c.action_id for c in projection.sort_actions(cards, "priority")] == ["a_cc", "a_aa", "a_bb"]


def test_sort_actions_repo_mode_is_label_case_insensitive_then_priority(projection):
    order = [card.action_id for card in projection.sort_actions(_queue(), "repo")]
    assert order == ["a_01", "a_03", "a_05", "a_07", "a_02", "a_04", "a_06", "a_08"]


def test_sort_actions_type_mode_groups_by_band_then_type_name(projection):
    order = [card.queue_type for card in projection.sort_actions(_queue(), "type")]
    assert order == ["delivery_uncertain", "recovery", "gate", "question", "failure",
                     "install_conflict", "revision", "run"]


def test_sort_actions_oldest_mode_ignores_priority(projection):
    order = [card.action_id for card in projection.sort_actions(_queue(), "oldest")]
    assert order == ["a_01", "a_02", "a_03", "a_04", "a_05", "a_06", "a_07", "a_08"]
    cards = list(reversed(_queue()))
    assert [c.action_id for c in projection.sort_actions(cards, "oldest")] == order


def test_sort_actions_does_not_mutate_its_input(projection):
    cards = _queue()
    before = list(cards)
    projection.sort_actions(cards, "priority")
    assert cards == before


def test_sort_actions_refuses_an_unknown_organize(projection, studio):
    with pytest.raises(studio.errors.StudioError) as exc:
        projection.sort_actions(_queue(), "whatever")
    assert exc.value.code == "bad_param"
    assert exc.value.status == 400


def test_group_key_per_organize_mode(projection):
    card = _queue()[0]                       # a run card in band 4, repo r_z
    assert projection.group_key(card, "priority") == "group.4"
    assert projection.group_key(card, "repo") == "r_z"
    assert projection.group_key(card, "type") == "run"
    assert projection.group_key(card, "oldest") == "oldest"


def test_priority_group_table_covers_every_action_type(proj_mod, C):
    assert set(proj_mod.PRIORITY_GROUP) == set(C.ACTION_TYPE)
    assert set(proj_mod.PRIORITY_GROUP.values()) == {1, 2, 3, 4}
    assert set(proj_mod.SEVERITY_RANK) == set(C.SEVERITY)


# --------------------------------------------------------------------------- #
# map model
# --------------------------------------------------------------------------- #


def test_map_model_shape_and_counts(projection, repo_record, gate_repo, C):
    snap = projection.snapshot(repo_record(gate_repo), "default", GATE_DIR)
    model = projection.map(snap)
    assert model.intent_key == GATE_DIR
    assert [phase.phase for phase in model.phases] == list(C.PHASES)
    assert model.stage_count == 33
    assert model.graph_version == C.BUNDLED_ENGINE_VERSION
    assert model.counts["stages_known"] == 33
    total = sum(phase.counts["total"] for phase in model.phases)
    assert total == 33
    body = model.to_json()
    assert set(body) == {"intent_key", "phases", "counts", "units", "graph_version", "stage_count"}
    assert set(body["phases"][0]) == {"phase", "status", "stages", "counts"}


def test_map_stage_joins_graph_and_state_row(projection, repo_record, gate_repo):
    model = projection.map(projection.snapshot(repo_record(gate_repo), "default", GATE_DIR))
    stages = {stage.slug: stage for phase in model.phases for stage in phase.stages}
    gate = stages[GATE_STAGE]
    assert gate.number == "2.3"
    assert gate.name == "Requirements Analysis"
    assert gate.phase == "inception"
    assert gate.state == "awaiting_approval"
    assert gate.in_scope is True
    assert gate.gate is True
    assert gate.is_current is True
    assert gate.is_directive is True
    assert "requirements.md" in {a.name for a in gate.artifacts}
    assert gate.summary_confirmation == "required"
    # initialization stages never gate (FORMAT-NOTES §4: the graph has no `gate` key)
    assert stages["workspace-scaffold"].gate is False


def test_map_dependencies_are_bidirectional(projection, repo_record, gate_repo):
    model = projection.map(projection.snapshot(repo_record(gate_repo), "default", GATE_DIR))
    stages = {stage.slug: stage for phase in model.phases for stage in phase.stages}
    assert "workspace-scaffold" in stages["workspace-detection"].depends_on
    assert "workspace-detection" in stages["workspace-scaffold"].dependents


def test_map_skipped_reason_from_row_suffix_and_from_stages_to_skip(projection, repo_record, repo_builder):
    """Both spellings are real (FORMAT-NOTES §3)."""
    state = F.state_text(
        F.DEVDELTA_REMEDIATION,
        marks={GATE_STAGE: "?"},
        fields={"Current Stage": GATE_STAGE},
    )
    state = state.replace(
        "- **Stages to Skip**: 1.1 (intent-capture)",
        "- **Stages to Skip**: 1.1 (intent-capture — greenfield)",
    )
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(GATE_DIR, state=state, audit=F.gate_open_audit(GATE_STAGE))
        .build()
    )
    model = projection.map(projection.snapshot(repo_record(root), "default", GATE_DIR))
    stages = {stage.slug: stage for phase in model.phases for stage in phase.stages}
    assert stages["market-research"].in_scope is False
    assert stages["market-research"].skipped_reason is None       # suffix is a bare "SKIP"
    assert stages["intent-capture"].skipped_reason == "greenfield"
    assert stages[GATE_STAGE].in_scope is True


def test_map_marks_a_graph_stage_the_plan_never_selected_as_excluded(
    projection, repo_record, repo_builder
):
    """A v7 record (32 rows) read against the bundled 33-stage graph — the real drift case."""
    state = F.state_text(F.PAYLOAD_GATE_OPEN, tree=F.PAYLOAD_230)
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(GATE_DIR, state=state, audit=F.gate_open_audit(GATE_STAGE))
        .build()
    )
    model = projection.map(projection.snapshot(repo_record(root), "default", GATE_DIR))
    stages = {stage.slug: stage for phase in model.phases for stage in phase.stages}
    assert stages["contract-design"].state == "excluded"   # in the 33-graph, not in a v7 state file
    assert stages["contract-design"].in_scope is False
    assert "application-design" in stages                 # the v7 row the 33-graph does not know
    assert stages["application-design"].number == ""


def test_map_per_unit_stages_expand_into_sub_rows(projection, repo_record, repo_builder):
    """The real per-unit Construction layout (FORMAT-NOTES §8)."""
    state = F.state_text(
        marks={"code-generation": "-"},
        fields={"Current Stage": "code-generation", "Status": "Running", "Lifecycle Phase": "CONSTRUCTION"},
    )
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(
            GATE_DIR,
            state=state,
            audit=F.audit_text(F.audit_block("STAGE_STARTED", "2026-09-04T09:00:00Z", Stage="code-generation")),
            artifacts={
                "construction/ledger-export/code-generation/code-generation-plan.md": "# Plan\n",
                "construction/build-and-test/build-test-results.md": "# Results\n",
            },
        )
        .build()
    )
    snap = projection.snapshot(repo_record(root), "default", GATE_DIR)
    assert snap.units == ("ledger-export",)
    model = projection.map(snap)
    stages = {stage.slug: stage for phase in model.phases for stage in phase.stages}
    codegen = stages["code-generation"]
    assert codegen.per_unit is True
    assert [unit.unit for unit in codegen.units] == ["ledger-export"]
    assert {a.name for a in codegen.units[0].artifacts} == {"code-generation-plan.md"}
    # a once-per-workflow construction stage has no unit sub-rows
    assert stages["build-and-test"].per_unit is False
    assert stages["build-and-test"].units == ()
    assert {a.name for a in stages["build-and-test"].artifacts} == {"build-test-results.md"}
    assert model.units == ("ledger-export",)


def test_map_elapsed_measures_the_latest_attempt(projection, repo_record, repo_builder, clock):
    audit = F.audit_text(
        F.audit_block("STAGE_STARTED", "2026-09-04T08:00:00Z", Stage=GATE_STAGE),
        F.audit_block("STAGE_COMPLETED", "2026-09-04T08:10:00Z", Stage=GATE_STAGE),
        F.audit_block("STAGE_STARTED", "2026-09-04T09:00:00Z", Stage=GATE_STAGE),
        F.audit_block("STAGE_AWAITING_APPROVAL", "2026-09-04T09:00:03Z", Stage=GATE_STAGE),
    )
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(GATE_DIR, state=_gate_state(), audit=audit)
        .build()
    )
    model = projection.map(projection.snapshot(repo_record(root), "default", GATE_DIR))
    stages = {stage.slug: stage for phase in model.phases for stage in phase.stages}
    # the stage is still open, so it is measured to now (10:00) from the LATEST start (09:00)
    assert stages[GATE_STAGE].elapsed_secs == 3600
    assert stages["workspace-scaffold"].elapsed_secs is None


@pytest.mark.parametrize("zone", ["America/Los_Angeles", "Europe/Berlin", "UTC"])
def test_map_elapsed_is_the_same_in_a_dst_timezone(
    projection, repo_record, repo_builder, monkeypatch, request, zone
):
    """An audit timestamp is UTC; converting it must not go through local time.

    The obvious local spelling (``mktime`` minus ``time.timezone``) is an hour off for half the year in
    any DST zone, which showed here as "running for 2h" two minutes after a stage started. The machine
    this suite is usually run on has no DST, so the zone is forced.
    """
    import time as time_module

    request.addfinalizer(time_module.tzset)   # runs after monkeypatch restores TZ in the environment
    monkeypatch.setenv("TZ", zone)
    time_module.tzset()

    audit = F.audit_text(F.audit_block("STAGE_STARTED", "2026-09-04T09:00:00Z", Stage=GATE_STAGE))
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(GATE_DIR, state=_gate_state(), audit=audit)
        .build()
    )
    model = projection.map(projection.snapshot(repo_record(root), "default", GATE_DIR))
    stages = {stage.slug: stage for phase in model.phases for stage in phase.stages}
    assert stages[GATE_STAGE].elapsed_secs == 3600


def test_map_phase_status_comes_from_the_state_file(projection, repo_record, gate_repo):
    model = projection.map(projection.snapshot(repo_record(gate_repo), "default", GATE_DIR))
    status = {phase.phase: phase.status for phase in model.phases}
    assert status["initialization"] == "Verified"
    assert set(status) == set(("initialization", "ideation", "inception", "construction", "operation"))


def test_map_of_an_unreadable_state_is_empty_but_valid(projection, repo_record, missing_repo):
    model = projection.map(
        projection.snapshot(repo_record(missing_repo, availability="unavailable"), "default", GATE_DIR)
    )
    assert model.phases == ()
    assert model.counts == {"stages_known": 0, "stages_selected": 0, "gates": 0}
    json.dumps(model.to_json())


# --------------------------------------------------------------------------- #
# artifacts
# --------------------------------------------------------------------------- #


def test_artifacts_listing_ids_and_kinds(projection, repo_record, gate_repo):
    import hashlib

    snap = projection.snapshot(repo_record(gate_repo), "default", GATE_DIR)
    metas, truncated = projection.artifacts(snap)
    assert truncated is False
    by_name = {meta.name: meta for meta in metas}
    assert "requirements.md" in by_name and f"{GATE_STAGE}-questions.md" in by_name
    assert by_name[f"{GATE_STAGE}-questions.md"].kind == "questions"
    assert by_name["requirements.md"].kind == "artifact"
    assert by_name["requirements.md"].stage == GATE_STAGE
    assert by_name["requirements.md"].phase == "inception"
    assert by_name["requirements.md"].artifact_id == hashlib.sha1(
        by_name["requirements.md"].relpath.encode("utf-8")
    ).hexdigest()
    assert "aidlc-state.md" in by_name        # the record's own files are listed too


def test_artifacts_truncation_is_reported(projection, repo_record, gate_repo, monkeypatch, studio):
    """A partial walk must never be presented as a complete record."""
    monkeypatch.setattr(studio.constants, "MAX_ARTIFACTS_PER_INTENT", 1)
    snap = projection.snapshot(repo_record(gate_repo), "default", GATE_DIR)
    metas, truncated = projection.artifacts(snap)
    assert truncated is True
    assert len(metas) == 1
    detail = projection.detail(snap)
    assert detail.artifacts_truncated is True


# --------------------------------------------------------------------------- #
# summary / detail wire shapes
# --------------------------------------------------------------------------- #


SUMMARY_KEYS = {
    "repo_id", "repo_label", "space", "intent_dir", "intent_key", "uuid", "slug", "scope",
    "registry_status", "title", "operational_state", "disk", "counts", "open_actions",
    "blocking_findings", "warn_findings", "session", "archived", "paused", "keep_moving",
    "interrupted", "last_activity_at", "stable_boundary", "unstable", "is_active_cursor",
}
DETAIL_EXTRA_KEYS = {
    "state_sections", "phases", "stages", "findings", "actions", "directive", "recovery", "markers",
    "audit_tail", "questions", "artifacts_count", "artifacts_truncated", "git", "binding", "engine",
    "active_directive_stage",
}


def test_intent_summary_wire_shape(projection, gate_snap, C):
    body = projection.summarize(
        gate_snap,
        binding=BindingStub(slot_key="slot-1"),
        live_actions=[ActionStub(type="gate"), ActionStub(type="revision", action_id="a_2")],
        host=SessionStub(),
        findings=[FindingStub("receipt_drift", "warn"), FindingStub("dangling_cursor", "blocking")],
    ).to_json()
    assert set(body) == SUMMARY_KEYS
    assert set(body["disk"]) == {"status", "lifecycle_phase", "current_stage", "next_stage",
                                 "state_version", "total_stages", "completed", "revision_count",
                                 "parked_at", "last_updated"}
    assert set(body["counts"]) == {"total", "done", *C.STAGE_STATE}
    assert body["open_actions"] == 1                      # the revision card is not a queue item (C18)
    assert body["blocking_findings"] == 1 and body["warn_findings"] == 1
    assert body["session"]["slot_key"] == SessionStub().slot_key
    assert body["keep_moving"] is False                   # always false in v1 (machine lane absent)
    assert body["is_active_cursor"] is True
    # the STAGE_AWAITING_APPROVAL row, not the later HUMAN_TURN/SESSION rows (NOISE_EVENTS)
    assert body["last_activity_at"] == "2026-09-04T09:00:03Z"
    json.dumps(body)


def test_summary_last_activity_never_counts_noise(projection, repo_record, repo_builder, C):
    audit = F.audit_text(
        F.audit_block("STAGE_STARTED", "2026-09-04T08:00:00Z", Stage=GATE_STAGE),
        F.audit_block("STAGE_AWAITING_APPROVAL", "2026-09-04T08:00:03Z", Stage=GATE_STAGE),
        F.audit_block("HUMAN_TURN", "2026-09-04T09:59:00Z"),
        F.audit_block("SESSION_STARTED", "2026-09-04T09:59:30Z", Source="startup"),
    )
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(GATE_DIR, state=_gate_state(), audit=audit)
        .build()
    )
    snap = projection.snapshot(repo_record(root), "default", GATE_DIR)
    assert projection.summarize(snap).last_activity_at == "2026-09-04T08:00:03Z"


@pytest.mark.parametrize(
    "placeholder",
    ["[Project description]", "[Pasted document provided]", "[Pasted document boundary needs clarification]"],
)
def test_summary_title_drops_every_template_placeholder(
    projection, repo_record, repo_builder, proj_mod, placeholder
):
    """A birth with no usable free text leaves a bracketed template value in `- **Project**:`.

    2.7.1 added the two pasted-document forms, which a birth from a pasted brief writes when it cannot
    use the text. Any of the three in the rail would read as an intent title, so all three are dropped —
    and the set is asserted whole here so a literal added to the module has to be added to a test too.
    """
    assert proj_mod.PROJECT_PLACEHOLDERS == {
        "[Project description]",
        "[Pasted document provided]",
        "[Pasted document boundary needs clarification]",
    }
    state = F.state_text(F.DEVDELTA_REMEDIATION, marks={GATE_STAGE: "?"},
                         fields={"Current Stage": GATE_STAGE, "Project": placeholder})
    assert f"- **Project**: {placeholder}" in state
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(GATE_DIR, state=state, audit=F.gate_open_audit(GATE_STAGE))
        .build()
    )
    summary = projection.summarize(projection.snapshot(repo_record(root), "default", GATE_DIR))
    assert summary.title is None
    # and the state file's current scope wins over the registry's birth-time one (FORMAT-NOTES §2)
    assert summary.scope == "review-major-remediation"
    assert summary.registry_status == "in-flight"


def test_summary_title_is_capped(projection, gate_snap, proj_mod):
    title = projection.summarize(gate_snap).title
    assert title and len(title) == proj_mod.MAX_TITLE_CHARS


def test_intent_detail_is_a_superset_of_the_summary(projection, gate_snap, C):
    detail = projection.detail(
        gate_snap,
        binding=BindingStub(slot_key="slot-1"),
        findings=[FindingStub("directive_state_digest_mismatch", "info")],
        actions=[],
    )
    body = detail.to_json()
    assert set(body) == SUMMARY_KEYS | DETAIL_EXTRA_KEYS
    assert body["active_directive_stage"] == GATE_STAGE
    assert body["engine"]["engine_version"] == C.BUNDLED_ENGINE_VERSION
    assert len(body["stages"]) == 33
    assert body["phases"][0] == ["Initialization", "Verified"]
    assert body["questions"]["stage"] == GATE_STAGE
    assert body["artifacts_count"] > 0
    assert body["findings"][0]["code"] == "directive_state_digest_mismatch"
    assert set(body["markers"]) == {"human_turn_at", "engine_touch_at", "turn_counter",
                                   "goal_stop_present", "recovery", "hooks_health", "compose_pending"}
    assert len(body["audit_tail"]) == 4
    assert set(body["audit_tail"][0]) == {"shard", "pos", "timestamp", "event", "fields", "raw"}
    json.dumps(body)


def test_intent_detail_audit_tail_is_capped(projection, repo_record, repo_builder, proj_mod):
    audit = F.audit_text(
        *[F.audit_block("HUMAN_TURN", f"2026-09-04T09:{i // 60:02d}:{i % 60:02d}Z") for i in range(80)]
    )
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(GATE_DIR, state=_gate_state(), audit=audit)
        .build()
    )
    detail = projection.detail(projection.snapshot(repo_record(root), "default", GATE_DIR))
    assert len(detail.audit_tail) == proj_mod.AUDIT_TAIL_LIMIT == 50


def test_active_directive_stage_is_none_when_the_digest_is_stale(projection, repo_record, repo_builder):
    """C06: the engine treats a mismatched marker as absent, so Studio must not show it as live."""
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(
            GATE_DIR,
            state=_gate_state(),
            audit=F.gate_open_audit(GATE_STAGE),
            directive={"version": 1, "stage": GATE_STAGE, "state_sha256": "0" * 64},
        )
        .build()
    )
    snap = projection.snapshot(repo_record(root), "default", GATE_DIR)
    assert snap.directive is not None and snap.directive.matches_state is False
    assert projection.detail(snap).active_directive_stage is None
    assert projection.evidence(snap).to_json()["directive"]["matches_state"] is False


def test_intent_key_is_space_qualified_outside_default(projection, proj_mod, repo_record, repo_builder):
    root = repo_builder.with_engine("payload").with_workspace().build()
    alpha = root / "aidlc" / "spaces" / "alpha" / "intents"
    alpha.mkdir(parents=True, exist_ok=True)
    (alpha / "intents.json").write_text("[]\n")
    root = (
        repo_builder.with_intent(
            GATE_DIR, state=_gate_state(), audit=F.gate_open_audit(GATE_STAGE), space="alpha"
        ).build()
    )
    snap = projection.snapshot(repo_record(root), "alpha", GATE_DIR)
    assert snap.intent_key == f"alpha~{GATE_DIR}" == proj_mod.intent_key_for("alpha", GATE_DIR)
    assert snap.is_active is False        # the active space is still `default`
    seed = _by_type(_seeds(projection, snap))["gate"]
    assert seed.dedupe_key.startswith(f"gate:{REPO_ID}:alpha~{GATE_DIR}:")


def test_slug_falls_back_to_the_directory_name_for_an_orphan_record(
    projection, repo_record, repo_builder
):
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(GATE_DIR, state=_gate_state(), audit=F.gate_open_audit(GATE_STAGE))
        .build()
    )
    (root / "aidlc" / "spaces" / "default" / "intents" / "intents.json").write_text("[]\n")
    summary = projection.summarize(projection.snapshot(repo_record(root), "default", GATE_DIR))
    assert summary.uuid is None
    assert summary.registry_status is None
    assert summary.slug == "gate-demo"          # the YYMMDD- prefix is not part of the name


def test_first_unreadable_at_is_carried_for_the_api(projection, repo_record, missing_repo):
    """The reconciler is the only component that has seen the previous ticks (§1.7 grace window)."""
    snap = projection.snapshot(
        repo_record(missing_repo, availability="unavailable"),
        "default",
        GATE_DIR,
        first_unreadable_at="2026-09-04T09:58:00Z",
    )
    assert snap.first_unreadable_at == "2026-09-04T09:58:00Z"
    assert snap.audit.shards == []


def test_derive_cards_on_an_unreadable_record_offers_no_human_lane_decision(
    projection, repo_record, missing_repo
):
    """Nothing readable means nothing submittable — the only card is the operator's own recovery card."""
    snap = projection.snapshot(
        repo_record(missing_repo, availability="unavailable"), "default", GATE_DIR
    )
    seeds = _seeds(projection, snap, findings=[FindingStub("state_unreadable", "blocking")])
    assert [seed.type for seed in seeds] == ["recovery", "missing_input"]
    recovery, pick = seeds
    assert recovery.decisions == ("rebind_session", "mark_not_delivered", "acknowledge")
    assert pick.decisions == ("pick_intent",)          # admin lane, not a prompt
    assert all(seed.captured["state_hash"] is None for seed in seeds)
    assert all(seed.captured["stable"] is False for seed in seeds)


def _inventory(root: Path) -> dict[str, tuple[int, str]]:
    import hashlib

    out: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            out[str(path.relative_to(root))] = (len(data), hashlib.sha256(data).hexdigest())
    return out


def test_the_read_model_never_writes_into_a_registered_repository(projection, repo_record, gate_repo):
    """Architecture §3: AI-DLC owns these files; Studio only observes them."""
    before = _inventory(Path(gate_repo))
    snap = projection.snapshot(repo_record(gate_repo), "default", GATE_DIR)
    projection.summarize(snap)
    projection.detail(snap)
    projection.map(snap)
    projection.artifacts(snap)
    projection.stable_boundary(snap)
    _seeds(projection, snap, breakers=[BreakerStub()], install=InstallStub())
    assert _inventory(Path(gate_repo)) == before


def test_an_oversized_state_file_degrades_without_claiming_an_empty_audit(
    projection, repo_record, gate_repo, monkeypatch, studio
):
    """`too_large` is the real `state_unreadable` input; it must not look like a fresh empty record."""
    monkeypatch.setattr(studio.constants, "MAX_STATE_BYTES", 16)
    snap = projection.snapshot(repo_record(gate_repo), "default", GATE_DIR)
    assert snap.state is None and snap.state_snap is None
    assert snap.stable is False
    assert snap.audit.complete is False       # nothing was read, so nothing may be claimed complete
    assert snap.audit.shards == []


def test_a_malformed_intent_dir_is_refused_rather_than_served_as_empty(
    projection, repo_record, gate_repo, studio
):
    with pytest.raises(studio.errors.StudioError) as exc:
        projection.snapshot(repo_record(gate_repo), "default", "../../etc")
    assert exc.value.code == "bad_path"


def test_map_without_an_engine_still_renders_the_state_rows(projection, repo_record, repo_builder):
    """A repo whose harness was removed still has a workflow on disk; the map must show it."""
    root = (
        repo_builder.with_workspace()
        .with_intent(GATE_DIR, state=_gate_state(), audit=F.gate_open_audit(GATE_STAGE))
        .build()
    )
    snap = projection.snapshot(repo_record(root), "default", GATE_DIR)
    assert snap.engine is None and snap.graph == ()
    model = projection.map(snap)
    stages = {stage.slug: stage for phase in model.phases for stage in phase.stages}
    assert len(stages) == 33
    assert stages[GATE_STAGE].number == ""            # unknown without the graph, never invented
    assert stages[GATE_STAGE].state == "awaiting_approval"
    assert stages[GATE_STAGE].in_scope is True
    assert model.stage_count == 33
    assert model.graph_version is None


#: §1.12 `DECISIONS` (action type → allowed decision kinds). Mirrored here so a derived card that offers
#: a decision `HumanActionBroker.submit` would refuse with `invalid_decision` fails at derivation instead
#: of at the user's click.
CONTRACT_DECISIONS = {
    "gate": ("approve", "request_changes", "accept_as_is"),
    "question": ("answers", "confirm_summary", "approve_plan", "request_plan_changes"),
    "missing_input": ("provide_input", "pick_intent"),
    "recovery": ("rebind_session", "mark_not_delivered", "acknowledge"),
    "delivery_uncertain": ("reconcile", "mark_not_delivered", "resubmit"),
    "failure": ("retry_now", "keep_paused"),
    "circuit_breaker": ("retry_now", "keep_paused"),
    "install_conflict": (),
    "budget_stop": ("run_now", "keep_paused"),
    "revision": (),
}


def test_constants_decisions_table_matches_the_contract(C):
    """``C.DECISIONS`` is the shipped table; the mirror above is the contract text read by hand.

    Pinned here because the projection now *reads* the shipped table instead of spelling decisions out,
    so without this assertion a wrong entry in ``constants.py`` would silently become "correct".
    """
    for action_type, allowed in CONTRACT_DECISIONS.items():
        assert C.DECISIONS[action_type] == allowed, action_type
    # the four command types have no derived seed, so only this test covers them
    assert {t: C.DECISIONS[t] for t in ("run", "resume", "force_stop", "prepare_commit")} == {
        "run": ("run",), "resume": ("resume",),
        "force_stop": ("force_stop",), "prepare_commit": ("prepare_commit",),
    }
    assert set(C.DECISIONS) == set(C.ACTION_TYPE)
    every = {d for row in C.DECISIONS.values() for d in row}
    assert every == set(C.DECISION_KIND)
    assert every == C.HUMAN_LANE_DECISIONS | C.HOST_CONTROL_DECISIONS | C.STUDIO_ONLY_DECISIONS


def test_no_seed_offers_a_decision_its_type_does_not_allow(
    projection, repo_record, gate_repo, revising_repo, dangling_repo, burst_repo
):
    seen: set[tuple[str, str]] = set()
    for path in (gate_repo, revising_repo, dangling_repo, burst_repo):
        snap = projection.snapshot(repo_record(path), "default", GATE_DIR)
        for seed in _seeds(
            projection,
            snap,
            findings=[FindingStub("gate_without_audit_row", "blocking")],
            breakers=[BreakerStub()],
            install=InstallStub(),
            live_actions=[ActionStub(type="run", status="Failed", retry_fingerprint="guard.x:1")],
        ):
            allowed = CONTRACT_DECISIONS[seed.type]
            assert set(seed.decisions) <= set(allowed), (seed.type, seed.decisions)
            seen.update((seed.type, d) for d in seed.decisions)
    # every decision these four repositories can offer was exercised at least once
    # (`provide_input` needs an empty `Scope`, which has its own test above)
    assert {"approve", "request_changes", "answers", "confirm_summary", "pick_intent",
            "acknowledge", "mark_not_delivered", "rebind_session", "retry_now",
            "keep_paused"} == {d for _, d in seen}


def test_the_whole_read_model_never_writes_to_the_repository(projection, repo_record, gate_repo):
    """Every public projection surface over one real repository, then a byte-for-byte tree compare.

    ``test_aidlc_reader.py`` proves the reader alone is read-only; this proves the *composition* is, and
    it is the composition a user meets — the map, the artifact list and the evidence blob each reach for
    files the reader's own test does not open. Studio never writing into a registered repository is the
    product's central promise (FR-REP-007), so it is asserted at the level the API actually calls.
    """
    def digest():
        return sorted(
            (path.relative_to(gate_repo).as_posix(), path.stat().st_size,
             F.sha256(path.read_bytes().decode("utf-8", "replace")))
            for path in gate_repo.rglob("*")
            if path.is_file() and not path.is_symlink()
        )

    before = digest()
    record = repo_record(gate_repo)
    snap = projection.snapshot(record, "default", GATE_DIR)
    findings = [FindingStub("gate_without_audit_row", "blocking")]
    projection.summarize(snap, findings=findings)
    projection.detail(snap, findings=findings)
    projection.map(snap)
    projection.artifacts(snap)
    projection.evidence(snap, findings=findings)
    projection.questions_view(snap)
    projection.stable_boundary(snap)
    projection.captured(snap, GATE_STAGE)
    _seeds(projection, snap, findings=findings, install=InstallStub(), breakers=[BreakerStub()])
    assert digest() == before


def test_a_new_question_group_never_inherits_another_stages_answer_time(
    projection, repo_record, repo_builder
):
    """`waiting_since` is what the queue sorts on and what the card tells the reader.

    `_latest_ts` used to fall back to the newest event of the requested types for ANY stage when the
    asked-for stage had none. So a question group written moments ago, on a stage with no
    `QUESTION_ANSWERED` of its own, inherited an OLD one from an earlier stage: the queue sorted it as
    though it had been waiting since then, and the detail pane said so. The truthful floor is the
    questions file's own mtime.
    """
    text = F.read(
        F.OLDER_INTENTS / F.OLDER_INTENT / "ideation" / "feasibility" / "feasibility-questions.md"
    )
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(
            GATE_DIR,
            state=F.state_text(
                marks={"feasibility": "-"},
                fields={"Current Stage": "feasibility", "Status": "Running",
                        "Lifecycle Phase": "IDEATION"},
            ),
            # An answered question on a DIFFERENT, much older stage. This is the event that used to be
            # borrowed.
            audit=F.audit_text(
                F.audit_block("QUESTION_ANSWERED", "2026-01-01T00:00:00Z", Stage="intent-capture"),
                F.audit_block("STAGE_STARTED", "2026-09-04T09:00:02Z", Stage="feasibility"),
            ),
            questions={"ideation/feasibility/feasibility-questions.md": text},
        )
        .build()
    )
    snap = projection.snapshot(repo_record(root), "default", GATE_DIR)
    question = _by_type(_seeds(projection, snap))["question"]

    assert question.waiting_since != "2026-01-01T00:00:00Z", "it borrowed another stage's clock"
    assert question.waiting_since != snap.taken_at, "and it must not reset on every scan either"
    mtimes = {a.relpath: a.mtime for a in snap.stage_artifacts}
    assert question.waiting_since == mtimes[snap.questions.relpath]


def test_sort_actions_keeps_two_repositories_with_the_same_label_in_separate_runs(projection):
    """The group key is the repo_id, so a label tie that is not broken splits a repository in two.

    A clone and a worktree of one project are commonly given the same label. Sorting on the label alone
    interleaved their cards, and because `buildGroups` splits a run-length sequence, `groups[]` came back
    with the same key twice and the UI rendered one heading with the other repository's cards under it.
    """
    one = {"repo_id": "r_aaa", "label": "checkout", "canonical_path": "/one"}
    two = {"repo_id": "r_bbb", "label": "checkout", "canonical_path": "/two"}
    cards = [
        CardStub("a_1", "gate", "blocking", "2026-09-04T01:00:00Z", one),
        CardStub("a_2", "run", "info", "2026-09-04T02:00:00Z", two),
        CardStub("a_3", "gate", "blocking", "2026-09-04T03:00:00Z", two),
        CardStub("a_4", "run", "info", "2026-09-04T04:00:00Z", one),
    ]
    ordered = projection.sort_actions(cards, "repo")
    keys = [projection.group_key(card, "repo") for card in ordered]

    # Every repository's cards are consecutive, so a run-length split yields one group per repository.
    runs = [key for index, key in enumerate(keys) if index == 0 or keys[index - 1] != key]
    assert runs == ["r_aaa", "r_bbb"], keys
    assert len(runs) == len(set(runs)), "a repo_id appeared as two separate groups"
    # And within a repository the priority band still leads.
    assert [c.action_id for c in ordered] == ["a_1", "a_4", "a_3", "a_2"]


def test_one_oversized_artifact_still_produces_a_gate_card(projection, repo_record, repo_builder, C):
    """A snapshot that raises makes the reconciler skip the intent, so the gate simply disappears.

    One produced artifact over `MAX_ARTIFACT_RENDER_BYTES` used to make `snapshot` raise `too_large`. The
    reconciler swallows that and moves on, so an on-disk `[?]` boundary produced no card at all and a
    person waited at a gate Studio had stopped reporting. A missing verdict is a gap in the evidence; a
    missing card is a gap in the product.
    """
    huge = "x" * (C.MAX_ARTIFACT_RENDER_BYTES + 1024)
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(
            GATE_DIR,
            state=_gate_state(),
            audit=F.gate_open_audit(GATE_STAGE),
            artifacts={"inception/requirements-analysis/requirements.md": huge},
        )
        .build()
    )

    snap = projection.snapshot(repo_record(root), "default", GATE_DIR)
    gate = _by_type(_seeds(projection, snap))["gate"]
    assert gate.type == "gate"

    # And the evidence says the verdict could not be read, rather than implying there is no reviewer.
    assert snap.review is None and snap.review_unreadable is True
    review = projection.evidence(snap).review
    assert review is not None, "an unreadable verdict must not read as 'no reviewer'"
    assert review["findings_parsed"] is False
    assert review["unreadable"] is True
    assert review["verdict"] is None


def test_a_second_open_gate_shows_its_own_artifacts_and_verdict(projection, repo_record, repo_builder):
    """Two `[?]` rows. Each card must be about the stage it is a gate for.

    The evidence dict was built once from the snapshot's CURRENT stage and shared by every seed, so the
    card for the other boundary listed the current stage's artifacts, quoted the current stage's reviewer
    verdict, and offered the current stage's questions. Its `question_digest` and `evidence_digest` were
    both null as well, so compare-and-submit had nothing to compare and the card could never go stale.
    A person approving that gate was reading another stage's work.
    """
    other = "user-stories"
    state = F.state_text(
        marks={GATE_STAGE: "?", other: "?"},
        fields={"Current Stage": GATE_STAGE, "Status": "Running", "Lifecycle Phase": "INCEPTION"},
    )
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(
            GATE_DIR,
            state=state,
            audit=F.gate_open_audit(GATE_STAGE),
            artifacts={
                "inception/requirements-analysis/requirements.md":
                    "# Requirements\n\n## Review\n\nVerdict: READY\n\n### Findings\n\n- blocker: current stage finding\n",
                "inception/user-stories/user-stories.md":
                    "# User stories\n\n## Review\n\nVerdict: NOT-READY\n\n### Findings\n\n- blocker: the other stage's finding\n",
            },
        )
        .build()
    )
    snap = projection.snapshot(repo_record(root), "default", GATE_DIR)
    gates = {seed.stage: seed for seed in _seeds(projection, snap) if seed.type == "gate"}
    assert set(gates) == {GATE_STAGE, other}, sorted(gates)

    mine = gates[other]
    paths = [a["relpath"] for a in mine.evidence["artifacts"]]
    assert any("user-stories" in path for path in paths), paths
    assert not any("requirements-analysis" in path for path in paths), paths
    assert mine.evidence["review"]["verdict"] == "NOT-READY"
    assert "the other stage's finding" in json.dumps(mine.evidence["review"]["findings"])

    # And the current stage's card is unchanged: its own artifacts and its own verdict.
    current = gates[GATE_STAGE]
    assert all("requirements-analysis" in a["relpath"] for a in current.evidence["artifacts"])
    assert current.evidence["review"]["verdict"] == "READY"

    # Compare-and-submit now has something to compare for the second gate too.
    captured = projection.captured(snap, other)
    assert captured["evidence_digest"] is not None
    assert captured["evidence_digest"] != projection.captured(snap, GATE_STAGE)["evidence_digest"]
