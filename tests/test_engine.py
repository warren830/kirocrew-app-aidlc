"""``backend/studio/engine.py``, the only module that starts an AI-DLC subprocess: policy as much as behaviour.

The argv is a list and the child sees exactly it (``probe`` stand-ins print their own argv/cwd/env); the deny
log ``fake_bun`` writes is a tripwire checked after the whole file; a cursor switch is proved from disk by
breaking the real thing each mismatch names; and the engine's own verb listing is the authority for verb names.
Nothing here mocks ``engine.py``: the only doubles are stand-in executables and a recording activity sink.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import dataclasses
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

import fixtures as F
from conftest import APP_ROOT

#: Where ``fake_bun`` records a refusal without ``FAKE_BUN_DENIED_LOG`` — always, since the env scrub drops it.
DEFAULT_DENIED_LOG = Path("/tmp/aidlc-studio-denied.log")
PAYLOAD_TOOLS = APP_ROOT / "payload" / "aidlc-kiro" / ".kiro" / "tools"
ALPHA, BETA = "260901-alpha", "260902-beta"
ALPHA_UUID, BETA_UUID = "01a00000-0000-7000-8000-0000000000aa", "01a00000-0000-7000-8000-0000000000bb"
OTHER_UUID = "01a00000-0000-7000-8000-00000000ffff"


class SyncActivity:
    """An ``ActivityProjector`` offering the synchronous seam the runner prefers."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record_sync(self, **row: object) -> None:
        self.rows.append(dict(row))


class AsyncActivity:
    """A sink with only the async ``record`` of §1.17; the runner writes through ``record_sync`` alone."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def record(self, **row: object) -> int:
        self.rows.append(dict(row))
        return len(self.rows)


@pytest.fixture(scope="module", autouse=True)
def denied_tripwire() -> object:
    """Clear the fakes' refusal log before this file runs and prove it is still empty after."""
    DEFAULT_DENIED_LOG.unlink(missing_ok=True)
    yield
    text = DEFAULT_DENIED_LOG.read_text("utf-8") if DEFAULT_DENIED_LOG.exists() else ""
    assert text == "", f"a denied engine invocation was recorded: {text}"


@pytest.fixture
def engine_mod(studio):
    assert studio.engine is not None, getattr(studio, "engine__error", None)
    return studio.engine


@pytest.fixture
def C(studio):
    return studio.constants


@pytest.fixture
def security(studio):
    return studio.security


@pytest.fixture
def StudioError(studio):
    return studio.errors.StudioError


@pytest.fixture
def runner(engine_mod, fake_bun, clock):
    return engine_mod.EngineRunner(bun_path=fake_bun, clock=clock, activity=SyncActivity())


@pytest.fixture
def repo(repo_builder) -> Path:
    """A real 2.6.2 install (the harvested data files are all either side reads) with two intents, cursor on BETA."""
    builder = repo_builder.with_engine("devdelta").with_workspace()
    builder.with_intent(ALPHA, state=F.state_text(fields={"Current Stage": "requirements-analysis"}), uuid=ALPHA_UUID,
                        slug="alpha", active=False, audit=F.gate_open_audit("requirements-analysis"))
    builder.with_intent(BETA, state=F.state_text(fields={"Current Stage": "user-stories"}), uuid=BETA_UUID,
                        slug="beta", active=True, audit=F.gate_open_audit("user-stories"))
    return builder.build()


def write_stand_in(path: Path, body: str) -> str:
    """A tiny ``bun`` stand-in on the running interpreter (macOS's ``python3`` shim injects env and serialises)."""
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def cursor(repo: Path, space: str = "default") -> Path:
    return repo / "aidlc" / "spaces" / space / "intents" / "active-intent"


@contextlib.contextmanager
def read_only(path: Path):
    """The engine's write to ``path`` fails for the duration (a crash mid-switch, as AI-DLC would see it)."""
    path.chmod(0o444)
    try:
        yield
    finally:
        path.chmod(0o644)


# --- the table itself ---
def test_every_allowlist_entry_is_on_its_lane(engine_mod, C, security):
    """Each entry must survive the security module's own lane check for the lane its lease implies."""
    for key, verb in engine_mod.ENGINE_ALLOWLIST.items():
        assert verb.key == key and verb.tool in ("aidlc-utility.ts", "aidlc-state.ts", "aidlc-runtime.ts")
        assert verb.lease in (None, "admin") and 0 < verb.timeout_secs <= C.SUBPROCESS_TIMEOUT_SECS
        security.assert_engine_argv_allowed(verb.tool, verb.verb, admin=verb.admin)
        allowed = set((C.ENGINE_ADMIN_VERBS if verb.admin else C.ENGINE_READ_VERBS).get(verb.tool, ()))
        for canonical, spellings in C.ENGINE_VERB_ALIASES.items():
            if canonical in allowed:
                allowed.update(spellings)
        assert verb.verb in allowed, f"{key} is not on the {'admin' if verb.admin else 'read'} lane"


def test_read_lane_verbs_mutate_nothing_and_need_no_lease(engine_mod):
    for key, verb in engine_mod.ENGINE_ALLOWLIST.items():
        if verb.lease is None:
            assert verb.side_effects == (), f"{key} runs unattended but claims side effects"
            assert not verb.explicit_user_only


def test_doctor_is_the_only_explicit_user_verb_and_is_audit_appending(engine_mod):
    """A12/C09: doctor appends HEALTH_CHECKED/GUARDRAIL_LOADED, so it can never be a silent read."""
    assert {k for k, v in engine_mod.ENGINE_ALLOWLIST.items() if v.explicit_user_only} == {"utility.doctor"}
    doctor = engine_mod.ENGINE_ALLOWLIST["utility.doctor"]
    assert doctor.side_effects == ("audit",) and doctor.lease == "admin"


def test_the_switch_verbs_declare_the_session_they_re_stamp(engine_mod):
    """2.7.1's ``intent <dir>``/``space <slug>`` re-stamp the calling session's binding; neither touches state."""
    intent, space = (engine_mod.ENGINE_ALLOWLIST[key] for key in ("utility.intent_switch", "utility.space_switch"))
    assert intent.side_effects == ("session",) and space.side_effects == ("harness", "session")
    for verb in (intent, space):
        assert verb.mutates_session is True and verb.mutates_state is False and verb.mutates_audit is False


def test_deny_list_covers_the_forbidden_tools_and_no_allowlisted_verb(engine_mod, C):
    for tool in C.ENGINE_FORBIDDEN_TOOLS:
        assert tool in engine_mod.ENGINE_DENY
    for key, verb in engine_mod.ENGINE_ALLOWLIST.items():
        assert verb.tool not in engine_mod.ENGINE_DENY and f"{verb.tool}:{verb.verb}" not in engine_mod.ENGINE_DENY, key


def test_upgrade_is_never_reachable(engine_mod, C):
    assert not {v.verb for v in engine_mod.ENGINE_ALLOWLIST.values()} & set(C.ENGINE_UNAVAILABLE_VERBS)


