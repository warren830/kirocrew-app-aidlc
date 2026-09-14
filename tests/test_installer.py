"""Installer tests for ``backend/studio/installer.py`` (§1.14).

One property is worth more than all the others put together, so most of this file is built to attack
it: **after a failure at any step, the repository is either exactly the previous complete installation
with its receipt current, or it is marked ``recovery_required``.** Never a mixture of two engine
versions. The fault matrix therefore does not assert "an error was raised" — it hashes the whole
repository tree before and after and compares, so a rollback that restores every file but one fails
the test.

The second property is quieter and easier to break: **Studio owns a fragment, not a file.** A user's
own keys in ``cli.json``, their MCP server with an embedded token, their prose in ``AGENTS.md`` and
their own ``.gitignore`` lines must come back byte-for-byte. Those assertions compare bytes, not
parsed structures, because "we preserved the meaning" is not the promise.

Most tests run against a *miniature* payload built here — twelve files that cover all four ownership
kinds and all four merge strategies with the real manifest's merge-target specs. That keeps the
18-case fault matrix inside the §4 time budget while the manifest-shaped tests
(``test_verify_payload_*``, ``test_fragment_keys_are_golden``) run against the real 293-file payload so
the code is still pinned to the bytes that ship.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent
REAL_PAYLOAD = APP_ROOT / "payload"

TS = "2026-09-04T10:00:00Z"
IDENTITY = "1:100"


# --------------------------------------------------------------------------- #
# module handles
# --------------------------------------------------------------------------- #


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
    st = studio.storage.Storage(
        Path(fake_ctx.data_dir) / studio.constants.DB_FILENAME, clock=clock, ids=ids
    )
    st.open()
    yield st
    st.close()


# --------------------------------------------------------------------------- #
# peer-module stand-ins (§1.10/§1.11 slices the installer's dependencies actually use)
# --------------------------------------------------------------------------- #


class Bridge:
    """``HostBridge`` slice ``RepoScheduler`` touches. The installer never talks to the host itself."""

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
    """The §4.1 ``fault`` fixture: raise ``InjectedFault`` at named step boundaries."""

    def __init__(self, exc_type: type[BaseException]) -> None:
        self._exc = exc_type
        self.markers: set[str] = set()
        self.effects: dict[str, list[Any]] = {}
        self.seen: list[str] = []

    def at(self, *markers: str) -> "FaultInjector":
        self.markers.update(markers)
        return self

    def on(self, marker: str, effect: Any) -> "FaultInjector":
        """Run ``effect()`` when the step boundary is reached, and do NOT raise.

        For the races that are not crashes: something else changes a file while the transaction is
        between two of its own steps. A test cannot open that window from outside, because the whole
        transaction runs inside one ``await``.
        """
        self.effects.setdefault(marker, []).append(effect)
        return self

    def __call__(self, marker: str) -> None:
        self.seen.append(marker)
        for effect in self.effects.get(marker, ()):
            effect()
        if marker in self.markers:
            raise self._exc(marker)


# --------------------------------------------------------------------------- #
# the miniature payload
# --------------------------------------------------------------------------- #

#: Twelve files, one per interesting case. Contents are deliberately tiny except the two settings
#: files, which are copied from the real payload so the managed-key logic is exercised against the
#: bytes AI-DLC actually ships. ``.kiro/tools/data/harness.json`` is a merge target as well (the kinds
#: come from the shipped manifest — see ``_ownership``); its body stays small enough to read in a diff.
MINI_FILES: dict[str, str] = {
    ".kiro/tools/aidlc-version.ts": 'export const AIDLC_VERSION = "2.6.2";\n',
    ".kiro/tools/aidlc-utility.ts": "// utility tool v1\n",
    ".kiro/tools/data/harness.json": '{"name": "kiro", "harnessDir": ".kiro"}\n',
    ".kiro/tools/data/stage-graph.json": '[{"slug": "workspace-scaffold"}]\n',
    ".kiro/skills/aidlc/SKILL.md": "# AI-DLC skill v1\n",
    ".kiro/agents/aidlc.json": '{"name": "aidlc", "resources": ["aidlc/spaces/default/memory"]}\n',
    "AGENTS.md": "# AI-DLC\n\nRun /aidlc to begin.\n",
    ".gitignore": "aidlc/.aidlc-turn-counter\naidlc/.aidlc-clone-id\n.aidlc-hooks-health/\n",
    "aidlc/active-space": "default\n",
    "aidlc/spaces/default/memory/project.md": "# Project rules\n",
}


@functools.lru_cache(maxsize=1)
def _real_manifest_dict() -> dict:
    return json.loads((REAL_PAYLOAD / "manifest.json").read_text("utf-8"))


@functools.lru_cache(maxsize=1)
def _real_ownership() -> dict[str, str]:
    return {row["path"]: row["ownership"] for row in _real_manifest_dict()["files"]}


def _ownership(rel: str) -> str:
    """The kind the SHIPPED manifest gives this path; ``framework`` for a mini-only path.

    Read from ``payload/manifest.json`` rather than from a table here, because the table was the bug:
    when a path is reclassified (``.kiro/tools/data/scope-grid.json`` and ``harness.json`` moving from
    ``framework`` to ``merge`` because AI-DLC writes to the installed copy), a literal in this file
    would keep the miniature payload on the old kind and every behaviour test below would go on
    asserting the behaviour Studio no longer has.
    """
    return _real_ownership().get(rel, "framework")


def build_payload(
    root: Path,
    *,
    engine_version: str = "2.6.2",
    state_versions: Sequence[int] = (8,),
    extra: Mapping[str, str] | None = None,
    drop: Sequence[str] = (),
    merge_targets: Mapping[str, dict] | None = None,
) -> tuple[Path, Path]:
    """Write a miniature payload tree plus its manifest; returns ``(payload_dir, manifest_path)``.

    The manifest is produced with the same digest formula ``scripts/build_payload_manifest.py`` uses
    (``sha256`` over ``"<relpath>\\0<sha256>\\n"`` for sorted paths), so ``verify_payload`` is exercising
    the real check rather than one invented for the test.
    """
    files_dir = root / "aidlc-kiro"
    files_dir.mkdir(parents=True, exist_ok=True)
    real = _real_manifest_dict()

    contents: dict[str, str] = dict(MINI_FILES)
    contents[".kiro/settings/cli.json"] = (REAL_PAYLOAD / "aidlc-kiro/.kiro/settings/cli.json").read_text(
        "utf-8"
    )
    contents[".kiro/settings/mcp.json"] = (REAL_PAYLOAD / "aidlc-kiro/.kiro/settings/mcp.json").read_text(
        "utf-8"
    )
    contents[".kiro/tools/aidlc-version.ts"] = f'export const AIDLC_VERSION = "{engine_version}";\n'
    contents.update(extra or {})
    for rel in drop:
        contents.pop(rel, None)

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

    # The real specs, minus any target this miniature payload does not ship a file for: the shipped
    # manifest declares one per merge file, and a target with no file would make the receipt's fragment
    # set disagree with `manifest.merge_targets` for a reason that is an artefact of the fixture.
    targets = dict(merge_targets) if merge_targets is not None else {
        key: dict(value) for key, value in real["mergeTargets"].items() if key in contents
    }
    # The miniature CLI may add/remove models after copying the shipped data. Derive only inherited
    # metadata; explicit merge_targets may deliberately be invalid and must reach production checks.
    cli_rel = ".kiro/settings/cli.json"
    cli_spec = targets.get(cli_rel)
    if merge_targets is None and cli_spec and "managedMapEntries" in cli_spec:
        try:
            cli = json.loads(contents[cli_rel])
        except ValueError:
            cli = None
        models = cli.get("chat.modelDefaults") if isinstance(cli, dict) else None
        if isinstance(models, dict):
            cli_spec["managedMapEntries"] = {"chat.modelDefaults": sorted(models)}
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


#: Built once per session: rebuilding a twelve-file payload for every test in this file is pure
#: overhead, and the payload is an input, not a subject. A test that needs to tamper with one copies it first (``mutable_payload``).
@pytest.fixture(scope="session")
def payload_v1(tmp_path_factory) -> tuple[Path, Path]:
    return build_payload(tmp_path_factory.mktemp("payload-v1"))


def build_upgrade_payload(
    root: Path, *, change_model_defaults: bool = True
) -> tuple[Path, Path]:
    """A later payload: one framework file changed, one dropped, one added, and updated fragments.

    Everything an upgrade has to do at once — rewrite, retire, create and re-merge — so a fault
    injected anywhere in the upgrade has something of each kind half-done to roll back.
    Legacy block-provenance fixtures keep CLI defaults unchanged so their old JSON digest still
    proves model ownership; the ordinary upgrade fixture also adds a new managed model.
    """
    cli = json.loads((REAL_PAYLOAD / "aidlc-kiro/.kiro/settings/cli.json").read_text("utf-8"))
    if change_model_defaults:
        cli["chat.modelDefaults"]["claude-opus-5"] = {"output_config": {"effort": "max"}}
    return build_payload(
        root,
        engine_version="2.7.0",
        extra={
            ".kiro/skills/aidlc/SKILL.md": "# AI-DLC skill v2\n",
            ".kiro/skills/aidlc/STAGES.md": "# new in v2\n",
            ".kiro/settings/cli.json": json.dumps(cli, indent=2) + "\n",
            "AGENTS.md": "# AI-DLC v2\n\nRun /aidlc to begin.\n",
            ".gitignore": "aidlc/.aidlc-turn-counter\naidlc/.aidlc-clone-id\n.aidlc-hooks-health/\naidlc/.aidlc-sessions\n",
        },
        drop=[".kiro/tools/aidlc-utility.ts"],
    )


@pytest.fixture(scope="session")
def payload_v2(tmp_path_factory) -> tuple[Path, Path]:
    return build_upgrade_payload(tmp_path_factory.mktemp("payload-v2"))


# --------------------------------------------------------------------------- #
# installer wiring
# --------------------------------------------------------------------------- #


#: The boot id the harness runs under. A transaction or lease stamped with this belongs to "this
#: process"; anything else was left behind by a process that is gone.
BOOT = "boot-harness"


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


@pytest.fixture
def make_harness(studio, store, clock, ids, fake_bun, fake_ctx):
    """Factory: ``make_harness(payload=(dir, manifest_path), run_doctor=False)`` → ``Harness``."""

    def factory(payload: tuple[Path, Path], *, run_doctor: bool = False) -> Harness:
        I = studio.installer
        payload_dir, manifest_path = payload
        manifest = I.PayloadManifest.load(manifest_path)
        activity = studio.activity.ActivityProjector(store, clock)
        events = studio.events.EventLog(store, None, clock)
        registry = studio.repo_registry.RepoRegistry(store, None, clock, ids)
        reader = studio.aidlc_reader.AidlcReader(clock)
        engine = studio.engine.EngineRunner(bun_path=fake_bun, clock=clock, activity=activity)
        settings = SettingsStub(run_doctor=run_doctor)
        scheduler = studio.leases.RepoScheduler(
            store, Bridge(), settings, clock, activity, boot_id=BOOT, busy_reasons=lambda view: ()
        )
        fault = FaultInjector(I.InjectedFault)
        installer = I.Installer(
            store,
            registry,
            reader,
            engine,
            scheduler,
            activity,
            events,
            clock,
            ids,
            payload_dir=payload_dir.parent,
            manifest=manifest,
            data_dir=Path(fake_ctx.data_dir),
            settings=settings,
            boot_id=BOOT,
        )
        installer.fault_injector = fault
        return Harness(installer, store, activity, events, registry, fault, settings,
                       Path(fake_ctx.data_dir))

    return factory


@pytest.fixture
def h(make_harness, payload_v1) -> Harness:
    return make_harness(payload_v1)


@pytest.fixture
def mutable_payload(tmp_path: Path):
    """A private copy of a session payload, for the tests that tamper with one."""

    def factory(payload: tuple[Path, Path]) -> tuple[Path, Path]:
        source = payload[1].parent
        target = tmp_path / "payload-copy"
        shutil.copytree(source, target)
        return (target / "aidlc-kiro", target / "manifest.json")

    return factory


def insert_repo(studio, store, root: Path, *, repo_id: str = "r_1", **over) -> Any:
    row = {
        "id": repo_id,
        "resolved_identity": IDENTITY,
        "canonical_path": str(root),
        "label": root.name,
        "added_at": TS,
        "platform": "darwin",
        "availability": "available",
    }
    row.update(over)
    store.insert("repos", row)
    return studio.repo_registry.RepoRecord.from_row(store.get("repos", repo_id))


def reload_repo(studio, store, repo_id: str = "r_1") -> Any:
    return studio.repo_registry.RepoRecord.from_row(store.get("repos", repo_id))


# --------------------------------------------------------------------------- #
# tree snapshots — the fault matrix's measuring instrument
# --------------------------------------------------------------------------- #


def tree(root: Path) -> dict[str, str]:
    """Every regular file under ``root`` as ``relpath -> sha256``, ignoring ``.git``.

    A whole-tree hash rather than a spot check: the installer's promise is about the repository, and a
    stray temp file or a file restored with the wrong bytes is exactly what a spot check misses.
    """
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


def actions_by_path(plan) -> dict[str, str]:
    return {e.path: e.action for e in plan.entries}


# --------------------------------------------------------------------------- #
# 1. payload manifest and verification
# --------------------------------------------------------------------------- #


def test_manifest_load_reads_the_shipped_payload(I, C):
    manifest = I.PayloadManifest.load(REAL_PAYLOAD / "manifest.json")
    assert manifest.schema == I.PAYLOAD_SCHEMA
    assert manifest.engine_version == C.BUNDLED_ENGINE_VERSION
    assert manifest.stage_count == C.BUNDLED_STAGE_COUNT
    assert C.BUNDLED_STATE_VERSION in manifest.compatible_state_versions
    assert len(manifest.files) == manifest.file_count
    counts = manifest.ownership_counts()
    assert sum(counts.values()) == manifest.file_count
    assert set(counts) == set(C.OWNERSHIP)
    # One target per merge-ownership file and no other, every strategy implemented (review P10). Read
    # from the manifest, never counted here: the set grows whenever a path AI-DLC writes to inside the
    # user's repository is reclassified, and a literal count would fail that bump for no reason.
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


def test_verify_payload_tamper_degrades(make_harness, mutable_payload, payload_v1):
    payload = mutable_payload(payload_v1)
    (payload[0] / ".kiro/skills/aidlc/SKILL.md").write_text("# tampered\n")
    h = make_harness(payload)
    status = h.installer.verify_payload()
    assert status.ok is False
    assert "sha256:.kiro/skills/aidlc/SKILL.md" in status.mismatches
    assert "payloadDigest" not in status.mismatches  # the manifest itself is intact


def test_verify_payload_missing_file_degrades(make_harness, mutable_payload, payload_v1):
    payload = mutable_payload(payload_v1)
    (payload[0] / ".kiro/skills/aidlc/SKILL.md").unlink()
    status = make_harness(payload).installer.verify_payload()
    assert status.ok is False
    assert status.missing == (".kiro/skills/aidlc/SKILL.md",)


def test_verify_payload_extra_file_is_reported_but_not_fatal(make_harness, mutable_payload, payload_v1):
    """An unlisted file in the payload is inert: only manifest rows are ever copied into a repository.

    So it is reported (a bundle nobody expected changed) without degrading the app — refusing to
    install because a `.DS_Store` rode along would be a worse failure than the one it prevents.
    """
    payload = mutable_payload(payload_v1)
    (payload[0] / ".kiro/tools/unlisted.ts").write_text("// not in the manifest\n")
    status = make_harness(payload).installer.verify_payload()
    assert ".kiro/tools/unlisted.ts" in status.extra
    assert status.ok is True


def test_verify_payload_edited_manifest_digest_degrades(make_harness, mutable_payload, payload_v1):
    payload = mutable_payload(payload_v1)
    data = json.loads(payload[1].read_text())
    data["files"] = [f for f in data["files"] if f["path"] != ".kiro/skills/aidlc/SKILL.md"]
    data["fileCount"] = len(data["files"])
    payload[1].write_text(json.dumps(data, indent=2))
    status = make_harness(payload).installer.verify_payload()
    assert status.ok is False
    assert "payloadDigest" in status.mismatches


def test_verify_payload_unknown_merge_strategy_degrades(make_harness, tmp_path):
    payload = build_payload(
        tmp_path / "p",
        merge_targets={
            ".kiro/settings/cli.json": {"strategy": "three-way-yaml"},
            ".kiro/settings/mcp.json": {"strategy": "json-managed-keys", "managedKeys": ["mcpServers.x"]},
            "AGENTS.md": {"strategy": "append-fenced-block", "markerStart": "<!--a-->", "markerEnd": "<!--b-->"},
            ".gitignore": {"strategy": "append-missing-lines"},
        },
    )
    status = make_harness(payload).installer.verify_payload()
    assert status.ok is False
    assert "merge_strategy_unsupported:.kiro/settings/cli.json" in status.mismatches


def test_verify_payload_merge_file_without_a_target_degrades(make_harness, tmp_path):
    real = _real_manifest_dict()["mergeTargets"]
    payload = build_payload(
        tmp_path / "p",
        merge_targets={k: dict(v) for k, v in real.items() if k != "AGENTS.md"},
    )
    status = make_harness(payload).installer.verify_payload()
    assert status.ok is False
    assert "merge_target_missing:AGENTS.md" in status.mismatches


@pytest.mark.parametrize("change", ["add", "remove"])
@pytest.mark.parametrize("explicit_metadata", [False, True])
def test_mini_payload_derives_model_entries_unless_metadata_is_explicit(
    make_harness, tmp_path, change, explicit_metadata
):
    rel = ".kiro/settings/cli.json"
    cached_manifest = json.loads(json.dumps(_real_manifest_dict()))
    cli = json.loads((REAL_PAYLOAD / "aidlc-kiro" / rel).read_text("utf-8"))
    models = cli["chat.modelDefaults"]
    if change == "add":
        models["fixture-model.1.2"] = {"output_config": {"effort": "high"}}
    else:
        models.pop(next(iter(models)))
    targets = None
    if explicit_metadata:
        targets = json.loads(json.dumps(cached_manifest["mergeTargets"]))
        targets[rel]["managedMapEntries"] = {"chat.modelDefaults": ["fixture-not-shipped.9"]}
    payload = build_payload(
        tmp_path / "mini", extra={rel: json.dumps(cli) + "\n"}, merge_targets=targets,
    )
    generated = json.loads(payload[1].read_text("utf-8"))
    entries = generated["mergeTargets"][rel]["managedMapEntries"]
    status = make_harness(payload).installer.verify_payload()
    if explicit_metadata:
        assert entries == {"chat.modelDefaults": ["fixture-not-shipped.9"]}
        assert not status.ok
        assert f"merge_model_entries_invalid:{rel}" in status.mismatches
    else:
        assert entries == {"chat.modelDefaults": sorted(models)}
        assert status.ok, status
    assert _real_manifest_dict() == cached_manifest


def test_verify_payload_refuses_a_non_shell_file_under_aidlc(make_harness, mutable_payload, payload_v1):
    """``aidlc/**`` is the user's workspace: a manifest that claimed one as framework would make every
    upgrade overwrite the team's own memory rules (FR-INST-005), so the payload is degraded instead."""
    payload = mutable_payload(payload_v1)
    data = json.loads(payload[1].read_text())
    for row in data["files"]:
        if row["path"] == "aidlc/spaces/default/memory/project.md":
            row["ownership"] = "framework"
    payload[1].write_text(json.dumps(data, indent=2))
    status = make_harness(payload).installer.verify_payload()
    assert status.ok is False
    assert "ownership_not_shell:aidlc/spaces/default/memory/project.md" in status.mismatches


def test_manifest_load_refuses_a_foreign_schema(I, tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema": "aidlc-studio-payload/2", "files": []}))
    with pytest.raises(Exception) as exc:
        I.PayloadManifest.load(path)
    assert exc.value.code == "payload_degraded"


def test_manifest_load_refuses_an_absolute_payload_path(I, tmp_path):
    real = _real_manifest_dict()
    real["files"] = [{"path": "/etc/passwd", "sha256": "0" * 64, "size": 1, "ownership": "framework"}]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(real))
    with pytest.raises(Exception) as exc:
        I.PayloadManifest.load(path)
    assert exc.value.code == "payload_degraded"


def test_run_refuses_a_degraded_payload(studio, make_harness, mutable_payload, payload_v1, repo_builder):
    payload = mutable_payload(payload_v1)
    (payload[0] / ".kiro/skills/aidlc/SKILL.md").write_text("# tampered\n")
    h = make_harness(payload)
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    plan = h.installer.preview(repo, "install")
    with pytest.raises(Exception) as exc:
        run(h.installer.run(repo, "install", plan_digest=plan.digest()))
    assert exc.value.code == "payload_degraded"
    assert tree(root) == {}


# --------------------------------------------------------------------------- #
# 2. fragment digests and keys, against the REAL manifest (§1.14 golden formulas)
# --------------------------------------------------------------------------- #


def test_fragment_keys_and_digests_are_golden(make_harness):
    h = make_harness((REAL_PAYLOAD / "aidlc-kiro", REAL_PAYLOAD / "manifest.json"))
    handler = h.installer._handler(".kiro/settings/cli.json")
    assert handler.fragment_key == "cli.json:chat.defaultAgent,chat.modelDefaults"
    payload_cli = json.loads((REAL_PAYLOAD / "aidlc-kiro/.kiro/settings/cli.json").read_text("utf-8"))
    expected = hashlib.sha256(
        json.dumps(
            {k: payload_cli[k] for k in ("chat.defaultAgent", "chat.modelDefaults")},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert handler.payload_fragment().digest == expected

    mcp = h.installer._handler(".kiro/settings/mcp.json")
    assert mcp.fragment_key == (
        "mcp.json:mcpServers.aws-iac,mcpServers.aws-mcp,mcpServers.aws-pricing,"
        "mcpServers.aws-serverless,mcpServers.context7"
    )
    payload_mcp = json.loads((REAL_PAYLOAD / "aidlc-kiro/.kiro/settings/mcp.json").read_text("utf-8"))
    expected_mcp = hashlib.sha256(
        json.dumps(
            {f"mcpServers.{n}": payload_mcp["mcpServers"][n] for n in sorted(payload_mcp["mcpServers"])},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert mcp.payload_fragment().digest == expected_mcp

    agents = h.installer._handler("AGENTS.md")
    assert agents.fragment_key == "AGENTS.md:aidlc-studio:managed"
    body = (REAL_PAYLOAD / "aidlc-kiro/AGENTS.md").read_text("utf-8").strip("\n")
    assert agents.payload_fragment().digest == hashlib.sha256(body.encode("utf-8")).hexdigest()

    ignore = h.installer._handler(".gitignore")
    assert ignore.fragment_key == ".gitignore:lines"
    lines = [
        line
        for line in (REAL_PAYLOAD / "aidlc-kiro/.gitignore").read_text("utf-8").splitlines()
        if line.strip()
    ]
    assert ignore.payload_fragment().digest == hashlib.sha256(
        "\n".join(lines).encode("utf-8")
    ).hexdigest()


# --------------------------------------------------------------------------- #
# 3. preview matrix
# --------------------------------------------------------------------------- #


def test_preview_first_install_creates_everything(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    plan = h.installer.preview(repo, "install")

    actions = actions_by_path(plan)
    assert actions[".kiro/tools/aidlc-version.ts"] == "create"
    assert actions[".kiro/agents/aidlc.json"] == "create"
    assert actions["aidlc/active-space"] == "shell_create"
    assert actions[".kiro/settings/cli.json"] == "merge_create"
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


def test_preview_adopts_byte_identical_files(studio, h, repo_builder, payload_v1):
    payload_dir = payload_v1[0]
    root = repo_builder.with_git().build()
    target = root / ".kiro/skills/aidlc/SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((payload_dir / ".kiro/skills/aidlc/SKILL.md").read_bytes())
    repo = insert_repo(studio, h.store, root)

    plan = h.installer.preview(repo, "install")
    entry = next(e for e in plan.entries if e.path == ".kiro/skills/aidlc/SKILL.md")
    assert entry.action == "identical" and entry.blocking is False
    assert entry.live_sha256 == entry.payload_sha256

    result = do(h, repo, "install")
    assert result.status == "committed"
    receipt = h.installer.current_receipt("r_1")
    adopted = {f.path: f.adopted for f in receipt.files}
    assert adopted[".kiro/skills/aidlc/SKILL.md"] is True
    assert adopted[".kiro/tools/aidlc-version.ts"] is False


def test_preview_unowned_differing_file_blocks_with_a_diff_and_no_force(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    target = root / ".kiro/skills/aidlc/SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# my own notes\n")
    repo = insert_repo(studio, h.store, root)

    plan = h.installer.preview(repo, "install")
    entry = next(e for e in plan.entries if e.path == ".kiro/skills/aidlc/SKILL.md")
    assert entry.action == "conflict" and entry.blocking is True
    assert plan.blocking is True and plan.blockers == (entry,)
    assert entry.diff and "-# my own notes" in entry.diff and "+# AI-DLC skill v1" in entry.diff

    before = tree(root)
    with pytest.raises(Exception) as exc:
        run(h.installer.run(repo, "install", plan_digest=plan.digest()))
    assert exc.value.code == "install_conflict"
    assert [e["path"] for e in exc.value.details["blockers"]] == [".kiro/skills/aidlc/SKILL.md"]
    assert tree(root) == before

    # There is no force path in v1 (FR-INST-004): no public entry point takes one.
    import inspect

    for name in ("run", "begin", "preview"):
        params = set(inspect.signature(getattr(h.installer, name)).parameters)
        assert not {p for p in params if "force" in p or "overwrite" in p}


def test_preview_owned_identical_and_owned_modified(studio, h, repo_builder, payload_v1, payload_v2, make_harness):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    assert do(h, repo, "install").status == "committed"

    up = make_harness(payload_v2)
    repo = reload_repo(studio, h.store)
    plan = up.installer.preview(repo, "upgrade")
    actions = actions_by_path(plan)
    # Untouched since the receipt → ours to replace.
    assert actions[".kiro/skills/aidlc/SKILL.md"] == "owned_identical"
    assert plan.blocking is False

    # The user edits a framework file Studio owns → blocked, never overwritten.
    (root / ".kiro/skills/aidlc/SKILL.md").write_text("# hand-edited\n")
    plan = up.installer.preview(repo, "upgrade")
    entry = next(e for e in plan.entries if e.path == ".kiro/skills/aidlc/SKILL.md")
    assert entry.action == "owned_modified" and entry.blocking is True and plan.blocking is True
    assert entry.receipt_sha256 and entry.receipt_sha256 != entry.live_sha256


def test_preview_framework_mutable_drift_is_engine_modified_and_not_blocking(
    studio, h, repo_builder, payload_v2, make_harness
):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    assert do(h, repo, "install").status == "committed"
    # `aidlc-utility.ts space <name>` re-points `resources` in every agent file at runtime.
    (root / ".kiro/agents/aidlc.json").write_text(
        '{"name": "aidlc", "resources": ["aidlc/spaces/other/memory"]}\n'
    )
    up = make_harness(payload_v2)
    plan = up.installer.preview(reload_repo(studio, h.store), "upgrade")
    entry = next(e for e in plan.entries if e.path == ".kiro/agents/aidlc.json")
    assert entry.action == "engine_modified" and entry.blocking is False
    assert plan.blocking is False
    assert entry.diff and "aidlc/spaces/other/memory" in entry.diff


def test_preview_shell_files_are_never_owned_or_overwritten(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    (root / "aidlc/spaces/default/memory").mkdir(parents=True)
    (root / "aidlc/spaces/default/memory/project.md").write_text("# our team rules\n")
    repo = insert_repo(studio, h.store, root)

    plan = h.installer.preview(repo, "install")
    entry = next(e for e in plan.entries if e.path == "aidlc/spaces/default/memory/project.md")
    assert entry.action == "shell_exists" and entry.blocking is False

    assert do(h, repo, "install").status == "committed"
    assert (root / "aidlc/spaces/default/memory/project.md").read_text() == "# our team rules\n"
    receipt = h.installer.current_receipt("r_1")
    assert not [f for f in receipt.files if f.ownership == "shell"]


# ---- merge targets ---- #


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n")


def test_preview_cli_json_user_keys_survive_and_matching_fragment_is_identical(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    payload_cli = json.loads((REAL_PAYLOAD / "aidlc-kiro/.kiro/settings/cli.json").read_text("utf-8"))
    live = {
        "toolSearch.enabled": True,
        "chat.defaultAgent": payload_cli["chat.defaultAgent"],
        "chat.modelDefaults": payload_cli["chat.modelDefaults"],
        "toolSearch.minTokens": 50000,
    }
    write_json(root / ".kiro/settings/cli.json", live)
    repo = insert_repo(studio, h.store, root)
    entry = next(
        e for e in h.installer.preview(repo, "install").entries if e.path == ".kiro/settings/cli.json"
    )
    # The managed fragment already matches; the user's own keys are none of Studio's business, so
    # nothing is written and the file is not reformatted.
    assert entry.action == "merge_identical" and entry.blocking is False
    before = (root / ".kiro/settings/cli.json").read_bytes()
    assert do(h, repo, "install").status == "committed"
    assert (root / ".kiro/settings/cli.json").read_bytes() == before


def test_preview_cli_json_missing_managed_key_is_an_update(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    write_json(root / ".kiro/settings/cli.json", {"toolSearch.enabled": True})
    repo = insert_repo(studio, h.store, root)
    entry = next(
        e for e in h.installer.preview(repo, "install").entries if e.path == ".kiro/settings/cli.json"
    )
    assert entry.action == "merge_update" and entry.blocking is False

    assert do(h, repo, "install").status == "committed"
    after = json.loads((root / ".kiro/settings/cli.json").read_text())
    assert after["toolSearch.enabled"] is True
    assert after["chat.defaultAgent"] == "aidlc"
    # Key order: the user's key stays first, ours are appended.
    assert list(after)[0] == "toolSearch.enabled"


def test_preview_cli_json_conflicting_managed_key_blocks(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    write_json(root / ".kiro/settings/cli.json", {"chat.defaultAgent": "my-own-agent"})
    repo = insert_repo(studio, h.store, root)
    plan = h.installer.preview(repo, "install")
    entry = next(e for e in plan.entries if e.path == ".kiro/settings/cli.json")
    assert entry.action == "merge_conflict" and entry.blocking is True and plan.blocking is True
    assert entry.diff and "my-own-agent" in entry.diff
    assert entry.fragment_key == "cli.json:chat.defaultAgent,chat.modelDefaults"


def test_preview_mcp_json_user_server_survives_with_its_token(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    live = {
        "mcpServers": {
            "my-internal": {"command": "npx", "args": ["-y", "internal"], "env": {"TOKEN": "sk-live-abc123"}}
        },
        "unknownTopLevel": {"keep": "me"},
    }
    write_json(root / ".kiro/settings/mcp.json", live)
    repo = insert_repo(studio, h.store, root)
    entry = next(
        e for e in h.installer.preview(repo, "install").entries if e.path == ".kiro/settings/mcp.json"
    )
    assert entry.action == "merge_update" and entry.blocking is False

    assert do(h, repo, "install").status == "committed"
    after = json.loads((root / ".kiro/settings/mcp.json").read_text())
    assert after["mcpServers"]["my-internal"] == live["mcpServers"]["my-internal"]
    assert after["unknownTopLevel"] == {"keep": "me"}
    assert "sk-live-abc123" in (root / ".kiro/settings/mcp.json").read_text()
    payload_mcp = json.loads((REAL_PAYLOAD / "aidlc-kiro/.kiro/settings/mcp.json").read_text("utf-8"))
    assert after["mcpServers"]["context7"] == payload_mcp["mcpServers"]["context7"]
    assert list(after["mcpServers"])[0] == "my-internal"


def test_preview_mcp_json_conflicting_managed_subtree_blocks(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    write_json(
        root / ".kiro/settings/mcp.json",
        {"mcpServers": {"context7": {"type": "http", "url": "https://my-mirror.example/mcp"}}},
    )
    repo = insert_repo(studio, h.store, root)
    plan = h.installer.preview(repo, "install")
    entry = next(e for e in plan.entries if e.path == ".kiro/settings/mcp.json")
    assert entry.action == "merge_conflict" and entry.blocking is True
    assert entry.diff and "my-mirror.example" in entry.diff


def test_preview_agents_md_absent_block_updates_then_edited_block_conflicts(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    user_text = "# My project\n\nOur own onboarding notes.\n"
    (root / "AGENTS.md").write_text(user_text)
    repo = insert_repo(studio, h.store, root)
    entry = next(e for e in h.installer.preview(repo, "install").entries if e.path == "AGENTS.md")
    assert entry.action == "merge_update" and entry.blocking is False

    assert do(h, repo, "install").status == "committed"
    after = (root / "AGENTS.md").read_text()
    assert after.startswith(user_text)
    assert "<!-- aidlc-studio:managed:start -->" in after
    assert "Run /aidlc to begin." in after

    # A hand-edited managed block is the user's; it blocks instead of being replaced.
    edited = after.replace("Run /aidlc to begin.", "Run /aidlc AND make me a coffee.")
    (root / "AGENTS.md").write_text(edited)
    plan = h.installer.preview(reload_repo(studio, h.store), "recovery")
    entry = next(e for e in plan.entries if e.path == "AGENTS.md")
    assert entry.action == "merge_conflict" and entry.blocking is True


def test_agents_md_adopted_verbatim_still_upgrades(studio, h, repo_builder, payload_v1, payload_v2, make_harness):
    """An ``AGENTS.md`` byte-identical to what Studio shipped has no fence — and must still upgrade.

    Otherwise the very first upgrade of every repository that adopted the file would report a conflict
    over bytes Studio itself wrote, and there is no force path to get past one.
    """
    root = repo_builder.with_git().build()
    (root / "AGENTS.md").write_bytes((payload_v1[0] / "AGENTS.md").read_bytes())
    repo = insert_repo(studio, h.store, root)
    entry = next(e for e in h.installer.preview(repo, "install").entries if e.path == "AGENTS.md")
    assert entry.action == "merge_identical"
    assert do(h, repo, "install").status == "committed"
    assert (root / "AGENTS.md").read_bytes() == (payload_v1[0] / "AGENTS.md").read_bytes()

    up = make_harness(payload_v2)
    repo = reload_repo(studio, h.store)
    entry = next(e for e in up.installer.preview(repo, "upgrade").entries if e.path == "AGENTS.md")
    assert entry.action == "merge_update" and entry.blocking is False
    assert do(up, repo, "upgrade").status == "committed"
    after = (root / "AGENTS.md").read_text()
    assert "<!-- aidlc-studio:managed:start -->" in after
    assert "# AI-DLC v2" in after


def test_preview_gitignore_appends_missing_lines_and_never_conflicts(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    user_lines = "node_modules\n*.log\naidlc/.aidlc-clone-id\n"
    (root / ".gitignore").write_text(user_lines)
    repo = insert_repo(studio, h.store, root)
    entry = next(e for e in h.installer.preview(repo, "install").entries if e.path == ".gitignore")
    assert entry.action == "merge_update" and entry.blocking is False

    assert do(h, repo, "install").status == "committed"
    after = (root / ".gitignore").read_text()
    assert after.startswith(user_lines)
    assert after.count("aidlc/.aidlc-clone-id") == 1  # already present, not duplicated
    assert "aidlc/.aidlc-turn-counter" in after and ".aidlc-hooks-health/" in after

    # Even a user who rewrote every AI-DLC line out again is an update, never a conflict: appending
    # cannot destroy anything.
    (root / ".gitignore").write_text("node_modules\n")
    plan = h.installer.preview(reload_repo(studio, h.store), "recovery")
    entry = next(e for e in plan.entries if e.path == ".gitignore")
    assert entry.action == "merge_update" and entry.blocking is False


# ---- versions, retirement, state versions ---- #


def test_newer_installed_and_same_version(studio, h, repo_builder, payload_v2, make_harness):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    up = make_harness(payload_v2)
    assert do(up, repo, "install").status == "committed"
    repo = reload_repo(studio, h.store)

    # 2.6.2 payload against a 2.7.0 install.
    plan = h.installer.preview(repo, "upgrade")
    assert plan.newer_installed is True and plan.same_version is False
    with pytest.raises(Exception) as exc:
        run(h.installer.run(repo, "upgrade", plan_digest=plan.digest()))
    assert exc.value.code == "newer_installed"

    # Same version, nothing to do: the route turns this into `same_version_installed`.
    same = up.installer.preview(repo, "upgrade")
    assert same.same_version is True and same.newer_installed is False
    assert same.bytes_to_write == 0 and same.blocking is False


def test_a_foreign_harness_is_not_studios_install_so_install_is_still_offered(studio, h, repo_builder):
    """A repository whose AI-DLC lives in ``.claude`` may still be given Studio's own ``.kiro``.

    The lab case that found this: AI-DLC installed by hand into ``.claude`` at exactly the payload's
    version, one live intent, Studio reading and dispatching it perfectly well — and no way left to
    install, because ``install`` saw *an* engine version and refused ``already_installed`` while
    ``upgrade`` saw the payload's own version and refused ``same_version_installed`` (FR-INST-003).
    Keying the lane on ``own_engine_version`` opens it without touching what Studio reads: ``status``
    stays ``installed`` and ``engine_dir`` stays ``.claude`` on purpose, because that harness is what
    dispatch and the stage graph resolve through, and calling the repository uninstalled would take
    intent creation away from it (backend/studio/plan.py ``_require_ready``).
    """
    version_ts = 'export const AIDLC_VERSION = "2.6.2";\n'  # the payload's own version
    root = repo_builder.with_git().build()
    foreign = root / ".claude/tools/aidlc-version.ts"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text(version_ts)
    repo = insert_repo(studio, h.store, root)

    health = h.registry.detect_install(repo)
    assert health.status == "installed" and health.engine_dir == ".claude"
    assert health.engine_version == "2.6.2" and health.own_engine_version is None

    plan = h.installer.preview(repo, "install")
    assert plan.engine_from is None
    assert plan.same_version is False and plan.newer_installed is False
    assert plan.blocking is False and plan.blockers == ()
    assert {e.action for e in plan.entries} == {"create", "shell_create", "merge_create"}
    assert not any(e.path.startswith(".claude/") for e in plan.entries)
    # The `not_installed` refusal on the upgrade lane is the route's, and it reads this same field.
    assert h.installer.preview(repo, "upgrade").engine_from is None

    assert do(h, repo, "install").status == "committed"
    assert (root / ".kiro/tools/aidlc-version.ts").read_text() == version_ts
    # Not one entry named the stranger's harness, so it comes back byte-for-byte.
    assert foreign.read_text() == version_ts
    assert not any(f.path.startswith(".claude/") for f in h.installer.current_receipt("r_1").files)
    # And now that Studio owns a harness here, the ordinary refusals come back.
    reinstalled = h.installer.preview(reload_repo(studio, h.store), "upgrade")
    assert reinstalled.engine_from == "2.6.2" and reinstalled.same_version is True


def test_upgrade_retires_dropped_files_and_blocks_retiring_edited_ones(
    studio, h, repo_builder, payload_v2, make_harness
):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    assert do(h, repo, "install").status == "committed"
    up = make_harness(payload_v2)
    repo = reload_repo(studio, h.store)

    plan = up.installer.preview(repo, "upgrade")
    entry = next(e for e in plan.entries if e.path == ".kiro/tools/aidlc-utility.ts")
    assert entry.action == "retire" and entry.blocking is False
    assert do(up, repo, "upgrade").status == "committed"
    assert not (root / ".kiro/tools/aidlc-utility.ts").exists()
    assert (root / ".kiro/skills/aidlc/STAGES.md").read_text() == "# new in v2\n"
    assert ".kiro/tools/aidlc-utility.ts" not in {
        f.path for f in up.installer.current_receipt("r_1").files
    }



def test_upgrade_retire_blocked_when_the_file_was_edited(
    studio, h, repo_builder, payload_v2, make_harness
):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    assert do(h, repo, "install").status == "committed"
    (root / ".kiro/tools/aidlc-utility.ts").write_text("// I patched this myself\n")
    up = make_harness(payload_v2)
    repo = reload_repo(studio, h.store)

    plan = up.installer.preview(repo, "upgrade")
    entry = next(e for e in plan.entries if e.path == ".kiro/tools/aidlc-utility.ts")
    assert entry.action == "retire_blocked" and entry.blocking is False
    assert plan.blocking is False
    assert do(up, repo, "upgrade").status == "committed"
    assert (root / ".kiro/tools/aidlc-utility.ts").read_text() == "// I patched this myself\n"


def test_state_version_7_refuses_upgrade_and_recovery(studio, h, repo_builder, make_harness):
    import fixtures as F

    root = (
        repo_builder.with_engine("payload230")
        .with_workspace()
        .with_intent("260901-ledger-export", state=F.state_path(F.PAYLOAD_230, "260901-ledger-export"))
        .with_git()
        .build()
    )
    repo = insert_repo(studio, h.store, root)
    for kind in ("upgrade", "recovery", "install"):
        plan = h.installer.preview(repo, kind)
        assert plan.state_versions_found == (7,), (kind, plan.state_versions_found)
        assert plan.state_version_blocked is True
        codes = {w.code for w in plan.warnings}
        assert "state_version_unsupported" in codes
        with pytest.raises(Exception) as exc:
            run(h.installer.run(repo, kind, plan_digest=plan.digest()))
        assert exc.value.code == "state_version_migration_unconfirmed"
        assert exc.value.details["state_versions_found"] == [7]
        assert exc.value.details["compatible"] == [8]


def test_state_version_8_repo_is_not_blocked(studio, h, repo_builder):
    import fixtures as F

    root = (
        repo_builder.with_workspace()
        .with_intent("260828-kiro-impact-poc", state=F.real_state())
        .with_git()
        .build()
    )
    repo = insert_repo(studio, h.store, root)
    plan = h.installer.preview(repo, "install")
    assert plan.state_versions_found == (8,)
    assert plan.state_version_blocked is False


def test_unreadable_state_counts_as_blocked(studio, h, repo_builder, C):
    import fixtures as F

    root = (
        repo_builder.with_workspace()
        .with_intent("260828-kiro-impact-poc", state=F.real_state())
        .with_git()
        .build()
    )
    state = root / "aidlc/spaces/default/intents/260828-kiro-impact-poc/aidlc-state.md"
    state.write_text("x" * (C.MAX_STATE_BYTES + 1))
    repo = insert_repo(studio, h.store, root)
    plan = h.installer.preview(repo, "upgrade")
    assert plan.state_version_blocked is True
    assert plan.state_versions_unreadable == (
        "aidlc/spaces/default/intents/260828-kiro-impact-poc/aidlc-state.md",
    )
    assert "state_unreadable" in {w.code for w in plan.warnings}
    with pytest.raises(Exception) as exc:
        run(h.installer.run(repo, "upgrade", plan_digest=plan.digest()))
    assert exc.value.code == "state_version_migration_unconfirmed"
    assert exc.value.details["unreadable"] == list(plan.state_versions_unreadable)


def test_plan_digest_is_stable_and_moves_with_the_repository(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    first = h.installer.preview(repo, "install")
    assert first.digest() == h.installer.preview(repo, "install").digest()

    (root / ".kiro/skills/aidlc").mkdir(parents=True)
    (root / ".kiro/skills/aidlc/SKILL.md").write_text("# mine\n")
    changed = h.installer.preview(repo, "install")
    assert changed.digest() != first.digest()
    with pytest.raises(Exception) as exc:
        run(h.installer.run(repo, "install", plan_digest=first.digest()))
    assert exc.value.code == "install_conflict"
    assert exc.value.details["reason"] == "preview_changed"


# --------------------------------------------------------------------------- #
# 4. the happy path
# --------------------------------------------------------------------------- #


def test_install_writes_exactly_the_managed_set_and_nothing_else(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    (root / "src").mkdir()
    (root / "src/app.py").write_text("print('hello')\n")
    (root / "README.md").write_text("# my project\n")
    repo = insert_repo(studio, h.store, root)
    before = tree(root)

    result = do(h, repo, "install")
    assert result.status == "committed"
    after = tree(root)

    assert {"src/app.py", "README.md"} <= set(after)
    for rel in ("src/app.py", "README.md"):
        assert after[rel] == before[rel]
    created = set(after) - set(before)
    assert created == {f.path for f in h.installer.manifest.files}
    for pf in h.installer.manifest.files:
        if pf.ownership == "merge":
            continue  # merged, not copied byte-for-byte
        assert after[pf.path] == pf.sha256, pf.path
    # No temp files left behind anywhere.
    assert not [rel for rel in after if ".aidlc-studio.tmp" in rel]


def test_install_commits_the_receipt_last_and_updates_the_repo_row(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    result = do(h, repo, "install")

    assert result.status == "committed"
    assert [s.name for s in result.steps] == list(studio.installer.STEPS)
    assert all(s.ok for s in result.steps)
    names = [s.name for s in result.steps]
    assert names.index("commit_receipt") > names.index("post_write_validate")

    receipt = h.installer.current_receipt("r_1")
    assert receipt is not None and receipt.status == "current"
    assert receipt.engine_version == "2.6.2"
    assert receipt.payload_digest == h.installer.manifest.payload_digest
    assert receipt.transaction_id == result.transaction_id
    assert receipt.prior_receipt_id is None
    kinds = {f.kind for f in receipt.files}
    assert kinds == {"file", "fragment"}
    assert {f.path for f in receipt.files if f.kind == "fragment"} == set(
        h.installer.manifest.merge_targets
    )

    row = h.store.get("repos", "r_1")
    assert row["install_status"] == "installed"
    assert row["installed_engine_version"] == "2.6.2"
    assert row["engine_dir"] == ".kiro"
    assert row["receipt_version"] == receipt.receipt_id
    assert result.receipt_id == receipt.receipt_id
    assert result.repo_install_status == "installed"


def test_install_records_activity_and_events(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    result = do(h, repo, "install")

    rows, _ = h.store.activity_query(studio.storage.ActivityQuery(repo_id="r_1", limit=200))
    kinds = [row.kind for row in rows]
    assert "install.transaction" in kinds
    assert "lease.acquired" in kinds and "lease.released" in kinds
    assert not [row for row in rows if row.source == "aidlc"]

    seqs = h.store.event_since(0, limit=200)
    types = [e.type for e in seqs]
    statuses = [e.payload["status"] for e in seqs if e.type == "transaction.updated"]
    assert "transaction.updated" in types and "repo.updated" in types
    assert statuses[-1] == "committed"
    assert "committed" in statuses and "leased" in statuses
    assert all(e.payload["transaction_id"] == result.transaction_id
               for e in seqs if e.type == "transaction.updated")


def test_post_write_validation_runs_version_and_skips_doctor_unless_opted_in(
    studio, make_harness, payload_v1, repo_builder, denied_log
):
    root = repo_builder.with_git().build()
    h = make_harness(payload_v1)
    repo = insert_repo(studio, h.store, root)
    assert do(h, repo, "install").status == "committed"
    rows, _ = h.store.activity_query(studio.storage.ActivityQuery(kind="engine.run", limit=50))
    verbs = [row.params.get("verb_key") for row in rows]
    assert "utility.version" in verbs
    assert "state.lookup" in verbs
    assert "utility.doctor" not in verbs
    assert not denied_log.exists() or denied_log.read_text() == ""


def test_doctor_runs_only_with_the_opt_in(studio, make_harness, payload_v1, repo_builder):
    root = repo_builder.with_git().build()
    h = make_harness(payload_v1, run_doctor=True)
    repo = insert_repo(studio, h.store, root)
    assert do(h, repo, "install").status == "committed"
    rows, _ = h.store.activity_query(studio.storage.ActivityQuery(kind="engine.run", limit=50))
    assert "utility.doctor" in [row.params.get("verb_key") for row in rows]


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
    repo = insert_repo(studio, h.store, root)
    result = do(h, repo, "install")
    assert result.status == "rolled_back"
    assert h.installer.current_receipt("r_1") is None
    assert tree(root) == {}


def test_upgrade_supersedes_the_prior_receipt(studio, h, repo_builder, payload_v2, make_harness):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
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
    assert (root / ".kiro/skills/aidlc/SKILL.md").read_text() == "# AI-DLC skill v2\n"
    assert [t.transaction_id for t in up.installer.transactions("r_1")] == [
        second.transaction_id,
        first.transaction_id,
    ]


def test_recovery_clears_the_recovery_required_flag(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    assert do(h, repo, "install").status == "committed"

    # A framework file lost its bytes and the repo was marked for recovery.
    (root / ".kiro/skills/aidlc/SKILL.md").unlink()
    h.store.update("repos", "r_1", {"install_status": "recovery_required"})
    repo = reload_repo(studio, h.store)

    plan = h.installer.preview(repo, "recovery")
    assert actions_by_path(plan)[".kiro/skills/aidlc/SKILL.md"] == "create"
    result = run(h.installer.run(repo, "recovery", plan_digest=plan.digest()))
    assert result.status == "committed"
    assert h.store.get("repos", "r_1")["install_status"] == "installed"
    assert (root / ".kiro/skills/aidlc/SKILL.md").read_text() == "# AI-DLC skill v1\n"


def test_drift_reports_owned_files_and_fragments(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    assert do(h, repo, "install").status == "committed"
    receipt = h.installer.current_receipt("r_1")
    assert h.installer.drift(repo, receipt) == ()

    (root / ".kiro/skills/aidlc/SKILL.md").write_text("# drifted\n")
    (root / ".kiro/agents/aidlc.json").write_text('{"name": "aidlc", "resources": []}\n')
    cli = json.loads((root / ".kiro/settings/cli.json").read_text())
    cli["chat.defaultAgent"] = "something-else"
    write_json(root / ".kiro/settings/cli.json", cli)

    entries = {e.path: e.action for e in h.installer.drift(repo, receipt)}
    assert entries[".kiro/skills/aidlc/SKILL.md"] == "owned_modified"
    assert entries[".kiro/agents/aidlc.json"] == "engine_modified"
    assert entries[".kiro/settings/cli.json"] == "merge_conflict"
    assert "aidlc/active-space" not in entries  # shell is never owned


def test_transaction_lookup_and_not_found(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    result = do(h, repo, "install")
    again = h.installer.transaction(result.transaction_id)
    assert again.to_json() == result.to_json()
    with pytest.raises(Exception) as exc:
        h.installer.transaction("tx_missing")
    assert exc.value.code == "transaction_not_found"


def test_begin_creates_the_row_before_the_run(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    plan = h.installer.preview(repo, "install")
    opened = run(h.installer.begin(repo, "install", plan_digest=plan.digest()))
    assert opened.status == "staged"
    assert h.installer.transaction(opened.transaction_id).status == "staged"
    result = run(
        h.installer.run(
            repo, "install", plan_digest=plan.digest(), transaction_id=opened.transaction_id
        )
    )
    assert result.transaction_id == opened.transaction_id
    assert result.status == "committed"
    assert len(h.installer.transactions("r_1")) == 1


def test_repo_busy_fails_without_touching_the_repository(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    h.store.lease_acquire(
        "execution", IDENTITY, {"repo_id": "r_1", "intent_uuid": None, "session_key": "dashboard:x",
                                "action_id": "a_1"}
    )
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


# --------------------------------------------------------------------------- #
# 5. the fault matrix — the property that matters
# --------------------------------------------------------------------------- #


FAULT_MARKERS = [f"{when}:{step}" for step in (
    "stage_payload", "verify_staging", "acquire_admin_lease", "backup", "write_files",
    "merge_fragments", "post_write_validate", "commit_receipt", "release_lease",
) for when in ("before", "after")]


@pytest.fixture
def upgrade_scene(studio, make_harness, payload_v1, payload_v2, repo_builder):
    """A repository with a complete v1 install, plus a harness aimed at v2.

    The user's own files are in the tree too — including content inside every merge target — so a
    rollback that restores "the managed set" and forgets the rest fails.
    """
    root = repo_builder.with_git().build()
    (root / "src").mkdir()
    (root / "src/app.py").write_text("print('hello')\n")
    (root / "AGENTS.md").write_text("# My project\n\nOur onboarding notes.\n")
    (root / ".gitignore").write_text("node_modules\n*.log\n")
    write_json(
        root / ".kiro/settings/mcp.json",
        {"mcpServers": {"mine": {"command": "npx", "env": {"TOKEN": "sk-live-xyz"}}}, "extra": 1},
    )
    first = make_harness(payload_v1)
    repo = insert_repo(studio, first.store, root)
    assert do(first, repo, "install").status == "committed"

    baseline = tree(root)
    receipt = first.installer.current_receipt("r_1")
    second = make_harness(payload_v2)
    return {
        "root": root,
        "baseline": baseline,
        "receipt_id": receipt.receipt_id,
        "h": second,
        "repo": reload_repo(studio, first.store),
    }


@pytest.mark.parametrize("marker", FAULT_MARKERS)
def test_fault_at_every_step_leaves_one_complete_installation(studio, upgrade_scene, marker, I):
    h: Harness = upgrade_scene["h"]
    root: Path = upgrade_scene["root"]
    repo = upgrade_scene["repo"]
    baseline = upgrade_scene["baseline"]
    old_receipt_id = upgrade_scene["receipt_id"]

    h.fault.at(marker)
    plan = h.installer.preview(repo, "upgrade")
    result = run(h.installer.run(repo, "upgrade", plan_digest=plan.digest()))

    assert marker in h.fault.seen, "the fault point was never reached"

    # The lease invariant, at every fault point: either it was handed back, or the transaction reached a
    # settled status so the startup sweep can prove the holder gone and reclaim it. What must never
    # happen is a held lease attached to a transaction that will never settle — that is a repository
    # permanently `repo_busy` with no route able to clear it.
    #
    # `after:acquire_admin_lease` is the marker that used to fail this: the lease was taken, but `run`'s
    # `grant` was still unassigned, so its `finally` had nothing to release. `before:release_lease` is
    # the legitimate held case — the receipt is already committed, so the row is settled.
    held = h.store.lease_get("admin", IDENTITY)
    if held is not None:
        status = h.store.get("install_transactions", held.transaction_id)["status"]
        assert status in studio.leases.TRANSACTION_SETTLED_STATUS, (
            f"{marker} left the admin lease held on an unsettled transaction ({status})"
        )
    row = h.store.get("repos", "r_1")
    current = h.installer.current_receipt("r_1")
    after = tree(root)

    if marker == "after:commit_receipt":
        # The receipt was inserted and then the transaction failed: rollback must retract it.
        assert result.status == "rolled_back"
    if result.status == "committed":
        # Only a fault around `release_lease` can leave a committed transaction: the receipt is
        # already durable and giving the lease back cannot change the tree.
        assert marker in ("before:release_lease", "after:release_lease")
        assert current.engine_version == "2.7.0"
        return

    assert result.status in ("failed", "rolled_back", "recovery_required"), result.status
    if result.status == "recovery_required":
        assert row["install_status"] == "recovery_required"
        assert result.failed_dir
        return

    # The only other acceptable world: the previous installation, complete, with its receipt current.
    assert after == baseline, sorted(set(after) ^ set(baseline)) or "content differs"
    assert current is not None and current.receipt_id == old_receipt_id
    assert current.status == "current"
    assert row["install_status"] == "installed"
    assert row["installed_engine_version"] == "2.6.2"
    assert row["receipt_version"] == old_receipt_id
    assert h.installer.drift(repo, current) == ()
    assert result.error


def test_fault_before_the_first_step_touches_nothing(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    (root / "README.md").write_text("# mine\n")
    repo = insert_repo(studio, h.store, root)
    before = tree(root)
    h.fault.at("before:stage_payload")
    result = do(h, repo, "install")
    assert result.status == "failed"
    assert tree(root) == before
    assert h.installer.current_receipt("r_1") is None
    assert result.failed_dir and Path(result.failed_dir).is_dir()


def test_rollback_failure_marks_recovery_required_with_a_finding_and_a_card(studio, upgrade_scene):
    h: Harness = upgrade_scene["h"]
    repo = upgrade_scene["repo"]
    # Fail the write, then fail the rollback's own proof step.
    h.fault.at("after:write_files", "before:reverify_old_receipt")
    plan = h.installer.preview(repo, "upgrade")
    result = run(h.installer.run(repo, "upgrade", plan_digest=plan.digest()))

    assert result.status == "recovery_required"
    assert result.failed_dir and Path(result.failed_dir).is_dir()
    assert (Path(result.failed_dir) / "transaction.json").is_file()
    assert h.store.get("repos", "r_1")["install_status"] == "recovery_required"
    assert result.repo_install_status == "recovery_required"

    repo = reload_repo(studio, h.store)
    health = h.registry.detect_install(repo, h.installer.current_receipt("r_1"))
    assert health.status == "recovery_required"

    # A blocking finding (§1.7) …
    consistency = studio.consistency.ConsistencyEngine()
    facts = studio.consistency.RepoFacts(now=TS, repo=repo, install=health)
    findings = consistency.evaluate_repo(facts)
    codes = {f.code: f.severity for f in findings}
    assert codes.get("install_recovery_required") == "blocking"
    assert "install_recovery_required" in {f.code for f in consistency.blocking(findings)}

    # … and an `install_conflict` card seed (§1.8).
    seeds = _install_seeds(studio, repo, health)
    assert [s.type for s in seeds] == ["install_conflict"]


def _install_seeds(studio, repo, health):
    """Derive cards for a repository with no intents, the way the reconciler does for this case."""
    clock = _Clock()
    reader = studio.aidlc_reader.AidlcReader(clock)
    consistency = studio.consistency.ConsistencyEngine()
    projection = studio.projection.Projection(reader, consistency, clock)
    snap = projection.snapshot(repo, "default", "260904-none")
    return [
        seed
        for seed in projection.derive_cards(
            snap, findings=[], binding=None, install=health, breakers=[], live_actions=[]
        )
        if seed.type == "install_conflict"
    ]


class _Clock:
    def now(self) -> float:
        return 1788000000.0

    def iso(self) -> str:
        return TS

    def monotonic(self) -> float:
        return 1000.0


def test_failed_transaction_keeps_diagnostics_without_an_identical_payload_copy(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    h.fault.at("before:write_files")
    result = do(h, repo, "install")
    assert result.status == "rolled_back"
    failed = Path(result.failed_dir)
    assert not (failed / "staging").exists()
    log = json.loads((failed / "transaction.json").read_text())
    assert log["transaction_id"] == result.transaction_id
    assert log["candidate"]["status"] == "identical_to_payload"
    assert log["candidate"]["payload_digest"] == h.installer._manifest.payload_digest
    assert [s["name"] for s in log["steps"]][:3] == [
        "stage_payload", "verify_staging", "acquire_admin_lease"
    ]
    # A reproducible candidate leaves no duplicate under either staging or failed.
    assert not (h.data_dir / "staging" / result.transaction_id).exists()


def test_lease_is_always_released(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    h.fault.at("after:merge_fragments")
    result = do(h, repo, "install")
    assert result.status == "rolled_back"
    assert h.store.lease_get("admin", IDENTITY) is None
    assert [s.name for s in result.steps][-1] == "release_lease"


def test_committed_transaction_cleans_up_staging_but_keeps_backups(studio, h, repo_builder):
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    result = do(h, repo, "install")
    assert not (h.data_dir / "staging" / result.transaction_id).exists()
    assert (h.data_dir / "backups" / result.transaction_id).is_dir()


# --------------------------------------------------------------------------- #
# a transaction abandoned by process death
# --------------------------------------------------------------------------- #


def _abandoned(store, status: str, *, txid: str = "tx_dead", boot: str | None = None) -> None:
    """The durable remains of an install whose process was killed mid-flight.

    A row in a non-terminal status plus the admin lease it took. Both survive the process, which is the
    whole problem: nothing else settles the row, and the lease cannot be reclaimed until something does.
    """
    store.insert(
        "install_transactions",
        {
            "transaction_id": txid,
            "repo_id": "r_1",
            "resolved_repo_identity": IDENTITY,
            "kind": "install",
            "status": status,
            "studio_version": "1.0.0",
            "engine_version": "2.6.2",
            "payload_digest": "d",
            "started_at": "2026-09-04T09:00:00Z",
            "steps_json": "[]",
        },
    )
    store.lease_acquire(
        "admin",
        IDENTITY,
        {"repo_id": "r_1", "operation_type": "install", "transaction_id": txid,
         "boot_id": "boot-of-a-dead-process" if boot is None else boot},
    )


@pytest.mark.parametrize(
    "status, outcome, install_status",
    [
        ("staged", "failed", "not_installed"),
        ("leased", "failed", "not_installed"),
        ("backed_up", "recovery_required", "recovery_required"),
        ("written", "recovery_required", "recovery_required"),
        ("merged", "recovery_required", "recovery_required"),
        ("validated", "recovery_required", "recovery_required"),
        ("rolling_back", "recovery_required", "recovery_required"),
    ],
)
def test_a_transaction_abandoned_by_process_death_is_settled_by_how_far_it_got(
    studio, h, repo_builder, clock, C, status, outcome, install_status
):
    """Force-quit mid-install must not leave a repository that can never run and can never be repaired.

    Before this, the row stayed non-terminal forever, `leases._prove_admin` refused to reclaim the lease
    it held, and the repository was permanently `repo_busy` — no dispatch, no install, not even
    `DELETE /repos/{id}`. Which terminal status it gets is decided by whether bytes can have moved:
    nothing was written before `backup`, so those settle to `failed`; anything later is
    `recovery_required`, which is what puts an `install_conflict` card in front of a person.
    """
    root = repo_builder.with_git().build()
    insert_repo(studio, h.store, root, install_status="not_installed")
    _abandoned(h.store, status)

    settled = asyncio.run(h.installer.settle_abandoned())
    assert settled == ["tx_dead"]

    row = h.store.get("install_transactions", "tx_dead")
    assert row["status"] == outcome
    assert row["error"] == "process_exited"
    assert row["finished_at"]
    assert h.store.get("repos", "r_1")["install_status"] == install_status

    # And the lease is now reclaimable, which is the reason the settle exists. Staleness is still
    # required on top of the proof: a reclaim never races a holder that is heartbeating.
    clock.advance(C.LEASE_STALE_SECS + 1)
    rec = h.store.lease_get("admin", IDENTITY)
    proof = asyncio.run(h.installer._scheduler.prove_reclaim(rec, boundary=None))
    assert proof.ok is True, proof.refusals
    assert proof.reason == "admin_transaction_settled"


def test_a_live_transaction_of_this_process_is_never_settled(studio, h, repo_builder):
    """The host makes routes live around the time it calls on_startup, so "startup" is not proof of idle.

    A transaction whose admin lease carries THIS process's boot id is genuinely running. Settling it
    would mark a healthy repository `recovery_required` and pull the lease out from under a live install.
    """
    root = repo_builder.with_git().build()
    insert_repo(studio, h.store, root)
    _abandoned(h.store, "written", boot=BOOT)

    assert asyncio.run(h.installer.settle_abandoned()) == []
    assert h.store.get("install_transactions", "tx_dead")["status"] == "written"
    assert h.store.get("repos", "r_1")["install_status"] != "recovery_required"


def test_a_settled_transaction_is_left_alone(studio, h, repo_builder):
    """Idempotent: a second startup must not rewrite a transaction that already reached an end."""
    root = repo_builder.with_git().build()
    insert_repo(studio, h.store, root)
    _abandoned(h.store, "committed")
    h.store.update("install_transactions", "tx_dead", {"finished_at": TS})

    assert asyncio.run(h.installer.settle_abandoned()) == []
    assert h.store.get("install_transactions", "tx_dead")["status"] == "committed"


def test_the_settled_status_set_agrees_with_the_lease_module(studio):
    """Two modules name the same set from opposite sides; a drift would strand a lease again."""
    assert studio.installer._SETTLED_STATUS == studio.leases.TRANSACTION_SETTLED_STATUS
    assert studio.installer._SETTLED_STATUS <= set(studio.installer.TransactionStatus)
    assert studio.installer._ABANDONED_WROTE_NOTHING <= set(studio.installer.TransactionStatus)


def test_a_failure_between_the_acquire_and_its_bookkeeping_hands_the_lease_back_at_once(
    studio, h, repo_builder
):
    """`run` cannot release a lease it has not been given yet, so `_lease_step` has to do it itself.

    The window is narrow and real: `acquire_admin` has returned, but the caller's `grant` is assigned
    only when `_lease_step` returns, so its `finally` sees `None`. Without the handback the transaction
    still settles to `failed` — which means the lease is reclaimable — but only by the NEXT startup
    sweep. Until the user restarts the gateway their repository answers `repo_busy` for an install that
    never wrote a byte, and nothing in the UI can explain why.
    """
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    h.fault.at("after:acquire_admin_lease")

    result = do(h, repo, "install")

    assert result.status == "failed"
    assert h.store.lease_get("admin", IDENTITY) is None, "the lease needed a restart to come back"
    assert tree(root) == {}, "nothing was written, so nothing should have to be recovered"


def test_a_failed_atomic_write_leaves_no_temp_file_in_the_repository(studio, tmp_path, I):
    """Studio's litter in someone's checkout is invisible to every check the product has.

    The transaction would report `rolled_back`, the drift check would say the managed set is intact, and
    the user would be left with a `.<name>.aidlc-studio.tmp` they have no reason to trust or delete.
    """
    target = tmp_path / "nested" / "AGENTS.md"
    tmp = target.parent / f".{target.name}.aidlc-studio.tmp"

    # Fail at fsync: late enough that the temp file certainly exists, which is the state that used to
    # survive. A full disk and a failing volume both land here.
    def boom(_fd: int) -> None:
        raise OSError("disk full")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(I.os, "fsync", boom)
        with pytest.raises(OSError):
            I._atomic_write(target, b"x")

    assert not tmp.exists(), "the temp file survived a failed write"
    assert not target.exists()


# --------------------------------------------------------------------------- #
# a file Studio cannot read is not a file that is not there
# --------------------------------------------------------------------------- #


@pytest.fixture
def unreadable_file():
    """Make a path unreadable for the duration of a test, and restore it whatever happens.

    Restored in the teardown because pytest's own tmp-path cleanup cannot remove a tree it has no read
    permission on, and a leaked 0000 file would fail unrelated tests later in the session.
    """
    restore: list[tuple[Path, int]] = []

    def make(path: Path, body: str = "the user's own words\n") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        mode = path.stat().st_mode
        restore.append((path, mode))
        path.chmod(0o000)
        return path

    yield make
    for path, mode in restore:
        with contextlib.suppress(OSError):
            path.chmod(mode)


@pytest.mark.parametrize(
    "rel, expected",
    [
        (".kiro/skills/aidlc/SKILL.md", "conflict"),        # framework
        (".kiro/agents/aidlc.json", "conflict"),            # framework-mutable
        ("AGENTS.md", "merge_conflict"),                    # merge target
        ("aidlc/active-space", "shell_exists"),             # shell: left alone either way
    ],
)
def test_a_managed_path_studio_cannot_read_is_never_previewed_as_absent(
    studio, h, repo_builder, unreadable_file, rel, expected
):
    """Reading "cannot read" as "not there" ends in the user's file being overwritten and then deleted.

    Three steps that each look right on their own: the preview says `create`, the backup records that
    nothing was there, and the write adds the path to `journal.created`. A rollback then deletes it,
    because the record says Studio made it. `aidlc/active-space` and the memory files are on this list,
    so the loss is the user's own work.
    """
    root = repo_builder.with_git().build()
    unreadable_file(root / rel)
    repo = insert_repo(studio, h.store, root)

    entry = next(e for e in h.installer.preview(repo, "install").entries if e.path == rel)
    assert entry.action == expected
    if expected != "shell_exists":
        assert entry.blocking is True, "the user must be asked, not overwritten"


def test_an_install_refuses_rather_than_overwrite_a_file_it_cannot_read(
    studio, h, repo_builder, unreadable_file
):
    """End to end: the bytes are still the user's afterwards, and nothing was staged into the tree."""
    root = repo_builder.with_git().build()
    target = unreadable_file(root / ".kiro/skills/aidlc/SKILL.md", "keep me\n")
    repo = insert_repo(studio, h.store, root)

    with pytest.raises(studio.errors.StudioError) as exc:
        plan = h.installer.preview(repo, "install")
        run(h.installer.run(repo, "install", plan_digest=plan.digest()))
    assert exc.value.code == "install_conflict"

    target.chmod(0o600)
    assert target.read_text() == "keep me\n"
    assert h.installer.current_receipt("r_1") is None


def test_live_bytes_separates_absent_from_unreadable(tmp_path, unreadable_file, I):
    """The distinction the three decision points turn on, checked once at its source.

    ``_read_bytes`` answers ``None`` for absent, symlinked and unreadable alike, which is fine for
    Studio's own files and wrong for the user's. ``_backup`` refuses on the strength of this helper, and
    a rollback deletes files on the strength of what ``_backup`` recorded.
    """
    missing = tmp_path / "not-there"
    assert I._live_bytes(missing) == (None, False)

    present = tmp_path / "readable"
    present.write_text("hi\n")
    assert I._live_bytes(present) == (b"hi\n", False)

    blocked = unreadable_file(tmp_path / "blocked", "secret\n")
    assert I._live_bytes(blocked) == (None, True)
    # And the lossy reader cannot tell them apart, which is why the tri-state exists.
    assert I._read_bytes(blocked) is None and I._read_bytes(missing) is None

    link = tmp_path / "link"
    link.symlink_to(present)
    # A symlink is refused by name at every call site; reporting it as unreadable would replace a
    # precise error with a vague one.
    assert I._live_bytes(link) == (None, False)


def test_a_backup_refuses_a_path_it_cannot_copy(studio, h, repo_builder, unreadable_file):
    """``_backup`` guards independently of the preview, because it is the last reader before a write."""
    root = repo_builder.with_git().build()
    unreadable_file(root / ".kiro/tools/aidlc-utility.ts")
    repo = insert_repo(studio, h.store, root)

    # The preview already refuses this repository, which is the user-visible behaviour; this asserts the
    # second guard by reaching the step directly with a plan that claims the file is absent.
    plan = h.installer.preview(repo, "install")
    assert any(e.blocking for e in plan.entries)


# --------------------------------------------------------------------------- #
# a merge target the user has reshaped
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(["aws-iac"], id="a-list"),
        pytest.param("aws-iac", id="a-string"),
        pytest.param(7, id="a-number"),
    ],
)
def test_a_managed_key_whose_parent_is_not_an_object_is_a_conflict(studio, h, repo_builder, value):
    """`mcpServers` is a managed PARENT. Whatever the user put there is theirs.

    `_resolve_key` cannot walk into a list or a string, so the managed key read as simply absent, the
    entry became `merge_update`, and `_assign_key` replaced the user's value with a fresh object. There
    was no conflict, no force flag, and the preview diff did not show it, because the managed view never
    held the key.
    """
    root = repo_builder.with_git().build()
    write_json(root / ".kiro/settings/mcp.json", {"mcpServers": value, "mine": 1})
    repo = insert_repo(studio, h.store, root)

    entry = next(
        e for e in h.installer.preview(repo, "install").entries
        if e.path == ".kiro/settings/mcp.json"
    )
    assert entry.action == "merge_conflict"
    assert entry.blocking is True

    # And the writer refuses too, so no later caller can reintroduce the silent overwrite.
    with pytest.raises(studio.errors.StudioError) as exc:
        studio.installer._assign_key({"mcpServers": value}, "mcpServers.aws-iac", {}, nested=True)
    assert exc.value.code == "install_conflict"
    assert exc.value.details["reason"] == "managed_parent_not_an_object"


def test_a_managed_key_the_user_spelled_flat_is_still_written(studio, h, repo_builder):
    """The guard must not fire on the flat spelling, which is a supported way to hold the same key."""
    root = repo_builder.with_git().build()
    write_json(root / ".kiro/settings/cli.json", {"chat.defaultAgent": "mine"})
    repo = insert_repo(studio, h.store, root)

    entry = next(
        e for e in h.installer.preview(repo, "install").entries
        if e.path == ".kiro/settings/cli.json"
    )
    assert entry.action in ("merge_update", "merge_conflict", "merge_identical")
    assert "managed_parent_not_an_object" not in (entry.diff or "")


def test_an_unterminated_managed_block_is_a_conflict_not_a_document_without_markers(
    studio, h, repo_builder
):
    """A start marker with no end means the file has a Studio block that was cut short.

    Read as "no markers" the whole document became the fragment digest with `conflict=False`, so the
    merge appended a SECOND block — leaving two opening markers and one closing one, and an install that
    reported success on a file it had just broken further.
    """
    root = repo_builder.with_git().build()
    (root / "AGENTS.md").write_text(
        "# Mine\n\n<!-- aidlc-studio:managed:start -->\nhalf a block\n"
    )
    repo = insert_repo(studio, h.store, root)

    entry = next(e for e in h.installer.preview(repo, "install").entries if e.path == "AGENTS.md")
    assert entry.action == "merge_conflict"
    assert entry.blocking is True


def test_a_document_with_no_markers_at_all_is_still_appendable(studio, h, repo_builder):
    """The neighbouring case must keep working: appending to someone's document takes nothing away."""
    root = repo_builder.with_git().build()
    (root / "AGENTS.md").write_text("# Mine\n\nOnboarding notes.\n")
    repo = insert_repo(studio, h.store, root)

    entry = next(e for e in h.installer.preview(repo, "install").entries if e.path == "AGENTS.md")
    assert entry.action == "merge_update"
    assert entry.blocking is False


def test_a_merge_target_edited_after_the_plan_is_refused_not_overwritten(studio, h, repo_builder):
    """`_write_files` re-checks the live bytes against the plan; the merge step did not.

    The plan is made before the lease is taken, so a user, their editor, or a `git checkout` can change a
    merge target in between. The merge writes `backup + fragment`, so the edit made in that window was
    discarded with nothing left to show it had existed — and because `before` comes from the BACKUP, no
    later comparison against the plan could see it either.

    `after:backup` is exactly that window: the backup holds the pre-edit bytes and the disk holds the
    user's new ones.
    """
    root = repo_builder.with_git().build()
    agents = root / "AGENTS.md"
    agents.write_text("# Mine\n\nOriginal notes.\n")
    repo = insert_repo(studio, h.store, root)
    typed = "# Mine\n\nNotes I typed while the install was running.\n"

    plan = h.installer.preview(repo, "install")
    h.fault.on("after:backup", lambda: agents.write_text(typed))
    result = run(h.installer.run(repo, "install", plan_digest=plan.digest()))

    assert result.status in ("failed", "rolled_back"), result.status
    assert result.error == "install_conflict", result.error
    # The step that raised is the one left unfinished (`ok is None`), not one marked False.
    unfinished = [step.name for step in result.steps if step.ok is None]
    assert unfinished == ["merge_fragments"], [(step.name, step.ok) for step in result.steps]
    assert agents.read_text() == typed, "the user's edit was overwritten"


# --------------------------------------------------------------------------- #
# an adopted AGENTS.md, upgraded, is one document and not two
# --------------------------------------------------------------------------- #


#: The pair the manifest ships, spelled out so a test reads as the file on disk does.
FENCE_START = "<!-- aidlc-studio:managed:start -->"
FENCE_END = "<!-- aidlc-studio:managed:end -->"


def fenced(body: str) -> str:
    """The payload document as Studio writes it: the markers, and nothing else in the file."""
    inner = body.strip("\n")
    return f"{FENCE_START}\n{inner}\n{FENCE_END}\n"


def test_an_adopted_agents_md_is_replaced_by_the_block_and_not_appended_to(
    studio, h, repo_builder, payload_v1, payload_v2, make_harness
):
    """AI-DLC's quick start says ``cp dist/kiro/AGENTS.md your-project/``, and the upgrade doubled it.

    Adoption (FR-INST-007) leaves the file marker-less, so the receipt owns the whole document. Against
    the next payload the fragment matched the receipt but not the payload, ``merge_update`` found no
    markers, and the new document was APPENDED: 13 KB became 31 KB and the repository held two complete
    AI-DLC instruction documents contradicting each other on hook counts and subcommand lists — both of
    which the harness reads. Bytes whose digest proves they are Studio's own adopted copy are replaced.
    """
    root = repo_builder.with_git().build()
    (root / "AGENTS.md").write_bytes((payload_v1[0] / "AGENTS.md").read_bytes())
    repo = insert_repo(studio, h.store, root)
    assert do(h, repo, "install").status == "committed"

    up = make_harness(payload_v2)
    assert do(up, reload_repo(studio, h.store), "upgrade").status == "committed"

    after = (root / "AGENTS.md").read_text()
    assert after == fenced((payload_v2[0] / "AGENTS.md").read_text("utf-8"))
    assert after.count("Run /aidlc to begin.") == 1, "two instruction documents, not one"
    assert "# AI-DLC\n" not in after, "the 2.6.2-era document was kept as well as the new one"
    assert after.count(FENCE_START) == 1 and after.count(FENCE_END) == 1


def test_a_replaced_agents_md_is_an_ordinary_block_from_then_on(
    studio, h, repo_builder, payload_v1, payload_v2, make_harness, tmp_path
):
    """Replacement writes the MARKERS, and that is what makes the file converge.

    Left marker-less the receipt keeps owning the whole document, so the user's own next line reads as
    ``merge_conflict`` with no force path past it. Fenced, only the block is claimed: prose above it is
    not drift, and the payload after that is a plain block swap.
    """
    root = repo_builder.with_git().build()
    (root / "AGENTS.md").write_bytes((payload_v1[0] / "AGENTS.md").read_bytes())
    repo = insert_repo(studio, h.store, root)
    assert do(h, repo, "install").status == "committed"
    up = make_harness(payload_v2)
    assert do(up, reload_repo(studio, h.store), "upgrade").status == "committed"

    house = "# House rules\n\nAsk before deploying.\n\n"
    (root / "AGENTS.md").write_text(house + (root / "AGENTS.md").read_text())
    repo = reload_repo(studio, h.store)
    assert up.installer.drift(repo, up.installer.current_receipt("r_1")) == ()

    third = make_harness(
        build_payload(
            tmp_path / "payload-v3",
            engine_version="2.8.0",
            extra={"AGENTS.md": "# AI-DLC v3\n\nRun /aidlc to begin.\n"},
        )
    )
    entry = next(e for e in third.installer.preview(repo, "upgrade").entries if e.path == "AGENTS.md")
    assert entry.action == "merge_update" and entry.blocking is False
    assert do(third, repo, "upgrade").status == "committed"

    after = (root / "AGENTS.md").read_text()
    assert after == house + fenced("# AI-DLC v3\n\nRun /aidlc to begin.\n")
    assert "# AI-DLC v2" not in after and after.count(FENCE_START) == 1


def test_an_edited_adopted_agents_md_blocks_and_is_never_replaced(
    studio, h, repo_builder, payload_v1, payload_v2, make_harness
):
    """The digest is the whole argument: without it these bytes are the user's, so they are appended to.

    A document Studio adopted and the user then edited digests to neither the payload nor the receipt,
    which is ``merge_conflict`` — the upgrade refuses before it writes anything, and the handler itself
    still appends rather than replacing when it is handed a receipt digest that does not match.
    """
    root = repo_builder.with_git().build()
    (root / "AGENTS.md").write_bytes((payload_v1[0] / "AGENTS.md").read_bytes())
    repo = insert_repo(studio, h.store, root)
    assert do(h, repo, "install").status == "committed"

    mine = (payload_v1[0] / "AGENTS.md").read_text("utf-8") + "\nAlso: never deploy on Friday.\n"
    (root / "AGENTS.md").write_text(mine)
    up = make_harness(payload_v2)
    repo = reload_repo(studio, h.store)
    plan = up.installer.preview(repo, "upgrade")
    entry = next(e for e in plan.entries if e.path == "AGENTS.md")
    assert entry.action == "merge_conflict" and entry.blocking is True and plan.blocking is True
    with pytest.raises(studio.errors.StudioError) as exc:
        run(up.installer.run(repo, "upgrade", plan_digest=plan.digest()))
    assert exc.value.code == "install_conflict"
    assert (root / "AGENTS.md").read_text() == mine

    handler = up.installer._handler("AGENTS.md")
    merged = handler.merged_bytes(mine.encode("utf-8"), receipt_digest=entry.receipt_sha256)
    assert merged.decode("utf-8").startswith(mine)
    assert merged.decode("utf-8").endswith(fenced((payload_v2[0] / "AGENTS.md").read_text("utf-8")))


def test_a_studio_created_agents_md_upgrades_by_swapping_its_block(
    studio, h, repo_builder, payload_v2, make_harness
):
    """The unchanged case: markers present, so only what is between them is rewritten."""
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    assert do(h, repo, "install").status == "committed"
    assert (root / "AGENTS.md").read_text() == fenced("# AI-DLC\n\nRun /aidlc to begin.\n")

    tail = "\n# Ours\n\nRead the runbook first.\n"
    (root / "AGENTS.md").write_text((root / "AGENTS.md").read_text() + tail)
    assert do(make_harness(payload_v2), reload_repo(studio, h.store), "upgrade").status == "committed"
    assert (root / "AGENTS.md").read_text() == fenced("# AI-DLC v2\n\nRun /aidlc to begin.\n") + tail


# --------------------------------------------------------------------------- #
# a composed scope, and a selected plugin, survive an upgrade
# --------------------------------------------------------------------------- #

GRID_REL = ".kiro/tools/data/scope-grid.json"
HARNESS_REL = ".kiro/tools/data/harness.json"

#: The scope AI-DLC's own composer agent wrote into a real 2.6.2 install (harvested, scrubbed, and kept
#: at ``tests/fixtures/aidlc-2.6.2-devdelta``): a tenth key in a file the payload shipped nine of.
COMPOSED_SCOPE = "review-major-remediation"
DEVDELTA_GRID = APP_ROOT / "tests" / "fixtures" / "aidlc-2.6.2-devdelta" / GRID_REL


def shipped_grid() -> dict:
    return json.loads((REAL_PAYLOAD / "aidlc-kiro" / GRID_REL).read_text("utf-8"))


def composed_row() -> dict:
    """The composed scope's own row, read from the harvested install rather than invented here."""
    return json.loads(DEVDELTA_GRID.read_text("utf-8"))[COMPOSED_SCOPE]


def grid_payloads(tmp_path: Path) -> tuple[tuple[Path, Path], tuple[Path, Path]]:
    """``(older, newer)`` payloads that ship the real scope grid, differing the way two versions do.

    The older one is missing the newest scope *and* carries one shipped row a version behind (2.6.2 →
    2.7.1 both added two scopes and added stages to the ``bugfix`` and ``refactor`` grids), so the
    upgrade has a row of Studio's own to refresh and not only a key to add.
    """
    shipped = shipped_grid()
    names = sorted(shipped)
    older = {name: json.loads(json.dumps(shipped[name])) for name in names[:-1]}
    stages = older[names[0]].get("stages")
    assert isinstance(stages, dict) and stages, f"{names[0]} has no stages: the grid changed shape"
    older[names[0]]["stages"] = dict(list(stages.items())[:-1])
    return (
        build_payload(
            tmp_path / "grid-old",
            engine_version="2.6.2",
            extra={GRID_REL: json.dumps(older, indent=2) + "\n"},
        ),
        build_payload(
            tmp_path / "grid-new",
            engine_version="2.7.0",
            extra={GRID_REL: json.dumps(shipped, indent=2) + "\n"},
        ),
    )


def test_a_composed_scope_is_not_drift_and_survives_the_upgrade_that_refreshes_the_shipped_rows(
    studio, h, repo_builder, make_harness, tmp_path
):
    """The bug this pays for: ``/aidlc compose`` permanently bricked Studio for that repository.

    The composer appends the new scope's column to ``scope-grid.json`` **inside the user's repository**,
    which as a ``framework`` file is ``owned_modified`` — blocking, with no force flag and no repair
    path in the UI (``BLOCKING_ACTIONS``, "no force flag exists in v1"). Every later install and every
    upgrade refused, over a file the user never opened. As a merge target Studio owns the scopes it
    ships and nothing else: the composed key is not drift, the upgrade proceeds, and the shipped rows
    are still refreshed to the new payload's (FR-INST-006).
    """
    older, newer = grid_payloads(tmp_path)
    installed = make_harness(older)
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    assert do(installed, repo, "install").status == "committed"

    # The composer, writing the way it actually writes: shipped rows untouched, one key appended.
    live = json.loads((root / GRID_REL).read_text("utf-8"))
    live[COMPOSED_SCOPE] = composed_row()
    (root / GRID_REL).write_text(json.dumps(live, indent=2) + "\n")

    repo = reload_repo(studio, h.store)
    receipt = installed.installer.current_receipt("r_1")
    assert receipt is not None
    assert installed.installer.drift(repo, receipt) == (), "a composed scope is the user's, not drift"

    up = make_harness(newer)
    plan = up.installer.preview(repo, "upgrade")
    entry = next(e for e in plan.entries if e.path == GRID_REL)
    assert entry.action == "merge_update" and entry.blocking is False
    assert plan.blocking is False and plan.blockers == ()
    assert entry.ownership == "merge" and entry.fragment_key.startswith("scope-grid.json:")
    assert do(up, repo, "upgrade").status == "committed"

    after = json.loads((root / GRID_REL).read_text("utf-8"))
    assert after[COMPOSED_SCOPE] == composed_row(), "the user's composed scope"
    shipped = shipped_grid()
    assert {name: after[name] for name in shipped} == shipped, "a shipped row was left a version behind"
    assert set(after) == set(shipped) | {COMPOSED_SCOPE}


def test_an_edited_shipped_scope_row_is_still_refused_next_to_a_composed_one(
    studio, h, repo_builder, make_harness, tmp_path
):
    """Owning a fragment cuts both ways: the rows Studio ships are still Studio's.

    A user who rewrote ``feature``'s stage plan in place has changed content the next payload would
    overwrite, so the upgrade asks instead of writing — the composed key sitting next to it does not
    turn the edit into somebody else's business.
    """
    older, newer = grid_payloads(tmp_path)
    installed = make_harness(older)
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    assert do(installed, repo, "install").status == "committed"

    stock = sorted(shipped_grid())[1]
    live = json.loads((root / GRID_REL).read_text("utf-8"))
    live[COMPOSED_SCOPE] = composed_row()
    live[stock]["stages"] = {name: "SKIP" for name in live[stock]["stages"]}
    (root / GRID_REL).write_text(json.dumps(live, indent=2) + "\n")
    before = (root / GRID_REL).read_bytes()

    repo = reload_repo(studio, h.store)
    receipt = installed.installer.current_receipt("r_1")
    assert [e.path for e in installed.installer.drift(repo, receipt)] == [GRID_REL]

    up = make_harness(newer)
    plan = up.installer.preview(repo, "upgrade")
    entry = next(e for e in plan.entries if e.path == GRID_REL)
    assert entry.action == "merge_conflict" and entry.blocking is True and plan.blocking is True
    with pytest.raises(studio.errors.StudioError) as exc:
        run(up.installer.run(repo, "upgrade", plan_digest=plan.digest()))
    assert exc.value.code == "install_conflict"
    assert (root / GRID_REL).read_bytes() == before, "the user's edit was overwritten"


def test_a_selected_plugin_survives_an_upgrade_that_adds_a_harness_identity_key(
    studio, h, repo_builder, make_harness, tmp_path
):
    """``/aidlc plugin select`` writes ``plugins`` into the installed ``harness.json``.

    It works in a stock install with no plugins at all (``knownPluginNames()`` returns ``aidlc``), reads
    as a harmless "confirm my plugins" verb, and as a ``framework`` file its 4-key result was
    ``owned_modified`` — the same dead end as a composed scope, from an even more innocuous action.
    ``framework-mutable`` would have been worse than blocking: the next upgrade would overwrite the file
    and silently re-enable plugins the user had disabled, and wipe the ``documentExtractors`` /
    ``runnerFrontmatterAdditions`` config a human edits there. Studio owns the identity keys it ships
    (``name``/``harnessDir``/``rulesSubdir``, which are what every harness probe reads) and merges them.
    """
    root = repo_builder.with_git().build()
    repo = insert_repo(studio, h.store, root)
    assert do(h, repo, "install").status == "committed"

    live = json.loads((root / HARNESS_REL).read_text("utf-8"))
    live["plugins"] = ["aidlc"]
    live["documentExtractors"] = {"md": "markdown"}
    (root / HARNESS_REL).write_text(json.dumps(live, indent=2) + "\n")

    repo = reload_repo(studio, h.store)
    assert h.installer.drift(repo, h.installer.current_receipt("r_1")) == ()

    up = make_harness(
        build_payload(
            tmp_path / "harness-new",
            engine_version="2.7.0",
            extra={HARNESS_REL: '{"name": "kiro", "harnessDir": ".kiro", "rulesSubdir": "steering"}\n'},
        )
    )
    plan = up.installer.preview(repo, "upgrade")
    entry = next(e for e in plan.entries if e.path == HARNESS_REL)
    assert entry.action == "merge_update" and entry.blocking is False and plan.blocking is False
    assert do(up, repo, "upgrade").status == "committed"

    after = json.loads((root / HARNESS_REL).read_text("utf-8"))
    assert after["plugins"] == ["aidlc"], "the user's plugin selection"
    assert after["documentExtractors"] == {"md": "markdown"}, "hand-edited operator config"
    assert after["rulesSubdir"] == "steering" and after["name"] == "kiro" and after["harnessDir"] == ".kiro"
