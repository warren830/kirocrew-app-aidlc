"""``backend/studio/estimates.py``: a band never claims evidence it does not have (``rule_band``/``low``,
``samples == 0``), credits are never invented (``None``/``unavailable`` on every path), and the rule bands are
golden-tested against §1.15's table so a multiplier tweak shows up as a diff in an expected number.
"""

from __future__ import annotations

from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent


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
def svc(E):
    return E.EstimateService()


def stage(P, slug: str, *, phase: str = "inception", mode: str = "inline", enabled: bool = True,
          in_grid: bool = True, per_unit: bool = False, review_class: str = "none",
          summary_confirmation: str | None = None, produces: tuple[str, ...] = ()):
    """A ``PlanStage`` with only the fields the estimator reads spelled out."""
    return P.PlanStage(
        slug=slug, number="2.1", name=slug, phase=phase, execution="CONDITIONAL", in_grid=in_grid, enabled=enabled,
        locked=False, lock_reason=None, gate=phase != "initialization", review_class=review_class, reviewer=None,
        per_unit=per_unit, produces=produces, consumes=(), depends_on=(), conditional_on=None, state=None, mode=mode,
        summary_confirmation=summary_confirmation)


def estimate(svc, *stages, depth="Standard", units=1, scope="feature"):
    return svc.estimate(list(stages), depth=depth, units=units, scope=scope)


def band(est):
    return est.turns.low, est.turns.high


# ---- classification ---- #


@pytest.mark.parametrize("slug, phase, mode, per_unit, expected", [
    ("workspace-scaffold", "initialization", "inline", False, "initialization"),
    ("intent-capture", "ideation", "inline", False, "ideation_inline"),
    ("requirements-analysis", "inception", "inline", False, "inception_inline"),
    ("reverse-engineering", "inception", "pipeline", False, "inception_pipeline"),
    ("user-stories", "inception", "mob", False, "inception_mob"),
    ("practices-discovery", "inception", "subagent", False, "inception_mob"),
    ("functional-design", "construction", "inline", True, "construction_per_unit"),
    # Also mode=subagent: the slug rule must win, or it would be priced as a mob stage at half its band.
    ("code-generation", "construction", "subagent", True, "code_generation"),
    ("build-and-test", "construction", "inline", False, "build_and_test"),
    # 2.6.2 puts ci-pipeline in the CONSTRUCTION phase; its band is keyed by slug regardless.
    ("ci-pipeline", "construction", "inline", False, "ci_pipeline"),
    ("deployment-pipeline", "operation", "inline", False, "operation"),
    ("something-new", "", "inline", False, "inception_inline"),
])
def test_classify_stage_table(E, P, slug, phase, mode, per_unit, expected):
    assert E.classify_stage(stage(P, slug, phase=phase, mode=mode, per_unit=per_unit)) == expected


def test_every_class_has_a_band(E):
    assert set(E.STAGE_CLASSES) == set(E.RULE_BANDS)
    assert E.RULE_BANDS["code_generation"] == (10, 20)
    assert E.RULE_BANDS["initialization"] == (1, 2)


# ---- rule bands ---- #


@pytest.mark.parametrize("stage_class, slug, phase, mode, per_unit, golden", [
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
])
def test_rule_band_golden_at_standard_depth(E, P, stage_class, slug, phase, mode, per_unit, golden, svc):
    """Standard depth, no review, one unit: the band must be §1.15's table verbatim."""
    est = estimate(svc, stage(P, slug, phase=phase, mode=mode, per_unit=per_unit))
    assert E.RULE_BANDS[stage_class] == golden
    assert band(est) == golden
    assert est.turns.source == E.SOURCE_RULE_BAND
    assert est.turns.confidence == E.CONFIDENCE_LOW


