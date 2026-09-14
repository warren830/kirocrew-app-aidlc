"""Plan-composer tests for ``backend/studio/plan.py``.

The properties these tests exist to defend, in order of how much damage their absence would do:

1. **Creation never starts a workflow.** ``create_intent`` runs one allowlisted engine verb under an
   admin lease and nothing else: no prompt, no ``next``, no human turn in the audit. A test asserts the
   new intent's audit contains no ``HUMAN_TURN`` row and that the fake engine was never asked for a
   forbidden verb, because "Studio ran my workflow when I only pressed Create" is unrecoverable.
2. **A change is verified on disk, never on an exit code.** Both mutating paths re-read the state file
   and refuse when the evidence is missing.
3. **The plan a user is shown is the plan the engine would run.** Lock reasons and refusals are pinned
   against the engine's own guards (``aidlc-utility.ts`` ``recompose``, ``aidlc-graph.ts::validateGrid``)
   and the exact counts are golden per scope on BOTH graph generations, so a graph or grid change shows
   up as a number that moved rather than as a plan that quietly disagrees with the engine.
"""

from __future__ import annotations

import ast
import asyncio
import atexit
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent
TS = "2026-09-04T10:00:00Z"
IDENTITY = "1:100"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def P(studio):
    module = studio.plan
    assert module is not None, getattr(studio, "plan__error", "plan.py did not import")
    return module


@pytest.fixture
def errors(studio):
    return studio.errors


@pytest.fixture
def deps(studio, fake_ctx, fake_host, clock, ids, fake_bun, denied_log):
    """One real object graph: only ``RepoRegistry`` is absent (``PlanService`` never calls it)."""
    store = studio.storage.Storage(
        Path(fake_ctx.data_dir) / studio.constants.DB_FILENAME, clock=clock, ids=ids
    )
    store.open()
    activity = studio.activity.ActivityProjector(store, clock)
    events = studio.events.EventLog(store, fake_ctx.events, clock)
    settings = studio.settings.SettingsService(store, fake_host, clock)
    scheduler = studio.leases.RepoScheduler(store, fake_host, settings, clock, activity)
    engine = studio.engine.EngineRunner(bun_path=fake_bun, clock=clock, activity=activity)
    reader = studio.aidlc_reader.AidlcReader(clock)
    projection = studio.projection.Projection(reader, studio.consistency.ConsistencyEngine(), clock)
    estimates = studio.estimates.EstimateService(store, clock)
    service = studio.plan.PlanService(
        reader, engine, scheduler, None, projection, store, estimates, activity, events, clock, ids
    )
    yield SimpleNamespace(
        store=store,
        activity=activity,
        events=events,
        scheduler=scheduler,
        engine=engine,
        reader=reader,
        projection=projection,
        estimates=estimates,
        svc=service,
        ctx=fake_ctx,
        denied_log=denied_log,
    )
    store.close()


def run(coro):
    return asyncio.run(coro)


def repo_row(studio, store, root: Path, *, repo_id: str = "r_1", engine_version: str = "2.6.2", **over):
    row = {
        "id": repo_id,
        "canonical_path": str(root),
        "label": repo_id,
        "added_at": TS,
        "platform": "darwin",
        "availability": "available",
        "install_status": "installed",
        "engine_dir": ".kiro",
        "installed_engine_version": engine_version,
        "resolved_identity": IDENTITY,
    }
    row.update(over)
    store.insert("repos", row)
    return studio.repo_registry.RepoRecord.from_row(store.get("repos", repo_id))


def request(P, scope: str = "poc", depth: str = "", **over):
    body = {
        "space": "default",
        "scope": scope,
        "depth": depth,
        "objective": "Ship a monthly ledger export",
        "label": "ledger-export",
    }
    body.update(over)
    return P.PlanRequest.from_json(body, repo_id="r_1")


#: source name -> (the shipped tree, its stage count). These are the two graph generations a plan is
#: computed against; the trees themselves are build artefacts, never a repository root — see
#: ``_engine_tree``.
ENGINE_TREES = {
    "payload": (APP_ROOT / "payload" / "aidlc-kiro", 33),
    "payload230": (APP_ROOT / "tests" / "fixtures" / "aidlc-2.3.0-payload", 32),
}
#: One copy of each tree per test process, made on first use and removed at exit.
_TREE_COPIES: dict[str, Path] = {}


def _engine_tree(source: str) -> tuple[Path, int]:
    """A throwaway copy of a shipped engine tree, plus its stage count.

    Every test here either only *reads* the graph or is refused before an engine verb could run, so
    nothing should ever be written — but "should" is the whole problem. If one of those refusals
    regresses, ``create_intent`` reaches ``fake_bun``, whose ``intent-create`` writes
    ``aidlc/spaces/<space>/intents/<dir>/`` under the repository root. Pointed at
    ``payload/aidlc-kiro`` that silently corrupts the artefact the installer ships and
    ``scripts/check.sh``'s payload-integrity step verifies against ``payload/manifest.json`` — and it
    corrupts it *after* that step has already run, so the damage first surfaces on some later,
    unrelated run. A suite must not be able to damage the thing it is testing, so the tests get a copy
    and a regression writes into ``/tmp``. The copy is byte-identical, so every golden count and every
    pinned shape still reads the real 2.7.1 (or 2.3.0) engine.
    """
    root, stage_count = ENGINE_TREES[source]
    copy = _TREE_COPIES.get(source)
    if copy is None:
        base = Path(tempfile.mkdtemp(prefix=f"aidlc-plan-{source}-"))
        atexit.register(shutil.rmtree, base, True)
        copy = base / root.name
        shutil.copytree(root, copy, symlinks=True)
        _TREE_COPIES[source] = copy
    return copy, stage_count


def engine_only_repo(studio, deps, source: str, *, repo_id: str = "r_1", **over):
    """A ``RepoRecord`` whose root is a copy of a shipped engine tree (``_engine_tree``)."""
    root, _count = _engine_tree(source)
    return repo_row(studio, deps.store, root, repo_id=repo_id, **over)


# --------------------------------------------------------------------------- #
# exact counts — golden per scope on both graph generations
# --------------------------------------------------------------------------- #

