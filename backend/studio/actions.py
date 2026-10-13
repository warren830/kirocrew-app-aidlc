"""The decision lane: one durable record per human decision, and no way around it.

Everything else in Studio observes; this module acts on a user's behalf, so it is written as refusals with
one narrow exit. Five properties are load-bearing:

1. **Two-phase submission.** The backend commits a durable ``Delivering`` record; the *browser* sends through
   the host's public chat API and reports back. A private host symbol would silently stop reaching the model
   after a gateway upgrade, with the record saying it did (architecture §3.1/§7.0).
2. **At-most-once, never on the client's word.** A ``Delivering`` row is never replayed. ``NotDelivered`` is
   a proof: an authoritative 4xx (or a verified client preflight code) **and** four checks of our own — no
   matching row in any slot of the repository, the slot idle since ``delivering_at``, the presence baseline
   unchanged, the same ``boot_id`` (C24). Anything less is ``ReconciliationRequired`` and a human decides.
3. **Compare-and-submit over the whole captured set** (C30/P26.3), against both what the UI captured and
   what the row recorded, ``null`` differing from non-``null``.
4. **The cursor switch runs before every dispatch**, and its read-back digest must equal what the human
   decided against: the conductor resolves the intent from the repository-wide cursor (C22/P01).
5. **One lane per decision.** ``human_lane`` is a prompt, ``host_control`` (force stop) the host's stop
   endpoint, ``studio_only`` never leaves Studio. There is no unattended lane (S12 unproven).

Threading: async facade (§0.6); SQLite, snapshots and the cursor-switch child run in worker threads.
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
from .projection import (
    SEED_SEVERITY, SEVERITY_RANK, UNCERTAIN_STATUSES, dedupe_key, priority_group_for, queue_type_for,
    plan_session_stale, run_unavailable_reason,
)
from .repo_registry import RepoRecord
from .sessions import busy_reasons
from .wire import WireRecord

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .activity import ActivityProjector
    from .aidlc_reader import AidlcReader, QuestionsFile
    from .consistency import ConsistencyEngine
    from .engine import EngineRunner
    from .events import EventLog
    from .leases import RepoScheduler
    from .projection import CardSeed, IntentSnapshot, Projection
    from .sessions import HostBridge, SessionBinder
    from .settings import SettingsService
    from .storage import Storage

logger = logging.getLogger("aidlc-studio.actions")

__all__ = [
    "ABSENCE_PROOF", "ALLOWED_TRANSITIONS", "CLIENT_PREFLIGHT_CODES", "COMMAND_ACTION_TYPES", "Captured", "DECISIONS",
    "HOST_CONTROL_DECISIONS", "HUMAN_LANE_DECISIONS", "HostCall", "HumanActionBroker", "STUDIO_ONLY_DECISIONS",
    "ActionRecord", "PresenceBaseline", "SubmitReceipt", "WIRE_TEXT_TEMPLATES", "lane_for", "wire_text_for",
]


# ---- tables (§1.12): data, so the state machine and its tests read one source ---- #

#: Derived from ``constants`` rather than restated: two copies of a transition table drift, and the drift
#: shows up as a status the reconciler can reach but the broker refuses.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {s: frozenset(t) for s, t in C.ALLOWED_TRANSITIONS.items()}

DECISIONS, HUMAN_LANE_DECISIONS = C.DECISIONS, C.HUMAN_LANE_DECISIONS
HOST_CONTROL_DECISIONS, STUDIO_ONLY_DECISIONS = C.HOST_CONTROL_DECISIONS, C.STUDIO_ONLY_DECISIONS

#: The only receipt codes a browser may mint for ``not_delivered`` (R08): pre-send observations of the
#: host's slot listing, re-verified against ``HostBridge`` before they are believed.
CLIENT_PREFLIGHT_CODES = ("slot_missing", "slot_mismatch_preflight")

#: Types Studio creates because a human asked for a turn, as opposed to types derived from a boundary on
#: disk. The distinction decides who may cancel a card and what ``retire_stale`` sweeps.
COMMAND_ACTION_TYPES: frozenset[str] = frozenset({"run", "resume", "prepare_commit", "force_stop"})
#: Command types that use the two-phase prompt lane (``force_stop`` uses the stop endpoint).
PROMPT_COMMAND_TYPES: tuple[str, ...] = ("run", "resume", "prepare_commit")

#: Nothing has been handed to the host yet: a decision may be submitted, and live evidence joined.
SUBMITTABLE_STATUSES: frozenset[str] = frozenset({"Queued", "NotDelivered"})

#: The compare-and-submit set (C30), in the order failures are reported: coarsest fact first.
COMPARED_CAPTURED_FIELDS: tuple[str, ...] = (
    "state_hash", "boundary_token", "question_digest", "stage_attempt", "evidence_digest", "is_active",
)

#: ``decision -> wire text template``, keyed like ``docs/design/wire-text.json`` ``decisions`` (a test pins
#: them equal): a template that disagreed with ``wire_text_for`` would be a confirmation dialog that lies.
WIRE_TEXT_TEMPLATES: dict[str, str | None] = {
    "approve": C.WIRE_APPROVE,
    "request_changes": f"{C.WIRE_REQUEST_CHANGES_PREFIX}<feedback>",
    "accept_as_is": C.WIRE_ACCEPT_AS_IS,
    "approve_plan": C.WIRE_APPROVE_PLAN,
    "request_plan_changes": f"{C.WIRE_REQUEST_CHANGES_PREFIX}<feedback>",
    "confirm_summary.looks_correct": C.WIRE_LOOKS_CORRECT,
    "confirm_summary.request_changes": f"{C.WIRE_SUMMARY_REQUEST_CHANGES_PREFIX}<feedback>",
    "accept_assumptions": C.WIRE_ACCEPT_ASSUMPTIONS,
    "convert_assumptions": C.WIRE_CONVERT_ASSUMPTIONS,
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
#: The template a multi-form decision's card shows; the confirmation panel swaps in the other form.
_DEFAULT_TEMPLATE = {
    "confirm_summary": "confirm_summary.looks_correct", "answers": "answers.single",
    "provide_input": "provide_input.free_text",
}
#: Decisions whose wire text is a constant.
_FIXED_WIRE: dict[str, str] = {
    "approve": C.WIRE_APPROVE, "accept_as_is": C.WIRE_ACCEPT_AS_IS, "approve_plan": C.WIRE_APPROVE_PLAN,
    "accept_assumptions": C.WIRE_ACCEPT_ASSUMPTIONS, "convert_assumptions": C.WIRE_CONVERT_ASSUMPTIONS,
    "run": C.WIRE_RUN, "resume": C.WIRE_RESUME, "prepare_commit": C.WIRE_PREPARE_COMMIT,
}
#: ``decision -> (payload key, {value: (prefix, free-text field or None)})`` for the two sub-choices.
_CHOICE_WIRE: dict[str, tuple[str, dict[str, tuple[str, str | None]]]] = {
    "confirm_summary": ("choice", {
        "looks_correct": (C.WIRE_LOOKS_CORRECT, None),
        "request_changes": (C.WIRE_SUMMARY_REQUEST_CHANGES_PREFIX, "feedback"),
    }),
    "provide_input": ("kind", {"scope": (C.WIRE_SCOPE_PREFIX, "scope"), "free_text": ("", "text")}),
}

#: What the UI must collect before a decision can be sent (§2.11 ``DecisionSpec.requires``).
DECISION_REQUIRES: dict[str, tuple[str, ...]] = {
    "request_changes": ("feedback",), "request_plan_changes": ("feedback",), "provide_input": ("feedback",),
    "confirm_summary": ("confirm",), "answers": ("answers",), "resubmit": ("confirm",),
    "mark_not_delivered": ("confirm",),
}

#: Keys of ``EvidenceSnapshot.to_json()`` (§1.8). ``evidence_json`` also carries delivery and card
#: bookkeeping; ``card()`` splits them apart again so the §2.11 wire shape stays exact.
EVIDENCE_SNAPSHOT_KEYS: tuple[str, ...] = (
    "state", "audit", "directive", "artifacts", "review", "acceptance_criteria", "findings", "session",
    "questions", "git", "markers",
)
#: What a live snapshot may refresh on an undispatched card. ``findings`` is merged separately (stored codes
#: must survive) and ``git`` needs an observer the broker lacks. ``session`` is re-read because a card queued
#: before a conversation was bound used to say "nothing bound" forever, refusing what ``submit`` accepts.
LIVE_REFRESHED_EVIDENCE_KEYS: tuple[str, ...] = (
    "state", "audit", "directive", "artifacts", "review", "acceptance_criteria", "session", "questions",
    "markers",
)
#: The seed's presentation facts (severity, decisions, headline) and the two ``Captured`` fields with no
#: column live under one key, so bookkeeping can never collide with an ``EvidenceSnapshot`` key.
CARD_META_KEY = "card"

#: Activity kind and severity per target status (§1.17); severities band like the Action Center does.
_ACTIVITY_KIND_BY_STATUS: dict[str, str] = {
    "Queued": "action.updated", "Delivering": "action.submitted", "Delivered": "action.delivery",
    "NotDelivered": "action.delivery", "ReconciliationRequired": "action.delivery",
    "StateChanged": "action.resolved", "ResolvedNoTransition": "action.resolved", "Failed": "action.failed",
    "Cancelled": "action.cancelled",
}
_ACTIVITY_SEVERITY_BY_STATUS: dict[str, str] = {
    "ReconciliationRequired": "critical", "NotDelivered": "attention", "Failed": "attention",
}
#: Resolution ``kind`` per final status (§2.11 ``Resolution``); exactly the statuses with no way out.
_RESOLUTION_KIND: dict[str, str] = {
    "StateChanged": "state_changed", "ResolvedNoTransition": "no_transition", "Failed": "failed",
    "Cancelled": "cancelled",
}
#: The reconciler's proof that a decision never left (C24), stored on the row and checked again by
#: ``mark_not_delivered``: no row in any slot, the slot never ran, the disk baseline intact, the same boot.
ABSENCE_PROOF: dict[str, bool] = {
    "transcript_row_absent": True, "slot_ran_since": False, "disk_baseline_unchanged": True, "boot_id_unchanged": True,
}
#: ``binding`` flag → refusal, in the order checked (C29): both block every human-lane dispatch.
_DISPATCH_BLOCKS = (
    ("archived", "intent_archived", "this intent is archived in Studio"),
    ("paused", "intent_paused", "this intent is paused; resume it before sending anything"),
)
#: Row fields read as "absent" when empty.
_OPTIONAL_COLUMNS = (
    "resolved_repo_identity", "intent_uuid", "stage", "unit", "payload_digest", "human_text", "wire_text",
    "session_key", "slot_key", "delivery_id", "delivering_at", "delivered_at", "resolved_at", "deadline_at",
    "boot_id", "retry_fingerprint", "dedupe_key",
)
#: Where a new card lives: read off a seed or a record, or passed as a mapping.
_SCOPE = ("repo_id", "space", "intent_dir", "intent_uuid", "stage", "unit")

_ACTIONS, _TRANSITIONS, _BREAKERS = "actions", "action_transitions", "breakers"
_DRAFTS, _REPOS = "advisor_drafts", "repos"


def _refuse(code: str, message: str, **details: Any) -> NoReturn:
    raise StudioError(code, message, details=details)


def lane_for(decision: str) -> str:
    """Which lane a decision travels in; an unknown one is ``studio_only``, the lane that reaches nothing."""
    if decision in HUMAN_LANE_DECISIONS:
        return "human_lane"
    return "host_control" if decision in HOST_CONTROL_DECISIONS else "studio_only"


def _canonical_json(value: Any) -> str:
    """Sorted keys, no whitespace: a digest must not depend on dict order, or every resubmit looks tampered."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return security.sha256_text(_canonical_json(value))


def _plain(value: Any) -> Any:
    """``to_json()`` when the value has one, a copy of a mapping, else the value itself."""
    if isinstance(value, Mapping):
        return dict(value)
    return value.to_json() if hasattr(value, "to_json") else value


def _at_or_after(candidate: object, since: object) -> bool:
    """``candidate >= since`` across Studio's ``…Z`` and the host's offset-aware stamps (as strings the host
    row sorts *before* a Studio stamp of the same second); ``False`` when either cannot be read."""

    def seconds(value: object) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        if not isinstance(value, str) or not value:
            return None
        exact = C.epoch_from_iso(value)
        if exact is not None:
            return exact
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()

    left, right = seconds(candidate), seconds(since)
    return left is not None and right is not None and left >= right


