"""A stage's questions file owns its question round, on the two real runs of 2026-11-02.

Both times Studio delivered the person's answers to the file's six questions; the conductor recorded them as the
answer to the mode question ("Chat"), logged the file's questions as one decision of its own after that reply
("Practices interview Q1-Q6 (…)", Options "Q1 A/B/C/X,…,Q6 A-E/X multi"; "Q1-Q6 intent capture questions", Options
"Q1,…,Q6"), and the engine refused the answer it tried next, since no reply had arrived after that decision. The file
had, or then got, the answers. Studio raised the decision as a one-question card, "0 of 1 unanswered", whose choices
sent "Q1", and AI-DLC asked for all six answers again.

While the file has unanswered questions it is the card. A decision logged while its round was open, with only the
mode answer recorded before it, belongs to that round: once the file is answered nothing raises it again. A question
asked after the round's own answer was recorded, the learnings ritual, is still a card of its own.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import conftest as CT
import fixtures as F
import pytest

from test_e2e_gate import _post, _queue, _rescan
from test_handlers_auth import common, routes, sv  # noqa: F401 - fixtures

FILE_PROMPTS = [
    "How should changes reach `main`?", "Build a thin end-to-end slice first?",
    "When are tests written, and how much is required?", "Which Python, and how is pytest provided?",
    "Add a formatter or linter?", "Which of these become firm rules? (pick any)",
]
FILE_LETTERS = [list("ABCX"), list("ABX"), list("ABCX"), list("ABX"), list("ABX"), list("ABCDEX")]
CHAT_ONLY_PROMPTS = [
    "What problem does this solve, and for whom?", "What does success look like for this first version?",
    "Why do this now?", "Who decides what goes in, and who else cares about the result?",
    "Does anyone need progress updates while this is built?",
    "What should this first version leave out? (select all that apply)",
]


@pytest.fixture
def clock(request):
    return CT.FakeClock(getattr(request, "param", "2026-11-02T09:24:35Z"))  # when the live card was raised


def at(when: str):
    return pytest.mark.parametrize("clock", [when], indirect=True)


@pytest.fixture
def scene(sv, routes, fake_host, repo_builder):
    def build(tree, intent: str, rows: list[dict], *, running: bool = False) -> SimpleNamespace:
        root = tree(repo_builder.with_engine("payload").with_workspace().with_git().build())
        status, body = _post(sv, routes, fake_host, "/repos", {"path": str(root), "label": "real-run"})
        assert status == 201, body
        repo_id = body["repo"]["repo_id"]
        slot_key = f"aidlc-studio-{repo_id}-{intent}"
        slot = fake_host.add_slot(slot_key, project=str(root), agent="aidlc", app="", running=running)
        slot.messages = copy.deepcopy(rows)
        slot.options = suggestions(rows[-1])
        sv.host.attach(fake_host)
        status, bound = _post(sv, routes, fake_host, f"/repos/{repo_id}/intents/{intent}/session",
                              {"slot_key": slot_key})
        assert status == 200, bound
        return SimpleNamespace(repo_id=repo_id, slot_key=slot_key, slot=slot, root=root)
    return build


def suggestions(row: dict) -> list[str]:
    """The host's parse of a reply's ``[OPTIONS: …]`` line."""
    if row["role"] != "assistant" or "[OPTIONS:" not in row["content"]:
        return []
    line = row["content"].rsplit("[OPTIONS:", 1)[1].split("]", 1)[0]
    return [option.strip() for option in line.split("|") if option.strip()]


def practices(scene, moment: str, **kwargs) -> SimpleNamespace:
    return scene(lambda root: F.practices_batch_tree(root, moment), F.PRACTICES_BATCH_INTENT,
                 F.practices_batch_rows(moment), **kwargs)


def cards(sv, routes, fake_host, built) -> list[dict]:
    _rescan(sv, routes, fake_host, built)
    return _queue(sv, routes, fake_host)


def assert_the_files_questions(card: dict, relpath: str, prompts: list[str]) -> None:
    questions = card["evidence"]["questions"]
    assert card["type"] == "question" and [d["decision"] for d in card["decisions"]] == ["answers"]
    assert not questions.get("origin") and questions["relpath"].endswith(relpath)
    assert questions["pending_count"] == 6
    assert [q["prompt"] for q in questions["questions"]] == prompts
    assert not any(q["answered"] for q in questions["questions"])


@at("2026-11-02T09:24:15Z")
def test_while_the_file_is_open_its_six_questions_are_the_card(sv, routes, fake_host, scene):
    """09:24:14: the batch decision is pending and its answer refused; the file still has its blank tags. The card was
    raised when the round was asked; the turn running now keeps it (a turn still writing raises none of its own)."""
    built = practices(scene, "open")
    [raised] = cards(sv, routes, fake_host, built)
    built.slot.running = True
    [card] = cards(sv, routes, fake_host, built)
    assert card["action_id"] == raised["action_id"]
    assert_the_files_questions(card, F.PRACTICES_BATCH_QUESTIONS, FILE_PROMPTS)
    assert [[o["letter"] for o in q["options"]] for q in card["evidence"]["questions"]["questions"]] == FILE_LETTERS


@at("2026-11-02T09:24:35Z")
def test_once_the_file_is_answered_the_batch_decision_raises_no_card(sv, routes, fake_host, scene, studio):
    """09:24:30: the conductor wrote the answers into the file and asked in chat for them once more. Its suggestion
    is what the person sends next (they did, at 10:16, and the engine recorded it), not "Q1"."""
    built = practices(scene, "filled")
    snap = sv.projection.snapshot(sv.repos.get(built.repo_id), "default", F.PRACTICES_BATCH_INTENT)
    pending = studio.aidlc_reader.audit_questions(
        snap.audit, f"aidlc/spaces/default/intents/{F.PRACTICES_BATCH_INTENT}", "practices-discovery", None)
    assert pending.questions[0].prompt == F.PRACTICES_BATCH_DECISION  # still open in the audit: the live card's
    [card] = cards(sv, routes, fake_host, built)
    assert (card["type"], card["headline"]["params"]["reason"]) == ("missing_input", "conversation_reply")
    assert card["headline"]["params"]["options"] == ["Q1: A, Q2: A, Q3: A, Q4: A, Q5: A, Q6: A"]
    assert "Your answer didn't reach AI-DLC." in card["evidence"]["conversation_reply"]["text"]
    assert not card["evidence"]["questions"].get("origin")


@at("2026-11-02T10:18:31Z")
def test_the_learnings_asked_after_the_rounds_answer_are_still_a_card(sv, routes, fake_host, scene):
    built = practices(scene, "learnings")
    [card] = cards(sv, routes, fake_host, built)
    questions = card["evidence"]["questions"]
    assert card["type"] == "question" and questions["origin"]["kind"] == "audit"
    # Logged "Anything to add?" first, asked second in the chat (10:18:35): the card follows the chat.
    assert [q["prompt"] for q in questions["questions"]] == [
        "Which notes should be kept for next time?", "Anything to add for next time?"]
    assert [o["text"] for o in questions["questions"][1]["options"]][:2] == ["Nothing to add", "Add a note"]


@at("2026-11-02T08:43:52Z")
def test_the_mode_question_asked_with_the_file_leaves_the_file_as_the_card(sv, routes, fake_host, scene):
    """08:43:44: as before, while the mode question is pending the file's questions are the card; the conductor reads
    the mode from how the person answered them."""
    built = practices(scene, "asked")
    [card] = cards(sv, routes, fake_host, built)
    assert_the_files_questions(card, F.PRACTICES_BATCH_QUESTIONS, FILE_PROMPTS)


CHAT_ONLY_QUESTIONS = "ideation/intent-capture/intent-capture-questions.md"


def chat_only(scene, *, open_file: bool) -> SimpleNamespace:
    def tree(root):
        F.chat_only_reply_tree(root, replied=False)
        if open_file:
            path = F.chat_only_reply_record(root) / CHAT_ONLY_QUESTIONS
            path.write_text(F.blank_answers(path.read_text("utf-8")), "utf-8")
        return root
    # The transcript as the live card was raised: it ends on AI-DLC's 07:08:43 reply.
    return scene(tree, F.CHAT_ONLY_REPLY_INTENT, F.chat_only_reply_rows()[:-2])


@at("2026-11-02T07:08:51Z")
def test_the_intent_capture_run_shows_the_files_questions_while_they_are_open(sv, routes, fake_host, scene):
    """The record with the file's tags blank, as they were until 07:08:08: an open file is the card."""
    built = chat_only(scene, open_file=True)
    [card] = cards(sv, routes, fake_host, built)
    assert_the_files_questions(card, CHAT_ONLY_QUESTIONS, CHAT_ONLY_PROMPTS)


@at("2026-11-02T07:08:51Z")
def test_the_intent_capture_run_raises_no_card_for_its_batch_decision(sv, routes, fake_host, scene):
    """07:08:51: the file was answered at 07:08:08 and "Chat" recorded at 07:08:23, both before the decision."""
    built = chat_only(scene, open_file=False)
    [card] = cards(sv, routes, fake_host, built)
    assert (card["type"], card["headline"]["params"]["reason"]) == ("missing_input", "conversation_reply")
    assert card["evidence"]["conversation_reply"]["text"].startswith("Your answer didn't reach AI-DLC.")
    assert not card["evidence"]["questions"].get("origin")
