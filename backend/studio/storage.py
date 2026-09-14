"""Studio's own crash-safe store: one SQLite file, one connection, one transaction per call.

Why a database at all: the host's ``ctx.storage`` is a JSON key-value directory with an atomic
*replace* but no fsync, no locking and no compare-and-set (``01 §2.3``), so two interleaved
read-modify-write sequences silently lose one of them. Every durability promise in the PRD — a
decision recorded on disk *before* it is sent, a lease that cannot be held twice for one repository,
an action status only one writer may advance — needs a real CAS and a real fsync.

The shape of this module and the failure each choice prevents:

* **One connection guarded by an ``RLock``.** ``check_same_thread=False`` plus a re-entrant lock is
  simpler to reason about than a pool and makes "one transaction per public method" literally true:
  no second connection can interleave a half-finished write, so a reader inside a method sees exactly
  the rows that method is about to commit.
* **Explicit ``BEGIN IMMEDIATE``, never the driver's implicit transaction.** ``isolation_level=None``
  turns off sqlite3's guessing. A *deferred* transaction takes its write lock halfway through, so it
  can fail with ``SQLITE_BUSY`` *after* the method has already read the generation it is about to
  compare against; ``IMMEDIATE`` takes the write lock up front and that race disappears.
* **WAL + ``synchronous=FULL``.** The submit path must be able to promise "the ``Delivering`` record
  is on disk" before the browser is told to send the prompt. With ``synchronous=NORMAL`` a power loss
  can drop that record, and Studio would then replay a decision the model has already seen — the one
  duplicate the whole design exists to prevent.
* **``foreign_keys=ON``.** Removing a repository must not leave orphaned actions and bindings behind
  that a later scan would resurrect as live cards for a repo the user deleted.
* **Nothing here parses AI-DLC files, touches a repository, or judges lease eligibility.** Reclaim
  proof lives in ``leases.py``: a store that could declare a lease dead by looking at a clock would
  eventually reclaim a live one (FR-SES-007). ``lease_reclaim`` therefore takes the caller's proof as
  data and records it, and performs no time check of its own.

Threading: this module is **sync and blocking**. Callers reach it through
``await asyncio.to_thread(...)`` (§0.6); it never awaits, never spawns a thread and never holds the
lock across anything but its own SQLite calls.
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
from .errors import LeaseHeld, StaleGeneration, StudioError

if TYPE_CHECKING:  # the injectable protocols live in constants.py (§0.2); annotations only
    from .constants import Clock, IdFactory

#: Bumped only together with a new entry in ``MIGRATIONS``. ``open()`` is a no-op when the file
#: already carries this version, so a restart costs nothing.
SCHEMA_VERSION = 2

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

#: ``target_version -> DDL script``. The ladder is data so that ``open()`` is a plain loop and a
#: future version cannot be applied by accident: a file stamped with a version this build does not
#: know about is refused instead of being written to with the wrong shape.
#: ``boot_id`` on an admin lease, so a lease left behind by a dead process can be proven dead.
#:
#: Before this column the only proof ``leases._prove_admin`` could offer was "the install transaction
#: that took this lease has settled" — which no non-transactional admin operation ever satisfies, and
#: which nothing settles for a transaction whose process was killed. The result was a repository that
#: could never run another turn and could never be repaired. A boot id is a *positive* proof of owner
#: death (the process that took the lease is gone), never an age proof, so FR-SES-007 still holds.
#:
#: ``SCHEMA_V1`` is left exactly as it shipped: it is the historical record of version 1, and editing it
#: would make a fresh database disagree with a migrated one about what version 1 ever was.
SCHEMA_V2 = """\
ALTER TABLE admin_leases ADD COLUMN boot_id TEXT;
"""

MIGRATIONS: dict[int, str] = {1: SCHEMA_V1, 2: SCHEMA_V2}

#: ``table -> (generation column, timestamp column)``. Per table because the lease tables have no
#: ``updated_at`` — writing one would fail the whole CAS (review R06).
CAS_TABLES: dict[str, tuple[str, str]] = {
    "actions": ("status_generation", "updated_at"),
    "intent_bindings": ("binding_generation", "updated_at"),
    "execution_leases": ("generation", "heartbeat_at"),
    "admin_leases": ("generation", "heartbeat_at"),
}

#: ``table -> primary key column``. The generic helpers refuse a table that is not listed, which is
#: also the guard that keeps a caller-supplied name out of the SQL string.
TABLE_KEYS: dict[str, str] = {
    "schema_meta": "key",
    "repos": "id",
    "intent_bindings": "binding_key",
    "actions": "action_id",
    "action_transitions": "id",
    "execution_leases": "resolved_repo_identity",
    "admin_leases": "resolved_repo_identity",
    "lease_generations": "resolved_repo_identity",
    "install_transactions": "transaction_id",
    "install_receipts": "receipt_id",
    "preferences": "key",
    "activity": "id",
    "events": "seq",
    "calibration": "id",
    "breakers": "key",
    "advisor_drafts": "draft_id",
    "migrations": "id",
}

#: Columns each lease kind owns, beyond the shared ``repo_id``/timestamps/generation. Validated on
#: acquire so a caller cannot smuggle an execution field into an admin lease (or vice versa) and end
#: up with a row the API cannot render.
LEASE_FIELDS: dict[str, tuple[str, ...]] = {
    "execution": ("intent_uuid", "session_key", "action_id", "observed_busy_state"),
    "admin": ("operation_type", "transaction_id", "boot_id"),
}
_LEASE_TABLE = {"execution": "execution_leases", "admin": "admin_leases"}

#: Activity kind and severity for a reclaim. Written by ``lease_reclaim`` itself so the audit trail
#: cannot be forgotten by a caller that took a lease away from a presumed-dead owner.
_RECLAIM_KIND = "lease.reclaimed"
_RECLAIM_SEVERITY = "attention"

_ORDER_BY_TOKEN = "ASC", "DESC"


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LeaseRecord:
    """One row of ``execution_leases`` or ``admin_leases``, both kinds in one shape.

    ``repo_id`` is denormalised into the lease row at acquire time: ``GET /leases`` must render an
    orphaned lease whose repository row may already be gone, and a join that can return no row would
    hide exactly the lease the operator needs to see.
    """

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
    #: The ``Services.boot_id`` of the process that took an admin lease. ``None`` on an execution lease
    #: (its liveness is proven from the action row and the host's own session state) and on an admin
    #: lease written before schema 2.
    boot_id: str | None = None
    repo_id: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "resolved_repo_identity": self.resolved_repo_identity,
            "generation": self.generation,
            "acquired_at": self.acquired_at,
            "heartbeat_at": self.heartbeat_at,
            "intent_uuid": self.intent_uuid,
            "session_key": self.session_key,
            "action_id": self.action_id,
            "observed_busy_state": self.observed_busy_state,
            "operation_type": self.operation_type,
            "transaction_id": self.transaction_id,
            "boot_id": self.boot_id,
            "repo_id": self.repo_id,
        }

    @classmethod
    def from_row(cls, kind: str, row: Mapping[str, Any]) -> "LeaseRecord":
        return cls(
            kind=kind,
            resolved_repo_identity=str(row["resolved_repo_identity"]),
            generation=int(row["generation"]),
            acquired_at=str(row["acquired_at"]),
            heartbeat_at=str(row["heartbeat_at"]),
            intent_uuid=row.get("intent_uuid"),
            session_key=row.get("session_key"),
            action_id=row.get("action_id"),
            observed_busy_state=row.get("observed_busy_state"),
            operation_type=row.get("operation_type"),
            transaction_id=row.get("transaction_id"),
            boot_id=row.get("boot_id"),
            repo_id=str(row.get("repo_id") or ""),
        )


@dataclass(frozen=True, slots=True)
class EventRecord:
    """One SSE frame as it was persisted; ``seq`` is the cursor clients replay from."""

    seq: int
    at: str
    type: str
    payload: dict = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"seq": self.seq, "at": self.at, "type": self.type, "payload": self.payload}


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """A pointer to the thing that justifies an activity row — never the thing itself.

    Defined here rather than in ``activity.py`` because ``Storage`` must construct these when it
    reads a row back, and the store may not import the layer above it. ``activity.py`` re-exports the
    three activity dataclasses so §1.17's names resolve.
    """

    kind: str
    ref: str
    detail: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref": self.ref, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class ActivityRow:
    """One Studio-originated activity row.

    Every field has a default so a caller can name only what it knows; the NOT NULL columns are
    validated in ``activity_append`` instead, which keeps the failure at the write (with the row in
    the message) rather than as a bare sqlite constraint error.

    ``message_key`` + ``params`` and never English prose: the UI renders activity in the user's
    locale, so a stored sentence would be untranslatable forever.
    """

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

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "at": self.at,
            "source": self.source,
            "kind": self.kind,
            "severity": self.severity,
            "repo_id": self.repo_id,
            "space": self.space,
            "intent_dir": self.intent_dir,
            "intent_key": self.intent_key,
            "stage": self.stage,
            "action_id": self.action_id,
            "session_key": self.session_key,
            "message_key": self.message_key,
            "params": self.params,
            "evidence": [e.to_json() for e in self.evidence],
            "redacted": self.redacted,
            "human_text": self.human_text,
        }


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

    def to_json(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "space": self.space,
            "intent_dir": self.intent_dir,
            "source": self.source,
            "stage": self.stage,
            "kind": self.kind,
            "action_type": self.action_type,
            "severity": self.severity,
            "since": self.since,
            "until": self.until,
            "cursor": self.cursor,
            "limit": self.limit,
        }


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _statements(script: str) -> Iterator[str]:
    """Split a DDL script into statements.

    ``executescript`` commits the pending transaction before it runs, which would silently break the
    one-transaction rule for migrations, and a naive ``split(";")`` mangles this schema because two
    column comments contain a semicolon. ``sqlite3.complete_statement`` is the tokenizer's own answer.
    """
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


def _field(row: Any, name: str, default: Any = None) -> Any:
    """Read one field from an ActivityRow-shaped object or a plain mapping.

    Duck-typed on purpose: ``activity.py`` may hand over its own equivalent dataclass, and Storage
    should persist it rather than insist on an identity comparison of two structurally equal types.
    """
    if isinstance(row, Mapping):
        return row.get(name, default)
    value = getattr(row, name, default)
    return default if value is None and default is not None else value


def _intent_key(space: str | None, intent_dir: str | None) -> str | None:
    """``constants.intent_key_for`` with the null guard rows need: an activity row may name a repository
    and no intent at all, and a missing space means the default one."""
    if not intent_dir:
        return None
    return C.intent_key_for(space or C.DEFAULT_SPACE, intent_dir)


def _decode_evidence(raw: Any) -> tuple[EvidenceRef, ...]:
    if not raw:
        return ()
    data = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(data, Mapping):
        data = [data]
    out: list[EvidenceRef] = []
    for item in data or ():
        if isinstance(item, Mapping):
            out.append(
                EvidenceRef(
                    kind=str(item.get("kind", "studio")),
                    ref=str(item.get("ref", "")),
                    detail=item.get("detail"),
                )
            )
    return tuple(out)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #


class Storage:
    """The single writer for everything Studio owns. Sync; call through ``asyncio.to_thread``."""

    def __init__(self, path: Path, *, clock: "Clock", ids: "IdFactory") -> None:
        self._path = Path(path)
        self._clock = clock
        #: Held for symmetry with every other service (and so a future migration can mint ids
        #: without a signature change); Storage itself never invents an id — every key it stores was
        #: chosen by the module that owns that record.
        self._ids = ids
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._depth = 0
        self._columns: dict[str, tuple[str, ...]] = {}

    # ---- lifecycle ----

    @property
    def path(self) -> Path:
        return self._path

    def open(self) -> None:
        """Create the file, apply the PRAGMAs, run migrations up to ``SCHEMA_VERSION``.

        Idempotent: ``Services.start`` and a reconciler restart may both call it.
        """
        with self._lock:
            if self._conn is not None:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self._path),
                check_same_thread=False,
                isolation_level=None,
                timeout=C.SQLITE_BUSY_TIMEOUT_MS / 1000.0,
            )
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
        """Close the connection. Idempotent, and safe to call from ``Services.stop``."""
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
        """Insert one row. ``*_json`` values are JSON-encoded; a ``str`` is taken as already encoded.

        A unique-index violation is deliberately **not** translated into a ``StudioError``: the
        ``actions_live_dedupe`` collision is information the caller acts on (a live card for that
        boundary already exists), not a 500.
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
        """One row by primary key, ``*_json`` decoded back into Python objects.

        The comparison takes the key column's affinity, so the integer-keyed tables (``events``,
        ``activity``) accept the id as either ``int`` or ``str``.
        """
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
        """Rows matching an equality filter.

        A list/tuple/set value becomes an ``IN`` clause (so callers can ask for the live action
        statuses without writing SQL of their own) and ``None`` becomes ``IS NULL``. Table, column
        and ``order_by`` names are validated against the real schema, so nothing a caller supplies is
        ever interpolated unchecked.
        """
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
        """Non-CAS update. Refused on a table that has a generation column.

        Those rows are contended by definition (two browser tabs, the reconciler, a lease holder);
        letting one caller write them without comparing a generation is how a lost update happens.
        """
        if table in CAS_TABLES:
            raise ValueError(f"{table} has a generation column; use cas_update()")
        pk = self._pk(table)
        payload = self._encode_row(table, values)
        if not payload:
            return
        assignments = ", ".join(f"{name} = :{name}" for name in payload)
        params = dict(payload)
        params["__key"] = key
        with self._txn() as conn:
            conn.execute(f"UPDATE {table} SET {assignments} WHERE {pk} = :__key", params)

    def delete(self, table: str, key: str) -> None:
        pk = self._pk(table)
        with self._txn() as conn:
            conn.execute(f"DELETE FROM {table} WHERE {pk} = ?", (key,))

    def cas_update(
        self, table: str, key: str, expected_generation: int, new_values: Mapping[str, Any]
    ) -> int:
        """Compare-and-set one row; returns the new generation.

        One ``BEGIN IMMEDIATE`` for the read of the generation and the write, so no other writer can
        slip between them. ``StaleGeneration`` (``details.expected``/``details.actual``, ``actual``
        null when the row is gone) is the caller's signal that somebody else already moved this
        record — it is never retried automatically, because replaying a human decision is the exact
        duplicate this store exists to prevent.
        """
        with self._txn() as conn:
            return self._cas(conn, table, key, expected_generation, new_values)

    def deliver_under_lease(
        self,
        action_id: str,
        expected_generation: int,
        *,
        identity: str,
        lease_generation: int,
        new_values: Mapping[str, Any],
    ) -> int:
        """Move an action to ``Delivering`` only while the caller still owns the execution lease.

        The cursor switch is a subprocess, so acquiring the lease and committing ``Delivering``
        cannot be one transaction (C31). This method closes the remaining window: the lease check,
        the action CAS and the lease's ``action_id`` update are one ``BEGIN IMMEDIATE``, so after
        commit either the lease still names this caller *and* the durable ``Delivering`` record
        exists, or nothing changed at all (review P15).
        """
        status = new_values.get("status", "Delivering")
        if status != "Delivering":
            raise ValueError("deliver_under_lease only writes status 'Delivering'")
        values = {k: v for k, v in new_values.items() if k != "status"}
        values["status"] = "Delivering"
        with self._txn() as conn:
            row = conn.execute(
                "SELECT * FROM execution_leases WHERE resolved_repo_identity = ?", (identity,)
            ).fetchone()
            if row is None or int(row["generation"]) != int(lease_generation):
                raise StudioError(
                    "lease_lost",
                    "the execution lease is no longer held by this caller",
                    details={
                        "resolved_repo_identity": identity,
                        "expected_generation": int(lease_generation),
                        "actual_generation": None if row is None else int(row["generation"]),
                    },
                )
            generation = self._cas(conn, "actions", action_id, expected_generation, values)
            conn.execute(
                "UPDATE execution_leases SET action_id = ? WHERE resolved_repo_identity = ?",
                (action_id, identity),
            )
            return generation

    # ---- leases ----

    def lease_get(self, kind: str, identity: str) -> LeaseRecord | None:
        table = self._lease_table(kind)
        with self._txn() as conn:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE resolved_repo_identity = ?", (identity,)
            ).fetchone()
        return None if row is None else LeaseRecord.from_row(kind, dict(row))

    def lease_list(self) -> list[LeaseRecord]:
        """Every live lease of both kinds, oldest first (the order ``GET /leases`` renders)."""
        out: list[LeaseRecord] = []
        with self._txn() as conn:
            for kind, table in _LEASE_TABLE.items():
                for row in conn.execute(
                    f"SELECT * FROM {table} ORDER BY acquired_at, resolved_repo_identity"
                ).fetchall():
                    out.append(LeaseRecord.from_row(kind, dict(row)))
        out.sort(key=lambda r: (r.acquired_at, r.resolved_repo_identity, r.kind))
        return out

    def lease_acquire(self, kind: str, identity: str, fields: Mapping[str, Any]) -> LeaseRecord:
        """Take the lease for one resolved identity, or raise ``LeaseHeld``.

        Exclusivity is across **both** kinds: an install must not run while a turn is in flight, and
        a turn must not start while the framework files are being replaced underneath it.

        The generation comes from ``lease_generations``, which outlives the lease row. A released and
        re-acquired lease therefore never reuses a number, so a stale holder that wakes up after a
        reclaim fails its next heartbeat instead of writing to a lease it no longer owns.
        """
        table = self._lease_table(kind)
        allowed = set(LEASE_FIELDS[kind]) | {"repo_id"}
        unknown = sorted(set(fields) - allowed)
        if unknown:
            raise ValueError(f"{unknown} are not fields of a {kind} lease")
        repo_id = str(fields.get("repo_id") or "")
        if not repo_id:
            raise ValueError("a lease needs repo_id (denormalised for the API)")
        if kind == "admin" and not fields.get("operation_type"):
            raise ValueError("an admin lease needs operation_type")
        now = self._clock.iso()
        with self._txn() as conn:
            for other_kind, other_table in _LEASE_TABLE.items():
                row = conn.execute(
                    f"SELECT * FROM {other_table} WHERE resolved_repo_identity = ?", (identity,)
                ).fetchone()
                if row is not None:
                    owner = LeaseRecord.from_row(other_kind, dict(row))
                    raise LeaseHeld(
                        f"{other_kind} lease held for this repository",
                        details={
                            "kind": other_kind,
                            "owner": owner.to_json(),
                            "retry_after_secs": int(C.LEASE_HEARTBEAT_SECS),
                        },
                    )
            seen = conn.execute(
                "SELECT last_generation FROM lease_generations WHERE resolved_repo_identity = ?",
                (identity,),
            ).fetchone()
            generation = (int(seen["last_generation"]) if seen else 0) + 1
            conn.execute(
                "INSERT INTO lease_generations(resolved_repo_identity, last_generation) VALUES(?, ?) "
                "ON CONFLICT(resolved_repo_identity) DO UPDATE SET last_generation = excluded.last_generation",
                (identity, generation),
            )
            row_values: dict[str, Any] = {
                "resolved_repo_identity": identity,
                "repo_id": repo_id,
                "acquired_at": now,
                "heartbeat_at": now,
                "generation": generation,
            }
            for name in LEASE_FIELDS[kind]:
                row_values[name] = fields.get(name)
            names = ", ".join(row_values)
            placeholders = ", ".join(f":{n}" for n in row_values)
            conn.execute(f"INSERT INTO {table}({names}) VALUES({placeholders})", row_values)
            return LeaseRecord.from_row(kind, row_values)

    def lease_heartbeat(
        self, kind: str, identity: str, generation: int, observed_busy_state: str | None = None
    ) -> LeaseRecord:
        """Refresh ``heartbeat_at`` **without** moving the generation.

        The holder keeps using the generation it was granted for its next CAS, so a heartbeat that
        bumped it would make the owner stale against itself.
        """
        table = self._lease_table(kind)
        now = self._clock.iso()
        with self._txn() as conn:
            row = conn.execute(
                f"SELECT * FROM {table} WHERE resolved_repo_identity = ?", (identity,)
            ).fetchone()
            actual = None if row is None else int(row["generation"])
            if actual != int(generation):
                raise StaleGeneration(
                    "the lease moved under this holder",
                    details={"expected": int(generation), "actual": actual, "kind": kind},
                )
            values = dict(row)
            values["heartbeat_at"] = now
            if kind == "execution":
                values["observed_busy_state"] = observed_busy_state
                conn.execute(
                    f"UPDATE {table} SET heartbeat_at = ?, observed_busy_state = ? "
                    "WHERE resolved_repo_identity = ? AND generation = ?",
                    (now, observed_busy_state, identity, int(generation)),
                )
            else:
                conn.execute(
                    f"UPDATE {table} SET heartbeat_at = ? "
                    "WHERE resolved_repo_identity = ? AND generation = ?",
                    (now, identity, int(generation)),
                )
            return LeaseRecord.from_row(kind, values)

    def lease_release(self, kind: str, identity: str, generation: int) -> None:
        """Give the lease back. ``StaleGeneration`` when the caller no longer owns it.

        Refusing a release whose generation moved matters: otherwise a holder that was already
        reclaimed would delete the *new* owner's lease on its way out.
        """
        table = self._lease_table(kind)
        with self._txn() as conn:
            self._lease_expect(conn, table, kind, identity, generation)
            conn.execute(f"DELETE FROM {table} WHERE resolved_repo_identity = ?", (identity,))

    def lease_reclaim(
        self,
        kind: str,
        identity: str,
        generation: int,
        *,
        reason: str,
        evidence: Mapping[str, Any],
        expect_action: tuple[str | None, frozenset[str]] | None = None,
    ) -> None:
        """Take a lease away from an owner the **caller** has proven is not executing.

        Storage performs no time check: elapsed heartbeat age never authorises a reclaim (FR-SES-007),
        and a store that decided otherwise would eventually cut a live turn loose. The proof the
        caller assembled is recorded with the activity row in the same transaction as the delete, so
        a reclaim can never be invisible after the fact.

        ``expect_action`` re-asserts the caller's proof here, inside the delete's own transaction:
        ``(action_id, statuses)`` demands that action still hold one of ``statuses`` (an empty set
        demands the row still be absent), and ``StaleGeneration`` otherwise. The lease generation alone
        is not enough of a guard, because ``deliver_under_lease`` moves an action ``Queued →
        Delivering`` **without** touching it — so between a caller's proof and this delete a submit can
        put a decision on the wire, and a generation-only check would cut that lease loose and let a
        second submit dispatch a second turn into the same workflow.
        """
        table = self._lease_table(kind)
        with self._txn() as conn:
            row = self._lease_expect(conn, table, kind, identity, generation)
            if expect_action is not None:
                self._action_expect(conn, kind, *expect_action)
            lease = LeaseRecord.from_row(kind, dict(row))
            conn.execute(f"DELETE FROM {table} WHERE resolved_repo_identity = ?", (identity,))
            self._activity_insert(
                conn,
                ActivityRow(
                    at=self._clock.iso(),
                    source="studio",
                    kind=_RECLAIM_KIND,
                    severity=_RECLAIM_SEVERITY,
                    repo_id=lease.repo_id or None,
                    action_id=lease.action_id,
                    session_key=lease.session_key,
                    message_key=f"activity.{_RECLAIM_KIND}",
                    params={
                        "lease_kind": kind,
                        "reason": reason,
                        "generation": int(generation),
                        # The proof lives in params, not evidence_json: it is a dict of codes and
                        # booleans, while evidence_json carries EvidenceRef pointers only.
                        "evidence": dict(evidence),
                    },
                ),
            )

    # ---- events ring ----

    def event_append(self, type: str, payload: Mapping[str, Any]) -> int:
        """Append one event and trim the ring; returns the new ``seq`` (the SSE id)."""
        with self._txn() as conn:
            cur = conn.execute(
                "INSERT INTO events(at, type, payload_json) VALUES(?, ?, ?)",
                (self._clock.iso(), type, _dumps(dict(payload))),
            )
            seq = int(cur.lastrowid or 0)
            conn.execute("DELETE FROM events WHERE seq <= ?", (seq - int(C.EVENTS_RING_SIZE),))
            return seq

    def event_since(self, cursor: int, limit: int = 500) -> list[EventRecord]:
        """Events after ``cursor``, oldest first.

        A cursor older than ``event_bounds()[0] - 1`` has lost frames to the ring; the caller
        compares the two and emits ``reset`` rather than silently replaying an incomplete history.
        """
        count = max(1, min(int(limit), int(C.EVENTS_RING_SIZE)))
        with self._txn() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?", (int(cursor), count)
            ).fetchall()
        return [
            EventRecord(
                seq=int(r["seq"]),
                at=str(r["at"]),
                type=str(r["type"]),
                payload=json.loads(r["payload_json"] or "{}"),
            )
            for r in rows
        ]

    def event_bounds(self) -> tuple[int, int]:
        with self._txn() as conn:
            row = conn.execute("SELECT MIN(seq) AS lo, MAX(seq) AS hi FROM events").fetchone()
        if row is None or row["lo"] is None:
            return (0, 0)
        return (int(row["lo"]), int(row["hi"]))

    # ---- activity ----

    def activity_append(self, row: ActivityRow) -> int:
        with self._txn() as conn:
            return self._activity_insert(conn, row)

    def activity_query(self, q: ActivityQuery) -> tuple[list[ActivityRow], str | None]:
        """One page of activity, newest first, plus the cursor for the next page.

        Paging is by the autoincrement ``id``, not by ``at``: timestamps are second-precision, so
        several rows routinely share one, and an ``at``-keyed cursor would either skip or repeat them.
        """
        limit = max(1, min(int(_field(q, "limit", C.ACTIVITY_DEFAULT_LIMIT) or C.ACTIVITY_DEFAULT_LIMIT),
                           int(C.ACTIVITY_MAX_LIMIT)))
        conds: list[str] = []
        params: list[Any] = []
        cursor = _field(q, "cursor")
        if cursor:
            conds.append("id < ?")
            params.append(_decode_cursor(str(cursor)))
        for column, value in (
            ("repo_id", _field(q, "repo_id")),
            ("space", _field(q, "space")),
            ("intent_dir", _field(q, "intent_dir")),
            ("source", _field(q, "source")),
            ("stage", _field(q, "stage")),
            ("kind", _field(q, "kind")),
            ("severity", _field(q, "severity")),
        ):
            if value is not None:
                conds.append(f"{column} = ?")
                params.append(value)
        since, until = _field(q, "since"), _field(q, "until")
        if since:
            conds.append("at >= ?")
            params.append(since)
        if until:
            conds.append("at <= ?")
            params.append(until)
        action_type = _field(q, "action_type")
        if action_type:
            # The activity table stores no action type; the join keeps the filter honest instead of
            # asking every caller to resolve ids first.
            conds.append("action_id IN (SELECT action_id FROM actions WHERE type = ?)")
            params.append(action_type)
        where = f" WHERE {' AND '.join(conds)}" if conds else ""
        params.append(limit + 1)  # one extra row answers "is there a next page?"
        with self._txn() as conn:
            rows = conn.execute(
                f"SELECT * FROM activity{where} ORDER BY id DESC LIMIT ?", params
            ).fetchall()
        page = [self._activity_row(r) for r in rows[:limit]]
        next_cursor = _encode_cursor(page[-1].id) if len(rows) > limit and page else None
        return page, next_cursor

    def activity_purge_human_text(self, older_than_iso: str) -> int:
        """Drop the verbatim human text everywhere it is retained, older than the cutoff.

        Both tables that hold it (``activity.human_text`` for the submitted decision and
        ``actions.human_text`` for the record that was sent) are cleared in one transaction: a
        retention promise that only covered one of them would keep the same sentence on disk forever.
        Actions are cut off by when the text was actually sent, not by ``updated_at``, so a card that
        keeps being reconciled cannot extend its own retention.
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

    def pref_all(self) -> dict[str, Any]:
        with self._txn() as conn:
            rows = conn.execute("SELECT key, value_json FROM preferences ORDER BY key").fetchall()
        return {str(r["key"]): json.loads(r["value_json"]) for r in rows}

    # ---- housekeeping ----

    def vacuum_if_needed(self) -> None:
        """``VACUUM`` only when the free list is worth reclaiming.

        Deliberately outside ``_txn``: SQLite refuses ``VACUUM`` inside a transaction. It rewrites the
        whole file, so it runs on a threshold rather than on a schedule — an unconditional vacuum on
        every start would make the gateway's startup budget depend on the size of the history.
        """
        with self._lock:
            conn = self._require_conn()
            if self._depth:  # pragma: no cover - defensive; no public method nests this
                raise StudioError("storage_error", "vacuum cannot run inside a transaction")
            free = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
            pages = int(conn.execute("PRAGMA page_count").fetchone()[0]) or 1
            if free < int(C.VACUUM_MIN_FREELIST_PAGES):
                return
            if free < pages * float(C.VACUUM_FREELIST_RATIO):
                return
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def integrity_check(self) -> list[str]:
        """``PRAGMA quick_check`` lines; ``[]`` when the file is sound.

        ``quick_check`` rather than ``integrity_check``: it skips the index-content pass, which keeps
        it inside the startup budget on a large history while still catching a torn page.
        """
        with self._lock:
            conn = self._require_conn()
            lines = [str(r[0]) for r in conn.execute("PRAGMA quick_check").fetchall()]
        return [] if lines == ["ok"] else lines

    def pragmas(self) -> dict[str, Any]:
        """The durability settings and page accounting of the live connection.

        Exposed because ``GET /health`` reports ``storage.wal`` and ``schema_version``, and because a
        test must be able to prove the PRAGMAs are actually in force on the connection Studio uses
        (``synchronous`` and ``busy_timeout`` are per-connection and invisible in the file).
        """
        names = (
            "journal_mode",
            "synchronous",
            "busy_timeout",
            "foreign_keys",
            "page_size",
            "page_count",
            "freelist_count",
        )
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
        """One ``BEGIN IMMEDIATE`` per public method; a nested call joins the outer transaction.

        The nesting rule is what lets ``deliver_under_lease`` and ``lease_reclaim`` compose smaller
        steps (a CAS, an activity insert) without either opening a second transaction — which SQLite
        rejects — or duplicating their SQL.
        """
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
        """Apply the ladder up to ``SCHEMA_VERSION``; a no-op when the file is already there."""
        with self._txn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
            current = int(row["value"]) if row else 0
            if current == SCHEMA_VERSION:
                return
            if current > SCHEMA_VERSION:
                raise StudioError(
                    "storage_error",
                    "the Studio database was written by a newer version",
                    details={"found": current, "supported": SCHEMA_VERSION},
                )
            for target in range(current + 1, SCHEMA_VERSION + 1):
                for statement in _statements(MIGRATIONS[target]):
                    conn.execute(statement)
            now = self._clock.iso()
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('created_at', ?) "
                "ON CONFLICT(key) DO NOTHING",
                (now,),
            )
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('migrated_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (now,),
            )

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

    def _lease_table(self, kind: str) -> str:
        if kind not in _LEASE_TABLE:
            raise ValueError(f"unknown lease kind {kind!r}")
        return _LEASE_TABLE[kind]

    def _lease_expect(
        self, conn: sqlite3.Connection, table: str, kind: str, identity: str, generation: int
    ) -> sqlite3.Row:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE resolved_repo_identity = ?", (identity,)
        ).fetchone()
        actual = None if row is None else int(row["generation"])
        if actual != int(generation):
            raise StaleGeneration(
                "the lease moved under this holder",
                details={"expected": int(generation), "actual": actual, "kind": kind},
            )
        return row

    def _action_expect(
        self, conn: sqlite3.Connection, kind: str, action_id: str | None, statuses: frozenset[str]
    ) -> None:
        """The other half of a reclaim's guard: the proven action must not have moved (see lease_reclaim)."""
        if action_id is None:
            return
        row = conn.execute("SELECT status FROM actions WHERE action_id = ?", (action_id,)).fetchone()
        actual = None if row is None else str(row["status"] or "")
        ok = (actual is None) if not statuses else (actual in statuses)
        if not ok:
            raise StaleGeneration(
                "the action moved between the reclaim proof and the delete",
                details={
                    "kind": kind,
                    "action_id": action_id,
                    "actual": actual,
                    "expected": sorted(statuses),
                },
            )

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
                    # A blob only this module writes cannot normally be corrupt; surfacing the raw
                    # text beats failing every read of the row it lives in.
                    pass
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
                if direction not in _ORDER_BY_TOKEN:
                    raise ValueError(f"unsupported order_by {order_by!r}")
                parts.append(f"{column} {direction}")
            else:
                parts.append(column)
        return ", ".join(parts)

    def _cas(
        self,
        conn: sqlite3.Connection,
        table: str,
        key: str,
        expected_generation: int,
        new_values: Mapping[str, Any],
    ) -> int:
        try:
            gen_col, ts_col = CAS_TABLES[table]
        except KeyError:
            raise ValueError(f"{table} is not a CAS table; add it to CAS_TABLES") from None
        for reserved in (gen_col, ts_col):
            if reserved in new_values:
                raise ValueError(f"{reserved} is maintained by cas_update, not by the caller")
        pk = self._pk(table)
        row = conn.execute(f"SELECT {gen_col} FROM {table} WHERE {pk} = ?", (key,)).fetchone()
        actual = None if row is None else int(row[gen_col])
        if actual != int(expected_generation):
            raise StaleGeneration(
                "the record changed under this operation",
                details={"expected": int(expected_generation), "actual": actual,
                         "table": table, "key": key},
            )
        generation = actual + 1
        payload = self._encode_row(table, new_values)
        payload[gen_col] = generation
        payload[ts_col] = self._clock.iso()
        assignments = ", ".join(f"{name} = :{name}" for name in payload)
        params = dict(payload)
        params["__key"] = key
        conn.execute(
            f"UPDATE {table} SET {assignments} WHERE {pk} = :__key AND {gen_col} = :{gen_col} - 1",
            params,
        )
        return generation

    def _activity_insert(self, conn: sqlite3.Connection, row: Any) -> int:
        at = _field(row, "at") or self._clock.iso()
        kind = str(_field(row, "kind") or "")
        source = str(_field(row, "source") or "studio")
        severity = str(_field(row, "severity") or "info")
        message_key = str(_field(row, "message_key") or (f"activity.{kind}" if kind else ""))
        if not kind or not message_key:
            raise ValueError("an activity row needs kind and message_key")
        evidence = _field(row, "evidence") or ()
        if not isinstance(evidence, (list, tuple)):
            evidence = (evidence,)
        refs = [e.to_json() if hasattr(e, "to_json") else dict(e) for e in evidence]
        params = _field(row, "params") or {}
        cur = conn.execute(
            "INSERT INTO activity(at, source, kind, severity, repo_id, intent_dir, space, stage, "
            "action_id, session_key, message_key, params_json, evidence_json, redacted, human_text) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(at),
                source,
                kind,
                severity,
                _field(row, "repo_id"),
                _field(row, "intent_dir"),
                _field(row, "space"),
                _field(row, "stage"),
                _field(row, "action_id"),
                _field(row, "session_key"),
                message_key,
                _dumps(dict(params)),
                _dumps(refs) if refs else None,
                1 if _field(row, "redacted") else 0,
                _field(row, "human_text"),
            ),
        )
        return int(cur.lastrowid or 0)

    @staticmethod
    def _activity_row(row: sqlite3.Row) -> ActivityRow:
        space = row["space"]
        intent_dir = row["intent_dir"]
        return ActivityRow(
            id=int(row["id"]),
            at=str(row["at"]),
            source=str(row["source"]),
            kind=str(row["kind"]),
            severity=str(row["severity"]),
            repo_id=row["repo_id"],
            space=space,
            intent_dir=intent_dir,
            intent_key=_intent_key(space, intent_dir),
            stage=row["stage"],
            action_id=row["action_id"],
            session_key=row["session_key"],
            message_key=str(row["message_key"]),
            params=json.loads(row["params_json"] or "{}"),
            evidence=_decode_evidence(row["evidence_json"]),
            redacted=bool(row["redacted"]),
            human_text=row["human_text"],
        )
