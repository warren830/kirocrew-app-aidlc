"""Golden read-model scenarios on real on-disk workspaces: cards, precedence, evidence and the queue order.

``IntentSnapshot`` is projection's own dataclass, so an unstable read or an injected value is built with
``dataclasses.replace``: a value, never a mock of the module under test.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import fixtures as F
import pytest

GATE_DIR = "260904-gate-demo"
GATE_STAGE = "requirements-analysis"
QUESTIONS_REL = "inception/requirements-analysis/requirements-analysis-questions.md"
REPO_ID = "r_000000000001"
RUNNING = {"Current Stage": GATE_STAGE, "Status": "Running", "Lifecycle Phase": "INCEPTION"}
STARTED_AUDIT = F.audit_block("STAGE_STARTED", "2026-09-04T09:00:00Z", Stage=GATE_STAGE)


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
        fields = dict(repo_id=repo_id, label=label, canonical_path=str(path), resolved_identity="1:2",
                      git_common_dir_identity=None, platform="darwin", added_at=clock.iso(), last_seen=None,
                      availability="available", availability_detail=None, archived=False, legacy_console_id=None,
                      install_status="installed", installed_engine_version="2.6.2", engine_dir=".kiro",
                      receipt_version=None)
        fields.update(over)
        return studio.repo_registry.RepoRecord(**fields)

    return build


# --- stubs for objects other modules own (§1.11 / §1.12 shapes) ---
@dataclass(frozen=True)
class BindingStub:
    slot_key: str | None = None
    session_key: str | None = None
    archive_state: str = "active"
    paused: bool = False
    interrupted_at: str | None = None
    last_stable_boundary: Any | None = None

    def to_json(self) -> dict:
        return asdict(self)


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

    def to_json(self) -> dict:
        return asdict(self)


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


def failed_run(**over) -> ActionStub:
    return ActionStub(**{"type": "run", "status": "Failed", "retry_fingerprint": "guard.x:1", **over})


# --- golden repositories ---
def _builder(repo_builder, name: str):
    """A builder in its OWN directory: two golden repos on the one ``repo_builder`` would overwrite each other."""
    return type(repo_builder)(repo_builder.root.parent / f"repo-{name}")


def _gate_state(**fields: str) -> str:
    return F.state_text(marks={GATE_STAGE: "?"}, fields={"Current Stage": GATE_STAGE, "Status": "Running",
                                                         "Next Stage": "user-stories", "Lifecycle Phase": "INCEPTION",
                                                         **fields})


def build(repo_builder, name: str | None = None, *, state: str | None = None, audit: str | None = None, **kw) -> Path:
    """The payload engine plus intent ``GATE_DIR`` (an open gate unless told otherwise)."""
    builder = _builder(repo_builder, name) if name else repo_builder
    return builder.with_engine("payload").with_workspace().with_intent(
        GATE_DIR, state=state or _gate_state(), audit=F.gate_open_audit(GATE_STAGE) if audit is None else audit,
        **kw).build()


@pytest.fixture
def snap_of(projection, repo_record):
    return lambda root, **kw: projection.snapshot(repo_record(root, **kw), "default", GATE_DIR)


@pytest.fixture
def revising_repo(repo_builder) -> Path:
    """A `[R]` row: the engine is revising after a rejected gate (C18: informational, never a queue item)."""
    return build(repo_builder, "revising", audit=F.gate_rejected_audit(GATE_STAGE), state=F.state_text(
        marks={GATE_STAGE: "R"}, fields={**RUNNING, "Revision Count": "1"}))


@pytest.fixture
def question_repo(repo_builder) -> Path:
    """A `[-]` stage whose questions file has six blank tags and no gate — the real devlake case."""
    text = F.read(F.OLDER_INTENTS / F.OLDER_INTENT / "ideation" / "feasibility" / "feasibility-questions.md")
    return build(repo_builder, "questions", state=F.state_text(marks={"feasibility": "-"}, fields={
        "Current Stage": "feasibility", "Status": "Running", "Lifecycle Phase": "IDEATION"}),
        audit=F.audit_block("STAGE_STARTED", "2026-09-04T09:00:02Z", Stage="feasibility"),
        questions={"ideation/feasibility/feasibility-questions.md": text})


@pytest.fixture
def parked_repo(repo_builder) -> Path:
    state = F.insert_runtime_field(F.insert_runtime_field(_gate_state(), "Parked", "2026-09-03T12:00:00Z"),
                                   "Parked At Stage", GATE_STAGE)
    return build(repo_builder, "parked", state=state)


@pytest.fixture
def dangling_repo(repo_builder) -> Path:
    """`active-intent` naming a directory that has no state file (the engine would fall back silently)."""
    root = build(repo_builder, "dangling")
    cursor = root / "aidlc" / "spaces" / "default" / "intents" / "active-intent"
    (cursor.parent / "260905-ghost").mkdir(exist_ok=True)
    cursor.write_text("260905-ghost\n")
    return root


@pytest.fixture
def burst_repo(repo_builder) -> Path:
    """Three ``ERROR_LOGGED`` rows inside ten minutes."""
    return build(repo_builder, "burst", state=F.state_text(marks={GATE_STAGE: "-"}, fields=RUNNING), audit=(
        F.audit_block("STAGE_STARTED", "2026-09-04T08:50:00Z", Stage=GATE_STAGE)
        + F.error_burst_audit(ts="2026-09-04T09:00:00Z", count=3)))


@pytest.fixture
def missing_repo(tmp_path) -> Path:
    return tmp_path / "gone"


@pytest.fixture
def missing_snap(snap_of, missing_repo):
    """A registered path that is simply not on disk any more."""
    return snap_of(missing_repo, availability="unavailable")


@pytest.fixture
def gate_snap(projection, repo_record, gate_repo):
    return projection.snapshot(repo_record(gate_repo), "default", GATE_DIR)


def captured_now(projection, snap_of, root, stage=GATE_STAGE):
    return projection.captured(snap_of(root), stage)


def _seeds(projection, snap, **over):
    return projection.derive_cards(snap, **{"findings": [], "binding": None, "install": None, "breakers": [],
                                            "live_actions": [], **over})


def _by_type(seeds):
    return {seed.type: seed for seed in seeds}


# --- snapshot ---
def test_snapshot_reads_one_coherent_moment(gate_snap, gate_repo, C):
    snap = gate_snap
    assert snap.stable is True and snap.is_active is True and snap.dangling_cursor is False
    assert snap.stage == GATE_STAGE and snap.intent_key == GATE_DIR  # default space: the bare dir name (C02)
    assert snap.state is not None and snap.state.state_version == 8
    state = Path(gate_repo) / "aidlc" / "spaces" / "default" / "intents" / GATE_DIR / "aidlc-state.md"
    assert snap.state_sha256 == F.sha256(state.read_text("utf-8"))
    assert snap.engine is not None and snap.engine.engine_version == C.BUNDLED_ENGINE_VERSION
    assert len(snap.graph) == 33 and snap.layout == "spaces"
    assert snap.stage_dir_rel and snap.stage_dir_rel.endswith(f"inception/{GATE_STAGE}")


def test_snapshot_always_hashes_the_card_stage_artifacts(gate_snap):
    """Review P14: the evidence set is what the human looked at, so it is always digested."""
    assert {a.name for a in gate_snap.stage_artifacts} == {"requirements.md", f"{GATE_STAGE}-questions.md"}
    assert all(a.sha256 and len(a.sha256) == 64 for a in gate_snap.stage_artifacts)


def test_snapshot_parses_the_review_section_of_the_primary_artifact(snap_of, repo_builder):
    snap = snap_of(build(repo_builder, artifacts={"inception/requirements-analysis/requirements.md": (
        "# Requirements\n\nOne requirement.\n\n"
        "## Review\n\n**Verdict**: NOT-READY\n\n### Blocker\n\n- Acceptance criteria are missing\n")}))
    assert snap.review is not None
    verdict, findings = snap.review
    assert verdict == "NOT-READY" and [f.level for f in findings] == ["blocker"]


#: The four stages where 2.7.1's `review_artifact` is NOT `produces[0]` — `{slug: (reviewed, produced)}`.
REVIEW_ARTIFACT_DISAGREEMENTS = {
    "functional-design": ("functional-spec", "entities"),
    "nfr-requirements": ("security-requirements", "performance-requirements"),
    "nfr-design": ("security-design", "performance-design"),
    "infrastructure-design": ("cicd-pipeline", "infrastructure-specification"),
}


@pytest.mark.parametrize("slug", sorted(REVIEW_ARTIFACT_DISAGREEMENTS))
def test_review_comes_from_the_reviewed_artifact_not_the_first_produced_one(snap_of, repo_builder, slug):
    """Both files carry a `## Review` and disagree: a `produces[0]` guess shows another artifact's verdict."""
    reviewed, first_produced = REVIEW_ARTIFACT_DISAGREEMENTS[slug]
    snap = snap_of(build(
        repo_builder, f"review-{slug}", audit=F.gate_open_audit(slug),
        state=F.state_text(marks={slug: "?"}, fields={"Current Stage": slug, "Status": "Running"}),
        artifacts={f"construction/{slug}/{first_produced}.md": "# First\n\n## Review\n\n**Verdict**: NOT-READY\n",
                   f"construction/{slug}/{reviewed}.md": "# Reviewed\n\n## Review\n\n**Verdict**: READY\n"}))
    node = snap.node(slug)
    assert node is not None and (node.review_artifact, node.produces[0]) == (reviewed, first_produced)
    assert snap.review is not None and snap.review[0] == "READY"


