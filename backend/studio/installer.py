"""Transactional installer for the bundled AI-DLC payload (§1.14).

The property this module exists to guarantee, and the only one worth remembering: **after any failure
at any point, the repository is either exactly the previous complete installation with its receipt
current, or it is marked ``recovery_required``. It is never a mixture of two engine versions.** A
half-written engine tree would run — badly — and AI-DLC would then write state with it, so a
mixed tree is not a cosmetic problem but a corruption of the user's workflow record.

Four rules produce that property:

* **The receipt is the last thing written.** Until ``commit_receipt`` succeeds, Studio's story about
  the repository is still the previous version, so a crash anywhere earlier leaves a consistent pair
  (old files, old receipt).
* **Nothing is overwritten without a backup, and nothing is created without being remembered.**
  Rollback is therefore mechanical: restore what was replaced, delete what was invented, then *prove*
  the result by re-hashing every path the old receipt claims.
* **Ownership decides authority, not convenience.** A file that differs from the payload and is not in
  a receipt was written by somebody else; it blocks the install and there is no force path (FR-INST-004).
  User content inside a merge target — unknown keys, an embedded token, the user's own prose in
  ``AGENTS.md``, their own ``.gitignore`` lines — is byte-preserved, because Studio only ever owns the
  managed fragment (FR-INST-006).
* **A failure inside the rollback is reported, never papered over.** ``recovery_required`` blocks the
  repository with a finding and an ``install_conflict`` card instead of pretending the tree is fine.

What this module must never do: write outside the repository's managed paths or ``data_dir``; write
anything under ``aidlc/**`` except creating an absent shell file; overwrite a ``conflict`` /
``owned_modified`` / ``merge_conflict`` entry; run ``doctor`` without an explicit opt-in; or commit a
receipt before post-write validation has passed.

Threading: ``run`` is async orchestration; every step that touches the filesystem, a subprocess or
SQLite runs in one ``asyncio.to_thread`` call so the gateway loop is never blocked (§0.6).
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from . import constants as C
from . import security
from .aidlc_reader import WORKSPACE_DIRNAME
from .consistency import Finding
from .errors import LeaseHeld, StudioError
from .storage import EvidenceRef

if TYPE_CHECKING:  # pragma: no cover - annotations only; no runtime import edge is created
    from .activity import ActivityProjector
    from .aidlc_reader import AidlcReader
    from .engine import EngineRunner
    from .events import EventLog
    from .leases import LeaseGrant, RepoScheduler
    from .repo_registry import PreflightReport, RepoRecord, RepoRegistry
    from .settings import SettingsService
    from .storage import Storage


# --------------------------------------------------------------------------- #
# vocabulary (§1.14)
# --------------------------------------------------------------------------- #

#: The payload schema this code understands. A manifest that says anything else is refused rather
#: than read optimistically: the ownership vocabulary and the merge-strategy names are part of the
#: schema, and guessing at a future one would install files under the wrong authority.
PAYLOAD_SCHEMA = "aidlc-studio-payload/1"

PreviewAction = (
    "create",
    "identical",
    "conflict",
    "owned_identical",
    "owned_modified",
    "engine_modified",
    "retire",
    "retire_blocked",
    "merge_create",
    "merge_identical",
    "merge_update",
    "merge_conflict",
    "shell_create",
    "shell_exists",
)

#: Actions that block the whole transaction. No force flag exists in v1 (FR-INST-004): the user's own
#: bytes are never overwritten on Studio's judgement, so the only ways forward are for the user to
#: move their file aside or to leave AI-DLC uninstalled here.
BLOCKING_ACTIONS = frozenset({"conflict", "owned_modified", "merge_conflict"})

#: Actions that put bytes on disk. Everything else is a no-op or a refusal, which is what keeps
#: "nothing outside the managed set was touched" checkable by reading this one set.
WRITING_ACTIONS = frozenset(
    {"create", "owned_identical", "engine_modified", "shell_create", "merge_create", "merge_update"}
)

TransactionStatus = (
    "staged",
    "leased",
    "backed_up",
    "written",
    "merged",
    "validated",
    "committed",
    "rolling_back",
    "rolled_back",
    "recovery_required",
    "failed",
)

STEPS = (
    "stage_payload",
    "verify_staging",
    "acquire_admin_lease",
    "backup",
    "write_files",
    "merge_fragments",
    "post_write_validate",
    "commit_receipt",
    "release_lease",
)
ROLLBACK_STEPS = ("restore_backups", "delete_created", "reverify_old_receipt")

#: The statuses a transaction can stop in. ``leases.TRANSACTION_SETTLED_STATUS`` is the same set, seen
#: from the other side (it decides whether an admin lease may be reclaimed); this module owns the status
#: vocabulary, so the set is spelled from ``TransactionStatus`` here and pinned equal by a test rather
#: than imported — the installer must not depend on the module that consumes it.
_SETTLED_STATUS = frozenset({"committed", "rolled_back", "recovery_required", "failed"})
CANCELLABLE_KINDS = frozenset({"install", "upgrade", "recovery"})

#: Statuses a transaction can be abandoned in having written nothing to the repository: the payload is
#: staged under Studio's own data directory and the lease is held, but ``backup`` has not run. Anything
#: later may have moved bytes in someone's checkout, so it settles to ``recovery_required`` instead.
_ABANDONED_WROTE_NOTHING = frozenset({"staged", "leased"})

#: Recorded as the transaction's ``error`` when startup settles it. Deliberately not prose: it is a code
#: the UI translates, and it says what happened (the process went away) rather than guessing why.
_ABANDONED_ERROR = "process_exited"

#: Steps 3–7 of the §1.14 numbering. An exception inside any of them means bytes may already have
#: moved in the repository, so it rolls back; a failure before them leaves the repository untouched
#: and only the transaction row records it.
ROLLBACK_TRIGGERING_STEPS = frozenset(
    {"backup", "write_files", "merge_fragments", "post_write_validate", "commit_receipt"}
)

#: The status each successful step leaves the transaction in.
_STATUS_AFTER: dict[str, str] = {
    "verify_staging": "staged",
    "acquire_admin_lease": "leased",
    "backup": "backed_up",
    "write_files": "written",
    "merge_fragments": "merged",
    "post_write_validate": "validated",
    "commit_receipt": "committed",
}

#: Merge strategies this code implements. ``verify_payload`` refuses a payload whose ``mergeTargets``
#: names anything else (review P10) — an unimplemented strategy would otherwise silently fall through
#: to "no fragment written", producing an install that looks complete and is not.
MERGE_STRATEGIES = frozenset({"json-managed-keys", "append-fenced-block", "append-missing-lines"})

#: Ownership kinds. ``shell`` is deliberately outside the receipt: ``aidlc/**`` is the user's and the
#: engine's workspace (memory rules, the space cursor), so Studio creates a missing seed file and then
#: never claims it — receipting it would let a later upgrade report the user's own edits as drift.
OWNED_KINDS = frozenset({"framework", "framework-mutable"})

#: The directory inside ``payload/`` that holds the vendored files; manifest paths are relative to it.
PAYLOAD_FILES_DIRNAME = "aidlc-kiro"

#: Where a failed transaction's evidence lands, relative to ``data_dir/failed/<txid>/``.
FAILED_STAGING_DIRNAME = "staging"
FAILED_STEPS_FILENAME = "transaction.json"

# Uninstall uses the existing recovery admin category until the scheduler's operation vocabulary
# gains "uninstall". The transaction kind and all installer evidence still say "uninstall".
UNINSTALL_LEASE_OPERATION = "recovery"
UNINSTALL_JOURNAL_FILENAME = "uninstall-journal.json"
ROLLBACK_JOURNAL_FILENAME = "rollback-journal.json"
ROLLBACK_ACTIONS = ("restore_version", "remove", "preserve", "identical", "rollback_conflict")
UNINSTALL_ACTIONS = ("remove", "remove_fragment", "preserve", "already_absent", "uninstall_conflict")
UNINSTALL_STEPS = (
    "acquire_admin_lease", "backup", "remove_files", "remove_fragments",
    "post_uninstall_validate", "commit_uninstall", "release_lease",
)
_STATUS_AFTER.update({
    "remove_files": "written",
    "remove_fragments": "merged",
    "post_uninstall_validate": "validated",
    "commit_uninstall": "committed",
})
_STATUS_AFTER.update({
    "restore_version": "written", "post_rollback_validate": "validated", "commit_rollback": "committed",
})

#: The engine directory this payload installs into. Every manifest path is relative to the repository
#: root, and the harness half of them lives under this directory.
ENGINE_DIR = C.STUDIO_HARNESS_DIR

#: Post-write smoke: the payload's own stage graph must answer a phase lookup. Advisory (C15) — whether
#: ``lookup`` needs a live state file is version-dependent, so a non-zero exit is recorded, not fatal.
_SMOKE_LOOKUP_QUERY = "phase-of"
_SMOKE_LOOKUP_ARG = "workspace-scaffold"

#: Upper bound on the payload walk that looks for unlisted files. Four times the largest payload
#: shipped so far: enough headroom that a real bundle is never truncated, small enough that a walk of a
#: mistakenly-pointed-at directory cannot run away.
_MAX_PAYLOAD_WALK = 4000

#: A unified diff is evidence for a human decision, not a payload. Bounded and redacted before it
#: leaves this module; ``None`` rather than mojibake for anything that is not text.
_MAX_DIFF_BYTES = 64 * 1024
_DIFF_CONTEXT_LINES = 3


class InjectedFault(Exception):
    """Raised by a test's fault injector to simulate a crash at an exact step boundary.

    Its own type (not ``StudioError``) so the transaction cannot accidentally classify it as a
    known failure mode and take a nicer path than a real crash would get.
    """


class _InstallCancelled(Exception):
    """Internal control flow; ``cancelled`` is an outcome code, not a new HTTP error."""


# --------------------------------------------------------------------------- #
# payload manifest
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PayloadFile:
    """One vendored file: its repo-relative path, digest, size and ownership kind."""

    path: str
    sha256: str
    size: int
    ownership: str

    def to_json(self) -> dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size": self.size, "ownership": self.ownership}


@dataclass(frozen=True, slots=True)
class PayloadManifest:
    """``payload/manifest.json`` as data.

    Every version-derived number in the product (``/health``, ``/payload``, ``RepoRecord.install``,
    the compatible State Versions, the merge-target key lists) is read from here rather than from a
    literal in code, so bumping the payload is one regenerated file and no code edit (C21/review P03).
    """

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
        """Parse and shape-check the manifest. Anything unexpected is ``payload_degraded``.

        Strict on purpose: this file decides which bytes are written into a user's repository and under
        whose authority. A manifest that is missing ``files``, or carries an ownership kind this code
        has no rule for, cannot be installed *partially* — it must not be installed at all.
        """
        raw = security.bounded_read(Path(path), C.MAX_JSON_BYTES)
        if raw is None:
            raise StudioError(
                "payload_degraded", "payload manifest is missing or unreadable",
                details={"path": Path(path).name},
            )
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise StudioError("payload_degraded", "payload manifest is not valid JSON",
                              details={"reason": str(exc)[:200]}) from None
        if not isinstance(data, Mapping):
            raise StudioError("payload_degraded", "payload manifest is not an object")
        schema = str(data.get("schema") or "")
        if schema != PAYLOAD_SCHEMA:
            raise StudioError("payload_degraded", "unsupported payload schema",
                              details={"schema": schema, "expected": PAYLOAD_SCHEMA})

        rows = data.get("files")
        if not isinstance(rows, list) or not rows:
            raise StudioError("payload_degraded", "payload manifest lists no files")
        files: list[PayloadFile] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise StudioError("payload_degraded", "payload manifest has a malformed file entry")
            rel = str(row.get("path") or "")
            digest = str(row.get("sha256") or "")
            ownership = str(row.get("ownership") or "")
            if not rel or len(digest) != 64 or ownership not in C.OWNERSHIP:
                raise StudioError(
                    "payload_degraded", "payload manifest has a malformed file entry",
                    details={"path": rel, "ownership": ownership},
                )
            if rel.startswith("/") or ".." in Path(rel).parts:
                raise StudioError("payload_degraded", "payload manifest has a non-relative path",
                                  details={"path": rel})
            files.append(
                PayloadFile(path=rel, sha256=digest, size=int(row.get("size") or 0), ownership=ownership)
            )

        merge_targets = data.get("mergeTargets")
        if not isinstance(merge_targets, Mapping):
            raise StudioError("payload_degraded", "payload manifest has no mergeTargets object")
        engine_version = str(data.get("engineVersion") or "")
        if not engine_version:
            raise StudioError("payload_degraded", "payload manifest has no engineVersion")
        versions = data.get("compatibleStateVersions")
        if not isinstance(versions, list) or not versions:
            raise StudioError("payload_degraded", "payload manifest has no compatibleStateVersions")

        return cls(
            schema=schema,
            harness=str(data.get("harness") or ""),
            engine_version=engine_version,
            source=dict(data.get("source") or {}),
            compatible_state_versions=tuple(sorted({int(v) for v in versions})),
            stage_count=int(data.get("stageCount") or 0),
            payload_digest=str(data.get("payloadDigest") or ""),
            file_count=int(data.get("fileCount") or len(files)),
            merge_targets={str(k): dict(v) for k, v in merge_targets.items() if isinstance(v, Mapping)},
            files=tuple(files),
        )

    # ---- derived views -------------------------------------------------- #

    def by_path(self) -> dict[str, PayloadFile]:
        return {f.path: f for f in self.files}

    def ownership_counts(self) -> dict[str, int]:
        """``{ownership: count}`` for `GET /payload`; every declared kind present, zero-filled.

        Additive to §1.14: §2.1's ``/payload`` body carries ``ownership_counts`` and the numbers must
        come from the manifest rather than from a literal in the handler (review P03).
        """
        counts = {kind: 0 for kind in C.OWNERSHIP}
        for f in self.files:
            counts[f.ownership] = counts.get(f.ownership, 0) + 1
        return counts

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "harness": self.harness,
            "engine_version": self.engine_version,
            "source": dict(self.source),
            "compatible_state_versions": list(self.compatible_state_versions),
            "stage_count": self.stage_count,
            "payload_digest": self.payload_digest,
            "file_count": self.file_count,
            "ownership_counts": self.ownership_counts(),
            "merge_targets": {k: dict(v) for k, v in self.merge_targets.items()},
        }


@dataclass(frozen=True, slots=True)
class PayloadStatus:
    """The answer to "are the bytes we ship still the bytes we promised?"."""

    ok: bool
    engine_version: str
    file_count: int
    mismatches: tuple[str, ...]
    missing: tuple[str, ...]
    extra: tuple[str, ...]
    checked_at: str

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "engine_version": self.engine_version,
            "file_count": self.file_count,
            "mismatches": list(self.mismatches),
            "missing": list(self.missing),
            "extra": list(self.extra),
            "checked_at": self.checked_at,
        }


# --------------------------------------------------------------------------- #
# preview
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PreviewEntry:
    """What will happen to one path, and the evidence for that verdict."""

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

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ownership": self.ownership,
            "action": self.action,
            "live_sha256": self.live_sha256,
            "payload_sha256": self.payload_sha256,
            "receipt_sha256": self.receipt_sha256,
            "size": self.size,
            "fragment_key": self.fragment_key,
            "diff": self.diff,
            "blocking": self.blocking,
            **({"reason": self.reason} if self.reason is not None else {}),
        }

    def digest_json(self) -> dict[str, Any]:
        """The subset that identifies the decision, for ``PreviewPlan.digest`` (no diff body)."""
        return {
            "path": self.path,
            "ownership": self.ownership,
            "action": self.action,
            "live_sha256": self.live_sha256,
            "payload_sha256": self.payload_sha256,
            "receipt_sha256": self.receipt_sha256,
            "fragment_key": self.fragment_key,
            "blocking": self.blocking,
            **({"reason": self.reason} if self.reason is not None else {}),
        }


@dataclass(frozen=True, slots=True)
class PreviewPlan:
    """Everything a user confirms before Studio writes anything.

    ``state_versions_found`` / ``state_version_blocked`` are the P-06 guard: a 2.6.2 engine written
    over State Version 7 records is the mixed-version install the PRD forbids, and the engine's own
    v7→v8 migration is unverified (C21, Appendix B9), so such a repository stays readable and is never
    upgraded.
    """

    repo_id: str
    kind: str
    #: The version of *Studio's own* harness (``.kiro``) before this plan runs, ``None`` when Studio has
    #: nothing installed here. Never a foreign harness's version, however readable that harness is; see
    #: ``Installer.preview`` for why the whole install lane turns on that distinction.
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
    #: Records whose ``aidlc-state.md`` could not be read at all. Additive to §1.14: the route reports
    #: them as ``details.unreadable`` on ``state_version_migration_unconfirmed``, and an unreadable
    #: state file counts as blocked (it may be the v7 file we must not write over).
    state_versions_unreadable: tuple[str, ...] = ()
    #: Uninstall binds confirmation to one receipt and repository identity, independently of payload.
    receipt_id: str | None = None
    repo_identity: str | None = None
    target_receipt_id: str | None = None
    backup_transaction_id: str | None = None
    rollback_receipt_digest: str | None = None
    recovery_transaction_id: str | None = None
    recovery_kind: str | None = None
    recovery_evidence_digest: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "kind": self.kind,
            "engine_from": self.engine_from,
            "engine_to": self.engine_to,
            "studio_version": self.studio_version,
            "payload_digest": self.payload_digest,
            "entries": [e.to_json() for e in self.entries],
            "counts": dict(self.counts),
            "blocking": self.blocking,
            "blockers": [e.to_json() for e in self.blockers],
            "warnings": [w.to_json() for w in self.warnings],
            "newer_installed": self.newer_installed,
            "same_version": self.same_version,
            "requires_admin_lease": self.requires_admin_lease,
            "bytes_to_write": self.bytes_to_write,
            "state_versions_found": list(self.state_versions_found),
            "state_version_blocked": self.state_version_blocked,
            "state_versions_unreadable": list(self.state_versions_unreadable),
            "preflight": self.preflight.to_json() if self.preflight is not None else None,
            **({"receipt_id": self.receipt_id, "repo_identity": self.repo_identity}
               if self.kind in {"uninstall", "rollback"} else {}),
            **({"target_receipt_id": self.target_receipt_id,
                "backup_transaction_id": self.backup_transaction_id,
                "rollback_receipt_digest": self.rollback_receipt_digest}
               if self.kind == "rollback" else {}),
            **({"recovery_transaction_id": self.recovery_transaction_id,
                "recovery_kind": self.recovery_kind,
                "recovery_evidence_digest": self.recovery_evidence_digest}
               if self.recovery_transaction_id else {}),
        }

    def digest(self) -> str:
        """The ``plan_digest`` of §2.2: what the user confirmed, hashed.

        Deliberately excludes ``preflight`` and the diff bodies. Preflight carries free space and a
        write-permission probe, which move between the preview request and the confirmation a few
        seconds later; including them would make every confirmed install fail with ``preview_changed``
        and teach users to ignore the check that actually matters. Diffs are derived from the digests
        that *are* included, so omitting them removes nothing from the comparison.
        """
        return _canonical_json_digest(self._digest_data())

    def _digest_data(self) -> dict:
        """The exact confirmation material, also persisted with a removal journal."""
        payload = {
            "repo_id": self.repo_id,
            "kind": self.kind,
            "engine_from": self.engine_from,
            "engine_to": self.engine_to,
            "studio_version": self.studio_version,
            "payload_digest": self.payload_digest,
            "entries": [e.digest_json() for e in self.entries],
            "counts": dict(self.counts),
            "blocking": self.blocking,
            "newer_installed": self.newer_installed,
            "same_version": self.same_version,
            "bytes_to_write": self.bytes_to_write,
            "state_versions_found": list(self.state_versions_found),
            "state_version_blocked": self.state_version_blocked,
            "state_versions_unreadable": list(self.state_versions_unreadable),
        }
        if self.kind in {"uninstall", "rollback"}:
            payload.update(receipt_id=self.receipt_id, repo_identity=self.repo_identity)
        if self.kind == "rollback":
            payload.update(
                target_receipt_id=self.target_receipt_id, backup_transaction_id=self.backup_transaction_id,
                rollback_receipt_digest=self.rollback_receipt_digest,
            )
        if self.recovery_transaction_id:
            payload.update(
                recovery_transaction_id=self.recovery_transaction_id, recovery_kind=self.recovery_kind,
                recovery_evidence_digest=self.recovery_evidence_digest,
            )
        return payload


# --------------------------------------------------------------------------- #
# transaction records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StepRecord:
    """One step of one transaction. ``ok is None`` means it started and never finished."""

    name: str
    started_at: str
    finished_at: str | None
    ok: bool | None
    detail: dict

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "ok": self.ok,
            "detail": dict(self.detail),
        }

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "StepRecord":
        return cls(
            name=str(d.get("name") or ""),
            started_at=str(d.get("started_at") or ""),
            finished_at=d.get("finished_at"),
            ok=d.get("ok"),
            detail=dict(d.get("detail") or {}),
        )


@dataclass(frozen=True, slots=True)
class TransactionResult:
    """The durable story of one install/upgrade/recovery attempt."""

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

    def to_json(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "repo_id": self.repo_id,
            "kind": self.kind,
            "status": self.status,
            "steps": [s.to_json() for s in self.steps],
            "receipt_id": self.receipt_id,
            "prior_receipt_id": self.prior_receipt_id,
            "error": self.error,
            "failed_dir": self.failed_dir,
            "repo_install_status": self.repo_install_status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "engine_version": self.engine_version,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any], *, repo_install_status: str = "") -> "TransactionResult":
        steps = row.get("steps_json") or []
        if isinstance(steps, str):  # a row read before Storage decoded it
            try:
                steps = json.loads(steps)
            except ValueError:
                steps = []
        return cls(
            transaction_id=str(row.get("transaction_id") or ""),
            repo_id=str(row.get("repo_id") or ""),
            kind=str(row.get("kind") or ""),
            status=str(row.get("status") or ""),
            steps=tuple(StepRecord.from_json(s) for s in steps if isinstance(s, Mapping)),
            receipt_id=row.get("receipt_id"),
            prior_receipt_id=row.get("prior_receipt_id"),
            error=row.get("error"),
            failed_dir=row.get("failed_dir"),
            repo_install_status=repo_install_status,
            started_at=str(row.get("started_at") or ""),
            finished_at=row.get("finished_at"),
            engine_version=str(row.get("engine_version") or ""),
        )


@dataclass(frozen=True, slots=True)
class ReceiptFile:
    """One path Studio owns, and how ownership is verified.

    ``kind == "fragment"`` records ``canonical_digest`` — the digest of the managed fragment, never of
    the whole file, because the rest of a merge target belongs to the project (FR-INST-006).
    ``adopted`` marks a file that was already byte-correct when Studio arrived: nothing was written,
    but the path is owned from now on, which is what makes a later upgrade able to replace it.
    """

    path: str
    ownership: str
    kind: str
    sha256: str | None
    canonical_digest: str | None
    fragment_key: str | None
    adopted: bool
    #: Fragment provenance, not user bytes. Stored additively in the existing files_json column.
    uninstall: dict[str, Any] | None = None
    #: Receipt-level compatibility proof is stored once on the first file, without a schema migration.
    engine_state_versions: tuple[int, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ownership": self.ownership,
            "kind": self.kind,
            "sha256": self.sha256,
            "canonical_digest": self.canonical_digest,
            "fragment_key": self.fragment_key,
            "adopted": self.adopted,
            **({"uninstall": self.uninstall} if self.uninstall is not None else {}),
            **({"engine_state_versions": list(self.engine_state_versions)}
               if self.engine_state_versions else {}),
        }

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> "ReceiptFile":
        versions = d.get("engine_state_versions")
        if not isinstance(versions, list) or not all(type(v) is int and v > 0 for v in versions):
            versions = []
        return cls(
            path=str(d.get("path") or ""),
            ownership=str(d.get("ownership") or ""),
            kind=str(d.get("kind") or "file"),
            sha256=d.get("sha256"),
            canonical_digest=d.get("canonical_digest"),
            fragment_key=d.get("fragment_key"),
            adopted=bool(d.get("adopted")),
            uninstall=dict(d["uninstall"]) if isinstance(d.get("uninstall"), Mapping) else None,
            engine_state_versions=tuple(versions),
        )


@dataclass(frozen=True, slots=True)
class Receipt:
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
        out: dict[str, Any] = {
            "receipt_id": self.receipt_id,
            "repo_id": self.repo_id,
            "resolved_repo_identity": self.resolved_repo_identity,
            "studio_version": self.studio_version,
            "engine_version": self.engine_version,
            "payload_digest": self.payload_digest,
            "committed_at": self.committed_at,
            "prior_receipt_id": self.prior_receipt_id,
            "transaction_id": self.transaction_id,
            "status": self.status,
        }
        out["files"] = [f.to_json() for f in self.files] if include_files else None
        return out

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Receipt":
        files = row.get("files_json") or []
        if isinstance(files, str):
            try:
                files = json.loads(files)
            except ValueError:
                files = []
        return cls(
            receipt_id=str(row.get("receipt_id") or ""),
            repo_id=str(row.get("repo_id") or ""),
            resolved_repo_identity=row.get("resolved_repo_identity"),
            studio_version=str(row.get("studio_version") or ""),
            engine_version=str(row.get("engine_version") or ""),
            payload_digest=str(row.get("payload_digest") or ""),
            committed_at=str(row.get("committed_at") or ""),
            prior_receipt_id=row.get("prior_receipt_id"),
            transaction_id=str(row.get("transaction_id") or ""),
            files=tuple(ReceiptFile.from_json(f) for f in files if isinstance(f, Mapping)),
            status=str(row.get("status") or ""),
        )

    def by_path(self) -> dict[str, ReceiptFile]:
        return {f.path: f for f in self.files}


# --------------------------------------------------------------------------- #
# internal bookkeeping (not part of the module's public contract)
# --------------------------------------------------------------------------- #


@dataclass
class _Journal:
    """What rollback needs to know, filled while the transaction runs.

    A list rather than an inference: after a crash, "which files did this transaction create?" cannot
    be recomputed from the repository — the answer depends on what was there *before*, and that is
    exactly the information the repository no longer has.
    """

    txid: str
    staging_dir: Path
    backup_dir: Path
    #: repo-relative path → digest of the backed-up copy (``None`` for a file that was absent).
    backups: dict[str, str | None] = field(default_factory=dict)
    backup_modes: dict[str, int] = field(default_factory=dict)
    #: repo-relative paths this transaction brought into existence.
    created: list[str] = field(default_factory=list)
    #: repo-relative paths written or merged, with the digest they must have afterwards.
    written: dict[str, str] = field(default_factory=dict)
    #: repo-relative paths deleted as retirement, so rollback can put them back.
    retired: list[str] = field(default_factory=list)
    receipt_id: str | None = None
    prior_receipt_id: str | None = None
    prior_install_status: str = "not_installed"
    prior_engine_version: str | None = None
    prior_engine_dir: str | None = None
    prior_receipt_version: str | None = None
    receipt_inserted: bool = False
    cancelling: bool = False
    lease_generation: int | None = None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def version_tuple(text: str | None) -> tuple[int, ...] | None:
    """``"2.6.2"`` → ``(2, 6, 2)``; ``None`` when it is not a dotted numeric version.

    Unparseable is not "older": an engine whose version string Studio cannot read is not proven to be
    behind the payload, so the version comparison abstains rather than offering an upgrade.
    """
    if not text:
        return None
    parts: list[int] = []
    for chunk in str(text).strip().split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            return None
        parts.append(int(digits))
    return tuple(parts) or None


def payload_refresh_available(
    repo: "RepoRecord",
    receipt: Receipt | Mapping[str, Any] | None,
    own_engine_version: str | None,
    manifest: PayloadManifest,
) -> bool:
    """A current Studio receipt can authorize new payload bytes at the same engine version.

    The registry's receipt summary deliberately omits identity and payload digest; it cannot grant
    this exception. Callers supply the full current SQLite receipt, scoped to this repository.
    File drift, state compatibility and leases remain separate transaction checks.
    """
    prior_digest = C.attr(receipt, "payload_digest")
    digest = manifest.payload_digest
    return bool(
        repo.resolved_identity
        and version_tuple(own_engine_version)
        and own_engine_version == manifest.engine_version
        and C.attr(receipt, "status") == "current"
        and C.attr(receipt, "repo_id") == repo.repo_id
        and C.attr(receipt, "resolved_repo_identity") == repo.resolved_identity
        and C.attr(receipt, "engine_version") == own_engine_version
        and isinstance(prior_digest, str) and len(prior_digest) == 64
        and all(ch in "0123456789abcdef" for ch in prior_digest)
        and isinstance(digest, str) and len(digest) == 64
        and all(ch in "0123456789abcdef" for ch in digest)
        and prior_digest != digest
    )


def _resolve_key(obj: Any, dotted: str) -> tuple[bool, Any]:
    """``(found, value)`` for a managed key, literal spelling first, then a nested walk.

    Both spellings are real. Kiro's ``cli.json`` stores ``"chat.defaultAgent"`` as one flat key with a
    dot in its name, while ``mcp.json``'s ``mcpServers.context7`` is genuinely nested. Trying the
    literal key first is what makes one manifest key list describe both files, and it is also the safe
    order: a file that already holds the flat key must be read (and later written) in that spelling,
    or Studio would add a second, nested key that Kiro never reads.
    """
    if isinstance(obj, Mapping) and dotted in obj:
        return (True, obj[dotted])
    node = obj
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return (False, None)
        node = node[part]
    return (True, node)


def _assign_key(obj: dict, dotted: str, value: Any, *, nested: bool) -> None:
    """Write a managed key in whichever spelling the file already uses (``nested`` decides new keys).

    Sibling order is preserved because an existing key is updated in place; a genuinely new key lands
    at the end, which is where a human editing the file would also put it.
    """
    if dotted in obj:
        obj[dotted] = value
        return
    parts = dotted.split(".")
    if not nested or len(parts) == 1:
        obj[dotted] = value
        return
    node: dict = obj
    for part in parts[:-1]:
        child = node.get(part)
        if child is None:
            child = {}
            node[part] = child
        elif not isinstance(child, dict):
            # Replacing it would discard whatever the user put here, silently. `live_fragment` already
            # reports this as a `merge_conflict`, so reaching this line means a future caller found
            # another way in; refusing keeps that from becoming data loss.
            raise StudioError(
                "install_conflict",
                f"{part!r} holds a value that is not an object, so {dotted!r} cannot be written",
                details={"key": dotted, "parent": part, "reason": "managed_parent_not_an_object"},
            )
        node = child
    node[parts[-1]] = value


def _canonical_json_digest(value: Any) -> str:
    return security.sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _strict_json_object(data: bytes) -> dict:
    """Refuse ambiguous JSON before removing a key; duplicate keys hide user-owned values."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    obj = json.loads(data.decode("utf-8"), object_pairs_hook=unique)
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    return obj


