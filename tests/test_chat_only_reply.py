"""A delivered answer AI-DLC did not record while it asked in chat instead, on the real run of 2026-11-02.

The conductor logged a decision after the person's grouped answers had already arrived ("Q1-Q6 intent capture
questions", Options "Q1,…,Q6"); Studio raised it, a driver chose "Q1", and AI-DLC replied only in the conversation:
"Your reply came through as just "Q1"… Please send all six answers again". No receipt, no reply suggestions. The card
sat at Delivered with nothing for the person to act on.
"""

from __future__ import annotations

import asyncio
import copy
import os
from types import SimpleNamespace

import conftest as CT
import fixtures as F
import pytest

from test_e2e_gate import _post, _queue, _report, _rescan, _row, _submit
from test_handlers_auth import common, routes, sv  # noqa: F401 - fixtures

INTENT = F.CHAT_ONLY_REPLY_INTENT
ROWS = F.chat_only_reply_rows()
#: The transcript as the card was raised (it ends on AI-DLC's 07:08:43 reply), Studio's "Q1", and AI-DLC's answer.
BEFORE, DELIVERY, REPLY = ROWS[:-2], ROWS[-2], ROWS[-1]
PROMPT = "Q1-Q6 intent capture questions"


@pytest.fixture
def clock():
    return CT.FakeClock("2026-11-02T07:08:51Z")  # when the live card was raised


@pytest.fixture
def repo_dir(repo_builder):
    root = repo_builder.with_engine("payload").with_workspace().with_git().build()
    return F.chat_only_reply_tree(root, replied=False)


@pytest.fixture
def scene(sv, routes, fake_host, repo_dir):
    status, body = _post(sv, routes, fake_host, "/repos", {"path": str(repo_dir), "label": "v2-e2e"})
    assert status == 201, body
    repo_id = body["repo"]["repo_id"]
    slot_key = f"aidlc-studio-{repo_id}-{INTENT}"
    slot = fake_host.add_slot(slot_key, project=str(repo_dir), agent="aidlc", app="")
    slot.messages = copy.deepcopy(BEFORE)
    sv.host.attach(fake_host)
    status, bound = _post(sv, routes, fake_host, f"/repos/{repo_id}/intents/{INTENT}/session", {"slot_key": slot_key})
    assert status == 200, bound
    return SimpleNamespace(repo_id=repo_id, slot_key=slot_key, slot=slot, root=repo_dir)


def cards(sv, routes, fake_host, scene) -> list[dict]:
    _rescan(sv, routes, fake_host, scene)
    return _queue(sv, routes, fake_host)


def deliver_q1(sv, routes, fake_host, scene, clock) -> dict:
    """Choose A on the raised card and let the host take it, as the live driver did at 07:09:17."""
    [card] = [c for c in cards(sv, routes, fake_host, scene) if c["type"] == "question"]
    assert card["evidence"]["questions"]["questions"][0]["prompt"] == PROMPT
    clock.advance(26)
    status, receipt = _submit(sv, routes, fake_host, card, wire_text=DELIVERY["content"],
                              payload={"decision": "answers", "answers": [{"index": 1, "option_letters": ["A"]}]})
    assert status == 200, receipt
    row = copy.deepcopy(DELIVERY)
    row["meta"].update(studio_action_id=card["action_id"], studio_delivery_id=receipt["delivery_id"])
    scene.slot.messages.append(row)
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    return card


def ai_dlc_replies_in_chat(scene, clock, reply=REPLY) -> None:
    """The prompt hook records the reply; the conductor records nothing for it and answers in the conversation."""
    F.chat_only_reply_tree(scene.root, replied=True)
    marker = F.chat_only_reply_record(scene.root) / ".aidlc-engine" / "human-turn"
    later = marker.stat().st_mtime_ns + 1_000_000_000
    os.utime(marker, ns=(later, later))
    scene.slot.messages.append(copy.deepcopy(reply))
    clock.advance(13)


def reconcile(sv, card, times=3) -> dict:
    for _ in range(times):
        asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    return _row(sv, card["action_id"])


