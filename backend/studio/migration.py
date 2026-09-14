"""The one-time `aidlc-console` → `aidlc-studio` migration (§1.22, FR-MIG-001…005).

The prototype's whole persisted state is one file: `~/.kiro/crew/apps/aidlc-console/data/kv/repos.json`,
a list of `{id, path, label, added_at}` rows whose `id` is `sha256(str(Path.resolve()))[:12]` (07 §3.1/§3.2).
Everything else the user typed into the prototype — archive metadata, preferences, history — does not exist
on disk, so "move the Studio-owned data" means exactly: move that registry, and nothing else.

Five decisions, each for the failure it prevents:

1. **Re-resolve identity; never re-hash the string.** The prototype keyed a repository on the *text* of
   the path it was given, so the same directory registers twice under two ids on a case-insensitive
   filesystem (`/…/devdelta` and `/…/DevDelta` hash differently but are one inode — 07 §3.2). This
   module recomputes the §1.5 identity from each old path and merges the rows that collapse onto one,
   reporting the collision as `duplicate_of:<legacy_id>` instead of quietly registering a directory
   twice — two registrations of one repository would each take an execution lease and could run two
   AI-DLC turns in the same working tree.
2. **A path that is gone is kept, never dropped.** A user whose repository moved must be able to rebind
   it (FR-REP-009), and the only record that it ever existed is the row being migrated. Such a row is
   inserted with the availability `RepoRegistry.check_availability` reports (`unavailable`,
   `permission_denied`) and no `resolved_identity`, which is what keeps it visible, rebindable, and
   ineligible for any lease.
3. **The backup exists before the first write.** The raw source bytes are copied into
   `ctx.data_dir/<MIGRATION_BACKUP_DIRNAME>/` *before* the transaction opens, so the rollback copy can
   never be the thing that is missing when the migration is the thing that failed. The digest the
   preview published is taken from those same bytes, so what was validated is what was archived.
4. **Counts are validated before the migration is declared applied.** `rows_out` must equal the number
   of distinct identities planned, and the number of `repos` rows that actually appeared must equal
   `rows_out`. A mismatch rolls the batch back and records `rolled_back` — a half-migrated registry
   would leave the user rebinding phantom repositories with no way to tell which half is real.
5. **Applying twice is refused, and a crash mid-apply is recoverable.** The `migrations` row is the
   durable marker (`migration_already_applied`); it is written inside the same transaction as the repo
   rows. If the process dies before it lands, a retry sees the already-inserted repositories by
   identity, path or `legacy_console_id` and reports them as `already_registered:<repo_id>` rather than
   inserting them again.

What this module must never do (§1.22): copy `.app_secret` or any other credential, read anything under a
registered repository, or call the host's install/disable/uninstall APIs. Retiring the prototype is the
user's own click on `/apps/detail/aidlc-console` (C12); `apply` only names the steps in `next_steps`.
The only files it opens are the prototype's `repos.json` and its `installed.json`.

Threading (§0.6): sync module — stats, one `git rev-parse` per row through `RepoRegistry`, one file copy
and one SQLite transaction. Handlers call `preview`/`apply`/`status` through `asyncio.to_thread`.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Mapping, Sequence

from . import constants as C
from . import security
from .errors import StudioError
from .repo_registry import RepoRecord

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .constants import Clock, IdFactory
    from .repo_registry import RepoRegistry
    from .storage import Storage

try:  # pragma: no cover - the host module is always present in production
    from kiro_crew.atomic_write import atomic_write as _host_atomic_write
except Exception:  # pragma: no cover
    _host_atomic_write = None  # type: ignore[assignment]

__all__ = [
    "MIGRATION_ID",
    "MigrationPreview",
    "MigrationResult",
    "MigrationRowPlan",
    "MigrationService",
    "NEXT_STEPS",
]

# --------------------------------------------------------------------------- #
# vocabulary (data, so the i18n catalog and the tests read one source)
# --------------------------------------------------------------------------- #

#: Identity of this migration in the `migrations` table. Versioned: a future prototype-to-Studio move
#: gets its own id rather than reusing this row, so "already applied" can never be ambiguous.
MIGRATION_ID = "aidlc-console-v1"

#: What the UI offers after a successful apply. Codes, not prose (C11) — and deliberately *not* actions
#: this backend performs: `/api/apps/aidlc-console/*` is outside `permissions.api` (C12).
NEXT_STEPS: tuple[str, ...] = ("disable_console", "uninstall_console_keep_data")

#: `MigrationPreview.reason` values (§1.22). `None` means applicable.
REASON_NOT_FOUND = "not_found"
REASON_ALREADY_APPLIED = "already_applied"
REASON_MALFORMED = "malformed"

#: `MigrationResult.status` values (§1.22).
STATUS_APPLIED = "applied"
STATUS_FAILED = "failed"
STATUS_ROLLED_BACK = "rolled_back"

#: `MigrationRowPlan.resolution` prefixes. `duplicate_of:`/`already_registered:` carry an id.
RESOLUTION_MIGRATE = "migrate"
RESOLUTION_UNAVAILABLE = "unavailable"
RESOLUTION_DUPLICATE_PREFIX = "duplicate_of:"
RESOLUTION_REGISTERED_PREFIX = "already_registered:"

#: `MigrationRowPlan.error` codes. The first three come from `RepoRegistry`; `malformed_row` is this
#: module's own — an element of the source list that is not a row with a path, which is data to report
#: rather than to migrate (the prototype's own loader ignores such elements too, 07 §3.3).
ERROR_MALFORMED_ROW = "malformed_row"

#: How many rollback copies of the source are kept (FR-MIG-004 "bounded"). One apply writes one copy, so
#: this only ever trims after repeated rolled-back attempts.
MAX_BACKUPS = 5


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


class _RandomIdFactory:
    """The production `IdFactory` shape (§0.1), used when the caller injects none.

    `MigrationService` mints one `repo_id` per migrated row, and §1.22's constructor does not list an
    id factory; a local default keeps the documented four-argument construction working without making
    ids unpredictable in production or non-deterministic in tests (which inject their own).
    """

    def new(self, prefix: str) -> str:
        return f"{prefix}_{secrets.token_hex(6 if prefix == 'r' else 8)}"


def _write_file_bytes(path: Path, data: bytes) -> None:
    """Atomically write raw bytes, preferring the host's writer (architecture §7.0).

    The host's version carries the Windows rename retry and the fsync; the local fallback exists so the
    module stays usable (and testable) if that symbol ever changes shape, because a backup that cannot
    be written must fail the migration, not be skipped.
    """
    if _host_atomic_write is not None:
        try:
            _host_atomic_write(path, data, fsync=True)
            return
        except TypeError:  # pragma: no cover - signature drift, fall through to the local writer
            pass
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# public shapes (§1.22 / §3.1)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MigrationRowPlan:
    """One source row and what the migration would do with it."""

    legacy_id: str
    path: str
    label: str
    added_at: str | None
    resolution: str
    identity: str | None
    error: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "legacy_id": self.legacy_id,
            "path": self.path,
            "label": self.label,
            "added_at": self.added_at,
            "resolution": self.resolution,
            "identity": self.identity,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class MigrationPreview:
    """What `apply` would do, computed without writing anything."""

    applicable: bool
    reason: str | None
    source_path: str
    source_sha256: str | None
    rows_in: int
    rows: tuple[MigrationRowPlan, ...]
    rows_out: int
    console_installed: bool
    console_enabled: bool
    already_applied: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "applicable": self.applicable,
            "reason": self.reason,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "rows_in": self.rows_in,
            "rows": [row.to_json() for row in self.rows],
            "rows_out": self.rows_out,
            "console_installed": self.console_installed,
            "console_enabled": self.console_enabled,
            "already_applied": self.already_applied,
        }


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """The durable record of one apply attempt (`migrations` row, verbatim)."""

    migration_id: str
    applied_at: str
    status: str
    backup_path: str
    summary: dict[str, Any]
    next_steps: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "migration_id": self.migration_id,
            "applied_at": self.applied_at,
            "status": self.status,
            "backup_path": self.backup_path,
            "summary": dict(self.summary),
            "next_steps": list(self.next_steps),
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "MigrationResult":
        status = str(row.get("status") or "")
        summary = row.get("summary_json")
        return cls(
            migration_id=str(row.get("id") or ""),
            applied_at=str(row.get("applied_at") or ""),
            status=status,
            backup_path=str(row.get("backup_path") or ""),
            summary=dict(summary) if isinstance(summary, Mapping) else {},
            next_steps=NEXT_STEPS if status == STATUS_APPLIED else (),
        )


@dataclass(slots=True)
class _Candidate:
    """A planned row plus the fields only `apply` needs. Mutable: merging rewrites two of them."""

    legacy_id: str
    path: str
    label: str
    added_at: str | None
    resolution: str
    identity: str | None
    error: str | None
    canonical_path: str
    git_common_dir: str | None
    availability: str
    availability_detail: str | None
    key: str
    insertable: bool
    repo_id: str = ""
    existing_repo_id: str = ""

    def plan(self) -> MigrationRowPlan:
        return MigrationRowPlan(
            legacy_id=self.legacy_id,
            path=self.path,
            label=self.label,
            added_at=self.added_at,
            resolution=self.resolution,
            identity=self.identity,
            error=self.error,
        )


class _CountMismatch(Exception):
    """Raised inside the apply transaction when the row count does not match the plan."""


@dataclass(frozen=True, slots=True)
class _Source:
    """One read of the prototype's registry file: bytes, digest, parsed rows, or why not."""

    path: Path
    raw: bytes | None
    sha256: str | None
    rows: tuple[Any, ...]
    reason: str | None


