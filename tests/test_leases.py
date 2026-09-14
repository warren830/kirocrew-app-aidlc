"""Scheduling tests for ``backend/studio/leases.py``.

The property every other module trusts blindly: **a live owner is never preempted, and elapsed time
alone never authorises a reclaim.** So the reclaim tests are written as "one condition at a time is
missing → refuse", and only the case where every piece of proof is present reclaims. The refusal codes
are asserted, not just the boolean, because "refused for the wrong reason" is how a reclaim rule rots
into a time-based one.

``sessions.py`` is a peer module, so its ``busy_reasons`` predicate is injected here exactly as §1.11
specifies it (the scheduler resolves the real one lazily in production; the last test in this file
asserts that seam once the module exists).
"""

from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent

TS = "2026-09-04T10:00:00Z"
IDENTITY = "1:100"


# --------------------------------------------------------------------------- #
# module fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def L(studio):
    module = studio.leases
    assert module is not None, getattr(studio, "leases__error", "leases.py did not import")
    return module


@pytest.fixture
def C(studio):
    return studio.constants


@pytest.fixture
def errors(studio):
    return studio.errors


@pytest.fixture
def store(studio, fake_ctx, clock, ids):
    st = studio.storage.Storage(Path(fake_ctx.data_dir) / studio.constants.DB_FILENAME,
                                clock=clock, ids=ids)
    st.open()
    yield st
    st.close()


# --------------------------------------------------------------------------- #
# host / settings / activity stand-ins
# --------------------------------------------------------------------------- #

#: §1.11 verbatim. Local because ``sessions.py`` owns the real one and this module must be provable on
#: its own; ``test_default_busy_reasons_is_taken_from_sessions`` closes the loop when it lands.
BUSY_REASONS = ("running", "stage_execution", "stopping", "queued", "approval_pending", "subagents",
                "deliveries_inflight")


@dataclass
class View:
    """The ``SlotView`` fields ``busy_reasons`` and the scheduler read."""

    key: str
    session_key: str
    project: str = "/FIXTURE/repo/r_1"
    running: bool = False
    in_stage_execution: bool = False
    stop_state: str = "idle"
    stopping: bool = False
    queue_depth: int = 0
    approvals_pending: int = 0
    subagents_running: int = 0
    subagents_queued: int = 0
    deliveries_inflight: int = 0


def busy_reasons(view: View) -> tuple[str, ...]:
    out: list[str] = []
    if view.running:
        out.append("running")
    if view.in_stage_execution:
        out.append("stage_execution")
    if view.stop_state != "idle" or view.stopping:
        out.append("stopping")
    if view.queue_depth > 0:
        out.append("queued")
    if view.approvals_pending > 0:
        out.append("approval_pending")
    if view.subagents_running + view.subagents_queued > 0:
        out.append("subagents")
    if view.deliveries_inflight > 0:
        out.append("deliveries_inflight")
    return tuple(out)


class Bridge:
    """Only the four ``HostBridge`` reads ``RepoScheduler`` is allowed to make (§1.11)."""

    def __init__(self) -> None:
        self.is_attached = True
        self.views: dict[str, View] = {}
        self.busy: set[str] = set()
        self.busy_unknown = False       # the `sessions_busy` capability is unavailable
        self.shape_changed = False      # a host release renamed what we read

    def attached(self) -> bool:
        return self.is_attached

    def slots_for_project(self, canonical_path: str) -> list[View]:
        if self.shape_changed:
            raise AttributeError("state._slots")
        return [v for v in self.views.values() if v.project == canonical_path]

    def slot(self, slot_key: str) -> View | None:
        if self.shape_changed:
            raise AttributeError("get_slot")
        return self.views.get(slot_key)

    def is_busy(self, session_key: str) -> bool | None:
        return None if self.busy_unknown else session_key in self.busy


class Settings:
    def __init__(self, cap: int = 2) -> None:
        self.cap = cap

    def global_concurrency_cap(self) -> int:
        return self.cap


class Activity:
    """``ActivityProjector.record`` stand-in: keeps the kwargs so a test can read the audit trail."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def record(self, **kwargs) -> int:
        self.rows.append(kwargs)
        return len(self.rows)

    def kinds(self) -> list[str]:
        return [r["kind"] for r in self.rows]

    def of(self, kind: str) -> list[dict]:
        return [r for r in self.rows if r["kind"] == kind]


@pytest.fixture
def bridge() -> Bridge:
    return Bridge()


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def activity() -> Activity:
    return Activity()


#: The boot id the `sched` fixture runs under. A lease stamped with this is held by "this process";
#: anything else is a lease left behind by a process that is gone.
BOOT = "boot-live"


@pytest.fixture
def sched(L, store, bridge, settings, clock, activity):
    return L.RepoScheduler(
        store, bridge, settings, clock, activity, boot_id=BOOT, busy_reasons=busy_reasons
    )


# --------------------------------------------------------------------------- #
# row helpers
# --------------------------------------------------------------------------- #


def run(coro):
    return asyncio.run(coro)


_UNSET = object()


def repo(studio, store, repo_id: str = "r_1", *, identity=_UNSET, **over):
    """Insert a ``repos`` row and return the real ``RepoRecord`` the scheduler is called with."""
    row = {
        "id": repo_id,
        "resolved_identity": IDENTITY if identity is _UNSET else identity,
        "canonical_path": f"/FIXTURE/repo/{repo_id}",
        "label": repo_id,
        "added_at": TS,
        "platform": "darwin",
        "availability": "available",
    }
    row.update(over)
    store.insert("repos", row)
    return studio.repo_registry.RepoRecord.from_row(store.get("repos", repo_id))


def action(store, action_id: str = "a_1", *, status: str = "Queued", repo_id: str = "r_1", **over):
    row = {
        "action_id": action_id,
        "type": "gate",
        "risk_class": "human_lane",
        "repo_id": repo_id,
        "resolved_repo_identity": IDENTITY,
        "intent_dir": "260904-gate-demo",
        "space": "default",
        "status": status,
        "created_at": TS,
        "updated_at": TS,
        "waiting_since": TS,
        "source": "studio",
    }
    row.update(over)
    store.insert("actions", row)
    return action_id


async def _already(value):
    """An awaitable that yields a value already computed — a proof taken before the race."""
    return value


def stamp(store, generation, action_id: str = "a_1"):
    """Write `actions.lease_generation` the way `deliver_under_lease` does: under the row's own CAS."""
    row = store.get("actions", action_id)
    store.cas_update("actions", action_id, int(row["status_generation"]), {"lease_generation": generation})


