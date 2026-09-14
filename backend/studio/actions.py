"""The decision lane: one durable record per human decision, and no way around it.

Everything else in Studio observes. This module is the only component that *acts* on a user's behalf,
so it is written as a sequence of refusals with a single narrow exit. Five properties are load-bearing;
each one exists because of a specific way a well-meaning implementation would corrupt somebody's
AI-DLC workflow.

1. **Two-phase submission.** The backend commits a durable ``Delivering`` record and every
   precondition; the *browser* sends the prompt through the host's public chat API; the browser reports
   back. Nothing here calls a host dispatch function — not ``_run_chat``, not ``spawn_guarded_turn``,
   not ``slot.append`` (architecture §3.1/§7.0, A03). Those signatures already differ between the two
   gateway generations Studio supports, and a decision that reached the model through a private symbol
   would silently stop reaching it after an upgrade — with the record saying it did.

2. **At-most-once, and never on the client's word.** Once a record is ``Delivering`` the decision may
   already be in the conductor's hands, so nothing replays it. ``NotDelivered`` is a *proof*, not a
   report: an authoritative 4xx (or a verified client preflight code) **and** the backend's own four
   checks — no matching row in the canonical slot or in any slot of the repository, the slot idle since
   ``delivering_at``, the on-disk presence baseline byte-identical, the same ``boot_id`` (C24). A
   gateway restart between the send and the ack therefore yields ``ReconciliationRequired`` and a human
   decision, never a silent second prompt.

3. **Compare-and-submit over the whole captured set.** ``state_hash``, ``boundary_token``,
   ``question_digest``, ``stage_attempt``, ``evidence_digest`` and ``is_active`` are compared against
   *both* what the UI captured and what the row recorded, with ``null`` differing from non-``null``
   (C30/P26.3). Approving a gate whose artifact was rewritten under the reader is exactly the
   authority Studio must not lend.

4. **The cursor switch runs before every dispatch.** The conductor resolves which record a turn
   belongs to from the repository-shared cursor at each step (05 §8), so "we asked for intent A" is not
   evidence; the read-back is (C22/P01). It is unconditional — a gate approve moves the cursor exactly
   like Run does — and the read-back's state digest must still equal what the human decided against.

5. **One lane per decision.** ``human_lane`` becomes a prompt, ``host_control`` (force stop only) uses
   the host's stop endpoint, ``studio_only`` never leaves Studio. ``MachineActionBroker`` refuses
   always: there is no unattended lane in v1 (S12 unproven), and it exists so the marker-movement
   breaker of FR-SES-010 has a home rather than being a comment.

Threading: async facade (§0.6). Every SQLite call, every snapshot and the cursor-switch child process
run in a worker thread; the host is touched only from the loop, read-only, through ``HostBridge``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from concurrent.futures import Executor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, NoReturn, Sequence

from . import constants as C
from . import security
from .aidlc_reader import (
    PresenceBaseline, audit_question_accepts_text, file_questions_support_forms, is_audit_text_label,
)
from .consistency import ACTION_STORED_FINDING_CODES, RepoFacts
from .engine import CursorReadback
from .errors import IllegalTransition, StaleGeneration, StudioError
from .leases import LeaseGrant
from .projection import (
    SEED_SEVERITY,
    SEVERITY_RANK,
    UNCERTAIN_STATUSES,
    dedupe_key,
    priority_group_for,
    queue_type_for,
    run_unavailable_reason,
)
from .repo_registry import RepoRecord
from .sessions import busy_reasons

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .activity import ActivityProjector
    from .events import EventLog
    from .engine import EngineRunner
    from .consistency import ConsistencyEngine
    from .aidlc_reader import AidlcReader, QuestionsFile
    from .leases import RepoScheduler
    from .projection import CardSeed, IntentSnapshot, Projection
    from .sessions import HostBridge, SessionBinder
    from .settings import SettingsService
    from .storage import Storage

logger = logging.getLogger("aidlc-studio.actions")

__all__ = [
    "ALLOWED_TRANSITIONS",
    "CLIENT_PREFLIGHT_CODES",
    "COMMAND_ACTION_TYPES",
    "Captured",
    "DECISIONS",
    "HOST_CONTROL_DECISIONS",
    "HUMAN_LANE_DECISIONS",
    "HostCall",
    "HumanActionBroker",
    "MachineActionBroker",
    "STUDIO_ONLY_DECISIONS",
    "ActionRecord",
    "PresenceBaseline",
    "SubmitReceipt",
    "WIRE_TEXT_TEMPLATES",
    "lane_for",
    "wire_text_for",
]


# --------------------------------------------------------------------------- #
# tables (§1.12) — all data, so the state machine and its test read one source
# --------------------------------------------------------------------------- #

#: PRD §11.1 plus ``Draft→Cancelled`` and ``Queued→Cancelled`` (C04). Derived from the tuples in
#: ``constants`` rather than restated: two copies of a transition table drift, and the drift would show
#: up as a status the reconciler can reach but the broker refuses (or worse, the other way round).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    status: frozenset(targets) for status, targets in C.ALLOWED_TRANSITIONS.items()
}

DECISIONS = C.DECISIONS
HUMAN_LANE_DECISIONS = C.HUMAN_LANE_DECISIONS
HOST_CONTROL_DECISIONS = C.HOST_CONTROL_DECISIONS
STUDIO_ONLY_DECISIONS = C.STUDIO_ONLY_DECISIONS

#: The only receipt codes a browser may mint for ``not_delivered`` (review R08). Both are *pre-send*
#: observations of the host's own slot listing, and both are re-verified here against ``HostBridge``
#: before they are believed — a client-minted code is a hint, never a proof.
CLIENT_PREFLIGHT_CODES = ("slot_missing", "slot_mismatch_preflight")

#: Types Studio creates because a human asked for a turn, as opposed to types it derived from a
#: boundary on disk. The distinction decides who may cancel a card and what ``retire_stale`` sweeps.
COMMAND_ACTION_TYPES: frozenset[str] = frozenset({"run", "resume", "prepare_commit", "force_stop"})
#: Command types that go through the two-phase prompt lane (``force_stop`` uses the stop endpoint).
PROMPT_COMMAND_TYPES: tuple[str, ...] = ("run", "resume", "prepare_commit")

#: Statuses a decision may still be submitted from: nothing has been handed to the host yet.
SUBMITTABLE_STATUSES: frozenset[str] = frozenset({"Queued", "NotDelivered"})
#: Statuses under which ``dedupe_key`` must be unique — the ``actions_live_dedupe`` partial index.
DEDUPE_LIVE_STATUSES: frozenset[str] = frozenset(C.DEDUPE_LIVE_ACTION_STATUS)
LIVE_STATUSES: frozenset[str] = frozenset(C.LIVE_ACTION_STATUS)
TERMINAL_STATUSES: frozenset[str] = frozenset(C.TERMINAL_ACTION_STATUS)

#: The compare-and-submit set (C30). Order is the order failures are reported in, so a mismatch reads
#: from the coarsest fact (the state file) to the finest (which intent the cursor named).
COMPARED_CAPTURED_FIELDS: tuple[str, ...] = (
    "state_hash",
    "boundary_token",
    "question_digest",
    "stage_attempt",
    "evidence_digest",
    "is_active",
)

#: ``decision -> wire text template``, keyed exactly like ``docs/design/wire-text.json`` ``decisions``
#: (a test pins the two equal). Assembled from the constants rather than typed out again: the pinned
#: file is the source, and a template that disagreed with what ``wire_text_for`` actually sends would
#: be a confirmation dialog that lies about the bytes.
WIRE_TEXT_TEMPLATES: dict[str, str | None] = {
    "approve": C.WIRE_APPROVE,
    "request_changes": f"{C.WIRE_REQUEST_CHANGES_PREFIX}<feedback>",
    "accept_as_is": C.WIRE_ACCEPT_AS_IS,
    "approve_plan": C.WIRE_APPROVE_PLAN,
    "request_plan_changes": f"{C.WIRE_REQUEST_CHANGES_PREFIX}<feedback>",
    "confirm_summary.looks_correct": C.WIRE_LOOKS_CORRECT,
    "confirm_summary.request_changes": f"{C.WIRE_SUMMARY_REQUEST_CHANGES_PREFIX}<feedback>",
    "answers.single": "<label>[, <label>...] | <free text when X. Other>",
    "answers.grouped": (
        "Q<n>: <answer>\\nQ<m>: <answer> + WIRE_GROUPED_ANSWER_SUFFIX (file-backed group; one audit receipt)"
    ),
    "provide_input.scope": f"{C.WIRE_SCOPE_PREFIX}<scope>",
    "provide_input.free_text": "<text>",
    "run": C.WIRE_RUN,
    "resume": C.WIRE_RESUME,
    "prepare_commit": C.WIRE_PREPARE_COMMIT,
    "force_stop": None,
    "pick_intent": None,
}

#: What the UI must collect before a decision can be sent (§2.11 ``DecisionSpec.requires``).
DECISION_REQUIRES: dict[str, tuple[str, ...]] = {
    "request_changes": ("feedback",),
    "request_plan_changes": ("feedback",),
    "provide_input": ("feedback",),
    "confirm_summary": ("confirm",),
    "answers": ("answers",),
    "resubmit": ("confirm",),
    "mark_not_delivered": ("confirm",),
}

#: Keys of ``EvidenceSnapshot.to_json()`` (§1.8). ``evidence_json`` carries these *plus* delivery and
#: card bookkeeping; ``card()`` splits them apart again so the wire shape of §2.11 stays exact.
EVIDENCE_SNAPSHOT_KEYS: tuple[str, ...] = (
    "state",
    "audit",
    "directive",
    "artifacts",
    "review",
    "acceptance_criteria",
    "findings",
    "session",
    "questions",
    "git",
    "markers",
)
#: The subset a live snapshot may refresh on a card that has not been dispatched. ``findings`` is
#: refreshed separately (action-stored codes must survive), and ``git`` needs an observer the broker is
#: not given. ``session`` is refreshed from a binding the broker re-reads itself: a card queued before
#: the intent had a conversation bound used to keep saying "nothing bound" for the rest of its life, so
#: the confirmation refused a send that ``submit`` — which resolves the binding live — would have
#: accepted, and clicking Run again returned the same card (one live command per boundary).
LIVE_REFRESHED_EVIDENCE_KEYS: tuple[str, ...] = (
    "state",
    "audit",
    "directive",
    "artifacts",
    "review",
    "acceptance_criteria",
    "session",
    "questions",
    "markers",
)

#: Where the seed's non-column facts live inside ``evidence_json``. They are the card's *presentation*
#: (severity, offered decisions, headline params) plus the two ``Captured`` fields the schema has no
#: column for; keeping them under one key means no bookkeeping key can ever collide with an
#: ``EvidenceSnapshot`` key, and ``card()`` strips exactly one key on the way out.
CARD_META_KEY = "card"

#: Activity kind per target status (§1.17 vocabulary).
_ACTIVITY_KIND_BY_STATUS: dict[str, str] = {
    "Delivering": "action.submitted",
    "Delivered": "action.delivery",
    "Processing": "action.delivery",
    "NotDelivered": "action.delivery",
    "DeliveryUncertain": "action.delivery",
    "ReconciliationRequired": "action.updated",
    "StateChanged": "action.resolved",
    "ResolvedNoTransition": "action.resolved",
    "Failed": "action.failed",
    "Cancelled": "action.cancelled",
    "Queued": "action.updated",
}
#: Severity per target status: the queue's own vocabulary, so the Activity page bands a lost delivery
#: the same way the Action Center does.
_ACTIVITY_SEVERITY_BY_STATUS: dict[str, str] = {
    "DeliveryUncertain": "critical",
    "ReconciliationRequired": "critical",
    "NotDelivered": "attention",
    "Failed": "attention",
}

#: Resolution ``kind`` per terminal status (§2.11 ``Resolution``).
_RESOLUTION_KIND: dict[str, str] = {
    "StateChanged": "state_changed",
    "ResolvedNoTransition": "no_transition",
    "Failed": "failed",
    "Cancelled": "cancelled",
}

_ACTIONS = "actions"
_TRANSITIONS = "action_transitions"
_BREAKERS = "breakers"
_DRAFTS = "advisor_drafts"
_REPOS = "repos"


def lane_for(decision: str) -> str:
    """Which lane a decision travels in. Unknown decisions are ``studio_only``: refusing inside Studio
    is the only safe default for a name that reaches no host and no engine."""
    if decision in HUMAN_LANE_DECISIONS:
        return "human_lane"
    if decision in HOST_CONTROL_DECISIONS:
        return "host_control"
    return "studio_only"


def _canonical_json(value: Any) -> str:
    """The one JSON spelling every digest in this module is taken over.

    Sorted keys, no whitespace: a payload digest or an acknowledged-evidence digest must not depend on
    dict insertion order, or the same decision would hash differently between two processes and every
    ``resubmit`` would look tampered with.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return security.sha256_text(_canonical_json(value))


