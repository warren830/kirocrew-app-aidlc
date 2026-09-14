"""What a plan will cost, split into what Studio can count and what it can only bound (§1.15).

Two vocabularies, deliberately never mixed:

* **Exact counts** (stages, gates, artifacts, review intensity) are arithmetic over the installed stage
  graph and the scope grid. They live in ``plan.py`` because they are a property of the plan, not a
  prediction, and they are presented as numbers.
* **Ranges** (turns, active time, elapsed time) are predictions. Every one carries its own ``source``
  and ``confidence`` (review P26.4), because a band derived from ten local samples and a band derived
  from a table in this file are different claims and the UI must be able to say which it is showing.

Three rules exist to stop a prediction from being read as a promise:

* ``credits`` is always ``None`` with ``credits_status = "unavailable"`` (FR-EST-005). KiroCrew exposes
  no per-turn cost, so any number here would be invented — and an invented budget is the one estimate a
  user would act on financially.
* ``history_calibrated`` appears only when the cohort ``(scope, depth, stage_class)`` has at least
  ``CALIBRATION_MIN_SAMPLES`` rows. Below that the band is the labelled rule band: nine samples of one
  team's habits are noise, and presenting them as history would be a lie about the evidence.
* ``elapsed_secs`` is always ``source="assumption"``: wall-clock time depends on when the human comes
  back, which Studio cannot observe at all.

Calibration rows are content-free by construction (C17): a cohort key, integer measurements, and
``intent_ref = sha256(uuid)[:12]``. No intent name, no repository path, no prose ever reaches this table
— the estimator must be safe to export in a diagnostics bundle.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

from . import constants as C
from .errors import StudioError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .plan import PlanStage
    from .storage import Storage


# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #

#: ``Range.unit`` values (§1.15). Integer seconds and integer turns only — §0.2 forbids float durations
#: on the wire, and a fractional turn is not a thing a human can perform.
UNIT_TURNS = "turns"
UNIT_SECS = "secs"

#: ``Range.source`` values. ``assumption`` is weaker than ``rule_band``: a rule band was derived from
#: observed AI-DLC runs, an assumption is a spread this design chose because nothing observes it.
SOURCE_RULE_BAND = "rule_band"
SOURCE_HISTORY = "history_calibrated"
SOURCE_ASSUMPTION = "assumption"
RANGE_SOURCES = (SOURCE_RULE_BAND, SOURCE_HISTORY, SOURCE_ASSUMPTION)

CONFIDENCE_LOW = "low"
CONFIDENCE_MEDIUM = "medium"

#: FR-EST-005. Present as a field rather than as an absent key so the UI renders "unavailable" instead
#: of an empty slot a reader could mistake for zero cost.
CREDITS_STATUS = "unavailable"

#: Turn bands per stage class at ``Standard`` depth (§1.15 table, verbatim). Data, not code, so the
#: golden test in ``test_estimates.py`` reads the same table the estimator does.
RULE_BANDS: dict[str, tuple[int, int]] = {
    "initialization": (1, 2),
    "ideation_inline": (3, 6),
    "inception_inline": (4, 8),
    "inception_pipeline": (6, 12),
    "inception_mob": (5, 9),
    "construction_per_unit": (6, 12),
    "code_generation": (10, 20),
    "build_and_test": (6, 14),
    "ci_pipeline": (4, 8),
    "operation": (4, 8),
}
STAGE_CLASSES = tuple(RULE_BANDS)

#: The classes whose band is per unit of work; every other class runs once for the whole intent.
PER_UNIT_CLASSES = frozenset({"construction_per_unit", "code_generation"})

#: Depth multiplies the whole band (§1.15). An unknown depth is treated as ``Standard`` rather than
#: refused: the estimate is advisory, and refusing to show one because a state file spells depth
#: differently would hide the counts next to it.
DEPTH_MULTIPLIERS: dict[str, float] = {"Minimal": 0.7, "Standard": 1.0, "Comprehensive": 1.4}
DEFAULT_DEPTH = "Standard"

#: A reviewer loop adds work proportional to the stage band, not a flat number of turns.
REVIEW_UPLIFT: dict[str, float] = {"none": 0.0, "advisory": 0.3, "adversarial": 0.6}

#: A stage whose ``summary_confirmation`` is ``required`` ends with one extra human exchange.
SUMMARY_CONFIRMATION_REQUIRED = "required"
SUMMARY_CONFIRMATION_TURNS = 1

#: Seconds of attended time one turn costs (§1.15). Multiplied by the low/high turn counts respectively.
SECS_PER_TURN_LOW = 90
SECS_PER_TURN_HIGH = 300
#: Wall-clock is active time stretched by waiting for the human; a pure assumption (see module docstring).
ELAPSED_FACTOR_LOW = 3
ELAPSED_FACTOR_HIGH = 8

#: Percentiles used when a cohort is large enough to speak for itself. p10/p90 rather than min/max so one
#: pathological run (a session that died and was restarted) cannot widen every future estimate.
CALIBRATION_LOW_PERCENTILE = 0.10
CALIBRATION_HIGH_PERCENTILE = 0.90

#: ``calibration`` columns this module writes. Named because ``storage.py`` owns the DDL and a typo here
#: would surface as a SQLite error inside a worker thread rather than as a failed insert.
_CALIBRATION_TABLE = "calibration"

#: The DDL has no ``intent_ref`` column (§1.4), so the reference rides inside ``actual_json`` — it
#: identifies the observed run, which is what ``actual`` describes. Named so ``est_vs_actual`` and
#: ``record_actual`` cannot disagree about where it lives.
_INTENT_REF_KEY = "intent_ref"


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Range:
    """A bounded prediction that knows where it came from.

    ``source``/``confidence`` are per-metric rather than per-estimate (review P26.4): a plan can have a
    calibrated turn count and an assumed elapsed time in the same breath, and collapsing the two into
    one label would overstate the weaker half.
    """

    low: int
    high: int
    unit: str
    source: str
    confidence: str

    def to_json(self) -> dict[str, Any]:
        return {
            "low": self.low,
            "high": self.high,
            "unit": self.unit,
            "source": self.source,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class DominantStage:
    """One row of "where the time goes", so a user can see what to reconsider."""

    slug: str
    share_pct: int
    turns: Range

    def to_json(self) -> dict[str, Any]:
        return {"slug": self.slug, "share_pct": self.share_pct, "turns": self.turns.to_json()}


@dataclass(frozen=True, slots=True)
class CoverageLoss:
    """What a disabled stage would have produced.

    The counterweight to a cheaper estimate: turning a stage off always lowers the numbers, and the only
    honest way to show that trade is to name the artifacts that stop existing.
    """

    slug: str
    artifacts: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return {"slug": self.slug, "artifacts": list(self.artifacts)}


@dataclass(frozen=True, slots=True)
class Estimate:
    """The whole prediction for one plan.

    The top-level ``source``/``confidence``/``samples`` mirror ``turns`` (§1.15): turns are the metric
    everything else is derived from, so labelling the estimate by anything else would let a calibrated
    headline sit on top of an uncalibrated number.
    """

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

    def to_json(self) -> dict[str, Any]:
        return {
            "turns": self.turns.to_json(),
            "active_secs": self.active_secs.to_json(),
            "elapsed_secs": None if self.elapsed_secs is None else self.elapsed_secs.to_json(),
            "credits": None,
            "credits_status": self.credits_status,
            "source": self.source,
            "confidence": self.confidence,
            "samples": self.samples,
            "dominant": [d.to_json() for d in self.dominant],
            "coverage_lost": [c.to_json() for c in self.coverage_lost],
        }


@dataclass(frozen=True, slots=True)
class CalibrationSample:
    """One observed stage outcome, recorded against the estimate that was shown for it.

    Content-free by construction (C17): the cohort key, integers, and a hashed intent reference. There is
    deliberately no field that could carry an intent name, a repository path or a stage artifact.
    """

    scope: str
    depth: str
    stage_class: str
    actual_turns: int
    actual_active_secs: int
    intent_ref: str
    model_class: str | None = None
    review_iterations: int | None = None
    test_secs: int | None = None
    est: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "depth": self.depth,
            "stage_class": self.stage_class,
            "model_class": self.model_class,
            "review_iterations": self.review_iterations,
            "test_secs": self.test_secs,
            "est": dict(self.est),
            "actual": {"turns": self.actual_turns, "active_secs": self.actual_active_secs},
            "intent_ref": self.intent_ref,
        }

    @classmethod
    def of(
        cls,
        *,
        scope: str,
        depth: str,
        stage_class: str,
        actual: Mapping[str, Any],
        est: Mapping[str, Any] | None = None,
        intent_uuid: str | None = None,
        intent_ref: str | None = None,
        model_class: str | None = None,
        review_iterations: int | None = None,
        test_secs: int | None = None,
    ) -> "CalibrationSample":
        """Build a sample, hashing ``intent_uuid`` so a caller cannot store the uuid by accident."""
        ref = intent_ref or (intent_ref_for(intent_uuid) if intent_uuid else "")
        return cls(
            scope=scope,
            depth=depth,
            stage_class=stage_class,
            actual_turns=_non_negative_int(actual.get("turns")),
            actual_active_secs=_non_negative_int(actual.get("active_secs")),
            intent_ref=ref,
            model_class=model_class,
            review_iterations=review_iterations,
            test_secs=test_secs,
            est=dict(est or {}),
        )


def intent_ref_for(uuid: str) -> str:
    """``sha256(uuid)[:12]`` (C17) — correlatable across samples, reversible into nothing."""
    import hashlib

    return hashlib.sha256(uuid.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #

#: Stages whose band is keyed by slug rather than by phase (§1.15 table names them explicitly). Slug
#: rules are checked BEFORE phase/mode rules because ``code-generation`` also has ``mode: subagent`` and
#: would otherwise be priced as a mob stage at half its band.
_SLUG_CLASSES: dict[str, str] = {
    "code-generation": "code_generation",
    "build-and-test": "build_and_test",
    "ci-pipeline": "ci_pipeline",
    "reverse-engineering": "inception_pipeline",
}
#: Inception modes the engine runs as a facilitated group step (``user-stories``, ``practices-discovery``).
_MOB_MODES = frozenset({"mob", "subagent"})
#: Fallback per phase, so a stage a future graph adds still gets a band instead of an exception.
_PHASE_CLASSES: dict[str, str] = {
    "initialization": "initialization",
    "ideation": "ideation_inline",
    "inception": "inception_inline",
    "construction": "construction_per_unit",
    "operation": "operation",
}


def classify_stage(stage: "PlanStage") -> str:
    """The rule-band class of one stage (§1.15 table).

    Order is load-bearing: initialization first (those stages have no model work to speak of), then the
    slugs the table names, then mode, then per-unit fan-out, then the phase fallback. Written as a
    function rather than a lookup on the stage because the graph does not carry a class of its own and
    two callers need the same answer — the estimator and the calibration cohort key.
    """
    phase = (stage.phase or "").strip().lower()
    if phase == "initialization":
        return "initialization"
    slug_class = _SLUG_CLASSES.get(stage.slug)
    if slug_class is not None:
        return slug_class
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


# --------------------------------------------------------------------------- #
# service
# --------------------------------------------------------------------------- #


class EstimateService:
    """Rule bands, local calibration and the refusal to invent a credit number.

    Sync (§0.6): every method is a small SQLite read or write, so callers reach it through
    ``asyncio.to_thread``. It holds no repository path and stores no intent name — see the module
    docstring for why that is a hard rule rather than a preference.
    """

    def __init__(self, storage: "Storage", clock: "C.Clock") -> None:
        self._storage = storage
        self._clock = clock

    # ---- estimation --------------------------------------------------------

    def estimate(
        self,
        stages: Sequence["PlanStage"],
        *,
        depth: str,
        units: int | None,
        scope: str,
    ) -> Estimate:
        """Bound the work for one plan.

        ``stages`` is the WHOLE plan, enabled and disabled: the disabled rows are what
        ``coverage_lost`` reports, and dropping them here would make a cheaper estimate look free.
        ``units`` of ``None`` means the unit fan-out is not known yet (a new intent has no units), and
        the per-unit bands are then priced for one unit — ``ExactCounts.assumes_units`` is where that
        assumption is disclosed.
        """
        unit_count = max(1, int(units or 1))
        multiplier = DEPTH_MULTIPLIERS.get(depth, DEPTH_MULTIPLIERS[DEFAULT_DEPTH])
        enabled = [s for s in stages if s.enabled]

        cohorts: dict[str, _Cohort] = {}
        rows: list[tuple[str, Range, int, int]] = []  # slug, turns, active_low, active_high
        calibrated = bool(enabled)
        for stage in enabled:
            stage_class = classify_stage(stage)
            cohort = cohorts.get(stage_class)
            if cohort is None:
                cohort = self._cohort(scope, depth, stage_class)
                cohorts[stage_class] = cohort
            calibrated = calibrated and cohort.calibrated
            turns = self._stage_turns(stage, stage_class, cohort, multiplier, unit_count)
            secs_low, secs_high = cohort.secs_per_turn or (SECS_PER_TURN_LOW, SECS_PER_TURN_HIGH)
            rows.append((stage.slug, turns, turns.low * secs_low, turns.high * secs_high))

        # The headline is calibrated only when EVERY contributing class is: one uncalibrated stage means
        # the total is part guess, and labelling that "history" would overstate the evidence.
        source = SOURCE_HISTORY if calibrated else SOURCE_RULE_BAND
        confidence = CONFIDENCE_MEDIUM if calibrated else CONFIDENCE_LOW
        samples = sum(c.samples for c in cohorts.values())

        turns_total = Range(
            low=sum(r[1].low for r in rows),
            high=sum(r[1].high for r in rows),
            unit=UNIT_TURNS,
            source=source,
            confidence=confidence,
        )
        active_total = Range(
            low=sum(r[2] for r in rows),
            high=sum(r[3] for r in rows),
            unit=UNIT_SECS,
            source=source,
            confidence=confidence,
        )
        # Elapsed time is only meaningful once there is work to wait for; an empty plan gets `null`
        # rather than a 0–0 band that would read as "instant".
        elapsed = (
            None
            if not rows
            else Range(
                low=active_total.low * ELAPSED_FACTOR_LOW,
                high=active_total.high * ELAPSED_FACTOR_HIGH,
                unit=UNIT_SECS,
                source=SOURCE_ASSUMPTION,
                confidence=CONFIDENCE_LOW,
            )
        )

        return Estimate(
            turns=turns_total,
            active_secs=active_total,
            elapsed_secs=elapsed,
            credits=None,
            credits_status=CREDITS_STATUS,
            source=source,
            confidence=confidence,
            samples=samples,
            dominant=self._dominant(rows, turns_total.high),
            coverage_lost=self._coverage_lost(stages),
        )

    # ---- calibration -------------------------------------------------------

    def record_actual(self, sample: CalibrationSample) -> None:
        """Store one observed outcome. Refuses an unknown cohort key rather than polluting the table.

        A row with a mistyped ``stage_class`` would never be read back (the cohort key would not match
        any plan) yet would still count towards nothing forever, so it is a programming error worth
        raising on.
        """
        if sample.stage_class not in RULE_BANDS:
            raise StudioError(
                "internal_error",
                f"unknown stage_class {sample.stage_class!r}; add it to estimates.RULE_BANDS",
                details={"stage_class": sample.stage_class},
            )
        payload = sample.to_json()
        self._storage.insert(
            _CALIBRATION_TABLE,
            {
                "at": self._clock.iso(),
                "scope": sample.scope,
                "depth": sample.depth,
                "stage_class": sample.stage_class,
                "model_class": sample.model_class,
                "review_iterations": sample.review_iterations,
                "test_secs": sample.test_secs,
                "est_json": payload["est"],
                "actual_json": {**payload["actual"], _INTENT_REF_KEY: sample.intent_ref},
            },
        )

    def cohort_samples(self, scope: str, depth: str, stage_class: str) -> int:
        """How many comparable samples exist. The number the ``rule_band``/``history`` switch turns on."""
        return len(self._rows(scope, depth, stage_class))

    def cohorts(self) -> list[dict[str, Any]]:
        """Every cohort with at least one sample, for ``GET /calibration``.

        Not in §1.15's method list: the route needs the whole table grouped, and doing that grouping in
        the handler would put SQL knowledge in the HTTP layer.
        """
        counts: dict[tuple[str, str, str], int] = {}
        for row in self._storage.select(_CALIBRATION_TABLE):
            key = (str(row.get("scope") or ""), str(row.get("depth") or ""), str(row.get("stage_class") or ""))
            counts[key] = counts.get(key, 0) + 1
        return [
            {"scope": scope, "depth": depth, "stage_class": stage_class, "samples": samples}
            for (scope, depth, stage_class), samples in sorted(counts.items())
        ]

    def est_vs_actual(self, intent_ref: str) -> list[dict[str, Any]]:
        """Estimate against outcome for one hashed intent, oldest first (FR-EST-004 evidence)."""
        out: list[dict[str, Any]] = []
        for row in self._storage.select(_CALIBRATION_TABLE, order_by="id ASC"):
            actual = _as_dict(row.get("actual_json"))
            if str(actual.get(_INTENT_REF_KEY) or "") != intent_ref:
                continue
            out.append(
                {
                    "at": row.get("at"),
                    "scope": row.get("scope"),
                    "depth": row.get("depth"),
                    "stage_class": row.get("stage_class"),
                    "model_class": row.get("model_class"),
                    "review_iterations": row.get("review_iterations"),
                    "test_secs": row.get("test_secs"),
                    "est": _as_dict(row.get("est_json")),
                    "actual": {k: v for k, v in actual.items() if k != _INTENT_REF_KEY},
                    "intent_ref": intent_ref,
                }
            )
        return out

    def clear(self) -> int:
        """Delete every sample; returns how many went. Backs ``POST /calibration/clear``.

        Row-by-row because ``Storage`` exposes no bulk delete and inventing one here would mean writing
        SQL outside the module that owns the schema.
        """
        ids = [row.get("id") for row in self._storage.select(_CALIBRATION_TABLE)]
        for row_id in ids:
            self._storage.delete(_CALIBRATION_TABLE, row_id)
        return len(ids)

    # ---- internals ---------------------------------------------------------

    def _rows(self, scope: str, depth: str, stage_class: str) -> list[dict[str, Any]]:
        return self._storage.select(
            _CALIBRATION_TABLE, {"scope": scope, "depth": depth, "stage_class": stage_class}
        )

    def _cohort(self, scope: str, depth: str, stage_class: str) -> "_Cohort":
        """Turn a cohort's samples into bands, or into "not enough evidence"."""
        rows = self._rows(scope, depth, stage_class)
        if len(rows) < C.CALIBRATION_MIN_SAMPLES:
            return _Cohort(samples=len(rows), turns=None, secs_per_turn=None)
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
            # Rows exist but carry no usable turn counts; that is not evidence, so it is not history.
            return _Cohort(samples=len(rows), turns=None, secs_per_turn=None)
        band = (
            _percentile(turns, CALIBRATION_LOW_PERCENTILE),
            _percentile(turns, CALIBRATION_HIGH_PERCENTILE),
        )
        per_turn = (
            (_percentile(ratios, CALIBRATION_LOW_PERCENTILE), _percentile(ratios, CALIBRATION_HIGH_PERCENTILE))
            if len(ratios) >= C.CALIBRATION_MIN_SAMPLES
            else None
        )
        return _Cohort(samples=len(rows), turns=band, secs_per_turn=per_turn)

    @staticmethod
    def _stage_turns(
        stage: "PlanStage",
        stage_class: str,
        cohort: "_Cohort",
        multiplier: float,
        units: int,
    ) -> Range:
        """One stage's turn band.

        A calibrated band is used verbatim: depth is part of the cohort key, the samples already cover
        whatever units and review loops that stage really had, and re-applying the rule multipliers on
        top of measured history would double-count the very effects the measurement captured.
        """
        if cohort.turns is not None:
            low, high = cohort.turns
            return Range(
                low=max(0, low),
                high=max(low, high),
                unit=UNIT_TURNS,
                source=SOURCE_HISTORY,
                confidence=CONFIDENCE_MEDIUM,
            )
        base_low, base_high = RULE_BANDS[stage_class]
        if stage_class in PER_UNIT_CLASSES:
            base_low *= units
            base_high *= units
        factor = multiplier + REVIEW_UPLIFT.get(stage.review_class or "none", 0.0)
        # Ceiling, not rounding: an estimate that rounds work away reads as a promise it cannot keep.
        low = math.ceil(base_low * factor)
        high = math.ceil(base_high * factor)
        if (getattr(stage, "summary_confirmation", None) or "") == SUMMARY_CONFIRMATION_REQUIRED:
            low += SUMMARY_CONFIRMATION_TURNS
            high += SUMMARY_CONFIRMATION_TURNS
        return Range(
            low=low,
            high=max(low, high),
            unit=UNIT_TURNS,
            source=SOURCE_RULE_BAND,
            confidence=CONFIDENCE_LOW,
        )

    @staticmethod
    def _dominant(rows: Sequence[tuple[str, Range, int, int]], total_high: int) -> tuple[DominantStage, ...]:
        ordered = sorted(rows, key=lambda r: (-r[1].high, r[0]))
        return tuple(
            DominantStage(
                slug=slug,
                share_pct=int(round(turns.high * 100 / total_high)) if total_high > 0 else 0,
                turns=turns,
            )
            for slug, turns, _low, _high in ordered
        )

    @staticmethod
    def _coverage_lost(stages: Sequence["PlanStage"]) -> tuple[CoverageLoss, ...]:
        """Stages the request switched off: selected by the plan on disk, not enabled now.

        ``in_grid and not enabled`` is exactly "the composition wanted this and the request does not",
        which is what an override (or a recompose ``--skip``) does — no extra flag needed on the stage.
        """
        return tuple(
            CoverageLoss(slug=s.slug, artifacts=tuple(s.produces))
            for s in stages
            if s.in_grid and not s.enabled
        )


@dataclass(frozen=True, slots=True)
class _Cohort:
    """What history has to say about one ``(scope, depth, stage_class)``."""

    samples: int
    turns: tuple[int, int] | None
    secs_per_turn: tuple[int, int] | None

    @property
    def calibrated(self) -> bool:
        return self.turns is not None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _percentile(values: Iterable[int], q: float) -> int:
    """Nearest-rank percentile. Deterministic on purpose: a golden test must be able to predict it."""
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
    """Decode a ``*_json`` column that may arrive already decoded (``Storage``) or as text."""
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}
