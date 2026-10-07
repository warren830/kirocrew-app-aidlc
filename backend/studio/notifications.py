"""Telling the user something needs them — a dashboard toast and an owner Slack DM, never a way to decide.

* **No Slack quick actions.** The one host mechanism re-dispatches a click as a bare user turn: no
  compare-and-submit, no durable ``Delivering`` record (C13/review P02). So no message ends in an
  ``[OPTIONS: …]`` trailer (the host would turn it into buttons); a Slack message is a sentence and a link.
* **No evidence.** No artifact body, no submitted human text, no path, and text from disk is redacted: this
  reaches a Slack workspace and the host's notification history.
* **Failure is swallowed.** Every caller arrives after the thing worth knowing about is durable.

English only: the host's notification bus has no locale, and the UI renders its own localized copy.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from . import constants as C
from . import security as S

logger = logging.getLogger(f"kirocrew.app.{C.APP_NAME}.notifications")

#: Card types worth interrupting a person for (PRD FR-SLK-003) and their English titles. Anything absent
#: stays silent: notifying every stage transition trains the user to mute the channel, gates included.
_CARD_TITLES = {
    "gate": "Approval needed",
    "question": "Question waiting",
    "failure": "AI-DLC run failed",
    "circuit_breaker": "Stopped after repeated failures",
    "delivery_uncertain": "Delivery uncertain",
    "recovery": "Recovery needed",
    "install_conflict": "Install conflict",
}
#: A card's second notification class: the first time its delivery becomes uncertain is new information.
_UNCERTAIN_STATUSES = ("ReconciliationRequired",)

#: The one registered Activity kind for every notification; which one it was travels in ``params.kind``.
NOTIFICATION_ACTIVITY_KIND = "notification.sent"

_SECTION_LIMIT = 2900  # Slack rejects a longer section block, so the trim happens here where it can be marked
_EXCERPT_LIMIT = 600
_BODY_LIMIT = 500


@dataclass(frozen=True, slots=True)
class NotificationResult:
    """What actually happened, so a caller (and a test) can assert it without reading Slack."""

    dashboard: bool
    slack: str  # sent | muted | unavailable | ineligible | disabled
    slack_ts: str | None
    deep_link: str
    dedupe_hit: bool

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(slots=True)
class _Message:
    """One notification, built per kind and delivered by the shared pipeline in ``notify``."""

    kind: str  # params.kind on the Activity row and meta.kind on the toast
    dedupe_key: str
    link: str
    title: str
    body: str
    severity: str = "info"
    action_id: str = ""
    repo_id: str = ""
    slack: tuple[list[dict], str] | None = None  # (blocks, text); None means dashboard only
    scope: dict = field(default_factory=dict)  # the Activity row's repo/intent/stage/action columns
    params: dict = field(default_factory=dict)  # Activity params beside kind/dashboard/slack


def _text(value: Any, limit: int | None = None) -> str:
    out = "" if value is None else str(value)
    return out[: limit - 1].rstrip() + "…" if limit is not None and len(out) > limit else out


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """A field of a card whether it arrives as the JSON mapping or as a record."""
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def _blocks(heading: str, context: str, *sections: str) -> list[dict]:
    return [
        {"type": "header", "text": {"type": "plain_text", "text": _text(heading, 150)}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": _text(context, 300)}]},
        *({"type": "section", "text": {"type": "mrkdwn", "text": text}} for text in sections if text),
    ]


class NotificationAdapter:
    """Dashboard and Slack delivery for the events that need a human. Every attempt is an Activity row, which
    is what makes "I never got told" answerable."""

    #: ``{dedupe_key: iso}``. A preference rather than a table: losing it costs one duplicate notification.
    _DEDUPE_PREF = "notifications.sent"
    _DEDUPE_MAX = 500

    def __init__(self, host: Any, storage: Any, settings: Any, activity: Any, clock: Any) -> None:
        self._host = host
        self._storage = storage
        self._settings = settings
        self._activity = activity
        self._clock = clock

    def deep_link(self, action_id: str) -> str:
        """Identifiers only: a link authorises nothing — the gateway authenticates and Studio re-checks."""
        return f"{C.DEEP_LINK_BASE}?view=actions&action={_text(action_id)}"

    def _intent_link(self, repo_id: str, intent_key: str) -> str:
        return f"{C.DEEP_LINK_BASE}?view=intents&repo={_text(repo_id)}&intent={_text(intent_key)}"

    async def notify(self, kind: str, subject: Any = None, **fields: Any) -> NotificationResult:
        """The one entry point, at most once per dedupe key:

        * ``"action"`` — ``subject`` is a card; silent unless its type is notifiable.
        * ``"tool_approval"`` — keyword fields ``repo_id, repo_label, intent_key, intent_label, slot_key,
          approval, turn``: a conversation parked on a host tool approval (dashboard only; answered in chat).
        * ``"completion"`` — ``subject`` is an intent summary: the intent reached Completed.
        """
        build = {"action": self._action, "tool_approval": self._tool_approval, "completion": self._completion}[kind]
        message = build(subject, **fields)
        if isinstance(message, str):  # a card type that is not worth interrupting anyone for
            return NotificationResult(False, "ineligible", None, message, False)
        if message.dedupe_key in await asyncio.to_thread(self._read_ledger):
            return NotificationResult(False, "ineligible", None, message.link, True)
        dashboard = self._dashboard(message)
        slack, ts = await self._slack(message)
        await asyncio.to_thread(self._mark_seen, message.dedupe_key)
        await self._record(message, dashboard=dashboard, slack=slack)
        return NotificationResult(dashboard, slack, ts, message.link, False)

    # ---- the three kinds ----

    def _action(self, card: Any) -> _Message | str:
        action_id = _text(_get(card, "action_id"))
        link = self.deep_link(action_id)
        card_type = _text(_get(card, "queue_type") or _get(card, "type"))
        if card_type not in _CARD_TITLES:
            return link
        repo, intent, stage = (_get(card, name) or {} for name in ("repo", "intent", "stage"))
        repo_id = _text(_get(repo, "repo_id") or _get(card, "repo_id"))
        slug = _text(_get(intent, "slug") or _get(intent, "intent_dir"))
        repo_label = _text(_get(repo, "label"))
        stage_slug = _text(_get(stage, "slug"))
        headline = self._headline(card)

        title = f"{_CARD_TITLES[card_type]}: {_text(slug, 60)}" if slug else _CARD_TITLES[card_type]
        body = " · ".join(p for p in (_text(repo_label, 60), _text(stage_slug, 60)) if p)
        body = f"{body} — {headline}" if body and headline else body or headline

        heading = f"{_CARD_TITLES[card_type]}: {_text(slug, 80)}"
        context = _text(repo_label, 80) or _text(_get(card, "repo_id"), 80)
        if stage_slug:
            context += f" · {_text(stage_slug, 60)}"
        waiting = _text(_get(card, "waiting_since"))
        if waiting:
            context += f" · waiting since {waiting}"
        blocks = _blocks(heading, context, _text(headline, _SECTION_LIMIT), f"Decide in Studio: {link}")

        status_class = "uncertain" if _text(_get(card, "status")) in _UNCERTAIN_STATUSES else "open"
        return _Message(
            kind=card_type, dedupe_key=f"{action_id}:{status_class}", link=link,
            title=_text(title, 150), body=_text(body, _BODY_LIMIT),
            severity=_text(_get(card, "severity")) or "info", action_id=action_id, repo_id=repo_id,
            slack=(blocks, _text(heading, 150)),
            scope={
                "repo_id": _text(_get(repo, "repo_id")) or None,
                "space": _text(_get(card, "space")) or None,
                "intent_dir": _text(_get(intent, "intent_dir")) or None,
                "stage": stage_slug or None,
                "action_id": action_id or None,
            },
            params={"deep_link": link},
        )

    def _tool_approval(
        self, _subject: Any = None, *, repo_id: str, repo_label: str, intent_key: str, intent_label: str,
        slot_key: str, approval: Mapping[str, str], turn: str = "",
    ) -> _Message:
        """The link opens the intent, whose row says what is asked and opens the conversation with the
        host's own approve/reject controls: Studio makes the wait visible and never answers it (A31)."""
        # The approval's own id when the host gives one; otherwise what it asks plus the turn it was asked
        # in — an empty description alone would be one key per slot forever, swallowing every later approval.
        identity = _text(approval.get("request_id")) or S.sha256_text(
            f"{_text(approval.get('tool'))}\0{_text(approval.get('tool_input'))}\0{_text(turn)}"
        )
        label = _text(intent_label or intent_key, 60)
        body = f"{_text(repo_label or repo_id, 60)} · {label} — {_text(approval.get('tool')) or 'a tool call'}"
        command = _text(approval.get("tool_input"))
        return _Message(
            kind="approval", dedupe_key=f"approval:{_text(slot_key)}:{identity}",
            link=self._intent_link(repo_id, intent_key),
            title=_text(f"AI-DLC is waiting for your approval: {label}", 150),
            body=_text(f"{body}: {command}" if command else body, _BODY_LIMIT), severity="attention",
            params={"repo_id": repo_id, "intent_key": intent_key, "slot_key": slot_key},
        )

    def _completion(self, summary: Any) -> _Message:
        repo_id = _text(_get(summary, "repo_id"))
        intent_key = _text(_get(summary, "intent_key") or _get(summary, "intent_dir"))
        link = self._intent_link(repo_id, intent_key)
        label = _text(_get(summary, "repo_label") or repo_id)
        slug = _text(_get(summary, "slug") or intent_key)
        title = f"AI-DLC finished: {slug}"
        return _Message(
            kind="completion", dedupe_key=f"completion:{repo_id}:{intent_key}", link=link, title=title,
            body=_text(f"{label} · {slug} reached Completed. Nothing is waiting for you.", _BODY_LIMIT),
            repo_id=repo_id, slack=(_blocks(f"Completed: {slug}", label, f"Open in Studio: {link}"), title),
            params={"repo_id": repo_id, "intent_key": intent_key},
        )

    def _headline(self, card: Any) -> str:
        """A short, safe sentence: the type's own summary over anything the model wrote, redacted and trimmed
        wherever it came from disk."""
        failure = _get(card, "failure") or {}
        if isinstance(failure, Mapping) and failure.get("summary"):
            return S.redact(_text(failure.get("summary"), _EXCERPT_LIMIT))
        params = _get(_get(card, "headline") or {}, "params") or {}
        for key in ("summary", "reason", "stage", "detail"):
            value = params.get(key) if isinstance(params, Mapping) else None
            if value:
                return S.redact(_text(value, _EXCERPT_LIMIT))
        decision = _text(_get(_get(card, "primary") or {}, "decision"))
        return f"Waiting for your decision ({decision})." if decision else "Waiting for you."

    # ---- channels ----

    def _dashboard(self, message: _Message) -> bool:
        if not self._setting("notifications", "dashboard", True):
            return False
        meta = {"app": C.APP_NAME, "kind": message.kind}
        if message.action_id:
            meta["action_id"] = message.action_id
        return bool(self._host.notify(message.title, message.body, url=message.link, meta=meta))

    async def _slack(self, message: _Message) -> tuple[str, str | None]:
        if message.slack is None:
            return "disabled", None
        muted = bool(message.repo_id) and message.repo_id in set(self._setting("slack", "muted_repo_ids", ()) or ())
        if muted or not self._setting("slack", "enabled", False):
            return ("muted" if muted else "disabled"), None
        ts = await self._host.slack_dm(*message.slack)
        return ("sent" if ts else "unavailable"), ts

    def _setting(self, section: str, key: str, default: Any) -> Any:
        try:
            return getattr(self._settings, section)().get(key, default)
        except Exception:  # settings not loaded, or a stand-in without the section: assume the default
            return default

    # ---- dedupe ledger ----

    def _read_ledger(self) -> dict[str, str]:
        try:
            value = self._storage.pref_get(self._DEDUPE_PREF)
        except Exception:  # an unreadable cache must not fail a notification; it costs a duplicate at most
            return {}
        return dict(value) if isinstance(value, Mapping) else {}

    def _mark_seen(self, dedupe_key: str) -> None:
        ledger = self._read_ledger()
        ledger[dedupe_key] = self._clock.iso()
        if len(ledger) > self._DEDUPE_MAX:
            # Oldest first: trimming can only cost a duplicate for an action nobody touched in a long time.
            for key, _ in sorted(ledger.items(), key=lambda kv: kv[1])[: len(ledger) - self._DEDUPE_MAX]:
                ledger.pop(key, None)
        try:
            self._storage.pref_set(self._DEDUPE_PREF, ledger)
        except Exception:
            logger.debug("could not persist the notification dedupe ledger", exc_info=True)

    async def _record(self, message: _Message, *, dashboard: bool, slack: str) -> None:
        params = {"kind": message.kind, **message.params, "dashboard": dashboard}
        if message.slack is not None:
            params["slack"] = slack
        try:
            await self._activity.record(
                kind=NOTIFICATION_ACTIVITY_KIND,
                severity=message.severity if message.severity in C.SEVERITY else "info",
                source="studio",
                params=params,
                **{key: message.scope.get(key) for key in ("repo_id", "space", "intent_dir", "stage", "action_id")},
            )
        except Exception:  # an unrecorded notification is a lesser failure than a failed operation
            logger.debug("could not record a notification in activity", exc_info=True)
