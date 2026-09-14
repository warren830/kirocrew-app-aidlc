"""Tests for ``backend/studio/migration.py`` (contracts §1.22, FR-MIG-001…005).

The migration runs once, on a user's real registry, and there is no second chance to get it right — so
the properties asserted here are the ones whose failure would be unrecoverable:

* **Only Studio-owned data moves.** Every file read during a preview and an apply is recorded: the set
  must be exactly the prototype's ``repos.json`` and ``installed.json``. Not ``.app_secret``, not
  anything under a registered repository, not the host's config.
* **No registration is silently lost or silently duplicated.** Two rows that resolve to one identity
  become one repository with the collision reported (the case-alias hazard the prototype shipped with,
  07 §3.2); a row whose directory is gone becomes an *unavailable* repository the user can still rebind.
* **The rollback copy exists before the first write**, and a failure halfway through leaves the registry
  exactly as it was, with the attempt recorded as ``rolled_back`` and a retry still possible.
* **Applying twice is refused**, and the preview never writes anything at all.

The real ``Storage`` and the real ``RepoRegistry`` are used throughout: the whole point of this module is
the row it writes and the identity it computes, and a fake of either would test the fake. The source
fixture is the archived shape of a real prototype registry
(``docs/prototype-archive/aidlc-console-repos.json``) with its path pointed at a temp directory.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from conftest import APP_ROOT

ARCHIVE = APP_ROOT / "docs" / "prototype-archive" / "aidlc-console-repos.json"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def M(studio):
    module = studio.migration
    assert module is not None, getattr(studio, "migration__error", "migration.py did not import")
    return module


@pytest.fixture
def K(studio):
    return studio.constants


class StubGit:
    """The slice of ``GitObserver`` ``RepoRegistry`` uses while resolving an identity.

    ``None`` is the honest answer for a directory that is not a git repository, and it keeps the
    identity on the ``st_dev:st_ino`` path — which is the one the migration depends on. One test uses
    the real observer with ``fake_git`` to prove the common dir is carried through.
    """

    def common_dir(self, repo: Path) -> str | None:
        return None


@pytest.fixture
def apps_root(tmp_path: Path) -> Path:
    """``…/apps``: Studio and the prototype side by side, the way the host lays them out (07 §3.1)."""
    root = tmp_path / "apps"
    (root / "aidlc-studio" / "data").mkdir(parents=True)
    (root / "aidlc-console" / "data" / "kv").mkdir(parents=True)
    return root


@pytest.fixture
def console_dir(apps_root: Path) -> Path:
    return apps_root / "aidlc-console"


@pytest.fixture
def ctx(apps_root: Path, kirocrew_home: Path, manifest: dict):
    from kiro_crew.apps.app_storage import AppStorage
    from kiro_crew.apps.context import AppContext

    data_dir = apps_root / "aidlc-studio" / "data"
    return AppContext(
        name="aidlc-studio",
        data_dir=data_dir,
        config=dict(manifest.get("extra", {})),
        storage=AppStorage("aidlc-studio", data_dir),
    )


@pytest.fixture
def store(studio, ctx, K, clock, ids):
    st = studio.storage.Storage(Path(ctx.data_dir) / K.DB_FILENAME, clock=clock, ids=ids)
    st.open()
    yield st
    st.close()


@pytest.fixture
def registry(studio, store, clock, ids):
    return studio.repo_registry.RepoRegistry(store, StubGit(), clock, ids)


@pytest.fixture
def service(M, ctx, store, registry, clock, ids):
    return M.MigrationService(ctx, store, registry, clock, ids)


@pytest.fixture
def write_source(console_dir: Path):
    """Write the prototype's ``repos.json``; returns the raw bytes that landed on disk."""

    def _write(rows: object) -> bytes:
        path = console_dir / "data" / "kv" / "repos.json"
        raw = json.dumps(rows, indent=2).encode("utf-8")
        path.write_bytes(raw)
        return raw

    return _write


