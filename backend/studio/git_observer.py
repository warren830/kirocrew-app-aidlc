"""Read-only git: branch, dirty set, ahead/behind, commits, prior artifact versions, bounded diffs.

Every failure is data (``available=False`` and a reason): a missing, wedged or hostile git must never hide
the workflow state the user came to see (FR-GIT-005). What keeps a read a read:

1. **The verb is ``argv[0]`` and nothing may precede it** (``security.assert_git_argv_readonly``): git's
   global options are where ``-c``, ``-C``, ``--git-dir`` and ``--output=`` live, so the repository is
   selected by ``cwd`` alone (review P21).
2. **The repository's own ``.git/config`` is untrusted input**: its command-valued keys are pinned through
   ``GIT_CONFIG_COUNT`` (the one layer that outranks it), and ``--no-ext-diff --no-textconv`` keep a
   ``.gitattributes`` converter from running during a diff.
3. **``GIT_OPTIONAL_LOCKS=0``**, so ``status`` cannot refresh (write) ``.git/index``.
4. **Evidence is refused, never truncated**: an oversized prior version is ``None``. Only ``diff`` is cut,
   visibly.

Sync module: callers go through ``asyncio.to_thread``.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import constants as C
from . import security
from .errors import StudioError
from .wire import WireRecord

#: Why an observation is unavailable: a closed set of codes the UI translates, never free text (C11).
REASON_GIT_MISSING = "git_missing"
REASON_NOT_A_REPO = "not_a_repo"
REASON_TIMEOUT = "timeout"
REASON_ERROR = "error"

#: Config keys git runs as a program, pinned inert for every invocation. Verified against git 2.50:
#: ``core.fsmonitor=<script>`` in a repository's own config runs during ``status``, ``diff`` and ``ls-files``;
#: the others are inert today only because of an argv flag or verb choice here. Not coverable this way: a
#: ``filter.<name>.clean`` bound by ``.gitattributes``, which is also how Git-LFS works honestly.
_GIT_CONFIG_PINS = {"core.fsmonitor": "false", "core.alternateRefsCommand": "", "core.pager": "cat",
                    "diff.external": "", "uploadpack.packObjectsHook": ""}

#: Added to ``security.build_subprocess_env()``. The pins travel as ``GIT_CONFIG_KEY_n``/``VALUE_n`` because
#: ``-c`` is a forbidden token; a git older than 2.31 ignores them, so they can only remove capability.
_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_COUNT": str(len(_GIT_CONFIG_PINS)),
    **{f"GIT_CONFIG_KEY_{i}": key for i, key in enumerate(_GIT_CONFIG_PINS)},
    **{f"GIT_CONFIG_VALUE_{i}": value for i, value in enumerate(_GIT_CONFIG_PINS.values())},
}

_STATUS_ARGV = ("status", "--porcelain=v2", "--branch")
_COMMON_DIR_ARGV = ("rev-parse", "--git-common-dir")
_BRANCH_ARGV = ("rev-parse", "--abbrev-ref", "HEAD")
_AHEAD_BEHIND_ARGV = ("rev-list", "--left-right", "--count", "@{upstream}...HEAD")
_DIFF_ARGV = ("diff", "--no-color", "--no-ext-diff", "--no-textconv")
#: sha, committer date, subject — separated by US because a subject may contain anything else, tabs included.
_LOG_FORMAT = "%H%x1f%cI%x1f%s"
_FIELD_SEP = "\x1f"
#: Bounds ``recent_commits`` so a card render can never become a walk of the whole history.
MAX_LOG_LIMIT = 100
#: The one argv with no verb: ``--version`` reads no repository and no config, and ``/health`` reports the
#: binary Studio would use. Matched exactly and spawned without a ``cwd``.
_VERSION_ARGV = ("--version",)

_DETACHED = "(detached)"
_INITIAL = "(initial)"
_AB_RE = re.compile(r"^\+(\d+)\s+-(\d+)$")
#: Index of ``<path>`` in a porcelain-v2 entry by kind (``1`` ordinary, ``2`` renamed/copied, ``u`` unmerged).
_ENTRY_PATH_INDEX = {"1": 8, "2": 9, "u": 10}
#: Stable wording for many releases; a missed match only downgrades the reason to ``error``.
_NOT_A_REPO_RE = re.compile(
    r"not a git repository|not a work tree|does not appear to be a git repository", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class GitObservation(WireRecord):
    """A repository's git state at one moment. ``available=False`` carries a ``reason`` and nothing else;
    ``ahead``/``behind``/``upstream`` are ``None`` without an upstream, so "in sync" and "nothing to compare
    against" stay distinguishable."""

    available: bool
    reason: str | None
    branch: str | None
    detached: bool
    head: str | None
    head_subject: str | None
    dirty: bool
    dirty_files: int
    ahead: int | None
    behind: int | None
    upstream: str | None
    observed_at: str
    took_ms: int

    @classmethod
    def unavailable(cls, reason: str, *, observed_at: str, took_ms: int) -> "GitObservation":
        return cls(False, reason, None, False, None, None, False, 0, None, None, None, observed_at, took_ms)


