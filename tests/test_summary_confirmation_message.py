"""The summary a summary confirmation asks about, quoted on its card (enterprise E2E, 2026-11-03).

AI-DLC writes that summary only in the conversation: the questions file's ``Consolidated Summary Confirmation``
section holds the question and its two answers. The card said "intent-capture is waiting for you to confirm its
summary" and showed the recorded answers as letters, so the person was asked to confirm text the card did not show,
with nothing saying it was in the Conversation tab.
"""

from __future__ import annotations

import pytest

import fixtures as F
from test_e2e_gate import INTENT, QUESTIONS_REL, STAGE, _get, _queue, _record_dir, _rescan, _shard, scene  # noqa: F401
from test_handlers_auth import common, routes, sv  # noqa: F401
from test_summary_confirmation_switch import SUMMARY_QUESTIONS, _state, _write_state, repo_dir  # noqa: F401

def _get_activity(sv, routes, fake_host):
    status, body = _get(sv, routes, fake_host, "/activity", limit="200")
    return status, (body.get("events") or body.get("items") or [])


SUMMARY = (
    "**Stage:** Requirements Analysis\n\n- **Store:** SQLite, because the tool stays on one machine. (Q1)\n\n"
    "**Confirm**: Does this all look correct before I generate the artifact?\n\n"
    "[OPTIONS: Looks correct | Request changes]"
)


def _says(fake_host, scene, role: str, content: str, ts: str) -> None:
    fake_host.get_slot(scene.slot_key).messages.append(
        {"role": role, "content": content, "cls": "msg", "ts": ts, "meta": {}})


def _card(sv, routes, fake_host, scene, questions: str = SUMMARY_QUESTIONS) -> dict:
    path = _record_dir(scene.root) / QUESTIONS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(questions)
    _rescan(sv, routes, fake_host, scene)
    [card] = [c for c in _queue(sv, routes, fake_host) if c["type"] == "question"]
    return card


def test_the_card_quotes_the_summary_ai_dlc_wrote_in_the_conversation(sv, routes, fake_host, scene):
    _says(fake_host, scene, "user", "Q1: A", "2026-09-04T10:00:05Z")
    _says(fake_host, scene, "assistant", SUMMARY, "2026-09-04T10:00:25Z")
    card = _card(sv, routes, fake_host, scene)
    assert card["evidence"]["questions"]["pending_checkpoint"] == "summary_confirmation"
    quoted = card["evidence"]["conversation_reply"]
    assert quoted["text"].startswith("**Stage:** Requirements Analysis")
    assert "SQLite, because the tool stays on one machine" in quoted["text"]
    # The two answers are the card's own choice; the host's suggestion line is not part of the summary.
    assert "[OPTIONS:" not in quoted["text"]
    assert quoted["ts"] == "2026-09-04T10:00:25Z"


@pytest.mark.parametrize("case", ["running", "person_spoke_last", "nothing_said"])
def test_nothing_is_quoted_unless_ai_dlcs_reply_is_what_the_conversation_waits_on(
        sv, routes, fake_host, scene, case):
    if case != "nothing_said":
        _says(fake_host, scene, "assistant", SUMMARY, "2026-09-04T10:00:25Z")
    if case == "person_spoke_last":
        _says(fake_host, scene, "user", "Looks correct", "2026-09-04T10:00:40Z")
    card = _card(sv, routes, fake_host, scene)
    if case == "running":
        # The card was raised once the turn ended; a later turn running now leaves it open, quoting nothing.
        fake_host.get_slot(scene.slot_key).running = True
        _rescan(sv, routes, fake_host, scene)
        [card] = [c for c in _queue(sv, routes, fake_host) if c["action_id"] == card["action_id"]]
    assert card["evidence"].get("conversation_reply") is None


def test_open_questions_are_the_form_and_quote_nothing(sv, routes, fake_host, scene):
    _says(fake_host, scene, "assistant", "I wrote 1 question in the file.", "2026-09-04T10:00:25Z")
    open_question = SUMMARY_QUESTIONS.replace("[Answer]: A", "[Answer]:", 1)
    card = _card(sv, routes, fake_host, scene, open_question)
    assert card["evidence"]["questions"]["pending_count"] == 1
    assert card["evidence"].get("conversation_reply") is None


SWITCHED = (
    "AIDLC Guard Policy: Summary Confirmation changed: on (from scope enterprise) to off (set by you)\n\n"
    "Your Scope Definition answers stay as they are, and the confirm step is skipped. Next, I write the scope "
    "document and the backlog from your answers.\n\n[OPTIONS: Write the scope documents now | Change an answer first]"
)


