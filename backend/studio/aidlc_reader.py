"""Everything Studio knows about an AI-DLC workspace, read from the bytes AI-DLC wrote.

The product's eyes: a confident misread becomes a confident wrong card, and a card sends a prompt into
someone's session. Four rules, each forced by a real fixture (FORMAT-NOTES §11.1): tolerate the writer
but never guess for it; two reads or no authority (``stable_read``); every path contained and every read
capped; read only. Synchronous and blocking: callers use ``asyncio.to_thread`` or the reconciler's pool.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import os
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from . import constants as C
from . import security
from .errors import StudioError
from .wire import WireRecord

if TYPE_CHECKING:  # annotations only — the reader must stay importable on its own
    from .constants import Clock
    from .repo_registry import HarnessDir

# ---- on-disk names (the engine's own layout, 04 §2/§3) ---------------------- #

WORKSPACE_DIRNAME = "aidlc"
SPACES_DIRNAME = "spaces"
INTENTS_DIRNAME = "intents"
#: Re-exported from ``constants`` so the layout names still read as one block here.
DEFAULT_SPACE = C.DEFAULT_SPACE
ACTIVE_SPACE_REL = f"{WORKSPACE_DIRNAME}/active-space"
ACTIVE_INTENT_FILENAME = "active-intent"
INTENTS_REGISTRY_FILENAME = "intents.json"
STATE_FILENAME = "aidlc-state.md"
AUDIT_DIRNAME = "audit"
DIRECTIVE_FILENAME = ".aidlc-active-directive.json"
#: Marker versions ``parse_directive`` trusts: 2.6.2 writes 1, 2.7.1 writes 2 and keeps an existing 1.
DIRECTIVE_VERSIONS = frozenset({1, 2})
#: The ``kind`` values a version-2 marker may carry; any other shape names no running stage.
DIRECTIVE_KINDS = frozenset({"run-stage", "load-steering", "invoke-swarm"})
RECOVERY_FILENAME = ".aidlc-recovery.md"
GOAL_STOP_FILENAME = ".aidlc-goal-stop"
HUMAN_TURN_FILENAME = ".aidlc-human-turn"
ENGINE_TOUCH_FILENAME = ".aidlc-engine-touch"
HOOKS_HEALTH_DIRNAME = ".aidlc-hooks-health"
HOOKS_HEALTH_SUFFIX = ".last"
STOP_HOOK_REL = ".aidlc-stop-hook/block-count.json"
REVIEWER_DISPATCH_FILENAME = ".aidlc-reviewer-dispatch.json"
TURN_COUNTER_REL = f"{WORKSPACE_DIRNAME}/.aidlc-turn-counter"
#: AI-DLC 2.9+ keeps a record's engine bookkeeping under ``<record>/.aidlc-engine/`` and reads the
#: legacy record-root names only as a fallback. Keyed by the legacy first path component.
ENGINE_DIRNAME = ".aidlc-engine"
ENGINE_RECORD_NAMES: dict[str, str] = {
    DIRECTIVE_FILENAME: "active-directive.json",
    RECOVERY_FILENAME: "recovery.md",
    HUMAN_TURN_FILENAME: "human-turn",
    ENGINE_TOUCH_FILENAME: "engine-touch",
    HOOKS_HEALTH_DIRNAME: "hooks-health",
    ".aidlc-stop-hook": "stop-hook",
    REVIEWER_DISPATCH_FILENAME: "reviewer-dispatch.json",
    ".aidlc-sensors": "sensors",
}
#: Legacy names the engine still falls back to; the rest are pre-2.9 leftovers (FORMAT-NOTES §11.2).
ENGINE_LEGACY_READS = frozenset({DIRECTIVE_FILENAME, HOOKS_HEALTH_DIRNAME, ".aidlc-sensors"})


def record_file(repo: Path, record: str, legacy_rel: str) -> Path:
    """Where the engine reads ``legacy_rel`` for this record, by its own rule (``engineReadDirFor``).

    The ``.aidlc-engine`` copy wins unless it is absent and the legacy one exists (FORMAT-NOTES §11.2).
    """
    head, _, rest = legacy_rel.partition("/")
    current_rel = f"{record}/{ENGINE_DIRNAME}/{ENGINE_RECORD_NAMES[head]}" + (f"/{rest}" if rest else "")
    current = security.resolve_inside(repo, current_rel)
    legacy = security.resolve_inside(repo, f"{record}/{legacy_rel}")
    try:
        engine = security.resolve_inside(repo, f"{record}/{ENGINE_DIRNAME}")
        if os.path.lexists(engine) and (not engine.is_dir() or head not in ENGINE_LEGACY_READS):
            return current
        if not os.path.lexists(current) and os.path.lexists(legacy):
            return legacy
    except (OSError, StudioError):
        pass
    return current


COMPOSE_PENDING_REL = f"{WORKSPACE_DIRNAME}/.aidlc-compose-pending"
TOOLS_DIRNAME = "tools"
TOOLS_DATA_DIRNAME = "data"
STAGE_GRAPH_REL = f"{TOOLS_DIRNAME}/{TOOLS_DATA_DIRNAME}/stage-graph.json"
SCOPE_GRID_REL = f"{TOOLS_DIRNAME}/{TOOLS_DATA_DIRNAME}/scope-grid.json"
#: Names inside a ``<harness>/tools`` directory.
HARNESS_JSON_NAME = f"{TOOLS_DATA_DIRNAME}/harness.json"
STAGE_GRAPH_NAME = f"{TOOLS_DATA_DIRNAME}/stage-graph.json"
ENGINE_VERSION_NAME = "aidlc-version.ts"
ENGINE_LIB_NAME = "aidlc-lib.ts"
ENGINE_UTILITY_NAME = "aidlc-utility.ts"
SCOPES_DIRNAME = "scopes"
SCOPE_FILE_PREFIX = "aidlc-"
STAGES_REL = "aidlc-common/stages"
QUESTIONS_SUFFIX = "-questions.md"

#: Build manifests whose *presence* says what kind of project this is (FR-ADV-003); never opened.
WORKSPACE_MANIFESTS: tuple[str, ...] = (
    "package.json", "pyproject.toml", "setup.py", "requirements.txt", "go.mod", "Cargo.toml", "pom.xml",
    "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "Makefile", "Dockerfile",
    "docker-compose.yml", "CMakeLists.txt", "mix.exs", "pubspec.yaml", "tsconfig.json",
)
#: The one file ``read_workspace_signals`` reads an excerpt of, first hit wins.
README_NAMES: tuple[str, ...] = ("README.md", "README", "readme.md", "README.rst", "README.txt")
#: Files that make ``<harness>/tools`` an engine tree, not a user's own folder (``repo_registry`` mirrors it).
HARNESS_MARKERS = (ENGINE_VERSION_NAME, HARNESS_JSON_NAME, ENGINE_UTILITY_NAME)
#: The eleven scopes AI-DLC ships; any other ``aidlc-<name>.md`` is ``project_owned`` (FORMAT-NOTES §11.7).
STOCK_SCOPES = frozenset({
    "bugfix", "classic", "enterprise", "express", "feature", "infra", "mvp", "poc", "refactor",
    "security-patch", "workshop",
})
#: Construction slugs every engine knows: unit detection's fallback without a state file (FORMAT-NOTES §8).
CONSTRUCTION_STAGE_SLUGS = frozenset(set(C.PER_UNIT_STAGES) | {"build-and-test", "ci-pipeline"})
#: Above this many files a record listing stops hashing; no card evidence comes from it (FORMAT-NOTES §11.10).
WHOLE_RECORD_HASH_LIMIT = 200
#: How much of the newest audit shard the presence baseline digests: one page covers a turn (§1.6.4/C23).
PRESENCE_TAIL_BYTES = 4096
#: Extensions rendered as text; anything else is metadata only, so a binary is never splatted into the UI.
TEXT_EXTENSIONS = frozenset({
    ".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".csv", ".tsv",
    ".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs", ".java", ".rb", ".sh", ".sql", ".html",
    ".css", ".xml", ".mmd", ".puml", ".log", ".diff", ".patch",
})

#: ``- **State Version**: 7`` in a state file or a pre-2.5 engine's state template (shared with ``repo_registry``).
_TEMPLATE_STATE_VERSION_RE = C.TEMPLATE_STATE_VERSION_RE
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
#: ``**Verdict:** READY`` / ``**Verdict**: READY`` / ``Verdict: NOT-READY`` all occur in real artifacts.
_VERDICT_RE = re.compile(r"^\s*\*{0,2}Verdict\*{0,2}\s*:\s*\*{0,2}\s*(NOT-READY|READY)\b", re.IGNORECASE)
_REVIEW_HEADING_RE = re.compile(r"^##\s+Review\s*$", re.IGNORECASE)
_H2_RE = re.compile(r"^##(?!#)\s*(.*?)\s*$")
_QUESTION_H3_RE = re.compile(r"^### Q(?:uestion)?[ \t]*(\d+)(?:[.:]|[ \t]+[—–-])?[ \t]+(.*)$")
_QUESTION_SECTION_END_RE = re.compile(r"^#{1,6}(?:[ \t]+|$)")
_QUESTION_CODE_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_QUESTION_MULTI_SELECT_RE = re.compile(
    r"[(（\[]\s*(?:(?:multi[- ]select|多选|可多选)\s*|"
    r"select\s+all\b[^()\[\]（）\r\n]*)[)）\]]",
    re.IGNORECASE,
)
_QUESTION_OPTION_LIKE_RE = re.compile(r"^\s*(?:[-*]\s+)?[A-Z][.):：]\s+\S")
_H3_RE = re.compile(r"^###+\s*(.*?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*?)\s*$")
_QUOTED_RE = re.compile(r"[`\"“]([^`\"”]{3,})[`\"”]")
_REVIEWER_FIELD_RE = re.compile(r"^\s*\*{0,2}Reviewer\*{0,2}\s*:\s*\*{0,2}\s*(.+?)\s*\*{0,2}\s*$", re.IGNORECASE)
_ITERATION_FIELD_RE = re.compile(r"^\s*\*{0,2}Iteration\*{0,2}\s*:\s*\*{0,2}\s*(\d+)", re.IGNORECASE)
_ACCEPTANCE_HEADING_RE = re.compile(r"^##+\s*(Acceptance|Definition of Done|Exit criteria)", re.IGNORECASE)
#: Review-body table rows and ``|---|:--:|`` rule cells: findings are the rows below it (FORMAT-NOTES §11.9).
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_RULE_CELL_RE = re.compile(r"^:?-+:?$")
#: A table is findings only under a findings heading; the second set names something else (FORMAT-NOTES §11.9).
_FINDINGS_HEADING_RE = re.compile(r"^findings?\b", re.IGNORECASE)
_NON_FINDINGS_HEADING_RE = re.compile(r"^(summary|validation|verdict|recommendation)", re.IGNORECASE)
_FRONTMATTER_FENCE = "---"
_PER_UNIT_STAGE_SET = frozenset(C.PER_UNIT_STAGES)
#: Review bullet groups → ``ReviewFinding.level``. Prefix match, case-insensitive (§1.6.2).
_REVIEW_LEVELS = (("blocker", "blocker"), ("advisory", "advisory"), ("resolved", "resolved"))

_PHASE_SECTION = "Phase Progress"
_STAGE_SECTION = "Stage Progress"
#: The engine's literal for "no stage" / "no unit": absent, or a consumer would look for a dir ``none``.
_NONE_SENTINEL = "none"

# ---- small helpers ---------------------------------------------------------- #

#: One implementation for the whole backend, so stamps written here and parsed elsewhere round-trip.
_iso = C.iso_from_epoch


def _text(data: bytes | None) -> str:
    """Decode leniently: one bad byte in a 6 000-line audit shard must not cost every card it supports."""
    if data is None:
        return ""
    return data.decode("utf-8", errors="replace")


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value.strip()) if value is not None and value.strip() else None
    except ValueError:
        return None


def _clean(value: str | None) -> str | None:
    """``None`` for an absent, empty or ``none`` value; the trimmed string otherwise."""
    if value is None:
        return None
    text = value.strip()
    if not text or text.lower() == _NONE_SENTINEL:
        return None
    return text


def _json_loads(data: bytes | None) -> Any:
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8-sig", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None


def _str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if isinstance(v, (str, int, float)))
    return ()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


# ---- 1.6.1 snapshot and stable reads ---------------------------------------- #


@dataclass(frozen=True, slots=True)
class Snapshot(WireRecord):
    """One read of one file, with the evidence needed to decide whether it can be trusted."""

    #: ``bytes`` stays off the wire: content is served by the artifact route with its own cap and type.
    _wire_keys = ("relpath", "size", "mtime_ns", "mtime", "sha256", "unstable")
    relpath: str
    bytes: bytes
    size: int
    mtime_ns: int
    sha256: str
    unstable: bool

    @property
    def text(self) -> str:
        return _text(self.bytes)

    @property
    def mtime(self) -> str:
        return _iso(self.mtime_ns / 1_000_000_000)


def stable_read(repo: Path, rel: str, cap: int) -> Snapshot | None:
    """``stat`` → ``read`` → ``stat``, retried once; still moving is ``unstable=True`` (FORMAT-NOTES §11.3).

    ``None``: not a readable regular file. Oversize raises: half a file parses into a plausible, wrong view.
    """
    path = security.resolve_inside(repo, rel)
    if security.is_secret_file(path) or not security.is_regular_file(path):
        return None
    snap: Snapshot | None = None
    for attempt in (0, 1):
        try:
            before = os.stat(path)
        except OSError:
            return None
        if before.st_size > cap:
            raise StudioError(
                "too_large", f"{rel} is {before.st_size} bytes, over the {cap}-byte cap for this read",
                details={"relpath": rel, "size": before.st_size, "cap": cap},
            )
        try:
            with open(path, "rb") as handle:
                data = handle.read(cap + 1)
            after = os.stat(path)
        except OSError:
            return None
        if len(data) > cap:  # grew past the cap between the stat and the read
            raise StudioError("too_large", f"{rel} grew past the {cap}-byte cap while being read",
                              details={"relpath": rel, "size": len(data), "cap": cap})
        unstable = before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
        snap = Snapshot(relpath=rel, bytes=data, size=after.st_size, mtime_ns=after.st_mtime_ns,
                        sha256=hashlib.sha256(data).hexdigest(), unstable=unstable)
        if not unstable:
            return snap
        if attempt == 0:
            time.sleep(C.STABLE_READ_RETRY_SECS)
    return snap


# ---- 1.6.2 parsers — aidlc-state.md ----------------------------------------- #


@dataclass(frozen=True, slots=True)
class StageRow(WireRecord):
    """One ``- [x] build-and-test — EXECUTE`` line."""

    slug: str
    mark: str
    state: str
    phase: str | None
    suffix: str | None


@dataclass(frozen=True, slots=True)
class StateFile(WireRecord):
    """``aidlc-state.md`` as data: every ``## X`` block kept, scalars looked up by label like ``getField``."""

    _wire_keys = (
        "sections", "phases", "stages", "counts", "state_version", "current_stage", "next_stage", "status",
        "lifecycle_phase", "revision_count", "parked_at", "parked_at_stage", "autonomy_mode", "active_unit",
        "unit_state", "total_stages_claimed", "completed_claimed", "header_only",
    )
    sections: dict[str, dict[str, str]]
    project: dict[str, str]
    scope_config: dict[str, str]
    workspace: dict[str, str]
    plan: dict[str, str]
    runtime: dict[str, str]
    current: dict[str, str]
    resume: dict[str, str]
    phases: list[tuple[str, str]]
    stages: list[StageRow]
    counts: dict[str, int]
    state_version: int | None
    current_stage: str | None
    next_stage: str | None
    status: str | None
    lifecycle_phase: str | None
    revision_count: int
    parked_at: str | None
    parked_at_stage: str | None
    autonomy_mode: str | None
    active_unit: str | None
    unit_state: str | None
    total_stages_claimed: int | None
    completed_claimed: int | None
    header_only: bool

    def row(self, slug: str) -> StageRow | None:
        for row in self.stages:
            if row.slug == slug:
                return row
        return None


