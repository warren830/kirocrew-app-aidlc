"""Studio's own crash-safe store: one SQLite file, one connection, one transaction per public method.

The host's ``ctx.storage`` has no fsync, no locking and no compare-and-set (``01 §2.3``), and the PRD's durability
promises (a decision on disk *before* it is sent, an action status only one writer may advance) need all three:

* **One connection behind an ``RLock``**, so "one transaction per public method" is literal: no second connection
  can interleave a half-finished write.
* **``BEGIN IMMEDIATE``, never the driver's implicit transaction.** A deferred one takes its write lock halfway
  through and can fail with ``SQLITE_BUSY`` after the generation it compares against was read.
* **WAL + ``synchronous=FULL``.** With ``NORMAL`` a power loss can drop a committed ``Delivering`` row, and Studio
  would replay a decision the model has already seen.
* **``foreign_keys=ON``**, so removing a repository cannot leave actions and bindings a later scan resurrects.

Sync and blocking: callers use ``asyncio.to_thread`` (§0.6); the lock is held only across this module's own calls.
"""

from __future__ import annotations

import base64
import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Mapping

from . import constants as C
from .errors import StaleGeneration, StudioError
from .wire import WireRecord

if TYPE_CHECKING:  # annotations only
    from .constants import Clock

#: Bumped only together with a new entry in ``MIGRATIONS``; ``open()`` on a file already there is a no-op.
SCHEMA_VERSION = 4

