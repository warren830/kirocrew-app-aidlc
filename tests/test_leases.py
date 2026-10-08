"""Scheduling tests for ``backend/studio/leases.py``.

The property every other module trusts: **one turn per repository, and no turn while an admin operation
rewrites its files** — with nothing that can outlive its owner. An execution lease is an in-flight action,
so these tests drive action statuses; everything else is a per-repository ``flock`` held by a grant, so a
second scheduler on the same data dir stands in for another process, and a forked child that dies holding
the lock proves the kernel gives it back.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import fork_process

APP_ROOT = Path(__file__).resolve().parent.parent

TS = "2026-09-04T10:00:00Z"
IDENTITY = "1:100"


# --------------------------------------------------------------------------- #
# fixtures and stand-ins
# --------------------------------------------------------------------------- #


@pytest.fixture
def L(studio):
    module = studio.leases
    assert module is not None, getattr(studio, "leases__error", "leases.py did not import")
    return module


@pytest.fixture
def errors(studio):
    return studio.errors


@pytest.fixture
def store(studio, fake_ctx, clock, ids):
    st = studio.storage.Storage(Path(fake_ctx.data_dir) / studio.constants.DB_FILENAME,
                                clock=clock)
    st.open()
    yield st
    st.close()


@dataclass
class View:
    """The two ``SlotView`` fields ``require_host_idle`` reads, plus the busy reasons it is told about."""

    key: str
    project: str
    reasons: tuple[str, ...] = ()


def busy(view: View) -> tuple[str, ...]:
    return view.reasons


class Bridge:
    """The one ``HostBridge`` read ``RepoScheduler`` makes."""

    def __init__(self) -> None:
        self.views: list[View] = []

    def slots_for_project(self, canonical_path: str) -> list[View]:
        return [v for v in self.views if v.project == canonical_path]


class Settings:
    def __init__(self, cap: int = 2) -> None:
        self.cap = cap

    def global_concurrency_cap(self) -> int:
        return self.cap


class Activity:
    """``ActivityProjector.record`` stand-in: keeps the kwargs so a test can read the audit trail."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.fail = False

    async def record(self, **kwargs) -> int:
        if self.fail:
            raise RuntimeError("activity store unavailable")
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


@pytest.fixture
def sched(L, store, bridge, settings, clock, activity):
    return L.RepoScheduler(store, bridge, settings, clock, activity, busy_reasons=busy)


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


def action(store, action_id: str = "a_1", *, status: str = "Delivered", repo_id: str = "r_1",
           identity: str = IDENTITY, **over):
    row = {
        "action_id": action_id,
        "type": "gate",
        "risk_class": "human_lane",
        "repo_id": repo_id,
        "resolved_repo_identity": identity,
        "intent_dir": "260904-gate-demo",
        "space": "default",
        "status": status,
        "created_at": TS,
        "updated_at": TS,
        "waiting_since": TS,
        "source": "studio",
        "lease_generation": 7,   # stamped by the submit CAS: dispatched under an execution grant
    }
    row.update(over)
    store.insert("actions", row)
    return action_id


def settle(store, action_id: str = "a_1", status: str = "StateChanged") -> None:
    row = store.get("actions", action_id)
    store.cas_update("actions", action_id, int(row["status_generation"]), {"status": status})


def turn(sched, record, action_id: str = "a_1", **over):
    """Acquire for one submit, the way ``HumanActionBroker.submit`` does."""
    fields = {"intent_uuid": None, "session_key": "dashboard:slot-1", "action_id": action_id, **over}
    return run(sched.acquire_execution(record, **fields))


