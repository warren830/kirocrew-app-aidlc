"""An upgrade to a payload that stops merging into a file takes Studio's part out of it exactly as uninstall would,
and keeps anything Studio cannot prove is still its own.

AI-DLC 2.11's copy runtime ships no ``AGENTS.md`` or ``.gitignore``: the engine adds its own marked block to each at
the next session (``addRootBlocks``), so a repository installed from 2.10 must not keep Studio's 2.10 copy beside it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_installer import (  # noqa: F401 - fixtures
    CLI, FAULT_MARKERS, MEMORY, build_payload, committed, do, entry_of, h, make_harness, payload_v1, reload_repo, run,
    store, tree,
)
from test_installer_cancel import cancel_during
from test_installer_rollback import rollback
from test_installer_uninstall import register, strip_uninstall_metadata, uninstall

AGENTS, IGNORE = "AGENTS.md", ".gitignore"
FENCE_START, FENCE_END = "<!-- aidlc-studio:managed:start -->", "<!-- aidlc-studio:managed:end -->"
HOUSE_AGENTS = b"# House rules\n\nAsk before deploying.\n"
HOUSE_IGNORE = b"node_modules\n*.log\n"


@pytest.fixture(scope="session")
def payload_211(tmp_path_factory) -> tuple[Path, Path]:
    """Shaped like 2.11's copy runtime: no AGENTS.md, .gitignore, active space or team memory, and a cli.json that
    holds only the default agent."""
    return build_payload(tmp_path_factory.mktemp("payload-211"), engine_version="2.11.0",
                         extra={CLI: json.dumps({"chat.defaultAgent": "aidlc"}, indent=2) + "\n"},
                         drop=[AGENTS, IGNORE, "aidlc/active-space", MEMORY])


@pytest.fixture
def up(make_harness, payload_211):
    return make_harness(payload_211)


def install(studio, h, repo_builder, *, agents: bytes | None = None, ignore: bytes | None = None):
    """``(root, repo)``: the 2.10-shaped mini payload installed over the user's own files, if any."""
    root = repo_builder.with_git().build()
    if agents is not None:
        (root / AGENTS).write_bytes(agents)
    if ignore is not None:
        (root / IGNORE).write_bytes(ignore)
    repo = register(studio, h, root)
    committed(h, repo)
    return root, reload_repo(studio, h.store)


def test_the_211_shaped_payload_merges_neither_file(up):
    assert not {AGENTS, IGNORE} & set(up.installer.manifest.merge_targets)
    assert up.installer.manifest.merge_targets[CLI]["managedKeys"] == ["chat.defaultAgent"]


def test_upgrade_removes_the_files_studio_created(studio, h, up, repo_builder):
    root, repo = install(studio, h, repo_builder)
    assert FENCE_START in (root / AGENTS).read_text()
    plan = up.installer.preview(repo, "upgrade")
    for path, key in ((AGENTS, "AGENTS.md:aidlc-studio:managed"), (IGNORE, ".gitignore:lines")):
        retire = entry_of(plan, path)
        assert (retire.action, retire.ownership, retire.fragment_key) == ("retire", "merge", key)
        assert retire.blocking is False and retire.payload_sha256 is None
        assert retire.live_sha256 == tree(root)[path] and retire.diff
    assert f"-{FENCE_START}" in entry_of(plan, AGENTS).diff
    assert "-.aidlc-hooks-health/" in entry_of(plan, IGNORE).diff
    assert plan.blocking is False and plan.counts["retire"] == 2 and plan.counts["retire_blocked"] == 0
    committed(up, repo, "upgrade")
    assert not (root / AGENTS).exists() and not (root / IGNORE).exists()
    receipt = up.installer.current_receipt(repo.repo_id)
    assert receipt.engine_version == "2.11.0"
    assert not {AGENTS, IGNORE} & set(receipt.by_path())
    assert json.loads((root / CLI).read_text())["chat.defaultAgent"] == "aidlc"


def test_upgrade_takes_studios_part_out_of_the_users_own_files(studio, h, up, repo_builder):
    root, repo = install(studio, h, repo_builder, agents=HOUSE_AGENTS, ignore=HOUSE_IGNORE)
    assert (root / AGENTS).read_bytes() != HOUSE_AGENTS and (root / IGNORE).read_bytes() != HOUSE_IGNORE
    plan = up.installer.preview(repo, "upgrade")
    assert {entry_of(plan, p).action for p in (AGENTS, IGNORE)} == {"retire"}
    assert entry_of(plan, AGENTS).payload_sha256 is not None  # rewritten, never unlinked
    committed(up, repo, "upgrade")
    # Byte for byte, including the blank line Studio's merge put before its block.
    assert (root / AGENTS).read_bytes() == HOUSE_AGENTS
    assert (root / IGNORE).read_bytes() == HOUSE_IGNORE
    assert not {AGENTS, IGNORE} & set(up.installer.current_receipt(repo.repo_id).by_path())


