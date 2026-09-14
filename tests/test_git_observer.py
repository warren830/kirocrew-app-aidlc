"""Tests for ``backend/studio/git_observer.py``.

The properties that matter here are not "does it parse porcelain" (it does) but:

* **Nothing Studio spawns can write.** Every refusal is asserted at the policy boundary *and* at the
  process boundary: one test drives every public method and then reads the argv log the stand-in wrote,
  proving no write verb, no ``-c``/``-C``/``--git-dir``/``--output=`` and no option before the verb ever
  reached a child. The conftest fake's refusal log is asserted to stay empty as the second half of the
  same claim (a refusal there means a write verb got through).
* **A broken git degrades, it never hides the workflow.** Missing binary, missing repository, non-repo,
  wedged git and a poisoned environment each produce an ``available=False`` observation with a reason
  (FR-GIT-005), not an exception a route would turn into a 500.
* **Evidence is refused rather than truncated.** An oversized prior version comes back as ``None``; only
  the human-readable diff is truncated, and visibly.

Two kinds of stand-in are used, both real executables:

* ``fake_git`` from ``conftest`` — the shared harness, driven over the real harvested repository
  (``gate_repo``). Studio strips the environment of every child (only ``PATH``, ``HOME``, ``LANG``,
  ``LC_ALL``, ``TMPDIR`` survive), so ``FAKE_GIT_STATE`` never reaches it and it answers with its
  built-in defaults; those defaults are exactly what the integration tests here assert.
* ``scripted`` — a local stand-in that keeps its plan, its argv log and its env dump *beside the
  executable* for the same reason: a stripped environment cannot carry configuration. It is what makes
  exit codes, timeouts, oversized output and argv assertions expressible at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

# --------------------------------------------------------------------------- #
# a scripted, recording `git`
# --------------------------------------------------------------------------- #

_SCRIPTED_GIT = r'''#!/usr/bin/env python3
"""A scripted `git`. Its plan, argv log and env dump live beside the executable.

Studio spawns children with a stripped environment, so a stand-in cannot be configured through
environment variables: everything it needs is read from its own directory.
"""
import json, os, sys, time
from pathlib import Path

here = Path(__file__).resolve().parent
argv = sys.argv[1:]
with (here / "argv.log").open("a") as fh:
    fh.write(json.dumps(argv) + "\n")
(here / "env.json").write_text(json.dumps(dict(os.environ)))
(here / "cwd.txt").write_text(os.getcwd())
plan_path = here / "plan.json"
plan = json.loads(plan_path.read_text()) if plan_path.is_file() else {}
step = plan.get(argv[0] if argv else "", plan.get("*", {}))
if step.get("sleep"):
    time.sleep(float(step["sleep"]))
sys.stdout.write(step.get("stdout", "") * int(step.get("repeat", 1)))
sys.stderr.write(step.get("stderr", ""))
sys.exit(int(step.get("exit", 0)))
'''


class ScriptedGit:
    """One stand-in per test: ``plan`` what each verb answers, then read ``calls``."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = str(root / "git")

    def plan(self, mapping: dict) -> None:
        (self.root / "plan.json").write_text(json.dumps(mapping))

    @property
    def calls(self) -> list[list[str]]:
        log = self.root / "argv.log"
        if not log.is_file():
            return []
        return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]

    @property
    def verbs(self) -> list[str]:
        return [call[0] for call in self.calls if call]

    @property
    def env(self) -> dict:
        return json.loads((self.root / "env.json").read_text())

    @property
    def cwd(self) -> str:
        return (self.root / "cwd.txt").read_text()


@pytest.fixture
def scripted(tmp_path: Path) -> ScriptedGit:
    root = tmp_path / "scripted-git"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "git"
    path.write_text(_SCRIPTED_GIT)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return ScriptedGit(root)


# --------------------------------------------------------------------------- #
# fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def G(studio):
    module = studio.git_observer
    assert module is not None, getattr(studio, "git_observer__error", "git_observer.py did not import")
    return module


@pytest.fixture
def K(studio):
    return studio.constants


@pytest.fixture
def E(studio):
    return studio.errors


@pytest.fixture
def observer(G, fake_git, clock):
    """The observer over the shared conftest fake."""
    return G.GitObserver(fake_git, clock)


