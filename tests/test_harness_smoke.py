"""Smoke test for the harness itself: the fakes must refuse what policy forbids."""
import json, os, subprocess
import pytest
import conftest as CT
import fixtures as F

# the bundled payload's own metadata, read rather than pinned (C21): a literal would decide which AI-DLC Studio ships
MANIFEST = json.loads((CT.PAYLOAD / "manifest.json").read_text("utf-8"))
INTENT = "260904-gate-demo"


def test_repo_builder_lays_out_a_readable_workspace(gate_repo):
    assert (gate_repo / ".kiro" / "tools" / "data" / "stage-graph.json").is_file()
    record = gate_repo / "aidlc" / "spaces" / "default" / "intents" / INTENT
    state = (record / "aidlc-state.md").read_text()
    assert F.stage_marks(state)["requirements-analysis"] == "?"
    assert json.loads((record / ".aidlc-active-directive.json").read_text())["state_sha256"] == F.sha256(state)
    rows = json.loads((gate_repo / "aidlc/spaces/default/intents/intents.json").read_text())
    assert [r["dirName"] for r in rows] == [INTENT]
    assert (gate_repo / "aidlc/spaces/default/intents/active-intent").read_text().strip() == INTENT


def _run(exe, args, cwd, env=None):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("AIDLC_", "CLAUDE_"))}
    e.update(env or {})
    return subprocess.run([exe, *args], cwd=cwd, env=e, capture_output=True, text=True)


def bun(fake_bun, gate_repo, tool, *args):
    """``fake_bun .kiro/tools/<tool> <args> --project-dir <repo>``, run from the repository."""
    return _run(fake_bun, [str(gate_repo / ".kiro/tools" / tool), *args, "--project-dir", str(gate_repo)], gate_repo)


def test_fake_bun_serves_read_verbs_and_refuses_forbidden_ones(fake_bun, gate_repo, denied_log, studio):
    ok = bun(fake_bun, gate_repo, "aidlc-utility.ts", "version")
    # the fake reads AIDLC_VERSION out of the copied-in payload, so this pins fake and payload together
    assert ok.returncode == 0 and ok.stdout.startswith(f"aidlc {studio.constants.BUNDLED_ENGINE_VERSION}"), ok
    payload = json.loads(bun(fake_bun, gate_repo, "aidlc-utility.ts", "intent", "--json").stdout)
    assert payload["active"] == INTENT and payload["space"] == "default"
    assert bun(fake_bun, gate_repo, "aidlc-orchestrate.ts", "next").returncode == 99
    assert "forbidden tool" in denied_log.read_text()


def test_fake_bun_refuses_an_inherited_bypass_variable(fake_bun, gate_repo):
    r = _run(fake_bun, [str(gate_repo / ".kiro/tools/aidlc-utility.ts"), "version"], gate_repo,
             env={"AIDLC_SKIP_HUMAN_PRESENCE_GUARD": "1"})
    assert r.returncode == 98


def test_fake_bun_switches_the_cursor_and_reports_unknown_intents(fake_bun, gate_repo):
    bad = bun(fake_bun, gate_repo, "aidlc-utility.ts", "intent", "nope")
    assert bad.returncode == 1 and "Unknown intent" in bad.stderr
    good = bun(fake_bun, gate_repo, "aidlc-utility.ts", "intent", INTENT)
    assert good.returncode == 0 and "Active intent" in good.stdout


def test_fake_git_answers_reads_and_refuses_every_write_verb(fake_git, gate_repo, git_state, denied_log):
    git_state({"branch": "feature/x", "dirty": ["a.py"], "ahead": 2, "behind": 1, "upstream": "origin/main"})
    r = _run(fake_git, ["-C", str(gate_repo), "rev-parse", "--abbrev-ref", "HEAD"], gate_repo)
    assert r.stdout.strip() == "feature/x"
    s = _run(fake_git, ["-C", str(gate_repo), "status", "--porcelain=v2", "--branch"], gate_repo)
    assert "# branch.head feature/x" in s.stdout and "a.py" in s.stdout
    for verb in ("commit", "push", "checkout", "clean"):
        assert _run(fake_git, ["-C", str(gate_repo), verb], gate_repo).returncode == 97, verb
    assert denied_log.read_text().count("argv") == 4


def test_request_factory_shapes(request_factory, fake_host):
    r = request_factory.owner("POST", "/actions/a_1/submit", body={"x": 1}, match_info={"action_id": "a_1"},
                              host=fake_host)
    assert r["user"] == "owner-1" and r["app"] == "" and r.match_info["action_id"] == "a_1"
    assert r.app["state"] is fake_host
    assert request_factory.anon("GET", "/health").get("user") is None
    assert request_factory.internal("POST", "/repos", body={}).headers["X-Internal-Secret"] == "test-internal-secret"


def test_fake_ctx_is_a_real_app_context_with_a_recording_bus(fake_ctx):
    from kiro_crew.apps.context import AppContext
    assert isinstance(fake_ctx, AppContext)
    fake_ctx.storage.set("k", {"a": 1})
    assert fake_ctx.storage.get("k") == {"a": 1}
    fake_ctx.events.publish(f"{CT.APP_NAME}:action", {"id": "a_1"})
    assert fake_ctx.events.published == [(f"{CT.APP_NAME}:action", {"id": "a_1"})]
    with pytest.raises(PermissionError):
        fake_ctx.events.publish("something-else", {})
    assert fake_ctx.config["bundledAidlc"]["engineVersion"] == MANIFEST["engineVersion"]


def test_backend_loads_through_the_app_namespace(routes, studio):
    assert hasattr(routes, "register_routes")
    assert studio.constants.APP_NAME == CT.APP_NAME
    assert studio.security.HAS_HOST_SECURITY is True
