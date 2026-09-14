"""The vertical slice: an open gate on disk becomes an approved stage, once (§4.3).

Every other test file proves one module keeps its own promise. This one drives the *whole* product
through its own HTTP surface — register a repository, let the scan find a boundary, read the card,
refuse a stale submit, commit a fresh one, report the browser's delivery, let the engine move the disk,
reconcile, and watch the card leave the queue — because the invariants that matter most live in the
seams:

* the durable ``Delivering`` record exists **before** the receipt is returned, and the host slot is
  provably untouched at that moment: the browser sends, never the backend (architecture A03, C24);
* the decision is at-most-once. The crash case at the bottom constructs the exact state a kill between
  the CAS and the delivery report leaves behind and proves startup reconciliation lands on a legal
  status *without* a second send and without ever reaching ``NotDelivered`` — which is the one wrong
  answer, because a replayed approval is a decision the human never made twice (C24/review R02);
* Studio wrote nothing into AI-DLC's own files. The whole repository is hashed before the flow and
  after it, and only the bytes the *fake engine* writes may differ.

Everything runs against fakes: ``fake_host`` for the dashboard, ``fake_bun`` for the engine, ``fake_git``
for git. The AI-DLC side of the story — the ``GATE_APPROVED`` audit rows, the state file moving on — is
applied by hand from the real harvested fixture formats, because that is exactly what the conductor
leaves behind and the reconciler has no other input.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path
from typing import Any

import pytest

import conftest as CT
import fixtures as F
from test_handlers_auth import APP_ROOT, BOOT, call, common, routes, sv  # noqa: F401 - fixtures

INTENT = "260904-gate-demo"
STAGE = "requirements-analysis"
NEXT_STAGE = "user-stories"
QUESTIONS_REL = f"inception/{STAGE}/{STAGE}-questions.md"
REQUIREMENTS_REL = f"inception/{STAGE}/requirements.md"
HUMAN_TURN_MARKER = ".aidlc-human-turn"

#: The second boot's id. Different from ``BOOT`` on purpose: it is what makes ``boot_id_unchanged``
#: false, and that single flag is what forbids ``NotDelivered`` after a restart (C24).
BOOT_AFTER_CRASH = "boot-test-9999"

#: The gate's wire text, read from the module that owns it rather than spelled here. It is a protocol
#: string: the conductor matches on it, so a literal in a test would keep passing after a change that
#: broke every real approval. ``test_constants.py`` is where it is pinned against
#: ``docs/design/wire-text.json``.
APPROVE = CT.studio_module("constants").WIRE_APPROVE


# --------------------------------------------------------------------------- #
# the scene
# --------------------------------------------------------------------------- #


def _gate_state(**fields: str) -> str:
    values = {
        "Current Stage": STAGE,
        "Status": "Running",
        "Next Stage": NEXT_STAGE,
        "Lifecycle Phase": "INCEPTION",
    }
    values.update(fields)
    return F.state_text(marks={STAGE: "?"}, fields=values)


@pytest.fixture
def repo_dir(repo_builder) -> Path:
    """One real AI-DLC workspace with one intent parked at an open approval gate."""
    return (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(
            INTENT,
            state=_gate_state(),
            audit=F.gate_open_audit(STAGE),
            directive={"version": 1, "stage": STAGE, "state_sha256": "AUTO"},
            questions={QUESTIONS_REL: F.blank_answers(F.real_questions())},
            artifacts={
                REQUIREMENTS_REL: (
                    "# Requirements\n\nThe ledger export must stream.\n\n"
                    "## Review\n\n**Verdict**: READY\n"
                ),
            },
        )
        .with_git()
        .build()
    )


def _record_dir(root: Path) -> Path:
    return root / "aidlc" / "spaces" / "default" / "intents" / INTENT


def _shard(root: Path) -> Path:
    return next((_record_dir(root) / "audit").glob("*.md"))


def _tree_digest(root: Path) -> dict[str, str]:
    """sha256 of every file under ``aidlc/``, so "Studio edited nothing" is checkable, not asserted."""
    out: dict[str, str] = {}
    for path in sorted((root / "aidlc").rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _engine_approves_the_gate(root: Path, *, human_turns: int = 1,
                              ts: str = "2026-09-04T10:00:20Z") -> dict[str, str]:
    """What AI-DLC leaves on disk after it acts on an ``Approve``.

    The three rows land in the same second, which is the case review P23 exists for: a resolution rule
    keyed on ``ts > delivered_at`` would miss its own approval. ``.aidlc-human-turn`` is touched because
    its mtime — not the audit — is the presence fact the invariant reads (C23).
    """
    blocks = [F.audit_block("HUMAN_TURN", ts) for _ in range(human_turns)]
    blocks.append(F.audit_block("GATE_APPROVED", ts, Stage=STAGE, User_Input=APPROVE))
    blocks.append(F.audit_block("STAGE_COMPLETED", ts, Stage=STAGE))
    blocks.append(F.audit_block("STAGE_STARTED", ts, Stage=NEXT_STAGE))
    shard = _shard(root)
    shard.write_text(shard.read_text() + F.audit_text(*blocks))
    # `Lifecycle Phase` is restated because the base fixture is a *completed* intent: leaving it alone
    # would put `user-stories` in the wrong phase and the scan would raise `phase_stage_disagreement`,
    # which is a real blocking finding — and a fixture that trips it would be testing the wrong thing.
    (_record_dir(root) / "aidlc-state.md").write_text(
        F.state_text(
            marks={STAGE: "x"},
            fields={
                "Current Stage": NEXT_STAGE,
                "Status": "Running",
                "Next Stage": "domain-model",
                "Lifecycle Phase": "INCEPTION",
            },
        )
    )
    marker = _record_dir(root) / HUMAN_TURN_MARKER
    marker.write_text(f"{ts}\n")
    stamp = time.time_ns()
    os.utime(marker, ns=(stamp, stamp))
    return {
        rel: hashlib.sha256((root / rel).read_bytes()).hexdigest()
        for rel in (
            str(shard.relative_to(root)),
            str((_record_dir(root) / "aidlc-state.md").relative_to(root)),
            str(marker.relative_to(root)),
        )
    }


def _finish_the_turn(fake_host, slot_key: str, *, wire_text: str = APPROVE) -> None:
    """The transcript the host leaves after the turn: the user row it wrote, then the model's reply."""
    slot = fake_host.get_slot(slot_key)
    slot.messages.append(
        {"role": "user", "content": wire_text, "cls": "msg msg-u",
         "ts": "2026-09-04T10:00:05Z", "meta": {}}
    )
    slot.messages.append(
        {"role": "assistant", "content": "Stage approved.", "cls": "msg msg-a",
         "ts": "2026-09-04T10:00:25Z", "meta": {}}
    )
    slot.running = False


