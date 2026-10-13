"""HTTP vertical slice: an open gate on disk becomes an approved stage exactly once (§4.3); engine rows by hand."""

from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import conftest as CT
import fixtures as F
from _helpers_a import stamp_human_turn
from test_handlers_auth import APP_ROOT, BOOT, call, common, routes, sv  # noqa: F401 - fixtures

INTENT = "260904-gate-demo"
STAGE = "requirements-analysis"
NEXT_STAGE = "user-stories"
QUESTIONS_REL = f"inception/{STAGE}/{STAGE}-questions.md"
REQUIREMENTS_REL = f"inception/{STAGE}/requirements.md"
#: A different boot id makes ``boot_id_unchanged`` false, which alone forbids ``NotDelivered`` after a restart.
BOOT_AFTER_CRASH = "boot-test-9999"
#: The protocol string, read from its owner (pinned against wire-text.json in test_actions.py).
APPROVE = CT.studio_module("constants").WIRE_APPROVE


def _gate_state(**fields: str) -> str:
    values = {"Current Stage": STAGE, "Status": "Running", "Next Stage": NEXT_STAGE, "Lifecycle Phase": "INCEPTION"}
    return F.state_text(marks={STAGE: "?"}, fields={**values, **fields})


@pytest.fixture
def repo_dir(repo_builder) -> Path:
    """One real AI-DLC workspace with one intent parked at an open approval gate."""
    return repo_builder.with_engine("payload").with_workspace().with_intent(
        INTENT, state=_gate_state(), audit=F.gate_open_audit(STAGE),
        directive={"version": 1, "stage": STAGE, "state_sha256": "AUTO"},
        questions={QUESTIONS_REL: F.blank_answers(F.real_questions())},
        artifacts={REQUIREMENTS_REL: "# Requirements\n\nThe ledger export must stream.\n\n"
                                     "## Review\n\n**Verdict**: READY\n"},
    ).with_git().build()


def _record_dir(root: Path) -> Path:
    return root / "aidlc" / "spaces" / "default" / "intents" / INTENT


def _shard(root: Path) -> Path:
    return next((_record_dir(root) / "audit").glob("*.md"))


def _digest(root: Path, paths) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _tree_digest(root: Path) -> dict[str, str]:
    """sha256 of every file under ``aidlc/``, so "Studio edited nothing" is checked, not asserted."""
    return _digest(root, (path for path in sorted((root / "aidlc").rglob("*")) if path.is_file()))


def _engine_approves_the_gate(root: Path, *, human_turns: int = 1, ts: str = "2026-09-04T10:00:20Z") -> dict[str, str]:
    """What AI-DLC leaves after acting on ``Approve``: same-second rows (review P23) and a moved marker (C23)."""
    blocks = [F.audit_block("HUMAN_TURN", ts) for _ in range(human_turns)]
    blocks += [F.audit_block("GATE_APPROVED", ts, Stage=STAGE, User_Input=APPROVE),
               F.audit_block("STAGE_COMPLETED", ts, Stage=STAGE), F.audit_block("STAGE_STARTED", ts, Stage=NEXT_STAGE)]
    shard = _shard(root)
    shard.write_text(shard.read_text() + F.audit_text(*blocks))
    # `Lifecycle Phase` is restated, or `user-stories` lands in the wrong phase and trips a blocking finding.
    state = _record_dir(root) / "aidlc-state.md"
    state.write_text(F.state_text(marks={STAGE: "x"}, fields={
        "Current Stage": NEXT_STAGE, "Status": "Running", "Next Stage": "domain-model", "Lifecycle Phase": "INCEPTION"}))
    marker = stamp_human_turn(_record_dir(root), ts, ns=time.time_ns())
    return _digest(root, (shard, state, marker))


def _finish_the_turn(fake_host, slot_key: str, *, wire_text: str = APPROVE) -> None:
    """The transcript the host leaves after the turn: the user row it wrote, then the model's reply."""
    slot = fake_host.get_slot(slot_key)
    slot.messages += [
        {"role": "user", "content": wire_text, "cls": "msg msg-u", "ts": "2026-09-04T10:00:05Z", "meta": {}},
        {"role": "assistant", "content": "Stage approved.", "cls": "msg msg-a", "ts": "2026-09-04T10:00:25Z",
         "meta": {}},
    ]
    slot.running = False


