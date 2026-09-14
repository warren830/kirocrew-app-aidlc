"""AI-DLC Console — read-only projection of AI-DLC Workflows state.

AI-DLC already keeps everything this app shows on disk, per repository:

  <repo>/<harnessDir>/tools/data/{harness,stage-graph,scope-grid}.json   engine control plane
  <repo>/aidlc/spaces/<space>/intents/intents.json                       intent registry
  <repo>/aidlc/spaces/<space>/intents/<dir>/aidlc-state.md               phase + stage state
  <repo>/aidlc/spaces/<space>/intents/<dir>/.aidlc-active-directive.json  stage + state digest
  <repo>/aidlc/spaces/<space>/intents/<dir>/audit/*.md                   append-only audit shards

What is missing is a place to see it across repositories, so this app is a
PROJECTION and never an owner: it opens those files read-only and writes nothing
back into any AI-DLC tree. The only thing it persists is its own repo registry,
in ``ctx.storage``. That boundary is what keeps a ``/aidlc`` run in a terminal
and this board from ever disagreeing -- there is only one copy of the truth.

Two consequences of that stance shape the code:

* **Nothing about the stage list is hardcoded.** Installed AI-DLC versions
  differ in ways that would silently break a baked-in schema: a 32-stage tree
  carries ``application-design`` where a 33-stage tree splits it into
  ``domain-design`` + ``contract-design``, and older state files omit fields
  like ``Review Override`` entirely. Stages come from the file being read, and
  ``stage-graph.json`` supplies the per-repo authority to compare against.

* **Disagreement is reported, not resolved.** Real state files do contradict
  themselves -- a ``Phase Progress`` block saying Initialization/Active while
  ``Current Status`` says OPERATION/Completed. Picking a winner would hide a
  genuine engine or crash artifact, so both are surfaced as a finding.

Single-file on purpose: ``apps/module_loader.load_app_module`` loads a
third-party route module by file path via ``spec_from_file_location`` with no
``sys.path`` injection, so sibling imports are not importable here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import time
from functools import wraps
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import web

from kiro_crew.apps.context import AppContext
from kiro_crew.apps.route_registry import AppRoute

try:
    from kiro_crew.security import is_sensitive_path

    _HAS_SECURITY = True
except Exception:  # pragma: no cover - security module always present in prod
    _HAS_SECURITY = False

    def is_sensitive_path(path: str) -> bool:  # type: ignore[misc]
        """Fail CLOSED when the security module is unavailable.

        Every repo path the user registers is checked through this before it is
        read. Without the module we cannot make that judgement, so refuse
        everything rather than wave it through.
        """
        return True


logger = logging.getLogger("kirocrew.app.aidlc-console")

APP_NAME = "aidlc-console"

#: Engine directories an AI-DLC harness may install into. ``harness.json`` under
#: ``<dir>/tools/data/`` is the authoritative marker; one repo can legitimately
#: carry several (installing for Kiro and for Claude Code both).
_ENGINE_DIRS = (".kiro", ".claude", ".codex", ".aidlc", ".cursor")

#: Legacy pre-``spaces/`` layout, still present in older projects.
_LEGACY_STATE_REL = "aidlc-docs/aidlc-state.md"

#: Read caps. State files run ~3 KB and audit shards grow past 150 KB, so the
#: audit is tail-read and everything else is refused rather than truncated
#: mid-structure, which would produce a confidently wrong parse.
_MAX_STATE_BYTES = 512 * 1024
_MAX_JSON_BYTES = 4 * 1024 * 1024
_MAX_AUDIT_TAIL_BYTES = 48 * 1024

_MAX_REPOS = 64
_MAX_INTENTS_PER_REPO = 256

_STORAGE_KEY_REPOS = "repos"

# --------------------------------------------------------------------------- #
# state-file grammar
# --------------------------------------------------------------------------- #

_SECTION_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_SUBSECTION_RE = re.compile(r"^###\s+(?P<title>.+?)\s*$")
_FIELD_RE = re.compile(r"^-\s+\*\*(?P<key>[^*]+?)\*\*:\s*(?P<val>.*?)\s*$")

#: ``- [x] build-and-test — EXECUTE``. The separator is an em dash in current
#: writers; en dash and ``--`` are accepted because older files used them, and
#: the mode suffix is optional so a bare stage line still parses.
_STAGE_RE = re.compile(
    r"^-\s+\[(?P<mark>[ \-xXsSrR?])\]\s+(?P<slug>[a-z0-9][a-z0-9._-]*)"
    r"(?:\s*(?:—|–|--)\s*(?P<mode>[A-Za-z_]+))?\s*$"
)
_PHASE_HEADER_RE = re.compile(r"^(?P<phase>[A-Z][A-Z ]*?)\s+PHASE\s*$")

#: Guards the ``{intent}`` path parameter before it is joined to a filesystem
#: path. Membership in the discovered set is checked too -- this only rejects
#: the shapes that could traverse.
_INTENT_DIR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SPACE_RE = _INTENT_DIR_RE

_STAGE_MARKS = {
    " ": "not_started",
    "-": "in_progress",
    "?": "awaiting_approval",
    "r": "revising",
    "x": "completed",
    "s": "skipped",
}

#: Terminal-ish stage states, used to reconcile the file's own completed count.
_DONE_STATES = ("completed", "skipped")


# --------------------------------------------------------------------------- #
# small filesystem helpers
# --------------------------------------------------------------------------- #


def _read_text_capped(path: Path, cap: int) -> str | None:
    """Read a small text file, or None if absent/oversized/unreadable.

    Oversize is a refusal rather than a truncation: half a state file parses
    into a plausible-looking but wrong projection.
    """
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > cap:
            logger.warning("aidlc-console: %s exceeds %d bytes, skipped", path, cap)
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("aidlc-console: read failed for %s: %s", path, exc)
        return None


def _read_json_capped(path: Path, cap: int = _MAX_JSON_BYTES) -> Any:
    """Parse a JSON file, or None on any absence/size/decode problem."""
    raw = _read_text_capped(path, cap)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as exc:
        logger.debug("aidlc-console: bad JSON in %s: %s", path, exc)
        return None


def _sha256_file(path: Path) -> str | None:
    """Digest a file the same way AI-DLC's active-directive records it."""
    try:
        if not path.is_file():
            return None
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _tail_lines(path: Path, cap: int = _MAX_AUDIT_TAIL_BYTES, limit: int = 80) -> list[str]:
    """Last ``limit`` non-empty lines of a file, reading only its tail.

    Audit shards are append-only and unbounded, so the whole file is never
    pulled into memory just to show recent activity.
    """
    try:
        if not path.is_file():
            return []
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > cap:
                fh.seek(size - cap)
                fh.readline()  # discard the partial line the seek landed inside
            blob = fh.read()
    except OSError:
        return []
    text = blob.decode("utf-8", errors="replace")
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return lines[-limit:]