@pytest.fixture
def scene(sv, routes, fake_host, repo_dir):
    """The whole slice's starting point, built the way a user builds it: through the API.

    Returned as a namespace rather than several fixtures because every step below needs the same four
    facts (the repo id, the slot key, the tree on disk, the digest before anything happened), and a
    test that rebuilt them would not be testing the same repository the previous step touched.
    """
    from types import SimpleNamespace

    status, before = call(
        sv, routes, "POST", "/repos/preflight",
        CT.owner_request("POST", "/repos/preflight", host=fake_host, body={"path": str(repo_dir)}),
    )
    assert status == 200, before

    status, body = call(
        sv, routes, "POST", "/repos",
        CT.owner_request("POST", "/repos", host=fake_host,
                         body={"path": str(repo_dir), "label": "ledger"}),
    )
    assert status == 201, body
    repo_id = body["repo"]["repo_id"]

    # The UI creates and binds the canonical slot; the host owns the slot, Studio only records the bond.
    slot_key = f"aidlc-studio-{repo_id}-{INTENT}"
    fake_host.add_slot(slot_key, project=str(repo_dir), agent="aidlc", app="")
    sv.host.attach(fake_host)
    bind = f"/repos/{repo_id}/intents/{INTENT}/session/bind"
    status, bound = call(
        sv, routes, "POST", bind,
        CT.owner_request("POST", bind, host=fake_host, body={"slot_key": slot_key}),
    )
    assert status == 200, bound

    return SimpleNamespace(
        repo_id=repo_id,
        repo=body["repo"],
        preflight_before=before["preflight"],
        preflight=body["preflight"],
        slot_key=slot_key,
        root=repo_dir,
        digest_before=_tree_digest(repo_dir),
    )


