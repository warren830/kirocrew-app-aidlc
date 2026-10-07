"""The one place Studio touches a KiroCrew object, and the record of which slot an intent talks to.

``HostBridge`` is the only module that names a host attribute (§1.11), and it only observes: no private
host entry point that starts, queues or stops a turn is named here and no host attribute is assigned
(``test_sessions.py`` checks both on this file's AST). A read that cannot be performed answers
``None``/``[]`` and degrades one named capability on ``GET /health`` rather than raising, and it never
answers "idle" for "cannot tell": the lease grants and delivery proofs count an unknown as busy.

``SessionBinder`` owns the ``intent_bindings`` row: which slot an intent's decisions go to, and whether
the intent is paused, archived or interrupted. It never creates a slot, sends a prompt or stops a turn.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from . import constants as C
from . import security
from .errors import StaleGeneration, StudioError
from .projection import StableBoundary

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .activity import ActivityProjector
    from .projection import Projection
    from .repo_registry import RepoRecord
    from .storage import Storage


__all__ = ["BUSY_REASONS", "BindingView", "CAPABILITIES", "HostBridge", "HostCapability", "NOTIFY_KIND",
           "SessionBinder", "SessionRef", "SlotView", "TakeoverCandidate", "TranscriptRow", "busy_reasons"]


# --------------------------------------------------------------------------- #
# vocabulary and records
# --------------------------------------------------------------------------- #

#: What ``GET /health`` reports under ``host.capabilities``: one name per host seam, so a missing symbol
#: switches off exactly the feature that needed it.
CAPABILITIES = ("state", "slots", "slot_messages", "sessions_busy", "owner", "notify", "slack", "subagents",
                "pending_questions")

#: Reason codes, never prose (C11). A host symbol that moved reads ``missing:<symbol>`` (absent) or
#: ``unusable:<symbol>`` (raised or misshapen), so an upgrade is diagnosable from one ``/health`` line.
REASON_NOT_ATTACHED = "host_not_attached"
#: The slot is not in the host's table (a tab was closed): a real answer, so it still supports a proof.
REASON_SLOT_ABSENT = "slot_absent"
REASON_SLACK_NOT_CONFIGURED = "slack_not_configured"
REASON_OWNER_UNKNOWN = "owner_unknown"

#: ``state.notify`` kind: a protocol string (the host derives ``system.<kind>``; the UI filters on it).
NOTIFY_KIND = "aidlc_studio"

#: Re-probe the attached host at most this often, so a seam a failed read switched off is re-judged even
#: when nothing reads it again.
HOST_PROBE_TTL_SECS = 30.0

#: The golden order (§1.11): the first reason is what the UI shows as "why can I not send this".
BUSY_REASONS = ("running", "stage_execution", "stopping", "queued", "approval_pending", "subagents",
                "deliveries_inflight")

#: Transcript roles that prove a human-lane message reached the host. A message accepted behind a running
#: turn is ``queued``, not appended, and it *is* delivered (review R02).
DELIVERY_ROLES = ("user", "queued")

#: Something may still be on the wire: rebinding would send its delivery report to a session that no longer
#: owns it. Narrower than ``LIVE_ACTION_STATUS`` because rebinding is itself a recovery decision.
IN_FLIGHT_ACTION_STATUS = ("Delivering", "Delivered", "Processing")
#: The decision may already have reached AI-DLC, so archiving waits for these even on a paused intent.
ARCHIVE_BLOCKING_STATUS = IN_FLIGHT_ACTION_STATUS + ("DeliveryUncertain", "ReconciliationRequired")

#: Why a slot is a takeover candidate. ``current_binding`` classifies the slot §2.3 returns as ``current``.
TAKEOVER_REASONS = ("project_match", "name_match", "current_binding")

_BINDINGS = "intent_bindings"
ARCHIVE_ACTIVE = "active"
ARCHIVE_ARCHIVED = "archived"

#: Longest approval text Studio repeats on a card or in a notification.
_APPROVAL_TEXT_LIMIT = 500
#: The host's implicit-owner subjects, copied so the fallback owner gate stays exact.
_LOCAL_OWNER_SUBJECTS = frozenset({"local-app", "local-startup"})


def _instant(value: object) -> float | None:
    """A Studio (``…Z``) or host (``isoformat()``) stamp as POSIX seconds; ``None`` when unreadable. The two
    do not compare as strings (``.`` < ``Z``), which would hide the row appended in the dispatch's second."""
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
    return (parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed).timestamp()


def _at_or_after(row_ts: object, since_ts: object) -> bool:
    """Is ``row_ts`` at or after ``since_ts``? Unreadable input answers yes: an extra row costs a human a
    card to read, while a dropped one lets Studio conclude "never delivered" and send a decision twice."""
    since, row = _instant(since_ts), _instant(row_ts)
    return since is None or row is None or row >= since


@dataclass(frozen=True, slots=True)
class HostCapability:
    """One host seam and whether it can be used right now."""

    name: str
    available: bool
    reason: str | None
    checked_at: str


#: ``slot.to_dict()`` keys copied as text, as flags and as counts; the rest are mapped one by one.
_TEXT_KEYS = ("key", "project", "agent", "app", "last_ts", "linked_session_key")
_FLAG_KEYS = ("running", "pending_approval", "needs_input", "waiting_for_input", "interrupted", "has_options",
              "slack_linked", "stopping")
_COUNT_KEYS = ("queue_depth", "messages")


