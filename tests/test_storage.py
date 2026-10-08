"""Crash safety of ``backend/studio/storage.py``: a compare-and-set exactly one racer wins, durable commits, a
migration ladder that keeps every row it should, stable activity paging and a purge that clears only human text.
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
    store.insert("repos", {"id": repo_id, "resolved_identity": f"1:{repo_id}" if identity is _UNSET else identity,
                           "canonical_path": f"/FIXTURE/repo/{repo_id}", "label": repo_id, "added_at": TS,
                           "platform": "darwin", **over})
    return repo_id


def action(store, action_id="a_1", *, repo_id="r_1", status="Queued", **over):
    store.insert("actions", {"action_id": action_id, "type": "gate", "risk_class": "human_lane", "repo_id": repo_id,
                             "intent_dir": "260904-gate-demo", "space": "default", "status": status, "created_at": TS,
                             "updated_at": TS, "waiting_since": TS, **over})
    return action_id


def binding(store, key="r_1:default:260904-gate-demo", *, repo_id="r_1", **over):
    store.insert("intent_bindings", {"binding_key": key, "repo_id": repo_id, "space": "default",
                                     "intent_dir": "260904-gate-demo", "created_at": TS, "updated_at": TS, **over})
    return key


def activity_row(S, **over):
    return S.ActivityRow(**{"at": TS, "source": "studio", "kind": "action.created", "severity": "info",
                            "message_key": "activity.action.created", **over})


#: The tables version 4 dropped: nothing has read or written them since leases, events and calibration moved.
DROPPED = {"execution_leases", "admin_leases", "lease_generations", "events", "calibration", "migrations"}


def raw_insert(raw, table: str, **cols) -> None:
    """One row through a raw connection, the way an older build wrote it."""
    raw.execute(f"INSERT INTO {table}({', '.join(cols)}) VALUES({', '.join('?' * len(cols))})", tuple(cols.values()))


def legacy_file(S, db_path, version):
    """A raw connection to a database exactly as ``version`` left it: the ladder up to there, stamped so."""
    raw = sqlite3.connect(str(db_path))
    for target in range(1, version + 1):
        for statement in S._statements(S.MIGRATIONS[target]):
            raw.execute(statement)
    raw_insert(raw, "schema_meta", key="schema_version", value=str(version))
    raw_insert(raw, "repos", id="r_1", resolved_identity="1:r_1", canonical_path="/FIXTURE/repo/r_1", label="r_1",
               added_at=TS, platform="darwin")
    return raw


def schema_of(path):
    """Every table and index definition in the file, as SQLite itself stores them."""
    raw = sqlite3.connect(str(path))
    try:
        return raw.execute("SELECT type, name, tbl_name, sql FROM sqlite_master "
                           "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
    finally:
        raw.close()


def opened(S, db_path, clock):
    st = S.Storage(db_path, clock=clock)
    st.open()
    return st


# ---- schema, PRAGMAs, migration path ---- #


def test_ddl_applies_to_an_empty_file(store, S, db_path):
    assert db_path.is_file()
    assert {r["key"] for r in store.select("schema_meta")} >= {"schema_version", "created_at", "migrated_at"}
    for table in sorted(set(S.TABLE_KEYS) - {"schema_meta"}):  # every table exists; only schema_meta has rows
        assert store.select(table, limit=1) == []
    # ...and the key map is every table there is: a fresh file runs the whole ladder, drops included.
    assert {name for kind, name, _, _ in schema_of(db_path) if kind == "table"} == set(S.TABLE_KEYS)
    assert store.pragmas()["schema_version"] == S.SCHEMA_VERSION == 4
    assert store.integrity_check() == []


def test_the_partial_dedupe_index_covers_exactly_the_live_statuses(store, C):
    """Read from the file: the index in force is the one the latest migration built, not ``SCHEMA_V1``'s."""
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
    """``Processing`` was a started ``Delivered``, ``DeliveryUncertain`` an early ``ReconciliationRequired`` and
    ``Draft`` was never created; the turn's start is kept as evidence and the dedupe index is rebuilt."""
    raw = legacy_file(S, db_path, 2)

    def old_row(action_id, status, evidence, *, delivered_at=None, dedupe=None):
        raw_insert(raw, "actions", action_id=action_id, type="gate", risk_class="human_lane", repo_id="r_1",
                   intent_dir="260904-gate-demo", space="default", status=status, created_at=TS,
                   updated_at="2026-09-04T10:05:00Z", waiting_since=TS, delivered_at=delivered_at,
                   evidence_json=None if evidence is None else json.dumps(evidence), dedupe_key=dedupe)

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
        raw_insert(raw, "action_transitions", action_id="a_unsure", from_status=from_status, to_status=to_status,
                   generation=1, at=TS, reason=reason)
    raw.commit()
    raw.close()

    store = opened(S, db_path, clock)
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
    """Upgrading a version 3 file removes exactly the six dropped tables and leaves every other row as it was."""
    raw = legacy_file(S, db_path, 3)
    raw_insert(raw, "schema_meta", key="created_at", value="2026-01-01T00:00:00Z")
    raw_insert(raw, "actions", action_id="a_1", type="gate", risk_class="human_lane", repo_id="r_1",
               intent_dir="260904-gate-demo", space="default", status="Delivered", created_at=TS, updated_at=TS,
               waiting_since=TS, lease_generation=7, dedupe_key="k1")
    raw_insert(raw, "action_transitions", action_id="a_1", from_status="Delivering", to_status="Delivered",
               generation=2, at=TS, reason="ack")
    raw_insert(raw, "intent_bindings", binding_key="r_1:default:260904-gate-demo", repo_id="r_1", space="default",
               intent_dir="260904-gate-demo", created_at=TS, updated_at=TS)
    raw_insert(raw, "preferences", key="settings", value_json='{"locale": "zh-CN"}', updated_at=TS)
    raw_insert(raw, "activity", at=TS, source="studio", kind="repo.added", severity="info", repo_id="r_1",
               message_key="activity.repo.added", params_json="{}")
    raw_insert(raw, "breakers", key="r_1:260904-gate-demo:fp", count=2, reason="transport")
    raw_insert(raw, "install_transactions", transaction_id="tx_1", repo_id="r_1", kind="install", status="committed",
               studio_version="1.1.6", engine_version="2.10.0", payload_digest="d", started_at=TS)
    raw_insert(raw, "install_receipts", receipt_id="rc_1", repo_id="r_1", studio_version="1.1.6",
               engine_version="2.10.0", payload_digest="d", committed_at=TS, transaction_id="tx_1", files_json="[]",
               status="current")
    raw_insert(raw, "advisor_drafts", draft_id="d_1", action_id="a_1", kind="gate_analysis", status="ready",
               request_json="{}", created_at=TS, updated_at=TS, expires_at=TS)
    # One row in each dropped table, as the last version 3 process could have left it.
    raw_insert(raw, "execution_leases", resolved_repo_identity="1:r_1", repo_id="r_1", action_id="a_1",
               acquired_at=TS, heartbeat_at=TS, generation=7)
    raw_insert(raw, "admin_leases", resolved_repo_identity="2:r_2", repo_id="r_1", operation_type="install",
               acquired_at=TS, heartbeat_at=TS, generation=3, boot_id="boot-old")
    raw_insert(raw, "lease_generations", resolved_repo_identity="1:r_1", last_generation=7)
    raw_insert(raw, "events", at=TS, type="repo.updated", payload_json="{}")
    raw_insert(raw, "calibration", at=TS, scope="poc", depth="Standard", stage_class="operation", est_json="{}",
               actual_json="{}")
    raw_insert(raw, "migrations", id="console", applied_at=TS, status="applied", summary_json="{}")
    raw.commit()
    kept = sorted(set(S.TABLE_KEYS) - {"schema_meta"})
    before = {table: raw.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in kept}
    raw.close()
    assert all(before.values()), "every kept table carries a row into the upgrade"

    clock.advance(3600)
    store = opened(S, db_path, clock)
    try:
        assert {name for kind, name, _, _ in schema_of(db_path) if kind == "table"} == set(S.TABLE_KEYS)
        other = sqlite3.connect(str(db_path))
        try:
            after = {table: other.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() for table in kept}
            assert after == before
            sequences = {name for (name,) in other.execute("SELECT name FROM sqlite_sequence")}
        finally:
            other.close()
        assert not sequences & DROPPED  # the autoincrement counters went with their tables
        meta = {r["key"]: r["value"] for r in store.select("schema_meta")}
        assert meta == {"schema_version": "4", "created_at": "2026-01-01T00:00:00Z", "migrated_at": clock.iso()}
        assert store.integrity_check() == []
        with pytest.raises(sqlite3.IntegrityError):  # the live-dedupe index survived the upgrade
            action(store, "a_twin", status="Queued", dedupe_key="k1")
    finally:
        store.close()


