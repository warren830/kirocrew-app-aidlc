"""User preferences, and the honest truth about what Studio cannot do.

Two jobs. The second one is the interesting one.

The first is ordinary: a small schema with defaults, a deep merge so the UI can ``PUT`` one key
without re-sending the rest, and validation that names the offending key instead of answering
"invalid".

The second is a product invariant. Several keys in this schema describe features that do not exist in
v1 — the night window and everything downstream of it (the machine lane; S12 is unproven on Kiro,
architecture §3.2), the credit cap (KiroCrew exposes no usage a budget could be checked against,
FR-NIGHT-007), Slack quick actions (C13) and the grouped multi-question answer form (C14/review P18).
The tempting move is to hide those keys. Studio keeps them, reports each one as
``{"available": false, "reason": <code>}`` in the same response, and refuses ``409`` on any attempt to
switch one on. A hidden setting reads as a feature nobody built; a setting that says *why* it is
unavailable is the product's promise about what Studio will never do behind the user's back.

Refusal is defence in depth, not merely an error path: ``night_window.enabled`` and
``night_window.credit_cap`` are forced back to their defaults on every **read** as well, so a
hand-edited ``preferences`` row — or a value stored by some future build and then downgraded — cannot
arm unattended execution. For the same reason a stored override that no longer validates is dropped in
favour of the default instead of being served or raising: a corrupt preference row must not be able to
take ``GET /settings`` down or to smuggle a value past the validator.

Threading (§0.6): ``get``/``put`` are async and do their single SQLite round trip inside
``asyncio.to_thread``; the capability block is assembled on the loop because ``HostBridge`` is
loop-only. The sync accessors other modules call (``global_concurrency_cap``,
``human_text_retention_days``, ``slack``, ``installer``, …) read a cached snapshot and never touch
disk, so a scheduler running on the loop can never block on SQLite behind a worker's transaction. The
price is that ``load()`` must run once at startup (``await asyncio.to_thread(services.settings.load)``,
or any ``get()``): until then those accessors answer with the defaults.
"""

from __future__ import annotations

import asyncio
import copy
import re
import sys
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from . import constants as C
from .errors import StudioError

# --------------------------------------------------------------------------- #
# the schema
# --------------------------------------------------------------------------- #

#: Every preference Studio has, with the value a fresh install runs on (contracts §1.21). Only leaves
#: listed in ``SCHEMA`` below are storable, so this dict and that table are checked against each other
#: by a test rather than by review.
DEFAULTS: dict[str, Any] = {
    "locale": "auto",  # "auto" | "en-US" | "zh-CN"
    "density": "compact",  # "compact" | "comfortable"
    "queue_organize": "priority",  # priority | repo | type | oldest
    "global_concurrency_cap": 2,  # per-repo execution is always 1 (FR-REP-006)
    "slack": {"enabled": False, "muted_repo_ids": []},
    "night_window": {
        "enabled": False,
        "start_local": "22:00",
        "end_local": "06:00",
        "turn_cap": 40,
        "credit_cap": None,
    },
    "diagnostics": {"retention_days": 30, "export_include_human_text": False},
    "human_text_retention_days": 30,
    # ``auto_draft_repo_ids`` is the standing request FR-ADV-001 permits: empty means the Advisor runs
    # only on a click, which is the default on every install. An id here is one repository whose owner
    # has said "draft for me before I get there" — a grant, revocable by unchecking, never retroactive.
    "advisor": {"enabled": True, "auto_draft_repo_ids": []},
    "installer": {"run_doctor_after_install": False},
    "notifications": {"dashboard": True},
}

#: The single ``preferences`` row Studio's settings live in. One row, one write: a partial ``PUT`` that
#: touched several rows could be observed half-applied by a reader between transactions.
PREF_KEY = "settings"

