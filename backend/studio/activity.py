"""One timeline from two authorities, with the boundary between them never blurred.

Studio records what **Studio** did: a repository was registered, a decision was submitted, a lease was
reclaimed. AI-DLC records what the **workflow** did, in its own append-only audit shards. The user
wants to read both as one story, so this module merges them — and it is the merge that makes the rules
below load-bearing rather than stylistic.

Four decisions, each for the failure it prevents:

1. **AI-DLC rows are projected, never stored.** ``timeline`` reads ``snap.audit.events`` live and turns
   them into ``TimelineEntry`` objects on the way out; nothing with ``source="aidlc"`` is ever written
   to the ``activity`` table (``record`` refuses that source outright). Copying them in would create a
   second, silently diverging copy of a file AI-DLC owns and rewrites: after a shard rotation, a
   ``REDO`` jump or a scope change, Studio's copy would keep asserting history that is no longer on
   disk — and Studio would have become an authority for a lifecycle it only observes (architecture §3).
2. **The source is part of the row, not a rendering detail.** Every entry carries `source` and, for
   AI-DLC rows, the verbatim block in ``raw``. When a user asks "who says the gate was approved?", the
   answer must be attributable to the engine's own bytes rather than to a Studio paraphrase.
3. **Keys and params, never prose.** A stored English sentence is untranslatable forever (C11), so a
   Studio row is ``activity.<kind>`` plus params and an audit row is ``audit.<EVENT>`` plus its fields,
   with ``audit.unknown_event`` for an event name outside the taxonomy — real installs do produce them
   (``LOOP_RUN_STARTED`` exists on a harvested machine and is in no engine release; FORMAT-NOTES §6),
   and dropping or mislabelling such a row would lose real history.
4. **Redaction happens at the boundary that leaks.** ``params`` are redacted at write (they quote engine
   output and audit fields), ``human_text`` is stored verbatim — it is the user's own decision text and
   the retention purge, not this module, is what removes it — and the diagnostics export redacts
   *everything* again and replaces absolute paths outside the registered roots with ``<path>``. An
   export is the one artifact that leaves the machine, and a real audit field (``RULE_LEARNED``'s
   ``Destination``) carries an absolute path, so the scrub cannot be optional there.

Threading: async facade (§0.6). Every method that touches SQLite or walks a bundle does its work in
``asyncio.to_thread`` so neither a 500-row page nor a redaction pass can stall the gateway loop.
``record_sync`` is the one synchronous entry point, provided for callers that are *already* on a worker
thread (``EngineRunner.run_sync`` probes for it): it must never be called from the event loop.
"""

from __future__ import annotations

import asyncio
import heapq
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from . import constants as C
from . import security
from .errors import StudioError

# Re-exported so §1.17's names resolve here even though ``Storage`` must construct them (it decodes
# rows it reads back and may not import the layer above it) — one definition, two module paths.
from .storage import ActivityQuery, ActivityRow, EvidenceRef

if TYPE_CHECKING:  # pragma: no cover - typing only
    # Annotations only. `projection` and `consistency` are duck-typed below, so a snapshot or finding
    # assembled by hand in a test is indistinguishable from a real one, and this module stays importable
    # before those modules exist.
    from .aidlc_reader import AuditEvent
    from .consistency import Finding
    from .constants import Clock
    from .projection import IntentSnapshot
    from .storage import Storage

__all__ = [
    "ACTIVITY_KINDS",
    "AUDIT_EVENTS",
    "ActivityProjector",
    "ActivityQuery",
    "ActivityRow",
    "EvidenceRef",
    "TimelineEntry",
    "audit_message_key",
    "severity_of_audit_event",
    "severity_of_finding",
    "studio_refs",
]


# --------------------------------------------------------------------------- #
# the two key vocabularies (§1.17) — data, so the i18n catalog and the tests read one source
# --------------------------------------------------------------------------- #

