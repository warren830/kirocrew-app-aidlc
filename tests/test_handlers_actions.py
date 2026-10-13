"""Action Center routes (§2.4): §2.11 cards, ``submit`` never sends, proven ``not_delivered``, idempotent reports."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import fixtures as F
from conftest import APP_NAME
from test_e2e_gate import _get, _post
from test_handlers_auth import BOOT, common, routes, sv  # noqa: F401 - fixtures

INTENT = "260904-gate-demo"
REVISING = "260904-revising"
STAGE = "requirements-analysis"
QUESTIONS_REL = f"inception/{STAGE}/{STAGE}-questions.md"
CHECKS = {"row_absent", "slot_idle", "disk_baseline_unchanged", "boot_unchanged"}

#: §2.11, verbatim. Compared as a set equality on purpose: extra and missing keys are both failures.
ACTION_CARD_KEYS = {
    "action_id", "type", "queue_type", "status", "status_generation", "source", "risk_class",
    "severity", "priority_group", "repo", "space", "intent", "stage", "headline", "consequence",
    "waiting_since", "created_at", "updated_at", "primary", "decisions", "captured", "evidence",
    "delivery", "resolution", "acknowledged_evidence_sha256", "failure", "install", "budget",
    "advisor", "deep_link", "dedupe_key", "human_text_present", "submitted_answers",
}
EVIDENCE_KEYS = {
    "state", "audit", "directive", "artifacts", "review", "acceptance_criteria", "findings",
    "session", "questions", "git", "markers", "presence_baseline", "presence", "cursor_readback",
}
DELIVERY_KEYS = {
    "lane", "delivery_id", "slot_key", "session_key", "wire_text", "host", "delivering_at",
    "delivered_at", "queued_at", "deadline_at", "receipt", "outcome", "delivery_confirmed",
    "boot_id_unchanged", "transcript_row", "slot_ran_since", "disk_baseline_unchanged",
}


def _state(marks: dict[str, str] | None = None) -> str:
    return F.state_text(marks=marks or {STAGE: "?"}, fields={
        "Current Stage": STAGE, "Status": "Running", "Next Stage": "user-stories", "Lifecycle Phase": "INCEPTION",
        "Completed": "7"})


@pytest.fixture
def demo(repo_builder) -> Path:
    """One repository: an intent at an open gate, and a second one in a revision loop."""
    builder = repo_builder.with_engine("payload").with_workspace().with_intent(
        REVISING, state=_state(marks={STAGE: "r"}), audit=F.gate_rejected_audit(STAGE), active=False)
    return builder.with_intent(
        INTENT, state=_state(), audit=F.gate_open_audit(STAGE),
        directive={"version": 1, "stage": STAGE, "state_sha256": "AUTO"},
        questions={QUESTIONS_REL: F.blank_answers(F.real_questions())},
        artifacts={f"inception/{STAGE}/requirements.md":
                   "# Requirements\n\nOne requirement.\n\n## Review\n\n**Verdict**: READY\n"},
    ).with_git().build()


@pytest.fixture
def repo(sv, demo):
    return sv.repos.add(str(demo), "demo")


@pytest.fixture
def slot(sv, fake_host, repo):
    """The canonical slot, created by the UI and bound the way ``session/bind`` binds it."""
    key = f"aidlc-studio-{repo.repo_id}-{INTENT}"
    fake_host.add_slot(key, project=str(repo.canonical_path), agent="aidlc", app="")
    sv.host.attach(fake_host)
    asyncio.run(sv.sessions.bind(repo, "default", INTENT, slot_key=key, intent_uuid=None))
    return key


def _derive(sv: Any, repo: Any, intent_dir: str, action_type: str) -> Any:
    """One derived card, produced the way the reconciler produces it."""
    seeds = sv.projection.derive_cards(sv.projection.snapshot(repo, "default", intent_dir), findings=[], binding=None,
                                       install=None, breakers=[], live_actions=[], slot=None, host_card=None)
    wanted = [seed for seed in seeds if seed.type == action_type]
    assert wanted, f"the fixture produced no {action_type} seed (got {[s.type for s in seeds]})"
    return next(rec for rec in asyncio.run(sv.actions.upsert_derived(wanted)) if rec.type == action_type)


@pytest.fixture
def gate(sv, repo):
    return _derive(sv, repo, INTENT, "gate")


@pytest.fixture
def http(sv, routes, fake_host):
    """The real route table, called as the owner."""
    return SimpleNamespace(get=lambda path, **query: _get(sv, routes, fake_host, path, **query),
                           post=lambda path, body=None: _post(sv, routes, fake_host, path, body))


def card_of(http, rec) -> dict:
    return http.get(f"/actions/{rec.action_id}")[1]["action"]


def submit(http, rec, card, *, payload=None, captured=None, wire_text="Approve"):
    return http.post(f"/actions/{rec.action_id}/submit", {
        "captured": captured if captured is not None else card["captured"],
        "payload": payload or {"decision": "approve"}, "client_wire_text": wire_text})


def report(http, rec, delivery_id: str, outcome: str, http_status: int | None, receipt: Any = ...):
    body = {"delivery_id": delivery_id, "outcome": outcome, "http_status": http_status}
    if receipt is not ...:
        body["receipt"] = receipt
    return http.post(f"/actions/{rec.action_id}/delivery", body)


@pytest.fixture
def receipt(http, gate, slot) -> dict:
    """A fresh approval of ``gate`` committed (phase one): the receipt the browser must act on."""
    status, body = submit(http, gate, card_of(http, gate))
    assert status == 200, body
    return body


# --- the queue ---


def test_the_queue_is_empty_until_a_boundary_is_on_disk(http):
    status, body = http.get("/actions")
    assert status == 200
    assert body["actions"] == [] and body["organize"] == "priority" and body["groups"] == []
    assert body["counts"] == {"total": 0, "critical": 0, "blocking": 0, "attention": 0, "info": 0}
    assert body["generated_at"]


def test_the_card_has_exactly_the_contract_keys(http, repo, gate):
    card = http.get("/actions")[1]["actions"][0]
    assert set(card) == ACTION_CARD_KEYS
    assert set(card["evidence"]) == EVIDENCE_KEYS
    assert set(card["delivery"]) == DELIVERY_KEYS
    assert card["type"] == "gate" and card["queue_type"] == "gate"
    assert card["priority_group"] == 2 and card["risk_class"] == "human_lane"
    assert card["repo"] == {"repo_id": repo.repo_id, "label": "demo", "canonical_path": repo.canonical_path,
                            "availability": "available", "availability_detail": None, "archived": False}
    assert card["intent"]["intent_key"] == INTENT and card["stage"]["slug"] == STAGE
    assert card["headline"]["key"] == "action.gate.headline"
    assert card["deep_link"] == f"/apps/{APP_NAME}?view=actions&action={card['action_id']}"
    assert card["human_text_present"] is False, "the verbatim decision text is never on the card"
    assert [d["decision"] for d in card["decisions"]] == ["approve", "request_changes"]
    assert all(d["lane"] in ("human_lane", "studio_only") for d in card["decisions"])


def test_the_queue_groups_by_priority_band_with_an_i18n_label(http, gate):
    body = http.get("/actions")[1]
    assert body["groups"] == [{"key": "group.2", "label_key": "queue.group.blocking", "action_ids": [gate.action_id]}]
    assert body["counts"]["total"] == 1
    assert body["counts"][body["actions"][0]["severity"]] == 1


def test_revision_cards_are_excluded_unless_asked_for(http, sv, repo, gate):
    revision = _derive(sv, repo, REVISING, "revision")
    assert revision.action_id not in [card["action_id"] for card in http.get("/actions")[1]["actions"]]
    assert revision.action_id in [card["action_id"] for card in http.get("/actions", include="revision")[1]["actions"]]


def test_the_queue_can_be_organised_four_ways_and_refuses_a_fifth(http, gate):
    for mode in ("priority", "repo", "type", "oldest"):
        status, body = http.get("/actions", organize=mode)
        assert status == 200 and body["organize"] == mode
        assert body["groups"][0]["action_ids"] == [gate.action_id]
    status, body = http.get("/actions", organize="alphabetical")
    assert status == 400 and body["code"] == "bad_param"


def test_the_queue_filters_by_repo_intent_type_and_status(http, repo, gate):
    for query, expected in (
        ({"repo": repo.repo_id}, 1), ({"repo": "r_000000000009"}, 0), ({"intent": INTENT}, 1),
        ({"intent": REVISING}, 0), ({"type": "gate"}, 1), ({"type": "question"}, 0),
        ({"status": "Queued"}, 1), ({"status": "Delivering"}, 0),
    ):
        assert len(http.get("/actions", **query)[1]["actions"]) == expected, query


def test_the_queue_refuses_an_unknown_type_or_status(http):
    for query in ({"type": "gates"}, {"status": "Sent"}):
        status, body = http.get("/actions", **query)
        assert status == 400 and body["code"] == "bad_param"


def test_one_action_carries_its_transition_history_and_drafts(http, gate):
    status, body = http.get(f"/actions/{gate.action_id}")
    assert status == 200
    assert set(body) == {"action", "transitions", "drafts"}
    assert set(body["action"]) == ACTION_CARD_KEYS
    assert body["drafts"] == [] and body["transitions"] == []


def test_an_unknown_action_is_a_404(http):
    status, body = http.get("/actions/a_0000000000000009")
    assert status == 404 and body["code"] == "action_not_found"


# --- submit: phase one ---


def test_submit_commits_the_record_and_returns_the_call_the_browser_must_make(sv, fake_host, gate, slot, receipt):
    assert receipt["ok"] is True and receipt["lane"] == "human_lane" and receipt["status"] == "Delivering"
    assert receipt["wire_text"] == "Approve" and receipt["slot_key"] == slot
    assert receipt["delivery_id"].startswith("dl_")
    # The receipt's generation is the one stamped on the in-flight row, which is now the lease itself.
    assert receipt["lease_generation"] == sv.storage.get("actions", gate.action_id)["lease_generation"]
    assert [rec.generation for rec in sv.scheduler.live()] == [receipt["lease_generation"]]
    assert receipt["host"] == {"method": "POST", "path": "/api/chat?ws=1", "body": {
        "message": "Approve", "slot": slot, "agent": "aidlc",
        "meta": {"studio_action_id": gate.action_id, "studio_delivery_id": receipt["delivery_id"]}}}
    # Durable before the answer, and the host was never written to: the browser sends.
    row = sv.storage.get("actions", gate.action_id)
    assert row["status"] == "Delivering" and row["boot_id"] == BOOT
    assert fake_host.get_slot(slot).messages == []
    assert fake_host.get_slot(slot).running is False


def test_submit_refuses_a_wire_text_the_client_made_up(http, sv, gate, slot):
    status, body = submit(http, gate, card_of(http, gate), wire_text="Approve everything")
    assert status == 400 and body["code"] == "invalid_decision"
    assert sv.storage.get("actions", gate.action_id)["status"] == "Queued"


def test_submit_refuses_stale_captured_evidence_and_returns_the_refreshed_card(http, gate, slot):
    card = card_of(http, gate)
    status, body = submit(http, gate, card, captured={**card["captured"], "state_hash": "0" * 64})
    assert status == 409 and body["code"] == "action_stale"
    assert set(body["details"]["card"]) == ACTION_CARD_KEYS


def test_submit_refuses_a_paused_intent(http, sv, repo, gate, slot):
    card = card_of(http, gate)
    asyncio.run(sv.sessions.set_paused(repo.repo_id, "default", INTENT, True))
    status, body = submit(http, gate, card)
    assert status == 409 and body["code"] == "intent_paused"


def test_submit_refuses_an_unbound_intent(http, gate):
    status, body = submit(http, gate, card_of(http, gate))
    assert status == 409 and body["code"] == "session_unbound"


def test_submit_refuses_a_busy_slot_and_names_the_reasons(http, fake_host, gate, slot):
    card = card_of(http, gate)
    fake_host.set_running(slot, True)
    status, body = submit(http, gate, card)
    assert status == 409 and body["code"] == "slot_busy"
    assert "running" in body["details"]["reasons"]


def test_submit_needs_a_payload_that_names_a_decision(http, gate, slot):
    for body in ({"captured": {}}, {"payload": {"feedback": "x"}}):
        status, result = http.post(f"/actions/{gate.action_id}/submit", body)
        assert status == 400 and result["code"] == "bad_body"


def test_a_gate_rejection_carries_its_feedback_into_the_wire_text(http, gate, slot):
    status, body = submit(http, gate, card_of(http, gate), wire_text="Request Changes: tighten the scope",
                          payload={"decision": "request_changes", "feedback": "tighten the scope"})
    assert status == 200
    assert body["wire_text"] == "Request Changes: tighten the scope"
    assert body["host"]["body"]["message"] == "Request Changes: tighten the scope"


def test_request_changes_without_feedback_is_refused(http, gate, slot):
    status, body = submit(http, gate, card_of(http, gate), wire_text="Request Changes: ",
                          payload={"decision": "request_changes", "feedback": "   "})
    assert status == 400 and body["code"] == "feedback_required"


# --- delivery: phase three ---


def test_a_delivered_report_moves_the_row_and_is_idempotent(http, gate, slot, receipt):
    args = (http, gate, receipt["delivery_id"], "delivered", 200, {"ok": True, "slot": slot, "mid": "m-1"})
    status, first = report(*args)
    assert status == 200 and first["ok"] is True and first["idempotent"] is False
    delivery = first["action"]["delivery"]
    assert first["action"]["status"] == "Delivered"
    assert delivery["turn_started_at"] == delivery["delivered_at"]
    assert delivery["outcome"] == "delivered"
    assert delivery["receipt"] == {"ok": True, "slot": slot, "mid": "m-1"}
    # `delivery_confirmed` is the *late*-report flag (C24): on the happy path the status is the confirmation.
    assert delivery["delivery_confirmed"] is False
    status, second = report(*args)
    assert status == 200 and second["idempotent"] is True
    assert second["action"]["status"] == first["action"]["status"]


def test_a_queued_receipt_is_delivered_but_waits(http, gate, slot, receipt):
    """``{"ok": true, "queued": true}`` means the host accepted it behind a running turn (review P22)."""
    status, body = report(http, gate, receipt["delivery_id"], "delivered", 200, {"ok": True, "queued": True})
    assert status == 200
    assert body["action"]["status"] == "Delivered"
    assert body["action"]["delivery"]["queued_at"]
    assert "turn_started_at" not in body["action"]["delivery"], "its turn has not started yet"


def test_not_delivered_is_accepted_only_with_an_authoritative_refusal(http, gate, slot, receipt):
    status, body = report(http, gate, receipt["delivery_id"], "not_delivered", 409,
                          {"error": "slot agent mismatch", "code": "slot_agent_mismatch"})
    assert status == 200
    assert body["action"]["status"] == "NotDelivered"
    assert body["action"]["delivery"]["outcome"] == "not_delivered"


def test_not_delivered_is_refused_when_a_matching_row_exists_in_the_slot(http, sv, fake_host, gate, slot, receipt):
    """The client says "never sent"; the transcript says otherwise, so a human decides (C24)."""
    fake_host.append_user_row(slot, "Approve", "2026-09-04T10:00:30Z")
    status, body = report(http, gate, receipt["delivery_id"], "not_delivered", 400, {"error": "bad request"})
    assert status == 409 and body["code"] == "not_delivered_unproven"
    # The four proofs of C24 (`disk_baseline_unchanged` is the §2.11 `evidence.delivery` spelling).
    assert set(body["details"]["checks"]) == CHECKS
    assert body["details"]["checks"]["row_absent"] is False
    assert sv.storage.get("actions", gate.action_id)["status"] == "ReconciliationRequired"


def test_not_delivered_is_refused_without_an_authoritative_status(http, gate, slot, receipt):
    status, body = report(http, gate, receipt["delivery_id"], "not_delivered", None, {"ok": False})
    assert status == 409 and body["code"] == "not_delivered_unproven"


def test_a_late_delivered_report_confirms_the_delivery_and_blocks_a_resend(http, gate, slot):
    """The browser came back after the deadline: the decision *was* sent, so it must never be replayed."""
    card = card_of(http, gate)
    _status, receipt = submit(http, gate, card)
    report(http, gate, receipt["delivery_id"], "uncertain", None, None)
    _status, body = report(http, gate, receipt["delivery_id"], "delivered", 200, {"ok": True, "slot": slot})
    assert body["action"]["delivery"]["delivery_confirmed"] is True
    status, refused = submit(http, gate, card)
    assert status == 409, refused


def test_an_uncertain_report_is_recorded_as_uncertain(http, gate, slot, receipt):
    status, body = report(http, gate, receipt["delivery_id"], "uncertain", 502, None)
    assert status == 200 and body["action"]["status"] == "ReconciliationRequired"
    assert body["action"]["queue_type"] == "delivery_uncertain"


def test_a_delivery_report_for_another_delivery_is_refused(http, gate, slot, receipt):
    status, body = report(http, gate, "dl_0000000000000099", "delivered", 200, {"ok": True})
    assert status == 409 and body["code"] == "delivery_ack_invalid"


def test_a_delivery_report_needs_its_id_and_outcome(http, gate, slot):
    status, body = http.post(f"/actions/{gate.action_id}/delivery", {})
    assert status == 400 and body["code"] == "bad_body"
    assert body["details"]["missing"] == ["delivery_id", "outcome"]


def test_an_unknown_outcome_is_refused(http, gate, slot, receipt):
    status, body = report(http, gate, receipt["delivery_id"], "probably", 200)
    assert status == 400 and body["code"] == "bad_param"


# --- resolve ---


def test_resolve_reconcile_runs_the_reconciler_against_one_row(http, gate, slot, receipt):
    """``reconcile`` decides nothing itself: it applies the contracts the next tick would, without the wait."""
    report(http, gate, receipt["delivery_id"], "uncertain", None, None)
    status, body = http.post(f"/actions/{gate.action_id}/resolve", {"decision": "reconcile", "payload": {}})
    assert status == 200 and body["ok"] is True, body
    assert set(body["action"]) == ACTION_CARD_KEYS


def test_resolve_refuses_a_decision_the_card_does_not_offer(http, gate):
    status, body = http.post(f"/actions/{gate.action_id}/resolve", {"decision": "approve", "payload": {}})
    assert status == 400 and body["code"] == "invalid_decision"


def test_resolve_needs_a_decision(http, gate):
    status, body = http.post(f"/actions/{gate.action_id}/resolve", {})
    assert status == 400 and body["code"] == "bad_body"
    assert body["details"]["missing"] == ["decision"]


def test_resolve_refuses_mark_not_delivered_without_proof(http, sv, fake_host, gate, slot, receipt):
    """``mark_not_delivered`` is a human's claim; the backend still has to prove absence (C24)."""
    fake_host.append_user_row(slot, "Approve", "2026-09-04T10:00:30Z")
    report(http, gate, receipt["delivery_id"], "uncertain", None, None)
    asyncio.run(sv.reconciler.reconcile_now(gate.action_id))
    status, body = http.post(f"/actions/{gate.action_id}/resolve", {"decision": "mark_not_delivered", "payload": {}})
    assert status in (400, 409)
    assert body["code"] in ("not_delivered_unproven", "invalid_decision")
    assert sv.storage.get("actions", gate.action_id)["status"] != "NotDelivered"
