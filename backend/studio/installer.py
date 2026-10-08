"""Transactional installer for the bundled AI-DLC payload (§1.14): after any failure the repository is the
previous installation with its receipt current, or ``recovery_required`` — never two engine versions mixed.
Receipt last, backup before overwrite, journal before create; Studio owns only receipt paths (FR-INST-004/006)."""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import hashlib
import itertools
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from . import constants as C
from . import security
from .aidlc_reader import WORKSPACE_DIRNAME
from .consistency import Finding
from .errors import LeaseHeld, StudioError
from .storage import EvidenceRef
from .wire import WireRecord

if TYPE_CHECKING:  # pragma: no cover - annotations only; no runtime import edge is created
    from .activity import ActivityProjector
    from .aidlc_reader import AidlcReader
    from .engine import EngineRunner
    from .events import EventLog
    from .leases import LeaseGrant, RepoScheduler
    from .repo_registry import PreflightReport, RepoRecord, RepoRegistry
    from .settings import SettingsService
    from .storage import Storage

# ---- vocabulary (§1.14) ------------------------------------------------------ #

#: The only payload schema understood: ownership and merge-strategy names are part of it.
PAYLOAD_SCHEMA = "aidlc-studio-payload/1"
PreviewAction = (
    "create", "identical", "conflict", "owned_identical", "owned_modified", "engine_modified", "retire",
    "retire_blocked", "merge_create", "merge_identical", "merge_update", "merge_conflict", "shell_create",
    "shell_exists",
)
#: Actions that block the whole transaction; there is no force path (FR-INST-004).
BLOCKING_ACTIONS = frozenset({"conflict", "owned_modified", "merge_conflict"})
#: Actions that put bytes on disk.
WRITING_ACTIONS = frozenset({"create", "owned_identical", "engine_modified", "shell_create", "merge_create",
                             "merge_update"})
TransactionStatus = (
    "staged", "leased", "backed_up", "written", "merged", "validated", "committed", "rolling_back",
    "rolled_back", "recovery_required", "failed",
)
STEPS = (
    "stage_payload", "verify_staging", "acquire_admin_lease", "backup", "write_files", "merge_fragments",
    "post_write_validate", "commit_receipt", "release_lease",
)
ROLLBACK_STEPS = ("restore_backups", "delete_created", "reverify_old_receipt")
#: Where a transaction can stop; pinned equal to ``leases.TRANSACTION_SETTLED_STATUS`` by a test.
_SETTLED_STATUS = frozenset({"committed", "rolled_back", "recovery_required", "failed"})
CANCELLABLE_KINDS = frozenset({"install", "upgrade", "recovery"})
#: Abandoned here, a transaction had only staged the payload and taken the lease.
_ABANDONED_WROTE_NOTHING = frozenset({"staged", "leased"})
_ABANDONED_ERROR = "process_exited"
#: An exception in these steps may already have moved bytes in the repository, so it rolls back.
ROLLBACK_TRIGGERING_STEPS = frozenset({"backup", "write_files", "merge_fragments", "post_write_validate",
                                       "commit_receipt"})
#: The status each successful step leaves the transaction in.
_STATUS_AFTER: dict[str, str] = {
    "verify_staging": "staged", "acquire_admin_lease": "leased", "backup": "backed_up",
    "write_files": "written", "merge_fragments": "merged", "post_write_validate": "validated",
    "commit_receipt": "committed", "remove_files": "written", "remove_fragments": "merged",
    "post_uninstall_validate": "validated", "commit_uninstall": "committed", "restore_version": "written",
    "post_rollback_validate": "validated", "commit_rollback": "committed",
}
#: Strategies implemented here; ``verify_payload`` refuses a manifest naming any other (review P10).
MERGE_STRATEGIES = frozenset({"json-managed-keys", "append-fenced-block", "append-missing-lines"})
#: Receipt-owned file kinds. ``shell`` files (``aidlc/**``) are created when absent and never claimed.
OWNED_KINDS = frozenset({"framework", "framework-mutable"})
PAYLOAD_FILES_DIRNAME = "aidlc-kiro"
FAILED_STAGING_DIRNAME = "staging"
FAILED_STEPS_FILENAME = "transaction.json"
#: Uninstall and rollback take the admin lease under the scheduler's existing "recovery" operation.
UNINSTALL_LEASE_OPERATION = "recovery"
UNINSTALL_JOURNAL_FILENAME = "uninstall-journal.json"
ROLLBACK_JOURNAL_FILENAME = "rollback-journal.json"
ROLLBACK_ACTIONS = ("restore_version", "remove", "preserve", "identical", "rollback_conflict")
UNINSTALL_ACTIONS = ("remove", "remove_fragment", "preserve", "already_absent", "uninstall_conflict")
_RECOVERY_ACTIONS = ("restore_backup", "remove_created", "identical", "recovery_conflict")
_JOURNALS = {"uninstall": UNINSTALL_JOURNAL_FILENAME, "rollback": ROLLBACK_JOURNAL_FILENAME}
#: The plan actions a receipt-driven operation backs up and applies.
_APPLIED = {"uninstall": frozenset({"remove", "remove_fragment"}),
            "rollback": frozenset({"restore_version", "remove"})}
_BLOCKING = BLOCKING_ACTIONS | {"uninstall_conflict", "rollback_conflict", "recovery_conflict"}
_REPO_FIELDS = ("install_status", "installed_engine_version", "engine_dir", "receipt_version")
ENGINE_DIR = C.STUDIO_HARNESS_DIR
#: Post-write smoke lookup; advisory (C15): its exit code is recorded, never fatal.
_SMOKE_LOOKUP_QUERY = "phase-of"
_SMOKE_LOOKUP_ARG = "workspace-scaffold"
#: Bound on payload and staging walks: four times the largest payload shipped.
_MAX_PAYLOAD_WALK = 4000
_MAX_DIFF_BYTES = 64 * 1024
_DIFF_CONTEXT_LINES = 3


class InjectedFault(Exception):
    """Raised by a test's fault injector at a step boundary; deliberately not a ``StudioError``."""


class _InstallCancelled(Exception):
    """Internal control flow; ``cancelled`` is an outcome code, not an HTTP error."""


# ---- records ----------------------------------------------------------------- #


def _from_row(cls: type, row: Mapping[str, Any], text: Sequence[str], **values: Any) -> Any:
    """A record from a stored row: ``text`` fields coerced to ``str`` (missing → ""), the rest as stored."""
    data = {f.name: row.get(f.name) for f in fields(cls)}
    data.update({name: str(row.get(name) or "") for name in text}, **values)
    return cls(**data)


def _json_column(value: Any) -> Any:
    """A ``*_json`` column, tolerating a row read before Storage decoded it."""
    value = value or []
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return []
    return value


@dataclass(frozen=True, slots=True)
class PayloadFile(WireRecord):
    """One vendored file: repo-relative path, digest, size and ownership kind."""

    path: str
    sha256: str
    size: int
    ownership: str


def _degraded(message: str, **details: Any) -> StudioError:
    return StudioError("payload_degraded", message, details=details)


@dataclass(frozen=True, slots=True)
class PayloadManifest(WireRecord):
    """``payload/manifest.json`` as data: every version-derived number in the product comes from here (C21)."""

    _wire_keys = ("schema", "harness", "engine_version", "source", "compatible_state_versions", "stage_count",
                  "payload_digest", "file_count", "merge_targets")
    schema: str
    harness: str
    engine_version: str
    source: dict
    compatible_state_versions: tuple[int, ...]
    stage_count: int
    payload_digest: str
    file_count: int
    merge_targets: dict
    files: tuple[PayloadFile, ...]

    @classmethod
    def load(cls, path: Path) -> "PayloadManifest":
        """Parse strictly: anything unexpected is ``payload_degraded``, never a partially trusted install."""
        raw = security.bounded_read(Path(path), C.MAX_JSON_BYTES)
        if raw is None:
            raise _degraded("payload manifest is missing or unreadable", path=Path(path).name)
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise _degraded("payload manifest is not valid JSON", reason=str(exc)[:200]) from None
        if not isinstance(data, Mapping):
            raise _degraded("payload manifest is not an object")
        schema = str(data.get("schema") or "")
        if schema != PAYLOAD_SCHEMA:
            raise _degraded("unsupported payload schema", schema=schema, expected=PAYLOAD_SCHEMA)
        rows = data.get("files")
        if not isinstance(rows, list) or not rows:
            raise _degraded("payload manifest lists no files")
        files: list[PayloadFile] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise _degraded("payload manifest has a malformed file entry")
            rel, digest, ownership = (str(row.get(key) or "") for key in ("path", "sha256", "ownership"))
            if not rel or len(digest) != 64 or ownership not in C.OWNERSHIP:
                raise _degraded("payload manifest has a malformed file entry", path=rel, ownership=ownership)
            if rel.startswith("/") or ".." in Path(rel).parts:
                raise _degraded("payload manifest has a non-relative path", path=rel)
            files.append(PayloadFile(rel, digest, int(row.get("size") or 0), ownership))
        merge_targets = data.get("mergeTargets")
        if not isinstance(merge_targets, Mapping):
            raise _degraded("payload manifest has no mergeTargets object")
        engine_version = str(data.get("engineVersion") or "")
        if not engine_version:
            raise _degraded("payload manifest has no engineVersion")
        versions = data.get("compatibleStateVersions")
        if not isinstance(versions, list) or not versions:
            raise _degraded("payload manifest has no compatibleStateVersions")
        return cls(
            schema=schema, harness=str(data.get("harness") or ""), engine_version=engine_version,
            source=dict(data.get("source") or {}), compatible_state_versions=tuple(sorted({int(v) for v in versions})),
            stage_count=int(data.get("stageCount") or 0), payload_digest=str(data.get("payloadDigest") or ""),
            file_count=int(data.get("fileCount") or len(files)),
            merge_targets={str(k): dict(v) for k, v in merge_targets.items() if isinstance(v, Mapping)},
            files=tuple(files),
        )

    def by_path(self) -> dict[str, PayloadFile]:
        return {f.path: f for f in self.files}

    def ownership_counts(self) -> dict[str, int]:
        """``{ownership: count}`` for ``GET /payload``, every declared kind present (review P03)."""
        counts = {kind: 0 for kind in C.OWNERSHIP}
        for f in self.files:
            counts[f.ownership] = counts.get(f.ownership, 0) + 1
        return counts

    def to_json(self) -> dict[str, Any]:
        return {**WireRecord.to_json(self), "ownership_counts": self.ownership_counts()}


@dataclass(frozen=True, slots=True)
class PayloadStatus(WireRecord):
    """Whether the bytes we ship are still the bytes the manifest promises."""

    ok: bool
    engine_version: str
    file_count: int
    mismatches: tuple[str, ...]
    missing: tuple[str, ...]
    extra: tuple[str, ...]
    checked_at: str


@dataclass(frozen=True, slots=True)
class PreviewEntry(WireRecord):
    """What will happen to one path, and the evidence for that verdict."""

    _wire_keys = ("path", "ownership", "action", "live_sha256", "payload_sha256", "receipt_sha256", "size",
                  "fragment_key", "diff", "blocking", "reason?")
    path: str
    ownership: str
    action: str
    live_sha256: str | None
    payload_sha256: str | None
    receipt_sha256: str | None
    size: int | None
    fragment_key: str | None
    diff: str | None
    blocking: bool
    reason: str | None = None

    def digest_json(self) -> dict[str, Any]:
        """The decision without its diff body or size, for ``PreviewPlan.digest``."""
        return {k: v for k, v in self.to_json().items() if k not in ("size", "diff")}


def _entry(path: str, ownership: str, action: str, *, live: str | None = None, payload: str | None = None,
           receipt: str | None = None, size: int | None = None, key: str | None = None,
           diff: str | None = None, reason: str | None = None) -> PreviewEntry:
    return PreviewEntry(path, ownership, action, live, payload, receipt, size, key, diff, action in _BLOCKING, reason)


@dataclass(frozen=True, slots=True)
class PreviewPlan(WireRecord):
    """Everything a user confirms before Studio writes anything. ``engine_from`` is Studio's own harness version;
    ``state_version_blocked`` is the P-06 guard against workflow state the engine cannot write (C21, B9)."""

    repo_id: str
    kind: str
    engine_from: str | None
    engine_to: str
    studio_version: str
    payload_digest: str
    entries: tuple[PreviewEntry, ...]
    counts: dict[str, int]
    blocking: bool
    blockers: tuple[PreviewEntry, ...]
    warnings: tuple[Finding, ...]
    newer_installed: bool
    same_version: bool
    requires_admin_lease: bool
    bytes_to_write: int
    state_versions_found: tuple[int, ...]
    state_version_blocked: bool
    preflight: "PreflightReport"
    state_versions_unreadable: tuple[str, ...] = ()
    #: Receipt-driven plans also bind the receipt chain, the repository identity and the recovery evidence.
    receipt_id: str | None = None
    repo_identity: str | None = None
    target_receipt_id: str | None = None
    backup_transaction_id: str | None = None
    rollback_receipt_digest: str | None = None
    recovery_transaction_id: str | None = None
    recovery_kind: str | None = None
    recovery_evidence_digest: str | None = None

    def _absent(self, name: str) -> bool:
        """The optional bindings appear only for the plan kinds they belong to."""
        if name in ("receipt_id", "repo_identity"):
            return self.kind not in {"uninstall", "rollback"}
        if name in ("target_receipt_id", "backup_transaction_id", "rollback_receipt_digest"):
            return self.kind != "rollback"
        return name.startswith("recovery_") and not self.recovery_transaction_id

    def to_json(self) -> dict[str, Any]:
        return {name: value for name, value in WireRecord.to_json(self).items() if not self._absent(name)}

    def digest(self) -> str:
        """The §2.2 ``plan_digest``. Preflight is excluded: free space and a write probe move between preview
        and confirmation; diff bodies derive from the digests that are included."""
        return _canonical_json_digest(self._digest_data())

    def _digest_data(self) -> dict:
        """The exact confirmation material, also persisted with a removal journal."""
        data = {k: v for k, v in self.to_json().items()
                if k not in ("blockers", "warnings", "requires_admin_lease", "preflight")}
        data["entries"] = [e.digest_json() for e in self.entries]
        return data


@dataclass(frozen=True, slots=True)
class StepRecord(WireRecord):
    """One step of one transaction; ``ok is None`` means it started and never finished."""

    name: str
    started_at: str
    finished_at: str | None
    ok: bool | None
    detail: dict

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "StepRecord":
        return _from_row(cls, d, ("name", "started_at"), detail=dict(d.get("detail") or {}))


@dataclass(frozen=True, slots=True)
class TransactionResult(WireRecord):
    """The durable story of one transaction."""

    transaction_id: str
    repo_id: str
    kind: str
    status: str
    steps: tuple[StepRecord, ...]
    receipt_id: str | None
    prior_receipt_id: str | None
    error: str | None
    failed_dir: str | None
    repo_install_status: str
    started_at: str
    finished_at: str | None
    engine_version: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any], *, repo_install_status: str = "") -> "TransactionResult":
        steps = tuple(StepRecord.from_json(s) for s in _json_column(row.get("steps_json")) if isinstance(s, Mapping))
        return _from_row(cls, row, ("transaction_id", "repo_id", "kind", "status", "started_at", "engine_version"),
                         steps=steps, repo_install_status=repo_install_status)


def _steps_json(steps: Sequence[StepRecord]) -> list[dict[str, Any]]:
    return [s.to_json() for s in steps]


def _step_details(row: Mapping[str, Any], name: str) -> list[dict]:
    """The detail of every ``name`` step recorded on a transaction row, oldest first."""
    return [s.detail for s in TransactionResult.from_row(row).steps if s.name == name]


@dataclass(frozen=True, slots=True)
class ReceiptFile(WireRecord):
    """One path Studio owns. A ``fragment`` records the managed fragment's digest, never the whole file's
    (the rest belongs to the project, FR-INST-006); ``uninstall`` records what Studio itself introduced."""

    _wire_keys = ("path", "ownership", "kind", "sha256", "canonical_digest", "fragment_key", "adopted", "uninstall?",
                  "engine_state_versions")
    path: str
    ownership: str
    kind: str
    sha256: str | None
    canonical_digest: str | None
    fragment_key: str | None
    adopted: bool
    uninstall: dict[str, Any] | None = None
    #: Receipt-level state-version compatibility, stored once on the first file (no schema migration).
    engine_state_versions: tuple[int, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {k: v for k, v in WireRecord.to_json(self).items() if v or k != "engine_state_versions"}

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "ReceiptFile":
        versions = d.get("engine_state_versions")
        if not isinstance(versions, list) or not all(type(v) is int and v > 0 for v in versions):
            versions = []
        uninstall = dict(d["uninstall"]) if isinstance(d.get("uninstall"), Mapping) else None
        return _from_row(cls, d, ("path", "ownership"), kind=str(d.get("kind") or "file"),
                         adopted=bool(d.get("adopted")), uninstall=uninstall, engine_state_versions=tuple(versions))


@dataclass(frozen=True, slots=True)
class Receipt(WireRecord):
    """Studio's claim of ownership over a set of paths at one engine version."""

    receipt_id: str
    repo_id: str
    resolved_repo_identity: str | None
    studio_version: str
    engine_version: str
    payload_digest: str
    committed_at: str
    prior_receipt_id: str | None
    transaction_id: str
    files: tuple[ReceiptFile, ...]
    status: str

    def to_json(self, *, include_files: bool = True) -> dict[str, Any]:
        return {**WireRecord.to_json(self), **({} if include_files else {"files": None})}

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Receipt":
        files = tuple(ReceiptFile.from_json(f) for f in _json_column(row.get("files_json")) if isinstance(f, Mapping))
        return _from_row(cls, row, ("receipt_id", "repo_id", "studio_version", "engine_version", "payload_digest",
                                    "committed_at", "transaction_id", "status"), files=files)

    def by_path(self) -> dict[str, ReceiptFile]:
        return {f.path: f for f in self.files}


@dataclass
class _Journal:
    """What a rollback needs, recorded while the transaction runs: which paths it created cannot be
    recomputed afterwards, because what was there *before* is exactly what the repository no longer has."""

    txid: str
    staging_dir: Path
    backup_dir: Path
    #: repo-relative path → digest of the backed-up copy (``None`` for a path that was absent).
    backups: dict[str, str | None] = field(default_factory=dict)
    backup_modes: dict[str, int] = field(default_factory=dict)
    created: list[str] = field(default_factory=list)
    #: paths written or merged → the digest they must have afterwards.
    written: dict[str, str] = field(default_factory=dict)
    retired: list[str] = field(default_factory=list)
    receipt_id: str | None = None
    prior_receipt_id: str | None = None
    prior_repo: dict[str, Any] = field(default_factory=dict)
    receipt_inserted: bool = False
    cancelling: bool = False
    lease_generation: int | None = None


# ---- helpers ----------------------------------------------------------------- #


def version_tuple(text: str | None) -> tuple[int, ...] | None:
    """``"2.6.2"`` → ``(2, 6, 2)``; ``None`` when not dotted-numeric (unparseable is not "older")."""
    if not text:
        return None
    parts: list[int] = []
    for chunk in str(text).strip().split("."):
        digits = "".join(itertools.takewhile(str.isdigit, chunk))
        if not digits:
            return None
        parts.append(int(digits))
    return tuple(parts) or None


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def payload_refresh_available(repo: "RepoRecord", receipt: Receipt | Mapping[str, Any] | None,
                              own_engine_version: str | None, manifest: PayloadManifest) -> bool:
    """Whether a trusted current receipt authorizes new payload bytes at the same engine version. Callers pass
    the full SQLite receipt (the registry summary omits identity and digest)."""
    prior = C.attr(receipt, "payload_digest")
    return bool(
        repo.resolved_identity and version_tuple(own_engine_version)
        and own_engine_version == manifest.engine_version
        and C.attr(receipt, "status") == "current" and C.attr(receipt, "repo_id") == repo.repo_id
        and C.attr(receipt, "resolved_repo_identity") == repo.resolved_identity
        and C.attr(receipt, "engine_version") == own_engine_version
        and _is_digest(prior) and _is_digest(manifest.payload_digest) and prior != manifest.payload_digest
    )


def _resolve_key(obj: Any, dotted: str) -> tuple[bool, Any]:
    """``(found, value)``: the literal spelling first (Kiro's flat ``"chat.defaultAgent"``), then a nested walk
    (``mcpServers.context7``), so one key list describes both kinds of file."""
    if isinstance(obj, Mapping) and dotted in obj:
        return True, obj[dotted]
    node = obj
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _assign_key(obj: dict, dotted: str, value: Any, *, nested: bool) -> None:
    """Write a managed key in the spelling the file already uses (``nested`` decides a new key's)."""
    parts = dotted.split(".")
    if dotted in obj or not nested or len(parts) == 1:
        obj[dotted] = value
        return
    node: dict = obj
    for part in parts[:-1]:
        child = node.get(part)
        if child is None:
            child = node[part] = {}
        elif not isinstance(child, dict):  # replacing it would silently discard the user's value
            raise StudioError(
                "install_conflict", f"{part!r} holds a value that is not an object, so {dotted!r} cannot be written",
                details={"key": dotted, "parent": part, "reason": "managed_parent_not_an_object"},
            )
        node = child
    node[parts[-1]] = value


def _canonical_json_digest(value: Any) -> str:
    return security.sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _strict_json_object(data: bytes) -> dict:
    """A JSON object, refusing duplicate keys: they hide user-owned values from a removal."""
    obj = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_keys)
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    return obj