@pytest.fixture
def work(tmp_path: Path) -> Path:
    """A plain directory to use as ``cwd``; the scripted git never looks at its contents."""
    root = tmp_path / "work"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def real_git() -> str:
    """The operator's own git binary.

    One claim here cannot be made by any stand-in: what a *real* git does with a *real* repository's
    ``.git/config``. A stand-in would only prove that a fake ignores what we tell it to ignore.
    """
    path = shutil.which("git")
    if path is None:  # pragma: no cover - depends on the machine, not on the code under test
        pytest.skip("this claim is about real git's handling of repository-local config")
    return path


def real_repo(root: Path, git: str, config: tuple[str, str]) -> Path:
    """A real one-commit repository with a dirty working tree and ``config`` in its own ``.git/config``.

    The hostile key is written last on purpose: a ``core.fsmonitor`` in place before ``git commit`` would
    fire during the setup and prove nothing about Studio.
    """
    repo = root / "hostile-repo"
    repo.mkdir(parents=True, exist_ok=True)
    home = root / "elsewhere-home"  # never the operator's ~, so no real ~/.gitconfig can take part
    home.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "lab",
        "GIT_AUTHOR_EMAIL": "lab@example.invalid",
        "GIT_COMMITTER_NAME": "lab",
        "GIT_COMMITTER_EMAIL": "lab@example.invalid",
    }

    def run(*args: str) -> None:
        subprocess.run([git, *args], cwd=repo, env=env, check=True, capture_output=True)

    (repo / "aidlc-state.md").write_text("# Current Stage: Inception\n", encoding="utf-8")
    run("-c", "init.defaultBranch=main", "init", "-q", ".")
    run("add", "aidlc-state.md")
    run("commit", "-qm", "one")
    # "Construct" is the same byte count as "Inception", so git cannot decide "modified" from stat data
    # alone and has to read the file's content — the path where a repository's converters get their turn.
    (repo / "aidlc-state.md").write_text("# Current Stage: Construct\n", encoding="utf-8")
    (repo / "untracked.md").write_text("mine\n", encoding="utf-8")
    run("config", *config)
    return repo