# --------------------------------------------------------------------------- #
# helpers that speak HTTP
# --------------------------------------------------------------------------- #


def _get(sv, routes, fake_host, path: str, **query: str) -> tuple[int, Any]:
    request = CT.owner_request("GET", path, host=fake_host, query=query or None)
    return call(sv, routes, "GET", path, request)


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
    path = f"/actions/{card['action_id']}/submit"
    return _post(sv, routes, fake_host, path, {
        "captured": captured if captured is not None else card["captured"],
        "payload": payload or {"decision": "approve"},
        "client_wire_text": wire_text,
    })


def _report(sv, routes, fake_host, action_id: str, receipt: dict, *,
            outcome: str = "delivered", http_status: int | None = 200,
            body: dict | None = None) -> tuple[int, Any]:
    path = f"/actions/{action_id}/delivery"
    return _post(sv, routes, fake_host, path, {
        "delivery_id": receipt["delivery_id"],
        "outcome": outcome,
        "http_status": http_status,
        "receipt": body if body is not None else {"ok": True, "slot": receipt["slot_key"], "mid": "m-1"},
    })


def _row(sv, action_id: str) -> dict:
    return dict(sv.storage.get("actions", action_id))


# --------------------------------------------------------------------------- #
# 1. the repository and the boundary
# --------------------------------------------------------------------------- #


def test_registering_the_repository_finds_the_engine_without_running_anything(scene, sv):
    """A preflight may read files; it may not execute a line of the candidate repo's code (C35).

    The repository is untrusted data until it is registered, and the payload it carries is TypeScript.
    ``fake_bun`` records every denied invocation, so an execution here would leave a line in that log.
    """
    assert scene.preflight_before["can_register"] is True
    assert scene.preflight_before["duplicate_of"] is None
    assert scene.preflight_before["aidlc"]["intents"] == 1
    assert scene.repo["availability"] == "available"
    assert scene.repo["install"]["engine_version"] == sv.payload.engine_version
    # The post-insert report is relative to the registry, so it now names the row it justified — which
    # is also what makes a second `POST /repos` for the same directory a `duplicate_identity`.
    assert scene.preflight["duplicate_of"] == scene.repo_id
    assert scene.preflight["can_register"] is False


def test_the_scan_derives_exactly_one_gate_card_from_the_open_boundary(sv, routes, fake_host, scene):
    scan = _rescan(sv, routes, fake_host, scene)
    assert scan["availability"] == "available"
    assert [i["intent_dir"] for i in scan["intents"]] == [INTENT]

    card = _gate_card(sv, routes, fake_host)
    assert card["status"] == "Queued"
    assert card["stage"]["slug"] == STAGE
    assert card["risk_class"] == "human_lane"
    assert card["priority_group"] == 2
    assert [d["decision"] for d in card["decisions"]] == ["approve", "request_changes"]
    assert card["deep_link"] == f"/apps/aidlc-studio?view=actions&action={card['action_id']}"


def test_the_card_carries_the_evidence_the_decision_needs(sv, routes, fake_host, scene):
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    assert card["evidence"]["state"]["current_stage"] == STAGE
    assert card["evidence"]["review"] is not None
    assert card["evidence"]["artifacts"], "the gate must show what it is approving"
    assert card["captured"]["state_hash"] and card["captured"]["evidence_digest"]
    assert card["captured"]["is_active"] is True


def test_a_second_scan_does_not_duplicate_the_card(sv, routes, fake_host, scene):
    """The boundary is one fact on disk; scanning twice must not put two of it in the queue (C33)."""
    _rescan(sv, routes, fake_host, scene)
    first = _gate_card(sv, routes, fake_host)
    _rescan(sv, routes, fake_host, scene)
    assert _gate_card(sv, routes, fake_host)["action_id"] == first["action_id"]


# --------------------------------------------------------------------------- #
# 2. the stale submit
# --------------------------------------------------------------------------- #