def test_mismatch_codes_are_the_pinned_vocabulary(engine_mod):
    assert engine_mod.CURSOR_MISMATCH_CODES == ("active_space", "active_intent", "state_missing",
                                                "intent_list_active", "uuid")


# --- argv construction ---
GOLDEN = {
    "utility.version": ({}, ["version"]),
    "utility.intent_list": ({}, ["intent", "--json"]),
    "utility.space_list": ({}, ["space", "--json"]),
    "state.lookup": ({"query": "phase-of", "arg": "user-stories"}, ["lookup", "phase-of", "user-stories"]),
    "utility.intent_switch": ({"dir_name": ALPHA}, ["intent", ALPHA]),
    "runtime.compile": ({}, ["compile"]),
    "utility.space_switch": ({"slug": "alt"}, ["space", "alt"]),
    "utility.space_create": ({"slug": "alt"}, ["space-create", "alt"]),
    "utility.intent_create": (
        {"scope": "poc", "arguments": "Ship the ledger export", "label": "ledger-export", "depth": "Standard",
         "test_strategy": "Minimal", "review": "advisory", "repos": "api,web"},
        ["intent-create", "--scope", "poc", "--arguments", "Ship the ledger export", "--label", "ledger-export",
         "--depth", "Standard", "--test-strategy", "Minimal", "--review", "advisory", "--repos", "api,web"]),
    # The mutating verbs name their record explicitly: omitted, 2.7.1 resolves it from the session binding first.
    "utility.recompose": (
        {"skip": "nfr-design", "add": "user-stories", "dir_name": ALPHA, "slug": "default"},
        ["recompose", "--skip", "nfr-design", "--add", "user-stories", "--intent", ALPHA, "--space", "default"]),
    "utility.scope_change": (
        {"scope": "feature", "depth": "Comprehensive", "dir_name": ALPHA, "slug": "alt"},
        ["scope-change", "--scope", "feature", "--depth", "Comprehensive", "--intent", ALPHA, "--space", "alt"]),
    "utility.config_change": (
        {"test_strategy": "Standard", "dir_name": BETA, "slug": "default"},
        ["config-change", "--test-strategy", "Standard", "--intent", BETA, "--space", "default"]),
    "utility.doctor": ({}, ["doctor"]),
}


def test_golden_argv_covers_every_allowlisted_verb(engine_mod):
    assert set(GOLDEN) == set(engine_mod.ENGINE_ALLOWLIST)


@pytest.mark.parametrize("key", sorted(GOLDEN))
def test_golden_argv(engine_mod, runner, repo, key, fake_bun):
    kwargs, tokens = GOLDEN[key]
    verb = engine_mod.ENGINE_ALLOWLIST[key]
    argv = runner.build_argv(verb, repo, ".kiro", **kwargs)
    assert argv == [fake_bun, str(repo / ".kiro" / "tools" / verb.tool), *tokens, "--project-dir", str(repo)]
    assert isinstance(argv, list) and all(isinstance(t, str) for t in argv)


def test_optional_flags_are_omitted_when_absent_or_empty(engine_mod, runner, repo):
    argv = runner.build_argv(engine_mod.ENGINE_ALLOWLIST["utility.intent_create"], repo, ".kiro", scope="poc",
                             arguments="x", label="a-b", depth="", review=None)
    assert "--depth" not in argv and "--review" not in argv and "--repos" not in argv
    bare = runner.build_argv(engine_mod.ENGINE_ALLOWLIST["utility.config_change"], repo, ".kiro")
    assert bare[-2:] == ["--project-dir", str(repo)] and bare[2] == "config-change" and len(bare) == 5


@pytest.mark.parametrize("key,kwargs,why", [
    ("utility.intent_create", {"scope": "poc", "label": "a-b"}, "requires kwarg"),
    ("utility.version", {"scope": "poc"}, "does not accept"),
], ids=["required-placeholder-missing", "unknown-kwarg"])
def test_a_missing_or_unknown_kwarg_is_a_value_error(engine_mod, runner, repo, key, kwargs, why):
    with pytest.raises(ValueError, match=why):
        runner.build_argv(engine_mod.ENGINE_ALLOWLIST[key], repo, ".kiro", **kwargs)


def test_control_kwargs_never_reach_the_argv(engine_mod, runner, repo):
    argv = runner.build_argv(engine_mod.ENGINE_ALLOWLIST["utility.intent_switch"], repo, ".kiro", dir_name=ALPHA,
                             lease_generation=7, repo_id="r_000000000001", _confirmed=True)
    assert argv.count(ALPHA) == 1 and "7" not in argv and "r_000000000001" not in argv and "True" not in argv


@pytest.mark.parametrize("key,kwargs,why", [
    ("utility.intent_switch", {"dir_name": "../escape"}, "does not match"),
    ("utility.intent_switch", {"dir_name": "-rf"}, "must not start with"),
    ("utility.intent_switch", {"dir_name": "a\x00b"}, "NUL"),
    ("utility.space_switch", {"slug": "has space"}, "does not match"),
    ("utility.scope_change", {"scope": "PoC"}, "does not match"),
    ("state.lookup", {"query": "delete-everything", "arg": "x"}, "not one of"),
    ("state.lookup", {"query": "phase-of", "arg": "Not A Slug"}, "does not match"),
    ("utility.recompose", {"skip": "nfr-design,"}, "comma-separated"),
    ("utility.recompose", {"add": "Bad_Slug"}, "does not match"),
    ("utility.config_change", {"depth": "standard; rm -rf /"}, "does not match"),
])
def test_kwarg_grammar_refuses_injection(engine_mod, runner, repo, key, kwargs, why):
    with pytest.raises(ValueError, match=why):
        runner.build_argv(engine_mod.ENGINE_ALLOWLIST[key], repo, ".kiro", **{**GOLDEN[key][0], **kwargs})


def test_oversized_label_and_arguments_are_refused(engine_mod, runner, repo, C):
    verb = engine_mod.ENGINE_ALLOWLIST["utility.intent_create"]
    with pytest.raises(ValueError, match="longer than"):
        runner.build_argv(verb, repo, ".kiro", scope="poc", arguments="x", label="a" * (C.MAX_LABEL_CHARS + 1))
    with pytest.raises(ValueError, match="longer than"):
        runner.build_argv(verb, repo, ".kiro", scope="poc", arguments="x" * (engine_mod._MAX_ARGUMENTS_CHARS + 1),
                          label="a-b")


def test_arguments_may_contain_newlines(engine_mod, runner, repo):
    """The objective plus its context is one multi-line token — refusing newlines would break C-plan."""
    argv = runner.build_argv(engine_mod.ENGINE_ALLOWLIST["utility.intent_create"], repo, ".kiro", scope="poc",
                             arguments="objective\n\ncontext", label="a-b")
    assert "objective\n\ncontext" in argv


