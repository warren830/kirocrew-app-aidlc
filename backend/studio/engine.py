"""The only door through which Studio runs AI-DLC's own tools: ``bun <repo>/<harness>/tools/<tool>.ts``.

An argv is a list (``shell`` pinned False) of fixed allowlist tokens and kwargs proved against a grammar, so no
value can be re-read as a flag. ``security.assert_engine_argv_allowed`` re-checks every argv against
``constants.ENGINE_READ_VERBS``/``ENGINE_ADMIN_VERBS``, previews included. Admin verbs need a held lease,
``doctor`` (it appends audit rows) an explicit user request, and a cursor switch is believed only after a
read-back from disk. ``run_sync`` and ``switch_cursor_sync`` block: callers go through ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, fields, replace
from itertools import takewhile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from . import constants as C
from . import security
from .errors import StudioError

if TYPE_CHECKING:  # annotations only: importing these at runtime would invert the module layering
    from .activity import ActivityProjector
    from .constants import Clock


# --------------------------------------------------------------------------- #
# the verb table
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EngineVerb:
    """One allowlisted invocation. ``"{name}"`` is a required kwarg; ``"?--flag", "{name}"`` is emitted only
    when that kwarg is non-empty. The mutation flags are how the UI labels the operation, so they are contract.
    """

    key: str
    tool: str
    argv: tuple[str, ...]
    lease: str | None = None
    mutates_audit: bool = False
    mutates_state: bool = False
    mutates_harness: bool = False
    mutates_session: bool = False
    json_output: bool = False
    explicit_user_only: bool = False
    timeout_secs: float = C.ENGINE_READ_TIMEOUT_SECS

    @property
    def verb(self) -> str:
        return self.argv[0]

    @property
    def admin(self) -> bool:
        return self.lease == "admin"

    @property
    def side_effects(self) -> tuple[str, ...]:
        flags = (self.mutates_audit, self.mutates_state, self.mutates_harness, self.mutates_session)
        return tuple(code for code, on in zip(("audit", "state", "harness", "session"), flags) if on)


_UTILITY = "aidlc-utility.ts"
#: The record writers name their record: without both flags 2.7.1 resolves it from the calling session's
#: binding before the cursor, which may not be the record the user was shown.
_RECORD = ("?--intent", "{dir_name}", "?--space", "{slug}")


def _writer(key: str, argv: tuple[str, ...]) -> EngineVerb:
    """An admin verb that appends audit rows and rewrites the record's state file."""
    return EngineVerb(
        key, _UTILITY, argv, lease="admin", mutates_audit=True, mutates_state=True,
        timeout_secs=C.SUBPROCESS_TIMEOUT_SECS,
    )


#: Every engine call Studio can make; each is re-checked against the security lanes at call time, so this
#: table cannot widen the policy.
ENGINE_ALLOWLIST: dict[str, EngineVerb] = {verb.key: verb for verb in (
    EngineVerb("utility.version", _UTILITY, ("version",)),
    EngineVerb("utility.intent_list", _UTILITY, ("intent", "--json"), json_output=True),
    EngineVerb("utility.space_list", _UTILITY, ("space", "--json"), json_output=True),
    EngineVerb("state.lookup", "aidlc-state.ts", ("lookup", "{query}", "{arg}")),
    # Both switches re-stamp the CALLING session's binding (aidlc-utility.ts:6201-6203, :6258-6259): a
    # `session` effect, not `state`. An unknown intent name makes the engine append ERROR_LOGGED.
    EngineVerb("utility.intent_switch", _UTILITY, ("intent", "{dir_name}"), lease="admin", mutates_session=True),
    EngineVerb(
        "utility.space_switch", _UTILITY, ("space", "{slug}"), lease="admin", mutates_harness=True,
        mutates_session=True,
    ),
    EngineVerb(
        "utility.space_create", _UTILITY, ("space-create", "{slug}"), lease="admin", mutates_state=True,
        timeout_secs=C.SUBPROCESS_TIMEOUT_SECS,
    ),
    # The engine refuses a flag given a blank value, so an optional flag with no value is left out entirely.
    _writer("utility.intent_create", (
        "intent-create", "--scope", "{scope}", "--arguments", "{arguments}", "--label", "{label}",
        "?--depth", "{depth}", "?--test-strategy", "{test_strategy}", "?--review", "{review}", "?--repos", "{repos}",
    )),
    EngineVerb(
        "runtime.compile", "aidlc-runtime.ts", ("compile",), lease="admin", mutates_audit=True, json_output=True,
        timeout_secs=C.SUBPROCESS_TIMEOUT_SECS,
    ),
    _writer("utility.recompose", ("recompose", "?--skip", "{skip}", "?--add", "{add}", *_RECORD)),
    _writer("utility.scope_change", (
        "scope-change", "--scope", "{scope}", "?--depth", "{depth}", "?--test-strategy", "{test_strategy}", *_RECORD,
    )),
    _writer("utility.config_change", (
        "config-change", "?--depth", "{depth}", "?--test-strategy", "{test_strategy}", *_RECORD,
    )),
    # Appends HEALTH_CHECKED/GUARDRAIL_LOADED rows and clears leaked tmp locks: never the reconciler's (A12).
    EngineVerb("utility.doctor", _UTILITY, ("doctor",), lease="admin", mutates_audit=True, explicit_user_only=True),
)}