#: Each script is the historical record of its version: ``SCHEMA_V1`` stays exactly as it shipped, so a fresh file
#: (which runs the whole ladder) and a migrated one agree on what every version was.
SCHEMA_V1 = """\
CREATE TABLE IF NOT EXISTS schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS repos(
  id TEXT PRIMARY KEY,                       -- r_xxxxxxxxxxxx
  resolved_identity TEXT UNIQUE,             -- NULL only while availability='identity_unprovable'
  canonical_path TEXT NOT NULL,
  git_common_dir_identity TEXT,
  label TEXT NOT NULL,
  added_at TEXT NOT NULL, last_seen TEXT,
  platform TEXT NOT NULL,                    -- "darwin" | "linux" | "win32"
  install_status TEXT NOT NULL DEFAULT 'not_installed',
  installed_engine_version TEXT, engine_dir TEXT,   -- e.g. ".kiro"
  receipt_version TEXT,                      -- receipt_id of the current receipt
  archived INTEGER NOT NULL DEFAULT 0,
  availability TEXT NOT NULL DEFAULT 'available', availability_detail TEXT,
  legacy_console_id TEXT,
  scan_json TEXT                              -- last RepoScan summary (cache; never authoritative)
);

CREATE TABLE IF NOT EXISTS intent_bindings(
  binding_key TEXT PRIMARY KEY,              -- f"{repo_id}:{space}:{intent_dir}"
  repo_id TEXT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  resolved_repo_identity TEXT,
  space TEXT NOT NULL, intent_uuid TEXT, intent_dir TEXT NOT NULL, intent_slug TEXT,
  canonical_session_key TEXT,                -- host session key (dashboard:<slot>) ; NULL = unbound
  slot_key TEXT,                             -- host slot name the UI created
  binding_generation INTEGER NOT NULL DEFAULT 0,
  keep_moving INTEGER NOT NULL DEFAULT 0,    -- always 0 in v1
  archive_state TEXT NOT NULL DEFAULT 'active',   -- 'active' | 'archived'
  paused INTEGER NOT NULL DEFAULT 0, paused_at TEXT,
  interrupted_at TEXT,                       -- set when a force_stop action resolves; cleared ONLY by the recovery card's `acknowledge` (review P17)
  last_stable_boundary_json TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(repo_id, space, intent_dir)
);

CREATE TABLE IF NOT EXISTS actions(
  action_id TEXT PRIMARY KEY,
  type TEXT NOT NULL, risk_class TEXT NOT NULL,
  repo_id TEXT NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  resolved_repo_identity TEXT,
  intent_uuid TEXT, intent_dir TEXT NOT NULL, space TEXT NOT NULL,
  stage TEXT, unit TEXT,
  captured_state_hash TEXT, boundary_token TEXT, question_digest TEXT, stage_attempt INTEGER,
  evidence_digest TEXT,                       -- sha256 over the stage's produced artifacts + parsed Review section (review P14)
  is_active_captured INTEGER,                 -- cursor == this intent at capture (compare-and-submit set; review R01)
  payload_json TEXT,                          -- decision payload (redacted copy of human_text kept in human_text)
  payload_digest TEXT,
  human_text TEXT,                            -- exact text submitted; purged after retention
  wire_text TEXT,
  status TEXT NOT NULL, status_generation INTEGER NOT NULL DEFAULT 0,
  session_key TEXT, slot_key TEXT, delivery_id TEXT, lease_generation INTEGER,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  delivering_at TEXT, delivered_at TEXT, resolved_at TEXT, deadline_at TEXT,
  evidence_json TEXT,                          -- EvidenceSnapshot at creation (§1.8)
  cursor_readback_json TEXT,                   -- CursorReadback recorded at submit step 7 (review P01)
  presence_baseline_json TEXT,                 -- PresenceBaseline recorded at submit step 8 (review P04/R02)
  boot_id TEXT,                                -- Services.boot_id of the process that committed Delivering (review R02)
  resolution_json TEXT,                        -- ResolutionEvidence when terminal
  retry_fingerprint TEXT,
  source TEXT NOT NULL DEFAULT 'studio',
  waiting_since TEXT NOT NULL,
  dedupe_key TEXT                              -- NOT a column UNIQUE: uniqueness is enforced among live rows only (below)
);
CREATE INDEX IF NOT EXISTS actions_live ON actions(status, repo_id, intent_dir);
-- One live card per boundary; terminal rows (StateChanged, ResolvedNoTransition, Failed, Cancelled) keep their key for
-- history, so a retry of a Failed run or a resubmit after ReconciliationRequired never collides (review P06/R12).
CREATE UNIQUE INDEX IF NOT EXISTS actions_live_dedupe ON actions(dedupe_key)
  WHERE dedupe_key IS NOT NULL AND status IN ('Queued','Delivering','Delivered','Processing','DeliveryUncertain','ReconciliationRequired','NotDelivered');

CREATE TABLE IF NOT EXISTS action_transitions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action_id TEXT NOT NULL REFERENCES actions(action_id) ON DELETE CASCADE,
  from_status TEXT NOT NULL, to_status TEXT NOT NULL, generation INTEGER NOT NULL,
  at TEXT NOT NULL, reason TEXT NOT NULL, evidence_json TEXT
);

CREATE TABLE IF NOT EXISTS execution_leases(
  resolved_repo_identity TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL, intent_uuid TEXT, session_key TEXT, action_id TEXT,
  acquired_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL,
  generation INTEGER NOT NULL, observed_busy_state TEXT
);
CREATE TABLE IF NOT EXISTS admin_leases(
  resolved_repo_identity TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL, operation_type TEXT NOT NULL, transaction_id TEXT,
  acquired_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, generation INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS lease_generations(resolved_repo_identity TEXT PRIMARY KEY, last_generation INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS install_transactions(
  transaction_id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL, resolved_repo_identity TEXT,
  kind TEXT NOT NULL,                          -- 'install' | 'upgrade' | 'recovery'
  status TEXT NOT NULL,                        -- see §1.14 TransactionStatus
  studio_version TEXT NOT NULL, engine_version TEXT NOT NULL, payload_digest TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT,
  staging_dir TEXT, backup_dir TEXT, failed_dir TEXT, prior_receipt_id TEXT,
  steps_json TEXT NOT NULL DEFAULT '[]', error TEXT
);

CREATE TABLE IF NOT EXISTS install_receipts(
  receipt_id TEXT PRIMARY KEY,
  repo_id TEXT NOT NULL, resolved_repo_identity TEXT,
  studio_version TEXT NOT NULL, engine_version TEXT NOT NULL, payload_digest TEXT NOT NULL,
  committed_at TEXT NOT NULL, prior_receipt_id TEXT, transaction_id TEXT NOT NULL,
  files_json TEXT NOT NULL,                    -- list[ReceiptFile] §1.14
  status TEXT NOT NULL                          -- 'current' | 'superseded' | 'rolled_back'
);

CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS activity(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL,
  repo_id TEXT, intent_dir TEXT, space TEXT, stage TEXT, action_id TEXT, session_key TEXT,
  message_key TEXT NOT NULL, params_json TEXT,   -- i18n key + params (never English prose)
  evidence_json TEXT, redacted INTEGER NOT NULL DEFAULT 0,
  human_text TEXT                                 -- only for kind='action.submitted'; purged by retention
);
CREATE INDEX IF NOT EXISTS activity_at ON activity(at);
CREATE INDEX IF NOT EXISTS activity_scope ON activity(repo_id, intent_dir, at);

CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, type TEXT NOT NULL, payload_json TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS calibration(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
  scope TEXT NOT NULL, depth TEXT NOT NULL, stage_class TEXT NOT NULL, model_class TEXT,
  review_iterations INTEGER, test_secs INTEGER,
  est_json TEXT NOT NULL, actual_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS breakers(
  key TEXT PRIMARY KEY,                         -- f"{repo_id}:{intent_key}:{fingerprint}"
  count INTEGER NOT NULL DEFAULT 0, opened_at TEXT, reason TEXT, last_error_json TEXT, fingerprint_class TEXT
);

CREATE TABLE IF NOT EXISTS advisor_drafts(
  draft_id TEXT PRIMARY KEY, action_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
  spawn_id TEXT, request_json TEXT NOT NULL, result_json TEXT, error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS migrations(
  id TEXT PRIMARY KEY, applied_at TEXT NOT NULL, status TEXT NOT NULL, backup_path TEXT, summary_json TEXT NOT NULL
);
"""