def test_snapshot_of_a_missing_repo_degrades_instead_of_raising(projection, missing_snap):
    """One unavailable repository must not abort the scan of the other 63."""
    snap = missing_snap
    assert snap.state is None and snap.state_snap is None and snap.stable is False
    assert snap.stage is None and snap.engine is None and snap.graph == ()
    captured = projection.captured(snap, None)
    assert captured["state_hash"] is None and captured["boundary_token"] is None and captured["stable"] is False
    body = projection.summarize(snap).to_json()
    assert body["unstable"] is True and body["disk"]["status"] is None
    assert body["counts"]["total"] == 0 and body["operational_state"] == "Idle"


def test_snapshot_reports_a_dangling_cursor(snap_of, dangling_repo):
    snap = snap_of(dangling_repo)
    assert snap.dangling_cursor is True
    assert snap.is_active is False  # the cursor names the ghost, not this intent
    assert snap.cursor == "260905-ghost"  # reported verbatim, so the card can name it


# --- captured / evidence stability ---
def test_captured_is_the_full_compare_and_submit_set(projection, gate_snap):
    captured = projection.captured(gate_snap, GATE_STAGE)
    assert set(captured) == {"state_hash", "boundary_token", "question_digest", "stage_attempt", "evidence_digest",
                             "is_active", "captured_at", "stable"}
    assert all(captured[key] for key in ("state_hash", "boundary_token", "question_digest", "evidence_digest"))
    assert captured["stage_attempt"] == 1 and captured["is_active"] is True and captured["stable"] is True


def test_captured_is_stable_across_two_reads_of_an_untouched_repo(projection, snap_of, gate_repo, clock):
    """A card must not go stale because time passed — only because disk moved (FR-ACT-010)."""
    first = captured_now(projection, snap_of, gate_repo)
    clock.advance(120)
    second = captured_now(projection, snap_of, gate_repo)
    compare = ("state_hash", "boundary_token", "question_digest", "stage_attempt", "evidence_digest", "is_active")
    assert {k: first[k] for k in compare} == {k: second[k] for k in compare}
    assert first["captured_at"] != second["captured_at"]


def test_evidence_digest_moves_when_a_stage_artifact_changes(projection, snap_of, gate_repo):
    before = captured_now(projection, snap_of, gate_repo)
    record = Path(gate_repo) / "aidlc/spaces/default/intents" / GATE_DIR
    (record / "inception/requirements-analysis/requirements.md").write_text("# Requirements\n\nOne, revised.\n")
    after = captured_now(projection, snap_of, gate_repo)
    assert before["state_hash"] == after["state_hash"]  # the state file did not move
    assert before["evidence_digest"] != after["evidence_digest"]  # what the human reads did


def test_captured_null_vs_non_null_is_a_difference(projection, snap_of, gate_repo):
    """Review P26.3: removing the questions file must invalidate a card captured with its digest."""
    with_file = captured_now(projection, snap_of, gate_repo)
    (Path(gate_repo) / "aidlc/spaces/default/intents" / GATE_DIR / QUESTIONS_REL).unlink()
    without = captured_now(projection, snap_of, gate_repo)
    assert with_file["question_digest"] is not None and without["question_digest"] is None
    assert with_file != without


def test_captured_has_no_stage_evidence_for_a_stageless_card(projection, gate_snap):
    stageless = projection.captured(gate_snap, None)
    assert stageless["evidence_digest"] is None and stageless["question_digest"] is None
    assert stageless["stage_attempt"] is None
    assert stageless["state_hash"] == projection.captured(gate_snap, GATE_STAGE)["state_hash"]


def test_evidence_snapshot_carries_the_full_card_shape(projection, gate_snap):
    body = projection.evidence(
        gate_snap, findings=[FindingStub("completed_count_mismatch", "warn")],
        binding=BindingStub(slot_key="slot-1", session_key="dashboard:slot-1"), slot=SlotStub(),
    ).to_json()
    assert set(body) == {"state", "audit", "directive", "artifacts", "review", "acceptance_criteria", "findings",
                         "session", "questions", "git", "markers", "presence_baseline", "presence", "cursor_readback"}
    assert body["state"]["sha256"] == gate_snap.state_sha256 and body["state"]["stable"] is True
    assert body["audit"]["complete"] is True and body["audit"]["boundary_event"]["type"] == "STAGE_AWAITING_APPROVAL"
    assert body["directive"] == {"stage": GATE_STAGE, "unit": None, "units": [], "matches_state": True,
                                 "version": 1, "kind": None, "state_sha256": gate_snap.state_sha256}
    # the artifact the Gate approves comes first: the Gate template opens on artifacts[0]
    assert [a["name"] for a in body["artifacts"]] == ["requirements.md", f"{GATE_STAGE}-questions.md"]
    assert body["findings"][0]["code"] == "completed_count_mismatch" and body["session"]["slot_key"] == "slot-1"
    assert body["questions"]["mode"] == "structured"  # a supported persisted group supplies its controls
    # the three reconciler-owned keys are present and null, so the wire shape never changes later
    assert body["presence_baseline"] is None and body["presence"] is None and body["cursor_readback"] is None


def test_evidence_never_claims_an_acceptance_criterion_is_met(projection, gate_snap):
    assert all(row["met"] is None for row in projection.evidence(gate_snap).to_json()["acceptance_criteria"])


def test_supported_file_questions_do_not_require_a_native_widget(projection, gate_snap, studio, monkeypatch):
    monkeypatch.setattr(studio.constants, "GROUPED_ANSWERS_VERIFIED", True)
    view = projection.questions_view
    assert view(gate_snap).mode == "structured"
    assert view(gate_snap, host_card={"ask_id": "active-native-wait"}).mode == "degraded"
    assert view(replace(gate_snap, stable=False)).mode == "degraded"
    question = gate_snap.questions.questions[0]
    ambiguous = replace(gate_snap, questions=replace(gate_snap.questions, questions=(question, question)))
    assert view(ambiguous).mode == "degraded"
    assert view(ambiguous, host_card={"card_id": "nonblocking"}).mode == "degraded"
    monkeypatch.setattr(studio.constants, "GROUPED_ANSWERS_VERIFIED", False)
    assert view(gate_snap).mode == "degraded"


def test_evidence_markers_on_a_card_omit_volatile_hook_health(projection, gate_snap):
    assert set(projection.evidence(gate_snap).to_json()["markers"]) == {
        "human_turn_at", "engine_touch_at", "turn_counter", "goal_stop_present", "recovery"}


# --- unstable reads cannot authorise a decision ---
def test_unstable_snapshot_renders_refreshing_and_cannot_authorise(projection, studio, gate_snap):
    """FR-ACT-009: a read that spanned a write is evidence of nothing."""
    unstable = replace(gate_snap, stable=False)
    engine = studio.consistency.ConsistencyEngine()
    assert engine.unstable(unstable) is True  # the card renders `Refreshing`
    facts = studio.consistency.RepoFacts(now=unstable.taken_at, repo=None, install=None)
    assert "unstable_read" in [f.code for f in engine.evaluate_intent(unstable, facts)]
    assert projection.summarize(unstable).to_json()["unstable"] is True
    # every captured set taken from it is marked unstable, so `submit` refuses (409 unstable_read)
    assert projection.captured(unstable, GATE_STAGE)["stable"] is False
    seeds = _seeds(projection, unstable)
    assert seeds, "the boundary is still real — the card must exist, it just cannot be submitted"
    assert all(seed.captured["stable"] is False for seed in seeds)
    boundary = projection.stable_boundary(unstable)
    assert boundary.stable is False and "state_unstable" in boundary.reasons


def test_stable_boundary_names_every_unmet_condition(projection, proj_mod, gate_snap):
    good = projection.stable_boundary(gate_snap)
    assert good.stable is True and good.reasons == () and good.marker == "[?]" and good.stage == GATE_STAGE
    assert good.boundary_token == projection.captured(gate_snap, GATE_STAGE)["boundary_token"]
    busy = projection.stable_boundary(replace(gate_snap, stable=False, is_active=False, cursor="260905-other"),
                                      live_actions=[ActionStub(status="Delivered")], busy_reasons=("running",),
                                      lease_active=True)
    assert busy.stable is False
    assert set(busy.reasons) == {"state_unstable", "action_in_flight", "session_busy", "cursor_mismatch"}
    assert set(busy.reasons) <= set(proj_mod.BOUNDARY_REASONS)


def test_stable_boundary_reports_not_at_boundary(projection, snap_of, burst_repo):
    boundary = projection.stable_boundary(snap_of(burst_repo))
    assert boundary.marker is None and boundary.reasons == ("not_at_boundary",)


# --- operational state: every rule, in precedence order ---
@pytest.fixture
def op(projection, request):
    """``operational_state`` over the given snapshot, or over ``gate_snap`` (built only when asked for)."""
    def state(snap=None, **kw):
        return projection.operational_state(request.getfixturevalue("gate_snap") if snap is None else snap, **kw)

    return state


def test_rule_01_archived_wins_over_everything(op):
    """Even an unresolved delivery on an archived intent shows as Archived: nothing is dispatchable."""
    assert op(binding=BindingStub(archive_state="archived", interrupted_at="2026-09-04T09:00:00Z"),
              live_actions=[ActionStub(status="ReconciliationRequired")], breaker_open=True,
              findings=[FindingStub("dangling_cursor", "blocking")]) == "Archived"


def test_rule_02_blocking_finding_or_uncertain_action(op):
    assert op(findings=[FindingStub("gate_without_audit_row", "blocking")]) == "ReconciliationRequired"
    assert op(live_actions=[ActionStub(status="ReconciliationRequired")]) == "ReconciliationRequired"
    # a warn finding is not blocking and must not shadow the real state
    assert op(findings=[FindingStub("receipt_drift", "warn")]) == "WaitingForYou"


