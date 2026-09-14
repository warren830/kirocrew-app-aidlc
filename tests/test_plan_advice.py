"""Tests for ``backend/studio/plan_advice.py`` — the two pure functions behind a wizard plan draft.

``build_plan_questions`` renders the wizard's own questionnaire as the structured questions block the
advisor's parser already understands, and ``resolve_plan_proposal`` turns the letters the model answered
with back into a ``PlanRequest`` patch. Both are deterministic and neither reads a repository, which is
the property this file defends: a proposal is only ever built from the package's own vocabulary, so no
model prose is parsed as a value and an unknown scope or stage is unrepresentable (§1.4, FR-NEW-006).

The catalog here is a hand-built stand-in — ``SimpleNamespace`` stages, real ``ScopeMeta`` rows, a
grid dict — because these rules are about letters, rows and deltas, not about any shipped graph. The
integration through ``AdvisorBroker.poll`` lives in ``tests/test_advisor.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

REPO = "r_000000000001"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


# --------------------------------------------------------------------------- #
# module handles
# --------------------------------------------------------------------------- #


@pytest.fixture
def PA(studio):
    module = getattr(studio, "plan_advice", None)
    assert module is not None, getattr(studio, "plan_advice__error", "plan_advice.py did not import")
    return module


@pytest.fixture
def P(studio):
    return studio.plan


@pytest.fixture
def A(studio):
    return studio.advisor


@pytest.fixture
def E(studio):
    return studio.errors


# --------------------------------------------------------------------------- #
# a small catalog: three scopes, seven stages over four phases, one phase with nothing to choose
# --------------------------------------------------------------------------- #


def stage(slug: str, number: str, name: str, phase: str, execution: str = "CONDITIONAL") -> Any:
    return SimpleNamespace(slug=slug, number=number, name=name, phase=phase, execution=execution,
                           scopes=())


#: Graph order. ``initialization`` is all-ALWAYS (no question), ``operation`` has no stage (no question).
STAGES = (
    stage("workspace-detection", "0", "Workspace Detection", "initialization", "ALWAYS"),
    stage("intent-capture", "1", "Intent Capture", "ideation", "ALWAYS"),
    stage("market-research", "2", "Market Research", "ideation"),
    stage("requirements-analysis", "3", "Requirements Analysis", "inception", "ALWAYS"),
    stage("user-stories", "4", "User Stories", "inception"),
    stage("nfr-requirements", "5", "NFR Requirements", "inception"),
    stage("code-generation", "6", "Code Generation", "construction"),
)
ALWAYS = frozenset(node.slug for node in STAGES if node.execution == "ALWAYS")

#: ``odd`` is in the grid but has no scope file, and it skips a stage that always runs — the shape the
#: ALWAYS guard exists for. Values are mixed-case on purpose: ``grid_row`` compares upper-cased.
GRID: dict[str, dict[str, str]] = {
    "bugfix": {
        "workspace-detection": "EXECUTE", "intent-capture": "EXECUTE", "market-research": "SKIP",
        "requirements-analysis": "EXECUTE", "user-stories": "SKIP", "nfr-requirements": "SKIP",
        "code-generation": "EXECUTE",
    },
    "feature": {slug: "EXECUTE" for slug in (node.slug for node in STAGES)},
    "poc": {
        "workspace-detection": "EXECUTE", "intent-capture": "EXECUTE", "market-research": "SKIP",
        "requirements-analysis": "EXECUTE", "user-stories": "SKIP", "nfr-requirements": "SKIP",
        "code-generation": "EXECUTE",
    },
    "odd": {
        "workspace-detection": "EXECUTE", "intent-capture": "skip", "market-research": "skip",
        "requirements-analysis": "EXECUTE", "user-stories": "SKIP", "nfr-requirements": "SKIP",
        "code-generation": "EXECUTE",
    },
}


def grid_row(scope: str) -> dict[str, bool]:
    return {slug: value.upper() == "EXECUTE" for slug, value in GRID.get(scope, {}).items()}


def scope_meta(studio, name: str, description: str | None, **over: Any) -> Any:
    base = dict(name=name, depth="Standard", test_strategy=None, review_cap=None, skeleton=None,
                runner=None, keywords=(), description=description, plugin=None, project_owned=False)
    base.update(over)
    return studio.aidlc_reader.ScopeMeta(**base)


@pytest.fixture
def catalog(studio):
    scopes = (
        scope_meta(studio, "bugfix", "Fix one defect"),
        scope_meta(studio, "feature", None),
        scope_meta(studio, "poc", "Prove an idea", keywords=("spike", "prototype")),
    )
    return SimpleNamespace(scopes=scopes, stages=STAGES, grid=GRID, engine_version="2.6.2",
                           grid_row=grid_row)


def catalog_with(studio, *, scopes: int | None = None, construction_stages: int | None = None) -> Any:
    """The same catalog with one dimension inflated, for the letter and option ceilings."""
    scope_rows = tuple(scope_meta(studio, f"scope-{n:02d}", None) for n in range(scopes or 0)) or (
        scope_meta(studio, "poc", "Prove an idea"),
    )
    stages = list(STAGES)
    if construction_stages:
        stages = [node for node in STAGES if node.phase != "construction"] + [
            stage(f"build-{n:02d}", str(10 + n), f"Build {n}", "construction")
            for n in range(construction_stages)
        ]
    return SimpleNamespace(scopes=scope_rows, stages=tuple(stages), grid=GRID, engine_version=None,
                           grid_row=grid_row)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def rows_by_field(block: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["field"]: row for row in block["questions"]}


def answer(block: dict[str, Any], field: str, *tokens: str, letters: list[str] | None = None,
           text: str | None = None) -> dict[str, Any]:
    """One suggested answer, as the model would send it: letters by default, or exactly what is given."""
    row = rows_by_field(block)[field]
    if letters is None:
        letters = [LETTERS[row["values"].index(token)] for token in tokens]
    return {
        "question_index": row["index"],
        "option_letters": list(letters),
        "answer": ", ".join(tokens) if text is None else text,
    }


def result(A, *answers: dict[str, Any]) -> Any:
    return A.DraftResult(
        verdict=None,
        summary="A small change to an existing module.",
        suggested_answers=tuple(answers),
        evidence=("the objective names one module",),
        assumptions=(),
        alternatives=(),
        confidence="medium",
        needs_your_decision=(),
        drafted_feedback=None,
    )


def current(P, **body: Any) -> Any:
    return P.PlanRequest.from_json(body, repo_id=REPO)


def resolve(PA, block: dict[str, Any], res: Any, cur: Any) -> dict[str, Any]:
    return PA.resolve_plan_proposal(res, block, grid_row, ALWAYS, cur)


PROPOSAL_KEYS = {"scope", "depth", "test_strategy", "review_cap", "overrides", "unresolved", "base_scope"}


# --------------------------------------------------------------------------- #
# build_plan_questions
# --------------------------------------------------------------------------- #


def test_the_questionnaire_is_the_wizards_four_fields_then_one_row_per_phase_with_a_choice(PA, P, catalog):
    """Q1–Q4 in ``PLAN_FIELDS`` order, then one multi-select per lifecycle phase that has a stage the
    human could actually toggle. A phase whose stages all always run offers nothing to choose and a
    phase with no stage offers nothing at all, so neither becomes a question."""
    block = PA.build_plan_questions(catalog)
    assert block["mode"] == "structured" and block["source"] == "wizard"
    rows = block["questions"]
    assert [row["index"] for row in rows] == list(range(1, 8))
    assert [row["field"] for row in rows] == [
        "scope", "depth", "test_strategy", "review_cap",
        "stages:ideation", "stages:inception", "stages:construction",
    ]
    assert [row["multi"] for row in rows] == [False] * 4 + [True] * 3
    assert all(row["answered"] is False and row["answer"] is None for row in rows)
    assert all(option["is_other"] is False for row in rows for option in row["options"])
    assert all(
        [option["letter"] for option in row["options"]] == list(LETTERS[: len(row["values"])])
        for row in rows
    )
    assert PA.PLAN_FIELDS == ("scope", "depth", "test_strategy", "review_cap")
    assert PA.LETTERS == LETTERS

    by = rows_by_field(block)
    assert by["scope"]["values"] == ["bugfix", "feature", "poc"]
    assert [option["text"] for option in by["scope"]["options"]] == [
        "bugfix — Fix one defect",
        "feature — no description",
        "poc — Prove an idea",
    ]
    assert by["depth"]["values"] == list(P.DEPTHS)
    assert by["test_strategy"]["values"] == list(P.TEST_STRATEGIES)
    assert by["review_cap"]["values"] == list(P.REVIEW_CAPS)

    ideation = by["stages:ideation"]
    assert ideation["values"] == ["intent-capture", "market-research"]
    assert [option["text"] for option in ideation["options"]] == [
        "1 intent-capture — Intent Capture (cannot be turned off once selected)",
        "2 market-research — Market Research",
    ]
    assert ideation["prompt"].startswith("Stages to run in the ideation phase")
    assert "keeps that selection" in ideation["prompt"]
    assert by["stages:inception"]["values"] == ["requirements-analysis", "user-stories", "nfr-requirements"]
    assert by["stages:construction"]["values"] == ["code-generation"]


def test_a_row_never_offers_more_than_twenty_six_letters(PA, E, studio):
    """Letters are the whole answer vocabulary, so no row may run past ``Z``. Scopes are project-extensible
    (a repository can add its own), so the scope row is capped and the prompt says what was left out;
    a phase's stages come from the shipped graph, so a phase past 26 is a defect, not a menu."""
    twenty_six = PA.build_plan_questions(catalog_with(studio, scopes=26))
    scope_row = rows_by_field(twenty_six)["scope"]
    assert len(scope_row["values"]) == 26 and scope_row["options"][-1]["letter"] == "Z"
    assert "Only the first" not in scope_row["prompt"]

    capped = rows_by_field(PA.build_plan_questions(catalog_with(studio, scopes=27)))["scope"]
    assert len(capped["values"]) == 26 and capped["options"][-1]["letter"] == "Z"
    assert "Only the first 26 of 27 scopes are listed." in capped["prompt"]

    with pytest.raises(E.StudioError) as excinfo:
        PA.build_plan_questions(catalog_with(studio, construction_stages=27))
    assert excinfo.value.code == "internal_error"


