"""Contradiction reporting over one coherent read of an AI-DLC intent.

This module is the one place in Studio that is allowed to notice that two sources disagree, and the
one place that is forbidden to decide which of them is right. AI-DLC owns the lifecycle files; Studio
only observes them (architecture §3). So when ``aidlc-state.md`` says a gate is open and the audit
trail has no matching ``STAGE_AWAITING_APPROVAL``, the answer is never "trust the state file" — it is
a finding that carries **both** readings as evidence and, when the disagreement could change which
intent, stage, gate or artifact a decision applies to, blocks the decision entirely
(``409 state_inconsistent``, PRD §12.3 / FR-ACT-009).

Two design consequences worth stating, because both look like missing features until you know why:

* **Severity is not a matter of taste.** ``blocking`` is reserved for disagreements that could make a
  human decision land on the wrong boundary — a dangling cursor (the engine would resolve a different
  record), a gate the audit cannot corroborate (the approval may be forged or stale), a state version
  the installed engine does not write (the file may be interpreted by two different grammars). Drift
  that is merely *untidy* (a wrong ``Completed`` count, a receipt that is one release old) is ``warn``
  and never stops work: FR-ACT-008 keeps maintenance out of the blocking queue.
* **Absence of evidence is reported, not resolved.** A truncated audit tail, an unreadable state file
  inside its grace window and an unstable read are ``info`` findings that say "this snapshot cannot
  authorise a decision yet", which is a different claim from "the workflow is broken".

The engine is pure: no file is opened, nothing is written, no clock is read (``RepoFacts.now`` is
supplied by the caller). That is what lets the reconciler call it once per intent per scan on a worker
thread and lets the tests build every contradiction in the table from real fixture bytes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

from . import constants as C
from .storage import EvidenceRef

if TYPE_CHECKING:  # pragma: no cover - typing only
    # Imported for annotations only. `projection` imports THIS module at runtime (it constructs a
    # ConsistencyEngine), so a runtime import here would close a cycle; and the reader opens files,
    # which a pure module must not depend on. Everything below is duck-typed against the §1.6/§1.8
    # dataclasses, so a snapshot assembled by hand in a test is indistinguishable from a real one.
    from .aidlc_reader import AuditEvent, StageNode, StageRow, StateFile
    from .projection import IntentSnapshot
    from .repo_registry import InstallHealth, RepoRecord
    from .sessions import BindingView, SlotView
    from .storage import LeaseRecord


# --------------------------------------------------------------------------- #
# the finding table (§1.7) — data, so the engine, the UI catalog and the tests read one source
# --------------------------------------------------------------------------- #

#: Every finding code v1 can produce, in the order §1.7 lists them. The i18n key of a code is always
#: ``finding.<code>`` (C11: the backend ships keys and params, never English prose).
FINDING_CODES: tuple[str, ...] = (
    "dangling_cursor",
    "intent_registry_missing_row",
    "directive_state_digest_mismatch",
    "phase_stage_disagreement",
    "completed_count_mismatch",
    "total_count_mismatch",
    "stage_graph_drift",
    "gate_without_audit_row",
    "audit_gate_without_checkbox",
    "missing_required_artifact",
    "state_version_unsupported",
    "receipt_drift",
    "install_recovery_required",
    "duplicate_identity",
    "delivery_uncertain_action",
    "lease_conflict",
    "state_unreadable",
    "interrupted_session",
    "human_presence_anomaly",
    "cursor_moved",
    "wrong_slot",
    "legacy_layout",
    "unstable_read",
    "engine_version_unknown",
    "audit_incomplete",
    "cursor_mismatch",
    #: Additive to §1.7's table (whose list predates per-unit Construction being read): a per-unit stage
    #: whose executing unit no source names. Emitted here rather than left to the reader because only
    #: this module may say two sources disagree, and the reader's refusal to guess produces a card with
    #: no questions on it, which on its own looks like a bug instead of a contradiction.
    "unit_ambiguous",
)

#: The severity each code carries by default. ``state_unreadable`` is the single code whose emitted
#: severity is computed rather than looked up: a state file that is missing *right now* is usually a
#: write in flight, so it is ``info`` until it has stayed unreadable for
#: ``STATE_UNREADABLE_GRACE_SECS`` (review P26.2). Its table severity stays ``blocking`` because that
#: is what it becomes.
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

#: The blocking set: a finding with one of these codes stops Run/Submit. Derived from the table above
#: so the two can never drift apart.
BLOCKING_FINDING_CODES: frozenset[str] = frozenset(
    code for code, severity in FINDING_SEVERITIES.items() if severity == "blocking"
)

#: Codes the ENGINE cannot derive from disk: the reconciler discovers them while resolving a delivery
#: (it compared the human-presence baseline, the cursor, or the slot a transcript row landed in) and
#: stores them on the action. Consistency lifts them back out so they join the blocking set, drive
#: `operational_state` rule 2 and produce a `recovery` card — otherwise a presence anomaly would be
#: visible only on one action's evidence blob (review P04/R01/R08).
ACTION_STORED_FINDING_CODES: tuple[str, ...] = (
    "human_presence_anomaly",
    "cursor_moved",
    "wrong_slot",
    "cursor_mismatch",
)

#: Where the reconciler / broker may record those codes on an ``actions`` row. Accepting several
#: shapes is deliberate: the writer is a different module, and a finding that is silently dropped
#: because it was stored as a string instead of a dict would remove a blocking guard.
_ACTION_EVIDENCE_KEYS = ("findings", "finding")

#: Action statuses that mean "Studio is not sure this decision landed" (§1.12/C04). Both are blocking:
#: dispatching a second decision on top of an unresolved one is the at-most-once violation PRD §12.1
#: forbids.
_UNCERTAIN_ACTION_STATUSES = ("DeliveryUncertain", "ReconciliationRequired")

#: Checkbox marks that contradict an open gate in the audit trail (§1.7 `audit_gate_without_checkbox`).
_NON_GATE_STAGE_STATES = ("not_started", "in_progress", "skipped")

#: ``Phase Progress`` values that mean the phase is not the one being executed.
_INACTIVE_PHASE_STATUSES = ("pending", "skipped")

#: Repo-relative layout, used only to name a file in ``evidence``. The snapshot carries its own
#: ``relpath`` for everything it read (state, audit shards, artifacts) and that is always preferred;
#: these templates exist because the most important findings are about files the snapshot could NOT
#: turn into an object — a dangling cursor, a missing registry row, an artifact that is not there.
#: ``AidlcReader`` owns the same layout; if it ever grows public path helpers these should use them.
_INTENTS_DIR_TEMPLATE = "aidlc/spaces/{space}/intents"
_CURSOR_FILENAME = "active-intent"
_REGISTRY_FILENAME = "intents.json"
_STATE_FILENAME = "aidlc-state.md"
_DIRECTIVE_FILENAME = ".aidlc-active-directive.json"
_STAGE_GRAPH_REL = "tools/data/stage-graph.json"
#: The record subdirectory whose immediate children are units of work, and the state field the engine
#: mirrors the executing unit into. Named only in `unit_ambiguous` evidence.
_CONSTRUCTION_DIRNAME = "construction"
_ACTIVE_UNIT_FIELD = "Active Unit"

#: Sort order for a findings list: the reader sees what blocks first, and the UI's `#f-<n>` anchors
#: stay stable across scans that produce the same set.
_SEVERITY_RANK = {"blocking": 0, "warn": 1, "info": 2}


# --------------------------------------------------------------------------- #
# public dataclasses
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Finding:
    """One reported contradiction, with both readings attached.

    ``params`` is what the UI interpolates into ``message_key`` (``finding.<code>``); it is
    JSON-serialisable and contains no absolute path and no credential — a finding travels into the
    Action Center, the timeline and exports. ``evidence`` names the sources, never their contents: a
    file's repo-relative path, an audit block as ``<shard>#<pos>``, a Studio row as its id.
    """

    code: str
    severity: str
    message_key: str
    params: dict[str, Any]
    evidence: tuple[EvidenceRef, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message_key": self.message_key,
            "params": dict(self.params),
            "evidence": [ref.to_json() for ref in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class RepoFacts:
    """Everything the pure engine needs that is not inside the snapshot (review P26.2).

    Assembled by the reconciler (or a handler) because each field comes from a layer the engine may
    not reach: ``repo``/``install`` from the registry, ``binding``/``slot`` from the session layer,
    ``live_actions``/``leases`` from Storage, ``first_unreadable_at`` from the reconciler's own
    per-intent memory (it is the only component that has seen the previous ticks).
    """

    now: str
    repo: "RepoRecord"
    install: "InstallHealth"
    binding: "BindingView | None" = None
    slot: "SlotView | None" = None
    live_actions: Sequence[Any] = ()
    leases: Sequence["LeaseRecord"] = ()
    other_repos_same_identity: Sequence[str] = ()
    first_unreadable_at: str | None = None
    #: ``AidlcReader.layout(repo)`` — "spaces" | "legacy" | None. Additive to §1.7 because
    #: `legacy_layout` is a disk fact about a repo that has NO spaces intents, so no IntentSnapshot
    #: exists to carry it and `evaluate_repo` has nowhere else to read it from. Optional, so a caller
    #: that does not know the layout simply gets no layout finding.
    layout: str | None = None


# --------------------------------------------------------------------------- #
# engine
# --------------------------------------------------------------------------- #


class ConsistencyEngine:
    """Reports contradictions; resolves none.

    Stateless and sync. Every method is a pure function of its arguments, which is why the reconciler
    can run it on the scan pool for 64 repositories without ordering constraints, and why a test can
    assert a code appears by handing it a snapshot built from fixture bytes.
    """

    # ---- entry points -------------------------------------------------- #

    def evaluate_intent(self, snap: "IntentSnapshot", ctx: RepoFacts) -> list[Finding]:
        """Every finding that applies to one intent, most severe first.

        The repository's own findings are included: an intent inside a repo whose install needs
        recovery, or whose identity is claimed by a second registry row, IS blocked, and `submit`
        step 4 and the `recovery` card derivation both read this one list (§1.8/§1.12).
        """
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

        others = [r for r in (facts.other_repos_same_identity or ()) if r]
        if others:
            out.append(
                self._finding(
                    "duplicate_identity",
                    {
                        "repo_id": _attr(facts.repo, "repo_id"),
                        "other_repo_ids": sorted(others),
                        "identity": _attr(facts.repo, "resolved_identity"),
                    },
                    (
                        EvidenceRef("studio", f"repo:{_attr(facts.repo, 'repo_id')}",
                                    _attr(facts.repo, "canonical_path")),
                        *(EvidenceRef("studio", f"repo:{other}", "same resolved identity")
                          for other in sorted(others)),
                    ),
                )
            )

        if _attr(facts.repo, "install_status") == "recovery_required":
            out.append(
                self._finding(
                    "install_recovery_required",
                    {
                        "repo_id": _attr(facts.repo, "repo_id"),
                        "engine_dir": _attr(facts.install, "engine_dir") or _attr(facts.repo, "engine_dir"),
                        "receipt_id": _attr(facts.install, "receipt_id"),
                    },
                    (EvidenceRef("studio", f"repo:{_attr(facts.repo, 'repo_id')}", "install_status"),),
                )
            )

        if _attr(facts.install, "status") == "drift":
            out.append(
                self._finding(
                    "receipt_drift",
                    {
                        "drift_count": _attr(facts.install, "drift_count", 0),
                        "installed_engine_version": _attr(facts.install, "engine_version"),
                        "receipt_engine_version": _attr(facts.install, "receipt_engine_version"),
                    },
                    (
                        EvidenceRef("studio", f"receipt:{_attr(facts.install, 'receipt_id')}",
                                    f"engine {_attr(facts.install, 'receipt_engine_version')}"),
                        EvidenceRef("file", _attr(facts.install, "engine_dir") or C.STUDIO_HARNESS_DIR,
                                    f"engine {_attr(facts.install, 'engine_version')}"),
                    ),
                )
            )

        for harness in _attr(facts.install, "harness_dirs", ()) or ():
            if _attr(harness, "engine_version") is None:
                out.append(
                    self._finding(
                        "engine_version_unknown",
                        {"dir": _attr(harness, "dir")},
                        (EvidenceRef("file", f"{_attr(harness, 'dir')}/tools", "no AIDLC_VERSION found"),),
                    )
                )

        if facts.layout == "legacy":
            out.append(
                self._finding(
                    "legacy_layout",
                    {"path": C.LEGACY_STATE_REL},
                    (EvidenceRef("file", C.LEGACY_STATE_REL, "pre-spaces layout"),),
                )
            )

        return self._finalise(out)

    def blocking(self, findings: list[Finding]) -> list[Finding]:
        """The subset that stops Run/Submit.

        Filters on the *emitted* severity, not on the code, so a `state_unreadable` still inside its
        grace window does not block a decision that a moment later will be fine.
        """
        return [f for f in findings if f.severity == "blocking"]

    def unstable(self, snap: "IntentSnapshot") -> bool:
        """True when the snapshot's own reads disagreed (a file changed mid-read).

        A card built on an unstable snapshot may not authorise a decision (FR-ACT-009); the UI renders
        it as ``Refreshing`` instead.
        """
        return not bool(_attr(snap, "stable", True))

    # ---- cursor and registry ------------------------------------------ #

    def _cursor_and_registry(self, snap: "IntentSnapshot") -> list[Finding]:
        out: list[Finding] = []
        space = _attr(snap, "space") or "default"
        intents_dir = _INTENTS_DIR_TEMPLATE.format(space=space)

        if _attr(snap, "dangling_cursor", False):
            # The engine's own resolution order is "explicit arg > active-intent > lone intent"
            # (05 §8): a cursor that names a record without a state file makes it fall through
            # silently to a DIFFERENT record, so which intent a decision applies to is unknowable.
            out.append(
                self._finding(
                    "dangling_cursor",
                    {"space": space, "intent_dir": _attr(snap, "intent_dir")},
                    (
                        EvidenceRef("file", f"{intents_dir}/{_CURSOR_FILENAME}",
                                    "names a record with no aidlc-state.md"),
                        EvidenceRef("file", intents_dir, "record directories"),
                    ),
                )
            )

        if _attr(snap, "row", "__missing__") is None:
            out.append(
                self._finding(
                    "intent_registry_missing_row",
                    {"intent_dir": _attr(snap, "intent_dir"), "space": space},
                    (
                        EvidenceRef("file", f"{intents_dir}/{_REGISTRY_FILENAME}", "no row for dirName"),
                        EvidenceRef("file", self._state_rel(snap), "record exists on disk"),
                    ),
                )
            )
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
            # Nothing else about the state file can be asserted, and asserting it anyway would emit a
            # pile of derived findings from one root cause.
            return [self._state_unreadable(snap, ctx, reason, state_rel)]

        out: list[Finding] = []
        rows: list["StageRow"] = list(_attr(state, "stages", ()) or ())
        by_slug = {_attr(r, "slug"): r for r in rows}
        counts: Mapping[str, int] = _attr(state, "counts", {}) or {}

        out += self._phase_findings(state, by_slug, state_rel)

        claimed_done = _attr(state, "completed_claimed")
        actual_done = counts.get("completed")
        if claimed_done is not None and actual_done is not None and int(claimed_done) != int(actual_done):
            out.append(
                self._finding(
                    "completed_count_mismatch",
                    {"claimed": int(claimed_done), "actual": int(actual_done)},
                    (
                        EvidenceRef("file", state_rel, f"Completed: {claimed_done}"),
                        EvidenceRef("file", state_rel, f"{actual_done} rows marked [x]"),
                    ),
                )
            )

        claimed_total = _attr(state, "total_stages_claimed")
        actual_total = sum(1 for r in rows if (_attr(r, "suffix") or "").strip().upper().startswith("EXECUTE"))
        if claimed_total is not None and rows and int(claimed_total) != actual_total:
            out.append(
                self._finding(
                    "total_count_mismatch",
                    {"claimed": int(claimed_total), "actual": actual_total},
                    (
                        EvidenceRef("file", state_rel, f"Total Stages: {claimed_total}"),
                        EvidenceRef("file", state_rel, f"{actual_total} rows suffixed EXECUTE"),
                    ),
                )
            )

        out += self._graph_drift(snap, rows)
        out += self._state_version(snap, ctx, state, state_rel)
        out += self._artifact_findings(snap, state, by_slug)
        return out

    def _phase_findings(
        self, state: "StateFile", by_slug: Mapping[str, "StageRow"], state_rel: str
    ) -> list[Finding]:
        """`Lifecycle Phase` versus `Phase Progress` and versus the current stage's own phase.

        Both clauses of §1.7 are reported when both hold: they are different contradictions about the
        same field (which phase is running, and whether the stage being executed belongs to it), and
        collapsing them would hide one.
        """
        lifecycle = (_attr(state, "lifecycle_phase") or "").strip()
        if not lifecycle:
            return []
        out: list[Finding] = []
        phases = {str(name).strip().lower(): str(status).strip() for name, status in
                  (_attr(state, "phases", ()) or ())}
        status = phases.get(lifecycle.lower())
        if status is None or status.lower() in _INACTIVE_PHASE_STATUSES:
            out.append(
                self._finding(
                    "phase_stage_disagreement",
                    {"reason": "phase_not_active", "phase": lifecycle, "phase_status": status},
                    (
                        EvidenceRef("file", state_rel, f"Lifecycle Phase: {lifecycle}"),
                        EvidenceRef("file", state_rel,
                                    f"Phase Progress: {lifecycle} = {status if status is not None else 'absent'}"),
                    ),
                )
            )

        current = (_attr(state, "current_stage") or "").strip()
        if current:
            row = by_slug.get(current)
            row_phase = (_attr(row, "phase") if row is not None else None) or None
            if row is None:
                out.append(
                    self._finding(
                        "phase_stage_disagreement",
                        {"reason": "stage_row_absent", "phase": lifecycle, "stage": current,
                         "stage_phase": None},
                        (
                            EvidenceRef("file", state_rel, f"Current Stage: {current}"),
                            EvidenceRef("file", state_rel, "no Stage Progress row with that slug"),
                        ),
                    )
                )
            elif row_phase and row_phase.strip().lower() != lifecycle.lower():
                out.append(
                    self._finding(
                        "phase_stage_disagreement",
                        {"reason": "stage_not_in_phase", "phase": lifecycle, "stage": current,
                         "stage_phase": row_phase},
                        (
                            EvidenceRef("file", state_rel, f"Lifecycle Phase: {lifecycle}"),
                            EvidenceRef("file", state_rel, f"{current} sits under {row_phase}"),
                        ),
                    )
                )
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
        return [
            self._finding(
                "stage_graph_drift",
                {"only_in_state": only_state, "only_in_graph": only_graph,
                 "state_rows": len(state_slugs), "graph_stages": len(graph_slugs)},
                (
                    EvidenceRef("file", self._state_rel(snap), f"{len(state_slugs)} stage rows"),
                    EvidenceRef("file", self._graph_rel(snap), f"{len(graph_slugs)} graph stages"),
                ),
            )
        ]

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
        return [
            self._finding(
                "state_version_unsupported",
                {"reason": reason, "claimed": claimed, "engine_state_version": engine_version,
                 "supported": list(C.SUPPORTED_STATE_VERSIONS)},
                (
                    EvidenceRef("file", state_rel, f"{C.STATE_VERSION_FIELD}: {claimed}"),
                    EvidenceRef("file", f"{engine_dir}/tools", f"engine writes v{engine_version}"),
                ),
            )
        ]

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
            (
                EvidenceRef("file", state_rel, reason),
                EvidenceRef("studio", f"intent:{_attr(snap, 'intent_key')}",
                            f"first unreadable at {since}" if since else "first observation"),
            ),
            severity="blocking" if blocked else "info",
        )

    # ---- directive ---------------------------------------------------- #

    def _directive_findings(self, snap: "IntentSnapshot") -> list[Finding]:
        directive = _attr(snap, "directive")
        if directive is None or _attr(directive, "matches_state", True):
            return []
        actual = _attr(_attr(snap, "state_snap"), "sha256")
        return [
            self._finding(
                "directive_state_digest_mismatch",
                {"stage": _attr(directive, "stage"), "unit": _attr(directive, "unit"),
                 "directive_state_sha256": _attr(directive, "state_sha256"), "state_sha256": actual},
                (
                    EvidenceRef("file", self._record_rel(snap) + "/" + _DIRECTIVE_FILENAME,
                                f"state_sha256 {_short(_attr(directive, 'state_sha256'))}"),
                    EvidenceRef("file", self._state_rel(snap), f"sha256 {_short(actual)}"),
                ),
            )
        ]

    # ---- which unit is executing -------------------------------------- #

    def _unit_findings(self, snap: "IntentSnapshot") -> list[Finding]:
        """A per-unit Construction stage whose executing unit neither source names.

        Two sources can say which unit a per-unit stage is running: the ``unit`` key of
        ``.aidlc-active-directive.json``, and the ``Active Unit`` field the engine mirrors into the state
        file. When neither carries it and Construction holds several unit directories, the identity of
        the work at this boundary is unknowable: the reader refuses to pick one, so the card has no
        questions on it and, without this finding, reads as a Studio bug rather than as something the
        human has to resolve. Blocking for the same reason as ``dangling_cursor`` — the decision would
        land on a different piece of work than the one it was written for.

        Not reported when the reader found exactly one unit's questions file: the unit is then recorded
        in that file's own path rather than guessed, which is what the missing field would have told us.
        """
        stage = (_attr(snap, "stage") or "").strip()
        if stage not in C.PER_UNIT_STAGES or _attr(snap, "unit"):
            return []
        units = sorted(unit for unit in (_attr(snap, "units", ()) or ()) if unit)
        if len(units) < 2:
            return []
        if _unit_of_path(_attr(_attr(snap, "questions"), "relpath"), units):
            return []
        record = self._record_rel(snap)
        return [
            self._finding(
                "unit_ambiguous",
                {
                    "stage": stage,
                    "reason": "no_source_names_the_unit",
                    "directive_unit": _attr(_attr(snap, "directive"), "unit"),
                    "state_unit": _attr(_attr(snap, "state"), "active_unit"),
                    "units": units,
                },
                (
                    EvidenceRef("file", self._state_rel(snap), f"no {_ACTIVE_UNIT_FIELD} field"),
                    EvidenceRef("file", f"{record}/{_DIRECTIVE_FILENAME}", "no unit key"),
                    EvidenceRef("file", f"{record}/{_CONSTRUCTION_DIRNAME}",
                                f"{len(units)} unit directories: {', '.join(units)}"),
                ),
            )
        ]

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
            out.append(
                self._finding(
                    "gate_without_audit_row",
                    {
                        "stage": slug,
                        "reason": "absent" if awaiting is None else "superseded",
                        "awaiting_at": _attr(awaiting, "timestamp"),
                        "superseded_by": _attr(newer, "event"),
                        "superseded_at": _attr(newer, "timestamp"),
                        "audit_complete": complete,
                    },
                    (
                        EvidenceRef("file", state_rel, f"row [{_attr(row, 'mark')}] {slug}"),
                        *(() if awaiting is None else (_audit_ref(awaiting),)),
                        *(() if newer is None else (_audit_ref(newer),)),
                        *(() if complete else (EvidenceRef("audit", self._audit_rel(snap),
                                                           "shard tail-read; history is partial"),)),
                    ),
                )
            )

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
                out.append(
                    self._finding(
                        "audit_gate_without_checkbox",
                        {"stage": current, "mark": _attr(row, "mark"), "row_state": _attr(row, "state"),
                         "awaiting_at": _attr(latest_gate, "timestamp")},
                        (
                            _audit_ref(latest_gate),
                            EvidenceRef("file", state_rel, f"row [{_attr(row, 'mark')}] {current}"),
                        ),
                    )
                )

        if not complete:
            out.append(
                self._finding(
                    "audit_incomplete",
                    {"shards": [_attr(s, "relpath") for s in (_attr(bundle, "shards", ()) or ())],
                     "truncated": [_attr(s, "relpath") for s in (_attr(bundle, "shards", ()) or ())
                                   if _attr(s, "truncated", False)]},
                    (EvidenceRef("audit", self._audit_rel(snap), "tail-read"),),
                )
            )
        return out

    def _artifact_findings(
        self, snap: "IntentSnapshot", state: "StateFile", by_slug: Mapping[str, "StageRow"]
    ) -> list[Finding]:
        """Declared, non-optional, kind-unrestricted `produces` files missing at an open gate.

        Artifacts listed in ``produces_kinds`` are skipped: they apply only to units of a given kind
        (service / ui / library / spec / packaging) and Studio cannot read a unit's kind from the state
        file, so requiring them would invent a blocking finding for a stage that legitimately never
        produced them.
        """
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
            out.append(
                self._finding(
                    "missing_required_artifact",
                    {"stage": current, "artifact": name, "stage_dir": stage_dir,
                     "present": sorted(_attr(m, "name") or "" for m in artifacts)},
                    (
                        EvidenceRef("file", self._graph_rel(snap), f"{current} produces {name}"),
                        EvidenceRef("file", stage_dir, f"no regular file for {expected_name} in this directory"),
                    ),
                )
            )
        return out

    # ---- Studio rows -------------------------------------------------- #

    def _action_findings(self, snap: "IntentSnapshot", ctx: RepoFacts) -> list[Finding]:
        out: list[Finding] = []
        for rec in _actions_for_intent(ctx.live_actions, snap):
            status = _attr(rec, "status")
            action_id = _attr(rec, "action_id")
            if status in _UNCERTAIN_ACTION_STATUSES:
                out.append(
                    self._finding(
                        "delivery_uncertain_action",
                        {"action_id": action_id, "status": status, "type": _attr(rec, "type"),
                         "stage": _attr(rec, "stage")},
                        (EvidenceRef("studio", f"action:{action_id}", status),),
                    )
                )
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
            out.append(
                self._finding(code, params, (EvidenceRef("studio", f"action:{action_id}", code),))
            )

        readback = _attr(rec, "cursor_readback")
        if readback is not None and not _attr(readback, "ok", True) and "cursor_mismatch" not in seen:
            out.append(
                self._finding(
                    "cursor_mismatch",
                    {"action_id": action_id, "status": status,
                     "mismatch": list(_attr(readback, "mismatch", ()) or ()),
                     "space": _attr(readback, "space"), "dir_name": _attr(readback, "dir_name")},
                    (EvidenceRef("studio", f"action:{action_id}", "cursor read-back refused"),),
                )
            )
        return out

    def _lease_findings(self, snap: "IntentSnapshot", ctx: RepoFacts) -> list[Finding]:
        """An execution lease owned by another intent, or any admin lease, blocks this intent.

        Two turns inside one repository would interleave writes to the same engine files, and an admin
        operation (cursor switch, install, recompose) moves the very files a decision was captured
        against — so both are contradictions between "what Studio is about to do" and "what Studio is
        already doing" (FR-SES-007).
        """
        uuid = _attr(_attr(snap, "row"), "uuid")
        out: list[Finding] = []
        for lease in ctx.leases or ():
            if not _lease_matches_repo(lease, ctx.repo):
                continue
            kind = _attr(lease, "kind")
            if kind == "execution" and _attr(lease, "intent_uuid") == uuid:
                continue
            out.append(
                self._finding(
                    "lease_conflict",
                    {
                        "kind": kind,
                        "reason": "admin_lease" if kind != "execution" else "other_intent",
                        "intent_uuid": _attr(lease, "intent_uuid"),
                        "operation_type": _attr(lease, "operation_type"),
                        "action_id": _attr(lease, "action_id"),
                        "acquired_at": _attr(lease, "acquired_at"),
                        "heartbeat_at": _attr(lease, "heartbeat_at"),
                    },
                    (
                        EvidenceRef(
                            "studio",
                            f"lease:{kind}:{_attr(lease, 'resolved_repo_identity')}",
                            f"generation {_attr(lease, 'generation')}",
                        ),
                        EvidenceRef("studio", f"intent:{_attr(snap, 'intent_key')}", f"uuid {uuid}"),
                    ),
                )
            )
        return out

    def _session_findings(self, ctx: RepoFacts) -> list[Finding]:
        binding = ctx.binding
        interrupted_at = _attr(binding, "interrupted_at") if binding is not None else None
        if not interrupted_at:
            return []
        slot = ctx.slot
        if slot is not None and _slot_busy_stopping(slot):
            # The force stop is still in flight: PRD §11.2 keeps the intent in `Interrupted` until the
            # slot is idle, and only then does manual takeover become mandatory (review P17).
            return []
        return [
            self._finding(
                "interrupted_session",
                {"interrupted_at": interrupted_at, "slot_key": _attr(binding, "slot_key"),
                 "slot_present": slot is not None},
                (
                    EvidenceRef("studio", f"binding:{_attr(binding, 'intent_key')}",
                                f"interrupted_at {interrupted_at}"),
                    EvidenceRef("host", f"slot:{_attr(binding, 'slot_key')}",
                                "absent" if slot is None else "idle"),
                ),
            )
        ]

    def _snapshot_quality(self, snap: "IntentSnapshot") -> list[Finding]:
        if not self.unstable(snap):
            return []
        return [
            self._finding(
                "unstable_read",
                {"intent_key": _attr(snap, "intent_key"), "taken_at": _attr(snap, "taken_at")},
                (EvidenceRef("file", self._state_rel(snap), "changed while being read"),),
            )
        ]

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
        """De-duplicate identical findings and order them blocking → warn → info.

        Duplicates are possible because repository findings are folded into every intent and because
        two code paths can notice one contradiction; the identity of a finding is its code plus its
        params (two missing artifacts are two findings, the same missing artifact twice is one).
        """
        seen: set[str] = set()
        unique: list[Finding] = []
        for finding in findings:
            key = finding.code + "|" + json.dumps(finding.params, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            unique.append(finding)
        return sorted(unique, key=lambda f: (_SEVERITY_RANK.get(f.severity, 9), f.code))

    # ---- relpath helpers ---------------------------------------------- #

    @staticmethod
    def _record_rel(snap: "IntentSnapshot") -> str:
        space = _attr(snap, "space") or "default"
        return f"{_INTENTS_DIR_TEMPLATE.format(space=space)}/{_attr(snap, 'intent_dir')}"

    def _state_rel(self, snap: "IntentSnapshot") -> str:
        return _attr(_attr(snap, "state_snap"), "relpath") or \
            f"{self._record_rel(snap)}/{_STATE_FILENAME}"

    def _audit_rel(self, snap: "IntentSnapshot") -> str:
        shards = _attr(_attr(snap, "audit"), "shards", ()) or ()
        return _attr(shards[0], "relpath") if shards else f"{self._record_rel(snap)}/audit"

    @staticmethod
    def _graph_rel(snap: "IntentSnapshot") -> str:
        engine_dir = _attr(_attr(snap, "engine"), "dir") or C.STUDIO_HARNESS_DIR
        return f"{engine_dir}/{_STAGE_GRAPH_REL}"

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


# --------------------------------------------------------------------------- #
# module-private functions
# --------------------------------------------------------------------------- #


#: The engine is handed objects built by four other modules and must import none of them; every read of
#: one goes through the shared structural accessor (``constants.attr``).
_attr = C.attr


def _event_key(event: Any) -> tuple[str, int, int]:
    """The merge order of §1.6.2: (timestamp, shard_index, pos).

    Same-second events in different shards are causally unordered (04 §5.1); using the same key the
    reader sorts by means Consistency never claims an order the merge did not already establish.
    """
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
    """The unit segment of a record-relative path, or None when the path names no unit.

    ``construction/<unit>/<stage>/…`` and ``construction/<stage>/…`` both occur in one real record
    (FORMAT-NOTES §8), so the segment after ``construction`` is a unit exactly when it is one of the
    unit directories the reader enumerated. Matching against that list rather than against a stage-slug
    denylist keeps this module out of the business of knowing the engine's stage names.
    """
    parts = (relpath or "").split("/")
    for index, part in enumerate(parts[:-1]):
        if part == _CONSTRUCTION_DIRNAME and parts[index + 1] in set(units):
            return parts[index + 1]
    return None


def _actions_for_intent(actions: Sequence[Any], snap: "IntentSnapshot") -> list[Any]:
    """The rows that belong to this intent.

    ``RepoFacts.live_actions`` may be the whole repository's live set, so filtering here (rather than
    trusting the caller) prevents one intent's uncertain delivery from blocking a sibling intent.
    """
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
    """Finding codes the reconciler recorded on an action, in every shape it may have written.

    Accepted: ``evidence["findings"] = ["cursor_moved", {"code": "wrong_slot", "params": {...}}]`` and
    ``evidence["finding"] = "human_presence_anomaly"`` (or the same as a dict). Anything else is
    ignored rather than raising: this is a defensive read of another module's blob.
    """
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


#: Timestamp parsing only — the engine never reads a clock; "now" always comes from ``RepoFacts.now``
#: so a scan is reproducible from its inputs alone. Shared with the rest of the backend
#: (``constants.epoch_from_iso``) so all four callers agree about DST.
_epoch = C.epoch_from_iso


def _short(digest: str | None) -> str:
    return (digest or "")[:12] or "none"


def _jsonable(value: Any) -> Any:
    """Params must survive ``json.dumps``; tuples and sets become lists, everything else is left."""
    if isinstance(value, tuple) or isinstance(value, set) or isinstance(value, frozenset):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value
