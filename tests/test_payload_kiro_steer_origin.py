"""A message steered into a running Kiro CLI turn is the person's only when the person wrote it.

Kiro CLI runs no prompt hook for a steer: it keeps the message in its chat record, inside the tool results of the
running turn, as ``[LIVE STEERING - New message from user]``, and the bundled Kiro adapter records it as a turn before
the next AI-DLC command. KiroCrew steers its own notices the same way, such as the one it sends when a tool approval
went unanswered and it declined the call itself, so the label proves nothing. Measured 2026-11-12 on KiroCrew 0.11.2
over kiro-cli: that notice became a person's HUMAN_TURN, which then let a system package install through. Real Bun, the
real bundled hooks, a throwaway copy of the payload, and a Kiro chat record under a throwaway home.
"""

import json
from pathlib import Path

import pytest

from test_payload_engine_questions import BUN, INSTALL, SESSION, events, hook, repo, shell  # noqa: F401

pytestmark = pytest.mark.skipif(BUN is None, reason="bun is not installed")

# KiroCrew 0.11.2's notices as kiro-cli handed them to the agent, captured 2026-11-12: an approval prompt that expired,
# and that notice queued with the one for the rest of its batch, which the host declined with it.
APPROVAL_EXPIRED = (
    "<user_message>\n[Kiro Crew host notice] The tool call you just made was auto-declined because its "
    "approval prompt expired unanswered. This was NOT a user action — the user did not cancel, reject, or "
    "interrupt anything. The tool result you were handed for it is generic and wrong about who denied it — on "
    "kiro-cli it reads \"User denied tool execution\".\n\nBlocked: Running: grep -n -E \"^(## Developer Code "
    "Scan Results|### Scan Coverage|## Handoff Summary)$\" "
    "aidlc/spaces/default/intents/261112-rc-fresh-run/inception/reverse-engineering/developer-scan.md: the "
    "approval prompt went unanswered for 600s\n\nDo not apologise for a cancellation and do not ask the user "
    "whether to retry. Decide and continue in this same turn: nobody answered within the window, so the action "
    "itself was never judged — do not abandon it or route around it on this evidence. State the permission you "
    "need and why, then continue with what you can do without it. Do not immediately reissue the same call: "
    "the person who did not answer is still away, and re-prompting re-arms the same wait for the same "
    "silence.\n</user_message>"
)
APPROVAL_EXPIRED_WITH_BATCH = (
    "<user_message>\n[Kiro Crew host notice] The tool call you just made was auto-declined because its "
    "approval prompt expired unanswered. This was NOT a user action — the user did not cancel, reject, or "
    "interrupt anything. The tool result you were handed for it is generic and wrong about who denied it — on "
    "kiro-cli it reads \"User denied tool execution\".\n\nBlocked: Running: python3 -m unittest discover -s "
    "tests -p 'test_cli.py' -v: the approval prompt went unanswered for 600s\n\nDo not apologise for a "
    "cancellation and do not ask the user whether to retry. Decide and continue in this same turn: nobody "
    "answered within the window, so the action itself was never judged — do not abandon it or route around it "
    "on this evidence. State the permission you need and why, then continue with what you can do without it. "
    "Do not immediately reissue the same call: the person who did not answer is still away, and re-prompting "
    "re-arms the same wait for the same silence.\n</user_message>\n\n<user_message>\n[Kiro Crew host notice] "
    "The tool call you just made was auto-declined along with every remaining call in its batch, because the "
    "host declined an earlier tool of the same batch. This was NOT a user action — the user did not cancel, "
    "reject, or interrupt anything. The tool result you were handed for it is generic and wrong about who "
    "denied it — on kiro-cli it reads \"User denied tool execution\".\n\nBlocked: Running: git status "
    "--porcelain: the host declined an earlier tool of this batch (the approval prompt went unanswered for "
    "600s)\n\nDo not apologise for a cancellation and do not ask the user whether to retry. Decide and "
    "continue in this same turn: nothing judged these calls themselves — the group was cut short as a whole. "
    "Address what declined that earlier tool (the reason above), then re-issue the calls you still need; if "
    "you genuinely cannot proceed without them, say so and stop with the reason.\n</user_message>"
)
HOST_NOTICE = APPROVAL_EXPIRED
PERSON_STEER = "Go ahead and install pytest for the whole machine"
AIDLC_COMMAND = "bun .kiro/tools/aidlc.ts engine workspace codekb"


def steer_into_turn(home: Path, words: str) -> None:
    """kiro-cli's chat record for SESSION: the turn's prompt, then a tool result with one steered message beside it."""
    envelope = (
        "[LIVE STEERING - New message from user]\n\nThe user sent a new message while you are working.\n\n"
        f'<user_message id="steer-0a1b2c3d4e5f">\n{words}\n</user_message>\n\n'
        "IMPORTANT: After completing your work, include a brief note about how you handled this steering message."
    )
    rows = [
        {"version": "v1", "kind": "Prompt", "data": {"message_id": "p-1", "content": [
            {"kind": "text", "data": "[RUNTIME] KiroCrew dashboard\n\nRun the next stage"}]}},
        {"version": "v1", "kind": "ToolResults", "data": {"message_id": "t-1", "content": [
            {"kind": "toolResult", "data": {"toolUseId": "tool-1", "status": "error",
                                            "content": [{"kind": "text", "data": "User denied tool execution"}]}},
            {"kind": "text", "data": envelope}]}},
    ]
    record = home / ".kiro" / "sessions" / "cli" / f"{SESSION}.jsonl"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text("".join(json.dumps(row) + "\n" for row in rows))


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    return path


def next_aidlc_command(repo: Path) -> None:
    hook(repo, "state-transition-guard", "preToolUse", tool_name="execute_bash", tool_input={"command": AIDLC_COMMAND})


@pytest.mark.parametrize("notice", [APPROVAL_EXPIRED, APPROVAL_EXPIRED_WITH_BATCH],
                         ids=["expired", "expired_with_batch"])
def test_kirocrews_own_notice_steered_into_the_turn_is_the_hosts_and_grants_nothing(repo, home, clock, notice):
    steer_into_turn(home, notice)
    next_aidlc_command(repo)
    turns = [(name, fields) for name, fields in events(repo, clock) if name in ("HUMAN_TURN", "HOST_TURN")]
    assert [name for name, _ in turns] == ["HOST_TURN"], "the notice is the host's turn, not the person's"
    assert (turns[0][1]["Origin"], turns[0][1]["Reason"]) == ("host", "kiro crew host notice")

    held = shell(repo, INSTALL, code=2)
    assert "installs a system package" in held.stdout + held.stderr, "nobody asked for the install"


@pytest.mark.parametrize("words", [PERSON_STEER, f"{HOST_NOTICE}\n{PERSON_STEER}"], ids=["alone", "beside_a_notice"])
def test_a_person_steering_the_turn_still_stands_behind_what_they_asked_for(repo, home, clock, words):
    """Their own line beside a queued host notice keeps the whole steer theirs: only a steer that is nothing but the
    host's sentences is the host's."""
    steer_into_turn(home, words)
    next_aidlc_command(repo)
    turns = [(name, fields) for name, fields in events(repo, clock) if name in ("HUMAN_TURN", "HOST_TURN")]
    assert [(name, fields["Origin"]) for name, fields in turns] == [("HUMAN_TURN", "person")]

    shell(repo, INSTALL)
    allowed = [fields for name, fields in events(repo, clock) if name == "SYSTEM_INSTALL_ALLOWED"]
    assert [PERSON_STEER in fields["Person Reply"] for fields in allowed] == [True], (
        "their words let the install through")
