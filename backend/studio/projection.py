"""The read model: one coherent look at an intent, turned into everything the UI reads.

**The snapshot** is one *moment*: every read a decision depends on is taken in a fixed order with the state
file re-read last (`AidlcReader.stable_boundary_inputs`). A snapshot that spans a write is `stable=False` and
authorises nothing — the card renders `Refreshing` and `submit` answers `409 unstable_read` — because a gate
approved against a state file that changed mid-read approves a boundary that no longer exists (FR-ACT-009).

**The cards** are derived from disk, never from a gesture: a boundary on disk has exactly one live card and a card
whose boundary left disk is retired, so the human cannot dismiss a `[?]` row and Studio cannot invent one.
`dedupe_key` ties a card to its boundary (§0.1); a partial unique index in `storage.py` keeps one live row per key.

**The operational state** is a first-match-wins ladder whose order is the safety property (`operational_state`).

Pure over its arguments plus bounded reads through `AidlcReader` — no Storage, host or child process — which is
why the reconciler can run it on the scan pool and a test can pin a rule with a fixture repository.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from dataclasses import field as dataclasses_field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from . import constants as C
from .aidlc_reader import (
    AidlcReader,
    ArtifactMeta,
    AuditBundle,
    AuditEvent,
    Directive,
    IntentRow,
    Markers,
    QuestionsFile,
    ReviewFinding,
    ScopeMeta,
    Snapshot,
    StageNode,
    StageRow,
    StateFile,
    boundary_token,
    effective_review_class,
    evidence_digest,
    file_questions_support_forms,
    latest_event,
    match_intent_row,
    parse_review_section,
    stage_attempt,
)
from .errors import StudioError
from .wire import WireRecord

if TYPE_CHECKING:  # pragma: no cover - typing only
    # `consistency` imports this module under TYPE_CHECKING; importing it at runtime would close a cycle.
    from .consistency import ConsistencyEngine
    from .repo_registry import HarnessDir, InstallHealth, RepoRecord

__all__ = [
    "ArtifactMeta",
    "AuditEventView",
    "CardSeed",
    "DiskState",
    "EvidenceSnapshot",
    "IntentDetail",
    "IntentSnapshot",
    "IntentSummary",
    "MarkersView",
    "PRIORITY_GROUP",
    "Projection",
    "QuestionsView",
    "SEVERITY_RANK",
    "StableBoundary",
    "dedupe_key",
    "intent_key_for",
    "priority_group_for",
    "queue_type_for",
]


# --------------------------------------------------------------------------- #
# sort and card tables (§3.5 / §1.8) — data, so the UI mirror and the tests read one source
# --------------------------------------------------------------------------- #

#: Queue band per action type (FR-ACT-003): the table `ui/src/lib/sort.ts` mirrors, pinned by both test suites.
PRIORITY_GROUP: dict[str, int] = {
    **dict.fromkeys(("recovery", "delivery_uncertain"), 1),
    **dict.fromkeys(("gate", "question", "missing_input"), 2),
    **dict.fromkeys(("circuit_breaker", "failure", "install_conflict"), 3),
    **dict.fromkeys(("budget_stop", "revision", "run", "resume", "force_stop", "prepare_commit"), 4),
}

#: `critical` first: those are the cards where Studio does not know what happened.
SEVERITY_RANK: dict[str, int] = {"critical": 0, "blocking": 1, "attention": 2, "info": 3}

ORGANIZE_MODES: tuple[str, ...] = ("priority", "repo", "type", "oldest")

#: Severity, risk class and default consequence variant (§2.11) per derived card type (§1.8). The risk class is the
#: lane the card's decisions use (§1.12): resolved inside Studio is `studio_only` even when the boundary is
#: AI-DLC's, and `install_conflict` only navigates.
_SEED_TABLE: dict[str, tuple[str, str, str | None]] = {
    "gate": ("blocking", "human_lane", "approve_unlocks"),
    "revision": ("info", "read", None),
    "question": ("blocking", "human_lane", "advances_turn"),
    "missing_input": ("blocking", "human_lane", None),
    "recovery": ("critical", "studio_only", "blocked_until_agree"),
    "delivery_uncertain": ("critical", "studio_only", "no_replay"),
    "failure": ("attention", "studio_only", "no_dispatch"),
    "circuit_breaker": ("attention", "studio_only", None),
    "install_conflict": ("attention", "read", "nothing_written"),
    "budget_stop": ("info", "studio_only", None),
}
SEED_SEVERITY: dict[str, str] = {kind: row[0] for kind, row in _SEED_TABLE.items()}
SEED_RISK_CLASS: dict[str, str] = {kind: row[1] for kind, row in _SEED_TABLE.items()}
SEED_CONSEQUENCE: dict[str, str | None] = {kind: row[2] for kind, row in _SEED_TABLE.items()}

#: What each pending checkpoint lets the human decide (C34, review P09).
_CHECKPOINT_DECISIONS: dict[str, tuple[str, ...]] = {
    "summary_confirmation": ("confirm_summary",),
    "assumption_confirmation": ("accept_assumptions", "convert_assumptions"),
    "plan_approval": C.PLAN_REPLY_DECISIONS,
}

#: Blocking findings that never become a `recovery` card: `delivery_uncertain_action` already has one (the
#: uncertain action, re-typed by `queue_type_for`), and `lease_conflict` clears itself when the other operation
#: finishes — a critical card for it would teach the user to ignore critical cards.
RECOVERY_EXCLUDED_FINDINGS: frozenset[str] = frozenset({"delivery_uncertain_action", "lease_conflict"})

#: Action types that represent a turn Studio asked for, as opposed to a boundary it observed. Only
#: these make an intent `Queued` (review P08).
COMMAND_ACTION_TYPES: frozenset[str] = frozenset({"run", "resume", "prepare_commit", "force_stop"})

#: Statuses that re-type a card as `delivery_uncertain` in the queue (§2.11).
UNCERTAIN_STATUSES: frozenset[str] = frozenset({"ReconciliationRequired"})

#: How many audit rows `IntentDetail.audit_tail` carries (§1.8).
AUDIT_TAIL_LIMIT = 50

#: `## Q…` errors aside, `ERROR_LOGGED` is a failure only when dense — this many rows inside the window for the
#: current stage. Isolated rows are the conductor correcting itself (FORMAT-NOTES §6) and raise no card.
ERROR_BURST_COUNT = 3
ERROR_BURST_WINDOW_SECS = 600

#: Template values AI-DLC leaves in `- **Project**:` when an intent was born with no usable text (FORMAT-NOTES §3),
#: never shown as a title. 2.7.1 added the two pasted-document forms.
PROJECT_PLACEHOLDERS = frozenset(
    {"[Project description]", "[Pasted document provided]", "[Pasted document boundary needs clarification]"}
)

MAX_TITLE_CHARS = 160  # `IntentSummary.title` cap (§1.8)

#: Unmet-condition codes `StableBoundary.reasons` can carry (§1.8).
BOUNDARY_REASONS = ("state_unstable", "not_at_boundary", "action_in_flight", "session_busy", "cursor_mismatch")

_DATE_PREFIX_LEN = 6


# --------------------------------------------------------------------------- #
# small pure helpers shared with the broker and the handlers
# --------------------------------------------------------------------------- #


#: §1.8's name for the §0.1 formula; `Storage` mints the same key reading activity back, so it lives in constants.
intent_key_for = C.intent_key_for


def dedupe_key(action_type: str, repo_id: str, intent_key: str, stage: str | None, token: str | None) -> str:
    """`type:repo_id:intent_key:stage_or_-:token` (§0.1).

    The token is the boundary token for a card about a state/audit boundary, and the condition's own identity
    otherwise (breaker fingerprint, install status, finding code), so such a card survives the boundary moving
    underneath it instead of being retired and re-created on every scan.
    """
    return f"{action_type}:{repo_id}:{intent_key}:{stage or '-'}:{token or '-'}"


def queue_type_for(action_type: str, status: str) -> str:
    """The type the queue shows: an uncertain delivery is always shown as what it is (§2.11)."""
    return "delivery_uncertain" if status in UNCERTAIN_STATUSES else action_type


def priority_group_for(queue_type: str) -> int:
    """Band 1..4; an unknown type sorts last rather than raising — it is still a card the user must see."""
    return PRIORITY_GROUP.get(queue_type, 4)


#: Objects other modules own (`BindingView`, `SlotView`, `ActionRecord`, …) are read structurally, which keeps
#: projection free of an import edge to any of them.
_attr = C.attr

#: One implementation for the whole backend; a local one was once an hour off under DST.
_epoch = C.epoch_from_iso


def _json_of(obj: Any) -> Any:
    """A record's `to_json()`, a row's copy."""
    return dict(obj) if isinstance(obj, Mapping) else obj.to_json() if hasattr(obj, "to_json") else None


def _iso_from_ns(mtime_ns: int | None) -> str | None:
    return None if mtime_ns is None else C.iso_from_epoch(mtime_ns / 1_000_000_000)


def _sha12(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:12]


def _capped(read: Any, default: Any) -> tuple[Any, bool]:
    """`(read(), False)`, or `(default, True)` when the read hit its size cap.

    Only `too_large` is absorbed: a `bad_path` must reach the caller as a refusal, never as an absence. The flag
    is for evidence a person reads — "no reviewer verdict" and "a verdict too large to render" differ.
    """
    try:
        return read(), False
    except StudioError as exc:
        if exc.code != "too_large":
            raise
        return default, True


# --------------------------------------------------------------------------- #
# dataclasses
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StageEvidence:
    """What one boundary stage's own directory holds: an intent can carry several `[?]` rows, and a gate card must
    show the artifacts of the stage it is a gate for."""

    stage_dir_rel: str | None
    artifacts: tuple[ArtifactMeta, ...]
    review: tuple[str | None, list[ReviewFinding]] | None
    questions: "QuestionsFile | None"
    review_unreadable: bool = False
    acceptance_criteria: tuple[str, ...] = ()
    artifact_dir_rel: str | None = None


@dataclass(frozen=True, slots=True)
class IntentSnapshot:
    """One coherent read of one intent. The fields after `first_unreadable_at` are additive to §1.8: they are what
    the contract's fixed method signatures need, carried here instead of threading a second argument through."""

    repo_id: str
    space: str
    intent_dir: str
    intent_key: str
    row: IntentRow | None
    state_snap: Snapshot | None
    state: StateFile | None
    directive: Directive | None
    audit: AuditBundle
    markers: Markers
    questions: QuestionsFile | None
    stage_artifacts: tuple[ArtifactMeta, ...]
    review: tuple[str | None, list[ReviewFinding]] | None
    graph: tuple[StageNode, ...]
    grid: dict[str, dict[str, str]]
    scopes: tuple[ScopeMeta, ...]
    engine: "HarnessDir | None"
    taken_at: str
    stable: bool
    is_active: bool
    dangling_cursor: bool
    first_unreadable_at: str | None
    # ---- additive ----
    repo_label: str = ""
    canonical_path: str = ""
    engine_dir: str | None = None
    stage: str | None = None
    unit: str | None = None
    stage_dir_rel: str | None = None
    units: tuple[str, ...] = ()
    cursor: str | None = None
    active_space: str = C.DEFAULT_SPACE
    layout: str | None = None
    acceptance_criteria: tuple[str, ...] = ()
    #: The reviewer's verdict exists but sits in a file over the render cap, so `review` is `None` for a reason
    #: that is not "there is no reviewer".
    review_unreadable: bool = False
    #: Evidence for every open boundary row that is NOT `stage`; empty for the usual single-boundary intent.
    other_stage_evidence: "Mapping[str, StageEvidence]" = dataclasses_field(default_factory=dict)
    artifact_dir_rel: str | None = None
    # Internal read evidence for audit questions, including after the pending question disappears.
    audit_question_attempt: tuple[str | None, bool] | None = None

    def stage_evidence(self, slug: str | None) -> StageEvidence:
        """The evidence of `slug`, current stage or not: one accessor, so a gate card for a second `[?]` row can
        never be handed the current stage's artifacts, verdict and questions to approve."""
        if slug is not None and slug != self.stage and slug in self.other_stage_evidence:
            return self.other_stage_evidence[slug]
        return StageEvidence(self.stage_dir_rel, self.stage_artifacts, self.review, self.questions,
                             self.review_unreadable, self.acceptance_criteria, self.artifact_dir_rel)

    @property
    def state_sha256(self) -> str | None:
        return self.state_snap.sha256 if self.state_snap is not None else None

    def node(self, slug: str | None) -> StageNode | None:
        return next((node for node in self.graph if node.slug == slug), None) if slug else None

    def row_for(self, slug: str | None) -> StageRow | None:
        return self.state.row(slug) if slug and self.state is not None else None


