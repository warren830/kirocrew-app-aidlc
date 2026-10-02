"""A run parked on a host tool approval is visible in Studio and notified, but answered in the chat."""

import asyncio

from test_e2e_gate import (  # noqa: F401
    INTENT, _get, _rescan, repo_dir, scene,
)
from test_handlers_auth import common, routes, sv  # noqa: F401

PERMISSION = {
    "role": "permission", "ts": "2026-09-04T10:00:30Z", "content": "Locate the load-steering instructions",
    "cls": '{"tool_input": "grep -n steering .kiro/skills/aidlc/SKILL.md", "approval_id": "req-1"}',
}


class Pending:
    def done(self) -> bool:
        return False


def park(fake_host, scene):
    slot = fake_host.get_slot(scene.slot_key)
    slot.messages.append(PERMISSION)
    slot._approval_futures["req-1"] = Pending()
    return slot


def test_the_queue_lists_a_parked_conversation_with_what_it_asks(sv, routes, fake_host, scene):
    status, body = _get(sv, routes, fake_host, "/actions")
    assert status == 200 and body["approval_waits"] == []

    park(fake_host, scene)
    status, body = _get(sv, routes, fake_host, "/actions")
    assert status == 200
    assert body["approval_waits"] == [{
        "repo_id": scene.repo_id, "repo_label": "ledger", "space": "default", "intent_dir": INTENT,
        "intent_key": INTENT, "slot_key": scene.slot_key,
        "tool": "Locate the load-steering instructions",
        "tool_input": "grep -n steering .kiro/skills/aidlc/SKILL.md",
    }]
    # The queue's own filter applies: another repository's queue does not show it.
    status, body = _get(sv, routes, fake_host, "/actions", repo="r_other")
    assert status == 200 and body["approval_waits"] == []

    status, body = _get(sv, routes, fake_host, f"/repos/{scene.repo_id}/intents")
    session = next(i for i in body["intents"] if i["intent_dir"] == INTENT)["session"]
    assert session["waiting_approval"] is True
    assert session["approval"] == {
        "tool": "Locate the load-steering instructions",
        "tool_input": "grep -n steering .kiro/skills/aidlc/SKILL.md",
    }


def test_a_scan_notifies_once_per_approval_with_a_link_to_the_conversation(sv, routes, fake_host, scene):
    park(fake_host, scene)
    before = len(fake_host.notifications)
    for _ in range(2):
        asyncio.run(sv.reconciler.scan_repo(sv.repos.get(scene.repo_id)))
    sent = fake_host.notifications[before:]
    assert len(sent) == 1
    assert sent[0]["title"] == "AI-DLC is waiting for your approval: gate-demo"
    assert "grep -n steering" in sent[0]["body"]
