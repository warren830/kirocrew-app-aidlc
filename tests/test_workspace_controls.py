"""Workspace controls through owner routes and the real bundled engine in temporary repositories."""

import asyncio
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

import conftest as CT
import fixtures as F
from _helpers_c import after
from test_handlers_auth import call, common, sv  # noqa: F401
from test_installer import tree

INTENT = "260910-workspace-proof"
BUN = Path("/Users/ychchen/.bun/bin/bun")


@pytest.fixture
def workspace():
    return CT.studio_module("handlers.workspace")


@pytest.fixture
def repo(sv, studio, repo_builder):
    if not BUN.is_file():
        pytest.skip("real bun is not installed")
    state = F.state_text(F.DEVDELTA_POC, marks={"requirements-analysis": " "},
                         fields={"Current Stage": "requirements-analysis", "Status": "Running"})
    root = (repo_builder.with_engine("payload").with_workspace()
            .with_intent(INTENT, state=state, scope="feature").with_git().build())
    sv.engine = studio.engine.EngineRunner(bun_path=str(BUN), clock=sv.clock, activity=sv.activity)
    sv.plan.engine = sv.engine
    return sv.repos.add(str(root), "workspace proof")


@pytest.fixture
def routes(common, sv):
    """The workspace routes of the one table, so the owner sweep below covers exactly them."""
    return [r for r in common.all_routes(sv) if r.handler.__module__.endswith(".handlers.workspace")]


def request(sv, routes, fake_host, repo, method, suffix, body=None):
    path = f"/repos/{repo.repo_id}{suffix}"
    return call(sv, routes, method, path, CT.owner_request(method, path, host=fake_host, body=body))


@pytest.fixture
def post(sv, routes, fake_host, repo):
    """``post(suffix, body)``: POST ``/repos/<repo><suffix>`` as the owner."""
    return lambda suffix, body=None: request(sv, routes, fake_host, repo, "POST", suffix, body)


def state_path(repo):
    return Path(repo.canonical_path) / f"aidlc/spaces/default/intents/{INTENT}/aidlc-state.md"


SETTINGS = f"/intents/{INTENT}/settings"


def confirm(post, preview, suffix=SETTINGS, **changes):
    """Apply ``changes`` with the digest ``preview`` handed out."""
    return post(suffix, {**changes, "proposal_digest": preview["proposal_digest"]})


def test_preview_is_read_only_and_confirm_preserves_completed_stages(sv, post, repo):
    state = state_path(repo)
    before = state.read_bytes()
    status, preview = post(SETTINGS + "/preview", {"scope": "mvp", "depth": "Comprehensive"})
    assert status == 200, preview
    assert preview["proposal"]["allowed"], preview
    assert preview["proposal"]["stages"]
    assert state.read_bytes() == before
    assert asyncio.run(sv.scheduler.list()) == []
    status, result = confirm(post, preview, scope="mvp", depth="Comprehensive")
    assert status == 200, result
    assert result["verified"]["settings"]["scope"] == "mvp"
    assert result["verified"]["settings"]["depth"] == "Comprehensive"
    marks = F.stage_marks(state.read_text())
    assert all(marks[slug] == mark for slug, mark in F.stage_marks(before.decode()).items() if mark.lower() == "x")
    assert asyncio.run(sv.scheduler.list()) == []


def test_space_creation_is_separate_from_switch_and_disk_authoritative(sv, post, repo):
    status, result = post("/spaces", {"name": "team-two"})
    assert status == 201, result
    assert result["spaces"] == ["default", "team-two"]
    assert result["active_space"] == "default"
    root = Path(repo.canonical_path)
    assert (root / "aidlc/spaces/team-two/memory/org.md").is_file()
    status, result = post("/spaces/switch", {"name": "team-two"})
    assert status == 200, result
    assert result["active_space"] == "team-two"
    assert (root / "aidlc/active-space").read_text().strip() == "team-two"
    assert asyncio.run(sv.scheduler.list()) == []


