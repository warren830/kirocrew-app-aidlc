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
from typing import Iterable, Mapping, Sequence

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

#: What the bundled engine itself wrote while a team-owned intent (`Unit Ownership: team`, units `unit-a` and
#: `unit-b`, per-Unit stages functional-design and nfr-requirements) went through its Unit gates, one directory
#: per moment: `A*` the main checkout walking every Unit itself (claimless, per-stage rhythm), `B*` the unit-end
#: rhythm, `C*` a teammate's clone that claimed `unit-a`. Driven with `bun` and real `HUMAN_TURN` rows (2026-11-02);
#: only the files a Studio read needs were kept.
TEAM_UNIT_GATE = FIXTURES / "team-unit-gate"
TEAM_UNIT_GATE_INTENT = "261102-ledger"
TEAM_UNIT_GATE_UUID = "01a19a4a-4f0e-7973-9f06-fcffeb2262ce"

#: A real run (2026-11-02) where Studio answered a conductor's mis-logged batch decision ("Q1-Q6 intent capture
#: questions", Options "Q1,…,Q6") with "Q1" and AI-DLC replied only in chat, asking for the six answers again. The
#: tree is the record once that reply had arrived (only the files a Studio read needs); ``conversation.jsonl`` is
#: KiroCrew's transcript of the conversation, the project path written as ``<project-dir>``.
CHAT_ONLY_REPLY = FIXTURES / "chat-only-reply"
CHAT_ONLY_REPLY_INTENT = "261102-add-an-optional"
#: The last bytes of the audit shard are the ``HUMAN_TURN`` Studio's "Q1" minted; the record before it is the one
#: the question card was raised from.
CHAT_ONLY_REPLY_SHARD = "80a99723a8c8-498d2b12f686.md"

#: A UI-driven run that day (v2-full, the same intent name): the person answered intent-capture's Q1 and
#: requirements-analysis's Q2 with Other, Studio sent only their words, and AI-DLC wrote both as option A
#: (``A. I (…) — "Just me…"``, ``A — "Not overdue…"``). The two sheets as AI-DLC wrote them, and
#: ``wire-vectors.json``: what Studio sent then (the transcript's user turns) and sends now, shared with the UI tests.
OTHER_ANSWER_MAPPED = FIXTURES / "other-answer-mapped"

#: The other real run of that day (tasklist, practices-discovery): Studio delivered the person's answers to the
#: sheet's six questions, the conductor recorded them as the mode answer ("Chat"), logged the sheet's questions as
#: one decision ("Practices interview Q1-Q6 …", Options "Q1 A/B/C/X,…"), had its answer refused (no reply since that
#: decision), wrote the answers into the file and told the person their answer had not reached AI-DLC. Studio raised
#: that decision as a one-question card. The tree is the record once the person had answered again in chat and
#: AI-DLC asked the learnings questions (10:18:29); the state is the one Studio captured on every card of the stage
#: (sha256 ``3abb2762…``: the later gate-start changed only the checkbox). ``questions-as-asked.md`` is the sheet as
#: created at 08:43:44, from the transcript (Studio's question digest of the time); ``conversation.jsonl`` is
#: KiroCrew's transcript from the stage's start, the project path written as ``<project-dir>``.
PRACTICES_BATCH = FIXTURES / "practices-batch-decision"
PRACTICES_BATCH_INTENT = "261102-due-dates"
PRACTICES_BATCH_SHARD = "80a99723a8c8-17f552fbc793.md"
PRACTICES_BATCH_QUESTIONS = "inception/practices-discovery/practices-discovery-questions.md"
PRACTICES_BATCH_DECISION = ("Practices interview Q1-Q6 (merge flow, thin slice first, test order and coverage, "
                            "Python and pytest, formatter/linter, firm rules)")
#: Each moment as (the shard's last row, the human-turn marker, the turn counter, the transcript's last row): the mode
#: question asked with the sheet, the batch decision pending with the sheet still open, the sheet filled in with that
#: decision still pending (the conductor's write at 09:24:24 went through ``bun -e``, so no row logged it), and the
#: learnings asked once the person had answered again.
PRACTICES_BATCH_MOMENTS = {
    "asked": ("2026-11-02T08:43:44Z", "2026-11-02T08:41:05Z", 3, "2026-11-02T08:43:51"),
    "open": ("2026-11-02T09:24:14Z", "2026-11-02T09:23:42Z", 4, "2026-11-02T09:24:14"),
    "filled": ("2026-11-02T09:24:14Z", "2026-11-02T09:23:42Z", 4, "2026-11-02T09:24:29"),
    "learnings": ("2026-11-02T10:18:29Z", "2026-11-02T10:16:17Z", 5, "2026-11-02T10:18:35"),
}