def test_the_whole_ladder_takes_a_version_1_file_to_the_schema_a_fresh_file_has(S, db_path, tmp_path, clock):
    """A version 1 file upgrades in one ``open``, every step applied to a populated file."""
    raw = legacy_file(S, db_path, 1)
    for action_id, status in (("a_ran", "Processing"), ("a_unsure", "DeliveryUncertain"), ("a_new", "Queued")):
        raw_insert(raw, "actions", action_id=action_id, type="gate", risk_class="human_lane", repo_id="r_1",
                   intent_dir="260904-gate-demo", space="default", status=status, created_at=TS, updated_at=TS,
                   waiting_since=TS, delivered_at=TS)
    raw_insert(raw, "admin_leases", resolved_repo_identity="1:r_1", repo_id="r_1", operation_type="install",
               acquired_at=TS, heartbeat_at=TS, generation=1)
    raw_insert(raw, "events", at=TS, type="repo.updated", payload_json="{}")
    raw.commit()
    raw.close()

    store = opened(S, db_path, clock)
    fresh = opened(S, tmp_path / "fresh" / "studio.sqlite3", clock)
    try:
        assert schema_of(db_path) == schema_of(fresh.path)
        assert {r["action_id"]: r["status"] for r in store.select("actions")} == {
            "a_ran": "Delivered", "a_unsure": "ReconciliationRequired", "a_new": "Queued"}
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
    assert p["synchronous"] == 2  # FULL
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
    st = opened(S, db_path, clock)
    repo(st)
    stamps = {r["key"]: r["value"] for r in st.select("schema_meta")}
    st.insert("schema_meta", {"key": "sentinel", "value": "keep-me"})
    st.close()
    clock.advance(3600)
    again = opened(S, db_path, clock)
    try:
        after = {r["key"]: r["value"] for r in again.select("schema_meta")}
        assert after["migrated_at"] == stamps["migrated_at"]  # no migration ran
        assert after["created_at"] == stamps["created_at"]
        assert after["sentinel"] == "keep-me"
        assert again.get("repos", "r_1")["label"] == "r_1"
        assert again.integrity_check() == []
    finally:
        again.close()


