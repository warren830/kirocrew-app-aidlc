"""The read model: one coherent look at an intent, turned into everything the UI reads.

Three products come out of this module and each has a different reason to exist.

**The snapshot** (`IntentSnapshot`) is one *moment* of an intent, not a cache. Every read that a
decision could depend on — state file, audit shards, directive marker, the current stage's questions
file, that stage's produced artifacts and its Review section — is taken in a fixed order with the
state file re-read at the end (`AidlcReader.stable_boundary_inputs`). If those reads span a write the
snapshot is `stable == False`, and everything downstream must refuse to authorise a decision from it:
the card renders `Refreshing`, `submit` answers `409 unstable_read`, and no `Captured` set taken from
it can match a later one. That is the whole point of FR-ACT-009 — a gate approved against a state file
that changed mid-read is an approval of a boundary that no longer exists.

**The cards** (`derive_cards`) are derived from disk, never from a user gesture. A boundary that is on
disk always has exactly one live card, and a card whose boundary left disk is retired
(`HumanActionBroker.retire_stale`). That inversion is what makes the queue trustworthy: the human
cannot dismiss a `[?]` row, and Studio cannot invent one. `dedupe_key` is what ties a card to its
boundary, so its shape is fixed here (§0.1) and the partial unique index in `storage.py` enforces one
live row per key.

**The operational state** (`operational_state`) is a strict first-match-wins ladder, and the order is
the safety property, not a presentation choice. `ReconciliationRequired` outranks everything except
`Archived` because an unresolved delivery must never look runnable; `Interrupted` outranks
`CircuitOpen` because a force stop in flight is a live host operation; `WaitingForYou` sits *below*
`Queued`-for-commands but *above* plain `Queued`, because a derived card sitting in `Queued` is a card
waiting for the human, not a turn waiting for the model (review P08).

Everything here is sync and pure over its arguments plus bounded reads through `AidlcReader`. It never
touches Storage, the host or a child process, which is why the reconciler can run it on the scan pool for
64 repositories and a test can pin a rule by handing it a fixture repository.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, is_dataclass
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
    evidence_digest,
    file_questions_support_forms,
    latest_event,
    match_intent_row,
    parse_review_section,
    stage_attempt,
)
from .errors import StudioError

if TYPE_CHECKING:  # pragma: no cover - typing only
    # `consistency` imports THIS module under TYPE_CHECKING, so importing it at runtime would close a
    # cycle. Everything below is duck-typed: a Finding is anything with `.code`, `.severity` and
    # `.to_json()`, which is what lets a test build one by hand.
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
    "MapModel",
    "MapPhase",
    "MapStage",
    "MapUnit",
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

#: Queue band per action type (FR-ACT-003). Byte-for-byte the same table as `ui/src/lib/sort.ts`;
#: `sort.test.ts` and `test_projection.py` pin the same golden order against it. Kept here rather than
#: in `constants.py` because it is the projection's own ranking of its own card types — nothing else
#: in the backend has an opinion about queue bands.
PRIORITY_GROUP: dict[str, int] = {
    "recovery": 1,
    "delivery_uncertain": 1,
    "gate": 2,
    "question": 2,
    "missing_input": 2,
    "circuit_breaker": 3,
    "failure": 3,
    "install_conflict": 3,
    "budget_stop": 4,
    "revision": 4,
    "run": 4,
    "resume": 4,
    "force_stop": 4,
    "prepare_commit": 4,
}

#: `SEVERITY` in queue order. `critical` first because those cards are the ones where Studio does not
#: know what happened; `info` last because nothing is blocked by them.
SEVERITY_RANK: dict[str, int] = {"critical": 0, "blocking": 1, "attention": 2, "info": 3}

ORGANIZE_MODES: tuple[str, ...] = ("priority", "repo", "type", "oldest")

#: Severity per derived card type (§1.8 table).
SEED_SEVERITY: dict[str, str] = {
    "gate": "blocking",
    "revision": "info",
    "question": "blocking",
    "missing_input": "blocking",
    "recovery": "critical",
    "delivery_uncertain": "critical",
    "failure": "attention",
    "circuit_breaker": "attention",
    "install_conflict": "attention",
    "budget_stop": "info",
}

#: Risk class per derived card type. It follows the lane its decisions use (§1.12): a card whose
#: decisions all resolve inside Studio is `studio_only` even when the boundary it describes is
#: AI-DLC's, and `install_conflict` is `read` because it only navigates.
SEED_RISK_CLASS: dict[str, str] = {
    "gate": "human_lane",
    "revision": "read",
    "question": "human_lane",
    "missing_input": "human_lane",
    "recovery": "studio_only",
    "delivery_uncertain": "studio_only",
    "failure": "studio_only",
    "circuit_breaker": "studio_only",
    "install_conflict": "read",
    "budget_stop": "studio_only",
}

#: Consequence variant per card type (§2.11 "consequence keys"). `gate` picks between two at runtime.
SEED_CONSEQUENCE: dict[str, str | None] = {
    "gate": "approve_unlocks",
    "revision": None,
    "question": "advances_turn",
    "missing_input": None,
    "recovery": "blocked_until_agree",
    "delivery_uncertain": "no_replay",
    "failure": "no_dispatch",
    "circuit_breaker": None,
    "install_conflict": "nothing_written",
    "budget_stop": None,
}

#: Blocking findings that must NOT become a `recovery` card. `delivery_uncertain_action` already has a
#: card — the uncertain action itself, re-typed by `queue_type_for` — and `lease_conflict` is a
#: transient scheduling fact that clears itself when the other operation finishes, so raising a
#: critical card for it would train the user to ignore critical cards.
RECOVERY_EXCLUDED_FINDINGS: frozenset[str] = frozenset({"delivery_uncertain_action", "lease_conflict"})

#: Action types that represent a turn Studio asked for, as opposed to a boundary it observed. Only
#: these make an intent `Queued` (review P08).
COMMAND_ACTION_TYPES: frozenset[str] = frozenset({"run", "resume", "prepare_commit", "force_stop"})

#: Statuses that re-type a card as `delivery_uncertain` in the queue (§2.11).
UNCERTAIN_STATUSES: frozenset[str] = frozenset({"DeliveryUncertain", "ReconciliationRequired"})

#: How many audit rows `IntentDetail.audit_tail` carries (§1.8).
AUDIT_TAIL_LIMIT = 50

#: `## Q…` errors aside, an `ERROR_LOGGED` burst is a failure only when it is dense: three rows inside
#: this window for the current stage. Isolated `ERROR_LOGGED` rows are conductor mistakes that the
#: engine recovers from (FORMAT-NOTES §6) and must not raise a card.
ERROR_BURST_COUNT = 3
ERROR_BURST_WINDOW_SECS = 600

#: The template values AI-DLC leaves in `- **Project**:` when an intent was born with no usable free text
#: (FORMAT-NOTES §3). Showing one as an intent title would put a placeholder in the rail. 2.7.1 added the
#: two pasted-document forms, which a birth from a pasted brief writes verbatim.
PROJECT_PLACEHOLDERS = frozenset(
    {
        "[Project description]",
        "[Pasted document provided]",
        "[Pasted document boundary needs clarification]",
    }
)

#: `IntentSummary.title` cap (§1.8).
MAX_TITLE_CHARS = 160

#: Marker names `StableBoundary.marker` can carry (§1.8).
BOUNDARY_MARKERS = ("[?]", "[R]", "question", "parked", "completed", "phase_complete")

#: Unmet-condition codes `StableBoundary.reasons` can carry (§1.8).
BOUNDARY_REASONS = ("state_unstable", "not_at_boundary", "action_in_flight", "session_busy", "cursor_mismatch")

_DATE_PREFIX_LEN = 6


# --------------------------------------------------------------------------- #
# small pure helpers shared with the broker and the handlers
# --------------------------------------------------------------------------- #


#: The §1.8 public name for the §0.1 formula. Projection is its documented producer, but `Storage` mints
#: the same key when it reads an activity row back, so the implementation lives in `constants`.
intent_key_for = C.intent_key_for


def dedupe_key(action_type: str, repo_id: str, intent_key: str, stage: str | None, token: str | None) -> str:
    """`type:repo_id:intent_key:stage_or_-:boundary_token` (§0.1).

    The last segment is whatever discriminates *this* boundary from the next one for this card type.
    For every card derived from a state/audit boundary that is the `boundary_token`. For cards that
    have no boundary — an open breaker, a repo whose install needs recovery, a missing cursor — it is
    the stable identity of the condition instead (the breaker fingerprint, the install status, the
    finding code), so the card survives the boundary moving underneath it rather than being retired and
    re-created on every scan.
    """
    return f"{action_type}:{repo_id}:{intent_key}:{stage or '-'}:{token or '-'}"


def queue_type_for(action_type: str, status: str) -> str:
    """The type the queue shows. An uncertain delivery is always shown as what it is (§2.11)."""
    return "delivery_uncertain" if status in UNCERTAIN_STATUSES else action_type


def priority_group_for(queue_type: str) -> int:
    """Queue band 1..4. An unknown type sorts last rather than raising: a card the UI cannot rank is
    still a card the user must see."""
    return PRIORITY_GROUP.get(queue_type, 4)


#: Several objects this module consumes are owned by modules written in parallel (`BindingView`,
#: `SlotView`, `SessionRef`, `ActionRecord`, `GitObservation`); reading them through the shared
#: structural accessor (`constants.attr`) is what keeps projection free of an import edge to any of them.
_attr = C.attr


def _json_of(obj: Any) -> Any:
    """`to_json()` when the object has one, else a dataclass's own fields, else `None`.

    The dataclass fallback matters because several of these objects are owned by modules written in
    parallel: a `SessionRef` or `GitObservation` that has not grown its `to_json()` yet must still reach
    the wire as its fields rather than silently as `null`, which would look like "not bound" / "no git".
    """
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return dict(obj)
    to_json = getattr(obj, "to_json", None)
    if callable(to_json):
        return to_json()
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    return None


def _iso_from_ns(mtime_ns: int | None) -> str | None:
    if mtime_ns is None:
        return None
    return C.iso_from_epoch(mtime_ns / 1_000_000_000)


#: One implementation for the whole backend (``constants.epoch_from_iso``); a local one here used to be
#: an hour off under DST, which showed up as an inflated ``MapStage.elapsed_secs``.
_epoch = C.epoch_from_iso


def _sha12(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()[:12]


def _tolerate_too_large(read: Any, default: Any) -> Any:
    """Run a bounded read, accepting `too_large` as "no answer".

    Only `too_large` is swallowed. Every other `StudioError` — `bad_path` above all — keeps propagating,
    because a path that escapes the repository must reach the caller as a refusal, never as an empty
    result that looks like an ordinary absence.
    """
    return _tolerate_too_large_flag(read, default)[0]


def _tolerate_too_large_flag(read: Any, default: Any) -> tuple[Any, bool]:
    """As :func:`_tolerate_too_large`, plus whether the cap is what produced the answer.

    A caller that shows evidence to a person needs the difference: "this stage has no reviewer verdict"
    and "this stage's verdict is in a file too large to render" are different sentences, and only one of
    them is an invitation to go and read the artifact.
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
    """What one boundary stage's own directory holds.

    Exists because an intent can carry more than one ``[?]`` row, and a gate card must show the artifacts
    of the stage it is a gate for. The snapshot reads the *current* stage as a matter of course; this is
    the same read performed for each of the others.
    """

    stage_dir_rel: str | None
    artifacts: tuple[ArtifactMeta, ...]
    review: tuple[str | None, list[ReviewFinding]] | None
    questions: "QuestionsFile | None"
    review_unreadable: bool = False
    acceptance_criteria: tuple[str, ...] = ()
    artifact_dir_rel: str | None = None