@pytest.mark.parametrize("key,forge,kwargs,message", [
    ("state.lookup", {"key": "state.forged", "argv": ("approve",)}, {}, ""),
    ("utility.intent_create", {"lease": None}, {"scope": "poc", "arguments": "x", "label": "a-b"},
     "read-only allowlist"),
    ("utility.version", {"lease": "admin"}, {}, "admin allowlist"),  # the admin lane is not a superset
    ("utility.version", {"tool": "aidlc-orchestrate.ts"}, {}, ""),
], ids=["verb-off-its-lane", "admin-verb-on-the-read-lane", "read-verb-promoted-to-admin", "denied-tool"])
def test_build_argv_refuses_a_forged_table_entry(engine_mod, runner, repo, StudioError, key, forge, kwargs, message):
    """The runtime lane guard is wired into ``build_argv``, not only into the static table."""
    with pytest.raises(StudioError) as exc:
        runner.build_argv(dataclasses.replace(engine_mod.ENGINE_ALLOWLIST[key], **forge), repo, ".kiro", **kwargs)
    assert exc.value.code == "internal_error" and message in exc.value.message


@pytest.mark.parametrize("engine_dir", ["../elsewhere", "/etc", ""])
def test_engine_dir_cannot_point_outside_the_repo(engine_mod, runner, repo, engine_dir, StudioError):
    """An absolute or escaping harness dir is refused, never trimmed into something plausible."""
    with pytest.raises(StudioError) as exc:
        runner.build_argv(engine_mod.ENGINE_ALLOWLIST["utility.version"], repo, engine_dir)
    assert exc.value.code == "bad_path"


def test_missing_bun_is_service_unavailable(engine_mod, clock, repo, StudioError):
    for bun, activity in ((None, SyncActivity()), (str(repo / "no-such-bun"), None)):
        with pytest.raises(StudioError) as exc:
            engine_mod.EngineRunner(bun_path=bun, clock=clock, activity=activity).run_sync("utility.version", repo,
                                                                                           ".kiro")
        assert exc.value.code == "bun_missing"


def test_unknown_verb_key_is_internal_error(runner, repo, StudioError):
    with pytest.raises(StudioError) as exc:
        runner.run_sync("utility.definitely_not_a_verb", repo, ".kiro")
    assert exc.value.code == "internal_error"


# --- version-aware verb aliasing ---
@pytest.mark.parametrize("source,spelling", [("payload", "intent-create"), ("payload230", "intent-birth"),
                                             ("older", "intent-birth")])
def test_intent_create_spelling_follows_the_installed_engine(engine_mod, runner, repo_builder, source, spelling):
    """2.6+ renamed ``intent-birth`` → ``intent-create`` and dies on the old name (review R04)."""
    tree = repo_builder.with_engine(source).with_workspace().build()
    argv = runner.build_argv(engine_mod.ENGINE_ALLOWLIST["utility.intent_create"], tree, ".kiro", scope="poc",
                             arguments="x", label="a-b")
    assert argv[2] == spelling


def test_an_unreadable_engine_version_refuses_an_aliased_verb_but_not_an_unaliased_one(engine_mod, runner, repo,
                                                                                       StudioError):
    """Never guess a spelling; but a damaged harness must still be askable for its version."""
    (repo / ".kiro" / "tools" / "aidlc-version.ts").unlink()
    with pytest.raises(StudioError) as exc:
        runner.build_argv(engine_mod.ENGINE_ALLOWLIST["utility.intent_create"], repo, ".kiro", scope="poc",
                          arguments="x", label="a-b")
    assert exc.value.code == "engine_unavailable" and exc.value.details["reason"] == "engine_version_unreadable"
    assert runner.build_argv(engine_mod.ENGINE_ALLOWLIST["utility.version"], repo, ".kiro")[2] == "version"


# --- running: process hygiene ---
_PROBE = """
import json, os, sys
json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), "env": dict(os.environ)}, sys.stdout)
"""


def test_child_sees_the_argv_list_the_cwd_and_a_scrubbed_env(engine_mod, clock, repo, tmp_path, monkeypatch, C,
                                                              security):
    """From inside the child: no shell splitting, the repo as cwd, Studio's env intact and nothing leaked."""
    for name, value in (("AIDLC_DEBUG", "1"), ("CLAUDE_PROJECT", "leak"), ("KIROCREW_TOKEN", "leak"),
                        ("SOME_UNRELATED_VAR", "leak")):
        monkeypatch.setenv(name, value)
    runner = engine_mod.EngineRunner(bun_path=write_stand_in(tmp_path / "probe-bun", _PROBE), clock=clock,
                                     activity=SyncActivity())
    handed_over = security.build_subprocess_env()
    assert set(handed_over) <= set(C.ENV_KEEP) | {"NO_COLOR"}
    result = runner.run_sync("utility.intent_create", repo, ".kiro", scope="poc", arguments="Ship the ledger export",
                             label="ledger-export", lease_generation=1)
    assert result.ok
    seen = json.loads(result.stdout)
    assert seen["argv"] == [str(repo / ".kiro" / "tools" / "aidlc-utility.ts"), "intent-create", "--scope", "poc",
                            "--arguments", "Ship the ledger export", "--label", "ledger-export", "--project-dir",
                            str(repo)]
    assert Path(seen["cwd"]) == Path(os.path.realpath(repo))
    assert {k: seen["env"][k] for k in handed_over} == handed_over
    assert not [k for k in seen["env"] if k.startswith(C.ENV_FORBIDDEN_PREFIXES)]
    assert "SOME_UNRELATED_VAR" not in seen["env"]


def test_forbidden_env_var_never_reaches_fake_bun(runner, repo, monkeypatch):
    monkeypatch.setenv("AIDLC_VERBOSE", "1")
    monkeypatch.setenv("CLAUDE_CODE", "1")
    result = runner.run_sync("utility.version", repo, ".kiro")
    assert result.exit_code == 0, result.stderr
    assert "aidlc" in result.stdout


def test_guard_bypass_variable_refuses_to_start_a_child(runner, repo, monkeypatch, StudioError):
    """An exported bypass switch is a configuration problem the operator must see, not route around."""
    monkeypatch.setenv("AIDLC_SKIP_HUMAN_PRESENCE_GUARD", "1")
    with pytest.raises(StudioError) as exc:
        runner.run_sync("utility.version", repo, ".kiro")
    assert exc.value.code == "internal_error"
    assert "AIDLC_SKIP_HUMAN_PRESENCE_GUARD" in exc.value.details["variables"]


def test_timeout_is_enforced_and_reported_as_a_result(engine_mod, clock, repo, tmp_path, monkeypatch):
    monkeypatch.setattr(clock, "monotonic", time.monotonic)
    monkeypatch.setitem(engine_mod.ENGINE_ALLOWLIST, "utility.version", dataclasses.replace(
        engine_mod.ENGINE_ALLOWLIST["utility.version"], timeout_secs=0.4))
    slow = write_stand_in(tmp_path / "slow-bun", "import time\ntime.sleep(30)\n")
    result = engine_mod.EngineRunner(bun_path=slow, clock=clock, activity=SyncActivity()).run_sync(
        "utility.version", repo, ".kiro")
    assert result.timed_out is True and result.exit_code is None and result.ok is False
    assert result.duration_ms >= 400