#: Every Studio-originated activity kind, verbatim from §1.17 and in its order. The i18n key of a kind
#: is always ``activity.<kind>``. Kept here rather than in ``constants.py`` for the same reason
#: ``FINDING_CODES`` lives in ``consistency.py``: this is the key set *this* module mints, and the
#: catalog generator reads it from the owner.
ACTIVITY_KINDS: tuple[str, ...] = (
    "repo.added",
    "repo.removed",
    "repo.rebound",
    "repo.rescanned",
    "repo.updated",
    "action.created",
    "action.updated",
    "action.submitted",
    "action.delivery",
    "action.resolved",
    "action.cancelled",
    "action.retried",
    "action.failed",
    "breaker.opened",
    "breaker.reset",
    "lease.acquired",
    "lease.released",
    "lease.reclaimed",
    "engine.run",
    "install.transaction",
    "migration.previewed",
    "migration.applied",
    "advisor.requested",
    # A draft Studio asked for on the owner's standing grant, not one a human clicked for
    # (FR-ADV-010). A separate kind rather than a parameter on ``advisor.requested`` because the
    # Activity row is where a user checks *why* a subagent ran, and one sentence that covers both
    # cases would make an automatic spawn indistinguishable from their own click.
    "advisor.auto_requested",
    "advisor.completed",
    "advisor.failed",
    "notification.sent",
    "slack.sent",
    "slack.callback",
    "session.bound",
    "session.unbound",
    "session.takeover",
    "intent.created",
    "intent.paused",
    "intent.resumed",
    "intent.archived",
    "intent.restored",
    "intent.force_stop",
    "machine_lane.refused",
    "settings.updated",
    "health.startup",
    "calibration.cleared",
)
_KIND_SET = frozenset(ACTIVITY_KINDS)

#: The only kind whose verbatim human text is retained (§1.17); every other kind drops it on the way in.
KIND_ACTION_SUBMITTED = "action.submitted"

#: Audit event names that real installs write but no ``constants.py`` classification tuple lists
#: (FORMAT-NOTES §6 enumerates the 38 seen on disk). They are *known* — they get their own
#: ``audit.<EVENT>`` key — because a workspace scaffold or a learned memory rule is history a user
#: should be able to read. ``LOOP_RUN_STARTED`` is deliberately absent: FORMAT-NOTES records it as
#: belonging to no engine taxonomy, so it is exactly the case ``audit.unknown_event`` exists for.
_EXTRA_AUDIT_EVENTS: tuple[str, ...] = (
    "WORKFLOW_STARTED",
    "WORKSPACE_INITIALISED",
    "WORKSPACE_SCAFFOLDED",
    "WORKSPACE_SCANNED",
    "WORKTREE_CREATED",
    "RULE_LEARNED",
    "MEMORY_EMPTY",
    "PHASE_SKIPPED",
    # AI-DLC 2.7.1's unit/claim and document-index rows. They are known here and NOT in
    # ``MOVEMENT_EVENTS``: a merged unit or an indexed document is something that happened, but reading
    # it as workflow movement would let the reconciler resolve a decision the workflow never answered.
    # Being readable history and being proof of progress are different jobs, and this is the first one.
    "UNIT_MERGED",
    "UNIT_OWNERSHIP_SET",
    "UNIT_GATE_RHYTHM_SET",
    "SWARM_SOURCE_MERGED",
    "PIPELINE_LINK_COMPLETED",
    "DOCUMENT_INDEXED",
    "DOCUMENT_UPDATED",
    "DOCUMENT_REMOVED",
)

#: Every audit event Studio ships an ``audit.<EVENT>`` key for. Derived from the engine taxonomy in
#: ``constants.py`` so a new classification there cannot leave the catalog behind.
AUDIT_EVENTS: tuple[str, ...] = tuple(
    sorted(
        {
            *C.MOVEMENT_EVENTS,
            *C.NOISE_EVENTS,
            *C.REVIEW_EVENTS,
            C.ERROR_EVENT,
            C.HUMAN_PRESENCE_EVENT,
            *_EXTRA_AUDIT_EVENTS,
        }
    )
)
_AUDIT_EVENT_SET = frozenset(AUDIT_EVENTS)

