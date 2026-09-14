"""Tests for ``backend/studio/repo_registry.py``.

The properties here are the ones every later module trusts: that two spellings of one directory are
one repository, that a refused path leaves nothing behind, that unregistering never touches the tree,
that a preflight of a stranger's directory executes nothing, and that install status comes from the
bytes on disk rather than from asking the engine.

Two deliberate choices about doubles:

* ``GitObserver`` is not written yet and is not the module under test, so a recording double stands in
  for it. It answers ``common_dir`` from the repository's real ``.git`` directory, so the identity
  fallback is exercised against a real path rather than a constant, and it records every call so a
  test can prove the registry asked git nothing.
* ``Storage`` is the real thing on a real SQLite file. The refusal tests assert against the table, not
  against a spy, because "nothing was stored" is a claim about the database.

Engine trees come from the harvested fixtures and the bundled payload through ``repo_builder``; every
version number asserted here is read from ``payload/manifest.json`` or from the fixture's own
``aidlc-version.ts``, never typed as a literal (C21).
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import fixtures as F

TS = "2026-09-04T10:00:00Z"


# --------------------------------------------------------------------------- #
# doubles and fixtures
# --------------------------------------------------------------------------- #


class RecordingGit:
    """The read-only slice of ``GitObserver`` the registry is allowed to use (§1.5)."""

    def __init__(self, *, available: bool = True, reason: str | None = None,
                 branch: str = "main", dirty: bool = False, common_dir: str | None = "AUTO") -> None:
        self.available = available
        self.reason = reason
        self.branch = branch
        self.dirty = dirty
        self._common_dir = common_dir
        self.calls: list[tuple[str, str]] = []

    def common_dir(self, repo: Path) -> str | None:
        self.calls.append(("common_dir", str(repo)))
        if self._common_dir == "AUTO":
            candidate = Path(repo) / ".git"
            return str(candidate) if candidate.exists() else None
        return self._common_dir

    def observe(self, repo: Path) -> SimpleNamespace:
        self.calls.append(("observe", str(repo)))
        return SimpleNamespace(
            available=self.available,
            reason=self.reason,
            branch=self.branch if self.available else None,
            detached=False,
            head=None,
            head_subject=None,
            dirty=self.dirty,
            dirty_files=1 if self.dirty else 0,
            ahead=None,
            behind=None,
            upstream=None,
            observed_at=TS,
            took_ms=1,
        )


@pytest.fixture
def R(studio):
    module = studio.repo_registry
    assert module is not None, getattr(studio, "repo_registry__error", "repo_registry.py did not import")
    return module


@pytest.fixture
def K(studio):
    return studio.constants


@pytest.fixture
def store(studio, fake_ctx, clock, ids):
    st = studio.storage.Storage(Path(fake_ctx.data_dir) / studio.constants.DB_FILENAME,
                                clock=clock, ids=ids)
    st.open()
    yield st
    st.close()


@pytest.fixture
def git() -> RecordingGit:
    return RecordingGit()


@pytest.fixture
def bun_calls() -> list[str]:
    return []


@pytest.fixture
def registry(R, store, git, clock, ids, bun_calls):
    def probe(path: str) -> str:
        bun_calls.append(path)
        return "1.1.42"

    return R.RepoRegistry(store, git, clock, ids, bun_version=probe)


@pytest.fixture
def payload_manifest(app_root) -> dict:
    return json.loads((app_root / "payload" / "manifest.json").read_text("utf-8"))


def plain_repo(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / "work" / name
    root.mkdir(parents=True)
    return root


def engine_repo(repo_builder, source: str) -> Path:
    return repo_builder.with_engine(source).build()


def foreign_engine_repo(repo_builder, source: str = "payload", harness: str = ".claude") -> Path:
    """An engine tree under a harness Studio does not manage, and nothing under ``.kiro``.

    The live shape this exists for is a repository someone drives from Claude Code: AI-DLC installed at
    ``.claude``, a real ``aidlc/`` workspace, no Kiro harness at all.
    """
    root = repo_builder.with_engine(source).build()
    (root / ".kiro").rename(root / harness)
    return root


def tree_digest(root: Path) -> dict[str, str]:
    """Every regular file under ``root`` as ``relpath -> sha256``, symlinks included by name."""
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


def case_insensitive(base: Path) -> bool:
    probe = base / "CaseProbe"
    probe.mkdir()
    return (base / "caseprobe").is_dir()


def receipt_for(root: Path, rels: list[str], *, receipt_id: str = "rc_0000000000000001",
                engine_version: str = "2.6.2", extra_files: list[dict] | None = None) -> dict:
    """A receipt row shaped like ``install_receipts`` (``detect_install`` accepts either shape)."""
    files = [
        {
            "path": rel,
            "ownership": "framework",
            "kind": "file",
            "sha256": hashlib.sha256((root / rel).read_bytes()).hexdigest(),
            "canonical_digest": None,
            "fragment_key": None,
            "adopted": False,
        }
        for rel in rels
    ]
    files.extend(extra_files or [])
    return {
        "receipt_id": receipt_id,
        "repo_id": "r_000000000001",
        "resolved_repo_identity": None,
        "studio_version": "1.0.0",
        "engine_version": engine_version,
        "payload_digest": "0" * 64,
        "committed_at": TS,
        "prior_receipt_id": None,
        "transaction_id": "tx_0000000000000001",
        "files_json": files,
        "status": "current",
        "files": files,
    }


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #


def test_identity_is_dev_ino_when_the_filesystem_supplies_it(registry, tmp_path, git):
    root = plain_repo(tmp_path)
    identity = registry.resolve_identity(str(root))

    st = os.stat(root)
    assert identity.identity_str == f"{st.st_dev}:{st.st_ino}"
    assert (identity.st_dev, identity.st_ino) == (st.st_dev, st.st_ino)
    assert identity.provable is True
    assert identity.canonical_path == str(root)
    assert identity.git_common_dir is None  # no .git in this tree


def test_identity_records_the_git_common_dir_when_there_is_one(registry, repo_builder, git):
    root = repo_builder.with_git().build()
    identity = registry.resolve_identity(str(root))

    assert identity.git_common_dir == str(root / ".git")
    assert ("common_dir", str(root)) in git.calls


def test_identity_falls_back_to_the_path_digest_without_inodes(registry, R, repo_builder,
                                                               monkeypatch, studio):
    """A filesystem that reports ``st_ino == 0`` must not collapse every repo into one identity."""
    root = repo_builder.with_git().build()
    real_stat = os.stat

    def zero_ino(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        if str(path) == str(root):
            fields = list(st)
            fields[1] = 0
            return os.stat_result(tuple(fields))
        return st

    monkeypatch.setattr(os, "stat", zero_ino)
    identity = registry.resolve_identity(str(root))

    expected = studio.security.sha256_text(str(root) + "\0" + str(root / ".git"))
    assert identity.identity_str == expected
    assert (identity.st_dev, identity.st_ino) == (None, None)
    assert identity.provable is True  # the git common dir is the deterministic fallback


def test_identity_is_unprovable_without_inodes_or_git(registry, tmp_path, git, monkeypatch, studio):
    root = plain_repo(tmp_path)
    real_stat = os.stat

    def zero_ino(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        if str(path) == str(root):
            fields = list(st)
            fields[1] = 0
            return os.stat_result(tuple(fields))
        return st

    monkeypatch.setattr(os, "stat", zero_ino)
    identity = registry.resolve_identity(str(root))

    assert identity.provable is False
    assert identity.identity_str == studio.security.sha256_text(str(root) + "\0" + "")

    record = registry.add(str(root), None)
    assert record.availability == "identity_unprovable"
    assert record.resolved_identity is None  # no lease may ever be keyed on an unproven identity


def test_two_case_spellings_of_one_directory_are_one_identity(registry, tmp_path, store):
    base = tmp_path / "work"
    base.mkdir(parents=True)
    if not case_insensitive(base):
        pytest.skip("filesystem is case-sensitive; the alias cannot exist")
    root = base / "Repo"
    root.mkdir()

    first = registry.add(str(root), "typed case")
    alias = base / "repo"
    assert registry.resolve_identity(str(alias)).identity_str == first.resolved_identity

    with pytest.raises(Exception) as exc:
        registry.add(str(alias), "lower case")
    assert exc.value.code == "duplicate_identity"
    assert exc.value.details["repo_id"] == first.repo_id
    assert len(store.select("repos")) == 1
    assert store.get("repos", first.repo_id)["canonical_path"] == str(root)  # typed case kept (A13)


# --------------------------------------------------------------------------- #
# add: refusals leave nothing behind
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", ["", "   ", "relative/path", "repo"])
def test_add_refuses_a_relative_or_empty_path(registry, store, bad):
    with pytest.raises(Exception) as exc:
        registry.add(bad, None)
    assert exc.value.code == "bad_path"
    assert store.select("repos") == []


def test_add_refuses_a_file_and_a_missing_directory(registry, store, tmp_path):
    file_path = tmp_path / "a-file"
    file_path.write_text("x")

    for candidate in (str(file_path), str(tmp_path / "nope")):
        with pytest.raises(Exception) as exc:
            registry.add(candidate, None)
        assert exc.value.code == "bad_path"
    assert store.select("repos") == []


def test_add_refuses_a_sensitive_path(registry, store, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    with pytest.raises(Exception) as exc:
        registry.add(str(home / ".ssh"), None)
    assert exc.value.code == "sensitive_path"
    assert store.select("repos") == []


def test_add_refuses_the_same_path_twice(registry, store, tmp_path):
    root = plain_repo(tmp_path)
    first = registry.add(str(root), None)

    with pytest.raises(Exception) as exc:
        registry.add(str(root), None)
    assert exc.value.code == "duplicate_identity"
    assert exc.value.details["repo_id"] == first.repo_id
    assert len(store.select("repos")) == 1


def test_add_refuses_beyond_the_registry_cap(registry, store, tmp_path, monkeypatch, K):
    monkeypatch.setattr(K, "MAX_REPOS", 2)
    for index in range(2):
        registry.add(str(plain_repo(tmp_path, f"repo-{index}")), None)

    with pytest.raises(Exception) as exc:
        registry.add(str(plain_repo(tmp_path, "one-too-many")), None)
    assert exc.value.code == "too_many_repos"
    assert exc.value.details["max"] == 2
    assert len(store.select("repos")) == 2


def test_add_stores_the_row_and_defaults_the_label(registry, store, repo_builder, clock, K):
    root = engine_repo(repo_builder, "devdelta")
    record = registry.add(str(root), None)

    row = store.get("repos", record.repo_id)
    assert record.repo_id.startswith("r_")
    assert record.label == root.name
    assert row["canonical_path"] == str(root)
    assert row["availability"] == "available"
    assert row["added_at"] == clock.iso()
    assert row["install_status"] == "installed"
    assert row["engine_dir"] == K.STUDIO_HARNESS_DIR
    assert record.to_json()["installed_engine_version"] == "2.6.2"


def test_add_truncates_an_overlong_label(registry, tmp_path, K):
    record = registry.add(str(plain_repo(tmp_path)), "L" * (K.MAX_LABEL_CHARS + 50))
    assert len(record.label) == K.MAX_LABEL_CHARS


# --------------------------------------------------------------------------- #
# remove
# --------------------------------------------------------------------------- #


def test_remove_unregisters_only_and_never_touches_the_tree(registry, store, repo_builder):
    root = (
        repo_builder.with_engine("devdelta")
        .with_workspace()
        .with_intent("260904-demo", state=F.real_state(), audit=F.gate_open_audit("requirements-analysis"))
        .with_git()
        .build()
    )
    record = registry.add(str(root), None)
    before = tree_digest(root)

    registry.remove(record.repo_id)

    assert tree_digest(root) == before
    assert store.select("repos") == []
    with pytest.raises(Exception) as exc:
        registry.get(record.repo_id)
    assert exc.value.code == "repo_not_found"


def test_remove_of_an_unknown_repo_raises(registry):
    with pytest.raises(Exception) as exc:
        registry.remove("r_000000000099")
    assert exc.value.code == "repo_not_found"


def test_remove_cascades_bindings_and_actions(registry, store, tmp_path):
    record = registry.add(str(plain_repo(tmp_path)), None)
    store.insert("intent_bindings", {
        "binding_key": f"{record.repo_id}:default:260904-demo",
        "repo_id": record.repo_id, "space": "default", "intent_dir": "260904-demo",
        "created_at": TS, "updated_at": TS,
    })

    registry.remove(record.repo_id)
    assert store.select("intent_bindings") == []


# --------------------------------------------------------------------------- #
# availability
# --------------------------------------------------------------------------- #


def test_availability_is_available_for_a_healthy_repo(registry, tmp_path):
    record = registry.add(str(plain_repo(tmp_path)), None)
    assert registry.check_availability(record) == ("available", None)


def test_availability_is_unavailable_when_the_path_is_gone(registry, tmp_path):
    root = plain_repo(tmp_path)
    record = registry.add(str(root), None)
    root.rename(root.parent / "moved-away")

    assert registry.check_availability(record) == ("unavailable", "path_missing")


def test_availability_is_moved_when_the_identity_turns_up_at_another_row(registry, store, tmp_path):
    root = plain_repo(tmp_path)
    record = registry.add(str(root), None)
    new_home = root.parent / "renamed"
    root.rename(new_home)
    # A migrated/imported row (FR-MIG-003) pointing at the same directory under its new name.
    store.insert("repos", {
        "id": "r_000000000042", "resolved_identity": None, "canonical_path": str(new_home),
        "label": "imported", "added_at": TS, "platform": "darwin",
        "availability": "identity_unprovable",
    })

    assert registry.check_availability(record) == ("moved", "identity_at_other_repo")


def test_availability_is_moved_when_a_fresh_directory_took_the_path(registry, tmp_path):
    root = plain_repo(tmp_path)
    record = registry.add(str(root), None)
    root.rename(root.parent / "elsewhere")
    root.mkdir()  # same path, different inode

    assert registry.check_availability(record) == ("moved", "identity_changed")


def test_availability_is_unavailable_when_a_file_took_the_path(registry, tmp_path):
    root = plain_repo(tmp_path)
    record = registry.add(str(root), None)
    root.rmdir()
    root.write_text("not a directory")

    assert registry.check_availability(record) == ("unavailable", "not_a_directory")


def test_availability_is_permission_denied_when_stat_is_refused(registry, tmp_path):
    parent = tmp_path / "locked"
    root = parent / "repo"
    root.mkdir(parents=True)
    record = registry.add(str(root), None)
    parent.chmod(0o000)
    try:
        assert registry.check_availability(record) == ("permission_denied", "permission_denied")
    finally:
        parent.chmod(0o755)


def test_availability_is_unprovable_when_the_identity_cannot_be_recomputed(registry, tmp_path,
                                                                          monkeypatch):
    root = plain_repo(tmp_path)
    record = registry.add(str(root), None)
    real_stat = os.stat

    def zero_ino(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        if str(path) == str(root):
            fields = list(st)
            fields[1] = 0
            return os.stat_result(tuple(fields))
        return st

    monkeypatch.setattr(os, "stat", zero_ino)
    assert registry.check_availability(record) == ("identity_unprovable", "identity_unprovable")


# --------------------------------------------------------------------------- #
# rebind
# --------------------------------------------------------------------------- #


def test_rebind_is_refused_while_the_repo_is_available(registry, store, tmp_path):
    root = plain_repo(tmp_path)
    record = registry.add(str(root), None)
    store.update("repos", record.repo_id, {"availability": "moved", "availability_detail": "stale"})
    other = plain_repo(tmp_path, "other")

    with pytest.raises(Exception) as exc:
        registry.rebind(record.repo_id, str(other))
    assert exc.value.code == "rebind_not_allowed"
    assert exc.value.details["availability"] == "available"
    row = store.get("repos", record.repo_id)
    assert (row["availability"], row["availability_detail"]) == ("available", None)
    assert row["canonical_path"] == str(root)


def test_rebind_moves_an_unavailable_repo_and_keeps_the_repo_id(registry, store, repo_builder,
                                                               tmp_path, clock):
    root = engine_repo(repo_builder, "devdelta")
    record = registry.add(str(root), "my repo")
    moved_to = root.parent / "moved-repo"
    root.rename(moved_to)
    clock.advance(60)

    rebound = registry.rebind(record.repo_id, str(moved_to))

    assert rebound.repo_id == record.repo_id
    assert rebound.canonical_path == str(moved_to)
    assert rebound.availability == "available"
    assert rebound.label == "my repo"
    assert rebound.added_at == record.added_at
    assert rebound.install_status == "installed"
    row = store.get("repos", record.repo_id)
    assert row["canonical_path"] == str(moved_to)
    assert row["last_seen"] == clock.iso()
    assert registry.check_availability(rebound) == ("available", None)


def test_rebind_refuses_a_path_owned_by_another_repo(registry, tmp_path):
    first = registry.add(str(plain_repo(tmp_path, "one")), None)
    second = registry.add(str(plain_repo(tmp_path, "two")), None)
    (tmp_path / "work" / "one").rename(tmp_path / "work" / "one-moved")

    with pytest.raises(Exception) as exc:
        registry.rebind(first.repo_id, str(tmp_path / "work" / "two"))
    assert exc.value.code == "duplicate_identity"
    assert exc.value.details["repo_id"] == second.repo_id


def test_rebind_refuses_a_bad_new_path(registry, tmp_path):
    root = plain_repo(tmp_path)
    record = registry.add(str(root), None)
    root.rename(root.parent / "gone")

    with pytest.raises(Exception) as exc:
        registry.rebind(record.repo_id, "relative/path")
    assert exc.value.code == "bad_path"


def test_rebind_of_an_unknown_repo_raises(registry, tmp_path):
    with pytest.raises(Exception) as exc:
        registry.rebind("r_000000000099", str(plain_repo(tmp_path)))
    assert exc.value.code == "repo_not_found"


# --------------------------------------------------------------------------- #
# list / get / touch_seen
# --------------------------------------------------------------------------- #


def test_list_hides_archived_rows_unless_asked(registry, store, tmp_path):
    first = registry.add(str(plain_repo(tmp_path, "one")), None)
    second = registry.add(str(plain_repo(tmp_path, "two")), None)
    store.update("repos", second.repo_id, {"archived": 1})

    assert [r.repo_id for r in registry.list()] == [first.repo_id]
    assert [r.repo_id for r in registry.list(include_archived=True)] == [first.repo_id, second.repo_id]
    assert registry.list(include_archived=True)[1].archived is True


def test_touch_seen_stamps_last_seen_and_tolerates_a_missing_row(registry, store, tmp_path, clock):
    record = registry.add(str(plain_repo(tmp_path)), None)
    clock.advance(120)
    registry.touch_seen(record.repo_id)

    assert store.get("repos", record.repo_id)["last_seen"] == clock.iso()
    registry.touch_seen("r_000000000099")  # a scan loop must not raise on a removed repo


# --------------------------------------------------------------------------- #
# detect_install
# --------------------------------------------------------------------------- #


def test_detect_install_reports_not_installed_without_a_harness(registry, tmp_path):
    record = registry.add(str(plain_repo(tmp_path)), None)
    health = registry.detect_install(record, None)

    assert health.status == "not_installed"
    assert (health.engine_dir, health.engine_version, health.stage_count) == (None, None, None)
    assert health.harness_dirs == ()
    assert health.to_json()["harness_dirs"] == []


def test_detect_install_reads_the_bundled_payload(registry, repo_builder, payload_manifest, K):
    record = registry.add(str(engine_repo(repo_builder, "payload")), None)
    health = registry.detect_install(record, None)

    assert health.status == "installed"
    assert health.engine_dir == K.STUDIO_HARNESS_DIR
    assert health.engine_version == payload_manifest["engineVersion"]
    assert health.engine_state_version == payload_manifest["compatibleStateVersions"][0]
    assert health.stage_count == payload_manifest["stageCount"]
    assert health.drift_count == 0
    assert health.receipt_id is None
    kiro = health.harness_dirs[0]
    assert (kiro.dir, kiro.harness_name, kiro.rules_subdir) == (K.STUDIO_HARNESS_DIR, "kiro", "steering")
    assert kiro.has_utility is True
    # The harness Studio reads and the harness Studio manages are the same one here.
    assert health.own_engine_version == payload_manifest["engineVersion"]
    assert health.to_json()["own_engine_version"] == payload_manifest["engineVersion"]


def test_detect_install_leaves_a_foreign_harness_repo_installed_with_no_harness_of_our_own(
    registry, repo_builder, payload_manifest
):
    """A repo whose AI-DLC lives under ``.claude``: Studio can drive it, and owns nothing in it.

    ``status`` deliberately stays ``installed`` — it is the "can this repo host AI-DLC work at all"
    gate, and ``not_installed`` would refuse intent creation and block the repo in the new-intent
    wizard, taking away a repository Studio drives perfectly well today. ``own_engine_version is
    None`` is therefore the one fact that says "Studio's own harness is not installed here", and it is
    what the install lane must key on so ``install`` stays offerable.
    """
    record = registry.add(str(foreign_engine_repo(repo_builder)), None)
    health = registry.detect_install(record, None)

    assert health.status == "installed"
    assert health.engine_dir == ".claude"
    assert health.engine_version == payload_manifest["engineVersion"]
    assert health.own_engine_version is None
    assert [h.dir for h in health.harness_dirs] == [".claude"]


def test_detect_install_reads_our_own_harness_when_it_sits_beside_a_stranger(
    registry, repo_builder, payload_manifest, K
):
    """Both harnesses present: every field answers about ``.kiro``, not about whichever came first."""
    root = repo_builder.with_engine("payload").build()
    shutil.copytree(F.OLDER / ".kiro", root / ".claude")
    record = registry.add(str(root), None)
    health = registry.detect_install(record, None)

    bundled = payload_manifest["engineVersion"]
    assert (health.engine_dir, health.engine_version) == (K.STUDIO_HARNESS_DIR, bundled)
    assert health.own_engine_version == bundled
    assert [h.dir for h in health.harness_dirs] == [K.STUDIO_HARNESS_DIR, ".claude"]
    assert health.harness_dirs[1].engine_version != bundled   # the stranger is an older AI-DLC


@pytest.mark.parametrize(
    "source, tree, stages, harness_name",
    [
        ("devdelta", F.DEVDELTA, 33, "kiro"),
        ("payload230", F.PAYLOAD_230, 32, None),
        ("older", F.OLDER, 32, None),
    ],
)
def test_detect_install_reads_each_fixture_tree(registry, repo_builder, source, tree, stages,
                                               harness_name):
    """Version, stage count and harness name come from the tree's own files (FORMAT-NOTES §4)."""
    record = registry.add(str(engine_repo(repo_builder, source)), None)
    health = registry.detect_install(record, None)

    version_text = (tree / ".kiro" / "tools" / "aidlc-version.ts").read_text("utf-8")
    expected_version = version_text.split('"')[1]
    assert health.status == "installed"
    assert health.engine_version == expected_version
    assert health.stage_count == stages
    assert len(F.stage_graph(tree)) == stages
    assert health.harness_dirs[0].harness_name == harness_name
    # These trees ship data files only: no aidlc-lib.ts, so the state version is unknown, not guessed.
    assert health.engine_state_version is None


