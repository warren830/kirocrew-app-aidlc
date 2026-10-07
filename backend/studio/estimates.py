"""What a plan will cost (§1.15): predictions, each labelled with its source; exact counts live in ``plan.py``.

``credits`` is never invented (FR-EST-005), a band is ``history_calibrated`` only from ``CALIBRATION_MIN_SAMPLES``
rows, and elapsed time is always an ``assumption``.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any, ClassVar, Iterable, Mapping, Sequence

from . import constants as C

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .plan import PlanStage
    from .storage import Storage

UNIT_TURNS, UNIT_SECS = "turns", "secs"
SOURCE_RULE_BAND, SOURCE_HISTORY, SOURCE_ASSUMPTION = "rule_band", "history_calibrated", "assumption"
CONFIDENCE_LOW, CONFIDENCE_MEDIUM = "low", "medium"
CREDITS_STATUS = "unavailable"

#: Turn bands per stage class at ``Standard`` depth (§1.15 table, verbatim).
RULE_BANDS: dict[str, tuple[int, int]] = {
    "initialization": (1, 2), "ideation_inline": (3, 6), "inception_inline": (4, 8),
    "inception_pipeline": (6, 12), "inception_mob": (5, 9), "construction_per_unit": (6, 12),
    "code_generation": (10, 20), "build_and_test": (6, 14), "ci_pipeline": (4, 8), "operation": (4, 8),
}
STAGE_CLASSES = tuple(RULE_BANDS)
PER_UNIT_CLASSES = frozenset({"construction_per_unit", "code_generation"})
#: An unknown depth prices as ``Standard``: the estimate is advisory and must not hide the counts beside it.
DEPTH_MULTIPLIERS: dict[str, float] = {"Minimal": 0.7, "Standard": 1.0, "Comprehensive": 1.4}
DEFAULT_DEPTH = "Standard"
REVIEW_UPLIFT: dict[str, float] = {"none": 0.0, "advisory": 0.3, "adversarial": 0.6}
SUMMARY_CONFIRMATION_REQUIRED = "required"
SUMMARY_CONFIRMATION_TURNS = 1
SECS_PER_TURN_LOW, SECS_PER_TURN_HIGH = 90, 300
ELAPSED_FACTOR_LOW, ELAPSED_FACTOR_HIGH = 3, 8
#: p10/p90 rather than min/max, so one pathological run cannot widen every future estimate.
CALIBRATION_LOW_PERCENTILE, CALIBRATION_HIGH_PERCENTILE = 0.10, 0.90
_CALIBRATION_TABLE = "calibration"


class Jsonable:
    """``to_json`` over a dataclass's fields in declaration order, minus ``_hidden``: nested objects serialise
    themselves, sequences become lists and mappings are copied."""

    __slots__ = ()
    _hidden: ClassVar[tuple[str, ...]] = ()

    def to_json(self) -> dict[str, Any]:
        return {f.name: _plain(getattr(self, f.name)) for f in fields(self) if f.name not in self._hidden}


def _plain(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Mapping):
        return dict(value)
    return value.to_json() if hasattr(value, "to_json") else value


@dataclass(frozen=True, slots=True)
class Range(Jsonable):
    """A bounded prediction that knows where it came from (``source``/``confidence`` per metric, P26.4)."""

    low: int
    high: int
    unit: str
    source: str
    confidence: str


@dataclass(frozen=True, slots=True)
class DominantStage(Jsonable):
    slug: str
    share_pct: int
    turns: Range


@dataclass(frozen=True, slots=True)
class CoverageLoss(Jsonable):
    """What a disabled stage would have produced: the honest counterweight to a cheaper estimate."""

    slug: str
    artifacts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Estimate(Jsonable):
    """The whole prediction; the top-level ``source``/``confidence``/``samples`` mirror ``turns`` (§1.15)."""

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


@dataclass(frozen=True, slots=True)
class _Cohort:
    """What history says about one ``(scope, depth, stage_class)``; ``turns is None`` means "not enough"."""

    samples: int
    turns: tuple[int, int] | None = None
    secs_per_turn: tuple[int, int] | None = None


class EstimateService:
    """Rule bands, local calibration and the refusal to invent a credit number. Sync: call off-thread."""

    def __init__(self, storage: "Storage", clock: "C.Clock") -> None:
        self._storage = storage
        self._clock = clock

    def estimate(
        self, stages: Sequence["PlanStage"], *, depth: str, units: int | None, scope: str
    ) -> Estimate:
        """Bound the WHOLE plan: disabled rows feed ``coverage_lost``, and ``units=None`` prices one unit."""
        unit_count = max(1, int(units or 1))
        multiplier = DEPTH_MULTIPLIERS.get(depth, DEPTH_MULTIPLIERS[DEFAULT_DEPTH])
        enabled = [s for s in stages if s.enabled]
        classes = [classify_stage(stage) for stage in enabled]
        history = self._history(scope, depth) if enabled else {}
        cohorts = {name: _cohort(history.get(name, [])) for name in set(classes)}
        rows: list[tuple[str, Range, int, int]] = []  # slug, turns, active_low, active_high
        for stage, stage_class in zip(enabled, classes):
            cohort = cohorts[stage_class]
            turns = _stage_turns(stage, stage_class, cohort, multiplier, unit_count)
            secs_low, secs_high = cohort.secs_per_turn or (SECS_PER_TURN_LOW, SECS_PER_TURN_HIGH)
            rows.append((stage.slug, turns, turns.low * secs_low, turns.high * secs_high))
        # Calibrated only when EVERY contributing class is: one guessed stage makes the total part guess.
        calibrated = bool(enabled) and all(c.turns is not None for c in cohorts.values())
        source = SOURCE_HISTORY if calibrated else SOURCE_RULE_BAND
        confidence = CONFIDENCE_MEDIUM if calibrated else CONFIDENCE_LOW
        total = Range(sum(r[1].low for r in rows), sum(r[1].high for r in rows), UNIT_TURNS, source, confidence)
        active = Range(sum(r[2] for r in rows), sum(r[3] for r in rows), UNIT_SECS, source, confidence)
        # An empty plan has nothing to wait for: `null`, not a 0–0 band that reads as "instant".
        elapsed = Range(active.low * ELAPSED_FACTOR_LOW, active.high * ELAPSED_FACTOR_HIGH, UNIT_SECS,
                        SOURCE_ASSUMPTION, CONFIDENCE_LOW) if rows else None
        ordered = sorted(rows, key=lambda r: (-r[1].high, r[0]))
        return Estimate(
            turns=total, active_secs=active, elapsed_secs=elapsed, credits=None, credits_status=CREDITS_STATUS,
            source=source, confidence=confidence, samples=sum(c.samples for c in cohorts.values()),
            dominant=tuple(
                DominantStage(slug, int(round(turns.high * 100 / total.high)) if total.high > 0 else 0, turns)
                for slug, turns, _low, _high in ordered
            ),
            # `in_grid and not enabled` is exactly "the composition wanted this and the request does not".
            coverage_lost=tuple(
                CoverageLoss(s.slug, tuple(s.produces)) for s in stages if s.in_grid and not s.enabled
            ),
        )

    def cohorts(self) -> list[dict[str, Any]]:
        """Every cohort with at least one sample, for ``GET /calibration``."""
        counts = Counter(
            (str(row.get("scope") or ""), str(row.get("depth") or ""), str(row.get("stage_class") or ""))
            for row in self._storage.select(_CALIBRATION_TABLE)
        )
        return [
            {"scope": scope, "depth": depth, "stage_class": stage_class, "samples": samples}
            for (scope, depth, stage_class), samples in sorted(counts.items())
        ]

    def clear(self) -> int:
        """Delete every sample; returns how many went. Backs ``POST /calibration/clear``."""
        ids = [row.get("id") for row in self._storage.select(_CALIBRATION_TABLE)]
        for row_id in ids:
            self._storage.delete(_CALIBRATION_TABLE, row_id)
        return len(ids)

    def _history(self, scope: str, depth: str) -> dict[str, list[dict[str, Any]]]:
        """This plan's calibration rows by stage class: one query instead of one per class."""
        out: dict[str, list[dict[str, Any]]] = {}
        for row in self._storage.select(_CALIBRATION_TABLE, {"scope": scope, "depth": depth}):
            out.setdefault(row.get("stage_class"), []).append(row)
        return out