@pytest.mark.parametrize("depth, expected", [("Minimal", (7, 14)), ("Standard", (10, 20)), ("Comprehensive", (14, 28))])
def test_depth_multiplier(P, svc, depth, expected):
    code = stage(P, "code-generation", phase="construction", mode="subagent", per_unit=True)
    assert band(estimate(svc, code, depth=depth, scope="poc")) == expected


@pytest.mark.parametrize("review_class, expected", [
    ("none", (10, 20)), ("advisory", (13, 26)), ("adversarial", (16, 32))])
def test_review_uplift_is_a_share_of_the_band(P, svc, review_class, expected):
    code = stage(P, "code-generation", phase="construction", per_unit=True, review_class=review_class)
    assert band(estimate(svc, code)) == expected


def test_summary_confirmation_costs_one_turn(P, svc):
    plain = estimate(svc, stage(P, "requirements-analysis"))
    with_checkpoint = estimate(svc, stage(P, "requirements-analysis", summary_confirmation="required"))
    assert band(with_checkpoint) == (plain.turns.low + 1, plain.turns.high + 1)


def test_per_unit_classes_scale_with_units(P, svc):
    stages = (stage(P, "code-generation", phase="construction", per_unit=True),
              stage(P, "build-and-test", phase="construction"))
    # code-generation triples (10-20 → 30-60); build-and-test does not (6-14).
    assert band(estimate(svc, *stages, units=1)) == (16, 34)
    assert band(estimate(svc, *stages, units=3)) == (36, 74)


def test_units_none_prices_one_unit(P, svc):
    code = stage(P, "code-generation", phase="construction", per_unit=True)
    assert estimate(svc, code, units=None).turns == estimate(svc, code, units=1).turns


# ---- derived metrics ---- #


def test_active_and_elapsed_are_derived_from_turns(E, P, svc):
    est = estimate(svc, stage(P, "ci-pipeline", phase="construction"))
    assert band(est) == (4, 8)
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
    est = estimate(svc, stage(P, "ci-pipeline", enabled=False))
    assert band(est) == (0, 0)
    assert est.elapsed_secs is None


def test_dominant_is_sorted_by_high_with_shares(P, svc):
    est = estimate(svc, stage(P, "state-init", phase="initialization"),
                   stage(P, "code-generation", phase="construction", per_unit=True),
                   stage(P, "build-and-test", phase="construction"))
    assert [d.slug for d in est.dominant] == ["code-generation", "build-and-test", "state-init"]
    assert est.turns.high == 20 + 14 + 2
    assert est.dominant[0].share_pct == round(20 * 100 / 36)
    assert sum(d.turns.high for d in est.dominant) == est.turns.high


def test_coverage_lost_names_the_artifacts_a_disabled_stage_would_have_written(P, svc):
    est = estimate(svc, stage(P, "ci-pipeline", phase="construction", enabled=False, in_grid=True,
                              produces=("ci-pipeline-plan", "ci-config")),
                   # never in the composition → nothing was lost by leaving it off
                   stage(P, "incident-response", phase="operation", enabled=False, in_grid=False,
                         produces=("runbook",)))
    assert [(c.slug, c.artifacts) for c in est.coverage_lost] == [("ci-pipeline", ("ci-pipeline-plan", "ci-config"))]


def test_every_estimate_is_a_rule_band_with_no_samples(E, P, svc):
    est = estimate(svc, stage(P, "code-generation", phase="construction", per_unit=True), depth="Minimal", scope="poc")
    assert (est.source, est.confidence, est.samples) == (E.SOURCE_RULE_BAND, E.CONFIDENCE_LOW, 0)
    assert band(est) == (7, 14)
    assert all(d.turns.source == E.SOURCE_RULE_BAND for d in est.dominant)


def test_service_stores_no_repository_state(E):
    """The estimator must be safe to export: it reads stages, never a repository."""
    source = (APP_ROOT / "backend" / "studio" / "estimates.py").read_text("utf-8")
    for forbidden in ("canonical_path", "intent_dir", "repo_id", "Path("):
        assert forbidden not in source, forbidden
