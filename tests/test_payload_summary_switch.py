"""The bundled engine turns summary confirmation off for the person when Studio sends its switch as their turn.

Studio's summary-confirmation control sends ``WIRE_SUMMARY_CONFIRMATION_OFF`` (``/aidlc --summary-confirmation off``)
into the intent's conversation, inside KiroCrew's prompt envelope. These run the real payload with bun, the way kiro-cli
runs its userPromptSubmit hook (``engine adapter kiro verb-intercept``), on a throwaway feature-scope intent:

* the switch alone is applied at prompt time as the person's own, ``Summary Confirmation: off (set by you)``, and the
  turn is recorded as a command, so it answers no open question;
* the same switch beside other words (a checkpoint's reply) is new-work text, not a switch: why Studio never bundles it;
* with a summary confirmation already asked, the switch leaves it open, the next stage directive carries the ceremony
  off, and the person's later ``Looks correct`` is still recorded;
* ``on`` is forwarded to the conductor's ``next``, whose setter writes it.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import conftest as CT

C = CT.studio_module("constants")
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


@pytest.fixture(scope="module")
def installed(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("summary-switch") / "repo"
    shutil.copytree(PAYLOAD, root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    bun(root, ".kiro/tools/aidlc-utility.ts", "intent-create", "--scope", "feature",
        "--arguments", "Add a subtract command", "--label", "subtract")
    return root


@pytest.fixture
def repo(installed, tmp_path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(installed, root, symlinks=True)
    return root


def record(repo: Path) -> Path:
    [found] = repo.glob("aidlc/spaces/default/intents/*/aidlc-state.md")
    return found.parent


def line(repo: Path) -> str | None:
    match = re.search(r"^- \*\*Summary Confirmation\*\*: (.*)$", (record(repo) / "aidlc-state.md").read_text(), re.M)
    return match.group(1) if match else None


def rows(repo: Path, event: str) -> list[dict[str, str]]:
    found = []
    for shard in sorted((record(repo) / "audit").glob("*.md")):
        for block in shard.read_text().split("\n---\n"):
            fields = dict(re.findall(r"^\*\*([^*]+)\*\*: (.*)$", block, re.M))
            if fields.get("Event") == event:
                found.append(fields)
    return found


def send(repo: Path, turn: str) -> str:
    payload = {"hook_event_name": "userPromptSubmit", "cwd": str(repo), "session_id": SESSION, "prompt": framed(turn)}
    return bun(repo, ".kiro/tools/aidlc.ts", "engine", "adapter", "kiro", "verb-intercept",
               stdin=json.dumps(payload)).stdout


def ask_summary(repo: Path) -> str:
    """The checkpoint as the conductor asks it: the file's blank confirmation tag and its recorded prompt."""
    questions = record(repo) / "ideation" / STAGE / f"{STAGE}-questions.md"
    questions.parent.mkdir(parents=True, exist_ok=True)
    questions.write_text(
        "# Intent Capture Questions\n\n## Question 1\nWhat is the main goal?\n\nA) Add subtraction\nB) Add division\n"
        "X) Other (please specify)\n\n[Answer]: A\n\n"
        f"## {C.SUMMARY_CONFIRMATION_HEADING}\n\n- Goal: add subtraction\n\n- Looks correct\n- Request changes\n\n"
        "[Answer]:\n")
    relpath = str(questions.relative_to(repo))
    bun(repo, ".kiro/tools/aidlc.ts", "engine", "log", "decision", "--stage", STAGE, "--checkpoint",
        "summary-confirmation", "--questions-file", relpath, "--decision",
        "Does this all look correct before I generate the artifact?", "--options", "Looks correct,Request changes")
    return relpath


def directive(repo: Path, *args: str) -> dict:
    """``next``, through its steering bundle, to the directive the conductor acts on."""
    answer = json.loads(bun(repo, ".kiro/tools/aidlc.ts", "engine", "orchestrate", "next", *args).stdout)
    while answer.get("kind") == "load-steering":
        answer = json.loads(bun(repo, ".kiro/tools/aidlc-orchestrate.ts", "continue", answer["receipt"]).stdout)
    return answer