def test_a_submit_against_evidence_that_has_moved_is_refused_with_the_fresh_card(
    sv, routes, fake_host, scene
):
    """Compare-and-submit: the user approved *what they saw*, and the disk has since changed.

    Sending anyway would approve a stage on evidence nobody read. The refusal carries the refreshed
    card so the UI can show what moved instead of asking the user to guess.
    """
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)

    # The engine appended a question while the user was reading — the artifact digest moves with it.
    (_record_dir(scene.root) / REQUIREMENTS_REL).write_text(
        "# Requirements\n\nRewritten.\n\n## Review\n\n**Verdict**: NEEDS WORK\n"
    )
    _rescan(sv, routes, fake_host, scene)

    status, body = _submit(sv, routes, fake_host, card)
    assert status == 409, body
    assert body["code"] == "action_stale"
    assert body["details"]["card"]["action_id"] == card["action_id"]
    assert _row(sv, card["action_id"])["status"] == "Queued"
    assert fake_host.get_slot(scene.slot_key).messages == []


@pytest.mark.parametrize("field", ["state_hash", "boundary_token", "evidence_digest", "stage_attempt"])
def test_every_compared_field_on_its_own_makes_the_submit_stale(sv, routes, fake_host, scene, field):
    """Each member of the compare set is load-bearing; a set that ignored one would be a set of five."""
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    captured = dict(card["captured"])
    captured[field] = 99 if field == "stage_attempt" else "0" * 64
    status, body = _submit(sv, routes, fake_host, card, captured=captured)
    assert status == 409 and body["code"] == "action_stale", field


# --------------------------------------------------------------------------- #
# 3. the fresh submit — phase one of two
# --------------------------------------------------------------------------- #


def test_the_receipt_is_the_call_the_browser_must_make_and_the_record_is_already_durable(
    sv, routes, fake_host, scene
):
    """The single most important assertion in the suite.

    Three things are true at the same instant: the row says ``Delivering`` on disk, the receipt tells
    the browser exactly what to POST, and the host slot is empty. If the backend ever sent the message
    itself, the third would fail — and with it the whole at-most-once argument, because a crash before
    the row was written would leave a decision in the model's mouth that Studio has no record of.
    """
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    status, receipt = _submit(sv, routes, fake_host, card)
    assert status == 200, receipt

    assert receipt["ok"] is True
    assert receipt["lane"] == "human_lane"
    assert receipt["status"] == "Delivering"
    assert receipt["wire_text"] == APPROVE == "Approve"
    assert receipt["slot_key"] == scene.slot_key
    assert receipt["delivery_id"].startswith("dl_")
    assert receipt["host"] == {
        "method": "POST",
        "path": "/api/chat?ws=1",
        "body": {
            "message": APPROVE,
            "slot": scene.slot_key,
            "agent": "aidlc",
            "meta": {
                "studio_action_id": card["action_id"],
                "studio_delivery_id": receipt["delivery_id"],
            },
        },
    }

    row = _row(sv, card["action_id"])
    assert row["status"] == "Delivering"
    assert row["boot_id"] == BOOT
    assert row["delivery_id"] == receipt["delivery_id"]
    assert row["presence_baseline_json"], "no baseline means no way to prove a human turn happened"
    assert row["cursor_readback_json"], "the cursor was switched and read back before the CAS (C22)"

    slot = fake_host.get_slot(scene.slot_key)
    assert slot.messages == [] and slot.running is False


def test_the_cursor_was_switched_and_read_back_before_the_record_was_committed(
    sv, routes, fake_host, scene
):
    """The conductor resolves which record to write from the repo-shared cursor at every step (C22).

    So the read-back's state digest must be the digest the user's decision was captured against — if it
    is not, the decision would land on a different intent's record and nothing would ever say so.
    """
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    _status, _receipt = _submit(sv, routes, fake_host, card)

    fresh = _get(sv, routes, fake_host, f"/actions/{card['action_id']}")[1]["action"]
    readback = fresh["evidence"]["cursor_readback"]
    assert readback["ok"] is True
    assert readback["mismatch"] == []
    assert readback["state_sha256"] == card["captured"]["state_hash"]


def test_the_execution_lease_is_held_while_the_decision_is_in_flight(sv, routes, fake_host, scene):
    """One decision per repository at a time: a second turn would race the first for the state file."""
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    _status, receipt = _submit(sv, routes, fake_host, card)
    assert receipt["lease_generation"] == 1

    status, body = _get(sv, routes, fake_host, "/leases")
    assert status == 200
    execution = [row for row in body["leases"] if row["kind"] == "execution"]
    assert [row["repo_id"] for row in execution] == [scene.repo_id]
    assert execution[0]["action_id"] == card["action_id"]