def _remove_json_keys(
    data: bytes, keys: Sequence[str], parents: Sequence[Sequence[str]],
    *, entry_paths: Sequence[Sequence[str]] = (),
) -> bytes:
    """Remove owned members without reserializing any retained key, value or string escape."""
    obj = _strict_json_object(data)
    text = data.decode("utf-8")
    decoder = json.JSONDecoder()
    objects: dict[tuple[str, ...], list[tuple[str, int, int]]] = {}

    def whitespace(pos):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        return pos

    def scan(pos, prefix):
        members = []
        pos = whitespace(pos + 1)
        while text[pos] != "}":
            start = pos
            key, pos = decoder.raw_decode(text, pos)
            pos = whitespace(pos)
            pos = whitespace(pos + 1)  # validated ':' from the strict parse above
            value, end = decoder.raw_decode(text, pos)
            if isinstance(value, dict):
                scan(pos, (*prefix, key))
            members.append((key, start, end))
            pos = whitespace(end)
            if text[pos] == ",":
                pos = whitespace(pos + 1)
        objects[prefix] = members

    scan(whitespace(0), ())
    removed: set[tuple[str, ...]] = set()
    paths = [(key,) if key in obj else tuple(key.split(".")) for key in keys]
    paths.extend(tuple(path) for path in entry_paths)
    for path in paths:
        node = obj
        for part in path[:-1]:
            node = node.get(part)
            if not isinstance(node, dict):
                break
        else:
            if path[-1] in node:
                del node[path[-1]]
                removed.add(path)
    for parts in sorted(parents, key=len, reverse=True):
        path = tuple(parts)
        if not path:
            continue
        node = obj
        for part in path[:-1]:
            node = node.get(part)
            if not isinstance(node, dict):
                break
        else:
            if node.get(path[-1]) == {}:
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