# ---- records ----------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Captured(WireRecord):
    """What a decision was captured against (C30): six compared fields and two facts about the read.
    ``null`` is a value: a gate captured with no questions file must go stale the moment one appears."""

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
        attempt, active = raw.get("stage_attempt"), raw.get("is_active")
        return cls(
            raw.get("state_hash") or None, raw.get("boundary_token") or None, raw.get("question_digest") or None,
            None if attempt is None else int(attempt), raw.get("evidence_digest") or None,
            None if active is None else bool(active), str(raw.get("captured_at") or ""),
            bool(raw.get("stable", True)),
        )

    def differences(self, other: "Captured") -> list[str]:
        """Compared fields where the two disagree, ``null`` vs non-``null`` included."""
        return [name for name in COMPARED_CAPTURED_FIELDS if getattr(self, name) != getattr(other, name)]


@dataclass(frozen=True, slots=True)
class HostCall(WireRecord):
    """The call the browser must make next. Data, because the backend must not make it itself."""

    method: str
    path: str
    body: dict


@dataclass(frozen=True, slots=True)
class ActionRecord(WireRecord):
    """One ``actions`` row, typed. ``evidence`` stays a plain dict: the scan and the reconciler write it from
    opposite ends, and ``card()`` splits it into the exact §2.11 sub-objects."""

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
        evidence = dict(row["evidence_json"]) if isinstance(row.get("evidence_json"), Mapping) else {}
        meta = evidence.get(CARD_META_KEY) if isinstance(evidence.get(CARD_META_KEY), Mapping) else {}
        space, intent_dir = str(row.get("space") or C.DEFAULT_SPACE), str(row.get("intent_dir") or "")
        payload, resolution, lease = row.get("payload_json"), row.get("resolution_json"), row.get("lease_generation")
        return cls(
            **{name: row.get(name) or None for name in _OPTIONAL_COLUMNS},
            action_id=str(row.get("action_id") or ""), type=str(row.get("type") or ""),
            risk_class=str(row.get("risk_class") or "studio_only"), repo_id=str(row.get("repo_id") or ""),
            intent_dir=intent_dir, space=space, intent_key=C.intent_key_for(space, intent_dir),
            captured=Captured.from_json({
                "state_hash": row.get("captured_state_hash"), "boundary_token": row.get("boundary_token"),
                "question_digest": row.get("question_digest"), "stage_attempt": row.get("stage_attempt"),
                "evidence_digest": row.get("evidence_digest"), "is_active": row.get("is_active_captured"),
                "captured_at": meta.get("captured_at") or row.get("created_at"),
                "stable": meta.get("stable", True),
            }),
            payload=dict(payload) if isinstance(payload, Mapping) and payload else None,
            status=str(row.get("status") or "Queued"), status_generation=int(row.get("status_generation") or 0),
            lease_generation=None if lease is None else int(lease),
            created_at=str(row.get("created_at") or ""), updated_at=str(row.get("updated_at") or ""),
            evidence=evidence,
            resolution=dict(resolution) if isinstance(resolution, Mapping) and resolution else None,
            cursor_readback=_decode_readback(row.get("cursor_readback_json")),
            presence_baseline=_decode_presence(row.get("presence_baseline_json")),
            source=str(row.get("source") or "studio"),
            waiting_since=str(row.get("waiting_since") or row.get("created_at") or ""),
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
        """The row as data, for diagnostics and tests (the UI is served ``card()``). Retained prose never
        leaves as data; the flag says whether there is any."""
        out = WireRecord.to_json(self)
        del out["human_text"]
        return {**out, "human_text_present": bool(self.human_text)}


@dataclass(frozen=True, slots=True)
class SubmitReceipt(WireRecord):
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
        return {"ok": True, **WireRecord.to_json(self)}


def _decode_readback(raw: object) -> CursorReadback | None:
    """The stored read-back as the engine's dataclass: ``ConsistencyEngine`` reads ``readback.ok`` to raise
    ``cursor_mismatch``, and a dict would answer ``None`` there and drop a blocking finding."""
    if isinstance(raw, CursorReadback) or not isinstance(raw, Mapping):
        return raw if isinstance(raw, CursorReadback) else None
    return CursorReadback(
        ok=bool(raw.get("ok")), space=str(raw.get("space") or ""), dir_name=str(raw.get("dir_name") or ""),
        uuid=raw.get("uuid") or None, state_sha256=raw.get("state_sha256") or None,
        mismatch=tuple(str(m) for m in (raw.get("mismatch") or ())),
    )


def _decode_presence(raw: object) -> PresenceBaseline | None:
    if isinstance(raw, PresenceBaseline) or not isinstance(raw, Mapping):
        return raw if isinstance(raw, PresenceBaseline) else None
    sizes, mtime, counter = raw.get("shard_sizes"), raw.get("human_turn_mtime_ns"), raw.get("turn_counter")
    return PresenceBaseline(
        human_turn_events=int(raw.get("human_turn_events") or 0),
        human_turn_events_partial=bool(raw.get("human_turn_events_partial")),
        human_turn_mtime_ns=None if mtime is None else int(mtime),
        turn_counter=None if counter is None else int(counter),
        shard_sizes={str(k): int(v) for k, v in sizes.items()} if isinstance(sizes, Mapping) else {},
        audit_tail_sha256=str(raw.get("audit_tail_sha256") or ""), state_sha256=raw.get("state_sha256") or None,
        taken_at=str(raw.get("taken_at") or ""),
    )


def _presence_comparable(baseline: Any) -> dict[str, Any] | None:
    """A baseline without its capture time: two reads of an unchanged record differ in exactly that field,
    and comparing it would make the disk half of the delivery proof impossible to satisfy."""
    payload = _plain(baseline)
    if payload is None:
        return None
    payload.pop("taken_at", None)
    return payload


def _captured_columns(captured: Mapping[str, Any]) -> dict[str, Any]:
    """The compare-and-submit set as ``actions`` columns. ``is_active`` keeps ``None`` apart from ``False``:
    "the cursor did not name this intent" and "we never looked" are different answers (P26.3)."""
    active = captured.get("is_active")
    return {
        "captured_state_hash": captured.get("state_hash"), "boundary_token": captured.get("boundary_token"),
        "question_digest": captured.get("question_digest"), "stage_attempt": captured.get("stage_attempt"),
        "evidence_digest": captured.get("evidence_digest"),
        "is_active_captured": None if active is None else int(bool(active)),
    }


# ---- wire text (FR-GATE-002): the exact bytes a decision sends --------------- #


def wire_text_for(
    decision: str, payload: Mapping[str, Any], *, questions: "QuestionsFile | None", grouped_answers_enabled: bool,
) -> str | None:
    """The bytes this decision sends, or ``None`` for a non-prompt lane. Pure and total: what it cannot produce
    is a ``StudioError``, never a best guess, so ``submit`` can compare ``client_wire_text`` byte for byte and
    no UI, Slack message or replayed request chooses the text that lands in someone's audit trail."""
    if decision in STUDIO_ONLY_DECISIONS or decision in HOST_CONTROL_DECISIONS:
        return None
    if decision in _FIXED_WIRE:
        return _FIXED_WIRE[decision]
    if decision in ("request_changes", "request_plan_changes"):
        return C.WIRE_REQUEST_CHANGES_PREFIX + _feedback(payload, "feedback")
    if decision == "answers":
        return _answers_wire_text(payload, questions, grouped_answers_enabled)
    if decision in _CHOICE_WIRE:
        key, options = _CHOICE_WIRE[decision]
        value = str(payload.get(key) or "")
        if value not in options:
            _refuse("invalid_decision", f"{decision} needs {key} {' or '.join(map(repr, options))}",
                    decision=decision, **{key: value})
        prefix, field = options[value]
        return prefix + _feedback(payload, field) if field else prefix
    _refuse("invalid_decision", f"unknown decision {decision!r}", decision=decision)


def _feedback(payload: Mapping[str, Any], key: str) -> str:
    """A required free-text field, stripped and bounded. Blank is ``feedback_required``, not an empty prompt:
    ``Request Changes: `` is a turn the conductor cannot act on, and it still spends the human's reply."""
    raw = payload.get(key)
    text = str(raw).strip() if raw is not None else ""
    if not text:
        _refuse("feedback_required", f"{key} must not be blank", field=key)
    if len(text) > C.MAX_FEEDBACK_CHARS:
        _refuse("too_large", f"{key} is longer than {C.MAX_FEEDBACK_CHARS} characters",
                field=key, length=len(text), cap=C.MAX_FEEDBACK_CHARS)
    return text


def _answers_wire_text(payload: Mapping[str, Any], questions: "QuestionsFile | None", grouped_enabled: bool) -> str:
    """Encode a question group's answers (C14, P18): option **labels**, never letters, which a revision may
    re-letter. One pending question sends its answer alone (research 05 §5.1); more use the verified
    file-backed ``Q<n>:`` form, or the audit form when the engine asked them together (A37)."""
    if questions is None:
        _refuse("answers_incomplete", "there is no questions file to answer", reason="no_questions")
    pending = [q for q in questions.questions if not q.answered]
    if not pending:
        _refuse("answers_incomplete", "no question is pending", reason="nothing_pending")
    raw = payload.get("answers")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        _refuse("answers_incomplete", "answers must be a list", reason="not_a_list")
    supplied: dict[int, Mapping[str, Any]] = {}
    for item in raw:
        if not isinstance(item, Mapping) or type(item.get("index")) is not int:
            _refuse("invalid_decision", "each answer needs an integer question index")
        if item["index"] in supplied:
            _refuse("invalid_decision", "a question was answered more than once", index=item["index"])
        supplied[item["index"]] = item
    pending_indexes = {q.index for q in pending}
    if len(pending_indexes) != len(pending) or any(i < 1 for i in pending_indexes):
        _refuse("invalid_decision", "the question indexes are ambiguous")
    extra = sorted(set(supplied) - pending_indexes)
    if extra:
        _refuse("invalid_decision", "answers include questions that are not pending", indexes=extra)
    missing = [q.index for q in pending if q.index not in supplied]
    if missing:
        _refuse("answers_incomplete", "every pending question must be answered in one submit", missing=missing)

    text_input = audit_question_accepts_text(questions)
    batch = questions.origin is not None and int(questions.origin.get("batch_size") or 1) > 1
    texts: list[tuple[int, str]] = []
    for question in pending:
        answer = supplied[question.index]
        letters = answer.get("option_letters") or []
        if not isinstance(letters, (list, tuple)) or any(not isinstance(v, str) for v in letters):
            _refuse("invalid_decision", "option_letters must be a list of labels")
        letters = list(letters)
        known = {option.letter: option for option in question.options}
        if len(known) != len(question.options) or len(set(letters)) != len(letters):
            _refuse("invalid_decision", "option letters must be unique", index=question.index)
        unknown = [letter for letter in letters if letter not in known]
        if unknown:
            _refuse("invalid_decision", "an answer names an option this question does not offer",
                    index=question.index, unknown=unknown, options=sorted(known))
        if len(letters) > 1 and not question.multi_select:
            _refuse("invalid_decision", "this question accepts one option", index=question.index,
                    option_letters=letters)
        free_text = str(answer.get("free_text") or "").strip()
        if batch and len(letters) > 1 and any(known[letter].text == C.AUDIT_NONE_LABEL for letter in letters):
            _refuse("invalid_decision", "None of these cannot be combined with another choice", index=question.index)
        if questions.origin is not None and not text_input and not batch and (len(letters) != 1 or free_text):
            _refuse("invalid_decision", "an audit question requires one of its exact recorded labels",
                    index=question.index)
        if not letters or any(known[letter].is_other for letter in letters):
            # `X. Other` and an option-less (free-form) question both send the human's own words.
            if not free_text or (text_input and is_audit_text_label(free_text)):
                _refuse("answers_incomplete", "this answer needs free text", index=question.index,
                        reason="free_text_required")
            parts = [free_text if known[letter].is_other else known[letter].text for letter in letters]
            text = C.WIRE_MULTI_SELECT_JOINER.join(parts) if letters else free_text
        else:
            text = C.WIRE_MULTI_SELECT_JOINER.join(known[letter].text for letter in letters)
        if len(text) > C.MAX_ANSWER_CHARS:
            _refuse("too_large", f"answer {question.index} is longer than {C.MAX_ANSWER_CHARS} characters",
                    index=question.index, cap=C.MAX_ANSWER_CHARS)
        texts.append((question.index, text))

    if len(texts) == 1:
        return texts[0][1]
    if not batch and not grouped_enabled:
        _refuse("grouped_answers_unavailable", "answering several questions in one prompt is not verified yet",
                pending=[index for index, _ in texts], reason="s1_s2_unverified")
    lines = C.WIRE_ANSWER_JOINER.join(C.WIRE_ANSWER_LINE.format(index=index, answer=text) for index, text in texts)
    # An audit batch is one engine question expecting one reply (A37): no file mapping is involved.
    return lines + (C.WIRE_AUDIT_GROUPED_ANSWER_SUFFIX if batch else C.WIRE_GROUPED_ANSWER_SUFFIX)


def _human_prose(payload: Mapping[str, Any]) -> str | None:
    """The human's own words in a payload. Only prose is retained (and purged): storing ``Approve`` there
    would pretend a protocol label was personal data, while ``human_text_present`` really means prose."""
    for key in ("feedback", "text", "scope"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    answers = payload.get("answers")
    if isinstance(answers, Sequence) and not isinstance(answers, (str, bytes)):
        parts = [str(item.get("free_text")).strip() for item in answers
                 if isinstance(item, Mapping) and str(item.get("free_text") or "").strip()]
        if parts:
            return "\n".join(parts)
    return None


def _host_call(rec: ActionRecord) -> HostCall | None:
    """The browser's call for a dispatched row: the host's stop endpoint, or the chat send with the server's
    wire text and the delivery meta the reconciler matches transcript rows by (R08)."""
    if not (rec.delivery_id and rec.slot_key):
        return None
    if rec.type == "force_stop":
        return HostCall("POST", f"{C.HOST_CHAT_SLOTS_PATH}/{rec.slot_key}/stop", {})
    if rec.wire_text is None:
        return None
    return HostCall("POST", C.HOST_CHAT_SEND_PATH, {
        "message": rec.wire_text, "slot": rec.slot_key, "agent": C.AIDLC_AGENT_NAME,
        "meta": {"studio_action_id": rec.action_id, "studio_delivery_id": rec.delivery_id},
    })


def _queue_key(rec: ActionRecord) -> tuple[int, int, str, str]:
    severity = str(rec.card_meta.get("severity") or SEED_SEVERITY.get(rec.queue_type, "info"))
    return (priority_group_for(rec.queue_type), SEVERITY_RANK.get(severity, 3), rec.waiting_since or rec.created_at,
            rec.action_id)


# ---- the human lane ---------------------------------------------------------- #


class HumanActionBroker:
    """Owns ``actions``, ``action_transitions`` and ``breakers``. Async; loop thread."""

    def __init__(
        self, storage: "Storage", projection: "Projection", reader: "AidlcReader",
        consistency: "ConsistencyEngine", scheduler: "RepoScheduler", sessions: "SessionBinder",
        engine: "EngineRunner", host: "HostBridge", events: "EventLog", activity: "ActivityProjector",
        notifications: Any, settings: "SettingsService", clock: C.Clock, ids: C.IdFactory, boot_id: str,
        *, scan_pool: Executor | None = None,
    ) -> None:
        self._storage, self._projection, self._reader = storage, projection, reader
        self._consistency, self._scheduler, self._sessions = consistency, scheduler, sessions
        self._engine, self._host, self._events, self._activity = engine, host, events, activity
        self._notifications, self._settings, self._clock, self._ids = notifications, settings, clock, ids
        self._boot_id, self._scan_pool = boot_id, scan_pool
        #: Late-bound by ``Services.build`` (the reconciler is built from the services holding this broker);
        #: only ``resolve("reconcile")`` needs it, and it says so rather than doing nothing.
        self.reconciler: Any = None

    # ---- reads ------------------------------------------------------------ #

    async def get(self, action_id: str) -> ActionRecord:
        row = await asyncio.to_thread(self._storage.get, _ACTIONS, action_id)
        if row is None:
            _refuse("action_not_found", "no such action", action_id=action_id)
        return ActionRecord.from_row(row)

    async def list_live(
        self, *, repo_id: str | None = None, space: str | None = None, intent_dir: str | None = None,
        include_revision: bool = False,
    ) -> list[ActionRecord]:
        """Every queue-visible row in queue order (FR-ACT-003/004); ``revision`` cards only when asked for,
        so an informational ``[R]`` never competes with a decision (C18)."""
        where: dict[str, Any] = {"status": sorted(C.LIVE_ACTION_STATUS)}
        where.update({k: v for k, v in (("repo_id", repo_id), ("space", space), ("intent_dir", intent_dir)) if v})
        rows = await asyncio.to_thread(self._storage.select, _ACTIONS, where)
        recs = (ActionRecord.from_row(row) for row in rows)
        return sorted((rec for rec in recs if include_revision or rec.type != "revision"), key=_queue_key)

    # ---- derivation (the reconciler's half of the queue) ------------------- #

    async def upsert_derived(self, seeds: "Sequence[CardSeed]") -> list[ActionRecord]:
        """Insert or refresh one card per boundary the last scan saw. A live row is refreshed only while nothing
        was dispatched (from ``Delivering`` on it records what a human decided); when only terminal rows hold the
        key a new ``Queued`` row goes in, so no dismissed or failed card hides an open gate (FR-ACT-007, R12)."""
        scopes: dict[tuple[str, str, str], None] = {}
        for seed in seeds:
            scopes[(seed.repo_id, seed.space, seed.intent_dir)] = None
            await self._upsert_seed(seed)
        out: dict[str, ActionRecord] = {}
        for repo_id, space, intent_dir in scopes:
            for rec in await self.list_live(repo_id=repo_id, space=space, intent_dir=intent_dir, include_revision=True):
                out.setdefault(rec.action_id, rec)
        return sorted(out.values(), key=_queue_key)

    async def _upsert_seed(self, seed: "CardSeed") -> None:
        live = await self._live_for_key(seed.dedupe_key)
        if live is not None:
            if live.status in SUBMITTABLE_STATUSES:
                await self._refresh_card(live, seed)
            return
        superseded = await self._live_for_key(seed.dedupe_key, newest=True)
        evidence = {**seed.evidence, CARD_META_KEY: _seed_meta(seed)}
        if superseded is not None:
            evidence["superseded_action_id"] = superseded.action_id
        try:
            await self._insert(seed.type, seed, risk_class=seed.risk_class, captured=seed.captured, evidence=evidence,
                               dedupe_key=seed.dedupe_key, waiting_since=seed.waiting_since, source=seed.source)
        except sqlite3.IntegrityError:
            # Another tick or gateway inserted the boundary between the read and the write: the index did its job.
            logger.debug("aidlc-studio: card for %s already exists", seed.dedupe_key)

    async def _refresh_card(self, rec: ActionRecord, seed: "CardSeed") -> None:
        """Bring an undispatched card's evidence up to date, writing only when something changed: every write
        costs an fsync and bumps ``status_generation``, losing a tab holding it a race it never entered."""
        meta = _seed_meta(seed)
        # A re-observation keeps the first observation time, so it cannot invalidate an in-flight submit's
        # CAS; a real change takes the new time below.
        meta["captured_at"] = rec.captured.captured_at
        meta["stable"] = bool(seed.captured.get("stable", True))
        # Snapshot keys and the seed's own extras are refreshed; findings keep the codes stored on the action.
        evidence = {**rec.evidence, **{k: v for k, v in seed.evidence.items() if k != CARD_META_KEY},
                    "findings": _merge_findings(rec.evidence.get("findings"), seed.evidence.get("findings")),
                    CARD_META_KEY: meta}
        values = {**_captured_columns(seed.captured), "intent_uuid": seed.intent_uuid,
                  "waiting_since": seed.waiting_since, "evidence_json": evidence}
        current = {**_captured_columns(rec.captured.to_json()), "intent_uuid": rec.intent_uuid,
                   "waiting_since": rec.waiting_since, "evidence_json": rec.evidence}
        if values == current:
            return
        meta["captured_at"] = seed.captured.get("captured_at") or rec.captured.captured_at
        try:
            await asyncio.to_thread(self._storage.cas_update, _ACTIONS, rec.action_id, rec.status_generation, values)
        except StaleGeneration:
            # A submit or reconcile moved the row while the scan assembled seeds; the next tick re-derives
            # from a snapshot that has seen the move, where writing now would overwrite a decision's capture.
            logger.debug("aidlc-studio: card %s moved during refresh", rec.action_id)

    async def retire_stale(
        self, repo_id: str, space: str, intent_dir: str, live_keys: set[str], *, reason: str = "boundary_superseded",
    ) -> None:
        """Retire derived cards whose boundary left the disk (``intent_missing`` when the whole intent did,
        §1.13 step 1b). ``Failed`` is terminal (C04/P07): it is marked ``superseded`` instead, so the failure
        stays readable. Command cards have no derived key; they are the user's."""
        for rec in await self.list_live(repo_id=repo_id, space=space, intent_dir=intent_dir, include_revision=True):
            if rec.type in COMMAND_ACTION_TYPES or not rec.dedupe_key or rec.dedupe_key in live_keys:
                continue
            if rec.status in SUBMITTABLE_STATUSES:
                await self._transition(rec, "Cancelled", reason=reason, evidence={"superseded": True})
            elif rec.status == "Failed" and not rec.evidence.get("superseded"):
                await self.patch_evidence(rec, {"superseded": True})

    # ---- command actions -------------------------------------------------- #

    async def create_command(
        self, type: str, repo: RepoRecord, space: str, intent_dir: str, *, snap: "IntentSnapshot",
        source: str = "studio",
    ) -> ActionRecord:
        """Create the ``run``/``resume``/``prepare_commit`` card a human asked for. A second click while the
        first card is live returns it: one live command per boundary."""
        if type not in PROMPT_COMMAND_TYPES:
            raise ValueError(f"{type!r} is not a prompt command type; force_stop uses create_force_stop")
        binding = await self._sessions.get(repo.repo_id, space, intent_dir)
        await self._assert_dispatchable(binding, decision=type, intent_key=snap.intent_key, repo_id=repo.repo_id)
        findings = await self._assert_consistent(repo, snap, binding)
        if type in ("run", "resume"):
            await self._assert_run_current(type, snap)
            slot = self._host.slot(binding.slot_key) if binding.slot_key else None
            if slot is not None and busy_reasons(slot):
                _refuse("slot_busy", "the canonical conversation is already busy",
                        slot_key=binding.slot_key, reasons=list(busy_reasons(slot)))
        return await self._command_card(type, repo, space, intent_dir, snap, binding, findings, source)

    async def _command_card(
        self, kind: str, repo: RepoRecord, space: str, intent_dir: str, snap: "IntentSnapshot", binding: Any,
        findings: Sequence[Any], source: str,
    ) -> ActionRecord:
        captured = self._projection.captured(snap, snap.stage)
        key = dedupe_key(kind, repo.repo_id, snap.intent_key, snap.stage, captured["boundary_token"])
        live = await self._live_for_key(key)
        if live is not None:
            return live
        evidence = self._projection.evidence(snap, findings=findings, binding=binding).to_json()
        evidence[CARD_META_KEY] = {
            "severity": "info", "decisions": list(DECISIONS[kind]), "headline_params": {"stage": snap.stage},
            "consequence_variant": "one_turn" if kind == "run" else None,
            "stage_ref": self._projection.stage_ref(snap, snap.stage), "reason": None, "related_action_id": None,
            "captured_at": captured["captured_at"], "stable": bool(captured["stable"]),
        }
        scope = {"repo_id": repo.repo_id, "space": space, "intent_dir": intent_dir, "stage": snap.stage,
                 "unit": snap.unit, "intent_uuid": snap.row.uuid if snap.row is not None else binding.intent_uuid}
        return await self._insert(kind, scope, risk_class="host_control" if kind == "force_stop" else "human_lane",
                                  captured=captured, evidence=evidence, dedupe_key=key, waiting_since=snap.taken_at,
                                  source=source, resolved_repo_identity=repo.resolved_identity)

    async def _assert_run_current(self, decision: str, snap: "IntentSnapshot", rec: ActionRecord | None = None) -> None:
        """A Run/Resume must still be what the checkpoint asks for; a never-sent stale one is retired on the way
        out, so the user is shown the current checkpoint instead."""
        if decision not in ("run", "resume") or (rec is not None and rec.type == "question"):
            return  # a question card's Resume answers to `_assert_plan_session`
        reason = run_unavailable_reason(snap, stage=rec.stage if rec else None, unit=rec.unit if rec else None)
        if reason is None:
            return
        if rec is not None and rec.status == "Queued" and not rec.delivery_id and not rec.delivering_at:
            rec = await self._transition(rec, "Cancelled", reason="command_superseded", evidence={
                "superseded": True, "command_unavailable_reason": reason, "current_stage": snap.stage})
        _refuse("run_not_applicable", "open the intent's current checkpoint instead of this run", reason=reason,
                current_stage=snap.stage, **({"card": await self.card(rec, snap)} if rec is not None else {}))

    async def _assert_plan_session(self, decision: str, snap: "IntentSnapshot", rec: ActionRecord,
                                   slot: Any | None) -> None:
        """AI-DLC 2.10 records a Plan Approval reply only in the session that asked it, so a question card offers
        Resume while that session is gone and a reply while it is not (`plan_session_stale`). The other one is
        refused with the card as it stands, never retired: the card's seed follows the session on the next scan.
        The pending checkpoint is the reason for this Resume, so the command Run rule does not judge it."""
        if rec.type != "question" or decision not in ("resume", *C.PLAN_REPLY_DECISIONS):
            return
        stale = plan_session_stale(snap, slot)
        if (decision == "resume") != stale:
            _refuse("action_stale", "the session that asked this plan approval is gone; resume so AI-DLC asks again"
                    if stale else "AI-DLC has asked for this plan approval again in this session",
                    fields=["plan_session"], card=await self.card(rec, snap))

    async def create_force_stop(
        self, repo: RepoRecord, space: str, intent_dir: str, *, snap: "IntentSnapshot",
    ) -> SubmitReceipt:
        """Commit a force stop and hand the browser the host's stop call (C28, P16). Nothing reaches the model, so
        no wire text, lease, cursor switch or presence baseline; the durable record still comes first (FR-RUN-004),
        since it becomes the ``Interrupted`` state PRD §11.2's manual takeover requires."""
        binding = await self._sessions.get(repo.repo_id, space, intent_dir)
        if not binding.slot_key:
            _refuse("session_unbound", "this intent has no bound chat slot to stop", intent_key=snap.intent_key)
        lives = await self.list_live(repo_id=repo.repo_id, space=space, intent_dir=intent_dir, include_revision=True)
        rec = next((live for live in lives if live.type == "force_stop"), None)
        if rec is None:
            rec = await self._command_card("force_stop", repo, space, intent_dir, snap, binding, (), "studio")
        if not rec.delivery_id:
            rec = await self._transition(rec, "Delivering", reason="force_stop_committed", evidence={}, values={
                "delivery_id": self._ids.new("dl"), "slot_key": binding.slot_key,
                "session_key": binding.session_key or f"dashboard:{binding.slot_key}",
                "delivering_at": self._clock.iso(), "deadline_at": self._deadline(C.DELIVERY_ACK_DEADLINE_SECS),
                "boot_id": self._boot_id,
            })
        return self._receipt(rec, None)

    @staticmethod
    def _receipt(rec: ActionRecord, lease_generation: int | None) -> SubmitReceipt:
        return SubmitReceipt(rec.action_id, rec.status, str(rec.lane), rec.delivery_id or "", rec.slot_key or "",
                             rec.session_key or "", rec.wire_text, rec.deadline_at or "", lease_generation,
                             _host_call(rec))

    # ---- two-phase submit ------------------------------------------------- #

    async def submit(
        self, action_id: str, *, captured: Mapping[str, Any], payload: Mapping[str, Any], client_wire_text: str,
        user: str, source: str = "studio",
    ) -> SubmitReceipt:
        """Commit one human decision in the order §1.12 fixes, and return the browser's next call. Each step is a
        refusal that must happen before the next can do damage: cheap ones first, the lease once the decision is
        known legal, the cursor switch under it, the durable ``Delivering`` record before returning (PRD §11.5).
        The grant is released once that CAS is decided (C31/P15): the in-flight action holds the repository."""
        rec = await self.get(action_id)
        if rec.status not in SUBMITTABLE_STATUSES:
            _refuse("action_not_submittable", f"an action in {rec.status} cannot be submitted",
                    action_id=action_id, status=rec.status)
        if rec.evidence.get("delivery_confirmed"):
            # `NotDelivered` is submittable, so this is the second door into `Delivering` for a row a later
            # report proved delivered; `retry` refuses it too, or the refusal would be decorative.
            _refuse("not_delivered_unproven", "a later report confirmed this decision was delivered; it must not "
                    "be sent again", action_id=action_id, status=rec.status, delivery_confirmed=True)
        decision = str(payload.get("decision") or "")
        offered = DECISIONS.get(rec.type, ())
        if decision not in offered:
            _refuse("invalid_decision", f"{rec.type} cards do not offer {decision!r}",
                    decision=decision, type=rec.type, offered=list(offered))
        if decision not in HUMAN_LANE_DECISIONS:
            _refuse("invalid_decision", f"{decision!r} is not submitted through the human lane",
                    decision=decision, lane=lane_for(decision))
        if decision == "accept_as_is" and int(rec.captured.stage_attempt or 1) < C.ACCEPT_AS_IS_MIN_ATTEMPT:
            _refuse("invalid_decision", "Accept as-is only exists after three rejection cycles", decision=decision,
                    stage_attempt=rec.captured.stage_attempt, min_attempt=C.ACCEPT_AS_IS_MIN_ATTEMPT)

        repo = await self._repo(rec.repo_id)
        if repo.availability != "available":
            _refuse("repo_unavailable", "restore access to this repository before submitting a decision",
                    repo_id=repo.repo_id, availability=repo.availability, detail=repo.availability_detail)
        binding = await self._sessions.get(rec.repo_id, rec.space, rec.intent_dir)
        await self._assert_dispatchable(binding, decision=decision, intent_key=rec.intent_key, repo_id=rec.repo_id)

        snap = await self._snapshot(repo, rec.space, rec.intent_dir)
        await self._assert_run_current(decision, snap, rec)
        await self._assert_plan_session(decision, snap, rec, self._host.slot(binding.slot_key) if binding.slot_key
                                        else None)
        card_questions = rec.evidence.get("questions") or {}
        audit_question = rec.type == "question" and (card_questions.get("origin") or {}).get("kind") == "audit"
        file_answer = decision == "answers" and not audit_question
        if audit_question:
            self._assert_audit_question_current(rec, snap)
        if (rec.type == "question" and snap.questions and snap.questions.unsupported_pending_count
                and rec.stage == snap.stage and snap.questions.relpath == card_questions.get("relpath")):
            # Only this card's own file: another stage's follow-ups mean the boundary moved, which the drift
            # check reports as action_stale. An old card may still offer answers or a confirmation.
            _refuse("invalid_decision", "answer the pending follow-ups in the conversation",
                    reason="unsupported_question_format")
        if file_answer and (snap.questions is None or not file_questions_support_forms(snap.questions)):
            _refuse("invalid_decision", "this question file cannot be answered as a form",
                    reason="unsupported_question_format")
        grouped = bool(self._settings.capabilities().get("grouped_answers", {}).get("available"))
        wire_text = wire_text_for(decision, payload, questions=snap.questions, grouped_answers_enabled=grouped)
        if client_wire_text != wire_text:
            _refuse("invalid_decision", "the text the browser displayed is not the text this decision sends",
                    decision=decision, expected=wire_text)

        live_captured = Captured.from_json(self._projection.captured(snap, rec.stage))
        if (live_captured.differences(rec.captured) and rec.type in PROMPT_COMMAND_TYPES and rec.status == "Queued"
                and rec.delivery_id is None and rec.delivering_at is None and snap.stable):
            # No seed refreshes a user-created command card: persist the evidence card() shows, under CAS,
            # before comparing the user's capture. A stale browser still fails below.
            slot = self._host.slot(binding.slot_key) if binding.slot_key else None
            live = self._projection.evidence(snap, binding=binding, slot=slot, stage=rec.stage).to_json()
            evidence = {**rec.evidence, **{key: live.get(key) for key in LIVE_REFRESHED_EVIDENCE_KEYS}, CARD_META_KEY: {
                **rec.card_meta, "captured_at": live_captured.captured_at, "stable": live_captured.stable}}
            generation = await asyncio.to_thread(
                self._storage.cas_update, _ACTIONS, rec.action_id, rec.status_generation,
                {**_captured_columns(live_captured.to_json()), "evidence_json": evidence})
            # Exactly the generation written: re-reading could adopt a concurrent submit's and make a second
            # dispatch eligible.
            rec = replace(rec, captured=live_captured, evidence=evidence, status_generation=generation)
        drift = sorted(set(live_captured.differences(Captured.from_json(captured)))
                       | set(live_captured.differences(rec.captured)))
        if drift:
            _refuse("action_stale", "the boundary this decision was captured against has moved", fields=drift,
                    card=await self.card(rec, snap))

        await self._assert_consistent(repo, snap, binding)
        slot_key = binding.slot_key
        if not slot_key:
            _refuse("session_unbound", "this intent has no bound chat slot", intent_key=rec.intent_key)
        view = self._host.slot(slot_key)
        if view is None or not self._host.slot_matches_repo(view, repo.canonical_path):
            _refuse("slot_mismatch", "the bound slot is gone or no longer points at this repository",
                    slot_key=slot_key, canonical_path=repo.canonical_path, present=view is not None)
        if busy_reasons(view):
            _refuse("slot_busy", "the session is busy; sending now would interleave two turns",
                    slot_key=slot_key, reasons=list(busy_reasons(view)))
        if file_answer:
            self._assert_file_answer_session(slot_key)

        session_key = binding.session_key or view.session_key
        grant = await self._scheduler.acquire_execution(
            repo, intent_uuid=binding.intent_uuid or rec.intent_uuid, session_key=session_key, action_id=rec.action_id)
        try:
            readback = await asyncio.to_thread(
                self._engine.switch_cursor_sync, Path(repo.canonical_path),
                repo.engine_dir or snap.engine_dir or C.STUDIO_HARNESS_DIR, rec.space, rec.intent_dir,
                binding.intent_uuid or rec.intent_uuid or (snap.row.uuid if snap.row is not None else None),
                lease_generation=grant.generation, repo_id=repo.repo_id)
            if not readback.ok or readback.state_sha256 != live_captured.state_hash:
                _refuse("cursor_mismatch", "the repository cursor does not name this intent",
                        mismatch=list(readback.mismatch) or ["state_changed"], readback=readback.to_json(),
                        expected_state_hash=live_captured.state_hash)
            if audit_question or file_answer or decision in ("run", "resume"):
                # The switch may yield to an engine write: recheck what this decision rests on, under the lease.
                snap = await self._snapshot(repo, rec.space, rec.intent_dir)
            if audit_question:
                self._assert_audit_question_current(rec, snap)
            if file_answer:
                moved = Captured.from_json(self._projection.captured(snap, rec.stage)).differences(live_captured)
                if moved or not snap.stable:
                    _refuse("action_stale", "the question group changed before dispatch", fields=list(moved))
            if decision in ("run", "resume"):
                if not snap.stable:
                    _refuse("unstable_read", "the intent changed before dispatch")
                await self._assert_run_current(decision, snap, rec)
            await self._assert_plan_session(decision, snap, rec, self._host.slot(slot_key))
            presence = await asyncio.to_thread(self._reader.presence_baseline, Path(repo.canonical_path), rec.space,
                                               rec.intent_dir, snap.audit, live_captured.state_hash)
            if file_answer:
                self._assert_file_answer_session(slot_key)
            body = dict(payload)
            generation = await asyncio.to_thread(self._storage.cas_update, _ACTIONS, rec.action_id,
                                                 rec.status_generation, {
                "status": "Delivering", "delivery_id": self._ids.new("dl"), "slot_key": slot_key,
                "session_key": session_key, "lease_generation": grant.generation,
                # The identity the grant locked: from here on the in-flight row is the execution lease.
                "resolved_repo_identity": repo.resolved_identity,
                "delivering_at": self._clock.iso(), "deadline_at": self._deadline(C.DELIVERY_ACK_DEADLINE_SECS),
                "wire_text": wire_text, "payload_json": body, "payload_digest": _digest(body),
                "human_text": _human_prose(payload), "cursor_readback_json": readback.to_json(),
                "presence_baseline_json": presence.to_json(), "boot_id": self._boot_id, "source": source,
                "evidence_json": {**rec.evidence, "cursor_readback": readback.to_json(),
                                  "presence_baseline": presence.to_json(), "outcome": None},
            })
        finally:
            await self._scheduler.release(grant)

        after = await self.get(rec.action_id)
        await self._record_transition(
            rec, after, generation, reason=f"submitted:{decision}",
            evidence={"decision": decision, "delivery_id": after.delivery_id}, activity_kind="action.submitted",
            human_text=after.human_text, params={"decision": decision, "type": rec.type,
                                                 "delivery_id": after.delivery_id, "slot_key": slot_key, "user": user})
        return self._receipt(after, grant.generation)

    def _assert_file_answer_session(self, slot_key: str) -> None:
        """A file reply must never bypass a host widget parked on its own answer endpoint."""
        cards = self._host.pending_question_cards(slot_key)
        capability = self._host.capabilities().get("pending_questions")
        if capability is None or not capability.available:
            _refuse("host_unavailable", "the host's pending questions could not be inspected")
        if any("ask_id" in card for card in cards):
            _refuse("slot_busy", "answer the active native question in the conversation", slot_key=slot_key,
                    reasons=["native_question_pending"])
        view = self._host.slot(slot_key)
        if view is None or busy_reasons(view):
            _refuse("slot_busy", "the session changed before the answers could be dispatched", slot_key=slot_key)

    # ---- delivery reports (phase three) ----------------------------------- #

    async def record_delivery(
        self, action_id: str, *, delivery_id: str, outcome: str, http_status: int | None,
        receipt: Mapping[str, Any] | None,
    ) -> tuple[ActionRecord, bool]:
        """Record what the browser observed when it sent; idempotent per ``delivery_id``. ``delivered`` is believed
        (the host is the authority on its own queue), ``not_delivered`` never is. A late report is kept as
        evidence, only a proven ``not_delivered`` moves the row, and a late ``delivered`` closes retry (C24, R16)."""
        if outcome not in ("delivered", "not_delivered", "uncertain"):
            _refuse("bad_param", f"unknown delivery outcome {outcome!r}", outcome=outcome)
        rec = await self.get(action_id)
        if not rec.delivery_id or rec.delivery_id != delivery_id:
            _refuse("delivery_ack_invalid", "this delivery id does not belong to this action",
                    action_id=action_id, delivery_id=delivery_id)
        body = dict(receipt or {})
        if rec.status == "Queued":
            # Re-queued by `retry`, so this is a report for the old attempt: accepting it would let a stale tab
            # advance an action that has no delivery.
            _refuse("delivery_ack_invalid", "this action has no delivery in flight", action_id=action_id,
                    status=rec.status, delivery_id=delivery_id)
        if rec.status == "Delivering" and outcome == "delivered":
            return await self._accept_delivered(rec, body), False
        if rec.status in ("Delivering", "ReconciliationRequired") and outcome == "not_delivered":
            return await self._accept_not_delivered(rec, body, http_status), False
        if rec.status == "Delivering":
            return await self._transition(rec, "ReconciliationRequired", reason="delivery_report_uncertain", evidence={
                "receipt": body, "outcome": "uncertain", "http_status": http_status}), False
        if rec.status == "Delivered":
            if outcome != "delivered":
                raise IllegalTransition("the host already confirmed this delivery",
                                        details={"from": rec.status, "outcome": outcome, "action_id": action_id})
            return rec, True
        if rec.status == "ReconciliationRequired":
            if outcome == "uncertain":
                return rec, True
            updated = await self.patch_evidence(rec, {"delivery_confirmed": True, "receipt": body,
                                                      "outcome": "delivered", "late_report_at": self._clock.iso()})
            await self._activity.record(
                kind="action.delivery", severity="attention", repo_id=rec.repo_id, space=rec.space,
                intent_dir=rec.intent_dir, stage=rec.stage, action_id=rec.action_id,
                params={"outcome": "delivered", "late": True, "status": rec.status})
            return updated, False
        # Terminal, or NotDelivered: the report is kept so "the browser said it failed" has an answer.
        late = [*(rec.evidence.get("late_reports") or ()),
                {"at": self._clock.iso(), "outcome": outcome, "http_status": http_status, "receipt": body}]
        patch: dict[str, Any] = {"late_reports": late[-10:]}
        if outcome == "delivered":
            patch["delivery_confirmed"] = True
        return await self.patch_evidence(rec, patch), True

    async def _accept_delivered(self, rec: ActionRecord, receipt: Mapping[str, Any]) -> ActionRecord:
        """``Delivered``. The turn started now unless the host queued the message behind a running turn; then
        it may wait ``DELIVERED_QUEUED_WAIT_FACTOR`` turn timeouts, and only the reconciler can see it start
        (P22). The turn timeout counts from the turn's start, never from the ack."""
        if receipt.get("ok") is not True:
            _refuse("delivery_ack_invalid", "a delivered report must carry the host's ok receipt",
                    receipt=dict(receipt))
        now, queued, timeout = self._clock.iso(), bool(receipt.get("queued")), self._turn_timeout()
        started = "queued_at" if queued else "turn_started_at"
        return await self._transition(
            rec, "Delivered", reason="delivery_report_delivered",
            evidence={"receipt": dict(receipt), "outcome": "delivered", started: now},
            values={"delivered_at": now,
                    "deadline_at": self._deadline(timeout * C.DELIVERED_QUEUED_WAIT_FACTOR if queued else timeout)})

    async def _accept_not_delivered(
        self, rec: ActionRecord, receipt: Mapping[str, Any], http_status: int | None,
    ) -> ActionRecord:
        """``NotDelivered`` is a proof (C24): an authoritative 4xx — or, while still ``Delivering``, a verified
        preflight code — and all four checks. The checks run even for a weak receipt: "here is what we could
        and could not see" is the difference between a user who can act and one who is only told no."""
        late = rec.status == "ReconciliationRequired"
        authoritative = _authoritative_4xx(http_status, receipt)
        preflight = not late and str(receipt.get("code") or "") in CLIENT_PREFLIGHT_CODES
        checks = await self._prove_not_delivered(rec, receipt)
        evidence = {"receipt": dict(receipt), "http_status": http_status,
                    **({"late_report_at": self._clock.iso()} if late else {})}
        if (authoritative or preflight) and all(checks.values()):
            return await self._transition(rec, "NotDelivered", reason="not_delivered_proven", evidence={
                **evidence, "outcome": "not_delivered", "checks": checks,
                **({"authoritative_4xx": True} if authoritative else {}),
                **({"client_preflight": receipt.get("code")} if preflight else {})})
        evidence["authoritative_4xx"] = authoritative
        if late:
            await self.patch_evidence(rec, evidence)
        else:
            await self._transition(rec, "ReconciliationRequired", reason="not_delivered_unproven",
                                   evidence={**evidence, "outcome": "uncertain", "checks": checks})
        _refuse("not_delivered_unproven", "this decision cannot be proven undelivered", checks=checks,
                reason="evidence" if authoritative or preflight else "receipt_not_authoritative")

    async def _prove_not_delivered(self, rec: ActionRecord, receipt: Mapping[str, Any]) -> dict[str, bool]:
        """The four proofs of absence (C24); an unknown is *not proven*. ``row_absent`` searches every slot of the
        repository (a message in another session was still delivered); ``disk_baseline_unchanged`` is the half
        that survives a restart, which empties the host's in-memory transcript (R02)."""
        repo = await self._repo(rec.repo_id)
        slot_key, since = rec.slot_key or "", rec.delivering_at or rec.created_at

        def find(key: str) -> Any:
            return self._host.find_delivery_row(key, since_ts=since, delivery_id=rec.delivery_id or "",
                                                wire_text=rec.wire_text or "")

        found = find(slot_key) if slot_key else None
        if found is None:
            found = next((row for view in self._host.slots_for_project(repo.canonical_path) if view.key != slot_key
                          for row in [find(view.key)] if row is not None), None)
        view = self._host.slot(slot_key) if slot_key else None
        # Asked once, after the reads it qualifies: an unreadable slot table makes `found is None` and
        # `view is None` statements about Studio's eyesight, and the step after NotDelivered is Retry.
        can_see = self._host.can_prove_absence()
        if str(receipt.get("code") or "") == "slot_missing":
            # The browser's slot is gone, so nothing ran in it — provided the slot table could be read.
            slot_idle = view is None and can_see
        else:
            # A slot that vanished without that code is the restart case: missing evidence, never proof.
            slot_idle = view is not None and not view.running and not _at_or_after(view.last_turn_ts, since)
        if rec.presence_baseline is None:
            # host_control sends nothing to the model, so it has no disk half; for a prompt a missing baseline
            # is missing evidence.
            disk_unchanged = rec.type == "force_stop"
        else:
            snap = await self._snapshot(repo, rec.space, rec.intent_dir)
            fresh = await asyncio.to_thread(self._reader.presence_baseline, Path(repo.canonical_path), rec.space,
                                            rec.intent_dir, snap.audit, snap.state_sha256)
            disk_unchanged = _presence_comparable(fresh) == _presence_comparable(rec.presence_baseline)
        return {"row_absent": found is None and can_see, "slot_idle": bool(slot_idle),
                "disk_baseline_unchanged": bool(disk_unchanged), "boot_unchanged": rec.boot_id == self._boot_id}

    # ---- lifecycle -------------------------------------------------------- #

    async def retry(self, action_id: str, *, user: str) -> ActionRecord:
        """Re-queue a proven-undelivered decision (the same action: at-most-once is intact, nothing was
        enqueued), or start a fresh attempt after a failed command (a new action: the failure is history)."""
        rec = await self.get(action_id)
        if rec.status == "NotDelivered":
            if rec.evidence.get("delivery_confirmed"):
                _refuse("retry_not_allowed", "a later report confirmed this decision was delivered; it must not be "
                        "replayed", action_id=action_id)
            return await self._transition(rec, "Queued", reason="retry", evidence={"retried_by": user},
                                          activity_kind="action.retried")
        if rec.status != "Failed" or rec.type not in PROMPT_COMMAND_TYPES:
            _refuse("retry_not_allowed", f"an action in {rec.status} cannot be retried", action_id=action_id,
                    status=rec.status, type=rec.type)
        await self._reset_breakers(rec.repo_id, rec.intent_key)
        repo = await self._repo(rec.repo_id)
        snap = await self._snapshot(repo, rec.space, rec.intent_dir)
        created = await self.create_command(rec.type, repo, rec.space, rec.intent_dir, snap=snap, source=rec.source)
        await self.patch_evidence(rec, {"superseded": True, "retried_as": created.action_id})
        await self._activity.record(
            kind="action.retried", severity="info", repo_id=rec.repo_id, space=rec.space, intent_dir=rec.intent_dir,
            stage=rec.stage, action_id=created.action_id,
            params={"retried": rec.action_id, "type": rec.type, "user": user})
        return created

    async def cancel(self, action_id: str, *, user: str) -> ActionRecord:
        """Cancel only where cancelling loses no decision. A command the user queued is theirs to withdraw; a
        derived card exists because a boundary is on disk, so dismissing it would hide a ``[?]`` the workflow
        still waits on (FR-ACT-007, C33) — unless it provably never left (``NotDelivered``)."""
        rec = await self.get(action_id)
        if rec.status not in (SUBMITTABLE_STATUSES if rec.type in COMMAND_ACTION_TYPES else {"NotDelivered"}):
            _refuse("cancel_not_safe", "this card cannot be cancelled from its current state", action_id=action_id,
                    status=rec.status, type=rec.type)
        return await self._transition(rec, "Cancelled", reason="cancelled_by_user", evidence={"cancelled_by": user})

    async def resolve(
        self, action_id: str, *, decision: str, payload: Mapping[str, Any], user: str,
    ) -> tuple[ActionRecord, ActionRecord | None]:
        """Apply a decision that never leaves Studio; returns ``(this action, created action | None)``. Nothing was
        sent, so each ends its card by cancelling it; a condition still on disk derives a fresh card next scan."""
        rec = await self.get(action_id)
        # The queue type too: an uncertain delivery offers reconcile / mark undelivered / resubmit, exactly what
        # card() shows, and the stored type alone would refuse all three.
        if decision not in set(DECISIONS.get(rec.type, ())) | set(DECISIONS.get(rec.queue_type, ())):
            _refuse("invalid_decision", f"{rec.type} cards do not offer {decision!r}", decision=decision,
                    type=rec.type, queue_type=rec.queue_type)
        if decision not in STUDIO_ONLY_DECISIONS:
            _refuse("invalid_decision", f"{decision!r} is not a studio-only decision", decision=decision,
                    lane=lane_for(decision))
        handler = getattr(self, f"_resolve_{decision}", None)
        if handler is None:
            _refuse("invalid_decision", f"unhandled decision {decision!r}", decision=decision)
        return await handler(rec, payload, user)

    @staticmethod
    def _require_reconciliation(rec: ActionRecord, to_status: str) -> None:
        """``mark_not_delivered`` and ``resubmit`` exist only once the reconciler could not tell (R07), and never for
        a decision seen reaching the conversation: that is resolved from disk, and a client 4xx describes one HTTP
        attempt where the confirmation describes the host's own queue."""
        if rec.status != "ReconciliationRequired":
            raise IllegalTransition(f"{to_status} is only reachable by hand from ReconciliationRequired",
                                    details={"from": rec.status, "to": to_status, "action_id": rec.action_id})
        if rec.evidence.get("delivery_confirmed"):
            _refuse("not_delivered_unproven", "this decision was delivered; it is resolved from disk, never replayed",
                    action_id=rec.action_id, delivery_confirmed=True)

    async def _resolve_mark_not_delivered(self, rec: ActionRecord, payload: Mapping[str, Any], user: str) -> Any:
        """Close a decision as never delivered with evidence, never on the human's word: an authoritative 4xx
        they can see, or the reconciler's own four proofs. Anything less, because Retry comes next."""
        self._require_reconciliation(rec, "NotDelivered")
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), Mapping) else {}
        receipt = evidence.get("receipt") if isinstance(evidence.get("receipt"), Mapping) else {}
        by_receipt = _authoritative_4xx(evidence.get("http_status"), receipt)
        stored = rec.evidence
        checks = {"authoritative_4xx": by_receipt, **{
            key: stored.get(key) if key == "slot_ran_since" else bool(stored.get(key)) for key in ABSENCE_PROOF}}
        by_reconciler = all(checks[key] is value for key, value in ABSENCE_PROOF.items())
        if not (by_receipt or by_reconciler):
            _refuse("not_delivered_unproven", "marking a decision undelivered needs a 4xx receipt or the "
                    "reconciler's proof", checks=checks)
        return await self._transition(rec, "NotDelivered", reason="marked_not_delivered", evidence={
            "outcome": "not_delivered", "marked_by": user, "authoritative_4xx": by_receipt,
            **({"receipt": dict(receipt)} if receipt else {})}), None

    async def _resolve_resubmit(self, rec: ActionRecord, payload: Mapping[str, Any], user: str) -> Any:
        """Offer the same decision again as a **new** action, after the human read the evidence. The echoed
        ``acknowledged_evidence_sha256`` makes that checkable: changed evidence is ``action_stale``."""
        self._require_reconciliation(rec, "Queued")
        card = await self.card(rec, None)
        expected = card["acknowledged_evidence_sha256"]
        if str(payload.get("acknowledged_evidence_sha256") or "") != expected:
            _refuse("action_stale", "the evidence changed since it was shown; read it again before resubmitting",
                    expected=expected, card=card)
        # Ordered, not atomic: the old row leaves the live-dedupe index before the new row takes its key. A
        # failed insert leaves the boundary cardless for one tick, and `upsert_derived` re-creates it.
        superseded = await self._transition(rec, "Failed", reason="superseded_by_resubmit",
                                            evidence={"superseded": True, "resubmitted_by": user})
        evidence = {k: v for k, v in rec.evidence.items() if k not in ("receipt", "checks", "late_reports")}
        evidence["superseded_action_id"] = rec.action_id
        created = await self._insert(rec.type, rec, risk_class=rec.risk_class, captured=rec.captured.to_json(),
                                     evidence=evidence, dedupe_key=rec.dedupe_key, waiting_since=rec.waiting_since,
                                     source=rec.source, resolved_repo_identity=rec.resolved_repo_identity)
        await self.patch_evidence(superseded, {"resubmitted_as": created.action_id})
        return await self.get(rec.action_id), created

    async def _resolve_reconcile(self, rec: ActionRecord, payload: Mapping[str, Any], user: str) -> Any:
        if self.reconciler is None:
            _refuse("internal_error", "the reconciler is not attached to this broker", action_id=rec.action_id)
        updated = await self.reconciler.reconcile_now(rec.action_id)
        return (updated if isinstance(updated, ActionRecord) else await self.get(rec.action_id)), None

    async def _resolve_acknowledge(self, rec: ActionRecord, payload: Mapping[str, Any], user: str) -> Any:
        """Close a recovery card the human has read. ``interrupted_at`` is cleared only here and only for the
        interruption (C28, P17): PRD §11.2 requires a takeover after a mid-stage interruption, so a Run that
        cleared it would be the removed one-click way out of ``Interrupted``."""
        codes = _finding_codes(rec.evidence)
        if "interrupted_session" in codes or rec.card_meta.get("reason") == "interrupted_session":
            await self._sessions.set_interrupted(rec.repo_id, rec.space, rec.intent_dir, None)
        return await self._cancel_if_open(rec, "acknowledged", {"acknowledged_by": user}), None

    async def _resolve_rebind_session(self, rec: ActionRecord, payload: Mapping[str, Any], user: str) -> Any:
        repo = await self._repo(rec.repo_id)
        slot_key = str(payload.get("slot_key") or "")
        if not slot_key:
            _refuse("bad_body", "rebind_session needs a slot_key", decision="rebind_session")
        await self._sessions.takeover(repo, rec.space, rec.intent_dir, slot_key=slot_key)
        return await self._transition(rec, "Cancelled", reason="session_rebound",
                                      evidence={"slot_key": slot_key, "by": user}), None

    async def _resolve_keep_paused(self, rec: ActionRecord, payload: Mapping[str, Any], user: str) -> Any:
        await self._sessions.set_paused(rec.repo_id, rec.space, rec.intent_dir, True)
        if rec.status in SUBMITTABLE_STATUSES:
            return await self._transition(rec, "Cancelled", reason="kept_paused",
                                          evidence={"kept_paused": True, "by": user}), None
        return await self.patch_evidence(rec, {"kept_paused": True}), None

    async def _resolve_retry_now(self, rec: ActionRecord, payload: Mapping[str, Any], user: str) -> Any:
        """Reset the breaker and start the failed work again (PRD §11.4). A ``failure`` card names the action
        that failed, so the retry is that action's; a ``circuit_breaker`` card names a fingerprint, so the reset
        is the whole of it. Retry is the way out of an open breaker, never another thing it blocks."""
        await self._reset_breakers(rec.repo_id, rec.intent_key)
        created: ActionRecord | None = None
        related = rec.card_meta.get("related_action_id")
        if isinstance(related, str) and related:
            try:
                created = await self.retry(related, user=user)
            except StudioError as exc:
                if exc.code not in ("retry_not_allowed", "action_not_found"):
                    raise
        evidence = {"retried_by": user, **({"created": created.action_id} if created else {})}
        return await self._cancel_if_open(rec, "retry_now", evidence), created

    async def _resolve_run_now(self, rec: ActionRecord, payload: Mapping[str, Any], user: str) -> Any:
        repo = await self._repo(rec.repo_id)
        snap = await self._snapshot(repo, rec.space, rec.intent_dir)
        created = await self.create_command("run", repo, rec.space, rec.intent_dir, snap=snap)
        return await self._cancel_if_open(rec, "run_now", {"created": created.action_id}), created

    async def _resolve_pick_intent(self, rec: ActionRecord, payload: Mapping[str, Any], user: str) -> Any:
        """Point the repository cursor at an intent under the admin lease, with no prompt: ``/aidlc intent``
        typed into the conversation would mint a ``HUMAN_TURN`` for a navigation step (C22/R01)."""
        intent_dir = str(payload.get("intent_dir") or "")
        if not C.INTENT_DIR_RE.match(intent_dir):
            _refuse("bad_param", "pick_intent needs a valid intent_dir", intent_dir=intent_dir)
        repo = await self._repo(rec.repo_id)
        rows = await asyncio.to_thread(self._reader.registry, Path(repo.canonical_path), rec.space)
        registry_uuid = next((row.uuid for row in rows if row.dir_name == intent_dir), None)
        grant = await self._scheduler.acquire_admin(repo, operation_type="cursor_switch",
                                                    transaction_id=self._ids.new("tx"))
        try:
            readback = await asyncio.to_thread(
                self._engine.switch_cursor_sync, Path(repo.canonical_path), repo.engine_dir or C.STUDIO_HARNESS_DIR,
                rec.space, intent_dir, registry_uuid, lease_generation=grant.generation, repo_id=repo.repo_id)
        finally:
            await self._scheduler.release(grant)
        if not readback.ok:
            _refuse("cursor_mismatch", "the cursor did not move to that intent", mismatch=list(readback.mismatch),
                    readback=readback.to_json())
        return await self._cancel_if_open(rec, "cursor_switched", {
            "intent_dir": intent_dir, "by": user, "readback": readback.to_json()}), None

    async def _cancel_if_open(self, rec: ActionRecord, reason: str, evidence: Mapping[str, Any]) -> ActionRecord:
        """Cancel a card that is still open; only record evidence on one that is already settled."""
        if rec.status in SUBMITTABLE_STATUSES:
            return await self._transition(rec, "Cancelled", reason=reason, evidence=dict(evidence))
        return await self.patch_evidence(rec, {"resolution_reason": reason, **evidence})

    # ---- transitions (the reconciler's entry points) ---------------------- #

    async def transition(
        self, action_id: str, to_status: str, *, expected_generation: int, reason: str, evidence: Mapping[str, Any],
    ) -> ActionRecord:
        """Move one action, validated against ``ALLOWED_TRANSITIONS`` and the caller's generation. A refusal is never
        retried here: something else already moved the record, and two writers cannot both be right about it."""
        rec = await self.get(action_id)
        if rec.status_generation != int(expected_generation):
            raise StaleGeneration("the action changed under this transition", details={
                "expected": int(expected_generation), "actual": rec.status_generation, "action_id": action_id})
        return await self._transition(rec, to_status, reason=reason, evidence=dict(evidence))

    async def patch_evidence(
        self, rec: ActionRecord, patch: Mapping[str, Any], *, values: Mapping[str, Any] | None = None,
    ) -> ActionRecord:
        """Merge ``patch`` into the evidence (and ``values`` into the row) without touching the status, under the
        same generation CAS; a lost CAS is not an error. Nothing is written when nothing changes: every write bumps
        the generation a tab holds, and the reconciler re-stamps unchanged evidence every tick."""
        merged = {**rec.evidence, **patch}
        if merged != rec.evidence or values:
            try:
                await asyncio.to_thread(self._storage.cas_update, _ACTIONS, rec.action_id, rec.status_generation,
                                        {"evidence_json": merged, **(values or {})})
            except StaleGeneration:
                logger.debug("aidlc-studio: evidence patch for %s lost the CAS", rec.action_id)
        return await self.get(rec.action_id)

    async def record_failure(
        self, rec: ActionRecord, *, fingerprint: str, fingerprint_class: str, summary: str, evidence: Mapping[str, Any],
    ) -> tuple[ActionRecord, bool]:
        """Fail an action and count it against the intent's circuit breaker (§11.4): transient classes get three
        attempts, a deterministic one opens at once; an opening yields one card and one notification. The strike is
        written only after the status CAS, or a lost race (the tick and a "Reconcile from disk" click) would leave
        a phantom strike that could open the breaker on a healthy intent."""
        key = f"{rec.repo_id}:{rec.intent_key}:{fingerprint}"
        row = await asyncio.to_thread(self._storage.get, _BREAKERS, key) or {}
        count = int(row.get("count") or 0) + 1
        should_open = fingerprint_class not in C.TRANSIENT_FINGERPRINT_CLASSES or count >= C.BREAKER_TRANSIENT_THRESHOLD
        already_open = bool(row.get("opened_at"))
        strike = {"count": count, "opened_at": row.get("opened_at") or (self._clock.iso() if should_open else None),
                  "reason": summary, "fingerprint_class": fingerprint_class, "last_error_json": dict(evidence)}
        failure = {"fingerprint": fingerprint, "fingerprint_class": fingerprint_class, "summary_key": summary,
                   "count": count, "breaker_open": should_open or already_open}
        updated = await self._transition(rec, "Failed", reason=summary, evidence={"failure": failure, **evidence},
                                         values={"retry_fingerprint": fingerprint})
        if row:
            await asyncio.to_thread(self._storage.update, _BREAKERS, key, strike)
        else:
            await asyncio.to_thread(self._storage.insert, _BREAKERS, {"key": key, **strike})
        opened = should_open and not already_open
        if opened:
            await self._open_breaker_card(updated, failure)
        return updated, opened

    async def _transition(
        self, rec: ActionRecord, to_status: str, *, reason: str, evidence: Mapping[str, Any],
        values: Mapping[str, Any] | None = None, activity_kind: str | None = None,
    ) -> ActionRecord:
        """Validate, CAS, leave a trail: the one status writer besides ``submit``'s ``Delivering`` CAS."""
        allowed = ALLOWED_TRANSITIONS.get(rec.status, frozenset())
        if to_status not in allowed:
            raise IllegalTransition(f"{rec.status} → {to_status} is not a legal action transition", details={
                "from": rec.status, "to": to_status, "action_id": rec.action_id, "allowed": sorted(allowed)})
        now = self._clock.iso()
        payload: dict[str, Any] = {"status": to_status, "evidence_json": {**rec.evidence, **evidence}, **(values or {})}
        if to_status in _RESOLUTION_KIND:
            payload.setdefault("resolved_at", now)
            payload.setdefault("resolution_json", {"kind": _RESOLUTION_KIND[to_status], "resolved_at": now,
                                                   "evidence": dict(evidence) or None, "reason": reason})
        if to_status in _RESOLUTION_KIND or to_status == "Queued":
            payload.setdefault("deadline_at", None)
        generation = await asyncio.to_thread(self._storage.cas_update, _ACTIONS, rec.action_id, rec.status_generation,
                                             payload)
        after = await self.get(rec.action_id)
        await self._record_transition(rec, after, generation, reason=reason, evidence=evidence,
                                      activity_kind=activity_kind)
        return after

    async def _record_transition(
        self, before: ActionRecord, after: ActionRecord, generation: int, *, reason: str, evidence: Mapping[str, Any],
        activity_kind: str | None = None, human_text: str | None = None, params: Mapping[str, Any] | None = None,
    ) -> None:
        """The audit half of a transition: the row, the event, the Activity line. Written after the CAS, never
        before: a trail claiming a status the store did not commit is worse than a missing line."""
        await asyncio.to_thread(self._storage.insert, _TRANSITIONS, {
            "action_id": after.action_id, "from_status": before.status, "to_status": after.status,
            "generation": int(generation), "at": self._clock.iso(), "reason": reason, "evidence_json": dict(evidence)})
        await self._events.publish("action.updated", {
            "action_id": after.action_id, "repo_id": after.repo_id, "intent_key": after.intent_key, "type": after.type,
            "status": after.status, "status_generation": after.status_generation, "from_status": before.status,
            "reason": reason})
        kind = activity_kind or _ACTIVITY_KIND_BY_STATUS.get(after.status, "action.updated")
        await self._activity.record(
            kind=kind, severity=_ACTIVITY_SEVERITY_BY_STATUS.get(after.status, "info"), repo_id=after.repo_id,
            space=after.space, intent_dir=after.intent_dir, stage=after.stage, action_id=after.action_id,
            session_key=after.session_key,
            params=dict(params or {"status": after.status, "reason": reason, "type": after.type}),
            human_text=human_text if kind == "action.submitted" else None)

    # ---- the card (§2.11) -------------------------------------------------- #

    async def card(self, rec: ActionRecord, snap: "IntentSnapshot | None") -> dict[str, Any]:
        """The queue/detail object for one action (§2.11). Live evidence is joined only while nothing was
        dispatched: an open card shows what is true now, a card on the wire what the human decided against."""
        repo_row = await asyncio.to_thread(self._storage.get, _REPOS, rec.repo_id) or {}
        meta, open_card = rec.card_meta, rec.status in SUBMITTABLE_STATUSES
        captured, stage_ref = rec.captured, meta.get("stage_ref")
        evidence = {key: rec.evidence.get(key) for key in EVIDENCE_SNAPSHOT_KEYS}
        intent = {"intent_dir": rec.intent_dir, "intent_key": rec.intent_key, "slug": rec.intent_dir,
                  "uuid": rec.intent_uuid, "title": None}
        if snap is not None:
            # `summarize` owns the slug/title rules, so the card names the intent exactly as the rail does.
            summary = self._projection.summarize(snap)
            intent.update(slug=summary.slug, uuid=summary.uuid or rec.intent_uuid, title=summary.title)
            if open_card:
                captured = Captured.from_json(self._projection.captured(snap, rec.stage))
                # Binding and slot are re-read: the UI disables the send exactly when `evidence.session.slot_key`
                # is absent, and without the slot every conversation would read idle. `stage=rec.stage`, never
                # the current stage (A20): with two `[?]` rows the person approving one must not be shown the
                # other's files and verdict under the right stage's name.
                binding = await self._sessions.get(rec.repo_id, rec.space, rec.intent_dir)
                slot = self._host.slot(binding.slot_key) if binding.slot_key else None
                live = self._projection.evidence(snap, findings=(), binding=binding, slot=slot,
                                                 stage=rec.stage).to_json()
                evidence.update({key: live.get(key) for key in LIVE_REFRESHED_EVIDENCE_KEYS})
                stage_ref = self._projection.stage_ref(snap, rec.stage) or stage_ref
        evidence.update(presence_baseline=_plain(rec.presence_baseline), presence=rec.evidence.get("presence"),
                        cursor_readback=_plain(rec.cursor_readback))
        # Every §2.11 key has its kind of empty value: the UI type has no optional members.
        if not isinstance(evidence.get("audit"), Mapping):
            evidence["audit"] = {}
        for key in ("acceptance_criteria", "findings", "artifacts"):
            evidence[key] = list(evidence.get(key) or [])

        queue_type = rec.queue_type
        severity = str(meta.get("severity") or SEED_SEVERITY.get(rec.type, "info"))
        if queue_type != rec.type:
            severity = SEED_SEVERITY.get(queue_type, severity)
        questions = evidence.get("questions") or {}
        decisions = self._offered_decisions(rec, meta)
        if open_card and (rec.type == "question" and questions.get("unsupported_pending_count", 0) or (
                rec.type in ("run", "resume") and snap is not None
                and run_unavailable_reason(snap, stage=rec.stage, unit=rec.unit))):
            decisions = []
        availability = str(repo_row.get("availability") or "unavailable")
        if availability != "available":
            # A saved gate is still evidence, but cannot authorize a prompt against an unreadable repository.
            decisions = [d for d in decisions if d["lane"] != "human_lane"]
        delivery = self._delivery_json(rec)
        checkpoint = questions.get("pending_checkpoint") if questions.get("pending_count") == 0 else None
        checkpoint = checkpoint if rec.type == "question" and checkpoint in C.QUESTION_CHECKPOINT_KINDS else None
        params = meta.get("headline_params") or {}
        variant = meta.get("consequence_variant")
        # A checkpoint names its own consequence unless the seed chose a more specific one: a plan asked in a
        # replaced session cannot be approved from here, so "your reply approves the plan" would be false.
        specific = variant if variant not in (None, checkpoint, "advances_turn") else None
        consequence = None
        if checkpoint or variant:
            key = (f"{rec.type}.consequence.{specific}" if specific
                   else f"question.consequence.{checkpoint}" if checkpoint else f"{rec.type}.consequence.{variant}")
            consequence = {"key": f"action.{key}", "params": dict(params)}
        return {
            "action_id": rec.action_id, "type": rec.type, "queue_type": queue_type, "status": rec.status,
            "status_generation": rec.status_generation, "source": rec.source, "risk_class": rec.risk_class,
            "severity": severity, "priority_group": priority_group_for(queue_type),
            "repo": {
                "repo_id": rec.repo_id, "label": str(repo_row.get("label") or ""),
                "canonical_path": str(repo_row.get("canonical_path") or ""), "availability": availability,
                "availability_detail": repo_row.get("availability_detail"), "archived": bool(repo_row.get("archived")),
            },
            "space": rec.space, "intent": intent, "stage": dict(stage_ref) if isinstance(stage_ref, Mapping) else None,
            "headline": {
                "key": f"action.question.{checkpoint}.headline" if checkpoint else f"action.{rec.type}.headline",
                "params": dict(params),
            },
            "consequence": consequence, "waiting_since": rec.waiting_since, "created_at": rec.created_at,
            "updated_at": rec.updated_at,
            "primary": {key: decisions[0][key] for key in ("decision", "label_key")} if decisions else None,
            "decisions": decisions, "captured": captured.to_json(), "evidence": evidence, "delivery": delivery,
            "resolution": self._resolution_json(rec), "failure": _failure_json(rec), "install": _install_json(rec),
            "budget": None, "advisor": await self._advisor_json(rec),
            "deep_link": f"{C.DEEP_LINK_BASE}?view=actions&action={rec.action_id}", "dedupe_key": rec.dedupe_key,
            "human_text_present": bool(rec.human_text),
            "acknowledged_evidence_sha256": _digest({"delivery": delivery, "audit": evidence.get("audit"),
                                                     "presence": evidence.get("presence")}),
            "submitted_answers": _submitted_answers(rec),
        }

    def _offered_decisions(self, rec: ActionRecord, meta: Mapping[str, Any]) -> list[dict[str, Any]]:
        """What this card may do right now. An uncertain delivery offers the uncertain vocabulary, not the gate
        buttons it was born with; a card in flight offers nothing, since every decision would be refused, and a
        button that always fails is worse than no button."""
        uncertain = rec.status in UNCERTAIN_STATUSES
        if uncertain:
            names: Sequence[str] = DECISIONS["delivery_uncertain"]
        elif rec.status in SUBMITTABLE_STATUSES:
            stored = meta.get("decisions")
            names = ([str(d) for d in stored] if isinstance(stored, Sequence) and not isinstance(stored, (str, bytes))
                     else DECISIONS.get(rec.type, ()))
        elif rec.status == "Failed":
            names = [d for d in DECISIONS.get(rec.type, ()) if d in ("retry_now", "keep_paused")]
        else:
            names = ()
        confirmed = bool(rec.evidence.get("delivery_confirmed"))
        return [
            {"decision": name, "label_key": f"decision.{name}.label", "lane": lane_for(name),
             "wire_text_template": _template_for(name), "requires": list(DECISION_REQUIRES.get(name, ()))}
            for name in names
            if (uncertain or name in DECISIONS.get(rec.type, ()))
            # A missing workflow receipt does not undo a confirmed message delivery.
            and not (confirmed and name in ("mark_not_delivered", "resubmit"))
            # Refused from every status but ReconciliationRequired (R07); a recovery card is always Queued.
            and not (name == "mark_not_delivered" and rec.status != "ReconciliationRequired")
            # No client sends the `slot_key` it needs; rebinding lives on the session panel (§2.13).
            and name != "rebind_session"
        ]

    @staticmethod
    def _delivery_json(rec: ActionRecord) -> dict[str, Any]:
        evidence, host = rec.evidence, _host_call(rec)
        transcript, receipt = evidence.get("transcript_row"), evidence.get("receipt")
        return {
            "lane": rec.lane, "delivery_id": rec.delivery_id, "slot_key": rec.slot_key, "session_key": rec.session_key,
            "wire_text": rec.wire_text, "host": host.to_json() if host else None, "delivering_at": rec.delivering_at,
            "delivered_at": rec.delivered_at, "queued_at": evidence.get("queued_at"),
            # `Delivered` covers the running turn too, and the UI says "processing" once it started. Omitted
            # until then, so every card that never had a turn keeps its wire shape (and evidence digest).
            **({"turn_started_at": evidence["turn_started_at"]} if evidence.get("turn_started_at") else {}),
            "deadline_at": rec.deadline_at,
            "receipt": receipt if isinstance(receipt, Mapping) else None, "outcome": evidence.get("outcome"),
            "delivery_confirmed": bool(evidence.get("delivery_confirmed")),
            "boot_id_unchanged": evidence.get("boot_id_unchanged"),
            "transcript_row": dict(transcript) if isinstance(transcript, Mapping) else None,
            "slot_ran_since": evidence.get("slot_ran_since"),
            "disk_baseline_unchanged": evidence.get("disk_baseline_unchanged"),
        }

    @staticmethod
    def _resolution_json(rec: ActionRecord) -> dict[str, Any]:
        stored = rec.resolution or {}
        return {
            "kind": stored.get("kind") or _RESOLUTION_KIND.get(rec.status), "resolved_at": rec.resolved_at,
            "evidence": stored.get("evidence") if isinstance(stored.get("evidence"), Mapping) else None,
            "reason": stored.get("reason") or rec.evidence.get("resolution_reason"),
        }

    async def _advisor_json(self, rec: ActionRecord) -> dict[str, Any]:
        rows = await asyncio.to_thread(self._storage.select, _DRAFTS, {"action_id": rec.action_id},
                                       order_by="created_at DESC, draft_id DESC", limit=1)
        return {"latest_draft_id": rows[0].get("draft_id") if rows else None,
                "status": rows[0].get("status") if rows else None}

    # ---- internals -------------------------------------------------------- #

    async def _repo(self, repo_id: str) -> RepoRecord:
        row = await asyncio.to_thread(self._storage.get, _REPOS, repo_id)
        if row is None:
            _refuse("repo_not_found", "no such repository", repo_id=repo_id)
        return RepoRecord.from_row(row)

    async def _assert_consistent(self, repo: RepoRecord, snap: "IntentSnapshot", binding: Any) -> list[Any]:
        """No blocking finding and a stable read; returns the findings. A ``lease_conflict`` is runtime
        ownership, not a contradiction between AI-DLC files, so it names the holder as ``repo_busy``."""
        findings = await self._findings(repo, snap, binding)
        blocking = self._consistency.blocking(findings)
        disk_findings = [finding for finding in blocking if finding.code != "lease_conflict"]
        if disk_findings:
            _refuse("state_inconsistent", "the intent's files contradict each other",
                    findings=[finding.to_json() for finding in disk_findings])
        if blocking:
            _refuse("repo_busy", "another operation holds this repository", repo_id=repo.repo_id,
                    owner=dict(blocking[0].params))
        if not snap.stable:
            _refuse("unstable_read", "the record changed while it was being read", intent_key=snap.intent_key)
        return findings

    async def _snapshot(self, repo: RepoRecord, space: str, intent_dir: str) -> "IntentSnapshot":
        """One coherent read of the intent off the loop: on the reconciler's scan pool when injected (R11),
        else the default executor — blocking the loop is the one thing that is never acceptable."""
        if self._scan_pool is not None:
            return await asyncio.get_running_loop().run_in_executor(
                self._scan_pool, self._projection.snapshot, repo, space, intent_dir)
        return await asyncio.to_thread(self._projection.snapshot, repo, space, intent_dir)

    async def _findings(self, repo: RepoRecord, snap: "IntentSnapshot", binding: Any) -> list[Any]:
        """The intent's consistency findings, with the facts the engine cannot see itself."""
        live = await self.list_live(repo_id=repo.repo_id, space=snap.space, intent_dir=snap.intent_dir,
                                    include_revision=True)
        slot_key = getattr(binding, "slot_key", None)
        facts = RepoFacts(now=self._clock.iso(), repo=repo, install=None,  # type: ignore[arg-type]
                          binding=binding, slot=self._host.slot(slot_key) if slot_key else None,
                          live_actions=live, leases=await self._scheduler.list(), layout=snap.layout)
        return await asyncio.to_thread(self._consistency.evaluate_intent, snap, facts)

    async def _assert_dispatchable(self, binding: Any, *, decision: str, intent_key: str, repo_id: str) -> None:
        """Pause and archive block every human-lane dispatch, not only Run (C29, P11): FR-RUN-003 says pause
        "prevents future turns", and a paused gate that could still be approved would take one. An open breaker
        blocks only Run/Resume: the gate a failure stopped at must stay answerable."""
        for flag, code, message in _DISPATCH_BLOCKS:
            if getattr(binding, flag, False):
                _refuse(code, message, intent_key=intent_key, repo_id=repo_id, decision=decision)
        if decision in ("run", "resume") and await self._breaker_open(repo_id, intent_key):
            _refuse("breaker_open", "this intent's circuit breaker is open; review the failure first",
                    intent_key=intent_key, decision=decision)

    def _assert_audit_question_current(self, rec: ActionRecord, snap: "IntentSnapshot") -> None:
        questions = snap.questions
        live = Captured.from_json(self._projection.captured(snap, rec.stage))
        if (not snap.stable or not snap.audit.complete or snap.stage != rec.stage or snap.unit != rec.unit
                or questions is None or not questions.origin
                or questions.origin != (rec.evidence.get("questions") or {}).get("origin")
                or snap.audit_question_attempt != (questions.origin.get("attempt_generation"), True)
                or live.differences(rec.captured)):
            _refuse("action_stale", "the captured audit question is no longer provably pending",
                    fields=["question_digest"])

    def _turn_timeout(self) -> int:
        try:
            return int(self._host.turn_timeout_secs())
        except Exception:  # the host owns this value; a missing accessor must not fail a delivery
            return int(C.PROCESSING_DEADLINE_CAP_SECS)

    def _deadline(self, seconds: float) -> str:
        return C.iso_from_epoch(self._clock.now() + seconds)

    async def _live_for_key(self, key: str | None, *, newest: bool = False) -> ActionRecord | None:
        """The live row holding a dedupe key (at most one: the partial index), or with ``newest`` the most
        recent row of any status."""
        if not key:
            return None
        where: dict[str, Any] = {"dedupe_key": key}
        if not newest:
            where["status"] = sorted(C.DEDUPE_LIVE_ACTION_STATUS)
        rows = await asyncio.to_thread(self._storage.select, _ACTIONS, where,
                                       order_by="created_at DESC, action_id DESC", limit=1)
        return ActionRecord.from_row(rows[0]) if rows else None

    async def _insert(
        self, action_type: str, scope: Any, *, risk_class: str, captured: Mapping[str, Any],
        evidence: Mapping[str, Any], dedupe_key: str | None, waiting_since: str, source: str,
        resolved_repo_identity: str | None = None,
    ) -> ActionRecord:
        """A new ``Queued`` card; ``scope`` is a seed, a record or a mapping of ``_SCOPE``."""
        where = {key: (scope.get(key) if isinstance(scope, Mapping) else getattr(scope, key)) for key in _SCOPE}
        now, action_id = self._clock.iso(), self._ids.new("a")
        if resolved_repo_identity is None:
            repo_row = await asyncio.to_thread(self._storage.get, _REPOS, where["repo_id"])
            resolved_repo_identity = (repo_row or {}).get("resolved_identity")
        meta = dict(evidence[CARD_META_KEY]) if isinstance(evidence.get(CARD_META_KEY), Mapping) else {}
        meta.setdefault("captured_at", captured.get("captured_at") or now)
        meta.setdefault("stable", bool(captured.get("stable", True)))
        row = {
            "action_id": action_id, "type": action_type, "risk_class": risk_class,
            "resolved_repo_identity": resolved_repo_identity, **where, **_captured_columns(captured),
            "status": "Queued", "status_generation": 0, "created_at": now, "updated_at": now,
            "evidence_json": {**evidence, CARD_META_KEY: meta}, "source": source,
            "waiting_since": waiting_since or now, "dedupe_key": dedupe_key,
        }
        await asyncio.to_thread(self._storage.insert, _ACTIONS, row)
        rec = ActionRecord.from_row(row)
        await self._events.publish("action.created", {
            "action_id": action_id, "repo_id": rec.repo_id, "intent_key": rec.intent_key, "type": action_type,
            "status": "Queued", "status_generation": 0, "from_status": None, "reason": "created"})
        await self._activity.record(
            kind="action.created", severity="info", repo_id=rec.repo_id, space=rec.space, intent_dir=rec.intent_dir,
            stage=rec.stage, action_id=action_id,
            params={"type": action_type, "status": "Queued", "dedupe_key": dedupe_key})
        return rec

    # ---- breakers --------------------------------------------------------- #

    async def _breaker_rows(self, repo_id: str, intent_key: str) -> list[dict[str, Any]]:
        prefix = f"{repo_id}:{intent_key}:"
        rows = await asyncio.to_thread(self._storage.select, _BREAKERS)
        return [row for row in rows if str(row.get("key") or "").startswith(prefix)]

    async def _breaker_open(self, repo_id: str, intent_key: str) -> bool:
        return any(row.get("opened_at") for row in await self._breaker_rows(repo_id, intent_key))

    async def _open_breaker_card(self, rec: ActionRecord, failure: Mapping[str, Any]) -> None:
        """One card and one notification per opening (§11.4): a breaker that shouted on every retry would train
        people to ignore it."""
        key = dedupe_key("circuit_breaker", rec.repo_id, rec.intent_key, rec.stage, str(failure.get("fingerprint")))
        if await self._live_for_key(key) is not None:
            return
        evidence = {name: rec.evidence.get(name) for name in EVIDENCE_SNAPSHOT_KEYS}
        evidence["breaker"] = dict(failure)
        evidence[CARD_META_KEY] = {
            "severity": SEED_SEVERITY["circuit_breaker"], "decisions": list(DECISIONS["circuit_breaker"]),
            "headline_params": {"stage": rec.stage, "count": failure.get("count"),
                                "fingerprint_class": failure.get("fingerprint_class")},
            "consequence_variant": None, "stage_ref": rec.card_meta.get("stage_ref"),
            "reason": failure.get("fingerprint"), "related_action_id": rec.action_id,
            "captured_at": rec.captured.captured_at, "stable": rec.captured.stable,
        }
        try:
            card_rec = await self._insert(
                "circuit_breaker", rec, risk_class="studio_only", captured=rec.captured.to_json(), evidence=evidence,
                dedupe_key=key, waiting_since=self._clock.iso(), source="studio",
                resolved_repo_identity=rec.resolved_repo_identity)
        except sqlite3.IntegrityError:
            return
        await self._activity.record(
            kind="breaker.opened", severity="attention", repo_id=rec.repo_id, space=rec.space,
            intent_dir=rec.intent_dir, stage=rec.stage, action_id=card_rec.action_id,
            params={key_: failure.get(key_) for key_ in ("fingerprint", "fingerprint_class", "count")})
        notify = getattr(self._notifications, "notify", None)
        if notify is None:
            return
        try:
            await notify("action", await self.card(card_rec, None))
        except StudioError as exc:
            logger.info("aidlc-studio: breaker notification unavailable (%s)", exc.code)

    async def _reset_breakers(self, repo_id: str, intent_key: str) -> None:
        for row in await self._breaker_rows(repo_id, intent_key):
            if not row.get("opened_at") and not row.get("count"):
                continue
            await asyncio.to_thread(self._storage.update, _BREAKERS, str(row["key"]),
                                    {"count": 0, "opened_at": None, "reason": "reset"})
            await self._activity.record(kind="breaker.reset", severity="info", repo_id=repo_id,
                                        params={"key": row["key"], "intent_key": intent_key})


