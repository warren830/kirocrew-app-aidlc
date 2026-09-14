"""Everything Studio knows about an AI-DLC workspace, read from the bytes AI-DLC wrote.

This is the product's eyes. Every claim the UI makes about a repository — which stage is open, whether
a gate is waiting, what the human is being asked — starts here, and nothing downstream can recover
from a misread: a confidently wrong parse becomes a confidently wrong card, and a card is what sends a
prompt into someone's session.

So the module obeys four rules, each of which exists because of a real file in ``tests/fixtures``:

1. **Tolerate the writer, never guess for it.** State Version 7 and 8, 32- and 33-stage graphs, three
   separator spellings, a value that is empty with a trailing space, a ``Skeleton Stance`` row with no
   blank line before the next section, a stage-progress comment that lists four checkbox states while
   the file uses six, a literal ``Per unit: [TBD]`` line in the middle of the rows. All of that parses.
   What does *not* happen is inference: an unsupported ``State Version`` is reported as the number on
   disk, an unknown audit event keeps its name, and a field that is absent is ``None`` rather than a
   default that looks like data.
2. **Two reads or no authority.** ``stable_read`` stats, reads, stats again. A file that moved between
   the two stats is returned with ``unstable=True`` and can authorise nothing (FR-ACT-009); the caller
   re-reads instead of choosing one of the two versions.
3. **Bounded and contained.** Every path is repo-relative and resolved through
   ``security.resolve_inside``; every read is capped; audit shards — append-only and unbounded — are
   the one thing tail-read, and a tail-read shard marks the bundle ``complete=False`` so evidence built
   on partial history says so.
4. **Read only.** No writes, no subprocesses, no ``.aidlc-steering-token-key``, no ``usage-ledger.json``,
   and ``.aidlc-goal-stop`` is surfaced as a presence flag with no authority at all (nothing in AI-DLC
   writes it — ``04 §3.11``).

Threading: synchronous and blocking. Callers reach it with ``await asyncio.to_thread(...)`` (or the
reconciler's private scan pool for whole-repo walks); nothing here may run on the gateway loop.
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

if TYPE_CHECKING:  # annotations only — the reader must stay importable on its own
    from .constants import Clock
    from .repo_registry import HarnessDir

# --------------------------------------------------------------------------- #
# on-disk names (the engine's own layout, 04 §2/§3)
# --------------------------------------------------------------------------- #

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
#: Marker schema versions ``parse_directive`` trusts. 2.6.2 and earlier wrote ``1``; 2.7.1 writes ``2`` at
#: the same path but leaves an existing ``1`` marker at ``1`` (``refreshActiveDirectiveMarker``), so both
#: shapes stay live in the field and a reader that knew only one would lose the execution cursor.
DIRECTIVE_VERSIONS = frozenset({1, 2})
#: The ``kind`` values a version-2 marker may carry. Anything else is a marker shape this Studio has never
#: seen, and believing it names a running stage would point every card at work nobody started.
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

#: Build manifests whose *presence* says what kind of project a repository is (§1.6.3, FR-ADV-003). Names
#: only — ``read_workspace_signals`` reports which of these exist and never opens one.
WORKSPACE_MANIFESTS: tuple[str, ...] = (
    "package.json",
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "composer.json",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "CMakeLists.txt",
    "mix.exs",
    "pubspec.yaml",
    "tsconfig.json",
)
#: The one file ``read_workspace_signals`` reads an excerpt of, first hit wins.
README_NAMES: tuple[str, ...] = ("README.md", "README", "readme.md", "README.rst", "README.txt")

#: A ``<harness>/tools`` directory is an engine tree only when one of these is inside it. Mirrors
#: ``repo_registry.HARNESS_MARKERS``: a user's own ``.cursor/tools`` folder is not an AI-DLC install.
HARNESS_MARKERS = (ENGINE_VERSION_NAME, HARNESS_JSON_NAME, ENGINE_UTILITY_NAME)

#: The eleven scopes AI-DLC ships (``classic`` and ``express`` arrived in 2.7.1). A
#: ``.kiro/scopes/aidlc-<name>.md`` outside this set was composed for the project (DevDelta has one),
#: which the installer must never overwrite — hence ``project_owned``. A shipped scope missing from here
#: is reported to the wizard and the Advisor as "defined by this project", which is false evidence.
STOCK_SCOPES = frozenset(
    {
        "bugfix", "classic", "enterprise", "express", "feature", "infra", "mvp", "poc", "refactor",
        "security-patch", "workshop",
    }
)

#: Construction stage slugs known to every engine generation. Used only as the fallback for unit
#: detection when the intent's own state file cannot be read: a directory directly under
#: ``construction/`` is a unit of work unless it is a stage (FORMAT-NOTES §8).
CONSTRUCTION_STAGE_SLUGS = frozenset(set(C.PER_UNIT_STAGES) | {"build-and-test", "ci-pipeline"})

#: Above this many files a whole-record listing stops hashing (§1.8 ``ArtifactMeta.sha256``). Card
#: evidence never comes from that listing — ``stage_artifacts`` always hashes — so the cap costs no
#: authority, and it keeps a 2 000-file record from turning a page load into a hashing job.
WHOLE_RECORD_HASH_LIMIT = 200

#: How much of the newest audit shard the presence baseline digests. Enough to cover the rows a turn
#: appends, small enough that recording a baseline costs one page-sized read (§1.6.4/C23).
PRESENCE_TAIL_BYTES = 4096

#: Extensions Studio is willing to render as text. Everything else is offered as metadata only, so a
#: binary that happens to sit in a record dir can never be splatted into the UI.
TEXT_EXTENSIONS = frozenset(
    {
        ".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".csv", ".tsv",
        ".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs", ".java", ".rb", ".sh", ".sql", ".html",
        ".css", ".xml", ".mmd", ".puml", ".log", ".diff", ".patch",
    }
)

#: ``- **State Version**: 7`` inside a state file *or* inside an older engine's state template, which
#: is the only place a pre-2.5 build records the version it writes. One pattern shared with
#: ``repo_registry``, which reads the same line out of a template.
_TEMPLATE_STATE_VERSION_RE = C.TEMPLATE_STATE_VERSION_RE
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
#: ``**Verdict:** READY`` / ``**Verdict**: READY`` / ``Verdict: NOT-READY`` all occur in real artifacts.
_VERDICT_RE = re.compile(
    r"^\s*\*{0,2}Verdict\*{0,2}\s*:\s*\*{0,2}\s*(NOT-READY|READY)\b", re.IGNORECASE
)
_REVIEW_HEADING_RE = re.compile(r"^##\s+Review\s*$", re.IGNORECASE)
_H2_RE = re.compile(r"^##(?!#)\s*(.*?)\s*$")
_QUESTION_H3_RE = re.compile(r"^### Q(\d+)(?:[.:]|[ \t]+[—–-])?[ \t]+(.*)$")
_QUESTION_SECTION_END_RE = re.compile(r"^#{1,6}(?:[ \t]+|$)")
_QUESTION_CODE_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_QUESTION_MULTI_SELECT_RE = re.compile(
    r"[(（\[]\s*(?:multi[- ]select|select\s+all\s+that\s+apply|多选|可多选)\s*[)）\]]",
    re.IGNORECASE,
)
_QUESTION_OPTION_LIKE_RE = re.compile(r"^\s*(?:[-*]\s+)?[A-Z][.):：]\s+\S")
_H3_RE = re.compile(r"^###+\s*(.*?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*?)\s*$")
_QUOTED_RE = re.compile(r"[`\"“]([^`\"”]{3,})[`\"”]")
_REVIEWER_FIELD_RE = re.compile(r"^\s*\*{0,2}Reviewer\*{0,2}\s*:\s*\*{0,2}\s*(.+?)\s*\*{0,2}\s*$", re.IGNORECASE)
_ITERATION_FIELD_RE = re.compile(r"^\s*\*{0,2}Iteration\*{0,2}\s*:\s*\*{0,2}\s*(\d+)", re.IGNORECASE)
_ACCEPTANCE_HEADING_RE = re.compile(
    r"^##+\s*(Acceptance|Definition of Done|Exit criteria)", re.IGNORECASE
)
#: A Markdown table row inside a review body, e.g. ``| 1 | Major | FR2.2 | … | … |``. Both 2.6.2
#: reviewer agents ship a template whose findings are a table, so a bullets-only parser reads a table
#: full of Critical rows as "no findings" and the gate card then tells a human there are no blockers.
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
#: One cell of the ``|---|:--:|`` line that divides a table's header from its body. Findings are taken
#: only from rows *below* that line, which is what keeps the column headings out of the list.
_TABLE_RULE_CELL_RE = re.compile(r"^:?-+:?$")
#: The subsection heading whose rows are findings. A review body also carries tables that are NOT
#: findings (``### Validation Tool Results``), so a table's meaning comes from its heading, never from
#: being a table.
_FINDINGS_HEADING_RE = re.compile(r"^findings?\b", re.IGNORECASE)
#: Subsection headings whose rows are known to be something other than findings. Listed explicitly so
#: a reviewer's tool-output table cannot be mistaken for findings Studio failed to read.
_NON_FINDINGS_HEADING_RE = re.compile(r"^(summary|validation|verdict|recommendation)", re.IGNORECASE)
_FRONTMATTER_FENCE = "---"
_PER_UNIT_STAGE_SET = frozenset(C.PER_UNIT_STAGES)
#: Review bullet groups → ``ReviewFinding.level``. Prefix match, case-insensitive (§1.6.2).
_REVIEW_LEVELS = (("blocker", "blocker"), ("advisory", "advisory"), ("resolved", "resolved"))

_PHASE_SECTION = "Phase Progress"
_STAGE_SECTION = "Stage Progress"
#: The engine writes this literal for "no stage" / "no unit". Treated as absent, because a consumer
#: that compared it to a slug would look for a stage directory called ``none``.
_NONE_SENTINEL = "none"


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


#: One implementation for the whole backend, so a stamp this module writes into a snapshot and a stamp
#: another module parses out of it round-trip exactly.
_iso = C.iso_from_epoch


def _text(data: bytes | None) -> str:
    """Decode engine output leniently.

    ``errors="replace"`` rather than a raise: a single bad byte in a 6 000-line audit shard must not
    cost the user every card the shard supports.
    """
    if data is None:
        return ""
    return data.decode("utf-8", errors="replace")


def _int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return int(text)
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


# --------------------------------------------------------------------------- #
# 1.6.1 snapshot and stable reads
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One read of one file, with the evidence needed to decide whether it can be trusted."""

    relpath: str
    bytes: bytes
    size: int
    mtime_ns: int
    sha256: str
    unstable: bool

    @property
    def text(self) -> str:
        return _text(self.bytes)

    def to_json(self) -> dict[str, Any]:
        # ``bytes`` is deliberately absent: a snapshot is metadata on the wire, content is served by
        # the artifact route with its own cap and content type.
        return {
            "relpath": self.relpath,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "mtime": _iso(self.mtime_ns / 1_000_000_000),
            "sha256": self.sha256,
            "unstable": self.unstable,
        }


