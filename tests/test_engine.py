"""Tests for ``backend/studio/engine.py``.

This module is the only place in the product that starts an AI-DLC subprocess, so the tests are about
policy as much as behaviour. Four claims are worth stating because they are the ones a reviewer will
want proof of:

* **The argv is a list and the child sees exactly it.** ``probe_bun`` is a stand-in that prints its own
  ``sys.argv``, ``os.getcwd()`` and ``os.environ`` as JSON, so "argv list, repo cwd, scrubbed env" is
  asserted from the child's point of view rather than from the parent's intention.
* **The deny log is the tripwire.** ``fake_bun`` writes a line and exits 99 for any tool or verb Studio
  must never invoke. A module-scoped fixture clears that file before the first test and asserts it is
  still empty after the last one, so a policy regression anywhere in this file fails the file.
  ``build_subprocess_env`` strips ``FAKE_BUN_DENIED_LOG`` (it is not on ``ENV_KEEP``), so the child
  falls back to its default path — that default is what the tripwire watches.
* **A cursor switch is proved from disk.** Every mismatch code has a test that produces it by breaking
  the real thing it names: a read-only cursor file (the engine's write fails mid-switch), a deleted
  state file, a corrupt registry, a wrong uuid.
* **The engine's own dispatch table is the authority for verb names.** ``test_allowlist_matches_engine``
  runs the real bundled ``aidlc-utility.ts``/``aidlc-state.ts`` with a real ``bun`` when one is on PATH
  and asserts every allowlisted verb is in the list the tool itself prints, so a rename upstream fails
  here instead of in a user's repository.

Nothing here mocks ``engine.py``. The only doubles are external: stand-in executables, and a recording
object that offers the slice of ``ActivityProjector`` the runner is allowed to use.
"""

from __future__ import annotations

import ast
import asyncio
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

#: Where ``fake_bun`` records a refused invocation when ``FAKE_BUN_DENIED_LOG`` is absent from its
#: environment — which is always, because the env scrub keeps only ``ENV_KEEP``.
DEFAULT_DENIED_LOG = Path("/tmp/aidlc-studio-denied.log")

PAYLOAD_TOOLS = APP_ROOT / "payload" / "aidlc-kiro" / ".kiro" / "tools"

ALPHA = "260901-alpha"
BETA = "260902-beta"
ALPHA_UUID = "01a00000-0000-7000-8000-0000000000aa"
BETA_UUID = "01a00000-0000-7000-8000-0000000000bb"


# --------------------------------------------------------------------------- #
# doubles and fixtures
# --------------------------------------------------------------------------- #


class SyncActivity:
    """An ``ActivityProjector`` that offers the synchronous seam the runner prefers."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record_sync(self, **row: object) -> None:
        self.rows.append(dict(row))


class AsyncActivity:
    """An ``ActivityProjector`` with only the async ``record`` of §1.17, so the buffer is exercised."""

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
    module = getattr(studio, "engine", None)
    assert module is not None, getattr(studio, "engine__error", None)
    return module


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
    """The runner every test drives, wired to ``fake_bun`` and a recording activity sink."""
    return engine_mod.EngineRunner(bun_path=fake_bun, clock=clock, activity=SyncActivity())


@pytest.fixture
def repo(repo_builder) -> Path:
    """One installed 2.6.2 repository with two intents; the cursor starts on BETA.

    The harvested ``devdelta`` harness rather than the 293-file payload: it is a real 2.6.2 install and
    the only file either the runner or ``fake_bun`` reads out of it is ``tools/aidlc-version.ts``, so
    copying the whole payload into every test would buy nothing but seconds. The payload itself is
    exercised by the alias test below and by ``test_allowlist_matches_engine``.
    """
    builder = repo_builder.with_engine("devdelta").with_workspace()
    builder.with_intent(
        ALPHA,
        state=F.state_text(fields={"Current Stage": "requirements-analysis"}),
        uuid=ALPHA_UUID,
        slug="alpha",
        active=False,
        audit=F.gate_open_audit("requirements-analysis"),
    )
    builder.with_intent(
        BETA,
        state=F.state_text(fields={"Current Stage": "user-stories"}),
        uuid=BETA_UUID,
        slug="beta",
        active=True,
        audit=F.gate_open_audit("user-stories"),
    )
    return builder.build()


def write_stand_in(path: Path, body: str) -> str:
    """A tiny executable that stands in for ``bun`` when a specific child behaviour is needed.

    The shebang names the running interpreter rather than ``env python3``: macOS's ``python3`` shim
    goes through ``xcrun``, which both injects environment variables of its own and serialises
    concurrent launches — either would be measured here as a property of Studio rather than of the
    stand-in.
    """
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def cursor(repo: Path, space: str = "default") -> Path:
    return repo / "aidlc" / "spaces" / space / "intents" / "active-intent"


# --------------------------------------------------------------------------- #
# the table itself
# --------------------------------------------------------------------------- #


def test_every_allowlist_entry_is_on_its_lane(engine_mod, C, security):
    """Each entry must survive the security module's own lane check for the lane its lease implies."""
    for key, verb in engine_mod.ENGINE_ALLOWLIST.items():
        assert verb.key == key
        assert verb.tool in ("aidlc-utility.ts", "aidlc-state.ts", "aidlc-runtime.ts")
        assert verb.lease in (None, "admin")
        assert 0 < verb.timeout_secs <= C.SUBPROCESS_TIMEOUT_SECS
        security.assert_engine_argv_allowed(verb.tool, verb.verb, admin=verb.admin)
        table = C.ENGINE_ADMIN_VERBS if verb.admin else C.ENGINE_READ_VERBS
        allowed = set(table.get(verb.tool, ()))
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
    explicit = {k for k, v in engine_mod.ENGINE_ALLOWLIST.items() if v.explicit_user_only}
    assert explicit == {"utility.doctor"}
    doctor = engine_mod.ENGINE_ALLOWLIST["utility.doctor"]
    assert doctor.side_effects == ("audit",)
    assert doctor.lease == "admin"


def test_the_switch_verbs_declare_the_session_they_re_stamp(engine_mod):
    """Both switch verbs write the calling session's binding, so neither is a bare cursor move.

    2.7.1's ``intent <dir>`` and ``space <slug>`` read ``.current-session`` and re-stamp that session's
    space/intent binding, then clear its rebind offer (aidlc-utility.ts:6201-6203, :6258-6259). That is a
    write the UI must be allowed to name, and it is not ``state``: neither verb touches ``aidlc-state.md``.
    """
    intent = engine_mod.ENGINE_ALLOWLIST["utility.intent_switch"]
    space = engine_mod.ENGINE_ALLOWLIST["utility.space_switch"]
    assert intent.side_effects == ("session",)
    assert space.side_effects == ("harness", "session")
    for verb in (intent, space):
        assert verb.mutates_session is True
        assert verb.mutates_state is False and verb.mutates_audit is False
        assert verb.to_json()["side_effects"] == list(verb.side_effects)


def test_deny_list_covers_the_forbidden_tools_and_no_allowlisted_verb(engine_mod, C):
    for tool in C.ENGINE_FORBIDDEN_TOOLS:
        assert tool in engine_mod.ENGINE_DENY
    for key, verb in engine_mod.ENGINE_ALLOWLIST.items():
        assert verb.tool not in engine_mod.ENGINE_DENY
        assert f"{verb.tool}:{verb.verb}" not in engine_mod.ENGINE_DENY, key


def test_upgrade_is_never_reachable(engine_mod, C):
    """The engine's own ``upgrade`` prints "unavailable"; Studio's transactional installer owns it."""
    verbs = {v.verb for v in engine_mod.ENGINE_ALLOWLIST.values()}
    assert not verbs & set(C.ENGINE_UNAVAILABLE_VERBS)


def test_mismatch_codes_are_the_pinned_vocabulary(engine_mod):
    assert engine_mod.CURSOR_MISMATCH_CODES == (
        "active_space",
        "active_intent",
        "state_missing",
        "intent_list_active",
        "uuid",
    )


# --------------------------------------------------------------------------- #
# argv construction
# --------------------------------------------------------------------------- #

