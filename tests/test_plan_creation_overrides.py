"""Confirmed stage overrides must reach the real engine before runtime compilation."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from test_plan import P, deps, repo_row, request, run  # noqa: F401


@pytest.fixture
def real_plan_repo(studio, deps, repo_builder):
    bun = Path("/Users/ychchen/.bun/bin/bun")
    if not bun.is_file():
        pytest.skip("real Bun is required for creation override regression")
    root = repo_builder.with_engine("payload").with_workspace().build()
    (root / "main.py").write_text("def clamp(value):\n    return min(1, max(0, value))\n")
    repo = repo_row(studio, deps.store, root, engine_version="2.7.1")
    deps.svc._engine = studio.engine.EngineRunner(
        bun_path=str(bun), clock=deps.svc._clock, activity=deps.activity,
    )
    return repo


@pytest.mark.parametrize("add_user_stories", [False, True])
def test_real_creation_applies_confirmed_overrides_before_compile(
    P, deps, real_plan_repo, monkeypatch, fake_host, add_user_stories,
):
    overrides = {"deployment-execution": False, "observability-setup": False}
    if add_user_stories:
        overrides["user-stories"] = True
    req = request(
        P, "express", depth="Minimal", project_type="Brownfield",
        label="override-proof", overrides=overrides,
    )
    preview = deps.svc.effective_plan(real_plan_repo, ".kiro", req)
    assert preview.valid
    assert preview.exact.stages == (9 if add_user_stories else 8)
    assert preview.exact.gates == (6 if add_user_stories else 5)
    engine_run = deps.svc._engine.run
    calls = []
    state_at_compile = {}

    async def observe(verb, *args, **kwargs):
        assert await deps.scheduler.get(real_plan_repo.resolved_identity)
        if verb == "runtime.compile":
            root = Path(real_plan_repo.canonical_path)
            intent, _ = deps.reader.active_intent(root, "default")
            state_at_compile["bytes"] = (
                root / deps.reader.record_rel("default", intent) / "aidlc-state.md"
            ).read_bytes()
        result = await engine_run(verb, *args, **kwargs)
        calls.append(result)
        return result

    monkeypatch.setattr(deps.svc._engine, "run", observe)
    result = run(deps.svc.create_intent(
        real_plan_repo, req, confirm_plan_digest=preview.digest(),
    ))
    root = Path(real_plan_repo.canonical_path)
    record = root / deps.reader.record_rel("default", result.intent_dir)
    state_text = (record / "aidlc-state.md").read_text()
    state = deps.reader.read_state(root, "default", result.intent_dir)[1]
    rows = {row.slug: row for row in state.stages}
    for slug, wanted in overrides.items():
        assert rows[slug].suffix == ("EXECUTE" if wanted else "SKIP"), state_text
    assert {slug for slug, row in rows.items() if row.suffix == "EXECUTE"} == {
        stage.slug for stage in preview.stages if stage.enabled
    }
    assert f"- **Total Stages**: {preview.exact.stages}" in state_text
    assert state_at_compile["bytes"] == (record / "aidlc-state.md").read_bytes()
    assert [call.verb_key for call in calls] == [
        "utility.intent_create", "utility.recompose", "runtime.compile",
    ]
    argv = calls[1].argv
    assert set(argv[argv.index("--skip") + 1].split(",")) == {
        "deployment-execution", "observability-setup",
    }
    assert argv[argv.index("--intent") + 1] == result.intent_dir
    assert argv[argv.index("--space") + 1] == "default"
    if add_user_stories:
        assert argv[argv.index("--add") + 1] == "user-stories"
    runtime = json.loads((record / "runtime-graph.json").read_text())
    assert runtime["scope"] == "express" and runtime["stages"]
    assert {row["stage_slug"] for row in runtime["stages"]} <= {
        stage.slug for stage in preview.stages if stage.enabled
    }
    assert run(deps.scheduler.get(real_plan_repo.resolved_identity)) is None
    audit = "\n".join(path.read_text() for path in (record / "audit").glob("*.md"))
    assert "RECOMPOSED" in audit and "HUMAN_TURN" not in audit
    assert fake_host.notifications == []
    assert not any(slot.messages for slot in fake_host._slots.values())


@pytest.mark.parametrize("failure", ["exit", "no_change"])
def test_create_never_reports_success_when_override_application_fails(
    studio, P, deps, real_plan_repo, monkeypatch, failure,
):
    req = request(P, "express", project_type="Brownfield", overrides={"deployment-execution": False})
    engine_run = deps.svc._engine.run
    created = []
    calls = []

    async def fail_recompose(verb, *args, **kwargs):
        calls.append(verb)
        if verb == "utility.recompose":
            return replace(
                created[0], verb_key=verb, ok=failure == "no_change",
                exit_code=0 if failure == "no_change" else 3,
            )
        result = await engine_run(verb, *args, **kwargs)
        if verb == "utility.intent_create":
            created.append(result)
        return result

    monkeypatch.setattr(deps.svc._engine, "run", fail_recompose)
    with pytest.raises(studio.errors.StudioError) as exc:
        run(deps.svc.create_intent(real_plan_repo, req))
    assert exc.value.details["intent_created"] is True
    assert exc.value.details["creation_failed_phase"] == "plan_composition"
    assert exc.value.details["intent_dir"]
    assert "runtime.compile" not in calls
    assert run(deps.scheduler.get(real_plan_repo.resolved_identity)) is None
    assert len(deps.reader.list_intent_dirs(Path(real_plan_repo.canonical_path), "default")) == 1


@pytest.mark.parametrize("with_overrides", [False, True])
@pytest.mark.parametrize("failure", ["exit", "missing", "malformed"])
def test_compile_failure_is_distinct_from_unapplied_composition(
    studio, P, deps, real_plan_repo, monkeypatch, with_overrides, failure,
):
    overrides = (
        {"deployment-execution": False, "observability-setup": False} if with_overrides else {}
    )
    req = request(P, "express", project_type="Brownfield", overrides=overrides)
    engine_run = deps.svc._engine.run
    root = Path(real_plan_repo.canonical_path)

    async def fail_compile(verb, *args, **kwargs):
        result = await engine_run(verb, *args, **kwargs)
        if verb == "runtime.compile":
            if failure == "exit":
                return replace(result, ok=False, exit_code=3)
            intent_dir, _ = deps.reader.active_intent(root, "default")
            runtime = root / deps.reader.record_rel("default", intent_dir) / "runtime-graph.json"
            if failure == "missing":
                runtime.unlink()
            else:
                runtime.write_text("not json")
        return result

    monkeypatch.setattr(deps.svc._engine, "run", fail_compile)
    with pytest.raises(studio.errors.StudioError) as exc:
        run(deps.svc.create_intent(real_plan_repo, req))
    details = exc.value.details
    assert details["intent_created"] is True
    assert details["creation_failed_phase"] == "runtime_compile"
    assert details["space"] == "default"
    assert details["intent_key"] == details["intent_dir"]
    state = deps.reader.read_state(root, "default", details["intent_dir"])[1]
    rows = {row.slug: row for row in state.stages}
    for slug in overrides:
        assert rows[slug].suffix == "SKIP"
    assert run(deps.scheduler.get(real_plan_repo.resolved_identity)) is None
    assert len(deps.reader.list_intent_dirs(root, "default")) == 1