def test_the_queue_shows_the_decision_as_in_flight_rather_than_waiting(sv, routes, fake_host, scene):
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    _submit(sv, routes, fake_host, card)
    live = _gate_card(sv, routes, fake_host)
    assert live["status"] == "Delivering"
    assert live["delivery"]["lane"] == "human_lane"
    assert live["delivery"]["wire_text"] == APPROVE
    assert live["delivery"]["delivering_at"]


# --------------------------------------------------------------------------- #
# 4. the browser reports back — phase three
# --------------------------------------------------------------------------- #


def test_the_delivery_report_moves_the_row_and_is_idempotent(sv, routes, fake_host, scene):
    """The browser may retry its report (a reload, a flaky connection); one delivery is one delivery."""
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    _status, receipt = _submit(sv, routes, fake_host, card)
    fake_host.append_user_row(scene.slot_key, APPROVE, "2026-09-04T10:00:05Z")

    status, first = _report(sv, routes, fake_host, card["action_id"], receipt)
    assert status == 200, first
    assert first["idempotent"] is False
    assert first["action"]["status"] in ("Delivered", "Processing")

    status, again = _report(sv, routes, fake_host, card["action_id"], receipt)
    assert status == 200
    assert again["idempotent"] is True
    assert again["action"]["status"] == first["action"]["status"]


def test_a_client_claiming_never_sent_is_refused_when_the_transcript_disagrees(
    sv, routes, fake_host, scene
):
    """``NotDelivered`` is proven, never believed (C24).

    The browser's own report is the *least* reliable witness in the system: it is the party that just
    crashed. With a matching row in the slot, the honest answer is uncertainty and a human.
    """
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    _status, receipt = _submit(sv, routes, fake_host, card)
    fake_host.append_user_row(scene.slot_key, APPROVE, "2026-09-04T10:00:05Z")

    status, body = _report(
        sv, routes, fake_host, card["action_id"], receipt,
        outcome="not_delivered", http_status=400, body={"error": "bad request"},
    )
    assert status == 409
    assert body["code"] == "not_delivered_unproven"
    assert body["details"]["checks"]["row_absent"] is False
    assert _row(sv, card["action_id"])["status"] == "DeliveryUncertain"


# --------------------------------------------------------------------------- #
# 5. the engine acts, the reconciler resolves, the card leaves
# --------------------------------------------------------------------------- #


@pytest.fixture
def delivered(sv, routes, fake_host, scene):
    """The state the slice is really about: a decision the host accepted, waiting on the engine."""
    from types import SimpleNamespace

    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    _status, receipt = _submit(sv, routes, fake_host, card)
    fake_host.append_user_row(scene.slot_key, APPROVE, "2026-09-04T10:00:05Z")
    status, body = _report(sv, routes, fake_host, card["action_id"], receipt)
    assert status == 200, body
    return SimpleNamespace(card=card, receipt=receipt, action_id=card["action_id"])


def test_the_reconciler_resolves_state_changed_once_the_engine_has_moved_the_disk(
    sv, routes, fake_host, scene, delivered
):
    """The resolution contract for ``gate/approve``: a new ``GATE_APPROVED`` *and* the row completed.

    Both halves are required. An audit row alone could be a retry of an older approval; a completed row
    alone could be a stage someone finished in a terminal. Only the pair is the decision Studio sent.
    """
    _engine_approves_the_gate(scene.root)
    _finish_the_turn(fake_host, scene.slot_key)

    asyncio.run(sv.reconciler.tick())

    row = _row(sv, delivered.action_id)
    assert row["status"] == "StateChanged"
    resolution = dict(row["resolution_json"] or {})
    assert resolution["kind"] == "state_changed"
    assert resolution["reason"] == "gate_approved"
    evidence = dict(row["evidence_json"] or {})
    presence = evidence["presence"]
    assert presence["ok"] is True
    # Exactly one new turn, and the marker moved. Two facts, because either alone can be forged: an
    # audit row is a file an agent can append to, and an mtime is a touch (C23/FR-SES-010).
    assert presence["human_turn_delta"] == 1
    assert presence["marker_advanced"] is True