@pytest.mark.parametrize("edit, path", [
    pytest.param(lambda text: text.replace("Run /aidlc to begin.", "Run /aidlc to begin, then ask Sam."), AGENTS,
                 id="block-edited"),
    pytest.param(lambda text: text.replace(FENCE_END, ""), AGENTS, id="block-cut-short"),
    pytest.param(lambda text: text + FENCE_START + "\n", AGENTS, id="block-markers-twice"),
    pytest.param(lambda text: text.replace(".aidlc-hooks-health/\n", ""), IGNORE, id="studio-line-removed"),
])
def test_upgrade_keeps_a_fragment_that_changed_and_says_why(studio, h, up, repo_builder, edit, path):
    root, repo = install(studio, h, repo_builder, agents=HOUSE_AGENTS, ignore=HOUSE_IGNORE)
    (root / path).write_text(edit((root / path).read_text()))
    before = (root / path).read_bytes()
    plan = up.installer.preview(repo, "upgrade")
    kept = entry_of(plan, path)
    assert (kept.action, kept.reason, kept.blocking) == ("retire_blocked", "fragment_modified", False)
    assert plan.blocking is False
    committed(up, repo, "upgrade")
    assert (root / path).read_bytes() == before
    assert path not in up.installer.current_receipt(repo.repo_id).by_path()


def test_upgrade_says_nothing_about_a_part_already_taken_out(studio, h, up, repo_builder):
    root, repo = install(studio, h, repo_builder, agents=HOUSE_AGENTS, ignore=HOUSE_IGNORE)
    (root / AGENTS).write_bytes(HOUSE_AGENTS)
    (root / IGNORE).write_bytes(HOUSE_IGNORE)
    plan = up.installer.preview(repo, "upgrade")
    assert not {AGENTS, IGNORE} & {e.path for e in plan.entries}
    committed(up, repo, "upgrade")
    assert (root / AGENTS).read_bytes() == HOUSE_AGENTS and (root / IGNORE).read_bytes() == HOUSE_IGNORE


def test_upgrade_keeps_a_file_studio_only_adopted(studio, h, up, repo_builder):
    """A repository that already held AI-DLC's AGENTS.md verbatim: Studio adopted it and never added it."""
    shipped = (h.installer.payload_root / AGENTS).read_bytes()
    root, repo = install(studio, h, repo_builder, agents=shipped)
    assert h.installer.current_receipt(repo.repo_id).by_path()[AGENTS].adopted is True
    kept = entry_of(up.installer.preview(repo, "upgrade"), AGENTS)
    assert (kept.action, kept.reason) == ("retire_blocked", "preexisting_fragment")
    committed(up, repo, "upgrade")
    assert (root / AGENTS).read_bytes() == shipped


def test_a_file_changed_before_the_run_starts_refuses_the_plan(studio, h, up, repo_builder):
    root, repo = install(studio, h, repo_builder, agents=HOUSE_AGENTS)
    plan = up.installer.preview(repo, "upgrade")
    (root / AGENTS).write_bytes((root / AGENTS).read_bytes() + b"\nOne more house rule.\n")
    before = (root / AGENTS).read_bytes()
    with pytest.raises(Exception) as refused:
        run(up.installer.run(repo, "upgrade", plan_digest=plan.digest()))
    assert refused.value.code == "install_conflict"
    assert (root / AGENTS).read_bytes() == before


def test_a_file_changed_while_the_run_backs_up_is_left_alone(studio, h, up, repo_builder, monkeypatch):
    root, repo = install(studio, h, repo_builder, agents=HOUSE_AGENTS)
    edited = (root / AGENTS).read_bytes() + b"\nOne more house rule.\n"
    backup = up.installer._backup

    def backup_then_edit(*args):
        detail = backup(*args)
        (root / AGENTS).write_bytes(edited)
        return detail

    monkeypatch.setattr(up.installer, "_backup", backup_then_edit)
    result = do(up, repo, "upgrade")
    assert result.status == "committed"
    assert (root / AGENTS).read_bytes() == edited
    write = next(step for step in result.steps if step.name == "write_files")
    assert write.detail["retire_skipped"] == 1