def transaction(store, transaction_id: str = "tx_1", *, status: str = "written", repo_id: str = "r_1"):
    store.insert(
        "install_transactions",
        {
            "transaction_id": transaction_id,
            "repo_id": repo_id,
            "resolved_repo_identity": IDENTITY,
            "kind": "install",
            "status": status,
            "studio_version": "1.0.0",
            "engine_version": "2.6.2",
            "payload_digest": "d" * 64,
            "started_at": TS,
        },
    )
    return transaction_id


@dataclass(frozen=True)
class Boundary:
    """``StableBoundary`` stand-in (``projection.py`` owns the real one)."""

    stable: bool = True
    reasons: tuple[str, ...] = ()


def idle_slot(bridge: Bridge, *, session_key: str = "dashboard:slot-1", key: str = "slot-1",
              project: str = "/FIXTURE/repo/r_1", **over) -> View:
    view = View(key=key, session_key=session_key, project=project, **over)
    bridge.views[key] = view
    return view


def reclaimable(studio, store, bridge, clock, *, status: str = "StateChanged", slot: bool = False):
    """A repo + execution lease + action in ``status``, stale, with the session provably idle."""
    record = repo(studio, store)
    action(store, status=status, slot_key="slot-1")
    store.lease_acquire("execution", IDENTITY, {"repo_id": "r_1", "session_key": "dashboard:slot-1",
                                                "action_id": "a_1"})
    if slot:
        idle_slot(bridge)
    clock.advance(studio.constants.LEASE_STALE_SECS + 1)
    return record, store.lease_get("execution", IDENTITY)


# --------------------------------------------------------------------------- #
# exclusivity: execution ↔ admin
# --------------------------------------------------------------------------- #


def test_an_admin_lease_queues_behind_a_live_execution_owner_and_never_preempts(
    studio, store, sched, C, errors, clock, activity
):
    record = repo(studio, store)
    grant = run(sched.acquire_execution(record, intent_uuid="u-1", session_key="dashboard:slot-1",
                                        action_id="a_1"))

    with pytest.raises(errors.LeaseHeld) as exc:
        run(sched.acquire_admin(record, operation_type="install", transaction_id="tx_1"))
    assert exc.value.code == "repo_busy"
    assert exc.value.status == 409
    assert exc.value.details["retry_after_secs"] == int(C.LEASE_HEARTBEAT_SECS)
    owner = exc.value.details["owner"]
    assert owner["kind"] == "execution" and owner["session_key"] == "dashboard:slot-1"
    assert owner["orphaned"] is False

    # the live owner is untouched: same generation, same action, still there
    held = run(sched.get(IDENTITY))
    assert (held.kind, held.generation, held.action_id) == ("execution", grant.generation, "a_1")

    # ... and once it releases, the admin operation gets a *new* generation, never the old one
    run(sched.release(grant))
    admin = run(sched.acquire_admin(record, operation_type="install", transaction_id="tx_1"))
    assert admin.generation == grant.generation + 1
    assert activity.kinds() == ["lease.acquired", "lease.released", "lease.acquired"]


def test_an_execution_lease_queues_behind_a_live_admin_owner(studio, store, sched, C, errors):
    record = repo(studio, store)
    run(sched.acquire_admin(record, operation_type="upgrade", transaction_id="tx_1"))

    with pytest.raises(errors.LeaseHeld) as exc:
        run(sched.acquire_execution(record, intent_uuid="u-1", session_key="dashboard:slot-1",
                                    action_id="a_1"))
    assert exc.value.details["kind"] == "admin"
    assert exc.value.details["owner"]["operation_type"] == "upgrade"
    assert exc.value.details["owner"]["transaction_id"] == "tx_1"
    assert run(sched.get(IDENTITY)).kind == "admin"


def test_a_second_execution_lease_on_the_same_repo_is_refused(studio, store, sched, errors):
    record = repo(studio, store)
    run(sched.acquire_execution(record, intent_uuid="u-1", session_key="dashboard:slot-1",
                                action_id="a_1"))
    with pytest.raises(errors.LeaseHeld):
        run(sched.acquire_execution(record, intent_uuid="u-1", session_key="dashboard:slot-2",
                                    action_id="a_2"))
    assert run(sched.get(IDENTITY)).action_id == "a_1"


def test_lease_held_details_owner_is_a_complete_lease_view(studio, store, sched, L, errors, clock):
    """The UI renders ``details.owner`` as a ``LeaseView``; a missing key would render as blank."""
    record = repo(studio, store)
    run(sched.acquire_execution(record, intent_uuid="u-1", session_key="dashboard:slot-1",
                                action_id="a_1"))
    clock.advance(studio.constants.LEASE_STALE_SECS + 1)
    with pytest.raises(errors.LeaseHeld) as exc:
        run(sched.acquire_admin(record, operation_type="doctor", transaction_id="tx_9"))

    expected = set(L.lease_view(run(sched.get(IDENTITY))))
    assert set(exc.value.details["owner"]) == expected
    assert expected == {"kind", "repo_id", "resolved_repo_identity", "generation", "acquired_at",
                        "heartbeat_at", "intent_uuid", "intent_key", "session_key", "action_id",
                        "operation_type", "transaction_id", "observed_busy_state", "orphaned"}
    # `boot_id` is on the stored row (schema 2) but not in the view: it identifies Studio's own process,
    # which is evidence for a reclaim proof and noise to a person reading "who holds this lease".
    assert "boot_id" not in exc.value.details["owner"]
    assert exc.value.details["owner"]["orphaned"] is True     # nobody has heartbeat it in > 120 s
    assert exc.value.details["owner"]["intent_key"] is None   # only the handler can resolve it


# --------------------------------------------------------------------------- #
# global concurrency cap
# --------------------------------------------------------------------------- #