#: (source, scope) -> (stages, gates, artifacts, review_intensity). Computed from the installed
#: `stage-graph.json` + `scope-grid.json` and pinned here: these are the numbers FR-EST-001 calls exact,
#: so any drift must be a visible diff, not a silently different promise.
GOLDEN_COUNTS = {
    ("payload", "poc"): (8, 5, 25, {"none": 5, "advisory": 3, "adversarial": 0}),
    ("payload", "feature"): (33, 30, 128, {"none": 20, "advisory": 8, "adversarial": 5}),
    # 2.7.1 flips `deployment-pipeline` and `deployment-execution` from SKIP to EXECUTE for both
    # `bugfix` and `refactor`: a fix you cannot ship is not a fix. `refactor` declares no `review_cap`,
    # so its two adversarial stages stay adversarial where `bugfix` caps everything at advisory.
    ("payload", "bugfix"): (9, 6, 30, {"none": 7, "advisory": 2, "adversarial": 0}),
    ("payload", "refactor"): (10, 7, 34, {"none": 7, "advisory": 1, "adversarial": 2}),
    # The 32-stage graph has no `review_class` key at all: a reviewer alone means "advisory".
    ("payload230", "poc"): (8, 5, 23, {"none": 6, "advisory": 2, "adversarial": 0}),
    ("payload230", "feature"): (32, 29, 120, {"none": 21, "advisory": 11, "adversarial": 0}),
    ("payload230", "bugfix"): (7, 4, 20, {"none": 5, "advisory": 2, "adversarial": 0}),
}


@pytest.mark.parametrize("source, scope", sorted(GOLDEN_COUNTS))
def test_exact_counts_are_golden(studio, P, deps, source, scope):
    repo = engine_only_repo(studio, deps, source)
    plan = deps.svc.effective_plan(repo, ".kiro", request(P, scope))
    stages, gates, artifacts, intensity = GOLDEN_COUNTS[(source, scope)]
    assert plan.valid, [i.to_json() for i in plan.issues]
    assert plan.exact.stages == stages
    assert plan.exact.gates == gates
    assert plan.exact.artifacts == artifacts
    assert plan.exact.review_intensity == intensity
    assert plan.graph_stage_count == ENGINE_TREES[source][1]
    assert sum(intensity.values()) == stages
    # Gates are every enabled stage outside initialization; the three init stages are never gates.
    assert stages - gates == 3
    # `assumes_units` discloses the fan-out guess a plan with no intent has to make.
    assert plan.exact.assumes_units == 1
    assert plan.estimate.turns.low > 0 and plan.estimate.turns.high >= plan.estimate.turns.low


def test_counts_track_the_unit_fan_out(studio, P, deps):
    repo = engine_only_repo(studio, deps, "payload")
    plan = deps.svc.effective_plan(repo, ".kiro", request(P, "poc"))
    per_unit = [s for s in plan.stages if s.enabled and s.per_unit]
    assert [s.slug for s in per_unit] == ["code-generation"]
    assert plan.exact.artifacts == 25  # units unknown → priced for one


def test_products_are_the_union_of_enabled_produces(studio, P, deps):
    repo = engine_only_repo(studio, deps, "payload")
    plan = deps.svc.effective_plan(repo, ".kiro", request(P, "feature"))
    enabled = [s for s in plan.stages if s.enabled]
    assert set(plan.products) == {a for s in enabled for a in s.produces}
    # `artifacts` counts occurrences, `products` distinct names: duplicates prove they are not the same.
    assert len(plan.products) < plan.exact.artifacts


def test_review_cap_only_ever_reduces_review_work(studio, P, deps):
    repo = engine_only_repo(studio, deps, "payload")
    uncapped = deps.svc.effective_plan(repo, ".kiro", request(P, "feature"))
    capped = deps.svc.effective_plan(repo, ".kiro", request(P, "feature", review_cap="advisory"))
    raised = deps.svc.effective_plan(repo, ".kiro", request(P, "feature", review_cap="adversarial"))
    assert uncapped.exact.review_intensity["adversarial"] == 5
    assert capped.exact.review_intensity["adversarial"] == 0
    assert capped.exact.review_intensity["advisory"] == 8 + 5
    # A request cannot raise a stage above its own class: the cap is a minimum, not an assignment.
    assert raised.exact.review_intensity == uncapped.exact.review_intensity
    # A cheaper review shows up in the estimate, not only in the counts.
    assert capped.estimate.turns.high < uncapped.estimate.turns.high


def test_scope_depth_comes_from_the_scope_when_the_request_omits_it(studio, P, deps):
    repo = engine_only_repo(studio, deps, "payload")
    implicit = deps.svc.effective_plan(repo, ".kiro", request(P, "poc"))
    explicit = deps.svc.effective_plan(repo, ".kiro", request(P, "poc", depth="Minimal"))
    assert implicit.scope_meta is not None and implicit.scope_meta.depth == "Minimal"
    assert implicit.estimate.turns == explicit.estimate.turns
    assert implicit.valid


# --------------------------------------------------------------------------- #
# lock reasons
# --------------------------------------------------------------------------- #


def test_always_stages_are_locked_only_while_selected(studio, P, deps):
    """``poc`` SKIPs four ``execution: ALWAYS`` stages; locking those would make them unaddable."""
    repo = engine_only_repo(studio, deps, "payload")
    plan = deps.svc.effective_plan(repo, ".kiro", request(P, "poc"))
    assert plan.stage("code-generation").lock_reason == P.LOCK_ALWAYS
    skipped_always = [
        s for s in plan.stages if s.execution == "ALWAYS" and not s.enabled
    ]
    assert {s.slug for s in skipped_always} == {
        "scope-definition", "approval-handoff", "units-generation", "delivery-planning"
    }
    assert all(s.lock_reason is None and not s.locked for s in skipped_always)


