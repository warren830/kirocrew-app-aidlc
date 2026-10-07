"""The wizard's questionnaire as advisor questions, and the advisor's letters back as a plan patch (§1.18a).

A ``plan_draft`` has no card, so rather than teach the agent a second answer format the wizard's fields are
rendered as the structured questions it already answers (one letter per question, several per phase), and
``resolve_plan_proposal`` maps the letters back deterministically: a letter names a token the package listed or
the answer is unresolved, so no model prose is ever parsed as a value and PlanService stays the only authority.
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

PLAN_DRAFT_KIND = "plan_draft"
#: The four scalar settings, in question order: question ``i + 1`` asks for ``PLAN_FIELDS[i]``.
PLAN_FIELDS: tuple[str, ...] = ("scope", "depth", "test_strategy", "review_cap")
#: Option letters; 26 is also the hard cap on options per question.
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
#: ``advisor.MAX_OPTIONS`` (not imported: ``advisor`` imports this module); past it the phase prompt says so.
PHASE_OPTIONS_NOTE_AT = 12
#: What ``execution: ALWAYS`` means to a plan: the lock holds only once a scope selects the stage.
ALWAYS_LABEL = "cannot be turned off once selected"
_LABEL_SEP = " — "
_STAGE_FIELD_PREFIX = "stages:"
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
#: Q2–Q4: the fixed vocabularies, in ``PLAN_FIELDS`` order after the scope.
_SETTING_QUESTIONS = (
    ("How deep should the workflow go?", DEPTHS, _DEPTH_TEXT),
    ("How much testing should the workflow produce?", TEST_STRATEGIES, _TEST_STRATEGY_TEXT),
    ("How much review should the workflow run? A cap can only ever reduce review work.", REVIEW_CAPS,
     _REVIEW_CAP_TEXT),
)


@dataclass(frozen=True, slots=True)
class PlanDraftRequest:
    """``POST /repos/{repo_id}/intents/plan/advise`` as the broker sees it. No ``auto``: only a click asks."""

    repo_id: str
    space: str
    objective: str
    context: str | None
    project_type: str | None
    #: Validated later by the broker against ``ADVISOR_LOCALES``.
    locale: str
    #: The human's picks so far: the proposal is a patch against them.
    current: PlanRequest

    @classmethod
    def from_json(cls, body: Mapping[str, Any] | None, *, repo_id: str) -> "PlanDraftRequest":
        """Refuse shapes rather than coerce them; a whitespace-only objective is the missing objective."""
        data = dict(body or {})
        objective = _opt_str(data.get("objective"))
        if objective is None:
            raise StudioError("bad_body", "an objective is required", details={"missing": ["objective"]})
        current_raw = data.get("current")
        if current_raw is None:
            current_raw = {}
        if not isinstance(current_raw, Mapping):
            raise StudioError("bad_body", "current must be an object", details={"key": "current"})
        space = _opt_str(data.get("space")) or C.DEFAULT_SPACE
        if not C.SPACE_RE.match(space):
            raise StudioError("bad_body", "space is not a legal space name", details={"key": "space"})
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
        """The six settings the resolver needs — never the objective, label or context (the row is read back)."""
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
        """What the draft is about. The objective and picks travel as digests: together they are the
        idempotency key, and a draft made against other picks would propose deltas the wizard cannot read."""
        picks = json.dumps(self.picks(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return {
            "repo_id": self.repo_id,
            "space": self.space,
            "objective_sha256": security.sha256_text(self.objective.strip()),
            "picks_sha256": security.sha256_text(picks),
        }


def _row(
    index: int, prompt: str, field: str, values: list[str], labels: list[str], *, multi: bool
) -> dict[str, Any]:
    if len(values) > len(LETTERS):
        raise StudioError("internal_error", "a plan question would need more options than there are letters",
                          details={"field": field, "options": len(values), "letters": len(LETTERS)})
    return {
        "index": index,
        "prompt": prompt,
        "multi": multi,
        "field": field,
        "values": list(values),
        "options": [{"letter": LETTERS[position], "text": text, "is_other": False}
                    for position, text in enumerate(labels)],
        "answered": False,
        "answer": None,
    }


def build_plan_questions(
    catalog: "PlanCatalog", *, clean: Callable[[str], str] = lambda text: text
) -> dict[str, Any]:
    """Q1 scope, Q2–Q4 depth/test strategy/review cap, then one multi-select per phase (``C.PHASES`` order) that
    has a stage to toggle. ``values`` holds the tokens the letters stand for, so no label is ever parsed.

    ``clean`` (the broker's redacting funnel) is applied to every catalogue string, so ``values`` carry the same
    spellings as ``plan.scopes``, ``plan_grid`` and ``plan_always``. These rows are never shrunk: they are the
    request.
    """
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
    labels = [f"{name}{_LABEL_SEP}{descriptions.get(name) or 'no description'}" for name in names]
    rows = [_row(1, prompt, PLAN_FIELDS[0], names, labels, multi=False)]
    for field, (prompt, tokens, texts) in zip(PLAN_FIELDS[1:], _SETTING_QUESTIONS):
        rows.append(_row(len(rows) + 1, prompt, field, list(tokens),
                         [f"{token}{_LABEL_SEP}{texts.get(token, token)}" for token in tokens], multi=False))
    for phase in C.PHASES:
        stages = [node for node in catalog.stages if node.phase == phase]
        if not stages or all(node.execution == "ALWAYS" for node in stages):
            continue
        prompt = (
            f"Stages to run in the {phase} phase. Answer ONLY to change the proposed scope's own "
            "selection; an unanswered or empty answer keeps that selection. List EVERY stage that "
            "should run in this phase."
        )
        if len(stages) > PHASE_OPTIONS_NOTE_AT:
            prompt += f" This phase has {len(stages)} stages."
        labels = [
            f"{clean(str(node.number))} {clean(str(node.slug))}{_LABEL_SEP}{clean(str(node.name))}"
            + (f" ({ALWAYS_LABEL})" if node.execution == "ALWAYS" else "")
            for node in stages
        ]
        rows.append(_row(len(rows) + 1, prompt, f"{_STAGE_FIELD_PREFIX}{phase}",
                         [clean(str(node.slug)) for node in stages], labels, multi=True))
    return {"mode": "structured", "source": "wizard", "questions": rows}


def stage_needs(stages: "Collection[Any]") -> dict[str, tuple[str, ...]]:
    """Per stage, the producers of its required ``consumes`` plus ``requires_stage`` (``plan.stages[*].needs``).

    Without it the model cannot know ``code-generation`` also needs ``units-generation``, and the engine refuses
    the very proposal it was asked for.
    """
    producers: dict[str, set[str]] = {}
    for node in stages:
        for artifact in (*(getattr(node, "produces", ()) or ()), *(getattr(node, "optional_produces", ()) or ())):
            producers.setdefault(str(artifact), set()).add(str(node.slug))
    out: dict[str, tuple[str, ...]] = {}
    for node in stages:
        needs: set[str] = {str(slug) for slug in (getattr(node, "requires_stage", ()) or ())}
        for consume in getattr(node, "consumes", ()) or ():
            if getattr(consume, "required", True) is not False:
                needs |= producers.get(str(getattr(consume, "artifact", "")), set())
        needs.discard(str(node.slug))
        out[str(node.slug)] = tuple(sorted(needs))
    return out


def _letters_to_tokens(row: Mapping[str, Any], letters: Any) -> list[str] | None:
    """The tokens the letters name, or ``None`` if ANY letter is invalid: half an answer is worse than none."""
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
    """A free-text answer that copied the label: exact, then case-folded, then the ``<token> — …`` head."""
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
    return head if head in values else None


def resolve_plan_proposal(
    result: "DraftResult",
    questions: Mapping[str, Any],
    catalog_grid_row: Callable[[str], dict[str, bool]],
    always: Collection[str],
    current: PlanRequest,
) -> dict[str, Any]:
    """Letters → tokens → a SELF-CONTAINED ``PlanRequest`` patch; the wizard replaces, never merges.

    ``scope``/``base_scope`` are the proposed scope, else the human's, else ``None`` (never ``""``). ``overrides``
    is the complete map for that scope: the human's own overrides when it is their scope, else nothing, with each
    answered phase rewritten as deltas against the base scope's grid row (a no-op override is dropped, and an
    always-run stage the scope or the human selected is never turned off).
    """
    proposal: dict[str, Any] = {"scope": None, "depth": None, "test_strategy": None, "review_cap": None,
                                "overrides": {}, "unresolved": [], "base_scope": None}
    unresolved: set[int] = set()
    rows: dict[int, Mapping[str, Any]] = {}
    for row in questions.get("questions") or ():
        if isinstance(row, Mapping) and isinstance(row.get("index"), int):
            rows.setdefault(int(row["index"]), row)
    # First answer per index wins; a duplicate is recorded as unresolved, not silently overridden.
    answers: dict[int, Mapping[str, Any]] = {}
    for answer in sorted((a for a in result.suggested_answers if isinstance(a.get("question_index"), int)),
                         key=lambda a: int(a["question_index"])):
        index = int(answer["question_index"])
        if index in answers:
            unresolved.add(index)
        else:
            answers[index] = answer

    for index, answer in answers.items():  # Q1–Q4: one token each
        row = rows.get(index)
        if row is None:
            unresolved.add(index)
            continue
        field = str(row.get("field") or "")
        if field not in PLAN_FIELDS:
            continue
        tokens = _letters_to_tokens(row, answer.get("option_letters"))
        if tokens == []:  # no letters: the label copied verbatim is the answer
            token = _token_from_text(row, str(answer.get("answer") or ""))
        else:
            token = tokens[0] if tokens is not None and len(tokens) == 1 else None
        if token is None:
            unresolved.add(index)
        else:
            proposal[field] = token

    # A blank current scope is "no scope yet": None keeps the wizard asking, and leaves no row to diff against.
    base: str | None = proposal["scope"] if proposal["scope"] is not None else (current.scope or None)
    proposal["scope"] = proposal["base_scope"] = base
    overrides: dict[str, bool] = (
        {str(slug): bool(on) for slug, on in current.overrides.items()}
        if base is not None and base == current.scope else {}
    )
    grid_row = (catalog_grid_row(base) or {}) if base is not None else {}
    always_set = {str(slug) for slug in always}

    for index, answer in answers.items():  # stages:<phase>: deltas against the base scope's own selection
        row = rows.get(index)
        if row is None or not str(row.get("field") or "").startswith(_STAGE_FIELD_PREFIX):
            continue
        letters = answer.get("option_letters") or []
        if not letters:
            continue  # an empty answer keeps the selection as it stands
        tokens = _letters_to_tokens(row, letters) if grid_row else None
        if tokens is None:
            unresolved.add(index)  # a bad letter, or a scope with no selection to measure against
            continue
        chosen = set(tokens)
        for slug in (str(value) for value in row.get("values") or ()):
            base_on = grid_row.get(slug, False)
            # The engine locks an ALWAYS stage only while selected (`plan._base_lock`): keep one the scope or the
            # human selected on, but do not force on one the scope skips.
            want = slug in chosen or (slug in always_set and (base_on or overrides.get(slug) is True))
            if want == base_on:
                overrides.pop(slug, None)
            else:
                overrides[slug] = want
    proposal["overrides"] = overrides
    proposal["unresolved"] = sorted(unresolved)
    return proposal