def test_stdout_and_stderr_are_capped(engine_mod, clock, repo, tmp_path, C):
    loud = write_stand_in(tmp_path / "loud-bun", f"import sys\nsys.stdout.write('x' * {C.MAX_SUBPROCESS_STDOUT + 4096})"
                                                 f"\nsys.stderr.write('y' * {C.MAX_SUBPROCESS_STDERR + 4096})\n")
    result = engine_mod.EngineRunner(bun_path=loud, clock=clock, activity=SyncActivity()).run_sync(
        "utility.version", repo, ".kiro")
    assert "output truncated" in result.stdout and "output truncated" in result.stderr
    assert len(result.stdout) < C.MAX_SUBPROCESS_STDOUT + 200 and len(result.stderr) < C.MAX_SUBPROCESS_STDERR + 200


def test_result_argv_keeps_repo_paths_and_scrubs_the_rest(runner, repo, fake_bun):
    argv = runner.run_sync("utility.version", repo, ".kiro").argv
    assert argv[1] == str(repo / ".kiro" / "tools" / "aidlc-utility.ts") and argv[-1] == str(repo)
    assert argv[0] == "<path>", "the interpreter path is not repository content"
    assert fake_bun not in argv


def test_json_is_parsed_only_for_json_verbs(runner, repo):
    listed = runner.run_sync("utility.intent_list", repo, ".kiro")
    assert listed.ok and listed.json["active"] == BETA
    assert {row["dirName"] for row in listed.json["intents"]} == {ALPHA, BETA}
    assert runner.run_sync("utility.version", repo, ".kiro").json is None


def test_result_to_json_is_wire_shaped(runner, repo):
    payload = runner.run_sync("utility.version", repo, ".kiro").to_json()
    assert set(payload) == {"verb_key", "argv", "exit_code", "timed_out", "duration_ms", "stdout", "stderr", "json",
                            "ok", "side_effects"}
    assert isinstance(payload["argv"], list)
    json.dumps(payload)


# --- lane and confirmation guards at run time ---
def test_doctor_is_refused_without_the_explicit_user_flag(runner, repo, StudioError):
    with pytest.raises(StudioError) as exc:
        runner.run_sync("utility.doctor", repo, ".kiro", lease_generation=1)
    assert exc.value.code == "invalid_decision" and exc.value.details["side_effects"] == ["audit"]


def test_doctor_runs_when_confirmed_and_documents_its_audit_side_effect(runner, repo):
    result = runner.run_sync("utility.doctor", repo, ".kiro", _confirmed=True, lease_generation=3)
    assert result.ok and result.side_effects == ("audit",) and result.to_json()["side_effects"] == ["audit"]


ADMIN_KEYS = ("runtime.compile", "utility.intent_switch", "utility.space_switch", "utility.space_create",
              "utility.intent_create", "utility.recompose", "utility.scope_change", "utility.config_change")


def test_admin_key_list_is_complete(engine_mod):
    """Keeps the parametrisation below honest if a new admin verb is added to the table."""
    assert {k for k, v in engine_mod.ENGINE_ALLOWLIST.items() if v.admin} == set(ADMIN_KEYS) | {"utility.doctor"}


@pytest.mark.parametrize("key", ADMIN_KEYS)
def test_admin_verbs_refuse_to_run_without_a_lease(runner, repo, key, StudioError):
    """No lease, no subprocess: an unleased mutation could race another session's engine run."""
    with pytest.raises(StudioError) as exc:
        runner.run_sync(key, repo, ".kiro", **GOLDEN[key][0])
    assert exc.value.code == "internal_error" and exc.value.details["lease"] == "admin"


def test_admin_verb_runs_with_a_lease_generation(runner, repo):
    assert runner.run_sync("utility.intent_switch", repo, ".kiro", dir_name=ALPHA, lease_generation=1).ok
    assert cursor(repo).read_text().strip() == ALPHA


# --- async wrapper and activity ---
def test_run_records_the_engine_run_row(engine_mod, fake_bun, clock, repo):
    sink = SyncActivity()
    runner = engine_mod.EngineRunner(bun_path=fake_bun, clock=clock, activity=sink)
    assert asyncio.run(runner.run("utility.version", repo, ".kiro", repo_id="r_000000000001")).ok
    assert len(sink.rows) == 1
    row = sink.rows[0]
    assert (row["kind"], row["repo_id"], row["params"]["verb_key"]) == ("engine.run", "r_000000000001",
                                                                        "utility.version")
    assert row["params"]["exit_code"] == 0 and "duration_ms" in row["params"]
    assert [e["ref"] for e in row["evidence"]] == ["engine.stdout"]


def test_run_never_blocks_the_event_loop(engine_mod, clock, repo, tmp_path):
    """Two concurrent ``run`` calls against a sleeping child overlap (after one warm-up of the executor)."""
    sleepy = write_stand_in(tmp_path / "sleepy-bun", "import time\ntime.sleep(0.4)\n")
    runner = engine_mod.EngineRunner(bun_path=sleepy, clock=clock, activity=AsyncActivity())

    async def scenario():
        await runner.run("utility.version", repo, ".kiro")
        started = time.monotonic()
        await asyncio.gather(runner.run("utility.version", repo, ".kiro"), runner.run("utility.version", repo, ".kiro"))
        return time.monotonic() - started

    assert asyncio.run(scenario()) < 0.75


def test_sync_seam_records_immediately(engine_mod, fake_bun, clock, repo):
    sink = SyncActivity()
    engine_mod.EngineRunner(bun_path=fake_bun, clock=clock, activity=sink).run_sync("utility.version", repo, ".kiro",
                                                                                     repo_id="r_000000000001")
    assert len(sink.rows) == 1


def test_failed_runs_are_recorded_as_attention(engine_mod, fake_bun, clock, repo):
    """A refused switch must leave a trace: it is the evidence a cursor_mismatch card points at."""
    sink = SyncActivity()
    runner = engine_mod.EngineRunner(bun_path=fake_bun, clock=clock, activity=sink)
    assert not runner.run_sync("utility.intent_switch", repo, ".kiro", dir_name="260101-nope", lease_generation=1).ok
    assert sink.rows[-1]["severity"] == "attention"
    assert "engine.stderr" in [e["ref"] for e in sink.rows[-1]["evidence"]]


# --- cursor switch and read-back ---
def test_switch_cursor_happy_path(runner, repo, security):
    assert cursor(repo).read_text().strip() == BETA
    readback = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=4)
    assert readback.ok is True and readback.mismatch == ()
    assert (readback.space, readback.dir_name, readback.uuid) == ("default", ALPHA, ALPHA_UUID)
    state = repo / "aidlc" / "spaces" / "default" / "intents" / ALPHA / "aidlc-state.md"
    assert readback.state_sha256 == security.sha256_file(state) and cursor(repo).read_text().strip() == ALPHA
    assert [r.verb_key for r in readback.results] == ["utility.intent_switch", "utility.intent_list"]


