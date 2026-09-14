"""The wizard's questionnaire as advisor questions, and the advisor's letters as a plan patch (§1.18a).

A ``plan_draft`` is the one Advisor kind with no card: the human is still in the new-intent wizard
and nothing exists yet. Rather than teach the agent a second answer format, the wizard's own fields
(scope, depth, test strategy, review cap, then the stage set of each lifecycle phase) are rendered as
the *structured questions* the agent already knows how to answer — one option letter per question,
several for a multi-select phase — and the answer travels in the unchanged nine-key ``DraftResult``
through ``suggested_answers``. This module is the two pure ends of that round trip:

- ``build_plan_questions`` turns a ``PlanCatalog`` into the questions block;
- ``resolve_plan_proposal`` turns the letters the agent chose back into a *self-contained*
  ``PlanRequest`` patch, deterministically, with the stage picks already expressed as override deltas
  against the proposed scope's own grid row.

Pure functions on purpose: no I/O, no clock, no model prose ever parsed as a value. A letter either
names a token the package itself listed or the whole answer is recorded as unresolved — an unknown
scope or stage slug is unrepresentable, so PlanService stays the only authority on validity
(FR-PLAN-004/005) and FR-NEW-004 stays literally true: the proposal is only ever values the human
accepts into the wizard's own fields.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Collection, Mapping

from . import constants as C
from . import security
from .errors import StudioError
from .plan import DEPTHS, REVIEW_CAPS, TEST_STRATEGIES, PlanRequest, _opt_str

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .advisor import DraftResult
    from .plan import PlanCatalog

__all__ = [
    "LETTERS",
    "PLAN_DRAFT_KIND",
    "PLAN_FIELDS",
    "PlanDraftRequest",
    "build_plan_questions",
    "stage_needs",
    "ALWAYS_LABEL",
    "resolve_plan_proposal",
]

# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #

PLAN_DRAFT_KIND = "plan_draft"
#: The four scalar settings, in question order: question ``i + 1`` asks for ``PLAN_FIELDS[i]``.
PLAN_FIELDS: tuple[str, ...] = ("scope", "depth", "test_strategy", "review_cap")
#: Option letters, in order. Twenty-six is also the hard cap on options per question: a row that
#: would need a twenty-seventh letter is a defect in the catalogue, not something to paginate.
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
#: Mirrors ``advisor.MAX_OPTIONS``: past this many stages in one phase the prompt says so, because the
#: card-kind questions the agent is otherwise shown never exceed it. No shipped graph does (the largest
#: phase has nine); spelled here rather than imported because ``advisor`` imports this module.
PHASE_OPTIONS_NOTE_AT = 12

#: What an ``execution: ALWAYS`` stage means to the plan, said the way the engine applies it: the lock
#: holds only once a scope selects the stage (``plan._base_lock``); a scope grid may still leave it out.
#: The first live draft read a bare "(always runs)" as "already in every plan" and proposed against it.
ALWAYS_LABEL = "cannot be turned off once selected"
#: The separator between a token and its explanation in an option label, ``"<token> — <text>"``. The
#: resolver's text fallback splits on it when the agent copied a whole label instead of a letter.
_LABEL_SEP = " — "
_STAGE_FIELD_PREFIX = "stages:"

#: One-line explanations for the fixed vocabularies, so an option reads like a choice and not a bare
#: token. Claims are the engine's (``plan.py``: a cap may only ever *reduce* review work; depth and
#: test strategy pick how much of the scope's work runs), and none of them names a stage.
_DEPTH_TEXT = {
    "Minimal": "the lightest pass the scope allows; for small, well-understood changes",
    "Standard": "the scope's own default depth",
    "Comprehensive": "the most thorough pass; for large, novel or risky work",
}
_TEST_STRATEGY_TEXT = {
    "Minimal": "the least testing the scope allows",
    "Standard": "the scope's own default test coverage",
    "Comprehensive": "the fullest test coverage the workflow can produce",
}
_REVIEW_CAP_TEXT = {
    "none": "no review stage runs at all",
    "advisory": "reviews run but are capped at advisory — they comment, they never block",
    "adversarial": "no cap — every stage keeps its own review class",
}


# --------------------------------------------------------------------------- #
# request
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PlanDraftRequest:
    """``POST /repos/{repo_id}/intents/plan/advise`` as the broker sees it (§2.3, §1.18a).

    There is deliberately no ``auto``: a plan draft is only ever the human's click, and the broker
    builds the ``DraftRequest(auto=False)`` itself, so a body claiming otherwise has nothing to set.
    """

    repo_id: str
    space: str
    objective: str
    context: str | None
    project_type: str | None
    #: Validated later, by the broker, against ``ADVISOR_LOCALES`` — this module never imports advisor.
    locale: str
    #: The human's picks so far. The proposal is a patch *against* these: an unresolved depth keeps the
    #: human's depth, and the stage deltas start from the human's own overrides when the proposed scope
    #: is the one they already picked.
    current: PlanRequest

    @classmethod
    def from_json(cls, body: Mapping[str, Any] | None, *, repo_id: str) -> "PlanDraftRequest":
        """Refuse shapes rather than coerce them, like ``PlanRequest.from_json``.

        ``read_json(required=("objective",))`` only catches ``None`` and ``""``; a whitespace-only
        objective is the same missing objective and is refused here with the same ``details``.
        """
        data = dict(body or {})
        objective = _opt_str(data.get("objective"))
        if objective is None:
            raise StudioError(
                "bad_body", "an objective is required", details={"missing": ["objective"]}
            )
        current_raw = data.get("current")
        if current_raw is None:
            current_raw = {}
        if not isinstance(current_raw, Mapping):
            raise StudioError("bad_body", "current must be an object", details={"key": "current"})
        space = _opt_str(data.get("space")) or C.DEFAULT_SPACE
        if not C.SPACE_RE.match(space):
            # The space names a directory the registry is read from and lands on the Activity row as a
            # location; a stray shape is refused here the way every other route refuses it (§2.3).
            raise StudioError(
                "bad_body", "space is not a legal space name", details={"key": "space"}
            )
        return cls(
            repo_id=repo_id,
            space=space,
            objective=objective,
            context=_opt_str(data.get("context")),
            project_type=_opt_str(data.get("project_type")),
            locale=_opt_str(data.get("locale")) or "",
            current=PlanRequest.from_json(current_raw, repo_id=repo_id),
        )

    @property
    def action_id(self) -> str:
        """The sentinel that stands in for a card id (§1.18a): ``plan:<repo_id>``."""
        return f"{C.ADVISOR_PLAN_ACTION_PREFIX}{self.repo_id}"

    def picks(self) -> dict[str, Any]:
        """The human's picks as the row stores them (``request_json["plan_current"]``, §1.18a).

        Only the six settings the resolver needs — never the objective, label or context, which
        ``PlanRequest.to_json`` would otherwise carry: the objective already lives in the package and
        the row is what the Activity drawer and a diagnostics bundle read back.
        """
        current = self.current
        return {
            "space": current.space,
            "scope": current.scope,
            "depth": current.depth,
            "test_strategy": current.test_strategy,
            "review_cap": current.review_cap,
            "overrides": {str(slug): bool(on) for slug, on in current.overrides.items()},
        }

    def to_subject(self) -> dict[str, Any]:
        """What the draft is about, for the row and its Activity entries. The objective travels as a
        digest: it is the idempotency key, and the text itself already lives in the package.

        ``picks_sha256`` digests the canonical JSON of :meth:`picks`, so a second click with the same
        objective but a different scope or override map is a *different* request — the questionnaire
        the agent answers starts from those picks, and handing back a draft made against other picks
        would propose deltas the wizard cannot read (§1.18a).
        """
        return {
            "repo_id": self.repo_id,
            "space": self.space,
            "objective_sha256": security.sha256_text(self.objective.strip()),
            "picks_sha256": security.sha256_text(_canonical_json(self.picks())),
        }


def _canonical_json(payload: Any) -> str:
    """One spelling per value: sorted keys, no whitespace, so equal picks digest equal."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --------------------------------------------------------------------------- #