def marker_payload(root: Path, name: str) -> tuple[Path, Path]:
    """An executable that touches a marker and exits 0, and the marker path. Kept outside the repository
    so its presence is never confused with a file git itself wrote."""
    marker = root / f"{name}.marker"
    payload = root / f"{name}.sh"
    payload.write_text(f'#!/bin/sh\n: > "{marker}"\nexit 0\n', encoding="utf-8")
    payload.chmod(payload.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return payload, marker


def status_text(
    *,
    oid: str = "a" * 40,
    head: str = "main",
    upstream: str | None = None,
    ab: str | None = None,
    entries: tuple[str, ...] = (),
) -> str:
    lines = [f"# branch.oid {oid}", f"# branch.head {head}"]
    if upstream:
        lines.append(f"# branch.upstream {upstream}")
    if ab:
        lines.append(f"# branch.ab {ab}")
    return "\n".join([*lines, *entries]) + "\n"


def changed(path: str, xy: str = ".M") -> str:
    zeros = "0" * 40
    return f"1 {xy} N... 100644 100644 100644 {zeros} {zeros} {path}"


def renamed(path: str, orig: str, xy: str = "R.") -> str:
    zeros = "0" * 40
    return f"2 {xy} N... 100644 100644 100644 {zeros} {zeros} R100 {path}\t{orig}"


def untracked(path: str) -> str:
    return f"? {path}"


def log_line(sha: str, at: str, subject: str) -> str:
    return f"{sha}\x1f{at}\x1f{subject}"


def tree_digest(root: Path) -> dict[str, str]:
    """Every path under ``root`` as ``relpath -> sha256`` (or a marker), for a "nothing changed" claim."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            out[rel] = "symlink:" + os.readlink(path)
        elif path.is_file():
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_dir():
            out[rel] = "dir"
    return out


# --------------------------------------------------------------------------- #
# policy: nothing that can write is ever built or spawned
# --------------------------------------------------------------------------- #


def test_every_write_verb_is_refused_and_never_spawned(G, K, E, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    for verb in sorted(K.GIT_WRITE_VERBS):
        with pytest.raises(E.StudioError) as excinfo:
            G.GitObserver.check_argv([verb])
        assert excinfo.value.code == "internal_error"
        with pytest.raises(E.StudioError):
            obs.run_sync(work, [verb, "--dry-run"])
    assert scripted.calls == [], "a refused argv must never reach a child process"


def test_global_options_before_the_verb_are_refused(G, E, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    shapes = (
        [],
        ["--no-decorate", "log"],
        ["-c", "core.fsmonitor=x", "status"],
        ["-C", "/", "status"],
        ["--git-dir=/tmp/x", "status"],
        ["log", "--output=/tmp/x"],
        ["log", "--exec-path=/tmp/x"],
        ["status", "--git-dir", "/tmp/x"],
        ["log", "--config-env=core.pager=x"],
    )
    for shape in shapes:
        with pytest.raises(E.StudioError) as excinfo:
            obs.run_sync(work, shape)
        assert excinfo.value.code == "internal_error", shape
    assert scripted.calls == []


def test_an_unlisted_verb_is_refused_even_when_it_looks_harmless(G, E, work, scripted, clock):
    obs = G.GitObserver(scripted.path, clock)
    for verb in ("fsck", "archive", "bugreport", "help"):
        with pytest.raises(E.StudioError):
            obs.run_sync(work, [verb])
    assert scripted.calls == []


def test_a_read_argv_is_accepted_verbatim(G):
    assert G.GitObserver.check_argv(["log", "--no-decorate", "-n", "3"]) == (
        "log",
        "--no-decorate",
        "-n",
        "3",
    )


def test_a_nul_byte_in_an_argument_is_refused(G, E):
    with pytest.raises(E.StudioError):
        G.GitObserver.check_argv(["log", "--", "a\x00b"])


def test_the_shared_checker_reads_the_argv_the_way_this_module_spawns_it(studio, E):
    """``security.assert_git_argv_readonly`` takes the argv WITHOUT the binary (§1.3).

    Pinned separately from ``check_argv`` because the convention is the whole check: read the same argv
    as if the binary were in slot 0 and ``["-C", "/", "status"]`` looks like a plain ``status`` — the
    shape that lets a caller read (or write) a different repository entirely. So the case list is
    §4.3's, asserted against the shared function directly rather than only through this module.
    """
    check = studio.security.assert_git_argv_readonly
    for argv in (
        [],
        ["--no-decorate", "log"],
        ["-c", "core.fsmonitor=x", "status"],
        ["log", "--output=/tmp/x"],
        ["-C", "/", "status"],
        ["commit", "-m", "x"],
        ["git", "log"],                      # the binary in slot 0 is not a verb → refused
    ):
        with pytest.raises(E.StudioError) as excinfo:
            check(argv)
        assert excinfo.value.code == "internal_error", argv
    for argv in (["log", "--no-decorate", "-n", "3"], ["status", "--porcelain=v2", "--branch"],
                 ["rev-parse", "--git-common-dir"], ["show", "HEAD:file"]):
        check(argv)                          # no raise


def test_every_spawned_argv_is_a_read_with_no_global_options(G, K, scripted, clock, work):
    """Drive the whole public surface, then audit what actually reached a child."""
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan(
        {
            "status": {"stdout": status_text(upstream="origin/main", ab="+1 -2", entries=(changed("a.md"),))},
            "log": {"stdout": log_line("f" * 40, "2026-09-04T10:00:00Z", "work") + "\n"},
            "rev-parse": {"stdout": ".git\n"},
            "show": {"stdout": "old\n"},
            "cat-file": {"stdout": "4\n"},
            "diff": {"stdout": "@@ -1 +1 @@\n"},
            "--version": {"stdout": "git version 2.50.1\n"},
        }
    )
    obs.version()  # first, so the recorded cwd below belongs to a repository-scoped call
    obs.observe(work)
    obs.common_dir(work)
    obs.dirty_split(work, ["a.md"])
    obs.recent_commits(work, ["a.md"], limit=3)
    obs.prior_version(work, "a.md")
    obs.diff(work, "a.md")

    assert scripted.calls, "the stand-in recorded nothing; the test proves nothing"
    for call in scripted.calls:
        if call == ["--version"]:
            continue  # the one documented exemption: no repository, no config, no path
        assert call[0] in K.GIT_READ_VERBS, call
        assert call[0] not in K.GIT_WRITE_VERBS, call
        for token in call:
            assert token not in K.GIT_FORBIDDEN_TOKENS, call
            assert not token.startswith(K.GIT_FORBIDDEN_PREFIXES), call
            assert token not in K.GIT_WRITE_VERBS, call
    assert scripted.cwd == os.path.realpath(str(work)), "the repository is selected by cwd, never by -C"


def test_the_conftest_fake_never_records_a_refusal(observer, gate_repo, denied_log):
    """The fake exits 97 and logs when a write verb reaches it; that log must stay empty.

    Both destinations are checked: the fixture's path, and the fallback the fake uses when the
    environment (which Studio strips) does not carry ``FAKE_GIT_DENIED_LOG``.
    """
    fallback = Path("/tmp/aidlc-studio-git-denied.log")
    before = fallback.stat().st_size if fallback.is_file() else 0

    observation = observer.observe(gate_repo)
    observer.common_dir(gate_repo)
    observer.dirty_split(gate_repo, [".kiro"])
    observer.recent_commits(gate_repo, [], limit=5)
    observer.diff(gate_repo, "aidlc/spaces/default/intents")
    observer.version()

    assert observation.available is True
    assert observation.branch == "main"
    assert not denied_log.exists() or denied_log.read_text() == ""
    after = fallback.stat().st_size if fallback.is_file() else 0
    assert after == before, "the fake refused an invocation: a write verb reached it"


def test_the_child_environment_is_minimal_and_pins_git_to_a_read(G, K, scripted, clock, work, monkeypatch):
    monkeypatch.setenv("AIDLC_DEBUG", "1")  # must not be inherited
    monkeypatch.setenv("KIROCREW_TOKEN", "secret")
    monkeypatch.setenv("GIT_DIR", "/somewhere/else/.git")
    obs = G.GitObserver(scripted.path, clock)
    obs.common_dir(work)

    env = scripted.env
    # Not an equality check on the key set: the macOS system python3 the stand-in runs under injects
    # SDKROOT/CPATH/LIBRARY_PATH of its own. What Studio controls is asserted item by item.
    assert set(env) & {"AIDLC_DEBUG", "KIROCREW_TOKEN", "GIT_DIR", "GIT_WORK_TREE"} == set()
    inherited = {key for key in env if key in K.ENV_KEEP}
    assert inherited <= set(K.ENV_KEEP) and "PATH" in inherited
    assert env["NO_COLOR"] == "1"
    assert env["GIT_OPTIONAL_LOCKS"] == "0", "status must not be allowed to refresh (write) the index"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert not [key for key in env if key.startswith(K.ENV_FORBIDDEN_PREFIXES)]
    # Command-line precedence, the only layer above the observed repository's own .git/config, which
    # NOSYSTEM and GLOBAL do not touch: without these a repo-local core.fsmonitor is a program git runs.
    pins = {
        env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(int(env["GIT_CONFIG_COUNT"]))
    }
    assert pins["core.fsmonitor"] == "false"
    assert set(pins) >= {
        "core.fsmonitor",
        "core.alternateRefsCommand",
        "core.pager",
        "diff.external",
        "uploadpack.packObjectsHook",
    }


def test_a_repository_never_gets_to_name_the_program_git_runs_during_a_read(
    G, real_git, clock, tmp_path
):
    """The adversary owns the repository the user registered, and writes ``.git/config`` in it.

    ``core.fsmonitor`` there is a program git executes while running ``status``, ``diff`` and
    ``ls-files``; ``GIT_CONFIG_NOSYSTEM`` and ``GIT_CONFIG_GLOBAL`` drop the operator's config and not
    the repository's, and the argv check cannot see a key that never appears in the argv. So a GET on the
    git view would run that program with the gateway's own privileges, inside the repository whose
    ``aidlc-state.md`` and audit shards Studio promises never to write.
    """
    payload, marker = marker_payload(tmp_path, "fsmonitor")
    repo = real_repo(tmp_path, real_git, ("core.fsmonitor", str(payload)))
    observer = G.GitObserver(real_git, clock)

    observation = observer.observe(repo)
    observer.dirty_split(repo, ["."])
    observer.diff(repo, "aidlc-state.md")
    observer.recent_commits(repo, [], limit=3)
    observer.prior_version(repo, "aidlc-state.md")
    observer.common_dir(repo)

    assert not marker.exists(), "the repository's own config chose a program and a read verb ran it"
    # The pins must silence the repository without blinding the observation they protect.
    assert observation.available is True and observation.reason is None
    assert observation.branch == "main"
    assert observation.dirty is True and observation.dirty_files == 2


def test_a_bypass_variable_in_the_gateway_never_reaches_git(G, E, scripted, clock, work, monkeypatch):
    monkeypatch.setenv("AIDLC_SKIP_ARTIFACT_GUARD", "1")
    obs = G.GitObserver(scripted.path, clock)

    observation = obs.observe(work)
    assert observation.available is False
    assert observation.reason == G.REASON_ERROR
    with pytest.raises(E.StudioError) as excinfo:
        obs.run_sync(work, ["status"])
    assert excinfo.value.code == "internal_error"
    assert scripted.calls == []


# --------------------------------------------------------------------------- #
# observation
# --------------------------------------------------------------------------- #


def test_observe_reads_branch_head_and_a_clean_tree(observer, gate_repo, clock):
    observation = observer.observe(gate_repo)
    assert observation.available is True
    assert observation.reason is None
    assert observation.branch == "main"
    assert observation.detached is False
    assert observation.head == "0" * 40
    assert observation.dirty is False
    assert observation.dirty_files == 0
    assert observation.upstream is None
    assert observation.observed_at == clock.iso()
    assert observation.took_ms == 0, "timing must come from the injected clock, not time.monotonic"


def test_observe_counts_every_kind_of_dirty_entry(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan(
        {
            "status": {
                "stdout": status_text(
                    entries=(
                        changed("aidlc/spaces/default/intents/x/aidlc-state.md"),
                        renamed("docs/new name.md", "docs/old.md"),
                        untracked('"docs/caf\\303\\251.md"'),
                        "! build/ignored.txt",
                    )
                )
            },
            "log": {"stdout": log_line("b" * 40, "2026-09-04T09:00:00+00:00", "state moved") + "\n"},
        }
    )
    observation = obs.observe(work)
    assert observation.dirty is True
    assert observation.dirty_files == 3, "ignored entries are not dirt"
    assert observation.head_subject == "state moved"


def test_observe_reports_ahead_and_behind_from_the_status_header(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"status": {"stdout": status_text(upstream="origin/main", ab="+3 -7")}})
    observation = obs.observe(work)
    assert (observation.upstream, observation.ahead, observation.behind) == ("origin/main", 3, 7)
    assert "rev-list" not in scripted.verbs, "the header already answered; no extra process"


def test_observe_leaves_ahead_and_behind_unknown_without_an_upstream(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"status": {"stdout": status_text()}})
    observation = obs.observe(work)
    assert observation.upstream is None
    assert observation.ahead is None, "no upstream is not the same as 'in sync'"
    assert observation.behind is None
    assert "rev-list" not in scripted.verbs


def test_observe_falls_back_to_rev_list_when_the_header_is_missing(G, scripted, clock, work):
    """``@{upstream}...HEAD`` prints the BEHIND count first (verified against git 2.50)."""
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan(
        {
            "status": {"stdout": status_text(upstream="origin/main")},
            "rev-list": {"stdout": "2636\t0\n"},
        }
    )
    observation = obs.observe(work)
    assert observation.behind == 2636
    assert observation.ahead == 0
    assert ["rev-list", "--left-right", "--count", "@{upstream}...HEAD"] in scripted.calls


def test_observe_reports_a_detached_head(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"status": {"stdout": status_text(head="(detached)")}})
    observation = obs.observe(work)
    assert observation.detached is True
    assert observation.branch is None
    assert observation.available is True


def test_observe_falls_back_to_rev_parse_for_a_branchless_status(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan(
        {
            "status": {"stdout": "1 .M N... 100644 100644 100644 %s %s a.md\n" % ("0" * 40, "0" * 40)},
            "rev-parse": {"stdout": "HEAD\n"},
        }
    )
    observation = obs.observe(work)
    assert observation.detached is True
    assert observation.branch is None
    assert ["rev-parse", "--abbrev-ref", "HEAD"] in scripted.calls


def test_observe_reports_an_unborn_head_as_available(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"status": {"stdout": status_text(oid="(initial)")}, "log": {"exit": 128}})
    observation = obs.observe(work)
    assert observation.available is True
    assert observation.head is None
    assert observation.head_subject is None
    assert observation.branch == "main"


def test_observe_degrades_when_git_is_absent(G, clock, work):
    for path in (None, "", str(work / "no-such-git")):
        observation = G.GitObserver(path, clock).observe(work)
        assert observation.available is False
        assert observation.reason == G.REASON_GIT_MISSING
        assert observation.branch is None and observation.dirty_files == 0


def test_observe_degrades_when_the_directory_is_not_a_repository(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan(
        {"status": {"exit": 128, "stderr": "fatal: not a git repository (or any parent up to /)\n"}}
    )
    observation = obs.observe(work)
    assert observation.available is False
    assert observation.reason == G.REASON_NOT_A_REPO


def test_observe_degrades_to_error_for_an_unexplained_git_failure(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"status": {"exit": 128, "stderr": "fatal: detected dubious ownership\n"}})
    observation = obs.observe(work)
    assert observation.available is False
    assert observation.reason == G.REASON_ERROR


def test_observe_degrades_when_the_repository_directory_is_gone(observer, tmp_path, G):
    observation = observer.observe(tmp_path / "moved-away")
    assert observation.available is False
    assert observation.reason == G.REASON_ERROR, "a vanished repo is not a missing git binary"


def test_observe_gives_up_on_a_wedged_git_instead_of_blocking(G, K, scripted, clock, work, monkeypatch):
    monkeypatch.setattr(K, "GIT_TIMEOUT_SECS", 0.25)
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"status": {"sleep": 5, "stdout": status_text()}})
    observation = obs.observe(work)
    assert observation.available is False
    assert observation.reason == G.REASON_TIMEOUT


def test_run_sync_reports_a_timeout_without_raising(G, K, scripted, clock, work, monkeypatch):
    monkeypatch.setattr(K, "GIT_TIMEOUT_SECS", 0.25)
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"log": {"sleep": 5}})
    result = obs.run_sync(work, ["log", "-n", "1"])
    assert result.timed_out is True
    assert result.exit_code is None
    assert result.ok is False


def test_run_sync_redacts_credentials_out_of_stderr(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"log": {"exit": 128, "stderr": "fatal: token=0123456789abcdef failed\n"}})
    result = obs.run_sync(work, ["log"])
    assert "0123456789abcdef" not in result.stderr
    assert result.exit_code == 128


# --------------------------------------------------------------------------- #
# prior version and diff
# --------------------------------------------------------------------------- #


def test_prior_version_returns_the_committed_bytes(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"cat-file": {"stdout": "13\n"}, "show": {"stdout": "# Requirements\n"}})
    assert obs.prior_version(work, "inception/requirements.md") == b"# Requirements\n"
    assert ["cat-file", "-s", "HEAD:inception/requirements.md"] in scripted.calls
    assert ["show", "--no-textconv", "HEAD:inception/requirements.md"] in scripted.calls


def test_prior_version_refuses_an_oversize_blob_before_reading_it(G, K, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"cat-file": {"stdout": f"{K.MAX_ARTIFACT_RENDER_BYTES + 1}\n"}})
    assert obs.prior_version(work, "big.md") is None
    assert "show" not in scripted.verbs, "the size probe exists so the blob is never materialised"


def test_prior_version_refuses_an_oversize_blob_when_the_size_probe_is_silent(G, K, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan(
        {
            "cat-file": {"exit": 1},
            "show": {"stdout": "x" * 1024, "repeat": K.MAX_ARTIFACT_RENDER_BYTES // 1024 + 1},
        }
    )
    assert obs.prior_version(work, "big.md") is None, "half a file would make the diff view lie"
    assert "show" in scripted.verbs


def test_prior_version_is_none_for_an_untracked_file(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan(
        {
            "cat-file": {"exit": 128, "stderr": "fatal: path 'new.md' does not exist in 'HEAD'\n"},
            "show": {"exit": 128, "stderr": "fatal: path 'new.md' does not exist in 'HEAD'\n"},
        }
    )
    assert obs.prior_version(work, "new.md") is None


def test_prior_version_and_diff_refuse_a_path_that_leaves_the_repository(G, E, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    for bad in ("../../etc/passwd", "/etc/passwd", "a/../../outside.md"):
        with pytest.raises(E.StudioError) as excinfo:
            obs.prior_version(work, bad)
        assert excinfo.value.code == "bad_path", bad
        with pytest.raises(E.StudioError):
            obs.diff(work, bad)
    with pytest.raises(E.StudioError):
        obs.prior_version(work, "")  # the repository root is not an artifact
    assert scripted.calls == []


def test_prior_version_follows_no_symlink_out_of_the_repository(G, E, scripted, clock, work, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n")
    (work / "link.md").symlink_to(outside)
    obs = G.GitObserver(scripted.path, clock)
    with pytest.raises(E.StudioError) as excinfo:
        obs.prior_version(work, "link.md")
    assert excinfo.value.code == "bad_path"
    assert scripted.calls == []


def test_diff_is_truncated_visibly_rather_than_returned_whole(G, K, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"diff": {"stdout": "+" * 1024, "repeat": K.MAX_SUBPROCESS_STDOUT // 1024 + 2}})
    patch = obs.diff(work, "a.md")
    assert patch is not None
    assert "truncated" in patch.splitlines()[-1]
    assert len(patch) < K.MAX_SUBPROCESS_STDOUT + 200


def test_diff_separates_no_change_from_no_observation(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"diff": {"stdout": ""}})
    assert obs.diff(work, "a.md") == "", "an empty patch is information: the file matches the index"
    assert G.GitObserver(None, clock).diff(work, "a.md") is None
    scripted.plan({"diff": {"exit": 128, "stderr": "fatal: not a git repository\n"}})
    assert obs.diff(work, "a.md") is None


def test_diff_disables_repository_supplied_converters(G, scripted, clock, work):
    """A repo-local diff driver would otherwise run a binary of the repository's choosing on a read."""
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"diff": {"stdout": ""}})
    obs.diff(work, "a.md")
    call = next(c for c in scripted.calls if c[0] == "diff")
    assert "--no-ext-diff" in call and "--no-textconv" in call and "--no-color" in call
    assert call[-2:] == ["--", "a.md"]