def stable_read(repo: Path, rel: str, cap: int) -> Snapshot | None:
    """Read ``rel`` twice-stat'd and report whether it moved underneath us.

    ``stat`` → ``read`` → ``stat``. A difference in size or mtime means AI-DLC (or an editor) was
    writing while we read, so the bytes we hold may be half of one version and half of another. One
    retry after ``STABLE_READ_RETRY_SECS`` catches the common case — a write that finished during the
    first attempt — and if the second attempt is also unstable the snapshot is returned anyway with
    ``unstable=True``: the UI needs to show *something* ("Refreshing"), it just must not be allowed to
    authorise a decision from it (FR-ACT-009).

    ``None`` means "not a readable regular file" (missing, a symlink, a directory, or one of the secret
    files Studio never opens). Oversize raises ``too_large`` instead of returning a truncated read,
    because half a state file parses into a plausible, wrong projection.
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
                "too_large",
                f"{rel} is {before.st_size} bytes, over the {cap}-byte cap for this read",
                details={"relpath": rel, "size": before.st_size, "cap": cap},
            )
        try:
            with open(path, "rb") as handle:
                data = handle.read(cap + 1)
            after = os.stat(path)
        except OSError:
            return None
        if len(data) > cap:  # grew past the cap between the stat and the read
            raise StudioError(
                "too_large",
                f"{rel} grew past the {cap}-byte cap while being read",
                details={"relpath": rel, "size": len(data), "cap": cap},
            )
        unstable = before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
        snap = Snapshot(
            relpath=rel,
            bytes=data,
            size=after.st_size,
            mtime_ns=after.st_mtime_ns,
            sha256=hashlib.sha256(data).hexdigest(),
            unstable=unstable,
        )
        if not unstable:
            return snap
        if attempt == 0:
            time.sleep(C.STABLE_READ_RETRY_SECS)
    return snap


# --------------------------------------------------------------------------- #
# 1.6.2 parsers — aidlc-state.md
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StageRow:
    """One ``- [x] build-and-test — EXECUTE`` line."""

    slug: str
    mark: str
    state: str
    phase: str | None
    suffix: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "mark": self.mark,
            "state": self.state,
            "phase": self.phase,
            "suffix": self.suffix,
        }


@dataclass(frozen=True, slots=True)
class StateFile:
    """``aidlc-state.md`` as data, with the engine's own field-lookup semantics.

    ``sections`` keeps every ``## X`` block, including ones this version of Studio does not know
    about, so a newer engine's extra section is preserved for display instead of silently dropped.
    The named dicts are conveniences over the same data; the scalar fields are looked up by label
    across the whole file (``getField``, ``04 §3.1``) because the engine matches labels, not sections.
    """

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

    def to_json(self) -> dict[str, Any]:
        return {
            "sections": {name: dict(fields) for name, fields in self.sections.items()},
            "phases": [[name, value] for name, value in self.phases],
            "stages": [row.to_json() for row in self.stages],
            "counts": dict(self.counts),
            "state_version": self.state_version,
            "current_stage": self.current_stage,
            "next_stage": self.next_stage,
            "status": self.status,
            "lifecycle_phase": self.lifecycle_phase,
            "revision_count": self.revision_count,
            "parked_at": self.parked_at,
            "parked_at_stage": self.parked_at_stage,
            "autonomy_mode": self.autonomy_mode,
            "active_unit": self.active_unit,
            "unit_state": self.unit_state,
            "total_stages_claimed": self.total_stages_claimed,
            "completed_claimed": self.completed_claimed,
            "header_only": self.header_only,
        }


def parse_state_file(text: str) -> StateFile:
    """Parse a state file without ever refusing one.

    Never raises on content. The traps this handles are all real (FORMAT-NOTES §3): a ``## Phase
    Progress`` heading with no blank line before it because ``Skeleton Stance`` was inserted last, a
    literal ``Per unit: [TBD]`` line among the construction rows, ``- **Review Override**: `` with a
    trailing space and no value, an en dash or ``--`` instead of the em dash, and a one-line
    header-only file written by ``createIntent`` before the body exists (``04 §2.2``) — which is
    reported as ``header_only`` rather than as an empty workflow, because "birth in progress" and
    "workflow with no stages" must not look alike.
    """
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
            # Phase sub-headers are the only ### in a state file; anything else leaves the phase alone
            # so a future sub-heading cannot silently re-label the rows under it.
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
                stages.append(
                    StageRow(
                        slug=slug,
                        mark=mark,
                        state=C.CHECKBOX_MAP.get(mark.lower(), "unknown"),
                        phase=phase,
                        suffix=(suffix or "").strip() or None,
                    )
                )

    counts = {"total": len(stages)}
    for state_name in C.STAGE_STATE:
        counts[state_name] = sum(1 for row in stages if row.state == state_name)
    counts["done"] = sum(counts[name] for name in C.DONE_STAGE_STATES)

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


# --------------------------------------------------------------------------- #
# 1.6.2 parsers — registry, cursors, directive
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class IntentRow:
    """One ``intents.json`` entry (``IntentRegistryEntry``, ``04 §2.1``)."""

    uuid: str
    slug: str
    dir_name: str | None
    scope: str | None
    repos: tuple[str, ...]
    status: str

    def to_json(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "slug": self.slug,
            "dir_name": self.dir_name,
            "scope": self.scope,
            "repos": list(self.repos),
            "status": self.status,
        }


def parse_intents_json(data: bytes) -> list[IntentRow]:
    """Rows from ``intents.json``; ``[]`` for anything unparseable.

    The engine treats an absent or malformed registry as empty and carries on from the directories on
    disk, so Studio does the same: a broken registry must degrade the *labels* on an intent, never
    hide the work. ``dirName`` is optional (pre-spike rows omit it) and ``repos`` appears only for
    multi-repo intents.
    """
    payload = _json_loads(data)
    if not isinstance(payload, list):
        return []
    rows: list[IntentRow] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        dir_name = entry.get("dirName")
        scope = entry.get("scope")
        rows.append(
            IntentRow(
                uuid=str(entry.get("uuid") or ""),
                slug=str(entry.get("slug") or ""),
                dir_name=str(dir_name) if isinstance(dir_name, str) and dir_name else None,
                scope=str(scope) if isinstance(scope, str) and scope else None,
                repos=_str_tuple(entry.get("repos")),
                status=str(entry.get("status") or "unknown"),
            )
        )
    return rows


def match_intent_row(rows: list[IntentRow], dir_name: str) -> IntentRow | None:
    """The registry row for a record directory, by the engine's own join (``recordDirMatches``).

    Exact ``dirName`` first. The legacy fallback (``<slug>-<hex>`` where the hex is a suffix of the
    uuid) exists for records born before ``dirName`` was stored; joining by slug alone would attach
    the wrong row to two intents that share a label.
    """
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
    """First line of a cursor file, or ``default`` when the file is missing or blank.

    ``activeSpace``/``activeIntent`` never throw in the engine, and an unreadable cursor there means
    "the default", not "no workspace" (``04 §2``).
    """
    if data is None:
        return default
    lines = _text(data).splitlines()
    first = lines[0].strip() if lines else ""
    return first or default


@dataclass(frozen=True, slots=True)
class Directive:
    """``.aidlc-active-directive.json`` — which stage/unit is really executing (``04 §3.2``)."""

    version: int
    stage: str
    unit: str | None
    state_sha256: str
    matches_state: bool
    #: ``None`` on a version-1 marker, which has no ``kind`` field at all. On version 2 it is one of
    #: ``DIRECTIVE_KINDS``: only ``run-stage`` and ``invoke-swarm`` execute a stage, and ``invoke-swarm``
    #: is the unitless code-generation shape, so a caller that must tell them apart can.
    kind: str | None = None
    #: The multi-unit form of ``unit`` (``invoke-swarm`` fans out over several units). Empty when the
    #: marker names none, which is every version-1 marker.
    units: tuple[str, ...] = ()
    #: Internal scope evidence: v2 Code Generation run-stage omitted both unit fields. A null,
    #: empty or malformed field must not be mistaken for this engine-defined zero-unit shape.
    zero_unit_run: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "kind": self.kind,
            "stage": self.stage,
            "unit": self.unit,
            "units": list(self.units),
            "state_sha256": self.state_sha256,
            "matches_state": self.matches_state,
        }


def parse_directive(data: bytes, state_sha256: str) -> Directive | None:
    """The active-directive marker, or ``None`` when it is not a marker at all.

    Both live schema versions are accepted, and the parsed number is carried through rather than
    hardcoded: 2.6.2 wrote ``{"version": 1, stage, unit?, state_sha256}`` and 2.7.1 writes
    ``{"version": 2, "kind": …, stage, unit?/units?, state_sha256}`` at the same path while preserving an
    already-present v1 marker, so a field install can be serving either shape. Refusing v2 did not
    degrade to "no marker": the execution cursor vanished, a per-unit Construction stage was then probed
    at ``Current Stage``, its questions file was not found, and the question / plan-approval card
    disappeared while the intent read Idle.

    ``kind`` is mandatory on v2 and must be one the engine emits, because this marker *is* the execution
    cursor — a later non-execution kind must not be mistaken for a running stage. v1 has no ``kind``.

    The rest of the shape validation is the engine's and is identical in both versions (slug-shaped
    stage, 64 hex digits, same digest semantics). A digest that does not match the live state is *not*
    rejected: the engine treats a stale marker as absent and falls back to ``Current Stage``, and
    staleness is normal after every state write, so it is reported as ``matches_state=False`` for a
    low-severity finding (C06) instead of thrown away.
    """
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
        version=int(version),
        stage=stage,
        unit=str(unit) if isinstance(unit, str) and unit else None,
        state_sha256=digest,
        matches_state=digest == state_sha256,
        kind=kind if version != 1 else None,
        units=_str_tuple(payload.get("units")),
        zero_unit_run=(
            version == 2 and kind == "run-stage" and stage == "code-generation"
            and "unit" not in payload and "units" not in payload
        ),
    )


# --------------------------------------------------------------------------- #
# 1.6.2 parsers — audit
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AuditEvent:
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

    def to_json(self) -> dict[str, Any]:
        return {
            "shard": self.shard,
            "shard_index": self.shard_index,
            "pos": self.pos,
            "timestamp": self.timestamp,
            "event": self.event,
            "fields": dict(self.fields),
            "raw": self.raw,
        }


def parse_audit_shard(text: str, shard: str, shard_index: int) -> list[AuditEvent]:
    """Split one shard into events.

    Keyed on ``**Event**:``, never on the ``## Heading`` line: headings were renamed between engine
    versions and 2.0 emitted free-form ones (``04 §5.2``). A block with no ``Event`` field is dropped
    (it is the file header or a hand-edit), and an event name Studio does not know is kept verbatim —
    ``LOOP_RUN_STARTED`` exists on a real machine and is in no engine taxonomy, so a parser that
    dropped unknown names would silently lose history.

    ``pos`` is the block's index within the shard. Together with ``shard_index`` it is the only
    tie-break available for the same-second rows a birth writes by the dozen.
    """
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
        events.append(
            AuditEvent(
                shard=shard,
                shard_index=shard_index,
                pos=pos,
                timestamp=timestamp,
                event=event,
                fields=fields,
                raw="\n".join(lines[start:]).strip("\n"),
            )
        )
    return events


def merge_audit(shards: list[list[AuditEvent]]) -> list[AuditEvent]:
    """Chronological order across shards: ``(timestamp, shard_index, pos)``.

    Position only orders rows *within* one shard. Two shards writing in the same second are causally
    unordered (``04 §5.1``) — this sort makes the order deterministic, not meaningful, which is why
    every authority check that depends on ordering also requires a strictly later timestamp or the
    same shard.
    """
    merged = [event for shard in shards for event in shard]
    merged.sort(key=lambda event: event.sort_key)
    return merged


def latest_event(
    events: list[AuditEvent], types: Sequence[str], stage: str | None = None
) -> AuditEvent | None:
    """The newest event of any of ``types`` (optionally for one stage), or ``None``.

    Computed with ``max`` over the sort key rather than "last in the list", so a caller that passes an
    unsorted slice still gets the newest row instead of the last one it happened to read.
    """
    wanted = set(types)
    candidates = [
        event
        for event in events
        if event.event in wanted and (stage is None or event.fields.get("Stage") == stage)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda event: event.sort_key)


def human_acted_since_resolution(events: list[AuditEvent]) -> bool | None:
    """Has a human taken a turn since the last recorded resolution? (``humanActedSinceGate``)

    ``None`` means "no audit at all", which is a different answer from ``False`` and must stay
    distinguishable: no ledger cannot prove absence of a human. Otherwise this is the engine's own
    rule — the newest ``HUMAN_TURN`` must be strictly later than the newest resolution, or in the same
    second *and* later in the same shard than every one of them (``04 §5.4``). Equal-second rows from
    a different shard fail closed, because their order is unknowable.
    """
    if not events:
        return None
    turns = [event for event in events if event.event == C.HUMAN_PRESENCE_EVENT]
    if not turns:
        return False
    resolutions = [event for event in events if event.event in C.RESOLUTION_EVENTS]
    if not resolutions:
        return True
    turn = max(turns, key=lambda event: event.sort_key)
    newest_ts = max(event.timestamp for event in resolutions)
    if turn.timestamp > newest_ts:
        return True
    if turn.timestamp < newest_ts:
        return False
    same_second = [event for event in resolutions if event.timestamp == newest_ts]
    return all(
        event.shard_index == turn.shard_index and event.pos < turn.pos for event in same_second
    )


@dataclass(frozen=True, slots=True)
class ShardMeta:
    relpath: str
    size: int
    mtime_ns: int
    truncated: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "relpath": self.relpath,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class AuditBundle:
    """Every shard of one record, merged. ``complete=False`` when any shard was tail-read."""

    events: list[AuditEvent]
    shards: list[ShardMeta]
    complete: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "events": [event.to_json() for event in self.events],
            "shards": [shard.to_json() for shard in self.shards],
            "complete": self.complete,
        }


# --------------------------------------------------------------------------- #
# 1.6.2 parsers — stage graph, scope grid, scopes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Consume:
    artifact: str
    required: bool
    conditional_on: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "required": self.required,
            "conditional_on": self.conditional_on,
        }


@dataclass(frozen=True, slots=True)
class StageNode:
    """One stage of ``stage-graph.json``.

    Keys absent in older graphs (``review_artifact`` before 2.7.1, ``review_class`` and
    ``summary_confirmation`` before 2.5, ``produces_kinds``/``optional_produces``/``workspace_requires``
    before 2.3) come back ``None`` or empty. Stage *numbers* moved between versions when
    ``contract-design`` was inserted, so nothing here may be keyed on ``number`` — ``slug`` is the
    identity (FORMAT-NOTES §4).
    """

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
    #: The one artifact the reviewer protocol may append ``## Review`` to (2.7.1+, 13 stages declare it).
    #: On four of them it is not ``produces[0]``, so reading the verdict off ``produces[0]`` attaches one
    #: artifact's verdict to another artifact's evidence digest. ``None`` on every older graph.
    review_artifact: str | None = None
    #: Declared output location; not every stage writes its products into its record directory.
    outputs: str | None = None

    @property
    def per_unit(self) -> bool:
        return bool(self.for_each) or self.slug in _PER_UNIT_STAGE_SET

    def to_json(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "number": self.number,
            "name": self.name,
            "phase": self.phase,
            "execution": self.execution,
            "condition": self.condition,
            "lead_agent": self.lead_agent,
            "support_agents": list(self.support_agents),
            "mode": self.mode,
            "for_each": self.for_each,
            "workspace_requires": self.workspace_requires,
            "produces": list(self.produces),
            "optional_produces": list(self.optional_produces),
            "produces_kinds": {key: list(value) for key, value in self.produces_kinds.items()},
            "consumes": [consume.to_json() for consume in self.consumes],
            "requires_stage": list(self.requires_stage),
            "sensors": list(self.sensors),
            "scopes": list(self.scopes),
            "reviewer": self.reviewer,
            "reviewer_max_iterations": self.reviewer_max_iterations,
            "review_class": self.review_class,
            "summary_confirmation": self.summary_confirmation,
            "review_artifact": self.review_artifact,
            "outputs": self.outputs,
            "per_unit": self.per_unit,
        }


def parse_stage_graph(data: bytes) -> list[StageNode]:
    """The stage graph, tolerant of every generation shipped.

    The file is a top-level JSON array in every version seen; ``{"stages": [...]}`` is accepted too so
    a future wrapper does not blank the map. An entry with no ``slug`` is skipped — it could not be
    joined to a state row anyway.
    """
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
                consumes.append(
                    Consume(
                        artifact=item["artifact"],
                        required=bool(item.get("required")),
                        conditional_on=str(conditional) if isinstance(conditional, str) else None,
                    )
                )
            elif isinstance(item, str):
                consumes.append(Consume(artifact=item, required=False, conditional_on=None))
        kinds_raw = entry.get("produces_kinds")
        kinds = (
            {str(key): _str_tuple(value) for key, value in kinds_raw.items()}
            if isinstance(kinds_raw, dict)
            else {}
        )
        nodes.append(
            StageNode(
                slug=slug,
                number=str(entry.get("number") or ""),
                name=str(entry.get("name") or slug),
                phase=str(entry.get("phase") or ""),
                execution=str(entry.get("execution") or ""),
                condition=(entry.get("condition") if isinstance(entry.get("condition"), str) else None),
                lead_agent=str(entry.get("lead_agent") or ""),
                support_agents=_str_tuple(entry.get("support_agents")),
                mode=str(entry.get("mode") or ""),
                for_each=(entry.get("for_each") if isinstance(entry.get("for_each"), str) else None),
                workspace_requires=bool(entry.get("workspace_requires")),
                produces=_str_tuple(entry.get("produces")),
                optional_produces=_str_tuple(entry.get("optional_produces")),
                produces_kinds=kinds,
                consumes=tuple(consumes),
                requires_stage=_str_tuple(entry.get("requires_stage")),
                sensors=_str_tuple(entry.get("sensors")),
                scopes=_str_tuple(entry.get("scopes")),
                reviewer=(entry.get("reviewer") if isinstance(entry.get("reviewer"), str) else None),
                reviewer_max_iterations=_int_or_none(
                    str(entry.get("reviewer_max_iterations"))
                    if entry.get("reviewer_max_iterations") is not None
                    else None
                ),
                review_class=(
                    entry.get("review_class") if isinstance(entry.get("review_class"), str) else None
                ),
                summary_confirmation=(
                    entry.get("summary_confirmation")
                    if isinstance(entry.get("summary_confirmation"), str)
                    else None
                ),
                review_artifact=(
                    entry.get("review_artifact")
                    if isinstance(entry.get("review_artifact"), str) and entry.get("review_artifact")
                    else None
                ),
                outputs=entry.get("outputs") if isinstance(entry.get("outputs"), str) else None,
            )
        )
    return nodes


def parse_scope_grid(data: bytes) -> dict[str, dict[str, str]]:
    """``{scope: {slug: "EXECUTE"|"SKIP"}}`` from ``scope-grid.json``.

    Project-composed scopes are normal (DevDelta ships a tenth), so an unknown key is data, not an
    error. The nested ``{"stages": {...}}`` wrapper and a flat mapping are both accepted.
    """
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
        grid[str(scope)] = {
            str(slug): decision
            for slug, decision in stages.items()
            if isinstance(decision, str)
        }
    return grid


@dataclass(frozen=True, slots=True)
class ScopeMeta:
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

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "depth": self.depth,
            "test_strategy": self.test_strategy,
            "review_cap": self.review_cap,
            "skeleton": self.skeleton,
            "runner": self.runner,
            "keywords": list(self.keywords),
            "description": self.description,
            "plugin": self.plugin,
            "project_owned": self.project_owned,
        }


@dataclass(frozen=True, slots=True)
class WorkspaceSignals:
    """What a repository's top level says about the project, bounded to names and one README excerpt.

    Evidence for the Advisor's plan draft (§1.18a, PRD §16.2 / FR-ADV-003): enough to tell a Python
    service from a TypeScript UI, deliberately too little to be a listing of the repository. Names
    only, no recursion, one bounded text read; the broker's ``_clean`` redacts every string again
    before it enters a package.
    """

    #: The first ``MAX_WORKSPACE_ENTRIES`` names in sorted order; directories carry a trailing ``/``;
    #: dot-entries and symlinks skipped.
    top_level: tuple[str, ...]
    #: The ``WORKSPACE_MANIFESTS`` present as regular, non-symlinked files, in that tuple's order.
    manifests: tuple[str, ...]
    #: The first ``README_NAMES`` hit as ``bounded_text`` read it (at most ``MAX_ARTIFACT_RENDER_BYTES``),
    #: or None. Deliberately NOT cut to ``MAX_README_CHARS`` here: the broker's ``_clean`` redacts the
    #: whole text first and cuts second, so a credential straddling the cut is masked whole instead of
    #: leaving a half the scanner cannot recognise (§1.18a).
    readme_excerpt: str | None
    #: More than ``MAX_WORKSPACE_ENTRIES`` eligible entries exist; ``top_level`` is the first cap of them.
    truncated: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "top_level": list(self.top_level),
            "manifests": list(self.manifests),
            "readme_excerpt": self.readme_excerpt,
            "truncated": self.truncated,
        }


def _parse_frontmatter(text: str) -> dict[str, Any] | None:
    """The leading ``---`` block as a flat mapping, with ``- item`` lists.

    A deliberately small YAML subset: scope frontmatter is flat by construction, and pulling in a YAML
    parser to read nine shipped files would add a dependency the gateway does not have.
    """
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
            out[key] = [
                part.strip().strip("'\"") for part in value[1:-1].split(",") if part.strip()
            ]
        elif value:
            out[key] = value.strip("'\"")
        else:
            out[key] = []
    return out


def parse_scope_frontmatter(text: str, filename: str) -> ScopeMeta | None:
    """One scope definition, or ``None`` when the file has no frontmatter.

    ``project_owned`` is decided by the file name (``aidlc-<name>.md`` minus the prefix), not by the
    ``name`` field, because the installer's ownership decision is about the *path* it would overwrite.
    ``testStrategy`` is camelCase on disk (only ``aidlc-workshop.md`` has it) and ``test_strategy`` is
    accepted as well, so a spelling change in a later engine does not lose the value.
    """
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
        name=one("name") or derived,
        depth=one("depth"),
        test_strategy=one("testStrategy", "test_strategy"),
        review_cap=one("review_cap", "reviewCap"),
        skeleton=one("skeleton"),
        runner=None if runner_raw is None else runner_raw.lower() in ("true", "yes", "on"),
        keywords=tuple(str(item) for item in keywords) if isinstance(keywords, list) else (),
        description=one("description"),
        plugin=one("plugin"),
        project_owned=derived not in STOCK_SCOPES,
    )


# --------------------------------------------------------------------------- #
# 1.6.2 parsers — questions file
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class QuestionOption:
    letter: str
    text: str
    is_other: bool

    def to_json(self) -> dict[str, Any]:
        return {"letter": self.letter, "text": self.text, "is_other": self.is_other}


@dataclass(frozen=True, slots=True)
class Question:
    index: int
    prompt: str
    options: tuple[QuestionOption, ...]
    multi_select: bool
    answer: str | None
    answered: bool
    raw: str

    def to_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "prompt": self.prompt,
            "options": [option.to_json() for option in self.options],
            "multi_select": self.multi_select,
            "answer": self.answer,
            "answered": self.answered,
            "raw": self.raw,
        }


@dataclass(frozen=True, slots=True)
class Checkpoint:
    """A ``## Plan Approval`` / ``## Consolidated Summary Confirmation`` block (review P09)."""

    kind: str
    present: bool
    answered: bool
    answer: str | None
    options: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "present": self.present,
            "answered": self.answered,
            "answer": self.answer,
            "options": list(self.options),
        }


