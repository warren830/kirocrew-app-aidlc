"""Action Center routes (§2.4) — the queue, the card, and the two-phase human lane.

Four properties carry this file, and each of them is the product rather than the plumbing.

1. **The ``ActionCard`` keys are exactly §2.11.** The card is the UI's whole model of a decision; a key
   that quietly appears or disappears is a frontend bug nobody sees until a user cannot approve a gate.
2. **``submit`` hands the browser the call; it never makes it.** The response is the durable
   ``Delivering`` record plus ``receipt.host`` — byte for byte what the UI must POST to the host — and
   the host slot is provably untouched.
3. **``not_delivered`` is proven, never believed.** With any evidence that the message might have
   arrived, the answer is ``not_delivered_unproven`` and the row goes to ``DeliveryUncertain``: replaying
   a decision the conductor already acted on is the one failure the whole design exists to prevent.
4. **Delivery reports are idempotent.** The same ``delivery_id`` twice is one delivery.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import conftest as CT
import fixtures as F
from test_handlers_auth import APP_ROOT, BOOT, call, common, routes, sv  # noqa: F401 - fixtures
from test_handlers_intents import runnable_demo  # noqa: F401 - command fixture

INTENT = "260904-gate-demo"
REVISING = "260904-revising"
STAGE = "requirements-analysis"
QUESTIONS_REL = f"inception/{STAGE}/{STAGE}-questions.md"

#: §2.11, verbatim. Compared as a set equality on purpose — extra and missing are both failures.
ACTION_CARD_KEYS = {
    "action_id", "type", "queue_type", "status", "status_generation", "source", "risk_class",
    "severity", "priority_group", "repo", "space", "intent", "stage", "headline", "consequence",
    "waiting_since", "created_at", "updated_at", "primary", "decisions", "captured", "evidence",
    "delivery", "resolution", "acknowledged_evidence_sha256", "failure", "install", "budget",
    "advisor", "deep_link", "dedupe_key", "human_text_present",
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


def _state(marks: dict[str, str] | None = None, **fields: str) -> str:
    values = {
        "Current Stage": STAGE,
        "Status": "Running",
        "Next Stage": "user-stories",
        "Lifecycle Phase": "INCEPTION",
        "Completed": "7",
    }
    values.update(fields)
    return F.state_text(marks=marks or {STAGE: "?"}, fields=values)


@pytest.fixture
def demo(repo_builder) -> Path:
    """One repository: an intent at an open gate, and a second one in a revision loop."""
    builder = repo_builder.with_engine("payload").with_workspace()
    builder = builder.with_intent(
        REVISING,
        state=_state(marks={STAGE: "r"}),
        audit=F.gate_rejected_audit(STAGE),
        active=False,
    )
    return (
        builder.with_intent(
            INTENT,
            state=_state(),
            audit=F.gate_open_audit(STAGE),
            directive={"version": 1, "stage": STAGE, "state_sha256": "AUTO"},
            questions={QUESTIONS_REL: F.blank_answers(F.real_questions())},
            artifacts={
                f"inception/{STAGE}/requirements.md":
                    "# Requirements\n\nOne requirement.\n\n## Review\n\n**Verdict**: READY\n",
            },
        )
        .with_git()
        .build()
    )


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


@pytest.fixture
def gate(sv, repo):
    """One derived ``gate`` card, produced the way the reconciler produces it."""
    return _derive(sv, repo, INTENT, "gate")


# --------------------------------------------------------------------------- #
# the queue
# --------------------------------------------------------------------------- #


def test_the_queue_is_empty_until_a_boundary_is_on_disk(sv, routes, fake_host):
    status, body = call(sv, routes, "GET", "/actions",
                        CT.owner_request("GET", "/actions", host=fake_host))
    assert status == 200
    assert body["actions"] == []
    assert body["organize"] == "priority"
    assert body["groups"] == []
    assert body["counts"] == {"total": 0, "critical": 0, "blocking": 0, "attention": 0, "info": 0}
    assert body["generated_at"]


def test_the_card_has_exactly_the_contract_keys(sv, routes, fake_host, repo, gate):
    _status, body = call(sv, routes, "GET", "/actions",
                         CT.owner_request("GET", "/actions", host=fake_host))
    card = body["actions"][0]
    assert set(card) == ACTION_CARD_KEYS
    assert set(card["evidence"]) == EVIDENCE_KEYS
    assert set(card["delivery"]) == DELIVERY_KEYS
    assert card["type"] == "gate" and card["queue_type"] == "gate"
    assert card["priority_group"] == 2
    assert card["risk_class"] == "human_lane"
    assert card["repo"] == {"repo_id": repo.repo_id, "label": "demo",
                            "canonical_path": repo.canonical_path, "availability": "available",
                            "availability_detail": None, "archived": False}
    assert card["intent"]["intent_key"] == INTENT
    assert card["stage"]["slug"] == STAGE
    assert card["headline"]["key"] == "action.gate.headline"
    assert card["deep_link"] == f"/apps/aidlc-studio?view=actions&action={card['action_id']}"
    # The verbatim decision text is never on the card, only whether one exists.
    assert card["human_text_present"] is False
    assert [d["decision"] for d in card["decisions"]] == ["approve", "request_changes"]
    assert all(d["lane"] in ("human_lane", "studio_only") for d in card["decisions"])


def test_the_queue_groups_by_priority_band_with_an_i18n_label(sv, routes, fake_host, repo, gate):
    _status, body = call(sv, routes, "GET", "/actions",
                         CT.owner_request("GET", "/actions", host=fake_host))
    assert body["groups"] == [
        {"key": "group.2", "label_key": "queue.group.blocking", "action_ids": [gate.action_id]}
    ]
    assert body["counts"]["total"] == 1
    assert body["counts"][body["actions"][0]["severity"]] == 1


def test_revision_cards_are_excluded_unless_asked_for(sv, routes, fake_host, repo, gate):
    revision = _derive(sv, repo, REVISING, "revision")
    _status, body = call(sv, routes, "GET", "/actions",
                         CT.owner_request("GET", "/actions", host=fake_host))
    assert revision.action_id not in [card["action_id"] for card in body["actions"]]

    request = CT.owner_request("GET", "/actions", host=fake_host, query={"include": "revision"})
    _status, body = call(sv, routes, "GET", "/actions", request)
    assert revision.action_id in [card["action_id"] for card in body["actions"]]


def test_the_queue_can_be_organised_four_ways_and_refuses_a_fifth(sv, routes, fake_host, repo, gate):
    for mode in ("priority", "repo", "type", "oldest"):
        request = CT.owner_request("GET", "/actions", host=fake_host, query={"organize": mode})
        status, body = call(sv, routes, "GET", "/actions", request)
        assert status == 200 and body["organize"] == mode
        assert body["groups"][0]["action_ids"] == [gate.action_id]

    request = CT.owner_request("GET", "/actions", host=fake_host, query={"organize": "alphabetical"})
    status, body = call(sv, routes, "GET", "/actions", request)
    assert status == 400 and body["code"] == "bad_param"


def test_the_queue_filters_by_repo_intent_type_and_status(sv, routes, fake_host, repo, gate):
    for query, expected in (
        ({"repo": repo.repo_id}, 1),
        ({"repo": "r_000000000009"}, 0),
        ({"intent": INTENT}, 1),
        ({"intent": REVISING}, 0),
        ({"type": "gate"}, 1),
        ({"type": "question"}, 0),
        ({"status": "Queued"}, 1),
        ({"status": "Delivering"}, 0),
    ):
        request = CT.owner_request("GET", "/actions", host=fake_host, query=query)
        _status, body = call(sv, routes, "GET", "/actions", request)
        assert len(body["actions"]) == expected, query


def test_the_queue_refuses_an_unknown_type_or_status(sv, routes, fake_host):
    for query in ({"type": "gates"}, {"status": "Sent"}):
        request = CT.owner_request("GET", "/actions", host=fake_host, query=query)
        status, body = call(sv, routes, "GET", "/actions", request)
        assert status == 400 and body["code"] == "bad_param"


def test_one_action_carries_its_transition_history_and_drafts(sv, routes, fake_host, gate):
    path = f"/actions/{gate.action_id}"
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 200
    assert set(body) == {"action", "transitions", "drafts"}
    assert set(body["action"]) == ACTION_CARD_KEYS
    assert body["drafts"] == []
    assert body["transitions"] == []


def test_an_unknown_action_is_a_404(sv, routes, fake_host):
    path = "/actions/a_0000000000000009"
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 404 and body["code"] == "action_not_found"


# --------------------------------------------------------------------------- #
# submit — phase one
# --------------------------------------------------------------------------- #


def test_submit_commits_the_record_and_returns_the_call_the_browser_must_make(sv, routes, fake_host,
                                                                            repo, gate, slot):
    card = _card(sv, routes, fake_host, gate)
    status, body = _submit(sv, routes, fake_host, gate, card)
    assert status == 200
    assert body["ok"] is True
    assert body["lane"] == "human_lane"
    assert body["status"] == "Delivering"
    assert body["wire_text"] == "Approve"
    assert body["slot_key"] == slot
    assert body["delivery_id"].startswith("dl_")
    assert body["lease_generation"] == 1
    assert body["host"] == {
        "method": "POST",
        "path": "/api/chat?ws=1",
        "body": {
            "message": "Approve",
            "slot": slot,
            "agent": "aidlc",
            "meta": {"studio_action_id": gate.action_id, "studio_delivery_id": body["delivery_id"]},
        },
    }
    # Durable before the answer, and the host was never written to: the browser sends.
    row = sv.storage.get("actions", gate.action_id)
    assert row["status"] == "Delivering" and row["boot_id"] == BOOT
    assert fake_host.get_slot(slot).messages == []
    assert fake_host.get_slot(slot).running is False


def test_submit_refuses_a_wire_text_the_client_made_up(sv, routes, fake_host, repo, gate, slot):
    card = _card(sv, routes, fake_host, gate)
    status, body = _submit(sv, routes, fake_host, gate, card, wire_text="Approve everything")
    assert status == 400 and body["code"] == "invalid_decision"
    assert sv.storage.get("actions", gate.action_id)["status"] == "Queued"


def test_submit_refuses_stale_captured_evidence_and_returns_the_refreshed_card(sv, routes, fake_host,
                                                                             repo, gate, slot):
    card = _card(sv, routes, fake_host, gate)
    captured = dict(card["captured"])
    captured["state_hash"] = "0" * 64
    status, body = _submit(sv, routes, fake_host, gate, card, captured=captured)
    assert status == 409 and body["code"] == "action_stale"
    assert set(body["details"]["card"]) == ACTION_CARD_KEYS


def test_submit_refuses_a_paused_intent(sv, routes, fake_host, repo, gate, slot):
    card = _card(sv, routes, fake_host, gate)
    asyncio.run(sv.sessions.set_paused(repo.repo_id, "default", INTENT, True))
    status, body = _submit(sv, routes, fake_host, gate, card)
    assert status == 409 and body["code"] == "intent_paused"


def test_submit_refuses_an_unbound_intent(sv, routes, fake_host, repo, gate):
    card = _card(sv, routes, fake_host, gate)
    status, body = _submit(sv, routes, fake_host, gate, card)
    assert status == 409 and body["code"] == "session_unbound"


def test_submit_refuses_a_busy_slot_and_names_the_reasons(sv, routes, fake_host, repo, gate, slot):
    card = _card(sv, routes, fake_host, gate)
    fake_host.set_running(slot, True)
    status, body = _submit(sv, routes, fake_host, gate, card)
    assert status == 409 and body["code"] == "slot_busy"
    assert "running" in body["details"]["reasons"]


def test_submit_needs_a_payload_that_names_a_decision(sv, routes, fake_host, gate, slot):
    path = f"/actions/{gate.action_id}/submit"
    request = CT.owner_request("POST", path, host=fake_host, body={"captured": {}})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 400 and body["code"] == "bad_body"

    request = CT.owner_request("POST", path, host=fake_host, body={"payload": {"feedback": "x"}})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 400 and body["code"] == "bad_body"


def test_a_gate_rejection_carries_its_feedback_into_the_wire_text(sv, routes, fake_host, repo, gate,
                                                                 slot):
    card = _card(sv, routes, fake_host, gate)
    status, body = _submit(
        sv, routes, fake_host, gate, card,
        payload={"decision": "request_changes", "feedback": "tighten the scope"},
        wire_text="Request Changes: tighten the scope",
    )
    assert status == 200
    assert body["wire_text"] == "Request Changes: tighten the scope"
    assert body["host"]["body"]["message"] == "Request Changes: tighten the scope"


def test_request_changes_without_feedback_is_refused(sv, routes, fake_host, repo, gate, slot):
    card = _card(sv, routes, fake_host, gate)
    status, body = _submit(
        sv, routes, fake_host, gate, card,
        payload={"decision": "request_changes", "feedback": "   "},
        wire_text="Request Changes: ",
    )
    assert status == 400 and body["code"] == "feedback_required"


# --------------------------------------------------------------------------- #
# delivery — phase three
# --------------------------------------------------------------------------- #


def test_a_delivered_report_moves_the_row_and_is_idempotent(sv, routes, fake_host, repo, gate, slot):
    card = _card(sv, routes, fake_host, gate)
    _status, receipt = _submit(sv, routes, fake_host, gate, card)
    body = {
        "delivery_id": receipt["delivery_id"],
        "outcome": "delivered",
        "http_status": 200,
        "receipt": {"ok": True, "slot": slot, "mid": "m-1"},
    }
    path = f"/actions/{gate.action_id}/delivery"
    status, first = call(sv, routes, "POST", path,
                         CT.owner_request("POST", path, host=fake_host, body=body))
    assert status == 200 and first["ok"] is True and first["idempotent"] is False
    assert first["action"]["status"] in ("Delivered", "Processing")
    assert first["action"]["delivery"]["outcome"] == "delivered"
    assert first["action"]["delivery"]["receipt"] == {"ok": True, "slot": slot, "mid": "m-1"}
    # `delivery_confirmed` is the *late*-report flag (C24): on the happy path the status is the
    # confirmation, and the flag exists to block a retry of something acknowledged after the deadline.
    assert first["action"]["delivery"]["delivery_confirmed"] is False

    status, second = call(sv, routes, "POST", path,
                          CT.owner_request("POST", path, host=fake_host, body=body))
    assert status == 200 and second["idempotent"] is True
    assert second["action"]["status"] == first["action"]["status"]


def test_a_queued_receipt_is_delivered_but_waits(sv, routes, fake_host, repo, gate, slot):
    """``{"ok": true, "queued": true}`` means the host accepted it behind a running turn (review P22)."""
    card = _card(sv, routes, fake_host, gate)
    _status, receipt = _submit(sv, routes, fake_host, gate, card)
    path = f"/actions/{gate.action_id}/delivery"
    request = CT.owner_request("POST", path, host=fake_host, body={
        "delivery_id": receipt["delivery_id"], "outcome": "delivered", "http_status": 200,
        "receipt": {"ok": True, "queued": True},
    })
    status, body = call(sv, routes, "POST", path, request)
    assert status == 200
    assert body["action"]["status"] == "Delivered"
    assert body["action"]["delivery"]["queued_at"]


def test_not_delivered_is_accepted_only_with_an_authoritative_refusal(sv, routes, fake_host, repo,
                                                                     gate, slot):
    card = _card(sv, routes, fake_host, gate)
    _status, receipt = _submit(sv, routes, fake_host, gate, card)
    path = f"/actions/{gate.action_id}/delivery"
    request = CT.owner_request("POST", path, host=fake_host, body={
        "delivery_id": receipt["delivery_id"], "outcome": "not_delivered", "http_status": 409,
        "receipt": {"error": "slot agent mismatch", "code": "slot_agent_mismatch"},
    })
    status, body = call(sv, routes, "POST", path, request)
    assert status == 200
    assert body["action"]["status"] == "NotDelivered"
    assert body["action"]["delivery"]["outcome"] == "not_delivered"


def test_not_delivered_is_refused_when_a_matching_row_exists_in_the_slot(sv, routes, fake_host, repo,
                                                                        gate, slot):
    """The client says "never sent"; the transcript says otherwise, so a human decides (C24)."""
    card = _card(sv, routes, fake_host, gate)
    _status, receipt = _submit(sv, routes, fake_host, gate, card)
    fake_host.append_user_row(slot, "Approve", "2026-09-04T10:00:30Z")
    path = f"/actions/{gate.action_id}/delivery"
    request = CT.owner_request("POST", path, host=fake_host, body={
        "delivery_id": receipt["delivery_id"], "outcome": "not_delivered", "http_status": 400,
        "receipt": {"error": "bad request"},
    })
    status, body = call(sv, routes, "POST", path, request)
    assert status == 409 and body["code"] == "not_delivered_unproven"
    # The four proofs of C24. `disk_baseline_unchanged` is the spelling `evidence.delivery` uses in
    # §2.11 (§2.4's table abbreviates it) — reported to the orchestrator.
    assert set(body["details"]["checks"]) == {"row_absent", "slot_idle", "disk_baseline_unchanged",
                                             "boot_unchanged"}
    assert body["details"]["checks"]["row_absent"] is False
    assert sv.storage.get("actions", gate.action_id)["status"] == "DeliveryUncertain"


def test_not_delivered_is_refused_without_an_authoritative_status(sv, routes, fake_host, repo, gate,
                                                                 slot):
    card = _card(sv, routes, fake_host, gate)
    _status, receipt = _submit(sv, routes, fake_host, gate, card)
    path = f"/actions/{gate.action_id}/delivery"
    request = CT.owner_request("POST", path, host=fake_host, body={
        "delivery_id": receipt["delivery_id"], "outcome": "not_delivered", "http_status": None,
        "receipt": {"ok": False},
    })
    status, body = call(sv, routes, "POST", path, request)
    assert status == 409 and body["code"] == "not_delivered_unproven"


def test_a_late_delivered_report_confirms_the_delivery_and_blocks_a_retry(sv, routes, fake_host, repo,
                                                                        gate, slot):
    """The browser came back after the deadline: the decision *was* sent, so it must never be replayed."""
    card = _card(sv, routes, fake_host, gate)
    _status, receipt = _submit(sv, routes, fake_host, gate, card)
    path = f"/actions/{gate.action_id}/delivery"
    call(sv, routes, "POST", path, CT.owner_request("POST", path, host=fake_host, body={
        "delivery_id": receipt["delivery_id"], "outcome": "uncertain", "http_status": None,
        "receipt": None,
    }))
    _status, body = call(sv, routes, "POST", path, CT.owner_request("POST", path, host=fake_host, body={
        "delivery_id": receipt["delivery_id"], "outcome": "delivered", "http_status": 200,
        "receipt": {"ok": True, "slot": slot},
    }))
    assert body["action"]["delivery"]["delivery_confirmed"] is True

    retry = f"/actions/{gate.action_id}/retry"
    status, refused = call(sv, routes, "POST", retry,
                           CT.owner_request("POST", retry, host=fake_host, body={}))
    assert status == 409 and refused["code"] == "retry_not_allowed"


def test_an_uncertain_report_is_recorded_as_uncertain(sv, routes, fake_host, repo, gate, slot):
    card = _card(sv, routes, fake_host, gate)
    _status, receipt = _submit(sv, routes, fake_host, gate, card)
    path = f"/actions/{gate.action_id}/delivery"
    request = CT.owner_request("POST", path, host=fake_host, body={
        "delivery_id": receipt["delivery_id"], "outcome": "uncertain", "http_status": 502,
        "receipt": None,
    })
    status, body = call(sv, routes, "POST", path, request)
    assert status == 200 and body["action"]["status"] == "DeliveryUncertain"


def test_a_delivery_report_for_another_delivery_is_refused(sv, routes, fake_host, repo, gate, slot):
    card = _card(sv, routes, fake_host, gate)
    _submit(sv, routes, fake_host, gate, card)
    path = f"/actions/{gate.action_id}/delivery"
    request = CT.owner_request("POST", path, host=fake_host, body={
        "delivery_id": "dl_0000000000000099", "outcome": "delivered", "http_status": 200,
        "receipt": {"ok": True},
    })
    status, body = call(sv, routes, "POST", path, request)
    assert status == 409 and body["code"] == "delivery_ack_invalid"


def test_a_delivery_report_needs_its_id_and_outcome(sv, routes, fake_host, gate, slot):
    path = f"/actions/{gate.action_id}/delivery"
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 400 and body["code"] == "bad_body"
    assert body["details"]["missing"] == ["delivery_id", "outcome"]


def test_an_unknown_outcome_is_refused(sv, routes, fake_host, repo, gate, slot):
    card = _card(sv, routes, fake_host, gate)
    _status, receipt = _submit(sv, routes, fake_host, gate, card)
    path = f"/actions/{gate.action_id}/delivery"
    request = CT.owner_request("POST", path, host=fake_host, body={
        "delivery_id": receipt["delivery_id"], "outcome": "probably", "http_status": 200,
    })
    status, body = call(sv, routes, "POST", path, request)
    assert status == 400 and body["code"] == "bad_param"


# --------------------------------------------------------------------------- #
# retry, reconcile, cancel, resolve
# --------------------------------------------------------------------------- #


def test_retry_requeues_a_proven_undelivered_decision(sv, routes, fake_host, repo, gate, slot):
    card = _card(sv, routes, fake_host, gate)
    _status, receipt = _submit(sv, routes, fake_host, gate, card)
    delivery = f"/actions/{gate.action_id}/delivery"
    call(sv, routes, "POST", delivery, CT.owner_request("POST", delivery, host=fake_host, body={
        "delivery_id": receipt["delivery_id"], "outcome": "not_delivered", "http_status": 409,
        "receipt": {"error": "slot agent mismatch"},
    }))
    path = f"/actions/{gate.action_id}/retry"
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 200 and body["action"]["status"] == "Queued"


def test_retry_is_refused_for_a_card_that_was_never_dispatched(sv, routes, fake_host, gate):
    path = f"/actions/{gate.action_id}/retry"
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 409 and body["code"] == "retry_not_allowed"


def test_reconcile_runs_the_reconciler_against_one_row(sv, routes, fake_host, repo, gate, slot):
    card = _card(sv, routes, fake_host, gate)
    _submit(sv, routes, fake_host, gate, card)
    path = f"/actions/{gate.action_id}/reconcile"
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 200 and body["ok"] is True
    assert set(body["action"]) == ACTION_CARD_KEYS


def test_reconcile_of_an_unknown_action_is_a_404(sv, routes, fake_host):
    path = "/actions/a_0000000000000009/reconcile"
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 404 and body["code"] == "action_not_found"


def test_a_derived_gate_card_cannot_be_dismissed(sv, routes, fake_host, gate):
    """The gate is on disk; hiding the card would lose the boundary rather than close it (C33)."""
    path = f"/actions/{gate.action_id}/cancel"
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={"reason": "not now"}))
    assert status == 409 and body["code"] == "cancel_not_safe"


def test_a_command_the_user_queued_can_be_withdrawn(sv, routes, fake_host, repo, slot, runnable_demo):
    run = f"/repos/{repo.repo_id}/intents/{INTENT}/run"
    status, created = call(sv, routes, "POST", run,
                           CT.owner_request("POST", run, host=fake_host, body={}))
    assert status == 201, created
    path = f"/actions/{created['action_id']}/cancel"
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={"reason": "changed mind"}))
    assert status == 200 and body["action"]["status"] == "Cancelled"


def test_resolve_refuses_a_decision_the_card_does_not_offer(sv, routes, fake_host, gate):
    path = f"/actions/{gate.action_id}/resolve"
    request = CT.owner_request("POST", path, host=fake_host,
                               body={"decision": "approve", "payload": {}})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 400 and body["code"] == "invalid_decision"


def test_resolve_needs_a_decision(sv, routes, fake_host, gate):
    path = f"/actions/{gate.action_id}/resolve"
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 400 and body["code"] == "bad_body"
    assert body["details"]["missing"] == ["decision"]


def test_resolve_refuses_mark_not_delivered_without_proof(sv, routes, fake_host, repo, gate, slot):
    """``mark_not_delivered`` is a human's claim; the backend still has to prove absence (C24)."""
    card = _card(sv, routes, fake_host, gate)
    _status, receipt = _submit(sv, routes, fake_host, gate, card)
    fake_host.append_user_row(slot, "Approve", "2026-09-04T10:00:30Z")
    delivery = f"/actions/{gate.action_id}/delivery"
    call(sv, routes, "POST", delivery, CT.owner_request("POST", delivery, host=fake_host, body={
        "delivery_id": receipt["delivery_id"], "outcome": "uncertain", "http_status": None,
        "receipt": None,
    }))
    asyncio.run(sv.reconciler.reconcile_now(gate.action_id))

    path = f"/actions/{gate.action_id}/resolve"
    request = CT.owner_request("POST", path, host=fake_host,
                               body={"decision": "mark_not_delivered", "payload": {}})
    status, body = call(sv, routes, "POST", path, request)
    assert status in (400, 409)
    assert body["code"] in ("not_delivered_unproven", "invalid_decision")
    assert sv.storage.get("actions", gate.action_id)["status"] != "NotDelivered"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _derive(sv: Any, repo: Any, intent_dir: str, action_type: str) -> Any:
    snap = sv.projection.snapshot(repo, "default", intent_dir)
    seeds = sv.projection.derive_cards(snap, findings=[], binding=None, install=None, breakers=[],
                                       live_actions=[], slot=None, host_card=None)
    wanted = [seed for seed in seeds if seed.type == action_type]
    assert wanted, f"the fixture produced no {action_type} seed (got {[s.type for s in seeds]})"
    records = asyncio.run(sv.actions.upsert_derived(wanted))
    return next(rec for rec in records if rec.type == action_type)


def _card(sv: Any, routes: Any, fake_host: Any, rec: Any) -> dict:
    path = f"/actions/{rec.action_id}"
    _status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    return body["action"]


def _submit(
    sv: Any,
    routes: Any,
    fake_host: Any,
    rec: Any,
    card: dict,
    *,
    payload: dict | None = None,
    captured: dict | None = None,
    wire_text: str = "Approve",
) -> tuple[int, Any]:
    path = f"/actions/{rec.action_id}/submit"
    request = CT.owner_request("POST", path, host=fake_host, body={
        "captured": captured if captured is not None else card["captured"],
        "payload": payload or {"decision": "approve"},
        "client_wire_text": wire_text,
    })
    return call(sv, routes, "POST", path, request)