GOLDEN = {
    "utility.version": ({}, ["version"]),
    "utility.status": ({}, ["status"]),
    "utility.detect": ({}, ["detect", "--json"]),
    "utility.intent_list": ({}, ["intent", "--json"]),
    "utility.space_list": ({}, ["space", "--json"]),
    "utility.scope_table": ({}, ["scope-table"]),
    "state.get": ({"field": "Current Stage"}, ["get", "Current Stage"]),
    "state.lookup": (
        {"query": "phase-of", "arg": "user-stories"},
        ["lookup", "phase-of", "user-stories"],
    ),
    "state.resume": ({}, ["resume"]),
    "state.count": ({}, ["count"]),
    "utility.intent_switch": ({"dir_name": ALPHA}, ["intent", ALPHA]),
    "runtime.compile": ({}, ["compile"]),
    "utility.space_switch": ({"slug": "alt"}, ["space", "alt"]),
    "utility.space_create": ({"slug": "alt"}, ["space-create", "alt"]),
    "utility.intent_create": (
        {
            "scope": "poc",
            "arguments": "Ship the ledger export",
            "label": "ledger-export",
            "depth": "Standard",
            "test_strategy": "Minimal",
            "review": "advisory",
            "repos": "api,web",
        },
        [
            "intent-create",
            "--scope",
            "poc",
            "--arguments",
            "Ship the ledger export",
            "--label",
            "ledger-export",
            "--depth",
            "Standard",
            "--test-strategy",
            "Minimal",
            "--review",
            "advisory",
            "--repos",
            "api,web",
        ],
    ),
    # All three mutating verbs carry an explicit record selector: with `--intent`/`--space` omitted the
    # engine resolves the record itself (2.7.1: the calling session's binding before the cursor), and
    # `--intent` alone is not enough because the space would still come from that binding.
    "utility.recompose": (
        {"skip": "nfr-design", "add": "user-stories", "dir_name": ALPHA, "slug": "default"},
        [
            "recompose",
            "--skip",
            "nfr-design",
            "--add",
            "user-stories",
            "--intent",
            ALPHA,
            "--space",
            "default",
        ],
    ),
    "utility.scope_change": (
        {"scope": "feature", "depth": "Comprehensive", "dir_name": ALPHA, "slug": "alt"},
        [
            "scope-change",
            "--scope",
            "feature",
            "--depth",
            "Comprehensive",
            "--intent",
            ALPHA,
            "--space",
            "alt",
        ],
    ),
    "utility.config_change": (
        {"test_strategy": "Standard", "dir_name": BETA, "slug": "default"},
        ["config-change", "--test-strategy", "Standard", "--intent", BETA, "--space", "default"],
    ),
    "utility.doctor": ({}, ["doctor"]),
}


def test_golden_argv_covers_every_allowlisted_verb(engine_mod):
    assert set(GOLDEN) == set(engine_mod.ENGINE_ALLOWLIST)


@pytest.mark.parametrize("key", sorted(GOLDEN))
def test_golden_argv(engine_mod, runner, repo, key, fake_bun):
    kwargs, tokens = GOLDEN[key]
    verb = engine_mod.ENGINE_ALLOWLIST[key]
    argv = runner.build_argv(verb, repo, ".kiro", **kwargs)
    assert argv == [
        fake_bun,
        str(repo / ".kiro" / "tools" / verb.tool),
        *tokens,
        "--project-dir",
        str(repo),
    ]
    assert isinstance(argv, list) and all(isinstance(t, str) for t in argv)


def test_optional_flags_are_omitted_when_absent_or_empty(engine_mod, runner, repo):
    verb = engine_mod.ENGINE_ALLOWLIST["utility.intent_create"]
    argv = runner.build_argv(
        verb, repo, ".kiro", scope="poc", arguments="x", label="a-b", depth="", review=None
    )
    assert "--depth" not in argv and "--review" not in argv and "--repos" not in argv
    bare = runner.build_argv(engine_mod.ENGINE_ALLOWLIST["utility.config_change"], repo, ".kiro")
    assert bare[-2:] == ["--project-dir", str(repo)]
    assert bare[2] == "config-change" and len(bare) == 5


def test_required_placeholder_missing_is_a_value_error(engine_mod, runner, repo):
    verb = engine_mod.ENGINE_ALLOWLIST["utility.intent_create"]
    with pytest.raises(ValueError, match="requires kwarg"):
        runner.build_argv(verb, repo, ".kiro", scope="poc", label="a-b")


def test_unknown_kwarg_is_a_value_error(engine_mod, runner, repo):
    verb = engine_mod.ENGINE_ALLOWLIST["utility.version"]
    with pytest.raises(ValueError, match="does not accept"):
        runner.build_argv(verb, repo, ".kiro", scope="poc")


def test_control_kwargs_never_reach_the_argv(engine_mod, runner, repo):
    verb = engine_mod.ENGINE_ALLOWLIST["utility.intent_switch"]
    argv = runner.build_argv(
        verb, repo, ".kiro", dir_name=ALPHA, lease_generation=7, repo_id="r_000000000001", _confirmed=True
    )
    assert argv.count(ALPHA) == 1
    assert "7" not in argv and "r_000000000001" not in argv and "True" not in argv


@pytest.mark.parametrize(
    "key,kwargs,why",
    [
        ("utility.intent_switch", {"dir_name": "../escape"}, "does not match"),
        ("utility.intent_switch", {"dir_name": "-rf"}, "must not start with"),
        ("utility.intent_switch", {"dir_name": "a\x00b"}, "NUL"),
        ("utility.space_switch", {"slug": "has space"}, "does not match"),
        ("utility.scope_change", {"scope": "PoC"}, "does not match"),
        ("state.get", {"field": "Current\nStage"}, "does not match"),
        ("state.lookup", {"query": "delete-everything", "arg": "x"}, "not one of"),
        ("state.lookup", {"query": "phase-of", "arg": "Not A Slug"}, "does not match"),
        ("utility.recompose", {"skip": "nfr-design,"}, "comma-separated"),
        ("utility.recompose", {"add": "Bad_Slug"}, "does not match"),
        ("utility.config_change", {"depth": "standard; rm -rf /"}, "does not match"),
    ],
)
def test_kwarg_grammar_refuses_injection(engine_mod, runner, repo, key, kwargs, why):
    verb = engine_mod.ENGINE_ALLOWLIST[key]
    base = dict(GOLDEN[key][0])
    base.update(kwargs)
    with pytest.raises(ValueError, match=why):
        runner.build_argv(verb, repo, ".kiro", **base)


def test_oversized_label_and_arguments_are_refused(engine_mod, runner, repo, C):
    verb = engine_mod.ENGINE_ALLOWLIST["utility.intent_create"]
    with pytest.raises(ValueError, match="longer than"):
        runner.build_argv(
            verb, repo, ".kiro", scope="poc", arguments="x", label="a" * (C.MAX_LABEL_CHARS + 1)
        )
    too_long = "x" * (engine_mod._MAX_ARGUMENTS_CHARS + 1)
    with pytest.raises(ValueError, match="longer than"):
        runner.build_argv(verb, repo, ".kiro", scope="poc", arguments=too_long, label="a-b")


def test_arguments_may_contain_newlines(engine_mod, runner, repo):
    """The objective plus its context is one multi-line token — refusing newlines would break C-plan."""
    verb = engine_mod.ENGINE_ALLOWLIST["utility.intent_create"]
    argv = runner.build_argv(
        verb, repo, ".kiro", scope="poc", arguments="objective\n\ncontext", label="a-b"
    )
    assert "objective\n\ncontext" in argv


def test_build_argv_refuses_a_verb_off_its_lane(engine_mod, runner, repo, StudioError):
    """Proves the runtime guard is wired into build_argv, not only into the static table."""
    forged = dataclasses.replace(
        engine_mod.ENGINE_ALLOWLIST["state.get"], key="state.forged", argv=("approve",)
    )
    with pytest.raises(StudioError) as exc:
        runner.build_argv(forged, repo, ".kiro")
    assert exc.value.code == "internal_error"


