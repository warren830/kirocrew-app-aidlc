"""Helpers for building AI-DLC test inputs from the harvested real fixtures.

The three fixture trees under this directory are byte-faithful copies of real installs (see
``FORMAT-NOTES.md`` and each tree's ``README.md``). Tests need *variants* of those files — the same
33-stage state file but with one stage row at ``[?]``, the same audit shard plus a ``GATE_APPROVED``
block — and committing a dozen near-identical copies would rot the moment the real format moves.

So variants are derived here, at test time, from the real bytes. Every helper is a pure function over
text: it never touches a repository, and the transformations are the same edits the engine itself
makes (flip a checkbox mark, append an audit block, blank an answer tag), so a derived file stays
parseable by the same grammar as its source.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Iterable, Mapping

FIXTURES = Path(__file__).resolve().parent

#: The 2.6.2 / State Version 8 / 33-stage tree harvested from a real Kiro install.
DEVDELTA = FIXTURES / "aidlc-2.6.2-devdelta"
#: 2.3.0 engine data (32 stages) plus an authored State Version 7 workspace.
PAYLOAD_230 = FIXTURES / "aidlc-2.3.0-payload"
#: A real 2.1.1 install: the oldest format Studio's parsers must still read.
OLDER = FIXTURES / "aidlc-older"

#: Intent record directories inside each tree, by tree.
DEVDELTA_INTENTS = DEVDELTA / "aidlc" / "spaces" / "default" / "intents"
PAYLOAD_INTENTS = PAYLOAD_230 / "aidlc" / "spaces" / "default" / "intents"
OLDER_INTENTS = OLDER / "aidlc" / "spaces" / "default" / "intents"

#: Convenient names for the records tests use most.
DEVDELTA_POC = "260828-kiro-impact-poc"          # completed, stale directive, goal-stop marker
DEVDELTA_REMEDIATION = "260814-review-major-remediation"  # in-flight, custom scope
PAYLOAD_GATE_OPEN = "260901-ledger-export"       # [?] at requirements-analysis, Revision Count 1
PAYLOAD_COMPLETED = "260820-health-probe"        # all EXECUTE rows [x]
OLDER_INTENT = "260707-trello-plugin"

_CHECKBOX_LINE = re.compile(r"^- \[([ xXsSrR?\-])\] (\S+)(?:\s*(?:—|–|--)\s*(.*))?$", re.MULTILINE)
_FIELD_LINE = re.compile(r"^- \*\*(.+?)\*\*:[ \t]*(.*?)[ \t]*$", re.MULTILINE)

#: Heading AI-DLC writes for each audit event type. Only the events tests build are listed; an unknown
#: event falls back to its own name, which is what the engine does.
AUDIT_HEADINGS = {
    "HUMAN_TURN": "Human Turn",
    "STAGE_STARTED": "Stage Started",
    "STAGE_COMPLETED": "Stage Completed",
    "STAGE_SKIPPED": "Stage Skipped",
    "STAGE_JUMPED": "Stage Jumped",
    "STAGE_AWAITING_APPROVAL": "Stage Awaiting Approval",
    "STAGE_REVISING": "Stage Revising",
    "GATE_APPROVED": "Gate Approved",
    "GATE_REJECTED": "Gate Rejected",
    "QUESTION_ANSWERED": "Question Answered",
    "DECISION_RECORDED": "Decision Recorded",
    "SUMMARY_CONFIRMATION_RECORDED": "Summary Confirmation Recorded",
    "REVIEW_REQUESTED": "Review Requested",
    "REVIEW_COMPLETED": "Review Completed",
    "SESSION_STARTED": "Session Started",
    "ERROR_LOGGED": "Error Logged",
    "PHASE_STARTED": "Phase Started",
    "PHASE_COMPLETED": "Phase Completed",
    "PHASE_VERIFIED": "Phase Verified",
    "WORKFLOW_STARTED": "Workflow Started",
    "WORKFLOW_COMPLETED": "Workflow Completed",
    "WORKFLOW_PARKED": "Workflow Parked",
    "WORKFLOW_UNPARKED": "Workflow Unparked",
    "SCOPE_CHANGED": "Scope Changed",
    "RECOMPOSED": "Recomposed",
    "HEALTH_CHECKED": "Health Checked",
    "SUBAGENT_COMPLETED": "Subagent Completed",
}


# --------------------------------------------------------------------------- #
# reading real fixture bytes
# --------------------------------------------------------------------------- #


def read(path: Path) -> str:
    return path.read_text("utf-8")


def state_path(tree: Path, intent_dir: str, *, space: str = "default") -> Path:
    return tree / "aidlc" / "spaces" / space / "intents" / intent_dir / "aidlc-state.md"


def audit_paths(tree: Path, intent_dir: str, *, space: str = "default") -> list[Path]:
    audit = tree / "aidlc" / "spaces" / space / "intents" / intent_dir / "audit"
    return sorted(audit.glob("*.md")) if audit.is_dir() else []


def real_state(tree: Path = DEVDELTA, intent_dir: str = DEVDELTA_POC) -> str:
    """The verbatim text of a real state file (33 stages, State Version 8 by default)."""
    return read(state_path(tree, intent_dir))


def real_audit(tree: Path = DEVDELTA, intent_dir: str = DEVDELTA_POC) -> str:
    """The verbatim text of the largest audit shard of a record."""
    paths = audit_paths(tree, intent_dir)
    if not paths:
        raise FileNotFoundError(f"no audit shard in {tree}/{intent_dir}")
    return read(max(paths, key=lambda p: p.stat().st_size))


def real_questions(tree: Path = DEVDELTA, intent_dir: str = DEVDELTA_POC,
                   stage_rel: str = "inception/requirements-analysis") -> str:
    record = tree / "aidlc" / "spaces" / "default" / "intents" / intent_dir / stage_rel
    matches = sorted(record.glob("*-questions.md"))
    if not matches:
        raise FileNotFoundError(f"no questions file in {record}")
    return read(matches[0])


def intents_registry(tree: Path = DEVDELTA, *, space: str = "default") -> list[dict]:
    path = tree / "aidlc" / "spaces" / space / "intents" / "intents.json"
    return json.loads(read(path))


def stage_graph(tree: Path = DEVDELTA) -> list[dict]:
    return json.loads(read(tree / ".kiro" / "tools" / "data" / "stage-graph.json"))


def scope_grid(tree: Path = DEVDELTA) -> dict:
    return json.loads(read(tree / ".kiro" / "tools" / "data" / "scope-grid.json"))


# --------------------------------------------------------------------------- #
# deriving state-file variants
# --------------------------------------------------------------------------- #


def state_text(
    source: str | Path = DEVDELTA_POC,
    *,
    tree: Path = DEVDELTA,
    marks: Mapping[str, str] | None = None,
    fields: Mapping[str, str] | None = None,
    modes: Mapping[str, str] | None = None,
    drop_fields: Iterable[str] = (),
) -> str:
    """A real state file with specific rows and fields patched.

    ``marks`` maps a stage slug to a checkbox mark (``" "``, ``"-"``, ``"?"``, ``"R"``, ``"x"``,
    ``"S"``). ``modes`` maps a slug to the suffix after the em dash (``"EXECUTE"``, ``"SKIP (…)"``).
    ``fields`` patches ``- **Field**: value`` lines anywhere in the file, and ``drop_fields`` removes
    them — that is how a v8 file is turned into something a v7 parser would see.

    A slug that is not in the file, or a field that does not exist, raises: silently doing nothing
    would produce a fixture that does not test what its name claims.
    """
    text = read(source) if isinstance(source, Path) else real_state(tree, str(source))

    for slug, mark in (marks or {}).items():
        if len(mark) != 1:
            raise ValueError(f"checkbox mark must be one character, got {mark!r}")
        pattern = re.compile(rf"^- \[[ xXsSrR?\-]\] {re.escape(slug)}\b", re.MULTILINE)
        text, n = pattern.subn(f"- [{mark}] {slug}", text)
        if n == 0:
            raise KeyError(f"stage row {slug!r} not present in the source state file")

    for slug, mode in (modes or {}).items():
        pattern = re.compile(
            rf"^(- \[[ xXsSrR?\-]\] {re.escape(slug)})(?:\s*(?:—|–|--)\s*.*)?$", re.MULTILINE
        )
        text, n = pattern.subn(rf"\1 — {mode}", text)
        if n == 0:
            raise KeyError(f"stage row {slug!r} not present in the source state file")

    for key, value in (fields or {}).items():
        pattern = re.compile(rf"^- \*\*{re.escape(key)}\*\*:.*$", re.MULTILINE)
        replacement = f"- **{key}**: {value}" if value != "" else f"- **{key}**: "
        text, n = pattern.subn(replacement.replace("\\", "\\\\"), text)
        if n == 0:
            raise KeyError(f"field {key!r} not present in the source state file")

    for key in drop_fields:
        pattern = re.compile(rf"^- \*\*{re.escape(key)}\*\*:.*\n", re.MULTILINE)
        text, n = pattern.subn("", text)
        if n == 0:
            raise KeyError(f"field {key!r} not present in the source state file")

    return text


def insert_runtime_field(text: str, key: str, value: str) -> str:
    """Add a ``## Runtime State`` field (``Parked``, ``Parked At Stage``, ``Skeleton Stance``).

    Inserted directly after the ``Revision Count`` row, which is where the engine writes them.
    """
    pattern = re.compile(r"^(- \*\*Revision Count\*\*:.*)$", re.MULTILINE)
    if not pattern.search(text):
        raise KeyError("state file has no Revision Count row to anchor on")
    return pattern.sub(rf"\1\n- **{key}**: {value}", text, count=1)


def stage_marks(text: str) -> dict[str, str]:
    """Every stage row of a state file as ``{slug: mark}`` — the assertion helper for tests."""
    return {slug: mark for mark, slug, _mode in _CHECKBOX_LINE.findall(text)}


def state_fields(text: str) -> dict[str, str]:
    return dict(_FIELD_LINE.findall(text))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# deriving audit variants
# --------------------------------------------------------------------------- #


def audit_block(event: str, ts: str, **fields: str) -> str:
    """One audit block in the engine's exact wire format.

    ``\\n## <Heading>\\n**Timestamp**: <iso>\\n**Event**: <EVENT>\\n[**Key**: value…]\\n\\n---\\n``
    — the leading newline and the trailing fence are part of the format, so concatenating blocks
    produces a shard byte-compatible with what the engine writes. Newlines inside a value are escaped
    to a literal ``\\n`` the way the engine escapes them.
    """
    heading = AUDIT_HEADINGS.get(event, event)
    lines = [f"\n## {heading}", f"**Timestamp**: {ts}", f"**Event**: {event}"]
    for key, value in fields.items():
        label = key.replace("_", " ")
        text = str(value).replace("\r\n", "\\n").replace("\n", "\\n")
        lines.append(f"**{label}**: {text}")
    return "\n".join(lines) + "\n\n---\n"


def audit_text(*blocks: str) -> str:
    """Join blocks into a shard body."""
    return "".join(blocks)


def gate_open_audit(stage: str, *, ts: str = "2026-09-04T09:00:00Z", human_ts: str | None = None) -> str:
    """A shard whose latest relevant rows leave ``stage`` waiting at an open gate."""
    blocks = [
        audit_block("SESSION_STARTED", ts, Source="startup"),
        audit_block("HUMAN_TURN", human_ts or _plus(ts, 1)),
        audit_block("STAGE_STARTED", _plus(ts, 2), Stage=stage),
        audit_block("STAGE_AWAITING_APPROVAL", _plus(ts, 3), Stage=stage),
    ]
    return audit_text(*blocks)


def gate_approved_audit(stage: str, next_stage: str = "user-stories", *,
                        ts: str = "2026-09-04T09:00:00Z", user_input: str = "Approve") -> str:
    """``gate_open_audit`` plus the human turn and the engine's approval of it."""
    return audit_text(
        gate_open_audit(stage, ts=ts),
        audit_block("HUMAN_TURN", _plus(ts, 10)),
        audit_block("GATE_APPROVED", _plus(ts, 11), Stage=stage, User_Input=user_input),
        audit_block("STAGE_COMPLETED", _plus(ts, 11), Stage=stage),
        audit_block("STAGE_STARTED", _plus(ts, 12), Stage=next_stage),
    )