def test_rule_03_interrupted_only_while_the_stop_is_in_flight(op):
    """P17 / C28: `Interrupted → Queued` must be unreachable in one click."""
    binding = BindingStub(slot_key="slot-1", interrupted_at="2026-09-04T09:40:00Z")
    assert op(binding=binding, host=SessionStub(running=True)) == "Interrupted"
    assert op(binding=binding, slot=SlotStub(stop_state="killing")) == "Interrupted"
    # once the slot has stopped, `interrupted_session` (blocking) takes over through rule 2
    assert op(binding=binding, host=SessionStub(running=False),
              findings=[FindingStub("interrupted_session", "blocking")]) == "ReconciliationRequired"
    # and after `acknowledge` cleared interrupted_at, a queued run is simply Queued
    assert op(binding=BindingStub(slot_key="slot-1"),
              live_actions=[ActionStub(type="run", status="Queued")]) == "Queued"


def test_rule_04_breaker_open(op):
    assert op(breaker_open=True) == "CircuitOpen"
    # but a blocking finding still outranks it
    assert op(breaker_open=True, findings=[FindingStub("lease_conflict", "blocking")]) == "ReconciliationRequired"


def test_rule_05_completed_and_rule_06_parked(op, snap_of, parked_repo):
    parked = snap_of(parked_repo)
    assert parked.state is not None and parked.state.parked_at == "2026-09-03T12:00:00Z"
    assert op(parked) == "Parked"
    assert op(replace(parked, state=replace(parked.state, status="Completed"))) == "Completed"  # rule 5 precedes 6


def test_rule_07_running_from_the_slot_or_an_in_flight_turn(op):
    assert op(host=SessionStub(running=True)) == "Running"
    assert op(live_actions=[ActionStub(status="Delivered")]) == "Running"


def test_rule_08_only_command_actions_make_an_intent_queued(op):
    """Review P08: a derived gate card sitting in `Queued` is not a queued turn."""
    assert [op(live_actions=[ActionStub(type=kind, status=status)]) for kind, status in (
        ("gate", "Queued"), ("run", "Queued"), ("resume", "Queued"), ("prepare_commit", "Delivering"),
        ("force_stop", "Queued"),
        ("gate", "Delivering"),  # anything of any type in Delivering is a dispatch in flight
    )] == ["WaitingForYou", "Queued", "Queued", "Queued", "Queued", "Queued"]


def test_rule_09_waiting_for_you_from_the_gate_mark(op, snap_of, revising_repo, question_repo):
    assert op() == "WaitingForYou"
    revising = snap_of(revising_repo)
    assert revising.state is not None and F.stage_marks(revising.state_snap.text)[GATE_STAGE] == "R"
    assert op(revising) == "WaitingForYou"
    questions = snap_of(question_repo)
    assert questions.questions is not None and questions.questions.pending_count == 6
    assert op(questions) == "WaitingForYou"


def test_rule_09_waiting_for_you_from_a_pending_checkpoint(op, snap_of, repo_builder):
    snap = snap_of(build(repo_builder, state=F.state_text(marks={GATE_STAGE: "-"}, fields=RUNNING),
                         audit=STARTED_AUDIT, questions={QUESTIONS_REL: (
                             "# Plan\n\n## Plan Approval\n\n- Approve Plan\n- Request Changes\n\n[Answer]:\n")}))
    assert snap.questions is not None and snap.questions.pending_count == 0
    assert snap.questions.pending_checkpoint == "plan_approval" and op(snap) == "WaitingForYou"


def test_rule_10_newest_failed_action_is_retry_eligible_or_failed(op, snap_of, burst_repo):
    snap = snap_of(burst_repo)
    assert op(snap) == "Idle"
    transient = ActionStub(type="run", status="Failed", updated_at="2026-09-04T09:10:00Z",
                           retry_fingerprint="transport.stream-closed:ab12cd")
    assert op(snap, live_actions=[transient]) == "RetryEligible"
    # an open breaker on the same intent means retrying is not offered
    assert op(snap, live_actions=[transient], breaker_open=True) == "CircuitOpen"
    assert op(snap, live_actions=[replace(transient, retry_fingerprint="guard.refusing-to-record:99aa11")]) == "Failed"
    # an unclassifiable failure is treated as deterministic: never invite a replay Studio cannot vouch for
    assert op(snap, live_actions=[replace(transient, retry_fingerprint=None)]) == "Failed"
    older = ActionStub(type="run", status="Failed", updated_at="2026-09-04T08:00:00Z", retry_fingerprint="guard.x:1",
                       action_id="a_0000000000000002")
    assert op(snap, live_actions=[older, transient]) == "RetryEligible"  # the *newest* action decides


def test_rule_11_paused_and_rule_12_idle(op, snap_of, burst_repo):
    snap = snap_of(burst_repo)
    assert op(snap, binding=BindingStub(paused=True)) == "Paused" and op(snap, binding=BindingStub()) == "Idle"
    # pause never hides an open gate: rule 9 is above rule 11
    gate = replace(snap, state=replace(snap.state, stages=[
        replace(row, mark="?", state="awaiting_approval") if row.slug == GATE_STAGE else row
        for row in snap.state.stages]))
    assert op(gate, binding=BindingStub(paused=True)) == "WaitingForYou"


def test_every_derived_state_is_a_canonical_enum_value(op, C):
    assert {op(), op(binding=BindingStub(archive_state="archived")), op(breaker_open=True),
            op(host=SessionStub(running=True)), op(findings=[FindingStub("dangling_cursor", "blocking")]),
            op(live_actions=[ActionStub(type="run", status="Queued")])} <= set(C.INTENT_STATE)


# --- card derivation ---
def test_gate_and_question_cards_coexist_for_one_stage(projection, gate_snap):
    """FORMAT-NOTES §7: a `[?]` row with a pending `## Q…` legitimately yields both cards."""
    seeds = _seeds(projection, gate_snap)
    assert [seed.type for seed in seeds] == ["gate", "question"]
    gate, question = seeds
    assert gate.severity == "blocking" and gate.risk_class == "human_lane" and gate.stage == GATE_STAGE
    assert gate.decisions == ("approve", "request_changes") and gate.consequence_variant == "approve_unlocks"
    assert gate.waiting_since == "2026-09-04T09:00:03Z"  # the STAGE_AWAITING_APPROVAL row, not now
    assert question.decisions == ("answers", "confirm_summary")


def test_a_version_2_directive_still_carries_the_question_card_of_a_per_unit_stage(projection, snap_of, repo_builder):
    """The marker names the per-unit stage `Current Stage` does not; refusing v2 left the intent "Idle"."""
    snap = snap_of(build(
        repo_builder, "directive-v2",
        state=F.state_text(marks={"code-generation": "-"}, fields={
            "Current Stage": "build-and-test", "Status": "Running", "Lifecycle Phase": "CONSTRUCTION"}),
        audit=F.audit_block("STAGE_STARTED", "2026-09-04T09:00:02Z", Stage="code-generation"),
        directive={"version": 2, "kind": "run-stage", "stage": "code-generation", "unit": "authentication",
                   "state_sha256": "AUTO"},
        questions={"construction/authentication/code-generation/code-generation-questions.md":
                   F.blank_answers(F.real_questions())}))
    assert (snap.directive.version, snap.directive.kind) == (2, "run-stage") and snap.directive.matches_state is True
    assert snap.stage == "code-generation"  # the marker outranks `Current Stage`
    assert snap.questions is not None and snap.questions.pending_count > 0
    seeds = _seeds(projection, snap)
    assert [seed.type for seed in seeds] == ["question"]
    assert seeds[0].stage == "code-generation" and "answers" in seeds[0].decisions
    assert projection.detail(snap).active_directive_stage == "code-generation"


def test_dedupe_key_shape_is_type_repo_intent_stage_token(projection, proj_mod, gate_snap):
    gate = _by_type(_seeds(projection, gate_snap))["gate"]
    parts = gate.dedupe_key.split(":")
    assert len(parts) == 5 and parts[:4] == ["gate", REPO_ID, GATE_DIR, GATE_STAGE]
    assert parts[4] == projection.captured(gate_snap, GATE_STAGE)["boundary_token"]
    assert gate.dedupe_key == proj_mod.dedupe_key("gate", REPO_ID, GATE_DIR, GATE_STAGE, parts[4])
    # a stageless card uses "-" for the stage segment
    assert proj_mod.dedupe_key("install_conflict", REPO_ID, GATE_DIR, None, None) == (
        f"install_conflict:{REPO_ID}:{GATE_DIR}:-:-")


def test_dedupe_key_follows_the_boundary(projection, snap_of, gate_repo):
    before = _by_type(_seeds(projection, snap_of(gate_repo)))["gate"]
    state = Path(gate_repo) / "aidlc/spaces/default/intents" / GATE_DIR / "aidlc-state.md"
    state.write_text(state.read_text().replace("**Revision Count**: 0", "**Revision Count**: 1"))
    assert before.dedupe_key != _by_type(_seeds(projection, snap_of(gate_repo)))["gate"].dedupe_key


def test_accept_as_is_is_offered_only_from_the_fourth_attempt(projection, snap_of, repo_builder, C):
    """C29 / review P24: the conductor adds `Accept as-is` after three rejection cycles."""
    def gate_after(revisions: int) -> Any:
        audit = F.gate_open_audit(GATE_STAGE) + "".join(
            F.audit_block("STAGE_REVISING", f"2026-09-04T09:1{i}:00Z", Stage=GATE_STAGE, Revision_count=str(i + 1))
            for i in range(revisions))
        return _by_type(_seeds(projection, snap_of(build(repo_builder, f"attempt-{revisions}", audit=audit))))["gate"]

    third, fourth = gate_after(2), gate_after(3)
    assert third.captured["stage_attempt"] == 3 and "accept_as_is" not in third.decisions
    assert fourth.captured["stage_attempt"] == C.ACCEPT_AS_IS_MIN_ATTEMPT == 4
    assert fourth.decisions == ("approve", "request_changes", "accept_as_is")