def test_detect_install_reports_drift_when_a_receipt_owned_file_changed(registry, repo_builder, K):
    root = engine_repo(repo_builder, "payload")
    record = registry.add(str(root), None)
    owned = [f"{K.STUDIO_HARNESS_DIR}/tools/aidlc-version.ts",
             f"{K.STUDIO_HARNESS_DIR}/tools/data/harness.json"]
    receipt = receipt_for(root, owned)

    clean = registry.detect_install(record, receipt)
    assert (clean.status, clean.drift_count) == ("installed", 0)
    assert clean.receipt_id == receipt["receipt_id"]
    assert clean.receipt_engine_version == receipt["engine_version"]

    (root / owned[1]).write_text('{"harnessDir": ".kiro", "edited": true}\n')
    drifted = registry.detect_install(record, receipt)
    assert (drifted.status, drifted.drift_count) == ("drift", 1)

    (root / owned[0]).unlink()
    assert registry.detect_install(record, receipt).drift_count == 2


def test_detect_install_ignores_fragment_entries(registry, repo_builder, K):
    """A merge target's canonical digest belongs to the installer's strategies, not to a hash here."""
    root = engine_repo(repo_builder, "payload")
    record = registry.add(str(root), None)
    receipt = receipt_for(
        root,
        [f"{K.STUDIO_HARNESS_DIR}/tools/aidlc-version.ts"],
        extra_files=[{
            "path": f"{K.STUDIO_HARNESS_DIR}/settings/cli.json", "ownership": "merge",
            "kind": "fragment", "sha256": None, "canonical_digest": "f" * 64,
            "fragment_key": "cli.json:chat.defaultAgent", "adopted": False,
        }],
    )
    health = registry.detect_install(record, receipt)
    assert (health.status, health.drift_count) == ("installed", 0)