@dataclass(frozen=True, slots=True)
class QuestionsFile:
    relpath: str
    sha256: str
    questions: tuple[Question, ...]
    summary_confirmation: Checkpoint | None
    plan_approval: Checkpoint | None
    pending_count: int
    pending_checkpoint: str | None
    source_language_hint: str | None
    origin: dict[str, Any] | None = None
    # Internal reconciliation proof when an answered file yields to a newer audit question.
    # The UI still receives only the current question; this is never serialized into its payload.
    closed_file: "QuestionsFile | None" = None
    closed_file_receipts: tuple[AuditEvent, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "relpath": self.relpath,
            "sha256": self.sha256,
            "questions": [question.to_json() for question in self.questions],
            "summary_confirmation": (
                self.summary_confirmation.to_json() if self.summary_confirmation else None
            ),
            "plan_approval": self.plan_approval.to_json() if self.plan_approval else None,
            "pending_count": self.pending_count,
            "pending_checkpoint": self.pending_checkpoint,
            "source_language_hint": self.source_language_hint,
            **({"origin": dict(self.origin)} if self.origin is not None else {}),
        }


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
    return QuestionsFile(
        relpath=f"{record_rel}/{AUDIT_DIRNAME}/{decision.shard}",
        sha256=digest,
        questions=(Question(
            index=1, prompt=prompt,
            options=tuple(QuestionOption(
                chr(65 + i), label, len(labels) == 1 and is_audit_text_label(label),
            ) for i, label in enumerate(labels)),
            multi_select=False, answer=None, answered=False, raw=decision.raw,
        ),),
        summary_confirmation=None, plan_approval=None, pending_count=1, pending_checkpoint=None,
        source_language_hint=None, origin=origin,
    )


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
    if is_audit_text_label(captured.fields.get("Options", "")):
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