@pytest.fixture
def scene(sv, routes, fake_host, repo_dir):
    """The slice's starting point, built the way a user builds it: preflight, register, bind the slot."""
    status, before = _post(sv, routes, fake_host, "/repos/preflight", {"path": str(repo_dir)})
    assert status == 200, before
    status, body = _post(sv, routes, fake_host, "/repos", {"path": str(repo_dir), "label": "ledger"})
    assert status == 201, body
    repo_id = body["repo"]["repo_id"]
    # The UI creates and binds the canonical slot; the host owns it, Studio only records the bond.
    slot_key = f"aidlc-studio-{repo_id}-{INTENT}"
    fake_host.add_slot(slot_key, project=str(repo_dir), agent="aidlc", app="")
    sv.host.attach(fake_host)
    status, bound = _post(sv, routes, fake_host, f"/repos/{repo_id}/intents/{INTENT}/session", {"slot_key": slot_key})
    assert status == 200, bound
    return SimpleNamespace(repo_id=repo_id, repo=body["repo"], preflight_before=before["preflight"],
                           preflight=body["preflight"], slot_key=slot_key, root=repo_dir,
                           digest_before=_tree_digest(repo_dir))


def _get(sv, routes, fake_host, path: str, **query: str) -> tuple[int, Any]:
    return call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host, query=query or None))


def _post(sv, routes, fake_host, path: str, body: dict | None = None) -> tuple[int, Any]:
    request = CT.owner_request("POST", path, host=fake_host, body=body if body is not None else {})
    return call(sv, routes, "POST", path, request)


def _rescan(sv, routes, fake_host, scene) -> dict:
    status, body = _post(sv, routes, fake_host, f"/repos/{scene.repo_id}/rescan")
    assert status == 200, body
    return body["scan"]


def _queue(sv, routes, fake_host) -> list[dict]:
    status, body = _get(sv, routes, fake_host, "/actions")
    assert status == 200, body
    return body["actions"]


def _gate_card(sv, routes, fake_host) -> dict:
    cards = [card for card in _queue(sv, routes, fake_host) if card["type"] == "gate"]
    assert len(cards) == 1, [c["type"] for c in _queue(sv, routes, fake_host)]
    return cards[0]


def _submit(sv, routes, fake_host, card: dict, *, captured: dict | None = None,
            payload: dict | None = None, wire_text: str = APPROVE) -> tuple[int, Any]:
    return _post(sv, routes, fake_host, f"/actions/{card['action_id']}/submit", {
        "captured": captured if captured is not None else card["captured"],
        "payload": payload or {"decision": "approve"}, "client_wire_text": wire_text})


def _report(sv, routes, fake_host, action_id: str, receipt: dict, *, outcome: str = "delivered",
            http_status: int | None = 200, body: dict | None = None) -> tuple[int, Any]:
    return _post(sv, routes, fake_host, f"/actions/{action_id}/delivery", {
        "delivery_id": receipt["delivery_id"], "outcome": outcome, "http_status": http_status,
        "receipt": body if body is not None else {"ok": True, "slot": receipt["slot_key"], "mid": "m-1"}})


def _row(sv, action_id: str) -> dict:
    return dict(sv.storage.get("actions", action_id))


@pytest.fixture
def submitted(sv, routes, fake_host, scene):
    """A scanned gate card and the receipt of its fresh submit: phase one of two."""
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    status, receipt = _submit(sv, routes, fake_host, card)
    assert status == 200, receipt
    return SimpleNamespace(card=card, receipt=receipt, action_id=card["action_id"])


@pytest.fixture
def delivered(sv, routes, fake_host, scene, submitted):
    """A decision the host accepted (its user row is in the slot), waiting on the engine."""
    fake_host.append_user_row(scene.slot_key, APPROVE, "2026-09-04T10:00:05Z")
    status, body = _report(sv, routes, fake_host, submitted.action_id, submitted.receipt)
    assert status == 200, body
    return submitted