def test_readback_json_omits_the_subprocess_transcripts(runner, repo):
    payload = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=4).to_json()
    assert set(payload) == {"ok", "space", "dir_name", "uuid", "state_sha256", "mismatch"}
    json.dumps(payload)


def test_switch_skips_the_verbs_but_never_the_readback(runner, repo):
    """The cursor already names BETA: nothing is moved, yet the read-back still proves it."""
    before = cursor(repo).stat().st_mtime_ns
    readback = runner.switch_cursor_sync(repo, ".kiro", "default", BETA, BETA_UUID)
    assert readback.ok is True and [r.verb_key for r in readback.results] == ["utility.intent_list"]
    assert cursor(repo).stat().st_mtime_ns == before


def test_switch_moves_the_space_when_it_differs(runner, repo_builder):
    builder = repo_builder.with_engine("devdelta").with_workspace(active_space="default")
    builder.with_intent(BETA, state=F.real_state(), uuid=BETA_UUID, active=True)
    builder.with_workspace(active_space="alt")
    builder.with_intent(ALPHA, state=F.real_state(), uuid=ALPHA_UUID, space="alt", active=True)
    tree = builder.build()
    (tree / "aidlc" / "active-space").write_text("default\n")
    readback = runner.switch_cursor_sync(tree, ".kiro", "alt", ALPHA, ALPHA_UUID, lease_generation=2)
    assert readback.ok is True, readback.mismatch
    assert (tree / "aidlc" / "active-space").read_text().strip() == "alt"
    assert [r.verb_key for r in readback.results] == ["utility.space_switch", "utility.intent_switch",
                                                      "utility.intent_list"]


def test_crash_mid_switch_is_reported_never_assumed(runner, repo):
    """The engine's cursor write fails: Studio must report a mismatch, not proceed with a dispatch."""
    with read_only(cursor(repo)):
        readback = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=1)
    assert readback.ok is False and "active_intent" in readback.mismatch
    assert readback.dir_name == BETA, "the report names where the cursor really is"
    switch = [r for r in readback.results if r.verb_key == "utility.intent_switch"]
    assert switch and switch[0].ok is False, "the failing invocation is kept as evidence"


def test_mismatch_active_space(runner, repo_builder):
    builder = repo_builder.with_engine("devdelta").with_workspace(active_space="default")
    tree = builder.with_intent(ALPHA, state=F.real_state(), uuid=ALPHA_UUID, active=True).build()
    (tree / "aidlc" / "spaces" / "alt" / "intents").mkdir(parents=True)
    (tree / "aidlc" / "spaces" / "alt" / "intents" / "intents.json").write_text("[]\n")
    with read_only(tree / "aidlc" / "active-space"):
        readback = runner.switch_cursor_sync(tree, ".kiro", "alt", ALPHA, None, lease_generation=1)
    assert readback.ok is False and "active_space" in readback.mismatch and readback.space == "default"


def test_mismatch_state_missing(runner, repo):
    (repo / "aidlc" / "spaces" / "default" / "intents" / ALPHA / "aidlc-state.md").unlink()
    readback = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=1)
    assert readback.ok is False and "state_missing" in readback.mismatch and readback.state_sha256 is None


def test_mismatch_uuid(runner, repo):
    readback = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, OTHER_UUID, lease_generation=1)
    assert readback.ok is False and readback.mismatch == ("uuid",) and readback.uuid == ALPHA_UUID


def test_mismatch_intent_list_active_when_the_engine_view_is_unavailable(runner, repo):
    """No engine answer means no proof; the cursor switch is refused rather than believed."""
    (repo / "aidlc" / "spaces" / "default" / "intents" / "intents.json").write_text("{not json at all")
    readback = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=1)
    assert readback.ok is False and "intent_list_active" in readback.mismatch and readback.uuid is None


#: Resolves ``intent --json``'s ``active`` the way 2.7.1 does (session binding, then cursor); ``STICKY`` is a
#: binding the switch verb does not re-stamp. Only the two read-back verbs exist; anything else exits 3.
_SELECTION_BUN = """
import json, sys
from pathlib import Path

BINDING, STICKY = Path({binding!r}), {sticky!r}
args = sys.argv[2:]
flags = {{a[2:]: args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("--") else "true"
         for i, a in enumerate(args) if a.startswith("--")}}
positional = [a for i, a in enumerate(args) if not a.startswith("--") and not (i and args[i - 1].startswith("--"))]
repo = Path(flags["project-dir"])
space = (repo / "aidlc" / "active-space").read_text().strip()
intents = repo / "aidlc" / "spaces" / space / "intents"
cursor = (intents / "active-intent").read_text().strip()
rows = json.loads((intents / "intents.json").read_text())
binding = BINDING.read_text().strip() if BINDING.is_file() else ""
if positional[:1] == ["intent"] and flags.get("json") == "true":
    active = binding or cursor
    print(json.dumps({{"active": active, "space": space, "intents": [
        {{"dirName": r["dirName"], "uuid": r.get("uuid"), "slug": r.get("slug"), "status": r.get("status"),
          "active": r["dirName"] == active}} for r in rows]}}))
elif positional[:1] == ["intent"]:
    (intents / "active-intent").write_text(positional[1] + chr(10))
    if not STICKY:
        BINDING.write_text(positional[1] + chr(10))
    print("Active intent -> " + positional[1])
else:
    sys.exit(3)
"""


def selection_runner(engine_mod, clock, tmp_path, *, binding: str, sticky: bool = False):
    binding_file = tmp_path / "session-binding"
    binding_file.write_text(binding + "\n")
    bun = write_stand_in(tmp_path / "selection-bun", _SELECTION_BUN.format(binding=str(binding_file), sticky=sticky))
    return engine_mod.EngineRunner(bun_path=bun, clock=clock, activity=SyncActivity())


REPAIRED = ["utility.intent_list", "utility.intent_switch", "utility.intent_list"]


def test_a_stale_session_binding_is_repaired_rather_than_refused(engine_mod, clock, repo, tmp_path):
    """The cursor files are right but the session binding names other work: switch once, then only re-read counts."""
    cursor(repo).write_text(ALPHA + "\n")
    runner = selection_runner(engine_mod, clock, tmp_path, binding=BETA)
    readback = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=3)
    assert readback.ok is True and readback.mismatch == ()
    assert readback.dir_name == ALPHA and readback.uuid == ALPHA_UUID
    assert [r.verb_key for r in readback.results] == REPAIRED


def test_the_repair_switch_is_attempted_at_most_once(engine_mod, clock, repo, tmp_path):
    """A binding the switch does not re-stamp must come back as a mismatch, never as a retry loop."""
    cursor(repo).write_text(ALPHA + "\n")
    runner = selection_runner(engine_mod, clock, tmp_path, binding=BETA, sticky=True)
    readback = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=3)
    assert readback.ok is False and readback.mismatch == ("intent_list_active",)
    assert [r.verb_key for r in readback.results].count("utility.intent_switch") == 1
    assert [r.verb_key for r in readback.results] == REPAIRED