@dataclass(frozen=True, slots=True)
class IntentSnapshot:
    """One coherent read of one intent.

    The fields after `first_unreadable_at` are additive to §1.8 and every one of them exists because a
    method whose signature the contract fixes needs it: `summarize` needs `repo_label`, `artifacts` and
    `map` need `canonical_path` to walk the record, `map` needs `units` and `engine_dir`, the card
    seeds need `stage`/`unit`/`stage_dir_rel` to name the boundary, and `EvidenceSnapshot` needs
    `acceptance_criteria`. Adding them here keeps the contract's method signatures intact instead of
    threading a second argument through four of them.
    """

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
    #: True when a reviewer's verdict exists on disk but sits in a file over the render cap, so
    #: ``review`` is ``None`` for a reason that is not "there is no reviewer". Additive to §1.8.
    review_unreadable: bool = False
    #: Per-stage evidence for every boundary row that is NOT ``stage``, keyed by slug. Empty in the
    #: ordinary case, because an intent almost always has a single open boundary and it is the current
    #: stage. Additive to §1.8; see ``stage_evidence``.
    other_stage_evidence: "Mapping[str, StageEvidence]" = dataclasses_field(default_factory=dict)
    artifact_dir_rel: str | None = None
    # Internal read evidence for audit questions, including after the pending question disappears.
    audit_question_attempt: tuple[str | None, bool] | None = None

    def stage_evidence(self, slug: str | None) -> StageEvidence:
        """The evidence belonging to ``slug``, whether or not it is this snapshot's current stage.

        One accessor so a caller cannot accidentally read the current stage's artifacts for a different
        stage — which is exactly the defect this replaced: a gate card for a second ``[?]`` row was given
        the current stage's artifacts, its reviewer verdict and its questions file, and a person deciding
        at that gate was shown another stage's work to approve.
        """
        if slug is not None and slug != self.stage and slug in self.other_stage_evidence:
            return self.other_stage_evidence[slug]
        return StageEvidence(
            stage_dir_rel=self.stage_dir_rel,
            artifacts=self.stage_artifacts,
            review=self.review,
            questions=self.questions,
            review_unreadable=self.review_unreadable,
            acceptance_criteria=self.acceptance_criteria,
            artifact_dir_rel=self.artifact_dir_rel,
        )

    @property
    def state_sha256(self) -> str | None:
        return self.state_snap.sha256 if self.state_snap is not None else None

    def node(self, slug: str | None) -> StageNode | None:
        if not slug:
            return None
        for node in self.graph:
            if node.slug == slug:
                return node
        return None

    def row_for(self, slug: str | None) -> StageRow | None:
        if not slug or self.state is None:
            return None
        return self.state.row(slug)


@dataclass(frozen=True, slots=True)
class DiskState:
    """What the state file itself claims, unjudged (§2.3 `disk`).

    `total_stages` and `completed` are the file's own *claims*, not counts: the difference between them
    and `IntentSummary.counts` is what `completed_count_mismatch` / `total_count_mismatch` report, so
    silently replacing a claim with a count here would delete the evidence for those findings.
    """

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

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "lifecycle_phase": self.lifecycle_phase,
            "current_stage": self.current_stage,
            "next_stage": self.next_stage,
            "state_version": self.state_version,
            "total_stages": self.total_stages,
            "completed": self.completed,
            "revision_count": self.revision_count,
            "parked_at": self.parked_at,
            "last_updated": self.last_updated,
        }


def run_unavailable_reason(
    snap: IntentSnapshot, *, stage: str | None = None, unit: str | None = None,
) -> str | None:
    """Why Run/Resume no longer fits a coherent snapshot, without judging other commands.

    Revision rows still need agent work. Only actual approval/question checkpoints need a
    human response. Missing or changing evidence cannot prove a queued request obsolete.
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
class StableBoundary:
    """Whether this intent is sitting still enough for a decision (PRD §12.2).

    All five conditions are evaluated every time and the unmet ones are named in `reasons`, because
    "not stable" alone gives the user nothing to fix. `stable == True` with an empty `reasons` is the
    only shape that authorises a lease reclaim or a boundary-based dispatch.
    """

    stable: bool
    reasons: tuple[str, ...]
    stage: str | None
    marker: str | None
    boundary_token: str | None
    #: When this boundary was FIRST observed, not when it was last looked at. The difference is what
    #: makes "has this changed?" answerable: with the time of the latest look in here, every comparison
    #: between two ticks differed, so the binder wrote an fsync'd row and the reconciler published an
    #: `intent.updated` frame once per intent per tick for an app where nothing was happening.
    recorded_at: str

    def identity(self) -> tuple[Any, ...]:
        """Everything that makes this boundary *this* boundary, excluding ``recorded_at``."""
        return (self.stable, tuple(self.reasons), self.stage, self.marker, self.boundary_token)

    def to_json(self) -> dict[str, Any]:
        return {
            "stable": self.stable,
            "reasons": list(self.reasons),
            "stage": self.stage,
            "marker": self.marker,
            "boundary_token": self.boundary_token,
            "recorded_at": self.recorded_at,
        }


@dataclass(frozen=True, slots=True)
class MarkersView:
    """Runtime markers as timestamps rather than `mtime_ns` (§3.1 `MarkersView`).

    The engine only cares about the mtimes; a human reading the evidence drawer needs a time they can
    compare with the audit trail, so the conversion happens once, here.
    """

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
            human_turn_at=_iso_from_ns(markers.human_turn_mtime_ns),
            engine_touch_at=_iso_from_ns(markers.engine_touch_mtime_ns),
            turn_counter=markers.turn_counter,
            goal_stop_present=markers.goal_stop_present,
            recovery=dict(markers.recovery) if markers.recovery else None,
            hooks_health=dict(markers.hooks_health),
            compose_pending=markers.compose_pending,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "human_turn_at": self.human_turn_at,
            "engine_touch_at": self.engine_touch_at,
            "turn_counter": self.turn_counter,
            "goal_stop_present": self.goal_stop_present,
            "recovery": dict(self.recovery) if self.recovery else None,
            "hooks_health": dict(self.hooks_health),
            "compose_pending": self.compose_pending,
        }

    def card_json(self) -> dict[str, Any]:
        """The subset §2.11 puts on a card — no `hooks_health`, no `compose_pending`.

        Hook health moves on every session start and would make two evidence snapshots of the same
        boundary differ for no decision-relevant reason (FR-ADV-007 lists it as volatile).
        """
        return {
            "human_turn_at": self.human_turn_at,
            "engine_touch_at": self.engine_touch_at,
            "turn_counter": self.turn_counter,
            "goal_stop_present": self.goal_stop_present,
            "recovery": dict(self.recovery) if self.recovery else None,
        }


@dataclass(frozen=True, slots=True)
class AuditEventView:
    shard: str
    pos: int
    timestamp: str
    event: str
    fields: dict[str, str]
    raw: str

    @classmethod
    def of(cls, event: AuditEvent) -> "AuditEventView":
        return cls(
            shard=event.shard,
            pos=event.pos,
            timestamp=event.timestamp,
            event=event.event,
            fields=dict(event.fields),
            raw=event.raw,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "shard": self.shard,
            "pos": self.pos,
            "timestamp": self.timestamp,
            "event": self.event,
            "fields": dict(self.fields),
            "raw": self.raw,
        }


@dataclass(frozen=True, slots=True)
class QuestionsView:
    """A questions file plus where it lives and how much of it the UI may drive (§2.11).

    File-backed questions retain their host-dependent mode (Appendix B6). A proven audit enum is
    structured independently of the host; ``origin`` records which disk event supplied its labels.
    """

    relpath: str
    sha256: str
    stage: str
    unit: str | None
    questions: tuple[Any, ...]
    summary_confirmation: Any | None
    plan_approval: Any | None
    pending_count: int
    pending_checkpoint: str | None
    mode: str
    host_card: dict | None
    origin: dict[str, Any] | None = None
    unsupported_pending_count: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "relpath": self.relpath,
            "sha256": self.sha256,
            "stage": self.stage,
            "unit": self.unit,
            "questions": [
                {
                    "index": q.index,
                    "prompt": q.prompt,
                    "context": getattr(q, "context", ""),
                    "options": [{"letter": o.letter, "text": o.text, "is_other": o.is_other} for o in q.options],
                    "multi_select": q.multi_select,
                    "answer": q.answer,
                    "answered": q.answered,
                    "required": True,
                }
                for q in self.questions
            ],
            "summary_confirmation": _json_of(self.summary_confirmation),
            "plan_approval": _json_of(self.plan_approval),
            "pending_count": self.pending_count,
            "unsupported_pending_count": self.unsupported_pending_count,
            "pending_checkpoint": self.pending_checkpoint,
            "mode": self.mode,
            "host_card": dict(self.host_card) if self.host_card else None,
            **({"origin": dict(self.origin)} if self.origin is not None else {}),
        }


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    """What the human is being asked to decide on, frozen at derivation time (§2.11 `evidence`).

    This is stored verbatim in `actions.evidence_json` so the card can still show *what was true when
    the decision was offered* after disk has moved on. The three reconciler-owned keys
    (`presence_baseline`, `presence`, `cursor_readback`) are serialised as `null` here and filled in by
    `HumanActionBroker`/`Reconciler` on the same dict, so the wire shape never changes between a fresh
    card and a reconciled one.
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
    markers: MarkersView

    def to_json(self) -> dict[str, Any]:
        return {
            "state": dict(self.state) if self.state else None,
            "audit": dict(self.audit),
            "directive": dict(self.directive) if self.directive else None,
            "artifacts": [a.to_json() for a in self.artifacts],
            "review": dict(self.review) if self.review else None,
            "acceptance_criteria": [dict(row) for row in self.acceptance_criteria],
            "findings": [_json_of(f) for f in self.findings],
            "session": dict(self.session) if self.session else None,
            "questions": self.questions.to_json() if self.questions is not None else None,
            "git": _json_of(self.git),
            "markers": self.markers.card_json(),
            "presence_baseline": None,
            "presence": None,
            "cursor_readback": None,
        }