@pytest.fixture
def approved(sv, fake_host, scene, delivered) -> dict[str, str]:
    """The engine approved the gate on disk and the turn ended; one tick ran. Returns the engine's digests."""
    written = _engine_approves_the_gate(scene.root)
    _finish_the_turn(fake_host, scene.slot_key)
    asyncio.run(sv.reconciler.tick())
    return written


# --- 1. the repository, the boundary, the stale submit ---


def test_registering_the_repository_finds_the_engine_without_running_anything(scene, sv):
    """A preflight may read the untrusted repository's files but never execute its code (C35)."""
    assert scene.preflight_before["can_register"] is True
    assert scene.preflight_before["duplicate_of"] is None
    assert scene.preflight_before["aidlc"]["intents"] == 1
    assert scene.repo["availability"] == "available"
    assert scene.repo["install"]["engine_version"] == sv.payload.engine_version
    # After the insert the report names the row it justified, so a second POST is a `duplicate_identity`.
    assert scene.preflight["duplicate_of"] == scene.repo_id
    assert scene.preflight["can_register"] is False


def test_the_scan_derives_exactly_one_gate_card_with_its_evidence_and_no_duplicate(sv, routes, fake_host, scene):
    scan = _rescan(sv, routes, fake_host, scene)
    assert scan["availability"] == "available"
    assert [i["intent_dir"] for i in scan["intents"]] == [INTENT]
    card = _gate_card(sv, routes, fake_host)
    assert card["status"] == "Queued" and card["stage"]["slug"] == STAGE
    assert card["risk_class"] == "human_lane" and card["priority_group"] == 2
    assert [d["decision"] for d in card["decisions"]] == ["approve", "request_changes"]
    assert card["deep_link"] == f"/apps/{CT.APP_NAME}?view=actions&action={card['action_id']}"
    assert card["evidence"]["state"]["current_stage"] == STAGE
    assert card["evidence"]["review"] is not None
    assert card["evidence"]["artifacts"], "the gate must show what it is approving"
    assert card["captured"]["state_hash"] and card["captured"]["evidence_digest"]
    assert card["captured"]["is_active"] is True
    # The boundary is one fact on disk: scanning twice must not queue two of it (C33).
    _rescan(sv, routes, fake_host, scene)
    assert _gate_card(sv, routes, fake_host)["action_id"] == card["action_id"]


def test_a_submit_against_evidence_that_has_moved_is_refused_with_the_fresh_card(sv, routes, fake_host, scene):
    """Compare-and-submit: the user approved what they saw, so a moved artifact refuses with the refreshed card."""
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    (_record_dir(scene.root) / REQUIREMENTS_REL).write_text(
        "# Requirements\n\nRewritten.\n\n## Review\n\n**Verdict**: NEEDS WORK\n")
    _rescan(sv, routes, fake_host, scene)
    status, body = _submit(sv, routes, fake_host, card)
    assert status == 409 and body["code"] == "action_stale", body
    assert body["details"]["card"]["action_id"] == card["action_id"]
    assert _row(sv, card["action_id"])["status"] == "Queued"
    assert fake_host.get_slot(scene.slot_key).messages == []


@pytest.mark.parametrize("field", ["state_hash", "boundary_token", "evidence_digest", "stage_attempt"])
def test_every_compared_field_on_its_own_makes_the_submit_stale(sv, routes, fake_host, scene, field):
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    captured = {**card["captured"], field: 99 if field == "stage_attempt" else "0" * 64}
    status, body = _submit(sv, routes, fake_host, card, captured=captured)
    assert status == 409 and body["code"] == "action_stale", field


# --- 2. the fresh submit, and the browser's report ---