def _json_of(obj: Any) -> Any:
    """``to_json()`` when the object has one, a dict when it is a mapping, else ``None``."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return dict(obj)
    encode = getattr(obj, "to_json", None)
    return encode() if callable(encode) else None


def _instant(value: object) -> float | None:
    """Seconds for any timestamp Studio or the host writes, or ``None``.

    Two spellings meet here: Studio's ``…Z`` second-precision stamps and the host's offset-aware
    microsecond stamps (``2026-09-04T10:00:00.500000+00:00``). Comparing them as strings puts the host
    row *before* a Studio stamp of the same second, which would read as "this turn started before we
    sent" — the exact inversion the delivery proof must not make.
    """
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    exact = C.epoch_from_iso(value)
    if exact is not None:
        return exact
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _at_or_after(candidate: object, since: object) -> bool:
    """``candidate >= since`` for timestamps, ``False`` when either cannot be read.

    Unreadable means "no evidence", and every caller here treats absent evidence as *not proven* —
    which is the direction that refuses a dispatch rather than authorising one.
    """
    left, right = _instant(candidate), _instant(since)
    if left is None or right is None:
        return False
    return left >= right


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Captured:
    """What the human's decision was captured against (§1.12, C30).

    Six compared fields plus two facts about the read itself. ``null`` is a value, not a gap: a gate
    captured while its stage had no questions file must go stale the moment one appears, and a
    per-type field list could never notice that.
    """

    state_hash: str | None
    boundary_token: str | None
    question_digest: str | None
    stage_attempt: int | None
    evidence_digest: str | None
    is_active: bool | None
    captured_at: str = ""
    stable: bool = True

    @classmethod
    def from_json(cls, data: Mapping[str, Any] | None) -> "Captured":
        raw = data or {}
        attempt = raw.get("stage_attempt")
        active = raw.get("is_active")
        return cls(
            state_hash=raw.get("state_hash") or None,
            boundary_token=raw.get("boundary_token") or None,
            question_digest=raw.get("question_digest") or None,
            stage_attempt=None if attempt is None else int(attempt),
            evidence_digest=raw.get("evidence_digest") or None,
            is_active=None if active is None else bool(active),
            captured_at=str(raw.get("captured_at") or ""),
            stable=bool(raw.get("stable", True)),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "state_hash": self.state_hash,
            "boundary_token": self.boundary_token,
            "question_digest": self.question_digest,
            "stage_attempt": self.stage_attempt,
            "evidence_digest": self.evidence_digest,
            "is_active": self.is_active,
            "captured_at": self.captured_at,
            "stable": self.stable,
        }

    def compared(self) -> dict[str, Any]:
        """Only the fields compare-and-submit looks at."""
        return {name: getattr(self, name) for name in COMPARED_CAPTURED_FIELDS}

    def differences(self, other: "Captured") -> list[str]:
        """Field names where the two sets disagree, ``null`` vs non-``null`` included."""
        mine, theirs = self.compared(), other.compared()
        return [name for name in COMPARED_CAPTURED_FIELDS if mine[name] != theirs[name]]


@dataclass(frozen=True, slots=True)
class HostCall:
    """The call the browser must make next. Data, because the backend must not make it itself."""

    method: str
    path: str
    body: dict

    def to_json(self) -> dict[str, Any]:
        return {"method": self.method, "path": self.path, "body": dict(self.body)}


@dataclass(frozen=True, slots=True)
class ActionRecord:
    """One ``actions`` row, typed.

    ``evidence`` is deliberately a plain dict: it carries the ``EvidenceSnapshot`` the card was
    derived from *and* the delivery bookkeeping the reconciler appends (``receipt``, ``queued_at``,
    ``delivery_confirmed``, the four ``NotDelivered`` checks, stored findings). Two modules write it
    from opposite ends, so a typed shape would have to be edited in lockstep by both; ``card()`` is
    where it is split into the exact §2.11 sub-objects.
    """

    action_id: str
    type: str
    risk_class: str
    repo_id: str
    resolved_repo_identity: str | None
    intent_uuid: str | None
    intent_dir: str
    space: str
    intent_key: str
    stage: str | None
    unit: str | None
    captured: Captured
    payload: dict | None
    payload_digest: str | None
    human_text: str | None
    wire_text: str | None
    status: str
    status_generation: int
    session_key: str | None
    slot_key: str | None
    delivery_id: str | None
    lease_generation: int | None
    created_at: str
    updated_at: str
    delivering_at: str | None
    delivered_at: str | None
    resolved_at: str | None
    deadline_at: str | None
    evidence: dict
    resolution: dict | None
    cursor_readback: CursorReadback | None
    presence_baseline: PresenceBaseline | None
    boot_id: str | None
    retry_fingerprint: str | None
    source: str
    waiting_since: str
    dedupe_key: str | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "ActionRecord":
        evidence = row.get("evidence_json")
        evidence = dict(evidence) if isinstance(evidence, Mapping) else {}
        meta = evidence.get(CARD_META_KEY)
        meta = meta if isinstance(meta, Mapping) else {}
        space = str(row.get("space") or C.DEFAULT_SPACE)
        intent_dir = str(row.get("intent_dir") or "")
        payload = row.get("payload_json")
        resolution = row.get("resolution_json")
        return cls(
            action_id=str(row.get("action_id") or ""),
            type=str(row.get("type") or ""),
            risk_class=str(row.get("risk_class") or "studio_only"),
            repo_id=str(row.get("repo_id") or ""),
            resolved_repo_identity=row.get("resolved_repo_identity") or None,
            intent_uuid=row.get("intent_uuid") or None,
            intent_dir=intent_dir,
            space=space,
            intent_key=C.intent_key_for(space, intent_dir),
            stage=row.get("stage") or None,
            unit=row.get("unit") or None,
            captured=Captured.from_json(
                {
                    "state_hash": row.get("captured_state_hash"),
                    "boundary_token": row.get("boundary_token"),
                    "question_digest": row.get("question_digest"),
                    "stage_attempt": row.get("stage_attempt"),
                    "evidence_digest": row.get("evidence_digest"),
                    "is_active": row.get("is_active_captured"),
                    "captured_at": meta.get("captured_at") or row.get("created_at"),
                    "stable": meta.get("stable", True),
                }
            ),
            payload=dict(payload) if isinstance(payload, Mapping) else None,
            payload_digest=row.get("payload_digest") or None,
            human_text=row.get("human_text") or None,
            wire_text=row.get("wire_text") or None,
            status=str(row.get("status") or "Draft"),
            status_generation=int(row.get("status_generation") or 0),
            session_key=row.get("session_key") or None,
            slot_key=row.get("slot_key") or None,
            delivery_id=row.get("delivery_id") or None,
            lease_generation=(
                None if row.get("lease_generation") is None else int(row["lease_generation"])
            ),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
            delivering_at=row.get("delivering_at") or None,
            delivered_at=row.get("delivered_at") or None,
            resolved_at=row.get("resolved_at") or None,
            deadline_at=row.get("deadline_at") or None,
            evidence=evidence,
            resolution=dict(resolution) if isinstance(resolution, Mapping) else None,
            cursor_readback=_decode_readback(row.get("cursor_readback_json")),
            presence_baseline=_decode_presence(row.get("presence_baseline_json")),
            boot_id=row.get("boot_id") or None,
            retry_fingerprint=row.get("retry_fingerprint") or None,
            source=str(row.get("source") or "studio"),
            waiting_since=str(row.get("waiting_since") or row.get("created_at") or ""),
            dedupe_key=row.get("dedupe_key") or None,
        )

    @property
    def card_meta(self) -> dict[str, Any]:
        meta = self.evidence.get(CARD_META_KEY)
        return dict(meta) if isinstance(meta, Mapping) else {}

    @property
    def queue_type(self) -> str:
        return queue_type_for(self.type, self.status)

    @property
    def lane(self) -> str | None:
        """The lane this row's delivery used, or ``None`` when nothing was ever dispatched."""
        if self.delivery_id is None:
            return None
        return "host_control" if self.type == "force_stop" else "human_lane"

    def to_json(self) -> dict[str, Any]:
        """The row as data. The UI is served ``card()``; this is for diagnostics and tests."""
        return {
            "action_id": self.action_id,
            "type": self.type,
            "risk_class": self.risk_class,
            "repo_id": self.repo_id,
            "resolved_repo_identity": self.resolved_repo_identity,
            "intent_uuid": self.intent_uuid,
            "intent_dir": self.intent_dir,
            "space": self.space,
            "intent_key": self.intent_key,
            "stage": self.stage,
            "unit": self.unit,
            "captured": self.captured.to_json(),
            "payload": dict(self.payload) if self.payload else None,
            "payload_digest": self.payload_digest,
            "wire_text": self.wire_text,
            "status": self.status,
            "status_generation": self.status_generation,
            "session_key": self.session_key,
            "slot_key": self.slot_key,
            "delivery_id": self.delivery_id,
            "lease_generation": self.lease_generation,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "delivering_at": self.delivering_at,
            "delivered_at": self.delivered_at,
            "resolved_at": self.resolved_at,
            "deadline_at": self.deadline_at,
            "evidence": dict(self.evidence),
            "resolution": dict(self.resolution) if self.resolution else None,
            "cursor_readback": _json_of(self.cursor_readback),
            "presence_baseline": _json_of(self.presence_baseline),
            "boot_id": self.boot_id,
            "retry_fingerprint": self.retry_fingerprint,
            "source": self.source,
            "waiting_since": self.waiting_since,
            "dedupe_key": self.dedupe_key,
            "human_text_present": bool(self.human_text),
        }


@dataclass(frozen=True, slots=True)
class SubmitReceipt:
    """What the backend committed, and the one call the browser must now make."""

    action_id: str
    status: str
    lane: str
    delivery_id: str
    slot_key: str
    session_key: str
    wire_text: str | None
    expires_at: str
    lease_generation: int | None
    host: HostCall

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": True,
            "action_id": self.action_id,
            "status": self.status,
            "lane": self.lane,
            "delivery_id": self.delivery_id,
            "slot_key": self.slot_key,
            "session_key": self.session_key,
            "wire_text": self.wire_text,
            "expires_at": self.expires_at,
            "lease_generation": self.lease_generation,
            "host": self.host.to_json(),
        }


def _decode_readback(raw: object) -> CursorReadback | None:
    """Rebuild the engine's ``CursorReadback`` from its stored JSON.

    Rebuilt as the dataclass rather than left as a dict because ``ConsistencyEngine`` reads
    ``readback.ok`` to raise ``cursor_mismatch``; a dict would answer ``None`` there, which reads as
    "no read-back" and drops a blocking finding.
    """
    if isinstance(raw, CursorReadback):
        return raw
    if not isinstance(raw, Mapping):
        return None
    return CursorReadback(
        ok=bool(raw.get("ok")),
        space=str(raw.get("space") or ""),
        dir_name=str(raw.get("dir_name") or ""),
        uuid=raw.get("uuid") or None,
        state_sha256=raw.get("state_sha256") or None,
        mismatch=tuple(str(m) for m in (raw.get("mismatch") or ())),
    )


def _decode_presence(raw: object) -> PresenceBaseline | None:
    if isinstance(raw, PresenceBaseline):
        return raw
    if not isinstance(raw, Mapping):
        return None
    sizes = raw.get("shard_sizes")
    return PresenceBaseline(
        human_turn_events=int(raw.get("human_turn_events") or 0),
        human_turn_events_partial=bool(raw.get("human_turn_events_partial")),
        human_turn_mtime_ns=(
            None if raw.get("human_turn_mtime_ns") is None else int(raw["human_turn_mtime_ns"])
        ),
        turn_counter=None if raw.get("turn_counter") is None else int(raw["turn_counter"]),
        shard_sizes={str(k): int(v) for k, v in (sizes or {}).items()} if isinstance(sizes, Mapping) else {},
        audit_tail_sha256=str(raw.get("audit_tail_sha256") or ""),
        state_sha256=raw.get("state_sha256") or None,
        taken_at=str(raw.get("taken_at") or ""),
    )


def _presence_comparable(baseline: PresenceBaseline | None) -> dict[str, Any] | None:
    """A presence baseline with its own capture time removed.

    ``taken_at`` is the clock reading of the moment the baseline was taken, so two baselines of an
    unchanged record differ in exactly that field. Comparing it would make the disk half of the
    delivery proof impossible to satisfy — every ``not_delivered`` would become
    ``not_delivered_unproven`` and every genuinely lost decision would need a human.
    """
    payload = _json_of(baseline)
    if payload is None:
        return None
    payload.pop("taken_at", None)
    return payload


# --------------------------------------------------------------------------- #
# wire text (FR-GATE-002) — the exact bytes a decision sends
# --------------------------------------------------------------------------- #


def wire_text_for(
    decision: str,
    payload: Mapping[str, Any],
    *,
    questions: "QuestionsFile | None",
    grouped_answers_enabled: bool,
) -> str | None:
    """The bytes this decision sends into the AI-DLC conversation, or ``None`` for a non-prompt lane.

    Pure and total: the same payload always produces the same string, and anything it cannot produce
    is a ``StudioError`` rather than a best guess. That is what lets ``submit`` compare the client's
    ``client_wire_text`` byte-for-byte — the client displays what will be sent, and if the two differ
    the send is refused, so no UI (or Slack message, or replayed request) can choose the text that
    lands in someone's audit trail.
    """
    if decision in STUDIO_ONLY_DECISIONS or decision in HOST_CONTROL_DECISIONS:
        return None
    if decision == "approve":
        return C.WIRE_APPROVE
    if decision == "accept_as_is":
        return C.WIRE_ACCEPT_AS_IS
    if decision == "request_changes":
        return C.WIRE_REQUEST_CHANGES_PREFIX + _feedback(payload, "feedback")
    if decision == "request_plan_changes":
        return C.WIRE_REQUEST_CHANGES_PREFIX + _feedback(payload, "feedback")
    if decision == "approve_plan":
        return C.WIRE_APPROVE_PLAN
    if decision == "confirm_summary":
        choice = str(payload.get("choice") or "")
        if choice == "looks_correct":
            return C.WIRE_LOOKS_CORRECT
        if choice == "request_changes":
            return C.WIRE_SUMMARY_REQUEST_CHANGES_PREFIX + _feedback(payload, "feedback")
        raise StudioError(
            "invalid_decision",
            "confirm_summary needs choice 'looks_correct' or 'request_changes'",
            details={"decision": decision, "choice": choice},
        )
    if decision == "provide_input":
        kind = str(payload.get("kind") or "")
        if kind == "scope":
            return C.WIRE_SCOPE_PREFIX + _feedback(payload, "scope")
        if kind == "free_text":
            return _feedback(payload, "text")
        raise StudioError(
            "invalid_decision",
            "provide_input needs kind 'scope' or 'free_text'",
            details={"decision": decision, "kind": kind},
        )
    if decision == "run":
        return C.WIRE_RUN
    if decision == "resume":
        return C.WIRE_RESUME
    if decision == "prepare_commit":
        return C.WIRE_PREPARE_COMMIT
    if decision == "answers":
        return _answers_wire_text(payload, questions, grouped_answers_enabled)
    raise StudioError("invalid_decision", f"unknown decision {decision!r}", details={"decision": decision})


def _feedback(payload: Mapping[str, Any], key: str) -> str:
    """A required free-text field, stripped and bounded.

    Blank is ``feedback_required`` rather than an empty prompt: ``Request Changes: `` with nothing
    after it is a turn the conductor cannot act on, and it would still count as the human's one reply
    to that gate.
    """
    raw = payload.get(key)
    text = str(raw).strip() if raw is not None else ""
    if not text:
        raise StudioError(
            "feedback_required",
            f"{key} must not be blank",
            details={"field": key},
        )
    if len(text) > C.MAX_FEEDBACK_CHARS:
        raise StudioError(
            "too_large",
            f"{key} is longer than {C.MAX_FEEDBACK_CHARS} characters",
            details={"field": key, "length": len(text), "cap": C.MAX_FEEDBACK_CHARS},
        )
    return text