def parse_state_file(text: str) -> StateFile:
    """Parse a state file without ever refusing one; every trap it tolerates is real (FORMAT-NOTES §3, §11.4)."""
    sections: dict[str, dict[str, str]] = {}
    ordered: dict[str, list[tuple[str, str]]] = {}
    stages: list[StageRow] = []
    flat: dict[str, str] = {}
    section = ""
    phase: str | None = None

    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.rstrip("\r")
        sub = C.SUBSECTION_RE.match(line)
        if sub:
            marker = C.PHASE_HEADER_RE.match(sub.group(1).strip())
            # Only a phase sub-header moves ``phase``: a future ### must not re-label the rows under it.
            if marker:
                phase = marker.group(1).strip().lower()
            continue
        head = C.SECTION_RE.match(line)
        if head:
            section = head.group(1).strip()
            sections.setdefault(section, {})
            ordered.setdefault(section, [])
            phase = None
            continue
        field = C.FIELD_RE.match(line)
        if field:
            key, value = field.group(1).strip(), field.group(2)
            sections.setdefault(section, {})[key] = value
            ordered.setdefault(section, []).append((key, value))
            flat.setdefault(key, value)  # first occurrence wins, like the engine's getField
            continue
        if section == _STAGE_SECTION:
            box = C.CHECKBOX_RE.match(line)
            if box:
                mark, slug, suffix = box.group(1), box.group(2), box.group(3)
                stages.append(StageRow(slug=slug, mark=mark, state=C.CHECKBOX_MAP.get(mark.lower(), "unknown"),
                                       phase=phase, suffix=(suffix or "").strip() or None))

    counts = {"total": len(stages)}
    for state_name in C.STAGE_STATE:
        counts[state_name] = sum(1 for row in stages if row.state == state_name)
    counts["done"] = sum(counts[name] for name in C.DONE_STAGE_STATES)
    # The rows the plan runs (`— EXECUTE`): progress is measured against these, as the engine's own
    # "Progress: 5/8 in-scope stages" is. 0 for a state file that marks no plan, and then `total` is all there is.
    planned = [row for row in stages if (row.suffix or "").upper().startswith("EXECUTE")]
    counts["planned"] = len(planned)
    counts["planned_done"] = sum(1 for row in planned if row.state in C.DONE_STAGE_STATES)

    def named(title: str) -> dict[str, str]:
        return dict(sections.get(title, {}))

    return StateFile(
        sections={name: dict(fields) for name, fields in sections.items() if name},
        project=named("Project Information"),
        scope_config=named("Scope Configuration"),
        workspace=named("Workspace State"),
        plan=named("Execution Plan Summary"),
        runtime=named("Runtime State"),
        current=named("Current Status"),
        resume=named("Session Resume Point"),
        phases=list(ordered.get(_PHASE_SECTION, [])),
        stages=stages,
        counts=counts,
        state_version=_int_or_none(flat.get(C.STATE_VERSION_FIELD)),
        current_stage=_clean(flat.get("Current Stage")),
        next_stage=_clean(flat.get("Next Stage")),
        status=_clean(flat.get("Status")),
        lifecycle_phase=_clean(flat.get("Lifecycle Phase")),
        revision_count=_int_or_none(flat.get("Revision Count")) or 0,
        parked_at=_clean(flat.get("Parked")),
        parked_at_stage=_clean(flat.get("Parked At Stage")),
        autonomy_mode=_clean(flat.get("Construction Autonomy Mode")),
        active_unit=_clean(flat.get("Active Unit")),
        unit_state=_clean(flat.get("Unit State")),
        total_stages_claimed=_int_or_none(flat.get("Total Stages")),
        completed_claimed=_int_or_none(flat.get("Completed")),
        header_only=not sections and not stages,
    )


# ---- 1.6.2 parsers — registry, cursors, directive --------------------------- #


@dataclass(frozen=True, slots=True)
class IntentRow(WireRecord):
    """One ``intents.json`` entry (``IntentRegistryEntry``, ``04 §2.1``)."""

    uuid: str
    slug: str
    dir_name: str | None
    scope: str | None
    repos: tuple[str, ...]
    status: str


def parse_intents_json(data: bytes) -> list[IntentRow]:
    """Rows from ``intents.json``, ``[]`` if unparseable: a broken registry degrades labels, never hides work."""
    payload = _json_loads(data)
    if not isinstance(payload, list):
        return []
    rows: list[IntentRow] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        dir_name = entry.get("dirName")
        scope = entry.get("scope")
        rows.append(IntentRow(
            uuid=str(entry.get("uuid") or ""), slug=str(entry.get("slug") or ""),
            dir_name=str(dir_name) if isinstance(dir_name, str) and dir_name else None,
            scope=str(scope) if isinstance(scope, str) and scope else None,
            repos=_str_tuple(entry.get("repos")), status=str(entry.get("status") or "unknown"),
        ))
    return rows


def match_intent_row(rows: list[IntentRow], dir_name: str) -> IntentRow | None:
    """The registry row for a record directory, by the engine's ``recordDirMatches`` join (FORMAT-NOTES §11.5)."""
    for row in rows:
        if row.dir_name and row.dir_name == dir_name:
            return row
    for row in rows:
        if not row.slug or row.dir_name:
            continue
        prefix = f"{row.slug}-"
        if not dir_name.startswith(prefix):
            continue
        tail = dir_name[len(prefix):]
        if tail and re.fullmatch(r"[0-9a-f]+", tail) and row.uuid.endswith(tail):
            return row
    return None


def parse_cursor(data: bytes | None, *, default: str | None) -> str | None:
    """First line of a cursor file, or ``default`` when it is missing or blank (``04 §2``: never a throw)."""
    if data is None:
        return default
    lines = _text(data).splitlines()
    first = lines[0].strip() if lines else ""
    return first or default


@dataclass(frozen=True, slots=True)
class Directive(WireRecord):
    """``.aidlc-active-directive.json`` — which stage/unit is really executing (``04 §3.2``)."""

    _wire_keys = ("version", "kind", "stage", "unit", "units", "state_sha256", "matches_state")
    version: int
    stage: str
    unit: str | None
    state_sha256: str
    matches_state: bool
    #: ``None`` on version 1 (no such field); on version 2 one of ``DIRECTIVE_KINDS``, where only
    #: ``run-stage`` and ``invoke-swarm`` (the unitless code-generation shape) execute a stage.
    kind: str | None = None
    #: The multi-unit form of ``unit`` (``invoke-swarm``); empty on every version-1 marker.
    units: tuple[str, ...] = ()
    #: v2 Code Generation ``run-stage`` that omitted both unit fields; a null or malformed field is not this.
    zero_unit_run: bool = False
    #: Record-relative file the marker came from (``.aidlc-engine/…`` on 2.9+). Evidence only; off the wire.
    marker_name: str = DIRECTIVE_FILENAME


def parse_directive(data: bytes, state_sha256: str) -> Directive | None:
    """The directive marker (v1 or v2) or ``None``; stale means ``matches_state=False`` (FORMAT-NOTES §11.5)."""
    payload = _json_loads(data)
    if not isinstance(payload, dict):
        return None
    version = payload.get("version")
    if version is True or version not in DIRECTIVE_VERSIONS:   # JSON `true` equals 1 in Python
        return None
    kind = payload.get("kind")
    if version != 1 and kind not in DIRECTIVE_KINDS:
        return None
    stage = payload.get("stage")
    digest = payload.get("state_sha256")
    if not isinstance(stage, str) or not C.STAGE_SLUG_RE.match(stage):
        return None
    if not isinstance(digest, str) or not _SHA256_RE.match(digest):
        return None
    unit = payload.get("unit")
    return Directive(
        version=int(version), stage=stage, unit=str(unit) if isinstance(unit, str) and unit else None,
        state_sha256=digest, matches_state=digest == state_sha256, kind=kind if version != 1 else None,
        units=_str_tuple(payload.get("units")),
        zero_unit_run=(version == 2 and kind == "run-stage" and stage == "code-generation"
                       and "unit" not in payload and "units" not in payload),
    )


# ---- 1.6.2 parsers — audit -------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AuditEvent(WireRecord):
    """One audit block. ``fields`` values keep the literal ``\\n`` escapes the writer inserted."""

    shard: str
    shard_index: int
    pos: int
    timestamp: str
    event: str
    fields: dict[str, str]
    raw: str

    @property
    def sort_key(self) -> tuple[str, int, int]:
        return (self.timestamp, self.shard_index, self.pos)


def parse_audit_shard(text: str, shard: str, shard_index: int) -> list[AuditEvent]:
    """Split one shard into events keyed on ``**Event**:``; unknown event names are kept (FORMAT-NOTES §11.6)."""
    events: list[AuditEvent] = []
    chunks = text.replace("\r\n", "\n").split(C.AUDIT_BLOCK_SEPARATOR)
    for pos, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        fields: dict[str, str] = {}
        event = ""
        timestamp = ""
        lines = chunk.split("\n")
        for line in lines:
            match = C.AUDIT_FIELD_RE.match(line)
            if not match:
                continue
            key, value = match.group(1).strip(), match.group(2)
            if key == "Event":
                event = event or value.strip()
            elif key == "Timestamp":
                timestamp = timestamp or value.strip()
            else:
                fields.setdefault(key, value)
        if not event:
            continue
        start = next((i for i, line in enumerate(lines) if line.startswith("## ")), 0)
        events.append(AuditEvent(
            shard=shard, shard_index=shard_index, pos=pos, timestamp=timestamp, event=event, fields=fields,
            raw="\n".join(lines[start:]).strip("\n"),
        ))
    return events


def merge_audit(shards: list[list[AuditEvent]]) -> list[AuditEvent]:
    """Cross-shard order ``(timestamp, shard_index, pos)``: deterministic, not causal (FORMAT-NOTES §11.6)."""
    merged = [event for shard in shards for event in shard]
    merged.sort(key=lambda event: event.sort_key)
    return merged


def latest_event(
    events: list[AuditEvent], types: Sequence[str], stage: str | None = None
) -> AuditEvent | None:
    """The newest event of any of ``types`` (optionally for one stage): ``max``, so unsorted input works."""
    wanted = set(types)
    candidates = [e for e in events if e.event in wanted and (stage is None or e.fields.get("Stage") == stage)]
    if not candidates:
        return None
    return max(candidates, key=lambda event: event.sort_key)


@dataclass(frozen=True, slots=True)
class ShardMeta(WireRecord):
    relpath: str
    size: int
    mtime_ns: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class AuditBundle(WireRecord):
    """Every shard of one record, merged. ``complete=False`` when any shard was tail-read."""

    events: list[AuditEvent]
    shards: list[ShardMeta]
    complete: bool


def _audit_read_limits(
    sizes: Sequence[int], mtimes_ns: Sequence[int], per_shard: int, total: int
) -> list[int]:
    """Bytes each shard may add to one bounded history read; spare goes to fresh shards (FORMAT-NOTES §11.6)."""
    wanted = [max(0, min(size, per_shard)) for size in sizes]
    if sum(wanted) <= total:
        return wanted
    share = total // len(wanted)
    limits = [min(want, share) for want in wanted]
    left = total - sum(limits)
    for index in sorted(range(len(wanted)), key=lambda i: (-mtimes_ns[i], i)):
        if left <= 0:
            break
        extra = min(wanted[index] - limits[index], left)
        limits[index] += extra
        left -= extra
    return limits


# ---- 1.6.2 parsers — stage graph, scope grid, scopes ------------------------ #


@dataclass(frozen=True, slots=True)
class Consume(WireRecord):
    artifact: str
    required: bool
    conditional_on: str | None


REVIEW_CAPS = ("none", "advisory", "adversarial")
#: The effective review class is the *minimum* rank of stage, scope cap and request cap.
REVIEW_RANK = {name: rank for rank, name in enumerate(REVIEW_CAPS)}


def effective_review_class(node: "StageNode | None", caps: tuple[str | None, ...]) -> str:
    """``min(stage class, scope cap, request cap)`` by rank; a reviewer without a class counts as advisory."""
    if node is None:
        return "none"
    base = node.review_class if node.review_class in REVIEW_CAPS else ("advisory" if node.reviewer else "none")
    rank = REVIEW_RANK[base]
    for cap in caps:
        if cap in REVIEW_RANK:
            rank = min(rank, REVIEW_RANK[cap])
    return REVIEW_CAPS[rank]