@pytest.mark.parametrize("marker", FAULT_MARKERS)
def test_a_fault_at_any_step_leaves_the_previous_installation(studio, h, up, repo_builder, marker):
    root, repo = install(studio, h, repo_builder, agents=HOUSE_AGENTS, ignore=HOUSE_IGNORE)
    baseline, receipt = tree(root), h.installer.current_receipt(repo.repo_id)
    installed = {path: (root / path).read_bytes() for path in (AGENTS, IGNORE)}
    up.fault.at(marker)
    result = run(up.installer.run(repo, "upgrade", plan_digest=up.installer.preview(repo, "upgrade").digest()))
    assert marker in up.fault.seen
    step = marker.partition(":")[2]
    if step == "release_lease":  # the receipt is already durable
        assert result.status == "committed"
        assert (root / AGENTS).read_bytes() == HOUSE_AGENTS and (root / IGNORE).read_bytes() == HOUSE_IGNORE
        return
    # Nothing in the repository moves before `backup`; from there on every failure is rolled back and proved.
    expected = "failed" if step in ("stage_payload", "verify_staging", "acquire_admin_lease") else "rolled_back"
    assert result.status == expected, (result.status, result.error)
    assert tree(root) == baseline
    assert {path: (root / path).read_bytes() for path in (AGENTS, IGNORE)} == installed
    assert up.installer.current_receipt(repo.repo_id).receipt_id == receipt.receipt_id
    assert up.installer.drift(repo, receipt) == ()


@pytest.mark.parametrize("method", ["_write_files", "_merge_fragments", "_post_write_validate"])
def test_cancelling_after_the_retire_puts_studios_part_back(studio, h, up, repo_builder, monkeypatch, method):
    root, repo = install(studio, h, repo_builder, agents=HOUSE_AGENTS, ignore=HOUSE_IGNORE)
    before = tree(root)
    result = run(cancel_during(up, repo, "upgrade", method, monkeypatch))
    assert (result.status, result.error) == ("rolled_back", "cancelled")
    assert tree(root) == before


def test_rolling_back_the_upgrade_brings_studios_part_back(studio, h, up, repo_builder):
    root, repo = install(studio, h, repo_builder, agents=HOUSE_AGENTS, ignore=HOUSE_IGNORE)
    installed = {path: (root / path).read_bytes() for path in (AGENTS, IGNORE)}
    target = h.installer.current_receipt(repo.repo_id)
    committed(up, repo, "upgrade")
    repo = reload_repo(studio, up.store)
    plan = up.installer.preview(repo, "rollback")
    assert not plan.blocking, [(e.path, e.reason) for e in plan.blockers]
    assert {entry_of(plan, p).action for p in (AGENTS, IGNORE)} == {"restore_version"}
    result = rollback(up, repo)
    assert result.status == "committed", result
    assert {path: (root / path).read_bytes() for path in (AGENTS, IGNORE)} == installed
    assert up.installer.current_receipt(repo.repo_id).engine_version == target.engine_version


def test_a_narrower_cli_json_leaves_the_old_model_entry_and_uninstall_still_works(studio, h, up, repo_builder):
    """2.11 manages ``chat.defaultAgent`` alone: the 2.10 model default Studio wrote stays, unowned, and the new
    receipt claims nothing an uninstall would refuse as exceeding its ownership."""
    root, repo = install(studio, h, repo_builder)
    model = json.loads((root / CLI).read_text())["chat.modelDefaults"]
    assert list(model) == ["claude-opus-4.8"]
    cli = entry_of(up.installer.preview(repo, "upgrade"), CLI)
    assert (cli.action, cli.fragment_key) == ("merge_identical", "cli.json:chat.defaultAgent")
    committed(up, repo, "upgrade")
    repo = reload_repo(studio, up.store)
    assert up.installer.drift(repo, up.installer.current_receipt(repo.repo_id)) == ()
    plan = up.installer.preview(repo, "uninstall")
    assert not plan.blocking, [(e.path, e.reason) for e in plan.blockers]
    assert not {AGENTS, IGNORE} & {e.path for e in plan.entries}  # retired: no longer Studio's to remove
    assert uninstall(up, repo).status == "committed"
    left = json.loads((root / CLI).read_text())
    assert "chat.defaultAgent" not in left
    assert left["chat.modelDefaults"] == model  # the inert 2.10 default for claude-opus-4.8, left in place
    assert not (root / AGENTS).exists() and not (root / IGNORE).exists()