#: Fallback key for an event name outside the taxonomy; renders the raw name from ``params.event``.
UNKNOWN_AUDIT_MESSAGE_KEY = "audit.unknown_event"

#: ``FINDING_SEVERITY`` → ``SEVERITY`` (review P26.5). One vocabulary reaches the timeline, so a
#: finding's ``warn`` has to become the ``attention`` band the queue and the timeline already sort by.
_FINDING_SEVERITY_MAP = {"blocking": "blocking", "warn": "attention", "info": "info"}

#: Audit events that read as "something needs looking at" rather than "the workflow moved". Only these
#: two: an error row and a failed sensor. Gate/question/stage rows are ``info`` because they are the
#: normal shape of progress — the *card* is what asks for a decision, not the audit row.
_ATTENTION_AUDIT_EVENTS = frozenset({C.ERROR_EVENT, "SENSOR_FAILED"})

#: Per-value cap for an audit field projected into ``params``. A real ``User Input`` value runs to 300
#: characters and a ``Feedback`` value has no bound at all; a 200-row timeline must stay a timeline, and
#: the full block is right there in ``raw`` for the Evidence drawer.
AUDIT_PARAM_MAX_CHARS = 1000

#: Default width of one timeline page (§1.17) and the ceiling the storage layer would clamp to anyway.
TIMELINE_DEFAULT_LIMIT = 200

_PARAM_KEY_RE = re.compile(r"[^a-z0-9]+")


# --------------------------------------------------------------------------- #
# severity and key mapping (pure)
# --------------------------------------------------------------------------- #


def severity_of_finding(finding: "Finding | Mapping[str, Any]") -> str:
    """Map a consistency finding's severity onto the single timeline/Activity vocabulary."""
    raw = finding.get("severity") if isinstance(finding, Mapping) else getattr(finding, "severity", "")
    return _FINDING_SEVERITY_MAP.get(str(raw or ""), "info")


def severity_of_audit_event(event: "AuditEvent | Mapping[str, Any] | str") -> str:
    """Severity band for one AI-DLC audit row (§1.17)."""
    if isinstance(event, str):
        name = event
    elif isinstance(event, Mapping):
        name = str(event.get("event", ""))
    else:
        name = str(getattr(event, "event", ""))
    return "attention" if name in _ATTENTION_AUDIT_EVENTS else "info"


def audit_message_key(event: str) -> str:
    """``audit.<EVENT>`` for a known event name, else the unknown-event fallback."""
    return f"audit.{event}" if event in _AUDIT_EVENT_SET else UNKNOWN_AUDIT_MESSAGE_KEY


def _param_key(raw: str) -> str:
    """Audit field name → snake_case params key (``Candidate-ID`` → ``candidate_id``).

    Audit field names are free-form and version-specific (``Stage slug``, ``Questions SHA-256``,
    ``Candidate-ID``), while the i18n layer interpolates ``{name}`` with ``[A-Za-z0-9_]`` only. Without
    this normalisation half the real field names would be unreachable from a translation string.
    """
    key = _PARAM_KEY_RE.sub("_", raw.strip().lower()).strip("_")
    return key or "field"


def audit_params(event: "AuditEvent") -> dict[str, Any]:
    """The audit row's fields as i18n params, snake_cased, redacted and length-capped.

    ``event`` is written last so a hypothetical field of the same name cannot shadow the real event
    type, which ``audit.unknown_event`` needs in order to name what it is showing.
    """
    params: dict[str, Any] = {}
    for name, value in (getattr(event, "fields", None) or {}).items():
        text = str(value)
        if len(text) > AUDIT_PARAM_MAX_CHARS:
            text = text[:AUDIT_PARAM_MAX_CHARS] + "…"
        params[_param_key(str(name))] = security.redact(text)
    params["event"] = str(getattr(event, "event", ""))
    return params


