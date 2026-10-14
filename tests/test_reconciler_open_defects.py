"""Regressions for open-defects 2.1 and 2.5, using real disk, storage and HostBridge reads."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import fixtures as F
import pytest

from test_reconciler import (  # noqa: F401 - shared fixtures
    GATE_DIR, HUMAN_TURN_MARKER, R, SHARD, _record, append_audit, approve_on_disk, finish_turn, scene, svc,
    touch_human_turn,
)


def _reconcile(svc, action_id, snap=None):
    async def run():
        return await svc.reconciler.reconcile_action(await svc.actions.get(action_id), snap)

    return asyncio.run(run())


def _partial(svc, scene, tail_bytes: int):
    audit = svc.reader.read_audit(scene.root, "default", GATE_DIR, tail_bytes=tail_bytes)
    assert audit.complete is False
    return audit


@pytest.mark.parametrize("human_turns", [2, 3])
def test_confirmed_presence_anomaly_can_finish_without_rebasing_or_replay(svc, scene, fake_host, human_turns):
    """The frozen count must not strand a completed gate after its anomaly was escalated."""
    touch_human_turn(scene.root, mtime_ns=1_000_000_000)
    action_id = scene.make(status="Delivered", lease_generation=7)
    original = svc.storage.get("actions", action_id)
    approve_on_disk(scene.root, human_turns=human_turns)
    touch_human_turn(scene.root, mtime_ns=2_000_000_000)
    finish_turn(fake_host, scene.slot_key)
    transcript = list(scene.slot.messages)

    required = _reconcile(svc, action_id)
    assert required.status == "ReconciliationRequired"
    assert required.evidence["delivery_confirmed"] is True
    assert "human_presence_anomaly" in {f["code"] for f in required.evidence["findings"]}

    settled = _reconcile(svc, action_id)
    assert settled.status == "StateChanged"
    assert settled.resolution["evidence"]["presence"] == {
        "human_turn_delta": human_turns, "partial": False, "marker_advanced": True, "ok": True}
    assert settled.presence_baseline == original["presence_baseline_json"]
    assert settled.delivery_id == original["delivery_id"]
    assert scene.slot.messages == transcript
    assert [status for _, status, _ in svc.actions.transitions] == ["ReconciliationRequired", "StateChanged"]
    assert asyncio.run(svc.scheduler.get(scene.repo.resolved_identity)) is None
    assert asyncio.run(svc.actions.list_live(repo_id=scene.repo_id)) == []
    assert _reconcile(svc, action_id).status_generation == settled.status_generation


@pytest.mark.parametrize("extra_row,settles", [
    # KiroCrew resumed the turn after a backend error: its own prompt, recorded by AI-DLC as a HUMAN_TURN
    ({"role": "inject", "content": "[Interrupted turn — automatic recovery]\nContinue from where it stopped.",
      "cls": "msg msg-inject", "ts": "2026-09-04T09:59:40Z", "meta": {}}, True),
    ({"role": "subagent", "content": "Sub-agent result: done.", "cls": "msg", "ts": "2026-09-04T09:59:40Z",
      "meta": {}}, True),
    # a person wrote in the conversation beside the delivery: the extra turn is theirs, and stays an anomaly
    ({"role": "user", "content": "actually, reject it", "cls": "msg msg-u", "ts": "2026-09-04T09:59:40Z",
      "meta": {}}, False),
])
def test_a_prompt_kirocrew_wrote_itself_is_not_a_second_person(svc, scene, fake_host, extra_row, settles):
    """Round 5: KiroCrew's automatic recovery after a backend error made two HUMAN_TURNs for one answer."""
    touch_human_turn(scene.root, mtime_ns=1_000_000_000)
    action_id = scene.make(status="Delivered", lease_generation=7)
    approve_on_disk(scene.root, human_turns=2)
    touch_human_turn(scene.root, mtime_ns=2_000_000_000)
    finish_turn(fake_host, scene.slot_key)
    scene.slot.messages.insert(-1, extra_row)

    after = _reconcile(svc, action_id)
    if settles:
        assert after.status == "StateChanged"
        assert after.resolution["evidence"]["presence"]["machine_prompts"] == 1
    else:
        assert after.status == "ReconciliationRequired"
        assert "human_presence_anomaly" in {f["code"] for f in after.evidence["findings"]}