def test_detect_install_keeps_a_stored_recovery_required(registry, store, repo_builder):
    root = engine_repo(repo_builder, "payload")
    record = registry.add(str(root), None)
    store.update("repos", record.repo_id, {"install_status": "recovery_required"})

    health = registry.detect_install(registry.get(record.repo_id), None)
    assert health.status == "recovery_required"


def test_detect_install_on_an_unreadable_root_keeps_the_stored_status(registry, repo_builder):
    root = engine_repo(repo_builder, "devdelta")
    record = registry.add(str(root), None)
    assert record.install_status == "installed"
    root.rename(root.parent / "unmounted")

    health = registry.detect_install(registry.get(record.repo_id), None)
    assert health.status == "installed"          # never "not_installed" for a repo we cannot see
    assert health.engine_version == "2.6.2"
    assert health.harness_dirs == ()


def test_detect_install_on_an_unreadable_root_calls_the_stored_version_ours_only_when_it_was(
    registry, store, repo_builder
):
    """We cannot look, so the stored ``engine_dir`` is the only evidence of whose harness that was.

    Guessing "ours" for a stored ``.claude`` would make the install lane refuse an install that has
    never happened; guessing "not ours" for a stored ``.kiro`` would offer to install over an unmounted
    installation. Each branch reports exactly what was written down.
    """
    root = engine_repo(repo_builder, "devdelta")
    record = registry.add(str(root), None)
    stored = (F.DEVDELTA / ".kiro" / "tools" / "aidlc-version.ts").read_text("utf-8").split('"')[1]
    root.rename(root.parent / "unmounted")

    ours = registry.detect_install(registry.get(record.repo_id), None)
    assert (ours.engine_version, ours.own_engine_version) == (stored, stored)

    store.update("repos", record.repo_id, {"engine_dir": ".claude"})
    theirs = registry.detect_install(registry.get(record.repo_id), None)
    assert theirs.status == "installed"
    assert (theirs.engine_dir, theirs.engine_version) == (".claude", stored)
    assert theirs.own_engine_version is None