def test_drift_reads_a_210_receipt_by_its_own_definitions(studio, h, up, repo_builder):
    """Before the upgrade, the 2.11 payload's narrower specs must not call an untouched 2.10 install drifted."""
    root, repo = install(studio, h, repo_builder, agents=HOUSE_AGENTS, ignore=HOUSE_IGNORE)
    assert up.installer.drift(repo, up.installer.current_receipt(repo.repo_id)) == ()
    (root / AGENTS).write_text((root / AGENTS).read_text().replace("Run /aidlc", "Run aidlc"))
    drifted = up.installer.drift(repo, up.installer.current_receipt(repo.repo_id))
    assert [(e.path, e.action) for e in drifted] == [(AGENTS, "merge_conflict")]


def test_upgrade_removes_only_the_ignore_lines_studio_added(studio, h, up, repo_builder):
    """A line the project already had stays, and so does a duplicate the user added after the install."""
    before = b"node_modules\n.aidlc-hooks-health/\n"
    root, repo = install(studio, h, repo_builder, ignore=before)
    meta = h.installer.current_receipt(repo.repo_id).by_path()[IGNORE].uninstall
    assert meta["lines"] == ["aidlc/.aidlc-turn-counter", "aidlc/.aidlc-clone-id"]
    later = b"coverage/\naidlc/.aidlc-clone-id\n"
    (root / IGNORE).write_bytes((root / IGNORE).read_bytes() + later)
    retire = entry_of(up.installer.preview(repo, "upgrade"), IGNORE)
    assert (retire.action, retire.reason) == ("retire", None)
    committed(up, repo, "upgrade")
    assert (root / IGNORE).read_bytes() == before + later


@pytest.mark.parametrize("adopted", [False, True])
def test_upgrade_keeps_what_a_legacy_receipt_cannot_prove(studio, h, up, repo_builder, adopted):
    """A receipt from before Studio recorded provenance: its ignore lines and an adopted block stay; a block Studio
    wrote goes exactly as uninstall takes it out (``test_legacy_receipt_preserves_unprovable_fragments``)."""
    shipped = (h.installer.payload_root / AGENTS).read_bytes()
    root, repo = install(studio, h, repo_builder, agents=shipped if adopted else HOUSE_AGENTS, ignore=HOUSE_IGNORE)
    strip_uninstall_metadata(h, h.installer.current_receipt(repo.repo_id))
    on_disk = {path: (root / path).read_bytes() for path in (AGENTS, IGNORE)}
    plan = up.installer.preview(repo, "upgrade")
    ignore, agents = entry_of(plan, IGNORE), entry_of(plan, AGENTS)
    assert (ignore.action, ignore.reason) == ("retire_blocked", "legacy_fragment_ownership_unknown")
    if adopted:
        assert (agents.action, agents.reason) == ("retire_blocked", "legacy_fragment_ownership_unknown")
    else:
        assert (agents.action, agents.reason) == ("retire", None)
    assert not plan.blocking
    committed(up, repo, "upgrade")
    assert (root / IGNORE).read_bytes() == on_disk[IGNORE]
    assert (root / AGENTS).read_bytes() == (shipped if adopted else HOUSE_AGENTS)
    assert not {AGENTS, IGNORE} & set(up.installer.current_receipt(repo.repo_id).by_path())


@pytest.mark.parametrize("users_files", [False, True], ids=["studio-created", "users-own"])
@pytest.mark.parametrize("marker", ["before:merge_fragments", "before:post_write_validate", "before:commit_receipt",
                                    "after:commit_receipt"])
def test_a_later_step_failing_after_the_retire_restores_the_bytes(studio, h, up, repo_builder, users_files, marker):
    root, repo = install(studio, h, repo_builder, **({"agents": HOUSE_AGENTS, "ignore": HOUSE_IGNORE}
                                                     if users_files else {}))
    installed = {path: (root / path).read_bytes() for path in (AGENTS, IGNORE)}
    receipt = h.installer.current_receipt(repo.repo_id)
    up.fault.at(marker)
    result = do(up, repo, "upgrade")
    assert result.status == "rolled_back" and marker in result.error, (result.status, result.error)
    write = next(step for step in result.steps if step.name == "write_files")
    assert write.detail["retired"] == 2  # both were taken out before the failure, and both came back
    assert {path: (root / path).read_bytes() for path in (AGENTS, IGNORE)} == installed
    assert up.installer.current_receipt(repo.repo_id).receipt_id == receipt.receipt_id


def test_retrying_after_a_rolled_back_upgrade_retires_the_same_way(studio, h, up, repo_builder):
    root, repo = install(studio, h, repo_builder, agents=HOUSE_AGENTS, ignore=HOUSE_IGNORE)
    first = up.installer.preview(repo, "upgrade")
    up.fault.at("before:commit_receipt")
    assert do(up, repo, "upgrade").status == "rolled_back"
    up.fault.markers.clear()
    again = up.installer.preview(reload_repo(studio, up.store), "upgrade")
    assert again.digest() == first.digest()
    committed(up, reload_repo(studio, up.store), "upgrade")
    assert (root / AGENTS).read_bytes() == HOUSE_AGENTS and (root / IGNORE).read_bytes() == HOUSE_IGNORE