@dataclass(frozen=True, slots=True)
class StageNode(WireRecord):
    """One ``stage-graph.json`` stage, keyed on ``slug``; keys old graphs lack are empty (FORMAT-NOTES §11.7)."""

    slug: str
    number: str
    name: str
    phase: str
    execution: str
    condition: str | None
    lead_agent: str
    support_agents: tuple[str, ...]
    mode: str
    for_each: str | None
    workspace_requires: bool
    produces: tuple[str, ...]
    optional_produces: tuple[str, ...]
    produces_kinds: dict[str, tuple[str, ...]]
    consumes: tuple[Consume, ...]
    requires_stage: tuple[str, ...]
    sensors: tuple[str, ...]
    scopes: tuple[str, ...]
    reviewer: str | None
    reviewer_max_iterations: int | None
    review_class: str | None
    summary_confirmation: str | None
    #: The one artifact the reviewer may append ``## Review`` to (2.7.1+). On four stages it is not
    #: ``produces[0]``, so guessing that file attaches one artifact's verdict to another's evidence.
    review_artifact: str | None = None
    #: Declared output location; not every stage writes its products into its record directory.
    outputs: str | None = None

    @property
    def per_unit(self) -> bool:
        return bool(self.for_each) or self.slug in _PER_UNIT_STAGE_SET

    def to_json(self) -> dict[str, Any]:
        return {**WireRecord.to_json(self), "per_unit": self.per_unit}


#: How ``parse_stage_graph`` reads the plain keys: text (``""`` when absent), a string or ``None``, and a
#: list of names. Every other key has its own rule below.
_NODE_TEXT = ("number", "phase", "execution", "lead_agent", "mode")
_NODE_OPTIONAL = ("condition", "for_each", "reviewer", "review_class", "summary_confirmation", "outputs")
_NODE_NAMES = ("support_agents", "produces", "optional_produces", "requires_stage", "sensors", "scopes")


def parse_stage_graph(data: bytes) -> list[StageNode]:
    """The stage graph of every generation shipped: an array or ``{"stages": [...]}``; no ``slug``, no node."""
    payload = _json_loads(data)
    if isinstance(payload, dict):
        payload = payload.get("stages")
    if not isinstance(payload, list):
        return []
    nodes: list[StageNode] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        consumes: list[Consume] = []
        for item in entry.get("consumes") or ():
            if isinstance(item, dict) and isinstance(item.get("artifact"), str):
                conditional = item.get("conditional_on")
                consumes.append(Consume(item["artifact"], bool(item.get("required")), _str_or_none(conditional)))
            elif isinstance(item, str):
                consumes.append(Consume(artifact=item, required=False, conditional_on=None))
        kinds = entry.get("produces_kinds")
        cap = entry.get("reviewer_max_iterations")
        review = entry.get("review_artifact")
        nodes.append(StageNode(
            slug=slug, name=str(entry.get("name") or slug),
            **{key: str(entry.get(key) or "") for key in _NODE_TEXT},
            **{key: _str_or_none(entry.get(key)) for key in _NODE_OPTIONAL},
            **{key: _str_tuple(entry.get(key)) for key in _NODE_NAMES},
            workspace_requires=bool(entry.get("workspace_requires")),
            produces_kinds={str(k): _str_tuple(v) for k, v in kinds.items()} if isinstance(kinds, dict) else {},
            consumes=tuple(consumes),
            reviewer_max_iterations=_int_or_none(str(cap)) if cap is not None else None,
            review_artifact=review if isinstance(review, str) and review else None,
        ))
    return nodes


def parse_scope_grid(data: bytes) -> dict[str, dict[str, str]]:
    """``{scope: {slug: "EXECUTE"|"SKIP"}}``, nested or flat; a project-composed scope is data, not an error."""
    payload = _json_loads(data)
    if not isinstance(payload, dict):
        return {}
    grid: dict[str, dict[str, str]] = {}
    for scope, value in payload.items():
        stages = value.get("stages") if isinstance(value, dict) else None
        if stages is None and isinstance(value, dict):
            stages = value
        if not isinstance(stages, dict):
            continue
        grid[str(scope)] = {str(slug): decision for slug, decision in stages.items() if isinstance(decision, str)}
    return grid


@dataclass(frozen=True, slots=True)
class ScopeMeta(WireRecord):
    """A ``.kiro/scopes/aidlc-<name>.md`` frontmatter block."""

    name: str
    depth: str | None
    test_strategy: str | None
    review_cap: str | None
    skeleton: str | None
    runner: bool | None
    keywords: tuple[str, ...]
    description: str | None
    plugin: str | None
    project_owned: bool


@dataclass(frozen=True, slots=True)
class WorkspaceSignals(WireRecord):
    """A repo's top level as Advisor evidence: names and a README excerpt, no listing (FORMAT-NOTES §11.7)."""

    #: The first ``MAX_WORKSPACE_ENTRIES`` names, sorted; directories end in ``/``; no dot-entries or links.
    top_level: tuple[str, ...]
    #: The ``WORKSPACE_MANIFESTS`` present as regular, non-symlinked files, in that tuple's order.
    manifests: tuple[str, ...]
    #: The first ``README_NAMES`` hit as ``bounded_text`` read it, NOT cut to ``MAX_README_CHARS``: the
    #: broker redacts the whole text before it cuts, so a credential is never split (§1.18a).
    readme_excerpt: str | None
    #: More than ``MAX_WORKSPACE_ENTRIES`` eligible entries exist.
    truncated: bool


def _parse_frontmatter(text: str) -> dict[str, Any] | None:
    """The leading ``---`` block as a flat mapping with ``- item`` lists: a deliberately small YAML subset."""
    lines = text.replace("\r\n", "\n").split("\n")
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines) or lines[start].strip() != _FRONTMATTER_FENCE:
        return None
    out: dict[str, Any] = {}
    key: str | None = None
    for line in lines[start + 1:]:
        if line.strip() == _FRONTMATTER_FENCE:
            break
        item = _BULLET_RE.match(line)
        if item and key is not None and line[:1] in (" ", "\t", "-"):
            bucket = out.setdefault(key, [])
            if isinstance(bucket, list):
                bucket.append(item.group(1).strip().strip("'\""))
            continue
        if ":" not in line:
            continue
        raw_key, _, raw_value = line.partition(":")
        key = raw_key.strip()
        value = raw_value.strip()
        if value in ("[]", "{}"):
            out[key] = []
        elif value.startswith("[") and value.endswith("]"):
            out[key] = [part.strip().strip("'\"") for part in value[1:-1].split(",") if part.strip()]
        elif value:
            out[key] = value.strip("'\"")
        else:
            out[key] = []
    return out


def parse_scope_frontmatter(text: str, filename: str) -> ScopeMeta | None:
    """One scope definition, or ``None``; ``project_owned`` follows the file name (FORMAT-NOTES §11.7)."""
    front = _parse_frontmatter(text)
    if front is None:
        return None
    stem = Path(filename).stem
    derived = stem[len(SCOPE_FILE_PREFIX):] if stem.startswith(SCOPE_FILE_PREFIX) else stem

    def one(*keys: str) -> str | None:
        for key in keys:
            value = front.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    runner_raw = one("runner")
    keywords = front.get("keywords")
    return ScopeMeta(
        name=one("name") or derived, depth=one("depth"), test_strategy=one("testStrategy", "test_strategy"),
        review_cap=one("review_cap", "reviewCap"), skeleton=one("skeleton"),
        runner=None if runner_raw is None else runner_raw.lower() in ("true", "yes", "on"),
        keywords=tuple(str(item) for item in keywords) if isinstance(keywords, list) else (),
        description=one("description"), plugin=one("plugin"), project_owned=derived not in STOCK_SCOPES,
    )


# ---- 1.6.2 parsers — questions file ----------------------------------------- #


@dataclass(frozen=True, slots=True)
class QuestionOption(WireRecord):
    letter: str
    text: str
    is_other: bool


@dataclass(frozen=True, slots=True)
class Question(WireRecord):
    _wire_keys = ("index", "prompt", "context", "options", "multi_select", "answer", "answered", "raw")
    index: int
    prompt: str
    options: tuple[QuestionOption, ...]
    multi_select: bool
    answer: str | None
    answered: bool
    raw: str
    context: str = ""


@dataclass(frozen=True, slots=True)
class Checkpoint(WireRecord):
    """A Plan Approval / Consolidated Summary Confirmation / Assumption Confirmation block.

    ``context`` (the text before the options) is kept only for the assumptions being accepted (review P09).
    """

    _wire_keys = ("kind", "present", "answered", "answer", "options", "context?", "engine_held?")
    kind: str
    present: bool
    answered: bool
    answer: str | None
    options: tuple[str, ...]
    context: str | None = None
    #: ``True`` for 2.11's engine-held Plan Approval (lettered choices): it answers from any chat, so it is never
    #: tied to the session that asked. ``None`` for every other block (and for 2.10's, which is session-bound).
    engine_held: bool | None = None


@dataclass(frozen=True, slots=True)
class QuestionsFile(WireRecord):
    _wire_keys = (
        "relpath", "sha256", "questions", "summary_confirmation", "plan_approval", "assumption_confirmation?",
        "pending_count", "unsupported_pending_count", "pending_checkpoint", "source_language_hint", "origin?",
    )
    relpath: str
    sha256: str
    questions: tuple[Question, ...]
    summary_confirmation: Checkpoint | None
    plan_approval: Checkpoint | None
    pending_count: int
    pending_checkpoint: str | None
    source_language_hint: str | None
    origin: dict[str, Any] | None = None
    # Internal proof that an answered file yielded to a newer audit question; never on the wire.
    closed_file: "QuestionsFile | None" = None
    closed_file_receipts: tuple[AuditEvent, ...] = ()
    # Blank human-answer tags outside supported Q/checkpoint sections. These carry no Q identifiers.
    unsupported_pending_count: int = 0
    assumption_confirmation: Checkpoint | None = None


# These are the bundled aidlc-log.ts / aidlc-lib.ts handshake and attempt boundaries.
# Kept local: they qualify audit questions, not every other use of audit history.
_AUDIT_QUESTION_ANSWERS = frozenset(
    ("QUESTION_ANSWERED", "SUMMARY_CONFIRMATION_RECORDED", "PLAN_APPROVAL_RECORDED")
)
_AUDIT_QUESTION_FLOORS = frozenset(
    ("STAGE_STARTED", "STAGE_REVISING", "STAGE_AWAITING_APPROVAL", "GATE_REJECTED",
     "STAGE_COMPLETED", "GATE_APPROVED", "WORKFLOW_STARTED", "STAGE_JUMPED")
)


def _audit_follows(candidate: AuditEvent, boundary: AuditEvent) -> bool | None:
    """Append position orders one shard; equal-second rows in different shards are unordered."""
    if candidate.shard == boundary.shard:
        return candidate.pos > boundary.pos
    if candidate.timestamp == boundary.timestamp:
        return None
    return candidate.timestamp > boundary.timestamp


def _audit_frontier(events: Sequence[AuditEvent]) -> list[AuditEvent]:
    by_shard: dict[str, AuditEvent] = {}
    for event in events:
        previous = by_shard.get(event.shard)
        if previous is None or event.pos > previous.pos:
            by_shard[event.shard] = event
    latest = max((e.timestamp for e in by_shard.values()), default="")
    return [e for e in by_shard.values() if e.timestamp == latest]


def _audit_event_identity(event: AuditEvent) -> dict[str, Any]:
    # shard_index is an enumeration offset, not identity: another shard may appear before it.
    return {
        "shard": event.shard, "pos": event.pos, "timestamp": event.timestamp,
        "event": event.event, "fields": dict(event.fields),
    }


def _audit_question_scope(event: AuditEvent, stage: str, unit: str | None) -> bool:
    return (
        not event.fields.get("Workflow")
        and event.fields.get("Stage") == stage
        and (event.fields.get("Unit") or None) == unit
    )


def _audit_question_floor(event: AuditEvent, stage: str, unit: str | None) -> bool:
    if event.event not in _AUDIT_QUESTION_FLOORS or event.fields.get("Workflow"):
        return False
    if event.fields.get("Unit") not in (None, "", unit):
        return False
    return event.event in ("WORKFLOW_STARTED", "STAGE_JUMPED") or event.fields.get("Stage") == stage


def _audit_question_history(audit: AuditBundle, stage: str, unit: str | None) -> list[AuditEvent]:
    return [
        e for e in audit.events
        if _audit_question_floor(e, stage, unit)
        or (_audit_question_scope(e, stage, unit)
            and e.event in _AUDIT_QUESTION_ANSWERS | {"DECISION_RECORDED"})
    ]


def _audit_question_generation(audit: AuditBundle, unit: str | None) -> str | None:
    generations = [
        e.fields["Attempt Generation"] for e in audit.events
        if unit and e.fields.get("Unit") == unit and not e.fields.get("Workflow")
        and re.fullmatch(r"[1-9][0-9]*", e.fields.get("Attempt Generation", ""))
    ]
    return str(max(map(int, generations))) if generations else None


def is_audit_text_label(label: str) -> bool:
    """Explicit input markers, not a guess from the prompt or an arbitrary single choice."""
    return " ".join(label.lower().replace("-", " ").split()) in ("free text", "free text note")


def audit_question_accepts_text(questions: QuestionsFile | None) -> bool:
    return bool(
        questions is not None and questions.origin is not None
        and questions.origin.get("kind") == "audit"
        and len(questions.questions) == 1
        and len(questions.questions[0].options) == 1
        and questions.questions[0].options[0].is_other
        and is_audit_text_label(questions.questions[0].options[0].text)
    )