def test_detect_install_uses_the_stored_receipt_when_none_is_passed(registry, store, repo_builder, K):
    root = engine_repo(repo_builder, "payload")
    record = registry.add(str(root), None)
    receipt = receipt_for(root, [f"{K.STUDIO_HARNESS_DIR}/tools/aidlc-version.ts"])
    row = {k: v for k, v in receipt.items() if k != "files"}
    row["repo_id"] = record.repo_id
    store.insert("install_receipts", row)

    (root / K.STUDIO_HARNESS_DIR / "tools" / "aidlc-version.ts").write_text("edited\n")
    health = registry.detect_install(record)
    assert (health.status, health.drift_count) == ("drift", 1)
    assert registry.current_receipt_summary(record.repo_id).files == 1


# --------------------------------------------------------------------------- #
# preflight
# --------------------------------------------------------------------------- #

_PREFLIGHT_KEYS = {
    "path_input", "canonical_path", "identity", "is_directory", "sensitive", "duplicate_of",
    "platform", "git", "bun", "harness_dirs", "aidlc", "symlinks_at_managed_paths",
    "free_space_bytes", "writable", "existing_receipt", "warnings", "can_register", "can_install",
}


def test_preflight_reports_the_full_shape_and_starts_nothing(registry, repo_builder, git, bun_calls,
                                                             denied_log):
    root = (
        repo_builder.with_engine("devdelta")
        .with_workspace()
        .with_intent("260904-demo", state=F.real_state())
        .build()
    )
    report = registry.preflight(str(root))
    body = report.to_json()

    assert set(body) == _PREFLIGHT_KEYS
    assert body["canonical_path"] == str(root)
    assert body["is_directory"] is True and body["sensitive"] is False
    assert body["duplicate_of"] is None
    assert body["can_register"] is True and body["can_install"] is True
    assert body["identity"]["provable"] is True
    assert body["harness_dirs"][0]["dir"] == ".kiro"
    assert body["aidlc"] == {"layout": "spaces", "spaces": ["default"], "intents": 1,
                             "state_versions": [8]}
    assert body["free_space_bytes"] > 0 and body["writable"] is True
    assert body["warnings"] == [] and body["existing_receipt"] is None
    # Opt-in only: no git rev-parse, no bun, nothing denied.
    assert body["git"] is None and body["bun"] is None
    assert git.calls == [] and bun_calls == []
    assert not denied_log.exists()


