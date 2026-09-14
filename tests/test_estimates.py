"""Estimation tests for ``backend/studio/estimates.py``.

Two properties are worth more than the arithmetic:

* **A band never claims evidence it does not have.** Nine samples in a cohort must still read
  ``rule_band``/``low``; the tenth is what flips it to ``history_calibrated``/``medium``. The threshold
  is tested from both sides because "we calibrated this from your history" is a claim a user will act
  on.
* **Credits are never invented.** ``credits`` is ``None`` and ``credits_status`` is ``unavailable`` in
  every path, including the empty plan — KiroCrew exposes no per-turn cost, so any number would be
  fiction with a currency symbol in front of it.

The rule bands themselves are golden-tested against §1.15's table so a "harmless" tweak to a multiplier
shows up as a diff in an expected number rather than as a quietly different promise.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def E(studio):
    module = studio.estimates
    assert module is not None, getattr(studio, "estimates__error", "estimates.py did not import")
    return module


@pytest.fixture
def P(studio):
    module = studio.plan
    assert module is not None, getattr(studio, "plan__error", "plan.py did not import")
    return module


@pytest.fixture
def store(studio, fake_ctx, clock, ids):
    st = studio.storage.Storage(
        Path(fake_ctx.data_dir) / studio.constants.DB_FILENAME, clock=clock, ids=ids
    )
    st.open()
    yield st
    st.close()


@pytest.fixture
def svc(E, store, clock):
    return E.EstimateService(store, clock)


def stage(
    P,
    slug: str,
    *,
    phase: str = "inception",
    mode: str = "inline",
    enabled: bool = True,
    in_grid: bool = True,
    per_unit: bool = False,
    review_class: str = "none",
    summary_confirmation: str | None = None,
    produces: tuple[str, ...] = (),
):
    """A ``PlanStage`` with only the fields the estimator reads spelled out."""
    return P.PlanStage(
        slug=slug,
        number="2.1",
        name=slug,
        phase=phase,
        execution="CONDITIONAL",
        in_grid=in_grid,
        enabled=enabled,
        locked=False,
        lock_reason=None,
        gate=phase != "initialization",
        review_class=review_class,
        reviewer=None,
        per_unit=per_unit,
        produces=produces,
        consumes=(),
        depends_on=(),
        conditional_on=None,
        state=None,
        mode=mode,
        summary_confirmation=summary_confirmation,
    )


def sample(E, *, scope="poc", depth="Minimal", stage_class="code_generation", turns=10, secs=1800,
           intent_uuid="01a00000-0000-7000-8000-000000000001"):
    return E.CalibrationSample.of(
        scope=scope,
        depth=depth,
        stage_class=stage_class,
        actual={"turns": turns, "active_secs": secs},
        est={"turns": {"low": 8, "high": 16}},
        intent_uuid=intent_uuid,
    )


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "slug, phase, mode, per_unit, expected",
    [
        ("workspace-scaffold", "initialization", "inline", False, "initialization"),
        ("intent-capture", "ideation", "inline", False, "ideation_inline"),
        ("requirements-analysis", "inception", "inline", False, "inception_inline"),
        ("reverse-engineering", "inception", "pipeline", False, "inception_pipeline"),
        ("user-stories", "inception", "mob", False, "inception_mob"),
        ("practices-discovery", "inception", "subagent", False, "inception_mob"),
        ("functional-design", "construction", "inline", True, "construction_per_unit"),
        # `code-generation` is also mode=subagent: the slug rule must win, or it would be priced as a
        # mob stage at half its band.
        ("code-generation", "construction", "subagent", True, "code_generation"),
        ("build-and-test", "construction", "inline", False, "build_and_test"),
        # 2.6.2 puts ci-pipeline in the CONSTRUCTION phase; its band is keyed by slug regardless.
        ("ci-pipeline", "construction", "inline", False, "ci_pipeline"),
        ("deployment-pipeline", "operation", "inline", False, "operation"),
        ("something-new", "", "inline", False, "inception_inline"),
    ],
)
def test_classify_stage_table(E, P, slug, phase, mode, per_unit, expected):
    assert E.classify_stage(stage(P, slug, phase=phase, mode=mode, per_unit=per_unit)) == expected


def test_every_class_has_a_band(E):
    assert set(E.STAGE_CLASSES) == set(E.RULE_BANDS)
    assert E.RULE_BANDS["code_generation"] == (10, 20)
    assert E.RULE_BANDS["initialization"] == (1, 2)


# --------------------------------------------------------------------------- #
# rule bands
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "stage_class, slug, phase, mode, per_unit, band",
    [
        ("initialization", "state-init", "initialization", "inline", False, (1, 2)),
        ("ideation_inline", "intent-capture", "ideation", "inline", False, (3, 6)),
        ("inception_inline", "requirements-analysis", "inception", "inline", False, (4, 8)),
        ("inception_pipeline", "reverse-engineering", "inception", "pipeline", False, (6, 12)),
        ("inception_mob", "user-stories", "inception", "mob", False, (5, 9)),
        ("construction_per_unit", "functional-design", "construction", "inline", True, (6, 12)),
        ("code_generation", "code-generation", "construction", "subagent", True, (10, 20)),
        ("build_and_test", "build-and-test", "construction", "inline", False, (6, 14)),
        ("ci_pipeline", "ci-pipeline", "construction", "inline", False, (4, 8)),
        ("operation", "observability-setup", "operation", "inline", False, (4, 8)),
    ],
)
def test_rule_band_golden_at_standard_depth(E, P, stage_class, slug, phase, mode, per_unit, band, svc):
    """Standard depth, no review, one unit: the band must be §1.15's table verbatim."""
    est = svc.estimate(
        [stage(P, slug, phase=phase, mode=mode, per_unit=per_unit)],
        depth="Standard",
        units=1,
        scope="feature",
    )
    assert E.RULE_BANDS[stage_class] == band
    assert (est.turns.low, est.turns.high) == band
    assert est.turns.source == E.SOURCE_RULE_BAND
    assert est.turns.confidence == E.CONFIDENCE_LOW