def _answers_wire_text(
    payload: Mapping[str, Any], questions: "QuestionsFile | None", grouped_enabled: bool
) -> str:
    """Encode a question group's answers (C14, review P18).

    Option **labels**, never letters: the conductor records what the human "said", and a bare ``B``
    would be recorded as the answer to a question whose options may be re-lettered on the next
    revision. One pending question sends the answer alone — that is the mapping research 05 §5.1
    verified. More than one uses the verified file-backed ``Q<n>:`` form. Native blocking question
    widgets are a separate transport and must be answered in their canonical conversation.
    """
    if questions is None:
        raise StudioError(
            "answers_incomplete", "there is no questions file to answer", details={"reason": "no_questions"}
        )
    pending = [q for q in questions.questions if not q.answered]
    if not pending:
        raise StudioError(
            "answers_incomplete", "no question is pending", details={"reason": "nothing_pending"}
        )
    raw = payload.get("answers")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise StudioError(
            "answers_incomplete", "answers must be a list", details={"reason": "not_a_list"}
        )
    supplied: dict[int, Mapping[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping) or type(item.get("index")) is not int:
            raise StudioError("invalid_decision", "each answer needs an integer question index")
        index = item["index"]
        if index in supplied:
            raise StudioError("invalid_decision", "a question was answered more than once",
                              details={"index": index})
        supplied[index] = item

    pending_indexes = {q.index for q in pending}
    if len(pending_indexes) != len(pending) or any(i < 1 for i in pending_indexes):
        raise StudioError("invalid_decision", "the question indexes are ambiguous")
    extra = sorted(set(supplied) - pending_indexes)
    if extra:
        raise StudioError("invalid_decision", "answers include questions that are not pending",
                          details={"indexes": extra})

    missing = [q.index for q in pending if q.index not in supplied]
    if missing:
        raise StudioError(
            "answers_incomplete",
            "every pending question must be answered in one submit",
            details={"missing": missing},
        )

    texts: list[tuple[int, str]] = []
    for question in pending:
        answer = supplied[question.index]
        raw_letters = answer.get("option_letters") or []
        if not isinstance(raw_letters, (list, tuple)) or any(not isinstance(v, str) for v in raw_letters):
            raise StudioError("invalid_decision", "option_letters must be a list of labels")
        letters = list(raw_letters)
        known = {option.letter: option for option in question.options}
        if len(known) != len(question.options) or len(set(letters)) != len(letters):
            raise StudioError("invalid_decision", "option letters must be unique",
                              details={"index": question.index})
        unknown = [letter for letter in letters if letter not in known]
        if unknown:
            raise StudioError(
                "invalid_decision",
                "an answer names an option this question does not offer",
                details={"index": question.index, "unknown": unknown, "options": sorted(known)},
            )
        if len(letters) > 1 and not question.multi_select:
            raise StudioError(
                "invalid_decision",
                "this question accepts one option",
                details={"index": question.index, "option_letters": letters},
            )
        free_text = str(answer.get("free_text") or "").strip()
        text_input = audit_question_accepts_text(questions)
        if questions.origin is not None and not text_input and (len(letters) != 1 or free_text):
            raise StudioError(
                "invalid_decision",
                "an audit question requires one of its exact recorded labels",
                details={"index": question.index},
            )
        chosen_other = any(known[letter].is_other for letter in letters)
        if chosen_other or not letters:
            # `X. Other` and an option-less (free-form) question both send the human's own words.
            if not free_text or (text_input and is_audit_text_label(free_text)):
                raise StudioError(
                    "answers_incomplete",
                    "this answer needs free text",
                    details={"index": question.index, "reason": "free_text_required"},
                )
            text = C.WIRE_MULTI_SELECT_JOINER.join(
                free_text if known[letter].is_other else known[letter].text for letter in letters
            ) if letters else free_text
        else:
            text = C.WIRE_MULTI_SELECT_JOINER.join(known[letter].text for letter in letters)
        if len(text) > C.MAX_ANSWER_CHARS:
            raise StudioError(
                "too_large",
                f"answer {question.index} is longer than {C.MAX_ANSWER_CHARS} characters",
                details={"index": question.index, "cap": C.MAX_ANSWER_CHARS},
            )
        texts.append((question.index, text))

    if len(texts) == 1:
        return texts[0][1]
    if not grouped_enabled:
        raise StudioError(
            "grouped_answers_unavailable",
            "answering several questions in one prompt is not verified yet",
            details={"pending": [index for index, _ in texts], "reason": "s1_s2_unverified"},
        )
    return C.WIRE_ANSWER_JOINER.join(
        C.WIRE_ANSWER_LINE.format(index=index, answer=text) for index, text in texts
    ) + C.WIRE_GROUPED_ANSWER_SUFFIX


def _human_prose(payload: Mapping[str, Any]) -> str | None:
    """The human's own words inside a decision payload, or ``None`` when it carries none.

    Only prose is kept as ``human_text`` (and therefore only prose is subject to retention): storing
    the whole wire text would put ``Approve`` in a retained column and pretend it was personal data,
    while a card whose ``human_text_present`` is true really does hold something the user wrote.
    """
    for key in ("feedback", "text", "scope"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    answers = payload.get("answers")
    if isinstance(answers, Sequence) and not isinstance(answers, (str, bytes)):
        parts = [
            str(item.get("free_text")).strip()
            for item in answers
            if isinstance(item, Mapping) and str(item.get("free_text") or "").strip()
        ]
        if parts:
            return "\n".join(parts)
    return None


# --------------------------------------------------------------------------- #
# the human lane
# --------------------------------------------------------------------------- #


class HumanActionBroker:
    """Owns ``actions``, ``action_transitions`` and ``breakers``. Async; loop thread."""

    def __init__(
        self,
        storage: "Storage",
        projection: "Projection",
        reader: "AidlcReader",
        consistency: "ConsistencyEngine",
        scheduler: "RepoScheduler",
        sessions: "SessionBinder",
        engine: "EngineRunner",
        host: "HostBridge",
        events: "EventLog",
        activity: "ActivityProjector",
        notifications: Any,
        settings: "SettingsService",
        clock: C.Clock,
        ids: C.IdFactory,
        boot_id: str,
        *,
        scan_pool: Executor | None = None,
    ) -> None:
        self._storage = storage
        self._projection = projection
        self._reader = reader
        self._consistency = consistency
        self._scheduler = scheduler
        self._sessions = sessions
        self._engine = engine
        self._host = host
        self._events = events
        self._activity = activity
        self._notifications = notifications
        self._settings = settings
        self._clock = clock
        self._ids = ids
        self._boot_id = boot_id
        self._scan_pool = scan_pool
        #: Late-bound by ``Services.build`` because the reconciler is constructed *from* the services
        #: object that already holds this broker. Only ``resolve("reconcile")`` needs it, and it says
        #: so out loud rather than silently doing nothing.
        self.reconciler: Any = None

    # ---- reads ------------------------------------------------------------ #

    async def get(self, action_id: str) -> ActionRecord:
        row = await asyncio.to_thread(self._storage.get, _ACTIONS, action_id)
        if row is None:
            raise StudioError("action_not_found", "no such action", details={"action_id": action_id})
        return ActionRecord.from_row(row)

    async def list_live(
        self,
        *,
        repo_id: str | None = None,
        space: str | None = None,
        intent_dir: str | None = None,
        include_revision: bool = False,
    ) -> list[ActionRecord]:
        """Every queue-visible row, in queue order (FR-ACT-003/004).

        ``revision`` cards are excluded unless asked for: ``[R]`` is informational and must not compete
        with a decision the user actually has to make (C18).
        """
        where: dict[str, Any] = {"status": sorted(LIVE_STATUSES)}
        if repo_id:
            where["repo_id"] = repo_id
        if space:
            where["space"] = space
        if intent_dir:
            where["intent_dir"] = intent_dir
        rows = await asyncio.to_thread(self._storage.select, _ACTIONS, where)
        recs = [ActionRecord.from_row(row) for row in rows]
        if not include_revision:
            recs = [rec for rec in recs if rec.type != "revision"]
        return sorted(recs, key=self._queue_key)

    async def list_for_intent(
        self,
        repo_id: str,
        space: str,
        intent_dir: str,
        *,
        include_terminal: bool = False,
        limit: int = 50,
    ) -> list[ActionRecord]:
        rows = await asyncio.to_thread(
            self._storage.select,
            _ACTIONS,
            {"repo_id": repo_id, "space": space, "intent_dir": intent_dir},
            order_by="created_at DESC, action_id DESC",
            limit=None if include_terminal else max(1, int(limit)) * 4,
        )
        recs = [ActionRecord.from_row(row) for row in rows]
        if not include_terminal:
            recs = [rec for rec in recs if rec.status in LIVE_STATUSES]
        return recs[: max(1, int(limit))]

    @staticmethod
    def _queue_key(rec: ActionRecord) -> tuple[int, int, str, str]:
        queue_type = rec.queue_type
        severity = str(rec.card_meta.get("severity") or SEED_SEVERITY.get(queue_type, "info"))
        return (
            priority_group_for(queue_type),
            SEVERITY_RANK.get(severity, 3),
            rec.waiting_since or rec.created_at,
            rec.action_id,
        )

    # ---- derivation (the reconciler's half of the queue) ------------------- #

    async def upsert_derived(self, seeds: "Sequence[CardSeed]") -> list[ActionRecord]:
        """Insert or refresh one card per boundary the last scan saw.

        The inversion that makes the queue trustworthy: a card is a *projection* of a boundary that is
        on disk right now. A live row for the same ``dedupe_key`` is refreshed only while nothing has
        been dispatched for it (``Queued``/``NotDelivered``); from ``Delivering`` on, the row is the
        record of what a human decided and disk moving underneath it must not rewrite it. When only
        terminal rows hold the key, a new ``Queued`` row is inserted — the partial index permits it —
        so a dismissed or failed card can never hide a gate that is still open (FR-ACT-007, C33/R12).
        """
        if not seeds:
            return []
        scopes: list[tuple[str, str, str]] = []
        for seed in seeds:
            scope = (seed.repo_id, seed.space, seed.intent_dir)
            if scope not in scopes:
                scopes.append(scope)
            await self._upsert_seed(seed)
        out: list[ActionRecord] = []
        seen: set[str] = set()
        for repo_id, space, intent_dir in scopes:
            for rec in await self.list_live(
                repo_id=repo_id, space=space, intent_dir=intent_dir, include_revision=True
            ):
                if rec.action_id not in seen:
                    seen.add(rec.action_id)
                    out.append(rec)
        return sorted(out, key=self._queue_key)

    async def _upsert_seed(self, seed: "CardSeed") -> None:
        live = await self._live_for_key(seed.dedupe_key)
        if live is not None:
            if live.status in SUBMITTABLE_STATUSES:
                await self._refresh_card(live, seed)
            return
        superseded = await self._newest_for_key(seed.dedupe_key)
        evidence = dict(seed.evidence)
        evidence[CARD_META_KEY] = _seed_meta(seed)
        if superseded is not None:
            evidence["superseded_action_id"] = superseded.action_id
        try:
            await self._insert(
                action_type=seed.type,
                risk_class=seed.risk_class,
                repo_id=seed.repo_id,
                space=seed.space,
                intent_dir=seed.intent_dir,
                intent_uuid=seed.intent_uuid,
                stage=seed.stage,
                unit=seed.unit,
                captured=seed.captured,
                evidence=evidence,
                dedupe_key=seed.dedupe_key,
                waiting_since=seed.waiting_since,
                source=seed.source,
                status="Queued",
            )
        except sqlite3.IntegrityError:
            # Another tick (or another gateway) inserted the same boundary between the read and the
            # write. The index did its job; the card exists, so there is nothing to repair.
            logger.debug("aidlc-studio: card for %s already exists", seed.dedupe_key)

    async def _refresh_card(self, rec: ActionRecord, seed: "CardSeed") -> None:
        """Bring an undispatched card's evidence up to date, writing only when something changed.

        The reconciler calls this for every card on every tick. With ``synchronous=FULL`` a blind write
        would be one fsync per card per five seconds, and — worse — every write bumps
        ``status_generation``, which would make a browser tab that is holding a generation lose a race
        it never entered.
        """
        meta = _seed_meta(seed)
        meta["captured_at"] = seed.captured.get("captured_at") or rec.captured.captured_at
        meta["stable"] = bool(seed.captured.get("stable", True))
        evidence = dict(rec.evidence)
        for key in EVIDENCE_SNAPSHOT_KEYS:
            if key in seed.evidence:
                evidence[key] = seed.evidence[key]
        evidence["findings"] = _merge_findings(rec.evidence.get("findings"), seed.evidence.get("findings"))
        for key, value in seed.evidence.items():
            if key not in EVIDENCE_SNAPSHOT_KEYS and key != CARD_META_KEY:
                evidence[key] = value
        evidence[CARD_META_KEY] = meta

        values: dict[str, Any] = {
            "captured_state_hash": seed.captured.get("state_hash"),
            "boundary_token": seed.captured.get("boundary_token"),
            "question_digest": seed.captured.get("question_digest"),
            "stage_attempt": seed.captured.get("stage_attempt"),
            "evidence_digest": seed.captured.get("evidence_digest"),
            "is_active_captured": _bool_int(seed.captured.get("is_active")),
            "intent_uuid": seed.intent_uuid,
            "waiting_since": seed.waiting_since,
            "evidence_json": evidence,
        }
        current = {
            "captured_state_hash": rec.captured.state_hash,
            "boundary_token": rec.captured.boundary_token,
            "question_digest": rec.captured.question_digest,
            "stage_attempt": rec.captured.stage_attempt,
            "evidence_digest": rec.captured.evidence_digest,
            "is_active_captured": _bool_int(rec.captured.is_active),
            "intent_uuid": rec.intent_uuid,
            "waiting_since": rec.waiting_since,
            "evidence_json": rec.evidence,
        }
        if all(current[key] == values[key] for key in values):
            return
        try:
            await asyncio.to_thread(
                self._storage.cas_update, _ACTIONS, rec.action_id, rec.status_generation, values
            )
        except StaleGeneration:
            # Somebody moved the row (a submit, a reconcile) while the scan was assembling seeds. The
            # next tick re-derives from a snapshot that has seen that move; refreshing now would
            # overwrite a decision's own captured set with a stale read.
            logger.debug("aidlc-studio: card %s moved during refresh", rec.action_id)

    async def retire_stale(
        self,
        repo_id: str,
        space: str,
        intent_dir: str,
        live_keys: set[str],
        *,
        reason: str = "boundary_superseded",
    ) -> None:
        """Retire derived cards whose boundary is no longer on disk.

        ``Failed`` is terminal (C04/P07): it is marked ``evidence.superseded`` instead of transitioned,
        so the failure stays readable and leaves the queue by its own condition. Command cards
        have no derived key to compare. The reconciler separately checks never-sent Run/Resume
        requests against current checkpoints and stage scope; other commands are left to the user.

        ``reason`` is the word the resolution and the transition row carry. The scan passes the default
        for a boundary that moved on; the reconciler's vanished-intent sweep passes ``intent_missing``
        with an empty ``live_keys`` — the whole intent directory left the disk, so every derived card
        of it is a card for nothing (§1.13 step 1b).
        """
        rows = await self.list_live(
            repo_id=repo_id, space=space, intent_dir=intent_dir, include_revision=True
        )
        for rec in rows:
            if rec.type in COMMAND_ACTION_TYPES or not rec.dedupe_key:
                continue
            if rec.dedupe_key in live_keys:
                continue
            if rec.status in SUBMITTABLE_STATUSES:
                await self._transition(
                    rec, "Cancelled", reason=reason, evidence={"superseded": True}
                )
            elif rec.status == "Failed" and not rec.evidence.get("superseded"):
                await self._patch_evidence(rec, {"superseded": True})

    # ---- command actions -------------------------------------------------- #

    async def create_command(
        self,
        type: str,
        repo: RepoRecord,
        space: str,
        intent_dir: str,
        *,
        snap: "IntentSnapshot",
        source: str = "studio",
    ) -> ActionRecord:
        """Create the ``run``/``resume``/``prepare_commit`` card a human asked for.

        Idempotent per boundary: a second click while the first card is still live returns that card
        rather than queueing a second turn for the same state (one live command per boundary).
        """
        if type not in PROMPT_COMMAND_TYPES:
            raise ValueError(f"{type!r} is not a prompt command type; force_stop uses create_force_stop")
        binding = await self._sessions.get(repo.repo_id, space, intent_dir)
        self._assert_dispatchable(binding, decision=type, intent_key=snap.intent_key, repo_id=repo.repo_id)
        if await self._breaker_open(repo.repo_id, snap.intent_key) and type in ("run", "resume"):
            raise StudioError(
                "breaker_open",
                "this intent's circuit breaker is open; review the failure first",
                details={"intent_key": snap.intent_key},
            )
        findings = await self._findings(repo, snap, binding)
        blocking = self._consistency.blocking(findings)
        self._raise_blocking_findings(blocking, repo.repo_id)
        if not snap.stable:
            raise StudioError(
                "unstable_read",
                "the record changed while it was being read",
                details={"intent_key": snap.intent_key},
            )
        if type in ("run", "resume"):
            await self._assert_run_current(type, snap)
            slot = self._host.slot(binding.slot_key) if binding.slot_key else None
            if slot is not None and busy_reasons(slot):
                raise StudioError("slot_busy", "the canonical conversation is already busy",
                                  details={"slot_key": binding.slot_key, "reasons": list(busy_reasons(slot))})
        captured = self._projection.captured(snap, snap.stage)
        key = dedupe_key(type, repo.repo_id, snap.intent_key, snap.stage, captured["boundary_token"])
        live = await self._live_for_key(key)
        if live is not None:
            return live
        evidence = self._projection.evidence(snap, findings=findings, binding=binding).to_json()
        evidence[CARD_META_KEY] = {
            "severity": "info",
            "decisions": list(DECISIONS[type]),
            "headline_params": {"stage": snap.stage},
            "consequence_variant": "one_turn" if type == "run" else None,
            "stage_ref": self._projection.stage_ref(snap, snap.stage),
            "reason": None,
            "related_action_id": None,
            "captured_at": captured["captured_at"],
            "stable": bool(captured["stable"]),
        }
        return await self._insert(
            action_type=type,
            risk_class="human_lane",
            repo_id=repo.repo_id,
            space=space,
            intent_dir=intent_dir,
            intent_uuid=snap.row.uuid if snap.row is not None else binding.intent_uuid,
            stage=snap.stage,
            unit=snap.unit,
            captured=captured,
            evidence=evidence,
            dedupe_key=key,
            waiting_since=snap.taken_at,
            source=source,
            status="Queued",
            resolved_repo_identity=repo.resolved_identity,
        )

    async def _assert_run_current(
        self, decision: str, snap: "IntentSnapshot", rec: ActionRecord | None = None,
    ) -> None:
        if decision not in ("run", "resume"):
            return
        reason = run_unavailable_reason(
            snap, stage=rec.stage if rec else None, unit=rec.unit if rec else None,
        )
        if reason is None:
            return
        if rec is not None and rec.status == "Queued" and not rec.delivery_id and not rec.delivering_at:
            rec = await self._transition(rec, "Cancelled", reason="command_superseded", evidence={
                "superseded": True, "command_unavailable_reason": reason, "current_stage": snap.stage,
            })
        raise StudioError(
            "run_not_applicable", "open the intent's current checkpoint instead of this run",
            details={"reason": reason, "current_stage": snap.stage,
                     **({"card": await self.card(rec, snap)} if rec is not None else {})},
        )

    async def create_force_stop(
        self, repo: RepoRecord, space: str, intent_dir: str, *, snap: "IntentSnapshot"
    ) -> SubmitReceipt:
        """Commit a force stop and hand the browser the host's stop call (C28, review P16).

        The third lane: no prompt, no wire text, no execution lease, no cursor switch, no presence
        baseline — nothing reaches the model, so none of the guards that protect a *decision* apply.
        What does apply is the durable record: FR-RUN-004 requires an interruption to be confirmed and
        recorded before it happens, and the record is what later turns into ``Interrupted`` and the
        manual takeover PRD §11.2 insists on.
        """
        binding = await self._sessions.get(repo.repo_id, space, intent_dir)
        slot_key = binding.slot_key
        if not slot_key:
            raise StudioError(
                "session_unbound",
                "this intent has no bound chat slot to stop",
                details={"intent_key": snap.intent_key},
            )
        live = next(
            (
                rec
                for rec in await self.list_live(
                    repo_id=repo.repo_id, space=space, intent_dir=intent_dir, include_revision=True
                )
                if rec.type == "force_stop"
            ),
            None,
        )
        if live is not None and live.delivery_id:
            return self._force_stop_receipt(live)

        captured = self._projection.captured(snap, snap.stage)
        key = dedupe_key(
            "force_stop", repo.repo_id, snap.intent_key, snap.stage, captured["boundary_token"]
        )
        rec = live
        if rec is None:
            evidence = self._projection.evidence(snap, findings=(), binding=binding).to_json()
            evidence[CARD_META_KEY] = {
                "severity": "info",
                "decisions": list(DECISIONS["force_stop"]),
                "headline_params": {"stage": snap.stage},
                "consequence_variant": None,
                "stage_ref": self._projection.stage_ref(snap, snap.stage),
                "reason": None,
                "related_action_id": None,
                "captured_at": captured["captured_at"],
                "stable": bool(captured["stable"]),
            }
            rec = await self._insert(
                action_type="force_stop",
                risk_class="host_control",
                repo_id=repo.repo_id,
                space=space,
                intent_dir=intent_dir,
                intent_uuid=snap.row.uuid if snap.row is not None else binding.intent_uuid,
                stage=snap.stage,
                unit=snap.unit,
                captured=captured,
                evidence=evidence,
                dedupe_key=key,
                waiting_since=snap.taken_at,
                source="studio",
                status="Queued",
                resolved_repo_identity=repo.resolved_identity,
            )
        delivery_id = self._ids.new("dl")
        now = self._clock.iso()
        deadline = C.iso_from_epoch(self._clock.now() + C.DELIVERY_ACK_DEADLINE_SECS)
        rec = await self._transition(
            rec,
            "Delivering",
            reason="force_stop_committed",
            evidence={},
            values={
                "delivery_id": delivery_id,
                "slot_key": slot_key,
                "session_key": binding.session_key or f"dashboard:{slot_key}",
                "delivering_at": now,
                "deadline_at": deadline,
                "boot_id": self._boot_id,
            },
        )
        return self._force_stop_receipt(rec)

    def _force_stop_receipt(self, rec: ActionRecord) -> SubmitReceipt:
        return SubmitReceipt(
            action_id=rec.action_id,
            status=rec.status,
            lane="host_control",
            delivery_id=rec.delivery_id or "",
            slot_key=rec.slot_key or "",
            session_key=rec.session_key or "",
            wire_text=None,
            expires_at=rec.deadline_at or "",
            lease_generation=None,
            host=HostCall("POST", f"/api/chat/slots/{rec.slot_key}/stop", {}),
        )

    # ---- two-phase submit ------------------------------------------------- #

    async def submit(
        self,
        action_id: str,
        *,
        captured: Mapping[str, Any],
        payload: Mapping[str, Any],
        client_wire_text: str,
        user: str,
        source: str = "studio",
    ) -> SubmitReceipt:
        """Commit one human decision, in the order §1.12 fixes, and return the browser's next call.

        The order is the contract, not a convenience: every step is a refusal that must happen before
        the step after it can do damage. The cheap refusals come first so a paused intent never costs a
        child process; the lease is taken only once the decision is known to be legal; the cursor switch
        happens under the lease and before anything is durable; the durable ``Delivering`` record is
        committed *before* this function returns, therefore before the browser can send (PRD §11.5).
        From the moment the lease is held, every exception path releases it (C31/P15) — a lease left
        behind by a failed submit would make the repository permanently ``repo_busy``.
        """
        rec = await self.get(action_id)
        if rec.status not in SUBMITTABLE_STATUSES:
            raise StudioError(
                "action_not_submittable",
                f"an action in {rec.status} cannot be submitted",
                details={"action_id": action_id, "status": rec.status},
            )
        if rec.evidence.get("delivery_confirmed"):
            # `NotDelivered` is submittable, so this is the second door into `Delivering` for a row that
            # a later report proved delivered after all. `retry` refuses it (`retry_not_allowed`); the
            # guard has to be on both edges or the refusal is decorative.
            raise StudioError(
                "not_delivered_unproven",
                "a later report confirmed this decision was delivered; it must not be sent again",
                details={"action_id": action_id, "status": rec.status, "delivery_confirmed": True},
            )
        decision = str(payload.get("decision") or "")
        if decision not in DECISIONS.get(rec.type, ()):
            raise StudioError(
                "invalid_decision",
                f"{rec.type} cards do not offer {decision!r}",
                details={"decision": decision, "type": rec.type, "offered": list(DECISIONS.get(rec.type, ()))},
            )
        if decision not in HUMAN_LANE_DECISIONS:
            raise StudioError(
                "invalid_decision",
                f"{decision!r} is not submitted through the human lane",
                details={"decision": decision, "lane": lane_for(decision)},
            )
        if (
            decision == "accept_as_is"
            and int(rec.captured.stage_attempt or 1) < C.ACCEPT_AS_IS_MIN_ATTEMPT
        ):
            raise StudioError(
                "invalid_decision",
                "Accept as-is only exists after three rejection cycles",
                details={
                    "decision": decision,
                    "stage_attempt": rec.captured.stage_attempt,
                    "min_attempt": C.ACCEPT_AS_IS_MIN_ATTEMPT,
                },
            )

        repo = await self._repo(rec.repo_id)
        if repo.availability != "available":
            raise StudioError(
                "repo_unavailable",
                "restore access to this repository before submitting a decision",
                details={"repo_id": repo.repo_id, "availability": repo.availability,
                         "detail": repo.availability_detail},
            )
        binding = await self._sessions.get(rec.repo_id, rec.space, rec.intent_dir)
        self._assert_dispatchable(
            binding, decision=decision, intent_key=rec.intent_key, repo_id=rec.repo_id
        )
        if decision in ("run", "resume") and await self._breaker_open(rec.repo_id, rec.intent_key):
            raise StudioError(
                "breaker_open",
                "this intent's circuit breaker is open; review the failure first",
                details={"intent_key": rec.intent_key, "decision": decision},
            )

        snap = await self._snapshot(repo, rec.space, rec.intent_dir)
        await self._assert_run_current(decision, snap, rec)
        audit_question = rec.type == "question" and (
            (rec.evidence.get("questions") or {}).get("origin") or {}
        ).get("kind") == "audit"
        file_answer = decision == "answers" and not audit_question
        if audit_question:
            self._assert_audit_question_current(rec, snap)
        if file_answer and (
            snap.questions is None or not file_questions_support_forms(snap.questions)
        ):
            raise StudioError("invalid_decision", "this question file cannot be answered as a form",
                              details={"reason": "unsupported_question_format"})
        grouped = bool(self._settings.capabilities().get("grouped_answers", {}).get("available"))
        wire_text = wire_text_for(
            decision, payload, questions=snap.questions, grouped_answers_enabled=grouped
        )
        if wire_text is None:
            raise StudioError(
                "invalid_decision",
                f"{decision!r} sends nothing to the conversation",
                details={"decision": decision},
            )
        if client_wire_text != wire_text:
            raise StudioError(
                "invalid_decision",
                "the text the browser displayed is not the text this decision sends",
                details={"decision": decision, "expected": wire_text},
            )

        live_captured = Captured.from_json(self._projection.captured(snap, rec.stage))
        request_captured = Captured.from_json(captured)
        if (
            live_captured.differences(rec.captured)
            and rec.type in PROMPT_COMMAND_TYPES
            and rec.status == "Queued"
            and rec.delivery_id is None
            and rec.delivering_at is None
            and snap.stable
        ):
            # Command cards are user-created, so the reconciler has no seed to refresh them from.
            # Persist the evidence already shown by card(), under CAS, before comparing the user's
            # capture. A stale browser still fails below; a decision ever dispatched stays frozen.
            evidence = dict(rec.evidence)
            slot = self._host.slot(binding.slot_key) if binding.slot_key else None
            live = self._projection.evidence(snap, binding=binding, slot=slot, stage=rec.stage).to_json()
            for key in LIVE_REFRESHED_EVIDENCE_KEYS:
                evidence[key] = live.get(key)
            evidence[CARD_META_KEY] = {
                **rec.card_meta,
                "captured_at": live_captured.captured_at,
                "stable": live_captured.stable,
            }
            generation = await asyncio.to_thread(
                self._storage.cas_update, _ACTIONS, rec.action_id, rec.status_generation,
                {
                    "captured_state_hash": live_captured.state_hash,
                    "boundary_token": live_captured.boundary_token,
                    "question_digest": live_captured.question_digest,
                    "stage_attempt": live_captured.stage_attempt,
                    "evidence_digest": live_captured.evidence_digest,
                    "is_active_captured": _bool_int(live_captured.is_active),
                    "evidence_json": evidence,
                },
            )
            # Use precisely the generation we wrote. Re-reading could adopt a concurrent submit's
            # generation and accidentally make a second dispatch eligible.
            rec = replace(rec, captured=live_captured, evidence=evidence, status_generation=generation)
        drift = sorted(
            set(live_captured.differences(request_captured)) | set(live_captured.differences(rec.captured))
        )
        if drift:
            raise StudioError(
                "action_stale",
                "the boundary this decision was captured against has moved",
                details={"fields": drift, "card": await self.card(rec, snap)},
            )

        findings = await self._findings(repo, snap, binding)
        blocking = self._consistency.blocking(findings)
        self._raise_blocking_findings(blocking, repo.repo_id)
        if not snap.stable:
            raise StudioError(
                "unstable_read",
                "the record changed while it was being read",
                details={"intent_key": rec.intent_key},
            )

        slot_key = binding.slot_key
        if not slot_key:
            raise StudioError(
                "session_unbound",
                "this intent has no bound chat slot",
                details={"intent_key": rec.intent_key},
            )
        view = self._host.slot(slot_key)
        if view is None or not self._host.slot_matches_repo(view, repo.canonical_path):
            raise StudioError(
                "slot_mismatch",
                "the bound slot is gone or no longer points at this repository",
                details={
                    "slot_key": slot_key,
                    "canonical_path": repo.canonical_path,
                    "present": view is not None,
                },
            )
        reasons = busy_reasons(view)
        if reasons:
            raise StudioError(
                "slot_busy",
                "the session is busy; sending now would interleave two turns",
                details={"slot_key": slot_key, "reasons": list(reasons)},
            )
        if file_answer:
            self._assert_file_answer_session(slot_key)

        grant = await self._scheduler.acquire_execution(
            repo,
            intent_uuid=binding.intent_uuid or rec.intent_uuid,
            session_key=binding.session_key or view.session_key,
            action_id=rec.action_id,
        )
        try:
            engine_dir = repo.engine_dir or snap.engine_dir or C.STUDIO_HARNESS_DIR
            expected_uuid = binding.intent_uuid or rec.intent_uuid or (
                snap.row.uuid if snap.row is not None else None
            )
            readback = await asyncio.to_thread(
                self._engine.switch_cursor_sync,
                Path(repo.canonical_path),
                engine_dir,
                rec.space,
                rec.intent_dir,
                expected_uuid,
                lease_generation=grant.generation,
                repo_id=repo.repo_id,
            )
            await self._engine.drain_activity()
            if not readback.ok or readback.state_sha256 != live_captured.state_hash:
                mismatch = list(readback.mismatch) or ["state_changed"]
                raise StudioError(
                    "cursor_mismatch",
                    "the repository cursor does not name this intent",
                    details={
                        "mismatch": mismatch,
                        "readback": readback.to_json(),
                        "expected_state_hash": live_captured.state_hash,
                    },
                )
            if audit_question:
                # Cursor switching may yield to an engine write. Recheck the actual audit decision
                # under the execution lease before committing a durable Delivering record.
                snap = await self._snapshot(repo, rec.space, rec.intent_dir)
                self._assert_audit_question_current(rec, snap)
            if file_answer:
                snap = await self._snapshot(repo, rec.space, rec.intent_dir)
                fresh = Captured.from_json(self._projection.captured(snap, rec.stage))
                drift = fresh.differences(live_captured)
                if drift or not snap.stable:
                    raise StudioError("action_stale", "the question group changed before dispatch",
                                      details={"fields": list(drift)})
            if decision in ("run", "resume"):
                snap = await self._snapshot(repo, rec.space, rec.intent_dir)
                if not snap.stable:
                    raise StudioError("unstable_read", "the intent changed before dispatch")
                await self._assert_run_current(decision, snap, rec)
            presence = await asyncio.to_thread(
                self._reader.presence_baseline,
                Path(repo.canonical_path),
                rec.space,
                rec.intent_dir,
                snap.audit,
                live_captured.state_hash,
            )
            if file_answer:
                self._assert_file_answer_session(slot_key)
            delivery_id = self._ids.new("dl")
            now = self._clock.iso()
            deadline = C.iso_from_epoch(self._clock.now() + C.DELIVERY_ACK_DEADLINE_SECS)
            body = dict(payload)
            evidence = dict(rec.evidence)
            evidence["cursor_readback"] = readback.to_json()
            evidence["presence_baseline"] = presence.to_json()
            evidence["outcome"] = None
            values = {
                "status": "Delivering",
                "delivery_id": delivery_id,
                "slot_key": slot_key,
                "session_key": binding.session_key or view.session_key,
                "lease_generation": grant.generation,
                "delivering_at": now,
                "deadline_at": deadline,
                "wire_text": wire_text,
                "payload_json": body,
                "payload_digest": _digest(body),
                "human_text": _human_prose(payload),
                "cursor_readback_json": readback.to_json(),
                "presence_baseline_json": presence.to_json(),
                "boot_id": self._boot_id,
                "evidence_json": evidence,
                "source": source,
            }
            generation = await asyncio.to_thread(
                self._storage.deliver_under_lease,
                rec.action_id,
                rec.status_generation,
                identity=repo.resolved_identity or "",
                lease_generation=grant.generation,
                new_values=values,
            )
        except BaseException:
            await self._scheduler.release(grant)
            raise

        after = await self.get(rec.action_id)
        await self._record_transition(
            rec,
            after,
            generation,
            reason=f"submitted:{decision}",
            evidence={"decision": decision, "delivery_id": after.delivery_id},
            activity_kind="action.submitted",
            human_text=after.human_text,
            params={
                "decision": decision,
                "type": rec.type,
                "delivery_id": after.delivery_id,
                "slot_key": slot_key,
                "user": user,
            },
        )
        return SubmitReceipt(
            action_id=after.action_id,
            status=after.status,
            lane="human_lane",
            delivery_id=after.delivery_id or "",
            slot_key=after.slot_key or "",
            session_key=after.session_key or "",
            wire_text=wire_text,
            expires_at=after.deadline_at or "",
            lease_generation=grant.generation,
            host=HostCall(
                "POST",
                C.HOST_CHAT_SEND_PATH,
                {
                    "message": wire_text,
                    "slot": slot_key,
                    "agent": C.AIDLC_AGENT_NAME,
                    "meta": {
                        "studio_action_id": after.action_id,
                        "studio_delivery_id": after.delivery_id,
                    },
                },
            ),
        )

    def _assert_file_answer_session(self, slot_key: str) -> None:
        """A file reply must never bypass a host widget parked on its own answer endpoint."""
        cards = self._host.pending_question_cards(slot_key)
        capability = self._host.capabilities().get("pending_questions")
        if capability is None or not capability.available:
            raise StudioError("host_unavailable", "the host's pending questions could not be inspected")
        if any("ask_id" in card for card in cards):
            raise StudioError("slot_busy", "answer the active native question in the conversation",
                              details={"slot_key": slot_key, "reasons": ["native_question_pending"]})
        view = self._host.slot(slot_key)
        if view is None or busy_reasons(view):
            raise StudioError("slot_busy", "the session changed before the answers could be dispatched",
                              details={"slot_key": slot_key})

    async def record_delivery(
        self,
        action_id: str,
        *,
        delivery_id: str,
        outcome: str,
        http_status: int | None,
        receipt: Mapping[str, Any] | None,
    ) -> tuple[ActionRecord, bool]:
        """Record what the browser observed when it sent. Idempotent per ``delivery_id``.

        The asymmetry here is the whole at-most-once story. ``delivered`` is believed (the host said
        ok, and the host is the authority on its own queue). ``not_delivered`` is *never* believed: it
        needs an authoritative 4xx — or one of the two verified preflight codes — **and** four
        independent checks the backend performs itself. If any of them fails to prove absence the row
        becomes ``DeliveryUncertain`` and a human decides, because the alternative is re-sending a
        decision the conductor already acted on (C24, review P05/R02/R16).
        """
        if outcome not in ("delivered", "not_delivered", "uncertain"):
            raise StudioError(
                "bad_param", f"unknown delivery outcome {outcome!r}", details={"outcome": outcome}
            )
        rec = await self.get(action_id)
        if not rec.delivery_id or rec.delivery_id != delivery_id:
            raise StudioError(
                "delivery_ack_invalid",
                "this delivery id does not belong to this action",
                details={"action_id": action_id, "delivery_id": delivery_id},
            )
        body = dict(receipt or {})
        if rec.status in ("Draft", "Queued"):
            # Nothing is in flight: either the row was re-queued by `retry` (a duplicate report for the
            # old attempt) or the caller is confused. Accepting it would let a stale browser tab
            # advance an action that has no delivery.
            raise StudioError(
                "delivery_ack_invalid",
                "this action has no delivery in flight",
                details={"action_id": action_id, "status": rec.status, "delivery_id": delivery_id},
            )

        if rec.status == "Delivering":
            if outcome == "delivered":
                return await self._accept_delivered(rec, body), False
            if outcome == "uncertain":
                updated = await self._transition(
                    rec,
                    "DeliveryUncertain",
                    reason="delivery_report_uncertain",
                    evidence={"receipt": body, "outcome": "uncertain", "http_status": http_status},
                )
                return updated, False
            return await self._accept_not_delivered(rec, body, http_status), False

        if rec.status in ("Delivered", "Processing"):
            if outcome == "delivered":
                return rec, True
            raise IllegalTransition(
                "the host already confirmed this delivery",
                details={"from": rec.status, "outcome": outcome, "action_id": action_id},
            )

        if rec.status in ("DeliveryUncertain", "ReconciliationRequired"):
            if outcome == "delivered":
                updated = await self._patch_evidence(
                    rec,
                    {
                        "delivery_confirmed": True,
                        "receipt": body,
                        "outcome": "delivered",
                        "late_report_at": self._clock.iso(),
                    },
                )
                await self._activity.record(
                    kind="action.delivery",
                    severity="attention",
                    repo_id=rec.repo_id,
                    space=rec.space,
                    intent_dir=rec.intent_dir,
                    stage=rec.stage,
                    action_id=rec.action_id,
                    params={"outcome": "delivered", "late": True, "status": rec.status},
                )
                return updated, False
            if outcome == "uncertain":
                return rec, True
            authoritative = _authoritative_4xx(http_status, body)
            updated = await self._patch_evidence(
                rec,
                {
                    "receipt": body,
                    "http_status": http_status,
                    "authoritative_4xx": authoritative,
                    "late_report_at": self._clock.iso(),
                },
            )
            if not authoritative:
                raise StudioError(
                    "not_delivered_unproven",
                    "a late not-delivered report needs an authoritative 4xx",
                    details={"checks": {}, "reason": "receipt_not_authoritative"},
                )
            if rec.status == "DeliveryUncertain":
                # The reconciler continues DeliveryUncertain → ReconciliationRequired on its next tick
                # (R07); recording the evidence now is all this report can safely do.
                return updated, False
            checks = await self._prove_not_delivered(updated, body)
            if not all(checks.values()):
                raise StudioError(
                    "not_delivered_unproven",
                    "this decision cannot be proven undelivered",
                    details={"checks": checks},
                )
            final = await self._transition(
                updated,
                "NotDelivered",
                reason="not_delivered_proven",
                evidence={"checks": checks, "outcome": "not_delivered"},
            )
            await self._release_lease(final)
            return final, False

        # Terminal, or NotDelivered: nothing left to move. The report is kept as evidence so a support
        # question ("the browser said it failed") has an answer, and a `delivered` late report closes
        # the door on `retry`.
        late = list(rec.evidence.get("late_reports") or ())
        late.append(
            {"at": self._clock.iso(), "outcome": outcome, "http_status": http_status, "receipt": body}
        )
        patch: dict[str, Any] = {"late_reports": late[-10:]}
        if outcome == "delivered":
            patch["delivery_confirmed"] = True
        updated = await self._patch_evidence(rec, patch)
        return updated, True

    async def _accept_delivered(
        self, rec: ActionRecord, receipt: Mapping[str, Any]
    ) -> ActionRecord:
        if receipt.get("ok") is not True:
            raise StudioError(
                "delivery_ack_invalid",
                "a delivered report must carry the host's ok receipt",
                details={"receipt": dict(receipt)},
            )
        now = self._clock.iso()
        queued = bool(receipt.get("queued"))
        turn_timeout = self._turn_timeout()
        evidence: dict[str, Any] = {"receipt": dict(receipt), "outcome": "delivered"}
        if queued:
            evidence["queued_at"] = now
        deadline = C.iso_from_epoch(
            self._clock.now()
            + (turn_timeout * C.DELIVERED_QUEUED_WAIT_FACTOR if queued else turn_timeout)
        )
        rec = await self._transition(
            rec,
            "Delivered",
            reason="delivery_report_delivered",
            evidence=evidence,
            values={"delivered_at": now, "deadline_at": deadline},
        )
        if queued:
            # The host accepted it behind a running turn. The processing clock starts when the turn
            # actually starts, which only the reconciler can observe (review P22).
            return rec
        return await self._transition(
            rec, "Processing", reason="turn_started", evidence={}, values={"deadline_at": deadline}
        )

    async def _accept_not_delivered(
        self, rec: ActionRecord, receipt: Mapping[str, Any], http_status: int | None
    ) -> ActionRecord:
        authoritative = _authoritative_4xx(http_status, receipt)
        preflight = str(receipt.get("code") or "") in CLIENT_PREFLIGHT_CODES
        # The four checks run even when the receipt is not authoritative: the refusal body carries them
        # (§2.4 `details.checks`), and "here is what we could and could not see" is the difference
        # between a user who can act and a user who is told no.
        checks = await self._prove_not_delivered(rec, receipt)
        if (authoritative or preflight) and all(checks.values()):
            final = await self._transition(
                rec,
                "NotDelivered",
                reason="not_delivered_proven",
                evidence={
                    "receipt": dict(receipt),
                    "http_status": http_status,
                    "outcome": "not_delivered",
                    "checks": checks,
                    **({"authoritative_4xx": True} if authoritative else {}),
                    **({"client_preflight": receipt.get("code")} if preflight else {}),
                },
            )
            await self._release_lease(final)
            return final
        await self._transition(
            rec,
            "DeliveryUncertain",
            reason="not_delivered_unproven",
            evidence={
                "receipt": dict(receipt),
                "http_status": http_status,
                "outcome": "uncertain",
                "checks": checks,
                "authoritative_4xx": authoritative,
            },
        )
        raise StudioError(
            "not_delivered_unproven",
            "this decision cannot be proven undelivered",
            details={
                "checks": checks,
                "reason": "receipt_not_authoritative" if not (authoritative or preflight) else "evidence",
            },
        )

    async def _prove_not_delivered(
        self, rec: ActionRecord, receipt: Mapping[str, Any]
    ) -> dict[str, bool]:
        """The four proofs of absence (C24). Every one must hold; any unknown counts as *not proven*.

        ``row_absent`` looks in the canonical slot **and** in every slot bound to the repository,
        because a message that landed in another session of the same repo was still delivered — it just
        went somewhere Studio did not expect (that is the ``wrong_slot`` case, not a re-send).
        ``disk_baseline_unchanged`` is the half that survives a restart: the host's transcript is
        process memory, so without a disk comparison a restart would look exactly like a message that
        was never sent (review R02).
        """
        repo = await self._repo(rec.repo_id)
        slot_key = rec.slot_key or ""
        since = rec.delivering_at or rec.created_at
        wire_text = rec.wire_text or ""
        code = str(receipt.get("code") or "")

        found = None
        if slot_key:
            found = self._host.find_delivery_row(
                slot_key, since_ts=since, delivery_id=rec.delivery_id or "", wire_text=wire_text
            )
        if found is None:
            for view in self._host.slots_for_project(repo.canonical_path):
                if view.key == slot_key:
                    continue
                found = self._host.find_delivery_row(
                    view.key, since_ts=since, delivery_id=rec.delivery_id or "", wire_text=wire_text
                )
                if found is not None:
                    break
        view = self._host.slot(slot_key) if slot_key else None

        # Asked only now, and only once: `can_prove_absence` reports on the reads just performed, and
        # both of the checks below turn on the same fact. An unreadable slot table makes `found is None`
        # and `view is None` statements about Studio's eyesight, not about the host's queue — and the
        # step after NotDelivered is Retry, so a blind "absent" is a decision delivered twice.
        can_see = self._host.can_prove_absence()
        row_absent = found is None and can_see

        if code == "slot_missing":
            # The slot the browser was told to use is gone. Nothing could have run in it, so "idle" is
            # proven by absence rather than by its flags — provided the slot table could be read at all,
            # because otherwise "gone" is only the preflight's opinion, unconfirmed by the host.
            slot_idle = view is None and can_see
        elif view is None:
            # The slot vanished without a preflight code saying so: this is the restart case, and a
            # missing slot is missing *evidence*, never proof that no turn happened.
            slot_idle = False
        else:
            slot_idle = not view.running and not _at_or_after(view.last_turn_ts, since)

        if rec.presence_baseline is None:
            # `host_control` sends nothing to the model, so there is no disk half to compare. For any
            # other lane a missing baseline is missing *evidence* and must never count as proof.
            disk_unchanged = rec.type == "force_stop"
        else:
            snap = await self._snapshot(repo, rec.space, rec.intent_dir)
            fresh = await asyncio.to_thread(
                self._reader.presence_baseline,
                Path(repo.canonical_path),
                rec.space,
                rec.intent_dir,
                snap.audit,
                snap.state_sha256,
            )
            disk_unchanged = _presence_comparable(fresh) == _presence_comparable(rec.presence_baseline)

        return {
            "row_absent": bool(row_absent),
            "slot_idle": bool(slot_idle),
            "disk_baseline_unchanged": bool(disk_unchanged),
            "boot_unchanged": rec.boot_id == self._boot_id,
        }

    # ---- lifecycle -------------------------------------------------------- #

    async def retry(self, action_id: str, *, user: str) -> ActionRecord:
        """Re-queue a proven-undelivered decision, or start a fresh attempt after a failure.

        ``NotDelivered → Queued`` reuses the same action because at-most-once is intact: the proof
        said nothing was enqueued. A ``Failed`` command gets a *new* action instead — the failed row is
        history, and history is what the user is looking at when they press Retry.
        """
        rec = await self.get(action_id)
        if rec.status == "NotDelivered":
            if rec.evidence.get("delivery_confirmed"):
                raise StudioError(
                    "retry_not_allowed",
                    "a later report confirmed this decision was delivered; it must not be replayed",
                    details={"action_id": action_id},
                )
            updated = await self._transition(
                rec, "Queued", reason="retry", evidence={"retried_by": user}, activity_kind="action.retried"
            )
            return updated
        if rec.status == "Failed" and rec.type in PROMPT_COMMAND_TYPES:
            await self._reset_breakers(rec.repo_id, rec.intent_key)
            repo = await self._repo(rec.repo_id)
            snap = await self._snapshot(repo, rec.space, rec.intent_dir)
            created = await self.create_command(
                rec.type, repo, rec.space, rec.intent_dir, snap=snap, source=rec.source
            )
            await self._patch_evidence(rec, {"superseded": True, "retried_as": created.action_id})
            await self._activity.record(
                kind="action.retried",
                severity="info",
                repo_id=rec.repo_id,
                space=rec.space,
                intent_dir=rec.intent_dir,
                stage=rec.stage,
                action_id=created.action_id,
                params={"retried": rec.action_id, "type": rec.type, "user": user},
            )
            return created
        raise StudioError(
            "retry_not_allowed",
            f"an action in {rec.status} cannot be retried",
            details={"action_id": action_id, "status": rec.status, "type": rec.type},
        )

    async def cancel(self, action_id: str, *, user: str) -> ActionRecord:
        """Cancel a card, but only where cancelling cannot lose a decision.

        A command the user queued is theirs to withdraw. A *derived* card is not: it exists because a
        boundary is on disk, so dismissing a ``Queued`` gate card would hide a ``[?]`` the workflow is
        still waiting on (FR-ACT-007, C33). The one exception is ``NotDelivered`` — a decision that
        provably never left the building.
        """
        rec = await self.get(action_id)
        allowed = (
            rec.status in ("Draft", "Queued", "NotDelivered")
            if rec.type in COMMAND_ACTION_TYPES
            else rec.status == "NotDelivered"
        )
        if not allowed:
            raise StudioError(
                "cancel_not_safe",
                "this card cannot be cancelled from its current state",
                details={"action_id": action_id, "status": rec.status, "type": rec.type},
            )
        return await self._transition(
            rec, "Cancelled", reason="cancelled_by_user", evidence={"cancelled_by": user}
        )

    async def resolve(
        self, action_id: str, *, decision: str, payload: Mapping[str, Any], user: str
    ) -> tuple[ActionRecord, ActionRecord | None]:
        """Apply a decision that never leaves Studio; returns ``(this action, created action | None)``.

        Every studio-only decision ends its card by cancelling it rather than by inventing a
        ``ResolvedNoTransition`` edge the PRD graph does not have: nothing was sent, so there is
        nothing to resolve. When the condition that produced the card is still true on disk, the next
        scan re-derives a fresh ``Queued`` card — which is the honest outcome for "I acknowledged this
        but it is still broken".
        """
        rec = await self.get(action_id)
        # The row's own type *and* its queue type: an uncertain delivery is re-typed in the queue
        # (`delivery_uncertain`), so a gate whose decision went missing offers reconcile / mark
        # undelivered / resubmit — exactly what `card()` shows. Validating against the stored type
        # alone would refuse the only three decisions such a card has.
        offered = set(DECISIONS.get(rec.type, ())) | set(DECISIONS.get(rec.queue_type, ()))
        if decision not in offered:
            raise StudioError(
                "invalid_decision",
                f"{rec.type} cards do not offer {decision!r}",
                details={"decision": decision, "type": rec.type, "queue_type": rec.queue_type},
            )
        if decision not in STUDIO_ONLY_DECISIONS:
            raise StudioError(
                "invalid_decision",
                f"{decision!r} is not a studio-only decision",
                details={"decision": decision, "lane": lane_for(decision)},
            )

        if decision == "mark_not_delivered":
            return await self._mark_not_delivered(rec, payload, user), None
        if decision == "resubmit":
            return await self._resubmit(rec, payload, user)
        if decision == "reconcile":
            if self.reconciler is None:
                raise StudioError(
                    "internal_error",
                    "the reconciler is not attached to this broker",
                    details={"action_id": action_id},
                )
            updated = await self.reconciler.reconcile_now(rec.action_id)
            return (updated if isinstance(updated, ActionRecord) else await self.get(action_id)), None
        if decision == "acknowledge":
            return await self._acknowledge(rec, user), None
        if decision == "rebind_session":
            repo = await self._repo(rec.repo_id)
            slot_key = str(payload.get("slot_key") or "")
            if not slot_key:
                raise StudioError(
                    "bad_body", "rebind_session needs a slot_key", details={"decision": decision}
                )
            await self._sessions.takeover(repo, rec.space, rec.intent_dir, slot_key=slot_key)
            updated = await self._transition(
                rec, "Cancelled", reason="session_rebound", evidence={"slot_key": slot_key, "by": user}
            )
            return updated, None
        if decision == "keep_paused":
            await self._sessions.set_paused(rec.repo_id, rec.space, rec.intent_dir, True)
            if rec.status in SUBMITTABLE_STATUSES:
                updated = await self._transition(
                    rec, "Cancelled", reason="kept_paused", evidence={"kept_paused": True, "by": user}
                )
            else:
                updated = await self._patch_evidence(rec, {"kept_paused": True})
            return updated, None
        if decision == "retry_now":
            return await self._retry_now(rec, user)
        if decision == "run_now":
            repo = await self._repo(rec.repo_id)
            snap = await self._snapshot(repo, rec.space, rec.intent_dir)
            created = await self.create_command("run", repo, rec.space, rec.intent_dir, snap=snap)
            updated = await self._cancel_if_open(rec, "run_now", {"created": created.action_id})
            return updated, created
        if decision == "pick_intent":
            return await self._pick_intent(rec, payload, user), None
        raise StudioError(
            "invalid_decision", f"unhandled decision {decision!r}", details={"decision": decision}
        )

    async def _mark_not_delivered(
        self, rec: ActionRecord, payload: Mapping[str, Any], user: str
    ) -> ActionRecord:
        """Let a human close a decision as never delivered — with evidence, never on their word.

        Only from ``ReconciliationRequired`` (R07): that is the state the reconciler puts a decision in
        once it has looked and could not tell. The human may finish the proof with an authoritative
        4xx receipt they can see, or accept the reconciler's own four proofs; anything less is
        ``not_delivered_unproven``, because "mark as not delivered" is followed by Retry.
        """
        if rec.status != "ReconciliationRequired":
            raise IllegalTransition(
                "a decision can only be marked undelivered from ReconciliationRequired",
                details={"from": rec.status, "to": "NotDelivered", "action_id": rec.action_id},
            )
        if rec.evidence.get("delivery_confirmed"):
            # Studio has seen this decision reach the conversation (a transcript row, or a late
            # `delivered` report). A client-supplied 4xx cannot outvote that: the receipt describes one
            # HTTP attempt, while `delivery_confirmed` describes the host's own queue. Believing the
            # receipt here would end in Retry, and the human made this decision once. `_resubmit` refuses
            # the same row for the same reason; the only honest exits are `reconcile` and reading the
            # evidence.
            raise StudioError(
                "not_delivered_unproven",
                "this decision was delivered; it cannot be recorded as never sent",
                details={"action_id": rec.action_id, "delivery_confirmed": True},
            )
        evidence = payload.get("evidence")
        evidence = evidence if isinstance(evidence, Mapping) else {}
        receipt = evidence.get("receipt")
        receipt = receipt if isinstance(receipt, Mapping) else {}
        by_receipt = _authoritative_4xx(evidence.get("http_status"), receipt)
        stored = rec.evidence
        by_reconciler = bool(
            stored.get("transcript_row_absent")
            and stored.get("slot_ran_since") is False
            and stored.get("disk_baseline_unchanged")
            and stored.get("boot_id_unchanged")
        )
        if not (by_receipt or by_reconciler):
            raise StudioError(
                "not_delivered_unproven",
                "marking a decision undelivered needs a 4xx receipt or the reconciler's proof",
                details={
                    "checks": {
                        "authoritative_4xx": by_receipt,
                        "transcript_row_absent": bool(stored.get("transcript_row_absent")),
                        "slot_ran_since": stored.get("slot_ran_since"),
                        "disk_baseline_unchanged": bool(stored.get("disk_baseline_unchanged")),
                        "boot_id_unchanged": bool(stored.get("boot_id_unchanged")),
                    }
                },
            )
        updated = await self._transition(
            rec,
            "NotDelivered",
            reason="marked_not_delivered",
            evidence={
                "outcome": "not_delivered",
                "marked_by": user,
                "authoritative_4xx": by_receipt,
                **({"receipt": dict(receipt)} if receipt else {}),
            },
        )
        await self._release_lease(updated)
        return updated

    async def _resubmit(
        self, rec: ActionRecord, payload: Mapping[str, Any], user: str
    ) -> tuple[ActionRecord, ActionRecord]:
        """Offer the same decision again as a **new** action, after the human read the evidence.

        Refused outright once anything confirmed delivery: a delivered decision is resolved from disk
        by the reconciler, never replayed (PRD §11.1/§12.1). The echoed
        ``acknowledged_evidence_sha256`` is what makes "the human read the evidence" checkable — if the
        delivery, audit or presence evidence changed since the card was rendered, the answer is
        ``action_stale`` and a refreshed card, not a second prompt.
        """
        if rec.status != "ReconciliationRequired":
            raise IllegalTransition(
                "a decision can only be resubmitted from ReconciliationRequired",
                details={"from": rec.status, "to": "Queued", "action_id": rec.action_id},
            )
        if rec.evidence.get("delivery_confirmed"):
            raise StudioError(
                "not_delivered_unproven",
                "this decision was delivered; it is resolved from disk, never replayed",
                details={"action_id": rec.action_id, "delivery_confirmed": True},
            )
        card = await self.card(rec, None)
        expected = card["acknowledged_evidence_sha256"]
        supplied = str(payload.get("acknowledged_evidence_sha256") or "")
        if supplied != expected:
            raise StudioError(
                "action_stale",
                "the evidence changed since it was shown; read it again before resubmitting",
                details={"expected": expected, "card": card},
            )
        # Ordered, not atomic: the old row must leave the live-dedupe index before the new row can
        # take its key. If the insert then failed, the boundary would have no live card for one tick
        # and `upsert_derived` would re-create one — the same repair path a crash here takes.
        superseded = await self._transition(
            rec,
            "Failed",
            reason="superseded_by_resubmit",
            evidence={"superseded": True, "resubmitted_by": user},
        )
        evidence = dict(rec.evidence)
        evidence.pop("receipt", None)
        evidence.pop("checks", None)
        evidence.pop("late_reports", None)
        evidence["superseded_action_id"] = rec.action_id
        created = await self._insert(
            action_type=rec.type,
            risk_class=rec.risk_class,
            repo_id=rec.repo_id,
            space=rec.space,
            intent_dir=rec.intent_dir,
            intent_uuid=rec.intent_uuid,
            stage=rec.stage,
            unit=rec.unit,
            captured=rec.captured.to_json(),
            evidence=evidence,
            dedupe_key=rec.dedupe_key,
            waiting_since=rec.waiting_since,
            source=rec.source,
            status="Queued",
            resolved_repo_identity=rec.resolved_repo_identity,
        )
        await self._patch_evidence(superseded, {"resubmitted_as": created.action_id})
        return await self.get(rec.action_id), created

    async def _acknowledge(self, rec: ActionRecord, user: str) -> ActionRecord:
        """Close a recovery card the human has read.

        Clearing ``interrupted_at`` happens **only** here and only for the interruption finding (C28,
        review P17): PRD §11.2 requires a human takeover after a mid-stage interruption, so a Run that
        cleared the flag would be the one-click path out of ``Interrupted`` that was removed. Every
        other recovery finding is disk-derived and comes straight back on the next scan.
        """
        findings = _finding_codes(rec.evidence)
        if "interrupted_session" in findings or rec.card_meta.get("reason") == "interrupted_session":
            await self._sessions.set_interrupted(rec.repo_id, rec.space, rec.intent_dir, None)
        return await self._cancel_if_open(rec, "acknowledged", {"acknowledged_by": user})

    async def _retry_now(self, rec: ActionRecord, user: str) -> tuple[ActionRecord, ActionRecord | None]:
        """Reset the breaker and start the failed work again (PRD §11.4).

        A ``failure`` card points at the action that failed, so the retry belongs to that action; a
        ``circuit_breaker`` card points at a fingerprint, so resetting it and letting the user press
        Run is the whole of it. Neither path can raise ``breaker_open`` — Retry is the way out of an
        open breaker, not another thing it blocks.
        """
        await self._reset_breakers(rec.repo_id, rec.intent_key)
        created: ActionRecord | None = None
        related = rec.card_meta.get("related_action_id")
        if isinstance(related, str) and related:
            try:
                created = await self.retry(related, user=user)
            except StudioError as exc:
                if exc.code not in ("retry_not_allowed", "action_not_found"):
                    raise
        updated = await self._cancel_if_open(
            rec, "retry_now", {"retried_by": user, **({"created": created.action_id} if created else {})}
        )
        return updated, created

    async def _pick_intent(
        self, rec: ActionRecord, payload: Mapping[str, Any], user: str
    ) -> ActionRecord:
        """Point the repository cursor at an intent, under the admin lease, with no prompt.

        This is the one studio-only decision that runs an engine verb. It is *not* a prompt on purpose:
        ``/aidlc intent <name>`` typed into the conversation would mint a ``HUMAN_TURN`` for a
        navigation step, and a human turn is the scarcest thing in this design (C22/R01).
        """
        intent_dir = str(payload.get("intent_dir") or "")
        if not C.INTENT_DIR_RE.match(intent_dir):
            raise StudioError(
                "bad_param", "pick_intent needs a valid intent_dir", details={"intent_dir": intent_dir}
            )
        repo = await self._repo(rec.repo_id)
        registry_uuid = None
        rows = await asyncio.to_thread(
            self._reader.registry, Path(repo.canonical_path), rec.space
        )
        for row in rows:
            if row.dir_name == intent_dir:
                registry_uuid = row.uuid
                break
        grant = await self._scheduler.acquire_admin(
            repo, operation_type="cursor_switch", transaction_id=self._ids.new("tx")
        )
        try:
            readback = await asyncio.to_thread(
                self._engine.switch_cursor_sync,
                Path(repo.canonical_path),
                repo.engine_dir or C.STUDIO_HARNESS_DIR,
                rec.space,
                intent_dir,
                registry_uuid,
                lease_generation=grant.generation,
                repo_id=repo.repo_id,
            )
            await self._engine.drain_activity()
        finally:
            await self._scheduler.release(grant)
        if not readback.ok:
            raise StudioError(
                "cursor_mismatch",
                "the cursor did not move to that intent",
                details={"mismatch": list(readback.mismatch), "readback": readback.to_json()},
            )
        return await self._cancel_if_open(
            rec, "cursor_switched", {"intent_dir": intent_dir, "by": user, "readback": readback.to_json()}
        )

    async def _cancel_if_open(
        self, rec: ActionRecord, reason: str, evidence: Mapping[str, Any]
    ) -> ActionRecord:
        """Cancel a card that is still open; only record evidence on one that is already settled."""
        if rec.status in SUBMITTABLE_STATUSES:
            return await self._transition(rec, "Cancelled", reason=reason, evidence=dict(evidence))
        return await self._patch_evidence(rec, {"resolution_reason": reason, **dict(evidence)})

    # ---- transitions (the reconciler's entry point) ------------------------ #

    async def transition(
        self,
        action_id: str,
        to_status: str,
        *,
        expected_generation: int,
        reason: str,
        evidence: Mapping[str, Any],
    ) -> ActionRecord:
        """Move one action, validating against ``ALLOWED_TRANSITIONS`` and the caller's generation.

        The only status writer other than ``submit``'s lease-bound CAS. ``IllegalTransition`` for a
        pair outside the graph and ``StaleGeneration`` for a lost CAS are both refusals, never retried
        here: something else already moved this record, and the whole point of the record is that two
        writers cannot both be right about a decision.
        """
        rec = await self.get(action_id)
        if rec.status_generation != int(expected_generation):
            raise StaleGeneration(
                "the action changed under this transition",
                details={
                    "expected": int(expected_generation),
                    "actual": rec.status_generation,
                    "action_id": action_id,
                },
            )
        return await self._transition(rec, to_status, reason=reason, evidence=dict(evidence))

    async def record_failure(
        self,
        rec: ActionRecord,
        *,
        fingerprint: str,
        fingerprint_class: str,
        summary: str,
        evidence: Mapping[str, Any],
    ) -> tuple[ActionRecord, bool]:
        """Fail an action and count it against the intent's circuit breaker (§11.4).

        Transient classes get three attempts because the third identical transport error is no longer
        transient; a deterministic class opens on the first, because running it again would fail the
        same way and spend the user's model budget proving it. Opening produces exactly one card and
        exactly one notification — a breaker that shouted on every retry would train people to ignore
        it, which is the opposite of a circuit breaker.
        """
        if rec.status not in ("Processing", "ReconciliationRequired"):
            raise IllegalTransition(
                "only an in-flight action can be failed",
                details={"from": rec.status, "to": "Failed", "action_id": rec.action_id},
            )
        # The strike is computed first and *written* last, with the status CAS between them. Written
        # first, it outlived a lost CAS: two actors can classify the same action in one pass (the tick and
        # a "Reconcile from disk" click), the loser's `_transition` raises `StaleGeneration`, and the
        # phantom strike it had already banked stayed on disk — enough of them, or one deterministic
        # class, and the breaker opens against an intent nothing was ever wrong with. Only a failure that
        # actually committed may leave a mark.
        strike = await self._peek_breaker(
            rec.repo_id, rec.intent_key, fingerprint, fingerprint_class, summary, evidence
        )
        failure = {
            "fingerprint": fingerprint,
            "fingerprint_class": fingerprint_class,
            "summary_key": summary,
            "count": strike["count"],
            "breaker_open": bool(strike["should_open"] or strike["already_open"]),
        }
        updated = await self._transition(
            rec,
            "Failed",
            reason=summary,
            evidence={"failure": failure, **dict(evidence)},
            values={"retry_fingerprint": fingerprint},
        )
        opened = await self._write_breaker(strike)
        await self._release_lease(updated)
        if opened:
            await self._open_breaker_card(updated, failure)
        return updated, opened

    # ---- the card (§2.11) -------------------------------------------------- #

    async def card(self, rec: ActionRecord, snap: "IntentSnapshot | None") -> dict[str, Any]:
        """The queue/detail object for one action (§2.11).

        Live evidence is joined **only** while nothing has been dispatched (``Draft``/``Queued``/
        ``NotDelivered``). That split is the point of the durable record: an open card must show what
        is true now (and carry a ``captured`` set fresh enough to submit against), while a card whose
        decision is on the wire must keep showing what the human actually decided against — including
        the evidence hash they have to echo back to resubmit.
        """
        repo_row = await asyncio.to_thread(self._storage.get, _REPOS, rec.repo_id)
        repo_row = repo_row or {}
        meta = rec.card_meta
        open_card = rec.status in ("Draft", "Queued", "NotDelivered")

        captured = rec.captured
        evidence = {key: rec.evidence.get(key) for key in EVIDENCE_SNAPSHOT_KEYS}
        stage_ref = meta.get("stage_ref")
        intent = {
            "intent_dir": rec.intent_dir,
            "intent_key": rec.intent_key,
            "slug": rec.intent_dir,
            "uuid": rec.intent_uuid,
            "title": None,
        }
        if snap is not None:
            # `summarize` is the public path to the slug/title rules (registry row first, placeholder
            # project names suppressed); re-deriving them here would give the card a different name for
            # the same intent than the rail shows.
            summary = self._projection.summarize(snap)
            intent = {
                "intent_dir": rec.intent_dir,
                "intent_key": rec.intent_key,
                "slug": summary.slug,
                "uuid": summary.uuid or rec.intent_uuid,
                "title": summary.title,
            }
            if open_card:
                captured = Captured.from_json(self._projection.captured(snap, rec.stage))
                # Re-read rather than reuse the record's slice: `sendBlockedFor` in the UI disables the
                # send exactly when `evidence.session.slot_key` is absent, so a stale null here is a
                # refusal the backend no longer agrees with.
                binding = await self._sessions.get(rec.repo_id, rec.space, rec.intent_dir)
                # The slot goes in too, or the refresh would *remove* facts the seed had: without it
                # `_session_json` reports every conversation idle with no busy reasons.
                slot = self._host.slot(binding.slot_key) if binding.slot_key else None
                # `stage=rec.stage`, never the snapshot's current stage (A20). An intent can hold more
                # than one `[?]` row, and this refresh overwrites `artifacts`, `review`,
                # `acceptance_criteria` and `questions`: defaulting to `snap.stage` showed the person
                # about to approve the *other* boundary the current stage's file list and verdict, under
                # the right stage's name, with `captured` and `stage_ref` (below) still correctly scoped
                # so nothing refused it. That is `evidence-1`, and it was only ever fixed in the seeds.
                live = self._projection.evidence(
                    snap, findings=(), binding=binding, slot=slot, stage=rec.stage
                ).to_json()
                for key in LIVE_REFRESHED_EVIDENCE_KEYS:
                    evidence[key] = live.get(key)
                stage_ref = self._projection.stage_ref(snap, rec.stage) or stage_ref

        evidence["presence_baseline"] = _json_of(rec.presence_baseline)
        evidence["presence"] = rec.evidence.get("presence")
        evidence["cursor_readback"] = _json_of(rec.cursor_readback)
        # Every §2.11 key is present with the right kind of empty value: the UI type has no optional
        # members, and a missing `audit` object would crash the evidence drawer rather than show "none".
        if not isinstance(evidence.get("audit"), Mapping):
            evidence["audit"] = {}
        if not isinstance(evidence.get("acceptance_criteria"), list):
            evidence["acceptance_criteria"] = list(evidence.get("acceptance_criteria") or [])
        evidence["findings"] = list(evidence.get("findings") or [])
        evidence["artifacts"] = list(evidence.get("artifacts") or [])

        queue_type = rec.queue_type
        severity = str(meta.get("severity") or SEED_SEVERITY.get(rec.type, "info"))
        if queue_type != rec.type:
            severity = SEED_SEVERITY.get(queue_type, severity)
        decisions = self._offered_decisions(rec, meta)
        if open_card and rec.type in ("run", "resume") and snap is not None and run_unavailable_reason(
            snap, stage=rec.stage, unit=rec.unit,
        ):
            decisions = []
        availability = str(repo_row.get("availability") or "unavailable")
        if availability != "available":
            # A saved gate is still evidence, but cannot authorize a prompt against an unreadable repo.
            decisions = [decision for decision in decisions if decision["lane"] != "human_lane"]
        delivery = self._delivery_json(rec)
        question_view = evidence.get("questions") or {}
        checkpoint = question_view.get("pending_checkpoint") if question_view.get("pending_count") == 0 else None
        checkpoint = checkpoint if rec.type == "question" and checkpoint in ("summary_confirmation", "plan_approval") else None
        card: dict[str, Any] = {
            "action_id": rec.action_id,
            "type": rec.type,
            "queue_type": queue_type,
            "status": rec.status,
            "status_generation": rec.status_generation,
            "source": rec.source,
            "risk_class": rec.risk_class,
            "severity": severity,
            "priority_group": priority_group_for(queue_type),
            "repo": {
                "repo_id": rec.repo_id,
                "label": str(repo_row.get("label") or ""),
                "canonical_path": str(repo_row.get("canonical_path") or ""),
                "availability": availability,
                "availability_detail": repo_row.get("availability_detail"),
                "archived": bool(repo_row.get("archived")),
            },
            "space": rec.space,
            "intent": intent,
            "stage": dict(stage_ref) if isinstance(stage_ref, Mapping) else None,
            "headline": {
                "key": f"action.question.{checkpoint}.headline" if checkpoint else f"action.{rec.type}.headline",
                "params": dict(meta.get("headline_params") or {}),
            },
            "consequence": (
                {"key": f"action.question.consequence.{checkpoint}",
                 "params": dict(meta.get("headline_params") or {})}
                if checkpoint else
                {
                    "key": f"action.{rec.type}.consequence.{meta['consequence_variant']}",
                    "params": dict(meta.get("headline_params") or {}),
                }
                if meta.get("consequence_variant")
                else None
            ),
            "waiting_since": rec.waiting_since,
            "created_at": rec.created_at,
            "updated_at": rec.updated_at,
            "primary": (
                {"decision": decisions[0]["decision"], "label_key": decisions[0]["label_key"]}
                if decisions
                else None
            ),
            "decisions": decisions,
            "captured": captured.to_json(),
            "evidence": evidence,
            "delivery": delivery,
            "resolution": self._resolution_json(rec),
            "failure": _failure_json(rec),
            "install": _install_json(rec),
            "budget": None,
            "advisor": await self._advisor_json(rec),
            "deep_link": f"{C.DEEP_LINK_BASE}?view=actions&action={rec.action_id}",
            "dedupe_key": rec.dedupe_key,
            "human_text_present": bool(rec.human_text),
        }
        card["acknowledged_evidence_sha256"] = _digest(
            {
                "delivery": delivery,
                "audit": evidence.get("audit"),
                "presence": evidence.get("presence"),
            }
        )
        return card

    def _offered_decisions(self, rec: ActionRecord, meta: Mapping[str, Any]) -> list[dict[str, Any]]:
        """What this card may actually do right now.

        An uncertain delivery is re-typed in the queue, so it must also offer the uncertain
        vocabulary — reconcile, mark undelivered, resubmit — and not the gate buttons it was born
        with. A card in flight offers nothing: every decision it could name would be refused, and a
        button that always fails is worse than no button.
        """
        if rec.status in UNCERTAIN_STATUSES:
            names: Sequence[str] = DECISIONS["delivery_uncertain"]
        elif rec.status in ("Draft", "Queued", "NotDelivered"):
            stored = meta.get("decisions")
            names = [str(d) for d in stored] if isinstance(stored, Sequence) and not isinstance(
                stored, (str, bytes)
            ) else list(DECISIONS.get(rec.type, ()))
        elif rec.status == "Failed":
            names = [d for d in DECISIONS.get(rec.type, ()) if d in ("retry_now", "keep_paused")]
        else:
            names = ()
        out: list[dict[str, Any]] = []
        for name in names:
            if name not in DECISIONS.get(rec.type, ()) and rec.status not in UNCERTAIN_STATUSES:
                continue
            if name in ("mark_not_delivered", "resubmit") and rec.evidence.get("delivery_confirmed"):
                # A missing workflow receipt does not undo the confirmed message delivery.
                continue
            if name == "mark_not_delivered" and rec.status != "ReconciliationRequired":
                # `_mark_not_delivered` refuses from every other status (R07), and a recovery card is
                # always `Queued` — its decisions are all studio-only, so it never reaches an uncertain
                # status at all. Offered there, this danger-styled button could only answer 409.
                continue
            if name == "rebind_session":
                # Nothing can satisfy it: `resolve` demands a `slot_key` and no client sends one (the
                # UI's `ResolvePayload` carries the decision alone), so every click answered 400
                # `bad_body`. Rebinding lives on the intent's session panel, which can actually pick a
                # target conversation (§2.13 takeover) — the vocabulary stays, the dead button goes.
                continue
            out.append(
                {
                    "decision": name,
                    "label_key": f"decision.{name}.label",
                    "lane": lane_for(name),
                    "wire_text_template": _template_for(name),
                    "requires": list(DECISION_REQUIRES.get(name, ())),
                }
            )
        return out

    def _delivery_json(self, rec: ActionRecord) -> dict[str, Any]:
        evidence = rec.evidence
        host: dict[str, Any] | None = None
        if rec.delivery_id and rec.slot_key:
            if rec.type == "force_stop":
                host = HostCall("POST", f"/api/chat/slots/{rec.slot_key}/stop", {}).to_json()
            elif rec.wire_text is not None:
                host = HostCall(
                    "POST",
                    C.HOST_CHAT_SEND_PATH,
                    {
                        "message": rec.wire_text,
                        "slot": rec.slot_key,
                        "agent": C.AIDLC_AGENT_NAME,
                        "meta": {
                            "studio_action_id": rec.action_id,
                            "studio_delivery_id": rec.delivery_id,
                        },
                    },
                ).to_json()
        transcript = evidence.get("transcript_row")
        return {
            "lane": rec.lane,
            "delivery_id": rec.delivery_id,
            "slot_key": rec.slot_key,
            "session_key": rec.session_key,
            "wire_text": rec.wire_text,
            "host": host,
            "delivering_at": rec.delivering_at,
            "delivered_at": rec.delivered_at,
            "queued_at": evidence.get("queued_at"),
            "deadline_at": rec.deadline_at,
            "receipt": evidence.get("receipt") if isinstance(evidence.get("receipt"), Mapping) else None,
            "outcome": evidence.get("outcome"),
            "delivery_confirmed": bool(evidence.get("delivery_confirmed")),
            "boot_id_unchanged": evidence.get("boot_id_unchanged"),
            "transcript_row": dict(transcript) if isinstance(transcript, Mapping) else None,
            "slot_ran_since": evidence.get("slot_ran_since"),
            "disk_baseline_unchanged": evidence.get("disk_baseline_unchanged"),
        }

    def _resolution_json(self, rec: ActionRecord) -> dict[str, Any]:
        stored = rec.resolution if isinstance(rec.resolution, Mapping) else {}
        kind = _RESOLUTION_KIND.get(rec.status)
        return {
            "kind": stored.get("kind") or kind,
            "resolved_at": rec.resolved_at,
            "evidence": stored.get("evidence") if isinstance(stored.get("evidence"), Mapping) else None,
            "reason": stored.get("reason") or rec.evidence.get("resolution_reason"),
        }

    async def _advisor_json(self, rec: ActionRecord) -> dict[str, Any]:
        rows = await asyncio.to_thread(
            self._storage.select,
            _DRAFTS,
            {"action_id": rec.action_id},
            order_by="created_at DESC, draft_id DESC",
            limit=1,
        )
        if not rows:
            return {"latest_draft_id": None, "status": None}
        return {"latest_draft_id": rows[0].get("draft_id"), "status": rows[0].get("status")}

    # ---- internals -------------------------------------------------------- #

    async def _repo(self, repo_id: str) -> RepoRecord:
        row = await asyncio.to_thread(self._storage.get, _REPOS, repo_id)
        if row is None:
            raise StudioError("repo_not_found", "no such repository", details={"repo_id": repo_id})
        return RepoRecord.from_row(row)

    @staticmethod
    def _raise_blocking_findings(blocking: Sequence[Any], repo_id: str) -> None:
        # Runtime ownership is not a contradiction between AI-DLC files. Keep the holder's lease;
        # expose its identity so the UI can direct the user to the operation already in progress.
        disk_findings = [finding for finding in blocking if finding.code != "lease_conflict"]
        if disk_findings:
            raise StudioError("state_inconsistent", "the intent's files contradict each other",
                              details={"findings": [finding.to_json() for finding in disk_findings]})
        if blocking:
            raise StudioError("repo_busy", "another operation holds this repository",
                              details={"repo_id": repo_id, "owner": dict(blocking[0].params)})

    async def _snapshot(self, repo: RepoRecord, space: str, intent_dir: str) -> "IntentSnapshot":
        """One coherent read of the intent, off the event loop.

        Uses the scan pool when one was injected (the reconciler's 2-worker pool, review R11) and the
        process default executor otherwise: a single-intent snapshot is a short, user-initiated read,
        and blocking the loop on it is the one thing that is never acceptable.
        """
        if self._scan_pool is not None:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self._scan_pool, self._projection.snapshot, repo, space, intent_dir
            )
        return await asyncio.to_thread(self._projection.snapshot, repo, space, intent_dir)

    async def _findings(self, repo: RepoRecord, snap: "IntentSnapshot", binding: Any) -> list[Any]:
        """The intent's consistency findings, assembled with the facts the engine cannot see itself."""
        live = await self.list_live(
            repo_id=repo.repo_id, space=snap.space, intent_dir=snap.intent_dir, include_revision=True
        )
        leases = await asyncio.to_thread(self._storage.lease_list)
        slot_key = getattr(binding, "slot_key", None)
        facts = RepoFacts(
            now=self._clock.iso(),
            repo=repo,
            install=None,  # type: ignore[arg-type]
            binding=binding,
            slot=self._host.slot(slot_key) if slot_key else None,
            live_actions=live,
            leases=leases,
            layout=snap.layout,
        )
        return await asyncio.to_thread(self._consistency.evaluate_intent, snap, facts)

    def _assert_dispatchable(
        self, binding: Any, *, decision: str, intent_key: str, repo_id: str
    ) -> None:
        """Pause and archive block **every** human-lane dispatch, not only Run (C29, review P11).

        FR-RUN-003 says pause "prevents future turns"; a paused intent whose gate could still be
        approved would take a turn the user explicitly stopped. Archived is the same argument about a
        record the user has put away.
        """
        if getattr(binding, "archived", False):
            raise StudioError(
                "intent_archived",
                "this intent is archived in Studio",
                details={"intent_key": intent_key, "repo_id": repo_id, "decision": decision},
            )
        if getattr(binding, "paused", False):
            raise StudioError(
                "intent_paused",
                "this intent is paused; resume it before sending anything",
                details={"intent_key": intent_key, "repo_id": repo_id, "decision": decision},
            )

    def _turn_timeout(self) -> int:
        try:
            return int(self._host.turn_timeout_secs())
        except Exception:  # the host owns this value; a missing accessor must not fail a delivery
            return int(C.PROCESSING_DEADLINE_CAP_SECS)

    async def _live_for_key(self, key: str | None) -> ActionRecord | None:
        if not key:
            return None
        rows = await asyncio.to_thread(
            self._storage.select,
            _ACTIONS,
            {"dedupe_key": key, "status": sorted(DEDUPE_LIVE_STATUSES)},
        )
        if not rows:
            return None
        return ActionRecord.from_row(rows[0])

    async def _newest_for_key(self, key: str | None) -> ActionRecord | None:
        if not key:
            return None
        rows = await asyncio.to_thread(
            self._storage.select,
            _ACTIONS,
            {"dedupe_key": key},
            order_by="created_at DESC, action_id DESC",
            limit=1,
        )
        return ActionRecord.from_row(rows[0]) if rows else None

    async def _insert(
        self,
        *,
        action_type: str,
        risk_class: str,
        repo_id: str,
        space: str,
        intent_dir: str,
        intent_uuid: str | None,
        stage: str | None,
        unit: str | None,
        captured: Mapping[str, Any],
        evidence: Mapping[str, Any],
        dedupe_key: str | None,
        waiting_since: str,
        source: str,
        status: str,
        resolved_repo_identity: str | None = None,
    ) -> ActionRecord:
        now = self._clock.iso()
        action_id = self._ids.new("a")
        identity = resolved_repo_identity
        if identity is None:
            repo_row = await asyncio.to_thread(self._storage.get, _REPOS, repo_id)
            identity = (repo_row or {}).get("resolved_identity")
        blob = dict(evidence)
        meta = blob.get(CARD_META_KEY)
        meta = dict(meta) if isinstance(meta, Mapping) else {}
        meta.setdefault("captured_at", captured.get("captured_at") or now)
        meta.setdefault("stable", bool(captured.get("stable", True)))
        blob[CARD_META_KEY] = meta
        row = {
            "action_id": action_id,
            "type": action_type,
            "risk_class": risk_class,
            "repo_id": repo_id,
            "resolved_repo_identity": identity,
            "intent_uuid": intent_uuid,
            "intent_dir": intent_dir,
            "space": space,
            "stage": stage,
            "unit": unit,
            "captured_state_hash": captured.get("state_hash"),
            "boundary_token": captured.get("boundary_token"),
            "question_digest": captured.get("question_digest"),
            "stage_attempt": captured.get("stage_attempt"),
            "evidence_digest": captured.get("evidence_digest"),
            "is_active_captured": _bool_int(captured.get("is_active")),
            "status": status,
            "status_generation": 0,
            "created_at": now,
            "updated_at": now,
            "evidence_json": blob,
            "source": source,
            "waiting_since": waiting_since or now,
            "dedupe_key": dedupe_key,
        }
        await asyncio.to_thread(self._storage.insert, _ACTIONS, row)
        rec = ActionRecord.from_row(row)
        await self._events.publish(
            "action.created",
            {
                "action_id": action_id,
                "repo_id": repo_id,
                "intent_key": rec.intent_key,
                "type": action_type,
                "status": status,
                "status_generation": 0,
                "from_status": None,
                "reason": "created",
            },
        )
        await self._activity.record(
            kind="action.created",
            severity="info",
            repo_id=repo_id,
            space=space,
            intent_dir=intent_dir,
            stage=stage,
            action_id=action_id,
            params={"type": action_type, "status": status, "dedupe_key": dedupe_key},
        )
        return rec

    async def _transition(
        self,
        rec: ActionRecord,
        to_status: str,
        *,
        reason: str,
        evidence: Mapping[str, Any],
        values: Mapping[str, Any] | None = None,
        activity_kind: str | None = None,
    ) -> ActionRecord:
        """Validate, CAS, and leave a trail. The single status writer outside ``submit``."""
        if to_status not in ALLOWED_TRANSITIONS.get(rec.status, frozenset()):
            raise IllegalTransition(
                f"{rec.status} → {to_status} is not a legal action transition",
                details={
                    "from": rec.status,
                    "to": to_status,
                    "action_id": rec.action_id,
                    "allowed": sorted(ALLOWED_TRANSITIONS.get(rec.status, frozenset())),
                },
            )
        now = self._clock.iso()
        blob = dict(rec.evidence)
        blob.update(dict(evidence))
        payload: dict[str, Any] = {"status": to_status, "evidence_json": blob}
        payload.update(dict(values or {}))
        if to_status in TERMINAL_STATUSES or to_status == "Failed":
            payload.setdefault("resolved_at", now)
            payload.setdefault("deadline_at", None)
            payload.setdefault(
                "resolution_json",
                {
                    "kind": _RESOLUTION_KIND.get(to_status),
                    "resolved_at": now,
                    "evidence": dict(evidence) or None,
                    "reason": reason,
                },
            )
        if to_status == "Queued":
            payload.setdefault("deadline_at", None)
        generation = await asyncio.to_thread(
            self._storage.cas_update, _ACTIONS, rec.action_id, rec.status_generation, payload
        )
        after = await self.get(rec.action_id)
        await self._record_transition(
            rec, after, generation, reason=reason, evidence=evidence, activity_kind=activity_kind
        )
        return after

    async def _record_transition(
        self,
        before: ActionRecord,
        after: ActionRecord,
        generation: int,
        *,
        reason: str,
        evidence: Mapping[str, Any],
        activity_kind: str | None = None,
        human_text: str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        """The audit half of a transition: the row, the event, the Activity line.

        Written after the CAS, never before: a trail that claims a status the store did not commit is
        worse than a missing line, because the trail is what a support conversation is based on.
        """
        await asyncio.to_thread(
            self._storage.insert,
            _TRANSITIONS,
            {
                "action_id": after.action_id,
                "from_status": before.status,
                "to_status": after.status,
                "generation": int(generation),
                "at": self._clock.iso(),
                "reason": reason,
                "evidence_json": dict(evidence),
            },
        )
        await self._events.publish(
            "action.updated",
            {
                "action_id": after.action_id,
                "repo_id": after.repo_id,
                "intent_key": after.intent_key,
                "type": after.type,
                "status": after.status,
                "status_generation": after.status_generation,
                "from_status": before.status,
                "reason": reason,
            },
        )
        kind = activity_kind or _ACTIVITY_KIND_BY_STATUS.get(after.status, "action.updated")
        row_params = dict(params or {"status": after.status, "reason": reason, "type": after.type})
        await self._activity.record(
            kind=kind,
            severity=_ACTIVITY_SEVERITY_BY_STATUS.get(after.status, "info"),
            repo_id=after.repo_id,
            space=after.space,
            intent_dir=after.intent_dir,
            stage=after.stage,
            action_id=after.action_id,
            session_key=after.session_key,
            params=row_params,
            human_text=human_text if kind == "action.submitted" else None,
        )

    def _assert_audit_question_current(self, rec: ActionRecord, snap: "IntentSnapshot") -> None:
        questions = snap.questions
        live = Captured.from_json(self._projection.captured(snap, rec.stage))
        if (
            not snap.stable or not snap.audit.complete
            or snap.stage != rec.stage or snap.unit != rec.unit
            or questions is None or not questions.origin
            or questions.origin != (rec.evidence.get("questions") or {}).get("origin")
            or snap.audit_question_attempt != (questions.origin.get("attempt_generation"), True)
            or live.differences(rec.captured)
        ):
            raise StudioError(
                "action_stale", "the captured audit question is no longer provably pending",
                details={"fields": ["question_digest"]},
            )

    async def _patch_evidence(self, rec: ActionRecord, patch: Mapping[str, Any]) -> ActionRecord:
        """Update ``evidence_json`` without touching the status.

        ``actions`` has a generation column, so even a non-status write goes through the CAS — that is
        what keeps two writers from clobbering each other's evidence. A lost CAS is not an error here:
        the other writer's blob is at least as new as ours.
        """
        blob = dict(rec.evidence)
        blob.update(dict(patch))
        try:
            await asyncio.to_thread(
                self._storage.cas_update,
                _ACTIONS,
                rec.action_id,
                rec.status_generation,
                {"evidence_json": blob},
            )
        except StaleGeneration:
            logger.debug("aidlc-studio: evidence patch for %s lost the CAS", rec.action_id)
        return await self.get(rec.action_id)

    async def _release_lease(self, rec: ActionRecord) -> None:
        """Give back the execution lease a settled action was holding.

        Reconstructed from the row rather than kept in memory: the process that acquired it may be
        gone (that is the whole reason the generation is on the row), and releasing with a stale
        generation is a no-op Storage refuses rather than a write over the new owner.
        """
        if rec.lease_generation is None or not rec.resolved_repo_identity:
            return
        grant = LeaseGrant(
            kind="execution",
            resolved_repo_identity=rec.resolved_repo_identity,
            generation=int(rec.lease_generation),
            acquired_at=rec.delivering_at or rec.created_at,
            repo_id=rec.repo_id,
        )
        await self._scheduler.release(grant)

    # ---- breakers --------------------------------------------------------- #

    async def _breaker_rows(self, repo_id: str, intent_key: str) -> list[dict[str, Any]]:
        prefix = f"{repo_id}:{intent_key}:"
        rows = await asyncio.to_thread(self._storage.select, _BREAKERS)
        return [row for row in rows if str(row.get("key") or "").startswith(prefix)]

    async def _breaker_open(self, repo_id: str, intent_key: str) -> bool:
        return any(row.get("opened_at") for row in await self._breaker_rows(repo_id, intent_key))

    async def _peek_breaker(
        self,
        repo_id: str,
        intent_key: str,
        fingerprint: str,
        fingerprint_class: str,
        summary: str,
        evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        """What the strike WOULD be. Reads only — see `record_failure` for why nothing is written yet."""
        key = f"{repo_id}:{intent_key}:{fingerprint}"
        row = await asyncio.to_thread(self._storage.get, _BREAKERS, key)
        count = int((row or {}).get("count") or 0) + 1
        already_open = bool((row or {}).get("opened_at"))
        transient = fingerprint_class in C.TRANSIENT_FINGERPRINT_CLASSES
        should_open = count >= C.BREAKER_TRANSIENT_THRESHOLD if transient else True
        return {
            "key": key,
            "exists": row is not None,
            "count": count,
            "already_open": already_open,
            "should_open": bool(should_open),
            "values": {
                "count": count,
                "opened_at": (row or {}).get("opened_at")
                or (self._clock.iso() if should_open else None),
                "reason": summary,
                "fingerprint_class": fingerprint_class,
                "last_error_json": dict(evidence),
            },
        }

    async def _write_breaker(self, strike: Mapping[str, Any]) -> bool:
        """Bank a peeked strike. Returns True when this one opened the breaker."""
        key = str(strike["key"])
        if strike["exists"]:
            await asyncio.to_thread(self._storage.update, _BREAKERS, key, dict(strike["values"]))
        else:
            await asyncio.to_thread(
                self._storage.insert, _BREAKERS, {"key": key, **dict(strike["values"])}
            )
        return bool(strike["should_open"] and not strike["already_open"])

    async def _open_breaker_card(self, rec: ActionRecord, failure: Mapping[str, Any]) -> None:
        """One card and one notification per opening (§11.4)."""
        key = dedupe_key(
            "circuit_breaker", rec.repo_id, rec.intent_key, rec.stage, str(failure.get("fingerprint"))
        )
        live = await self._live_for_key(key)
        if live is not None:
            return
        evidence = {key_: rec.evidence.get(key_) for key_ in EVIDENCE_SNAPSHOT_KEYS}
        evidence["breaker"] = dict(failure)
        evidence[CARD_META_KEY] = {
            "severity": SEED_SEVERITY["circuit_breaker"],
            "decisions": list(DECISIONS["circuit_breaker"]),
            "headline_params": {
                "stage": rec.stage,
                "count": failure.get("count"),
                "fingerprint_class": failure.get("fingerprint_class"),
            },
            "consequence_variant": None,
            "stage_ref": rec.card_meta.get("stage_ref"),
            "reason": failure.get("fingerprint"),
            "related_action_id": rec.action_id,
            "captured_at": rec.captured.captured_at,
            "stable": rec.captured.stable,
        }
        try:
            card_rec = await self._insert(
                action_type="circuit_breaker",
                risk_class="studio_only",
                repo_id=rec.repo_id,
                space=rec.space,
                intent_dir=rec.intent_dir,
                intent_uuid=rec.intent_uuid,
                stage=rec.stage,
                unit=rec.unit,
                captured=rec.captured.to_json(),
                evidence=evidence,
                dedupe_key=key,
                waiting_since=self._clock.iso(),
                source="studio",
                status="Queued",
                resolved_repo_identity=rec.resolved_repo_identity,
            )
        except sqlite3.IntegrityError:
            return
        await self._activity.record(
            kind="breaker.opened",
            severity="attention",
            repo_id=rec.repo_id,
            space=rec.space,
            intent_dir=rec.intent_dir,
            stage=rec.stage,
            action_id=card_rec.action_id,
            params={
                "fingerprint": failure.get("fingerprint"),
                "fingerprint_class": failure.get("fingerprint_class"),
                "count": failure.get("count"),
            },
        )
        notify = getattr(self._notifications, "notify_action", None)
        if notify is None:
            return
        try:
            await notify(await self.card(card_rec, None))
        except StudioError as exc:
            logger.info("aidlc-studio: breaker notification unavailable (%s)", exc.code)

    async def _reset_breakers(self, repo_id: str, intent_key: str) -> None:
        for row in await self._breaker_rows(repo_id, intent_key):
            if not row.get("opened_at") and not row.get("count"):
                continue
            await asyncio.to_thread(
                self._storage.update,
                _BREAKERS,
                str(row["key"]),
                {"count": 0, "opened_at": None, "reason": "reset"},
            )
            await self._activity.record(
                kind="breaker.reset",
                severity="info",
                repo_id=repo_id,
                params={"key": row["key"], "intent_key": intent_key},
            )


# --------------------------------------------------------------------------- #
# the machine lane — refuses, always
# --------------------------------------------------------------------------- #


class MachineActionBroker:
    """The lane that does not exist in v1, written down so it cannot be added by accident.

    Every KiroCrew path that reaches kiro-cli fires ``userPromptSubmit`` and therefore mints a
    ``HUMAN_TURN`` (02 §5); ``_session/steer`` is unproven (S12). So an unattended continuation would
    either be impossible or — worse — would forge human presence in AI-DLC's own audit trail. This
    class refuses and records the refusal, and it owns the marker-movement check that turns such a
    forgery into an open breaker (FR-SES-010).
    """

    def __init__(self, actions: HumanActionBroker, activity: "ActivityProjector", clock: C.Clock) -> None:
        self._actions = actions
        self._activity = activity
        self._clock = clock

    async def dispatch(self, *args: Any, **kwargs: Any) -> NoReturn:
        """Refuse, loudly and on the record."""
        await self._activity.record(
            kind="machine_lane.refused",
            severity="attention",
            params={"reason": C.MACHINE_LANE_REASON, "args": len(args), "kwargs": sorted(kwargs)},
        )
        raise StudioError(
            "machine_lane_unavailable",
            "there is no machine lane in this version",
            details={"reason": C.MACHINE_LANE_REASON},
        )

    async def assert_no_human_marker_movement(
        self, before: Any, after: Any, *, repo_id: str, intent_key: str
    ) -> None:
        """Open the deterministic breaker if human-presence markers moved without a human (FR-SES-010).

        There is no machine dispatch in v1, so a ``.aidlc-human-turn`` stamp or a turn counter that
        advances while Studio believes nothing was sent means one of two things: something forged human
        presence, or Studio's model of what it sent is wrong. Both must stop the intent, and both are
        deterministic — retrying would repeat whatever did it.
        """
        moved: dict[str, Any] = {}
        before_mtime = getattr(before, "human_turn_mtime_ns", None)
        after_mtime = getattr(after, "human_turn_mtime_ns", None)
        if before_mtime != after_mtime:
            moved["human_turn_mtime_ns"] = {"before": before_mtime, "after": after_mtime}
        before_counter = getattr(before, "turn_counter", None)
        after_counter = getattr(after, "turn_counter", None)
        if before_counter != after_counter:
            moved["turn_counter"] = {"before": before_counter, "after": after_counter}
        if not moved:
            return
        await self._activity.record(
            kind="machine_lane.refused",
            severity="critical",
            repo_id=repo_id,
            params={"reason": "human_marker_movement", "moved": moved, "intent_key": intent_key},
        )
        candidate = None
        for rec in await self._actions.list_live(repo_id=repo_id, include_revision=True):
            if rec.intent_key == intent_key and rec.status in ("Processing", "ReconciliationRequired"):
                candidate = rec
                break
        fingerprint = "human_marker_movement:" + security.composite_token(
            repo_id, intent_key, _canonical_json(moved)
        )[:6]
        if candidate is not None:
            await self._actions.record_failure(
                candidate,
                fingerprint=fingerprint,
                fingerprint_class="human_marker_movement",
                summary="failure.human_marker_movement",
                evidence={"moved": moved},
            )
            return
        # No action to fail, so there is no CAS to gate this one on: peek and bank it straight away.
        strike = await self._actions._peek_breaker(  # noqa: SLF001 - same module, one implementation
            repo_id,
            intent_key,
            fingerprint,
            "human_marker_movement",
            "failure.human_marker_movement",
            {"moved": moved},
        )
        await self._actions._write_breaker(strike)  # noqa: SLF001 - same module


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def _seed_meta(seed: "CardSeed") -> dict[str, Any]:
    """The seed facts the ``actions`` schema has no column for (§2.11 presentation)."""
    return {
        "severity": seed.severity,
        "decisions": list(seed.decisions),
        "headline_params": dict(seed.headline_params),
        "consequence_variant": seed.consequence_variant,
        "stage_ref": dict(seed.stage_ref) if seed.stage_ref else None,
        "reason": seed.reason,
        "related_action_id": seed.related_action_id,
        "captured_at": seed.captured.get("captured_at"),
        "stable": bool(seed.captured.get("stable", True)),
    }


def _merge_findings(stored: Any, fresh: Any) -> list[Any]:
    """Fresh intent findings plus any the reconciler recorded on this action.

    The reconciler writes ``human_presence_anomaly`` / ``cursor_moved`` / ``wrong_slot`` into the same
    list, and ``ConsistencyEngine`` lifts them back out to block dispatch. Overwriting the list on the
    next scan would silently drop a blocking guard the user is looking at.
    """
    out = list(fresh) if isinstance(fresh, list) else []
    codes = {
        item.get("code")
        for item in out
        if isinstance(item, Mapping) and isinstance(item.get("code"), str)
    }
    for item in stored if isinstance(stored, list) else []:
        code = item.get("code") if isinstance(item, Mapping) else item
        if isinstance(code, str) and code in ACTION_STORED_FINDING_CODES and code not in codes:
            out.append(item)
            codes.add(code)
    return out


def _finding_codes(evidence: Mapping[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ("findings", "finding"):
        raw = evidence.get(key)
        for item in raw if isinstance(raw, (list, tuple)) else [raw]:
            if isinstance(item, str):
                out.add(item)
            elif isinstance(item, Mapping) and isinstance(item.get("code"), str):
                out.add(item["code"])
    return out


def _authoritative_4xx(http_status: Any, receipt: Mapping[str, Any] | None) -> bool:
    """Is this the host's own refusal?

    A 4xx **with the host's error body** is proof the message was never enqueued (the host validates
    the slot and the agent before it touches the queue). A bare status with no body, or a 5xx, or a
    network failure, proves nothing — the request may have been handled and the answer lost.
    """
    try:
        status = int(http_status)
    except (TypeError, ValueError):
        return False
    if not 400 <= status <= 499:
        return False
    body = receipt or {}
    if body.get("ok") is True:
        return False
    return bool(body.get("error") or body.get("code"))


def _template_for(decision: str) -> str | None:
    """The wire-text template a card shows for a decision (§2.11 ``wire_text_template``).

    ``confirm_summary`` and the two multi-form decisions have several pinned templates; the card shows
    the default branch's, and the confirmation panel substitutes the other once the user picks it.
    """
    if decision in WIRE_TEXT_TEMPLATES:
        return WIRE_TEXT_TEMPLATES[decision]
    if decision == "confirm_summary":
        return WIRE_TEXT_TEMPLATES["confirm_summary.looks_correct"]
    if decision == "answers":
        return WIRE_TEXT_TEMPLATES["answers.single"]
    if decision == "provide_input":
        return WIRE_TEXT_TEMPLATES["provide_input.free_text"]
    return None


def _failure_json(rec: ActionRecord) -> dict[str, Any] | None:
    failure = rec.evidence.get("failure")
    if isinstance(failure, Mapping):
        out = dict(failure)
    else:
        breaker = rec.evidence.get("breaker")
        if not isinstance(breaker, Mapping):
            return None
        key = str(breaker.get("key") or "")
        out = {
            "fingerprint": breaker.get("fingerprint") or key.rsplit(":", 1)[-1],
            "fingerprint_class": breaker.get("fingerprint_class"),
            "summary_key": breaker.get("reason") or breaker.get("summary_key"),
            "count": int(breaker.get("count") or 0),
            "breaker_open": bool(breaker.get("opened_at") or breaker.get("breaker_open")),
        }
    out.setdefault("fingerprint", rec.retry_fingerprint or "")
    out.setdefault("fingerprint_class", None)
    out.setdefault("summary_key", None)
    out.setdefault("count", 0)
    out.setdefault("breaker_open", False)
    out.setdefault("history", [])
    out.setdefault("stderr_excerpt", None)
    return out


def _install_json(rec: ActionRecord) -> dict[str, Any] | None:
    install = rec.evidence.get("install")
    if not isinstance(install, Mapping):
        return None
    return {
        "receipt_id": install.get("receipt_id"),
        "engine_version": install.get("engine_version"),
        "bundled_engine_version": C.BUNDLED_ENGINE_VERSION,
        "drift": list(install.get("drift") or []),
        "transaction_id": install.get("transaction_id"),
    }


def _bool_int(value: Any) -> int | None:
    """``bool`` as SQLite's integer, keeping ``None`` distinct from ``False``.

    ``is_active`` is part of the compare-and-submit set, where "the cursor did not name this intent"
    and "we never looked" must stay different answers (P26.3).
    """
    return None if value is None else (1 if value else 0)