@pytest.fixture
def archived_row() -> dict:
    """One real row from the archived prototype registry (`{id, path, label, added_at}`)."""
    rows = json.loads(ARCHIVE.read_text("utf-8"))
    assert isinstance(rows, list) and rows, "the archived prototype registry must be a non-empty list"
    row = rows[0]
    assert set(row) == {"id", "path", "label", "added_at"}, row
    return dict(row)


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    """A directory standing in for a registered repository, with AI-DLC-owned files inside it."""
    root = tmp_path / "DevDelta"
    (root / ".kiro" / "tools").mkdir(parents=True)
    (root / ".kiro" / "tools" / "aidlc-version.ts").write_text('export const AIDLC_VERSION = "2.6.2"\n')
    (root / "aidlc" / "spaces" / "default" / "intents" / "one").mkdir(parents=True)
    (root / "aidlc" / "spaces" / "default" / "intents" / "one" / "aidlc-state.md").write_text("# state\n")
    (root / ".aidlc-steering-token-key").write_text("token-key-material\n")
    return root


def _console_installed(console_dir: Path, *, enabled: bool = True) -> None:
    (console_dir / "installed.json").write_text(
        json.dumps(
            {
                "name": "aidlc-console",
                "version": "0.1.0",
                "displayName": "AI-DLC Console",
                "enabled": enabled,
                "installedAt": "2026-09-04T09:06:39Z",
                "origin": "registry",
                "schemaVersion": 2,
            }
        )
    )


def _tree(root: Path) -> dict[str, str]:
    """Every file under ``root`` with its bytes, for a before/after comparison."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = path.read_bytes().hex()
    return out


# --------------------------------------------------------------------------- #
# the contract's own shapes
# --------------------------------------------------------------------------- #


def test_public_shapes_match_the_contract(M, service):
    """Field-for-field against §1.22: the UI's `MigrationPreview` (§3.1) is generated from this."""
    assert [f.name for f in dataclasses.fields(M.MigrationRowPlan)] == [
        "legacy_id", "path", "label", "added_at", "resolution", "identity", "error",
    ]
    assert [f.name for f in dataclasses.fields(M.MigrationPreview)] == [
        "applicable", "reason", "source_path", "source_sha256", "rows_in", "rows", "rows_out",
        "console_installed", "console_enabled", "already_applied",
    ]
    assert [f.name for f in dataclasses.fields(M.MigrationResult)] == [
        "migration_id", "applied_at", "status", "backup_path", "summary", "next_steps",
    ]
    assert set(M.MigrationPreview.__annotations__) == set(M.MigrationPreview(**{
        "applicable": False, "reason": None, "source_path": "", "source_sha256": None, "rows_in": 0,
        "rows": (), "rows_out": 0, "console_installed": False, "console_enabled": False,
        "already_applied": False,
    }).to_json())
    for name in ("source_path", "console_state", "preview", "apply", "status"):
        assert callable(getattr(service, name)), name
    assert sorted(service.status()) == ["applied", "console", "preview_available", "result"]
    assert M.STATUS_APPLIED in ("applied",) and M.STATUS_FAILED == "failed"
    assert M.STATUS_ROLLED_BACK == "rolled_back"


# --------------------------------------------------------------------------- #
# detection
# --------------------------------------------------------------------------- #


def test_source_and_console_state_come_from_the_sibling_app_dir(
    service, console_dir, K, write_source
):
    assert service.source_path() == console_dir / "data" / "kv" / "repos.json"
    assert service.source_path().name == f"{K.LEGACY_STORAGE_KEY}.json"
    assert console_dir.name == K.LEGACY_APP_NAME
    # No installed.json yet: not installed, not enabled — and no exception.
    assert service.console_state() == (False, False)

    _console_installed(console_dir, enabled=True)
    assert service.console_state() == (True, True)
    _console_installed(console_dir, enabled=False)
    assert service.console_state() == (True, False)


def test_keep_data_uninstall_is_still_migratable(service, console_dir, write_source, archived_row):
    """`uninstall --keep-data` removes `installed.json` and leaves the registry (07 §5.2)."""
    write_source([archived_row])
    assert service.console_state() == (False, False)
    preview = service.preview()
    assert (preview.console_installed, preview.console_enabled) == (False, False)
    assert preview.applicable is True and preview.reason is None


def test_missing_and_malformed_sources_are_reported_not_raised(M, service, console_dir, write_source):
    preview = service.preview()
    assert (preview.applicable, preview.reason) == (False, M.REASON_NOT_FOUND)
    assert preview.source_sha256 is None and preview.rows_in == 0 and preview.rows == ()
    with pytest.raises(Exception) as first:
        service.apply()
    assert first.value.code == "migration_not_applicable"

    write_source({"repos": []})  # an object, not the list the prototype writes
    preview = service.preview()
    assert (preview.applicable, preview.reason) == (False, M.REASON_MALFORMED)
    assert preview.source_sha256 is not None and preview.rows == ()
    with pytest.raises(Exception) as second:
        service.apply()
    assert second.value.code == "migration_not_applicable"
    assert second.value.details["reason"] == M.REASON_MALFORMED