def test_preflight_probes_git_and_bun_only_when_asked(registry, repo_builder, git, fake_bun,
                                                      bun_calls, monkeypatch):
    root = repo_builder.with_engine("devdelta").with_git(branch="feature/x").build()
    monkeypatch.setenv("PATH", str(Path(fake_bun).parent) + os.pathsep + os.environ["PATH"])
    git.branch = "feature/x"
    git.dirty = True

    report = registry.preflight(str(root), probe_tools=True)

    assert report.git.to_json() == {"is_repo": True, "branch": "feature/x", "dirty": True,
                                    "common_dir": str(root / ".git")}
    assert report.bun.to_json() == {"found": True, "path": fake_bun, "version": "1.1.42",
                                    "source": "path", "searched": [fake_bun]}
    assert bun_calls == [fake_bun]
    assert ("observe", str(root)) in git.calls
    assert report.identity.git_common_dir == str(root / ".git")


def test_preflight_finds_bun_where_the_installers_put_it_when_path_has_none(
    registry, repo_builder, git, bun_calls, fake_bun, monkeypatch, tmp_path, studio
):
    """The add-repo preflight refused installs on this machine because it only read PATH.

    ``fake_bun`` is never executed: the finder's proof that a candidate is bun is the prober this module
    was given, which is the whole reason the registry can still be grepped clean of child processes.
    """
    root = repo_builder.with_engine("devdelta").build()
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    monkeypatch.setattr(studio.engine, "BUN_CANDIDATE_PATHS", (fake_bun,))

    report = registry.preflight(str(root), probe_tools=True)

    assert report.bun.to_json() == {"found": True, "path": fake_bun, "version": "1.1.42",
                                    "source": "install_location", "searched": [fake_bun]}
    assert "bun_missing" not in [w.code for w in report.warnings]
    assert bun_calls == [fake_bun]


