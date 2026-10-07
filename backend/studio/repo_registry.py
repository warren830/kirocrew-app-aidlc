"""Which directories Studio watches, and whether AI-DLC is really installed in them.

Identity is the filesystem's ``st_dev:st_ino``, not the path: APFS is case-insensitive, so two spellings can
be one directory (A13; the displayed path keeps the typed case). Without inode numbers it falls back to
``sha256(realpath + NUL + git_common_dir)``; an identity provable neither way registers observe-only, since
leases key on it (FR-REP-003/005). Install health comes from disk bytes plus Studio's receipt, never from the
engine: nothing from a candidate repository is executed (C35), and the only child processes reachable from
here are the injected ``git rev-parse``/``bun --version``, which preflight runs only with ``probe_tools``.
Nothing here writes into a repository (FR-REP-007) or scans a home or parent directory (FR-REP-002).
Sync and blocking: callers use ``asyncio.to_thread`` (§0.6).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

from . import constants as C
from . import security
from .engine import find_bun
from .errors import StudioError
from .storage import EvidenceRef, Storage

if TYPE_CHECKING:  # annotations only — importing these at runtime would invert the module layering
    from .constants import Clock, IdFactory
    from .git_observer import GitObserver
    from .installer import Receipt

# on-disk layout of an AI-DLC harness
TOOLS_DIRNAME = "tools"
ENGINE_VERSION_FILE = "aidlc-version.ts"
ENGINE_LIB_FILE = "aidlc-lib.ts"
ENGINE_UTILITY_FILE = "aidlc-utility.ts"
HARNESS_JSON_REL = "data/harness.json"
STAGE_GRAPH_REL = "data/stage-graph.json"

#: A ``<harness>/tools`` dir is an install only with one of these inside: a user's own ``.cursor/tools``
#: is not, and calling it one would offer an upgrade that overwrites their files.
HARNESS_MARKERS = (ENGINE_VERSION_FILE, HARNESS_JSON_REL, ENGINE_UTILITY_FILE)

#: Paths the installer writes or merges. A symlink at one would make ``os.replace`` write outside the
#: repository, which the installer refuses, so preflight reports it first.
MANAGED_PATH_PROBES = (
    *C.HARNESS_DIRS,
    *(f"{C.STUDIO_HARNESS_DIR}/{d}" for d in ("tools", "settings", "steering", "scopes", "agents", "hooks")),
    "aidlc", "AGENTS.md", ".gitignore",
)

#: The states a rebind may start from. Not ``identity_unprovable``: nothing is known to have moved.
REBINDABLE_AVAILABILITY = ("moved", "unavailable", "permission_denied")

#: Preflight warning codes (§1.5) with the severity each carries (§1.7 vocabulary).
PREFLIGHT_WARNING_SEVERITY = {"legacy_layout": "warn", "engine_version_unknown": "warn",
                              "mixed_harness_versions": "warn", "bun_missing": "warn", "git_missing": "info"}

#: What a rebind rewrites; label, archive flag, history and ``receipt_version`` stay as they were.
_REBIND_COLUMNS = ("canonical_path", "resolved_identity", "git_common_dir_identity", "platform",
                   "last_seen", "availability", "availability_detail", "install_status",
                   "installed_engine_version", "engine_dir")


# ---- records ----


def _json(value: Any) -> Any:
    """One field on the wire: a record through its ``to_json``, a sequence as a list (records inside it
    through theirs), a mapping as a shallow copy."""
    if callable(getattr(value, "to_json", None)):
        return value.to_json()
    if isinstance(value, (list, tuple)):
        return [v.to_json() if callable(getattr(v, "to_json", None)) else v for v in value]
    return dict(value) if isinstance(value, Mapping) else value


class _Record:
    """``to_json`` for the records below: fields in declaration order, which is the wire order."""

    __slots__ = ()

    def to_json(self) -> dict[str, Any]:
        return {f.name: _json(getattr(self, f.name)) for f in fields(self)}


@dataclass(frozen=True, slots=True)
class ResolvedIdentity(_Record):
    """What the filesystem says about one directory, and whether that is provable."""

    canonical_path: str
    st_dev: int | None
    st_ino: int | None
    git_common_dir: str | None
    identity_str: str
    provable: bool


@dataclass(frozen=True, slots=True)
class HarnessDir(_Record):
    """One ``<dir>/tools`` engine tree found in a repository, read from its own files."""

    dir: str
    harness_name: str | None
    rules_subdir: str | None
    engine_version: str | None
    engine_state_version: int | None
    stage_count: int | None
    has_utility: bool


@dataclass(frozen=True, slots=True)
class ReceiptSummary(_Record):
    """A receipt without its file list. Defined here because the registry sits below the installer."""

    receipt_id: str
    engine_version: str
    studio_version: str
    committed_at: str
    status: str
    files: int

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "ReceiptSummary":
        files = row.get("files_json") or []
        text = ("receipt_id", "engine_version", "studio_version", "committed_at", "status")
        return cls(**{name: str(row.get(name) or "") for name in text},
                   files=len(files) if isinstance(files, (list, tuple)) else 0)


@dataclass(frozen=True, slots=True)
class InstallHealth(_Record):
    """Whether AI-DLC is installed here, from disk bytes plus Studio's own receipt."""

    status: str
    engine_dir: str | None
    engine_version: str | None
    #: The version under Studio's own harness (``C.STUDIO_HARNESS_DIR``), else ``None``. ``engine_*`` name
    #: the harness Studio reads, which may be foreign; only the install lane keys on this (FR-INST-003).
    #: ``kw_only`` lets it default without moving: declaration order is the wire order.
    own_engine_version: str | None = field(default=None, kw_only=True)
    engine_state_version: int | None
    stage_count: int | None
    receipt_id: str | None
    receipt_engine_version: str | None
    drift_count: int
    harness_dirs: tuple[HarnessDir, ...]