# ---- small helpers ----------------------------------------------------------- #


def _seed_meta(seed: "CardSeed") -> dict[str, Any]:
    """The seed facts the ``actions`` schema has no column for (§2.11 presentation)."""
    return {
        "severity": seed.severity, "decisions": list(seed.decisions), "headline_params": dict(seed.headline_params),
        "consequence_variant": seed.consequence_variant, "stage_ref": dict(seed.stage_ref) if seed.stage_ref else None,
        "reason": seed.reason, "related_action_id": seed.related_action_id,
        "captured_at": seed.captured.get("captured_at"), "stable": bool(seed.captured.get("stable", True)),
    }


def _merge_findings(stored: Any, fresh: Any) -> list[Any]:
    """Fresh intent findings plus those the reconciler stored on this action (``human_presence_anomaly``,
    ``cursor_moved``, ``wrong_slot``), which ``ConsistencyEngine`` lifts back out to block dispatch:
    overwriting the list on the next scan would drop a guard the user is looking at."""
    out = list(fresh) if isinstance(fresh, list) else []
    codes = {item.get("code") for item in out if isinstance(item, Mapping) and isinstance(item.get("code"), str)}
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
    """The host's own refusal: a 4xx **with its error body** proves the message was never enqueued (the host
    validates slot and agent before touching the queue). A bare status, a 5xx or a network failure proves
    nothing — the request may have been handled and the answer lost."""
    try:
        status = int(http_status)
    except (TypeError, ValueError):
        return False
    body = receipt or {}
    return 400 <= status <= 499 and body.get("ok") is not True and bool(body.get("error") or body.get("code"))


