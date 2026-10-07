"""User preferences: defaults, a deep-merged partial ``PUT``, and validation that names the offending key.

Capabilities travel with the values. A feature Studio cannot offer is reported as
``{"available": false, "reason": <code>}`` in the same response — Slack quick actions (C13) above all —
because a hidden setting reads as a feature nobody built, and a toggle that has to be un-rendered after a
second fetch reads as one that might work.

The whole document lives in one ``preferences`` row (``PREF_KEY``), so a partial ``PUT`` is one write that
no reader can observe half-applied. A stored leaf that no longer validates — the retired v1
``night_window`` keys, a value a newer build accepted, a hand edit — is dropped on read in favour of the
default: a corrupt row must not take ``GET /settings`` down or smuggle a value past the validator.

The sync accessors other modules call read a cached snapshot and never touch disk, so a scheduler on the
loop never blocks behind a worker's transaction; ``load()`` runs once at startup, and until then they
answer with the defaults.
"""

from __future__ import annotations

import asyncio
import copy
import sys
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from . import constants as C
from .errors import StudioError

#: Every preference with the value a fresh install runs on (contracts §1.21). Only leaves listed in
#: ``SCHEMA`` are storable; a test checks the two tables against each other.
DEFAULTS: dict[str, Any] = {
    "locale": "auto",  # "auto" | "en-US" | "zh-CN"
    "density": "compact",  # "compact" | "comfortable"
    "queue_organize": "priority",  # priority | repo | type | oldest
    "global_concurrency_cap": 2,  # per-repo execution is always 1 (FR-REP-006)
    "slack": {"enabled": False, "muted_repo_ids": []},
    "diagnostics": {"retention_days": 30, "export_include_human_text": False},
    "human_text_retention_days": C.HUMAN_TEXT_RETENTION_DAYS_DEFAULT,
    # The standing grant FR-ADV-001 permits: empty means the Advisor runs only on a click. An id here is one
    # repository whose owner asked for drafts ahead of time — revocable by unchecking, never retroactive.
    "advisor": {"enabled": True, "auto_draft_repo_ids": []},
    "installer": {"run_doctor_after_install": False},
    "notifications": {"dashboard": True},
}

PREF_KEY = "settings"

#: Both retention knobs only go downward from the PRD's 30 days: Studio must not accept a number that
#: promises to keep history it does not keep.
RETENTION_DAYS_RANGE = (1, C.HUMAN_TEXT_RETENTION_DAYS_MAX)

#: Capability names, in the order the API reports them (contracts §1.21 / §3.1 ``SettingsResponse``).
CAPABILITY_NAMES = ("slack", "advisor", "slack_quick_actions", "grouped_answers")
#: Slack quick actions need a host seam that re-dispatches a click as an authenticated compare-and-submit;
#: the only mechanism that exists sends a bare user turn (C13/review P02).
REASON_HOST_SEAM = "host_seam_unavailable"
#: The grouped ``Q<n>: <answer>`` reply, until ``constants.GROUPED_ANSWERS_VERIFIED`` says otherwise.
REASON_GROUPED_ANSWERS = "s1_s2_unverified"
REASON_HOST_NOT_ATTACHED = "host_not_attached"
#: Feature detection failed (older host, changed shape) — not the host saying the feature is off.
REASON_HOST_UNKNOWN = "host_capability_unknown"
REASON_HOST_UNAVAILABLE = "host_capability_unavailable"

PROBLEM_UNKNOWN_KEY = "unknown_key"
PROBLEM_NOT_ALLOWED = "not_allowed"
PROBLEM_TYPE = "type"
PROBLEM_RANGE = "range"
PROBLEM_TOO_MANY = "too_many"