LOCALES = ("auto", "en-US", "zh-CN")
DENSITIES = ("compact", "comfortable")
#: Must equal ``ui/src/lib/types.ts`` ``Organize`` (contracts §3.1).
QUEUE_ORGANIZE = ("priority", "repo", "type", "oldest")
#: Per-repo execution is serialised by lease, so this only bounds how many *different* repositories may
#: run at once. Eight is the point past which the cap stops describing a laptop.
GLOBAL_CONCURRENCY_CAP_RANGE = (1, 8)
#: Both retention knobs are "configurable downward" only (PRD §privacy: default 30 days). Studio must
#: not accept a number that promises to keep history it does not keep.
RETENTION_DAYS_RANGE = (1, C.HUMAN_TEXT_RETENTION_DAYS_MAX)
#: A night's dispatch budget. Inert while the machine lane is unavailable, but a stored value must still
#: be a budget rather than a way to spell "unlimited".
NIGHT_TURN_CAP_RANGE = (1, 1000)
#: ``HH:MM``, 24-hour, local time. Deliberately strict: ``9:00`` and ``22:00:00`` are the two spellings a
#: hand-written config gets wrong, and a lenient parser would store a value the UI cannot round-trip.
CLOCK_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

# --------------------------------------------------------------------------- #
# capability and problem codes (machine-readable; the UI translates them)
# --------------------------------------------------------------------------- #

#: Capability names, in the order the API reports them (contracts §1.21 / §3.1 ``SettingsResponse``).
CAPABILITY_NAMES = (
    "night_window",
    "credit_cap",
    "slack",
    "advisor",
    "slack_quick_actions",
    "grouped_answers",
)

#: Why the night window is off. Note this is NOT ``constants.MACHINE_LANE_REASON`` (``"s12_unproven"``):
#: that string is the *error detail* ``/keep-moving`` and ``MachineActionBroker`` report, while §1.21 and
#: §2.1 pin this one as the capability's reason. Both are codes the UI renders as
#: "Unavailable — S12 unproven"; keeping each where its contract puts it avoids a silent wire change.
REASON_MACHINE_LANE = "machine_lane_unavailable"
#: KiroCrew reports no credit or token usage Studio could compare a budget against (FR-NIGHT-007), so a
#: credit cap would be a budget that is never checked. Turn budgets stay configurable.
REASON_CREDITS = "credits_unobservable"
#: Slack quick actions need a host seam that re-dispatches a click as an authenticated compare-and-submit;
#: the only mechanism that exists sends a bare user turn (C13/review P02).
REASON_HOST_SEAM = "host_seam_unavailable"
#: The grouped ``Q<n>: <answer>`` reply is specified but unverified against the conductor (C14/review P18).
REASON_GROUPED_ANSWERS = "s1_s2_unverified"
#: No ``HostBridge``, or it is not attached to a ``DashboardState`` yet.
REASON_HOST_NOT_ATTACHED = "host_not_attached"
#: The bridge is there but does not report this capability (older host, changed shape). Feature detection
#: failed, which is different from the host telling us the feature is off.
REASON_HOST_UNKNOWN = "host_capability_unknown"
#: The host reports the capability unavailable and gave no reason of its own.
REASON_HOST_UNAVAILABLE = "host_capability_unavailable"

PROBLEM_UNKNOWN_KEY = "unknown_key"
PROBLEM_NOT_ALLOWED = "not_allowed"
PROBLEM_TYPE = "type"
PROBLEM_RANGE = "range"
PROBLEM_FORMAT = "format"
PROBLEM_TOO_MANY = "too_many"


@dataclass(frozen=True, slots=True)
class _Field:
    """One storable leaf: how to check it, and how to explain the check to a human.

    ``locked`` is the interesting kind — the only accepted value is the default, because the feature
    behind the key does not exist. It carries the capability it belongs to so the refusal names the same
    capability the ``GET`` response reports as unavailable.
    """

    kind: str  # enum | bool | int | clock_time | repo_ids | locked
    choices: tuple[str, ...] = ()
    lo: int = 0
    hi: int = 0
    capability: str = ""
    reason: str = ""

    def expected(self) -> str:
        if self.kind == "enum":
            return "one of: " + ", ".join(self.choices)
        if self.kind == "bool":
            return "boolean"
        if self.kind == "int":
            return f"integer {self.lo}..{self.hi}"
        if self.kind == "clock_time":
            return "HH:MM (24-hour local time)"
        if self.kind == "repo_ids":
            return f"list of at most {C.MAX_REPOS} repo ids"
        return "unchangeable while the capability is unavailable"