def _mtime_iso(path: Path) -> str | None:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# state-file parsing
# --------------------------------------------------------------------------- #


def parse_state_file(text: str) -> dict[str, Any]:
    """Parse ``aidlc-state.md`` into a structured projection.

    Deliberately tolerant: unknown sections are preserved as flat key/value maps
    so a newer AI-DLC that adds a section still renders, and no stage slug or
    phase name is validated against a builtin list.
    """
    sections: dict[str, dict[str, str]] = {}
    stages: list[dict[str, Any]] = []
    phases: list[dict[str, str]] = []

    section: str | None = None
    subsection: str | None = None
    current_phase: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        m_sec = _SECTION_RE.match(line)
        if m_sec:
            section = m_sec.group("title").strip()
            subsection = None
            current_phase = None
            sections.setdefault(section, {})
            continue

        m_sub = _SUBSECTION_RE.match(line)
        if m_sub:
            subsection = m_sub.group("title").strip()
            m_phase = _PHASE_HEADER_RE.match(subsection)
            current_phase = (
                m_phase.group("phase").strip().lower() if m_phase else None
            )
            continue

        if section == "Stage Progress":
            m_stage = _STAGE_RE.match(line)
            if m_stage:
                mark = m_stage.group("mark")
                stages.append(
                    {
                        "slug": m_stage.group("slug"),
                        "mark": mark,
                        "state": _STAGE_MARKS.get(mark.lower(), "unknown"),
                        "phase": current_phase,
                        "mode": m_stage.group("mode") or None,
                    }
                )
            continue

        m_field = _FIELD_RE.match(line)
        if m_field and section is not None:
            key = m_field.group("key").strip()
            val = m_field.group("val").strip()
            sections[section][key] = val
            if section == "Phase Progress":
                phases.append({"name": key, "status": val})

    counts: dict[str, int] = {"total": len(stages)}
    for state in _STAGE_MARKS.values():
        counts[state] = sum(1 for s in stages if s["state"] == state)
    counts["done"] = sum(1 for s in stages if s["state"] in _DONE_STATES)

    project = sections.get("Project Information", {})
    current = sections.get("Current Status", {})
    resume = sections.get("Session Resume Point", {})
    plan = sections.get("Execution Plan Summary", {})

    return {
        "sections": sections,
        "project": project,
        "plan": plan,
        "current": current,
        "resume": resume,
        "phases": phases,
        "stages": stages,
        "counts": counts,
    }