def test_preflight_warns_when_bun_and_git_are_missing(registry, repo_builder, git, monkeypatch,
                                                      tmp_path, studio):
    """An empty PATH is no longer enough to be missing: the install locations are neutralised too.

    Pointing ``BUN_CANDIDATE_PATHS`` at a path that does not exist is what keeps this test about Studio
    rather than about whether the machine running the suite happens to have bun installed.
    """
    nowhere = str(tmp_path / "not-installed" / "bun")
    root = repo_builder.with_engine("devdelta").build()
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    monkeypatch.setattr(studio.engine, "BUN_CANDIDATE_PATHS", (nowhere,))
    git.available = False
    git.reason = "git_missing"

    report = registry.preflight(str(root), probe_tools=True)
    codes = [w.code for w in report.warnings]

    assert report.bun.to_json() == {"found": False, "path": None, "version": None,
                                    "source": None, "searched": [nowhere]}
    assert "bun_missing" in codes and "git_missing" in codes
    # The finding carries the locations, so an exported warning is readable without the probe beside it.
    assert {w.code: w.params for w in report.warnings}["bun_missing"] == {"locations": [nowhere]}
    assert report.git.is_repo is False
    assert {w.code: w.severity for w in report.warnings}["git_missing"] == "info"
    assert all(w.message_key == f"finding.{w.code}" for w in report.warnings)