# --------------------------------------------------------------------------- #
# preview
# --------------------------------------------------------------------------- #


def test_preview_of_a_real_prototype_row(M, service, write_source, archived_row, repo_dir):
    row = {**archived_row, "path": str(repo_dir)}
    raw = write_source([row])
    preview = service.preview()

    assert preview.applicable is True and preview.reason is None
    assert preview.already_applied is False
    assert preview.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert preview.rows_in == 1 and preview.rows_out == 1
    (plan,) = preview.rows
    assert plan.legacy_id == archived_row["id"]  # carried, never recomputed from the path
    assert plan.path == str(repo_dir)
    assert plan.label == archived_row["label"]
    assert plan.added_at == archived_row["added_at"]
    assert plan.resolution == M.RESOLUTION_MIGRATE
    st = os.stat(repo_dir)
    assert plan.identity == f"{st.st_dev}:{st.st_ino}"  # §1.5 identity, not sha256(path)
    assert plan.error is None
    assert preview.to_json()["rows"][0]["resolution"] == M.RESOLUTION_MIGRATE


def test_preview_writes_nothing(M, service, store, K, ctx, write_source, archived_row, repo_dir):
    raw = write_source([{**archived_row, "path": str(repo_dir)}])
    source = service.source_path()
    before_stat = source.stat().st_mtime_ns
    before_tree = _tree(Path(ctx.data_dir))

    service.preview()
    service.preview()

    assert store.select("repos") == []
    assert store.get("migrations", M.MIGRATION_ID) is None
    assert source.read_bytes() == raw and source.stat().st_mtime_ns == before_stat
    assert not (Path(ctx.data_dir) / K.MIGRATION_BACKUP_DIRNAME).exists()
    # The only file the previews may have touched is the database itself (WAL sidecars).
    after_tree = _tree(Path(ctx.data_dir))
    assert set(after_tree) - set(before_tree) <= {
        f"{K.DB_FILENAME}-wal",
        f"{K.DB_FILENAME}-shm",
    }


# --------------------------------------------------------------------------- #
# identity collapse and unavailable rows
# --------------------------------------------------------------------------- #


def test_alias_rows_collapse_to_one_repository(M, service, store, write_source, repo_dir, tmp_path):
    """Two spellings of one directory are one repository, and the collision is reported.

    A symlink is used so the test proves the identity rule on every filesystem; the case-alias variant
    below covers the exact hazard the prototype shipped with on macOS.
    """
    alias = tmp_path / "devdelta-link"
    alias.symlink_to(repo_dir, target_is_directory=True)
    write_source(
        [
            {"id": "aaaaaaaaaaaa", "path": str(repo_dir), "label": "DevDelta", "added_at": "2026-09-04T09:48:49Z"},
            {"id": "bbbbbbbbbbbb", "path": str(alias), "label": "devdelta", "added_at": "2026-09-03T08:00:00Z"},
        ]
    )
    preview = service.preview()
    assert preview.rows_in == 2 and preview.rows_out == 1
    first, second = preview.rows
    assert first.resolution == M.RESOLUTION_MIGRATE
    assert second.resolution == f"{M.RESOLUTION_DUPLICATE_PREFIX}aaaaaaaaaaaa"
    assert first.identity == second.identity

    result = service.apply()
    assert result.summary["duplicates"] == 1 and result.summary["rows_out"] == 1
    rows = store.select("repos")
    assert len(rows) == 1
    assert rows[0]["legacy_console_id"] == "aaaaaaaaaaaa"
    assert rows[0]["canonical_path"] == str(repo_dir)
    # The surviving row keeps the earliest registration time of the group.
    assert rows[0]["added_at"] == "2026-09-03T08:00:00Z"


