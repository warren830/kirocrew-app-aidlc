"""Repair a created intent's derived runtime using the bundled engine and temporary repos."""

import asyncio
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

import conftest as CT
from test_handlers_auth import call, sv  # noqa: F401
from test_workspace_controls import INTENT, repo, state_path  # noqa: F401


@pytest.fixture
def runtime():
    return CT.studio_module("handlers.runtime")


@pytest.fixture
def routes(sv, runtime):
    return runtime.ROUTES(sv)


def request(sv, routes, fake_host, repo, *, intent=INTENT, body=None):
    path = f"/repos/{repo.repo_id}/intents/{intent}/runtime/compile"
    return call(sv, routes, "POST", path,
                CT.owner_request("POST", path, host=fake_host, body={} if body is None else body))


def test_real_compile_and_retry_preserve_state_and_never_create_an_intent(sv, routes, fake_host, repo):
    root = Path(repo.canonical_path)
    state = state_path(repo)
    before = state.read_bytes()
    registry = (state.parent.parent / "intents.json").read_bytes()
    dirs = sv.reader.list_intent_dirs(root, "default")
    assert not (state.parent / "runtime-graph.json").exists()
    for _ in range(2):
        status, body = request(sv, routes, fake_host, repo)
        assert status == 200, body
        assert body == {"ok": True, "intent_key": INTENT, "runtime_graph_present": True}
        graph = json.loads((state.parent / "runtime-graph.json").read_bytes())
        assert isinstance(graph["stages"], list)
        assert state.read_bytes() == before
        assert (state.parent.parent / "intents.json").read_bytes() == registry
        assert sv.reader.list_intent_dirs(root, "default") == dirs
        assert asyncio.run(sv.scheduler.list()) == []
    assert fake_host._slots == {}


def test_real_compile_failure_keeps_created_intent_and_retry_repairs_it(sv, routes, fake_host, repo):
    root = Path(repo.canonical_path)
    state = state_path(repo)
    before = state.read_bytes()
    registry = (state.parent.parent / "intents.json").read_bytes()
    tool = root / ".kiro/tools/aidlc-runtime.ts"
    source = tool.read_bytes()
    tool.write_text('process.stderr.write("compile failed in test\\n"); process.exit(7);\n')
    try:
        status, body = request(sv, routes, fake_host, repo)
        assert status == 409 and body["code"] == "state_inconsistent", body
        assert body["details"]["verb_key"] == "runtime.compile"
        assert body["details"]["exit_code"] == 7
        assert not (state.parent / "runtime-graph.json").exists()
        assert state.read_bytes() == before
        assert (state.parent.parent / "intents.json").read_bytes() == registry
        assert asyncio.run(sv.scheduler.list()) == []
    finally:
        tool.write_bytes(source)
    for _ in range(2):
        status, body = request(sv, routes, fake_host, repo)
        assert status == 200 and body["runtime_graph_present"], body
    assert sv.reader.list_intent_dirs(root, "default") == [INTENT]
    assert state.read_bytes() == before
    assert (state.parent.parent / "intents.json").read_bytes() == registry


def test_inactive_intent_in_another_space_is_selected_and_only_its_graph_is_written(
    sv, routes, fake_host, repo, repo_builder
):
    root = Path(repo.canonical_path)
    original = state_path(repo)
    before = original.read_bytes()
    other = "260910-second-runtime"
    repo_builder.with_workspace(active_space="team-two").with_intent(
        other, space="team-two", state=before.decode(), scope="feature",
    )
    (root / "aidlc/active-space").write_text("default\n")
    other_state = root / f"aidlc/spaces/team-two/intents/{other}/aidlc-state.md"
    status, body = request(sv, routes, fake_host, repo, intent=f"team-two~{other}")
    assert status == 200 and body["intent_key"] == f"team-two~{other}", body
    assert (other_state.parent / "runtime-graph.json").is_file()
    assert not (original.parent / "runtime-graph.json").exists()
    assert other_state.read_bytes() == before and original.read_bytes() == before
    assert (root / "aidlc/active-space").read_text().strip() == "team-two"
    assert sv.reader.list_intent_dirs(root, "team-two") == [other]
    assert sv.reader.list_intent_dirs(root, "default") == [INTENT]
    assert fake_host._slots == {}


@pytest.mark.parametrize("kind", ["execution", "admin"])
def test_busy_lease_is_never_preempted(sv, routes, fake_host, repo, kind):
    if kind == "execution":
        grant = asyncio.run(sv.scheduler.acquire_execution(
            repo, intent_uuid=None, session_key=None, action_id="other-action",
        ))
    else:
        grant = asyncio.run(sv.scheduler.acquire_admin(repo, operation_type="install", transaction_id=None))
    try:
        status, body = request(sv, routes, fake_host, repo)
        assert status == 409 and body["code"] == "repo_busy", body
        assert not (state_path(repo).parent / "runtime-graph.json").exists()
        assert asyncio.run(sv.scheduler.get(repo.resolved_identity)).generation == grant.generation
    finally:
        asyncio.run(sv.scheduler.release(grant))