def _closed_question_receipts(
    found: QuestionsFile, pending: QuestionsFile, audit: AuditBundle, data: bytes | None,
) -> list[AuditEvent] | None:
    """Only a file-bound closure in this attempt can hand an answered sheet back to audit.

    Legacy answered questions without a checkpoint receipt keep file ownership. The engine's
    confirmed-content-v1 hash normalizes newlines and trims ECMAScript trailing whitespace; it is
    not the whole-file digest Studio captures. Verify the full normalized content here. Receipts
    that exclude a later Assumption Confirmation section cannot match this stronger binding and
    conservatively retain file ownership rather than trusting an unverified portion of the sheet.
    """
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
    for checkpoint, event_name, heading, answer in (
        (found.summary_confirmation, "SUMMARY_CONFIRMATION_RECORDED",
         C.SUMMARY_CONFIRMATION_HEADING, C.WIRE_LOOKS_CORRECT),
        (found.plan_approval, "PLAN_APPROVAL_RECORDED", C.PLAN_APPROVAL_HEADING, C.WIRE_APPROVE_PLAN),
    ):
        if checkpoint is None:
            continue
        if not checkpoint.answered or checkpoint.answer != answer:
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
            checkpoint_names.add(C.PLAN_APPROVAL_AUDIT_CHECKPOINT)
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
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                return None
            normalized = re.sub(r"\r\n?", "\n", text)
            confirmed = normalized.rstrip(
                "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
                "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
            )
            digest = _sha256(confirmed)
        else:
            return None
        if receipt.fields.get("Questions SHA-256") != digest:
            return None
        receipts.append(receipt)
    return receipts or None