# --------------------------------------------------------------------------- #
# an in-flight action is the execution lease
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", ["Delivering", "Delivered"])
def test_an_in_flight_action_is_the_execution_lease_and_blocks_admin_work(
    studio, store, sched, L, errors, status
):
    record = repo(studio, store)
    action(store, status=status, delivering_at="2026-09-04T09:59:00Z", lease_generation=42,
           session_key="dashboard:slot-1", intent_uuid="u-1")

    held = run(sched.get(IDENTITY))
    assert L.lease_view(held, intent_key="default/260904-gate-demo") == {
        "kind": "execution", "repo_id": "r_1", "resolved_repo_identity": IDENTITY, "generation": 42,
        "acquired_at": "2026-09-04T09:59:00Z", "heartbeat_at": "2026-09-04T09:59:00Z",
        "intent_uuid": "u-1", "intent_key": "default/260904-gate-demo", "session_key": "dashboard:slot-1",
        "action_id": "a_1", "operation_type": None, "transaction_id": None, "observed_busy_state": None,
        "orphaned": False,
    }
    with pytest.raises(errors.LeaseHeld) as exc:
        run(sched.acquire_admin(record, operation_type="install", transaction_id="tx_1"))
    assert exc.value.code == "repo_busy" and exc.value.status == 409
    assert exc.value.details == {"kind": "execution", "owner": L.lease_view(held),
                                 "retry_after_secs": L.RETRY_AFTER_SECS}


def test_the_action_leaving_flight_is_the_release(studio, store, sched, errors):
    record = repo(studio, store)
    action(store, status="Delivered")
    with pytest.raises(errors.LeaseHeld):
        run(sched.acquire_admin(record, operation_type="upgrade", transaction_id="tx_1"))

    settle(store)
    assert run(sched.get(IDENTITY)) is None
    run(sched.release(run(sched.acquire_admin(record, operation_type="upgrade", transaction_id="tx_1"))))


@pytest.mark.parametrize("status,generation", [
    *[(status, 7) for status in ("Queued", "NotDelivered", "ReconciliationRequired",
                                 "StateChanged", "ResolvedNoTransition", "Failed", "Cancelled")],
    ("Delivering", None),   # a force stop: in flight, but the host-control lane takes no lease
])
def test_an_action_out_of_flight_holds_nothing(studio, store, sched, status, generation):
    """Uncertain rows included: what keeps a second turn out is the host-idle check every grant makes."""
    record = repo(studio, store)
    action(store, status=status, lease_generation=generation)
    assert run(sched.list()) == []
    run(sched.release(turn(sched, record, action_id="a_2")))


@pytest.mark.parametrize("kind", ["execution", "admin"])
def test_a_turn_running_in_any_session_of_the_repository_refuses_every_grant(
    studio, store, sched, bridge, errors, kind
):
    """An uncertain delivery holds no lease, yet its turn may still be running, maybe in another session.
    The cursor is repository-wide, so no grant may switch it or rewrite engine files under that turn."""
    record = repo(studio, store)
    action(store, status="ReconciliationRequired", slot_key="slot-other")
    bridge.views.append(View("slot-other", record.canonical_path, reasons=("running",)))

    def grant():
        if kind == "execution":
            return turn(sched, record, action_id="a_2")
        return run(sched.acquire_admin(record, operation_type="install", transaction_id="tx_1"))

    with pytest.raises(errors.StudioError) as exc:
        grant()
    assert exc.value.code == "repo_busy"
    assert exc.value.details == {"repo_id": "r_1", "slot_key": "slot-other"}
    assert run(sched.list()) == []                 # the refusal gave the file lock back

    bridge.views[0].reasons = ()
    run(sched.release(grant()))


def test_a_repository_shows_its_newest_in_flight_action(studio, store, sched):
    repo(studio, store)
    action(store, "a_old", delivering_at="2026-09-04T09:00:00Z")
    action(store, "a_new", delivering_at="2026-09-04T09:30:00Z", status="Delivering")
    assert [(r.kind, r.action_id) for r in run(sched.list())] == [("execution", "a_new")]


# --------------------------------------------------------------------------- #
# grants: exclusivity, the submit window, the file lock
# --------------------------------------------------------------------------- #


def test_an_execution_grant_refuses_while_an_admin_operation_runs(studio, store, sched, errors):
    record = repo(studio, store)
    admin = run(sched.acquire_admin(record, operation_type="upgrade", transaction_id="tx_1"))

    with pytest.raises(errors.LeaseHeld) as exc:
        turn(sched, record)
    assert exc.value.details["kind"] == "admin"
    assert exc.value.details["owner"]["operation_type"] == "upgrade"
    assert exc.value.details["owner"]["transaction_id"] == "tx_1"
    assert run(sched.get(IDENTITY)).generation == admin.generation