@pytest.mark.parametrize(
    "depth, expected",
    [("Minimal", (7, 14)), ("Standard", (10, 20)), ("Comprehensive", (14, 28))],
)
def test_depth_multiplier(P, svc, depth, expected):
    est = svc.estimate(
        [stage(P, "code-generation", phase="construction", mode="subagent", per_unit=True)],
        depth=depth,
        units=1,
        scope="poc",
    )
    assert (est.turns.low, est.turns.high) == expected


@pytest.mark.parametrize(
    "review_class, expected",
    [("none", (10, 20)), ("advisory", (13, 26)), ("adversarial", (16, 32))],
)
def test_review_uplift_is_a_share_of_the_band(P, svc, review_class, expected):
    est = svc.estimate(
        [stage(P, "code-generation", phase="construction", per_unit=True, review_class=review_class)],
        depth="Standard",
        units=1,
        scope="feature",
    )
    assert (est.turns.low, est.turns.high) == expected


def test_summary_confirmation_costs_one_turn(P, svc):
    plain = svc.estimate([stage(P, "requirements-analysis")], depth="Standard", units=1, scope="feature")
    with_checkpoint = svc.estimate(
        [stage(P, "requirements-analysis", summary_confirmation="required")],
        depth="Standard",
        units=1,
        scope="feature",
    )
    assert (with_checkpoint.turns.low, with_checkpoint.turns.high) == (plain.turns.low + 1, plain.turns.high + 1)


def test_per_unit_classes_scale_with_units(P, svc):
    stages = [
        stage(P, "code-generation", phase="construction", per_unit=True),
        stage(P, "build-and-test", phase="construction"),
    ]
    one = svc.estimate(stages, depth="Standard", units=1, scope="feature")
    three = svc.estimate(stages, depth="Standard", units=3, scope="feature")
    # code-generation triples (10-20 → 30-60); build-and-test does not (6-14).
    assert (one.turns.low, one.turns.high) == (16, 34)
    assert (three.turns.low, three.turns.high) == (36, 74)


def test_units_none_prices_one_unit(P, svc):
    stages = [stage(P, "code-generation", phase="construction", per_unit=True)]
    assert svc.estimate(stages, depth="Standard", units=None, scope="feature").turns == svc.estimate(
        stages, depth="Standard", units=1, scope="feature"
    ).turns


# --------------------------------------------------------------------------- #
# derived metrics
# --------------------------------------------------------------------------- #