# --------------------------------------------------------------------------- #
# public dataclass
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """One row of the merged intent timeline, from either authority.

    ``id`` is the ``activity`` row id for a Studio entry and ``None`` for a projected AI-DLC one — an
    audit row has no Studio identity, and pretending otherwise would let a client try to page or
    reference it. It is not in §1.17's field list but §2.6's JSON requires it, so it is carried here
    (the route cannot invent it).

    ``refs`` holds only the keys that apply (``action_id``, ``session_key``, ``audit``, ``git``): the
    Evidence drawer renders a link per present key, and a ``null`` would render an empty one.
    """

    at: str
    source: str
    kind: str
    message_key: str
    params: dict = field(default_factory=dict)
    severity: str = "info"
    refs: dict = field(default_factory=dict)
    raw: str | None = None
    id: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "source": self.source,
            "kind": self.kind,
            "message_key": self.message_key,
            "params": dict(self.params),
            "severity": self.severity,
            "refs": dict(self.refs),
            "raw": self.raw,
            "id": self.id,
        }


# --------------------------------------------------------------------------- #
# normalisation helpers (write path)
# --------------------------------------------------------------------------- #


def _normalise_params(value: Any) -> Any:
    """Redact every string leaf and make every leaf JSON-encodable.

    The coercion of an unexpected leaf type to its redacted ``str`` is deliberate: a row that cannot be
    serialised would raise inside the write, and the moments when a caller passes something odd (a
    ``Path`` in a failure detail) are exactly the moments the activity trail matters most.
    """
    if isinstance(value, str):
        return security.redact(value)
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _normalise_params(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_normalise_params(v) for v in value]
    return security.redact(str(value))


def _normalise_evidence(evidence: Any) -> tuple[EvidenceRef, ...]:
    """Accept ``EvidenceRef`` objects or the plain mappings the sync callers build, redact both.

    ``EngineRunner`` assembles its rows as dicts on a worker thread (it must not import this module to
    construct a dataclass), so both shapes are first-class here.
    """
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
        out.append(
            EvidenceRef(
                kind=str(kind or "studio"),
                ref=security.redact(str(ref or "")),
                detail=security.redact(str(detail)) if detail is not None else None,
            )
        )
    return tuple(out)


def _scrub(value: Any, roots: Sequence[str]) -> Any:
    """Redact credentials and replace absolute paths outside ``roots`` with ``<path>``, recursively."""
    if isinstance(value, str):
        return security.scrub_repo_paths(security.redact(value), roots)
    if isinstance(value, Mapping):
        return {str(k): _scrub(v, roots) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v, roots) for v in value]
    return value


def studio_refs(row: ActivityRow) -> dict[str, Any]:
    """The ``refs`` block for a Studio row: ids it names plus the first audit/git pointer it carries."""
    refs: dict[str, Any] = {}
    if row.action_id:
        refs["action_id"] = row.action_id
    if row.session_key:
        refs["session_key"] = row.session_key
    for ref in row.evidence:
        if ref.kind == "audit" and "audit" not in refs:
            shard, _, pos = ref.ref.partition("#")
            refs["audit"] = {
                "shard": shard,
                "pos": int(pos) if pos.isdigit() else None,
                # An EvidenceRef names its source, never its content, so the event type is only known
                # when the writer put it in `detail`.
                "event": ref.detail or "",
            }
        elif ref.kind == "git" and "git" not in refs:
            refs["git"] = {"sha": ref.ref.split(":", 1)[-1]}
    return refs


# --------------------------------------------------------------------------- #
# the projector
# --------------------------------------------------------------------------- #