def test_the_receipt_is_the_call_the_browser_must_make_and_the_record_is_already_durable(
    sv, fake_host, scene, submitted
):
    """At one instant the row is ``Delivering`` on disk, the receipt names the POST, and the slot is empty."""
    card, receipt = submitted.card, submitted.receipt
    assert receipt["ok"] is True and receipt["lane"] == "human_lane" and receipt["status"] == "Delivering"
    assert receipt["wire_text"] == APPROVE == "Approve"
    assert receipt["slot_key"] == scene.slot_key and receipt["delivery_id"].startswith("dl_")
    assert receipt["host"] == {"method": "POST", "path": "/api/chat?ws=1", "body": {
        "message": APPROVE, "slot": scene.slot_key, "agent": "aidlc",
        "meta": {"studio_action_id": card["action_id"], "studio_delivery_id": receipt["delivery_id"]}}}
    row = _row(sv, card["action_id"])
    assert (row["status"], row["boot_id"], row["delivery_id"]) == ("Delivering", BOOT, receipt["delivery_id"])
    assert row["presence_baseline_json"], "no baseline means no way to prove a human turn happened"
    assert row["cursor_readback_json"], "the cursor was switched and read back before the CAS (C22)"
    slot = fake_host.get_slot(scene.slot_key)
    assert slot.messages == [] and slot.running is False


def test_the_cursor_was_switched_and_read_back_before_the_record_was_committed(sv, routes, fake_host, submitted):
    """The read-back digest is the one the decision was captured against, or it lands on another intent (C22)."""
    fresh = _get(sv, routes, fake_host, f"/actions/{submitted.action_id}")[1]["action"]
    readback = fresh["evidence"]["cursor_readback"]
    assert readback["ok"] is True and readback["mismatch"] == []
    assert readback["state_sha256"] == submitted.card["captured"]["state_hash"]


def test_the_execution_lease_is_held_while_the_decision_is_in_flight(sv, routes, fake_host, scene, submitted):
    """One decision per repository at a time: a second turn would race the first for the state file."""
    status, body = _get(sv, routes, fake_host, f"/repos/{scene.repo_id}")
    assert status == 200
    execution = body["repo"]["leases"]["execution"]
    assert execution["action_id"] == submitted.action_id
    assert execution["generation"] == submitted.receipt["lease_generation"]
    assert body["repo"]["leases"]["admin"] is None
    counts = _get(sv, routes, fake_host, "/health")[1]["counts"]
    assert (counts["execution_leases"], counts["admin_leases"]) == (1, 0)


def test_the_queue_shows_the_decision_as_in_flight_rather_than_waiting(sv, routes, fake_host, submitted):
    live = _gate_card(sv, routes, fake_host)
    assert live["status"] == "Delivering"
    assert live["delivery"]["lane"] == "human_lane" and live["delivery"]["wire_text"] == APPROVE
    assert live["delivery"]["delivering_at"]


def test_the_delivery_report_moves_the_row_and_is_idempotent(sv, routes, fake_host, scene, submitted):
    """The browser may retry its report (a reload, a flaky connection); one delivery is one delivery."""
    fake_host.append_user_row(scene.slot_key, APPROVE, "2026-09-04T10:00:05Z")
    status, first = _report(sv, routes, fake_host, submitted.action_id, submitted.receipt)
    assert status == 200, first
    assert first["idempotent"] is False and first["action"]["status"] == "Delivered"
    status, again = _report(sv, routes, fake_host, submitted.action_id, submitted.receipt)
    assert status == 200 and again["idempotent"] is True
    assert again["action"]["status"] == first["action"]["status"]


def test_a_client_claiming_never_sent_is_refused_when_the_transcript_disagrees(
    sv, routes, fake_host, scene, submitted
):
    """``NotDelivered`` is proven, never believed: with a matching slot row the answer is a human (C24)."""
    fake_host.append_user_row(scene.slot_key, APPROVE, "2026-09-04T10:00:05Z")
    status, body = _report(sv, routes, fake_host, submitted.action_id, submitted.receipt, outcome="not_delivered",
                           http_status=400, body={"error": "bad request"})
    assert status == 409 and body["code"] == "not_delivered_unproven"
    assert body["details"]["checks"]["row_absent"] is False
    assert _row(sv, submitted.action_id)["status"] == "ReconciliationRequired"


# --- 3. the engine acts, the reconciler resolves, the card leaves ---