def test_a_phase_row_is_never_shrunk_to_a_cards_option_ceiling(PA, studio):
    """``MAX_OPTIONS`` (12) belongs to card questions; a plan row must list every stage in the phase,
    because a stage the model cannot see is a stage it can never propose."""
    block = PA.build_plan_questions(catalog_with(studio, construction_stages=13))
    row = rows_by_field(block)["stages:construction"]
    assert len(row["values"]) == 13 and len(row["options"]) == 13
    assert row["options"][-1]["letter"] == "M"


def test_the_questionnaire_carries_the_cleaners_spellings_in_values_and_labels(PA, catalog):
    """The broker passes its redacting funnel as ``clean`` (§1.18a); every catalogue-authored string —
    scope name and description, stage slug, number and name — goes through it, so the ``values`` the
    resolver later matches are spelled exactly as ``plan.scopes``, ``plan_grid`` and ``plan_always``
    are. A real token is unchanged by the funnel; here a marker cleaner makes the pass visible."""
    block = PA.build_plan_questions(catalog, clean=lambda text: f"<{text}>")
    by = rows_by_field(block)
    assert by["scope"]["values"] == ["<bugfix>", "<feature>", "<poc>"]
    assert by["scope"]["options"][0]["text"] == "<bugfix> — <Fix one defect>"
    assert by["scope"]["options"][1]["text"] == "<feature> — no description"
    ideation = by["stages:ideation"]
    assert ideation["values"] == ["<intent-capture>", "<market-research>"]
    assert ideation["options"][1]["text"] == "<2> <market-research> — <Market Research>"
    # The fixed vocabularies are the engine's own words, not the catalogue's: untouched.
    assert by["depth"]["values"] == list(PA.DEPTHS)
    assert PA.build_plan_questions(catalog) == PA.build_plan_questions(catalog, clean=lambda t: t)


