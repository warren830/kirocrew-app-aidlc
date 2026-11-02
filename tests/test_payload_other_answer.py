"""The bundled engine records Studio's grouped answers with an Other answer as the person's own words.

On 2026-11-02 Studio sent an Other answer as the person's words alone, and AI-DLC wrote it as option A
(``tests/fixtures/other-answer-mapped``). Studio now names each option as the questions file does and puts the
file's ``X. Other …`` before the person's words. The conductor is a model, so what these show is the engine's
side of it, with the real payload under bun: the prompt hook keeps Studio's text as the person's reply, unchanged,
and the receipt and the questions file take the answers the way the delivery note asks for them (``X.`` and the
words verbatim, letters for the picks).
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import conftest as CT
import fixtures as F

A = CT.studio_module("actions")
C = CT.studio_module("constants")
reader = CT.studio_module("aidlc_reader")
ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "payload" / "aidlc-kiro"
_FALLBACKS = (Path.home() / ".local" / "bin" / "bun", Path.home() / ".bun" / "bin" / "bun")
BUN = shutil.which("bun") or next((str(path) for path in _FALLBACKS if path.exists()), None)
SESSION = "0f0e0d0c-1111-2222-3333-444455556666"
STAGE = "intent-capture"

pytestmark = pytest.mark.skipif(BUN is None, reason="bun is not installed")


def framed(turn: str) -> str:
    """A dashboard turn as KiroCrew hands it to kiro-cli: its context blocks, then the person's text."""
    return (
        "[RUNTIME] KiroCrew dashboard\nThis is the interface carrying the current user message.\n\n"
        "[PROJECT] Active project directory: /repo\n\n[REPLY FORMAT RULES]\n\n"
        "(If presenting choices, end with [OPTIONS: choice1 | choice2] as the very last line.)"
        f"[CURRENT USER REQUEST -- respond to this]\n{turn}"
    )


def _env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith(("AIDLC_", "CLAUDE_"))}


def bun(repo: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    done = subprocess.run([BUN, *args], cwd=repo, input=stdin, text=True, capture_output=True, env=_env(), timeout=120)
    assert done.returncode == 0, (args, done.stdout[-2000:], done.stderr[-2000:])
    return done


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(PAYLOAD, root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    bun(root, ".kiro/tools/aidlc-utility.ts", "intent-create", "--scope", "poc",
        "--arguments", "Add an optional due date to tasks and mark overdue tasks in the list output",
        "--label", "add-an-optional")
    return root


def record(repo: Path) -> Path:
    [found] = repo.glob("aidlc/spaces/default/intents/*/aidlc-state.md")
    return found.parent


def rows(repo: Path, event: str) -> list[dict[str, str]]:
    found = []
    for shard in sorted((record(repo) / "audit").glob("*.md")):
        for block in shard.read_text().split("\n---\n"):
            fields = dict(re.findall(r"^\*\*([^*]+)\*\*: (.*)$", block, re.M))
            if fields.get("Event") == event:
                found.append(fields)
    return found


def test_the_engine_keeps_an_other_answer_as_the_persons_words(repo):
    sheet = next(s for s in json.loads((F.OTHER_ANSWER_MAPPED / "wire-vectors.json").read_text("utf-8"))["sheets"]
                 if s["file"] == f"{STAGE}-questions.md")
    real = (F.OTHER_ANSWER_MAPPED / sheet["file"]).read_text("utf-8")
    blank = re.sub(r"^\[Answer\]:.*$", "[Answer]:", real, flags=re.M)
    questions = record(repo) / "ideation" / STAGE / sheet["file"]
    questions.parent.mkdir(parents=True, exist_ok=True)
    questions.write_text(blank)
    relpath = str(questions.relative_to(repo))
    wire = A.wire_text_for("answers", {"answers": sheet["answers"]},
                           questions=reader.parse_questions_file(blank, relpath, "sha"), grouped_answers_enabled=True)
    assert wire == "\n".join(sheet["lines"]) + C.WIRE_GROUPED_ANSWER_SUFFIX

    # The conductor asks the sheet's questions, and Studio's reply arrives through the prompt hook.
    bun(repo, ".kiro/tools/aidlc.ts", "engine", "log", "decision", "--stage", STAGE,
        "--decision", "Q1-Q5 intent capture questions", "--options", "Q1,Q2,Q3,Q4,Q5")
    payload = {"hook_event_name": "userPromptSubmit", "cwd": str(repo), "session_id": SESSION, "prompt": framed(wire)}
    bun(repo, ".kiro/tools/aidlc.ts", "engine", "adapter", "kiro", "verb-intercept", stdin=json.dumps(payload))

    # As the delivery note asks: X. and the words verbatim, letters for the picks, and one receipt (its text holds
    # quotes and a dash, so it goes in the engine's answer-text file).
    tags = {1: "X. " + sheet["answers"][0]["free_text"], 2: "A, B", 3: "A", 4: "A", 5: "A, B"}
    filled = iter(tags.values())
    questions.write_text(re.sub(r"^\[Answer\]:$", lambda _: f"[Answer]: {next(filled)}", blank, flags=re.M))
    details = "; ".join(sheet["lines"])
    answer_file = record(repo) / ".aidlc-engine" / "answer-text" / "answer.txt"
    answer_file.parent.mkdir(parents=True, exist_ok=True)
    answer_file.write_text(details)
    out = json.loads(bun(repo, ".kiro/tools/aidlc.ts", "engine", "log", "answer", "--stage", STAGE,
                         "--details-file", ".aidlc-engine/answer-text/answer.txt").stdout)
    assert out["emitted"] == "QUESTION_ANSWERED"
    [receipt] = rows(repo, "QUESTION_ANSWERED")
    assert receipt["Details"] == details
    # The hook kept the person's reply as Studio sent it, the Other marker beside their words.
    assert receipt["Person Reply"].startswith(
        "Q1: X. Other (please specify): Just me: a single person who keeps a personal task list and misses deadlines")

    paired = json.loads(bun(repo, ".kiro/tools/aidlc.ts", "engine", "log", "answers", "--stage", STAGE).stdout)
    assert [(a["question"], a["answer"]) for a in paired["answered"]] == [("Q1-Q5 intent capture questions", details)]
    parsed = reader.parse_questions_file(questions.read_text(), relpath, "sha")
    assert parsed.pending_count == 0
    assert parsed.questions[0].answer == "X. Just me: a single person who keeps a personal task list and misses deadlines"