def test_config_changes_work_at_a_gate_without_rewriting_stage_rows(post, repo):
    state = state_path(repo)
    text = state.read_text().replace("- [ ] requirements-analysis", "- [?] requirements-analysis")
    state.write_text(text)
    status, preview = post(SETTINGS + "/preview", {"depth": "Minimal", "test_strategy": "Comprehensive"})
    assert status == 200 and preview["proposal"]["allowed"], preview
    assert preview["proposal"]["verb_key"] == "utility.config_change"
    status, result = confirm(post, preview, depth="Minimal", test_strategy="Comprehensive")
    assert status == 200, result
    assert F.stage_marks(state.read_text()) == F.stage_marks(text)
    assert result["verified"]["settings"] == {"scope": "feature", "depth": "Minimal", "test_strategy": "Comprehensive"}


@pytest.mark.parametrize("mark", ["?", "R"])
def test_scope_refuses_marks_the_engine_serializer_cannot_preserve(sv, post, repo, mark):
    state = state_path(repo)
    state.write_text(state.read_text().replace("- [ ] requirements-analysis", f"- [{mark}] requirements-analysis"))
    before = state.read_bytes()
    status, preview = post(SETTINGS + "/preview", {"scope": "mvp"})
    assert status == 200, preview
    assert "open_boundary" in preview["proposal"]["refusals"]
    status, result = confirm(post, preview, scope="mvp")
    assert status == 409 and result["code"] == "plan_invalid"
    assert state.read_bytes() == before
    assert asyncio.run(sv.scheduler.list()) == []


def test_scope_preserves_jump_skips_and_completed_artifacts(post, repo):
    state = state_path(repo)
    state.write_text(state.read_text().replace("- [ ] user-stories", "- [S] user-stories"))
    artifact = state.parent / "inception/reverse-engineering/evidence.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("completed evidence, retained")
    _, preview = post(SETTINGS + "/preview", {"scope": "mvp"})
    status, result = confirm(post, preview, scope="mvp")
    assert status == 200, result
    assert F.stage_marks(state.read_text())["user-stories"] == "S"
    assert artifact.read_text() == "completed evidence, retained"


@pytest.mark.parametrize("when", ["before_request", "after_lease"])
def test_stale_digest_is_checked_against_disk_inside_the_lease(sv, post, repo, monkeypatch, when):
    _, preview = post(SETTINGS + "/preview", {"depth": "Minimal"})
    state = state_path(repo)

    def move():
        state.write_text(state.read_text().replace("- [ ] user-stories", "- [x] user-stories"))

    if when == "before_request":
        move()
    else:
        after(monkeypatch, sv.scheduler, "acquire_admin", move)
    status, result = confirm(post, preview, depth="Minimal")
    assert status == 409 and result["details"]["reason"] == "proposal_digest_mismatch"
    assert "- **Depth**: Standard" in state.read_text()
    assert asyncio.run(sv.scheduler.list()) == []


def test_digest_cannot_authorize_different_settings(post):
    _, preview = post(SETTINGS + "/preview", {"depth": "Minimal"})
    status, result = confirm(post, preview, depth="Comprehensive")
    assert status == 409 and result["details"]["reason"] == "proposal_digest_mismatch"


@pytest.mark.parametrize("changes", [
    {"scope": "no-such-scope"}, {"depth": "Deep"}, {"depth": 4}, {"scope": None},
    {"depth": ""}, {"depth": " Minimal"}, {"command": "next"}, {"scope": "../escape"},
])
def test_bad_settings_are_refused_without_writing(sv, post, repo, changes):
    state = state_path(repo)
    before = state.read_bytes()
    status, result = post(SETTINGS + "/preview", changes)
    assert status in (400, 409), result
    assert state.read_bytes() == before
    assert asyncio.run(sv.scheduler.list()) == []


@pytest.mark.parametrize("name", [
    "../escape", "UPPER", "with space", "two--hyphens", "trailing-", "-leading", "1team",
    "help", "list", "switch", "create", "archive", "rename", "show", "birth", "a" * 49, "", None,
])
def test_space_names_are_validated_before_engine_invocation(sv, post, repo, name):
    status, result = post("/spaces", {"name": name})
    assert status == 400, result
    assert sv.reader.list_spaces(Path(repo.canonical_path)) == ["default"]
    assert asyncio.run(sv.scheduler.list()) == []


def test_duplicate_missing_and_escaping_spaces_are_refused(sv, post, repo, tmp_path):
    for suffix, name in [("/spaces", "default"), ("/spaces/switch", "missing")]:
        status, result = post(suffix, {"name": name})
        assert status == 400, result
    outside = tmp_path / "outside"
    outside.mkdir()
    (Path(repo.canonical_path) / "aidlc/spaces/escaping").symlink_to(outside, target_is_directory=True)
    status, result = post("/spaces", {"name": "escaping"})
    assert status in (400, 403), result
    assert list(outside.iterdir()) == []
    assert asyncio.run(sv.scheduler.list()) == []


