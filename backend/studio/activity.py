"""One timeline from two authorities: Studio records what Studio did, AI-DLC's append-only audit shards what
the workflow did, and the boundary between them is never blurred.

1. **AI-DLC rows are projected, never stored.** ``timeline`` reads ``snap.audit.events`` live and ``record``
   refuses ``source="aidlc"``: a copy would keep asserting history that a shard rotation, a ``REDO`` or a
   scope change removed from disk.
2. **The source is part of the row**, and an AI-DLC row carries its verbatim block in ``raw``, so "who says
   the gate was approved?" is answered by the engine's own bytes.
3. **Keys and params, never prose** (C11): ``activity.<kind>``, ``audit.<EVENT>``, and ``audit.unknown_event``
   for a name outside the taxonomy, which real installs write (``LOOP_RUN_STARTED``; FORMAT-NOTES §6).
4. **Redaction happens at the boundary that leaks.** Params are redacted at write; ``human_text`` is stored
   verbatim (retention removes it); the diagnostics export redacts everything again and scrubs absolute paths
   outside the registered roots (``RULE_LEARNED``'s ``Destination`` carries one), because an export leaves
   the machine.
"""

from __future__ import annotations

import asyncio
import dataclasses
import heapq
import re
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from . import constants as C
from . import security
from .errors import StudioError

# Re-exported so §1.17's names resolve here; ``Storage`` constructs them and may not import this layer.
from .storage import ActivityQuery, ActivityRow, EvidenceRef
from .wire import WireRecord

if TYPE_CHECKING:  # pragma: no cover - typing only; snapshots and events are duck-typed below
    from .aidlc_reader import AuditEvent
    from .constants import Clock
    from .projection import IntentSnapshot
    from .storage import Storage

#: Every Studio-originated kind, verbatim from §1.17; its i18n key is ``activity.<kind>``.
ACTIVITY_KINDS: tuple[str, ...] = (
    "repo.added", "repo.removed", "repo.rebound", "repo.rescanned", "repo.updated",
    "action.created", "action.updated", "action.submitted", "action.delivery", "action.resolved",
    "action.cancelled", "action.retried", "action.failed",
    "breaker.opened", "breaker.reset", "lease.acquired", "lease.released", "lease.reclaimed",
    "engine.run", "install.transaction",
    # `advisor.auto_requested` is a draft started on the owner's standing grant (FR-ADV-010): its own kind,
    # because Activity is where a user checks why a subagent ran, and one sentence would hide it behind a click.
    "advisor.requested", "advisor.auto_requested", "advisor.completed", "advisor.failed",
    "notification.sent", "slack.sent", "slack.callback", "session.bound", "session.unbound", "session.takeover",
    "intent.created", "intent.paused", "intent.resumed", "intent.archived", "intent.restored", "intent.force_stop",
    "settings.updated", "health.startup",
    # What KiroCrew itself reported in a bound conversation (an `error` row it is not retrying), at that row's time.
    "conversation.error",
)
_KIND_SET = frozenset(ACTIVITY_KINDS)

#: The only kind whose verbatim human text is retained (§1.17).
KIND_ACTION_SUBMITTED = "action.submitted"

#: Audit events real installs write that no ``constants`` classification lists (FORMAT-NOTES §6). They are
#: readable history and deliberately NOT ``MOVEMENT_EVENTS``: reading a merged unit or an indexed document as
#: workflow movement would let the reconciler resolve a decision the workflow never answered.
#: ``LOOP_RUN_STARTED`` is absent on purpose — it belongs to no engine taxonomy.
_EXTRA_AUDIT_EVENTS: tuple[str, ...] = (
    "WORKFLOW_STARTED", "WORKSPACE_INITIALISED", "WORKSPACE_SCAFFOLDED", "WORKSPACE_SCANNED", "WORKTREE_CREATED",
    "RULE_LEARNED", "MEMORY_EMPTY", "PHASE_SKIPPED",
    # AI-DLC 2.7.1: unit/claim and document-index rows.
    "UNIT_MERGED", "UNIT_OWNERSHIP_SET", "UNIT_GATE_RHYTHM_SET", "SWARM_SOURCE_MERGED", "PIPELINE_LINK_COMPLETED",
    "DOCUMENT_INDEXED", "DOCUMENT_UPDATED", "DOCUMENT_REMOVED",
    # AI-DLC 2.9/2.10: ceremony and guard settings, construction policy, checkpoint verification, commit
    # provenance and archiving (the two gate-resolving ones are also in ``RESOLUTION_EVENTS``).
    "CEREMONY_SET", "CHANGE_ACCEPTED", "CHANGE_CONTROL_SET", "CHECKPOINT_VERIFICATION_RECORDED",
    "CONSTRUCTION_POLICY_RECORDED", "GUARD_DISABLED", "GUARD_POLICY_SET", "GUARD_RESTORED", "GUARD_STOOD_ASIDE",
    "PLAN_APPROVAL_OVERRIDDEN", "SOURCE_COMMITTED", "VERIFICATION_COMMAND_RECORDED", "WORKFLOW_ARCHIVED",
    "WORKFLOW_UNARCHIVED",
)