# --------------------------------------------------------------------------- #
# commits
# --------------------------------------------------------------------------- #


def test_recent_commits_parses_rows_and_normalises_the_timestamp(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan(
        {
            "log": {
                "stdout": "\n".join(
                    (
                        log_line("a" * 40, "2026-09-04T12:49:31+02:00", "feat: add stage table"),
                        log_line("b" * 40, "2026-09-03T23:00:00Z", "chore: bump"),
                    )
                )
                + "\n"
            }
        }
    )
    commits = obs.recent_commits(work, ["aidlc/spaces/default"], limit=5)
    assert [c.sha for c in commits] == ["a" * 40, "b" * 40]
    assert commits[0].at == "2026-09-04T10:49:31Z", "commit times sort against audit rows"
    assert commits[0].subject == "feat: add stage table"
    assert commits[0].to_json() == {
        "sha": "a" * 40,
        "at": "2026-09-04T10:49:31Z",
        "subject": "feat: add stage table",
    }


def test_recent_commits_bounds_the_walk_and_separates_paths(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"log": {"stdout": ""}})
    obs.recent_commits(work, ["a.md", "docs"], limit=10_000)
    call = next(c for c in scripted.calls if c[0] == "log")
    assert call[call.index("-n") + 1] == str(G.MAX_LOG_LIMIT)
    assert call[call.index("--") + 1 :] == ["a.md", "docs"]
    assert "--no-decorate" in call