# questionnaire → questions block
# --------------------------------------------------------------------------- #


def _row(
    index: int,
    prompt: str,
    field: str,
    values: list[str],
    labels: list[str],
    *,
    multi: bool,
) -> dict[str, Any]:
    if len(values) > len(LETTERS):
        raise StudioError(
            "internal_error",
            "a plan question would need more options than there are letters",
            details={"field": field, "options": len(values), "letters": len(LETTERS)},
        )
    return {
        "index": index,
        "prompt": prompt,
        "multi": multi,
        "field": field,
        "values": list(values),
        "options": [
            {"letter": LETTERS[position], "text": text, "is_other": False}
            for position, text in enumerate(labels)
        ],
        "answered": False,
        "answer": None,
    }


def build_plan_questions(
    catalog: "PlanCatalog", *, clean: Callable[[str], str] = lambda text: text
) -> dict[str, Any]:
    """The wizard's questionnaire as a structured questions block the existing parser already understands.

    Q1 scope, Q2 depth, Q3 test strategy, Q4 review cap, then one multi-select question per lifecycle
    phase (``C.PHASES`` order) listing that phase's stages in graph order. A phase with no stage, or
    whose every stage always runs, is skipped: there is nothing to choose. ``values`` holds the tokens
    the letters stand for, so the resolver never has to parse an option label. ``MAX_QUESTIONS`` does
    not apply here — it bounds ``_questions()`` for card kinds; a plan package is never shrunk on
    ``questions`` because they *are* the request.

    ``clean`` is applied to every catalogue-authored string that enters a row — scope names and
    descriptions, stage slugs, numbers and names — so the broker can pass its redacting funnel and the
    ``values`` the resolver later matches carry the *same* spellings as ``plan.scopes``, ``plan_grid``
    and ``plan_always`` (§1.18a). A real token is unchanged by it; the default is the identity.
    """
    rows: list[dict[str, Any]] = []

    names = [clean(str(meta.name)) for meta in catalog.scopes]
    descriptions = {
        clean(str(meta.name)): (clean(str(meta.description)) if meta.description else None)
        for meta in catalog.scopes
    }
    prompt = (
        "Which scope should this intent use? Choose the smallest scope whose description covers the "
        "objective; \"plan.scope_grid\" lists the stages each scope runs."
    )
    if len(names) > len(LETTERS):
        prompt += f" Only the first {len(LETTERS)} of {len(names)} scopes are listed."
        names = names[: len(LETTERS)]
    rows.append(
        _row(
            1,
            prompt,
            PLAN_FIELDS[0],
            names,
            [f"{name}{_LABEL_SEP}{descriptions.get(name) or 'no description'}" for name in names],
            multi=False,
        )
    )
    rows.append(
        _row(
            2,
            "How deep should the workflow go?",
            PLAN_FIELDS[1],
            list(DEPTHS),
            [f"{token}{_LABEL_SEP}{_DEPTH_TEXT.get(token, token)}" for token in DEPTHS],
            multi=False,
        )
    )
    rows.append(
        _row(
            3,
            "How much testing should the workflow produce?",
            PLAN_FIELDS[2],
            list(TEST_STRATEGIES),
            [f"{token}{_LABEL_SEP}{_TEST_STRATEGY_TEXT.get(token, token)}" for token in TEST_STRATEGIES],
            multi=False,
        )
    )
    rows.append(
        _row(
            4,
            "How much review should the workflow run? A cap can only ever reduce review work.",
            PLAN_FIELDS[3],
            list(REVIEW_CAPS),
            [f"{token}{_LABEL_SEP}{_REVIEW_CAP_TEXT.get(token, token)}" for token in REVIEW_CAPS],
            multi=False,
        )
    )

    index = len(rows)
    for phase in C.PHASES:
        stages = [node for node in catalog.stages if node.phase == phase]
        if not stages or all(node.execution == "ALWAYS" for node in stages):
            continue
        index += 1
        prompt = (
            f"Stages to run in the {phase} phase. Answer ONLY to change the proposed scope's own "
            "selection; an unanswered or empty answer keeps that selection. List EVERY stage that "
            "should run in this phase."
        )
        if len(stages) > PHASE_OPTIONS_NOTE_AT:
            prompt += f" This phase has {len(stages)} stages."
        rows.append(
            _row(
                index,
                prompt,
                f"{_STAGE_FIELD_PREFIX}{phase}",
                [clean(str(node.slug)) for node in stages],
                [
                    f"{clean(str(node.number))} {clean(str(node.slug))}{_LABEL_SEP}{clean(str(node.name))}"
                    + (f" ({ALWAYS_LABEL})" if node.execution == "ALWAYS" else "")
                    for node in stages
                ],
                multi=True,
            )
        )

    return {"mode": "structured", "source": "wizard", "questions": rows}