# --------------------------------------------------------------------------- #
# PlanDraftRequest — the body, the picks and the subject
# --------------------------------------------------------------------------- #


def test_the_space_must_be_a_legal_space_name(PA, E, studio):
    """The space names the directory the registry is read from and lands on the Activity row as a
    location, so a stray shape is refused as ``bad_body`` with the key named, the way every other route
    refuses it (§2.3); a missing or empty space is the default one."""
    K = studio.constants
    for space in ("../other", "a b", ".hidden", "x" * 200, "team/one"):
        with pytest.raises(E.StudioError) as excinfo:
            PA.PlanDraftRequest.from_json({"objective": "x", "space": space}, repo_id=REPO)
        assert excinfo.value.code == "bad_body", space
        assert excinfo.value.details == {"key": "space"}, space
    assert PA.PlanDraftRequest.from_json({"objective": "x"}, repo_id=REPO).space == K.DEFAULT_SPACE
    assert PA.PlanDraftRequest.from_json({"objective": "x", "space": ""}, repo_id=REPO).space == K.DEFAULT_SPACE
    assert PA.PlanDraftRequest.from_json({"objective": "x", "space": "team-1"}, repo_id=REPO).space == "team-1"


def test_the_picks_carry_the_six_settings_and_none_of_the_prose(PA):
    """``request_json["plan_current"]`` is the resolver's input and the Activity drawer's reading: the
    six settings only — never the objective, label or context ``PlanRequest.to_json`` would carry
    (the objective already lives in the package)."""
    req = PA.PlanDraftRequest.from_json(
        {"objective": "Export the ledger", "current": {
            "scope": "poc", "depth": "Minimal", "review_cap": "advisory",
            "overrides": {"user-stories": True},
            "objective": "Export the ledger", "label": "Ledger", "context": "Finance asked",
        }},
        repo_id=REPO,
    )
    assert req.picks() == {
        "space": "default", "scope": "poc", "depth": "Minimal", "test_strategy": None,
        "review_cap": "advisory", "overrides": {"user-stories": True},
    }
    for key in ("objective", "label", "context", "project_type", "repo_id"):
        assert key not in req.picks(), key