@pytest.mark.parametrize("scope", ["poc", "express"])
def test_optional_always_stage_can_be_unselected_before_plan_creation(studio, P, deps, scope):
    repo = engine_only_repo(studio, deps, "payload")
    baseline = deps.svc.effective_plan(repo, ".kiro", request(P, scope, project_type="Greenfield"))
    optional = [s.slug for s in baseline.stages if s.execution == "ALWAYS" and not s.enabled]
    assert optional
    for slug in optional:
        selected = deps.svc.effective_plan(
            repo, ".kiro", request(P, scope, project_type="Greenfield", overrides={slug: True})
        )
        assert selected.stage(slug).enabled
        assert not selected.stage(slug).locked, (slug, selected.stage(slug).lock_reason)
        cancelled = deps.svc.effective_plan(
            repo, ".kiro", request(P, scope, project_type="Greenfield", overrides={slug: False})
        )
        assert not cancelled.stage(slug).enabled
        assert cancelled.exact == baseline.exact
        assert cancelled.valid == baseline.valid
        assert cancelled.stage("code-generation").lock_reason == P.LOCK_ALWAYS


def test_sole_producer_of_a_required_input_is_dependency_locked(studio, P, deps):
    repo = engine_only_repo(studio, deps, "payload")
    plan = deps.svc.effective_plan(repo, ".kiro", request(P, "feature"))
    domain = plan.stage("domain-design")
    assert domain.lock_reason == P.lock_required_by("units-generation")
    assert domain.locked
    # A stage nobody depends on for a required input stays adjustable, or the matrix would be inert.
    assert plan.stage("performance-validation").lock_reason is None


def test_optional_stage_still_locks_inputs_for_a_new_consumer(studio, P, deps):
    repo = engine_only_repo(studio, deps, "payload")
    both = deps.svc.effective_plan(repo, ".kiro", request(
        P, "poc", project_type="Greenfield",
        overrides={"domain-design": True, "units-generation": True},
    ))
    assert both.stage("domain-design").lock_reason == P.lock_required_by("units-generation")
    removed_consumer = deps.svc.effective_plan(repo, ".kiro", request(
        P, "poc", project_type="Greenfield", overrides={"domain-design": True},
    ))
    assert not removed_consumer.stage("domain-design").locked
    invalid = deps.svc.effective_plan(repo, ".kiro", request(
        P, "poc", project_type="Greenfield", overrides={"units-generation": True},
    ))
    assert not invalid.valid
    assert any(issue.code == "dependency_missing" for issue in invalid.issues)


def test_row_state_locks_win_over_the_graph_declaration(studio, P, deps, live_repo):
    plan = live_repo.plan()
    assert plan.stage("workspace-scaffold").lock_reason == P.LOCK_COMPLETED
    assert plan.stage("requirements-analysis").lock_reason == P.LOCK_AT_GATE
    assert plan.stage("units-generation").lock_reason == P.LOCK_ALWAYS
    # `market-research` was reset to `[ ]` behind the cursor: position, not stage number, decides.
    assert plan.stage("market-research").lock_reason == P.LOCK_BEHIND_CURSOR
    assert plan.stage("market-research").state == "not_started"


def test_a_row_the_installed_graph_does_not_know_is_locked_not_in_graph(studio, P, deps, repo_builder):
    """A v7 state (``application-design``) under the 33-stage engine (``domain-design``+``contract-design``)."""
    import fixtures as F

    state = F.state_text(F.PAYLOAD_GATE_OPEN, tree=F.PAYLOAD_230)
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent("260901-ledger-export", state=state, scope="feature")
        .build()
    )
    repo = repo_row(studio, deps.store, root)
    snap = deps.projection.snapshot(repo, "default", "260901-ledger-export")
    plan = deps.svc.effective_plan(
        repo, ".kiro", deps.svc._request_from_state(repo, snap, skip=(), add=()), snap=snap
    )
    orphan = plan.stage("application-design")
    assert orphan is not None
    assert orphan.lock_reason == P.LOCK_NOT_IN_GRAPH
    assert orphan.number == "" and orphan.produces == ()
    # It still runs, so it still counts — hiding it would understate the plan.
    assert orphan.enabled and orphan.in_grid
    # …and the plan stays committable: an engine-generation skew is not the user's error.
    assert plan.valid, [i.to_json() for i in plan.issues]


# --------------------------------------------------------------------------- #
# overrides and validation
# --------------------------------------------------------------------------- #


def test_override_adds_a_stage_and_shows_it_in_the_diff(studio, P, deps):
    repo = engine_only_repo(studio, deps, "payload")
    plan = deps.svc.effective_plan(repo, ".kiro", request(P, "poc", overrides={"ci-pipeline": True}))
    assert plan.valid
    assert plan.exact.stages == 9 and plan.exact.gates == 6
    assert [(d.slug, d.from_enabled, d.to_enabled, d.reason) for d in plan.diff] == [
        ("ci-pipeline", False, True, P.DIFF_REASON_OVERRIDE)
    ]
    assert plan.stage("ci-pipeline").enabled and not plan.stage("ci-pipeline").in_grid


def test_override_that_starves_a_required_input_is_a_dependency_issue(studio, P, deps):
    repo = engine_only_repo(studio, deps, "payload")
    plan = deps.svc.effective_plan(repo, ".kiro", request(P, "poc", overrides={"units-generation": True}))
    assert not plan.valid
    issue = plan.issues[0]
    assert issue.code == "dependency_missing"
    assert issue.slugs == ("units-generation", "domain-design")
    assert issue.params == {"stage": "units-generation", "artifact": "components",
                            "producers": ["domain-design"]}
    assert issue.message_key == "plan.issue.dependency_missing"


def test_starvation_the_scope_was_born_with_is_not_an_error(studio, P, deps):
    """``poc`` runs ``code-generation`` while skipping ``units-generation``, whose output it consumes."""
    repo = engine_only_repo(studio, deps, "payload")
    plan = deps.svc.effective_plan(repo, ".kiro", request(P, "poc"))
    assert plan.valid
    assert plan.stage("code-generation").enabled
    assert not plan.stage("units-generation").enabled
    assert "unit-of-work" in plan.stage("code-generation").consumes


def test_disabling_a_locked_stage_is_refused_with_the_lock_reason(studio, P, deps):
    repo = engine_only_repo(studio, deps, "payload")
    always = deps.svc.effective_plan(repo, ".kiro", request(P, "poc", overrides={"code-generation": False}))
    assert not always.valid
    assert always.issues[0].code == "required_stage_disabled"
    assert always.issues[0].params["lock_reason"] == P.LOCK_ALWAYS
    # the override is IGNORED, not applied: a refused toggle must not change the plan
    assert always.stage("code-generation").enabled
    assert always.diff == ()

    needed = deps.svc.effective_plan(
        repo, ".kiro", request(P, "feature", overrides={"domain-design": False})
    )
    assert needed.issues[0].code == "required_stage_disabled"
    assert needed.issues[0].params["lock_reason"] == P.lock_required_by("units-generation")