def test_revision_card_is_informational(projection, snap_of, revising_repo):
    revision = _by_type(_seeds(projection, snap_of(revising_repo)))["revision"]
    assert revision.severity == "info" and revision.risk_class == "read"
    assert revision.decisions == ()  # nothing to decide (C18)
    assert revision.waiting_since == "2026-09-04T09:00:11Z"


@pytest.mark.parametrize("checkpoint,text,expected", [
    ("summary_confirmation",
     "# Q\n\n## Consolidated Summary Confirmation\n\n- Looks correct\n- Request changes\n\n[Answer]:\n",
     ("confirm_summary",)),
    ("plan_approval", "# Q\n\n## Plan Approval\n\n- Approve Plan\n- Request Changes\n\n[Answer]:\n",
     ("approve_plan", "request_plan_changes")),
])
def test_question_card_decisions_follow_the_pending_checkpoint(projection, snap_of, repo_builder, checkpoint, text,
                                                               expected):
    snap = snap_of(build(repo_builder, checkpoint, state=F.state_text(marks={GATE_STAGE: "-"}, fields=RUNNING),
                         audit=STARTED_AUDIT, questions={QUESTIONS_REL: text}))
    question = _by_type(_seeds(projection, snap))["question"]
    assert question.decisions == expected and question.reason == checkpoint
    # what the card renders `action.question.<checkpoint>.headline` and `.consequence.<checkpoint>` from
    assert question.headline_params == {"stage": GATE_STAGE, "pending": 0, "checkpoint": checkpoint}
    assert question.consequence_variant == checkpoint and question.dedupe_key.endswith(snap.questions.sha256)


PLAN_TEXT = "# Q\n\n## Plan Approval\n\n- Approve Plan\n- Request Changes\n\n[Answer]:\n"


def _plan_asked(session: str, ts: str = "2026-09-04T09:01:00Z") -> str:
    return F.audit_block("DECISION_RECORDED", ts, Stage=GATE_STAGE, Checkpoint="Code Generation Plan Approval",
                         Session=session)


@pytest.mark.parametrize("audit,stale", [
    pytest.param(_plan_asked("s-1") + F.audit_block("HUMAN_TURN", "2026-09-04T09:02:00Z", Session="s-1"), False,
                 id="same-session"),
    pytest.param(_plan_asked("s-1") + F.audit_block("SESSION_STARTED", "2026-09-04T12:00:00Z", Session="s-2")
                 + F.audit_block("HUMAN_TURN", "2026-09-04T12:00:02Z", Session="s-2"), True, id="restarted"),
    pytest.param(F.audit_block("SESSION_STARTED", "2026-09-04T08:00:00Z", Session="s-0") + _plan_asked("s-1"), False,
                 id="asked-after-the-restart"),
])
def test_a_plan_asked_in_an_earlier_session_is_resumed_not_approved(projection, snap_of, repo_builder, audit, stale):
    """2.10 records a plan reply only in the session that asked; after a restart Approve could only be refused."""
    snap = snap_of(build(repo_builder, "plan_approval", state=F.state_text(marks={GATE_STAGE: "-"}, fields=RUNNING),
                         audit=STARTED_AUDIT + audit, questions={QUESTIONS_REL: PLAN_TEXT}))
    question = _by_type(_seeds(projection, snap))["question"]
    if stale:
        assert question.decisions == ("resume",) and question.consequence_variant == "plan_session_stale"
    else:
        assert question.decisions == ("approve_plan", "request_plan_changes")
        assert question.consequence_variant == "plan_approval"


def test_blank_question_group_yields_one_atomic_card(projection, snap_of, question_repo):
    """FR-Q-002/D13: one group is one card, never one card per question."""
    snap = snap_of(question_repo)
    seeds = _seeds(projection, snap)
    assert [seed.type for seed in seeds] == ["question"]
    question = seeds[0]
    assert question.decisions == ("answers",) and question.headline_params["pending"] == 6
    assert question.evidence["questions"]["pending_count"] == 6
    assert len(question.evidence["questions"]["questions"]) == 6
    assert question.waiting_since != snap.taken_at  # the boundary time is the file, never "now" (C20)


def test_question_card_from_a_host_needs_input_signal(projection, snap_of, repo_builder):
    snap = snap_of(build(repo_builder, state=F.state_text(marks={GATE_STAGE: "-"}, fields=RUNNING), audit=STARTED_AUDIT,
                         questions={QUESTIONS_REL: "# Q\n\n## Q1. Which?\n\n- A. One\n\n[Answer]: A\n"}))
    assert snap.questions is not None and snap.questions.pending_count == 0 and _seeds(projection, snap) == []
    seeds = _seeds(projection, snap, slot=SlotStub(needs_input=True))
    assert [seed.type for seed in seeds] == ["question"] and seeds[0].reason == "needs_input"


def test_missing_input_card_for_a_dangling_cursor_is_admin_lane_and_space_scoped(projection, snap_of, dangling_repo):
    """C22: picking the active intent is an engine verb under an admin lease, never a prompt."""
    card = _by_type(_seeds(projection, snap_of(dangling_repo)))["missing_input"]
    assert card.decisions == ("pick_intent",) and card.risk_class == "admin"
    assert card.stage is None and card.reason == "dangling_cursor"
    # space-scoped key: every intent in the space derives it, the live-dedupe index collapses them
    assert card.dedupe_key == f"missing_input:{REPO_ID}:default~*:-:dangling_cursor"


def test_missing_input_card_for_an_empty_scope_is_human_lane(projection, snap_of, repo_builder):
    state = F.state_text(marks={GATE_STAGE: "-"}, fields={"Scope": "", "Current Stage": GATE_STAGE})
    card = _by_type(_seeds(projection, snap_of(build(repo_builder, state=state))))["missing_input"]
    assert card.decisions == ("provide_input",) and card.risk_class == "human_lane"
    assert card.reason == "missing_scope"
    assert card.dedupe_key == f"missing_input:{REPO_ID}:{GATE_DIR}:-:missing_scope"


def test_recovery_card_is_one_card_for_every_blocking_finding(projection, gate_snap):
    card = _by_type(_seeds(projection, gate_snap, findings=[
        FindingStub("gate_without_audit_row", "blocking"), FindingStub("state_version_unsupported", "blocking"),
        FindingStub("receipt_drift", "warn")]))["recovery"]
    assert card.severity == "critical" and card.risk_class == "studio_only"
    assert card.decisions == ("rebind_session", "mark_not_delivered", "acknowledge")
    assert card.evidence["recovery_codes"] == ["gate_without_audit_row", "state_version_unsupported"]
    assert card.reason == "gate_without_audit_row"


def test_recovery_card_skips_the_two_findings_that_already_have_a_card(projection, gate_snap):
    """`delivery_uncertain_action` IS the uncertain action; `lease_conflict` clears itself."""
    seeds = _seeds(projection, gate_snap, findings=[FindingStub("delivery_uncertain_action", "blocking"),
                                                    FindingStub("lease_conflict", "blocking")])
    assert "recovery" not in _by_type(seeds)


def test_recovery_card_for_a_session_lost_mid_stage(projection, snap_of, burst_repo):
    snap = snap_of(burst_repo)
    bound = BindingStub(slot_key="slot-1", session_key="dashboard:slot-1")
    lost = _by_type(_seeds(projection, snap, binding=bound, slot=None))
    assert lost["recovery"].evidence["recovery_codes"] == ["session_lost_mid_stage"]
    assert "recovery" not in _by_type(_seeds(projection, snap, binding=bound, slot=SlotStub()))
    assert "recovery" not in _by_type(_seeds(projection, snap, binding=BindingStub(), slot=None))  # unbound != lost


def test_no_seed_is_derived_for_an_uncertain_delivery(projection, gate_snap, proj_mod):
    """The card IS that action, re-typed for the queue — a seed would duplicate a live decision."""
    seeds = _seeds(projection, gate_snap, live_actions=[ActionStub(status="ReconciliationRequired")],
                   findings=[FindingStub("delivery_uncertain_action", "blocking")])
    assert "delivery_uncertain" not in _by_type(seeds)
    assert [proj_mod.queue_type_for("gate", s) for s in ("Delivered", "ReconciliationRequired", "Queued")] == [
        "gate", "delivery_uncertain", "gate"]


def test_failure_card_from_an_error_burst(projection, snap_of, burst_repo):
    card = _by_type(_seeds(projection, snap_of(burst_repo)))["failure"]
    assert card.severity == "attention" and card.decisions == ("retry_now", "keep_paused")
    assert card.reason == "error_burst" and card.waiting_since == "2026-09-04T09:00:00Z"
    assert card.evidence["error_burst_at"] == "2026-09-04T09:00:00Z"


def test_isolated_error_rows_never_raise_a_failure_card(projection, snap_of, repo_builder):
    """FORMAT-NOTES §6: single `ERROR_LOGGED` rows are the conductor correcting itself."""
    error = {"Tool": "aidlc-state.ts", "Error": "Unknown subcommand: set-status."}
    snap = snap_of(build(repo_builder, state=F.state_text(marks={GATE_STAGE: "-"}, fields={
        "Current Stage": GATE_STAGE, "Status": "Running"}), audit=(
        F.audit_block("STAGE_STARTED", "2026-09-04T08:00:00Z", Stage=GATE_STAGE)
        + F.audit_block("ERROR_LOGGED", "2026-09-04T08:10:00Z", **error)
        + F.audit_block("ERROR_LOGGED", "2026-09-04T09:00:00Z", **error))))
    assert "failure" not in _by_type(_seeds(projection, snap))