def audit_questions(
    audit: AuditBundle, record_rel: str, stage: str, unit: str | None,
    *, generation: str | None = None,
) -> QuestionsFile | None:
    """Project one provably pending main-workflow decision; audit text remains data throughout.

    Missing/partial history, an ambiguous frontier, hidden exact choices and malformed enums have no
    actionable projection. A newer unusable decision still supersedes its predecessor.
    """
    if not audit.complete:
        return None
    history = _audit_question_history(audit, stage, unit)
    if any(C.epoch_from_iso(e.timestamp) is None for e in history):
        return None
    decisions = _audit_frontier([e for e in history if e.event == "DECISION_RECORDED"])
    if len(decisions) != 1:
        return None
    decision = decisions[0]
    floors = _audit_frontier([e for e in history if _audit_question_floor(e, stage, unit)])
    if not floors or any(_audit_follows(decision, floor) is not True for floor in floors):
        return None
    generation = generation or _audit_question_generation(audit, unit)
    if (decision.fields.get("Attempt Generation") or None) != generation:
        return None
    for event in history:
        if event.event not in _AUDIT_QUESTION_ANSWERS:
            continue
        if (event.fields.get("Attempt Generation") or None) != generation:
            continue
        if _audit_follows(event, decision) is not False:
            return None

    batch = _same_turn_decisions(audit, decision, stage, unit)
    parsed = [_audit_decision_labels(e) for e in batch]
    if any(p is None for p in parsed):
        return None
    prompt, labels = parsed[-1]
    identity = _audit_event_identity(decision)
    origin = {
        "kind": "audit", "event": decision.event, "shard": decision.shard,
        "pos": decision.pos, "timestamp": decision.timestamp, "stage": stage, "unit": unit,
        "workflow": None, "attempt_generation": generation,
        "decision_sha256": _sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False)),
    }
    digest = _sha256(json.dumps(
        {"decision": identity, "floors": sorted(
            (_audit_event_identity(e) for e in floors), key=lambda e: (e["shard"], e["pos"])
        ), "generation": generation},
        sort_keys=True, ensure_ascii=False,
    ))
    if len(batch) > 1:
        # Several questions asked in one turn take one reply (A37).
        origin["batch_size"] = len(batch)
        digest = _sha256(json.dumps(
            {"batch": [_audit_event_identity(e) for e in batch], "digest": digest},
            sort_keys=True, ensure_ascii=False,
        ))
        questions = tuple(
            _batch_question(n, p, ls, e.raw)
            for n, (e, (p, ls)) in enumerate(zip(batch, parsed), start=1)
        )
    else:
        questions = (Question(
            index=1, prompt=prompt,
            options=tuple(QuestionOption(
                chr(65 + i), label, len(labels) == 1 and is_audit_text_label(label),
            ) for i, label in enumerate(labels)),
            multi_select=False, answer=None, answered=False, raw=decision.raw,
        ),)
    return QuestionsFile(
        relpath=f"{record_rel}/{AUDIT_DIRNAME}/{decision.shard}",
        sha256=digest,
        questions=questions,
        summary_confirmation=None, plan_approval=None, pending_count=len(questions), pending_checkpoint=None,
        source_language_hint=None, origin=origin,
    )


def _batch_question(index: int, prompt: str, labels: list[str], raw: str) -> Question:
    """One question of a same-turn batch, with the choices the engine's chat offers (FORMAT-NOTES §11.8)."""
    single = prompt == C.LEARNINGS_ADD_PROMPT
    texts = [label for label in labels if label.lower() != C.AUDIT_OTHER_LABEL.lower()]
    if not single and C.AUDIT_NONE_LABEL not in texts:
        texts.append(C.AUDIT_NONE_LABEL)
    options = [QuestionOption(chr(65 + i), label, False) for i, label in enumerate(texts)]
    options.append(QuestionOption(chr(65 + len(options)), C.AUDIT_OTHER_LABEL, True))
    return Question(
        index=index, prompt=prompt, options=tuple(options), multi_select=not single,
        answer=None, answered=False, raw=raw,
    )


def _audit_decision_labels(decision: AuditEvent) -> tuple[str, list[str]] | None:
    prompt = decision.fields.get("Decision", "")
    raw_options = decision.fields.get("Options", "")
    labels = [label.strip() for label in raw_options.split(",")]
    if (
        not prompt.strip() or not 1 <= len(labels) <= 26 or not all(labels)
        or len(set(labels)) != len(labels) or "[protected exact choices]" in raw_options
        or any(len(label) > C.MAX_ANSWER_CHARS for label in labels)
        or any(char in prompt + raw_options for char in ("\x00", "\r", "\n"))
    ):
        return None
    return prompt, labels


def _same_turn_decisions(
    audit: AuditBundle, decision: AuditEvent, stage: str, unit: str | None,
) -> list[AuditEvent]:
    """The unbroken run of this stage's decision rows ending at ``decision``: they were asked together."""
    shard = sorted((e for e in audit.events if e.shard == decision.shard), key=lambda e: e.pos)
    index = next(i for i, e in enumerate(shard) if e.pos == decision.pos)
    run = [decision]
    for event in reversed(shard[:index]):
        if event.event != "DECISION_RECORDED" or not _audit_question_scope(event, stage, unit):
            break
        if (event.fields.get("Attempt Generation") or None) != (decision.fields.get("Attempt Generation") or None):
            break
        run.insert(0, event)
    return run


def audit_question_answer(
    audit: AuditBundle, origin: dict[str, Any], wire_text: str, since: str,
    *, project_dir: str | None = None,
) -> AuditEvent | None:
    """A receipt for the captured question, before another prompt or attempt can consume it.

    Disappearance alone is not an answer. Require complete readable history, the exact captured
    decision, matching stage/unit/generation/workflow, the sent label and provable causal order.
    """
    if not audit.complete or C.epoch_from_iso(since) is None:
        return None
    stage, unit = origin.get("stage"), origin.get("unit")
    if not stage or origin.get("workflow") is not None:
        return None
    history = _audit_question_history(audit, stage, unit)
    if any(C.epoch_from_iso(e.timestamp) is None for e in history):
        return None
    captured = next((
        e for e in history if e.event == "DECISION_RECORDED"
        and _sha256(json.dumps(_audit_event_identity(e), sort_keys=True, ensure_ascii=False))
        == origin.get("decision_sha256")
    ), None)
    if captured is None:
        return None
    expected = wire_text
    batch = int(origin.get("batch_size") or 1) > 1
    if batch:
        # The engine records the grouped reply without Studio's delivery instruction (A37).
        expected = expected.removesuffix(C.WIRE_AUDIT_GROUPED_ANSWER_SUFFIX)
    if batch or is_audit_text_label(captured.fields.get("Options", "")):
        # Mirror redactProjectDirPrefix + renderAuditBlock on our known message.
        # Never unescape untrusted audit rows or weaken the question/presence binding.
        if project_dir:
            variants = {str(Path(project_dir).absolute()), str(Path(project_dir).resolve())}
            variants |= {path.replace("\\", "/") for path in tuple(variants)}
            variants |= {path.replace("/", "\\") for path in tuple(variants)}
            for path in sorted(variants, key=len, reverse=True):
                expected = re.sub(re.escape(path) + r"(?=$|[/\\\s])", "<project-dir>", expected)
        expected = re.sub(r"\r\n?|\n|\u2028|\u2029", lambda _: "\\n", expected)
    generation = origin.get("attempt_generation")
    if _audit_question_generation(audit, unit) != generation:
        return None
    blockers = [
        e for e in history if e is not captured
        and (e.event == "DECISION_RECORDED" or _audit_question_floor(e, stage, unit))
        and _audit_follows(e, captured) is not False
    ]
    for event in history:
        if (
            event.event in _AUDIT_QUESTION_ANSWERS
            and (event.fields.get("Attempt Generation") or None) == generation
            and event.fields.get("Checkpoint") == captured.fields.get("Checkpoint")
            and event.fields.get("Questions File") == captured.fields.get("Questions File")
            and event.fields.get("Details") == expected
            and event.timestamp >= since
            and _audit_follows(event, captured) is True
            and all(_audit_follows(blocker, event) is True for blocker in blockers)
        ):
            return event
    return None


def plan_approval_choice(answer: str | None) -> str:
    """A Plan Approval answer as the choice it names: 2.11 records the lettered choice (``A. Approve Plan``), 2.10
    the bare one, and both mean ``Approve Plan``."""
    return C.PLAN_ANSWER_LETTER_RE.sub("", (answer or "").strip(), count=1)


def _closed_question_receipts(
    found: QuestionsFile, pending: QuestionsFile, audit: AuditBundle, data: bytes | None,
) -> list[AuditEvent] | None:
    """Only a file-bound closure in this attempt can hand an answered sheet back to audit (FORMAT-NOTES §11.8)."""
    if (
        not audit.complete or found.pending_count or found.pending_checkpoint
        or data is None or hashlib.sha256(data).hexdigest() != found.sha256
        or pending.origin is None
    ):
        return None
    origin = pending.origin
    stage, unit, generation = origin["stage"], origin["unit"], origin["attempt_generation"]
    history = _audit_question_history(audit, stage, unit)
    decision = next((
        e for e in history if e.shard == origin["shard"] and e.pos == origin["pos"]
        and e.event == "DECISION_RECORDED"
    ), None)
    floors = _audit_frontier([e for e in history if _audit_question_floor(e, stage, unit)])
    if decision is None or not floors:
        return None
    receipts: list[AuditEvent] = []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    normalized = re.sub(r"\r\n?", "\n", text)
    confirmed_digests = {_sha256(_confirmed_content(normalized))}
    assumption = found.assumption_confirmation
    if assumption is not None:
        prefix = _before_final_assumption_section(normalized)
        if prefix is None:
            return None
        confirmed_digests = {_sha256(_confirmed_content(prefix))}
    for checkpoint, event_name, heading, answer in (
        (found.summary_confirmation, "SUMMARY_CONFIRMATION_RECORDED",
         C.SUMMARY_CONFIRMATION_HEADING, C.WIRE_LOOKS_CORRECT),
        (found.plan_approval, "PLAN_APPROVAL_RECORDED", C.PLAN_APPROVAL_HEADING, C.WIRE_APPROVE_PLAN),
    ):
        if checkpoint is None:
            continue
        recorded = plan_approval_choice(checkpoint.answer) if event_name == "PLAN_APPROVAL_RECORDED" \
            else checkpoint.answer
        if not checkpoint.answered or recorded != answer:
            return None
        # Pick the frontier before checking its binding: a newer malformed receipt supersedes an
        # older valid one, and same-second receipts in different shards cannot be ordered.
        latest = _audit_frontier([
            e for e in history if e.event == event_name and _audit_question_scope(e, stage, unit)
            and (e.fields.get("Attempt Generation") or None) == generation
        ])
        if len(latest) != 1:
            return None
        receipt = latest[0]
        checkpoint_names = {heading}
        if event_name == "PLAN_APPROVAL_RECORDED":
            checkpoint_names.update(C.PLAN_APPROVAL_AUDIT_CHECKPOINTS)
        if (
            receipt.fields.get("Questions File") != found.relpath
            or receipt.fields.get("Checkpoint") not in checkpoint_names
            or receipt.fields.get("Details") != answer
            or _audit_follows(decision, receipt) is not True
            or any(_audit_follows(receipt, floor) is not True for floor in floors)
        ):
            return None
        scope = receipt.fields.get("Hash Scope")
        if scope is None:
            digest = found.sha256
        elif scope == "confirmed-content-v1" and event_name == "SUMMARY_CONFIRMATION_RECORDED":
            digest = receipt.fields.get("Questions SHA-256")
            if digest not in confirmed_digests:
                return None
        else:
            return None
        if receipt.fields.get("Questions SHA-256") != digest:
            return None
        receipts.append(receipt)
    if assumption is not None:
        summary = next((r for r in receipts if r.event == "SUMMARY_CONFIRMATION_RECORDED"), None)
        answer = (assumption.answer or "").strip()
        if (
            summary is None or not assumption.answered
            or answer not in (C.WIRE_ACCEPT_ASSUMPTIONS, C.WIRE_CONVERT_ASSUMPTIONS)
        ):
            return None
        answered = _audit_frontier([
            e for e in history if e.event == "QUESTION_ANSWERED" and _audit_question_scope(e, stage, unit)
            and (e.fields.get("Attempt Generation") or None) == generation
            and _audit_follows(e, summary) is True and _audit_follows(decision, e) is True
        ])
        if len(answered) != 1 or (answered[0].fields.get("Details") or "").strip() != answer:
            return None
        receipts.append(answered[0])
    return receipts or None


_CONFIRMED_CONTENT_TRAILING = (
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)


def _confirmed_content(normalized: str) -> str:
    """The engine's confirmed-content-v1 input: newline-normalized, ECMAScript trailing trim."""
    return normalized.rstrip(_CONFIRMED_CONTENT_TRAILING)


def _before_final_assumption_section(normalized: str) -> str | None:
    """Content before the one ``## Assumption Confirmation`` heading, when that section ends the file."""
    headings = list(re.finditer(r"^## .*$", normalized, re.MULTILINE))
    assumption = [
        m for m in headings
        if m.group(0).rstrip() == f"## {C.ASSUMPTION_CONFIRMATION_HEADING}"
    ]
    if len(assumption) != 1 or headings[-1] is not assumption[0]:
        return None
    return normalized[:assumption[0].start()]


#: Heading → ``Checkpoint.kind``. ``Post-approval Amendment`` is deliberately absent: it is a record of an
#: amendment already agreed, not something pending (C34).
_CHECKPOINT_KINDS = {
    C.SUMMARY_CONFIRMATION_HEADING: "summary_confirmation",
    C.ASSUMPTION_CONFIRMATION_HEADING: "assumption_confirmation",
    C.PLAN_APPROVAL_HEADING: "plan_approval",
}


def _answer_from(lines: list[str], tag_index: int, *, plain_continuation: bool = False) -> str:
    """The tag remainder plus its continuing paragraph or bullets (multi-line, FORMAT-NOTES §7, §11.8)."""
    head = C.ANSWER_TAG_RE.match(lines[tag_index])
    parts = [head.group(1).strip()] if head else []
    bullets = False
    for line in lines[tag_index + 1:]:
        stripped = line.strip()
        if not stripped or stripped == _FRONTMATTER_FENCE or stripped.startswith("#"):
            break
        bullet = bool(C.ANSWER_CONTINUATION_RE.match(line))
        if not bullet and (not plain_continuation or bullets):
            break
        bullets = bullets or bullet
        # The ``- `` marker is kept: four bare lines shown back to the human read as one run-on sentence.
        parts.append(stripped)
    return "\n".join(part for part in parts if part).strip()


def _visible_question_lines(raw_lines: list[str]) -> list[str]:
    """Mask fenced examples while keeping line offsets identical to the source."""
    lines: list[str] = []
    fence: str | None = None
    for line in raw_lines:
        marker = _QUESTION_CODE_FENCE_RE.match(line)
        if fence is not None:
            if (
                marker and marker.group(1)[0] == fence[0]
                and len(marker.group(1)) >= len(fence) and not marker.group(2).strip(" \t")
            ):
                fence = None
            lines.append("")
        elif marker and (marker.group(1)[0] == "~" or "`" not in marker.group(2)):
            fence = marker.group(1)
            lines.append("")
        else:
            lines.append(line)
    return lines


