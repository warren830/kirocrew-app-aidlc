"""The bundled engine (AI-DLC 2.11.3) puts questions of its own on the record, and Studio reads them as asked.

2.11.3 writes ``QUESTION_ASKED`` when it hands out a question that has no row of its own. One is new: the go-ahead
for a system package install nobody asked for, which the plan-approval guard holds (``SYSTEM_INSTALL_BLOCKED``) while
the agent asks in the conversation; the person's turn lets the retry through (``SYSTEM_INSTALL_ALLOWED``). A question
or a choice holding a quote, ``$`` or a backtick now reaches ``log decision`` in files the agent wrote, so the record
keeps it as written. Real Bun, the real bundled hooks, a throwaway copy of the payload; Studio's reader and projection
read the record those hooks wrote.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import conftest as CT
import pytest

from test_projection import SessionStub, SlotStub, proj_mod, projection, repo_record  # noqa: F401

A = CT.studio_module("actions")
activity = CT.studio_module("activity")
C = CT.studio_module("constants")
reader = CT.studio_module("aidlc_reader")
PAYLOAD = Path(__file__).resolve().parents[1] / "payload" / "aidlc-kiro"
_FALLBACKS = (Path.home() / ".local" / "bin" / "bun", Path.home() / ".bun" / "bin" / "bun")
BUN = shutil.which("bun") or next((str(path) for path in _FALLBACKS if path.exists()), None)
SESSION = "0f0e0d0c-1111-2222-3333-444455556666"
STAGE = "intent-capture"
INSTALL = "sudo apt-get install -y python3-pytest"
QUESTION = 'Which price should the free plan show: "$0.00" or `free`?'
CHOICES = ("Show $0.00, as the plan says", "Show `free`, like the pricing page", "Show both, in that order")

pytestmark = pytest.mark.skipif(BUN is None, reason="bun is not installed")


def _env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith(("AIDLC_", "CLAUDE_", "KIRO_"))}


def bun(repo: Path, *args: str, stdin: str | None = None, code: int = 0) -> subprocess.CompletedProcess:
    done = subprocess.run([BUN, *args], cwd=repo, input=stdin, text=True, capture_output=True, env=_env(), timeout=120)
    assert done.returncode == code, (args, done.stdout[-2000:], done.stderr[-2000:])
    return done


def hook(repo: Path, target: str, event: str, code: int = 0, **fields) -> subprocess.CompletedProcess:
    payload = {"hook_event_name": event, "cwd": str(repo), "session_id": SESSION, **fields}
    return bun(repo, ".kiro/tools/aidlc.ts", "engine", "adapter", "kiro", target, stdin=json.dumps(payload), code=code)


def person_says(repo: Path, words: str) -> None:
    framed = ("[RUNTIME] KiroCrew dashboard\nThis is the interface carrying the current user message.\n\n"
              f"[CURRENT USER REQUEST -- respond to this]\n{words}")
    hook(repo, "verb-intercept", "userPromptSubmit", prompt=framed)


def shell(repo: Path, command: str, code: int = 0) -> subprocess.CompletedProcess:
    return hook(repo, "plan-approval-guard", "preToolUse", code=code,
                tool_name="execute_bash", tool_input={"command": command})


def agent_writes(repo: Path, path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    hook(repo, "audit-and-sensors", "postToolUse", tool_name="fs_write",
         tool_input={"command": "create", "path": str(path), "file_text": text})


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(PAYLOAD, root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    bun(root, ".kiro/tools/aidlc-utility.ts", "intent-create", "--scope", "poc",
        "--arguments", "Add an optional due date to tasks", "--label", "add-due-date")
    hook(root, "session-start", "agentSpawn")
    return root


def intent_dir(repo: Path) -> str:
    [state] = repo.glob("aidlc/spaces/default/intents/*/aidlc-state.md")
    return state.parent.name


def record(repo: Path) -> Path:
    return repo / "aidlc" / "spaces" / "default" / "intents" / intent_dir(repo)


def audit(repo: Path, clock) -> "reader.AuditBundle":
    return reader.AidlcReader(clock).read_audit(repo, "default", intent_dir(repo))


def events(repo: Path, clock) -> list[tuple[str, dict]]:
    return [(e.event, dict(e.fields)) for e in audit(repo, clock).events]


def test_a_held_install_and_its_go_ahead_are_activity_studio_can_name(repo, clock):
    held = shell(repo, INSTALL, code=2)
    assert "installs a system package" in held.stdout + held.stderr, "the guard holds the install"
    person_says(repo, "Yes, install it")
    shell(repo, INSTALL)

    rows = events(repo, clock)
    names = [name for name, _ in rows]
    blocked = names.index("SYSTEM_INSTALL_BLOCKED")
    assert names[blocked + 1:] == ["QUESTION_ASKED", "HUMAN_TURN", "SYSTEM_INSTALL_ALLOWED"]
    asked = rows[blocked + 1][1]
    assert (asked["Question Type"], asked["Stage"]) == ("system-install", STAGE)
    assert rows[-1][1]["Person Reply"] == "Yes, install it", "the go-ahead names the person's own words"

    unnamed = sorted({name for name in names if activity.audit_message_key(name) == activity.UNKNOWN_AUDIT_MESSAGE_KEY})
    assert unnamed == [], "every row of the run has a sentence of its own on the Activity page"
    severity = {e.event: activity.severity_of_audit_event(e) for e in audit(repo, clock).events}
    assert severity["SYSTEM_INSTALL_BLOCKED"] == "attention", "the held command waits on the person"
    assert {severity["QUESTION_ASKED"], severity["SYSTEM_INSTALL_ALLOWED"]} == {"info"}


def test_the_install_question_after_engine_errors_is_what_the_queue_shows(repo, clock, projection, repo_record):
    for _ in range(3):
        bun(repo, ".kiro/tools/aidlc.ts", "engine", "log", "answer", "--stage", STAGE, "--details", "Yes", code=1)
    shell(repo, INSTALL, code=2)
    assert [name for name, _ in events(repo, clock)][-5:] == [
        "ERROR_LOGGED", "ERROR_LOGGED", "ERROR_LOGGED", "SYSTEM_INSTALL_BLOCKED", "QUESTION_ASKED",
    ]

    snap = projection.snapshot(repo_record(repo, installed_engine_version=C.BUNDLED_ENGINE_VERSION), "default",
                               intent_dir(repo))
    assert snap.stable and snap.audit.complete and snap.stage == STAGE
    asked = SlotStub(has_options=True, options=("Yes, install it", "I'll install it myself"),
                     last_turn_ts=snap.audit.events[-1].timestamp)
    cards = projection.derive_cards(snap, findings=(), binding=SessionStub(), install=None, breakers=(),
                                    live_actions=(), slot=asked)
    assert [(card.type, card.reason) for card in cards] == [("missing_input", "conversation_reply")], (
        "the agent's question about the install, not Retry now on the refusals before it")


def test_a_question_with_shell_characters_reaches_the_card_and_its_receipt_whole(repo, clock):
    files = record(repo) / ".aidlc-engine" / "answer-text"
    agent_writes(repo, files / "question.txt", QUESTION)
    agent_writes(repo, files / "options.txt", "\n".join(CHOICES) + "\n")
    bun(repo, ".kiro/tools/aidlc.ts", "engine", "log", "decision", "--stage", STAGE,
        "--decision-file", ".aidlc-engine/answer-text/question.txt",
        "--options-file", ".aidlc-engine/answer-text/options.txt")
    names = [name for name, _ in events(repo, clock)]
    assert names[-3:] == ["ARTIFACT_CREATED", "ARTIFACT_CREATED", "DECISION_RECORDED"], (
        "the agent's question files are logged before the question they hold")

    record_rel = reader.AidlcReader.record_rel("default", intent_dir(repo))
    questions = reader.audit_questions(audit(repo, clock), record_rel, STAGE, None)
    assert questions is not None, "the decision is a pending question"
    [question] = questions.questions
    assert (question.prompt, tuple(option.text for option in question.options)) == (QUESTION, CHOICES)

    wire = A.wire_text_for("answers", {"answers": [{"index": 1, "option_letters": ["A"], "free_text": None}]},
                           questions=questions, grouped_answers_enabled=True)
    assert wire == CHOICES[0]
    since = questions.origin["timestamp"]
    person_says(repo, wire)
    agent_writes(repo, files / "answer.txt", wire)
    bun(repo, ".kiro/tools/aidlc.ts", "engine", "log", "answer", "--stage", STAGE,
        "--details-file", ".aidlc-engine/answer-text/answer.txt")
    receipt = reader.audit_question_answer(audit(repo, clock), questions.origin, wire, since, project_dir=str(repo))
    assert receipt is not None and receipt.fields["Details"] == wire, "the card settles on the engine's receipt"
    assert re.search(r"\$0\.00", receipt.raw), "the record keeps the dollar sign"
