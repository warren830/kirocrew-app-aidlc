"""The only door through which Studio may run AI-DLC's own command-line tools.

AI-DLC owns the workflow. Studio observes it and, for a handful of operations the engine itself
provides, asks the engine to do them. That asking is the most dangerous thing this product does: it
starts a subprocess that can rewrite a user's state file, append to a protected audit trail and move a
cursor other sessions read. So the rules are data, not habit, and they are checked at call time.

Five properties this module exists to guarantee:

1. **An argv is a list.** Never a shell string; the one ``subprocess`` call pins ``shell`` to False and
   a static test proves no backend module says otherwise. Every token is either a fixed token from the
   allowlist or a kwarg value that has been matched against a grammar. A value can never begin with
   ``-``, so no value can be re-read as a flag (``--label "--project-dir"`` is the attack this closes).
2. **Two lanes, two tables.** ``ENGINE_READ_VERBS`` may run unattended; ``ENGINE_ADMIN_VERBS`` need an
   explicit user action holding the repository's admin (or execution) lease. The lane is derived from
   ``EngineVerb.lease`` and proved by ``security.assert_engine_argv_allowed`` on the way to every
   ``subprocess.run`` — including from ``build_argv``, so a preview cannot show an argv the runner
   would refuse.
3. **``doctor`` is a user action.** It appends ``HEALTH_CHECKED``/``GUARDRAIL_LOADED`` rows (A12), so
   it is refused without ``_confirmed=True`` and its result carries ``side_effects=("audit",)`` so the
   route and the UI can label it honestly rather than calling it a read.
4. **Verb spelling follows the engine on disk.** ``intent-create`` was called ``intent-birth`` before
   AI-DLC 2.6, and 2.6.2 deliberately dies on the old name (aidlc-utility.ts default arm). The runner
   reads the installed ``AIDLC_VERSION`` and picks the spelling; when it cannot read a version it
   refuses instead of guessing, because guessing wrong on an admin verb means an audit row that says
   the wrong thing.
5. **A cursor switch is only believed after a read-back.** The conductor resolves which intent record
   to write from the repo-shared ``active-intent`` pointer at every step (05 §8), so a decision
   dispatched while the cursor points elsewhere would be committed against the wrong record.
   ``switch_cursor_sync`` therefore re-reads the cursor files, the record's state file, the engine's
   own ``intent --json`` view and the registry uuid, and returns a mismatch list instead of a boolean
   the caller could ignore. A switch that crashed halfway is reported as a mismatch, never assumed to
   have worked. Exactly one of those mismatches is repaired rather than reported: 2.7.1 resolves the
   engine's own ``active`` through the calling session's binding before the cursor, so when every file
   agrees and only that view disagrees the switch is forced once and the second read-back is believed.

Threading: ``run_sync`` and ``switch_cursor_sync`` block on a subprocess and on file reads; callers
reach them through ``asyncio.to_thread`` (§0.6). ``run`` is the async convenience wrapper and does
nothing but that. No thread pool is created here.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from . import constants as C
from . import security
from .errors import StudioError

if TYPE_CHECKING:  # annotations only — importing these at runtime would invert the module layering
    from .activity import ActivityProjector
    from .constants import Clock


# --------------------------------------------------------------------------- #
# the verb table
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EngineVerb:
    """One allowlisted engine invocation, described completely enough to audit without reading code.

    ``argv`` holds the fixed tokens. ``"{name}"`` is a required placeholder filled from a kwarg;
    a ``"?--flag"`` token followed by a placeholder is emitted only when that kwarg is present and
    non-empty. The mutation flags are what the UI labels an operation with, so they are part of the
    contract rather than a comment: a verb that appends audit rows must never be presented as a read.
    """

    key: str
    tool: str
    argv: tuple[str, ...]
    lease: str | None
    mutates_audit: bool
    mutates_state: bool
    mutates_harness: bool
    mutates_session: bool
    json_output: bool
    explicit_user_only: bool
    timeout_secs: float

    @property
    def verb(self) -> str:
        """The engine subcommand — always ``argv[0]``; every allowlist entry starts with its verb."""
        return self.argv[0]

    @property
    def admin(self) -> bool:
        return self.lease == "admin"

    @property
    def side_effects(self) -> tuple[str, ...]:
        codes = []
        if self.mutates_audit:
            codes.append("audit")
        if self.mutates_state:
            codes.append("state")
        if self.mutates_harness:
            codes.append("harness")
        if self.mutates_session:
            codes.append("session")
        return tuple(codes)

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "tool": self.tool,
            "argv": list(self.argv),
            "lease": self.lease,
            "mutates_audit": self.mutates_audit,
            "mutates_state": self.mutates_state,
            "mutates_harness": self.mutates_harness,
            "mutates_session": self.mutates_session,
            "json_output": self.json_output,
            "explicit_user_only": self.explicit_user_only,
            "timeout_secs": self.timeout_secs,
            "side_effects": list(self.side_effects),
        }


def _verb(
    key: str,
    tool: str,
    argv: tuple[str, ...],
    *,
    lease: str | None = None,
    audit: bool = False,
    state: bool = False,
    harness: bool = False,
    session: bool = False,
    json_output: bool = False,
    explicit_user_only: bool = False,
    timeout: float = C.ENGINE_READ_TIMEOUT_SECS,
) -> EngineVerb:
    return EngineVerb(
        key=key,
        tool=tool,
        argv=argv,
        lease=lease,
        mutates_audit=audit,
        mutates_state=state,
        mutates_harness=harness,
        mutates_session=session,
        json_output=json_output,
        explicit_user_only=explicit_user_only,
        timeout_secs=timeout,
    )


_UTILITY = "aidlc-utility.ts"
_STATE = "aidlc-state.ts"

#: The complete set of engine invocations that exist in this product. Adding a key here is the only
#: way to make a new engine call possible, and every entry is checked against
#: ``constants.ENGINE_READ_VERBS``/``ENGINE_ADMIN_VERBS`` at call time, so this table cannot quietly
#: widen the policy the security module enforces.
ENGINE_ALLOWLIST: dict[str, EngineVerb] = {
    # ---- read lane: no lease, no mutation, safe to run unattended ----
    "utility.version": _verb("utility.version", _UTILITY, ("version",)),
    "utility.status": _verb("utility.status", _UTILITY, ("status",)),
    "utility.detect": _verb("utility.detect", _UTILITY, ("detect", "--json"), json_output=True),
    "utility.intent_list": _verb("utility.intent_list", _UTILITY, ("intent", "--json"), json_output=True),
    "utility.space_list": _verb("utility.space_list", _UTILITY, ("space", "--json"), json_output=True),
    "utility.scope_table": _verb("utility.scope_table", _UTILITY, ("scope-table",)),
    "state.get": _verb("state.get", _STATE, ("get", "{field}")),
    "state.lookup": _verb("state.lookup", _STATE, ("lookup", "{query}", "{arg}")),
    "state.resume": _verb("state.resume", _STATE, ("resume",), json_output=True),
    "state.count": _verb("state.count", _STATE, ("count",)),
    # ---- admin lane: explicit user action, lease held, engine-owned mutation ----
    # An unknown intent name makes the engine append ERROR_LOGGED, so callers validate the name
    # against `utility.intent_list` first; the switch itself writes no audit row on success. Both switch
    # verbs also re-stamp the CALLING session: each reads `.current-session`, writes that session's
    # space/intent binding and clears its rebind offer (aidlc-utility.ts:6201-6203 for `intent`,
    # :6258-6259 for `space`). That is per-user state outside the record, so it is declared as its own
    # `session` side effect rather than folded into `state` — neither verb touches `aidlc-state.md`, and
    # a UI that said "writes your state file" about a cursor move would be wrong in the other direction.
    "utility.intent_switch": _verb(
        "utility.intent_switch", _UTILITY, ("intent", "{dir_name}"), lease="admin", session=True
    ),
    "utility.space_switch": _verb(
        "utility.space_switch", _UTILITY, ("space", "{slug}"), lease="admin", harness=True, session=True
    ),
    "utility.space_create": _verb(
        "utility.space_create", _UTILITY, ("space-create", "{slug}"),
        lease="admin", state=True, timeout=C.SUBPROCESS_TIMEOUT_SECS,
    ),
    # 2.7.1 spells the verb `intent-create` (aidlc-utility.ts:8214); `intent-birth` hits the default arm
    # and die()s "renamed to intent-create" (:8323-8329). Flags verified at :8162-8165, and a flag given
    # with a blank value is refused at :302-320 before anything is minted (review R04).
    "utility.intent_create": _verb(
        "utility.intent_create",
        _UTILITY,
        (
            "intent-create",
            "--scope",
            "{scope}",
            "--arguments",
            "{arguments}",
            "--label",
            "{label}",
            "?--depth",
            "{depth}",
            "?--test-strategy",
            "{test_strategy}",
            "?--review",
            "{review}",
            "?--repos",
            "{repos}",
        ),
        lease="admin",
        audit=True,
        state=True,
        timeout=C.SUBPROCESS_TIMEOUT_SECS,
    ),
    "runtime.compile": _verb(
        "runtime.compile", "aidlc-runtime.ts", ("compile",),
        lease="admin", audit=True, json_output=True, timeout=C.SUBPROCESS_TIMEOUT_SECS,
    ),
    # The three mutating verbs below name their record EXPLICITLY. All three resolve the state file
    # through `stateFilePath(projectDir, flags.intent, flags.space)` (aidlc-utility.ts:7322, :7098,
    # :7612), and with those flags omitted the engine picks the record itself — 2.7.1 through
    # `resolveWorkflowSelection`, which prefers the calling session's binding over the cursor. Studio's
    # lease, preview, proposal digest, activity row and event all name the intent the request named, so
    # letting the engine choose a different one would mutate a record nobody was shown. `--intent`
    # alone is not enough: the space would still come from the binding, so the pair always travels
    # together and the caller passes both from the same snapshot.
    "utility.recompose": _verb(
        "utility.recompose",
        _UTILITY,
        (
            "recompose",
            "?--skip",
            "{skip}",
            "?--add",
            "{add}",
            "?--intent",
            "{dir_name}",
            "?--space",
            "{slug}",
        ),
        lease="admin",
        audit=True,
        state=True,
        timeout=C.SUBPROCESS_TIMEOUT_SECS,
    ),
    "utility.scope_change": _verb(
        "utility.scope_change",
        _UTILITY,
        (
            "scope-change",
            "--scope",
            "{scope}",
            "?--depth",
            "{depth}",
            "?--test-strategy",
            "{test_strategy}",
            "?--intent",
            "{dir_name}",
            "?--space",
            "{slug}",
        ),
        lease="admin",
        audit=True,
        state=True,
        timeout=C.SUBPROCESS_TIMEOUT_SECS,
    ),
    "utility.config_change": _verb(
        "utility.config_change",
        _UTILITY,
        (
            "config-change",
            "?--depth",
            "{depth}",
            "?--test-strategy",
            "{test_strategy}",
            "?--intent",
            "{dir_name}",
            "?--space",
            "{slug}",
        ),
        lease="admin",
        audit=True,
        state=True,
        timeout=C.SUBPROCESS_TIMEOUT_SECS,
    ),
    # Read-only for state, but it appends HEALTH_CHECKED/GUARDRAIL_LOADED rows and clears leaked tmp
    # locks (05 §1), so it is an explicit user action and never the reconciler's (A12/C09).
    "utility.doctor": _verb(
        "utility.doctor", _UTILITY, ("doctor",), lease="admin", audit=True, explicit_user_only=True
    ),
}

#: Tools and ``tool:verb`` pairs Studio must never invoke. Kept as data next to the allowlist so one
#: read shows both halves of the policy, and greppable so ``test_engine.py`` can prove no forbidden
#: token appears anywhere else in ``backend/``.
ENGINE_DENY: frozenset[str] = frozenset(
    {
        "aidlc-orchestrate.ts",
        "aidlc-audit.ts",
        "aidlc-log.ts",
        "aidlc-jump.ts",
        "aidlc-graph.ts",
        "aidlc-learnings.ts",
        "aidlc-swarm.ts",
        "aidlc-bolt.ts",
        "aidlc-worktree.ts",
        "aidlc-runtime.ts:fragment-fork",
        "aidlc-runtime.ts:fragment-merge",
        # New in 2.7.1. The positive allowlist already refuses every tool not on it, so these entries
        # are the policy written down rather than the thing that stops them: a future allowlist edit
        # that reached for one of them has to delete a line here first.
        "aidlc-knowledge.ts",
        "aidlc-unit.ts",
        "aidlc-plugin-build.ts",
        "aidlc-plugin-create.ts",
        "aidlc-plugin-emit.ts",
        "aidlc-plugin-test.ts",
        "aidlc-plugin-validate.ts",
        "aidlc-review-brief.ts",
        "aidlc-artifact-resolution.ts",
        "aidlc-artifact-vocabulary.ts",
        "aidlc-documentkb-schema.ts",
        "aidlc-testing-posture.ts",
        "aidlc-validity.ts",
        "aidlc-state.ts:set",
        "aidlc-state.ts:checkbox",
        "aidlc-state.ts:gate-start",
        "aidlc-state.ts:approve",
        "aidlc-state.ts:reject",
        "aidlc-state.ts:revise",
        "aidlc-state.ts:skip",
        "aidlc-state.ts:advance",
        "aidlc-state.ts:finalize",
        "aidlc-state.ts:complete-workflow",
        "aidlc-state.ts:park",
        "aidlc-state.ts:unpark",
        "aidlc-state.ts:fork",
        "aidlc-state.ts:merge",
        "aidlc-state.ts:set-skeleton-stance",
        # Writes `Construction Iteration` into Runtime State (aidlc-state.ts:893). Present since
        # 2.6.2 and simply never written down here, which is the omission this list exists to
        # prevent: `test_every_verb_the_state_tool_dispatches_is_either_allowlisted_or_denied`
        # now derives the denominator from the engine's own verb listing so it cannot recur.
        "aidlc-state.ts:set-construction-iteration",
        "aidlc-state.ts:acknowledge-compaction",
        "aidlc-state.ts:reuse-artifact",
        "aidlc-state.ts:practices-event",
        "aidlc-state.ts:practices-promote",
        # 2.7.1's Unit mutators. `refresh-unit-progress`, `sync-unit-scope-stage` and `fold-unit-merge`
        # are engine-owned transitions (aidlc-state.ts:648-651) that refuse a caller which is not
        # orchestrate; the two setters do not, which is exactly why they are named here.
        "aidlc-state.ts:set-unit-ownership",
        "aidlc-state.ts:set-unit-gate-rhythm",
        "aidlc-state.ts:refresh-unit-progress",
        "aidlc-state.ts:sync-unit-scope-stage",
        "aidlc-state.ts:fold-unit-merge",
        # `unit start|pause|resume|complete` (aidlc-state.ts:1814): rewrites the unit ledger under
        # the audit lock and appends UNIT_STARTED/PAUSED/RESUMED/COMPLETED. A per-unit transition,
        # so it belongs to the engine's orchestrator and never to Studio. Also present in 2.6.2.
        "aidlc-state.ts:unit",
    }
)


# --------------------------------------------------------------------------- #
# kwarg grammar
# --------------------------------------------------------------------------- #

#: A cursor file holds one directory name and a newline. Anything larger is not a cursor, so the read
#: is bounded far below the generic text cap and the value is truncated before it is reported.
_MAX_CURSOR_BYTES = 4096
_MAX_CURSOR_REPORT_CHARS = 128

#: ``--arguments`` carries the human's objective text. Bounded because it becomes one argv token and
#: an audit field; the number is the §1.9 contract value and belongs in ``constants.py`` the next time
#: that module is opened.
_MAX_ARGUMENTS_CHARS = 4000

#: ``aidlc-state.ts lookup`` accepts exactly these queries (§1.9). Anything else is a caller bug, and
#: an unlisted query would reach the engine's own argument parser with an unpredictable effect.
_LOOKUP_QUERIES = (
    "phase-of",
    "next-stage",
    "agent-for",
    "number-of",
    "stages-in-scope",
    "first-in-phase",
    "validate-stage",
    "validate-phase",
)

#: A short configuration word (``Standard``, ``adversarial``, …). The *enumeration* of legal values is
#: ``plan.py``'s product rule; this module's job is only to prove nothing injectable reaches the argv.
_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,31}$")
#: A state-file field name such as ``Current Stage``: letters, digits, spaces and a few separators.
_FIELD_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 ._()/-]{0,63}$")

#: Control kwargs consumed by the runner instead of being filled into an argv. Documented names, so a
#: caller cannot pass one by accident and have it silently become a flag value.
_CONTROL_KWARGS = ("_confirmed", "lease_generation", "repo_id")

#: Canonical verb → the first engine version that speaks each spelling in
#: ``constants.ENGINE_VERB_ALIASES`` (newest first, one floor per spelling except the oldest, which is
#: the fallback). The version arithmetic lives here rather than in ``constants.py`` because choosing a
#: spelling is this module's dispatch rule, not a shared limit.
_ALIAS_FLOORS: dict[str, tuple[tuple[int, ...], ...]] = {"intent-create": ((2, 6),)}


def _reject(name: str, value: str, why: str) -> None:
    raise ValueError(f"engine kwarg {name}={value!r} rejected: {why}")


def _check_match(name: str, value: str, pattern: re.Pattern[str]) -> None:
    if not pattern.match(value):
        _reject(name, value, f"does not match {pattern.pattern}")


def _check_slug_list(name: str, value: str) -> None:
    parts = [p.strip() for p in value.split(",")]
    if not parts or any(not p for p in parts):
        _reject(name, value, "must be a comma-separated list of stage slugs")
    for part in parts:
        _check_match(name, part, C.STAGE_SLUG_RE)


def _check_repo_list(name: str, value: str) -> None:
    parts = [p.strip() for p in value.split(",")]
    if not parts or any(not p for p in parts):
        _reject(name, value, "must be a comma-separated list of repository names")
    for part in parts:
        _check_match(name, part, C.UNIT_NAME_RE)


def _validate_kwarg(name: str, value: str) -> None:
    """Prove one kwarg value is safe to place in an argv, or raise ``ValueError``.

    Two rules apply to every value before its own grammar does. A NUL byte truncates the string the
    moment it crosses into ``execve``, so a value carrying one means the token the engine sees is not
    the token Studio validated. A leading ``-`` would let a value be re-read as an option by the
    engine's own parser, which is how ``--label`` could become ``--project-dir``.
    """
    if "\x00" in value:
        _reject(name, value, "contains a NUL byte")
    if value.startswith("-"):
        _reject(name, value, "must not start with '-' (it would be read as a flag)")
    if name == "dir_name":
        _check_match(name, value, C.INTENT_DIR_RE)
    elif name == "slug":
        _check_match(name, value, C.SPACE_RE)
    elif name == "scope":
        # Scope ids are lowercase-kebab, the same shape as a stage slug — the bundled scope grid keys
        # (poc, feature, security-patch, …) are the evidence, so no second regex is invented for them.
        _check_match(name, value, C.STAGE_SLUG_RE)
    elif name == "label":
        # The engine slugifies the label into the new record's directory name, so the label must
        # satisfy the grammar the directory must satisfy. The "two or three words" guidance is a
        # product rule and lives in plan.py.
        if len(value) > C.MAX_LABEL_CHARS:
            _reject(name, value, f"longer than {C.MAX_LABEL_CHARS} characters")
        _check_match(name, value, C.INTENT_DIR_RE)
    elif name == "arguments":
        # The one value allowed to contain newlines: it is the objective plus optional context.
        if len(value) > _MAX_ARGUMENTS_CHARS:
            _reject(name, value, f"longer than {_MAX_ARGUMENTS_CHARS} characters")
    elif name in ("depth", "test_strategy", "review"):
        _check_match(name, value, _TOKEN_RE)
    elif name == "field":
        _check_match(name, value, _FIELD_NAME_RE)
    elif name == "query":
        if value not in _LOOKUP_QUERIES:
            _reject(name, value, f"not one of {', '.join(_LOOKUP_QUERIES)}")
    elif name == "arg":
        _check_match(name, value, C.STAGE_SLUG_RE)
    elif name in ("skip", "add"):
        _check_slug_list(name, value)
    elif name == "repos":
        _check_repo_list(name, value)
    else:  # pragma: no cover - unreachable: placeholders are drawn from the table above
        _reject(name, value, "has no validator")
    if "\n" in value and name != "arguments":
        _reject(name, value, "contains a newline")


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EngineResult:
    """What one engine invocation did, in a form safe to store, export and render.

    ``argv``, ``stdout`` and ``stderr`` are already capped and redacted: this object is written into
    the activity table and returned by ``POST /repos/{id}/doctor``, so the redaction happens at
    capture rather than at every place it is read. Absolute paths outside the repository (the ``bun``
    interpreter, most obviously) are replaced with ``<path>`` — a diagnostics bundle about one
    repository must not carry the layout of the user's whole disk.

    ``side_effects`` restates the verb's mutation flags on the result so a caller that only has the
    result — a route body, an activity row — can still label a ``doctor`` run as audit-appending.
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
        return {
            "verb_key": self.verb_key,
            "argv": list(self.argv),
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "json": self.json,
            "ok": self.ok,
            "side_effects": list(self.side_effects),
        }