@dataclass(frozen=True, slots=True)
class DiskState(WireRecord):
    """What the state file claims, unjudged (§2.3 `disk`). `total_stages`/`completed` stay the file's claims, not
    counts: the difference is the evidence `completed_count_mismatch` / `total_count_mismatch` report."""

    status: str | None
    lifecycle_phase: str | None
    current_stage: str | None
    next_stage: str | None
    state_version: int | None
    total_stages: int | None
    completed: int | None
    revision_count: int
    parked_at: str | None
    last_updated: str | None


def run_unavailable_reason(
    snap: IntentSnapshot, *, stage: str | None = None, unit: str | None = None,
) -> str | None:
    """Why Run/Resume no longer fits a coherent snapshot, judging no other command.

    A revision row still needs agent work; only approval and question checkpoints need a human. Missing or moving
    evidence cannot prove a queued request obsolete.
    """
    if not snap.stable or snap.state is None:
        return None
    if any(row.mark == "?" for row in snap.state.stages):
        return "approval_pending"
    if snap.questions and (snap.questions.pending_count or snap.questions.pending_checkpoint):
        return "question_pending"
    if (snap.state.status or "").lower() == "completed":
        return "intent_completed"
    if stage is not None and snap.stage is not None and (stage != snap.stage or unit != snap.unit):
        return "stage_changed"
    return None


@dataclass(frozen=True, slots=True)
class StableBoundary(WireRecord):
    """Whether this intent sits still enough for a decision (PRD §12.2). All five conditions are evaluated and the
    unmet ones named, because "not stable" alone gives the user nothing to fix."""

    stable: bool
    reasons: tuple[str, ...]
    stage: str | None
    marker: str | None
    boundary_token: str | None
    #: When this boundary was FIRST observed. The time of the latest look made every two ticks differ, so the binder
    #: wrote an fsync'd row and the reconciler published `intent.updated` per intent per tick for an idle app.
    recorded_at: str

    def identity(self) -> tuple[Any, ...]:
        """Everything that makes this boundary *this* boundary, excluding `recorded_at`."""
        return (self.stable, tuple(self.reasons), self.stage, self.marker, self.boundary_token)


@dataclass(frozen=True, slots=True)
class MarkersView(WireRecord):
    """Runtime markers as timestamps a person can compare with the audit trail (§3.1), not `mtime_ns`."""

    human_turn_at: str | None
    engine_touch_at: str | None
    turn_counter: int | None
    goal_stop_present: bool
    recovery: dict[str, str] | None
    hooks_health: dict[str, str]
    compose_pending: bool

    @classmethod
    def of(cls, markers: Markers | None) -> "MarkersView":
        if markers is None:
            return cls(None, None, None, False, None, {}, False)
        return cls(
            _iso_from_ns(markers.human_turn_mtime_ns), _iso_from_ns(markers.engine_touch_mtime_ns),
            markers.turn_counter, markers.goal_stop_present, dict(markers.recovery) if markers.recovery else None,
            dict(markers.hooks_health), markers.compose_pending,
        )

    def card_json(self) -> dict[str, Any]:
        """The §2.11 card subset: hook health moves on every session start and would make two evidence snapshots of
        one boundary differ for no decision-relevant reason (FR-ADV-007)."""
        return {k: v for k, v in self.to_json().items() if k not in ("hooks_health", "compose_pending")}


@dataclass(frozen=True, slots=True)
class AuditEventView(WireRecord):
    shard: str
    pos: int
    timestamp: str
    event: str
    fields: dict[str, str]
    raw: str

    @classmethod
    def of(cls, event: AuditEvent) -> "AuditEventView":
        return cls(event.shard, event.pos, event.timestamp, event.event, dict(event.fields), event.raw)


@dataclass(frozen=True, slots=True)
class QuestionsView(WireRecord):
    """A questions file plus where it lives and how much of it the UI may drive (§2.11). File-backed questions keep
    their host-dependent mode (Appendix B6); a proven audit enum is structured on its own, `origin` naming the
    disk event that supplied its labels."""

    _wire_keys = (
        "relpath", "sha256", "stage", "unit", "questions", "summary_confirmation", "plan_approval",
        "assumption_confirmation?", "pending_count", "unsupported_pending_count", "pending_checkpoint", "mode",
        "host_card", "origin?",
    )
    relpath: str
    sha256: str
    stage: str
    unit: str | None
    questions: tuple[dict, ...]
    summary_confirmation: Any | None
    plan_approval: Any | None
    pending_count: int
    pending_checkpoint: str | None
    mode: str
    host_card: dict | None
    origin: dict[str, Any] | None = None
    unsupported_pending_count: int = 0
    assumption_confirmation: Any | None = None


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot(WireRecord):
    """What the human is asked to decide on, frozen at derivation time (§2.11 `evidence`).

    Stored verbatim in `actions.evidence_json`, so the card still shows what was true when the decision was offered.
    The three reconciler-owned keys are `null` here and filled in on the same dict later, so the wire shape of a
    fresh card and a reconciled one never differs.
    """

    state: dict | None
    audit: dict
    directive: dict | None
    artifacts: tuple[ArtifactMeta, ...]
    review: dict | None
    acceptance_criteria: tuple[dict, ...]
    findings: tuple[Any, ...]
    session: dict | None
    questions: QuestionsView | None
    git: Any | None
    markers: dict
    presence_baseline: None = None
    presence: None = None
    cursor_readback: None = None


@dataclass(frozen=True, slots=True)
class CardSeed:
    """Everything `HumanActionBroker.upsert_derived` needs to insert or refresh one card. Not an action — no status,
    id or delivery state: it says "this boundary is on disk now", and the broker decides what that means."""

    type: str
    repo_id: str
    space: str
    intent_dir: str
    intent_key: str
    intent_uuid: str | None
    stage: str | None
    unit: str | None
    severity: str
    risk_class: str
    captured: dict
    evidence: dict
    dedupe_key: str
    waiting_since: str
    decisions: tuple[str, ...]
    headline_params: dict
    consequence_variant: str | None
    stage_ref: dict | None = None
    source: str = "studio"
    reason: str | None = None
    related_action_id: str | None = None