def test_the_delivered_answer_settles_and_the_conversations_question_is_the_next_card(
        sv, routes, fake_host, scene, clock):
    card = deliver_q1(sv, routes, fake_host, scene, clock)
    ai_dlc_replies_in_chat(scene, clock)
    assert scene.slot.options == []  # a plain question: KiroCrew parsed no reply suggestions

    row = reconcile(sv, card)
    assert row["status"] == "ResolvedNoTransition", row["resolution_json"]
    resolution = row["resolution_json"]
    assert resolution["reason"] == "answer_not_recorded"
    assert resolution["evidence"]["answer_verified"] is False
    assert resolution["evidence"]["conversation_asks"] is True

    # What AI-DLC waits on now is its question in the conversation, not the decision "Q1" was already sent to.
    [reply_card] = cards(sv, routes, fake_host, scene)
    assert reply_card["type"] == "missing_input", reply_card["type"]
    assert reply_card["headline"]["params"] == {"reason": "conversation_reply", "kind": "free_text", "options": []}
    quoted = reply_card["evidence"]["conversation_reply"]
    assert quoted["text"].startswith('Your reply came through as just "Q1".')
    assert "Please send all six answers again" in quoted["text"]


def test_a_turn_still_running_keeps_the_lease(sv, routes, fake_host, scene, clock):
    card = deliver_q1(sv, routes, fake_host, scene, clock)
    ai_dlc_replies_in_chat(scene, clock)
    fake_host.set_running(scene.slot_key, True)
    assert reconcile(sv, card)["status"] == "Delivered"


@pytest.mark.parametrize("ending", ["tool_row", "stopped_on_error", "foreign_receipt"])
def test_a_turn_that_did_not_end_on_ai_dlcs_reply_alone_keeps_waiting(sv, routes, fake_host, scene, clock, ending):
    """Nothing AI-DLC said follows the delivery, the turn stopped on an error after it, or a receipt Studio cannot
    match to this question may be its record: nothing shows the answer went unrecorded, so the deadline decides."""
    card = deliver_q1(sv, routes, fake_host, scene, clock)
    if ending == "tool_row":
        ai_dlc_replies_in_chat(scene, clock, reply={**REPLY, "role": "tool", "content": "🔧 bun .kiro/tools/aidlc.ts"})
    else:
        ai_dlc_replies_in_chat(scene, clock)
    if ending == "stopped_on_error":
        scene.slot.messages.append({"role": "error", "content": "⚠ The model stopped responding.",
                                    "ts": "2026-11-02T07:09:30.100000+00:00", "meta": {}})
    if ending == "foreign_receipt":
        shard = F.chat_only_reply_record(scene.root) / "audit" / F.CHAT_ONLY_REPLY_SHARD
        shard.write_text(shard.read_text() + F.audit_text(F.audit_block(
            "QUESTION_ANSWERED", "2026-11-02T07:09:25Z", Stage="intent-capture", Unit="another-unit", Details="Q1")))
    assert reconcile(sv, card)["status"] == "Delivered"


def test_a_turn_that_ran_and_stopped_inside_the_dispatchs_own_second_still_ran(sv, routes, fake_host, scene, clock):
    """KiroCrew stamps rows with microseconds and an offset, Studio its dispatch with ``Z``, and as strings ``.`` sorts
    before ``Z``: a turn whose every row fell in the second the decision went out read as a slot that never ran, one
    of the four proofs that nothing was delivered."""
    card = deliver_q1(sv, routes, fake_host, scene, clock)
    fake_host.set_running(scene.slot_key, True)  # its newest row is the delivery's, at 07:09:17.942867+00:00
    assert reconcile(sv, card, times=1)["status"] == "Delivered"
    fake_host.set_running(scene.slot_key, False)
    scene.slot.messages.append({"role": "error", "content": "⚠ The model stopped responding.",
                                "ts": "2026-11-02T07:09:17.990000+00:00", "meta": {}})
    clock.advance(3 * 24 * 3600)
    row = reconcile(sv, card, times=1)
    assert row["status"] == "ReconciliationRequired"
    assert row["evidence_json"]["slot_ran_since"] is True