@dataclass(frozen=True, slots=True)
class GitPreflight(_Record):
    """The git facts a preflight shows. Filled from ``GitObserver.observe``; never a write verb."""

    is_repo: bool
    branch: str | None
    dirty: bool
    common_dir: str | None


@dataclass(frozen=True, slots=True)
class ToolProbe(_Record):
    """Where a tool is; ``version`` only from an injected prober. ``source``/``searched`` are
    ``engine.find_bun``'s answer: "missing" is only actionable next to the locations that were tried."""

    found: bool
    path: str | None
    version: str | None
    source: str | None = None
    searched: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AidlcLayoutProbe(_Record):
    """Which AI-DLC workspace layout the repository has, and how much is in it."""

    layout: str | None
    spaces: tuple[str, ...]
    intents: int
    state_versions: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PreflightFinding(_Record):
    """A preflight warning in ``consistency.Finding``'s exact JSON shape (importing it would be a cycle)."""

    code: str
    severity: str
    message_key: str
    params: dict[str, Any]
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True)
class PreflightReport(_Record):
    """Everything a read-only look at a candidate directory can establish (FR-INST-001)."""

    path_input: str
    canonical_path: str | None
    identity: ResolvedIdentity | None
    is_directory: bool
    sensitive: bool
    duplicate_of: str | None
    platform: str
    git: GitPreflight | None
    bun: ToolProbe | None
    harness_dirs: tuple[HarnessDir, ...]
    aidlc: AidlcLayoutProbe
    symlinks_at_managed_paths: tuple[str, ...]
    free_space_bytes: int | None
    writable: bool | None
    existing_receipt: ReceiptSummary | None
    warnings: tuple[PreflightFinding, ...]
    can_register: bool
    can_install: bool