def stage_needs(stages: "Collection[Any]") -> dict[str, tuple[str, ...]]:
    """For every stage, the other stages whose products it requires (§1.18a ``plan.stages[*].needs``).

    Derived from the graph the same way ``PlanService.validate`` starves a plan: a stage needs the
    producers of each ``consumes`` entry marked ``required`` plus anything in ``requires_stage``. Without
    this list the model has no way to know that turning on ``code-generation`` also needs
    ``units-generation``, and the engine then refuses the very proposal it was asked for.
    """
    producers: dict[str, set[str]] = {}
    for node in stages:
        for artifact in tuple(getattr(node, "produces", ()) or ()) + tuple(
            getattr(node, "optional_produces", ()) or ()
        ):
            producers.setdefault(str(artifact), set()).add(str(node.slug))
    out: dict[str, tuple[str, ...]] = {}
    for node in stages:
        needs: set[str] = {str(slug) for slug in (getattr(node, "requires_stage", ()) or ())}
        for consume in getattr(node, "consumes", ()) or ():
            if getattr(consume, "required", True) is False:
                continue
            needs |= producers.get(str(getattr(consume, "artifact", "")), set())
        needs.discard(str(node.slug))
        out[str(node.slug)] = tuple(sorted(needs))
    return out

# --------------------------------------------------------------------------- #
# letters → plan patch
# --------------------------------------------------------------------------- #