def test_the_global_cap_counts_execution_leases_across_repos(studio, store, sched, settings, errors):
    settings.cap = 2
    repos = [repo(studio, store, f"r_{n}", identity=f"1:{n}") for n in (1, 2, 3)]
    grants = [run(sched.acquire_execution(r, intent_uuid=None, session_key=f"dashboard:s{n}",
                                          action_id=f"a_{n}"))
              for n, r in enumerate(repos[:2], start=1)]

    with pytest.raises(errors.StudioError) as exc:
        run(sched.acquire_execution(repos[2], intent_uuid=None, session_key="dashboard:s3",
                                    action_id="a_3"))
    assert exc.value.code == "rate_limited" and exc.value.status == 429
    assert exc.value.details["cap"] == 2
    assert exc.value.details["live_execution"] == 2

    # an admin operation is not a turn: the cap does not apply to it
    run(sched.acquire_admin(repos[2], operation_type="doctor", transaction_id="tx_3"))
    # and freeing one turn frees one slot in the cap
    run(sched.release(grants[0]))
    assert run(sched.acquire_execution(repos[0], intent_uuid=None, session_key="dashboard:s1",
                                       action_id="a_1")).generation == 2


def test_a_busy_repo_reports_repo_busy_rather_than_the_cap(studio, store, sched, settings, errors):
    """With cap 1 the repository's own lease must not be counted against it — repo_busy is the truth."""
    settings.cap = 1
    record = repo(studio, store)
    run(sched.acquire_execution(record, intent_uuid=None, session_key="dashboard:s1", action_id="a_1"))
    with pytest.raises(errors.LeaseHeld):
        run(sched.acquire_execution(record, intent_uuid=None, session_key="dashboard:s1",
                                    action_id="a_2"))


def test_the_cap_holds_when_two_repos_are_acquired_concurrently(studio, store, sched, settings):
    """Counting and inserting must be one critical section, or both callers read "cap not reached"."""
    settings.cap = 1
    left = repo(studio, store, "r_1", identity="1:1")
    right = repo(studio, store, "r_2", identity="1:2")

    async def both():
        return await asyncio.gather(
            sched.acquire_execution(left, intent_uuid=None, session_key="dashboard:s1", action_id="a_1"),
            sched.acquire_execution(right, intent_uuid=None, session_key="dashboard:s2", action_id="a_2"),
            return_exceptions=True,
        )

    outcomes = run(both())
    codes = sorted(getattr(o, "code", "granted") for o in outcomes)
    assert codes == ["granted", "rate_limited"]
    assert len([r for r in store.lease_list() if r.kind == "execution"]) == 1


def test_a_corrupt_cap_still_lets_one_turn_run(studio, store, sched, settings):
    settings.cap = 0
    record = repo(studio, store)
    assert run(sched.acquire_execution(record, intent_uuid=None, session_key="dashboard:s1",
                                       action_id="a_1")).generation == 1


# --------------------------------------------------------------------------- #
# preconditions on the repository
# --------------------------------------------------------------------------- #


def test_an_unprovable_identity_or_unavailable_repo_gets_no_execution_lease(studio, store, sched, errors):
    unprovable = repo(studio, store, "r_1", identity=None, availability="identity_unprovable")
    with pytest.raises(errors.StudioError) as exc:
        run(sched.acquire_execution(unprovable, intent_uuid=None, session_key="dashboard:s1",
                                    action_id="a_1"))
    assert exc.value.code == "identity_unprovable"
    with pytest.raises(errors.StudioError) as admin:
        run(sched.acquire_admin(unprovable, operation_type="recovery", transaction_id="tx_1"))
    assert admin.value.code == "identity_unprovable"

    moved = repo(studio, store, "r_2", identity="1:2", availability="moved",
                 availability_detail="path gone")
    with pytest.raises(errors.StudioError) as gone:
        run(sched.acquire_execution(moved, intent_uuid=None, session_key="dashboard:s2",
                                    action_id="a_2"))
    assert gone.value.code == "repo_unavailable"
    assert gone.value.details["availability_detail"] == "path gone"
    assert store.lease_list() == []


def test_every_admin_operation_type_is_accepted_and_nothing_else_is(studio, store, sched, L):
    record = repo(studio, store)
    for index, operation in enumerate(L.ADMIN_OPERATION_TYPES, start=1):
        grant = run(sched.acquire_admin(record, operation_type=operation, transaction_id=f"tx_{index}"))
        assert grant.generation == index
        run(sched.release(grant))
    with pytest.raises(ValueError):
        run(sched.acquire_admin(record, operation_type="rewrite-everything", transaction_id="tx_x"))
    assert store.lease_list() == []


# --------------------------------------------------------------------------- #
# holder operations
# --------------------------------------------------------------------------- #


def test_heartbeat_carries_the_observed_busy_state_and_dies_with_the_generation(
    studio, store, sched, clock, errors
):
    record = repo(studio, store)
    grant = run(sched.acquire_execution(record, intent_uuid=None, session_key="dashboard:s1",
                                        action_id="a_1"))
    clock.advance(15)
    run(sched.heartbeat(grant, observed_busy_state="running"))
    beat = store.lease_get("execution", IDENTITY)
    assert (beat.heartbeat_at, beat.observed_busy_state, beat.generation) == (
        clock.iso(), "running", grant.generation)

    store.lease_reclaim("execution", IDENTITY, grant.generation, reason="never_dispatched", evidence={})
    with pytest.raises(errors.StaleGeneration):
        run(sched.heartbeat(grant))


def test_releasing_a_lease_that_was_already_reclaimed_is_recorded_not_raised(
    studio, store, sched, activity
):
    """`submit` releases from every exception path; raising here would hide the real failure."""
    record = repo(studio, store)
    grant = run(sched.acquire_execution(record, intent_uuid=None, session_key="dashboard:s1",
                                        action_id="a_1"))
    store.lease_reclaim("execution", IDENTITY, grant.generation, reason="never_dispatched", evidence={})
    other = run(sched.acquire_execution(record, intent_uuid=None, session_key="dashboard:s2",
                                        action_id="a_2"))

    run(sched.release(grant))                      # the old holder's cleanup
    assert activity.of("lease.released")[-1]["params"]["stale"] is True
    assert run(sched.get(IDENTITY)).generation == other.generation   # the new owner survived

    run(sched.release(other))
    assert activity.of("lease.released")[-1]["params"]["stale"] is False
    assert run(sched.get(IDENTITY)) is None


