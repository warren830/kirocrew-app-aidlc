"""Turning AI-DLC's learnings ritual off (or back on) for one intent, from Studio.

The ritual asks, before every stage's approval, which of the agent's diary notes to keep and whether the person has
anything to add (stage-protocol-learnings.md §13). The setting is the engine's line, ``- **Learnings**: on (from scope
feature)``, and Studio only reads it. The person changes it by typing AI-DLC's own switch as a turn of its own
(``/aidlc --learnings off``). Unlike summary confirmation, the bundled prompt hook does not apply it: it records the
turn as a command and forwards it to the conductor's ``next``, whose setter writes ``off (set by a command)`` (proved
with the real engine in ``test_payload_learnings_switch.py``). Studio's part mirrors the summary switch, a command card
created by a click, sent only through the two-phase submit with the exact text confirmed, refused where the send would
be, never folded into a question's reply, and resolved on the engine's line once the turn ends.
"""

from __future__ import annotations

import asyncio
import time

import fixtures as F
import pytest

from _helpers_a import stamp_human_turn
from test_e2e_gate import (  # noqa: F401
    INTENT, STAGE, _get, _post, _queue, _record_dir, _report, _rescan, _row, _shard, _submit, _tree_digest, scene,
)
from test_handlers_auth import call, common, routes, sv  # noqa: F401

import conftest as CT

C = CT.studio_module("constants")
reader = CT.studio_module("aidlc_reader")
OFF, ON = C.WIRE_LEARNINGS_OFF, C.WIRE_LEARNINGS_ON
ADD_PROMPT = C.LEARNINGS_ADD_PROMPTS[0]
ADD_LABELS = ("Nothing to add", "Add a note")


def _with_lines(text: str, learnings: str | None, summary: str | None = "on (from scope feature)") -> str:
    """A state file carrying the engine's ceremony lines where 2.11 writes them, under Test Strategy."""
    anchor = "- **Test Strategy**: Standard\n"
    assert anchor in text
    lines = "".join(f"- **{field}**: {value}\n" for field, value in (
        (C.LEARNINGS_FIELD, learnings), (C.SUMMARY_CONFIRMATION_FIELD, summary)) if value is not None)
    return text.replace(anchor, anchor + lines, 1)


def _state(line: str | None = "on (from scope feature)", **fields: str) -> str:
    values = {"Current Stage": STAGE, "Status": "Running", "Next Stage": "user-stories", "Lifecycle Phase": "INCEPTION"}
    return _with_lines(F.state_text(marks={STAGE: "-"}, fields={**values, **fields}), line)


@pytest.fixture
def repo_dir(repo_builder):
    """An intent mid-stage with the learnings ritual on, as a feature-scope intent is created."""
    return repo_builder.with_engine("payload").with_workspace().with_intent(
        INTENT, state=_state(),
        audit=F.audit_text(F.audit_block("HUMAN_TURN", "2026-09-04T09:00:00Z"),
                           F.audit_block("STAGE_STARTED", "2026-09-04T09:00:01Z", Stage=STAGE)),
        directive={"version": 1, "stage": STAGE, "state_sha256": "AUTO"},
        artifacts={f"inception/{STAGE}/requirements.md": "# Requirements\n"},
    ).with_git().build()


def _write_state(scene, text: str) -> None:
    (_record_dir(scene.root) / "aidlc-state.md").write_text(text)


def _ask(sv, routes, fake_host, scene, value: str = "off"):
    return _post(sv, routes, fake_host, f"/repos/{scene.repo_id}/intents/{INTENT}/learnings", {"value": value})


def _send(sv, routes, fake_host, card: dict, value: str = "off", *, wire: str | None = None):
    return _submit(sv, routes, fake_host, card, payload={"decision": "set_learnings", "value": value},
                   wire_text=wire if wire is not None else (OFF if value == "off" else ON))


def _ask_learnings(scene) -> None:
    """The ritual's add question, as the conductor logs it before asking (§13 step 3)."""
    shard = _shard(scene.root)
    shard.write_text(shard.read_text() + F.audit_text(F.audit_block(
        "DECISION_RECORDED", "2026-09-04T09:59:30Z", Stage=STAGE, Decision=ADD_PROMPT, Options=",".join(ADD_LABELS))))


