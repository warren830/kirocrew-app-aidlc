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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from . import constants as C
from . import security
from .errors import StudioError

# Re-exported so §1.17's names resolve here; ``Storage`` constructs them and may not import this layer.
from .storage import ActivityQuery, ActivityRow, EvidenceRef

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
    "machine_lane.refused", "settings.updated", "health.startup",
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
class TimelineEntry:
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

    def to_json(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


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
    ) -> int:
        """Append one Studio row; returns its id. Blocking: only for callers already on a worker thread, whose
        rows would otherwise wait in a buffer a crash could lose."""
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
            at=self._clock.iso(), source=source, kind=kind, severity=severity, repo_id=repo_id, space=space,
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
        self, snap: "IntentSnapshot", *, limit: int = TIMELINE_DEFAULT_LIMIT, sources: Sequence[str] | None = None
    ) -> list[TimelineEntry]:
        """Studio rows for this intent merged with its AI-DLC audit rows, newest first.

        The audit half comes from the snapshot the caller took, not a second read: a timeline assembled from
        another read could show a gate approval whose state file the caller never saw.
        """
        return await asyncio.to_thread(self._timeline_sync, snap, limit, sources)

    async def export(
        self, q: ActivityQuery, *, include_human_text: bool, allowed_roots: Sequence[str]
    ) -> dict[str, Any]:
        """The diagnostics activity bundle: one page, fully redacted and path-scrubbed. ``human_text`` is the
        caller's decision; withheld, it is present and ``null`` so "withheld" reads differently from "none"."""
        return await asyncio.to_thread(self._export_sync, q, include_human_text, allowed_roots)

    # ---- internals (worker thread) ----

    def _timeline_sync(
        self, snap: "IntentSnapshot", limit: int, sources: Sequence[str] | None
    ) -> list[TimelineEntry]:
        width = max(1, min(int(limit or TIMELINE_DEFAULT_LIMIT), int(C.ACTIVITY_MAX_LIMIT)))
        wanted = frozenset(sources) if sources else None
        entries: list[TimelineEntry] = []
        if wanted is None or "aidlc" in wanted:
            events = list(getattr(getattr(snap, "audit", None), "events", None) or ())
            # nlargest, not a full sort: a tail-read shard can hold thousands of rows and only the newest
            # `width` survive the merge — and it is correct for a bundle whose order was assembled by hand.
            newest = heapq.nlargest(width, events, key=lambda e: getattr(e, "sort_key", (e.timestamp, 0, 0)))
            entries.extend(self._audit_entry(event) for event in newest)
        if wanted is None or wanted - {"aidlc"}:
            rows, _cursor = self._storage.activity_query(ActivityQuery(
                repo_id=getattr(snap, "repo_id", None), space=getattr(snap, "space", None),
                intent_dir=getattr(snap, "intent_dir", None), limit=width,
            ))
            entries.extend(TimelineEntry.of_row(row) for row in rows if wanted is None or row.source in wanted)
        # Stable sort on `at` alone: each half is already in its own newest-first order and second-precision
        # timestamps tie constantly, so stability keeps those orders inside a tie group.
        entries.sort(key=lambda e: e.at, reverse=True)
        return entries[:width]

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
