"""A queued Run must not outlive the work or displace a human checkpoint."""

import asyncio
import os
from dataclasses import replace

import fixtures as F
import pytest

from test_e2e_gate import (  # noqa: F401
    INTENT, STAGE, NEXT_STAGE, QUESTIONS_REL, _gate_state, _get, _post, _queue,
    _finish_the_turn, _record_dir, _report, _rescan, _shard, _submit, _tree_digest, scene,
)
from test_handlers_auth import common, routes, sv  # noqa: F401


@pytest.fixture
def repo_dir(repo_builder):
    return repo_builder.with_engine("payload").with_workspace().with_intent(
        INTENT, state=_gate_state().replace(f"[?] {STAGE}", f"[-] {STAGE}"),
        audit=F.audit_text(
            F.audit_block("HUMAN_TURN", "2026-09-04T09:00:00Z"),
            F.audit_block("STAGE_STARTED", "2026-09-04T09:00:01Z", Stage=STAGE),
        ),
        directive={"version": 1, "stage": STAGE, "state_sha256": "AUTO"},
        artifacts={f"inception/{STAGE}/requirements.md": "# Requirements\n\n## Review\n\n**Verdict**: READY\n"},
    ).with_git().build()


def command(sv, routes, fake_host, scene, kind="run"):
    return _post(sv, routes, fake_host, f"/repos/{scene.repo_id}/intents/{INTENT}/{kind}")


def checkpoint(scene, kind):
    record = _record_dir(scene.root)
    if kind == "gate":
        (record / "aidlc-state.md").write_text(_gate_state())
        path = record / QUESTIONS_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(F.questions_text(
            stage=STAGE, questions=[("Which store?", ["SQLite", "Postgres"])], answers={1: "A"},
        ))
        with _shard(scene.root).open("a") as file:
            file.write(F.audit_block("STAGE_AWAITING_APPROVAL", "2026-09-04T09:59:00Z", Stage=STAGE))
    elif kind in ("questions", "summary", "plan"):
        path = record / QUESTIONS_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        text = F.questions_text(stage=STAGE, questions=[("Which store?", ["SQLite", "Postgres"])])
        if kind != "questions":
            heading = "Consolidated Summary Confirmation" if kind == "summary" else "Plan Approval"
            text = f"# Questions\n\n## {heading}\n\n[Answer]:\n"
        path.write_text(text)
    elif kind == "stage":
        text = (record / "aidlc-state.md").read_text().replace(f"[-] {STAGE}", f"[x] {STAGE}")
        text = text.replace(f"**Current Stage**: {STAGE}", f"**Current Stage**: {NEXT_STAGE}")
        (record / "aidlc-state.md").write_text(text)
    elif kind == "completed":
        path = record / "aidlc-state.md"
        path.write_text(path.read_text().replace("**Status**: Running", "**Status**: Completed"))


@pytest.mark.parametrize("kind", ["gate", "questions", "summary", "plan", "stage", "completed"])
@pytest.mark.parametrize("path", ["scan", "submit"])
def test_queued_run_is_retired_when_its_purpose_has_expired(sv, routes, fake_host, scene, kind, path):
    status, created = command(sv, routes, fake_host, scene)
    assert status == 201, created
    card = created["action"]
    checkpoint(scene, kind)
    before = _tree_digest(scene.root)
    if path == "scan":
        _rescan(sv, routes, fake_host, scene)
    else:
        status, body = _submit(sv, routes, fake_host, card, payload={"decision": "run"}, wire_text="/aidlc")
        assert status == 409 and body["code"] == "run_not_applicable", body
    record = asyncio.run(sv.actions.get(card["action_id"]))
    assert record.status == "Cancelled"
    assert record.delivery_id is None and record.delivering_at is None
    assert record.evidence["superseded"] is True
    cards = _queue(sv, routes, fake_host)
    assert card["action_id"] not in [a["action_id"] for a in cards]
    if path == "scan" and kind in ("gate", "questions", "summary", "plan"):
        assert any(a["type"] == ("gate" if kind == "gate" else "question") for a in cards)
    assert not sv.storage.lease_list()
    assert fake_host.get_slot(scene.slot_key).messages == []
    assert _tree_digest(scene.root) == before


