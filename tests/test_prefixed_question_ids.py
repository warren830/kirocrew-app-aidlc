"""Questions numbered with a stage prefix (``## RMQ1.``), on the enterprise run of 2026-11-04.

From Refined Mockups on, the conductor named its questions after the stage, as it already named the earlier stages'
answers in prose (``FQ6``, ``SQ2``): ``## RMQ1. What should the message say when a typed due date is refused?`` with
options A, B and X and a blank ``[Answer]:``. The engine keys a question by its section and tag, not by the letters
before ``Q``. Studio only read ``## Q<n>``, so the section counted as an unsupported answer field: the card went to
degraded mode with no way to answer from Studio, said "There is nothing to decide here", and no reply card offered the
conversation's own choices either.
"""

from __future__ import annotations

import conftest as CT

from test_e2e_gate import QUESTIONS_REL, _queue, _record_dir, _rescan, scene  # noqa: F401
from test_handlers_auth import common, routes, sv  # noqa: F401
from test_summary_confirmation_switch import repo_dir  # noqa: F401

R = CT.studio_module("aidlc_reader")
A = CT.studio_module("actions")

RMQ1 = """# Refined Mockups Questions

## Already Settled, Not Asked Again

- Color: none.

## RMQ1. What should the message say when a typed due date is refused?

When a due date is refused, the tool prints a usage line, then one error line.

```
'2026-11-31' is not a real date; use YYYY-MM-DD, such as 2026-11-30
```

A. Say what is wrong: a day that does not exist, a wrong form, or an empty value each get their own words (Recommended)
B. One message for every refused value
X. Other (please specify)

[Answer]:
"""


def test_a_prefixed_question_is_a_question_with_its_own_name():
    found = R.parse_questions_file(RMQ1, "refined-mockups-questions.md", "0" * 64)
    assert (found.pending_count, found.unsupported_pending_count) == (1, 0)
    [question] = found.questions
    assert (question.index, question.label) == (1, "RMQ1")
    assert question.prompt == "What should the message say when a typed due date is refused?"
    assert [o.letter for o in question.options] == ["A", "B", "X"]
    assert "'2026-11-31' is not a real date" in question.context
    assert R.file_questions_support_forms(found)
    plain = R.parse_questions_file(RMQ1.replace("## RMQ1.", "## Q1."), "q.md", "0" * 64)
    assert plain.questions[0].label is None  # a plain Q<n> keeps its wire shape


def test_a_grouped_reply_names_each_question_as_the_file_does():
    text = RMQ1 + "\n## RMQ2. Should the message name the option?\n\nA. Yes\nB. One message for every refused value\nX. Other (please specify)\n\n[Answer]:\n"
    found = R.parse_questions_file(text, "refined-mockups-questions.md", "0" * 64)
    assert [q.label for q in found.questions] == ["RMQ1", "RMQ2"]
    payload = {"answers": [{"index": 1, "option_letters": ["A"]}, {"index": 2, "option_letters": ["B"]}]}
    wire = A.wire_text_for("answers", payload, questions=found, grouped_answers_enabled=True)
    assert wire.startswith("RMQ1: A. Say what is wrong:")
    assert "\nRMQ2: B. One message for every refused value" in wire
    # One question alone is sent as its answer, as before.
    single = A.wire_text_for("answers", {"answers": payload["answers"][:1]},
                             questions=R.parse_questions_file(RMQ1, "q.md", "0" * 64), grouped_answers_enabled=True)
    assert single.startswith("A. Say what is wrong:")


def test_the_card_offers_the_prefixed_question_as_a_form(sv, routes, fake_host, scene):
    path = _record_dir(scene.root) / QUESTIONS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RMQ1)
    _rescan(sv, routes, fake_host, scene)
    [card] = [c for c in _queue(sv, routes, fake_host) if c["type"] == "question"]
    assert [d["decision"] for d in card["decisions"]] == ["answers"]
    questions = card["evidence"]["questions"]
    assert (questions["pending_count"], questions["unsupported_pending_count"]) == (1, 0)
    assert questions["questions"][0]["label"] == "RMQ1"