def test_override_naming_an_unknown_stage(studio, P, deps):
    repo = engine_only_repo(studio, deps, "payload")
    plan = deps.svc.effective_plan(repo, ".kiro", request(P, "poc", overrides={"not-a-stage": True}))
    assert [(i.code, i.slugs) for i in plan.issues] == [("unknown_stage", ("not-a-stage",))]
    assert not plan.valid


def test_unknown_scope_and_invalid_depth(studio, P, deps):
    repo = engine_only_repo(studio, deps, "payload")
    unknown = deps.svc.effective_plan(repo, ".kiro", request(P, "no-such-scope"))
    assert [i.code for i in unknown.issues] == ["scope_unknown"]
    assert unknown.exact.stages == 0
    assert unknown.estimate.elapsed_secs is None

    bad_depth = deps.svc.effective_plan(repo, ".kiro", request(P, "poc", depth="Deep"))
    assert "depth_invalid" in [i.code for i in bad_depth.issues]
    assert not bad_depth.valid


def test_from_json_refuses_a_malformed_override_map(P, errors):
    with pytest.raises(errors.StudioError) as excinfo:
        P.PlanRequest.from_json({"scope": "poc", "overrides": {"ci-pipeline": "yes"}}, repo_id="r_1")
    assert excinfo.value.code == "bad_body"
    with pytest.raises(errors.StudioError):
        P.PlanRequest.from_json({"scope": "poc", "overrides": {"Not A Slug": True}}, repo_id="r_1")
    with pytest.raises(errors.StudioError):
        P.PlanRequest.from_json({"scope": "poc", "overrides": []}, repo_id="r_1")


def test_request_json_omits_the_path_parameter(P):
    body = P.PlanRequest.from_json({"scope": "poc", "depth": "Minimal"}, repo_id="r_1").to_json()
    assert "repo_id" not in body
    assert set(body) == {"space", "scope", "depth", "test_strategy", "review_cap", "project_type",
                         "overrides", "objective", "label", "context"}


def test_stage_json_is_the_wire_shape_only(studio, P, deps):
    """§3.1 pins ``PlanStage``; the estimator's two extra fields must not leak onto the wire."""
    repo = engine_only_repo(studio, deps, "payload")
    plan = deps.svc.effective_plan(repo, ".kiro", request(P, "poc"))
    row = plan.stage("code-generation")
    assert row.mode == "subagent" and row.summary_confirmation is None
    assert set(row.to_json()) == {
        "slug", "number", "name", "phase", "execution", "in_grid", "enabled", "locked", "lock_reason",
        "gate", "review_class", "reviewer", "per_unit", "produces", "consumes", "depends_on",
        "conditional_on", "state",
    }
    payload = plan.to_json()
    assert set(payload) == {"request", "scope_meta", "stages", "exact", "estimate", "diff", "issues",
                            "valid", "products", "graph_stage_count", "engine_version"}
    assert payload["engine_version"] == "2.6.2"


# --------------------------------------------------------------------------- #
# recompose preview
# --------------------------------------------------------------------------- #


@pytest.fixture
def live_repo(studio, deps, repo_builder):
    """A Running intent at an open gate, with one pending row reset behind the cursor."""
    import fixtures as F

    state = F.state_text(F.PAYLOAD_GATE_OPEN, tree=F.PAYLOAD_230, marks={"market-research": " "})
    root = (
        repo_builder.with_engine("payload230")
        .with_workspace()
        .with_intent(
            "260901-ledger-export",
            state=state,
            scope="feature",
            audit=F.gate_open_audit("requirements-analysis"),
        )
        .build()
    )
    record = repo_row(studio, deps.store, root, engine_version="2.3.0")

    class Live:
        repo = record
        builder = repo_builder
        intent_dir = "260901-ledger-export"

        def snap(self):
            return deps.projection.snapshot(self.repo, "default", self.intent_dir)

        def plan(self, snap=None):
            snapshot = snap or self.snap()
            return deps.svc.effective_plan(
                self.repo,
                ".kiro",
                deps.svc._request_from_state(self.repo, snapshot, skip=(), add=()),
                snap=snapshot,
            )

        def preview(self, *, skip=(), add=(), snap=None):
            return deps.svc.recompose_preview(self.repo, snap or self.snap(), skip=list(skip), add=list(add))

    return Live()


def test_live_plan_baseline_is_the_state_file(studio, P, deps, live_repo):
    plan = live_repo.plan()
    assert plan.valid
    assert plan.exact.stages == 32
    assert plan.request.scope == "feature"
    assert plan.request.depth == "Standard"
    assert all(s.state is not None for s in plan.stages)


def test_recompose_allows_a_pending_ahead_of_cursor_stage(studio, P, deps, live_repo):
    proposal = live_repo.preview(skip=["user-stories"])
    assert proposal.allowed
    assert proposal.refusals == ()
    assert proposal.skip == ("user-stories",) and proposal.add == ()
    assert proposal.current_stage == "requirements-analysis"
    assert proposal.argv_preview == ("aidlc-utility.ts", "recompose", "--skip", "user-stories")
    # The preview never names a host path: it is rendered in a browser.
    assert all("/" not in token for token in proposal.argv_preview)
    assert proposal.plan.exact.stages == 31
    assert [(d.slug, d.to_enabled) for d in proposal.plan.diff] == [("user-stories", False)]
    assert [c.slug for c in proposal.plan.estimate.coverage_lost] == ["user-stories"]


@pytest.mark.parametrize(
    "slug, code",
    [
        ("workspace-scaffold", "frozen_stage"),        # completed
        ("requirements-analysis", "frozen_stage"),     # at its gate
        ("market-research", "behind_cursor"),          # pending but behind the cursor
        ("units-generation", "required_stage_disabled"),  # execution ALWAYS
        ("domain-design", "unknown_stage"),            # not in the 32-stage graph
        ("nope", "unknown_stage"),
    ],
)
def test_recompose_refuses_everything_outside_pending_ahead_of_cursor(deps, live_repo, slug, code):
    proposal = live_repo.preview(skip=[slug])
    assert not proposal.allowed
    assert code in [r.code for r in proposal.refusals], [r.to_json() for r in proposal.refusals]