@pytest.mark.parametrize("lease_kind", ["execution", "admin"])
def test_settings_and_spaces_cannot_preempt_another_lease(sv, post, repo, lease_kind):
    _, preview = post(SETTINGS + "/preview", {"depth": "Minimal"})
    if lease_kind == "execution":
        lease = sv.scheduler.acquire_execution(repo, intent_uuid=None, session_key=None, action_id="a_other")
    else:
        lease = sv.scheduler.acquire_admin(repo, operation_type="install", transaction_id=None)
    grant = asyncio.run(lease)
    try:
        for suffix, body in [
            ("/spaces", {"name": "blocked"}), ("/spaces/switch", {"name": "default"}),
            (SETTINGS, {"depth": "Minimal", "proposal_digest": preview["proposal_digest"]}),
        ]:
            status, result = post(suffix, body)
            assert status == 409 and result["code"] == "repo_busy", result
        assert asyncio.run(sv.scheduler.get(repo.resolved_identity)).generation == grant.generation
    finally:
        asyncio.run(sv.scheduler.release(grant))
    assert sv.reader.list_spaces(Path(repo.canonical_path)) == ["default"]


def test_unleased_host_execution_also_blocks_space_changes(sv, post, fake_host, repo):
    fake_host.add_slot("manual-chat", project=repo.canonical_path, agent="aidlc", running=True)
    for suffix in ("/spaces", "/spaces/switch"):
        status, result = post(suffix, {"name": "team-new"})
        assert status == 409 and result["code"] == "repo_busy", result
    assert asyncio.run(sv.scheduler.list()) == []


@pytest.mark.parametrize("operation", ["create", "switch", "settings"])
def test_replaced_directory_cannot_use_the_registered_identity(sv, post, repo, operation):
    digest = ""
    if operation == "settings":
        status, preview = post(SETTINGS + "/preview", {"depth": "Minimal"})
        assert status == 200
        digest = preview["proposal_digest"]
    root = Path(repo.canonical_path)
    saved = root.with_name(root.name + "-original")
    root.rename(saved)
    shutil.copytree(saved, root)
    before = tree(root)
    suffix, body = {"create": ("/spaces", {"name": "new-team"}), "switch": ("/spaces/switch", {"name": "default"}),
                    "settings": (SETTINGS, {"depth": "Minimal", "proposal_digest": digest})}[operation]
    status, result = post(suffix, body)
    assert status == 409 and result["code"] == "repo_unavailable", result
    assert tree(root) == before
    assert asyncio.run(sv.scheduler.list()) == []


def test_settings_recheck_host_execution_after_acquiring_lease(sv, post, fake_host, repo, monkeypatch):
    status, preview = post(SETTINGS + "/preview", {"depth": "Minimal"})
    assert status == 200
    after(monkeypatch, sv.scheduler, "acquire_admin", lambda: fake_host.add_slot(
        "manual-started-during-lease", project=repo.canonical_path, agent="aidlc", running=True))
    before = tree(Path(repo.canonical_path))
    status, result = confirm(post, preview, depth="Minimal")
    assert status == 409 and result["code"] == "repo_busy", result
    assert tree(Path(repo.canonical_path)) == before
    assert asyncio.run(sv.scheduler.list()) == []


def engine_answers(monkeypatch, sv, repo, **over):
    """Replace ``sv.engine.run`` with one that writes nothing and answers like a real read, plus ``over``."""
    read = asyncio.run(sv.engine.run("utility.space_list", Path(repo.canonical_path), repo.engine_dir))

    async def no_write(verb, *args, **kwargs):
        return replace(read, verb_key=verb, **over)

    monkeypatch.setattr(sv.engine, "run", no_write)


@pytest.mark.parametrize("failed", [False, True])
def test_space_engine_failure_and_false_success_never_report_applied(sv, post, repo, monkeypatch, failed):
    engine_answers(monkeypatch, sv, repo, ok=not failed, exit_code=1 if failed else 0)
    status, result = post("/spaces", {"name": "not-created"})
    assert status == 409 and result["code"] == "state_inconsistent", result
    assert asyncio.run(sv.scheduler.list()) == []
    assert not (Path(repo.canonical_path) / "aidlc/spaces/not-created").exists()