def gate_rejected_audit(stage: str, *, ts: str = "2026-09-04T09:00:00Z",
                        feedback: str = "Needs the acceptance criteria spelled out.") -> str:
    return audit_text(
        gate_open_audit(stage, ts=ts),
        audit_block("HUMAN_TURN", _plus(ts, 10)),
        audit_block("GATE_REJECTED", _plus(ts, 11), Stage=stage, Feedback=feedback),
        audit_block("STAGE_REVISING", _plus(ts, 11), Stage=stage, Revision_count="1", Feedback=feedback),
    )


def error_burst_audit(*, ts: str = "2026-09-04T09:00:00Z", count: int = 3,
                      error: str = "ACP transport closed") -> str:
    """Consecutive ``ERROR_LOGGED`` rows with one fingerprint — the breaker's input."""
    return audit_text(
        *[
            audit_block(
                "ERROR_LOGGED",
                _plus(ts, 60 * i),
                Tool="aidlc-orchestrate.ts",
                Command="next",
                Error=error,
            )
            for i in range(count)
        ]
    )


def _plus(iso: str, seconds: int) -> str:
    """ISO-Z timestamp arithmetic without importing a clock into fixtures."""
    from datetime import datetime, timedelta, timezone

    base = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (base + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# deriving questions variants
# --------------------------------------------------------------------------- #


def blank_answers(text: str, *, keep: int = 0) -> str:
    """Blank every ``[Answer]:`` tag except the first ``keep`` of them.

    Turns a real answered questions file into the unanswered form the engine writes when a stage first
    asks, which is what the Action Center's question card is derived from.
    """
    parts = text.split("[Answer]:")
    if len(parts) == 1:
        return text
    out = [parts[0]]
    for index, chunk in enumerate(parts[1:]):
        if index < keep:
            out.append("[Answer]:" + chunk)
            continue
        # Drop the answered value (everything up to the next blank line) and leave an empty tag.
        remainder = chunk.split("\n\n", 1)
        tail = remainder[1] if len(remainder) > 1 else ""
        out.append("[Answer]:\n\n" + tail)
    return "".join(out)


def questions_text(
    *,
    stage: str = "requirements-analysis",
    questions: Iterable[tuple[str, list[str]]] = (),
    answers: Mapping[int, str] | None = None,
) -> str:
    """An authored questions file in the engine's format (options A…, plus ``X. Other``)."""
    answers = answers or {}
    lines = [f"# {stage.replace('-', ' ').title()} — Questions", "", f"Stage: `{stage}`", "", "---", ""]
    for index, (prompt, options) in enumerate(questions, start=1):
        lines += [f"## Q{index}. {prompt}", ""]
        for letter, option in zip("ABCDE", options):
            lines.append(f"- {letter}. {option}")
        lines += ["- X. Other (please specify)", "", f"[Answer]: {answers.get(index, '')}".rstrip(), ""]
    return "\n".join(lines) + "\n"