def test_read_lane_refuses_an_admin_verb(engine_mod, runner, repo, StudioError):
    """A read-lane entry may only carry a verb from ``ENGINE_READ_VERBS``, whatever the table says."""
    forged = dataclasses.replace(engine_mod.ENGINE_ALLOWLIST["utility.intent_create"], lease=None)
    with pytest.raises(StudioError) as exc:
        runner.build_argv(forged, repo, ".kiro", scope="poc", arguments="x", label="a-b")
    assert exc.value.code == "internal_error"
    assert "read-only allowlist" in exc.value.message


def test_admin_lane_refuses_a_verb_that_is_not_an_admin_verb(engine_mod, runner, repo, StudioError):
    """And the admin lane is not a superset: a read verb promoted to admin is still refused."""
    forged = dataclasses.replace(engine_mod.ENGINE_ALLOWLIST["utility.detect"], lease="admin")
    with pytest.raises(StudioError) as exc:
        runner.build_argv(forged, repo, ".kiro")
    assert exc.value.code == "internal_error"
    assert "admin allowlist" in exc.value.message


def test_build_argv_refuses_a_denied_tool(engine_mod, runner, repo, StudioError):
    forged = dataclasses.replace(
        engine_mod.ENGINE_ALLOWLIST["utility.version"], tool="aidlc-orchestrate.ts"
    )
    with pytest.raises(StudioError) as exc:
        runner.build_argv(forged, repo, ".kiro")
    assert exc.value.code == "internal_error"


@pytest.mark.parametrize("engine_dir", ["../elsewhere", "/etc", ""])
def test_engine_dir_cannot_point_outside_the_repo(engine_mod, runner, repo, engine_dir, StudioError):
    """An absolute or escaping harness dir is refused, never trimmed into something plausible."""
    verb = engine_mod.ENGINE_ALLOWLIST["utility.version"]
    with pytest.raises(StudioError) as exc:
        runner.build_argv(verb, repo, engine_dir)
    assert exc.value.code == "bad_path"


def test_missing_bun_is_service_unavailable(engine_mod, clock, repo, StudioError):
    bare = engine_mod.EngineRunner(bun_path=None, clock=clock, activity=SyncActivity())
    with pytest.raises(StudioError) as exc:
        bare.run_sync("utility.version", repo, ".kiro")
    assert exc.value.code == "bun_missing"

    absent = engine_mod.EngineRunner(bun_path=str(repo / "no-such-bun"), clock=clock, activity=None)
    with pytest.raises(StudioError) as exc:
        absent.run_sync("utility.version", repo, ".kiro")
    assert exc.value.code == "bun_missing"


def test_unknown_verb_key_is_internal_error(runner, repo, StudioError):
    with pytest.raises(StudioError) as exc:
        runner.run_sync("utility.definitely_not_a_verb", repo, ".kiro")
    assert exc.value.code == "internal_error"


# --------------------------------------------------------------------------- #
# version-aware verb aliasing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "source,spelling",
    [("payload", "intent-create"), ("payload230", "intent-birth"), ("older", "intent-birth")],
)
def test_intent_create_spelling_follows_the_installed_engine(
    engine_mod, runner, repo_builder, source, spelling
):
    """2.6+ renamed ``intent-birth`` → ``intent-create`` and dies on the old name (review R04)."""
    tree = repo_builder.with_engine(source).with_workspace().build()
    verb = engine_mod.ENGINE_ALLOWLIST["utility.intent_create"]
    argv = runner.build_argv(verb, tree, ".kiro", scope="poc", arguments="x", label="a-b")
    assert argv[2] == spelling


def test_unreadable_engine_version_refuses_rather_than_guessing(
    engine_mod, runner, repo, StudioError
):
    (repo / ".kiro" / "tools" / "aidlc-version.ts").unlink()
    verb = engine_mod.ENGINE_ALLOWLIST["utility.intent_create"]
    with pytest.raises(StudioError) as exc:
        runner.build_argv(verb, repo, ".kiro", scope="poc", arguments="x", label="a-b")
    assert exc.value.code == "engine_unavailable"
    assert exc.value.details["reason"] == "engine_version_unreadable"


def test_unaliased_verbs_do_not_need_a_readable_version(engine_mod, runner, repo):
    """A repo whose harness is damaged must still be askable for its version, or its status is unknowable."""
    (repo / ".kiro" / "tools" / "aidlc-version.ts").unlink()
    argv = runner.build_argv(engine_mod.ENGINE_ALLOWLIST["utility.version"], repo, ".kiro")
    assert argv[2] == "version"


# --------------------------------------------------------------------------- #
# running: process hygiene
# --------------------------------------------------------------------------- #

_PROBE = """
import json, os, sys
json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), "env": dict(os.environ)}, sys.stdout)
"""


def test_child_sees_the_argv_list_the_cwd_and_a_scrubbed_env(
    engine_mod, clock, repo, tmp_path, monkeypatch, C, security
):
    """One assertion from inside the child: no shell splitting, cwd is the repo, env is minimal.

    The child's environment is compared to what ``build_subprocess_env`` produced rather than to a
    fixed set, because the child's own launcher adds a few of its own names (``/usr/bin/env python3``
    on macOS exports ``SDKROOT``/``CPATH``; CoreFoundation adds ``__CF_USER_TEXT_ENCODING``). What
    matters is that nothing Studio handed over is outside ``ENV_KEEP`` and that nothing the gateway
    had leaked through.
    """
    monkeypatch.setenv("AIDLC_DEBUG", "1")
    monkeypatch.setenv("CLAUDE_PROJECT", "leak")
    monkeypatch.setenv("KIROCREW_TOKEN", "leak")
    monkeypatch.setenv("SOME_UNRELATED_VAR", "leak")
    probe = write_stand_in(tmp_path / "probe-bun", _PROBE)
    runner = engine_mod.EngineRunner(bun_path=probe, clock=clock, activity=SyncActivity())

    handed_over = security.build_subprocess_env()
    assert set(handed_over) <= set(C.ENV_KEEP) | {"NO_COLOR"}

    result = runner.run_sync("state.get", repo, ".kiro", field="Current Stage")
    assert result.ok
    seen = json.loads(result.stdout)

    assert seen["argv"] == [
        str(repo / ".kiro" / "tools" / "aidlc-state.ts"),
        "get",
        "Current Stage",
        "--project-dir",
        str(repo),
    ]
    assert Path(seen["cwd"]) == Path(os.path.realpath(repo))
    assert {k: seen["env"][k] for k in handed_over} == handed_over
    assert not [k for k in seen["env"] if k.startswith(C.ENV_FORBIDDEN_PREFIXES)]
    assert "SOME_UNRELATED_VAR" not in seen["env"]


def test_forbidden_env_var_never_reaches_fake_bun(runner, repo, monkeypatch):
    """``fake_bun`` exits 98 when an AI-DLC/Kiro variable is inherited; it must never see one."""
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


def test_timeout_is_enforced_and_reported_as_a_result(
    engine_mod, clock, repo, tmp_path, monkeypatch
):
    slow = write_stand_in(tmp_path / "slow-bun", "import time\ntime.sleep(30)\n")
    monkeypatch.setattr(clock, "monotonic", time.monotonic)
    monkeypatch.setitem(
        engine_mod.ENGINE_ALLOWLIST,
        "utility.version",
        dataclasses.replace(engine_mod.ENGINE_ALLOWLIST["utility.version"], timeout_secs=0.4),
    )
    runner = engine_mod.EngineRunner(bun_path=slow, clock=clock, activity=SyncActivity())

    result = runner.run_sync("utility.version", repo, ".kiro")
    assert result.timed_out is True
    assert result.exit_code is None
    assert result.ok is False
    assert result.duration_ms >= 400


