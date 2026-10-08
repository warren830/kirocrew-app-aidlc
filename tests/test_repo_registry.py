"""``backend/studio/repo_registry.py``: two spellings of one directory are one repository, a refusal leaves nothing
behind, unregistering never touches the tree, a preflight executes nothing, and install status comes from the bytes.

``Storage`` is the real thing on SQLite ("nothing was stored" is a claim about the table); ``RecordingGit`` answers
``common_dir`` from the repository's real ``.git`` and records every call. Versions are read from the payload
manifest or the fixture's own ``aidlc-version.ts`` (C21).
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
UNKNOWN = "r_000000000099"


class RecordingGit:
    """The read-only slice of ``GitObserver`` the registry is allowed to use (§1.5)."""

    def __init__(self, *, available: bool = True, reason: str | None = None, branch: str = "main",
                 dirty: bool = False, common_dir: str | None = "AUTO") -> None:
        self.available, self.reason, self.branch, self.dirty = available, reason, branch, dirty
        self._common_dir = common_dir
        self.calls: list[tuple[str, str]] = []

    def common_dir(self, repo: Path) -> str | None:
        self.calls.append(("common_dir", str(repo)))
        if self._common_dir == "AUTO":
            return str(Path(repo) / ".git") if (Path(repo) / ".git").exists() else None
        return self._common_dir

    def observe(self, repo: Path) -> SimpleNamespace:
        self.calls.append(("observe", str(repo)))
        return SimpleNamespace(available=self.available, reason=self.reason,
                               branch=self.branch if self.available else None, detached=False, head=None,
                               head_subject=None, dirty=self.dirty, dirty_files=1 if self.dirty else 0, ahead=None,
                               behind=None, upstream=None, observed_at=TS, took_ms=1)


@pytest.fixture
def R(studio):
    assert studio.repo_registry is not None, getattr(studio, "repo_registry__error", "repo_registry.py did not import")
    return studio.repo_registry


@pytest.fixture
def K(studio):
    return studio.constants


@pytest.fixture
def store(studio, fake_ctx, clock, ids):
    st = studio.storage.Storage(Path(fake_ctx.data_dir) / studio.constants.DB_FILENAME, clock=clock)
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
    """AI-DLC installed under a harness Studio does not manage (Claude Code's ``.claude``), nothing under ``.kiro``."""
    root = repo_builder.with_engine(source).build()
    (root / ".kiro").rename(root / harness)
    return root


def tree_digest(root: Path) -> dict[str, str]:
    """Every path under ``root``: ``relpath -> sha256`` for files, a marker for symlinks and directories."""
    return {str(path.relative_to(root)): "symlink:" + os.readlink(path) if path.is_symlink()
            else hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir"
            for path in sorted(root.rglob("*")) if path.is_symlink() or path.is_file() or path.is_dir()}


def zero_inode(monkeypatch, root: Path) -> None:
    """A filesystem that reports ``st_ino == 0`` for ``root``."""
    real_stat = os.stat

    def stat(path, *args, **kwargs):
        st = real_stat(path, *args, **kwargs)
        if str(path) != str(root):
            return st
        fields = list(st)
        fields[1] = 0
        return os.stat_result(tuple(fields))

    monkeypatch.setattr(os, "stat", stat)


def receipt_for(root: Path, rels: list[str], *, extra_files: list[dict] | None = None) -> dict:
    """A receipt row shaped like ``install_receipts`` (``detect_install`` accepts either shape)."""
    files = [{"path": rel, "ownership": "framework", "kind": "file",
              "sha256": hashlib.sha256((root / rel).read_bytes()).hexdigest(), "canonical_digest": None,
              "fragment_key": None, "adopted": False} for rel in rels] + (extra_files or [])
    return {"receipt_id": "rc_0000000000000001", "repo_id": "r_000000000001", "resolved_repo_identity": None,
            "studio_version": "1.0.0", "engine_version": "2.6.2", "payload_digest": "0" * 64, "committed_at": TS,
            "prior_receipt_id": None, "transaction_id": "tx_0000000000000001", "files_json": files,
            "status": "current", "files": files}


def store_receipt(store, receipt: dict, repo_id: str) -> None:
    store.insert("install_receipts", {**{k: v for k, v in receipt.items() if k != "files"}, "repo_id": repo_id})


def code_of(call) -> str:
    with pytest.raises(Exception) as exc:
        call()
    return exc.value.code


# --- identity ---
def test_identity_is_dev_ino_when_the_filesystem_supplies_it(registry, tmp_path):
    root = plain_repo(tmp_path)
    identity, st = registry.resolve_identity(str(root)), os.stat(root)
    assert identity.identity_str == f"{st.st_dev}:{st.st_ino}"
    assert (identity.st_dev, identity.st_ino) == (st.st_dev, st.st_ino)
    assert identity.provable is True and identity.canonical_path == str(root)
    assert identity.git_common_dir is None  # no .git in this tree


def test_identity_records_the_git_common_dir_when_there_is_one(registry, repo_builder, git):
    root = repo_builder.with_git().build()
    assert registry.resolve_identity(str(root)).git_common_dir == str(root / ".git")
    assert ("common_dir", str(root)) in git.calls


def test_identity_falls_back_to_the_path_digest_without_inodes(registry, repo_builder, monkeypatch, studio):
    """A filesystem that reports ``st_ino == 0`` must not collapse every repo into one identity."""
    root = repo_builder.with_git().build()
    zero_inode(monkeypatch, root)
    identity = registry.resolve_identity(str(root))
    assert identity.identity_str == studio.security.sha256_text(str(root) + "\0" + str(root / ".git"))
    assert (identity.st_dev, identity.st_ino) == (None, None)
    assert identity.provable is True  # the git common dir is the deterministic fallback


def test_identity_is_unprovable_without_inodes_or_git(registry, tmp_path, monkeypatch, studio):
    root = plain_repo(tmp_path)
    zero_inode(monkeypatch, root)
    identity = registry.resolve_identity(str(root))
    assert identity.provable is False and identity.identity_str == studio.security.sha256_text(str(root) + "\0" + "")
    record = registry.add(str(root), None)
    assert record.availability == "identity_unprovable"
    assert record.resolved_identity is None  # no lease may ever be keyed on an unproven identity


def test_two_case_spellings_of_one_directory_are_one_identity(registry, tmp_path, store):
    base = tmp_path / "work"
    base.mkdir(parents=True)
    (base / "CaseProbe").mkdir()
    if not (base / "caseprobe").is_dir():
        pytest.skip("filesystem is case-sensitive; the alias cannot exist")
    (base / "Repo").mkdir()
    first = registry.add(str(base / "Repo"), "typed case")
    assert registry.resolve_identity(str(base / "repo")).identity_str == first.resolved_identity
    with pytest.raises(Exception) as exc:
        registry.add(str(base / "repo"), "lower case")
    assert exc.value.code == "duplicate_identity" and exc.value.details["repo_id"] == first.repo_id
    assert len(store.select("repos")) == 1
    assert store.get("repos", first.repo_id)["canonical_path"] == str(base / "Repo")  # typed case kept (A13)


# --- add: refusals leave nothing behind ---
@pytest.mark.parametrize("bad", ["", "   ", "relative/path", "repo", "<file>", "<missing>"])
def test_add_refuses_a_relative_empty_file_or_missing_path(registry, store, tmp_path, bad):
    (tmp_path / "a-file").write_text("x")
    path = {"<file>": str(tmp_path / "a-file"), "<missing>": str(tmp_path / "nope")}.get(bad, bad)
    assert code_of(lambda: registry.add(path, None)) == "bad_path"
    assert store.select("repos") == []


def test_add_refuses_a_sensitive_path(registry, store, tmp_path, monkeypatch):
    (tmp_path / "home" / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert code_of(lambda: registry.add(str(tmp_path / "home" / ".ssh"), None)) == "sensitive_path"
    assert store.select("repos") == []


def test_add_refuses_the_same_path_twice(registry, store, tmp_path):
    root = plain_repo(tmp_path)
    first = registry.add(str(root), None)
    with pytest.raises(Exception) as exc:
        registry.add(str(root), None)
    assert exc.value.code == "duplicate_identity" and exc.value.details["repo_id"] == first.repo_id
    assert len(store.select("repos")) == 1


def test_add_refuses_beyond_the_registry_cap(registry, store, tmp_path, monkeypatch, K):
    monkeypatch.setattr(K, "MAX_REPOS", 2)
    for index in range(2):
        registry.add(str(plain_repo(tmp_path, f"repo-{index}")), None)
    with pytest.raises(Exception) as exc:
        registry.add(str(plain_repo(tmp_path, "one-too-many")), None)
    assert exc.value.code == "too_many_repos" and exc.value.details["max"] == 2
    assert len(store.select("repos")) == 2


def test_add_stores_the_row_and_defaults_the_label(registry, store, repo_builder, clock, K):
    root = engine_repo(repo_builder, "devdelta")
    record = registry.add(str(root), None)
    row = store.get("repos", record.repo_id)
    assert record.repo_id.startswith("r_") and record.label == root.name
    assert (row["canonical_path"], row["availability"], row["added_at"]) == (str(root), "available", clock.iso())
    assert row["install_status"] == "installed" and row["engine_dir"] == K.STUDIO_HARNESS_DIR
    assert record.to_json()["installed_engine_version"] == "2.6.2"


def test_add_truncates_an_overlong_label(registry, tmp_path, K):
    assert len(registry.add(str(plain_repo(tmp_path)), "L" * (K.MAX_LABEL_CHARS + 50)).label) == K.MAX_LABEL_CHARS


# --- remove ---
def test_remove_unregisters_only_and_never_touches_the_tree(registry, store, repo_builder):
    root = repo_builder.with_engine("devdelta").with_workspace().with_intent(
        "260904-demo", state=F.real_state(), audit=F.gate_open_audit("requirements-analysis")).with_git().build()
    record = registry.add(str(root), None)
    before = tree_digest(root)
    registry.remove(record.repo_id)
    assert tree_digest(root) == before and store.select("repos") == []
    assert code_of(lambda: registry.get(record.repo_id)) == "repo_not_found"


@pytest.mark.parametrize("operation", ["remove", "rebind", "rescan"])
def test_an_unknown_repo_raises(registry, tmp_path, operation):
    args = (UNKNOWN, str(plain_repo(tmp_path))) if operation == "rebind" else (UNKNOWN,)
    assert code_of(lambda: getattr(registry, operation)(*args)) == "repo_not_found"


def test_remove_cascades_bindings_and_actions(registry, store, tmp_path):
    record = registry.add(str(plain_repo(tmp_path)), None)
    store.insert("intent_bindings", {"binding_key": f"{record.repo_id}:default:260904-demo", "repo_id": record.repo_id,
                                     "space": "default", "intent_dir": "260904-demo", "created_at": TS,
                                     "updated_at": TS})
    registry.remove(record.repo_id)
    assert store.select("intent_bindings") == []


# --- availability ---
def test_availability_of_a_healthy_a_vanished_and_a_replaced_path(registry, tmp_path):
    root = plain_repo(tmp_path)
    record = registry.add(str(root), None)
    assert registry.check_availability(record) == ("available", None)
    root.rename(root.parent / "moved-away")
    assert registry.check_availability(record) == ("unavailable", "path_missing")
    root.mkdir()  # same path, different inode
    assert registry.check_availability(record) == ("moved", "identity_changed")
    root.rmdir()
    root.write_text("not a directory")
    assert registry.check_availability(record) == ("unavailable", "not_a_directory")


def test_availability_is_moved_when_the_identity_turns_up_at_another_row(registry, store, tmp_path):
    root = plain_repo(tmp_path)
    record = registry.add(str(root), None)
    root.rename(root.parent / "renamed")
    # a migrated/imported row (FR-MIG-003) pointing at the same directory under its new name
    store.insert("repos", {"id": "r_000000000042", "resolved_identity": None,
                           "canonical_path": str(root.parent / "renamed"), "label": "imported", "added_at": TS,
                           "platform": "darwin", "availability": "identity_unprovable"})
    assert registry.check_availability(record) == ("moved", "identity_at_other_repo")


def _renumber(store, repo_id: str, *, repo: bool = True) -> tuple[str, str]:
    """Rewrite a stored identity as if its volume had another device number (a macOS reboot), receipt included."""
    fresh = store.get("repos", repo_id)["resolved_identity"]
    old = f"{int(fresh.split(':')[0]) + 1}:{fresh.split(':')[1]}"
    with store._txn() as conn:
        if repo:
            conn.execute("UPDATE repos SET resolved_identity = ? WHERE id = ?", (old, repo_id))
        conn.execute("INSERT INTO install_receipts(receipt_id, repo_id, resolved_repo_identity, studio_version,"
                     " engine_version, payload_digest, committed_at, transaction_id, files_json, status)"
                     " VALUES('rc_1', ?, ?, '1.1.6', '2.7.1', ?, ?, 'tx_1', '[]', 'current')",
                     (repo_id, old, "0" * 64, TS))
    return fresh, old


@pytest.mark.parametrize("repo_row_renumbered", [True, False])
def test_a_renumbered_volume_is_the_same_repository_and_its_rows_are_re_stamped(registry, store, tmp_path,
                                                                                 repo_row_renumbered):
    """Same path and inode, new ``st_dev``: available, and every row follows (also a receipt a rebind left behind)."""
    record = registry.add(str(plain_repo(tmp_path)), None)
    fresh, _old = _renumber(store, record.repo_id, repo=repo_row_renumbered)
    assert registry.check_availability(registry.get(record.repo_id)) == ("available", None)
    assert registry.rescan(record.repo_id).availability == "available"
    assert store.get("repos", record.repo_id)["resolved_identity"] == fresh
    assert store.get("install_receipts", "rc_1")["resolved_repo_identity"] == fresh


def test_a_new_inode_at_the_same_path_is_not_re_stamped(registry, store, tmp_path):
    root = plain_repo(tmp_path)
    record = registry.add(str(root), None)
    _fresh, old = _renumber(store, record.repo_id)
    root.rename(root.parent / "elsewhere")
    root.mkdir()  # same path, different inode: a different directory, not a renumbering
    assert registry.rescan(record.repo_id).availability == "moved"
    assert store.get("repos", record.repo_id)["resolved_identity"] == old
    assert store.get("install_receipts", "rc_1")["resolved_repo_identity"] == old


def test_re_stamping_never_takes_an_identity_another_registration_holds(registry, store, tmp_path):
    record = registry.add(str(plain_repo(tmp_path)), None)
    fresh, old = _renumber(store, record.repo_id)
    store.insert("repos", {"id": "r_other", "resolved_identity": fresh, "canonical_path": str(tmp_path / "x"),
                           "label": "other", "added_at": TS, "platform": "darwin", "availability": "moved"})
    assert store.restamp_repo_identity(record.repo_id, fresh, int(fresh.split(":")[1])) == 0
    assert store.get("install_receipts", "rc_1")["resolved_repo_identity"] == old


def test_availability_is_permission_denied_when_stat_is_refused(registry, tmp_path):
    root = tmp_path / "locked" / "repo"
    root.mkdir(parents=True)
    record = registry.add(str(root), None)
    root.parent.chmod(0o000)
    try:
        assert registry.check_availability(record) == ("permission_denied", "permission_denied")
    finally:
        root.parent.chmod(0o755)


def test_availability_is_unprovable_when_the_identity_cannot_be_recomputed(registry, tmp_path, monkeypatch):
    root = plain_repo(tmp_path)
    record = registry.add(str(root), None)
    zero_inode(monkeypatch, root)
    assert registry.check_availability(record) == ("identity_unprovable", "identity_unprovable")


# --- rebind ---
def test_rebind_is_refused_while_the_repo_is_available(registry, store, tmp_path):
    root = plain_repo(tmp_path)
    record = registry.add(str(root), None)
    store.update("repos", record.repo_id, {"availability": "moved", "availability_detail": "stale"})
    with pytest.raises(Exception) as exc:
        registry.rebind(record.repo_id, str(plain_repo(tmp_path, "other")))
    assert exc.value.code == "rebind_not_allowed" and exc.value.details["availability"] == "available"
    row = store.get("repos", record.repo_id)
    assert (row["availability"], row["availability_detail"], row["canonical_path"]) == ("available", None, str(root))


def test_rebind_moves_an_unavailable_repo_and_keeps_the_repo_id(registry, store, repo_builder, clock):
    root = engine_repo(repo_builder, "devdelta")
    record = registry.add(str(root), "my repo")
    moved_to = root.parent / "moved-repo"
    root.rename(moved_to)
    clock.advance(60)
    rebound = registry.rebind(record.repo_id, str(moved_to))
    assert (rebound.repo_id, rebound.canonical_path, rebound.availability) == (record.repo_id, str(moved_to),
                                                                               "available")
    assert (rebound.label, rebound.added_at, rebound.install_status) == ("my repo", record.added_at, "installed")
    row = store.get("repos", record.repo_id)
    assert row["canonical_path"] == str(moved_to) and row["last_seen"] == clock.iso()
    assert registry.check_availability(rebound) == ("available", None)


def test_rebind_refuses_a_path_owned_by_another_repo_or_a_bad_path(registry, tmp_path):
    first = registry.add(str(plain_repo(tmp_path, "one")), None)
    second = registry.add(str(plain_repo(tmp_path, "two")), None)
    (tmp_path / "work" / "one").rename(tmp_path / "work" / "one-moved")
    with pytest.raises(Exception) as exc:
        registry.rebind(first.repo_id, str(tmp_path / "work" / "two"))
    assert exc.value.code == "duplicate_identity" and exc.value.details["repo_id"] == second.repo_id
    assert code_of(lambda: registry.rebind(first.repo_id, "relative/path")) == "bad_path"


# --- list / get ---
def test_list_hides_archived_rows_unless_asked(registry, store, tmp_path):
    first = registry.add(str(plain_repo(tmp_path, "one")), None)
    second = registry.add(str(plain_repo(tmp_path, "two")), None)
    store.update("repos", second.repo_id, {"archived": 1})
    assert [r.repo_id for r in registry.list()] == [first.repo_id]
    assert [r.repo_id for r in registry.list(include_archived=True)] == [first.repo_id, second.repo_id]
    assert registry.list(include_archived=True)[1].archived is True


# --- detect_install ---
def test_detect_install_reports_not_installed_without_a_harness(registry, tmp_path):
    health = registry.detect_install(registry.add(str(plain_repo(tmp_path)), None), None)
    assert health.status == "not_installed"
    assert (health.engine_dir, health.engine_version, health.stage_count) == (None, None, None)
    assert health.harness_dirs == () and health.to_json()["harness_dirs"] == []


def test_detect_install_reads_the_bundled_payload(registry, repo_builder, payload_manifest, K):
    health = registry.detect_install(registry.add(str(engine_repo(repo_builder, "payload")), None), None)
    version = payload_manifest["engineVersion"]
    assert (health.status, health.engine_dir, health.engine_version) == ("installed", K.STUDIO_HARNESS_DIR, version)
    assert health.engine_state_version == payload_manifest["compatibleStateVersions"][0]
    assert (health.stage_count, health.drift_count, health.receipt_id) == (payload_manifest["stageCount"], 0, None)
    kiro = health.harness_dirs[0]
    assert (kiro.dir, kiro.harness_name, kiro.rules_subdir) == (K.STUDIO_HARNESS_DIR, "kiro", "steering")
    assert kiro.has_utility is True
    # the harness Studio reads and the harness Studio manages are the same one here
    assert health.own_engine_version == version and health.to_json()["own_engine_version"] == version


def test_detect_install_leaves_a_foreign_harness_repo_installed_with_no_harness_of_our_own(registry, repo_builder,
                                                                                            payload_manifest):
    """AI-DLC under ``.claude`` can still host work (``installed``); ``own_engine_version is None`` keys install."""
    health = registry.detect_install(registry.add(str(foreign_engine_repo(repo_builder)), None), None)
    assert (health.status, health.engine_dir) == ("installed", ".claude")
    assert health.engine_version == payload_manifest["engineVersion"]
    assert health.own_engine_version is None and [h.dir for h in health.harness_dirs] == [".claude"]


def test_detect_install_reads_our_own_harness_when_it_sits_beside_a_stranger(registry, repo_builder,
                                                                             payload_manifest, K):
    """Both harnesses present: every field answers about ``.kiro``, not about whichever came first."""
    root = repo_builder.with_engine("payload").build()
    shutil.copytree(F.OLDER / ".kiro", root / ".claude")
    health = registry.detect_install(registry.add(str(root), None), None)
    bundled = payload_manifest["engineVersion"]
    assert (health.engine_dir, health.engine_version) == (K.STUDIO_HARNESS_DIR, bundled)
    assert health.own_engine_version == bundled
    assert [h.dir for h in health.harness_dirs] == [K.STUDIO_HARNESS_DIR, ".claude"]
    assert health.harness_dirs[1].engine_version != bundled  # the stranger is an older AI-DLC


@pytest.mark.parametrize("source, tree, stages, harness_name", [("devdelta", F.DEVDELTA, 33, "kiro"),
                                                                ("payload230", F.PAYLOAD_230, 32, None),
                                                                ("older", F.OLDER, 32, None)])
def test_detect_install_reads_each_fixture_tree(registry, repo_builder, source, tree, stages, harness_name):
    """Version, stage count and harness name come from the tree's own files (FORMAT-NOTES §4)."""
    health = registry.detect_install(registry.add(str(engine_repo(repo_builder, source)), None), None)
    version = (tree / ".kiro" / "tools" / "aidlc-version.ts").read_text("utf-8").split('"')[1]
    assert (health.status, health.engine_version, health.stage_count) == ("installed", version, stages)
    assert len(F.stage_graph(tree)) == stages
    assert health.harness_dirs[0].harness_name == harness_name
    assert health.engine_state_version is None  # data files only, no aidlc-lib.ts: unknown, not guessed


def test_detect_install_reports_drift_when_a_receipt_owned_file_changed(registry, repo_builder, K):
    root = engine_repo(repo_builder, "payload")
    record = registry.add(str(root), None)
    owned = [f"{K.STUDIO_HARNESS_DIR}/tools/aidlc-version.ts", f"{K.STUDIO_HARNESS_DIR}/tools/data/harness.json"]
    receipt = receipt_for(root, owned)
    clean = registry.detect_install(record, receipt)
    assert (clean.status, clean.drift_count, clean.receipt_id) == ("installed", 0, receipt["receipt_id"])
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
    fragment = {"path": f"{K.STUDIO_HARNESS_DIR}/settings/cli.json", "ownership": "merge", "kind": "fragment",
                "sha256": None, "canonical_digest": "f" * 64, "fragment_key": "cli.json:chat.defaultAgent",
                "adopted": False}
    receipt = receipt_for(root, [f"{K.STUDIO_HARNESS_DIR}/tools/aidlc-version.ts"], extra_files=[fragment])
    health = registry.detect_install(record, receipt)
    assert (health.status, health.drift_count) == ("installed", 0)


def test_detect_install_keeps_a_stored_recovery_required(registry, store, repo_builder):
    record = registry.add(str(engine_repo(repo_builder, "payload")), None)
    store.update("repos", record.repo_id, {"install_status": "recovery_required"})
    assert registry.detect_install(registry.get(record.repo_id), None).status == "recovery_required"


def test_detect_install_on_an_unreadable_root_keeps_what_was_stored(registry, store, repo_builder):
    """Never ``not_installed`` for a repo we cannot see; only the stored ``engine_dir`` says whose harness it was."""
    root = engine_repo(repo_builder, "devdelta")
    record = registry.add(str(root), None)
    assert record.install_status == "installed"
    stored = (F.DEVDELTA / ".kiro" / "tools" / "aidlc-version.ts").read_text("utf-8").split('"')[1]
    root.rename(root.parent / "unmounted")
    ours = registry.detect_install(registry.get(record.repo_id), None)
    assert (ours.status, ours.harness_dirs) == ("installed", ())
    assert ours.engine_version == ours.own_engine_version == stored == "2.6.2"
    store.update("repos", record.repo_id, {"engine_dir": ".claude"})
    theirs = registry.detect_install(registry.get(record.repo_id), None)
    assert (theirs.status, theirs.engine_dir, theirs.engine_version) == ("installed", ".claude", stored)
    assert theirs.own_engine_version is None


def test_detect_install_uses_the_stored_receipt_when_none_is_passed(registry, store, repo_builder, K):
    root = engine_repo(repo_builder, "payload")
    record = registry.add(str(root), None)
    store_receipt(store, receipt_for(root, [f"{K.STUDIO_HARNESS_DIR}/tools/aidlc-version.ts"]), record.repo_id)
    (root / K.STUDIO_HARNESS_DIR / "tools" / "aidlc-version.ts").write_text("edited\n")
    health = registry.detect_install(record)
    assert (health.status, health.drift_count) == ("drift", 1)
    assert registry.current_receipt_summary(record.repo_id).files == 1


# --- preflight ---
PREFLIGHT_KEYS = {"path_input", "canonical_path", "identity", "is_directory", "sensitive", "duplicate_of", "platform",
                  "git", "bun", "harness_dirs", "aidlc", "symlinks_at_managed_paths", "free_space_bytes", "writable",
                  "existing_receipt", "warnings", "can_register", "can_install"}


def test_preflight_reports_the_full_shape_and_starts_nothing(registry, repo_builder, git, bun_calls, denied_log):
    root = repo_builder.with_engine("devdelta").with_workspace().with_intent("260904-demo",
                                                                             state=F.real_state()).build()
    body = registry.preflight(str(root)).to_json()
    assert set(body) == PREFLIGHT_KEYS and body["canonical_path"] == str(root)
    assert body["is_directory"] is True and body["sensitive"] is False and body["duplicate_of"] is None
    assert body["can_register"] is True and body["can_install"] is True and body["identity"]["provable"] is True
    assert body["harness_dirs"][0]["dir"] == ".kiro"
    assert body["aidlc"] == {"layout": "spaces", "spaces": ["default"], "intents": 1, "state_versions": [8]}
    assert body["free_space_bytes"] > 0 and body["writable"] is True
    assert body["warnings"] == [] and body["existing_receipt"] is None
    # opt-in only: no git rev-parse, no bun, nothing denied
    assert body["git"] is None and body["bun"] is None and git.calls == [] and bun_calls == []
    assert not denied_log.exists()


def test_preflight_probes_git_and_bun_only_when_asked(registry, repo_builder, git, fake_bun, bun_calls, monkeypatch):
    root = repo_builder.with_engine("devdelta").with_git(branch="feature/x").build()
    monkeypatch.setenv("PATH", str(Path(fake_bun).parent) + os.pathsep + os.environ["PATH"])
    git.branch, git.dirty = "feature/x", True
    report = registry.preflight(str(root), probe_tools=True)
    assert report.git.to_json() == {"is_repo": True, "branch": "feature/x", "dirty": True,
                                    "common_dir": str(root / ".git")}
    assert report.bun.to_json() == {"found": True, "path": fake_bun, "version": "1.1.42", "source": "path",
                                    "searched": [fake_bun]}
    assert bun_calls == [fake_bun] and ("observe", str(root)) in git.calls
    assert report.identity.git_common_dir == str(root / ".git")


def test_preflight_finds_bun_where_the_installers_put_it_when_path_has_none(registry, repo_builder, bun_calls,
                                                                            fake_bun, monkeypatch, tmp_path, studio):
    """``fake_bun`` is never executed: the prober this module was given is the finder's proof of bun."""
    root = repo_builder.with_engine("devdelta").build()
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    monkeypatch.setattr(studio.engine, "BUN_CANDIDATE_PATHS", (fake_bun,))
    report = registry.preflight(str(root), probe_tools=True)
    assert report.bun.to_json() == {"found": True, "path": fake_bun, "version": "1.1.42", "source": "install_location",
                                    "searched": [fake_bun]}
    assert "bun_missing" not in [w.code for w in report.warnings] and bun_calls == [fake_bun]


def test_preflight_warns_when_bun_and_git_are_missing(registry, repo_builder, git, monkeypatch, tmp_path, studio):
    """Install locations are neutralised too, so this is about Studio, not about whether this machine has bun."""
    nowhere = str(tmp_path / "not-installed" / "bun")
    root = repo_builder.with_engine("devdelta").build()
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    monkeypatch.setattr(studio.engine, "BUN_CANDIDATE_PATHS", (nowhere,))
    git.available, git.reason = False, "git_missing"
    report = registry.preflight(str(root), probe_tools=True)
    assert report.bun.to_json() == {"found": False, "path": None, "version": None, "source": None,
                                    "searched": [nowhere]}
    codes = [w.code for w in report.warnings]
    assert "bun_missing" in codes and "git_missing" in codes
    # the finding carries the locations, so an exported warning is readable without the probe beside it
    assert {w.code: w.params for w in report.warnings}["bun_missing"] == {"locations": [nowhere]}
    assert report.git.is_repo is False and {w.code: w.severity for w in report.warnings}["git_missing"] == "info"
    assert all(w.message_key == f"finding.{w.code}" for w in report.warnings)


def test_preflight_reports_a_sensitive_path_instead_of_raising(registry, tmp_path, monkeypatch):
    (tmp_path / "home" / ".aws").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    report = registry.preflight(str(tmp_path / "home" / ".aws"))
    assert report.sensitive is True and report.is_directory is True
    assert (report.can_register, report.can_install) == (False, False)
    assert report.identity is None  # nothing inside a protected directory is stat-ed
    assert report.harness_dirs == ()


def test_preflight_reports_a_file_and_a_missing_directory(registry, tmp_path):
    (tmp_path / "a-file").write_text("x")
    for candidate in (tmp_path / "a-file", tmp_path / "nope"):
        report = registry.preflight(str(candidate))
        assert report.is_directory is False and (report.can_register, report.can_install) == (False, False)
        assert report.aidlc.layout is None and report.free_space_bytes is None and report.writable is None


@pytest.mark.parametrize("bad", ["", "  ", "relative/path"])
def test_preflight_raises_only_for_an_uncanonicalisable_path(registry, bad):
    assert code_of(lambda: registry.preflight(bad)) == "bad_path"


def test_preflight_flags_a_duplicate_and_carries_its_receipt(registry, store, repo_builder, K):
    root = engine_repo(repo_builder, "payload")
    record = registry.add(str(root), None)
    receipt = receipt_for(root, [f"{K.STUDIO_HARNESS_DIR}/tools/aidlc-version.ts"])
    store_receipt(store, receipt, record.repo_id)
    report = registry.preflight(str(root))
    assert report.duplicate_of == record.repo_id and report.can_register is False
    assert report.existing_receipt.to_json() == {"receipt_id": receipt["receipt_id"],
                                                 "engine_version": receipt["engine_version"], "studio_version": "1.0.0",
                                                 "committed_at": TS, "status": "current", "files": 1}


def test_preflight_warns_on_the_legacy_flat_layout(registry, repo_builder, K):
    report = registry.preflight(str(repo_builder.with_legacy_layout().build()))
    assert (report.aidlc.layout, report.aidlc.intents) == ("legacy", 1)
    assert [w.code for w in report.warnings] == ["legacy_layout"]
    assert report.warnings[0].params == {"path": K.LEGACY_STATE_REL}
    assert report.warnings[0].evidence[0].to_json() == {"kind": "file", "ref": K.LEGACY_STATE_REL, "detail": None}
    assert report.can_register is True  # a legacy repo is registerable; it is only a warning


def test_preflight_warns_on_mixed_harness_versions(registry, repo_builder):
    root = repo_builder.with_engine("devdelta").build()
    shutil.copytree(F.OLDER / ".kiro", root / ".claude")
    report = registry.preflight(str(root))
    assert [h.dir for h in report.harness_dirs] == [".kiro", ".claude"]
    assert "mixed_harness_versions" in [w.code for w in report.warnings]
    mixed = next(w for w in report.warnings if w.code == "mixed_harness_versions")
    assert mixed.params["versions"] == ["2.1.1", "2.6.2"]


def test_preflight_warns_when_the_engine_version_is_unreadable(registry, repo_builder, K):
    root = repo_builder.with_engine("devdelta").build()
    (root / K.STUDIO_HARNESS_DIR / "tools" / "aidlc-version.ts").unlink()
    report = registry.preflight(str(root))
    assert [w.code for w in report.warnings] == ["engine_version_unknown"]
    assert report.harness_dirs[0].engine_version is None
    assert report.harness_dirs[0].stage_count == 33  # still a harness: data/harness.json is there


def test_preflight_flags_a_symlink_at_a_managed_path(registry, repo_builder, tmp_path):
    root = repo_builder.with_engine("devdelta").build()
    (tmp_path / "outside.md").write_text("# elsewhere\n")
    (root / "AGENTS.md").symlink_to(tmp_path / "outside.md")
    report = registry.preflight(str(root))
    assert report.symlinks_at_managed_paths == ("AGENTS.md",)
    assert report.can_install is False  # an os.replace would write outside the repository
    assert report.can_register is True


def test_preflight_reports_every_state_version_on_disk(registry, repo_builder):
    root = (repo_builder.with_engine("payload230").with_workspace()
            .with_intent("260901-ledger-export", state=F.state_path(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN))
            .with_intent("260820-health-probe", state=F.state_path(F.PAYLOAD_230, F.PAYLOAD_COMPLETED), active=False)
            .build())
    report = registry.preflight(str(root))
    assert (report.aidlc.intents, report.aidlc.state_versions) == (2, (7,))
    assert report.harness_dirs[0].engine_version == "2.3.0"


def test_preflight_ignores_a_symlinked_harness_dir(registry, repo_builder, tmp_path):
    """A ``.kiro`` link pointing outside the repo is not an install; following it would read a stranger's tree."""
    root = repo_builder.build()
    elsewhere = tmp_path / "other-engine" / ".kiro"
    (elsewhere / "tools" / "data").mkdir(parents=True)
    (elsewhere / "tools" / "aidlc-version.ts").write_text('export const AIDLC_VERSION = "9.9.9";\n')
    (root / ".kiro").symlink_to(elsewhere)
    report = registry.preflight(str(root))
    assert report.harness_dirs == () and ".kiro" in report.symlinks_at_managed_paths


# --- rescan ---
def test_rescan_persists_availability_and_install_health(registry, store, repo_builder, clock, payload_manifest):
    record = registry.add(str(engine_repo(repo_builder, "payload")), None)
    clock.advance(300)
    body = registry.rescan(record.repo_id).to_json()
    assert set(body) == {"repo_id", "availability", "install", "intents", "findings", "took_ms", "truncated",
                         "scanned_at"}
    assert body["availability"] == "available" and body["install"]["status"] == "installed"
    assert body["install"]["engine_version"] == payload_manifest["engineVersion"]
    assert body["intents"] == [] and body["findings"] == []
    assert body["took_ms"] >= 0 and body["truncated"] is False and body["scanned_at"] == clock.iso()
    row = store.get("repos", record.repo_id)
    assert (row["availability"], row["last_seen"], row["install_status"]) == ("available", clock.iso(), "installed")
    assert row["scan_json"]["install"]["stage_count"] == payload_manifest["stageCount"]


def test_rescan_records_an_unavailable_repo_without_moving_last_seen(registry, store, repo_builder, clock):
    root = engine_repo(repo_builder, "devdelta")
    record = registry.add(str(root), None)
    seen_at = store.get("repos", record.repo_id)["last_seen"]
    root.rename(root.parent / "gone")
    clock.advance(600)
    assert registry.rescan(record.repo_id).availability == "unavailable"
    row = store.get("repos", record.repo_id)
    assert (row["availability"], row["availability_detail"]) == ("unavailable", "path_missing")
    assert row["last_seen"] == seen_at
    assert row["install_status"] == "installed"  # unchanged: the tree is invisible, not empty


# --- no engine, no child process (C35 / review P13) ---
def test_registry_never_runs_the_engine_or_a_child_process(registry, R, repo_builder, fake_bun, denied_log, bun_calls,
                                                           git, monkeypatch):
    """Preflight, add, detect_install and rescan execute nothing from the candidate repository."""
    root = engine_repo(repo_builder, "payload")
    monkeypatch.setenv("PATH", str(Path(fake_bun).parent) + os.pathsep + os.environ["PATH"])
    registry.preflight(str(root))
    record = registry.add(str(root), None)
    registry.detect_install(record, None)
    registry.rescan(record.repo_id)
    assert bun_calls == []  # no version probe on any of these paths
    assert not denied_log.exists()  # fake_bun was never invoked at all
    assert all(call[0] == "common_dir" for call in git.calls)  # only rev-parse --git-common-dir
    source = Path(R.__file__).read_text("utf-8")
    for forbidden in ("subprocess", "os.system", "shell=True", "--project-dir", "aidlc-orchestrate", "aidlc-audit",
                      "aidlc-jump", "aidlc-log"):
        assert forbidden not in source, forbidden


def test_registry_takes_no_engine_runner(R):
    params = list(inspect.signature(R.RepoRegistry.__init__).parameters)
    assert params == ["self", "storage", "git", "clock", "ids", "bun_version"]


def test_record_json_matches_the_frontend_contract(registry, repo_builder):
    """Key order too: ``to_json`` follows field declaration order, and ``scan_json`` is stored as text."""
    record = registry.add(str(engine_repo(repo_builder, "devdelta")), None)
    body = record.to_json()
    assert list(body) == ["repo_id", "label", "canonical_path", "resolved_identity", "git_common_dir_identity",
                          "platform", "added_at", "last_seen", "availability", "availability_detail", "archived",
                          "legacy_console_id", "install_status", "installed_engine_version", "engine_dir",
                          "receipt_version"]
    assert json.loads(json.dumps(body)) == body
    health = registry.detect_install(record, None).to_json()
    assert list(health) == ["status", "engine_dir", "engine_version", "own_engine_version", "engine_state_version",
                            "stage_count", "receipt_id", "receipt_engine_version", "drift_count", "harness_dirs"]
    assert list(health["harness_dirs"][0]) == ["dir", "harness_name", "rules_subdir", "engine_version",
                                               "engine_state_version", "stage_count", "has_utility"]


def test_every_enum_value_the_registry_writes_is_canonical(registry, store, K, tmp_path, repo_builder):
    registry.add(str(engine_repo(repo_builder, "payload")), None)
    registry.add(str(plain_repo(tmp_path, "bare")), None)
    for row in store.select("repos"):
        assert row["availability"] in K.AVAILABILITY and row["install_status"] in K.INSTALL_STATUS
        assert row["platform"] in ("darwin", "linux", "win32")
