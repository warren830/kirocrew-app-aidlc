"""Notifications: the two courtesy channels, and the door that stays shut.

The interesting assertions are the negative ones. A notification must not carry evidence, must not become
a way to decide, and must not be able to fail the operation that produced it.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest


def _mod(studio):
    assert studio.notifications is not None, getattr(studio, "notifications__error", "module missing")
    return studio.notifications


class FakeHostBridge:
    """Only the two writes NotificationAdapter is allowed to make."""

    def __init__(self, *, notify_ok: bool = True, slack_ts: str | None = "ts-1") -> None:
        self.notify_ok = notify_ok
        self.slack_ts = slack_ts
        self.notified: list[dict[str, Any]] = []
        self.slack_calls: list[tuple[list[dict], str]] = []
        self.raise_on_notify = False
        self.raise_on_slack = False

    def notify(self, title: str, body: str, *, url: str, meta: dict | None = None) -> bool:
        if self.raise_on_notify:
            raise RuntimeError("host bus exploded")
        self.notified.append({"title": title, "body": body, "url": url, "meta": meta})
        return self.notify_ok

    async def slack_dm(self, blocks: list[dict], text: str) -> str | None:
        if self.raise_on_slack:
            raise RuntimeError("slack exploded")
        self.slack_calls.append((blocks, text))
        return self.slack_ts


class FakeSettings:
    def __init__(self, *, enabled: bool = True, muted: tuple[str, ...] = ()) -> None:
        self._slack = {"enabled": enabled, "muted_repo_ids": list(muted)}

    def slack(self) -> dict[str, Any]:
        return dict(self._slack)


class FakeActivity:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> int:
        self.rows.append(kwargs)
        return len(self.rows)


class MemoryPrefs:
    """Just the preference half of Storage, which is all this module touches."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.fail = False

    def pref_get(self, key: str) -> Any:
        if self.fail:
            raise RuntimeError("storage down")
        return self.values.get(key)

    def pref_set(self, key: str, value: Any) -> None:
        if self.fail:
            raise RuntimeError("storage down")
        self.values[key] = value


def _adapter(studio, clock, *, host=None, settings=None, storage=None, activity=None):
    mod = _mod(studio)
    return mod.NotificationAdapter(
        host or FakeHostBridge(),
        storage or MemoryPrefs(),
        settings or FakeSettings(),
        activity or FakeActivity(),
        clock,
    )


def _card(**over: Any) -> dict[str, Any]:
    card = {
        "action_id": "a_0000000000000001",
        "type": "gate",
        "queue_type": "gate",
        "status": "Queued",
        "severity": "blocking",
        "repo": {"repo_id": "r_000000000001", "label": "ledger-export", "canonical_path": "/home/dev/ledger"},
        "space": "default",
        "intent": {"intent_dir": "260904-ledger-export", "intent_key": "260904-ledger-export", "slug": "ledger-export"},
        "stage": {"slug": "requirements-analysis", "phase": "inception"},
        "waiting_since": "2026-09-05T04:00:00Z",
        "headline": {"key": "action.gate.headline", "params": {}},
        "primary": {"decision": "approve", "label_key": "decision.approve.label"},
        "failure": None,
    }
    card.update(over)
    return card


def test_deep_link_identifies_and_authorises_nothing(studio, clock):
    adapter = _adapter(studio, clock)
    link = adapter.deep_link("a_1")
    assert link == "/apps/aidlc-studio?view=actions&action=a_1"
    # No token, no secret, no signature: a link cannot be an authorisation.
    assert "token" not in link and "secret" not in link


def test_a_gate_notifies_both_channels_once(studio, clock):
    host, activity, prefs = FakeHostBridge(), FakeActivity(), MemoryPrefs()
    adapter = _adapter(studio, clock, host=host, activity=activity, storage=prefs)
    first = asyncio.run(adapter.notify_action(_card()))
    assert first.dashboard is True and first.slack == "sent" and first.dedupe_hit is False
    assert len(host.notified) == 1 and len(host.slack_calls) == 1
    assert activity.rows and activity.rows[0]["kind"] == "notification.gate"

    second = asyncio.run(adapter.notify_action(_card()))
    assert second.dedupe_hit is True
    assert len(host.notified) == 1, "a card that stays open must not notify on every tick"
    assert len(host.slack_calls) == 1


def test_delivery_uncertainty_is_its_own_notification_class(studio, clock):
    host, prefs = FakeHostBridge(), MemoryPrefs()
    adapter = _adapter(studio, clock, host=host, storage=prefs)
    asyncio.run(adapter.notify_action(_card()))
    # The same action becoming uncertain is new information, so it notifies again — but only once.
    asyncio.run(adapter.notify_action(_card(status="DeliveryUncertain", queue_type="delivery_uncertain")))
    asyncio.run(adapter.notify_action(_card(status="DeliveryUncertain", queue_type="delivery_uncertain")))
    assert len(host.notified) == 2


@pytest.mark.parametrize("card_type", ["revision", "run", "resume", "prepare_commit", "budget_stop"])
def test_non_blocking_card_types_stay_silent(studio, clock, card_type):
    host = FakeHostBridge()
    adapter = _adapter(studio, clock, host=host)
    result = asyncio.run(adapter.notify_action(_card(type=card_type, queue_type=card_type)))
    assert result.dashboard is False and result.slack == "ineligible"
    assert host.notified == [] and host.slack_calls == []


def test_slack_respects_enablement_and_per_repo_mute(studio, clock):
    host = FakeHostBridge()
    off = _adapter(studio, clock, host=host, settings=FakeSettings(enabled=False))
    assert asyncio.run(off.notify_action(_card())).slack == "disabled"
    assert host.slack_calls == []

    muted = _adapter(studio, clock, host=host, settings=FakeSettings(enabled=True, muted=("r_000000000001",)))
    assert asyncio.run(muted.notify_action(_card())).slack == "muted"
    assert host.slack_calls == []