def test_a_file_written_by_a_newer_schema_is_refused(S, db_path, clock, studio):
    opened(S, db_path, clock).close()
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
    st.open()  # open() is idempotent and re-usable after a close
    assert st.integrity_check() == []
    st.close()


def test_every_public_method_is_one_transaction(S):
    """A public method that writes outside ``_txn`` would break the one-transaction rule silently."""
    source = Path(S.__file__).read_text("utf-8")
    cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "Storage")
    outside = {"open", "close", "pragmas", "integrity_check", "path"}  # these only read PRAGMAs or lifecycle state
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


# ---- generic helpers ---- #


def test_generic_crud_roundtrips_json_columns_and_validates_names(store):
    repo(store, scan_json={"intents": 2, "findings": []})
    assert store.get("repos", "r_1")["scan_json"] == {"intents": 2, "findings": []}  # decoded, not raw text
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


# ---- cas_update ---- #


def _cas_case(store, table):
    """One live row per CAS table, plus the value the test writes to it."""
    repo(store)
    if table == "actions":
        action(store)
        return "a_1", {"status": "Delivering"}
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
    assert store.get(table, key)[gen_col] == actual  # nothing was written
    with pytest.raises(studio.errors.StaleGeneration) as missing:
        store.cas_update(table, "absent-key", 0, values)
    assert missing.value.details == {"expected": 0, "actual": None, "table": table, "key": "absent-key"}


def test_cas_update_refuses_an_unknown_table_and_caller_owned_columns(store):
    repo(store)
    action(store)
    for table, key, values in (("repos", "r_1", {"label": "x"}),  # no generation column
                               ("actions", "a_1", {"status_generation": 7}), ("actions", "a_1", {"updated_at": TS})):
        with pytest.raises(ValueError):
            store.cas_update(table, key, 0, values)


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
    """``Delivering`` is durable before the browser is told to send, so another connection sees it at once."""
    repo(store)
    action(store)
    assert store.cas_update("actions", "a_1", 0, {"status": "Delivering", "delivery_id": "dl_1"}) == 1
    other = sqlite3.connect(str(store.path))
    try:
        assert other.execute("SELECT status, delivery_id FROM actions").fetchall() == [("Delivering", "dl_1")]
    finally:
        other.close()