def _question_multi_select(prompt: str, context_lines: list[str]) -> bool:
    """An explicit marker in the heading or a standalone instruction, never a mention (FORMAT-NOTES §11.8)."""
    if _QUESTION_MULTI_SELECT_RE.search(prompt):
        return True
    emphasis = ("***", "___", "**", "__", "*", "_")
    for paragraph in re.split(r"\n[ \t]*\n", "\n".join(context_lines)):
        lines = paragraph.splitlines()
        # A quote's unprefixed soft-wrapped lines are still quoted background.
        if any(line.lstrip().startswith(">") for line in lines):
            continue
        text = " ".join(line.strip() for line in lines).strip()
        for delimiter in emphasis:
            if (
                text.startswith(delimiter) and text.endswith(delimiter)
                and len(text) > 2 * len(delimiter)
            ):
                text = text[len(delimiter):-len(delimiter)]
                break
        for marker in _QUESTION_MULTI_SELECT_RE.finditer(text):
            prefix = text[:marker.start()].rstrip()
            suffix = text[marker.end():].strip()
            # A final parenthetical can be emphasized independently of the preceding prose.
            for delimiter in emphasis:
                if prefix.endswith(delimiter) and suffix == delimiter:
                    prefix = prefix[:-len(delimiter)].rstrip()
                    suffix = ""
                    break
            if (
                not suffix
                and (not prefix or prefix.endswith((".", "!", "?", "。", "！", "？")))
            ):
                return True
    return False


def file_questions_support_forms(questions: QuestionsFile) -> bool:
    """Only unambiguous, persisted option groups become editable forms; the rest stay conversation-only."""
    if questions.unsupported_pending_count:
        return False
    indexes = [question.index for question in questions.questions]
    if any(index < 1 for index in indexes) or len(indexes) != len(set(indexes)):
        return False
    if not indexes:
        return bool(questions.pending_checkpoint)
    for question in questions.questions:
        if not question.prompt.strip():
            return False
        letters = [option.letter for option in question.options]
        if len(letters) != len(set(letters)) or any(not option.text.strip() for option in question.options):
            return False
        if question.answered:
            continue
        lines = _visible_question_lines(question.raw.splitlines())
        if not letters or sum(bool(C.ANSWER_TAG_RE.match(line)) for line in lines) != 1:
            return False
        for line in lines:
            if C.ANSWER_TAG_RE.match(line):
                break
            if _QUESTION_OPTION_LIKE_RE.match(line) and not C.OPTION_LINE_RE.match(line):
                return False
    return True


def parse_questions_file(text: str, relpath: str, sha256: str) -> QuestionsFile:
    """Parse a ``<stage>-questions.md`` file. Never raises on content.

    Section, option and ``pending_count`` rules, each from a real file: FORMAT-NOTES §7 and §11.8.
    """
    raw_lines = text.replace("\r\n", "\n").split("\n")
    lines = _visible_question_lines(raw_lines)
    # Blank placeholders keep raw section offsets intact, including an unclosed fence's remainder.
    # The preamble can also contain an answer tag without a supported heading.
    starts: list[tuple[int, str, Any]] = [(0, "other", None)]
    for index, line in enumerate(lines):
        question = C.QUESTION_HEADING_RE.match(line) or _QUESTION_H3_RE.match(line)
        if question:
            starts.append((index, "question", question))
            continue
        checkpoint = C.CHECKPOINT_HEADING_RE.match(line)
        if checkpoint:
            starts.append((index, "checkpoint", checkpoint))
            continue
        if _QUESTION_SECTION_END_RE.match(line):
            starts.append((index, "other", None))

    questions: list[Question] = []
    checkpoints: dict[str, Checkpoint] = {}
    unsupported_pending_count = 0
    for order, (start, kind, match) in enumerate(starts):
        end = starts[order + 1][0] if order + 1 < len(starts) else len(lines)
        body = lines[start:end]
        if kind == "other" or (
            kind == "checkpoint" and match.group(1) not in _CHECKPOINT_KINDS
        ):
            unsupported_pending_count += sum(bool(C.PENDING_ANSWER_TAG_RE.match(line)) for line in body)
            continue
        tag_index = next(
            (i for i, line in enumerate(body) if C.ANSWER_TAG_RE.match(line)), None
        )
        if tag_index is not None:
            # A section owns only its first tag. A blank one after it (a bold "Follow-up" label, a
            # setext heading, "- [Answer]:") is a question with no identifier of its own.
            unsupported_pending_count += sum(
                bool(C.PENDING_ANSWER_TAG_RE.match(line)) for line in body[tag_index + 1:]
            )
        answered = tag_index is not None and not C.BLANK_ANSWER_RE.match(body[tag_index])
        answer = _answer_from(body, tag_index, plain_continuation=kind == "question") if answered and tag_index is not None else None
        limit = tag_index if tag_index is not None else len(body)
        if kind == "question":
            options: list[QuestionOption] = []
            context_end = limit
            for index, line in enumerate(body[1:limit], 1):
                option = C.OPTION_LINE_RE.match(line)
                if option or _QUESTION_OPTION_LIKE_RE.match(line):
                    context_end = min(context_end, index)
                if option:
                    letter = option.group(1)
                    is_other = letter == C.OTHER_OPTION_LETTER
                    options.append(QuestionOption(letter, option.group(2).strip(), is_other))
            prompt = match.group(2).strip()
            context_start = 1
            # Trim blank boundary lines without stripping Markdown list/code indentation.
            while context_start < context_end and not raw_lines[start + context_start].strip():
                context_start += 1
            while context_end > context_start and not raw_lines[start + context_end - 1].strip():
                context_end -= 1
            context = "\n".join(raw_lines[start + context_start:start + context_end])
            questions.append(Question(
                index=int(match.group(1)), prompt=prompt, options=tuple(options),
                multi_select=_question_multi_select(prompt, body[context_start:context_end]),
                answer=answer, answered=answered, raw="\n".join(raw_lines[start:end]).strip("\n"), context=context,
            ))
            continue
        checkpoint_kind = _CHECKPOINT_KINDS.get(match.group(1))
        if checkpoint_kind is None:  # "Post-approval Amendment" — a divider, not a decision
            continue
        context = None
        if checkpoint_kind == "assumption_confirmation":
            option_rows = [i for i, line in enumerate(body[1:limit], 1) if C.ASSUMPTION_OPTION_RE.match(line)]
            labels = tuple(C.ASSUMPTION_OPTION_RE.match(body[i]).group(1) for i in option_rows)
            context_end = option_rows[0] if option_rows else limit
            context = "\n".join(raw_lines[start + 1:start + context_end]).strip("\n").strip() or None
        else:
            labels = tuple(
                line[2:].strip()
                for line in body[1:limit]
                if C.CHECKPOINT_OPTION_RE.match(line)
            )
        lettered = tuple(
            line[2:].strip() for line in body[1:limit] if C.PLAN_APPROVAL_OPTION_RE.match(line)
        ) if checkpoint_kind == "plan_approval" else ()
        # A re-opened checkpoint is appended below the old one, so the last block is the live one.
        checkpoints[checkpoint_kind] = Checkpoint(kind=checkpoint_kind, present=True, answered=answered,
                                                  answer=answer, options=lettered or labels, context=context,
                                                  engine_held=True if lettered else None)

    summary = checkpoints.get("summary_confirmation")
    assumptions = checkpoints.get("assumption_confirmation")
    plan = checkpoints.get("plan_approval")
    # The engine's own order: the summary is confirmed before its assumptions (intent-capture Step 5).
    pending_checkpoint = next(
        (kind for kind in C.QUESTION_CHECKPOINT_KINDS
         if checkpoints.get(kind) is not None and not checkpoints[kind].answered),
        None,
    )
    return QuestionsFile(
        relpath=relpath,
        sha256=sha256,
        questions=tuple(questions),
        summary_confirmation=summary,
        plan_approval=plan,
        pending_count=sum(1 for question in questions if not question.answered) + unsupported_pending_count,
        pending_checkpoint=pending_checkpoint,
        source_language_hint=None,
        unsupported_pending_count=unsupported_pending_count,
        assumption_confirmation=assumptions,
    )


def question_digest(data: bytes | None) -> str | None:
    """``sha256`` over the whole questions file (any edit changes what the human saw, C30), or ``None``."""
    if data is None:
        return None
    return hashlib.sha256(data).hexdigest()


# ---- 1.6.2 parsers — review section, stage file, breadcrumb ----------------- #


@dataclass(frozen=True, slots=True)
class ReviewFinding(WireRecord):
    level: str
    title: str
    quote: str | None
    anchor: str | None
    reviewer: str | None
    iteration: int | None
    #: 2.11's review record keeps each finding as fields. Kept apart, verbatim, so a card can show them as the
    #: reviewer wrote them; ``None`` for a finding read from a ``## Review`` section, whose row is the title.
    finding_id: str | None = None
    severity: str | None = None
    location: str | None = None
    required_action: str | None = None
    status: str | None = None

    def to_json(self) -> dict[str, Any]:
        # The record fields appear only when the record supplied them, so a `## Review` finding keeps the exact
        # shape (and evidence digest) it always had.
        out = WireRecord.to_json(self)
        for key in _RECORD_FINDING_FIELDS:
            if out.get(key) is None:
                out.pop(key, None)
        return out


_RECORD_FINDING_FIELDS = ("finding_id", "severity", "location", "required_action", "status")


class ReviewFindings(list):
    """``list[ReviewFinding]`` plus ``findings_parsed``: could the body be enumerated? (FORMAT-NOTES §11.9)."""

    __slots__ = ("findings_parsed",)

    def __init__(self, items: Iterable[ReviewFinding] = (), *, findings_parsed: bool = True) -> None:
        super().__init__(items)
        self.findings_parsed = findings_parsed


def parse_review_section(artifact_text: str) -> tuple[str | None, ReviewFindings]:
    """``(verdict, findings)`` from the LAST ``## Review`` section: only the terminal verdict binds.

    Bullets and rows under a findings heading; unplaced rows: ``findings_parsed=False`` (FORMAT-NOTES §11.9).
    """
    lines = artifact_text.replace("\r\n", "\n").split("\n")
    starts = [index for index, line in enumerate(lines) if _REVIEW_HEADING_RE.match(line)]
    if not starts:
        return (None, ReviewFindings())
    start = starts[-1]
    end = next(
        (index for index, line in enumerate(lines) if index > start and _H2_RE.match(line)),
        len(lines),
    )
    body = lines[start + 1:end]

    verdict: str | None = None
    reviewer: str | None = None
    iteration: int | None = None
    level = "unknown"
    #: What the rows under the current heading are: "findings", "other" or "unknown" (none recognised yet).
    section = "unknown"
    after_rule = False
    unplaced_rows = 0
    findings: list[ReviewFinding] = []

    def finding(title: str) -> ReviewFinding:
        quoted = _QUOTED_RE.search(title)
        return ReviewFinding(level=level, title=title, quote=quoted.group(1) if quoted else None, anchor=None,
                             reviewer=reviewer, iteration=iteration)

    for line in body:
        match = _VERDICT_RE.match(line)
        if match:
            verdict = match.group(1).upper()
            continue
        who = _REVIEWER_FIELD_RE.match(line)
        if who:
            reviewer = who.group(1).strip().strip("*")
            continue
        count = _ITERATION_FIELD_RE.match(line)
        if count:
            iteration = int(count.group(1))
            continue
        heading = _H3_RE.match(line)
        if heading:
            label = heading.group(1).strip().lower()
            level = next((value for prefix, value in _REVIEW_LEVELS if label.startswith(prefix)), "unknown")
            if level != "unknown" or _FINDINGS_HEADING_RE.match(label):
                section = "findings"
            elif _NON_FINDINGS_HEADING_RE.match(label):
                section = "other"
            else:
                section = "unknown"
            after_rule = False
            continue
        row = _TABLE_ROW_RE.match(line)
        if row:
            cells = [cell.strip() for cell in row.group(1).split("|")]
            solid = [cell for cell in cells if cell]
            if solid and all(_TABLE_RULE_CELL_RE.match(cell) for cell in solid):
                after_rule = True
                continue
            title = " | ".join(solid)
            if not after_rule or not title:
                continue
            if section == "findings":
                findings.append(finding(title))
            elif section == "unknown":
                unplaced_rows += 1
            continue
        # A blank line, or any prose, ends a table: what follows a paragraph is a new table's header.
        after_rule = False
        bullet = _BULLET_RE.match(line)
        if bullet and bullet.group(1):
            findings.append(finding(bullet.group(1).strip()))
    return (verdict, ReviewFindings(findings, findings_parsed=bool(findings) or unplaced_rows == 0))


#: Where 2.11 keeps each verdict, `<dir>/<stage>/stage/<attempt>/<n>.json` or `<dir>/<stage>/units/<unit>/…`
#: under the intent record. Audit rows written before the relocation keep naming `.aidlc-reviews/`.
_REVIEW_RECORD_DIRS = (".aidlc-engine/reviews", ".aidlc-reviews")
_REVIEW_RECORD_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REVIEW_ATTEMPT_RE = re.compile(r"^[0-9a-f]{16}$")
_REVIEW_ITERATION_RE = re.compile(r"^[1-9][0-9]*$")
#: A record finding's fields, in the column order of the table AI-DLC prints in its readable copy.
_REVIEW_RECORD_FINDING_KEYS = ("id", "severity", "location", "finding", "required_action", "status")


def is_review_record_path(path: str) -> bool:
    """AI-DLC's own shape check (`isReviewRecordRelativePath`): a row naming anything else names no record."""
    prefix = next((d for d in _REVIEW_RECORD_DIRS if path.startswith(f"{d}/")), None)
    if prefix is None:
        return False
    parts = path[len(prefix) + 1:].split("/")
    leaf = parts[-1]
    if not (len(parts) in (4, 5) and _REVIEW_RECORD_SEGMENT_RE.match(parts[0]) and leaf.endswith(".json")
            and _REVIEW_ITERATION_RE.match(leaf[:-5]) and _REVIEW_ATTEMPT_RE.match(parts[-2])):
        return False
    return (len(parts) == 4 and parts[1] == "stage") or (
        len(parts) == 5 and parts[1] == "units" and bool(_REVIEW_RECORD_SEGMENT_RE.match(parts[2])))


