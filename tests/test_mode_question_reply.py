"""The answers to a questions file while AI-DLC's mode question is still open, on the UI-driven run of 2026-11-02.

AI-DLC asked "How would you like to answer the questions?" beside its intent-capture file. Studio showed the file's
five questions (the file owns its round) and delivered the person's answers. The conductor recorded that one reply as
the mode answer ("Chat"), logged the file's questions as a decision after it, and the engine refused the answers: no
reply had arrived since that decision. It wrote them into the file all the same, said "I saved your 5 answers", and
Studio settled the card as answered on the strength of the "Chat" receipt.

The engine lets one reply close both, the mode answer first, as long as nothing is asked between them (the person's
own repository recorded exactly that, 06:46:20 and 06:46:27): Studio's answers now say they choose Chat, and that no
decision belongs between the two. A refused receipt settles the card as ``answer_refused``, never as answered.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import conftest as CT
import fixtures as F
import pytest

from test_e2e_gate import _post, _queue, _report, _rescan, _row, _submit
from test_handlers_auth import common, routes, sv  # noqa: F401 - fixtures

INTENT = F.MODE_QUESTION_REPLY_INTENT
#: What the person chose on the card, as Studio stored it (``submitted_answers`` of a_03b0ebd9666feb13).
ANSWERS = [
    {"index": 1, "option_letters": ["X"],
     "free_text": "Just me: a single person who keeps a personal task list and misses deadlines"},
    {"index": 2, "option_letters": ["A", "B"]},
    {"index": 3, "option_letters": ["A"]},
    {"index": 4, "option_letters": ["A"]},
    {"index": 5, "option_letters": ["A", "B"]},
]
#: The five lines Studio sent at 12:16:33, before its delivery note.
#: What Studio sends for this run's answers: the same answers' lines the encoding's own vectors pin, so the note's
#: place is checked against the text the browser builds too.
_VECTOR = next(
    sheet for sheet in json.loads(
        (Path(__file__).parent / "fixtures" / "other-answer-mapped" / "wire-vectors.json").read_text("utf-8"))["sheets"]
    if sheet["file"] == "intent-capture-questions.md")
LINES = "\n".join(_VECTOR["lines"])
#: The user turn the live run delivered at 12:16:33, in the encoding Studio used then: the transcript rows are that.
SENT_THEN = _VECTOR["sent"]


@pytest.fixture
def clock():
    return CT.FakeClock("2026-11-02T12:13:26Z")  # when the live card was captured


@pytest.fixture
def scene(sv, routes, fake_host, repo_builder):
    root = F.mode_question_reply_tree(repo_builder.with_engine("payload").with_workspace().with_git().build(), "asked")
    status, body = _post(sv, routes, fake_host, "/repos", {"path": str(root), "label": "v2-full"})
    assert status == 201, body
    repo_id = body["repo"]["repo_id"]
    slot_key = f"aidlc-studio-{repo_id}-{INTENT}"
    slot = fake_host.add_slot(slot_key, project=str(root), agent="aidlc", app="")
    slot.messages = copy.deepcopy(F.mode_question_reply_rows("asked"))
    slot.options = suggestions(slot.messages[-1])
    sv.host.attach(fake_host)
    status, bound = _post(sv, routes, fake_host, f"/repos/{repo_id}/intents/{INTENT}/session", {"slot_key": slot_key})
    assert status == 200, bound
    return SimpleNamespace(repo_id=repo_id, slot_key=slot_key, slot=slot, root=root)


def suggestions(row: dict) -> list[str]:
    """The host's parse of a reply's ``[OPTIONS: …]`` line."""
    if row["role"] != "assistant" or "[OPTIONS:" not in row["content"]:
        return []
    line = row["content"].rsplit("[OPTIONS:", 1)[1].split("]", 1)[0]
    return [option.strip() for option in line.split("|") if option.strip()]


def cards(sv, routes, fake_host, scene) -> list[dict]:
    _rescan(sv, routes, fake_host, scene)
    return _queue(sv, routes, fake_host)


def the_files_card(sv, routes, fake_host, scene) -> dict:
    [card] = [c for c in cards(sv, routes, fake_host, scene) if c["type"] == "question"]
    questions = card["evidence"]["questions"]
    assert not questions.get("origin") and questions["relpath"].endswith(F.MODE_QUESTION_REPLY_QUESTIONS)
    assert questions["pending_count"] == 5
    return card


def deliver(sv, routes, fake_host, scene, clock, studio) -> tuple[dict, str]:
    """Send the person's answers from the card, and let the host take them, as the live run did at 12:16:33."""
    card = the_files_card(sv, routes, fake_host, scene)
    wire = LINES + studio.constants.WIRE_ANSWER_MODE_CHAT_NOTE + studio.constants.WIRE_GROUPED_ANSWER_SUFFIX
    clock.advance(187)
    status, receipt = _submit(sv, routes, fake_host, card, wire_text=wire,
                              payload={"decision": "answers", "answers": ANSWERS})
    assert status == 200, receipt
    row = {"role": "user", "content": wire, "ts": "2026-11-02T12:16:33.075985+00:00", "source_thread": "dashboard",
           "source_user": "dashboard", "meta": {"studio_action_id": card["action_id"],
                                                "studio_delivery_id": receipt["delivery_id"], "human": True}}
    scene.slot.messages.append(row)
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    return card, wire


