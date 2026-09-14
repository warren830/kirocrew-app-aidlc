"""Which directories Studio watches, and whether AI-DLC is really installed in them.

The registry answers three questions, and each one is harder than it looks:

* **Is this the same repository as that one?** Not a path comparison. macOS APFS is case-insensitive,
  so ``/Users/x/Repo`` and ``/Users/x/repo`` are one directory with two spellings, and the prototype's
  path-hash id happily registered both (``07 §3.2``). Identity is therefore ``st_dev:st_ino`` — the
  filesystem's own answer — and only when the filesystem refuses to supply inode numbers does it fall
  back to ``sha256(realpath + NUL + git_common_dir)`` (FR-REP-003, architecture A13). A repository
  whose identity cannot be proven either way is still registerable, but observe-only: execution and
  install need a lease keyed by identity (FR-REP-005/006), and a lease on an unprovable key could be
  held twice for one directory. The *displayed* path keeps the user's typed case (A13/C36 D9); only
  symlinks are resolved.

* **Is it still there?** A moved repository must stay visible with a way out (FR-REP-009), so
  ``check_availability`` distinguishes "renamed or moved" (the identity turned up somewhere else, or
  its git common dir still exists) from "gone" and from "unreadable". Rebind is allowed only from
  those states: rebinding an *available* repo would silently repoint a healthy registration at a
  different directory, keeping its actions and bindings.

* **Is AI-DLC installed, and is the install intact?** From the bytes on disk plus Studio's own receipt,
  never by asking the engine. Preflight and ``detect_install`` execute **nothing** from the candidate
  repository (C35/review P13): repo-resident TypeScript is untrusted data (PRD §16.2), and a path the
  user has only just typed is the least trustworthy input the product has. This module starts no
  child process at all — the one external command it may cause is ``git rev-parse`` in the injected
  ``GitObserver``, and preflight does not even do that unless the caller opts in with
  ``probe_tools=True``.

Nothing here writes into a repository, ever: ``remove`` unregisters a row and leaves the tree byte for
byte as it was (FR-REP-007). The registry also never scans a home or parent directory — a repository
enters only through an explicit absolute path (FR-REP-002).

Threading: sync and blocking (``os.stat``, bounded file reads, one hash pass over receipt-owned
files). Callers reach it through ``await asyncio.to_thread(...)`` like every other disk module (§0.6).
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from dataclasses import dataclass
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

# --------------------------------------------------------------------------- #
# on-disk layout of an AI-DLC harness
# --------------------------------------------------------------------------- #

TOOLS_DIRNAME = "tools"
ENGINE_VERSION_FILE = "aidlc-version.ts"
ENGINE_LIB_FILE = "aidlc-lib.ts"
ENGINE_UTILITY_FILE = "aidlc-utility.ts"
HARNESS_JSON_REL = "data/harness.json"
STAGE_GRAPH_REL = "data/stage-graph.json"

#: A ``<harness>/tools`` directory counts as an AI-DLC install only when one of these is inside it.
#: A user's own ``.cursor/tools`` folder is not an engine, and calling it one would offer an upgrade
#: that overwrites their files.
HARNESS_MARKERS = (ENGINE_VERSION_FILE, HARNESS_JSON_REL, ENGINE_UTILITY_FILE)

#: Paths the installer writes or merges. A symlink at any of them means an ``os.replace`` would write
#: through the link, outside the repository — the installer refuses such a target, so preflight
#: reports it before the user gets that far. Repo-relative posix, deliberately shallow and bounded.
MANAGED_PATH_PROBES = (
    *C.HARNESS_DIRS,
    f"{C.STUDIO_HARNESS_DIR}/tools",
    f"{C.STUDIO_HARNESS_DIR}/settings",
    f"{C.STUDIO_HARNESS_DIR}/steering",
    f"{C.STUDIO_HARNESS_DIR}/scopes",
    f"{C.STUDIO_HARNESS_DIR}/agents",
    f"{C.STUDIO_HARNESS_DIR}/hooks",
    "aidlc",
    "AGENTS.md",
    ".gitignore",
)

#: The only availability values a rebind may start from (§1.5). ``identity_unprovable`` is not one of
#: them: nothing is known to have moved, and the fix is to resolve the ambiguity, not to repoint.
REBINDABLE_AVAILABILITY = ("moved", "unavailable", "permission_denied")

#: ``availability_detail`` values. Reason codes rather than English prose, because the UI localises
#: them (C11) and a prose sentence would ship untranslated.
AVAILABILITY_REASONS = (
    "path_missing",
    "not_a_directory",
    "stat_failed",
    "permission_denied",
    "identity_changed",
    "identity_unprovable",
    "identity_at_other_repo",
    "git_common_dir_present",
)

#: Preflight warning codes (§1.5) with the severity each carries (§1.7 vocabulary).
PREFLIGHT_WARNING_SEVERITY = {
    "legacy_layout": "warn",
    "engine_version_unknown": "warn",
    "mixed_harness_versions": "warn",
    "bun_missing": "warn",
    "git_missing": "info",
}

#: ``- **State Version**: 7`` inside the older engines' state *template* in ``aidlc-utility.ts``, the
#: only place a pre-2.5 build records the state version it writes. The same line the reader looks for in
#: a record, so both read one pattern (``constants.TEMPLATE_STATE_VERSION_RE``).
_STATE_VERSION_FIELD_RE = C.TEMPLATE_STATE_VERSION_RE


#: ``detect_install`` is handed a receipt by whoever has one: the ``Installer``'s ``Receipt`` dataclass,
#: or the raw ``install_receipts`` row this module can fetch itself. Both carry the same field names, so
#: the shared structural accessor (``constants.attr``) keeps the drift logic from caring which arrived.
_attr = C.attr


def _receipt_entries(receipt: Any) -> Any:
    """The receipt's file list under either of its two spellings.

    The ``Installer``'s ``Receipt`` dataclass calls it ``files``; the ``install_receipts`` row this
    module fetches for itself calls it ``files_json``. Accepting both is what lets ``detect_install``
    be handed whichever one the caller happens to hold.
    """
    entries = _attr(receipt, "files")
    if entries is None:
        entries = _attr(receipt, "files_json")
    return entries or ()


def _json_list(values: Any) -> list[Any]:
    """Serialise a sequence whose members may be dataclasses with ``to_json`` or plain JSON."""
    out: list[Any] = []
    for value in values or ():
        to_json = getattr(value, "to_json", None)
        out.append(to_json() if callable(to_json) else value)
    return out


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    """What the filesystem says about one directory, and whether that is provable."""

    canonical_path: str
    st_dev: int | None
    st_ino: int | None
    git_common_dir: str | None
    identity_str: str
    provable: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "canonical_path": self.canonical_path,
            "st_dev": self.st_dev,
            "st_ino": self.st_ino,
            "git_common_dir": self.git_common_dir,
            "identity_str": self.identity_str,
            "provable": self.provable,
        }


@dataclass(frozen=True, slots=True)
class HarnessDir:
    """One ``<dir>/tools`` engine tree found in a repository, read from its own files."""

    dir: str
    harness_name: str | None
    rules_subdir: str | None
    engine_version: str | None
    engine_state_version: int | None
    stage_count: int | None
    has_utility: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "dir": self.dir,
            "harness_name": self.harness_name,
            "rules_subdir": self.rules_subdir,
            "engine_version": self.engine_version,
            "engine_state_version": self.engine_state_version,
            "stage_count": self.stage_count,
            "has_utility": self.has_utility,
        }


@dataclass(frozen=True, slots=True)
class ReceiptSummary:
    """The receipt fields every read model needs, without its file list.

    Defined here rather than in ``installer.py`` because preflight and ``RepoRecord`` need it and the
    registry sits below the installer; the installer builds the same shape from its ``Receipt``.
    """

    receipt_id: str
    engine_version: str
    studio_version: str
    committed_at: str
    status: str
    files: int

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "ReceiptSummary":
        files = row.get("files_json") or []
        return cls(
            receipt_id=str(row.get("receipt_id") or ""),
            engine_version=str(row.get("engine_version") or ""),
            studio_version=str(row.get("studio_version") or ""),
            committed_at=str(row.get("committed_at") or ""),
            status=str(row.get("status") or ""),
            files=len(files) if isinstance(files, (list, tuple)) else 0,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "engine_version": self.engine_version,
            "studio_version": self.studio_version,
            "committed_at": self.committed_at,
            "status": self.status,
            "files": self.files,
        }


@dataclass(frozen=True, slots=True)
class InstallHealth:
    """Whether AI-DLC is installed here, from disk bytes plus Studio's own receipt."""

    status: str
    engine_dir: str | None
    engine_version: str | None
    engine_state_version: int | None
    stage_count: int | None
    receipt_id: str | None
    receipt_engine_version: str | None
    drift_count: int
    harness_dirs: tuple[HarnessDir, ...]
    #: The engine version of the harness Studio MANAGES (``C.STUDIO_HARNESS_DIR``), or ``None`` when
    #: that directory is not there. Distinct from ``engine_dir``/``engine_version``, which name the
    #: harness Studio READS (§1.5): a foreign harness is perfectly fine to read, and reading it is how
    #: Studio drives a repository whose AI-DLC lives under ``.claude``. Only the install lane may key
    #: on this field — "is there an installation of *ours* to upgrade, or should we offer to add one?"
    #: — because keying that question on ``engine_version`` refuses both ``install``
    #: (already_installed) and ``upgrade`` (same_version_installed) for such a repository, leaving no
    #: path to the bundled payload at all (FR-INST-003).
    own_engine_version: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "engine_dir": self.engine_dir,
            "engine_version": self.engine_version,
            "own_engine_version": self.own_engine_version,
            "engine_state_version": self.engine_state_version,
            "stage_count": self.stage_count,
            "receipt_id": self.receipt_id,
            "receipt_engine_version": self.receipt_engine_version,
            "drift_count": self.drift_count,
            "harness_dirs": [h.to_json() for h in self.harness_dirs],
        }