def test_recompose_refuses_a_flip_that_moves_the_skeleton_anchor(studio, P, deps, repo_builder):
    import fixtures as F

    state = F.state_text(
        F.PAYLOAD_GATE_OPEN, tree=F.PAYLOAD_230, modes={"functional-design": "SKIP (recomposed)"}
    )
    root = (
        repo_builder.with_engine("payload230")
        .with_workspace()
        .with_intent("i1", state=state, scope="feature", audit=F.gate_open_audit("requirements-analysis"))
        .build()
    )
    repo = repo_row(studio, deps.store, root, engine_version="2.3.0")
    snap = deps.projection.snapshot(repo, "default", "i1")
    proposal = deps.svc.recompose_preview(repo, snap, skip=[], add=["functional-design"])
    assert not proposal.allowed
    anchor = [r for r in proposal.refusals if r.code == "skeleton_anchor"]
    assert anchor and anchor[0].params == {
        "anchor_before": "nfr-requirements", "anchor_after": "functional-design"
    }


def test_recompose_refuses_when_the_workflow_is_not_running(studio, deps, live_repo):
    import fixtures as F

    live_repo.builder.mutate_state(
        live_repo.intent_dir,
        lambda _t: F.state_text(F.PAYLOAD_GATE_OPEN, tree=F.PAYLOAD_230, fields={"Status": "Paused"}),
    )
    proposal = live_repo.preview(skip=["user-stories"])
    assert not proposal.allowed
    refusal = [r for r in proposal.refusals if r.code == "recompose_not_allowed"][0]
    assert refusal.params["status"] == "Paused"
    assert refusal.params["required_status"] == "Running"


def test_recompose_refuses_autonomous_construction(studio, deps, live_repo):
    import fixtures as F

    live_repo.builder.mutate_state(
        live_repo.intent_dir,
        lambda text: F.insert_runtime_field(text, "Construction Autonomy Mode", "autonomous"),
    )
    proposal = live_repo.preview(skip=["user-stories"])
    assert not proposal.allowed
    refusal = [r for r in proposal.refusals if r.code == "recompose_not_allowed"][0]
    assert refusal.params["autonomy_mode"] == "autonomous"


def test_recompose_with_no_flip_is_not_allowed(deps, live_repo):
    assert not live_repo.preview().allowed


def test_proposal_digest_binds_the_state_bytes_and_the_flips(deps, live_repo):
    first = live_repo.preview(skip=["user-stories"])
    assert first.digest() == live_repo.preview(skip=["user-stories"]).digest()
    assert first.digest() != live_repo.preview(skip=["refined-mockups"]).digest()
    live_repo.builder.append_audit(live_repo.intent_dir, "\n")  # audit is not part of the digest
    assert first.digest() == live_repo.preview(skip=["user-stories"]).digest()
    live_repo.builder.mutate_state(live_repo.intent_dir, lambda text: text.replace(
        "- [ ] refined-mockups", "- [x] refined-mockups"))
    assert first.digest() != live_repo.preview(skip=["user-stories"]).digest()


def test_malformed_slug_is_refused_before_anything_runs(deps, live_repo, errors):
    with pytest.raises(errors.StudioError) as excinfo:
        live_repo.preview(skip=["../escape"])
    assert excinfo.value.code == "bad_param"


# --------------------------------------------------------------------------- #
# create_intent
# --------------------------------------------------------------------------- #


@pytest.fixture
def fresh_repo(studio, deps, repo_builder):
    """A repository with the bundled engine and an empty workspace: ready for intent-create."""
    root = repo_builder.with_engine("payload").with_workspace().build()
    return repo_row(studio, deps.store, root)


def test_create_intent_runs_one_verb_verifies_disk_and_starts_nothing(studio, P, deps, fresh_repo,
                                                                      fake_host):
    result = run(deps.svc.create_intent(fresh_repo, request(P, "poc", depth="Minimal")))

    assert result.intent_dir == "260904-ledger-export"
    assert result.intent_key == "260904-ledger-export"  # default space carries no prefix
    assert result.space == "default"
    assert result.slug == "ledger-export"
    assert result.uuid is not None
    assert result.transaction_id.startswith("tx_")
    assert result.verified == {
        "state_present": True, "registry_row": True, "cursor_set": True, "state_version": None,
        "runtime_graph_present": True,
    }

    # The returned creation invocation carries the plan's own settings; compilation is derived work.
    argv = list(result.engine.argv)
    assert result.engine.verb_key == "utility.intent_create"
    assert argv[argv.index("intent-create") + 1 :][:2] == ["--scope", "poc"]
    assert "--label" in argv and "ledger-export" in argv
    assert "--depth" in argv and "Minimal" in argv
    assert result.engine.ok

    # nothing was sent anywhere: no prompt, no host call, no human turn on disk
    record = Path(fresh_repo.canonical_path) / "aidlc" / "spaces" / "default" / "intents" / result.intent_dir
    audit = "\n".join(p.read_text("utf-8") for p in (record / "audit").glob("*.md"))
    assert "HUMAN_TURN" not in audit
    assert studio.constants.WIRE_RUN not in audit
    assert fake_host.notifications == []
    assert not any(slot.messages for slot in fake_host._slots.values())
    assert not deps.denied_log.exists(), deps.denied_log.read_text()

    # the new intent is inert
    assert result.summary is not None
    assert result.summary.operational_state == "Idle"


def test_create_intent_releases_the_admin_lease_and_records_it(studio, P, deps, fresh_repo):
    run(deps.svc.create_intent(fresh_repo, request(P, "poc")))
    assert run(deps.scheduler.get(IDENTITY)) is None
    kinds = [row["kind"] for row in deps.store.select("activity", order_by="id ASC")]
    assert kinds.count("intent.created") == 1
    assert "lease.acquired" in kinds and "lease.released" in kinds
    assert "engine.run" in kinds
    created = [row for row in deps.store.select("activity") if row["kind"] == "intent.created"][0]
    assert created["message_key"] == "activity.intent.created"
    assert created["params_json"]["scope"] == "poc"
    published = [row["type"] for row in deps.store.select("events", order_by="seq ASC")]
    assert published == ["intent.updated"]
    assert deps.ctx.events.published[-1][0] == studio.constants.HOST_EVENT_REPO