def the_turn_ends(scene, clock, *, recorded: bool = False, file_left_blank: bool = False) -> None:
    """What the conductor's turn wrote, to its last reply at 12:19:32 (the learnings questions, with suggestions)."""
    F.mode_question_reply_tree(scene.root, "refused", recorded=recorded)
    record = F.mode_question_reply_record(scene.root)
    if file_left_blank:
        shutil.copyfile(F.MODE_QUESTION_REPLY / "questions-as-asked.md", record / F.MODE_QUESTION_REPLY_QUESTIONS)
    marker = record / ".aidlc-engine" / "human-turn"
    later = marker.stat().st_mtime_ns + 1_000_000_000
    os.utime(marker, ns=(later, later))
    rows = F.mode_question_reply_rows("refused")
    sent = len(F.mode_question_reply_rows("asked"))  # the live delivery's row, which `deliver` appended as Studio's
    assert rows[sent]["role"] == "user" and rows[sent]["content"].startswith(SENT_THEN.split("\n\n")[0])
    scene.slot.messages.extend(copy.deepcopy(rows[sent + 1:]))
    scene.slot.options = suggestions(rows[-1])
    clock.advance(181)


def reconcile(sv, card, times=3) -> dict:
    for _ in range(times):
        asyncio.run(sv.reconciler.reconcile_now(card["action_id"]))
    return _row(sv, card["action_id"])


def test_while_the_mode_question_is_open_the_files_answers_also_choose_chat(sv, routes, fake_host, scene, studio):
    """12:13:26: the mode question is the stage's open decision and the file is the card. Its answers say they choose
    Chat, before Studio's delivery note, so the conductor closes the mode question and then records the answers."""
    card = the_files_card(sv, routes, fake_host, scene)
    assert card["evidence"]["questions"]["answer_mode_pending"] is True
    snap = sv.projection.snapshot(sv.repos.get(scene.repo_id), "default", INTENT)
    assert snap.questions.answer_mode_pending is True
    C = studio.constants
    wire = studio.actions.wire_text_for("answers", {"answers": ANSWERS}, questions=snap.questions,
                                        grouped_answers_enabled=True)
    assert wire == LINES + C.WIRE_ANSWER_MODE_CHAT_NOTE + C.WIRE_GROUPED_ANSWER_SUFFIX
    assert "Chat" in C.WIRE_ANSWER_MODE_CHAT_NOTE and "no decision" in C.WIRE_ANSWER_MODE_CHAT_NOTE
    # One question left to answer sends its answer alone, and the note still goes with it.
    one_left = replace(snap.questions, questions=snap.questions.questions[2:3], pending_count=1)
    single = studio.actions.wire_text_for("answers", {"answers": ANSWERS[2:3]}, questions=one_left,
                                          grouped_answers_enabled=True)
    assert single == "A. A real deadline was missed recently" + C.WIRE_ANSWER_MODE_CHAT_NOTE


def test_a_refused_receipt_settles_the_card_as_refused_not_answered(sv, routes, fake_host, scene, clock, studio):
    """12:19:34 live: "question_answered", on the mode answer alone. The refusal is what the delivery came to; the
    answers are in the file only because the conductor wrote them there anyway."""
    card, _ = deliver(sv, routes, fake_host, scene, clock, studio)
    the_turn_ends(scene, clock)
    row = reconcile(sv, card)
    assert row["status"] == "ResolvedNoTransition", row["resolution_json"]
    resolution = row["resolution_json"]
    assert resolution["reason"] == "answer_refused"
    evidence = resolution["evidence"]
    assert evidence["question_answered"] is None  # "Chat" answers the mode question, not the file
    assert evidence["answer_verified"] is False and evidence["answers_in_file"] is True
    refusal = evidence["answer_refused"]
    assert refusal["event"] == "ERROR_LOGGED" and refusal["timestamp"] == "2026-11-02T12:16:56Z"
    assert "no new human reply has arrived" in refusal["fields"]["Error"]

    # What AI-DLC waits on next is its learnings question in the conversation; the file it filled raises nothing.
    [reply] = cards(sv, routes, fake_host, scene)
    assert (reply["type"], reply["headline"]["params"]["reason"]) == ("missing_input", "conversation_reply")
    assert reply["headline"]["params"]["options"][0] == "Keep none, nothing to add"
    assert reply["evidence"]["conversation_reply"]["text"].startswith("I saved your 5 answers.")