def _as_int(value: str | None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# consistency findings
# --------------------------------------------------------------------------- #


def _finding(code: str, severity: str, message: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"code": code, "severity": severity, "message": message}
    out.update(extra)
    return out


def compute_findings(
    state: dict[str, Any],
    *,
    directive: Any = None,
    state_digest: str | None = None,
    registry_status: str | None = None,
    graph_slugs: set[str] | None = None,
    goal_stop: bool = False,
) -> list[dict[str, Any]]:
    """Cross-check the projected state against its own companion files.

    Severities: ``action`` is a human decision AI-DLC is waiting on, ``warn`` is
    an inconsistency worth a look, ``info`` is context. Nothing here is a
    verdict on correctness -- it reports what disagrees.
    """
    findings: list[dict[str, Any]] = []
    counts = state.get("counts", {})
    stages = state.get("stages", [])
    current = state.get("current", {})

    gated = [s["slug"] for s in stages if s["state"] == "awaiting_approval"]
    if gated:
        findings.append(
            _finding(
                "awaiting_approval",
                "action",
                f"{len(gated)} stage(s) hold an open approval gate: {', '.join(gated)}",
                stages=gated,
            )
        )

    revising = [s["slug"] for s in stages if s["state"] == "revising"]
    if revising:
        findings.append(
            _finding(
                "revising",
                "action",
                f"{len(revising)} stage(s) are being revised after a rejected gate: "
                f"{', '.join(revising)}",
                stages=revising,
            )
        )

    if goal_stop:
        findings.append(
            _finding(
                "goal_stop",
                "warn",
                "A .aidlc-goal-stop marker is present, so the autonomous loop was halted.",
            )
        )

    # The engine writes the digest of the state file it issued a directive for.
    # A mismatch means the state moved on without the directive being retired --
    # typically a crash mid-stage or an edit by hand.
    if isinstance(directive, dict) and state_digest:
        recorded = str(directive.get("state_sha256") or "")
        if recorded and recorded != state_digest:
            findings.append(
                _finding(
                    "stale_directive",
                    "warn",
                    "The active directive references a different state-file digest "
                    "than the state file on disk.",
                    directive_stage=directive.get("stage"),
                    recorded_digest=recorded[:12],
                    actual_digest=state_digest[:12],
                )
            )

    # The file carries its own completed tally; recompute it from the checkboxes.
    claimed = _as_int(state.get("plan", {}).get("Completed"))
    if claimed is not None and claimed != counts.get("completed", 0):
        findings.append(
            _finding(
                "completed_count_mismatch",
                "warn",
                f"Execution Plan Summary claims {claimed} completed stage(s) but "
                f"{counts.get('completed', 0)} are marked [x].",
                claimed=claimed,
                actual=counts.get("completed", 0),
            )
        )

    claimed_total = _as_int(state.get("plan", {}).get("Total Stages"))
    if claimed_total is not None and claimed_total != counts.get("total", 0):
        findings.append(
            _finding(
                "total_count_mismatch",
                "warn",
                f"Execution Plan Summary claims {claimed_total} total stage(s) but "
                f"{counts.get('total', 0)} stage lines were parsed.",
                claimed=claimed_total,
                actual=counts.get("total", 0),
            )
        )

    # Phase Progress vs the checkbox grid. Observed to disagree in the wild
    # (Initialization/Active while Current Status says OPERATION/Completed), so
    # both are shown and neither is corrected.
    phase_status = {
        p["name"].strip().lower(): p["status"].strip().lower()
        for p in state.get("phases", [])
    }
    lifecycle = str(current.get("Lifecycle Phase", "")).strip().lower()
    if lifecycle and phase_status:
        declared = phase_status.get(lifecycle)
        if declared in ("pending", ""):
            findings.append(
                _finding(
                    "phase_stage_disagreement",
                    "warn",
                    f"Current Status says the lifecycle phase is {lifecycle.upper()}, "
                    f"but Phase Progress lists that phase as "
                    f"{(declared or 'absent').title()}.",
                    lifecycle_phase=lifecycle,
                    phase_progress=declared or None,
                )
            )

    if registry_status:
        reg = registry_status.strip().lower()
        status = str(current.get("Status", "")).strip().lower()
        if reg == "complete" and status and status != "completed":
            findings.append(
                _finding(
                    "registry_status_mismatch",
                    "info",
                    f"intents.json marks this intent {registry_status!r} while the "
                    f"state file's Status is {current.get('Status')!r}.",
                    registry_status=registry_status,
                    state_status=current.get("Status"),
                )
            )

    # Slugs the state file carries that this repo's engine no longer knows about
    # (or vice versa) mean the state predates an engine upgrade.
    if graph_slugs:
        state_slugs = {s["slug"] for s in stages}
        unknown = sorted(state_slugs - graph_slugs)
        missing = sorted(graph_slugs - state_slugs)
        if unknown or missing:
            findings.append(
                _finding(
                    "stage_graph_drift",
                    "warn",
                    "The state file and the installed stage-graph.json disagree on "
                    "the stage set, so the state predates an engine change.",
                    only_in_state=unknown,
                    only_in_graph=missing,
                )
            )

    return findings


# --------------------------------------------------------------------------- #
# repository discovery
# --------------------------------------------------------------------------- #


def _repo_id(resolved: Path) -> str:
    """Stable short id so URLs never carry a raw filesystem path."""
    return hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]