def test_get_returns_whichever_kind_holds_the_identity_and_list_shows_both(studio, store, sched):
    execution_repo = repo(studio, store, "r_1", identity="1:1")
    admin_repo = repo(studio, store, "r_2", identity="1:2")
    run(sched.acquire_execution(execution_repo, intent_uuid=None, session_key="dashboard:s1",
                                action_id="a_1"))
    run(sched.acquire_admin(admin_repo, operation_type="install", transaction_id="tx_1"))

    assert run(sched.get("1:1")).kind == "execution"
    assert run(sched.get("1:2")).kind == "admin"
    assert run(sched.get("1:404")) is None
    assert {(r.kind, r.resolved_repo_identity) for r in run(sched.list())} == {
        ("execution", "1:1"), ("admin", "1:2")}


# --------------------------------------------------------------------------- #
# reclaim: age is never authority
# --------------------------------------------------------------------------- #


def test_age_alone_never_authorises_a_reclaim(studio, store, sched, bridge, clock):
    """Everything except the action is in favour, the action is mid-flight: refuse, however old."""
    record, rec = reclaimable(studio, store, bridge, clock, status="Processing", slot=True)
    clock.advance(30 * 24 * 3600)

    proof = run(sched.prove_reclaim(rec, boundary=Boundary()))
    assert proof.ok is False and proof.refusals == ("action_in_flight",)
    assert proof.evidence["heartbeat_age_secs"] > studio.constants.LEASE_STALE_SECS
    assert run(sched.try_reclaim(rec, boundary=Boundary())) is False
    assert store.lease_get("execution", IDENTITY) is not None


def test_a_fresh_owner_is_never_reclaimed_even_with_a_full_proof(studio, store, sched, bridge, clock):
    """The submit-in-flight regression: between acquire and the Delivering CAS a healthy submit looks
    exactly like the crash case (lease held, action Queued, slot idle because step 5 demanded it)."""
    repo(studio, store)
    action(store, status="Queued")
    store.lease_acquire("execution", IDENTITY, {"repo_id": "r_1", "session_key": "dashboard:slot-1",
                                                "action_id": "a_1"})
    idle_slot(bridge)
    rec = store.lease_get("execution", IDENTITY)

    clock.advance(studio.constants.RECONCILE_INTERVAL_SECS)          # one reconciler tick later
    proof = run(sched.prove_reclaim(rec, boundary=None))
    assert proof.refusals == ("owner_recent",) and proof.reason == "never_dispatched"
    assert run(sched.try_reclaim(rec, boundary=None)) is False

    clock.advance(studio.constants.LEASE_STALE_SECS + 1)             # the owner really is gone
    assert run(sched.try_reclaim(rec, boundary=None)) is True


@pytest.mark.parametrize("busy", [
    {"running": True},
    {"in_stage_execution": True},
    {"stop_state": "soft_pending"},
    {"stopping": True},
    {"queue_depth": 1},
    {"approvals_pending": 1},
    {"subagents_running": 1},
    {"subagents_queued": 1},
    {"deliveries_inflight": 1},
])
def test_any_busy_reason_refuses_the_reclaim(studio, store, sched, bridge, clock, busy):
    record, rec = reclaimable(studio, store, bridge, clock)
    idle_slot(bridge, **busy)

    proof = run(sched.prove_reclaim(rec, boundary=Boundary()))
    assert proof.ok is False and "slot_busy" in proof.refusals
    assert proof.evidence["busy_reasons"] == list(busy_reasons(bridge.views["slot-1"]))
    assert run(sched.try_reclaim(rec, boundary=Boundary())) is False
    assert store.lease_get("execution", IDENTITY) is not None


def test_a_detached_host_cannot_prove_anything(studio, store, sched, bridge, clock):
    record, rec = reclaimable(studio, store, bridge, clock, slot=True)
    bridge.is_attached = False
    proof = run(sched.prove_reclaim(rec, boundary=Boundary()))
    assert proof.refusals == ("host_unattached",) and proof.evidence["host_attached"] is False
    assert run(sched.try_reclaim(rec, boundary=Boundary())) is False


def test_unreadable_host_state_refuses_like_a_running_turn(studio, store, sched, bridge, clock):
    record, rec = reclaimable(studio, store, bridge, clock, slot=True)
    bridge.shape_changed = True                     # a host release moved what we read
    assert run(sched.prove_reclaim(rec, boundary=Boundary())).refusals == ("busy_state_unknown",)

    bridge.shape_changed = False
    bridge.views.clear()                            # slot gone, but the busy capability is missing
    bridge.busy_unknown = True
    proof = run(sched.prove_reclaim(rec, boundary=Boundary()))
    assert proof.refusals == ("session_busy_unknown",) and proof.evidence["session_busy"] is None

    bridge.busy_unknown = False
    bridge.busy.add("dashboard:slot-1")             # slot gone but the session is still busy
    assert run(sched.prove_reclaim(rec, boundary=Boundary())).refusals == ("session_busy",)


def test_a_lease_without_a_session_cannot_be_proven_idle(studio, store, sched, bridge, clock, C):
    repo(studio, store)
    action(store, status="StateChanged")
    store.lease_acquire("execution", IDENTITY, {"repo_id": "r_1", "action_id": "a_1"})
    clock.advance(C.LEASE_STALE_SECS + 1)
    rec = store.lease_get("execution", IDENTITY)
    assert run(sched.prove_reclaim(rec, boundary=Boundary())).refusals == ("session_unknown",)


def test_the_owning_slot_is_found_through_the_repository_not_the_session_key_shape(
    studio, store, sched, bridge, clock
):
    """A taken-over slot keeps its own name and may carry a linked session key (§1.11)."""
    record, rec = reclaimable(studio, store, bridge, clock)
    store.cas_update("execution_leases", IDENTITY, rec.generation, {"session_key": "linked-42"})
    clock.advance(studio.constants.LEASE_STALE_SECS + 1)   # the CAS refreshed heartbeat_at
    rec = store.lease_get("execution", IDENTITY)
    idle_slot(bridge, key="someones-own-slot", session_key="linked-42", running=True)

    proof = run(sched.prove_reclaim(rec, boundary=Boundary()))
    assert proof.refusals == ("slot_busy",)
    assert proof.evidence["slot_key"] == "someones-own-slot"