def test_stdout_and_stderr_are_capped(engine_mod, clock, repo, tmp_path, C):
    loud = write_stand_in(
        tmp_path / "loud-bun",
        "import sys\n"
        f"sys.stdout.write('x' * {C.MAX_SUBPROCESS_STDOUT + 4096})\n"
        f"sys.stderr.write('y' * {C.MAX_SUBPROCESS_STDERR + 4096})\n",
    )
    runner = engine_mod.EngineRunner(bun_path=loud, clock=clock, activity=SyncActivity())
    result = runner.run_sync("utility.version", repo, ".kiro")
    assert "output truncated" in result.stdout
    assert "output truncated" in result.stderr
    assert len(result.stdout) < C.MAX_SUBPROCESS_STDOUT + 200
    assert len(result.stderr) < C.MAX_SUBPROCESS_STDERR + 200


def test_result_argv_keeps_repo_paths_and_scrubs_the_rest(runner, repo, fake_bun):
    result = runner.run_sync("utility.version", repo, ".kiro")
    assert result.argv[1] == str(repo / ".kiro" / "tools" / "aidlc-utility.ts")
    assert result.argv[-1] == str(repo)
    assert result.argv[0] == "<path>", "the interpreter path is not repository content"
    assert fake_bun not in result.argv


def test_json_is_parsed_only_for_json_verbs(runner, repo):
    listed = runner.run_sync("utility.intent_list", repo, ".kiro")
    assert listed.ok and listed.json["active"] == BETA
    assert {row["dirName"] for row in listed.json["intents"]} == {ALPHA, BETA}

    plain = runner.run_sync("utility.version", repo, ".kiro")
    assert plain.json is None


def test_result_to_json_is_wire_shaped(runner, repo):
    payload = runner.run_sync("utility.version", repo, ".kiro").to_json()
    assert set(payload) == {
        "verb_key",
        "argv",
        "exit_code",
        "timed_out",
        "duration_ms",
        "stdout",
        "stderr",
        "json",
        "ok",
        "side_effects",
    }
    assert isinstance(payload["argv"], list)
    json.dumps(payload)


# --------------------------------------------------------------------------- #
# lane and confirmation guards at run time
# --------------------------------------------------------------------------- #


def test_doctor_is_refused_without_the_explicit_user_flag(runner, repo, StudioError):
    with pytest.raises(StudioError) as exc:
        runner.run_sync("utility.doctor", repo, ".kiro", lease_generation=1)
    assert exc.value.code == "invalid_decision"
    assert exc.value.details["side_effects"] == ["audit"]


def test_doctor_runs_when_confirmed_and_documents_its_audit_side_effect(runner, repo):
    result = runner.run_sync("utility.doctor", repo, ".kiro", _confirmed=True, lease_generation=3)
    assert result.ok
    assert result.side_effects == ("audit",)
    assert result.to_json()["side_effects"] == ["audit"]


ADMIN_KEYS = (
    "runtime.compile",
    "utility.intent_switch",
    "utility.space_switch",
    "utility.space_create",
    "utility.intent_create",
    "utility.recompose",
    "utility.scope_change",
    "utility.config_change",
)


def test_admin_key_list_is_complete(engine_mod):
    """Keeps the parametrisation below honest if a new admin verb is added to the table."""
    admin = {k for k, v in engine_mod.ENGINE_ALLOWLIST.items() if v.admin}
    assert admin == set(ADMIN_KEYS) | {"utility.doctor"}


@pytest.mark.parametrize("key", ADMIN_KEYS)
def test_admin_verbs_refuse_to_run_without_a_lease(runner, repo, key, StudioError):
    """No lease, no subprocess: an unleased mutation could race another session's engine run."""
    with pytest.raises(StudioError) as exc:
        runner.run_sync(key, repo, ".kiro", **GOLDEN[key][0])
    assert exc.value.code == "internal_error"
    assert exc.value.details["lease"] == "admin"


def test_admin_verb_runs_with_a_lease_generation(runner, repo):
    result = runner.run_sync("utility.intent_switch", repo, ".kiro", dir_name=ALPHA, lease_generation=1)
    assert result.ok
    assert cursor(repo).read_text().strip() == ALPHA


# --------------------------------------------------------------------------- #
# async wrapper and activity
# --------------------------------------------------------------------------- #


def test_run_uses_a_worker_thread_and_drains_activity(engine_mod, fake_bun, clock, repo):
    sink = AsyncActivity()
    runner = engine_mod.EngineRunner(bun_path=fake_bun, clock=clock, activity=sink)

    async def scenario():
        result = await runner.run("utility.version", repo, ".kiro", repo_id="r_000000000001")
        assert result.ok
        assert runner.pending_activity == 0
        return result

    asyncio.run(scenario())
    assert len(sink.rows) == 1
    row = sink.rows[0]
    assert row["kind"] == "engine.run"
    assert row["repo_id"] == "r_000000000001"
    assert row["params"]["verb_key"] == "utility.version"
    assert row["params"]["exit_code"] == 0
    assert "duration_ms" in row["params"]
    assert [e["ref"] for e in row["evidence"]] == ["engine.stdout"]


def test_run_never_blocks_the_event_loop(engine_mod, clock, repo, tmp_path):
    """Two concurrent ``run`` calls against a sleeping child must overlap, not queue on the loop.

    One warm-up call first: the first piece of executor work in a process pays for thread creation and
    the platform's own subprocess machinery, and that fixed cost would otherwise be measured as if the
    two calls had serialised.
    """
    sleepy = write_stand_in(tmp_path / "sleepy-bun", "import time\ntime.sleep(0.4)\n")
    runner = engine_mod.EngineRunner(bun_path=sleepy, clock=clock, activity=AsyncActivity())

    async def scenario():
        await runner.run("utility.version", repo, ".kiro")
        started = time.monotonic()
        await asyncio.gather(
            runner.run("utility.version", repo, ".kiro"),
            runner.run("utility.version", repo, ".kiro"),
        )
        return time.monotonic() - started

    assert asyncio.run(scenario()) < 0.75


def test_sync_seam_records_immediately(engine_mod, fake_bun, clock, repo):
    """With a synchronous seam nothing is buffered, so an install step leaves its trace at once."""
    sink = SyncActivity()
    runner = engine_mod.EngineRunner(bun_path=fake_bun, clock=clock, activity=sink)
    runner.run_sync("utility.version", repo, ".kiro", repo_id="r_000000000001")
    assert len(sink.rows) == 1
    assert runner.pending_activity == 0


def test_failed_runs_are_recorded_as_attention(engine_mod, fake_bun, clock, repo):
    """A refused switch must leave a trace: it is the evidence a cursor_mismatch card points at."""
    sink = SyncActivity()
    runner = engine_mod.EngineRunner(bun_path=fake_bun, clock=clock, activity=sink)
    result = runner.run_sync(
        "utility.intent_switch", repo, ".kiro", dir_name="260101-nope", lease_generation=1
    )
    assert not result.ok
    assert sink.rows[-1]["severity"] == "attention"
    assert "engine.stderr" in [e["ref"] for e in sink.rows[-1]["evidence"]]


def test_activity_buffer_is_bounded(engine_mod, fake_bun, clock, repo, monkeypatch):
    """A sync-only caller must not be able to grow memory by never draining."""
    monkeypatch.setattr(engine_mod.EngineRunner, "_ACTIVITY_BUFFER", 3)
    runner = engine_mod.EngineRunner(bun_path=fake_bun, clock=clock, activity=AsyncActivity())
    for _ in range(5):
        runner.run_sync("utility.version", repo, ".kiro")
    assert runner.pending_activity == 3


# --------------------------------------------------------------------------- #
# cursor switch and read-back
# --------------------------------------------------------------------------- #


def test_switch_cursor_happy_path(runner, repo, security):
    """Two intents, cursor on BETA, switch to ALPHA: the pointer moves and every check agrees."""
    assert cursor(repo).read_text().strip() == BETA
    readback = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=4)

    assert readback.ok is True
    assert readback.mismatch == ()
    assert readback.space == "default"
    assert readback.dir_name == ALPHA
    assert readback.uuid == ALPHA_UUID
    state = repo / "aidlc" / "spaces" / "default" / "intents" / ALPHA / "aidlc-state.md"
    assert readback.state_sha256 == security.sha256_file(state)
    assert cursor(repo).read_text().strip() == ALPHA
    assert [r.verb_key for r in readback.results] == ["utility.intent_switch", "utility.intent_list"]