def test_a_real_divergence_is_never_repaired(engine_mod, clock, repo, tmp_path):
    """A cursor file that really disagrees, or a uuid that does not match: no second listing, so no repair."""
    runner = selection_runner(engine_mod, clock, tmp_path, binding=BETA, sticky=True)
    with read_only(cursor(repo)):  # the cursor cannot be moved: `active_intent` mismatches too
        divergent = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=1)
    assert divergent.ok is False and "active_intent" in divergent.mismatch
    assert [r.verb_key for r in divergent.results] == ["utility.intent_switch", "utility.intent_list"]
    cursor(repo).write_text(ALPHA + "\n")
    wrong_uuid = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, OTHER_UUID, lease_generation=1)
    assert wrong_uuid.ok is False and wrong_uuid.mismatch == ("intent_list_active", "uuid")
    assert [r.verb_key for r in wrong_uuid.results] == ["utility.intent_list"]


def test_switch_validates_its_own_arguments(runner, repo):
    with pytest.raises(ValueError):
        runner.switch_cursor_sync(repo, ".kiro", "default", "../escape", None)
    with pytest.raises(ValueError):
        runner.switch_cursor_sync(repo, ".kiro", "../escape", ALPHA, None)


def test_corrupt_cursor_file_cannot_aim_a_read_outside_the_repo(runner, repo):
    """``aidlc/active-space`` is repository content: compared as a string (it is unwritable), never used as a path."""
    space_file = repo / "aidlc" / "active-space"
    space_file.write_text("../../../../etc\n")
    with read_only(space_file):
        readback = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=1)
    assert readback.ok is False and "active_space" in readback.mismatch and readback.space == "../../../../etc"


# --- static policy checks over backend/ ---
FORBIDDEN_TOOL_FRAGMENTS = ("aidlc-orchestrate", "aidlc-audit", "aidlc-log", "aidlc-jump")
#: Exactly the tokens §1.9 names: words like ``set``/``merge`` are ordinary English a scan would cry wolf on.
FORBIDDEN_VERB_TOKENS = frozenset(
    "approve reject revise advance finalize complete-workflow gate-start checkbox park unpark".split()
)
#: Studio's decision vocabulary: ``"approve"`` there names a card decision, not an engine subcommand.
VOCABULARY_TABLES = frozenset({"DECISION_KIND", "DECISIONS", "HUMAN_LANE_DECISIONS", "HOST_CONTROL_DECISIONS",
                               "STUDIO_ONLY_DECISIONS"})
#: Assignments whose whole point is to name what must never run: a forbidden token there is the policy itself.
POLICY_TABLES = frozenset({
    "ENGINE_DENY", "ENGINE_FORBIDDEN_TOOLS", "ENGINE_FORBIDDEN_VERBS", "ENGINE_READ_VERBS", "ENGINE_ADMIN_VERBS",
    "ENGINE_VERB_ALIASES", "ENGINE_UNAVAILABLE_VERBS", "GIT_WRITE_VERBS", "GIT_READ_VERBS", "GIT_FORBIDDEN_TOKENS",
    "GIT_FORBIDDEN_PREFIXES",
})
EXEMPT_TABLES = POLICY_TABLES | VOCABULARY_TABLES
#: Grouped replies ask the agent to record one answer receipt; A37 adds the same-turn audit variant.
PINNED_HUMAN_WIRE_NAMES = ("WIRE_GROUPED_ANSWER_SUFFIX", "WIRE_AUDIT_GROUPED_ANSWER_SUFFIX")


def backend_sources() -> list[Path]:
    return sorted((APP_ROOT / "backend").rglob("*.py"))


def _policy_spans(tree: ast.AST) -> list[tuple[int, int]]:
    spans = []
    for node in ast.walk(tree):
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) else [])
        if any(isinstance(t, ast.Name) and t.id in POLICY_TABLES for t in targets):
            spans.append((node.lineno, node.end_lineno or node.lineno))
    return spans


def _pinned_human_wire_spans(tree: ast.AST) -> list[tuple[int, int]]:
    """Only the exact, reviewed human-message string may name an agent-side audit command; nothing else is exempt."""
    constants = json.loads((APP_ROOT / "docs/design/wire-text.json").read_text())["constants"]
    expected = {name: constants[name] for name in PINNED_HUMAN_WIRE_NAMES}
    return [(node.lineno, node.end_lineno or node.lineno) for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id in expected
            and isinstance(node.value, ast.Constant) and node.value.value == expected[node.targets[0].id]]


def _code_literals(tree: ast.AST, *, allow_pinned_wire: bool = False) -> list[str]:
    """Every string literal that is not a docstring and not part of a policy table."""
    out: list[str] = []
    wire_spans = _pinned_human_wire_spans(tree) if allow_pinned_wire else []

    def visit(node: ast.AST, inside_policy: bool) -> None:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            return  # a bare string statement is documentation, not an argv token
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id in EXEMPT_TABLES for t in targets):
                inside_policy = True
        inside_wire = any(lo <= getattr(node, "lineno", 0) <= hi for lo, hi in wire_spans)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and not inside_policy and not inside_wire:
            out.append(node.value)
        for child in ast.iter_child_nodes(node):
            visit(child, inside_policy)

    visit(tree, False)
    return out


def _argv_like_sequences(tree: ast.AST) -> list[tuple[int, list[str]]]:
    """List/tuple literals naming the interpreter or an engine tool file: the only shape a verb token can run in."""
    out: list[tuple[int, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)):
            strings = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if any(s == "bun" or s.endswith(".ts") for s in strings):
                out.append((node.lineno, strings))
    return out


def test_no_forbidden_engine_tool_appears_in_backend_code():
    """A denied tool name (no second meaning) may not exist as a string outside a deny table."""
    offences = [f"{path.name}: {literal!r}" for path in backend_sources()
                for literal in _code_literals(ast.parse(path.read_text("utf-8")),
                                              allow_pinned_wire=path.name == "constants.py")
                if any(fragment in literal for fragment in FORBIDDEN_TOOL_FRAGMENTS)]
    assert offences == [], offences


def test_no_forbidden_verb_reaches_an_argv():
    """A transition verb may not appear in anything shaped like a command line (the bare word is a card decision)."""
    offences: list[str] = []
    for path in backend_sources():
        tree = ast.parse(path.read_text("utf-8"))
        policy = _policy_spans(tree)
        for lineno, strings in _argv_like_sequences(tree):
            if any(start <= lineno <= end for start, end in policy):
                continue
            for token in strings:
                if token in FORBIDDEN_VERB_TOKENS or any(f in token for f in FORBIDDEN_TOOL_FRAGMENTS):
                    offences.append(f"{path.name}:{lineno}: argv carries {token!r} ({strings})")
    assert offences == [], offences