def test_preflight_reports_a_sensitive_path_instead_of_raising(registry, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".aws").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    report = registry.preflight(str(home / ".aws"))

    assert report.sensitive is True
    assert report.is_directory is True
    assert (report.can_register, report.can_install) == (False, False)
    assert report.identity is None          # nothing inside a protected directory is stat-ed
    assert report.harness_dirs == ()


def test_preflight_reports_a_file_and_a_missing_directory(registry, tmp_path):
    target = tmp_path / "a-file"
    target.write_text("x")

    for candidate in (target, tmp_path / "nope"):
        report = registry.preflight(str(candidate))
        assert report.is_directory is False
        assert (report.can_register, report.can_install) == (False, False)
        assert report.aidlc.layout is None
        assert report.free_space_bytes is None and report.writable is None


@pytest.mark.parametrize("bad", ["", "  ", "relative/path"])
def test_preflight_raises_only_for_an_uncanonicalisable_path(registry, bad):
    with pytest.raises(Exception) as exc:
        registry.preflight(bad)
    assert exc.value.code == "bad_path"


def test_preflight_flags_a_duplicate_and_carries_its_receipt(registry, store, repo_builder, K):
    root = engine_repo(repo_builder, "payload")
    record = registry.add(str(root), None)
    receipt = receipt_for(root, [f"{K.STUDIO_HARNESS_DIR}/tools/aidlc-version.ts"])
    row = {k: v for k, v in receipt.items() if k != "files"}
    row["repo_id"] = record.repo_id
    store.insert("install_receipts", row)

    report = registry.preflight(str(root))

    assert report.duplicate_of == record.repo_id
    assert report.can_register is False
    assert report.existing_receipt.to_json() == {
        "receipt_id": receipt["receipt_id"], "engine_version": receipt["engine_version"],
        "studio_version": "1.0.0", "committed_at": TS, "status": "current", "files": 1,
    }


def test_preflight_warns_on_the_legacy_flat_layout(registry, repo_builder, K):
    root = repo_builder.with_legacy_layout().build()

    report = registry.preflight(str(root))

    assert report.aidlc.layout == "legacy"
    assert report.aidlc.intents == 1
    assert [w.code for w in report.warnings] == ["legacy_layout"]
    assert report.warnings[0].params == {"path": K.LEGACY_STATE_REL}
    assert report.warnings[0].evidence[0].to_json() == {"kind": "file", "ref": K.LEGACY_STATE_REL,
                                                        "detail": None}
    assert report.can_register is True   # a legacy repo is registerable; it is only a warning


def test_preflight_warns_on_mixed_harness_versions(registry, repo_builder, tmp_path):
    root = repo_builder.with_engine("devdelta").build()
    shutil.copytree(F.OLDER / ".kiro", root / ".claude")

    report = registry.preflight(str(root))
    codes = [w.code for w in report.warnings]

    assert [h.dir for h in report.harness_dirs] == [".kiro", ".claude"]
    assert "mixed_harness_versions" in codes
    versions = next(w for w in report.warnings if w.code == "mixed_harness_versions").params["versions"]
    assert versions == ["2.1.1", "2.6.2"]


def test_preflight_warns_when_the_engine_version_is_unreadable(registry, repo_builder, K):
    root = repo_builder.with_engine("devdelta").build()
    (root / K.STUDIO_HARNESS_DIR / "tools" / "aidlc-version.ts").unlink()

    report = registry.preflight(str(root))

    assert [w.code for w in report.warnings] == ["engine_version_unknown"]
    assert report.harness_dirs[0].engine_version is None
    assert report.harness_dirs[0].stage_count == 33   # still a harness: data/harness.json is there


def test_preflight_flags_a_symlink_at_a_managed_path(registry, repo_builder, tmp_path):
    root = repo_builder.with_engine("devdelta").build()
    outside = tmp_path / "outside.md"
    outside.write_text("# elsewhere\n")
    (root / "AGENTS.md").symlink_to(outside)

    report = registry.preflight(str(root))

    assert report.symlinks_at_managed_paths == ("AGENTS.md",)
    assert report.can_install is False       # an os.replace would write outside the repository
    assert report.can_register is True


def test_preflight_reports_every_state_version_on_disk(registry, repo_builder):
    root = (
        repo_builder.with_engine("payload230")
        .with_workspace()
        .with_intent("260901-ledger-export",
                     state=F.state_path(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN))
        .with_intent("260820-health-probe",
                     state=F.state_path(F.PAYLOAD_230, F.PAYLOAD_COMPLETED), active=False)
        .build()
    )
    report = registry.preflight(str(root))

    assert report.aidlc.intents == 2
    assert report.aidlc.state_versions == (7,)
    assert report.harness_dirs[0].engine_version == "2.3.0"


