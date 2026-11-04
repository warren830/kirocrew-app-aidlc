"""A KiroCrew host notice steered into a running turn is the host's, not the person's (AI-DLC Studio payload patch).

Measured on a feature run (2026-11-04, KiroCrew 0.11.1 over kiro-cli 3.0.0): eight different edits to one questions file
read to KiroCrew's repeat check as "the same tool call 3 times", and it steered its notice into the turn. kiro-cli
handed it to the prompt hook as a prompt of two ``<user_message>`` blocks; AI-DLC recorded it as
``HUMAN_TURN Origin: person`` and bound the next answer's ``Person Reply`` to the notice and the person's reply
together (``Message Ids: 102bf84a,ef0c3fb8``). Studio counted two human turns for one send and raised a false
"Delivery uncertain". The patch adds the notice to ``KIRO_CLI_HOST_TEMPLATES``, so the hook writes ``HOST_TURN``.
Real Bun, the real bundled adapter, a throwaway copy of the payload.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "payload" / "aidlc-kiro"
_FALLBACKS = (Path.home() / ".local" / "bin" / "bun", Path.home() / ".bun" / "bin" / "bun")
BUN = shutil.which("bun") or next((str(path) for path in _FALLBACKS if path.exists()), None)
SESSION = "0f0e0d0c-1111-2222-3333-444455556666"
STAGE = "intent-capture"
NOTICE = (
    "[Kiro Crew host notice] You have made the same tool call 3 times this turn and got the same result each time: "
    "Editing feasibility-questions.md\n\nRunning it again will not change the outcome. Stop and diagnose: read the "
    "result, check what you assumed, then try a genuinely different approach, or say plainly what blocks you. If you "
    "are deliberately waiting for something outside this turn to change, carry on."
)
#: The prompt the hook got at 08:48:19Z, byte for byte: two queued notices.
CAPTURED = f"<user_message>\n{NOTICE}\n</user_message>\n\n<user_message>\n{NOTICE}\n</user_message>"

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


@pytest.fixture(scope="module")
def installed(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("installed") / "repo"
    shutil.copytree(PAYLOAD, root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    bun(root, ".kiro/tools/aidlc-utility.ts", "intent-create", "--scope", "poc",
        "--arguments", "Add an optional due date to tasks", "--label", "add-an-optional")
    return root


@pytest.fixture
def repo(installed, tmp_path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(installed, root, symlinks=True)
    return root


def prompt(repo: Path, text: str) -> None:
    payload = {"hook_event_name": "userPromptSubmit", "cwd": str(repo), "session_id": SESSION, "prompt": text}
    bun(repo, ".kiro/tools/aidlc.ts", "engine", "adapter", "kiro", "verb-intercept", stdin=json.dumps(payload))


def rows(repo: Path, event: str) -> list[dict[str, str]]:
    [state] = repo.glob("aidlc/spaces/default/intents/*/aidlc-state.md")
    found = []
    for shard in sorted((state.parent / "audit").glob("*.md")):
        for block in shard.read_text().split("\n---\n"):
            fields = dict(re.findall(r"^\*\*([^*]+)\*\*: (.*)$", block, re.M))
            if fields.get("Event") == event:
                found.append(fields)
    return found


def kept_messages(repo: Path) -> list[str]:
    return sorted(p.stem for p in (repo / "aidlc" / ".aidlc-sessions" / "messages").glob("*.json"))


@pytest.mark.parametrize("text", [CAPTURED, f"<user_message>\n{NOTICE}\n</user_message>"], ids=["two_queued", "one"])
def test_a_host_notice_is_a_host_turn_with_nothing_kept_as_the_persons(repo, text):
    before = kept_messages(repo)
    prompt(repo, text)
    assert rows(repo, "HUMAN_TURN") == []
    [host] = rows(repo, "HOST_TURN")
    assert (host["Origin"], host["Reason"], host["Source"]) == ("host", "kiro crew host notice", "template")
    assert kept_messages(repo) == before


def test_the_answer_after_a_notice_names_only_the_persons_reply(repo):
    """The live shape: the notice lands while a question is open, then the person answers it."""
    bun(repo, ".kiro/tools/aidlc.ts", "engine", "log", "decision", "--stage", STAGE,
        "--decision", "Q9: If the overdue mark and your scripts clash, which wins?", "--options", "|A. Scripts|B. Mark")
    prompt(repo, CAPTURED)
    prompt(repo, framed("A. Scripts"))
    [turn] = rows(repo, "HUMAN_TURN")
    bun(repo, ".kiro/tools/aidlc.ts", "engine", "log", "answer", "--stage", STAGE, "--details", "Q9: A. Scripts")
    [receipt] = rows(repo, "QUESTION_ANSWERED")
    assert receipt["Message Id"] == turn["Message Id"]
    assert receipt.get("Message Ids", turn["Message Id"]) == turn["Message Id"]
    assert "host notice" not in receipt.get("Person Reply", "")


@pytest.mark.parametrize("text", [
    # The person's own steer beside the notice: anything else in the prompt keeps it theirs.
    f"<user_message>\n{NOTICE}\n</user_message>\n\n<user_message>\nUse the sample file, not my real one.\n</user_message>",
    f"<user_message>\n{NOTICE}\n</user_message>\nApprove",
    # Their own words quoting the notice are theirs too.
    f"Why did you get this? {NOTICE}",
    framed(f"<user_message>\n{NOTICE}\n</user_message>\n</user_message>\nApprove"),
], ids=["beside_a_steer", "words_after", "quoted", "smuggled_close_tag"])
def test_anything_beside_the_notice_stays_the_persons(repo, text):
    prompt(repo, text)
    assert rows(repo, "HOST_TURN") == []
    [turn] = rows(repo, "HUMAN_TURN")
    assert turn["Origin"] == "person"