@dataclass(frozen=True, slots=True)
class _Field:
    """One storable leaf: how to check it, and how to explain the check to a human."""

    kind: str  # enum | bool | int | repo_ids
    choices: tuple[str, ...] = ()
    lo: int = 0
    hi: int = 0

    def expected(self) -> str:
        if self.kind == "enum":
            return "one of: " + ", ".join(self.choices)
        if self.kind == "int":
            return f"integer {self.lo}..{self.hi}"
        if self.kind == "repo_ids":
            return f"list of at most {C.MAX_REPOS} repo ids"
        return "boolean"


_DAYS = _Field("int", lo=RETENTION_DAYS_RANGE[0], hi=RETENTION_DAYS_RANGE[1])

SCHEMA: dict[str, _Field] = {
    "locale": _Field("enum", choices=("auto", "en-US", "zh-CN")),
    "density": _Field("enum", choices=("compact", "comfortable")),
    # Must equal ``ui/src/lib/types.ts`` ``Organize`` (contracts §3.1).
    "queue_organize": _Field("enum", choices=("priority", "repo", "type", "oldest")),
    # Per-repo execution is serialised by lease, so this bounds how many *different* repositories run at once.
    "global_concurrency_cap": _Field("int", lo=1, hi=8),
    "slack.enabled": _Field("bool"),
    "slack.muted_repo_ids": _Field("repo_ids"),
    "diagnostics.retention_days": _DAYS,
    "diagnostics.export_include_human_text": _Field("bool"),
    "human_text_retention_days": _DAYS,
    "advisor.enabled": _Field("bool"),
    "advisor.auto_draft_repo_ids": _Field("repo_ids"),
    "installer.run_doctor_after_install": _Field("bool"),
    "notifications.dashboard": _Field("bool"),
}

#: Dotted paths whose default is an object: a patch may address them, and a scalar there is a type error.
GROUPS: tuple[str, ...] = tuple(sorted(k for k, v in DEFAULTS.items() if isinstance(v, dict)))


def _assign(target: dict, dotted: str, value: Any) -> None:
    *parents, leaf = dotted.split(".")
    node = target
    for part in parents:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[leaf] = value


def _check(field: _Field, value: Any) -> str | None:
    """The reason ``value`` is not storable, or ``None``."""
    if field.kind == "bool":
        return None if value is True or value is False else PROBLEM_TYPE
    if field.kind == "int":
        # bool is an int subclass; accepting True as 1 would store a boolean where readers expect a count.
        if not isinstance(value, int) or isinstance(value, bool):
            return PROBLEM_TYPE
        return None if field.lo <= value <= field.hi else PROBLEM_RANGE
    if field.kind == "enum":
        if not isinstance(value, str):
            return PROBLEM_TYPE
        return None if value in field.choices else PROBLEM_NOT_ALLOWED
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        return PROBLEM_TYPE
    return None if len(value) <= C.MAX_REPOS else PROBLEM_TOO_MANY


def _scan(patch: Mapping[str, Any]) -> tuple[list[tuple[str, Any]], list[tuple[str, str]]]:
    """A (possibly partial, possibly nested) patch as storable leaves plus problems, in request order."""
    leaves: list[tuple[str, Any]] = []
    problems: list[tuple[str, str]] = []

    def walk(node: Mapping[str, Any], prefix: str) -> None:
        for key, value in node.items():
            dotted = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(key, str) and dotted in SCHEMA:
                reason = _check(SCHEMA[dotted], value)
                if reason is None:
                    leaves.append((dotted, value))
                else:
                    problems.append((dotted, reason))
            elif isinstance(key, str) and dotted in GROUPS:
                if isinstance(value, Mapping):
                    walk(value, dotted)
                else:
                    problems.append((dotted, PROBLEM_TYPE))
            else:
                problems.append((dotted, PROBLEM_UNKNOWN_KEY))

    walk(patch, "")
    return leaves, problems