def test_active_and_elapsed_are_derived_from_turns(E, P, svc):
    est = svc.estimate([stage(P, "ci-pipeline", phase="construction")], depth="Standard", units=1,
                       scope="feature")
    assert (est.turns.low, est.turns.high) == (4, 8)
    assert (est.active_secs.low, est.active_secs.high) == (4 * 90, 8 * 300)
    assert est.active_secs.unit == E.UNIT_SECS
    assert est.elapsed_secs is not None
    assert (est.elapsed_secs.low, est.elapsed_secs.high) == (4 * 90 * 3, 8 * 300 * 8)
    # Wall-clock time is nobody's measurement: it is labelled as the assumption it is.
    assert (est.elapsed_secs.source, est.elapsed_secs.confidence) == (E.SOURCE_ASSUMPTION, E.CONFIDENCE_LOW)


def test_credits_are_always_unavailable(E, P, svc):
    for stages in ([], [stage(P, "code-generation", phase="construction", per_unit=True)]):
        est = svc.estimate(stages, depth="Standard", units=2, scope="feature")
        assert est.credits is None
        assert est.credits_status == E.CREDITS_STATUS == "unavailable"
        assert est.to_json()["credits"] is None
        assert est.to_json()["credits_status"] == "unavailable"


def test_empty_plan_has_no_elapsed_band(P, svc):
    est = svc.estimate([stage(P, "ci-pipeline", enabled=False)], depth="Standard", units=1, scope="feature")
    assert (est.turns.low, est.turns.high) == (0, 0)
    assert est.elapsed_secs is None


def test_dominant_is_sorted_by_high_with_shares(P, svc):
    est = svc.estimate(
        [
            stage(P, "state-init", phase="initialization"),
            stage(P, "code-generation", phase="construction", per_unit=True),
            stage(P, "build-and-test", phase="construction"),
        ],
        depth="Standard",
        units=1,
        scope="feature",
    )
    assert [d.slug for d in est.dominant] == ["code-generation", "build-and-test", "state-init"]
    assert est.turns.high == 20 + 14 + 2
    assert est.dominant[0].share_pct == round(20 * 100 / 36)
    assert sum(d.turns.high for d in est.dominant) == est.turns.high


def test_coverage_lost_names_the_artifacts_a_disabled_stage_would_have_written(P, svc):
    est = svc.estimate(
        [
            stage(P, "ci-pipeline", phase="construction", enabled=False, in_grid=True,
                  produces=("ci-pipeline-plan", "ci-config")),
            # never in the composition → nothing was lost by leaving it off
            stage(P, "incident-response", phase="operation", enabled=False, in_grid=False,
                  produces=("runbook",)),
        ],
        depth="Standard",
        units=1,
        scope="feature",
    )
    assert [(c.slug, c.artifacts) for c in est.coverage_lost] == [
        ("ci-pipeline", ("ci-pipeline-plan", "ci-config"))
    ]


# --------------------------------------------------------------------------- #
# calibration
# --------------------------------------------------------------------------- #


def test_nine_samples_are_not_history_ten_are(E, P, svc, store):
    stages = [stage(P, "code-generation", phase="construction", per_unit=True)]
    for index in range(9):
        svc.record_actual(sample(E, turns=40 + index, secs=(40 + index) * 100))
    assert svc.cohort_samples("poc", "Minimal", "code_generation") == 9
    nine = svc.estimate(stages, depth="Minimal", units=1, scope="poc")
    assert nine.source == E.SOURCE_RULE_BAND
    assert nine.confidence == E.CONFIDENCE_LOW
    assert nine.samples == 9
    assert (nine.turns.low, nine.turns.high) == (7, 14)  # still the rule band

    svc.record_actual(sample(E, turns=49, secs=4900))
    ten = svc.estimate(stages, depth="Minimal", units=1, scope="poc")
    assert ten.source == E.SOURCE_HISTORY
    assert ten.confidence == E.CONFIDENCE_MEDIUM
    assert ten.samples == 10
    # p10/p90 of 40..49 by nearest rank → indexes 1 and 8.
    assert (ten.turns.low, ten.turns.high) == (41, 48)
    assert ten.turns.source == E.SOURCE_HISTORY
    # A calibrated band is used verbatim: the depth multiplier is already part of the cohort key.
    assert ten.turns.low > 7