def _template_for(decision: str) -> str | None:
    """The wire-text template a card shows for a decision (§2.11 ``wire_text_template``)."""
    return WIRE_TEXT_TEMPLATES.get(decision, WIRE_TEXT_TEMPLATES.get(_DEFAULT_TEMPLATE.get(decision, "")))


def _submitted_answers(rec: ActionRecord) -> list[dict[str, Any]] | None:
    """The answers a sent question card carried, as chosen (the file's letters and the person's text), or ``None``.

    A closed card's question group is the one Studio captured before the reply, still unanswered there; without
    these it read "4 of 4 unanswered" with no option marked. The wire text stays the record of what was sent.
    """
    answers = (rec.payload or {}).get("answers")
    if rec.status in SUBMITTABLE_STATUSES or rec.type != "question" or not isinstance(answers, list):
        return None
    return [{"index": a.get("index"), "option_letters": [str(x) for x in a.get("option_letters") or []],
             "free_text": a.get("free_text") if isinstance(a.get("free_text"), str) else None}
            for a in answers if isinstance(a, Mapping) and isinstance(a.get("index"), int)]


def _failure_json(rec: ActionRecord) -> dict[str, Any] | None:
    failure, breaker = rec.evidence.get("failure"), rec.evidence.get("breaker")
    if isinstance(failure, Mapping):
        out = dict(failure)
    elif isinstance(breaker, Mapping):
        out = {
            "fingerprint": breaker.get("fingerprint") or str(breaker.get("key") or "").rsplit(":", 1)[-1],
            "fingerprint_class": breaker.get("fingerprint_class"),
            "summary_key": breaker.get("reason") or breaker.get("summary_key"),
            "count": int(breaker.get("count") or 0),
            "breaker_open": bool(breaker.get("opened_at") or breaker.get("breaker_open")),
        }
    else:
        return None
    defaults = {"fingerprint": rec.retry_fingerprint or "", "fingerprint_class": None, "summary_key": None,
                "count": 0, "breaker_open": False, "history": [], "stderr_excerpt": None}
    return {**out, **{key: value for key, value in defaults.items() if key not in out}}


def _install_json(rec: ActionRecord) -> dict[str, Any] | None:
    install = rec.evidence.get("install")
    if not isinstance(install, Mapping):
        return None
    return {"receipt_id": install.get("receipt_id"), "engine_version": install.get("engine_version"),
            "bundled_engine_version": C.BUNDLED_ENGINE_VERSION, "drift": list(install.get("drift") or []),
            "transaction_id": install.get("transaction_id")}