#: Heading → ``Checkpoint.kind``. ``Post-approval Amendment`` is deliberately absent: it is a record of an
#: amendment already agreed, not something pending (C34).
_CHECKPOINT_KINDS = {
    C.SUMMARY_CONFIRMATION_HEADING: "summary_confirmation",
    C.PLAN_APPROVAL_HEADING: "plan_approval",
}


def _answer_from(lines: list[str], tag_index: int, *, plain_continuation: bool = False) -> str:
    """The answer text: the tag remainder and its continuing paragraph or bullet list.

    Real answers are multi-line (FORMAT-NOTES §7): ``[Answer]: X. 采用四个互斥分桶…`` continues on four
    ``- `` lines. A parser that took only the rest of the tag line would send a truncated answer back
    into the conversation. Continuation stops at a blank line, a heading or a ``---`` fence.
    """
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
        # The ``- `` marker is kept: this text is shown back to the human as what they wrote, and a
        # four-bullet answer that arrives as four bare lines reads as one run-on sentence.
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


def file_questions_support_forms(questions: QuestionsFile) -> bool:
    """Only unambiguous, persisted option groups become editable forms.

    Unsupported syntax stays readable through the existing conversation fallback. This never invents
    an option or silently merges repeated question/option identifiers.
    """
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

    Sections start at ``## Q<n>.``, ``### Q<n> —`` and at the three checkpoint headings. Any other
    Markdown heading ends the current section without starting one, so free sections and explanatory
    subheadings cannot contribute options or answers to the preceding question.
    Fenced code contributes no headings, options or answers, while ``raw`` retains the original text.

    Both option spellings occur — ``A. …`` and ``- A. …`` — and ``X.`` is the escape hatch that takes
    free text. Options are collected before the section's first ``[Answer]:`` tag, because a multi-line
    answer's bullets sit after it and must not be mistaken for choices.

    ``## Post-approval Amendment`` resets nothing: the ``## Q<n>.`` sections after it are ordinary
    questions, which is how one intent legitimately has both an open gate and a pending question.

    ``pending_count`` counts questions that are not answered, which includes a section the engine
    wrote without an ``[Answer]:`` line at all: the human still owes that answer, and calling it
    answered would hide a question card. Checkpoints are counted separately in ``pending_checkpoint``
    because they carry different decisions and different wire text.
    """
    raw_lines = text.replace("\r\n", "\n").split("\n")
    lines = _visible_question_lines(raw_lines)
    # Blank placeholders keep raw section offsets intact, including an unclosed fence's remainder.
    starts: list[tuple[int, str, Any]] = []
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
    for order, (start, kind, match) in enumerate(starts):
        end = starts[order + 1][0] if order + 1 < len(starts) else len(lines)
        body = lines[start:end]
        if kind == "other":
            continue
        tag_index = next(
            (i for i, line in enumerate(body) if C.ANSWER_TAG_RE.match(line)), None
        )
        answered = tag_index is not None and not C.BLANK_ANSWER_RE.match(body[tag_index])
        answer = _answer_from(body, tag_index, plain_continuation=kind == "question") if answered and tag_index is not None else None
        limit = tag_index if tag_index is not None else len(body)
        if kind == "question":
            options: list[QuestionOption] = []
            for line in body[1:limit]:
                option = C.OPTION_LINE_RE.match(line)
                if option:
                    letter = option.group(1)
                    options.append(
                        QuestionOption(
                            letter=letter,
                            text=option.group(2).strip(),
                            is_other=letter == C.OTHER_OPTION_LETTER,
                        )
                    )
            prompt = match.group(2).strip()
            questions.append(
                Question(
                    index=int(match.group(1)),
                    prompt=prompt,
                    options=tuple(options),
                    multi_select=bool(_QUESTION_MULTI_SELECT_RE.search(prompt)),
                    answer=answer,
                    answered=answered,
                    raw="\n".join(raw_lines[start:end]).strip("\n"),
                )
            )
            continue
        checkpoint_kind = _CHECKPOINT_KINDS.get(match.group(1))
        if checkpoint_kind is None:  # "Post-approval Amendment" — a divider, not a decision
            continue
        labels = tuple(
            line[2:].strip()
            for line in body[1:limit]
            if C.CHECKPOINT_OPTION_RE.match(line)
        )
        # A re-opened checkpoint is appended below the old one, so the last block is the live one.
        checkpoints[checkpoint_kind] = Checkpoint(
            kind=checkpoint_kind,
            present=True,
            answered=answered,
            answer=answer,
            options=labels,
        )

    summary = checkpoints.get("summary_confirmation")
    plan = checkpoints.get("plan_approval")
    pending_checkpoint = None
    if summary and not summary.answered:
        pending_checkpoint = "summary_confirmation"
    elif plan and not plan.answered:
        pending_checkpoint = "plan_approval"
    return QuestionsFile(
        relpath=relpath,
        sha256=sha256,
        questions=tuple(questions),
        summary_confirmation=summary,
        plan_approval=plan,
        pending_count=sum(1 for question in questions if not question.answered),
        pending_checkpoint=pending_checkpoint,
        source_language_hint=None,
    )


def question_digest(data: bytes | None) -> str | None:
    """``sha256`` over the whole questions file, or ``None`` when there is none.

    Any edit anywhere in the file changes what the human was shown, which is exactly what
    compare-and-submit must notice (C30). Engine confirmation receipts may use a different,
    explicitly named content hash scope; those are checked separately.
    """
    if data is None:
        return None
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# 1.6.2 parsers — review section, stage file, breadcrumb, derived JSON
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    level: str
    title: str
    quote: str | None
    anchor: str | None
    reviewer: str | None
    iteration: int | None

    def to_json(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "title": self.title,
            "quote": self.quote,
            "anchor": self.anchor,
            "reviewer": self.reviewer,
            "iteration": self.iteration,
        }


class ReviewFindings(list):
    """``list[ReviewFinding]`` that also remembers whether the body could be enumerated at all.

    §1.6.2 types the second half of ``parse_review_section`` as a plain list, and a plain empty list
    makes one claim ("the reviewer listed nothing") while meaning two: that, or "Studio could not tell
    what this reviewer listed". The gate card affirms *no open blockers* off that list, so the
    difference decides whether a human is told a fact or a guess. Same device as ``ArtifactList``:
    subclassing keeps the declared type honest while carrying the flag.
    """

    __slots__ = ("findings_parsed",)

    def __init__(self, items: Iterable[ReviewFinding] = (), *, findings_parsed: bool = True) -> None:
        super().__init__(items)
        self.findings_parsed = findings_parsed


def parse_review_section(artifact_text: str) -> tuple[str | None, ReviewFindings]:
    """``(verdict, findings)`` from the LAST ``## Review`` section of an artifact.

    The last one, because a re-reviewed artifact accumulates sections and only the terminal verdict
    binds. Findings come from bullets and from the rows of a Markdown table: both 2.6.2 reviewer agents
    ship a template whose findings are a table, and reading only bullets turned a table of Critical
    rows into "no findings". A table is findings only when its heading says so (``### Findings``, or a
    ``Blocker``/``Advisory``/``Resolved`` group); the same body also carries ``### Validation Tool
    Results``, and promoting tool output to findings would invent evidence rather than lose it.

    The severity vocabulary of a table row (Critical/Major/Minor) is the reviewer's own and stays in the
    title verbatim: Studio does not re-grade a reviewer, so a row's ``level`` is only ever what its
    heading said, otherwise ``unknown``. When nothing could be enumerated and rows Studio could not
    place remain, ``findings.findings_parsed`` is ``False`` — the caller must then say it could not
    read the findings instead of asserting there are none.
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
    #: What the rows under the heading being read are: "findings", "other" (a heading that names
    #: something else), or "unknown" (no heading yet, or one nobody recognises).
    section = "unknown"
    after_rule = False
    unplaced_rows = 0
    findings: list[ReviewFinding] = []
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
                quoted = _QUOTED_RE.search(title)
                findings.append(
                    ReviewFinding(
                        level=level,
                        title=title,
                        quote=quoted.group(1) if quoted else None,
                        anchor=None,
                        reviewer=reviewer,
                        iteration=iteration,
                    )
                )
            elif section == "unknown":
                unplaced_rows += 1
            continue
        # A blank line, or any prose, ends a table: what follows a paragraph is a new table's header.
        after_rule = False
        bullet = _BULLET_RE.match(line)
        if bullet and bullet.group(1):
            title = bullet.group(1).strip()
            quoted = _QUOTED_RE.search(title)
            findings.append(
                ReviewFinding(
                    level=level,
                    title=title,
                    quote=quoted.group(1) if quoted else None,
                    anchor=None,
                    reviewer=reviewer,
                    iteration=iteration,
                )
            )
    return (verdict, ReviewFindings(findings, findings_parsed=bool(findings) or unplaced_rows == 0))