def _remove_json_keys(data: bytes, keys: Sequence[str], parents: Sequence[Sequence[str]], *,
                      entry_paths: Sequence[Sequence[str]] = ()) -> bytes:
    """Remove owned members without reserializing any retained key, value or string escape."""
    obj = _strict_json_object(data)
    text = data.decode("utf-8")
    decoder = json.JSONDecoder()
    objects: dict[tuple[str, ...], list[tuple[str, int, int]]] = {}

    def skip(pos: int) -> int:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        return pos

    def scan(pos: int, prefix: tuple[str, ...]) -> None:
        members = []
        pos = skip(pos + 1)
        while text[pos] != "}":
            start = pos
            key, pos = decoder.raw_decode(text, pos)
            pos = skip(skip(pos) + 1)  # past the ':' the strict parse already validated
            value, end = decoder.raw_decode(text, pos)
            if isinstance(value, dict):
                scan(pos, (*prefix, key))
            members.append((key, start, end))
            pos = skip(end)
            if text[pos] == ",":
                pos = skip(pos + 1)
        objects[prefix] = members

    def parent_of(path: tuple[str, ...]) -> dict | None:
        node = obj
        for part in path[:-1]:
            node = node.get(part)
            if not isinstance(node, dict):
                return None
        return node

    scan(skip(0), ())
    removed: set[tuple[str, ...]] = set()
    paths = [(key,) if key in obj else tuple(key.split(".")) for key in keys]
    for path in [*paths, *(tuple(p) for p in entry_paths)]:
        node = parent_of(path)
        if node is not None and path[-1] in node:
            del node[path[-1]]
            removed.add(path)
    for path in sorted((tuple(p) for p in parents if p), key=len, reverse=True):
        node = parent_of(path)
        if node is not None and node.get(path[-1]) == {}:
            del node[path[-1]]
            removed.add(path)
    removed = {path for path in removed if not any(path[:i] in removed for i in range(1, len(path)))}
    intervals = []
    for prefix, members in objects.items():
        index = 0
        while index < len(members):
            if (*prefix, members[index][0]) not in removed:
                index += 1
                continue
            first = index
            while index < len(members) and (*prefix, members[index][0]) in removed:
                index += 1
            if index < len(members):
                intervals.append((members[first][1], members[index][1]))
            elif first:
                intervals.append((members[first - 1][2], members[-1][2]))
            else:
                intervals.append((members[0][1], members[-1][2]))
    for start, end in sorted(intervals, reverse=True):
        text = text[:start] + text[end:]
    result = text.encode("utf-8")
    if _strict_json_object(result) != obj:
        raise ValueError("JSON member removal did not preserve the remaining object")
    return result


def _remove_owned_lines(live: bytes, lines: Sequence[str], message: str) -> bytes:
    """``live`` minus one exact occurrence of each line Studio added; a later user duplicate remains."""
    parts = live.splitlines(keepends=True)
    for line in lines:
        exact = (line + "\n").encode("utf-8")
        if exact not in parts:
            raise StudioError("install_conflict", message)
        parts.remove(exact)
    return b"".join(parts)


def _is_text(data: bytes | None) -> bool:
    if data is None or b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _unified_diff(rel: str, old: bytes | None, new: bytes | None) -> str | None:
    """A bounded, redacted unified diff for a human decision; ``None`` unless both sides are text."""
    if not _is_text(old) or not _is_text(new):
        return None
    lines = difflib.unified_diff(
        old.decode("utf-8").splitlines(keepends=True),  # type: ignore[union-attr]
        new.decode("utf-8").splitlines(keepends=True),  # type: ignore[union-attr]
        fromfile=f"a/{rel}", tofile=f"b/{rel}", n=_DIFF_CONTEXT_LINES,
    )
    out: list[str] = []
    size = 0
    for line in lines:  # truncated while building, so a large file is never joined only to be discarded
        size += len(line)
        if size > _MAX_DIFF_BYTES:
            out.append("\n… diff truncated …\n")
            break
        out.append(line)
    return security.redact("".join(out)) if out else None


def _atomic_write(target: Path, data: bytes) -> None:
    """Same-directory temporary file, ``fsync``, ``os.replace``; the temporary file (in the user's repository)
    is removed on every failure path."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.aidlc-studio.tmp"
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(target))
    except BaseException:
        tmp.unlink(missing_ok=True)  # `missing_ok`: `os.open` itself may be what failed
        raise


def _durable_write(target: Path, data: bytes, mode: int = 0o600) -> None:
    """Atomic replacement through an unpredictable temporary name, exact mode, and file + directory fsync."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".aidlc-studio-", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_bytes(path: Path) -> bytes | None:
    """A regular file's bytes; ``None`` for absent, symlink or unreadable. For Studio's own files only."""
    if not security.is_regular_file(path):
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _live_bytes(path: Path) -> tuple[bytes | None, bool]:
    """``(bytes, unreadable)``: "cannot read" is never "absent", which would back up nothing and let a rollback
    delete the user's file. Symlinks and other non-regular files answer ``(None, False)``; callers refuse those."""
    try:
        st = path.lstat()
    except FileNotFoundError:
        return None, False
    except OSError:
        return None, True
    if not stat.S_ISREG(st.st_mode):
        return None, False
    try:
        return path.read_bytes(), False
    except OSError:
        return None, True


def _digest_of(data: bytes | None) -> str | None:
    return security.sha256_bytes(data) if data is not None else None


def _finding(code: str, severity: str, params: dict[str, Any], evidence: Sequence[EvidenceRef]) -> Finding:
    return Finding(code=code, severity=severity, message_key=f"finding.{code}", params=params, evidence=tuple(evidence))


def _terminated(text: str) -> str:
    return text if text.endswith("\n") or not text else text + "\n"


# ---- merge fragment handlers ------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Fragment:
    """One side of a merge target reduced to its managed part; ``conflict``: present, and not ours."""

    digest: str | None
    conflict: bool = False
    conflict_detail: tuple[str, ...] = ()
    text: str | None = None  # the managed part rendered for the preview diff


class _MergeHandler:
    """Base for the three strategies: read the payload side, rewrite the live side."""

    strategy = ""
    #: Append-only strategies absorb a user's change; value-replacing ones must block on it.
    can_overwrite_user_change = False

    def __init__(self, rel: str, spec: Mapping[str, Any], payload_bytes: bytes | None) -> None:
        self.rel, self.spec, self.payload_bytes = rel, dict(spec), payload_bytes


class _JsonManagedKeys(_MergeHandler):
    """JSON settings files: only the listed dotted keys are Studio's; every other key round-trips."""

    strategy = "json-managed-keys"

    @property
    def managed_keys(self) -> tuple[str, ...]:
        return tuple(str(k) for k in self.spec.get("managedKeys") or [])

    @property
    def managed_map_entries(self) -> dict[str, tuple[str, ...]]:
        """Literal model IDs Studio owns inside ``chat.modelDefaults``; never dotted paths or deep merges."""
        if "managedMapEntries" not in self.spec:
            return {}
        entries = self.spec["managedMapEntries"]
        names = entries.get("chat.modelDefaults") if isinstance(entries, dict) else None
        if (
            self.rel != ".kiro/settings/cli.json" or not isinstance(entries, dict)
            or set(entries) != {"chat.modelDefaults"} or "chat.modelDefaults" not in self.managed_keys
            or not isinstance(names, list) or not all(isinstance(key, str) and key for key in names)
            or len(set(names)) != len(names)
        ):
            raise StudioError("payload_degraded", "invalid managed model-entry metadata", details={"path": self.rel})
        return {key: tuple(value) for key, value in entries.items()}

    @property
    def fragment_key(self) -> str:
        return f"{Path(self.rel).name}:{','.join(self.managed_keys)}"

    def _parse(self, data: bytes) -> Any:
        return _strict_json_object(data) if self.managed_map_entries else json.loads(data.decode("utf-8"))

    def _payload_obj(self) -> dict:
        if self.payload_bytes is None:
            return {}
        if self.managed_map_entries:
            try:
                obj = _strict_json_object(self.payload_bytes)
                if self._model_map_problems(obj):
                    raise ValueError("ambiguous model defaults")
                for key, names in self.managed_map_entries.items():
                    found, value = _resolve_key(obj, key)
                    if not found or not isinstance(value, dict) or not set(names) <= set(value):
                        raise ValueError("declared model entries are missing")
                return obj
            except ValueError as exc:
                raise StudioError("payload_degraded", "invalid payload model defaults",
                                  details={"path": self.rel}) from exc
        try:
            obj = json.loads(self.payload_bytes.decode("utf-8"))
        except ValueError:
            return {}
        return obj if isinstance(obj, dict) else {}

    def _managed_view(self, obj: Mapping[str, Any] | None) -> dict[str, Any]:
        """The managed keys actually present; an absent key is omitted (a user's ``null`` is not absence)."""
        out: dict[str, Any] = {}
        for key in self.managed_keys if obj is not None else ():
            found, value = _resolve_key(obj, key)
            if found:
                names = self.managed_map_entries.get(key)
                out[key] = value if names is None else {name: value[name] for name in names if name in value}
        return out

    def _model_map_problems(self, obj: Mapping[str, Any]) -> tuple[str, ...]:
        problems = []
        for key, names in self.managed_map_entries.items():
            parent = obj.get("chat")
            if key in obj and isinstance(parent, Mapping) and "modelDefaults" in parent:
                problems.append("ambiguous_model_defaults_spelling")
            found, value = _resolve_key(obj, key)
            if found and not isinstance(value, dict):
                problems.append("model_defaults_not_an_object")
            elif found and any(name in value and not isinstance(value[name], dict) for name in names):
                problems.append("model_preferences_not_an_object")
        return tuple(problems)

    def _managed_parents_not_objects(self, obj: Mapping[str, Any]) -> tuple[str, ...]:
        """Nested managed keys whose parent holds a non-object, which a merge would otherwise replace."""
        out: list[str] = []
        for key in self.managed_keys:
            if key in obj or "." not in key:
                continue
            node: Any = obj
            walked: list[str] = []
            for part in key.split(".")[:-1]:
                if not isinstance(node, Mapping):
                    break
                if part not in node:
                    node = None
                    break
                walked.append(part)
                node = node[part]
            else:
                if node is not None and not isinstance(node, Mapping):
                    out.append(f"managed_parent_not_an_object:{'.'.join(walked)}")
        return tuple(dict.fromkeys(out))

    def payload_fragment(self) -> _Fragment:
        view = self._managed_view(self._payload_obj())
        return _Fragment(_canonical_json_digest(view), text=json.dumps(view, indent=2, sort_keys=True) + "\n")

    def live_fragment(self, live: bytes | None) -> _Fragment:
        if live is None:
            return _Fragment(None)
        try:
            obj = self._parse(live)
        except ValueError:  # possibly mid-edit: a conflict, never something to overwrite
            return _Fragment(None, True, ("unparseable",))
        if not isinstance(obj, dict):
            return _Fragment(None, True, ("not_an_object",))
        blocked = self._managed_parents_not_objects(obj) + self._model_map_problems(obj)
        if blocked:  # a non-object managed parent would read as "absent" and then be replaced wholesale
            return _Fragment(None, True, blocked)
        view = self._managed_view(obj)
        payload_view = self._managed_view(self._payload_obj())
        maps = self.managed_map_entries
        differing = tuple(key for key in self.managed_keys if key in view and (
            any(value != payload_view.get(key, {}).get(name) for name, value in view[key].items())
            if key in maps else view.get(key) != payload_view.get(key)))
        return _Fragment(_canonical_json_digest(view), bool(differing), differing,
                         json.dumps(view, indent=2, sort_keys=True) + "\n")

    def merged_bytes(self, live: bytes | None, *, receipt_digest: str | None = None) -> bytes:
        obj: dict[str, Any] = {}
        if live is not None:
            try:
                decoded = self._parse(live)
            except ValueError:
                raise StudioError("install_conflict", f"{self.rel} is not valid JSON and cannot be merged",
                                  details={"path": self.rel}) from None
            if isinstance(decoded, dict):
                obj = decoded
        maps = self.managed_map_entries
        if self._model_map_problems(obj) or (maps and self._managed_parents_not_objects(obj)):
            raise StudioError("install_conflict", "ambiguous model defaults cannot be merged")
        payload_obj = self._payload_obj()
        for key in self.managed_keys:
            found, value = _resolve_key(payload_obj, key)
            if not found:
                continue
            nested = key not in payload_obj  # a key neither file has yet mirrors the payload's spelling
            if key in maps:
                present, current = _resolve_key(obj, key)
                value = {**(current if present else {}), **{name: value[name] for name in maps[key]}}
                if present:
                    nested = key not in obj
            _assign_key(obj, key, value, nested=nested)
        return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


class _FencedBlock(_MergeHandler):
    """``AGENTS.md``: Studio owns only what lies between the two markers; the payload ships the block body."""

    strategy = "append-fenced-block"

    @property
    def marker_start(self) -> str:
        return str(self.spec.get("markerStart") or "")

    @property
    def marker_end(self) -> str:
        return str(self.spec.get("markerEnd") or "")

    @property
    def fragment_key(self) -> str:
        """``AGENTS.md:aidlc-studio:managed``: the block named from ``markerStart`` minus comment syntax."""
        token = self.marker_start.strip().removeprefix("<!--").removesuffix("-->").strip().removesuffix(":start")
        return f"{Path(self.rel).name}:{token}"

    def _payload_body(self) -> str:
        return "" if self.payload_bytes is None else self.payload_bytes.decode("utf-8", "replace").strip("\n")

    def payload_fragment(self) -> _Fragment:
        body = self._payload_body()
        return _Fragment(security.sha256_text(body), text=body + "\n")

    def _extract(self, text: str) -> str | None:
        start = text.find(self.marker_start)
        if start < 0:
            return None
        after = start + len(self.marker_start)
        if text[after:after + 1] == "\n":
            after += 1
        end = text.find(self.marker_end, after)
        if end < 0:
            return None
        body = text[after:end]
        return body[:-1] if body.endswith("\n") else body

    def live_fragment(self, live: bytes | None) -> _Fragment:
        if live is None:
            return _Fragment(None)
        text = live.decode("utf-8", "replace")
        body = self._extract(text)
        if body is None and self.marker_start and self.marker_start in text:
            # A start marker without its end is a cut-short block for the user to repair; merging would add another.
            return _Fragment(None, True, ("unterminated_block",))
        if body is None:
            # No markers: the whole document is the fragment, so a verbatim adopted copy stays recognisable
            # (also against a later payload's receipt) and appending to anyone else's takes nothing away.
            whole = text.strip("\n")
            return _Fragment(security.sha256_text(whole), text=whole + "\n")
        digest = security.sha256_text(body)
        modified = digest != self.payload_fragment().digest
        return _Fragment(digest, modified, ("block_modified",) if modified else (), body + "\n")

    def merged_bytes(self, live: bytes | None, *, receipt_digest: str | None = None) -> bytes:
        """Swap the block between markers; replace a marker-less document only when it digests to the receipt's
        (Studio's own adopted copy, FR-INST-007 — appending would leave two instruction documents); else append."""
        block = f"{self.marker_start}\n{self._payload_body()}\n{self.marker_end}\n"
        if live is None:
            return block.encode("utf-8")
        text = live.decode("utf-8", "replace")
        start = text.find(self.marker_start)
        if start >= 0:
            end = text.find(self.marker_end, start)
            if end >= 0:
                return (text[:start] + block + text[end + len(self.marker_end):].removeprefix("\n")).encode("utf-8")
        elif receipt_digest is not None and security.sha256_text(text.strip("\n")) == receipt_digest:
            return block.encode("utf-8")
        prefix = _terminated(text)
        return (prefix + ("\n" if prefix else "") + block).encode("utf-8")


class _MissingLines(_MergeHandler):
    """``.gitignore``: append the payload's missing lines in payload order; append-only, so never a conflict."""

    strategy = "append-missing-lines"
    can_overwrite_user_change = True

    @property
    def fragment_key(self) -> str:
        return f"{Path(self.rel).name}:lines"

    def set_lines(self, lines: Sequence[str]) -> None:
        """Use a receipt's recorded line list as the payload side."""
        self.payload_bytes = ("\n".join(lines) + "\n").encode("utf-8")

    def _payload_lines(self) -> list[str]:
        if self.payload_bytes is None:
            return []
        return [line for line in self.payload_bytes.decode("utf-8", "replace").splitlines() if line.strip()]

    def payload_fragment(self) -> _Fragment:
        lines = "\n".join(self._payload_lines())
        return _Fragment(security.sha256_text(lines), text=lines + "\n")

    def live_fragment(self, live: bytes | None) -> _Fragment:
        if live is None:
            return _Fragment(None)
        present = {line.strip() for line in live.decode("utf-8", "replace").splitlines()}
        kept = "\n".join(line for line in self._payload_lines() if line.strip() in present)
        return _Fragment(security.sha256_text(kept), text=kept + "\n")

    def merged_bytes(self, live: bytes | None, *, receipt_digest: str | None = None) -> bytes:
        payload_lines = self._payload_lines()
        if live is None:
            return ("\n".join(payload_lines) + "\n").encode("utf-8")
        text = live.decode("utf-8", "replace")
        present = {line.strip() for line in text.splitlines()}
        missing = [line for line in payload_lines if line.strip() not in present]
        if not missing:
            return live
        return (_terminated(text) + "\n".join(missing) + "\n").encode("utf-8")