def test_failure_card_from_a_deterministic_failed_action_only(projection, snap_of, revising_repo):
    snap = snap_of(revising_repo)
    deterministic = ActionStub(type="run", status="Failed", updated_at="2026-09-04T09:20:00Z",
                               resolved_at="2026-09-04T09:20:00Z", retry_fingerprint="guard.refusing-to-record:99aa11")
    card = _by_type(_seeds(projection, snap, live_actions=[deterministic]))["failure"]
    assert card.reason == "action_failed" and card.related_action_id == deterministic.action_id
    assert card.waiting_since == "2026-09-04T09:20:00Z"
    transient = replace(deterministic, retry_fingerprint="transport.stream-closed:ab12cd")
    assert "failure" not in _by_type(_seeds(projection, snap, live_actions=[transient]))


def test_circuit_breaker_card_is_keyed_on_the_fingerprint(projection, gate_snap):
    card = _by_type(_seeds(projection, gate_snap, breakers=[BreakerStub()]))["circuit_breaker"]
    assert card.severity == "attention" and card.decisions == ("retry_now", "keep_paused")
    assert card.waiting_since == "2026-09-04T09:30:00Z" and card.dedupe_key.endswith(":transport.stream-closed:ab12cd")
    assert card.evidence["breaker"]["fingerprint_class"] == "transport"
    closed = replace(BreakerStub(), opened_at=None, count=1)  # a closed breaker row raises nothing
    assert "circuit_breaker" not in _by_type(_seeds(projection, gate_snap, breakers=[closed]))


def test_install_conflict_card_is_navigation_only(projection, gate_snap):
    card = _by_type(_seeds(projection, gate_snap, install=InstallStub()))["install_conflict"]
    assert card.decisions == () and card.risk_class == "read" and card.stage is None
    assert card.severity == "attention"  # nothing was written (FR-ACT-008)
    assert card.evidence["install"]["status"] == "recovery_required"
    installed = replace(InstallStub(), status="installed")
    assert "install_conflict" not in _by_type(_seeds(projection, gate_snap, install=installed))


def test_budget_stop_is_never_derived(projection, gate_snap, proj_mod, C):
    """C16: reserved so the UI template set is stable; no unattended turns exist in v1."""
    seeds = _seeds(projection, gate_snap, breakers=[BreakerStub()], install=InstallStub(),
                   findings=[FindingStub("dangling_cursor", "blocking")], live_actions=[failed_run()])
    assert "budget_stop" not in _by_type(seeds)
    assert "budget_stop" in proj_mod.PRIORITY_GROUP and "budget_stop" in C.ACTION_TYPE


def test_every_seed_type_severity_and_risk_class_is_a_canonical_enum(projection, gate_snap, C):
    seeds = _seeds(projection, gate_snap, breakers=[BreakerStub()], install=InstallStub(),
                   findings=[FindingStub("gate_without_audit_row", "blocking")], live_actions=[failed_run()])
    assert {s.type for s in seeds} == {"recovery", "gate", "question", "failure", "circuit_breaker", "install_conflict"}
    for seed in seeds:
        assert seed.type in C.ACTION_TYPE and seed.severity in C.SEVERITY and seed.risk_class in C.RISK_CLASS
        assert all(decision in C.DECISION_KIND for decision in seed.decisions)
        assert seed.intent_uuid == "01a00000-0000-7000-8000-000000000001"
        json.dumps(asdict(seed))  # every seed must survive the SQLite json column


def test_seeds_come_back_in_queue_order(projection, gate_snap, proj_mod):
    seeds = _seeds(projection, gate_snap, breakers=[BreakerStub()], install=InstallStub(),
                   findings=[FindingStub("gate_without_audit_row", "blocking")])
    groups = [proj_mod.priority_group_for(seed.type) for seed in seeds]
    assert groups == sorted(groups) and seeds[0].type == "recovery"


def test_a_card_carries_the_snapshot_evidence_it_was_derived_from(projection, gate_snap):
    seed = _by_type(_seeds(projection, gate_snap))["gate"]
    assert seed.evidence["state"]["sha256"] == gate_snap.state_sha256
    assert seed.captured["evidence_digest"] == projection.captured(gate_snap, GATE_STAGE)["evidence_digest"]
    assert {a["relpath"] for a in seed.evidence["artifacts"]} == {a.relpath for a in gate_snap.stage_artifacts}
    assert seed.stage_ref == {"slug": GATE_STAGE, "number": "2.3", "name": "Requirements Analysis",
                              "phase": "inception", "unit": None}


def test_a_new_question_group_never_inherits_another_stages_answer_time(projection, snap_of, repo_builder):
    """`waiting_since` falls back to the questions file's mtime, never to another stage's QUESTION_ANSWERED."""
    text = F.read(F.OLDER_INTENTS / F.OLDER_INTENT / "ideation" / "feasibility" / "feasibility-questions.md")
    snap = snap_of(build(
        repo_builder, state=F.state_text(marks={"feasibility": "-"}, fields={
            "Current Stage": "feasibility", "Status": "Running", "Lifecycle Phase": "IDEATION"}),
        # an answered question on a DIFFERENT, much older stage: the event that used to be borrowed
        audit=(F.audit_block("QUESTION_ANSWERED", "2026-01-01T00:00:00Z", Stage="intent-capture")
               + F.audit_block("STAGE_STARTED", "2026-09-04T09:00:02Z", Stage="feasibility")),
        questions={"ideation/feasibility/feasibility-questions.md": text}))
    question = _by_type(_seeds(projection, snap))["question"]
    assert question.waiting_since != "2026-01-01T00:00:00Z", "it borrowed another stage's clock"
    assert question.waiting_since != snap.taken_at, "and it must not reset on every scan either"
    assert question.waiting_since == {a.relpath: a.mtime for a in snap.stage_artifacts}[snap.questions.relpath]


def test_one_oversized_artifact_still_produces_a_gate_card(projection, snap_of, repo_builder, C):
    """A `snapshot` that raised `too_large` made the reconciler skip the intent, so the gate disappeared."""
    snap = snap_of(build(repo_builder, artifacts={
        "inception/requirements-analysis/requirements.md": "x" * (C.MAX_ARTIFACT_RENDER_BYTES + 1024)}))
    assert _by_type(_seeds(projection, snap))["gate"].type == "gate"
    # and the evidence says the verdict could not be read, rather than implying there is no reviewer
    assert snap.review is None and snap.review_unreadable is True
    review = projection.evidence(snap).review
    assert review is not None, "an unreadable verdict must not read as 'no reviewer'"
    assert review["findings_parsed"] is False and review["unreadable"] is True and review["verdict"] is None


def test_a_second_open_gate_shows_its_own_artifacts_and_verdict(projection, snap_of, repo_builder):
    """Two `[?]` rows: each card shows its own stage's artifacts, verdict and compare-and-submit digests."""
    other = "user-stories"
    snap = snap_of(build(
        repo_builder, state=F.state_text(marks={GATE_STAGE: "?", other: "?"}, fields=RUNNING), artifacts={
            "inception/requirements-analysis/requirements.md":
                "# Requirements\n\n## Review\n\nVerdict: READY\n\n### Findings\n\n- blocker: current stage finding\n",
            "inception/user-stories/user-stories.md":
                "# Stories\n\n## Review\n\nVerdict: NOT-READY\n\n### Findings\n\n"
                "- blocker: the other stage's finding\n",
        }))
    gates = {seed.stage: seed for seed in _seeds(projection, snap) if seed.type == "gate"}
    assert set(gates) == {GATE_STAGE, other}, sorted(gates)
    mine = gates[other]
    paths = [a["relpath"] for a in mine.evidence["artifacts"]]
    assert any("user-stories" in path for path in paths), paths
    assert not any("requirements-analysis" in path for path in paths), paths
    assert mine.evidence["review"]["verdict"] == "NOT-READY"
    assert "the other stage's finding" in json.dumps(mine.evidence["review"]["findings"])
    current = gates[GATE_STAGE]  # unchanged: its own artifacts and its own verdict
    assert all("requirements-analysis" in a["relpath"] for a in current.evidence["artifacts"])
    assert current.evidence["review"]["verdict"] == "READY"
    captured = projection.captured(snap, other)  # compare-and-submit has something to compare
    assert captured["evidence_digest"] is not None
    assert captured["evidence_digest"] != projection.captured(snap, GATE_STAGE)["evidence_digest"]


# --- sort_actions ---
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
    return [CardStub("a_08", "run", "info", "2026-09-04T08:00:00Z", zed),
            CardStub("a_07", "revision", "info", "2026-09-04T07:00:00Z", alfa),
            CardStub("a_06", "install_conflict", "attention", "2026-09-04T06:00:00Z", zed),
            CardStub("a_05", "failure", "attention", "2026-09-04T05:00:00Z", alfa),
            CardStub("a_04", "question", "blocking", "2026-09-04T04:00:00Z", zed),
            CardStub("a_03", "gate", "blocking", "2026-09-04T03:00:00Z", alfa),
            CardStub("a_02", "delivery_uncertain", "critical", "2026-09-04T02:00:00Z", zed),
            CardStub("a_01", "recovery", "critical", "2026-09-04T01:00:00Z", alfa)]