def _letters_to_tokens(row: Mapping[str, Any], letters: Any) -> list[str] | None:
    """The tokens the letters name, or ``None`` when ANY letter is invalid — never a partial list.

    Half an answer is worse than none: "turn on A, C and Q" with Q out of range would otherwise become
    "turn on A and C", a stage set the agent did not propose.
    """
    values = [str(value) for value in row.get("values") or ()]
    out: list[str] = []
    for letter in letters or ():
        if not isinstance(letter, str) or len(letter) != 1 or letter not in LETTERS:
            return None
        position = LETTERS.index(letter)
        if position >= len(values):
            return None
        out.append(values[position])
    return out


def _token_from_text(row: Mapping[str, Any], text: str) -> str | None:
    """The fallback for a free-text answer with no letters: the option label copied verbatim.

    The request contract says ``"answer"`` is the option label copied verbatim, so a model that left
    ``option_letters`` empty but copied a label still resolves. Exact first, then case-folded, then the
    ``"<token> — …"`` head; anything else is unresolved, never guessed.
    """
    values = [str(value) for value in row.get("values") or ()]
    candidate = text.strip()
    if not candidate:
        return None
    if candidate in values:
        return candidate
    labels = [str(option.get("text") or "") for option in row.get("options") or ()]
    for position, label in enumerate(labels):
        if candidate == label and position < len(values):
            return values[position]
    folded = candidate.casefold()
    for value in values:
        if folded == value.casefold():
            return value
    for position, label in enumerate(labels):
        if folded == label.casefold() and position < len(values):
            return values[position]
    head = candidate.split(_LABEL_SEP, 1)[0].strip()
    if head in values:
        return head
    return None