@dataclass(frozen=True, slots=True)
class IntentSummary(WireRecord):
    repo_id: str
    repo_label: str
    space: str
    intent_dir: str
    intent_key: str
    uuid: str | None
    slug: str
    scope: str | None
    registry_status: str | None
    title: str | None
    operational_state: str
    disk: DiskState
    counts: dict[str, int]
    open_actions: int
    blocking_findings: int
    warn_findings: int
    session: Any | None
    archived: bool
    paused: bool
    interrupted: bool
    last_activity_at: str | None
    stable_boundary: Any | None
    unstable: bool
    is_active_cursor: bool


@dataclass(frozen=True, slots=True)
class IntentDetail(IntentSummary):
    """`IntentSummary` plus everything the single-intent page reads. A subclass, so the JSON is a superset of the
    summary's (the frontend type `extends` it too) and the rail and the page can never disagree on a field."""

    state_sections: dict[str, dict[str, str]] = dataclasses_field(default_factory=dict)
    phases: tuple[tuple[str, str], ...] = ()
    stages: tuple[StageRow, ...] = ()
    findings: tuple[Any, ...] = ()
    actions: tuple[Any, ...] = ()
    directive: Directive | None = None
    recovery: dict[str, str] | None = None
    markers: MarkersView = dataclasses_field(default_factory=lambda: MarkersView.of(None))
    audit_tail: tuple[AuditEventView, ...] = ()
    questions: QuestionsView | None = None
    artifacts_count: int = 0
    artifacts_truncated: bool = False
    git: Any | None = None
    binding: Any | None = None
    engine: Any | None = None
    active_directive_stage: str | None = None


# --------------------------------------------------------------------------- #
# the projection
# --------------------------------------------------------------------------- #