def test_readback_json_omits_the_subprocess_transcripts(runner, repo):
    readback = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=4)
    payload = readback.to_json()
    assert set(payload) == {"ok", "space", "dir_name", "uuid", "state_sha256", "mismatch"}
    json.dumps(payload)


def test_switch_skips_the_verbs_but_never_the_readback(runner, repo):
    """The cursor already names BETA: nothing is moved, yet the read-back still proves it."""
    before = cursor(repo).stat().st_mtime_ns
    readback = runner.switch_cursor_sync(repo, ".kiro", "default", BETA, BETA_UUID)
    assert readback.ok is True
    assert [r.verb_key for r in readback.results] == ["utility.intent_list"]
    assert cursor(repo).stat().st_mtime_ns == before


def test_switch_moves_the_space_when_it_differs(runner, repo_builder, security):
    builder = repo_builder.with_engine("devdelta").with_workspace(active_space="default")
    builder.with_intent(BETA, state=F.real_state(), uuid=BETA_UUID, active=True)
    builder.with_workspace(active_space="alt")
    builder.with_intent(ALPHA, state=F.real_state(), uuid=ALPHA_UUID, space="alt", active=True)
    tree = builder.build()
    (tree / "aidlc" / "active-space").write_text("default\n")

    readback = runner.switch_cursor_sync(tree, ".kiro", "alt", ALPHA, ALPHA_UUID, lease_generation=2)
    assert readback.ok is True, readback.mismatch
    assert (tree / "aidlc" / "active-space").read_text().strip() == "alt"
    assert [r.verb_key for r in readback.results] == [
        "utility.space_switch",
        "utility.intent_switch",
        "utility.intent_list",
    ]


def test_crash_mid_switch_is_reported_never_assumed(runner, repo):
    """The engine's cursor write fails: Studio must report a mismatch, not proceed with a dispatch."""
    path = cursor(repo)
    path.chmod(0o444)
    try:
        readback = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=1)
    finally:
        path.chmod(0o644)

    assert readback.ok is False
    assert "active_intent" in readback.mismatch
    assert readback.dir_name == BETA, "the report names where the cursor really is"
    switch = [r for r in readback.results if r.verb_key == "utility.intent_switch"]
    assert switch and switch[0].ok is False, "the failing invocation is kept as evidence"


def test_mismatch_active_space(runner, repo_builder):
    builder = repo_builder.with_engine("devdelta").with_workspace(active_space="default")
    builder.with_intent(ALPHA, state=F.real_state(), uuid=ALPHA_UUID, active=True)
    tree = builder.build()
    (tree / "aidlc" / "spaces" / "alt" / "intents").mkdir(parents=True)
    (tree / "aidlc" / "spaces" / "alt" / "intents" / "intents.json").write_text("[]\n")
    space_file = tree / "aidlc" / "active-space"
    space_file.chmod(0o444)
    try:
        readback = runner.switch_cursor_sync(tree, ".kiro", "alt", ALPHA, None, lease_generation=1)
    finally:
        space_file.chmod(0o644)
    assert readback.ok is False
    assert "active_space" in readback.mismatch
    assert readback.space == "default"


def test_mismatch_state_missing(runner, repo):
    (repo / "aidlc" / "spaces" / "default" / "intents" / ALPHA / "aidlc-state.md").unlink()
    readback = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=1)
    assert readback.ok is False
    assert "state_missing" in readback.mismatch
    assert readback.state_sha256 is None


def test_mismatch_uuid(runner, repo):
    readback = runner.switch_cursor_sync(
        repo, ".kiro", "default", ALPHA, "01a00000-0000-7000-8000-00000000ffff", lease_generation=1
    )
    assert readback.ok is False
    assert readback.mismatch == ("uuid",)
    assert readback.uuid == ALPHA_UUID


def test_mismatch_intent_list_active_when_the_engine_view_is_unavailable(runner, repo):
    """No engine answer means no proof; the cursor switch is refused rather than believed."""
    registry = repo / "aidlc" / "spaces" / "default" / "intents" / "intents.json"
    registry.write_text("{not json at all")
    readback = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=1)
    assert readback.ok is False
    assert "intent_list_active" in readback.mismatch
    assert readback.uuid is None


#: A stand-in that resolves ``intent --json``'s ``active`` the way 2.7.1 does: the calling session's
#: binding first, the cursor second (``resolveWorkflowSelection``). The binding lives in a file so the
#: child can be asked about it between invocations, and ``STICKY`` models the case the repair cannot fix
#: — a binding the switch verb does not re-stamp. Only the two verbs the read-back path uses are
#: implemented; anything else exits 3 so a stray invocation is visible.
_SELECTION_BUN = """
import json, sys
from pathlib import Path

BINDING = Path({binding!r})
STICKY = {sticky!r}

args = sys.argv[2:]
flags, positional = {{}}, []
i = 0
while i < len(args):
    token = args[i]
    if token.startswith("--"):
        if i + 1 < len(args) and not args[i + 1].startswith("--"):
            flags[token[2:]] = args[i + 1]; i += 2
        else:
            flags[token[2:]] = "true"; i += 1
    else:
        positional.append(token); i += 1

repo = Path(flags["project-dir"])
space = (repo / "aidlc" / "active-space").read_text().strip()
intents = repo / "aidlc" / "spaces" / space / "intents"
cursor = (intents / "active-intent").read_text().strip()
rows = json.loads((intents / "intents.json").read_text())
binding = BINDING.read_text().strip() if BINDING.is_file() else ""

if positional[:1] == ["intent"] and flags.get("json") == "true":
    active = binding or cursor
    print(json.dumps({{
        "active": active,
        "space": space,
        "intents": [
            {{"dirName": row["dirName"], "uuid": row.get("uuid"), "slug": row.get("slug"),
              "status": row.get("status"), "active": row["dirName"] == active}}
            for row in rows
        ],
    }}))
elif positional[:1] == ["intent"]:
    (intents / "active-intent").write_text(positional[1] + chr(10))
    if not STICKY:
        BINDING.write_text(positional[1] + chr(10))
    print("Active intent -> " + positional[1])
else:
    sys.exit(3)
"""


def selection_runner(engine_mod, clock, tmp_path, *, binding: str, sticky: bool = False):
    """A runner whose engine reports ``active`` from a session binding rather than from the cursor."""
    binding_file = tmp_path / "session-binding"
    binding_file.write_text(binding + "\n")
    bun = write_stand_in(
        tmp_path / "selection-bun",
        _SELECTION_BUN.format(binding=str(binding_file), sticky=sticky),
    )
    return engine_mod.EngineRunner(bun_path=bun, clock=clock, activity=SyncActivity())


def test_a_stale_session_binding_is_repaired_rather_than_refused(engine_mod, clock, repo, tmp_path):
    """The cursor files are right and the engine still names another intent: switch once, then re-read.

    This is 2.7.1's ``resolveWorkflowSelection`` seen from Studio: the gateway's pid is registered under
    ``aidlc/.aidlc-sessions/pids/``, so the bun child inherits a session whose binding may name earlier
    work. Refusing the human's decision would be a dead end — the switch verbs are skipped exactly
    because the files already agree — so the switch is forced once and only the second read-back counts.
    """
    cursor(repo).write_text(ALPHA + "\n")
    runner = selection_runner(engine_mod, clock, tmp_path, binding=BETA)

    readback = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=3)

    assert readback.ok is True
    assert readback.mismatch == ()
    assert readback.dir_name == ALPHA and readback.uuid == ALPHA_UUID
    assert [r.verb_key for r in readback.results] == [
        "utility.intent_list",
        "utility.intent_switch",
        "utility.intent_list",
    ]