class ActivityProjector:
    """Records Studio's own activity, projects AI-DLC's live, and never confuses the two."""

    def __init__(self, storage: "Storage", clock: "Clock") -> None:
        self._storage = storage
        self._clock = clock

    # ---- write ----

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
        """Blocking body of ``record``; call only from a worker thread.

        Exists as a separate entry point because two callers are already off the loop when they have a
        row to write (``EngineRunner.run_sync`` and the installer's file steps). Without it their rows
        would have to wait in a buffer for someone to drain it, and a crash in between would lose
        precisely the trace a failed engine invocation needs.
        """
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
            # §1.17 invariant: AI-DLC rows are projected live from its own files. A stored row claiming
            # to be AI-DLC's would be a second authority for a lifecycle Studio only observes.
            raise StudioError("internal_error", "AI-DLC activity is projected, never recorded",
                              details={"kind": kind})
        if source not in C.SOURCE:
            raise StudioError("internal_error", f"unknown activity source {source!r}",
                              details={"source": source})

        row = ActivityRow(
            at=self._clock.iso(),
            source=source,
            kind=kind,
            severity=severity,
            repo_id=repo_id,
            space=space,
            intent_dir=intent_dir,
            stage=stage,
            action_id=action_id,
            session_key=session_key,
            message_key=f"activity.{kind}",
            params=_normalise_params(dict(params or {})),
            evidence=_normalise_evidence(evidence),
            redacted=True,
            # Retained for exactly one kind, and stored verbatim on purpose: it is the user's own
            # decision text, it must match what was sent, and retention (not redaction) is what removes
            # it. Every read path that leaves the process redacts it again.
            human_text=human_text if kind == KIND_ACTION_SUBMITTED else None,
        )
        return int(self._storage.activity_append(row))

    async def record(
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
        """Append one Studio row; returns its id. Loop-safe (the write runs on a worker thread).

        The signature is spelled out rather than forwarded as ``**kwargs`` so a misspelled keyword
        fails at the caller's ``await`` instead of inside the worker thread.
        """
        return await asyncio.to_thread(
            self.record_sync,
            kind=kind,
            severity=severity,
            source=source,
            repo_id=repo_id,
            space=space,
            intent_dir=intent_dir,
            stage=stage,
            action_id=action_id,
            session_key=session_key,
            params=params,
            evidence=evidence,
            human_text=human_text,
        )

    # ---- read ----

    async def query(self, q: ActivityQuery) -> tuple[list[ActivityRow], str | None]:
        """One page of Studio rows, newest first, plus the cursor for the next page."""
        return await asyncio.to_thread(self._storage.activity_query, q)

    async def timeline(
        self,
        snap: "IntentSnapshot",
        *,
        limit: int = TIMELINE_DEFAULT_LIMIT,
        sources: Sequence[str] | None = None,
    ) -> list[TimelineEntry]:
        """Studio rows for this intent merged with its AI-DLC audit rows, newest first.

        The audit half is read from the snapshot the caller already took, not from disk again: a
        timeline assembled from a second read could show a gate approval whose state file the caller
        never saw, which is the incoherence ``IntentSnapshot`` exists to prevent.
        """
        return await asyncio.to_thread(self._timeline_sync, snap, limit, sources)

    async def export(
        self,
        q: ActivityQuery,
        *,
        include_human_text: bool,
        allowed_roots: Sequence[str],
    ) -> dict[str, Any]:
        """The diagnostics activity bundle: one page of rows, fully redacted and path-scrubbed.

        ``include_human_text`` is the caller's decision (the route requires both the setting and an
        explicit query flag); when it is false the field is present and ``null`` rather than missing, so
        a reader can tell "withheld" from "there was none".
        """
        return await asyncio.to_thread(self._export_sync, q, include_human_text, allowed_roots)

    # ---- internals (worker thread) ----

    def _timeline_sync(
        self,
        snap: "IntentSnapshot",
        limit: int,
        sources: Sequence[str] | None,
    ) -> list[TimelineEntry]:
        width = max(1, min(int(limit or TIMELINE_DEFAULT_LIMIT), int(C.ACTIVITY_MAX_LIMIT)))
        wanted = frozenset(sources) if sources else None

        entries: list[TimelineEntry] = []
        if wanted is None or "aidlc" in wanted:
            entries.extend(self._project_audit(snap, width))
        if wanted is None or wanted - {"aidlc"}:
            entries.extend(self._studio_entries(snap, width, wanted))

        # Stable sort on `at` alone: each half arrives already in its own correct newest-first order
        # (audit by (timestamp, shard_index, pos), Studio by descending id), and second-precision
        # timestamps tie constantly. Stability keeps those orders inside a tie group instead of
        # shuffling two rows of the same second on every request.
        entries.sort(key=lambda e: e.at, reverse=True)
        return entries[:width]

    def _project_audit(self, snap: "IntentSnapshot", width: int) -> list[TimelineEntry]:
        bundle = getattr(snap, "audit", None)
        events = list(getattr(bundle, "events", None) or ())
        if not events:
            return []
        # nlargest rather than sorting the whole bundle: a tail-read shard can hold thousands of rows,
        # and only the newest `width` of them can survive the merge anyway. It also makes the projection
        # correct for a bundle whose order a caller assembled by hand.
        newest = heapq.nlargest(width, events, key=lambda e: getattr(e, "sort_key", (e.timestamp, 0, 0)))
        return [self._audit_entry(event) for event in newest]

    @staticmethod
    def _audit_entry(event: "AuditEvent") -> TimelineEntry:
        name = str(getattr(event, "event", ""))
        return TimelineEntry(
            at=str(getattr(event, "timestamp", "")),
            source="aidlc",
            # The event name verbatim: it is AI-DLC-authored, never translated, and it keeps
            # `message_key == f"audit.{kind}"` true for every known event.
            kind=name,
            message_key=audit_message_key(name),
            params=audit_params(event),
            severity=severity_of_audit_event(event),
            refs={
                "audit": {
                    "shard": str(getattr(event, "shard", "")),
                    "pos": int(getattr(event, "pos", 0) or 0),
                    "event": name,
                }
            },
            # Redacted even though the drawer calls it verbatim: a credential pasted into a gate
            # response reaches this block, and a diagnostics bundle is forever (architecture §10).
            raw=security.redact(str(getattr(event, "raw", "") or "")) or None,
            id=None,
        )

    def _studio_entries(
        self,
        snap: "IntentSnapshot",
        width: int,
        wanted: frozenset[str] | None,
    ) -> list[TimelineEntry]:
        rows, _cursor = self._storage.activity_query(
            ActivityQuery(
                repo_id=getattr(snap, "repo_id", None),
                space=getattr(snap, "space", None),
                intent_dir=getattr(snap, "intent_dir", None),
                limit=width,
            )
        )
        out: list[TimelineEntry] = []
        for row in rows:
            if wanted is not None and row.source not in wanted:
                continue
            params = dict(row.params)
            if row.stage and "stage" not in params:
                # The stage is a column, not a param; surfacing it here makes a stage filter (and a
                # translation that mentions the stage) work identically for both halves of the merge.
                params["stage"] = row.stage
            out.append(
                TimelineEntry(
                    at=row.at,
                    source=row.source,
                    kind=row.kind,
                    message_key=row.message_key,
                    params=params,
                    severity=row.severity,
                    refs=studio_refs(row),
                    raw=None,
                    id=row.id,
                )
            )
        return out

    def _export_sync(
        self,
        q: ActivityQuery,
        include_human_text: bool,
        allowed_roots: Sequence[str],
    ) -> dict[str, Any]:
        roots = tuple(str(security.realpath(root)) for root in allowed_roots if root)
        rows, _cursor = self._storage.activity_query(q)

        exported: list[dict[str, Any]] = []
        repo_ids: list[str] = []
        for row in rows:
            data = row.to_json()
            if not include_human_text:
                data["human_text"] = None
            exported.append(_scrub(data, roots))
            if row.repo_id and row.repo_id not in repo_ids:
                repo_ids.append(row.repo_id)

        # A legend rather than the repos table: an id in a bundle is unreadable, and the full record
        # would carry paths and install state that belong to `/diagnostics`' own repo section.
        repos: list[dict[str, Any]] = []
        if repo_ids:
            for repo_row in self._storage.select("repos", {"id": repo_ids}):
                repos.append(
                    {
                        "repo_id": str(repo_row.get("id") or ""),
                        "label": _scrub(str(repo_row.get("label") or ""), roots),
                    }
                )
            repos.sort(key=lambda entry: entry["repo_id"])

        return {
            "generated_at": self._clock.iso(),
            "app_version": C.APP_VERSION,
            "redacted": True,
            "human_text_included": bool(include_human_text),
            "rows": exported,
            "repos": repos,
        }