@pytest.mark.parametrize("when", ["before_request", "after_lease", "after_cursor"])
def test_external_host_execution_is_rechecked_before_compile(sv, routes, fake_host, repo, monkeypatch, when):
    def busy():
        fake_host.add_slot("manual", project=repo.canonical_path, agent="aidlc", running=True)

    if when == "before_request":
        busy()
    elif when == "after_lease":
        original = sv.scheduler.acquire_admin

        async def acquire(*args, **kwargs):
            grant = await original(*args, **kwargs)
            busy()
            return grant

        monkeypatch.setattr(sv.scheduler, "acquire_admin", acquire)
    else:
        original = sv.engine.switch_cursor_sync

        def switch(*args, **kwargs):
            result = original(*args, **kwargs)
            busy()
            return result

        monkeypatch.setattr(sv.engine, "switch_cursor_sync", switch)
    state = state_path(repo)
    before = state.read_bytes()
    status, body = request(sv, routes, fake_host, repo)
    assert status == 409 and body["code"] == "repo_busy", body
    assert not (state.parent / "runtime-graph.json").exists()
    assert state.read_bytes() == before
    assert asyncio.run(sv.scheduler.list()) == []


@pytest.mark.parametrize("when", ["after_lease", "after_cursor", "during_compile"])
def test_changed_state_is_not_overwritten_or_reported_as_success(sv, routes, fake_host, repo, monkeypatch, when):
    state = state_path(repo)
    changed = state.read_bytes() + b"\nUser note added during repair.\n"

    def edit():
        state.write_bytes(changed)

    if when == "after_lease":
        original = sv.scheduler.acquire_admin

        async def acquire(*args, **kwargs):
            grant = await original(*args, **kwargs)
            edit()
            return grant

        monkeypatch.setattr(sv.scheduler, "acquire_admin", acquire)
    elif when == "after_cursor":
        original = sv.engine.switch_cursor_sync

        def switch(*args, **kwargs):
            result = original(*args, **kwargs)
            edit()
            return result

        monkeypatch.setattr(sv.engine, "switch_cursor_sync", switch)
    else:
        original = sv.engine.run

        async def compile_then_edit(*args, **kwargs):
            result = await original(*args, **kwargs)
            edit()
            return result

        monkeypatch.setattr(sv.engine, "run", compile_then_edit)
    status, body = request(sv, routes, fake_host, repo)
    assert status == 409 and body["code"] == "unstable_read", body
    assert state.read_bytes() == changed
    assert asyncio.run(sv.scheduler.list()) == []
    if when != "during_compile":
        assert not (state.parent / "runtime-graph.json").exists()


@pytest.mark.parametrize("when", ["after_lease", "after_cursor"])
def test_replaced_repo_identity_cannot_authorize_compilation(sv, routes, fake_host, repo, monkeypatch, when):
    root = Path(repo.canonical_path)
    saved = root.with_name("saved-original")

    def swap():
        root.rename(saved)
        shutil.copytree(saved, root)

    if when == "after_lease":
        original = sv.scheduler.acquire_admin

        async def acquire(*args, **kwargs):
            grant = await original(*args, **kwargs)
            swap()
            return grant

        monkeypatch.setattr(sv.scheduler, "acquire_admin", acquire)
    else:
        original = sv.engine.switch_cursor_sync

        def switch(*args, **kwargs):
            result = original(*args, **kwargs)
            swap()
            return result

        monkeypatch.setattr(sv.engine, "switch_cursor_sync", switch)
    status, body = request(sv, routes, fake_host, repo)
    assert status == 409 and body["code"] == "repo_unavailable", body
    assert not (state_path(repo).parent / "runtime-graph.json").exists()
    assert not (saved / state_path(repo).parent.relative_to(root) / "runtime-graph.json").exists()
    assert asyncio.run(sv.scheduler.list()) == []


@pytest.mark.parametrize("when", [1, 2])
def test_unstable_snapshot_never_reaches_engine(sv, routes, fake_host, repo, monkeypatch, when):
    original = sv.projection.snapshot
    calls = 0

    def unstable(*args, **kwargs):
        nonlocal calls
        calls += 1
        snap = original(*args, **kwargs)
        return replace(snap, stable=False) if calls == when else snap

    monkeypatch.setattr(sv.projection, "snapshot", unstable)
    status, body = request(sv, routes, fake_host, repo)
    assert status == 409 and body["code"] == "unstable_read", body
    assert not (state_path(repo).parent / "runtime-graph.json").exists()
    assert asyncio.run(sv.scheduler.list()) == []