#: Mismatch codes ``switch_cursor_sync`` may report. Fixed vocabulary: the UI renders one localized
#: sentence per code and the action row stores them verbatim.
CURSOR_MISMATCH_CODES = ("active_space", "active_intent", "state_missing", "intent_list_active", "uuid")


def _selection_repair_is_safe(readback: "CursorReadback", listing: "EngineResult") -> bool:
    """True when the only thing that disagrees is the engine's *selection*, not the cursor on disk.

    2.7.1 resolves ``intent --json``'s ``active`` through ``resolveWorkflowSelection`` — an explicit
    selector first, then the CALLING session's binding, then the cursor — so a binding left over from
    earlier work makes the engine name a different active intent while every file Studio can read is
    correct. Studio meets this because AI-DLC's hooks register the gateway's pid under
    ``<repo>/aidlc/.aidlc-sessions/pids/`` when a hosted slot fires them, so the bun child Studio starts
    has a registered ancestor and inherits that session. Refusing the human's decision over it would be
    a dead end (the switch verbs are skipped precisely because the files already match), so this case is
    repaired instead — see ``switch_cursor_sync``.

    Safe means nothing ELSE disagrees: the cursor files name the requested space and intent, the
    record's state file is there, and the registry uuid matches whenever the caller knows one. A parsed
    listing is required too — an engine view that could not be read at all (a corrupt registry) is a
    genuine failure, and re-running a switch verb into it would only invite an ``ERROR_LOGGED`` row.
    """
    return readback.mismatch == ("intent_list_active",) and listing.ok and isinstance(listing.json, Mapping)