# --------------------------------------------------------------------------- #
# reclaim: the boundary
# --------------------------------------------------------------------------- #


def test_an_absent_or_unstable_boundary_refuses_the_reclaim(studio, store, sched, bridge, clock):
    record, rec = reclaimable(studio, store, bridge, clock, slot=True)

    assert run(sched.prove_reclaim(rec, boundary=None)).refusals == ("boundary_unknown",)
    unstable = Boundary(stable=False, reasons=("state_unstable",))
    proof = run(sched.prove_reclaim(rec, boundary=unstable))
    assert proof.refusals == ("boundary_unstable",)
    assert proof.evidence["boundary_reasons"] == ["state_unstable"]
    assert run(sched.try_reclaim(rec, boundary=unstable)) is False


@pytest.mark.parametrize("status,reason", [
    ("StateChanged", "action_terminal"),
    ("ResolvedNoTransition", "action_terminal"),
    ("Cancelled", "action_terminal"),
    ("Failed", "action_terminal"),
    ("DeliveryUncertain", "action_uncertain"),
    ("ReconciliationRequired", "action_uncertain"),
])
def test_a_settled_or_uncertain_action_reclaims_with_a_stable_boundary(
    studio, store, sched, bridge, clock, status, reason
):
    record, rec = reclaimable(studio, store, bridge, clock, status=status, slot=True)
    proof = run(sched.prove_reclaim(rec, boundary=Boundary()))
    assert (proof.ok, proof.reason, proof.refusals) == (True, reason, ())
    assert run(sched.try_reclaim(rec, boundary=Boundary())) is True
    assert store.lease_get("execution", IDENTITY) is None


@pytest.mark.parametrize("status", ["StateChanged", "ResolvedNoTransition", "Cancelled", "Failed"])
def test_a_settled_action_stamped_with_this_lease_waives_the_boundary(
    studio, store, sched, bridge, clock, status
):
    """The deadlock this exists to break: a crashed holder leaves debris no boundary can ever unlock.

    `deliver_under_lease` writes `actions.lease_generation` inside the transaction that checks the lease
    row, so a settled action still carrying this generation is the holder's own receipt that its turn is
    over and its release never ran. Requiring a stable boundary there wedges the repository forever: the
    lease refuses every dispatch, and only a dispatch can carry the intent to its next boundary.
    """
    record, rec = reclaimable(studio, store, bridge, clock, status=status, slot=True)
    stamp(store, rec.generation)

    unstable = Boundary(stable=False, reasons=("not_at_boundary",))
    proof = run(sched.prove_reclaim(rec, boundary=unstable))
    assert (proof.ok, proof.reason, proof.refusals) == (True, "release_lost", ())
    assert proof.evidence["action_lease_generation"] == rec.generation
    assert proof.evidence["boundary_required"] is False
    # No boundary at all is the startup sweep, which is exactly when this debris is found.
    assert run(sched.prove_reclaim(rec, boundary=None)).ok is True
    assert run(sched.try_reclaim(rec, boundary=None)) is True
    assert store.lease_get("execution", IDENTITY) is None


def test_a_settled_action_from_an_earlier_lease_still_needs_a_boundary(
    studio, store, sched, bridge, clock
):
    """The waiver is the holder's receipt, not a licence for every terminal action.

    A stamp from an *earlier* generation says some previous turn ended; it says nothing about the turn
    this lease was taken for, so condition (c) stands.
    """
    record, rec = reclaimable(studio, store, bridge, clock, slot=True)
    stamp(store, int(rec.generation) - 1)

    proof = run(sched.prove_reclaim(rec, boundary=None))
    assert (proof.ok, proof.reason, proof.refusals) == (False, "action_terminal", ("boundary_unknown",))
    assert proof.evidence["action_lease_generation"] == int(rec.generation) - 1
    assert run(sched.prove_reclaim(rec, boundary=Boundary())).ok is True, "a stable boundary still works"


def test_an_unreadable_generation_stamp_refuses_rather_than_guesses(
    studio, store, sched, bridge, clock
):
    record, rec = reclaimable(studio, store, bridge, clock, slot=True)
    stamp(store, "not a number")

    proof = run(sched.prove_reclaim(rec, boundary=None))
    assert (proof.ok, proof.reason) == (False, "action_terminal")
    assert proof.evidence["action_lease_generation"] is None


@pytest.mark.parametrize("status", ["Queued", "NotDelivered"])
def test_never_dispatched_waives_the_boundary_only(studio, store, sched, bridge, clock, status):
    record, rec = reclaimable(studio, store, bridge, clock, status=status, slot=True)
    proof = run(sched.prove_reclaim(rec, boundary=None))
    assert (proof.ok, proof.reason) == (True, "never_dispatched")
    assert proof.evidence["boundary_required"] is False
    assert run(sched.try_reclaim(rec, boundary=None)) is True

    # the host proof is NOT waived: a busy slot still refuses
    store.lease_acquire("execution", IDENTITY, {"repo_id": "r_1", "session_key": "dashboard:slot-1",
                                                "action_id": "a_1"})
    clock.advance(studio.constants.LEASE_STALE_SECS + 1)
    bridge.views["slot-1"].running = True
    again = store.lease_get("execution", IDENTITY)
    assert run(sched.try_reclaim(again, boundary=None)) is False


def test_an_absent_action_row_reclaims_and_an_unknown_status_refuses(studio, store, sched, bridge, clock, C):
    repo(studio, store)
    store.lease_acquire("execution", IDENTITY, {"repo_id": "r_1", "session_key": "dashboard:slot-1"})
    idle_slot(bridge)
    clock.advance(C.LEASE_STALE_SECS + 1)
    rec = store.lease_get("execution", IDENTITY)
    proof = run(sched.prove_reclaim(rec, boundary=Boundary()))
    assert (proof.ok, proof.reason) == (True, "action_absent")

    store.lease_reclaim("execution", IDENTITY, rec.generation, reason=proof.reason, evidence={})
    action(store, "a_2", status="Draft")
    store.lease_acquire("execution", IDENTITY, {"repo_id": "r_1", "session_key": "dashboard:slot-1",
                                               "action_id": "a_2"})
    clock.advance(C.LEASE_STALE_SECS + 1)
    draft = store.lease_get("execution", IDENTITY)
    assert run(sched.prove_reclaim(draft, boundary=Boundary())).refusals == ("action_status_unknown",)


