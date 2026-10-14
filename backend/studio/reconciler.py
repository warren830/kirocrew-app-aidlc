"""Reconciliation — the loop that closes decisions Studio has already committed.

Every other module is allowed to refuse: a handler answers 409, the broker declines a transition, the
scheduler withholds a lease. This one cannot. Once the ``Delivering`` row is durable the decision may
already be in the model's mouth, so the only honest answers left are "it landed"
(``StateChanged``/``ResolvedNoTransition``), "we cannot tell" (``ReconciliationRequired``) and — with
four independent proofs, never on a client's word — "it never left" (``NotDelivered``). Nothing here
sends a prompt, stops a turn, replays a decision, writes an AI-DLC file or runs a protected engine verb:
the reconciler reads disk, reads the host, and moves Studio's own rows.

Four kinds of care run through the whole module.

**Ordering is evidence.** "New" audit rows are ``ts >= delivering_at`` *and* positioned after the
boundary event the card captured, because the engine writes ``GATE_APPROVED`` in the same second as
the human's turn (review P23); second-precision ``>`` would miss exactly the row that proves the
decision landed.

**Presence is required.** An in-flight human-lane resolution needs exactly one new ``HUMAN_TURN``
since the baseline the submit recorded. Zero or multiple turns escalate as ``human_presence_anomaly``
(FR-SES-010, C23), unless KiroCrew's transcript shows its own prompts (an automatic recovery, a sub-agent's
result) account for every extra turn and no person wrote beside this delivery. Once delivery is confirmed in ``ReconciliationRequired``, a complete audit with a
positive increment and an advanced human marker can close a matching resolution contract. The original
baseline stays intact; later turns must not make a completed decision permanently unresolvable.

**A restart is not an absence.** ``slot.messages`` is process memory, so a gateway restart between the
browser's send and its report empties the only host-side evidence there was. Every ``Delivering``-class
row carries the ``boot_id`` that committed it; when that differs, ``NotDelivered`` becomes unreachable
by construction (review R02).

**Nothing may starve the loop.** Repository walks run on Studio's own two-worker pool under a
semaphore with a per-tick wall budget — 64 repositories × a 10 s walk on the host's shared executor
would stall chat persistence and spawns (review R11/C32).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from . import constants as C
from . import sessions as _sessions
from .actions import ABSENCE_PROOF
from .aidlc_reader import audit_question_accepts_text, audit_question_answer, plan_approval_choice
from .consistency import RepoFacts
from .errors import IllegalTransition, StaleGeneration, StudioError
from .projection import run_unavailable_reason
from .repo_registry import RepoScan
from .wire import WireRecord

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .projection import StableBoundary
    from .repo_registry import RepoRecord
    from .leases import LeaseRecord

__all__ = [
    "FINGERPRINT_RULES",
    "PRESENCE_COMPARED_FIELDS",
    "REPO_SCAN_MIN_INTERVAL_SECS",
    "RECONCILABLE_STATUS",
    "STARTUP_STATUS",
    "Reconciler",
    "Resolution",
    "StartupReport",
    "presence_unchanged",
]

_LOG = logging.getLogger("aidlc_studio.reconciler")
_attr = C.attr


# --------------------------------------------------------------------------- #
# tuning constants
# --------------------------------------------------------------------------- #

#: A repository with no open action is not re-walked more often than this (§1.13 tick step 1). One with
#: an open action is always re-walked: its evidence is what a human is waiting on.
REPO_SCAN_MIN_INTERVAL_SECS: float = 2.0

#: Statuses ``reconcile_action`` has something to say about: ``Queued``/``NotDelivered`` are the human's to
#: move, and the terminal four are done.
RECONCILABLE_STATUS: tuple[str, ...] = ("Delivering", "Delivered", "ReconciliationRequired")

#: In flight when the previous process stopped, so re-read by ``startup()`` before any loop or scan runs
#: (§1.13): every later decision — a derived card, a delivery report — must see that re-reading first.
STARTUP_STATUS: tuple[str, ...] = ("Delivering", "Delivered")

#: The host was handed something and has not answered yet.
IN_FLIGHT_STATUS: tuple[str, ...] = STARTUP_STATUS

#: ``PresenceBaseline`` fields compared for "did anything on disk move?". Not ``taken_at``: it is the clock
#: of the read, and comparing it would make every comparison false and ``NotDelivered`` unreachable.
PRESENCE_COMPARED_FIELDS: tuple[str, ...] = (
    "human_turn_events",
    "human_turn_events_partial",
    "human_turn_mtime_ns",
    "turn_counter",
    "shard_sizes",
    "audit_tail_sha256",
    "state_sha256",
)

#: New ``ERROR_LOGGED`` rows inside the window that count as a failure rather than a conductor mistake: single
#: rows are routine (FORMAT-NOTES §6), so one must never fail an action.
_ERROR_BURST_COUNT = 3
_ERROR_BURST_WINDOW_SECS = 600

#: Ordered failure classification (§1.13.1): first match wins, so a throttled ACP stream is ``rate_limit``,
#: not ``transport``.
FINGERPRINT_RULES: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("rate_limit", re.compile(r"rate limit|429|throttl")),
    ("acp_timeout", re.compile(r"turn exceeded the .*ceiling|timeouterror|timed out")),
    ("transport", re.compile(r"stream closed|connection reset|econn")),
    ("guard", re.compile(r"refusing to")),
    ("validation", re.compile(r"unknown intent|ambiguous intent|invalid scope|cannot use")),
    ("permission", re.compile(r"eacces|permission denied")),
    ("dependency", re.compile(r"bun: command not found|cannot find module")),
)

#: What a fingerprint forgets so one failure fingerprints the same twice: colour, absolute paths, long hex
#: ids and any run of digits (pids, ports, byte counts, timestamps).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_PATH_RE = re.compile(r"(?:[a-z]:)?(?:/[^\s'\"]+)+")
_HEX_RE = re.compile(r"\b[0-9a-f]{8,}\b")
_NUM_RE = re.compile(r"\d+")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")
_MAX_NORMALISED_CHARS = 400
_MAX_SLUG_CHARS = 48

#: A contract row that only waits for the turn to end: ``(outcome, reason)`` by whether it has.
_TURN_OUTCOME = {True: ("no_transition", "turn_ended"), False: ("pending", "turn_running")}

#: Pending reasons that mean "a human must look", not "wait longer". They double as the finding codes: why a
#: resolution was refused is exactly what the recovery card has to say.
_ANOMALY_REASONS = ("human_presence_anomaly", "cursor_moved")
#: Transcript roles a prompt into the conversation can carry: a person's message (sent, or waiting behind a turn),
#: and the prompts KiroCrew writes itself, which AI-DLC's hook records as ``HUMAN_TURN`` all the same.
_PROMPT_ROLES = ("user", "queued", "inject", "subagent")
_PERSON_ROLES = ("user", "queued")
#: How far back the transcript is read for one delivery's turn.
_PRESENCE_ROWS = 200


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Resolution(WireRecord):
    """The reconciler's reading of one dispatched decision against the disk. ``outcome`` is four-valued on
    purpose: "pending" is the normal answer on most ticks, and folding it into a terminal one is how a
    decision would be declared landed before the engine committed it."""

    outcome: str  # "state_changed" | "no_transition" | "pending" | "failed"
    reason: str
    evidence: dict[str, Any]

    @property
    def resolved(self) -> bool:
        return self.outcome in ("state_changed", "no_transition")


@dataclass(frozen=True, slots=True)
class StartupReport(WireRecord):
    """What the ordered startup pass found, published as ``health.updated``. ``within_budget=False`` is not an
    error: the remaining work leads the first tick, and a caller that could not tell would either block the
    gateway or silently skip repositories."""

    boot_id: str
    started_at: str
    finished_at: str
    took_ms: int
    integrity: tuple[str, ...]
    payload_ok: bool | None
    payload_mismatches: int
    #: Transactions a previous process abandoned, closed by this startup: a gateway died mid-install, which
    #: belongs in a report a person reads rather than only in the log.
    transactions_settled: tuple[str, ...]
    actions_reevaluated: int
    boot_id_mismatches: int
    repos_scanned: int
    repos_pending: int
    within_budget: bool
    issues: tuple[str, ...]


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #


def _busy_reasons(view: Any) -> tuple[str, ...]:
    """Every reason a slot cannot take a dispatch; ``()`` means idle.

    Prefers the view's own property (``SlotView.busy_reasons``) and falls back to the module predicate
    ``sessions.busy_reasons`` so a host release that adds a busy signal is honoured in one place.
    """
    if view is None:
        return ()
    own = getattr(view, "busy_reasons", None)
    if isinstance(own, (tuple, list)):
        return tuple(str(r) for r in own)
    try:
        return tuple(_sessions.busy_reasons(view))
    except AttributeError:
        # An unanswerable predicate is treated as busy everywhere else in Studio; the same reading here
        # keeps a reconciler from calling a slot idle because one field was missing.
        return ("running",)


def _json_of(value: Any) -> Any:
    """A JSON-safe copy of a record, whatever shape it arrived in."""
    if value is None:
        return None
    to_json = getattr(value, "to_json", None)
    if callable(to_json):
        return to_json()
    if isinstance(value, Mapping):
        return {str(k): _json_of(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_of(v) for v in value]
    return value


def presence_unchanged(baseline: Any, fresh: Any) -> bool:
    """Is every recorded presence fact identical between the submit-time baseline and a fresh read? The disk
    half of the ``NotDelivered`` proof (C24); ``False`` when either side is missing, or the at-most-once
    guarantee would belong to whichever caller forgot to record one."""
    if baseline is None or fresh is None:
        return False
    for name in PRESENCE_COMPARED_FIELDS:
        left, right = _attr(baseline, name), _attr(fresh, name)
        if isinstance(left, Mapping) or isinstance(right, Mapping):
            left, right = dict(left or {}), dict(right or {})
        if left != right:
            return False
    return True


def _storable(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Evidence without the parsed ``AuditEvent`` objects, which stay in this module: the store takes JSON."""
    return {key: value for key, value in evidence.items() if key != "audit_error_events"}


def _normalise_message(text: str) -> str:
    """A failure message reduced to the part that identifies the failure."""
    out = _ANSI_RE.sub("", str(text or "")).strip().lower()
    out = _NUM_RE.sub("<n>", _HEX_RE.sub("<id>", _PATH_RE.sub("<path>", out)))
    return re.sub(r"\s+", " ", out).strip()[:_MAX_NORMALISED_CHARS]


def _slug_of(normalised: str) -> str:
    return _SLUG_RE.sub("-", normalised).strip("-")[:_MAX_SLUG_CHARS].strip("-") or "unknown"


def _error_text(event: Any) -> str:
    """The meaningful half of an ``ERROR_LOGGED`` row. Field names vary by version (FORMAT-NOTES §6); the
    event name is the last resort, since a fingerprint of "" would merge every unlabelled failure."""
    fields = _attr(event, "fields", {}) or {}
    for key in ("Error", "Message", "Details"):
        value = str(fields.get(key) or "").strip()
        if value:
            return value
    return str(_attr(event, "event", "") or "")


def _event_order(event: Any) -> tuple[int, int]:
    return int(_attr(event, "shard_index", 0) or 0), int(_attr(event, "pos", 0) or 0)


def _unit_scoped(event: Any, unit: Any, snap: Any) -> bool:
    """Is this main-workflow row about the action's unit? Only team gates write ``Unit`` on gate rows, so a
    blank Unit belongs to the unit running now, which must still be the action's (the reader's question
    floors read it the same way). A recorded Unit must match exactly."""
    fields = _attr(event, "fields", {}) or {}
    if fields.get("Workflow"):
        return False
    recorded, expected = str(fields.get("Unit") or "").strip() or None, unit or None
    if recorded is not None:
        return recorded == expected
    return expected is None or _attr(snap, "unit") == expected


def _iso_gt(left: Any, right: Any) -> bool:
    """``left > right`` for two second-precision UTC stamps; ``False`` when either is absent."""
    return bool(left) and bool(right) and str(left) > str(right)