@dataclass(frozen=True, slots=True)
class GitPreflight:
    """The git facts a preflight shows. Filled from ``GitObserver.observe``; never a write verb."""

    is_repo: bool
    branch: str | None
    dirty: bool
    common_dir: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "is_repo": self.is_repo,
            "branch": self.branch,
            "dirty": self.dirty,
            "common_dir": self.common_dir,
        }


@dataclass(frozen=True, slots=True)
class ToolProbe:
    """Where an external tool is. ``version`` is filled only by an injected prober.

    ``source`` and ``searched`` are ``engine.find_bun``'s answer, and they are on the wire because PATH
    alone cannot find ``bun`` under launchd: without the list of locations that were tried, a preflight
    can only repeat the advice that was wrong in the first place ("bun is not installed") at a user who
    has it. Both default to the "nobody searched" values so a probe of a tool with no candidate list —
    every tool but ``bun`` — stays a three-field answer.
    """

    found: bool
    path: str | None
    version: str | None
    source: str | None = None
    searched: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "found": self.found,
            "path": self.path,
            "version": self.version,
            "source": self.source,
            "searched": list(self.searched),
        }


@dataclass(frozen=True, slots=True)
class AidlcLayoutProbe:
    """Which AI-DLC workspace layout the repository has, and how much is in it."""

    layout: str | None
    spaces: tuple[str, ...]
    intents: int
    state_versions: tuple[int, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "layout": self.layout,
            "spaces": list(self.spaces),
            "intents": self.intents,
            "state_versions": list(self.state_versions),
        }