def _learnings_card(sv, routes, fake_host, scene) -> dict:
    _rescan(sv, routes, fake_host, scene)
    [card] = [c for c in _queue(sv, routes, fake_host) if c["type"] == "question"]
    return card


# --- the engine's line, read and shown ---


@pytest.mark.parametrize(("raw", "expected"), [
    ("off (set by a command)", ("off", "set by a command")),
    ("on (from scope feature)", ("on", "from scope feature")),
    ("off (set by you)", ("off", "set by you")),
    ("OFF", ("off", None)),
])
def test_the_line_reads_as_the_engine_reads_it(raw, expected):
    state = reader.parse_state_file(_with_lines(F.state_text(), raw))
    found = reader.ceremony_setting(state, C.LEARNINGS_FIELD)
    assert (found.value, found.source) == expected and found.line == raw


def test_each_ceremony_reads_its_own_line():
    state = reader.parse_state_file(_with_lines(F.state_text(), "off (set by a command)", "on (from scope feature)"))
    assert reader.ceremony_setting(state, C.LEARNINGS_FIELD).value == "off"
    assert reader.ceremony_setting(state, C.SUMMARY_CONFIRMATION_FIELD).value == "on"
    assert reader.ceremony_setting(reader.parse_state_file(F.state_text()), C.LEARNINGS_FIELD) is None


def test_the_intent_and_its_cards_show_the_line_as_written(sv, routes, fake_host, scene):
    _write_state(scene, _state("off (set by a command)"))
    status, body = _get(sv, routes, fake_host, f"/repos/{scene.repo_id}/intents/{INTENT}")
    assert status == 200
    assert body["intent"]["disk"]["learnings"] == {"value": "off", "source": "set by a command",
                                                   "line": "off (set by a command)"}
    assert body["intent"]["disk"]["summary_confirmation"]["line"] == "on (from scope feature)"
    _ask_learnings(scene)
    card = _learnings_card(sv, routes, fake_host, scene)
    assert card["evidence"]["questions"]["questions"][0]["prompt"] == ADD_PROMPT
    assert card["evidence"]["state"]["learnings"]["line"] == "off (set by a command)"


# --- the request: a command card, nothing sent ---


def test_asking_creates_a_queued_card_and_sends_nothing(sv, routes, fake_host, scene):
    status, body = _ask(sv, routes, fake_host, scene)
    assert status == 201, body
    card = body["action"]
    assert (card["type"], card["status"], card["risk_class"]) == ("set_learnings", "Queued", "human_lane")
    assert [(d["decision"], d["wire_text_template"]) for d in card["decisions"]] == [("set_learnings", OFF)]
    assert card["headline"] == {"key": "action.set_learnings.headline", "params": {"stage": STAGE, "value": "off"}}
    assert fake_host.get_slot(scene.slot_key).messages == [] and card["delivery"]["delivery_id"] is None
    assert _tree_digest(scene.root) == scene.digest_before


def test_one_live_card_per_value_and_per_ceremony(sv, routes, fake_host, scene):
    first, again = (_ask(sv, routes, fake_host, scene)[1] for _ in range(2))
    assert first["action_id"] == again["action_id"] and len(sv.storage.select("actions")) == 1
    # The summary switch is a request of its own, never this card again.
    status, summary = _post(sv, routes, fake_host, f"/repos/{scene.repo_id}/intents/{INTENT}/summary-confirmation",
                            {"value": "off"})
    assert status == 201 and summary["action"]["type"] == "set_summary_confirmation"
    assert summary["action_id"] != first["action_id"]
    # Turned off another way (the terminal): back on is a different request, with its own text.
    _write_state(scene, _state("off (set by a command)"))
    status, on = _ask(sv, routes, fake_host, scene, "on")
    assert status == 201 and on["action_id"] != first["action_id"]
    assert on["action"]["decisions"][0]["wire_text_template"] == ON
    assert on["action"]["headline"]["params"]["value"] == "on"