def _iso_ge(left: Any, right: Any) -> bool:
    return bool(left) and bool(right) and str(left) >= str(right)


def _carry_recorded_at(previous: Any, boundary: Any) -> Any:
    """``boundary`` with ``previous``'s ``recorded_at`` when the two describe the same boundary.

    Returns ``boundary`` untouched when there is no previous one, when the identity differs, or when
    either side is not a ``StableBoundary`` (the binding may hold a plain dict decoded from storage — in
    that case the dict's own ``recorded_at`` is adopted only if the rest matches).
    """
    if previous is None or boundary is None:
        return boundary
    identity = getattr(boundary, "identity", None)
    if not callable(identity):
        return boundary
    prior_identity = getattr(previous, "identity", None)
    if callable(prior_identity):
        if prior_identity() != identity():
            return boundary
        stamp = getattr(previous, "recorded_at", "")
    elif isinstance(previous, Mapping):
        as_json = dict(boundary.to_json())
        stamp = str(previous.get("recorded_at") or "")
        if {k: v for k, v in previous.items() if k != "recorded_at"} != {
            k: v for k, v in as_json.items() if k != "recorded_at"
        }:
            return boundary
    else:
        return boundary
    return replace(boundary, recorded_at=stamp) if stamp else boundary


def _elapsed(since: Any, now: Any) -> float | None:
    a, b = C.epoch_from_iso(since), C.epoch_from_iso(now)
    return None if a is None or b is None else b - a


# --------------------------------------------------------------------------- #
# the reconciler
# --------------------------------------------------------------------------- #


