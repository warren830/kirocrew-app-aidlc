"""Telling the user something needs them — without ever becoming a way to decide.

Two channels, both courtesy channels: a dashboard notification and an owner Slack DM. Neither can carry
a decision, and that restriction is the whole design of this module rather than an omission:

* **No Slack quick actions in v1.** The one host mechanism that looked usable — an ``[OPTIONS: …]`` block
  on a thread linked to the intent's canonical session — re-dispatches whatever the user clicks as a bare
  user turn on the ``aidlc`` slot. That turn would reach the AI-DLC conductor with no compare-and-submit
  behind it (so a card the user is no longer looking at could resolve a boundary that has moved), no
  durable ``Delivering`` record committed first, and no cursor switch — and a second, non-decision button
  such as "Open in Studio" would be typed into the conversation as if it were an answer. So
  ``slack_quick_action_eligible`` always refuses, with a reason code the Settings page shows. The
  eligibility rules a future host seam would need are written down in that method so re-enabling it is a
  deliberate act with a test to satisfy, not a one-line flip.
* **A notification never carries evidence.** No artifact body, no submitted human text, no repository
  path beyond the label, no credentials. A Slack message is a sentence and a deep link; the decision is
  made in Studio, where the evidence and the staleness check are.

Failure here is always swallowed. Every caller reaches this module *after* the thing worth knowing about
is already durable, so a Slack outage or a missing host attribute must never fail the operation that
earned the notification. Each result says what actually happened instead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from . import constants as C
from . import security as S

logger = logging.getLogger(f"kirocrew.app.{C.APP_NAME}.notifications")

#: The classes of event worth interrupting a person for (PRD FR-SLK-003). A stage transition is not one:
#: notifying every transition trains the user to mute the channel, which costs them the gate as well.
NOTIFY_KINDS = (
    "gate",
    "question",
    "failure",
    "circuit_breaker",
    "delivery_uncertain",
    "recovery",
    "install_conflict",
    "completion",
)

#: Card types that map onto a notifiable class. Anything absent is deliberately silent.
_TYPE_TO_KIND = {
    "gate": "gate",
    "question": "question",
    "failure": "failure",
    "circuit_breaker": "circuit_breaker",
    "delivery_uncertain": "delivery_uncertain",
    "recovery": "recovery",
    "install_conflict": "install_conflict",
}

#: Two dedupe classes per action: the first time it needs attention, and the first time its delivery
#: becomes uncertain. A card that stays open for a day produces one notification, not one per tick.
_UNCERTAIN_STATUSES = ("DeliveryUncertain", "ReconciliationRequired")

_SLACK_SENT = "sent"
_SLACK_MUTED = "muted"
_SLACK_UNAVAILABLE = "unavailable"
_SLACK_INELIGIBLE = "ineligible"
_SLACK_DISABLED = "disabled"

#: Slack's own limits, and Studio's stricter one. A block that exceeds Slack's limit is rejected by the
#: API, so a long decision brief is trimmed here where the trim can be marked.
_SLACK_SECTION_LIMIT = 2900
_STUDIO_EXCERPT_LIMIT = 600
_BODY_LIMIT = 500

#: The eligibility bar a future Slack quick action would have to clear. Kept as data so the rules are
#: already reviewable and testable while the feature itself is refused.
QUICK_ACTION_REQUIREMENTS = (
    "card.type == 'gate'",
    "card.status == 'Queued'",
    "captured evidence stable",
    "no blocking finding for the intent",
    "no live critical card for the intent",
    "rendered brief <= 2800 characters",
    "host seam that routes the click through submit + the host send with the same two-phase record",
)
REASON_HOST_SEAM = "host_seam_unavailable"


@dataclass(frozen=True, slots=True)
class NotificationResult:
    """What actually happened, so a caller (and a test) can assert it without reading Slack."""

    dashboard: bool
    slack: str  # sent | muted | unavailable | ineligible | disabled
    slack_ts: str | None
    deep_link: str
    dedupe_hit: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "dashboard": self.dashboard,
            "slack": self.slack,
            "slack_ts": self.slack_ts,
            "deep_link": self.deep_link,
            "dedupe_hit": self.dedupe_hit,
        }


def _text(value: Any, limit: int | None = None) -> str:
    out = "" if value is None else str(value)
    if limit is not None and len(out) > limit:
        return out[: limit - 1].rstrip() + "…"
    return out


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field from a card whether it arrives as the JSON mapping or as a record."""
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