def test_recent_commits_omits_the_separator_when_no_path_is_given(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"log": {"stdout": ""}})
    obs.recent_commits(work, [])
    call = next(c for c in scripted.calls if c[0] == "log")
    assert "--" not in call


def test_recent_commits_is_empty_when_git_fails(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"log": {"exit": 128, "stderr": "fatal: your current branch has no commits\n"}})
    assert obs.recent_commits(work, []) == []
    assert G.GitObserver(None, clock).recent_commits(work, []) == []


# --------------------------------------------------------------------------- #
# dirty split and common dir
# --------------------------------------------------------------------------- #


def test_dirty_split_keeps_receipt_owned_drift_apart_from_the_users_work(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan(
        {
            "status": {
                "stdout": status_text(
                    entries=(
                        changed(".kiro/tools/aidlc-utility.ts", "M."),
                        untracked(".kiro/agents/extra.json"),
                        changed("src/app.py"),
                        changed("README.md"),
                    )
                )
            }
        }
    )
    split = obs.dirty_split(work, [".kiro", "AGENTS.md"])
    assert split["owned_dirty"] == [".kiro/agents/extra.json", ".kiro/tools/aidlc-utility.ts"]
    assert split["unrelated_dirty"] == 2, "the user's own work in progress never blocks an upgrade"
    assert split["owned_changes"] == [
        {"relpath": ".kiro/agents/extra.json", "status": "?"},
        {"relpath": ".kiro/tools/aidlc-utility.ts", "status": "M."},
    ]
    assert split["available"] is True and split["reason"] is None


def test_dirty_split_does_not_claim_a_clean_tree_when_git_failed(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"status": {"exit": 128, "stderr": "fatal: not a git repository\n"}})
    split = obs.dirty_split(work, [".kiro"])
    assert split == {
        "owned_dirty": [],
        "unrelated_dirty": 0,
        "owned_changes": [],
        "available": False,
        "reason": G.REASON_NOT_A_REPO,
        "truncated": False,
    }