#: A real poc run (v2-full, 2026-11-02, plan approval off) through five stages, each ending in the learnings ritual.
#: ``audit-excerpt.md`` is every ritual decision row and the plan-approval skip row, verbatim and in shard order;
#: ``conversation.jsonl`` is each assistant message that asked the ritual; ``code-generation-questions.md`` is the
#: record-only sheet the engine wrote for code generation. The conductor logged the ritual's two rows in either
#: order, and every chat asked the keep question first.
LEARNINGS_RITUAL = FIXTURES / "learnings-ritual"


def learnings_ritual_rows(stage: str, *, event: str = "DECISION_RECORDED") -> list[str]:
    """``stage``'s rows of ``event`` from the excerpt, verbatim, in the order they were appended."""
    blocks = (LEARNINGS_RITUAL / "audit-excerpt.md").read_text("utf-8").split("\n---\n")
    return ["\n" + block.strip("\n") + "\n\n---\n" for block in blocks
            if f"**Event**: {event}\n" in block and f"**Stage**: {stage}\n" in block]


def learnings_ritual_chat(stage_words: str) -> str:
    """The assistant message that asked the ritual, picked by words it alone contains."""
    rows = [json.loads(line) for line in (LEARNINGS_RITUAL / "conversation.jsonl").read_text("utf-8").splitlines()
            if line.strip()]
    [row] = [row for row in rows if stage_words in row["content"]]
    return row["content"]


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


def team_unit_gate_tree(root: Path, moment: str) -> Path:
    """Copy one ``TEAM_UNIT_GATE`` moment into ``root`` with its intent selected.

    A claimed clone keeps no cursor files (the engine git-ignores them) and Studio's submit writes them through
    the engine, so the copy reads as it does once the intent has been selected.
    """
    import shutil

    shutil.copytree(TEAM_UNIT_GATE / moment, root, dirs_exist_ok=True)
    space = root / "aidlc" / "active-space"
    if not space.is_file():
        space.write_text("default\n")
    cursor = root / "aidlc" / "spaces" / "default" / "intents" / "active-intent"
    if not cursor.is_file():
        cursor.write_text(f"{TEAM_UNIT_GATE_INTENT}\n")
    return root


def team_unit_gate_record(root: Path) -> Path:
    return root / "aidlc" / "spaces" / "default" / "intents" / TEAM_UNIT_GATE_INTENT


def chat_only_reply_record(root: Path) -> Path:
    return root / "aidlc" / "spaces" / "default" / "intents" / CHAT_ONLY_REPLY_INTENT


def chat_only_reply_tree(root: Path, *, replied: bool, sheet_receipt: bool = False) -> Path:
    """Copy the ``CHAT_ONLY_REPLY`` record into ``root``: as it was when the card was raised, or with ``replied`` once
    the "Q1" it delivered reached AI-DLC. The earlier moment is the later one without what that reply wrote: the
    shard's final ``HUMAN_TURN``, its marker stamp and its turn-counter bump. Copies take the time of the copy, so the
    later moment's marker reads as the newer one, as it does when the hook writes it.

    As recorded, the batch decision belongs to the sheet's round and raises no card. ``sheet_receipt`` adds, after the
    mode answer, the receipt for the sheet's six answers the conductor tried to record at 07:08:15 (the engine takes a
    second answer from one reply when nothing was asked in between): the decision after it is then a question of its
    own, for the tests of what happens to an answer Studio delivers to one."""
    import shutil

    shutil.copytree(CHAT_ONLY_REPLY / "aidlc", root / "aidlc", dirs_exist_ok=True, copy_function=shutil.copyfile)
    record = chat_only_reply_record(root)
    shard = record / "audit" / CHAT_ONLY_REPLY_SHARD
    if not replied:
        text = shard.read_text("utf-8")
        head, _, tail = text.rpartition("\n## Human Turn\n")
        assert head and "**Timestamp**: 2026-11-02T07:09:18Z" in tail
        shard.write_text(head, "utf-8")
        (record / ".aidlc-engine" / "human-turn").write_text("2026-11-02T07:07:49Z\n")
        counter = root / "aidlc" / ".aidlc-turn-counter"
        counter.write_text(str(int(counter.read_text().strip()) - 1) + "\n")
    if sheet_receipt:
        text = shard.read_text("utf-8")
        mode_answer = text.index("**Timestamp**: 2026-11-02T07:08:23Z\n**Event**: QUESTION_ANSWERED")
        refused = text.index("**Command**: aidlc-log answer --stage intent-capture --details Q1: ")
        details = text[refused:text.index("\n", refused)].split("--details ", 1)[1]
        cut = text.index("\n---\n", mode_answer) + len("\n---\n")
        receipt = audit_block("QUESTION_ANSWERED", "2026-11-02T07:08:23Z", Stage="intent-capture", Details=details)
        shard.write_text(text[:cut] + receipt + text[cut:], "utf-8")
    return root