#: The forbidden-tool floor ``security`` enforces, under the name callers still use. There is no second list:
#: whatever the allowlist and ``constants.ENGINE_*_VERBS`` do not name is refused.
ENGINE_DENY: frozenset[str] = frozenset(C.ENGINE_FORBIDDEN_TOOLS)


# --------------------------------------------------------------------------- #
# kwarg grammar
# --------------------------------------------------------------------------- #

_MAX_CURSOR_BYTES = 4096  # a cursor file is one directory name and a newline
_MAX_CURSOR_REPORT_CHARS = 128
_MAX_ARGUMENTS_CHARS = 4000  # §1.9: --arguments becomes one argv token and an audit field
#: ``aidlc-state.ts lookup`` queries (§1.9); an unlisted one would reach the engine's own argument parser.
_LOOKUP_QUERIES = (
    "phase-of", "next-stage", "agent-for", "number-of", "stages-in-scope", "first-in-phase", "validate-stage",
    "validate-phase",
)
_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,31}$")  # a config word; plan.py owns the legal values
#: Single-token kwargs and their grammar (scope ids have the shape of stage slugs).
_KWARG_RE = {
    "dir_name": C.INTENT_DIR_RE, "slug": C.SPACE_RE, "scope": C.STAGE_SLUG_RE, "arg": C.STAGE_SLUG_RE,
    "depth": _TOKEN_RE, "test_strategy": _TOKEN_RE, "review": _TOKEN_RE,
}
#: Comma-separated kwargs: the grammar of each item and the noun a refusal names.
_KWARG_LISTS = {
    "skip": (C.STAGE_SLUG_RE, "stage slugs"), "add": (C.STAGE_SLUG_RE, "stage slugs"),
    "repos": (C.UNIT_NAME_RE, "repository names"),
}
_CONTROL_KWARGS = ("_confirmed", "lease_generation", "repo_id")  # read by the runner, never put in an argv
#: Canonical verb -> the first engine version of each spelling in ``C.ENGINE_VERB_ALIASES`` but the last.
_ALIAS_FLOORS: dict[str, tuple[tuple[int, ...], ...]] = {"intent-create": ((2, 6),)}


def _reject(name: str, value: str, why: str) -> None:
    raise ValueError(f"engine kwarg {name}={value!r} rejected: {why}")


def _check_match(name: str, value: str, pattern: re.Pattern[str]) -> None:
    if not pattern.match(value):
        _reject(name, value, f"does not match {pattern.pattern}")