class Reconciler:
    """Startup reconciliation plus two background loops (§1.13).

    Holds ``Services`` rather than its parts because it is the one component that spans every layer —
    disk, host, store, broker, scheduler — and a constructor listing twenty collaborators would go stale
    with each of them. Everything it touches is read through ``services`` at call time, so a lazily
    attached ``HostBridge`` (C08) is picked up without re-wiring.
    """

    def __init__(self, services: Any) -> None:
        self._s = services
        self._sem_obj: asyncio.Semaphore | None = None
        self._sem_loop: asyncio.AbstractEventLoop | None = None
        #: Round-robin cursor over repositories without open actions, kept across ticks so a per-tick
        #: budget cannot starve the tail of a 64-repository registry.
        self._cursor = 0
        #: ``repo_id -> monotonic`` of the last completed scan (the 2 s skip of tick step 1).
        self._scanned_at: dict[str, float] = {}
        #: ``binding_key -> iso`` first time the intent's state file was unreadable. Only this component
        #: has seen the previous ticks, which is why ``RepoFacts`` takes the value from here (review P26.2).
        self._first_unreadable: dict[str, str] = {}
        #: ``binding_key -> IntentSummary JSON`` of the last published summary, so ``intent.updated`` is
        #: published on change instead of every five seconds for every intent.
        self._published: dict[str, Any] = {}
        #: ``action_id -> True`` once the bound slot was observed busy after ``delivering_at``. The host
        #: keeps no history of "was it running": if this process does not remember, nobody can say.
        self._ran: dict[str, bool] = {}
        #: ``(repo_id, space, intent_dir) -> monotonic`` first time a scan found live cards for an intent
        #: whose directory was not on disk. Only this component sees consecutive scans, so only it can
        #: tell a branch switch in progress from an intent that is gone (§1.13 step 1b).
        self._missing_since: dict[tuple[str, str, str], float] = {}
        #: ``repo_id -> intent directories the last complete scan found``: ``GET /health`` ``counts.intents``.
        #: Binding rows outlive a deleted intent on purpose (its archive flag and session), so they cannot count.
        self._intents_on_disk: dict[str, int] = {}
        #: Binding keys whose state file this process read as not completed: only their completion is announced.
        self._unfinished: set[str] = set()
        #: Gate and question cards created before this instant were minted by an earlier process (or by v1, which
        #: never announced them); they are already on the user's queue and are not announced again.
        self._announce_since: str = services.clock.iso()
        self._running = False
        self._last_tick_at: str | None = None
        self._last_tick_ms: int | None = None
        self._repos_scanned = 0

    # ---- introspection ---------------------------------------------------- #

    def status(self) -> dict[str, Any]:
        """The ``reconciler`` block of ``GET /health`` (§2.1): four numbers no other component observes."""
        return {"running": self._running, "last_tick_at": self._last_tick_at, "last_tick_ms": self._last_tick_ms,
                "repos_scanned": self._repos_scanned}

    def intents_on_disk(self) -> int:
        """``GET /health`` ``counts.intents``: zero before the first scan, a lower bound while a listing is
        truncated, unchanged while a repository is unavailable (nothing can be said about it until it is back)."""
        return sum(self._intents_on_disk.values())

    # ---- startup ---------------------------------------------------------- #

    async def startup(self) -> StartupReport:
        """Ordered startup: integrity, payload, in-flight actions, abandoned transactions, first full scan.

        Each step is a precondition of the next: every in-flight action is re-read before the first scan, so no
        card is minted for a boundary a delivered decision already closed. Bounded by
        ``HOST_STARTUP_HOOK_BUDGET_SECS`` (the gateway detaches an app whose ``on_startup`` overruns): leftover work
        leads the first tick.
        """
        clock = self._s.clock
        started_at, started = clock.iso(), clock.monotonic()
        deadline = started + C.HOST_STARTUP_HOOK_BUDGET_SECS
        issues: list[str] = []
        integrity = tuple(await self._to_thread(self._s.storage.integrity_check))
        if integrity:
            issues.append("storage_integrity")
        payload_ok, mismatches = await self._verify_payload(issues)
        reevaluated, boot_mismatches = await self._startup_actions(deadline, issues)
        # A transaction abandoned by process death is settled and its repository marked `recovery_required`, so a
        # person is told the tree may be half written.
        settled: tuple[str, ...] = ()
        installer = getattr(self._s, "installer", None)
        if installer is not None:
            try:
                settled = tuple(await installer.settle_abandoned())
            except StudioError as exc:
                issues.append(f"settle_abandoned:{exc.code}")
        if settled:
            issues.append(f"transactions_settled:{len(settled)}")
        scanned, pending = await self._startup_scan(deadline, issues)
        report = StartupReport(
            boot_id=str(getattr(self._s, "boot_id", "") or ""), started_at=started_at, finished_at=clock.iso(),
            took_ms=max(0, int((clock.monotonic() - started) * 1000)), integrity=integrity, payload_ok=payload_ok,
            payload_mismatches=mismatches, transactions_settled=settled, actions_reevaluated=reevaluated,
            boot_id_mismatches=boot_mismatches, repos_scanned=scanned, repos_pending=pending,
            within_budget=clock.monotonic() <= deadline, issues=tuple(issues),
        )
        await self._record("health.startup", severity="info", params=report.to_json())
        await self._publish("health.updated", {"startup": report.to_json()})
        return report

    async def _verify_payload(self, issues: list[str]) -> tuple[bool | None, int]:
        """Verify the vendored payload. Unverifiable is *degraded*, not broken: a missing installer costs this one
        number, never the startup every other module's recovery depends on."""
        verify = getattr(getattr(self._s, "installer", None), "verify_payload", None)
        if not callable(verify):
            issues.append("payload_unverified")
            return None, 0
        try:
            status = await self._to_thread(verify)
        except StudioError as exc:
            issues.append(f"payload:{exc.code}")
            return False, 0
        ok = bool(_attr(status, "ok", False))
        if not ok:
            issues.append("payload_degraded")
        return ok, len(tuple(_attr(status, "mismatches", ()) or ())) + len(tuple(_attr(status, "missing", ()) or ()))

    async def _startup_actions(self, deadline: float, issues: list[str]) -> tuple[int, int]:
        """Re-read every row that was mid-flight when the previous process stopped.

        The ``boot_id`` stamp is written before the row is reconciled: the four-proof ``NotDelivered`` path reads
        ``evidence.boot_id_unchanged``, so a row reconciled first could be declared undelivered on evidence this
        process cannot have (review R02).
        """
        rows = await self._to_thread(
            self._s.storage.select, "actions", {"status": list(STARTUP_STATUS)}, order_by="created_at ASC"
        )
        boot_id = str(getattr(self._s, "boot_id", "") or "")
        reevaluated = 0
        mismatches = 0
        for row in rows:
            action_id = str(row.get("action_id"))
            if row.get("status") in IN_FLIGHT_STATUS and str(row.get("boot_id") or "") != boot_id:
                mismatches += 1
                try:
                    await self._stamp_evidence_row(row, {"boot_id_unchanged": False})
                except StaleGeneration:
                    pass
            try:
                rec = await self._s.actions.get(action_id)
            except StudioError:
                continue
            try:
                await self.reconcile_action(rec)
                reevaluated += 1
            except (StudioError, OSError) as exc:
                _LOG.warning("startup reconcile of %s failed: %s", action_id, exc)
            if self._s.clock.monotonic() >= deadline:
                issues.append("actions_reevaluation_deferred")
                break
        return reevaluated, mismatches

    async def _startup_scan(self, deadline: float, issues: list[str]) -> tuple[int, int]:
        repos = await self._to_thread(self._s.repos.list)
        scanned = 0
        for repo in repos:
            if self._s.clock.monotonic() >= deadline:
                issues.append("first_scan_deferred")
                return scanned, len(repos) - scanned
            with contextlib.suppress(StudioError, OSError, asyncio.TimeoutError):
                await self._scan_bounded(repo)
                scanned += 1
        return scanned, 0

    # ---- loops ------------------------------------------------------------ #

    async def run_forever(self) -> None:
        """Tick every ``RECONCILE_INTERVAL_SECS`` until cancelled; a failed tick is logged and the next one runs."""
        self._running = True
        try:
            while True:
                try:
                    await self.tick()
                except Exception:  # pragma: no cover - defensive
                    _LOG.exception("reconciler tick failed")
                await asyncio.sleep(C.RECONCILE_INTERVAL_SECS)
        finally:
            self._running = False

    async def tick(self) -> None:
        """One pass over the whole registry (§1.13 steps 1-8). Scans come first, so the actions reconciled in step 2
        are judged against evidence read in this tick."""
        clock = self._s.clock
        started = clock.monotonic()
        self._last_tick_at = clock.iso()

        await self._scan_pass(started)
        await self._reconcile_open_actions()
        # The advisor pass sits exactly here: after steps 1-5, so the ``Queued`` set it drafts for is the one this
        # tick derived (a card a scan just retired must never draw a spawn); before retention, so a draft it settles
        # past its TTL is expired this tick rather than sitting ``ready`` five more seconds.
        await self._advisor_pass()
        await self._retention_pass()

        self._last_tick_ms = max(0, int((clock.monotonic() - started) * 1000))

    # ---- tick step 1: scan scheduling ------------------------------------- #

    async def _scan_pass(self, tick_started: float) -> None:
        """Repositories with open actions first, then the rest round-robin from where the last tick's budget ran
        out, so the tail of a large registry is never starved. A repository scanned under 2 s ago is skipped."""
        clock = self._s.clock
        repos = await self._to_thread(self._s.repos.list)
        known = {r.repo_id for r in repos}
        # A repository that left the registry takes its memory with it, or it would count toward
        # ``counts.intents`` forever.
        for repo_id in [key for key in self._intents_on_disk if key not in known]:
            self._intents_on_disk.pop(repo_id, None)
        for key in [key for key in self._missing_since if key[0] not in known]:
            self._missing_since.pop(key, None)
        if not repos:
            return
        rows = await self._to_thread(self._s.storage.select, "actions", {"status": list(C.LIVE_ACTION_STATUS)})
        open_ids = {str(row.get("repo_id")) for row in rows if row.get("repo_id")}
        priority = [r for r in repos if r.repo_id in open_ids]
        rest = [r for r in repos if r.repo_id not in open_ids]
        if rest:
            cursor = self._cursor % len(rest)
            rest = rest[cursor:] + rest[:cursor]
        inflight: set[asyncio.Task] = set()
        scanned = 0
        stopped_at: int | None = None
        for index, repo in enumerate(priority + rest):
            if clock.monotonic() - tick_started >= C.SCAN_TICK_BUDGET_SECS:
                stopped_at = index - len(priority)
                break
            last = self._scanned_at.get(repo.repo_id, float("-inf"))
            if repo.repo_id not in open_ids and clock.monotonic() - last < REPO_SCAN_MIN_INTERVAL_SECS:
                continue
            inflight.add(asyncio.create_task(self._scan_bounded(repo)))
            scanned += 1
            if len(inflight) >= C.SCAN_CONCURRENCY:
                _, inflight = await asyncio.wait(inflight, return_when=asyncio.FIRST_COMPLETED)
        if inflight:
            await asyncio.wait(inflight)
        if rest:
            advanced = len(rest) if stopped_at is None else max(0, stopped_at)
            self._cursor = (self._cursor + advanced) % len(rest)
        self._repos_scanned = scanned

    async def _scan_bounded(self, repo: "RepoRecord") -> RepoScan | None:
        """``scan_repo`` under the per-repo deadline, so a slow repository never holds up the tick.

        The executor future is not cancellable: on a timeout the worker finishes into a result nobody reads,
        which costs one pool slot for a moment and cannot corrupt anything (every scan writes through one store).
        """
        scan = None
        try:
            scan = await asyncio.wait_for(self.scan_repo(repo), timeout=C.REPO_SCAN_DEADLINE_SECS)
        except asyncio.TimeoutError:
            _LOG.warning("repo scan of %s exceeded %.1fs", repo.repo_id, C.REPO_SCAN_DEADLINE_SECS)
        except (StudioError, OSError) as exc:
            _LOG.warning("repo scan of %s failed: %s", repo.repo_id, exc)
        self._scanned_at[repo.repo_id] = self._s.clock.monotonic()
        return scan

    # ---- scanning --------------------------------------------------------- #

    async def scan_repo(self, repo: "RepoRecord") -> RepoScan:
        """One repository: availability and install health, then every intent's read model.

        The semaphore is held for the whole body, intent loop included: the pinned invariant is "at most
        ``SCAN_CONCURRENCY`` concurrent ``scan_repo`` calls", and a gate around single reads would let 64 walks
        interleave.
        """
        async with self._semaphore():
            return await self._scan_repo_locked(repo)

    async def _scan_repo_locked(self, repo: "RepoRecord") -> RepoScan:
        clock = self._s.clock
        started = clock.monotonic()
        base: RepoScan = await self._in_pool(self._s.repos.rescan, repo.repo_id)
        repo = await self._to_thread(self._s.repos.get, repo.repo_id)
        summaries: list[Any] = []
        findings: list[Any] = []
        truncated = base.truncated
        # Every directory the listing found, scanned or not: an intent whose state file is mid-write is still on
        # disk, and its cards must not be swept.
        present: set[tuple[str, str]] = set()
        if base.availability == "available":
            leases = await self._s.scheduler.list()
            root = Path(repo.canonical_path)
            for space in await self._in_pool(self._s.reader.list_spaces, root):
                dirs = await self._in_pool(self._s.reader.list_intent_dirs, root, space)
                truncated = truncated or len(dirs) >= C.MAX_INTENTS_PER_REPO
                present.update((space, intent_dir) for intent_dir in dirs)
                for intent_dir in dirs:
                    try:
                        summary, intent_findings = await self._scan_intent(
                            repo, space, intent_dir, install=base.install, leases=leases
                        )
                    except StudioError as exc:
                        _LOG.debug("intent scan %s/%s skipped: %s", space, intent_dir, exc.code)
                        continue
                    summaries.append(summary)
                    findings.extend(intent_findings)
            # Recorded even when truncated: the health number is a lower bound then too.
            self._intents_on_disk[repo.repo_id] = len(present)
        if base.availability == "available" and not truncated:
            await self._retire_vanished(repo.repo_id, present)
        else:
            # A truncated listing proves no absence, and an unavailable repository says nothing about its intents
            # (every directory "vanished" at once): forget the timers, so a remount retires nothing on stale evidence.
            self._forget_missing(repo.repo_id)
        return RepoScan(
            repo_id=repo.repo_id, availability=base.availability, install=base.install, intents=tuple(summaries),
            findings=tuple(findings), took_ms=max(0, int((clock.monotonic() - started) * 1000)), truncated=truncated,
            scanned_at=clock.iso(),
        )

    async def _retire_vanished(self, repo_id: str, present: set[tuple[str, str]]) -> None:
        """Step 1b: retire the cards of intents whose directory left the disk (a hand deletion, a branch switch,
        a renamed space), which ``retire_stale`` per *found* intent never reaches.

        Absence must last ``INTENT_MISSING_GRACE_SECS`` before it is believed: a listing can be empty for a tick
        while a checkout is mid-flight, and a card cancelled on that evidence is a gate the human was about to
        answer. Command cards are left alone by ``retire_stale`` itself; ``Failed`` ones are marked (P07).
        """
        live = await self._s.actions.list_live(repo_id=repo_id, include_revision=True)
        gone: set[tuple[str, str]] = set()
        for rec in live:
            space = str(_attr(rec, "space") or "")
            intent_dir = str(_attr(rec, "intent_dir") or "")
            # A `Failed` card already marked superseded stays visible until its own condition clears it (P07);
            # it is no reason to keep sweeping the intent every tick.
            if str(_attr(rec, "status") or "") == "Failed" and (_attr(rec, "evidence") or {}).get("superseded"):
                continue
            if intent_dir and (space, intent_dir) not in present:
                gone.add((space, intent_dir))
        # An intent that is back, or whose cards are all settled, is no longer missing.
        for key in [key for key in self._missing_since if key[0] == repo_id]:
            if (key[1], key[2]) not in gone:
                self._missing_since.pop(key, None)
        now = self._s.clock.monotonic()
        for space, intent_dir in sorted(gone):
            if now - self._missing_since.setdefault((repo_id, space, intent_dir), now) < C.INTENT_MISSING_GRACE_SECS:
                continue
            await self._s.actions.retire_stale(repo_id, space, intent_dir, set(), reason="intent_missing")
            _LOG.info("intent %s/%s left the disk; its cards were retired", space, intent_dir)

    def _forget_missing(self, repo_id: str) -> None:
        for key in [key for key in self._missing_since if key[0] == repo_id]:
            self._missing_since.pop(key, None)

    async def _scan_intent(
        self,
        repo: "RepoRecord",
        space: str,
        intent_dir: str,
        *,
        install: Any,
        leases: Sequence["LeaseRecord"],
    ) -> tuple[Any, list[Any]]:
        """Snapshot → findings → cards → summary for one intent (§1.13 ``scan_repo``)."""
        key = f"{repo.repo_id}:{space}:{intent_dir}"
        remembered = self._first_unreadable.get(key)
        # Only requests that existed before this snapshot was taken are retired: a Run created after the workflow
        # advanced must not be judged against an earlier checkpoint.
        live = await self._s.actions.list_live(
            repo_id=repo.repo_id, space=space, intent_dir=intent_dir, include_revision=True
        )
        snap = await self._in_pool(
            functools.partial(self._s.projection.snapshot, repo, space, intent_dir, first_unreadable_at=remembered)
        )
        first_unreadable = self._track_unreadable(key, snap, remembered)

        binding = await self._s.sessions.get(repo.repo_id, space, intent_dir)
        slot = self._s.host.slot(binding.slot_key) if binding.slot_key else None
        if slot is not None and slot.approval is not None and not binding.archived:
            # A run parked on a host tool approval waits silently in the chat; say so once per approval.
            # Not for an archived intent: the user set it aside, and the queue does not list it either.
            await self._notify(
                "tool_approval", repo_id=repo.repo_id, repo_label=repo.label, intent_key=snap.intent_key,
                intent_label=_attr(snap.row, "slug") or intent_dir, slot_key=binding.slot_key,
                approval=slot.approval, turn=str(_attr(slot, "last_turn_ts") or ""),
            )
        for command in live:
            await self._retire_obsolete_run(command, snap)
        live = await self._s.actions.list_live(
            repo_id=repo.repo_id, space=space, intent_dir=intent_dir, include_revision=True
        )
        breakers = await self._breakers_for(repo.repo_id, snap.intent_key)
        facts = RepoFacts(
            now=self._s.clock.iso(), repo=repo, install=install, binding=binding, slot=slot, live_actions=live,
            leases=leases, first_unreadable_at=first_unreadable, layout=snap.layout,
        )
        findings = await self._in_pool(self._s.consistency.evaluate_intent, snap, facts)
        seeds = await self._in_pool(functools.partial(
            self._s.projection.derive_cards, snap, findings=findings, binding=binding, install=install,
            breakers=breakers, live_actions=live, slot=slot, host_card=self._host_card(binding.slot_key),
        ))
        await self._s.actions.upsert_derived(seeds)
        keys = {seed.dedupe_key for seed in seeds if seed.dedupe_key}
        await self._s.actions.retire_stale(repo.repo_id, space, intent_dir, keys)
        # Re-read rather than use upsert's answer, which is empty when nothing was derived: a queued Run or an
        # in-flight decision still belongs in this intent's summary and boundary.
        live = await self._s.actions.list_live(
            repo_id=repo.repo_id, space=space, intent_dir=intent_dir, include_revision=True
        )
        if not binding.archived:  # an archived intent's cards are set aside with it
            for rec in live:
                # A gate or question this scan leaves open is announced once; the notification ledger remembers.
                # Only cards this process minted: an upgrade or restart must not replay every pending decision.
                if (_attr(rec, "type") in ("gate", "question") and _attr(rec, "status") == "Queued"
                        and _attr(rec, "dedupe_key") in keys
                        and str(_attr(rec, "created_at") or "") >= self._announce_since):
                    await self._notify("action", rec, snap=snap)

        boundary = await self._record_boundary(repo, snap, binding, live, slot, leases)
        binding = await self._s.sessions.get(repo.repo_id, space, intent_dir)
        summary = await self._in_pool(functools.partial(
            self._s.projection.summarize, snap, binding=binding,
            live_actions=[a for a in live if _attr(a, "type") != "revision"],
            breaker_open=any(_attr(b, "opened_at") for b in breakers),
            host=await self._s.sessions.session_ref(binding), findings=findings, slot=slot,
        ))
        await self._publish_intent(key, summary, boundary)
        return summary, list(findings)

    def _track_unreadable(self, key: str, snap: Any, remembered: str | None) -> str | None:
        """Remember when the state file first went unreadable, and forget it once it reads again: ``state_unreadable``
        is ``info`` inside its grace window and ``blocking`` after it, and only this component saw the earlier
        ticks (review P26.2)."""
        if _attr(snap, "state") is None:
            since = remembered or _attr(snap, "taken_at") or self._s.clock.iso()
            self._first_unreadable[key] = since
            return since
        self._first_unreadable.pop(key, None)
        return None

    def _host_card(self, slot_key: str | None) -> dict | None:
        cards = self._s.host.pending_question_cards(str(slot_key)) if slot_key else None
        return cards[0] if cards else None

    async def _breakers_for(self, repo_id: str, intent_key: str) -> list[dict]:
        prefix = f"{repo_id}:{intent_key}:"
        rows = await self._to_thread(self._s.storage.select, "breakers")
        return [row for row in rows if str(row.get("key") or "").startswith(prefix)]

    # ---- tick step 4: stable boundary ------------------------------------- #

    async def _record_boundary(
        self,
        repo: "RepoRecord",
        snap: Any,
        binding: Any,
        live: Sequence[Any],
        slot: Any,
        leases: Sequence["LeaseRecord"],
    ) -> "StableBoundary":
        """Evaluate PRD §12.2 on the snapshot this scan already took (a second read would answer about another
        moment, which a "stable" boundary must not), and persist it when it changed."""
        boundary = self._s.projection.stable_boundary(
            snap, live_actions=live, busy_reasons=_busy_reasons(slot),
            lease_active=self._repo_in_flight(repo, leases),
        )
        # An unchanged boundary keeps the time it was FIRST seen ("stable since"). Stamped with the time of each
        # look, the two dedupes downstream never matched: an fsync'd `intent_bindings` write and an
        # `intent.updated` frame per intent per tick, on an app where nothing had happened.
        previous = _attr(binding, "last_stable_boundary")
        boundary = _carry_recorded_at(previous, boundary)
        if _json_of(previous) != _json_of(boundary):
            await self._s.sessions.record_stable_boundary(repo.repo_id, snap.space, snap.intent_dir, boundary)
        return boundary

    def _repo_in_flight(self, repo: "RepoRecord", leases: Sequence["LeaseRecord"]) -> bool:
        """Does this repository have a turn in flight? Its execution lease is exactly that."""
        identity = repo.resolved_identity
        return any(
            _attr(lease, "kind") == "execution"
            and (_attr(lease, "repo_id") == repo.repo_id
                 or bool(identity) and _attr(lease, "resolved_repo_identity") == identity)
            for lease in leases
        )

    async def _publish_intent(self, key: str, summary: Any, boundary: Any) -> None:
        """``intent.updated`` when the summary changed, and a completion this process watched happen.

        A completion is news only for an intent whose state file this process read as unfinished: after a
        restart, an un-archive or a briefly unreadable state file, ``Completed`` can be a finish long past.
        """
        payload = _json_of(summary)
        if self._published.get(key) == payload:
            return
        self._published[key] = payload
        await self._publish("intent.updated", {
            "repo_id": _attr(summary, "repo_id"), "space": _attr(summary, "space"),
            "intent_dir": _attr(summary, "intent_dir"), "intent_key": _attr(summary, "intent_key"),
            "operational_state": _attr(summary, "operational_state"), "open_actions": _attr(summary, "open_actions"),
            "stable_boundary": _json_of(boundary),
        })
        status = str(_attr(_attr(summary, "disk"), "status") or "").strip().lower()
        if status and status != "completed":
            self._unfinished.add(key)
        elif _attr(summary, "operational_state") == "Completed" and key in self._unfinished:
            self._unfinished.discard(key)
            await self._notify("completion", summary)

    # ---- tick step 2/3: open actions -------------------------------------- #

    async def _reconcile_open_actions(self) -> int:
        """Reconcile every action the host might still be working on, one snapshot per intent."""
        rows = await self._to_thread(
            self._s.storage.select, "actions", {"status": list(RECONCILABLE_STATUS)}, order_by="created_at ASC"
        )
        done = 0
        snapshots: dict[tuple[str, str, str], Any] = {}
        for row in rows:
            try:
                rec = await self._s.actions.get(str(row.get("action_id")))
            except StudioError:
                continue
            key = (rec.repo_id, rec.space, rec.intent_dir)
            if key not in snapshots:
                snapshots[key] = await self._snapshot_for(rec)
            try:
                await self.reconcile_action(rec, snapshots[key])
                done += 1
            except (StudioError, OSError) as exc:
                _LOG.warning("reconcile of %s failed: %s", rec.action_id, exc)
        return done

    async def _snapshot_for(self, rec: Any) -> Any | None:
        try:
            repo = await self._to_thread(self._s.repos.get, rec.repo_id)
            return await self._in_pool(functools.partial(
                self._s.projection.snapshot, repo, rec.space, rec.intent_dir,
                first_unreadable_at=self._first_unreadable.get(f"{rec.repo_id}:{rec.space}:{rec.intent_dir}"),
            ))
        except (StudioError, OSError):
            return None

    async def reconcile_now(self, action_id: str) -> Any:
        """``POST /actions/{id}/reconcile``: one action, now, on the caller's request."""
        rec = await self._s.actions.get(action_id)
        return await self.reconcile_action(rec, await self._snapshot_for(rec))

    async def reconcile_action(self, rec: Any, snap: Any | None = None) -> Any:
        """Move one action as far as today's evidence allows, and no further. A never-sent Run/Resume may
        retire when its purpose expires; no terminal or undelivered record is reopened."""
        if rec.status == "Queued" and rec.type in ("run", "resume"):
            return await self._retire_obsolete_run(rec, snap if snap is not None else await self._snapshot_for(rec))
        if rec.status not in RECONCILABLE_STATUS:
            return rec
        if snap is None and rec.status != "Delivering":
            snap = await self._snapshot_for(rec)
        slot = self._slot_for(rec)
        self._observe_run(rec, slot)
        if rec.status == "Delivered":
            return await self._reconcile_delivered(rec, snap, slot)
        if rec.status == "ReconciliationRequired":
            return await self._reconcile_required(rec, snap, slot)
        # Delivering, and the browser never reported. Never `NotDelivered` from here: with no report and no
        # 4xx, the uncertainty is the only certain thing (§1.13).
        if not self._past_deadline(rec):
            return rec
        snap = snap if snap is not None else await self._snapshot_for(rec)
        return await self._to_reconciliation(rec, snap, slot, reason="ack_deadline")

    async def _reconcile_delivered(self, rec: Any, snap: Any, slot: Any) -> Any:
        """Wait for a queued message's turn to start (P22), then look for the resolution it declared."""
        timeout = self._turn_timeout()
        if not rec.evidence.get("turn_started_at"):
            now = self._s.clock.iso()
            if self._turn_started(rec, slot, snap):
                # The turn timeout counts from the turn's own start, never from the host's ack.
                return await self._stamp(rec, {"turn_started_at": now}, deadline_at=self._deadline_from(now, timeout))
            waited = _elapsed(rec.delivered_at, now)
            if waited is not None and waited > C.DELIVERED_QUEUED_WAIT_FACTOR * timeout:
                return await self._to_reconciliation(rec, snap, slot, reason="queued_wait_exceeded")
            return rec
        if snap is not None:
            resolution = self.evaluate_resolution(rec, snap, slot)
            if resolution.resolved:
                return await self._settle(rec, snap, resolution)
            if resolution.reason in _ANOMALY_REASONS:
                return await self._to_reconciliation(rec, snap, slot, reason=resolution.reason,
                                                     extra=resolution.evidence)
            # Tool discovery and recoverable retries can log several errors in one turn: keep the lease while
            # the agent works, and give its final answer first refusal.
            failure = self._classified_failure(rec, snap) if self._turn_ended(rec, slot, snap) else None
            if failure is not None:
                return await self._fail(rec, snap, failure)
        if self._session_replaced(rec, slot):
            return await self._to_reconciliation(rec, snap, slot, reason="session_replaced")
        if self._past_deadline(rec):
            return await self._to_reconciliation(rec, snap, slot, reason="turn_deadline")
        return rec

    async def _retire_obsolete_run(self, rec: Any, snap: Any | None) -> Any:
        """A never-sent Run may expire; an in-flight or uncertain delivery never does."""
        if (snap is None or rec.type not in ("run", "resume") or rec.status != "Queued" or rec.delivery_id
                or rec.delivering_at):
            return rec
        reason = run_unavailable_reason(snap, stage=rec.stage, unit=rec.unit)
        if reason is None:
            return rec
        try:
            return await self._s.actions.transition(
                rec.action_id, "Cancelled", expected_generation=rec.status_generation, reason="command_superseded",
                evidence={"superseded": True, "command_unavailable_reason": reason, "current_stage": snap.stage},
            )
        except StaleGeneration:
            # A concurrent submit owns the new generation: never adopt it to cancel its delivery.
            return await self._s.actions.get(rec.action_id)

    async def _reconcile_required(self, rec: Any, snap: Any, slot: Any) -> Any:
        """Resolve from disk once delivery is confirmed; otherwise prove absence or wait for a human."""
        evidence = await self._delivery_evidence(rec, snap, slot)
        confirmed = bool(rec.evidence.get("delivery_confirmed")) or self._delivery_confirmed(evidence)
        evidence["delivery_confirmed"] = confirmed
        if confirmed:
            if snap is not None:
                resolution, failure = self.evaluate_resolution(rec, snap, slot), None
                if not resolution.resolved:
                    evidence["findings"] = self._merge_findings(evidence.get("findings"),
                                                                resolution.evidence.get("findings"))
                    failure = self._classified_failure(rec, snap) if self._turn_ended(rec, slot, snap) else None
                if resolution.resolved or failure is not None:
                    rec = await self._stamp(rec, evidence)
                    return await (self._fail(rec, snap, failure) if failure else self._settle(rec, snap, resolution))
            return await self._stamp(rec, evidence)
        if evidence.get("wrong_slot"):
            evidence["findings"] = self._with_finding(evidence.get("findings"), "wrong_slot",
                                                      {"slot_key": evidence["wrong_slot"]})
            rec = await self._stamp(rec, evidence)
            await self._record("action.updated", severity="critical", rec=rec,
                               params={"finding": "wrong_slot", "slot_key": evidence["wrong_slot"]})
            return rec
        if all(evidence.get(key) is value for key, value in ABSENCE_PROOF.items()):
            return await self._transition(rec, "NotDelivered", reason="proven_not_delivered", evidence=evidence)
        return await self._stamp(rec, evidence)

    async def _to_reconciliation(
        self, rec: Any, snap: Any, slot: Any, *, reason: str, extra: Mapping[str, Any] | None = None
    ) -> Any:
        """Hand a dispatched decision to a human with every fact Studio can honestly state (C04/R07): what
        ``resubmit``/``mark_not_delivered`` are checked against, plus a refused resolution's findings, which
        ``ConsistencyEngine`` lifts into the blocking set (P04/R01) — dropped, they would unblock the intent."""
        evidence = await self._delivery_evidence(rec, snap, slot)
        if extra:
            evidence = {**evidence, **extra, "findings": self._merge_findings(extra.get("findings"), None)}
        evidence["delivery_confirmed"] = (bool(rec.evidence.get("delivery_confirmed"))
                                          or self._delivery_confirmed(evidence))
        moved = await self._transition(rec, "ReconciliationRequired", reason=reason, evidence=evidence)
        if reason in _ANOMALY_REASONS:
            await self._record("action.updated", severity="critical", rec=moved,
                               params={"finding": reason, "presence": evidence.get("disk_movement")})
        return moved

    # ---- resolution contracts (§1.12 table) ------------------------------- #

    def evaluate_resolution(self, rec: Any, snap: Any, slot: Any = None) -> Resolution:
        """The contract row for this action's decision, then presence and cursor, which turn a matched row into
        one Studio will sign: the disk may have moved for reasons unrelated to what was sent. Both apply to the
        prompt lane only; a force stop puts nothing before the model and never switched the cursor."""
        row = self._contract_row(rec, snap, slot, self._decision_of(rec))
        if not row.resolved:
            return row
        presence = self._presence(rec, snap)
        evidence = {**row.evidence, "presence": presence, "is_active": _attr(snap, "is_active")}
        human = rec.risk_class == "human_lane"
        if human and presence.get("ok") is not True:
            code, params = "human_presence_anomaly", presence
        elif human and _attr(snap, "is_active") is False:
            code, params = "cursor_moved", {"cursor": _attr(snap, "cursor"), "intent_dir": _attr(snap, "intent_dir")}
        else:
            return Resolution(row.outcome, row.reason, evidence)
        findings = self._with_finding(evidence.get("findings"), code, params)
        return Resolution("pending", code, {**evidence, "findings": findings})

    def _contract_row(self, rec: Any, snap: Any, slot: Any, decision: str) -> Resolution:
        """The type/decision row of the §1.12 table, with no presence or cursor judgement yet."""
        action_type, stage = rec.type, rec.stage
        ended = self._turn_ended(rec, slot, snap)
        seen: dict[str, Any] = {"turn_ended": ended}

        if action_type == "gate" and decision in ("approve", "accept_as_is"):
            approved = self._newest_new(rec, snap, ("GATE_APPROVED",), stage)
            if approved is None:
                return Resolution("pending", "no_gate_approved", seen)
            seen["gate_approved"] = _json_of(approved)
            if self._row_completed(snap, stage) or self._current_stage(snap) != stage:
                return Resolution("state_changed", "gate_approved", seen)
            return Resolution("pending", "gate_approved_without_row", seen)

        if action_type == "gate" and decision == "request_changes":
            rejected = self._newest_new(rec, snap, ("GATE_REJECTED",), stage)
            revising = self._newest_new(rec, snap, ("STAGE_REVISING",), stage)
            seen.update(gate_rejected=_json_of(rejected), stage_revising=_json_of(revising))
            if rejected is not None and revising is not None and self._row_mark(snap, stage) in ("R", "r"):
                return Resolution("state_changed", "gate_rejected", seen)
            returned = self._returned_revision_gate(rec, snap, stage) if ended else None
            if returned is not None:
                seen["returned_gate"] = _json_of(returned)
                return Resolution("state_changed", "gate_revision_returned", seen)
            return Resolution("pending", "no_gate_rejected", seen)

        if action_type == "question":
            return self._question_row(rec, snap, decision, stage, ended, seen)

        if action_type == "missing_input" and decision == "provide_input":
            payload = rec.payload or {}
            if payload.get("kind") != "scope":
                return Resolution(*_TURN_OUTCOME[bool(ended)], seen)
            seen["scope"] = current = self._scope_of(snap)
            if current and current == str(payload.get("scope") or "").strip():
                return Resolution("state_changed", "scope_recorded", seen)
            return Resolution("pending", "scope_unchanged", seen)

        if action_type in ("run", "resume"):
            movement = self._newest_new(rec, snap, C.MOVEMENT_EVENTS, None)
            seen.update(state_moved=self._state_moved(rec, snap), movement_event=_json_of(movement))
            if not ended:
                return Resolution("pending", "turn_running", seen)
            if seen["state_moved"] or movement is not None:
                return Resolution("state_changed", "workflow_moved", seen)
            return Resolution("no_transition", "turn_ended_without_movement", seen)

        if action_type == "prepare_commit":
            if not ended:
                return Resolution(*_TURN_OUTCOME[False], seen)
            # AI-DLC records no commit, so Git is the only evidence the conversation made one: the newest commit, when
            # it is newer than the request. "The turn ended" alone told the person to go and look.
            seen["new_commit"] = self._commit_since(rec, snap)
            return Resolution("no_transition", "commit_made" if seen["new_commit"] else "turn_ended_no_commit", seen)

        if action_type == "force_stop":
            if slot is None:
                # Nothing runs in a slot that is gone; `set_interrupted` still fires, because the user asked
                # for a stop and the durable record must say it took effect (C28).
                return Resolution("no_transition", "slot_absent", seen)
            seen["slot_idle"] = not _attr(slot, "running", False) and str(_attr(slot, "stop_state", "idle")) == "idle"
            return Resolution("no_transition", "slot_idle", seen) if seen["slot_idle"] else Resolution(
                "pending", "stop_in_progress", seen)

        return Resolution("pending", "no_contract_row", seen)

    def _commit_since(self, rec: Any, snap: Any) -> dict[str, Any] | None:
        """``{sha, at, subject}`` of the repository's newest commit when it was made after ``rec`` went out, else
        ``None`` (no Git, no commit, or an older one). One ``git log -1``, read only at the end of the turn."""
        sent = _attr(rec, "delivering_at") or _attr(rec, "delivered_at")
        git, root = getattr(self._s, "git", None), _attr(snap, "canonical_path")
        if not sent or git is None or not root:
            return None
        try:
            commits = git.recent_commits(Path(root), [], 1)
        except Exception:  # noqa: BLE001 - FR-GIT-005: a Git failure is a missing fact, never a failed resolution
            return None
        newest = commits[0] if commits else None
        return newest.to_json() if newest is not None and str(newest.at) >= str(sent) else None

    def _question_row(self, rec: Any, snap: Any, decision: str, stage: Any, ended: bool,
                      seen: dict[str, Any]) -> Resolution:
        """The question rows, all ``ResolvedNoTransition``: an answer moves the conversation on, it does not
        close a gate, so ``StateChanged`` would claim a lifecycle move AI-DLC did not make."""
        questions = _attr(snap, "questions")
        digest_changed = self._digest_changed(rec, snap)
        seen["question_digest_changed"] = digest_changed
        if not ended:
            return Resolution("pending", "turn_running", seen)

        if decision == "answers":
            origin = (rec.evidence.get("questions") or {}).get("origin")
            if isinstance(origin, dict) and origin.get("kind") == "audit":
                return self._audit_answer_row(rec, snap, questions, origin, stage, seen)
            answered = self._newest_new(rec, snap, ("QUESTION_ANSWERED",), stage)
            seen["question_answered"] = _json_of(answered)
            if answered is not None:
                return Resolution("no_transition", "question_answered", seen)
            if digest_changed and self._answers_filled(rec, questions):
                return Resolution("no_transition", "answers_recorded", seen)
            return Resolution("pending", "answers_not_recorded", seen)

        if decision == "confirm_summary":
            recorded = self._newest_new(rec, snap, ("SUMMARY_CONFIRMATION_RECORDED",), stage)
            seen["summary_confirmation_recorded"] = _json_of(recorded)
            answered = bool(_attr(_attr(questions, "summary_confirmation"), "answered", False))
            if recorded is not None or (digest_changed and answered):
                return Resolution("no_transition", "summary_confirmed", seen)
            return Resolution("pending", "summary_not_confirmed", seen)

        if decision in ("accept_assumptions", "convert_assumptions"):
            # The engine fills the tag with the chosen option and logs a QUESTION_ANSWERED (intent-capture Step 5);
            # either proof must name *this* choice, or the reply did not carry out what the human confirmed.
            chosen = C.WIRE_ACCEPT_ASSUMPTIONS if decision == "accept_assumptions" else C.WIRE_CONVERT_ASSUMPTIONS
            confirmation = _attr(questions, "assumption_confirmation")
            seen["assumption_answer"] = answer = str(_attr(confirmation, "answer", "") or "").strip()
            if digest_changed and _attr(confirmation, "answered", False) and answer.startswith(chosen):
                return Resolution("no_transition", "assumptions_recorded", seen)
            answered = self._newest_new(rec, snap, ("QUESTION_ANSWERED",), stage)
            details = str((_attr(answered, "fields", {}) or {}).get("Details") or "").strip()
            if answered is not None and details.startswith(chosen):
                seen["question_answered"] = _json_of(answered)
                return Resolution("no_transition", "assumptions_recorded", seen)
            return Resolution("pending", "assumptions_not_recorded", seen)

        if decision == "approve_plan":
            return self._plan_approval_row(rec, snap, questions, stage, digest_changed, seen)

        if decision == "resume":
            # Offered when the plan was asked in an earlier session: resumed, AI-DLC asks it again in this one. The
            # card closes either way once the turn ends, and the next scan offers whatever is waiting then.
            asked = self._plan_rows(rec, snap, stage, "DECISION_RECORDED")
            if asked:
                return Resolution("no_transition", "plan_asked_again",
                                  {**seen, "plan_asked_again": _json_of(max(asked, key=_event_order))})
            return Resolution("no_transition", "resumed_without_reask", seen)

        if decision == "request_plan_changes":
            plan = _attr(questions, "plan_approval")
            seen["plan_answer"] = answer = str(_attr(plan, "answer", "") or "").strip()
            seen["plan_cleared"] = bool(_attr(plan, "present", False)) and not bool(_attr(plan, "answered", False))
            if digest_changed and (plan_approval_choice(answer).lower().startswith("request changes")
                                   or seen["plan_cleared"]):
                return Resolution("no_transition", "plan_changes_requested", seen)
            # 2.10 records a Request Changes choice as a plan-approval QUESTION_ANSWERED, then may revise nothing
            # the questions file shows before asking again.
            receipt = next((event for event in self._plan_rows(rec, snap, stage, "QUESTION_ANSWERED")
                            if (_attr(event, "fields", {}) or {}).get("Details") == C.PLAN_REQUEST_CHANGES_CHOICE), None)
            if receipt is not None:
                return Resolution("no_transition", "plan_changes_requested",
                                  {**seen, "plan_changes_recorded": _json_of(receipt)})
            refused = self._plan_asked_again(rec, snap, stage)
            if refused is not None:
                return Resolution("no_transition", "plan_reply_not_recorded", {**seen, **refused})
            return Resolution("pending", "plan_unchanged", seen)

        return Resolution("pending", "no_contract_row", seen)

    def _audit_answer_row(self, rec: Any, snap: Any, questions: Any, origin: Mapping[str, Any], stage: Any,
                          seen: dict[str, Any]) -> Resolution:
        """An audit-recorded question: settled by the engine's receipt for exactly this question."""
        if origin.get("unit") is not None and (
            _attr(snap, "unit") != origin["unit"]
            or _attr(snap, "audit_question_attempt") != (origin.get("attempt_generation"), True)
        ):
            return Resolution("pending", "question_attempt_changed", seen)
        answered = audit_question_answer(_attr(snap, "audit"), origin, str(rec.wire_text or ""),
                                         str(rec.delivering_at or ""), project_dir=_attr(snap, "canonical_path"))
        seen["question_answered"] = _json_of(answered)
        if answered is not None:
            return Resolution("no_transition", "question_answered", seen)
        # Older Studio versions sent a free-text marker as an enum label, which a proven human turn cannot answer
        # without a note: retire this attempt only, so the still-pending audit decision gets a fresh card.
        supplied = (rec.payload or {}).get("answers") or []
        if (audit_question_accepts_text(questions)
                and (_attr(questions, "origin") or {}).get("decision_sha256") == origin.get("decision_sha256")
                and len(supplied) == 1 and isinstance(supplied[0], dict)
                and not str(supplied[0].get("free_text") or "").strip()
                and rec.wire_text == questions.questions[0].options[0].text):
            return Resolution("no_transition", "answer_requires_text", seen)
        receipt = self._unverified_receipt(rec, snap, origin, stage)
        if receipt is None:
            return Resolution("pending", "answers_not_recorded", seen)
        # A receipt for this question that does not verify our reply retires the lease, whether AI-DLC then opened
        # a gate or asked a follow-up — never a replay, and never an "answered" claim.
        gate = self._next_gate(rec, snap, stage, origin.get("unit"))
        seen.update(answer_verified=False, recorded_answer=_json_of(receipt), next_gate=_json_of(gate))
        reason = "answer_not_verified_at_gate" if gate is not None else "answer_not_verified"
        return Resolution("no_transition", reason, seen)

    def _plan_approval_row(self, rec: Any, snap: Any, questions: Any, stage: Any, digest_changed: bool,
                           seen: dict[str, Any]) -> Resolution:
        plan = _attr(questions, "plan_approval")
        seen["plan_answer"] = answer = str(_attr(plan, "answer", "") or "")
        if digest_changed and _attr(plan, "answered", False) and plan_approval_choice(answer).startswith(
                C.WIRE_APPROVE_PLAN):
            return Resolution("no_transition", "plan_approved", seen)
        # A later Learnings prompt can own `snap.questions` by now; the reader keeps the closed file and its
        # validated receipts from the same snapshot, matched on the plan's path, scope and dispatch boundary.
        closed, origin = _attr(questions, "closed_file"), _attr(questions, "origin") or {}
        captured_question = rec.evidence.get("questions") or {}
        if (closed is not None and origin.get("kind") == "audit" and origin.get("stage") == stage
                and origin.get("unit") == rec.unit and _attr(closed, "relpath") == captured_question.get("relpath")
                and _attr(closed, "sha256") != _attr(rec.captured, "question_digest")):
            fresh = self._new_events(rec, snap, ("PLAN_APPROVAL_RECORDED",), stage)
            receipt = next((
                event for event in (_attr(questions, "closed_file_receipts", ()) or ())
                if event in fresh and self._receipt_prompt_matches(rec, snap, stage, event)
                and event.fields.get("Intent") in (None, rec.intent_uuid)
            ), None)
            if receipt is not None:
                seen.update(plan_approval_recorded=_json_of(receipt), approved_questions_file=_attr(closed, "relpath"))
                return Resolution("no_transition", "plan_approved", seen)
        if _attr(plan, "present", False) and not _attr(plan, "answered", False):
            receipt = self._historical_plan_approval(rec, snap, stage, questions)
            if receipt is not None:
                return Resolution("no_transition", "plan_approval_recorded_before_reset", {
                    **seen, "plan_approval_recorded": _json_of(receipt), "current_plan_requires_approval": True})
        refused = self._plan_asked_again(rec, snap, stage)
        if refused is not None:
            return Resolution("no_transition", "plan_reply_not_recorded", {**seen, **refused})
        return Resolution("pending", "plan_not_approved", seen)

    def _plan_rows(self, rec: Any, snap: Any, stage: Any, *types: str) -> list[Any]:
        """This dispatch's audit rows of the given types for the plan this card showed: same checkpoint, unit and
        questions file."""
        unit = rec.unit or None
        relpath = (rec.evidence.get("questions") or {}).get("relpath")
        return [
            event for event in self._new_events(rec, snap, types, stage)
            if (fields := _attr(event, "fields", {}) or {}).get("Checkpoint") in C.PLAN_APPROVAL_AUDIT_CHECKPOINTS
            and (fields.get("Unit") or None) == unit and (not relpath or fields.get("Questions File") == relpath)
        ]

    def _plan_asked_again(self, rec: Any, snap: Any, stage: Any) -> dict[str, Any] | None:
        """AI-DLC recorded no answer to this plan after the reply, and either refused it (2.10 records only an
        offered choice, in the session that asked) or put the plan to the person again. Settling the card lets the
        next scan open a fresh one for what is waiting — another approval, or a resume when the question belongs to
        an earlier session; the reply is never resent and nothing is approved."""
        if self._plan_rows(rec, snap, stage, "PLAN_APPROVAL_RECORDED", "QUESTION_ANSWERED"):
            return None
        asked = self._plan_rows(rec, snap, stage, "DECISION_RECORDED")
        refusals = [event for event in self._new_events(rec, snap, (C.ERROR_EVENT,), None)
                    if "Plan Approval" in _error_text(event)]
        if not asked and not refusals:
            return None
        return {
            "answer_verified": False,
            "plan_asked_again": _json_of(max(asked, key=_event_order)) if asked else None,
            "refusal": _error_text(max(refusals, key=_event_order)) if refusals else None,
        }

    # ---- resolution outcome and failure classification -------------------- #

    async def _settle(self, rec: Any, snap: Any, resolution: Resolution) -> Any:
        """Commit a resolution. ``resolved_at`` and ``resolution_json`` are composed by the broker's own terminal
        write from the reason and evidence handed over (§1.12): one record, one author."""
        to_status = "StateChanged" if resolution.outcome == "state_changed" else "ResolvedNoTransition"
        rec = await self._transition(rec, to_status, reason=resolution.reason, evidence=resolution.evidence)
        if rec.type == "force_stop":
            # A resolved force stop is a durable interruption (C28/P17), cleared only by the recovery card's
            # `acknowledge`: PRD §11.2 requires a manual takeover after a mid-stage interruption.
            await self._s.sessions.set_interrupted(rec.repo_id, rec.space, rec.intent_dir, self._s.clock.iso())
            await self._record("intent.force_stop", severity="attention", rec=rec, params={"slot_key": rec.slot_key})
        self._forget(rec)
        return rec

    async def _fail(self, rec: Any, snap: Any, resolution: Resolution) -> Any:
        fingerprint, cls, summary_key = self.classify_failure(
            host_error=resolution.evidence.get("host_error"),
            audit_errors=list(resolution.evidence.get("audit_error_events") or ()),
        )
        try:
            rec, opened = await self._s.actions.record_failure(
                rec, fingerprint=fingerprint, fingerprint_class=cls, summary=summary_key,
                evidence=_storable(resolution.evidence))
        except (IllegalTransition, StaleGeneration) as exc:
            # The row moved between the read and the verdict; failing it anyway would strike the breaker for a
            # decision that has already settled.
            _LOG.debug("failure for %s skipped: %s", rec.action_id, exc)
            return await self._s.actions.get(rec.action_id)
        self._forget(rec)
        if opened:
            _LOG.info("breaker opened for %s (%s)", rec.intent_dir, fingerprint)
        return rec

    @staticmethod
    def _merge_findings(left: Any, right: Any) -> list[dict]:
        merged: dict[str, dict] = {}
        for item in [*(left or []), *(right or [])]:
            code = str(item.get("code") or "") if isinstance(item, Mapping) else ""
            if code:
                merged.setdefault(code, {"code": code, "params": dict(item.get("params") or {})})
        return list(merged.values())

    def classify_failure(self, *, host_error: str | None, audit_errors: Sequence[Any]) -> tuple[str, str, str]:
        """``(fingerprint, fingerprint_class, summary_key)`` from one message, not the burst: a fingerprint that
        changed with the count would never reach the transient threshold. The host's error wins (it describes the
        layer that failed); the class is read from the readable text, the fingerprint from the scrubbed one."""
        message = str(host_error or "").strip()
        if not message and audit_errors:
            message = _error_text(max(audit_errors, key=lambda e: _attr(e, "sort_key", ("", 0, 0))))
        readable = re.sub(r"\s+", " ", message).strip().lower()
        normalised = _normalise_message(message)
        cls = next((name for name, pattern in FINGERPRINT_RULES if pattern.search(readable)), "config")
        digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:6]
        return f"{cls}.{_slug_of(normalised)}:{digest}", cls, f"template.failure.{cls}"

    def _classified_failure(self, rec: Any, snap: Any) -> Resolution | None:
        """A host error on the receipt, or a burst of new ``ERROR_LOGGED`` rows; a single row is the conductor
        correcting itself (FORMAT-NOTES §6), and failing on it would turn self-correction into a breaker."""
        receipt = rec.evidence.get("receipt") or {}
        host_error = receipt.get("error") if isinstance(receipt, Mapping) else None
        errors = [event for event in self._new_events(rec, snap, (C.ERROR_EVENT,), None)
                  if (gap := _elapsed(rec.delivering_at, _attr(event, "timestamp"))) is None
                  or gap <= _ERROR_BURST_WINDOW_SECS]
        if not host_error and len(errors) < _ERROR_BURST_COUNT:
            return None
        return Resolution("failed", "host_error" if host_error else "error_burst", {
            "host_error": host_error, "audit_error_events": errors, "audit_errors": [_json_of(e) for e in errors]})

    # ---- delivery evidence, presence, turn -------------------------------- #

    async def _delivery_evidence(self, rec: Any, snap: Any, slot: Any) -> dict[str, Any]:
        """Everything Studio can honestly say about whether this dispatch reached the host. The facts only mean
        something together: a restart empties the transcript, and a quiet slot means nothing if the disk moved."""
        host = self._s.host
        slot_key, since = str(rec.slot_key or ""), str(rec.delivering_at or "")
        row = wrong_slot = None
        readable = False
        if slot_key and since:
            row = self._delivery_row(rec)
            # Each read updates the host capabilities: capture this one before a healthy read of another slot
            # erases its failure. Missing evidence never proves non-delivery.
            readable = host.can_prove_absence()
            if row is None:
                row, wrong_slot, others_readable = self._search_other_slots(rec)
                readable = readable and others_readable
        fresh = await self._fresh_presence(rec, snap)
        presence = self._presence(rec, snap)
        return {
            # Findings already on the row are carried: a presence anomaly recorded a tick ago is still true.
            "findings": self._merge_findings(rec.evidence.get("findings"), None),
            "transcript_row": None if row is None else {
                "slot_key": wrong_slot or slot_key, "ts": row.ts, "role": row.role},
            "transcript_row_absent": (row is None) if readable else False,
            "wrong_slot": wrong_slot,
            "slot_ran_since": self._slot_ran_since(rec, slot),
            "boot_id_unchanged": str(rec.boot_id or "") == str(getattr(self._s, "boot_id", "") or ""),
            "disk_movement": {
                **{key: presence.get(key) for key in ("human_turn_delta", "marker_advanced")},
                "resolution_event_seen": self._newest_new(rec, snap, C.RESOLUTION_EVENTS, None) is not None,
            },
            "disk_baseline_unchanged": (presence_unchanged(rec.presence_baseline, fresh)
                                        if rec.presence_baseline is not None else None),
        }

    def _search_other_slots(self, rec: Any) -> tuple[Any, str | None, bool]:
        """A matching row in another slot of the repository, its slot, and whether every read succeeded (R08). A
        row elsewhere is ``wrong_slot``: it reached the host, not the captured session, so nothing is automatic."""
        try:
            repo = self._s.repos.get(rec.repo_id)
        except StudioError:
            return None, None, False
        host = self._s.host
        views = host.slots_for_project(repo.canonical_path)
        readable = host.can_prove_absence()
        for view in views:
            if view.key == str(rec.slot_key or ""):
                continue
            found = host.find_delivery_row(view.key, since_ts=str(rec.delivering_at or ""),
                                           delivery_id=str(rec.delivery_id or ""), wire_text=str(rec.wire_text or ""))
            readable = host.can_prove_absence() and readable
            if found is not None:
                return found, view.key, readable
        return None, None, readable

    def _slot_ran_since(self, rec: Any, slot: Any) -> bool | None:
        """Did the bound slot run a turn after ``delivering_at``? ``None`` (never ``False``) when the slot cannot
        be read: ``False`` is one of the four proofs of ``NotDelivered``."""
        if slot is None or not self._slots_readable():
            return None
        if self._ran.get(rec.action_id) or _busy_reasons(slot):
            return True
        return bool(_iso_ge(_attr(slot, "last_turn_ts"), rec.delivering_at))

    @staticmethod
    def _delivery_confirmed(evidence: Mapping[str, Any]) -> bool:
        movement = evidence.get("disk_movement") or {}
        delta = movement.get("human_turn_delta")
        return bool((evidence.get("transcript_row") is not None and not evidence.get("wrong_slot"))
                    or (isinstance(delta, int) and delta >= 1) or movement.get("resolution_event_seen"))

    async def _fresh_presence(self, rec: Any, snap: Any) -> Any:
        if snap is None:
            return None
        try:
            repo = await self._to_thread(self._s.repos.get, rec.repo_id)
            return await self._in_pool(self._s.reader.presence_baseline, Path(repo.canonical_path), rec.space,
                                       rec.intent_dir, _attr(snap, "audit"), _attr(snap, "state_sha256"))
        except (StudioError, OSError):
            return None

    def _presence(self, rec: Any, snap: Any) -> dict[str, Any]:
        """Human turns and the adapter's marker against the submit-time baseline; ``ok`` is ``None`` without one
        (nothing was sent to the model). In flight a decision needs exactly one new turn; in
        ``ReconciliationRequired`` a complete audit with any new turn may settle, or the action would be stranded
        against a baseline that must never be rewritten. Partial history counts the turns after dispatch."""
        baseline = rec.presence_baseline
        if baseline is None or snap is None:
            return {"human_turn_delta": None, "marker_advanced": None, "ok": None}
        events = list(_attr(_attr(snap, "audit"), "events", ()) or ())
        delta = sum(1 for e in events if _attr(e, "event") == C.HUMAN_PRESENCE_EVENT) - int(
            _attr(baseline, "human_turn_events", 0) or 0)
        before, after = _attr(baseline, "human_turn_mtime_ns"), _attr(_attr(snap, "markers"), "human_turn_mtime_ns")
        advanced = after is not None and (before is None or int(after) > int(before))
        if _attr(baseline, "human_turn_events_partial", False):
            fresh = len(self._new_events(rec, snap, (C.HUMAN_PRESENCE_EVENT,), None))
            return {"human_turn_delta": delta, "new_human_turns": fresh, "partial": True,
                    "marker_advanced": advanced, "ok": fresh >= 1 and advanced}
        reconciling = rec.status == "ReconciliationRequired" and _attr(_attr(snap, "audit"), "complete", False) is True
        ok = bool((delta >= 1 if reconciling else delta == 1) and advanced)
        presence = {"human_turn_delta": delta, "partial": False, "marker_advanced": advanced, "ok": ok}
        if not ok and delta > 1 and advanced:
            machine = self._machine_prompts(rec)
            if machine is not None and delta - 1 <= machine:
                presence.update(machine_prompts=machine, ok=True)
        return presence

    def _machine_prompts(self, rec: Any) -> int | None:
        """How many prompts KiroCrew itself put into the conversation since this delivery: an automatic recovery
        after a backend error, a sub-agent's result, a scheduled or app message. AI-DLC records each as a
        ``HUMAN_TURN``, since its hook cannot tell who submitted a prompt; KiroCrew's transcript can. ``None`` when
        the transcript does not show this delivery (a restarted gateway holds none), or shows a person's message
        beside it: then the extra turns stay unexplained and the anomaly stands."""
        if not rec.slot_key or not rec.delivering_at:
            return None
        rows = self._s.host.recent_rows(str(rec.slot_key), since_ts=str(rec.delivering_at), roles=_PROMPT_ROLES,
                                        limit=_PRESENCE_ROWS)
        people = [row for row in rows if row.role in _PERSON_ROLES]
        mine = [row for row in people if (rec.delivery_id and row.meta.get("studio_delivery_id") == rec.delivery_id)
                or (rec.wire_text and row.content == rec.wire_text)]
        if not mine or len(people) > len(mine):
            return None
        return sum(1 for row in rows if row.role not in _PERSON_ROLES)

    def _turn_started(self, rec: Any, slot: Any, snap: Any) -> bool:
        """Has the queued message become a running turn (P22)? The host has no "queue drained" event, so: the
        slot went busy, or the queued row became a user row with an assistant reply behind it."""
        if self._ran.get(rec.action_id) or (slot is not None and _busy_reasons(slot)):
            return True
        row = self._delivery_row(rec) if slot is not None else None
        return row is not None and self._assistant_after(rec, row)

    def _turn_ended(self, rec: Any, slot: Any, snap: Any) -> bool:
        """Did the turn finish? The slot is idle *and* a turn visibly happened (we watched it run, or a reply
        follows the delivery row): a slot that never started is idle too. Disk movement proves work, not an
        end, so without a live slot reading the lease is kept."""
        if not self._slots_readable() or slot is None or _busy_reasons(slot):
            return False
        if self._ran.get(rec.action_id):
            return True
        row = self._delivery_row(rec)
        return row is not None and (self._assistant_after(rec, row) or _iso_gt(_attr(slot, "last_turn_ts"), row.ts))

    def _delivery_row(self, rec: Any) -> Any:
        if not rec.slot_key or not rec.delivering_at:
            return None
        return self._s.host.find_delivery_row(str(rec.slot_key), since_ts=str(rec.delivering_at),
                                              delivery_id=str(rec.delivery_id or ""),
                                              wire_text=str(rec.wire_text or ""))

    def _assistant_after(self, rec: Any, row: Any) -> bool:
        rows = self._s.host.recent_rows(str(rec.slot_key or ""), since_ts=row.ts, roles=("assistant",), limit=10)
        return any(_iso_ge(r.ts, row.ts) for r in rows)

    def _observe_run(self, rec: Any, slot: Any) -> None:
        """Remember that the bound slot was busy after this dispatch. The host keeps no "was it running" history,
        so a missed observation is lost for good, and ``slot_ran_since`` is one of the ``NotDelivered`` proofs."""
        if slot is None or not _busy_reasons(slot):
            return
        last = _attr(slot, "last_ts")
        if not last or _iso_ge(last, rec.delivering_at):
            self._ran[rec.action_id] = True

    def _forget(self, rec: Any) -> None:
        self._ran.pop(rec.action_id, None)

    # ---- disk predicates -------------------------------------------------- #

    def _captured_plan_prompt(self, rec: Any, snap: Any, stage: Any, receipt: Any) -> Any:
        """The Plan Approval prompt ``receipt`` answers, when it is the one this card showed. The capture matches
        ``Questions SHA-256`` (the file's bytes), the receipt ``Prompt SHA-256`` (every answer blanked), which
        differ whenever the file holds another answer or other line endings. The prompt is the newest same-target
        decision before the receipt in its shard and must predate the dispatch: one asked again after the reply
        was sent could have consumed some other reply."""
        unit = rec.unit or None
        captured = _attr(rec.captured, "question_digest")
        relpath = (rec.evidence.get("questions") or {}).get("relpath")
        sent = C.epoch_from_iso(rec.delivering_at)
        if not captured or not relpath or sent is None:
            return None
        prompts = [
            event for event in list(_attr(_attr(snap, "audit"), "events", ()) or ())
            if _attr(event, "event") == "DECISION_RECORDED" and _attr(event, "shard") == _attr(receipt, "shard")
            and _event_order(event) < _event_order(receipt)
            and (_attr(event, "fields", {}) or {}).get("Stage") == stage
            and ((_attr(event, "fields", {}) or {}).get("Unit") or None) == unit
        ]
        if not prompts:
            return None
        prompt = max(prompts, key=_event_order)
        fields = _attr(prompt, "fields", {}) or {}
        asked = C.epoch_from_iso(_attr(prompt, "timestamp"))
        if (asked is None or asked > sent or fields.get("Workflow")
                or fields.get("Checkpoint") != C.PLAN_APPROVAL_AUDIT_CHECKPOINT
                or fields.get("Questions File") != relpath or fields.get("Questions SHA-256") != captured
                or not _SHA256_HEX_RE.fullmatch(str(fields.get("Prompt SHA-256") or ""))):
            return None
        return prompt

    def _receipt_prompt_matches(self, rec: Any, snap: Any, stage: Any, receipt: Any) -> bool:
        """Does a closed file's approval receipt answer the prompt this card captured? Older receipts carry no
        prompt digest; blanking changes nothing in a file holding only a blank Plan Approval answer, so an
        equal whole-file digest is the same prompt."""
        prompt_sha = (_attr(receipt, "fields", {}) or {}).get("Prompt SHA-256")
        if prompt_sha in (None, _attr(rec.captured, "question_digest")):
            return True
        prompt = self._captured_plan_prompt(rec, snap, stage, receipt)
        return prompt is not None and (_attr(prompt, "fields", {}) or {}).get("Prompt SHA-256") == prompt_sha

    def _historical_plan_approval(self, rec: Any, snap: Any, stage: Any, questions: Any) -> Any:
        """Settle the old reply, never grant authority to a plan reset since. The prompt is found from the
        receipt, not at the card's captured boundary: that boundary is the newest gate/question row of any
        kind, which a second unit's plan prompt or another clone's shard can occupy instead."""
        if stage != "code-generation" or not _attr(_attr(snap, "audit"), "complete", False):
            return None
        captured_question = rec.evidence.get("questions") or {}
        if _attr(questions, "relpath") != captured_question.get("relpath") or not rec.intent_uuid:
            return None
        unit = rec.unit or None
        identity = {
            "Stage": stage, "Checkpoint": C.PLAN_APPROVAL_AUDIT_CHECKPOINT,
            "Plan Target": f"unit:{unit}" if unit else "stage:code-generation",
            "Intent": rec.intent_uuid, "Questions File": captured_question.get("relpath"),
        }
        for receipt in self._new_events(rec, snap, ("PLAN_APPROVAL_RECORDED",), stage):
            prompt = self._captured_plan_prompt(rec, snap, stage, receipt)
            fields = _attr(prompt, "fields", {}) or {}
            if prompt is None or any(fields.get(key) != value for key, value in identity.items()):
                continue
            if not fields.get("Run floor") or not fields.get("Session") or any(
                not re.fullmatch(r"sha256:[0-9a-f]{64}", str(fields.get(key) or ""))
                for key in ("Directive Epoch", "Approval Fingerprint")
            ):
                continue
            required = {**identity, **{key: fields[key] for key in (
                "Directive Epoch", "Approval Fingerprint", "Run floor", "Session", "Prompt SHA-256")}}
            received = _attr(receipt, "fields", {}) or {}
            approved_sha = str(received.get("Questions SHA-256") or "")
            if (received.get("Details") != C.WIRE_APPROVE_PLAN or (received.get("Unit") or None) != unit
                    or received.get("Workflow") or any(received.get(key) != value for key, value in required.items())
                    or not _SHA256_HEX_RE.fullmatch(approved_sha) or approved_sha == fields.get("Questions SHA-256")):
                continue
            return receipt
        return None

    def _returned_revision_gate(self, rec: Any, snap: Any, stage: Any) -> Any:
        """Recover a finished revision no poll saw as ``[R]``: by then the returned gate may be ``?`` or even
        approved, so the mark proves nothing. Require the original boundary, a complete same-shard rejection →
        revision → returned-gate chain in this unit (each link the newest in scope, so another unit's later rows
        cannot hide it) and the matching revision increase. This never approves the gate."""
        at_gate = self._current_stage(snap) == stage and self._row_mark(snap, stage) == "?"
        if (not (at_gate or self._row_completed(snap, stage)) or not _attr(_attr(snap, "audit"), "complete", False)
                or self._boundary_order(rec, snap) is None):
            return None
        shard = ((rec.evidence.get("audit") or {}).get("boundary_event") or {}).get("shard")
        unit = rec.unit or None
        before = (rec.evidence.get("state") or {}).get("revision_count")
        current = _attr(_attr(snap, "state"), "revision_count")
        if not shard or type(before) is not int or type(current) is not int or current <= before:
            return None

        def scoped(event_type: str) -> list[Any]:
            return sorted((event for event in self._new_events(rec, snap, (event_type,), stage)
                           if _attr(event, "shard") == shard and _unit_scoped(event, unit, snap)), key=_event_order)

        rejections, revisions = scoped("GATE_REJECTED"), scoped("STAGE_REVISING")
        for gate in reversed(scoped("STAGE_AWAITING_APPROVAL")):
            revising = next((e for e in reversed(revisions) if _event_order(e) < _event_order(gate)), None)
            if revising is None:
                continue
            rejected = next((e for e in reversed(rejections) if _event_order(e) < _event_order(revising)), None)
            if rejected is None:
                continue
            times = [C.epoch_from_iso(_attr(event, "timestamp")) for event in (rejected, revising, gate)]
            if any(value is None for value in times) or not (times[0] <= times[1] <= times[2]):
                continue
            fields = _attr(revising, "fields", {}) or {}
            recorded = str(fields.get("Revision count", fields.get("Revision Count", ""))).strip()
            if recorded.isdecimal() and int(recorded) == current:
                return gate
        return None

    def _unverified_receipt(self, rec: Any, snap: Any, origin: Mapping[str, Any], stage: Any) -> Any:
        """A new receipt for exactly this audit question whose recorded text is not our reply."""
        for candidate in self._new_events(rec, snap, ("QUESTION_ANSWERED",), stage):
            recorded_text = (_attr(candidate, "fields", {}) or {}).get("Details")
            if isinstance(recorded_text, str) and recorded_text and audit_question_answer(
                _attr(snap, "audit"), origin, recorded_text, str(rec.delivering_at or ""),
                project_dir=_attr(snap, "canonical_path"),
            ) is candidate:
                return candidate
        return None

    def _next_gate(self, rec: Any, snap: Any, stage: Any, unit: Any) -> Any:
        """The newest new gate in this stage and unit, while the stage is still waiting at it."""
        if self._current_stage(snap) != stage or self._row_mark(snap, stage) != "?":
            return None
        gates = [event for event in self._new_events(rec, snap, ("STAGE_AWAITING_APPROVAL",), stage)
                 if _unit_scoped(event, unit, snap)]
        return max(gates, key=lambda e: _attr(e, "sort_key", ("", 0, 0))) if gates else None

    def _new_events(self, rec: Any, snap: Any, types: Sequence[str], stage: Any) -> list[Any]:
        """Audit rows of *this* dispatch (P23): at or after ``delivering_at`` (second precision, hence ``>=``)
        *and* after the captured boundary event. Time alone would accept the row that opened the gate; position
        alone, a row from a previous session in a shard that happens to sort late."""
        if snap is None:
            return []
        since, wanted, floor = rec.delivering_at, set(types), self._boundary_order(rec, snap)
        return [
            event for event in list(_attr(_attr(snap, "audit"), "events", ()) or ())
            if _attr(event, "event") in wanted
            and (stage is None or (_attr(event, "fields", {}) or {}).get("Stage") == stage)
            and (not since or _iso_ge(_attr(event, "timestamp"), since))
            and (floor is None or _event_order(event) > floor)
        ]

    def _newest_new(self, rec: Any, snap: Any, types: Sequence[str], stage: Any) -> Any:
        events = self._new_events(rec, snap, types, stage)
        return max(events, key=lambda e: _attr(e, "sort_key", ("", 0, 0))) if events else None

    def _boundary_order(self, rec: Any, snap: Any) -> tuple[int, int] | None:
        """``(shard_index, pos)`` of the captured boundary event in today's bundle, or ``None``: a vanished shard
        leaves the timestamp rule alone. A truncated window numbers blocks from wherever it starts, so a position
        read from one (at capture or now) names another row once the file grows: inside it, time decides alone."""
        audit = rec.evidence.get("audit") or {}
        boundary = audit.get("boundary_event") if isinstance(audit, Mapping) else None
        if not isinstance(boundary, Mapping):
            return None
        shard, pos = str(boundary.get("shard") or ""), boundary.get("pos")
        if not shard or not isinstance(pos, int):
            return None
        captured_truncated = any(
            isinstance(meta, Mapping) and meta.get("truncated")
            and str(meta.get("relpath") or "").rsplit("/", 1)[-1] == shard
            for meta in (audit.get("shards") or ())
        )
        for index, meta in enumerate(list(_attr(_attr(snap, "audit"), "shards", ()) or ())):
            if str(_attr(meta, "relpath", "")).rsplit("/", 1)[-1] == shard:
                return (index, -1) if captured_truncated or _attr(meta, "truncated", False) else (index, pos)
        return None

    def _state_moved(self, rec: Any, snap: Any) -> bool:
        """Did the state file change since capture? Every engine write to ``aidlc-state.md`` changes the digest,
        so this stands in for "a stage row or ``Current Stage`` changed" and errs towards ``StateChanged``."""
        captured, current = _attr(rec.captured, "state_hash"), _attr(snap, "state_sha256")
        return bool(captured) and bool(current) and captured != current

    def _digest_changed(self, rec: Any, snap: Any) -> bool:
        return _attr(rec.captured, "question_digest") != _attr(_attr(snap, "questions"), "sha256")

    def _answers_filled(self, rec: Any, questions: Any) -> bool:
        """Is every index the submit answered now non-blank in the file?"""
        indices = {int(a.get("index")) for a in ((rec.payload or {}).get("answers") or [])
                   if isinstance(a, Mapping) and isinstance(a.get("index"), int)}
        if not indices or questions is None:
            return False
        by_index = {int(_attr(q, "index", 0)): q for q in _attr(questions, "questions", ()) or ()}
        return all(bool(_attr(by_index.get(i), "answered", False)) for i in indices)

    def _row_mark(self, snap: Any, stage: Any) -> str | None:
        state = _attr(snap, "state")
        row = state.row(str(stage)) if state is not None and stage else None
        return None if row is None else str(_attr(row, "mark", "") or "")

    def _row_completed(self, snap: Any, stage: Any) -> bool:
        return C.CHECKBOX_MAP.get((self._row_mark(snap, stage) or "").lower()) == "completed"

    def _current_stage(self, snap: Any) -> Any:
        return _attr(_attr(snap, "state"), "current_stage")

    def _scope_of(self, snap: Any) -> str | None:
        state = _attr(snap, "state")
        return None if state is None else (dict(_attr(state, "project", {}) or {}).get("Scope") or "").strip() or None

    # ---- tick step 6: advisor -------------------------------------------- #

    async def _advisor_pass(self) -> tuple[int, int]:
        """Settle drafts nobody is polling, then start at most one draft the owner has standing-granted.

        Settling first is the ordering: the host prunes a finished subagent's answer after an hour and a pre-draft
        exists because nobody has the card open, so starting drafts must never be why a finished one went unread;
        it also frees the slots the automatic cap counts. Each half is caught on its own and never re-raised: an
        advisor that cannot spawn is a missing convenience, a dead tick stops every delivery. The counts are for
        tests only (``health.reconciler`` is a pinned four-key object).
        """
        broker = getattr(self._s, "advisor", None)
        settle_open = getattr(broker, "settle_open", None)
        autodraft = getattr(broker, "autodraft", None)
        if not callable(settle_open) or not callable(autodraft):
            return 0, 0  # the one collaborator a tick does not need; tests wire only what they assert on
        settled = started = 0
        try:
            settled = int(await settle_open())
        except Exception as exc:
            _LOG.debug("advisor settle pass failed: %s", exc)
        try:
            started = int(await autodraft())
        except Exception as exc:
            _LOG.debug("advisor pre-draft pass failed: %s", exc)
        return settled, started

    # ---- tick step 7: retention ------------------------------------------ #

    async def _retention_pass(self) -> tuple[int, int]:
        """Purge verbatim human text past its retention, delete Activity rows older than
        ``diagnostics.retention_days``, and expire advisor drafts."""
        days = int(self._s.settings.human_text_retention_days())
        cutoff = C.iso_from_epoch(self._s.clock.now() - days * 86400)
        purged = await self._to_thread(self._s.storage.activity_purge_human_text, cutoff)
        keep = int(self._s.settings.diagnostics()["retention_days"])
        await self._to_thread(
            self._s.storage.activity_delete_before, C.iso_from_epoch(self._s.clock.now() - keep * 86400)
        )
        expired = await self._expire_drafts()
        return int(purged), expired

    async def _expire_drafts(self) -> int:
        """Retire past-TTL drafts through the broker, the one writer of that table: its ``expire_stale`` publishes
        ``advisor.updated``, so an open tab drops a stale *Apply* within a tick. No broker expires nothing."""
        expire = getattr(getattr(self._s, "advisor", None), "expire_stale", None)
        if not callable(expire):
            return 0
        return int(await expire())

    # ---- transitions, evidence writes, small helpers ---------------------- #

    async def _transition(self, rec: Any, to_status: str, *, reason: str, evidence: Mapping[str, Any]) -> Any:
        """One status move through the broker, with this pass's evidence on top of the row's so the resolution
        carries everything it was settled on. A lost race (``StaleGeneration``, ``IllegalTransition``) answers
        with the current row: retrying would replay a judgement against superseded evidence."""
        try:
            return await self._s.actions.transition(
                rec.action_id, to_status, expected_generation=int(rec.status_generation or 0), reason=reason,
                evidence={**rec.evidence, **_storable(evidence)},
            )
        except (StaleGeneration, IllegalTransition) as exc:
            _LOG.debug("transition %s → %s skipped: %s", rec.action_id, to_status, exc)
            return await self._s.actions.get(rec.action_id)

    async def _stamp(self, rec: Any, evidence: Mapping[str, Any], **values: Any) -> Any:
        """Merge evidence (and row values) without a status change; nothing is written when nothing changed."""
        return await self._s.actions.patch_evidence(rec, _storable(evidence), values=values or None)

    async def _stamp_evidence_row(self, row: Mapping[str, Any], patch: Mapping[str, Any]) -> None:
        await self._stamp(await self._s.actions.get(str(row.get("action_id"))), patch)

    @staticmethod
    def _with_finding(existing: Any, code: str, params: Mapping[str, Any]) -> list[dict]:
        """Add a reconciler-discovered finding (P04/R01/R08) in the shape ``ConsistencyEngine`` lifts back out:
        stored on the action, it becomes a blocking finding and a recovery card."""
        out = [f for f in (existing or []) if isinstance(f, Mapping) and f.get("code") != code]
        out.append({"code": code, "params": {k: v for k, v in dict(params).items() if k != "ok"}})
        return out

    def _decision_of(self, rec: Any) -> str:
        """The submitted decision; a command carries it in its type, and a card that never sent has none."""
        payload = rec.payload
        decision = str(payload.get("decision") or "") if isinstance(payload, Mapping) else ""
        return decision or (rec.type if rec.type in C.DECISION_KIND else "")

    def _slot_for(self, rec: Any) -> Any:
        return self._s.host.slot(str(rec.slot_key)) if rec.slot_key else None

    def _slots_readable(self) -> bool:
        """Can the host's slot table be read at all? "No slot" is a terminated session, "no host" a disk-only
        Studio (C08): collapsing them would send every in-flight action to reconciliation before attach."""
        host = self._s.host
        if not host.attached():
            return False
        cap = host.capabilities().get("slots")
        return cap is None or bool(_attr(cap, "available", True))

    def _session_replaced(self, rec: Any, slot: Any) -> bool:
        """Is the session this decision was dispatched into gone, or now a different session?"""
        if not self._slots_readable():
            return False
        if slot is None:
            return True
        recorded, observed = str(rec.session_key or ""), str(_attr(slot, "session_key") or "")
        return bool(recorded and observed and recorded != observed)

    def _past_deadline(self, rec: Any) -> bool:
        return bool(rec.deadline_at) and str(self._s.clock.iso()) > str(rec.deadline_at)

    def _turn_timeout(self) -> int:
        return int(min(self._s.host.turn_timeout_secs(), C.PROCESSING_DEADLINE_CAP_SECS))

    @staticmethod
    def _deadline_from(iso: str, seconds: int) -> str:
        return C.iso_from_epoch((C.epoch_from_iso(iso) or 0.0) + seconds)

    async def _record(
        self, kind: str, *, severity: str, params: Mapping[str, Any], rec: Any = None
    ) -> None:
        with contextlib.suppress(StudioError):
            await self._s.activity.record(
                kind=kind,
                severity=severity,
                repo_id=_attr(rec, "repo_id"),
                space=_attr(rec, "space"),
                intent_dir=_attr(rec, "intent_dir"),
                stage=_attr(rec, "stage"),
                action_id=_attr(rec, "action_id"),
                params=dict(params),
            )

    async def _publish(self, event_type: str, payload: Mapping[str, Any]) -> None:
        events = getattr(self._s, "events", None)
        if events is None:
            return
        with contextlib.suppress(StudioError, ValueError):
            await events.publish(event_type, dict(payload))

    async def _notify(self, kind: str, subject: Any = None, *, snap: Any = None, **fields: Any) -> None:
        """One notification attempt, never raised: the scan that found the news must go on.

        A card is looked up in the ledger before it is rendered, so a scan whose cards were all announced renders
        none of them.
        """
        notifications = getattr(self._s, "notifications", None)
        if notifications is None:
            return
        try:
            if kind == "action":
                if await notifications.announced(subject.action_id):
                    return
                subject = await self._s.actions.card(subject, snap)
            await notifications.notify(kind, subject, **fields)
        except Exception:
            _LOG.debug("%s notification failed", kind, exc_info=True)

    # ---- threading ------------------------------------------------------- #

    def _semaphore(self) -> asyncio.Semaphore:
        """The scan gate, re-created when the running loop changes: a semaphore remembers the loop it first
        suspended on, and tests drive one ``Reconciler`` from several ``asyncio.run`` calls."""
        loop = asyncio.get_running_loop()
        if self._sem_obj is None or self._sem_loop is not loop:
            self._sem_obj = asyncio.Semaphore(C.SCAN_CONCURRENCY)
            self._sem_loop = loop
        return self._sem_obj

    async def _in_pool(self, fn: Callable[..., Any], *args: Any) -> Any:
        """One sync call on Studio's own scan pool (review R11): the default executor is the host's (chat
        persistence, spawns, atomic writes), and a 64-repository pass must not block the gateway's own work."""
        pool = getattr(self._s, "scan_pool", None)
        if pool is None:
            return await asyncio.to_thread(fn, *args)
        return await asyncio.get_running_loop().run_in_executor(pool, functools.partial(fn, *args))

    @staticmethod
    async def _to_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Short store calls and single-file reads on the default executor (§0.6); walks go through `_in_pool`."""
        return await asyncio.to_thread(functools.partial(fn, *args, **kwargs))