@pytest.mark.parametrize("kind", ["gate", "questions", "summary", "plan", "completed"])
def test_checkpoint_refuses_new_run_before_creating_a_record(sv, routes, fake_host, scene, kind):
    checkpoint(scene, kind)
    before = len(sv.storage.select("actions"))
    status, body = command(sv, routes, fake_host, scene)
    assert status == 409 and body["code"] == "run_not_applicable", body
    assert len(sv.storage.select("actions")) == before


def test_running_session_does_not_accumulate_another_run(sv, routes, fake_host, scene):
    fake_host.get_slot(scene.slot_key).running = True
    before = len(sv.storage.select("actions"))
    status, body = command(sv, routes, fake_host, scene)
    assert status == 409 and body["code"] == "slot_busy", body
    assert len(sv.storage.select("actions")) == before
    assert fake_host.get_slot(scene.slot_key).running is True


def test_unstable_snapshot_cannot_retire_a_run(sv, routes, fake_host, scene):
    _, created = command(sv, routes, fake_host, scene)
    checkpoint(scene, "gate")
    repo = sv.repos.get(scene.repo_id)
    snap = replace(sv.projection.snapshot(repo, "default", INTENT), stable=False)
    record = asyncio.run(sv.actions.get(created["action_id"]))
    asyncio.run(sv.reconciler.reconcile_action(record, snap))
    assert asyncio.run(sv.actions.get(record.action_id)).status == "Queued"


def test_dispatched_run_is_never_cancelled_by_checkpoint_cleanup(sv, routes, fake_host, scene):
    _, created = command(sv, routes, fake_host, scene)
    status, receipt = _submit(
        sv, routes, fake_host, created["action"], payload={"decision": "run"}, wire_text="/aidlc",
    )
    assert status == 200, receipt
    fake_host.get_slot(scene.slot_key).running = True
    checkpoint(scene, "gate")
    _rescan(sv, routes, fake_host, scene)
    record = asyncio.run(sv.actions.get(created["action_id"]))
    assert record.status != "Cancelled" and record.delivery_id == receipt["delivery_id"]
    assert sv.storage.lease_list()


def test_checkpoint_appearing_under_lease_refuses_the_send(sv, routes, fake_host, scene, monkeypatch):
    _, created = command(sv, routes, fake_host, scene)
    switch = sv.engine.switch_cursor_sync

    def switch_then_ask(*args, **kwargs):
        result = switch(*args, **kwargs)
        checkpoint(scene, "questions")
        return result

    monkeypatch.setattr(sv.engine, "switch_cursor_sync", switch_then_ask)
    status, body = _submit(
        sv, routes, fake_host, created["action"], payload={"decision": "run"}, wire_text="/aidlc",
    )
    assert status == 409 and body["code"] == "run_not_applicable", body
    record = asyncio.run(sv.actions.get(created["action_id"]))
    assert record.status == "Cancelled" and record.delivery_id is None
    assert not sv.storage.lease_list()
    assert fake_host.get_slot(scene.slot_key).messages == []


def test_cleanup_cannot_cancel_a_concurrent_dispatch(sv, routes, fake_host, scene, monkeypatch):
    _, created = command(sv, routes, fake_host, scene)
    checkpoint(scene, "gate")
    transition = sv.actions.transition

    async def dispatch_first(action_id, to_status, **kwargs):
        if to_status == "Cancelled" and action_id == created["action_id"]:
            sv.storage.cas_update("actions", action_id, kwargs["expected_generation"],
                                  {"status": "Delivering", "delivery_id": "dl_racing_dispatch"})
        return await transition(action_id, to_status, **kwargs)

    monkeypatch.setattr(sv.actions, "transition", dispatch_first)
    asyncio.run(sv.reconciler.reconcile_now(created["action_id"]))
    record = asyncio.run(sv.actions.get(created["action_id"]))
    assert record.status == "Delivering" and record.delivery_id == "dl_racing_dispatch"


def test_revision_still_allows_agent_work(sv, routes, fake_host, scene):
    path = _record_dir(scene.root) / "aidlc-state.md"
    path.write_text(path.read_text().replace(f"[-] {STAGE}", f"[R] {STAGE}"))
    status, created = command(sv, routes, fake_host, scene)
    assert status == 201, created
    _rescan(sv, routes, fake_host, scene)
    assert asyncio.run(sv.actions.get(created["action_id"])).status == "Queued"