def test_the_submit_window_blocks_a_second_submit_and_an_admin_operation(studio, store, sched, errors):
    """Between acquire and the ``Delivering`` CAS nothing is in flight yet: the held grant is the lease."""
    record = repo(studio, store)
    grant = turn(sched, record, intent_uuid="u-1")
    with pytest.raises(errors.LeaseHeld) as again:
        turn(sched, record, action_id="a_2")
    assert again.value.details["owner"]["action_id"] == "a_1"
    with pytest.raises(errors.LeaseHeld):
        run(sched.acquire_admin(record, operation_type="doctor", transaction_id=None))

    held = run(sched.get(IDENTITY))
    assert (held.kind, held.generation, held.intent_uuid) == ("execution", grant.generation, "u-1")
    assert held.heartbeat_at == held.acquired_at == TS


def test_another_in_flight_action_refuses_a_submit_but_its_own_row_does_not(studio, store, sched, errors):
    record = repo(studio, store)
    action(store, "a_other", status="Delivered")
    with pytest.raises(errors.LeaseHeld) as exc:
        turn(sched, record)
    assert exc.value.details["owner"]["action_id"] == "a_other"

    settle(store, "a_other")
    action(store, "a_1", status="Delivering")
    run(sched.release(turn(sched, record, action_id="a_1")))


def test_the_file_lock_refuses_a_second_holder_even_in_this_process(
    studio, store, sched, L, bridge, settings, clock, activity, fake_ctx, errors
):
    """``flock`` belongs to the open file, so a second scheduler is refused exactly like another process."""
    record = repo(studio, store)
    other = L.RepoScheduler(store, bridge, settings, clock, activity, busy_reasons=busy)
    grant = run(sched.acquire_admin(record, operation_type="doctor", transaction_id=None))
    name = hashlib.sha256(IDENTITY.encode("utf-8")).hexdigest()[:24]
    assert (Path(fake_ctx.data_dir) / "locks" / f"{name}.lock").is_file()

    with pytest.raises(errors.LeaseHeld) as exc:
        run(other.acquire_admin(record, operation_type="doctor", transaction_id=None))
    assert exc.value.details == {"kind": None, "owner": None, "retry_after_secs": L.RETRY_AFTER_SECS}

    run(sched.release(grant))
    run(other.release(run(other.acquire_admin(record, operation_type="doctor", transaction_id=None))))


def test_a_process_that_dies_holding_the_lock_leaves_nothing_held(studio, store, sched):
    record = repo(studio, store)

    def child():
        store.close()
        store.open()
        run(sched.acquire_admin(record, operation_type="install", transaction_id="tx_1"))
        os._exit(17)  # dies holding the lock: no release, no finally

    process = fork_process(child)
    process.start()
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join()
        pytest.fail("the child holding the lock did not exit")
    assert process.exitcode == 17, "the child never got the lock"

    assert run(sched.list()) == []
    run(sched.release(run(sched.acquire_admin(record, operation_type="recovery", transaction_id="tx_2"))))


def test_generations_increase_and_a_restart_does_not_reuse_one(
    studio, store, sched, L, bridge, settings, clock, activity
):
    record = repo(studio, store)
    seen = []
    for _ in range(3):
        grant = run(sched.acquire_admin(record, operation_type="doctor", transaction_id=None))
        seen.append(grant.generation)
        run(sched.release(grant))
    assert seen == sorted(set(seen)), "strictly increasing even when the clock stands still"

    clock.advance(1)
    restarted = L.RepoScheduler(store, bridge, settings, clock, activity, busy_reasons=busy)
    assert run(restarted.acquire_admin(record, operation_type="doctor", transaction_id=None)).generation > seen[-1]


# --------------------------------------------------------------------------- #
# global concurrency cap
# --------------------------------------------------------------------------- #


def test_the_global_cap_counts_repositories_with_a_turn_in_flight(studio, store, sched, settings, errors):
    settings.cap = 2
    repos = [repo(studio, store, f"r_{n}", identity=f"1:{n}") for n in (1, 2, 3)]
    action(store, "a_1", repo_id="r_1", identity="1:1")           # a turn on the wire
    window = turn(sched, repos[1], action_id="a_2")                # a submit before its CAS

    with pytest.raises(errors.StudioError) as exc:
        turn(sched, repos[2], action_id="a_3")
    assert exc.value.code == "rate_limited" and exc.value.status == 429
    assert exc.value.details == {"cap": 2, "live_execution": 2, "repo_ids": ["r_1", "r_2"],
                                 "retry_after_secs": studio.leases.RETRY_AFTER_SECS}

    # an admin operation is not a turn: the cap does not apply to it
    run(sched.release(run(sched.acquire_admin(repos[2], operation_type="doctor", transaction_id=None))))
    # and ending one turn frees one slot in the cap
    run(sched.release(window))
    run(sched.release(turn(sched, repos[2], action_id="a_3")))


