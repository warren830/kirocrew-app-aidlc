"""The code a stage wrote, compared with HEAD (``source_changes``), against a real git repository.

The statuses that do not show a diff are claims too: a secret file must never leak, a path outside the repository
must never be read, and none of them may read as "unchanged".
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

MANIFEST = "aidlc/spaces/default/intents/i-1/construction/code-generation/source-manifest.json"


@pytest.fixture
def git_bin() -> str:
    path = shutil.which("git")
    if path is None:  # pragma: no cover - depends on the machine
        pytest.skip("needs a real git to compare with HEAD")
    return path


def _git(repo: Path, git: str, *args: str, date: str | None = None) -> None:
    env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(repo.parent), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "lab", "GIT_AUTHOR_EMAIL": "lab@example.invalid",
           "GIT_COMMITTER_NAME": "lab", "GIT_COMMITTER_EMAIL": "lab@example.invalid",
           **({"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date} if date else {})}
    subprocess.run([git, *args], cwd=repo, env=env, check=True, capture_output=True)


def _repo(tmp_path: Path, git: str, *, commit: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "old.py").write_text("gone = True\n")
    (repo / "same.py").write_text("x = 1\n")
    (repo / "add").write_text("spelled like a git write verb\n")
    _git(repo, git, "-c", "init.defaultBranch=main", "init", "-q", ".")
    if commit:
        _git(repo, git, "add", "-A")
        _git(repo, git, "commit", "-qm", "base")
    return repo


def _manifest(repo: Path, paths: list[str]) -> None:
    target = repo / MANIFEST
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"version": 1, "stage": "code-generation", "unit": None,
                                  "writes": [{"path": path} for path in paths]}))


@pytest.fixture
def read(studio, git_bin, clock):
    def run(repo: Path, *, git: str | None = git_bin, base: str | None = None, at: str | None = None):
        return studio.source_changes.read_changes(studio.aidlc_reader.AidlcReader(clock),
                                                  studio.git_observer.GitObserver(git, clock), repo, MANIFEST, base, at)
    return run


def _by_path(out: dict) -> dict[str, dict]:
    return {row["path"]: row for row in out["files"]}


def test_each_written_file_is_diffed_against_head(read, tmp_path, git_bin):
    repo = _repo(tmp_path, git_bin)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n")
    (repo / "test_calc.py").write_text("import unittest\n")
    (repo / "old.py").unlink()
    (repo / "add").write_text("still readable\n")
    _manifest(repo, ["calc.py", "test_calc.py", "old.py", "same.py", "add", "calc.py"])
    out = read(repo)
    assert out["available"] is True and out["manifest_readable"] is True and out["truncated"] is False
    rows = _by_path(out)
    assert list(rows) == ["calc.py", "test_calc.py", "old.py", "same.py", "add"], "listed once, in manifest order"
    calc = rows["calc.py"]
    assert (calc["status"], calc["additions"], calc["deletions"]) == ("modified", 4, 0)
    assert calc["diff"].startswith("--- a/calc.py\n+++ b/calc.py\n@@") and "+def subtract(a, b):\n" in calc["diff"]
    assert rows["test_calc.py"]["status"] == "added"
    assert rows["test_calc.py"]["diff"].startswith("--- /dev/null\n+++ b/test_calc.py\n")
    assert (rows["old.py"]["status"], rows["old.py"]["deletions"]) == ("deleted", 1)
    assert rows["old.py"]["diff"].startswith("--- a/old.py\n+++ /dev/null\n")
    assert rows["same.py"]["status"] == "unchanged" and rows["same.py"]["diff"] is None
    assert rows["add"]["status"] == "modified", "a path is never a bare git token, so no write-verb refusal"


def test_a_long_file_is_counted_as_git_counts_it(read, tmp_path, git_bin):
    """From 200 lines on, difflib's default junk heuristic matched around blank lines and miscounted by one."""
    def case(i: int) -> str:
        return f"    def test_case_{i}(self):\n        self.assertEqual(f({i}), {i})\n\n"
    head = "import unittest\n\n\nclass T(unittest.TestCase):\n"
    repo = _repo(tmp_path, git_bin)
    (repo / "test_f.py").write_text('"""Tests for f.\n\nOne per row.\n"""\n' + head + "".join(map(case, range(6))))
    _git(repo, git_bin, "add", "-A")
    _git(repo, git_bin, "commit", "-qm", "tests")
    (repo / "test_f.py").write_text('"""Tests for f and g.\n\nRows and subtests.\n"""\n' + head
                                    + "".join(map(case, range(6))) + "\nclass U(unittest.TestCase):\n"
                                    + "".join(map(case, range(6, 70))))
    _manifest(repo, ["test_f.py"])
    row = _by_path(read(repo))["test_f.py"]
    numstat = subprocess.run([git_bin, "diff", "--numstat", "--", "test_f.py"], cwd=repo, check=True,
                             capture_output=True, text=True).stdout.split()
    assert (row["additions"], row["deletions"]) == (int(numstat[0]), int(numstat[1])) == (196, 2)