class Projection:
    """Sync read model. Reads through `AidlcReader`; touches nothing else."""

    def __init__(self, reader: AidlcReader, consistency: "ConsistencyEngine", clock: C.Clock) -> None:
        """`consistency` is held, never called: every finding is passed in (§1.8), because what blocks a decision
        also depends on the reconciler's `RepoFacts` (leases, live actions, first-unreadable time)."""
        self._reader = reader
        self._consistency = consistency
        self._clock = clock

    # -- snapshot ----------------------------------------------------------- #

    def snapshot(
        self, repo: "RepoRecord", space: str, intent_dir: str, *, first_unreadable_at: str | None = None
    ) -> IntentSnapshot:
        """One coherent read of one intent.

        An oversized or unreadable state file yields `state=None, stable=False` — what `state_unreadable` needs —
        rather than raising, which would hide every other intent of the repository. `first_unreadable_at` is the
        reconciler's memory of earlier ticks, carried so the API shows what `RepoFacts` gives consistency.
        """
        root = Path(repo.canonical_path)
        engine_dir = repo.engine_dir or C.STUDIO_HARNESS_DIR
        harness = self._harness(root, engine_dir)
        engine_dir = harness.dir if harness is not None else engine_dir
        try:
            inputs = self._reader.stable_boundary_inputs(root, space, intent_dir)
        except StudioError as exc:
            # `too_large` only: a `bad_path` must answer 400/404, never serve a plausible empty intent.
            if exc.code != "too_large":
                raise
            inputs = None
        if inputs is None:
            state_snap = state = directive = questions = stage = None
            # complete=False: "we did not look" is not "there is nothing" (`audit_incomplete`).
            audit, stable, taken_at = AuditBundle(events=[], shards=[], complete=False), False, self._clock.iso()
        else:
            state_snap, state, directive, questions, stage = (
                inputs.state_snap, inputs.state, inputs.directive, inputs.questions, inputs.stage)
            audit, stable, taken_at = inputs.audit, bool(inputs.stable), inputs.taken_at

        active_space = self._reader.active_space(root)
        cursor, dangling = self._reader.active_intent(root, space)
        row = match_intent_row(self._reader.registry(root, space), intent_dir)
        graph = tuple(self._reader.read_stage_graph(root, engine_dir))
        grid = self._reader.read_scope_grid(root, engine_dir)
        scopes = tuple(self._reader.read_scopes(root, engine_dir))
        markers = self._reader.read_markers(root, space, intent_dir)
        # `list_units` re-reads the state file: an oversized one costs the (presentational) units, not the snapshot.
        units = _capped(lambda: tuple(self._reader.list_units(root, space, intent_dir)), ())[0]
        unit = directive.unit if directive is not None and directive.matches_state else None
        if unit is None and state is not None:
            unit = state.active_unit
        audit_question_attempt = self._reader._question_claim_generation(root, space, intent_dir, unit, audit)
        if questions is not None and questions.origin and audit_question_attempt != (
            questions.origin.get("attempt_generation"), True
        ):
            questions = None

        current = self._read_stage_evidence(
            root, engine_dir, space, intent_dir, state, graph, stage, unit, questions
        )
        # Every other open boundary gets the same read, so a gate card never shows another stage's evidence.
        # Usually empty: an intent normally has one open boundary, and it is the current stage.
        others: dict[str, StageEvidence] = {}
        for boundary in (state.stages if state is not None else ()):
            if boundary.mark in ("?", "R", "r") and boundary.slug != stage and boundary.slug not in others:
                others[boundary.slug] = self._read_stage_evidence(
                    root, engine_dir, space, intent_dir, state, graph, boundary.slug, None, None
                )
        return IntentSnapshot(
            repo_id=repo.repo_id, space=space, intent_dir=intent_dir, intent_key=intent_key_for(space, intent_dir),
            row=row, state_snap=state_snap, state=state, directive=directive, audit=audit, markers=markers,
            questions=questions, stage_artifacts=current.artifacts, review=current.review,
            review_unreadable=current.review_unreadable, graph=graph, grid=grid, scopes=scopes, engine=harness,
            taken_at=taken_at, stable=stable, is_active=(active_space == space and cursor == intent_dir),
            dangling_cursor=dangling, first_unreadable_at=first_unreadable_at, repo_label=repo.label,
            canonical_path=repo.canonical_path, engine_dir=engine_dir, stage=stage, unit=unit,
            stage_dir_rel=current.stage_dir_rel, units=units, cursor=cursor, active_space=active_space,
            layout=self._reader.layout(root), acceptance_criteria=current.acceptance_criteria,
            other_stage_evidence=others, artifact_dir_rel=current.artifact_dir_rel,
            audit_question_attempt=audit_question_attempt,
        )

    def _harness(self, root: Path, preferred: str) -> "HarnessDir | None":
        """The registered harness, else the first with a `tools/` tree: a repository can hold two, and the wrong one
        would read another framework's stage graph."""
        dirs = self._reader.detect_harness_dirs(root)
        return (next((h for h in dirs if h.dir == preferred), None) or next((h for h in dirs if h.has_utility), None)
                or (dirs[0] if dirs else None))

    @staticmethod
    def row_phase(state: StateFile | None, stage: str | None) -> str | None:
        row = state.row(stage) if (state is not None and stage) else None
        return row.phase if row is not None else None

    def _read_stage_evidence(
        self, root: Path, engine_dir: str, space: str, intent_dir: str, state: "StateFile | None",
        graph: Sequence[StageNode], slug: str | None, unit: str | None, questions: "QuestionsFile | None",
    ) -> StageEvidence:
        """One stage directory's artifacts, verdict, questions and criteria.

        `questions` arrives for the current stage (the boundary read found it, and it proves which Construction
        layout the record uses). `too_large` is tolerated throughout: a snapshot that raises makes the reconciler
        skip the intent, so one artifact over the render cap once cost an on-disk boundary its card.
        """
        node = next((n for n in graph if n.slug == slug), None)
        stage_dir_rel = self._stage_dir_rel(space, intent_dir, state, node, slug, unit, questions)
        if stage_dir_rel is None:
            return StageEvidence(None, (), None, questions)
        # Reverse engineering writes shared repository knowledge; its questions and notes stay in the record.
        artifact_dir_rel = stage_dir_rel
        if C.artifact_output_location(node) == "codekb":
            artifact_dir_rel = f"aidlc/spaces/{space}/codekb/{root.name}"
        artifacts, artifacts_capped = _capped(
            lambda: tuple(self._reader.stage_artifacts(root, artifact_dir_rel, node)), ()
        )
        review, review_capped = _capped(lambda: self._review_of(root, artifacts, node), None)
        primary = self._primary_artifact(artifacts, node)
        judged = review_capped or (review is not None and (review[0] is not None or bool(review[1])))
        # A Gate opens on the first artifact, so the one the stage is judged on leads: in name order
        # `requirements-analysis-questions.md` sorts before `requirements.md`, and the Gate showed the questions.
        # Where no reviewer judged it, the stage's own account of what it did (`code-summary`,
        # `build-and-test-summary`) leads instead of the first file it declares (`build-instructions`).
        lead = primary if judged else (self._summary_artifact(artifacts, node) or primary)
        if lead is not None and artifacts[0] is not lead:
            artifacts = (lead, *(a for a in artifacts if a is not lead))
        if questions is None and slug:
            questions = _capped(lambda: self._reader.read_questions(root, stage_dir_rel, slug), None)[0]
        criteria: tuple[str, ...] = ()
        phase = node.phase if node is not None else (self.row_phase(state, slug) or "")
        if phase:
            meta = _capped(lambda: self._reader.read_stage_file(root, engine_dir, phase, slug or ""), None)[0]
            criteria = tuple(meta.acceptance_criteria) if meta is not None else ()
        return StageEvidence(stage_dir_rel, artifacts, review, questions, review_capped or artifacts_capped,
                             criteria, artifact_dir_rel)

    def _stage_dir_rel(
        self, space: str, intent_dir: str, state: StateFile | None, node: StageNode | None, stage: str | None,
        unit: str | None, questions: QuestionsFile | None,
    ) -> str | None:
        """Where a stage's files live: the questions file the reader found proves which Construction layout
        (`construction/<unit>/<slug>` or `construction/<slug>`) the record uses, so it wins over composing one."""
        if not stage:
            return None
        if questions is not None and questions.relpath.endswith(f"/{stage}-questions.md"):
            return questions.relpath.rsplit("/", 1)[0]
        phase = (node.phase if node is not None else None) or self.row_phase(state, stage)
        if phase not in C.PHASES:
            return None
        return self._reader.stage_dir(space, intent_dir, phase, stage, unit)

    def _review_of(
        self, root: Path, artifacts: Sequence[ArtifactMeta], node: StageNode | None
    ) -> tuple[str | None, list[ReviewFinding]] | None:
        """The parsed `## Review` of the stage's primary artifact: a verdict lives inside the artifact it judges."""
        primary = self._primary_artifact(artifacts, node)
        snap = self._reader.read_artifact(root, primary.relpath) if primary is not None else None
        return parse_review_section(snap.text) if snap is not None else None

    @staticmethod
    def _primary_artifact(artifacts: Sequence[ArtifactMeta], node: StageNode | None) -> ArtifactMeta | None:
        """The one artifact a stage's `## Review` appendix can be in.

        `review_artifact` (2.7.1+) wins over `produces[0]`: on four stages they disagree (`functional-design`
        reviews `functional-spec` but produces `entities` first), and the reviewer protocol writes the appendix
        into `review_artifact` only. Older graphs declare none, hence the `produces` scan behind it.
        """
        by_name = {a.name: a for a in artifacts}
        declared = ([node.review_artifact] if node is not None and node.review_artifact else []) + (
            list(node.produces) if node is not None else [])
        for name in declared:
            for candidate in (f"{name}.md", f"{name}.json", name):
                if candidate in by_name:
                    return by_name[candidate]
        return next((a for a in artifacts if a.kind == "artifact"), None)

    @staticmethod
    def _summary_artifact(artifacts: Sequence[ArtifactMeta], node: StageNode | None) -> ArtifactMeta | None:
        """The summary product a stage declares and wrote (`code-summary`, `build-and-test-summary`), if any."""
        by_name = {a.name: a for a in artifacts}
        names = [f"{name}.md" for name in (node.produces if node is not None else ()) if name.endswith("summary")]
        return next((by_name[name] for name in names if name in by_name), None)

    # -- captured / evidence ------------------------------------------------ #

    def captured(self, snap: IntentSnapshot, stage: str | None) -> dict[str, Any]:
        """The compare-and-submit set for one boundary (§1.6.4 / C30).

        Built the same way for every card type and again at submit time — the only way the null-vs-non-null rule
        can work (review P26.3): a gate captured before its questions file existed must go stale when one appears.
        The digests come from the stage's own directory for every boundary the snapshot read, so a second gate's
        evidence can invalidate it too; a stage the snapshot did not read digests to `None`.
        """
        state_sha = snap.state_sha256
        token = None
        if state_sha:
            token = boundary_token(
                state_sha, list(snap.audit.events),
                snap.state.current_stage if snap.state is not None else None,
                snap.state.revision_count if snap.state is not None else 0,
            )
        evidence = snap.stage_evidence(stage)
        known = stage is not None and (stage == snap.stage or stage in snap.other_stage_evidence)
        return {
            "state_hash": state_sha,
            "boundary_token": token,
            "question_digest": evidence.questions.sha256 if known and evidence.questions is not None else None,
            "stage_attempt": stage_attempt(list(snap.audit.events), stage) if stage else None,
            "evidence_digest": evidence_digest(list(evidence.artifacts), evidence.review) if known else None,
            "is_active": snap.is_active,
            "captured_at": snap.taken_at,
            "stable": snap.stable,
        }

    def evidence(
        self, snap: IntentSnapshot, *, findings: Sequence[Any] = (), binding: Any | None = None,
        slot: Any | None = None, git: Any | None = None, host_card: dict | None = None, stage: str | None = None,
    ) -> EvidenceSnapshot:
        """The evidence a card shows, for `stage`'s boundary (default: the current stage).

        Only the stage directory differs per boundary: built once and shared, it showed a second `[?]` row's card
        the current stage's artifacts, verdict and questions, under the right stage's name.
        """
        target = stage if stage is not None else snap.stage
        bundle = snap.stage_evidence(target)
        state, files = snap.state, snap.state_snap
        state_json = None if files is None else {
            "relpath": files.relpath, "sha256": files.sha256, "size": files.size, "mtime_ns": files.mtime_ns,
            "current_stage": state.current_stage if state is not None else None,
            "status": state.status if state is not None else None,
            "lifecycle_phase": state.lifecycle_phase if state is not None else None,
            "revision_count": state.revision_count if state is not None else 0,
            "stable": snap.stable,
        }
        events = list(snap.audit.events)
        last = events[-1] if events else None
        boundary = latest_event(events, C.BOUNDARY_EVENTS)
        audit_json = {
            "shards": [
                {"relpath": shard.relpath, "size": shard.size, "truncated": shard.truncated}
                for shard in snap.audit.shards
            ],
            "last_event": {"type": last.event, "ts": last.timestamp} if last is not None else None,
            "boundary_event": None if boundary is None else {
                "type": boundary.event, "ts": boundary.timestamp, "stage": boundary.fields.get("Stage"),
                "shard": boundary.shard, "pos": boundary.pos,
            },
            "complete": snap.audit.complete,
        }
        node = snap.node(target)
        review_class = _run_review_class(snap, node)
        review_json: dict | None = None
        if bundle.review is not None:
            verdict, found = bundle.review
            review_json = {
                "verdict": verdict,
                "findings": [f.to_json() for f in found],
                "reviewer": next((f.reviewer for f in found if f.reviewer), None)
                or (node.reviewer if node is not None and review_class is not None else None),
                "review_class": review_class,
                "iteration": next((f.iteration for f in found if f.iteration is not None), None),
                # False when the findings were in a shape Studio could not enumerate: an empty list then means
                # "could not tell", which the card must not present as "no open blockers" (§2.11).
                "findings_parsed": bool(getattr(found, "findings_parsed", True)),
            }
        elif bundle.review_unreadable:  # a verdict over the render cap is not "no reviewer"
            review_json = {"verdict": None, "findings": [], "reviewer": None, "review_class": review_class,
                           "iteration": None, "findings_parsed": False, "unreadable": True}
        return EvidenceSnapshot(
            state=state_json, audit=audit_json,
            directive=snap.directive.to_json() if snap.directive is not None else None,
            artifacts=bundle.artifacts, review=review_json,
            # `met` stays null: whether a criterion is met is the reviewer's call inside AI-DLC.
            acceptance_criteria=tuple({"text": t, "met": None, "why_key": None} for t in bundle.acceptance_criteria),
            findings=tuple(findings), session=self._session_json(binding, slot),
            questions=self.questions_view(snap, host_card=host_card, stage=target), git=git,
            markers=MarkersView.of(snap.markers).card_json(),
        )

    @staticmethod
    def _session_json(binding: Any | None, slot: Any | None) -> dict | None:
        slot_key = _attr(binding, "slot_key") or _attr(slot, "key")
        if not slot_key:
            return None
        return {
            "slot_key": slot_key,
            "session_key": _attr(binding, "session_key") or _attr(slot, "session_key") or "",
            "running": bool(_attr(slot, "running", False)),
            "stop_state": str(_attr(slot, "stop_state", "idle")),
            "last_turn_ts": _attr(slot, "last_turn_ts") or None,
            "queue_depth": int(_attr(slot, "queue_depth", 0) or 0),
            "busy_reasons": list(_attr(slot, "busy_reasons", ()) or ()),
            # The choices the conversation's last reply offered (its `[OPTIONS: …]`), while no turn runs: a card
            # names the ones it cannot send instead of hiding them.
            "options": [] if _attr(slot, "running", False) else [str(o) for o in (_attr(slot, "options", ()) or ())],
        }

    def questions_view(
        self, snap: IntentSnapshot, *, host_card: dict | None = None, stage: str | None = None
    ) -> QuestionsView | None:
        """The questions a card offers for `stage` (default: the current one). `unit` is known for the current
        stage only: another boundary's per-unit path was resolved from its own directory."""
        target = stage if stage is not None else snap.stage
        questions = snap.stage_evidence(target).questions
        if questions is None or not target:
            return None
        native_wait = host_card is not None and "ask_id" in host_card
        file_form = (
            (C.GROUPED_ANSWERS_VERIFIED or bool(host_card))
            and snap.stable and file_questions_support_forms(questions)
        )
        return QuestionsView(
            relpath=questions.relpath, sha256=questions.sha256, stage=target,
            unit=snap.unit if target == snap.stage else None,
            # `raw` duplicates prompt and options; every question in a group must be answered.
            questions=tuple({k: v for k, v in q.to_json().items() if k != "raw"} | {"required": True}
                            for q in questions.questions),
            summary_confirmation=questions.summary_confirmation, plan_approval=questions.plan_approval,
            assumption_confirmation=getattr(questions, "assumption_confirmation", None),
            pending_count=questions.pending_count, pending_checkpoint=questions.pending_checkpoint,
            mode="structured" if not native_wait and (questions.origin or file_form) else "degraded",
            host_card=dict(host_card) if host_card else None, origin=questions.origin,
            unsupported_pending_count=questions.unsupported_pending_count,
        )

    # -- operational state -------------------------------------------------- #

    def operational_state(
        self, snap: IntentSnapshot, *, binding: Any | None = None, live_actions: Sequence[Any] = (),
        breaker_open: bool = False, host: Any | None = None, findings: Sequence[Any] = (), slot: Any | None = None,
    ) -> str:
        """PRD §11.2 as a first-match-wins ladder whose order is the safety property.

        `ReconciliationRequired` outranks all but `Archived`: an unresolved delivery must never look runnable.
        `Interrupted` holds only while the force stop is in flight — then `interrupted_session` takes over through
        rule 2, so `Interrupted → Queued` is unreachable (C28, P17). Only *command* actions (and anything
        `Delivering`) make an intent `Queued`: a derived card waiting in `Queued` waits for the human, and ranking
        it `Queued` would hide every open gate behind a spinner (P08).
        """
        statuses = [str(_attr(action, "status", "")) for action in live_actions]
        types = [str(_attr(action, "type", "")) for action in live_actions]
        if str(_attr(binding, "archive_state", "active")) == "archived":
            return "Archived"
        blocking = any(str(_attr(f, "severity", "")) == "blocking" for f in findings)
        if blocking or any(status in UNCERTAIN_STATUSES for status in statuses):
            return "ReconciliationRequired"
        slot_running = bool(_attr(host, "running", False)) or bool(_attr(slot, "running", False))
        slot_stopping = bool(_attr(slot, "stopping", False)) or str(_attr(slot, "stop_state", "idle")) != "idle"
        if _attr(binding, "interrupted_at") and (slot_running or slot_stopping):
            return "Interrupted"
        if breaker_open:
            return "CircuitOpen"
        state = snap.state
        if state is not None and (state.status or "").strip().lower() == "completed":
            return "Completed"
        if state is not None and state.parked_at:
            return "Parked"
        if slot_running or any(status in ("Delivered", "Processing") for status in statuses):
            return "Running"
        commands_queued = any(
            kind in COMMAND_ACTION_TYPES and status in ("Queued", "Delivering")
            for kind, status in zip(types, statuses)
        )
        if commands_queued or "Delivering" in statuses:
            return "Queued"
        if self._waiting_for_human(snap):
            return "WaitingForYou"
        newest = self._newest_action(live_actions)
        if newest is not None and str(_attr(newest, "status", "")) == "Failed":
            return "Failed" if self._deterministic(newest) or breaker_open else "RetryEligible"
        return "Paused" if bool(_attr(binding, "paused", False)) else "Idle"

    @staticmethod
    def _waiting_for_human(snap: IntentSnapshot) -> bool:
        if snap.state is not None and any(row.mark in ("?", "R", "r") for row in snap.state.stages):
            return True
        questions = snap.questions
        return questions is not None and (questions.pending_count > 0 or bool(questions.pending_checkpoint))

    @staticmethod
    def _newest_action(actions: Sequence[Any]) -> Any | None:
        return max(actions, key=lambda a: (
            str(_attr(a, "updated_at", "") or ""), str(_attr(a, "created_at", "") or ""),
            str(_attr(a, "action_id", "") or ""),
        )) if actions else None

    @staticmethod
    def _deterministic(action: Any) -> bool:
        """Whether retrying cannot fix this failure. An unknown class counts as deterministic: `Retry now` for a
        failure Studio cannot classify invites a replay that fails the same way."""
        klass = _attr(_attr(action, "evidence", {}) or {}, "fingerprint_class")
        if not klass:
            fingerprint = _attr(action, "retry_fingerprint") or ""
            klass = str(fingerprint).split(".", 1)[0] if fingerprint else ""
        return str(klass) not in C.TRANSIENT_FINGERPRINT_CLASSES

    # -- stable boundary ---------------------------------------------------- #

    def stable_boundary(
        self, snap: IntentSnapshot, *, live_actions: Sequence[Any] = (), busy_reasons: Sequence[str] = (),
        lease_active: bool = False,
    ) -> StableBoundary:
        """All five PRD §12.2 conditions, every unmet one named. The marker and token are the projection's own
        reading of the snapshot; the reconciler supplies the three facts that are not on disk."""
        reasons: list[str] = []
        if not snap.stable:
            reasons.append("state_unstable")
        marker = self._boundary_marker(snap)
        stage = self._boundary_stage(snap, marker)
        if marker is None:
            reasons.append("not_at_boundary")
        in_flight = ("Delivering", "Delivered", "Processing", "DeliveryUncertain")
        if any(str(_attr(action, "status", "")) in in_flight for action in live_actions):
            reasons.append("action_in_flight")
        if tuple(busy_reasons) or lease_active:
            reasons.append("session_busy")
        if not snap.is_active and snap.cursor is not None:
            reasons.append("cursor_mismatch")
        return StableBoundary(
            stable=not reasons, reasons=tuple(reasons), stage=stage, marker=marker,
            boundary_token=self.captured(snap, stage)["boundary_token"], recorded_at=snap.taken_at,
        )

    @staticmethod
    def _boundary_marker(snap: IntentSnapshot) -> str | None:
        state = snap.state
        if state is not None:
            if any(row.mark == "?" for row in state.stages):
                return "[?]"
            if any(row.mark in ("R", "r") for row in state.stages):
                return "[R]"
        questions = snap.questions
        if questions is not None and (questions.pending_count > 0 or questions.pending_checkpoint):
            return "question"
        if state is not None and state.parked_at:
            return "parked"
        if state is not None and (state.status or "").strip().lower() == "completed":
            return "completed"
        events = list(snap.audit.events)
        newest_done = latest_event(events, ("PHASE_COMPLETED", "STAGE_COMPLETED"))
        newest_start = latest_event(events, ("STAGE_STARTED",))
        directive_live = snap.directive is not None and snap.directive.matches_state
        if newest_done is not None and not directive_live:
            if newest_start is None or newest_done.sort_key > newest_start.sort_key:
                return "phase_complete"
        return None

    @staticmethod
    def _boundary_stage(snap: IntentSnapshot, marker: str | None) -> str | None:
        """The marker's stage, which is not always `Current Stage` (the engine advances it before the previous gate
        closes in some paths): a boundary recorded against the wrong stage would authorise the wrong gate."""
        wanted = {"[?]": ("?",), "[R]": ("R", "r")}.get(marker or "")
        if snap.state is not None and wanted:
            for row in snap.state.stages:
                if row.mark in wanted:
                    return row.slug
        return snap.stage

    # -- card derivation ---------------------------------------------------- #

    def derive_cards(
        self, snap: IntentSnapshot, *, findings: Sequence[Any], binding: Any | None, install: "InstallHealth | None",
        breakers: Sequence[Any], live_actions: Sequence[Any], slot: Any | None = None, git: Any | None = None,
        host_card: dict | None = None,
    ) -> list[CardSeed]:
        """One seed per live boundary, in queue order (§1.8 table).

        No `delivery_uncertain` seed: that card is the uncertain action, re-typed by `queue_type_for`, and a seed
        would be the second live row for one decision that PRD §12.1 forbids. `budget_stop` is reserved: there are
        no unattended turns without the machine lane (C16).
        """
        def evidence(stage: str | None = None) -> dict:
            return self.evidence(snap, findings=findings, binding=binding, slot=slot, git=git, host_card=host_card,
                                 stage=stage).to_json()

        base = evidence()
        seeds: list[CardSeed] = []
        state, events = snap.state, list(snap.audit.events)

        # One recovery card for every blocking finding a human must agree on: the move is the same however many
        # contradictions there are, and N critical cards for one broken intent would bury every other repository.
        codes = {str(_attr(f, "code", "")) for f in findings if str(_attr(f, "severity", "")) == "blocking"}
        codes -= RECOVERY_EXCLUDED_FINDINGS
        if _attr(binding, "slot_key") and slot is None and state is not None and any(
            row.mark == "-" for row in state.stages
        ):  # the bound slot is gone mid-stage; `slot is None` without a binding is "not bound", not a loss
            codes.add("session_lost_mid_stage")
        if codes:
            ordered = sorted(codes)
            seeds.append(self._seed(
                snap, "recovery", snap.stage, {**base, "recovery_codes": ordered}, snap.taken_at,
                ("rebind_session", "mark_not_delivered", "acknowledge"),
                {"stage": snap.stage, "codes": list(ordered)}, fallback=_sha12(*ordered), reason=ordered[0],
            ))

        for row in state.stages if state is not None else ():
            if row.mark not in ("?", "R", "r"):
                continue
            # A second open boundary shows its own stage's evidence, or its card would show another stage's work.
            own = base if row.slug == snap.stage or row.slug not in snap.other_stage_evidence else evidence(row.slug)
            if row.mark == "?":
                # The mark is the gate: only AI-DLC clears it, so the card cannot be dismissed (FR-ACT-007).
                # "Accept as-is" exists after three rejection cycles (05 §5.1), so Studio offers it then (C29/P24).
                attempt = stage_attempt(events, row.slug)
                seeds.append(self._seed(
                    snap, "gate", row.slug, own, self._gate_opened_at(snap, row.slug),
                    [d for d in C.DECISIONS["gate"] if d != "accept_as_is" or attempt >= C.ACCEPT_AS_IS_MIN_ATTEMPT],
                    {"stage": row.slug, "attempt": attempt},
                    consequence="final_stage" if self._is_final_stage(snap, row.slug) else "approve_unlocks",
                ))
            else:  # `[R]` is informational: the engine is revising and nothing waits on a decision (C18)
                seeds.append(self._seed(
                    snap, "revision", row.slug, own,
                    self._latest_ts(snap, ("STAGE_REVISING", "GATE_REJECTED"), row.slug), (),
                    {"stage": row.slug, "revision_count": state.revision_count},
                ))

        questions, needs_input = snap.questions, bool(_attr(slot, "needs_input", False))
        if questions is not None and (questions.pending_count or questions.pending_checkpoint or needs_input):
            # The decisions follow what is pending, not the card type: blank Q tags are `answers`, a blank
            # checkpoint its own pair. Unsupported follow-ups keep the card but offer nothing: they carry no Q ids,
            # and a checkpoint confirmation must not skip them.
            pending, checkpoint = questions.pending_count, questions.pending_checkpoint
            decisions = (["answers"] if pending > 0 else []) + list(_CHECKPOINT_DECISIONS.get(checkpoint, ()))
            # AI-DLC 2.10 binds a Plan Approval to the conversation session that asked it. Once that session is
            # gone, no reply can be recorded: AI-DLC must ask again in the new one, which a resumed run does.
            # Offering Approve would send a reply bound to be refused.
            stale = checkpoint == "plan_approval" and plan_session_stale(snap, slot)
            if stale:
                decisions = [d for d in decisions if d not in _CHECKPOINT_DECISIONS["plan_approval"]] + ["resume"]
            seeds.append(self._seed(
                snap, "question", snap.stage, base, self._question_asked_at(snap),
                # the host wants input the file does not show: answer in the file's own vocabulary
                [] if questions.unsupported_pending_count else decisions or ["answers"],
                {"stage": snap.stage, "pending": pending, "checkpoint": checkpoint},
                token=questions.sha256 if snap.stage is not None else None,
                consequence=(
                    "plan_session_stale" if stale and pending == 0
                    else checkpoint if pending == 0 and checkpoint in C.QUESTION_CHECKPOINT_KINDS else "__default__"
                ),
                reason=checkpoint or ("needs_input" if needs_input and pending == 0 else "questions"),
            ))

        if snap.dangling_cursor or snap.cursor is None:
            # Space-level, so every intent's copy collapses into one card; picking the intent is the admin-lane
            # engine verb, never a prompt, which would mint a HUMAN_TURN for a navigation step (C22).
            why = "dangling_cursor" if snap.dangling_cursor else "no_cursor"
            seeds.append(self._seed(
                snap, "missing_input", None, base, snap.taken_at, ("pick_intent",),
                {"reason": why, "space": snap.space, "cursor": snap.cursor},
                token=why, risk_class="admin", reason=why, space_wide=True,
            ))
        if state is not None and not state.header_only and not state.project.get("Scope", "").strip():
            seeds.append(self._seed(
                snap, "missing_input", None, base, snap.taken_at, ("provide_input",),
                {"reason": "missing_scope", "kind": "scope"}, token="missing_scope", reason="missing_scope",
            ))

        newest = self._newest_action(live_actions)
        failed = newest if newest is not None and str(_attr(newest, "status", "")) == "Failed" and (
            self._deterministic(newest)) else None
        burst = self._error_burst(snap)
        if failed is not None or burst is not None:
            waiting = (str(_attr(failed, "resolved_at", "") or _attr(failed, "updated_at", "") or snap.taken_at)
                       if failed is not None else burst)
            seeds.append(self._seed(
                snap, "failure", snap.stage, {**base, "error_burst_at": burst}, waiting or snap.taken_at,
                ("retry_now", "keep_paused"), {"stage": snap.stage, "burst": burst is not None}, fallback="failure",
                reason="action_failed" if failed is not None else "error_burst",
                related_action_id=(str(_attr(failed, "action_id", "")) or None) if failed is not None else None,
            ))

        for breaker in breakers:
            if not _attr(breaker, "opened_at"):
                continue
            # Keyed on the fingerprint: the breaker outlives the boundary that opened it, and a card re-created on
            # every boundary move would reset the human's context on the very failure they are studying.
            key = str(_attr(breaker, "key", "") or "")
            fingerprint = str(_attr(breaker, "fingerprint", "") or key.rsplit(":", 1)[-1])
            seeds.append(self._seed(
                snap, "circuit_breaker", snap.stage, {**base, "breaker": _json_of(breaker) or {"key": key}},
                str(_attr(breaker, "opened_at")), ("retry_now", "keep_paused"),
                {"stage": snap.stage, "count": int(_attr(breaker, "count", 0) or 0),
                 "fingerprint_class": _attr(breaker, "fingerprint_class")},
                token=fingerprint or "breaker", reason=fingerprint or None,
            ))

        if install is not None and str(_attr(install, "status", "")) == "recovery_required":
            # Navigation only: an interrupted install is repaired on the Repos page under an admin lease. Not
            # `blocking`, because the transaction rolled back and nothing was written (FR-ACT-008).
            seeds.append(self._seed(
                snap, "install_conflict", None, {**base, "install": _json_of(install)}, snap.taken_at, (),
                {"engine_dir": _attr(install, "engine_dir"), "engine_version": _attr(install, "engine_version")},
                token="recovery_required", reason="recovery_required", space_wide=True,
            ))

        seeds.sort(key=lambda s: (priority_group_for(s.type), SEVERITY_RANK.get(s.severity, 3), s.dedupe_key))
        return seeds

    def _seed(
        self, snap: IntentSnapshot, action_type: str, stage: str | None, evidence: dict, waiting_since: str,
        decisions: Sequence[str], headline_params: Mapping[str, Any], *, token: str | None = None,
        fallback: str | None = None, consequence: str | None = "__default__", risk_class: str | None = None,
        reason: str | None = None, related_action_id: str | None = None, space_wide: bool = False,
    ) -> CardSeed:
        """A seed with its type's table defaults. `token` tells this boundary from the next in the dedupe key
        (`None`: the boundary token, else `fallback`); `space_wide` keys a space-level fact on the space."""
        severity, default_risk, default_variant = _SEED_TABLE[action_type]
        captured = self.captured(snap, stage)
        intent_key = f"{snap.space}~*" if space_wide else snap.intent_key
        return CardSeed(
            type=action_type, repo_id=snap.repo_id, space=snap.space, intent_dir=snap.intent_dir,
            intent_key=snap.intent_key, intent_uuid=snap.row.uuid if snap.row is not None else None, stage=stage,
            unit=snap.unit if stage == snap.stage else None, severity=severity, risk_class=risk_class or default_risk,
            captured=captured,
            # a copy per seed: several share one derivation, and one card's blob must not rewrite another's
            evidence=dict(evidence),
            dedupe_key=dedupe_key(
                action_type, snap.repo_id, intent_key, stage, token or captured["boundary_token"] or fallback
            ),
            waiting_since=waiting_since, decisions=tuple(decisions), headline_params=dict(headline_params),
            consequence_variant=default_variant if consequence == "__default__" else consequence,
            stage_ref=self.stage_ref(snap, stage), reason=reason, related_action_id=related_action_id,
        )

    def stage_ref(self, snap: IntentSnapshot, slug: str | None) -> dict | None:
        """`{slug, number, name, phase, unit}` for a card's stage (§2.11), or `None`."""
        if not slug:
            return None
        node, row = snap.node(slug), snap.row_for(slug)
        return {
            "slug": slug,
            "number": node.number if node is not None else None,
            "name": node.name if node is not None else None,
            "phase": (node.phase if node is not None else None) or (row.phase if row is not None else None),
            "unit": snap.unit if slug == snap.stage else None,
        }

    @staticmethod
    def _error_burst(snap: IntentSnapshot) -> str | None:
        """Newest unresolved burst in the current scope, using audit causality rather than age.

        Only complete history can prove that a later lifecycle boundary retired old errors. Same-shard
        append order is authoritative; cross-shard timestamp ties cannot prove resolution. Unit-scoped
        boundaries never clear another unit's errors, and separate units never combine into a burst.
        """
        stage = snap.stage
        workflow_boundaries = {"WORKFLOW_STARTED", "WORKFLOW_COMPLETED"}
        stage_boundaries = {
            "STAGE_STARTED", "STAGE_REVISING", "STAGE_JUMPED",
            "STAGE_COMPLETED", "STAGE_SKIPPED", "GATE_APPROVED",
        }
        boundaries = [
            event for event in snap.audit.events
            if snap.audit.complete and event.event in workflow_boundaries | stage_boundaries
            and event.fields.get("Workflow") in (None, "", "main")
            and _epoch(event.timestamp) is not None
        ]

        def applies(boundary: AuditEvent, error: AuditEvent) -> bool:
            boundary_unit = boundary.fields.get("Unit") or None
            if boundary.event in workflow_boundaries:
                return boundary_unit is None
            boundary_stage = boundary.fields.get("Stage")
            return (
                bool(boundary_stage) and boundary_unit == (error.fields.get("Unit") or None)
                and error.fields.get("Stage") in (None, "", boundary_stage)
            )

        scopes: dict[tuple[str | None, str | None], list[AuditEvent]] = {}
        for event in snap.audit.events:
            if (
                event.event != C.ERROR_EVENT
                or event.fields.get("Workflow") not in (None, "", "main")
                or (stage is not None and event.fields.get("Stage") not in (None, "", stage))
                or (snap.unit is not None and event.fields.get("Unit") not in (None, "", snap.unit))
                or _epoch(event.timestamp) is None
            ):
                continue
            scope = (event.fields.get("Stage") or stage, event.fields.get("Unit") or None)
            scopes.setdefault(scope, []).append(event)

        bursts: list[AuditEvent] = []
        for candidates in scopes.values():
            remaining = candidates
            for boundary in boundaries:
                # A tied error makes this boundary unusable as resolution proof for the scope.
                if any(
                    applies(boundary, error) and error.shard != boundary.shard
                    and _epoch(error.timestamp) == _epoch(boundary.timestamp)
                    for error in candidates
                ):
                    continue
                remaining = [
                    error for error in remaining
                    if not applies(boundary, error) or not (
                        boundary.pos > error.pos if boundary.shard == error.shard
                        else _epoch(boundary.timestamp) > _epoch(error.timestamp)
                    )
                ]
            rows = sorted(remaining, key=lambda event: event.sort_key)
            for start in range(len(rows) - ERROR_BURST_COUNT, -1, -1):
                window = rows[start:start + ERROR_BURST_COUNT]
                first, last = _epoch(window[0].timestamp), _epoch(window[-1].timestamp)
                if first is not None and last is not None and 0 <= last - first <= ERROR_BURST_WINDOW_SECS:
                    bursts.append(window[0])
                    break
        return max(bursts, key=lambda event: event.sort_key).timestamp if bursts else None

    # -- boundary timestamps ------------------------------------------------ #

    def _gate_opened_at(self, snap: IntentSnapshot, stage: str) -> str:
        """When the gate opened — the FR-ACT-004 sort key (C20): `created_at` would let a card minted by a restart
        jump ahead of a gate that has waited an hour."""
        return self._latest_ts(snap, ("STAGE_AWAITING_APPROVAL",), stage) or snap.taken_at

    def _question_asked_at(self, snap: IntentSnapshot) -> str:
        """When this question group was asked, never `now`, which would reset its age every scan (C20): an audit
        question's own time, else the newest asking event, else the file's mtime, else the stage's start."""
        questions = snap.questions
        if questions is not None and questions.origin:
            return str(questions.origin["timestamp"])
        asked = self._latest_ts(snap, C.QUESTION_EVENTS, snap.stage)
        if asked:
            return asked
        if questions is not None:
            for artifact in snap.stage_artifacts:
                if artifact.relpath == questions.relpath:
                    return artifact.mtime
        return self._latest_ts(snap, ("STAGE_STARTED",), snap.stage) or snap.taken_at

    @staticmethod
    def _latest_ts(snap: IntentSnapshot, types: Sequence[str], stage: str | None) -> str | None:
        """The newest matching event's timestamp. No cross-stage fallback: borrowing another stage's
        `QUESTION_ANSWERED` sorted a brand-new question group as hours old. `stage=None` means any stage."""
        event = latest_event(list(snap.audit.events), types, stage)
        return event.timestamp if event is not None else None

    def _is_final_stage(self, snap: IntentSnapshot, slug: str) -> bool:
        """Whether approving this gate finishes the workflow (the `final_stage` consequence)."""
        return snap.state is not None and not any(
            (row.suffix or "").upper().startswith("EXECUTE") and row.state not in C.DONE_STAGE_STATES
            and row.slug != slug
            for row in snap.state.stages
        )

    # -- summaries ---------------------------------------------------------- #

    def summarize(
        self, snap: IntentSnapshot, *, binding: Any | None = None, live_actions: Sequence[Any] = (),
        breaker_open: bool = False, host: Any | None = None, findings: Sequence[Any] = (), slot: Any | None = None,
    ) -> IntentSummary:
        state, row = snap.state, snap.row
        return IntentSummary(
            repo_id=snap.repo_id, repo_label=snap.repo_label, space=snap.space, intent_dir=snap.intent_dir,
            intent_key=snap.intent_key, uuid=row.uuid if row is not None else None, slug=self._slug(snap),
            scope=self._scope(snap), registry_status=row.status if row is not None else None, title=self._title(snap),
            operational_state=self.operational_state(
                snap, binding=binding, live_actions=live_actions, breaker_open=breaker_open, host=host,
                findings=findings, slot=slot,
            ),
            disk=self._disk(snap),
            counts=(dict(state.counts) if state is not None
                    else {"total": 0, "done": 0, **dict.fromkeys(C.STAGE_STATE, 0)}),
            # `revision` cards are informational and kept out of the queue (C18), so they stay out of the badge too,
            # as does a decision already sent: it waits on AI-DLC, not on the person.
            open_actions=sum(1 for a in live_actions if str(_attr(a, "type", "")) != "revision"
                             and str(_attr(a, "status", "")) in C.AWAITING_PERSON_STATUS),
            blocking_findings=sum(1 for f in findings if str(_attr(f, "severity", "")) == "blocking"),
            warn_findings=sum(1 for f in findings if str(_attr(f, "severity", "")) == "warn"),
            session=host, archived=str(_attr(binding, "archive_state", "active")) == "archived",
            paused=bool(_attr(binding, "paused", False)),
            interrupted=bool(_attr(binding, "interrupted_at")),
            last_activity_at=self._latest_ts(snap, C.MOVEMENT_EVENTS, None),
            stable_boundary=_attr(binding, "last_stable_boundary"), unstable=not snap.stable,
            is_active_cursor=snap.is_active,
        )

    def detail(
        self, snap: IntentSnapshot, *, binding: Any | None = None, live_actions: Sequence[Any] = (),
        breaker_open: bool = False, host: Any | None = None, findings: Sequence[Any] = (), slot: Any | None = None,
        git: Any | None = None, actions: Sequence[Any] = (), host_card: dict | None = None,
    ) -> IntentDetail:
        summary = self.summarize(
            snap, binding=binding, live_actions=live_actions, breaker_open=breaker_open, host=host,
            findings=findings, slot=slot,
        )
        metas, truncated = self.artifacts(snap)
        state, directive = snap.state, snap.directive
        return IntentDetail(
            **{field: getattr(summary, field) for field in summary.__slots__},
            state_sections={k: dict(v) for k, v in (state.sections if state else {}).items()},
            phases=tuple(state.phases) if state is not None else (),
            stages=tuple(state.stages) if state is not None else (),
            findings=tuple(findings), actions=tuple(actions), directive=directive,
            recovery=dict(snap.markers.recovery) if snap.markers.recovery else None,
            markers=MarkersView.of(snap.markers),
            audit_tail=tuple(AuditEventView.of(e) for e in snap.audit.events[-AUDIT_TAIL_LIMIT:]),
            questions=self.questions_view(snap, host_card=host_card), artifacts_count=len(metas),
            artifacts_truncated=truncated, git=git, binding=binding, engine=snap.engine,
            active_directive_stage=directive.stage if directive is not None and directive.matches_state else None,
        )

    @staticmethod
    def _slug(snap: IntentSnapshot) -> str:
        """The registry row's slug (authoritative, `04 §2.1`), else the directory name minus its `YYMMDD-` prefix —
        an orphan record is a `warn` finding, not a reason to show an empty name."""
        if snap.row is not None and snap.row.slug:
            return snap.row.slug
        head, _, tail = snap.intent_dir.partition("-")
        return tail if tail and len(head) == _DATE_PREFIX_LEN and head.isdigit() else snap.intent_dir

    @staticmethod
    def _scope(snap: IntentSnapshot) -> str | None:
        """The state file's current scope, else the registry's birth-time one: a `SCOPE_CHANGED` run rewrites the
        state but not the registry row (FORMAT-NOTES §2), and what is executing now is what the user needs."""
        if snap.state is not None:
            scope = (snap.state.project.get("Scope") or "").strip()
            if scope:
                return scope
        return snap.row.scope if snap.row is not None else None

    @staticmethod
    def _title(snap: IntentSnapshot) -> str | None:
        value = (snap.state.project.get("Project") or "").strip() if snap.state is not None else ""
        return value[:MAX_TITLE_CHARS] if value and value not in PROJECT_PLACEHOLDERS else None

    @staticmethod
    def _disk(snap: IntentSnapshot) -> DiskState:
        state = snap.state
        if state is None:
            return DiskState(None, None, None, None, None, None, None, 0, None, None)
        return DiskState(
            status=state.status, lifecycle_phase=state.lifecycle_phase, current_stage=state.current_stage,
            next_stage=state.next_stage, state_version=state.state_version, total_stages=state.total_stages_claimed,
            completed=state.completed_claimed, revision_count=state.revision_count, parked_at=state.parked_at,
            last_updated=(state.sections.get("Current Status", {}).get("Last Updated")
                          or state.project.get("Last Updated") or None),
        )

    # -- artifacts ---------------------------------------------------------- #

    def artifacts(self, snap: IntentSnapshot) -> tuple[list[ArtifactMeta], bool]:
        """Files in the record and its declared shared outputs, plus whether the walk was cut short: a listing that
        hit the file cap or the deadline is partial, and calling it complete would let the UI claim a file missing.
        """
        root = Path(snap.canonical_path)
        metas = self._reader.list_artifacts(root, snap.space, snap.intent_dir)
        found = {meta.relpath: meta for meta in metas}
        for node in snap.graph:
            if C.artifact_output_location(node) != "codekb":
                continue
            output_dir = f"aidlc/spaces/{snap.space}/codekb/{root.name}"
            for meta in self._reader.stage_artifacts(root, output_dir, node):
                found[meta.relpath] = meta
        ordered = sorted(found.values(), key=lambda meta: meta.relpath)
        truncated = bool(getattr(metas, "truncated", False)) or len(ordered) > C.MAX_ARTIFACTS_PER_INTENT
        return (ordered[:C.MAX_ARTIFACTS_PER_INTENT], truncated)

    # -- map ---------------------------------------------------------------- #

    def map_inputs(self, snap: IntentSnapshot) -> dict[str, Any]:
        """The facts the intent map is drawn from; `ui/src/map/buildMap.ts` assembles them.

        Joining the graph with the state rows, grouping artifacts and counting are presentation, so they
        live in the UI. What stays here needs a reader: the artifact walk, the skip reasons parsed out of
        the state file and each stage's latest attempt read from the audit. `now` travels with them so an
        open attempt is measured against this server's clock, exactly as when the server did the sum.
        """
        state = snap.state
        directive = snap.directive
        metas, _truncated = self.artifacts(snap)
        return {
            "intent_key": snap.intent_key,
            "graph_version": snap.engine.engine_version if snap.engine is not None else None,
            "now": self._clock.now(),
            "nodes": [
                {
                    "slug": node.slug, "number": node.number, "name": node.name, "phase": node.phase,
                    "execution": node.execution, "mode": node.mode, "lead_agent": node.lead_agent,
                    # The class this run reviews at, as on the cards: an express intent's Map promised the
                    # graph's "adversarial" review that its scope never runs.
                    "reviewer": node.reviewer, "review_class": _run_review_class(snap, node),
                    "summary_confirmation": node.summary_confirmation, "per_unit": node.per_unit,
                    "consumes": [consume.artifact for consume in node.consumes],
                    "produces": list(node.produces), "requires_stage": list(node.requires_stage),
                }
                for node in snap.graph
            ],
            "rows": [row.to_json() for row in state.stages] if state is not None else [],
            "status": state.status if state is not None else None,
            "phases": [list(pair) for pair in state.phases] if state is not None else [],
            "current_stage": snap.stage or (state.current_stage if state is not None else None),
            "directive_stage": directive.stage if directive is not None and directive.matches_state else None,
            # Only this intent's scope: it is what a suffixless legacy row falls back to.
            "scope_grid": dict(snap.grid.get(self._scope(snap) or "", {})),
            "units": list(snap.units),
            "active_unit": snap.unit,
            "artifacts": [meta.to_json() for meta in metas],
            "skip_reasons": self._skip_reasons(snap),
            "attempts": self._attempts(snap),
        }

    @staticmethod
    def _attempts(snap: IntentSnapshot) -> dict[str, dict[str, float | None]]:
        """Each stage's latest attempt in epoch seconds: `started`, and `ended` (`None` while it runs).

        The latest `STAGE_STARTED`, so a revised stage reports the current pass rather than its whole
        history, closed by the first `STAGE_COMPLETED`/`STAGE_SKIPPED` after it. An attempt whose
        timestamps do not parse is left out: its length is unknown, so the map shows none.
        """
        events = list(snap.audit.events)
        out: dict[str, dict[str, float | None]] = {}
        for slug in dict.fromkeys(event.fields.get("Stage") for event in events if event.event == "STAGE_STARTED"):
            started = latest_event(events, ("STAGE_STARTED",), slug) if slug else None
            begin = _epoch(started.timestamp) if started is not None else None
            if started is None or begin is None:
                continue
            closing = next(
                (
                    event
                    for event in events
                    if event.event in ("STAGE_COMPLETED", "STAGE_SKIPPED")
                    and event.fields.get("Stage") == slug
                    and event.sort_key > started.sort_key
                ),
                None,
            )
            end = _epoch(closing.timestamp) if closing is not None else None
            if closing is None or end is not None:
                out[slug] = {"started": begin, "ended": end}
        return out

    @staticmethod
    def _skip_reasons(snap: IntentSnapshot) -> dict[str, str | None]:
        """Why a stage is not executing, from the row suffix and from `Stages to Skip`.

        Both spellings are real: the row suffix is `SKIP` / `SKIP: reason`, and the scope-configuration
        line carries `2.1 (reverse-engineering — greenfield)` where the text after the dash is the
        reason the composer recorded (FORMAT-NOTES §3).
        """
        out: dict[str, str | None] = {}
        state = snap.state
        if state is None:
            return out
        for row in state.stages:
            suffix = (row.suffix or "").strip()
            if suffix.upper().startswith("SKIP"):
                out[row.slug] = suffix[4:].strip(" :()—–-") or None
        listed = state.scope_config.get("Stages to Skip", "") or ""
        if listed.strip().lower() in ("", "none"):
            return out
        for entry in listed.split(","):
            entry = entry.strip()
            if "(" not in entry or not entry.endswith(")"):
                continue
            inner = entry[entry.index("(") + 1: -1]
            for dash in ("—", "–", " - "):
                if dash in inner:
                    slug, _, reason = inner.partition(dash)
                    slug, reason = slug.strip(), reason.strip()
                    if slug and (out.get(slug) is None):
                        out[slug] = reason or None
                    break
            else:
                out.setdefault(inner.strip(), None)
        return out

    # -- sorting ------------------------------------------------------------ #

    @staticmethod
    def sort_actions(cards: Sequence[Any], organize: str) -> list[Any]:
        """FR-ACT-003/004 order, the same comparator as `ui/src/lib/sort.ts`.

        Ties break on `waiting_since` (the boundary time, C20), then `action_id`, so two clients that fetched one
        queue render one order. Labels collate by casefold: the server has no user locale, and the client re-sorts
        with `Intl.Collator` after filtering (§3.6).
        """
        if organize not in ORGANIZE_MODES:
            raise StudioError(
                "bad_param", f"organize must be one of {', '.join(ORGANIZE_MODES)}",
                details={"organize": organize, "allowed": list(ORGANIZE_MODES)},
            )
        out = list(cards)
        if organize == "oldest":
            out.sort(key=_oldest_key)
        elif organize == "priority":
            out.sort(key=lambda card: (_group_of(card), _severity_of(card), _oldest_key(card)))
        elif organize == "repo":
            # `repo_id` breaks a label tie before the band does: the group key is the repo_id, so two repositories
            # with one label (a clone and a worktree) interleaved and `groups[]` repeated a key.
            out.sort(key=lambda card: (_repo_label(card), _repo_id_of(card), _group_of(card), _oldest_key(card)))
        else:  # "type"
            out.sort(key=lambda card: (
                _group_of(card), str(_attr(card, "queue_type", "") or ""), _severity_of(card), _oldest_key(card),
            ))
        return out

    @staticmethod
    def group_key(card: Any, organize: str) -> str:
        """The bucket a card belongs to in the current organize mode (§3.5)."""
        if organize == "priority":
            return f"group.{_group_of(card)}"
        if organize == "repo":
            return _repo_id_of(card)
        if organize == "type":
            return str(_attr(card, "queue_type", "") or "")
        return "oldest"