def _cohort(rows: Sequence[Mapping[str, Any]]) -> _Cohort:
    """A cohort's samples as bands, or as "not enough evidence" (rows without usable turns are no evidence)."""
    turns: list[int] = []
    ratios: list[int] = []
    for row in rows:
        actual = _as_dict(row.get("actual_json"))
        observed = _non_negative_int(actual.get("turns"))
        if observed <= 0:
            continue
        turns.append(observed)
        secs = _non_negative_int(actual.get("active_secs"))
        if secs > 0:
            ratios.append(max(1, int(round(secs / observed))))
    if len(turns) < C.CALIBRATION_MIN_SAMPLES:
        return _Cohort(len(rows))
    return _Cohort(len(rows), _band(turns), _band(ratios) if len(ratios) >= C.CALIBRATION_MIN_SAMPLES else None)


def _stage_turns(stage: "PlanStage", stage_class: str, cohort: _Cohort, multiplier: float, units: int) -> Range:
    """One stage's turn band. A calibrated band is used verbatim: re-applying multipliers would double-count."""
    if cohort.turns is not None:
        low, high = cohort.turns
        return Range(max(0, low), max(low, high), UNIT_TURNS, SOURCE_HISTORY, CONFIDENCE_MEDIUM)
    base_low, base_high = RULE_BANDS[stage_class]
    if stage_class in PER_UNIT_CLASSES:
        base_low, base_high = base_low * units, base_high * units
    factor = multiplier + REVIEW_UPLIFT.get(stage.review_class or "none", 0.0)
    # Ceiling, not rounding: an estimate that rounds work away reads as a promise it cannot keep.
    low, high = math.ceil(base_low * factor), math.ceil(base_high * factor)
    if (getattr(stage, "summary_confirmation", None) or "") == SUMMARY_CONFIRMATION_REQUIRED:
        low, high = low + SUMMARY_CONFIRMATION_TURNS, high + SUMMARY_CONFIRMATION_TURNS
    return Range(low, max(low, high), UNIT_TURNS, SOURCE_RULE_BAND, CONFIDENCE_LOW)


def _band(values: Sequence[int]) -> tuple[int, int]:
    return _percentile(values, CALIBRATION_LOW_PERCENTILE), _percentile(values, CALIBRATION_HIGH_PERCENTILE)


def _percentile(values: Iterable[int], q: float) -> int:
    """Nearest-rank percentile: deterministic, so a golden test can predict it."""
    ordered = sorted(values)
    if not ordered:
        return 0
    index = int(round(q * (len(ordered) - 1)))
    return int(ordered[max(0, min(len(ordered) - 1, index))])


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _as_dict(value: Any) -> dict[str, Any]:
    """A ``*_json`` column that may arrive already decoded (``Storage``) or as text."""
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}