# ---- the actions_live_dedupe partial index ---- #

DEDUPE_KEY = "gate:r_1:260904-gate-demo:requirements-analysis:tok"


@pytest.mark.parametrize("terminal", ["Failed", "Cancelled", "StateChanged"])
def test_a_live_card_may_sit_beside_a_terminal_twin(store, terminal):
    repo(store)
    action(store, "a_old", status=terminal, dedupe_key=DEDUPE_KEY)
    action(store, "a_new", status="Queued", dedupe_key=DEDUPE_KEY)
    assert {r["action_id"] for r in store.select("actions", {"dedupe_key": DEDUPE_KEY})} == {"a_old", "a_new"}


def test_a_second_live_card_for_one_boundary_is_refused(store):
    repo(store)
    action(store, "a_1", status="Queued", dedupe_key=DEDUPE_KEY)
    with pytest.raises(sqlite3.IntegrityError):
        action(store, "a_2", status="Delivering", dedupe_key=DEDUPE_KEY)
    assert store.get("actions", "a_2") is None
    action(store, "a_3", dedupe_key=None)  # a null key never collides: derived cards have no boundary token
    action(store, "a_4", dedupe_key=None)


# ---- activity ---- #


def test_activity_paging_is_stable_across_pages(store, S):
    repo(store)
    ids_written = [store.activity_append(activity_row(S, repo_id="r_1", params={"n": n})) for n in range(7)]
    assert ids_written == list(range(1, 8))
    # Every row shares one second: an `at`-keyed cursor would skip or repeat rows here.
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
    assert seen == sorted(ids_written, reverse=True)  # newest first, no gaps, no repeats


def test_activity_query_filters_and_derives_the_intent_key(store, S, clock):
    repo(store)
    action(store, "a_1", type="gate")
    action(store, "a_2", type="run")
    store.activity_append(activity_row(S, repo_id="r_1", kind="action.created", action_id="a_1",
                                       intent_dir="260904-gate-demo", stage="requirements-analysis"))
    clock.advance(60)
    store.activity_append(activity_row(S, at=clock.iso(), repo_id="r_1", kind="action.submitted", severity="attention",
                                       action_id="a_2", space="research", intent_dir="260904-gate-demo",
                                       source="studio"))
    clock.advance(60)
    store.activity_append(activity_row(S, at=clock.iso(), repo_id="r_2", kind="repo.added"))

    def kinds(**filters):
        return [r.kind for r in store.activity_query(S.ActivityQuery(**filters))[0]]

    assert kinds(repo_id="r_1") == ["action.submitted", "action.created"]
    assert kinds(repo_id="r_2") == ["repo.added"]
    assert kinds(severity="attention") == ["action.submitted"]
    assert kinds(stage="requirements-analysis") == ["action.created"]
    assert kinds(kind="repo.added") == ["repo.added"]
    # action_type has no column of its own; it resolves through the actions table.
    assert [r.action_id for r in store.activity_query(S.ActivityQuery(action_type="run"))[0]] == ["a_2"]
    assert [r.action_id for r in store.activity_query(S.ActivityQuery(action_type="gate"))[0]] == ["a_1"]
    assert kinds(since=clock.iso()) == ["repo.added"]
    assert kinds(until=TS) == ["action.created"]
    assert kinds(source="aidlc") == []
    default_space = store.activity_query(S.ActivityQuery(repo_id="r_1"))[0][1]
    research = store.activity_query(S.ActivityQuery(space="research"))[0][0]
    assert default_space.intent_key == "260904-gate-demo"  # C02: bare dir in the default space
    assert research.intent_key == "research~260904-gate-demo"
    assert store.activity_query(S.ActivityQuery(limit=500))[0][0].to_json()["evidence"] == []


def test_activity_evidence_and_limits_round_trip(store, S, studio):
    ref = S.EvidenceRef(kind="audit", ref="603e5f4a8072-26e77d17b1cd.md#12", detail="GATE_APPROVED")
    store.activity_append(activity_row(S, kind="action.resolved", evidence=(ref,), redacted=True))
    row = store.activity_query(S.ActivityQuery())[0][0]
    assert row.evidence == (ref,) and row.redacted is True
    assert row.to_json()["evidence"] == [{"kind": "audit", "ref": "603e5f4a8072-26e77d17b1cd.md#12",
                                         "detail": "GATE_APPROVED"}]
    assert store.activity_query(S.ActivityQuery(limit=10_000))[1] is None  # clamped, not refused
    with pytest.raises(ValueError):
        store.activity_append(S.ActivityRow(at=TS, kind="", message_key=""))
    with pytest.raises(studio.errors.StudioError) as bad:
        store.activity_query(S.ActivityQuery(cursor="not-base64!!"))
    assert bad.value.code == "bad_param"