@pytest.mark.parametrize("body", [{}, {"value": "maybe"}, {"value": True}])
def test_only_the_engines_two_words_are_asked_for(sv, routes, fake_host, scene, body):
    status, answer = _post(sv, routes, fake_host, f"/repos/{scene.repo_id}/intents/{INTENT}/learnings", body)
    assert status == 400 and answer["code"] in ("bad_body", "bad_param") and not sv.storage.select("actions")


def test_a_setting_already_in_force_is_not_asked_for_again(sv, routes, fake_host, scene):
    _write_state(scene, _state("off (set by a command)"))
    status, body = _ask(sv, routes, fake_host, scene)
    assert (status, body["code"], body["details"]["reason"]) == (400, "invalid_decision", "already_set")
    assert body["details"]["current"] == "off (set by a command)" and not sv.storage.select("actions")
    assert _ask(sv, routes, fake_host, scene, "on")[0] == 201


def test_a_line_the_engine_never_wrote_is_the_scope_default_and_may_be_turned_off(sv, routes, fake_host, scene):
    _write_state(scene, _state(None))
    assert _ask(sv, routes, fake_host, scene)[0] == 201


def test_a_finished_workflow_has_nothing_left_to_learn_from(sv, routes, fake_host, scene):
    _write_state(scene, _state(Status="Completed"))
    status, body = _ask(sv, routes, fake_host, scene)
    assert (status, body["code"], body["details"]["reason"]) == (400, "invalid_decision", "intent_completed")


@pytest.mark.parametrize(("setter", "code"), [("set_paused", "intent_paused"), ("set_archived", "intent_archived")])
def test_a_paused_or_archived_intent_is_refused_before_a_card_exists(sv, routes, fake_host, scene, setter, code):
    asyncio.run(getattr(sv.sessions, setter)(scene.repo_id, "default", INTENT, True))
    status, body = _ask(sv, routes, fake_host, scene)
    assert (status, body["code"]) == (409, code) and not sv.storage.select("actions")


def test_a_running_conversation_is_refused_before_a_card_exists(sv, routes, fake_host, scene):
    fake_host.get_slot(scene.slot_key).running = True
    status, body = _ask(sv, routes, fake_host, scene)
    assert (status, body["code"]) == (409, "slot_busy") and not sv.storage.select("actions")


@pytest.mark.parametrize(("request_of", "code"), [
    (CT.anon_request, "unauthorized"), (CT.user_request, "owner_required"), (CT.app_request, "app_token_forbidden"),
])
def test_only_the_owner_may_ask(sv, routes, fake_host, scene, request_of, code):
    path = f"/repos/{scene.repo_id}/intents/{INTENT}/learnings"
    status, body = call(sv, routes, "POST", path, request_of("POST", path, host=fake_host, body={"value": "off"}))
    assert (status, body["code"]) == ({"unauthorized": 401}.get(code, 403), code)
    assert not sv.storage.select("actions")


def test_a_failed_request_is_retried_as_the_same_request(sv, routes, fake_host, scene):
    card = _ask(sv, routes, fake_host, scene)[1]["action"]
    row = _row(sv, card["action_id"])
    sv.storage.cas_update("actions", card["action_id"], int(row["status_generation"]), {"status": "Failed"})
    retried = asyncio.run(sv.actions.retry(card["action_id"], user="owner-1"))
    assert retried.action_id != card["action_id"] and retried.type == "set_learnings"
    assert retried.card_meta["setting_value"] == "off"
    [spec] = asyncio.run(sv.actions.card(retried, None))["decisions"]
    assert spec["wire_text_template"] == OFF


# --- the send: exactly the switch, once, on its own ---