def test_sort_actions_priority_and_oldest_golden_order(projection):
    in_order = ["a_01", "a_02", "a_03", "a_04", "a_05", "a_06", "a_07", "a_08"]
    assert [card.action_id for card in projection.sort_actions(_queue(), "priority")] == in_order
    assert [card.action_id for card in projection.sort_actions(_queue(), "oldest")] == in_order  # ignores priority
    assert [c.action_id for c in projection.sort_actions(list(reversed(_queue())), "oldest")] == in_order


def test_sort_actions_equal_priority_is_oldest_first_then_action_id(projection):
    cards = [CardStub(action_id, "gate", "blocking", f"2026-09-04T0{hour}:00:00Z", {"repo_id": "r", "label": "r"})
             for action_id, hour in (("a_bb", 3), ("a_aa", 3), ("a_cc", 1))]
    assert [c.action_id for c in projection.sort_actions(cards, "priority")] == ["a_cc", "a_aa", "a_bb"]


def test_sort_actions_repo_mode_is_label_case_insensitive_then_priority(projection):
    order = [card.action_id for card in projection.sort_actions(_queue(), "repo")]
    assert order == ["a_01", "a_03", "a_05", "a_07", "a_02", "a_04", "a_06", "a_08"]


def test_sort_actions_type_mode_groups_by_band_then_type_name(projection):
    assert [card.queue_type for card in projection.sort_actions(_queue(), "type")] == [
        "delivery_uncertain", "recovery", "gate", "question", "failure", "install_conflict", "revision", "run"]


def test_sort_actions_does_not_mutate_its_input_and_refuses_an_unknown_organize(projection, studio):
    cards = _queue()
    before = list(cards)
    projection.sort_actions(cards, "priority")
    assert cards == before
    with pytest.raises(studio.errors.StudioError) as exc:
        projection.sort_actions(_queue(), "whatever")
    assert exc.value.code == "bad_param" and exc.value.status == 400


def test_sort_actions_keeps_two_repositories_with_the_same_label_in_separate_runs(projection):
    """A clone and a worktree often share a label; the group key is the repo_id, so a tie must not interleave."""
    one = {"repo_id": "r_aaa", "label": "checkout", "canonical_path": "/one"}
    two = {"repo_id": "r_bbb", "label": "checkout", "canonical_path": "/two"}
    ordered = projection.sort_actions([
        CardStub("a_1", "gate", "blocking", "2026-09-04T01:00:00Z", one),
        CardStub("a_2", "run", "info", "2026-09-04T02:00:00Z", two),
        CardStub("a_3", "gate", "blocking", "2026-09-04T03:00:00Z", two),
        CardStub("a_4", "run", "info", "2026-09-04T04:00:00Z", one)], "repo")
    keys = [projection.group_key(card, "repo") for card in ordered]
    runs = [key for index, key in enumerate(keys) if index == 0 or keys[index - 1] != key]
    assert runs == ["r_aaa", "r_bbb"], keys  # one run-length group per repository
    assert len(runs) == len(set(runs)), "a repo_id appeared as two separate groups"
    assert [c.action_id for c in ordered] == ["a_1", "a_4", "a_3", "a_2"]  # within a repository the band leads


def test_group_key_per_organize_mode(projection):
    card = _queue()[0]  # a run card in band 4, repo r_z
    assert [projection.group_key(card, mode) for mode in ("priority", "repo", "type", "oldest")] == [
        "group.4", "r_z", "run", "oldest"]


def test_priority_group_table_covers_every_action_type(proj_mod, C):
    assert set(proj_mod.PRIORITY_GROUP) == set(C.ACTION_TYPE)
    assert set(proj_mod.PRIORITY_GROUP.values()) == {1, 2, 3, 4} and set(proj_mod.SEVERITY_RANK) == set(C.SEVERITY)


# --- map inputs (the assembly is `ui/src/map/buildMap.ts`, pinned by `buildMap.test.ts`) ---
MAP_INPUT_KEYS = {"intent_key", "graph_version", "now", "nodes", "rows", "status", "phases", "current_stage",
                  "directive_stage", "scope_grid", "units", "active_unit", "artifacts", "skip_reasons", "attempts"}
MAP_NODE_KEYS = {"slug", "number", "name", "phase", "execution", "mode", "lead_agent", "reviewer", "review_class",
                 "summary_confirmation", "per_unit", "consumes", "produces", "requires_stage"}


def test_map_inputs_carry_the_graph_the_rows_and_the_cursor(projection, gate_snap, clock, C):
    inputs = projection.map_inputs(gate_snap)
    assert set(inputs) == MAP_INPUT_KEYS
    assert json.loads(json.dumps(inputs)) == inputs  # JSON-native: the route sends it as it is
    assert (inputs["intent_key"], inputs["graph_version"], inputs["now"]) == (
        GATE_DIR, C.BUNDLED_ENGINE_VERSION, clock.now())
    assert len(inputs["nodes"]) == 33 and all(set(node) == MAP_NODE_KEYS for node in inputs["nodes"])
    nodes = {node["slug"]: node for node in inputs["nodes"]}
    gate = nodes[GATE_STAGE]
    assert (gate["number"], gate["name"], gate["phase"]) == ("2.3", "Requirements Analysis", "inception")
    assert gate["summary_confirmation"] == "required"
    assert all(isinstance(name, str) for node in inputs["nodes"] for name in node["consumes"])
    assert "workspace-scaffold" in nodes["workspace-detection"]["requires_stage"]
    rows = {row["slug"]: row for row in inputs["rows"]}
    assert len(rows) == 33 and rows[GATE_STAGE]["state"] == "awaiting_approval" and inputs["status"] == "Running"
    assert inputs["current_stage"] == inputs["directive_stage"] == GATE_STAGE
    assert {name.strip().lower(): value for name, value in inputs["phases"]}["initialization"] == "Verified"
    assert any(meta["name"] == "requirements.md" and meta["stage"] == GATE_STAGE for meta in inputs["artifacts"])


def test_map_inputs_skip_reasons_from_row_suffix_and_from_stages_to_skip(projection, snap_of, repo_builder):
    """Both spellings are real (FORMAT-NOTES §3)."""
    state = F.state_text(F.DEVDELTA_REMEDIATION, marks={GATE_STAGE: "?"}, fields={"Current Stage": GATE_STAGE})
    state = state.replace("- **Stages to Skip**: 1.1 (intent-capture)",
                          "- **Stages to Skip**: 1.1 (intent-capture — greenfield)")
    inputs = projection.map_inputs(snap_of(build(repo_builder, state=state)))
    reasons = inputs["skip_reasons"]
    assert next(row for row in inputs["rows"] if row["slug"] == "market-research")["suffix"] == "SKIP"
    assert "market-research" in reasons and reasons["market-research"] is None  # a bare "SKIP"
    assert reasons["intent-capture"] == "greenfield" and GATE_STAGE not in reasons


def test_map_inputs_send_the_state_scope_grid_before_the_birth_scope(projection, snap_of, repo_builder):
    """A suffixless legacy row falls back to this column, so it must be the scope the workflow runs now."""
    slug = "user-stories"
    state = F.state_text(fields={"Scope": "feature", "Status": "Running", "Current Stage": slug},
                         marks={slug: " ", "workspace-scaffold": "x"},
                         modes={slug: "EXECUTE", "workspace-scaffold": "EXECUTE"})
    state = state.replace(f"- [ ] {slug} — EXECUTE", f"- [ ] {slug}").replace(
        "- [x] workspace-scaffold — EXECUTE", "- [x] workspace-scaffold")
    snap = snap_of(build(repo_builder, state=state, audit="", scope="poc"))
    assert snap.state.row(slug).suffix is None
    assert snap.state.project["Scope"] == "feature" and snap.row.scope == "poc"
    assert (snap.grid["feature"][slug], snap.grid["poc"][slug]) == ("EXECUTE", "SKIP")
    inputs = projection.map_inputs(snap)
    assert inputs["scope_grid"] == snap.grid["feature"]
    assert next(row for row in inputs["rows"] if row["slug"] == slug)["suffix"] is None
    assert projection.map_inputs(replace(snap, grid={}))["scope_grid"] == {}


def test_map_inputs_keep_both_sides_of_a_graph_drift(projection, snap_of, repo_builder):
    """A v7 record (32 rows) read against the bundled 33-stage graph; `buildMap` shows the union."""
    inputs = projection.map_inputs(snap_of(build(repo_builder, state=F.state_text(F.PAYLOAD_GATE_OPEN,
                                                                                  tree=F.PAYLOAD_230))))
    nodes, rows = {node["slug"] for node in inputs["nodes"]}, {row["slug"] for row in inputs["rows"]}
    assert "contract-design" in nodes - rows  # in the 33-graph, not in a v7 state file
    assert "application-design" in rows - nodes  # the v7 row the 33-graph does not know


def test_map_inputs_carry_units_and_each_artifacts_stage_and_unit(projection, snap_of, repo_builder):
    """The real per-unit Construction layout (FORMAT-NOTES §8)."""
    snap = snap_of(build(
        repo_builder, state=F.state_text(marks={"code-generation": "-"}, fields={
            "Current Stage": "code-generation", "Status": "Running", "Lifecycle Phase": "CONSTRUCTION"}),
        audit=F.audit_block("STAGE_STARTED", "2026-09-04T09:00:00Z", Stage="code-generation"),
        artifacts={"construction/ledger-export/code-generation/code-generation-plan.md": "# Plan\n",
                   "construction/build-and-test/build-test-results.md": "# Results\n"}))
    assert snap.units == ("ledger-export",)
    inputs = projection.map_inputs(snap)
    nodes = {node["slug"]: node for node in inputs["nodes"]}
    assert inputs["units"] == ["ledger-export"]
    # a once-per-workflow construction stage has no unit sub-rows
    assert nodes["code-generation"]["per_unit"] is True and nodes["build-and-test"]["per_unit"] is False
    placed = {(meta["name"], meta["stage"], meta["unit"]) for meta in inputs["artifacts"]}
    assert ("code-generation-plan.md", "code-generation", "ledger-export") in placed
    assert ("build-test-results.md", "build-and-test", None) in placed


