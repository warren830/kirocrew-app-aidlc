"""Who may touch one repository right now (§1.10): one turn at a time, and none during admin work.

An execution lease *is* a dispatched action (stamped with ``lease_generation``) in ``Delivering``/
``Delivered``/``Processing``: the action leaving those statuses is the release, so nothing is heartbeaten
or reclaimed. Admin operations, and the submit window before ``Delivering``, hold a per-repository
``flock`` that the kernel drops when the process dies. A force stop is in flight but takes no lease, and
uncertain actions hold nothing: the submit-time slot checks keep a second turn out of a busy session.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from .errors import LeaseHeld, StudioError

if TYPE_CHECKING:  # pragma: no cover - typing only; these modules are never imported at runtime here
    from .activity import ActivityProjector
    from .constants import Clock
    from .repo_registry import RepoRecord
    from .sessions import HostBridge
    from .settings import SettingsService
    from .storage import Storage

#: Operation types an admin lease may be taken for (§1.10); the scheduler is the one enforcement point.
ADMIN_OPERATION_TYPES = ("install", "upgrade", "recovery", "intent_create", "recompose", "scope_change",
                         "config_change", "cursor_switch", "doctor", "repo_metadata")
#: A dispatch is or may still be on the wire: an action in one of these holds its repository.
IN_FLIGHT_ACTION_STATUS = frozenset({"Delivering", "Delivered", "Processing"})
#: ``install_transactions.status`` values that prove an admin operation has stopped touching the repo.
TRANSACTION_SETTLED_STATUS = frozenset({"committed", "rolled_back", "recovery_required", "failed"})
#: ``retry_after_secs`` on a refusal: a turn or an admin operation rarely ends sooner.
RETRY_AFTER_SECS = 15


@dataclass(frozen=True, slots=True)
class LeaseRecord:
    """One live lease as ``/leases`` shows it: a grant this process holds, or an in-flight action."""

    kind: str
    resolved_repo_identity: str
    generation: int
    acquired_at: str
    heartbeat_at: str
    intent_uuid: str | None = None
    session_key: str | None = None
    action_id: str | None = None
    observed_busy_state: str | None = None
    operation_type: str | None = None
    transaction_id: str | None = None
    repo_id: str = ""
    #: Always ``False``: a lease can no longer outlive its owner. Kept for the ``LeaseView`` shape.
    orphaned: bool = False


@dataclass(frozen=True, slots=True)
class LeaseGrant:
    """What ``acquire_*`` hands back and ``release`` takes; the generation stamps the holder's work."""

    kind: str
    resolved_repo_identity: str
    generation: int
    acquired_at: str
    repo_id: str

    @classmethod
    def from_record(cls, rec: LeaseRecord) -> "LeaseGrant":
        return cls(rec.kind, rec.resolved_repo_identity, int(rec.generation), rec.acquired_at, rec.repo_id)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def lease_view(rec: LeaseRecord, *, intent_key: str | None = None) -> dict[str, Any]:
    """The ``LeaseView`` JSON (§1.10/§2); ``intent_key`` needs the binding table, so the handler passes it."""
    return {**asdict(rec), "intent_key": intent_key}