def detect_engines(repo: Path) -> list[dict[str, Any]]:
    """Installed AI-DLC engine copies in a repo, newest marker first."""
    found: list[dict[str, Any]] = []
    for name in _ENGINE_DIRS:
        data_dir = repo / name / "tools" / "data"
        harness = _read_json_capped(data_dir / "harness.json")
        utility = repo / name / "tools" / "aidlc-utility.ts"
        if harness is None and not utility.is_file():
            continue
        graph = _read_json_capped(data_dir / "stage-graph.json")
        slugs: list[str] = []
        if isinstance(graph, list):
            slugs = [
                str(item.get("slug"))
                for item in graph
                if isinstance(item, dict) and item.get("slug")
            ]
        found.append(
            {
                "dir": name,
                "harness": harness.get("name") if isinstance(harness, dict) else None,
                "rules_subdir": (
                    harness.get("rulesSubdir") if isinstance(harness, dict) else None
                ),
                "stage_count": len(slugs),
                "stage_slugs": slugs,
                "has_utility": utility.is_file(),
                "marker_mtime": _mtime_iso(data_dir / "harness.json")
                or _mtime_iso(utility),
            }
        )
    return found


def _space_dirs(repo: Path) -> list[Path]:
    base = repo / "aidlc" / "spaces"
    if not base.is_dir():
        return []
    try:
        return sorted(
            p for p in base.iterdir() if p.is_dir() and _SPACE_RE.match(p.name)
        )
    except OSError:
        return []


def _active_cursor(path: Path) -> str | None:
    raw = _read_text_capped(path, 4096)
    return raw.strip() or None if raw else None