def test_create_intent_refuses_while_the_repository_is_leased(studio, P, deps, fresh_repo, errors):
    run(deps.scheduler.acquire_admin(fresh_repo, operation_type="install", transaction_id="tx_other"))
    with pytest.raises(errors.StudioError) as excinfo:
        run(deps.svc.create_intent(fresh_repo, request(P, "poc")))
    assert excinfo.value.code == "repo_busy"
    # nothing was created and no second lease exists
    assert deps.reader.list_intent_dirs(Path(fresh_repo.canonical_path), "default") == []


@pytest.mark.parametrize(
    "over, code",
    [
        ({"install_status": "not_installed"}, "not_installed"),
        ({"install_status": "recovery_required"}, "install_recovery_required"),
        ({"engine_dir": None}, "not_installed"),
        ({"availability": "moved"}, "repo_unavailable"),
        ({"availability": "identity_unprovable"}, "identity_unprovable"),
    ],
)
def test_create_intent_refuses_a_repository_that_cannot_host_a_verb(studio, P, deps, over, code,
                                                                    errors):
    """Refused before a lease is taken, so the fixture engine tree is never even opened for writing."""
    repo = engine_only_repo(studio, deps, "payload", repo_id="r_2", **over)
    with pytest.raises(errors.StudioError) as excinfo:
        run(deps.svc.create_intent(repo, request(P, "poc")))
    assert excinfo.value.code == code
    assert run(deps.scheduler.get(IDENTITY)) is None


def test_create_intent_refuses_blocking_findings(studio, P, deps, fresh_repo, errors):
    finding = SimpleNamespace(
        severity="blocking",
        to_json=lambda: {"code": "duplicate_identity", "severity": "blocking"},
    )
    with pytest.raises(errors.StudioError) as excinfo:
        run(deps.svc.create_intent(fresh_repo, request(P, "poc"), blocking_findings=[finding]))
    assert excinfo.value.code == "state_inconsistent"
    assert excinfo.value.details["findings"][0]["code"] == "duplicate_identity"


@pytest.mark.parametrize(
    "over, code",
    [
        ({"objective": None}, "bad_body"),
        ({"objective": "   "}, "bad_body"),
        ({"label": "Not A Label"}, "bad_body"),
        ({"label": "one-two-three-four"}, "bad_body"),
        ({"space": "other"}, "bad_param"),
        ({"scope": "no-such-scope"}, "plan_invalid"),
        ({"overrides": {"units-generation": True}}, "plan_invalid"),
    ],
)
def test_create_intent_refuses_a_bad_request(studio, P, deps, over, code, errors):
    """Every one of these is refused before the engine runs — hence the read-only engine tree."""
    repo = engine_only_repo(studio, deps, "payload")
    with pytest.raises(errors.StudioError) as excinfo:
        run(deps.svc.create_intent(repo, request(P, **over)))
    assert excinfo.value.code == code
    assert deps.reader.list_intent_dirs(Path(repo.canonical_path), "default") == []
    assert run(deps.scheduler.get(IDENTITY)) is None


def test_create_intent_derives_a_label_from_the_objective(studio, P, deps, fresh_repo):
    result = run(deps.svc.create_intent(
        fresh_repo, request(P, "poc", label=None, objective="Ship the ledger export now")
    ))
    assert result.intent_dir.endswith("ship-the-ledger")


def test_create_intent_refuses_an_objective_that_slugifies_to_nothing(studio, P, deps, fresh_repo,
                                                                      errors):
    with pytest.raises(errors.StudioError) as excinfo:
        run(deps.svc.create_intent(fresh_repo, request(P, "poc", label=None, objective="贡献证据闭环")))
    assert excinfo.value.code == "bad_body"
    assert excinfo.value.details["key"] == "label"


def test_create_intent_honours_the_plan_confirmation_digest(studio, P, deps, fresh_repo, errors):
    """§2.3's ``confirm_plan_digest``: an intent is never created from a plan the user did not see."""
    req = request(P, "poc")
    plan = deps.svc.effective_plan(fresh_repo, ".kiro", req)
    assert plan.digest() == deps.svc.effective_plan(fresh_repo, ".kiro", req).digest()
    assert plan.digest() != deps.svc.effective_plan(
        fresh_repo, ".kiro", request(P, "poc", overrides={"ci-pipeline": True})
    ).digest()

    with pytest.raises(errors.StudioError) as excinfo:
        run(deps.svc.create_intent(fresh_repo, req, confirm_plan_digest="0" * 64))
    assert excinfo.value.code == "plan_invalid"
    assert excinfo.value.details["reason"] == "plan_digest_mismatch"
    assert deps.reader.list_intent_dirs(Path(fresh_repo.canonical_path), "default") == []

    result = run(deps.svc.create_intent(fresh_repo, req, confirm_plan_digest=plan.digest()))
    assert result.verified["state_present"]


def test_create_intent_sends_the_objective_and_context_as_one_argument(studio, P, deps, fresh_repo):
    result = run(deps.svc.create_intent(
        fresh_repo, request(P, "poc", objective="Export the ledger", context="Billing service only")
    ))
    argv = list(result.engine.argv)
    assert argv[argv.index("--arguments") + 1] == "Export the ledger\n\nBilling service only"


def test_a_well_formed_pasted_document_is_sent_through_untouched(studio, P, deps, fresh_repo):
    """Directions, then one `<document>` block at the end: exactly the shape the engine accepts."""
    paste = "<document>\nthe spec, pasted\n</document>"
    result = run(deps.svc.create_intent(
        fresh_repo, request(P, "poc", objective="Turn this spec into a plan", context=paste)
    ))
    argv = list(result.engine.argv)
    assert argv[argv.index("--arguments") + 1] == "Turn this spec into a plan\n\n" + paste
    assert result.verified["state_present"]