def test_the_switch_alone_is_the_persons_and_answers_nothing(repo):
    assert line(repo) == "on (from scope feature)"
    context = send(repo, C.WIRE_SUMMARY_CONFIRMATION_OFF)
    assert line(repo) == "off (set by you)"
    [ceremony] = rows(repo, "CEREMONY_SET")
    assert (ceremony["Key"], ceremony["Old"], ceremony["New"], ceremony["Source"]) == (
        "summary_confirmation", "on", "off", "you")
    # One human turn, recorded as a command: no checkpoint pairs with it.
    assert rows(repo, "HUMAN_TURN")[-1]["Reply"] == "command"
    assert "the switch is already applied" in context
    # The conductor's own follow-up keeps the person's label.
    assert json.loads(bun(repo, ".kiro/tools/aidlc.ts", "engine", "orchestrate", "next", "--summary-confirmation",
                          "off").stdout)["kind"] == "print"
    assert "already off (set by you)" in bun(repo, ".kiro/tools/aidlc.ts", "engine", "config", "set",
                                             "summary-confirmation", "off").stdout
    assert line(repo) == "off (set by you)"


@pytest.mark.parametrize("turn", [
    f"{C.WIRE_SUMMARY_CONFIRMATION_OFF} {C.WIRE_LOOKS_CORRECT}",
    f"{C.WIRE_SUMMARY_CONFIRMATION_OFF}\n{C.WIRE_LOOKS_CORRECT}",
], ids=["same-line", "next-line"])
def test_the_switch_beside_a_reply_is_not_a_switch(repo, turn):
    """Words after the flags describe new work, so a bundled reply would neither switch nor answer."""
    send(repo, turn)
    assert line(repo) == "on (from scope feature)" and rows(repo, "CEREMONY_SET") == []


def test_an_open_confirmation_stays_open_and_is_still_answered(repo):
    relpath = ask_summary(repo)
    send(repo, C.WIRE_SUMMARY_CONFIRMATION_OFF)
    assert line(repo) == "off (set by you)"
    assert rows(repo, "SUMMARY_CONFIRMATION_RECORDED") == []
    assert (repo / relpath).read_text().rstrip().endswith("[Answer]:")
    # The stage goes on with the ceremony off: nothing more is owed before it writes its files.
    stage = directive(repo)
    assert (stage["kind"], stage["stage"], stage["ceremony"]["summary_confirmation"]) == ("run-stage", STAGE, "off")
    questions = repo / relpath
    questions.write_text(questions.read_text().replace("[Answer]:\n", f"[Answer]: {C.WIRE_LOOKS_CORRECT}\n"))
    receipt = ("engine", "log", "answer", "--stage", STAGE, "--checkpoint", "summary-confirmation",
               "--questions-file", relpath, "--details", C.WIRE_LOOKS_CORRECT)
    # The switch was no reply to it: a receipt now is refused, as for any turn that answered nothing.
    refused = subprocess.run([BUN, ".kiro/tools/aidlc.ts", *receipt], cwd=repo, text=True, capture_output=True,
                             env=_env(), timeout=120)
    assert refused.returncode != 0 and "no human reply has arrived" in refused.stdout + refused.stderr
    # The confirmation already asked still takes the person's own reply.
    send(repo, C.WIRE_LOOKS_CORRECT)
    recorded = json.loads(bun(repo, ".kiro/tools/aidlc.ts", *receipt).stdout)
    assert (recorded["emitted"], recorded["choice"]) == ("SUMMARY_CONFIRMATION_RECORDED", C.WIRE_LOOKS_CORRECT)


def test_back_on_is_the_conductors_setter(repo):
    send(repo, C.WIRE_SUMMARY_CONFIRMATION_OFF)
    context = send(repo, C.WIRE_SUMMARY_CONFIRMATION_ON)
    # Turning a check on needs nobody's word: the hook leaves it to the conductor's `next`.
    assert line(repo) == "off (set by you)"
    assert "engine orchestrate next --summary-confirmation on" in context
    printed = directive(repo, "--summary-confirmation", "on")
    assert "config set summary-confirmation on" in printed["message"]
    bun(repo, ".kiro/tools/aidlc.ts", "engine", "config", "set", "summary-confirmation", "on")
    assert line(repo) == "on (set by a command)"