def test_the_send_is_exactly_the_switch_and_is_durable_before_the_browser_sends(sv, routes, fake_host, scene):
    card = _ask(sv, routes, fake_host, scene)[1]["action"]
    status, receipt = _send(sv, routes, fake_host, card)
    assert status == 200, receipt
    assert receipt["wire_text"] == OFF == "/aidlc --learnings off"
    assert receipt["host"]["body"]["message"] == OFF and receipt["host"]["body"]["slot"] == scene.slot_key
    row = _row(sv, card["action_id"])
    assert row["status"] == "Delivering" and row["wire_text"] == OFF and row["human_text"] is None
    assert fake_host.get_slot(scene.slot_key).messages == [] and _tree_digest(scene.root) == scene.digest_before


@pytest.mark.parametrize(("value", "wire"), [
    ("off", "/aidlc --learnings off\nNothing to add"),
    ("off", "Nothing to add /aidlc --learnings off"),
    ("off", ON),
    ("off", C.WIRE_SUMMARY_CONFIRMATION_OFF),
    ("on", OFF),
    ("on", ON),
])
def test_the_browser_cannot_change_the_text_or_the_value(sv, routes, fake_host, scene, value, wire):
    card = _ask(sv, routes, fake_host, scene)[1]["action"]
    status, body = _send(sv, routes, fake_host, card, value, wire=wire)
    assert (status, body["code"]) == (400, "invalid_decision")
    assert _row(sv, card["action_id"])["status"] == "Queued"


def test_a_conversation_that_started_running_refuses_the_send(sv, routes, fake_host, scene):
    card = _ask(sv, routes, fake_host, scene)[1]["action"]
    fake_host.get_slot(scene.slot_key).running = True
    status, body = _send(sv, routes, fake_host, card)
    assert (status, body["code"]) == (409, "slot_busy") and _row(sv, card["action_id"])["status"] == "Queued"


def test_an_unbound_intent_refuses_the_send(sv, routes, fake_host, scene):
    card = _ask(sv, routes, fake_host, scene)[1]["action"]
    status, _ = _post(sv, routes, fake_host, f"/repos/{scene.repo_id}/intents/{INTENT}/session", {"slot_key": None})
    assert status == 200
    status, body = _send(sv, routes, fake_host, card)
    assert (status, body["code"]) == (409, "session_unbound")


def test_a_card_for_what_the_line_already_says_is_retired_rather_than_sent(sv, routes, fake_host, scene):
    """Queued, then turned off from the terminal: sending it now would spend a turn on "already off"."""
    card = _ask(sv, routes, fake_host, scene)[1]["action"]
    _write_state(scene, _state("off (set by a command)"))
    status, body = _send(sv, routes, fake_host, card, wire=OFF)
    assert (status, body["code"], body["details"]["reason"]) == (400, "invalid_decision", "already_set")
    assert body["details"]["current"] == "off (set by a command)"
    assert _row(sv, card["action_id"])["status"] == "Cancelled"
    assert fake_host.get_slot(scene.slot_key).messages == []


def test_an_open_learnings_question_keeps_its_own_reply(sv, routes, fake_host, scene):
    """The switch is its own card and its own text: the question's reply stays exactly the label picked."""
    _ask_learnings(scene)
    question = _learnings_card(sv, routes, fake_host, scene)
    assert [d["decision"] for d in question["decisions"]] == ["answers"]
    card = _ask(sv, routes, fake_host, scene)[1]["action"]
    status, receipt = _send(sv, routes, fake_host, card)
    assert status == 200 and receipt["wire_text"] == OFF
    after = next(c for c in _queue(sv, routes, fake_host) if c["action_id"] == question["action_id"])
    assert after["status"] == "Queued" and [d["decision"] for d in after["decisions"]] == ["answers"]
    assert [o["text"] for o in after["evidence"]["questions"]["questions"][0]["options"]][:2] == list(ADD_LABELS)


# --- the outcome: the engine's line, once the turn ends ---