def test_the_repair_switch_is_attempted_at_most_once(engine_mod, clock, repo, tmp_path):
    """A binding the switch does not re-stamp must come back as a mismatch, never as a retry loop."""
    cursor(repo).write_text(ALPHA + "\n")
    runner = selection_runner(engine_mod, clock, tmp_path, binding=BETA, sticky=True)

    readback = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=3)

    assert readback.ok is False
    assert readback.mismatch == ("intent_list_active",)
    assert [r.verb_key for r in readback.results].count("utility.intent_switch") == 1
    assert [r.verb_key for r in readback.results] == [
        "utility.intent_list",
        "utility.intent_switch",
        "utility.intent_list",
    ]


def test_a_real_divergence_is_never_repaired(engine_mod, clock, repo, tmp_path):
    """Two guards on the repair: the cursor file really disagreeing, and a uuid that does not match.

    Either one means the engine's view is not the only thing that is wrong, so nothing is forced and the
    caller is told. The read-back transcript is the proof: no second listing, hence no repair pass.
    """
    runner = selection_runner(engine_mod, clock, tmp_path, binding=BETA, sticky=True)
    path = cursor(repo)
    path.chmod(0o444)  # the cursor cannot be moved: `active_intent` mismatches too
    try:
        divergent = runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=1)
    finally:
        path.chmod(0o644)
    assert divergent.ok is False
    assert "active_intent" in divergent.mismatch
    assert [r.verb_key for r in divergent.results] == ["utility.intent_switch", "utility.intent_list"]

    path.write_text(ALPHA + "\n")
    wrong_uuid = runner.switch_cursor_sync(
        repo, ".kiro", "default", ALPHA, "01a00000-0000-7000-8000-00000000ffff", lease_generation=1
    )
    assert wrong_uuid.ok is False
    assert wrong_uuid.mismatch == ("intent_list_active", "uuid")
    assert [r.verb_key for r in wrong_uuid.results] == ["utility.intent_list"]


def test_switch_validates_its_own_arguments(runner, repo):
    with pytest.raises(ValueError):
        runner.switch_cursor_sync(repo, ".kiro", "default", "../escape", None)
    with pytest.raises(ValueError):
        runner.switch_cursor_sync(repo, ".kiro", "../escape", ALPHA, None)


def test_corrupt_cursor_file_cannot_aim_a_read_outside_the_repo(runner, repo):
    """``aidlc/active-space`` is repository content; its bytes must never become a path.

    The file is made unwritable as well, so the engine cannot simply repair it and the read-back has
    to compare the traversal string — which it does as a string, never as a path.
    """
    space_file = repo / "aidlc" / "active-space"
    space_file.write_text("../../../../etc\n")
    space_file.chmod(0o444)
    try:
        readback = runner.switch_cursor_sync(
            repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=1
        )
    finally:
        space_file.chmod(0o644)
    assert readback.ok is False
    assert "active_space" in readback.mismatch
    assert readback.space == "../../../../etc"


# --------------------------------------------------------------------------- #
# static policy checks over backend/
# --------------------------------------------------------------------------- #

FORBIDDEN_TOOL_FRAGMENTS = ("aidlc-orchestrate", "aidlc-audit", "aidlc-log", "aidlc-jump")
#: Exactly the tokens §1.9 names. Deliberately not the whole of ``ENGINE_FORBIDDEN_VERBS``: words like
#: ``set`` or ``merge`` are ordinary English that appear in Studio's own vocabularies, and a test that
#: cried wolf on them would be turned off.
FORBIDDEN_VERB_TOKENS = frozenset(
    "approve reject revise advance finalize complete-workflow gate-start checkbox park unpark".split()
)
#: Studio's own decision vocabulary. ``"approve"`` there is the name of a *card decision*, resolved
#: into wire text; it is a different namespace from an engine subcommand and must not trip the scan.
#: ``DECISIONS`` (action type → legal decisions, §1.12) is the same vocabulary keyed the other way; every
#: module that offers or validates a decision reads it from there rather than spelling one out, which is
#: what keeps the scan meaningful outside these tables.
VOCABULARY_TABLES = frozenset(
    {"DECISION_KIND", "DECISIONS", "HUMAN_LANE_DECISIONS", "HOST_CONTROL_DECISIONS",
     "STUDIO_ONLY_DECISIONS"}
)
#: Assignments whose whole point is to name what must never run. A forbidden token inside one of these
#: is the policy; anywhere else it is a call waiting to happen.
POLICY_TABLES = frozenset(
    {
        "ENGINE_DENY",
        "ENGINE_FORBIDDEN_TOOLS",
        "ENGINE_FORBIDDEN_VERBS",
        "ENGINE_READ_VERBS",
        "ENGINE_ADMIN_VERBS",
        "ENGINE_VERB_ALIASES",
        "ENGINE_UNAVAILABLE_VERBS",
        "GIT_WRITE_VERBS",
        "GIT_READ_VERBS",
        "GIT_FORBIDDEN_TOKENS",
        "GIT_FORBIDDEN_PREFIXES",
    }
)
EXEMPT_TABLES = POLICY_TABLES | VOCABULARY_TABLES


def backend_sources() -> list[Path]:
    return sorted((APP_ROOT / "backend").rglob("*.py"))


def _policy_spans(tree: ast.AST) -> list[tuple[int, int]]:
    spans = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(t, ast.Name) and t.id in POLICY_TABLES for t in targets):
            spans.append((node.lineno, node.end_lineno or node.lineno))
    return spans