def scan_intents(repo: Path) -> tuple[list[dict[str, Any]], str | None]:
    """Every intent in every space, plus the detected layout name.

    Returns ``([], None)`` when the repo carries no recognisable AI-DLC
    workspace at all, and ``layout == "legacy"`` for the pre-``spaces/``
    single-state-file projects that are still around.
    """
    spaces = _space_dirs(repo)
    if spaces:
        intents: list[dict[str, Any]] = []
        active_space = _active_cursor(repo / "aidlc" / "active-space")
        for space in spaces:
            intents_dir = space / "intents"
            if not intents_dir.is_dir():
                continue
            registry = _read_json_capped(intents_dir / "intents.json")
            by_dir: dict[str, dict[str, Any]] = {}
            if isinstance(registry, list):
                for row in registry:
                    if isinstance(row, dict) and row.get("dirName"):
                        by_dir[str(row["dirName"])] = row
            active_intent = _active_cursor(intents_dir / "active-intent")
            try:
                candidates = sorted(
                    p
                    for p in intents_dir.iterdir()
                    if p.is_dir()
                    and not p.name.startswith(".")
                    and _INTENT_DIR_RE.match(p.name)
                )
            except OSError:
                candidates = []
            for intent_dir in candidates[:_MAX_INTENTS_PER_REPO]:
                if not (intent_dir / "aidlc-state.md").is_file():
                    continue
                row = by_dir.get(intent_dir.name, {})
                intents.append(
                    {
                        "space": space.name,
                        "dir_name": intent_dir.name,
                        "slug": row.get("slug") or intent_dir.name,
                        "uuid": row.get("uuid"),
                        "scope": row.get("scope"),
                        "registry_status": row.get("status"),
                        "is_active": (
                            intent_dir.name == active_intent
                            and (active_space is None or space.name == active_space)
                        ),
                        "state_path": str(intent_dir / "aidlc-state.md"),
                        "updated": _mtime_iso(intent_dir / "aidlc-state.md"),
                    }
                )
        return intents, "spaces"

    legacy = repo / _LEGACY_STATE_REL
    if legacy.is_file():
        return (
            [
                {
                    "space": None,
                    "dir_name": "-",
                    "slug": repo.name,
                    "uuid": None,
                    "scope": None,
                    "registry_status": None,
                    "is_active": True,
                    "state_path": str(legacy),
                    "updated": _mtime_iso(legacy),
                }
            ],
            "legacy",
        )
    return [], None


def _intent_paths(repo: Path, space: str | None, dir_name: str) -> Path | None:
    """Resolve an intent directory, refusing anything that escapes the repo."""
    if space is not None and not _SPACE_RE.match(space):
        return None
    if not _INTENT_DIR_RE.match(dir_name):
        return None
    candidate = (
        repo / "aidlc" / "spaces" / space / "intents" / dir_name
        if space
        else repo
    )
    try:
        resolved = candidate.resolve()
        if not resolved.is_relative_to(repo.resolve()):
            return None
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_dir() else None


def load_intent_detail(repo: Path, intent: dict[str, Any]) -> dict[str, Any]:
    """Full projection for one intent: parsed state plus its findings."""
    state_path = Path(intent["state_path"])
    text = _read_text_capped(state_path, _MAX_STATE_BYTES)
    if text is None:
        return {
            **intent,
            "error": "state file unreadable, absent, or over the size cap",
            "state": None,
            "findings": [],
        }

    state = parse_state_file(text)
    intent_dir = state_path.parent
    directive = _read_json_capped(intent_dir / ".aidlc-active-directive.json")
    engines = detect_engines(repo)
    graph_slugs: set[str] | None = None
    for engine in engines:
        if engine["stage_slugs"]:
            graph_slugs = set(engine["stage_slugs"])
            break

    findings = compute_findings(
        state,
        directive=directive,
        state_digest=_sha256_file(state_path),
        registry_status=intent.get("registry_status"),
        graph_slugs=graph_slugs,
        goal_stop=(intent_dir / ".aidlc-goal-stop").is_file(),
    )

    recovery = _read_text_capped(intent_dir / ".aidlc-recovery.md", 16 * 1024)
    audit_dir = intent_dir / "audit"
    audit_tail: list[str] = []
    if audit_dir.is_dir():
        try:
            shards = sorted(
                audit_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True
            )
        except OSError:
            shards = []
        if shards:
            audit_tail = _tail_lines(shards[0])

    return {
        **intent,
        "state": state,
        "findings": findings,
        "directive": directive if isinstance(directive, dict) else None,
        "recovery": recovery,
        "audit_tail": audit_tail,
        "error": None,
    }