#: Version 2 gave admin leases the ``boot_id`` that proved their owner dead.
SCHEMA_V2 = """\
ALTER TABLE admin_leases ADD COLUMN boot_id TEXT;
"""

#: Version 3 folds the transient action statuses into their neighbours: ``Processing`` becomes ``Delivered`` with
#: ``evidence.turn_started_at`` (the delivery's stamp when it recorded none: it was not queued), ``DeliveryUncertain``
#: ``ReconciliationRequired`` and the never-created ``Draft`` ``Queued``. History keeps ``Processing``, a label the UI
#: still has, and the live-dedupe index is rebuilt over the statuses that remain.
SCHEMA_V3 = """\
UPDATE actions SET status = 'Delivered', evidence_json = json_set(COALESCE(evidence_json, '{}'),
  '$.turn_started_at', COALESCE(json_extract(evidence_json, '$.turn_started_at'), delivered_at, updated_at))
  WHERE status = 'Processing';
UPDATE actions SET status = 'ReconciliationRequired' WHERE status = 'DeliveryUncertain';
UPDATE actions SET status = 'Queued' WHERE status = 'Draft';
UPDATE action_transitions SET from_status = CASE from_status WHEN 'DeliveryUncertain' THEN 'ReconciliationRequired'
  WHEN 'Draft' THEN 'Queued' ELSE from_status END, to_status = CASE to_status
  WHEN 'DeliveryUncertain' THEN 'ReconciliationRequired' WHEN 'Draft' THEN 'Queued' ELSE to_status END
  WHERE from_status IN ('DeliveryUncertain', 'Draft') OR to_status IN ('DeliveryUncertain', 'Draft');
DROP INDEX IF EXISTS actions_live_dedupe;
CREATE UNIQUE INDEX actions_live_dedupe ON actions(dedupe_key)
  WHERE dedupe_key IS NOT NULL AND status IN ('Queued','Delivering','Delivered','ReconciliationRequired','NotDelivered');
"""

#: Version 4 drops the tables nothing reads any more: a lease is an in-flight action or a ``flock`` (§1.10), events
#: live in an in-memory ring, calibration is gone, and the console migration that wrote ``migrations`` never ran.
SCHEMA_V4 = """\
DROP TABLE IF EXISTS execution_leases;
DROP TABLE IF EXISTS admin_leases;
DROP TABLE IF EXISTS lease_generations;
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS calibration;
DROP TABLE IF EXISTS migrations;
"""