def _group_of(card: Any) -> int:
    return priority_group_for(str(_attr(card, "queue_type", "") or _attr(card, "type", "") or ""))


def _severity_of(card: Any) -> int:
    return SEVERITY_RANK.get(str(_attr(card, "severity", "") or ""), 3)


def _oldest_key(card: Any) -> tuple[str, str]:
    return (str(_attr(card, "waiting_since", "") or ""), str(_attr(card, "action_id", "") or ""))


def _repo_label(card: Any) -> tuple[str, str]:
    label = str(_attr(_attr(card, "repo", {}), "label", "") or "")
    return (label.casefold(), label)


def _repo_id_of(card: Any) -> str:
    return str(_attr(_attr(card, "repo", {}), "repo_id", "") or "")


def plan_session_stale(snap: IntentSnapshot, slot: Any | None) -> bool:
    """Is the session that asked this Plan Approval gone, so that AI-DLC cannot record a reply to it? Either the
    conversation already moved to another session, or the host dropped the idle one and the next message opens a
    new session (``slot.session_live``)."""
    return plan_asked_in_an_earlier_session(snap) or _attr(slot, "session_live", None) is False


def plan_asked_in_an_earlier_session(snap: IntentSnapshot) -> bool:
    """Did AI-DLC ask this unit's Plan Approval in a session the conversation has since left? The newest
    plan-approval ``DECISION_RECORDED`` names its session; a later ``SESSION_STARTED`` or ``HUMAN_TURN`` naming
    another one means a reply now reaches a session that holds no challenge for it."""
    unit = snap.unit or None
    events = list(snap.audit.events)
    asked = [event for event in events if event.event == "DECISION_RECORDED"
             and event.fields.get("Checkpoint") == C.PLAN_APPROVAL_AUDIT_CHECKPOINT
             and (event.fields.get("Unit") or None) == unit and event.fields.get("Session")]
    if not asked:
        return False
    newest = max(asked, key=lambda event: event.sort_key)
    later = [event for event in events if event.event in ("SESSION_STARTED", "HUMAN_TURN")
             and event.sort_key > newest.sort_key and event.fields.get("Session")]
    return bool(later) and max(later, key=lambda event: event.sort_key).fields["Session"] != newest.fields["Session"]


def _run_review_class(snap: IntentSnapshot, node: StageNode | None) -> str | None:
    """The class this run reviews the stage at: the graph's, capped by the intent's scope and its Review
    Override (the planner's rule). ``None`` when the caps turn review off: ``express`` runs no reviewer, so a
    card showing the graph's "advisory" would promise a review that never happens."""
    if node is None:
        return None
    state = snap.state
    scope = (state.project.get("Scope") or "").strip() if state is not None else ""
    meta = next((s for s in snap.scopes if s.name == scope), None)
    override = (state.scope_config.get("Review Override") or "").strip() or None if state is not None else None
    effective = effective_review_class(node, (meta.review_cap if meta is not None else None, override))
    return None if effective == "none" else effective