SCHEMA: dict[str, _Field] = {
    "locale": _Field("enum", choices=LOCALES),
    "density": _Field("enum", choices=DENSITIES),
    "queue_organize": _Field("enum", choices=QUEUE_ORGANIZE),
    "global_concurrency_cap": _Field("int", lo=GLOBAL_CONCURRENCY_CAP_RANGE[0], hi=GLOBAL_CONCURRENCY_CAP_RANGE[1]),
    "slack.enabled": _Field("bool"),
    "slack.muted_repo_ids": _Field("repo_ids"),
    "night_window.enabled": _Field("locked", capability="night_window", reason=REASON_MACHINE_LANE),
    "night_window.start_local": _Field("clock_time"),
    "night_window.end_local": _Field("clock_time"),
    "night_window.turn_cap": _Field("int", lo=NIGHT_TURN_CAP_RANGE[0], hi=NIGHT_TURN_CAP_RANGE[1]),
    "night_window.credit_cap": _Field("locked", capability="credit_cap", reason=REASON_CREDITS),
    "diagnostics.retention_days": _Field("int", lo=RETENTION_DAYS_RANGE[0], hi=RETENTION_DAYS_RANGE[1]),
    "diagnostics.export_include_human_text": _Field("bool"),
    "human_text_retention_days": _Field("int", lo=RETENTION_DAYS_RANGE[0], hi=RETENTION_DAYS_RANGE[1]),
    "advisor.enabled": _Field("bool"),
    "advisor.auto_draft_repo_ids": _Field("repo_ids"),
    "installer.run_doctor_after_install": _Field("bool"),
    "notifications.dashboard": _Field("bool"),
}

#: Dotted paths whose default is a nested object. A patch may address them, and a patch that puts a
#: scalar where one of these lives is a type error rather than an unknown key.
GROUPS: tuple[str, ...] = tuple(sorted(k for k, v in DEFAULTS.items() if isinstance(v, dict)))


# --------------------------------------------------------------------------- #
# pure helpers over the schema
# --------------------------------------------------------------------------- #


def _default_at(dotted: str) -> Any:
    node: Any = DEFAULTS
    for part in dotted.split("."):
        node = node[part]
    return node