def test_activity_purge_clears_only_what_retention_allows(store, S):
    """The verbatim decision text is the only thing purged, in both tables that keep it."""
    repo(store)
    human = F.real_state()[:400] + "\n批准 — approve with the note above\n"
    old_id = store.activity_append(activity_row(S, at="2026-08-01T00:00:00Z", kind="action.submitted", repo_id="r_1",
                                                action_id="a_old", human_text=human, params={"decision": "approve"}))
    keep_id = store.activity_append(activity_row(S, at="2026-09-03T00:00:00Z", kind="action.submitted",
                                                 repo_id="r_1", action_id="a_new", human_text=human))
    action(store, "a_old", human_text=human, created_at="2026-08-01T00:00:00Z", delivering_at="2026-08-01T00:00:10Z")
    action(store, "a_new", human_text=human, created_at="2026-09-03T00:00:00Z", delivering_at="2026-09-03T00:00:10Z")
    assert store.activity_purge_human_text("2026-09-01T00:00:00Z") == 2  # one activity row + one action row
    rows = {r.id: r for r in store.activity_query(S.ActivityQuery(limit=10))[0]}
    assert rows[old_id].human_text is None
    assert rows[old_id].params == {"decision": "approve"}  # everything else is untouched
    assert rows[old_id].kind == "action.submitted" and rows[old_id].at == "2026-08-01T00:00:00Z"
    assert rows[keep_id].human_text == human  # still inside the retention window
    assert store.get("actions", "a_old")["human_text"] is None
    assert store.get("actions", "a_new")["human_text"] == human
    assert store.activity_purge_human_text("2026-09-01T00:00:00Z") == 0  # idempotent


def test_activity_delete_before_drops_only_rows_older_than_the_cutoff(store, S):
    old_id = store.activity_append(activity_row(S, at="2026-08-01T00:00:00Z", kind="repo.added"))
    edge_id = store.activity_append(activity_row(S, at="2026-09-01T00:00:00Z", kind="repo.added"))
    assert store.activity_delete_before("2026-09-01T00:00:00Z") == 1
    assert [r.id for r in store.activity_query(S.ActivityQuery(limit=10))[0]] == [edge_id]
    assert old_id != edge_id and store.activity_delete_before("2026-09-01T00:00:00Z") == 0


# ---- preferences and housekeeping ---- #


def test_preferences_round_trip_every_json_shape(store, clock):
    assert store.pref_get("locale") is None
    store.pref_set("locale", "zh-CN")
    store.pref_set("runtime.bun", {"path": "/opt/bun/bin/bun", "probe": {"ok": True, "tries": 2}})
    store.pref_set("muted_repo_ids", ["r_1", "r_2"])
    store.pref_set("credit_cap", None)
    store.pref_set("locale", "en-US")  # upsert, not a duplicate row
    assert store.pref_get("locale") == "en-US"
    assert store.pref_get("runtime.bun")["probe"]["tries"] == 2
    assert store.pref_get("muted_repo_ids") == ["r_1", "r_2"]
    assert store.pref_get("credit_cap") is None
    assert [r["key"] for r in store.select("preferences", order_by="key")] == [
        "credit_cap", "locale", "muted_repo_ids", "runtime.bun"]
    assert store.get("preferences", "locale")["updated_at"] == clock.iso()


def test_the_injectable_protocols_live_in_constants(C, clock, ids):
    """§0.2 puts Clock (and with it IdFactory) in constants.py; storage only annotates them."""
    assert hasattr(C, "Clock") and hasattr(C, "IdFactory")
    for name in ("now", "iso", "monotonic"):
        assert callable(getattr(clock, name)) and hasattr(C.Clock, name)
    assert callable(ids.new) and hasattr(C.IdFactory, "new")
    assert isinstance(clock.iso(), str) and clock.iso().endswith("Z")
    assert F.sha256("x")  # harvested fixtures import cleanly