def test_a_summary_asked_before_the_switch_is_owed_nothing_once_it_is_off(sv, routes, fake_host, scene):
    """Enterprise run, 2026-11-04: the person turned summary confirmation off from the open summary card. 2.11's
    `summaryConfirmationOwed` reads the setting as it is now, so AI-DLC skipped the step and asked in the conversation
    whether to write the files; the card still asked for `Looks correct` and hid that question."""
    _write_state(scene, _state("off (set by you)"))
    slot = fake_host.get_slot(scene.slot_key)
    slot.options = ["Write the scope documents now", "Change an answer first"]
    _says(fake_host, scene, "user", "/aidlc --summary-confirmation off", "2026-09-04T10:00:05Z")
    _says(fake_host, scene, "assistant", SWITCHED, "2026-09-04T10:00:25Z")
    path = _record_dir(scene.root) / QUESTIONS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SUMMARY_QUESTIONS)
    _rescan(sv, routes, fake_host, scene)
    cards = _queue(sv, routes, fake_host)
    assert [c["type"] for c in cards] == ["missing_input"], [(c["type"], c["headline"]) for c in cards]
    [reply] = cards
    assert reply["headline"]["params"]["options"] == ["Write the scope documents now", "Change an answer first"]
    assert "the confirm step is skipped" in reply["evidence"]["conversation_reply"]["text"]


def test_a_summary_asked_while_it_is_on_is_still_the_card(sv, routes, fake_host, scene):
    _says(fake_host, scene, "assistant", SUMMARY, "2026-09-04T10:00:25Z")
    card = _card(sv, routes, fake_host, scene)
    assert card["evidence"]["questions"]["pending_checkpoint"] == "summary_confirmation"
    assert [d["decision"] for d in card["decisions"]] == ["confirm_summary"]


SUMMARY_DECISION = "Does this all look correct before I generate the artifact?"


@pytest.mark.parametrize(("line", "raised"), [("on (from scope feature)", True), ("off (set by you)", False)])
def test_the_summary_decision_is_a_card_only_while_a_summary_is_owed(sv, routes, fake_host, scene, line, raised):
    """Enterprise run, 2026-11-04: once the setting was off the conductor removed the summary from the file, and the
    summary's own audit decision, never to be answered, came back as a card offering Looks correct mid-turn."""
    _write_state(scene, _state(line))
    shard = _shard(scene.root)
    shard.write_text(shard.read_text() + F.audit_text(F.audit_block(
        "DECISION_RECORDED", "2026-09-04T09:30:00Z", Stage=STAGE, Decision=SUMMARY_DECISION,
        Options="Looks correct,Request changes", Checkpoint="Consolidated Summary Confirmation",
        Questions_File=f"aidlc/spaces/default/intents/{INTENT}/{QUESTIONS_REL}")))
    _rescan(sv, routes, fake_host, scene)
    prompts = [q["prompt"] for c in _queue(sv, routes, fake_host) if c["type"] == "question"
               for q in c["evidence"]["questions"]["questions"]]
    assert (SUMMARY_DECISION in prompts) is raised, prompts


def test_a_turn_still_writing_its_questions_raises_no_card_until_it_ends(sv, routes, fake_host, scene):
    """Enterprise run, 2026-11-03: market-research's questions file was rewritten 20 seconds after the conductor first
    wrote it, in the same turn. Each version raised a card and a notification; the first was cancelled as it opened."""
    slot = fake_host.get_slot(scene.slot_key)
    slot.running = True
    path = _record_dir(scene.root) / QUESTIONS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SUMMARY_QUESTIONS.replace("[Answer]: A", "[Answer]:", 1))
    _rescan(sv, routes, fake_host, scene)
    assert [c for c in _queue(sv, routes, fake_host) if c["type"] == "question"] == []
    path.write_text(SUMMARY_QUESTIONS.replace("[Answer]: A", "[Answer]:", 1).replace("Which store?", "Which database?"))
    _rescan(sv, routes, fake_host, scene)
    assert [c for c in _queue(sv, routes, fake_host) if c["type"] == "question"] == []
    slot.running = False  # the conductor asks as it ends the turn
    _says(fake_host, scene, "assistant", "I wrote 1 question in the file.", "2026-09-04T10:00:25Z")
    _rescan(sv, routes, fake_host, scene)
    [card] = [c for c in _queue(sv, routes, fake_host) if c["type"] == "question"]
    assert card["evidence"]["questions"]["questions"][0]["prompt"] == "Which database?"
    status, body = _get_activity(sv, routes, fake_host)
    created = [e for e in body if e.get("kind") == "action.created" and (e.get("params") or {}).get("type") == "question"]
    assert len(created) == 1, created
