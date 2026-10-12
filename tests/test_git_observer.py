"""``backend/studio/git_observer.py``: nothing Studio spawns can write (refusals are asserted at the policy boundary
*and* in the argv log a stand-in wrote), a broken git degrades to ``available=False`` with a reason (FR-GIT-005), and
oversized evidence is refused rather than truncated (only the human-readable diff is truncated, visibly).

``fake_git`` (conftest) answers with its built-in defaults because Studio strips every child's environment;
``scripted`` keeps its plan, argv log and env dump beside the executable for the same reason.
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

TS = "2026-09-04T10:00:00Z"
ZEROS = "0" * 40
GATE_STATE = "aidlc/spaces/default/intents/260904-gate-demo/aidlc-state.md"

_SCRIPTED_GIT = r'''#!/usr/bin/env python3
"""A scripted `git`: its plan, argv log and env dump live beside the executable (a stripped env carries nothing)."""
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
        self.root, self.path = root, str(root / "git")

    def plan(self, mapping: dict) -> None:
        (self.root / "plan.json").write_text(json.dumps(mapping))

    @property
    def calls(self) -> list[list[str]]:
        log = self.root / "argv.log"
        return [json.loads(line) for line in log.read_text().splitlines() if line.strip()] if log.is_file() else []

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
    (root / "git").write_text(_SCRIPTED_GIT)
    (root / "git").chmod((root / "git").stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return ScriptedGit(root)


@pytest.fixture
def G(studio):
    assert studio.git_observer is not None, getattr(studio, "git_observer__error", "git_observer.py did not import")
    return studio.git_observer


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
def obs(G, scripted, clock):
    """The observer over the scripted stand-in (tests that patch the environment or constants build their own)."""
    return G.GitObserver(scripted.path, clock)


@pytest.fixture
def work(tmp_path: Path) -> Path:
    """A plain directory to use as ``cwd``; the scripted git never looks at its contents."""
    (tmp_path / "work").mkdir(parents=True, exist_ok=True)
    return tmp_path / "work"


@pytest.fixture
def real_git() -> str:
    """The operator's own git: only a real git shows what it does with a real repository's ``.git/config``."""
    path = shutil.which("git")
    if path is None:  # pragma: no cover - depends on the machine, not on the code under test
        pytest.skip("this claim is about real git's handling of repository-local config")
    return path


def real_repo(root: Path, git: str, config: tuple[str, str]) -> Path:
    """A real one-commit repo with a dirty tree; ``config`` goes into ``.git/config`` last, so setup cannot fire it."""
    repo, home = root / "hostile-repo", root / "elsewhere-home"  # never the operator's ~ (or its ~/.gitconfig)
    repo.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "lab",
           "GIT_AUTHOR_EMAIL": "lab@example.invalid", "GIT_COMMITTER_NAME": "lab",
           "GIT_COMMITTER_EMAIL": "lab@example.invalid"}

    def run(*args: str) -> None:
        subprocess.run([git, *args], cwd=repo, env=env, check=True, capture_output=True)

    (repo / "aidlc-state.md").write_text("# Current Stage: Inception\n", encoding="utf-8")
    run("-c", "init.defaultBranch=main", "init", "-q", ".")
    run("add", "aidlc-state.md")
    run("commit", "-qm", "one")
    # same byte count as "Inception": git must read the content, where a repository's converters get their turn
    (repo / "aidlc-state.md").write_text("# Current Stage: Construct\n", encoding="utf-8")
    (repo / "untracked.md").write_text("mine\n", encoding="utf-8")
    run("config", *config)
    return repo


def status_text(*, oid: str = "a" * 40, head: str = "main", upstream: str | None = None, ab: str | None = None,
                entries: tuple[str, ...] = ()) -> str:
    lines = [f"# branch.oid {oid}", f"# branch.head {head}", *([f"# branch.upstream {upstream}"] if upstream else []),
             *([f"# branch.ab {ab}"] if ab else []), *entries]
    return "\n".join(lines) + "\n"


def changed(path: str, xy: str = ".M") -> str:
    return f"1 {xy} N... 100644 100644 100644 {ZEROS} {ZEROS} {path}"


def renamed(path: str, orig: str, xy: str = "R.") -> str:
    return f"2 {xy} N... 100644 100644 100644 {ZEROS} {ZEROS} R100 {path}\t{orig}"


def log_line(sha: str, at: str, subject: str) -> str:
    return f"{sha}\x1f{at}\x1f{subject}"


def tree_digest(root: Path) -> dict[str, str]:
    """Every path under ``root``: ``relpath -> sha256`` for files, a marker for symlinks and directories."""
    return {str(path.relative_to(root)): "symlink:" + os.readlink(path) if path.is_symlink()
            else hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir"
            for path in sorted(root.rglob("*")) if path.is_symlink() or path.is_file() or path.is_dir()}


# --- policy: nothing that can write is ever built or spawned ---
def test_every_write_verb_is_refused_and_never_spawned(G, K, E, obs, scripted, work):
    for verb in sorted(K.GIT_WRITE_VERBS):
        with pytest.raises(E.StudioError) as excinfo:
            G.GitObserver.check_argv([verb])
        assert excinfo.value.code == "internal_error"
        with pytest.raises(E.StudioError):
            obs._run(work, [verb, "--dry-run"], stdout_cap=1024)
    assert scripted.calls == [], "a refused argv must never reach a child process"


def test_global_options_before_the_verb_are_refused(E, obs, scripted, work):
    for shape in ([], ["--no-decorate", "log"], ["-c", "core.fsmonitor=x", "status"], ["-C", "/", "status"],
                  ["--git-dir=/tmp/x", "status"], ["log", "--output=/tmp/x"], ["log", "--exec-path=/tmp/x"],
                  ["status", "--git-dir", "/tmp/x"], ["log", "--config-env=core.pager=x"]):
        with pytest.raises(E.StudioError) as excinfo:
            obs._run(work, shape, stdout_cap=1024)
        assert excinfo.value.code == "internal_error", shape
    assert scripted.calls == []


def test_an_unlisted_verb_is_refused_even_when_it_looks_harmless(E, obs, scripted, work):
    for verb in ("fsck", "archive", "bugreport", "help"):
        with pytest.raises(E.StudioError):
            obs._run(work, [verb], stdout_cap=1024)
    assert scripted.calls == []


def test_a_read_argv_is_accepted_verbatim(G):
    assert G.GitObserver.check_argv(["log", "--no-decorate", "-n", "3"]) == ("log", "--no-decorate", "-n", "3")


def test_a_nul_byte_in_an_argument_is_refused(G, E):
    with pytest.raises(E.StudioError):
        G.GitObserver.check_argv(["log", "--", "a\x00b"])


def test_the_shared_checker_reads_the_argv_the_way_this_module_spawns_it(studio, E):
    """The shared checker takes the argv without the binary (§1.3), so ``-C / status`` never reads as ``status``."""
    check = studio.security.assert_git_argv_readonly
    for argv in ([], ["--no-decorate", "log"], ["-c", "core.fsmonitor=x", "status"], ["log", "--output=/tmp/x"],
                 ["-C", "/", "status"], ["commit", "-m", "x"], ["git", "log"]):  # a binary in slot 0 is not a verb
        with pytest.raises(E.StudioError) as excinfo:
            check(argv)
        assert excinfo.value.code == "internal_error", argv
    for argv in (["log", "--no-decorate", "-n", "3"], ["status", "--porcelain=v2", "--branch"],
                 ["rev-parse", "--git-common-dir"], ["show", "HEAD:file"]):
        check(argv)  # no raise


def test_every_spawned_argv_is_a_read_with_no_global_options(K, obs, scripted, work):
    """Drive the whole public surface, then audit what actually reached a child."""
    scripted.plan({"status": {"stdout": status_text(upstream="origin/main", ab="+1 -2", entries=(changed("a.md"),))},
                   "log": {"stdout": log_line("f" * 40, TS, "work") + "\n"}, "rev-parse": {"stdout": ".git\n"},
                   "show": {"stdout": "old\n"}, "cat-file": {"stdout": "4\n"}, "diff": {"stdout": "@@ -1 +1 @@\n"},
                   "--version": {"stdout": "git version 2.50.1\n"}})
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
        assert call[0] in K.GIT_READ_VERBS and call[0] not in K.GIT_WRITE_VERBS, call
        for token in call:
            assert token not in K.GIT_FORBIDDEN_TOKENS and not token.startswith(K.GIT_FORBIDDEN_PREFIXES), call
            assert token not in K.GIT_WRITE_VERBS, call
    assert scripted.cwd == os.path.realpath(str(work)), "the repository is selected by cwd, never by -C"


def test_the_conftest_fake_never_records_a_refusal(observer, gate_repo, denied_log):
    """The fake exits 97 and logs when a write verb reaches it: neither the fixture's log nor its fallback may grow."""
    fallback = Path("/tmp/aidlc-studio-git-denied.log")
    before = fallback.stat().st_size if fallback.is_file() else 0
    observation = observer.observe(gate_repo)
    observer.common_dir(gate_repo)
    observer.dirty_split(gate_repo, [".kiro"])
    observer.recent_commits(gate_repo, [], limit=5)
    observer.diff(gate_repo, "aidlc/spaces/default/intents")
    observer.version()
    assert observation.available is True and observation.branch == "main"
    assert not denied_log.exists() or denied_log.read_text() == ""
    after = fallback.stat().st_size if fallback.is_file() else 0
    assert after == before, "the fake refused an invocation: a write verb reached it"


