"""Read-only git: branch, dirty set, ahead/behind, commits, prior artifact versions, bounded diffs.

Studio watches git and never touches it. Everything here is one of a dozen allowlisted read verbs, run
with a stripped environment inside the repository's own directory, and every failure is *data* — an
observation with ``available=False`` and a reason — rather than an exception, because a missing, wedged
or hostile git must never hide the AI-DLC workflow state the user came to see (FR-GIT-005).

Five decisions worth knowing, each for the failure it prevents:

1. **The verb is ``argv[0]`` and nothing may precede it.** Git's global options are the dangerous half
   of its command line: ``-c core.fsmonitor=<cmd>`` runs a binary of the caller's choosing, ``-C`` and
   ``--git-dir`` point a "read" at a different repository, ``--output=`` writes a file. So the check
   refuses any token before the verb and any forbidden option anywhere in the argv, and the repository
   is selected by ``cwd`` alone — never by ``-C`` (review P21).
2. **The observed repository's own configuration is untrusted input.** ``GIT_CONFIG_NOSYSTEM`` and
   ``GIT_CONFIG_GLOBAL`` drop the operator's system and global config but do nothing about the
   repository's ``.git/config``, which git reads on every one of these verbs; a registered repository
   that sets ``core.fsmonitor`` to a script of its own has git execute it during ``status``. So the
   command-valued keys are pinned by ``GIT_CONFIG_COUNT``/``GIT_CONFIG_KEY_n`` (see
   ``_GIT_CONFIG_PINS``), the one config layer that outranks a repository's own file, and
   ``--no-ext-diff --no-textconv`` keep a ``.gitattributes`` plus ``.git/config`` pair from executing a
   converter during what the user believes is a read.
3. **``GIT_OPTIONAL_LOCKS=0``** so ``status`` cannot refresh (write) ``.git/index``. Studio never writes
   inside a registered repository, and an index refresh would be exactly that.
4. **Refuse, never truncate, for anything used as evidence.** A prior artifact version larger than the
   render cap comes back as ``None`` — the UI then says the comparison is unavailable — instead of as a
   plausible half file that would make a diff view lie. Only the human-readable ``diff`` is truncated,
   and then visibly.
5. **Ahead/behind comes from ``status --porcelain=v2 --branch`` first.** Its ``# branch.ab`` header is
   the same number pair git's own prompt uses and costs no extra process; the ``rev-list`` fallback runs
   only when a repository has an upstream but git reported no counts (``status.aheadBehind=false``, or a
   git too old for the header).

Sync module: every method blocks on a subprocess and is called through ``asyncio.to_thread`` by its
caller (§0.6). An observation costs two spawns (``status`` plus a one-commit ``log`` for the head
subject), so a caller that walks many repositories should observe once per scan and cache the result.
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

# --------------------------------------------------------------------------- #
# observation reasons and fixed argv fragments
# --------------------------------------------------------------------------- #

#: Why an observation is unavailable. Deliberately a closed set of codes with no free-text detail: the
#: UI renders one localized sentence per reason, and a detail string would be untranslatable English
#: prose on the wire (C11).
REASON_GIT_MISSING = "git_missing"
REASON_NOT_A_REPO = "not_a_repo"
REASON_TIMEOUT = "timeout"
REASON_ERROR = "error"
OBSERVATION_REASONS = (REASON_GIT_MISSING, REASON_NOT_A_REPO, REASON_TIMEOUT, REASON_ERROR)

#: Config keys whose value git runs as a program, pinned to something inert for every invocation. A
#: registered repository can put any of them in its own ``.git/config``, which no ``GIT_CONFIG_NOSYSTEM``
#: or ``GIT_CONFIG_GLOBAL`` suppresses and no argv check can see: verified against git 2.50,
#: ``core.fsmonitor=<script>`` in a repository's own config makes ``status``, ``diff`` and ``ls-files``
#: execute that script, which would turn every read route into arbitrary code execution inside the
#: user's repository. ``core.pager`` needs a terminal (this module always pipes), ``diff.external`` and
#: ``core.alternateRefsCommand`` are held off by ``--no-ext-diff`` and by the verbs Studio does not run,
#: and ``uploadpack.packObjectsHook`` is a server-side key: they are pinned anyway, because the reason
#: each one does not fire today is an argv flag or a verb choice somewhere else in this file.
#:
#: Not covered, and not coverable this way: a ``filter.<name>.clean`` bound by ``.gitattributes`` is run
#: by ``status`` and ``diff`` when git has to hash a working-tree file, and ``<name>`` is chosen by the
#: repository, so no fixed key list can name it. Neutralising it would break every legitimate Git-LFS
#: repository, which is that same mechanism used honestly, so it stays a known exposure of registering a
#: repository rather than something this module can silently disarm.
_GIT_CONFIG_PINS = (
    ("core.fsmonitor", "false"),
    ("core.alternateRefsCommand", ""),
    ("core.pager", "cat"),
    ("diff.external", ""),
    ("uploadpack.packObjectsHook", ""),
)


def _config_pin_env(pins: Sequence[tuple[str, str]]) -> dict[str, str]:
    """``pins`` as git's ``GIT_CONFIG_COUNT``/``GIT_CONFIG_KEY_n``/``GIT_CONFIG_VALUE_n`` triplets.

    These carry command-line precedence, which is the point: they outrank the observed repository's own
    file. They are passed as environment rather than as ``-c`` because ``-c`` is on
    ``GIT_FORBIDDEN_TOKENS`` — an option before the verb is refused, and rightly so. A git older than
    2.31 ignores these variables, so the pins can only remove capability, never add a dependency.
    """
    env = {"GIT_CONFIG_COUNT": str(len(pins))}
    for index, (key, value) in enumerate(pins):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


#: Environment additions on top of ``security.build_subprocess_env()`` (§1.16). ``GIT_CONFIG_GLOBAL`` is
#: ``os.devnull`` rather than the literal ``/dev/null`` so the pin stays correct on any platform the
#: gateway is ever ported to; on darwin and linux the two are the same string.
_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    **_config_pin_env(_GIT_CONFIG_PINS),
}

_STATUS_ARGV = ("status", "--porcelain=v2", "--branch")
_COMMON_DIR_ARGV = ("rev-parse", "--git-common-dir")
_BRANCH_ARGV = ("rev-parse", "--abbrev-ref", "HEAD")
_AHEAD_BEHIND_ARGV = ("rev-list", "--left-right", "--count", "@{upstream}...HEAD")
_DIFF_ARGV = ("diff", "--no-color", "--no-ext-diff", "--no-textconv")
#: sha, committer date (strict ISO-8601), subject — separated by US (``%x1f``) because a subject may
#: contain anything else, including tabs.
_LOG_FORMAT = "%H%x1f%cI%x1f%s"
_FIELD_SEP = "\x1f"
#: An upper bound on ``recent_commits(limit=…)`` so a caller cannot turn a card render into a walk of
#: the whole history.
MAX_LOG_LIMIT = 100
#: The one argv whose first token is not a verb. Exactly this tuple, nothing else: ``git --version``
#: performs no repository discovery and reads no config, and ``/health`` has to report the version of
#: the binary Studio would use (§2.1) — without this the only alternative would be a second module
#: spawning git, which is worse.
_VERSION_ARGV = ("--version",)

_HEADER_PREFIX = "# "
_DETACHED = "(detached)"
_INITIAL = "(initial)"
_AB_RE = re.compile(r"^\+(\d+)\s+-(\d+)$")
#: Field index of ``<path>`` in a porcelain-v2 entry, per entry kind (``1`` ordinary, ``2`` renamed or
#: copied, ``u`` unmerged). Paths may contain spaces, so the line is split with this maxsplit and the
#: remainder taken verbatim.
_ENTRY_PATH_INDEX = {"1": 8, "2": 9, "u": 10}
_UNTRACKED_STATUS = "?"
#: git says this when the directory is not a work tree; the wording has been stable for many releases
#: and a missed match only downgrades the reason to ``error``.
_NOT_A_REPO_RE = re.compile(
    r"not a git repository|not a work tree|does not appear to be a git repository", re.IGNORECASE
)


# --------------------------------------------------------------------------- #
# public value objects
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class GitObservation:
    """What Studio knows about a repository's git state at one moment.

    ``available=False`` carries a ``reason`` and leaves every other field empty; it is a normal value
    the UI renders as a degraded capability strip, never an error the user has to dismiss
    (FR-GIT-005). ``ahead``/``behind``/``upstream`` are ``None`` — not ``0`` — when the branch has no
    upstream, so "in sync" and "nothing to compare against" stay distinguishable.
    """

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
        return cls(
            available=False,
            reason=reason,
            branch=None,
            detached=False,
            head=None,
            head_subject=None,
            dirty=False,
            dirty_files=0,
            ahead=None,
            behind=None,
            upstream=None,
            observed_at=observed_at,
            took_ms=took_ms,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "branch": self.branch,
            "detached": self.detached,
            "head": self.head,
            "head_subject": self.head_subject,
            "dirty": self.dirty,
            "dirty_files": self.dirty_files,
            "ahead": self.ahead,
            "behind": self.behind,
            "upstream": self.upstream,
            "observed_at": self.observed_at,
            "took_ms": self.took_ms,
        }


@dataclass(frozen=True, slots=True)
class GitCommit:
    """One commit line. ``at`` is normalised to the §0.2 UTC form so it sorts against audit rows."""

    sha: str
    at: str
    subject: str

    def to_json(self) -> dict[str, Any]:
        return {"sha": self.sha, "at": self.at, "subject": self.subject}


@dataclass(frozen=True, slots=True)
class GitResult:
    """The outcome of one invocation. ``exit_code is None`` means the process never ran or was killed.

    ``stderr`` is redacted (a remote URL in an error message can carry a token) and capped; ``stdout``
    is not redacted, because it is repository content — masking a diff or a file body would corrupt the
    very evidence the caller asked for. Whatever renders or exports it redacts at that boundary.
    """

    argv: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def to_json(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
        }


@dataclass(frozen=True, slots=True)
class _Run:
    """Internal spawn result: raw bytes, so a caller can apply its own cap before decoding."""

    argv: tuple[str, ...]
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

    def failure_reason(self) -> str:
        """Classify a failure into one of ``OBSERVATION_REASONS``."""
        if self.spawn_reason:
            return self.spawn_reason
        if self.timed_out:
            return REASON_TIMEOUT
        if _NOT_A_REPO_RE.search(self.stderr):
            return REASON_NOT_A_REPO
        return REASON_ERROR


# --------------------------------------------------------------------------- #
# porcelain v2 parsing (pure)
# --------------------------------------------------------------------------- #


def _unquote_path(value: str) -> str:
    """Undo git's C-style quoting of a path with control characters or non-ASCII bytes.

    Only quoted paths are touched. Octal escapes are UTF-8 *bytes*, so the escaped string is decoded to
    code points 0-255 and re-interpreted as UTF-8; a path we cannot decode is returned as git printed it
    rather than dropped, because a mangled name in a dirty list is still a signal.
    """
    if len(value) < 2 or not value.startswith('"') or not value.endswith('"'):
        return value
    inner = value[1:-1]
    try:
        return inner.encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return value


def _parse_status(text: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """``status --porcelain=v2 --branch`` → (headers, [(relpath, status)]).

    Headers keep their dotted names (``branch.oid``, ``branch.head``, ``branch.upstream``,
    ``branch.ab``). Entry status is the porcelain ``XY`` field for tracked changes and ``?`` for an
    untracked file, so a caller can tell "modified" from "not in git at all" without a second command.
    Ignored entries (``!``) are dropped: Studio never asks for them, and counting one as dirty would
    report a repository with build output in it as having uncommitted work.
    """
    headers: dict[str, str] = {}
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line:
            continue
        if line.startswith(_HEADER_PREFIX):
            key, _, value = line[len(_HEADER_PREFIX) :].partition(" ")
            headers[key] = value.strip()
            continue
        kind = line[0]
        if kind == "!":
            continue
        if kind == _UNTRACKED_STATUS:
            entries.append((_unquote_path(line[2:]), _UNTRACKED_STATUS))
            continue
        index = _ENTRY_PATH_INDEX.get(kind)
        if index is None:
            continue
        fields = line.split(" ", index)
        if len(fields) <= index:
            continue
        path = fields[index]
        if kind == "2":  # "<path>\t<origPath>" for a rename or copy: the new path is what changed
            path = path.split("\t", 1)[0]
        entries.append((_unquote_path(path), fields[1]))
    return headers, entries


def _parse_ab(headers: dict[str, str]) -> tuple[int | None, int | None]:
    """``# branch.ab +3 -1`` → ``(ahead, behind)``; ``(None, None)`` when the header is absent."""
    match = _AB_RE.match(headers.get("branch.ab", ""))
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _iso_utc(raw: str) -> str:
    """git's ``%cI`` (offset-bearing) → the §0.2 ``…Z`` form, so commits sort against audit rows.

    An unparseable value is passed through: a wrong-looking timestamp on a commit line is better than
    dropping the commit that carries it.
    """
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