# --------------------------------------------------------------------------- #
# reclaim: bookkeeping and races
# --------------------------------------------------------------------------- #


def test_a_granted_reclaim_records_the_proof_exactly_once(studio, store, sched, bridge, clock, activity):
    record, rec = reclaimable(studio, store, bridge, clock, slot=True)
    assert run(sched.try_reclaim(rec, boundary=Boundary())) is True

    # Storage writes the row inside the delete's own transaction; the scheduler must not add a second
    rows, _ = store.activity_query(studio.storage.ActivityQuery(kind="lease.reclaimed"))
    assert len(rows) == 1 and activity.of("lease.reclaimed") == []
    params = rows[0].params
    assert params["reason"] == "action_terminal" and params["lease_kind"] == "execution"
    evidence = params["evidence"]
    assert evidence["host_attached"] is True and evidence["busy_reasons"] == []
    assert evidence["action_status"] == "StateChanged" and evidence["boundary_stable"] is True
    assert evidence["heartbeat_age_secs"] >= studio.constants.LEASE_STALE_SECS


@pytest.mark.parametrize("landed", ["Delivering", "Delivered", "Processing"])
def test_a_reclaim_refuses_when_the_action_dispatched_between_the_proof_and_the_delete(
    studio, store, sched, bridge, clock, errors, landed
):
    """The prove-then-delete window (C31): the lease generation does not move when a submit dispatches.

    `deliver_under_lease` writes `actions.status = Delivering` and repoints the lease's `action_id`
    without bumping `generation`, so a reclaim proven a moment earlier — while the action was still
    `Queued` and the slot still idle, which is exactly what step 5 requires — would pass a
    generation-only check and cut a decision that is already on the wire. A second submit would then take
    the freed lease and dispatch a second turn into the same AI-DLC workflow.
    """
    record, rec = reclaimable(studio, store, bridge, clock, status="Queued", slot=True)
    stale = run(sched.prove_reclaim(rec, boundary=None))
    assert (stale.ok, stale.reason) == (True, "never_dispatched"), "the proof was valid when it was taken"

    row = store.get("actions", "a_1")
    store.cas_update("actions", "a_1", int(row["status_generation"]), {"status": landed})
    sched.prove_reclaim = lambda *a, **k: _already(stale)  # the racing submit won after the proof

    assert run(sched.try_reclaim(rec, boundary=None)) is False
    held = store.lease_get("execution", IDENTITY)
    assert held is not None and held.generation == rec.generation, "the live dispatch keeps its lease"
    rows, _ = store.activity_query(studio.storage.ActivityQuery(kind="lease.reclaimed"))
    assert rows == [], "a refused reclaim records nothing"

    with pytest.raises(errors.StaleGeneration) as exc:
        store.lease_reclaim(
            "execution", IDENTITY, rec.generation,
            reason="never_dispatched", evidence={},
            expect_action=("a_1", studio.leases.NEVER_DISPATCHED_ACTION_STATUS),
        )
    assert exc.value.details["actual"] == landed


def test_the_action_guard_covers_absence_and_every_granted_reason(studio, store, sched, bridge, clock, L):
    """`action_absent` demands the row stay absent, and no granted reason may go unguarded."""
    record = repo(studio, store)
    store.lease_acquire("execution", IDENTITY, {"repo_id": "r_1", "session_key": "dashboard:slot-1",
                                                "action_id": "a_gone"})
    idle_slot(bridge)
    clock.advance(studio.constants.LEASE_STALE_SECS + 1)
    rec = store.lease_get("execution", IDENTITY)
    proof = run(sched.prove_reclaim(rec, boundary=Boundary()))
    assert (proof.ok, proof.reason) == (True, "action_absent")
    action(store, "a_gone", status="Delivering")          # the row appears after the proof
    sched.prove_reclaim = lambda *a, **k: _already(proof)
    assert run(sched.try_reclaim(rec, boundary=Boundary())) is False
    assert store.lease_get("execution", IDENTITY) is not None

    execution_reasons = {"never_dispatched", "action_absent", "action_terminal", "release_lost",
                        "action_uncertain"}
    assert execution_reasons <= set(L.RECLAIM_ACTION_GUARD), "an unguarded reason reopens the window"
    assert set(L.RECLAIM_ACTION_GUARD) <= set(L.RECLAIM_REASONS)


def test_a_reclaim_whose_lease_moved_underneath_it_changes_nothing(studio, store, sched, bridge, clock):
    record, rec = reclaimable(studio, store, bridge, clock, slot=True)
    store.lease_release("execution", IDENTITY, rec.generation)
    fresh = store.lease_acquire("execution", IDENTITY, {"repo_id": "r_1",
                                                        "session_key": "dashboard:slot-1",
                                                        "action_id": "a_1"})

    assert run(sched.try_reclaim(rec, boundary=Boundary())) is False    # rec is generation 1
    assert store.lease_get("execution", IDENTITY).generation == fresh.generation


# --------------------------------------------------------------------------- #
# reclaim: admin leases
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status,expected", [
    ("committed", True), ("rolled_back", True), ("recovery_required", True), ("failed", True),
    ("staged", False), ("leased", False), ("backed_up", False), ("written", False),
    ("merged", False), ("validated", False), ("rolling_back", False),
])
def test_an_admin_lease_is_reclaimable_only_once_its_transaction_settled(
    studio, store, sched, clock, C, status, expected
):
    repo(studio, store)
    transaction(store, status=status)
    store.lease_acquire("admin", IDENTITY, {"repo_id": "r_1", "operation_type": "install",
                                           "transaction_id": "tx_1"})
    clock.advance(C.LEASE_STALE_SECS + 1)
    rec = store.lease_get("admin", IDENTITY)

    proof = run(sched.prove_reclaim(rec, boundary=None))
    assert proof.ok is expected
    assert proof.evidence["transaction_status"] == status
    if expected:
        assert proof.reason == "admin_transaction_settled"
    else:
        assert proof.refusals == ("transaction_in_progress",)
    assert run(sched.try_reclaim(rec, boundary=None)) is expected