def test_the_subject_digests_the_picks_so_other_picks_are_another_request(PA):
    """Same objective, other picks → another ``picks_sha256``: the questionnaire the agent answers
    starts from the human's picks, so a live draft made against other picks is not the same request
    (§1.18a). The digest is over canonical JSON — override order does not change it."""
    def request(**current):
        return PA.PlanDraftRequest.from_json({"objective": "Export the ledger", "current": current},
                                             repo_id=REPO)

    base = request(scope="poc", overrides={"a-stage": True, "b-stage": False})
    subject = base.to_subject()
    assert set(subject) == {"repo_id", "space", "objective_sha256", "picks_sha256"}
    reordered = request(scope="poc", overrides={"b-stage": False, "a-stage": True})
    assert reordered.to_subject() == subject
    other_scope = request(scope="bugfix", overrides={"a-stage": True, "b-stage": False})
    assert other_scope.to_subject()["objective_sha256"] == subject["objective_sha256"]
    assert other_scope.to_subject()["picks_sha256"] != subject["picks_sha256"]
    other_toggle = request(scope="poc", overrides={"a-stage": True})
    assert other_toggle.to_subject()["picks_sha256"] != subject["picks_sha256"]


# --------------------------------------------------------------------------- #
# resolve_plan_proposal — letters to tokens
# --------------------------------------------------------------------------- #