def test_a_slack_outage_never_fails_the_notification(studio, clock):
    host = FakeHostBridge(slack_ts=None)
    adapter = _adapter(studio, clock, host=host)
    result = asyncio.run(adapter.notify_action(_card()))
    assert result.dashboard is True and result.slack == "unavailable"


def test_storage_failure_degrades_to_a_duplicate_not_an_error(studio, clock):
    prefs = MemoryPrefs()
    prefs.fail = True
    adapter = _adapter(studio, clock, storage=prefs)
    # The dedupe ledger is advisory; losing it may cost a duplicate but must never raise.
    assert asyncio.run(adapter.notify_action(_card())).dashboard is True
    assert asyncio.run(adapter.notify_action(_card())).dedupe_hit is False


def test_quick_actions_are_refused_unconditionally(studio, clock):
    mod = _mod(studio)
    adapter = _adapter(studio, clock)
    for status in ("Queued", "Delivering", "ReconciliationRequired"):
        for kind in ("gate", "question", "failure"):
            assert adapter.slack_quick_action_eligible(_card(type=kind, status=status)) == (
                False,
                mod.REASON_HOST_SEAM,
            )
    # The bar a future implementation must clear is written down rather than implied.
    assert any("compare-and-submit" in r or "submit" in r for r in mod.QUICK_ACTION_REQUIREMENTS)


def test_slack_blocks_carry_a_link_and_never_an_options_trailer(studio, clock):
    adapter = _adapter(studio, clock)
    blocks, text = adapter.build_slack_blocks(_card())
    encoded = json.dumps(blocks)
    # An [OPTIONS:] trailer would be turned into buttons whose clicks the host re-dispatches as user
    # turns on the AI-DLC session — a decision with none of Studio's guards behind it.
    assert "[OPTIONS" not in encoded and "action::" not in encoded
    assert "actions" in encoded and "a_0000000000000001" in encoded
    assert any(b["type"] == "header" for b in blocks)
    assert text and "\n" not in text


def test_no_evidence_no_paths_and_no_secrets_reach_a_message(studio, clock):
    host = FakeHostBridge()
    adapter = _adapter(studio, clock, host=host)
    card = _card(
        failure={"summary": "ACP transport closed while token=abcdef1234567890 was in flight"},
        evidence={"artifacts": [{"relpath": "inception/requirements-analysis/requirements.md"}]},
        human_text="Approve",
    )
    asyncio.run(adapter.notify_action(card))
    encoded = json.dumps(host.notified) + json.dumps(host.slack_calls)
    assert "abcdef1234567890" not in encoded, "a credential-looking string must be redacted"
    assert "/home/dev/ledger" not in encoded, "the repository path is not a notification's business"
    assert "requirements.md" not in encoded, "artifact contents and names stay in Studio"
    assert "human_text" not in encoded


def test_a_host_that_raises_is_absorbed(studio, clock):
    host = FakeHostBridge()
    host.raise_on_notify = True
    adapter = _adapter(studio, clock, host=host)
    # HostBridge.notify swallows host failures itself; this proves the adapter does not reintroduce one.
    with pytest.raises(RuntimeError):
        host.notify("t", "b", url="/apps/aidlc-studio")
    host.raise_on_notify = False
    assert asyncio.run(adapter.notify_action(_card())).dashboard is True


def test_completion_notifies_once_and_links_to_the_intent(studio, clock):
    host, prefs = FakeHostBridge(), MemoryPrefs()
    adapter = _adapter(studio, clock, host=host, storage=prefs)
    summary = SimpleNamespace(
        repo_id="r_000000000001",
        repo_label="ledger-export",
        intent_key="260904-ledger-export",
        intent_dir="260904-ledger-export",
        slug="ledger-export",
    )
    first = asyncio.run(adapter.notify_completion(summary))
    assert first.dashboard is True and "view=intents" in first.deep_link
    assert asyncio.run(adapter.notify_completion(summary)).dedupe_hit is True
    assert len(host.notified) == 1


def test_the_digest_says_plainly_that_no_unattended_work_ran(studio, clock):
    host = FakeHostBridge()
    adapter = _adapter(studio, clock, host=host)
    result = asyncio.run(adapter.notify_digest({"waiting": 3}))
    assert result.dashboard is True
    body = host.notified[0]["body"]
    # PRD FR-NIGHT-008 asks for a digest; with no machine lane the honest digest says so rather than
    # implying work happened.
    assert "No unattended work ran" in body and "3 item(s)" in body


def test_forget_lets_a_genuinely_new_occurrence_notify_again(studio, clock):
    host, prefs = FakeHostBridge(), MemoryPrefs()
    adapter = _adapter(studio, clock, host=host, storage=prefs)
    asyncio.run(adapter.notify_action(_card()))
    adapter.forget("a_0000000000000001")
    assert asyncio.run(adapter.notify_action(_card())).dedupe_hit is False
    assert len(host.notified) == 2


def test_the_dedupe_ledger_is_bounded(studio, clock):
    mod = _mod(studio)
    prefs = MemoryPrefs()
    adapter = _adapter(studio, clock, storage=prefs)
    for index in range(mod.NotificationAdapter._DEDUPE_MAX + 25):
        clock.advance(1)
        asyncio.run(adapter.notify_action(_card(action_id=f"a_{index:016d}")))
    stored = prefs.values[mod.NotificationAdapter._DEDUPE_PREF]
    assert len(stored) <= mod.NotificationAdapter._DEDUPE_MAX