def test_case_aliased_rows_collapse(M, service, store, write_source, repo_dir):
    """The prototype's real bug: `/…/devdelta` and `/…/DevDelta` hashed differently (07 §3.2)."""
    lowered = repo_dir.parent / repo_dir.name.lower()
    if not lowered.is_dir():
        pytest.skip("case-sensitive filesystem: the case-alias hazard cannot occur here")
    write_source(
        [
            {"id": "75d6cb9ecb67", "path": str(lowered), "label": "devdelta", "added_at": "2026-09-04T09:48:49Z"},
            {"id": "6e64d0745308", "path": str(repo_dir), "label": "DevDelta", "added_at": "2026-09-04T09:49:00Z"},
        ]
    )
    preview = service.preview()
    assert preview.rows_out == 1
    assert preview.rows[1].resolution == f"{M.RESOLUTION_DUPLICATE_PREFIX}75d6cb9ecb67"
    service.apply()
    assert len(store.select("repos")) == 1


def test_unavailable_path_is_kept_and_stays_rebindable(
    M, service, store, registry, write_source, repo_dir, tmp_path
):
    gone = tmp_path / "moved-away"
    write_source(
        [
            {"id": "cccccccccccc", "path": str(gone), "label": "gone", "added_at": "2026-09-01T00:00:00Z"},
            {"id": "dddddddddddd", "path": str(repo_dir), "label": "here", "added_at": "2026-09-02T00:00:00Z"},
        ]
    )
    preview = service.preview()
    assert preview.rows_out == 2  # the missing one is kept, not dropped
    missing = preview.rows[0]
    assert missing.resolution == M.RESOLUTION_UNAVAILABLE
    assert missing.identity is None and missing.error == "bad_path"

    result = service.apply()
    assert result.summary["unavailable"] == 1
    rows = {row["legacy_console_id"]: row for row in store.select("repos")}
    kept = rows["cccccccccccc"]
    assert kept["availability"] == "unavailable"
    assert kept["availability_detail"] == "path_missing"
    assert kept["resolved_identity"] is None
    assert kept["canonical_path"] == str(gone)
    assert kept["label"] == "gone" and kept["added_at"] == "2026-09-01T00:00:00Z"
    assert kept["install_status"] == "not_installed"

    # FR-REP-009: the user can point the migrated row at wherever the repository went.
    landed = tmp_path / "moved-here"
    landed.mkdir()
    rebound = registry.rebind(kept["id"], str(landed))
    assert rebound.repo_id == kept["id"]  # the id survives a rebind (C01)
    assert rebound.canonical_path == str(landed)
    assert rebound.availability == "available" and rebound.legacy_console_id == "cccccccccccc"


def test_protected_path_is_kept_unavailable_never_read(M, service, store, studio, write_source, tmp_path, monkeypatch):
    protected = tmp_path / "protected"
    protected.mkdir()
    monkeypatch.setattr(
        studio.security, "is_sensitive", lambda path: str(path) == str(protected)
    )
    write_source([{"id": "eeeeeeeeeeee", "path": str(protected), "label": "p", "added_at": None}])

    preview = service.preview()
    (plan,) = preview.rows
    assert plan.resolution == M.RESOLUTION_UNAVAILABLE and plan.error == "sensitive_path"
    service.apply()
    (row,) = store.select("repos")
    assert row["availability"] == "unavailable" and row["availability_detail"] == "sensitive_path"
    assert row["resolved_identity"] is None


def test_row_without_a_usable_path_is_reported_and_not_inserted(M, service, store, write_source):
    write_source(["not-a-row", {"id": "ffffffffffff", "label": "no path"}, 42])
    preview = service.preview()
    assert preview.rows_in == 3 and preview.rows_out == 0
    assert [row.error for row in preview.rows] == [M.ERROR_MALFORMED_ROW] * 3
    result = service.apply()
    assert result.status == M.STATUS_APPLIED
    assert store.select("repos") == []
    assert result.summary["rows_in"] == 3 and result.summary["rows_out"] == 0


def test_identity_unprovable_row_is_migrated_observe_only(
    M, service, store, registry, write_source, repo_dir, monkeypatch
):
    """A filesystem with no inode numbers and no git: registered, but never leaseable (FR-REP-005)."""
    unprovable = _unprovable_identity(registry, repo_dir)
    monkeypatch.setattr(registry, "resolve_identity", lambda path: unprovable)
    if True:
        write_source([{"id": "111111111111", "path": str(repo_dir), "label": "u", "added_at": None}])
        preview = service.preview()
        (plan,) = preview.rows
        assert plan.resolution == M.RESOLUTION_MIGRATE and plan.identity is None
        assert plan.error == "identity_unprovable"
        service.apply()
        (row,) = store.select("repos")
        assert row["availability"] == "identity_unprovable" and row["resolved_identity"] is None