def _validate_kwarg(name: str, value: str) -> None:
    """Prove one kwarg value is safe in an argv, or raise ``ValueError``.

    A NUL truncates the token at ``execve``; a leading ``-`` would be re-read as a flag (``--label`` turned
    ``--project-dir``); ``$`` matches before a trailing newline, hence the last check.
    """
    if "\x00" in value:
        _reject(name, value, "contains a NUL byte")
    if value.startswith("-"):
        _reject(name, value, "must not start with '-' (it would be read as a flag)")
    if name in _KWARG_RE:
        _check_match(name, value, _KWARG_RE[name])
    elif name in _KWARG_LISTS:
        pattern, noun = _KWARG_LISTS[name]
        parts = [part.strip() for part in value.split(",")]
        if not all(parts):
            _reject(name, value, f"must be a comma-separated list of {noun}")
        for part in parts:
            _check_match(name, part, pattern)
    elif name == "label":  # slugified into the new record's directory name, so it obeys that grammar
        if len(value) > C.MAX_LABEL_CHARS:
            _reject(name, value, f"longer than {C.MAX_LABEL_CHARS} characters")
        _check_match(name, value, C.INTENT_DIR_RE)
    elif name == "arguments":  # the objective plus its context: the one value that may span lines
        if len(value) > _MAX_ARGUMENTS_CHARS:
            _reject(name, value, f"longer than {_MAX_ARGUMENTS_CHARS} characters")
    elif name == "query":
        if value not in _LOOKUP_QUERIES:
            _reject(name, value, f"not one of {', '.join(_LOOKUP_QUERIES)}")
    else:  # pragma: no cover - unreachable: placeholders are drawn from the table above
        _reject(name, value, "has no validator")
    if "\n" in value and name != "arguments":
        _reject(name, value, "contains a newline")


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #


def _wire(obj: Any, *omit: str) -> dict[str, Any]:
    """A dataclass's fields in declaration order with tuples as lists: the shape the wire types pin."""
    out: dict[str, Any] = {}
    for field in fields(obj):
        if field.name not in omit:
            value = getattr(obj, field.name)
            out[field.name] = list(value) if isinstance(value, tuple) else value
    return out


@dataclass(frozen=True, slots=True)
class EngineResult:
    """One invocation, safe to store and render: argv/stdout/stderr are capped and redacted at capture, and
    absolute paths outside the repository (the interpreter's) become ``<path>``. ``side_effects`` lets a caller
    holding only the result label a ``doctor`` run as audit-appending.
    """

    verb_key: str
    argv: tuple[str, ...]
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    stdout: str
    stderr: str
    json: Any | None
    ok: bool
    side_effects: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return _wire(self)