@dataclass(frozen=True, slots=True)
class PreflightFinding:
    """A preflight warning in the exact JSON shape of a ``consistency.Finding``.

    ``consistency.py`` owns ``Finding`` and depends on this module for ``RepoRecord``/``InstallHealth``
    (§1.7 ``RepoFacts``), so importing it here would close an import cycle. Same field names, same
    ``to_json``, same ``EvidenceRef`` type — a consumer cannot tell the two apart on the wire, which
    is all §1.5's ``warnings: list[Finding]`` needs. Mirrors the precedent of ``EvidenceRef`` living
    in ``storage.py`` rather than in the layer above it.
    """

    code: str
    severity: str
    message_key: str
    params: dict[str, Any]
    evidence: tuple[EvidenceRef, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message_key": self.message_key,
            "params": dict(self.params),
            "evidence": [e.to_json() for e in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class PreflightReport:
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

    def to_json(self) -> dict[str, Any]:
        return {
            "path_input": self.path_input,
            "canonical_path": self.canonical_path,
            "identity": self.identity.to_json() if self.identity else None,
            "is_directory": self.is_directory,
            "sensitive": self.sensitive,
            "duplicate_of": self.duplicate_of,
            "platform": self.platform,
            "git": self.git.to_json() if self.git else None,
            "bun": self.bun.to_json() if self.bun else None,
            "harness_dirs": [h.to_json() for h in self.harness_dirs],
            "aidlc": self.aidlc.to_json(),
            "symlinks_at_managed_paths": list(self.symlinks_at_managed_paths),
            "free_space_bytes": self.free_space_bytes,
            "writable": self.writable,
            "existing_receipt": self.existing_receipt.to_json() if self.existing_receipt else None,
            "warnings": [w.to_json() for w in self.warnings],
            "can_register": self.can_register,
            "can_install": self.can_install,
        }


@dataclass(frozen=True, slots=True)
class RepoRecord:
    """One ``repos`` row. ``repo_id`` outlives every other field, including the identity (C01)."""

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
    legacy_console_id: str | None
    install_status: str
    installed_engine_version: str | None
    engine_dir: str | None
    receipt_version: str | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "RepoRecord":
        return cls(
            repo_id=str(row["id"]),
            label=str(row.get("label") or ""),
            canonical_path=str(row.get("canonical_path") or ""),
            resolved_identity=row.get("resolved_identity"),
            git_common_dir_identity=row.get("git_common_dir_identity"),
            platform=str(row.get("platform") or sys.platform),
            added_at=str(row.get("added_at") or ""),
            last_seen=row.get("last_seen"),
            availability=str(row.get("availability") or "available"),
            availability_detail=row.get("availability_detail"),
            archived=bool(row.get("archived")),
            legacy_console_id=row.get("legacy_console_id"),
            install_status=str(row.get("install_status") or "not_installed"),
            installed_engine_version=row.get("installed_engine_version"),
            engine_dir=row.get("engine_dir"),
            receipt_version=row.get("receipt_version"),
        )

    def to_json(self) -> dict[str, Any]:
        """The record's own fields.

        §2.2's ``RepoRecord`` JSON additionally carries ``install`` (composed from ``InstallHealth``
        plus the payload manifest), ``counts``, ``leases``, ``git``, ``findings`` and ``scanned_at``;
        those come from modules that own that data, so the handler assembles them around this dict.
        """
        return {
            "repo_id": self.repo_id,
            "label": self.label,
            "canonical_path": self.canonical_path,
            "resolved_identity": self.resolved_identity,
            "git_common_dir_identity": self.git_common_dir_identity,
            "platform": self.platform,
            "added_at": self.added_at,
            "last_seen": self.last_seen,
            "availability": self.availability,
            "availability_detail": self.availability_detail,
            "archived": self.archived,
            "legacy_console_id": self.legacy_console_id,
            "install_status": self.install_status,
            "installed_engine_version": self.installed_engine_version,
            "engine_dir": self.engine_dir,
            "receipt_version": self.receipt_version,
        }


@dataclass(frozen=True, slots=True)
class RepoScan:
    """The result of looking at one repository once.

    ``intents`` and ``findings`` are ``list[IntentSummary]`` / ``list[Finding]`` when the reconciler
    fills them in (§1.13); they are typed loosely here because ``projection`` and ``consistency`` sit
    above this module. ``RepoRegistry.rescan`` produces the repo-level half — availability and install
    health — which is what the registry can establish on its own.
    """

    repo_id: str
    availability: str
    install: InstallHealth
    intents: tuple[Any, ...]
    findings: tuple[Any, ...]
    took_ms: int
    truncated: bool
    scanned_at: str

    def to_json(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "availability": self.availability,
            "install": self.install.to_json(),
            "intents": _json_list(self.intents),
            "findings": _json_list(self.findings),
            "took_ms": self.took_ms,
            "truncated": self.truncated,
            "scanned_at": self.scanned_at,
        }


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #


class RepoRegistry:
    """Owns the ``repos`` table: identity, availability and install health.

    Deliberately not given an ``EngineRunner``: no AI-DLC verb may run during ``preflight``, ``add``,
    ``check_availability`` or ``detect_install`` (C35/review P13), and the cheapest way to guarantee
    that is to make it impossible. ``bun_version`` exists for the one probe that legitimately needs a
    child process (``bun --version``): the caller injects it from the module that owns child processes, so
    this module can be grepped clean of them (architecture §10).
    """

    def __init__(
        self,
        storage: Storage,
        git: "GitObserver",
        clock: "Clock",
        ids: "IdFactory",
        *,
        bun_version: Callable[[str], str | None] | None = None,
    ) -> None:
        self._storage = storage
        self._git = git
        self._clock = clock
        self._ids = ids
        self._bun_version = bun_version
        self._bun_location = None

    def set_bun_location(self, location: Any) -> None:
        """Use the same validated interpreter selection as the application's engine runner."""
        self._bun_location = location

    # ---- identity ----

    def resolve_identity(self, path: str) -> ResolvedIdentity:
        """Identity of a user-supplied path, validated first.

        Raises ``bad_path`` (relative, missing, not a directory, NUL) and ``sensitive_path``; the
        validation lives in ``security`` so every door into the filesystem uses one rule.
        """
        root = security.validate_repo_root(path)
        return self._identity_for(root)

    def require_current_identity(self, repo: RepoRecord) -> ResolvedIdentity:
        """Revalidate the directory at the write boundary, including a concurrent rebind."""
        current = self.get(repo.repo_id)
        identity = self.resolve_identity(repo.canonical_path)
        if (current is None or current.canonical_path != repo.canonical_path
                or current.resolved_identity != repo.resolved_identity
                or not identity.provable or identity.identity_str != repo.resolved_identity):
            raise StudioError("repo_unavailable", "the registered repository identity changed",
                              details={"repo_id": repo.repo_id, "reason": "identity_changed"})
        return identity

    # ---- preflight ----

    def preflight(self, path: str, *, probe_tools: bool = False) -> PreflightReport:
        """Read-only report on a candidate directory. Never raises for a broken repository.

        A directory that is protected, is not a directory at all, or is already registered is
        *reported* (``sensitive``, ``is_directory``, ``duplicate_of``, ``can_register``) rather than
        refused, because the point of a preflight is to explain the problem before the user commits to
        it; only an input that cannot be canonicalised at all (empty, NUL-bearing, relative) raises
        ``bad_path``. ``add`` applies the refusals.

        ``probe_tools`` gates every child process: with the default ``False`` this call spawns nothing —
        no ``git rev-parse``, no ``bun --version`` — so an unattended caller can run it on an
        arbitrary path without executing anything, and a test can prove it (C35/review P13).
        """
        text = (path or "").strip()
        if not text or "\x00" in text:
            raise StudioError("bad_path", "path is required")
        expanded = Path(text).expanduser()
        if not expanded.is_absolute():
            raise StudioError("bad_path", "path must be absolute")

        root = security.realpath(expanded)
        canonical = str(root)
        is_directory = root.is_dir()
        sensitive = security.is_sensitive(root)
        readable = is_directory and not sensitive

        identity: ResolvedIdentity | None = None
        duplicate_of: str | None = None
        harness_dirs: tuple[HarnessDir, ...] = ()
        layout = AidlcLayoutProbe(None, (), 0, ())
        symlinks: tuple[str, ...] = ()
        free_space: int | None = None
        writable: bool | None = None
        git: GitPreflight | None = None
        git_reason: str | None = None
        receipt: ReceiptSummary | None = None
        warnings: list[PreflightFinding] = []

        if readable:
            identity = self._identity_for(root, use_git=probe_tools)
            duplicate_of = self._duplicate_of(identity)
            harness_dirs = self._harness_dirs(root)
            layout = self._layout_probe(root)
            symlinks = self._symlinks_at_managed_paths(root)
            free_space = self._free_space(root)
            writable = os.access(root, os.W_OK)
            if duplicate_of:
                receipt = self.current_receipt_summary(duplicate_of)
            if probe_tools:
                git, git_reason = self._git_preflight(root)
            warnings.extend(self._layout_warnings(layout, harness_dirs))

        bun = self._bun_probe() if probe_tools else None
        if bun is not None and not bun.found:
            # The locations travel with the finding: it is stored and exported, and "not installed" is
            # only true if the reader can see where Studio looked.
            warnings.append(
                self._warning(
                    "bun_missing", {"locations": list(bun.searched)}, (EvidenceRef("studio", "bun"),)
                )
            )
        if git_reason == "git_missing":
            warnings.append(self._warning("git_missing", {}, (EvidenceRef("studio", "git"),)))

        can_register = readable and duplicate_of is None
        can_install = (
            readable
            and bool(writable)
            and not symlinks
            and identity is not None
            and identity.provable
        )
        return PreflightReport(
            path_input=text,
            canonical_path=canonical,
            identity=identity,
            is_directory=is_directory,
            sensitive=sensitive,
            duplicate_of=duplicate_of,
            platform=sys.platform,
            git=git,
            bun=bun,
            harness_dirs=harness_dirs,
            aidlc=layout,
            symlinks_at_managed_paths=symlinks,
            free_space_bytes=free_space,
            writable=writable,
            existing_receipt=receipt,
            warnings=tuple(warnings),
            can_register=can_register,
            can_install=can_install,
        )

    # ---- registry mutations ----

    def add(self, path: str, label: str | None = None) -> RepoRecord:
        """Register a directory. Nothing is stored unless every check passes.

        Order matters: the path is validated (``bad_path``/``sensitive_path``) before the database is
        touched, then the identity is compared against every existing row (``duplicate_identity`` with
        ``details.repo_id`` so the UI can point at the row that already owns it), and only then is the
        cap enforced. An unprovable identity is stored with ``resolved_identity = NULL`` and
        ``availability = 'identity_unprovable'``: visible and readable, but no lease can be keyed on it
        so execution and install stay disabled (FR-REP-005).
        """
        root = security.validate_repo_root(path)
        identity = self._identity_for(root)
        duplicate = self._duplicate_of(identity)
        if duplicate:
            raise StudioError(
                "duplicate_identity",
                "another registered repository resolves to this identity",
                details={"repo_id": duplicate, "identity": identity.identity_str},
            )
        rows = self._storage.select("repos")
        if len(rows) >= C.MAX_REPOS:
            raise StudioError(
                "too_many_repos",
                "the repository registry is full",
                details={"max": C.MAX_REPOS, "count": len(rows)},
            )

        provable = identity.provable
        record = RepoRecord(
            repo_id=self._ids.new("r"),
            label=self._label(label, root),
            canonical_path=identity.canonical_path,
            resolved_identity=identity.identity_str if provable else None,
            git_common_dir_identity=identity.git_common_dir,
            platform=sys.platform,
            added_at=self._clock.iso(),
            last_seen=self._clock.iso(),
            availability="available" if provable else "identity_unprovable",
            availability_detail=None if provable else "identity_unprovable",
            archived=False,
            legacy_console_id=None,
            install_status="not_installed",
            installed_engine_version=None,
            engine_dir=None,
            receipt_version=None,
        )
        health = self.detect_install(record)
        record = self._with_install(record, health)
        self._storage.insert("repos", self._row(record))
        return record

    def remove(self, repo_id: str) -> None:
        """Unregister a repository. Never touches disk (FR-REP-007).

        Bindings and actions go with it through ``ON DELETE CASCADE`` — orphans would be resurrected
        as live cards for a repository the user deleted. Lease rows are keyed by identity and are
        left alone on purpose: they are not this row's to delete, and ``leases.py`` reclaims a dead
        one with proof rather than on age.
        """
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
        """Point an unavailable registration at the directory it moved to, keeping ``repo_id``.

        Availability is recomputed first rather than trusted from the row: rebinding a repository that
        is actually fine would silently move a healthy registration — with its bindings, actions and
        history — onto a different directory. The freshly computed state is persisted either way, so a
        refusal also repairs a stale ``moved`` badge.
        """
        record = self.get(repo_id)
        availability, detail = self.check_availability(record)
        if availability not in REBINDABLE_AVAILABILITY:
            self._persist_availability(record, availability, detail)
            raise StudioError(
                "rebind_not_allowed",
                "only a moved, unavailable or unreadable repository can be rebound",
                details={"availability": availability, "repo_id": repo_id},
            )
        identity = self.resolve_identity(new_path)
        duplicate = self._duplicate_of(identity, exclude=repo_id)
        if duplicate:
            raise StudioError(
                "duplicate_identity",
                "another registered repository resolves to this identity",
                details={"repo_id": duplicate, "identity": identity.identity_str},
            )
        provable = identity.provable
        moved = RepoRecord(
            repo_id=record.repo_id,
            label=record.label,
            canonical_path=identity.canonical_path,
            resolved_identity=identity.identity_str if provable else None,
            git_common_dir_identity=identity.git_common_dir,
            platform=sys.platform,
            added_at=record.added_at,
            last_seen=self._clock.iso(),
            availability="available" if provable else "identity_unprovable",
            availability_detail=None if provable else "identity_unprovable",
            archived=record.archived,
            legacy_console_id=record.legacy_console_id,
            install_status=record.install_status,
            installed_engine_version=record.installed_engine_version,
            engine_dir=record.engine_dir,
            receipt_version=record.receipt_version,
        )
        moved = self._with_install(moved, self.detect_install(moved, self.current_receipt_row(repo_id)))
        self._storage.update(
            "repos",
            repo_id,
            {
                "canonical_path": moved.canonical_path,
                "resolved_identity": moved.resolved_identity,
                "git_common_dir_identity": moved.git_common_dir_identity,
                "platform": moved.platform,
                "last_seen": moved.last_seen,
                "availability": moved.availability,
                "availability_detail": moved.availability_detail,
                "install_status": moved.install_status,
                "installed_engine_version": moved.installed_engine_version,
                "engine_dir": moved.engine_dir,
            },
        )
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

    def touch_seen(self, repo_id: str) -> None:
        """Stamp ``last_seen``. Silent when the row is gone — a scan loop must not raise on a race."""
        self._storage.update("repos", repo_id, {"last_seen": self._clock.iso()})

    def check_availability(self, rec: RepoRecord) -> tuple[str, str | None]:
        """``(availability, reason_code)`` from a fresh stat plus an identity recompute. Never raises.

        The distinction that matters is *moved* versus *unavailable*: a renamed directory keeps its
        inode, so its identity turns up under another registered path or its git common dir is still
        on disk, and the user can rebind. Nothing found means the directory is really gone and the
        record can only be removed (FR-REP-009).
        """
        root = Path(rec.canonical_path)
        try:
            st = os.stat(root)
        except PermissionError:
            return "permission_denied", "permission_denied"
        except (FileNotFoundError, NotADirectoryError):
            elsewhere = self._identity_elsewhere(rec)
            if elsewhere:
                return "moved", "identity_at_other_repo"
            common = rec.git_common_dir_identity
            if common and Path(common).exists():
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

    def detect_install(
        self, rec: RepoRecord, receipt: "Receipt | Mapping[str, Any] | None" = None
    ) -> InstallHealth:
        """Install status from the repository's own bytes plus Studio's receipt. Runs no engine verb.

        Rules (§1.5): no harness directory with a ``tools/`` tree → ``not_installed``; a stored
        ``recovery_required`` stays (a failed rollback left the tree in a state only an explicit
        recovery may clear, FR-INST-013); a receipt whose owned files no longer hash to their recorded
        digest → ``drift``; otherwise ``installed``. ``status`` answers "can this repository host
        AI-DLC work at all", and ANY harness answers it: a repository whose AI-DLC lives under
        ``.claude`` is ``installed``, with ``engine_dir``/``engine_version`` naming that harness,
        because those are the bytes every read and every dispatch here goes through.

        ``own_engine_version`` is the separate fact the install lane needs: the engine version under
        ``C.STUDIO_HARNESS_DIR``, or ``None`` when Studio's own harness is simply not there yet. It is
        deliberately not folded into ``status`` — ``not_installed`` refuses intent creation and blocks
        the repository in the new-intent wizard, which would take away the ``.claude`` repository
        Studio can drive today just to make the Install button appear.

        An unreadable root is the one case not covered by those rules, and answering
        ``not_installed`` there would be a lie with consequences — the UI would offer to install into
        a repository that is merely on an unmounted volume. The stored status and engine version are
        returned instead, with no harness directories, which is exactly "we cannot see it right now";
        the stored version counts as ours only when the stored ``engine_dir`` says it was.
        """
        root = Path(rec.canonical_path)
        receipt = receipt if receipt is not None else self.current_receipt_row(rec.repo_id)
        receipt_id = _attr(receipt, "receipt_id") if receipt is not None else None
        receipt_engine = _attr(receipt, "engine_version") if receipt is not None else None
        if not root.is_dir():
            return InstallHealth(
                status=rec.install_status,
                engine_dir=rec.engine_dir,
                engine_version=rec.installed_engine_version,
                engine_state_version=None,
                stage_count=None,
                receipt_id=receipt_id,
                receipt_engine_version=receipt_engine,
                drift_count=0,
                harness_dirs=(),
                own_engine_version=(
                    rec.installed_engine_version
                    if rec.engine_dir == C.STUDIO_HARNESS_DIR
                    else None
                ),
            )

        harness_dirs = self._harness_dirs(root)
        engine = self._engine_harness(harness_dirs)
        own = next((h for h in harness_dirs if h.dir == C.STUDIO_HARNESS_DIR), None)
        drift_count = self._drift_count(root, receipt) if receipt is not None else 0
        if not harness_dirs:
            status = "not_installed"
        elif rec.install_status == "recovery_required":
            status = "recovery_required"
        elif receipt is not None and drift_count:
            status = "drift"
        else:
            status = "installed"
        return InstallHealth(
            status=status,
            engine_dir=engine.dir if engine else None,
            engine_version=engine.engine_version if engine else None,
            engine_state_version=engine.engine_state_version if engine else None,
            stage_count=engine.stage_count if engine else None,
            receipt_id=receipt_id,
            receipt_engine_version=receipt_engine,
            drift_count=drift_count,
            harness_dirs=harness_dirs,
            own_engine_version=own.engine_version if own else None,
        )

    def rescan(self, repo_id: str) -> RepoScan:
        """Re-establish availability and install health for one repository and persist the result.

        The repo-level half of a scan; the reconciler adds intents and findings on top (§1.13) using
        the same ``RepoScan`` shape. ``last_seen`` moves only when the repository was actually
        readable, so "when did Studio last see it" keeps meaning that.
        """
        record = self.get(repo_id)
        started = self._clock.monotonic()
        availability, detail = self.check_availability(record)
        receipt = self.current_receipt_row(repo_id)
        health = self.detect_install(record, receipt)
        scanned_at = self._clock.iso()
        scan = RepoScan(
            repo_id=repo_id,
            availability=availability,
            install=health,
            intents=(),
            findings=(),
            took_ms=max(0, int((self._clock.monotonic() - started) * 1000)),
            truncated=False,
            scanned_at=scanned_at,
        )
        values: dict[str, Any] = {
            "availability": availability,
            "availability_detail": detail,
            "install_status": health.status,
            "installed_engine_version": health.engine_version,
            "engine_dir": health.engine_dir,
            "receipt_version": health.receipt_id,
            "scan_json": scan.to_json(),
        }
        if availability == "available":
            values["last_seen"] = scanned_at
        self._storage.update("repos", repo_id, values)
        return scan

    # ---- receipts ----

    def current_receipt_row(self, repo_id: str) -> dict[str, Any] | None:
        """The current receipt row, or ``None``. Storage read only — no file hashing."""
        rows = self._storage.select(
            "install_receipts", {"repo_id": repo_id, "status": "current"}, limit=1
        )
        return rows[0] if rows else None

    def current_receipt_summary(self, repo_id: str) -> ReceiptSummary | None:
        row = self.current_receipt_row(repo_id)
        return ReceiptSummary.from_row(row) if row else None

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    def _identity_for(self, root: Path, *, use_git: bool = True) -> ResolvedIdentity:
        """Identity of an already-resolved directory. Never raises, never writes.

        ``st_ino == 0`` counts as "no inode numbers here": some network and emulated filesystems
        report zero for every entry, and trusting that would collapse every repository on the mount
        into one identity — the exact confusion the fallback digest exists to avoid.
        """
        canonical = str(root)
        st_dev: int | None = None
        st_ino: int | None = None
        try:
            st = os.stat(root)
        except OSError:
            st = None
        if st is not None and st.st_ino:
            st_dev, st_ino = int(st.st_dev), int(st.st_ino)
        common = self._common_dir(root) if use_git else None
        if st_dev is not None and st_ino is not None:
            identity_str = f"{st_dev}:{st_ino}"
        else:
            identity_str = security.sha256_text(canonical + "\0" + (common or ""))
        provable = (st_dev is not None and st_ino is not None) or common is not None
        return ResolvedIdentity(canonical, st_dev, st_ino, common, identity_str, provable)

    def _common_dir(self, root: Path) -> str | None:
        """``git rev-parse --git-common-dir`` as a realpath, or ``None``.

        Wrapped because identity must not depend on git's health: a git binary that is missing, is
        wedged, or belongs to a repository Studio cannot read must degrade to "no git identity", not
        fail the registration.
        """
        try:
            value = self._git.common_dir(root)
        except Exception:  # noqa: BLE001 - a broken observer may never break identity resolution
            return None
        if not value:
            return None
        return str(security.realpath(str(value)))

    def _git_preflight(self, root: Path) -> tuple[GitPreflight | None, str | None]:
        try:
            obs = self._git.observe(root)
        except Exception:  # noqa: BLE001 - FR-GIT-005: an observation failure is data, not an error
            return None, "error"
        available = bool(_attr(obs, "available", False))
        reason = _attr(obs, "reason")
        common = self._common_dir(root) if available else None
        return (
            GitPreflight(
                is_repo=available,
                branch=_attr(obs, "branch"),
                dirty=bool(_attr(obs, "dirty", False)),
                common_dir=common,
            ),
            reason,
        )

    def _bun_probe(self) -> ToolProbe:
        """Where ``bun`` is, how it was found, and (only via the injected prober) which version.

        No longer a bare PATH search. The desktop gateway is started by launchd with a four-directory
        PATH that no bun installer ever writes to, so a preflight that read PATH alone warned
        ``bun_missing`` at users who had bun installed; ``engine.find_bun`` carries the measurement and
        the search order. This module still starts no child process, because the one command a preflight
        may legitimately run (``bun --version``, never anything from the candidate repository — C35) is
        the injected prober, which is also what the finder uses as its proof that a candidate really is
        bun. With no prober injected the finder falls back to its own, i.e. the spawn still happens at
        ``engine.py``'s door; production always injects (``Services.build``).
        """
        located = self._bun_location or find_bun(probe=self._bun_version)
        return ToolProbe(
            located.found, located.path, located.version, located.source, located.searched
        )

    def _harness_dirs(self, root: Path) -> tuple[HarnessDir, ...]:
        found = [self._harness_dir(root, name) for name in C.HARNESS_DIRS]
        return tuple(h for h in found if h is not None)

    def _harness_dir(self, root: Path, name: str) -> HarnessDir | None:
        try:
            tools = security.resolve_inside(root, f"{name}/{TOOLS_DIRNAME}")
        except StudioError:
            return None
        if not tools.is_dir():
            return None
        if not any((tools / marker).exists() for marker in HARNESS_MARKERS):
            return None
        harness = self._read_json(tools / HARNESS_JSON_REL, C.MAX_JSON_BYTES)
        harness = harness if isinstance(harness, dict) else {}
        version_text = security.bounded_text(tools / ENGINE_VERSION_FILE, C.MAX_ARTIFACT_RENDER_BYTES)
        engine_version = None
        if version_text:
            match = C.ENGINE_VERSION_RE.search(version_text)
            engine_version = match.group(1) if match else None
        return HarnessDir(
            dir=name,
            harness_name=harness.get("name") or None,
            rules_subdir=harness.get("rulesSubdir") or None,
            engine_version=engine_version,
            engine_state_version=self._engine_state_version(tools),
            stage_count=self._stage_count(tools),
            has_utility=security.is_regular_file(tools / ENGINE_UTILITY_FILE),
        )

    def _engine_state_version(self, tools: Path) -> int | None:
        """The state version the installed engine writes.

        ``CURRENT_STATE_VERSION`` in ``aidlc-lib.ts`` is authoritative from 2.5 on; older builds only
        carry the number inside the state *template* in ``aidlc-utility.ts``, so that is the fallback.
        Neither present (the fixture trees ship data files only) → unknown, never a guess: a wrong
        answer here would make ``state_version_unsupported`` fire against a perfectly good repository.
        """
        lib = security.bounded_text(tools / ENGINE_LIB_FILE, C.MAX_ARTIFACT_RENDER_BYTES)
        if lib:
            match = C.ENGINE_STATE_VERSION_RE.search(lib)
            if match:
                return int(match.group(1))
        utility = security.bounded_text(tools / ENGINE_UTILITY_FILE, C.MAX_ARTIFACT_RENDER_BYTES)
        if utility:
            match = _STATE_VERSION_FIELD_RE.search(utility)
            if match:
                return int(match.group(1))
        return None

    def _stage_count(self, tools: Path) -> int | None:
        graph = self._read_json(tools / STAGE_GRAPH_REL, C.MAX_JSON_BYTES)
        if isinstance(graph, list):
            return len(graph)
        if isinstance(graph, dict) and isinstance(graph.get("stages"), list):
            return len(graph["stages"])
        return None

    @staticmethod
    def _engine_harness(harness_dirs: tuple[HarnessDir, ...]) -> HarnessDir | None:
        """The harness Studio drives: ``.kiro`` when present, else the first one found.

        A read-and-dispatch choice only. "Is there a harness of *ours* here?" is a different question
        with a different answer, and it lives in ``InstallHealth.own_engine_version``.
        """
        for harness in harness_dirs:
            if harness.dir == C.STUDIO_HARNESS_DIR:
                return harness
        return harness_dirs[0] if harness_dirs else None

    def _drift_count(self, root: Path, receipt: Any) -> int:
        """How many receipt-owned files no longer hash to their recorded digest.

        Only ``kind == "file"`` entries: a fragment's canonical digest depends on the merge strategy
        that produced it, which is the installer's knowledge (§1.14), and guessing here would report
        drift on a merge target the user is perfectly entitled to have edited around.
        """
        count = 0
        for entry in _receipt_entries(receipt):
            if str(_attr(entry, "kind", "file")) != "file":
                continue
            expected = _attr(entry, "sha256")
            rel = _attr(entry, "path")
            if not expected or not rel:
                continue
            try:
                live = security.sha256_file(security.resolve_inside(root, str(rel)))
            except StudioError:
                count += 1  # a receipt path that no longer resolves inside the repo is drift
                continue
            if live != expected:
                count += 1
        return count

    def _layout_probe(self, root: Path) -> AidlcLayoutProbe:
        """Which AI-DLC workspace layout is on disk, plus the state versions in it.

        ``legacy`` follows the engine's own ``needsFlatMigration`` test (``04 §5``): a flat
        ``aidlc-docs/aidlc-state.md`` and no spaces record anywhere. Reads are bounded and the number
        of records is capped, so a repository with thousands of directories cannot stall a preflight.
        """
        spaces: list[str] = []
        records: list[Path] = []
        spaces_root = root / "aidlc" / "spaces"
        if spaces_root.is_dir():
            for entry in sorted(spaces_root.iterdir()):
                if entry.is_symlink() or not entry.is_dir() or not C.SPACE_RE.match(entry.name):
                    continue
                spaces.append(entry.name)
                intents = entry / "intents"
                if not intents.is_dir():
                    continue
                for record in sorted(intents.iterdir()):
                    if len(records) >= C.MAX_INTENTS_PER_REPO:
                        break
                    if record.is_symlink() or not record.is_dir():
                        continue
                    if security.is_regular_file(record / "aidlc-state.md"):
                        records.append(record / "aidlc-state.md")

        legacy_state = root / C.LEGACY_STATE_REL
        layout: str | None = None
        if records or spaces:
            layout = "spaces"
        elif security.is_regular_file(legacy_state):
            layout = "legacy"
            records.append(legacy_state)

        versions: set[int] = set()
        for state in records:
            text = security.bounded_text(state, C.MAX_STATE_BYTES)
            if not text:
                continue
            match = _STATE_VERSION_FIELD_RE.search(text)
            if match:
                versions.add(int(match.group(1)))
        return AidlcLayoutProbe(layout, tuple(spaces), len(records), tuple(sorted(versions)))

    def _symlinks_at_managed_paths(self, root: Path) -> tuple[str, ...]:
        out: list[str] = []
        for rel in MANAGED_PATH_PROBES:
            if (root / rel).is_symlink():
                out.append(rel)
        return tuple(out)

    @staticmethod
    def _free_space(root: Path) -> int | None:
        try:
            return int(shutil.disk_usage(root).free)
        except OSError:
            return None

    @staticmethod
    def _read_json(path: Path, cap: int) -> Any:
        raw = security.bounded_read(path, cap)
        if raw is None:
            return None
        try:
            return json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            return None

    def _layout_warnings(
        self, layout: AidlcLayoutProbe, harness_dirs: tuple[HarnessDir, ...]
    ) -> list[PreflightFinding]:
        out: list[PreflightFinding] = []
        if layout.layout == "legacy":
            out.append(
                self._warning(
                    "legacy_layout",
                    {"path": C.LEGACY_STATE_REL},
                    (EvidenceRef("file", C.LEGACY_STATE_REL),),
                )
            )
        for harness in harness_dirs:
            if harness.engine_version is None:
                out.append(
                    self._warning(
                        "engine_version_unknown",
                        {"dir": harness.dir},
                        (EvidenceRef("file", f"{harness.dir}/{TOOLS_DIRNAME}/{ENGINE_VERSION_FILE}"),),
                    )
                )
        versions = sorted({h.engine_version for h in harness_dirs if h.engine_version})
        if len(versions) > 1:
            out.append(
                self._warning(
                    "mixed_harness_versions",
                    {"versions": versions},
                    tuple(EvidenceRef("file", f"{h.dir}/{TOOLS_DIRNAME}/{ENGINE_VERSION_FILE}")
                          for h in harness_dirs),
                )
            )
        return out

    @staticmethod
    def _warning(code: str, params: dict[str, Any], evidence: tuple[EvidenceRef, ...]) -> PreflightFinding:
        return PreflightFinding(
            code=code,
            severity=PREFLIGHT_WARNING_SEVERITY[code],
            message_key=f"finding.{code}",
            params=params,
            evidence=evidence,
        )

    def _duplicate_of(self, identity: ResolvedIdentity, *, exclude: str | None = None) -> str | None:
        """The ``repo_id`` already holding this identity or path, if any.

        Both comparisons are needed: the identity catches two spellings of one directory on a
        case-insensitive filesystem (where the paths differ textually but the inode does not), and the
        path catches a row whose identity could never be proven.
        """
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

    def _identity_elsewhere(self, rec: RepoRecord) -> str | None:
        """Another registered path whose live identity equals this record's stored identity.

        A renamed directory keeps its inode, so the moved repository is recognisable at whatever path
        the user has since registered it under — which is what makes ``moved`` distinguishable from
        ``unavailable``.
        """
        if not rec.resolved_identity:
            return None
        for row in self._storage.select("repos"):
            repo_id = str(row.get("id"))
            if repo_id == rec.repo_id:
                continue
            other = Path(str(row.get("canonical_path") or ""))
            if not other.is_dir():
                continue
            if self._identity_for(other, use_git=False).identity_str == rec.resolved_identity:
                return repo_id
        return None

    def _persist_availability(self, rec: RepoRecord, availability: str, detail: str | None) -> None:
        if rec.availability == availability and rec.availability_detail == detail:
            return
        self._storage.update(
            "repos", rec.repo_id, {"availability": availability, "availability_detail": detail}
        )

    @staticmethod
    def _with_install(rec: RepoRecord, health: InstallHealth) -> RepoRecord:
        return RepoRecord(
            repo_id=rec.repo_id,
            label=rec.label,
            canonical_path=rec.canonical_path,
            resolved_identity=rec.resolved_identity,
            git_common_dir_identity=rec.git_common_dir_identity,
            platform=rec.platform,
            added_at=rec.added_at,
            last_seen=rec.last_seen,
            availability=rec.availability,
            availability_detail=rec.availability_detail,
            archived=rec.archived,
            legacy_console_id=rec.legacy_console_id,
            install_status=health.status,
            installed_engine_version=health.engine_version,
            engine_dir=health.engine_dir,
            receipt_version=health.receipt_id or rec.receipt_version,
        )

    @staticmethod
    def _label(label: str | None, root: Path) -> str:
        text = (label or "").strip() or root.name or str(root)
        return text[: C.MAX_LABEL_CHARS]

    @staticmethod
    def _row(rec: RepoRecord) -> dict[str, Any]:
        return {
            "id": rec.repo_id,
            "resolved_identity": rec.resolved_identity,
            "canonical_path": rec.canonical_path,
            "git_common_dir_identity": rec.git_common_dir_identity,
            "label": rec.label,
            "added_at": rec.added_at,
            "last_seen": rec.last_seen,
            "platform": rec.platform,
            "install_status": rec.install_status,
            "installed_engine_version": rec.installed_engine_version,
            "engine_dir": rec.engine_dir,
            "receipt_version": rec.receipt_version,
            "archived": int(rec.archived),
            "availability": rec.availability,
            "availability_detail": rec.availability_detail,
            "legacy_console_id": rec.legacy_console_id,
        }