def test_recovery_finishes_a_retire_that_was_interrupted_halfway(studio, h, up, repo_builder, monkeypatch):
    """The process loses the first retire's rollback: recovery sees that part already gone, retires the other, and
    commits a receipt that owns neither."""
    root, repo = install(studio, h, repo_builder, agents=HOUSE_AGENTS, ignore=HOUSE_IGNORE)
    retire, calls = up.installer._retire_fragment, []

    def second_fails(*args):
        calls.append(args[1].path)
        if len(calls) == 2:
            raise OSError("disk went away")
        return retire(*args)

    monkeypatch.setattr(up.installer, "_retire_fragment", second_fails)
    up.fault.at("before:restore_backups")
    assert do(up, repo, "upgrade").status == "recovery_required"
    monkeypatch.undo()
    up.fault.markers.clear()
    first, second = calls
    assert (root / first).read_bytes() == {AGENTS: HOUSE_AGENTS, IGNORE: HOUSE_IGNORE}[first]
    assert {AGENTS: FENCE_START.encode(), IGNORE: b".aidlc-hooks-health/"}[second] in (root / second).read_bytes()
    repo = reload_repo(studio, up.store)
    plan = up.installer.preview(repo, "recovery")
    assert [(e.path, e.action) for e in plan.entries if e.action.startswith("retire")] == [(second, "retire")]
    assert run(up.installer.run(repo, "recovery", plan_digest=plan.digest())).status == "committed"
    assert (root / AGENTS).read_bytes() == HOUSE_AGENTS and (root / IGNORE).read_bytes() == HOUSE_IGNORE
    assert up.store.get("repos", repo.repo_id)["install_status"] == "installed"
    assert not {AGENTS, IGNORE} & set(up.installer.current_receipt(repo.repo_id).by_path())


def test_rolling_back_an_upgrade_that_kept_an_edited_part_leaves_it_as_it_is(studio, h, up, repo_builder):
    """The upgrade never touched a part it kept, so the rollback restores the old receipt's claim over the very bytes
    it held before; its backup cannot match that receipt, which must not block the rollback forever."""
    root, repo = install(studio, h, repo_builder, agents=HOUSE_AGENTS, ignore=HOUSE_IGNORE)
    (root / AGENTS).write_text((root / AGENTS).read_text().replace("Run /aidlc to begin.", "Run /aidlc, ask Sam."))
    (root / IGNORE).write_text((root / IGNORE).read_text().replace(".aidlc-hooks-health/\n", ""))
    kept = {path: (root / path).read_bytes() for path in (AGENTS, IGNORE)}
    target = h.installer.current_receipt(repo.repo_id)
    assert {entry_of(up.installer.preview(repo, "upgrade"), p).action for p in (AGENTS, IGNORE)} == {"retire_blocked"}
    committed(up, repo, "upgrade")
    repo = reload_repo(studio, up.store)
    plan = up.installer.preview(repo, "rollback")
    assert not plan.blocking, [(e.path, e.reason) for e in plan.blockers]
    assert {entry_of(plan, p).action for p in (AGENTS, IGNORE)} == {"identical"}
    assert rollback(up, repo).status == "committed"
    assert {path: (root / path).read_bytes() for path in (AGENTS, IGNORE)} == kept
    restored = up.installer.current_receipt(repo.repo_id)
    assert restored.engine_version == target.engine_version
    assert {p: restored.by_path()[p].canonical_digest for p in (AGENTS, IGNORE)} == {
        p: target.by_path()[p].canonical_digest for p in (AGENTS, IGNORE)}


def test_a_kept_part_edited_again_after_the_upgrade_still_blocks_the_rollback(studio, h, up, repo_builder):
    root, repo = install(studio, h, repo_builder, agents=HOUSE_AGENTS)
    (root / AGENTS).write_text((root / AGENTS).read_text().replace("Run /aidlc to begin.", "Run /aidlc, ask Sam."))
    committed(up, repo, "upgrade")
    (root / AGENTS).write_bytes((root / AGENTS).read_bytes() + b"\nAnd one more.\n")
    plan = up.installer.preview(reload_repo(studio, up.store), "rollback")
    assert plan.blocking and entry_of(plan, AGENTS).action == "rollback_conflict"
