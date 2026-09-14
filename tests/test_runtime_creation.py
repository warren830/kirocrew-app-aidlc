"""Intent creation must also materialize the engine's runtime graph without running a turn."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from test_plan import P, deps, fresh_repo, repo_row, request, run  # noqa: F401


def test_create_materializes_runtime_graph_before_releasing_admin_lease(
    studio, P, deps, fresh_repo, monkeypatch
):
    engine_run = deps.engine.run
    observed = []

    async def observe(verb, *args, **kwargs):
        observed.append((verb, bool(await deps.scheduler.get(fresh_repo.resolved_identity))))
        return await engine_run(verb, *args, **kwargs)

    monkeypatch.setattr(deps.engine, "run", observe)
    result = run(deps.svc.create_intent(fresh_repo, request(P)))
    record = Path(fresh_repo.canonical_path) / "aidlc/spaces/default/intents" / result.intent_dir
    graph = json.loads((record / "runtime-graph.json").read_text())
    assert isinstance(graph["stages"], list)
    assert observed == [("utility.intent_create", True), ("runtime.compile", True)]
    assert run(deps.scheduler.get(fresh_repo.resolved_identity)) is None
    assert result.summary.operational_state == "Idle"
    assert "HUMAN_TURN" not in "\n".join(p.read_text() for p in (record / "audit").glob("*.md"))


@pytest.mark.parametrize("test_strategy", ["Minimal", "Standard", "Comprehensive"])
def test_real_bundled_engine_creation_also_compiles_runtime(
    studio, P, deps, fresh_repo, monkeypatch, test_strategy
):
    bun = Path("/Users/ychchen/.bun/bin/bun")
    if not bun.is_file():
        pytest.skip("real bun is not installed")
    deps.svc._engine = studio.engine.EngineRunner(bun_path=str(bun), clock=deps.svc._clock, activity=deps.activity)
    engine_run = deps.svc._engine.run
    before_compile = {}

    async def observe(verb, *args, **kwargs):
        if verb == "runtime.compile":
            root = Path(fresh_repo.canonical_path)
            intent, _ = deps.reader.active_intent(root, "default")
            state = root / deps.reader.record_rel("default", intent) / "aidlc-state.md"
            before_compile["state"] = state.read_bytes()
        return await engine_run(verb, *args, **kwargs)

    monkeypatch.setattr(deps.svc._engine, "run", observe)
    result = run(deps.svc.create_intent(fresh_repo, request(
        P, label="runtime-proof", depth="Minimal", test_strategy=test_strategy,
    )))
    record = Path(fresh_repo.canonical_path) / "aidlc/spaces/default/intents" / result.intent_dir
    graph = json.loads((record / "runtime-graph.json").read_text())
    assert graph["scope"] == "poc"
    assert isinstance(graph["stages"], list)
    assert (record / "aidlc-state.md").read_bytes() == before_compile["state"]
    assert f"- **Test Strategy**: {test_strategy}" in (record / "aidlc-state.md").read_text()
    assert result.summary.operational_state == "Idle"
    assert "HUMAN_TURN" not in "\n".join(p.read_text() for p in (record / "audit").glob("*.md"))


@pytest.mark.parametrize("failure", ["exit", "missing", "malformed"])
def test_compile_failure_names_the_created_intent_and_releases_lease(
    studio, P, deps, fresh_repo, monkeypatch, failure
):
    engine_run = deps.engine.run

    async def broken_runtime(verb, *args, **kwargs):
        result = await engine_run(verb, *args, **kwargs)
        if verb == "runtime.compile":
            root = Path(fresh_repo.canonical_path)
            intent, _ = deps.reader.active_intent(root, "default")
            graph = root / deps.reader.record_rel("default", intent) / "runtime-graph.json"
            if failure == "exit":
                return replace(result, ok=False, exit_code=3, stderr="compile failure")
            if failure == "missing":
                graph.unlink()
            else:
                graph.write_text("not json")
        return result

    monkeypatch.setattr(deps.engine, "run", broken_runtime)
    with pytest.raises(studio.errors.StudioError) as exc:
        run(deps.svc.create_intent(fresh_repo, request(P)))
    assert exc.value.details["intent_created"] is True
    assert exc.value.details["intent_key"] == "260904-ledger-export"
    assert run(deps.scheduler.get(fresh_repo.resolved_identity)) is None
    assert deps.reader.list_intent_dirs(Path(fresh_repo.canonical_path), "default") == ["260904-ledger-export"]