@dataclass(frozen=True, slots=True)
class RepoRecord(_Record):
    """One ``repos`` row; ``repo_id`` outlives every other field, including the identity (C01). The handler
    adds §2.2's ``install``/``counts``/``leases``/``git``/``findings`` around ``to_json()``."""

    repo_id: str
    label: str
    canonical_path: str
    resolved_identity: str | None
    git_common_dir_identity: str | None
    platform: str
    added_at: str
    last_seen: str | None
    availability: str
    availability_detail: str | None
    archived: bool
    #: Never set by Studio; kept because the column and the UI's wire type still carry it (``kw_only`` as
    #: above: a default without moving it).
    legacy_console_id: str | None = field(default=None, kw_only=True)
    install_status: str
    installed_engine_version: str | None
    engine_dir: str | None
    receipt_version: str | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "RepoRecord":
        """NOT NULL text columns come back as ``str`` (defaulted when blank); the rest pass through."""
        text = {"label": "", "canonical_path": "", "platform": sys.platform, "added_at": "",
                "availability": "available", "install_status": "not_installed"}
        values = {f.name: row.get(f.name) for f in fields(cls)}
        values.update({name: str(row.get(name) or default) for name, default in text.items()})
        return cls(**{**values, "repo_id": str(row["id"]), "archived": bool(row.get("archived"))})


@dataclass(frozen=True, slots=True)
class RepoScan(_Record):
    """One look at one repository. ``RepoRegistry.rescan`` fills the repo-level half; the reconciler adds
    ``intents``/``findings`` (``IntentSummary``/``Finding``, typed loosely: those modules sit above)."""

    repo_id: str
    availability: str
    install: InstallHealth
    intents: tuple[Any, ...]
    findings: tuple[Any, ...]
    took_ms: int
    truncated: bool
    scanned_at: str


def _warning(code: str, params: dict[str, Any], *evidence: EvidenceRef) -> PreflightFinding:
    return PreflightFinding(code, PREFLIGHT_WARNING_SEVERITY[code], f"finding.{code}", params, evidence)


def _read_json(path: Path) -> Any:
    raw = security.bounded_read(path, C.MAX_JSON_BYTES)
    try:
        return None if raw is None else json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return None


def _search(path: Path, pattern: re.Pattern[str], cap: int) -> str | None:
    """``pattern``'s first group in a bounded read of ``path``, or ``None``."""
    text = security.bounded_text(path, cap)
    match = pattern.search(text) if text else None
    return match.group(1) if match else None


# ---- the registry ----