@dataclass(frozen=True, slots=True)
class CursorReadback:
    """Where the cursor really points after a switch: observed values, not requested ones. ``mismatch`` is empty
    exactly when ``ok``; ``to_json`` leaves out ``results`` (transcripts live in the activity table, §3.1).
    """

    ok: bool
    space: str
    dir_name: str
    uuid: str | None
    state_sha256: str | None
    mismatch: tuple[str, ...]
    results: tuple[EngineResult, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return _wire(self, "results")


#: Mismatch codes ``switch_cursor_sync`` may report; the UI renders one sentence per code.
CURSOR_MISMATCH_CODES = ("active_space", "active_intent", "state_missing", "intent_list_active", "uuid")


def _selection_repair_is_safe(readback: CursorReadback, listing: EngineResult) -> bool:
    """Only the engine's ``active`` disagrees, in a listing that parsed: every file Studio reads is right.

    2.7.1 resolves ``active`` from the calling session's binding before the cursor, and the bun child inherits
    the gateway's registered session, so a stale binding can disagree while the cursor on disk is correct.
    """
    return readback.mismatch == ("intent_list_active",) and listing.ok and isinstance(listing.json, Mapping)


# --------------------------------------------------------------------------- #
# the runner
# --------------------------------------------------------------------------- #


class EngineRunner:
    """Builds, guards and runs engine argvs; nothing else in the backend starts ``bun``."""

    def __init__(
        self, *, bun_path: str | None, clock: "Clock", activity: "ActivityProjector | None" = None
    ) -> None:
        self._bun_path = bun_path
        self._clock = clock
        self._activity = activity

    @property
    def bun_path(self) -> str | None:
        return self._bun_path

    def set_bun_path(self, path: str | None) -> None:
        """Apply the container's validated interpreter selection to subsequent invocations."""
        self._bun_path = path

    # ---- argv ----

    def build_argv(self, verb: EngineVerb, repo: Path, engine_dir: str, **kwargs: str) -> list[str]:
        """``[bun, <repo>/<engine_dir>/tools/<tool>, *tokens, "--project-dir", <repo>]``, never a string."""
        bun_path = self._bun_path
        if not bun_path:
            raise StudioError("bun_missing", "bun is not available; AI-DLC's tools cannot be run")
        repo = Path(repo)
        # Only a trailing slash is trimmed, so an absolute engine_dir reaches resolve_inside and is refused.
        # Containment is proved on the resolved path; the argv keeps the spelling the user registered (A13).
        tool_rel = f"{engine_dir.rstrip('/')}/tools/{verb.tool}"
        security.resolve_inside(repo, tool_rel)
        spelling = self._verb_spelling(verb, repo, engine_dir)
        security.assert_engine_argv_allowed(verb.tool, spelling, admin=verb.admin)

        filled = [spelling]
        supplied = {k: v for k, v in kwargs.items() if k not in _CONTROL_KWARGS}
        used: set[str] = set()
        tokens = iter(verb.argv[1:])
        for token in tokens:
            if token.startswith("?--"):
                flag, name = token[1:], next(tokens)[1:-1]
            elif token.startswith("{") and token.endswith("}"):
                flag, name = None, token[1:-1]
            else:
                filled.append(token)
                continue
            value = supplied.get(name)
            text = "" if value is None else str(value)
            if text:
                _validate_kwarg(name, text)
                filled.extend((flag, text) if flag else (text,))
            elif flag is None:
                raise ValueError(f"engine verb {verb.key} requires kwarg {name!r}")
            used.add(name)

        unknown = sorted(set(supplied) - used)
        if unknown:
            raise ValueError(f"engine verb {verb.key} does not accept kwarg(s) {', '.join(unknown)}")
        return [bun_path, str(repo / tool_rel), *filled, "--project-dir", str(repo)]

    # ---- running ----

    def run_sync(self, verb_key: str, repo: Path, engine_dir: str, **kwargs: str) -> EngineResult:
        """Run one allowlisted verb to completion (blocking; call through ``asyncio.to_thread``).

        A timeout is a result, since the caller needs the duration and partial output; a missing interpreter or
        a policy refusal is an exception, since nothing legitimately started.
        """
        verb = self.verb_for(verb_key)
        confirmed = bool(kwargs.get("_confirmed", False))
        lease_generation = kwargs.get("lease_generation")
        repo_id = kwargs.get("repo_id")
        if verb.explicit_user_only and not confirmed:
            raise StudioError(
                "invalid_decision",
                f"{verb_key} appends audit rows and runs only on an explicit user request",
                details={"verb_key": verb_key, "side_effects": list(verb.side_effects)},
            )
        if verb.lease and lease_generation is None:
            raise StudioError(
                "internal_error",
                f"{verb_key} requires a held {verb.lease} lease",
                details={"verb_key": verb_key, "lease": verb.lease},
            )

        argv = self.build_argv(verb, repo, engine_dir, **kwargs)
        env = security.build_subprocess_env()
        started = self._clock.monotonic()
        timed_out = False
        exit_code: int | None
        try:
            completed = subprocess.run(  # noqa: S603 - argv list, shell=False, fixed allowlist
                argv, cwd=str(repo), env=env, capture_output=True, timeout=verb.timeout_secs, shell=False,
                stdin=subprocess.DEVNULL, check=False,
            )
            exit_code = completed.returncode
            raw_out, raw_err = completed.stdout or b"", completed.stderr or b""
        except (FileNotFoundError, PermissionError) as exc:
            missing = isinstance(exc, FileNotFoundError)
            raise StudioError(
                "bun_missing",
                "bun could not be executed" if missing else "bun is present but not executable",
                details={"detail": str(exc.filename or "")},
            ) from None
        except subprocess.TimeoutExpired as exc:
            timed_out, exit_code = True, None
            raw_out, raw_err = exc.stdout or b"", exc.stderr or b""

        duration_ms = max(0, int(round((self._clock.monotonic() - started) * 1000)))
        result = EngineResult(
            verb_key=verb_key,
            argv=_redact_argv(argv, Path(repo)),
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=duration_ms,
            stdout=security.redact(security.truncate_output(raw_out, C.MAX_SUBPROCESS_STDOUT)),
            stderr=security.redact(security.truncate_output(raw_err, C.MAX_SUBPROCESS_STDERR)),
            json=self._parse_json(verb, raw_out),
            ok=(exit_code == 0 and not timed_out),
            side_effects=verb.side_effects,
        )
        self._record_run(result, repo_id=repo_id if isinstance(repo_id, str) else None)
        return result

    async def run(self, verb_key: str, repo: Path, engine_dir: str, **kwargs: str) -> EngineResult:
        """``run_sync`` on a worker thread."""
        return await asyncio.to_thread(self.run_sync, verb_key, repo, engine_dir, **kwargs)

    # ---- cursor ----

    def switch_cursor_sync(
        self,
        repo: Path,
        engine_dir: str,
        space: str,
        dir_name: str,
        expected_uuid: str | None,
        *,
        lease_generation: int | None = None,
        repo_id: str | None = None,
    ) -> CursorReadback:
        """Point the repo-shared cursor at one intent and prove, from disk, that it moved.

        The switch verbs are skipped when the cursor already matches (a no-op switch risks an ERROR_LOGGED row),
        but the read-back always runs, after a failed switch too, so a half-done switch is a mismatch. When only
        the engine's ``active`` disagrees (``_selection_repair_is_safe``) the intent switch is issued once more
        and only the second read-back counts. Paths are built from the validated request; the cursor's own
        bytes are only ever compared as strings, so ``../..`` in a cursor file cannot aim a read.
        """
        repo = Path(repo)
        _validate_kwarg("slug", space)
        _validate_kwarg("dir_name", dir_name)

        def switch(verb_key: str, **target: str) -> EngineResult:
            return self.run_sync(
                verb_key, repo, engine_dir, lease_generation=lease_generation, repo_id=repo_id, **target
            )

        results: list[EngineResult] = []
        observed_space = self._read_cursor(repo, _ACTIVE_SPACE_REL) or C.DEFAULT_SPACE
        observed_intent = self._read_cursor(repo, _active_intent_rel(space))
        if observed_space != space:
            results.append(switch("utility.space_switch", slug=space))
        if observed_space != space or observed_intent != dir_name:
            results.append(switch("utility.intent_switch", dir_name=dir_name))

        readback, listing = self._read_back_cursor(repo, engine_dir, space, dir_name, expected_uuid, repo_id=repo_id)
        results.append(listing)
        if _selection_repair_is_safe(readback, listing):
            results.append(switch("utility.intent_switch", dir_name=dir_name))
            readback, listing = self._read_back_cursor(
                repo, engine_dir, space, dir_name, expected_uuid, repo_id=repo_id
            )
            results.append(listing)
        return replace(readback, results=tuple(results))

    def _read_back_cursor(
        self,
        repo: Path,
        engine_dir: str,
        space: str,
        dir_name: str,
        expected_uuid: str | None,
        *,
        repo_id: str | None,
    ) -> tuple[CursorReadback, EngineResult]:
        """One memory-less pass over the cursor files, the record and the engine's view. The listing comes back
        too, so the caller can tell an engine view that disagreed from one it could not read.
        """
        mismatch: list[str] = []
        final_space = self._read_cursor(repo, _ACTIVE_SPACE_REL) or C.DEFAULT_SPACE
        if final_space != space:
            mismatch.append("active_space")
        final_intent = self._read_cursor(repo, _active_intent_rel(space)) or ""
        if final_intent != dir_name:
            mismatch.append("active_intent")

        state_sha256: str | None = None
        state_path = security.resolve_inside(repo, f"aidlc/spaces/{space}/intents/{dir_name}/aidlc-state.md")
        if security.is_regular_file(state_path):
            state_sha256 = security.sha256_file(state_path)
        else:
            mismatch.append("state_missing")

        listing = self.run_sync("utility.intent_list", repo, engine_dir, repo_id=repo_id)
        payload = listing.json if isinstance(listing.json, Mapping) else None
        if payload is None or payload.get("active") != dir_name:
            mismatch.append("intent_list_active")
        row_uuid: str | None = None
        rows = payload.get("intents") if payload is not None else None
        if isinstance(rows, Sequence):
            for row in rows:
                if isinstance(row, Mapping) and row.get("dirName") == dir_name:
                    value = row.get("uuid")
                    row_uuid = str(value) if value else None
                    break
        if expected_uuid and row_uuid != expected_uuid:
            mismatch.append("uuid")

        readback = CursorReadback(
            ok=not mismatch, space=final_space, dir_name=final_intent, uuid=row_uuid, state_sha256=state_sha256,
            mismatch=tuple(mismatch),
        )
        return readback, listing

    # ---- activity ----

    async def drain_activity(self) -> int:
        """Kept for its callers: ``_record_run`` writes through ``record_sync`` at once, so no row ever waits."""
        return 0

    @property
    def pending_activity(self) -> int:
        """Always 0, for the same reason."""
        return 0

    # ---- internals ----

    @staticmethod
    def verb_for(verb_key: str) -> EngineVerb:
        try:
            return ENGINE_ALLOWLIST[verb_key]
        except KeyError:
            raise StudioError(
                "internal_error",
                f"engine verb key {verb_key!r} is not on the allowlist",
                details={"verb_key": verb_key},
            ) from None

    def _verb_spelling(self, verb: EngineVerb, repo: Path, engine_dir: str) -> str:
        """The name this repository's engine answers to (``intent-create`` was ``intent-birth`` before 2.6, and
        2.6.2 dies on the old name). Only aliased verbs read the version, so a broken harness can still be asked
        its version; an unreadable version refuses rather than guesses.
        """
        token = verb.verb
        spellings = C.ENGINE_VERB_ALIASES.get(token)
        if not spellings:
            return token
        floors = _ALIAS_FLOORS.get(token)
        if not floors:  # pragma: no cover - guarded by test_engine.py
            raise StudioError("internal_error", f"alias {token!r} has no version floor", details={"verb": token})
        version = self._installed_version(repo, engine_dir)
        if version is None:
            raise StudioError(
                "engine_unavailable",
                f"cannot tell which spelling of {token!r} this engine accepts",
                details={"verb": token, "reason": "engine_version_unreadable"},
            )
        for spelling, floor in zip(spellings, floors):
            if version >= floor:
                return spelling
        return spellings[-1]

    def _installed_version(self, repo: Path, engine_dir: str) -> tuple[int, ...] | None:
        """``AIDLC_VERSION`` of the harness on disk, read on every call because an upgrade rewrites it."""
        try:
            path = security.resolve_inside(repo, f"{engine_dir.rstrip('/')}/tools/aidlc-version.ts")
        except StudioError:
            return None
        text = security.bounded_text(path, C.MAX_TEXT_PREVIEW_BYTES)
        match = C.ENGINE_VERSION_RE.search(text) if text else None
        if not match:
            return None
        return tuple(int(chunk) for chunk in takewhile(str.isdigit, match.group(1).split("."))) or None

    @staticmethod
    def _parse_json(verb: EngineVerb, raw: bytes) -> Any | None:
        """Stdout of a ``--json`` verb, parsed from the raw bytes (redaction could break the document) and only
        when it fits the cap (a truncated document could parse into something shorter than the truth).
        """
        if not verb.json_output or not raw or len(raw) > C.MAX_SUBPROCESS_STDOUT:
            return None
        try:
            return security.redact_json(json.loads(raw.decode("utf-8", errors="replace")))
        except (ValueError, UnicodeDecodeError):
            return None

    def _read_cursor(self, repo: Path, rel: str) -> str | None:
        """One cursor file's value without the engine's trailing newline, truncated because it is stored and
        reported; ``None`` when absent or unreadable.
        """
        try:
            path = security.resolve_inside(repo, rel)
        except StudioError:
            return None
        text = security.bounded_text(path, _MAX_CURSOR_BYTES)
        if text is None:
            return None
        return text.strip()[:_MAX_CURSOR_REPORT_CHARS] or None

    def _record_run(self, result: EngineResult, *, repo_id: str | None) -> None:
        """Write the ``engine.run`` activity row. Transcripts go to ``evidence``, never to the listed ``params``."""
        record = getattr(self._activity, "record_sync", None)
        if not callable(record):
            return
        record(
            kind="engine.run",
            severity="info" if result.ok else "attention",
            source="studio",
            repo_id=repo_id,
            params={
                "verb_key": result.verb_key,
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "timed_out": result.timed_out,
                "side_effects": list(result.side_effects),
            },
            evidence=[
                {"kind": "studio", "ref": f"engine.{stream}", "detail": text[: C.MAX_TEXT_PREVIEW_BYTES]}
                for stream, text in (("stdout", result.stdout), ("stderr", result.stderr))
                if text
            ],
        )


def _redact_argv(argv: Sequence[str], repo: Path) -> tuple[str, ...]:
    """Repository paths survive (typed and real spelling: ``/tmp`` is ``/private/tmp`` on macOS); every other
    absolute path, such as the interpreter's, becomes ``<path>``.
    """
    inside = (str(repo), str(security.realpath(repo)))
    return tuple(
        security.redact(token if token.startswith(inside) else security.scrub_repo_paths(token, [str(repo)]))
        for token in argv
    )


_ACTIVE_SPACE_REL = "aidlc/active-space"  # absent in real 2.1.1 installs, which means the default space


def _active_intent_rel(space: str) -> str:
    return f"aidlc/spaces/{space}/intents/active-intent"


def probe_bun_version(bun_path: str) -> str | None:
    """``bun --version`` with the scrubbed environment, or ``None``. Here because this is the one module allowed
    to spawn ``bun`` (C35). Sync; call through a worker thread.
    """
    if not bun_path:
        return None
    try:
        done = subprocess.run(  # noqa: S603 - argv list, no shell, fixed arguments
            [bun_path, "--version"], capture_output=True, timeout=C.GIT_TIMEOUT_SECS,
            env=security.build_subprocess_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    text = done.stdout.decode("utf-8", "replace").strip().splitlines()
    return text[0].strip() if text and text[0].strip() else None


# --------------------------------------------------------------------------- #
# finding bun
# --------------------------------------------------------------------------- #

#: How ``find_bun`` found its answer; on the wire in ``/health`` (``tools.bun.source``) and the preflight.
BUN_SOURCE_PATH = "path"
BUN_SOURCE_INSTALL_LOCATION = "install_location"
BUN_SOURCE_EXPLICIT = "explicit"  # the binary was named (owner setting or ``Services.build``); nothing searched
#: Where bun lives when PATH does not name it. ``~`` is expanded at call time; the docs name this exact list.
BUN_CANDIDATE_PATHS: tuple[str, ...] = (
    "~/.bun/bin/bun",                      # bun's own installer
    "/opt/homebrew/bin/bun",               # Homebrew on Apple Silicon
    "/usr/local/bin/bun",                  # Homebrew on Intel macOS, and hand-placed builds
    "/home/linuxbrew/.linuxbrew/bin/bun",  # Linuxbrew, for the Linux target
)


@dataclass(frozen=True, slots=True)
class BunLocation:
    """Which ``bun`` Studio will run, how it was found, and every location tried (what makes "not found"
    actionable)."""

    path: str | None
    version: str | None
    source: str | None
    searched: tuple[str, ...]

    @property
    def found(self) -> bool:
        return self.path is not None


def probe_explicit_bun(
    path: str, *, probe: Callable[[str], str | None] | None = None
) -> BunLocation:
    """Probe exactly the owner-selected executable; never silently choose a different binary."""
    if not isinstance(path, str) or not path or len(path) > 4096 or "\0" in path or not Path(path).is_absolute():
        raise StudioError("bad_path", "bun must be an absolute executable path")
    version = None
    if Path(path).is_file() and os.access(path, os.X_OK):
        try:
            version = (probe or probe_bun_version)(path)
        except Exception:
            version = None
    return BunLocation(path if version else None, version, BUN_SOURCE_EXPLICIT, (path,))


def find_bun(
    *,
    candidates: Sequence[str] | None = None,
    probe: Callable[[str], str | None] | None = None,
) -> BunLocation:
    """The user's PATH first, then ``BUN_CANDIDATE_PATHS``; a location wins only by answering ``--version``.

    The desktop gateway runs under launchd, whose PATH (``/usr/bin:/bin:/usr/sbin:/sbin``) holds no bun.
    ``Path.is_file`` follows symlinks on purpose (Homebrew's ``bin/bun``). ``probe`` lets ``repo_registry`` run
    the probe through its injected door. No environment override: the answer becomes argv[0] of every call.
    """
    prober = probe if probe is not None else probe_bun_version
    order: list[tuple[str, str]] = [
        (str(Path(raw).expanduser()), BUN_SOURCE_INSTALL_LOCATION)
        for raw in (BUN_CANDIDATE_PATHS if candidates is None else candidates)
    ]
    on_path = shutil.which("bun")
    if on_path:
        order.insert(0, (on_path, BUN_SOURCE_PATH))

    searched: list[str] = []
    for path, source in order:
        if path in searched:  # PATH may well name one of the candidates; do not run the same file twice
            continue
        searched.append(path)
        if not Path(path).is_file() or not os.access(path, os.X_OK):
            continue
        try:
            version = prober(path)
        except Exception:  # noqa: BLE001 - a probe that fails means "not this one", never a crash at boot
            version = None
        if version:
            return BunLocation(path=path, version=version, source=source, searched=tuple(searched))
    return BunLocation(path=None, version=None, source=None, searched=tuple(searched))