@dataclass(frozen=True, slots=True)
class CursorReadback:
    """Where the cursor actually points after a switch, and how that differs from what was asked.

    ``space``/``dir_name``/``uuid`` are the **observed** values, not the requested ones: the caller
    already knows what it asked for, and a mismatch card is only useful if it can say where the cursor
    really is. ``mismatch`` is empty exactly when ``ok`` is True.

    ``to_json`` omits ``results`` on purpose — the frontend type is pinned without it (§3.1), and the
    subprocess transcripts belong in the activity table where they are queryable, not duplicated onto
    every action row.
    """

    ok: bool
    space: str
    dir_name: str
    uuid: str | None
    state_sha256: str | None
    mismatch: tuple[str, ...]
    results: tuple[EngineResult, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "space": self.space,
            "dir_name": self.dir_name,
            "uuid": self.uuid,
            "state_sha256": self.state_sha256,
            "mismatch": list(self.mismatch),
        }


# --------------------------------------------------------------------------- #
# the runner
# --------------------------------------------------------------------------- #


class EngineRunner:
    """Builds, guards and runs engine argvs; nothing else in the backend starts ``bun``."""

    #: How many activity rows may wait for a drain before the oldest are dropped. A bound rather than
    #: an unbounded list because a long-lived sync-only caller must not be able to grow memory.
    _ACTIVITY_BUFFER = 64

    def __init__(
        self,
        *,
        bun_path: str | None,
        clock: "Clock",
        activity: "ActivityProjector | None" = None,
    ) -> None:
        self._bun_path = bun_path
        self._clock = clock
        self._activity = activity
        self._pending: deque[dict[str, Any]] = deque(maxlen=self._ACTIVITY_BUFFER)

    @property
    def bun_path(self) -> str | None:
        return self._bun_path

    def set_bun_path(self, path: str | None) -> None:
        """Apply the container's validated interpreter selection to subsequent invocations."""
        self._bun_path = path

    # ---- argv ----

    def build_argv(self, verb: EngineVerb, repo: Path, engine_dir: str, **kwargs: str) -> list[str]:
        """The exact list that will be executed, or an exception — never a string.

        Order is fixed by the contract: interpreter, absolute path to the tool inside the repository's
        harness, the verb's own tokens, then ``--project-dir <repo>``. ``--project-dir`` is how the
        engine is pointed at a repository; ``cwd`` is set to the same directory so a tool that reads
        the working directory instead agrees with the flag.
        """
        bun_path = self._bun_path
        if not bun_path:
            raise StudioError("bun_missing", "bun is not available; AI-DLC's tools cannot be run")
        repo = Path(repo)
        # Only a trailing slash is trimmed: an absolute ``engine_dir`` must reach ``resolve_inside``
        # still absolute so it is refused, not silently reinterpreted as a directory inside the repo.
        tool_rel = f"{engine_dir.rstrip('/')}/tools/{verb.tool}"
        # Containment is proved against the resolved path; the argv keeps the unresolved spelling so
        # the tool path and --project-dir describe the same repository the user registered (A13).
        security.resolve_inside(repo, tool_rel)
        spelling = self._verb_spelling(verb, repo, engine_dir)
        security.assert_engine_argv_allowed(verb.tool, spelling, admin=verb.admin)
        self._assert_not_denied(verb.tool, spelling)

        filled: list[str] = [spelling]
        supplied = {k: v for k, v in kwargs.items() if k not in _CONTROL_KWARGS}
        used: set[str] = set()
        tokens = list(verb.argv[1:])
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.startswith("?--"):
                flag = token[1:]
                placeholder = tokens[index + 1]
                name = placeholder[1:-1]
                value = supplied.get(name)
                if value is not None and str(value) != "":
                    text = str(value)
                    _validate_kwarg(name, text)
                    filled.extend([flag, text])
                used.add(name)
                index += 2
                continue
            if token.startswith("{") and token.endswith("}"):
                name = token[1:-1]
                value = supplied.get(name)
                if value is None or str(value) == "":
                    raise ValueError(f"engine verb {verb.key} requires kwarg {name!r}")
                text = str(value)
                _validate_kwarg(name, text)
                filled.append(text)
                used.add(name)
                index += 1
                continue
            filled.append(token)
            index += 1

        unknown = sorted(set(supplied) - used)
        if unknown:
            raise ValueError(f"engine verb {verb.key} does not accept kwarg(s) {', '.join(unknown)}")
        return [bun_path, str(repo / tool_rel), *filled, "--project-dir", str(repo)]

    # ---- running ----

    def run_sync(self, verb_key: str, repo: Path, engine_dir: str, **kwargs: str) -> EngineResult:
        """Run one allowlisted verb to completion. Blocking; call through ``asyncio.to_thread``.

        A timeout is a result, not an exception: the caller needs the duration and the partial output
        to decide whether to retry or to surface the repository as unresponsive. A missing interpreter
        and a policy refusal *are* exceptions, because there is nothing to report about a run that
        never legitimately started.
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
        raw_out = b""
        raw_err = b""
        try:
            completed = subprocess.run(  # noqa: S603 - argv list, shell=False, fixed allowlist
                argv,
                cwd=str(repo),
                env=env,
                capture_output=True,
                timeout=verb.timeout_secs,
                shell=False,
                stdin=subprocess.DEVNULL,
                check=False,
            )
            exit_code = completed.returncode
            raw_out = completed.stdout or b""
            raw_err = completed.stderr or b""
        except FileNotFoundError as exc:
            raise StudioError(
                "bun_missing",
                "bun could not be executed",
                details={"detail": str(exc.filename or "")},
            ) from None
        except PermissionError as exc:
            raise StudioError(
                "bun_missing",
                "bun is present but not executable",
                details={"detail": str(exc.filename or "")},
            ) from None
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = None
            raw_out = exc.stdout or b""
            raw_err = exc.stderr or b""

        duration_ms = max(0, int(round((self._clock.monotonic() - started) * 1000)))
        stdout = security.redact(security.truncate_output(raw_out, C.MAX_SUBPROCESS_STDOUT))
        stderr = security.redact(security.truncate_output(raw_err, C.MAX_SUBPROCESS_STDERR))
        parsed = self._parse_json(verb, raw_out)
        result = EngineResult(
            verb_key=verb_key,
            argv=_redact_argv(argv, Path(repo)),
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
            json=parsed,
            ok=(exit_code == 0 and not timed_out),
            side_effects=verb.side_effects,
        )
        self._record_run(result, repo_id=repo_id if isinstance(repo_id, str) else None)
        return result

    async def run(self, verb_key: str, repo: Path, engine_dir: str, **kwargs: str) -> EngineResult:
        """``run_sync`` on a worker thread, plus a drain of any activity rows the sync lane buffered."""
        result = await asyncio.to_thread(self.run_sync, verb_key, repo, engine_dir, **kwargs)
        await self.drain_activity()
        return result

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

        The switch verbs are skipped when the cursor already names this space and intent (there is
        nothing to move, and running the verb anyway risks an ``ERROR_LOGGED`` row for a no-op), but
        the read-back always runs: the caller is about to dispatch a human decision that AI-DLC will
        commit against whatever record the cursor names, so "we asked it to move" is not evidence.

        A failed or timed-out switch does not short-circuit. The read-back runs regardless and reports
        what is actually on disk, which is how a crash halfway through a switch becomes a mismatch
        instead of a false success.

        One mismatch is repairable and is repaired here. When the cursor files, the state file and the
        uuid all agree and the ONLY disagreement is the engine's own ``active`` (2.7.1 resolves it from
        the calling session's binding before the cursor — ``_selection_repair_is_safe``), the switch verb
        is issued once even though the files already match: it re-stamps that binding, exits 0 and writes
        no audit row into the record. Only the second read-back is believed, and it is attempted at most
        once, so a divergence that is real still comes back as a mismatch instead of a retry loop.

        Every path read here is built from the **requested** space and intent, both validated against
        their grammars first. The observed cursor value is repository content and is only ever
        compared as a string — building a path out of it would let a file whose bytes are ``../..``
        aim a read outside the repository.
        """
        repo = Path(repo)
        _validate_kwarg("slug", space)
        _validate_kwarg("dir_name", dir_name)
        results: list[EngineResult] = []
        observed_space = self._read_cursor(repo, _ACTIVE_SPACE_REL) or _DEFAULT_SPACE
        observed_intent = self._read_cursor(repo, _active_intent_rel(space))

        if observed_space != space:
            results.append(
                self.run_sync(
                    "utility.space_switch",
                    repo,
                    engine_dir,
                    slug=space,
                    lease_generation=lease_generation,
                    repo_id=repo_id,
                )
            )
        if observed_space != space or observed_intent != dir_name:
            results.append(
                self.run_sync(
                    "utility.intent_switch",
                    repo,
                    engine_dir,
                    dir_name=dir_name,
                    lease_generation=lease_generation,
                    repo_id=repo_id,
                )
            )

        # ---- read-back ----
        readback, listing = self._read_back_cursor(
            repo, engine_dir, space, dir_name, expected_uuid, repo_id=repo_id
        )
        results.append(listing)

        if _selection_repair_is_safe(readback, listing):
            results.append(
                self.run_sync(
                    "utility.intent_switch",
                    repo,
                    engine_dir,
                    dir_name=dir_name,
                    lease_generation=lease_generation,
                    repo_id=repo_id,
                )
            )
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
        """One pass of "where does the cursor actually point": the files, the record, the engine's view.

        Separate from ``switch_cursor_sync`` because it runs twice on the repair path and must carry no
        memory of the first pass. The returned ``CursorReadback`` has no ``results``; the caller owns
        the transcript and its order, and the listing comes back alongside so the caller can tell an
        engine view that disagreed from one it could not read.
        """
        mismatch: list[str] = []
        final_space = self._read_cursor(repo, _ACTIVE_SPACE_REL) or _DEFAULT_SPACE
        if final_space != space:
            mismatch.append("active_space")
        final_intent = self._read_cursor(repo, _active_intent_rel(space)) or ""
        if final_intent != dir_name:
            mismatch.append("active_intent")

        state_sha256: str | None = None
        state_path = security.resolve_inside(repo, f"{_record_rel(space, dir_name)}/aidlc-state.md")
        if security.is_regular_file(state_path):
            state_sha256 = security.sha256_file(state_path)
        else:
            mismatch.append("state_missing")

        listing = self.run_sync("utility.intent_list", repo, engine_dir, repo_id=repo_id)
        payload = listing.json if isinstance(listing.json, Mapping) else None
        row_uuid: str | None = None
        if payload is None or payload.get("active") != dir_name:
            mismatch.append("intent_list_active")
        if payload is not None:
            rows = payload.get("intents")
            if isinstance(rows, Sequence):
                for row in rows:
                    if isinstance(row, Mapping) and row.get("dirName") == dir_name:
                        value = row.get("uuid")
                        row_uuid = str(value) if value else None
                        break
        if expected_uuid and row_uuid != expected_uuid:
            mismatch.append("uuid")

        return (
            CursorReadback(
                ok=not mismatch,
                space=final_space,
                dir_name=final_intent,
                uuid=row_uuid,
                state_sha256=state_sha256,
                mismatch=tuple(mismatch),
            ),
            listing,
        )

    # ---- activity ----

    async def drain_activity(self) -> int:
        """Hand buffered ``engine.run`` rows to the projector. Loop-thread only.

        ``run_sync`` and ``switch_cursor_sync`` execute on a worker thread and therefore cannot await
        ``ActivityProjector.record``. When the projector offers a synchronous seam the row is written
        immediately; otherwise it waits in a bounded buffer and the loop drains it here (``run`` does
        this for its own callers). Without this seam an install's post-write validation and every
        cursor switch would leave no trace, which is exactly the evidence a failed dispatch needs.
        """
        sink = self._activity
        record = getattr(sink, "record", None) if sink is not None else None
        drained = 0
        while self._pending:
            row = self._pending.popleft()
            if record is None:
                continue
            outcome = record(**row)
            if asyncio.iscoroutine(outcome) or isinstance(outcome, asyncio.Future):
                await outcome
            drained += 1
        return drained

    @property
    def pending_activity(self) -> int:
        """How many rows are waiting for a drain. Read by tests and by ``/diagnostics``."""
        return len(self._pending)

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

    @staticmethod
    def _assert_not_denied(tool: str, verb: str) -> None:
        if tool in ENGINE_DENY or f"{tool}:{verb}" in ENGINE_DENY:
            raise StudioError(
                "internal_error",
                f"{tool} {verb!r} is on the engine deny list",
                details={"tool": tool, "verb": verb},
            )

    def _verb_spelling(self, verb: EngineVerb, repo: Path, engine_dir: str) -> str:
        """The name this repository's engine answers to for ``verb``.

        Only verbs listed in ``ENGINE_VERB_ALIASES`` read the version file, so a repository whose
        harness is unreadable can still be asked its version — refusing everything would make the
        install status of a broken repo unknowable.
        """
        token = verb.verb
        spellings = C.ENGINE_VERB_ALIASES.get(token)
        if not spellings:
            return token
        floors = _ALIAS_FLOORS.get(token)
        if not floors:  # pragma: no cover - guarded by test_engine.py
            raise StudioError(
                "internal_error", f"alias {token!r} has no version floor", details={"verb": token}
            )
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
        """``AIDLC_VERSION`` from the harness on disk, as a comparable tuple.

        Read on every call rather than cached: an upgrade rewrites this file while the process lives,
        and a cached version would pick yesterday's verb spelling for today's engine.
        """
        try:
            path = security.resolve_inside(repo, f"{engine_dir.rstrip('/')}/tools/aidlc-version.ts")
        except StudioError:
            return None
        text = security.bounded_text(path, C.MAX_TEXT_PREVIEW_BYTES)
        if not text:
            return None
        match = C.ENGINE_VERSION_RE.search(text)
        if not match:
            return None
        parts = []
        for chunk in match.group(1).split("."):
            if not chunk.isdigit():
                break
            parts.append(int(chunk))
        return tuple(parts) or None

    @staticmethod
    def _parse_json(verb: EngineVerb, raw: bytes) -> Any | None:
        """Parsed stdout for a ``--json`` verb, or ``None``.

        Parsed from the raw bytes before redaction (redaction can rewrite the inside of a string and
        break the document) and only when the output fits the cap, because a truncated JSON document
        would either fail to parse or, worse, parse into something shorter than the truth.
        """
        if not verb.json_output or not raw or len(raw) > C.MAX_SUBPROCESS_STDOUT:
            return None
        try:
            return security.redact_json(json.loads(raw.decode("utf-8", errors="replace")))
        except (ValueError, UnicodeDecodeError):
            return None

    def _read_cursor(self, repo: Path, rel: str) -> str | None:
        """One cursor file's value, or ``None`` when absent/unreadable.

        The engine writes ``<value>\\n``; the trailing newline is stripped rather than compared,
        because a byte-exact comparison against the requested name would fail on every real
        repository (FORMAT-NOTES §2). The value is truncated before it is returned: it ends up in a
        stored ``CursorReadback`` and in an error body, and a corrupted cursor file must not be able
        to put kilobytes of arbitrary bytes there.
        """
        try:
            path = security.resolve_inside(repo, rel)
        except StudioError:
            return None
        text = security.bounded_text(path, _MAX_CURSOR_BYTES)
        if text is None:
            return None
        value = text.strip()[:_MAX_CURSOR_REPORT_CHARS]
        return value or None

    def _record_run(self, result: EngineResult, *, repo_id: str | None) -> None:
        """Queue (or immediately write) the ``engine.run`` activity row for one invocation.

        stdout/stderr go into ``evidence`` and never into ``params``: params are rendered in the
        activity list, and a 256 KiB transcript there would be unreadable and would bloat every query.
        """
        evidence = []
        if result.stdout:
            evidence.append(
                {"kind": "studio", "ref": "engine.stdout", "detail": result.stdout[: C.MAX_TEXT_PREVIEW_BYTES]}
            )
        if result.stderr:
            evidence.append(
                {"kind": "studio", "ref": "engine.stderr", "detail": result.stderr[: C.MAX_TEXT_PREVIEW_BYTES]}
            )
        row: dict[str, Any] = {
            "kind": "engine.run",
            "severity": "info" if result.ok else "attention",
            "source": "studio",
            "repo_id": repo_id,
            "params": {
                "verb_key": result.verb_key,
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
                "timed_out": result.timed_out,
                "side_effects": list(result.side_effects),
            },
            "evidence": evidence,
        }
        sink = self._activity
        immediate = getattr(sink, "record_sync", None) if sink is not None else None
        if callable(immediate):
            immediate(**row)
            return
        self._pending.append(row)


