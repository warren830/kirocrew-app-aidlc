"""What a plan will cost (§1.15): rule-band predictions, each labelled with its source; exact counts live in
``plan.py``. ``credits`` is never invented (FR-EST-005) and elapsed time is always an ``assumption``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Sequence

from .wire import WireRecord

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .plan import PlanStage

UNIT_TURNS, UNIT_SECS = "turns", "secs"
SOURCE_RULE_BAND, SOURCE_ASSUMPTION = "rule_band", "assumption"
CONFIDENCE_LOW = "low"
CREDITS_STATUS = "unavailable"

#: Conversation turns per stage class at ``Standard`` depth (§1.15 table, verbatim). Recalibrated 2026-10-13 against
#: the click runs on AI-DLC 2.10/2.11: a stage takes its gate and at most a round of questions or a plan approval
#: (express ran 7 stages in 7 turns, bugfix 9 in about as many); the initialization stages ride the first turn.
RULE_BANDS: dict[str, tuple[int, int]] = {
    "initialization": (0, 1), "ideation_inline": (1, 3), "inception_inline": (1, 3),
    "inception_pipeline": (1, 2), "inception_mob": (2, 4), "construction_per_unit": (1, 3),
    "code_generation": (2, 4), "build_and_test": (1, 3), "ci_pipeline": (1, 3), "operation": (1, 3),
}
STAGE_CLASSES = tuple(RULE_BANDS)
PER_UNIT_CLASSES = frozenset({"construction_per_unit", "code_generation"})
#: An unknown depth prices as ``Standard``: the estimate is advisory and must not hide the counts beside it.
DEPTH_MULTIPLIERS: dict[str, float] = {"Minimal": 0.7, "Standard": 1.0, "Comprehensive": 1.4}
DEFAULT_DEPTH = "Standard"
REVIEW_UPLIFT: dict[str, float] = {"none": 0.0, "advisory": 0.3, "adversarial": 0.6}
SUMMARY_CONFIRMATION_REQUIRED = "required"
SUMMARY_CONFIRMATION_TURNS = 1
#: One turn ran 3 to 10 minutes in those runs; elapsed time adds the person's own pace at each gate.
SECS_PER_TURN_LOW, SECS_PER_TURN_HIGH = 180, 720
ELAPSED_FACTOR_LOW, ELAPSED_FACTOR_HIGH = 1, 6


@dataclass(frozen=True, slots=True)
class Range(WireRecord):
    """A bounded prediction that knows where it came from (``source``/``confidence`` per metric, P26.4)."""

    low: int
    high: int
    unit: str
    source: str
    confidence: str


@dataclass(frozen=True, slots=True)
class DominantStage(WireRecord):
    slug: str
    share_pct: int
    turns: Range


@dataclass(frozen=True, slots=True)
class CoverageLoss(WireRecord):
    """What a disabled stage would have produced: the honest counterweight to a cheaper estimate."""

    slug: str
    artifacts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Estimate(WireRecord):
    """The whole prediction; the top-level ``source``/``confidence``/``samples`` mirror ``turns`` (§1.15).
    ``samples`` stays on the wire and is always 0: there is no calibration history any more."""

    turns: Range
    active_secs: Range
    elapsed_secs: Range | None
    credits: None
    credits_status: str
    source: str
    confidence: str
    samples: int
    dominant: tuple[DominantStage, ...]
    coverage_lost: tuple[CoverageLoss, ...]


#: Slug rules win over mode: ``code-generation`` is also ``mode: subagent`` and would price as a mob stage.
_SLUG_CLASSES = {"code-generation": "code_generation", "build-and-test": "build_and_test",
                 "ci-pipeline": "ci_pipeline", "reverse-engineering": "inception_pipeline"}
_MOB_MODES = frozenset({"mob", "subagent"})
_PHASE_CLASSES = {"initialization": "initialization", "ideation": "ideation_inline",
                  "inception": "inception_inline", "construction": "construction_per_unit",
                  "operation": "operation"}


def classify_stage(stage: "PlanStage") -> str:
    """The rule-band class of one stage (§1.15); the order of the checks is load-bearing."""
    phase = (stage.phase or "").strip().lower()
    if phase == "initialization":
        return "initialization"
    if stage.slug in _SLUG_CLASSES:
        return _SLUG_CLASSES[stage.slug]
    mode = (getattr(stage, "mode", "") or "").strip().lower()
    if mode == "pipeline":
        return "inception_pipeline"
    if phase == "ideation":
        return "ideation_inline"
    if phase == "inception" and mode in _MOB_MODES:
        return "inception_mob"
    if stage.per_unit:
        return "construction_per_unit"
    return _PHASE_CLASSES.get(phase, "inception_inline")


class EstimateService:
    """Rule bands and the refusal to invent a credit number. Sync and pure."""

    def estimate(
        self, stages: Sequence["PlanStage"], *, depth: str, units: int | None, scope: str
    ) -> Estimate:
        """Bound the WHOLE plan: disabled rows feed ``coverage_lost``, and ``units=None`` prices one unit."""
        unit_count = max(1, int(units or 1))
        multiplier = DEPTH_MULTIPLIERS.get(depth, DEPTH_MULTIPLIERS[DEFAULT_DEPTH])
        rows: list[tuple[str, Range]] = []
        for stage in (s for s in stages if s.enabled):
            rows.append((stage.slug, _stage_turns(stage, classify_stage(stage), multiplier, unit_count)))
        total = Range(sum(r.low for _, r in rows), sum(r.high for _, r in rows), UNIT_TURNS,
                      SOURCE_RULE_BAND, CONFIDENCE_LOW)
        active = Range(total.low * SECS_PER_TURN_LOW, total.high * SECS_PER_TURN_HIGH, UNIT_SECS,
                       SOURCE_RULE_BAND, CONFIDENCE_LOW)
        # An empty plan has nothing to wait for: `null`, not a 0–0 band that reads as "instant".
        elapsed = Range(active.low * ELAPSED_FACTOR_LOW, active.high * ELAPSED_FACTOR_HIGH, UNIT_SECS,
                        SOURCE_ASSUMPTION, CONFIDENCE_LOW) if rows else None
        ordered = sorted(rows, key=lambda r: (-r[1].high, r[0]))
        return Estimate(
            turns=total, active_secs=active, elapsed_secs=elapsed, credits=None, credits_status=CREDITS_STATUS,
            source=SOURCE_RULE_BAND, confidence=CONFIDENCE_LOW, samples=0,
            dominant=tuple(
                DominantStage(slug, int(round(turns.high * 100 / total.high)) if total.high > 0 else 0, turns)
                for slug, turns in ordered
            ),
            # `in_grid and not enabled` is exactly "the composition wanted this and the request does not".
            coverage_lost=tuple(
                CoverageLoss(s.slug, tuple(s.produces)) for s in stages if s.in_grid and not s.enabled
            ),
        )


def _stage_turns(stage: "PlanStage", stage_class: str, multiplier: float, units: int) -> Range:
    """One stage's turn band at ``depth``, scaled per unit and uplifted for review."""
    base_low, base_high = RULE_BANDS[stage_class]
    if stage_class in PER_UNIT_CLASSES:
        base_low, base_high = base_low * units, base_high * units
    factor = multiplier + REVIEW_UPLIFT.get(stage.review_class or "none", 0.0)
    # Ceiling, not rounding: an estimate that rounds work away reads as a promise it cannot keep.
    low, high = math.ceil(base_low * factor), math.ceil(base_high * factor)
    if (getattr(stage, "summary_confirmation", None) or "") == SUMMARY_CONFIRMATION_REQUIRED:
        low, high = low + SUMMARY_CONFIRMATION_TURNS, high + SUMMARY_CONFIRMATION_TURNS
    return Range(low, max(low, high), UNIT_TURNS, SOURCE_RULE_BAND, CONFIDENCE_LOW)
