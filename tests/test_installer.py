"""Installer (§1.14): after a failure at any step the repository is exactly the previous installation with its
receipt current, or it is ``recovery_required``; and Studio owns a fragment of a user's file, never the file.

Most tests run against a miniature payload whose ownership kinds and merge specs come from the manifest builder's
own rules (``scripts/build_payload_manifest.py``), so it keeps every kind of file even when a release stops shipping
one; the manifest-shaped tests run against the real payload.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import importlib.util
import inspect
import json
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

import fixtures as F

APP_ROOT = Path(__file__).resolve().parent.parent
REAL_PAYLOAD = APP_ROOT / "payload"


def _load_builder() -> Any:
    spec = importlib.util.spec_from_file_location("studio_payload_manifest_builder",
                                                  APP_ROOT / "scripts" / "build_payload_manifest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: The script that writes ``payload/manifest.json``: its ``classify`` and ``derive_merge_targets`` describe the mini
#: payloads exactly as they would describe a release shipping those files.
BUILDER = _load_builder()

TS = "2026-09-04T10:00:00Z"
IDENTITY = "1:100"
SKILL = ".kiro/skills/aidlc/SKILL.md"
CLI = ".kiro/settings/cli.json"
#: The mini payloads' cli.json. The real payload's keys, plus the per-model defaults the merge machinery is
#: tested on: 2.11 ships none (Kiro CLI asks for the session model at setup), but a repository installed from
#: 2.10 still holds Studio's managed entry, so the machinery that owns and retires it must keep working.
MINI_CLI = {"chat.defaultAgent": "aidlc",
            "chat.modelDefaults": {"claude-opus-4.8": {"output_config": {"effort": "xhigh"}}}}
MCP = ".kiro/settings/mcp.json"
MEMORY = "aidlc/spaces/default/memory/project.md"


@pytest.fixture
def I(studio):
    module = studio.installer
    assert module is not None, getattr(studio, "installer__error", "installer.py did not import")
    return module


@pytest.fixture
def C(studio):
    return studio.constants


@pytest.fixture
def store(studio, fake_ctx, clock, ids):
    st = studio.storage.Storage(Path(fake_ctx.data_dir) / studio.constants.DB_FILENAME, clock=clock)
    st.open()
    yield st
    st.close()


class Bridge:
    """The ``HostBridge`` slice ``RepoScheduler`` touches; the installer never talks to the host itself."""

    def attached(self) -> bool:
        return True

    def slots_for_project(self, canonical_path: str) -> list:
        return []

    def slot(self, slot_key: str):
        return None

    def is_busy(self, session_key: str):
        return False


class SettingsStub:
    def __init__(self, *, cap: int = 2, run_doctor: bool = False) -> None:
        self.cap = cap
        self.run_doctor = run_doctor

    def global_concurrency_cap(self) -> int:
        return self.cap

    def installer(self) -> dict:
        return {"run_doctor_after_install": self.run_doctor}


class FaultInjector:
    """The §4.1 ``fault`` fixture: raise ``InjectedFault`` at named step boundaries, or run an effect there."""

    def __init__(self, exc_type: type[BaseException]) -> None:
        self._exc = exc_type
        self.markers: set[str] = set()
        self.effects: dict[str, list[Any]] = {}
        self.seen: list[str] = []

    def at(self, *markers: str) -> "FaultInjector":
        self.markers.update(markers)
        return self

    def on(self, marker: str, effect: Any) -> "FaultInjector":
        """Run ``effect()`` without raising: the race between two steps that no test can open from outside."""
        self.effects.setdefault(marker, []).append(effect)
        return self

    def __call__(self, marker: str) -> None:
        self.seen.append(marker)
        for effect in self.effects.get(marker, ()):
            effect()
        if marker in self.markers:
            raise self._exc(marker)


#: One file per interesting case; ``build_payload`` adds ``MINI_CLI`` and the real payload's mcp.json.
MINI_FILES: dict[str, str] = {
    ".kiro/tools/aidlc-version.ts": 'export const AIDLC_VERSION = "2.6.2";\n',
    ".kiro/tools/aidlc-utility.ts": "// utility tool v1\n",
    ".kiro/tools/data/harness.json": '{"name": "kiro", "harnessDir": ".kiro"}\n',
    ".kiro/tools/data/stage-graph.json": '[{"slug": "workspace-scaffold"}]\n',
    SKILL: "# AI-DLC skill v1\n",
    ".kiro/agents/aidlc.json": '{"name": "aidlc", "resources": ["aidlc/spaces/default/memory"]}\n',
    "AGENTS.md": "# AI-DLC\n\nRun /aidlc to begin.\n",
    ".gitignore": "aidlc/.aidlc-turn-counter\naidlc/.aidlc-clone-id\n.aidlc-hooks-health/\n",
    "aidlc/active-space": "default\n",
    MEMORY: "# Project rules\n",
}


@functools.lru_cache(maxsize=1)
def _real_manifest_dict() -> dict:
    return json.loads((REAL_PAYLOAD / "manifest.json").read_text("utf-8"))


def _ownership(rel: str) -> str:
    """The kind the manifest builder gives ``rel``: never a local table, and never whether today's payload happens to
    ship it (2.11 ships no ``AGENTS.md`` or ``.gitignore``, but the installer must still merge and retire them)."""
    return BUILDER.classify(rel)


def mini_contents(*, engine_version: str = "2.6.2", extra: Mapping[str, str] | None = None,
                  drop: Sequence[str] = ()) -> dict[str, str]:
    """The mini payload's files, ``{relative path: text}``."""
    contents: dict[str, str] = dict(MINI_FILES)
    contents[CLI] = json.dumps(MINI_CLI, indent=2) + "\n"
    contents[MCP] = (REAL_PAYLOAD / "aidlc-kiro" / MCP).read_text("utf-8")
    contents[".kiro/tools/aidlc-version.ts"] = f'export const AIDLC_VERSION = "{engine_version}";\n'
    contents.update(extra or {})
    for rel in drop:
        contents.pop(rel, None)
    return contents


def mini_merge_targets(contents: Mapping[str, str]) -> dict[str, dict]:
    """The merge specs the builder derives for ``contents``."""
    return BUILDER.derive_merge_targets({rel: text.encode("utf-8") for rel, text in contents.items()})