def test_studio_reload_preserves_an_active_conversation_and_its_lease(
    sv, routes, fake_host, scene, studio, fake_bun, fake_git,
):
    _, created = command(sv, routes, fake_host, scene)
    status, receipt = _submit(
        sv, routes, fake_host, created["action"], payload={"decision": "run"}, wire_text="/aidlc",
    )
    assert status == 200, receipt
    slot = fake_host.get_slot(scene.slot_key)
    slot.running = True
    assert _report(sv, routes, fake_host, created["action_id"], receipt)[0] == 200
    lease = sv.storage.lease_list()[0]
    before = _tree_digest(scene.root)

    async def reload():
        await sv.stop()
        restored = studio.services.Services.build(
            sv.ctx, clock=sv.clock, ids=sv.ids, bun_path=fake_bun, git_path=fake_git,
            app_root=sv.app_root, boot_id="reloaded-studio", bun_version=lambda _: "1.1.42",
        )
        restored.storage.open()
        restored.settings.load()
        restored.host.attach(fake_host)
        try:
            await restored.reconciler.startup()
            record = await restored.actions.get(created["action_id"])
            assert record.status in ("Delivered", "Processing")
            after = restored.storage.lease_list()[0]
            assert (after.action_id, after.generation) == (lease.action_id, lease.generation)
            assert slot.running is True and slot.messages == []
            assert _tree_digest(scene.root) == before
        finally:
            await restored.stop()

    asyncio.run(reload())


def test_cold_start_cannot_treat_plan_approval_as_an_ended_turn(
    sv, routes, fake_host, scene, studio, fake_bun, fake_git,
):
    checkpoint(scene, "plan")
    _rescan(sv, routes, fake_host, scene)
    card = next(a for a in _queue(sv, routes, fake_host) if a["type"] == "question")
    status, receipt = _submit(
        sv, routes, fake_host, card, payload={"decision": "approve_plan"}, wire_text="Approve Plan",
    )
    assert status == 200, receipt
    slot = fake_host.get_slot(scene.slot_key)
    slot.running = True
    assert _report(sv, routes, fake_host, card["action_id"], receipt)[0] == 200
    record = _record_dir(scene.root)
    path = record / QUESTIONS_REL
    path.write_text(path.read_text().replace("[Answer]:", "[Answer]: Approve Plan"))
    with _shard(scene.root).open("a") as file:
        file.write(F.audit_text(
            F.audit_block("HUMAN_TURN", "2026-09-04T10:00:20Z"),
            F.audit_block("PLAN_APPROVAL_RECORDED", "2026-09-04T10:00:20Z",
                          Stage=STAGE, Details="Approve Plan"),
        ))
    marker = record / ".aidlc-human-turn"
    marker.write_text("2026-09-04T10:00:20Z\n")
    os.utime(marker, ns=(2_000_000_000, 2_000_000_000))
    lease = sv.storage.lease_list()[0]

    async def cold_start():
        await sv.stop()
        restored = studio.services.Services.build(
            sv.ctx, clock=sv.clock, ids=sv.ids, bun_path=fake_bun, git_path=fake_git,
            app_root=sv.app_root, boot_id="host-not-attached-yet", bun_version=lambda _: "1.1.42",
        )
        restored.storage.open()
        restored.settings.load()
        try:
            await restored.reconciler.startup()
            current = await restored.actions.get(card["action_id"])
            assert current.status in ("Delivered", "Processing")
            assert restored.storage.lease_list()[0].generation == lease.generation
            restored.host.attach(fake_host)
            await restored.reconciler.reconcile_now(card["action_id"])
            assert (await restored.actions.get(card["action_id"])).status in ("Delivered", "Processing")
            assert restored.storage.lease_list()
            _finish_the_turn(fake_host, scene.slot_key, wire_text="Approve Plan")
            await restored.reconciler.reconcile_now(card["action_id"])
            assert (await restored.actions.get(card["action_id"])).status == "ResolvedNoTransition"
            assert not restored.storage.lease_list()
        finally:
            await restored.stop()

    asyncio.run(cold_start())