class RepoScheduler:
    """Grants and reports repository leases. One process-wide gate orders the grant decisions, so the
    cap count and the grant cannot interleave with a second acquire."""

    def __init__(self, storage: "Storage", host: "HostBridge", settings: "SettingsService", clock: "Clock",
                 activity: "ActivityProjector", *, busy_reasons: Callable[[Any], Sequence[str]] | None = None) -> None:
        self._storage, self._host, self._settings, self._clock = storage, host, settings, clock
        self._activity = activity
        self._busy_reasons = busy_reasons  # `sessions.busy_reasons` unless a test injects one
        self._lock_dir = Path(storage.path).parent / "locks"
        #: identity -> (lease, locked fd) for every grant this process holds right now.
        self._held: dict[str, tuple[LeaseRecord, int]] = {}
        self._generation = 0
        self._gate: asyncio.Lock | None = None
        self._gate_loop: asyncio.AbstractEventLoop | None = None

    async def acquire_execution(self, repo: "RepoRecord", *, intent_uuid: str | None, session_key: str | None,
                                action_id: str) -> LeaseGrant:
        """Lock the repository for one submit. The caller CASes its action to ``Delivering`` and releases on
        every path; from that CAS on, the in-flight action holds the repository. ``rate_limited`` counts only
        *other* repositories, so a second submit against a busy one reports the actionable ``repo_busy``."""
        return await self._acquire("execution", repo, intent_uuid=intent_uuid, session_key=session_key,
                                   action_id=action_id)

    async def acquire_admin(self, repo: "RepoRecord", *, operation_type: str,
                            transaction_id: str | None) -> LeaseGrant:
        """Lock the repository until ``release`` for work on engine-owned files. Refuses while a turn is in
        flight and never preempts it; not capped, because the cap bounds turns (model spend, FR-SES-007)."""
        if operation_type not in ADMIN_OPERATION_TYPES:  # a programmer error: the code path chooses it
            raise ValueError(f"{operation_type!r} is not an admin operation type: {ADMIN_OPERATION_TYPES}")
        return await self._acquire("admin", repo, operation_type=operation_type, transaction_id=transaction_id)

    def require_host_idle(self, repo: "RepoRecord") -> None:
        """Check external host turns after acquiring an admin lease and immediately before writing."""
        for view in self._host.slots_for_project(repo.canonical_path):
            reasons = self._reasons(view)
            if reasons is None or reasons:
                raise StudioError("repo_busy", "a host session may be executing in this repository",
                                  details={"repo_id": repo.repo_id, "slot_key": getattr(view, "key", None)})

    async def release(self, grant: LeaseGrant) -> None:
        """Give the grant back. One no longer held is recorded as ``stale``, not raised: release runs from
        cleanup paths, where raising would hide the failure the user needs to see."""
        held = self._held.get(grant.resolved_repo_identity)
        stale = held is None or held[0].generation != grant.generation
        if not stale:
            self._drop(grant.resolved_repo_identity)
        await self._activity.record(kind="lease.released", severity="info", repo_id=grant.repo_id or None, params={
            "lease_kind": grant.kind, "generation": int(grant.generation),
            "resolved_repo_identity": grant.resolved_repo_identity, "stale": stale})

    async def get(self, identity: str) -> LeaseRecord | None:
        """The lease held for one identity, of whichever kind holds it (they are exclusive)."""
        return next((rec for rec in await self.list() if rec.resolved_repo_identity == identity), None)

    async def list(self) -> list[LeaseRecord]:
        """Every live lease, oldest first."""
        return await asyncio.to_thread(self.live)

    def live(self) -> list[LeaseRecord]:
        """``list()`` for a caller already off the event loop (``Services.health``): it reads SQLite."""
        return sorted(self._live(self._in_flight()).values(),
                      key=lambda r: (r.acquired_at, r.resolved_repo_identity, r.kind))

    # ---- plumbing ----------------------------------------------------------

    async def _acquire(self, kind: str, repo: "RepoRecord", **fields: Any) -> LeaseGrant:
        identity = repo.resolved_identity
        if not identity:
            raise StudioError("identity_unprovable", "this repository has no provable identity to lock",
                              details={"repo_id": repo.repo_id, "availability": repo.availability})
        if kind == "execution" and repo.availability != "available":
            raise StudioError("repo_unavailable", "the repository is not currently readable", details={
                "repo_id": repo.repo_id, "availability": repo.availability,
                "availability_detail": repo.availability_detail})
        async with self._serialised():
            if identity in self._held:
                raise _busy(self._held[identity][0])
            # The file lock first and the in-flight read under it: another process can then only commit
            # `Delivering` before this read (which sees it) or after this grant is released.
            fd = self._lock(identity)
            if fd is None:
                raise _busy(None)
            try:
                live = self._live(await asyncio.to_thread(self._in_flight, fields.get("action_id")))
                if identity in live:
                    raise _busy(live[identity])
                if kind == "execution":
                    self._assert_cap(identity, live)
                now = self._clock.iso()
                rec = LeaseRecord(kind, identity, self._next_generation(), now, now, repo_id=repo.repo_id, **fields)
                self._held[identity] = (rec, fd)
            except BaseException:
                _unlock(fd)
                raise
        params = {"lease_kind": kind, "generation": rec.generation, "resolved_repo_identity": identity,
                  **{k: v for k, v in fields.items() if v is not None and k not in ("action_id", "session_key")}}
        try:
            await self._activity.record(kind="lease.acquired", severity="info", repo_id=repo.repo_id,
                                        action_id=rec.action_id, session_key=rec.session_key, params=params)
        except BaseException:
            self._drop(identity)  # the caller never receives this grant, so nobody else could release it
            raise
        return LeaseGrant.from_record(rec)

    def _lock(self, identity: str) -> int | None:
        """``flock`` the repository's lock file (non-blocking, so on the loop), or ``None`` while another open
        file holds it: the lock belongs to the open file, so this process can be refused by itself too."""
        self._lock_dir.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        fd = os.open(self._lock_dir / f"{name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _drop(self, identity: str) -> None:
        _unlock(self._held.pop(identity)[1])

    def _in_flight(self, excluding: str | None = None) -> list[dict]:
        """Rows holding an execution lease, newest first. Sync: callers on the loop use ``to_thread``."""
        rows = self._storage.select("actions", {"status": sorted(IN_FLIGHT_ACTION_STATUS)},
                                    order_by="delivering_at DESC")
        return [r for r in rows if r.get("lease_generation") is not None and r.get("action_id") != excluding]

    def _live(self, rows: Sequence[Mapping[str, Any]]) -> dict[str, LeaseRecord]:
        """identity -> lease: this process's grants, else each repository's newest in-flight action."""
        live = {rec.resolved_repo_identity: rec for rec, _fd in list(self._held.values())}
        for row in rows:
            identity = str(row.get("resolved_repo_identity") or "")
            if identity and identity not in live:
                at = str(row.get("delivering_at") or row.get("updated_at") or "")
                live[identity] = LeaseRecord(
                    "execution", identity, int(row["lease_generation"]), at, at, intent_uuid=row.get("intent_uuid"),
                    session_key=row.get("session_key"), action_id=row.get("action_id"),
                    repo_id=str(row.get("repo_id") or ""))
        return live

    def _next_generation(self) -> int:
        """Strictly increasing here, and seeded from the clock in microseconds so a restart does not hand
        out a number an earlier process already stamped on its work."""
        self._generation = max(self._generation + 1, int(self._clock.now() * 1_000_000))
        return self._generation

    def _serialised(self) -> asyncio.Lock:
        """One lock, lazily bound to the running loop, so a ``Services`` rebuild (or a test running each
        scenario on its own loop) never trips asyncio's "bound to a different event loop"."""
        loop = asyncio.get_running_loop()
        if self._gate is None or self._gate_loop is not loop:
            self._gate, self._gate_loop = asyncio.Lock(), loop
        return self._gate

    def _assert_cap(self, identity: str, live: Mapping[str, LeaseRecord]) -> None:
        """The settings cap, floored at 1: a hand-edited 0 would refuse every decision forever."""
        others = [rec for key, rec in live.items() if rec.kind == "execution" and key != identity]
        cap = max(1, int(self._settings.global_concurrency_cap()))
        if len(others) >= cap:
            raise StudioError("rate_limited", "too many repositories are already running a turn", details={
                "cap": cap, "live_execution": len(others), "retry_after_secs": RETRY_AFTER_SECS,
                "repo_ids": sorted({rec.repo_id for rec in others if rec.repo_id})})

    def _reasons(self, view: Any) -> tuple[str, ...] | None:
        """``sessions.busy_reasons(view)``, or ``None`` — never ``()`` — when it cannot be evaluated."""
        from .sessions import busy_reasons  # lazily: the host-facing module is the layer above this one

        try:
            return tuple((self._busy_reasons or busy_reasons)(view))
        except (AttributeError, TypeError, KeyError):
            return None


def _busy(owner: LeaseRecord | None) -> LeaseHeld:
    """``repo_busy`` with the holder's ``LeaseView``; ``None`` when another process holds the lock."""
    if owner is None:
        return LeaseHeld("another process holds this repository",
                         details={"kind": None, "owner": None, "retry_after_secs": RETRY_AFTER_SECS})
    return LeaseHeld(f"{owner.kind} lease held for this repository", details={
        "kind": owner.kind, "owner": lease_view(owner), "retry_after_secs": RETRY_AFTER_SECS})


def _unlock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
