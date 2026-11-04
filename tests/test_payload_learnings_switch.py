"""The bundled engine turns the learnings ritual off when Studio sends its switch as the person's turn.

Studio's learnings control sends ``WIRE_LEARNINGS_OFF`` (``/aidlc --learnings off``) into the intent's conversation,
inside KiroCrew's prompt envelope. These run the real payload with bun, the way kiro-cli runs its userPromptSubmit hook
(``engine adapter kiro verb-intercept``), then the conductor's own calls, on a throwaway feature-scope intent:

* the switch alone is recorded as a command, so it answers no open question, and is forwarded verbatim to the
  conductor's ``next``. The hook does not apply it (2.11.2 applies no setting at prompt time); the conductor's
  ``config set`` writes ``Learnings: off (set by you)`` (2.11.0 labelled it ``set by a command``);
* the stage the conductor runs next carries the ritual off and no longer loads its protocol module;
* the same switch beside other words (a question's reply) is a flag of new work: why Studio never bundles it;
* with a learnings question already asked, the switch leaves it open, and the person's later reply is still recorded;
* ``on`` takes the same road back.
"""

import json
import re
import subprocess

import pytest

import conftest as CT
from test_payload_summary_switch import (  # noqa: F401 - fixtures
    BUN, STAGE, _env, bun, directive, installed, record, repo, rows, send,
)

C = CT.studio_module("constants")
OFF, ON = C.WIRE_LEARNINGS_OFF, C.WIRE_LEARNINGS_ON
ADD_PROMPT = C.LEARNINGS_ADD_PROMPTS[0]

pytestmark = pytest.mark.skipif(BUN is None, reason="bun is not installed")


def line(repo) -> str | None:
    match = re.search(r"^- \*\*Learnings\*\*: (.*)$", (record(repo) / "aidlc-state.md").read_text(), re.M)
    return match.group(1) if match else None


def config_set(repo, value: str) -> str:
    """The setter the conductor's ``next`` tells it to run, as it runs it."""
    return bun(repo, ".kiro/tools/aidlc.ts", "engine", "config", "set", "learnings", value).stdout


def conductor_applies(repo, value: str) -> str:
    """What the conductor does with the forwarded switch: ``next --learnings <value>``, then the setter it prints."""
    printed = directive(repo, "--learnings", value)
    assert printed["kind"] == "print" and f"engine config set learnings {value}" in printed["message"], printed
    return config_set(repo, value)


def test_the_switch_alone_is_a_command_the_hook_forwards(repo):
    assert line(repo) == "on (from scope feature)"
    context = send(repo, OFF)
    # One human turn, recorded as a command: no question pairs with it.
    assert rows(repo, "HUMAN_TURN")[-1]["Reply"] == "command"
    # The hook leaves the line to the conductor, and tells it exactly what to run.
    assert line(repo) == "on (from scope feature)" and rows(repo, "CEREMONY_SET") == []
    assert "engine orchestrate next --learnings off" in context


def test_the_conductor_sets_it_off_as_a_command(repo):
    send(repo, OFF)
    said = conductor_applies(repo, "off")
    assert said.strip() == "Learnings changed: on (from scope feature) to off (set by you)"
    assert line(repo) == "off (set by you)"
    [ceremony] = rows(repo, "CEREMONY_SET")
    assert (ceremony["Key"], ceremony["Old"], ceremony["New"], ceremony["Source"]) == (
        "learnings", "on", "off", "you")
    # The stage it runs next has no learnings ritual: the module that asks the two questions is not loaded.
    stage = directive(repo)
    assert (stage["kind"], stage["stage"], stage["ceremony"]["learnings"]) == ("run-stage", STAGE, "off")
    assert "learnings" not in stage["protocol_modules"]
    # Asked again, AI-DLC only says it is so: why Studio refuses a request for what the line already says.
    assert "Learnings is already off (set by you)" in config_set(repo, "off")


@pytest.mark.parametrize("turn", [f"{OFF} Nothing to add", f"{OFF}\nNothing to add"], ids=["same-line", "next-line"])
def test_the_switch_beside_a_reply_is_new_work(repo, turn):
    """Words after the flags describe new work, so a bundled reply would neither switch nor answer."""
    context = send(repo, turn)
    assert "engine orchestrate next --learnings off Nothing to add" in context
    routed = directive(repo, "--learnings", "off", "Nothing", "to", "add")
    assert (routed["kind"], routed.get("ask_type"), routed.get("new_work_description")) == (
        "ask", "new-work-routing", "Nothing to add")
    assert line(repo) == "on (from scope feature)" and rows(repo, "CEREMONY_SET") == []


def test_an_open_learnings_question_stays_open_and_is_still_answered(repo):
    bun(repo, ".kiro/tools/aidlc.ts", "engine", "log", "decision", "--stage", STAGE, "--decision", ADD_PROMPT,
        "--options", "Nothing to add,Add a note")
    send(repo, OFF)
    conductor_applies(repo, "off")
    assert line(repo) == "off (set by you)"
    receipt = ("engine", "log", "answer", "--stage", STAGE, "--details", "Nothing to add")
    # The switch was no reply to it: a receipt now is refused, as for any turn that answered nothing.
    refused = subprocess.run([BUN, ".kiro/tools/aidlc.ts", *receipt], cwd=repo, text=True, capture_output=True,
                             env=_env(), timeout=120)
    assert refused.returncode != 0 and "no new human reply has arrived" in refused.stdout + refused.stderr
    assert rows(repo, "QUESTION_ANSWERED") == []
    # The question already asked still takes the person's own reply.
    send(repo, "Nothing to add")
    recorded = json.loads(bun(repo, ".kiro/tools/aidlc.ts", *receipt).stdout)
    assert recorded["emitted"] == "QUESTION_ANSWERED"
    assert rows(repo, "QUESTION_ANSWERED")[-1]["Details"] == "Nothing to add"


def test_back_on_is_the_conductors_setter_too(repo):
    send(repo, OFF)
    conductor_applies(repo, "off")
    context = send(repo, ON)
    assert line(repo) == "off (set by you)"
    assert "engine orchestrate next --learnings on" in context
    assert conductor_applies(repo, "on").strip() == (
        "Learnings changed: off (set by you) to on (set by you)")
    assert line(repo) == "on (set by you)"