@dataclass(frozen=True, slots=True)
class CardSeed:
    """Everything `HumanActionBroker.upsert_derived` needs to insert or refresh one card.

    A seed is not an action: it carries no status, no id and no delivery state. It says "this boundary
    exists on disk right now, here is its identity and its evidence". The broker decides whether that
    means inserting a `Queued` row, refreshing one, or leaving an in-flight row alone.
    """

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

    @property
    def headline_key(self) -> str:
        checkpoint = self.headline_params.get("checkpoint")
        if self.type == "question" and self.headline_params.get("pending") == 0 and checkpoint in (
            "summary_confirmation", "plan_approval",
        ):
            return f"action.question.{checkpoint}.headline"
        return f"action.{self.type}.headline"

    @property
    def consequence_key(self) -> str | None:
        return f"action.{self.type}.consequence.{self.consequence_variant}" if self.consequence_variant else None

    def to_json(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "repo_id": self.repo_id,
            "space": self.space,
            "intent_dir": self.intent_dir,
            "intent_key": self.intent_key,
            "intent_uuid": self.intent_uuid,
            "stage": self.stage,
            "unit": self.unit,
            "severity": self.severity,
            "risk_class": self.risk_class,
            "captured": dict(self.captured),
            "evidence": dict(self.evidence),
            "dedupe_key": self.dedupe_key,
            "waiting_since": self.waiting_since,
            "decisions": list(self.decisions),
            "headline": {"key": self.headline_key, "params": dict(self.headline_params)},
            "consequence": ({"key": self.consequence_key, "params": dict(self.headline_params)}
                            if self.consequence_key else None),
            "stage_ref": dict(self.stage_ref) if self.stage_ref else None,
            "source": self.source,
            "reason": self.reason,
            "related_action_id": self.related_action_id,
        }


@dataclass(frozen=True, slots=True)
class IntentSummary:
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
    keep_moving: bool
    interrupted: bool
    last_activity_at: str | None
    stable_boundary: Any | None
    unstable: bool
    is_active_cursor: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "repo_label": self.repo_label,
            "space": self.space,
            "intent_dir": self.intent_dir,
            "intent_key": self.intent_key,
            "uuid": self.uuid,
            "slug": self.slug,
            "scope": self.scope,
            "registry_status": self.registry_status,
            "title": self.title,
            "operational_state": self.operational_state,
            "disk": self.disk.to_json(),
            "counts": dict(self.counts),
            "open_actions": self.open_actions,
            "blocking_findings": self.blocking_findings,
            "warn_findings": self.warn_findings,
            "session": _json_of(self.session),
            "archived": self.archived,
            "paused": self.paused,
            "keep_moving": self.keep_moving,
            "interrupted": self.interrupted,
            "last_activity_at": self.last_activity_at,
            "stable_boundary": _json_of(self.stable_boundary),
            "unstable": self.unstable,
            "is_active_cursor": self.is_active_cursor,
        }


@dataclass(frozen=True, slots=True)
class IntentDetail(IntentSummary):
    """`IntentSummary` plus everything the single-intent page reads.

    Declared as a subclass so the JSON stays a superset of the summary's (the frontend type does the
    same with `extends`), which means the rail and the detail page can never disagree about a shared
    field.
    """

    state_sections: dict[str, dict[str, str]] = dataclasses_field(default_factory=dict)
    phases: tuple[tuple[str, str], ...] = ()
    stages: tuple[StageRow, ...] = ()
    findings: tuple[Any, ...] = ()
    actions: tuple[Any, ...] = ()
    directive: Directive | None = None
    recovery: dict[str, str] | None = None
    markers: MarkersView | None = None
    audit_tail: tuple[AuditEventView, ...] = ()
    questions: QuestionsView | None = None
    artifacts_count: int = 0
    artifacts_truncated: bool = False
    git: Any | None = None
    binding: Any | None = None
    engine: Any | None = None
    active_directive_stage: str | None = None

    def to_json(self) -> dict[str, Any]:
        # `IntentSummary.to_json(self)`, not `super()`: `@dataclass(slots=True)` REPLACES the class
        # object, so the `__class__` cell `super()` closes over points at the pre-decoration class and
        # raises `TypeError`. Naming the base explicitly is the only form that survives the rewrite.
        out = IntentSummary.to_json(self)
        out.update(
            {
                "state_sections": {k: dict(v) for k, v in (self.state_sections or {}).items()},
                "phases": [list(pair) for pair in self.phases],
                "stages": [row.to_json() for row in self.stages],
                "findings": [_json_of(f) for f in self.findings],
                "actions": [_json_of(a) for a in self.actions],
                "directive": _json_of(self.directive),
                "recovery": dict(self.recovery) if self.recovery else None,
                "markers": (self.markers or MarkersView.of(None)).to_json(),
                "audit_tail": [event.to_json() for event in self.audit_tail],
                "questions": self.questions.to_json() if self.questions is not None else None,
                "artifacts_count": self.artifacts_count,
                "artifacts_truncated": self.artifacts_truncated,
                "git": _json_of(self.git),
                "binding": _json_of(self.binding),
                "engine": _json_of(self.engine),
                "active_directive_stage": self.active_directive_stage,
            }
        )
        return out


@dataclass(frozen=True, slots=True)
class MapUnit:
    unit: str
    state: str
    artifacts: tuple[ArtifactMeta, ...]

    def to_json(self) -> dict[str, Any]:
        return {"unit": self.unit, "state": self.state, "artifacts": [a.to_json() for a in self.artifacts]}


@dataclass(frozen=True, slots=True)
class MapStage:
    slug: str
    number: str
    name: str
    phase: str
    state: str
    execution: str
    in_scope: bool
    mode: str
    agent: str
    reviewer: str | None
    review_class: str | None
    gate: bool
    per_unit: bool
    summary_confirmation: str | None
    consumes: tuple[str, ...]
    produces: tuple[str, ...]
    depends_on: tuple[str, ...]
    dependents: tuple[str, ...]
    elapsed_secs: int | None
    artifacts: tuple[ArtifactMeta, ...]
    skipped_reason: str | None
    units: tuple[MapUnit, ...]
    is_current: bool
    is_directive: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "number": self.number,
            "name": self.name,
            "phase": self.phase,
            "state": self.state,
            "execution": self.execution,
            "in_scope": self.in_scope,
            "mode": self.mode,
            "agent": self.agent,
            "reviewer": self.reviewer,
            "review_class": self.review_class,
            "gate": self.gate,
            "per_unit": self.per_unit,
            "summary_confirmation": self.summary_confirmation,
            "consumes": list(self.consumes),
            "produces": list(self.produces),
            "depends_on": list(self.depends_on),
            "dependents": list(self.dependents),
            "elapsed_secs": self.elapsed_secs,
            "artifacts": [a.to_json() for a in self.artifacts],
            "skipped_reason": self.skipped_reason,
            "units": [unit.to_json() for unit in self.units],
            "is_current": self.is_current,
            "is_directive": self.is_directive,
        }


@dataclass(frozen=True, slots=True)
class MapPhase:
    phase: str
    status: str
    stages: tuple[MapStage, ...]
    counts: dict[str, int]

    def to_json(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "status": self.status,
            "stages": [stage.to_json() for stage in self.stages],
            "counts": dict(self.counts),
        }


@dataclass(frozen=True, slots=True)
class MapModel:
    intent_key: str
    phases: tuple[MapPhase, ...]
    counts: dict[str, int]
    units: tuple[str, ...]
    graph_version: str | None
    stage_count: int

    def to_json(self) -> dict[str, Any]:
        return {
            "intent_key": self.intent_key,
            "phases": [phase.to_json() for phase in self.phases],
            "counts": dict(self.counts),
            "units": list(self.units),
            "graph_version": self.graph_version,
            "stage_count": self.stage_count,
        }


# --------------------------------------------------------------------------- #
# the projection
# --------------------------------------------------------------------------- #