def test_map_inputs_attempts_are_each_stages_latest_attempt(projection, snap_of, repo_builder, clock, C):
    """The open attempt is the LATEST start, measured to `now`; one whose start or end does not parse is absent."""
    blocks = [("STAGE_STARTED", "2026-09-04T08:00:00Z", GATE_STAGE),
              ("STAGE_COMPLETED", "2026-09-04T08:10:00Z", GATE_STAGE),
              ("STAGE_STARTED", "2026-09-04T09:00:00Z", GATE_STAGE),
              ("STAGE_AWAITING_APPROVAL", "2026-09-04T09:00:03Z", GATE_STAGE),
              ("STAGE_STARTED", "2026-09-04T06:00:00Z", "market-research"),
              ("STAGE_SKIPPED", "2026-09-04T06:05:00Z", "market-research"),
              ("STAGE_STARTED", "2026-09-04T05:00:00Z", "feasibility"), ("STAGE_COMPLETED", "yesterday", "feasibility"),
              ("STAGE_STARTED", "soon", "scope-definition")]
    audit = "".join(F.audit_block(event, ts, Stage=stage) for event, ts, stage in blocks)
    inputs = projection.map_inputs(snap_of(build(repo_builder, audit=audit)))
    at = C.epoch_from_iso
    assert inputs["attempts"] == {
        GATE_STAGE: {"started": at("2026-09-04T09:00:00Z"), "ended": None},
        "market-research": {"started": at("2026-09-04T06:00:00Z"), "ended": at("2026-09-04T06:05:00Z")}}
    assert inputs["now"] == clock.now() == at("2026-09-04T10:00:00Z")


@pytest.mark.parametrize("zone", ["America/Los_Angeles", "Europe/Berlin", "UTC"])
def test_map_inputs_attempt_times_are_the_same_in_a_dst_timezone(projection, snap_of, repo_builder, monkeypatch,
                                                                 request, clock, zone):
    """An audit timestamp is UTC; converting through local time is an hour off for half the year in a DST zone."""
    import time as time_module

    request.addfinalizer(time_module.tzset)  # runs after monkeypatch restores TZ in the environment
    monkeypatch.setenv("TZ", zone)
    time_module.tzset()
    inputs = projection.map_inputs(snap_of(build(repo_builder, audit=STARTED_AUDIT)))
    assert clock.now() - inputs["attempts"][GATE_STAGE]["started"] == 3600


def test_map_inputs_of_an_unreadable_state_are_empty_but_valid(projection, missing_snap):
    inputs = projection.map_inputs(missing_snap)
    assert (inputs["nodes"], inputs["rows"], inputs["phases"], inputs["status"]) == ([], [], [], None)
    assert inputs["skip_reasons"] == {} and inputs["attempts"] == {}
    json.dumps(inputs)


def test_map_inputs_without_an_engine_still_carry_the_state_rows(projection, snap_of, repo_builder):
    """A repo whose harness was removed still has a workflow on disk; the map must show it."""
    snap = snap_of(repo_builder.with_workspace().with_intent(GATE_DIR, state=_gate_state(),
                                                             audit=F.gate_open_audit(GATE_STAGE)).build())
    assert snap.engine is None and snap.graph == ()
    inputs = projection.map_inputs(snap)
    assert inputs["nodes"] == [] and inputs["graph_version"] is None
    rows = {row["slug"]: row for row in inputs["rows"]}
    assert len(rows) == 33 and (rows[GATE_STAGE]["state"], rows[GATE_STAGE]["suffix"]) == ("awaiting_approval",
                                                                                         "EXECUTE")


# --- artifacts ---
def test_artifacts_listing_ids_and_kinds(projection, gate_snap):
    metas, truncated = projection.artifacts(gate_snap)
    by_name = {meta.name: meta for meta in metas}
    assert truncated is False and "aidlc-state.md" in by_name  # the record's own files are listed too
    assert by_name[f"{GATE_STAGE}-questions.md"].kind == "questions"
    requirements = by_name["requirements.md"]
    assert (requirements.kind, requirements.stage, requirements.phase) == ("artifact", GATE_STAGE, "inception")
    assert requirements.artifact_id == hashlib.sha1(requirements.relpath.encode("utf-8")).hexdigest()


def test_artifacts_truncation_is_reported(projection, snap_of, gate_repo, monkeypatch, studio):
    """A partial walk must never be presented as a complete record."""
    monkeypatch.setattr(studio.constants, "MAX_ARTIFACTS_PER_INTENT", 1)
    snap = snap_of(gate_repo)
    metas, truncated = projection.artifacts(snap)
    assert truncated is True and len(metas) == 1 and projection.detail(snap).artifacts_truncated is True


# --- summary / detail wire shapes ---
SUMMARY_KEYS = {"repo_id", "repo_label", "space", "intent_dir", "intent_key", "uuid", "slug", "scope",
                "registry_status", "title", "operational_state", "disk", "counts", "open_actions",
                "blocking_findings", "warn_findings", "session", "archived", "paused", "interrupted",
                "last_activity_at", "stable_boundary", "unstable", "is_active_cursor"}
DETAIL_EXTRA_KEYS = {"state_sections", "phases", "stages", "findings", "actions", "directive", "recovery", "markers",
                     "audit_tail", "questions", "artifacts_count", "artifacts_truncated", "git", "binding", "engine",
                     "active_directive_stage"}


@pytest.mark.parametrize("status,waiting", [
    ("Queued", 1), ("NotDelivered", 1), ("ReconciliationRequired", 1), ("Failed", 1),
    ("Delivering", 0), ("Delivered", 0),
])
def test_a_sent_decision_is_not_counted_as_waiting_for_you(projection, gate_snap, status, waiting):
    """A sent decision waits on AI-DLC; the Action Center hides it, so "1 decision waiting" must not count it."""
    body = projection.summarize(gate_snap, live_actions=[ActionStub(status=status)]).to_json()
    assert body["open_actions"] == waiting


def test_intent_summary_wire_shape(projection, gate_snap, C):
    body = projection.summarize(
        gate_snap, binding=BindingStub(slot_key="slot-1"), host=SessionStub(),
        live_actions=[ActionStub(type="gate"), ActionStub(type="revision", action_id="a_2")],
        findings=[FindingStub("receipt_drift", "warn"), FindingStub("dangling_cursor", "blocking")]).to_json()
    assert set(body) == SUMMARY_KEYS
    assert set(body["disk"]) == {"status", "lifecycle_phase", "current_stage", "next_stage", "state_version",
                                 "total_stages", "completed", "revision_count", "parked_at", "last_updated"}
    assert set(body["counts"]) == {"total", "done", "planned", "planned_done", *C.STAGE_STATE}
    assert body["open_actions"] == 1  # the revision card is not a queue item (C18)
    assert body["blocking_findings"] == 1 and body["warn_findings"] == 1
    assert body["session"]["slot_key"] == SessionStub().slot_key and body["is_active_cursor"] is True
    # the STAGE_AWAITING_APPROVAL row, not the later HUMAN_TURN/SESSION rows (NOISE_EVENTS)
    assert body["last_activity_at"] == "2026-09-04T09:00:03Z"
    json.dumps(body)


def test_summary_last_activity_never_counts_noise(projection, snap_of, repo_builder):
    snap = snap_of(build(repo_builder, audit=(
        F.audit_block("STAGE_STARTED", "2026-09-04T08:00:00Z", Stage=GATE_STAGE)
        + F.audit_block("STAGE_AWAITING_APPROVAL", "2026-09-04T08:00:03Z", Stage=GATE_STAGE)
        + F.audit_block("HUMAN_TURN", "2026-09-04T09:59:00Z")
        + F.audit_block("SESSION_STARTED", "2026-09-04T09:59:30Z", Source="startup"))))
    assert projection.summarize(snap).last_activity_at == "2026-09-04T08:00:03Z"


PLACEHOLDERS = ("[Project description]", "[Pasted document provided]", "[Pasted document boundary needs clarification]")


@pytest.mark.parametrize("placeholder", PLACEHOLDERS)
def test_summary_title_drops_every_template_placeholder(projection, snap_of, repo_builder, proj_mod, placeholder):
    """A birth with no usable free text leaves a bracketed template value; the set is pinned whole here."""
    assert proj_mod.PROJECT_PLACEHOLDERS == set(PLACEHOLDERS)
    state = F.state_text(F.DEVDELTA_REMEDIATION, marks={GATE_STAGE: "?"},
                         fields={"Current Stage": GATE_STAGE, "Project": placeholder})
    assert f"- **Project**: {placeholder}" in state
    summary = projection.summarize(snap_of(build(repo_builder, state=state)))
    assert summary.title is None
    # and the state file's current scope wins over the registry's birth-time one (FORMAT-NOTES §2)
    assert summary.scope == "review-major-remediation" and summary.registry_status == "in-flight"


def test_summary_title_is_capped(projection, gate_snap, proj_mod):
    title = projection.summarize(gate_snap).title
    assert title and len(title) == proj_mod.MAX_TITLE_CHARS