#: Every event with an ``audit.<EVENT>`` key, derived from the engine taxonomy so the catalog cannot lag it.
AUDIT_EVENTS: tuple[str, ...] = tuple(
    sorted({*C.MOVEMENT_EVENTS, *C.NOISE_EVENTS, *C.REVIEW_EVENTS, C.ERROR_EVENT, C.HUMAN_PRESENCE_EVENT,
            *_EXTRA_AUDIT_EVENTS})
)
_AUDIT_EVENT_SET = frozenset(AUDIT_EVENTS)
UNKNOWN_AUDIT_MESSAGE_KEY = "audit.unknown_event"

_ATTENTION_AUDIT_EVENTS = frozenset({C.ERROR_EVENT, "SENSOR_FAILED"})

#: Per-value cap for an audit field in ``params``: a ``Feedback`` value has no bound, and the full block is in
#: ``raw`` for the Evidence drawer.
AUDIT_PARAM_MAX_CHARS = 1000
TIMELINE_DEFAULT_LIMIT = 200
_PARAM_KEY_RE = re.compile(r"[^a-z0-9]+")


#: A merged timeline's cursor prefix; a Studio-rows cursor is an encoded row id and never starts with it.
TIMELINE_CURSOR_PREFIX = "t:"


def _epoch(value: object) -> float | None:
    """An ISO instant in POSIX seconds, whatever its precision (the browser sends milliseconds); else ``None``."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw[-1:] in ("z", "Z") else raw)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


def encode_timeline_cursor(key: tuple[str, int, int, int]) -> str:
    """The newest-first key of the last entry a merged page showed: its time, then audit (1) before Studio (0)
    inside one second, then the audit shard and block, or the Studio row id."""
    return TIMELINE_CURSOR_PREFIX + "|".join(str(part) for part in key)


def decode_timeline_cursor(raw: str) -> tuple[str, int, int, int]:
    parts = raw[len(TIMELINE_CURSOR_PREFIX):].split("|") if raw.startswith(TIMELINE_CURSOR_PREFIX) else []
    try:
        at, side, first, second = parts
        key = (at, int(side), int(first), int(second))
    except ValueError:
        raise StudioError("bad_param", "malformed timeline cursor", details={"param": "cursor"}) from None
    if _epoch(at) is None:
        raise StudioError("bad_param", "malformed timeline cursor", details={"param": "cursor"})
    return key


def severity_of_audit_event(event: "AuditEvent") -> str:
    """Only an error row and a failed sensor read as "needs looking at" (§1.17): gate/question/stage rows are
    the normal shape of progress — the *card* asks for the decision, not the audit row."""
    return "attention" if getattr(event, "event", "") in _ATTENTION_AUDIT_EVENTS else "info"


def audit_message_key(event: str) -> str:
    """``audit.<EVENT>`` for a known event name, else the unknown-event fallback."""
    return f"audit.{event}" if event in _AUDIT_EVENT_SET else UNKNOWN_AUDIT_MESSAGE_KEY


def audit_params(event: "AuditEvent") -> dict[str, Any]:
    """The row's fields as i18n params: snake_cased (``Candidate-ID`` → ``candidate_id``, because the i18n layer
    interpolates ``[A-Za-z0-9_]`` only), redacted and capped. ``event`` goes last so no field can shadow it."""
    params: dict[str, Any] = {}
    for name, value in (getattr(event, "fields", None) or {}).items():
        text = str(value)
        if len(text) > AUDIT_PARAM_MAX_CHARS:
            text = text[:AUDIT_PARAM_MAX_CHARS] + "…"
        params[_PARAM_KEY_RE.sub("_", str(name).strip().lower()).strip("_") or "field"] = security.redact(text)
    params["event"] = str(getattr(event, "event", ""))
    return params


def studio_refs(row: ActivityRow) -> dict[str, Any]:
    """The ``refs`` block for a Studio row: the ids it names plus the first audit/git pointer it carries.

    Only present keys appear: the Evidence drawer renders a link per key, and a ``null`` would be an empty one.
    """
    refs: dict[str, Any] = {}
    if row.repo_id:
        # Which repository and intent the row is about: the label is filled per page (`handlers.events`), and stays
        # null once the repository is unregistered.
        refs["repo"] = {"repo_id": row.repo_id, "label": None,
                        **({"space": row.space or C.DEFAULT_SPACE, "intent_dir": row.intent_dir} if row.intent_dir else {})}
    if row.action_id:
        refs["action_id"] = row.action_id
    if row.session_key:
        refs["session_key"] = row.session_key
    for ref in row.evidence:
        if ref.kind == "audit" and "audit" not in refs:
            shard, _, pos = ref.ref.partition("#")
            # An EvidenceRef names its source, never its content: the event type is known only from `detail`.
            refs["audit"] = {"shard": shard, "pos": int(pos) if pos.isdigit() else None, "event": ref.detail or ""}
        elif ref.kind == "git" and "git" not in refs:
            refs["git"] = {"sha": ref.ref.split(":", 1)[-1]}
    return refs


@dataclass(frozen=True, slots=True)
class TimelineEntry(WireRecord):
    """One row of the merged timeline. ``id`` is the activity row id, and ``None`` for a projected AI-DLC row:
    an audit row has no Studio identity a client could page by or reference (§2.6)."""

    at: str
    source: str
    kind: str
    message_key: str
    params: dict = field(default_factory=dict)
    severity: str = "info"
    refs: dict = field(default_factory=dict)
    raw: str | None = None
    id: int | None = None

    @classmethod
    def of_row(cls, row: ActivityRow) -> "TimelineEntry":
        """A Studio row in the shape both halves of ``/activity`` share. ``stage`` is a column, lifted into
        ``params`` so a translation that mentions the stage works for either half."""
        params = dict(row.params)
        if row.stage and "stage" not in params:
            params["stage"] = row.stage
        return cls(at=row.at, source=row.source, kind=row.kind, message_key=row.message_key, params=params,
                   severity=row.severity, refs=studio_refs(row), raw=None, id=row.id)


def _normalise_params(value: Any) -> Any:
    """Redact every string leaf and make every leaf JSON-encodable. An odd leaf (a ``Path`` in a failure
    detail) becomes its redacted ``str``: the moments a caller passes one are when the trail matters most."""
    if isinstance(value, str):
        return security.redact(value)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(k): _normalise_params(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_normalise_params(v) for v in value]
    return security.redact(str(value))


def _normalise_evidence(evidence: Any) -> tuple[EvidenceRef, ...]:
    """``EvidenceRef``s or the plain mappings ``EngineRunner`` builds on its worker thread, redacted."""
    if evidence is None:
        return ()
    if isinstance(evidence, (EvidenceRef, Mapping)):
        evidence = (evidence,)
    out: list[EvidenceRef] = []
    for item in evidence:
        if isinstance(item, Mapping):
            kind, ref, detail = item.get("kind", "studio"), item.get("ref", ""), item.get("detail")
        else:
            kind, ref, detail = getattr(item, "kind", "studio"), getattr(item, "ref", ""), getattr(item, "detail", None)
        out.append(EvidenceRef(
            kind=str(kind or "studio"),
            ref=security.redact(str(ref or "")),
            detail=security.redact(str(detail)) if detail is not None else None,
        ))
    return tuple(out)


class ActivityProjector:
    """Records Studio's own activity, projects AI-DLC's live, and never confuses the two."""

    def __init__(self, storage: "Storage", clock: "Clock") -> None:
        self._storage = storage
        self._clock = clock

    def record_sync(
        self,
        *,
        kind: str,
        severity: str = "info",
        source: str = "studio",
        repo_id: str | None = None,
        space: str | None = None,
        intent_dir: str | None = None,
        stage: str | None = None,
        action_id: str | None = None,
        session_key: str | None = None,
        params: Mapping[str, Any] | None = None,
        evidence: Sequence[Any] = (),
        human_text: str | None = None,
        at: str | None = None,
    ) -> int:
        """Append one Studio row; returns its id. Blocking: only for callers already on a worker thread, whose
        rows would otherwise wait in a buffer a crash could lose. ``at`` dates a row read from another record (a
        host transcript row) at that record's own time; a malformed one is ignored for now."""
        if kind not in _KIND_SET:
            raise StudioError(
                "internal_error",
                f"unknown activity kind {kind!r}; add it to activity.ACTIVITY_KINDS and to the i18n catalog",
                details={"kind": kind},
            )
        if severity not in C.SEVERITY:
            raise StudioError("internal_error", f"unknown activity severity {severity!r}",
                              details={"severity": severity})
        if source == "aidlc":
            raise StudioError("internal_error", "AI-DLC activity is projected, never recorded",
                              details={"kind": kind})
        if source not in C.SOURCE:
            raise StudioError("internal_error", f"unknown activity source {source!r}", details={"source": source})
        row = ActivityRow(
            at=at if at and C.epoch_from_iso(at) is not None else self._clock.iso(), source=source, kind=kind, severity=severity, repo_id=repo_id, space=space,
            intent_dir=intent_dir, stage=stage, action_id=action_id, session_key=session_key,
            message_key=f"activity.{kind}", params=_normalise_params(dict(params or {})),
            evidence=_normalise_evidence(evidence), redacted=True,
            # Verbatim on purpose: it is the user's own decision text and must match what was sent. Every read
            # path that leaves the process redacts it again.
            human_text=human_text if kind == KIND_ACTION_SUBMITTED else None,
        )
        return int(self._storage.activity_append(row))

    async def record(self, **fields: Any) -> int:
        """``record_sync`` on a worker thread; loop-safe. A misspelled keyword fails at the caller's await."""
        return await asyncio.to_thread(self.record_sync, **fields)

    async def query(self, q: ActivityQuery) -> tuple[list[ActivityRow], str | None]:
        """One page of Studio rows, newest first, plus the cursor for the next page."""
        return await asyncio.to_thread(self._storage.activity_query, q)

    async def timeline(
        self, snap: "IntentSnapshot", *, limit: int = TIMELINE_DEFAULT_LIMIT, sources: Sequence[str] | None = None,
        q: ActivityQuery | None = None,
    ) -> list[TimelineEntry]:
        """Studio rows for this intent merged with its AI-DLC audit rows, newest first.

        The audit half comes from the snapshot the caller took, not a second read: a timeline assembled from
        another read could show a gate approval whose state file the caller never saw.
        """
        entries, _cursor = await self.timeline_page(snap, limit=limit, sources=sources, q=q)
        return entries

    async def timeline_page(
        self, snap: "IntentSnapshot", *, limit: int = TIMELINE_DEFAULT_LIMIT, sources: Sequence[str] | None = None,
        q: ActivityQuery | None = None,
    ) -> tuple[list[TimelineEntry], str | None]:
        """One page of the merged timeline plus the cursor for the next. ``q``'s stage, kind, severity, since,
        until and cursor are applied to both halves before the page is cut: filtered in the browser, a stage filter
        searched only the newest page and could not reach an older event at all."""
        return await asyncio.to_thread(self._timeline_sync, snap, limit, sources, q)

    async def export(
        self, q: ActivityQuery, *, include_human_text: bool, allowed_roots: Sequence[str]
    ) -> dict[str, Any]:
        """The diagnostics activity bundle: one page, fully redacted and path-scrubbed. ``human_text`` is the
        caller's decision; withheld, it is present and ``null`` so "withheld" reads differently from "none"."""
        return await asyncio.to_thread(self._export_sync, q, include_human_text, allowed_roots)

    # ---- internals (worker thread) ----

    def _timeline_sync(
        self, snap: "IntentSnapshot", limit: int, sources: Sequence[str] | None, q: ActivityQuery | None = None,
    ) -> tuple[list[TimelineEntry], str | None]:
        width = max(1, min(int(limit or TIMELINE_DEFAULT_LIMIT), int(C.ACTIVITY_MAX_LIMIT)))
        wanted = frozenset(sources) if sources else None
        q = q or ActivityQuery()
        after = decode_timeline_cursor(str(q.cursor)) if q.cursor else None
        since, until = _epoch(q.since), _epoch(q.until)
        keyed: list[tuple[tuple[float, int, int, int], tuple[str, int, int, int], TimelineEntry]] = []

        def inside(at: str, key: tuple[str, int, int, int]) -> tuple[float, int, int, int] | None:
            when = _epoch(at)
            if when is None or (since is not None and when < since) or (until is not None and when > until):
                return None
            order = (when, *key[1:])
            if after is not None and order >= (_epoch(after[0]) or 0.0, *after[1:]):
                return None
            return order  # type: ignore[return-value]

        # The action-type filter is a join against Studio's actions, which AI-DLC's own events have no part in.
        if (wanted is None or "aidlc" in wanted) and q.action_type is None:
            candidates = []
            for event in getattr(getattr(snap, "audit", None), "events", None) or ():
                name = str(getattr(event, "event", ""))
                if q.kind is not None and name != q.kind:
                    continue
                if q.severity is not None and severity_of_audit_event(event) != q.severity:
                    continue
                if q.stage is not None and str((getattr(event, "fields", None) or {}).get("Stage") or "") != q.stage:
                    continue
                _ts, shard, pos = getattr(event, "sort_key", (event.timestamp, 0, 0))
                key = (str(event.timestamp), 1, int(shard), int(pos))
                order = inside(key[0], key)
                if order is not None:
                    candidates.append((order, key, event))
            # nlargest, not a full sort: a long history holds thousands of rows and one page survives the merge.
            for order, key, event in heapq.nlargest(width + 1, candidates, key=lambda row: row[0]):
                keyed.append((order, key, self._audit_entry(event)))
        if wanted is None or wanted - {"aidlc"}:
            # Rows tied at the cursor's second are read again and dropped by key, so a page edge never skips one.
            rows, _cursor = self._storage.activity_query(dataclasses.replace(
                q, repo_id=getattr(snap, "repo_id", None), space=getattr(snap, "space", None),
                intent_dir=getattr(snap, "intent_dir", None), source=None, cursor=None,
                until=after[0] if after is not None and (until is None or (_epoch(after[0]) or 0) < until)
                else q.until,
                limit=min(2 * width + 1, int(C.ACTIVITY_MAX_LIMIT)),
            ))
            for row in rows:
                if wanted is not None and row.source not in wanted:
                    continue
                key = (str(row.at), 0, int(row.id or 0), 0)
                order = inside(key[0], key)
                if order is not None:
                    keyed.append((order, key, TimelineEntry.of_row(row)))
        keyed.sort(key=lambda row: row[0], reverse=True)
        page = keyed[:width]
        cursor = encode_timeline_cursor(page[-1][1]) if len(keyed) > width and page else None
        return [entry for _order, _key, entry in page], cursor

    @staticmethod
    def _audit_entry(event: "AuditEvent") -> TimelineEntry:
        name = str(getattr(event, "event", ""))
        return TimelineEntry(
            at=str(getattr(event, "timestamp", "")),
            source="aidlc",
            kind=name,  # verbatim and never translated, so `message_key == f"audit.{kind}"` for known events
            message_key=audit_message_key(name),
            params=audit_params(event),
            severity=severity_of_audit_event(event),
            refs={"audit": {"shard": str(getattr(event, "shard", "")), "pos": int(getattr(event, "pos", 0) or 0),
                            "event": name}},
            # Redacted although the drawer calls it verbatim: a credential pasted into a gate response reaches
            # this block, and a diagnostics bundle is forever (architecture §10).
            raw=security.redact(str(getattr(event, "raw", "") or "")) or None,
            id=None,
        )

    def _export_sync(self, q: ActivityQuery, include_human_text: bool, allowed_roots: Sequence[str]) -> dict[str, Any]:
        roots = tuple(str(security.realpath(root)) for root in allowed_roots if root)
        rows, _cursor = self._storage.activity_query(q)
        exported: list[dict[str, Any]] = []
        repo_ids: list[str] = []
        for row in rows:
            data = row.to_json()
            if not include_human_text:
                data["human_text"] = None
            exported.append(security.redact_json(data, roots))
            if row.repo_id and row.repo_id not in repo_ids:
                repo_ids.append(row.repo_id)
        # A legend rather than the repos table: an id alone is unreadable, and the full record would carry paths
        # and install state that belong to `/diagnostics`' own repo section.
        repos = sorted(
            ({"repo_id": str(r.get("id") or ""), "label": security.redact_json(str(r.get("label") or ""), roots)}
             for r in (self._storage.select("repos", {"id": repo_ids}) if repo_ids else ())),
            key=lambda entry: entry["repo_id"],
        )
        return {
            "generated_at": self._clock.iso(),
            "app_version": C.APP_VERSION,
            "redacted": True,
            "human_text_included": bool(include_human_text),
            "rows": exported,
            "repos": repos,
        }