def test_the_argv_scan_would_catch_a_real_violation():
    """Prove the scan is not vacuous: a synthetic argv with a forbidden verb must be detected."""
    tree = ast.parse('argv = ["bun", "/repo/.kiro/tools/aidlc-state.ts", "approve", slug]\n')
    found = [token for _lineno, strings in _argv_like_sequences(tree) for token in strings
             if token in FORBIDDEN_VERB_TOKENS]
    assert found == ["approve"]


def test_forbidden_tool_names_appear_only_in_comments_or_deny_tables():
    """The plain-text half of the grep, so a token hidden in a way ast misses still fails."""
    offences: list[str] = []
    for path in backend_sources():
        text = path.read_text("utf-8")
        tree = ast.parse(text)
        spans = _policy_spans(tree) + (_pinned_human_wire_spans(tree) if path.name == "constants.py" else [])
        for number, line in enumerate(text.splitlines(), start=1):
            if (any(fragment in line for fragment in FORBIDDEN_TOOL_FRAGMENTS) and not line.lstrip().startswith("#")
                    and not any(lo <= number <= hi for lo, hi in spans)):
                offences.append(f"{path.name}:{number}: {line.strip()}")
    assert offences == [], offences


def test_pinned_human_wire_exception_cannot_hide_a_backend_command():
    expected = json.loads((APP_ROOT / "docs/design/wire-text.json").read_text())["constants"][
        "WIRE_GROUPED_ANSWER_SUFFIX"]
    tree = ast.parse(f'WIRE_GROUPED_ANSWER_SUFFIX = {expected!r}\nargv = ["bun", "aidlc-log.ts", "answer"]')
    assert _pinned_human_wire_spans(tree) == [(1, 1)]
    assert "aidlc-log.ts" in _code_literals(tree, allow_pinned_wire=True)
    assert _argv_like_sequences(tree) == [(2, ["bun", "aidlc-log.ts", "answer"])]
    assert _pinned_human_wire_spans(ast.parse(f'WIRE_GROUPED_ANSWER_SUFFIX = {expected + " modified"!r}')) == []


def test_no_shell_execution_anywhere_in_backend():
    for path in backend_sources():
        text = path.read_text("utf-8")
        assert "shell=True" not in text and "os.system" not in text and "os.popen" not in text, path
        for node in ast.walk(ast.parse(text)):
            for keyword in node.keywords if isinstance(node, ast.Call) else ():
                if keyword.arg == "shell":
                    assert isinstance(keyword.value, ast.Constant) and keyword.value.value is False, (
                        f"{path.name}:{node.lineno} passes a non-literal-False shell=")


_SPAWN_ATTRS = frozenset({"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"})


def test_only_the_subprocess_modules_spawn_anything():
    """One door per external tool: a stray spawn elsewhere would bypass every guard in this file."""
    callers = {path.name for path in backend_sources() for node in ast.walk(ast.parse(path.read_text("utf-8")))
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
               and node.func.attr in _SPAWN_ATTRS}
    assert callers <= {"engine.py", "git_observer.py", "installer.py"}, sorted(callers)
    assert "engine.py" in callers


# --- the real engine's own dispatch table ---
def _ask_real_engine(bun: str, tool: str, verb: str, cwd: Path, env: dict) -> str:
    completed = subprocess.run([bun, str(PAYLOAD_TOOLS / tool), verb], cwd=str(cwd), env=env, capture_output=True,
                               timeout=60, shell=False, stdin=subprocess.DEVNULL, check=False)
    return completed.stdout.decode("utf-8", "replace") + completed.stderr.decode("utf-8", "replace")


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not on PATH")
def test_allowlist_matches_engine(engine_mod, security, tmp_path):
    """Every allowlisted verb is one the bundled tools list themselves (help and the unknown-command arm)."""
    bun, env = shutil.which("bun"), security.build_subprocess_env()
    utility = (_ask_real_engine(bun, "aidlc-utility.ts", "help", tmp_path, env)
               + _ask_real_engine(bun, "aidlc-utility.ts", "__studio_probe_unknown__", tmp_path, env))
    state = _ask_real_engine(bun, "aidlc-state.ts", "__studio_probe_unknown__", tmp_path, env)
    runtime = _ask_real_engine(bun, "aidlc-runtime.ts", "--help", tmp_path, env)
    assert "Available commands:" in utility and "Valid:" in state
    for key, verb in engine_mod.ENGINE_ALLOWLIST.items():
        haystack = {"aidlc-utility.ts": utility, "aidlc-state.ts": state, "aidlc-runtime.ts": runtime}[verb.tool]
        assert verb.verb in haystack, f"{key}: the engine no longer lists {verb.verb!r}"
    assert not (tmp_path / "aidlc").exists(), "asking the engine for help must touch no workspace"


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not on PATH")
def test_every_state_verb_but_lookup_is_refused_on_both_lanes(security, StudioError, tmp_path):
    """Default-deny against the engine's own verb listing: a mutator a version bump adds is refused unwritten."""
    listing = _ask_real_engine(shutil.which("bun"), "aidlc-state.ts", "__studio_probe_unknown__", tmp_path,
                               security.build_subprocess_env())
    assert "Valid:" in listing
    dispatched = {token.strip(' "}\n') for token in listing.split("Valid:", 1)[1].split(",")} - {""}
    assert len(dispatched) > 20, dispatched  # the listing parsed, not one long token
    reads = {"lookup"}  # the only state verb any Studio code path runs
    assert reads <= dispatched
    for verb in sorted(dispatched):
        if verb in reads:
            security.assert_engine_argv_allowed("aidlc-state.ts", verb, admin=False)
        for admin in (True,) if verb in reads else (False, True):
            with pytest.raises(StudioError):
                security.assert_engine_argv_allowed("aidlc-state.ts", verb, admin=admin)


# --- finding bun ---
_PRINTS_VERSION = 'print("1.2.3")\n'  # ``find_bun`` accepts a location only by running it
_EXITS_NONZERO = "import sys\nsys.exit(1)\n"  # executable and useless: a wrapper whose binary was uninstalled


@pytest.fixture
def no_bun_on_path(monkeypatch, tmp_path) -> Path:
    """A PATH with nothing in it (what launchd hands the gateway), so no result depends on this machine's PATH."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    return empty


def test_bun_is_found_where_the_installers_put_it_when_path_has_none(engine_mod, no_bun_on_path, tmp_path):
    """The measured defect: the gateway's PATH is four system directories and bun is in none of them."""
    installed = write_stand_in(tmp_path / "installed-bun", _PRINTS_VERSION)
    found = engine_mod.find_bun(candidates=[installed])
    assert (found.found, found.path, found.version) == (True, installed, "1.2.3")
    assert found.source == engine_mod.BUN_SOURCE_INSTALL_LOCATION and found.searched == (installed,)


