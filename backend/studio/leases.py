"""Who is allowed to touch one repository right now (§1.10).

``Storage`` already guarantees the two mechanical properties: one lease row per
``resolved_repo_identity`` across **both** kinds, and a generation that never repeats. This module adds
the three judgements that need context Storage deliberately does not have:

* the **global concurrency cap** — per repository the cap is always 1 (that is the lease itself), across
  repositories it comes from settings, so it can only be enforced where settings are visible;
* **queuing without preemption** — an admin operation waits for a live turn and a turn waits for a live
  admin operation; neither ever takes the lease away from the other;
* **reclaim proof** — the only way a lease is ever removed other than by its own holder. Elapsed time
  never authorises that removal (FR-SES-007); a positive proof that the previous owner is not executing
  does, and anything unknown counts against the proof, never for it.

The asymmetry is deliberate: failing to reclaim a dead owner's lease costs the user one visible
"orphaned" row on ``/leases`` and a manual nudge, while reclaiming a live owner's lease lets two
dispatches interleave inside one AI-DLC workflow — the corruption this whole design exists to prevent.
Every refusal in ``prove_reclaim`` is therefore a *code*, recorded as evidence, rather than a silent
``False``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from . import constants as C
from .errors import LeaseHeld, StaleGeneration, StudioError

if TYPE_CHECKING:  # pragma: no cover - typing only; these modules are never imported at runtime here
    from .activity import ActivityProjector
    from .projection import StableBoundary
    from .repo_registry import RepoRecord
    from .sessions import HostBridge
    from .settings import SettingsService
    from .storage import LeaseRecord, Storage


# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #

#: Operation types an admin lease may be taken for (§1.10). Kept here rather than in ``constants.py``
#: because §1.10 is the only place the set is defined and the scheduler is the only enforcement point;
#: ``installer.py``/``actions.py`` import it from here so a new operation type is added once.
ADMIN_OPERATION_TYPES = (
    "install",
    "upgrade",
    "recovery",
    "intent_create",
    "recompose",
    "scope_change",
    "config_change",
    "cursor_switch",
    "doctor",
    "repo_metadata",
)

#: An action in one of these statuses can no longer dispatch anything: nothing more will be sent for it
#: (``ALLOWED_TRANSITIONS`` has no outgoing edge that reaches the host), so the lease it holds is dead
#: weight. ``Failed`` belongs here even though the queue still shows the card (§0.5).
SETTLED_ACTION_STATUS = frozenset(C.TERMINAL_ACTION_STATUS) | {"Failed"}
#: The reconciler already decided the delivery is unresolved and has taken the action off the dispatch
#: path; the lease is no longer protecting an in-flight turn (§1.10 (b)).
UNCERTAIN_ACTION_STATUS = frozenset({"DeliveryUncertain", "ReconciliationRequired"})
#: The crash window of C31/P15: the lease was acquired but the ``Delivering`` CAS never happened, so
#: nothing whatsoever was handed to the host and the boundary cannot have moved (§1.10 (b')).
NEVER_DISPATCHED_ACTION_STATUS = frozenset({"Queued", "NotDelivered"})
#: A dispatch is or may still be in flight — never reclaimable.
IN_FLIGHT_ACTION_STATUS = frozenset({"Delivering", "Delivered", "Processing"})

#: ``install_transactions.status`` values that prove an admin operation has stopped touching the repo
#: (§1.14 ``TransactionStatus``: everything else is a step still in progress or a rollback in flight).
TRANSACTION_SETTLED_STATUS = frozenset({"committed", "rolled_back", "recovery_required", "failed"})

#: Reasons a reclaim was granted. Persisted with the ``lease.reclaimed`` activity row, so they are
#: codes the UI translates, never prose.
RECLAIM_REASONS = (
    "never_dispatched",
    "action_absent",
    "action_terminal",
    #: The dispatch this lease was taken for is over and the release that should have followed it never
    #: ran: the settled action still carries this lease's own generation stamp. See `_prove_execution`.
    "release_lost",
    "action_uncertain",
    "admin_transaction_settled",
    #: The process that took a non-transactional admin lease is gone (its `boot_id` is not this one).
    #: The proof six of the nine admin operations rely on, since they open no install transaction.
    "admin_owner_process_gone",
)
#: What each granted execution reason assumed about the action, re-asserted inside
#: ``Storage.lease_reclaim``'s own transaction. The proof and the delete are two transactions, and
#: ``deliver_under_lease`` moves an action ``Queued → Delivering`` without touching the lease generation,
#: so a generation-only guard would let a reclaim cut a decision that is already on the wire and a second
#: submit dispatch a second turn. An empty set means "the row must still be absent".
RECLAIM_ACTION_GUARD: Mapping[str, frozenset[str]] = {
    "never_dispatched": NEVER_DISPATCHED_ACTION_STATUS,
    "action_terminal": SETTLED_ACTION_STATUS,
    "release_lost": SETTLED_ACTION_STATUS,
    "action_uncertain": UNCERTAIN_ACTION_STATUS,
    "action_absent": frozenset(),
}

#: Reasons a reclaim was refused. ``*_unknown`` members are the important ones: they are the cases where
#: the evidence could not be read at all, and they refuse exactly like a positive "still running".
RECLAIM_REFUSALS = (
    "owner_recent",
    "host_unattached",
    "session_unknown",
    "busy_state_unknown",
    "slot_busy",
    "session_busy",
    "session_busy_unknown",
    "action_in_flight",
    "action_status_unknown",
    "boundary_unknown",
    "boundary_unstable",
    "transaction_unknown",
    "transaction_missing",
    "transaction_in_progress",
    #: The transaction is unsettled and its owner is gone: `Installer.settle_abandoned` has not run yet.
    #: Distinct from `transaction_in_progress` because it names a wait, not a live operation.
    "transaction_abandoned_pending_settle",
    #: An admin lease written before schema 2 carries no `boot_id`, so there is nothing to compare.
    "admin_owner_unknown",
    #: This process still holds the lease. A refusal on evidence, never on age (FR-SES-007).
    "admin_owner_process_live",
)


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LeaseGrant:
    """Proof of ownership handed back to the caller of ``acquire_*``.

    Carries the generation because every later write by the holder — heartbeat, release, and the
    ``Delivering`` CAS through ``Storage.deliver_under_lease`` — compares it. A holder that was
    reclaimed therefore fails its next write instead of writing over the new owner's work.
    """

    kind: str
    resolved_repo_identity: str
    generation: int
    acquired_at: str
    repo_id: str

    @classmethod
    def from_record(cls, rec: "LeaseRecord") -> "LeaseGrant":
        return cls(
            kind=rec.kind,
            resolved_repo_identity=rec.resolved_repo_identity,
            generation=int(rec.generation),
            acquired_at=rec.acquired_at,
            repo_id=rec.repo_id,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "resolved_repo_identity": self.resolved_repo_identity,
            "generation": self.generation,
            "acquired_at": self.acquired_at,
            "repo_id": self.repo_id,
        }


@dataclass(frozen=True, slots=True)
class ReclaimProof:
    """The full answer to "may this lease be taken away?", not just the yes/no.

    ``try_reclaim`` returns a bool per the contract, but the reason and the refusal codes are what an
    operator staring at an orphaned lease on ``/leases`` (or a diagnostics export) needs, and what a
    test can assert precisely instead of guessing which of five conditions failed.
    """

    ok: bool
    reason: str
    refusals: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": self.reason,
            "refusals": list(self.refusals),
            "evidence": dict(self.evidence),
        }


def _generation_of(row: Mapping[str, Any] | None) -> int | None:
    """``actions.lease_generation``, or ``None`` when it is absent or unreadable.

    Unreadable reads as absent on purpose: this value only ever *grants* a reclaim, so a column this
    version cannot parse must refuse rather than guess.
    """
    if row is None:
        return None
    value = row.get("lease_generation")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def lease_view(
    rec: "LeaseRecord", *, intent_key: str | None = None, orphaned: bool = False
) -> dict[str, Any]:
    """The ``LeaseView`` JSON of §1.10/§2, assembled from the row plus the two derived fields.

    ``intent_key`` needs the binding table and ``orphaned`` needs the clock, so neither can live on the
    record itself; both are passed in by whoever already holds that context (the handler, or
    ``RepoScheduler.is_stale``). Every key is always present, ``null`` when unknown (§0.4).
    """
    return {
        "kind": rec.kind,
        "repo_id": rec.repo_id,
        "resolved_repo_identity": rec.resolved_repo_identity,
        "generation": int(rec.generation),
        "acquired_at": rec.acquired_at,
        "heartbeat_at": rec.heartbeat_at,
        "intent_uuid": rec.intent_uuid,
        "intent_key": intent_key,
        "session_key": rec.session_key,
        "action_id": rec.action_id,
        "operation_type": rec.operation_type,
        "transaction_id": rec.transaction_id,
        "observed_busy_state": rec.observed_busy_state,
        "orphaned": bool(orphaned),
    }


# --------------------------------------------------------------------------- #
# scheduler
# --------------------------------------------------------------------------- #


class RepoScheduler:
    """Async facade over the Storage lease primitives; one call, one ``to_thread``.

    Async because two of its inputs are: the host's in-memory slot state (loop thread only) and the
    activity log. Nothing here blocks: every SQLite call goes to a worker thread, and the host reads
    are attribute lookups on objects the gateway owns.
    """

    def __init__(
        self,
        storage: "Storage",
        host: "HostBridge",
        settings: "SettingsService",
        clock: "C.Clock",
        activity: "ActivityProjector",
        *,
        boot_id: str = "",
        busy_reasons: Callable[[Any], Sequence[str]] | None = None,
    ) -> None:
        self._storage = storage
        self._host = host
        self._settings = settings
        self._clock = clock
        self._activity = activity
        # Stamped on every admin lease this process takes, and compared in `_prove_admin`. Defaulted so
        # a test can build a scheduler without one; an empty boot id simply proves nothing, which is the
        # safe direction (the lease is reported as orphaned instead of being reclaimed).
        self._boot_id = str(boot_id or "")
        # `sessions.busy_reasons` is the one predicate this module borrows from another module. It is
        # injectable so the scheduler can be exercised (and reasoned about) without a host bridge, and
        # resolved lazily otherwise — importing sessions.py eagerly would make the lease module depend
        # on the host-facing module it is a peer of.
        self._busy_reasons = busy_reasons
        self._gate: asyncio.Lock | None = None
        self._gate_loop: asyncio.AbstractEventLoop | None = None

    # ---- acquisition -------------------------------------------------------

    async def acquire_execution(
        self,
        repo: "RepoRecord",
        *,
        intent_uuid: str | None,
        session_key: str | None,
        action_id: str,
    ) -> LeaseGrant:
        """Take the execution lease for a human-lane dispatch, or refuse.

        ``rate_limited`` counts only leases held for *other* identities: this repository can hold at
        most one lease anyway, so excluding it means a second submit against a busy repository reports
        the actionable ``repo_busy`` (with its owner) instead of a cap that is not really the problem.
        """
        identity = self._require_identity(repo)
        self._require_available(repo)
        async with self._serialised():
            # The count and the insert are one critical section: two submits for different
            # repositories would otherwise both read "cap not reached" and both acquire.
            await self._assert_cap(identity)
            rec = await self._acquire(
                "execution",
                identity,
                repo.repo_id,
                {"intent_uuid": intent_uuid, "session_key": session_key, "action_id": action_id},
            )
        await self._record(
            "lease.acquired",
            rec,
            params={"intent_uuid": intent_uuid},
        )
        return LeaseGrant.from_record(rec)

    async def acquire_admin(
        self, repo: "RepoRecord", *, operation_type: str, transaction_id: str | None
    ) -> LeaseGrant:
        """Take the admin lease for an operation that rewrites engine-owned files.

        Queues behind a live execution lease by raising ``LeaseHeld`` — never preempts it. An install
        that replaced framework files under a running turn would corrupt the very state the turn is
        deciding about, and the turn belongs to a human who is watching.

        ``transaction_id`` is optional because most admin operations genuinely have none: only install,
        upgrade and recovery open an ``install_transactions`` row. The alternative — minting an id that
        names no row — would make the lease look transactional to every reader of it, which is worse than
        an honest ``None``. Liveness for the non-transactional operations is proven from ``boot_id``
        instead (see ``_prove_admin``); contracts.md §1.10 types this parameter ``str`` and this is the
        documented difference.

        Not capped: the global concurrency cap bounds *turns* (model spend, FR-SES-007), and an admin
        operation is Studio's own file work. Availability is the caller's policy (the installer refuses
        far earlier and with a better error), but the identity is structural — a lease row is keyed by it.
        """
        if operation_type not in ADMIN_OPERATION_TYPES:
            # A programmer error, not a user error: operation_type is chosen by the code path.
            raise ValueError(
                f"{operation_type!r} is not an admin operation type; expected one of {ADMIN_OPERATION_TYPES}"
            )
        identity = self._require_identity(repo)
        # No critical section: there is no count to straddle here, and Storage's own transaction is
        # what decides the execution↔admin exclusivity either way.
        rec = await self._acquire(
            "admin",
            identity,
            repo.repo_id,
            {
                "operation_type": operation_type,
                "transaction_id": transaction_id,
                "boot_id": self._boot_id,
            },
        )
        await self._record(
            "lease.acquired",
            rec,
            params={"operation_type": operation_type, "transaction_id": transaction_id},
        )
        return LeaseGrant.from_record(rec)

    def require_host_idle(self, repo: "RepoRecord") -> None:
        """Check external host turns after acquiring an admin lease and immediately before writing."""
        for view in self._host.slots_for_project(repo.canonical_path):
            reasons = self._reasons(view)
            if reasons is None or reasons:
                raise StudioError("repo_busy", "a host session may be executing in this repository",
                                  details={"repo_id": repo.repo_id, "slot_key": getattr(view, "key", None)})

    # ---- holder operations -------------------------------------------------

    async def heartbeat(
        self, grant: LeaseGrant, *, observed_busy_state: str | None = None
    ) -> None:
        """Refresh the lease's ``heartbeat_at``; ``StaleGeneration`` means the holder lost it.

        The generation is not moved (Storage's rule), so a holder keeps using the number it was granted.
        A caller that catches ``StaleGeneration`` must abandon its work rather than retry: somebody
        proved it dead and another owner may already be running.
        """
        await asyncio.to_thread(
            self._storage.lease_heartbeat,
            grant.kind,
            grant.resolved_repo_identity,
            grant.generation,
            observed_busy_state,
        )

    async def release(self, grant: LeaseGrant) -> None:
        """Give the lease back.

        A release whose generation no longer matches is **not** an error here: release runs from
        cleanup paths (`submit` releases the grant from every exception path, C31), and raising there
        would replace the failure the user actually needs to see with a bookkeeping one. The
        postcondition the caller wants — "I no longer hold this lease" — is already true, and Storage
        refused to delete a row that is now somebody else's. The fact is recorded on the activity row.
        """
        stale = False
        try:
            await asyncio.to_thread(
                self._storage.lease_release,
                grant.kind,
                grant.resolved_repo_identity,
                grant.generation,
            )
        except StaleGeneration:
            stale = True
        await self._activity.record(
            kind="lease.released",
            severity="info",
            repo_id=grant.repo_id or None,
            params={
                "lease_kind": grant.kind,
                "generation": int(grant.generation),
                "resolved_repo_identity": grant.resolved_repo_identity,
                "stale": stale,
            },
        )

    # ---- reads -------------------------------------------------------------

    async def get(self, identity: str) -> "LeaseRecord | None":
        """The lease held for one identity, of whichever kind holds it (they are exclusive)."""
        for kind in C.LEASE_KIND:
            rec = await asyncio.to_thread(self._storage.lease_get, kind, identity)
            if rec is not None:
                return rec
        return None

    async def list(self) -> list["LeaseRecord"]:
        return await asyncio.to_thread(self._storage.lease_list)

    def is_stale(self, rec: "LeaseRecord") -> bool:
        """No heartbeat within the stale window.

        Presentation (``LeaseView.orphaned``) and a *necessary* precondition for a reclaim attempt —
        never a sufficient one. See ``prove_reclaim``.
        """
        age = self._age_secs(rec)
        return age is None or age > C.LEASE_STALE_SECS

    # ---- reclaim -----------------------------------------------------------

    async def prove_reclaim(
        self, rec: "LeaseRecord", *, boundary: "StableBoundary | None"
    ) -> ReclaimProof:
        """Assemble the positive proof that this lease's owner is not executing.

        Execution leases need all of:

        * **(a)** the host is attached and the slot behind ``rec.session_key`` reports no busy reason
          at all (the same predicate ``submit`` step 5 dispatches on), or that slot is gone *and* the
          host says the session is not busy;
        * **(b)** the action is absent, settled, or already parked in
          ``DeliveryUncertain``/``ReconciliationRequired`` — **or (b')** it never left
          ``Queued``/``NotDelivered``, so nothing was ever handed to the host (``never_dispatched``);
        * **(c)** a stable boundary, waived for (b') — a dispatch that never happened cannot have moved
          the boundary — and for (b'') below.
        * **(b'')** the action is settled *and* still carries this lease's own ``lease_generation``
          (``release_lost``): the holder got as far as ``deliver_under_lease`` and then died before its
          release, so the lease is debris. This waives (c) too, because requiring a boundary here
          deadlocks the repository — see the branch's own comment in ``_prove_execution``.

        Admin leases need only their transaction to have settled: the operation is Studio's own file
        work, so its transaction record — not the host — is the evidence.

        On top of the contract's conditions the owner must also be **stale** (no heartbeat within
        ``LEASE_STALE_SECS``), which is what that constant exists for: it gates the reclaim *attempt*.
        Without it the reconciler would sabotage healthy submits — between ``acquire_execution`` and the
        ``Delivering`` CAS a submit runs a cursor-switch subprocess, so for a few seconds a perfectly
        live submit looks exactly like the (b') crash case (lease held, action still ``Queued``, slot
        idle because step 5 required it), and a tick every ``RECONCILE_INTERVAL_SECS`` would reclaim it
        out from under the user, turning their decision into ``lease_lost``. Staleness can only ever
        refuse a reclaim, so it cannot violate "elapsed time never authorises a reclaim" (FR-SES-007);
        the age is recorded as evidence, never as permission.
        """
        evidence: dict[str, Any] = {
            "lease_kind": rec.kind,
            "generation": int(rec.generation),
            "repo_id": rec.repo_id,
            "heartbeat_at": rec.heartbeat_at,
            # Recorded so a human can see what the age *was*; it is not part of any grant.
            "heartbeat_age_secs": self._age_secs(rec),
            "stale_after_secs": int(C.LEASE_STALE_SECS),
        }
        refusals: list[str] = []
        if not self.is_stale(rec):
            refusals.append("owner_recent")

        if rec.kind == "admin":
            reason, more = await self._prove_admin(rec, evidence)
        else:
            reason, more = await self._prove_execution(rec, boundary, evidence)
        refusals.extend(more)

        ok = not refusals and reason is not None
        return ReclaimProof(ok=ok, reason=reason or "", refusals=tuple(refusals), evidence=evidence)

    async def try_reclaim(
        self, rec: "LeaseRecord", *, boundary: "StableBoundary | None"
    ) -> bool:
        """Reclaim the lease when — and only when — ``prove_reclaim`` is satisfied.

        The ``lease.reclaimed`` activity row is written by ``Storage.lease_reclaim`` inside the same
        transaction as the delete, so the proof can never be missing from the record; this method adds
        no second row. A ``StaleGeneration`` means the lease moved between the proof and the delete —
        somebody else already resolved it, so the answer is ``False`` and nothing is retried.
        """
        proof = await self.prove_reclaim(rec, boundary=boundary)
        if not proof.ok:
            return False
        guard = RECLAIM_ACTION_GUARD.get(proof.reason)
        try:
            await asyncio.to_thread(
                self._storage.lease_reclaim,
                rec.kind,
                rec.resolved_repo_identity,
                int(rec.generation),
                reason=proof.reason,
                evidence=proof.evidence,
                # Re-assert the action half of the proof under the delete's own transaction: a submit can
                # reach `Delivering` in the window between the two, and the lease generation does not move
                # when it does.
                expect_action=None if guard is None else (rec.action_id, guard),
            )
        except StaleGeneration:
            return False
        return True

    async def startup_sweep(self) -> list["LeaseRecord"]:
        """Try to clear leases left behind by a previous process; return the ones that stay.

        Deliberately passes ``boundary=None``: at startup Studio has no snapshot yet, and inventing one
        here would mean reading repositories before the reconciler's own ordered startup does. So the
        sweep clears exactly what needs no boundary — never-dispatched execution leases (the crash
        window of C31), leases whose settled action still carries their own generation (``release_lost``,
        which is precisely the debris a crashed process leaves and therefore what a *startup* sweep is
        for) and settled admin leases — and reports everything else as orphaned for ``/leases``.
        Boundary-dependent reclaims belong to the reconciler tick, which owns snapshots.
        """
        orphans: list["LeaseRecord"] = []
        for rec in await self.list():
            if not await self.try_reclaim(rec, boundary=None):
                orphans.append(rec)
        return orphans

    # ---- proof helpers -----------------------------------------------------

    async def _prove_execution(
        self,
        rec: "LeaseRecord",
        boundary: "StableBoundary | None",
        evidence: dict[str, Any],
    ) -> tuple[str | None, list[str]]:
        refusals: list[str] = []
        action_row = await self._row("actions", rec.action_id)
        refusals.extend(await self._prove_session_idle(rec, action_row, evidence))

        status = None if action_row is None else str(action_row.get("status") or "")
        evidence["action_id"] = rec.action_id
        evidence["action_status"] = status
        reason: str | None = None
        needs_boundary = True
        if rec.action_id is None:
            reason = "action_absent"
        elif action_row is None:
            # The row is gone (a purge, or an id that never existed): nothing can dispatch it.
            reason = "action_absent"
        elif status in NEVER_DISPATCHED_ACTION_STATUS:
            reason, needs_boundary = "never_dispatched", False
        elif status in SETTLED_ACTION_STATUS:
            reason = "action_terminal"
            stamp = _generation_of(action_row)
            evidence["action_lease_generation"] = stamp
            if stamp is not None and stamp == int(rec.generation):
                # The holder's own receipt. `Storage.deliver_under_lease` writes
                # `actions.lease_generation` inside the very transaction that checks this lease row, so a
                # settled action stamped with THIS generation says the turn the lease was taken for is
                # over and the release that should have followed it never ran — the lease is the debris
                # of a crash, not the guard of a turn. The waiver therefore rests on the holder's own
                # authority (`Storage.lease_release` accepts exactly this generation), not on elapsed
                # time, so FR-SES-007 still holds; staleness is still required to even attempt it.
                #
                # Without it the repository deadlocks. The lease refuses every dispatch (`_acquire`
                # raises `LeaseHeld` and never reclaims inline), only a dispatch can carry the intent to
                # its next boundary, and condition (c) demands a boundary that can therefore never come.
                # Observed live: one gate approval whose gateway died mid-turn left a repository unable
                # to run anything for 18 hours with no in-product way out.
                reason, needs_boundary = "release_lost", False
        elif status in UNCERTAIN_ACTION_STATUS:
            reason = "action_uncertain"
        elif status in IN_FLIGHT_ACTION_STATUS:
            refusals.append("action_in_flight")
        else:
            # `Draft`, or a status this version does not know: unreadable evidence refuses.
            refusals.append("action_status_unknown")

        if needs_boundary:
            stable = getattr(boundary, "stable", None) if boundary is not None else None
            evidence["boundary_stable"] = stable
            evidence["boundary_reasons"] = list(getattr(boundary, "reasons", ()) or ())
            if boundary is None:
                refusals.append("boundary_unknown")
            elif stable is not True:
                refusals.append("boundary_unstable")
        else:
            evidence["boundary_required"] = False

        return reason, refusals

    async def _prove_session_idle(
        self, rec: "LeaseRecord", action_row: Mapping[str, Any] | None, evidence: dict[str, Any]
    ) -> list[str]:
        """Condition (a): the host must positively say nothing is happening in that session."""
        evidence["session_key"] = rec.session_key
        try:
            attached = bool(self._host.attached())
        except (AttributeError, TypeError):
            attached = False
        evidence["host_attached"] = attached
        if not attached:
            # Disk evidence alone cannot see a running turn (02 §4.4): the transcript and the busy
            # predicates live in host memory only.
            return ["host_unattached"]
        if not rec.session_key:
            return ["session_unknown"]

        try:
            view = await self._owning_slot(rec, action_row)
        except (AttributeError, TypeError, KeyError):
            return ["busy_state_unknown"]
        evidence["slot_present"] = view is not None
        if view is not None:
            evidence["slot_key"] = getattr(view, "key", None)
            reasons = self._reasons(view)
            if reasons is None:
                return ["busy_state_unknown"]
            evidence["busy_reasons"] = list(reasons)
            return ["slot_busy"] if reasons else []

        try:
            busy = self._host.is_busy(rec.session_key)
        except (AttributeError, TypeError, KeyError):
            busy = None
        evidence["session_busy"] = busy
        if busy is None:
            return ["session_busy_unknown"]
        return ["session_busy"] if busy else []

    async def _owning_slot(self, rec: "LeaseRecord", action_row: Mapping[str, Any] | None) -> Any:
        """The slot whose effective session key is the lease's, or ``None`` when it is gone.

        Looked up through the repository's project path rather than by rebuilding ``dashboard:<slot>``
        from the session key: a session key may be a *linked* one (the host folds a slot into another
        session), and a taken-over slot need not carry Studio's canonical name at all — both cases
        would silently look like "the slot is gone", which is the weaker branch of condition (a).
        """
        canonical = ""
        repo_row = await self._row("repos", rec.repo_id)
        if repo_row:
            canonical = str(repo_row.get("canonical_path") or "")
        if canonical:
            for view in self._host.slots_for_project(canonical) or ():
                if getattr(view, "session_key", None) == rec.session_key:
                    return view
        slot_key = str((action_row or {}).get("slot_key") or "")
        if slot_key:
            return self._host.slot(slot_key)
        return None

    async def _prove_admin(
        self, rec: "LeaseRecord", evidence: dict[str, Any]
    ) -> tuple[str | None, list[str]]:
        evidence["operation_type"] = rec.operation_type
        evidence["transaction_id"] = rec.transaction_id
        evidence["boot_id"] = rec.boot_id
        evidence["live_boot_id"] = self._boot_id

        # A transaction, when there is one, is the strongest proof available: it records that the
        # operation reached an end, so a reclaim cannot land in the middle of a rollback.
        if rec.transaction_id:
            row = await self._row("install_transactions", rec.transaction_id)
            if row is not None:
                status = str(row.get("status") or "")
                evidence["transaction_status"] = status
                if status in TRANSACTION_SETTLED_STATUS:
                    return "admin_transaction_settled", []
                # An unsettled transaction from a previous boot is an abandoned one. Startup settles
                # those (`Installer.settle_abandoned`) before this sweep runs, so reaching here from a
                # dead boot means the settle has not happened yet — report it and wait rather than
                # reclaiming a lease whose file work may be half done.
                if self._owner_process_gone(rec):
                    return None, ["transaction_abandoned_pending_settle"]
                return None, ["transaction_in_progress"]
            evidence["transaction_status"] = None

        # No transaction row: either the operation type never had one (cursor_switch, doctor,
        # intent_create, recompose, scope_change, config_change all take the lease without inserting an
        # `install_transactions` row) or the row is gone. Before schema 2 this returned an unsatisfiable
        # refusal, so a crash during any of those operations left the repository permanently `repo_busy`
        # with no route able to clear it — no dispatch, no install, not even DELETE /repos/{id}.
        #
        # The proof that replaces it is still positive: the process that took this lease no longer
        # exists, because this process has a different boot id. It is never an age proof, so a slow
        # operation in a live process is never interrupted (FR-SES-007).
        if not rec.boot_id:
            # Written before schema 2, so there is nothing to compare. Left refusable on purpose: a
            # guess here would be a reclaim with no evidence at all.
            return None, ["admin_owner_unknown"]
        if not self._owner_process_gone(rec):
            return None, ["admin_owner_process_live"]
        return "admin_owner_process_gone", []

    def _owner_process_gone(self, rec: "LeaseRecord") -> bool:
        """Whether the process that took this lease is provably not this one.

        Both ids must be present and different. An empty live boot id (a scheduler built without one)
        proves nothing and must not be read as "different", or every lease would look abandoned.
        """
        return bool(rec.boot_id) and bool(self._boot_id) and rec.boot_id != self._boot_id

    # ---- plumbing ----------------------------------------------------------

    def _serialised(self) -> asyncio.Lock:
        """One lock, lazily bound to the loop that is actually running.

        The gateway has a single loop, so this is simply "the" lock; re-binding when the loop changes
        keeps the scheduler usable after a ``Services`` rebuild (and in tests, where each scenario runs
        its own loop) instead of raising "bound to a different event loop" from asyncio's mixin.
        """
        loop = asyncio.get_running_loop()
        if self._gate is None or self._gate_loop is not loop:
            self._gate = asyncio.Lock()
            self._gate_loop = loop
        return self._gate

    async def _acquire(
        self, kind: str, identity: str, repo_id: str, fields: Mapping[str, Any]
    ) -> "LeaseRecord":
        row = dict(fields)
        row["repo_id"] = repo_id
        try:
            return await asyncio.to_thread(self._storage.lease_acquire, kind, identity, row)
        except LeaseHeld as exc:
            raise self._as_lease_view(exc) from None

    def _as_lease_view(self, exc: LeaseHeld) -> LeaseHeld:
        """Re-raise Storage's ``LeaseHeld`` with ``details.owner`` in full ``LeaseView`` shape.

        Storage returns the raw row (it knows nothing about ``intent_key`` or staleness); the UI renders
        a ``LeaseView`` and would read ``undefined`` for the two missing keys.
        """
        owner = dict(exc.details.get("owner") or {})
        if owner:
            age = self._age_of(owner.get("heartbeat_at"))
            owner.setdefault("intent_key", None)
            owner.setdefault("orphaned", age is None or age > C.LEASE_STALE_SECS)
            # `boot_id` is this process's own identifier. It is evidence for `_prove_admin` and means
            # nothing to a person looking at "who holds this lease", so it stays out of the shape the UI
            # renders — `details.owner` must be exactly a `LeaseView` (§1.10) and nothing more.
            owner.pop("boot_id", None)
        details = dict(exc.details)
        details["owner"] = owner or None
        details["retry_after_secs"] = int(C.LEASE_HEARTBEAT_SECS)
        return LeaseHeld(exc.message, details=details)

    async def _assert_cap(self, identity: str) -> None:
        cap = self._cap()
        others = [
            rec
            for rec in await self.list()
            if rec.kind == "execution" and rec.resolved_repo_identity != identity
        ]
        if len(others) >= cap:
            raise StudioError(
                "rate_limited",
                "too many repositories are already running a turn",
                details={
                    "cap": cap,
                    "live_execution": len(others),
                    "repo_ids": sorted({rec.repo_id for rec in others if rec.repo_id}),
                    "retry_after_secs": int(C.LEASE_HEARTBEAT_SECS),
                },
            )

    def _cap(self) -> int:
        """The settings cap, floored at 1.

        ``SettingsService`` validates the range, so the floor only matters for a hand-edited
        preferences row: a cap of 0 would refuse every decision forever, which is a worse failure than
        running one turn at a time.
        """
        value = int(self._settings.global_concurrency_cap())
        return value if value >= 1 else 1

    def _require_identity(self, repo: "RepoRecord") -> str:
        identity = repo.resolved_identity
        if not identity:
            raise StudioError(
                "identity_unprovable",
                "this repository has no provable identity, so no lease can be keyed to it",
                details={"repo_id": repo.repo_id, "availability": repo.availability},
            )
        return str(identity)

    def _require_available(self, repo: "RepoRecord") -> None:
        if repo.availability != "available":
            raise StudioError(
                "repo_unavailable",
                "the repository is not currently readable",
                details={
                    "repo_id": repo.repo_id,
                    "availability": repo.availability,
                    "availability_detail": repo.availability_detail,
                },
            )

    async def _record(
        self, kind: str, rec: "LeaseRecord", *, params: Mapping[str, Any]
    ) -> None:
        payload = {
            "lease_kind": rec.kind,
            "generation": int(rec.generation),
            "resolved_repo_identity": rec.resolved_repo_identity,
        }
        payload.update({k: v for k, v in params.items() if v is not None})
        await self._activity.record(
            kind=kind,
            severity="info",
            repo_id=rec.repo_id or None,
            action_id=rec.action_id,
            session_key=rec.session_key,
            params=payload,
        )

    async def _row(self, table: str, key: str | None) -> dict | None:
        if not key:
            return None
        return await asyncio.to_thread(self._storage.get, table, key)

    def _reasons(self, view: Any) -> tuple[str, ...] | None:
        """``sessions.busy_reasons(view)``, or ``None`` when the predicate cannot be evaluated.

        ``None`` is a refusal, not an empty answer: "we could not tell whether the slot is busy" must
        never read as "the slot is idle".
        """
        if self._busy_reasons is None:
            try:
                from .sessions import busy_reasons
            except Exception:  # pragma: no cover - sessions.py is always present in a real install
                return None
            self._busy_reasons = busy_reasons
        try:
            return tuple(self._busy_reasons(view))
        except (AttributeError, TypeError, KeyError):
            return None

    def _age_secs(self, rec: "LeaseRecord") -> int | None:
        age = self._age_of(rec.heartbeat_at)
        return None if age is None else int(age)

    def _age_of(self, heartbeat_at: Any) -> float | None:
        stamp = _epoch(heartbeat_at)
        if stamp is None:
            return None
        return max(0.0, self._clock.now() - stamp)


#: ``None`` for an unparseable stamp, which the callers above read as "unknown age" — and an unknown
#: age never authorises a reclaim. Shared with the rest of the backend (``constants.epoch_from_iso``).
_epoch = C.epoch_from_iso