def test_dirty_split_over_the_real_repository_reports_it_clean(observer, gate_repo):
    split = observer.dirty_split(gate_repo, ["aidlc", ".kiro"])
    assert split["available"] is True
    assert split["owned_dirty"] == []
    assert split["unrelated_dirty"] == 0


def test_common_dir_is_absolute_even_when_git_answers_relatively(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"rev-parse": {"stdout": ".git\n"}})
    assert obs.common_dir(work) == os.path.join(os.path.realpath(str(work)), ".git")


def test_common_dir_is_none_outside_a_repository(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"rev-parse": {"exit": 128, "stderr": "fatal: not a git repository\n"}})
    assert obs.common_dir(work) is None
    scripted.plan({"rev-parse": {"stdout": "\n"}})
    assert obs.common_dir(work) is None
    assert G.GitObserver(None, clock).common_dir(work) is None


def test_common_dir_over_the_real_repository(observer, gate_repo):
    assert observer.common_dir(gate_repo) == os.path.join(os.path.realpath(str(gate_repo)), ".git")


def test_version_reports_the_binary_or_nothing(G, scripted, clock):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"--version": {"stdout": "git version 2.50.1 (Apple Git-155)\n"}})
    assert obs.version() == "git version 2.50.1 (Apple Git-155)"
    assert obs.git_path == scripted.path
    blind = G.GitObserver(None, clock)
    assert blind.version() is None and blind.git_path is None
    assert G.GitObserver("", clock).git_path is None, "an empty path is 'no git', not a binary named ''"