@dataclass(frozen=True, slots=True)
class StageFileMeta:
    slug: str
    acceptance_criteria: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {"slug": self.slug, "acceptance_criteria": list(self.acceptance_criteria)}


def parse_stage_file(text: str, slug: str) -> StageFileMeta:
    """Acceptance criteria from a stage definition, empty when the stage declares none.

    2.6.2's stage files carry none (they describe steps, not exit criteria), so "empty" is the normal
    answer and must not be presented as a missing file.
    """
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


def parse_runtime_graph(data: bytes) -> dict | None:
    """``runtime-graph.json`` as a raw dict — derived data, never authoritative over the audit."""
    payload = _json_loads(data)
    return payload if isinstance(payload, dict) else None


def parse_traceability(data: bytes) -> dict | None:
    payload = _json_loads(data)
    return payload if isinstance(payload, dict) else None


# --------------------------------------------------------------------------- #
# artifacts, markers, baselines
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ArtifactMeta:
    """One file in a record, described well enough to list, render and hash-compare.

    Defined here rather than in ``projection.py`` (where §1.8 lists it) because the reader is what
    constructs it and the projection already imports the reader; the other direction would be an
    import cycle. ``projection`` re-exports this class.
    """

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

    def to_json(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "relpath": self.relpath,
            "name": self.name,
            "stage": self.stage,
            "phase": self.phase,
            "unit": self.unit,
            "size": self.size,
            "mtime": self.mtime,
            "sha256": self.sha256,
            "kind": self.kind,
            "renderable": self.renderable,
        }


class ArtifactList(list):
    """``list[ArtifactMeta]`` that also remembers whether the walk was cut short.

    ``list_artifacts`` is typed as returning a list (§1.6.3) but the caller has to know when the cap or
    the scan deadline stopped it, or it would report a partial record as a complete one. Subclassing
    keeps the declared type honest while carrying the flag.
    """

    __slots__ = ("truncated", "reason")

    def __init__(self, items: Iterable[ArtifactMeta] = (), *, truncated: bool = False,
                 reason: str | None = None) -> None:
        super().__init__(items)
        self.truncated = truncated
        self.reason = reason


@dataclass(frozen=True, slots=True)
class Markers:
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

    def to_json(self) -> dict[str, Any]:
        return {
            "human_turn_mtime_ns": self.human_turn_mtime_ns,
            "engine_touch_mtime_ns": self.engine_touch_mtime_ns,
            "recovery": dict(self.recovery) if self.recovery is not None else None,
            "goal_stop_present": self.goal_stop_present,
            "hooks_health": dict(self.hooks_health),
            "stop_block_count": self.stop_block_count,
            "turn_counter": self.turn_counter,
            "compose_pending": self.compose_pending,
            "reviewer_dispatch": self.reviewer_dispatch,
        }


@dataclass(frozen=True, slots=True)
class PresenceBaseline:
    """What the record looked like when a decision was submitted (C23).

    Compared byte-for-byte by the reconciler to answer two questions no single signal can: did a human
    actually take a turn for this decision, and did anything on disk move at all. ``human_turn_events``
    is a lower bound when the audit was tail-read, which ``human_turn_events_partial`` says out loud
    so nobody treats "same count" as proof.
    """

    human_turn_events: int
    human_turn_events_partial: bool
    human_turn_mtime_ns: int | None
    turn_counter: int | None
    shard_sizes: dict[str, int]
    audit_tail_sha256: str
    state_sha256: str | None
    taken_at: str

    def to_json(self) -> dict[str, Any]:
        return {
            "human_turn_events": self.human_turn_events,
            "human_turn_events_partial": self.human_turn_events_partial,
            "human_turn_mtime_ns": self.human_turn_mtime_ns,
            "turn_counter": self.turn_counter,
            "shard_sizes": dict(self.shard_sizes),
            "audit_tail_sha256": self.audit_tail_sha256,
            "state_sha256": self.state_sha256,
            "taken_at": self.taken_at,
        }


@dataclass(frozen=True, slots=True)
class BoundaryInputs:
    """One coherent read of everything a boundary decision depends on.

    Taken in a fixed order and with the state file re-read at the end: if the state changed while the
    audit and questions were being read, the set describes no single moment in time and ``stable`` is
    ``False``. Nothing may be submitted from an unstable set (FR-ACT-009).
    """

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
    #: True when the stage runs once per unit of work, several units on disk hold its questions, and
    #: nothing names the one executing. ``questions`` is then ``None`` on purpose: showing one unit's
    #: questions would deliver the answer as if it answered another unit's. Additive to §1.6.3 — a
    #: caller that does not know about it simply gets no questions, which is the safe half.
    unit_ambiguous: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "space": self.space,
            "intent_dir": self.intent_dir,
            "state": self.state_snap.to_json() if self.state_snap else None,
            "directive": self.directive.to_json() if self.directive else None,
            "questions": self.questions.to_json() if self.questions else None,
            "stage": self.stage,
            "unit_ambiguous": self.unit_ambiguous,
            "audit_complete": self.audit.complete,
            "stable": self.stable,
            "taken_at": self.taken_at,
        }


# --------------------------------------------------------------------------- #
# 1.6.4 token formulas (binding)
# --------------------------------------------------------------------------- #


def boundary_token(
    state_sha256: str,
    events: list[AuditEvent],
    current_stage: str | None,
    revision_count: int,
) -> str:
    """A fingerprint of "the boundary the human is looking at".

    State digest + newest boundary event timestamp + current stage + revision count. It changes when
    the workflow moves and stays put when unrelated audit noise (a session start, a sensor firing) is
    appended, which is what makes it usable as the compare-and-submit key.
    """
    last = latest_event(events, C.BOUNDARY_EVENTS)
    ts = last.timestamp if last else "none"
    return security.composite_token(state_sha256, ts, current_stage or "none", str(revision_count))


def stage_attempt(events: list[AuditEvent], stage: str) -> int:
    """Which attempt at this stage we are on: ``1 + count(STAGE_REVISING)``.

    ``Revision Count`` in the state file is workflow-global, so the per-stage revising rows are the
    only durable per-stage counter (C05). The conductor offers ``Accept as-is`` only after three
    rejection cycles, and this is the number that gate is checked against.
    """
    return 1 + sum(
        1 for event in events if event.event == "STAGE_REVISING" and event.fields.get("Stage") == stage
    )


def evidence_digest(
    artifacts: list[ArtifactMeta] | None,
    review: tuple[str | None, list[ReviewFinding]] | None,
) -> str | None:
    """A digest of what the human actually looked at (review P14/C30).

    Sorted ``relpath:sha256`` rows plus the parsed review section. If an artifact is rewritten between
    the moment a card was rendered and the moment Approve is pressed, this differs and the submit is
    refused as stale — the state file alone would not notice, because rewriting an artifact does not
    touch it. ``None`` only for a card with no stage, where there is no evidence set to speak of.
    """
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


# --------------------------------------------------------------------------- #
# 1.6.3 repo-level readers
# --------------------------------------------------------------------------- #