def test_the_reconciler_resolves_state_changed_once_the_engine_has_moved_the_disk(sv, delivered, approved):
    """``gate/approve`` needs a new ``GATE_APPROVED`` *and* the completed row: either alone could be forged."""
    row = _row(sv, delivered.action_id)
    assert row["status"] == "StateChanged"
    resolution = dict(row["resolution_json"] or {})
    assert (resolution["kind"], resolution["reason"]) == ("state_changed", "gate_approved")
    presence = dict(row["evidence_json"] or {})["presence"]
    # Exactly one new turn AND the marker moved: an audit row can be appended, an mtime touched (C23).
    assert presence["ok"] is True and presence["human_turn_delta"] == 1 and presence["marker_advanced"] is True


def test_the_card_leaves_the_queue_when_the_stage_has_moved_on(sv, routes, fake_host, scene, delivered, approved):
    cards = _queue(sv, routes, fake_host)
    assert delivered.action_id not in [card["action_id"] for card in cards]
    assert [card["type"] for card in cards if card["type"] == "gate"] == []
    status, body = _get(sv, routes, fake_host, f"/repos/{scene.repo_id}/intents/{INTENT}")
    assert status == 200 and body["intent"]["disk"]["current_stage"] == NEXT_STAGE
    # `Idle`, not `WaitingForYou`: the boundary left the disk and the turn ended, so nothing waits on the human.
    assert body["intent"]["operational_state"] == "Idle"
    assert [f["code"] for f in body["intent"]["findings"] if f["severity"] == "blocking"] == []


def test_an_approval_that_nobody_was_present_for_is_not_a_resolution(sv, fake_host, scene, delivered):
    """Zero ``HUMAN_TURN`` rows means the model approved its own gate: a finding for a human (FR-SES-010/C23)."""
    _engine_approves_the_gate(scene.root, human_turns=0)
    _finish_the_turn(fake_host, scene.slot_key)
    asyncio.run(sv.reconciler.tick())
    row = _row(sv, delivered.action_id)
    assert row["status"] != "StateChanged"
    findings = dict(row["evidence_json"] or {}).get("findings") or []
    assert "human_presence_anomaly" in {str(f.get("code")) for f in findings if isinstance(f, dict)}


def test_the_transition_history_is_the_whole_path_and_every_edge_is_legal(
    sv, routes, fake_host, studio, delivered, approved
):
    status, body = _get(sv, routes, fake_host, f"/actions/{delivered.action_id}")
    assert status == 200
    path = [(t["from_status"], t["to_status"]) for t in body["transitions"]]
    assert path[0][0] == "Queued" and path[-1][1] == "StateChanged"
    for source, target in path:
        assert target in studio.constants.ALLOWED_TRANSITIONS.get(source, ()), (source, target)


# --- 4. what the flow recorded, and what it must not have touched ---


def test_the_flow_is_visible_in_the_activity_timeline(sv, routes, fake_host, scene, approved):
    """Stored rows are Studio's own; asked for one intent, the live AI-DLC audit is merged in beside them."""
    status, body = _get(sv, routes, fake_host, "/activity", limit="200")
    assert status == 200
    kinds = [item["kind"] for item in body["items"]]
    assert "repo.added" in kinds and any(kind.startswith("action.") for kind in kinds)
    assert all(item["source"] != "aidlc" for item in body["items"]), "stored rows are Studio's own"
    for item in body["items"]:
        assert item["message_key"], item
        assert isinstance(item["params"], dict)
    assert any("action_id" in item["refs"] for item in body["items"]), "a live action's rows link to its card"
    status, body = _get(sv, routes, fake_host, "/activity", repo=scene.repo_id, intent=INTENT, limit="200")
    assert status == 200
    sources = {item["source"] for item in body["items"]}
    assert "aidlc" in sources and "studio" in sources


def test_the_flow_is_visible_on_the_event_stream(sv, routes, fake_host, approved):
    """The UI is driven by these events; a status the stream never mentions is a stale screen."""
    status, body = _get(sv, routes, fake_host, "/events/poll", cursor="0", limit="500")
    assert status == 200
    types = [event["type"] for event in body["events"]]
    assert "action.created" in types and "action.updated" in types
    assert body["cursor"] >= len(body["events"]) and body["reset"] is False
    published = [name for name, _data in sv.ctx.events.published]
    assert f"{CT.APP_NAME}:action" in published, "the host bus must see the app's own declared name"