# --------------------------------------------------------------------------- #
# shape of the values other modules consume
# --------------------------------------------------------------------------- #


def test_observation_json_matches_the_wire_contract(observer, gate_repo):
    payload = observer.observe(gate_repo).to_json()
    assert set(payload) == {
        "available",
        "reason",
        "branch",
        "detached",
        "head",
        "head_subject",
        "dirty",
        "dirty_files",
        "ahead",
        "behind",
        "upstream",
        "observed_at",
        "took_ms",
    }
    assert json.loads(json.dumps(payload)) == payload


def test_the_value_objects_are_frozen_and_slotted(G):
    cases = (
        (G.GitObservation.unavailable(G.REASON_ERROR, observed_at="2026-09-04T10:00:00Z", took_ms=0), "reason"),
        (G.GitCommit("a" * 40, "2026-09-04T10:00:00Z", "s"), "sha"),
        (G.GitResult(("log",), 0, "", "", False), "stdout"),
    )
    for value, field in cases:
        assert type(value).__slots__ is not None, type(value)
        assert not hasattr(value, "__dict__"), type(value)
        with pytest.raises(Exception):
            setattr(value, field, "mutated")


def test_unavailable_observations_use_only_the_documented_reasons(G):
    assert G.OBSERVATION_REASONS == ("git_missing", "not_a_repo", "timeout", "error")
    blank = G.GitObservation.unavailable(G.REASON_TIMEOUT, observed_at="2026-09-04T10:00:00Z", took_ms=7)
    assert blank.to_json()["reason"] == "timeout"
    assert blank.to_json()["took_ms"] == 7
    assert blank.available is False