def _unprovable_identity(registry, repo_dir: Path):
    """The identity of ``repo_dir`` with ``provable`` forced off (no inode, no git common dir)."""
    identity = registry.resolve_identity(str(repo_dir))
    return type(identity)(
        canonical_path=identity.canonical_path,
        st_dev=None,
        st_ino=None,
        git_common_dir=None,
        identity_str=identity.identity_str,
        provable=False,
    )


def test_git_common_dir_is_carried_into_the_row(M, studio, ctx, store, clock, ids, write_source, repo_dir, fake_git):
    """With a real observer the git identity fallback lands on the row (§1.5)."""
    git = studio.git_observer.GitObserver(fake_git, clock)
    registry = studio.repo_registry.RepoRegistry(store, git, clock, ids)
    service = M.MigrationService(ctx, store, registry, clock, ids)
    write_source([{"id": "222222222222", "path": str(repo_dir), "label": "g", "added_at": None}])
    service.apply()
    (row,) = store.select("repos")
    assert row["git_common_dir_identity"] == str(repo_dir / ".git")


# --------------------------------------------------------------------------- #
# apply
# --------------------------------------------------------------------------- #


def test_apply_writes_the_backup_and_the_versioned_record(
    M, service, store, ctx, K, clock, write_source, archived_row, repo_dir
):
    raw = write_source([{**archived_row, "path": str(repo_dir)}])
    result = service.apply()

    assert result.status == M.STATUS_APPLIED
    assert result.migration_id == M.MIGRATION_ID == "aidlc-console-v1"
    assert result.applied_at == clock.iso()
    assert result.next_steps == ("disable_console", "uninstall_console_keep_data")

    backup = Path(result.backup_path)
    assert backup.parent == Path(ctx.data_dir) / K.MIGRATION_BACKUP_DIRNAME
    assert backup.read_bytes() == raw  # byte-exact rollback copy
    assert ":" not in backup.name  # a stamp that is legal on every platform
    assert result.summary["source_sha256"] == hashlib.sha256(raw).hexdigest()

    row = store.get("migrations", M.MIGRATION_ID)
    assert row["status"] == M.STATUS_APPLIED
    assert row["applied_at"] == result.applied_at  # the returned record IS the durable one
    assert row["backup_path"] == str(backup)
    assert row["summary_json"]["rows_in"] == 1
    assert row["summary_json"]["repo_ids"] == [store.select("repos")[0]["id"]]


def test_apply_twice_is_refused(M, service, store, write_source, archived_row, repo_dir):
    write_source([{**archived_row, "path": str(repo_dir)}])
    service.apply()
    before = store.select("repos")

    with pytest.raises(Exception) as exc:
        service.apply()
    assert exc.value.code == "migration_already_applied"
    assert store.select("repos") == before  # nothing inserted a second time

    preview = service.preview()
    assert preview.already_applied is True
    assert (preview.applicable, preview.reason) == (False, M.REASON_ALREADY_APPLIED)
    # The rows still explain themselves: every one is now a repository Studio already has.
    (plan,) = preview.rows
    assert plan.resolution.startswith(M.RESOLUTION_REGISTERED_PREFIX)


def test_apply_refuses_a_source_that_changed_since_the_preview(
    M, service, store, write_source, archived_row, repo_dir
):
    write_source([{**archived_row, "path": str(repo_dir)}])
    stale = service.preview().source_sha256
    write_source([{**archived_row, "path": str(repo_dir), "label": "renamed"}])

    with pytest.raises(Exception) as exc:
        service.apply(expect_sha256=stale)
    assert exc.value.code == "bad_body"
    assert exc.value.details["key"] == "source_sha256"
    assert store.select("repos") == []

    fresh = service.preview().source_sha256
    assert service.apply(expect_sha256=fresh).status == M.STATUS_APPLIED