def test_the_card_leaves_the_queue_when_the_stage_has_moved_on(
    sv, routes, fake_host, scene, delivered
):
    _engine_approves_the_gate(scene.root)
    _finish_the_turn(fake_host, scene.slot_key)
    asyncio.run(sv.reconciler.tick())

    cards = _queue(sv, routes, fake_host)
    assert delivered.action_id not in [card["action_id"] for card in cards]
    assert [card["type"] for card in cards if card["type"] == "gate"] == []

    status, body = _get(sv, routes, fake_host, f"/repos/{scene.repo_id}/intents/{INTENT}")
    assert status == 200
    assert body["intent"]["disk"]["current_stage"] == NEXT_STAGE
    # ``Idle``, not ``WaitingForYou``: the boundary is gone from disk and the slot's turn has finished,
    # so there is nothing for the human to do and nothing running. That is the whole point of the slice.
    assert body["intent"]["operational_state"] == "Idle"
    assert [f["code"] for f in body["intent"]["findings"] if f["severity"] == "blocking"] == []


def test_an_approval_that_nobody_was_present_for_is_not_a_resolution(
    sv, routes, fake_host, scene, delivered
):
    """Zero ``HUMAN_TURN`` rows means the model approved its own gate (FR-SES-010/C23).

    It is the anomaly the presence baseline exists for, and it must never resolve silently — the row
    goes to a human with the finding on it.
    """
    _engine_approves_the_gate(scene.root, human_turns=0)
    _finish_the_turn(fake_host, scene.slot_key)
    asyncio.run(sv.reconciler.tick())

    row = _row(sv, delivered.action_id)
    assert row["status"] != "StateChanged"
    codes = {
        str(f.get("code"))
        for f in (dict(row["evidence_json"] or {}).get("findings") or [])
        if isinstance(f, dict)
    }
    assert "human_presence_anomaly" in codes


def test_the_transition_history_is_the_whole_path_and_every_edge_is_legal(
    sv, routes, fake_host, scene, delivered, studio
):
    _engine_approves_the_gate(scene.root)
    _finish_the_turn(fake_host, scene.slot_key)
    asyncio.run(sv.reconciler.tick())

    status, body = _get(sv, routes, fake_host, f"/actions/{delivered.action_id}")
    assert status == 200
    path = [(t["from_status"], t["to_status"]) for t in body["transitions"]]
    assert path[0][0] == "Queued"
    assert path[-1][1] == "StateChanged"
    allowed = studio.constants.ALLOWED_TRANSITIONS
    for source, target in path:
        assert target in allowed.get(source, ()), (source, target)


# --------------------------------------------------------------------------- #
# 6. what the flow recorded, and what it must not have touched
# --------------------------------------------------------------------------- #


def test_the_flow_is_visible_in_the_activity_timeline(sv, routes, fake_host, scene, delivered):
    """A decision nobody can audit afterwards is a decision the product cannot stand behind."""
    _engine_approves_the_gate(scene.root)
    _finish_the_turn(fake_host, scene.slot_key)
    asyncio.run(sv.reconciler.tick())

    status, body = _get(sv, routes, fake_host, "/activity", limit="200")
    assert status == 200
    kinds = [item["kind"] for item in body["items"]]
    assert "repo.added" in kinds
    assert any(kind.startswith("action.") for kind in kinds)
    assert all(item["source"] != "aidlc" for item in body["items"]), "stored rows are Studio's own"
    for item in body["items"]:
        assert item["message_key"], item
        assert isinstance(item["params"], dict)


def test_the_merged_timeline_shows_the_engines_own_rows_beside_studios(
    sv, routes, fake_host, scene, delivered
):
    """Asked for one intent, the timeline merges the live AI-DLC audit in — never stored, always fresh."""
    _engine_approves_the_gate(scene.root)
    _finish_the_turn(fake_host, scene.slot_key)
    asyncio.run(sv.reconciler.tick())

    status, body = _get(
        sv, routes, fake_host, "/activity",
        repo=scene.repo_id, intent=INTENT, limit="200",
    )
    assert status == 200
    sources = {item["source"] for item in body["items"]}
    assert "aidlc" in sources and "studio" in sources