def test_studio_never_wrote_a_byte_of_ai_dlc_state(scene, approved):
    """Only the files the fake engine rewrote may differ, and each must hold exactly the engine's bytes."""
    after = _tree_digest(scene.root)
    changed = {rel for rel in set(scene.digest_before) | set(after) if scene.digest_before.get(rel) != after.get(rel)}
    assert changed == set(approved), sorted(changed ^ set(approved))
    for rel, digest in approved.items():
        assert after[rel] == digest, rel


def test_no_denied_engine_verb_was_ever_invoked(approved, denied_log):
    """``fake_bun`` writes a line for any denied tool or verb; the file must not exist at all."""
    assert not denied_log.exists(), denied_log.read_text()


# --- 5. the crash path: killed between the CAS and the report ---


@pytest.fixture
def restarted(sv, studio, fake_ctx, clock, ids, fake_bun, fake_git, submitted):
    """Kill after the CAS, then a second ``Services`` on the same data dir (new boot, empty host, deadline past)."""
    assert _row(sv, submitted.action_id)["status"] == "Delivering"
    sv.storage.close()
    after = studio.services.Services.build(fake_ctx, clock=clock, ids=ids, bun_path=fake_bun, git_path=fake_git,
                                           app_root=APP_ROOT, boot_id=BOOT_AFTER_CRASH,
                                           bun_version=lambda path: "1.1.42")
    after.storage.open()
    after.settings.load()
    empty_host = CT.FakeDashboardState()
    after.host.attach(empty_host)
    clock.advance(studio.constants.DELIVERY_ACK_DEADLINE_SECS + 30)
    report = asyncio.run(after.reconciler.startup())
    yield SimpleNamespace(services=after, host=empty_host, report=report, **vars(submitted))
    after.storage.close()
    sv.storage.open()


def test_a_crash_between_the_cas_and_the_report_never_replays_the_decision(scene, restarted):
    """``NotDelivered`` would license a replay; the new boot id closes the four-proof path (C24/review R02)."""
    after = restarted.services
    assert restarted.report.actions_reevaluated >= 1
    row = dict(after.storage.get("actions", restarted.action_id))
    assert row["status"] == "ReconciliationRequired", row["status"]
    assert dict(row["evidence_json"] or {})["boot_id_unchanged"] is False
    # Two more ticks: the escalation must reach a state a human can act on and stop there.
    asyncio.run(after.reconciler.tick())
    asyncio.run(after.reconciler.tick())
    row = dict(after.storage.get("actions", restarted.action_id))
    assert row["status"] == "ReconciliationRequired"
    assert row["status"] != "NotDelivered"
    # Nothing was sent twice: no slot to send to, the first delivery id, one edge into `Delivering`.
    assert restarted.host.get_slot(scene.slot_key) is None
    assert row["delivery_id"] == restarted.receipt["delivery_id"]
    dispatches = [dict(t) for t in after.storage.select("action_transitions", {"action_id": restarted.action_id})
                  if dict(t)["to_status"] == "Delivering"]
    assert len(dispatches) == 1, dispatches


def test_the_restarted_process_offers_the_human_a_resolution_rather_than_a_dead_end(studio, restarted):
    """A ``ReconciliationRequired`` card names studio-only exits, and ``mark_not_delivered`` still needs proof."""
    after = restarted.services
    asyncio.run(after.reconciler.tick())
    asyncio.run(after.reconciler.tick())
    rec = asyncio.run(after.actions.get(restarted.action_id))
    offered = {d["decision"] for d in asyncio.run(after.actions.card(rec, None))["decisions"]}
    assert offered, "a card with no decision is a dead end"
    assert offered <= set(studio.constants.DECISIONS["delivery_uncertain"])
    assert offered <= studio.constants.STUDIO_ONLY_DECISIONS
    assert "resubmit" in offered and "mark_not_delivered" in offered
    with pytest.raises(Exception) as raised:
        asyncio.run(after.actions.resolve(restarted.action_id, decision="mark_not_delivered", payload={},
                                          user="owner-1"))
    assert getattr(raised.value, "code", "") in ("not_delivered_unproven", "invalid_decision")
    assert dict(after.storage.get("actions", restarted.action_id))["status"] != "NotDelivered"