#: ``target_version -> script``. Data, so ``open()`` is a plain loop and a file stamped with a version this build
#: does not know is refused instead of written to with the wrong shape.
MIGRATIONS: dict[int, str] = {1: SCHEMA_V1, 2: SCHEMA_V2, 3: SCHEMA_V3, 4: SCHEMA_V4}

#: ``table -> generation column``; ``cas_update`` advances it and stamps ``updated_at``.
CAS_TABLES: dict[str, str] = {"actions": "status_generation", "intent_bindings": "binding_generation"}

#: ``table -> primary key``. The generic helpers refuse any other table, which also keeps a caller-supplied name
#: out of the SQL.
TABLE_KEYS: dict[str, str] = {
    "schema_meta": "key", "repos": "id", "intent_bindings": "binding_key", "actions": "action_id",
    "action_transitions": "id", "install_transactions": "transaction_id", "install_receipts": "receipt_id",
    "preferences": "key", "activity": "id", "breakers": "key", "advisor_drafts": "draft_id",
}


# ---- records -------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EvidenceRef(WireRecord):
    """A pointer to what justifies an activity row, never the thing itself. Defined here, not in ``activity.py``
    (which re-exports it), because the store builds these when it reads a row back."""

    kind: str
    ref: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ActivityRow(WireRecord):
    """One Studio activity row: ``message_key`` + ``params``, never prose, so the UI can render it in the user's
    locale. Every field defaults; ``activity_append`` checks the required ones and names what is missing."""

    id: int = 0
    at: str = ""
    source: str = "studio"
    kind: str = ""
    severity: str = "info"
    repo_id: str | None = None
    space: str | None = None
    intent_dir: str | None = None
    intent_key: str | None = None
    stage: str | None = None
    action_id: str | None = None
    session_key: str | None = None
    message_key: str = ""
    params: dict = field(default_factory=dict)
    evidence: tuple[EvidenceRef, ...] = ()
    redacted: bool = False
    human_text: str | None = None


@dataclass(frozen=True, slots=True)
class ActivityQuery:
    """Filters for one page of activity. ``cursor`` is opaque (§1.24: base64url of the last id)."""

    repo_id: str | None = None
    space: str | None = None
    intent_dir: str | None = None
    source: str | None = None
    stage: str | None = None
    kind: str | None = None
    action_type: str | None = None
    severity: str | None = None
    since: str | None = None
    until: str | None = None
    cursor: str | None = None
    limit: int = C.ACTIVITY_DEFAULT_LIMIT


#: The equality filters of ``ActivityQuery``, each a column of the same name.
_ACTIVITY_FILTERS = ("repo_id", "space", "intent_dir", "source", "stage", "kind", "severity")


# ---- helpers -------------------------------------------------------------- #


def _statements(script: str) -> Iterator[str]:
    """Split a DDL script into statements. Not ``executescript``: it commits the pending transaction first, which
    would break the one-transaction rule for migrations. Not ``split(";")``: two column comments contain one."""
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            yield buf.strip()
            buf = ""
    tail = buf.strip()
    if tail:  # pragma: no cover - only reachable if the schema text loses a semicolon
        yield tail