# --------------------------------------------------------------------------- #
# the observer
# --------------------------------------------------------------------------- #


class GitObserver:
    """Every git invocation Studio makes, and the only place allowed to make one."""

    __slots__ = ("_git_path", "_clock")

    def __init__(self, git_path: str | None, clock: C.Clock) -> None:
        self._git_path = git_path or None
        self._clock = clock

    @property
    def git_path(self) -> str | None:
        """The binary that would be spawned, or ``None``. ``/health`` reports it as ``tools.git``."""
        return self._git_path

    # ---------------------------------------------------------------- policy #

    @staticmethod
    def check_argv(argv: Sequence[str]) -> tuple[str, ...]:
        """Prove an argv is a read, or raise ``StudioError("internal_error")``.

        The rule set is §1.3's and lives in ``security.assert_git_argv_readonly``, which this delegates
        to: one implementation, so a policy fix cannot land in one of two places. What this adds is the
        ``git --version`` exemption — that argv names no verb, so the shared rule ("a repository read
        names a read verb") cannot express it — and the tuple this class then spawns.
        """
        tokens = tuple(str(token) for token in argv)
        if not tokens:
            raise StudioError("internal_error", "git invocation carries no subcommand")
        if tokens == _VERSION_ARGV:
            return tokens
        security.assert_git_argv_readonly(tokens)
        return tokens

    def _spawn_argv(self, tokens: tuple[str, ...]) -> list[str]:
        """The full command line: the binary, then the tokens ``check_argv`` has already proven."""
        return [self._git_path or "git", *tokens]

    def _env(self) -> dict[str, str]:
        """Minimal env plus the git pins. Rebuilt per call so a variable exported into the gateway
        after startup is still caught by ``build_subprocess_env``'s bypass assertion."""
        env = security.build_subprocess_env()
        env.update(_GIT_ENV)
        return env

    # --------------------------------------------------------------- spawning #

    def _run(self, repo: Path | None, argv: Sequence[str], *, stdout_cap: int) -> _Run:
        """Spawn one read verb and come back with bounded bytes, never an exception for git's failure.

        Only two conditions raise: an argv that is not a read (a bug in this process, loud on purpose)
        and an inherited AI-DLC bypass variable (an operator misconfiguration the caller must see).
        Everything git itself can do — missing binary, timeout, non-zero exit — is reported in the
        returned value.
        """
        tokens = self.check_argv(argv)
        if self._git_path is None:
            return _Run(tokens, None, b"", "git is not installed", False, False, REASON_GIT_MISSING)
        command = self._spawn_argv(tokens)
        env = self._env()
        cwd = str(repo) if repo is not None else None
        try:
            proc = subprocess.run(  # noqa: S603 - argv list, never a shell; verb allowlisted above
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=C.GIT_TIMEOUT_SECS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # subprocess.run has already killed and reaped the child.
            return _Run(tokens, None, b"", "git did not finish in time", True, False, None)
        except OSError as exc:
            return _Run(
                tokens, None, b"", f"{type(exc).__name__}: {exc}", False, False,
                self._spawn_failure_reason(exc),
            )
        raw = proc.stdout or b""
        err = security.redact(
            security.truncate_output(proc.stderr or b"", C.MAX_SUBPROCESS_STDERR)
        ).strip()
        return _Run(
            tokens,
            proc.returncode,
            raw[: stdout_cap + 1],
            err,
            False,
            len(raw) > stdout_cap,
            None,
        )

    def _spawn_failure_reason(self, exc: OSError) -> str:
        """Tell "the git binary is gone" from "this repository cannot be entered".

        CPython reports the ``cwd`` as ``filename`` when the child failed at ``chdir`` and the
        executable otherwise, so the two cases are distinguishable — and they mean different things to
        the user: one is a tooling gap, the other is a repository availability problem.
        """
        if isinstance(exc, FileNotFoundError) and getattr(exc, "filename", None) in (
            None,
            self._git_path,
        ):
            return REASON_GIT_MISSING
        return REASON_ERROR

    def _took(self, started: float) -> int:
        return max(0, int(round((self._clock.monotonic() - started) * 1000)))

    # ------------------------------------------------------------- public API #

    def run_sync(self, repo: Path, argv: Sequence[str]) -> GitResult:
        """One allowlisted read verb in ``repo``. ``argv`` excludes ``git``; the verb is ``argv[0]``."""
        run = self._run(repo, argv, stdout_cap=C.MAX_SUBPROCESS_STDOUT)
        return GitResult(
            argv=run.argv,
            exit_code=run.exit_code,
            stdout=security.truncate_output(run.stdout, C.MAX_SUBPROCESS_STDOUT),
            stderr=run.stderr,
            timed_out=run.timed_out,
        )

    def version(self) -> str | None:
        """``git --version`` as printed, or ``None`` when git cannot be run.

        Not in §1.16; added because ``/health`` must report the version of the binary Studio would use
        (§2.1) and this is the only module permitted to spawn git. Runs with no ``cwd`` and no
        repository: ``--version`` short-circuits before git discovers or reads anything.
        """
        run = self._run(None, _VERSION_ARGV, stdout_cap=C.MAX_TEXT_PREVIEW_BYTES)
        if not run.ok:
            return None
        first = run.text().strip().splitlines()
        return first[0].strip() if first else None

    def common_dir(self, repo: Path) -> str | None:
        """``rev-parse --git-common-dir`` as an absolute realpath, or ``None`` when there is no repo.

        The common dir (not ``--git-dir``) is what identifies a repository across its worktrees, which
        is why ``RepoRegistry`` uses it as the identity fallback (§1.5): two worktrees of one clone must
        not register as two unrelated repositories.
        """
        run = self._run(repo, _COMMON_DIR_ARGV, stdout_cap=C.MAX_TEXT_PREVIEW_BYTES)
        if not run.ok:
            return None
        lines = [line.strip() for line in run.text().splitlines() if line.strip()]
        if not lines:
            return None
        candidate = Path(lines[0])
        if not candidate.is_absolute():
            candidate = Path(repo) / candidate
        return str(security.realpath(candidate))

    def observe(self, repo: Path) -> GitObservation:
        """Branch, head, dirty count and upstream divergence, or a reason why none of it is known.

        ``dirty_files`` is a floor, not a census: a working tree with more changes than the subprocess
        output cap holds is still reported as dirty, with the count of what fitted. Nothing decides
        anything on that number — it is a strip in the UI — and undercounting is the only direction it
        can be wrong in.
        """
        started = self._clock.monotonic()
        at = self._clock.iso()
        try:
            return self._observe(repo, observed_at=at, started=started)
        except Exception:  # noqa: BLE001 - FR-GIT-005: an observation never raises at its caller
            return GitObservation.unavailable(
                REASON_ERROR, observed_at=at, took_ms=self._took(started)
            )

    def _observe(self, repo: Path, *, observed_at: str, started: float) -> GitObservation:
        if self._git_path is None:
            return GitObservation.unavailable(
                REASON_GIT_MISSING, observed_at=observed_at, took_ms=self._took(started)
            )
        status = self._run(repo, _STATUS_ARGV, stdout_cap=C.MAX_SUBPROCESS_STDOUT)
        if not status.ok:
            return GitObservation.unavailable(
                status.failure_reason(), observed_at=observed_at, took_ms=self._took(started)
            )
        headers, entries = _parse_status(status.text())
        branch, detached = self._branch(repo, headers)
        head = headers.get("branch.oid") or None
        if head == _INITIAL:  # a repository with no commit yet: real, readable, no head
            head = None
        upstream = headers.get("branch.upstream") or None
        ahead, behind = _parse_ab(headers)
        if upstream and ahead is None:
            ahead, behind = self._ahead_behind(repo)
        if not upstream:
            # No upstream is not "in sync": leave the pair unknown rather than claim zero divergence.
            ahead, behind = None, None
        head_subject: str | None = None
        recent = self._log(repo, (), 1)
        if recent:
            head_subject = recent[0].subject or None
            head = head or recent[0].sha or None
        return GitObservation(
            available=True,
            reason=None,
            branch=branch,
            detached=detached,
            head=head,
            head_subject=head_subject,
            dirty=bool(entries),
            dirty_files=len(entries),
            ahead=ahead,
            behind=behind,
            upstream=upstream,
            observed_at=observed_at,
            took_ms=self._took(started),
        )

    def _branch(self, repo: Path, headers: dict[str, str]) -> tuple[str | None, bool]:
        """(branch, detached) from the status header, falling back to ``rev-parse --abbrev-ref HEAD``.

        The fallback exists for a git whose ``--branch`` header is missing; spending a second process on
        every observation to learn what ``# branch.head`` already said would double the cost of a
        64-repository scan for nothing.
        """
        raw = headers.get("branch.head")
        if raw == _DETACHED:
            return None, True
        if raw:
            return raw, False
        run = self._run(repo, _BRANCH_ARGV, stdout_cap=C.MAX_TEXT_PREVIEW_BYTES)
        if not run.ok:
            return None, False
        lines = [line.strip() for line in run.text().splitlines() if line.strip()]
        if not lines:
            return None, False
        name = lines[0]
        if name == "HEAD":  # what git prints for a detached head
            return None, True
        return name, False

    def _ahead_behind(self, repo: Path) -> tuple[int | None, int | None]:
        """``rev-list --left-right --count @{upstream}...HEAD`` → ``(ahead, behind)``.

        The left side of the range is the upstream, so git prints **behind first** (verified against
        git 2.50: a branch 2636 commits behind and 0 ahead prints ``2636\\t0``). The fields are swapped
        here rather than by reordering the range, because §1.16 pins this argv.
        """
        run = self._run(repo, _AHEAD_BEHIND_ARGV, stdout_cap=C.MAX_TEXT_PREVIEW_BYTES)
        if not run.ok:
            return None, None
        fields = run.text().split()
        if len(fields) != 2 or not all(field.isdigit() for field in fields):
            return None, None
        return int(fields[1]), int(fields[0])

    def prior_version(self, repo: Path, relpath: str) -> bytes | None:
        """The committed bytes of one artifact (``show HEAD:<relpath>``), or ``None``.

        ``None`` means "no comparison is possible": the file is untracked, HEAD is unborn, git failed, or
        the blob is larger than ``MAX_ARTIFACT_RENDER_BYTES``. The size is asked for first with
        ``cat-file -s`` so an oversized blob is refused before it is materialised in the gateway's
        memory; when that probe cannot answer, the read still refuses on the byte count instead of
        returning a truncated file, because half an artifact makes a diff view confidently wrong.
        """
        rel = self._safe_rel(repo, relpath, allow_tree=False)
        spec = f"HEAD:{rel}"
        size = self._blob_size(repo, spec)
        if size is not None and size > C.MAX_ARTIFACT_RENDER_BYTES:
            return None
        run = self._run(repo, ("show", "--no-textconv", spec), stdout_cap=C.MAX_ARTIFACT_RENDER_BYTES)
        if not run.ok or run.truncated:
            return None
        return run.stdout

    def _blob_size(self, repo: Path, spec: str) -> int | None:
        """``cat-file -s <rev>:<path>`` in bytes, or ``None`` when git cannot say."""
        run = self._run(repo, ("cat-file", "-s", spec), stdout_cap=C.MAX_TEXT_PREVIEW_BYTES)
        if not run.ok:
            return None
        text = run.text().strip()
        return int(text) if text.isdigit() else None

    def diff(self, repo: Path, relpath: str) -> str | None:
        """Working-tree diff for one path, capped at ``MAX_SUBPROCESS_STDOUT`` with a visible marker.

        ``None`` means the diff could not be obtained (no git, no repo, git failed); ``""`` means the
        path genuinely has no local change. The caller needs both: one is a degraded capability, the
        other is information. Unlike ``prior_version`` this output is truncated rather than refused —
        it is prose for a human to read, not evidence a decision is compared against.
        """
        rel = self._safe_rel(repo, relpath, allow_tree=True)
        run = self._run(repo, (*_DIFF_ARGV, "--", rel), stdout_cap=C.MAX_SUBPROCESS_STDOUT)
        if not run.ok:
            return None
        return security.truncate_output(run.stdout, C.MAX_SUBPROCESS_STDOUT)

    def recent_commits(
        self, repo: Path, paths: Sequence[str], limit: int = 10
    ) -> list[GitCommit]:
        """Commits touching ``paths`` (all commits when empty), newest first, at most ``MAX_LOG_LIMIT``."""
        rels = [self._safe_rel(repo, path, allow_tree=True) for path in paths if str(path).strip()]
        return self._log(repo, rels, limit)

    def _log(self, repo: Path, rels: Sequence[str], limit: int) -> list[GitCommit]:
        bounded = max(1, min(int(limit), MAX_LOG_LIMIT))
        argv: list[str] = ["log", "--no-decorate", f"--format={_LOG_FORMAT}", "-n", str(bounded)]
        if rels:
            # `--` first: without it a path that starts with a dash, or one that happens to match a ref
            # name, would be read as an option or a revision.
            argv += ["--", *rels]
        run = self._run(repo, argv, stdout_cap=C.MAX_SUBPROCESS_STDOUT)
        if not run.ok:
            return []
        out: list[GitCommit] = []
        for line in run.text().splitlines():
            if not line.strip():
                continue
            fields = line.split(_FIELD_SEP)
            if len(fields) < 3:
                continue
            out.append(
                GitCommit(
                    sha=fields[0].strip(),
                    at=_iso_utc(fields[1]),
                    subject=_FIELD_SEP.join(fields[2:]).strip(),
                )
            )
            if len(out) >= bounded:
                break
        return out

    def dirty_split(self, repo: Path, owned_paths: Sequence[str]) -> dict[str, Any]:
        """Split the dirty set into "files Studio installed" and "everything else".

        FR-GIT-004: an upgrade must be blocked by drift in receipt-owned files, and must **not** be
        blocked because the user has unrelated work in progress. So the owned paths are listed by name
        (the installer needs to show them) while the rest is only counted (it is the user's business).

        Three keys beyond §1.16, all additive: ``owned_changes`` carries the porcelain status per owned
        path so the intent git view can render ``{relpath, status}`` without a second invocation;
        ``available``/``reason`` keep a clean tree apart from "git could not tell us"; ``truncated`` says the
        status output hit the output cap, in which case an empty ``owned_dirty`` is NOT proof that
        nothing drifted (the installer's receipt digests are the authoritative drift check).
        """
        owned = [self._safe_rel(repo, path, allow_tree=True) for path in owned_paths if str(path).strip()]
        run = self._run(repo, _STATUS_ARGV, stdout_cap=C.MAX_SUBPROCESS_STDOUT)
        if not run.ok:
            return {
                "owned_dirty": [],
                "unrelated_dirty": 0,
                "owned_changes": [],
                "available": False,
                "reason": run.failure_reason(),
                "truncated": False,
            }
        _, entries = _parse_status(run.text())
        # A caller that names the repository root owns everything in it.
        whole_tree = any(root == "." for root in owned)
        owned_dirty: list[str] = []
        owned_changes: list[dict[str, str]] = []
        unrelated = 0
        for path, status in entries:
            if whole_tree or any(path == root or path.startswith(root + "/") for root in owned):
                owned_dirty.append(path)
                owned_changes.append({"relpath": path, "status": status})
            else:
                unrelated += 1
        owned_dirty.sort()
        owned_changes.sort(key=lambda row: row["relpath"])
        return {
            "owned_dirty": owned_dirty,
            "unrelated_dirty": unrelated,
            "owned_changes": owned_changes,
            "available": True,
            "reason": None,
            "truncated": run.truncated,
        }

    # ------------------------------------------------------------------ paths #

    @staticmethod
    def _safe_rel(repo: Path, relpath: str, *, allow_tree: bool) -> str:
        """A repo-relative posix path proven to stay inside ``repo``, or ``StudioError("bad_path")``.

        Containment is checked here even though the paths come from Studio's own projection: an artifact
        id maps to a relpath that arrived in a URL, and ``git show HEAD:../../etc/passwd`` would
        otherwise read outside the repository the user registered.
        """
        resolved = security.resolve_inside(repo, relpath)
        root = security.realpath(repo)
        rel = "." if resolved == root else resolved.relative_to(root).as_posix()
        if rel == "." and not allow_tree:
            raise StudioError("bad_path", "a file path is required, not the repository root")
        return rel