def resolve_plan_proposal(
    result: "DraftResult",
    questions: Mapping[str, Any],
    catalog_grid_row: Callable[[str], dict[str, bool]],
    always: Collection[str],
    current: PlanRequest,
) -> dict[str, Any]:
    """Letters → tokens → a SELF-CONTAINED ``PlanRequest`` patch. Deterministic; no model prose is ever
    parsed as a value.

    The patch always carries ``scope`` and ``base_scope`` (the proposed scope, or the human's when Q1 did
    not resolve) so the wizard never re-bases the deltas onto a scope the model did not see; when Q1 did
    not resolve and the human has not picked a scope either, both are ``None`` — never ``""``, which the
    wizard would read as a scope called nothing — and every answered stage row is unresolved, there
    being no selection to measure against. ``overrides`` is the COMPLETE map the wizard should use with
    that scope: it starts from the human's own overrides when the base is the scope they already picked
    (else from nothing — a new scope's matrix starts from its own selection, the rule
    ``WizardView.pickScope`` applies), and every answered phase question rewrites that phase's slugs as
    deltas against the base scope's grid row, dropping a no-op override exactly like
    ``WizardView.toggleStage``. An always-run stage that the base scope runs, or that the human's own
    override turned on, is never turned off. The UI replaces, never merges.
    """
    proposal: dict[str, Any] = {
        "scope": None,
        "depth": None,
        "test_strategy": None,
        "review_cap": None,
        "overrides": {},
        "unresolved": [],
        "base_scope": None,
    }
    unresolved: set[int] = set()

    rows: dict[int, Mapping[str, Any]] = {}
    for row in questions.get("questions") or ():
        if isinstance(row, Mapping) and isinstance(row.get("index"), int):
            rows.setdefault(int(row["index"]), row)

    # First answer per index wins; a duplicate is recorded as unresolved, not silently overridden.
    answers: dict[int, Mapping[str, Any]] = {}
    for answer in sorted(
        (a for a in result.suggested_answers if isinstance(a.get("question_index"), int)),
        key=lambda a: int(a["question_index"]),
    ):
        index = int(answer["question_index"])
        if index in answers:
            unresolved.add(index)
            continue
        answers[index] = answer

    # ---- Q1–Q4: one token each ---- #
    for index, answer in answers.items():
        row = rows.get(index)
        if row is None:
            unresolved.add(index)
            continue
        field = str(row.get("field") or "")
        if field not in PLAN_FIELDS:
            continue
        tokens = _letters_to_tokens(row, answer.get("option_letters"))
        if tokens is None or len(tokens) > 1:
            unresolved.add(index)
            continue
        if len(tokens) == 1:
            proposal[field] = tokens[0]
            continue
        token = _token_from_text(row, str(answer.get("answer") or ""))
        if token is None:
            unresolved.add(index)
        else:
            proposal[field] = token

    # ---- the base scope and the override map it is measured against ---- #
    # A blank current scope is "no scope yet", not a scope: the patch says None so the wizard keeps
    # asking, and there is no row to compute stage deltas against.
    base: str | None = proposal["scope"] if proposal["scope"] is not None else (current.scope or None)
    proposal["scope"] = proposal["base_scope"] = base
    overrides: dict[str, bool] = (
        {str(slug): bool(on) for slug, on in current.overrides.items()}
        if base is not None and base == current.scope
        else {}
    )
    grid_row = (catalog_grid_row(base) or {}) if base is not None else {}
    always_set = {str(slug) for slug in always}

    # ---- stages:<phase>: deltas against the base scope's own selection ---- #
    for index, answer in answers.items():
        row = rows.get(index)
        if row is None or not str(row.get("field") or "").startswith(_STAGE_FIELD_PREFIX):
            continue
        letters = answer.get("option_letters") or []
        if not letters:
            continue  # an empty answer keeps the selection as it stands
        if not grid_row:
            unresolved.add(index)  # a scope with no selection to measure against
            continue
        tokens = _letters_to_tokens(row, letters)
        if tokens is None:
            unresolved.add(index)
            continue
        chosen = set(tokens)
        for slug in (str(value) for value in row.get("values") or ()):
            want = slug in chosen
            base_on = grid_row.get(slug, False)
            if slug in always_set and (base_on or overrides.get(slug) is True) and not want:
                # Never turn off a stage that always runs. "Runs" is the base scope's word, or the
                # human's, not the graph's alone: the stock bugfix/poc grids SKIP four ``execution:
                # ALWAYS`` stages and the engine locks an ALWAYS stage only while it is selected
                # (``plan._base_lock``), so an omitted ALWAYS stage the scope already skips stays skipped
                # rather than being forced on — but one the human's own override selected is locked the
                # same way the engine locks it, and the proposal keeps it on.
                want = True
            if want == base_on:
                overrides.pop(slug, None)
            else:
                overrides[slug] = want

    proposal["overrides"] = overrides
    proposal["unresolved"] = sorted(unresolved)
    return proposal