def _merged(base: Mapping[str, Any], leaves: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    out = copy.deepcopy(dict(base))
    for dotted, value in leaves:
        _assign(out, dotted, copy.deepcopy(value))
    return out


def _effective(overrides: Mapping[str, Any]) -> dict[str, Any]:
    return _merged(DEFAULTS, _scan(overrides)[0])


def _decode(raw: Any) -> tuple[dict[str, Any], str | None]:
    """Stored row -> (valid overrides, updated_at). Tolerant: anything unrecognised degrades to the default."""
    if not isinstance(raw, Mapping):
        return {}, None
    body = raw.get("values")
    if not isinstance(body, Mapping):
        body = {k: v for k, v in raw.items() if k != "updated_at"}
    stamp = raw.get("updated_at")
    return _merged({}, _scan(body)[0]), stamp if isinstance(stamp, str) and stamp else None


@dataclass(frozen=True, slots=True)
class Settings:
    """One coherent answer to ``GET /settings``: the values, what is unavailable, and which versions."""

    values: dict
    capabilities: dict
    versions: dict
    updated_at: str | None

    def to_json(self) -> dict[str, Any]:
        """The §2.8 body (``settings``, not ``values`` — the wire name is pinned there)."""
        return {
            "settings": copy.deepcopy(self.values),
            "capabilities": copy.deepcopy(self.capabilities),
            "versions": dict(self.versions),
            "updated_at": self.updated_at,
        }


class SettingsService:
    """Reads, validates and stores the preference schema; reports what the host cannot do."""

    def __init__(self, storage: Any, host: Any, clock: Any) -> None:
        self._storage = storage
        self._host = host
        self._clock = clock
        #: ``(values, updated_at)``, replaced whole so a sync reader never sees a half-updated cache.
        self._cache: tuple[dict[str, Any], str | None] = (_effective({}), None)
        # `_apply` is read-modify-write. A threading lock, taken inside the worker thread and never across an
        # await, so the service is not bound to whichever loop made the first call.
        self._write_lock = threading.Lock()

    async def get(self) -> Settings:
        """Re-reads the row every time (one indexed lookup), so an out-of-band edit is honoured."""
        await asyncio.to_thread(self.load)
        return self.snapshot()

    async def put(self, patch: dict) -> Settings:
        """Deep-merge ``patch`` into the stored overrides, or store none of it.

        All-or-nothing: a partial write behind a 400 would leave the user unable to tell what Studio kept.
        """
        if not isinstance(patch, Mapping):
            raise StudioError("bad_body", "settings patch must be an object", details={"reason": "not_an_object"})
        leaves, problems = _scan(patch)
        if problems:
            key, reason = problems[0]
            details: dict[str, Any] = {"key": key, "reason": reason, "problems": [f"{k}: {r}" for k, r in problems]}
            if key in SCHEMA:
                details["expected"] = SCHEMA[key].expected()
            raise StudioError("bad_body", f"invalid settings value for {key}", details=details)
        await asyncio.to_thread(self._apply, leaves)
        return self.snapshot()

    def load(self) -> None:
        """Refresh the cache from storage. **Sync**: call it through ``asyncio.to_thread``."""
        overrides, updated_at = _decode(self._storage.pref_get(PREF_KEY))
        self._cache = (_effective(overrides), updated_at)

    def snapshot(self) -> Settings:
        """The public object from the cache. Loop thread only — it reads host capabilities."""
        values, updated_at = self._cache
        return Settings(copy.deepcopy(values), self.capabilities(), self.versions(), updated_at)

    def validate(self, values: dict) -> list[str]:
        """``"<dotted key>: <reason>"`` for everything wrong with a full or partial ``values``; ``[]`` if storable."""
        if not isinstance(values, Mapping):
            return ["settings: not_an_object"]
        return [f"{key}: {reason}" for key, reason in _scan(values)[1]]

    def capabilities(self) -> dict[str, dict[str, Any]]:
        """What is available, and the code for why not. Loop thread only (it asks ``HostBridge``)."""
        slack_ok, slack_reason = self._host_capability("slack")
        # The advisor's own seam (`ctx.spawn` plus a uniquely named agent) is AdvisorBroker's to check; the
        # nearest thing observable here is the host's subagent manager. `advisor.enabled` is a preference.
        advisor_ok, advisor_reason = self._host_capability("subagents")
        grouped = bool(C.GROUPED_ANSWERS_VERIFIED)
        return {
            "slack": {"available": slack_ok, "reason": slack_reason},
            "advisor": {"available": advisor_ok, "reason": advisor_reason},
            "slack_quick_actions": {"available": False, "reason": REASON_HOST_SEAM},
            "grouped_answers": {"available": grouped, "reason": None if grouped else REASON_GROUPED_ANSWERS},
        }

    def versions(self) -> dict[str, Any]:
        # Read from `sys.modules`, not imported: this runs on the loop, and an import is disk work.
        host = getattr(sys.modules.get("kiro_crew"), "__version__", None)
        return {
            "studio": C.APP_VERSION,
            "bundled_engine": C.BUNDLED_ENGINE_VERSION,
            "min_kirocrew": C.MIN_KIROCREW_VERSION,
            "host": host if isinstance(host, str) and host else None,
        }

    # ---- sync accessors for other modules (cache only, never disk) ----

    def values(self) -> dict[str, Any]:
        return copy.deepcopy(self._cache[0])

    def human_text_retention_days(self) -> int:
        return int(self._cache[0]["human_text_retention_days"])

    def global_concurrency_cap(self) -> int:
        return int(self._cache[0]["global_concurrency_cap"])

    def locale(self) -> str:
        return str(self._cache[0]["locale"])

    def slack(self) -> dict[str, Any]:
        return copy.deepcopy(self._cache[0]["slack"])

    def installer(self) -> dict[str, Any]:
        return copy.deepcopy(self._cache[0]["installer"])

    def advisor(self) -> dict[str, Any]:
        return copy.deepcopy(self._cache[0]["advisor"])

    def diagnostics(self) -> dict[str, Any]:
        return copy.deepcopy(self._cache[0]["diagnostics"])

    def notifications(self) -> dict[str, Any]:
        return copy.deepcopy(self._cache[0]["notifications"])

    # ---- internals ----

    def _apply(self, leaves: Sequence[tuple[str, Any]]) -> None:
        """Re-read, merge, persist, refresh — one worker call. The re-read means a concurrent writer (another
        process, an operator with ``sqlite3``) loses only a conflicting key, never the whole row."""
        with self._write_lock:
            overrides, updated_at = _decode(self._storage.pref_get(PREF_KEY))
            merged = _merged(overrides, leaves)
            if merged != overrides:
                updated_at = self._clock.iso()
                self._storage.pref_set(PREF_KEY, {"values": merged, "updated_at": updated_at})
            self._cache = (_effective(merged), updated_at)

    def _host_capability(self, name: str) -> tuple[bool, str | None]:
        """One host capability, feature-detected like §1.11: a shape mismatch is an answer, never a crash."""
        host = self._host
        attached = getattr(host, "attached", None)
        try:
            if host is None or (callable(attached) and attached() is False):
                return False, REASON_HOST_NOT_ATTACHED
        except (AttributeError, TypeError, KeyError):
            return False, REASON_HOST_NOT_ATTACHED
        try:
            entry = host.capabilities()[name]
        except (AttributeError, TypeError, KeyError, IndexError):
            return False, REASON_HOST_UNKNOWN
        get = entry.get if isinstance(entry, Mapping) else lambda key: getattr(entry, key, None)
        available, reason = get("available"), get("reason")
        reason = reason if isinstance(reason, str) and reason else None
        if available is True:
            return True, reason
        if available is False:
            return False, reason or REASON_HOST_UNAVAILABLE
        return False, REASON_HOST_UNKNOWN