_HANDLERS: dict[str, type[_MergeHandler]] = {c.strategy: c for c in (_JsonManagedKeys, _FencedBlock, _MissingLines)}


# ---- the installer ----------------------------------------------------------- #


class Installer:
    """Preview and transaction owner for the bundled payload. Blocking work runs in ``asyncio.to_thread`` (§0.6)."""

    def __init__(
        self, storage: "Storage", registry: "RepoRegistry", reader: "AidlcReader", engine: "EngineRunner",
        scheduler: "RepoScheduler", activity: "ActivityProjector", events: "EventLog", clock: "C.Clock",
        ids: "C.IdFactory", *, payload_dir: Path, manifest: PayloadManifest, data_dir: Path,
        fault_injector: Callable[[str], None] | None = None, settings: "SettingsService | None" = None,
    ) -> None:
        self._storage, self._registry, self._reader, self._engine = storage, registry, reader, engine
        self._scheduler, self._activity, self._events = scheduler, activity, events
        self._clock, self._ids = clock, ids
        self._manifest, self._data_dir = manifest, Path(data_dir)
        # `payload_dir` may name `payload/` or its file root `payload/aidlc-kiro/`; manifest paths use the root.
        nested = Path(payload_dir) / PAYLOAD_FILES_DIRNAME
        self._payload_root = nested if nested.is_dir() else Path(payload_dir)
        #: Public so a test can raise or act at a named step boundary (§4.1 ``fault`` fixture).
        self.fault_injector = fault_injector
        # `installer.run_doctor_after_install` has no other source: absent settings mean doctor never runs.
        self._settings = settings

    @property
    def manifest(self) -> PayloadManifest:
        return self._manifest

    @property
    def payload_root(self) -> Path:
        return self._payload_root

    # ---- payload ---- #

    def verify_payload(self) -> PayloadStatus:
        """Re-hash the payload and check every merge target has a handler: run at startup, so a bad payload
        degrades the app before anyone installs with it."""
        root = self._payload_root
        mismatches: list[str] = []
        missing: list[str] = []
        for pf in self._manifest.files:
            try:
                data = _read_bytes(security.resolve_inside(root, pf.path))
            except StudioError:
                mismatches.append(f"path_escapes_payload:{pf.path}")
                continue
            if data is None:
                missing.append(pf.path)
            elif security.sha256_bytes(data) != pf.sha256:
                mismatches.append(f"sha256:{pf.path}")
            elif len(data) != pf.size:
                mismatches.append(f"size:{pf.path}")
        listed = {pf.path for pf in self._manifest.files}
        walked = security.iter_regular_files(root, max_files=_MAX_PAYLOAD_WALK) if root.is_dir() else []
        extra = [rel for rel in (p.relative_to(root).as_posix() for p in walked) if rel not in listed]
        if len(self._manifest.files) != self._manifest.file_count:
            mismatches.append("fileCount")
        if self._payload_digest() != self._manifest.payload_digest:
            mismatches.append("payloadDigest")
        mismatches.extend(self._merge_target_problems())
        return PayloadStatus(not mismatches and not missing, self._manifest.engine_version, len(self._manifest.files),
                             tuple(sorted(mismatches)), tuple(sorted(missing)), tuple(sorted(extra)), self._clock.iso())

    def _payload_digest(self) -> str:
        """``payloadDigest`` recomputed from the manifest rows in their order: proves the *list* was not edited."""
        h = hashlib.sha256()
        for pf in self._manifest.files:
            h.update(f"{pf.path}\0{pf.sha256}\n".encode("utf-8"))
        return h.hexdigest()

    def _merge_target_problems(self) -> list[str]:
        problems: list[str] = []
        targets = self._manifest.merge_targets
        for rel, spec in targets.items():
            strategy = str(spec.get("strategy") or "")
            if strategy not in MERGE_STRATEGIES:
                problems.append(f"merge_strategy_unsupported:{rel}")
            elif strategy == "json-managed-keys" and not spec.get("managedKeys"):
                problems.append(f"merge_keys_missing:{rel}")
            elif strategy == "append-fenced-block" and not (spec.get("markerStart") and spec.get("markerEnd")):
                problems.append(f"merge_markers_missing:{rel}")
            if "managedMapEntries" in spec:
                try:
                    handler = self._handler(rel)
                    if not isinstance(handler, _JsonManagedKeys):
                        raise StudioError("payload_degraded", "model entries require JSON keys")
                    handler.payload_fragment()
                    obj = handler._payload_obj()
                    for key, names in handler.managed_map_entries.items():
                        found, value = _resolve_key(obj, key)
                        if not found or not isinstance(value, dict) or set(value) != set(names):
                            raise StudioError("payload_degraded", "model-entry metadata is incomplete")
                except StudioError:
                    problems.append(f"merge_model_entries_invalid:{rel}")
        for pf in self._manifest.files:
            if pf.ownership == "merge" and pf.path not in targets:
                problems.append(f"merge_target_missing:{pf.path}")
            if pf.path.startswith(f"{WORKSPACE_DIRNAME}/") and pf.ownership != "shell":
                problems.append(f"ownership_not_shell:{pf.path}")  # `aidlc/**` is the user's (FR-INST-005)
        return problems

    def _handler(self, rel: str, *, source: Path | None = None) -> _MergeHandler:
        """The strategy for one merge target, reading the payload side from ``source`` (the verified staging copy
        once a transaction runs, so an app update replacing ``payload/`` cannot mix two versions)."""
        spec = self._manifest.merge_targets.get(rel) or {}
        strategy = str(spec.get("strategy") or "")
        cls = _HANDLERS.get(strategy)
        if cls is None:
            raise StudioError("payload_degraded", f"no handler for merge strategy {strategy!r}",
                              details={"path": rel, "strategy": strategy})
        return cls(rel, spec, _read_bytes(security.resolve_inside(source or self._payload_root, rel)))

    # ---- preview ---- #

    def preview(self, repo: "RepoRecord", kind: str, *, target_receipt_id: str | None = None) -> PreviewPlan:
        """The ownership × live-state matrix for one repository; sync, read-only, no engine verb (C35). Keyed on
        ``own_engine_version`` (Studio's ``.kiro``), never a foreign harness Studio merely reads (FR-INST-003)."""
        if kind == "uninstall":
            return self._preview_uninstall(repo)
        if kind == "rollback":
            return self._rollback_material(repo, target_receipt_id)[0]
        if kind == "recovery":
            recovery = self._preview_receipt_recovery(repo)
            if recovery is not None:
                return recovery
        if kind not in ("install", "upgrade", "recovery"):
            raise ValueError(f"unknown install kind {kind!r}")
        root = Path(repo.canonical_path)
        receipt = self.current_receipt(repo.repo_id)
        owned = receipt.by_path() if receipt else {}
        health = self._registry.detect_install(repo, receipt)
        preflight = self._registry.preflight(repo.canonical_path)
        entries = [self._file_entry(root, pf, owned.get(pf.path)) for pf in self._manifest.files]
        bytes_to_write = sum(int(e.size or 0) for e in entries if e.action in WRITING_ACTIONS)
        entries.extend(self._retire_entries(root, receipt))
        counts = {action: 0 for action in PreviewAction}
        for entry in entries:
            counts[entry.action] = counts.get(entry.action, 0) + 1
        blockers = tuple(e for e in entries if e.blocking)
        found, unreadable = self._state_versions(root)
        compatible = self._manifest.compatible_state_versions
        incompatible = tuple(v for v in found if v not in compatible)
        warnings = [_finding(w.code, w.severity, dict(w.params), w.evidence) for w in preflight.warnings]
        if incompatible:
            warnings.append(_finding(
                "state_version_unsupported", "blocking",
                {"found": list(found), "incompatible": list(incompatible), "compatible": list(compatible)},
                (EvidenceRef("studio", f"repo:{repo.repo_id}", "State Version on disk"),)))
        if unreadable:
            warnings.append(_finding("state_unreadable", "blocking", {"paths": list(unreadable)},
                                     tuple(EvidenceRef("file", rel) for rel in unreadable[:8])))
        own = health.own_engine_version
        installed, bundled = version_tuple(own), version_tuple(self._manifest.engine_version)
        return PreviewPlan(
            repo_id=repo.repo_id, kind=kind, engine_from=own, engine_to=self._manifest.engine_version,
            studio_version=C.APP_VERSION, payload_digest=self._manifest.payload_digest, entries=tuple(entries),
            counts=counts, blocking=bool(blockers), blockers=blockers, warnings=tuple(warnings),
            newer_installed=bool(installed and bundled and installed > bundled),
            same_version=bool(own and own == self._manifest.engine_version
                              and not payload_refresh_available(repo, receipt, own, self._manifest)),
            # Always: the lease is taken before anything is checked, even when the plan writes nothing.
            requires_admin_lease=True, bytes_to_write=bytes_to_write, state_versions_found=found,
            state_version_blocked=bool(incompatible) or bool(unreadable), preflight=preflight,
            state_versions_unreadable=unreadable,
        )

    def _file_entry(self, root: Path, pf: PayloadFile, rcpt: ReceiptFile | None) -> PreviewEntry:
        if pf.ownership == "merge":
            return self._merge_entry(root, pf, rcpt)
        try:
            target = security.resolve_inside(root, pf.path)
        except StudioError:  # a managed path resolving outside the repository can never be written
            return _entry(pf.path, pf.ownership, "conflict", payload=pf.sha256, size=pf.size)
        symlinked = (root / pf.path).is_symlink()
        live, unreadable = (None, False) if symlinked else _live_bytes(target)
        live_sha, rcpt_sha = _digest_of(live), (rcpt.sha256 if rcpt else None)
        if pf.ownership == "shell":  # an existing shell file, even an unreadable one, is left alone
            action = "shell_exists" if symlinked or unreadable or live is not None else "shell_create"
            return _entry(pf.path, pf.ownership, action, live=live_sha, payload=pf.sha256, receipt=rcpt_sha,
                          size=pf.size)
        if symlinked or unreadable:
            action = "conflict"  # never `create` over a file Studio cannot see (see `_live_bytes`)
        elif live is None:
            action = "create"
        elif live_sha == pf.sha256:
            action = "identical"
        elif rcpt_sha is None:
            action = "conflict"
        elif live_sha == rcpt_sha:
            action = "owned_identical"
        else:
            action = "engine_modified" if pf.ownership == "framework-mutable" else "owned_modified"
        diff = None
        if action in BLOCKING_ACTIONS or action == "engine_modified":
            diff = _unified_diff(pf.path, live, _read_bytes(security.resolve_inside(self._payload_root, pf.path)))
        return _entry(pf.path, pf.ownership, action, live=live_sha, payload=pf.sha256, receipt=rcpt_sha,
                      size=pf.size, diff=diff)

    def _merge_entry(self, root: Path, pf: PayloadFile, rcpt: ReceiptFile | None) -> PreviewEntry:
        """Decide a merge target by its fragment, never the whole file; a fragment Studio cannot prove is still
        its own is ``merge_conflict``: Studio asks rather than overwrites."""
        handler = self._handler(pf.path)
        symlinked = (root / pf.path).is_symlink()
        target = security.resolve_inside(root, pf.path)
        live, unreadable = (None, False) if symlinked else _live_bytes(target)
        ours = handler.payload_fragment()
        theirs = handler.live_fragment(live)
        rcpt_digest = rcpt.canonical_digest if rcpt else None
        receipt_conflict = False
        if isinstance(handler, _JsonManagedKeys) and handler.managed_map_entries and rcpt is not None:
            try:
                previous = self._receipt_handler(rcpt)
                receipt_conflict = live is not None and previous.live_fragment(live).digest != rcpt_digest
            except StudioError:
                receipt_conflict = True
                theirs = replace(theirs, conflict=True,
                                 conflict_detail=(*theirs.conflict_detail, "legacy_model_ownership_unknown"))
        if symlinked or receipt_conflict or unreadable:
            action = "merge_conflict"  # a merge rewrites the file: never do that to bytes Studio did not read
        elif live is None:
            action = "merge_create"
        elif theirs.digest == ours.digest:
            action = "merge_identical"
        elif (rcpt_digest is not None and theirs.digest == rcpt_digest) or handler.can_overwrite_user_change:
            action = "merge_update"  # ours and untouched, or an append-only strategy
        elif theirs.conflict or rcpt_digest is not None:
            action = "merge_conflict"  # managed content that is not ours, or our fragment edited by hand
        else:
            action = "merge_update"
        diff = None
        if action in ("merge_conflict", "merge_update"):
            diff = _unified_diff(f"{pf.path} [{handler.fragment_key}]",
                                 (theirs.text or "").encode("utf-8") if live is not None else b"",
                                 (ours.text or "").encode("utf-8"))
            if theirs.conflict_detail:  # otherwise a refused, unparseable file shows as a plain addition
                diff = f"# managed: {', '.join(theirs.conflict_detail)}\n" + (diff or "")
        return _entry(pf.path, pf.ownership, action, live=theirs.digest, payload=ours.digest, receipt=rcpt_digest,
                      size=pf.size, key=handler.fragment_key, diff=diff)

    def _retire_entries(self, root: Path, receipt: Receipt | None) -> list[PreviewEntry]:
        """Receipt-owned files the payload no longer ships: retired only while byte-identical to the receipt; an
        edited one is reported (``retire_blocked``) and left on disk, never deleted."""
        if receipt is None:
            return []
        shipped = {pf.path for pf in self._manifest.files}
        out: list[PreviewEntry] = []
        for rf in receipt.files:
            if rf.kind != "file" or rf.path in shipped or rf.ownership not in OWNED_KINDS:
                continue
            try:
                live = _read_bytes(security.resolve_inside(root, rf.path))
            except StudioError:
                continue
            if live is not None:
                sha = security.sha256_bytes(live)
                out.append(_entry(rf.path, rf.ownership, "retire" if sha == rf.sha256 else "retire_blocked",
                                  live=sha, receipt=rf.sha256, size=len(live)))
        return out

    def _state_versions(self, root: Path) -> tuple[tuple[int, ...], tuple[str, ...]]:
        """Every ``State Version`` on disk and the records whose state is unreadable, for every kind (P-06)."""
        found: set[int] = set()
        unreadable: list[str] = []
        if not root.is_dir():
            return (), ()
        for space in self._reader.list_spaces(root) or [C.DEFAULT_SPACE]:
            for intent_dir in self._reader.list_intent_dirs(root, space):
                rel = f"{self._reader.record_rel(space, intent_dir)}/aidlc-state.md"
                try:
                    result = self._reader.read_state(root, space, intent_dir)
                except StudioError:
                    result = None
                if result is None:
                    unreadable.append(rel)
                elif result[1].state_version is not None:
                    found.add(int(result[1].state_version))
        return tuple(sorted(found)), tuple(sorted(unreadable))

    # ---- receipt-driven plans: uninstall, one-hop rollback and their recovery ---- #

    def _uninstall_root(self, repo: "RepoRecord", receipt: Receipt) -> Path:
        """The repository root, proved by path, device/inode, registration and receipt identity."""
        fresh = self._registry.get(repo.repo_id)
        root = security.validate_repo_root(repo.canonical_path)
        info = root.stat()
        if (
            fresh.canonical_path != repo.canonical_path or fresh.resolved_identity != repo.resolved_identity
            or not repo.resolved_identity or receipt.resolved_repo_identity != repo.resolved_identity
            or not info.st_ino or f"{info.st_dev}:{info.st_ino}" != repo.resolved_identity
            or str(root) != repo.canonical_path
        ):
            raise StudioError("install_conflict", "repository identity no longer matches the receipt",
                              details={"reason": "identity_changed"})
        return root

    @staticmethod
    def _safe_path(root: Path, rel: str) -> Path:
        """``root/rel`` for a normalized relative path with no symlink at any component."""
        if (not rel or Path(rel).is_absolute() or Path(rel).as_posix() != rel
                or any(part in {".", ".."} for part in rel.split("/")) or "\0" in rel or "\\" in rel):
            raise StudioError("bad_path", "invalid uninstall path", details={"path": rel})
        target = root
        for part in rel.split("/"):
            target = target / part
            if target.is_symlink():
                raise StudioError("bad_path", "uninstall path is a symlink", details={"path": rel})
        return target

    @staticmethod
    def _uninstall_owned_path(rf: ReceiptFile) -> bool:
        """A receipt can never extend removal into workflow data, another harness or project code."""
        if rf.kind == "file":
            return rf.ownership in OWNED_KINDS and rf.path.startswith(ENGINE_DIR + "/")
        return rf.kind == "fragment" and rf.ownership == "merge" and (
            rf.path in {"AGENTS.md", ".gitignore"} or rf.path.startswith(ENGINE_DIR + "/"))

    def _receipt_handler(self, rf: ReceiptFile) -> _MergeHandler:
        """The strategy as the receipt recorded it; a legacy receipt's is rebuilt from ``fragment_key``, and its
        whole-map model claim narrowed only when the old fragment digest proves it."""
        spec = (rf.uninstall or {}).get("spec")
        if not isinstance(spec, dict):
            token = (rf.fragment_key or "").partition(":")[2]
            if rf.path.endswith(".json") and token:
                spec = {"strategy": "json-managed-keys", "managedKeys": token.split(",")}
            elif rf.path == "AGENTS.md" and token:
                spec = {"strategy": "append-fenced-block", "markerStart": f"<!-- {token}:start -->",
                        "markerEnd": f"<!-- {token}:end -->"}
            else:
                raise StudioError("install_conflict", "receipt lacks the fragment definition",
                                  details={"reason": "legacy_fragment_ownership_unknown"})
        desired = self._manifest.merge_targets.get(rf.path) or {}
        if rf.path == ".kiro/settings/cli.json" and "managedMapEntries" in desired and "managedMapEntries" not in spec:
            candidate = self._handler(rf.path)
            if candidate.payload_fragment().digest != rf.canonical_digest:
                raise StudioError("install_conflict", "legacy model-entry ownership is unknown",
                                  details={"reason": "legacy_fragment_ownership_unknown"})
            spec = dict(candidate.spec)
        cls = _HANDLERS.get(spec.get("strategy"))
        if cls is None:
            raise StudioError("install_conflict", "unsupported receipt fragment")
        handler = cls(rf.path, spec, None)
        if handler.fragment_key != rf.fragment_key:
            raise StudioError("install_conflict", "receipt fragment definition changed")
        if isinstance(handler, _JsonManagedKeys):
            _ = handler.managed_map_entries  # validate any persisted model-entry definition
        return handler

    def _remove_receipt_fragment(self, rf: ReceiptFile, live: bytes) -> tuple[bytes | None, str | None]:
        """``(bytes without what Studio introduced, None)``, or ``(live, reason)`` to preserve the file. Adoption is
        not provenance: a receipt without it cannot authorize removal, and nothing is guessed from today's payload."""
        meta = rf.uninstall or {}
        if not meta and (rf.adopted or rf.fragment_key == f"{Path(rf.path).name}:lines"):
            return live, "legacy_fragment_ownership_unknown"
        handler = self._receipt_handler(rf)
        live.decode("utf-8")  # a lossy decode must never authorize deletion
        if isinstance(handler, _MissingLines):
            handler.set_lines(meta.get("all_lines") or [])
        if isinstance(handler, _JsonManagedKeys):
            _strict_json_object(live)
        if handler.live_fragment(live).digest != rf.canonical_digest:
            raise StudioError("install_conflict", "receipt-owned fragment was modified")
        if isinstance(handler, _JsonManagedKeys):
            if not meta:
                return live, "legacy_fragment_ownership_unknown"
            keys = meta.get("keys") or []
            if not set(keys) <= set(handler.managed_keys):
                raise StudioError("install_conflict", "uninstall keys exceed receipt ownership")
            entry_paths: list[tuple[str, ...]] = []
            parents = list(meta.get("parents") or [])
            maps = handler.managed_map_entries
            if maps:
                obj = _strict_json_object(live)
                introduced = meta.get("map_entries")
                if introduced is None:  # `_receipt_handler` proved the legacy fragment before narrowing its claim
                    introduced = {key: list(names) for key, names in maps.items() if key in keys}
                    parents.extend([key] if key in obj else key.split(".") for key in maps if key in keys)
                if not isinstance(introduced, dict) or not set(introduced) <= set(maps):
                    raise StudioError("install_conflict", "uninstall model entries exceed ownership")
                for key, names in introduced.items():
                    if not isinstance(names, list) or not all(isinstance(n, str) and n in maps[key] for n in names):
                        raise StudioError("install_conflict", "uninstall model entries exceed ownership")
                    parent = (key,) if key in obj else tuple(key.split("."))
                    entry_paths.extend((*parent, name) for name in names)
                keys = [key for key in keys if key not in maps]
            if not keys and not entry_paths:
                return live, "preexisting_fragment"
            after: bytes | None = _remove_json_keys(live, keys, parents, entry_paths=entry_paths)
            if meta.get("created") and _strict_json_object(after) == {}:
                after = None
            return after, None
        if isinstance(handler, _FencedBlock):
            if meta and not meta.get("block"):
                return live, "preexisting_fragment"
            start, end = handler.marker_start.encode(), handler.marker_end.encode()
            if live.count(start) != 1 or live.count(end) != 1:
                raise StudioError("install_conflict", "ambiguous or missing Studio block")
            first, last = live.index(start), live.index(end) + len(end)
            if last <= first:
                raise StudioError("install_conflict", "reversed Studio block markers")
            last += live[last:last + 1] == b"\n"
            after = live[:first] + live[last:]
        else:
            owned_lines = list(meta.get("lines") or [])
            if not owned_lines:
                return live, "preexisting_fragment"
            after = _remove_owned_lines(live, owned_lines, "an installed line was edited")
        return (None if not after and meta.get("created") else after), None

    def _uninstall_entry(self, root: Path, rf: ReceiptFile) -> PreviewEntry:
        action, reason, live, after = "preserve", "protected_path", None, None
        if self._uninstall_owned_path(rf):
            try:
                target = self._safe_path(root, rf.path)
                live, unreadable = _live_bytes(target)
                if unreadable or (target.exists() and not security.is_regular_file(target)):
                    raise StudioError("install_conflict", "unreadable or non-regular owned path")
                if live is None:
                    action, reason = "already_absent", "already_absent"
                elif rf.kind != "file":
                    after, reason = self._remove_receipt_fragment(rf, live)
                    action = "preserve" if reason else "remove_fragment"
                elif _digest_of(live) == rf.sha256:
                    action, reason = "remove", None
                elif rf.ownership == "framework-mutable":
                    action, reason = "preserve", "engine_mutable_changed"
                else:
                    raise StudioError("install_conflict", "receipt-owned file was modified")
            except (StudioError, OSError, ValueError) as exc:
                action = "uninstall_conflict"
                reason = exc.code if isinstance(exc, StudioError) else "unreadable_fragment"
        return _entry(rf.path, rf.ownership, action, live=_digest_of(live), payload=_digest_of(after),
                      receipt=rf.sha256 if rf.kind == "file" else rf.canonical_digest,
                      size=len(live) if live is not None else None, key=rf.fragment_key, reason=reason)

    def _receipt_plan(self, repo: "RepoRecord", kind: str, entries: Sequence[PreviewEntry], actions: Sequence[str], *,
                      written: str | None = None, found: tuple[int, ...] = (), blocked: bool = False,
                      unreadable: tuple[str, ...] = (), **binding: Any) -> PreviewPlan:
        blockers = tuple(e for e in entries if e.blocking)
        return PreviewPlan(
            repo_id=repo.repo_id, kind=kind, studio_version=C.APP_VERSION, entries=tuple(entries),
            counts={a: sum(e.action == a for e in entries) for a in actions}, blocking=bool(blockers),
            blockers=blockers, warnings=(), newer_installed=False, same_version=False, requires_admin_lease=True,
            bytes_to_write=sum(e.size or 0 for e in entries if e.action == written), state_versions_found=found,
            state_version_blocked=blocked, state_versions_unreadable=unreadable,
            preflight=self._registry.preflight(repo.canonical_path), **binding,
        )

    def _preview_uninstall(self, repo: "RepoRecord") -> PreviewPlan:
        if self._install_status(repo.repo_id) == "recovery_required":
            raise StudioError("install_recovery_required", "recover the interrupted transaction first")
        receipt = self.current_receipt(repo.repo_id)
        if receipt is None:
            raise StudioError("not_installed", "no current Studio receipt authorizes uninstall")
        root = self._uninstall_root(repo, receipt)
        if len(receipt.by_path()) != len(receipt.files):
            raise StudioError("install_conflict", "receipt contains duplicate paths")
        return self._receipt_plan(
            repo, "uninstall", [self._uninstall_entry(root, rf) for rf in receipt.files], UNINSTALL_ACTIONS,
            engine_from=receipt.engine_version, engine_to="", payload_digest=receipt.payload_digest,
            receipt_id=receipt.receipt_id, repo_identity=repo.resolved_identity,
        )

    def _confirmed_plan(self, repo: "RepoRecord", kind: str, digest: str, target_receipt_id: str | None = None
                        ) -> tuple[PreviewPlan, dict[str, bytes | None] | None]:
        """Recompute an uninstall/rollback plan and refuse anything but the confirmed, unblocked one."""
        if kind == "rollback":
            plan, candidates = self._rollback_material(repo, target_receipt_id)
        else:
            plan, candidates = self._preview_uninstall(repo), None
        if plan.digest() != digest:
            raise StudioError("install_conflict", f"{kind} preview changed",
                              details={"reason": "preview_changed", "plan_digest": plan.digest()})
        if plan.blocking:
            message = "rollback is blocked" if kind == "rollback" else "modified receipt content blocks uninstall"
            raise StudioError("install_conflict", message, details={"blockers": [e.to_json() for e in plan.blockers]})
        return plan, candidates

    def _rollback_context(self, repo: "RepoRecord", target_receipt_id: str | None, *, recovery: bool = False
                          ) -> tuple[Path, Receipt, Receipt]:
        """The proved root, the current receipt and its immediate superseded predecessor (an older engine)."""
        if not recovery and self._install_status(repo.repo_id) == "recovery_required":
            raise StudioError("install_recovery_required", "recover the interrupted transaction first")
        current = self.current_receipt(repo.repo_id)
        if current is None or current.prior_receipt_id is None:
            raise StudioError("install_conflict", "no prior engine receipt is available",
                              details={"reason": "no_rollback_target"})
        if target_receipt_id is not None and target_receipt_id != current.prior_receipt_id:
            raise StudioError("install_conflict", "rollback only accepts the immediate prior receipt")
        target = self.receipt(current.prior_receipt_id)
        root = self._uninstall_root(repo, current)
        source = self._storage.get("install_transactions", current.transaction_id)
        if (
            target.repo_id != repo.repo_id or target.resolved_repo_identity != repo.resolved_identity
            or target.status != "superseded" or source is None or source["status"] != "committed"
            or source["repo_id"] != repo.repo_id or source.get("prior_receipt_id") != target.receipt_id
            or source.get("resolved_repo_identity") != repo.resolved_identity
            or len(current.by_path()) != len(current.files) or len(target.by_path()) != len(target.files)
        ):
            raise StudioError("install_conflict", "rollback receipt chain is inconsistent")
        old, new = version_tuple(target.engine_version), version_tuple(current.engine_version)
        if not old or not new or old >= new:
            raise StudioError("install_conflict", "the prior receipt is not an older engine",
                              details={"reason": "target_not_older"})
        return root, current, target

    def _rollback_fragment_bytes(self, current: ReceiptFile | None, target: ReceiptFile, live: bytes | None,
                                 saved: bytes) -> bytes:
        """The live merge target with the old receipt's fragment back from its verified backup, refusing to
        restore over content neither receipt covers."""
        handler = self._receipt_handler(target)
        saved.decode("utf-8")
        if isinstance(handler, _MissingLines):
            lines = (target.uninstall or {}).get("all_lines")
            if lines is None:
                raise StudioError("install_conflict", "old ignore-line definition is unavailable",
                                  details={"reason": "legacy_fragment_ownership_unknown"})
            handler.set_lines(lines)
        if handler.live_fragment(saved).digest != target.canonical_digest:
            raise StudioError("install_conflict", "old fragment backup is missing or corrupt",
                              details={"reason": "backup_digest_mismatch"})
        if isinstance(handler, _FencedBlock):
            body = handler._extract(saved.decode("utf-8"))
            handler.payload_bytes = (body if body is not None else saved.decode("utf-8").strip("\n")).encode()
        elif isinstance(handler, _JsonManagedKeys):
            _strict_json_object(saved)
            handler.payload_bytes = saved
        if live is not None:
            live.decode("utf-8")
        if current is None:  # a historical receipt confers no authority over a fragment the user has created since
            if live is not None and handler.live_fragment(live).digest not in {None, target.canonical_digest}:
                raise StudioError("install_conflict", "unowned content occupies an old merge target")
            return handler.merged_bytes(live, receipt_digest=None)
        now = self._receipt_handler(current)
        if type(now) is not type(handler):
            raise StudioError("install_conflict", "fragment strategy changed across versions")
        if isinstance(now, _MissingLines):
            lines = (current.uninstall or {}).get("all_lines")
            if lines is None:
                raise StudioError("install_conflict", "current ignore-line ownership is unknown")
            now.set_lines(lines)
        if now.live_fragment(live).digest != current.canonical_digest:
            raise StudioError("install_conflict", "current managed fragment was modified")
        if isinstance(handler, _FencedBlock):
            if now.fragment_key != handler.fragment_key or (live is not None and (
                    live.count(handler.marker_start.encode()) != 1 or live.count(handler.marker_end.encode()) != 1)):
                raise StudioError("install_conflict", "ambiguous rollback block markers")
        elif isinstance(handler, _JsonManagedKeys):
            obj, saved_obj = _strict_json_object(live), _strict_json_object(saved)
            for key in set(handler.managed_keys) - set(now.managed_keys):
                exists, value = _resolve_key(obj, key)
                if exists and (exists, value) != _resolve_key(saved_obj, key):
                    raise StudioError("install_conflict", "user key occupies an old managed key")
            # Owning a parent key confers no ownership of every model in it: a model only the old receipt owned is
            # restored only when absent or still equal to the verified backup.
            for key, names in handler.managed_map_entries.items():
                exists, models = _resolve_key(obj, key)
                if not exists:
                    continue
                _, saved_models = _resolve_key(saved_obj, key)
                if not isinstance(models, dict) or not isinstance(saved_models, dict):
                    raise StudioError("install_conflict", "ambiguous rollback model defaults")
                for name in set(names) - set(now.managed_map_entries.get(key, ())):
                    if name in models and (name not in saved_models or _canonical_json_digest(models[name])
                                           != _canonical_json_digest(saved_models[name])):
                        raise StudioError("install_conflict", "user model occupies an old managed entry",
                                          details={"model": name})
            meta = current.uninstall or {}
            live = _remove_json_keys(live, sorted(set(meta.get("keys", [])) - set(handler.managed_keys)),
                                     meta.get("parents", []))
        else:
            live = _remove_owned_lines(live or b"", set((current.uninstall or {}).get("lines", []))
                                       - set(handler._payload_lines()), "installed ignore line was modified")
        return handler.merged_bytes(live, receipt_digest=current.canonical_digest)

    def _rollback_material(self, repo: "RepoRecord", target_receipt_id: str | None = None
                           ) -> tuple[PreviewPlan, dict[str, bytes | None]]:
        """The rollback plan, plus the exact bytes each restored path will receive."""
        root, current, target = self._rollback_context(repo, target_receipt_id)
        now_files, old_files = current.by_path(), target.by_path()
        entries: list[PreviewEntry] = []
        candidates: dict[str, bytes | None] = {}
        for rel in sorted(set(now_files) | set(old_files)):
            now, old = now_files.get(rel), old_files.get(rel)
            rf = old or now
            live = after = reason = None
            action = "rollback_conflict"
            try:
                if not self._uninstall_owned_path(rf) or (now and not self._uninstall_owned_path(now)):
                    raise StudioError("install_conflict", "rollback cannot claim protected paths",
                                      details={"reason": "protected_path"})
                path = self._safe_path(root, rel)
                live, unreadable = _live_bytes(path)
                if unreadable or (path.exists() and not security.is_regular_file(path)):
                    raise StudioError("install_conflict", "rollback target is unreadable")
                if now and now.kind == "file" and _digest_of(live) != now.sha256:
                    raise StudioError("install_conflict", "current owned file was modified")
                if now and old and (now.kind, now.ownership) != (old.kind, old.ownership):
                    raise StudioError("install_conflict", "ownership changed across versions")
                if old is None:
                    after, reason = (self._remove_receipt_fragment(now, live)
                                     if now.kind == "fragment" and live is not None else (None, None))
                    action = "preserve" if reason else ("remove" if live is not None else "identical")
                else:
                    backup = self._failure_path(C.BACKUP_DIRNAME, current.transaction_id)
                    saved = _read_bytes(self._safe_path(backup, rel))
                    if saved is None:
                        raise StudioError("install_conflict", "old engine backup is missing",
                                          details={"reason": "backup_missing"})
                    if old.kind == "file":
                        if _digest_of(saved) != old.sha256:
                            raise StudioError("install_conflict", "old engine backup is corrupt",
                                              details={"reason": "backup_digest_mismatch"})
                        if now is None and live is not None and _digest_of(live) != old.sha256:
                            raise StudioError("install_conflict", "user file occupies an old engine path")
                        after = saved
                    else:
                        after = self._rollback_fragment_bytes(now, old, live, saved)
                    action = "identical" if after == live else "restore_version"
                candidates[rel] = after
            except (StudioError, OSError, ValueError) as exc:
                reason = (((exc.details or {}).get("reason") or exc.code) if isinstance(exc, StudioError)
                          else "unreadable_backup")
            entries.append(_entry(rel, rf.ownership, action, live=_digest_of(live), payload=_digest_of(after),
                                  receipt=rf.sha256 or rf.canonical_digest, size=len(after) if after is not None else 0,
                                  key=rf.fragment_key, reason=reason))
        versions = next((f.engine_state_versions for f in target.files if f.engine_state_versions), ())
        found, unreadable = self._state_versions(root)
        incompatible = bool(unreadable or any(v not in versions for v in found))
        if not versions or incompatible:
            why = "target_compatibility_unknown" if not versions else "state_version_unsupported"
            entries.append(_entry(ENGINE_DIR, "framework", "rollback_conflict", reason=why))
        chain = _canonical_json_digest([current.to_json(), target.to_json()])
        plan = self._receipt_plan(
            repo, "rollback", entries, ROLLBACK_ACTIONS, written="restore_version", found=found,
            blocked=incompatible or not versions, unreadable=unreadable, engine_from=current.engine_version,
            engine_to=target.engine_version, payload_digest=target.payload_digest, receipt_id=current.receipt_id,
            repo_identity=repo.resolved_identity, target_receipt_id=target.receipt_id,
            backup_transaction_id=current.transaction_id, rollback_receipt_digest=chain,
        )
        return plan, candidates

    def _receipt_recovery_row(self, repo: "RepoRecord", txid: str | None = None) -> dict | None:
        """The interrupted uninstall/rollback a recovery restores: the named one, or the single candidate."""
        if txid is not None:
            row = self._storage.get("install_transactions", txid)
            if row is None or row["repo_id"] != repo.repo_id or row["kind"] not in {"uninstall", "rollback"}:
                raise StudioError("transaction_not_found", "no such receipt recovery transaction")
            return row
        if self._install_status(repo.repo_id) != "recovery_required":
            return None
        receipt = self.current_receipt(repo.repo_id)
        candidates = [
            row for row in self._storage.select("install_transactions", {"repo_id": repo.repo_id})
            if row["kind"] in {"uninstall", "rollback"}
            and (row["status"] == "recovery_required"
                 or (row["status"] == "staged" and _step_details(row, "confirm_recovery")))
            and (receipt is None or row.get("prior_receipt_id") == receipt.receipt_id)
        ]
        if len(candidates) > 1:
            raise StudioError("install_recovery_required", "multiple interrupted receipt transactions need review")
        return candidates[0] if candidates else None

    def _preview_receipt_recovery(self, repo: "RepoRecord", txid: str | None = None) -> PreviewPlan | None:
        """Restore plan for an interrupted uninstall/rollback, from its own journal and backups only."""
        row = self._receipt_recovery_row(repo, txid)
        if row is None:
            return None
        txid, kind = row["transaction_id"], row["kind"]
        receipt = self.current_receipt(repo.repo_id)
        entries: list[PreviewEntry] = []
        evidence_digest = None
        try:
            snapshot = self._load_journal(repo, kind, txid)
            evidence_digest = _digest_of(_read_bytes(self._failure_path(C.BACKUP_DIRNAME, txid, _JOURNALS[kind])))
            root = self._uninstall_root(repo, receipt)
            backup = self._failure_path(C.BACKUP_DIRNAME, txid)
            files = {f["path"]: f for f in snapshot["files"]}
            if kind == "rollback":
                for e in snapshot["plan"]["entries"]:
                    files.setdefault(e["path"], {"path": e["path"], "before": e["live_sha256"],
                                                 "after": e["live_sha256"], "unchanged": True})
            for rel, f in sorted(files.items()):
                live, unreadable = _live_bytes(self._safe_path(root, rel))
                digest = _digest_of(live)
                reason, action, size = None, "identical", 0
                if unreadable or digest not in {f["before"], f["after"]}:
                    action, reason = "recovery_conflict", "user_changed_recovery_target"
                if not f.get("unchanged") and f["before"] is not None:
                    saved = _read_bytes(self._safe_path(backup, rel))
                    if saved is None or _digest_of(saved) != f["before"]:
                        action, reason = "recovery_conflict", "recovery_backup_invalid"
                    else:
                        size = len(saved)
                if action != "recovery_conflict" and digest != f["before"]:
                    action = "restore_backup" if f["before"] is not None else "remove_created"
                entries.append(_entry(rel, "framework", action, live=digest, payload=f["before"], receipt=f["before"],
                                      size=size, reason=reason))
        except (StudioError, OSError, ValueError, TypeError):
            entries = [_entry(ENGINE_DIR, "framework", "recovery_conflict", reason="recovery_evidence_invalid")]
        return self._receipt_plan(
            repo, "recovery", entries, _RECOVERY_ACTIONS, written="restore_backup",
            engine_from=receipt.engine_version if receipt else None,
            engine_to=receipt.engine_version if receipt else str(row.get("engine_version") or ""),
            payload_digest=receipt.payload_digest if receipt else "", recovery_transaction_id=txid,
            recovery_kind=kind, recovery_evidence_digest=evidence_digest,
        )

    def _check_receipt_recovery(self, repo: "RepoRecord", digest: str, txid: str | None = None) -> PreviewPlan:
        plan = self._preview_receipt_recovery(repo, txid)
        if plan is None or plan.digest() != digest:
            raise StudioError("install_conflict", "recovery preview changed", details={"reason": "preview_changed"})
        if plan.blocking:
            raise StudioError("install_recovery_required", "recovery evidence or target needs repair",
                              details={"blockers": [e.to_json() for e in plan.blockers]})
        return plan

    def _reserve_receipt_recovery(self, repo: "RepoRecord", plan: PreviewPlan) -> TransactionResult:
        """Record the recovery confirmation on the original row, atomically with a fresh check."""
        txid = plan.recovery_transaction_id
        with self._storage._txn():
            self._check_receipt_recovery(repo, plan.digest(), txid)
            row = self._receipt_recovery_row(repo, txid)
            steps = list(TransactionResult.from_row(row).steps)
            confirmations = [s.detail for s in steps if s.name == "confirm_recovery"]
            if row["status"] == "staged" and confirmations and confirmations[-1]["plan_digest"] == plan.digest():
                return self.transaction(txid)
            if row["status"] != "recovery_required":
                raise StudioError("install_recovery_required", "the transaction is already being recovered")
            steps.append(StepRecord("confirm_recovery", self._clock.iso(), self._clock.iso(), True, {
                "plan_digest": plan.digest(), "recovery_kind": row["kind"], "prior_error": row.get("error")}))
            self._storage.update("install_transactions", txid, {
                "status": "staged", "finished_at": None, "error": None, "steps_json": _steps_json(steps)})
            return self.transaction(txid)

    # ---- receipts and history ---- #

    def current_receipt(self, repo_id: str) -> Receipt | None:
        rows = self._storage.select("install_receipts", {"repo_id": repo_id, "status": "current"}, limit=1)
        return Receipt.from_row(rows[0]) if rows else None

    def receipts(self, repo_id: str) -> list[Receipt]:
        rows = self._storage.select("install_receipts", {"repo_id": repo_id}, order_by="committed_at DESC")
        return [Receipt.from_row(row) for row in rows]

    def receipt(self, receipt_id: str) -> Receipt:
        row = self._storage.get("install_receipts", receipt_id)
        if row is None:
            raise StudioError("receipt_not_found", "no such receipt", details={"receipt_id": receipt_id})
        return Receipt.from_row(row)

    def transaction(self, transaction_id: str) -> TransactionResult:
        row = self._storage.get("install_transactions", transaction_id)
        if row is None:
            raise StudioError("transaction_not_found", "no such transaction",
                              details={"transaction_id": transaction_id})
        return self._as_result(row)

    def transactions(self, repo_id: str, limit: int = 20) -> list[TransactionResult]:
        # The id tiebreak keeps "the last N" exact when two transactions share a second.
        rows = self._storage.select("install_transactions", {"repo_id": repo_id},
                                    order_by="started_at DESC, transaction_id DESC", limit=limit)
        return [self._as_result(row) for row in rows]

    def _as_result(self, row: Mapping[str, Any]) -> TransactionResult:
        """A transaction row plus its standing receipt (derived, so a rolled-back one never points at one)."""
        txid = str(row.get("transaction_id") or "")
        receipts = self._storage.select("install_receipts", {"transaction_id": txid, "status": "current"}, limit=1)
        return TransactionResult.from_row({**row, "receipt_id": receipts[0]["receipt_id"] if receipts else None},
                                          repo_install_status=self._install_status(str(row.get("repo_id") or "")))

    def drift(self, repo: "RepoRecord", receipt: Receipt) -> tuple[PreviewEntry, ...]:
        """Receipt-owned paths whose live bytes no longer match the receipt, fragments included (only this module
        knows the strategy behind a canonical digest)."""
        root = Path(repo.canonical_path)
        out: list[PreviewEntry] = []
        for rf in receipt.files:
            expected = rf.sha256 if rf.kind == "file" else rf.canonical_digest
            try:
                target = security.resolve_inside(root, rf.path)
            except StudioError:
                out.append(_entry(rf.path, rf.ownership, "conflict", receipt=expected, key=rf.fragment_key))
                continue
            if rf.kind == "fragment":
                spec = (rf.uninstall or {}).get("spec") or {}
                handler = self._receipt_handler(rf) if "managedMapEntries" in spec else self._handler(rf.path)
                digest = handler.live_fragment(_read_bytes(target)).digest
                if digest != rf.canonical_digest:
                    out.append(_entry(rf.path, rf.ownership, "merge_conflict", live=digest, receipt=expected,
                                      key=rf.fragment_key))
                continue
            sha = _digest_of(_read_bytes(target))
            if sha != rf.sha256:
                action = ("create" if sha is None else
                          "engine_modified" if rf.ownership == "framework-mutable" else "owned_modified")
                out.append(_entry(rf.path, rf.ownership, action, live=sha, receipt=expected, key=rf.fragment_key))
        return tuple(out)

    def _install_status(self, repo_id: str) -> str:
        row = self._storage.get("repos", repo_id)
        return str((row or {}).get("install_status") or "not_installed")

    # ---- transactions ---- #

    async def cancel(self, repo: "RepoRecord", *, transaction_id: str) -> TransactionResult:
        """Request cooperative cancellation: the worker finishes its current atomic operation, then restores and
        verifies its before-image. A committed or settling transaction is returned unchanged."""
        result = await asyncio.to_thread(self._request_cancel, repo, transaction_id)
        await self._publish(transaction_id, repo.repo_id, result.status)
        return result

    def _request_cancel(self, repo: "RepoRecord", txid: str) -> TransactionResult:
        with self._storage._txn():
            row = self._storage.get("install_transactions", txid)
            if row is None or row["repo_id"] != repo.repo_id:
                raise StudioError("transaction_not_found", "no such repository transaction")
            if row.get("resolved_repo_identity") != repo.resolved_identity:
                raise StudioError("install_conflict", "transaction repository identity changed")
            if row["kind"] not in CANCELLABLE_KINDS:
                raise StudioError("cancel_not_safe", "this transaction kind cannot be cancelled")
            if row["status"] not in _SETTLED_STATUS and row["status"] != "rolling_back":
                self._storage.update("install_transactions", txid, {"error": "cancel_requested"})
            return self.transaction(txid)

    def _raise_if_cancelled(self, txid: str) -> None:
        row = self._storage.get("install_transactions", txid)
        if row and row["kind"] in CANCELLABLE_KINDS and row.get("error") == "cancel_requested":
            raise _InstallCancelled()

    async def begin(self, repo: "RepoRecord", kind: str, *, plan_digest: str,
                    target_receipt_id: str | None = None) -> TransactionResult:
        """Run every refusal, then insert the row, so a route answers 409 before 202 and the id it returns is
        already pollable. ``run`` repeats the checks when called on its own."""
        if kind == "recovery" and await asyncio.to_thread(self._preview_receipt_recovery, repo) is not None:
            plan = await asyncio.to_thread(self._check_receipt_recovery, repo, plan_digest)
            await self._refuse_if_busy(repo)
            result = await asyncio.to_thread(self._reserve_receipt_recovery, repo, plan)
            await self._publish(result.transaction_id, repo.repo_id, result.status)
            return result
        if kind in ("uninstall", "rollback"):
            plan, _ = await asyncio.to_thread(self._confirmed_plan, repo, kind, plan_digest, target_receipt_id)
            insert = lambda txid: self._insert_receipt_op(txid, repo, plan)  # noqa: E731
        else:
            await asyncio.to_thread(self._preflight_transaction, repo, kind, plan_digest)
            insert = lambda txid: self._insert_transaction(txid, repo, kind)  # noqa: E731
        await self._refuse_if_busy(repo)
        txid = self._ids.new("tx")
        await asyncio.to_thread(insert, txid)
        await self._publish(txid, repo.repo_id, "staged")
        return await asyncio.to_thread(self.transaction, txid)

    async def _refuse_if_busy(self, repo: "RepoRecord") -> None:
        """Answer 409 at once while the repository is leased; advisory, ``acquire_admin`` is the authority."""
        if not repo.resolved_identity:
            return
        record = await self._scheduler.get(repo.resolved_identity)
        if record is not None:
            raise LeaseHeld("repository is busy", details={
                "kind": record.kind, "repo_id": repo.repo_id, "operation_type": record.operation_type})

    async def run(self, repo: "RepoRecord", kind: str, *, plan_digest: str, transaction_id: str | None = None,
                  target_receipt_id: str | None = None) -> TransactionResult:
        """Install, upgrade or recover: stage and verify a private copy, lease, back up, write, merge, validate, then
        commit the receipt; a failure from ``backup`` on rolls back and re-proves the old receipt. Uninstall,
        rollback and their recovery are dispatched to the receipt-op runner."""
        if kind == "recovery":
            if transaction_id is None:
                if await asyncio.to_thread(self._preview_receipt_recovery, repo) is not None:
                    pending = await self.begin(repo, kind, plan_digest=plan_digest)
                    return await self._run_receipt_recovery(repo, pending.transaction_id, plan_digest)
            else:
                row = await asyncio.to_thread(self._storage.get, "install_transactions", transaction_id)
                if row is not None and row["kind"] in {"uninstall", "rollback"}:
                    return await self._run_receipt_recovery(repo, transaction_id, plan_digest)
        if kind == "uninstall":
            return await self.uninstall(repo, confirm_digest=plan_digest, transaction_id=transaction_id)
        if kind == "rollback":
            return await self.rollback(repo, confirm_digest=plan_digest, target_receipt_id=target_receipt_id,
                                       transaction_id=transaction_id)
        if transaction_id is None:
            await asyncio.to_thread(self._preflight_transaction, repo, kind, plan_digest)
            transaction_id = self._ids.new("tx")
            await asyncio.to_thread(self._insert_transaction, transaction_id, repo, kind)
        else:
            row = await asyncio.to_thread(self._storage.get, "install_transactions", transaction_id)
            if row is None or row["repo_id"] != repo.repo_id or row["kind"] != kind:
                raise StudioError("transaction_not_found", "no such repository transaction")
            if row.get("resolved_repo_identity") != repo.resolved_identity:
                raise StudioError("install_conflict", "transaction repository identity changed")
            if row["status"] in _SETTLED_STATUS:
                return await asyncio.to_thread(self.transaction, transaction_id)
            if row["status"] != "staged":
                raise StudioError("install_recovery_required", "an interrupted install needs recovery")
        txid = transaction_id
        journal = self._journal(txid)
        steps: list[StepRecord] = []
        grant: "LeaseGrant | None" = None
        status, error, failed_dir = "staged", None, None
        try:
            plan = await asyncio.to_thread(self._plan_for_run, repo, kind, plan_digest)
            await asyncio.to_thread(self._open_journal, journal, repo, txid)
            await self._step(steps, txid, repo, "stage_payload", self._stage_payload, journal)
            await self._step(steps, txid, repo, "verify_staging", self._verify_staging, journal)
            grant = await self._lease_step(steps, txid, repo, kind)
            journal.lease_generation = grant.generation
            if kind == "upgrade" and plan.engine_from == plan.engine_to:
                # A same-version refresh: recheck receipt authority, plan and identity under the lease.
                await asyncio.to_thread(self._registry.require_current_identity, repo)
                plan = await asyncio.to_thread(self._plan_for_run, repo, kind, plan_digest)
            for name in ("backup", "write_files", "merge_fragments", "post_write_validate", "commit_receipt"):
                await self._step(steps, txid, repo, name, getattr(self, f"_{name}"), journal, plan, repo)
            status = "committed"
        except Exception as exc:  # noqa: BLE001 - every failure mode has an outcome
            # Not BaseException: a shutdown cancellation cannot complete a rollback and needs none, because the
            # old receipt still describes the tree; drift detection and recovery repair it.
            error = self._error_text(exc)
            if steps and steps[-1].name in ROLLBACK_TRIGGERING_STEPS:
                status, failed_dir = await self._rollback(steps, txid, repo, journal, error)
            else:
                status = "failed"
                failed_dir = await asyncio.to_thread(self._archive_failure, journal, steps, error)
        finally:
            if grant is not None:
                await self._release_step(steps, txid, repo, grant)
        if status == "committed":
            await asyncio.to_thread(self._cleanup_staging, journal)
        return await self._finish(txid, repo, status, steps, journal, error, failed_dir)

    # ---- uninstall and one-hop rollback ---- #

    async def uninstall(self, repo: "RepoRecord", *, confirm_digest: str,
                        transaction_id: str | None = None) -> TransactionResult:
        """Remove only confirmed receipt-owned content (no engine run, payload read or workflow write). Retrying a
        terminal id returns its outcome; an interrupted removal needs ``recover_uninstall``."""
        return await self._receipt_op(repo, "uninstall", confirm_digest, transaction_id, None)

    async def rollback(self, repo: "RepoRecord", *, confirm_digest: str, target_receipt_id: str | None = None,
                       transaction_id: str | None = None) -> TransactionResult:
        """Restore the immediate prior engine from verified backups under a new receipt; workflow files never
        enter the plan, and a failed attempt restores the current engine from its own before-image."""
        return await self._receipt_op(repo, "rollback", confirm_digest, transaction_id, target_receipt_id)

    async def recover_uninstall(self, repo: "RepoRecord", *, transaction_id: str,
                                confirm_digest: str) -> TransactionResult:
        """Restore an interrupted uninstall under a fresh admin lease, using its original confirmation."""
        return await self._recover_receipt_operation(repo, "uninstall", transaction_id, confirm_digest)

    async def recover_rollback(self, repo: "RepoRecord", *, transaction_id: str,
                               confirm_digest: str) -> TransactionResult:
        """Undo an interrupted version rollback under a fresh admin lease, using its own journal."""
        return await self._recover_receipt_operation(repo, "rollback", transaction_id, confirm_digest)

    def _insert_receipt_op(self, txid: str, repo: "RepoRecord", plan: PreviewPlan) -> None:
        self._insert_transaction(txid, repo, plan.kind)
        detail = {"confirm_digest": plan.digest(), "receipt_id": plan.receipt_id,
                  **({"target_receipt_id": plan.target_receipt_id} if plan.kind == "rollback" else {}),
                  "repo_identity": plan.repo_identity}
        self._storage.update("install_transactions", txid, {
            "engine_version": plan.engine_to if plan.kind == "rollback" else plan.engine_from,
            "payload_digest": plan.payload_digest,
            "steps_json": [StepRecord(f"confirm_{plan.kind}", self._clock.iso(), self._clock.iso(), True,
                                      detail).to_json()],
        })

    def _receipt_row(self, repo: "RepoRecord", kind: str, txid: str, digest: str) -> tuple[dict, dict]:
        """A reserved uninstall/rollback row and its confirmation, refusing any mismatch."""
        self._failure_path(C.BACKUP_DIRNAME, txid)
        row = self._storage.get("install_transactions", txid)
        if row is None:
            raise StudioError("transaction_not_found", f"no such {kind} transaction")
        confirmation = next(iter(_step_details(row, f"confirm_{kind}")), {})
        if (row.get("kind") != kind or row.get("repo_id") != repo.repo_id
                or row.get("resolved_repo_identity") != repo.resolved_identity
                or not digest or confirmation.get("confirm_digest") != digest):
            raise StudioError("install_conflict", f"{kind} transaction or confirmation does not match")
        return row, confirmation

    async def _receipt_op(self, repo: "RepoRecord", kind: str, digest: str, txid: str | None,
                          target_receipt_id: str | None) -> TransactionResult:
        """Back up, journal, apply, validate and commit an uninstall or a rollback under the admin lease."""
        if txid is None:
            pending = await self.begin(repo, kind, plan_digest=digest, target_receipt_id=target_receipt_id)
            txid = pending.transaction_id
        row, confirmation = await asyncio.to_thread(self._receipt_row, repo, kind, txid, digest)
        if kind == "rollback" and target_receipt_id not in (None, confirmation["target_receipt_id"]):
            raise StudioError("install_conflict", "rollback target does not match confirmation")
        if row["status"] in _SETTLED_STATUS:
            return await asyncio.to_thread(self.transaction, txid)
        if row["status"] != "staged":
            raise StudioError("install_recovery_required", f"an interrupted {kind} needs recovery")
        steps = list(TransactionResult.from_row(row).steps)
        if any(s.name == "confirm_recovery" for s in steps):
            raise StudioError("install_recovery_required", "this transaction is reserved for restoration")
        await self._refuse_if_busy(repo)
        journal = self._journal(txid, checked=True)
        grant: "LeaseGrant | None" = None
        snapshot = None
        status, error, failed_dir = "failed", None, None
        try:
            grant = await self._lease_step(steps, txid, repo, UNINSTALL_LEASE_OPERATION, receipt_op=True)
            if grant is None:
                return await asyncio.to_thread(self.transaction, txid)
            plan, candidates = await asyncio.to_thread(self._confirmed_plan, repo, kind, digest,
                                                       confirmation.get("target_receipt_id"))
            await self._step(steps, txid, repo, "backup", self._backup_receipt_op, journal, plan, candidates, repo)
            snapshot = await asyncio.to_thread(self._load_journal, repo, kind, txid)
            journal.backups = {f["path"]: f["before"] for f in snapshot["files"]}
            applied = ((("remove_files", "remove"), ("remove_fragments", "remove_fragment")) if kind == "uninstall"
                       else (("restore_version", None),))
            for name, action in applied:
                await self._step(steps, txid, repo, name, self._apply_receipt_op, snapshot, repo, action)
            await self._step(steps, txid, repo, f"post_{kind}_validate", self._validate_receipt_op, snapshot, repo)
            detail = await self._step(steps, txid, repo, f"commit_{kind}", self._commit_receipt_op, snapshot, repo,
                                      steps)
            journal.receipt_id = detail.get("receipt_id")
            status = "committed"
        except Exception as exc:  # every reversible failure has a durable outcome
            current = await asyncio.to_thread(self._storage.get, "install_transactions", txid)
            if current["status"] == "committed":  # the atomic record commit is the point of no return
                status, steps = "committed", list(TransactionResult.from_row(current).steps)
            else:
                if grant is None and current["status"] != "staged":
                    raise  # this caller never claimed the row; another invocation owns its outcome
                if isinstance(exc, LeaseHeld):
                    held = await self._scheduler.get(repo.resolved_identity)
                    if held is not None and held.transaction_id == txid:
                        raise  # another invocation of this same reserved id holds the lease
                error = self._error_text(exc)
                if snapshot is not None:
                    status = await self._restore_receipt_op(snapshot, repo, steps, error)
                failed_dir = await asyncio.to_thread(self._archive_failure, journal, steps, error)
        finally:
            if grant is not None:
                await self._release_step(steps, txid, repo, grant)
        return await self._finish(txid, repo, status, steps, journal, error, failed_dir)

    def _backup_receipt_op(self, journal: _Journal, plan: PreviewPlan, candidates: dict[str, bytes | None] | None,
                           repo: "RepoRecord") -> dict:
        """Back up each path the plan changes (a rollback also stores the bytes it will write), then a journal
        binding them to the confirmed plan and the prior repository row."""
        kind = plan.kind
        receipt = self.receipt(plan.receipt_id)
        root = self._uninstall_root(repo, receipt) if kind == "uninstall" else None
        backup = self._failure_path(C.BACKUP_DIRNAME, journal.txid)
        if backup.exists():
            raise StudioError("install_conflict", f"{kind} backup already exists; recover it first")
        files = []
        for entry in plan.entries:
            if entry.action not in _APPLIED[kind]:
                continue
            if kind == "rollback":
                root = self._uninstall_root(repo, receipt)
            path = self._safe_path(root, entry.path)
            before, unreadable = _live_bytes(path)
            if unreadable or _digest_of(before) != entry.live_sha256:
                raise StudioError("install_conflict", f"{kind} target changed before backup")
            if before is not None:
                _durable_write(self._safe_path(backup, entry.path), before)
            if kind == "rollback":
                after = candidates[entry.path]
                if _digest_of(after) != entry.payload_sha256:
                    raise StudioError("install_conflict", "rollback candidate changed before backup")
                if after is not None:
                    _durable_write(self._safe_path(backup, "rollback-after/" + entry.path), after)
            files.append({"path": entry.path, "action": entry.action, "before": entry.live_sha256,
                          "after": entry.payload_sha256,
                          "mode": stat.S_IMODE(path.stat().st_mode) if before is not None else 0o644})
        row = self._storage.get("repos", repo.repo_id)
        snapshot = {
            "version": 1, "transaction_id": journal.txid, "repo_id": repo.repo_id, "root": repo.canonical_path,
            "repo_identity": repo.resolved_identity, "receipt_id": plan.receipt_id,
            **({"target_receipt_id": plan.target_receipt_id} if kind == "rollback" else {}),
            "confirm_digest": plan.digest(), "plan": plan._digest_data(), "files": files,
            "prior_repo": {key: row.get(key) for key in _REPO_FIELDS},
        }
        path = self._failure_path(C.BACKUP_DIRNAME, journal.txid, _JOURNALS[kind])
        _durable_write(path, (json.dumps(snapshot, indent=2) + "\n").encode())
        return {"backed_up": len(files), f"{kind}_journal": str(path)}

    def _load_journal(self, repo: "RepoRecord", kind: str, txid: str) -> dict:
        """The operation's journal, proved against its confirmation, the receipt chain and the confirmed plan."""
        data = _read_bytes(self._failure_path(C.BACKUP_DIRNAME, txid, _JOURNALS[kind]))
        if data is None and kind == "uninstall":
            raise StudioError("install_recovery_required", "uninstall journal is missing")
        try:
            snapshot = json.loads(data or b"")
            row, confirmation = self._receipt_row(repo, kind, txid, snapshot["confirm_digest"])
            if kind == "rollback":
                root, current, target = self._rollback_context(repo, snapshot["target_receipt_id"], recovery=True)
                owned = {**current.by_path(), **target.by_path()}
                chain_ok = (confirmation["target_receipt_id"] == target.receipt_id
                            and snapshot["plan"]["rollback_receipt_digest"]
                            == _canonical_json_digest([current.to_json(), target.to_json()]))
            else:
                current = self.current_receipt(repo.repo_id)
                root, owned, chain_ok = Path(repo.canonical_path), (current.by_path() if current else {}), True
            if (not chain_ok or current is None or snapshot["version"] != 1 or snapshot["transaction_id"] != txid
                    or snapshot["repo_id"] != repo.repo_id or snapshot["root"] != repo.canonical_path
                    or snapshot["repo_identity"] != repo.resolved_identity
                    or snapshot["receipt_id"] != current.receipt_id or row["prior_receipt_id"] != current.receipt_id
                    or _canonical_json_digest(snapshot["plan"]) != snapshot["confirm_digest"]
                    or set(snapshot["prior_repo"]) != set(_REPO_FIELDS)
                    or snapshot["prior_repo"]["receipt_version"] != current.receipt_id):
                raise ValueError(f"{kind} journal does not match its confirmed receipt chain")
            if kind == "uninstall":
                self._uninstall_root(repo, current)
            expected = {e["path"]: (e["action"], e["live_sha256"], e["payload_sha256"])
                        for e in snapshot["plan"]["entries"] if e["action"] in _APPLIED[kind]}
            actual = {f["path"]: (f["action"], f["before"], f["after"]) for f in snapshot["files"]}
            if actual != expected or len(actual) != len(snapshot["files"]):
                raise ValueError(f"{kind} journal does not cover the confirmed plan")
            for f in snapshot["files"]:
                rf = owned.get(f["path"])
                if (rf is None or not self._uninstall_owned_path(rf)
                        or (kind == "uninstall"
                            and (f["action"] != ("remove" if rf.kind == "file" else "remove_fragment")
                                 or not isinstance(f["before"], str) or len(f["before"]) != 64))
                        or any(v is not None and (not isinstance(v, str) or len(v) != 64)
                               for v in (f["before"], f["after"]))
                        or not isinstance(f["mode"], int) or not 0 <= f["mode"] <= 0o7777):
                    raise ValueError(f"invalid {kind} journal file")
                self._safe_path(root, f["path"])
            return snapshot
        except (KeyError, TypeError, ValueError) as exc:
            raise StudioError("install_recovery_required", f"invalid {kind} journal") from exc

    def _apply_receipt_op(self, snapshot: dict, repo: "RepoRecord", action: str | None) -> dict:
        """Apply journaled changes path by path, re-proving root and before-bytes at every path."""
        kind = "rollback" if "target_receipt_id" in snapshot else "uninstall"
        receipt = self.receipt(snapshot["receipt_id"])
        owned = receipt.by_path()
        if kind == "uninstall":
            self._uninstall_root(repo, receipt)
        changed = 0
        for f in snapshot["files"]:
            if action is not None and f["action"] != action:
                continue
            rel = f["path"]
            self._fault(f"before:{kind}_path:{rel}")
            target = self._safe_path(self._uninstall_root(repo, receipt), rel)
            live, unreadable = _live_bytes(target)
            if unreadable or _digest_of(live) != f["before"]:
                raise StudioError("install_conflict", f"{kind} target changed after backup", details={"path": rel})
            after = None
            if kind == "rollback" and f["after"] is not None:
                after_dir = self._failure_path(C.BACKUP_DIRNAME, snapshot["transaction_id"], "rollback-after")
                after = _read_bytes(self._safe_path(after_dir, rel))
                if after is None or _digest_of(after) != f["after"]:
                    raise StudioError("install_recovery_required", "rollback candidate is missing or corrupt")
            elif kind == "uninstall":
                if action == "remove_fragment":
                    after, reason = self._remove_receipt_fragment(owned[rel], live)
                    if reason:
                        raise StudioError("install_conflict", "fragment ownership changed after preview")
                if _digest_of(after) != f["after"]:
                    raise StudioError("install_conflict", "uninstall result changed after preview")
            if after is None:
                target.unlink(missing_ok=kind == "rollback")
            else:
                _durable_write(target, after, f["mode"])
            changed += 1
            self._fault(f"after:{kind}_path:{rel}")
        return {"restored" if kind == "rollback" else "changed": changed}

    def _validate_receipt_op(self, snapshot: dict, repo: "RepoRecord") -> dict:
        """Every journaled path holds its planned bytes; a rollback also proves every file of the old receipt,
        workflow-state compatibility and the restored engine's own version."""
        receipt = self.receipt(snapshot["receipt_id"])
        root = self._uninstall_root(repo, receipt)
        for f in snapshot["files"]:
            target = self._safe_path(root, f["path"])
            irregular = target.exists() and not security.is_regular_file(target)
            if irregular or _digest_of(_read_bytes(target)) != f["after"]:
                raise StudioError("install_conflict", "uninstall result failed verification",
                                  details={"path": f["path"]})
        verified = {"verified": len(snapshot["files"])}
        if "target_receipt_id" not in snapshot:
            return verified
        target = self.receipt(snapshot["target_receipt_id"])
        expected = {e["path"]: e["payload_sha256"] for e in snapshot["plan"]["entries"]}
        for rf in target.files:  # identical paths join the new receipt too: an edit there must not slip through
            live, unreadable = _live_bytes(self._safe_path(self._uninstall_root(repo, receipt), rf.path))
            if unreadable or _digest_of(live) != expected[rf.path]:
                raise StudioError("install_conflict", "old engine receipt would claim changed content",
                                  details={"path": rf.path})
        versions = next((f.engine_state_versions for f in target.files if f.engine_state_versions), ())
        found, unreadable_states = self._state_versions(Path(repo.canonical_path))
        if not versions or unreadable_states or any(v not in versions for v in found):
            raise StudioError("state_version_migration_unconfirmed", "old engine cannot write current state")
        result = self._engine.run_sync("utility.version", Path(repo.canonical_path), ENGINE_DIR)
        if result.exit_code != 0 or target.engine_version not in (result.stdout or ""):
            raise StudioError("install_conflict", "restored engine failed version validation")
        return {**verified, "engine_version": target.engine_version}

    def _commit_receipt_op(self, snapshot: dict, repo: "RepoRecord", steps: list[StepRecord]) -> dict:
        """Receipt, repository and transaction rows in one SQLite transaction: the point of no return."""
        self._validate_receipt_op(snapshot, repo)
        txid, receipt_id = snapshot["transaction_id"], snapshot["receipt_id"]
        rollback = "target_receipt_id" in snapshot
        if rollback:
            self._load_journal(repo, "rollback", txid)
            target = self.receipt(snapshot["target_receipt_id"])
            new_id, now = self._ids.new("rc"), self._clock.iso()
            detail = {"receipt_id": new_id, "target_receipt_id": target.receipt_id}
        else:
            health = self._registry.detect_install(replace(repo, install_status="not_installed", receipt_version=None,
                                                           installed_engine_version=None, engine_dir=None), {})
            now = self._clock.iso()
            detail = {"removed_receipt_id": receipt_id}
        completed = [*steps[:-1], replace(steps[-1], ok=True, finished_at=now, detail=detail)]
        with self._storage._txn():
            current = self.current_receipt(repo.repo_id)
            if current is None or current.receipt_id != receipt_id:
                raise StudioError("install_conflict",
                                  f"current receipt changed during {'rollback' if rollback else 'uninstall'}")
            if rollback:
                self._insert_receipt(new_id, repo, target.engine_version, target.payload_digest, current.receipt_id,
                                     txid, target.files, now)
                self._storage.update("install_receipts", current.receipt_id, {"status": "superseded"})
                self._storage.update("repos", repo.repo_id, {
                    "install_status": "installed", "installed_engine_version": target.engine_version,
                    "engine_dir": ENGINE_DIR, "receipt_version": new_id})
            else:
                self._storage.update("install_receipts", current.receipt_id, {"status": "uninstalled"})
                self._storage.update("repos", repo.repo_id, {
                    "install_status": health.status, "installed_engine_version": health.engine_version,
                    "engine_dir": health.engine_dir, "receipt_version": None})
            self._storage.update("install_transactions", txid, {
                "status": "committed", "finished_at": now, "error": None, "steps_json": _steps_json(completed)})
        return detail

    async def _restore_receipt_op(self, snapshot: dict, repo: "RepoRecord", steps: list[StepRecord],
                                  error: str | None) -> str:
        """Put every journaled path back to its before-image and re-prove it, else ``recovery_required``."""
        txid = snapshot["transaction_id"]
        await asyncio.to_thread(self._storage.update, "install_transactions", txid,
                                {"status": "rolling_back", "error": error})
        await self._publish(txid, repo.repo_id, "rolling_back")
        try:
            await self._step(steps, txid, repo, "restore_backups", self._restore_journaled, snapshot, repo)
            await self._step(steps, txid, repo, "reverify_old_receipt", self._verify_journaled, snapshot, repo)
        except Exception as exc:
            steps[-1] = replace(steps[-1], ok=False, finished_at=self._clock.iso(),
                                detail={"error": self._error_text(exc)})
            await self._mark_recovery_required(repo.repo_id)
            return "recovery_required"
        return "rolled_back"

    def _restore_journaled(self, snapshot: dict, repo: "RepoRecord") -> dict:
        receipt = self.receipt(snapshot["receipt_id"])
        self._uninstall_root(repo, receipt)
        backup = self._failure_path(C.BACKUP_DIRNAME, snapshot["transaction_id"])
        restored = 0
        for f in snapshot["files"]:
            target = self._safe_path(self._uninstall_root(repo, receipt), f["path"])
            live, unreadable = _live_bytes(target)
            if unreadable or (target.exists() and not security.is_regular_file(target)):
                raise StudioError("install_recovery_required", "cannot inspect uninstall recovery target")
            digest = _digest_of(live)
            if digest == f["before"]:
                continue
            if digest != f["after"]:  # never overwrite an edit made after the operation
                raise StudioError("install_recovery_required", "user changed an uninstall recovery target",
                                  details={"path": f["path"]})
            if f["before"] is None:
                target.unlink(missing_ok=True)
            else:
                data = _read_bytes(self._safe_path(backup, f["path"]))
                if data is None or _digest_of(data) != f["before"]:
                    raise StudioError("install_recovery_required", "uninstall backup is missing or corrupt")
                _durable_write(target, data, f["mode"])
            restored += 1
        return {"restored": restored}

    def _verify_journaled(self, snapshot: dict, repo: "RepoRecord") -> dict:
        root = self._uninstall_root(repo, self.receipt(snapshot["receipt_id"]))
        for f in snapshot["files"]:
            if _digest_of(_read_bytes(self._safe_path(root, f["path"]))) != f["before"]:
                raise StudioError("install_recovery_required", "uninstall rollback verification failed")
        for entry in snapshot["plan"]["entries"] if "target_receipt_id" in snapshot else ():
            live, unreadable = _live_bytes(self._safe_path(root, entry["path"]))
            if unreadable or _digest_of(live) != entry["live_sha256"]:
                raise StudioError("install_recovery_required", "prior engine changed during rollback",
                                  details={"path": entry["path"]})
        with self._storage._txn():
            current = self.current_receipt(repo.repo_id)
            if current is None or current.receipt_id != snapshot["receipt_id"]:
                raise StudioError("install_recovery_required", "uninstall rollback receipt changed")
            self._storage.update("repos", repo.repo_id, snapshot["prior_repo"])
        return {"verified": len(snapshot["files"])}

    async def _run_receipt_recovery(self, repo: "RepoRecord", txid: str, plan_digest: str) -> TransactionResult:
        """The application recovery flow for an interrupted uninstall/rollback: reserve, then restore."""
        row = await asyncio.to_thread(self._receipt_recovery_row, repo, txid)
        confirmations = _step_details(row, "confirm_recovery")
        if not confirmations:
            plan = await asyncio.to_thread(self._check_receipt_recovery, repo, plan_digest, txid)
            await self._refuse_if_busy(repo)
            await asyncio.to_thread(self._reserve_receipt_recovery, repo, plan)
        elif confirmations[-1].get("plan_digest") != plan_digest:
            raise StudioError("install_conflict", "recovery reservation does not match confirmation")
        original = next(iter(_step_details(row, f"confirm_{row['kind']}")), {}).get("confirm_digest")
        try:
            return await self._recover_receipt_operation(repo, row["kind"], txid, original, recovery_digest=plan_digest)
        except LeaseHeld:  # a lease taken after the advisory check must leave a retryable durable outcome
            current = await asyncio.to_thread(self._storage.get, "install_transactions", txid)
            held = await self._scheduler.get(repo.resolved_identity)
            if current["status"] == "staged" and not (held and held.transaction_id == txid):
                await asyncio.to_thread(self._storage.update, "install_transactions", txid, {
                    "status": "recovery_required", "error": "repo_busy", "finished_at": self._clock.iso()})
                await self._publish(txid, repo.repo_id, "recovery_required")
                return await asyncio.to_thread(self.transaction, txid)
            raise

    async def _recover_receipt_operation(self, repo: "RepoRecord", kind: str, txid: str, confirm_digest: str, *,
                                         recovery_digest: str | None = None) -> TransactionResult:
        row, _ = await asyncio.to_thread(self._receipt_row, repo, kind, txid, confirm_digest)
        if row["status"] in {"rolled_back", "failed"}:
            return await asyncio.to_thread(self.transaction, txid)
        if row["status"] == "committed":
            raise StudioError("install_conflict", "a committed operation is not a recovery candidate")
        await self._refuse_if_busy(repo)
        journal = self._journal(txid, checked=True)
        steps = list(TransactionResult.from_row(row).steps)
        grant: "LeaseGrant | None" = None
        error, status, failed_dir = row.get("error"), "recovery_required", row.get("failed_dir")
        try:
            snapshot = await asyncio.to_thread(self._load_journal, repo, kind, txid)
            journal.backups = {f["path"]: f["before"] for f in snapshot["files"]}
            grant = await self._lease_step(steps, txid, repo, UNINSTALL_LEASE_OPERATION, receipt_op=True, recovery=True)
            if grant is None:
                return await asyncio.to_thread(self.transaction, txid)
            if recovery_digest is not None:
                await asyncio.to_thread(self._check_receipt_recovery, repo, recovery_digest, txid)
            status = await self._restore_receipt_op(snapshot, repo, steps, error)
            failed_dir = await asyncio.to_thread(self._archive_failure, journal, steps, error)
        except Exception as exc:
            if grant is None:
                held = await self._scheduler.get(repo.resolved_identity)
                if held is not None and held.transaction_id == txid:
                    raise
            error = self._error_text(exc)
            await self._mark_recovery_required(repo.repo_id)
            failed_dir = await asyncio.to_thread(self._archive_failure, journal, steps, error)
        finally:
            if grant is not None:
                await self._release_step(steps, txid, repo, grant)
        return await self._finish(txid, repo, status, steps, journal, error, failed_dir)

    # ---- startup settlement and preconditions ---- #

    async def settle_abandoned(self) -> list[str]:
        """Settle transactions whose process died: ``staged``/``leased`` → ``failed``, later ones (or a journaled
        uninstall/rollback) → ``recovery_required``, unfinished commits finished; a row whose admin lease this
        process holds is live. Returns the ids settled."""
        rows = await asyncio.to_thread(self._storage.select, "install_transactions")
        candidates = [row for row in rows
                      if str(row.get("status") or "") not in _SETTLED_STATUS or row.get("status") == "committed"]
        if not candidates:
            return []
        mine = {rec.transaction_id for rec in await self._scheduler.list() if rec.kind == "admin"} - {None}
        settled: list[str] = []
        cleanup_failures: list[tuple[str, StudioError]] = []
        now = self._clock.iso()
        for row in candidates:
            txid, status, repo_id = (str(row.get(key) or "") for key in ("transaction_id", "status", "repo_id"))
            if not txid or txid in mine:
                continue
            if status == "committed":
                try:
                    was_finished = bool(row.get("finished_at"))
                    if not await asyncio.to_thread(self._finish_abandoned_commit, txid, now, was_finished):
                        continue
                except StudioError as exc:  # reported after the other candidates settle
                    cleanup_failures.append((txid, exc))
                    continue
                outcome, severity, extra = "committed", "info", {}
            else:
                wrote_nothing = status in _ABANDONED_WROTE_NOTHING
                if row.get("kind") in {"uninstall", "rollback"} and wrote_nothing:
                    # Recovery can die after taking its lease: a durable journal means bytes may have moved.
                    path = self._failure_path(C.BACKUP_DIRNAME, txid, _JOURNALS[row["kind"]])
                    wrote_nothing = not await asyncio.to_thread(path.exists)
                outcome = "failed" if wrote_nothing else "recovery_required"
                # Archive first, so an interrupted settle retries without losing its candidate or evidence.
                failed_dir = await asyncio.to_thread(self._archive_failure, self._journal(txid),
                                                     list(TransactionResult.from_row(row).steps), _ABANDONED_ERROR)
                if not wrote_nothing and repo_id:  # durable before the row settles, so it stays retryable
                    await self._mark_recovery_required(repo_id)
                await asyncio.to_thread(self._storage.update, "install_transactions", txid, {
                    "status": outcome, "finished_at": now, "error": _ABANDONED_ERROR, "failed_dir": failed_dir})
                severity, extra = ("attention" if wrote_nothing else "critical"), {"error": _ABANDONED_ERROR}
            await self._activity.record(kind="install.transaction", severity=severity, repo_id=repo_id, params={
                "transaction_id": txid, "status": outcome, **extra, "abandoned_in_status": status})
            await self._publish(txid, repo_id, outcome)
            settled.append(txid)
        if cleanup_failures:
            txid, first = cleanup_failures[0]
            raise StudioError(first.code, first.message, details={
                **first.details, "transaction_id": txid,
                "committed_cleanup_failures": [{"transaction_id": t, "code": e.code} for t, e in cleanup_failures],
                "settled_transaction_ids": settled}) from first
        return settled

    def _finish_abandoned_commit(self, txid: str, finished_at: str, was_finished: bool) -> bool:
        """Retry post-commit staging cleanup, leaving ``finished_at`` unset until it succeeds."""
        staging = self._failure_path(C.STAGING_DIRNAME, txid)
        has_staging = staging.exists()
        if was_finished and not has_staging:
            return False
        if has_staging:
            try:
                shutil.rmtree(staging)
            except OSError as exc:
                raise StudioError("storage_error", "committed transaction staging cleanup failed",
                                  details={"transaction_id": txid}) from exc
        if not was_finished:
            self._storage.update("install_transactions", txid, {"finished_at": finished_at})
        return True

    def _preflight_transaction(self, repo: "RepoRecord", kind: str, plan_digest: str) -> PreviewPlan:
        """Every reason not to start, in the order that reveals the most useful one. Sync."""
        status = self.verify_payload()
        if not status.ok:
            raise StudioError("payload_degraded", "the bundled payload does not match its manifest", details={
                "mismatches": list(status.mismatches[:10]), "missing": list(status.missing[:10])})
        return self._plan_for_run(repo, kind, plan_digest)

    def _plan_for_run(self, repo: "RepoRecord", kind: str, plan_digest: str) -> PreviewPlan:
        plan = self.preview(repo, kind)
        if plan.digest() != plan_digest:
            raise StudioError("install_conflict", "the repository changed since the plan was previewed",
                              details={"reason": "preview_changed", "plan_digest": plan.digest()})
        if plan.state_version_blocked:
            raise StudioError("state_version_migration_unconfirmed",
                              "this repository holds AI-DLC state this payload cannot write", details={
                                  "state_versions_found": list(plan.state_versions_found),
                                  "compatible": list(self._manifest.compatible_state_versions),
                                  "unreadable": list(plan.state_versions_unreadable)})
        if plan.newer_installed:
            raise StudioError("newer_installed", "the installed engine is newer than the bundled payload",
                              details={"installed": plan.engine_from, "bundled": plan.engine_to})
        if kind == "upgrade" and plan.same_version:
            raise StudioError("same_version_installed", "the bundled engine is already installed",
                              details={"engine_version": plan.engine_to})
        if plan.blocking:
            raise StudioError("install_conflict", "files in this repository are not Studio's to replace",
                              details={"blockers": [e.to_json() for e in plan.blockers[:20]]})
        return plan

    def _insert_transaction(self, txid: str, repo: "RepoRecord", kind: str) -> None:
        prior = self.current_receipt(repo.repo_id)
        self._storage.insert("install_transactions", {
            "transaction_id": txid, "repo_id": repo.repo_id, "resolved_repo_identity": repo.resolved_identity,
            "kind": kind, "status": "staged", "studio_version": C.APP_VERSION,
            "engine_version": self._manifest.engine_version, "payload_digest": self._manifest.payload_digest,
            "started_at": self._clock.iso(), "staging_dir": str(self._data_dir / C.STAGING_DIRNAME / txid),
            "backup_dir": str(self._data_dir / C.BACKUP_DIRNAME / txid),
            "prior_receipt_id": prior.receipt_id if prior else None, "steps_json": []})

    def _journal(self, txid: str, *, checked: bool = False) -> _Journal:
        """This transaction's own staging and backup paths (``checked``: refusing symlinks and bad ids)."""
        if checked:
            return _Journal(txid, self._failure_path(C.STAGING_DIRNAME, txid),
                            self._failure_path(C.BACKUP_DIRNAME, txid))
        return _Journal(txid, self._data_dir / C.STAGING_DIRNAME / txid, self._data_dir / C.BACKUP_DIRNAME / txid)

    def _open_journal(self, journal: _Journal, repo: "RepoRecord", txid: str) -> None:
        """Record the prior receipt and repository fields before anything moves; a rollback restores them."""
        row = self._storage.get("install_transactions", txid)
        if row is None:
            raise StudioError("transaction_not_found", "no such transaction", details={"transaction_id": txid})
        journal.prior_receipt_id = row.get("prior_receipt_id")
        repo_row = self._storage.get("repos", repo.repo_id) or {}
        journal.prior_repo = {key: repo_row.get(key) for key in _REPO_FIELDS}
        journal.prior_repo["install_status"] = str(journal.prior_repo["install_status"] or "not_installed")

    # ---- step plumbing ---- #

    def _fault(self, marker: str) -> None:
        if self.fault_injector is not None:
            self.fault_injector(marker)

    async def _step(self, steps: list[StepRecord], txid: str, repo: "RepoRecord", name: str, fn: Callable[..., dict],
                    *args: Any, persist: bool = True) -> dict:
        """Run one blocking step in a worker thread, with a fault point either side. The record is appended
        first, so a crash leaves ``ok = None`` on the step that was in flight."""
        steps.append(StepRecord(name, self._clock.iso(), None, None, {}))
        index = len(steps) - 1
        self._fault(f"before:{name}")
        if name in STEPS:
            await asyncio.to_thread(self._raise_if_cancelled, txid)
        detail = await asyncio.to_thread(fn, *args)
        self._fault(f"after:{name}")
        steps[index] = replace(steps[index], finished_at=self._clock.iso(), ok=True, detail=detail)
        if persist:
            await self._advance(txid, repo, name, steps)
        return detail

    async def _advance(self, txid: str, repo: "RepoRecord", name: str, steps: list[StepRecord]) -> None:
        status = _STATUS_AFTER.get(name)
        values: dict[str, Any] = {"steps_json": _steps_json(steps), **({"status": status} if status else {})}
        await asyncio.to_thread(self._advance_record, txid, name, values)
        if status:
            await self._publish(txid, repo.repo_id, status)

    def _advance_record(self, txid: str, name: str, values: dict) -> None:
        # Serialized with the commit boundary: a cancel accepted before `committed` is observed, and one arriving
        # after it sees the durable outcome.
        with self._storage._txn():
            if name in STEPS and name != "release_lease":
                self._raise_if_cancelled(txid)
            self._storage.update("install_transactions", txid, values)

    async def _publish(self, txid: str, repo_id: str, status: str) -> None:
        await self._events.publish("transaction.updated",
                                   {"transaction_id": txid, "repo_id": repo_id, "status": status})

    async def _mark_recovery_required(self, repo_id: str) -> None:
        await asyncio.to_thread(self._storage.update, "repos", repo_id, {"install_status": "recovery_required"})

    async def _lease_step(self, steps: list[StepRecord], txid: str, repo: "RepoRecord", operation: str, *,
                          receipt_op: bool = False, recovery: bool = False) -> "LeaseGrant | None":
        """Take the admin lease (``repo_busy`` is a refusal: nothing was written). A receipt operation then
        rechecks its row and returns ``None`` when a concurrent invocation already settled it."""
        steps.append(StepRecord("acquire_admin_lease", self._clock.iso(), None, None, {}))
        index = len(steps) - 1
        self._fault("before:acquire_admin_lease")
        if not receipt_op:
            await asyncio.to_thread(self._raise_if_cancelled, txid)
        grant = await self._scheduler.acquire_admin(repo, operation_type=operation, transaction_id=txid)
        try:  # the caller cannot see the grant until this returns, so a failure in the tail hands it back here
            if receipt_op:
                row = await asyncio.to_thread(self._storage.get, "install_transactions", txid)
                if row["status"] in _SETTLED_STATUS and not (recovery and row["status"] == "recovery_required"):
                    await self._scheduler.release(grant)
                    return None
                if not recovery and row["status"] != "staged":
                    raise StudioError("install_recovery_required", "uninstall already started")
            self._fault("after:acquire_admin_lease")
            detail = {"kind": grant.kind, "generation": grant.generation,
                      **({"operation_type": operation} if receipt_op else {})}
            steps[index] = replace(steps[index], finished_at=self._clock.iso(), ok=True, detail=detail)
            await self._advance(txid, repo, "acquire_admin_lease", steps)
        except BaseException:
            with contextlib.suppress(Exception):  # the original failure is the one worth raising
                await self._scheduler.release(grant)
            raise
        return grant

    async def _release_step(self, steps: list[StepRecord], txid: str, repo: "RepoRecord", grant: "LeaseGrant") -> None:
        """Give the lease back; a failure here never changes the outcome."""
        steps.append(StepRecord("release_lease", self._clock.iso(), None, None, {}))
        index = len(steps) - 1
        ok, detail = True, {"generation": grant.generation}
        try:
            self._fault("before:release_lease")
            await self._scheduler.release(grant)
            self._fault("after:release_lease")
        except Exception as exc:  # noqa: BLE001 - see docstring
            ok, detail["error"] = False, self._error_text(exc)
        steps[index] = replace(steps[index], finished_at=self._clock.iso(), ok=ok, detail=detail)
        await asyncio.to_thread(self._storage.update, "install_transactions", txid, {"steps_json": _steps_json(steps)})

    # ---- the install steps (sync; each runs in one to_thread) ---- #

    @staticmethod
    def _managed_target(root: Path, rel: str) -> Path:
        target = security.resolve_inside(root, rel)
        if (root / rel).is_symlink():
            raise StudioError("install_conflict", "a managed path is a symlink", details={"path": rel})
        return target

    def _stage_payload(self, journal: _Journal) -> dict:
        """Copy the payload into ``data_dir/staging/<txid>/``; everything later reads this private copy."""
        staging = journal.staging_dir
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        total = 0
        for pf in self._manifest.files:
            self._raise_if_cancelled(journal.txid)
            data = _read_bytes(security.resolve_inside(self._payload_root, pf.path))
            if data is None:
                raise StudioError("payload_degraded", "a payload file is missing", details={"path": pf.path})
            target = security.resolve_inside(staging, pf.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            total += len(data)
        return {"files": len(self._manifest.files), "bytes": total, "staging_dir": str(staging)}

    def _verify_staging(self, journal: _Journal) -> dict:
        """Re-hash the staged copy against the manifest; a mismatch stops before the repository is touched."""
        bad = [pf.path for pf in self._manifest.files
               if _digest_of(_read_bytes(security.resolve_inside(journal.staging_dir, pf.path))) != pf.sha256]
        if bad:
            raise StudioError("payload_degraded", "the staged payload does not match its manifest",
                              details={"paths": bad[:10]})
        return {"verified": len(self._manifest.files)}

    def _backup(self, journal: _Journal, plan: PreviewPlan, repo: "RepoRecord") -> dict:
        """Copy everything the plan may replace (plus the standing receipt's files) and record what is absent:
        after the write, a file Studio created looks exactly like one it replaced."""
        root = Path(repo.canonical_path)
        backup = journal.backup_dir
        if backup.exists():
            shutil.rmtree(backup)
        backup.mkdir(parents=True, exist_ok=True)
        receipt = self.current_receipt(repo.repo_id)
        touched = {e.path for e in plan.entries if e.action in WRITING_ACTIONS or e.action == "retire"}
        if receipt is not None:
            touched.update(rf.path for rf in receipt.files)
        copied = 0
        for rel in sorted(touched):
            target = self._managed_target(root, rel)
            data, unreadable = _live_bytes(target)
            if unreadable:  # "absent" in the journal is what would later let a rollback delete it
                raise StudioError("install_conflict",
                                  "a managed path exists and cannot be read, so it cannot be backed up",
                                  details={"path": rel, "reason": "unreadable"})
            if data is None:
                journal.backups[rel] = None
                continue
            copy_at = security.resolve_inside(backup, rel)
            copy_at.parent.mkdir(parents=True, exist_ok=True)
            copy_at.write_bytes(data)
            journal.backups[rel] = security.sha256_bytes(data)
            journal.backup_modes[rel] = stat.S_IMODE(target.stat().st_mode)
            copied += 1
        return {"backed_up": copied, "recorded": len(journal.backups), "backup_dir": str(backup)}

    def _write_files(self, journal: _Journal, plan: PreviewPlan, repo: "RepoRecord") -> dict:
        """Write framework and shell files, each re-checked against the plan, then retire dropped files."""
        root = Path(repo.canonical_path)
        written = retired = skipped = 0
        for entry in plan.entries:
            if entry.action not in ("create", "owned_identical", "engine_modified", "shell_create"):
                continue
            self._raise_if_cancelled(journal.txid)
            rel = entry.path
            target = self._managed_target(root, rel)
            live = _read_bytes(target)
            if _digest_of(live) != entry.live_sha256:
                raise StudioError("install_conflict", "a managed file changed after the plan was made",
                                  details={"path": rel, "expected": entry.live_sha256, "found": _digest_of(live)})
            data = _read_bytes(security.resolve_inside(journal.staging_dir, rel))
            if data is None:
                raise StudioError("payload_degraded", "a staged payload file vanished", details={"path": rel})
            if live is None:
                journal.created.append(rel)
            _atomic_write(target, data)
            journal.written[rel] = security.sha256_bytes(data)
            written += 1
        for entry in plan.entries:
            if entry.action != "retire":
                continue
            self._raise_if_cancelled(journal.txid)
            target = security.resolve_inside(root, entry.path)
            live = _read_bytes(target)
            if live is None:
                continue
            if security.sha256_bytes(live) != entry.receipt_sha256:
                skipped += 1  # edited since the preview: left alone, and counted
                continue
            target.unlink()
            journal.retired.append(entry.path)
            retired += 1
        return {"written": written, "retired": retired, "retire_skipped": skipped}

    def _merge_fragments(self, journal: _Journal, plan: PreviewPlan, repo: "RepoRecord") -> dict:
        """Rewrite each merge target from its backed-up before-bytes plus the staged fragment (so a retry cannot
        duplicate a block), refusing a target the user changed since the plan."""
        root = Path(repo.canonical_path)
        merged = 0
        for entry in plan.entries:
            if entry.action not in ("merge_create", "merge_update"):
                continue
            self._raise_if_cancelled(journal.txid)
            rel = entry.path
            target = self._managed_target(root, rel)
            # The journal decides "before": a target backed up as absent is created, whatever appeared since.
            backed_up = journal.backups[rel]
            before = _read_bytes(security.resolve_inside(journal.backup_dir, rel)) if backed_up is not None else None
            live, unreadable = _live_bytes(target)
            if unreadable:
                raise StudioError("install_conflict", "a merge target exists and cannot be read",
                                  details={"path": rel, "reason": "unreadable"})
            if _digest_of(live) not in (backed_up, journal.written.get(rel)):  # `written`: a retry of this step
                raise StudioError("install_conflict", "a merge target changed after the plan was made",
                                  details={"path": rel, "expected": backed_up, "found": _digest_of(live)})
            # The receipt digest lets `_FencedBlock` replace a verbatim adopted document instead of appending to it.
            handler = self._handler(rel, source=journal.staging_dir)
            data = handler.merged_bytes(before, receipt_digest=entry.receipt_sha256)
            if before is None:
                journal.created.append(rel)
            _atomic_write(target, data)
            journal.written[rel] = security.sha256_bytes(data)
            merged += 1
        return {"merged": merged}

    def _post_write_validate(self, journal: _Journal, plan: PreviewPlan, repo: "RepoRecord") -> dict:
        """Prove the tree before the receipt claims it: digests catch a partial write and ``version`` an engine
        that cannot run; the stage-graph lookup is advisory (C15) and doctor runs only when opted in."""
        root = Path(repo.canonical_path)
        bad: list[str] = []
        for entry in plan.entries:
            if entry.action in ("create", "owned_identical", "engine_modified", "shell_create"):
                if _digest_of(_read_bytes(security.resolve_inside(root, entry.path))) != entry.payload_sha256:
                    bad.append(entry.path)
            elif entry.action in ("merge_create", "merge_update"):
                handler = self._handler(entry.path, source=journal.staging_dir)
                live = _read_bytes(security.resolve_inside(root, entry.path))
                if handler.live_fragment(live).digest != handler.payload_fragment().digest:
                    bad.append(entry.path)
        if bad:
            raise StudioError("install_conflict", "written files do not match the payload", details={"paths": bad[:10]})
        version = self._engine.run_sync("utility.version", root, ENGINE_DIR)
        if self._manifest.engine_version not in (version.stdout or ""):
            raise StudioError("install_conflict", "the installed engine does not report the bundled version", details={
                "expected": self._manifest.engine_version, "exit_code": version.exit_code,
                "stdout": (version.stdout or "")[:200]})
        smoke = self._engine.run_sync("state.lookup", root, ENGINE_DIR, query=_SMOKE_LOOKUP_QUERY,
                                      arg=_SMOKE_LOOKUP_ARG)
        detail: dict[str, Any] = {"verified": len(plan.entries), "version_ok": True,
                                  "lookup_exit_code": smoke.exit_code}
        if self._run_doctor():
            doctor = self._engine.run_sync("utility.doctor", root, ENGINE_DIR, _confirmed=True,
                                           lease_generation=str(journal.lease_generation))
            detail["doctor_exit_code"] = doctor.exit_code
        return detail

    def _run_doctor(self) -> bool:
        if self._settings is None:
            return False
        try:
            return bool(self._settings.installer().get("run_doctor_after_install"))
        except Exception:  # noqa: BLE001 - a settings read must never fail an install
            return False

    def _insert_receipt(self, receipt_id: str, repo: "RepoRecord", engine_version: str, payload_digest: str,
                        prior: str | None, txid: str, files: Sequence[ReceiptFile], committed_at: str) -> None:
        self._storage.insert("install_receipts", {
            "receipt_id": receipt_id, "repo_id": repo.repo_id, "resolved_repo_identity": repo.resolved_identity,
            "studio_version": C.APP_VERSION, "engine_version": engine_version, "payload_digest": payload_digest,
            "committed_at": committed_at, "prior_receipt_id": prior, "transaction_id": txid,
            "files_json": [f.to_json() for f in files], "status": "current"})

    def _commit_receipt(self, journal: _Journal, plan: PreviewPlan, repo: "RepoRecord") -> dict:
        """Insert the receipt, supersede the prior one and point the repo row at it: the last write."""
        receipt_id = self._ids.new("rc")
        files = self._receipt_files(plan, journal, Path(repo.canonical_path))
        self._insert_receipt(receipt_id, repo, self._manifest.engine_version, self._manifest.payload_digest,
                             journal.prior_receipt_id, journal.txid, files, self._clock.iso())
        journal.receipt_id, journal.receipt_inserted = receipt_id, True
        if journal.prior_receipt_id:
            self._storage.update("install_receipts", journal.prior_receipt_id, {"status": "superseded"})
        self._storage.update("repos", repo.repo_id, {
            "install_status": "installed", "installed_engine_version": self._manifest.engine_version,
            "engine_dir": ENGINE_DIR, "receipt_version": receipt_id})
        return {"receipt_id": receipt_id, "files": len(files)}

    def _receipt_files(self, plan: PreviewPlan, journal: _Journal, root: Path) -> list[ReceiptFile]:
        """What the new receipt owns: never ``shell`` files, whose later edits are the user's, not drift."""
        prior = self.current_receipt(plan.repo_id)
        previous = prior.by_path() if prior else {}
        out: list[ReceiptFile] = []
        for e in plan.entries:
            if e.ownership == "shell" or e.action in ("retire", "retire_blocked"):
                continue
            if e.ownership == "merge":
                out.append(ReceiptFile(e.path, e.ownership, "fragment", None, e.payload_sha256, e.fragment_key,
                                       e.action == "merge_identical",
                                       self._fragment_uninstall_metadata(e, journal, root, previous.get(e.path))))
            else:
                out.append(ReceiptFile(e.path, e.ownership, "file", e.payload_sha256, None, None,
                                       e.action == "identical"))
        if out:
            out[0] = replace(out[0], engine_state_versions=self._manifest.compatible_state_versions)
        return out

    def _fragment_uninstall_metadata(self, entry: PreviewEntry, journal: _Journal, root: Path,
                                     prior: ReceiptFile | None) -> dict:
        """Record what Studio introduced, so uninstall never removes a matching pre-existing key, line or block;
        provenance is inherited from the prior receipt, never invented for a legacy one."""
        spec = dict(self._manifest.merge_targets[entry.path])
        previous = (prior.uninstall if prior else None) or {}
        written = entry.action in {"merge_create", "merge_update"}
        if written:
            before = (_read_bytes(security.resolve_inside(journal.backup_dir, entry.path))
                      if journal.backups.get(entry.path) is not None else None)
        else:
            before = _read_bytes(security.resolve_inside(root, entry.path))
        after = _read_bytes(security.resolve_inside(root, entry.path))
        handler = self._handler(entry.path, source=journal.staging_dir)
        if (isinstance(handler, _JsonManagedKeys) and handler.managed_map_entries
                and handler.live_fragment(after).digest != entry.payload_sha256):
            raise StudioError("install_conflict", "owned model preferences changed before receipt commit",
                              details={"path": entry.path})
        metadata: dict[str, Any] = {"spec": spec, "created": previous.get("created", written and before is None)}
        if isinstance(handler, _JsonManagedKeys):
            old = _strict_json_object(before) if before is not None else {}
            new = _strict_json_object(after) if after is not None else {}
            maps = handler.managed_map_entries
            keys = [key for key in handler.managed_keys if key not in maps and (key in previous.get("keys", [])
                    or (written and not _resolve_key(old, key)[0] and _resolve_key(new, key)[0]))]
            parents = {tuple(p) for p in previous.get("parents", [])}
            introduced_maps = []
            if maps:
                introduced = {}
                for key, names in maps.items():
                    present, old_map = _resolve_key(old, key)
                    old_map = old_map if present else {}
                    legacy_added = (key in previous.get("keys", []) and prior is not None
                                    and prior.canonical_digest == handler.payload_fragment().digest
                                    and "managedMapEntries" not in (previous.get("spec") or {}))
                    introduced[key] = [name for name in names if name in previous.get("map_entries", {}).get(key, [])
                                       or legacy_added or (written and name not in old_map)]
                    if legacy_added or (written and not present):
                        parents.add((key,) if key in new else tuple(key.split(".")))
                        introduced_maps.append(key)
                metadata["map_entries"] = introduced
            for key in [*keys, *introduced_maps]:
                if key in new:
                    continue
                parts, node = key.split("."), old
                for index, part in enumerate(parts[:-1], 1):
                    if not isinstance(node, dict) or part not in node:
                        parents.add(tuple(parts[:index]))
                        node = {}
                    else:
                        node = node[part]
            metadata.update(keys=keys, parents=[list(p) for p in sorted(parents)])
        elif isinstance(handler, _MissingLines):
            present = {line.strip() for line in (before or b"").decode("utf-8").splitlines()}
            all_lines = handler._payload_lines()
            metadata.update(all_lines=all_lines, lines=[line for line in all_lines if line in previous.get("lines", [])
                                                        or (written and line.strip() not in present)])
        else:  # rewriting a pre-existing block grants no right to remove it later
            metadata["block"] = bool(previous.get("block", written and prior is None))
        return metadata

    # ---- install rollback ---- #

    async def _rollback(self, steps: list[StepRecord], txid: str, repo: "RepoRecord", journal: _Journal,
                        error: str) -> tuple[str, str | None]:
        """Retract Studio's records, undo every change and prove it by re-hashing; returns ``(status, failed_dir)``.
        No committed receipt ever describes a half-restored tree: without proof the repo is ``recovery_required``."""
        journal.cancelling = error == "cancelled"
        await asyncio.to_thread(self._storage.update, "install_transactions", txid,
                                {"status": "rolling_back", "error": error, "steps_json": _steps_json(steps)})
        await self._publish(txid, repo.repo_id, "rolling_back")
        await asyncio.to_thread(self._retract_records, journal, repo)
        rollback_error: str | None = None
        for name in ROLLBACK_STEPS:  # each step runs the method of the same name
            try:
                await self._step(steps, txid, repo, name, getattr(self, f"_{name}"), journal, repo, persist=False)
            except Exception as exc:  # noqa: BLE001 - a rollback failure is a reportable outcome
                rollback_error = self._error_text(exc)
                steps[-1] = replace(steps[-1], finished_at=self._clock.iso(), ok=False,
                                    detail={"error": rollback_error})
                break
        status = "rolled_back" if rollback_error is None else "recovery_required"
        if rollback_error is not None:
            await self._mark_recovery_required(repo.repo_id)
        failed_dir = await asyncio.to_thread(self._archive_failure, journal, steps, rollback_error or error)
        await self._activity.record(
            kind="install.transaction", severity="attention" if rollback_error is None else "critical",
            repo_id=repo.repo_id, evidence=(EvidenceRef("studio", f"transaction:{txid}"),),
            params={"transaction_id": txid, "status": status, "error": error, "rollback_error": rollback_error})
        return status, failed_dir

    def _restore_backups(self, journal: _Journal, repo: "RepoRecord") -> dict:
        """Put back exactly what this transaction wrote, merged or retired: a backed-up path it never touched may
        hold an edit the user made while it ran."""
        root = Path(repo.canonical_path)
        changed = set(journal.written) | set(journal.retired)
        restored = 0
        for rel, digest in journal.backups.items():
            if digest is None or rel not in changed:
                continue
            if journal.cancelling:
                self._guard_cancel_restore(journal, repo, rel)
            data = _read_bytes(security.resolve_inside(journal.backup_dir, rel))
            if data is None or security.sha256_bytes(data) != digest:
                raise StudioError("install_recovery_required", "a backup copy is missing or corrupt",
                                  details={"path": rel})
            if journal.cancelling:
                _durable_write(self._safe_path(root, rel), data, journal.backup_modes[rel])
            else:
                _atomic_write(security.resolve_inside(root, rel), data)
            restored += 1
        return {"restored": restored}

    def _delete_created(self, journal: _Journal, repo: "RepoRecord") -> dict:
        """Remove files this transaction invented; only paths that had no backup are eligible."""
        root = Path(repo.canonical_path)
        removed = 0
        for rel in journal.created:
            if journal.backups.get(rel) is not None:
                continue
            if journal.cancelling:
                self._guard_cancel_restore(journal, repo, rel)
            target = security.resolve_inside(root, rel)
            if security.is_regular_file(target):
                target.unlink()
                removed += 1
        return {"removed": removed}

    def _guard_cancel_restore(self, journal: _Journal, repo: "RepoRecord", rel: str) -> None:
        """A cancellation restores only under its own lease, in the same root, over its own bytes."""
        root = security.validate_repo_root(repo.canonical_path)
        info = root.stat()
        if str(root) != repo.canonical_path or f"{info.st_dev}:{info.st_ino}" != repo.resolved_identity:
            raise StudioError("install_recovery_required", "cancelled repository identity changed")
        live, unreadable = _live_bytes(self._safe_path(root, rel))
        if unreadable or _digest_of(live) != journal.written.get(rel):
            raise StudioError("install_recovery_required", "user edited a cancellation rollback target",
                              details={"path": rel})

    def _retract_records(self, journal: _Journal, repo: "RepoRecord") -> None:
        """Put Studio's own rows back to the pre-transaction story, before any byte moves back."""
        if not (journal.receipt_inserted and journal.receipt_id):
            return
        self._storage.update("install_receipts", journal.receipt_id, {"status": "rolled_back"})
        if journal.prior_receipt_id:
            self._storage.update("install_receipts", journal.prior_receipt_id, {"status": "current"})
        self._storage.update("repos", repo.repo_id, journal.prior_repo)

    def _reverify_old_receipt(self, journal: _Journal, repo: "RepoRecord") -> dict:
        """Prove the repository is exactly the previous installation: every file the standing receipt owns hashes
        to its digest, every changed path is back to its backup (merge targets are proved by backup bytes, not
        by re-deriving a fragment with another payload), and nothing this transaction created remains."""
        root = Path(repo.canonical_path)
        checked = fragments = 0
        receipt = self.current_receipt(repo.repo_id)
        if receipt is not None and receipt.receipt_id != journal.receipt_id:
            for rf in receipt.files:
                if rf.kind == "fragment":
                    fragments += 1
                elif rf.sha256 is not None:
                    if _digest_of(_read_bytes(security.resolve_inside(root, rf.path))) != rf.sha256:
                        raise StudioError("install_recovery_required", "a receipt-owned file did not come back",
                                          details={"path": rf.path})
                    checked += 1
        changed = set(journal.written) | set(journal.retired)  # an untouched path may hold an edit made meanwhile
        restored = [(rel, digest) for rel, digest in journal.backups.items() if digest is not None and rel in changed]
        for rel, digest in restored:
            if _digest_of(_read_bytes(security.resolve_inside(root, rel))) != digest:
                raise StudioError("install_recovery_required", "a replaced file was not restored",
                                  details={"path": rel})
        for rel in journal.created:
            if journal.backups.get(rel) is None and security.is_regular_file(security.resolve_inside(root, rel)):
                raise StudioError("install_recovery_required", "a created file was not removed", details={"path": rel})
        return {"receipt_paths_verified": checked, "fragments_proved_by_backup": fragments,
                "restored_verified": len(restored)}

    # ---- evidence and outcome ---- #

    def _failure_path(self, *parts: str) -> Path:
        """An evidence path under ``data_dir`` with no symlink at any component: evidence belongs to one txid."""
        path = self._data_dir
        for part in parts:
            if not part or part in {".", ".."} or any(c in part for c in "/\\\0"):
                raise StudioError("bad_path", "invalid transaction evidence path")
            path = path / part
            if path.is_symlink():
                raise StudioError("bad_path", "transaction evidence path is a symlink")
        return path

    def _candidate_matches_payload(self, staging: Path) -> bool:
        """Whether the staged candidate is reproducible from this manifest, so it may be discarded; stricter than
        ``verify_payload``: any extra entry, symlink, special or unreadable file, or incomplete walk retains it."""
        files = self._manifest.by_path()
        if (len(files) != self._manifest.file_count or len(files) != len(self._manifest.files)
                or self._payload_digest() != self._manifest.payload_digest):
            return False
        directories = {parent.as_posix() for rel in files for parent in Path(rel).parents if parent != Path(".")}
        seen: set[str] = set()
        walked = 0

        def walk_error(exc: OSError) -> None:
            raise exc

        try:
            if not stat.S_ISDIR(staging.lstat().st_mode):
                return False
            for base, dirs, names in os.walk(staging, followlinks=False, onerror=walk_error):
                walked += len(dirs) + len(names)
                if walked > _MAX_PAYLOAD_WALK:
                    return False
                for name in dirs:
                    path = Path(base) / name
                    if (not stat.S_ISDIR(path.lstat().st_mode)
                            or path.relative_to(staging).as_posix() not in directories):
                        return False
                for name in names:
                    path = Path(base) / name
                    rel = path.relative_to(staging).as_posix()
                    pf = files.get(rel)
                    st = path.lstat()
                    if (pf is None or not stat.S_ISREG(st.st_mode) or st.st_size != pf.size
                            or security.sha256_file(path) != pf.sha256):
                        return False
                    seen.add(rel)
        except OSError:
            return False
        return seen == set(files)

    def _archive_failure(self, journal: _Journal, steps: list[StepRecord], error: str | None) -> str:
        """Keep this transaction's evidence in ``failed/<txid>/``, omitting only a proven-identical payload copy;
        repeatable after a crash: evidence is never replaced, and a candidate is deleted only once explained."""
        staging = self._failure_path(C.STAGING_DIRNAME, journal.txid)
        failed = self._failure_path(C.FAILED_DIRNAME, journal.txid)
        target = self._failure_path(C.FAILED_DIRNAME, journal.txid, FAILED_STAGING_DIRNAME)
        log_path = self._failure_path(C.FAILED_DIRNAME, journal.txid, FAILED_STEPS_FILENAME)
        existing: Any = {}
        if log_path.exists():
            raw = _read_bytes(log_path)
            try:
                existing = json.loads(raw) if raw is not None else None
            except ValueError:
                existing = None
            if not isinstance(existing, dict) or existing.get("transaction_id") != journal.txid:
                raise StudioError("bad_path", "existing transaction evidence is unreadable or unrelated")
        archives = []
        if failed.is_dir():
            for path in sorted(failed.iterdir()):
                if path.name == FAILED_STAGING_DIRNAME or path.name.startswith(FAILED_STAGING_DIRNAME + "-"):
                    self._failure_path(C.FAILED_DIRNAME, journal.txid, path.name)
                    archives.append(path.name)
        failed.mkdir(parents=True, exist_ok=True)
        payload_digest = (self._storage.get("install_transactions", journal.txid) or {}).get("payload_digest")
        discard = False
        candidate = existing.get("candidate") or {"status": "retained" if archives else "absent",
                                                  "payload_digest": payload_digest}
        if staging.exists():
            # A failed verification stays evidence even if a later read sees the expected bytes again.
            verify_failed = any(s.name == "verify_staging" and s.ok is not True for s in steps)
            discard = (not verify_failed and payload_digest == self._manifest.payload_digest
                       and self._candidate_matches_payload(staging))
            if discard:
                candidate = {"status": "retained" if archives else "identical_to_payload",
                             "payload_digest": payload_digest}
                if archives:
                    candidate["omitted_identical_staging"] = True
            else:
                suffix = 1
                while target.exists():
                    target = self._failure_path(C.FAILED_DIRNAME, journal.txid, f"{FAILED_STAGING_DIRNAME}-{suffix}")
                    suffix += 1
                staging.rename(target)  # both live under data_dir: no link is followed, no copy crosses a device
                archives.append(target.name)
                candidate = {"status": "retained", "payload_digest": payload_digest}
        if archives:
            candidate = {**candidate, "status": "retained", "paths": archives}
        record = {
            **existing, "transaction_id": journal.txid, "error": error,
            "steps": existing.get("steps") or _steps_json(steps),
            # A process that died cannot rebuild its in-memory journal: keep whatever it already archived.
            "backups": journal.backups or existing.get("backups", {}),
            "created": journal.created or existing.get("created", []),
            "retired": journal.retired or existing.get("retired", []), "candidate": candidate,
        }
        _durable_write(log_path, (json.dumps(record, indent=2) + "\n").encode("utf-8"))
        if discard:
            shutil.rmtree(staging)
        return str(failed)

    def _cleanup_staging(self, journal: _Journal) -> None:
        """Drop the staged copy after a commit; the backups stay as the rollback record."""
        if journal.staging_dir.is_dir():
            shutil.rmtree(journal.staging_dir, ignore_errors=True)

    async def _finish(self, txid: str, repo: "RepoRecord", status: str, steps: list[StepRecord], journal: _Journal,
                      error: str | None, failed_dir: str | None) -> TransactionResult:
        values: dict[str, Any] = {"status": status, "finished_at": self._clock.iso(),
                                  "steps_json": _steps_json(steps), "error": error}
        if failed_dir:
            values["failed_dir"] = failed_dir
        await asyncio.to_thread(self._storage.update, "install_transactions", txid, values)
        await self._publish(txid, repo.repo_id, status)
        evidence = (EvidenceRef("studio", f"transaction:{txid}"),)
        if status == "committed":
            row = await asyncio.to_thread(self._storage.get, "install_transactions", txid)
            await self._activity.record(kind="install.transaction", severity="info", repo_id=repo.repo_id, params={
                "transaction_id": txid, "status": status, "kind": row.get("kind"),
                "engine_version": None if row.get("kind") == "uninstall" else row.get("engine_version"),
                "receipt_id": journal.receipt_id}, evidence=evidence)
        elif status == "failed":
            await self._activity.record(kind="install.transaction", severity="attention", repo_id=repo.repo_id,
                                        params={"transaction_id": txid, "status": status, "error": error},
                                        evidence=evidence)
        await self._events.publish("repo.updated", {"repo_id": repo.repo_id})
        return await asyncio.to_thread(self.transaction, txid)

    @staticmethod
    def _error_text(exc: BaseException) -> str:
        """A code the UI localises (C11) when there is one; ``LeaseHeld`` already carries ``repo_busy``."""
        if isinstance(exc, StudioError):
            return exc.code
        if isinstance(exc, _InstallCancelled):
            return "cancelled"
        return f"{type(exc).__name__}: {exc}"[:400]
