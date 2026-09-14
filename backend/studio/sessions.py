"""The one place Studio touches a KiroCrew object, and the record of which slot an intent talks to.

Two classes, one rule each.

``HostBridge`` is the **only** module that names a host attribute (§1.11). Everything it reads is
feature-detected and every failure degrades one named capability instead of raising: a gateway upgrade
that renames ``_ChatSlot._in_stage_execution`` must cost the user one greyed-out control and a reason on
``GET /health``, never a 500 on the Action Center. That is not defensive politeness — the measured
signature drift between 0.3.0 and 0.5.0 (architecture §7.0) is exactly why the human lane sends through
the host's public HTTP API instead of an in-process call, and the same drift reaches these reads.

Two things this module deliberately cannot do:

* **dispatch.** None of the private host entry points architecture §7.0 lists — the chat runner's turn
  starter, the slot's row appender and prompt enqueuer, the state's slot factory, the session manager's
  turn stopper — is called here, and no slot attribute is ever assigned. The browser sends and stops
  (§2.13); this module only *observes* and reports what it saw. ``test_sessions.py`` walks this file's
  AST and fails if any of those names appears at all, so re-introducing one is a test failure rather
  than a review comment.
* **guess.** Every read that cannot be performed returns ``None``/``[]`` **and** says which symbol was
  missing. "Unknown" must never be readable as "idle": the lease reclaim proof (§1.10) and the delivery
  proof (C24) both count an unknown answer against the proof, and both would authorise a duplicate
  human decision if this module answered "not busy" when it simply could not tell.

``SessionBinder`` owns the ``intent_bindings`` row: which slot an intent's decisions go to, whether it
is paused, archived or interrupted, and the last stable boundary observed for it. It never creates a
host slot (the UI does), never sends a prompt and never stops a turn.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from . import constants as C
from . import security
from .errors import StaleGeneration, StudioError
from .projection import StableBoundary

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .activity import ActivityProjector
    from .projection import Projection
    from .repo_registry import RepoRecord
    from .storage import Storage


__all__ = [
    "BUSY_REASONS",
    "BindingView",
    "CAPABILITIES",
    "HostBridge",
    "HostCapability",
    "NOTIFY_KIND",
    "SessionBinder",
    "SessionRef",
    "SlotView",
    "TakeoverCandidate",
    "TranscriptRow",
    "busy_reasons",
]


# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #

#: Every capability §2.1 reports under ``health.host.capabilities``. One name per host seam, so a
#: missing symbol switches off exactly the feature that needed it (Settings renders the reason, and
#: ``Services`` mirrors it into ``ctx.health``).
CAPABILITIES = (
    "state",
    "slots",
    "slot_messages",
    "sessions_busy",
    "owner",
    "notify",
    "slack",
    "subagents",
    "pending_questions",
)

#: Reason codes, never prose (C11: the UI owns the wording). Two families carry a symbol name so a host
#: upgrade can be diagnosed from one ``/health`` line: ``missing:<symbol>`` (the attribute is not there)
#: and ``unusable:<symbol>`` (it is there but raised or answered with the wrong shape).
REASON_NOT_ATTACHED = "host_not_attached"
#: The slot the caller asked about does not exist in the host's table. Not a defect — a tab was closed —
#: but it is why a transcript read came back empty, and the reconciler must not read that as proof.
REASON_SLOT_ABSENT = "slot_absent"
#: The seam exists but is switched off in this installation (no Slack app, no owner configured).
REASON_SLACK_NOT_CONFIGURED = "slack_not_configured"
REASON_OWNER_UNKNOWN = "owner_unknown"


def _missing(symbol: str) -> str:
    return f"missing:{symbol}"


def _unusable(symbol: str) -> str:
    return f"unusable:{symbol}"


#: ``state.notify(kind, …)`` kind for every Studio notification. Underscored, not the hyphenated app
#: name: the host folds it into a channel name (``system.<kind>``) and preserves it verbatim on the
#: note the frontend filters on, so it is a protocol string. Here rather than in ``constants.py``
#: because this module is the only caller (the orchestrator may hoist it).
NOTIFY_KIND = "aidlc_studio"

#: Re-probe the host's shape at most this often. "Feature-detected once, re-checked lazily" needs a
#: bound in both directions: probing on every request would cost a dozen ``getattr``s per call, while
#: probing only once would leave ``slack`` permanently unavailable for a gateway that connected Slack
#: after Studio started — and nothing would ever call it again to discover otherwise.
HOST_PROBE_TTL_SECS = 30.0

#: Ordered exactly as §1.11 lists them; the tuple is the golden order a test pins, because the first
#: reason is what the UI shows the user as "why can I not send this".
BUSY_REASONS = (
    "running",
    "stage_execution",
    "stopping",
    "queued",
    "approval_pending",
    "subagents",
    "deliveries_inflight",
)

#: Transcript roles that prove a human-lane message reached the host. ``queued`` belongs here: a message
#: accepted while a turn was running is queued, not appended, and it **is** delivered (review R02).
DELIVERY_ROLES = ("user", "queued")

#: Statuses in which something may still be on the wire for an intent. Rebinding or unbinding under one
#: of these would send the delivery report for a live decision to a session that no longer owns it, so
#: both refuse. Narrower than ``LIVE_ACTION_STATUS`` on purpose: ``rebind_session`` is itself a recovery
#: decision offered on a ``ReconciliationRequired`` card, so blocking on every live card would deadlock
#: the recovery path.
IN_FLIGHT_ACTION_STATUS = ("Delivering", "Delivered", "Processing")

#: Why a slot is offered as a takeover candidate. ``current_binding`` is the classification of the slot
#: the preview *excludes* — §2.3 returns it separately as ``current`` — and is therefore never attached
#: to a candidate; it is listed so the wire vocabulary has one definition.
TAKEOVER_REASONS = ("project_match", "name_match", "current_binding")

#: The ``intent_bindings`` row Studio owns, and the two archive states its ``archive_state`` column takes.
_BINDINGS = "intent_bindings"
ARCHIVE_ACTIVE = "active"
ARCHIVE_ARCHIVED = "archived"


# --------------------------------------------------------------------------- #
# timestamps
# --------------------------------------------------------------------------- #


def _instant(value: object) -> float | None:
    """A transcript or Studio timestamp as POSIX seconds; ``None`` when it cannot be read.

    Two spellings meet here and they do **not** compare as strings. Studio writes §0.2
    (``2026-09-04T10:49:31Z``); the host stamps transcript rows with ``datetime.isoformat()``
    (``2026-09-04T10:49:31.500000+00:00``). Lexicographically the second sorts *before* the first
    because ``.`` < ``Z`` — so a naive string compare hides every row appended in the same second as the
    dispatch. That is the row that proves delivery, and hiding it is how a delivered decision would be
    replayed (C24). Comparing instants is the only correct answer.
    """
    if not isinstance(value, str) or not value:
        return None
    epoch = C.epoch_from_iso(value)
    if epoch is not None:
        return epoch
    raw = value.strip()
    if raw[-1:] in ("z", "Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _at_or_after(row_ts: object, since_ts: object) -> bool:
    """Is ``row_ts`` inside the window that starts at ``since_ts``?

    Unreadable input answers **yes** in both directions. Every caller is looking for evidence that a
    message was delivered, and the asymmetry of the two mistakes is total: an extra row costs a human a
    ``ReconciliationRequired`` card to read, while a dropped row lets Studio conclude "never delivered"
    and send a human decision twice.
    """
    since = _instant(since_ts)
    if since is None:
        return True
    row = _instant(row_ts)
    if row is None:
        return True
    return row >= since


# --------------------------------------------------------------------------- #
# host-side records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class HostCapability:
    """One host seam and whether it can be used right now."""

    name: str
    available: bool
    reason: str | None
    checked_at: str

    @staticmethod
    def unavailable(name: str, reason: str) -> "HostCapability":
        """An unavailable capability stamped with wall-clock time.

        A static factory has no injected ``Clock``, so it reads the system clock; ``HostBridge`` builds
        its own entries through the clock it was given, which is what keeps test timestamps exact.
        """
        return HostCapability(name=name, available=False, reason=reason,
                              checked_at=C.iso_from_epoch(time.time()))

    def to_json(self) -> dict[str, Any]:
        """§2.1 shape: the name is the dict key, so it is not repeated in the value."""
        return {"available": self.available, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class SlotView:
    """An immutable snapshot of one host chat slot.

    A copy rather than a handle on purpose: the live ``_ChatSlot`` is mutated by the loop while a
    decision is being validated, so a precondition checked against the object itself could be true when
    it was read and false when it was acted on. Every field is optional on the host side (``to_dict()``
    is a superset in 0.5.0 and gains keys between releases), so the mapping uses ``.get`` defaults
    throughout and a key this host does not send degrades one field.
    """

    key: str
    running: bool
    project: str
    agent: str
    app: str
    queue_depth: int
    pending_approval: bool
    needs_input: bool
    waiting_for_input: bool
    stop_state: str
    interrupted: bool
    last_ts: str
    last_turn_ts: str
    linked_session_key: str
    session_key: str
    messages: int
    has_options: bool
    options: tuple[str, ...]
    slack_linked: bool
    title: str
    stopping: bool
    wait_state: dict | None
    # The busy predicates `api_chat_slot_continue` guards on (02 §4.4). They live on private slot
    # attributes, so each one defaults instead of failing the whole read (review P12).
    in_stage_execution: bool = False
    approvals_pending: int = 0
    subagents_running: int = 0
    subagents_queued: int = 0
    deliveries_inflight: int = 0

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        session_key: str = "",
        in_stage_execution: bool = False,
        approvals_pending: int = 0,
        subagents_running: int = 0,
        subagents_queued: int = 0,
        deliveries_inflight: int = 0,
    ) -> "SlotView":
        """Map ``slot.to_dict()`` plus the facts that are not in it.

        ``session_key`` is not a ``to_dict()`` key — it comes from
        ``chat_utils.effective_session_key(slot)``, because a channel-born slot runs its turns on
        another session entirely and addressing it by ``dashboard:<slot>`` would observe the wrong one.
        """
        last_ts = _text(payload.get("last_ts"))
        return cls(
            key=_text(payload.get("key")),
            running=bool(payload.get("running", False)),
            project=_text(payload.get("project")),
            agent=_text(payload.get("agent")),
            app=_text(payload.get("app")),
            queue_depth=_count(payload.get("queue_depth")),
            pending_approval=bool(payload.get("pending_approval", False)),
            needs_input=bool(payload.get("needs_input", False)),
            waiting_for_input=bool(payload.get("waiting_for_input", False)),
            # An unknown value passes through rather than being folded to "idle": `busy_reasons` treats
            # anything that is not "idle" as stopping, so a stop state this Studio has never heard of
            # refuses a dispatch instead of authorising one.
            stop_state=_text(payload.get("stop_state")) or "idle",
            interrupted=bool(payload.get("interrupted", False)),
            last_ts=last_ts,
            # 0.3.0's serialisation has no separate turn stamp; the newest row is the honest fallback.
            last_turn_ts=_text(payload.get("last_turn_ts")) or last_ts,
            linked_session_key=_text(payload.get("linked_session_key")),
            session_key=_text(session_key),
            messages=_count(payload.get("messages")),
            has_options=bool(payload.get("has_options", False)),
            options=tuple(_text(o) for o in (payload.get("options") or ())),
            slack_linked=bool(payload.get("slack_linked", False)),
            title=_text(payload.get("title")) or _text(payload.get("key")),
            stopping=bool(payload.get("stopping", False)),
            wait_state=dict(payload["wait_state"])
            if isinstance(payload.get("wait_state"), Mapping)
            else None,
            in_stage_execution=bool(in_stage_execution),
            approvals_pending=_count(approvals_pending),
            subagents_running=_count(subagents_running),
            subagents_queued=_count(subagents_queued),
            deliveries_inflight=_count(deliveries_inflight),
        )

    @property
    def busy_reasons(self) -> tuple[str, ...]:
        """Same answer as the module-level predicate, reachable off the view.

        The projection reads it structurally (``_attr(slot, "busy_reasons", ())``) when it composes
        ``IntentSummary.session``, so the queue can say *why* a slot is unavailable without importing
        this module.
        """
        return _busy_reasons(self)

    def to_json(self) -> dict[str, Any]:
        """Exactly the §3.1 ``SlotView`` interface — no extra keys (the UI type is pinned)."""
        return {
            "key": self.key,
            "running": self.running,
            "project": self.project,
            "agent": self.agent,
            "app": self.app,
            "queue_depth": self.queue_depth,
            "pending_approval": self.pending_approval,
            "needs_input": self.needs_input,
            "waiting_for_input": self.waiting_for_input,
            "stop_state": self.stop_state,
            "interrupted": self.interrupted,
            "last_ts": self.last_ts,
            "last_turn_ts": self.last_turn_ts,
            "linked_session_key": self.linked_session_key,
            "session_key": self.session_key,
            "messages": self.messages,
            "has_options": self.has_options,
            "options": list(self.options),
            "slack_linked": self.slack_linked,
            "title": self.title,
            "stopping": self.stopping,
            "wait_state": dict(self.wait_state) if self.wait_state is not None else None,
            "in_stage_execution": self.in_stage_execution,
            "approvals_pending": self.approvals_pending,
            "subagents_running": self.subagents_running,
            "subagents_queued": self.subagents_queued,
            "deliveries_inflight": self.deliveries_inflight,
        }


def _busy_reasons(view: Any) -> tuple[str, ...]:
    out: list[str] = []
    if view.running:
        out.append("running")
    if view.in_stage_execution:
        out.append("stage_execution")
    if (view.stop_state or "idle") != "idle" or view.stopping:
        out.append("stopping")
    if (view.queue_depth or 0) > 0:
        out.append("queued")
    if (view.approvals_pending or 0) > 0:
        out.append("approval_pending")
    if (view.subagents_running or 0) + (view.subagents_queued or 0) > 0:
        out.append("subagents")
    if (view.deliveries_inflight or 0) > 0:
        out.append("deliveries_inflight")
    return tuple(out)


def busy_reasons(view: Any) -> tuple[str, ...]:
    """Every reason this slot cannot take a dispatch right now; empty tuple == dispatchable.

    Deliberately reads the fields directly instead of defaulting them: a view that is missing one
    raises ``AttributeError``, and the two callers that matter (``submit`` step 5 and
    ``RepoScheduler.try_reclaim``) treat an unanswerable predicate as *busy*. Defaulting here would turn
    "we cannot tell" into "idle" at the exact moment that reading authorises a second dispatch into one
    AI-DLC workflow.
    """
    return _busy_reasons(view)


@dataclass(frozen=True, slots=True)
class TranscriptRow:
    """One row of a host transcript, as evidence.

    ``content`` is redacted (it leaves the process in cards, exports and Slack). ``meta`` is not: the
    host already redacted its string values at append time, and masking it again would corrupt the
    ``studio_delivery_id`` that is the reconciler's primary match key.
    """

    role: str
    content: str
    ts: str
    cls: str
    meta: dict

    def to_json(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content, "ts": self.ts, "cls": self.cls,
                "meta": dict(self.meta)}


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _count(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _host_symbol(module: str, name: str) -> Any:
    """A host function that is *already imported*, or ``None``.

    Never imports. Two reasons: importing a host package is disk work and this runs on the event loop,
    and in the gateway the module is imported by definition (the process serving the request *is* the
    dashboard). When it is genuinely absent — a test, the CLI, an older release without the module —
    the caller has a documented fallback, which is more useful than a stalled loop.
    """
    mod = sys.modules.get(module)
    if mod is None:
        return None
    return getattr(mod, name, None)


# --------------------------------------------------------------------------- #
# HostBridge
# --------------------------------------------------------------------------- #


class HostBridge:
    """Read-mostly access to the running gateway. Loop thread only."""

    def __init__(self, clock: C.Clock, logger: logging.Logger) -> None:
        self._clock = clock
        self._log = logger
        self._state: Any = None
        self._caps: dict[str, HostCapability] = {}
        self._probed_at: float | None = None
        self._notify_kwargs: tuple[str, ...] = ("meta", "url")
        self._turn_timeout: int | None = None
        self._reset(REASON_NOT_ATTACHED)

    # ---- attachment ------------------------------------------------------- #

    def attach(self, state: object) -> None:
        """Bind (or re-bind) the ``DashboardState``. Idempotent and cheap enough for every request.

        Lazy because ``on_startup(ctx)`` has no ``web.Application`` to read it from (C08): every handler
        passes ``request.app["state"]``, and startup tries the Slack handler's module-level accessor,
        which is set only when Slack is configured. Until something attaches, Studio is disk-only —
        which is a correct, reduced mode, not an error.
        """
        if state is None:
            return
        if state is self._state and not self._probe_due():
            return
        if state is not self._state:
            self._state = state
            self._turn_timeout = None
        self._probe()

    def attached(self) -> bool:
        return self._state is not None

    def capabilities(self) -> dict[str, HostCapability]:
        """What works right now, with the reason for everything that does not (§2.1, ctx.health)."""
        return dict(self._caps)

    # ---- slot reads ------------------------------------------------------- #

    def slot(self, slot_key: str) -> SlotView | None:
        """The named slot, or ``None`` when it does not exist (or cannot be read)."""
        slot = self._raw_slot(slot_key)
        return None if slot is None else self._view(slot)

    def slots_for_project(self, canonical_path: str) -> list[SlotView]:
        """Every slot bound to this repository root, whatever its name.

        Matching on the resolved project rather than on Studio's slot-name convention is what makes
        takeover honest: a session the user opened by hand, or one renamed by the host, is still a
        session that can move this repository's AI-DLC state.
        """
        wanted = _realpath(canonical_path)
        out: list[SlotView] = []
        for slot in self._iter_slots():
            view = self._view(slot)
            if view is None:
                continue
            if _same_dir(view.project, wanted):
                out.append(view)
        return out

    def can_prove_absence(self) -> bool:
        """Whether a ``None`` from the transcript readers means "not there" rather than "cannot look".

        Call this AFTER the reads it qualifies. Capabilities are corrected by each attempt, so asking
        first would answer about the previous one.

        This exists because ``slot()`` and ``find_delivery_row()`` both answer ``None`` for two very
        different facts: the message is not in the host's queue, and Studio cannot read the host's queue
        at all (a renamed attribute, a host that is not attached). The first is proof of absence and is
        one of the four checks behind ``NotDelivered``; the second is missing evidence. Conflating them
        would let one renamed attribute turn "I cannot see" into "nothing was sent", and the step after
        ``NotDelivered`` is Retry — a second copy of a decision the human made once.

        ``slot_messages`` is allowed to be unavailable for exactly one reason: the slot itself is not
        there (``REASON_SLOT_ABSENT``). That is a real answer about the host, arrived at through a
        readable slot table, so it still supports a proof.
        """
        slots = self._caps.get("slots")
        if slots is None or not slots.available:
            return False
        rows = self._caps.get("slot_messages")
        if rows is not None and not rows.available and rows.reason != REASON_SLOT_ABSENT:
            return False
        return True

    def slots_by_prefix(self, prefix: str) -> list[SlotView]:
        """Slots whose key starts with ``prefix`` (Studio's canonical naming)."""
        needle = str(prefix or "")
        out: list[SlotView] = []
        for slot in self._iter_slots():
            view = self._view(slot)
            if view is not None and view.key.startswith(needle):
                out.append(view)
        return out

    def slot_matches_repo(self, view: SlotView, canonical_path: str) -> bool:
        """Is this slot a legitimate target for the repository's AI-DLC decisions?

        Project, agent and app all have to line up. ``app`` accepts both the empty string (a slot the
        user opened) and Studio's own name; anything else belongs to another app and its turns are not
        Studio's to reason about.
        """
        if view is None:
            return False
        if _text(getattr(view, "agent", "")) != C.AIDLC_AGENT_NAME:
            return False
        if _text(getattr(view, "app", "")) not in ("", C.APP_NAME):
            return False
        return _same_dir(_text(getattr(view, "project", "")), _realpath(canonical_path))

    def recent_rows(
        self,
        slot_key: str,
        *,
        since_ts: str,
        roles: Sequence[str] = DELIVERY_ROLES,
        limit: int = 50,
    ) -> list[TranscriptRow]:
        """Newest-first transcript rows in the given roles at or after ``since_ts``.

        ``slot.messages`` is process memory, not a file (``chat_persistence`` flushes a *copy*), so an
        empty answer means "no evidence in this process" and never "not delivered" — a restart empties
        it while the message was really sent (review R02). Callers must treat ``[]`` accordingly; the
        ``slot_messages`` capability carries the reason when the read itself could not happen.
        """
        rows = self._raw_rows(slot_key)
        if not rows:
            return []
        wanted = frozenset(str(r) for r in (roles or ()))
        cap = max(1, int(limit or 1))
        out: list[TranscriptRow] = []
        for row in reversed(rows):
            if not isinstance(row, Mapping):
                continue
            role = _text(row.get("role"))
            if wanted and role not in wanted:
                continue
            if not _at_or_after(row.get("ts"), since_ts):
                continue
            out.append(_row_view(row))
            if len(out) >= cap:
                break
        return out

    def find_delivery_row(
        self, slot_key: str, *, since_ts: str, delivery_id: str, wire_text: str
    ) -> TranscriptRow | None:
        """The row that proves this delivery reached the host, or ``None``.

        Three passes, strongest evidence first: the ``meta.studio_delivery_id`` the browser sent and the
        host persisted on its own user row; then an exact ``wire_text`` match; then the pending queue.
        The queue pass exists because the host's queue path keeps the message in ``slot._queue`` with
        *its own* meta — the browser's ``meta`` is dropped there — so a message accepted behind a
        running turn has no transcript row yet even though it is delivered. Without this pass that
        message looks absent, and "absent" is one of the four proofs of ``NotDelivered`` (C24).
        """
        rows = self._raw_rows(slot_key)
        wanted_id = str(delivery_id or "")
        wanted_text = str(wire_text or "")

        if wanted_id:
            for row in reversed(rows):
                if not _is_delivery_row(row) or not _at_or_after(row.get("ts"), since_ts):
                    continue
                meta = row.get("meta")
                if isinstance(meta, Mapping) and _text(meta.get("studio_delivery_id")) == wanted_id:
                    return _row_view(row)
        if wanted_text:
            for row in reversed(rows):
                if not _is_delivery_row(row) or not _at_or_after(row.get("ts"), since_ts):
                    continue
                if _text(row.get("content")) == wanted_text:
                    return _row_view(row)
        return self._queued_delivery(slot_key, delivery_id=wanted_id, wire_text=wanted_text)

    def pending_question_cards(self, slot_key: str) -> list[dict]:
        """The host's own question cards for this slot, in the order its API serves them.

        The backend serves these (``GET …/{intent}/questions.host_cards``) so ``permissions.api`` stays
        at two prefixes and the UI never calls ``/api/ask-question`` (review R13). Blocking asks come
        from the state-level wait registry first — that is the authoritative record while a turn is
        parked on a question — then the slot's own non-blocking cards, skipping the status-only markers
        the host's route also skips. A host without either attribute yields ``[]``: no structured card is
        the sanctioned degraded mode (A05), not a failure.
        """
        key = str(slot_key or "")
        out: list[dict] = []
        state = self._require("pending_questions")
        if state is None:
            return out
        registry = getattr(state, "_pending_questions", None)
        if isinstance(registry, Mapping):
            for ask_id, rec in list(registry.items()):
                if not isinstance(rec, Mapping) or _text(rec.get("slot")) != key:
                    continue
                questions = _questions_of(rec)
                if questions:
                    out.append({"ask_id": _text(ask_id), "slot": key, "questions": questions,
                                "ts": rec.get("ts", 0)})
        slot = self._raw_slot(key)
        if slot is None:
            return out
        pending = getattr(slot, "_question_pending", None)
        if pending is None:
            self._miss("pending_questions", _missing("slot._question_pending"))
            return out
        if not isinstance(pending, Mapping):
            self._miss("pending_questions", _unusable("slot._question_pending"))
            return out
        for card_id, rec in list(pending.items()):
            if not isinstance(rec, Mapping) or rec.get("blocking"):
                continue
            questions = _questions_of(rec)
            if questions:
                out.append({"card_id": _text(card_id), "slot": key, "questions": questions,
                            "ts": rec.get("ts", 0)})
        self._ok("pending_questions")
        return out

    # ---- session / process reads ------------------------------------------ #

    def is_busy(self, session_key: str) -> bool | None:
        """Session-level busy state; ``None`` when the host cannot answer.

        ``None`` is not ``False``. The lease reclaim proof records it as ``session_busy_unknown`` and
        refuses, because a running turn that cannot be observed is still a running turn.
        """
        state = self._require("sessions_busy")
        if state is None:
            return None
        sessions = getattr(state, "sessions", None)
        if sessions is None:
            return self._miss("sessions_busy", _missing("state.sessions"))
        try:
            busy = bool(sessions.is_busy(str(session_key or "")))
        except (AttributeError, TypeError, KeyError) as exc:
            return self._miss("sessions_busy", _unusable("state.sessions.is_busy"), exc)
        self._ok("sessions_busy")
        return busy

    def owner_id(self) -> str | None:
        """The configured dashboard owner, or ``None`` (implicit local bootstrap owner)."""
        if self._state is None:
            return self._miss("owner", REASON_NOT_ATTACHED)
        if not hasattr(self._state, "owner_id"):
            return self._miss("owner", _missing("state.owner_id"))
        owner = _text(getattr(self._state, "owner_id", ""))
        if not owner:
            self._mark("owner", False, REASON_OWNER_UNKNOWN)
            return None
        self._ok("owner")
        return owner

    def is_owner_request(self, request: Any) -> bool:
        """Is this request the dashboard owner's own session (§1.24's owner gate)?

        Here rather than in the handler layer so ``is_owner_dashboard_request`` is not a second place
        that names a host symbol. The fallback reproduces the host's rule exactly (app token refused,
        owner id match, local bootstrap subjects) so an unimported host module cannot silently widen the
        gate — every branch still has to be satisfied.
        """
        probe = _host_symbol("kiro_crew.dashboard.handlers.source_providers",
                             "is_owner_dashboard_request")
        if probe is not None:
            try:
                return bool(probe(request))
            except (AttributeError, TypeError, KeyError) as exc:
                self._log.debug("is_owner_dashboard_request unusable: %s", exc)
        try:
            if "app" not in request or request["app"] != "":
                return False
            caller = _text(request.get("user"))
        except (AttributeError, TypeError, KeyError):
            return False
        if not caller:
            return False
        owner = _text(getattr(self._state, "owner_id", ""))
        if owner:
            return caller == owner
        return caller in _LOCAL_OWNER_SUBJECTS

    def boot_id(self) -> str | None:
        """The gateway's own per-process id when this release has one (0.5.0), else ``None``.

        Extra evidence only: restart detection uses ``Services.boot_id``, which exists on every host
        (review R02). No capability is marked — 0.3.0 legitimately has no such module.
        """
        probe = _host_symbol("kiro_crew.dashboard.boot_id", "current_boot_id")
        if probe is None:
            return None
        try:
            value = probe()
        except (AttributeError, TypeError, KeyError):
            return None
        return _text(value) or None

    def turn_timeout_secs(self) -> int:
        """How long one host turn may run, capped by ``PROCESSING_DEADLINE_CAP_SECS``.

        Resolved once and cached: the config accessor reads ``config.json`` from disk, and the
        reconciler asks for this on every tick for every intent with an open action. A ceiling that
        changes only when a human edits the config and restarts does not justify a stat per tick on the
        event loop.
        """
        if self._turn_timeout is not None:
            return self._turn_timeout
        value: float | None = None
        config = _host_symbol("kiro_crew.config", "KiroCrewConfig")
        if config is not None:
            try:
                value = float(config.load().agent.chat_turn_timeout_secs)
            except Exception as exc:  # the config file is the host's, and may be anything
                self._log.debug("chat turn ceiling unreadable, using the default: %s", exc)
                value = None
        if value is None or value <= 0:
            fallback = _host_symbol("kiro_crew.constants", "CHAT_TURN_TIMEOUT")
            try:
                value = float(fallback) if fallback else float(C.PROCESSING_DEADLINE_CAP_SECS)
            except (TypeError, ValueError):
                value = float(C.PROCESSING_DEADLINE_CAP_SECS)
        resolved = max(1, min(int(value), int(C.PROCESSING_DEADLINE_CAP_SECS)))
        self._turn_timeout = resolved
        return resolved

    def subagent(self, spawn_id: str) -> dict | None:
        """One subagent record (the advisor's own spawn), or ``None``."""
        state = self._require("subagents")
        if state is None:
            return None
        manager = getattr(state, "subagents", None)
        if manager is None:
            return self._miss("subagents", _missing("state.subagents"))
        try:
            info = manager.get(str(spawn_id or ""))
        except (AttributeError, TypeError, KeyError) as exc:
            return self._miss("subagents", _unusable("state.subagents.get"), exc)
        self._ok("subagents")
        if info is None:
            return None
        return {
            "id": _text(getattr(info, "id", spawn_id)),
            "done": bool(getattr(info, "done", False)),
            "queued": bool(getattr(info, "queued", False)),
            # Model output: redacted here because it reaches a card and a diagnostics export.
            "result": security.redact(_text(getattr(info, "result", ""))),
            "result_path": _text(getattr(info, "result_path", "")),
            "result_truncated": bool(getattr(info, "result_truncated", False)),
            "error": security.redact(_text(getattr(info, "error", ""))),
            "agent": _text(getattr(info, "agent", "")),
            "app": _text(getattr(info, "app", "")),
        }

    def subagent_result_text(self, record: Mapping[str, Any] | None) -> str | None:
        """The subagent's *whole* answer, recovered from disk when the completion event dropped part.

        The host puts only the first ``agent.completion_keep_chars`` of a subagent's answer on the
        completion event — 3 000 characters in a default install — and keeps the untruncated transcript
        at ``result_path`` until that event has been delivered. A real Advisor draft is several times
        that cap, so before this recovery existed every draft on an unmodified host settled as
        ``bad_result: result_truncated``; the live install proved it, and no test caught it because the
        fakes hand over a hand-authored short result.

        ``None`` means "the untruncated text is not available", never "the answer was empty". The caller
        must fail the draft on ``None`` rather than parse the head of a JSON object — a half-parsed
        recommendation is the confidently-wrong answer the strict parser exists to prevent. An absent
        file is the normal case once the host has cleaned up, so it degrades no capability: the read is
        pure filesystem work on a path the host handed over, not a host symbol that could drift.
        """
        if record is None:
            return None
        if not record.get("result_truncated"):
            return _text(record.get("result"))
        raw = _text(record.get("result_path"))
        if not raw:
            return None
        text = security.bounded_text(Path(raw), C.MAX_SUBAGENT_RESULT_BYTES)
        if text is None:
            self._log.debug("subagent result unrecoverable from %s", raw)
            return None
        # Redacted here for the same reason ``subagent`` redacts its own copy: this text reaches a card
        # and a diagnostics export.
        return security.redact(text)

    # ---- the only two host writes ---------------------------------------- #

    def notify(self, title: str, body: str, *, url: str, meta: dict | None = None) -> bool:
        """Push a dashboard notification. ``False`` when the seam is unavailable or the link is unsafe.

        Never raises. A notification is an aside on every path that produces one (a decision is already
        durable by then), so failing one must not fail the operation that earned it. The ``url`` gate is
        Studio's own: the host drops a note whose link is not dashboard-internal, which would silently
        cost the user the Open button, so a link outside ``DEEP_LINK_BASE`` is refused here where the
        caller's own test can see it.
        """
        target = _text(url)
        if not _is_studio_link(target):
            self._log.warning("refusing a notification whose url is outside %s", C.DEEP_LINK_BASE)
            return False
        state = self._require("notify")
        if state is None:
            return False
        send = getattr(state, "notify", None)
        if not callable(send):
            self._miss("notify", _missing("state.notify"))
            return False
        kwargs: dict[str, Any] = {}
        if "meta" in self._notify_kwargs:
            kwargs["meta"] = dict(meta or {})
        if "url" in self._notify_kwargs:
            kwargs["url"] = target
        try:
            send(NOTIFY_KIND, _text(title), _text(body), **kwargs)
        except Exception as exc:  # the host's own bus; it documents "never raises", so this is drift
            self._miss("notify", _unusable("state.notify"), exc)
            return False
        self._ok("notify")
        return True

    async def slack_dm(self, blocks: list[dict], text: str) -> str | None:
        """DM the owner and return the message timestamp, or ``None``.

        Notification plus deep link only — no quick actions exist in v1 (C13), so nothing here can turn
        a Slack click into a decision. Failure is always ``None``: Slack is a courtesy channel, and the
        decision is made in Studio where the evidence and the staleness check are.
        """
        state = self._require("slack")
        if state is None:
            return None
        client = getattr(state, "slack_client", None)
        if client is None:
            self._mark("slack", False, REASON_SLACK_NOT_CONFIGURED)
            return None
        owner = _text(getattr(self._state, "owner_id", ""))
        if not owner:
            self._mark("slack", False, REASON_OWNER_UNKNOWN)
            return None
        try:
            channel = await client.open_dm(owner)
            if not channel:
                self._mark("slack", False, _unusable("slack_client.open_dm"))
                return None
            ts = await client.post_blocks(str(channel), list(blocks or ()), _text(text))
        except Exception as exc:  # transport, auth, rate limit — all the same answer to the caller
            self._miss("slack", _unusable("state.slack_client"), exc)
            return None
        self._ok("slack")
        return _text(ts) or None

    # ---- internals -------------------------------------------------------- #

    def _probe_due(self) -> bool:
        if self._probed_at is None:
            return True
        try:
            return (self._clock.monotonic() - self._probed_at) >= HOST_PROBE_TTL_SECS
        except (AttributeError, TypeError):  # pragma: no cover - a Clock always has monotonic()
            return True

    def _probe(self) -> None:
        """Feature-detect every capability against the attached host.

        Cheap ``getattr``s only — no call reaches the host here, because a probe that ran host code
        would make attaching a side effect. What cannot be judged without a live slot
        (``slot.messages``, ``slot._question_pending``) starts available and is corrected by the first
        read that fails.
        """
        state = self._state
        try:
            self._probed_at = self._clock.monotonic()
        except (AttributeError, TypeError):  # pragma: no cover
            self._probed_at = time.monotonic()
        if state is None:
            self._reset(REASON_NOT_ATTACHED)
            return

        slots = getattr(state, "_slots", None)
        if not callable(getattr(state, "get_slot", None)) and not isinstance(slots, Mapping):
            self._mark("state", False, _missing("state.get_slot"))
        else:
            self._mark("state", True, None)

        if callable(getattr(state, "get_slot", None)) or isinstance(slots, Mapping):
            self._mark("slots", True, None)
        else:
            self._mark("slots", False, _missing("state._slots"))

        self._mark("slot_messages", True, None)
        self._mark("pending_questions", True, None)

        sessions = getattr(state, "sessions", None)
        if sessions is None:
            self._mark("sessions_busy", False, _missing("state.sessions"))
        elif not callable(getattr(sessions, "is_busy", None)):
            self._mark("sessions_busy", False, _missing("state.sessions.is_busy"))
        else:
            self._mark("sessions_busy", True, None)

        if not hasattr(state, "owner_id"):
            self._mark("owner", False, _missing("state.owner_id"))
        elif not _text(getattr(state, "owner_id", "")):
            self._mark("owner", False, REASON_OWNER_UNKNOWN)
        else:
            self._mark("owner", True, None)

        send = getattr(state, "notify", None)
        if not callable(send):
            self._mark("notify", False, _missing("state.notify"))
        else:
            self._notify_kwargs = _accepted_kwargs(send, ("meta", "url"))
            self._mark("notify", True, None)

        client = getattr(state, "slack_client", None)
        if client is None:
            self._mark("slack", False, REASON_SLACK_NOT_CONFIGURED)
        elif not callable(getattr(client, "open_dm", None)) or not callable(
            getattr(client, "post_blocks", None)
        ):
            self._mark("slack", False, _missing("slack_client.post_blocks"))
        elif not _text(getattr(state, "owner_id", "")):
            self._mark("slack", False, REASON_OWNER_UNKNOWN)
        else:
            self._mark("slack", True, None)

        manager = getattr(state, "subagents", None)
        if manager is None:
            self._mark("subagents", False, _missing("state.subagents"))
        elif not callable(getattr(manager, "get", None)):
            self._mark("subagents", False, _missing("state.subagents.get"))
        else:
            self._mark("subagents", True, None)

    def _reset(self, reason: str) -> None:
        for name in CAPABILITIES:
            self._mark(name, False, reason)

    def _mark(self, name: str, available: bool, reason: str | None) -> None:
        self._caps[name] = HostCapability(
            name=name, available=available, reason=reason, checked_at=self._stamp()
        )

    def _ok(self, name: str) -> None:
        current = self._caps.get(name)
        if current is None or not current.available:
            self._mark(name, True, None)

    def _miss(self, name: str, reason: str, exc: BaseException | None = None) -> Any:
        """Record a degraded capability and answer ``None``. The one exit for every failed read."""
        if exc is not None:
            self._log.debug("host capability %s unavailable (%s): %s", name, reason, exc)
        self._mark(name, False, reason)
        return None

    def _require(self, name: str) -> Any:
        """The attached host, or ``None`` after marking ``name`` as simply not attached.

        Without this guard every read on a detached bridge would blame a specific symbol
        (``missing:state.sessions``) for a host that is not there at all, and ``GET /health`` would
        report a shape problem where the truth is "Studio is running disk-only" (C08).
        """
        if self._state is None:
            self._mark(name, False, REASON_NOT_ATTACHED)
            return None
        return self._state

    def _stamp(self) -> str:
        try:
            return self._clock.iso()
        except (AttributeError, TypeError):  # pragma: no cover
            return C.iso_from_epoch(time.time())

    def _raw_slot(self, slot_key: str) -> Any:
        state = self._state
        if state is None:
            return self._miss("slots", REASON_NOT_ATTACHED)
        key = str(slot_key or "")
        getter = getattr(state, "get_slot", None)
        if callable(getter):
            try:
                slot = getter(key)
            except (AttributeError, TypeError, KeyError) as exc:
                return self._miss("slots", _unusable("state.get_slot"), exc)
            self._ok("slots")
            return slot
        slots = getattr(state, "_slots", None)
        if isinstance(slots, Mapping):
            self._ok("slots")
            return slots.get(key)
        return self._miss("slots", _missing("state.get_slot"))

    def _iter_slots(self) -> list[Any]:
        state = self._state
        if state is None:
            self._miss("slots", REASON_NOT_ATTACHED)
            return []
        slots = getattr(state, "_slots", None)
        if not isinstance(slots, Mapping):
            self._miss("slots", _missing("state._slots"))
            return []
        try:
            # A snapshot of the values: the loop may open or close a slot while this list is walked,
            # and a dict that changes size mid-iteration would raise inside a read path.
            listed = list(slots.values())
        except (AttributeError, TypeError, RuntimeError) as exc:
            self._miss("slots", _unusable("state._slots"), exc)
            return []
        self._ok("slots")
        return listed

    def _raw_rows(self, slot_key: str) -> list[Any]:
        if self._require("slot_messages") is None:
            return []
        slot = self._raw_slot(slot_key)
        if slot is None:
            self._mark("slot_messages", False, REASON_SLOT_ABSENT)
            return []
        messages = getattr(slot, "messages", None)
        if messages is None:
            self._miss("slot_messages", _missing("slot.messages"))
            return []
        try:
            rows = list(messages)
        except (TypeError, RuntimeError) as exc:
            self._miss("slot_messages", _unusable("slot.messages"), exc)
            return []
        self._ok("slot_messages")
        return rows

    def _queued_delivery(
        self, slot_key: str, *, delivery_id: str, wire_text: str
    ) -> TranscriptRow | None:
        """A match still sitting in the slot's pending queue, rendered as the ``queued`` row it is."""
        slot = self._raw_slot(slot_key)
        if slot is None:
            return None
        queue = getattr(slot, "_queue", None)
        if not isinstance(queue, (list, tuple)):
            return None
        stamp = _text(getattr(slot, "_last_enqueue_ts", ""))
        for item in list(queue):
            if not isinstance(item, Mapping):
                continue
            meta = item.get("meta")
            matched = (
                delivery_id
                and isinstance(meta, Mapping)
                and _text(meta.get("studio_delivery_id")) == delivery_id
            ) or (wire_text and _text(item.get("content")) == wire_text)
            if not matched:
                continue
            return TranscriptRow(
                role="queued",
                content=security.redact(_text(item.get("content"))),
                ts=stamp,
                cls="queue",
                meta={"queue_id": _text(item.get("id"))},
            )
        return None

    def _view(self, slot: Any) -> SlotView | None:
        try:
            payload = slot.to_dict()
        except (AttributeError, TypeError, KeyError) as exc:
            return self._miss("slots", _unusable("slot.to_dict"), exc)
        if not isinstance(payload, Mapping):
            return self._miss("slots", _unusable("slot.to_dict"))
        session_key = self._session_key(slot)
        return SlotView.from_payload(
            payload, session_key=session_key, **self._busy_facts(slot, payload, session_key)
        )

    def _session_key(self, slot: Any) -> str:
        """``effective_session_key(slot)``, or the same rule spelled out.

        The host's function is preferred because it is the definition; the fallback repeats it (a linked
        key wins, otherwise ``dashboard:<key>`` with the legacy ``dashboard_`` prefixes stripped) so a
        process that has not imported the module still addresses the right session instead of inventing
        ``dashboard:slack:<ts>`` for a channel-born slot.
        """
        probe = _host_symbol("kiro_crew.dashboard.chat_utils", "effective_session_key")
        if probe is not None:
            try:
                return _text(probe(slot))
            except (AttributeError, TypeError, KeyError):
                pass
        linked = _text(getattr(slot, "linked_session_key", ""))
        if linked:
            return linked
        key = _text(getattr(slot, "key", ""))
        if key.startswith("dashboard:"):
            return key
        while key.startswith("dashboard_"):
            key = key[len("dashboard_"):]
        return f"dashboard:{key}" if key else ""

    def _busy_facts(
        self, slot: Any, payload: Mapping[str, Any], session_key: str
    ) -> dict[str, Any]:
        """The five busy predicates that are not in ``to_dict()`` (review P12).

        Each one falls back rather than failing the read, and where ``to_dict()`` carries a public
        equivalent that is the fallback: ``orchestrating`` is ``_in_stage_execution`` and
        ``pending_approval`` is "at least one undone approval future". Marking ``slots`` unavailable for
        a renamed private attribute would refuse every dispatch over a value the serialisation already
        reports.
        """
        stage = getattr(slot, "_in_stage_execution", None)
        if stage is None:
            stage = payload.get("orchestrating", False)

        futures = getattr(slot, "_approval_futures", None)
        approvals = 0
        if isinstance(futures, Mapping):
            for fut in list(futures.values()):
                try:
                    if not fut.done():
                        approvals += 1
                except (AttributeError, TypeError):
                    approvals += 1  # an unreadable future is a pending one, never a resolved one
        elif payload.get("pending_approval"):
            approvals = 1

        running, queued = self._subagent_depth(session_key)
        return {
            "in_stage_execution": bool(stage),
            "approvals_pending": approvals,
            "subagents_running": running,
            "subagents_queued": queued,
            "deliveries_inflight": _count(getattr(slot, "_subagent_deliveries_inflight", 0)),
        }

    def _subagent_depth(self, session_key: str) -> tuple[int, int]:
        manager = getattr(self._state, "subagents", None)
        if manager is None:
            self._mark("subagents", False, _missing("state.subagents"))
            return 0, 0
        running = 0
        queued = 0
        try:
            rows = manager.running_agents_for(session_key)
            running = len(list(rows or ()))
        except (AttributeError, TypeError, KeyError) as exc:
            self._miss("subagents", _unusable("state.subagents.running_agents_for"), exc)
            return 0, 0
        # `_queued_depth` is the name §1.11 pins; `queued_count_for` is its public twin. Trying both
        # keeps a queued wave visible on either host generation instead of reading as "no pending work".
        for name in ("_queued_depth", "queued_count_for"):
            probe = getattr(manager, name, None)
            if callable(probe):
                try:
                    queued = _count(probe(session_key))
                    break
                except (AttributeError, TypeError, KeyError) as exc:
                    self._log.debug("subagent queue depth unreadable via %s: %s", name, exc)
        self._ok("subagents")
        return running, queued


#: The host's own implicit-owner subjects (``source_providers._LOCAL_DASHBOARD_OWNER_SUBJECTS``). Copied
#: rather than imported so the fallback owner gate stays exact when the module is not loaded.
_LOCAL_OWNER_SUBJECTS = frozenset({"local-app", "local-startup"})


def _is_delivery_row(row: Any) -> bool:
    return isinstance(row, Mapping) and _text(row.get("role")) in DELIVERY_ROLES


def _row_view(row: Mapping[str, Any]) -> TranscriptRow:
    meta = row.get("meta")
    return TranscriptRow(
        role=_text(row.get("role")),
        content=security.redact(_text(row.get("content"))),
        ts=_text(row.get("ts")),
        cls=_text(row.get("cls")),
        meta=dict(meta) if isinstance(meta, Mapping) else {},
    )


def _questions_of(rec: Mapping[str, Any]) -> list[dict]:
    """The card's questions as Studio's own copies; ``[]`` for a status-only marker."""
    raw = rec.get("questions")
    if not isinstance(raw, (list, tuple)):
        return []
    return [dict(q) for q in raw if isinstance(q, Mapping)]


def _accepted_kwargs(fn: Any, wanted: Sequence[str]) -> tuple[str, ...]:
    """Which of ``wanted`` this installed signature takes (architecture §7.0).

    A host that drops a keyword must cost one field of a notification, not a ``TypeError`` out of a
    read path. Unintrospectable callables get the full set — the call itself is guarded anyway.
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):  # pragma: no cover - builtins and C callables
        return tuple(wanted)
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return tuple(wanted)
    return tuple(name for name in wanted if name in params)


def _is_studio_link(url: str) -> bool:
    """Is this a deep link into Studio's own page?

    A prefix test alone is not enough: ``/apps/aidlc-studio-elsewhere`` starts with the base and is a
    different app's page. The character after the base has to be a boundary, so the check cannot be
    widened by a sibling name.
    """
    base = C.DEEP_LINK_BASE
    if not url.startswith(base):
        return False
    tail = url[len(base):]
    return tail == "" or tail[0] in "?#/"


def _realpath(path: object) -> str:
    raw = _text(path)
    if not raw:
        return ""
    try:
        return os.path.realpath(raw)
    except (OSError, ValueError):  # pragma: no cover - a NUL byte or an unreadable mount
        return raw


def _same_dir(left: object, right: object) -> bool:
    """Do these two paths name the same directory?

    String comparison after ``realpath`` first, then inode identity. The second pass is not
    belt-and-braces: on case-insensitive APFS ``/Users/x/Repo`` and ``/Users/x/repo`` are one directory
    and ``realpath`` preserves the caller's spelling (A13), so a slot bound by a differently-cased path
    would otherwise look like somebody else's repository and refuse every dispatch.
    """
    a = _realpath(left)
    b = _text(right)
    if not a or not b:
        return False
    if a == b:
        return True
    try:
        sa, sb = os.stat(a), os.stat(b)
    except OSError:
        return False
    return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)


# --------------------------------------------------------------------------- #
# binding records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SessionRef:
    """The compact "which session is this intent talking to" shape §2.3 embeds in an IntentSummary."""

    slot_key: str
    session_key: str
    running: bool
    bound_at: str

    def to_json(self) -> dict[str, Any]:
        return {"slot_key": self.slot_key, "session_key": self.session_key,
                "running": self.running, "bound_at": self.bound_at}


@dataclass(frozen=True, slots=True)
class BindingView:
    """One ``intent_bindings`` row: Studio's own facts about an intent that AI-DLC does not record."""

    repo_id: str
    space: str
    intent_dir: str
    intent_key: str
    intent_uuid: str | None
    slot_key: str | None
    session_key: str | None
    binding_generation: int
    archive_state: str
    paused: bool
    paused_at: str | None
    interrupted_at: str | None
    keep_moving: bool
    last_stable_boundary: StableBoundary | None
    updated_at: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "BindingView":
        space = _text(row.get("space")) or C.DEFAULT_SPACE
        intent_dir = _text(row.get("intent_dir"))
        return cls(
            repo_id=_text(row.get("repo_id")),
            space=space,
            intent_dir=intent_dir,
            intent_key=C.intent_key_for(space, intent_dir),
            intent_uuid=row.get("intent_uuid") or None,
            slot_key=row.get("slot_key") or None,
            session_key=row.get("canonical_session_key") or None,
            binding_generation=int(row.get("binding_generation") or 0),
            archive_state=_text(row.get("archive_state")) or ARCHIVE_ACTIVE,
            paused=bool(row.get("paused")),
            paused_at=row.get("paused_at") or None,
            interrupted_at=row.get("interrupted_at") or None,
            # Always False in v1: there is no machine lane to keep moving (A04).
            keep_moving=bool(row.get("keep_moving")),
            last_stable_boundary=_decode_boundary(row.get("last_stable_boundary_json")),
            updated_at=_text(row.get("updated_at")),
        )

    @property
    def archived(self) -> bool:
        return self.archive_state == ARCHIVE_ARCHIVED

    @property
    def interrupted(self) -> bool:
        return bool(self.interrupted_at)

    def to_json(self) -> dict[str, Any]:
        boundary = self.last_stable_boundary
        return {
            "repo_id": self.repo_id,
            "space": self.space,
            "intent_dir": self.intent_dir,
            "intent_key": self.intent_key,
            "intent_uuid": self.intent_uuid,
            "slot_key": self.slot_key,
            "session_key": self.session_key,
            "binding_generation": self.binding_generation,
            "archive_state": self.archive_state,
            "paused": self.paused,
            "paused_at": self.paused_at,
            "interrupted_at": self.interrupted_at,
            "keep_moving": self.keep_moving,
            "last_stable_boundary": boundary.to_json() if boundary is not None else None,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class TakeoverCandidate:
    """A slot the user could move this intent's decisions to, and why it is a candidate."""

    slot: SlotView
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {"slot": self.slot.to_json(), "reason": self.reason}


def _decode_boundary(raw: object) -> StableBoundary | None:
    """Rebuild the projection's ``StableBoundary`` from its stored JSON.

    Rebuilt as the real dataclass, not left as a dict: ``RepoScheduler.prove_reclaim`` reads
    ``boundary.stable`` with ``getattr`` and a dict would answer ``None`` there — which reads as
    "boundary unknown" and quietly refuses every reclaim.
    """
    if isinstance(raw, StableBoundary):
        return raw
    if not isinstance(raw, Mapping):
        return None
    return StableBoundary(
        stable=bool(raw.get("stable")),
        reasons=tuple(_text(r) for r in (raw.get("reasons") or ())),
        stage=raw.get("stage") or None,
        marker=raw.get("marker") or None,
        boundary_token=raw.get("boundary_token") or None,
        recorded_at=_text(raw.get("recorded_at")),
    )


def _boundary_json(boundary: Any) -> dict[str, Any] | None:
    if boundary is None:
        return None
    encode = getattr(boundary, "to_json", None)
    if callable(encode):
        return dict(encode())
    return dict(boundary) if isinstance(boundary, Mapping) else None


# --------------------------------------------------------------------------- #
# SessionBinder
# --------------------------------------------------------------------------- #


class SessionBinder:
    """Owns the ``intent_bindings`` row. Async; every store call goes to a worker thread."""

    def __init__(
        self,
        storage: "Storage",
        host: HostBridge,
        projection: "Projection",
        clock: C.Clock,
        activity: "ActivityProjector",
    ) -> None:
        self._storage = storage
        self._host = host
        # Held because §1.11 wires it here: the projection owns `StableBoundary` and every intent-shaped
        # read, so a component that has a binder has the reader that produced its boundary too.
        self._projection = projection
        self._clock = clock
        self._activity = activity

    # ---- reads ------------------------------------------------------------ #

    async def get(self, repo_id: str, space: str, intent_dir: str) -> BindingView:
        """The binding, creating an unbound row on first access.

        Created eagerly so `paused`, `interrupted_at` and the stable boundary have a home before
        anything binds a slot: those facts are about the *intent*, and an intent can be paused (or
        interrupted by a force stop) without ever having had a session.
        """
        return BindingView.from_row(await self._ensure(repo_id, space, intent_dir))

    async def session_ref(self, binding: BindingView) -> SessionRef | None:
        """The live session shape for a binding, or ``None`` when it is unbound.

        ``running`` comes from the host (the row cannot know it); ``bound_at`` is the row's
        ``updated_at``, which is the moment the binding was last written.
        """
        slot_key = _text(getattr(binding, "slot_key", ""))
        if not slot_key:
            return None
        view = self._host.slot(slot_key)
        return SessionRef(
            slot_key=slot_key,
            session_key=_text(getattr(binding, "session_key", ""))
            or (view.session_key if view is not None else ""),
            running=bool(view.running) if view is not None else False,
            bound_at=_text(getattr(binding, "updated_at", "")),
        )

    async def canonical_slot_name(self, repo_id: str, intent_dir: str) -> str:
        """The slot name Studio asks the UI to create for this intent (§0.1)."""
        return C.SLOT_KEY_TEMPLATE.format(repo_id=repo_id, intent_dir=intent_dir)

    # ---- binding ---------------------------------------------------------- #

    async def bind(
        self,
        repo: "RepoRecord",
        space: str,
        intent_dir: str,
        *,
        slot_key: str,
        intent_uuid: str | None,
    ) -> BindingView:
        """Record which slot this intent's decisions go to, after proving the slot is the right one.

        Every check is a refusal, not a repair. Studio cannot create or re-point a host slot (the UI
        does that, §2.13), and binding an intent to a slot whose project or agent differs would send a
        gate approval into a conversation that is not running AI-DLC over this repository — an
        irreversible write into someone else's audit trail.
        """
        repo_id = _text(C.attr(repo, "repo_id"))
        canonical = _text(C.attr(repo, "canonical_path"))
        key = _text(slot_key)
        if not key:
            raise StudioError("bad_param", "slot_key is required", details={"field": "slot_key"})
        if not self._host.attached():
            raise StudioError(
                "host_unavailable",
                "the dashboard state is not attached, so the slot cannot be verified",
                details={"reason": REASON_NOT_ATTACHED},
            )
        view = self._host.slot(key)
        if view is None:
            raise StudioError(
                "slot_mismatch",
                "no such host slot",
                details={
                    "slot_key": key,
                    "reason": "slot_missing",
                    "expected_project": canonical,
                    "actual_project": None,
                    "expected_agent": C.AIDLC_AGENT_NAME,
                    "actual_agent": None,
                },
            )
        if not self._host.slot_matches_repo(view, canonical):
            raise StudioError(
                "slot_mismatch",
                "the slot is not bound to this repository with the aidlc agent",
                details={
                    "slot_key": key,
                    "reason": "slot_mismatch",
                    "expected_project": canonical,
                    "actual_project": view.project,
                    "expected_agent": C.AIDLC_AGENT_NAME,
                    "actual_agent": view.agent,
                    "actual_app": view.app,
                },
            )

        row = await self._ensure(repo_id, space, intent_dir)
        other = await self._other_binding(repo_id, space, intent_dir, key)
        if other is not None and view.running:
            # A running turn belongs to whichever intent is bound to that slot; stealing it mid-turn
            # would report that turn's outcome against the wrong workflow.
            raise StudioError(
                "slot_busy",
                "the slot is running a turn for another intent",
                details={
                    "slot_key": key,
                    "reasons": list(busy_reasons(view)),
                    "bound_intent_key": C.intent_key_for(
                        _text(other.get("space")), _text(other.get("intent_dir"))
                    ),
                },
            )

        updated = await self._cas(
            row,
            {
                "slot_key": key,
                "canonical_session_key": view.session_key,
                "intent_uuid": intent_uuid if intent_uuid else row.get("intent_uuid"),
                "resolved_repo_identity": C.attr(repo, "resolved_identity"),
            },
        )
        await self._record("session.bound", updated, params={
            "slot_key": key,
            "session_key": view.session_key,
            "previous_slot_key": row.get("slot_key") or None,
        })
        return BindingView.from_row(updated)

    async def unbind(self, repo_id: str, space: str, intent_dir: str, *, reason: str) -> BindingView:
        """Forget the slot. Refused while a decision may still be on the wire."""
        row = await self._ensure(repo_id, space, intent_dir)
        await self._refuse_when_in_flight(repo_id, space, intent_dir, operation="unbind")
        previous = row.get("slot_key") or None
        updated = await self._cas(row, {"slot_key": None, "canonical_session_key": None})
        await self._record("session.unbound", updated,
                           params={"reason": _text(reason), "previous_slot_key": previous})
        return BindingView.from_row(updated)

    async def takeover_preview(
        self, repo: "RepoRecord", space: str, intent_dir: str
    ) -> list[TakeoverCandidate]:
        """Slots the user could hand this intent to, excluding the one it already uses.

        Two searches, because the two ways a slot can belong to this intent are independent: a slot
        bound to the repository root (whatever it is called) and a slot carrying Studio's canonical name
        (even if its project was changed underneath it). The union, deduped by key, is what the takeover
        dialog lists.
        """
        repo_id = _text(C.attr(repo, "repo_id"))
        canonical = _text(C.attr(repo, "canonical_path"))
        row = await self._ensure(repo_id, space, intent_dir)
        current = _text(row.get("slot_key"))

        seen: dict[str, TakeoverCandidate] = {}
        for view in self._host.slots_for_project(canonical):
            if view.key and view.key != current:
                seen.setdefault(view.key, TakeoverCandidate(slot=view, reason="project_match"))
        prefix = C.SLOT_KEY_TEMPLATE.format(repo_id=repo_id, intent_dir="")
        for view in self._host.slots_by_prefix(prefix):
            if view.key and view.key != current:
                seen.setdefault(view.key, TakeoverCandidate(slot=view, reason="name_match"))
        return [seen[key] for key in sorted(seen)]

    async def takeover(
        self, repo: "RepoRecord", space: str, intent_dir: str, *, slot_key: str
    ) -> BindingView:
        """Move the intent to another slot. Refused while a decision may still be on the wire."""
        repo_id = _text(C.attr(repo, "repo_id"))
        row = await self._ensure(repo_id, space, intent_dir)
        await self._refuse_when_in_flight(repo_id, space, intent_dir, operation="takeover")
        previous = row.get("slot_key") or None
        binding = await self.bind(
            repo, space, intent_dir, slot_key=slot_key, intent_uuid=row.get("intent_uuid")
        )
        await self._record("session.takeover", await self._row(repo_id, space, intent_dir),
                           params={"slot_key": binding.slot_key, "previous_slot_key": previous})
        return binding

    # ---- intent-level flags ---------------------------------------------- #

    async def set_paused(
        self, repo_id: str, space: str, intent_dir: str, paused: bool
    ) -> BindingView:
        """Pause or resume dispatch for this intent (FR-RUN-003).

        Pause blocks **every** human-lane dispatch, not only run/resume (C29), so it is recorded on the
        binding rather than derived from anything AI-DLC writes: it is Studio's own decision about
        whether the user wants turns to happen at all.
        """
        row = await self._ensure(repo_id, space, intent_dir)
        want = bool(paused)
        updated = await self._cas(
            row,
            {"paused": 1 if want else 0, "paused_at": self._clock.iso() if want else None},
            retry=True,
        )
        await self._record("intent.paused" if want else "intent.resumed", updated,
                           params={"paused": want})
        return BindingView.from_row(updated)

    async def set_interrupted(
        self, repo_id: str, space: str, intent_dir: str, at: str | None
    ) -> BindingView:
        """Stamp (or clear) the mid-stage interruption.

        Set by the reconciler when a ``force_stop`` resolves. Cleared **only** by the recovery card's
        ``acknowledge`` (review P17): run and resume never clear it, because PRD §11.2 requires a human
        to take over after a mid-stage interruption, and a one-click Run that cleared the flag would be
        exactly the "Interrupted → Queued in one click" path that was removed.
        """
        row = await self._ensure(repo_id, space, intent_dir)
        updated = await self._cas(row, {"interrupted_at": at or None}, retry=True)
        return BindingView.from_row(updated)

    async def set_archived(
        self, repo_id: str, space: str, intent_dir: str, archived: bool
    ) -> BindingView:
        """Archive or restore the intent in Studio's own view (AI-DLC's files are untouched).

        Archiving is refused while any card for the intent is still live: an archived intent is hidden
        from the queue, and hiding a live card would lose the decision rather than close it. Restoring
        is always allowed.
        """
        row = await self._ensure(repo_id, space, intent_dir)
        want = bool(archived)
        if want:
            await self._refuse_when_live(repo_id, space, intent_dir, operation="archive")
        updated = await self._cas(
            row, {"archive_state": ARCHIVE_ARCHIVED if want else ARCHIVE_ACTIVE}, retry=True
        )
        await self._record("intent.archived" if want else "intent.restored", updated,
                           params={"archived": want})
        return BindingView.from_row(updated)

    async def record_stable_boundary(
        self, repo_id: str, space: str, intent_dir: str, boundary: Any
    ) -> None:
        """Persist the last observed stable boundary, skipping a write that would change nothing.

        The reconciler recomputes this every tick for every bound intent. With ``synchronous=FULL`` a
        blind write would be one fsync per intent per five seconds; comparing first makes the cost
        proportional to real movement instead of to the tick rate.
        """
        row = await self._ensure(repo_id, space, intent_dir)
        payload = _boundary_json(boundary)
        if _boundary_json(_decode_boundary(row.get("last_stable_boundary_json"))) == payload:
            return
        await self._cas(row, {"last_stable_boundary_json": payload}, retry=True)

    # ---- internals -------------------------------------------------------- #

    @staticmethod
    def _key(repo_id: str, space: str, intent_dir: str) -> str:
        return f"{repo_id}:{space}:{intent_dir}"

    async def _row(self, repo_id: str, space: str, intent_dir: str) -> dict:
        row = await asyncio.to_thread(
            self._storage.get, _BINDINGS, self._key(repo_id, space, intent_dir)
        )
        return row or {}

    async def _ensure(self, repo_id: str, space: str, intent_dir: str) -> dict:
        space = _text(space) or C.DEFAULT_SPACE
        intent_dir = _text(intent_dir)
        key = self._key(repo_id, space, intent_dir)
        row = await asyncio.to_thread(self._storage.get, _BINDINGS, key)
        if row is not None:
            return row
        now = self._clock.iso()
        try:
            await asyncio.to_thread(
                self._storage.insert,
                _BINDINGS,
                {
                    "binding_key": key,
                    "repo_id": repo_id,
                    "space": space,
                    "intent_dir": intent_dir,
                    "binding_generation": 0,
                    "keep_moving": 0,
                    "archive_state": ARCHIVE_ACTIVE,
                    "paused": 0,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        except sqlite3.IntegrityError as exc:
            # Two tabs opened the same intent at once: the row the other request wrote is as good as
            # the one this request would have written, so the loser reads it back. The same exception
            # also covers the table's one foreign key, which is a different failure entirely — an
            # unregistered repository — and must not be swallowed as a won race.
            row = await asyncio.to_thread(self._storage.get, _BINDINGS, key)
            if row is None:
                raise StudioError(
                    "repo_not_found",
                    "the intent binding references a repository that is not registered",
                    details={"repo_id": repo_id, "binding_key": key, "reason": str(exc)},
                ) from exc
            return row
        row = await asyncio.to_thread(self._storage.get, _BINDINGS, key)
        if row is None:  # pragma: no cover - the insert either succeeded or raised above
            raise StudioError("storage_error", "the intent binding could not be created",
                              details={"binding_key": key})
        return row

    async def _cas(self, row: Mapping[str, Any], values: Mapping[str, Any], *, retry: bool = False) -> dict:
        """Generation-checked update, returning the row as written.

        ``retry`` is for the writers whose value is an end state the caller asked for (paused,
        archived, interrupted, boundary): losing a race there means re-reading and applying the same
        intent again. It is deliberately off for bind/unbind/takeover — *which slot* a decision goes to
        is not a last-writer-wins field, and a lost CAS there means another tab bound it, which the user
        must see rather than have overwritten.
        """
        key = _text(row.get("binding_key")) or self._key(
            _text(row.get("repo_id")), _text(row.get("space")), _text(row.get("intent_dir"))
        )
        generation = int(row.get("binding_generation") or 0)
        try:
            await asyncio.to_thread(self._storage.cas_update, _BINDINGS, key, generation, values)
        except StaleGeneration:
            if not retry:
                raise
            fresh = await asyncio.to_thread(self._storage.get, _BINDINGS, key)
            if fresh is None:
                raise
            await asyncio.to_thread(
                self._storage.cas_update, _BINDINGS, key,
                int(fresh.get("binding_generation") or 0), values,
            )
        updated = await asyncio.to_thread(self._storage.get, _BINDINGS, key)
        return updated or dict(row)

    async def _other_binding(
        self, repo_id: str, space: str, intent_dir: str, slot_key: str
    ) -> dict | None:
        """Another intent already bound to this slot, if any."""
        rows = await asyncio.to_thread(self._storage.select, _BINDINGS, {"slot_key": slot_key})
        for row in rows or ():
            if (
                _text(row.get("repo_id")) == repo_id
                and _text(row.get("space")) == (_text(space) or C.DEFAULT_SPACE)
                and _text(row.get("intent_dir")) == _text(intent_dir)
            ):
                continue
            return row
        return None

    async def _actions_in(
        self, repo_id: str, space: str, intent_dir: str, statuses: Sequence[str]
    ) -> list[dict]:
        return await asyncio.to_thread(
            self._storage.select,
            "actions",
            {
                "repo_id": repo_id,
                "space": _text(space) or C.DEFAULT_SPACE,
                "intent_dir": _text(intent_dir),
                "status": list(statuses),
            },
        )

    async def _refuse_when_in_flight(
        self, repo_id: str, space: str, intent_dir: str, *, operation: str
    ) -> None:
        rows = await self._actions_in(repo_id, space, intent_dir, IN_FLIGHT_ACTION_STATUS)
        if rows:
            raise StudioError(
                "action_not_submittable",
                "a decision for this intent may still be in flight",
                details={
                    "operation": operation,
                    "action_ids": [_text(r.get("action_id")) for r in rows],
                    "statuses": sorted({_text(r.get("status")) for r in rows}),
                },
            )

    async def _refuse_when_live(
        self, repo_id: str, space: str, intent_dir: str, *, operation: str
    ) -> None:
        rows = await self._actions_in(repo_id, space, intent_dir, C.LIVE_ACTION_STATUS)
        if rows:
            raise StudioError(
                "action_not_submittable",
                "this intent still has live cards in the queue",
                details={
                    "operation": operation,
                    "action_ids": [_text(r.get("action_id")) for r in rows],
                    "statuses": sorted({_text(r.get("status")) for r in rows}),
                },
            )

    async def _record(self, kind: str, row: Mapping[str, Any], *, params: Mapping[str, Any]) -> None:
        await self._activity.record(
            kind=kind,
            severity="info",
            repo_id=_text(row.get("repo_id")) or None,
            space=_text(row.get("space")) or None,
            intent_dir=_text(row.get("intent_dir")) or None,
            session_key=_text(row.get("canonical_session_key")) or None,
            params=dict(params),
        )