def test_intent_detail_is_a_superset_of_the_summary(projection, gate_snap, C):
    body = projection.detail(gate_snap, binding=BindingStub(slot_key="slot-1"), actions=[],
                             findings=[FindingStub("directive_state_digest_mismatch", "info")]).to_json()
    assert set(body) == SUMMARY_KEYS | DETAIL_EXTRA_KEYS
    assert body["active_directive_stage"] == GATE_STAGE and body["questions"]["stage"] == GATE_STAGE
    assert body["engine"]["engine_version"] == C.BUNDLED_ENGINE_VERSION
    assert len(body["stages"]) == 33 and body["phases"][0] == ["Initialization", "Verified"]
    assert body["artifacts_count"] > 0 and body["findings"][0]["code"] == "directive_state_digest_mismatch"
    assert set(body["markers"]) == {"human_turn_at", "engine_touch_at", "turn_counter", "goal_stop_present",
                                    "recovery", "hooks_health", "compose_pending"}
    assert len(body["audit_tail"]) == 4
    assert set(body["audit_tail"][0]) == {"shard", "pos", "timestamp", "event", "fields", "raw"}
    json.dumps(body)


def test_intent_detail_audit_tail_is_capped(projection, snap_of, repo_builder, proj_mod):
    audit = "".join(F.audit_block("HUMAN_TURN", f"2026-09-04T09:{i // 60:02d}:{i % 60:02d}Z") for i in range(80))
    detail = projection.detail(snap_of(build(repo_builder, audit=audit)))
    assert len(detail.audit_tail) == proj_mod.AUDIT_TAIL_LIMIT == 50


def test_active_directive_stage_is_none_when_the_digest_is_stale(projection, snap_of, repo_builder):
    """C06: the engine treats a mismatched marker as absent, so Studio must not show it as live."""
    snap = snap_of(build(repo_builder, directive={"version": 1, "stage": GATE_STAGE, "state_sha256": "0" * 64}))
    assert snap.directive is not None and snap.directive.matches_state is False
    assert projection.detail(snap).active_directive_stage is None
    assert projection.evidence(snap).to_json()["directive"]["matches_state"] is False


def test_intent_key_is_space_qualified_outside_default(projection, proj_mod, repo_record, repo_builder):
    root = repo_builder.with_engine("payload").with_workspace().build()
    alpha = root / "aidlc" / "spaces" / "alpha" / "intents"
    alpha.mkdir(parents=True, exist_ok=True)
    (alpha / "intents.json").write_text("[]\n")
    repo_builder.with_intent(GATE_DIR, state=_gate_state(), audit=F.gate_open_audit(GATE_STAGE), space="alpha")
    snap = projection.snapshot(repo_record(root), "alpha", GATE_DIR)
    assert snap.intent_key == f"alpha~{GATE_DIR}" == proj_mod.intent_key_for("alpha", GATE_DIR)
    assert snap.is_active is False  # the active space is still `default`
    assert _by_type(_seeds(projection, snap))["gate"].dedupe_key.startswith(f"gate:{REPO_ID}:alpha~{GATE_DIR}:")


def test_slug_falls_back_to_the_directory_name_for_an_orphan_record(projection, snap_of, repo_builder):
    root = build(repo_builder)
    (root / "aidlc" / "spaces" / "default" / "intents" / "intents.json").write_text("[]\n")
    summary = projection.summarize(snap_of(root))
    assert summary.uuid is None and summary.registry_status is None
    assert summary.slug == "gate-demo"  # the YYMMDD- prefix is not part of the name


def test_first_unreadable_at_is_carried_for_the_api(projection, repo_record, missing_repo):
    """The reconciler is the only component that has seen the previous ticks (§1.7 grace window)."""
    snap = projection.snapshot(repo_record(missing_repo, availability="unavailable"), "default", GATE_DIR,
                               first_unreadable_at="2026-09-04T09:58:00Z")
    assert snap.first_unreadable_at == "2026-09-04T09:58:00Z" and snap.audit.shards == []


def test_derive_cards_on_an_unreadable_record_offers_no_human_lane_decision(projection, missing_snap):
    """Nothing readable means nothing submittable — the only card is the operator's own recovery card."""
    seeds = _seeds(projection, missing_snap, findings=[FindingStub("state_unreadable", "blocking")])
    assert [seed.type for seed in seeds] == ["recovery", "missing_input"]
    recovery, pick = seeds
    assert recovery.decisions == ("rebind_session", "mark_not_delivered", "acknowledge")
    assert pick.decisions == ("pick_intent",)  # admin lane, not a prompt
    assert all(seed.captured["state_hash"] is None and seed.captured["stable"] is False for seed in seeds)


def test_the_whole_read_model_never_writes_to_the_repository(projection, repo_record, gate_repo):
    """Every public projection surface over one real repository, then a byte-for-byte tree compare (FR-REP-007)."""
    def digest():
        return sorted((path.relative_to(gate_repo).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
                      for path in gate_repo.rglob("*") if path.is_file() and not path.is_symlink())

    before = digest()
    snap = projection.snapshot(repo_record(gate_repo), "default", GATE_DIR)
    findings = [FindingStub("gate_without_audit_row", "blocking")]
    for kw in ({}, {"findings": findings}):
        projection.summarize(snap, **kw)
        projection.detail(snap, **kw)
        projection.evidence(snap, **kw)
        _seeds(projection, snap, install=InstallStub(), breakers=[BreakerStub()], **kw)
    for surface in (projection.map_inputs, projection.artifacts, projection.questions_view, projection.stable_boundary):
        surface(snap)
    projection.captured(snap, GATE_STAGE)
    assert digest() == before


def test_an_oversized_state_file_degrades_without_claiming_an_empty_audit(snap_of, gate_repo, monkeypatch, studio):
    """`too_large` is the real `state_unreadable` input; it must not look like a fresh empty record."""
    monkeypatch.setattr(studio.constants, "MAX_STATE_BYTES", 16)
    snap = snap_of(gate_repo)
    assert snap.state is None and snap.state_snap is None and snap.stable is False
    assert snap.audit.complete is False and snap.audit.shards == []  # nothing read, so nothing complete


def test_a_malformed_intent_dir_is_refused_rather_than_served_as_empty(projection, repo_record, gate_repo, studio):
    with pytest.raises(studio.errors.StudioError) as exc:
        projection.snapshot(repo_record(gate_repo), "default", "../../etc")
    assert exc.value.code == "bad_path"


#: §1.12 `DECISIONS` (action type → allowed decision kinds), mirrored so a card offering a decision
#: `HumanActionBroker.submit` would refuse fails at derivation instead of at the user's click.
CONTRACT_DECISIONS = {
    "gate": ("approve", "request_changes", "accept_as_is"),
    "question": ("answers", "confirm_summary", "accept_assumptions", "convert_assumptions", "approve_plan",
                 "request_plan_changes", "resume"),
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
    """``C.DECISIONS`` is read by the projection, so a wrong shipped entry would otherwise become "correct"."""
    for action_type, allowed in CONTRACT_DECISIONS.items():
        assert C.DECISIONS[action_type] == allowed, action_type
    # the four command types have no derived seed, so only this test covers them
    assert {t: C.DECISIONS[t] for t in ("run", "resume", "force_stop", "prepare_commit")} == {
        "run": ("run",), "resume": ("resume",), "force_stop": ("force_stop",), "prepare_commit": ("prepare_commit",)}
    assert set(C.DECISIONS) == set(C.ACTION_TYPE)
    every = {d for row in C.DECISIONS.values() for d in row}
    assert every == set(C.DECISION_KIND)
    assert every == C.HUMAN_LANE_DECISIONS | C.HOST_CONTROL_DECISIONS | C.STUDIO_ONLY_DECISIONS


def test_no_seed_offers_a_decision_its_type_does_not_allow(projection, snap_of, gate_repo, revising_repo,
                                                           dangling_repo, burst_repo):
    seen: set[tuple[str, str]] = set()
    for path in (gate_repo, revising_repo, dangling_repo, burst_repo):
        for seed in _seeds(projection, snap_of(path), findings=[FindingStub("gate_without_audit_row", "blocking")],
                           breakers=[BreakerStub()], install=InstallStub(), live_actions=[failed_run()]):
            assert set(seed.decisions) <= set(CONTRACT_DECISIONS[seed.type]), (seed.type, seed.decisions)
            seen.update((seed.type, d) for d in seed.decisions)
    # every decision these four repositories can offer was exercised (`provide_input` needs an empty `Scope`)
    assert {d for _, d in seen} == {"approve", "request_changes", "answers", "confirm_summary", "pick_intent",
                                    "acknowledge", "mark_not_delivered", "rebind_session", "retry_now", "keep_paused"}


# --- the review a card promises is the one this run performs ---
def _review_snap(scope: str, override: str = "", scopes: tuple = ()) -> Any:
    from types import SimpleNamespace as NS
    return NS(state=NS(project={"Scope": scope}, scope_config={"Review Override": override}), scopes=scopes)


@pytest.mark.parametrize(("scope_cap", "override", "node_class", "reviewer", "expected"), [
    ("none", "", "advisory", "aidlc-product-lead-agent", None),        # express: no reviewer runs
    (None, "", "adversarial", "aidlc-architect-agent", "adversarial"),
    (None, "advisory", "adversarial", "aidlc-architect-agent", "advisory"),   # the state's override caps it
    ("advisory", "", "adversarial", "aidlc-architect-agent", "advisory"),
    (None, "", None, "aidlc-reviewer", "advisory"),                    # an older graph: a reviewer, no class
    (None, "", None, None, None),
])
def test_a_cards_review_class_is_capped_like_the_plan(proj_mod, scope_cap, override, node_class, reviewer, expected):
    from types import SimpleNamespace as NS
    snap = _review_snap("s", override, scopes=(NS(name="s", review_cap=scope_cap),))
    assert proj_mod._run_review_class(snap, NS(review_class=node_class, reviewer=reviewer)) == expected
    assert proj_mod._run_review_class(snap, None) is None