@pytest.mark.parametrize(
    "over, key, fragment",
    [
        ({"context": "<document>\nhalf a spec"}, "context", "without a matching </document>"),
        ({"context": "the spec ends here</document>"}, "context", "</document> without a matching"),
        ({"context": "<document>a<document>b</document></document>"}, "context", "nested"),
        ({"context": "<document>a</document> and then hurry"}, "context", "content after the closing"),
        ({"objective": "<document>a</document>"}, "objective", "no directions outside it"),
    ],
)
def test_create_intent_refuses_a_malformed_document_paste_before_the_lease(studio, P, deps, fresh_repo,
                                                                          errors, over, key, fragment):
    """2.7.1 die()s on these AFTER the lease is taken, so the user would get `state_inconsistent`.

    ``intent-create`` scans ``--arguments`` for `<document>` markers and refuses an unbalanced, nested or
    directions-free paste (aidlc-utility.ts:5468-5487 over ``authoritativeProjectDescription``). By then
    Studio holds the admin lease and the engine has already appended an ERROR_LOGGED row, which is a
    frightening answer to a typo in a textarea. Refused here instead: a 400 that names the field to fix,
    with no lease taken and no record touched.
    """
    # A repository of its own, not the read-only engine tree: if this refusal ever regresses, the verb
    # runs against whatever `canonical_path` names and creates a record there. In a tmp tree that is a
    # failed assertion; in the bundled payload it would be four stray files inside the shipped bytes.
    with pytest.raises(errors.StudioError) as excinfo:
        run(deps.svc.create_intent(fresh_repo, request(P, "poc", **over)))
    assert excinfo.value.code == "bad_body"
    assert excinfo.value.details["key"] == key
    assert fragment in excinfo.value.details["reason"]
    assert deps.reader.list_intent_dirs(Path(fresh_repo.canonical_path), "default") == []
    assert run(deps.scheduler.get(IDENTITY)) is None
    assert not deps.denied_log.exists()


# --------------------------------------------------------------------------- #
# recompose / scope-change / config-change
# --------------------------------------------------------------------------- #


def test_recompose_applies_the_flip_and_verifies_the_state_file(studio, deps, live_repo):
    proposal = live_repo.preview(skip=["user-stories"])
    result = run(
        deps.svc.recompose(
            live_repo.repo, live_repo.snap(), skip=["user-stories"], add=[],
            proposal_digest=proposal.digest(),
        )
    )
    assert result.engine.verb_key == "utility.recompose"
    argv = list(result.engine.argv)
    assert argv[argv.index("--skip") + 1] == "user-stories"
    assert result.verified["skipped"] == {"user-stories": True}
    assert result.verified["missing_rows"] == []
    assert result.transaction_id.startswith("tx_")
    assert run(deps.scheduler.get(IDENTITY)) is None
    # the flip is on disk, not merely reported
    plan = live_repo.plan()
    assert not plan.stage("user-stories").enabled
    assert [row["type"] for row in deps.store.select("events")] == ["intent.updated"]
    assert not deps.denied_log.exists()


def test_recompose_refuses_a_stale_proposal(studio, deps, live_repo, errors):
    proposal = live_repo.preview(skip=["user-stories"])
    live_repo.builder.mutate_state(
        live_repo.intent_dir, lambda text: text.replace("- [ ] refined-mockups", "- [x] refined-mockups")
    )
    with pytest.raises(errors.StudioError) as excinfo:
        run(deps.svc.recompose(live_repo.repo, live_repo.snap(), skip=["user-stories"], add=[],
                               proposal_digest=proposal.digest()))
    assert excinfo.value.code == "plan_invalid"
    assert excinfo.value.details["reason"] == "proposal_digest_mismatch"
    assert live_repo.plan().stage("user-stories").enabled


def test_recompose_refuses_a_disallowed_flip(studio, deps, live_repo, errors):
    proposal = live_repo.preview(skip=["market-research"])
    with pytest.raises(errors.StudioError) as excinfo:
        run(deps.svc.recompose(live_repo.repo, live_repo.snap(), skip=["market-research"], add=[],
                               proposal_digest=proposal.digest()))
    assert excinfo.value.code == "recompose_not_allowed"
    assert [r["code"] for r in excinfo.value.details["refusals"]] == ["behind_cursor"]
    assert run(deps.scheduler.get(IDENTITY)) is None


def test_scope_change_and_config_change_run_under_a_lease(studio, deps, live_repo, errors):
    scope = run(deps.svc.scope_change(live_repo.repo, live_repo.snap(), scope="mvp", depth="Comprehensive"))
    assert scope.verb_key == "utility.scope_change"
    argv = list(scope.argv)
    assert argv[argv.index("--scope") + 1] == "mvp"
    assert argv[argv.index("--depth") + 1] == "Comprehensive"
    assert run(deps.scheduler.get(IDENTITY)) is None

    config = run(deps.svc.config_change(live_repo.repo, live_repo.snap(), depth="Minimal"))
    assert config.verb_key == "utility.config_change"
    assert "--test-strategy" not in list(config.argv)

    with pytest.raises(errors.StudioError) as excinfo:
        run(deps.svc.config_change(live_repo.repo, live_repo.snap()))
    assert excinfo.value.code == "bad_param"
    with pytest.raises(errors.StudioError):
        run(deps.svc.config_change(live_repo.repo, live_repo.snap(), depth="Deep"))
    with pytest.raises(errors.StudioError):
        run(deps.svc.scope_change(live_repo.repo, live_repo.snap(), scope="Not A Scope"))


def test_every_mutating_verb_names_the_record_it_was_asked_about(studio, deps, live_repo):
    """`--intent`/`--space` come from the snapshot, so the engine never resolves a record of its own.

    Everything else about these three calls — the lease, the preview, the proposal digest, the on-disk
    verification, the activity row, the event — is about the intent the request named. With the selector
    omitted the engine picks the record itself (2.7.1 asks ``resolveWorkflowSelection`` first, so the
    calling session's binding wins over the cursor), which is how a confirmed change lands on a record
    nobody was shown. Both flags are asserted because `--intent` alone still takes the space from the
    binding.
    """
    snap = live_repo.snap()
    proposal = live_repo.preview(skip=["user-stories"])
    recomposed = run(
        deps.svc.recompose(
            live_repo.repo, snap, skip=["user-stories"], add=[], proposal_digest=proposal.digest()
        )
    )
    scoped = run(deps.svc.scope_change(live_repo.repo, live_repo.snap(), scope="mvp"))
    configured = run(deps.svc.config_change(live_repo.repo, live_repo.snap(), depth="Minimal"))

    assert snap.intent_dir == "260901-ledger-export" and snap.space == "default"
    for result in (recomposed.engine, scoped, configured):
        argv = list(result.argv)
        assert argv[argv.index("--intent") + 1] == snap.intent_dir, result.verb_key
        assert argv[argv.index("--space") + 1] == snap.space, result.verb_key