def _summarize(detail: dict[str, Any]) -> dict[str, Any]:
    """Row-level shape for the board: no stage list, no audit."""
    state = detail.get("state") or {}
    counts = state.get("counts", {})
    current = state.get("current", {})
    return {
        "space": detail.get("space"),
        "dir_name": detail.get("dir_name"),
        "slug": detail.get("slug"),
        "scope": detail.get("scope"),
        "registry_status": detail.get("registry_status"),
        "is_active": detail.get("is_active"),
        "updated": detail.get("updated"),
        "error": detail.get("error"),
        "project": (state.get("project", {}) or {}).get("Project"),
        "lifecycle_phase": current.get("Lifecycle Phase"),
        "current_stage": current.get("Current Stage"),
        "next_stage": current.get("Next Stage"),
        "status": current.get("Status"),
        "next_action": (state.get("resume", {}) or {}).get("Next Action"),
        "counts": counts,
        "phases": state.get("phases", []),
        "findings": detail.get("findings", []),
    }


# --------------------------------------------------------------------------- #
# repo registry (the only thing this app owns)
# --------------------------------------------------------------------------- #


async def _load_repos(ctx: AppContext) -> list[dict[str, Any]]:
    if ctx.storage is None:
        return []
    try:
        rows = ctx.storage.get(_STORAGE_KEY_REPOS)
    except Exception as exc:  # pragma: no cover - storage is a plain JSON file
        logger.warning("aidlc-console: registry read failed: %s", exc)
        return []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


async def _save_repos(ctx: AppContext, rows: list[dict[str, Any]]) -> None:
    if ctx.storage is None:
        raise web.HTTPServiceUnavailable(
            reason="storage permission is required to register repositories"
        )
    ctx.storage.set(_STORAGE_KEY_REPOS, rows)


def _validate_repo_path(raw: str) -> tuple[Path | None, str | None]:
    """Accept only an absolute, existing, non-sensitive directory."""
    candidate = (raw or "").strip()
    if not candidate:
        return None, "path is required"
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        return None, "path must be absolute"
    try:
        resolved = path.resolve()
    except (OSError, ValueError) as exc:
        return None, f"path could not be resolved: {exc}"
    if not resolved.is_dir():
        return None, "path is not an existing directory"
    if is_sensitive_path(str(resolved)):
        return None, "path is protected and cannot be read"
    return resolved, None


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #


def _json(payload: Any, status: int = 200) -> web.Response:
    return web.json_response(payload, status=status)


def _deny_unauthenticated(request: web.Request) -> web.Response | None:
    """Refuse a request the gateway did not authenticate.

    ``dashboard/token_auth`` sets ``request["user"]`` for an authenticated
    caller, so an absent value means the auth middleware did not vouch for this
    request. Every route here reads the user's source tree, so it fails closed
    rather than trusting reachability of the port.
    """
    if request.get("user") is None:
        return _json({"error": "unauthorized", "code": "unauthorized"}, status=401)
    return None


async def _resolve_registered(
    ctx: AppContext, repo_id: str
) -> tuple[Path | None, dict[str, Any] | None]:
    for row in await _load_repos(ctx):
        if row.get("id") == repo_id:
            resolved, err = _validate_repo_path(str(row.get("path", "")))
            return (None, row) if err else (resolved, row)
    return None, None


async def handle_health(request: web.Request, ctx: AppContext) -> web.Response:
    """Self-check: how many repos are registered and whether bun is present.

    ``bun`` is the only hard runtime AI-DLC has. This app does not need it --
    everything here is a file read -- but reporting it early explains why a
    later write-capable feature would refuse to run.
    """
    repos = await _load_repos(ctx)
    return _json(
        {
            "app": APP_NAME,
            "read_only": True,
            "security_module": _HAS_SECURITY,
            "storage": ctx.storage is not None,
            "registered_repos": len(repos),
            "bun": shutil.which("bun"),
        }
    )