def test_a_refused_receipt_with_the_file_still_blank_offers_the_files_questions_again(
        sv, routes, fake_host, scene, clock, studio):
    """Had the conductor stopped at the refusal, the file's questions are what the person answers, now without the
    mode note: the mode answer is on record, and the round's decision waits for the answers' reply."""
    card, _ = deliver(sv, routes, fake_host, scene, clock, studio)
    the_turn_ends(scene, clock, file_left_blank=True)
    assert reconcile(sv, card)["resolution_json"]["reason"] == "answer_refused"
    again = the_files_card(sv, routes, fake_host, scene)
    assert again["action_id"] != card["action_id"] and again["status"] == "Queued"
    assert not again["evidence"]["questions"].get("answer_mode_pending")


def test_both_receipts_from_the_one_reply_settle_the_card_as_answered(sv, routes, fake_host, scene, clock, studio):
    """What the engine records when the conductor follows the note: "Chat", then the answers, nothing asked between.
    The receipt is the answers', not the mode answer's."""
    card, _ = deliver(sv, routes, fake_host, scene, clock, studio)
    the_turn_ends(scene, clock, recorded=True)
    resolution = reconcile(sv, card)["resolution_json"]
    assert resolution["reason"] == "question_answered", resolution
    assert resolution["evidence"]["question_answered"]["fields"]["Details"].startswith("Q1: Just me: ")
    # The round's own receipt is on record, so the learnings asked after it are a card of their own (dfc7b54).
    [learnings] = cards(sv, routes, fake_host, scene)
    assert learnings["type"] == "question" and learnings["evidence"]["questions"]["origin"]["kind"] == "audit"
    # In the order the chat asked them (keep first), not the order the conductor logged them.
    assert [q["prompt"] for q in learnings["evidence"]["questions"]["questions"]] == [
        "Which notes should be kept for next time?", "Anything to add for next time?"]


def test_once_the_mode_is_on_record_the_files_answers_carry_no_note(sv, routes, fake_host, scene, studio):
    """Requirements Analysis reused "Chat" and asked no mode question: its answers go out as before."""
    F.mode_question_reply_tree(scene.root, "refused")
    record = F.mode_question_reply_record(scene.root)
    shutil.copyfile(F.MODE_QUESTION_REPLY / "questions-as-asked.md", record / F.MODE_QUESTION_REPLY_QUESTIONS)
    shard = record / "audit" / F.MODE_QUESTION_REPLY_SHARD
    text = shard.read_text("utf-8")
    shard.write_text(text[:text.index("\n## Artifact Created", text.index("**Event**: ERROR_LOGGED"))], "utf-8")
    snap = sv.projection.snapshot(sv.repos.get(scene.repo_id), "default", INTENT)
    assert snap.questions.pending_count == 5 and snap.questions.answer_mode_pending is None
    wire = studio.actions.wire_text_for("answers", {"answers": ANSWERS}, questions=snap.questions,
                                        grouped_answers_enabled=True)
    assert wire == LINES + studio.constants.WIRE_GROUPED_ANSWER_SUFFIX


# ---- the real engine ------------------------------------------------------------------------------------------- #

ROOT = Path(__file__).resolve().parents[1]
_HOME_BUN = Path.home() / ".local" / "bin" / "bun"
BUN = shutil.which("bun") or (str(_HOME_BUN) if _HOME_BUN.exists() else None)
SESSION = "0f0e0d0c-1111-2222-3333-444455556666"
MODE_PROMPT = "How would you like to answer the questions?"


def _env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith(("AIDLC_", "CLAUDE_"))}