# --------------------------------------------------------------------------- #
# catalog (§1.15) — what the advisor's plan questionnaire is built from
# --------------------------------------------------------------------------- #


def test_the_catalog_reads_scopes_grid_and_graph_once_and_refuses_an_uninstalled_repo(studio, deps, errors,
                                                                                     tmp_path, monkeypatch):
    """One read of the three engine files, as ``effective_plan`` reads them, and nothing invented.

    The catalog is the vocabulary of a wizard plan draft (§1.18a): its scopes must be the scope files,
    its stages the graph in graph order, and its grid rows the engine's own selection with the values
    normalised so ``grid_row`` can compare them. A repository with no engine has nothing to propose
    from and is refused ``not_installed`` rather than answering an empty questionnaire.
    """
    repo = engine_only_repo(studio, deps, "payload")
    root, stage_count = _engine_tree("payload")

    # "Once": the catalog is built off-thread per click, so it must cost exactly one read of each
    # engine file — never a re-read per scope or per stage.
    reads: dict[str, int] = {}
    for name in ("read_scopes", "read_stage_graph", "read_scope_grid"):
        original = getattr(type(deps.reader), name)

        def counting(self, *args, _name=name, _original=original, **kwargs):
            reads[_name] = reads.get(_name, 0) + 1
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(type(deps.reader), name, counting)
    catalog = deps.svc.catalog(repo, ".kiro")
    assert reads == {"read_scopes": 1, "read_stage_graph": 1, "read_scope_grid": 1}
    monkeypatch.undo()

    scopes = deps.reader.read_scopes(root, ".kiro")
    graph = deps.reader.read_stage_graph(root, ".kiro")
    grid = deps.reader.read_scope_grid(root, ".kiro")
    assert [meta.name for meta in catalog.scopes] == [meta.name for meta in scopes]
    assert [node.slug for node in catalog.stages] == [node.slug for node in graph]
    assert len(catalog.stages) == stage_count
    assert set(catalog.grid) == set(grid)
    assert all(value in ("EXECUTE", "SKIP") for row in catalog.grid.values() for value in row.values())
    assert catalog.engine_version == repo.installed_engine_version

    slugs = {node.slug for node in catalog.stages}
    poc = catalog.grid_row("poc")
    assert set(poc) <= slugs and all(isinstance(on, bool) for on in poc.values())
    # The grid's own EXECUTE row for poc is the 8-stage plan FR-EST-001 calls exact.
    assert sum(poc.values()) == GOLDEN_COUNTS[("payload", "poc")][0]
    assert catalog.grid_row("no-such-scope") == {}

    bare = tmp_path / "bare"
    bare.mkdir()
    orphan = repo_row(studio, deps.store, bare, repo_id="r_2", resolved_identity="1:200")
    with pytest.raises(errors.StudioError) as excinfo:
        deps.svc.catalog(orphan, ".kiro")
    assert excinfo.value.code == "not_installed"
    assert excinfo.value.details["repo_id"] == "r_2"
    assert not deps.denied_log.exists()


# --------------------------------------------------------------------------- #
# policy (static)
# --------------------------------------------------------------------------- #


def test_module_touches_only_allowlisted_admin_verbs(studio, P):
    """Every engine verb this module names must be allowlisted, and every mutating one lease-bound."""
    source = (APP_ROOT / "backend" / "studio" / "plan.py").read_text("utf-8")
    tree = ast.parse(source)
    used = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (node.value.startswith("utility.") or node.value.startswith("state."))
    }
    assert used == {"utility.intent_create", "utility.recompose", "utility.scope_change",
                    "utility.config_change"}
    for key in used:
        verb = studio.engine.ENGINE_ALLOWLIST[key]
        assert verb.lease == "admin", key
        assert verb.tool not in studio.engine.ENGINE_DENY


def test_module_never_writes_a_file_or_names_a_protected_verb():
    source = (APP_ROOT / "backend" / "studio" / "plan.py").read_text("utf-8")
    for forbidden in ("write_text", "write_bytes", "open(", "os.replace", "shutil",
                      "aidlc-orchestrate", "aidlc-audit.ts", "aidlc-jump", "aidlc-state.ts"):
        assert forbidden not in source, forbidden
    # the protected transition verbs, as argv tokens
    tree = ast.parse(source)
    literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert not literals & {"next", "report", "approve", "reject", "revise", "advance", "park",
                           "checkbox", "gate-start", "finalize", "complete-workflow"}


def test_no_test_here_makes_a_shipped_engine_tree_a_repository_root(studio, deps):
    """The graph under test is a *copy*, so a regressed refusal cannot damage a shipped artefact.

    ``create_intent`` ends in ``fake_bun``'s ``intent-create``, which writes
    ``aidlc/spaces/<space>/intents/<dir>/`` under the repository root. The refusal tests above are the
    ones that reach it if a guard regresses, and pointing their root at ``payload/aidlc-kiro`` would
    add files the payload manifest does not list — undetectable until ``scripts/check.sh``'s
    payload-integrity step runs again, on some later and unrelated change. Hence ``_engine_tree``.
    """
    for index, source in enumerate(ENGINE_TREES):
        repo = engine_only_repo(
            studio, deps, source, repo_id=f"r_{source}", resolved_identity=f"1:{200 + index}"
        )
        root = Path(repo.canonical_path)
        assert not root.is_relative_to(APP_ROOT), f"{source} tree is inside the checkout: {root}"
        assert (root / ".kiro").is_dir(), f"{source} copy is not an engine tree: {root}"
        assert deps.reader.read_stage_graph(root, ".kiro"), source