def test_run_sync_result_carries_the_argv_it_ran(G, scripted, clock, work):
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"status": {"stdout": status_text()}})
    result = obs.run_sync(work, ["status", "--porcelain=v2", "--branch"])
    assert result.argv == ("status", "--porcelain=v2", "--branch")
    assert result.ok is True
    assert "# branch.head main" in result.stdout
    assert result.to_json()["argv"] == ["status", "--porcelain=v2", "--branch"]


def test_observing_a_repository_never_changes_a_byte_of_it(observer, gate_repo):
    """The whole product rests on this: Studio reads git, it does not run git against the tree."""
    before = tree_digest(gate_repo)
    observer.observe(gate_repo)
    observer.common_dir(gate_repo)
    observer.dirty_split(gate_repo, ["aidlc", ".kiro"])
    observer.recent_commits(gate_repo, ["aidlc/spaces/default/intents"], limit=5)
    observer.prior_version(gate_repo, "aidlc/spaces/default/intents/260904-gate-demo/aidlc-state.md")
    observer.diff(gate_repo, "aidlc/spaces/default/intents/260904-gate-demo/aidlc-state.md")
    assert tree_digest(gate_repo) == before


def test_a_missing_git_hides_nothing_but_git(G, clock, gate_repo):
    """FR-GIT-005: git being unavailable is a degraded capability, not a failed repository read."""
    blind = G.GitObserver(None, clock)
    observation = blind.observe(gate_repo)
    assert observation.available is False and observation.reason == G.REASON_GIT_MISSING
    assert observation.to_json()["branch"] is None
    assert blind.dirty_split(gate_repo, ["aidlc"])["available"] is False
    assert blind.recent_commits(gate_repo, []) == []
    assert blind.prior_version(gate_repo, "aidlc/spaces/default/intents/260904-gate-demo/aidlc-state.md") is None
    state = gate_repo / "aidlc" / "spaces" / "default" / "intents" / "260904-gate-demo" / "aidlc-state.md"
    assert "Current Stage" in state.read_text("utf-8"), "the workflow state is still there to read"
