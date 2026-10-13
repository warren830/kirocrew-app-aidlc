"""Contradiction reporting over one coherent read of an AI-DLC intent.

The one place in Studio allowed to notice that two sources disagree, and forbidden to decide which is
right: a finding carries **both** readings and, when the disagreement could change which intent, stage,
gate or artifact a decision applies to, blocks it (``409 state_inconsistent``, PRD §12.3 / FR-ACT-009).
Pure: no file opened, nothing written, no clock read. Severity rules: FORMAT-NOTES §12.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

from . import constants as C
from .aidlc_reader import (
    ACTIVE_INTENT_FILENAME, DIRECTIVE_FILENAME, INTENTS_DIRNAME, INTENTS_REGISTRY_FILENAME, SPACES_DIRNAME,
    STAGE_GRAPH_REL, STATE_FILENAME, WORKSPACE_DIRNAME,
)
from .storage import EvidenceRef
from .wire import WireRecord

if TYPE_CHECKING:  # pragma: no cover - typing only
    # Annotations only: `projection` imports this module at runtime, so importing it back would close a
    # cycle. Everything is duck-typed, so a snapshot a test assembles by hand is as good as a real one.
    from .aidlc_reader import AuditEvent, StageNode, StageRow, StateFile
    from .projection import IntentSnapshot
    from .repo_registry import InstallHealth, RepoRecord
    from .sessions import BindingView, SlotView
    from .leases import LeaseRecord


# ---- the finding table (§1.7): one source for the engine, the UI catalog and the tests ---- #

#: Every finding code v1 can produce, in §1.7's order, with the severity it carries by default. The i18n
#: key is always ``finding.<code>`` (C11). ``state_unreadable`` is the one code whose emitted severity is
#: computed; ``unit_ambiguous`` is additive to §1.7 (FORMAT-NOTES §12.2).
FINDING_SEVERITIES: dict[str, str] = {
    "dangling_cursor": "blocking",
    "intent_registry_missing_row": "warn",
    "directive_state_digest_mismatch": "info",
    "phase_stage_disagreement": "blocking",
    "completed_count_mismatch": "warn",
    "total_count_mismatch": "warn",
    "stage_graph_drift": "warn",
    "gate_without_audit_row": "blocking",
    "audit_gate_without_checkbox": "blocking",
    "missing_required_artifact": "blocking",
    "state_version_unsupported": "blocking",
    "receipt_drift": "warn",
    "install_recovery_required": "blocking",
    "duplicate_identity": "blocking",
    "delivery_uncertain_action": "blocking",
    "lease_conflict": "blocking",
    "state_unreadable": "blocking",
    "interrupted_session": "blocking",
    "human_presence_anomaly": "blocking",
    "cursor_moved": "blocking",
    "wrong_slot": "blocking",
    "legacy_layout": "warn",
    "unstable_read": "info",
    "engine_version_unknown": "warn",
    "audit_incomplete": "info",
    "cursor_mismatch": "blocking",
    "unit_ambiguous": "blocking",
}
FINDING_CODES: tuple[str, ...] = tuple(FINDING_SEVERITIES)
#: The blocking set: a finding with one of these codes stops Run/Submit. Derived, so the two cannot drift.
BLOCKING_FINDING_CODES: frozenset[str] = frozenset(
    code for code, severity in FINDING_SEVERITIES.items() if severity == "blocking"
)
#: Codes only the reconciler can discover while resolving a delivery; it stores them on the action and they
#: are lifted back out here so they block and drive a ``recovery`` card (review P04/R01/R08).
ACTION_STORED_FINDING_CODES: tuple[str, ...] = (
    "human_presence_anomaly", "cursor_moved", "wrong_slot", "cursor_mismatch",
)
#: Where those codes may sit on an ``actions`` row: several shapes, because a finding dropped for its shape
#: would remove a blocking guard.
_ACTION_EVIDENCE_KEYS = ("findings", "finding")
#: "Not sure this decision landed" (§1.12/C04): dispatching another on top breaks at-most-once (PRD §12.1).
_UNCERTAIN_ACTION_STATUSES = ("ReconciliationRequired",)
#: Checkbox marks that contradict an open gate in the audit trail (§1.7 `audit_gate_without_checkbox`).
_NON_GATE_STAGE_STATES = ("not_started", "in_progress", "skipped")
#: ``Phase Progress`` values that mean the phase is not the one being executed.
_INACTIVE_PHASE_STATUSES = ("pending", "skipped")
#: Blocking first, and the UI's `#f-<n>` anchors stay stable across scans that produce the same set.
_SEVERITY_RANK = {"blocking": 0, "warn": 1, "info": 2}


# ---- public dataclasses ----------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Finding(WireRecord):
    """One reported contradiction with both readings attached; ``evidence`` names sources, never contents."""

    code: str
    severity: str
    message_key: str
    params: dict[str, Any]
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True)
class RepoFacts:
    """Everything the pure engine needs that is not inside the snapshot (review P26.2, FORMAT-NOTES §12.1)."""

    now: str
    repo: "RepoRecord"
    install: "InstallHealth"
    binding: "BindingView | None" = None
    slot: "SlotView | None" = None
    live_actions: Sequence[Any] = ()
    leases: Sequence["LeaseRecord"] = ()
    other_repos_same_identity: Sequence[str] = ()
    first_unreadable_at: str | None = None
    #: ``AidlcReader.layout(repo)``. Additive to §1.7: a legacy-layout repo has no snapshot to carry it.
    layout: str | None = None


# ---- engine ----------------------------------------------------------------- #


class ConsistencyEngine:
    """Reports contradictions; resolves none. Stateless and sync: every method is a pure function."""

    # ---- entry points -------------------------------------------------- #

    def evaluate_intent(self, snap: "IntentSnapshot", ctx: RepoFacts) -> list[Finding]:
        """Every finding for one intent, repository findings included, most severe first (§1.8/§1.12)."""
        out: list[Finding] = []
        out += self._cursor_and_registry(snap)
        out += self._state_findings(snap, ctx)
        out += self._directive_findings(snap)
        out += self._unit_findings(snap)
        out += self._audit_findings(snap)
        out += self._action_findings(snap, ctx)
        out += self._lease_findings(snap, ctx)
        out += self._session_findings(ctx)
        out += self._snapshot_quality(snap)
        out += self.evaluate_repo(ctx)
        return self._finalise(out)

    def evaluate_repo(self, facts: RepoFacts) -> list[Finding]:
        """Findings about the repository itself, independent of any intent."""
        out: list[Finding] = []
        repo_id = _attr(facts.repo, "repo_id")
        others = [r for r in (facts.other_repos_same_identity or ()) if r]
        if others:
            out.append(self._finding(
                "duplicate_identity",
                {"repo_id": repo_id, "other_repo_ids": sorted(others),
                 "identity": _attr(facts.repo, "resolved_identity")},
                (EvidenceRef("studio", f"repo:{repo_id}", _attr(facts.repo, "canonical_path")),
                 *(EvidenceRef("studio", f"repo:{other}", "same resolved identity") for other in sorted(others))),
            ))
        if _attr(facts.repo, "install_status") == "recovery_required":
            out.append(self._finding(
                "install_recovery_required",
                {"repo_id": repo_id,
                 "engine_dir": _attr(facts.install, "engine_dir") or _attr(facts.repo, "engine_dir"),
                 "receipt_id": _attr(facts.install, "receipt_id")},
                (EvidenceRef("studio", f"repo:{repo_id}", "install_status"),),
            ))
        if _attr(facts.install, "status") == "drift":
            out.append(self._finding(
                "receipt_drift",
                {"drift_count": _attr(facts.install, "drift_count", 0),
                 "installed_engine_version": _attr(facts.install, "engine_version"),
                 "receipt_engine_version": _attr(facts.install, "receipt_engine_version")},
                (EvidenceRef("studio", f"receipt:{_attr(facts.install, 'receipt_id')}",
                             f"engine {_attr(facts.install, 'receipt_engine_version')}"),
                 EvidenceRef("file", _attr(facts.install, "engine_dir") or C.STUDIO_HARNESS_DIR,
                             f"engine {_attr(facts.install, 'engine_version')}")),
            ))
        for harness in _attr(facts.install, "harness_dirs", ()) or ():
            if _attr(harness, "engine_version") is None:
                out.append(self._finding(
                    "engine_version_unknown", {"dir": _attr(harness, "dir")},
                    (EvidenceRef("file", f"{_attr(harness, 'dir')}/tools", "no AIDLC_VERSION found"),),
                ))
        if facts.layout == "legacy":
            out.append(self._finding(
                "legacy_layout", {"path": C.LEGACY_STATE_REL},
                (EvidenceRef("file", C.LEGACY_STATE_REL, "pre-spaces layout"),),
            ))
        return self._finalise(out)

    def blocking(self, findings: list[Finding]) -> list[Finding]:
        """The subset that stops Run/Submit, by *emitted* severity (a ``state_unreadable`` in grace does not)."""
        return [f for f in findings if f.severity == "blocking"]

    def unstable(self, snap: "IntentSnapshot") -> bool:
        """True when the snapshot's own reads disagreed: its card is ``Refreshing`` and authorises nothing."""
        return not bool(_attr(snap, "stable", True))

    # ---- cursor and registry ------------------------------------------ #

    def _cursor_and_registry(self, snap: "IntentSnapshot") -> list[Finding]:
        out: list[Finding] = []
        space = _attr(snap, "space") or "default"
        intents_dir = _intents_rel(space)
        if _attr(snap, "dangling_cursor", False):
            # "explicit arg > active-intent > lone intent" (05 §8): a cursor naming a record without a state
            # file falls through silently to a DIFFERENT record, so the decision's intent is unknowable.
            out.append(self._finding(
                "dangling_cursor", {"space": space, "intent_dir": _attr(snap, "intent_dir")},
                (EvidenceRef("file", f"{intents_dir}/{ACTIVE_INTENT_FILENAME}",
                             "names a record with no aidlc-state.md"),
                 EvidenceRef("file", intents_dir, "record directories")),
            ))
        if _attr(snap, "row", "__missing__") is None:
            out.append(self._finding(
                "intent_registry_missing_row", {"intent_dir": _attr(snap, "intent_dir"), "space": space},
                (EvidenceRef("file", f"{intents_dir}/{INTENTS_REGISTRY_FILENAME}", "no row for dirName"),
                 EvidenceRef("file", self._state_rel(snap), "record exists on disk")),
            ))
        return out

    # ---- state file --------------------------------------------------- #

    def _state_findings(self, snap: "IntentSnapshot", ctx: RepoFacts) -> list[Finding]:
        state: "StateFile | None" = _attr(snap, "state")
        snapshot = _attr(snap, "state_snap")
        state_rel = self._state_rel(snap)

        reason = None
        if state is None or snapshot is None:
            reason = "missing"
        elif _attr(state, "header_only", False):
            reason = "header_only"
        if reason is not None:
            # Nothing else about the file can be asserted; derived findings would repeat one root cause.
            return [self._state_unreadable(snap, ctx, reason, state_rel)]

        out: list[Finding] = []
        rows: list["StageRow"] = list(_attr(state, "stages", ()) or ())
        by_slug = {_attr(r, "slug"): r for r in rows}
        counts: Mapping[str, int] = _attr(state, "counts", {}) or {}

        out += self._phase_findings(state, by_slug, state_rel)

        claimed_done = _attr(state, "completed_claimed")
        actual_done = counts.get("completed")
        if claimed_done is not None and actual_done is not None and int(claimed_done) != int(actual_done):
            out.append(self._finding(
                "completed_count_mismatch", {"claimed": int(claimed_done), "actual": int(actual_done)},
                (EvidenceRef("file", state_rel, f"Completed: {claimed_done}"),
                 EvidenceRef("file", state_rel, f"{actual_done} rows marked [x]")),
            ))

        claimed_total = _attr(state, "total_stages_claimed")
        actual_total = sum(1 for r in rows if (_attr(r, "suffix") or "").strip().upper().startswith("EXECUTE"))
        if claimed_total is not None and rows and int(claimed_total) != actual_total:
            out.append(self._finding(
                "total_count_mismatch", {"claimed": int(claimed_total), "actual": actual_total},
                (EvidenceRef("file", state_rel, f"Total Stages: {claimed_total}"),
                 EvidenceRef("file", state_rel, f"{actual_total} rows suffixed EXECUTE")),
            ))

        out += self._graph_drift(snap, rows)
        out += self._state_version(snap, ctx, state, state_rel)
        out += self._artifact_findings(snap, state, by_slug)
        return out

    def _phase_findings(
        self, state: "StateFile", by_slug: Mapping[str, "StageRow"], state_rel: str
    ) -> list[Finding]:
        """`Lifecycle Phase` versus `Phase Progress`, and versus the current stage's phase: both, if both hold."""
        lifecycle = (_attr(state, "lifecycle_phase") or "").strip()
        if not lifecycle:
            return []
        out: list[Finding] = []
        phases = {str(name).strip().lower(): str(status).strip() for name, status in
                  (_attr(state, "phases", ()) or ())}
        status = phases.get(lifecycle.lower())
        if status is None or status.lower() in _INACTIVE_PHASE_STATUSES:
            out.append(self._finding(
                "phase_stage_disagreement",
                {"reason": "phase_not_active", "phase": lifecycle, "phase_status": status},
                (EvidenceRef("file", state_rel, f"Lifecycle Phase: {lifecycle}"),
                 EvidenceRef("file", state_rel,
                             f"Phase Progress: {lifecycle} = {status if status is not None else 'absent'}")),
            ))

        current = (_attr(state, "current_stage") or "").strip()
        if current:
            row = by_slug.get(current)
            row_phase = (_attr(row, "phase") if row is not None else None) or None
            if row is None:
                out.append(self._finding(
                    "phase_stage_disagreement",
                    {"reason": "stage_row_absent", "phase": lifecycle, "stage": current, "stage_phase": None},
                    (EvidenceRef("file", state_rel, f"Current Stage: {current}"),
                     EvidenceRef("file", state_rel, "no Stage Progress row with that slug")),
                ))
            elif row_phase and row_phase.strip().lower() != lifecycle.lower():
                out.append(self._finding(
                    "phase_stage_disagreement",
                    {"reason": "stage_not_in_phase", "phase": lifecycle, "stage": current,
                     "stage_phase": row_phase},
                    (EvidenceRef("file", state_rel, f"Lifecycle Phase: {lifecycle}"),
                     EvidenceRef("file", state_rel, f"{current} sits under {row_phase}")),
                ))
        return out

    def _graph_drift(self, snap: "IntentSnapshot", rows: Sequence["StageRow"]) -> list[Finding]:
        graph: Sequence["StageNode"] = _attr(snap, "graph", ()) or ()
        if not graph or not rows:
            # With only one source there is no disagreement to report.
            return []
        state_slugs = {_attr(r, "slug") for r in rows}
        graph_slugs = {_attr(n, "slug") for n in graph}
        only_state = sorted(state_slugs - graph_slugs)
        only_graph = sorted(graph_slugs - state_slugs)
        if not only_state and not only_graph:
            return []
        return [self._finding(
            "stage_graph_drift",
            {"only_in_state": only_state, "only_in_graph": only_graph,
             "state_rows": len(state_slugs), "graph_stages": len(graph_slugs)},
            (EvidenceRef("file", self._state_rel(snap), f"{len(state_slugs)} stage rows"),
             EvidenceRef("file", self._graph_rel(snap), f"{len(graph_slugs)} graph stages")),
        )]

    def _state_version(
        self, snap: "IntentSnapshot", ctx: RepoFacts, state: "StateFile", state_rel: str
    ) -> list[Finding]:
        claimed = _attr(state, "state_version")
        engine_version = _attr(ctx.install, "engine_state_version")
        if engine_version is None:
            engine_version = _attr(_attr(snap, "engine"), "engine_state_version")
        engine_dir = _attr(ctx.install, "engine_dir") or _attr(_attr(snap, "engine"), "dir") \
            or C.STUDIO_HARNESS_DIR

        if claimed not in C.SUPPORTED_STATE_VERSIONS:
            reason = "unsupported"
        elif engine_version is not None and int(claimed) != int(engine_version):
            # A 2.6.2 engine over a v7 record is the mixed-version install P-06 forbids (C21): two
            # grammars would interpret the same file, so nothing may be dispatched against it.
            reason = "engine_mismatch"
        else:
            return []
        return [self._finding(
            "state_version_unsupported",
            {"reason": reason, "claimed": claimed, "engine_state_version": engine_version,
             "supported": list(C.SUPPORTED_STATE_VERSIONS)},
            (EvidenceRef("file", state_rel, f"{C.STATE_VERSION_FIELD}: {claimed}"),
             EvidenceRef("file", f"{engine_dir}/tools", f"engine writes v{engine_version}")),
        )]

    def _state_unreadable(
        self, snap: "IntentSnapshot", ctx: RepoFacts, reason: str, state_rel: str
    ) -> Finding:
        since = ctx.first_unreadable_at or _attr(snap, "first_unreadable_at")
        elapsed = _elapsed_secs(since, ctx.now)
        blocked = elapsed is not None and elapsed > C.STATE_UNREADABLE_GRACE_SECS
        return self._finding(
            "state_unreadable",
            {"reason": reason, "since": since, "elapsed_secs": elapsed,
             "grace_secs": C.STATE_UNREADABLE_GRACE_SECS},
            (EvidenceRef("file", state_rel, reason),
             EvidenceRef("studio", f"intent:{_attr(snap, 'intent_key')}",
                         f"first unreadable at {since}" if since else "first observation")),
            severity="blocking" if blocked else "info",
        )

    # ---- directive ---------------------------------------------------- #

    def _directive_findings(self, snap: "IntentSnapshot") -> list[Finding]:
        directive = _attr(snap, "directive")
        if directive is None or _attr(directive, "matches_state", True):
            return []
        state = _attr(snap, "state")
        if (str(_attr(state, "status", "") or "")).strip().lower() == "completed":
            # A finished workflow never writes the note again, so "normal right after a state update" would stay on
            # every card for good: there is no running stage left for the note to be about.
            return []
        actual = _attr(_attr(snap, "state_snap"), "sha256")
        marker = _attr(directive, "marker_name") or DIRECTIVE_FILENAME
        return [self._finding(
            "directive_state_digest_mismatch",
            {"stage": _attr(directive, "stage"), "unit": _attr(directive, "unit"),
             "directive_state_sha256": _attr(directive, "state_sha256"), "state_sha256": actual},
            (EvidenceRef("file", f"{self._record_rel(snap)}/{marker}",
                         f"state_sha256 {_short(_attr(directive, 'state_sha256'))}"),
             EvidenceRef("file", self._state_rel(snap), f"sha256 {_short(actual)}")),
        )]

    # ---- which unit is executing -------------------------------------- #

    def _unit_findings(self, snap: "IntentSnapshot") -> list[Finding]:
        """A per-unit stage whose executing unit no source names (FORMAT-NOTES §12.3)."""
        stage = (_attr(snap, "stage") or "").strip()
        if stage not in C.PER_UNIT_STAGES or _attr(snap, "unit"):
            return []
        units = sorted(unit for unit in (_attr(snap, "units", ()) or ()) if unit)
        if len(units) < 2:
            return []
        if _unit_of_path(_attr(_attr(snap, "questions"), "relpath"), units):
            return []
        record = self._record_rel(snap)
        marker = _attr(_attr(snap, "directive"), "marker_name") or DIRECTIVE_FILENAME
        return [self._finding(
            "unit_ambiguous",
            {"stage": stage, "reason": "no_source_names_the_unit",
             "directive_unit": _attr(_attr(snap, "directive"), "unit"),
             "state_unit": _attr(_attr(snap, "state"), "active_unit"), "units": units},
            (EvidenceRef("file", self._state_rel(snap), "no Active Unit field"),
             EvidenceRef("file", f"{record}/{marker}", "no unit key"),
             EvidenceRef("file", f"{record}/construction", f"{len(units)} unit directories: {', '.join(units)}")),
        )]

    # ---- audit versus checkboxes -------------------------------------- #

    def _audit_findings(self, snap: "IntentSnapshot") -> list[Finding]:
        state: "StateFile | None" = _attr(snap, "state")
        if state is None or _attr(state, "header_only", False):
            return []
        bundle = _attr(snap, "audit")
        events: Sequence["AuditEvent"] = _attr(bundle, "events", ()) or ()
        complete = bool(_attr(bundle, "complete", True))
        rows: Sequence["StageRow"] = _attr(state, "stages", ()) or ()
        by_slug = {_attr(r, "slug"): r for r in rows}
        state_rel = self._state_rel(snap)
        out: list[Finding] = []

        for row in rows:
            if _attr(row, "state") != "awaiting_approval":
                continue
            slug = _attr(row, "slug")
            awaiting = _latest(events, ("STAGE_AWAITING_APPROVAL",), slug)
            started = _latest(events, ("STAGE_STARTED",), slug)
            rejected = _latest(events, ("GATE_REJECTED",), slug)
            newer = max((e for e in (started, rejected) if e is not None),
                        key=_event_key, default=None)
            if awaiting is not None and (newer is None or _event_key(awaiting) > _event_key(newer)):
                continue
            out.append(self._finding(
                "gate_without_audit_row",
                {"stage": slug, "reason": "absent" if awaiting is None else "superseded",
                 "awaiting_at": _attr(awaiting, "timestamp"), "superseded_by": _attr(newer, "event"),
                 "superseded_at": _attr(newer, "timestamp"), "audit_complete": complete},
                (EvidenceRef("file", state_rel, f"row [{_attr(row, 'mark')}] {slug}"),
                 *(() if awaiting is None else (_audit_ref(awaiting),)),
                 *(() if newer is None else (_audit_ref(newer),)),
                 *(() if complete else (EvidenceRef("audit", self._audit_rel(snap),
                                                    "shard tail-read; history is partial"),))),
            ))

        current = (_attr(state, "current_stage") or "").strip()
        if current:
            latest_gate = _latest(events, C.GATE_EVENTS, current)
            row = by_slug.get(current)
            if (
                latest_gate is not None
                and _attr(latest_gate, "event") == "STAGE_AWAITING_APPROVAL"
                and row is not None
                and _attr(row, "state") in _NON_GATE_STAGE_STATES
            ):
                out.append(self._finding(
                    "audit_gate_without_checkbox",
                    {"stage": current, "mark": _attr(row, "mark"), "row_state": _attr(row, "state"),
                     "awaiting_at": _attr(latest_gate, "timestamp")},
                    (_audit_ref(latest_gate),
                     EvidenceRef("file", state_rel, f"row [{_attr(row, 'mark')}] {current}")),
                ))

        if not complete:
            shards = _attr(bundle, "shards", ()) or ()
            out.append(self._finding(
                "audit_incomplete",
                {"shards": [_attr(s, "relpath") for s in shards],
                 "truncated": [_attr(s, "relpath") for s in shards if _attr(s, "truncated", False)]},
                (EvidenceRef("audit", self._audit_rel(snap), "tail-read"),),
            ))
        return out

    def _artifact_findings(
        self, snap: "IntentSnapshot", state: "StateFile", by_slug: Mapping[str, "StageRow"]
    ) -> list[Finding]:
        """Declared, non-optional, kind-unrestricted `produces` missing at an open gate (FORMAT-NOTES §12.3)."""
        current = (_attr(state, "current_stage") or "").strip()
        row = by_slug.get(current) if current else None
        if row is None or _attr(row, "state") != "awaiting_approval":
            return []
        node = next((n for n in (_attr(snap, "graph", ()) or ()) if _attr(n, "slug") == current), None)
        if node is None:
            return []
        if C.artifact_output_location(node) is None:
            # Do not invent a blocking finding for a path the snapshot does not know how to read.
            return []

        optional = set(_attr(node, "optional_produces", ()) or ())
        if current == "build-and-test":
            # The graph lists the union of outputs; Steps 3–7 need no extra test instructions for Minimal
            # and only integration instructions for Standard.
            strategy = (_attr(state, "scope_config", {}) or {}).get("Test Strategy")
            if strategy in ("Minimal", "Standard"):
                optional.update(("performance-test-instructions", "security-test-instructions"))
            if strategy == "Minimal":
                optional.add("integration-test-instructions")
        kinds = _attr(node, "produces_kinds", {}) or {}
        required = [name for name in (_attr(node, "produces", ()) or ())
                    if name not in optional and name not in kinds]
        if not required:
            return []

        artifacts = list(_attr(snap, "stage_artifacts", ()) or ())
        present = set()
        for meta in artifacts:
            for candidate in (_attr(meta, "name"), (_attr(meta, "relpath") or "").rsplit("/", 1)[-1]):
                if candidate:
                    present.add(candidate)
                    present.add(candidate.rsplit(".", 1)[0])
        stage_dir = self._stage_dir_rel(snap, node, artifacts)

        out: list[Finding] = []
        for name in required:
            expected_name = C.ARTIFACT_FILENAME_ALIASES.get(name, name)
            if expected_name in present:
                continue
            out.append(self._finding(
                "missing_required_artifact",
                {"stage": current, "artifact": name, "stage_dir": stage_dir,
                 "present": sorted(_attr(m, "name") or "" for m in artifacts)},
                (EvidenceRef("file", self._graph_rel(snap), f"{current} produces {name}"),
                 EvidenceRef("file", stage_dir, f"no regular file for {expected_name} in this directory")),
            ))
        return out

    # ---- Studio rows -------------------------------------------------- #

    def _action_findings(self, snap: "IntentSnapshot", ctx: RepoFacts) -> list[Finding]:
        out: list[Finding] = []
        for rec in _actions_for_intent(ctx.live_actions, snap):
            status = _attr(rec, "status")
            action_id = _attr(rec, "action_id")
            if status in _UNCERTAIN_ACTION_STATUSES:
                out.append(self._finding(
                    "delivery_uncertain_action",
                    {"action_id": action_id, "status": status, "type": _attr(rec, "type"),
                     "stage": _attr(rec, "stage")},
                    (EvidenceRef("studio", f"action:{action_id}", status),),
                ))
            out += self._stored_action_findings(rec)
        return out

    def _stored_action_findings(self, rec: Any) -> list[Finding]:
        action_id = _attr(rec, "action_id")
        status = _attr(rec, "status")
        out: list[Finding] = []
        seen: set[str] = set()

        for code, extra in _stored_findings(rec):
            if code not in ACTION_STORED_FINDING_CODES or code in seen:
                continue
            seen.add(code)
            params = {"action_id": action_id, "status": status}
            params.update(extra)
            out.append(self._finding(code, params, (EvidenceRef("studio", f"action:{action_id}", code),)))

        readback = _attr(rec, "cursor_readback")
        if readback is not None and not _attr(readback, "ok", True) and "cursor_mismatch" not in seen:
            out.append(self._finding(
                "cursor_mismatch",
                {"action_id": action_id, "status": status, "mismatch": list(_attr(readback, "mismatch", ()) or ()),
                 "space": _attr(readback, "space"), "dir_name": _attr(readback, "dir_name")},
                (EvidenceRef("studio", f"action:{action_id}", "cursor read-back refused"),),
            ))
        return out

    def _lease_findings(self, snap: "IntentSnapshot", ctx: RepoFacts) -> list[Finding]:
        """Another intent's execution lease, or any admin lease, blocks this one (FORMAT-NOTES §12.3)."""
        uuid = _attr(_attr(snap, "row"), "uuid")
        out: list[Finding] = []
        for lease in ctx.leases or ():
            if not _lease_matches_repo(lease, ctx.repo):
                continue
            kind = _attr(lease, "kind")
            if kind == "execution" and _attr(lease, "intent_uuid") == uuid:
                continue
            out.append(self._finding(
                "lease_conflict",
                {"kind": kind, "reason": "admin_lease" if kind != "execution" else "other_intent",
                 "intent_uuid": _attr(lease, "intent_uuid"), "operation_type": _attr(lease, "operation_type"),
                 "action_id": _attr(lease, "action_id"), "acquired_at": _attr(lease, "acquired_at"),
                 "heartbeat_at": _attr(lease, "heartbeat_at")},
                (EvidenceRef("studio", f"lease:{kind}:{_attr(lease, 'resolved_repo_identity')}",
                             f"generation {_attr(lease, 'generation')}"),
                 EvidenceRef("studio", f"intent:{_attr(snap, 'intent_key')}", f"uuid {uuid}")),
            ))
        return out

    def _session_findings(self, ctx: RepoFacts) -> list[Finding]:
        binding = ctx.binding
        interrupted_at = _attr(binding, "interrupted_at") if binding is not None else None
        if not interrupted_at:
            return []
        slot = ctx.slot
        if slot is not None and _slot_busy_stopping(slot):
            # The force stop is still in flight: PRD §11.2 keeps the intent `Interrupted` until the slot is
            # idle, and only then does manual takeover become mandatory (review P17).
            return []
        return [self._finding(
            "interrupted_session",
            {"interrupted_at": interrupted_at, "slot_key": _attr(binding, "slot_key"),
             "slot_present": slot is not None},
            (EvidenceRef("studio", f"binding:{_attr(binding, 'intent_key')}", f"interrupted_at {interrupted_at}"),
             EvidenceRef("host", f"slot:{_attr(binding, 'slot_key')}", "absent" if slot is None else "idle")),
        )]

    def _snapshot_quality(self, snap: "IntentSnapshot") -> list[Finding]:
        if not self.unstable(snap):
            return []
        return [self._finding(
            "unstable_read", {"intent_key": _attr(snap, "intent_key"), "taken_at": _attr(snap, "taken_at")},
            (EvidenceRef("file", self._state_rel(snap), "changed while being read"),),
        )]

    # ---- construction helpers ----------------------------------------- #

    def _finding(
        self,
        code: str,
        params: Mapping[str, Any],
        evidence: Iterable[EvidenceRef],
        *,
        severity: str | None = None,
    ) -> Finding:
        return Finding(
            code=code,
            severity=severity or FINDING_SEVERITIES[code],
            message_key=f"finding.{code}",
            params={key: _jsonable(value) for key, value in params.items()},
            evidence=tuple(evidence),
        )

    @staticmethod
    def _finalise(findings: list[Finding]) -> list[Finding]:
        """De-duplicate identical findings (same code and params) and order them blocking → warn → info."""
        seen: set[str] = set()
        unique: list[Finding] = []
        for finding in findings:
            key = finding.code + "|" + json.dumps(finding.params, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            unique.append(finding)
        return sorted(unique, key=lambda f: (_SEVERITY_RANK.get(f.severity, 9), f.code))

    # ---- relpath helpers: the snapshot's own relpath wins; these name files it could not read ---- #

    @staticmethod
    def _record_rel(snap: "IntentSnapshot") -> str:
        return f"{_intents_rel(_attr(snap, 'space') or 'default')}/{_attr(snap, 'intent_dir')}"

    def _state_rel(self, snap: "IntentSnapshot") -> str:
        return _attr(_attr(snap, "state_snap"), "relpath") or f"{self._record_rel(snap)}/{STATE_FILENAME}"

    def _audit_rel(self, snap: "IntentSnapshot") -> str:
        shards = _attr(_attr(snap, "audit"), "shards", ()) or ()
        return _attr(shards[0], "relpath") if shards else f"{self._record_rel(snap)}/audit"

    @staticmethod
    def _graph_rel(snap: "IntentSnapshot") -> str:
        engine_dir = _attr(_attr(snap, "engine"), "dir") or C.STUDIO_HARNESS_DIR
        return f"{engine_dir}/{STAGE_GRAPH_REL}"

    def _stage_dir_rel(
        self, snap: "IntentSnapshot", node: "StageNode", artifacts: Sequence[Any]
    ) -> str:
        resolved = _attr(snap, "artifact_dir_rel") or _attr(snap, "stage_dir_rel")
        if resolved:
            return str(resolved)
        for meta in artifacts:
            relpath = _attr(meta, "relpath") or ""
            if "/" in relpath:
                return relpath.rsplit("/", 1)[0]
        phase = (_attr(node, "phase") or "").strip().lower()
        return f"{self._record_rel(snap)}/{phase}/{_attr(node, 'slug')}"


# ---- module-private functions ----------------------------------------------- #

#: Objects come from four other modules this one must not import, so every read is structural.
_attr = C.attr


def _intents_rel(space: str) -> str:
    return f"{WORKSPACE_DIRNAME}/{SPACES_DIRNAME}/{space}/{INTENTS_DIRNAME}"


def _event_key(event: Any) -> tuple[str, int, int]:
    """The reader's merge order (timestamp, shard_index, pos), so no order the merge lacks is claimed."""
    return (
        str(_attr(event, "timestamp") or ""),
        int(_attr(event, "shard_index", 0) or 0),
        int(_attr(event, "pos", 0) or 0),
    )


def _latest(events: Sequence[Any], types: Sequence[str], stage: str | None = None) -> Any:
    """The newest event of one of ``types`` (optionally for one stage), or None."""
    wanted = set(types)
    best = None
    for event in events:
        if _attr(event, "event") not in wanted:
            continue
        if stage is not None and (_attr(event, "fields", {}) or {}).get("Stage") != stage:
            continue
        if best is None or _event_key(event) >= _event_key(best):
            best = event
    return best


def _audit_ref(event: Any) -> EvidenceRef:
    return EvidenceRef(
        "audit",
        f"{_attr(event, 'shard') or ''}#{_attr(event, 'pos', 0)}",
        f"{_attr(event, 'event')} {_attr(event, 'timestamp')}",
    )


def _unit_of_path(relpath: str | None, units: Sequence[str]) -> str | None:
    """The segment after ``construction`` when it is one of the enumerated ``units`` (FORMAT-NOTES §8)."""
    parts = (relpath or "").split("/")
    for index, part in enumerate(parts[:-1]):
        if part == "construction" and parts[index + 1] in set(units):
            return parts[index + 1]
    return None


def _actions_for_intent(actions: Sequence[Any], snap: "IntentSnapshot") -> list[Any]:
    """The rows that belong to this intent: one intent's uncertain delivery must not block a sibling."""
    intent_key = _attr(snap, "intent_key")
    space = _attr(snap, "space")
    intent_dir = _attr(snap, "intent_dir")
    out = []
    for rec in actions or ():
        rec_key = _attr(rec, "intent_key")
        if rec_key is not None and intent_key is not None:
            if rec_key == intent_key:
                out.append(rec)
            continue
        if _attr(rec, "intent_dir") == intent_dir and _attr(rec, "space") == space:
            out.append(rec)
    return out


def _stored_findings(rec: Any) -> list[tuple[str, dict[str, Any]]]:
    """Finding codes the reconciler recorded on an action, in every shape it may write (FORMAT-NOTES §12.2)."""
    evidence = _attr(rec, "evidence", {}) or {}
    if not isinstance(evidence, Mapping):
        return []
    out: list[tuple[str, dict[str, Any]]] = []
    for key in _ACTION_EVIDENCE_KEYS:
        raw = evidence.get(key)
        for item in (raw if isinstance(raw, (list, tuple)) else [raw]):
            if isinstance(item, str):
                out.append((item, {}))
            elif isinstance(item, Mapping):
                code = item.get("code")
                if isinstance(code, str):
                    params = item.get("params")
                    out.append((code, dict(params) if isinstance(params, Mapping) else {}))
    return out


def _lease_matches_repo(lease: Any, repo: Any) -> bool:
    identity = _attr(repo, "resolved_identity")
    if identity and _attr(lease, "resolved_repo_identity") == identity:
        return True
    lease_repo = _attr(lease, "repo_id")
    return bool(lease_repo) and lease_repo == _attr(repo, "repo_id")


def _slot_busy_stopping(slot: Any) -> bool:
    """True while the host slot is still running or still processing a stop."""
    if _attr(slot, "running", False):
        return True
    if _attr(slot, "stopping", False):
        return True
    return (_attr(slot, "stop_state", "idle") or "idle") != "idle"


def _elapsed_secs(since: str | None, now: str | None) -> int | None:
    """Whole seconds between two ISO-Z timestamps, or None when either cannot be parsed."""
    start, end = _epoch(since), _epoch(now)
    if start is None or end is None:
        return None
    return int(end - start)


#: Parsing only: "now" always comes from ``RepoFacts.now``, so a scan is reproducible from its inputs.
_epoch = C.epoch_from_iso


def _short(digest: str | None) -> str:
    return (digest or "")[:12] or "none"


def _jsonable(value: Any) -> Any:
    """Params must survive ``json.dumps``; tuples and sets become lists, everything else is left."""
    if isinstance(value, (tuple, set, frozenset, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value