def test_an_admin_lease_from_a_dead_process_is_reclaimable_without_a_transaction(
    studio, store, sched, clock, C
):
    """Six of the nine admin operations open no install transaction, and a crash must not brick the repo.

    Before this, ``_prove_admin`` refused any lease it could not tie to a *settled* transaction, so a
    crash during ``cursor_switch``, ``doctor``, ``intent_create``, ``recompose``, ``scope_change`` or
    ``config_change`` left the repository permanently ``repo_busy``: no dispatch, no install, and not
    even ``DELETE /repos/{id}``. The proof that replaces it is still positive — the process that took
    the lease is gone — so it is never a reclaim on age alone (FR-SES-007).
    """
    repo(studio, store)
    store.lease_acquire("admin", IDENTITY, {"repo_id": "r_1", "operation_type": "cursor_switch",
                                           "boot_id": "boot-of-a-process-that-died"})
    clock.advance(C.LEASE_STALE_SECS + 1)

    proof = run(sched.prove_reclaim(store.lease_get("admin", IDENTITY), boundary=None))
    assert proof.ok is True
    assert proof.reason == "admin_owner_process_gone"
    assert proof.evidence["boot_id"] == "boot-of-a-process-that-died"
    assert run(sched.try_reclaim(store.lease_get("admin", IDENTITY), boundary=None)) is True
    assert store.lease_get("admin", IDENTITY) is None


def test_an_admin_lease_this_process_still_holds_is_never_reclaimed(studio, store, sched, clock, C):
    """Age is not evidence: a slow doctor run in a live process must not have its lease taken away."""
    repo(studio, store)
    store.lease_acquire("admin", IDENTITY, {"repo_id": "r_1", "operation_type": "doctor",
                                           "boot_id": BOOT})
    clock.advance(C.LEASE_STALE_SECS * 10)

    proof = run(sched.prove_reclaim(store.lease_get("admin", IDENTITY), boundary=None))
    assert proof.ok is False
    assert proof.refusals == ("admin_owner_process_live",)
    assert run(sched.try_reclaim(store.lease_get("admin", IDENTITY), boundary=None)) is False
    assert store.lease_get("admin", IDENTITY) is not None


def test_an_admin_lease_older_than_schema_2_has_no_owner_to_prove(studio, store, sched, clock, C):
    """A row written before ``boot_id`` existed carries no evidence, so it is reported, never guessed."""
    repo(studio, store)
    store.lease_acquire("admin", IDENTITY, {"repo_id": "r_1", "operation_type": "cursor_switch"})
    clock.advance(C.LEASE_STALE_SECS + 1)
    assert run(sched.prove_reclaim(store.lease_get("admin", IDENTITY),
                                   boundary=None)).refusals == ("admin_owner_unknown",)
    assert store.lease_get("admin", IDENTITY) is not None


def test_an_unsettled_transaction_from_a_dead_boot_waits_for_the_settle(studio, store, sched, clock, C):
    """Half-finished file work is not something a reclaim may walk into.

    Startup settles abandoned transactions (``Installer.settle_abandoned``) before the lease sweep runs,
    so an unsettled row from a dead boot means the settle has not happened yet. The honest answer is a
    distinct refusal that names the wait, not a reclaim.
    """
    repo(studio, store)
    transaction(store, status="backed_up")
    store.lease_acquire("admin", IDENTITY, {"repo_id": "r_1", "operation_type": "install",
                                           "transaction_id": "tx_1", "boot_id": "boot-dead"})
    clock.advance(C.LEASE_STALE_SECS + 1)
    proof = run(sched.prove_reclaim(store.lease_get("admin", IDENTITY), boundary=None))
    assert proof.ok is False
    assert proof.refusals == ("transaction_abandoned_pending_settle",)
    assert store.lease_get("admin", IDENTITY) is not None


def test_an_admin_reclaim_does_not_consult_the_host(studio, store, sched, bridge, clock, C):
    """An admin operation is Studio's own file work; its transaction is the whole evidence."""
    repo(studio, store)
    transaction(store, status="rolled_back")
    store.lease_acquire("admin", IDENTITY, {"repo_id": "r_1", "operation_type": "upgrade",
                                           "transaction_id": "tx_1"})
    clock.advance(C.LEASE_STALE_SECS + 1)
    bridge.is_attached = False
    idle_slot(bridge, running=True)
    assert run(sched.try_reclaim(store.lease_get("admin", IDENTITY), boundary=None)) is True


# --------------------------------------------------------------------------- #
# startup sweep
# --------------------------------------------------------------------------- #


def test_startup_sweep_clears_what_needs_no_boundary_and_reports_the_rest(
    studio, store, sched, bridge, clock, C
):
    for index, (repo_id, identity) in enumerate(
        [("r_1", "1:1"), ("r_2", "1:2"), ("r_3", "1:3"), ("r_4", "1:4")], start=1
    ):
        repo(studio, store, repo_id, identity=identity)
        idle_slot(bridge, key=f"slot-{index}", session_key=f"dashboard:slot-{index}",
                  project=f"/FIXTURE/repo/{repo_id}")
    action(store, "a_1", status="Queued")                     # crashed between acquire and Delivering
    action(store, "a_2", status="Processing", repo_id="r_2")  # a turn the host may still be running
    transaction(store, "tx_3", status="committed")            # an install that finished
    transaction(store, "tx_4", status="written")              # an install that did not
    store.lease_acquire("execution", "1:1", {"repo_id": "r_1", "session_key": "dashboard:slot-1",
                                             "action_id": "a_1"})
    store.lease_acquire("execution", "1:2", {"repo_id": "r_2", "session_key": "dashboard:slot-2",
                                             "action_id": "a_2"})
    store.lease_acquire("admin", "1:3", {"repo_id": "r_3", "operation_type": "install",
                                         "transaction_id": "tx_3"})
    store.lease_acquire("admin", "1:4", {"repo_id": "r_4", "operation_type": "install",
                                         "transaction_id": "tx_4"})
    clock.advance(C.LEASE_STALE_SECS + 1)

    orphans = run(sched.startup_sweep())
    assert [(r.kind, r.resolved_repo_identity) for r in orphans] == [
        ("execution", "1:2"), ("admin", "1:4")]
    assert {r.resolved_repo_identity for r in store.lease_list()} == {"1:2", "1:4"}
    rows, _ = store.activity_query(studio.storage.ActivityQuery(kind="lease.reclaimed"))
    assert sorted(r.params["reason"] for r in rows) == ["admin_transaction_settled", "never_dispatched"]