def test_a_closed_card_compares_with_the_head_it_captured(read, tmp_path, git_bin):
    """Once the approved code is committed, today's HEAD says "unchanged"; the HEAD the card captured does not."""
    repo = _repo(tmp_path, git_bin)
    captured = subprocess.run([git_bin, "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True,
                              text=True).stdout.strip()
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n")
    _manifest(repo, ["calc.py"])
    _git(repo, git_bin, "add", "-A")
    _git(repo, git_bin, "commit", "-qm", "the approved change")
    now = read(repo)
    assert now["base"] is None and _by_path(now)["calc.py"]["status"] == "unchanged"
    then = read(repo, base=captured)
    calc = _by_path(then)["calc.py"]
    assert then["base"] == captured and (calc["status"], calc["additions"], calc["deletions"]) == ("modified", 4, 0)
    # a commit the repository does not have is not a comparison: HEAD is used, and the answer says so
    gone = read(repo, base="0" * 40)
    assert gone["base"] is None and _by_path(gone)["calc.py"]["status"] == "unchanged"


def test_a_closed_card_compares_with_the_head_of_the_moment_it_was_decided(read, tmp_path, git_bin):
    """A card stores no git reading, but its decision time names the commit HEAD was then."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    _git(repo, git_bin, "-c", "init.defaultBranch=main", "init", "-q", ".")
    _git(repo, git_bin, "add", "-A")
    _git(repo, git_bin, "commit", "-qm", "base", date="2026-10-13T13:16:00Z")
    first = subprocess.run([git_bin, "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True,
                           text=True).stdout.strip()
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n")
    _manifest(repo, ["calc.py"])
    _git(repo, git_bin, "add", "-A")
    _git(repo, git_bin, "commit", "-qm", "the approved change", date="2026-10-13T15:08:00Z")
    decided = read(repo, at="2026-10-13T14:38:13Z")
    calc = _by_path(decided)["calc.py"]
    assert decided["base"] == first and (calc["status"], calc["additions"]) == ("modified", 4)
    # before the first commit there was nothing to compare with, and HEAD is used
    assert read(repo, at="2026-10-01T00:00:00Z")["base"] is None



def _head(repo: Path, git: str) -> str:
    return subprocess.run([git, "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def test_a_closed_card_stops_at_the_commit_that_took_its_change_in(read, tmp_path, git_bin):
    """A later intent's work on the same file is not this gate's: each file is read as the first commit after the
    decision left it, and only a file no commit has taken in yet is read from the working tree."""
    repo = _repo(tmp_path, git_bin)
    decided_at = _head(repo, git_bin)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n")
    _manifest(repo, ["calc.py", "same.py"])
    _git(repo, git_bin, "add", "-A")
    _git(repo, git_bin, "commit", "-qm", "the approved change")
    took_in = _head(repo, git_bin)
    (repo / "calc.py").write_text((repo / "calc.py").read_text() + "\n\ndef multiply(a, b):\n    return a * b\n")
    _git(repo, git_bin, "commit", "-qam", "a later intent")
    (repo / "calc.py").write_text((repo / "calc.py").read_text() + "\n# not committed yet\n")
    out = read(repo, base=decided_at)
    calc = _by_path(out)["calc.py"]
    assert (calc["status"], calc["additions"], calc["deletions"], calc["until"]) == ("modified", 4, 0, took_in)
    assert "multiply" not in calc["diff"]
    # same.py was never changed after the decision: read from the working tree, like an uncommitted change
    assert (_by_path(out)["same.py"]["status"], _by_path(out)["same.py"]["until"]) == ("unchanged", None)
    assert out["until"] is None, "the files were read from different places"
    _manifest(repo, ["calc.py"])
    assert read(repo, base=decided_at)["until"] == took_in
    # a live card (no decision-time base) still reads today's files against HEAD
    assert _by_path(read(repo))["calc.py"]["until"] is None

def test_what_is_not_shown_says_why(read, tmp_path, git_bin):
    repo = _repo(tmp_path, git_bin)
    (repo / ".env").write_text("API_KEY=do-not-show\n")
    (repo / "logo.bin").write_bytes(b"\x89PNG\x00\x01")
    (tmp_path / "escape.py").write_text("outside = True\n")
    _manifest(repo, [".env", "logo.bin", "../escape.py", "never-written.py"])
    rows = _by_path(read(repo))
    assert rows[".env"]["status"] == "secret" and rows[".env"]["diff"] is None
    assert (rows["logo.bin"]["status"], rows["logo.bin"]["change"]) == ("binary", "added")
    assert rows["../escape.py"]["status"] == "outside" and rows["../escape.py"]["diff"] is None
    assert rows["never-written.py"]["status"] == "unknown", "listed yet neither on disk nor at HEAD"
    assert "do-not-show" not in json.dumps(rows)


def test_without_a_commit_every_written_file_is_new(read, tmp_path, git_bin):
    repo = _repo(tmp_path, git_bin, commit=False)
    _manifest(repo, ["calc.py"])
    assert _by_path(read(repo))["calc.py"]["status"] == "added"


def test_without_git_nothing_is_claimed_about_a_file(read, tmp_path, git_bin):
    repo = _repo(tmp_path, git_bin)
    _manifest(repo, ["calc.py"])
    out = read(repo, git=None)
    assert out["available"] is False and out["reason"]
    assert [(row["path"], row["status"]) for row in out["files"]] == [("calc.py", "unknown")]


def test_an_unreadable_manifest_lists_nothing(read, tmp_path, git_bin):
    repo = _repo(tmp_path, git_bin)
    (repo / MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    (repo / MANIFEST).write_text("{not json")
    out = read(repo)
    assert out["files"] == [] and out["manifest_readable"] is False


def test_a_long_diff_is_cut_with_a_marker(read, studio, tmp_path, git_bin, monkeypatch):
    repo = _repo(tmp_path, git_bin)
    (repo / "big.py").write_text("".join(f"line_{n} = {n}\n" for n in range(400)))
    _manifest(repo, ["big.py"])
    monkeypatch.setattr(studio.constants, "MAX_SOURCE_CHANGE_DIFF_BYTES", 512)
    row = _by_path(read(repo))["big.py"]
    assert row["truncated"] is True and len(row["diff"]) <= 512


TRACEABILITY = "aidlc/spaces/default/intents/i-1/construction/code-generation/traceability.json"


def _traceability(repo: Path, rows: list) -> None:
    target = repo / TRACEABILITY
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"stage": "code-generation", "upstream_ids": [], "coverage": rows}))


def test_zero_unit_code_is_read_from_its_traceability_targets(studio, clock, tmp_path, git_bin):
    """2.11 writes no source manifest without a Unit; its traceability's OK targets are the files it wrote."""
    repo = _repo(tmp_path, git_bin)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b + 0\n")
    (repo / "test_calc.py").write_text("import unittest\n")
    _traceability(repo, [
        {"id": "FR1", "status": "OK", "target": "calc.py"}, {"id": "FR2", "status": "OK", "target": "calc.py"},
        {"id": "NFR1", "status": "OK", "target": "test_calc.py"}, {"id": "FR3", "status": "GAP", "target": "x.py"},
        {"id": "FR4", "status": "OK", "target": "aidlc/spaces/default/intents/i-1/inception/requirements.md"},
        {"id": "FR5", "status": "OK", "target": "/etc/hosts"}, "not a row"])
    reader = studio.aidlc_reader.AidlcReader(clock)
    out = studio.source_changes.read_changes(reader, studio.git_observer.GitObserver(git_bin, clock), repo,
                                             TRACEABILITY)
    assert out["manifest_readable"] is True
    assert [(row["path"], row["status"]) for row in out["files"]] == [("calc.py", "modified"),
                                                                      ("test_calc.py", "added")]
    # the Git panel names them as this intent's code too
    assert studio.source_changes.intent_written_paths(reader, repo) == {"calc.py": "i-1", "test_calc.py": "i-1"}