def _assign(target: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = target
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _check(dotted: str, field: _Field, value: Any) -> str | None:
    """Reason code the value fails on, or ``None`` when it is storable."""
    if field.kind == "locked":
        # Identity, not equality: `0 == False` and `0.0 == False` in Python, and "night window off"
        # must not be satisfiable by a value that merely compares equal to False.
        return None if value is _default_at(dotted) else field.reason
    if field.kind == "bool":
        return None if value is True or value is False else PROBLEM_TYPE
    if field.kind == "int":
        # bool is an int subclass; accepting True as 1 would let `{"global_concurrency_cap": true}`
        # through and store a boolean where every reader expects a count.
        if not isinstance(value, int) or isinstance(value, bool):
            return PROBLEM_TYPE
        return None if field.lo <= value <= field.hi else PROBLEM_RANGE
    if field.kind == "enum":
        if not isinstance(value, str):
            return PROBLEM_TYPE
        return None if value in field.choices else PROBLEM_NOT_ALLOWED
    if field.kind == "clock_time":
        if not isinstance(value, str):
            return PROBLEM_TYPE
        return None if CLOCK_TIME_RE.match(value) else PROBLEM_FORMAT
    if field.kind == "repo_ids":
        if not isinstance(value, list) or isinstance(value, (str, bytes)):
            return PROBLEM_TYPE
        if any(not isinstance(item, str) or not item for item in value):
            return PROBLEM_TYPE
        return None if len(value) <= C.MAX_REPOS else PROBLEM_TOO_MANY
    raise AssertionError(f"unknown field kind {field.kind!r}")  # pragma: no cover - guarded by test


def _scan(patch: Mapping[str, Any]) -> tuple[list[tuple[str, Any]], list[tuple[str, str]]]:
    """Flatten a (possibly partial, possibly nested) patch into leaves plus ordered problems.

    Traversal order is the caller's key order, so the first problem reported is the first one the user
    would see in their own request body.
    """
    leaves: list[tuple[str, Any]] = []
    problems: list[tuple[str, str]] = []

    def walk(node: Mapping[str, Any], prefix: str) -> None:
        for key, value in node.items():
            name = key if isinstance(key, str) else str(key)
            dotted = f"{prefix}.{name}" if prefix else name
            if not isinstance(key, str):
                problems.append((dotted, PROBLEM_UNKNOWN_KEY))
            elif dotted in SCHEMA:
                reason = _check(dotted, SCHEMA[dotted], value)
                if reason is None:
                    leaves.append((dotted, value))
                else:
                    problems.append((dotted, reason))
            elif dotted in GROUPS:
                if isinstance(value, Mapping):
                    walk(value, dotted)
                else:
                    problems.append((dotted, PROBLEM_TYPE))
            else:
                problems.append((dotted, PROBLEM_UNKNOWN_KEY))

    walk(patch, "")
    return leaves, problems


def _effective(overrides: Mapping[str, Any]) -> dict[str, Any]:
    """``DEFAULTS`` deep-merged with the stored overrides, with the locked keys forced back.

    The forcing is redundant while ``_decode`` drops invalid overrides — and it stays here anyway,
    because this is the function every reader in the process goes through, and "the night window is off"
    must not depend on which path put a value in the dict.
    """
    values = copy.deepcopy(DEFAULTS)
    for dotted in SCHEMA:
        try:
            stored = _lookup(overrides, dotted)
        except KeyError:
            continue
        _assign(values, dotted, copy.deepcopy(stored))
    for dotted, field in SCHEMA.items():
        if field.kind == "locked":
            _assign(values, dotted, _default_at(dotted))
    return values


def _lookup(node: Mapping[str, Any], dotted: str) -> Any:
    cursor: Any = node
    for part in dotted.split("."):
        if not isinstance(cursor, Mapping) or part not in cursor:
            raise KeyError(dotted)
        cursor = cursor[part]
    return cursor


def _decode(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Stored blob → (overrides, updated_at), keeping only leaves that still validate.

    Tolerant on purpose. This row survives app upgrades and downgrades and is trivially hand-editable;
    a shape it does not recognise (or a value a newer build accepted) must degrade to the default, never
    raise on the read path and never be served as if Studio honoured it.
    """
    if not isinstance(raw, Mapping):
        return {}, None
    body = raw.get("values")
    if not isinstance(body, Mapping):
        body = {k: v for k, v in raw.items() if k != "updated_at"}
    leaves, _ = _scan(body)
    overrides: dict[str, Any] = {}
    for dotted, value in leaves:
        _assign(overrides, dotted, copy.deepcopy(value))
    stamp = raw.get("updated_at")
    return overrides, stamp if isinstance(stamp, str) and stamp else None


def _capability(available: bool, reason: str | None) -> dict[str, Any]:
    return {"available": bool(available), "reason": reason}


def _host_version(host: Any) -> str | None:
    """The running gateway's version, or ``None`` when there is no host in this process.

    Read out of ``sys.modules`` rather than imported: this runs on the event loop, and importing a
    package is disk work. In production the gateway has long since imported ``kiro_crew``; when it has
    not, "no host version" is the truthful answer rather than something worth blocking the loop for.
    """
    probe = getattr(host, "host_version", None)
    if callable(probe):  # a future HostBridge accessor wins — it knows the attached host
        try:
            reported = probe()
        except (AttributeError, TypeError, KeyError):
            reported = None
        if isinstance(reported, str) and reported:
            return reported
    module = sys.modules.get("kiro_crew")
    version = getattr(module, "__version__", None)
    return version if isinstance(version, str) and version else None


# --------------------------------------------------------------------------- #
# public shapes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Settings:
    """One coherent answer to ``GET /settings``: the values, what is unavailable, and which versions.

    Capabilities travel with the values on purpose. A UI that fetched them separately could render a
    night-window toggle as enabled for one paint, and a toggle that has to be un-rendered is exactly the
    "Studio might be working unattended" impression the design refuses to give.
    """

    values: dict
    capabilities: dict
    versions: dict
    updated_at: str | None

    def to_json(self) -> dict[str, Any]:
        """The §2.8 ``GET /settings`` body (``settings``, not ``values`` — the wire name is pinned there)."""
        return {
            "settings": copy.deepcopy(self.values),
            "capabilities": copy.deepcopy(self.capabilities),
            "versions": dict(self.versions),
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class _State:
    """The cached snapshot. One frozen object so a sync accessor can never read a half-updated cache."""

    overrides: dict
    values: dict
    updated_at: str | None
    loaded: bool


class SettingsService:
    """Reads, validates and stores the preference schema; reports the rest as unavailable."""

    def __init__(self, storage: Any, host: Any, clock: Any) -> None:
        self._storage = storage
        self._host = host
        self._clock = clock
        # Defaults until `load()` runs, so a sync accessor called during startup answers with a real
        # value instead of raising or reaching for SQLite from the loop thread.
        self._state = _State(overrides={}, values=_effective({}), updated_at=None, loaded=False)
        # `_apply` is read-modify-write; two concurrent PUTs could otherwise drop one of their keys.
        # A threading lock rather than an asyncio one because it is taken and released inside the worker
        # thread, never held across an await — and because a service object must not become bound to the
        # loop that happened to make the first call.
        self._write_lock = threading.Lock()

    # ---- async API ------------------------------------------------------- #

    async def get(self) -> Settings:
        """Current settings. Re-reads the row every time — one indexed lookup, and it keeps the cache
        honest if the database was edited out of band (support does this)."""
        await asyncio.to_thread(self.load)
        return self.snapshot()

    async def put(self, patch: dict) -> Settings:
        """Deep-merge ``patch`` into the stored overrides, or refuse the whole thing.

        All-or-nothing. A patch that both renames a locale and tries to enable the night window stores
        neither: a partial write behind a ``409`` would leave the user unable to tell what Studio kept.
        """
        if not isinstance(patch, Mapping):
            raise StudioError("bad_body", "settings patch must be an object", details={"reason": "not_an_object"})
        leaves, problems = _scan(patch)

        # 409 before 400: "this feature does not exist" is more useful than "that value is not allowed",
        # and it is the answer that stays true no matter what the user types next.
        for key, reason in problems:
            field = SCHEMA.get(key)
            if field is not None and field.kind == "locked":
                raise StudioError(
                    "machine_lane_unavailable",
                    f"{key} cannot be changed while its capability is unavailable",
                    details={"key": key, "capability": field.capability, "reason": reason},
                )
        if problems:
            key, reason = problems[0]
            field = SCHEMA.get(key)
            details: dict[str, Any] = {
                "key": key,
                "reason": reason,
                "problems": [f"{k}: {r}" for k, r in problems],
            }
            if field is not None:
                details["expected"] = field.expected()
            raise StudioError("bad_body", f"invalid settings value for {key}", details=details)

        await asyncio.to_thread(self._apply, leaves)
        return self.snapshot()

    # ---- sync API (worker thread for the two that touch SQLite) ---------- #

    def load(self) -> None:
        """Refresh the cache from storage. **Sync**: call it through ``asyncio.to_thread``.

        Services should run this once at startup: every sync accessor below answers from the cache, so
        without it a scheduler that starts before the first ``GET /settings`` would use the default
        concurrency cap rather than the user's.
        """
        overrides, updated_at = _decode(self._storage.pref_get(PREF_KEY))
        self._install(overrides, updated_at)

    def snapshot(self) -> Settings:
        """Build the public object from the cache. Loop thread only — it reads host capabilities."""
        state = self._state
        return Settings(
            values=copy.deepcopy(state.values),
            capabilities=self.capabilities(),
            versions=self.versions(),
            updated_at=state.updated_at,
        )

    def validate(self, values: dict) -> list[str]:
        """``"<dotted key>: <reason>"`` for everything wrong with ``values``; ``[]`` when it is storable.

        Accepts a full or partial dict, so the same function checks a PUT body and a row read back from
        SQLite. Locked keys report their capability reason (``machine_lane_unavailable``,
        ``credits_unobservable``) rather than a validation reason, because that is what the caller has to
        tell the user.
        """
        if not isinstance(values, Mapping):
            return ["settings: not_an_object"]
        _, problems = _scan(values)
        return [f"{key}: {reason}" for key, reason in problems]

    def capabilities(self) -> dict[str, dict[str, Any]]:
        """What is available, and the code for why not. Loop thread only (it asks ``HostBridge``)."""
        slack_ok, slack_reason = self._host_capability("slack")
        # The advisor's own seam is `ctx.spawn` plus a uniquely named agent, which only AdvisorBroker can
        # check (§1.18). The nearest thing observable from here is the host's subagent manager, so this
        # reports that: available means "the seam exists", and a draft request may still refuse with
        # advisor_unavailable. `advisor.enabled` is a preference, not a capability, and never appears here.
        advisor_ok, advisor_reason = self._host_capability("subagents")
        grouped = bool(C.GROUPED_ANSWERS_VERIFIED)
        return {
            "night_window": _capability(False, REASON_MACHINE_LANE),
            "credit_cap": _capability(False, REASON_CREDITS),
            "slack": _capability(slack_ok, slack_reason),
            "advisor": _capability(advisor_ok, advisor_reason),
            "slack_quick_actions": _capability(False, REASON_HOST_SEAM),
            "grouped_answers": _capability(grouped, None if grouped else REASON_GROUPED_ANSWERS),
        }

    def versions(self) -> dict[str, Any]:
        return {
            "studio": C.APP_VERSION,
            "bundled_engine": C.BUNDLED_ENGINE_VERSION,
            "min_kirocrew": C.MIN_KIROCREW_VERSION,
            "host": _host_version(self._host),
        }

    # ---- sync accessors for other modules (cache only, never disk) ------- #

    def loaded(self) -> bool:
        """False until ``load()`` has run once; the accessors below are serving defaults."""
        return self._state.loaded

    def values(self) -> dict[str, Any]:
        return copy.deepcopy(self._state.values)

    def human_text_retention_days(self) -> int:
        return int(self._state.values["human_text_retention_days"])

    def global_concurrency_cap(self) -> int:
        return int(self._state.values["global_concurrency_cap"])

    def slack(self) -> dict[str, Any]:
        return copy.deepcopy(self._state.values["slack"])

    def installer(self) -> dict[str, Any]:
        return copy.deepcopy(self._state.values["installer"])

    def advisor(self) -> dict[str, Any]:
        return copy.deepcopy(self._state.values["advisor"])

    def diagnostics(self) -> dict[str, Any]:
        return copy.deepcopy(self._state.values["diagnostics"])

    def notifications(self) -> dict[str, Any]:
        return copy.deepcopy(self._state.values["notifications"])

    def locale(self) -> str:
        return str(self._state.values["locale"])

    # ---- internals ------------------------------------------------------- #

    def _apply(self, leaves: Sequence[tuple[str, Any]]) -> None:
        """Re-read, merge, persist and refresh — all in one worker call. **Sync.**

        The re-read is what makes a concurrent writer (another gateway process, an operator with
        ``sqlite3``) lose only their conflicting key instead of the whole row.
        """
        with self._write_lock:
            overrides, updated_at = _decode(self._storage.pref_get(PREF_KEY))
            merged = copy.deepcopy(overrides)
            for dotted, value in leaves:
                _assign(merged, dotted, copy.deepcopy(value))
            if merged != overrides:
                updated_at = self._clock.iso()
                self._storage.pref_set(PREF_KEY, {"values": merged, "updated_at": updated_at})
            self._install(merged, updated_at)

    def _install(self, overrides: dict[str, Any], updated_at: str | None) -> None:
        self._state = _State(
            overrides=overrides,
            values=_effective(overrides),
            updated_at=updated_at,
            loaded=True,
        )

    def _host_capability(self, name: str) -> tuple[bool, str | None]:
        """One host capability, feature-detected exactly like §1.11: a shape mismatch is never a crash."""
        host = self._host
        if host is None:
            return False, REASON_HOST_NOT_ATTACHED
        attached = getattr(host, "attached", None)
        if callable(attached):
            try:
                if attached() is False:
                    return False, REASON_HOST_NOT_ATTACHED
            except (AttributeError, TypeError, KeyError):
                return False, REASON_HOST_NOT_ATTACHED
        try:
            entry = host.capabilities()[name]
        except (AttributeError, TypeError, KeyError, IndexError):
            return False, REASON_HOST_UNKNOWN
        if isinstance(entry, Mapping):
            available = entry.get("available")
            reason = entry.get("reason")
        else:
            available = getattr(entry, "available", None)
            reason = getattr(entry, "reason", None)
        clean = reason if isinstance(reason, str) and reason else None
        if available is True:
            return True, clean
        if available is False:
            return False, clean or REASON_HOST_UNAVAILABLE
        return False, REASON_HOST_UNKNOWN