def _encode_cursor(value: int) -> str:
    return base64.urlsafe_b64encode(str(int(value)).encode("ascii")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> int:
    raw = cursor.strip()
    try:
        padded = raw + "=" * (-len(raw) % 4)
        return int(base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii"))
    except Exception:
        raise StudioError("bad_param", "malformed cursor", details={"param": "cursor"}) from None


def _decode_evidence(raw: str | None) -> tuple[EvidenceRef, ...]:
    data = json.loads(raw) if raw else []
    if isinstance(data, Mapping):
        data = [data]
    return tuple(
        EvidenceRef(kind=str(item.get("kind", "studio")), ref=str(item.get("ref", "")), detail=item.get("detail"))
        for item in data if isinstance(item, Mapping)
    )


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# ---- Storage -------------------------------------------------------------- #


class Storage:
    """The single writer for everything Studio owns. Sync; call through ``asyncio.to_thread``."""

    def __init__(self, path: Path, *, clock: "Clock") -> None:
        self._path = Path(path)
        self._clock = clock
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._depth = 0
        self._columns: dict[str, tuple[str, ...]] = {}

    # ---- lifecycle ----

    @property
    def path(self) -> Path:
        return self._path

    def open(self) -> None:
        """Create the file, apply the PRAGMAs, migrate up to ``SCHEMA_VERSION``. Idempotent."""
        with self._lock:
            if self._conn is not None:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._path), check_same_thread=False, isolation_level=None,
                                   timeout=C.SQLITE_BUSY_TIMEOUT_MS / 1000.0)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute(f"PRAGMA busy_timeout={int(C.SQLITE_BUSY_TIMEOUT_MS)}")
                conn.execute("PRAGMA foreign_keys=ON")
                self._conn = conn
                self._migrate()
                self._columns = self._load_columns(conn)
            except BaseException:
                self._conn = None
                conn.close()
                raise

    def close(self) -> None:
        """Close the connection. Idempotent."""
        with self._lock:
            conn, self._conn = self._conn, None
            self._depth = 0
            self._columns = {}
            if conn is not None:
                try:
                    conn.execute("PRAGMA optimize")
                except sqlite3.Error:  # pragma: no cover - shutdown must not fail on a hint
                    pass
                conn.close()

    # ---- generic ----

    def insert(self, table: str, row: Mapping[str, Any]) -> None:
        """Insert one row; ``*_json`` values are encoded (a ``str`` is taken as already encoded).

        A unique-index violation stays an ``sqlite3.IntegrityError``: an ``actions_live_dedupe`` collision tells
        the caller a live card for that boundary already exists, which is not a 500.
        """
        self._table_columns(table)
        values = self._encode_row(table, row)
        if not values:
            raise ValueError(f"insert into {table} with no columns")
        placeholders = ", ".join(f":{name}" for name in values)
        names = ", ".join(values)
        with self._txn() as conn:
            conn.execute(f"INSERT INTO {table}({names}) VALUES({placeholders})", values)

    def get(self, table: str, key: str) -> dict | None:
        """One row by primary key, ``*_json`` decoded. An integer key may be passed as ``int`` or ``str``."""
        pk = self._pk(table)
        with self._txn() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE {pk} = ?", (key,)).fetchone()
        return self._decode_row(row)

    def select(
        self,
        table: str,
        where: Mapping[str, Any] | None = None,
        *,
        order_by: str = "",
        limit: int | None = None,
    ) -> list[dict]:
        """Rows matching an equality filter: a list/tuple/set value is an ``IN``, ``None`` is ``IS NULL``. Every
        name is checked against the real schema, so nothing a caller supplies reaches the SQL unchecked."""
        clause, params = self._where(table, where)
        sql = f"SELECT * FROM {table}{clause}"
        if order_by:
            sql += f" ORDER BY {self._order_by(table, order_by)}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._txn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._decode_row(r) for r in rows if r is not None]

    def update(self, table: str, key: str, values: Mapping[str, Any]) -> None:
        """Non-CAS update, refused on a CAS table: those rows are contended, and a write that skips the
        generation check is how a lost update happens."""
        if table in CAS_TABLES:
            raise ValueError(f"{table} has a generation column; use cas_update()")
        pk = self._pk(table)
        payload = self._encode_row(table, values)
        if not payload:
            return
        assignments = ", ".join(f"{name} = :{name}" for name in payload)
        with self._txn() as conn:
            conn.execute(f"UPDATE {table} SET {assignments} WHERE {pk} = :__key", {**payload, "__key": key})

    def delete(self, table: str, key: str) -> None:
        pk = self._pk(table)
        with self._txn() as conn:
            conn.execute(f"DELETE FROM {table} WHERE {pk} = ?", (key,))

    def cas_update(
        self, table: str, key: str, expected_generation: int, new_values: Mapping[str, Any]
    ) -> int:
        """Compare-and-set one row in one ``BEGIN IMMEDIATE``; returns the new generation.

        ``StaleGeneration`` (``details.expected``/``actual``; ``actual`` is null when the row is gone) means someone
        else moved the record. It is never retried here: replaying a human decision is the duplicate this store
        exists to prevent.
        """
        try:
            gen_col = CAS_TABLES[table]
        except KeyError:
            raise ValueError(f"{table} is not a CAS table; add it to CAS_TABLES") from None
        for reserved in (gen_col, "updated_at"):
            if reserved in new_values:
                raise ValueError(f"{reserved} is maintained by cas_update, not by the caller")
        pk = self._pk(table)
        payload = self._encode_row(table, new_values)
        with self._txn() as conn:
            row = conn.execute(f"SELECT {gen_col} FROM {table} WHERE {pk} = ?", (key,)).fetchone()
            actual = None if row is None else int(row[gen_col])
            if actual != int(expected_generation):
                raise StaleGeneration(
                    "the record changed under this operation",
                    details={"expected": int(expected_generation), "actual": actual, "table": table, "key": key},
                )
            payload[gen_col] = actual + 1
            payload["updated_at"] = self._clock.iso()
            assignments = ", ".join(f"{name} = :{name}" for name in payload)
            conn.execute(
                f"UPDATE {table} SET {assignments} WHERE {pk} = :__key AND {gen_col} = :{gen_col} - 1",
                {**payload, "__key": key},
            )
        return actual + 1

    # ---- activity ----

    def activity_append(self, row: ActivityRow) -> int:
        """Insert one row, returning its id; ``kind`` and ``message_key`` (default ``activity.<kind>``) are required."""
        message_key = row.message_key or (f"activity.{row.kind}" if row.kind else "")
        if not row.kind or not message_key:
            raise ValueError("an activity row needs kind and message_key")
        evidence = [ref.to_json() for ref in row.evidence]
        with self._txn() as conn:
            cur = conn.execute(
                "INSERT INTO activity(at, source, kind, severity, repo_id, intent_dir, space, stage, action_id, "
                "session_key, message_key, params_json, evidence_json, redacted, human_text) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row.at or self._clock.iso(), row.source or "studio", row.kind, row.severity or "info", row.repo_id,
                 row.intent_dir, row.space, row.stage, row.action_id, row.session_key, message_key,
                 _dumps(dict(row.params or {})), _dumps(evidence) if evidence else None, 1 if row.redacted else 0,
                 row.human_text),
            )
        return int(cur.lastrowid or 0)

    def activity_query(self, q: ActivityQuery) -> tuple[list[ActivityRow], str | None]:
        """One page of activity, newest first, plus the cursor for the next page.

        Paged by the autoincrement ``id``, not by ``at``: timestamps are second-precision, so rows routinely share
        one, and an ``at`` cursor would skip or repeat them.
        """
        limit = max(1, min(int(q.limit or C.ACTIVITY_DEFAULT_LIMIT), int(C.ACTIVITY_MAX_LIMIT)))
        conds: list[str] = []
        params: list[Any] = []
        if q.cursor:
            conds.append("id < ?")
            params.append(_decode_cursor(str(q.cursor)))
        for column in _ACTIVITY_FILTERS:
            value = getattr(q, column)
            if value is not None:
                conds.append(f"{column} = ?")
                params.append(value)
        if q.since:
            conds.append("at >= ?")
            params.append(q.since)
        if q.until:
            conds.append("at <= ?")
            params.append(q.until)
        if q.action_type:
            # The activity table stores no action type; the join keeps the filter honest instead of asking every
            # caller to resolve ids first.
            conds.append("action_id IN (SELECT action_id FROM actions WHERE type = ?)")
            params.append(q.action_type)
        where = f" WHERE {' AND '.join(conds)}" if conds else ""
        params.append(limit + 1)  # one extra row answers "is there a next page?"
        with self._txn() as conn:
            rows = conn.execute(f"SELECT * FROM activity{where} ORDER BY id DESC LIMIT ?", params).fetchall()
        page = [self._activity_row(r) for r in rows[:limit]]
        return page, (_encode_cursor(page[-1].id) if len(rows) > limit and page else None)

    def activity_purge_human_text(self, older_than_iso: str) -> int:
        """Clear the verbatim human text older than the cutoff, everywhere it is kept, in one transaction.

        A retention promise that covered only ``activity.human_text`` would keep the same sentence in
        ``actions.human_text`` forever. Actions are cut off by when the text was sent, not ``updated_at``, so a
        card that keeps being reconciled cannot extend its own retention.
        """
        with self._txn() as conn:
            cleared = conn.execute(
                "UPDATE activity SET human_text = NULL WHERE human_text IS NOT NULL AND at < ?",
                (older_than_iso,),
            ).rowcount
            cleared += conn.execute(
                "UPDATE actions SET human_text = NULL WHERE human_text IS NOT NULL "
                "AND COALESCE(delivering_at, created_at) < ?",
                (older_than_iso,),
            ).rowcount
        return int(cleared)

    def activity_delete_before(self, older_than_iso: str) -> int:
        """Delete Studio's activity rows older than the cutoff (``diagnostics.retention_days``)."""
        with self._txn() as conn:
            return int(conn.execute("DELETE FROM activity WHERE at < ?", (older_than_iso,)).rowcount)

    # ---- preferences ----

    def pref_get(self, key: str) -> Any | None:
        with self._txn() as conn:
            row = conn.execute("SELECT value_json FROM preferences WHERE key = ?", (key,)).fetchone()
        return None if row is None else json.loads(row["value_json"])

    def pref_set(self, key: str, value: Any) -> None:
        with self._txn() as conn:
            conn.execute(
                "INSERT INTO preferences(key, value_json, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, "
                "updated_at = excluded.updated_at",
                (key, _dumps(value), self._clock.iso()),
            )

    # ---- health ----

    def integrity_check(self) -> list[str]:
        """``PRAGMA quick_check`` lines, ``[]`` when sound. ``quick_check`` skips the index-content pass, which
        keeps it inside the startup budget on a large history while still catching a torn page."""
        with self._lock:
            conn = self._require_conn()
            lines = [str(r[0]) for r in conn.execute("PRAGMA quick_check").fetchall()]
        return [] if lines == ["ok"] else lines

    def pragmas(self) -> dict[str, Any]:
        """The live connection's durability settings, page accounting and ``schema_version``: ``synchronous`` and
        ``busy_timeout`` are per connection and invisible in the file, so ``/health`` and the tests ask here."""
        names = ("journal_mode", "synchronous", "busy_timeout", "foreign_keys", "page_size", "page_count",
                 "freelist_count")
        with self._lock:
            conn = self._require_conn()
            out: dict[str, Any] = {}
            for name in names:
                row = conn.execute(f"PRAGMA {name}").fetchone()
                out[name] = None if row is None else row[0]
            row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
            out["schema_version"] = int(row["value"]) if row else 0
        return out

    # ---- internals ----

    def _require_conn(self) -> sqlite3.Connection:
        conn = self._conn
        if conn is None:
            raise StudioError("storage_unavailable", "the Studio database is not open")
        return conn

    @contextmanager
    def _txn(self) -> Iterator[sqlite3.Connection]:
        """One ``BEGIN IMMEDIATE`` per public method; a nested call joins the outer transaction, so a caller can
        compose several writes into one (SQLite rejects a second ``BEGIN``)."""
        with self._lock:
            conn = self._require_conn()
            if self._depth:
                self._depth += 1
                try:
                    yield conn
                finally:
                    self._depth -= 1
                return
            conn.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield conn
            except BaseException:
                self._depth = 0
                conn.execute("ROLLBACK")
                raise
            else:
                self._depth = 0
                conn.execute("COMMIT")

    def _migrate(self) -> None:
        """Apply the ladder up to ``SCHEMA_VERSION`` in one transaction; a no-op when the file is already there."""
        with self._txn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
            current = int(row["value"]) if row else 0
            if current == SCHEMA_VERSION:
                return
            if current > SCHEMA_VERSION:
                raise StudioError("storage_error", "the Studio database was written by a newer version",
                                  details={"found": current, "supported": SCHEMA_VERSION})
            for target in range(current + 1, SCHEMA_VERSION + 1):
                for statement in _statements(MIGRATIONS[target]):
                    conn.execute(statement)
            now = self._clock.iso()
            conn.executemany("INSERT INTO schema_meta(key, value) VALUES(?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                             (("schema_version", str(SCHEMA_VERSION)), ("migrated_at", now)))
            conn.execute("INSERT OR IGNORE INTO schema_meta(key, value) VALUES('created_at', ?)", (now,))

    @staticmethod
    def _load_columns(conn: sqlite3.Connection) -> dict[str, tuple[str, ...]]:
        columns: dict[str, tuple[str, ...]] = {}
        for table in TABLE_KEYS:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            columns[table] = tuple(str(r["name"]) for r in rows)
        return columns

    def _table_columns(self, table: str) -> tuple[str, ...]:
        try:
            return self._columns[table]
        except KeyError:
            self._require_conn()
            raise ValueError(f"unknown table {table!r}") from None

    def _pk(self, table: str) -> str:
        self._table_columns(table)
        return TABLE_KEYS[table]

    def _column(self, table: str, name: str) -> str:
        if name not in self._table_columns(table):
            raise ValueError(f"{table} has no column {name!r}")
        return name

    def _encode_row(self, table: str, row: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, value in row.items():
            self._column(table, name)
            if name.endswith("_json") and value is not None and not isinstance(value, str):
                value = _dumps(value)
            out[name] = value
        return out

    def _decode_row(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        out: dict[str, Any] = {}
        for name in row.keys():
            value = row[name]
            if name.endswith("_json") and isinstance(value, str) and value:
                try:
                    value = json.loads(value)
                except ValueError:
                    pass  # only this module writes these; the raw text beats failing every read of the row
            out[name] = value
        return out

    def _where(self, table: str, where: Mapping[str, Any] | None) -> tuple[str, list[Any]]:
        if not where:
            self._table_columns(table)
            return "", []
        conds: list[str] = []
        params: list[Any] = []
        for name, value in where.items():
            self._column(table, name)
            if value is None:
                conds.append(f"{name} IS NULL")
            elif isinstance(value, (list, tuple, set, frozenset)):
                items = list(value)
                if not items:
                    conds.append("0 = 1")
                    continue
                conds.append(f"{name} IN ({', '.join('?' for _ in items)})")
                params.extend(items)
            else:
                conds.append(f"{name} = ?")
                params.append(value)
        return f" WHERE {' AND '.join(conds)}", params

    def _order_by(self, table: str, order_by: str) -> str:
        parts: list[str] = []
        for term in order_by.split(","):
            tokens = term.split()
            if not tokens or len(tokens) > 2:
                raise ValueError(f"unsupported order_by {order_by!r}")
            column = self._column(table, tokens[0])
            if len(tokens) == 2:
                direction = tokens[1].upper()
                if direction not in ("ASC", "DESC"):
                    raise ValueError(f"unsupported order_by {order_by!r}")
                parts.append(f"{column} {direction}")
            else:
                parts.append(column)
        return ", ".join(parts)

    @staticmethod
    def _activity_row(row: sqlite3.Row) -> ActivityRow:
        space, intent_dir = row["space"], row["intent_dir"]
        return ActivityRow(
            id=int(row["id"]),
            at=str(row["at"]),
            source=str(row["source"]),
            kind=str(row["kind"]),
            severity=str(row["severity"]),
            repo_id=row["repo_id"],
            space=space,
            intent_dir=intent_dir,
            # A row may name a repository and no intent; a missing space is the default one.
            intent_key=C.intent_key_for(space or C.DEFAULT_SPACE, intent_dir) if intent_dir else None,
            stage=row["stage"],
            action_id=row["action_id"],
            session_key=row["session_key"],
            message_key=str(row["message_key"]),
            params=json.loads(row["params_json"] or "{}"),
            evidence=_decode_evidence(row["evidence_json"]),
            redacted=bool(row["redacted"]),
            human_text=row["human_text"],
        )