class RepoRegistry:
    """Owns the ``repos`` table. Deliberately given no ``EngineRunner``, so no AI-DLC verb can run during
    preflight, add or detection (C35); ``bun_version`` is injected by the module that owns child processes."""

    def __init__(self, storage: Storage, git: "GitObserver", clock: "Clock", ids: "IdFactory", *,
                 bun_version: Callable[[str], str | None] | None = None) -> None:
        self._storage, self._git, self._clock, self._ids = storage, git, clock, ids
        self._bun_version = bun_version
        self._bun_location = None

    def set_bun_location(self, location: Any) -> None:
        """Use the same validated interpreter selection as the application's engine runner."""
        self._bun_location = location

    # ---- identity ----

    def resolve_identity(self, path: str) -> ResolvedIdentity:
        """Identity of a user-supplied path; ``security`` raises ``bad_path``/``sensitive_path`` first."""
        return self._identity_for(security.validate_repo_root(path))

    def require_current_identity(self, repo: RepoRecord) -> ResolvedIdentity:
        """Revalidate the directory at the write boundary, including a concurrent rebind."""
        current = self.get(repo.repo_id)
        identity = self.resolve_identity(repo.canonical_path)
        if (current.canonical_path != repo.canonical_path
                or current.resolved_identity != repo.resolved_identity
                or not identity.provable or identity.identity_str != repo.resolved_identity):
            raise StudioError("repo_unavailable", "the registered repository identity changed",
                              details={"repo_id": repo.repo_id, "reason": "identity_changed"})
        return identity

    def preflight(self, path: str, *, probe_tools: bool = False) -> PreflightReport:
        """Read-only report on a candidate directory: protected, non-directory and registered paths are
        reported, not refused (``add`` refuses), and only an uncanonicalisable input raises ``bad_path``.
        Without ``probe_tools`` nothing is spawned — no ``git rev-parse``, no ``bun --version`` (C35)."""
        text = (path or "").strip()
        if not text or "\x00" in text:
            raise StudioError("bad_path", "path is required")
        expanded = Path(text).expanduser()
        if not expanded.is_absolute():
            raise StudioError("bad_path", "path must be absolute")

        root = security.realpath(expanded)
        is_directory, sensitive = root.is_dir(), security.is_sensitive(root)
        readable = is_directory and not sensitive
        identity = duplicate_of = git = git_reason = receipt = free_space = writable = None
        harness_dirs: tuple[HarnessDir, ...] = ()
        symlinks: tuple[str, ...] = ()
        layout = AidlcLayoutProbe(None, (), 0, ())
        warnings: list[PreflightFinding] = []
        if readable:
            identity = self._identity_for(root, use_git=probe_tools)
            duplicate_of = self._duplicate_of(identity)
            harness_dirs, layout = self._harness_dirs(root), self._layout_probe(root)
            symlinks = tuple(rel for rel in MANAGED_PATH_PROBES if (root / rel).is_symlink())
            with contextlib.suppress(OSError):
                free_space = int(shutil.disk_usage(root).free)
            writable = os.access(root, os.W_OK)
            if duplicate_of:
                receipt = self.current_receipt_summary(duplicate_of)
            if probe_tools:
                git, git_reason = self._git_preflight(root)
            warnings += self._layout_warnings(layout, harness_dirs)

        bun = None
        if probe_tools:
            # PATH alone misses bun under launchd (``engine.find_bun``); the finder proves a candidate with
            # the injected prober, so the one spawn happens at the injecting module's door.
            located = self._bun_location or find_bun(probe=self._bun_version)
            bun = ToolProbe(located.found, located.path, located.version, located.source, located.searched)
            if not bun.found:  # the finding is stored: "not installed" is only true next to where we looked
                warnings.append(_warning("bun_missing", {"locations": list(bun.searched)},
                                         EvidenceRef("studio", "bun")))
        if git_reason == "git_missing":
            warnings.append(_warning("git_missing", {}, EvidenceRef("studio", "git")))

        return PreflightReport(
            path_input=text, canonical_path=str(root), identity=identity, is_directory=is_directory,
            sensitive=sensitive, duplicate_of=duplicate_of, platform=sys.platform, git=git, bun=bun,
            harness_dirs=harness_dirs, aidlc=layout, symlinks_at_managed_paths=symlinks,
            free_space_bytes=free_space, writable=writable, existing_receipt=receipt,
            warnings=tuple(warnings), can_register=readable and duplicate_of is None,
            can_install=(readable and bool(writable) and not symlinks
                         and identity is not None and identity.provable),
        )

    # ---- registry mutations ----

    def add(self, path: str, label: str | None = None) -> RepoRecord:
        """Register a directory; nothing is stored unless every check passes. An unprovable identity is
        stored as ``identity_unprovable`` without ``resolved_identity``: no lease keys on it (FR-REP-005)."""
        root = security.validate_repo_root(path)
        identity = self._identity_for(root)
        self._refuse_duplicate(identity)
        count = len(self._storage.select("repos"))
        if count >= C.MAX_REPOS:
            raise StudioError("too_many_repos", "the repository registry is full",
                              details={"max": C.MAX_REPOS, "count": count})
        record = RepoRecord(
            repo_id=self._ids.new("r"),
            label=((label or "").strip() or root.name or str(root))[: C.MAX_LABEL_CHARS],
            added_at=self._clock.iso(), **self._located_at(identity), archived=False,
            install_status="not_installed", installed_engine_version=None, engine_dir=None,
            receipt_version=None,
        )
        record = self._with_install(record, self.detect_install(record))
        row = {**record.to_json(), "archived": int(record.archived)}
        row["id"] = row.pop("repo_id")
        self._storage.insert("repos", row)
        return record

    def remove(self, repo_id: str) -> None:
        """Unregister; never touches disk (FR-REP-007). Bindings and actions cascade with the row; lease rows
        are keyed by identity and are ``leases.py``'s to reclaim, with proof."""
        self.get(repo_id)
        self._storage.delete("repos", repo_id)

    def update_metadata(self, repo_id: str, patch: Mapping[str, Any]) -> RepoRecord:
        """Change display metadata, preserving the registration and all of its children."""
        self.get(repo_id)
        if not patch or set(patch) - {"label", "archived"}:
            raise StudioError("bad_body", "only label and archived can be changed")
        values: dict[str, Any] = {}
        if "label" in patch:
            label = patch["label"]
            if (not isinstance(label, str) or not label.strip() or "\x00" in label
                    or len(label.strip()) > C.MAX_LABEL_CHARS):
                raise StudioError("bad_body", "label must be nonempty text within the label limit")
            values["label"] = label.strip()
        if "archived" in patch:
            if not isinstance(patch["archived"], bool):
                raise StudioError("bad_body", "archived must be a boolean")
            values["archived"] = int(patch["archived"])
        self._storage.update("repos", repo_id, values)
        return self.get(repo_id)

    def rebind(self, repo_id: str, new_path: str) -> RepoRecord:
        """Point an unavailable registration at the directory it moved to, keeping ``repo_id``. Availability
        is recomputed, not trusted (rebinding a healthy repository would silently repoint its history), and
        persisted even on refusal, which repairs a stale ``moved`` badge."""
        record = self.get(repo_id)
        availability, detail = self.check_availability(record)
        if availability not in REBINDABLE_AVAILABILITY:
            if (record.availability, record.availability_detail) != (availability, detail):
                self._storage.update("repos", record.repo_id,
                                     {"availability": availability, "availability_detail": detail})
            raise StudioError("rebind_not_allowed",
                              "only a moved, unavailable or unreadable repository can be rebound",
                              details={"availability": availability, "repo_id": repo_id})
        identity = self.resolve_identity(new_path)
        self._refuse_duplicate(identity, exclude=repo_id)
        moved = replace(record, **self._located_at(identity))
        moved = self._with_install(moved, self.detect_install(moved, self.current_receipt_row(repo_id)))
        self._storage.update("repos", repo_id, {name: getattr(moved, name) for name in _REBIND_COLUMNS})
        return moved

    # ---- reads ----

    def list(self, *, include_archived: bool = False) -> list[RepoRecord]:
        where = None if include_archived else {"archived": 0}
        rows = self._storage.select("repos", where, order_by="added_at ASC, id ASC")
        return [RepoRecord.from_row(row) for row in rows]

    def get(self, repo_id: str) -> RepoRecord:
        row = self._storage.get("repos", repo_id)
        if row is None:
            raise StudioError("repo_not_found", "no such repository", details={"repo_id": repo_id})
        return RepoRecord.from_row(row)

    def check_availability(self, rec: RepoRecord) -> tuple[str, str | None]:
        """``(availability, reason_code)`` from a fresh stat plus an identity recompute; never raises. A
        renamed directory keeps its inode (or its git common dir), which makes it a rebindable ``moved``."""
        root = Path(rec.canonical_path)
        try:
            st = os.stat(root)
        except PermissionError:
            return "permission_denied", "permission_denied"
        except (FileNotFoundError, NotADirectoryError):
            if self._identity_elsewhere(rec):
                return "moved", "identity_at_other_repo"
            if rec.git_common_dir_identity and Path(rec.git_common_dir_identity).exists():
                return "moved", "git_common_dir_present"
            return "unavailable", "path_missing"
        except OSError:
            return "unavailable", "stat_failed"
        if not stat.S_ISDIR(st.st_mode):
            return "unavailable", "not_a_directory"
        identity = self._identity_for(root)
        if not identity.provable:
            return "identity_unprovable", "identity_unprovable"
        if rec.resolved_identity and identity.identity_str != rec.resolved_identity:
            return "moved", "identity_changed"
        return "available", None

    def detect_install(self, rec: RepoRecord,
                       receipt: "Receipt | Mapping[str, Any] | None" = None) -> InstallHealth:
        """Install status from the repository's own bytes plus Studio's receipt (§1.5); runs no engine verb.

        No harness → ``not_installed``; a stored ``recovery_required`` stays (FR-INST-013); a changed
        receipt-owned file → ``drift``; else ``installed`` for ANY harness (a ``.claude`` repository is
        drivable), so "is ours installed" is ``own_engine_version``. An unreadable root (an unmounted
        volume) keeps the stored state rather than claiming ``not_installed``."""
        root = Path(rec.canonical_path)
        receipt = receipt if receipt is not None else self.current_receipt_row(rec.repo_id)
        from_receipt = {"receipt_id": C.attr(receipt, "receipt_id"),
                        "receipt_engine_version": C.attr(receipt, "engine_version")}
        if not root.is_dir():
            ours = rec.engine_dir == C.STUDIO_HARNESS_DIR
            return InstallHealth(
                status=rec.install_status, engine_dir=rec.engine_dir,
                engine_version=rec.installed_engine_version,
                own_engine_version=rec.installed_engine_version if ours else None,
                engine_state_version=None, stage_count=None, drift_count=0, harness_dirs=(), **from_receipt,
            )

        harness_dirs = self._harness_dirs(root)
        own = next((h for h in harness_dirs if h.dir == C.STUDIO_HARNESS_DIR), None)
        engine = own or next(iter(harness_dirs), None)  # the harness Studio reads and dispatches through
        drift_count = self._drift_count(root, receipt) if receipt is not None else 0
        if not harness_dirs:
            status = "not_installed"
        elif rec.install_status == "recovery_required":
            status = "recovery_required"
        else:
            status = "drift" if drift_count else "installed"
        return InstallHealth(
            status=status, engine_dir=C.attr(engine, "dir"), engine_version=C.attr(engine, "engine_version"),
            own_engine_version=C.attr(own, "engine_version"), stage_count=C.attr(engine, "stage_count"),
            engine_state_version=C.attr(engine, "engine_state_version"), drift_count=drift_count,
            harness_dirs=harness_dirs, **from_receipt)

    def rescan(self, repo_id: str) -> RepoScan:
        """Re-establish and persist availability and install health (the reconciler adds intents, §1.13).
        ``last_seen`` moves only when the repository was actually readable."""
        record = self.get(repo_id)
        started = self._clock.monotonic()
        availability, detail = self.check_availability(record)
        health = self.detect_install(record, self.current_receipt_row(repo_id))
        scanned_at = self._clock.iso()
        took_ms = max(0, int((self._clock.monotonic() - started) * 1000))
        scan = RepoScan(repo_id=repo_id, availability=availability, install=health, intents=(), findings=(),
                        took_ms=took_ms, truncated=False, scanned_at=scanned_at)
        values: dict[str, Any] = {
            "availability": availability, "availability_detail": detail, "install_status": health.status,
            "installed_engine_version": health.engine_version, "engine_dir": health.engine_dir,
            "receipt_version": health.receipt_id, "scan_json": scan.to_json(),
        }
        if availability == "available":
            values["last_seen"] = scanned_at
        self._storage.update("repos", repo_id, values)
        return scan

    def current_receipt_row(self, repo_id: str) -> dict[str, Any] | None:
        """The current receipt row, or ``None``. Storage read only — no file hashing."""
        rows = self._storage.select("install_receipts", {"repo_id": repo_id, "status": "current"}, limit=1)
        return rows[0] if rows else None

    def current_receipt_summary(self, repo_id: str) -> ReceiptSummary | None:
        row = self.current_receipt_row(repo_id)
        return ReceiptSummary.from_row(row) if row else None

    # ---- internals ----

    def _identity_for(self, root: Path, *, use_git: bool = True) -> ResolvedIdentity:
        """Identity of an already-resolved directory; never raises or writes. ``st_ino == 0`` means "no
        inode numbers" (some network/emulated filesystems): trusting it would merge a mount's repositories."""
        try:
            st: os.stat_result | None = os.stat(root)
        except OSError:
            st = None
        st_dev, st_ino = (int(st.st_dev), int(st.st_ino)) if st is not None and st.st_ino else (None, None)
        common = self._common_dir(root) if use_git else None
        inode = st_ino is not None
        identity_str = f"{st_dev}:{st_ino}" if inode else security.sha256_text(f"{root}\0{common or ''}")
        return ResolvedIdentity(str(root), st_dev, st_ino, common, identity_str, inode or common is not None)

    def _common_dir(self, root: Path) -> str | None:
        """``git rev-parse --git-common-dir`` as a realpath, or ``None``: a missing or wedged git degrades
        identity to "no git", never fails a registration."""
        try:
            value = self._git.common_dir(root)
        except Exception:  # noqa: BLE001 - a broken observer may never break identity resolution
            return None
        return str(security.realpath(str(value))) if value else None

    def _git_preflight(self, root: Path) -> tuple[GitPreflight | None, str | None]:
        try:
            obs = self._git.observe(root)
        except Exception:  # noqa: BLE001 - FR-GIT-005: an observation failure is data, not an error
            return None, "error"
        available = bool(C.attr(obs, "available", False))
        common = self._common_dir(root) if available else None
        git = GitPreflight(is_repo=available, branch=C.attr(obs, "branch"),
                           dirty=bool(C.attr(obs, "dirty", False)), common_dir=common)
        return git, C.attr(obs, "reason")

    @staticmethod
    def _harness_dirs(root: Path) -> tuple[HarnessDir, ...]:
        found: list[HarnessDir] = []
        for name in C.HARNESS_DIRS:
            try:
                tools = security.resolve_inside(root, f"{name}/{TOOLS_DIRNAME}")
            except StudioError:
                continue
            if not tools.is_dir() or not any((tools / marker).exists() for marker in HARNESS_MARKERS):
                continue
            harness = _read_json(tools / HARNESS_JSON_REL)
            harness = harness if isinstance(harness, dict) else {}
            version = _search(tools / ENGINE_VERSION_FILE, C.ENGINE_VERSION_RE, C.MAX_ARTIFACT_RENDER_BYTES)
            # The state version: ``CURRENT_STATE_VERSION`` from 2.5 on, else the older engines' state
            # template. Unknown rather than guessed, which would fire ``state_version_unsupported``.
            state = (_search(tools / ENGINE_LIB_FILE, C.ENGINE_STATE_VERSION_RE, C.MAX_ENGINE_SOURCE_BYTES)
                     or _search(tools / ENGINE_UTILITY_FILE, C.TEMPLATE_STATE_VERSION_RE,
                                C.MAX_ENGINE_SOURCE_BYTES))
            graph = _read_json(tools / STAGE_GRAPH_REL)
            graph = graph.get("stages") if isinstance(graph, dict) else graph
            found.append(HarnessDir(
                dir=name, harness_name=harness.get("name") or None,
                rules_subdir=harness.get("rulesSubdir") or None, engine_version=version,
                engine_state_version=int(state) if state else None,
                stage_count=len(graph) if isinstance(graph, list) else None,
                has_utility=security.is_regular_file(tools / ENGINE_UTILITY_FILE),
            ))
        return tuple(found)

    @staticmethod
    def _drift_count(root: Path, receipt: Any) -> int:
        """Receipt-owned files that no longer hash to their recorded digest. Only ``kind == "file"``: a
        fragment's digest depends on the installer's merge strategy (§1.14); guessing flags user edits."""
        entries = C.attr(receipt, "files")  # ``Receipt.files``, or ``files_json`` on a stored row
        if entries is None:
            entries = C.attr(receipt, "files_json")
        count = 0
        for entry in entries or ():
            expected, rel = C.attr(entry, "sha256"), C.attr(entry, "path")
            if str(C.attr(entry, "kind", "file")) != "file" or not expected or not rel:
                continue
            try:
                live = security.sha256_file(security.resolve_inside(root, str(rel)))
            except StudioError:
                live = None  # a receipt path that no longer resolves inside the repo is drift
            count += live != expected
        return count

    @staticmethod
    def _layout_probe(root: Path) -> AidlcLayoutProbe:
        """Workspace layout plus its state versions. ``legacy`` is the engine's ``needsFlatMigration`` test
        (``04 §5``: a flat state file, no spaces). Reads are bounded and capped so preflight cannot stall."""
        spaces: list[str] = []
        records: list[Path] = []
        spaces_root = root / "aidlc" / "spaces"
        for entry in sorted(spaces_root.iterdir()) if spaces_root.is_dir() else ():
            if entry.is_symlink() or not entry.is_dir() or not C.SPACE_RE.match(entry.name):
                continue
            spaces.append(entry.name)
            intents = entry / "intents"
            for record in sorted(intents.iterdir()) if intents.is_dir() else ():
                if len(records) >= C.MAX_INTENTS_PER_REPO:
                    break
                state = record / "aidlc-state.md"
                if not record.is_symlink() and record.is_dir() and security.is_regular_file(state):
                    records.append(state)
        layout: str | None = None
        if records or spaces:
            layout = "spaces"
        elif security.is_regular_file(root / C.LEGACY_STATE_REL):
            layout = "legacy"
            records.append(root / C.LEGACY_STATE_REL)
        found = (_search(state, C.TEMPLATE_STATE_VERSION_RE, C.MAX_STATE_BYTES) for state in records)
        versions = sorted({int(version) for version in found if version})
        return AidlcLayoutProbe(layout, tuple(spaces), len(records), tuple(versions))

    @staticmethod
    def _layout_warnings(layout: AidlcLayoutProbe,
                         harness_dirs: tuple[HarnessDir, ...]) -> list[PreflightFinding]:
        def version_file(h: HarnessDir) -> EvidenceRef:
            return EvidenceRef("file", f"{h.dir}/{TOOLS_DIRNAME}/{ENGINE_VERSION_FILE}")

        out: list[PreflightFinding] = []
        if layout.layout == "legacy":
            out.append(_warning("legacy_layout", {"path": C.LEGACY_STATE_REL},
                                EvidenceRef("file", C.LEGACY_STATE_REL)))
        out += [_warning("engine_version_unknown", {"dir": h.dir}, version_file(h))
                for h in harness_dirs if h.engine_version is None]
        versions = sorted({h.engine_version for h in harness_dirs if h.engine_version})
        if len(versions) > 1:
            out.append(_warning("mixed_harness_versions", {"versions": versions},
                                *(version_file(h) for h in harness_dirs)))
        return out

    def _duplicate_of(self, identity: ResolvedIdentity, *, exclude: str | None = None) -> str | None:
        """The ``repo_id`` already holding this identity (two spellings of one directory) or this path (a row
        whose identity could never be proven), if any."""
        target = os.path.normcase(identity.canonical_path)
        for row in self._storage.select("repos", order_by="added_at ASC, id ASC"):
            repo_id = str(row.get("id"))
            if exclude is not None and repo_id == exclude:
                continue
            stored = row.get("resolved_identity")
            if stored and stored == identity.identity_str:
                return repo_id
            if os.path.normcase(str(row.get("canonical_path") or "")) == target:
                return repo_id
        return None

    def _refuse_duplicate(self, identity: ResolvedIdentity, *, exclude: str | None = None) -> None:
        duplicate = self._duplicate_of(identity, exclude=exclude)
        if duplicate:
            raise StudioError("duplicate_identity", "another registered repository resolves to this identity",
                              details={"repo_id": duplicate, "identity": identity.identity_str})

    def _identity_elsewhere(self, rec: RepoRecord) -> str | None:
        """Another registered path whose live identity equals this record's stored one: a renamed
        directory keeps its inode, which is what tells ``moved`` from ``unavailable``."""
        if not rec.resolved_identity:
            return None
        for row in self._storage.select("repos"):
            repo_id = str(row.get("id"))
            other = Path(str(row.get("canonical_path") or ""))
            if repo_id == rec.repo_id or not other.is_dir():
                continue
            if self._identity_for(other, use_git=False).identity_str == rec.resolved_identity:
                return repo_id
        return None

    def _located_at(self, identity: ResolvedIdentity) -> dict[str, Any]:
        """The fields a registration takes from the directory it points at (``add`` and ``rebind``)."""
        provable = identity.provable
        unprovable = None if provable else "identity_unprovable"
        return dict(canonical_path=identity.canonical_path, git_common_dir_identity=identity.git_common_dir,
                    resolved_identity=identity.identity_str if provable else None, platform=sys.platform,
                    last_seen=self._clock.iso(), availability=unprovable or "available",
                    availability_detail=unprovable)

    @staticmethod
    def _with_install(rec: RepoRecord, health: InstallHealth) -> RepoRecord:
        return replace(rec, install_status=health.status, installed_engine_version=health.engine_version,
                       engine_dir=health.engine_dir, receipt_version=health.receipt_id or rec.receipt_version)