def test_letters_resolve_into_tokens_and_a_self_contained_patch(PA, P, A, catalog):
    block = PA.build_plan_questions(catalog)
    res = result(
        A,
        answer(block, "scope", "poc"),
        answer(block, "depth", "Minimal"),
        answer(block, "test_strategy", "Comprehensive"),
        answer(block, "review_cap", "advisory"),
    )
    proposal = resolve(PA, block, res, current(P))
    assert set(proposal) == PROPOSAL_KEYS
    assert proposal == {
        "scope": "poc",
        "base_scope": "poc",
        "depth": "Minimal",
        "test_strategy": "Comprehensive",
        "review_cap": "advisory",
        "overrides": {},
        "unresolved": [],
    }


def test_a_scalar_field_takes_exactly_one_letter(PA, P, A, catalog):
    block = PA.build_plan_questions(catalog)
    res = result(A, answer(block, "scope", "poc"), answer(block, "depth", letters=["A", "B"]))
    proposal = resolve(PA, block, res, current(P))
    assert proposal["depth"] is None
    assert proposal["unresolved"] == [2]
    assert proposal["scope"] == "poc"


def test_a_label_copied_verbatim_resolves_without_a_letter(PA, P, A, catalog):
    """The request contract asks for the option label in ``answer``; a model that sent the label and no
    letter still resolves — by value, by the option's full text (then case-folded), or by the
    ``<token> — …`` head. Prose that is none of those is unresolved, never guessed."""
    block = PA.build_plan_questions(catalog)
    res = result(
        A,
        answer(block, "scope", letters=[], text="POC — PROVE AN IDEA"),
        answer(block, "depth", letters=[], text="Standard"),
        answer(block, "test_strategy", letters=[], text="Comprehensive — the model wrote its own reason"),
        answer(block, "review_cap", letters=[], text="somewhere in the middle"),
    )
    proposal = resolve(PA, block, res, current(P))
    assert proposal["scope"] == "poc"
    assert proposal["depth"] == "Standard"
    assert proposal["test_strategy"] == "Comprehensive"
    assert proposal["review_cap"] is None
    assert proposal["unresolved"] == [4]

    by_text = result(A, answer(block, "scope", letters=[], text="feature — no description"))
    assert resolve(PA, block, by_text, current(P))["scope"] == "feature"


def test_a_duplicate_question_index_is_unresolved_and_the_first_wins(PA, P, A, catalog):
    block = PA.build_plan_questions(catalog)
    res = result(A, answer(block, "scope", "poc"), answer(block, "depth", "Minimal"),
                 answer(block, "depth", "Comprehensive"))
    proposal = resolve(PA, block, res, current(P))
    assert proposal["depth"] == "Minimal"
    assert 2 in proposal["unresolved"]


def test_a_thirteen_letter_phase_answer_survives_the_parser_whole(PA, P, A, studio):
    """The parser once clipped ``option_letters`` to ``MAX_OPTIONS`` (12), the card-kind option ceiling;
    a plan phase row may offer up to 26, so a thirteen-stage answer lost its thirteenth letter and the
    resolver turned that stage OFF. ``MAX_OPTION_LETTERS`` is one per letter of the alphabet, and the
    answer resolves to all thirteen."""
    block = PA.build_plan_questions(catalog_with(studio, construction_stages=13))
    row = rows_by_field(block)["stages:construction"]
    assert len(row["values"]) == 13
    payload = {
        "verdict": None,
        "summary": "Build everything.",
        "suggested_answers": [{
            "question_index": row["index"],
            "answer": ", ".join(row["values"]),
            "option_letters": list(LETTERS[:13]),
        }],
        "evidence": [],
        "assumptions": [],
        "alternatives": [],
        "confidence": "medium",
        "needs_your_decision": [],
        "drafted_feedback": None,
    }
    parsed = A.parse_draft_result(
        "```json\n" + json.dumps(payload) + "\n```",
        kind="plan_draft",
        question_indices=tuple(r["index"] for r in block["questions"]),
    )
    assert A.MAX_OPTION_LETTERS == len(LETTERS) == 26
    assert parsed.suggested_answers[0]["option_letters"] == list(LETTERS[:13])
    # ``feature``'s row predates the thirteen build stages, so every one of them is a delta: 13 ON.
    proposal = resolve(PA, block, parsed, current(P, scope="feature"))
    assert proposal["overrides"] == {f"build-{n:02d}": True for n in range(13)}
    assert proposal["unresolved"] == []


