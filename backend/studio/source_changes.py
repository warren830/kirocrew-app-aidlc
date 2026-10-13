"""The source files a stage says it wrote, each compared with HEAD.

AI-DLC lists what a code stage wrote in its record's ``source-manifest.json`` (``writes[].path``, repository
relative). 2.11 writes one only for a Unit; zero-Unit code generation names its files as the ``OK`` targets of its
``traceability.json`` instead. A gate on that stage asks the human to approve code, so the card shows the change
itself beside the plan the reviewer judged. Read-only: the working tree goes through the guarded reader artifacts
use (inside the repository, regular files, never a secret file, capped) and HEAD through git, never a copy Studio
keeps.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from . import constants as C
from . import security
from .errors import StudioError

__all__ = ["intent_written_paths", "read_changes"]

#: Context lines around each hunk, as ``git diff`` prints by default.
_CONTEXT_LINES = 3


def read_changes(reader: Any, git: Any, repo: Path, manifest_relpath: str) -> dict[str, Any]:
    """``{"available", "reason", "files", "truncated", "manifest_readable"}`` for one listing: a source manifest,
    or a zero-Unit stage's ``traceability.json``. ``available`` is git's: without it nothing can be compared, so
    each listed path says ``unknown`` rather than "added"."""
    paths, readable = _listed_paths(reader, repo, manifest_relpath)
    head = git.observe(repo)
    available = bool(head.available)
    files: list[dict[str, Any]] = []
    budget = C.MAX_SOURCE_CHANGES_DIFF_BYTES
    for path in paths[:C.MAX_SOURCE_CHANGE_FILES]:
        change = _change(reader, git, repo, path, has_head=available and head.head is not None) if available \
            else _row(path, "unknown")
        diff = change.get("diff")
        if diff is not None and len(diff) > budget:  # the whole response stays bounded, not only each file
            change.update(diff=None, truncated=True)
        budget -= len(change.get("diff") or "")
        files.append(change)
    return {"available": available, "reason": None if available else head.reason, "files": files,
            "truncated": len(paths) > C.MAX_SOURCE_CHANGE_FILES, "manifest_readable": readable}


def intent_written_paths(reader: Any, repo: Path) -> dict[str, str]:
    """Each repository path an intent says it wrote, mapped to that intent's directory (the first listing naming
    it, in path order), so the Git panel can say a dirty file is an intent's code rather than the person's own
    unrelated work. The listings are every ``source-manifest.json``, and the ``traceability.json`` of zero-Unit code
    generation, which writes no manifest. Bounded by ``MAX_SOURCE_MANIFESTS``; every read goes through ``reader``."""
    spaces = repo / "aidlc" / "spaces"
    if not spaces.is_dir() or spaces.is_symlink():
        return {}
    found = sorted([*spaces.glob(f"*/intents/*/**/{C.SOURCE_MANIFEST_NAME}"),
                    *spaces.glob(f"*/intents/*/construction/{C.CODE_STAGE}/{C.TRACEABILITY_NAME}")])
    out: dict[str, str] = {}
    for listing in found[:C.MAX_SOURCE_MANIFESTS]:
        rel = listing.relative_to(repo).as_posix()
        parts = rel.split("/")  # aidlc/spaces/<space>/intents/<intent>/…/<listing>
        if len(parts) < 7:
            continue
        for path in _listed_paths(reader, repo, rel)[0]:
            out.setdefault(path, parts[4])
    return out


def _listed_paths(reader: Any, repo: Path, relpath: str) -> tuple[list[str], bool]:
    """``(paths, readable)`` from a source manifest's ``writes``, or a traceability file's ``OK`` targets."""
    data, readable = _json(reader, repo, relpath)
    if relpath.endswith(f"/{C.TRACEABILITY_NAME}"):
        return _traceability_paths(data) if readable else ([], False)
    return _manifest_paths(data) if readable else ([], False)


def _json(reader: Any, repo: Path, relpath: str) -> tuple[Any, bool]:
    try:
        snapshot = reader.read_artifact(repo, relpath)
    except StudioError:
        return None, False
    if snapshot is None:
        return None, False
    try:
        return json.loads(snapshot.bytes.decode("utf-8")), True
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, False


def _traceability_paths(data: Any) -> tuple[list[str], bool]:
    """Each workspace file an ``OK`` coverage row targets. A target under ``aidlc/`` is a record document, not
    code, and an absolute one is not a workspace path."""
    rows = data.get("coverage") if isinstance(data, dict) else None
    paths: list[str] = []
    for row in rows if isinstance(rows, list) else ():
        target = row.get("target") if isinstance(row, dict) else None
        if (isinstance(target, str) and row.get("status") == "OK" and target.strip()
                and not target.startswith(("aidlc/", "/")) and target not in paths):
            paths.append(target)
    return paths, isinstance(rows, list)


def _manifest_paths(data: Any) -> tuple[list[str], bool]:
    writes = data.get("writes") if isinstance(data, dict) else None
    paths: list[str] = []
    for entry in writes if isinstance(writes, list) else ():
        path = entry.get("path") if isinstance(entry, dict) else None
        if isinstance(path, str) and path.strip() and path not in paths:
            paths.append(path)
    return paths, isinstance(writes, list)


def _row(path: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"path": path, "status": status, "additions": 0, "deletions": 0, "diff": None, "truncated": False,
            **extra}


def _change(reader: Any, git: Any, repo: Path, path: str, *, has_head: bool) -> dict[str, Any]:
    """One written path: ``added``, ``modified``, ``deleted``, ``unchanged`` (HEAD already holds it), or why it is
    not shown (``outside``, ``secret``, ``too_large``, ``binary``, ``unknown``)."""
    try:
        target = security.resolve_inside(repo, path)
    except StudioError:
        return _row(path, "outside")
    if security.is_secret_file(target):
        return _row(path, "secret")
    try:
        current = reader.read_artifact(repo, path)
    except StudioError as exc:
        return _row(path, "too_large" if exc.code == "too_large" else "unknown")
    old_state, old = git.head_blob(repo, path) if has_head else ("absent", None)
    if old_state == "too_large":
        return _row(path, "too_large")
    if old_state == "unknown":
        return _row(path, "unknown")
    new = current.bytes if current is not None else None
    if old is None and new is None:
        return _row(path, "unknown")  # listed as written, yet neither on disk nor at HEAD
    if old is not None and new is not None and old == new:
        return _row(path, "unchanged")
    status = "added" if old is None else "deleted" if new is None else "modified"
    if not _is_text(old) or not _is_text(new):
        return _row(path, "binary", change=status)
    diff, truncated = _unified(path, old, new)
    additions = sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff.splitlines() if line.startswith("-") and not line.startswith("---"))
    return {"path": path, "status": status, "additions": additions, "deletions": deletions, "diff": diff,
            "truncated": truncated}


def _is_text(data: bytes | None) -> bool:
    if data is None:
        return True  # the absent side of an added or deleted file
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _unified(path: str, old: bytes | None, new: bytes | None) -> tuple[str, bool]:
    """``git diff``'s shape (``/dev/null`` for the missing side), capped per file, with secrets redacted."""
    lines = difflib.unified_diff(
        (old or b"").decode("utf-8").splitlines(keepends=True),
        (new or b"").decode("utf-8").splitlines(keepends=True),
        fromfile=f"a/{path}" if old is not None else "/dev/null",
        tofile=f"b/{path}" if new is not None else "/dev/null", n=_CONTEXT_LINES,
    )
    out: list[str] = []
    size = 0
    for line in lines:
        if not line.endswith("\n"):
            line += "\n\\ No newline at end of file\n"
        size += len(line)
        if size > C.MAX_SOURCE_CHANGE_DIFF_BYTES:
            return security.redact("".join(out)), True
        out.append(line)
    return security.redact("".join(out)), False