@dataclass(frozen=True, slots=True)
class GitCommit(WireRecord):
    """One commit line; ``at`` is the §0.2 UTC form so it sorts against audit rows."""

    sha: str
    at: str
    subject: str


@dataclass(frozen=True, slots=True)
class _Run:
    """One spawn: raw stdout (so each caller applies its own cap) and redacted, capped stderr — a remote URL
    in an error message can carry a token. Stdout is repository content and is never redacted here."""

    exit_code: int | None
    stdout: bytes
    stderr: str
    timed_out: bool
    truncated: bool
    spawn_reason: str | None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")

    def lines(self) -> list[str]:
        """Non-blank stripped stdout lines of a successful run; none for a failed one."""
        return [line.strip() for line in self.text().splitlines() if line.strip()] if self.ok else []

    def failure_reason(self) -> str:
        if self.spawn_reason:
            return self.spawn_reason
        if self.timed_out:
            return REASON_TIMEOUT
        return REASON_NOT_A_REPO if _NOT_A_REPO_RE.search(self.stderr) else REASON_ERROR


def _unquote_path(value: str) -> str:
    """Undo git's C-style quoting (octal escapes are UTF-8 bytes). An undecodable path is returned as git
    printed it: a mangled name in a dirty list is still a signal."""
    if len(value) < 2 or not value.startswith('"') or not value.endswith('"'):
        return value
    try:
        return value[1:-1].encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return value