# --------------------------------------------------------------------------- #
# resolve_plan_proposal — the scope row and the stage deltas
# --------------------------------------------------------------------------- #


def test_the_scope_is_always_set_and_falls_back_to_the_humans_pick(PA, P, A, catalog):
    """A patch with no scope would make the wizard re-base the deltas onto whatever it had, which may
    not be the scope the model saw. Unanswered, it is the human's current scope; both ``scope`` and
    ``base_scope`` say so."""
    block = PA.build_plan_questions(catalog)
    proposal = resolve(PA, block, result(A, answer(block, "depth", "Minimal")),
                       current(P, scope="bugfix"))
    assert proposal["scope"] == "bugfix" and proposal["base_scope"] == "bugfix"
    assert proposal["depth"] == "Minimal" and proposal["unresolved"] == []


def test_an_unknown_base_scope_leaves_every_stage_row_unresolved(PA, P, A, catalog):
    """No grid row means no selection to compute deltas against — and an override for a stage the
    engine will compose differently would be a proposal the human cannot read."""
    block = PA.build_plan_questions(catalog)
    picks = (answer(block, "stages:ideation", "intent-capture", "market-research"),
             answer(block, "stages:construction", "code-generation"))
    # A blank current scope is "no scope yet", so the patch says None rather than a scope called "".
    for scope, expected in (("", None), ("no-such-scope", "no-such-scope")):
        proposal = resolve(PA, block, result(A, *picks), current(P, scope=scope))
        assert proposal["scope"] == expected and proposal["base_scope"] == expected
        assert proposal["overrides"] == {}
        assert proposal["unresolved"] == [5, 7]


def test_no_scope_at_all_is_none_never_an_empty_name(PA, P, A, catalog):
    """Q1 unanswered and the human has not picked a scope either: ``scope`` and ``base_scope`` are
    ``None`` — never ``""``, which the wizard would take for a scope called nothing — and every ANSWERED
    stage row is unresolved, there being no selection to measure deltas against. An unanswered or
    empty phase row is simply absent, and the scalar picks still resolve."""
    block = PA.build_plan_questions(catalog)
    res = result(A, answer(block, "depth", "Minimal"),
                 answer(block, "stages:ideation", "market-research"),
                 answer(block, "stages:inception", letters=[], text=""))
    proposal = resolve(PA, block, res, current(P, scope=""))
    assert proposal["scope"] is None and proposal["base_scope"] is None
    assert proposal["depth"] == "Minimal"
    assert proposal["overrides"] == {}
    assert proposal["unresolved"] == [5]


