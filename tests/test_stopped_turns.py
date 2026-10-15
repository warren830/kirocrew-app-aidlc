"""A run whose turn stopped on a host error says so where the person looks, instead of reading as merely idle."""

import asyncio

import pytest

from test_e2e_gate import INTENT, _get, repo_dir, scene  # noqa: F401
from test_handlers_auth import common, routes, sv  # noqa: F401

SENT = {"role": "user", "ts": "2026-09-04T10:00:00Z", "content": "/aidlc", "meta": {}}
RETRYING = {"role": "error", "ts": "2026-09-04T10:00:20Z", "content": "⟳ Connection unstable — resuming…",
            "meta": {"kind": "transient_retry", "notice": "transient_resuming"}}
RECOVERY = {"role": "inject", "ts": "2026-09-04T10:00:22Z", "content": "[Interrupted turn — automatic recovery]",
            "meta": {}}
TOOL = {"role": "tool", "ts": "2026-09-04T10:00:40Z", "content": "🔧 bun .kiro/tools/aidlc.ts engine orchestrate next",
        "meta": {"kind": "execute"}}
FAILED = {"role": "error", "ts": "2026-09-04T10:01:00Z",
          "content": "❌ The model backend hit a transient error (HTTP 5xx). Retry in a moment.", "meta": {}}
REPLY = {"role": "assistant", "ts": "2026-09-04T10:02:00Z", "content": "Requirements are ready for review.",
         "meta": {}}


def _session(sv, routes, fake_host, scene):
    status, body = _get(sv, routes, fake_host, f"/repos/{scene.repo_id}/intents")
    assert status == 200
    return next(i for i in body["intents"] if i["intent_dir"] == INTENT)["session"]


def test_a_turn_that_stopped_on_an_error_is_listed_and_named_on_the_intent(sv, routes, fake_host, scene):
    status, body = _get(sv, routes, fake_host, "/actions")
    assert status == 200 and body["stopped_turns"] == []
    fake_host.get_slot(scene.slot_key).messages.extend([SENT, RETRYING, RECOVERY, TOOL, FAILED])
    stopped = {"at": FAILED["ts"], "text": "The model backend hit a transient error (HTTP 5xx). Retry in a moment.",
               "kind": ""}
    status, body = _get(sv, routes, fake_host, "/actions")
    assert status == 200
    assert body["stopped_turns"] == [{
        "repo_id": scene.repo_id, "repo_label": "ledger", "space": "default", "intent_dir": INTENT,
        "intent_key": INTENT, "slot_key": scene.slot_key, **stopped}]
    # The queue's own filter applies.
    status, body = _get(sv, routes, fake_host, "/actions", repo="r_other")
    assert status == 200 and body["stopped_turns"] == []
    assert _session(sv, routes, fake_host, scene)["stopped_on_error"] == stopped


@pytest.mark.parametrize("later", [SENT, RECOVERY, REPLY], ids=["a new prompt", "a recovery", "a reply"])
def test_the_conversation_going_on_past_the_error_clears_it(sv, routes, fake_host, scene, later):
    fake_host.get_slot(scene.slot_key).messages.extend([FAILED, {**later, "ts": "2026-09-04T10:05:00Z"}])
    assert _session(sv, routes, fake_host, scene)["stopped_on_error"] is None
    status, body = _get(sv, routes, fake_host, "/actions")
    assert status == 200 and body["stopped_turns"] == []


def test_a_retry_kirocrew_is_still_making_is_not_a_stop(sv, routes, fake_host, scene):
    fake_host.get_slot(scene.slot_key).messages.extend([SENT, RETRYING])
    assert _session(sv, routes, fake_host, scene)["stopped_on_error"] is None


def test_nothing_is_said_while_a_turn_runs(sv, routes, fake_host, scene):
    """KiroCrew may still be recovering the turn; the error is only where it stopped once the slot is idle."""
    slot = fake_host.get_slot(scene.slot_key)
    slot.messages.extend([SENT, FAILED])
    slot.running = True
    assert _session(sv, routes, fake_host, scene)["stopped_on_error"] is None
    status, body = _get(sv, routes, fake_host, "/actions")
    assert status == 200 and body["stopped_turns"] == []


def test_a_long_error_is_cut_and_says_so(sv, routes, fake_host, scene):
    fake_host.get_slot(scene.slot_key).messages.append({**FAILED, "content": "❌ " + "x" * 900, "meta": {"kind": "usage_limit"}})
    stopped = _session(sv, routes, fake_host, scene)["stopped_on_error"]
    assert len(stopped["text"]) == 400 and stopped["text"].endswith("…")
    assert stopped["kind"] == "usage_limit"


def test_an_until_in_milliseconds_keeps_a_row_stamped_in_that_second(sv, routes, fake_host, scene):
    """The browser sends "…:00.000Z" and Studio stores "…:00Z": compared as strings, the row fell outside."""
    asyncio.run(sv.activity.record(kind="session.bound", repo_id=scene.repo_id, at="2026-09-04T10:30:00Z"))
    status, body = _get(sv, routes, fake_host, "/activity", repo=scene.repo_id, until="2026-09-04T10:30:00.000Z")
    assert status == 200 and any(item["at"] == "2026-09-04T10:30:00Z" for item in body["items"])
    status, body = _get(sv, routes, fake_host, "/activity", until="not-an-instant")
    assert status == 400