def _redact_argv(argv: Sequence[str], repo: Path) -> tuple[str, ...]:
    """Redact an argv for storage: repository paths survive, every other absolute path does not.

    Both spellings of the repository root are accepted (the typed one and its realpath) because on
    macOS ``/tmp`` and ``/private/tmp`` are the same directory, and scrubbing the repository's own
    paths would make a stored argv useless for diagnosing which record a verb touched.
    """
    inside = (str(repo), str(security.realpath(repo)))
    out: list[str] = []
    for token in argv:
        if token.startswith(inside):
            out.append(security.redact(token))
        else:
            out.append(security.redact(security.scrub_repo_paths(token, [str(repo)])))
    return tuple(out)


#: Workspace-level cursor paths. Literal here rather than in ``constants.py`` because only this module
#: and ``aidlc_reader`` name them, and the reader owns the general layout helpers.
_ACTIVE_SPACE_REL = "aidlc/active-space"
#: A repository with no ``aidlc/active-space`` file is in the default space (real 2.1.1 installs have
#: none — FORMAT-NOTES §2), so a missing file is a value, not an error.
_DEFAULT_SPACE = C.DEFAULT_SPACE


def _active_intent_rel(space: str) -> str:
    return f"aidlc/spaces/{space}/intents/active-intent"