def test_a_plan_draft_never_turns_off_a_stage_that_always_runs(PA, P, A, catalog):
    """Omitting ``intent-capture`` from the ideation list is not a request to skip it when the stage
    runs: the engine locks an ALWAYS stage once it is selected (``plan._base_lock``) and would refuse
    the override, so the resolver keeps it on. "Selected" is the base scope's row OR the human's own
    override — and an ALWAYS stage the scope skips is NOT forced on by an omission either."""
    block = PA.build_plan_questions(catalog)
    # poc runs intent-capture: the omission is a no-op, and market-research is the only delta.
    res = result(A, answer(block, "scope", "poc"), answer(block, "stages:ideation", "market-research"))
    proposal = resolve(PA, block, res, current(P))
    assert proposal["overrides"] == {"market-research": True}
    assert proposal["unresolved"] == []
    # odd's grid row does not run intent-capture and the human did not turn it on: there is nothing to
    # keep on, and the omission is not a request to force it on — the stage is simply not overridden.
    forced = resolve(PA, block, result(A, answer(block, "stages:ideation", "market-research")),
                     current(P, scope="odd"))
    assert forced["overrides"] == {"market-research": True}
    assert "intent-capture" not in forced["overrides"]
    # The human selected it themselves: the lock holds the same way, and the proposal keeps it on
    # rather than turning off a stage the engine would refuse to turn off.
    kept = resolve(PA, block, result(A, answer(block, "stages:ideation", "market-research")),
                   current(P, scope="odd", overrides={"intent-capture": True}))
    assert kept["overrides"] == {"intent-capture": True, "market-research": True}
    assert kept["unresolved"] == []


def test_an_empty_phase_answer_keeps_the_scopes_own_selection(PA, P, A, catalog):
    """"Answer ONLY to change the selection": no letters means the scope's row stands, and so does
    whatever the human had already toggled in that phase."""
    block = PA.build_plan_questions(catalog)
    cur = current(P, scope="poc", overrides={"user-stories": True})
    empty = result(A, answer(block, "scope", "poc"),
                   answer(block, "stages:inception", letters=[], text=""))
    proposal = resolve(PA, block, empty, cur)
    assert proposal["overrides"] == {"user-stories": True}
    assert proposal["unresolved"] == []
    unanswered = result(A, answer(block, "scope", "poc"))
    assert resolve(PA, block, unanswered, cur)["overrides"] == {"user-stories": True}


def test_a_bad_letter_leaves_the_whole_phase_unresolved_not_half_applied(PA, P, A, catalog):
    """A list with one letter past the row (or not a letter at all) is a list the model did not build
    from the menu, so none of it is applied — a half-applied stage list would silently skip stages.
    Resolved against ``feature``, which runs every stage: had the valid ``A`` alone been applied, the
    inception row would have turned user-stories and nfr-requirements OFF and the overrides would say
    so; against poc (which skips them already) a half-applied answer would have hidden as no delta."""
    block = PA.build_plan_questions(catalog)
    assert all(on for on in grid_row("feature").values())
    for letters in (["A", "Z"], ["a"], ["1"], ["A", ""]):
        res = result(A, answer(block, "scope", "feature"),
                     answer(block, "stages:inception", letters=letters, text="user-stories"))
        proposal = resolve(PA, block, res, current(P))
        assert proposal["overrides"] == {}, letters
        assert proposal["unresolved"] == [6], letters
        assert proposal["scope"] == "feature"


def test_a_proposal_for_the_current_scope_starts_from_the_humans_own_overrides(PA, P, A, catalog):
    """Same scope as the wizard shows: the human's toggles in phases the model left alone survive, and
    an answered phase is recomputed wholesale against the grid row."""
    block = PA.build_plan_questions(catalog)
    cur = current(P, scope="poc", overrides={"user-stories": True, "code-generation": False})
    res = result(A, answer(block, "scope", "poc"),
                 answer(block, "stages:ideation", "intent-capture", "market-research"))
    proposal = resolve(PA, block, res, cur)
    assert proposal["overrides"] == {
        "user-stories": True,            # inception unanswered: kept
        "code-generation": False,        # construction unanswered: kept
        "market-research": True,         # ideation answered: the one delta against poc's row
    }
    # Answer construction with exactly the row's selection and the human's off-toggle is recomputed away.
    recomputed = result(A, answer(block, "scope", "poc"),
                        answer(block, "stages:construction", "code-generation"))
    assert resolve(PA, block, recomputed, cur)["overrides"] == {"user-stories": True}