def test_extra_turns_with_no_transcript_stay_an_anomaly(svc, scene, fake_host):
    """A restarted gateway holds no transcript: nothing then says who submitted the second prompt."""
    touch_human_turn(scene.root, mtime_ns=1_000_000_000)
    action_id = scene.make(status="Delivered", lease_generation=7)
    approve_on_disk(scene.root, human_turns=2)
    touch_human_turn(scene.root, mtime_ns=2_000_000_000)
    finish_turn(fake_host, scene.slot_key)
    scene.slot.messages[:] = [row for row in scene.slot.messages if row["role"] == "assistant"]
    assert _reconcile(svc, action_id).status == "ReconciliationRequired"


@pytest.mark.parametrize("missing_proof", [
    "human_turn", "marker", "marker_advance", "marker_monotonicity", "baseline", "active_cursor", "gate_resolution",
    "new_gate_resolution", "complete_audit",
])
def test_confirmed_recovery_still_requires_presence_and_resolution_proofs(svc, scene, fake_host, missing_proof):
    """Confirmation alone cannot bless a forged approval or incomplete multi-turn history."""
    touch_human_turn(scene.root, mtime_ns=2_000_000_000)
    action_id = scene.make(status="ReconciliationRequired", presence=missing_proof != "baseline",
                           evidence_extra={"delivery_confirmed": True})
    if missing_proof != "gate_resolution":
        approve_on_disk(scene.root, human_turns=0 if missing_proof == "human_turn" else 2,
                        ts="2026-09-04T09:58:00Z" if missing_proof == "new_gate_resolution" else "2026-09-04T10:00:00Z")
    touch_human_turn(scene.root, mtime_ns=3_000_000_000)
    if missing_proof == "marker":
        (_record(scene.root) / HUMAN_TURN_MARKER).unlink()
    elif missing_proof in ("marker_advance", "marker_monotonicity"):
        touch_human_turn(scene.root, mtime_ns=2_000_000_000 if missing_proof == "marker_advance" else 1_000_000_000)
    elif missing_proof == "active_cursor":
        (_record(scene.root).parent / "active-intent").write_text("another-intent\n")
    finish_turn(fake_host, scene.slot_key)
    snap = scene.snapshot()
    if missing_proof == "complete_audit":
        snap = replace(snap, audit=_partial(svc, scene, (_record(scene.root) / "audit" / SHARD).stat().st_size - 1))

    for _ in range(2):
        rec = _reconcile(svc, action_id, snap)
        assert rec.status == "ReconciliationRequired"
        assert rec.resolution is None
        assert rec.evidence["delivery_confirmed"] is True
    assert svc.actions.transitions == []


@pytest.mark.parametrize("history", ["new_with_smaller_tail", "old_timestamp", "before_boundary", "stale_marker"])
def test_partial_audit_keeps_dispatch_order_and_marker_requirements(svc, scene, history):
    """Partial counts are not totals: only a new, ordered event plus the marker can resolve."""
    shard = append_audit(scene.root, *(F.audit_block("HUMAN_TURN", "2026-09-04T09:30:00Z") for _ in range(12)))
    touch_human_turn(scene.root, mtime_ns=1_000_000_000)
    snap = scene.snapshot()
    action_id = scene.make(status="ReconciliationRequired", evidence_extra={"delivery_confirmed": True},
                           snap=replace(snap, audit=_partial(svc, scene, shard.stat().st_size - 1)))
    before_text = shard.read_text()
    approve_on_disk(scene.root, human_turns=2 if history in ("new_with_smaller_tail", "stale_marker") else 0)
    touch_human_turn(scene.root, mtime_ns=1_000_000_000 if history == "stale_marker" else 2_000_000_000)
    if history == "old_timestamp":
        append_audit(scene.root, *(F.audit_block("HUMAN_TURN", "2026-09-04T09:58:00Z") for _ in range(2)))
    elif history == "before_boundary":
        (shard.parent / "000-earlier.md").write_text(
            F.audit_text(*(F.audit_block("HUMAN_TURN", "2026-09-04T10:00:00Z") for _ in range(2))))
    elif history == "new_with_smaller_tail":
        # Keep new events in a later shard: tail offsets in the old shard are not absolute offsets.
        appended = shard.read_text()[len(before_text):]
        shard.write_text(before_text)
        (shard.parent / "zz-completed.md").write_text(appended)
    snap = scene.snapshot()
    if history == "new_with_smaller_tail":
        snap = replace(snap, audit=_partial(svc, scene, len(appended.encode()) + 16))

    rec = _reconcile(svc, action_id, snap)
    assert rec.presence_baseline["human_turn_events_partial"] is True
    if history == "new_with_smaller_tail":
        assert rec.status == "StateChanged"
        presence = rec.resolution["evidence"]["presence"]
        assert presence["human_turn_delta"] < 0
        assert presence["new_human_turns"] == 2 and presence["partial"] is True
    else:
        assert rec.status == "ReconciliationRequired"
        presence = {f["code"]: f["params"] for f in rec.evidence["findings"]}["human_presence_anomaly"]
        assert presence["partial"] is True
        if history == "stale_marker":
            assert presence["marker_advanced"] is False
        else:
            assert presence["human_turn_delta"] == 2 and presence["new_human_turns"] == 0


