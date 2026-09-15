"""Reconciliation — the loop that closes decisions Studio has already committed.

Every other module is allowed to refuse: a handler answers 409, the broker declines a transition, the
scheduler withholds a lease. This one cannot. Once the ``Delivering`` row is durable the decision may
already be in the model's mouth, so the only honest answers left are "it landed"
(``StateChanged``/``ResolvedNoTransition``), "we cannot tell" (``DeliveryUncertain`` →
``ReconciliationRequired``) and — with four independent proofs, never on a client's word — "it never
left" (``NotDelivered``). Nothing here sends a prompt, stops a turn, replays a decision, writes an
AI-DLC file or runs a protected engine verb: the reconciler reads disk, reads the host, and moves
Studio's own rows.

Four kinds of care run through the whole module.

**Ordering is evidence.** "New" audit rows are ``ts >= delivering_at`` *and* positioned after the
boundary event the card captured, because the engine writes ``GATE_APPROVED`` in the same second as
the human's turn (review P23); second-precision ``>`` would miss exactly the row that proves the
decision landed.

**Presence is required.** An in-flight human-lane resolution needs exactly one new ``HUMAN_TURN``
since the baseline the submit recorded. Zero or multiple turns escalate as ``human_presence_anomaly``
(FR-SES-010, C23). Once delivery is confirmed in ``ReconciliationRequired``, a complete audit with a
positive increment and an advanced human marker can close a matching resolution contract. The original
baseline stays intact; later turns must not make a completed decision permanently unresolvable.

**A restart is not an absence.** ``slot.messages`` is process memory, so a gateway restart between the
browser's send and its report empties the only host-side evidence there was. Every ``Delivering``-class
row carries the ``boot_id`` that committed it; when that differs, ``NotDelivered`` becomes unreachable
by construction (review R02).

**Nothing may starve the loop.** Repository walks run on Studio's own two-worker pool under a
semaphore with a per-tick wall budget, and lease heartbeats run in a task that never waits on a scan —
64 repositories × a 10 s walk on the host's shared executor would stall chat persistence and spawns,
and a heartbeat that drifted behind a scan would let a healthy submit look abandoned (review R11/C32).
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
from .aidlc_reader import audit_question_accepts_text, audit_question_answer
from .consistency import RepoFacts
from .errors import IllegalTransition, StaleGeneration, StudioError
from .leases import LeaseGrant
from .projection import run_unavailable_reason
from .repo_registry import RepoScan

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .projection import StableBoundary
    from .repo_registry import RepoRecord
    from .storage import LeaseRecord

__all__ = [
    "FINGERPRINT_RULES",
    "PRESENCE_COMPARED_FIELDS",
    "REPO_SCAN_MIN_INTERVAL_SECS",
    "RECONCILABLE_STATUS",
    "SCAN_TICK_BUDGET_SECS",
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

#: Aliased from ``constants.py``, which owns it (§1.1): this module must not become a second source of
#: truth for a number that decides how long a tick may spend walking disks.
SCAN_TICK_BUDGET_SECS: float = C.SCAN_TICK_BUDGET_SECS

#: A repository with no open action is not re-walked more often than this (§1.13 tick step 1). One with
#: an open action is always re-walked: its evidence is what a human is waiting on.
REPO_SCAN_MIN_INTERVAL_SECS: float = 2.0

#: Statuses ``reconcile_action`` has something to say about. ``Queued``/``NotDelivered`` are the human's
#: to move, and the terminal four are done.
RECONCILABLE_STATUS: tuple[str, ...] = (
    "Delivering",
    "Delivered",
    "Processing",
    "DeliveryUncertain",
    "ReconciliationRequired",
)

#: Re-evaluated by ``startup()`` before any loop, lease sweep or scan runs (§1.13). These are the rows
#: that were mid-flight when the previous process stopped, so every later decision — a reclaim, a
#: derived card, a delivery report — must see Studio's re-reading of them first.
STARTUP_STATUS: tuple[str, ...] = ("Delivering", "Delivered", "Processing", "DeliveryUncertain")

#: Statuses that mean the host was handed something and has not answered yet.
IN_FLIGHT_STATUS: tuple[str, ...] = ("Delivering", "Delivered", "Processing")

#: ``PresenceBaseline`` fields compared when asking "did anything on disk move?". ``taken_at`` is
#: excluded on purpose: it is the clock of the read, not a fact about the record, so including it would
#: make every comparison false and ``NotDelivered`` unreachable. §1.12's "byte-for-byte" is read as
#: "every recorded fact identical" — see the module report.
PRESENCE_COMPARED_FIELDS: tuple[str, ...] = (
    "human_turn_events",
    "human_turn_events_partial",
    "human_turn_mtime_ns",
    "turn_counter",
    "shard_sizes",
    "audit_tail_sha256",
    "state_sha256",
)

#: How many new ``ERROR_LOGGED`` rows inside ``_ERROR_BURST_WINDOW_SECS`` count as a failure rather than
#: as a conductor mistake. FORMAT-NOTES §6 is explicit that single ``ERROR_LOGGED`` rows are routine
#: ("Unknown subcommand…"), so one row must never fail an action.
_ERROR_BURST_COUNT = 3
_ERROR_BURST_WINDOW_SECS = 600

#: Ordered failure classification (§1.13.1). First match wins, so the table is read top to bottom and
#: the order is part of the contract: a throttled ACP stream is ``rate_limit``, not ``transport``.
FINGERPRINT_RULES: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("rate_limit", re.compile(r"rate limit|429|throttl")),
    ("acp_timeout", re.compile(r"turn exceeded the .*ceiling|timeouterror|timed out")),
    ("transport", re.compile(r"stream closed|connection reset|econn")),
    ("guard", re.compile(r"refusing to")),
    ("validation", re.compile(r"unknown intent|ambiguous intent|invalid scope|cannot use")),
    ("permission", re.compile(r"eacces|permission denied")),
    ("dependency", re.compile(r"bun: command not found|cannot find module")),
)

#: Everything a fingerprint must forget so the same failure fingerprints the same twice: shell colour,
#: absolute paths, long hex ids, and any run of digits (pids, ports, byte counts, timestamps).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_PATH_RE = re.compile(r"(?:[a-z]:)?(?:/[^\s'\"]+)+")
_HEX_RE = re.compile(r"\b[0-9a-f]{8,}\b")
_NUM_RE = re.compile(r"\d+")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MAX_NORMALISED_CHARS = 400
_MAX_SLUG_CHARS = 48

#: Pending reasons that are not "wait longer" but "a human must look". They are also the finding codes,
#: because the reason a resolution was refused is exactly what the recovery card has to say.
_ANOMALY_REASONS = ("human_presence_anomaly", "cursor_moved")


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Resolution:
    """The reconciler's reading of one dispatched decision against the disk.

    ``outcome`` is deliberately four-valued. "Pending" is not a failure and not a resolution — it is the
    normal answer for most ticks, and collapsing it into either of the terminal answers is how a decision
    would be declared landed before the engine committed it.
    """

    outcome: str  # "state_changed" | "no_transition" | "pending" | "failed"
    reason: str
    evidence: dict[str, Any]

    @property
    def resolved(self) -> bool:
        return self.outcome in ("state_changed", "no_transition")

    def to_json(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "reason": self.reason, "evidence": dict(self.evidence)}


@dataclass(frozen=True, slots=True)
class StartupReport:
    """What the ordered startup pass found, published as the ``health.updated`` event.

    Carries ``within_budget`` because a startup that ran out of the host's hook window is not an error:
    the remaining work simply leads the first tick. A caller that could not tell the difference would
    either block the gateway or silently skip repositories.
    """

    boot_id: str
    started_at: str
    finished_at: str
    took_ms: int
    integrity: tuple[str, ...]
    payload_ok: bool | None
    payload_mismatches: int
    #: Transactions a previous process abandoned, closed out by this startup. Non-empty means a gateway
    #: died mid-install, so it belongs in the report a person can read rather than only in the log.
    transactions_settled: tuple[str, ...]
    actions_reevaluated: int
    boot_id_mismatches: int
    orphaned_leases: tuple[str, ...]
    repos_scanned: int
    repos_pending: int
    within_budget: bool
    issues: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "boot_id": self.boot_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "took_ms": self.took_ms,
            "integrity": list(self.integrity),
            "payload_ok": self.payload_ok,
            "payload_mismatches": self.payload_mismatches,
            "transactions_settled": list(self.transactions_settled),
            "actions_reevaluated": self.actions_reevaluated,
            "boot_id_mismatches": self.boot_id_mismatches,
            "orphaned_leases": list(self.orphaned_leases),
            "repos_scanned": self.repos_scanned,
            "repos_pending": self.repos_pending,
            "within_budget": self.within_budget,
            "issues": list(self.issues),
        }


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
    """Is every recorded presence fact identical between the submit-time baseline and a fresh read?

    The disk half of the ``NotDelivered`` proof (C24). ``False`` when either side is missing: a decision
    with no baseline cannot be proven undelivered from disk, and answering ``True`` there would hand the
    at-most-once guarantee to whichever caller forgot to record one.
    """
    if baseline is None or fresh is None:
        return False
    for name in PRESENCE_COMPARED_FIELDS:
        left = _attr(baseline, name)
        right = _attr(fresh, name)
        if isinstance(left, Mapping) or isinstance(right, Mapping):
            if dict(left or {}) != dict(right or {}):
                return False
            continue
        if left != right:
            return False
    return True


def _normalise_message(text: str) -> str:
    """Collapse a failure message to the part that identifies the failure and nothing else."""
    out = _ANSI_RE.sub("", str(text or "")).strip().lower()
    out = _PATH_RE.sub("<path>", out)
    out = _HEX_RE.sub("<id>", out)
    out = _NUM_RE.sub("<n>", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out[:_MAX_NORMALISED_CHARS]


def _slug_of(normalised: str) -> str:
    slug = _SLUG_RE.sub("-", normalised).strip("-")
    return slug[:_MAX_SLUG_CHARS].strip("-") or "unknown"


def _error_text(event: Any) -> str:
    """The human-meaningful half of an ``ERROR_LOGGED`` row.

    Field names are version-specific (FORMAT-NOTES §6), so the three that carry a message are tried in
    order and the event name is the last resort — a fingerprint over an empty string would merge every
    unlabelled failure into one breaker.
    """
    fields = _attr(event, "fields", {}) or {}
    for key in ("Error", "Message", "Details"):
        value = str(fields.get(key) or "").strip()
        if value:
            return value
    return str(_attr(event, "event", "") or "")


def _event_order(event: Any) -> tuple[int, int]:
    return int(_attr(event, "shard_index", 0) or 0), int(_attr(event, "pos", 0) or 0)


def _iso_gt(left: Any, right: Any) -> bool:
    """``left > right`` for two ISO-8601 UTC second-precision stamps; ``False`` when either is absent."""
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
    a = C.epoch_from_iso(since)
    b = C.epoch_from_iso(now)
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
        #: ``repo_id -> number of intent directories the last complete scan found``. What
        #: ``GET /health`` reports as ``counts.intents``: binding rows outlive a deleted intent on purpose
        #: (they hold its archive flag and session binding for when it comes back), so counting them
        #: would keep counting intents that are no longer there.
        self._intents_on_disk: dict[str, int] = {}
        self._running = False
        self._last_tick_at: str | None = None
        self._last_tick_ms: int | None = None
        self._repos_scanned = 0
        self._tasks: list[asyncio.Task] = []

    # ---- introspection ---------------------------------------------------- #

    def status(self) -> dict[str, Any]:
        """The ``reconciler`` block of ``GET /health`` (§2.1).

        Additive to §1.13's signature list: the health body requires these four numbers and no other
        component observes them. Reported to the orchestrator.
        """
        return {
            "running": self._running,
            "last_tick_at": self._last_tick_at,
            "last_tick_ms": self._last_tick_ms,
            "repos_scanned": self._repos_scanned,
        }

    def intents_on_disk(self) -> int:
        """Intent directories found by the last complete scan of every registered repository.

        The ``counts.intents`` of ``GET /health`` (§2.1). Zero until the first scan has run, a lower
        bound while a repository's listing is truncated, and unchanged while a repository is
        unavailable (nothing can be said about its intents until it is back).
        """
        return sum(self._intents_on_disk.values())

    # ---- startup ---------------------------------------------------------- #

    async def startup(self) -> StartupReport:
        """Ordered startup: integrity, payload, in-flight actions, lease sweep, first full scan.

        The order is the contract's and each step is a precondition of the next. Re-evaluating every
        in-flight action *before* the lease sweep is what stops a reclaim from being decided against a
        stale status; doing it before the first scan is what stops a derived card from being minted for a
        boundary a delivered decision has already closed.

        Bounded by ``HOST_STARTUP_HOOK_BUDGET_SECS``: the gateway detaches an app whose ``on_startup``
        overruns, so leftover work is left for the first tick rather than finished here.
        """
        clock = self._s.clock
        started_at = clock.iso()
        started = clock.monotonic()
        deadline = started + C.HOST_STARTUP_HOOK_BUDGET_SECS
        issues: list[str] = []

        integrity = tuple(await self._to_thread(self._s.storage.integrity_check))
        if integrity:
            issues.append("storage_integrity")

        payload_ok, mismatches = await self._verify_payload(issues)

        reevaluated, boot_mismatches = await self._startup_actions(deadline, issues)

        # Before the lease sweep, and in this order for a reason: a transaction abandoned by process
        # death holds an admin lease that `_prove_admin` refuses to reclaim until the transaction has
        # settled. Settling first is what makes that lease reclaimable in the same pass instead of one
        # restart later — and marks the repository `recovery_required` so a person is told the tree may
        # be half written rather than finding a repository that simply never runs again.
        settled: tuple[str, ...] = ()
        installer = getattr(self._s, "installer", None)
        if installer is not None:
            try:
                settled = tuple(await installer.settle_abandoned())
            except StudioError as exc:
                issues.append(f"settle_abandoned:{exc.code}")
        if settled:
            issues.append(f"transactions_settled:{len(settled)}")

        orphans: tuple[str, ...] = ()
        if clock.monotonic() < deadline:
            try:
                orphans = tuple(
                    str(_attr(rec, "resolved_repo_identity") or "")
                    for rec in await self._s.scheduler.startup_sweep()
                )
            except StudioError as exc:
                issues.append(f"lease_sweep:{exc.code}")
        else:
            issues.append("lease_sweep_deferred")

        scanned, pending = await self._startup_scan(deadline, issues)

        finished_at = clock.iso()
        report = StartupReport(
            boot_id=str(getattr(self._s, "boot_id", "") or ""),
            started_at=started_at,
            finished_at=finished_at,
            took_ms=max(0, int((clock.monotonic() - started) * 1000)),
            integrity=integrity,
            payload_ok=payload_ok,
            payload_mismatches=mismatches,
            transactions_settled=settled,
            actions_reevaluated=reevaluated,
            boot_id_mismatches=boot_mismatches,
            orphaned_leases=orphans,
            repos_scanned=scanned,
            repos_pending=pending,
            within_budget=clock.monotonic() <= deadline,
            issues=tuple(issues),
        )
        await self._record("health.startup", severity="info", params=report.to_json())
        await self._publish("health.updated", {"startup": report.to_json()})
        return report

    async def _verify_payload(self, issues: list[str]) -> tuple[bool | None, int]:
        """Verify the vendored payload, tolerating an installer that is not wired yet.

        ``Installer`` is a peer module; a Studio whose payload cannot be verified is *degraded*, not
        broken, so a missing collaborator degrades this one number instead of aborting the startup that
        every other module's recovery depends on.
        """
        installer = getattr(self._s, "installer", None)
        verify = getattr(installer, "verify_payload", None)
        if not callable(verify):
            issues.append("payload_unverified")
            return None, 0
        try:
            status = await self._to_thread(verify)
        except StudioError as exc:
            issues.append(f"payload:{exc.code}")
            return False, 0
        ok = bool(_attr(status, "ok", False))
        mismatches = len(tuple(_attr(status, "mismatches", ()) or ())) + len(
            tuple(_attr(status, "missing", ()) or ())
        )
        if not ok:
            issues.append("payload_degraded")
        return ok, mismatches

    async def _startup_actions(self, deadline: float, issues: list[str]) -> tuple[int, int]:
        """Re-read every row that was mid-flight when the previous process stopped.

        The ``boot_id`` stamp is written before the row is reconciled, not after: the four-proof
        ``NotDelivered`` path and ``resolve(mark_not_delivered)`` both read ``evidence.boot_id_unchanged``
        off the row, so a row reconciled before it was stamped could be declared undelivered on evidence
        this process cannot have (review R02).
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
        """Run the heartbeat loop and the tick loop as two independent tasks (review R11).

        Independent because they have opposite failure modes: a tick may legitimately take seconds of
        disk time, while a heartbeat that is late by that much makes a live lease look abandoned to the
        reclaim rules. Sharing one task would couple them.
        """
        self._running = True
        self._tasks = [
            asyncio.create_task(self.heartbeat_loop(), name="aidlc-studio-heartbeat"),
            asyncio.create_task(self.tick_loop(), name="aidlc-studio-tick"),
        ]
        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            raise
        finally:
            self._running = False
            for task in self._tasks:
                task.cancel()
            for task in self._tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            self._tasks = []

    async def heartbeat_loop(self) -> None:
        """Refresh every live execution lease whose action is still in flight. Never awaits a scan."""
        while True:
            try:
                await self.heartbeat_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive: a loop must not die
                _LOG.exception("reconciler heartbeat failed")
            await asyncio.sleep(C.LEASE_HEARTBEAT_SECS)

    async def tick_loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive
                _LOG.exception("reconciler tick failed")
            await asyncio.sleep(C.RECONCILE_INTERVAL_SECS)

    async def heartbeat_once(self) -> int:
        """One heartbeat pass; returns how many leases were refreshed.

        A lease whose action already settled is *not* heartbeaten: keeping it warm would hide it from the
        reclaim rules that are the only way it ever comes back.
        """
        refreshed = 0
        for rec in await self._s.scheduler.list():
            if _attr(rec, "kind") != "execution":
                continue
            action_id = _attr(rec, "action_id")
            if not action_id:
                continue
            row = await self._to_thread(self._s.storage.get, "actions", str(action_id))
            if row is None or row.get("status") not in IN_FLIGHT_STATUS:
                continue
            observed = self._observed_busy_state(row.get("slot_key"))
            try:
                await self._s.scheduler.heartbeat(
                    LeaseGrant.from_record(rec), observed_busy_state=observed
                )
                refreshed += 1
            except StaleGeneration:
                # Somebody proved this holder dead and took the lease; abandoning it is the contract.
                continue
        return refreshed

    def _observed_busy_state(self, slot_key: Any) -> str | None:
        if not slot_key:
            return None
        view = self._s.host.slot(str(slot_key))
        if view is None:
            return "slot_absent"
        return ",".join(_busy_reasons(view)) or "idle"

    async def tick(self) -> None:
        """One reconciliation pass over the whole registry (§1.13 steps 1-8).

        Scans come first so that the actions reconciled in step 2 are judged against evidence read in
        this tick, and so that the boundary a reclaim decision needs was computed from the same read.
        """
        clock = self._s.clock
        started = clock.monotonic()
        self._last_tick_at = clock.iso()

        await self._scan_pass(started)
        await self._reconcile_open_actions()
        await self._reclaim_pass()
        # The advisor pass sits exactly here. After steps 1-5 because the ``Queued`` set it drafts for is
        # the one this tick just derived and reconciled — a card a scan retired seconds ago must never
        # draw a spawn. Before retention because a draft this pass settles is still handed to
        # ``_expire_drafts`` in the same tick, so one that was already past its TTL is expired now rather
        # than sitting ``ready`` for five more seconds.
        await self._advisor_pass()
        await self._retention_pass()

        self._last_tick_ms = max(0, int((clock.monotonic() - started) * 1000))

    # ---- tick step 1: scan scheduling ------------------------------------- #

    async def _scan_pass(self, tick_started: float) -> int:
        """Scan repositories, open-action ones first, the rest round-robin inside the tick budget."""
        repos = await self._to_thread(self._s.repos.list)
        known = {r.repo_id for r in repos}
        # A repository that left the registry takes its per-repo memory with it; otherwise a removed
        # repository would keep counting toward ``counts.intents`` forever.
        for repo_id in [key for key in self._intents_on_disk if key not in known]:
            self._intents_on_disk.pop(repo_id, None)
        for key in [key for key in self._missing_since if key[0] not in known]:
            self._missing_since.pop(key, None)
        if not repos:
            return 0
        open_ids = await self._repos_with_open_actions()
        priority = [r for r in repos if r.repo_id in open_ids]
        rest = [r for r in repos if r.repo_id not in open_ids]
        if rest:
            cursor = self._cursor % len(rest)
            rest = rest[cursor:] + rest[:cursor]

        clock = self._s.clock
        inflight: set[asyncio.Task] = set()
        scanned = 0
        stopped_at: int | None = None

        for index, repo in enumerate(priority + rest):
            if clock.monotonic() - tick_started >= SCAN_TICK_BUDGET_SECS:
                stopped_at = index - len(priority)
                break
            if repo.repo_id not in open_ids and not self._due(repo.repo_id):
                continue
            inflight.add(asyncio.create_task(self._scan_bounded(repo)))
            scanned += 1
            if len(inflight) >= C.SCAN_CONCURRENCY:
                _, inflight = await asyncio.wait(inflight, return_when=asyncio.FIRST_COMPLETED)
        if inflight:
            await asyncio.wait(inflight)

        if rest:
            # Resume where the budget ran out, so the tail of a large registry is never starved.
            advanced = len(rest) if stopped_at is None else max(0, stopped_at)
            self._cursor = (self._cursor + advanced) % len(rest)
        self._repos_scanned = scanned
        return scanned

    def _due(self, repo_id: str) -> bool:
        last = self._scanned_at.get(repo_id)
        if last is None:
            return True
        return (self._s.clock.monotonic() - last) >= REPO_SCAN_MIN_INTERVAL_SECS

    async def _repos_with_open_actions(self) -> set[str]:
        rows = await self._to_thread(
            self._s.storage.select, "actions", {"status": list(C.LIVE_ACTION_STATUS)}
        )
        return {str(row.get("repo_id")) for row in rows if row.get("repo_id")}

    async def _scan_bounded(self, repo: "RepoRecord") -> RepoScan | None:
        """``scan_repo`` under the per-repo deadline; a slow repository never holds up the tick.

        The executor future is not cancellable, so a timeout marks the scan truncated and moves on: the
        worker finishes into a result nobody reads, which costs one pool slot for a moment and cannot
        corrupt anything (every scan writes through the same guarded store).
        """
        try:
            scan = await asyncio.wait_for(self.scan_repo(repo), timeout=C.REPO_SCAN_DEADLINE_SECS)
        except asyncio.TimeoutError:
            _LOG.warning("repo scan of %s exceeded %.1fs", repo.repo_id, C.REPO_SCAN_DEADLINE_SECS)
            self._scanned_at[repo.repo_id] = self._s.clock.monotonic()
            return None
        except (StudioError, OSError) as exc:
            _LOG.warning("repo scan of %s failed: %s", repo.repo_id, exc)
            self._scanned_at[repo.repo_id] = self._s.clock.monotonic()
            return None
        self._scanned_at[repo.repo_id] = self._s.clock.monotonic()
        return scan

    # ---- scanning --------------------------------------------------------- #

    async def scan_repo(self, repo: "RepoRecord") -> RepoScan:
        """One repository: availability and install health, then every intent's read model.

        Holds the scan semaphore for its whole body — including the intent loop — because the invariant
        the contract pins is "at most ``SCAN_CONCURRENCY`` concurrent ``scan_repo`` calls", and a
        semaphore held only around the individual disk reads would let 64 walks interleave.
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
        #: Every intent directory the listing found, whether or not its scan succeeded: an intent whose
        #: state file is mid-write is still on disk, and its cards must not be swept.
        present: set[tuple[str, str]] = set()
        if base.availability == "available":
            leases = await self._s.scheduler.list()
            root = Path(repo.canonical_path)
            spaces = await self._in_pool(self._s.reader.list_spaces, root)
            for space in spaces:
                dirs = await self._in_pool(self._s.reader.list_intent_dirs, root, space)
                if len(dirs) >= C.MAX_INTENTS_PER_REPO:
                    truncated = True
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

        if base.availability == "available":
            # A truncated listing is a lower bound, never proof of absence; the sweep needs the whole
            # set. The count is still recorded (the health number is a lower bound then too).
            self._intents_on_disk[repo.repo_id] = len(present)
            if truncated:
                self._forget_missing(repo.repo_id)
            else:
                await self._retire_vanished(repo.repo_id, present)
        else:
            # An unmounted or unreadable repository says nothing about its intents: every directory
            # "vanished" at once. Forget the timers so a remount does not retire cards on stale evidence.
            self._forget_missing(repo.repo_id)

        scan = RepoScan(
            repo_id=repo.repo_id,
            availability=base.availability,
            install=base.install,
            intents=tuple(summaries),
            findings=tuple(findings),
            took_ms=max(0, int((clock.monotonic() - started) * 1000)),
            truncated=truncated,
            scanned_at=clock.iso(),
        )
        return scan

    async def _retire_vanished(self, repo_id: str, present: set[tuple[str, str]]) -> None:
        """Step 1b of ``scan_repo``: retire the cards of intents whose directory left the disk.

        ``retire_stale`` runs per intent the scan *found*, so an intent that disappeared whole — a hand
        deletion, a branch switch, a space renamed — kept every Queued card it had, forever (the cards
        stayed in the queue, ``counts.intents`` kept counting it, and a click on one of them would have
        addressed a boundary that no longer exists). The sweep is graced by
        ``INTENT_MISSING_GRACE_SECS``: absence has to be observed for that long before it is believed,
        because a listing can be empty for a tick while a checkout is mid-flight, and a card cancelled
        on that evidence is a gate the human was about to answer. Command cards are left alone by
        ``retire_stale`` itself, and ``Failed`` cards are marked rather than moved (P07).
        """
        live = await self._s.actions.list_live(repo_id=repo_id, include_revision=True)
        gone: set[tuple[str, str]] = set()
        for rec in live:
            space = str(_attr(rec, "space") or "")
            intent_dir = str(_attr(rec, "intent_dir") or "")
            evidence = _attr(rec, "evidence") or {}
            # A ``Failed`` card already marked superseded stays queue-visible until its own condition
            # clears it (P07); it is not a reason to keep sweeping the intent every tick.
            if str(_attr(rec, "status") or "") == "Failed" and evidence.get("superseded"):
                continue
            if intent_dir and (space, intent_dir) not in present:
                gone.add((space, intent_dir))
        # An intent that is back, or whose cards are all settled, is no longer missing.
        for key in [key for key in self._missing_since if key[0] == repo_id]:
            if (key[1], key[2]) not in gone:
                self._missing_since.pop(key, None)
        now = self._s.clock.monotonic()
        for space, intent_dir in sorted(gone):
            key = (repo_id, space, intent_dir)
            first = self._missing_since.setdefault(key, now)
            if now - first < C.INTENT_MISSING_GRACE_SECS:
                continue
            await self._s.actions.retire_stale(
                repo_id, space, intent_dir, set(), reason="intent_missing"
            )
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
        # Only retire requests that existed before this snapshot was taken. A new Run created
        # after the workflow advanced must not be judged against an earlier checkpoint.
        live = await self._s.actions.list_live(
            repo_id=repo.repo_id, space=space, intent_dir=intent_dir, include_revision=True
        )
        snap = await self._in_pool(
            functools.partial(
                self._s.projection.snapshot, repo, space, intent_dir, first_unreadable_at=remembered
            )
        )
        first_unreadable = self._track_unreadable(key, snap, remembered)

        binding = await self._s.sessions.get(repo.repo_id, space, intent_dir)
        slot = self._s.host.slot(binding.slot_key) if binding.slot_key else None
        for command in live:
            await self._retire_obsolete_run(command, snap)
        live = await self._s.actions.list_live(
            repo_id=repo.repo_id, space=space, intent_dir=intent_dir, include_revision=True
        )
        breakers = await self._breakers_for(repo.repo_id, snap.intent_key)
        facts = RepoFacts(
            now=self._s.clock.iso(),
            repo=repo,
            install=install,
            binding=binding,
            slot=slot,
            live_actions=live,
            leases=leases,
            first_unreadable_at=first_unreadable,
            layout=snap.layout,
        )
        findings = await self._in_pool(self._s.consistency.evaluate_intent, snap, facts)

        seeds = await self._in_pool(
            functools.partial(
                self._s.projection.derive_cards,
                snap,
                findings=findings,
                binding=binding,
                install=install,
                breakers=breakers,
                live_actions=live,
                slot=slot,
                host_card=self._host_card(binding.slot_key),
            )
        )
        live = await self._s.actions.upsert_derived(seeds)
        await self._s.actions.retire_stale(
            repo.repo_id, space, intent_dir, {seed.dedupe_key for seed in seeds if seed.dedupe_key}
        )

        boundary = await self._record_boundary(repo, snap, binding, live, slot, leases)
        binding = await self._s.sessions.get(repo.repo_id, space, intent_dir)

        summary = await self._in_pool(
            functools.partial(
                self._s.projection.summarize,
                snap,
                binding=binding,
                live_actions=[a for a in live if _attr(a, "type") != "revision"],
                breaker_open=any(_attr(b, "opened_at") for b in breakers),
                host=await self._s.sessions.session_ref(binding),
                findings=findings,
                slot=slot,
            )
        )
        await self._publish_intent(key, summary, boundary)
        return summary, list(findings)

    def _track_unreadable(self, key: str, snap: Any, remembered: str | None) -> str | None:
        """Remember when an intent's state file first went unreadable; forget it when it reads again.

        ``state_unreadable`` is ``info`` inside its grace window and ``blocking`` after it (review
        P26.2), and only this component has seen the earlier ticks that make the distinction possible.
        """
        if _attr(snap, "state") is None:
            since = remembered or _attr(snap, "taken_at") or self._s.clock.iso()
            self._first_unreadable[key] = since
            return since
        self._first_unreadable.pop(key, None)
        return None

    def _host_card(self, slot_key: str | None) -> dict | None:
        if not slot_key:
            return None
        cards = self._s.host.pending_question_cards(str(slot_key))
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
        """Evaluate PRD §12.2 and persist it when it changed.

        Computed here rather than in a separate tick step because the five conditions need the snapshot
        this scan already took; a second read would answer about a different moment, which is exactly
        what a "stable" boundary must not do.
        """
        boundary = self._s.projection.stable_boundary(
            snap,
            live_actions=live,
            busy_reasons=_busy_reasons(slot),
            lease_active=self._execution_lease_warm(repo, leases),
        )
        # An unchanged boundary keeps the time it was FIRST seen. Without this, `recorded_at` is the
        # time of this look, so both dedupes downstream compared two values that could never be equal:
        # the binder wrote an fsync'd `intent_bindings` row and `_publish_intent` emitted a frame once per
        # intent per tick, on an app where nothing had happened. `recorded_at` also becomes a more useful
        # answer — "stable since", rather than "we checked just now", which the tick rate already implies.
        previous = _attr(binding, "last_stable_boundary")
        boundary = _carry_recorded_at(previous, boundary)
        if _json_of(previous) != _json_of(boundary):
            await self._s.sessions.record_stable_boundary(
                repo.repo_id, snap.space, snap.intent_dir, boundary
            )
        return boundary

    def _execution_lease_warm(
        self, repo: "RepoRecord", leases: Sequence["LeaseRecord"]
    ) -> bool:
        """Is an execution lease for this repository still heartbeating?

        Two heartbeat intervals, per §1.13: one missed beat is a busy worker thread, two is a holder
        that stopped reporting — and only the second is a reason to keep calling the boundary unstable.
        """
        now = self._s.clock.iso()
        for lease in leases:
            if _attr(lease, "kind") != "execution":
                continue
            if _attr(lease, "repo_id") != repo.repo_id and (
                not repo.resolved_identity
                or _attr(lease, "resolved_repo_identity") != repo.resolved_identity
            ):
                continue
            age = _elapsed(_attr(lease, "heartbeat_at"), now)
            if age is None or age <= C.LEASE_HEARTBEAT_SECS * 2:
                return True
        return False

    async def _publish_intent(self, key: str, summary: Any, boundary: Any) -> None:
        payload = _json_of(summary)
        if self._published.get(key) == payload:
            return
        self._published[key] = payload
        await self._publish(
            "intent.updated",
            {
                "repo_id": _attr(summary, "repo_id"),
                "space": _attr(summary, "space"),
                "intent_dir": _attr(summary, "intent_dir"),
                "intent_key": _attr(summary, "intent_key"),
                "operational_state": _attr(summary, "operational_state"),
                "open_actions": _attr(summary, "open_actions"),
                "stable_boundary": _json_of(boundary),
            },
        )

    # ---- tick step 2/3: open actions -------------------------------------- #

    async def _reconcile_open_actions(self) -> int:
        """Reconcile every action the host might still be working on, one snapshot per intent."""
        rows = await self._to_thread(
            self._s.storage.select,
            "actions",
            {"status": list(RECONCILABLE_STATUS)},
            order_by="created_at ASC",
        )
        done = 0
        snapshots: dict[str, Any] = {}
        for row in rows:
            action_id = str(row.get("action_id"))
            try:
                rec = await self._s.actions.get(action_id)
            except StudioError:
                continue
            key = f"{rec.repo_id}:{rec.space}:{rec.intent_dir}"
            if key not in snapshots:
                snapshots[key] = await self._snapshot_for(rec)
            try:
                await self.reconcile_action(rec, snapshots[key])
                done += 1
            except (StudioError, OSError) as exc:
                _LOG.warning("reconcile of %s failed: %s", action_id, exc)
        return done

    async def _snapshot_for(self, rec: Any) -> Any | None:
        try:
            repo = await self._to_thread(self._s.repos.get, rec.repo_id)
        except StudioError:
            return None
        key = f"{rec.repo_id}:{rec.space}:{rec.intent_dir}"
        try:
            return await self._in_pool(
                functools.partial(
                    self._s.projection.snapshot,
                    repo,
                    rec.space,
                    rec.intent_dir,
                    first_unreadable_at=self._first_unreadable.get(key),
                )
            )
        except (StudioError, OSError):
            return None

    async def reconcile_now(self, action_id: str) -> Any:
        """``POST /actions/{id}/reconcile`` — one action, right now, on the caller's request."""
        rec = await self._s.actions.get(action_id)
        return await self.reconcile_action(rec, await self._snapshot_for(rec))

    async def reconcile_action(self, rec: Any, snap: Any | None = None) -> Any:
        """Move one action as far as today's evidence allows, and no further.

        Dispatches on status because each status has exactly one legal question. A never-sent
        Run/Resume may retire when its purpose expires; no terminal or undelivered record is reopened.
        """
        status = str(_attr(rec, "status") or "")
        if status == "Queued" and _attr(rec, "type") in ("run", "resume"):
            return await self._retire_obsolete_run(
                rec, snap if snap is not None else await self._snapshot_for(rec),
            )
        if status not in RECONCILABLE_STATUS:
            return rec
        if snap is None and status != "Delivering":
            snap = await self._snapshot_for(rec)

        slot = self._slot_for(rec)
        self._observe_run(rec, slot)

        if status == "Delivering":
            return await self._reconcile_delivering(rec, snap, slot)
        if status == "Delivered":
            return await self._reconcile_delivered(rec, snap, slot)
        if status == "Processing":
            return await self._reconcile_processing(rec, snap, slot)
        if status == "DeliveryUncertain":
            return await self._reconcile_uncertain(rec, snap, slot)
        return await self._reconcile_required(rec, snap, slot)

    # ---- per-status bodies ------------------------------------------------ #

    async def _reconcile_delivering(self, rec: Any, snap: Any, slot: Any) -> Any:
        """The browser never reported. After the ack deadline the answer becomes "we cannot tell".

        Never ``NotDelivered``: this path has no authoritative 4xx and no client report, so the only
        thing that is certain is the uncertainty (§1.13 "The reconciler NEVER moves Delivering →
        NotDelivered").
        """
        if not self._past_deadline(rec):
            return rec
        if snap is None:
            snap = await self._snapshot_for(rec)
        evidence = await self._delivery_evidence(rec, snap, slot)
        return await self._transition(
            rec, "DeliveryUncertain", reason="ack_deadline", evidence=evidence
        )

    async def _reconcile_delivered(self, rec: Any, snap: Any, slot: Any) -> Any:
        """A delivered decision either starts its turn or runs out of patience (review P22)."""
        evidence = dict(_attr(rec, "evidence", {}) or {})
        queued_at = evidence.get("queued_at")
        delivered_at = _attr(rec, "delivered_at")
        timeout = self._turn_timeout()

        if queued_at:
            if self._turn_started(rec, slot, snap):
                deadline = self._deadline_from(self._s.clock.iso(), timeout)
                return await self._transition(
                    rec,
                    "Processing",
                    reason="queued_turn_started",
                    evidence={"turn_started_at": self._s.clock.iso()},
                    values={"deadline_at": deadline},
                )
            waited = _elapsed(delivered_at, self._s.clock.iso())
            if waited is not None and waited > C.DELIVERED_QUEUED_WAIT_FACTOR * timeout:
                return await self._to_uncertain(rec, snap, slot, reason="queued_wait_exceeded")
            return rec

        if self._session_replaced(rec, slot):
            return await self._to_uncertain(rec, snap, slot, reason="session_replaced")
        if self._past_deadline(rec):
            return await self._to_uncertain(rec, snap, slot, reason="delivery_deadline")
        return rec

    async def _retire_obsolete_run(self, rec: Any, snap: Any | None) -> Any:
        """A never-sent Run may expire; an in-flight or uncertain delivery never does."""
        if (
            snap is None or _attr(rec, "type") not in ("run", "resume")
            or _attr(rec, "status") != "Queued"
            or _attr(rec, "delivery_id") or _attr(rec, "delivering_at")
        ):
            return rec
        reason = run_unavailable_reason(snap, stage=_attr(rec, "stage"), unit=_attr(rec, "unit"))
        if reason is None:
            return rec
        try:
            return await self._s.actions.transition(
                rec.action_id, "Cancelled", expected_generation=rec.status_generation,
                reason="command_superseded", evidence={
                    "superseded": True, "command_unavailable_reason": reason, "current_stage": snap.stage,
                },
            )
        except StaleGeneration:
            # A concurrent submit owns the new generation. Never adopt it to cancel its delivery.
            return await self._s.actions.get(rec.action_id)

    async def _reconcile_processing(self, rec: Any, snap: Any, slot: Any) -> Any:
        """The turn is running (or was). Look for the resolution the decision declared.

        An anomaly leaves through ``DeliveryUncertain`` rather than straight to
        ``ReconciliationRequired``: ``Processing`` has no edge to it in PRD §11.1, and the next tick
        escalates with the finding carried on the row (C04). Inventing the shortcut would put a state
        outside the transition graph into the durable record.
        """
        if snap is not None:
            resolution = self.evaluate_resolution(rec, snap, slot)
            if resolution.resolved:
                return await self._settle(rec, snap, resolution)
            if resolution.reason in _ANOMALY_REASONS:
                return await self._to_uncertain(
                    rec, snap, slot, reason=resolution.reason, extra=resolution.evidence
                )
            # Tool discovery and recoverable retries can emit several audit errors during one turn.
            # Keep the lease while the agent is working; its final answer evidence gets first refusal.
            failure = self._classified_failure(rec, snap) if self._turn_ended(rec, slot, snap) else None
            if failure is not None:
                return await self._fail(rec, snap, failure)
        if self._session_replaced(rec, slot):
            return await self._to_uncertain(rec, snap, slot, reason="session_replaced")
        if self._past_deadline(rec):
            return await self._to_uncertain(rec, snap, slot, reason="turn_deadline")
        return rec

    async def _reconcile_uncertain(self, rec: Any, snap: Any, slot: Any) -> Any:
        """``DeliveryUncertain`` always continues to ``ReconciliationRequired`` (C04/review R07).

        A status with no reconciler-driven exit is a dead end, and a dead end in this graph means a
        boundary nobody can act on. The evidence gathered here is what the human's ``resubmit`` /
        ``mark_not_delivered`` decision is checked against, so it is recomputed rather than reused.
        """
        evidence = await self._delivery_evidence(rec, snap, slot)
        previous = dict(_attr(rec, "evidence", {}) or {})
        confirmed = bool(previous.get("delivery_confirmed")) or self._delivery_confirmed(evidence)
        evidence["delivery_confirmed"] = confirmed
        return await self._transition(
            rec, "ReconciliationRequired", reason="uncertain_escalated", evidence=evidence
        )

    async def _reconcile_required(self, rec: Any, snap: Any, slot: Any) -> Any:
        """Resolve from disk when delivery is confirmed; otherwise prove absence or wait for a human."""
        evidence = await self._delivery_evidence(rec, snap, slot)
        previous = dict(_attr(rec, "evidence", {}) or {})
        confirmed = bool(previous.get("delivery_confirmed")) or self._delivery_confirmed(evidence)
        evidence["delivery_confirmed"] = confirmed

        if confirmed:
            if snap is not None:
                resolution = self.evaluate_resolution(rec, snap, slot)
                if resolution.resolved:
                    await self._stamp_evidence(rec, evidence)
                    rec = await self._s.actions.get(rec.action_id)
                    return await self._settle(rec, snap, resolution)
                evidence = self._carry_findings(evidence, resolution)
                failure = self._classified_failure(rec, snap) if self._turn_ended(rec, slot, snap) else None
                if failure is not None:
                    await self._stamp_evidence(rec, evidence)
                    rec = await self._s.actions.get(rec.action_id)
                    return await self._fail(rec, snap, failure)
            return await self._stamp_evidence(rec, evidence)

        if evidence.get("wrong_slot"):
            evidence["findings"] = self._with_finding(
                evidence.get("findings"), "wrong_slot", {"slot_key": evidence["wrong_slot"]}
            )
            rec = await self._stamp_evidence(rec, evidence)
            await self._record(
                "action.updated",
                severity="critical",
                rec=rec,
                params={"finding": "wrong_slot", "slot_key": evidence["wrong_slot"]},
            )
            return rec

        if (
            evidence.get("transcript_row_absent") is True
            and evidence.get("slot_ran_since") is False
            and evidence.get("disk_baseline_unchanged") is True
            and evidence.get("boot_id_unchanged") is True
        ):
            return await self._transition(
                rec, "NotDelivered", reason="proven_not_delivered", evidence=evidence
            )
        return await self._stamp_evidence(rec, evidence)

    # ---- resolution contracts (§1.12 table) ------------------------------- #

    def evaluate_resolution(self, rec: Any, snap: Any, slot: Any = None) -> Resolution:
        """Apply the resolution-contract row for this action's decision, plus presence and cursor.

        Public so a test can assert one table row against fixture bytes without driving a whole tick.
        The presence and cursor rules are applied *after* the row matches, because they are what turns a
        matched row into a resolution Studio is willing to sign: the disk may have moved for a reason
        that has nothing to do with the decision Studio sent.
        """
        decision = self._decision_of(rec)
        row = self._contract_row(rec, snap, slot, decision)
        if not row.resolved:
            return row

        evidence = dict(row.evidence)
        presence = self._presence(rec, snap)
        evidence["presence"] = presence
        evidence["is_active"] = _attr(snap, "is_active")
        # Both rules are scoped to the lane that sent a prompt. `host_control` (force stop) puts nothing
        # in front of the model and never switched the cursor, so requiring a human turn or a matching
        # cursor there would refuse a stop that provably worked — see the module report.
        human_lane = str(_attr(rec, "risk_class") or "") == "human_lane"

        if human_lane and presence.get("ok") is not True:
            return Resolution(
                "pending",
                "human_presence_anomaly",
                {
                    **evidence,
                    "findings": self._with_finding(
                        evidence.get("findings"), "human_presence_anomaly", presence
                    ),
                },
            )
        if human_lane and _attr(snap, "is_active") is False:
            return Resolution(
                "pending",
                "cursor_moved",
                {
                    **evidence,
                    "findings": self._with_finding(
                        evidence.get("findings"),
                        "cursor_moved",
                        {"cursor": _attr(snap, "cursor"), "intent_dir": _attr(snap, "intent_dir")},
                    ),
                },
            )
        return Resolution(row.outcome, row.reason, evidence)

    def _contract_row(self, rec: Any, snap: Any, slot: Any, decision: str) -> Resolution:
        """The type/decision row of the §1.12 table, with no presence or cursor judgement yet."""
        action_type = str(_attr(rec, "type") or "")
        stage = _attr(rec, "stage")
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
            seen["gate_rejected"] = _json_of(rejected)
            seen["stage_revising"] = _json_of(revising)
            if rejected is not None and revising is not None and self._row_mark(snap, stage) in ("R", "r"):
                return Resolution("state_changed", "gate_rejected", seen)
            if ended and rejected is not None and revising is not None:
                returned = self._returned_revision_gate(rec, snap, stage, rejected, revising)
                if returned is not None:
                    return Resolution("state_changed", "gate_revision_returned", {
                        **seen, "returned_gate": _json_of(returned),
                    })
            return Resolution("pending", "no_gate_rejected", seen)

        if action_type == "question":
            return self._question_row(rec, snap, decision, stage, ended, seen)

        if action_type == "missing_input" and decision == "provide_input":
            payload = dict(_attr(rec, "payload", {}) or {})
            if payload.get("kind") == "scope":
                current = self._scope_of(snap)
                seen["scope"] = current
                if current and current == str(payload.get("scope") or "").strip():
                    return Resolution("state_changed", "scope_recorded", seen)
                return Resolution("pending", "scope_unchanged", seen)
            return (
                Resolution("no_transition", "turn_ended", seen)
                if ended
                else Resolution("pending", "turn_running", seen)
            )

        if action_type in ("run", "resume"):
            moved = self._state_moved(rec, snap)
            movement = self._newest_new(rec, snap, C.MOVEMENT_EVENTS, None)
            seen["state_moved"] = moved
            seen["movement_event"] = _json_of(movement)
            if not ended:
                return Resolution("pending", "turn_running", seen)
            if moved or movement is not None:
                return Resolution("state_changed", "workflow_moved", seen)
            return Resolution("no_transition", "turn_ended_without_movement", seen)

        if action_type == "prepare_commit":
            return (
                Resolution("no_transition", "turn_ended", seen)
                if ended
                else Resolution("pending", "turn_running", seen)
            )

        if action_type == "force_stop":
            if slot is None:
                # The slot is gone, so nothing is running in it. `set_interrupted` still fires: the
                # user asked for a stop and the durable record must say it took effect (C28).
                return Resolution("no_transition", "slot_absent", seen)
            idle = not _attr(slot, "running", False) and str(_attr(slot, "stop_state", "idle")) == "idle"
            seen["slot_idle"] = idle
            return (
                Resolution("no_transition", "slot_idle", seen)
                if idle
                else Resolution("pending", "stop_in_progress", seen)
            )

        return Resolution("pending", "no_contract_row", seen)

    def _question_row(
        self, rec: Any, snap: Any, decision: str, stage: Any, ended: bool, seen: dict[str, Any]
    ) -> Resolution:
        """The four question rows. All of them are ``ResolvedNoTransition``: answering a question moves
        the conversation on, it does not close a gate, so declaring ``StateChanged`` would claim a
        lifecycle move AI-DLC did not make."""
        questions = _attr(snap, "questions")
        digest_changed = self._digest_changed(rec, snap)
        seen["question_digest_changed"] = digest_changed
        if not ended:
            return Resolution("pending", "turn_running", seen)

        if decision == "answers":
            origin = (dict(_attr(rec, "evidence", {}) or {}).get("questions") or {}).get("origin")
            if isinstance(origin, dict) and origin.get("kind") == "audit":
                if origin.get("unit") is not None and (
                    _attr(snap, "unit") != origin["unit"]
                    or _attr(snap, "audit_question_attempt") != (
                        origin.get("attempt_generation"), True
                    )
                ):
                    return Resolution("pending", "question_attempt_changed", seen)
                answered = audit_question_answer(
                    _attr(snap, "audit"), origin,
                    str(_attr(rec, "wire_text") or ""), str(_attr(rec, "delivering_at") or ""),
                    project_dir=_attr(snap, "canonical_path"),
                )
                seen["question_answered"] = _json_of(answered)
                # Older Studio versions sent a free-text marker as an enum label. An idle,
                # proven human turn cannot answer that prompt without a note. Retire only this
                # attempt; the unchanged audit decision remains pending and gets a fresh card.
                live_origin = _attr(questions, "origin") or {}
                payload = dict(_attr(rec, "payload", {}) or {})
                supplied = payload.get("answers") or []
                if (
                    answered is None and audit_question_accepts_text(questions)
                    and live_origin.get("decision_sha256") == origin.get("decision_sha256")
                    and len(supplied) == 1 and isinstance(supplied[0], dict)
                    and not str(supplied[0].get("free_text") or "").strip()
                    and _attr(rec, "wire_text") == questions.questions[0].options[0].text
                ):
                    return Resolution("no_transition", "answer_requires_text", seen)
                if (
                    answered is None and self._current_stage(snap) == stage
                    and self._row_mark(snap, stage) == "?"
                ):
                    gate = self._newest_new(rec, snap, ("STAGE_AWAITING_APPROVAL",), stage)
                    gate_fields = _attr(gate, "fields", {}) or {}
                    if (
                        gate is not None and not gate_fields.get("Workflow")
                        and (gate_fields.get("Unit") or None) == origin.get("unit")
                    ):
                        for candidate in self._new_events(rec, snap, ("QUESTION_ANSWERED",), stage):
                            recorded_text = (_attr(candidate, "fields", {}) or {}).get("Details")
                            if not isinstance(recorded_text, str) or not recorded_text:
                                continue
                            scoped_receipt = audit_question_answer(
                                _attr(snap, "audit"), origin, recorded_text,
                                str(_attr(rec, "delivering_at") or ""),
                                project_dir=_attr(snap, "canonical_path"),
                            )
                            if scoped_receipt is candidate:
                                # The receipt belongs to this question but does not verify our reply.
                                # The ended turn and later gate permit retiring its lease, never replay
                                # or an "answered" claim. Presence/cursor checks still run above.
                                return Resolution("no_transition", "answer_not_verified_at_gate", {
                                    **seen, "answer_verified": False,
                                    "recorded_answer": _json_of(candidate),
                                    "next_gate": _json_of(gate),
                                })
                return Resolution(
                    "no_transition" if answered is not None else "pending",
                    "question_answered" if answered is not None else "answers_not_recorded",
                    seen,
                )
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
            confirmation = _attr(questions, "summary_confirmation")
            if recorded is not None:
                return Resolution("no_transition", "summary_confirmed", seen)
            if digest_changed and bool(_attr(confirmation, "answered", False)):
                return Resolution("no_transition", "summary_confirmed", seen)
            return Resolution("pending", "summary_not_confirmed", seen)

        if decision == "approve_plan":
            plan = _attr(questions, "plan_approval")
            answer = str(_attr(plan, "answer", "") or "")
            seen["plan_answer"] = answer
            if digest_changed and _attr(plan, "answered", False) and answer.startswith(C.WIRE_APPROVE_PLAN):
                return Resolution("no_transition", "plan_approved", seen)
            # A later Learnings prompt can now own `snap.questions`. The reader retains the
            # file and its validated closure receipts from that same coherent snapshot; match
            # the original plan's path, scope and dispatch boundary before settling its action.
            closed = _attr(questions, "closed_file")
            origin = _attr(questions, "origin") or {}
            captured_question = (_attr(rec, "evidence", {}) or {}).get("questions") or {}
            if (
                closed is not None and origin.get("kind") == "audit"
                and origin.get("stage") == stage and origin.get("unit") == _attr(rec, "unit")
                and _attr(closed, "relpath") == captured_question.get("relpath")
                and _attr(closed, "sha256") != _attr(_attr(rec, "captured"), "question_digest")
            ):
                new_receipts = self._new_events(rec, snap, ("PLAN_APPROVAL_RECORDED",), stage)
                receipt = next((
                    event for event in (_attr(questions, "closed_file_receipts", ()) or ())
                    if event in new_receipts
                    and event.fields.get("Prompt SHA-256") in (
                        None, _attr(_attr(rec, "captured"), "question_digest"),
                    )
                    and event.fields.get("Intent") in (None, _attr(rec, "intent_uuid"))
                ), None)
                if receipt is not None:
                    seen["plan_approval_recorded"] = _json_of(receipt)
                    seen["approved_questions_file"] = _attr(closed, "relpath")
                    return Resolution("no_transition", "plan_approved", seen)
            if _attr(plan, "present", False) and not _attr(plan, "answered", False):
                receipt = self._historical_plan_approval(rec, snap, stage, questions)
                if receipt is not None:
                    return Resolution("no_transition", "plan_approval_recorded_before_reset", {
                        **seen, "plan_approval_recorded": _json_of(receipt),
                        "current_plan_requires_approval": True,
                    })
            return Resolution("pending", "plan_not_approved", seen)

        if decision == "request_plan_changes":
            plan = _attr(questions, "plan_approval")
            answer = str(_attr(plan, "answer", "") or "").strip()
            seen["plan_answer"] = answer
            holds_request = answer.lower().startswith("request changes")
            cleared = bool(_attr(plan, "present", False)) and not bool(_attr(plan, "answered", False))
            seen["plan_cleared"] = cleared
            if digest_changed and (holds_request or cleared):
                return Resolution("no_transition", "plan_changes_requested", seen)
            return Resolution("pending", "plan_unchanged", seen)

        return Resolution("pending", "no_contract_row", seen)

    # ---- resolution outcome ---------------------------------------------- #

    async def _settle(self, rec: Any, snap: Any, resolution: Resolution) -> Any:
        """Commit a resolution: transition, release the lease, force the card's retirement."""
        to_status = "StateChanged" if resolution.outcome == "state_changed" else "ResolvedNoTransition"
        # `resolved_at` and `resolution_json` are composed by the broker's own terminal write from the
        # reason and evidence handed over here (§1.12); writing them from this side would give one record
        # two authors.
        rec = await self._transition(
            rec, to_status, reason=resolution.reason, evidence=resolution.evidence
        )
        await self._release_lease(rec)
        if str(_attr(rec, "type") or "") == "force_stop":
            await self._settle_force_stop(rec)
        self._forget(rec)
        return rec

    async def _settle_force_stop(self, rec: Any) -> None:
        """A resolved force stop is a durable interruption (C28/review P17).

        ``interrupted_at`` is written here and cleared only by the recovery card's ``acknowledge``, so
        ``Interrupted → ReconciliationRequired → Queued|Paused`` stays the only way back: PRD §11.2
        requires manual takeover after a mid-stage interruption.
        """
        await self._s.sessions.set_interrupted(
            rec.repo_id, rec.space, rec.intent_dir, self._s.clock.iso()
        )
        await self._record(
            "intent.force_stop",
            severity="attention",
            rec=rec,
            params={"slot_key": _attr(rec, "slot_key")},
        )

    async def _fail(self, rec: Any, snap: Any, resolution: Resolution) -> Any:
        fingerprint, cls, summary_key = self.classify_failure(
            host_error=resolution.evidence.get("host_error"),
            audit_errors=list(resolution.evidence.get("audit_error_events") or ()),
        )
        try:
            rec, opened = await self._s.actions.record_failure(
                rec,
                fingerprint=fingerprint,
                fingerprint_class=cls,
                summary=summary_key,
                # The parsed `AuditEvent` objects stay inside this module: everything that crosses into
                # the store must be JSON, and their `to_json()` form is already in `audit_errors`.
                evidence={k: v for k, v in resolution.evidence.items() if k != "audit_error_events"},
            )
        except (IllegalTransition, StaleGeneration) as exc:
            # The row moved between the read and the verdict (a delivery report, a second pass). Failing
            # it anyway would count a breaker strike against a decision that has already settled.
            _LOG.debug("failure for %s skipped: %s", _attr(rec, "action_id"), exc)
            return await self._s.actions.get(_attr(rec, "action_id"))
        await self._release_lease(rec)
        self._forget(rec)
        if opened:
            _LOG.info("breaker opened for %s (%s)", _attr(rec, "intent_dir"), fingerprint)
        return rec

    async def _to_uncertain(
        self, rec: Any, snap: Any, slot: Any, *, reason: str, extra: Mapping[str, Any] | None = None
    ) -> Any:
        evidence = await self._delivery_evidence(rec, snap, slot)
        if extra:
            evidence = self._carry_findings({**evidence, **dict(extra)}, None)
            evidence["findings"] = self._merge_findings(
                evidence.get("findings"), dict(extra).get("findings")
            )
        moved = await self._transition(rec, "DeliveryUncertain", reason=reason, evidence=evidence)
        if reason in _ANOMALY_REASONS:
            await self._record(
                "action.updated",
                severity="critical",
                rec=moved,
                params={"finding": reason, "presence": evidence.get("disk_movement")},
            )
        return moved

    def _carry_findings(
        self, evidence: dict[str, Any], resolution: "Resolution | None"
    ) -> dict[str, Any]:
        """Fold a pending resolution's findings into the evidence blob that is about to be written.

        Findings the reconciler discovers (`human_presence_anomaly`, `cursor_moved`, `wrong_slot`) live on
        the action row because that is where `ConsistencyEngine` lifts them back out into the blocking set
        (review P04/R01/R08). Losing them on a status move would silently unblock the intent.
        """
        if resolution is not None:
            evidence["findings"] = self._merge_findings(
                evidence.get("findings"), resolution.evidence.get("findings")
            )
        return evidence

    @staticmethod
    def _merge_findings(left: Any, right: Any) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for group in (left or [], right or []):
            for item in group:
                if not isinstance(item, Mapping):
                    continue
                code = str(item.get("code") or "")
                if not code or code in seen:
                    continue
                seen.add(code)
                out.append({"code": code, "params": dict(item.get("params") or {})})
        return out

    # ---- failure classification ------------------------------------------ #

    def classify_failure(
        self, *, host_error: str | None, audit_errors: Sequence[Any]
    ) -> tuple[str, str, str]:
        """``(fingerprint, fingerprint_class, summary_key)`` — deterministic for the same failure.

        One message is chosen rather than the whole burst: three identical ``ERROR_LOGGED`` rows and one
        are the same failure, and a fingerprint that changed with the row count would never reach the
        breaker's transient threshold. The host's own error wins when there is one — it describes the
        transport, which is the layer that actually failed.
        """
        message = str(host_error or "").strip()
        if not message and audit_errors:
            newest = max(audit_errors, key=lambda e: _attr(e, "sort_key", ("", 0, 0)))
            message = _error_text(newest)
        # Classified against the readable text and fingerprinted against the scrubbed one: the class
        # patterns include literal numbers (`429`) and paths, which the fingerprint has to forget so the
        # same failure fingerprints the same twice.
        readable = re.sub(r"\s+", " ", str(message or "")).strip().lower()
        normalised = _normalise_message(message)
        cls = "config"
        for name, pattern in FINGERPRINT_RULES:
            if pattern.search(readable):
                cls = name
                break
        digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:6]
        return f"{cls}.{_slug_of(normalised)}:{digest}", cls, f"template.failure.{cls}"

    def _classified_failure(self, rec: Any, snap: Any) -> Resolution | None:
        """A burst of new ``ERROR_LOGGED`` rows, or a host error on the receipt, means failure.

        The burst threshold is the point: FORMAT-NOTES §6 records that single ``ERROR_LOGGED`` rows are
        the conductor correcting itself ("Unknown subcommand: set-status"), so failing an action on one
        would turn AI-DLC's normal self-correction into a breaker.
        """
        receipt = dict(_attr(rec, "evidence", {}) or {}).get("receipt") or {}
        host_error = receipt.get("error") if isinstance(receipt, Mapping) else None
        errors = [
            event
            for event in self._new_events(rec, snap, (C.ERROR_EVENT,), None)
            if self._within_burst_window(rec, event)
        ]
        if not host_error and len(errors) < _ERROR_BURST_COUNT:
            return None
        return Resolution(
            "failed",
            "error_burst" if not host_error else "host_error",
            {
                "host_error": host_error,
                "audit_error_events": errors,
                "audit_errors": [_json_of(e) for e in errors],
            },
        )

    def _within_burst_window(self, rec: Any, event: Any) -> bool:
        newest = _attr(event, "timestamp")
        since = _attr(rec, "delivering_at")
        gap = _elapsed(since, newest)
        return gap is None or gap <= _ERROR_BURST_WINDOW_SECS

    # ---- delivery evidence ----------------------------------------------- #

    async def _delivery_evidence(self, rec: Any, snap: Any, slot: Any) -> dict[str, Any]:
        """Everything Studio can honestly say about whether this dispatch reached the host.

        Six independent facts, assembled in one place because they are only meaningful together: a
        missing transcript row means nothing on its own (process memory is emptied by a restart), and a
        quiet slot means nothing either if the disk moved.
        """
        host = self._s.host
        slot_key = str(_attr(rec, "slot_key") or "")
        since = str(_attr(rec, "delivering_at") or "")
        delivery_id = str(_attr(rec, "delivery_id") or "")
        wire_text = str(_attr(rec, "wire_text") or "")

        row = None
        wrong_slot = None
        readable = False
        if slot_key and since:
            row = host.find_delivery_row(
                slot_key, since_ts=since, delivery_id=delivery_id, wire_text=wire_text
            )
            # Each read updates the host capabilities. Capture its result before a healthy read of
            # another slot can erase this one's failure; missing evidence never proves non-delivery.
            readable = host.can_prove_absence()
            if row is None:
                row, wrong_slot, others_readable = self._search_other_slots(
                    rec, since, delivery_id, wire_text
                )
                readable = readable and others_readable

        evidence: dict[str, Any] = {
            # Findings already on the row are carried forward: a presence anomaly recorded one tick ago
            # is still true, and dropping it here would unblock the intent on the next status move.
            "findings": self._merge_findings(
                dict(_attr(rec, "evidence", {}) or {}).get("findings"), None
            ),
            "transcript_row": (
                {"slot_key": wrong_slot or slot_key, "ts": row.ts, "role": row.role}
                if row is not None
                else None
            ),
            "transcript_row_absent": (row is None) if readable else False,
            "wrong_slot": wrong_slot,
            "slot_ran_since": self._slot_ran_since(rec, slot),
            "boot_id_unchanged": str(_attr(rec, "boot_id") or "")
            == str(getattr(self._s, "boot_id", "") or ""),
        }

        fresh = await self._fresh_presence(rec, snap)
        baseline = _attr(rec, "presence_baseline")
        presence = self._presence(rec, snap)
        evidence["disk_movement"] = {
            "human_turn_delta": presence.get("human_turn_delta"),
            "marker_advanced": presence.get("marker_advanced"),
            "resolution_event_seen": self._resolution_event_seen(rec, snap),
        }
        evidence["disk_baseline_unchanged"] = (
            presence_unchanged(baseline, fresh) if baseline is not None else None
        )
        return evidence

    def _search_other_slots(
        self, rec: Any, since: str, delivery_id: str, wire_text: str
    ) -> tuple[Any, str | None, bool]:
        """Return a matching row, its slot and whether every absence read succeeded (review R08).

        A row in another slot is the ``wrong_slot`` finding, not a delivery: the decision reached the
        host but not the session Studio captured against, so no automatic answer is safe.
        """
        try:
            repo = self._s.repos.get(rec.repo_id)
        except StudioError:
            return None, None, False
        host = self._s.host
        views = host.slots_for_project(repo.canonical_path)
        readable = host.can_prove_absence()
        for view in views:
            if view.key == str(_attr(rec, "slot_key") or ""):
                continue
            found = host.find_delivery_row(
                view.key, since_ts=since, delivery_id=delivery_id, wire_text=wire_text
            )
            readable = host.can_prove_absence() and readable
            if found is not None:
                return found, view.key, readable
        return None, None, readable

    def _slot_ran_since(self, rec: Any, slot: Any) -> bool | None:
        """Did the bound slot run a turn after ``delivering_at``? ``None`` when the slot is unreadable.

        ``None`` rather than ``False`` matters: ``False`` is one of the four proofs of ``NotDelivered``,
        and "we could not look" must never be able to stand in for "it did not happen".
        """
        if slot is None or not self._slots_readable():
            return None
        if self._ran.get(str(_attr(rec, "action_id"))):
            return True
        if _busy_reasons(slot):
            return True
        return bool(_iso_ge(_attr(slot, "last_turn_ts"), _attr(rec, "delivering_at")))

    def _delivery_confirmed(self, evidence: Mapping[str, Any]) -> bool:
        movement = evidence.get("disk_movement") or {}
        delta = movement.get("human_turn_delta")
        return bool(
            (evidence.get("transcript_row") is not None and not evidence.get("wrong_slot"))
            or (isinstance(delta, int) and delta >= 1)
            or movement.get("resolution_event_seen")
        )

    async def _fresh_presence(self, rec: Any, snap: Any) -> Any:
        if snap is None:
            return None
        try:
            repo = await self._to_thread(self._s.repos.get, rec.repo_id)
        except StudioError:
            return None
        try:
            return await self._in_pool(
                self._s.reader.presence_baseline,
                Path(repo.canonical_path),
                rec.space,
                rec.intent_dir,
                _attr(snap, "audit"),
                _attr(snap, "state_sha256"),
            )
        except (StudioError, OSError):
            return None

    # ---- presence, cursor, turn ------------------------------------------ #

    def _presence(self, rec: Any, snap: Any) -> dict[str, Any]:
        """Compare human turns and the adapter's marker against the original submit-time baseline.

        ``ok`` is ``None`` when there is no baseline — ``host_control`` and studio-only decisions send
        nothing to the model, so they have no presence requirement — and the callers treat ``None`` and
        ``False`` differently for exactly that reason.

        In-flight decisions still require exactly one turn. The confirmed-delivery branch of
        ``_reconcile_required`` may settle after multiple turns when both audit counts are complete:
        demanding one forever would strand the action against a baseline that must never be rewritten.
        Partial history keeps its separate dispatch-order check below.
        """
        baseline = _attr(rec, "presence_baseline")
        if baseline is None or snap is None:
            return {"human_turn_delta": None, "marker_advanced": None, "ok": None}
        events = list(_attr(_attr(snap, "audit"), "events", ()) or ())
        count = sum(1 for e in events if _attr(e, "event") == C.HUMAN_PRESENCE_EVENT)
        delta = count - int(_attr(baseline, "human_turn_events", 0) or 0)
        before = _attr(baseline, "human_turn_mtime_ns")
        after = _attr(_attr(snap, "markers"), "human_turn_mtime_ns")
        advanced = after is not None and (before is None or int(after) > int(before))
        partial = bool(_attr(baseline, "human_turn_events_partial", False))
        if partial:
            fresh = len(self._new_events(rec, snap, (C.HUMAN_PRESENCE_EVENT,), None))
            ok = fresh >= 1 and advanced
            return {
                "human_turn_delta": delta,
                "new_human_turns": fresh,
                "partial": True,
                "marker_advanced": advanced,
                "ok": ok,
            }
        reconciling = (
            _attr(rec, "status") == "ReconciliationRequired"
            and _attr(_attr(snap, "audit"), "complete", False) is True
        )
        return {
            "human_turn_delta": delta,
            "partial": False,
            "marker_advanced": advanced,
            "ok": bool((delta >= 1 if reconciling else delta == 1) and advanced),
        }

    def _turn_started(self, rec: Any, slot: Any, snap: Any) -> bool:
        """Has the queued message become a running turn? (review P22)

        Two signals, because the host gives no "queue drained" event: the slot became busy after
        ``delivered_at``, or the queued row turned into a user row with an assistant reply behind it.
        """
        if self._ran.get(str(_attr(rec, "action_id"))):
            return True
        if slot is None:
            return False
        if _busy_reasons(slot):
            return True
        row = self._delivery_row(rec)
        return row is not None and self._assistant_after(rec, row)

    def _turn_ended(self, rec: Any, slot: Any, snap: Any) -> bool:
        """Did the dispatched turn finish?

        With the host attached: the slot is idle *and* something shows a turn actually happened (we
        watched it run, or an assistant row follows the delivery row). "Idle" alone is not enough — a
        slot that never started is idle too, and that case belongs to the deadline path.

        Disk movement proves that a turn did work, not that it finished. During startup the host
        may attach only on the first request; keep its execution lease until live idleness is known.
        """
        if not self._slots_readable():
            return False
        if slot is None:
            return False
        if _busy_reasons(slot):
            return False
        if self._ran.get(str(_attr(rec, "action_id"))):
            return True
        row = self._delivery_row(rec)
        if row is None:
            return False
        return self._assistant_after(rec, row) or _iso_gt(_attr(slot, "last_turn_ts"), row.ts)

    def _delivery_row(self, rec: Any) -> Any:
        slot_key = str(_attr(rec, "slot_key") or "")
        since = str(_attr(rec, "delivering_at") or "")
        if not slot_key or not since:
            return None
        return self._s.host.find_delivery_row(
            slot_key,
            since_ts=since,
            delivery_id=str(_attr(rec, "delivery_id") or ""),
            wire_text=str(_attr(rec, "wire_text") or ""),
        )

    def _assistant_after(self, rec: Any, row: Any) -> bool:
        rows = self._s.host.recent_rows(
            str(_attr(rec, "slot_key") or ""), since_ts=row.ts, roles=("assistant",), limit=10
        )
        return any(_iso_ge(r.ts, row.ts) for r in rows)

    def _observe_run(self, rec: Any, slot: Any) -> None:
        """Remember that the bound slot was busy after this dispatch.

        The host keeps no "was it running" history, so an observation missed here is lost for good — and
        ``slot_ran_since`` is one of the four facts the ``NotDelivered`` proof rests on.
        """
        if slot is None or not _busy_reasons(slot):
            return
        last = _attr(slot, "last_ts")
        if not last or _iso_ge(last, _attr(rec, "delivering_at")):
            self._ran[str(_attr(rec, "action_id"))] = True

    def _forget(self, rec: Any) -> None:
        self._ran.pop(str(_attr(rec, "action_id")), None)

    # ---- disk predicates -------------------------------------------------- #

    def _historical_plan_approval(
        self, rec: Any, snap: Any, stage: Any, questions: Any,
    ) -> Any:
        """Settle the old reply, never grant authority to a subsequently reset plan."""
        if stage != "code-generation" or not _attr(_attr(snap, "audit"), "complete", False):
            return None
        evidence = _attr(rec, "evidence", {}) or {}
        captured_question = evidence.get("questions") or {}
        if _attr(questions, "relpath") != captured_question.get("relpath"):
            return None
        boundary = (evidence.get("audit") or {}).get("boundary_event") or {}
        events = list(_attr(_attr(snap, "audit"), "events", ()) or ())
        decision = next((
            event for event in events
            if _attr(event, "event") == "DECISION_RECORDED"
            and _attr(event, "shard") == boundary.get("shard")
            and _attr(event, "pos") == boundary.get("pos")
        ), None)
        if decision is None:
            return None
        fields = _attr(decision, "fields", {}) or {}
        unit = _attr(rec, "unit") or None
        target = f"unit:{unit}" if unit else "stage:code-generation"
        prompt_sha = _attr(_attr(rec, "captured"), "question_digest")
        required = {
            "Stage": stage, "Checkpoint": "Code Generation Plan Approval",
            "Plan Target": target, "Intent": _attr(rec, "intent_uuid"),
            "Questions File": captured_question.get("relpath"),
            "Prompt SHA-256": prompt_sha,
        }
        if (
            not required["Intent"] or not prompt_sha
            or any(fields.get(key) != value for key, value in required.items())
            or fields.get("Questions SHA-256") != prompt_sha
            or (fields.get("Unit") or None) != unit or fields.get("Workflow")
        ):
            return None
        for key in ("Directive Epoch", "Approval Fingerprint"):
            value = fields.get(key, "")
            if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
                return None
        if not fields.get("Run floor") or not fields.get("Session"):
            return None
        required.update({key: fields[key] for key in (
            "Directive Epoch", "Approval Fingerprint", "Run floor", "Session",
        )})
        for receipt in self._new_events(rec, snap, ("PLAN_APPROVAL_RECORDED",), stage):
            received = _attr(receipt, "fields", {}) or {}
            approved_sha = received.get("Questions SHA-256", "")
            if (
                _attr(receipt, "shard") != _attr(decision, "shard")
                or _event_order(receipt) <= _event_order(decision)
                or received.get("Details") != C.WIRE_APPROVE_PLAN
                or (received.get("Unit") or None) != unit or received.get("Workflow")
                or any(received.get(key) != value for key, value in required.items())
                or not isinstance(approved_sha, str)
                or not re.fullmatch(r"[0-9a-f]{64}", approved_sha)
                or approved_sha == prompt_sha
            ):
                continue
            # A later same-target prompt could have consumed this reply instead.
            if any(
                _attr(event, "event") == "DECISION_RECORDED"
                and (_attr(event, "fields", {}) or {}).get("Stage") == stage
                and ((_attr(event, "fields", {}) or {}).get("Unit") or None) == unit
                and _event_order(decision) < _event_order(event) < _event_order(receipt)
                for event in events
            ):
                continue
            return receipt
        return None

    def _returned_revision_gate(
        self, rec: Any, snap: Any, stage: Any, rejected: Any, revising: Any,
    ) -> Any:
        """Recover a finished revision even if no poll observed its transient R row.

        A question mark alone proves nothing. Require the original audit boundary,
        a complete same-shard rejection/revision/returned-gate chain in the same
        scope, and the corresponding revision increase. Presence and cursor checks
        still run in evaluate_resolution; this never approves the returned gate.
        """
        audit = _attr(snap, "audit")
        if (
            self._current_stage(snap) != stage or self._row_mark(snap, stage) != "?"
            or not _attr(audit, "complete", False)
            or self._boundary_order(rec, snap) is None
        ):
            return None
        gate = self._newest_new(rec, snap, ("STAGE_AWAITING_APPROVAL",), stage)
        if gate is None:
            return None
        events = (rejected, revising, gate)
        expected_unit = _attr(rec, "unit") or None
        for event in events:
            fields = _attr(event, "fields", {}) or {}
            if fields.get("Workflow") or (fields.get("Unit") or None) != expected_unit:
                return None
        shard = _attr(rejected, "shard")
        if not shard or any(_attr(event, "shard") != shard for event in events):
            return None
        boundary = (_attr(rec, "evidence", {}) or {}).get("audit", {}).get("boundary_event") or {}
        if boundary.get("shard") != shard:
            return None
        if not (_event_order(rejected) < _event_order(revising) < _event_order(gate)):
            return None
        times = [C.epoch_from_iso(_attr(event, "timestamp")) for event in events]
        if any(value is None for value in times) or not (times[0] <= times[1] <= times[2]):
            return None
        before = ((_attr(rec, "evidence", {}) or {}).get("state") or {}).get("revision_count")
        current = _attr(_attr(snap, "state"), "revision_count")
        fields = _attr(revising, "fields", {}) or {}
        recorded = str(fields.get("Revision count", fields.get("Revision Count", ""))).strip()
        if (
            type(before) is not int or type(current) is not int or not recorded.isdecimal()
            or current != int(recorded) or current <= before
        ):
            return None
        return gate

    def _new_events(self, rec: Any, snap: Any, types: Sequence[str], stage: Any) -> list[Any]:
        """Audit rows that belong to *this* dispatch (the "new X" rule, review P23).

        Both halves are required: the timestamp must be at or after ``delivering_at`` (second precision,
        hence ``>=``) and the row must sort after the boundary event the card captured. Timestamp alone
        would accept the row that opened the gate; position alone would accept a row from a previous
        session in a shard that happens to sort late.
        """
        if snap is None:
            return []
        since = _attr(rec, "delivering_at")
        wanted = set(types)
        floor = self._boundary_order(rec, snap)
        out: list[Any] = []
        for event in list(_attr(_attr(snap, "audit"), "events", ()) or ()):
            if _attr(event, "event") not in wanted:
                continue
            if stage is not None and (_attr(event, "fields", {}) or {}).get("Stage") != stage:
                continue
            if since and not _iso_ge(_attr(event, "timestamp"), since):
                continue
            if floor is not None and _event_order(event) <= floor:
                continue
            out.append(event)
        return out

    def _newest_new(self, rec: Any, snap: Any, types: Sequence[str], stage: Any) -> Any:
        events = self._new_events(rec, snap, types, stage)
        if not events:
            return None
        return max(events, key=lambda e: _attr(e, "sort_key", ("", 0, 0)))

    def _boundary_order(self, rec: Any, snap: Any) -> tuple[int, int] | None:
        """``(shard_index, pos)`` of the captured boundary event, or ``None`` when it cannot be placed.

        The stored evidence names the shard by basename; the index is whatever position that shard holds
        in the current bundle. When the shard is gone the row cannot be ordered against, so the floor is
        dropped and the timestamp condition carries the rule alone — the honest reading of "we no longer
        have that history".
        """
        audit = dict(_attr(rec, "evidence", {}) or {}).get("audit") or {}
        boundary = audit.get("boundary_event") if isinstance(audit, Mapping) else None
        if not isinstance(boundary, Mapping):
            return None
        shard = str(boundary.get("shard") or "")
        pos = boundary.get("pos")
        if not shard or not isinstance(pos, int):
            return None
        for index, meta in enumerate(list(_attr(_attr(snap, "audit"), "shards", ()) or ())):
            if str(_attr(meta, "relpath", "")).rsplit("/", 1)[-1] == shard:
                return index, pos
        return None

    def _resolution_event_seen(self, rec: Any, snap: Any) -> bool:
        return self._newest_new(rec, snap, C.RESOLUTION_EVENTS, None) is not None

    def _state_moved(self, rec: Any, snap: Any) -> bool:
        """Did the state file change since the card was captured?

        Read as the durable proxy for the table's "any stage row state changed, or ``Current Stage``
        changed": those are engine writes to ``aidlc-state.md``, and every engine write to it changes the
        digest. Studio stores the captured digest, not the captured rows, so the digest is what there is
        — and it fails in the informative direction (``StateChanged`` rather than ``ResolvedNoTransition``).
        """
        captured = _attr(_attr(rec, "captured"), "state_hash")
        current = _attr(snap, "state_sha256")
        return bool(captured) and bool(current) and captured != current

    def _digest_changed(self, rec: Any, snap: Any) -> bool:
        captured = _attr(_attr(rec, "captured"), "question_digest")
        current = _attr(_attr(snap, "questions"), "sha256")
        return captured != current

    def _answers_filled(self, rec: Any, questions: Any) -> bool:
        """Is every index the submit answered now non-blank in the file?"""
        payload = dict(_attr(rec, "payload", {}) or {})
        indices = {
            int(a.get("index"))
            for a in (payload.get("answers") or [])
            if isinstance(a, Mapping) and isinstance(a.get("index"), int)
        }
        if not indices or questions is None:
            return False
        by_index = {int(_attr(q, "index", 0)): q for q in _attr(questions, "questions", ()) or ()}
        return all(bool(_attr(by_index.get(i), "answered", False)) for i in indices)

    def _row_mark(self, snap: Any, stage: Any) -> str | None:
        state = _attr(snap, "state")
        if state is None or not stage:
            return None
        row = state.row(str(stage))
        return None if row is None else str(_attr(row, "mark", "") or "")

    def _row_completed(self, snap: Any, stage: Any) -> bool:
        mark = (self._row_mark(snap, stage) or "").lower()
        return C.CHECKBOX_MAP.get(mark) == "completed"

    def _current_stage(self, snap: Any) -> Any:
        state = _attr(snap, "state")
        return None if state is None else _attr(state, "current_stage")

    def _scope_of(self, snap: Any) -> str | None:
        state = _attr(snap, "state")
        if state is None:
            return None
        return (dict(_attr(state, "project", {}) or {}).get("Scope") or "").strip() or None

    # ---- tick step 5: reclaim -------------------------------------------- #

    async def _reclaim_pass(self) -> int:
        """Offer every lease to the scheduler's proof rules; it decides, this only supplies the boundary."""
        reclaimed = 0
        for rec in await self._s.scheduler.list():
            boundary = await self._boundary_for_lease(rec)
            try:
                if await self._s.scheduler.try_reclaim(rec, boundary=boundary):
                    reclaimed += 1
            except StudioError as exc:  # pragma: no cover - defensive
                _LOG.debug("reclaim of %s refused: %s", _attr(rec, "resolved_repo_identity"), exc)
        return reclaimed

    async def _boundary_for_lease(self, rec: "LeaseRecord") -> Any:
        action_id = _attr(rec, "action_id")
        if not action_id:
            return None
        row = await self._to_thread(self._s.storage.get, "actions", str(action_id))
        if row is None:
            return None
        binding = await self._s.sessions.get(
            str(row.get("repo_id")), str(row.get("space")), str(row.get("intent_dir"))
        )
        return _attr(binding, "last_stable_boundary")

    # ---- tick step 6: advisor -------------------------------------------- #

    async def _advisor_pass(self) -> tuple[int, int]:
        """Settle drafts nobody is polling, then start at most one draft the owner has standing-granted."""
        broker = getattr(self._s, "advisor", None)
        settle_open = getattr(broker, "settle_open", None)
        autodraft = getattr(broker, "autodraft", None)
        # A container without the broker — or with an older one — is a normal shape here: the advisor is
        # the one collaborator a tick does not need, and tests wire only the parts they assert on. A
        # housekeeping pass that raised over a missing attribute would stop the loop every delivery needs.
        if not callable(settle_open) or not callable(autodraft):
            return 0, 0

        settled = 0
        started = 0
        # Settling first is the ordering, not a preference. ``poll`` is otherwise reachable only from the
        # GET route, the host keeps a finished subagent's whole answer for an hour and then prunes it, and
        # a pre-draft exists precisely because nobody has a browser open on that card: the pass that
        # starts drafts must never be the reason a finished one went unread. It also frees the inflight
        # slots the automatic cap counts, so the next draft starts in this tick rather than the next one.
        try:
            settled = int(await settle_open())
        except Exception as exc:
            # Caught per sub-step and never re-raised. An advisor that cannot spawn is a missing
            # convenience; a tick that died on it would cost the retention pass behind it its human-text
            # purge and its draft expiry, and would stop the loop every action's delivery is waiting on.
            _LOG.debug("advisor settle pass failed: %s", exc)
        try:
            started = int(await autodraft())
        except Exception as exc:
            _LOG.debug("advisor pre-draft pass failed: %s", exc)
        # Counts are returned for the tests that assert both halves ran, and go nowhere else. In
        # particular they stay out of ``status()``: ``health.reconciler`` is a pinned four-key object
        # (§3.1) and a draft nobody asked for is not a fact about whether reconciliation is healthy.
        return settled, started

    # ---- tick step 7: retention ------------------------------------------ #

    async def _retention_pass(self) -> tuple[int, int]:
        """Purge verbatim human text past its retention and expire advisor drafts."""
        days = int(self._s.settings.human_text_retention_days())
        cutoff = C.iso_from_epoch(self._s.clock.now() - days * 86400)
        purged = await self._to_thread(self._s.storage.activity_purge_human_text, cutoff)
        expired = await self._expire_drafts()
        return int(purged), expired

    async def _expire_drafts(self) -> int:
        """Retire past-TTL drafts through the broker, which is the only thing that may write that table.

        Delegated rather than a second ``UPDATE ... SET status='expired'`` here: the broker's own
        ``expire_stale`` goes through ``_finish``, which publishes ``advisor.updated``, so a tab already
        open on the card learns the draft expired within a tick instead of keeping a stale *Apply* — or,
        for an automatic draft, a stale pre-fill notice — until the human reloads. A container without a
        broker is the blessed shape ``_advisor_pass`` already tolerates, and expires nothing.
        """
        expire = getattr(getattr(self._s, "advisor", None), "expire_stale", None)
        if not callable(expire):
            return 0
        return int(await expire())

    # ---- transitions and evidence writes --------------------------------- #

    async def _transition(
        self,
        rec: Any,
        to_status: str,
        *,
        reason: str,
        evidence: Mapping[str, Any],
        values: Mapping[str, Any] | None = None,
    ) -> Any:
        """One status move through the broker, tolerating the two races that are not errors.

        ``StaleGeneration`` means a delivery report or a second reconciler pass moved the row first, and
        ``IllegalTransition`` means the row is no longer where this decision was computed for. Both are
        answered by returning the current row: retrying would replay a judgement against evidence that
        has already been superseded.

        The evidence written is the row's own blob with this pass merged on top, not just the delta.
        `captured.boundary_event` lives in that blob and is what the "new X" ordering rule is measured
        against, so a broker that replaced rather than merged would quietly weaken every later comparison.
        """
        payload = dict(_attr(rec, "evidence", {}) or {})
        payload.update({k: v for k, v in dict(evidence).items() if k != "audit_error_events"})
        try:
            if values:
                await self._stamp_values(rec, values)
                rec = await self._s.actions.get(rec.action_id)
            return await self._s.actions.transition(
                rec.action_id,
                to_status,
                expected_generation=int(_attr(rec, "status_generation", 0) or 0),
                reason=reason,
                evidence=payload,
            )
        except (StaleGeneration, IllegalTransition) as exc:
            _LOG.debug("transition %s → %s skipped: %s", rec.action_id, to_status, exc)
            return await self._s.actions.get(rec.action_id)

    async def _stamp_values(self, rec: Any, values: Mapping[str, Any]) -> None:
        await self._to_thread(
            self._s.storage.cas_update,
            "actions",
            rec.action_id,
            int(_attr(rec, "status_generation", 0) or 0),
            dict(values),
        )

    async def _stamp_evidence(self, rec: Any, patch: Mapping[str, Any]) -> Any:
        """Merge evidence onto an action without changing its status.

        Goes through ``cas_update`` and not ``Storage.update``: ``actions`` is a CAS table and the store
        refuses a non-CAS write to it, precisely so two writers cannot lose each other's evidence.
        §1.12's "non-status ``Storage.update``" is therefore implemented as a generation-checked update
        with no status column — see the module report.
        """
        row = await self._to_thread(self._s.storage.get, "actions", rec.action_id)
        if row is None:
            return rec
        try:
            await self._stamp_evidence_row(row, patch)
        except StaleGeneration:
            return await self._s.actions.get(rec.action_id)
        return await self._s.actions.get(rec.action_id)

    async def _stamp_evidence_row(self, row: Mapping[str, Any], patch: Mapping[str, Any]) -> None:
        previous = dict(row.get("evidence_json") or {})
        merged = dict(previous)
        merged.update({k: v for k, v in dict(patch).items() if k != "audit_error_events"})
        if merged == previous:
            return
        await self._to_thread(
            self._s.storage.cas_update,
            "actions",
            str(row.get("action_id")),
            int(row.get("status_generation") or 0),
            {"evidence_json": merged},
        )

    @staticmethod
    def _with_finding(existing: Any, code: str, params: Mapping[str, Any]) -> list[dict]:
        """Append a reconciler-discovered finding in the shape ``consistency`` lifts back out.

        Stored on the action because ``ConsistencyEngine`` cannot derive these from disk: only the
        reconciler compared the presence baseline, the cursor and the slot a row landed in (review
        P04/R01/R08). Recording them here is what makes them blocking findings and a recovery card
        instead of one blob nobody reads.
        """
        out = [f for f in (existing or []) if isinstance(f, Mapping) and f.get("code") != code]
        out.append({"code": code, "params": {k: v for k, v in dict(params).items() if k != "ok"}})
        return out

    async def _release_lease(self, rec: Any) -> None:
        """Give back the execution lease this action held — and only that one.

        The action id is compared before releasing: by the time an action resolves the lease may already
        belong to another dispatch, and releasing that one would let two turns into one repository.
        """
        identity = _attr(rec, "resolved_repo_identity")
        if not identity:
            return
        lease = await self._s.scheduler.get(str(identity))
        if lease is None or _attr(lease, "kind") != "execution":
            return
        if str(_attr(lease, "action_id") or "") != str(_attr(rec, "action_id")):
            return
        with contextlib.suppress(StudioError):
            await self._s.scheduler.release(LeaseGrant.from_record(lease))

    # ---- small helpers ---------------------------------------------------- #

    def _decision_of(self, rec: Any) -> str:
        payload = _attr(rec, "payload") or {}
        decision = str(dict(payload).get("decision") or "") if isinstance(payload, Mapping) else ""
        if decision:
            return decision
        # Command actions carry their decision in their type (`run`, `resume`, …), and a card created
        # before the UI sent a payload has none at all.
        action_type = str(_attr(rec, "type") or "")
        return action_type if action_type in C.DECISION_KIND else ""

    def _slot_for(self, rec: Any) -> Any:
        slot_key = _attr(rec, "slot_key")
        return self._s.host.slot(str(slot_key)) if slot_key else None

    def _slots_readable(self) -> bool:
        """Can the host's slot table be read at all right now?

        The difference between "no slot" and "no host" is the difference between a terminated session and
        a Studio running disk-only (C08); collapsing them would push every in-flight action to
        ``DeliveryUncertain`` the moment the gateway had not attached its state yet.
        """
        host = self._s.host
        if not host.attached():
            return False
        cap = host.capabilities().get("slots")
        return cap is None or bool(_attr(cap, "available", True))

    def _session_replaced(self, rec: Any, slot: Any) -> bool:
        """Is the session this decision was dispatched into gone or now a different session?"""
        if not self._slots_readable():
            return False
        if slot is None:
            return True
        recorded = str(_attr(rec, "session_key") or "")
        observed = str(_attr(slot, "session_key") or "")
        return bool(recorded and observed and recorded != observed)

    def _past_deadline(self, rec: Any) -> bool:
        deadline = _attr(rec, "deadline_at")
        return bool(deadline) and str(self._s.clock.iso()) > str(deadline)

    def _turn_timeout(self) -> int:
        return int(min(self._s.host.turn_timeout_secs(), C.PROCESSING_DEADLINE_CAP_SECS))

    @staticmethod
    def _deadline_from(iso: str, seconds: int) -> str:
        base = C.epoch_from_iso(iso) or 0.0
        return C.iso_from_epoch(base + seconds)

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

    # ---- threading ------------------------------------------------------- #

    def _semaphore(self) -> asyncio.Semaphore:
        """The scan gate, re-created when the running loop changes.

        A semaphore remembers the loop it first suspended on; tests build one ``Reconciler`` and drive it
        from several ``asyncio.run`` calls, and a gate bound to a dead loop would deadlock the first scan.
        """
        loop = asyncio.get_running_loop()
        if self._sem_obj is None or self._sem_loop is not loop:
            self._sem_obj = asyncio.Semaphore(C.SCAN_CONCURRENCY)
            self._sem_loop = loop
        return self._sem_obj

    async def _in_pool(self, fn: Callable[..., Any], *args: Any) -> Any:
        """Run one sync call on Studio's own scan pool (review R11).

        Repository walks and hashing go here rather than on the process default executor, which the host
        uses for chat persistence, spawns and atomic writes: a 64-repository pass must not be able to
        block the gateway's own work.
        """
        pool = getattr(self._s, "scan_pool", None)
        if pool is None:
            return await asyncio.to_thread(fn, *args)
        return await asyncio.get_running_loop().run_in_executor(pool, functools.partial(fn, *args))

    @staticmethod
    async def _to_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Short store calls on the process default executor, per §0.6.

        Reserved for `Storage` and single-file reads; anything that walks a repository goes through
        `_in_pool` instead, so the host's own executor is never held for a filesystem traversal.
        """
        return await asyncio.to_thread(functools.partial(fn, *args, **kwargs))