def test_a_proposal_for_another_scope_starts_from_that_scopes_selection(PA, P, A, catalog):
    """The same rule as ``WizardView.pickScope``: a new scope's matrix is that scope's own row, so a
    toggle made against poc is not carried onto bugfix."""
    block = PA.build_plan_questions(catalog)
    cur = current(P, scope="poc", overrides={"user-stories": True})
    proposal = resolve(PA, block, result(A, answer(block, "scope", "bugfix")), cur)
    assert proposal == {
        "scope": "bugfix", "base_scope": "bugfix", "depth": None, "test_strategy": None,
        "review_cap": None, "overrides": {}, "unresolved": [],
    }
    with_delta = result(A, answer(block, "scope", "bugfix"),
                        answer(block, "stages:inception", "requirements-analysis", "user-stories"))
    assert resolve(PA, block, with_delta, cur)["overrides"] == {"user-stories": True}


def test_a_no_op_override_is_dropped(PA, P, A, catalog):
    """Exactly like ``WizardView.toggleStage``: a stage set to what the row already says is no override,
    so the map the wizard receives is only the deltas — and the wizard replaces, never merges."""
    block = PA.build_plan_questions(catalog)
    cur = current(P, scope="poc", overrides={"market-research": True})
    res = result(A, answer(block, "scope", "poc"), answer(block, "stages:ideation", "intent-capture"))
    assert resolve(PA, block, res, cur)["overrides"] == {}
    exact_row = result(A, answer(block, "scope", "feature"),
                       answer(block, "stages:inception", "requirements-analysis", "user-stories",
                              "nfr-requirements"))
    assert resolve(PA, block, exact_row, current(P))["overrides"] == {}


def test_values_are_always_members_of_the_packages_own_vocabulary(PA, P, A, catalog):
    """Every token in the patch was offered by a row: the wizard can look each one up, and an unknown
    scope or slug cannot be smuggled in through ``answer`` text."""
    block = PA.build_plan_questions(catalog)
    res = result(
        A,
        answer(block, "scope", letters=[], text="enterprise"),
        answer(block, "depth", letters=[], text="Exhaustive"),
        answer(block, "stages:construction", letters=[], text="deploy-to-prod"),
    )
    proposal = resolve(PA, block, res, current(P, scope="poc"))
    assert proposal["scope"] == "poc" and proposal["depth"] is None
    assert proposal["overrides"] == {}
    assert proposal["unresolved"] == [1, 2]
    stage_slugs = {node.slug for node in STAGES}
    assert set(proposal["overrides"]) <= stage_slugs


# --------------------------------------------------------------------------- #
# stage needs — the dependency edges the package hands the agent
# --------------------------------------------------------------------------- #


def test_a_stage_lists_the_producers_of_what_it_requires(PA):
    """``plan.stages[*].needs`` (§1.18a). The first live plan draft proposed ``code-generation`` on its
    own and the engine refused the plan with ``dependency_missing: units-generation`` — the model had no
    way to know. A required consume maps to every producer of that artifact (including an optional
    producer, which is still a producer), ``requires_stage`` is included verbatim, an optional consume
    adds nothing, and a stage never needs itself.
    """
    consume = lambda artifact, required=True: SimpleNamespace(artifact=artifact, required=required, conditional_on=None)  # noqa: E731
    units = SimpleNamespace(slug="units-generation", produces=("units.md",), optional_produces=(), consumes=(), requires_stage=())
    design = SimpleNamespace(slug="domain-design", produces=(), optional_produces=("domain.md",), consumes=(), requires_stage=())
    code = SimpleNamespace(
        slug="code-generation",
        produces=("src",),
        optional_produces=(),
        consumes=(consume("units.md"), consume("domain.md"), consume("nfr.md", required=False), consume("src")),
        requires_stage=("build-plan",),
    )
    needs = PA.stage_needs((units, design, code))
    assert needs["code-generation"] == ("build-plan", "domain-design", "units-generation")
    assert needs["units-generation"] == () and needs["domain-design"] == ()