def test_config_false_success_is_detected_on_disk(sv, post, repo, monkeypatch):
    _, preview = post(SETTINGS + "/preview", {"depth": "Minimal"})
    engine_answers(monkeypatch, sv, repo)
    status, result = confirm(post, preview, depth="Minimal")
    assert status == 409 and result["code"] == "state_inconsistent", result
    assert asyncio.run(sv.scheduler.list()) == []


def test_inactive_intent_is_activated_so_state_and_audit_target_the_same_record(post, repo, repo_builder):
    other = "260910-other-intent"
    root = Path(repo.canonical_path)
    before = state_path(repo).read_bytes()
    repo_builder.with_workspace(active_space="team-two").with_intent(
        other, space="team-two", state=before.decode(), scope="feature")
    (root / "aidlc/active-space").write_text("default\n")
    suffix = f"/intents/team-two~{other}/settings"
    _, preview = post(suffix + "/preview", {"depth": "Minimal"})
    assert preview["proposal"]["selection"] == {"space": "team-two", "intent_dir": other}
    status, result = confirm(post, preview, suffix, depth="Minimal")
    assert status == 200, result
    record = root / f"aidlc/spaces/team-two/intents/{other}"
    assert "- **Depth**: Minimal" in (record / "aidlc-state.md").read_text()
    audit = "\n".join(p.read_text() for p in (record / "audit").glob("*.md"))
    assert "DEPTH_CHANGED" in audit
    assert "HUMAN_TURN" not in audit
    assert state_path(repo).read_bytes() == before
    original_audit = "\n".join(p.read_text() for p in (state_path(repo).parent / "audit").glob("*.md"))
    assert "DEPTH_CHANGED" not in original_audit
    assert (root / "aidlc/active-space").read_text().strip() == "team-two"


def test_cancelled_request_keeps_lease_until_engine_finishes(sv, routes, fake_host, repo, monkeypatch, studio):
    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()
        engine_run = sv.engine.run

        async def slow(verb, *args, **kwargs):
            started.set()
            await finish.wait()
            return await engine_run(verb, *args, **kwargs)

        monkeypatch.setattr(sv.engine, "run", slow)
        req = CT.owner_request("POST", f"/repos/{repo.repo_id}/spaces", host=fake_host, body={"name": "finishing"})
        req.match_info["repo_id"] = repo.repo_id
        entry = next(r for r in routes if r.method == "POST" and r.path.endswith("/spaces"))
        task = asyncio.create_task(entry.handler(req, sv.ctx))
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        await asyncio.sleep(0)
        assert await sv.scheduler.get(repo.resolved_identity) is not None
        with pytest.raises(studio.errors.StudioError) as exc:
            await sv.scheduler.acquire_admin(repo, operation_type="cursor_switch", transaction_id=None)
        assert exc.value.code == "repo_busy"
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await sv.scheduler.get(repo.resolved_identity) is None
        assert "finishing" in sv.reader.list_spaces(Path(repo.canonical_path))

    asyncio.run(scenario())


@pytest.mark.parametrize("factory", [CT.anon_request, CT.user_request, CT.app_request])
def test_all_workspace_mutations_require_the_owner(sv, routes, fake_host, factory):
    for entry in routes:
        if entry.method == "GET":
            continue
        path = entry.path.replace("{repo_id}", "r_one").replace("{intent}", INTENT)
        status, _ = call(sv, routes, "POST", path, factory("POST", path, host=fake_host, body={}))
        assert status in (401, 403), (entry.path, status)


def test_space_create_argv_and_admin_lease_are_enforced(sv, studio, repo):
    root = Path(repo.canonical_path)
    verb = sv.engine.verb_for("utility.space_create")
    argv = sv.engine.build_argv(verb, root, ".kiro", slug="team-new")
    assert argv[2:] == ["space-create", "team-new", "--project-dir", str(root)]
    assert verb.lease == "admin" and verb.mutates_state
    with pytest.raises(studio.errors.StudioError) as exc:
        sv.engine.run_sync("utility.space_create", root, ".kiro", slug="team-new")
    assert exc.value.details["lease"] == "admin"
    assert not (root / "aidlc/spaces/team-new").exists()