def _engine(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([BUN, ".kiro/tools/aidlc.ts", *args], cwd=repo, capture_output=True, text=True,
                          env=_env(), timeout=120)


def _hook(repo: Path, name: str, prompt: str = "") -> None:
    event = {"session-start": "agentSpawn", "verb-intercept": "userPromptSubmit"}[name]
    payload = {"hook_event_name": event, "cwd": str(repo), "session_id": SESSION, "prompt": prompt}
    subprocess.run([BUN, ".kiro/tools/aidlc.ts", "engine", "adapter", "kiro", name], cwd=repo,
                   input=json.dumps(payload), text=True, check=True, capture_output=True, env=_env(), timeout=120)


@pytest.fixture(scope="module")
def asked(tmp_path_factory) -> Path:
    """A fresh repository with the bundled payload at intent-capture, its file written and the mode question logged."""
    if BUN is None:
        pytest.skip("bun is not installed")
    root = tmp_path_factory.mktemp("mode-question") / "repo"
    shutil.copytree(ROOT / "payload" / "aidlc-kiro", root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run([BUN, ".kiro/tools/aidlc-utility.ts", "intent-create", "--scope", "poc", "--arguments",
                    "Add an optional due date to tasks", "--label", "due-dates"],
                   cwd=root, check=True, capture_output=True, env=_env(), timeout=120)
    _hook(root, "session-start")
    _hook(root, "verb-intercept", "/aidlc")
    directive = json.loads(_engine(root, "engine", "orchestrate", "next").stdout)
    while directive["kind"] == "load-steering":
        directive = json.loads(subprocess.run(directive["next"].split(), cwd=root, capture_output=True, text=True,
                                              env=_env(), timeout=120).stdout)
    # 2.11.2 asks the mode question by a note in the stage directive, with its own question kind (2.11.0 carried
    # `answer_mode: {ask: true}`).
    assert (directive["kind"], directive["stage"]) == ("run-stage", "intent-capture")
    assert "--question-kind answer-mode" in directive["answer_mode_note"]
    record = next((root / "aidlc" / "spaces" / "default" / "intents").glob("*-due-dates"))
    questions = record / F.MODE_QUESTION_REPLY_QUESTIONS
    questions.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(F.MODE_QUESTION_REPLY / "questions-as-asked.md", questions)
    logged = _engine(root, "engine", "log", "decision", "--stage", "intent-capture", "--decision", MODE_PROMPT,
                     "--options", "Guide me,I'll edit the file,Chat", "--question-kind", "answer-mode")
    assert logged.returncode == 0, logged.stderr
    return root


@pytest.fixture
def replied(asked, tmp_path, studio) -> SimpleNamespace:
    """The person's one reply, Studio's exact text for the card, as the prompt hook records it."""
    repo = tmp_path / "repo"
    shutil.copytree(asked, repo, symlinks=True)
    C = studio.constants
    _hook(repo, "verb-intercept", LINES + C.WIRE_ANSWER_MODE_CHAT_NOTE + C.WIRE_GROUPED_ANSWER_SUFFIX)

    def answer(details: str) -> subprocess.CompletedProcess:
        return _engine(repo, "engine", "log", "answer", "--stage", "intent-capture", "--details", details)

    def decision(prompt: str, options: str) -> subprocess.CompletedProcess:
        return _engine(repo, "engine", "log", "decision", "--stage", "intent-capture", "--decision", prompt,
                       "--options", options)

    def receipts() -> list[str]:
        shard = next(repo.glob("aidlc/spaces/default/intents/*/audit/*.md")).read_text("utf-8")
        return re.findall(r"\*\*Event\*\*: QUESTION_ANSWERED\n\*\*Stage\*\*: intent-capture\n\*\*Details\*\*: (.*)",
                          shard)
    return SimpleNamespace(repo=repo, answer=answer, decision=decision, receipts=receipts,
                           answers=LINES.replace("\n", "; "))


def test_the_engine_records_the_mode_and_the_answers_from_the_one_reply(replied):
    """The order the note asks for: both receipts, from one human turn."""
    assert replied.answer("Chat").returncode == 0
    recorded = replied.answer(replied.answers)
    assert recorded.returncode == 0, recorded.stderr
    assert replied.receipts() == ["Chat", replied.answers]


def test_a_decision_logged_since_the_reply_takes_that_reply(replied):
    """The live run's order, 12:16:46-56: a question logged after the reply. 2.11.0 refused its answers ("no new
    human reply has arrived"); 2.11.2 records them: "A question you log after the person already replied takes that
    reply" (stage-protocol §2)."""
    assert replied.answer("Chat").returncode == 0
    assert replied.decision("Q1-Q5 intent capture questions",
                            "Q1: A-D,X; Q2: A-E,X; Q3: A-D,X; Q4: A-D,X; Q5: A-E,X").returncode == 0
    recorded = replied.answer(replied.answers)
    assert recorded.returncode == 0, recorded.stderr
    assert replied.receipts() == ["Chat", replied.answers]


def test_the_engine_refuses_the_answers_as_the_mode_answer(replied):
    """The person's own repository, 06:46:01: the answers name no way to answer."""
    refused = replied.answer(replied.answers)
    assert refused.returncode == 1 and "does not name a way to answer" in refused.stderr
    assert replied.receipts() == []