def _engine_reads_the_switch(scene, fake_host, wire: str, line: str | None) -> None:
    """What the turn leaves behind: one HUMAN_TURN (a command), the moved marker and, when the conductor ran its
    setter, the line it wrote and its CEREMONY_SET row."""
    ts = "2026-09-04T10:00:20Z"
    shard = _shard(scene.root)
    blocks = [F.audit_block("HUMAN_TURN", ts, Reply="command")]
    if line is not None:
        blocks.append(F.audit_block("CEREMONY_SET", ts, Key="learnings", Old="on" if line.startswith("off") else "off",
                                    New=line.split()[0], Source="command"))
        _write_state(scene, _state(line))
    shard.write_text(shard.read_text() + F.audit_text(*blocks))
    stamp_human_turn(_record_dir(scene.root), ts, ns=time.time_ns())
    slot = fake_host.get_slot(scene.slot_key)
    slot.messages += [
        {"role": "user", "content": wire, "cls": "msg msg-u", "ts": "2026-09-04T10:00:05Z", "meta": {}},
        {"role": "assistant", "content": "Learnings changed.", "cls": "msg msg-a", "ts": "2026-09-04T10:00:25Z",
         "meta": {}},
    ]
    slot.running = False


def _delivered(sv, routes, fake_host, scene, value: str = "off") -> str:
    card = _ask(sv, routes, fake_host, scene, value)[1]["action"]
    status, receipt = _send(sv, routes, fake_host, card, value)
    assert status == 200, receipt
    fake_host.append_user_row(scene.slot_key, OFF if value == "off" else ON, "2026-09-04T10:00:05Z")
    status, body = _report(sv, routes, fake_host, card["action_id"], receipt)
    assert status == 200, body
    return card["action_id"]


@pytest.mark.parametrize(("value", "line"), [("off", "off (set by a command)"), ("on", "on (set by a command)")])
def test_the_card_settles_on_the_line_the_engine_wrote(sv, routes, fake_host, scene, value, line):
    if value == "on":
        _write_state(scene, _state("off (set by a command)"))
    action_id = _delivered(sv, routes, fake_host, scene, value)
    _engine_reads_the_switch(scene, fake_host, OFF if value == "off" else ON, line)
    asyncio.run(sv.reconciler.tick())
    row = _row(sv, action_id)
    resolution = dict(row["resolution_json"] or {})
    assert (row["status"], resolution["reason"]) == ("StateChanged", "learnings_set")
    assert resolution["evidence"]["learnings"] == line


def test_a_turn_that_left_the_line_alone_says_so(sv, routes, fake_host, scene):
    """The conductor did not run its setter (it stopped, or answered something else): no claim that it changed."""
    action_id = _delivered(sv, routes, fake_host, scene)
    _engine_reads_the_switch(scene, fake_host, OFF, None)
    asyncio.run(sv.reconciler.tick())
    row = _row(sv, action_id)
    resolution = dict(row["resolution_json"] or {})
    assert (row["status"], resolution["reason"]) == ("ResolvedNoTransition", "learnings_unchanged")
    assert resolution["evidence"]["learnings"] == "on (from scope feature)"


def test_the_summary_line_does_not_settle_a_learnings_request(sv, routes, fake_host, scene):
    action_id = _delivered(sv, routes, fake_host, scene)
    _engine_reads_the_switch(scene, fake_host, OFF, None)
    _write_state(scene, _with_lines(_state(None), None, "off (set by you)"))
    asyncio.run(sv.reconciler.tick())
    resolution = dict(_row(sv, action_id)["resolution_json"] or {})
    assert resolution["reason"] == "learnings_unchanged" and resolution["evidence"]["learnings"] is None


def test_the_card_waits_while_the_turn_runs(sv, routes, fake_host, scene):
    action_id = _delivered(sv, routes, fake_host, scene)
    _write_state(scene, _state("off (set by a command)"))
    fake_host.get_slot(scene.slot_key).running = True
    asyncio.run(sv.reconciler.tick())
    assert _row(sv, action_id)["status"] == "Delivered"


def test_an_activity_row_about_the_request_names_no_stage(sv, routes, fake_host, scene):
    """The setting is the intent's from that turn on, like a commit request: the stage it captured is incidental."""
    action_id = _delivered(sv, routes, fake_host, scene)
    status, body = _get(sv, routes, fake_host, "/activity")
    assert status == 200, body
    rows = [item for item in body["items"] if item["refs"].get("action_id") == action_id]
    assert rows and all("stage" not in item["params"] for item in rows)