def latest_review_completion(events: Sequence[AuditEvent], stage: str, unit: str | None) -> AuditEvent | None:
    """The newest ``REVIEW_COMPLETED`` of one stage scope that answers a ``REVIEW_REQUESTED``.

    Paired the way AI-DLC 2.11 pairs them (`pairedReviewCompletions`): same stage, Unit and iteration, bound by the
    request id, each request answered once. Not floored by the attempt, as AI-DLC's own reader is not: across a
    rejection the previous review stays the one shown until the next replaces it. A ``Workflow`` row is an
    isolated ``--single`` run reviewing for its own gate, not the stage's.
    """
    pending: set[tuple[str, str, str]] = set()
    latest: AuditEvent | None = None
    for event in events:
        fields = event.fields
        if event.event not in C.REVIEW_EVENTS or fields.get("Stage") != stage or fields.get("Workflow"):
            continue
        iteration = fields.get("Iteration") or ""
        if not _REVIEW_ITERATION_RE.match(iteration):
            continue
        key = (fields.get("Unit") or "", iteration, fields.get("Request Id") or "")
        if event.event == "REVIEW_REQUESTED":
            pending.add(key)
        elif key in pending:
            pending.discard(key)
            if key[0] == (unit or ""):
                latest = event
    return latest


def parse_review_record(data: bytes, completion: AuditEvent) -> tuple[str | None, ReviewFindings] | None:
    """``(verdict, findings)`` from the 2.11 review record ``completion`` names; ``None`` when these bytes are not it.

    The two checks AI-DLC's own reader makes (`readReviewRecord`, `reviewRecordMatchesCompletion`): the digest the
    row pinned binds the bytes, and the scope fields bind the content, so a record edited afterwards, or another
    scope's record, is not this review. Each finding is its table row, cells joined in the readable copy's order
    and kept verbatim; only a finding AI-DLC marked ``Resolved`` gets a level, since a severity is not a level.
    """
    fields = completion.fields
    if f"sha256:{hashlib.sha256(data).hexdigest()}" != fields.get("Review Record Digest"):
        return None
    record = _json_loads(data)
    if not isinstance(record, dict):
        return None
    verdict, iteration, reviewer = record.get("verdict"), record.get("iteration"), record.get("reviewer")
    if not (isinstance(verdict, str) and verdict == fields.get("Verdict")
            and isinstance(iteration, int) and not isinstance(iteration, bool)
            and str(iteration) == fields.get("Iteration")
            and isinstance(reviewer, str) and reviewer == fields.get("Reviewer")
            and record.get("stage") == fields.get("Stage")
            and (record.get("unit") or "") == (fields.get("Unit") or "")):
        return None
    rows = record.get("findings")
    findings: list[ReviewFinding] = []
    parsed = isinstance(rows, list)
    for row in rows if isinstance(rows, list) else ():
        cells = {key: row.get(key) for key in _REVIEW_RECORD_FINDING_KEYS} if isinstance(row, dict) else {}
        text = {key: value.strip() for key, value in cells.items() if isinstance(value, str) and value.strip()}
        title = text.get("finding") or " | ".join(text.values())
        if not title:
            parsed = False
            continue
        # No quote: the first backtick span of a structured finding is a file name more often than the point.
        findings.append(ReviewFinding(
            level="resolved" if text.get("status") == "Resolved" else "unknown", title=title, quote=None, anchor=None,
            reviewer=reviewer, iteration=iteration, finding_id=text.get("id"), severity=text.get("severity"),
            location=text.get("location"), required_action=text.get("required_action"), status=text.get("status")))
    return (verdict.upper(), ReviewFindings(findings, findings_parsed=parsed))


@dataclass(frozen=True, slots=True)
class StageFileMeta(WireRecord):
    slug: str
    acceptance_criteria: tuple[str, ...]


def parse_stage_file(text: str, slug: str) -> StageFileMeta:
    """Acceptance criteria from a stage definition; empty is normal (2.6.2 stage files declare none)."""
    lines = text.replace("\r\n", "\n").split("\n")
    criteria: list[str] = []
    collecting = False
    for line in lines:
        if _ACCEPTANCE_HEADING_RE.match(line):
            collecting = True
            continue
        if collecting and (_H2_RE.match(line) or _H3_RE.match(line)):
            break
        if collecting:
            bullet = _BULLET_RE.match(line)
            if bullet and bullet.group(1):
                criteria.append(bullet.group(1).strip())
    return StageFileMeta(slug=slug, acceptance_criteria=tuple(criteria))


def parse_recovery_breadcrumb(text: str) -> dict[str, str]:
    """``.aidlc-recovery.md`` fields. Its lines carry no leading ``- `` (FORMAT-NOTES §8)."""
    out: dict[str, str] = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        match = C.AUDIT_FIELD_RE.match(line)
        if match:
            out.setdefault(match.group(1).strip(), match.group(2).strip())
    return out


# ---- artifacts, markers, baselines ------------------------------------------ #


@dataclass(frozen=True, slots=True)
class ArtifactMeta(WireRecord):
    """One file in a record (§1.8; defined here to avoid an import cycle, re-exported by ``projection``)."""

    artifact_id: str
    relpath: str
    name: str
    stage: str | None
    phase: str | None
    unit: str | None
    size: int
    mtime: str
    sha256: str | None
    kind: str
    renderable: bool


class ArtifactList(list):
    """``list[ArtifactMeta]`` that also says whether the cap or the scan deadline cut the walk short."""

    __slots__ = ("truncated", "reason")

    def __init__(self, items: Iterable[ArtifactMeta] = (), *, truncated: bool = False,
                 reason: str | None = None) -> None:
        super().__init__(items)
        self.truncated = truncated
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Markers(WireRecord):
    """The runtime dot-files around a record. Presence and mtime only — none of it is authority."""

    human_turn_mtime_ns: int | None
    engine_touch_mtime_ns: int | None
    recovery: dict[str, str] | None
    goal_stop_present: bool
    hooks_health: dict[str, str]
    stop_block_count: dict | None
    turn_counter: int | None
    compose_pending: bool
    reviewer_dispatch: dict | None


@dataclass(frozen=True, slots=True)
class PresenceBaseline(WireRecord):
    """What the record looked like when a decision was submitted (C23, FORMAT-NOTES §11.10)."""

    human_turn_events: int
    human_turn_events_partial: bool
    human_turn_mtime_ns: int | None
    turn_counter: int | None
    shard_sizes: dict[str, int]
    audit_tail_sha256: str
    state_sha256: str | None
    taken_at: str


@dataclass(frozen=True, slots=True)
class BoundaryInputs:
    """One coherent read of everything a boundary decision depends on; ``stable=False`` if it spans a write."""

    space: str
    intent_dir: str
    state_snap: Snapshot | None
    state: StateFile | None
    audit: AuditBundle
    directive: Directive | None
    questions: QuestionsFile | None
    stage: str | None
    stable: bool
    taken_at: str
    #: A per-unit stage whose questions several units hold while nothing names the executing one.
    #: ``questions`` is then ``None`` on purpose (FORMAT-NOTES §11.10); additive to §1.6.3.
    unit_ambiguous: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "space": self.space, "intent_dir": self.intent_dir,
            "state": self.state_snap.to_json() if self.state_snap else None,
            "directive": self.directive.to_json() if self.directive else None,
            "questions": self.questions.to_json() if self.questions else None,
            "stage": self.stage, "unit_ambiguous": self.unit_ambiguous, "audit_complete": self.audit.complete,
            "stable": self.stable, "taken_at": self.taken_at,
        }


# ---- 1.6.4 token formulas (binding) ----------------------------------------- #


def boundary_token(
    state_sha256: str,
    events: list[AuditEvent],
    current_stage: str | None,
    revision_count: int,
) -> str:
    """A fingerprint of the boundary the human is looking at: the compare-and-submit key (FORMAT-NOTES §11.10)."""
    last = latest_event(events, C.BOUNDARY_EVENTS)
    ts = last.timestamp if last else "none"
    return security.composite_token(state_sha256, ts, current_stage or "none", str(revision_count))


def stage_attempt(events: list[AuditEvent], stage: str) -> int:
    """Which attempt at this stage: ``1 + count(STAGE_REVISING)`` (``Revision Count`` is workflow-global, C05)."""
    return 1 + sum(
        1 for event in events if event.event == "STAGE_REVISING" and event.fields.get("Stage") == stage
    )