# --------------------------------------------------------------------------- #
# the service
# --------------------------------------------------------------------------- #


class MigrationService:
    """Detect, preview and apply the prototype registry migration. Owns the `migrations` table row."""

    __slots__ = ("_ctx", "_storage", "_registry", "_clock", "_ids")

    def __init__(
        self,
        ctx: Any,
        storage: "Storage",
        registry: "RepoRegistry",
        clock: "Clock",
        ids: "IdFactory | None" = None,
    ) -> None:
        self._ctx = ctx
        self._storage = storage
        self._registry = registry
        self._clock = clock
        self._ids: Any = ids or _RandomIdFactory()

    # ---- detection ----

    def apps_root(self) -> Path:
        """The directory both apps live in (`…/apps`), derived from Studio's own `data_dir`.

        Derived rather than recomputed from `config_dir()`: the app must find its sibling on whatever
        root the host actually gave it, including the temp roots tests and dev installs use.
        """
        return Path(self._ctx.data_dir).parent.parent

    def source_path(self) -> Path:
        """The prototype's registry file. Survives a keep-data uninstall, which is why it is the probe."""
        return self.apps_root() / C.LEGACY_APP_NAME / "data" / "kv" / f"{C.LEGACY_STORAGE_KEY}.json"

    def console_state(self) -> tuple[bool, bool]:
        """`(installed, enabled)` from the prototype's `installed.json`; `(False, False)` when absent.

        Read (not asked of the host) because Studio has no permission for `/api/apps/*` (C12) and
        because a keep-data uninstall removes this file while leaving the registry — the state the
        migration must still be able to describe.
        """
        path = self.apps_root() / C.LEGACY_APP_NAME / "installed.json"
        raw = security.bounded_read(path, C.MAX_JSON_BYTES)
        if raw is None:
            return (path.is_file(), False)
        data = _loads(raw)
        if not isinstance(data, Mapping):
            return (True, False)
        return (True, bool(data.get("enabled")))

    # ---- read-only preview ----

    def preview(self) -> MigrationPreview:
        """What `apply` would do, computed without writing anything anywhere.

        It reads the prototype's two files and stats each registered path (identity resolution, §1.5);
        it opens nothing inside a repository, creates no backup and touches no row.
        """
        source = self._read_source()
        applied_row = self._applied_row()
        installed, enabled = self.console_state()
        candidates = self._plan(source.rows) if source.reason is None else []
        if applied_row is not None:
            reason: str | None = REASON_ALREADY_APPLIED
        else:
            reason = source.reason
        rows = tuple(candidate.plan() for candidate in candidates)
        return MigrationPreview(
            applicable=reason is None,
            reason=reason,
            source_path=str(source.path),
            source_sha256=source.sha256,
            rows_in=len(source.rows),
            rows=rows,
            rows_out=sum(1 for candidate in candidates if candidate.insertable),
            console_installed=installed,
            console_enabled=enabled,
            already_applied=applied_row is not None,
        )

    # ---- apply ----

    def apply(self, *, expect_sha256: str | None = None) -> MigrationResult:
        """Migrate the prototype registry once, under a validated transaction.

        `expect_sha256` is the digest the caller showed the user: supplying it makes "the preview the
        user confirmed" and "the bytes that get migrated" the same read, so a registry edited between
        the two calls is refused (`bad_body`) instead of silently migrating something else.
        """
        if self._applied_row() is not None:
            raise StudioError(
                "migration_already_applied",
                "the aidlc-console registry has already been migrated",
                details={"migration_id": MIGRATION_ID},
            )
        source = self._read_source()
        if source.reason is not None or source.raw is None:
            raise StudioError(
                "migration_not_applicable",
                "there is no readable aidlc-console registry to migrate",
                details={"reason": source.reason or REASON_NOT_FOUND, "source_path": str(source.path)},
            )
        if expect_sha256 is not None and expect_sha256 != source.sha256:
            raise StudioError(
                "bad_body",
                "the aidlc-console registry changed since the preview",
                details={"key": "source_sha256", "expected": source.sha256},
            )

        candidates = self._plan(source.rows)
        inserts = [candidate for candidate in candidates if candidate.insertable]
        rows_out = len(inserts)
        existing = len(self._storage.select("repos"))
        if existing + rows_out > C.MAX_REPOS:
            raise StudioError(
                "too_many_repos",
                "migrating this registry would exceed the repository cap",
                details={"max": C.MAX_REPOS, "count": existing, "incoming": rows_out},
            )

        # Before the first write: the rollback copy of exactly the bytes that were validated.
        backup_path = self._write_backup(source.raw)
        summary = self._summary(source, candidates, rows_out)

        unique = {candidate.key for candidate in inserts}
        if len(unique) != rows_out:  # pragma: no cover - the planner merges by this key
            return self._record(
                STATUS_ROLLED_BACK, backup_path, {**summary, "error": "duplicate_identity"}
            )

        applied_at = self._clock.iso()
        inserted: list[str] = []
        try:
            with self._one_transaction():
                before = len(self._storage.select("repos"))
                for candidate in inserts:
                    candidate.repo_id = self._ids.new("r")
                    self._storage.insert("repos", self._repo_row(candidate))
                    inserted.append(candidate.repo_id)
                self._stamp_legacy_ids(candidates)
                after = len(self._storage.select("repos"))
                if after - before != rows_out:
                    raise _CountMismatch(f"{after - before} rows appeared, {rows_out} planned")
                summary["repo_ids"] = list(inserted)
                self._write_migration_row(STATUS_APPLIED, backup_path, summary, applied_at)
        except _CountMismatch as exc:
            self._undo(inserted)
            return self._record(
                STATUS_ROLLED_BACK, backup_path, {**summary, "repo_ids": [], "error": str(exc)}
            )
        except Exception:
            # An unexpected failure is not a refusal: undo, record what happened, and let the caller
            # see the original error — recording must never be what hides it.
            self._undo(inserted)
            try:
                self._record(
                    STATUS_ROLLED_BACK, backup_path, {**summary, "repo_ids": [], "error": "insert_failed"}
                )
            except Exception:  # pragma: no cover - the store that just failed may fail again
                pass
            raise
        return MigrationResult(
            migration_id=MIGRATION_ID,
            applied_at=applied_at,
            status=STATUS_APPLIED,
            backup_path=backup_path,
            summary=summary,
            next_steps=NEXT_STEPS,
        )

    # ---- status ----

    def status(self) -> dict[str, Any]:
        """`GET /migration/status` (§2.8): whether it ran, what it did, and whether a preview is offerable."""
        row = self._storage.get("migrations", MIGRATION_ID)
        result = MigrationResult.from_row(row) if row else None
        installed, enabled = self.console_state()
        return {
            "applied": bool(row and row.get("status") == STATUS_APPLIED),
            "result": result.to_json() if result else None,
            "preview_available": self.source_path().is_file(),
            "console": {"installed": installed, "enabled": enabled},
        }

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _applied_row(self) -> dict[str, Any] | None:
        row = self._storage.get("migrations", MIGRATION_ID)
        if row and row.get("status") == STATUS_APPLIED:
            return row
        return None

    def _read_source(self) -> _Source:
        """One bounded read of the prototype registry: the bytes, their digest and the parsed list.

        The digest and the archived copy come from this single read so the preview, the validation and
        the backup can never describe three different versions of the file.
        """
        path = self.source_path()
        if not path.is_file():
            return _Source(path, None, None, (), REASON_NOT_FOUND)
        raw = security.bounded_read(path, C.MAX_JSON_BYTES)
        if raw is None:
            return _Source(path, None, None, (), REASON_MALFORMED)
        sha = security.sha256_bytes(raw)
        data = _loads(raw)
        if not isinstance(data, list):
            return _Source(path, raw, sha, (), REASON_MALFORMED)
        return _Source(path, raw, sha, tuple(data), None)

    def _plan(self, rows: Sequence[Any]) -> list[_Candidate]:
        """One `_Candidate` per source element, with duplicates and existing rows resolved.

        Order is the file's, and the first row of a colliding group survives: the migration must be
        reproducible from the same file, and the earliest registration is the one whose `added_at` the
        user recognises. The survivor keeps the earliest `added_at` of the group so the queue's
        oldest-first ordering does not shift under the user's feet.
        """
        index = self._existing_index()
        survivors: dict[str, int] = {}
        out: list[_Candidate] = []
        for raw in rows:
            candidate = self._candidate(raw, len(out))
            existing_id = self._existing_match(index, candidate)
            if existing_id:
                candidate.resolution = f"{RESOLUTION_REGISTERED_PREFIX}{existing_id}"
                candidate.existing_repo_id = existing_id
                candidate.insertable = False
            elif candidate.key in survivors:
                survivor = out[survivors[candidate.key]]
                _merge_into(survivor, candidate)
                candidate.resolution = f"{RESOLUTION_DUPLICATE_PREFIX}{survivor.legacy_id}"
                candidate.insertable = False
            elif candidate.insertable:
                survivors[candidate.key] = len(out)
            out.append(candidate)
        return out

    def _candidate(self, raw: Any, position: int) -> _Candidate:
        """Resolve one source row: identity when the directory is there, availability when it is not."""
        row: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
        legacy_id = _text(row.get("id"))
        path_text = _text(row.get("path"))
        label_text = _text(row.get("label"))
        added_at = _text(row.get("added_at")) or None
        if added_at is not None and C.epoch_from_iso(added_at) is None:
            added_at = None  # unparseable: the insert falls back to "now" rather than storing garbage
        if not path_text:
            return _Candidate(
                legacy_id=legacy_id,
                path=str(row.get("path") or ""),
                label=label_text,
                added_at=added_at,
                resolution=RESOLUTION_UNAVAILABLE,
                identity=None,
                error=ERROR_MALFORMED_ROW,
                canonical_path="",
                git_common_dir=None,
                availability="unavailable",
                availability_detail=ERROR_MALFORMED_ROW,
                key=f"malformed:{position}",
                insertable=False,
            )

        try:
            identity = self._registry.resolve_identity(path_text)
        except StudioError as exc:
            canonical = str(security.realpath(Path(path_text).expanduser()))
            availability, detail = self._unavailable_state(exc, canonical)
            return _Candidate(
                legacy_id=legacy_id,
                path=path_text,
                label=label_text or Path(canonical).name or canonical,
                added_at=added_at,
                resolution=RESOLUTION_UNAVAILABLE,
                identity=None,
                error=exc.code,
                canonical_path=canonical,
                git_common_dir=None,
                availability=availability,
                availability_detail=detail,
                key=f"path:{os.path.normcase(canonical)}",
                insertable=True,
            )

        provable = identity.provable
        return _Candidate(
            legacy_id=legacy_id,
            path=path_text,
            label=label_text or Path(identity.canonical_path).name or identity.canonical_path,
            added_at=added_at,
            resolution=RESOLUTION_MIGRATE,
            identity=identity.identity_str if provable else None,
            error=None if provable else "identity_unprovable",
            canonical_path=identity.canonical_path,
            git_common_dir=identity.git_common_dir,
            availability="available" if provable else "identity_unprovable",
            availability_detail=None if provable else "identity_unprovable",
            key=(
                f"identity:{identity.identity_str}"
                if provable
                else f"path:{os.path.normcase(identity.canonical_path)}"
            ),
            insertable=True,
        )

    def _unavailable_state(self, exc: StudioError, canonical: str) -> tuple[str, str | None]:
        """Availability for a row whose path did not resolve, from `RepoRegistry`'s own check.

        A protected path is the one case decided here: the registry would report it `available` (its
        check does not consult `is_sensitive_path`), but Studio may not read it, and registering it as
        available would offer install and execution over a directory every read refuses. It is kept as
        an unavailable row so the user can rebind or delete it.
        """
        if exc.code == "sensitive_path":
            return "unavailable", "sensitive_path"
        probe = RepoRecord(
            repo_id="",
            label="",
            canonical_path=canonical,
            resolved_identity=None,
            git_common_dir_identity=None,
            platform="",
            added_at="",
            last_seen=None,
            availability="available",
            availability_detail=None,
            archived=False,
            legacy_console_id=None,
            install_status="not_installed",
            installed_engine_version=None,
            engine_dir=None,
            receipt_version=None,
        )
        return self._registry.check_availability(probe)

    def _existing_index(self) -> dict[str, dict[str, str]]:
        """Everything already in Studio's registry, keyed three ways.

        All three are needed: `legacy` recognises a row a crashed apply already inserted even if the
        user has since rebound it, `identity` recognises the same directory under another spelling, and
        `path` recognises a row whose identity was never provable.
        """
        by_legacy: dict[str, str] = {}
        by_identity: dict[str, str] = {}
        by_path: dict[str, str] = {}
        for row in self._storage.select("repos", order_by="added_at ASC, id ASC"):
            repo_id = str(row.get("id") or "")
            legacy = _text(row.get("legacy_console_id"))
            identity = _text(row.get("resolved_identity"))
            path = _text(row.get("canonical_path"))
            if legacy:
                by_legacy.setdefault(legacy, repo_id)
            if identity:
                by_identity.setdefault(identity, repo_id)
            if path:
                by_path.setdefault(os.path.normcase(path), repo_id)
        return {"legacy": by_legacy, "identity": by_identity, "path": by_path}

    @staticmethod
    def _existing_match(index: Mapping[str, Mapping[str, str]], candidate: _Candidate) -> str:
        if not candidate.insertable:
            return ""
        if candidate.legacy_id:
            hit = index["legacy"].get(candidate.legacy_id)
            if hit:
                return hit
        if candidate.identity:
            hit = index["identity"].get(candidate.identity)
            if hit:
                return hit
        if candidate.canonical_path:
            hit = index["path"].get(os.path.normcase(candidate.canonical_path))
            if hit:
                return hit
        return ""

    def _repo_row(self, candidate: _Candidate) -> dict[str, Any]:
        """The `repos` row for one migrated registration.

        `install_status` stays `not_installed` and no engine version is recorded: detecting an install
        means reading `.kiro/tools/*` inside the repository, which this module does not do (§1.22). The
        first rescan fills it in from the repository's own bytes.
        """
        now = self._clock.iso()
        available = candidate.availability in ("available", "identity_unprovable")
        return {
            "id": candidate.repo_id,
            "resolved_identity": candidate.identity,
            "canonical_path": candidate.canonical_path,
            "git_common_dir_identity": candidate.git_common_dir,
            "label": (candidate.label or Path(candidate.canonical_path).name)[: C.MAX_LABEL_CHARS],
            "added_at": candidate.added_at or now,
            "last_seen": now if available else None,
            "platform": sys.platform,
            "install_status": "not_installed",
            "installed_engine_version": None,
            "engine_dir": None,
            "receipt_version": None,
            "archived": 0,
            "availability": candidate.availability,
            "availability_detail": candidate.availability_detail,
            "legacy_console_id": candidate.legacy_id or None,
            "scan_json": None,
        }

    def _stamp_legacy_ids(self, candidates: Sequence[_Candidate]) -> None:
        """Record the prototype id on a repository the user had already registered by hand.

        Studio-owned metadata only, and only when the column is still empty: it is what lets a link or
        a bookmark carrying a prototype id resolve to the right repository after the migration.
        """
        for candidate in candidates:
            if not candidate.existing_repo_id or not candidate.legacy_id:
                continue
            row = self._storage.get("repos", candidate.existing_repo_id)
            if row is None or _text(row.get("legacy_console_id")):
                continue
            self._storage.update(
                "repos", candidate.existing_repo_id, {"legacy_console_id": candidate.legacy_id}
            )

    def _summary(
        self, source: _Source, candidates: Sequence[_Candidate], rows_out: int
    ) -> dict[str, Any]:
        return {
            "rows_in": len(source.rows),
            "rows_out": rows_out,
            "repo_ids": [],
            "unavailable": sum(
                1
                for candidate in candidates
                if candidate.insertable and candidate.resolution == RESOLUTION_UNAVAILABLE
            ),
            "duplicates": sum(
                1
                for candidate in candidates
                if candidate.resolution.startswith(RESOLUTION_DUPLICATE_PREFIX)
            ),
            "already_registered": sum(
                1
                for candidate in candidates
                if candidate.resolution.startswith(RESOLUTION_REGISTERED_PREFIX)
            ),
            "source_sha256": source.sha256,
        }

    # ---- backup ----

    def _write_backup(self, raw: bytes) -> str:
        """Copy the source bytes into Studio's own storage and trim to `MAX_BACKUPS` copies.

        The timestamp in the name is colon-free: `:` is legal in a POSIX filename but not on Windows,
        and a backup that cannot be created on one platform is the one that will be needed there.
        """
        directory = Path(self._ctx.data_dir) / C.MIGRATION_BACKUP_DIRNAME
        directory.mkdir(parents=True, exist_ok=True)
        stamp = self._clock.iso().replace("-", "").replace(":", "")
        path = directory / f"{C.LEGACY_APP_NAME}-{C.LEGACY_STORAGE_KEY}.{stamp}.json"
        _write_file_bytes(path, raw)
        self._trim_backups(directory, keep=path)
        return str(path)

    @staticmethod
    def _trim_backups(directory: Path, *, keep: Path) -> None:
        try:
            copies = sorted(p for p in directory.glob("*.json") if p.is_file())
        except OSError:  # pragma: no cover - the directory was just created
            return
        for stale in copies[: max(0, len(copies) - MAX_BACKUPS)]:
            if stale == keep:
                continue
            try:
                stale.unlink()
            except OSError:  # pragma: no cover - a copy someone else removed is fine
                pass

    # ---- durable record ----

    def _record(self, status: str, backup_path: str, summary: Mapping[str, Any]) -> MigrationResult:
        applied_at = self._clock.iso()
        self._write_migration_row(status, backup_path, summary, applied_at)
        return MigrationResult(
            migration_id=MIGRATION_ID,
            applied_at=applied_at,
            status=status,
            backup_path=backup_path,
            summary=dict(summary),
            next_steps=NEXT_STEPS if status == STATUS_APPLIED else (),
        )

    def _write_migration_row(
        self, status: str, backup_path: str, summary: Mapping[str, Any], applied_at: str
    ) -> None:
        """One row per migration id: a previous non-applied attempt is replaced, never accumulated.

        Replacing is safe precisely because `apply` refuses once the row says `applied`; what is being
        overwritten is always a failed attempt whose repository rows were undone.
        """
        row = {
            "id": MIGRATION_ID,
            "applied_at": applied_at,
            "status": status,
            "backup_path": backup_path,
            "summary_json": dict(summary),
        }
        if self._storage.get("migrations", MIGRATION_ID) is not None:
            self._storage.delete("migrations", MIGRATION_ID)
        self._storage.insert("migrations", row)

    def _undo(self, repo_ids: Sequence[str]) -> None:
        """Remove rows a failed apply inserted, for the case where no transaction envelope was in force.

        Harmless after a rollback (the rows are already gone) and load-bearing without one: a partially
        migrated registry is the single outcome this module exists to prevent.
        """
        for repo_id in repo_ids:
            try:
                self._storage.delete("repos", repo_id)
            except Exception:  # pragma: no cover - best effort; the row may already be gone
                pass

    @contextmanager
    def _one_transaction(self) -> Iterator[None]:
        """Wrap the whole batch in one `BEGIN IMMEDIATE` when `Storage` offers one (§1.22).

        `Storage`'s transaction helper is re-entrant by design, so the public `insert`/`update` calls
        inside join this envelope instead of opening transactions of their own. When no such helper is
        exposed the batch still runs — `_undo` is what keeps the failure atomic in that case — because
        a missing private convenience must not disable the migration.
        """
        opener = getattr(self._storage, "transaction", None) or getattr(self._storage, "_txn", None)
        if opener is None:  # pragma: no cover - both host generations expose one
            yield
            return
        with opener():
            yield


def _merge_into(survivor: _Candidate, duplicate: _Candidate) -> None:
    """Fold a colliding row into the one that survives: keep the earliest registration time."""
    if duplicate.added_at and (survivor.added_at is None or duplicate.added_at < survivor.added_at):
        survivor.added_at = duplicate.added_at


def _loads(raw: bytes) -> Any:
    """Parse JSON, answering `None` for anything unparseable (the caller reports `malformed`)."""
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