def test_a_busy_repo_reports_repo_busy_rather_than_the_cap(studio, store, sched, settings, errors):
    """With cap 1 the repository's own lease must not be counted against it — repo_busy is the truth."""
    settings.cap = 1
    record = repo(studio, store)
    turn(sched, record)
    with pytest.raises(errors.LeaseHeld):
        turn(sched, record, action_id="a_2")


def test_the_cap_holds_when_two_repos_are_acquired_concurrently(studio, store, sched, settings):
    """Counting and granting are one critical section, or both callers read "cap not reached"."""
    settings.cap = 1
    left = repo(studio, store, "r_1", identity="1:1")
    right = repo(studio, store, "r_2", identity="1:2")

    async def both():
        return await asyncio.gather(
            sched.acquire_execution(left, intent_uuid=None, session_key="dashboard:s1", action_id="a_1"),
            sched.acquire_execution(right, intent_uuid=None, session_key="dashboard:s2", action_id="a_2"),
            return_exceptions=True,
        )

    codes = sorted(getattr(o, "code", "granted") for o in run(both()))
    assert codes == ["granted", "rate_limited"]
    assert len([r for r in sched.live() if r.kind == "execution"]) == 1


def test_a_corrupt_cap_still_lets_one_turn_run(studio, store, sched, settings):
    settings.cap = 0
    assert turn(sched, repo(studio, store)).generation > 0


# --------------------------------------------------------------------------- #
# preconditions and bookkeeping
# --------------------------------------------------------------------------- #


def test_an_unprovable_identity_or_unavailable_repo_gets_no_execution_lease(studio, store, sched, errors):
    unprovable = repo(studio, store, "r_1", identity=None, availability="identity_unprovable")
    with pytest.raises(errors.StudioError) as exc:
        turn(sched, unprovable)
    assert exc.value.code == "identity_unprovable"
    with pytest.raises(errors.StudioError) as admin:
        run(sched.acquire_admin(unprovable, operation_type="recovery", transaction_id="tx_1"))
    assert admin.value.code == "identity_unprovable"

    moved = repo(studio, store, "r_2", identity="1:2", availability="moved", availability_detail="path gone")
    with pytest.raises(errors.StudioError) as gone:
        turn(sched, moved, action_id="a_2")
    assert gone.value.code == "repo_unavailable"
    assert gone.value.details["availability_detail"] == "path gone"
    assert sched.live() == []


def test_every_admin_operation_type_is_accepted_and_nothing_else_is(studio, store, sched, L):
    record = repo(studio, store)
    for index, operation in enumerate(L.ADMIN_OPERATION_TYPES, start=1):
        run(sched.release(run(sched.acquire_admin(record, operation_type=operation, transaction_id=f"tx_{index}"))))
    with pytest.raises(ValueError):
        run(sched.acquire_admin(record, operation_type="rewrite-everything", transaction_id="tx_x"))
    assert sched.live() == []


def test_a_release_of_a_grant_no_longer_held_is_recorded_not_raised(studio, store, sched, activity):
    """Release runs from cleanup paths; raising there would hide the real failure."""
    record = repo(studio, store)
    grant = turn(sched, record)
    run(sched.release(grant))
    other = turn(sched, record, action_id="a_2")

    run(sched.release(grant))                      # the old holder's cleanup, a second time
    assert [r["params"]["stale"] for r in activity.of("lease.released")] == [False, True]
    assert run(sched.get(IDENTITY)).generation == other.generation, "the new owner keeps its lock"

    run(sched.release(other))
    assert run(sched.get(IDENTITY)) is None