def _parse_status(text: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """``status --porcelain=v2 --branch`` → (headers, [(relpath, status)]).

    The status is the porcelain ``XY`` field, or ``?`` for an untracked file. Ignored entries (``!``) are
    dropped: counting build output as dirty would report uncommitted work that is not there.
    """
    headers: dict[str, str] = {}
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line:
            continue
        if line.startswith("# "):
            key, _, value = line[2:].partition(" ")
            headers[key] = value.strip()
            continue
        kind = line[0]
        if kind == "?":
            entries.append((_unquote_path(line[2:]), "?"))
            continue
        index = _ENTRY_PATH_INDEX.get(kind)
        fields = line.split(" ", index) if index is not None else []
        if index is None or len(fields) <= index:
            continue
        path = fields[index]
        if kind == "2":  # "<path>\t<origPath>": the new path is what changed
            path = path.split("\t", 1)[0]
        entries.append((_unquote_path(path), fields[1]))
    return headers, entries


def _iso_utc(raw: str) -> str:
    """git's offset-bearing ``%cI`` → the §0.2 ``…Z`` form. An unparseable value is passed through: a
    wrong-looking timestamp beats dropping the commit that carries it."""
    text = raw.strip()
    if not text:
        return text
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime(C.TIMESTAMP_FORMAT)


class GitObserver:
    """Every git invocation Studio makes, and the only place allowed to make one."""

    __slots__ = ("_git_path", "_clock")

    def __init__(self, git_path: str | None, clock: C.Clock) -> None:
        self._git_path = git_path or None
        self._clock = clock

    @staticmethod
    def check_argv(argv: Sequence[str]) -> tuple[str, ...]:
        """Prove an argv is a read, or raise ``internal_error``: §1.3's rules (``security``), plus the exact
        ``--version`` exemption that names no verb."""
        tokens = tuple(str(token) for token in argv)
        if tokens != _VERSION_ARGV:
            security.assert_git_argv_readonly(tokens)
        return tokens

    def _run(self, repo: Path | None, argv: Sequence[str], *, stdout_cap: int) -> _Run:
        """Spawn one read verb. Only a non-read argv (a bug here) and an inherited AI-DLC bypass variable (an
        operator misconfiguration) raise; everything git itself can do comes back as data."""
        tokens = self.check_argv(argv)
        if self._git_path is None:
            return _Run(None, b"", "git is not installed", False, False, REASON_GIT_MISSING)
        # Rebuilt per call, so a variable exported into the gateway after startup is still caught.
        env = {**security.build_subprocess_env(), **_GIT_ENV}
        try:
            proc = subprocess.run(  # noqa: S603 - argv list, never a shell; verb allowlisted above
                [self._git_path, *tokens],
                cwd=str(repo) if repo is not None else None,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=C.GIT_TIMEOUT_SECS,
                check=False,
            )
        except subprocess.TimeoutExpired:  # subprocess.run has already killed and reaped the child
            return _Run(None, b"", "git did not finish in time", True, False, None)
        except OSError as exc:
            # CPython names the cwd as `filename` when the child failed at chdir: a repository that cannot be
            # entered is an availability problem, not a missing tool.
            missing = isinstance(exc, FileNotFoundError) and exc.filename in (None, self._git_path)
            return _Run(None, b"", f"{type(exc).__name__}: {exc}", False, False,
                        REASON_GIT_MISSING if missing else REASON_ERROR)
        raw = proc.stdout or b""
        err = security.redact(security.truncate_output(proc.stderr or b"", C.MAX_SUBPROCESS_STDERR)).strip()
        return _Run(proc.returncode, raw[: stdout_cap + 1], err, False, len(raw) > stdout_cap, None)

    def _took(self, started: float) -> int:
        return max(0, int(round((self._clock.monotonic() - started) * 1000)))

    def version(self) -> str | None:
        """``git --version`` as printed, or ``None`` when git cannot be run."""
        lines = self._run(None, _VERSION_ARGV, stdout_cap=C.MAX_TEXT_PREVIEW_BYTES).lines()
        return lines[0] if lines else None

    def common_dir(self, repo: Path) -> str | None:
        """``rev-parse --git-common-dir`` as an absolute realpath: what identifies a repository across its
        worktrees, so two worktrees of one clone never register as two repositories (§1.5)."""
        lines = self._run(repo, _COMMON_DIR_ARGV, stdout_cap=C.MAX_TEXT_PREVIEW_BYTES).lines()
        if not lines:
            return None
        candidate = Path(lines[0])
        return str(security.realpath(candidate if candidate.is_absolute() else Path(repo) / candidate))

    def observe(self, repo: Path) -> GitObservation:
        """Branch, head, dirty count and upstream divergence, or the reason none of it is known.

        ``dirty_files`` is a floor: a tree with more changes than the output cap holds is still dirty, with the
        count of what fitted. Costs two spawns (``status`` and a one-commit ``log``).
        """
        started = self._clock.monotonic()
        at = self._clock.iso()
        try:
            return self._observe(repo, observed_at=at, started=started)
        except Exception:  # noqa: BLE001 - FR-GIT-005: an observation never raises at its caller
            return GitObservation.unavailable(REASON_ERROR, observed_at=at, took_ms=self._took(started))

    def _observe(self, repo: Path, *, observed_at: str, started: float) -> GitObservation:
        status = self._run(repo, _STATUS_ARGV, stdout_cap=C.MAX_SUBPROCESS_STDOUT)
        if not status.ok:
            return GitObservation.unavailable(status.failure_reason(), observed_at=observed_at,
                                              took_ms=self._took(started))
        headers, entries = _parse_status(status.text())
        branch, detached = self._branch(repo, headers)
        head = headers.get("branch.oid") or None
        if head == _INITIAL:  # no commit yet: real, readable, no head
            head = None
        upstream = headers.get("branch.upstream") or None
        ahead = behind = None
        if upstream:  # no upstream is not "in sync": the pair stays unknown
            match = _AB_RE.match(headers.get("branch.ab", ""))
            ahead, behind = (int(match.group(1)), int(match.group(2))) if match else self._ahead_behind(repo)
        recent = self._log(repo, (), 1)
        return GitObservation(
            available=True,
            reason=None,
            branch=branch,
            detached=detached,
            head=head or (recent[0].sha if recent else None) or None,
            head_subject=(recent[0].subject if recent else None) or None,
            dirty=bool(entries),
            dirty_files=len(entries),
            ahead=ahead,
            behind=behind,
            upstream=upstream,
            observed_at=observed_at,
            took_ms=self._took(started),
        )

    def _branch(self, repo: Path, headers: dict[str, str]) -> tuple[str | None, bool]:
        """(branch, detached) from the status header; ``rev-parse`` only for a git that printed no header,
        because a second process per observation would double the cost of a 64-repository scan."""
        raw = headers.get("branch.head")
        if raw == _DETACHED:
            return None, True
        if raw:
            return raw, False
        lines = self._run(repo, _BRANCH_ARGV, stdout_cap=C.MAX_TEXT_PREVIEW_BYTES).lines()
        if not lines:
            return None, False
        return (None, True) if lines[0] == "HEAD" else (lines[0], False)

    def _ahead_behind(self, repo: Path) -> tuple[int | None, int | None]:
        """``rev-list --left-right --count @{upstream}...HEAD``. The upstream is the left side, so git prints
        **behind first** (verified against git 2.50); the fields are swapped here because §1.16 pins the argv."""
        run = self._run(repo, _AHEAD_BEHIND_ARGV, stdout_cap=C.MAX_TEXT_PREVIEW_BYTES)
        fields = run.text().split() if run.ok else []
        if len(fields) != 2 or not all(value.isdigit() for value in fields):
            return None, None
        return int(fields[1]), int(fields[0])

    def prior_version(self, repo: Path, relpath: str) -> bytes | None:
        """The committed bytes of one artifact, or ``None`` when no comparison is possible: untracked, unborn
        HEAD, git failed, or larger than ``MAX_ARTIFACT_RENDER_BYTES`` — probed first, so an oversized blob is
        refused before it is in memory."""
        spec = f"HEAD:{self._safe_rel(repo, relpath, allow_tree=False)}"
        size = self._run(repo, ("cat-file", "-s", spec), stdout_cap=C.MAX_TEXT_PREVIEW_BYTES)
        text = size.text().strip() if size.ok else ""
        if text.isdigit() and int(text) > C.MAX_ARTIFACT_RENDER_BYTES:
            return None
        run = self._run(repo, ("show", "--no-textconv", spec), stdout_cap=C.MAX_ARTIFACT_RENDER_BYTES)
        return run.stdout if run.ok and not run.truncated else None

    def head_blob(self, repo: Path, relpath: str) -> tuple[str, bytes | None]:
        """One path's bytes at HEAD, for a caller that knows HEAD exists: ``("present", bytes)``, ``("absent",
        None)`` when HEAD has no such file, ``("too_large", None)`` over ``MAX_ARTIFACT_RENDER_BYTES`` (probed first,
        so the blob is never in memory), ``("unknown", None)`` when git could not read one it has."""
        spec = f"HEAD:{self._safe_rel(repo, relpath, allow_tree=False)}"
        size = self._run(repo, ("cat-file", "-s", spec), stdout_cap=C.MAX_TEXT_PREVIEW_BYTES)
        text = size.text().strip() if size.ok else ""
        if not text.isdigit():
            return "absent", None
        if int(text) > C.MAX_ARTIFACT_RENDER_BYTES:
            return "too_large", None
        run = self._run(repo, ("show", "--no-textconv", spec), stdout_cap=C.MAX_ARTIFACT_RENDER_BYTES)
        return ("present", run.stdout) if run.ok and not run.truncated else ("unknown", None)

    def diff(self, repo: Path, relpath: str) -> str | None:
        """Working-tree diff for one path, capped with a visible marker. ``None`` means no diff could be had
        (a degraded capability); ``""`` means no local change (information)."""
        rel = self._safe_rel(repo, relpath, allow_tree=True)
        run = self._run(repo, (*_DIFF_ARGV, "--", rel), stdout_cap=C.MAX_SUBPROCESS_STDOUT)
        return security.truncate_output(run.stdout, C.MAX_SUBPROCESS_STDOUT) if run.ok else None

    def recent_commits(self, repo: Path, paths: Sequence[str], limit: int = 10) -> list[GitCommit]:
        """Commits touching ``paths`` (all when empty), newest first, at most ``MAX_LOG_LIMIT``."""
        return self._log(repo, [self._safe_rel(repo, p, allow_tree=True) for p in paths if str(p).strip()], limit)

    def _log(self, repo: Path, rels: Sequence[str], limit: int) -> list[GitCommit]:
        bounded = max(1, min(int(limit), MAX_LOG_LIMIT))
        argv = ["log", "--no-decorate", f"--format={_LOG_FORMAT}", "-n", str(bounded)]
        if rels:
            # `--` first, or a path starting with a dash (or matching a ref name) reads as an option or revision.
            argv += ["--", *rels]
        run = self._run(repo, argv, stdout_cap=C.MAX_SUBPROCESS_STDOUT)
        out: list[GitCommit] = []
        for line in run.text().splitlines() if run.ok else ():
            fields = line.split(_FIELD_SEP)
            if not line.strip() or len(fields) < 3:
                continue
            out.append(GitCommit(fields[0].strip(), _iso_utc(fields[1]), _FIELD_SEP.join(fields[2:]).strip()))
            if len(out) >= bounded:
                break
        return out

    def dirty_split(self, repo: Path, owned_paths: Sequence[str]) -> dict[str, Any]:
        """The dirty set split into receipt-owned paths (listed, with their porcelain status) and the rest
        (counted): an upgrade is blocked by drift in owned files, never by unrelated work (FR-GIT-004).
        ``truncated`` means an empty ``owned_dirty`` proves nothing; the receipt digests are the real check."""
        owned = [self._safe_rel(repo, path, allow_tree=True) for path in owned_paths if str(path).strip()]
        run = self._run(repo, _STATUS_ARGV, stdout_cap=C.MAX_SUBPROCESS_STDOUT)
        if not run.ok:
            return {"owned_dirty": [], "unrelated_dirty": 0, "owned_changes": [], "owned_untracked_dirs": [],
                    "available": False, "reason": run.failure_reason(), "truncated": False}
        whole_tree = "." in owned  # a caller that names the repository root owns everything in it
        changes: list[dict[str, str]] = []
        install_dirs: list[str] = []
        unrelated = 0
        for path, status in _parse_status(run.text())[1]:
            if whole_tree or any(path == root or path.startswith(root + "/") for root in owned):
                changes.append({"relpath": path, "status": status})
            elif path.endswith("/") and any(root.startswith(path) for root in owned):
                # Git lists an untracked directory as one entry: a fresh install's `.kiro/` and `aidlc/` are
                # Studio's own output not committed yet, not the user's work.
                install_dirs.append(path)
            else:
                unrelated += 1
        changes.sort(key=lambda row: row["relpath"])
        return {
            "owned_dirty": [row["relpath"] for row in changes],
            "unrelated_dirty": unrelated,
            "owned_changes": changes,
            "owned_untracked_dirs": sorted(install_dirs),
            "available": True,
            "reason": None,
            "truncated": run.truncated,
        }

    @staticmethod
    def _safe_rel(repo: Path, relpath: str, *, allow_tree: bool) -> str:
        """A repo-relative posix path proven inside ``repo``. Checked although the paths come from Studio's own
        projection: an artifact id arrives in a URL, and ``git show HEAD:../../etc/passwd`` must not read."""
        resolved = security.resolve_inside(repo, relpath)
        root = security.realpath(repo)
        rel = "." if resolved == root else resolved.relative_to(root).as_posix()
        if rel == "." and not allow_tree:
            raise StudioError("bad_path", "a file path is required, not the repository root")
        return rel