def test_calibrated_band_ignores_other_cohorts(E, P, svc):
    for _ in range(12):
        svc.record_actual(sample(E, scope="feature", depth="Standard", turns=99, secs=9900))
    est = svc.estimate(
        [stage(P, "code-generation", phase="construction", per_unit=True)],
        depth="Minimal",
        units=1,
        scope="poc",
    )
    assert est.source == E.SOURCE_RULE_BAND
    assert est.samples == 0


def test_one_uncalibrated_class_keeps_the_whole_estimate_a_rule_band(E, P, svc):
    for index in range(10):
        svc.record_actual(sample(E, scope="feature", depth="Standard", turns=10 + index,
                                 secs=(10 + index) * 120))
    stages = [
        stage(P, "code-generation", phase="construction", per_unit=True),
        stage(P, "build-and-test", phase="construction"),
    ]
    est = svc.estimate(stages, depth="Standard", units=1, scope="feature")
    assert est.source == E.SOURCE_RULE_BAND
    # the calibrated class still uses its measured band for its own row
    dominant = {d.slug: d.turns for d in est.dominant}
    assert dominant["code-generation"].source == E.SOURCE_HISTORY
    assert dominant["build-and-test"].source == E.SOURCE_RULE_BAND


def test_calibrated_seconds_per_turn_come_from_history(E, P, svc):
    for index in range(10):
        svc.record_actual(sample(E, scope="feature", depth="Standard", turns=10, secs=2000 + index))
    est = svc.estimate(
        [stage(P, "code-generation", phase="construction", per_unit=True)],
        depth="Standard",
        units=1,
        scope="feature",
    )
    # 2000..2009 seconds over 10 turns → ~200 s per turn, not the 90/300 rule constants.
    assert est.active_secs.low == est.turns.low * 200
    assert est.active_secs.high == est.turns.high * 201


def test_record_actual_refuses_an_unknown_cohort_class(E, svc, studio):
    with pytest.raises(studio.errors.StudioError) as excinfo:
        svc.record_actual(sample(E, stage_class="does-not-exist"))
    assert excinfo.value.code == "internal_error"


def test_rows_are_content_free(E, svc, store):
    svc.record_actual(sample(E, intent_uuid="01a00000-0000-7000-8000-0000000000ff"))
    row = store.select("calibration")[0]
    text = repr(row)
    assert "01a00000" not in text
    assert row["actual_json"]["intent_ref"] == E.intent_ref_for("01a00000-0000-7000-8000-0000000000ff")
    assert row["actual_json"]["intent_ref"] == hashlib.sha256(
        b"01a00000-0000-7000-8000-0000000000ff"
    ).hexdigest()[:12]
    assert set(row) >= {"scope", "depth", "stage_class", "est_json", "actual_json"}
    assert row["scope"] == "poc"


def test_cohorts_and_est_vs_actual(E, svc):
    svc.record_actual(sample(E, intent_uuid="uuid-a", turns=11, secs=1100))
    svc.record_actual(sample(E, intent_uuid="uuid-a", turns=13, secs=1300, stage_class="build_and_test"))
    svc.record_actual(sample(E, intent_uuid="uuid-b", turns=15, secs=1500))
    assert svc.cohorts() == [
        {"scope": "poc", "depth": "Minimal", "stage_class": "build_and_test", "samples": 1},
        {"scope": "poc", "depth": "Minimal", "stage_class": "code_generation", "samples": 2},
    ]
    rows = svc.est_vs_actual(E.intent_ref_for("uuid-a"))
    assert [r["actual"]["turns"] for r in rows] == [11, 13]
    assert rows[0]["est"] == {"turns": {"low": 8, "high": 16}}
    assert "intent_ref" not in rows[0]["actual"]
    assert svc.est_vs_actual("nothing-here") == []


def test_clear_removes_every_sample(E, svc):
    for index in range(4):
        svc.record_actual(sample(E, turns=10 + index))
    assert svc.clear() == 4
    assert svc.cohorts() == []
    assert svc.cohort_samples("poc", "Minimal", "code_generation") == 0
    assert svc.clear() == 0


def test_service_stores_no_repository_state(E):
    """The estimator must be safe to export: it holds a store and a clock, nothing else."""
    source = (APP_ROOT / "backend" / "studio" / "estimates.py").read_text("utf-8")
    for forbidden in ("canonical_path", "intent_dir", "repo_id", "Path("):
        assert forbidden not in source, forbidden