def test_is_stale_only_looks_at_the_heartbeat_and_feeds_the_orphaned_flag(
    studio, store, sched, L, clock, C
):
    repo(studio, store)
    store.lease_acquire("execution", IDENTITY, {"repo_id": "r_1", "session_key": "dashboard:slot-1"})
    rec = store.lease_get("execution", IDENTITY)
    assert sched.is_stale(rec) is False
    assert L.lease_view(rec, orphaned=sched.is_stale(rec))["orphaned"] is False

    clock.advance(C.LEASE_STALE_SECS + 1)
    beaten = store.lease_heartbeat("execution", IDENTITY, rec.generation, "running")
    assert sched.is_stale(beaten) is False        # a heartbeat is all it takes to stay alive
    clock.advance(C.LEASE_STALE_SECS + 1)
    assert sched.is_stale(store.lease_get("execution", IDENTITY)) is True
    assert L.lease_view(rec, intent_key="260904-gate-demo", orphaned=True)["intent_key"] == \
        "260904-gate-demo"


# --------------------------------------------------------------------------- #
# structural guarantees
# --------------------------------------------------------------------------- #


def test_no_storage_call_runs_on_the_event_loop():
    """Every SQLite call must be *handed* to ``asyncio.to_thread``, never invoked inline (§0.6)."""
    tree = ast.parse((APP_ROOT / "backend" / "studio" / "leases.py").read_text("utf-8"))
    inline = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "_storage"
    ]
    assert inline == []


def test_the_real_settings_activity_and_boundary_objects_fit_the_scheduler(studio, store, bridge, clock, L):
    """Wiring check against the real collaborators: constructor order, sync cap accessor, activity
    kinds and ``StableBoundary``. The rest of this file injects stand-ins to isolate the scheduler."""
    for name in ("settings", "activity", "projection"):
        if getattr(studio, name, None) is None:
            pytest.skip(f"{name}.py not implemented yet")
    service = studio.settings.SettingsService(store, bridge, clock)
    projector = studio.activity.ActivityProjector(store, clock)
    scheduler = L.RepoScheduler(store, bridge, service, clock, projector, busy_reasons=busy_reasons)

    record = repo(studio, store)
    action(store, status="StateChanged", slot_key="slot-1")
    idle_slot(bridge)
    run(scheduler.acquire_execution(record, intent_uuid="u-1", session_key="dashboard:slot-1",
                                    action_id="a_1"))
    assert service.global_concurrency_cap() == 2                     # DEFAULTS, before any load()

    boundary = studio.projection.StableBoundary(stable=True, reasons=(), stage="requirements-analysis",
                                                marker="[?]", boundary_token="tok", recorded_at=TS)
    clock.advance(studio.constants.LEASE_STALE_SECS + 1)
    assert run(scheduler.try_reclaim(store.lease_get("execution", IDENTITY), boundary=boundary)) is True
    rows, _ = store.activity_query(studio.storage.ActivityQuery())
    assert [r.kind for r in rows] == ["lease.reclaimed", "lease.acquired"]   # newest first
    assert rows[-1].params["intent_uuid"] == "u-1" and rows[-1].message_key == "activity.lease.acquired"


def test_the_settled_transaction_statuses_exist_in_the_installers_own_tuple(studio, L):
    installer = getattr(studio, "installer", None)
    if installer is None or not hasattr(installer, "TransactionStatus"):
        pytest.skip("installer.py not implemented yet")
    assert L.TRANSACTION_SETTLED_STATUS <= set(installer.TransactionStatus)


def test_the_local_busy_predicate_speaks_the_pinned_vocabulary():
    """The stand-in above must not invent a reason code the real predicate does not have (§1.11)."""
    every = View(key="k", session_key="dashboard:k", running=True, in_stage_execution=True,
                 stop_state="killing", queue_depth=1, approvals_pending=1, subagents_running=1,
                 deliveries_inflight=1)
    assert busy_reasons(every) == BUSY_REASONS
    assert busy_reasons(View(key="k", session_key="dashboard:k")) == ()


def test_default_busy_reasons_is_taken_from_sessions(studio, L, store, bridge, settings, clock, activity):
    sessions = getattr(studio, "sessions", None)
    if sessions is None or not hasattr(sessions, "busy_reasons"):
        pytest.skip("sessions.py not implemented yet")
    assert sessions.BUSY_REASONS == BUSY_REASONS
    scheduler = L.RepoScheduler(store, bridge, settings, clock, activity)
    assert scheduler._reasons(View(key="k", session_key="dashboard:k")) == ()
    assert scheduler._busy_reasons is sessions.busy_reasons


def test_every_reason_and_refusal_this_module_can_emit_is_declared(studio, L):
    """The two tuples are the contract for these codes; an undeclared one reaches the UI as a raw string.

    Checked by scanning the module for the literals it hands to `ReclaimProof`, so a new proof path has to
    declare its code rather than only returning it.
    """
    import ast
    import pathlib

    source = pathlib.Path(L.__file__).read_text("utf-8")
    tree = ast.parse(source)
    declared = set(L.RECLAIM_REASONS) | set(L.RECLAIM_REFUSALS)

    emitted: set[str] = set()
    for node in ast.walk(tree):
        # `return "reason", []` and `return None, ["refusal"]` are the two shapes `_prove_*` uses.
        if not isinstance(node, ast.Return) or not isinstance(node.value, ast.Tuple):
            continue
        for element in node.value.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                emitted.add(element.value)
            elif isinstance(element, ast.List):
                for item in element.elts:
                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                        emitted.add(item.value)

    assert emitted, "the scan found nothing, so it has stopped working"
    assert emitted <= declared, sorted(emitted - declared)