def _record_rel(space: str, dir_name: str) -> str:
    return f"aidlc/spaces/{space}/intents/{dir_name}"


def probe_bun_version(bun_path: str) -> str | None:
    """``bun --version``, or ``None`` when it cannot be run. Sync; call through a worker thread.

    Lives in this module because this is the only file allowed to spawn ``bun``: a second spawn site
    elsewhere would be a second place where an argv, a cwd and an environment could go wrong, and the
    static policy test that enforces "one door per external tool" would have nothing left to prove.

    The one child process a preflight may start (C35): it names no repository, inherits the scrubbed
    environment, and runs from a directory the candidate repo cannot influence. A non-zero exit is
    "unknown version", never an error — bun's absence is reported by the preflight's own warning.
    """
    if not bun_path:
        return None
    try:
        done = subprocess.run(  # noqa: S603 - argv list, no shell, fixed arguments
            [bun_path, "--version"],
            capture_output=True,
            timeout=C.GIT_TIMEOUT_SECS,
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


#: How ``find_bun`` found what it returned; on the wire in ``/health`` (``tools.bun.source``) and in the
#: add-repo preflight, because "your own PATH has bun" and "Studio looked in the usual places" are two
#: different support conversations.
BUN_SOURCE_PATH = "path"
BUN_SOURCE_INSTALL_LOCATION = "install_location"
#: Studio was told where ``bun`` is (``Services.build(bun_path=...)``) and searched nothing at all.
BUN_SOURCE_EXPLICIT = "explicit"

#: Where to look for ``bun`` when PATH does not name it, in the order a machine is likely to have it.
#: ``~`` is expanded at call time and never at import, so HOME may still change under us (and a test may
#: move it). A module constant because the docs and the "not found" copy must be able to name exactly the
#: list the code walks — a user cannot act on "the usual places" if nobody says which ones.
BUN_CANDIDATE_PATHS: tuple[str, ...] = (
    "~/.bun/bin/bun",                      # bun's own installer, the way bun.sh tells people to install
    "/opt/homebrew/bin/bun",               # Homebrew on Apple Silicon
    "/usr/local/bin/bun",                  # Homebrew on Intel macOS, and hand-placed builds
    "/home/linuxbrew/.linuxbrew/bin/bun",  # Linuxbrew, for the Linux target
)


@dataclass(frozen=True, slots=True)
class BunLocation:
    """Which ``bun`` Studio will run, how it was found, and every location that was tried.

    ``searched`` is the field that makes a refusal actionable. "bun is not installed" is wrong advice for
    the user whose bun is installed twice over and whose gateway simply cannot see it, and the only thing
    the UI can say that is always true is which locations were looked at.
    """

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
    """Find ``bun`` the way a desktop app must: the user's PATH first, then the standard install locations.

    ``shutil.which`` alone was the whole defect, and the reason is not guessable from the code: the
    KiroCrew desktop app is started by launchd, and a launchd child's PATH is literally
    ``/usr/bin:/bin:/usr/sbin:/sbin`` — measured on the running gateway with ``ps eww``. No installer
    puts ``bun`` in any of those four directories, so a machine carrying two working bun installs
    reported ``bun: found=false`` and an Install rolled back with ``bun_missing``. That refusal was
    right (post-install validation runs an engine verb, and Studio must not claim it verified what it
    wrote when it could not), but the premise was false. A gateway started from a shell inherits the
    user's own PATH, so PATH is searched first and that case keeps behaving exactly as it did.

    A location wins only by running: an executable file whose ``--version`` prints something. An
    unexecutable stub, a dangling symlink, a directory named ``bun`` or a binary for the wrong
    architecture therefore loses instead of becoming argv[0] of every engine invocation. ``Path.is_file``
    follows symlinks on purpose where ``security.is_regular_file`` would not: Homebrew's ``bin/bun`` is a
    symlink into ``Cellar``, which is the most likely install on a mac.

    ``probe`` is the seam ``repo_registry`` needs. That module must start no child process of its own
    (C35, and the one-door static test), so it passes the prober its container injected and the execution
    happens at the caller's door instead. Left at ``None`` the probe is this module's own
    ``probe_bun_version`` — the finder never reports a candidate it could not run.

    There is deliberately no environment-variable override. The returned path becomes argv[0] of every
    engine invocation, and this app scrubs ``AIDLC_*``/``KIROCREW*`` out of every child environment, so
    letting an environment variable name the command Studio runs is a security decision rather than a
    bug fix, and belongs in its own change.
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