def test_cursor_readback_mismatch_blocks_compile(sv, routes, fake_host, repo, monkeypatch):
    original = sv.engine.switch_cursor_sync

    def mismatch(*args, **kwargs):
        return replace(original(*args, **kwargs), ok=False, mismatch=("intent_list_active",))

    monkeypatch.setattr(sv.engine, "switch_cursor_sync", mismatch)
    status, body = request(sv, routes, fake_host, repo)
    assert status == 409 and body["code"] == "cursor_mismatch", body
    assert not (state_path(repo).parent / "runtime-graph.json").exists()
    assert asyncio.run(sv.scheduler.list()) == []


@pytest.mark.parametrize("kind", ["missing", "invalid_json", "invalid_shape", "wrong_target", "skipped", "unstable"])
def test_success_requires_a_readable_graph_for_the_selected_intent(
    sv, routes, fake_host, repo, monkeypatch, kind
):
    original = sv.engine.run
    graph = state_path(repo).parent / "runtime-graph.json"

    async def damaged(*args, **kwargs):
        result = await original(*args, **kwargs)
        if kind == "missing":
            graph.unlink()
        elif kind == "invalid_json":
            graph.write_bytes(b"{")
        elif kind == "invalid_shape":
            graph.write_text('{"stages": {}}')
        elif kind == "wrong_target":
            result = replace(result, json={"written": str(graph.with_name("other.json"))})
        elif kind == "skipped":
            result = replace(result, json={"skipped": "no-state"})
        return result

    monkeypatch.setattr(sv.engine, "run", damaged)
    if kind == "unstable":
        read = sv.reader.read_artifact
        monkeypatch.setattr(sv.reader, "read_artifact",
                            lambda *args: replace(read(*args), unstable=True))
    status, body = request(sv, routes, fake_host, repo)
    assert status == 409 and body["code"] == "state_inconsistent", body
    assert asyncio.run(sv.scheduler.list()) == []


@pytest.mark.parametrize("cancel_count", [1, 2])
def test_disconnect_holds_lease_until_real_compile_finishes(
    sv, runtime, fake_host, repo, monkeypatch, studio, cancel_count
):
    async def scenario():
        started, resume = asyncio.Event(), asyncio.Event()
        original = sv.engine.run

        async def paused(*args, **kwargs):
            started.set()
            await resume.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(sv.engine, "run", paused)
        path = f"/repos/{repo.repo_id}/intents/{INTENT}/runtime/compile"
        req = CT.owner_request("POST", path, host=fake_host, body={})
        req.match_info.update(repo_id=repo.repo_id, intent=INTENT)
        entry = runtime.ROUTES(sv)[0]
        task = asyncio.create_task(entry.handler(req, sv.ctx))
        await asyncio.wait_for(started.wait(), 5)
        try:
            for _ in range(cancel_count):
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done()
            assert await sv.scheduler.get(repo.resolved_identity) is not None
            with pytest.raises(studio.errors.StudioError) as exc:
                await sv.scheduler.acquire_admin(repo, operation_type="cursor_switch", transaction_id=None)
            assert exc.value.code == "repo_busy"
        finally:
            resume.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await sv.scheduler.get(repo.resolved_identity) is None
        assert (state_path(repo).parent / "runtime-graph.json").is_file()

    asyncio.run(scenario())


@pytest.mark.parametrize("body", [{"path": "../other"}, {"intent_key": "another"}, {"force": True}])
def test_only_empty_body_is_accepted(sv, routes, fake_host, repo, body):
    status, answer = request(sv, routes, fake_host, repo, body=body)
    assert status == 400 and answer["code"] == "bad_body", answer
    assert asyncio.run(sv.scheduler.list()) == []


def test_nonexistent_intent_cannot_be_created_by_repair(sv, routes, fake_host, repo):
    status, body = request(sv, routes, fake_host, repo, intent="260910-missing")
    assert status == 404 and body["code"] == "intent_not_found", body
    assert sv.reader.list_intent_dirs(Path(repo.canonical_path), "default") == [INTENT]
    assert asyncio.run(sv.scheduler.list()) == []


@pytest.mark.parametrize("factory", [CT.anon_request, CT.user_request, CT.app_request])
def test_route_is_owner_only(sv, routes, fake_host, factory):
    path = f"/repos/r_one/intents/{INTENT}/runtime/compile"
    status, _ = call(sv, routes, "POST", path, factory("POST", path, host=fake_host, body={}))
    assert status in (401, 403)