class UnreadableTranscript:
    def __iter__(self):
        raise RuntimeError("transcript changed during read")


def _break_transcript(monkeypatch, slot, failure):
    # Host slot metadata remains usable even when its separate transcript surface drifts.
    payload = slot.to_dict()
    monkeypatch.setattr(slot, "to_dict", lambda: dict(payload))
    monkeypatch.setattr(slot, "messages", None if failure == "missing" else UnreadableTranscript())


@pytest.mark.parametrize("failure", ["missing", "unreadable"])
@pytest.mark.parametrize("location", ["bound_only", "bound_before_healthy", "other_before_healthy"])
def test_failed_transcript_read_cannot_authorize_not_delivered(svc, scene, fake_host, monkeypatch, failure, location):
    """A later healthy slot cannot erase an earlier failed absence check."""
    action_id = scene.make(status="ReconciliationRequired")
    broken = scene.slot
    if location == "other_before_healthy":
        broken = fake_host.add_slot("other-broken", project=str(scene.root), agent="aidlc")
    if location != "bound_only":
        fake_host.add_slot("other-healthy", project=str(scene.root), agent="aidlc")
    _break_transcript(monkeypatch, broken, failure)
    assert svc.host.can_prove_absence() is True  # the last probe succeeded; this read has not happened

    rec = _reconcile(svc, action_id)
    evidence = rec.evidence
    assert svc.host.capabilities()["slots"].available is True
    assert evidence["slot_ran_since"] is False
    assert evidence["disk_baseline_unchanged"] is True and evidence["boot_id_unchanged"] is True
    assert evidence["transcript_row"] is None and evidence["transcript_row_absent"] is False
    assert rec.status == "ReconciliationRequired"
    assert svc.actions.transitions == []

    # The next successful read can prove absence; failure is sticky only within one observation.
    monkeypatch.setattr(broken, "messages", [])
    rec = _reconcile(svc, action_id)
    assert rec.status == "NotDelivered"
    assert rec.evidence["transcript_row_absent"] is True


@pytest.mark.parametrize("proof", ["restart", "slot_ran", "disk_moved", "confirmed"])
def test_readable_transcript_alone_still_cannot_prove_non_delivery(svc, scene, proof):
    action_id = scene.make(status="ReconciliationRequired", boot_id="another-boot" if proof == "restart" else svc.boot_id,
                           evidence_extra={"delivery_confirmed": True} if proof == "confirmed" else None)
    if proof == "slot_ran":
        scene.slot.running = True
    elif proof == "disk_moved":
        touch_human_turn(scene.root, mtime_ns=2_000_000_000)
    rec = _reconcile(svc, action_id)
    assert rec.evidence["transcript_row_absent"] is True
    assert rec.status == "ReconciliationRequired"
    assert svc.actions.transitions == []


def test_real_delivery_in_another_slot_survives_a_failed_bound_read(svc, scene, fake_host, monkeypatch):
    action_id = scene.make(status="ReconciliationRequired")
    other = fake_host.add_slot("other-delivered", project=str(scene.root), agent="aidlc")
    finish_turn(fake_host, other.key)
    _break_transcript(monkeypatch, scene.slot, "unreadable")
    rec = _reconcile(svc, action_id)
    assert rec.status == "ReconciliationRequired"
    assert rec.evidence["wrong_slot"] == other.key
    assert rec.evidence["transcript_row"]["slot_key"] == other.key
    assert rec.evidence["transcript_row_absent"] is False
    assert "wrong_slot" in {f["code"] for f in rec.evidence["findings"]}
    assert svc.actions.transitions == []