def test_the_flow_is_visible_on_the_event_stream(sv, routes, fake_host, scene, delivered):
    """The UI is driven by these events; a status the stream never mentions is a stale screen."""
    _engine_approves_the_gate(scene.root)
    _finish_the_turn(fake_host, scene.slot_key)
    asyncio.run(sv.reconciler.tick())

    status, body = _get(sv, routes, fake_host, "/events/poll", cursor="0", limit="500")
    assert status == 200
    types = [event["type"] for event in body["events"]]
    assert "action.created" in types
    assert "action.updated" in types
    assert body["cursor"] >= len(body["events"])
    assert body["reset"] is False

    published = [name for name, _data in sv.ctx.events.published]
    assert "aidlc-studio:action" in published, "the host bus must see the app's own declared name"


def test_studio_never_wrote_a_byte_of_ai_dlc_state(sv, routes, fake_host, scene, delivered):
    """The product's first promise, checked by hashing the workspace rather than by trusting the code.

    Only the files the *fake engine* rewrote may differ. Anything else — the intents registry, the
    questions file, the directive marker, another intent's record — would be Studio editing state it
    does not own.
    """
    engine_written = _engine_approves_the_gate(scene.root)
    _finish_the_turn(fake_host, scene.slot_key)
    asyncio.run(sv.reconciler.tick())

    after = _tree_digest(scene.root)
    changed = {
        rel
        for rel in set(scene.digest_before) | set(after)
        if scene.digest_before.get(rel) != after.get(rel)
    }
    # Set equality, and then digest equality: not only is every changed file one the fake engine wrote,
    # each one still holds exactly the bytes the engine left. An extra Studio append to an audit shard
    # would pass the first check and fail the second, which is the case worth catching.
    assert changed == set(engine_written), sorted(changed ^ set(engine_written))
    for rel, digest in engine_written.items():
        assert after[rel] == digest, rel


def test_no_denied_engine_verb_was_ever_invoked(sv, routes, fake_host, scene, delivered, denied_log):
    """``fake_bun`` writes a line for any denied tool or verb. The file must not exist at all."""
    _engine_approves_the_gate(scene.root)
    _finish_the_turn(fake_host, scene.slot_key)
    asyncio.run(sv.reconciler.tick())
    assert not denied_log.exists(), denied_log.read_text()


def test_the_machine_lane_refuses_the_same_decision_with_the_pinned_reason(sv, scene, delivered, studio):
    """There is no machine lane in v1: the broker refuses and records the refusal (S12)."""
    with pytest.raises(Exception) as raised:
        asyncio.run(sv.machine.dispatch(action_id=delivered.action_id))
    assert getattr(raised.value, "code", "") == "machine_lane_unavailable"
    assert raised.value.details["reason"] == studio.constants.MACHINE_LANE_REASON == "s12_unproven"

    rows = sv.storage.select("activity", {"kind": "machine_lane.refused"})
    assert len(rows) == 1, "the refusal must be on the record, not only raised at the caller"


# --------------------------------------------------------------------------- #
# 7. the crash path — killed between the CAS and the report
# --------------------------------------------------------------------------- #


def _restart(sv, studio, fake_ctx, clock, ids, fake_bun, fake_git, *, boot_id: str):
    """A second ``Services`` over the same data directory, with a new boot id and an empty host.

    That is exactly what the gateway hands the app after a restart: the durable rows survive, the host's
    in-memory slots do not, and ``boot_id`` is different. Building it here rather than reusing ``sv``
    is the point — a test that kept the same container could not tell the two processes apart.
    """
    fresh = studio.services.Services.build(
        fake_ctx,
        clock=clock,
        ids=ids,
        bun_path=fake_bun,
        git_path=fake_git,
        app_root=APP_ROOT,
        boot_id=boot_id,
        bun_version=lambda path: "1.1.42",
    )
    fresh.storage.open()
    fresh.settings.load()
    return fresh