def test_already_registered_repository_is_not_migrated_twice(
    M, service, store, registry, write_source, archived_row, repo_dir
):
    existing = registry.add(str(repo_dir), "added by hand")
    write_source([{**archived_row, "path": str(repo_dir)}])

    preview = service.preview()
    (plan,) = preview.rows
    assert plan.resolution == f"{M.RESOLUTION_REGISTERED_PREFIX}{existing.repo_id}"
    assert preview.rows_out == 0

    result = service.apply()
    assert result.summary["already_registered"] == 1 and result.summary["repo_ids"] == []
    rows = store.select("repos")
    assert len(rows) == 1 and rows[0]["id"] == existing.repo_id
    assert rows[0]["label"] == "added by hand"  # the user's own row is not overwritten
    assert rows[0]["legacy_console_id"] == archived_row["id"]  # …only the link is recorded


def test_failure_midway_leaves_the_registry_untouched_and_retryable(
    M, service, store, ctx, K, write_source, repo_dir, tmp_path, monkeypatch
):
    other = tmp_path / "second-repo"
    other.mkdir()
    raw = write_source(
        [
            {"id": "333333333333", "path": str(repo_dir), "label": "one", "added_at": None},
            {"id": "444444444444", "path": str(other), "label": "two", "added_at": None},
        ]
    )
    real_insert = store.insert
    seen = {"repos": 0}

    def flaky(table: str, row: dict) -> None:
        if table == "repos":
            seen["repos"] += 1
            if seen["repos"] == 2:
                raise RuntimeError("simulated write failure")
        real_insert(table, row)

    monkeypatch.setattr(store, "insert", flaky)
    with pytest.raises(RuntimeError):
        service.apply()

    assert store.select("repos") == []  # all or nothing
    row = store.get("migrations", M.MIGRATION_ID)
    assert row["status"] == M.STATUS_ROLLED_BACK and row["summary_json"]["repo_ids"] == []
    backups = sorted((Path(ctx.data_dir) / K.MIGRATION_BACKUP_DIRNAME).glob("*.json"))
    assert len(backups) == 1 and backups[0].read_bytes() == raw  # written before anything changed

    monkeypatch.undo()
    result = service.apply()
    assert result.status == M.STATUS_APPLIED
    assert len(store.select("repos")) == 2
    assert store.get("migrations", M.MIGRATION_ID)["status"] == M.STATUS_APPLIED


def test_rollback_is_the_transaction_not_the_compensating_delete(
    M, service, store, write_source, repo_dir, tmp_path, monkeypatch
):
    """The batch is one `BEGIN IMMEDIATE`: undoing it must not depend on deleting rows afterwards."""
    assert getattr(store, "transaction", None) or getattr(store, "_txn", None), (
        "Storage no longer exposes a transaction helper; migration.apply is no longer atomic"
    )
    other = tmp_path / "second-repo"
    other.mkdir()
    write_source(
        [
            {"id": "aaaa00000001", "path": str(repo_dir), "label": "one", "added_at": None},
            {"id": "aaaa00000002", "path": str(other), "label": "two", "added_at": None},
        ]
    )
    real_insert = store.insert
    seen = {"repos": 0}

    def flaky(table: str, row: dict) -> None:
        if table == "repos":
            seen["repos"] += 1
            if seen["repos"] == 2:
                raise RuntimeError("simulated write failure")
        real_insert(table, row)

    def refuse_delete(table: str, key: str) -> None:
        raise AssertionError(f"the rollback must not need a compensating delete of {table}")

    monkeypatch.setattr(store, "insert", flaky)
    monkeypatch.setattr(store, "delete", refuse_delete)
    with pytest.raises(RuntimeError):
        service.apply()
    monkeypatch.undo()
    assert store.select("repos") == []


def test_interrupted_apply_resumes_without_duplicating(
    M, service, store, registry, write_source, repo_dir, tmp_path, monkeypatch
):
    """A crash after the repo rows but before the marker: the retry recognises them (no second row)."""
    other = tmp_path / "second-repo"
    other.mkdir()
    write_source(
        [
            {"id": "555555555555", "path": str(repo_dir), "label": "one", "added_at": None},
            {"id": "666666666666", "path": str(other), "label": "two", "added_at": None},
        ]
    )
    # Simulate the half-applied state: the first repository is already in Studio's registry.
    registry.add(str(repo_dir), "one")
    result = service.apply()
    assert result.summary["already_registered"] == 1 and result.summary["rows_out"] == 1
    paths = sorted(row["canonical_path"] for row in store.select("repos"))
    assert paths == sorted([str(repo_dir), str(other)])