@dataclass(frozen=True, slots=True)
class SlotView:
    """An immutable snapshot of one host chat slot: a copy, because the live slot mutates while a decision
    is validated, and a precondition checked on it could be true when read and false when acted on."""

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
    in_stage_execution: bool = False
    approvals_pending: int = 0
    subagents_running: int = 0
    subagents_queued: int = 0
    deliveries_inflight: int = 0
    #: The newest unresolved tool approval (``tool``, ``tool_input``, ``request_id``); not on the wire.
    approval: Mapping[str, str] | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], **facts: Any) -> "SlotView":
        """Map ``slot.to_dict()`` (a key the host does not send degrades one field); ``facts`` carries the
        effective session key and the busy counters that are not in it."""
        get = payload.get
        return cls(
            **{name: _text(get(name)) for name in _TEXT_KEYS},
            **{name: bool(get(name, False)) for name in _FLAG_KEYS},
            **{name: _count(get(name)) for name in _COUNT_KEYS},
            # An unknown stop state passes through: anything but "idle" reads as stopping and refuses.
            stop_state=_text(get("stop_state")) or "idle",
            last_turn_ts=_text(get("last_turn_ts")) or _text(get("last_ts")),
            options=tuple(_text(o) for o in (get("options") or ())),
            title=_text(get("title")) or _text(get("key")),
            wait_state=dict(get("wait_state")) if isinstance(get("wait_state"), Mapping) else None,
            in_stage_execution=bool(get("orchestrating", False)),
            **facts,
        )

    @property
    def busy_reasons(self) -> tuple[str, ...]:
        """The module predicate, reachable off the view: the projection reads it without importing this."""
        return busy_reasons(self)

    def to_json(self) -> dict[str, Any]:
        """Exactly the §3.1 ``SlotView`` interface (the UI type is pinned): every field but ``approval``."""
        out = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "approval"}
        out["options"] = list(self.options)
        out["wait_state"] = dict(self.wait_state) if self.wait_state is not None else None
        return out


def busy_reasons(view: Any) -> tuple[str, ...]:
    """Every reason this slot cannot take a dispatch right now; ``()`` == dispatchable. A view missing a
    field raises rather than defaulting: ``submit`` and the lease grants read an unanswerable one as busy."""
    busy = (
        view.running,
        view.in_stage_execution,
        (view.stop_state or "idle") != "idle" or view.stopping,
        (view.queue_depth or 0) > 0,
        (view.approvals_pending or 0) > 0,
        (view.subagents_running or 0) + (view.subagents_queued or 0) > 0,
        (view.deliveries_inflight or 0) > 0,
    )
    return tuple(reason for reason, hit in zip(BUSY_REASONS, busy) if hit)