class NotificationAdapter:
    """Dashboard and Slack delivery for the events that need a human.

    ``storage`` provides the dedupe ledger (a preference row, so it survives a restart the way the
    notification history should), ``settings`` the enablement and per-repo mute, ``activity`` the record
    that a notification was attempted — which is what makes "I never got told" answerable.
    """

    #: Preference key holding ``{dedupe_key: iso}``. A preference rather than a table because it is a
    #: small, bounded, purely advisory cache: losing it costs one duplicate notification, and a schema
    #: migration for that would be a worse trade.
    _DEDUPE_PREF = "notifications.sent"
    _DEDUPE_MAX = 500

    def __init__(self, host: Any, storage: Any, settings: Any, activity: Any, clock: Any) -> None:
        self._host = host
        self._storage = storage
        self._settings = settings
        self._activity = activity
        self._clock = clock

    # ---- links ------------------------------------------------------------ #

    def deep_link(self, action_id: str) -> str:
        """The one link every message carries: identifiers only, no authorisation.

        A deep link cannot approve anything. It names an app route and an action id; the destination
        gateway still authenticates the user and Studio still re-checks the evidence before a submit.
        """
        return f"{C.DEEP_LINK_BASE}?view=actions&action={_text(action_id)}"

    def intent_link(self, repo_id: str, intent_key: str) -> str:
        return f"{C.DEEP_LINK_BASE}?view=intents&repo={_text(repo_id)}&intent={_text(intent_key)}"

    # ---- the public surface ---------------------------------------------- #

    async def notify_action(self, card: Any) -> NotificationResult:
        """Tell the user about one actionable card, at most once per class.

        Returns without sending when the card's type is not one of the notifiable classes: silence is
        the correct answer for a revision in progress or a plain run command.
        """
        action_id = _text(_get(card, "action_id"))
        status = _text(_get(card, "status"))
        card_type = _text(_get(card, "queue_type") or _get(card, "type"))
        kind = _TYPE_TO_KIND.get(card_type)
        link = self.deep_link(action_id)

        if kind is None:
            return NotificationResult(False, _SLACK_INELIGIBLE, None, link, False)

        status_class = "uncertain" if status in _UNCERTAIN_STATUSES else "open"
        dedupe_key = f"{action_id}:{status_class}"
        if await self._seen(dedupe_key):
            return NotificationResult(False, _SLACK_INELIGIBLE, None, link, True)

        title, body = self._render(card, kind)
        dashboard = self._notify_dashboard(title, body, link, action_id=action_id, kind=kind)
        slack_state, slack_ts = await self._notify_slack(card, kind=kind, title=title)
        await self._mark_seen(dedupe_key)
        await self._record(
            kind=f"notification.{kind}",
            severity=_text(_get(card, "severity")) or "info",
            card=card,
            params={"dashboard": dashboard, "slack": slack_state, "deep_link": link},
        )
        return NotificationResult(dashboard, slack_state, slack_ts, link, False)

    async def notify_completion(self, summary: Any) -> NotificationResult:
        """Tell the user an intent finished. One message, no evidence, no action."""
        repo_id = _text(_get(summary, "repo_id"))
        intent_key = _text(_get(summary, "intent_key") or _get(summary, "intent_dir"))
        link = self.intent_link(repo_id, intent_key)
        dedupe_key = f"completion:{repo_id}:{intent_key}"
        if await self._seen(dedupe_key):
            return NotificationResult(False, _SLACK_INELIGIBLE, None, link, True)

        label = _text(_get(summary, "repo_label") or repo_id)
        slug = _text(_get(summary, "slug") or intent_key)
        title = f"AI-DLC finished: {slug}"
        body = _text(f"{label} · {slug} reached Completed. Nothing is waiting for you.", _BODY_LIMIT)
        dashboard = self._notify_dashboard(title, body, link, action_id="", kind="completion")
        slack_state, slack_ts = (_SLACK_DISABLED, None)
        if self._slack_enabled(repo_id):
            blocks = self._completion_blocks(label, slug, link)
            slack_ts = await self._host.slack_dm(blocks, title)
            slack_state = _SLACK_SENT if slack_ts else _SLACK_UNAVAILABLE
        await self._mark_seen(dedupe_key)
        await self._record(
            kind="notification.completion",
            severity="info",
            card=None,
            params={"repo_id": repo_id, "intent_key": intent_key, "dashboard": dashboard, "slack": slack_state},
        )
        return NotificationResult(dashboard, slack_state, slack_ts, link, False)

    async def notify_digest(self, ledger: Mapping[str, Any]) -> NotificationResult:
        """The morning digest. In v1 it reports honestly that no unattended work ran.

        There is no machine lane, so there is nothing to summarise beyond what is waiting. Saying that
        plainly is better than an empty digest that looks like a bug, and better than omitting the
        feature and leaving the PRD's field list unanswerable.
        """
        waiting = int(ledger.get("waiting", 0) or 0)
        link = f"{C.DEEP_LINK_BASE}?view=actions"
        title = "AI-DLC Studio overnight digest"
        body = _text(
            f"No unattended work ran: automation is unavailable until a machine lane is proven "
            f"({C.MACHINE_LANE_REASON}). {waiting} item(s) are waiting for you.",
            _BODY_LIMIT,
        )
        dashboard = self._notify_dashboard(title, body, link, action_id="", kind="completion")
        await self._record(kind="notification.digest", severity="info", card=None, params=dict(ledger))
        return NotificationResult(dashboard, _SLACK_DISABLED, None, link, False)

    # ---- Slack -------------------------------------------------------------- #

    def slack_quick_action_eligible(self, card: Any) -> tuple[bool, str]:
        """Always ``(False, "host_seam_unavailable")`` in v1 — see the module docstring.

        The refusal is unconditional and does not depend on the card, because the missing piece is a host
        capability, not a property of any particular decision. ``QUICK_ACTION_REQUIREMENTS`` records what
        a future implementation must also satisfy so the bar is already written down.
        """
        return False, REASON_HOST_SEAM

    def build_slack_blocks(self, card: Any) -> tuple[list[dict], str]:
        """Blocks for one card: what and where, then the link. Never an options trailer.

        The absence of a trailing ``[OPTIONS: …]`` is load-bearing, not cosmetic: the host turns such a
        trailer into buttons whose clicks it re-dispatches as user turns, which would let a Slack tap
        reach the AI-DLC conductor without a single one of Studio's guards.
        """
        action_id = _text(_get(card, "action_id"))
        card_type = _text(_get(card, "queue_type") or _get(card, "type"))
        repo = _get(card, "repo") or {}
        intent = _get(card, "intent") or {}
        stage = _get(card, "stage") or {}
        link = self.deep_link(action_id)

        repo_label = _text(_get(repo, "label"), 80) or _text(_get(card, "repo_id"), 80)
        slug = _text(_get(intent, "slug") or _get(intent, "intent_dir"), 80)
        stage_slug = _text(_get(stage, "slug"), 60)
        waiting = _text(_get(card, "waiting_since"))
        headline = self._headline_text(card)

        heading = f"{_KIND_TITLES.get(_TYPE_TO_KIND.get(card_type, ''), 'AI-DLC Studio')}: {slug}"
        context = f"{repo_label}"
        if stage_slug:
            context += f" · {stage_slug}"
        if waiting:
            context += f" · waiting since {waiting}"

        blocks: list[dict] = [
            {"type": "header", "text": {"type": "plain_text", "text": _text(heading, 150)}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": _text(context, 300)}]},
        ]
        if headline:
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": _text(headline, _SLACK_SECTION_LIMIT)}}
            )
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"Decide in Studio: {link}"},
            }
        )
        return blocks, _text(heading, 150)

    def _completion_blocks(self, repo_label: str, slug: str, link: str) -> list[dict]:
        return [
            {"type": "header", "text": {"type": "plain_text", "text": _text(f"Completed: {slug}", 150)}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": _text(repo_label, 300)}]},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"Open in Studio: {link}"}},
        ]

    async def _notify_slack(self, card: Any, *, kind: str, title: str) -> tuple[str, str | None]:
        repo_id = _text(_get(_get(card, "repo") or {}, "repo_id") or _get(card, "repo_id"))
        if not self._slack_enabled(repo_id):
            return (_SLACK_MUTED if self._muted(repo_id) else _SLACK_DISABLED), None
        blocks, text = self.build_slack_blocks(card)
        ts = await self._host.slack_dm(blocks, text)
        return (_SLACK_SENT if ts else _SLACK_UNAVAILABLE), ts

    def _slack_enabled(self, repo_id: str) -> bool:
        try:
            slack = self._settings.slack()
        except Exception:  # settings not loaded yet: treat as off rather than guessing
            return False
        return bool(slack.get("enabled")) and not self._muted(repo_id)

    def _muted(self, repo_id: str) -> bool:
        try:
            muted: Sequence[str] = self._settings.slack().get("muted_repo_ids") or ()
        except Exception:
            return False
        return bool(repo_id) and repo_id in set(muted)

    # ---- dashboard --------------------------------------------------------- #

    def _notify_dashboard(self, title: str, body: str, link: str, *, action_id: str, kind: str) -> bool:
        meta = {"app": C.APP_NAME, "kind": kind}
        if action_id:
            meta["action_id"] = action_id
        return bool(self._host.notify(title, body, url=link, meta=meta))

    # ---- rendering --------------------------------------------------------- #

    def _render(self, card: Any, kind: str) -> tuple[str, str]:
        """Title and body for the dashboard toast.

        English only, and deliberately so: the host's notification bus has no locale, so a translated
        title would be wrong for anyone whose dashboard language differs from the one that happened to be
        active when the notification fired. The UI renders the localized version of the same card.
        """
        intent = _get(card, "intent") or {}
        repo = _get(card, "repo") or {}
        stage = _get(card, "stage") or {}
        slug = _text(_get(intent, "slug") or _get(intent, "intent_dir"), 60)
        repo_label = _text(_get(repo, "label"), 60)
        stage_slug = _text(_get(stage, "slug"), 60)

        title = f"{_KIND_TITLES.get(kind, 'AI-DLC Studio')}: {slug}" if slug else _KIND_TITLES.get(kind, "AI-DLC Studio")
        pieces = [p for p in (repo_label, stage_slug) if p]
        headline = self._headline_text(card)
        body = " · ".join(pieces)
        if headline:
            body = f"{body} — {headline}" if body else headline
        return _text(title, 150), _text(body, _BODY_LIMIT)

    def _headline_text(self, card: Any) -> str:
        """A short, safe sentence about the card.

        Prefers the type's own summary over anything the model wrote. Where a value does come from disk
        (a normalized error message, a stage name) it is redacted and trimmed, because this string
        reaches a Slack workspace and a notification history file.
        """
        failure = _get(card, "failure") or {}
        if isinstance(failure, Mapping) and failure.get("summary"):
            return S.redact(_text(failure.get("summary"), _STUDIO_EXCERPT_LIMIT))
        headline = _get(card, "headline") or {}
        params = _get(headline, "params") or {}
        for key in ("summary", "reason", "stage", "detail"):
            value = params.get(key) if isinstance(params, Mapping) else None
            if value:
                return S.redact(_text(value, _STUDIO_EXCERPT_LIMIT))
        primary = _get(card, "primary") or {}
        decision = _text(_get(primary, "decision"))
        return f"Waiting for your decision ({decision})." if decision else "Waiting for you."

    # ---- dedupe ledger ----------------------------------------------------- #

    async def _seen(self, dedupe_key: str) -> bool:
        import asyncio

        ledger = await asyncio.to_thread(self._read_ledger)
        return dedupe_key in ledger

    async def _mark_seen(self, dedupe_key: str) -> None:
        import asyncio

        await asyncio.to_thread(self._write_seen, dedupe_key)

    def _read_ledger(self) -> dict[str, str]:
        try:
            value = self._storage.pref_get(self._DEDUPE_PREF)
        except Exception:  # a notification must not fail because its cache is unreadable
            return {}
        return dict(value) if isinstance(value, Mapping) else {}

    def _write_seen(self, dedupe_key: str) -> None:
        ledger = self._read_ledger()
        ledger[dedupe_key] = self._clock.iso()
        if len(ledger) > self._DEDUPE_MAX:
            # Oldest first by timestamp; the ledger is advisory, so trimming it can only ever cost a
            # duplicate notification for an action nobody has touched in a very long time.
            for key, _ in sorted(ledger.items(), key=lambda kv: kv[1])[: len(ledger) - self._DEDUPE_MAX]:
                ledger.pop(key, None)
        try:
            self._storage.pref_set(self._DEDUPE_PREF, ledger)
        except Exception:
            logger.debug("could not persist the notification dedupe ledger", exc_info=True)

    def forget(self, action_id: str) -> None:
        """Drop an action's dedupe entries so a genuinely new occurrence notifies again."""
        ledger = self._read_ledger()
        changed = False
        for status_class in ("open", "uncertain"):
            if ledger.pop(f"{action_id}:{status_class}", None) is not None:
                changed = True
        if changed:
            try:
                self._storage.pref_set(self._DEDUPE_PREF, ledger)
            except Exception:
                logger.debug("could not update the notification dedupe ledger", exc_info=True)

    # ---- activity ---------------------------------------------------------- #

    async def _record(self, *, kind: str, severity: str, card: Any, params: Mapping[str, Any]) -> None:
        if self._activity is None:
            return
        repo = _get(card, "repo") or {} if card is not None else {}
        intent = _get(card, "intent") or {} if card is not None else {}
        stage = _get(card, "stage") or {} if card is not None else {}
        try:
            await self._activity.record(
                kind=kind,
                severity=severity if severity in C.SEVERITY else "info",
                source="studio",
                repo_id=_text(_get(repo, "repo_id")) or None,
                space=_text(_get(card, "space")) or None if card is not None else None,
                intent_dir=_text(_get(intent, "intent_dir")) or None,
                stage=_text(_get(stage, "slug")) or None,
                action_id=_text(_get(card, "action_id")) or None if card is not None else None,
                params=dict(params),
            )
        except Exception:  # an unrecorded notification is a lesser failure than a failed operation
            logger.debug("could not record a notification in activity", exc_info=True)


#: English titles for the dashboard toast and the Slack header. The UI translates its own copy; these
#: two surfaces have no locale to translate into.
_KIND_TITLES = {
    "gate": "Approval needed",
    "question": "Question waiting",
    "failure": "AI-DLC run failed",
    "circuit_breaker": "Stopped after repeated failures",
    "delivery_uncertain": "Delivery uncertain",
    "recovery": "Recovery needed",
    "install_conflict": "Install conflict",
    "completion": "AI-DLC finished",
}
