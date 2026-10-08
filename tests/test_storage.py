"""Crash-safety tests for ``backend/studio/storage.py``.

The properties under test are the ones the rest of the product trusts blindly: a compare-and-set that
exactly one racing writer can win, a committed ``Delivering`` record another connection can already
see, a migration ladder that takes any shipped file to today's schema without losing a row it keeps,
stable activity paging, and a retention purge that clears the verbatim human text without touching
anything else.
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
def store(S, db_path, clock):
    st = S.Storage(db_path, clock=clock)
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


#: The tables version 4 dropped: nothing has read or written them since leases, events and calibration moved.
DROPPED = {"execution_leases", "admin_leases", "lease_generations", "events", "calibration", "migrations"}


def legacy_file(S, db_path, version):
    """A raw connection to a database exactly as ``version`` left it: the ladder up to there, stamped so."""
    raw = sqlite3.connect(str(db_path))
    for target in range(1, version + 1):
        for statement in S._statements(S.MIGRATIONS[target]):
            raw.execute(statement)
    raw.execute("INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)", (str(version),))
    raw.execute("INSERT INTO repos(id, resolved_identity, canonical_path, label, added_at, platform) "
                "VALUES('r_1', '1:r_1', '/FIXTURE/repo/r_1', 'r_1', ?, 'darwin')", (TS,))
    return raw


def schema_of(path):
    """Every table and index definition in the file, as SQLite itself stores them."""
    raw = sqlite3.connect(str(path))
    try:
        return raw.execute("SELECT type, name, tbl_name, sql FROM sqlite_master "
                           "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
    finally:
        raw.close()


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
    # ...and the key map is every table there is: a fresh file runs the whole ladder, drops included
    assert {name for kind, name, _, _ in schema_of(db_path) if kind == "table"} == set(S.TABLE_KEYS)
    assert store.pragmas()["schema_version"] == S.SCHEMA_VERSION == 4
    assert store.integrity_check() == []


def test_the_partial_dedupe_index_covers_exactly_the_live_statuses(store, C):
    """Read from the file, not from a DDL string: ``SCHEMA_V1`` stays the record of version 1, and the index
    in force is the one the latest migration built."""
    raw = sqlite3.connect(str(store.path))
    try:
        (sql,) = raw.execute("SELECT sql FROM sqlite_master WHERE name = 'actions_live_dedupe'").fetchone()
    finally:
        raw.close()
    listed = sql.split("status IN (", 1)[1].split(")", 1)[0]
    statuses = tuple(part.strip().strip("'") for part in listed.split(","))
    assert statuses == tuple(C.DEDUPE_LIVE_ACTION_STATUS)
    assert "Failed" not in statuses and "Failed" in C.LIVE_ACTION_STATUS


def test_version_3_folds_the_retired_action_statuses_into_their_neighbours(S, C, db_path, clock):
    """``Processing`` was a ``Delivered`` whose turn had started, ``DeliveryUncertain`` an early
    ``ReconciliationRequired``, and ``Draft`` was never created. An upgraded file holds only statuses the code
    can still move: the turn's start is kept as evidence (the reconciler's own stamp when it made one, else the
    delivery), and the live-dedupe index is rebuilt over the statuses that remain."""
    raw = legacy_file(S, db_path, 2)

    def old_row(action_id, status, evidence, *, delivered_at=None, dedupe=None):
        raw.execute(
            "INSERT INTO actions(action_id, type, risk_class, repo_id, intent_dir, space, status, created_at, "
            "updated_at, waiting_since, delivered_at, evidence_json, dedupe_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (action_id, "gate", "human_lane", "r_1", "260904-gate-demo", "default", status, TS,
             "2026-09-04T10:05:00Z", TS, delivered_at, None if evidence is None else json.dumps(evidence), dedupe))

    old_row("a_ran", "Processing", {"receipt": {"ok": True}}, delivered_at="2026-09-04T10:01:00Z", dedupe="k1")
    old_row("a_queued", "Processing", {"queued_at": "2026-09-04T10:01:00Z", "turn_started_at": "2026-09-04T10:03:00Z"},
            delivered_at="2026-09-04T10:01:00Z", dedupe="k2")
    old_row("a_bare", "Processing", None)
    old_row("a_unsure", "DeliveryUncertain", {"outcome": "uncertain"}, dedupe="k3")
    old_row("a_draft", "Draft", {})
    old_row("a_waiting", "Delivered", {"queued_at": TS}, delivered_at=TS)
    for from_status, to_status, reason in (("Delivering", "DeliveryUncertain", "ack_deadline"),
                                           ("DeliveryUncertain", "ReconciliationRequired", "uncertain_escalated"),
                                           ("Delivered", "Processing", "turn_started")):
        raw.execute("INSERT INTO action_transitions(action_id, from_status, to_status, generation, at, reason) "
                    "VALUES('a_unsure', ?, ?, 1, ?, ?)", (from_status, to_status, TS, reason))
    raw.commit()
    raw.close()

    store = S.Storage(db_path, clock=clock)
    store.open()
    try:
        assert {r["action_id"]: r["status"] for r in store.select("actions")} == {
            "a_ran": "Delivered", "a_queued": "Delivered", "a_bare": "Delivered",
            "a_unsure": "ReconciliationRequired", "a_draft": "Queued", "a_waiting": "Delivered",
        }
        assert {r["status"] for r in store.select("actions")} <= set(C.ACTION_STATUS)

        def evidence(action_id):
            return store.get("actions", action_id)["evidence_json"]

        assert evidence("a_ran") == {"receipt": {"ok": True}, "turn_started_at": "2026-09-04T10:01:00Z"}
        assert evidence("a_queued")["turn_started_at"] == "2026-09-04T10:03:00Z"
        assert evidence("a_bare") == {"turn_started_at": "2026-09-04T10:05:00Z"}
        assert evidence("a_unsure") == {"outcome": "uncertain"}
        assert "turn_started_at" not in evidence("a_waiting"), "a queued message still waits for its turn"
        # History keeps its reasons; only a status the UI no longer has a label for is renamed.
        assert [(t["from_status"], t["to_status"], t["reason"])
                for t in store.select("action_transitions", order_by="id ASC")] == [
            ("Delivering", "ReconciliationRequired", "ack_deadline"),
            ("ReconciliationRequired", "ReconciliationRequired", "uncertain_escalated"),
            ("Delivered", "Processing", "turn_started"),
        ]
        assert store.pragmas()["schema_version"] == S.SCHEMA_VERSION
        assert store.integrity_check() == []
        with pytest.raises(sqlite3.IntegrityError):
            action(store, "a_twin", status="ReconciliationRequired", dedupe_key="k1")
    finally:
        store.close()


def test_version_4_drops_the_unread_tables_and_keeps_every_other_row(S, db_path, clock):
    """A lease is an in-flight action or a ``flock`` now, events live in memory, and calibration and the console
    migration are gone. Upgrading a version 3 file removes exactly their six tables, rows and all, and leaves
    every row of every other table byte for byte as it was."""
    raw = legacy_file(S, db_path, 3)
    raw.execute("INSERT INTO schema_meta(key, value) VALUES('created_at', '2026-01-01T00:00:00Z')")
    raw.execute("INSERT INTO actions(action_id, type, risk_class, repo_id, intent_dir, space, status, created_at, "
                "updated_at, waiting_since, lease_generation, dedupe_key) VALUES('a_1', 'gate', 'human_lane', 'r_1', "
                "'260904-gate-demo', 'default', 'Delivered', ?, ?, ?, 7, 'k1')", (TS, TS, TS))
    raw.execute("INSERT INTO action_transitions(action_id, from_status, to_status, generation, at, reason) "
                "VALUES('a_1', 'Delivering', 'Delivered', 2, ?, 'ack')", (TS,))
    raw.execute("INSERT INTO intent_bindings(binding_key, repo_id, space, intent_dir, created_at, updated_at) "
                "VALUES('r_1:default:260904-gate-demo', 'r_1', 'default', '260904-gate-demo', ?, ?)", (TS, TS))
    raw.execute("INSERT INTO preferences(key, value_json, updated_at) VALUES('settings', '{\"locale\": \"zh-CN\"}', ?)",
                (TS,))
    raw.execute("INSERT INTO activity(at, source, kind, severity, repo_id, message_key, params_json) "
                "VALUES(?, 'studio', 'repo.added', 'info', 'r_1', 'activity.repo.added', '{}')", (TS,))
    raw.execute("INSERT INTO breakers(key, count, reason) VALUES('r_1:260904-gate-demo:fp', 2, 'transport')")
    raw.execute("INSERT INTO install_transactions(transaction_id, repo_id, kind, status, studio_version, "
                "engine_version, payload_digest, started_at) VALUES('tx_1', 'r_1', 'install', 'committed', '1.1.6', "
                "'2.10.0', 'd', ?)", (TS,))
    raw.execute("INSERT INTO install_receipts(receipt_id, repo_id, studio_version, engine_version, payload_digest, "
                "committed_at, transaction_id, files_json, status) VALUES('rc_1', 'r_1', '1.1.6', '2.10.0', 'd', ?, "
                "'tx_1', '[]', 'current')", (TS,))
    raw.execute("INSERT INTO advisor_drafts(draft_id, action_id, kind, status, request_json, created_at, updated_at, "
                "expires_at) VALUES('d_1', 'a_1', 'gate_analysis', 'ready', '{}', ?, ?, ?)", (TS, TS, TS))
    # One row in each dropped table, as the last version 3 process could have left it.
    raw.execute("INSERT INTO execution_leases(resolved_repo_identity, repo_id, action_id, acquired_at, heartbeat_at, "
                "generation) VALUES('1:r_1', 'r_1', 'a_1', ?, ?, 7)", (TS, TS))
    raw.execute("INSERT INTO admin_leases(resolved_repo_identity, repo_id, operation_type, acquired_at, heartbeat_at, "
                "generation, boot_id) VALUES('2:r_2', 'r_1', 'install', ?, ?, 3, 'boot-old')", (TS, TS))
    raw.execute("INSERT INTO lease_generations(resolved_repo_identity, last_generation) VALUES('1:r_1', 7)")
    raw.execute("INSERT INTO events(at, type, payload_json) VALUES(?, 'repo.updated', '{}')", (TS,))
    raw.execute("INSERT INTO calibration(at, scope, depth, stage_class, est_json, actual_json) "
                "VALUES(?, 'poc', 'Standard', 'operation', '{}', '{}')", (TS,))
    raw.execute("INSERT INTO migrations(id, applied_at, status, summary_json) VALUES('console', ?, 'applied', '{}')",
                (TS,))
    raw.commit()
    kept = sorted(set(S.TABLE_KEYS) - {"schema_meta"})
    before = {table: raw.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in kept}
    raw.close()
    assert all(before.values()), "every kept table carries a row into the upgrade"

    clock.advance(3600)
    store = S.Storage(db_path, clock=clock)
    store.open()
    try:
        assert {name for kind, name, _, _ in schema_of(db_path) if kind == "table"} == set(S.TABLE_KEYS)
        other = sqlite3.connect(str(db_path))
        try:
            assert {table: other.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in kept} == before
            sequences = {name for (name,) in other.execute("SELECT name FROM sqlite_sequence")}
        finally:
            other.close()
        assert not sequences & DROPPED                       # the autoincrement counters went with their tables
        meta = {r["key"]: r["value"] for r in store.select("schema_meta")}
        assert meta == {"schema_version": "4", "created_at": "2026-01-01T00:00:00Z", "migrated_at": clock.iso()}
        assert store.integrity_check() == []
        with pytest.raises(sqlite3.IntegrityError):             # the live-dedupe index survived the upgrade
            action(store, "a_twin", status="Queued", dedupe_key="k1")
    finally:
        store.close()


def test_the_whole_ladder_takes_a_version_1_file_to_the_schema_a_fresh_file_has(S, db_path, tmp_path, clock):
    """A database last opened by version 1 still upgrades in one ``open``: every step applies to a populated file
    (the version 2 column to a lease that has a row, the version 3 rewrite to retired statuses, the version 4 drops
    to tables with content), and what comes out is exactly the schema a fresh install builds."""
    raw = legacy_file(S, db_path, 1)
    for action_id, status in (("a_ran", "Processing"), ("a_unsure", "DeliveryUncertain"), ("a_new", "Queued")):
        raw.execute("INSERT INTO actions(action_id, type, risk_class, repo_id, intent_dir, space, status, created_at, "
                    "updated_at, waiting_since, delivered_at) VALUES(?, 'gate', 'human_lane', 'r_1', "
                    "'260904-gate-demo', 'default', ?, ?, ?, ?, ?)", (action_id, status, TS, TS, TS, TS))
    raw.execute("INSERT INTO admin_leases(resolved_repo_identity, repo_id, operation_type, acquired_at, heartbeat_at, "
                "generation) VALUES('1:r_1', 'r_1', 'install', ?, ?, 1)", (TS, TS))
    raw.execute("INSERT INTO events(at, type, payload_json) VALUES(?, 'repo.updated', '{}')", (TS,))
    raw.commit()
    raw.close()

    store = S.Storage(db_path, clock=clock)
    fresh = S.Storage(tmp_path / "fresh" / "studio.sqlite3", clock=clock)
    store.open()
    fresh.open()
    try:
        assert schema_of(db_path) == schema_of(fresh.path)
        assert {r["action_id"]: r["status"] for r in store.select("actions")} == {
            "a_ran": "Delivered", "a_unsure": "ReconciliationRequired", "a_new": "Queued",
        }
        assert store.get("actions", "a_ran")["evidence_json"] == {"turn_started_at": TS}
        assert store.get("repos", "r_1")["label"] == "r_1"
        assert store.pragmas()["schema_version"] == fresh.pragmas()["schema_version"] == S.SCHEMA_VERSION
        assert store.integrity_check() == []
    finally:
        store.close()
        fresh.close()


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


def test_reopening_at_the_current_version_is_a_no_op(S, db_path, clock):
    st = S.Storage(db_path, clock=clock)
    st.open()
    repo(st)
    stamps = {r["key"]: r["value"] for r in st.select("schema_meta")}
    st.insert("schema_meta", {"key": "sentinel", "value": "keep-me"})
    st.close()

    clock.advance(3600)
    again = S.Storage(db_path, clock=clock)
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


def test_a_file_written_by_a_newer_schema_is_refused(S, db_path, clock, studio):
    st = S.Storage(db_path, clock=clock)
    st.open()
    st.close()
    raw = sqlite3.connect(str(db_path))
    raw.execute("UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'")
    raw.commit()
    raw.close()

    with pytest.raises(studio.errors.StudioError) as exc:
        S.Storage(db_path, clock=clock).open()
    assert exc.value.code == "storage_error"
    assert exc.value.details == {"found": 99, "supported": S.SCHEMA_VERSION}


def test_use_before_open_and_after_close_is_a_storage_error(S, db_path, clock, studio):
    st = S.Storage(db_path, clock=clock)
    with pytest.raises(studio.errors.StudioError) as before:
        st.pref_get("locale")
    assert before.value.code == "storage_unavailable"
    st.open()
    st.close()
    with pytest.raises(studio.errors.StudioError) as after:
        st.pref_get("locale")
    assert after.value.code == "storage_unavailable"
    st.open()          # open() is idempotent and re-usable after a close
    assert st.integrity_check() == []
    st.close()


def test_every_public_method_is_one_transaction(S):
    """A public method that writes outside ``_txn`` would break the one-transaction rule silently."""
    source = Path(S.__file__).read_text("utf-8")
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Storage")
    # These only read PRAGMAs or lifecycle state.
    outside = {"open", "close", "pragmas", "integrity_check", "path"}
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
    assert {"cas_update", "activity_query"} <= seen


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
    repo(store)
    binding(store)
    return "r_1:default:260904-gate-demo", {"paused": 1}


@pytest.mark.parametrize("table", ["actions", "intent_bindings"])
def test_cas_update_bumps_the_generation_and_updated_at(store, S, clock, table):
    gen_col = S.CAS_TABLES[table]
    key, values = _cas_case(store, table)
    before = store.get(table, key)
    clock.advance(60)

    new_gen = store.cas_update(table, key, before[gen_col], values)
    after = store.get(table, key)
    assert new_gen == before[gen_col] + 1 == after[gen_col]
    assert after["updated_at"] == clock.iso() != before["updated_at"]
    for name, value in values.items():
        assert after[name] == value


@pytest.mark.parametrize("table", ["actions", "intent_bindings"])
def test_cas_update_raises_stale_generation_with_expected_and_actual(store, S, studio, table):
    gen_col = S.CAS_TABLES[table]
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


def test_a_committed_cas_is_visible_to_a_second_connection(store):
    """The submit path promises the ``Delivering`` record is durable before the browser is told to send. A
    second connection can only see it if the transaction really committed (synchronous=FULL, not deferred)."""
    repo(store)
    action(store)
    assert store.cas_update("actions", "a_1", 0, {"status": "Delivering", "delivery_id": "dl_1"}) == 1
    other = sqlite3.connect(str(store.path))
    try:
        assert other.execute("SELECT status, delivery_id FROM actions").fetchall() == [("Delivering", "dl_1")]
    finally:
        other.close()


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


def test_activity_delete_before_drops_only_rows_older_than_the_cutoff(store, S):
    old_id = store.activity_append(activity_row(S, at="2026-08-01T00:00:00Z", kind="repo.added"))
    edge_id = store.activity_append(activity_row(S, at="2026-09-01T00:00:00Z", kind="repo.added"))
    assert store.activity_delete_before("2026-09-01T00:00:00Z") == 1
    assert [r.id for r in store.activity_query(S.ActivityQuery(limit=10))[0]] == [edge_id]
    assert old_id != edge_id and store.activity_delete_before("2026-09-01T00:00:00Z") == 0


# --------------------------------------------------------------------------- #
# preferences and housekeeping
# --------------------------------------------------------------------------- #


def test_preferences_round_trip_every_json_shape(store, clock):
    assert store.pref_get("locale") is None
    store.pref_set("locale", "zh-CN")
    store.pref_set("runtime.bun", {"path": "/opt/bun/bin/bun", "probe": {"ok": True, "tries": 2}})
    store.pref_set("muted_repo_ids", ["r_1", "r_2"])
    store.pref_set("credit_cap", None)
    store.pref_set("locale", "en-US")                      # upsert, not a duplicate row

    assert store.pref_get("locale") == "en-US"
    assert store.pref_get("runtime.bun")["probe"]["tries"] == 2
    assert store.pref_get("muted_repo_ids") == ["r_1", "r_2"]
    assert store.pref_get("credit_cap") is None
    assert [r["key"] for r in store.select("preferences", order_by="key")] == [
        "credit_cap", "locale", "muted_repo_ids", "runtime.bun",
    ]
    assert store.get("preferences", "locale")["updated_at"] == clock.iso()


def test_the_injectable_protocols_live_in_constants(C, clock, ids):
    """§0.2 puts Clock (and with it IdFactory) in constants.py; storage only annotates them."""
    assert hasattr(C, "Clock") and hasattr(C, "IdFactory")
    for name in ("now", "iso", "monotonic"):
        assert callable(getattr(clock, name)) and hasattr(C.Clock, name)
    assert callable(ids.new) and hasattr(C.IdFactory, "new")
    assert isinstance(clock.iso(), str) and clock.iso().endswith("Z")
    assert F.sha256("x")                                    # harvested fixtures import cleanly