def chat_only_reply_rows() -> list[dict]:
    """The transcript's message rows, oldest first (its metadata line is not a message)."""
    rows = [json.loads(line) for line in (CHAT_ONLY_REPLY / "conversation.jsonl").read_text("utf-8").splitlines()
            if line.strip()]
    return [row for row in rows if "role" in row]


def practices_batch_record(root: Path) -> Path:
    return root / "aidlc" / "spaces" / "default" / "intents" / PRACTICES_BATCH_INTENT


def practices_batch_tree(root: Path, moment: str) -> Path:
    """Copy the ``PRACTICES_BATCH`` record into ``root`` as it was at one of ``PRACTICES_BATCH_MOMENTS``. An earlier
    moment is the later record without what came after: the shard cut after its last row, that moment's human-turn
    marker and turn counter, and before 09:24:24 the sheet as created. The diary stays the learnings' one: nothing
    read before then depends on it."""
    import shutil

    last_row, human_turn, counter, _ = PRACTICES_BATCH_MOMENTS[moment]
    shutil.copytree(PRACTICES_BATCH / "aidlc", root / "aidlc", dirs_exist_ok=True, copy_function=shutil.copyfile)
    record = practices_batch_record(root)
    shard = record / "audit" / PRACTICES_BATCH_SHARD
    text = shard.read_text("utf-8")
    shard.write_text(text[:text.index("\n---\n", text.rindex(f"**Timestamp**: {last_row}")) + len("\n---\n")], "utf-8")
    (record / ".aidlc-engine" / "human-turn").write_text(f"{human_turn}\n")
    (root / "aidlc" / ".aidlc-turn-counter").write_text(f"{counter}\n")
    if moment in ("asked", "open"):
        shutil.copyfile(PRACTICES_BATCH / "questions-as-asked.md", record / PRACTICES_BATCH_QUESTIONS)
    return root


def practices_batch_rows(moment: str) -> list[dict]:
    """The transcript's message rows up to the end of ``moment``, oldest first."""
    end = PRACTICES_BATCH_MOMENTS[moment][3]
    rows = [json.loads(line) for line in (PRACTICES_BATCH / "conversation.jsonl").read_text("utf-8").splitlines()
            if line.strip()]
    return [row for row in rows if "role" in row and row["ts"][:19] <= end]


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


#: The request id a 2.11 review pair shares; the completion answers the request that carries it.
REVIEW_REQUEST_ID = "review:" + "f3" * 16


def review_record(stage: str, *, verdict: str = "READY", reviewer: str = "aidlc-architecture-reviewer-agent",
                  iteration: int = 1, unit: str | None = None, findings: Sequence[Mapping[str, str]] = (),
                  attempt: str = "11d5af9134c10ab1", request_id: str = REVIEW_REQUEST_ID,
                  body: str | None = None) -> tuple[str, str]:
    """A 2.11 review record: ``(its path under the intent record, its exact JSON text)``, keyed as 2.11 writes it."""
    scope = f"units/{unit}" if unit else "stage"
    record = {
        "version": 1, "stage": stage, "unit": unit, "workflow": None, "attempt": attempt, "iteration": iteration,
        "reviewer": reviewer, "verdict": verdict, "request_id": request_id, "request_challenge": None,
        "artifact_fingerprint": "sha256:" + "5d" * 32, "source_fingerprint": None, "unit_source_fingerprint": None,
        "findings": [dict(row) for row in findings],
        "body": f"## Review\n\n**Verdict:** {verdict}\n" if body is None else body,
        "recorded_at": "2026-10-13T09:30:24Z",
    }
    return f".aidlc-engine/reviews/{stage}/{scope}/{attempt}/{iteration}.json", json.dumps(record, indent=2) + "\n"


def review_audit(stage: str, record: tuple[str, str] | None, *, ts: str = "2026-09-04T09:00:01Z",
                 verdict: str = "READY", reviewer: str = "aidlc-architecture-reviewer-agent", iteration: int = 1,
                 unit: str | None = None, request_id: str = REVIEW_REQUEST_ID) -> str:
    """The ``REVIEW_REQUESTED`` / ``REVIEW_COMPLETED`` pair around a verdict; ``record`` is what the completion names
    (2.11), or ``None`` for a row from before review records."""
    scope = {"Unit": unit} if unit else {}
    pinned = {} if record is None else {"Review_Record": record[0], "Review_Record_Digest": "sha256:" + sha256(record[1])}
    return audit_text(
        audit_block("REVIEW_REQUESTED", ts, Stage=stage, Reviewer=reviewer, Iteration=str(iteration),
                    Request_Id=request_id, **scope),
        audit_block("REVIEW_COMPLETED", _plus(ts, 1), Stage=stage, Reviewer=reviewer, Iteration=str(iteration),
                    Verdict=verdict, Request_Id=request_id, **scope, **pinned),
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