def evidence_digest(
    artifacts: list[ArtifactMeta] | None,
    review: tuple[str | None, list[ReviewFinding]] | None,
) -> str | None:
    """A digest of what the human actually looked at: sorted ``relpath:sha256`` rows plus the review (C30)."""
    if artifacts is None:
        return None
    rows = sorted((meta.relpath, meta.sha256 or "") for meta in artifacts)
    review_json = json.dumps(
        {"verdict": review[0], "findings": [finding.to_json() for finding in review[1]]}
        if review
        else None,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256("\n".join(f"{path}:{digest}" for path, digest in rows) + "\n" + _sha256(review_json))


# ---- 1.6.3 repo-level readers ----------------------------------------------- #


class AidlcReader:
    """Bounded, containment-checked, uncached reads of one repository's AI-DLC workspace (FORMAT-NOTES §11.1)."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    # -- layout -------------------------------------------------------------
    @staticmethod
    def intents_rel(space: str) -> str:
        _check_name(C.SPACE_RE, space, "space name", "space")
        return f"{WORKSPACE_DIRNAME}/{SPACES_DIRNAME}/{space}/{INTENTS_DIRNAME}"

    @classmethod
    def record_rel(cls, space: str, intent_dir: str) -> str:
        _check_name(C.INTENT_DIR_RE, intent_dir, "intent directory", "intent_dir")
        return f"{cls.intents_rel(space)}/{intent_dir}"

    def detect_harness_dirs(self, repo: Path) -> list[HarnessDir]:
        """Every harness tree in the repo, read from its own files, never run (FORMAT-NOTES §11.7)."""
        from .repo_registry import HarnessDir  # deferred: keeps the parser layer import-cycle free

        found: list[HarnessDir] = []
        for name in C.HARNESS_DIRS:
            try:
                tools = security.resolve_inside(repo, f"{name}/{TOOLS_DIRNAME}")
            except StudioError:
                continue
            if not tools.is_dir() or not any((tools / marker).exists() for marker in HARNESS_MARKERS):
                continue
            harness = _json_dict(security.bounded_read(tools / HARNESS_JSON_NAME, C.MAX_JSON_BYTES)) or {}
            version_text = security.bounded_text(tools / ENGINE_VERSION_NAME, C.MAX_ARTIFACT_RENDER_BYTES)
            version_match = C.ENGINE_VERSION_RE.search(version_text) if version_text else None
            graph = _json_loads(security.bounded_read(tools / STAGE_GRAPH_NAME, C.MAX_JSON_BYTES))
            stage_count = len(graph) if isinstance(graph, list) else None
            found.append(HarnessDir(
                dir=name, harness_name=harness.get("name") or None,
                rules_subdir=harness.get("rulesSubdir") or None,
                engine_version=version_match.group(1) if version_match else None,
                engine_state_version=self._engine_state_version(tools), stage_count=stage_count,
                has_utility=security.is_regular_file(tools / ENGINE_UTILITY_NAME),
            ))
        return found

    @staticmethod
    def _engine_state_version(tools: Path) -> int | None:
        """The state version the installed engine writes, or ``None``; never a guess (FORMAT-NOTES §11.7)."""
        lib = security.bounded_text(tools / ENGINE_LIB_NAME, C.MAX_ENGINE_SOURCE_BYTES)
        if lib:
            match = C.ENGINE_STATE_VERSION_RE.search(lib)
            if match:
                return int(match.group(1))
        utility = security.bounded_text(tools / ENGINE_UTILITY_NAME, C.MAX_ENGINE_SOURCE_BYTES)
        if utility:
            match = _TEMPLATE_STATE_VERSION_RE.search(utility)
            if match:
                return int(match.group(1))
        return None

    def layout(self, repo: Path) -> str | None:
        """``"spaces"``, ``"legacy"`` or ``None``, by the engine's own ``needsFlatMigration`` test."""
        spaces_root = security.resolve_inside(repo, f"{WORKSPACE_DIRNAME}/{SPACES_DIRNAME}")
        if spaces_root.is_dir() and any(
            entry.is_dir() and not entry.is_symlink() for entry in _iterdir(spaces_root)
        ):
            return "spaces"
        if security.is_regular_file(security.resolve_inside(repo, C.LEGACY_STATE_REL)):
            return "legacy"
        return None

    def active_space(self, repo: Path) -> str:
        """The active space, ``default`` when the cursor is absent (2.1.1 has none) or unreadable."""
        data = security.bounded_read(
            security.resolve_inside(repo, ACTIVE_SPACE_REL), C.MAX_TEXT_PREVIEW_BYTES
        )
        value = parse_cursor(data, default=DEFAULT_SPACE) or DEFAULT_SPACE
        return value if C.SPACE_RE.match(value) else DEFAULT_SPACE

    def list_spaces(self, repo: Path) -> list[str]:
        """Space directories that actually hold a workspace (``intents/`` or ``memory/``)."""
        spaces_root = security.resolve_inside(repo, f"{WORKSPACE_DIRNAME}/{SPACES_DIRNAME}")
        out: list[str] = []
        for entry in _iterdir(spaces_root):
            if entry.is_symlink() or not entry.is_dir() or not C.SPACE_RE.match(entry.name):
                continue
            if (entry / INTENTS_DIRNAME).is_dir() or (entry / "memory").is_dir():
                out.append(entry.name)
        return sorted(out)

    def active_intent(self, repo: Path, space: str) -> tuple[str | None, bool]:
        """``(cursor value, dangling)``: dangling if it names no record with a state file (FORMAT-NOTES §11.5)."""
        path = security.resolve_inside(repo, f"{self.intents_rel(space)}/{ACTIVE_INTENT_FILENAME}")
        value = parse_cursor(security.bounded_read(path, C.MAX_TEXT_PREVIEW_BYTES), default=None)
        if value is None:
            return (None, False)
        if not C.INTENT_DIR_RE.match(value):
            return (None, True)
        state = security.resolve_inside(repo, f"{self.record_rel(space, value)}/{STATE_FILENAME}")
        return (value, not security.is_regular_file(state))

    def list_intent_dirs(self, repo: Path, space: str) -> list[str]:
        """Record directories that hold a state file (the engine's ``listIntentDirs`` rule), sorted, capped."""
        root = security.resolve_inside(repo, self.intents_rel(space))
        out: list[str] = []
        for entry in _iterdir(root):
            if len(out) >= C.MAX_INTENTS_PER_REPO:
                break
            if entry.is_symlink() or not entry.is_dir() or entry.name.startswith("."):
                continue
            if not C.INTENT_DIR_RE.match(entry.name):
                continue
            if security.is_regular_file(entry / STATE_FILENAME):
                out.append(entry.name)
        return sorted(out)

    def registry(self, repo: Path, space: str) -> list[IntentRow]:
        path = security.resolve_inside(repo, f"{self.intents_rel(space)}/{INTENTS_REGISTRY_FILENAME}")
        return parse_intents_json(security.bounded_read(path, C.MAX_JSON_BYTES) or b"")

    # -- record files -------------------------------------------------------
    def read_state(self, repo: Path, space: str, intent_dir: str) -> tuple[Snapshot, StateFile] | None:
        """The state file, twice-stat'd, plus its parse; ``None`` when absent, ``too_large`` when oversized."""
        snap = stable_read(repo, f"{self.record_rel(space, intent_dir)}/{STATE_FILENAME}", C.MAX_STATE_BYTES)
        if snap is None:
            return None
        return (snap, parse_state_file(snap.text))

    def read_directive(
        self, repo: Path, space: str, intent_dir: str, state_sha256: str
    ) -> Directive | None:
        path = record_file(repo, self.record_rel(space, intent_dir), DIRECTIVE_FILENAME)
        data = security.bounded_read(path, C.MAX_JSON_BYTES)
        if data is None:
            return None
        directive = parse_directive(data, state_sha256)
        if directive is not None and path.parent.name == ENGINE_DIRNAME:
            directive = replace(directive, marker_name=f"{ENGINE_DIRNAME}/{path.name}")
        return directive

    def read_audit(
        self, repo: Path, space: str, intent_dir: str, *, tail_bytes: int = C.MAX_AUDIT_HISTORY_BYTES
    ) -> AuditBundle:
        """Every ``audit/*.md`` shard, merged; over the history budget ``complete=False`` (FORMAT-NOTES §11.6)."""
        audit_dir = security.resolve_inside(repo, f"{self.record_rel(space, intent_dir)}/{AUDIT_DIRNAME}")
        record_rel = self.record_rel(space, intent_dir)
        shards: list[ShardMeta] = []
        per_shard: list[list[AuditEvent]] = []
        complete = True
        try:
            paths = sorted((p for p in audit_dir.iterdir() if p.suffix == ".md"), key=lambda p: p.name)
        except FileNotFoundError:
            paths = []
        except OSError:
            paths, complete = [], False
        if len(paths) > C.MAX_AUDIT_SHARDS:
            complete = False
        readable: list[tuple[Path, os.stat_result]] = []
        for path in paths[: C.MAX_AUDIT_SHARDS]:
            if not security.is_regular_file(path):
                complete = False
                continue
            try:
                readable.append((path, path.stat()))
            except OSError:
                complete = False
        limits = _audit_read_limits(
            [stat.st_size for _, stat in readable], [stat.st_mtime_ns for _, stat in readable],
            tail_bytes, C.MAX_AUDIT_HISTORY_BYTES,
        )
        spare = max(0, C.MAX_AUDIT_HISTORY_BYTES - sum(limits))
        for (path, stat), limit in zip(readable, limits):
            truncated = stat.st_size > limit
            if truncated:
                data: bytes | None = security.tail_read(path, limit) if limit > 0 else b""
            else:
                # The engine may append after the stat: let a whole shard grow into the unclaimed budget
                # rather than refuse it and lose every event in it (read as missing human turns).
                data = security.bounded_read(path, limit + spare)
                if data is None:
                    truncated = True
                    data = security.tail_read(path, limit) if limit > 0 else b""
                else:
                    spare = max(0, spare - max(0, len(data) - limit))
            # Audit-backed questions treat a missing answer as proof, so an unreadable or moving shard is partial.
            try:
                after = path.stat()
                if (stat.st_size, stat.st_mtime_ns, stat.st_ino) != (
                    after.st_size, after.st_mtime_ns, after.st_ino
                ):
                    complete = False
            except OSError:
                complete = False
            if data is None or (not truncated and len(data) != stat.st_size):
                complete = False
            data = data or b""
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                complete = False
            rel = f"{record_rel}/{AUDIT_DIRNAME}/{path.name}"
            # The index is the shard's position in ``shards``: the reconciler places a boundary by it.
            per_shard.append(parse_audit_shard(_text(data), path.name, len(shards)))
            shards.append(ShardMeta(rel, stat.st_size, stat.st_mtime_ns, truncated))
        try:
            if sorted(p.name for p in audit_dir.iterdir() if p.suffix == ".md") != [p.name for p in paths]:
                complete = False
        except FileNotFoundError:
            complete = complete and not paths
        except OSError:
            complete = False
        for shard in shards:
            try:
                final = (repo / shard.relpath).stat()
                if (final.st_size, final.st_mtime_ns) != (shard.size, shard.mtime_ns):
                    complete = False
            except OSError:
                complete = False
        return AuditBundle(
            events=merge_audit(per_shard),
            shards=shards,
            complete=complete and not any(shard.truncated for shard in shards),
        )

    # -- engine data --------------------------------------------------------
    def read_stage_graph(self, repo: Path, engine_dir: str) -> list[StageNode]:
        path = security.resolve_inside(repo, f"{engine_dir}/{STAGE_GRAPH_REL}")
        return parse_stage_graph(security.bounded_read(path, C.MAX_JSON_BYTES) or b"")

    def read_scope_grid(self, repo: Path, engine_dir: str) -> dict[str, dict[str, str]]:
        path = security.resolve_inside(repo, f"{engine_dir}/{SCOPE_GRID_REL}")
        return parse_scope_grid(security.bounded_read(path, C.MAX_JSON_BYTES) or b"")

    def read_scopes(self, repo: Path, engine_dir: str) -> list[ScopeMeta]:
        root = security.resolve_inside(repo, f"{engine_dir}/{SCOPES_DIRNAME}")
        out: list[ScopeMeta] = []
        for entry in sorted(_iterdir(root), key=lambda path: path.name):
            if entry.suffix != ".md" or not security.is_regular_file(entry):
                continue
            text = security.bounded_text(entry, C.MAX_ARTIFACT_RENDER_BYTES)
            if not text:
                continue
            meta = parse_scope_frontmatter(text, entry.name)
            if meta is not None:
                out.append(meta)
        return out

    def read_workspace_signals(self, repo: Path) -> WorkspaceSignals:
        """Top-level names, manifests and one README excerpt, bounded by construction (FORMAT-NOTES §11.7)."""
        cap = C.MAX_WORKSPACE_ENTRIES
        eligible = (
            entry for entry in _iterdir(repo)
            if not entry.is_symlink() and not entry.name.startswith(".")
        )
        # One more than the cap, so "truncated" is a fact about the directory and not a guess.
        kept = heapq.nsmallest(cap + 1, eligible, key=lambda path: path.name)
        truncated = len(kept) > cap
        names = [f"{entry.name}/" if entry.is_dir() else entry.name for entry in kept[:cap]]

        def plain_file(name: str) -> Path | None:
            if (repo / name).is_symlink():
                return None
            try:
                path = security.resolve_inside(repo, name)
            except StudioError:
                return None
            return path if security.is_regular_file(path) else None

        manifests = [name for name in WORKSPACE_MANIFESTS if plain_file(name) is not None]
        readme: str | None = None
        for name in README_NAMES:
            path = plain_file(name)
            if path is not None:
                readme = security.bounded_text(path, C.MAX_ARTIFACT_RENDER_BYTES) or None
                break
        return WorkspaceSignals(top_level=tuple(names), manifests=tuple(manifests), readme_excerpt=readme,
                                truncated=truncated)

    def read_stage_file(
        self, repo: Path, engine_dir: str, phase: str, slug: str
    ) -> StageFileMeta | None:
        if not C.STAGE_SLUG_RE.match(slug) or phase not in C.PHASES:
            return None
        path = security.resolve_inside(repo, f"{engine_dir}/{STAGES_REL}/{phase}/{slug}.md")
        text = security.bounded_text(path, C.MAX_ARTIFACT_RENDER_BYTES)
        if text is None:
            return None
        return parse_stage_file(text, slug)

    # -- stages, questions, units ------------------------------------------
    def stage_dir(
        self, space: str, intent_dir: str, phase: str, slug: str, unit: str | None
    ) -> str:
        """Where a stage's artifacts live: under the unit only for ``PER_UNIT_STAGES`` (FORMAT-NOTES §8)."""
        record = self.record_rel(space, intent_dir)
        if unit and slug in _PER_UNIT_STAGE_SET:
            _check_name(C.UNIT_NAME_RE, unit, "unit name", "unit")
            return f"{record}/construction/{unit}/{slug}"
        return f"{record}/{phase}/{slug}"

    def read_questions(self, repo: Path, stage_dir_rel: str, slug: str) -> QuestionsFile | None:
        rel = f"{stage_dir_rel}/{slug}{QUESTIONS_SUFFIX}"
        path = security.resolve_inside(repo, rel)
        data = security.bounded_read(path, C.MAX_QUESTIONS_BYTES)
        if data is None:
            return None
        return parse_questions_file(_text(data), rel, hashlib.sha256(data).hexdigest())

    def list_units(self, repo: Path, space: str, intent_dir: str) -> list[str]:
        """Units of work: ``construction/`` directories that are not stages, as the state file names them."""
        record = self.record_rel(space, intent_dir)
        known = set(CONSTRUCTION_STAGE_SLUGS)
        state = self.read_state(repo, space, intent_dir)
        if state is not None:
            known |= {row.slug for row in state[1].stages}
        root = security.resolve_inside(repo, f"{record}/construction")
        out: list[str] = []
        for entry in _iterdir(root):
            if entry.is_symlink() or not entry.is_dir() or entry.name.startswith("."):
                continue
            if entry.name in known or not C.UNIT_NAME_RE.match(entry.name):
                continue
            out.append(entry.name)
        return sorted(out)

    # -- artifacts ----------------------------------------------------------
    def list_artifacts(self, repo: Path, space: str, intent_dir: str) -> ArtifactList:
        """A record's regular files, capped by count and time, hashed only when few (FORMAT-NOTES §11.10)."""
        record_rel = self.record_rel(space, intent_dir)
        root = security.resolve_inside(repo, record_rel)
        deadline = self._clock.monotonic() + C.REPO_SCAN_DEADLINE_SECS
        found: list[tuple[str, Path, os.stat_result]] = []
        truncated = False
        reason: str | None = None
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            if self._clock.monotonic() > deadline:
                truncated, reason = True, "deadline"
                break
            dirnames[:] = sorted(
                name for name in dirnames
                if not name.startswith(".") and not Path(dirpath, name).is_symlink()
            )
            for name in sorted(filenames):
                if name.startswith("."):
                    continue
                if len(found) >= C.MAX_ARTIFACTS_PER_INTENT:
                    truncated, reason = True, "cap"
                    break
                path = Path(dirpath, name)
                if security.is_secret_file(path) or not security.is_regular_file(path):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                rel = f"{record_rel}/{path.relative_to(root).as_posix()}"
                found.append((rel, path, stat))
            if truncated:
                break
        found.sort(key=lambda row: row[0])
        hash_all = len(found) <= WHOLE_RECORD_HASH_LIMIT
        metas = [
            _artifact_meta(rel, stat, record_rel, security.sha256_file(path) if hash_all else None)
            for rel, path, stat in found
        ]
        return ArtifactList(metas, truncated=truncated, reason=reason)

    def read_artifact(self, repo: Path, relpath: str) -> Snapshot | None:
        return stable_read(repo, relpath, C.MAX_ARTIFACT_RENDER_BYTES)

    def read_review_record(
        self, repo: Path, space: str, intent_dir: str, completion: AuditEvent
    ) -> tuple[str | None, ReviewFindings]:
        """The review a 2.11 ``REVIEW_COMPLETED`` row names, read from beside the intent record.

        A record that is absent (AI-DLC's block keeps ``.aidlc-engine/`` out of git, so a fresh clone has none) or
        is not that review still leaves the row's verdict, with findings Studio could not list: a reviewer ran.
        """
        fallback = ((completion.fields.get("Verdict") or "").upper() or None, ReviewFindings(findings_parsed=False))
        path = completion.fields.get("Review Record") or ""
        if not is_review_record_path(path):
            return fallback
        snap = stable_read(repo, f"{self.record_rel(space, intent_dir)}/{path}", C.MAX_JSON_BYTES)
        review = parse_review_record(snap.bytes, completion) if snap is not None else None
        return review if review is not None else fallback

    def stage_artifacts(
        self, repo: Path, snap_stage_dir_rel: str, node: StageNode | None
    ) -> list[ArtifactMeta]:
        """ONE stage's artifacts, always hashed: the set ``evidence_digest`` compares (FORMAT-NOTES §11.10)."""
        root = security.resolve_inside(repo, snap_stage_dir_rel)
        record_rel = _record_prefix(snap_stage_dir_rel)
        names: list[str] = []
        for entry in sorted(_iterdir(root), key=lambda path: path.name):
            if entry.name.startswith(".") or not security.is_regular_file(entry):
                continue
            if security.is_secret_file(entry):
                continue
            names.append(entry.name)
        if node is not None:
            for declared in (*node.produces, *node.optional_produces):
                for candidate in (f"{declared}.md", f"{declared}.json"):
                    if candidate in names:
                        continue
                    path = root / candidate
                    if security.is_regular_file(path):
                        names.append(candidate)
        metas: list[ArtifactMeta] = []
        for name in sorted(set(names)):
            path = root / name
            try:
                stat = path.stat()
            except OSError:
                continue
            meta = _artifact_meta(
                f"{snap_stage_dir_rel}/{name}", stat, record_rel, _capped_digest(path, stat.st_size)
            )
            if node is not None and meta.stage is None:
                meta = replace(meta, phase=node.phase, stage=node.slug,
                               kind=_artifact_kind(name, node.slug, meta.relpath))
            metas.append(meta)
        return metas

    # -- markers, baselines, boundary --------------------------------------
    def read_markers(self, repo: Path, space: str, intent_dir: str) -> Markers:
        """The runtime markers around a record. ``.aidlc-goal-stop`` is presence only: AI-DLC never writes it."""
        record = self.record_rel(space, intent_dir)
        recovery = record_file(repo, record, RECOVERY_FILENAME)
        recovery_text = security.bounded_text(recovery, C.MAX_TEXT_PREVIEW_BYTES)
        hooks: dict[str, str] = {}
        hooks_dir = record_file(repo, record, HOOKS_HEALTH_DIRNAME)
        for entry in sorted(_iterdir(hooks_dir), key=lambda path: path.name):
            if not entry.name.endswith(HOOKS_HEALTH_SUFFIX) or not security.is_regular_file(entry):
                continue
            value = security.bounded_text(entry, C.MAX_TEXT_PREVIEW_BYTES) or ""
            hooks[entry.name[: -len(HOOKS_HEALTH_SUFFIX)]] = value.strip()
        return Markers(
            human_turn_mtime_ns=_mtime_ns(record_file(repo, record, HUMAN_TURN_FILENAME)),
            engine_touch_mtime_ns=_mtime_ns(record_file(repo, record, ENGINE_TOUCH_FILENAME)),
            recovery=parse_recovery_breadcrumb(recovery_text) if recovery_text is not None else None,
            goal_stop_present=security.is_regular_file(
                security.resolve_inside(repo, f"{record}/{GOAL_STOP_FILENAME}")
            ),
            hooks_health=hooks,
            stop_block_count=_json_dict(
                security.bounded_read(record_file(repo, record, STOP_HOOK_REL), C.MAX_JSON_BYTES)
            ),
            turn_counter=_turn_counter(repo),
            compose_pending=(security.resolve_inside(repo, COMPOSE_PENDING_REL)).exists(),
            reviewer_dispatch=_json_dict(
                security.bounded_read(record_file(repo, record, REVIEWER_DISPATCH_FILENAME), C.MAX_JSON_BYTES)
            ),
        )

    def presence_baseline(
        self,
        repo: Path,
        space: str,
        intent_dir: str,
        audit: AuditBundle,
        state_sha256: str | None,
    ) -> PresenceBaseline:
        """Human-presence evidence at submit time (C23): six facts, each alone forgeable (FORMAT-NOTES §11.10)."""
        record = self.record_rel(space, intent_dir)
        newest: tuple[int, str] | None = None
        sizes: dict[str, int] = {}
        for shard in audit.shards:
            sizes[shard.relpath] = shard.size
            if newest is None or (shard.mtime_ns, shard.relpath) > newest:
                newest = (shard.mtime_ns, shard.relpath)
        tail = b""
        if newest is not None:
            tail = security.tail_read(security.resolve_inside(repo, newest[1]), PRESENCE_TAIL_BYTES)
        return PresenceBaseline(
            human_turn_events=sum(1 for event in audit.events if event.event == C.HUMAN_PRESENCE_EVENT),
            human_turn_events_partial=not audit.complete,
            human_turn_mtime_ns=_mtime_ns(record_file(repo, record, HUMAN_TURN_FILENAME)),
            turn_counter=_turn_counter(repo),
            shard_sizes=sizes,
            audit_tail_sha256=hashlib.sha256(tail).hexdigest(),
            state_sha256=state_sha256,
            taken_at=self._clock.iso(),
        )

    def stable_boundary_inputs(self, repo: Path, space: str, intent_dir: str) -> BoundaryInputs:
        """State, audit, directive and questions as one moment; the state is re-read last (FORMAT-NOTES §11.10)."""
        taken_at = self._clock.iso()
        first = self.read_state(repo, space, intent_dir)
        if first is None:
            return BoundaryInputs(space=space, intent_dir=intent_dir, state_snap=None, state=None,
                                  audit=self.read_audit(repo, space, intent_dir), directive=None, questions=None,
                                  stage=None, stable=False, taken_at=taken_at)
        snap, state = first
        audit = self.read_audit(repo, space, intent_dir)
        directive = self.read_directive(repo, space, intent_dir, snap.sha256)
        stage = directive.stage if directive and directive.matches_state else state.current_stage
        unit = (directive.unit if directive and directive.matches_state else None) or state.active_unit
        questions, unit_ambiguous = self._questions_for_stage(
            repo, space, intent_dir, state, stage, unit, audit=audit, directive=directive
        )
        again = self.read_state(repo, space, intent_dir)
        stable = (again is not None and not snap.unstable and not again[0].unstable
                  and again[0].sha256 == snap.sha256)
        return BoundaryInputs(space=space, intent_dir=intent_dir, state_snap=snap, state=state, audit=audit,
                              directive=directive, questions=questions, stage=stage, stable=stable,
                              taken_at=taken_at, unit_ambiguous=unit_ambiguous)

    def _questions_for_stage(
        self,
        repo: Path,
        space: str,
        intent_dir: str,
        state: StateFile,
        stage: str | None,
        unit: str | None,
        *,
        audit: AuditBundle | None = None,
        directive: Directive | None = None,
    ) -> tuple[QuestionsFile | None, bool]:
        """``(questions, unit_ambiguous)`` for one stage; never a coin flip between units (FORMAT-NOTES §11.10)."""
        if not stage or not C.STAGE_SLUG_RE.match(stage):
            return (None, False)
        row = state.row(stage)
        # A matching v2 zero-unit Code Generation directive identifies the stage-level record.
        # Without that proof, a missing unit remains unknown and all existing ambiguity rules apply.
        zero_unit_run = bool(
            directive and directive.matches_state and directive.stage == stage
            and directive.zero_unit_run and unit is None and state.active_unit is None
        )

        def file_or_audit(found: QuestionsFile) -> QuestionsFile:
            # Pending file content owns the interaction, including unsupported answer sections
            # that require the conversation. Closed content can yield to a later audit decision
            # only with a current, file-bound checkpoint receipt.
            if found.pending_count or found.pending_checkpoint:
                return found
            if (
                audit is None or row is None or row.state in C.DONE_STAGE_STATES
                or (stage in _PER_UNIT_STAGE_SET and unit is None and not zero_unit_run)
            ):
                return found
            generation, readable = self._question_claim_generation(repo, space, intent_dir, unit, audit)
            if not readable:
                return found
            pending = audit_questions(
                audit, self.record_rel(space, intent_dir), stage, unit, generation=generation,
            )
            if pending is None:
                return found
            evidence: dict[str, Any] = {
                "audit": pending.sha256, "file": found.relpath, "file_sha256": found.sha256,
            }
            receipts: list[AuditEvent] = []
            if found.questions or found.summary_confirmation or found.plan_approval or found.assumption_confirmation:
                try:
                    data = security.bounded_read(
                        security.resolve_inside(repo, found.relpath), C.MAX_QUESTIONS_BYTES,
                    )
                except (OSError, StudioError):
                    return found
                receipts = _closed_question_receipts(found, pending, audit, data)
                if receipts is None:
                    return found
                evidence["closed_receipts"] = [_audit_event_identity(e) for e in receipts]
            # Compare-and-submit must also reject edits to the file that led to this fallback.
            return replace(pending, sha256=_sha256(json.dumps(
                evidence, sort_keys=True, ensure_ascii=False,
            )), closed_file=found if receipts else None, closed_file_receipts=tuple(receipts))

        probed: list[str] = []
        if stage in _PER_UNIT_STAGE_SET and not zero_unit_run:
            units = [unit] if unit else self.list_units(repo, space, intent_dir)
            per_unit: list[QuestionsFile] = []
            for candidate_unit in units:
                directory = self.stage_dir(space, intent_dir, "construction", stage, candidate_unit)
                probed.append(f"{directory}/{stage}{QUESTIONS_SUFFIX}")
                found = self.read_questions(repo, directory, stage)
                if found is not None:
                    per_unit.append(found)
            if unit is None and len(per_unit) > 1:
                return (None, True)
            if per_unit:
                return (file_or_audit(per_unit[0]), False)
        phases = [row.phase] if row and row.phase in C.PHASES else list(C.PHASES)
        for phase in phases:
            directory = self.stage_dir(space, intent_dir, phase, stage, None)
            probed.append(f"{directory}/{stage}{QUESTIONS_SUFFIX}")
            found = self.read_questions(repo, directory, stage)
            if found is not None:
                return (file_or_audit(found), False)
        if (
            audit is not None and row is not None and row.state not in C.DONE_STAGE_STATES
            and (stage not in _PER_UNIT_STAGE_SET or unit is not None or zero_unit_run)
        ):
            # Read failure is not absence: an existing file that could not be read forbids fallback.
            for rel in probed:
                try:
                    (repo / rel).lstat()
                except FileNotFoundError:
                    continue
                except OSError:
                    return (None, False)
                return (None, False)
            generation, readable = self._question_claim_generation(repo, space, intent_dir, unit, audit)
            if readable:
                return (
                    audit_questions(
                        audit, self.record_rel(space, intent_dir), stage, unit, generation=generation
                    ),
                    False,
                )
        return (None, False)

    def _question_claim_generation(
        self, repo: Path, space: str, intent_dir: str, unit: str | None, audit: AuditBundle,
    ) -> tuple[str | None, bool]:
        """Local claim evidence scopes Unit questions; never query or mutate the claim registry."""
        generation = _audit_question_generation(audit, unit)
        if unit is None:
            return generation, True
        try:
            row = match_intent_row(self.registry(repo, space), intent_dir)
        except StudioError:
            return None, False
        uuid = row.uuid if row is not None else None
        values: list[int] = [int(generation)] if generation else []
        for name in (".aidlc-unit-scope.json", ".aidlc-claim-generations.json"):
            try:
                path = security.resolve_inside(repo, f"{WORKSPACE_DIRNAME}/{name}")
                path.lstat()
            except FileNotFoundError:
                continue
            except (OSError, StudioError):
                return None, False
            if uuid is None:
                # A claim map is keyed by intent UUID. Missing identity is not a legacy claim.
                return None, False
            data = _json_dict(security.bounded_read(path, C.MAX_JSON_BYTES))
            if data is None:
                return None, False
            if name == ".aidlc-unit-scope.json":
                if data.get("space") != space or data.get("intent_uuid") != uuid:
                    continue
                if data.get("unit") != unit:
                    return None, False
                value = data.get("generation")
            else:
                value = data.get(f"{space}/{uuid}/{unit}")
                if value is None:
                    continue
            if type(value) is not int or value < 1:
                return None, False
            values.append(value)
        return (str(max(values)) if values else None), True


# ---- module-private helpers ------------------------------------------------- #


def _check_name(pattern: re.Pattern[str], value: str, what: str, key: str) -> None:
    if not pattern.match(value or ""):
        raise StudioError("bad_path", f"{what} is not a legal directory name", details={key: value})


def _turn_counter(repo: Path) -> int | None:
    text = security.bounded_text(security.resolve_inside(repo, TURN_COUNTER_REL), C.MAX_TEXT_PREVIEW_BYTES)
    return _int_or_none(text)


def _iterdir(path: Path) -> list[Path]:
    """``iterdir`` that yields nothing for a missing or unreadable directory."""
    try:
        return list(path.iterdir())
    except OSError:
        return []


def _mtime_ns(path: Path) -> int | None:
    if not security.is_regular_file(path):
        return None
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _json_dict(data: bytes | None) -> dict | None:
    payload = _json_loads(data)
    return payload if isinstance(payload, dict) else None


def _capped_digest(path: Path, size: int) -> str | None:
    """``sha256`` of a file, or of its first cap bytes plus its size, so growth past the cap still shows."""
    if size <= C.MAX_ARTIFACT_RENDER_BYTES:
        return security.sha256_file(path)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            digest.update(handle.read(C.MAX_ARTIFACT_RENDER_BYTES))
    except OSError:
        return None
    digest.update(b"\x00")
    digest.update(str(size).encode("ascii"))
    return digest.hexdigest()


def _record_prefix(rel: str) -> str:
    """The ``aidlc/spaces/<s>/intents/<d>`` prefix of a repo-relative path, or ``""``."""
    parts = rel.split("/")
    if len(parts) >= 5 and parts[0] == WORKSPACE_DIRNAME and parts[1] == SPACES_DIRNAME:
        return "/".join(parts[:5])
    return ""


def _split_stage_path(rel: str, record_rel: str) -> tuple[str | None, str | None, str | None]:
    """``(phase, stage, unit)`` of a path; a non-stage ``construction/`` child is a unit (FORMAT-NOTES §8)."""
    if not record_rel or not rel.startswith(record_rel + "/"):
        return (None, None, None)
    parts = rel[len(record_rel) + 1:].split("/")
    if len(parts) < 2 or parts[0] not in C.PHASES:
        return (None, None, None)
    phase = parts[0]
    if phase == "construction" and len(parts) >= 3 and parts[1] not in CONSTRUCTION_STAGE_SLUGS:
        return (phase, parts[2], parts[1])
    return (phase, parts[1], None)


def _artifact_kind(name: str, stage: str | None, relpath: str) -> str:
    lowered = name.lower()
    if lowered.endswith(QUESTIONS_SUFFIX):
        return "questions"
    if lowered == "memory.md":
        return "memory"
    if lowered.startswith("traceability."):
        return "traceability"
    if "/contributions/" in relpath:
        return "contribution"
    if "review" in lowered:
        return "review"
    return "artifact" if stage else "other"


def _artifact_meta(
    rel: str, stat: os.stat_result, record_rel: str, digest: str | None
) -> ArtifactMeta:
    name = rel.rsplit("/", 1)[-1]
    phase, stage, unit = _split_stage_path(rel, record_rel)
    suffix = Path(name).suffix.lower()
    return ArtifactMeta(
        artifact_id=hashlib.sha1(rel.encode("utf-8")).hexdigest(), relpath=rel, name=name, stage=stage,
        phase=phase, unit=unit, size=stat.st_size, mtime=_iso(stat.st_mtime), sha256=digest,
        kind=_artifact_kind(name, stage, rel),
        renderable=stat.st_size <= C.MAX_ARTIFACT_RENDER_BYTES and suffix in TEXT_EXTENSIONS,
    )