def test_a_grant_that_cannot_be_recorded_is_given_back(studio, store, sched, activity):
    """The caller never receives that grant, so nobody else could ever release it."""
    record = repo(studio, store)
    activity.fail = True
    with pytest.raises(RuntimeError):
        run(sched.acquire_admin(record, operation_type="doctor", transaction_id=None))
    activity.fail = False
    assert sched.live() == []
    run(sched.release(run(sched.acquire_admin(record, operation_type="doctor", transaction_id=None))))


def test_get_returns_whichever_kind_holds_the_identity_and_list_shows_both(studio, store, sched):
    repo(studio, store, "r_1", identity="1:1")
    admin_repo = repo(studio, store, "r_2", identity="1:2")
    action(store, "a_1", repo_id="r_1", identity="1:1")
    run(sched.acquire_admin(admin_repo, operation_type="install", transaction_id="tx_1"))

    assert run(sched.get("1:1")).kind == "execution"
    assert run(sched.get("1:2")).kind == "admin"
    assert run(sched.get("1:404")) is None
    assert {(r.kind, r.resolved_repo_identity) for r in run(sched.list())} == {
        ("execution", "1:1"), ("admin", "1:2")}
    assert run(sched.list()) == sched.live()


def test_require_host_idle_refuses_a_busy_or_unreadable_slot(studio, store, sched, bridge, errors):
    record = repo(studio, store)
    sched.require_host_idle(record)
    bridge.views.append(View("slot-1", record.canonical_path))
    sched.require_host_idle(record)

    bridge.views[0].reasons = ("running",)
    with pytest.raises(errors.StudioError) as exc:
        sched.require_host_idle(record)
    assert exc.value.code == "repo_busy" and exc.value.details["slot_key"] == "slot-1"

    bridge.views[0] = object()                     # a host release renamed what we read
    bridge.slots_for_project = lambda path: [bridge.views[0]]
    with pytest.raises(errors.StudioError):
        sched.require_host_idle(record)


# --------------------------------------------------------------------------- #
# structural guarantees
# --------------------------------------------------------------------------- #


def test_no_storage_call_runs_on_the_event_loop():
    """Inside an ``async def`` every SQLite read is *handed* to ``asyncio.to_thread``, never called (§0.6)."""
    tree = ast.parse((APP_ROOT / "backend" / "studio" / "leases.py").read_text("utf-8"))
    sync_readers = {"_in_flight", "live"}
    inline = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            target = node.func.value
            if isinstance(target, ast.Attribute) and target.attr == "_storage":
                inline.append(node.lineno)
            if isinstance(target, ast.Name) and target.id == "self" and node.func.attr in sync_readers:
                inline.append(node.lineno)
    assert inline == []


def test_the_real_settings_and_activity_fit_the_scheduler(studio, store, bridge, clock, L):
    """Wiring check against the real collaborators: constructor order, sync cap accessor, activity kinds."""
    service = studio.settings.SettingsService(store, bridge, clock)
    projector = studio.activity.ActivityProjector(store, clock)
    scheduler = L.RepoScheduler(store, bridge, service, clock, projector, busy_reasons=busy)

    record = repo(studio, store)
    grant = turn(scheduler, record, intent_uuid="u-1")
    assert service.global_concurrency_cap() == 2                     # DEFAULTS, before any load()
    run(scheduler.release(grant))
    rows, _ = store.activity_query(studio.storage.ActivityQuery())
    assert [r.kind for r in rows] == ["lease.released", "lease.acquired"]   # newest first
    assert rows[-1].params["intent_uuid"] == "u-1" and rows[-1].message_key == "activity.lease.acquired"


def test_the_settled_transaction_statuses_exist_in_the_installers_own_tuple(studio, L):
    assert L.TRANSACTION_SETTLED_STATUS <= set(studio.installer.TransactionStatus)


def test_default_busy_reasons_is_taken_from_sessions(studio, L, store, bridge, settings, clock, activity):
    scheduler = L.RepoScheduler(store, bridge, settings, clock, activity)
    idle = SimpleNamespace(running=False, in_stage_execution=False, stop_state="idle", stopping=False,
                           queue_depth=0, approvals_pending=0, subagents_running=0, subagents_queued=0,
                           deliveries_inflight=0)
    assert scheduler._reasons(idle) == ()
    assert scheduler._reasons(SimpleNamespace(**{**vars(idle), "running": True})) == ("running",)
    assert scheduler._reasons(object()) is None                       # unreadable, never "idle"