def test_the_users_own_path_wins_over_every_install_location(engine_mod, tmp_path, monkeypatch):
    (tmp_path / "shell-bin").mkdir()
    on_path = write_stand_in(tmp_path / "shell-bin" / "bun", _PRINTS_VERSION)
    candidate = write_stand_in(tmp_path / "candidate-bun", _PRINTS_VERSION)
    monkeypatch.setenv("PATH", str(tmp_path / "shell-bin"))
    found = engine_mod.find_bun(candidates=[candidate])
    assert (found.path, found.source) == (on_path, engine_mod.BUN_SOURCE_PATH)
    assert found.searched == (on_path,)  # the install locations were never even looked at


def test_a_location_that_cannot_run_bun_never_wins(engine_mod, no_bun_on_path, tmp_path):
    """Absent, not executable, or unable to answer ``--version``: this path becomes argv[0] of every invocation."""
    missing = str(tmp_path / "not-installed" / "bun")
    unexecutable = tmp_path / "stub-bun"
    unexecutable.write_text("#!/bin/sh\necho 1.2.3\n")  # a plausible file, no execute bit
    broken = write_stand_in(tmp_path / "broken-bun", _EXITS_NONZERO)
    good = write_stand_in(tmp_path / "good-bun", _PRINTS_VERSION)
    found = engine_mod.find_bun(candidates=[missing, str(unexecutable), broken, good])
    assert (found.path, found.version) == (good, "1.2.3")
    assert found.searched == (missing, str(unexecutable), broken, good)


def test_bun_nowhere_at_all_reports_every_location_it_tried(engine_mod, no_bun_on_path, tmp_path):
    """"Not found" has to name the places, or the UI can only repeat the advice that was already wrong."""
    tried = [str(tmp_path / "one" / "bun"), str(tmp_path / "two" / "bun")]
    found = engine_mod.find_bun(candidates=tried)
    assert (found.found, found.path, found.version, found.source) == (False, None, None, None)
    assert found.searched == tuple(tried)


def test_the_home_relative_candidate_is_expanded_when_the_search_runs(engine_mod, no_bun_on_path, tmp_path,
                                                                      monkeypatch):
    """The shipped ``BUN_CANDIDATE_PATHS`` starts at ``~/.bun/bin/bun``, with HOME read at call time."""
    home = tmp_path / "home"
    (home / ".bun" / "bin").mkdir(parents=True)
    installed = write_stand_in(home / ".bun" / "bin" / "bun", _PRINTS_VERSION)
    monkeypatch.setenv("HOME", str(home))
    found = engine_mod.find_bun()
    assert found.path == installed
    assert found.searched == (installed,)  # bun's own installer is tried before the package managers


def test_a_prober_that_raises_is_not_a_crash(engine_mod, no_bun_on_path, tmp_path):
    """A raising injected prober is "not this one": it escaping would take down the gateway's boot."""
    asked: list[str] = []

    def angry(path: str) -> str | None:
        asked.append(path)
        raise RuntimeError("boom")

    installed = write_stand_in(tmp_path / "installed-bun", _PRINTS_VERSION)
    found = engine_mod.find_bun(candidates=[installed], probe=angry)
    assert (found.found, found.searched) == (False, (installed,))
    assert asked == [installed]  # the prober is the proof, so it was really consulted


# --- the container's own bun lookup (§1.23 wiring) ---
@contextlib.contextmanager
def built_services(studio, ctx, clock, ids, **kwargs):
    """``Services.build`` (no ``git_path``: the empty PATH finds no git, so ``/health`` spawns none), pool closed."""
    services = studio.services.Services.build(ctx, clock=clock, ids=ids, app_root=APP_ROOT, boot_id="boot-find-bun",
                                              **kwargs)
    services.storage.open()
    services.settings.load()
    try:
        yield services
    finally:
        services.scan_pool.shutdown(wait=False)


def test_the_gateway_finds_bun_without_a_path_and_health_says_where(studio, fake_ctx, clock, ids, no_bun_on_path,
                                                                    tmp_path, monkeypatch):
    """launchd's PATH plus a real install is a healthy Studio; ``tools.bun`` adds ``source``/``searched``."""
    installed = write_stand_in(tmp_path / "installed-bun", _PRINTS_VERSION)
    monkeypatch.setattr(studio.engine, "BUN_CANDIDATE_PATHS", (installed,))
    with built_services(studio, fake_ctx, clock, ids, bun_version=lambda path: "1.2.3") as services:
        assert services.bun_path == installed and services.engine.bun_path == installed
        body = services.health()
    assert body["tools"]["bun"] == {"found": True, "path": installed, "configured_path": None, "version": "1.2.3",
                                    "source": "install_location", "searched": [installed]}
    assert "bun_missing" not in body["issues"]


def test_health_names_the_locations_when_bun_really_is_absent(studio, fake_ctx, clock, ids, no_bun_on_path,
                                                              tmp_path, monkeypatch):
    nowhere = str(tmp_path / "not-installed" / "bun")
    monkeypatch.setattr(studio.engine, "BUN_CANDIDATE_PATHS", (nowhere,))
    with built_services(studio, fake_ctx, clock, ids) as services:
        assert services.bun_path is None
        body = services.health()
    assert body["tools"]["bun"] == {"found": False, "path": None, "configured_path": None, "version": None,
                                    "source": None, "searched": [nowhere]}
    assert "bun_missing" in body["issues"]


def test_an_explicit_bun_path_still_wins_over_the_search(studio, fake_ctx, clock, ids, fake_bun, no_bun_on_path,
                                                         monkeypatch):
    """The search does not even run: running ``fake_bun --version`` would put a line in the tripwire's log."""
    def never(**kwargs):
        raise AssertionError("find_bun must not run when the caller named the binary")

    monkeypatch.setattr(studio.services, "find_bun", never)
    with built_services(studio, fake_ctx, clock, ids, bun_path=fake_bun, bun_version=lambda path: "1.1.42") as services:
        assert services.bun_path == fake_bun and (services.bun_source, services.bun_searched) == ("explicit", ())


# --- the tripwire, last on purpose ---
def test_no_denied_invocation_was_ever_attempted(runner, repo, denied_log):
    for key, kwargs in (("utility.version", {}), ("utility.intent_list", {}), ("utility.space_list", {}),
                        ("state.lookup", {"query": "phase-of", "arg": "user-stories"}),
                        ("utility.doctor", {"_confirmed": True, "lease_generation": 1}),
                        ("utility.recompose", {"skip": "nfr-design", "lease_generation": 1}),
                        ("utility.scope_change", {"scope": "feature", "lease_generation": 1}),
                        ("utility.config_change", {"depth": "Minimal", "lease_generation": 1}),
                        ("utility.intent_create", {"scope": "poc", "arguments": "Ship it", "label": "new-thing",
                                                   "lease_generation": 1})):
        runner.run_sync(key, repo, ".kiro", **kwargs)
    runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=1)
    for log in (denied_log, DEFAULT_DENIED_LOG):
        text = log.read_text("utf-8") if log.exists() else ""
        assert text == "", f"{log} recorded a refused invocation: {text}"