class Projection:
    """Sync read model. Reads through `AidlcReader`; touches nothing else."""

    def __init__(self, reader: AidlcReader, consistency: "ConsistencyEngine", clock: C.Clock) -> None:
        """`consistency` is held, not called.

        §1.8 requires every finding to be passed into `summarize` / `detail` / `derive_cards`, and that
        is deliberate: if the read model could evaluate findings itself it would also be deciding what
        blocks a decision, and the reconciler's `RepoFacts` (leases, live actions, the per-intent
        first-unreadable time) are things the projection cannot see. The reference exists so callers can
        build one object graph and so the constructor matches the contract.
        """
        self._reader = reader
        self._consistency = consistency
        self._clock = clock

    # -- snapshot ----------------------------------------------------------- #

    def snapshot(
        self, repo: "RepoRecord", space: str, intent_dir: str, *, first_unreadable_at: str | None = None
    ) -> IntentSnapshot:
        """One coherent read of one intent.

        An oversized or unreadable state file does not fail the snapshot: it produces a snapshot with
        `state is None` and `stable is False`, which is exactly what `state_unreadable` needs to see.
        Raising here instead would make one bad file hide every other intent in the repository.

        `first_unreadable_at` is the reconciler's own memory of when it first saw this intent unreadable
        (only it has seen the previous ticks); it is carried on the snapshot so the API can show the same
        value `RepoFacts` gives the consistency engine.
        """
        root = Path(repo.canonical_path)
        engine_dir = repo.engine_dir or C.STUDIO_HARNESS_DIR
        harness = self._harness(root, engine_dir)
        engine_dir = harness.dir if harness is not None else engine_dir

        try:
            inputs = self._reader.stable_boundary_inputs(root, space, intent_dir)
        except StudioError as exc:
            # `too_large` only. A `bad_path` must keep propagating so a handler answers 400/404 for a
            # malformed `{intent}` instead of serving a plausible-looking empty intent — the reconciler
            # never produces one (it enumerates directories through `INTENT_DIR_RE`), so a bad path here
            # is a caller error or an injection attempt, not a degraded record.
            if exc.code != "too_large":
                raise
            inputs = None

        state_snap = inputs.state_snap if inputs is not None else None
        state = inputs.state if inputs is not None else None
        audit = inputs.audit if inputs is not None else self._empty_audit()
        directive = inputs.directive if inputs is not None else None
        questions = inputs.questions if inputs is not None else None
        stage = inputs.stage if inputs is not None else None
        stable = bool(inputs.stable) if inputs is not None else False
        taken_at = inputs.taken_at if inputs is not None else self._clock.iso()

        active_space = self._reader.active_space(root)
        cursor, dangling = self._reader.active_intent(root, space)
        rows = self._reader.registry(root, space)
        row = match_intent_row(rows, intent_dir)

        graph = tuple(self._reader.read_stage_graph(root, engine_dir))
        grid = self._reader.read_scope_grid(root, engine_dir)
        scopes = tuple(self._reader.read_scopes(root, engine_dir))
        markers = self._reader.read_markers(root, space, intent_dir)
        # `list_units` re-reads the state file to learn which construction directories are stages, so an
        # oversized state file takes the unit list with it. Losing the units is acceptable (they are
        # presentational); letting the whole snapshot fail is not.
        units = _tolerate_too_large(lambda: tuple(self._reader.list_units(root, space, intent_dir)), ())

        unit = None
        if directive is not None and directive.matches_state:
            unit = directive.unit
        if unit is None and state is not None:
            unit = state.active_unit
        audit_question_attempt = self._reader._question_claim_generation(
            root, space, intent_dir, unit, audit
        )
        if questions is not None and questions.origin and audit_question_attempt != (
            questions.origin.get("attempt_generation"), True
        ):
            questions = None

        current = self._read_stage_evidence(
            root, engine_dir, space, intent_dir, state, graph, stage, unit, questions
        )
        stage_dir_rel = current.stage_dir_rel
        stage_artifacts = current.artifacts
        review = current.review
        review_unreadable = current.review_unreadable
        criteria = current.acceptance_criteria

        # Every OTHER boundary row gets the same read, so a gate card is never given a different stage's
        # artifacts, verdict or questions. Almost always empty: an intent normally has one open boundary
        # and it is the current stage, so this costs nothing in the ordinary case, and costs one directory
        # read per extra `[?]` in the case where showing the wrong evidence would mean a person approving
        # work they were never looking at.
        others: dict[str, StageEvidence] = {}
        for boundary in (state.stages if state is not None else ()):
            if boundary.mark not in ("?", "R", "r"):
                continue
            if boundary.slug == stage or boundary.slug in others:
                continue
            others[boundary.slug] = self._read_stage_evidence(
                root, engine_dir, space, intent_dir, state, graph, boundary.slug, None, None
            )

        return IntentSnapshot(
            repo_id=repo.repo_id,
            space=space,
            intent_dir=intent_dir,
            intent_key=intent_key_for(space, intent_dir),
            row=row,
            state_snap=state_snap,
            state=state,
            directive=directive,
            audit=audit,
            markers=markers,
            questions=questions,
            stage_artifacts=stage_artifacts,
            review=review,
            review_unreadable=review_unreadable,
            graph=graph,
            grid=grid,
            scopes=scopes,
            engine=harness,
            taken_at=taken_at,
            stable=stable,
            is_active=(active_space == space and cursor == intent_dir),
            dangling_cursor=dangling,
            first_unreadable_at=first_unreadable_at,
            repo_label=repo.label,
            canonical_path=repo.canonical_path,
            engine_dir=engine_dir,
            stage=stage,
            unit=unit,
            stage_dir_rel=stage_dir_rel,
            units=units,
            cursor=cursor,
            active_space=active_space,
            layout=self._reader.layout(root),
            acceptance_criteria=criteria,
            other_stage_evidence=others,
            artifact_dir_rel=current.artifact_dir_rel,
            audit_question_attempt=audit_question_attempt,
        )

    @staticmethod
    def _empty_audit() -> AuditBundle:
        """The audit bundle for a record Studio bailed out of before it read any shard.

        `complete=False` here, unlike the reader's answer for a record that genuinely has no `audit/`
        directory (nothing truncated → `complete=True`): "we did not look" and "there is nothing" are
        different claims, and evidence built on the first must say so (`audit_incomplete`).
        """
        return AuditBundle(events=[], shards=[], complete=False)

    def _harness(self, root: Path, preferred: str) -> "HarnessDir | None":
        """The harness the intent is driven by: the registered one when present, else the first that
        actually has a `tools/` tree. A repository can hold two harnesses and picking the wrong one
        would read another framework's stage graph."""
        dirs = self._reader.detect_harness_dirs(root)
        for harness in dirs:
            if harness.dir == preferred:
                return harness
        for harness in dirs:
            if harness.has_utility:
                return harness
        return dirs[0] if dirs else None

    @staticmethod
    def row_phase(state: StateFile | None, stage: str | None) -> str | None:
        row = state.row(stage) if (state is not None and stage) else None
        return row.phase if row is not None else None

    def _read_stage_evidence(
        self,
        root: Path,
        engine_dir: str,
        space: str,
        intent_dir: str,
        state: "StateFile | None",
        graph: Sequence[StageNode],
        slug: str | None,
        unit: str | None,
        questions: "QuestionsFile | None",
    ) -> StageEvidence:
        """Read one stage's directory: its artifacts, its reviewer verdict, its questions file.

        ``questions`` is passed in for the current stage because the boundary read already found it, and
        because that file is what proves which of the two Construction layouts the record uses. For any
        other stage it arrives as ``None`` and is looked up from the resolved directory.

        ``too_large`` is tolerated throughout rather than fatal: the reconciler skips an intent whose
        snapshot raises, so one produced artifact over the render cap used to make an on-disk boundary
        produce no card at all. A missing verdict is a gap in the evidence; a missing card is a gap in the
        product.
        """
        node = next((n for n in graph if n.slug == slug), None)
        stage_dir_rel = self._stage_dir_rel(space, intent_dir, state, node, slug, unit, questions)
        if stage_dir_rel is None:
            return StageEvidence(None, (), None, questions)

        # Reverse engineering writes shared repository knowledge, while questions and stage notes
        # still belong to the intent's stage record. Keep the two paths distinct.
        artifact_dir_rel = stage_dir_rel
        if C.artifact_output_location(node) == "codekb":
            artifact_dir_rel = f"aidlc/spaces/{space}/codekb/{root.name}"
        artifacts, artifacts_capped = _tolerate_too_large_flag(
            lambda: tuple(self._reader.stage_artifacts(root, artifact_dir_rel, node)), ()
        )
        review, review_capped = _tolerate_too_large_flag(
            lambda: self._review_of(root, artifacts, node), None
        )
        found = questions
        if found is None and slug:
            found = _tolerate_too_large(
                lambda: self._reader.read_questions(root, stage_dir_rel, slug), None
            )
        criteria: tuple[str, ...] = ()
        phase = node.phase if node is not None else (self.row_phase(state, slug) or "")
        if phase:
            meta = _tolerate_too_large(
                lambda: self._reader.read_stage_file(root, engine_dir, phase, slug or ""), None
            )
            criteria = tuple(meta.acceptance_criteria) if meta is not None else ()
        return StageEvidence(
            stage_dir_rel=stage_dir_rel,
            artifacts=artifacts,
            review=review,
            questions=found,
            review_unreadable=review_capped or artifacts_capped,
            acceptance_criteria=criteria,
            artifact_dir_rel=artifact_dir_rel,
        )

    def _stage_dir_rel(
        self,
        space: str,
        intent_dir: str,
        state: StateFile | None,
        node: StageNode | None,
        stage: str | None,
        unit: str | None,
        questions: QuestionsFile | None,
    ) -> str | None:
        """Where the current stage's files live.

        The questions file the reader already found is preferred, because it proves which of the two
        Construction layouts (`construction/<unit>/<slug>` vs `construction/<slug>`) this record uses;
        only when there is no questions file does this fall back to composing the path.
        """
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
        """The parsed `## Review` section of the stage's primary artifact.

        Primary = the graph's own `review_artifact` when it declares one, else the first declared
        `produces` file that exists, else the first plain artifact in path order. Reviewer verdicts live
        inside the artifact they judge, so guessing a different file would attach one stage's verdict to
        another stage's evidence digest.
        """
        primary = self._primary_artifact(artifacts, node)
        if primary is None:
            return None
        snap = self._reader.read_artifact(root, primary.relpath)
        if snap is None:
            return None
        return parse_review_section(snap.text)

    @staticmethod
    def _primary_artifact(
        artifacts: Sequence[ArtifactMeta], node: StageNode | None
    ) -> ArtifactMeta | None:
        """The one artifact a stage's `## Review` appendix can be in.

        `review_artifact` (2.7.1+) wins over `produces[0]`, because on four stages they disagree —
        `functional-design` reviews `functional-spec` while producing `entities` first, and the same
        holds for `nfr-requirements`, `nfr-design` and `infrastructure-design`. The reviewer protocol
        allows the appendix in `review_artifact` only, so guessing `produces[0]` there reads a file that
        can never hold a verdict and reports the stage as unreviewed. Older graphs declare no
        `review_artifact`, hence the unchanged `produces` scan behind it.
        """
        by_name = {a.name: a for a in artifacts}
        declared_names: list[str] = []
        if node is not None:
            if node.review_artifact:
                declared_names.append(node.review_artifact)
            declared_names.extend(node.produces)
        for declared in declared_names:
            for candidate in (f"{declared}.md", f"{declared}.json", declared):
                if candidate in by_name:
                    return by_name[candidate]
        for artifact in artifacts:
            if artifact.kind == "artifact":
                return artifact
        return None

    # -- captured / evidence ------------------------------------------------ #

    def captured(self, snap: IntentSnapshot, stage: str | None) -> dict[str, Any]:
        """The compare-and-submit set for one boundary of this snapshot (§1.6.4 / C30).

        Computed the same way for every card type and recomputed the same way at submit time — that is
        the only way the null-vs-non-null rule (review P26.3) can work: a gate captured while the stage
        had no questions file must go stale when one appears, and a set built with a per-type field list
        could never detect that.

        `question_digest` and `evidence_digest` come from the stage's OWN directory, for every boundary
        the snapshot read — not just the current one. They used to be `null` for any other `[?]` row,
        which made a second gate impossible to invalidate: its evidence could change on disk and the
        captured set would not notice, so compare-and-submit had nothing to compare. `snapshot` reads
        every boundary row's directory, and `submit` recomputes from a fresh snapshot the same way, so
        the two sides agree by construction.
        """
        state_sha = snap.state_sha256
        token = (
            boundary_token(
                state_sha,
                list(snap.audit.events),
                snap.state.current_stage if snap.state is not None else None,
                snap.state.revision_count if snap.state is not None else 0,
            )
            if state_sha
            else None
        )
        evidence = snap.stage_evidence(stage)
        # A stage the snapshot did not read at all (not the current stage and not an open boundary) still
        # answers `None` for both digests, because nothing was read to digest — `stage_evidence` falls
        # back to the current stage's bundle only when the slug IS the current stage.
        known = stage is not None and (stage == snap.stage or stage in snap.other_stage_evidence)
        question_digest = (
            evidence.questions.sha256 if (known and evidence.questions is not None) else None
        )
        digest = evidence_digest(list(evidence.artifacts), evidence.review) if known else None
        return {
            "state_hash": state_sha,
            "boundary_token": token,
            "question_digest": question_digest,
            "stage_attempt": stage_attempt(list(snap.audit.events), stage) if stage else None,
            "evidence_digest": digest,
            "is_active": snap.is_active,
            "captured_at": snap.taken_at,
            "stable": snap.stable,
        }

    def evidence(
        self,
        snap: IntentSnapshot,
        *,
        findings: Sequence[Any] = (),
        binding: Any | None = None,
        slot: Any | None = None,
        git: Any | None = None,
        host_card: dict | None = None,
        stage: str | None = None,
    ) -> EvidenceSnapshot:
        """The evidence a card shows.

        ``stage`` names WHICH boundary's evidence to render, defaulting to the snapshot's current stage.
        It exists because an intent can hold more than one ``[?]`` row: this used to be built once and
        shared by every seed, so a gate card for a second boundary listed the current stage's artifacts,
        quoted the current stage's reviewer verdict and offered the current stage's questions to a person
        about to approve a different stage's work. Everything outside the stage directory — the state file,
        the audit, the session, git, the markers — is the same for every card and is still built once.
        """
        target = stage if stage is not None else snap.stage
        bundle = snap.stage_evidence(target)
        state_json: dict | None = None
        if snap.state_snap is not None:
            state = snap.state
            state_json = {
                "relpath": snap.state_snap.relpath,
                "sha256": snap.state_snap.sha256,
                "size": snap.state_snap.size,
                "mtime_ns": snap.state_snap.mtime_ns,
                "current_stage": state.current_stage if state is not None else None,
                "status": state.status if state is not None else None,
                "lifecycle_phase": state.lifecycle_phase if state is not None else None,
                "revision_count": state.revision_count if state is not None else 0,
                "stable": snap.stable,
            }

        last = snap.audit.events[-1] if snap.audit.events else None
        boundary = latest_event(list(snap.audit.events), C.BOUNDARY_EVENTS)
        audit_json = {
            "shards": [
                {"relpath": shard.relpath, "size": shard.size, "truncated": shard.truncated}
                for shard in snap.audit.shards
            ],
            "last_event": {"type": last.event, "ts": last.timestamp} if last is not None else None,
            "boundary_event": (
                {
                    "type": boundary.event,
                    "ts": boundary.timestamp,
                    "stage": boundary.fields.get("Stage"),
                    "shard": boundary.shard,
                    "pos": boundary.pos,
                }
                if boundary is not None
                else None
            ),
            "complete": snap.audit.complete,
        }

        review_json: dict | None = None
        if bundle.review is not None:
            verdict, review_findings = bundle.review
            reviewer = next((f.reviewer for f in review_findings if f.reviewer), None)
            iteration = next((f.iteration for f in review_findings if f.iteration is not None), None)
            node = snap.node(target)
            review_json = {
                "verdict": verdict,
                "findings": [f.to_json() for f in review_findings],
                "reviewer": reviewer or (node.reviewer if node is not None else None),
                "review_class": node.review_class if node is not None else None,
                "iteration": iteration,
                # False when the reviewer wrote their findings in a shape Studio could not enumerate. An
                # empty `findings` list then means "could not tell", not "no open blockers", and the card
                # must not affirm the second (`review.findings_parsed`, §2.11).
                "findings_parsed": bool(getattr(review_findings, "findings_parsed", True)),
            }
        elif bundle.review_unreadable:
            # The verdict exists on disk and is in a file over the render cap. Saying nothing here would
            # read as "this stage has no reviewer", which is a different and wrong claim.
            review_json = {
                "verdict": None,
                "findings": [],
                "reviewer": None,
                "review_class": (
                    snap.node(target).review_class if snap.node(target) is not None else None
                ),
                "iteration": None,
                "findings_parsed": False,
                "unreadable": True,
            }

        return EvidenceSnapshot(
            state=state_json,
            audit=audit_json,
            directive=snap.directive.to_json() if snap.directive is not None else None,
            artifacts=bundle.artifacts,
            review=review_json,
            # `met` is deliberately null: deciding whether an acceptance criterion is satisfied is the
            # reviewer's job inside AI-DLC, and a Studio guess would look like an authority it is not.
            acceptance_criteria=tuple(
                {"text": text, "met": None, "why_key": None} for text in bundle.acceptance_criteria
            ),
            findings=tuple(findings),
            session=self._session_json(binding, slot),
            questions=self.questions_view(snap, host_card=host_card, stage=target),
            git=git,
            markers=MarkersView.of(snap.markers),
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
        }

    def questions_view(
        self, snap: IntentSnapshot, *, host_card: dict | None = None, stage: str | None = None
    ) -> QuestionsView | None:
        """The questions a card offers, for ``stage`` (default: the snapshot's current stage).

        ``unit`` is only known for the current stage: the per-unit path of another boundary was resolved
        from its own directory, and reporting this stage's unit against it would name the wrong one.
        """
        target = stage if stage is not None else snap.stage
        bundle = snap.stage_evidence(target)
        questions = bundle.questions
        if questions is None or not target:
            return None
        native_wait = host_card is not None and "ask_id" in host_card
        file_form = (
            (C.GROUPED_ANSWERS_VERIFIED or bool(host_card))
            and snap.stable and file_questions_support_forms(questions)
        )
        return QuestionsView(
            relpath=questions.relpath,
            sha256=questions.sha256,
            stage=target,
            unit=snap.unit if target == snap.stage else None,
            questions=questions.questions,
            summary_confirmation=questions.summary_confirmation,
            plan_approval=questions.plan_approval,
            pending_count=questions.pending_count,
            pending_checkpoint=questions.pending_checkpoint,
            mode="structured" if not native_wait and (questions.origin or file_form) else "degraded",
            host_card=host_card,
            origin=questions.origin,
            unsupported_pending_count=questions.unsupported_pending_count,
        )

    # -- operational state -------------------------------------------------- #

    def operational_state(
        self,
        snap: IntentSnapshot,
        *,
        binding: Any | None = None,
        live_actions: Sequence[Any] = (),
        breaker_open: bool = False,
        host: Any | None = None,
        findings: Sequence[Any] = (),
        slot: Any | None = None,
    ) -> str:
        """PRD §11.2 as a first-match-wins ladder. Pure; exposed so the table can be pinned directly.

        Order is a safety property (see the module docstring). Two rules deserve their own note:

        * Rule 3 fires only while the force stop is *in flight*. Once the slot has stopped, the
          `interrupted_session` finding takes over through rule 2, so the only path out of
          `Interrupted` is `ReconciliationRequired` and the human's `acknowledge` (C28, review P17) —
          `Interrupted → Queued` is unreachable by construction.
        * Rule 8 counts only *command* actions, plus anything in `Delivering`. A derived gate card
          sitting in `Queued` means "the human has not decided yet", not "a turn is queued"; ranking it
          as `Queued` would hide every open gate behind a spinner (review P08).
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
        if commands_queued or any(status == "Delivering" for status in statuses):
            return "Queued"

        if self._waiting_for_human(snap):
            return "WaitingForYou"

        newest = self._newest_action(live_actions)
        if newest is not None and str(_attr(newest, "status", "")) == "Failed":
            return "Failed" if self._deterministic(newest) or breaker_open else "RetryEligible"

        if bool(_attr(binding, "paused", False)):
            return "Paused"
        return "Idle"

    @staticmethod
    def _waiting_for_human(snap: IntentSnapshot) -> bool:
        state = snap.state
        if state is not None and any(row.mark in ("?", "R", "r") for row in state.stages):
            return True
        questions = snap.questions
        return questions is not None and (questions.pending_count > 0 or bool(questions.pending_checkpoint))

    @staticmethod
    def _newest_action(actions: Sequence[Any]) -> Any | None:
        if not actions:
            return None
        return max(
            actions,
            key=lambda a: (
                str(_attr(a, "updated_at", "") or ""),
                str(_attr(a, "created_at", "") or ""),
                str(_attr(a, "action_id", "") or ""),
            ),
        )

    @staticmethod
    def _deterministic(action: Any) -> bool:
        """Whether a failed action's fingerprint class is one that retrying cannot fix.

        Unknown classes count as deterministic: offering `Retry now` for a failure Studio cannot
        classify would invite the user to replay something that will fail the same way.
        """
        evidence = _attr(action, "evidence", {}) or {}
        klass = _attr(evidence, "fingerprint_class")
        if not klass:
            fingerprint = _attr(action, "retry_fingerprint") or ""
            klass = str(fingerprint).split(".", 1)[0] if fingerprint else ""
        return str(klass) not in C.TRANSIENT_FINGERPRINT_CLASSES

    # -- stable boundary ---------------------------------------------------- #

    def stable_boundary(
        self,
        snap: IntentSnapshot,
        *,
        live_actions: Sequence[Any] = (),
        busy_reasons: Sequence[str] = (),
        lease_active: bool = False,
    ) -> StableBoundary:
        """All five PRD §12.2 conditions, with every unmet one named.

        Lives here rather than in the reconciler because the marker and the boundary token it reports
        are the projection's own reading of the snapshot; the reconciler supplies only the three facts
        that are not on disk (in-flight actions, slot busyness, a live lease).
        """
        reasons: list[str] = []
        if not snap.stable:
            reasons.append("state_unstable")

        marker = self._boundary_marker(snap)
        stage = self._boundary_stage(snap, marker)
        if marker is None:
            reasons.append("not_at_boundary")

        if any(
            str(_attr(action, "status", "")) in ("Delivering", "Delivered", "Processing", "DeliveryUncertain")
            for action in live_actions
        ):
            reasons.append("action_in_flight")

        if tuple(busy_reasons) or lease_active:
            reasons.append("session_busy")

        if not snap.is_active and snap.cursor is not None:
            reasons.append("cursor_mismatch")

        captured = self.captured(snap, stage)
        return StableBoundary(
            stable=not reasons,
            reasons=tuple(reasons),
            stage=stage,
            marker=marker,
            boundary_token=captured["boundary_token"],
            recorded_at=snap.taken_at,
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
        """The stage the marker belongs to, which is not always `Current Stage`.

        A `[?]` row can legitimately be a stage other than the one the state file calls current (the
        engine advances `Current Stage` before the previous gate closes in some paths), and a boundary
        recorded against the wrong stage would authorise the wrong gate.
        """
        state = snap.state
        wanted = {"[?]": ("?",), "[R]": ("R", "r")}.get(marker or "")
        if state is not None and wanted:
            for row in state.stages:
                if row.mark in wanted:
                    return row.slug
        return snap.stage

    # -- card derivation ---------------------------------------------------- #

    def derive_cards(
        self,
        snap: IntentSnapshot,
        *,
        findings: Sequence[Any],
        binding: Any | None,
        install: "InstallHealth | None",
        breakers: Sequence[Any],
        live_actions: Sequence[Any],
        slot: Any | None = None,
        git: Any | None = None,
        host_card: dict | None = None,
    ) -> list[CardSeed]:
        """One seed per live boundary, in queue order (§1.8 table).

        Two rows of that table produce no seed here on purpose:

        * `delivery_uncertain` — the card *is* the uncertain action, re-typed for the queue by
          `queue_type_for`. Emitting a seed would create a second live row for a decision that is
          already recorded, which is the duplicate PRD §12.1 forbids.
        * `budget_stop` — reserved and never produced in v1; there are no unattended turns without the
          machine lane (C16). The type stays in the tables so the UI template set is stable.
        """
        seeds: list[CardSeed] = []
        evidence = self.evidence(
            snap, findings=findings, binding=binding, slot=slot, git=git, host_card=host_card
        )
        base = evidence.to_json()
        uuid = snap.row.uuid if snap.row is not None else None

        def evidence_for(slug: str | None) -> dict:
            """``base`` for the current stage; a stage's own evidence for any other open boundary.

            Built lazily and only for the slugs that need it, so the ordinary single-boundary intent pays
            nothing. The cards that are not about a stage directory at all (failure, breaker, install,
            recovery) keep using ``base`` directly.
            """
            if slug is None or slug == snap.stage or slug not in snap.other_stage_evidence:
                return base
            return self.evidence(
                snap, findings=findings, binding=binding, slot=slot, git=git,
                host_card=host_card, stage=slug,
            ).to_json()

        seeds.extend(self._recovery_seeds(snap, findings, base, uuid, slot=slot, binding=binding))
        seeds.extend(self._gate_seeds(snap, evidence_for, uuid))
        seeds.extend(self._question_seeds(snap, base, uuid, slot=slot))
        seeds.extend(self._missing_input_seeds(snap, base, uuid))
        seeds.extend(self._failure_seeds(snap, base, uuid, live_actions))
        seeds.extend(self._breaker_seeds(snap, base, uuid, breakers))
        seeds.extend(self._install_seeds(snap, base, uuid, install))
        seeds.extend(self._revision_seeds(snap, evidence_for, uuid))
        seeds.sort(
            key=lambda seed: (
                priority_group_for(seed.type),
                SEVERITY_RANK.get(seed.severity, 3),
                seed.dedupe_key,
            )
        )
        return seeds

    def _seed(
        self,
        snap: IntentSnapshot,
        action_type: str,
        *,
        stage: str | None,
        token: str | None,
        evidence: dict,
        waiting_since: str,
        decisions: Sequence[str],
        headline_params: Mapping[str, Any] | None = None,
        consequence: str | None = "__default__",
        risk_class: str | None = None,
        reason: str | None = None,
        related_action_id: str | None = None,
        intent_uuid: str | None = None,
        dedupe_intent_key: str | None = None,
    ) -> CardSeed:
        variant = SEED_CONSEQUENCE.get(action_type) if consequence == "__default__" else consequence
        return CardSeed(
            type=action_type,
            repo_id=snap.repo_id,
            space=snap.space,
            intent_dir=snap.intent_dir,
            intent_key=snap.intent_key,
            intent_uuid=intent_uuid,
            stage=stage,
            unit=snap.unit if stage == snap.stage else None,
            severity=SEED_SEVERITY[action_type],
            risk_class=risk_class or SEED_RISK_CLASS[action_type],
            captured=self.captured(snap, stage),
            # a copy per seed: several seeds share one derivation of the evidence, and a downstream
            # mutation on one card's blob must not silently rewrite another's
            evidence=dict(evidence),
            dedupe_key=dedupe_key(
                action_type, snap.repo_id, dedupe_intent_key or snap.intent_key, stage, token
            ),
            waiting_since=waiting_since,
            decisions=tuple(decisions),
            headline_params=dict(headline_params or {}),
            consequence_variant=variant,
            stage_ref=self.stage_ref(snap, stage),
            reason=reason,
            related_action_id=related_action_id,
        )

    def stage_ref(self, snap: IntentSnapshot, slug: str | None) -> dict | None:
        """`{slug, number, name, phase, unit}` for a card's stage (§2.11), or `None`."""
        if not slug:
            return None
        node = snap.node(slug)
        row = snap.row_for(slug)
        return {
            "slug": slug,
            "number": node.number if node is not None else None,
            "name": node.name if node is not None else None,
            "phase": (node.phase if node is not None else None) or (row.phase if row is not None else None),
            "unit": snap.unit if slug == snap.stage else None,
        }

    # -- one method per card type ------------------------------------------- #

    def _gate_seeds(
        self, snap: IntentSnapshot, evidence_for: Any, uuid: str | None
    ) -> list[CardSeed]:
        """One card per `[?]` row. The `[?]` mark is the gate: AI-DLC writes it and only AI-DLC clears
        it, which is why the card cannot be dismissed while the mark is on disk (FR-ACT-007).

        ``evidence_for(slug)`` rather than one shared dict: a second open boundary must show ITS OWN
        artifacts, verdict and questions, or the person approving is reading another stage's work.
        """
        if snap.state is None:
            return []
        out: list[CardSeed] = []
        for row in snap.state.stages:
            if row.mark != "?":
                continue
            captured = self.captured(snap, row.slug)
            attempt = captured["stage_attempt"] or 1
            # `C.DECISIONS` is the single decision vocabulary: the broker validates a submitted decision
            # against the same row, so a card can never offer one it would then refuse. The conductor
            # only offers "Accept as-is" after three rejection cycles (05 §5.1), so Studio withholds it
            # until the fourth attempt and `submit` refuses it earlier (C29/P24).
            decisions = [
                decision
                for decision in C.DECISIONS["gate"]
                if decision != "accept_as_is" or attempt >= C.ACCEPT_AS_IS_MIN_ATTEMPT
            ]
            final = self._is_final_stage(snap, row.slug)
            out.append(
                self._seed(
                    snap,
                    "gate",
                    stage=row.slug,
                    token=captured["boundary_token"],
                    evidence=evidence_for(row.slug),
                    waiting_since=self._gate_opened_at(snap, row.slug),
                    decisions=decisions,
                    headline_params={"stage": row.slug, "attempt": attempt},
                    consequence="final_stage" if final else "approve_unlocks",
                    intent_uuid=uuid,
                )
            )
        return out

    def _revision_seeds(
        self, snap: IntentSnapshot, evidence_for: Any, uuid: str | None
    ) -> list[CardSeed]:
        """`[R]` is informational: the engine is revising and no human decision is pending (C18)."""
        if snap.state is None:
            return []
        out: list[CardSeed] = []
        for row in snap.state.stages:
            if row.mark not in ("R", "r"):
                continue
            captured = self.captured(snap, row.slug)
            out.append(
                self._seed(
                    snap,
                    "revision",
                    stage=row.slug,
                    token=captured["boundary_token"],
                    evidence=evidence_for(row.slug),
                    waiting_since=self._latest_ts(snap, ("STAGE_REVISING", "GATE_REJECTED"), row.slug),
                    decisions=(),
                    headline_params={"stage": row.slug, "revision_count": snap.state.revision_count},
                    intent_uuid=uuid,
                )
            )
        return out

    def _question_seeds(
        self, snap: IntentSnapshot, base: dict, uuid: str | None, *, slot: Any | None
    ) -> list[CardSeed]:
        """One card for the current stage's pending questions or checkpoint.

        The decisions follow *what is pending*, not the card type: blank `Q` tags mean `answers`, a
        blank `## Consolidated Summary Confirmation` means `confirm_summary`, a blank `## Plan Approval`
        means `approve_plan` / `request_plan_changes` (C34, review P09). A file can hold both a pending
        question and an answered checkpoint, and the summary checkpoint wins over the plan one because
        the engine writes them in that order.
        """
        questions = snap.questions
        needs_input = bool(_attr(slot, "needs_input", False))
        if questions is None:
            return []
        pending_count = questions.pending_count
        checkpoint = questions.pending_checkpoint
        if pending_count == 0 and not checkpoint and not needs_input:
            return []
        decisions: list[str] = []
        if pending_count > 0:
            decisions.append("answers")
        if checkpoint == "summary_confirmation":
            decisions.append("confirm_summary")
        elif checkpoint == "plan_approval":
            decisions.extend(("approve_plan", "request_plan_changes"))
        if not decisions:
            # The host says the slot wants input but the file shows nothing pending: offer the file's
            # own vocabulary rather than guessing, so the human answers in the conversation.
            decisions.append("answers")
        if questions.unsupported_pending_count:
            # Keep the card and human boundary, but never map unsupported follow-ups onto Q ids
            # or let a checkpoint confirmation skip them.
            decisions = []
        captured = self.captured(snap, snap.stage)
        return [
            self._seed(
                snap,
                "question",
                stage=snap.stage,
                token=captured["question_digest"] or captured["boundary_token"],
                evidence=base,
                waiting_since=self._question_asked_at(snap),
                decisions=decisions,
                headline_params={
                    "stage": snap.stage,
                    "pending": pending_count,
                    "checkpoint": checkpoint,
                },
                consequence=checkpoint if pending_count == 0 and checkpoint in (
                    "summary_confirmation", "plan_approval",
                ) else "__default__",
                intent_uuid=uuid,
                reason=checkpoint or ("needs_input" if needs_input and pending_count == 0 else "questions"),
            )
        ]

    def _missing_input_seeds(self, snap: IntentSnapshot, base: dict, uuid: str | None) -> list[CardSeed]:
        """Two distinct "Studio cannot act until you tell it something" cases.

        A dangling or absent cursor is a *space-level* fact, so its dedupe key names the space rather
        than the intent: every intent in the space derives the same seed and the live-dedupe index
        collapses them into one card instead of one per intent. Its decision is the admin-lane
        `pick_intent` (an engine verb under an admin lease), never a prompt — `/aidlc intent <name>`
        as a prompt would mint a `HUMAN_TURN` for a navigation step (C22).
        """
        out: list[CardSeed] = []
        if snap.dangling_cursor or snap.cursor is None:
            reason = "dangling_cursor" if snap.dangling_cursor else "no_cursor"
            out.append(
                self._seed(
                    snap,
                    "missing_input",
                    stage=None,
                    token=reason,
                    evidence=base,
                    waiting_since=snap.taken_at,
                    decisions=("pick_intent",),
                    headline_params={"reason": reason, "space": snap.space, "cursor": snap.cursor},
                    risk_class="admin",
                    reason=reason,
                    intent_uuid=uuid,
                    dedupe_intent_key=f"{snap.space}~*",
                )
            )
        scope = (snap.state.project.get("Scope", "") if snap.state is not None else "").strip()
        if snap.state is not None and not snap.state.header_only and not scope:
            out.append(
                self._seed(
                    snap,
                    "missing_input",
                    stage=None,
                    token="missing_scope",
                    evidence=base,
                    waiting_since=snap.taken_at,
                    decisions=("provide_input",),
                    headline_params={"reason": "missing_scope", "kind": "scope"},
                    reason="missing_scope",
                    intent_uuid=uuid,
                )
            )
        return out

    def _recovery_seeds(
        self,
        snap: IntentSnapshot,
        findings: Sequence[Any],
        base: dict,
        uuid: str | None,
        *,
        slot: Any | None,
        binding: Any | None,
    ) -> list[CardSeed]:
        """One card for the blocking findings that need a human to agree what happened.

        A single card, not one per finding: the human's move (`acknowledge` after reading the evidence,
        `rebind_session`, `mark_not_delivered`) is the same regardless of how many contradictions were
        found, and N critical cards for one broken intent would bury every other repository.
        """
        codes = [
            str(_attr(f, "code", ""))
            for f in findings
            if str(_attr(f, "severity", "")) == "blocking"
            and str(_attr(f, "code", "")) not in RECOVERY_EXCLUDED_FINDINGS
        ]
        lost = self._session_lost_mid_stage(snap, slot=slot, binding=binding)
        if lost:
            codes.append("session_lost_mid_stage")
        if not codes:
            return []
        captured = self.captured(snap, snap.stage)
        token = captured["boundary_token"] or _sha12(*sorted(set(codes)))
        return [
            self._seed(
                snap,
                "recovery",
                stage=snap.stage,
                token=token,
                evidence={**base, "recovery_codes": sorted(set(codes))},
                waiting_since=snap.taken_at,
                decisions=("rebind_session", "mark_not_delivered", "acknowledge"),
                headline_params={"stage": snap.stage, "codes": sorted(set(codes))},
                reason=sorted(set(codes))[0],
                intent_uuid=uuid,
            )
        ]

    @staticmethod
    def _session_lost_mid_stage(snap: IntentSnapshot, *, slot: Any | None, binding: Any | None) -> bool:
        """The bound slot is gone while a stage row is `[-]`.

        Only claimed when a binding exists and the host was actually consulted (`slot is None` with no
        binding means "not bound", which is not a loss).
        """
        if not _attr(binding, "slot_key"):
            return False
        if slot is not None:
            return False
        state = snap.state
        return state is not None and any(row.mark == "-" for row in state.stages)

    def _failure_seeds(
        self, snap: IntentSnapshot, base: dict, uuid: str | None, live_actions: Sequence[Any]
    ) -> list[CardSeed]:
        """A deterministic `Failed` action, or a dense `ERROR_LOGGED` burst for the current stage.

        Isolated `ERROR_LOGGED` rows are the conductor correcting itself (`Unknown subcommand`,
        `Refusing to record this answer` — FORMAT-NOTES §6) and never raise a card; three inside ten
        minutes is a loop the human has to break.
        """
        newest = self._newest_action(live_actions)
        failed = (
            newest
            if newest is not None
            and str(_attr(newest, "status", "")) == "Failed"
            and self._deterministic(newest)
            else None
        )
        burst = self._error_burst(snap)
        if failed is None and burst is None:
            return []
        captured = self.captured(snap, snap.stage)
        waiting = (
            str(_attr(failed, "resolved_at", "") or _attr(failed, "updated_at", "") or snap.taken_at)
            if failed is not None
            else burst
        )
        return [
            self._seed(
                snap,
                "failure",
                stage=snap.stage,
                token=captured["boundary_token"] or "failure",
                evidence={**base, "error_burst_at": burst},
                waiting_since=waiting or snap.taken_at,
                decisions=("retry_now", "keep_paused"),
                headline_params={"stage": snap.stage, "burst": burst is not None},
                reason="action_failed" if failed is not None else "error_burst",
                related_action_id=str(_attr(failed, "action_id", "")) or None if failed is not None else None,
                intent_uuid=uuid,
            )
        ]

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

    def _breaker_seeds(
        self, snap: IntentSnapshot, base: dict, uuid: str | None, breakers: Sequence[Any]
    ) -> list[CardSeed]:
        """One card per open breaker. Keyed on the fingerprint, not the boundary: the breaker outlives
        the boundary that opened it, and re-creating the card on every boundary move would reset the
        human's context on the very failure they are trying to understand."""
        out: list[CardSeed] = []
        for breaker in breakers:
            if not _attr(breaker, "opened_at"):
                continue
            key = str(_attr(breaker, "key", "") or "")
            fingerprint = str(_attr(breaker, "fingerprint", "") or key.rsplit(":", 1)[-1])
            out.append(
                self._seed(
                    snap,
                    "circuit_breaker",
                    stage=snap.stage,
                    token=fingerprint or "breaker",
                    evidence={**base, "breaker": _json_of(breaker) or {"key": key}},
                    waiting_since=str(_attr(breaker, "opened_at")),
                    decisions=("retry_now", "keep_paused"),
                    headline_params={
                        "stage": snap.stage,
                        "count": int(_attr(breaker, "count", 0) or 0),
                        "fingerprint_class": _attr(breaker, "fingerprint_class"),
                    },
                    reason=fingerprint or None,
                    intent_uuid=uuid,
                )
            )
        return out

    def _install_seeds(
        self, snap: IntentSnapshot, base: dict, uuid: str | None, install: "InstallHealth | None"
    ) -> list[CardSeed]:
        """Navigation only (no decisions): an interrupted install is repaired on the Repos page under an
        admin lease, never from the queue. `install_conflict` is `attention`, not `blocking`, because
        nothing was written — the transaction rolled back (FR-ACT-008)."""
        if install is None or str(_attr(install, "status", "")) != "recovery_required":
            return []
        return [
            self._seed(
                snap,
                "install_conflict",
                stage=None,
                token="recovery_required",
                evidence={**base, "install": _json_of(install)},
                waiting_since=snap.taken_at,
                decisions=(),
                headline_params={
                    "engine_dir": _attr(install, "engine_dir"),
                    "engine_version": _attr(install, "engine_version"),
                },
                reason="recovery_required",
                intent_uuid=uuid,
                dedupe_intent_key=f"{snap.space}~*",
            )
        ]

    # -- boundary timestamps ------------------------------------------------ #

    def _gate_opened_at(self, snap: IntentSnapshot, stage: str) -> str:
        """When this gate opened — the FR-ACT-004 sort key (C20).

        `created_at` would let a card minted by a restart jump ahead of a gate that has been waiting for
        an hour, so the boundary time is what `waiting_since` carries.
        """
        return self._latest_ts(snap, ("STAGE_AWAITING_APPROVAL",), stage) or snap.taken_at

    def _question_asked_at(self, snap: IntentSnapshot) -> str:
        """When this question group was written.

        Audit questions have their own decision timestamp. File-backed groups fall back to the file's
        mtime when no event names them; using `now` would reset their age on every scan (C20).
        """
        if snap.questions is not None and snap.questions.origin:
            return str(snap.questions.origin["timestamp"])
        asked = self._latest_ts(snap, C.QUESTION_EVENTS, snap.stage)
        if asked:
            return asked
        questions = snap.questions
        if questions is not None:
            for artifact in snap.stage_artifacts:
                if artifact.relpath == questions.relpath:
                    return artifact.mtime
        return self._latest_ts(snap, ("STAGE_STARTED",), snap.stage) or snap.taken_at

    @staticmethod
    def _latest_ts(snap: IntentSnapshot, types: Sequence[str], stage: str | None) -> str | None:
        """The newest matching event's timestamp, or ``None``.

        No cross-stage fallback. Asking for a stage and being handed another stage's event made a brand
        new question group inherit an OLD ``QUESTION_ANSWERED`` timestamp as its ``waiting_since``, so the
        queue sorted a decision nobody had seen yet as though it had been waiting for hours, and the card
        told the reader it had. ``None`` is the truthful answer, and each caller already has a sensible
        floor for it (``snap.taken_at``, or the questions file's own mtime).

        ``stage=None`` still means "any stage": that is ``summarize``'s repo-wide ``last_activity_at``,
        which genuinely wants the newest movement anywhere in the intent.
        """
        event = latest_event(list(snap.audit.events), types, stage)
        return event.timestamp if event is not None else None

    def _is_final_stage(self, snap: IntentSnapshot, slug: str) -> bool:
        """Whether approving this gate finishes the workflow (drives the `final_stage` consequence)."""
        if snap.state is None:
            return False
        remaining = [
            row.slug
            for row in snap.state.stages
            if (row.suffix or "").upper().startswith("EXECUTE")
            and row.state not in C.DONE_STAGE_STATES
            and row.slug != slug
        ]
        return not remaining

    # -- summaries ---------------------------------------------------------- #

    def summarize(
        self,
        snap: IntentSnapshot,
        *,
        binding: Any | None = None,
        live_actions: Sequence[Any] = (),
        breaker_open: bool = False,
        host: Any | None = None,
        findings: Sequence[Any] = (),
        slot: Any | None = None,
    ) -> IntentSummary:
        state = snap.state
        counts = dict(state.counts) if state is not None else self._empty_counts()
        return IntentSummary(
            repo_id=snap.repo_id,
            repo_label=snap.repo_label,
            space=snap.space,
            intent_dir=snap.intent_dir,
            intent_key=snap.intent_key,
            uuid=snap.row.uuid if snap.row is not None else None,
            slug=self._slug(snap),
            scope=self._scope(snap),
            registry_status=snap.row.status if snap.row is not None else None,
            title=self._title(snap),
            operational_state=self.operational_state(
                snap,
                binding=binding,
                live_actions=live_actions,
                breaker_open=breaker_open,
                host=host,
                findings=findings,
                slot=slot,
            ),
            disk=self._disk(snap),
            counts=counts,
            # `revision` cards are informational and excluded from the queue (C18), so they must not
            # inflate the badge the rail shows.
            open_actions=sum(1 for a in live_actions if str(_attr(a, "type", "")) != "revision"),
            blocking_findings=sum(1 for f in findings if str(_attr(f, "severity", "")) == "blocking"),
            warn_findings=sum(1 for f in findings if str(_attr(f, "severity", "")) == "warn"),
            session=host,
            archived=str(_attr(binding, "archive_state", "active")) == "archived",
            paused=bool(_attr(binding, "paused", False)),
            keep_moving=False,
            interrupted=bool(_attr(binding, "interrupted_at")),
            last_activity_at=self._latest_ts(snap, C.MOVEMENT_EVENTS, None),
            stable_boundary=_attr(binding, "last_stable_boundary"),
            unstable=not snap.stable,
            is_active_cursor=snap.is_active,
        )

    def detail(
        self,
        snap: IntentSnapshot,
        *,
        binding: Any | None = None,
        live_actions: Sequence[Any] = (),
        breaker_open: bool = False,
        host: Any | None = None,
        findings: Sequence[Any] = (),
        slot: Any | None = None,
        git: Any | None = None,
        actions: Sequence[Any] = (),
        host_card: dict | None = None,
    ) -> IntentDetail:
        summary = self.summarize(
            snap,
            binding=binding,
            live_actions=live_actions,
            breaker_open=breaker_open,
            host=host,
            findings=findings,
            slot=slot,
        )
        metas, truncated = self.artifacts(snap)
        directive = snap.directive
        return IntentDetail(
            **{field: getattr(summary, field) for field in summary.__slots__},
            state_sections={k: dict(v) for k, v in (snap.state.sections if snap.state else {}).items()},
            phases=tuple(snap.state.phases) if snap.state is not None else (),
            stages=tuple(snap.state.stages) if snap.state is not None else (),
            findings=tuple(findings),
            actions=tuple(actions),
            directive=directive,
            recovery=dict(snap.markers.recovery) if snap.markers.recovery else None,
            markers=MarkersView.of(snap.markers),
            audit_tail=tuple(AuditEventView.of(e) for e in snap.audit.events[-AUDIT_TAIL_LIMIT:]),
            questions=self.questions_view(snap, host_card=host_card),
            artifacts_count=len(metas),
            artifacts_truncated=truncated,
            git=git,
            binding=binding,
            engine=snap.engine,
            active_directive_stage=(
                directive.stage if directive is not None and directive.matches_state else None
            ),
        )

    @staticmethod
    def _empty_counts() -> dict[str, int]:
        counts = {"total": 0, "done": 0}
        for name in C.STAGE_STATE:
            counts[name] = 0
        return counts

    @staticmethod
    def _slug(snap: IntentSnapshot) -> str:
        """The registry row's slug, else the directory name minus its `YYMMDD-` prefix.

        The registry is authoritative (`04 §2.1`); the fallback exists for an orphan record, which is a
        `warn` finding, not a reason to show an empty name.
        """
        if snap.row is not None and snap.row.slug:
            return snap.row.slug
        head, _, tail = snap.intent_dir.partition("-")
        if tail and len(head) == _DATE_PREFIX_LEN and head.isdigit():
            return tail
        return snap.intent_dir

    @staticmethod
    def _scope(snap: IntentSnapshot) -> str | None:
        """The state file's current scope, falling back to the registry's birth-time scope.

        They legitimately differ: a `SCOPE_CHANGED` run rewrites the state but not the registry row
        (FORMAT-NOTES §2), and what the workflow is executing now is what the user needs to see.
        """
        if snap.state is not None:
            scope = (snap.state.project.get("Scope") or "").strip()
            if scope:
                return scope
        return snap.row.scope if snap.row is not None else None

    @staticmethod
    def _title(snap: IntentSnapshot) -> str | None:
        if snap.state is None:
            return None
        value = (snap.state.project.get("Project") or "").strip()
        if not value or value in PROJECT_PLACEHOLDERS:
            return None
        return value[:MAX_TITLE_CHARS]

    @staticmethod
    def _disk(snap: IntentSnapshot) -> DiskState:
        state = snap.state
        if state is None:
            return DiskState(None, None, None, None, None, None, None, 0, None, None)
        return DiskState(
            status=state.status,
            lifecycle_phase=state.lifecycle_phase,
            current_stage=state.current_stage,
            next_stage=state.next_stage,
            state_version=state.state_version,
            total_stages=state.total_stages_claimed,
            completed=state.completed_claimed,
            revision_count=state.revision_count,
            parked_at=state.parked_at,
            last_updated=(state.sections.get("Current Status", {}).get("Last Updated")
                          or state.project.get("Last Updated")
                          or None),
        )

    # -- artifacts ---------------------------------------------------------- #

    def artifacts(self, snap: IntentSnapshot) -> tuple[list[ArtifactMeta], bool]:
        """Files in the record and its declared shared outputs, plus whether the walk was cut short.

        The truncation flag is not cosmetic: a listing that hit the 2 000-file cap or the 10 s deadline
        is a partial record, and reporting it as complete would let the UI claim an artifact is missing.
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

    def map(self, snap: IntentSnapshot) -> MapModel:
        """The stage graph joined with the state file's rows, grouped into the five phases.

        Union, not intersection: a stage the graph knows but the state file does not list is `excluded`
        (the plan did not select it), and a row the graph does not know is still shown — that pair is
        exactly what `stage_graph_drift` warns about, and dropping either side would hide it.
        """
        rows = {row.slug: row for row in (snap.state.stages if snap.state is not None else ())}
        nodes = list(snap.graph)
        known = {node.slug for node in nodes}
        by_stage = self._artifacts_by_stage(snap)
        dependents = self._dependents(nodes)
        skip_reasons = self._skip_reasons(snap)
        completed = snap.state is not None and (snap.state.status or "").strip().lower() == "completed"
        # Keep the last stage on the snapshot for navigation; terminal maps have no execution cursor.
        current = None if completed else snap.stage or (snap.state.current_stage if snap.state is not None else None)
        directive_stage = (
            snap.directive.stage
            if not completed and snap.directive is not None and snap.directive.matches_state else None
        )

        stages: list[MapStage] = []
        for node in nodes:
            stages.append(
                self._map_stage(
                    snap, node.slug, node, rows.get(node.slug), by_stage, dependents, skip_reasons,
                    current, directive_stage,
                )
            )
        for slug, row in rows.items():
            if slug in known:
                continue
            stages.append(
                self._map_stage(
                    snap, slug, None, row, by_stage, dependents, skip_reasons, current, directive_stage
                )
            )

        phase_status = {
            name.strip().lower(): value.strip()
            for name, value in (snap.state.phases if snap.state is not None else ())
        }
        phases: list[MapPhase] = []
        for name in C.PHASES:
            members = tuple(stage for stage in stages if stage.phase == name)
            if not members:
                continue
            phases.append(
                MapPhase(
                    phase=name,
                    status=phase_status.get(name, "Unknown"),
                    stages=members,
                    counts={
                        "total": len(members),
                        "in_scope": sum(1 for s in members if s.in_scope),
                        "done": sum(1 for s in members if s.state in C.DONE_STAGE_STATES),
                        "skipped": sum(1 for s in members if s.state == "skipped" or s.skipped_reason),
                    },
                )
            )
        extra = tuple(stage for stage in stages if stage.phase not in C.PHASES)
        if extra:
            phases.append(
                MapPhase(
                    phase="unknown",
                    status="Unknown",
                    stages=extra,
                    counts={"total": len(extra), "in_scope": sum(1 for s in extra if s.in_scope),
                            "done": 0, "skipped": 0},
                )
            )

        return MapModel(
            intent_key=snap.intent_key,
            phases=tuple(phases),
            counts={
                "stages_known": len(stages),
                "stages_selected": sum(1 for stage in stages if stage.in_scope),
                "gates": sum(1 for stage in stages if stage.gate and stage.in_scope),
            },
            units=snap.units,
            graph_version=snap.engine.engine_version if snap.engine is not None else None,
            stage_count=len(nodes) or len(rows),
        )

    def _map_stage(
        self,
        snap: IntentSnapshot,
        slug: str,
        node: StageNode | None,
        row: StageRow | None,
        by_stage: Mapping[tuple[str, str | None], tuple[ArtifactMeta, ...]],
        dependents: Mapping[str, tuple[str, ...]],
        skip_reasons: Mapping[str, str | None],
        current: str | None,
        directive_stage: str | None,
    ) -> MapStage:
        phase = (node.phase if node is not None else None) or (row.phase if row is not None else None) or "unknown"
        suffix = (row.suffix or "").strip().upper() if row is not None else ""
        in_scope = suffix.startswith("EXECUTE") if row is not None else False
        if row is not None and node is not None and not suffix:
            # effectivePlanAction resolves explicit state suffixes before the current scope grid.
            # Without grid metadata, keep known legacy rows visible like PlanService's live-plan
            # default (except jump-skipped rows). This fallback never invents an absent state row.
            action = (snap.grid.get(self._scope(snap) or "", {}).get(slug) or "").strip().upper()
            in_scope = action == "EXECUTE" if action in {"EXECUTE", "SKIP"} else row.state != "skipped"
        state = row.state if row is not None else "excluded"
        if not in_scope and state == "not_started":
            state = "excluded"
        per_unit = node.per_unit if node is not None else slug in C.PER_UNIT_STAGES
        return MapStage(
            slug=slug,
            number=node.number if node is not None else "",
            name=node.name if node is not None else slug.replace("-", " ").title(),
            phase=phase,
            state=state,
            execution=node.execution if node is not None else "ALWAYS",
            in_scope=in_scope,
            mode=node.mode if node is not None else "",
            agent=node.lead_agent if node is not None else "",
            reviewer=node.reviewer if node is not None else None,
            review_class=node.review_class if node is not None else None,
            # Only selected, non-initialization stages contribute a gate to this plan.
            gate=in_scope and phase != "initialization",
            per_unit=per_unit,
            summary_confirmation=node.summary_confirmation if node is not None else None,
            consumes=tuple(c.artifact for c in node.consumes) if node is not None else (),
            produces=tuple(node.produces) if node is not None else (),
            depends_on=tuple(node.requires_stage) if node is not None else (),
            dependents=dependents.get(slug, ()),
            elapsed_secs=self._elapsed(snap, slug, state),
            artifacts=by_stage.get((slug, None), ()),
            skipped_reason=skip_reasons.get(slug),
            units=self._map_units(snap, slug, per_unit, state, by_stage),
            is_current=slug == current,
            is_directive=slug == directive_stage,
        )

    def _map_units(
        self,
        snap: IntentSnapshot,
        slug: str,
        per_unit: bool,
        stage_state: str,
        by_stage: Mapping[tuple[str, str | None], tuple[ArtifactMeta, ...]],
    ) -> tuple[MapUnit, ...]:
        """Per-unit sub-rows for a Construction stage.

        The state file has ONE row per stage and writes a literal `Per unit: [TBD]` line
        (FORMAT-NOTES §3), so there is no per-unit mark on disk. The state shown here is therefore
        presentational and derived from two observable facts — whether this unit is the state file's
        `Active Unit`, and whether the unit's stage directory holds any file — and never authorises a
        decision.
        """
        if not per_unit or not snap.units:
            return ()
        out: list[MapUnit] = []
        for unit in snap.units:
            artifacts = by_stage.get((slug, unit), ())
            if unit == snap.unit:
                state = stage_state
            elif artifacts:
                state = "completed"
            else:
                state = "not_started"
            out.append(MapUnit(unit=unit, state=state, artifacts=artifacts))
        return tuple(out)

    def _artifacts_by_stage(
        self, snap: IntentSnapshot
    ) -> dict[tuple[str, str | None], tuple[ArtifactMeta, ...]]:
        grouped: dict[tuple[str, str | None], list[ArtifactMeta]] = {}
        metas, _ = self.artifacts(snap)
        for meta in metas:
            if meta.stage is None:
                continue
            grouped.setdefault((meta.stage, meta.unit), []).append(meta)
        return {key: tuple(value) for key, value in grouped.items()}

    @staticmethod
    def _dependents(nodes: Sequence[StageNode]) -> dict[str, tuple[str, ...]]:
        out: dict[str, list[str]] = {}
        for node in nodes:
            for required in node.requires_stage:
                out.setdefault(required, []).append(node.slug)
        return {key: tuple(value) for key, value in out.items()}

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

    def _elapsed(self, snap: IntentSnapshot, slug: str, state: str) -> int | None:
        """Wall time of the latest attempt at this stage; `None` when it never started.

        Measured `STAGE_STARTED → STAGE_COMPLETED` of the *latest* attempt, so a revised stage reports
        the current pass rather than the whole history. A stage still in flight is measured to `now`.
        """
        events = list(snap.audit.events)
        started = latest_event(events, ("STAGE_STARTED",), slug)
        if started is None:
            return None
        begin = _epoch(started.timestamp)
        if begin is None:
            return None
        end_event = next(
            (
                event
                for event in events
                if event.event in ("STAGE_COMPLETED", "STAGE_SKIPPED")
                and event.fields.get("Stage") == slug
                and event.sort_key > started.sort_key
            ),
            None,
        )
        if end_event is not None:
            end = _epoch(end_event.timestamp)
            return max(0, int(end - begin)) if end is not None else None
        if state in ("in_progress", "awaiting_approval", "revising"):
            return max(0, int(self._clock.now() - begin))
        return None

    # -- sorting ------------------------------------------------------------ #

    @staticmethod
    def sort_actions(cards: Sequence[Any], organize: str) -> list[Any]:
        """FR-ACT-003/004 order; byte-for-byte the same comparator as `ui/src/lib/sort.ts`.

        Ties break on `waiting_since` (the boundary time, C20) and then on `action_id`, so two clients
        that fetched the same queue always render the same order. Label collation uses casefold rather
        than a locale collator: the server has no user locale, and §3.6 has the client re-sort with
        `Intl.Collator` after filtering.
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
            # `repo_id` breaks a label tie before the priority group does. The reader never sees an id,
            # but the GROUP KEY is the repo_id (see `group_key`), so two repositories the user named the
            # same thing — a clone and a worktree of one project — interleaved: `groups[]` came back with
            # the same key twice and the client's run-length split rendered one heading with the other
            # repository's cards under it. `ui/src/lib/sort.ts` breaks the tie on the same key.
            out.sort(
                key=lambda card: (
                    _repo_label(card), _repo_id_of(card), _group_of(card), _oldest_key(card)
                )
            )
        else:  # "type"
            out.sort(
                key=lambda card: (
                    _group_of(card),
                    str(_attr(card, "queue_type", "") or ""),
                    _severity_of(card),
                    _oldest_key(card),
                )
            )
        return out

    @staticmethod
    def group_key(card: Any, organize: str) -> str:
        """The bucket a card belongs to in the current organize mode (§3.5)."""
        if organize == "priority":
            return f"group.{_group_of(card)}"
        if organize == "repo":
            return str(_attr(_attr(card, "repo", {}), "repo_id", "") or "")
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
    """The card's repository id, which is also its group key when organizing by repository."""
    return str(_attr(_attr(card, "repo", {}), "repo_id", "") or "")
