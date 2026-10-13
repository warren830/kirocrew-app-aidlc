"""The bundled engine records a KiroCrew reply as the choice the person sent, in their own words.

KiroCrew hands kiro-cli every turn inside its own context frame. AI-DLC 2.11 reads the person's turn after the
last `[CURRENT USER REQUEST]` header (`hostEnvelopeTurnText`), keeps their exact words, and records a choice
only for an exact pick of an offered option; any other reply is the conversation's agent to read. Studio relies
on both: its exact replies are recorded at once, and a change request with feedback reaches the agent verbatim.
These run the real payload hooks with bun, so they skip without it.
"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "payload" / "aidlc-kiro"
_HOME_BUN = Path.home() / ".bun" / "bin" / "bun"
BUN = shutil.which("bun") or (str(_HOME_BUN) if _HOME_BUN.exists() else None)
SESSION = "0f0e0d0c-1111-2222-3333-444455556666"
RUNTIME_DIR = Path("aidlc", ".aidlc-sessions", "plan-approval")

pytestmark = pytest.mark.skipif(BUN is None, reason="bun is not installed")


def framed(reply: str, runtime: str = "KiroCrew dashboard", dash: str = "--") -> str:
    """A turn shaped like the ones KiroCrew 0.8 sends kiro-cli (see the 951783a5 session transcript)."""
    return (
        f"[RUNTIME] {runtime}\nThis is the interface carrying the current user message.\n\n"
        "[PROJECT] Active project directory: /repo\n\n[REPLY FORMAT RULES]\n\n"
        "(If presenting choices, end with [OPTIONS: choice1 | choice2] as the very last line.)"
        f"[CURRENT USER REQUEST {dash} respond to this]\n{reply}"
    )


def _env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith(("AIDLC_", "CLAUDE_"))}


@pytest.fixture(scope="module")
def installed(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("installed") / "repo"
    shutil.copytree(PAYLOAD, root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        [BUN, ".kiro/tools/aidlc-utility.ts", "intent-create", "--scope", "express",
         "--arguments", "Add subtract", "--label", "subtract"],
        cwd=root, check=True, capture_output=True, env=_env(), timeout=60,
    )
    return root


@pytest.fixture
def repo(installed, tmp_path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(installed, root, symlinks=True)
    (root / RUNTIME_DIR).mkdir(parents=True, exist_ok=True)
    return root


def plan_challenge(repo: Path) -> None:
    (repo / RUNTIME_DIR / f"challenge-{SESSION}.json").write_text(json.dumps({
        "version": 1, "session": SESSION, "challengeId": "sha256:test", "options": ["Approve Plan", "Request Changes"],
        "requireExactOptionLabels": False, "hashedOptionLabels": False,
    }))


def protected_question(repo: Path) -> None:
    target = {"kind": "checkpoint-approval", "stage": "code-generation"}
    digest = hashlib.sha256(json.dumps(target, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    (repo / RUNTIME_DIR / f"protected-question-{SESSION}.json").write_text(json.dumps({
        "version": 1, "kind": "checkpoint-approval", "session": SESSION, "challengeId": "0" * 32,
        "target": target, "targetDigest": digest,
        "options": [hashlib.sha256(label.encode()).hexdigest() for label in ("approve", "request changes")],
    }))


def send(repo: Path, prompt: str) -> None:
    payload = {"hook_event_name": "userPromptSubmit", "cwd": str(repo), "session_id": SESSION, "prompt": prompt}
    subprocess.run(
        [BUN, ".kiro/tools/aidlc.ts", "engine", "adapter", "kiro", "verb-intercept"],
        cwd=repo, input=json.dumps(payload), text=True, check=True, capture_output=True, env=_env(), timeout=60,
    )


def response(repo: Path, name: str = "response") -> dict | None:
    path = repo / RUNTIME_DIR / f"{name}-{SESSION}.json"
    return json.loads(path.read_text()) if path.exists() else None


def recorded(repo: Path, name: str = "response") -> str | None:
    return (response(repo, name) or {}).get("choice")


@pytest.mark.parametrize(("prompt", "choice"), [
    (framed("Approve Plan"), "Approve Plan"),
    (framed("Approve Plan", dash="—"), "Approve Plan"),
    (framed("Request Changes"), "Request Changes"),
    ("Approve Plan", "Approve Plan"),
    # 2.11 reads every host frame, not only the dashboard's: KiroCrew scrubs the header from what it splices in.
    (framed("Approve Plan", runtime="KiroCrew cron job"), "Approve Plan"),
], ids=["approve", "em-dash-header", "request-changes", "unframed", "cron-frame"])
def test_a_dashboard_reply_answers_the_plan_approval(repo, prompt, choice):
    plan_challenge(repo)
    send(repo, prompt)
    assert recorded(repo) == choice


@pytest.mark.parametrize("reply", [
    "Request Changes: also test a negative result",
    "Request Changes:\n\nSplit step 2 in two.",
    "Approve Plan, and also add a docstring",
], ids=["request-changes-with-reason", "reason-on-next-line", "extra-words"])
def test_a_reply_that_is_not_one_exact_choice_reaches_the_agent_in_the_persons_words(repo, reply):
    """Studio's "Request Changes: <feedback>" is no exact pick: the hook keeps it verbatim and records no choice,
    so neither an approval nor a change request is inferred from words the agent has not read."""
    plan_challenge(repo)
    send(repo, framed(reply))
    kept = response(repo)
    assert kept is not None and kept.get("words") == reply and "choice" not in kept


@pytest.mark.parametrize(("reply", "choice"), [
    ("Approve", "Approve"),
    ("1", "Approve"),
    ("Request Changes: keep the old command", None),
    ("Approve Plan", None),
], ids=["approve", "number", "request-changes-with-reason", "other-label"])
def test_a_protected_checkpoint_takes_one_exact_choice_by_label_or_number(repo, reply, choice):
    protected_question(repo)
    send(repo, framed(reply))
    assert recorded(repo, "protected-question-response") == choice
