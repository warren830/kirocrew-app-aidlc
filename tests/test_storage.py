"""Crash-safety tests for ``backend/studio/storage.py``.

The properties under test are the ones the rest of the product trusts blindly: a compare-and-set that
exactly one racing writer can win, a ``Delivering`` record that is either durable *with* its lease or
not written at all, leases that cannot be held twice and whose generations never repeat, a bounded
event ring a client can replay from, stable activity paging, and a retention purge that clears the
verbatim human text without touching anything else.

Where a test needs a limit lowered (the event ring, the vacuum threshold) it patches the constant
rather than the code, so the production value stays the only one in the source.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import threading
from pathlib import Path

import pytest

import fixtures as F

TS = "2026-09-04T10:00:00Z"


# --------------------------------------------------------------------------- #
# fixtures and row helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def S(studio):
    module = studio.storage
    assert module is not None, getattr(studio, "storage__error", "storage.py did not import")
    return module


@pytest.fixture
def C(studio):
    return studio.constants


@pytest.fixture
def db_path(fake_ctx, C) -> Path:
    """The real location: ``ctx.data_dir / "studio.sqlite3"`` (§1.4)."""
    return Path(fake_ctx.data_dir) / C.DB_FILENAME


@pytest.fixture
def store(S, db_path, clock, ids):
    st = S.Storage(db_path, clock=clock, ids=ids)
    st.open()
    yield st
    st.close()


_UNSET = object()


def repo(store, repo_id="r_1", *, identity=_UNSET, **over):
    row = {
        "id": repo_id,
        "resolved_identity": f"1:{repo_id}" if identity is _UNSET else identity,
        "canonical_path": f"/FIXTURE/repo/{repo_id}",
        "label": repo_id,
        "added_at": TS,
        "platform": "darwin",
    }
    row.update(over)
    store.insert("repos", row)
    return repo_id


def action(store, action_id="a_1", *, repo_id="r_1", status="Queued", **over):
    row = {
        "action_id": action_id,
        "type": "gate",
        "risk_class": "human_lane",
        "repo_id": repo_id,
        "intent_dir": "260904-gate-demo",
        "space": "default",
        "status": status,
        "created_at": TS,
        "updated_at": TS,
        "waiting_since": TS,
    }
    row.update(over)
    store.insert("actions", row)
    return action_id


def binding(store, key="r_1:default:260904-gate-demo", *, repo_id="r_1", **over):
    row = {
        "binding_key": key,
        "repo_id": repo_id,
        "space": "default",
        "intent_dir": "260904-gate-demo",
        "created_at": TS,
        "updated_at": TS,
    }
    row.update(over)
    store.insert("intent_bindings", row)
    return key


def activity_row(S, **over):
    fields = {"at": TS, "source": "studio", "kind": "action.created", "severity": "info",
              "message_key": "activity.action.created"}
    fields.update(over)
    return S.ActivityRow(**fields)


# --------------------------------------------------------------------------- #
# schema, PRAGMAs, migration path
# --------------------------------------------------------------------------- #


def test_ddl_applies_to_an_empty_file(store, S, db_path):
    assert db_path.is_file()
    rows = store.select("schema_meta")
    assert {r["key"] for r in rows} >= {"schema_version", "created_at", "migrated_at"}
    # every table the key map claims exists and is readable; only schema_meta starts non-empty
    for table in sorted(set(S.TABLE_KEYS) - {"schema_meta"}):
        assert store.select(table, limit=1) == []
    assert store.pragmas()["schema_version"] == S.SCHEMA_VERSION == 2
    assert store.integrity_check() == []


def test_the_partial_dedupe_index_covers_exactly_the_live_statuses(S, C):
    tail = S.SCHEMA_V1.split("actions_live_dedupe", 1)[1]
    listed = tail.split("status IN (", 1)[1].split(")", 1)[0]
    statuses = tuple(part.strip().strip("'") for part in listed.split(","))
    assert statuses == tuple(C.DEDUPE_LIVE_ACTION_STATUS)
    assert "Failed" not in statuses and "Failed" in C.LIVE_ACTION_STATUS


def test_pragmas_are_in_force_on_the_connection(store, C):
    p = store.pragmas()
    assert p["journal_mode"] == "wal"
    assert p["synchronous"] == 2                      # FULL
    assert p["busy_timeout"] == C.SQLITE_BUSY_TIMEOUT_MS
    assert p["foreign_keys"] == 1


def test_foreign_keys_refuse_orphans_and_cascade_on_repo_delete(store):
    repo(store)
    action(store)
    binding(store)
    with pytest.raises(sqlite3.IntegrityError):
        action(store, "a_2", repo_id="r_missing")
    store.delete("repos", "r_1")
    assert store.select("actions") == [] and store.select("intent_bindings") == []


def test_reopening_at_the_current_version_is_a_no_op(S, db_path, clock, ids):
    st = S.Storage(db_path, clock=clock, ids=ids)
    st.open()
    repo(st)
    stamps = {r["key"]: r["value"] for r in st.select("schema_meta")}
    st.insert("schema_meta", {"key": "sentinel", "value": "keep-me"})
    st.close()

    clock.advance(3600)
    again = S.Storage(db_path, clock=clock, ids=ids)
    again.open()
    try:
        after = {r["key"]: r["value"] for r in again.select("schema_meta")}
        assert after["migrated_at"] == stamps["migrated_at"]      # no migration ran
        assert after["created_at"] == stamps["created_at"]
        assert after["sentinel"] == "keep-me"
        assert again.get("repos", "r_1")["label"] == "r_1"
        assert again.integrity_check() == []
    finally:
        again.close()


def test_a_file_written_by_a_newer_schema_is_refused(S, db_path, clock, ids, studio):
    st = S.Storage(db_path, clock=clock, ids=ids)
    st.open()
    st.close()
    raw = sqlite3.connect(str(db_path))
    raw.execute("UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'")
    raw.commit()
    raw.close()

    with pytest.raises(studio.errors.StudioError) as exc:
        S.Storage(db_path, clock=clock, ids=ids).open()
    assert exc.value.code == "storage_error"
    assert exc.value.details == {"found": 99, "supported": S.SCHEMA_VERSION}


def test_use_before_open_and_after_close_is_a_storage_error(S, db_path, clock, ids, studio):
    st = S.Storage(db_path, clock=clock, ids=ids)
    with pytest.raises(studio.errors.StudioError) as before:
        st.pref_all()
    assert before.value.code == "storage_unavailable"
    st.open()
    st.close()
    with pytest.raises(studio.errors.StudioError) as after:
        st.event_bounds()
    assert after.value.code == "storage_unavailable"
    st.open()          # open() is idempotent and re-usable after a close
    assert st.integrity_check() == []
    st.close()


def test_every_public_method_is_one_transaction(S):
    """A public method that writes outside ``_txn`` would break the one-transaction rule silently."""
    source = Path(S.__file__).read_text("utf-8")
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Storage")
    # These cannot be inside a transaction: SQLite refuses VACUUM in one, and the rest only read
    # PRAGMAs or lifecycle state.
    outside = {"open", "close", "pragmas", "integrity_check", "vacuum_if_needed", "path"}
    seen = set()
    for node in cls.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
            continue
        seen.add(node.name)
        body = ast.get_source_segment(source, node) or ""
        if node.name in outside:
            assert not any(w in body for w in ("INSERT ", "UPDATE ", "DELETE ")), node.name
        else:
            assert "self._txn()" in body, f"{node.name} does not open a transaction"
    assert outside - {"path"} <= seen
    assert {"cas_update", "deliver_under_lease", "lease_acquire", "activity_query"} <= seen


# --------------------------------------------------------------------------- #
# generic helpers
# --------------------------------------------------------------------------- #


def test_generic_crud_roundtrips_json_columns_and_validates_names(store):
    repo(store, scan_json={"intents": 2, "findings": []})
    row = store.get("repos", "r_1")
    assert row["scan_json"] == {"intents": 2, "findings": []}     # decoded, not raw text
    assert store.get("repos", "nope") is None

    repo(store, "r_2", identity=None, archived=1)
    assert [r["id"] for r in store.select("repos", order_by="id DESC")] == ["r_2", "r_1"]
    assert [r["id"] for r in store.select("repos", {"resolved_identity": None})] == ["r_2"]
    assert [r["id"] for r in store.select("repos", {"id": ["r_1", "r_2"]}, order_by="id")] == ["r_1", "r_2"]
    assert store.select("repos", {"id": []}) == []

    store.update("repos", "r_1", {"label": "renamed", "scan_json": {"intents": 3}})
    assert store.get("repos", "r_1")["label"] == "renamed"
    assert store.get("repos", "r_1")["scan_json"] == {"intents": 3}
    store.delete("repos", "r_2")
    assert len(store.select("repos")) == 1

    for bad in (lambda: store.get("no_such_table", "x"),
                lambda: store.select("repos", {"label; DROP TABLE repos": 1}),
                lambda: store.select("repos", order_by="label; DROP TABLE repos"),
                lambda: store.select("repos", order_by="label SIDEWAYS"),
                lambda: store.insert("repos", {"nope": 1}),
                lambda: store.update("actions", "a_1", {"status": "Queued"})):
        with pytest.raises(ValueError):
            bad()


def test_a_json_column_accepts_an_already_encoded_string(store):
    repo(store, scan_json=json.dumps({"pre": "encoded"}))
    assert store.get("repos", "r_1")["scan_json"] == {"pre": "encoded"}


# --------------------------------------------------------------------------- #
# cas_update
# --------------------------------------------------------------------------- #


def _cas_case(store, table):
    """One live row per CAS table, plus the value the test writes to it."""
    if table == "actions":
        repo(store)
        action(store)
        return "a_1", {"status": "Delivering"}
    if table == "intent_bindings":
        repo(store)
        binding(store)
        return "r_1:default:260904-gate-demo", {"paused": 1}
    if table == "execution_leases":
        store.lease_acquire("execution", "1:10", {"repo_id": "r_1", "session_key": "dashboard:s"})
        return "1:10", {"observed_busy_state": "running"}
    store.lease_acquire("admin", "1:11", {"repo_id": "r_1", "operation_type": "install"})
    return "1:11", {"transaction_id": "tx_1"}


@pytest.mark.parametrize("table", ["actions", "intent_bindings", "execution_leases", "admin_leases"])
def test_cas_update_bumps_the_generation_and_the_tables_own_timestamp(store, S, clock, table):
    gen_col, ts_col = S.CAS_TABLES[table]
    key, values = _cas_case(store, table)
    before = store.get(table, key)
    clock.advance(60)

    new_gen = store.cas_update(table, key, before[gen_col], values)
    after = store.get(table, key)
    assert new_gen == before[gen_col] + 1 == after[gen_col]
    assert after[ts_col] == clock.iso() != before[ts_col]
    for name, value in values.items():
        assert after[name] == value
    # review R06: the lease tables have no updated_at at all, so a single hard-coded timestamp
    # column would have failed every lease CAS.
    if table.endswith("_leases"):
        assert ts_col == "heartbeat_at" and "updated_at" not in after


@pytest.mark.parametrize("table", ["actions", "intent_bindings", "execution_leases", "admin_leases"])
def test_cas_update_raises_stale_generation_with_expected_and_actual(store, S, studio, table):
    gen_col, _ = S.CAS_TABLES[table]
    key, values = _cas_case(store, table)
    actual = store.get(table, key)[gen_col]

    with pytest.raises(studio.errors.StaleGeneration) as exc:
        store.cas_update(table, key, actual + 5, values)
    assert exc.value.code == "stale_generation"
    assert exc.value.details["expected"] == actual + 5
    assert exc.value.details["actual"] == actual
    assert store.get(table, key)[gen_col] == actual          # nothing was written

    with pytest.raises(studio.errors.StaleGeneration) as missing:
        store.cas_update(table, "absent-key", 0, values)
    assert missing.value.details == {"expected": 0, "actual": None, "table": table, "key": "absent-key"}


def test_cas_update_refuses_an_unknown_table_and_caller_owned_columns(store, S):
    repo(store)
    action(store)
    with pytest.raises(ValueError):
        store.cas_update("repos", "r_1", 0, {"label": "x"})           # no generation column
    with pytest.raises(ValueError):
        store.cas_update("actions", "a_1", 0, {"status_generation": 7})
    with pytest.raises(ValueError):
        store.cas_update("actions", "a_1", 0, {"updated_at": TS})


def test_two_threads_racing_one_row_produce_exactly_one_winner(store, studio):
    repo(store)
    action(store)
    start = threading.Barrier(2)
    results: list[object] = []
    lock = threading.Lock()

    def attempt(status):
        start.wait(timeout=5)
        try:
            outcome = store.cas_update("actions", "a_1", 0, {"status": status})
        except studio.errors.StaleGeneration as exc:
            outcome = exc
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt, args=(s,)) for s in ("Delivering", "Cancelled")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    winners = [r for r in results if isinstance(r, int)]
    losers = [r for r in results if isinstance(r, studio.errors.StaleGeneration)]
    assert len(winners) == 1 and len(losers) == 1
    assert winners[0] == 1
    assert losers[0].details == {"expected": 0, "actual": 1, "table": "actions", "key": "a_1"}
    assert store.get("actions", "a_1")["status_generation"] == 1
    assert store.integrity_check() == []


# --------------------------------------------------------------------------- #
# deliver_under_lease
# --------------------------------------------------------------------------- #


def _delivering(clock, **over):
    values = {"status": "Delivering", "delivery_id": "dl_1", "delivering_at": clock.iso(),
              "boot_id": "boot-test-0001", "session_key": "dashboard:slot", "lease_generation": 1}
    values.update(over)
    return values


def test_deliver_under_lease_commits_the_record_and_the_lease_together(store, clock):
    repo(store)
    action(store)
    grant = store.lease_acquire("execution", "1:r_1", {"repo_id": "r_1", "session_key": "dashboard:slot"})
    assert grant.action_id is None

    generation = store.deliver_under_lease(
        "a_1", 0, identity="1:r_1", lease_generation=grant.generation, new_values=_delivering(clock)
    )
    assert generation == 1
    row = store.get("actions", "a_1")
    assert row["status"] == "Delivering" and row["status_generation"] == 1
    assert row["delivery_id"] == "dl_1" and row["boot_id"] == "boot-test-0001"
    assert store.lease_get("execution", "1:r_1").action_id == "a_1"

    # The submit path promises the record is durable before the browser is told to send. A second
    # connection can only see it if the transaction really committed (synchronous=FULL, not deferred).
    other = sqlite3.connect(str(store.path))
    try:
        assert other.execute("SELECT status, action_id FROM actions").fetchall() == [("Delivering", "a_1")]
        assert other.execute("SELECT action_id FROM execution_leases").fetchall() == [("a_1",)]
    finally:
        other.close()


def test_deliver_under_lease_writes_nothing_when_the_lease_moved(store, studio, clock):
    repo(store)
    action(store)
    grant = store.lease_acquire("execution", "1:r_1", {"repo_id": "r_1"})
    store.lease_release("execution", "1:r_1", grant.generation)
    regained = store.lease_acquire("execution", "1:r_1", {"repo_id": "r_1"})
    assert regained.generation == grant.generation + 1

    with pytest.raises(studio.errors.StudioError) as exc:
        store.deliver_under_lease("a_1", 0, identity="1:r_1",
                                  lease_generation=grant.generation, new_values=_delivering(clock))
    assert exc.value.code == "lease_lost"
    assert exc.value.details["actual_generation"] == regained.generation
    row = store.get("actions", "a_1")
    assert row["status"] == "Queued" and row["status_generation"] == 0 and row["delivery_id"] is None
    assert store.lease_get("execution", "1:r_1").action_id is None


def test_deliver_under_lease_writes_nothing_when_there_is_no_lease(store, studio, clock):
    repo(store)
    action(store)
    with pytest.raises(studio.errors.StudioError) as exc:
        store.deliver_under_lease("a_1", 0, identity="1:r_1", lease_generation=1,
                                  new_values=_delivering(clock))
    assert exc.value.code == "lease_lost" and exc.value.details["actual_generation"] is None
    assert store.get("actions", "a_1")["status"] == "Queued"


def test_deliver_under_lease_leaves_the_lease_alone_when_the_action_cas_loses(store, studio, clock):
    repo(store)
    action(store)
    grant = store.lease_acquire("execution", "1:r_1", {"repo_id": "r_1"})
    store.cas_update("actions", "a_1", 0, {"status": "Cancelled"})     # somebody else moved it first

    with pytest.raises(studio.errors.StaleGeneration):
        store.deliver_under_lease("a_1", 0, identity="1:r_1",
                                  lease_generation=grant.generation, new_values=_delivering(clock))
    assert store.get("actions", "a_1")["status"] == "Cancelled"
    assert store.lease_get("execution", "1:r_1").action_id is None     # step 3 rolled back with it


def test_deliver_under_lease_only_writes_the_delivering_status(store, clock):
    repo(store)
    action(store)
    store.lease_acquire("execution", "1:r_1", {"repo_id": "r_1"})
    with pytest.raises(ValueError):
        store.deliver_under_lease("a_1", 0, identity="1:r_1", lease_generation=1,
                                  new_values=_delivering(clock, status="Delivered"))


# --------------------------------------------------------------------------- #
# leases
# --------------------------------------------------------------------------- #


def test_execution_and_admin_leases_are_mutually_exclusive_per_identity(store, studio, C):
    repo(store)
    store.lease_acquire("execution", "1:r_1", {"repo_id": "r_1", "session_key": "dashboard:slot"})

    with pytest.raises(studio.errors.LeaseHeld) as exc:
        store.lease_acquire("admin", "1:r_1", {"repo_id": "r_1", "operation_type": "install"})
    assert exc.value.code == "repo_busy"
    assert exc.value.details["kind"] == "execution"
    assert exc.value.details["owner"]["session_key"] == "dashboard:slot"
    assert exc.value.details["retry_after_secs"] == int(C.LEASE_HEARTBEAT_SECS)
    with pytest.raises(studio.errors.LeaseHeld):
        store.lease_acquire("execution", "1:r_1", {"repo_id": "r_1"})

    # a different repository is unaffected, and both kinds may be live on different identities
    store.lease_acquire("admin", "1:r_2", {"repo_id": "r_1", "operation_type": "install"})
    assert {(r.kind, r.resolved_repo_identity) for r in store.lease_list()} == {
        ("execution", "1:r_1"), ("admin", "1:r_2")}


def test_lease_generations_are_monotonic_across_release_and_reacquire(store, studio):
    first = store.lease_acquire("execution", "1:r_1", {"repo_id": "r_1"})
    assert first.generation == 1
    store.lease_release("execution", "1:r_1", 1)
    second = store.lease_acquire("admin", "1:r_1", {"repo_id": "r_1", "operation_type": "doctor"})
    assert second.generation == 2                     # the counter outlives both the row and the kind
    store.lease_release("admin", "1:r_1", 2)
    assert store.lease_acquire("execution", "1:r_1", {"repo_id": "r_1"}).generation == 3

    with pytest.raises(studio.errors.StaleGeneration):
        store.lease_release("execution", "1:r_1", 1)
    assert store.lease_get("execution", "1:r_1") is not None


def test_lease_heartbeat_refreshes_without_moving_the_generation(store, studio, clock):
    grant = store.lease_acquire("execution", "1:r_1", {"repo_id": "r_1"})
    clock.advance(15)
    beat = store.lease_heartbeat("execution", "1:r_1", grant.generation, "running")
    assert beat.generation == grant.generation           # the holder's next CAS must still match
    assert beat.heartbeat_at == clock.iso() != grant.acquired_at
    assert beat.observed_busy_state == "running"
    assert store.lease_get("execution", "1:r_1").heartbeat_at == clock.iso()

    with pytest.raises(studio.errors.StaleGeneration) as exc:
        store.lease_heartbeat("execution", "1:r_1", grant.generation + 1)
    assert exc.value.details == {"expected": 2, "actual": 1, "kind": "execution"}

    store.lease_release("execution", "1:r_1", grant.generation)
    with pytest.raises(studio.errors.StaleGeneration) as gone:
        store.lease_heartbeat("execution", "1:r_1", grant.generation)
    assert gone.value.details["actual"] is None


def test_lease_reclaim_records_the_proof_and_never_checks_the_clock(store, S, clock):
    repo(store)
    action(store)
    grant = store.lease_acquire("execution", "1:r_1",
                               {"repo_id": "r_1", "session_key": "dashboard:slot", "action_id": "a_1"})
    evidence = {"slot_present": False, "action_status": "Queued", "boundary_stable": True}

    # No clock movement at all: eligibility is the caller's proof, never elapsed time (FR-SES-007).
    store.lease_reclaim("execution", "1:r_1", grant.generation,
                        reason="never_dispatched", evidence=evidence)

    assert store.lease_get("execution", "1:r_1") is None
    rows, cursor = store.activity_query(S.ActivityQuery(kind="lease.reclaimed"))
    assert cursor is None and len(rows) == 1
    row = rows[0]
    assert (row.source, row.severity, row.message_key) == ("studio", "attention", "activity.lease.reclaimed")
    assert row.repo_id == "r_1" and row.action_id == "a_1" and row.session_key == "dashboard:slot"
    assert row.params == {"lease_kind": "execution", "reason": "never_dispatched",
                          "generation": 1, "evidence": evidence}
    assert row.at == clock.iso()
    assert store.lease_acquire("execution", "1:r_1", {"repo_id": "r_1"}).generation == 2


def test_lease_reclaim_of_a_moved_lease_changes_nothing(store, S, studio):
    store.lease_acquire("execution", "1:r_1", {"repo_id": "r_1"})
    with pytest.raises(studio.errors.StaleGeneration):
        store.lease_reclaim("execution", "1:r_1", 7, reason="stale", evidence={})
    assert store.lease_get("execution", "1:r_1").generation == 1
    assert store.activity_query(S.ActivityQuery())[0] == []


def test_lease_fields_are_validated_per_kind(store):
    for bad in (lambda: store.lease_acquire("execution", "1:x", {}),
                lambda: store.lease_acquire("admin", "1:x", {"repo_id": "r_1"}),
                lambda: store.lease_acquire("admin", "1:x", {"repo_id": "r_1", "operation_type": "install",
                                                             "session_key": "dashboard:s"}),
                lambda: store.lease_acquire("execution", "1:x", {"repo_id": "r_1", "operation_type": "install"}),
                lambda: store.lease_acquire("other", "1:x", {"repo_id": "r_1"}),
                lambda: store.lease_get("other", "1:x")):
        with pytest.raises(ValueError):
            bad()
    assert store.lease_list() == []


# --------------------------------------------------------------------------- #
# actions_live_dedupe partial index
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("terminal", ["Failed", "Cancelled", "StateChanged"])
def test_a_live_card_may_sit_beside_a_terminal_twin(store, terminal):
    repo(store)
    key = "gate:r_1:260904-gate-demo:requirements-analysis:tok"
    action(store, "a_old", status=terminal, dedupe_key=key)
    action(store, "a_new", status="Queued", dedupe_key=key)
    assert {r["action_id"] for r in store.select("actions", {"dedupe_key": key})} == {"a_old", "a_new"}


def test_a_second_live_card_for_one_boundary_is_refused(store):
    repo(store)
    key = "gate:r_1:260904-gate-demo:requirements-analysis:tok"
    action(store, "a_1", status="Queued", dedupe_key=key)
    with pytest.raises(sqlite3.IntegrityError):
        action(store, "a_2", status="Delivering", dedupe_key=key)
    assert store.get("actions", "a_2") is None
    # …and a null dedupe_key never collides (derived cards that have no boundary token)
    action(store, "a_3", dedupe_key=None)
    action(store, "a_4", dedupe_key=None)


# --------------------------------------------------------------------------- #
# events ring
# --------------------------------------------------------------------------- #


def test_the_event_ring_trims_and_replays_from_a_cursor(store, C, monkeypatch):
    monkeypatch.setattr(C, "EVENTS_RING_SIZE", 5)
    seqs = [store.event_append("action.updated", {"i": i}) for i in range(8)]
    assert seqs == list(range(1, 9))

    oldest, newest = store.event_bounds()
    assert (oldest, newest) == (4, 8)                       # exactly five rows survive

    replay = store.event_since(0)
    assert [e.seq for e in replay] == [4, 5, 6, 7, 8]
    assert replay[0].payload == {"i": 3} and replay[0].type == "action.updated"
    assert replay[0].to_json()["seq"] == 4 and replay[0].at == store.get("events", "4")["at"]
    assert [e.seq for e in store.event_since(6)] == [7, 8]
    assert [e.seq for e in store.event_since(6, limit=1)] == [7]
    assert store.event_since(8) == []

    # A cursor older than the oldest kept seq has lost frames: the caller must publish `reset`
    # with these bounds instead of pretending the replay is complete.
    stale_cursor = 1
    assert stale_cursor + 1 < oldest
    assert [e.seq for e in store.event_since(stale_cursor)][0] == oldest
    assert store.event_since(newest - 1)[0].seq == newest      # a fresh cursor needs no reset


def test_event_bounds_on_an_empty_ring(store):
    assert store.event_bounds() == (0, 0)
    assert store.event_since(0) == []


# --------------------------------------------------------------------------- #
# activity
# --------------------------------------------------------------------------- #


def test_activity_paging_is_stable_across_pages(store, S, clock):
    repo(store)
    ids_written = [store.activity_append(activity_row(S, repo_id="r_1", params={"n": n}))
                   for n in range(7)]
    assert ids_written == list(range(1, 8))
    # every row shares one second: an `at`-keyed cursor would skip or repeat rows here
    assert len({r.at for r in store.activity_query(S.ActivityQuery(limit=500))[0]}) == 1

    seen: list[int] = []
    cursor = None
    pages = 0
    while True:
        rows, cursor = store.activity_query(S.ActivityQuery(limit=3, cursor=cursor))
        pages += 1
        seen.extend(r.id for r in rows)
        if cursor is None:
            break
        assert len(rows) == 3
    assert pages == 3
    assert seen == sorted(ids_written, reverse=True)         # newest first, no gaps, no repeats


def test_activity_query_filters_and_derives_the_intent_key(store, S, clock):
    repo(store)
    action(store, "a_1", type="gate")
    action(store, "a_2", type="run")
    store.activity_append(activity_row(S, repo_id="r_1", kind="action.created", action_id="a_1",
                                       intent_dir="260904-gate-demo", stage="requirements-analysis"))
    clock.advance(60)
    store.activity_append(activity_row(S, at=clock.iso(), repo_id="r_1", kind="action.submitted",
                                       severity="attention", action_id="a_2", space="research",
                                       intent_dir="260904-gate-demo", source="studio"))
    clock.advance(60)
    store.activity_append(activity_row(S, at=clock.iso(), repo_id="r_2", kind="repo.added"))

    q = S.ActivityQuery
    assert [r.kind for r in store.activity_query(q(repo_id="r_1"))[0]] == ["action.submitted", "action.created"]
    assert [r.kind for r in store.activity_query(q(repo_id="r_2"))[0]] == ["repo.added"]
    assert [r.kind for r in store.activity_query(q(severity="attention"))[0]] == ["action.submitted"]
    assert [r.kind for r in store.activity_query(q(stage="requirements-analysis"))[0]] == ["action.created"]
    assert [r.kind for r in store.activity_query(q(kind="repo.added"))[0]] == ["repo.added"]
    # action_type has no column of its own; it resolves through the actions table
    assert [r.action_id for r in store.activity_query(q(action_type="run"))[0]] == ["a_2"]
    assert [r.action_id for r in store.activity_query(q(action_type="gate"))[0]] == ["a_1"]
    assert [r.kind for r in store.activity_query(q(since=clock.iso()))[0]] == ["repo.added"]
    assert [r.kind for r in store.activity_query(q(until=TS))[0]] == ["action.created"]
    assert store.activity_query(q(source="aidlc"))[0] == []

    default_space = store.activity_query(q(repo_id="r_1"))[0][1]
    research = store.activity_query(q(space="research"))[0][0]
    assert default_space.intent_key == "260904-gate-demo"          # C02: bare dir in the default space
    assert research.intent_key == "research~260904-gate-demo"
    assert store.activity_query(q(limit=500))[0][0].to_json()["evidence"] == []


def test_activity_evidence_and_limits_round_trip(store, S, studio):
    ref = S.EvidenceRef(kind="audit", ref="603e5f4a8072-26e77d17b1cd.md#12", detail="GATE_APPROVED")
    store.activity_append(activity_row(S, kind="action.resolved", evidence=(ref,), redacted=True))
    row = store.activity_query(S.ActivityQuery())[0][0]
    assert row.evidence == (ref,) and row.redacted is True
    assert row.to_json()["evidence"] == [{"kind": "audit", "ref": "603e5f4a8072-26e77d17b1cd.md#12",
                                         "detail": "GATE_APPROVED"}]

    assert store.activity_query(S.ActivityQuery(limit=10_000))[1] is None      # clamped, not refused
    with pytest.raises(ValueError):
        store.activity_append(S.ActivityRow(at=TS, kind="", message_key=""))
    with pytest.raises(studio.errors.StudioError) as bad:
        store.activity_query(S.ActivityQuery(cursor="not-base64!!"))
    assert bad.value.code == "bad_param"


def test_activity_purge_clears_only_what_retention_allows(store, S, clock):
    """The verbatim decision text is the only thing purged, in both tables that keep it."""
    repo(store)
    human = F.real_state()[:400] + "\n批准 — approve with the note above\n"
    old_id = store.activity_append(activity_row(S, at="2026-08-01T00:00:00Z", kind="action.submitted",
                                                repo_id="r_1", action_id="a_old", human_text=human,
                                                params={"decision": "approve"}))
    keep_id = store.activity_append(activity_row(S, at="2026-09-03T00:00:00Z", kind="action.submitted",
                                                 repo_id="r_1", action_id="a_new", human_text=human))
    action(store, "a_old", human_text=human, created_at="2026-08-01T00:00:00Z",
           delivering_at="2026-08-01T00:00:10Z")
    action(store, "a_new", human_text=human, created_at="2026-09-03T00:00:00Z",
           delivering_at="2026-09-03T00:00:10Z")

    cleared = store.activity_purge_human_text("2026-09-01T00:00:00Z")
    assert cleared == 2                                       # one activity row + one action row

    rows = {r.id: r for r in store.activity_query(S.ActivityQuery(limit=10))[0]}
    assert rows[old_id].human_text is None
    assert rows[old_id].params == {"decision": "approve"}     # everything else is untouched
    assert rows[old_id].kind == "action.submitted" and rows[old_id].at == "2026-08-01T00:00:00Z"
    assert rows[keep_id].human_text == human                  # still inside the retention window
    assert store.get("actions", "a_old")["human_text"] is None
    assert store.get("actions", "a_new")["human_text"] == human
    assert store.activity_purge_human_text("2026-09-01T00:00:00Z") == 0     # idempotent


# --------------------------------------------------------------------------- #
# preferences and housekeeping
# --------------------------------------------------------------------------- #


def test_preferences_round_trip_every_json_shape(store, clock):
    assert store.pref_get("locale") is None
    store.pref_set("locale", "zh-CN")
    store.pref_set("night_window", {"enabled": False, "start_local": "22:00", "turn_cap": 40})
    store.pref_set("muted_repo_ids", ["r_1", "r_2"])
    store.pref_set("credit_cap", None)
    store.pref_set("locale", "en-US")                      # upsert, not a duplicate row

    assert store.pref_get("locale") == "en-US"
    assert store.pref_get("night_window")["turn_cap"] == 40
    assert store.pref_get("muted_repo_ids") == ["r_1", "r_2"]
    assert store.pref_get("credit_cap") is None
    assert list(store.pref_all()) == ["credit_cap", "locale", "muted_repo_ids", "night_window"]
    assert store.get("preferences", "locale")["updated_at"] == clock.iso()


def test_vacuum_only_runs_when_the_free_list_is_worth_it(store, C, monkeypatch):
    repo(store)
    for n in range(200):
        store.insert("events", {"at": TS, "type": "activity.appended",
                               "payload_json": {"n": n, "pad": "x" * 400}})
    for row in store.select("events"):
        store.delete("events", str(row["seq"]))
    free_before = store.pragmas()["freelist_count"]
    assert free_before > 0

    store.vacuum_if_needed()                                # thresholds unmet: no rewrite
    assert store.pragmas()["freelist_count"] == free_before

    monkeypatch.setattr(C, "VACUUM_MIN_FREELIST_PAGES", 1)
    monkeypatch.setattr(C, "VACUUM_FREELIST_RATIO", 0.0)
    store.vacuum_if_needed()
    assert store.pragmas()["freelist_count"] == 0
    assert store.get("repos", "r_1")["label"] == "r_1"
    assert store.integrity_check() == []


def test_the_injectable_protocols_live_in_constants(C, clock, ids):
    """§0.2 puts Clock (and with it IdFactory) in constants.py; storage only annotates them."""
    assert hasattr(C, "Clock") and hasattr(C, "IdFactory")
    for name in ("now", "iso", "monotonic"):
        assert callable(getattr(clock, name)) and hasattr(C.Clock, name)
    assert callable(ids.new) and hasattr(C.IdFactory, "new")
    assert isinstance(clock.iso(), str) and clock.iso().endswith("Z")
    assert F.sha256("x")                                    # harvested fixtures import cleanly