def test_preflight_ignores_a_symlinked_harness_dir(registry, repo_builder, tmp_path):
    """A ``.kiro`` link pointing outside the repo is not an install; following it would read a stranger's tree."""
    root = repo_builder.build()
    elsewhere = tmp_path / "other-engine" / ".kiro"
    (elsewhere / "tools" / "data").mkdir(parents=True)
    (elsewhere / "tools" / "aidlc-version.ts").write_text('export const AIDLC_VERSION = "9.9.9";\n')
    (root / ".kiro").symlink_to(elsewhere)

    report = registry.preflight(str(root))

    assert report.harness_dirs == ()
    assert ".kiro" in report.symlinks_at_managed_paths


# --------------------------------------------------------------------------- #
# rescan
# --------------------------------------------------------------------------- #


def test_rescan_persists_availability_and_install_health(registry, store, repo_builder, clock,
                                                         payload_manifest):
    root = engine_repo(repo_builder, "payload")
    record = registry.add(str(root), None)
    clock.advance(300)

    scan = registry.rescan(record.repo_id)
    body = scan.to_json()

    assert set(body) == {"repo_id", "availability", "install", "intents", "findings", "took_ms",
                         "truncated", "scanned_at"}
    assert body["availability"] == "available"
    assert body["install"]["status"] == "installed"
    assert body["install"]["engine_version"] == payload_manifest["engineVersion"]
    assert body["intents"] == [] and body["findings"] == []
    assert body["took_ms"] >= 0 and body["truncated"] is False
    assert body["scanned_at"] == clock.iso()

    row = store.get("repos", record.repo_id)
    assert row["availability"] == "available"
    assert row["last_seen"] == clock.iso()
    assert row["install_status"] == "installed"
    assert row["scan_json"]["install"]["stage_count"] == payload_manifest["stageCount"]


def test_rescan_records_an_unavailable_repo_without_moving_last_seen(registry, store, repo_builder,
                                                                    clock):
    root = engine_repo(repo_builder, "devdelta")
    record = registry.add(str(root), None)
    seen_at = store.get("repos", record.repo_id)["last_seen"]
    root.rename(root.parent / "gone")
    clock.advance(600)

    scan = registry.rescan(record.repo_id)

    assert scan.availability == "unavailable"
    row = store.get("repos", record.repo_id)
    assert (row["availability"], row["availability_detail"]) == ("unavailable", "path_missing")
    assert row["last_seen"] == seen_at
    assert row["install_status"] == "installed"   # unchanged: the tree is invisible, not empty


def test_rescan_of_an_unknown_repo_raises(registry):
    with pytest.raises(Exception) as exc:
        registry.rescan("r_000000000099")
    assert exc.value.code == "repo_not_found"


# --------------------------------------------------------------------------- #
# no engine, no child process (C35 / review P13)
# --------------------------------------------------------------------------- #


def test_registry_never_runs_the_engine_or_a_child_process(registry, R, repo_builder, fake_bun,
                                                           denied_log, bun_calls, git, monkeypatch):
    """Preflight, add and detect_install must execute nothing from the candidate repository."""
    root = engine_repo(repo_builder, "payload")
    monkeypatch.setenv("PATH", str(Path(fake_bun).parent) + os.pathsep + os.environ["PATH"])

    registry.preflight(str(root))
    record = registry.add(str(root), None)
    registry.detect_install(record, None)
    registry.rescan(record.repo_id)

    assert bun_calls == []                       # no version probe on any of these paths
    assert not denied_log.exists()               # fake_bun was never invoked at all
    assert all(call[0] == "common_dir" for call in git.calls)   # only rev-parse --git-common-dir

    source = Path(R.__file__).read_text("utf-8")
    for forbidden in ("subprocess", "os.system", "shell=True", "--project-dir",
                      "aidlc-orchestrate", "aidlc-audit", "aidlc-jump", "aidlc-log"):
        assert forbidden not in source, forbidden


def test_registry_takes_no_engine_runner(R):
    params = list(inspect.signature(R.RepoRegistry.__init__).parameters)
    assert params == ["self", "storage", "git", "clock", "ids", "bun_version"]


def test_record_json_matches_the_frontend_contract(registry, repo_builder):
    record = registry.add(str(engine_repo(repo_builder, "devdelta")), None)
    body = record.to_json()

    assert set(body) == {
        "repo_id", "label", "canonical_path", "resolved_identity", "git_common_dir_identity",
        "platform", "added_at", "last_seen", "availability", "availability_detail", "archived",
        "legacy_console_id", "install_status", "installed_engine_version", "engine_dir",
        "receipt_version",
    }
    assert json.loads(json.dumps(body)) == body
    health = registry.detect_install(record, None).to_json()
    assert set(health) == {"status", "engine_dir", "engine_version", "own_engine_version",
                           "engine_state_version", "stage_count", "receipt_id",
                           "receipt_engine_version", "drift_count", "harness_dirs"}
    assert set(health["harness_dirs"][0]) == {"dir", "harness_name", "rules_subdir",
                                              "engine_version", "engine_state_version",
                                              "stage_count", "has_utility"}


def test_every_enum_value_the_registry_writes_is_canonical(registry, store, K, tmp_path,
                                                           repo_builder):
    registry.add(str(engine_repo(repo_builder, "payload")), None)
    registry.add(str(plain_repo(tmp_path, "bare")), None)

    for row in store.select("repos"):
        assert row["availability"] in K.AVAILABILITY
        assert row["install_status"] in K.INSTALL_STATUS
        assert row["platform"] in ("darwin", "linux", "win32")