def test_a_crash_between_the_cas_and_the_report_never_replays_the_decision(
    sv, routes, fake_host, scene, studio, fake_ctx, clock, ids, fake_bun, fake_git
):
    """The scenario the two-phase lane exists to survive.

    The row says ``Delivering`` and nothing else is known: the browser may have sent the approval and
    died before reporting, or may have died before sending. Studio cannot distinguish those from its
    own records — so it must not guess. ``NotDelivered`` would authorise a replay of an approval the
    conductor may already have acted on, which is the one outcome that is worse than doing nothing.

    Proven, not asserted: the second process's ``boot_id`` differs, so ``boot_id_unchanged`` is false,
    so the four-proof path is closed by construction (C24/review R02).
    """
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    _status, receipt = _submit(sv, routes, fake_host, card)
    assert _row(sv, card["action_id"])["status"] == "Delivering"

    # --- the kill. Nothing reported; the host's slot memory is gone with the process.
    sv.storage.close()
    after = _restart(sv, studio, fake_ctx, clock, ids, fake_bun, fake_git, boot_id=BOOT_AFTER_CRASH)
    empty_host = CT.FakeDashboardState()
    after.host.attach(empty_host)
    clock.advance(studio.constants.DELIVERY_ACK_DEADLINE_SECS + 30)

    report = asyncio.run(after.reconciler.startup())
    assert report.actions_reevaluated >= 1

    row = dict(after.storage.get("actions", card["action_id"]))
    assert row["status"] in ("DeliveryUncertain", "ReconciliationRequired"), row["status"]
    assert dict(row["evidence_json"] or {})["boot_id_unchanged"] is False

    # Two more ticks: the escalation must reach a state a human can act on and stop there.
    asyncio.run(after.reconciler.tick())
    asyncio.run(after.reconciler.tick())
    row = dict(after.storage.get("actions", card["action_id"]))
    assert row["status"] == "ReconciliationRequired"
    assert row["status"] != "NotDelivered"

    # And nothing was sent a second time. Three independent witnesses:
    #  * the host has no slot at all, so there is nowhere a message could have gone;
    #  * the delivery id is still the first one — a replay would mint a new one;
    #  * exactly one edge into `Delivering` was ever recorded, which is the dispatch count.
    assert empty_host.get_slot(scene.slot_key) is None
    assert row["delivery_id"] == receipt["delivery_id"]
    dispatches = [
        dict(t)
        for t in after.storage.select("action_transitions", {"action_id": card["action_id"]})
        if dict(t)["to_status"] == "Delivering"
    ]
    assert len(dispatches) == 1, dispatches
    after.storage.close()
    sv.storage.open()


def test_the_restarted_process_offers_the_human_a_resolution_rather_than_a_dead_end(
    sv, routes, fake_host, scene, studio, fake_ctx, clock, ids, fake_bun, fake_git
):
    """``ReconciliationRequired`` must be actionable: a status with no exit is a boundary nobody clears.

    The card the restarted process serves has to name the studio-only decisions a human can take, and
    ``mark_not_delivered`` must still be refused unless the backend can prove absence itself.
    """
    _rescan(sv, routes, fake_host, scene)
    card = _gate_card(sv, routes, fake_host)
    _submit(sv, routes, fake_host, card)

    sv.storage.close()
    after = _restart(sv, studio, fake_ctx, clock, ids, fake_bun, fake_git, boot_id=BOOT_AFTER_CRASH)
    after.host.attach(CT.FakeDashboardState())
    clock.advance(studio.constants.DELIVERY_ACK_DEADLINE_SECS + 30)
    asyncio.run(after.reconciler.startup())
    asyncio.run(after.reconciler.tick())
    asyncio.run(after.reconciler.tick())

    rec = asyncio.run(after.actions.get(card["action_id"]))
    body = asyncio.run(after.actions.card(rec, None))
    offered = {d["decision"] for d in body["decisions"]}
    assert offered, "a card with no decision is a dead end"
    assert offered <= set(studio.constants.DECISIONS["delivery_uncertain"])
    assert offered <= studio.constants.STUDIO_ONLY_DECISIONS
    assert "resubmit" in offered and "mark_not_delivered" in offered

    with pytest.raises(Exception) as raised:
        asyncio.run(
            after.actions.resolve(
                card["action_id"], decision="mark_not_delivered", payload={}, user="owner-1"
            )
        )
    assert getattr(raised.value, "code", "") in ("not_delivered_unproven", "invalid_decision")
    assert dict(after.storage.get("actions", card["action_id"]))["status"] != "NotDelivered"

    after.storage.close()
    sv.storage.open()