def test_apply_refuses_to_exceed_the_repository_cap(M, service, store, K, write_source, repo_dir, tmp_path, monkeypatch):
    other = tmp_path / "second-repo"
    other.mkdir()
    write_source(
        [
            {"id": "777777777777", "path": str(repo_dir), "label": "one", "added_at": None},
            {"id": "888888888888", "path": str(other), "label": "two", "added_at": None},
        ]
    )
    monkeypatch.setattr(K, "MAX_REPOS", 1)
    with pytest.raises(Exception) as exc:
        service.apply()
    assert exc.value.code == "too_many_repos"
    assert store.select("repos") == []
    assert store.get("migrations", M.MIGRATION_ID) is None


def test_backups_are_bounded(M, service, ctx, K, store, write_source, repo_dir, clock, monkeypatch):
    write_source([{"id": "999999999999", "path": str(repo_dir), "label": "one", "added_at": None}])
    directory = Path(ctx.data_dir) / K.MIGRATION_BACKUP_DIRNAME
    directory.mkdir(parents=True)
    for i in range(M.MAX_BACKUPS + 3):
        (directory / f"aidlc-console-repos.2026010{i}T000000Z.json").write_bytes(b"[]")
    service.apply()
    copies = sorted(directory.glob("*.json"))
    assert len(copies) == M.MAX_BACKUPS
    assert copies[-1].name.endswith(f"{clock.iso().replace('-', '').replace(':', '')}.json")


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #


def test_status_before_and_after(M, service, console_dir, store, write_source, archived_row, repo_dir):
    assert service.status() == {
        "applied": False,
        "result": None,
        "preview_available": False,
        "console": {"installed": False, "enabled": False},
    }
    write_source([{**archived_row, "path": str(repo_dir)}])
    _console_installed(console_dir, enabled=True)
    mid = service.status()
    assert mid["applied"] is False and mid["preview_available"] is True
    assert mid["console"] == {"installed": True, "enabled": True}

    result = service.apply()
    after = service.status()
    assert after["applied"] is True
    assert after["result"]["status"] == M.STATUS_APPLIED
    assert after["result"]["backup_path"] == result.backup_path
    assert after["result"]["next_steps"] == list(M.NEXT_STEPS)
    assert after["result"]["summary"]["rows_out"] == 1


# --------------------------------------------------------------------------- #
# what must never be touched
# --------------------------------------------------------------------------- #


def test_only_the_two_prototype_files_are_ever_read(
    M, studio, service, console_dir, ctx, write_source, archived_row, repo_dir, monkeypatch
):
    """Credentials, host config and repository contents are never opened (§1.22)."""
    secret = console_dir / ".app_secret"
    secret.write_text("s3cr3t-app-secret-material")
    secret.chmod(stat.S_IRUSR | stat.S_IWUSR)
    write_source([{**archived_row, "path": str(repo_dir)}])
    repo_before = _tree(repo_dir)

    reads: list[str] = []
    for name in ("bounded_read", "bounded_text", "tail_read", "sha256_file"):
        original = getattr(studio.security, name)

        def spy(path, *args, _original=original, **kwargs):
            reads.append(str(path))
            return _original(path, *args, **kwargs)

        monkeypatch.setattr(studio.security, name, spy)

    service.preview()
    result = service.apply()

    allowed = {str(service.source_path()), str(console_dir / "installed.json")}
    assert set(reads) == allowed, reads
    assert not any(str(repo_dir) in path for path in reads)
    assert Path(result.backup_path).read_bytes() == service.source_path().read_bytes()
    assert "s3cr3t" not in Path(result.backup_path).read_text()
    db = (Path(ctx.data_dir) / studio.constants.DB_FILENAME).read_bytes()
    assert b"s3cr3t" not in db
    assert b".app_secret" not in db
    assert _tree(repo_dir) == repo_before  # not one byte of AI-DLC state changed


def test_module_never_names_a_host_app_api_or_a_secret(M):
    """Prose may discuss them (and does); no executable line may name one."""
    for text in _code_text(Path(M.__file__).read_text("utf-8")):
        for forbidden in ("/api/apps", ".app_secret", "disable_app", "uninstall_app", "copytree"):
            assert forbidden not in text, (forbidden, text)


def _code_text(source: str) -> list[str]:
    """Every string literal and dotted name in the module except docstrings and comments."""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            out.append(node.value)
        elif isinstance(node, ast.Attribute):
            out.append(node.attr)
        elif isinstance(node, ast.Name):
            out.append(node.id)
    return out