def _pinned_human_wire_spans(tree: ast.AST) -> list[tuple[int, int]]:
    """Only the exact, reviewed human-message string may name an agent-side audit command.

    An argv, call, modified string or different assignment is not exempt. Runtime allowlist and
    no-backend-dispatch tests still apply; this string is previewed to the user and sent by the UI.
    """
    expected = json.loads((APP_ROOT / "docs/design/wire-text.json").read_text())["constants"][
        "WIRE_GROUPED_ANSWER_SUFFIX"
    ]
    return [
        (node.lineno, node.end_lineno or node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "WIRE_GROUPED_ANSWER_SUFFIX"
        and isinstance(node.value, ast.Constant) and node.value.value == expected
    ]


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
    """List/tuple literals whose strings look like a command line, with their line number.

    "Looks like a command line" means the sequence itself names the interpreter or one of the engine's
    tool files. That is the only shape in which a verb token is dangerous: a bare ``"approve"`` compared
    against a decision kind cannot run anything, while ``["bun", tool, "approve"]`` can.
    """
    out: list[tuple[int, list[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        strings = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if not strings:
            continue
        if any(s == "bun" or s.endswith(".ts") for s in strings):
            out.append((node.lineno, strings))
    return out


def test_no_forbidden_engine_tool_appears_in_backend_code():
    """A denied tool name may not exist as a string outside a deny table.

    Tool names are unambiguous — ``aidlc-orchestrate.ts`` has no second meaning — so any occurrence
    outside the tables that exist to forbid it is a call waiting to happen.
    """
    offences: list[str] = []
    for path in backend_sources():
        tree = ast.parse(path.read_text("utf-8"))
        for literal in _code_literals(tree, allow_pinned_wire=path.name == "constants.py"):
            if any(fragment in literal for fragment in FORBIDDEN_TOOL_FRAGMENTS):
                offences.append(f"{path.name}: {literal!r}")
    assert offences == [], offences


def test_no_forbidden_verb_reaches_an_argv():
    """A transition verb may not appear in anything shaped like a command line.

    Scanning for the bare word instead would flag Studio's own decision vocabulary — ``approve`` is a
    card decision as well as an engine subcommand — and a test that cries wolf on ordinary code gets
    switched off, which is worse than not having it. The failure this must catch is a verb reaching an
    argv, so that is exactly what it looks for.
    """
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
    found = [
        token
        for _lineno, strings in _argv_like_sequences(tree)
        for token in strings
        if token in FORBIDDEN_VERB_TOKENS
    ]
    assert found == ["approve"]


def test_forbidden_tool_names_appear_only_in_comments_or_deny_tables():
    """The plain-text half of the grep, so a token hidden in a way ast misses still fails."""
    offences: list[str] = []
    for path in backend_sources():
        text = path.read_text("utf-8")
        tree = ast.parse(text)
        spans = _policy_spans(tree)
        if path.name == "constants.py":
            spans += _pinned_human_wire_spans(tree)
        for number, line in enumerate(text.splitlines(), start=1):
            if not any(fragment in line for fragment in FORBIDDEN_TOOL_FRAGMENTS):
                continue
            if line.lstrip().startswith("#"):
                continue
            if any(lo <= number <= hi for lo, hi in spans):
                continue
            offences.append(f"{path.name}:{number}: {line.strip()}")
    assert offences == [], offences


def test_pinned_human_wire_exception_cannot_hide_a_backend_command():
    expected = json.loads((APP_ROOT / "docs/design/wire-text.json").read_text())["constants"][
        "WIRE_GROUPED_ANSWER_SUFFIX"
    ]
    tree = ast.parse(f'WIRE_GROUPED_ANSWER_SUFFIX = {expected!r}\nargv = ["bun", "aidlc-log.ts", "answer"]')
    assert _pinned_human_wire_spans(tree) == [(1, 1)]
    assert "aidlc-log.ts" in _code_literals(tree, allow_pinned_wire=True)
    assert _argv_like_sequences(tree) == [(2, ["bun", "aidlc-log.ts", "answer"])]
    changed = ast.parse(f'WIRE_GROUPED_ANSWER_SUFFIX = {expected + " modified"!r}')
    assert _pinned_human_wire_spans(changed) == []


def test_no_shell_execution_anywhere_in_backend():
    for path in backend_sources():
        text = path.read_text("utf-8")
        assert "shell=True" not in text, path
        assert "os.system" not in text, path
        assert "os.popen" not in text, path
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "shell":
                    continue
                assert isinstance(keyword.value, ast.Constant) and keyword.value.value is False, (
                    f"{path.name}:{node.lineno} passes a non-literal-False shell="
                )


_SPAWN_ATTRS = frozenset(
    {"run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
)


def test_only_the_subprocess_modules_spawn_anything():
    """One door per external tool: a stray spawn elsewhere would bypass every guard in this file."""
    callers = set()
    for path in backend_sources():
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            base = node.func.value
            if isinstance(base, ast.Name) and base.id == "subprocess" and node.func.attr in _SPAWN_ATTRS:
                callers.add(path.name)
    assert callers <= {"engine.py", "git_observer.py", "installer.py"}, sorted(callers)
    assert "engine.py" in callers


# --------------------------------------------------------------------------- #
# the real engine's own dispatch table
# --------------------------------------------------------------------------- #


def _ask_real_engine(bun: str, tool: str, verb: str, cwd: Path, env: dict) -> str:
    completed = subprocess.run(
        [bun, str(PAYLOAD_TOOLS / tool), verb],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        timeout=60,
        shell=False,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return completed.stdout.decode("utf-8", "replace") + completed.stderr.decode("utf-8", "replace")


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not on PATH")
def test_allowlist_matches_engine(engine_mod, security, tmp_path):
    """Run the bundled tools and assert every allowlisted verb is one the engine itself lists.

    ``help`` renders the ``/aidlc`` slash-command surface; the authoritative verb list is what the
    unknown-command arm prints (``Available commands: …`` / ``Valid: …``). Both are read, so a rename
    upstream fails in CI rather than in a user's repository. The bundled payload is Studio's own file,
    the invocation touches no repository, and the environment is the same scrubbed one the runner uses.
    """
    bun = shutil.which("bun")
    env = security.build_subprocess_env()
    utility = _ask_real_engine(bun, "aidlc-utility.ts", "help", tmp_path, env) + _ask_real_engine(
        bun, "aidlc-utility.ts", "__studio_probe_unknown__", tmp_path, env
    )
    state = _ask_real_engine(bun, "aidlc-state.ts", "__studio_probe_unknown__", tmp_path, env)
    runtime = _ask_real_engine(bun, "aidlc-runtime.ts", "--help", tmp_path, env)

    assert "Available commands:" in utility
    assert "Valid:" in state
    for key, verb in engine_mod.ENGINE_ALLOWLIST.items():
        haystack = {"aidlc-utility.ts": utility, "aidlc-state.ts": state, "aidlc-runtime.ts": runtime}[verb.tool]
        assert verb.verb in haystack, f"{key}: the engine no longer lists {verb.verb!r}"
    assert not (tmp_path / "aidlc").exists(), "asking the engine for help must touch no workspace"


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not on PATH")
def test_denied_verbs_still_exist_upstream(engine_mod, security, tmp_path):
    """The deny list is only meaningful while the engine still has those verbs to refuse."""
    bun = shutil.which("bun")
    listing = _ask_real_engine(
        bun, "aidlc-state.ts", "__studio_probe_unknown__", tmp_path, security.build_subprocess_env()
    )
    denied = {
        entry.split(":", 1)[1]
        for entry in engine_mod.ENGINE_DENY
        if entry.startswith("aidlc-state.ts:")
    }
    assert denied, "the state-tool half of the deny list disappeared"
    missing = sorted(v for v in denied if v not in listing)
    assert missing == [], f"deny list names verbs the engine no longer has: {missing}"


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun is not on PATH")
def test_every_verb_the_state_tool_dispatches_is_either_allowlisted_or_denied(engine_mod, security,
                                                                             tmp_path):
    """No verb of ``aidlc-state.ts`` may be merely unmentioned; each is a read Studio runs or a deny row.

    ``test_denied_verbs_still_exist_upstream`` guards one direction — the deny list must not name a verb
    the engine dropped. This guards the other, which is the one a version bump breaks: a new mutator
    arrives, nothing refers to it, and the omission is invisible because the positive allowlist happens
    to refuse it anyway. ENGINE_DENY exists so that "a future allowlist edit that reached for one of
    them has to delete a line here first", and a verb nobody wrote down buys none of that. The engine's
    own unknown-subcommand listing is the denominator, so the gate cannot be satisfied by a stale table.
    """
    bun = shutil.which("bun")
    listing = _ask_real_engine(
        bun, "aidlc-state.ts", "__studio_probe_unknown__", tmp_path, security.build_subprocess_env()
    )
    assert "Valid:" in listing
    dispatched = {token.strip(' "}\n') for token in listing.split("Valid:", 1)[1].split(",")}
    dispatched.discard("")
    assert len(dispatched) > 20, dispatched          # the listing parsed, not one long token
    allowlisted = {
        verb.verb for verb in engine_mod.ENGINE_ALLOWLIST.values() if verb.tool == "aidlc-state.ts"
    }
    denied = {
        entry.split(":", 1)[1]
        for entry in engine_mod.ENGINE_DENY
        if entry.startswith("aidlc-state.ts:")
    }
    unclassified = sorted(dispatched - allowlisted - denied)
    assert unclassified == [], f"state verbs neither allowlisted nor denied: {unclassified}"


# --------------------------------------------------------------------------- #
# finding bun
# --------------------------------------------------------------------------- #


#: A stand-in that answers ``--version`` the way bun does. ``find_bun`` accepts a location only by
#: running it, so every "this one wins" test needs a file that really runs.
_PRINTS_VERSION = 'print("1.2.3")\n'
#: Executable, and useless: the shape of a wrapper script whose real binary was uninstalled.
_EXITS_NONZERO = "import sys\nsys.exit(1)\n"


@pytest.fixture
def no_bun_on_path(monkeypatch, tmp_path) -> Path:
    """A PATH with nothing in it — near enough to what launchd hands the desktop gateway.

    Every finder test needs this: without it the result would depend on whether the machine running the
    suite happens to have bun on PATH, which is exactly the accident the finder exists to survive.
    """
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    return empty


def test_bun_is_found_where_the_installers_put_it_when_path_has_none(
    engine_mod, no_bun_on_path, tmp_path
):
    """The measured defect: the gateway's PATH is four system directories and bun is in none of them."""
    installed = write_stand_in(tmp_path / "installed-bun", _PRINTS_VERSION)

    found = engine_mod.find_bun(candidates=[installed])

    assert (found.found, found.path, found.version) == (True, installed, "1.2.3")
    assert found.source == engine_mod.BUN_SOURCE_INSTALL_LOCATION
    assert found.searched == (installed,)


def test_the_users_own_path_wins_over_every_install_location(engine_mod, tmp_path, monkeypatch):
    """A gateway started from a shell must keep using the bun that shell would use."""
    shell_bin = tmp_path / "shell-bin"
    shell_bin.mkdir()
    on_path = write_stand_in(shell_bin / "bun", _PRINTS_VERSION)
    candidate = write_stand_in(tmp_path / "candidate-bun", _PRINTS_VERSION)
    monkeypatch.setenv("PATH", str(shell_bin))

    found = engine_mod.find_bun(candidates=[candidate])

    assert (found.path, found.source) == (on_path, engine_mod.BUN_SOURCE_PATH)
    assert found.searched == (on_path,)          # the install locations were never even looked at


def test_a_location_that_cannot_run_bun_never_wins(engine_mod, no_bun_on_path, tmp_path):
    """Existing is not enough: this path becomes argv[0] of every engine invocation.

    Three ways a candidate can be present and worthless — absent, not executable, and executable but
    unable to answer ``--version`` (a wrapper whose binary went away, or a wrong-architecture build).
    """
    missing = str(tmp_path / "not-installed" / "bun")
    unexecutable = tmp_path / "stub-bun"
    unexecutable.write_text("#!/bin/sh\necho 1.2.3\n")      # a plausible file, no execute bit
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


def test_the_home_relative_candidate_is_expanded_when_the_search_runs(
    engine_mod, no_bun_on_path, tmp_path, monkeypatch
):
    """``~/.bun/bin/bun`` is the default list's first entry, and HOME is read at call time, not at import.

    Driven through the shipped ``BUN_CANDIDATE_PATHS`` rather than an injected list, so the constant the
    docs and the UI copy name is proved to be the list the code actually walks.
    """
    home = tmp_path / "home"
    (home / ".bun" / "bin").mkdir(parents=True)
    installed = write_stand_in(home / ".bun" / "bin" / "bun", _PRINTS_VERSION)
    monkeypatch.setenv("HOME", str(home))

    found = engine_mod.find_bun()

    assert found.path == installed
    assert found.searched == (installed,)        # bun's own installer is tried before the package managers


def test_a_prober_that_raises_is_not_a_crash(engine_mod, no_bun_on_path, tmp_path):
    """``repo_registry`` hands over an injected prober; a failure there is "not this one", never an error.

    The finder is called during startup wiring and inside the add-repo preflight, so a raising prober
    that escaped would take down the gateway's boot rather than warning about a missing tool.
    """
    asked: list[str] = []

    def angry(path: str) -> str | None:
        asked.append(path)
        raise RuntimeError("boom")

    installed = write_stand_in(tmp_path / "installed-bun", _PRINTS_VERSION)

    found = engine_mod.find_bun(candidates=[installed], probe=angry)

    assert (found.found, found.searched) == (False, (installed,))
    assert asked == [installed]                  # the prober is the proof, so it was really consulted


# --------------------------------------------------------------------------- #
# the container's own bun lookup (§1.23 wiring, no test_services.py exists)
# --------------------------------------------------------------------------- #


def build_services(studio, ctx, clock, ids, **kwargs):
    """``Services.build`` with a thread pool this file remembers to close.

    ``git_path`` is deliberately not passed: the tests below run with an empty PATH, so no ``git`` is
    found and ``/health`` starts no git subprocess either.
    """
    services = studio.services.Services.build(
        ctx, clock=clock, ids=ids, app_root=APP_ROOT, boot_id="boot-find-bun", **kwargs
    )
    services.storage.open()
    services.settings.load()
    return services


def test_the_gateway_finds_bun_without_a_path_and_health_says_where(
    studio, fake_ctx, clock, ids, no_bun_on_path, tmp_path, monkeypatch
):
    """End to end on the reported failure: launchd's PATH, a real install, a healthy Studio.

    ``tools.bun`` gains ``source``/``searched`` additively (§2.1 keeps ``found``/``path``/``version``),
    which is what lets the dashboard say "found where bun's installer puts it" instead of nothing.
    """
    installed = write_stand_in(tmp_path / "installed-bun", _PRINTS_VERSION)
    monkeypatch.setattr(studio.engine, "BUN_CANDIDATE_PATHS", (installed,))

    services = build_services(studio, fake_ctx, clock, ids, bun_version=lambda path: "1.2.3")
    try:
        assert services.bun_path == installed
        assert services.engine.bun_path == installed
        body = services.health()
    finally:
        services.scan_pool.shutdown(wait=False)

    assert body["tools"]["bun"] == {
        "found": True,
        "path": installed,
        "configured_path": None,
        "version": "1.2.3",
        "source": "install_location",
        "searched": [installed],
    }
    assert "bun_missing" not in body["issues"]


def test_health_names_the_locations_when_bun_really_is_absent(
    studio, fake_ctx, clock, ids, no_bun_on_path, tmp_path, monkeypatch
):
    """Still ``bun_missing`` when bun is genuinely not installed — with the search on the wire."""
    nowhere = str(tmp_path / "not-installed" / "bun")
    monkeypatch.setattr(studio.engine, "BUN_CANDIDATE_PATHS", (nowhere,))

    services = build_services(studio, fake_ctx, clock, ids)
    try:
        assert services.bun_path is None
        body = services.health()
    finally:
        services.scan_pool.shutdown(wait=False)

    assert body["tools"]["bun"] == {
        "found": False,
        "path": None,
        "configured_path": None,
        "version": None,
        "source": None,
        "searched": [nowhere],
    }
    assert "bun_missing" in body["issues"]


def test_an_explicit_bun_path_still_wins_over_the_search(
    studio, fake_ctx, clock, ids, fake_bun, no_bun_on_path, monkeypatch
):
    """Tests name the binary and must keep doing so: ``fake_bun`` answers ``--version`` with a refusal.

    So the search is not merely outranked, it does not happen — running the stand-in would put a line in
    the tripwire's log and fail this file.
    """

    def never(**kwargs):
        raise AssertionError("find_bun must not run when the caller named the binary")

    monkeypatch.setattr(studio.services, "find_bun", never)

    services = build_services(
        studio, fake_ctx, clock, ids, bun_path=fake_bun, bun_version=lambda path: "1.1.42"
    )
    try:
        assert services.bun_path == fake_bun
        assert (services.bun_source, services.bun_searched) == ("explicit", ())
    finally:
        services.scan_pool.shutdown(wait=False)


# --------------------------------------------------------------------------- #
# the tripwire, last on purpose
# --------------------------------------------------------------------------- #


def test_no_denied_invocation_was_ever_attempted(runner, repo, denied_log):
    """Run a representative slice of the allowlist, then assert both refusal logs are still empty."""
    runner.run_sync("utility.version", repo, ".kiro")
    runner.run_sync("utility.detect", repo, ".kiro")
    runner.run_sync("utility.intent_list", repo, ".kiro")
    runner.run_sync("utility.space_list", repo, ".kiro")
    runner.run_sync("state.get", repo, ".kiro", field="Current Stage")
    runner.run_sync("state.resume", repo, ".kiro")
    runner.run_sync("state.count", repo, ".kiro")
    runner.run_sync("state.lookup", repo, ".kiro", query="phase-of", arg="user-stories")
    runner.run_sync("utility.doctor", repo, ".kiro", _confirmed=True, lease_generation=1)
    runner.run_sync("utility.recompose", repo, ".kiro", skip="nfr-design", lease_generation=1)
    runner.run_sync("utility.scope_change", repo, ".kiro", scope="feature", lease_generation=1)
    runner.run_sync("utility.config_change", repo, ".kiro", depth="Minimal", lease_generation=1)
    runner.run_sync(
        "utility.intent_create",
        repo,
        ".kiro",
        scope="poc",
        arguments="Ship it",
        label="new-thing",
        lease_generation=1,
    )
    runner.switch_cursor_sync(repo, ".kiro", "default", ALPHA, ALPHA_UUID, lease_generation=1)

    for log in (denied_log, DEFAULT_DENIED_LOG):
        text = log.read_text("utf-8") if log.exists() else ""
        assert text == "", f"{log} recorded a refused invocation: {text}"