class AidlcReader:
    """Bounded, containment-checked reads of one repository's AI-DLC workspace.

    Every method takes the repository root plus repo-relative coordinates and resolves them through
    ``security.resolve_inside``, so a cursor file or a registry row that names ``../../etc`` fails as a
    bad path instead of reading outside the repo. Nothing here writes, spawns or caches: the caller
    owns freshness, because a cached parse is exactly how a stale card gets authorised.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    # -- layout -------------------------------------------------------------
    @staticmethod
    def intents_rel(space: str) -> str:
        _check_space(space)
        return f"{WORKSPACE_DIRNAME}/{SPACES_DIRNAME}/{space}/{INTENTS_DIRNAME}"

    @classmethod
    def record_rel(cls, space: str, intent_dir: str) -> str:
        _check_intent_dir(intent_dir)
        return f"{cls.intents_rel(space)}/{intent_dir}"

    def detect_harness_dirs(self, repo: Path) -> list[HarnessDir]:
        """Every AI-DLC harness tree in the repository, described by its own files.

        Read from ``aidlc-version.ts``/``aidlc-lib.ts``/``stage-graph.json`` rather than by asking the
        engine: repo-resident TypeScript is untrusted data (PRD §16.2, C35). ``.kiro`` is the harness
        Studio drives, but the others are reported so a repo driven from two harnesses is visible.
        """
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
            found.append(
                HarnessDir(
                    dir=name,
                    harness_name=harness.get("name") or None,
                    rules_subdir=harness.get("rulesSubdir") or None,
                    engine_version=version_match.group(1) if version_match else None,
                    engine_state_version=self._engine_state_version(tools),
                    stage_count=stage_count,
                    has_utility=security.is_regular_file(tools / ENGINE_UTILITY_NAME),
                )
            )
        return found

    @staticmethod
    def _engine_state_version(tools: Path) -> int | None:
        """The state version the installed engine writes, or ``None`` — never a guess.

        ``CURRENT_STATE_VERSION`` in ``aidlc-lib.ts`` is authoritative from 2.5 on; older builds carry
        the number only inside the state template in ``aidlc-utility.ts``. Guessing here would make
        ``state_version_unsupported`` fire against a healthy repository.
        """
        lib = security.bounded_text(tools / ENGINE_LIB_NAME, C.MAX_ARTIFACT_RENDER_BYTES)
        if lib:
            match = C.ENGINE_STATE_VERSION_RE.search(lib)
            if match:
                return int(match.group(1))
        utility = security.bounded_text(tools / ENGINE_UTILITY_NAME, C.MAX_ARTIFACT_RENDER_BYTES)
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
        """The active space, defaulting to ``default``.

        The 2.1.1 install in the fixtures has no ``aidlc/active-space`` at all, and the engine's
        ``activeSpace()`` never throws — an unreadable cursor means the default space, not no space.
        """
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
        """``(cursor value, dangling)``.

        ``dangling`` is the case the engine hides: ``activeIntent()`` ignores a cursor whose directory
        has no ``aidlc-state.md`` and silently falls back to the lone intent, so a repo can look fine
        while every decision would land somewhere the user did not choose. Studio reports it instead
        (finding ``dangling_cursor``). A cursor value that is not a legal directory name is reported as
        dangling with no value, so it can never become a path.
        """
        path = security.resolve_inside(repo, f"{self.intents_rel(space)}/{ACTIVE_INTENT_FILENAME}")
        value = parse_cursor(security.bounded_read(path, C.MAX_TEXT_PREVIEW_BYTES), default=None)
        if value is None:
            return (None, False)
        if not C.INTENT_DIR_RE.match(value):
            return (None, True)
        state = security.resolve_inside(repo, f"{self.record_rel(space, value)}/{STATE_FILENAME}")
        return (value, not security.is_regular_file(state))

    def list_intent_dirs(self, repo: Path, space: str) -> list[str]:
        """Record directories that hold a state file, sorted, capped.

        The engine's ``listIntentDirs`` rule: a directory counts as an intent only when
        ``aidlc-state.md`` is in it, which is what keeps ``intents.json``, the cursor file and the
        space-level ``.aidlc-hooks-health/`` out of the list.
        """
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
        """The state file, twice-stat'd, plus its parse. ``None`` when it is not there.

        Raises ``too_large`` for an oversized file rather than parsing part of it; the caller turns
        that into a ``state_unreadable`` finding after a grace period, because an unreadable state
        file is often just a write in flight.
        """
        snap = stable_read(repo, f"{self.record_rel(space, intent_dir)}/{STATE_FILENAME}", C.MAX_STATE_BYTES)
        if snap is None:
            return None
        return (snap, parse_state_file(snap.text))

    def read_directive(
        self, repo: Path, space: str, intent_dir: str, state_sha256: str
    ) -> Directive | None:
        path = security.resolve_inside(
            repo, f"{self.record_rel(space, intent_dir)}/{DIRECTIVE_FILENAME}"
        )
        data = security.bounded_read(path, C.MAX_JSON_BYTES)
        if data is None:
            return None
        return parse_directive(data, state_sha256)

    def read_audit(
        self, repo: Path, space: str, intent_dir: str, *, tail_bytes: int = C.MAX_AUDIT_TAIL_BYTES
    ) -> AuditBundle:
        """Every ``audit/*.md`` shard, merged chronologically.

        Shards are per clone, not per intent: the same file name appears under two intents and one
        intent can have two shards, so this always globs (``04 §5.1``). A shard bigger than
        ``tail_bytes`` is tail-read with its partial first line dropped and the bundle is marked
        incomplete — an evidence claim built on a truncated history has to say so (``audit_incomplete``).
        """
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
        for index, path in enumerate(paths[: C.MAX_AUDIT_SHARDS]):
            if not security.is_regular_file(path):
                complete = False
                continue
            try:
                stat = path.stat()
            except OSError:
                complete = False
                continue
            truncated = stat.st_size > tail_bytes
            data = (
                security.tail_read(path, tail_bytes)
                if truncated
                else security.bounded_read(path, tail_bytes)
            )
            # A failed read used to be indistinguishable from an empty shard. Audit-backed questions
            # need absence of a later answer to be a proof, so an unreadable/changing shard is partial.
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
            shards.append(
                ShardMeta(relpath=rel, size=stat.st_size, mtime_ns=stat.st_mtime_ns, truncated=truncated)
            )
            per_shard.append(parse_audit_shard(_text(data), path.name, index))
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
        """Top-level names, the manifests present, and one README excerpt (§1.6.3, FR-ADV-003).

        Bounded by construction rather than by trust: ``_iterdir`` yields nothing for an unreadable
        root, symlinks and dot-entries are never followed or named (a README or manifest that is itself
        a symlink is skipped the same way the listing skips it, even when its target is inside the
        repository), the listing keeps the first ``MAX_WORKSPACE_ENTRIES`` names without sorting the
        whole directory (``heapq.nsmallest`` over the eligible entries — a repository root with tens of
        thousands of files costs one pass, not a sort), every manifest and README path is
        containment-checked through ``security.resolve_inside`` before it is stat-ed, and the README is
        the *only* file whose bytes are read — through ``bounded_text`` so a secret file or an oversized
        one comes back as nothing. The README text is returned whole (as bounded) rather than cut to
        ``MAX_README_CHARS``: the broker redacts before it cuts (§1.18a).
        """
        cap = C.MAX_WORKSPACE_ENTRIES
        eligible = (
            entry for entry in _iterdir(repo)
            if not entry.is_symlink() and not entry.name.startswith(".")
        )
        # One more than the cap, so "truncated" is a fact about the directory and not a guess.
        kept = heapq.nsmallest(cap + 1, eligible, key=lambda path: path.name)
        truncated = len(kept) > cap
        names = [f"{entry.name}/" if entry.is_dir() else entry.name for entry in kept[:cap]]

        manifests: list[str] = []
        for name in WORKSPACE_MANIFESTS:
            if (repo / name).is_symlink():
                continue
            try:
                path = security.resolve_inside(repo, name)
            except StudioError:
                continue
            if security.is_regular_file(path):
                manifests.append(name)

        readme: str | None = None
        for name in README_NAMES:
            if (repo / name).is_symlink():
                continue
            try:
                path = security.resolve_inside(repo, name)
            except StudioError:
                continue
            if not security.is_regular_file(path):
                continue
            text = security.bounded_text(path, C.MAX_ARTIFACT_RENDER_BYTES)
            readme = text or None
            break

        return WorkspaceSignals(
            top_level=tuple(names),
            manifests=tuple(manifests),
            readme_excerpt=readme,
            truncated=truncated,
        )

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
        """Where a stage's artifacts live.

        Per-unit Construction stages nest under the unit (``construction/<unit>/<slug>``) while
        once-per-workflow stages sit directly under the phase — both layouts are real in one record
        (FORMAT-NOTES §8), so the unit segment is added only for ``PER_UNIT_STAGES``.
        """
        record = self.record_rel(space, intent_dir)
        if unit and slug in _PER_UNIT_STAGE_SET:
            _check_unit(unit)
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
        """Units of work: the directories under ``construction/`` that are not stages.

        The stage names to exclude come from the intent's own state file when it is readable, so a
        renamed or added stage in a newer engine cannot be mistaken for a unit; the known construction
        slugs are the fallback.
        """
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
        """Every regular file in a record, bounded by count and by wall clock.

        Symlinks are never followed and dot-directories are skipped, which keeps the walk out of
        ``.aidlc-sensors/``, ``.aidlc-hooks-health/`` and any link that leaves the repo. Digests are
        computed only for small records (``WHOLE_RECORD_HASH_LIMIT``): card evidence never comes from
        this listing, so hashing 2 000 files to render a file tree would be waste, not safety.
        """
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

    def stage_artifacts(
        self, repo: Path, snap_stage_dir_rel: str, node: StageNode | None
    ) -> list[ArtifactMeta]:
        """The produced artifacts of ONE stage, always hashed — the evidence set (review P14).

        Every regular file directly in the stage directory, plus each declared ``produces`` /
        ``optional_produces`` name that exists there. Always digested, because this list is what
        ``evidence_digest`` compares at submit time; a file too big to hash whole is digested over its
        first ``MAX_ARTIFACT_RENDER_BYTES`` plus its size, so growth past the cap still changes the
        answer. Symlinks are skipped, not followed.
        """
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
        """The runtime markers around a record.

        ``.aidlc-goal-stop`` is reported as presence only: nothing in AI-DLC writes it (``04 §3.11``),
        so treating it as a workflow signal would give an agent-written file authority over the product.
        """
        record = self.record_rel(space, intent_dir)
        recovery_text = security.bounded_text(
            security.resolve_inside(repo, f"{record}/{RECOVERY_FILENAME}"), C.MAX_TEXT_PREVIEW_BYTES
        )
        hooks: dict[str, str] = {}
        hooks_dir = security.resolve_inside(repo, f"{record}/{HOOKS_HEALTH_DIRNAME}")
        for entry in sorted(_iterdir(hooks_dir), key=lambda path: path.name):
            if not entry.name.endswith(HOOKS_HEALTH_SUFFIX) or not security.is_regular_file(entry):
                continue
            value = security.bounded_text(entry, C.MAX_TEXT_PREVIEW_BYTES) or ""
            hooks[entry.name[: -len(HOOKS_HEALTH_SUFFIX)]] = value.strip()
        return Markers(
            human_turn_mtime_ns=_mtime_ns(security.resolve_inside(repo, f"{record}/{HUMAN_TURN_FILENAME}")),
            engine_touch_mtime_ns=_mtime_ns(
                security.resolve_inside(repo, f"{record}/{ENGINE_TOUCH_FILENAME}")
            ),
            recovery=parse_recovery_breadcrumb(recovery_text) if recovery_text is not None else None,
            goal_stop_present=security.is_regular_file(
                security.resolve_inside(repo, f"{record}/{GOAL_STOP_FILENAME}")
            ),
            hooks_health=hooks,
            stop_block_count=_json_dict(
                security.bounded_read(
                    security.resolve_inside(repo, f"{record}/{STOP_HOOK_REL}"), C.MAX_JSON_BYTES
                )
            ),
            turn_counter=_int_or_none(
                security.bounded_text(
                    security.resolve_inside(repo, TURN_COUNTER_REL), C.MAX_TEXT_PREVIEW_BYTES
                )
            ),
            compose_pending=(security.resolve_inside(repo, COMPOSE_PENDING_REL)).exists(),
            reviewer_dispatch=_json_dict(
                security.bounded_read(
                    security.resolve_inside(repo, f"{record}/{REVIEWER_DISPATCH_FILENAME}"),
                    C.MAX_JSON_BYTES,
                )
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
        """Snapshot the human-presence evidence at submit time (C23).

        Six independent facts, because each alone is forgeable or noisy: the ``HUMAN_TURN`` count, the
        marker mtime, the Kiro turn counter, every shard's size, a digest of the newest shard's tail,
        and the state digest. The reconciler later needs *exactly one* new human turn to accept a
        resolution — zero means the engine recorded an answer nobody gave, more than one means the
        boundary cannot be attributed to this decision.
        """
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
            human_turn_events=sum(
                1 for event in audit.events if event.event == C.HUMAN_PRESENCE_EVENT
            ),
            human_turn_events_partial=not audit.complete,
            human_turn_mtime_ns=_mtime_ns(
                security.resolve_inside(repo, f"{record}/{HUMAN_TURN_FILENAME}")
            ),
            turn_counter=_int_or_none(
                security.bounded_text(
                    security.resolve_inside(repo, TURN_COUNTER_REL), C.MAX_TEXT_PREVIEW_BYTES
                )
            ),
            shard_sizes=sizes,
            audit_tail_sha256=hashlib.sha256(tail).hexdigest(),
            state_sha256=state_sha256,
            taken_at=self._clock.iso(),
        )

    def stable_boundary_inputs(self, repo: Path, space: str, intent_dir: str) -> BoundaryInputs:
        """State, audit, directive and the current stage's questions — as one moment or not at all.

        Fixed order (state, audit, directive, questions) and the state file re-read at the end. If the
        first read was unstable, the second was, or the two digests differ, the set spans a write and
        ``stable`` is ``False``; a decision built on it would be authorising a boundary that no longer
        exists.
        """
        taken_at = self._clock.iso()
        first = self.read_state(repo, space, intent_dir)
        if first is None:
            return BoundaryInputs(
                space=space,
                intent_dir=intent_dir,
                state_snap=None,
                state=None,
                audit=self.read_audit(repo, space, intent_dir),
                directive=None,
                questions=None,
                stage=None,
                stable=False,
                taken_at=taken_at,
            )
        snap, state = first
        audit = self.read_audit(repo, space, intent_dir)
        directive = self.read_directive(repo, space, intent_dir, snap.sha256)
        stage = directive.stage if directive and directive.matches_state else state.current_stage
        unit = (directive.unit if directive and directive.matches_state else None) or state.active_unit
        questions, unit_ambiguous = self._questions_for_stage(
            repo, space, intent_dir, state, stage, unit, audit=audit, directive=directive
        )
        again = self.read_state(repo, space, intent_dir)
        stable = (
            again is not None
            and not snap.unstable
            and not again[0].unstable
            and again[0].sha256 == snap.sha256
        )
        return BoundaryInputs(
            space=space,
            intent_dir=intent_dir,
            state_snap=snap,
            state=state,
            audit=audit,
            directive=directive,
            questions=questions,
            stage=stage,
            stable=stable,
            taken_at=taken_at,
            unit_ambiguous=unit_ambiguous,
        )

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
        """``(questions, unit_ambiguous)`` for one stage, found without needing the stage graph.

        The phase comes from the state file's own ``### <PHASE> PHASE`` grouping; when the row is
        missing (a stage the graph knows but this state file does not list) every phase directory is
        probed, plus the per-unit path. Bounded — at most one probe per phase per unit.

        A per-unit Construction stage is the dangerous case. When neither the directive nor the state
        names the executing unit, every unit directory is a candidate, and returning the first hit means
        returning whichever unit sorts first: the human would be shown one unit's questions and their
        answer delivered as though it answered another's. So all candidates are probed and a second hit
        makes the answer unknowable — no questions, and ``unit_ambiguous`` so Consistency can block the
        decision and the card becomes a recovery card instead of a coin flip. One candidate with a
        questions file is still an answer: which unit it was is recorded in the file's own path.
        """
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
            # Pending structured content always owns the interaction. Closed content can yield to
            # a later audit decision only with a current, file-bound checkpoint receipt.
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
            if found.questions or found.summary_confirmation or found.plan_approval:
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
                found = self.read_questions(
                    repo, directory, stage,
                )
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
            found = self.read_questions(
                repo, directory, stage
            )
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


# --------------------------------------------------------------------------- #
# module-private helpers
# --------------------------------------------------------------------------- #


def _check_space(space: str) -> None:
    if not C.SPACE_RE.match(space or ""):
        raise StudioError("bad_path", "space name is not a legal directory name", details={"space": space})


def _check_intent_dir(intent_dir: str) -> None:
    if not C.INTENT_DIR_RE.match(intent_dir or ""):
        raise StudioError(
            "bad_path", "intent directory is not a legal directory name", details={"intent_dir": intent_dir}
        )


def _check_unit(unit: str) -> None:
    if not C.UNIT_NAME_RE.match(unit or ""):
        raise StudioError("bad_path", "unit name is not a legal directory name", details={"unit": unit})


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
    """``sha256`` of a file, or of its first cap bytes plus its size when it is too big.

    The size is folded in so a file that keeps growing past the cap still changes its digest —
    otherwise a huge artifact would look frozen to compare-and-submit.
    """
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
    """``(phase, stage, unit)`` for a record-relative artifact path.

    Construction is the awkward one: ``construction/<unit>/<stage>/…`` and ``construction/<stage>/…``
    both exist in one real record, so the second segment is a unit exactly when it is not a known
    construction stage slug (FORMAT-NOTES §8).
    """
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
        artifact_id=hashlib.sha1(rel.encode("utf-8")).hexdigest(),
        relpath=rel,
        name=name,
        stage=stage,
        phase=phase,
        unit=unit,
        size=stat.st_size,
        mtime=_iso(stat.st_mtime),
        sha256=digest,
        kind=_artifact_kind(name, stage, rel),
        renderable=stat.st_size <= C.MAX_ARTIFACT_RENDER_BYTES and suffix in TEXT_EXTENSIONS,
    )