async def handle_list_repos(request: web.Request, ctx: AppContext) -> web.Response:
    out: list[dict[str, Any]] = []
    for row in await _load_repos(ctx):
        resolved, err = _validate_repo_path(str(row.get("path", "")))
        if err or resolved is None:
            out.append({**row, "available": False, "error": err, "engines": [], "intents": 0})
            continue
        intents, layout = scan_intents(resolved)
        out.append(
            {
                "id": row.get("id"),
                "path": str(resolved),
                "label": row.get("label") or resolved.name,
                "added_at": row.get("added_at"),
                "available": True,
                "error": None,
                "layout": layout,
                "engines": [
                    {k: v for k, v in e.items() if k != "stage_slugs"}
                    for e in detect_engines(resolved)
                ],
                "intents": len(intents),
            }
        )
    return _json({"repos": out})


async def handle_add_repo(request: web.Request, ctx: AppContext) -> web.Response:
    try:
        body = await request.json()
    except (ValueError, TypeError):
        return _json({"error": "invalid JSON body", "code": "bad_body"}, status=400)
    if not isinstance(body, dict):
        return _json({"error": "body must be an object", "code": "bad_body"}, status=400)

    resolved, err = _validate_repo_path(str(body.get("path", "")))
    if err or resolved is None:
        return _json({"error": err, "code": "bad_path"}, status=400)

    intents, layout = scan_intents(resolved)
    engines = detect_engines(resolved)
    if not engines and layout is None:
        return _json(
            {
                "error": (
                    "no AI-DLC installation found: expected an engine directory "
                    f"({', '.join(_ENGINE_DIRS)}) with tools/data/harness.json, "
                    "an aidlc/spaces/ workspace, or a legacy aidlc-docs/aidlc-state.md"
                ),
                "code": "not_aidlc_repo",
            },
            status=400,
        )

    rows = await _load_repos(ctx)
    if len(rows) >= _MAX_REPOS:
        return _json(
            {"error": f"at most {_MAX_REPOS} repositories", "code": "too_many"},
            status=409,
        )
    rid = _repo_id(resolved)
    if any(r.get("id") == rid for r in rows):
        return _json({"error": "already registered", "code": "duplicate"}, status=409)

    label = str(body.get("label") or resolved.name).strip()[:120] or resolved.name
    rows.append(
        {
            "id": rid,
            "path": str(resolved),
            "label": label,
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    await _save_repos(ctx, rows)
    return _json(
        {
            "id": rid,
            "path": str(resolved),
            "label": label,
            "layout": layout,
            "intents": len(intents),
            "engines": [{k: v for k, v in e.items() if k != "stage_slugs"} for e in engines],
        },
        status=201,
    )


async def handle_delete_repo(request: web.Request, ctx: AppContext) -> web.Response:
    repo_id = request.match_info.get("repo_id", "")
    rows = await _load_repos(ctx)
    kept = [r for r in rows if r.get("id") != repo_id]
    if len(kept) == len(rows):
        return _json({"error": "not registered", "code": "not_found"}, status=404)
    await _save_repos(ctx, kept)
    return _json({"removed": repo_id})


async def handle_repo_intents(request: web.Request, ctx: AppContext) -> web.Response:
    repo_id = request.match_info.get("repo_id", "")
    resolved, row = await _resolve_registered(ctx, repo_id)
    if row is None:
        return _json({"error": "not registered", "code": "not_found"}, status=404)
    if resolved is None:
        return _json({"error": "repo path unavailable", "code": "unavailable"}, status=409)
    intents, layout = scan_intents(resolved)
    return _json(
        {
            "repo": {"id": repo_id, "path": str(resolved), "label": row.get("label")},
            "layout": layout,
            "intents": [_summarize(load_intent_detail(resolved, i)) for i in intents],
        }
    )


async def handle_intent_detail(request: web.Request, ctx: AppContext) -> web.Response:
    repo_id = request.match_info.get("repo_id", "")
    dir_name = request.match_info.get("intent", "")
    resolved, row = await _resolve_registered(ctx, repo_id)
    if row is None:
        return _json({"error": "not registered", "code": "not_found"}, status=404)
    if resolved is None:
        return _json({"error": "repo path unavailable", "code": "unavailable"}, status=409)

    intents, layout = scan_intents(resolved)
    # Only ever open an intent this scan produced, so a crafted path parameter
    # cannot reach a file outside the discovered set.
    match = next((i for i in intents if i["dir_name"] == dir_name), None)
    if match is None:
        return _json({"error": "intent not found", "code": "not_found"}, status=404)
    if match.get("space") and _intent_paths(resolved, match["space"], dir_name) is None:
        return _json({"error": "intent path rejected", "code": "bad_path"}, status=400)

    detail = load_intent_detail(resolved, match)
    return _json(
        {
            "repo": {"id": repo_id, "path": str(resolved), "label": row.get("label")},
            "layout": layout,
            "intent": detail,
        }
    )


async def handle_board(request: web.Request, ctx: AppContext) -> web.Response:
    """The cross-repo roll-up: the one view that does not exist on disk today."""
    repos_out: list[dict[str, Any]] = []
    totals = {
        "repos": 0,
        "repos_unavailable": 0,
        "intents": 0,
        "in_flight": 0,
        "awaiting_approval": 0,
        "findings": 0,
    }

    for row in await _load_repos(ctx):
        totals["repos"] += 1
        resolved, err = _validate_repo_path(str(row.get("path", "")))
        if err or resolved is None:
            totals["repos_unavailable"] += 1
            repos_out.append(
                {
                    "id": row.get("id"),
                    "label": row.get("label") or row.get("path"),
                    "path": row.get("path"),
                    "available": False,
                    "error": err,
                    "intents": [],
                }
            )
            continue

        intents, layout = scan_intents(resolved)
        summaries = [_summarize(load_intent_detail(resolved, i)) for i in intents]
        totals["intents"] += len(summaries)
        for s in summaries:
            if str(s.get("registry_status") or "").lower() == "in-flight":
                totals["in_flight"] += 1
            gates = s["counts"].get("awaiting_approval", 0) if s.get("counts") else 0
            totals["awaiting_approval"] += gates
            totals["findings"] += sum(
                1 for f in s.get("findings", []) if f.get("severity") in ("warn", "action")
            )
        repos_out.append(
            {
                "id": row.get("id"),
                "label": row.get("label") or resolved.name,
                "path": str(resolved),
                "available": True,
                "error": None,
                "layout": layout,
                "engines": [
                    {k: v for k, v in e.items() if k != "stage_slugs"}
                    for e in detect_engines(resolved)
                ],
                "intents": summaries,
            }
        )

    return _json({"totals": totals, "repos": repos_out, "read_only": True})


# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #


_Handler = Callable[[web.Request, AppContext], Awaitable[web.Response]]


def _authenticated(handler: _Handler) -> _Handler:
    """Wrap a handler so an unauthenticated request never reaches it.

    Applied centrally in :func:`register_routes` rather than inside each
    handler, so adding a route cannot accidentally ship without the check.
    """

    @wraps(handler)
    async def guarded(request: web.Request, ctx: AppContext) -> web.Response:
        denied = _deny_unauthenticated(request)
        if denied is not None:
            return denied
        return await handler(request, ctx)

    guarded.__kirocrew_authenticated__ = True  # type: ignore[attr-defined]
    return guarded


def register_routes(ctx: AppContext) -> list[AppRoute]:
    """Third-party app route contract: ``register_routes(ctx) -> list[AppRoute]``.

    Paths are relative; the gateway serves them under
    ``/api/apps/aidlc-console``. Every route is a read of the filesystem except
    the two that maintain this app's own repo registry.
    """
    ctx.logger.info("aidlc-console: registering read-only projection routes")
    declared: list[tuple[str, str, _Handler]] = [
        ("GET", "/health", handle_health),
        ("GET", "/board", handle_board),
        ("GET", "/repos", handle_list_repos),
        ("POST", "/repos", handle_add_repo),
        ("DELETE", "/repos/{repo_id}", handle_delete_repo),
        ("GET", "/repos/{repo_id}/intents", handle_repo_intents),
        ("GET", "/repos/{repo_id}/intents/{intent}", handle_intent_detail),
    ]
    return [
        AppRoute(method, path, _authenticated(handler))
        for method, path, handler in declared
    ]