def test_the_child_environment_is_minimal_and_pins_git_to_a_read(G, K, scripted, clock, work, monkeypatch):
    for key, value in (("AIDLC_DEBUG", "1"), ("KIROCREW_TOKEN", "secret"), ("GIT_DIR", "/somewhere/else/.git")):
        monkeypatch.setenv(key, value)  # none of these may be inherited
    G.GitObserver(scripted.path, clock).common_dir(work)
    env = scripted.env
    # not a key-set equality: the macOS system python3 running the stand-in injects SDKROOT/CPATH of its own
    assert set(env) & {"AIDLC_DEBUG", "KIROCREW_TOKEN", "GIT_DIR", "GIT_WORK_TREE"} == set()
    inherited = {key for key in env if key in K.ENV_KEEP}
    assert inherited <= set(K.ENV_KEEP) and "PATH" in inherited
    # GIT_OPTIONAL_LOCKS=0: status must not be allowed to refresh (write) the index
    pinned = {"NO_COLOR": "1", "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
              "GIT_CONFIG_GLOBAL": os.devnull}
    assert {key: env.get(key) for key in pinned} == pinned
    assert not [key for key in env if key.startswith(K.ENV_FORBIDDEN_PREFIXES)]
    # command-line pins are the only layer above the repo's own .git/config, else core.fsmonitor is a program git runs
    pins = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    assert pins["core.fsmonitor"] == "false"
    assert set(pins) >= {"core.fsmonitor", "core.alternateRefsCommand", "core.pager", "diff.external",
                         "uploadpack.packObjectsHook"}


