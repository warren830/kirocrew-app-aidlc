"""An undispatched command can be reviewed and submitted after its snapshot changes."""

import pytest

from test_actions import (  # noqa: F401
    A, C, INTENT, STAGE, bind, captured_of, repo_options, run, snapshot, world,
)

@pytest.fixture
def repo_options():
    return {"runnable": True}


def change_artifact(world, text="# Updated requirements\n"):
    path = world.builder.record(INTENT) / f"inception/{STAGE}/requirements.md"
    path.write_text(text)


@pytest.mark.parametrize("kind", ["run", "prepare_commit"])
def test_fresh_displayed_command_is_submittable_after_artifact_changes(world, A, C, kind):
    async def scenario():
        await bind(world)
        rec = await world.broker.create_command(kind, world.repo, "default", INTENT, snap=snapshot(world))
        change_artifact(world)
        card = await world.broker.card(rec, snapshot(world))
        assert card["captured"]["evidence_digest"] != rec.captured.evidence_digest
        wire = C.WIRE_RUN if kind == "run" else C.WIRE_PREPARE_COMMIT
        receipt = await world.broker.submit(
            rec.action_id, captured=card["captured"], payload={"decision": kind},
            client_wire_text=wire, user="owner-1",
        )
        stored = await world.broker.get(rec.action_id)
        assert stored.captured.evidence_digest == card["captured"]["evidence_digest"]
        assert stored.status == "Delivering"
        assert receipt.wire_text == wire
        frozen = await world.broker.card(stored, snapshot(world))
        assert frozen["evidence"]["artifacts"] == card["evidence"]["artifacts"]

    run(scenario())


def test_command_refresh_cannot_overwrite_a_concurrent_status_change(world, C, studio, monkeypatch):
    async def scenario():
        await bind(world)
        rec = await world.broker.create_command("run", world.repo, "default", INTENT, snap=snapshot(world))
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
            await world.broker.submit(
                rec.action_id, captured=card["captured"], payload={"decision": "run"},
                client_wire_text=C.WIRE_RUN, user="owner-1",
            )
        stored = await world.broker.get(rec.action_id)
        assert raced and stored.status == "Cancelled"
        assert stored.delivery_id is None
        assert stored.captured.evidence_digest == rec.captured.evidence_digest

    run(scenario())


def test_old_command_capture_is_refused_then_returned_fresh_card_can_submit(world, C, studio):
    async def scenario():
        await bind(world)
        rec = await world.broker.create_command("run", world.repo, "default", INTENT, snap=snapshot(world))
        change_artifact(world)
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.submit(
                rec.action_id, captured=rec.captured.to_json(), payload={"decision": "run"},
                client_wire_text=C.WIRE_RUN, user="owner-1",
            )
        assert exc.value.code == "action_stale"
        card = exc.value.details["card"]
        receipt = await world.broker.submit(
            rec.action_id, captured=card["captured"], payload={"decision": "run"},
            client_wire_text=C.WIRE_RUN, user="owner-1",
        )
        assert receipt.status == "Delivering"

    run(scenario())


def test_dispatched_command_retains_its_original_captured_evidence(world, C, studio):
    async def scenario():
        await bind(world)
        rec = await world.broker.create_command("run", world.repo, "default", INTENT, snap=snapshot(world))
        await world.broker.submit(
            rec.action_id, captured=rec.captured.to_json(), payload={"decision": "run"},
            client_wire_text=C.WIRE_RUN, user="owner-1",
        )
        change_artifact(world)
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.submit(
                rec.action_id, captured=world.projection.captured(snapshot(world), STAGE),
                payload={"decision": "run"}, client_wire_text=C.WIRE_RUN, user="owner-1",
            )
        assert exc.value.code == "action_not_submittable"
        stored = await world.broker.get(rec.action_id)
        assert stored.captured.evidence_digest == rec.captured.evidence_digest
        card = await world.broker.card(stored, snapshot(world))
        assert card["captured"]["evidence_digest"] == rec.captured.evidence_digest

    run(scenario())