def build_payload(
    root: Path,
    *,
    engine_version: str = "2.6.2",
    state_versions: Sequence[int] = (8,),
    extra: Mapping[str, str] | None = None,
    drop: Sequence[str] = (),
    merge_targets: Mapping[str, dict] | None = None,
) -> tuple[Path, Path]:
    """A miniature payload and its manifest, digested the way ``scripts/build_payload_manifest.py`` does."""
    files_dir = root / "aidlc-kiro"
    files_dir.mkdir(parents=True, exist_ok=True)
    real = _real_manifest_dict()
    contents = mini_contents(engine_version=engine_version, extra=extra, drop=drop)

    rows = []
    digest_all = hashlib.sha256()
    for rel in sorted(contents):
        path = files_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        data = contents[rel].encode("utf-8")
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        rows.append({"path": rel, "sha256": digest, "size": len(data), "ownership": _ownership(rel)})
        digest_all.update(f"{rel}\0{digest}\n".encode("utf-8"))

    # The builder's specs for exactly these files; explicit targets reach the checks verbatim.
    targets = dict(merge_targets) if merge_targets is not None else mini_merge_targets(contents)
    manifest = {
        "schema": "aidlc-studio-payload/1",
        "harness": "kiro",
        "engineVersion": engine_version,
        "source": dict(real["source"]),
        "compatibleStateVersions": list(state_versions),
        "stageCount": 1,
        "payloadDigest": digest_all.hexdigest(),
        "fileCount": len(rows),
        "mergeTargets": targets,
        "files": rows,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return (files_dir, manifest_path)


@pytest.fixture(scope="session")
def payload_v1(tmp_path_factory) -> tuple[Path, Path]:
    """Built once per session; a test that tampers with a payload copies it first (``mutable_payload``)."""
    return build_payload(tmp_path_factory.mktemp("payload-v1"))


def build_upgrade_payload(root: Path, *, change_model_defaults: bool = True) -> tuple[Path, Path]:
    """A later payload that rewrites, retires, adds and re-merges at once (and, by default, adds a model)."""
    cli = json.loads(json.dumps(MINI_CLI))
    if change_model_defaults:
        cli["chat.modelDefaults"]["claude-opus-5"] = {"output_config": {"effort": "max"}}
    return build_payload(
        root,
        engine_version="2.7.0",
        extra={
            SKILL: "# AI-DLC skill v2\n",
            ".kiro/skills/aidlc/STAGES.md": "# new in v2\n",
            CLI: json.dumps(cli, indent=2) + "\n",
            "AGENTS.md": "# AI-DLC v2\n\nRun /aidlc to begin.\n",
            ".gitignore": MINI_FILES[".gitignore"] + "aidlc/.aidlc-sessions\n",
        },
        drop=[".kiro/tools/aidlc-utility.ts"],
    )


@pytest.fixture(scope="session")
def payload_v2(tmp_path_factory) -> tuple[Path, Path]:
    return build_upgrade_payload(tmp_path_factory.mktemp("payload-v2"))


class Harness:
    """Everything one installer needs, kept together so a test can reach any of the peers."""

    def __init__(self, installer, store, activity, events, registry, fault, settings, data_dir) -> None:
        self.installer = installer
        self.store = store
        self.activity = activity
        self.events = events
        self.registry = registry
        self.fault = fault
        self.settings = settings
        self.data_dir = Path(data_dir)

    @property
    def scheduler(self) -> Any:
        return self.installer._scheduler

    def lease(self, kind: str = "admin") -> Any:
        """The live lease of this kind on any repository, or ``None``."""
        return next((rec for rec in self.scheduler.live() if rec.kind == kind), None)


@pytest.fixture
def make_harness(studio, store, clock, ids, fake_bun, fake_ctx):
    """Factory: ``make_harness(payload=(dir, manifest_path), run_doctor=False)`` → ``Harness``."""

    def factory(payload: tuple[Path, Path], *, run_doctor: bool = False) -> Harness:
        I = studio.installer
        payload_dir, manifest_path = payload
        activity = studio.activity.ActivityProjector(store, clock)
        events = studio.events.EventLog(None, clock)
        registry = studio.repo_registry.RepoRegistry(store, None, clock, ids)
        engine = studio.engine.EngineRunner(bun_path=fake_bun, clock=clock, activity=activity)
        settings = SettingsStub(run_doctor=run_doctor)
        scheduler = studio.leases.RepoScheduler(store, Bridge(), settings, clock, activity,
                                                busy_reasons=lambda view: ())
        fault = FaultInjector(I.InjectedFault)
        installer = I.Installer(
            store, registry, studio.aidlc_reader.AidlcReader(clock), engine, scheduler, activity, events,
            clock, ids, payload_dir=payload_dir.parent, manifest=I.PayloadManifest.load(manifest_path),
            data_dir=Path(fake_ctx.data_dir), settings=settings,
        )
        installer.fault_injector = fault
        return Harness(installer, store, activity, events, registry, fault, settings, Path(fake_ctx.data_dir))

    return factory


@pytest.fixture
def h(make_harness, payload_v1) -> Harness:
    return make_harness(payload_v1)


@pytest.fixture
def mutable_payload(tmp_path: Path):
    """A private copy of a session payload, for the tests that tamper with one."""

    def factory(payload: tuple[Path, Path]) -> tuple[Path, Path]:
        target = tmp_path / "payload-copy"
        shutil.copytree(payload[1].parent, target)
        return (target / "aidlc-kiro", target / "manifest.json")

    return factory


def insert_repo(studio, store, root: Path, *, repo_id: str = "r_1", **over) -> Any:
    row = {"id": repo_id, "resolved_identity": IDENTITY, "canonical_path": str(root), "label": root.name,
           "added_at": TS, "platform": "darwin", "availability": "available", **over}
    store.insert("repos", row)
    return studio.repo_registry.RepoRecord.from_row(store.get("repos", repo_id))


def reload_repo(studio, store, repo_id: str = "r_1") -> Any:
    return studio.repo_registry.RepoRecord.from_row(store.get("repos", repo_id))


@pytest.fixture
def fresh(studio, h, repo_builder):
    """``(root, repo)``: an empty git checkout registered as ``r_1``."""
    root = repo_builder.with_git().build()
    return root, insert_repo(studio, h.store, root)


def tree(root: Path) -> dict[str, str]:
    """Every regular file under ``root`` as ``relpath -> sha256`` (``.git`` skipped): the whole tree."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/") or rel == ".git":
            continue
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def run(coro):
    return asyncio.run(coro)


def do(h: Harness, repo, kind: str):
    """Preview → confirm → run, the way a route does it."""
    plan = h.installer.preview(repo, kind)
    return run(h.installer.run(repo, kind, plan_digest=plan.digest()))


def committed(h: Harness, repo, kind: str = "install") -> None:
    assert do(h, repo, kind).status == "committed"


def entry_of(plan, path: str):
    return next(e for e in plan.entries if e.path == path)


def entry(h: Harness, repo, path: str, kind: str = "install"):
    return entry_of(h.installer.preview(repo, kind), path)


def actions_by_path(plan) -> dict[str, str]:
    return {e.path: e.action for e in plan.entries}


def refused(h: Harness, repo, kind: str, plan, error: type[BaseException] = Exception) -> Any:
    """Run ``plan`` and return the ``error`` it must be refused with."""
    with pytest.raises(error) as exc:
        run(h.installer.run(repo, kind, plan_digest=plan.digest()))
    return exc.value


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")


def payload_json(rel: str) -> Any:
    return json.loads((REAL_PAYLOAD / "aidlc-kiro" / rel).read_text("utf-8"))


# ---- payload manifest and verification ---- #


def test_manifest_load_reads_the_shipped_payload(I, C):
    manifest = I.PayloadManifest.load(REAL_PAYLOAD / "manifest.json")
    assert manifest.schema == I.PAYLOAD_SCHEMA
    assert manifest.engine_version == C.BUNDLED_ENGINE_VERSION
    assert manifest.stage_count == 33
    assert 8 in manifest.compatible_state_versions
    assert len(manifest.files) == manifest.file_count
    counts = manifest.ownership_counts()
    assert sum(counts.values()) == manifest.file_count
    assert set(counts) == set(C.OWNERSHIP)
    # One target per merge file and no other, every strategy implemented: read from the manifest, not counted.
    assert set(manifest.merge_targets) == {f.path for f in manifest.files if f.ownership == "merge"}
    assert counts["merge"] == len(manifest.merge_targets)
    for spec in manifest.merge_targets.values():
        assert spec["strategy"] in I.MERGE_STRATEGIES


def test_verify_payload_accepts_the_shipped_payload(make_harness):
    h = make_harness((REAL_PAYLOAD / "aidlc-kiro", REAL_PAYLOAD / "manifest.json"))
    status = h.installer.verify_payload()
    assert status.ok, (status.mismatches, status.missing, status.extra)
    assert status.mismatches == () and status.missing == () and status.extra == ()
    assert status.file_count == h.installer.manifest.file_count


def _drop_skill_row(data: dict) -> None:
    data["files"] = [f for f in data["files"] if f["path"] != SKILL]
    data["fileCount"] = len(data["files"])


def _claim_memory_as_framework(data: dict) -> None:
    """``aidlc/**`` is the user's workspace: claiming a file there would let upgrades overwrite their rules."""
    for row in data["files"]:
        if row["path"] == MEMORY:
            row["ownership"] = "framework"


@pytest.mark.parametrize("change, edit", [
    ("tamper", None), ("missing", None), ("extra", None),
    ("manifest_digest", _drop_skill_row), ("non_shell_under_aidlc", _claim_memory_as_framework),
])
def test_verify_payload_degrades_on_a_changed_payload_or_manifest(make_harness, mutable_payload, payload_v1,
                                                                  change, edit):
    payload = mutable_payload(payload_v1)
    if change == "tamper":
        (payload[0] / SKILL).write_text("# tampered\n")
    elif change == "missing":
        (payload[0] / SKILL).unlink()
    elif change == "extra":
        (payload[0] / ".kiro/tools/unlisted.ts").write_text("// not in the manifest\n")
    else:
        data = json.loads(payload[1].read_text())
        edit(data)
        payload[1].write_text(json.dumps(data, indent=2))
    status = make_harness(payload).installer.verify_payload()
    if change == "extra":
        # Inert (only manifest rows are ever copied), so reported without degrading the app.
        assert ".kiro/tools/unlisted.ts" in status.extra
        assert status.ok is True
        return
    assert status.ok is False
    if change == "tamper":
        assert f"sha256:{SKILL}" in status.mismatches
        assert "payloadDigest" not in status.mismatches  # the manifest itself is intact
    elif change == "missing":
        assert status.missing == (SKILL,)
    elif change == "manifest_digest":
        assert "payloadDigest" in status.mismatches
    else:
        assert f"ownership_not_shell:{MEMORY}" in status.mismatches


@pytest.mark.parametrize("targets, mismatch", [
    pytest.param({
        CLI: {"strategy": "three-way-yaml"},
        MCP: {"strategy": "json-managed-keys", "managedKeys": ["mcpServers.x"]},
        "AGENTS.md": {"strategy": "append-fenced-block", "markerStart": "<!--a-->", "markerEnd": "<!--b-->"},
        ".gitignore": {"strategy": "append-missing-lines"},
    }, f"merge_strategy_unsupported:{CLI}", id="unknown-strategy"),
    pytest.param(None, "merge_target_missing:AGENTS.md", id="merge-file-without-target"),
])
def test_verify_payload_degrades_on_bad_merge_targets(make_harness, tmp_path, targets, mismatch):
    if targets is None:
        targets = {k: v for k, v in mini_merge_targets(mini_contents()).items() if k != "AGENTS.md"}
    status = make_harness(build_payload(tmp_path / "p", merge_targets=targets)).installer.verify_payload()
    assert status.ok is False
    assert mismatch in status.mismatches


@pytest.mark.parametrize("change", ["add", "remove"])
@pytest.mark.parametrize("explicit_metadata", [False, True])
def test_mini_payload_derives_model_entries_unless_metadata_is_explicit(make_harness, tmp_path, change,
                                                                        explicit_metadata):
    cached_manifest = json.loads(json.dumps(_real_manifest_dict()))
    cli = json.loads(json.dumps(MINI_CLI))
    models = cli["chat.modelDefaults"]
    if change == "add":
        models["fixture-model.1.2"] = {"output_config": {"effort": "high"}}
    else:
        models.pop(next(iter(models)))
    targets = None
    if explicit_metadata:
        targets = mini_merge_targets(mini_contents(extra={CLI: json.dumps(cli) + "\n"}))
        targets[CLI]["managedMapEntries"] = {"chat.modelDefaults": ["fixture-not-shipped.9"]}
    payload = build_payload(tmp_path / "mini", extra={CLI: json.dumps(cli) + "\n"}, merge_targets=targets)
    entries = json.loads(payload[1].read_text("utf-8"))["mergeTargets"][CLI]["managedMapEntries"]
    status = make_harness(payload).installer.verify_payload()
    if explicit_metadata:
        assert entries == {"chat.modelDefaults": ["fixture-not-shipped.9"]}
        assert not status.ok
        assert f"merge_model_entries_invalid:{CLI}" in status.mismatches
    else:
        assert entries == {"chat.modelDefaults": sorted(models)}
        assert status.ok, status
    assert _real_manifest_dict() == cached_manifest


@pytest.mark.parametrize("data", [
    pytest.param({"schema": "aidlc-studio-payload/2", "files": []}, id="foreign-schema"),
    pytest.param(None, id="absolute-path"),
])
def test_manifest_load_refuses_a_foreign_schema_or_an_absolute_path(I, tmp_path, data):
    if data is None:
        data = {**_real_manifest_dict(),
                "files": [{"path": "/etc/passwd", "sha256": "0" * 64, "size": 1, "ownership": "framework"}]}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data))
    with pytest.raises(Exception) as exc:
        I.PayloadManifest.load(path)
    assert exc.value.code == "payload_degraded"


def test_run_refuses_a_degraded_payload(studio, make_harness, mutable_payload, payload_v1, repo_builder):
    payload = mutable_payload(payload_v1)
    (payload[0] / SKILL).write_text("# tampered\n")
    h = make_harness(payload)
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    assert refused(h, repo, "install", h.installer.preview(repo, "install")).code == "payload_degraded"
    assert tree(root) == {}


def _canonical_sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def test_fragment_keys_and_digests_are_golden(make_harness, payload_v1):
    """§1.14's golden formulas, against the REAL manifest (and the mini payload for the files 2.11 stops shipping)."""
    h = make_harness((REAL_PAYLOAD / "aidlc-kiro", REAL_PAYLOAD / "manifest.json"))
    cli = h.installer._handler(CLI)
    # 2.11 ships the agent alone: the session model is chosen at Kiro CLI setup.
    assert cli.fragment_key == "cli.json:chat.defaultAgent"
    payload_cli = payload_json(CLI)
    assert cli.payload_fragment().digest == _canonical_sha({"chat.defaultAgent": payload_cli["chat.defaultAgent"]})

    mcp = h.installer._handler(MCP)
    assert mcp.fragment_key == (
        "mcp.json:mcpServers.aws-iac,mcpServers.aws-mcp,mcpServers.aws-pricing,"
        "mcpServers.aws-serverless,mcpServers.context7"
    )
    servers = payload_json(MCP)["mcpServers"]
    assert mcp.payload_fragment().digest == _canonical_sha({f"mcpServers.{n}": servers[n] for n in sorted(servers)})

    # 2.11's copy runtime leaves the project's own AGENTS.md and .gitignore out: AI-DLC adds its own marked block to
    # each when the first chat starts, so the shipped payload merges neither.
    assert not {"AGENTS.md", ".gitignore"} & set(h.installer.manifest.merge_targets)

    mini = make_harness(payload_v1)
    agents = mini.installer._handler("AGENTS.md")
    assert agents.fragment_key == "AGENTS.md:aidlc-studio:managed"
    body = (payload_v1[0] / "AGENTS.md").read_text("utf-8").strip("\n")
    assert agents.payload_fragment().digest == hashlib.sha256(body.encode("utf-8")).hexdigest()

    ignore = mini.installer._handler(".gitignore")
    assert ignore.fragment_key == ".gitignore:lines"
    lines = [line for line in (payload_v1[0] / ".gitignore").read_text("utf-8").splitlines() if line.strip()]
    assert ignore.payload_fragment().digest == hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# ---- the preview matrix ---- #


def test_preview_first_install_creates_everything(studio, h, fresh):
    plan = h.installer.preview(fresh[1], "install")
    actions = actions_by_path(plan)
    assert actions[".kiro/tools/aidlc-version.ts"] == "create"
    assert actions[".kiro/agents/aidlc.json"] == "create"
    assert actions["aidlc/active-space"] == "shell_create"
    assert actions[CLI] == "merge_create"
    assert actions["AGENTS.md"] == "merge_create"
    assert actions[".gitignore"] == "merge_create"
    assert actions[".kiro/tools/data/harness.json"] == "merge_create"
    assert plan.blocking is False and plan.blockers == ()
    assert plan.engine_from is None and plan.engine_to == "2.6.2"
    assert plan.newer_installed is False and plan.same_version is False
    assert plan.requires_admin_lease is True
    assert plan.bytes_to_write == sum(f.size for f in h.installer.manifest.files)
    assert plan.counts["create"] == 5 and plan.counts["shell_create"] == 2
    assert plan.counts["merge_create"] == 5
    assert set(plan.counts) == set(studio.installer.PreviewAction)


def test_preview_adopts_byte_identical_files(h, fresh, payload_v1):
    root, repo = fresh
    (root / SKILL).parent.mkdir(parents=True, exist_ok=True)
    (root / SKILL).write_bytes((payload_v1[0] / SKILL).read_bytes())
    adopted = entry(h, repo, SKILL)
    assert adopted.action == "identical" and adopted.blocking is False
    assert adopted.live_sha256 == adopted.payload_sha256
    assert do(h, repo, "install").status == "committed"
    flags = {f.path: f.adopted for f in h.installer.current_receipt("r_1").files}
    assert flags[SKILL] is True
    assert flags[".kiro/tools/aidlc-version.ts"] is False


def test_preview_unowned_differing_file_blocks_with_a_diff_and_no_force(h, fresh):
    root, repo = fresh
    (root / SKILL).parent.mkdir(parents=True, exist_ok=True)
    (root / SKILL).write_text("# my own notes\n")
    plan = h.installer.preview(repo, "install")
    conflict = entry_of(plan, SKILL)
    assert conflict.action == "conflict" and conflict.blocking is True
    assert plan.blocking is True and plan.blockers == (conflict,)
    assert conflict.diff and "-# my own notes" in conflict.diff and "+# AI-DLC skill v1" in conflict.diff
    before = tree(root)
    error = refused(h, repo, "install", plan)
    assert error.code == "install_conflict"
    assert [e["path"] for e in error.details["blockers"]] == [SKILL]
    assert tree(root) == before
    # There is no force path in v1 (FR-INST-004): no public entry point takes one.
    for name in ("run", "begin", "preview"):
        params = set(inspect.signature(getattr(h.installer, name)).parameters)
        assert not {p for p in params if "force" in p or "overwrite" in p}


def test_preview_owned_identical_and_owned_modified(studio, h, fresh, payload_v2, make_harness):
    root, repo = fresh
    committed(h, repo)
    up = make_harness(payload_v2)
    repo = reload_repo(studio, h.store)
    plan = up.installer.preview(repo, "upgrade")
    assert actions_by_path(plan)[SKILL] == "owned_identical"  # untouched since the receipt: ours to replace
    assert plan.blocking is False
    (root / SKILL).write_text("# hand-edited\n")  # an edited framework file is blocked, never overwritten
    plan = up.installer.preview(repo, "upgrade")
    edited = entry_of(plan, SKILL)
    assert edited.action == "owned_modified" and edited.blocking is True and plan.blocking is True
    assert edited.receipt_sha256 and edited.receipt_sha256 != edited.live_sha256


def test_preview_framework_mutable_drift_is_engine_modified_and_not_blocking(studio, h, fresh, payload_v2,
                                                                             make_harness):
    root, repo = fresh
    committed(h, repo)
    # `aidlc-utility.ts space <name>` re-points `resources` in every agent file at runtime.
    (root / ".kiro/agents/aidlc.json").write_text('{"name": "aidlc", "resources": ["aidlc/spaces/other/memory"]}\n')
    plan = make_harness(payload_v2).installer.preview(reload_repo(studio, h.store), "upgrade")
    drift = entry_of(plan, ".kiro/agents/aidlc.json")
    assert drift.action == "engine_modified" and drift.blocking is False
    assert plan.blocking is False
    assert drift.diff and "aidlc/spaces/other/memory" in drift.diff


def test_preview_shell_files_are_never_owned_or_overwritten(h, fresh):
    root, repo = fresh
    (root / MEMORY).parent.mkdir(parents=True)
    (root / MEMORY).write_text("# our team rules\n")
    shell = entry(h, repo, MEMORY)
    assert shell.action == "shell_exists" and shell.blocking is False
    committed(h, repo)
    assert (root / MEMORY).read_text() == "# our team rules\n"
    assert not [f for f in h.installer.current_receipt("r_1").files if f.ownership == "shell"]


# ---- merge targets ---- #


def test_preview_cli_json_user_keys_survive_and_matching_fragment_is_identical(h, fresh):
    root, repo = fresh
    shipped = MINI_CLI
    write_json(root / CLI, {"toolSearch.enabled": True, "chat.defaultAgent": shipped["chat.defaultAgent"],
                            "chat.modelDefaults": shipped["chat.modelDefaults"], "toolSearch.minTokens": 50000})
    # The managed fragment already matches: nothing is written and the file is not reformatted.
    cli = entry(h, repo, CLI)
    assert cli.action == "merge_identical" and cli.blocking is False
    before = (root / CLI).read_bytes()
    committed(h, repo)
    assert (root / CLI).read_bytes() == before


def test_preview_cli_json_missing_managed_key_is_an_update(h, fresh):
    root, repo = fresh
    write_json(root / CLI, {"toolSearch.enabled": True})
    cli = entry(h, repo, CLI)
    assert cli.action == "merge_update" and cli.blocking is False
    committed(h, repo)
    after = json.loads((root / CLI).read_text())
    assert after["toolSearch.enabled"] is True
    assert after["chat.defaultAgent"] == "aidlc"
    assert list(after)[0] == "toolSearch.enabled"  # the user's key stays first, ours are appended


def test_preview_cli_json_conflicting_managed_key_blocks(h, fresh):
    root, repo = fresh
    write_json(root / CLI, {"chat.defaultAgent": "my-own-agent"})
    plan = h.installer.preview(repo, "install")
    cli = entry_of(plan, CLI)
    assert cli.action == "merge_conflict" and cli.blocking is True and plan.blocking is True
    assert cli.diff and "my-own-agent" in cli.diff
    assert cli.fragment_key == "cli.json:chat.defaultAgent,chat.modelDefaults"


def test_preview_mcp_json_user_server_survives_with_its_token(h, fresh):
    root, repo = fresh
    mine = {"command": "npx", "args": ["-y", "internal"], "env": {"TOKEN": "sk-live-abc123"}}
    write_json(root / MCP, {"mcpServers": {"my-internal": mine}, "unknownTopLevel": {"keep": "me"}})
    mcp = entry(h, repo, MCP)
    assert mcp.action == "merge_update" and mcp.blocking is False
    committed(h, repo)
    after = json.loads((root / MCP).read_text())
    assert after["mcpServers"]["my-internal"] == mine
    assert after["unknownTopLevel"] == {"keep": "me"}
    assert "sk-live-abc123" in (root / MCP).read_text()
    assert after["mcpServers"]["context7"] == payload_json(MCP)["mcpServers"]["context7"]
    assert list(after["mcpServers"])[0] == "my-internal"


def test_preview_mcp_json_conflicting_managed_subtree_blocks(h, fresh):
    root, repo = fresh
    write_json(root / MCP, {"mcpServers": {"context7": {"type": "http", "url": "https://my-mirror.example/mcp"}}})
    mcp = entry(h, repo, MCP)
    assert mcp.action == "merge_conflict" and mcp.blocking is True
    assert mcp.diff and "my-mirror.example" in mcp.diff


def test_preview_agents_md_absent_block_updates_then_edited_block_conflicts(studio, h, fresh):
    root, repo = fresh
    user_text = "# My project\n\nOur own onboarding notes.\n"
    (root / "AGENTS.md").write_text(user_text)
    agents = entry(h, repo, "AGENTS.md")  # no markers at all: appending takes nothing away
    assert agents.action == "merge_update" and agents.blocking is False
    committed(h, repo)
    after = (root / "AGENTS.md").read_text()
    assert after.startswith(user_text)
    assert "<!-- aidlc-studio:managed:start -->" in after
    assert "Run /aidlc to begin." in after
    # A hand-edited managed block is the user's: it blocks instead of being replaced.
    (root / "AGENTS.md").write_text(after.replace("Run /aidlc to begin.", "Run /aidlc AND make me a coffee."))
    agents = entry(h, reload_repo(studio, h.store), "AGENTS.md", "recovery")
    assert agents.action == "merge_conflict" and agents.blocking is True


def test_preview_gitignore_appends_missing_lines_and_never_conflicts(studio, h, fresh):
    root, repo = fresh
    user_lines = "node_modules\n*.log\naidlc/.aidlc-clone-id\n"
    (root / ".gitignore").write_text(user_lines)
    ignore = entry(h, repo, ".gitignore")
    assert ignore.action == "merge_update" and ignore.blocking is False
    committed(h, repo)
    after = (root / ".gitignore").read_text()
    assert after.startswith(user_lines)
    assert after.count("aidlc/.aidlc-clone-id") == 1  # already present, not duplicated
    assert "aidlc/.aidlc-turn-counter" in after and ".aidlc-hooks-health/" in after
    # Even with every AI-DLC line removed again it is an update: appending cannot destroy anything.
    (root / ".gitignore").write_text("node_modules\n")
    ignore = entry(h, reload_repo(studio, h.store), ".gitignore", "recovery")
    assert ignore.action == "merge_update" and ignore.blocking is False


# ---- versions, retirement, state versions ---- #


def test_newer_installed_and_same_version(studio, h, fresh, payload_v2, make_harness):
    up = make_harness(payload_v2)
    committed(up, fresh[1])
    repo = reload_repo(studio, h.store)
    plan = h.installer.preview(repo, "upgrade")  # the 2.6.2 payload against a 2.7.0 install
    assert plan.newer_installed is True and plan.same_version is False
    assert refused(h, repo, "upgrade", plan).code == "newer_installed"
    same = up.installer.preview(repo, "upgrade")  # nothing to do: the route answers `same_version_installed`
    assert same.same_version is True and same.newer_installed is False
    assert same.bytes_to_write == 0 and same.blocking is False


def test_a_foreign_harness_is_not_studios_install_so_install_is_still_offered(studio, h, fresh):
    """AI-DLC in ``.claude`` at the payload's version must not lock both lanes (FR-INST-003): the lane keys on
    ``own_engine_version`` while ``status``/``engine_dir`` keep describing the harness dispatch resolves through."""
    root, repo = fresh
    version_ts = 'export const AIDLC_VERSION = "2.6.2";\n'  # the payload's own version
    foreign = root / ".claude/tools/aidlc-version.ts"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text(version_ts)
    health = h.registry.detect_install(repo)
    assert health.status == "installed" and health.engine_dir == ".claude"
    assert health.engine_version == "2.6.2" and health.own_engine_version is None

    plan = h.installer.preview(repo, "install")
    assert plan.engine_from is None
    assert plan.same_version is False and plan.newer_installed is False
    assert plan.blocking is False and plan.blockers == ()
    assert {e.action for e in plan.entries} == {"create", "shell_create", "merge_create"}
    assert not any(e.path.startswith(".claude/") for e in plan.entries)
    assert h.installer.preview(repo, "upgrade").engine_from is None  # what the route's `not_installed` reads
    committed(h, repo)
    assert (root / ".kiro/tools/aidlc-version.ts").read_text() == version_ts
    assert foreign.read_text() == version_ts  # no entry named the stranger's harness
    assert not any(f.path.startswith(".claude/") for f in h.installer.current_receipt("r_1").files)
    reinstalled = h.installer.preview(reload_repo(studio, h.store), "upgrade")  # Studio's own refusals are back
    assert reinstalled.engine_from == "2.6.2" and reinstalled.same_version is True


@pytest.mark.parametrize("edited", [False, True])
def test_upgrade_retires_dropped_files_but_never_an_edited_one(studio, h, fresh, payload_v2, make_harness, edited):
    root, repo = fresh
    committed(h, repo)
    utility = root / ".kiro/tools/aidlc-utility.ts"
    if edited:
        utility.write_text("// I patched this myself\n")
    up = make_harness(payload_v2)
    repo = reload_repo(studio, h.store)
    plan = up.installer.preview(repo, "upgrade")
    retired = entry_of(plan, ".kiro/tools/aidlc-utility.ts")
    assert retired.action == ("retire_blocked" if edited else "retire") and retired.blocking is False
    assert plan.blocking is False
    committed(up, repo, "upgrade")
    if edited:
        assert utility.read_text() == "// I patched this myself\n"
    else:
        assert not utility.exists()
        assert (root / ".kiro/skills/aidlc/STAGES.md").read_text() == "# new in v2\n"
        assert ".kiro/tools/aidlc-utility.ts" not in {f.path for f in up.installer.current_receipt("r_1").files}


def test_state_version_7_refuses_upgrade_and_recovery(studio, h, repo_builder):
    root = (repo_builder.with_engine("payload230").with_workspace()
            .with_intent("260901-ledger-export", state=F.state_path(F.PAYLOAD_230, "260901-ledger-export"))
            .with_git().build())
    repo = insert_repo(studio, h.store, root)
    for kind in ("upgrade", "recovery", "install"):
        plan = h.installer.preview(repo, kind)
        assert plan.state_versions_found == (7,), (kind, plan.state_versions_found)
        assert plan.state_version_blocked is True
        assert "state_version_unsupported" in {w.code for w in plan.warnings}
        error = refused(h, repo, kind, plan)
        assert error.code == "state_version_migration_unconfirmed"
        assert error.details["state_versions_found"] == [7]
        assert error.details["compatible"] == [8]


@pytest.mark.parametrize("unreadable", [False, True])
def test_state_version_8_is_not_blocked_but_an_unreadable_state_is(studio, h, repo_builder, C, unreadable):
    root = (repo_builder.with_workspace().with_intent("260828-kiro-impact-poc", state=F.real_state())
            .with_git().build())
    state = "aidlc/spaces/default/intents/260828-kiro-impact-poc/aidlc-state.md"
    if unreadable:
        (root / state).write_text("x" * (C.MAX_STATE_BYTES + 1))
    repo = insert_repo(studio, h.store, root)
    if not unreadable:
        plan = h.installer.preview(repo, "install")
        assert plan.state_versions_found == (8,)
        assert plan.state_version_blocked is False
        return
    plan = h.installer.preview(repo, "upgrade")
    assert plan.state_version_blocked is True
    assert plan.state_versions_unreadable == (state,)
    assert "state_unreadable" in {w.code for w in plan.warnings}
    error = refused(h, repo, "upgrade", plan)
    assert error.code == "state_version_migration_unconfirmed"
    assert error.details["unreadable"] == list(plan.state_versions_unreadable)


def test_plan_digest_is_stable_and_moves_with_the_repository(h, fresh):
    root, repo = fresh
    first = h.installer.preview(repo, "install")
    assert first.digest() == h.installer.preview(repo, "install").digest()
    (root / SKILL).parent.mkdir(parents=True)
    (root / SKILL).write_text("# mine\n")
    assert h.installer.preview(repo, "install").digest() != first.digest()
    error = refused(h, repo, "install", first)
    assert error.code == "install_conflict"
    assert error.details["reason"] == "preview_changed"


# ---- the happy path ---- #


def test_install_writes_exactly_the_managed_set_and_nothing_else(h, fresh):
    root, repo = fresh
    (root / "src").mkdir()
    (root / "src/app.py").write_text("print('hello')\n")
    (root / "README.md").write_text("# my project\n")
    before = tree(root)
    committed(h, repo)
    after = tree(root)
    assert {"src/app.py", "README.md"} <= set(after)
    for rel in ("src/app.py", "README.md"):
        assert after[rel] == before[rel]
    assert set(after) - set(before) == {f.path for f in h.installer.manifest.files}
    for pf in h.installer.manifest.files:
        if pf.ownership != "merge":  # merged files are not copied byte-for-byte
            assert after[pf.path] == pf.sha256, pf.path
    assert not [rel for rel in after if ".aidlc-studio.tmp" in rel]


def test_install_commits_the_receipt_last_and_updates_the_repo_row(studio, h, fresh):
    result = do(h, fresh[1], "install")
    assert result.status == "committed"
    names = [s.name for s in result.steps]
    assert names == list(studio.installer.STEPS)
    assert all(s.ok for s in result.steps)
    assert names.index("commit_receipt") > names.index("post_write_validate")
    receipt = h.installer.current_receipt("r_1")
    assert receipt is not None and receipt.status == "current"
    assert receipt.engine_version == "2.6.2"
    assert receipt.payload_digest == h.installer.manifest.payload_digest
    assert receipt.transaction_id == result.transaction_id
    assert receipt.prior_receipt_id is None
    assert {f.kind for f in receipt.files} == {"file", "fragment"}
    assert {f.path for f in receipt.files if f.kind == "fragment"} == set(h.installer.manifest.merge_targets)
    row = h.store.get("repos", "r_1")
    assert row["install_status"] == "installed"
    assert row["installed_engine_version"] == "2.6.2"
    assert row["engine_dir"] == ".kiro"
    assert row["receipt_version"] == receipt.receipt_id
    assert result.receipt_id == receipt.receipt_id
    assert result.repo_install_status == "installed"


def test_install_records_activity_and_events(studio, h, fresh):
    result = do(h, fresh[1], "install")
    rows, _ = h.store.activity_query(studio.storage.ActivityQuery(repo_id="r_1", limit=200))
    kinds = [row.kind for row in rows]
    assert "install.transaction" in kinds
    assert "lease.acquired" in kinds and "lease.released" in kinds
    assert not [row for row in rows if row.source == "aidlc"]
    records = run(h.events.since(0, limit=200))
    statuses = [e.payload["status"] for e in records if e.type == "transaction.updated"]
    assert {"transaction.updated", "repo.updated"} <= {e.type for e in records}
    assert statuses[-1] == "committed"
    assert "committed" in statuses and "leased" in statuses
    assert all(e.payload["transaction_id"] == result.transaction_id
               for e in records if e.type == "transaction.updated")


@pytest.mark.parametrize("run_doctor", [False, True])
def test_post_write_validation_runs_version_and_doctor_only_when_opted_in(studio, make_harness, payload_v1,
                                                                          repo_builder, denied_log, run_doctor):
    h = make_harness(payload_v1, run_doctor=run_doctor)
    committed(h, insert_repo(studio, h.store, repo_builder.with_git().build()))
    rows, _ = h.store.activity_query(studio.storage.ActivityQuery(kind="engine.run", limit=50))
    verbs = [row.params.get("verb_key") for row in rows]
    assert "utility.version" in verbs
    assert "state.lookup" in verbs
    assert ("utility.doctor" in verbs) is run_doctor
    assert not denied_log.exists() or denied_log.read_text() == ""


def test_post_write_validation_refuses_a_version_mismatch(studio, make_harness, tmp_path, repo_builder):
    """The engine on disk must report the payload's version, or the receipt is never written."""
    payload = build_payload(tmp_path / "p-lying", engine_version="2.6.2")
    (payload[0] / ".kiro/tools/aidlc-version.ts").write_text('export const AIDLC_VERSION = "9.9.9";\n')
    manifest = json.loads(payload[1].read_text())
    for row in manifest["files"]:
        if row["path"] == ".kiro/tools/aidlc-version.ts":
            data = (payload[0] / row["path"]).read_bytes()
            row["sha256"] = hashlib.sha256(data).hexdigest()
            row["size"] = len(data)
    digest = hashlib.sha256()
    for row in sorted(manifest["files"], key=lambda r: r["path"]):
        digest.update(f"{row['path']}\0{row['sha256']}\n".encode("utf-8"))
    manifest["payloadDigest"] = digest.hexdigest()
    payload[1].write_text(json.dumps(manifest, indent=2))
    h = make_harness(payload)
    root = repo_builder.with_git().build()
    assert do(h, insert_repo(studio, h.store, root), "install").status == "rolled_back"
    assert h.installer.current_receipt("r_1") is None
    assert tree(root) == {}


def test_upgrade_supersedes_the_prior_receipt(studio, h, fresh, payload_v2, make_harness):
    root, repo = fresh
    first = do(h, repo, "install")
    old_receipt = h.installer.current_receipt("r_1")
    up = make_harness(payload_v2)
    second = do(up, reload_repo(studio, h.store), "upgrade")
    assert second.status == "committed"
    new_receipt = up.installer.current_receipt("r_1")
    assert new_receipt.receipt_id != old_receipt.receipt_id
    assert new_receipt.prior_receipt_id == old_receipt.receipt_id
    assert up.installer.receipt(old_receipt.receipt_id).status == "superseded"
    assert h.store.get("repos", "r_1")["installed_engine_version"] == "2.7.0"
    assert (root / SKILL).read_text() == "# AI-DLC skill v2\n"
    assert [t.transaction_id for t in up.installer.transactions("r_1")] == [
        second.transaction_id, first.transaction_id]


def test_recovery_clears_the_recovery_required_flag(studio, h, fresh):
    root, repo = fresh
    committed(h, repo)
    (root / SKILL).unlink()  # a framework file lost its bytes and the repo was marked for recovery
    h.store.update("repos", "r_1", {"install_status": "recovery_required"})
    repo = reload_repo(studio, h.store)
    plan = h.installer.preview(repo, "recovery")
    assert actions_by_path(plan)[SKILL] == "create"
    assert run(h.installer.run(repo, "recovery", plan_digest=plan.digest())).status == "committed"
    assert h.store.get("repos", "r_1")["install_status"] == "installed"
    assert (root / SKILL).read_text() == "# AI-DLC skill v1\n"


def test_drift_reports_owned_files_and_fragments(h, fresh):
    root, repo = fresh
    committed(h, repo)
    receipt = h.installer.current_receipt("r_1")
    assert h.installer.drift(repo, receipt) == ()
    (root / SKILL).write_text("# drifted\n")
    (root / ".kiro/agents/aidlc.json").write_text('{"name": "aidlc", "resources": []}\n')
    write_json(root / CLI, {**json.loads((root / CLI).read_text()), "chat.defaultAgent": "something-else"})
    entries = {e.path: e.action for e in h.installer.drift(repo, receipt)}
    assert entries[SKILL] == "owned_modified"
    assert entries[".kiro/agents/aidlc.json"] == "engine_modified"
    assert entries[CLI] == "merge_conflict"
    assert "aidlc/active-space" not in entries  # shell is never owned


def test_transaction_lookup_and_not_found(h, fresh):
    result = do(h, fresh[1], "install")
    assert h.installer.transaction(result.transaction_id).to_json() == result.to_json()
    with pytest.raises(Exception) as exc:
        h.installer.transaction("tx_missing")
    assert exc.value.code == "transaction_not_found"


def test_begin_creates_the_row_before_the_run(h, fresh):
    repo = fresh[1]
    plan = h.installer.preview(repo, "install")
    opened = run(h.installer.begin(repo, "install", plan_digest=plan.digest()))
    assert opened.status == "staged"
    assert h.installer.transaction(opened.transaction_id).status == "staged"
    result = run(h.installer.run(repo, "install", plan_digest=plan.digest(), transaction_id=opened.transaction_id))
    assert result.transaction_id == opened.transaction_id
    assert result.status == "committed"
    assert len(h.installer.transactions("r_1")) == 1


def test_repo_busy_fails_without_touching_the_repository(h, fresh):
    root, repo = fresh
    grant = run(h.scheduler.acquire_execution(repo, intent_uuid=None, session_key="dashboard:x", action_id="a_1"))
    plan = h.installer.preview(repo, "install")
    # `begin` refuses synchronously, so the route answers 409 instead of a 202 that fails later.
    with pytest.raises(Exception) as exc:
        run(h.installer.begin(repo, "install", plan_digest=plan.digest()))
    assert exc.value.code == "repo_busy"
    assert h.installer.transactions("r_1") == []
    # A lease taken after the 202 is caught by the transaction itself: recorded, nothing written.
    result = run(h.installer.run(repo, "install", plan_digest=plan.digest()))
    assert result.status == "failed"
    assert result.error == "repo_busy"
    assert tree(root) == {}
    assert h.installer.current_receipt("r_1") is None
    assert h.store.get("repos", "r_1")["install_status"] == "not_installed"
    run(h.scheduler.release(grant))


# ---- the fault matrix: the property that matters ---- #


FAULT_MARKERS = [f"{when}:{step}" for step in (
    "stage_payload", "verify_staging", "acquire_admin_lease", "backup", "write_files",
    "merge_fragments", "post_write_validate", "commit_receipt", "release_lease",
) for when in ("before", "after")]


@pytest.fixture
def upgrade_scene(studio, make_harness, payload_v1, payload_v2, repo_builder):
    """A complete v1 install holding the user's own files (inside every merge target too), and a v2 harness."""
    root = repo_builder.with_git().build()
    (root / "src").mkdir()
    (root / "src/app.py").write_text("print('hello')\n")
    (root / "AGENTS.md").write_text("# My project\n\nOur onboarding notes.\n")
    (root / ".gitignore").write_text("node_modules\n*.log\n")
    write_json(root / MCP, {"mcpServers": {"mine": {"command": "npx", "env": {"TOKEN": "sk-live-xyz"}}}, "extra": 1})
    first = make_harness(payload_v1)
    committed(first, insert_repo(studio, first.store, root))
    return {
        "root": root,
        "baseline": tree(root),
        "receipt_id": first.installer.current_receipt("r_1").receipt_id,
        "h": make_harness(payload_v2),
        "repo": reload_repo(studio, first.store),
    }


@pytest.mark.parametrize("marker", FAULT_MARKERS)
def test_fault_at_every_step_leaves_one_complete_installation(studio, upgrade_scene, marker, I):
    h: Harness = upgrade_scene["h"]
    repo = upgrade_scene["repo"]
    h.fault.at(marker)
    result = run(h.installer.run(repo, "upgrade", plan_digest=h.installer.preview(repo, "upgrade").digest()))
    assert marker in h.fault.seen, "the fault point was never reached"

    # The lease is handed back, or the fault fired at the release itself after the transaction settled: a lease
    # held on a transaction that never settles leaves the repository `repo_busy` until a restart.
    held = h.lease()
    if held is not None:
        status = h.store.get("install_transactions", held.transaction_id)["status"]
        assert status in studio.leases.TRANSACTION_SETTLED_STATUS, (
            f"{marker} left the admin lease held on an unsettled transaction ({status})"
        )
    row = h.store.get("repos", "r_1")
    current = h.installer.current_receipt("r_1")
    after = tree(upgrade_scene["root"])
    if marker == "after:commit_receipt":
        assert result.status == "rolled_back"  # the inserted receipt is retracted by the rollback
    if result.status == "committed":
        # Only a fault around `release_lease` can leave a committed transaction: the receipt is already durable.
        assert marker in ("before:release_lease", "after:release_lease")
        assert current.engine_version == "2.7.0"
        return
    assert result.status in ("failed", "rolled_back", "recovery_required"), result.status
    if result.status == "recovery_required":
        assert row["install_status"] == "recovery_required"
        assert result.failed_dir
        return
    # The only other acceptable world: the previous installation, complete, with its receipt current.
    baseline = upgrade_scene["baseline"]
    assert after == baseline, sorted(set(after) ^ set(baseline)) or "content differs"
    assert current is not None and current.receipt_id == upgrade_scene["receipt_id"]
    assert current.status == "current"
    assert row["install_status"] == "installed"
    assert row["installed_engine_version"] == "2.6.2"
    assert row["receipt_version"] == upgrade_scene["receipt_id"]
    assert h.installer.drift(repo, current) == ()
    assert result.error


def test_fault_before_the_first_step_touches_nothing(h, fresh):
    root, repo = fresh
    (root / "README.md").write_text("# mine\n")
    before = tree(root)
    h.fault.at("before:stage_payload")
    result = do(h, repo, "install")
    assert result.status == "failed"
    assert tree(root) == before
    assert h.installer.current_receipt("r_1") is None
    assert result.failed_dir and Path(result.failed_dir).is_dir()


class _Clock:
    def now(self) -> float:
        return 1788000000.0

    def iso(self) -> str:
        return TS

    def monotonic(self) -> float:
        return 1000.0


def test_rollback_failure_marks_recovery_required_with_a_finding_and_a_card(studio, upgrade_scene):
    h: Harness = upgrade_scene["h"]
    repo = upgrade_scene["repo"]
    h.fault.at("after:write_files", "before:reverify_old_receipt")  # fail the write, then the rollback's proof
    result = run(h.installer.run(repo, "upgrade", plan_digest=h.installer.preview(repo, "upgrade").digest()))
    assert result.status == "recovery_required"
    assert result.failed_dir and Path(result.failed_dir).is_dir()
    assert (Path(result.failed_dir) / "transaction.json").is_file()
    assert h.store.get("repos", "r_1")["install_status"] == "recovery_required"
    assert result.repo_install_status == "recovery_required"

    repo = reload_repo(studio, h.store)
    health = h.registry.detect_install(repo, h.installer.current_receipt("r_1"))
    assert health.status == "recovery_required"
    consistency = studio.consistency.ConsistencyEngine()  # a blocking finding (§1.7) …
    findings = consistency.evaluate_repo(studio.consistency.RepoFacts(now=TS, repo=repo, install=health))
    assert {f.code: f.severity for f in findings}.get("install_recovery_required") == "blocking"
    assert "install_recovery_required" in {f.code for f in consistency.blocking(findings)}
    # … and an `install_conflict` card seed (§1.8), derived the way the reconciler does for a repo with no intents.
    clock = _Clock()
    projection = studio.projection.Projection(studio.aidlc_reader.AidlcReader(clock), consistency, clock)
    seeds = projection.derive_cards(projection.snapshot(repo, "default", "260904-none"), findings=[], binding=None,
                                    install=health, breakers=[], live_actions=[])
    assert [s.type for s in seeds if s.type == "install_conflict"] == ["install_conflict"]


def test_failed_transaction_keeps_diagnostics_without_an_identical_payload_copy(h, fresh):
    h.fault.at("before:write_files")
    result = do(h, fresh[1], "install")
    assert result.status == "rolled_back"
    failed = Path(result.failed_dir)
    assert not (failed / "staging").exists()
    log = json.loads((failed / "transaction.json").read_text())
    assert log["transaction_id"] == result.transaction_id
    assert log["candidate"]["status"] == "identical_to_payload"
    assert log["candidate"]["payload_digest"] == h.installer._manifest.payload_digest
    assert [s["name"] for s in log["steps"]][:3] == ["stage_payload", "verify_staging", "acquire_admin_lease"]
    assert not (h.data_dir / "staging" / result.transaction_id).exists()  # no duplicate under staging either


def test_lease_is_always_released(h, fresh):
    h.fault.at("after:merge_fragments")
    result = do(h, fresh[1], "install")
    assert result.status == "rolled_back"
    assert h.lease() is None
    assert [s.name for s in result.steps][-1] == "release_lease"


def test_committed_transaction_cleans_up_staging_but_keeps_backups(h, fresh):
    result = do(h, fresh[1], "install")
    assert not (h.data_dir / "staging" / result.transaction_id).exists()
    assert (h.data_dir / "backups" / result.transaction_id).is_dir()


# ---- a transaction abandoned by process death ---- #


def _abandoned(store, status: str, *, txid: str = "tx_dead") -> None:
    """The durable remains of an install killed mid-flight: a row in a non-terminal status, its lock gone."""
    store.insert("install_transactions", {
        "transaction_id": txid, "repo_id": "r_1", "resolved_repo_identity": IDENTITY, "kind": "install",
        "status": status, "studio_version": "1.0.0", "engine_version": "2.6.2", "payload_digest": "d",
        "started_at": "2026-09-04T09:00:00Z", "steps_json": "[]",
    })


@pytest.mark.parametrize("status, outcome, install_status", [
    ("staged", "failed", "not_installed"),
    ("leased", "failed", "not_installed"),
    ("backed_up", "recovery_required", "recovery_required"),
    ("written", "recovery_required", "recovery_required"),
    ("merged", "recovery_required", "recovery_required"),
    ("validated", "recovery_required", "recovery_required"),
    ("rolling_back", "recovery_required", "recovery_required"),
])
def test_a_transaction_abandoned_by_process_death_is_settled_by_how_far_it_got(studio, h, repo_builder, status,
                                                                               outcome, install_status):
    """Nothing is written before ``backup``, so earlier rows settle ``failed``; later ones ``recovery_required``."""
    repo = insert_repo(studio, h.store, repo_builder.with_git().build(), install_status="not_installed")
    _abandoned(h.store, status)
    assert run(h.installer.settle_abandoned()) == ["tx_dead"]
    row = h.store.get("install_transactions", "tx_dead")
    assert row["status"] == outcome
    assert row["error"] == "process_exited"
    assert row["finished_at"]
    assert h.store.get("repos", "r_1")["install_status"] == install_status
    # Nothing is held: the dead process took its lock with it, so the recovery can start at once.
    assert h.lease() is None
    run(h.scheduler.release(run(h.scheduler.acquire_admin(repo, operation_type="recovery", transaction_id=None))))


def test_a_live_transaction_of_this_process_is_never_settled(h, fresh):
    """Startup is not proof of idle: a transaction whose admin lease THIS process holds is still running."""
    _abandoned(h.store, "written")
    grant = run(h.scheduler.acquire_admin(fresh[1], operation_type="install", transaction_id="tx_dead"))
    assert run(h.installer.settle_abandoned()) == []
    assert h.store.get("install_transactions", "tx_dead")["status"] == "written"
    assert h.store.get("repos", "r_1")["install_status"] != "recovery_required"
    run(h.scheduler.release(grant))


def test_a_settled_transaction_is_left_alone(h, fresh):
    """Idempotent: a second startup does not rewrite a transaction that already reached an end."""
    _abandoned(h.store, "committed")
    h.store.update("install_transactions", "tx_dead", {"finished_at": TS})
    assert run(h.installer.settle_abandoned()) == []
    assert h.store.get("install_transactions", "tx_dead")["status"] == "committed"


def test_the_settled_status_set_agrees_with_the_lease_module(studio):
    """Two modules name the same set from opposite sides; a drift would strand a lease again."""
    assert studio.installer._SETTLED_STATUS == studio.leases.TRANSACTION_SETTLED_STATUS
    assert studio.installer._SETTLED_STATUS <= set(studio.installer.TransactionStatus)
    assert studio.installer._ABANDONED_WROTE_NOTHING <= set(studio.installer.TransactionStatus)


def test_a_failure_between_the_acquire_and_its_bookkeeping_hands_the_lease_back_at_once(h, fresh):
    """``run``'s ``finally`` cannot release a grant it was never handed, so ``_lease_step`` must do it."""
    root, repo = fresh
    h.fault.at("after:acquire_admin_lease")
    assert do(h, repo, "install").status == "failed"
    assert h.lease() is None, "the lease needed a restart to come back"
    assert tree(root) == {}, "nothing was written, so nothing should have to be recovered"


def test_a_failed_atomic_write_leaves_no_temp_file_in_the_repository(tmp_path, I):
    """A failure at fsync (the temp file certainly exists then) must not leave ``.<name>.aidlc-studio.tmp``."""
    target = tmp_path / "nested" / "AGENTS.md"

    def boom(_fd: int) -> None:
        raise OSError("disk full")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(I.os, "fsync", boom)
        with pytest.raises(OSError):
            I._atomic_write(target, b"x")
    assert not (target.parent / f".{target.name}.aidlc-studio.tmp").exists(), "the temp file survived"
    assert not target.exists()


# ---- a file Studio cannot read is not a file that is not there ---- #


@pytest.fixture
def unreadable_file():
    """Make a path unreadable for the test, restoring it whatever happens (pytest cannot clean up a 0000 tree)."""
    restore: list[tuple[Path, int]] = []

    def make(path: Path, body: str = "the user's own words\n") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        restore.append((path, path.stat().st_mode))
        path.chmod(0o000)
        return path

    yield make
    for path, mode in restore:
        with contextlib.suppress(OSError):
            path.chmod(mode)


@pytest.mark.parametrize("rel, expected", [
    (SKILL, "conflict"),                                # framework
    (".kiro/tools/aidlc-utility.ts", "conflict"),       # framework: `_backup` is the last reader before a write
    (".kiro/agents/aidlc.json", "conflict"),            # framework-mutable
    ("AGENTS.md", "merge_conflict"),                    # merge target
    ("aidlc/active-space", "shell_exists"),             # shell: left alone either way
])
def test_a_managed_path_studio_cannot_read_is_never_previewed_as_absent(h, fresh, unreadable_file, rel, expected):
    """Read as absent, the file is planned ``create``, journalled as Studio's, and deleted by a rollback."""
    unreadable_file(fresh[0] / rel)
    unreadable = entry(h, fresh[1], rel)
    assert unreadable.action == expected
    if expected != "shell_exists":
        assert unreadable.blocking is True, "the user must be asked, not overwritten"


def test_an_install_refuses_rather_than_overwrite_a_file_it_cannot_read(studio, h, fresh, unreadable_file):
    root, repo = fresh
    target = unreadable_file(root / SKILL, "keep me\n")
    with pytest.raises(studio.errors.StudioError) as exc:
        plan = h.installer.preview(repo, "install")
        run(h.installer.run(repo, "install", plan_digest=plan.digest()))
    assert exc.value.code == "install_conflict"
    target.chmod(0o600)
    assert target.read_text() == "keep me\n"
    assert h.installer.current_receipt("r_1") is None


def test_live_bytes_separates_absent_from_unreadable(tmp_path, unreadable_file, I):
    """``_read_bytes`` answers ``None`` for absent and unreadable alike; ``_live_bytes`` is the tri-state."""
    missing = tmp_path / "not-there"
    assert I._live_bytes(missing) == (None, False)
    present = tmp_path / "readable"
    present.write_text("hi\n")
    assert I._live_bytes(present) == (b"hi\n", False)
    blocked = unreadable_file(tmp_path / "blocked", "secret\n")
    assert I._live_bytes(blocked) == (None, True)
    assert I._read_bytes(blocked) is None and I._read_bytes(missing) is None
    link = tmp_path / "link"
    link.symlink_to(present)
    assert I._live_bytes(link) == (None, False)  # a symlink is refused by name at every call site


# ---- a merge target the user has reshaped ---- #


@pytest.mark.parametrize("value", [
    pytest.param(["aws-iac"], id="a-list"), pytest.param("aws-iac", id="a-string"), pytest.param(7, id="a-number"),
])
def test_a_managed_key_whose_parent_is_not_an_object_is_a_conflict(studio, h, fresh, value):
    """Whatever the user put at a managed PARENT (``mcpServers``) is theirs: never silently replaced."""
    write_json(fresh[0] / MCP, {"mcpServers": value, "mine": 1})
    mcp = entry(h, fresh[1], MCP)
    assert mcp.action == "merge_conflict"
    assert mcp.blocking is True
    with pytest.raises(studio.errors.StudioError) as exc:  # the writer refuses too
        studio.installer._assign_key({"mcpServers": value}, "mcpServers.aws-iac", {}, nested=True)
    assert exc.value.code == "install_conflict"
    assert exc.value.details["reason"] == "managed_parent_not_an_object"


def test_a_managed_key_the_user_spelled_flat_is_still_written(h, fresh):
    """The guard must not fire on the flat spelling, a supported way to hold the same key."""
    write_json(fresh[0] / CLI, {"chat.defaultAgent": "mine"})
    cli = entry(h, fresh[1], CLI)
    assert cli.action in ("merge_update", "merge_conflict", "merge_identical")
    assert "managed_parent_not_an_object" not in (cli.diff or "")


def test_an_unterminated_managed_block_is_a_conflict_not_a_document_without_markers(h, fresh):
    """Read as "no markers", a cut-short block got a SECOND block appended to it."""
    (fresh[0] / "AGENTS.md").write_text("# Mine\n\n<!-- aidlc-studio:managed:start -->\nhalf a block\n")
    agents = entry(h, fresh[1], "AGENTS.md")
    assert agents.action == "merge_conflict"
    assert agents.blocking is True


def test_a_merge_target_edited_after_the_plan_is_refused_not_overwritten(h, fresh):
    """``after:backup`` is the window between the plan and the merge: an edit made there must survive."""
    agents = fresh[0] / "AGENTS.md"
    agents.write_text("# Mine\n\nOriginal notes.\n")
    typed = "# Mine\n\nNotes I typed while the install was running.\n"
    plan = h.installer.preview(fresh[1], "install")
    h.fault.on("after:backup", lambda: agents.write_text(typed))
    result = run(h.installer.run(fresh[1], "install", plan_digest=plan.digest()))
    assert result.status in ("failed", "rolled_back"), result.status
    assert result.error == "install_conflict", result.error
    unfinished = [step.name for step in result.steps if step.ok is None]  # the raising step is left unfinished
    assert unfinished == ["merge_fragments"], [(step.name, step.ok) for step in result.steps]
    assert agents.read_text() == typed, "the user's edit was overwritten"


# ---- an adopted AGENTS.md, upgraded, is one document and not two ---- #


FENCE_START = "<!-- aidlc-studio:managed:start -->"
FENCE_END = "<!-- aidlc-studio:managed:end -->"


def fenced(body: str) -> str:
    """The payload document as Studio writes it: the markers, and nothing else in the file."""
    inner = body.strip("\n")
    return f"{FENCE_START}\n{inner}\n{FENCE_END}\n"


def test_an_adopted_agents_md_upgrades_into_one_fenced_document(studio, h, fresh, payload_v1, payload_v2,
                                                                make_harness):
    """A copied-in ``AGENTS.md`` has no fence: its first upgrade must neither conflict over Studio's own bytes
    nor append a second instruction document; bytes whose digest proves they are Studio's are replaced."""
    root, repo = fresh
    shipped = (payload_v1[0] / "AGENTS.md").read_bytes()
    (root / "AGENTS.md").write_bytes(shipped)
    assert entry(h, repo, "AGENTS.md").action == "merge_identical"
    committed(h, repo)
    assert (root / "AGENTS.md").read_bytes() == shipped
    up = make_harness(payload_v2)
    repo = reload_repo(studio, h.store)
    agents = entry(up, repo, "AGENTS.md", "upgrade")
    assert agents.action == "merge_update" and agents.blocking is False
    committed(up, repo, "upgrade")
    after = (root / "AGENTS.md").read_text()
    assert after == fenced((payload_v2[0] / "AGENTS.md").read_text("utf-8"))
    assert after.count("Run /aidlc to begin.") == 1, "two instruction documents, not one"
    assert "# AI-DLC\n" not in after, "the 2.6.2-era document was kept as well as the new one"
    assert after.count(FENCE_START) == 1 and after.count(FENCE_END) == 1


def test_a_replaced_agents_md_is_an_ordinary_block_from_then_on(studio, h, fresh, payload_v1, payload_v2,
                                                                make_harness, tmp_path):
    """Replacement writes the markers, so prose added above the block later is not drift and the next upgrade
    is a plain block swap."""
    root, repo = fresh
    (root / "AGENTS.md").write_bytes((payload_v1[0] / "AGENTS.md").read_bytes())
    committed(h, repo)
    up = make_harness(payload_v2)
    committed(up, reload_repo(studio, h.store), "upgrade")
    house = "# House rules\n\nAsk before deploying.\n\n"
    (root / "AGENTS.md").write_text(house + (root / "AGENTS.md").read_text())
    repo = reload_repo(studio, h.store)
    assert up.installer.drift(repo, up.installer.current_receipt("r_1")) == ()
    third = make_harness(build_payload(tmp_path / "payload-v3", engine_version="2.8.0",
                                       extra={"AGENTS.md": "# AI-DLC v3\n\nRun /aidlc to begin.\n"}))
    agents = entry(third, repo, "AGENTS.md", "upgrade")
    assert agents.action == "merge_update" and agents.blocking is False
    committed(third, repo, "upgrade")
    after = (root / "AGENTS.md").read_text()
    assert after == house + fenced("# AI-DLC v3\n\nRun /aidlc to begin.\n")
    assert "# AI-DLC v2" not in after and after.count(FENCE_START) == 1


def test_an_edited_adopted_agents_md_blocks_and_is_never_replaced(studio, h, fresh, payload_v1, payload_v2,
                                                                  make_harness):
    """Adopted then edited digests to neither payload nor receipt: a conflict, and the handler appends."""
    root, repo = fresh
    (root / "AGENTS.md").write_bytes((payload_v1[0] / "AGENTS.md").read_bytes())
    committed(h, repo)
    mine = (payload_v1[0] / "AGENTS.md").read_text("utf-8") + "\nAlso: never deploy on Friday.\n"
    (root / "AGENTS.md").write_text(mine)
    up = make_harness(payload_v2)
    repo = reload_repo(studio, h.store)
    plan = up.installer.preview(repo, "upgrade")
    agents = entry_of(plan, "AGENTS.md")
    assert agents.action == "merge_conflict" and agents.blocking is True and plan.blocking is True
    assert refused(up, repo, "upgrade", plan, studio.errors.StudioError).code == "install_conflict"
    assert (root / "AGENTS.md").read_text() == mine
    merged = up.installer._handler("AGENTS.md").merged_bytes(mine.encode("utf-8"),
                                                             receipt_digest=agents.receipt_sha256)
    assert merged.decode("utf-8").startswith(mine)
    assert merged.decode("utf-8").endswith(fenced((payload_v2[0] / "AGENTS.md").read_text("utf-8")))


def test_a_studio_created_agents_md_upgrades_by_swapping_its_block(studio, h, fresh, payload_v2, make_harness):
    root, repo = fresh
    committed(h, repo)
    assert (root / "AGENTS.md").read_text() == fenced("# AI-DLC\n\nRun /aidlc to begin.\n")
    tail = "\n# Ours\n\nRead the runbook first.\n"
    (root / "AGENTS.md").write_text((root / "AGENTS.md").read_text() + tail)
    committed(make_harness(payload_v2), reload_repo(studio, h.store), "upgrade")
    assert (root / "AGENTS.md").read_text() == fenced("# AI-DLC v2\n\nRun /aidlc to begin.\n") + tail


# ---- a composed scope, and a selected plugin, survive an upgrade ---- #


GRID_REL = ".kiro/tools/data/scope-grid.json"
HARNESS_REL = ".kiro/tools/data/harness.json"
#: The scope AI-DLC's composer wrote into a real 2.6.2 install (``tests/fixtures/aidlc-2.6.2-devdelta``).
COMPOSED_SCOPE = "review-major-remediation"
DEVDELTA_GRID = APP_ROOT / "tests" / "fixtures" / "aidlc-2.6.2-devdelta" / GRID_REL


def shipped_grid() -> dict:
    return json.loads((REAL_PAYLOAD / "aidlc-kiro" / GRID_REL).read_text("utf-8"))


def composed_row() -> dict:
    return json.loads(DEVDELTA_GRID.read_text("utf-8"))[COMPOSED_SCOPE]


def composed_install(studio, store, repo_builder, make_harness, tmp_path, *, edit_stock: bool = False):
    """A real-grid install (one shipped row a version behind) whose grid the composer then appended a scope to.

    Returns ``(root, repo, installed, up)``: ``up`` ships the current grid.
    """
    shipped = shipped_grid()
    names = sorted(shipped)
    older = {name: json.loads(json.dumps(shipped[name])) for name in names[:-1]}
    stages = older[names[0]].get("stages")
    assert isinstance(stages, dict) and stages, f"{names[0]} has no stages: the grid changed shape"
    older[names[0]]["stages"] = dict(list(stages.items())[:-1])
    installed = make_harness(build_payload(tmp_path / "grid-old", engine_version="2.6.2",
                                           extra={GRID_REL: json.dumps(older, indent=2) + "\n"}))
    up = make_harness(build_payload(tmp_path / "grid-new", engine_version="2.7.0",
                                    extra={GRID_REL: json.dumps(shipped, indent=2) + "\n"}))
    root = repo_builder.with_git().build()
    committed(installed, insert_repo(studio, store, root))
    live = json.loads((root / GRID_REL).read_text("utf-8"))
    live[COMPOSED_SCOPE] = composed_row()  # the composer's own write: shipped rows untouched, one key appended
    if edit_stock:
        stock = names[1]
        live[stock]["stages"] = {name: "SKIP" for name in live[stock]["stages"]}
    (root / GRID_REL).write_text(json.dumps(live, indent=2) + "\n")
    return root, reload_repo(studio, store), installed, up


def test_a_composed_scope_is_not_drift_and_survives_the_upgrade_that_refreshes_the_shipped_rows(
    studio, store, repo_builder, make_harness, tmp_path
):
    """``/aidlc compose`` appends to ``scope-grid.json`` in the repo; as a framework file that bricked every
    later install. As a merge target Studio owns only the scopes it ships, and still refreshes them."""
    root, repo, installed, up = composed_install(studio, store, repo_builder, make_harness, tmp_path)
    receipt = installed.installer.current_receipt("r_1")
    assert receipt is not None
    assert installed.installer.drift(repo, receipt) == (), "a composed scope is the user's, not drift"
    plan = up.installer.preview(repo, "upgrade")
    grid = entry_of(plan, GRID_REL)
    assert grid.action == "merge_update" and grid.blocking is False
    assert plan.blocking is False and plan.blockers == ()
    assert grid.ownership == "merge" and grid.fragment_key.startswith("scope-grid.json:")
    committed(up, repo, "upgrade")
    after = json.loads((root / GRID_REL).read_text("utf-8"))
    assert after[COMPOSED_SCOPE] == composed_row(), "the user's composed scope"
    shipped = shipped_grid()
    assert {name: after[name] for name in shipped} == shipped, "a shipped row was left a version behind"
    assert set(after) == set(shipped) | {COMPOSED_SCOPE}


def test_an_edited_shipped_scope_row_is_still_refused_next_to_a_composed_one(
    studio, store, repo_builder, make_harness, tmp_path
):
    """The rows Studio ships are still Studio's: an in-place edit is a conflict, composed key or not."""
    root, repo, installed, up = composed_install(studio, store, repo_builder, make_harness, tmp_path, edit_stock=True)
    before = (root / GRID_REL).read_bytes()
    assert [e.path for e in installed.installer.drift(repo, installed.installer.current_receipt("r_1"))] == [GRID_REL]
    plan = up.installer.preview(repo, "upgrade")
    grid = entry_of(plan, GRID_REL)
    assert grid.action == "merge_conflict" and grid.blocking is True and plan.blocking is True
    assert refused(up, repo, "upgrade", plan, studio.errors.StudioError).code == "install_conflict"
    assert (root / GRID_REL).read_bytes() == before, "the user's edit was overwritten"


def test_a_selected_plugin_survives_an_upgrade_that_adds_a_harness_identity_key(studio, h, fresh, make_harness,
                                                                                tmp_path):
    """``/aidlc plugin select`` writes ``plugins`` into ``harness.json``: Studio merges only the identity keys
    it ships, so neither a block nor an overwrite re-enabling plugins the user disabled."""
    root, repo = fresh
    committed(h, repo)
    live = json.loads((root / HARNESS_REL).read_text("utf-8"))
    live["plugins"] = ["aidlc"]
    live["documentExtractors"] = {"md": "markdown"}
    (root / HARNESS_REL).write_text(json.dumps(live, indent=2) + "\n")
    repo = reload_repo(studio, h.store)
    assert h.installer.drift(repo, h.installer.current_receipt("r_1")) == ()
    up = make_harness(build_payload(tmp_path / "harness-new", engine_version="2.7.0", extra={
        HARNESS_REL: '{"name": "kiro", "harnessDir": ".kiro", "rulesSubdir": "steering"}\n'}))
    plan = up.installer.preview(repo, "upgrade")
    harness = entry_of(plan, HARNESS_REL)
    assert harness.action == "merge_update" and harness.blocking is False and plan.blocking is False
    committed(up, repo, "upgrade")
    after = json.loads((root / HARNESS_REL).read_text("utf-8"))
    assert after["plugins"] == ["aidlc"], "the user's plugin selection"
    assert after["documentExtractors"] == {"md": "markdown"}, "hand-edited operator config"
    assert after["rulesSubdir"] == "steering" and after["name"] == "kiro" and after["harnessDir"] == ".kiro"