def test_a_repository_never_gets_to_name_the_program_git_runs_during_a_read(G, real_git, clock, tmp_path):
    """A hostile repo-local ``core.fsmonitor`` (untouched by NOSYSTEM/GLOBAL, unseen by the argv check) never runs."""
    marker, payload = tmp_path / "fsmonitor.marker", tmp_path / "fsmonitor.sh"  # outside the repository
    payload.write_text(f'#!/bin/sh\n: > "{marker}"\nexit 0\n', encoding="utf-8")
    payload.chmod(payload.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    repo = real_repo(tmp_path, real_git, ("core.fsmonitor", str(payload)))
    observer = G.GitObserver(real_git, clock)
    observation = observer.observe(repo)
    observer.dirty_split(repo, ["."])
    observer.diff(repo, "aidlc-state.md")
    observer.recent_commits(repo, [], limit=3)
    observer.prior_version(repo, "aidlc-state.md")
    observer.common_dir(repo)
    assert not marker.exists(), "the repository's own config chose a program and a read verb ran it"
    # the pins must silence the repository without blinding the observation they protect
    assert observation.available is True and observation.reason is None and observation.branch == "main"
    assert observation.dirty is True and observation.dirty_files == 2


def test_a_bypass_variable_in_the_gateway_never_reaches_git(G, E, scripted, clock, work, monkeypatch):
    monkeypatch.setenv("AIDLC_SKIP_ARTIFACT_GUARD", "1")
    obs = G.GitObserver(scripted.path, clock)
    observation = obs.observe(work)
    assert observation.available is False and observation.reason == G.REASON_ERROR
    with pytest.raises(E.StudioError) as excinfo:
        obs._run(work, ["status"], stdout_cap=1024)
    assert excinfo.value.code == "internal_error"
    assert scripted.calls == []


# --- observation ---
def test_observe_reads_branch_head_and_a_clean_tree(observer, gate_repo, clock):
    observation = observer.observe(gate_repo)
    assert observation.available is True and observation.detached is False and observation.dirty is False
    assert (observation.reason, observation.upstream) == (None, None)
    assert (observation.branch, observation.head, observation.dirty_files) == ("main", "0" * 40, 0)
    assert observation.observed_at == clock.iso()
    assert observation.took_ms == 0, "timing must come from the injected clock, not time.monotonic"


def test_observe_counts_every_kind_of_dirty_entry(obs, scripted, work):
    entries = (changed("aidlc/spaces/default/intents/x/aidlc-state.md"), renamed("docs/new name.md", "docs/old.md"),
               '? "docs/caf\\303\\251.md"', "! build/ignored.txt")
    scripted.plan({"status": {"stdout": status_text(entries=entries)},
                   "log": {"stdout": log_line("b" * 40, "2026-09-04T09:00:00+00:00", "state moved") + "\n"}})
    observation = obs.observe(work)
    assert observation.dirty is True and observation.head_subject == "state moved"
    assert observation.dirty_files == 3, "ignored entries are not dirt"


@pytest.mark.parametrize("upstream, ab, expected", [("origin/main", "+3 -7", ("origin/main", 3, 7)),
                                                    (None, None, (None, None, None))])
def test_observe_reads_ahead_and_behind_from_the_status_header_alone(obs, scripted, work, upstream, ab, expected):
    """The header answers without an extra process; no upstream is unknown, not "in sync"."""
    scripted.plan({"status": {"stdout": status_text(upstream=upstream, ab=ab)}})
    observation = obs.observe(work)
    assert (observation.upstream, observation.ahead, observation.behind) == expected
    assert "rev-list" not in scripted.verbs


def test_observe_falls_back_to_rev_list_when_the_header_is_missing(obs, scripted, work):
    """``@{upstream}...HEAD`` prints the BEHIND count first (verified against git 2.50)."""
    scripted.plan({"status": {"stdout": status_text(upstream="origin/main")}, "rev-list": {"stdout": "2636\t0\n"}})
    observation = obs.observe(work)
    assert (observation.behind, observation.ahead) == (2636, 0)
    assert ["rev-list", "--left-right", "--count", "@{upstream}...HEAD"] in scripted.calls


@pytest.mark.parametrize("plan, fallback", [
    ({"status": {"stdout": status_text(head="(detached)")}}, None),
    ({"status": {"stdout": changed("a.md") + "\n"}, "rev-parse": {"stdout": "HEAD\n"}},  # a branchless status
     ["rev-parse", "--abbrev-ref", "HEAD"]),
])
def test_observe_reports_a_detached_head(obs, scripted, work, plan, fallback):
    scripted.plan(plan)
    observation = obs.observe(work)
    assert observation.detached is True and observation.branch is None and observation.available is True
    assert fallback is None or fallback in scripted.calls


def test_observe_reports_an_unborn_head_as_available(obs, scripted, work):
    scripted.plan({"status": {"stdout": status_text(oid="(initial)")}, "log": {"exit": 128}})
    observation = obs.observe(work)
    assert observation.available is True and observation.branch == "main"
    assert (observation.head, observation.head_subject) == (None, None)


def test_observe_degrades_when_git_is_absent(G, clock, work):
    for path in (None, "", str(work / "no-such-git")):
        observation = G.GitObserver(path, clock).observe(work)
        assert observation.available is False and observation.reason == G.REASON_GIT_MISSING
        assert observation.branch is None and observation.dirty_files == 0


@pytest.mark.parametrize("stderr, reason", [("fatal: not a git repository (or any parent up to /)\n",
                                             "REASON_NOT_A_REPO"),
                                            ("fatal: detected dubious ownership\n", "REASON_ERROR")])
def test_observe_degrades_when_git_fails(G, obs, scripted, work, stderr, reason):
    scripted.plan({"status": {"exit": 128, "stderr": stderr}})
    observation = obs.observe(work)
    assert observation.available is False and observation.reason == getattr(G, reason)


def test_observe_degrades_when_the_repository_directory_is_gone(G, observer, tmp_path):
    observation = observer.observe(tmp_path / "moved-away")
    assert observation.available is False
    assert observation.reason == G.REASON_ERROR, "a vanished repo is not a missing git binary"


def test_a_wedged_git_times_out_instead_of_blocking(G, K, scripted, clock, work, monkeypatch):
    monkeypatch.setattr(K, "GIT_TIMEOUT_SECS", 0.25)
    obs = G.GitObserver(scripted.path, clock)
    scripted.plan({"status": {"sleep": 5, "stdout": status_text()}, "log": {"sleep": 5}})
    observation = obs.observe(work)
    assert observation.available is False and observation.reason == G.REASON_TIMEOUT
    result = obs._run(work, ["log", "-n", "1"], stdout_cap=1024)  # a spawn reports the timeout without raising
    assert result.timed_out is True and result.exit_code is None and result.ok is False


def test_a_spawn_redacts_credentials_out_of_stderr(obs, scripted, work):
    scripted.plan({"log": {"exit": 128, "stderr": "fatal: token=0123456789abcdef failed\n"}})
    result = obs._run(work, ["log"], stdout_cap=1024)
    assert "0123456789abcdef" not in result.stderr and result.exit_code == 128


# --- prior version and diff ---
def test_prior_version_returns_the_committed_bytes(obs, scripted, work):
    scripted.plan({"cat-file": {"stdout": "13\n"}, "show": {"stdout": "# Requirements\n"}})
    assert obs.prior_version(work, "inception/requirements.md") == b"# Requirements\n"
    assert ["cat-file", "-s", "HEAD:inception/requirements.md"] in scripted.calls
    assert ["show", "--no-textconv", "HEAD:inception/requirements.md"] in scripted.calls


def test_prior_version_refuses_an_oversize_blob_before_reading_it(K, obs, scripted, work):
    scripted.plan({"cat-file": {"stdout": f"{K.MAX_ARTIFACT_RENDER_BYTES + 1}\n"}})
    assert obs.prior_version(work, "big.md") is None
    assert "show" not in scripted.verbs, "the size probe exists so the blob is never materialised"


def test_prior_version_refuses_an_oversize_blob_when_the_size_probe_is_silent(K, obs, scripted, work):
    scripted.plan({"cat-file": {"exit": 1},
                   "show": {"stdout": "x" * 1024, "repeat": K.MAX_ARTIFACT_RENDER_BYTES // 1024 + 1}})
    assert obs.prior_version(work, "big.md") is None, "half a file would make the diff view lie"
    assert "show" in scripted.verbs


def test_prior_version_is_none_for_an_untracked_file(obs, scripted, work):
    missing = {"exit": 128, "stderr": "fatal: path 'new.md' does not exist in 'HEAD'\n"}
    scripted.plan({"cat-file": missing, "show": missing})
    assert obs.prior_version(work, "new.md") is None


def test_prior_version_and_diff_refuse_a_path_that_leaves_the_repository(E, obs, scripted, work):
    for bad in ("../../etc/passwd", "/etc/passwd", "a/../../outside.md"):
        with pytest.raises(E.StudioError) as excinfo:
            obs.prior_version(work, bad)
        assert excinfo.value.code == "bad_path", bad
        with pytest.raises(E.StudioError):
            obs.diff(work, bad)
    with pytest.raises(E.StudioError):
        obs.prior_version(work, "")  # the repository root is not an artifact
    assert scripted.calls == []


def test_prior_version_follows_no_symlink_out_of_the_repository(E, obs, scripted, work, tmp_path):
    (tmp_path / "outside.md").write_text("secret\n")
    (work / "link.md").symlink_to(tmp_path / "outside.md")
    with pytest.raises(E.StudioError) as excinfo:
        obs.prior_version(work, "link.md")
    assert excinfo.value.code == "bad_path" and scripted.calls == []


def test_diff_is_truncated_visibly_rather_than_returned_whole(K, obs, scripted, work):
    scripted.plan({"diff": {"stdout": "+" * 1024, "repeat": K.MAX_SUBPROCESS_STDOUT // 1024 + 2}})
    patch = obs.diff(work, "a.md")
    assert patch is not None and "truncated" in patch.splitlines()[-1]
    assert len(patch) < K.MAX_SUBPROCESS_STDOUT + 200


def test_diff_separates_no_change_from_no_observation(G, obs, scripted, clock, work):
    scripted.plan({"diff": {"stdout": ""}})
    assert obs.diff(work, "a.md") == "", "an empty patch is information: the file matches the index"
    assert G.GitObserver(None, clock).diff(work, "a.md") is None
    scripted.plan({"diff": {"exit": 128, "stderr": "fatal: not a git repository\n"}})
    assert obs.diff(work, "a.md") is None


def test_diff_disables_repository_supplied_converters(obs, scripted, work):
    """A repo-local diff driver would otherwise run a binary of the repository's choosing on a read."""
    scripted.plan({"diff": {"stdout": ""}})
    obs.diff(work, "a.md")
    call = next(c for c in scripted.calls if c[0] == "diff")
    assert "--no-ext-diff" in call and "--no-textconv" in call and "--no-color" in call
    assert call[-2:] == ["--", "a.md"]


# --- commits ---
def test_recent_commits_parses_rows_and_normalises_the_timestamp(obs, scripted, work):
    scripted.plan({"log": {"stdout": log_line("a" * 40, "2026-09-04T12:49:31+02:00", "feat: add stage table") + "\n"
                           + log_line("b" * 40, "2026-09-03T23:00:00Z", "chore: bump") + "\n"}})
    commits = obs.recent_commits(work, ["aidlc/spaces/default"], limit=5)
    assert [c.sha for c in commits] == ["a" * 40, "b" * 40]
    assert commits[0].at == "2026-09-04T10:49:31Z", "commit times sort against audit rows"
    assert commits[0].subject == "feat: add stage table"
    assert commits[0].to_json() == {"sha": "a" * 40, "at": "2026-09-04T10:49:31Z", "subject": "feat: add stage table"}


def test_recent_commits_bounds_the_walk_and_separates_paths_only_when_given(G, obs, scripted, work):
    scripted.plan({"log": {"stdout": ""}})
    obs.recent_commits(work, ["a.md", "docs"], limit=10_000)
    obs.recent_commits(work, [])
    bounded, bare = [call for call in scripted.calls if call[0] == "log"]
    assert bounded[bounded.index("-n") + 1] == str(G.MAX_LOG_LIMIT) and "--no-decorate" in bounded
    assert bounded[bounded.index("--") + 1:] == ["a.md", "docs"]
    assert "--" not in bare


def test_recent_commits_is_empty_when_git_fails(G, obs, scripted, clock, work):
    scripted.plan({"log": {"exit": 128, "stderr": "fatal: your current branch has no commits\n"}})
    assert obs.recent_commits(work, []) == []
    assert G.GitObserver(None, clock).recent_commits(work, []) == []


# --- dirty split and common dir ---
def test_dirty_split_keeps_receipt_owned_drift_apart_from_the_users_work(obs, scripted, work):
    entries = (changed(".kiro/tools/aidlc-utility.ts", "M."), "? .kiro/agents/extra.json", changed("src/app.py"),
               changed("README.md"))
    scripted.plan({"status": {"stdout": status_text(entries=entries)}})
    split = obs.dirty_split(work, [".kiro", "AGENTS.md"])
    assert split["owned_dirty"] == [".kiro/agents/extra.json", ".kiro/tools/aidlc-utility.ts"]
    assert split["unrelated_dirty"] == 2, "the user's own work in progress never blocks an upgrade"
    assert split["owned_changes"] == [{"relpath": ".kiro/agents/extra.json", "status": "?"},
                                      {"relpath": ".kiro/tools/aidlc-utility.ts", "status": "M."}]
    assert split["available"] is True and split["reason"] is None


def test_an_untracked_install_directory_is_studios_output_not_the_users_work(obs, scripted, work):
    """Git lists a fresh install's untracked `.kiro/` as one entry; it holds receipt-owned files."""
    entries = ("? .kiro/", "? aidlc/", "? notes/", "? AGENTS.md")
    scripted.plan({"status": {"stdout": status_text(entries=entries)}})
    split = obs.dirty_split(work, [".kiro/agents/aidlc.json", "aidlc/spaces/default/memory/org.md", "AGENTS.md"])
    assert split["owned_untracked_dirs"] == [".kiro/", "aidlc/"]
    assert split["owned_dirty"] == ["AGENTS.md"] and split["unrelated_dirty"] == 1


def test_dirty_split_does_not_claim_a_clean_tree_when_git_failed(G, obs, scripted, work):
    scripted.plan({"status": {"exit": 128, "stderr": "fatal: not a git repository\n"}})
    assert obs.dirty_split(work, [".kiro"]) == {"owned_dirty": [], "unrelated_dirty": 0, "owned_changes": [],
                                                "owned_untracked_dirs": [], "available": False,
                                                "reason": G.REASON_NOT_A_REPO, "truncated": False}


def test_dirty_split_and_common_dir_over_the_real_repository(observer, gate_repo):
    split = observer.dirty_split(gate_repo, ["aidlc", ".kiro"])
    assert split["available"] is True and split["owned_dirty"] == [] and split["unrelated_dirty"] == 0
    assert observer.common_dir(gate_repo) == os.path.join(os.path.realpath(str(gate_repo)), ".git")


def test_common_dir_is_absolute_even_when_git_answers_relatively(obs, scripted, work):
    scripted.plan({"rev-parse": {"stdout": ".git\n"}})
    assert obs.common_dir(work) == os.path.join(os.path.realpath(str(work)), ".git")


def test_common_dir_is_none_outside_a_repository(G, obs, scripted, clock, work):
    scripted.plan({"rev-parse": {"exit": 128, "stderr": "fatal: not a git repository\n"}})
    assert obs.common_dir(work) is None
    scripted.plan({"rev-parse": {"stdout": "\n"}})
    assert obs.common_dir(work) is None
    assert G.GitObserver(None, clock).common_dir(work) is None


def test_version_reports_the_binary_or_nothing(G, obs, scripted, clock):
    scripted.plan({"--version": {"stdout": "git version 2.50.1 (Apple Git-155)\n"}})
    assert obs.version() == "git version 2.50.1 (Apple Git-155)"
    assert G.GitObserver(None, clock).version() is None
    assert G.GitObserver("", clock).version() is None, "an empty path is 'no git', not a binary named ''"


# --- shape of the values other modules consume ---
def test_observation_json_matches_the_wire_contract(observer, gate_repo):
    payload = observer.observe(gate_repo).to_json()
    assert set(payload) == {"available", "reason", "branch", "detached", "head", "head_subject", "dirty", "dirty_files",
                            "ahead", "behind", "upstream", "observed_at", "took_ms"}
    assert json.loads(json.dumps(payload)) == payload


def test_the_value_objects_are_frozen_and_slotted(G):
    for value, field in ((G.GitObservation.unavailable(G.REASON_ERROR, observed_at=TS, took_ms=0), "reason"),
                         (G.GitCommit("a" * 40, TS, "s"), "sha")):
        assert type(value).__slots__ is not None and not hasattr(value, "__dict__"), type(value)
        with pytest.raises(Exception):
            setattr(value, field, "mutated")


def test_unavailable_observations_use_only_the_documented_reasons(G):
    reasons = (G.REASON_GIT_MISSING, G.REASON_NOT_A_REPO, G.REASON_TIMEOUT, G.REASON_ERROR)
    assert reasons == ("git_missing", "not_a_repo", "timeout", "error")
    blank = G.GitObservation.unavailable(G.REASON_TIMEOUT, observed_at=TS, took_ms=7)
    assert (blank.to_json()["reason"], blank.to_json()["took_ms"]) == ("timeout", 7) and blank.available is False


def test_observing_a_repository_never_changes_a_byte_of_it(observer, gate_repo):
    """The whole product rests on this: Studio reads git, it does not run git against the tree."""
    before = tree_digest(gate_repo)
    observer.observe(gate_repo)
    observer.common_dir(gate_repo)
    observer.dirty_split(gate_repo, ["aidlc", ".kiro"])
    observer.recent_commits(gate_repo, ["aidlc/spaces/default/intents"], limit=5)
    observer.prior_version(gate_repo, GATE_STATE)
    observer.diff(gate_repo, GATE_STATE)
    assert tree_digest(gate_repo) == before


def test_a_missing_git_hides_nothing_but_git(G, clock, gate_repo):
    """FR-GIT-005: git being unavailable is a degraded capability, not a failed repository read."""
    blind = G.GitObserver(None, clock)
    observation = blind.observe(gate_repo)
    assert observation.available is False and observation.reason == G.REASON_GIT_MISSING
    assert observation.to_json()["branch"] is None
    assert blind.dirty_split(gate_repo, ["aidlc"])["available"] is False
    assert blind.recent_commits(gate_repo, []) == []
    assert blind.prior_version(gate_repo, GATE_STATE) is None
    assert "Current Stage" in (gate_repo / GATE_STATE).read_text("utf-8"), "the workflow state is still there to read"