def _is_text(data: bytes | None) -> bool:
    if data is None:
        return False
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _unified_diff(rel: str, old: bytes | None, new: bytes | None) -> str | None:
    """A bounded, redacted unified diff, or ``None`` when either side is not text.

    Binary bytes are never rendered: a diff is shown to a human deciding whether to keep their file,
    and a screen of escaped bytes helps nobody while risking a secret being displayed in a form no
    redactor recognises.
    """
    if not _is_text(old) or not _is_text(new):
        return None
    old_lines = old.decode("utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    new_lines = new.decode("utf-8").splitlines(keepends=True)  # type: ignore[union-attr]
    lines = difflib.unified_diff(
        old_lines, new_lines, fromfile=f"a/{rel}", tofile=f"b/{rel}", n=_DIFF_CONTEXT_LINES
    )
    # Truncated while building rather than after: a large file must not be joined into memory first
    # just to throw most of it away.
    out: list[str] = []
    size = 0
    for line in lines:
        size += len(line)
        if size > _MAX_DIFF_BYTES:
            out.append("\n… diff truncated …\n")
            break
        out.append(line)
    if not out:
        return None
    return security.redact("".join(out))


def _changed_paths(journal: "_Journal") -> set[str]:
    """Every repo-relative path this transaction modified: written, merged, or retired.

    Rollback restores exactly this set, and its proof checks exactly this set. The backup set is wider on
    purpose — it covers everything the plan might touch plus the standing receipt's files — but a path in
    the backup and not in here was never changed, so putting a copy of it back would overwrite whatever is
    there now, which may be an edit the user made while the install was running.
    """
    return set(journal.written) | set(journal.retired)


def _atomic_write(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` via a temporary file in the same directory plus ``os.replace``.

    Same directory so the replace is atomic (a rename across filesystems is not), and ``fsync`` before
    the replace so a power loss cannot leave a file that exists with the wrong contents — which is the
    one outcome the receipt cannot detect later.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f".{target.name}.aidlc-studio.tmp"
    # The temp file lives in the USER'S repository, so it must not outlive this call. A leftover
    # `.<name>.aidlc-studio.tmp` after a failed write leaves the tree in a state neither the old receipt
    # nor the new one describes: the transaction reports `rolled_back`, the drift check says the managed
    # set is intact, and the user is left with Studio's litter in their checkout and no way to know it is
    # safe to delete. So the unlink is on every failure path, including a cancellation at shutdown.
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(target))
    except BaseException:
        # `missing_ok`: `os.open` itself may be what failed, and a second exception here would replace
        # the real one with a misleading `FileNotFoundError`.
        tmp.unlink(missing_ok=True)
        raise


def _read_bytes(path: Path) -> bytes | None:
    """Whole-file bytes for a regular file; ``None`` for absent, a symlink, or unreadable.

    Deliberately lossy, and safe for every caller that is reading Studio's OWN files (the payload, the
    staging directory, a backup) because for those "cannot read" and "not there" lead to the same
    refusal. For a file in the USER'S repository the two are not the same, and a decision that turns on
    the difference must use :func:`_live_bytes` instead.
    """
    if not security.is_regular_file(path):
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _live_bytes(path: Path) -> tuple[bytes | None, bool]:
    """``(bytes, unreadable)`` for a path in the user's repository.

    ``(None, False)`` is genuinely absent. ``(None, True)`` is "something is there and Studio cannot
    read it" — a file mode of 0000, a permission the user set, an I/O error on a failing disk.

    The distinction is not academic. Reading an unreadable file as absent walks the install straight
    through three steps that each look correct on their own: the preview calls the entry ``create``, the
    backup records "nothing was here", and the write appends the path to ``journal.created``. The file is
    then overwritten, and a rollback DELETES it, because the record says Studio made it. The paths this
    can happen to include AI-DLC's ``active-space`` cursor and the user's own memory content, so the
    outcome is a person losing work that Studio promised never to touch.

    A symlink, directory or device answers ``(None, False)``: every caller refuses those separately and
    by name, and reporting them as unreadable would replace a precise error with a vague one.
    """
    try:
        st = path.lstat()
    except FileNotFoundError:
        return None, False
    except OSError:
        # The parent directory is unsearchable, or the filesystem is failing. Something is in the way of
        # an answer, which is the definition of missing evidence.
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
    return Finding(
        code=code, severity=severity, message_key=f"finding.{code}", params=params,
        evidence=tuple(evidence),
    )


# --------------------------------------------------------------------------- #
# merge fragment handlers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Fragment:
    """One side of a merge target reduced to its managed part.

    ``digest`` is what the receipt records; ``conflict`` says a managed key/block is present with
    content that is not ours, which is the one case a merge may not resolve on its own.
    """

    digest: str | None
    conflict: bool = False
    conflict_detail: tuple[str, ...] = ()
    #: Rendered form of the managed part, for the preview diff. ``None`` when it cannot be rendered.
    text: str | None = None


class _MergeHandler:
    """Base for the three strategies. Subclasses read the payload side and rewrite the live side."""

    strategy = ""

    def __init__(self, rel: str, spec: Mapping[str, Any], payload_bytes: bytes | None) -> None:
        self.rel = rel
        self.spec = dict(spec)
        self.payload_bytes = payload_bytes

    # -- identity --------------------------------------------------------- #

    @property
    def fragment_key(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def payload_fragment(self) -> _Fragment:  # pragma: no cover - overridden
        raise NotImplementedError

    def live_fragment(self, live: bytes | None) -> _Fragment:  # pragma: no cover - overridden
        raise NotImplementedError

    def merged_bytes(self, live: bytes | None, *, receipt_digest: str | None = None) -> bytes:
        """``live`` written back with the payload fragment in it.

        ``receipt_digest`` is what Studio's own receipt claimed this fragment digested to before the
        transaction started, i.e. proof of authorship for bytes already on disk. Only ``_FencedBlock``
        has anything to do with it, but every handler accepts it: the caller holds the base type.
        """
        raise NotImplementedError  # pragma: no cover - overridden

    #: Whether a live fragment that differs from both payload and receipt can be resolved by merging
    #: (append-only strategies) or must block (strategies that replace a value).
    can_overwrite_user_change = False


class _JsonManagedKeys(_MergeHandler):
    """``.kiro/settings/cli.json`` / ``.kiro/settings/mcp.json``: only the listed dotted keys are ours.

    Every other key — a user's ``toolSearch.*`` setting, their own MCP server with an embedded token —
    is round-tripped through ``json.load``/``json.dumps``, which preserves insertion order, so the file
    the user gets back differs only in the managed values.
    """

    strategy = "json-managed-keys"

    @property
    def managed_keys(self) -> tuple[str, ...]:
        keys = self.spec.get("managedKeys") or []
        return tuple(str(k) for k in keys)

    @property
    def managed_map_entries(self) -> dict[str, tuple[str, ...]]:
        """Explicit literal model IDs; never dotted paths or recursive merge instructions."""
        if "managedMapEntries" not in self.spec:
            return {}
        entries = self.spec["managedMapEntries"]
        if (
            self.rel != ".kiro/settings/cli.json"
            or not isinstance(entries, dict) or set(entries) != {"chat.modelDefaults"}
            or "chat.modelDefaults" not in self.managed_keys
            or not isinstance(entries["chat.modelDefaults"], list)
            or not all(isinstance(key, str) and key for key in entries["chat.modelDefaults"])
            or len(set(entries["chat.modelDefaults"])) != len(entries["chat.modelDefaults"])
        ):
            raise StudioError("payload_degraded", "invalid managed model-entry metadata",
                              details={"path": self.rel})
        return {key: tuple(names) for key, names in entries.items()}

    @property
    def fragment_key(self) -> str:
        return f"{Path(self.rel).name}:{','.join(self.managed_keys)}"

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
            except (ValueError, UnicodeDecodeError) as exc:
                raise StudioError("payload_degraded", "invalid payload model defaults",
                                  details={"path": self.rel}) from exc
        try:
            obj = json.loads(self.payload_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return obj if isinstance(obj, dict) else {}

    def _managed_view(self, obj: Mapping[str, Any] | None) -> dict[str, Any]:
        """The managed keys that are actually present, keyed by their dotted name.

        Absent keys are omitted rather than recorded as ``null``: a file that simply lacks a managed
        key needs it added (``merge_update``), which is a different situation from one where the user
        set it to ``null`` deliberately — and the second must not be silently overwritten.
        """
        out: dict[str, Any] = {}
        if obj is None:
            return out
        for key in self.managed_keys:
            found, value = _resolve_key(obj, key)
            if found:
                if key in self.managed_map_entries:
                    value = {name: value[name] for name in self.managed_map_entries[key] if name in value}
                out[key] = value
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
        """Managed keys whose dotted PARENT holds something that is not an object, worst first.

        Only the nested spelling can hit this: a key the file already carries flat (``"a.b": 1``) is
        resolved and written whole, and never walked.
        """
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
        return _Fragment(digest=_canonical_json_digest(view),
                         text=json.dumps(view, indent=2, sort_keys=True) + "\n")

    def live_fragment(self, live: bytes | None) -> _Fragment:
        if live is None:
            return _Fragment(digest=None)
        try:
            obj = (
                _strict_json_object(live) if self.managed_map_entries
                else json.loads(live.decode("utf-8"))
            )
        except (ValueError, UnicodeDecodeError):
            # Unparseable JSON at a managed path is a conflict, never something to overwrite: the file
            # may be mid-edit, and rewriting it would destroy whatever the user was doing.
            return _Fragment(digest=None, conflict=True, conflict_detail=("unparseable",))
        if not isinstance(obj, dict):
            return _Fragment(digest=None, conflict=True, conflict_detail=("not_an_object",))
        blocked = self._managed_parents_not_objects(obj) + self._model_map_problems(obj)
        if blocked:
            # A managed key is `mcpServers.aws-iac`, and the user has put something else at
            # `mcpServers` — a list, a string, `null`. `_resolve_key` cannot walk into that, so the key
            # reads as simply absent, the entry becomes `merge_update`, and `_assign_key` then REPLACES
            # the user's value with a fresh object. No conflict, no force flag, and a preview diff that
            # does not show it, because the managed view never contained the key.
            return _Fragment(digest=None, conflict=True, conflict_detail=blocked)
        view = self._managed_view(obj)
        payload_view = self._managed_view(self._payload_obj())
        differing = tuple(
            key for key in self.managed_keys
            if key in view and (
                any(value != payload_view.get(key, {}).get(name) for name, value in view[key].items())
                if key in self.managed_map_entries else view.get(key) != payload_view.get(key)
            )
        )
        return _Fragment(
            digest=_canonical_json_digest(view),
            conflict=bool(differing),
            conflict_detail=differing,
            text=json.dumps(view, indent=2, sort_keys=True) + "\n",
        )

    def merged_bytes(self, live: bytes | None, *, receipt_digest: str | None = None) -> bytes:
        # No use for `receipt_digest`: a managed key is replaced key-by-key, so authorship of the rest
        # of the file never enters into it.
        obj: dict[str, Any] = {}
        if live is not None:
            try:
                decoded = (
                    _strict_json_object(live) if self.managed_map_entries
                    else json.loads(live.decode("utf-8"))
                )
                if isinstance(decoded, dict):
                    obj = decoded
            except (ValueError, UnicodeDecodeError):
                raise StudioError(
                    "install_conflict", f"{self.rel} is not valid JSON and cannot be merged",
                    details={"path": self.rel},
                ) from None
        if self._model_map_problems(obj) or (
            self.managed_map_entries and self._managed_parents_not_objects(obj)
        ):
            raise StudioError("install_conflict", "ambiguous model defaults cannot be merged")
        payload_obj = self._payload_obj()
        for key in self.managed_keys:
            found, value = _resolve_key(payload_obj, key)
            if not found:
                continue
            if key in self.managed_map_entries:
                present, current = _resolve_key(obj, key)
                merged = dict(current) if present else {}
                for name in self.managed_map_entries[key]:
                    merged[name] = value[name]
                value = merged
            # Mirror the payload's own spelling for a key neither file has yet.
            nested = key not in payload_obj
            if key in self.managed_map_entries and _resolve_key(obj, key)[0]:
                nested = key not in obj
            _assign_key(obj, key, value, nested=nested)
        return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


class _FencedBlock(_MergeHandler):
    """``AGENTS.md``: Studio owns only what is between the two markers.

    The payload file ships without markers (it is a whole document), so the block body is the payload
    text and the markers are added when it is written. That is also why a live file that happens to be
    byte-identical to the payload counts as the block having been adopted verbatim: it is the same
    content, and refusing to recognise it would block an install over bytes Studio itself ships. The
    next payload then replaces that marker-less document rather than appending to it — see
    ``merged_bytes``, which is where a repository that adopted the file rejoins the fenced regime.
    """

    strategy = "append-fenced-block"
    can_overwrite_user_change = False

    @property
    def marker_start(self) -> str:
        return str(self.spec.get("markerStart") or "")

    @property
    def marker_end(self) -> str:
        return str(self.spec.get("markerEnd") or "")

    @property
    def fragment_key(self) -> str:
        """``AGENTS.md:aidlc-studio:managed`` — the key names the block, not the marker.

        Derived from the manifest's own ``markerStart`` (so a renamed marker renames the key) with the
        HTML comment syntax and the trailing ``:start`` removed: the pair of markers delimits one
        fragment, and a receipt entry that said ``…:managed:start`` would read as if only the opening
        marker were owned.
        """
        token = self.marker_start.strip()
        if token.startswith("<!--"):
            token = token[4:]
        if token.endswith("-->"):
            token = token[:-3]
        token = token.strip()
        if token.endswith(":start"):
            token = token[: -len(":start")]
        return f"{Path(self.rel).name}:{token}"

    def _payload_body(self) -> str:
        if self.payload_bytes is None:
            return ""
        return self.payload_bytes.decode("utf-8", "replace").strip("\n")

    def payload_fragment(self) -> _Fragment:
        body = self._payload_body()
        return _Fragment(digest=security.sha256_text(body), text=body + "\n")

    def _extract(self, text: str) -> str | None:
        start = text.find(self.marker_start)
        if start < 0:
            return None
        after = start + len(self.marker_start)
        if text[after : after + 1] == "\n":
            after += 1
        end = text.find(self.marker_end, after)
        if end < 0:
            return None
        body = text[:end][after:]
        return body[:-1] if body.endswith("\n") else body

    def live_fragment(self, live: bytes | None) -> _Fragment:
        if live is None:
            return _Fragment(digest=None)
        text = live.decode("utf-8", "replace")
        body = self._extract(text)
        if body is None and self.marker_start and self.marker_start in text:
            # The opening marker is there and the closing one is not, so the file has a Studio block that
            # was cut short — a hand edit, or an interrupted write. Treating it as "no markers" would
            # digest the WHOLE document as the fragment and report `conflict=False`, and the merge would
            # then append a SECOND block, leaving two opening markers and one closing one. The honest
            # answer is to ask the user to repair their file.
            return _Fragment(digest=None, conflict=True, conflict_detail=("unterminated_block",))
        if body is None:
            # No markers. The digest is the whole document, which does two jobs: a file Studio shipped
            # verbatim hashes to the payload fragment digest by construction (so adoption is
            # recognisable, and still recognisable against a LATER payload's receipt — the first
            # upgrade of an adopted file must not read as a conflict), and anybody else's document
            # hashes to something that matches no payload and no receipt.
            #
            # `conflict` stays False on purpose: appending a block to a document that has none takes
            # nothing away from the author.
            whole = text.strip("\n")
            return _Fragment(digest=security.sha256_text(whole), text=whole + "\n")
        payload_digest = self.payload_fragment().digest
        digest = security.sha256_text(body)
        return _Fragment(
            digest=digest,
            conflict=digest != payload_digest,
            conflict_detail=("block_modified",) if digest != payload_digest else (),
            text=body + "\n",
        )

    def merged_bytes(self, live: bytes | None, *, receipt_digest: str | None = None) -> bytes:
        """Three regimes, and the middle one is why this signature carries a receipt digest.

        * Markers present: replace what is between them. The ordinary case, and the one FR-INST-006
          describes — a merge target is never a wholesale replacement target, only its block is.
        * No markers, and the whole document digests to what Studio's receipt recorded: the user
          followed AI-DLC's own quick start (``cp dist/kiro/AGENTS.md your-project/``), Studio adopted
          those bytes verbatim (FR-INST-007), and nobody has edited them since. Appending here is what
          the bug was: the repository ended up holding TWO complete AI-DLC instruction documents that
          contradict each other on hook counts and subcommand lists, and the harness loads both. So
          the document is replaced by the marked block. This is not FR-INST-006 turning into
          replacement: the digest is the proof that every byte being dropped is Studio's own adopted
          copy of an older payload. Writing the MARKERS is half the fix — a marker-less file leaves
          the receipt owning the whole document, so a later title line of the user's own reads as
          ``merge_conflict``, whereas a fenced file converges and only the block is ever claimed.
        * Anything else: append. A document Studio cannot prove it wrote is the author's, and the
          fragment Studio does own is compared before we get here — an edited block is
          ``merge_conflict`` and never reaches this method.
        """
        body = self._payload_body()
        block = f"{self.marker_start}\n{body}\n{self.marker_end}\n"
        if live is None:
            return block.encode("utf-8")
        text = live.decode("utf-8", "replace")
        start = text.find(self.marker_start)
        if start >= 0:
            end = text.find(self.marker_end, start)
            if end >= 0:
                tail = text[end + len(self.marker_end) :]
                if tail.startswith("\n"):
                    tail = tail[1:]
                return (text[:start] + block + tail).encode("utf-8")
        if start < 0 and receipt_digest is not None and (
            # The same digest `live_fragment` reports for a marker-less file, so it is comparable with
            # the value the receipt actually holds. `start < 0` keeps a cut-short block out of here:
            # that file is `merge_conflict` for the user to repair, not ours to overwrite.
            security.sha256_text(text.strip("\n")) == receipt_digest
        ):
            return block.encode("utf-8")
        prefix = text if text.endswith("\n") or not text else text + "\n"
        separator = "\n" if prefix else ""
        return (prefix + separator + block).encode("utf-8")


class _MissingLines(_MergeHandler):
    """``.gitignore``: append the payload's lines that are not already there, in payload order.

    Append-only, so it can never conflict — every line the user wrote stays, and a line the payload
    wants is either already present or is added at the end under a comment of its own making. That is
    why a user-modified ``.gitignore`` is ``merge_update``, not ``merge_conflict``.
    """

    strategy = "append-missing-lines"
    can_overwrite_user_change = True

    @property
    def fragment_key(self) -> str:
        return f"{Path(self.rel).name}:lines"

    def _payload_lines(self) -> list[str]:
        if self.payload_bytes is None:
            return []
        return [
            line for line in self.payload_bytes.decode("utf-8", "replace").splitlines() if line.strip()
        ]

    def payload_fragment(self) -> _Fragment:
        lines = self._payload_lines()
        return _Fragment(digest=security.sha256_text("\n".join(lines)), text="\n".join(lines) + "\n")

    def live_fragment(self, live: bytes | None) -> _Fragment:
        if live is None:
            return _Fragment(digest=None)
        present = {line.strip() for line in live.decode("utf-8", "replace").splitlines()}
        kept = [line for line in self._payload_lines() if line.strip() in present]
        return _Fragment(digest=security.sha256_text("\n".join(kept)), text="\n".join(kept) + "\n")

    def merged_bytes(self, live: bytes | None, *, receipt_digest: str | None = None) -> bytes:
        # No use for `receipt_digest`: appending the missing lines is right whoever wrote the file.
        payload_lines = self._payload_lines()
        if live is None:
            return ("\n".join(payload_lines) + "\n").encode("utf-8")
        text = live.decode("utf-8", "replace")
        present = {line.strip() for line in text.splitlines()}
        missing = [line for line in payload_lines if line.strip() not in present]
        if not missing:
            return live
        prefix = text if text.endswith("\n") or not text else text + "\n"
        return (prefix + "\n".join(missing) + "\n").encode("utf-8")


_HANDLERS: dict[str, type[_MergeHandler]] = {
    _JsonManagedKeys.strategy: _JsonManagedKeys,
    _FencedBlock.strategy: _FencedBlock,
    _MissingLines.strategy: _MissingLines,
}


# --------------------------------------------------------------------------- #
# the installer
# --------------------------------------------------------------------------- #


class Installer:
    """Preview and transaction owner for the bundled payload."""

    def __init__(
        self,
        storage: "Storage",
        registry: "RepoRegistry",
        reader: "AidlcReader",
        engine: "EngineRunner",
        scheduler: "RepoScheduler",
        activity: "ActivityProjector",
        events: "EventLog",
        clock: "C.Clock",
        ids: "C.IdFactory",
        *,
        payload_dir: Path,
        manifest: PayloadManifest,
        data_dir: Path,
        fault_injector: Callable[[str], None] | None = None,
        settings: "SettingsService | None" = None,
        boot_id: str = "",
    ) -> None:
        self._storage = storage
        self._registry = registry
        self._reader = reader
        self._engine = engine
        self._scheduler = scheduler
        self._activity = activity
        self._events = events
        self._clock = clock
        self._ids = ids
        self._boot_id = str(boot_id or "")
        self._manifest = manifest
        self._data_dir = Path(data_dir)
        # `payload_dir` may name either `payload/` (which holds manifest.json) or the file root
        # `payload/aidlc-kiro/`. Manifest paths are relative to the file root, so resolve it once here
        # rather than making every caller know which spelling this constructor wanted.
        root = Path(payload_dir)
        nested = root / PAYLOAD_FILES_DIRNAME
        self._payload_root = nested if nested.is_dir() else root
        # Public so a test can install a fault at a step boundary (§4.1 `fault` fixture).
        self.fault_injector = fault_injector
        # Additive to the §1.14 signature: step 6 runs `doctor` only when
        # `settings.installer.run_doctor_after_install` is on, and that value has no other source.
        # Absent settings therefore mean the documented default — doctor does not run.
        self._settings = settings

    # ---- payload -------------------------------------------------------- #

    @property
    def manifest(self) -> PayloadManifest:
        return self._manifest

    @property
    def payload_root(self) -> Path:
        return self._payload_root

    def verify_payload(self) -> PayloadStatus:
        """Re-hash the whole payload and check that every merge target has a handler. Sync.

        Runs at startup, because a payload that does not match its manifest must degrade the app
        *before* a user can ask for an install, not fail halfway through one. The merge-strategy check
        is part of the same question: a ``merge``-ownership file with no ``mergeTargets`` entry, or an
        entry naming a strategy this build cannot execute, would install as "no fragment written" —
        an install that reports success and left ``chat.defaultAgent`` unset (review P10).
        """
        mismatches: list[str] = []
        missing: list[str] = []
        root = self._payload_root
        seen: set[str] = set()

        for pf in self._manifest.files:
            seen.add(pf.path)
            try:
                path = security.resolve_inside(root, pf.path)
            except StudioError:
                mismatches.append(f"path_escapes_payload:{pf.path}")
                continue
            data = _read_bytes(path)
            if data is None:
                missing.append(pf.path)
                continue
            if security.sha256_bytes(data) != pf.sha256:
                mismatches.append(f"sha256:{pf.path}")
            elif len(data) != pf.size:
                mismatches.append(f"size:{pf.path}")

        extra: list[str] = []
        if root.is_dir():
            for path in security.iter_regular_files(root, max_files=_MAX_PAYLOAD_WALK):
                rel = path.relative_to(root).as_posix()
                if rel not in seen:
                    extra.append(rel)

        if len(self._manifest.files) != self._manifest.file_count:
            mismatches.append("fileCount")
        if self._payload_digest() != self._manifest.payload_digest:
            mismatches.append("payloadDigest")
        mismatches.extend(self._merge_target_problems())

        return PayloadStatus(
            ok=not mismatches and not missing,
            engine_version=self._manifest.engine_version,
            file_count=len(self._manifest.files),
            mismatches=tuple(sorted(mismatches)),
            missing=tuple(sorted(missing)),
            extra=tuple(sorted(extra)),
            checked_at=self._clock.iso(),
        )

    def _payload_digest(self) -> str:
        """The manifest's own ``payloadDigest`` formula, recomputed from the manifest's rows.

        Over the manifest rather than over the disk so the two checks stay independent: per-file
        hashing above proves the bytes, this proves the *list* was not edited (a removed row would
        otherwise make a deleted file invisible, since a file that is not listed is never looked for).

        Rows are folded in the order the manifest lists them, which is the order the generator wrote
        them. Re-sorting here would mean reimplementing ``pathlib`` ordering (which is per component,
        not per byte) and getting a different digest for the same bytes.
        """
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
            elif strategy == "append-fenced-block" and not (
                spec.get("markerStart") and spec.get("markerEnd")
            ):
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
                # `aidlc/**` is the workspace AI-DLC and the user own. A manifest that declared a file
                # there as framework would make the installer overwrite a team's memory rules on every
                # upgrade, which is the one thing FR-INST-005 exists to prevent.
                problems.append(f"ownership_not_shell:{pf.path}")
        return problems

    def _handler(self, rel: str, *, source: Path | None = None) -> _MergeHandler:
        """The strategy object for one merge target, reading the payload side from ``source``.

        ``source`` is the staged copy once a transaction is running: the fragment that gets written and
        the fragment that gets validated must come from the same bytes the transaction verified, not
        from ``payload/`` which an app update could replace underneath it.
        """
        spec = self._manifest.merge_targets.get(rel) or {}
        strategy = str(spec.get("strategy") or "")
        cls = _HANDLERS.get(strategy)
        if cls is None:
            raise StudioError(
                "payload_degraded", f"no handler for merge strategy {strategy!r}",
                details={"path": rel, "strategy": strategy},
            )
        payload_bytes = _read_bytes(security.resolve_inside(source or self._payload_root, rel))
        return cls(rel, spec, payload_bytes)

    # ---- preview -------------------------------------------------------- #

    def preview(
        self, repo: "RepoRecord", kind: str, *, target_receipt_id: str | None = None
    ) -> PreviewPlan:
        """The full ownership × live-state matrix for one repository. Sync, read-only.

        Read-only in the strongest sense: it runs no engine verb (C35 — repo-resident TypeScript on a
        candidate path is untrusted) and opens no file for writing. Everything it reports is a
        comparison of three digests per path: what is on disk, what the payload ships, and what
        Studio's receipt claims to own.

        ``engine_from`` and ``newer_installed`` are read from
        ``health.own_engine_version`` — the version of ``.kiro`` alone — and deliberately not from
        ``health.engine_version``, which names the harness Studio *reads* and may be a foreign
        ``.claude`` / ``.codex`` / ``.cursor`` / ``.aidlc`` tree the user installed AI-DLC into
        themselves. A stranger's harness is not Studio's install: no receipt covers it, not one of its
        files is in this plan, and its version says nothing about what writing the payload here would
        replace. Keying the lane on it cost the user the whole feature — ``install`` was refused
        ``already_installed`` because something was installed, ``upgrade`` was refused
        ``same_version_installed`` because the stranger happened to be the payload's version, and
        Studio had no way left to add its own harness to that repository at all (FR-INST-003).
        Everything else in the plan is unchanged by this: the entries, the retirement set and the
        state-version guard are about paths and digests, which never depended on whose harness answered.
        ``same_version`` also checks receipt identity: a trusted current receipt with a different
        payload digest permits a refresh even when the upstream engine version has not changed.
        """
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
        rcpt_by_path = receipt.by_path() if receipt else {}
        health = self._registry.detect_install(repo, receipt)
        preflight = self._registry.preflight(repo.canonical_path)

        entries: list[PreviewEntry] = []
        bytes_to_write = 0
        for pf in self._manifest.files:
            entry = self._file_entry(root, pf, rcpt_by_path.get(pf.path))
            entries.append(entry)
            if entry.action in WRITING_ACTIONS:
                bytes_to_write += int(entry.size or 0)

        entries.extend(self._retire_entries(root, receipt))

        counts = {action: 0 for action in PreviewAction}
        for entry in entries:
            counts[entry.action] = counts.get(entry.action, 0) + 1
        blockers = tuple(e for e in entries if e.blocking)

        found, unreadable = self._state_versions(root)
        incompatible = tuple(
            v for v in found if v not in self._manifest.compatible_state_versions
        )
        state_version_blocked = bool(incompatible) or bool(unreadable)

        warnings: list[Finding] = [
            _finding(w.code, w.severity, dict(w.params), w.evidence) for w in preflight.warnings
        ]
        if incompatible:
            warnings.append(
                _finding(
                    "state_version_unsupported",
                    "blocking",
                    {
                        "found": list(found),
                        "incompatible": list(incompatible),
                        "compatible": list(self._manifest.compatible_state_versions),
                    },
                    (EvidenceRef("studio", f"repo:{repo.repo_id}", "State Version on disk"),),
                )
            )
        if unreadable:
            warnings.append(
                _finding(
                    "state_unreadable",
                    "blocking",
                    {"paths": list(unreadable)},
                    tuple(EvidenceRef("file", rel) for rel in unreadable[:8]),
                )
            )

        installed = version_tuple(health.own_engine_version)
        payload_version = version_tuple(self._manifest.engine_version)
        newer_installed = bool(installed and payload_version and installed > payload_version)
        same_version = bool(
            health.own_engine_version
            and health.own_engine_version == self._manifest.engine_version
            and not payload_refresh_available(
                repo, receipt, health.own_engine_version, self._manifest
            )
        )

        return PreviewPlan(
            repo_id=repo.repo_id,
            kind=kind,
            engine_from=health.own_engine_version,
            engine_to=self._manifest.engine_version,
            studio_version=C.APP_VERSION,
            payload_digest=self._manifest.payload_digest,
            entries=tuple(entries),
            counts=counts,
            blocking=bool(blockers),
            blockers=blockers,
            warnings=tuple(warnings),
            newer_installed=newer_installed,
            same_version=same_version,
            # Always true: the transaction takes the admin lease before it touches anything, even when
            # the plan turns out to write nothing, so that a concurrent turn cannot start mid-check.
            requires_admin_lease=True,
            bytes_to_write=bytes_to_write,
            state_versions_found=found,
            state_version_blocked=state_version_blocked,
            preflight=preflight,
            state_versions_unreadable=unreadable,
        )

    def _file_entry(
        self, root: Path, pf: PayloadFile, rcpt: ReceiptFile | None
    ) -> PreviewEntry:
        if pf.ownership == "merge":
            return self._merge_entry(root, pf, rcpt)

        try:
            target = security.resolve_inside(root, pf.path)
        except StudioError:
            # A managed path that resolves outside the repository (a symlinked directory pointing away)
            # can never be written; reporting it as a conflict keeps it in the blockers list where the
            # user will see it, instead of failing the whole preview.
            return PreviewEntry(
                path=pf.path, ownership=pf.ownership, action="conflict", live_sha256=None,
                payload_sha256=pf.sha256, receipt_sha256=None, size=pf.size, fragment_key=None,
                diff=None, blocking=True,
            )
        symlinked = (root / pf.path).is_symlink()
        live, unreadable = (None, False) if symlinked else _live_bytes(target)
        live_sha = _digest_of(live)
        rcpt_sha = rcpt.sha256 if rcpt else None

        if pf.ownership == "shell":
            # A shell file Studio cannot read is still a shell file that exists: leaving it alone is
            # already the behaviour, and `shell_exists` is how the preview says so.
            existing = symlinked or unreadable or live is not None
            action = "shell_exists" if existing else "shell_create"
            return PreviewEntry(
                path=pf.path, ownership=pf.ownership, action=action, live_sha256=live_sha,
                payload_sha256=pf.sha256, receipt_sha256=rcpt_sha, size=pf.size, fragment_key=None,
                diff=None, blocking=False,
            )

        if symlinked:
            action = "conflict"
        elif unreadable:
            # Not `create`: see `_live_bytes`. Blocking, so the user is asked about a file Studio cannot
            # see rather than having it replaced and then deleted by a rollback.
            action = "conflict"
        elif live is None:
            action = "create"
        elif live_sha == pf.sha256:
            action = "identical"
        elif rcpt_sha is None:
            action = "conflict"
        elif live_sha == rcpt_sha:
            action = "owned_identical"
        elif pf.ownership == "framework-mutable":
            action = "engine_modified"
        else:
            action = "owned_modified"

        blocking = action in BLOCKING_ACTIONS
        diff = None
        if blocking or action == "engine_modified":
            payload_bytes = _read_bytes(security.resolve_inside(self._payload_root, pf.path))
            diff = _unified_diff(pf.path, live, payload_bytes)
        return PreviewEntry(
            path=pf.path, ownership=pf.ownership, action=action, live_sha256=live_sha,
            payload_sha256=pf.sha256, receipt_sha256=rcpt_sha, size=pf.size, fragment_key=None,
            diff=diff, blocking=blocking,
        )

    def _merge_entry(
        self, root: Path, pf: PayloadFile, rcpt: ReceiptFile | None
    ) -> PreviewEntry:
        """Decide a merge target from its *fragment*, not from the whole file.

        Fragment equality with the payload is what "nothing to do" means (``merge_identical``): the
        user's own keys and lines are none of Studio's business, so a file that differs only outside
        the managed fragment must not be rewritten — reformatting somebody's JSON is a modification
        they did not ask for. §1.14's table spells the same rows out key-by-key; the fragment
        comparison is that table plus the case it leaves implicit.

        The receipt comparison spans payload versions, and that is sound for every change a payload can
        make except one: *removing* a managed key or renaming a marker changes what "the fragment" means,
        so yesterday's canonical digest and today's are not comparable. The result is
        ``merge_conflict`` — Studio cannot prove the fragment is still its own, so it asks instead of
        overwriting, which is the direction every ambiguity here resolves in.
        """
        handler = self._handler(pf.path)
        symlinked = (root / pf.path).is_symlink()
        target = security.resolve_inside(root, pf.path)
        live, unreadable = (None, False) if symlinked else _live_bytes(target)
        payload_fragment = handler.payload_fragment()
        live_fragment = handler.live_fragment(live)
        rcpt_digest = rcpt.canonical_digest if rcpt else None
        receipt_conflict = False
        if isinstance(handler, _JsonManagedKeys) and handler.managed_map_entries and rcpt is not None:
            try:
                previous = self._receipt_handler(rcpt)
                if live is not None and previous.live_fragment(live).digest != rcpt_digest:
                    receipt_conflict = True
            except StudioError:
                receipt_conflict = True
                live_fragment = replace(live_fragment, conflict=True, conflict_detail=(
                    *live_fragment.conflict_detail, "legacy_model_ownership_unknown",
                ))

        if symlinked or receipt_conflict:
            action = "merge_conflict"
        elif unreadable:
            # A merge writes the user's file back with Studio's fragment in it. Doing that to bytes
            # Studio never read would discard everything else the file contained.
            action = "merge_conflict"
        elif live is None:
            action = "merge_create"
        elif live_fragment.digest == payload_fragment.digest:
            action = "merge_identical"
        elif rcpt_digest is not None and live_fragment.digest == rcpt_digest:
            # Ours and untouched by the user: replacing it with the new payload fragment is exactly
            # what an upgrade is for.
            action = "merge_update"
        elif handler.can_overwrite_user_change:
            # Append-only strategies cannot conflict: every line the user wrote survives.
            action = "merge_update"
        elif live_fragment.conflict or rcpt_digest is not None:
            # A managed key/block is present with content that is not ours, or a fragment Studio owns
            # was edited by hand. Either way it is the user's call, and there is no force path.
            action = "merge_conflict"
        else:
            action = "merge_update"

        blocking = action in BLOCKING_ACTIONS
        diff = None
        if action in ("merge_conflict", "merge_update"):
            diff = _unified_diff(
                f"{pf.path} [{handler.fragment_key}]",
                (live_fragment.text or "").encode("utf-8") if live is not None else b"",
                (payload_fragment.text or "").encode("utf-8"),
            )
            if live_fragment.conflict_detail:
                # Without this line an unparseable settings file produces a diff that looks like a
                # plain addition, and the user is never told why their file was refused.
                diff = f"# managed: {', '.join(live_fragment.conflict_detail)}\n" + (diff or "")
        return PreviewEntry(
            path=pf.path, ownership=pf.ownership, action=action, live_sha256=live_fragment.digest,
            payload_sha256=payload_fragment.digest, receipt_sha256=rcpt_digest, size=pf.size,
            fragment_key=handler.fragment_key, diff=diff, blocking=blocking,
        )

    def _retire_entries(self, root: Path, receipt: Receipt | None) -> list[PreviewEntry]:
        """Paths the old receipt owns that the new payload no longer ships.

        Retired only when the live bytes still match the receipt — that is the proof nobody else took
        the file over. Anything else is left on disk and merely reported (``retire_blocked``, not
        blocking): deleting a file a user has edited would be the one destructive act this installer
        must never perform, and leaving a stale framework file behind is harmless by comparison.
        """
        if receipt is None:
            return []
        current = {pf.path for pf in self._manifest.files}
        out: list[PreviewEntry] = []
        for rf in receipt.files:
            if rf.kind != "file" or rf.path in current or rf.ownership not in OWNED_KINDS:
                continue
            try:
                target = security.resolve_inside(root, rf.path)
            except StudioError:
                continue
            live = _read_bytes(target)
            if live is None:
                continue
            live_sha = security.sha256_bytes(live)
            action = "retire" if live_sha == rf.sha256 else "retire_blocked"
            out.append(
                PreviewEntry(
                    path=rf.path, ownership=rf.ownership, action=action, live_sha256=live_sha,
                    payload_sha256=None, receipt_sha256=rf.sha256, size=len(live),
                    fragment_key=None, diff=None, blocking=False,
                )
            )
        return out

    def _state_versions(self, root: Path) -> tuple[tuple[int, ...], tuple[str, ...]]:
        """Every ``State Version`` on disk, and the records whose state could not be read.

        Computed for **every** kind, not only upgrade/recovery: §1.14's version rules refuse a fresh
        install into a repository that already holds v7 intents for the same reason they refuse an
        upgrade — the engine that would be installed writes v8, and mixing the two is P-06's
        mixed-version install (review P03).
        """
        found: set[int] = set()
        unreadable: list[str] = []
        if not root.is_dir():
            return ((), ())
        spaces = self._reader.list_spaces(root) or [C.DEFAULT_SPACE]
        for space in spaces:
            for intent_dir in self._reader.list_intent_dirs(root, space):
                rel = f"{self._reader.record_rel(space, intent_dir)}/aidlc-state.md"
                try:
                    result = self._reader.read_state(root, space, intent_dir)
                except StudioError:
                    unreadable.append(rel)
                    continue
                if result is None:
                    unreadable.append(rel)
                    continue
                version = result[1].state_version
                if version is not None:
                    found.add(int(version))
        return (tuple(sorted(found)), tuple(sorted(unreadable)))

    # ---- receipt-driven removal ---------------------------------------- #

    def _uninstall_root(self, repo: "RepoRecord", receipt: Receipt) -> Path:
        # A full preflight also scans the receipt and workflows. Keep that in preview, not in the
        # per-file guard: canonical path + device/inode + current registration prove the same root.
        fresh = self._registry.get(repo.repo_id)
        root = security.validate_repo_root(repo.canonical_path)
        info = root.stat()
        if (
            fresh.canonical_path != repo.canonical_path
            or fresh.resolved_identity != repo.resolved_identity
            or not repo.resolved_identity
            or receipt.resolved_repo_identity != repo.resolved_identity
            or not info.st_ino or f"{info.st_dev}:{info.st_ino}" != repo.resolved_identity
            or str(root) != repo.canonical_path
        ):
            raise StudioError("install_conflict", "repository identity no longer matches the receipt",
                              details={"reason": "identity_changed"})
        return root

    @staticmethod
    def _uninstall_path(root: Path, rel: str) -> Path:
        if (
            not rel or Path(rel).is_absolute() or Path(rel).as_posix() != rel
            or any(part in {".", ".."} for part in rel.split("/"))
            or "\0" in rel or "\\" in rel
        ):
            raise StudioError("bad_path", "invalid uninstall path", details={"path": rel})
        target = root
        for part in rel.split("/"):
            target = target / part
            if target.is_symlink():
                raise StudioError("bad_path", "uninstall path is a symlink", details={"path": rel})
        return target

    @staticmethod
    def _uninstall_owned_path(rf: ReceiptFile) -> bool:
        # A receipt cannot expand this operation into workflow data, another harness or project code.
        if rf.kind == "file":
            return rf.ownership in OWNED_KINDS and rf.path.startswith(ENGINE_DIR + "/")
        return rf.kind == "fragment" and rf.ownership == "merge" and (
            rf.path in {"AGENTS.md", ".gitignore"} or rf.path.startswith(ENGINE_DIR + "/")
        )

    def _receipt_handler(self, rf: ReceiptFile) -> _MergeHandler:
        meta = rf.uninstall or {}
        spec = meta.get("spec")
        if not isinstance(spec, dict):
            # Legacy receipts retain the definition of JSON keys and fenced markers in fragment_key.
            token = (rf.fragment_key or "").partition(":")[2]
            if rf.path.endswith(".json") and token:
                spec = {"strategy": "json-managed-keys", "managedKeys": token.split(",")}
            elif rf.path == "AGENTS.md" and token:
                spec = {"strategy": "append-fenced-block",
                        "markerStart": f"<!-- {token}:start -->",
                        "markerEnd": f"<!-- {token}:end -->"}
            else:
                raise StudioError("install_conflict", "receipt lacks the fragment definition",
                                  details={"reason": "legacy_fragment_ownership_unknown"})
        desired = self._manifest.merge_targets.get(rf.path) or {}
        if (
            rf.path == ".kiro/settings/cli.json"
            and "managedMapEntries" in desired and "managedMapEntries" not in spec
        ):
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
        meta = rf.uninstall or {}
        # Adoption proves that bytes matched at install time, not that Studio introduced them.
        # Legacy receipts cannot distinguish user-supplied markers from Studio-created markers.
        if not meta and rf.adopted:
            return live, "legacy_fragment_ownership_unknown"
        # Legacy line receipts contain only a hash, not the line list. Never guess from today's bundle.
        if not meta and rf.fragment_key == f"{Path(rf.path).name}:lines":
            return live, "legacy_fragment_ownership_unknown"
        handler = self._receipt_handler(rf)
        live.decode("utf-8")  # a lossy decode must never authorize deletion
        if isinstance(handler, _MissingLines):
            lines = meta.get("all_lines") or []
            handler.payload_bytes = ("\n".join(lines) + "\n").encode("utf-8")
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
            entry_paths = []
            parents = list(meta.get("parents") or [])
            maps = handler.managed_map_entries
            if maps:
                obj = _strict_json_object(live)
                introduced = meta.get("map_entries")
                if introduced is None:
                    # _receipt_handler proved a legacy fragment before narrowing its whole-map claim.
                    introduced = {key: list(names) for key, names in maps.items() if key in keys}
                    parents.extend(
                        [key] if key in obj else key.split(".") for key in maps if key in keys
                    )
                if not isinstance(introduced, dict) or not set(introduced) <= set(maps):
                    raise StudioError("install_conflict", "uninstall model entries exceed ownership")
                for key, names in introduced.items():
                    if not isinstance(names, list) or not all(
                        isinstance(name, str) and name in maps[key] for name in names
                    ):
                        raise StudioError("install_conflict", "uninstall model entries exceed ownership")
                    parent = (key,) if key in obj else tuple(key.split("."))
                    entry_paths.extend((*parent, name) for name in names)
                keys = [key for key in keys if key not in maps]
            if not keys and not entry_paths:
                return live, "preexisting_fragment"
            after = _remove_json_keys(live, keys, parents, entry_paths=entry_paths)
            if meta.get("created") and _strict_json_object(after) == {}:
                after = None
        elif isinstance(handler, _FencedBlock):
            start, end = handler.marker_start.encode(), handler.marker_end.encode()
            if meta and not meta.get("block"):
                return live, "preexisting_fragment"
            if live.count(start) != 1 or live.count(end) != 1:
                if not meta and rf.adopted and not live.count(start) and not live.count(end):
                    return live, "preexisting_fragment"
                raise StudioError("install_conflict", "ambiguous or missing Studio block")
            first, last = live.index(start), live.index(end) + len(end)
            if last <= first:
                raise StudioError("install_conflict", "reversed Studio block markers")
            if live[last:last + 1] == b"\n":
                last += 1
            after = live[:first] + live[last:]
            if not after and meta.get("created"):
                after = None
        else:
            owned_lines = list(meta.get("lines") or [])
            if not owned_lines:
                return live, "preexisting_fragment"
            parts = live.splitlines(keepends=True)
            for line in owned_lines:
                exact = (line + "\n").encode("utf-8")
                if exact not in parts:
                    raise StudioError("install_conflict", "an installed line was edited")
                parts.remove(exact)  # one owned occurrence; later user duplicates remain
            after = b"".join(parts)
            if not after and meta.get("created"):
                after = None
        return after, None

    def _uninstall_entry(self, root: Path, rf: ReceiptFile) -> PreviewEntry:
        action, reason, live, after = "preserve", "protected_path", None, None
        if self._uninstall_owned_path(rf):
            try:
                target = self._uninstall_path(root, rf.path)
                live, unreadable = _live_bytes(target)
                if unreadable or (target.exists() and not security.is_regular_file(target)):
                    raise StudioError("install_conflict", "unreadable or non-regular owned path")
                if live is None:
                    action, reason = "already_absent", "already_absent"
                elif rf.kind == "file":
                    if _digest_of(live) != rf.sha256:
                        if rf.ownership == "framework-mutable":
                            action, reason = "preserve", "engine_mutable_changed"
                        else:
                            raise StudioError("install_conflict", "receipt-owned file was modified")
                    else:
                        action, reason = "remove", None
                else:
                    after, reason = self._remove_receipt_fragment(rf, live)
                    action = "preserve" if reason else "remove_fragment"
            except (StudioError, OSError, ValueError, UnicodeError) as exc:
                action = "uninstall_conflict"
                reason = exc.code if isinstance(exc, StudioError) else "unreadable_fragment"
        return PreviewEntry(
            path=rf.path, ownership=rf.ownership, action=action,
            live_sha256=_digest_of(live), payload_sha256=_digest_of(after),
            receipt_sha256=rf.sha256 if rf.kind == "file" else rf.canonical_digest,
            size=len(live) if live is not None else None, fragment_key=rf.fragment_key,
            diff=None, blocking=action == "uninstall_conflict", reason=reason,
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
        entries = tuple(self._uninstall_entry(root, rf) for rf in receipt.files)
        blockers = tuple(e for e in entries if e.blocking)
        return PreviewPlan(
            repo_id=repo.repo_id, kind="uninstall", engine_from=receipt.engine_version, engine_to="",
            studio_version=C.APP_VERSION, payload_digest=receipt.payload_digest, entries=entries,
            counts={action: sum(e.action == action for e in entries) for action in UNINSTALL_ACTIONS},
            blocking=bool(blockers), blockers=blockers, warnings=(), newer_installed=False,
            same_version=False, requires_admin_lease=True, bytes_to_write=0,
            state_versions_found=(), state_version_blocked=False,
            preflight=self._registry.preflight(repo.canonical_path),
            receipt_id=receipt.receipt_id, repo_identity=repo.resolved_identity,
        )

    def _preflight_uninstall(self, repo: "RepoRecord", confirm_digest: str) -> PreviewPlan:
        plan = self._preview_uninstall(repo)
        if plan.digest() != confirm_digest:
            raise StudioError("install_conflict", "uninstall preview changed",
                              details={"reason": "preview_changed", "plan_digest": plan.digest()})
        if plan.blocking:
            raise StudioError("install_conflict", "modified receipt content blocks uninstall",
                              details={"blockers": [e.to_json() for e in plan.blockers]})
        return plan

    # ---- one-hop engine rollback ---------------------------------------- #

    def _rollback_context(
        self, repo: "RepoRecord", target_receipt_id: str | None, *, recovery: bool = False
    ) -> tuple[Path, Receipt, Receipt]:
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
            or target.status != "superseded"
            or source is None or source["status"] != "committed"
            or source["repo_id"] != repo.repo_id or source.get("prior_receipt_id") != target.receipt_id
            or source.get("resolved_repo_identity") != repo.resolved_identity
            or len(current.by_path()) != len(current.files) or len(target.by_path()) != len(target.files)
        ):
            raise StudioError("install_conflict", "rollback receipt chain is inconsistent")
        old_version, current_version = version_tuple(target.engine_version), version_tuple(current.engine_version)
        if not old_version or not current_version or old_version >= current_version:
            raise StudioError("install_conflict", "the prior receipt is not an older engine",
                              details={"reason": "target_not_older"})
        return root, current, target

    def _rollback_fragment_bytes(
        self, current: ReceiptFile | None, target: ReceiptFile, live: bytes | None, saved: bytes
    ) -> bytes:
        handler = self._receipt_handler(target)
        saved.decode("utf-8")
        if isinstance(handler, _MissingLines):
            lines = (target.uninstall or {}).get("all_lines")
            if lines is None:
                raise StudioError("install_conflict", "old ignore-line definition is unavailable",
                                  details={"reason": "legacy_fragment_ownership_unknown"})
            handler.payload_bytes = ("\n".join(lines) + "\n").encode()
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
        if current is None:
            # A historical receipt does not confer authority over a newly user-created fragment.
            if live is not None and handler.live_fragment(live).digest not in {None, target.canonical_digest}:
                raise StudioError("install_conflict", "unowned content occupies an old merge target")
        else:
            current_handler = self._receipt_handler(current)
            if type(current_handler) is not type(handler):
                raise StudioError("install_conflict", "fragment strategy changed across versions")
            if isinstance(current_handler, _MissingLines):
                lines = (current.uninstall or {}).get("all_lines")
                if lines is None:
                    raise StudioError("install_conflict", "current ignore-line ownership is unknown")
                current_handler.payload_bytes = ("\n".join(lines) + "\n").encode()
            if current_handler.live_fragment(live).digest != current.canonical_digest:
                raise StudioError("install_conflict", "current managed fragment was modified")
            if isinstance(handler, _FencedBlock):
                if current_handler.fragment_key != handler.fragment_key or (
                    live is not None and (
                        live.count(handler.marker_start.encode()) != 1
                        or live.count(handler.marker_end.encode()) != 1
                    )
                ):
                    raise StudioError("install_conflict", "ambiguous rollback block markers")
            elif isinstance(handler, _JsonManagedKeys):
                obj = _strict_json_object(live)
                saved_obj = _strict_json_object(saved)
                old_keys = set(current_handler.managed_keys)
                for key in set(handler.managed_keys) - old_keys:
                    exists, value = _resolve_key(obj, key)
                    if exists and (exists, value) != _resolve_key(saved_obj, key):
                        raise StudioError("install_conflict", "user key occupies an old managed key")
                # Owning the same parent key does not confer ownership of every model within it.
                # A target-only model may be restored only when absent or equal to the verified backup.
                for key, names in handler.managed_map_entries.items():
                    exists, models = _resolve_key(obj, key)
                    if not exists:
                        continue
                    _, saved_models = _resolve_key(saved_obj, key)
                    if not isinstance(models, dict) or not isinstance(saved_models, dict):
                        raise StudioError("install_conflict", "ambiguous rollback model defaults")
                    current_names = current_handler.managed_map_entries.get(key, ())
                    for name in set(names) - set(current_names):
                        if name in models and (
                            name not in saved_models
                            or _canonical_json_digest(models[name]) != _canonical_json_digest(saved_models[name])
                        ):
                            raise StudioError("install_conflict", "user model occupies an old managed entry",
                                              details={"model": name})
                removable = set((current.uninstall or {}).get("keys", [])) - set(handler.managed_keys)
                live = _remove_json_keys(live, sorted(removable), (current.uninstall or {}).get("parents", []))
            else:
                removable = set((current.uninstall or {}).get("lines", [])) - set(handler._payload_lines())
                parts = (live or b"").splitlines(keepends=True)
                for line in removable:
                    exact = (line + "\n").encode()
                    if exact not in parts:
                        raise StudioError("install_conflict", "installed ignore line was modified")
                    parts.remove(exact)
                live = b"".join(parts)
        return handler.merged_bytes(live, receipt_digest=current.canonical_digest if current else None)

    def _rollback_material(
        self, repo: "RepoRecord", target_receipt_id: str | None = None
    ) -> tuple[PreviewPlan, dict[str, bytes | None]]:
        root, current, target = self._rollback_context(repo, target_receipt_id)
        current_files, old_files = current.by_path(), target.by_path()
        entries, candidates = [], {}
        for rel in sorted(set(current_files) | set(old_files)):
            now, old = current_files.get(rel), old_files.get(rel)
            rf = old or now
            live = after = None
            reason = None
            action = "rollback_conflict"
            try:
                if not self._uninstall_owned_path(rf) or (now and not self._uninstall_owned_path(now)):
                    raise StudioError("install_conflict", "rollback cannot claim protected paths",
                                      details={"reason": "protected_path"})
                path = self._uninstall_path(root, rel)
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
                    source = self._uninstall_path(
                        self._failure_path(C.BACKUP_DIRNAME, current.transaction_id), rel
                    )
                    saved = _read_bytes(source)
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
            except (StudioError, OSError, ValueError, UnicodeError) as exc:
                reason = ((exc.details or {}).get("reason") or exc.code
                          if isinstance(exc, StudioError) else "unreadable_backup")
            entries.append(PreviewEntry(
                path=rel, ownership=rf.ownership, action=action, live_sha256=_digest_of(live),
                payload_sha256=_digest_of(after), receipt_sha256=rf.sha256 or rf.canonical_digest,
                size=len(after) if after is not None else 0, fragment_key=rf.fragment_key,
                diff=None, blocking=action == "rollback_conflict", reason=reason,
            ))
        versions = next((f.engine_state_versions for f in target.files if f.engine_state_versions), ())
        found, unreadable = self._state_versions(root)
        incompatible = bool(unreadable or any(v not in versions for v in found))
        if not versions or incompatible:
            entries.append(PreviewEntry(
                path=ENGINE_DIR, ownership="framework", action="rollback_conflict",
                live_sha256=None, payload_sha256=None, receipt_sha256=None, size=None,
                fragment_key=None, diff=None, blocking=True,
                reason="target_compatibility_unknown" if not versions else "state_version_unsupported",
            ))
        blockers = tuple(e for e in entries if e.blocking)
        plan = PreviewPlan(
            repo_id=repo.repo_id, kind="rollback", engine_from=current.engine_version,
            engine_to=target.engine_version, studio_version=C.APP_VERSION,
            payload_digest=target.payload_digest, entries=tuple(entries),
            counts={action: sum(e.action == action for e in entries) for action in ROLLBACK_ACTIONS},
            blocking=bool(blockers), blockers=blockers, warnings=(), newer_installed=False,
            same_version=False, requires_admin_lease=True,
            bytes_to_write=sum(e.size or 0 for e in entries if e.action == "restore_version"),
            state_versions_found=found, state_version_blocked=incompatible or not versions,
            state_versions_unreadable=unreadable, preflight=self._registry.preflight(repo.canonical_path),
            receipt_id=current.receipt_id, repo_identity=repo.resolved_identity,
            target_receipt_id=target.receipt_id, backup_transaction_id=current.transaction_id,
            rollback_receipt_digest=_canonical_json_digest([current.to_json(), target.to_json()]),
        )
        return plan, candidates

    def _preflight_rollback(
        self, repo: "RepoRecord", digest: str, target_receipt_id: str | None = None
    ) -> tuple[PreviewPlan, dict[str, bytes | None]]:
        plan, candidates = self._rollback_material(repo, target_receipt_id)
        if plan.digest() != digest:
            raise StudioError("install_conflict", "rollback preview changed",
                              details={"reason": "preview_changed", "plan_digest": plan.digest()})
        if plan.blocking:
            raise StudioError("install_conflict", "rollback is blocked",
                              details={"blockers": [e.to_json() for e in plan.blockers]})
        return plan, candidates

    def _receipt_recovery_row(self, repo: "RepoRecord", txid: str | None = None) -> dict | None:
        if txid is not None:
            row = self._storage.get("install_transactions", txid)
            if row is None or row["repo_id"] != repo.repo_id or row["kind"] not in {"uninstall", "rollback"}:
                raise StudioError("transaction_not_found", "no such receipt recovery transaction")
            return row
        if self._install_status(repo.repo_id) != "recovery_required":
            return None
        receipt = self.current_receipt(repo.repo_id)
        rows = self._storage.select("install_transactions", {"repo_id": repo.repo_id})
        candidates = [
            row for row in rows if row["kind"] in {"uninstall", "rollback"}
            and (row["status"] == "recovery_required" or (
                row["status"] == "staged" and any(
                    s.name == "confirm_recovery" for s in TransactionResult.from_row(row).steps
                )
            ))
            and (receipt is None or row.get("prior_receipt_id") == receipt.receipt_id)
        ]
        if len(candidates) > 1:
            raise StudioError("install_recovery_required", "multiple interrupted receipt transactions need review")
        return candidates[0] if candidates else None

    def _preview_receipt_recovery(
        self, repo: "RepoRecord", txid: str | None = None
    ) -> PreviewPlan | None:
        row = self._receipt_recovery_row(repo, txid)
        if row is None:
            return None
        txid, kind = row["transaction_id"], row["kind"]
        entries = []
        evidence_digest = None
        receipt = self.current_receipt(repo.repo_id)
        try:
            loader = self._load_uninstall_journal if kind == "uninstall" else self._load_rollback_journal
            snapshot = loader(repo, txid)
            filename = UNINSTALL_JOURNAL_FILENAME if kind == "uninstall" else ROLLBACK_JOURNAL_FILENAME
            evidence = self._failure_path(C.BACKUP_DIRNAME, txid, filename)
            evidence_digest = _digest_of(_read_bytes(evidence))
            root = self._uninstall_root(repo, receipt)
            backup = self._failure_path(C.BACKUP_DIRNAME, txid)
            files = {f["path"]: f for f in snapshot["files"]}
            if kind == "rollback":
                for entry in snapshot["plan"]["entries"]:
                    files.setdefault(entry["path"], {
                        "path": entry["path"], "before": entry["live_sha256"],
                        "after": entry["live_sha256"], "unchanged": True,
                    })
            for rel, f in sorted(files.items()):
                live, unreadable = _live_bytes(self._uninstall_path(root, rel))
                digest = _digest_of(live)
                reason, action, size = None, "identical", 0
                if unreadable or digest not in {f["before"], f["after"]}:
                    action, reason = "recovery_conflict", "user_changed_recovery_target"
                if not f.get("unchanged") and f["before"] is not None:
                    saved = _read_bytes(self._uninstall_path(backup, rel))
                    if saved is None or _digest_of(saved) != f["before"]:
                        action, reason = "recovery_conflict", "recovery_backup_invalid"
                    else:
                        size = len(saved)
                if action != "recovery_conflict" and digest != f["before"]:
                    action = "restore_backup" if f["before"] is not None else "remove_created"
                entries.append(PreviewEntry(
                    path=rel, ownership="framework", action=action, live_sha256=digest,
                    payload_sha256=f["before"], receipt_sha256=f["before"], size=size,
                    fragment_key=None, diff=None, blocking=action == "recovery_conflict", reason=reason,
                ))
        except (StudioError, OSError, ValueError, TypeError) as exc:
            entries = [PreviewEntry(
                path=ENGINE_DIR, ownership="framework", action="recovery_conflict",
                live_sha256=None, payload_sha256=None, receipt_sha256=None, size=None,
                fragment_key=None, diff=None, blocking=True, reason="recovery_evidence_invalid",
            )]
        blockers = tuple(e for e in entries if e.blocking)
        return PreviewPlan(
            repo_id=repo.repo_id, kind="recovery", engine_from=receipt.engine_version if receipt else None,
            engine_to=receipt.engine_version if receipt else str(row.get("engine_version") or ""),
            studio_version=C.APP_VERSION, payload_digest=receipt.payload_digest if receipt else "",
            entries=tuple(entries), counts={a: sum(e.action == a for e in entries) for a in (
                "restore_backup", "remove_created", "identical", "recovery_conflict"
            )}, blocking=bool(blockers), blockers=blockers, warnings=(), newer_installed=False,
            same_version=False, requires_admin_lease=True,
            bytes_to_write=sum(e.size or 0 for e in entries if e.action == "restore_backup"),
            state_versions_found=(), state_version_blocked=False,
            preflight=self._registry.preflight(repo.canonical_path),
            recovery_transaction_id=txid, recovery_kind=kind, recovery_evidence_digest=evidence_digest,
        )

    def _check_receipt_recovery(
        self, repo: "RepoRecord", digest: str, txid: str | None = None
    ) -> PreviewPlan:
        plan = self._preview_receipt_recovery(repo, txid)
        if plan is None or plan.digest() != digest:
            raise StudioError("install_conflict", "recovery preview changed",
                              details={"reason": "preview_changed"})
        if plan.blocking:
            raise StudioError("install_recovery_required", "recovery evidence or target needs repair",
                              details={"blockers": [e.to_json() for e in plan.blockers]})
        return plan

    def _reserve_receipt_recovery(self, repo: "RepoRecord", plan: PreviewPlan) -> TransactionResult:
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
                "plan_digest": plan.digest(), "recovery_kind": row["kind"], "prior_error": row.get("error"),
            }))
            self._storage.update("install_transactions", txid, {
                "status": "staged", "finished_at": None, "error": None,
                "steps_json": [s.to_json() for s in steps],
            })
            return self.transaction(txid)

    # ---- receipts and history ------------------------------------------- #

    def current_receipt(self, repo_id: str) -> Receipt | None:
        rows = self._storage.select(
            "install_receipts", {"repo_id": repo_id, "status": "current"}, limit=1
        )
        return Receipt.from_row(rows[0]) if rows else None

    def receipts(self, repo_id: str) -> list[Receipt]:
        rows = self._storage.select(
            "install_receipts", {"repo_id": repo_id}, order_by="committed_at DESC"
        )
        return [Receipt.from_row(row) for row in rows]

    def receipt(self, receipt_id: str) -> Receipt:
        row = self._storage.get("install_receipts", receipt_id)
        if row is None:
            raise StudioError("receipt_not_found", "no such receipt",
                              details={"receipt_id": receipt_id})
        return Receipt.from_row(row)

    def transaction(self, transaction_id: str) -> TransactionResult:
        row = self._storage.get("install_transactions", transaction_id)
        if row is None:
            raise StudioError("transaction_not_found", "no such transaction",
                              details={"transaction_id": transaction_id})
        return self._as_result(row)

    def transactions(self, repo_id: str, limit: int = 20) -> list[TransactionResult]:
        rows = self._storage.select(
            "install_transactions", {"repo_id": repo_id},
            # Two transactions on one repository cannot overlap (the admin lease), but a FakeClock or a
            # fast pair inside one second would otherwise order arbitrarily, and "the last 5" must mean
            # the last 5.
            order_by="started_at DESC, transaction_id DESC", limit=limit
        )
        return [self._as_result(row) for row in rows]

    def _as_result(self, row: Mapping[str, Any]) -> TransactionResult:
        """A transaction row plus the two facts that live elsewhere.

        ``receipt_id`` is looked up from ``install_receipts`` rather than stored on the transaction:
        schema v1 has no such column, and deriving it means a rolled-back transaction can never keep
        pointing at a receipt that no longer stands.
        """
        txid = str(row.get("transaction_id") or "")
        receipts = self._storage.select(
            "install_receipts", {"transaction_id": txid, "status": "current"}, limit=1
        )
        return TransactionResult.from_row(
            {**row, "receipt_id": receipts[0]["receipt_id"] if receipts else None},
            repo_install_status=self._install_status(str(row.get("repo_id") or "")),
        )

    def drift(self, repo: "RepoRecord", receipt: Receipt) -> tuple[PreviewEntry, ...]:
        """Receipt-owned paths whose live bytes no longer match what Studio recorded.

        Fragments are included, which ``RepoRegistry._drift_count`` deliberately leaves out: only this
        module knows the merge strategy that produced a canonical digest, so only here can a merge
        target's drift be told apart from a user editing their own half of the file.
        """
        root = Path(repo.canonical_path)
        out: list[PreviewEntry] = []
        for rf in receipt.files:
            try:
                target = security.resolve_inside(root, rf.path)
            except StudioError:
                out.append(self._drift_entry(rf, None, "conflict"))
                continue
            if rf.kind == "fragment":
                spec = (rf.uninstall or {}).get("spec") or {}
                handler = (
                    self._receipt_handler(rf) if "managedMapEntries" in spec
                    else self._handler(rf.path)
                )
                live = _read_bytes(target)
                digest = handler.live_fragment(live).digest
                if digest != rf.canonical_digest:
                    out.append(self._drift_entry(rf, digest, "merge_conflict"))
                continue
            live_sha = _digest_of(_read_bytes(target))
            if live_sha == rf.sha256:
                continue
            if live_sha is None:
                out.append(self._drift_entry(rf, None, "create"))
            elif rf.ownership == "framework-mutable":
                out.append(self._drift_entry(rf, live_sha, "engine_modified"))
            else:
                out.append(self._drift_entry(rf, live_sha, "owned_modified"))
        return tuple(out)

    def _drift_entry(self, rf: ReceiptFile, live_sha: str | None, action: str) -> PreviewEntry:
        expected = rf.sha256 if rf.kind == "file" else rf.canonical_digest
        return PreviewEntry(
            path=rf.path, ownership=rf.ownership, action=action, live_sha256=live_sha,
            payload_sha256=None, receipt_sha256=expected, size=None,
            fragment_key=rf.fragment_key, diff=None, blocking=action in BLOCKING_ACTIONS,
        )

    def _install_status(self, repo_id: str) -> str:
        row = self._storage.get("repos", repo_id)
        return str((row or {}).get("install_status") or "not_installed")

    # ---- transaction ---------------------------------------------------- #

    async def cancel(self, repo: "RepoRecord", *, transaction_id: str) -> TransactionResult:
        """Request cooperative cancellation; poll the same transaction for the rollback outcome.

        The request survives the HTTP task and does not cancel the worker or its subprocess. A worker
        finishes its current atomic operation, then restores and verifies its before-image. A committed
        or already settling transaction is returned unchanged: this API never pretends it was undone.
        """
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

    async def begin(
        self, repo: "RepoRecord", kind: str, *, plan_digest: str, target_receipt_id: str | None = None
    ) -> TransactionResult:
        """Run every refusal, then insert the transaction row. Additive to §1.14.

        Exists so `POST …/install` can answer 409 *before* it answers 202, and so the row the 202
        points at already exists when the UI polls it. ``run`` performs the same checks when it is
        called on its own, so nothing depends on this being used.
        """
        if kind == "recovery":
            recovery = await asyncio.to_thread(self._preview_receipt_recovery, repo)
            if recovery is not None:
                plan = await asyncio.to_thread(self._check_receipt_recovery, repo, plan_digest)
                await self._refuse_if_busy(repo)
                result = await asyncio.to_thread(self._reserve_receipt_recovery, repo, plan)
                await self._publish(result.transaction_id, repo.repo_id, result.status)
                return result
        if kind == "rollback":
            plan, _ = await asyncio.to_thread(self._preflight_rollback, repo, plan_digest, target_receipt_id)
            await self._refuse_if_busy(repo)
            txid = self._ids.new("tx")
            await asyncio.to_thread(self._insert_rollback, txid, repo, plan)
            await self._publish(txid, repo.repo_id, "staged")
            return await asyncio.to_thread(self.transaction, txid)
        if kind == "uninstall":
            plan = await asyncio.to_thread(self._preflight_uninstall, repo, plan_digest)
            await self._refuse_if_busy(repo)
            txid = self._ids.new("tx")
            await asyncio.to_thread(self._insert_uninstall, txid, repo, plan)
            await self._publish(txid, repo.repo_id, "staged")
            return await asyncio.to_thread(self.transaction, txid)
        await asyncio.to_thread(self._preflight_transaction, repo, kind, plan_digest)
        await self._refuse_if_busy(repo)
        txid = self._ids.new("tx")
        await asyncio.to_thread(self._insert_transaction, txid, repo, kind)
        await self._publish(txid, repo.repo_id, "staged")
        return await asyncio.to_thread(self.transaction, txid)

    async def _refuse_if_busy(self, repo: "RepoRecord") -> None:
        """Fail fast when the repository is already leased, so the route can answer 409 not 202.

        Advisory only — the authority is ``acquire_admin`` inside the transaction, which is the call
        that cannot race. Without this a submit-then-install pair would return 202 and then fail in the
        background, and the user would have to open the transaction to learn they were queued behind
        their own turn.
        """
        identity = repo.resolved_identity
        if not identity:
            return
        for kind in ("execution", "admin"):
            record = await asyncio.to_thread(self._storage.lease_get, kind, identity)
            if record is not None:
                raise LeaseHeld(
                    "repository is busy",
                    details={"kind": kind, "repo_id": repo.repo_id, "operation_type": record.operation_type},
                )

    async def run(
        self,
        repo: "RepoRecord",
        kind: str,
        *,
        plan_digest: str,
        transaction_id: str | None = None,
        target_receipt_id: str | None = None,
    ) -> TransactionResult:
        """Install, upgrade or recover, transactionally.

        The step order is the whole safety argument: stage and verify a private copy first (so a bad
        payload never reaches the repository), take the admin lease (so no turn is running), back up
        everything that will be replaced, write, merge, validate, and only then commit the receipt.
        Any failure from ``backup`` onwards rolls the repository back to the previous complete install
        and re-proves it against the old receipt.

        ``transaction_id`` is additive: a route that already answered 202 with an id passes it here so
        the background task continues *that* transaction instead of starting a second one. In that mode
        the refusals were already raised by ``begin``, so a change discovered now is *recorded* as a
        failed transaction rather than raised — there is no request left to answer.
        """
        if kind == "recovery":
            if transaction_id is None:
                recovery = await asyncio.to_thread(self._preview_receipt_recovery, repo)
                if recovery is not None:
                    pending = await self.begin(repo, kind, plan_digest=plan_digest)
                    return await self._run_receipt_recovery(repo, pending.transaction_id, plan_digest)
            else:
                row = await asyncio.to_thread(self._storage.get, "install_transactions", transaction_id)
                if row is not None and row["kind"] in {"uninstall", "rollback"}:
                    return await self._run_receipt_recovery(repo, transaction_id, plan_digest)
        if kind == "uninstall":
            return await self.uninstall(repo, confirm_digest=plan_digest, transaction_id=transaction_id)
        if kind == "rollback":
            return await self.rollback(
                repo, confirm_digest=plan_digest, target_receipt_id=target_receipt_id,
                transaction_id=transaction_id,
            )
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

        journal = _Journal(
            txid=transaction_id,
            staging_dir=self._data_dir / C.STAGING_DIRNAME / transaction_id,
            backup_dir=self._data_dir / C.BACKUP_DIRNAME / transaction_id,
        )
        steps: list[StepRecord] = []
        grant: "LeaseGrant | None" = None
        plan: PreviewPlan | None = None
        status = "staged"
        error: str | None = None
        failed_dir: str | None = None

        try:
            plan = await asyncio.to_thread(self._plan_for_run, repo, kind, plan_digest)
            await asyncio.to_thread(self._open_journal, journal, repo, transaction_id)

            await self._step(steps, transaction_id, repo, "stage_payload", self._stage_payload, journal)
            await self._step(steps, transaction_id, repo, "verify_staging", self._verify_staging, journal)

            grant = await self._lease_step(steps, transaction_id, repo, kind)
            journal.lease_generation = grant.generation

            if kind == "upgrade" and plan.engine_from == plan.engine_to:
                # Refresh authority was read before staging and acquiring the lease. Recheck the
                # current receipt, confirmed plan and actual repository identity before any writes.
                await asyncio.to_thread(self._registry.require_current_identity, repo)
                plan = await asyncio.to_thread(self._plan_for_run, repo, kind, plan_digest)

            await self._step(steps, transaction_id, repo, "backup", self._backup, journal, plan, repo)
            await self._step(steps, transaction_id, repo, "write_files", self._write_files, journal, plan, repo)
            await self._step(
                steps, transaction_id, repo, "merge_fragments", self._merge_fragments, journal, plan, repo
            )
            await self._validate_step(steps, transaction_id, repo, journal, plan, grant)
            await self._step(
                steps, transaction_id, repo, "commit_receipt", self._commit_receipt, journal, plan, repo
            )
            status = "committed"
        except Exception as exc:  # noqa: BLE001 - every failure mode has an outcome; see below
            # `Exception`, not `BaseException`: a cancellation at gateway shutdown cannot run a
            # rollback (every `await` in it would be cancelled again), and it does not need to — the
            # receipt is written last, so an interrupted transaction leaves the repository still
            # described by the OLD receipt. The next scan reports that as drift and the recovery route
            # repairs it, which is a strictly better outcome than a rollback that cannot complete.
            error = self._error_text(exc)
            last = steps[-1].name if steps else ""
            if last in ROLLBACK_TRIGGERING_STEPS:
                status, failed_dir = await self._rollback(steps, transaction_id, repo, journal, error)
            else:
                status = "failed"
                failed_dir = await asyncio.to_thread(self._archive_failure, journal, steps, error)
        finally:
            if grant is not None:
                await self._release_step(steps, transaction_id, repo, grant)

        if status == "committed":
            await asyncio.to_thread(self._cleanup_staging, journal)
        return await self._finish(transaction_id, repo, status, steps, journal, error, failed_dir)

    def _insert_uninstall(self, txid: str, repo: "RepoRecord", plan: PreviewPlan) -> None:
        self._insert_transaction(txid, repo, "uninstall")
        self._storage.update("install_transactions", txid, {
            "engine_version": plan.engine_from, "payload_digest": plan.payload_digest,
            "steps_json": [StepRecord(
                "confirm_uninstall", self._clock.iso(), self._clock.iso(), True,
                {"confirm_digest": plan.digest(), "receipt_id": plan.receipt_id,
                 "repo_identity": plan.repo_identity},
            ).to_json()],
        })

    def _insert_rollback(self, txid: str, repo: "RepoRecord", plan: PreviewPlan) -> None:
        self._insert_transaction(txid, repo, "rollback")
        self._storage.update("install_transactions", txid, {
            "engine_version": plan.engine_to, "payload_digest": plan.payload_digest,
            "steps_json": [StepRecord(
                "confirm_rollback", self._clock.iso(), self._clock.iso(), True,
                {"confirm_digest": plan.digest(), "receipt_id": plan.receipt_id,
                 "target_receipt_id": plan.target_receipt_id, "repo_identity": plan.repo_identity},
            ).to_json()],
        })

    def _rollback_transaction(self, repo: "RepoRecord", txid: str, digest: str) -> tuple[dict, dict]:
        self._failure_path(C.BACKUP_DIRNAME, txid)
        row = self._storage.get("install_transactions", txid)
        if row is None:
            raise StudioError("transaction_not_found", "no such rollback transaction")
        confirmation = next((s.detail for s in TransactionResult.from_row(row).steps
                             if s.name == "confirm_rollback"), {})
        if (
            row["kind"] != "rollback" or row["repo_id"] != repo.repo_id
            or row.get("resolved_repo_identity") != repo.resolved_identity
            or not digest or confirmation.get("confirm_digest") != digest
        ):
            raise StudioError("install_conflict", "rollback transaction or confirmation does not match")
        return row, confirmation

    async def rollback(
        self, repo: "RepoRecord", *, confirm_digest: str, target_receipt_id: str | None = None,
        transaction_id: str | None = None,
    ) -> TransactionResult:
        """Restore the immediate prior engine from verified backups, with a new receipt.

        Workflow files never enter this plan. A failed attempt restores the current engine from its
        own before-image; after process death, recover_rollback performs that same restoration.
        """
        if transaction_id is None:
            pending = await self.begin(
                repo, "rollback", plan_digest=confirm_digest, target_receipt_id=target_receipt_id,
            )
            transaction_id = pending.transaction_id
        row, confirmation = await asyncio.to_thread(
            self._rollback_transaction, repo, transaction_id, confirm_digest
        )
        target = confirmation["target_receipt_id"]
        if target_receipt_id is not None and target_receipt_id != target:
            raise StudioError("install_conflict", "rollback target does not match confirmation")
        if row["status"] in _SETTLED_STATUS:
            return await asyncio.to_thread(self.transaction, transaction_id)
        if row["status"] != "staged":
            raise StudioError("install_recovery_required", "an interrupted rollback needs recovery")
        if any(s.name == "confirm_recovery" for s in TransactionResult.from_row(row).steps):
            raise StudioError("install_recovery_required", "this transaction is reserved for restoration")
        await self._refuse_if_busy(repo)
        steps = list(TransactionResult.from_row(row).steps)
        journal = _Journal(
            transaction_id, self._failure_path(C.STAGING_DIRNAME, transaction_id),
            self._failure_path(C.BACKUP_DIRNAME, transaction_id),
        )
        grant = snapshot = None
        status, error, failed_dir = "failed", None, None
        try:
            grant = await self._uninstall_lease_step(steps, transaction_id, repo)
            if grant is None:
                return await asyncio.to_thread(self.transaction, transaction_id)
            plan, candidates = await asyncio.to_thread(self._preflight_rollback, repo, confirm_digest, target)
            await self._step(
                steps, transaction_id, repo, "backup", self._backup_rollback,
                journal, plan, candidates, repo, grant,
            )
            snapshot = await asyncio.to_thread(self._load_rollback_journal, repo, transaction_id)
            journal.backups = {f["path"]: f["before"] for f in snapshot["files"]}
            await self._step(
                steps, transaction_id, repo, "restore_version", self._apply_rollback, snapshot, repo, grant,
            )
            await self._step(
                steps, transaction_id, repo, "post_rollback_validate",
                self._validate_rollback, snapshot, repo, grant,
            )
            detail = await self._step(
                steps, transaction_id, repo, "commit_rollback",
                self._commit_rollback, snapshot, repo, steps, grant,
            )
            journal.receipt_id = detail["receipt_id"]
            status = "committed"
        except Exception as exc:
            current = await asyncio.to_thread(self._storage.get, "install_transactions", transaction_id)
            if current["status"] == "committed":
                status = "committed"
                steps = list(TransactionResult.from_row(current).steps)
            else:
                if grant is None and current["status"] != "staged":
                    raise
                if isinstance(exc, LeaseHeld):
                    held = await asyncio.to_thread(self._storage.lease_get, "admin", repo.resolved_identity)
                    if held is not None and held.transaction_id == transaction_id:
                        raise
                error = self._error_text(exc)
                if snapshot is not None:
                    status = await self._rollback_uninstall(snapshot, repo, steps, error, grant)
                failed_dir = await asyncio.to_thread(self._archive_failure, journal, steps, error)
        finally:
            if grant is not None:
                await self._release_step(steps, transaction_id, repo, grant)
        return await self._finish(transaction_id, repo, status, steps, journal, error, failed_dir)

    def _backup_rollback(
        self, journal: _Journal, plan: PreviewPlan, candidates: dict[str, bytes | None],
        repo: "RepoRecord", grant: "LeaseGrant",
    ) -> dict:
        receipt = self.receipt(plan.receipt_id)
        backup = self._failure_path(C.BACKUP_DIRNAME, journal.txid)
        if backup.exists():
            raise StudioError("install_conflict", "rollback backup already exists; recover it first")
        files = []
        for entry in plan.entries:
            if entry.action not in {"restore_version", "remove"}:
                continue
            self._uninstall_heartbeat(grant)
            root = self._uninstall_root(repo, receipt)
            path = self._uninstall_path(root, entry.path)
            before, unreadable = _live_bytes(path)
            if unreadable or _digest_of(before) != entry.live_sha256:
                raise StudioError("install_conflict", "rollback target changed before backup")
            if before is not None:
                self._uninstall_write(self._uninstall_path(backup, entry.path), before)
            after = candidates[entry.path]
            if _digest_of(after) != entry.payload_sha256:
                raise StudioError("install_conflict", "rollback candidate changed before backup")
            if after is not None:
                self._uninstall_write(self._uninstall_path(backup, "rollback-after/" + entry.path), after)
            files.append({
                "path": entry.path, "action": entry.action, "before": entry.live_sha256,
                "after": entry.payload_sha256,
                "mode": stat.S_IMODE(path.stat().st_mode) if before is not None else 0o644,
            })
        row = self._storage.get("repos", repo.repo_id)
        snapshot = {
            "version": 1, "transaction_id": journal.txid, "repo_id": repo.repo_id,
            "root": repo.canonical_path, "repo_identity": repo.resolved_identity,
            "receipt_id": plan.receipt_id, "target_receipt_id": plan.target_receipt_id,
            "confirm_digest": plan.digest(), "plan": plan._digest_data(), "files": files,
            "prior_repo": {key: row.get(key) for key in (
                "install_status", "installed_engine_version", "engine_dir", "receipt_version"
            )},
        }
        path = self._failure_path(C.BACKUP_DIRNAME, journal.txid, ROLLBACK_JOURNAL_FILENAME)
        self._uninstall_write(path, (json.dumps(snapshot, indent=2) + "\n").encode())
        return {"backed_up": len(files), "rollback_journal": str(path)}

    def _load_rollback_journal(self, repo: "RepoRecord", txid: str) -> dict:
        data = _read_bytes(self._failure_path(C.BACKUP_DIRNAME, txid, ROLLBACK_JOURNAL_FILENAME))
        try:
            snapshot = json.loads(data or b"")
            row, confirmation = self._rollback_transaction(repo, txid, snapshot["confirm_digest"])
            root, current, target = self._rollback_context(repo, snapshot["target_receipt_id"], recovery=True)
            if (
                snapshot["version"] != 1 or snapshot["transaction_id"] != txid
                or snapshot["repo_id"] != repo.repo_id or snapshot["root"] != str(root)
                or snapshot["repo_identity"] != repo.resolved_identity
                or snapshot["receipt_id"] != current.receipt_id
                or row["prior_receipt_id"] != current.receipt_id
                or confirmation["target_receipt_id"] != target.receipt_id
                or _canonical_json_digest(snapshot["plan"]) != snapshot["confirm_digest"]
                or snapshot["plan"]["rollback_receipt_digest"] !=
                    _canonical_json_digest([current.to_json(), target.to_json()])
                or set(snapshot["prior_repo"]) != {
                    "install_status", "installed_engine_version", "engine_dir", "receipt_version"
                }
                or snapshot["prior_repo"]["receipt_version"] != current.receipt_id
            ):
                raise ValueError("rollback journal does not match its confirmed receipt chain")
            expected = {
                e["path"]: (e["action"], e["live_sha256"], e["payload_sha256"])
                for e in snapshot["plan"]["entries"] if e["action"] in {"restore_version", "remove"}
            }
            actual = {f["path"]: (f["action"], f["before"], f["after"]) for f in snapshot["files"]}
            if actual != expected or len(actual) != len(snapshot["files"]):
                raise ValueError("rollback journal does not cover the confirmed plan")
            owned = {**current.by_path(), **target.by_path()}
            for f in snapshot["files"]:
                if (
                    f["path"] not in owned or not self._uninstall_owned_path(owned[f["path"]])
                    or any(v is not None and (not isinstance(v, str) or len(v) != 64)
                           for v in (f["before"], f["after"]))
                    or not isinstance(f["mode"], int) or not 0 <= f["mode"] <= 0o7777
                ):
                    raise ValueError("invalid rollback journal file")
                self._uninstall_path(root, f["path"])
            return snapshot
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            raise StudioError("install_recovery_required", "invalid rollback journal") from exc

    def _apply_rollback(self, snapshot: dict, repo: "RepoRecord", grant: "LeaseGrant") -> dict:
        receipt = self.receipt(snapshot["receipt_id"])
        for f in snapshot["files"]:
            self._fault(f"before:rollback_path:{f['path']}")
            self._uninstall_heartbeat(grant)
            root = self._uninstall_root(repo, receipt)
            path = self._uninstall_path(root, f["path"])
            live, unreadable = _live_bytes(path)
            if unreadable or _digest_of(live) != f["before"]:
                raise StudioError("install_conflict", "rollback target changed after backup")
            if f["after"] is None:
                path.unlink(missing_ok=True)
            else:
                source = self._uninstall_path(
                    self._failure_path(C.BACKUP_DIRNAME, snapshot["transaction_id"], "rollback-after"),
                    f["path"],
                )
                after = _read_bytes(source)
                if after is None or _digest_of(after) != f["after"]:
                    raise StudioError("install_recovery_required", "rollback candidate is missing or corrupt")
                self._uninstall_write(path, after, f["mode"])
            self._fault(f"after:rollback_path:{f['path']}")
        return {"restored": len(snapshot["files"])}

    def _validate_rollback(self, snapshot: dict, repo: "RepoRecord", grant: "LeaseGrant") -> dict:
        verified = self._validate_uninstall(snapshot, repo, grant)
        target = self.receipt(snapshot["target_receipt_id"])
        receipt = self.receipt(snapshot["receipt_id"])
        expected = {e["path"]: e["payload_sha256"] for e in snapshot["plan"]["entries"]}
        # Identical paths also go into the new receipt. A user edit there must not slip through just
        # because this transaction had no reason to write that path.
        for rf in target.files:
            self._uninstall_heartbeat(grant)
            root = self._uninstall_root(repo, receipt)
            path = self._uninstall_path(root, rf.path)
            live, unreadable = _live_bytes(path)
            if unreadable or _digest_of(live) != expected[rf.path]:
                raise StudioError("install_conflict", "old engine receipt would claim changed content",
                                  details={"path": rf.path})
        versions = next((f.engine_state_versions for f in target.files if f.engine_state_versions), ())
        found, unreadable = self._state_versions(Path(repo.canonical_path))
        if not versions or unreadable or any(v not in versions for v in found):
            raise StudioError("state_version_migration_unconfirmed", "old engine cannot write current state")
        result = self._engine.run_sync("utility.version", Path(repo.canonical_path), ENGINE_DIR)
        if result.exit_code != 0 or target.engine_version not in (result.stdout or ""):
            raise StudioError("install_conflict", "restored engine failed version validation")
        return {**verified, "engine_version": target.engine_version}

    def _commit_rollback(
        self, snapshot: dict, repo: "RepoRecord", steps: list[StepRecord], grant: "LeaseGrant"
    ) -> dict:
        self._validate_rollback(snapshot, repo, grant)
        self._load_rollback_journal(repo, snapshot["transaction_id"])
        target = self.receipt(snapshot["target_receipt_id"])
        receipt_id, now = self._ids.new("rc"), self._clock.iso()
        completed = list(steps)
        completed[-1] = replace(completed[-1], ok=True, finished_at=now, detail={
            "receipt_id": receipt_id, "target_receipt_id": target.receipt_id,
        })
        with self._storage._txn():
            self._uninstall_heartbeat(grant)
            current = self.current_receipt(repo.repo_id)
            if current is None or current.receipt_id != snapshot["receipt_id"]:
                raise StudioError("install_conflict", "current receipt changed during rollback")
            self._storage.insert("install_receipts", {
                "receipt_id": receipt_id, "repo_id": repo.repo_id,
                "resolved_repo_identity": repo.resolved_identity, "studio_version": C.APP_VERSION,
                "engine_version": target.engine_version, "payload_digest": target.payload_digest,
                "committed_at": now, "prior_receipt_id": current.receipt_id,
                "transaction_id": snapshot["transaction_id"],
                "files_json": [f.to_json() for f in target.files], "status": "current",
            })
            self._storage.update("install_receipts", current.receipt_id, {"status": "superseded"})
            self._storage.update("repos", repo.repo_id, {
                "install_status": "installed", "installed_engine_version": target.engine_version,
                "engine_dir": ENGINE_DIR, "receipt_version": receipt_id,
            })
            self._storage.update("install_transactions", snapshot["transaction_id"], {
                "status": "committed", "finished_at": now, "error": None,
                "steps_json": [s.to_json() for s in completed],
            })
        return {"receipt_id": receipt_id, "target_receipt_id": target.receipt_id}

    async def recover_rollback(
        self, repo: "RepoRecord", *, transaction_id: str, confirm_digest: str
    ) -> TransactionResult:
        """Undo an interrupted version rollback under a fresh admin lease, using its own journal."""
        return await self._recover_receipt_operation(
            repo, "rollback", transaction_id, confirm_digest,
        )

    def _uninstall_transaction(self, repo: "RepoRecord", txid: str, digest: str) -> dict:
        self._failure_path(C.BACKUP_DIRNAME, txid)
        row = self._storage.get("install_transactions", txid)
        if row is None:
            raise StudioError("transaction_not_found", "no such uninstall transaction")
        steps = TransactionResult.from_row(row).steps
        confirmation = next((s.detail for s in steps if s.name == "confirm_uninstall"), {})
        if (
            row.get("kind") != "uninstall" or row.get("repo_id") != repo.repo_id
            or row.get("resolved_repo_identity") != repo.resolved_identity
            or not digest or confirmation.get("confirm_digest") != digest
        ):
            raise StudioError("install_conflict", "uninstall transaction or confirmation does not match")
        return row

    async def _uninstall_lease_step(
        self, steps: list[StepRecord], txid: str, repo: "RepoRecord", *, recovery: bool = False
    ) -> "LeaseGrant | None":
        """Acquire, then recheck the reserved row before advancing it.

        A concurrent invocation may have completed while this caller was acquiring the lease.
        Such a caller returns the durable result instead of turning a committed row back into leased.
        """
        steps.append(StepRecord("acquire_admin_lease", self._clock.iso(), None, None, {}))
        index = len(steps) - 1
        self._fault("before:acquire_admin_lease")
        grant = await self._scheduler.acquire_admin(
            repo, operation_type=UNINSTALL_LEASE_OPERATION, transaction_id=txid
        )
        try:
            row = await asyncio.to_thread(self._storage.get, "install_transactions", txid)
            if row["status"] in _SETTLED_STATUS and not (
                recovery and row["status"] == "recovery_required"
            ):
                await self._scheduler.release(grant)
                return None
            if not recovery and row["status"] != "staged":
                raise StudioError("install_recovery_required", "uninstall already started")
            self._fault("after:acquire_admin_lease")
            steps[index] = replace(
                steps[index], finished_at=self._clock.iso(), ok=True,
                detail={"kind": grant.kind, "generation": grant.generation,
                        "operation_type": UNINSTALL_LEASE_OPERATION},
            )
            await self._advance(txid, repo, "acquire_admin_lease", steps)
        except BaseException:
            with contextlib.suppress(Exception):
                await self._scheduler.release(grant)
            raise
        return grant

    async def uninstall(
        self, repo: "RepoRecord", *, confirm_digest: str, transaction_id: str | None = None
    ) -> TransactionResult:
        """Remove only confirmed receipt-owned content; no engine, payload or workflow writes.

        `begin(repo, "uninstall", plan_digest=...)` may reserve the transaction for a 202 response.
        Retrying a terminal transaction id returns its durable outcome. Interrupted removals must
        use `recover_uninstall` to restore the saved before-image before another removal is attempted.
        """
        if transaction_id is None:
            reserved = await self.begin(repo, "uninstall", plan_digest=confirm_digest)
            transaction_id = reserved.transaction_id
        row = await asyncio.to_thread(
            self._uninstall_transaction, repo, transaction_id, confirm_digest
        )
        if row["status"] in _SETTLED_STATUS:
            return await asyncio.to_thread(self.transaction, transaction_id)
        if row["status"] != "staged":
            raise StudioError("install_recovery_required", "an interrupted uninstall needs recovery")
        if any(s.name == "confirm_recovery" for s in TransactionResult.from_row(row).steps):
            raise StudioError("install_recovery_required", "this transaction is reserved for restoration")
        await self._refuse_if_busy(repo)
        steps = list(TransactionResult.from_row(row).steps)
        journal = _Journal(
            transaction_id, self._failure_path(C.STAGING_DIRNAME, transaction_id),
            self._failure_path(C.BACKUP_DIRNAME, transaction_id),
        )
        grant = None
        snapshot = None
        status, error, failed_dir = "failed", None, None
        try:
            grant = await self._uninstall_lease_step(steps, transaction_id, repo)
            if grant is None:
                return await asyncio.to_thread(self.transaction, transaction_id)
            plan = await asyncio.to_thread(self._preflight_uninstall, repo, confirm_digest)
            await self._step(
                steps, transaction_id, repo, "backup", self._backup_uninstall, journal, plan, repo
            )
            snapshot = await asyncio.to_thread(self._load_uninstall_journal, repo, transaction_id)
            journal.backups = {f["path"]: f["before"] for f in snapshot["files"]}
            for name, action in (("remove_files", "remove"), ("remove_fragments", "remove_fragment")):
                await self._step(
                    steps, transaction_id, repo, name, self._apply_uninstall, snapshot, repo, action, grant
                )
            await self._step(
                steps, transaction_id, repo, "post_uninstall_validate",
                self._validate_uninstall, snapshot, repo, grant,
            )
            await self._step(
                steps, transaction_id, repo, "commit_uninstall",
                self._commit_uninstall, snapshot, repo, steps, grant,
            )
            status = "committed"
        except Exception as exc:  # every reversible failure has a durable outcome
            current = await asyncio.to_thread(self._storage.get, "install_transactions", transaction_id)
            if current["status"] == "committed":
                # The atomic receipt/repo/transaction commit is the point of no return.
                status = "committed"
                steps = list(TransactionResult.from_row(current).steps)
            else:
                if grant is None and current["status"] != "staged":
                    # This caller never claimed the row; another invocation owns its outcome.
                    raise
                error = self._error_text(exc)
                if isinstance(exc, LeaseHeld):
                    # Another invocation of this *same* reserved id may own the lease.
                    held = await asyncio.to_thread(self._storage.lease_get, "admin", repo.resolved_identity)
                    if held is not None and held.transaction_id == transaction_id:
                        raise
                if snapshot is not None:
                    status = await self._rollback_uninstall(snapshot, repo, steps, error, grant)
                failed_dir = await asyncio.to_thread(self._archive_failure, journal, steps, error)
        finally:
            if grant is not None:
                await self._release_step(steps, transaction_id, repo, grant)
        return await self._finish(transaction_id, repo, status, steps, journal, error, failed_dir)

    async def recover_uninstall(
        self, repo: "RepoRecord", *, transaction_id: str, confirm_digest: str
    ) -> TransactionResult:
        """Explicitly restore an interrupted uninstall using its original confirmation digest.

        Startup first calls settle_abandoned and the normal lease sweep. This method acquires a new
        admin lease; it never writes under a dead process's lease or overwrites a later user edit.
        """
        return await self._recover_receipt_operation(
            repo, "uninstall", transaction_id, confirm_digest,
        )

    async def _run_receipt_recovery(
        self, repo: "RepoRecord", txid: str, plan_digest: str
    ) -> TransactionResult:
        row = await asyncio.to_thread(self._receipt_recovery_row, repo, txid)
        steps = TransactionResult.from_row(row).steps
        confirmations = [s.detail for s in steps if s.name == "confirm_recovery"]
        if not confirmations:
            plan = await asyncio.to_thread(self._check_receipt_recovery, repo, plan_digest, txid)
            await self._refuse_if_busy(repo)
            await asyncio.to_thread(self._reserve_receipt_recovery, repo, plan)
        elif confirmations[-1].get("plan_digest") != plan_digest:
            raise StudioError("install_conflict", "recovery reservation does not match confirmation")
        original = next((s.detail.get("confirm_digest") for s in steps
                         if s.name == f"confirm_{row['kind']}"), None)
        try:
            return await self._recover_receipt_operation(
                repo, row["kind"], txid, original, recovery_digest=plan_digest,
            )
        except LeaseHeld:
            # A lease acquired after the advisory begin check must leave a retryable durable outcome.
            current = await asyncio.to_thread(self._storage.get, "install_transactions", txid)
            held = await asyncio.to_thread(self._storage.lease_get, "admin", repo.resolved_identity)
            if current["status"] == "staged" and not (held and held.transaction_id == txid):
                await asyncio.to_thread(self._storage.update, "install_transactions", txid, {
                    "status": "recovery_required", "error": "repo_busy", "finished_at": self._clock.iso(),
                })
                await self._publish(txid, repo.repo_id, "recovery_required")
                return await asyncio.to_thread(self.transaction, txid)
            raise

    async def _recover_receipt_operation(
        self, repo: "RepoRecord", kind: str, txid: str, confirm_digest: str,
        *, recovery_digest: str | None = None,
    ) -> TransactionResult:
        if kind == "uninstall":
            row = await asyncio.to_thread(self._uninstall_transaction, repo, txid, confirm_digest)
            loader = self._load_uninstall_journal
        else:
            row, _ = await asyncio.to_thread(self._rollback_transaction, repo, txid, confirm_digest)
            loader = self._load_rollback_journal
        if row["status"] in {"rolled_back", "failed"}:
            return await asyncio.to_thread(self.transaction, txid)
        if row["status"] == "committed":
            raise StudioError("install_conflict", "a committed operation is not a recovery candidate")
        await self._refuse_if_busy(repo)
        journal = _Journal(
            txid, self._failure_path(C.STAGING_DIRNAME, txid), self._failure_path(C.BACKUP_DIRNAME, txid),
        )
        steps = list(TransactionResult.from_row(row).steps)
        grant, error, status = None, row.get("error"), "recovery_required"
        failed_dir = row.get("failed_dir")
        try:
            snapshot = await asyncio.to_thread(loader, repo, txid)
            journal.backups = {f["path"]: f["before"] for f in snapshot["files"]}
            grant = await self._uninstall_lease_step(steps, txid, repo, recovery=True)
            if grant is None:
                return await asyncio.to_thread(self.transaction, txid)
            if recovery_digest is not None:
                await asyncio.to_thread(self._check_receipt_recovery, repo, recovery_digest, txid)
            status = await self._rollback_uninstall(snapshot, repo, steps, error, grant)
            failed_dir = await asyncio.to_thread(self._archive_failure, journal, steps, error)
        except Exception as exc:
            if grant is None:
                held = await asyncio.to_thread(self._storage.lease_get, "admin", repo.resolved_identity)
                if held is not None and held.transaction_id == txid:
                    raise
            error = self._error_text(exc)
            await asyncio.to_thread(
                self._storage.update, "repos", repo.repo_id, {"install_status": "recovery_required"}
            )
            failed_dir = await asyncio.to_thread(self._archive_failure, journal, steps, error)
        finally:
            if grant is not None:
                await self._release_step(steps, txid, repo, grant)
        return await self._finish(txid, repo, status, steps, journal, error, failed_dir)

    @staticmethod
    def _uninstall_write(target: Path, data: bytes, mode: int = 0o600) -> None:
        """Atomic file replacement without a predictable temporary path or permissive backups."""
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".uninstall-", delete=False) as f:
                temporary = Path(f.name)
                f.write(data)
                f.flush()
                os.fchmod(f.fileno(), mode)
                os.fsync(f.fileno())
            os.replace(temporary, target)
            fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _backup_uninstall(self, journal: _Journal, plan: PreviewPlan, repo: "RepoRecord") -> dict:
        receipt = self.receipt(plan.receipt_id)
        root = self._uninstall_root(repo, receipt)
        backup = self._failure_path(C.BACKUP_DIRNAME, journal.txid)
        journal_path = self._failure_path(C.BACKUP_DIRNAME, journal.txid, UNINSTALL_JOURNAL_FILENAME)
        if backup.exists():
            raise StudioError("install_conflict", "uninstall backup already exists; recover it first")
        files = []
        for entry in plan.entries:
            if entry.action not in {"remove", "remove_fragment"}:
                continue
            target = self._uninstall_path(root, entry.path)
            before = _read_bytes(target)
            if before is None or _digest_of(before) != entry.live_sha256:
                raise StudioError("install_conflict", "uninstall target changed before backup")
            copy = self._uninstall_path(backup, entry.path)
            self._uninstall_write(copy, before)
            files.append({
                "path": entry.path, "action": entry.action, "before": entry.live_sha256,
                "after": entry.payload_sha256, "mode": stat.S_IMODE(target.stat().st_mode),
            })
        row = self._storage.get("repos", repo.repo_id)
        snapshot = {
            "version": 1, "transaction_id": journal.txid, "repo_id": repo.repo_id,
            "repo_identity": repo.resolved_identity, "root": repo.canonical_path,
            "receipt_id": receipt.receipt_id, "confirm_digest": plan.digest(), "files": files,
            "plan": plan._digest_data(),
            "prior_repo": {key: row.get(key) for key in (
                "install_status", "installed_engine_version", "engine_dir", "receipt_version"
            )},
        }
        self._uninstall_write(journal_path, (json.dumps(snapshot, indent=2) + "\n").encode())
        return {"backed_up": len(files), "uninstall_journal": str(journal_path)}

    def _load_uninstall_journal(self, repo: "RepoRecord", txid: str) -> dict:
        path = self._failure_path(C.BACKUP_DIRNAME, txid, UNINSTALL_JOURNAL_FILENAME)
        data = _read_bytes(path)
        if data is None:
            raise StudioError("install_recovery_required", "uninstall journal is missing")
        try:
            snapshot = json.loads(data)
            row = self._uninstall_transaction(repo, txid, snapshot["confirm_digest"])
            receipt = self.current_receipt(repo.repo_id)
            if (
                snapshot["version"] != 1 or snapshot["transaction_id"] != txid
                or snapshot["repo_id"] != repo.repo_id
                or snapshot["repo_identity"] != repo.resolved_identity
                or snapshot["root"] != repo.canonical_path or receipt is None
                or snapshot["receipt_id"] != receipt.receipt_id
                or row["prior_receipt_id"] != receipt.receipt_id
                or _canonical_json_digest(snapshot["plan"]) != snapshot["confirm_digest"]
                or set(snapshot["prior_repo"]) != {
                    "install_status", "installed_engine_version", "engine_dir", "receipt_version"
                }
                or snapshot["prior_repo"]["receipt_version"] != receipt.receipt_id
            ):
                raise ValueError("uninstall journal does not match the current receipt")
            self._uninstall_root(repo, receipt)
            owned = receipt.by_path()
            expected = {
                e["path"]: (e["action"], e["live_sha256"], e["payload_sha256"])
                for e in snapshot["plan"]["entries"]
                if e["action"] in {"remove", "remove_fragment"}
            }
            actual = {f["path"]: (f["action"], f["before"], f["after"]) for f in snapshot["files"]}
            if actual != expected:
                raise ValueError("uninstall journal does not cover the confirmed plan")
            seen = set()
            for f in snapshot["files"]:
                rf = owned.get(f["path"])
                if (
                    rf is None or not self._uninstall_owned_path(rf) or f["path"] in seen
                    or f["action"] != ("remove" if rf.kind == "file" else "remove_fragment")
                    or not isinstance(f["before"], str) or len(f["before"]) != 64
                    or (f["after"] is not None and
                        (not isinstance(f["after"], str) or len(f["after"]) != 64))
                    or not isinstance(f["mode"], int) or not 0 <= f["mode"] <= 0o7777
                ):
                    raise ValueError("invalid uninstall journal file")
                self._uninstall_path(Path(repo.canonical_path), f["path"])
                seen.add(f["path"])
            return snapshot
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            raise StudioError("install_recovery_required", "invalid uninstall journal") from exc

    def _uninstall_heartbeat(self, grant: "LeaseGrant") -> None:
        self._storage.lease_heartbeat(
            grant.kind, grant.resolved_repo_identity, grant.generation, None
        )

    def _apply_uninstall(
        self, snapshot: dict, repo: "RepoRecord", action: str, grant: "LeaseGrant"
    ) -> dict:
        receipt = self.receipt(snapshot["receipt_id"])
        owned = receipt.by_path()
        root = self._uninstall_root(repo, receipt)
        removed = 0
        for f in snapshot["files"]:
            if f["action"] != action:
                continue
            self._fault(f"before:uninstall_path:{f['path']}")
            self._uninstall_heartbeat(grant)
            root = self._uninstall_root(repo, receipt)
            target = self._uninstall_path(root, f["path"])
            live = _read_bytes(target)
            if live is None or _digest_of(live) != f["before"]:
                raise StudioError("install_conflict", "uninstall target changed after backup",
                                  details={"path": f["path"]})
            after = None
            if action == "remove_fragment":
                after, reason = self._remove_receipt_fragment(owned[f["path"]], live)
                if reason:
                    raise StudioError("install_conflict", "fragment ownership changed after preview")
            if _digest_of(after) != f["after"]:
                raise StudioError("install_conflict", "uninstall result changed after preview")
            if after is None:
                target.unlink()
            else:
                self._uninstall_write(target, after, f["mode"])
            removed += 1
            self._fault(f"after:uninstall_path:{f['path']}")
        return {"changed": removed}

    def _validate_uninstall(
        self, snapshot: dict, repo: "RepoRecord", grant: "LeaseGrant"
    ) -> dict:
        self._uninstall_heartbeat(grant)
        root = self._uninstall_root(repo, self.receipt(snapshot["receipt_id"]))
        for f in snapshot["files"]:
            target = self._uninstall_path(root, f["path"])
            if (target.exists() and not security.is_regular_file(target)) or _digest_of(
                _read_bytes(target)
            ) != f["after"]:
                raise StudioError("install_conflict", "uninstall result failed verification",
                                  details={"path": f["path"]})
        return {"verified": len(snapshot["files"])}

    def _commit_uninstall(
        self, snapshot: dict, repo: "RepoRecord", steps: list[StepRecord], grant: "LeaseGrant"
    ) -> dict:
        self._validate_uninstall(snapshot, repo, grant)
        cleared = replace(repo, install_status="not_installed", receipt_version=None,
                          installed_engine_version=None, engine_dir=None)
        health = self._registry.detect_install(cleared, {})
        now = self._clock.iso()
        completed = list(steps)
        completed[-1] = replace(completed[-1], ok=True, finished_at=now,
                                detail={"removed_receipt_id": snapshot["receipt_id"]})
        # Storage's existing nested transaction support keeps the three rows indivisible; no schema
        # changes are needed. A future public Storage transaction context can replace this private seam.
        with self._storage._txn():
            self._uninstall_heartbeat(grant)
            current = self.current_receipt(repo.repo_id)
            if current is None or current.receipt_id != snapshot["receipt_id"]:
                raise StudioError("install_conflict", "current receipt changed during uninstall")
            self._storage.update("install_receipts", current.receipt_id, {"status": "uninstalled"})
            self._storage.update("repos", repo.repo_id, {
                "install_status": health.status, "installed_engine_version": health.engine_version,
                "engine_dir": health.engine_dir, "receipt_version": None,
            })
            self._storage.update("install_transactions", snapshot["transaction_id"], {
                "status": "committed", "finished_at": now, "error": None,
                "steps_json": [s.to_json() for s in completed],
            })
        return {"removed_receipt_id": snapshot["receipt_id"]}

    def _restore_uninstall(
        self, snapshot: dict, repo: "RepoRecord", grant: "LeaseGrant"
    ) -> dict:
        receipt = self.receipt(snapshot["receipt_id"])
        root = self._uninstall_root(repo, receipt)
        backup = self._failure_path(C.BACKUP_DIRNAME, snapshot["transaction_id"])
        restored = 0
        for f in snapshot["files"]:
            self._uninstall_heartbeat(grant)
            root = self._uninstall_root(repo, receipt)
            target = self._uninstall_path(root, f["path"])
            live, unreadable = _live_bytes(target)
            if unreadable or (target.exists() and not security.is_regular_file(target)):
                raise StudioError("install_recovery_required", "cannot inspect uninstall recovery target")
            digest = _digest_of(live)
            if digest == f["before"]:
                continue
            if digest != f["after"]:
                raise StudioError("install_recovery_required", "user changed an uninstall recovery target",
                                  details={"path": f["path"]})
            if f["before"] is None:
                target.unlink(missing_ok=True)
                restored += 1
                continue
            data = _read_bytes(self._uninstall_path(backup, f["path"]))
            if data is None or _digest_of(data) != f["before"]:
                raise StudioError("install_recovery_required", "uninstall backup is missing or corrupt")
            self._uninstall_write(target, data, f["mode"])
            restored += 1
        return {"restored": restored}

    def _verify_uninstall_restore(
        self, snapshot: dict, repo: "RepoRecord", grant: "LeaseGrant"
    ) -> dict:
        self._uninstall_heartbeat(grant)
        root = self._uninstall_root(repo, self.receipt(snapshot["receipt_id"]))
        for f in snapshot["files"]:
            if _digest_of(_read_bytes(self._uninstall_path(root, f["path"]))) != f["before"]:
                raise StudioError("install_recovery_required", "uninstall rollback verification failed")
        if "target_receipt_id" in snapshot:
            for entry in snapshot["plan"]["entries"]:
                path = self._uninstall_path(root, entry["path"])
                live, unreadable = _live_bytes(path)
                if unreadable or _digest_of(live) != entry["live_sha256"]:
                    raise StudioError("install_recovery_required", "prior engine changed during rollback",
                                      details={"path": entry["path"]})
        with self._storage._txn():
            current = self.current_receipt(repo.repo_id)
            if current is None or current.receipt_id != snapshot["receipt_id"]:
                raise StudioError("install_recovery_required", "uninstall rollback receipt changed")
            self._storage.update("repos", repo.repo_id, snapshot["prior_repo"])
        return {"verified": len(snapshot["files"])}

    async def _rollback_uninstall(
        self, snapshot: dict, repo: "RepoRecord", steps: list[StepRecord], error: str | None,
        grant: "LeaseGrant",
    ) -> str:
        txid = snapshot["transaction_id"]
        await asyncio.to_thread(
            self._storage.update, "install_transactions", txid, {"status": "rolling_back", "error": error}
        )
        await self._publish(txid, repo.repo_id, "rolling_back")
        try:
            for name, fn in (
                ("restore_backups", self._restore_uninstall),
                ("reverify_old_receipt", self._verify_uninstall_restore),
            ):
                await self._step(steps, txid, repo, name, fn, snapshot, repo, grant)
        except Exception as exc:
            steps[-1] = replace(steps[-1], ok=False, finished_at=self._clock.iso(),
                                detail={"error": self._error_text(exc)})
            await asyncio.to_thread(
                self._storage.update, "repos", repo.repo_id, {"install_status": "recovery_required"}
            )
            return "recovery_required"
        return "rolled_back"

    # -- transaction preconditions --------------------------------------- #

    async def settle_abandoned(self) -> list[str]:
        """Close out transactions whose process died, and return the ids settled.

        Called once from the ordered startup pass, **before** the lease sweep. Without it, a gateway that
        is force-quit or OOM-killed mid-install leaves two things behind: an ``install_transactions`` row
        in a non-terminal status, and the admin lease that row holds. ``leases._prove_admin`` will not
        reclaim a lease whose transaction has not settled, and nothing else settles it — so the
        repository could never run another turn, could never be installed into, and could not even be
        removed. The recovery operation FR-INST-013 promises was unreachable for exactly the failure it
        exists to repair.

        Which terminal status a row gets is decided by whether bytes can already have moved:

        * ``staged`` / ``leased`` — the payload was only staged under Studio's own data directory and
          the lease taken. The repository was never touched, so this is a plain ``failed``.
        * anything later (``backed_up`` through ``validated``, and ``rolling_back``) — files may be half
          replaced, so the honest status is ``recovery_required`` and the repository is marked the same
          way. That is what raises the blocking ``install_recovery_required`` finding and puts an
          ``install_conflict`` card in front of a person, instead of Studio quietly deciding for them.

        How far the dead process got is not written into the row: ``steps_json`` already records every
        step that ran and its outcome, and the ``install.transaction`` activity row names the status it
        was settled out of. Adding a column to repeat that would be a third copy to keep honest.

        A transaction whose admin lease this process holds is skipped: that one is genuinely running.
        The check is by ``boot_id`` rather than by "is startup finished", because the host makes routes
        live around the same time it calls ``on_startup`` and a request that squeezes in must not have
        its own live transaction declared abandoned.
        """
        rows = await asyncio.to_thread(self._storage.select, "install_transactions")
        unsettled = [
            row for row in rows if str(row.get("status") or "") not in _SETTLED_STATUS
        ]
        if not unsettled:
            return []

        leases = await asyncio.to_thread(self._storage.select, "admin_leases")
        mine = {
            str(row.get("transaction_id") or "")
            for row in leases
            if row.get("transaction_id") and str(row.get("boot_id") or "") == self._boot_id
        }
        mine.discard("")

        settled: list[str] = []
        now = self._clock.iso()
        for row in unsettled:
            txid = str(row.get("transaction_id") or "")
            if not txid or (self._boot_id and txid in mine):
                continue
            status = str(row.get("status") or "")
            wrote_nothing = status in _ABANDONED_WROTE_NOTHING
            if row.get("kind") in {"uninstall", "rollback"} and wrote_nothing:
                # Recovery can itself die after acquiring its lease. A durable removal journal
                # remains the authority that repository bytes may already have been removed.
                filename = (UNINSTALL_JOURNAL_FILENAME if row["kind"] == "uninstall"
                            else ROLLBACK_JOURNAL_FILENAME)
                path = self._failure_path(C.BACKUP_DIRNAME, txid, filename)
                wrote_nothing = not await asyncio.to_thread(path.exists)
            outcome = "failed" if wrote_nothing else "recovery_required"
            repo_id = str(row.get("repo_id") or "")
            # Reconstruct only paths owned by this transaction, never paths supplied by the row.
            # Archive before settling: if either operation is interrupted, startup can retry without
            # losing the candidate or pointing the recovery card at another transaction's evidence.
            journal = _Journal(
                txid=txid,
                staging_dir=self._data_dir / C.STAGING_DIRNAME / txid,
                backup_dir=self._data_dir / C.BACKUP_DIRNAME / txid,
            )
            steps = list(TransactionResult.from_row(row).steps)
            failed_dir = await asyncio.to_thread(
                self._archive_failure, journal, steps, _ABANDONED_ERROR
            )
            if not wrote_nothing and repo_id:
                # Leave the transaction retryable until the repository's recovery flag is durable.
                await asyncio.to_thread(
                    self._storage.update, "repos", repo_id, {"install_status": "recovery_required"}
                )
            await asyncio.to_thread(
                self._storage.update,
                "install_transactions",
                txid,
                {
                    "status": outcome,
                    "finished_at": now,
                    "error": _ABANDONED_ERROR,
                    "failed_dir": failed_dir,
                },
            )
            await self._activity.record(
                kind="install.transaction",
                severity="critical" if not wrote_nothing else "attention",
                repo_id=repo_id,
                params={
                    "transaction_id": txid,
                    "status": outcome,
                    "error": _ABANDONED_ERROR,
                    "abandoned_in_status": status,
                },
            )
            await self._publish(txid, repo_id, outcome)
            settled.append(txid)
        return settled

    def _preflight_transaction(self, repo: "RepoRecord", kind: str, plan_digest: str) -> PreviewPlan:
        """Every reason not to start, checked in the order that reveals the most useful one. Sync."""
        status = self.verify_payload()
        if not status.ok:
            raise StudioError(
                "payload_degraded", "the bundled payload does not match its manifest",
                details={"mismatches": list(status.mismatches[:10]), "missing": list(status.missing[:10])},
            )
        return self._plan_for_run(repo, kind, plan_digest)

    def _plan_for_run(self, repo: "RepoRecord", kind: str, plan_digest: str) -> PreviewPlan:
        plan = self.preview(repo, kind)
        if plan.digest() != plan_digest:
            raise StudioError(
                "install_conflict", "the repository changed since the plan was previewed",
                details={"reason": "preview_changed", "plan_digest": plan.digest()},
            )
        if plan.state_version_blocked:
            raise StudioError(
                "state_version_migration_unconfirmed",
                "this repository holds AI-DLC state this payload cannot write",
                details={
                    "state_versions_found": list(plan.state_versions_found),
                    "compatible": list(self._manifest.compatible_state_versions),
                    "unreadable": list(plan.state_versions_unreadable),
                },
            )
        if plan.newer_installed:
            raise StudioError(
                "newer_installed", "the installed engine is newer than the bundled payload",
                details={"installed": plan.engine_from, "bundled": plan.engine_to},
            )
        if kind == "upgrade" and plan.same_version:
            raise StudioError(
                "same_version_installed", "the bundled engine is already installed",
                details={"engine_version": plan.engine_to},
            )
        if plan.blocking:
            raise StudioError(
                "install_conflict", "files in this repository are not Studio's to replace",
                details={"blockers": [e.to_json() for e in plan.blockers[:20]]},
            )
        return plan

    def _insert_transaction(self, txid: str, repo: "RepoRecord", kind: str) -> None:
        prior = self.current_receipt(repo.repo_id)
        self._storage.insert(
            "install_transactions",
            {
                "transaction_id": txid,
                "repo_id": repo.repo_id,
                "resolved_repo_identity": repo.resolved_identity,
                "kind": kind,
                "status": "staged",
                "studio_version": C.APP_VERSION,
                "engine_version": self._manifest.engine_version,
                "payload_digest": self._manifest.payload_digest,
                "started_at": self._clock.iso(),
                "staging_dir": str(self._data_dir / C.STAGING_DIRNAME / txid),
                "backup_dir": str(self._data_dir / C.BACKUP_DIRNAME / txid),
                "prior_receipt_id": prior.receipt_id if prior else None,
                "steps_json": [],
            },
        )

    def _open_journal(self, journal: _Journal, repo: "RepoRecord", txid: str) -> None:
        """Record what "before" was, so a rollback can restore Studio's story as well as the files.

        The repo row's install fields cannot be recovered after ``commit_receipt`` has overwritten
        them, and the prior receipt id is the anchor the whole receipt chain hangs from. Both are read
        once, before anything moves. Sync.
        """
        row = self._storage.get("install_transactions", txid)
        if row is None:
            raise StudioError("transaction_not_found", "no such transaction",
                              details={"transaction_id": txid})
        journal.prior_receipt_id = row.get("prior_receipt_id")
        repo_row = self._storage.get("repos", repo.repo_id) or {}
        journal.prior_install_status = str(repo_row.get("install_status") or "not_installed")
        journal.prior_engine_version = repo_row.get("installed_engine_version")
        journal.prior_engine_dir = repo_row.get("engine_dir")
        journal.prior_receipt_version = repo_row.get("receipt_version")

    # -- step plumbing ---------------------------------------------------- #

    def _fault(self, marker: str) -> None:
        injector = self.fault_injector
        if injector is not None:
            injector(marker)

    async def _step(
        self,
        steps: list[StepRecord],
        txid: str,
        repo: "RepoRecord",
        name: str,
        fn: Callable[..., dict],
        *args: Any,
    ) -> dict:
        """Run one blocking step in a worker thread, with a fault point on both sides of it.

        The record is appended *before* the work so a crash leaves ``ok = None`` on the step that was
        in flight; that is what tells the rollback path (and a human reading the transaction) how far
        the repository got.
        """
        started = self._clock.iso()
        steps.append(StepRecord(name=name, started_at=started, finished_at=None, ok=None, detail={}))
        index = len(steps) - 1
        self._fault(f"before:{name}")
        if name in STEPS:
            await asyncio.to_thread(self._raise_if_cancelled, txid)
        detail = await asyncio.to_thread(fn, *args)
        self._fault(f"after:{name}")
        steps[index] = replace(steps[index], finished_at=self._clock.iso(), ok=True, detail=detail)
        await self._advance(txid, repo, name, steps)
        return detail

    async def _advance(self, txid: str, repo: "RepoRecord", name: str, steps: list[StepRecord]) -> None:
        status = _STATUS_AFTER.get(name)
        values: dict[str, Any] = {"steps_json": [s.to_json() for s in steps]}
        if status:
            values["status"] = status
        await asyncio.to_thread(self._advance_record, txid, name, values)
        if status:
            await self._publish(txid, repo.repo_id, status)

    def _advance_record(self, txid: str, name: str, values: dict) -> None:
        # Serialize acknowledgement with the commit boundary. A request accepted before committed
        # must be observed; a request arriving after committed must return that durable outcome.
        with self._storage._txn():
            if name in STEPS and name != "release_lease":
                self._raise_if_cancelled(txid)
            self._storage.update("install_transactions", txid, values)

    async def _publish(self, txid: str, repo_id: str, status: str) -> None:
        await self._events.publish(
            "transaction.updated", {"transaction_id": txid, "repo_id": repo_id, "status": status}
        )

    async def _lease_step(
        self, steps: list[StepRecord], txid: str, repo: "RepoRecord", kind: str
    ) -> "LeaseGrant":
        """Take the admin lease. ``repo_busy`` is a refusal, not a rollback: nothing was written yet."""
        started = self._clock.iso()
        steps.append(
            StepRecord(name="acquire_admin_lease", started_at=started, finished_at=None, ok=None, detail={})
        )
        index = len(steps) - 1
        self._fault("before:acquire_admin_lease")
        await asyncio.to_thread(self._raise_if_cancelled, txid)
        grant = await self._scheduler.acquire_admin(repo, operation_type=kind, transaction_id=txid)
        # From here the lease is held but `run` cannot see it yet: the caller's `grant` is only assigned
        # when this returns, so its `finally` has nothing to release. Anything that raises in the tail
        # below therefore has to hand the lease back itself, or the repository stays `repo_busy` until
        # the next startup sweep proves the holder gone.
        try:
            self._fault("after:acquire_admin_lease")
            steps[index] = replace(
                steps[index], finished_at=self._clock.iso(), ok=True,
                detail={"kind": grant.kind, "generation": grant.generation},
            )
            await self._advance(txid, repo, "acquire_admin_lease", steps)
        except BaseException:
            # Suppressed on purpose: the original failure is the one worth propagating, and a release
            # that fails here leaves exactly the state the startup sweep is built to clear.
            with contextlib.suppress(Exception):
                await self._scheduler.release(grant)
            raise
        return grant

    async def _validate_step(
        self,
        steps: list[StepRecord],
        txid: str,
        repo: "RepoRecord",
        journal: _Journal,
        plan: PreviewPlan,
        grant: "LeaseGrant",
    ) -> None:
        """Post-write validation. Separate from ``_step`` because it runs engine subprocesses."""
        name = "post_write_validate"
        steps.append(
            StepRecord(name=name, started_at=self._clock.iso(), finished_at=None, ok=None, detail={})
        )
        index = len(steps) - 1
        self._fault(f"before:{name}")
        await asyncio.to_thread(self._raise_if_cancelled, txid)
        detail = await asyncio.to_thread(self._post_write_validate, journal, plan, repo, grant)
        self._fault(f"after:{name}")
        steps[index] = replace(steps[index], finished_at=self._clock.iso(), ok=True, detail=detail)
        await self._advance(txid, repo, name, steps)

    async def _release_step(
        self, steps: list[StepRecord], txid: str, repo: "RepoRecord", grant: "LeaseGrant"
    ) -> None:
        """Give the lease back. A failure here never changes the transaction's outcome.

        The receipt is already committed (or already rolled back) by the time this runs; turning a
        bookkeeping error into a failed install would report a corruption that did not happen. The
        lease is reclaimable from its own settled transaction row (§1.10), so the worst case is one
        visible orphaned row.
        """
        started = self._clock.iso()
        steps.append(
            StepRecord(name="release_lease", started_at=started, finished_at=None, ok=None, detail={})
        )
        index = len(steps) - 1
        ok = True
        detail: dict[str, Any] = {"generation": grant.generation}
        try:
            self._fault("before:release_lease")
            await self._scheduler.release(grant)
            self._fault("after:release_lease")
        except Exception as exc:  # noqa: BLE001 - see docstring
            ok = False
            detail["error"] = self._error_text(exc)
        steps[index] = replace(steps[index], finished_at=self._clock.iso(), ok=ok, detail=detail)
        await asyncio.to_thread(
            self._storage.update, "install_transactions", txid,
            {"steps_json": [s.to_json() for s in steps]},
        )

    # -- the steps themselves (sync; each is one to_thread) --------------- #

    def _stage_payload(self, journal: _Journal) -> dict:
        """Copy the payload into ``data_dir/staging/<txid>/``.

        A private copy is what makes "the payload changed under us" impossible: everything from here
        on reads the staged bytes, so an app update replacing ``payload/`` mid-transaction cannot mix
        two versions into one repository.
        """
        staging = journal.staging_dir
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        count = 0
        total = 0
        for pf in self._manifest.files:
            self._raise_if_cancelled(journal.txid)
            source = security.resolve_inside(self._payload_root, pf.path)
            data = _read_bytes(source)
            if data is None:
                raise StudioError("payload_degraded", "a payload file is missing",
                                  details={"path": pf.path})
            target = security.resolve_inside(staging, pf.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            count += 1
            total += len(data)
        return {"files": count, "bytes": total, "staging_dir": str(staging)}

    def _verify_staging(self, journal: _Journal) -> dict:
        """Re-hash the staged copy against the manifest. A mismatch stops before the repo is touched."""
        bad: list[str] = []
        for pf in self._manifest.files:
            data = _read_bytes(security.resolve_inside(journal.staging_dir, pf.path))
            if data is None or security.sha256_bytes(data) != pf.sha256:
                bad.append(pf.path)
        if bad:
            raise StudioError("payload_degraded", "the staged payload does not match its manifest",
                              details={"paths": bad[:10]})
        return {"verified": len(self._manifest.files)}

    def _backup(self, journal: _Journal, plan: PreviewPlan, repo: "RepoRecord") -> dict:
        """Copy everything that will be replaced, and remember everything that will be created.

        Both halves are needed and neither can be derived later: after the write, a file that Studio
        created looks exactly like one it replaced.
        """
        root = Path(repo.canonical_path)
        backup = journal.backup_dir
        if backup.exists():
            shutil.rmtree(backup)
        backup.mkdir(parents=True, exist_ok=True)
        receipt = self.current_receipt(repo.repo_id)
        touched: set[str] = {e.path for e in plan.entries if e.action in WRITING_ACTIONS}
        touched.update(e.path for e in plan.entries if e.action == "retire")
        if receipt is not None:
            touched.update(rf.path for rf in receipt.files)

        copied = 0
        for rel in sorted(touched):
            target = security.resolve_inside(root, rel)
            if (root / rel).is_symlink():
                raise StudioError("install_conflict", "a managed path is a symlink",
                                  details={"path": rel})
            data, unreadable = _live_bytes(target)
            if unreadable:
                # `journal.backups[rel] = None` means "there was nothing here", and that record is what
                # a rollback uses to decide the file is Studio's to delete. Refusing is the only honest
                # answer for a file that exists and could not be copied.
                raise StudioError(
                    "install_conflict",
                    "a managed path exists and cannot be read, so it cannot be backed up",
                    details={"path": rel, "reason": "unreadable"},
                )
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
        """Write the framework and shell files, then retire what the payload dropped.

        Only the four actions that mean "these bytes are ours" are written; every refusal decided in
        the preview stays a refusal here, re-checked against the live bytes rather than trusted, so a
        file that changed between preview and write is a failure instead of an overwrite.
        """
        root = Path(repo.canonical_path)
        written = 0
        for entry in plan.entries:
            if entry.action not in ("create", "owned_identical", "engine_modified", "shell_create"):
                continue
            self._raise_if_cancelled(journal.txid)
            rel = entry.path
            target = security.resolve_inside(root, rel)
            if (root / rel).is_symlink():
                raise StudioError("install_conflict", "a managed path is a symlink",
                                  details={"path": rel})
            live = _read_bytes(target)
            live_sha = _digest_of(live)
            if live_sha != entry.live_sha256:
                raise StudioError(
                    "install_conflict", "a managed file changed after the plan was made",
                    details={"path": rel, "expected": entry.live_sha256, "found": live_sha},
                )
            data = _read_bytes(security.resolve_inside(journal.staging_dir, rel))
            if data is None:
                raise StudioError("payload_degraded", "a staged payload file vanished",
                                  details={"path": rel})
            if live is None:
                journal.created.append(rel)
            _atomic_write(target, data)
            journal.written[rel] = security.sha256_bytes(data)
            written += 1

        retired = 0
        skipped = 0
        for entry in plan.entries:
            if entry.action != "retire":
                continue
            self._raise_if_cancelled(journal.txid)
            target = security.resolve_inside(root, entry.path)
            live = _read_bytes(target)
            if live is None:
                continue
            if security.sha256_bytes(live) != entry.receipt_sha256:
                # Somebody edited it between preview and now: leave it alone rather than delete work.
                # Recorded, because "we planned to remove this and did not" is the kind of silence that
                # turns into a support question a year later.
                skipped += 1
                continue
            target.unlink()
            journal.retired.append(entry.path)
            retired += 1
        return {"written": written, "retired": retired, "retire_skipped": skipped}

    def _merge_fragments(self, journal: _Journal, plan: PreviewPlan, repo: "RepoRecord") -> dict:
        """Rewrite each merge target from its pre-transaction bytes plus the payload fragment.

        The *backup* is the input, not the file on disk: if this step is retried, or if a previous step
        already touched the file, merging on top of a merged file could duplicate an appended block.
        Reading the byte-exact "before" every time makes the merge idempotent.
        """
        root = Path(repo.canonical_path)
        merged = 0
        for entry in plan.entries:
            if entry.action not in ("merge_create", "merge_update"):
                continue
            self._raise_if_cancelled(journal.txid)
            rel = entry.path
            target = security.resolve_inside(root, rel)
            if (root / rel).is_symlink():
                raise StudioError("install_conflict", "a managed path is a symlink",
                                  details={"path": rel})
            if rel in journal.backups:
                # The journal, not the disk, decides what "before" was: a merge target that the backup
                # step recorded as absent must be created, even if something appeared at that path in
                # the meantime — merging on top of it would be merging into a file nobody backed up.
                before = (
                    _read_bytes(security.resolve_inside(journal.backup_dir, rel))
                    if journal.backups[rel] is not None
                    else None
                )
            else:  # pragma: no cover - every merge target is backed up before this step
                before = _read_bytes(target)

            # The same re-check `_write_files` performs, and for the same reason: the plan was made
            # before the lease was taken, so a user (or their editor, or a `git checkout`) can have
            # changed this file in between. Merging writes `before + fragment`, so without this the
            # window's edit is silently discarded — and `before` comes from the BACKUP, which makes it
            # invisible to any later comparison against the plan.
            live, live_unreadable = _live_bytes(target)
            if live_unreadable:
                raise StudioError(
                    "install_conflict", "a merge target exists and cannot be read",
                    details={"path": rel, "reason": "unreadable"},
                )
            live_sha = _digest_of(live)
            backed_up = journal.backups.get(rel) if rel in journal.backups else entry.live_sha256
            # `journal.written` covers a retry of this very step: those bytes are ours, already recorded,
            # and refusing them would make the retry fail on its own previous success.
            if live_sha not in (backed_up, journal.written.get(rel)):
                raise StudioError(
                    "install_conflict", "a merge target changed after the plan was made",
                    details={"path": rel, "expected": backed_up, "found": live_sha},
                )

            handler = self._handler(rel, source=journal.staging_dir)
            # The receipt digest goes in so `_FencedBlock` can tell a document Studio adopted verbatim
            # (replace it, once, with the fenced block) from one the user wrote (append to it).
            data = handler.merged_bytes(before, receipt_digest=entry.receipt_sha256)
            if before is None:
                journal.created.append(rel)
            _atomic_write(target, data)
            journal.written[rel] = security.sha256_bytes(data)
            merged += 1
        return {"merged": merged}

    def _post_write_validate(
        self, journal: _Journal, plan: PreviewPlan, repo: "RepoRecord", grant: "LeaseGrant"
    ) -> dict:
        """Prove the tree before the receipt claims it.

        Three checks with different failure modes: digests catch a partial write, the engine's own
        ``version`` catches an install that cannot run at all, and the stage-graph lookup is advisory
        (C15) because whether ``lookup`` needs a live state file is version-dependent — recording a
        non-zero exit is useful, failing the install on it is not.
        """
        root = Path(repo.canonical_path)
        bad: list[str] = []
        for entry in plan.entries:
            if entry.action in ("create", "owned_identical", "engine_modified", "shell_create"):
                data = _read_bytes(security.resolve_inside(root, entry.path))
                if data is None or security.sha256_bytes(data) != entry.payload_sha256:
                    bad.append(entry.path)
            elif entry.action in ("merge_create", "merge_update"):
                handler = self._handler(entry.path, source=journal.staging_dir)
                live = _read_bytes(security.resolve_inside(root, entry.path))
                if handler.live_fragment(live).digest != handler.payload_fragment().digest:
                    bad.append(entry.path)
        if bad:
            raise StudioError("install_conflict", "written files do not match the payload",
                              details={"paths": bad[:10]})

        version = self._engine.run_sync("utility.version", root, ENGINE_DIR)
        if self._manifest.engine_version not in (version.stdout or ""):
            raise StudioError(
                "install_conflict", "the installed engine does not report the bundled version",
                details={
                    "expected": self._manifest.engine_version,
                    "exit_code": version.exit_code,
                    "stdout": (version.stdout or "")[:200],
                },
            )
        smoke = self._engine.run_sync(
            "state.lookup", root, ENGINE_DIR, query=_SMOKE_LOOKUP_QUERY, arg=_SMOKE_LOOKUP_ARG
        )
        detail: dict[str, Any] = {
            "verified": len(plan.entries),
            "version_ok": True,
            "lookup_exit_code": smoke.exit_code,
        }
        if self._run_doctor():
            doctor = self._engine.run_sync(
                "utility.doctor", root, ENGINE_DIR, _confirmed=True,
                lease_generation=str(grant.generation),
            )
            detail["doctor_exit_code"] = doctor.exit_code
        return detail

    def _run_doctor(self) -> bool:
        settings = self._settings
        if settings is None:
            return False
        try:
            return bool(settings.installer().get("run_doctor_after_install"))
        except Exception:  # noqa: BLE001 - a settings read must never fail an install
            return False

    def _commit_receipt(self, journal: _Journal, plan: PreviewPlan, repo: "RepoRecord") -> dict:
        """Insert the receipt, supersede the prior one, and point the repo row at it. Last write."""
        receipt_id = self._ids.new("rc")
        files = self._receipt_files(plan, journal=journal, root=Path(repo.canonical_path))
        committed_at = self._clock.iso()
        self._storage.insert(
            "install_receipts",
            {
                "receipt_id": receipt_id,
                "repo_id": repo.repo_id,
                "resolved_repo_identity": repo.resolved_identity,
                "studio_version": C.APP_VERSION,
                "engine_version": self._manifest.engine_version,
                "payload_digest": self._manifest.payload_digest,
                "committed_at": committed_at,
                "prior_receipt_id": journal.prior_receipt_id,
                "transaction_id": journal.txid,
                "files_json": [f.to_json() for f in files],
                "status": "current",
            },
        )
        journal.receipt_id = receipt_id
        journal.receipt_inserted = True
        if journal.prior_receipt_id:
            self._storage.update(
                "install_receipts", journal.prior_receipt_id, {"status": "superseded"}
            )
        self._storage.update(
            "repos",
            repo.repo_id,
            {
                "install_status": "installed",
                "installed_engine_version": self._manifest.engine_version,
                "engine_dir": ENGINE_DIR,
                "receipt_version": receipt_id,
            },
        )
        return {"receipt_id": receipt_id, "files": len(files)}

    def _receipt_files(
        self, plan: PreviewPlan, *, journal: _Journal | None = None, root: Path | None = None
    ) -> list[ReceiptFile]:
        """What the new receipt owns.

        ``shell`` files are absent by design: ``aidlc/**`` is the user's and the engine's workspace, so
        Studio creates a missing seed and then makes no claim on it — otherwise every legitimate edit
        of ``memory/project.md`` would be reported as drift and every upgrade would offer to overwrite
        the team's own rules.
        """
        out: list[ReceiptFile] = []
        prior = self.current_receipt(plan.repo_id)
        previous = prior.by_path() if prior else {}
        for entry in plan.entries:
            if entry.ownership == "shell" or entry.action in ("retire", "retire_blocked"):
                continue
            if entry.ownership == "merge":
                out.append(
                    ReceiptFile(
                        path=entry.path, ownership=entry.ownership, kind="fragment", sha256=None,
                        canonical_digest=entry.payload_sha256, fragment_key=entry.fragment_key,
                        adopted=entry.action == "merge_identical",
                        uninstall=self._fragment_uninstall_metadata(
                            entry, journal, root, previous.get(entry.path)
                        ) if journal is not None and root is not None else None,
                    )
                )
                continue
            out.append(
                ReceiptFile(
                    path=entry.path, ownership=entry.ownership, kind="file",
                    sha256=entry.payload_sha256, canonical_digest=None, fragment_key=None,
                    adopted=entry.action == "identical",
                )
            )
        if out:
            out[0] = replace(out[0], engine_state_versions=self._manifest.compatible_state_versions)
        return out

    def _fragment_uninstall_metadata(
        self, entry: PreviewEntry, journal: _Journal, root: Path, prior: ReceiptFile | None
    ) -> dict:
        """Record what Studio added, so uninstall never removes a matching pre-existing user key."""
        spec = dict(self._manifest.merge_targets[entry.path])
        previous = (prior.uninstall if prior else None) or {}
        written = entry.action in {"merge_create", "merge_update"}
        before = (
            _read_bytes(security.resolve_inside(journal.backup_dir, entry.path))
            if written and journal.backups.get(entry.path) is not None
            else None
        )
        if not written:
            before = _read_bytes(security.resolve_inside(root, entry.path))
        after = _read_bytes(security.resolve_inside(root, entry.path))
        handler = self._handler(entry.path, source=journal.staging_dir)
        if (
            isinstance(handler, _JsonManagedKeys) and handler.managed_map_entries
            and handler.live_fragment(after).digest != entry.payload_sha256
        ):
            raise StudioError("install_conflict", "owned model preferences changed before receipt commit",
                              details={"path": entry.path})
        metadata: dict[str, Any] = {
            "spec": spec,
            "created": previous.get("created", written and before is None),
        }
        if isinstance(handler, _JsonManagedKeys):
            old = _strict_json_object(before) if before is not None else {}
            new = _strict_json_object(after) if after is not None else {}
            keys = [
                key for key in handler.managed_keys
                if key not in handler.managed_map_entries and (
                    key in previous.get("keys", [])
                    or (written and not _resolve_key(old, key)[0] and _resolve_key(new, key)[0])
                )
            ]
            parents = {tuple(p) for p in previous.get("parents", [])}
            introduced_maps = []
            if handler.managed_map_entries:
                introduced = {}
                for key, names in handler.managed_map_entries.items():
                    present, old_map = _resolve_key(old, key)
                    old_map = old_map if present else {}
                    legacy_added = (
                        key in previous.get("keys", []) and prior is not None
                        and prior.canonical_digest == handler.payload_fragment().digest
                        and "managedMapEntries" not in (previous.get("spec") or {})
                    )
                    introduced[key] = [
                        name for name in names
                        if name in previous.get("map_entries", {}).get(key, [])
                        or legacy_added or (written and name not in old_map)
                    ]
                    if legacy_added or (written and not present):
                        parents.add((key,) if key in new else tuple(key.split(".")))
                        introduced_maps.append(key)
                metadata["map_entries"] = introduced
            for key in [*keys, *introduced_maps]:
                if key in new:
                    continue
                parts = key.split(".")
                node = old
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
            metadata.update(
                all_lines=all_lines,
                lines=[line for line in all_lines if line in previous.get("lines", [])
                       or (written and line.strip() not in present)],
            )
        else:
            # Rewriting a pre-existing block does not grant Studio permission to remove it.
            # Legacy receipts lack introduction evidence; an upgrade cannot invent that history.
            metadata["block"] = bool(previous.get("block", written and prior is None))
        return metadata

    # -- rollback --------------------------------------------------------- #

    async def _rollback(
        self,
        steps: list[StepRecord],
        txid: str,
        repo: "RepoRecord",
        journal: _Journal,
        error: str,
    ) -> tuple[str, str | None]:
        """Undo everything this transaction did, then prove it. Returns ``(status, failed_dir)``.

        The proof is not optional. "We restored the backups" is a claim about a filesystem that just
        failed us once; re-hashing every path the old receipt owns is the only statement worth putting
        in front of a user, and when it cannot be made the repository is marked
        ``recovery_required`` rather than described as fine.
        """
        journal.cancelling = error == "cancelled"
        await asyncio.to_thread(
            self._storage.update, "install_transactions", txid,
            {"status": "rolling_back", "error": error, "steps_json": [s.to_json() for s in steps]},
        )
        await self._publish(txid, repo.repo_id, "rolling_back")
        # Retract Studio's own records first, before a single byte moves back. If the file restore
        # then fails, the repository is marked `recovery_required` while its *current* receipt still
        # describes the version that is (partly) on disk — a receipt claiming the new version over a
        # half-restored tree would be the mixed-version story the whole design refuses to tell.
        await asyncio.to_thread(self._retract_records, journal, repo)

        ok = True
        rollback_error: str | None = None
        handlers: dict[str, Callable[[_Journal, "RepoRecord"], dict]] = {
            "restore_backups": self._restore_backups,
            "delete_created": self._delete_created,
            "reverify_old_receipt": self._reverify_old_receipt,
        }
        for name in ROLLBACK_STEPS:
            fn = handlers[name]
            started = self._clock.iso()
            steps.append(StepRecord(name=name, started_at=started, finished_at=None, ok=None, detail={}))
            index = len(steps) - 1
            try:
                self._fault(f"before:{name}")
                detail = await asyncio.to_thread(fn, journal, repo)
                self._fault(f"after:{name}")
            except Exception as exc:  # noqa: BLE001 - a rollback failure is a reportable outcome
                ok = False
                rollback_error = self._error_text(exc)
                steps[index] = replace(
                    steps[index], finished_at=self._clock.iso(), ok=False,
                    detail={"error": rollback_error},
                )
                break
            steps[index] = replace(steps[index], finished_at=self._clock.iso(), ok=True, detail=detail)

        if ok:
            status = "rolled_back"
        else:
            status = "recovery_required"
            await asyncio.to_thread(
                self._storage.update, "repos", repo.repo_id, {"install_status": "recovery_required"}
            )
        failed_dir = await asyncio.to_thread(
            self._archive_failure, journal, steps, rollback_error or error
        )
        await self._activity.record(
            kind="install.transaction",
            severity="critical" if not ok else "attention",
            repo_id=repo.repo_id,
            params={
                "transaction_id": txid,
                "status": status,
                "error": error,
                "rollback_error": rollback_error,
            },
            evidence=(EvidenceRef("studio", f"transaction:{txid}"),),
        )
        return (status, failed_dir)

    def _restore_backups(self, journal: _Journal, repo: "RepoRecord") -> dict:
        """Put back the files this transaction actually wrote, and only those.

        The backup set is wider than the write set on purpose (it covers everything the plan *might*
        touch, plus the old receipt's files), but restoring all of it would overwrite files the
        transaction never modified. That matters because a transaction is not instantaneous: a user can
        edit one of those files while it runs, and reverting it to a copy taken before they typed would
        destroy their work in the name of undoing something Studio never did.

        ``written`` plus ``retired`` is a complete record of what changed: ``_atomic_write`` replaces a
        file in one step and the recording follows it immediately, and a retirement is a delete that is
        recorded the same way. A path in neither still holds the bytes it held before this transaction
        started, and has nothing to undo.
        """
        root = Path(repo.canonical_path)
        restored = 0
        changed = _changed_paths(journal)
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
                self._uninstall_write(self._uninstall_path(root, rel), data, journal.backup_modes[rel])
            else:
                _atomic_write(security.resolve_inside(root, rel), data)
            restored += 1
        return {"restored": restored}

    def _delete_created(self, journal: _Journal, repo: "RepoRecord") -> dict:
        """Remove files this transaction invented. Only paths with no backup are eligible."""
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
        root = security.validate_repo_root(repo.canonical_path)
        info = root.stat()
        if str(root) != repo.canonical_path or f"{info.st_dev}:{info.st_ino}" != repo.resolved_identity:
            raise StudioError("install_recovery_required", "cancelled repository identity changed")
        self._storage.lease_heartbeat("admin", repo.resolved_identity, journal.lease_generation, None)
        target = self._uninstall_path(root, rel)
        live, unreadable = _live_bytes(target)
        if unreadable or _digest_of(live) != journal.written.get(rel):
            raise StudioError("install_recovery_required", "user edited a cancellation rollback target",
                              details={"path": rel})

    def _retract_records(self, journal: _Journal, repo: "RepoRecord") -> None:
        """Put Studio's own rows back to the pre-transaction story. Sync, no filesystem.

        Not a rollback *step*: it has no filesystem failure mode worth reporting separately, and it must
        happen before the steps that do, so that no window exists in which a committed receipt describes
        a tree that is being taken apart.
        """
        if not (journal.receipt_inserted and journal.receipt_id):
            return
        self._storage.update("install_receipts", journal.receipt_id, {"status": "rolled_back"})
        if journal.prior_receipt_id:
            self._storage.update("install_receipts", journal.prior_receipt_id, {"status": "current"})
        self._storage.update(
            "repos",
            repo.repo_id,
            {
                "install_status": journal.prior_install_status,
                "installed_engine_version": journal.prior_engine_version,
                "engine_dir": journal.prior_engine_dir,
                "receipt_version": journal.prior_receipt_version,
            },
        )

    def _reverify_old_receipt(self, journal: _Journal, repo: "RepoRecord") -> dict:
        """Prove the repository is exactly the previous installation again.

        Three statements, all of which must hold: every path the standing receipt owns hashes to its
        recorded digest, every backed-up path is back to its backed-up bytes, and nothing this
        transaction created still exists. Anything less is not a rollback, it is a hope.

        Merge targets are proved by their **backup bytes**, never by re-deriving a fragment. The
        standing receipt's canonical digest was computed with the payload that wrote it, and this
        process is running a different one; re-deriving would compare two definitions of "the fragment"
        and could report a corruption that is really just a payload bump. Byte-equality with the
        pre-transaction copy is both stronger and version-free.
        """
        root = Path(repo.canonical_path)
        checked = 0
        fragments = 0
        receipt = self.current_receipt(repo.repo_id)
        if receipt is not None and receipt.receipt_id != journal.receipt_id:
            for rf in receipt.files:
                if rf.kind == "fragment":
                    fragments += 1
                    continue
                if rf.sha256 is None:
                    continue
                live = _read_bytes(security.resolve_inside(root, rf.path))
                if live is None or security.sha256_bytes(live) != rf.sha256:
                    raise StudioError(
                        "install_recovery_required", "a receipt-owned file did not come back",
                        details={"path": rf.path},
                    )
                checked += 1

        changed = _changed_paths(journal)
        for rel, digest in journal.backups.items():
            # Only what this transaction changed needs proving, for the same reason `_restore_backups`
            # only puts those back: a backed-up path it never touched still holds whatever it held, and
            # demanding byte-equality with a copy taken earlier would report a user's own edit made while
            # the install ran as a corrupted repository needing recovery.
            if digest is None or rel not in changed:
                continue
            live = _read_bytes(security.resolve_inside(root, rel))
            if live is None or security.sha256_bytes(live) != digest:
                raise StudioError("install_recovery_required", "a replaced file was not restored",
                                  details={"path": rel})
        for rel in journal.created:
            if journal.backups.get(rel) is None and security.is_regular_file(
                security.resolve_inside(root, rel)
            ):
                raise StudioError("install_recovery_required", "a created file was not removed",
                                  details={"path": rel})
        return {
            "receipt_paths_verified": checked,
            "fragments_proved_by_backup": fragments,
            "restored_verified": sum(
                1 for rel, digest in journal.backups.items()
                if digest is not None and rel in changed
            ),
        }

    # -- outcome ---------------------------------------------------------- #

    def _failure_path(self, *parts: str) -> Path:
        """An evidence path under the trusted data root, without symlink indirection.

        Even a link to another transaction inside data_dir is unsafe: evidence belongs to one txid.
        Symlinks *inside* a candidate are preserved by rename, never traversed during archival.
        """
        path = self._data_dir
        for part in parts:
            if not part or part in {".", ".."} or any(c in part for c in "/\\\0"):
                raise StudioError("bad_path", "invalid transaction evidence path")
            path = path / part
            if path.is_symlink():
                raise StudioError("bad_path", "transaction evidence path is a symlink")
        return path

    def _candidate_matches_payload(self, staging: Path) -> bool:
        """Prove the entire candidate is reproducible from this manifest before discarding it.

        The ordinary payload verifier permits extras and ignores non-regular files. Evidence needs
        a stricter test: any extra entry, symlink, unreadable file or incomplete walk means retain.
        """
        files = self._manifest.by_path()
        if (
            len(files) != self._manifest.file_count
            or len(files) != len(self._manifest.files)
            or self._payload_digest() != self._manifest.payload_digest
        ):
            return False
        directories = {
            parent.as_posix()
            for rel in files for parent in Path(rel).parents if parent != Path(".")
        }
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
                    if (
                        not stat.S_ISDIR(path.lstat().st_mode)
                        or path.relative_to(staging).as_posix() not in directories
                    ):
                        return False
                for name in names:
                    path = Path(base) / name
                    rel = path.relative_to(staging).as_posix()
                    pf = files.get(rel)
                    st = path.lstat()
                    if (
                        pf is None or not stat.S_ISREG(st.st_mode) or st.st_size != pf.size
                        or security.sha256_file(path) != pf.sha256
                    ):
                        return False
                    seen.add(rel)
        except OSError:
            return False
        return seen == set(files)

    def _archive_failure(self, journal: _Journal, steps: list[StepRecord], error: str | None) -> str:
        """Keep transaction-local evidence, omitting only a proven identical payload copy.

        Startup and the exception path share this operation. A restart after the move, log write or
        duplicate deletion can repeat it; existing evidence is never replaced by another candidate.
        """
        staging = self._failure_path(C.STAGING_DIRNAME, journal.txid)
        failed = self._failure_path(C.FAILED_DIRNAME, journal.txid)
        target = self._failure_path(C.FAILED_DIRNAME, journal.txid, FAILED_STAGING_DIRNAME)
        log_path = self._failure_path(C.FAILED_DIRNAME, journal.txid, FAILED_STEPS_FILENAME)
        existing: Any = {}
        if log_path.exists():
            raw = _read_bytes(log_path)
            try:
                existing = json.loads(raw) if raw is not None else None
            except (ValueError, UnicodeDecodeError):
                existing = None
            if not isinstance(existing, dict) or existing.get("transaction_id") != journal.txid:
                raise StudioError("bad_path", "existing transaction evidence is unreadable or unrelated")

        archives = []
        if failed.is_dir():
            for path in sorted(failed.iterdir()):
                if path.name == FAILED_STAGING_DIRNAME or path.name.startswith(
                    FAILED_STAGING_DIRNAME + "-"
                ):
                    self._failure_path(C.FAILED_DIRNAME, journal.txid, path.name)
                    archives.append(path.name)
        failed.mkdir(parents=True, exist_ok=True)
        row = self._storage.get("install_transactions", journal.txid) or {}
        payload_digest = row.get("payload_digest")
        discard = False
        candidate = existing.get("candidate") or {
            "status": "retained" if archives else "absent",
            "payload_digest": payload_digest,
        }
        if staging.exists():
            # A verify failure is informative even if a later read sees the expected bytes again.
            verify_failed = any(s.name == "verify_staging" and s.ok is not True for s in steps)
            discard = (
                not verify_failed
                and payload_digest == self._manifest.payload_digest
                and self._candidate_matches_payload(staging)
            )
            if discard:
                candidate = {
                    "status": "retained" if archives else "identical_to_payload",
                    "payload_digest": payload_digest,
                }
                if archives:
                    candidate["omitted_identical_staging"] = True
            else:
                suffix = 1
                while target.exists():
                    target = self._failure_path(
                        C.FAILED_DIRNAME, journal.txid, f"{FAILED_STAGING_DIRNAME}-{suffix}"
                    )
                    suffix += 1
                # Both directories belong to data_dir. Rename preserves special entries without
                # following links or silently copying a candidate across a filesystem boundary.
                staging.rename(target)
                archives.append(target.name)
                candidate = {"status": "retained", "payload_digest": payload_digest}
        if archives:
            candidate = {**candidate, "status": "retained", "paths": archives}
        record = {
            **existing,
            "transaction_id": journal.txid,
            "error": error,
            "steps": existing.get("steps") or [s.to_json() for s in steps],
            # An abandoned process cannot reconstruct its in-memory journal. Preserve any journal
            # already archived by that process instead of overwriting it with empty defaults.
            "backups": journal.backups or existing.get("backups", {}),
            "created": journal.created or existing.get("created", []),
            "retired": journal.retired or existing.get("retired", []),
            "candidate": candidate,
        }
        temporary: Path | None = None
        try:
            # Exclusive, unpredictable creation avoids following a planted fixed-name temp symlink.
            with tempfile.NamedTemporaryFile(
                dir=failed, prefix=".transaction-", suffix=".tmp", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write((json.dumps(record, indent=2) + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, log_path)
            fd = os.open(failed, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        if discard:
            # Do not delete the only candidate until its reproducibility is recorded durably.
            shutil.rmtree(staging)
        return str(failed)

    def _cleanup_staging(self, journal: _Journal) -> None:
        """Drop the staged copy after a commit; the backups stay as the rollback record."""
        if journal.staging_dir.is_dir():
            shutil.rmtree(journal.staging_dir, ignore_errors=True)

    async def _finish(
        self,
        txid: str,
        repo: "RepoRecord",
        status: str,
        steps: list[StepRecord],
        journal: _Journal,
        error: str | None,
        failed_dir: str | None,
    ) -> TransactionResult:
        values: dict[str, Any] = {
            "status": status,
            "finished_at": self._clock.iso(),
            "steps_json": [s.to_json() for s in steps],
            "error": error,
        }
        if failed_dir:
            values["failed_dir"] = failed_dir
        await asyncio.to_thread(self._storage.update, "install_transactions", txid, values)
        await self._publish(txid, repo.repo_id, status)
        if status == "committed":
            transaction_row = await asyncio.to_thread(self._storage.get, "install_transactions", txid)
            await self._activity.record(
                kind="install.transaction",
                severity="info",
                repo_id=repo.repo_id,
                params={
                    "transaction_id": txid,
                    "status": status,
                    "kind": transaction_row.get("kind"),
                    "engine_version": (
                        None if transaction_row.get("kind") == "uninstall"
                        else transaction_row.get("engine_version")
                    ),
                    "receipt_id": journal.receipt_id,
                },
                evidence=(EvidenceRef("studio", f"transaction:{txid}"),),
            )
        elif status == "failed":
            await self._activity.record(
                kind="install.transaction",
                severity="attention",
                repo_id=repo.repo_id,
                params={"transaction_id": txid, "status": status, "error": error},
                evidence=(EvidenceRef("studio", f"transaction:{txid}"),),
            )
        await self._events.publish("repo.updated", {"repo_id": repo.repo_id})
        return await asyncio.to_thread(self.transaction, txid)

    @staticmethod
    def _error_text(exc: BaseException) -> str:
        """A code, not a sentence, when there is one — the UI localises it (C11).

        ``LeaseHeld`` needs no special case: its code is already ``repo_busy``.
        """
        if isinstance(exc, StudioError):
            return exc.code
        if isinstance(exc, _InstallCancelled):
            return "cancelled"
        return f"{type(exc).__name__}: {exc}"[:400]