@dataclass(frozen=True, slots=True)
class TranscriptRow:
    """One host transcript row, as evidence. ``content`` is redacted; ``meta`` is not, because the host
    redacted its strings at append time and masking again would corrupt ``studio_delivery_id``."""

    role: str
    content: str
    ts: str
    cls: str
    meta: dict


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _count(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _host_symbol(module: str, name: str) -> Any:
    """A function of an *already imported* host module, or ``None``. Never imports: that is disk work on
    the event loop, and in the gateway the module is loaded by definition."""
    return getattr(sys.modules.get(module), name, None)


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
        self._turn_timeout: int | None = None
        self._reset(REASON_NOT_ATTACHED)

    # ---- attachment and capabilities -------------------------------------- #

    def attach(self, state: object) -> None:
        """Bind (or re-bind) the ``DashboardState``; cheap enough for every request. Lazy because
        ``on_startup`` has no ``web.Application`` (C08): until then Studio runs disk-only, not in error."""
        if state is None:
            return
        if state is not self._state:
            self._state = state
            self._turn_timeout = None
        elif self._probed_at is not None and self._clock.monotonic() - self._probed_at < HOST_PROBE_TTL_SECS:
            return
        self._probe()

    def attached(self) -> bool:
        return self._state is not None

    def capabilities(self) -> dict[str, HostCapability]:
        """What works right now, with the reason for everything that does not (§2.1, ctx.health)."""
        return dict(self._caps)

    def can_prove_absence(self) -> bool:
        """Does a ``None`` from the transcript readers mean "not there" rather than "cannot look"? Ask AFTER
        the reads it qualifies: reading "cannot look" as absence would let a renamed attribute prove
        ``NotDelivered``. A missing slot (``REASON_SLOT_ABSENT``) is still a real answer about the host."""
        slots, rows = self._caps.get("slots"), self._caps.get("slot_messages")
        if slots is None or not slots.available:
            return False
        return rows is None or rows.available or rows.reason == REASON_SLOT_ABSENT

    # ---- slot reads ------------------------------------------------------- #

    def slot(self, slot_key: str) -> SlotView | None:
        """The named slot, or ``None`` when it does not exist (or cannot be read)."""
        slot = self._raw_slot(slot_key)
        return None if slot is None else self._view(slot)

    def slots_for_project(self, canonical_path: str) -> list[SlotView]:
        """Every slot bound to this repository root, whatever its name: a session opened by hand or renamed
        by the host can still move this repository's AI-DLC state, so takeover must see it."""
        wanted = _realpath(canonical_path)
        return [view for view in self._all_views() if _same_dir(view.project, wanted)]

    def slots_by_prefix(self, prefix: str) -> list[SlotView]:
        """Slots whose key starts with ``prefix`` (Studio's canonical naming)."""
        needle = str(prefix or "")
        return [view for view in self._all_views() if view.key.startswith(needle)]

    def slot_matches_repo(self, view: SlotView, canonical_path: str) -> bool:
        """Is this slot a legitimate target for the repository's AI-DLC decisions? Project and agent must
        match, and ``app`` must be empty (a slot the user opened) or Studio's own."""
        if view is None:
            return False
        if _text(getattr(view, "agent", "")) != C.AIDLC_AGENT_NAME:
            return False
        if _text(getattr(view, "app", "")) not in ("", C.APP_NAME):
            return False
        return _same_dir(_text(getattr(view, "project", "")), _realpath(canonical_path))

    def recent_rows(self, slot_key: str, *, since_ts: str, roles: Sequence[str] = DELIVERY_ROLES,
                    limit: int = 50) -> list[TranscriptRow]:
        """Newest-first transcript rows in ``roles`` at or after ``since_ts``. ``slot.messages`` is process
        memory, so ``[]`` is "no evidence in this process", never "not delivered" (review R02)."""
        rows = self._raw_rows(slot_key)
        if not rows:
            return []
        wanted = frozenset(str(r) for r in (roles or ()))
        cap = max(1, int(limit or 1))
        matches = (_row_view(row) for row in reversed(rows)
                   if isinstance(row, Mapping) and (not wanted or _text(row.get("role")) in wanted)
                   and _at_or_after(row.get("ts"), since_ts))
        return list(islice(matches, cap))

    def find_delivery_row(self, slot_key: str, *, since_ts: str, delivery_id: str,
                          wire_text: str) -> TranscriptRow | None:
        """The row proving this delivery reached the host: by ``meta.studio_delivery_id``, then exact text,
        then the pending queue, where a message sent behind a running turn waits without the browser's
        meta and without a row; reading it as absent would be a proof of ``NotDelivered`` (C24)."""
        wanted_id, wanted_text = str(delivery_id or ""), str(wire_text or "")
        rows = [row for row in reversed(self._raw_rows(slot_key)) if isinstance(row, Mapping)
                and _text(row.get("role")) in DELIVERY_ROLES and _at_or_after(row.get("ts"), since_ts)]
        if wanted_id:
            for row in rows:
                if _delivery_id(row.get("meta")) == wanted_id:
                    return _row_view(row)
        if wanted_text:
            for row in rows:
                if _text(row.get("content")) == wanted_text:
                    return _row_view(row)
        return self._queued_delivery(slot_key, delivery_id=wanted_id, wire_text=wanted_text)

    def pending_question_cards(self, slot_key: str) -> list[dict]:
        """The host's question cards for this slot, in its API's order (review R13): blocking asks from the
        state's wait registry, then the slot's non-blocking cards. No card is the degraded mode (A05)."""
        key = str(slot_key or "")
        state = self._host("pending_questions")
        if state is None:
            return []
        out = _cards(getattr(state, "_pending_questions", None), "ask_id", key,
                     lambda rec: _text(rec.get("slot")) == key)
        slot = self._raw_slot(key)
        if slot is None:
            return out
        pending = getattr(slot, "_question_pending", None)
        if not isinstance(pending, Mapping):
            kind = "missing" if pending is None else "unusable"
            self._note("pending_questions", f"{kind}:slot._question_pending")
            return out
        out += _cards(pending, "card_id", key, lambda rec: not rec.get("blocking"))
        self._note("pending_questions")
        return out

    # ---- session and process reads ---------------------------------------- #

    def is_busy(self, session_key: str) -> bool | None:
        """Session-level busy state; ``None``, never ``False``, when the host cannot answer, because the
        delivery proofs refuse on an unobservable turn."""
        state = self._host("sessions_busy")
        if state is None:
            return None
        sessions = getattr(state, "sessions", None)
        if sessions is None:
            return self._note("sessions_busy", "missing:state.sessions")
        try:
            busy = bool(sessions.is_busy(str(session_key or "")))
        except (AttributeError, TypeError, KeyError) as exc:
            return self._note("sessions_busy", "unusable:state.sessions.is_busy", exc)
        self._note("sessions_busy")
        return busy

    def is_owner_request(self, request: Any) -> bool:
        """Is this the dashboard owner's own session (§1.24)? The host's predicate when its module is loaded,
        else its rule repeated exactly (no app token, the owner, else a local bootstrap subject)."""
        probe = _host_symbol("kiro_crew.dashboard.handlers.source_providers", "is_owner_dashboard_request")
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
        owner = _text(getattr(self._state, "owner_id", ""))
        return bool(caller) and (caller == owner if owner else caller in _LOCAL_OWNER_SUBJECTS)

    def turn_timeout_secs(self) -> int:
        """The host's chat-turn ceiling, capped by ``PROCESSING_DEADLINE_CAP_SECS``. Resolved once: it is a
        config read from disk, and the reconciler asks every tick."""
        if self._turn_timeout is None:
            cap = int(C.PROCESSING_DEADLINE_CAP_SECS)
            value = float(cap)
            config = _host_symbol("kiro_crew.config", "KiroCrewConfig")
            if config is not None:
                try:
                    value = float(config.load().agent.chat_turn_timeout_secs)
                except Exception as exc:  # the config file is the host's, and may be anything
                    self._log.debug("chat turn ceiling unreadable, using the cap: %s", exc)
            self._turn_timeout = max(1, min(int(value), cap)) if value > 0 else cap
        return self._turn_timeout

    def subagent(self, spawn_id: str) -> dict | None:
        """One subagent record (the advisor's own spawn), or ``None``; model text is redacted."""
        state = self._host("subagents")
        if state is None:
            return None
        manager = getattr(state, "subagents", None)
        if manager is None:
            return self._note("subagents", "missing:state.subagents")
        try:
            info = manager.get(str(spawn_id or ""))
        except (AttributeError, TypeError, KeyError) as exc:
            return self._note("subagents", "unusable:state.subagents.get", exc)
        self._note("subagents")
        if info is None:
            return None
        return {
            "id": _text(getattr(info, "id", spawn_id)), "done": bool(getattr(info, "done", False)),
            "queued": bool(getattr(info, "queued", False)),
            "result": security.redact(_text(getattr(info, "result", ""))),
            "result_path": _text(getattr(info, "result_path", "")),
            "result_truncated": bool(getattr(info, "result_truncated", False)),
            "error": security.redact(_text(getattr(info, "error", ""))),
            "agent": _text(getattr(info, "agent", "")), "app": _text(getattr(info, "app", "")),
        }

    def subagent_result_text(self, record: Mapping[str, Any] | None) -> str | None:
        """The whole answer, from ``result_path`` when the completion event kept only its first
        ``agent.completion_keep_chars``. ``None`` means "not available", never "empty": the caller fails the
        draft rather than parse half a JSON object. A file the host cleaned up degrades no capability."""
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
        return security.redact(text)

    # ---- the only two host writes ---------------------------------------- #

    def notify(self, title: str, body: str, *, url: str, meta: dict | None = None) -> bool:
        """Push a dashboard notification; ``False``, never a raise (the decision is already durable). A link
        outside ``DEEP_LINK_BASE`` is refused here: the host would silently drop it, and the Open button."""
        target = _text(url)
        if not _is_studio_link(target):
            self._log.warning("refusing a notification whose url is outside %s", C.DEEP_LINK_BASE)
            return False
        state = self._host("notify")
        if state is None:
            return False
        send = getattr(state, "notify", None)
        if not callable(send):
            self._note("notify", "missing:state.notify")
            return False
        try:
            send(NOTIFY_KIND, _text(title), _text(body), meta=dict(meta or {}), url=target)
        except Exception as exc:  # the host's bus documents "never raises", so this is drift
            self._note("notify", "unusable:state.notify", exc)
            return False
        self._note("notify")
        return True

    async def slack_dm(self, blocks: list[dict], text: str) -> str | None:
        """DM the owner and return the message timestamp, or ``None`` on any failure. A deep link only (no
        quick actions, C13): the decision is made in Studio, where the evidence and staleness check are."""
        state = self._host("slack")
        if state is None:
            return None
        client = getattr(state, "slack_client", None)
        if client is None:
            return self._note("slack", REASON_SLACK_NOT_CONFIGURED)
        owner = _text(getattr(state, "owner_id", ""))
        if not owner:
            return self._note("slack", REASON_OWNER_UNKNOWN)
        try:
            channel = await client.open_dm(owner)
            if not channel:
                return self._note("slack", "unusable:slack_client.open_dm")
            ts = await client.post_blocks(str(channel), list(blocks or ()), _text(text))
        except Exception as exc:  # transport, auth, rate limit: all the same answer to the caller
            return self._note("slack", "unusable:state.slack_client", exc)
        self._note("slack")
        return _text(ts) or None

    # ---- internals -------------------------------------------------------- #

    def _probe(self) -> None:
        """Judge every seam from the host's shape with ``getattr`` alone, so attaching runs no host code.
        ``slot_messages`` and ``pending_questions`` need a slot: the first read that fails corrects them."""
        self._probed_at = self._clock.monotonic()
        state = self._state
        if state is None:
            self._reset(REASON_NOT_ATTACHED)
            return
        slots = getattr(state, "_slots", None)
        table = callable(getattr(state, "get_slot", None)) or isinstance(slots, Mapping)
        sessions = getattr(state, "sessions", None)
        owned = bool(_text(getattr(state, "owner_id", "")))
        client = getattr(state, "slack_client", None)
        manager = getattr(state, "subagents", None)
        dm = callable(getattr(client, "open_dm", None)) and callable(getattr(client, "post_blocks", None))
        checks: dict[str, list[tuple[object, str]]] = {
            "state": [(table, "missing:state.get_slot")],
            "slots": [(table, "missing:state._slots")],
            "slot_messages": [],
            "pending_questions": [],
            "sessions_busy": [(sessions is not None, "missing:state.sessions"),
                              (callable(getattr(sessions, "is_busy", None)),
                               "missing:state.sessions.is_busy")],
            "owner": [(hasattr(state, "owner_id"), "missing:state.owner_id"), (owned, REASON_OWNER_UNKNOWN)],
            "notify": [(callable(getattr(state, "notify", None)), "missing:state.notify")],
            "slack": [(client is not None, REASON_SLACK_NOT_CONFIGURED),
                      (dm, "missing:slack_client.post_blocks"), (owned, REASON_OWNER_UNKNOWN)],
            "subagents": [(manager is not None, "missing:state.subagents"),
                          (callable(getattr(manager, "get", None)), "missing:state.subagents.get")],
        }
        for name, gaps in checks.items():
            self._set(name, next((reason for ok, reason in gaps if not ok), None))

    def _reset(self, reason: str) -> None:
        for name in CAPABILITIES:
            self._set(name, reason)

    def _set(self, name: str, reason: str | None) -> None:
        self._caps[name] = HostCapability(name, reason is None, reason, self._clock.iso())

    def _note(self, name: str, reason: str | None = None, exc: BaseException | None = None) -> None:
        """Report one read: the single path every failed read leaves through (answering ``None``), and how a
        seam that works again recovers. An available capability keeps its stamp."""
        if exc is not None:
            self._log.debug("host capability %s unavailable (%s): %s", name, reason, exc)
        current = self._caps.get(name)
        if reason is not None or current is None or not current.available:
            self._set(name, reason)

    def _host(self, name: str) -> Any:
        """The attached state, or ``None`` after marking ``name`` not attached: Studio running disk-only
        (C08), not a host whose symbols moved."""
        if self._state is None:
            self._note(name, REASON_NOT_ATTACHED)
        return self._state

    def _raw_slot(self, slot_key: str) -> Any:
        if self._host("slots") is None:
            return None
        getter = getattr(self._state, "get_slot", None)
        if not callable(getter):
            return self._note("slots", "missing:state.get_slot")
        try:
            slot = getter(str(slot_key or ""))
        except (AttributeError, TypeError, KeyError) as exc:
            return self._note("slots", "unusable:state.get_slot", exc)
        self._note("slots")
        return slot

    def _all_views(self) -> list[SlotView]:
        if self._host("slots") is None:
            return []
        table = getattr(self._state, "_slots", None)
        if not isinstance(table, Mapping):
            self._note("slots", "missing:state._slots")
            return []
        try:
            listed = list(table.values())  # a snapshot: the loop may open or close a slot mid-walk
        except (AttributeError, TypeError, RuntimeError) as exc:
            self._note("slots", "unusable:state._slots", exc)
            return []
        self._note("slots")
        return [view for view in map(self._view, listed) if view is not None]

    def _raw_rows(self, slot_key: str) -> list[Any]:
        if self._host("slot_messages") is None:
            return []
        slot = self._raw_slot(slot_key)
        if slot is None:
            self._note("slot_messages", REASON_SLOT_ABSENT)
            return []
        messages = getattr(slot, "messages", None)
        if messages is None:
            self._note("slot_messages", "missing:slot.messages")
            return []
        try:
            rows = list(messages)
        except (TypeError, RuntimeError) as exc:
            self._note("slot_messages", "unusable:slot.messages", exc)
            return []
        self._note("slot_messages")
        return rows

    def _queued_delivery(self, slot_key: str, *, delivery_id: str, wire_text: str) -> TranscriptRow | None:
        """A match still waiting in the slot's pending queue, rendered as the ``queued`` row it is."""
        slot = self._raw_slot(slot_key)
        queue = getattr(slot, "_queue", None) if slot is not None else None
        if not isinstance(queue, (list, tuple)):
            return None
        for item in list(queue):
            if not isinstance(item, Mapping):
                continue
            if (delivery_id and _delivery_id(item.get("meta")) == delivery_id) or (
                wire_text and _text(item.get("content")) == wire_text
            ):
                return TranscriptRow(role="queued", content=security.redact(_text(item.get("content"))),
                                     ts=_text(getattr(slot, "_last_enqueue_ts", "")), cls="queue",
                                     meta={"queue_id": _text(item.get("id"))})
        return None

    def _view(self, slot: Any) -> SlotView | None:
        try:
            payload = slot.to_dict()
        except (AttributeError, TypeError, KeyError) as exc:
            return self._note("slots", "unusable:slot.to_dict", exc)
        if not isinstance(payload, Mapping):
            return self._note("slots", "unusable:slot.to_dict")
        session_key = _session_key(slot)
        approvals = _approvals_pending(slot, payload)
        running, queued = self._subagent_depth(session_key)
        return SlotView.from_payload(
            payload,
            session_key=session_key,
            approvals_pending=approvals,
            subagents_running=running,
            subagents_queued=queued,
            deliveries_inflight=_count(getattr(slot, "_subagent_deliveries_inflight", 0)),
            approval=_pending_approval(payload.get("pending_approval_info")) if approvals else None,
        )

    def _subagent_depth(self, session_key: str) -> tuple[int, int]:
        """Running and queued subagents of this session: a queued wave is pending work, not idleness."""
        manager = getattr(self._state, "subagents", None)
        if manager is None:
            self._note("subagents", "missing:state.subagents")
            return 0, 0
        try:
            running = len(list(manager.running_agents_for(session_key) or ()))
        except (AttributeError, TypeError, KeyError) as exc:
            self._note("subagents", "unusable:state.subagents.running_agents_for", exc)
            return 0, 0
        try:
            queued = _count(manager.queued_count_for(session_key))
        except (AttributeError, TypeError, KeyError) as exc:
            self._log.debug("subagent queue depth unreadable: %s", exc)
            queued = 0
        self._note("subagents")
        return running, queued


def _session_key(slot: Any) -> str:
    """``chat_utils.effective_session_key(slot)``, or its rule when the module is not loaded: a linked
    (channel) key wins, else ``dashboard:<key>`` with legacy ``dashboard_`` prefixes stripped."""
    resolve = _host_symbol("kiro_crew.dashboard.chat_utils", "effective_session_key")
    if resolve is not None:
        try:
            return _text(resolve(slot))
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


def _approvals_pending(slot: Any, payload: Mapping[str, Any]) -> int:
    """Undone approval futures on the slot, else 1 when the host reports a sub-agent's approval, which waits
    on the state-level coordinator: uncounted, it was never shown or notified."""
    futures = getattr(slot, "_approval_futures", None)
    count = sum(1 for fut in list(futures.values()) if _undone(fut)) if isinstance(futures, Mapping) else 0
    return count or int(bool(payload.get("pending_approval")))


def _undone(future: Any) -> bool:
    try:
        return not future.done()
    except (AttributeError, TypeError):
        return True  # an unreadable future is a pending one, never a resolved one


def _pending_approval(info: Any) -> dict[str, str]:
    """What the waiting tool approval asks for, from the host's ``pending_approval_info`` (redacted model
    text). An approval nothing describes still waits, so the answer is then empty, never ``None``."""
    info = info if isinstance(info, Mapping) else {}
    return {
        "tool": security.redact(_text(info.get("tool")))[:_APPROVAL_TEXT_LIMIT],
        "tool_input": security.redact(_text(info.get("tool_input")))[:_APPROVAL_TEXT_LIMIT],
        "request_id": _text(info.get("request_id")),
    }


def _delivery_id(meta: Any) -> str:
    return _text(meta.get("studio_delivery_id")) if isinstance(meta, Mapping) else ""


def _row_view(row: Mapping[str, Any]) -> TranscriptRow:
    meta = row.get("meta")
    return TranscriptRow(role=_text(row.get("role")), content=security.redact(_text(row.get("content"))),
                         ts=_text(row.get("ts")), cls=_text(row.get("cls")),
                         meta=dict(meta) if isinstance(meta, Mapping) else {})


def _cards(records: Any, id_key: str, slot_key: str, keep: Callable[[Mapping], bool]) -> list[dict]:
    """Question cards out of a host registry, as Studio's own copies; status-only markers are skipped."""
    out: list[dict] = []
    if not isinstance(records, Mapping):
        return out
    for card_id, rec in list(records.items()):
        if not isinstance(rec, Mapping) or not keep(rec):
            continue
        raw = rec.get("questions")
        questions = [dict(q) for q in raw if isinstance(q, Mapping)] if isinstance(raw, (list, tuple)) else []
        if questions:
            out.append({id_key: _text(card_id), "slot": slot_key, "questions": questions,
                        "ts": rec.get("ts", 0)})
    return out


def _is_studio_link(url: str) -> bool:
    """A deep link into Studio's page; the base must end at a boundary, or ``…-elsewhere`` would pass."""
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
    """Do these paths name one directory? ``realpath``, then inode identity: on case-insensitive APFS
    ``realpath`` keeps the caller's casing (A13), and a mismatch would refuse every dispatch."""
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


@dataclass(frozen=True, slots=True)
class SessionRef:
    """Which session an intent talks to (§2.3's ``IntentSummary.session``); ``waiting_approval`` shows a run
    parked on a host tool approval where the intent is, not only in the chat."""

    slot_key: str
    session_key: str
    running: bool
    bound_at: str
    waiting_approval: bool = False
    approval: Mapping[str, str] | None = None

    def to_json(self) -> dict[str, Any]:
        approval = (
            {"tool": self.approval.get("tool", ""), "tool_input": self.approval.get("tool_input", "")}
            if self.waiting_approval and self.approval is not None else None
        )
        return {"slot_key": self.slot_key, "session_key": self.session_key, "running": self.running,
                "bound_at": self.bound_at, "waiting_approval": self.waiting_approval, "approval": approval}


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
            repo_id=_text(row.get("repo_id")), space=space, intent_dir=intent_dir,
            intent_key=C.intent_key_for(space, intent_dir), intent_uuid=row.get("intent_uuid") or None,
            slot_key=row.get("slot_key") or None, session_key=row.get("canonical_session_key") or None,
            binding_generation=int(row.get("binding_generation") or 0),
            archive_state=_text(row.get("archive_state")) or ARCHIVE_ACTIVE,
            paused=bool(row.get("paused")), paused_at=row.get("paused_at") or None,
            interrupted_at=row.get("interrupted_at") or None,
            keep_moving=bool(row.get("keep_moving")),  # always False in v1: there is no machine lane (A04)
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
        out = {f.name: getattr(self, f.name) for f in fields(self)}
        boundary = self.last_stable_boundary
        out["last_stable_boundary"] = boundary.to_json() if boundary is not None else None
        return out


@dataclass(frozen=True, slots=True)
class TakeoverCandidate:
    """A slot the user could move this intent's decisions to, and why it is a candidate."""

    slot: SlotView
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {"slot": self.slot.to_json(), "reason": self.reason}


def _decode_boundary(raw: object) -> StableBoundary | None:
    """The stored boundary as the projection's real dataclass: readers use ``getattr`` (``boundary.stable``),
    and a dict would quietly read as unstable everywhere."""
    if isinstance(raw, StableBoundary):
        return raw
    if not isinstance(raw, Mapping):
        return None
    return StableBoundary(
        stable=bool(raw.get("stable")), reasons=tuple(_text(r) for r in (raw.get("reasons") or ())),
        stage=raw.get("stage") or None, marker=raw.get("marker") or None,
        boundary_token=raw.get("boundary_token") or None, recorded_at=_text(raw.get("recorded_at")),
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

    def __init__(self, storage: "Storage", host: HostBridge, projection: "Projection", clock: C.Clock,
                 activity: "ActivityProjector") -> None:
        # `projection` stays in the §1.11 wiring; the stored boundary decodes without it.
        self._storage = storage
        self._host = host
        self._clock = clock
        self._activity = activity

    # ---- reads ------------------------------------------------------------ #

    async def get(self, repo_id: str, space: str, intent_dir: str) -> BindingView:
        """The binding, creating an unbound row on first access: pause, interruption and the stable boundary
        are facts about the intent, which can have them before it ever had a session."""
        return BindingView.from_row(await self._ensure(repo_id, space, intent_dir))

    async def session_ref(self, binding: BindingView) -> SessionRef | None:
        """The live session shape, or ``None`` when unbound; ``bound_at`` is when the row was last written."""
        slot_key = _text(getattr(binding, "slot_key", ""))
        if not slot_key:
            return None
        view = self._host.slot(slot_key)
        waiting = view is not None and (view.approvals_pending > 0 or view.pending_approval)
        return SessionRef(
            slot_key=slot_key,
            session_key=_text(getattr(binding, "session_key", ""))
            or (view.session_key if view is not None else ""),
            running=bool(view.running) if view is not None else False,
            bound_at=_text(getattr(binding, "updated_at", "")),
            waiting_approval=waiting,
            approval=view.approval if waiting and view is not None else None,
        )

    async def waiting_approvals(self) -> list[dict[str, Any]]:
        """Every bound, unarchived intent whose conversation is parked on a host tool approval, read from
        the host each call; the host's own controls in the conversation answer it."""
        out: list[dict[str, Any]] = []
        for row in await asyncio.to_thread(self._storage.select, _BINDINGS):
            slot_key = _text(row.get("slot_key"))
            if not slot_key or row.get("archive_state") == ARCHIVE_ARCHIVED:
                continue
            view = self._host.slot(slot_key)
            if view is None or not (view.approvals_pending > 0 or view.pending_approval):
                continue
            approval = view.approval or {}
            space = _text(row.get("space")) or C.DEFAULT_SPACE
            intent_dir = _text(row.get("intent_dir"))
            out.append({
                "repo_id": _text(row.get("repo_id")), "space": space, "intent_dir": intent_dir,
                "intent_key": C.intent_key_for(space, intent_dir), "slot_key": slot_key,
                "tool": _text(approval.get("tool")), "tool_input": _text(approval.get("tool_input")),
            })
        return out

    async def archived_intents(self) -> frozenset[tuple[str, str, str]]:
        """``(repo_id, space, intent_dir)`` of every archived intent: what the queue sets aside (§11.2)."""
        rows = await asyncio.to_thread(self._storage.select, _BINDINGS, {"archive_state": ARCHIVE_ARCHIVED})
        return frozenset(
            (_text(r.get("repo_id")), _text(r.get("space")) or C.DEFAULT_SPACE, _text(r.get("intent_dir")))
            for r in rows
        )

    async def canonical_slot_name(self, repo_id: str, intent_dir: str) -> str:
        """The slot name Studio asks the UI to create for this intent (§0.1)."""
        return C.SLOT_KEY_TEMPLATE.format(repo_id=repo_id, intent_dir=intent_dir)

    # ---- binding ---------------------------------------------------------- #

    async def bind(self, repo: "RepoRecord", space: str, intent_dir: str, *, slot_key: str,
                   intent_uuid: str | None) -> BindingView:
        """Record which slot this intent's decisions go to, after proving it is the right one. Every check
        refuses rather than repairs: a wrong slot would take a gate approval into another audit trail."""
        repo_id = _text(C.attr(repo, "repo_id"))
        canonical = _text(C.attr(repo, "canonical_path"))
        key = _text(slot_key)
        if not key:
            raise StudioError("bad_param", "slot_key is required", details={"field": "slot_key"})
        if not self._host.attached():
            raise StudioError("host_unavailable",
                              "the dashboard state is not attached, so the slot cannot be verified",
                              details={"reason": REASON_NOT_ATTACHED})
        view = self._host.slot(key)
        if view is None:
            raise StudioError("slot_mismatch", "no such host slot", details={
                "slot_key": key, "reason": "slot_missing", "expected_project": canonical,
                "actual_project": None, "expected_agent": C.AIDLC_AGENT_NAME, "actual_agent": None,
            })
        if not self._host.slot_matches_repo(view, canonical):
            message = "the slot is not bound to this repository with the aidlc agent"
            raise StudioError("slot_mismatch", message, details={
                "slot_key": key, "reason": "slot_mismatch", "expected_project": canonical,
                "actual_project": view.project, "expected_agent": C.AIDLC_AGENT_NAME,
                "actual_agent": view.agent, "actual_app": view.app,
            })

        row = await self._ensure(repo_id, space, intent_dir)
        other = await self._other_binding(repo_id, space, intent_dir, key)
        if other is not None and view.running:
            # The running turn belongs to the intent bound to that slot; taking the slot mid-turn would
            # report that turn's outcome against the wrong workflow.
            bound = C.intent_key_for(_text(other.get("space")), _text(other.get("intent_dir")))
            raise StudioError("slot_busy", "the slot is running a turn for another intent", details={
                "slot_key": key, "reasons": list(busy_reasons(view)), "bound_intent_key": bound,
            })
        updated = await self._cas(row, {
            "slot_key": key,
            "canonical_session_key": view.session_key,
            "intent_uuid": intent_uuid if intent_uuid else row.get("intent_uuid"),
            "resolved_repo_identity": C.attr(repo, "resolved_identity"),
        })
        await self._record("session.bound", updated, params={
            "slot_key": key, "session_key": view.session_key,
            "previous_slot_key": row.get("slot_key") or None,
        })
        return BindingView.from_row(updated)

    async def unbind(self, repo_id: str, space: str, intent_dir: str, *, reason: str) -> BindingView:
        """Forget the slot. Refused while a decision may still be on the wire."""
        row = await self._ensure(repo_id, space, intent_dir)
        await self._refuse(repo_id, space, intent_dir, operation="unbind")
        previous = row.get("slot_key") or None
        updated = await self._cas(row, {"slot_key": None, "canonical_session_key": None})
        await self._record("session.unbound", updated,
                           params={"reason": _text(reason), "previous_slot_key": previous})
        return BindingView.from_row(updated)

    async def takeover_preview(self, repo: "RepoRecord", space: str,
                               intent_dir: str) -> list[TakeoverCandidate]:
        """Slots the user could hand this intent to, except its own: those bound to the repository root
        (whatever their name) and those carrying Studio's canonical name (whatever their project)."""
        repo_id = _text(C.attr(repo, "repo_id"))
        row = await self._ensure(repo_id, space, intent_dir)
        current = _text(row.get("slot_key"))
        seen: dict[str, TakeoverCandidate] = {}
        for view in self._host.slots_for_project(_text(C.attr(repo, "canonical_path"))):
            if view.key and view.key != current:
                seen.setdefault(view.key, TakeoverCandidate(slot=view, reason="project_match"))
        for view in self._host.slots_by_prefix(C.SLOT_KEY_TEMPLATE.format(repo_id=repo_id, intent_dir="")):
            if view.key and view.key != current:
                seen.setdefault(view.key, TakeoverCandidate(slot=view, reason="name_match"))
        return [seen[key] for key in sorted(seen)]

    async def takeover(self, repo: "RepoRecord", space: str, intent_dir: str, *,
                       slot_key: str) -> BindingView:
        """Move the intent to another slot. Refused while a decision may still be on the wire."""
        repo_id = _text(C.attr(repo, "repo_id"))
        row = await self._ensure(repo_id, space, intent_dir)
        await self._refuse(repo_id, space, intent_dir, operation="takeover")
        previous = row.get("slot_key") or None
        binding = await self.bind(repo, space, intent_dir, slot_key=slot_key,
                                  intent_uuid=row.get("intent_uuid"))
        await self._record("session.takeover", await self._row(repo_id, space, intent_dir),
                           params={"slot_key": binding.slot_key, "previous_slot_key": previous})
        return binding

    # ---- intent-level flags ---------------------------------------------- #

    async def set_paused(self, repo_id: str, space: str, intent_dir: str, paused: bool) -> BindingView:
        """Pause or resume dispatch (FR-RUN-003). Pause blocks every human-lane dispatch (C29), so it is
        Studio's own decision, kept on the binding rather than derived from anything AI-DLC writes."""
        row = await self._ensure(repo_id, space, intent_dir)
        want = bool(paused)
        updated = await self._cas(
            row, {"paused": 1 if want else 0, "paused_at": self._clock.iso() if want else None}, retry=True
        )
        await self._record("intent.paused" if want else "intent.resumed", updated, params={"paused": want})
        return BindingView.from_row(updated)

    async def set_interrupted(self, repo_id: str, space: str, intent_dir: str,
                              at: str | None) -> BindingView:
        """Stamp (or clear) the interruption a resolved ``force_stop`` leaves. Only the recovery card's
        ``acknowledge`` clears it (review P17), never run: PRD §11.2 requires a human to take over."""
        row = await self._ensure(repo_id, space, intent_dir)
        updated = await self._cas(row, {"interrupted_at": at or None}, retry=True)
        return BindingView.from_row(updated)

    async def set_archived(self, repo_id: str, space: str, intent_dir: str, archived: bool) -> BindingView:
        """Archive or restore the intent in Studio's own view. PRD §11.2 archives from ``Paused``: unpaused
        live cards refuse, a paused intent's waiting cards are set aside with it, and a decision that may
        already have reached AI-DLC refuses either way. Restoring is always allowed."""
        row = await self._ensure(repo_id, space, intent_dir)
        want = bool(archived)
        if want:
            await self._refuse(repo_id, space, intent_dir, operation="archive",
                               statuses=ARCHIVE_BLOCKING_STATUS)
            if not row.get("paused"):
                await self._refuse(repo_id, space, intent_dir, operation="archive",
                                   statuses=C.LIVE_ACTION_STATUS, live=True)
        state = ARCHIVE_ARCHIVED if want else ARCHIVE_ACTIVE
        updated = await self._cas(row, {"archive_state": state}, retry=True)
        await self._record("intent.archived" if want else "intent.restored", updated,
                           params={"archived": want})
        return BindingView.from_row(updated)

    async def record_stable_boundary(self, repo_id: str, space: str, intent_dir: str, boundary: Any) -> None:
        """Persist the last observed stable boundary unless it is unchanged: the reconciler recomputes it
        every tick, and each blind write would be an fsync."""
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
        key = self._key(repo_id, space, intent_dir)
        return await asyncio.to_thread(self._storage.get, _BINDINGS, key) or {}

    async def _ensure(self, repo_id: str, space: str, intent_dir: str) -> dict:
        space = _text(space) or C.DEFAULT_SPACE
        intent_dir = _text(intent_dir)
        key = self._key(repo_id, space, intent_dir)
        row = await asyncio.to_thread(self._storage.get, _BINDINGS, key)
        if row is not None:
            return row
        now = self._clock.iso()
        lost: sqlite3.IntegrityError | None = None
        try:
            await asyncio.to_thread(self._storage.insert, _BINDINGS, {
                "binding_key": key, "repo_id": repo_id, "space": space, "intent_dir": intent_dir,
                "binding_generation": 0, "keep_moving": 0, "archive_state": ARCHIVE_ACTIVE, "paused": 0,
                "created_at": now, "updated_at": now,
            })
        except sqlite3.IntegrityError as exc:
            # Two tabs opened the same intent: the loser reads the winner's row back. The same exception
            # covers the table's foreign key (an unregistered repository), which must not pass as a race.
            lost = exc
        row = await asyncio.to_thread(self._storage.get, _BINDINGS, key)
        if row is not None:
            return row
        if lost is not None:
            raise StudioError("repo_not_found",
                              "the intent binding references a repository that is not registered",
                              details={"repo_id": repo_id, "binding_key": key, "reason": str(lost)}) from lost
        raise StudioError("storage_error", "the intent binding could not be created",  # pragma: no cover
                          details={"binding_key": key})

    async def _cas(self, row: Mapping[str, Any], values: Mapping[str, Any], *, retry: bool = False) -> dict:
        """Generation-checked update, returning the row as written. ``retry`` re-applies an end state the
        caller asked for (paused, archived, …) after a lost race; never for which slot a decision goes to,
        which is not last-writer-wins: the user must see that another tab bound it."""
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
                self._storage.cas_update, _BINDINGS, key, int(fresh.get("binding_generation") or 0), values
            )
        updated = await asyncio.to_thread(self._storage.get, _BINDINGS, key)
        return updated or dict(row)

    async def _other_binding(self, repo_id: str, space: str, intent_dir: str, slot_key: str) -> dict | None:
        """Another intent already bound to this slot, if any."""
        rows = await asyncio.to_thread(self._storage.select, _BINDINGS, {"slot_key": slot_key})
        mine = (repo_id, _text(space) or C.DEFAULT_SPACE, _text(intent_dir))
        for row in rows or ():
            if (_text(row.get("repo_id")), _text(row.get("space")), _text(row.get("intent_dir"))) != mine:
                return row
        return None

    async def _refuse(self, repo_id: str, space: str, intent_dir: str, *, operation: str,
                      statuses: Sequence[str] = IN_FLIGHT_ACTION_STATUS, live: bool = False) -> None:
        """Refuse ``operation`` while this intent has actions in ``statuses``: a decision possibly in flight,
        or (``live``) live cards still in the queue."""
        rows = await asyncio.to_thread(self._storage.select, "actions", {
            "repo_id": repo_id, "space": _text(space) or C.DEFAULT_SPACE, "intent_dir": _text(intent_dir),
            "status": list(statuses),
        })
        if rows:
            reason, message = (("live_not_paused", "this intent still has live cards in the queue") if live
                               else ("in_flight", "a decision for this intent may still be in flight"))
            raise StudioError("action_not_submittable", message, details={
                "operation": operation, "reason": reason,
                "action_ids": [_text(r.get("action_id")) for r in rows],
                "statuses": sorted({_text(r.get("status")) for r in rows}),
            })

    async def _record(self, kind: str, row: Mapping[str, Any], *, params: Mapping[str, Any]) -> None:
        await self._activity.record(
            kind=kind, severity="info", repo_id=_text(row.get("repo_id")) or None,
            space=_text(row.get("space")) or None, intent_dir=_text(row.get("intent_dir")) or None,
            session_key=_text(row.get("canonical_session_key")) or None, params=dict(params),
        )
