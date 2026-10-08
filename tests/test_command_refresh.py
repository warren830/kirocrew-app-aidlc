"""An undispatched command can be reviewed and submitted after its snapshot changes."""

import pytest

from _helpers_a import aio, refused
from test_actions import C, E, INTENT, STAGE, bind, snapshot, submit, world  # noqa: F401 - fixtures


@pytest.fixture
def repo_options():
    return {"runnable": True}


def change_artifact(world, text="# Updated requirements\n"):
    (world.builder.record(INTENT) / f"inception/{STAGE}/requirements.md").write_text(text)


async def command(world, kind="run"):
    await bind(world)
    return await world.broker.create_command(kind, world.repo, "default", INTENT, snap=snapshot(world))


@pytest.mark.parametrize("kind", ["run", "prepare_commit"])
@aio
async def test_fresh_displayed_command_is_submittable_after_artifact_changes(world, C, kind):
    rec = await command(world, kind)
    change_artifact(world)
    card = await world.broker.card(rec, snapshot(world))
    assert card["captured"]["evidence_digest"] != rec.captured.evidence_digest
    wire = C.WIRE_RUN if kind == "run" else C.WIRE_PREPARE_COMMIT
    receipt = await submit(world, rec, captured=card["captured"], payload={"decision": kind}, wire=wire)
    stored = await world.broker.get(rec.action_id)
    assert stored.captured.evidence_digest == card["captured"]["evidence_digest"]
    assert stored.status == "Delivering" and receipt.wire_text == wire
    frozen = await world.broker.card(stored, snapshot(world))
    assert frozen["evidence"]["artifacts"] == card["evidence"]["artifacts"]


@aio
async def test_command_refresh_cannot_overwrite_a_concurrent_status_change(world, C, studio, monkeypatch):
    rec = await command(world)
    change_artifact(world)
    card = await world.broker.card(rec, snapshot(world))
    cas = world.store.cas_update
    raced = False

    def concurrent_cancel(table, key, generation, values, **kwargs):
        nonlocal raced
        if table == "actions" and key == rec.action_id and "captured_state_hash" in values and not raced:
            raced = True
            cas(table, key, generation, {"status": "Cancelled"})
        return cas(table, key, generation, values, **kwargs)

    monkeypatch.setattr(world.store, "cas_update", concurrent_cancel)
    with pytest.raises(studio.storage.StaleGeneration):
        await submit(world, rec, captured=card["captured"], payload={"decision": "run"}, wire=C.WIRE_RUN)
    stored = await world.broker.get(rec.action_id)
    assert raced and stored.status == "Cancelled"
    assert stored.delivery_id is None
    assert stored.captured.evidence_digest == rec.captured.evidence_digest


@aio
async def test_old_command_capture_is_refused_then_returned_fresh_card_can_submit(world, C, E):
    rec = await command(world)
    change_artifact(world)
    with refused(E, "action_stale") as exc:
        await submit(world, rec, captured=rec.captured.to_json(), payload={"decision": "run"}, wire=C.WIRE_RUN)
    card = exc.value.details["card"]
    receipt = await submit(world, rec, captured=card["captured"], payload={"decision": "run"}, wire=C.WIRE_RUN)
    assert receipt.status == "Delivering"


@aio
async def test_dispatched_command_retains_its_original_captured_evidence(world, C, E):
    rec = await command(world)
    await submit(world, rec, captured=rec.captured.to_json(), payload={"decision": "run"}, wire=C.WIRE_RUN)
    change_artifact(world)
    with refused(E, "action_not_submittable"):
        await submit(world, rec, captured=world.projection.captured(snapshot(world), STAGE),
                     payload={"decision": "run"}, wire=C.WIRE_RUN)
    stored = await world.broker.get(rec.action_id)
    assert stored.captured.evidence_digest == rec.captured.evidence_digest
    card = await world.broker.card(stored, snapshot(world))
    assert card["captured"]["evidence_digest"] == rec.captured.evidence_digest
