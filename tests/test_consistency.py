"""One test per finding code, positive and negative, built from the real harvested fixtures.

Every input here is either a byte-faithful fixture file or a variant derived from one with the
``fixtures.*`` helpers (flip a checkbox, patch a field, append an audit block) — the same edits AI-DLC
itself makes. Three of the table's codes have a real positive on disk and are used as such:

* ``phase_stage_disagreement`` — the real 2.1.1 record says ``Lifecycle Phase: IDEATION`` while its
  ``Phase Progress`` row for Ideation is ``Pending`` (FORMAT-NOTES §3).
* ``directive_state_digest_mismatch`` — the DevDelta poc's ``.aidlc-active-directive.json`` holds a
  stale digest; the remediation record's matches.
* ``stage_graph_drift`` — a State Version 7 record (32 rows) read against the bundled 33-stage graph.

``IntentSnapshot`` (§1.8) and the §1.6 reader dataclasses are assembled here as small frozen views with
the contract's field names, because ``consistency.py`` is duck-typed against them on purpose (it must
not import the module that opens files). The real ``stage-graph.json`` is fed in as its own JSON dicts,
so the ``produces`` / ``optional_produces`` / ``produces_kinds`` rules are exercised against real data.
Two tests at the end of the file close the loop on that, so a divergence between these views and the
shipped dataclasses cannot hide: ``test_reader_state_parse_agrees_with_the_test_views`` compares the
local state parse with ``AidlcReader.parse_state_file``, and
``test_projection_snapshot_is_accepted_by_the_engine`` runs one scenario through the real
``Projection.snapshot`` and asserts it yields the same findings as the views do.
"""

from __future__ import annotations

import builtins
import json
from dataclasses import dataclass, field, replace
from typing import Any, Sequence

import fixtures as F
import pytest

# --------------------------------------------------------------------------- #
# views of the §1.6 / §1.8 dataclasses (field names are the contract's)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SnapshotView:
    relpath: str
    sha256: str
    size: int = 0
    mtime_ns: int = 0
    unstable: bool = False


@dataclass(frozen=True)
class StageRowView:
    slug: str
    mark: str
    state: str
    phase: str | None
    suffix: str | None


@dataclass(frozen=True)
class StateView:
    sections: dict
    phases: list
    stages: list
    counts: dict
    state_version: int | None
    current_stage: str | None
    next_stage: str | None
    status: str | None
    lifecycle_phase: str | None
    revision_count: int
    completed_claimed: int | None
    total_stages_claimed: int | None
    header_only: bool


@dataclass(frozen=True)
class IntentRowView:
    uuid: str
    slug: str
    dir_name: str
    scope: str
    repos: tuple = ()
    status: str = "in-flight"


@dataclass(frozen=True)
class DirectiveView:
    version: int
    stage: str
    unit: str | None
    state_sha256: str
    matches_state: bool


@dataclass(frozen=True)
class AuditEventView:
    shard: str
    shard_index: int
    pos: int
    timestamp: str
    event: str
    fields: dict
    raw: str = ""


@dataclass(frozen=True)
class ShardMetaView:
    relpath: str
    size: int = 0
    mtime_ns: int = 0
    truncated: bool = False


@dataclass(frozen=True)
class AuditBundleView:
    events: tuple = ()
    shards: tuple = ()
    complete: bool = True


@dataclass(frozen=True)
class ArtifactMetaView:
    relpath: str
    name: str
    sha256: str | None = None


@dataclass(frozen=True)
class BindingViewStub:
    intent_key: str = "260901-ledger-export"
    slot_key: str | None = "aidlc-studio-r_000000000001-260901-ledger-export"
    interrupted_at: str | None = None
    paused: bool = False
    archive_state: str = "active"


@dataclass(frozen=True)
class SlotViewStub:
    key: str = "aidlc-studio-r_000000000001-260901-ledger-export"
    running: bool = False
    stopping: bool = False
    stop_state: str = "idle"


@dataclass(frozen=True)
class CursorReadbackView:
    ok: bool
    space: str = "default"
    dir_name: str = "260901-ledger-export"
    uuid: str | None = None
    state_sha256: str | None = None
    mismatch: tuple = ()


@dataclass(frozen=True)
class ActionView:
    action_id: str = "a_0000000000000001"
    type: str = "gate"
    status: str = "Queued"
    space: str = "default"
    intent_dir: str = "260901-ledger-export"
    intent_key: str = "260901-ledger-export"
    stage: str | None = "requirements-analysis"
    evidence: dict = field(default_factory=dict)
    cursor_readback: CursorReadbackView | None = None


@dataclass(frozen=True)
class SnapView:
    """The §1.8 ``IntentSnapshot`` fields ``ConsistencyEngine`` is documented to read."""

    repo_id: str = "r_000000000001"
    space: str = "default"
    intent_dir: str = "260901-ledger-export"
    intent_key: str = "260901-ledger-export"
    row: IntentRowView | None = None
    state_snap: SnapshotView | None = None
    state: StateView | None = None
    directive: DirectiveView | None = None
    audit: AuditBundleView = AuditBundleView()
    markers: Any = None
    questions: Any = None
    stage_artifacts: tuple = ()
    review: Any = None
    graph: tuple = ()
    grid: dict = field(default_factory=dict)
    scopes: tuple = ()
    engine: Any = None
    taken_at: str = "2026-09-04T10:00:00Z"
    stable: bool = True
    is_active: bool = True
    dangling_cursor: bool = False
    first_unreadable_at: str | None = None


# --------------------------------------------------------------------------- #
# parsing real fixture bytes into those views (grammar comes from constants.py)
# --------------------------------------------------------------------------- #


def parse_state(text: str, constants: Any) -> StateView:
    """The subset of ``parse_state_file`` the findings depend on, using the shipped grammar.

    Only the constants' own regexes are used, so a grammar fix in ``constants.py`` reaches these tests
    too; the fields carry the §1.6 names so the views are drop-in for the real dataclass.
    """
    header_only = text.strip() == "# AI-DLC State Tracking"
    sections: dict[str, dict[str, str]] = {}
    phases: list[tuple[str, str]] = []
    stages: list[StageRowView] = []
    section: str | None = None
    phase: str | None = None

    for line in text.splitlines():
        heading = constants.SECTION_RE.match(line)
        if heading:
            section = heading.group(1)
            sections.setdefault(section, {})
            phase = None
            continue
        sub = constants.SUBSECTION_RE.match(line)
        if sub:
            phase_header = constants.PHASE_HEADER_RE.match(sub.group(1))
            phase = phase_header.group(1) if phase_header else sub.group(1)
            continue
        if section == "Stage Progress":
            row = constants.CHECKBOX_RE.match(line)
            if row:
                mark, slug, suffix = row.group(1), row.group(2), row.group(3)
                stages.append(
                    StageRowView(
                        slug=slug,
                        mark=mark,
                        state=constants.CHECKBOX_MAP.get(mark.lower(), "unknown"),
                        phase=phase,
                        suffix=(suffix or None),
                    )
                )
                continue
        pair = constants.FIELD_RE.match(line)
        if pair and section:
            sections[section][pair.group(1)] = pair.group(2)
            if section == "Phase Progress":
                phases.append((pair.group(1), pair.group(2)))

    flat = {key: value for rows in sections.values() for key, value in rows.items()}
    counts = {"total": len(stages)}
    for state_name in constants.STAGE_STATE:
        counts[state_name] = sum(1 for row in stages if row.state == state_name)
    counts["done"] = sum(counts[name] for name in constants.DONE_STAGE_STATES)

    return StateView(
        sections=sections,
        phases=phases,
        stages=stages,
        counts=counts,
        state_version=_int(flat.get(constants.STATE_VERSION_FIELD)),
        current_stage=flat.get("Current Stage") or None,
        next_stage=flat.get("Next Stage") or None,
        status=flat.get("Status") or None,
        lifecycle_phase=flat.get("Lifecycle Phase") or None,
        revision_count=_int(flat.get("Revision Count")) or 0,
        completed_claimed=_int(flat.get("Completed")),
        total_stages_claimed=_int(flat.get("Total Stages")),
        header_only=header_only,
    )


def parse_audit(text: str, constants: Any, *, shard: str, shard_index: int = 0) -> list[AuditEventView]:
    events: list[AuditEventView] = []
    for pos, block in enumerate(text.replace("\r\n", "\n").split(constants.AUDIT_BLOCK_SEPARATOR)):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            pair = constants.AUDIT_FIELD_RE.match(line)
            if pair:
                fields[pair.group(1)] = pair.group(2)
        event = fields.pop("Event", None)
        timestamp = fields.pop("Timestamp", "")
        if not event:
            continue
        events.append(
            AuditEventView(
                shard=shard,
                shard_index=shard_index,
                pos=pos,
                timestamp=timestamp,
                event=event,
                fields=fields,
                raw=block,
            )
        )
    return events


def _int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _codes(findings: Sequence[Any]) -> list[str]:
    return [f.code for f in findings]


def _one(findings: Sequence[Any], code: str) -> Any:
    matches = [f for f in findings if f.code == code]
    assert matches, f"expected a {code} finding, got {_codes(findings)}"
    return matches[0]


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def cons(studio):
    module = getattr(studio, "consistency", None)
    assert module is not None, getattr(studio, "consistency__error", "consistency.py did not import")
    return module


@pytest.fixture
def C(studio):
    return studio.constants


@pytest.fixture
def engine(cons):
    return cons.ConsistencyEngine()


@pytest.fixture
def graph():
    """The real bundled 33-stage graph as its own JSON dicts."""
    return tuple(F.stage_graph())


@pytest.fixture
def harness(studio):
    def make(*, engine_version: str | None = "2.6.2", engine_state_version: int | None = 8,
             directory: str = ".kiro"):
        return studio.repo_registry.HarnessDir(
            dir=directory,
            harness_name="kiro",
            rules_subdir=None,
            engine_version=engine_version,
            engine_state_version=engine_state_version,
            stage_count=33,
            has_utility=True,
        )

    return make


@pytest.fixture
def make_facts(studio, clock, harness):
    """A ``RepoFacts`` over a REAL ``RepoRecord``/``InstallHealth`` (registry drift breaks a test)."""

    def make(**over: Any):
        registry = studio.repo_registry
        repo = registry.RepoRecord(
            repo_id=over.pop("repo_id", "r_000000000001"),
            label="fixture",
            canonical_path="/FIXTURE/repo",
            resolved_identity=over.pop("resolved_identity", "16777220:99001"),
            git_common_dir_identity=None,
            platform="darwin",
            added_at="2026-09-01T00:00:00Z",
            last_seen=clock.iso(),
            availability="available",
            availability_detail=None,
            archived=False,
            legacy_console_id=None,
            install_status=over.pop("install_status", "installed"),
            installed_engine_version="2.6.2",
            engine_dir=".kiro",
            receipt_version="1",
        )
        install = registry.InstallHealth(
            status=over.pop("install_health", "installed"),
            engine_dir=".kiro",
            engine_version="2.6.2",
            engine_state_version=over.pop("engine_state_version", 8),
            stage_count=33,
            receipt_id="rc_0000000000000001",
            receipt_engine_version=over.pop("receipt_engine_version", "2.6.2"),
            drift_count=over.pop("drift_count", 0),
            harness_dirs=tuple(over.pop("harness_dirs", (harness(),))),
        )
        return studio.consistency.RepoFacts(
            now=over.pop("now", clock.iso()),
            repo=repo,
            install=install,
            **over,
        )

    return make


@pytest.fixture
def make_snap(C, graph, harness):
    """A snapshot over a state file's text; every field can be overridden per test."""

    def make(state_text: str | None = None, *, audit_shards: Sequence[str] = (), **over: Any):
        state = None
        state_snap = None
        if state_text is not None:
            state = parse_state(state_text, C)
            state_snap = SnapshotView(
                relpath=f"aidlc/spaces/default/intents/{over.get('intent_dir', '260901-ledger-export')}"
                        "/aidlc-state.md",
                sha256=F.sha256(state_text),
                size=len(state_text.encode("utf-8")),
            )
        events: list[AuditEventView] = []
        shards: list[ShardMetaView] = []
        for index, text in enumerate(audit_shards):
            relpath = f"audit/fixture-host-0f1e2d3c4b5{index}a.md"
            events += parse_audit(text, C, shard=relpath, shard_index=index)
            shards.append(ShardMetaView(relpath=relpath, size=len(text.encode("utf-8"))))
        events.sort(key=lambda e: (e.timestamp, e.shard_index, e.pos))
        bundle = over.pop(
            "audit",
            AuditBundleView(events=tuple(events), shards=tuple(shards),
                            complete=over.pop("audit_complete", True)),
        )
        defaults: dict[str, Any] = {
            "row": IntentRowView(
                uuid="01a047b1-c605-7f76-927a-8193f101d918",
                slug="ledger-export",
                dir_name=over.get("intent_dir", "260901-ledger-export"),
                scope="feature",
            ),
            "graph": graph,
            "engine": harness(),
        }
        defaults["state"] = state
        defaults["state_snap"] = state_snap
        defaults.update(over)
        return SnapView(audit=bundle, **defaults)

    return make


# real fixture texts -------------------------------------------------------- #


@pytest.fixture
def gate_state() -> str:
    """The real v7 record that is waiting at an open ``requirements-analysis`` gate."""
    return F.real_state(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN)


@pytest.fixture
def gate_audit() -> str:
    """That record's real audit shard (ends STAGE_AWAITING_APPROVAL after a reject/revise cycle)."""
    return F.real_audit(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN)


@pytest.fixture
def poc_state() -> str:
    """The real 2.6.2 / State Version 8 record (33 rows, Status Completed)."""
    return F.real_state()


# --------------------------------------------------------------------------- #
# the table itself
# --------------------------------------------------------------------------- #


def test_severity_table_covers_every_code_exactly_once(cons, C):
    assert set(cons.FINDING_SEVERITIES) == set(cons.FINDING_CODES)
    assert len(cons.FINDING_CODES) == len(set(cons.FINDING_CODES))
    assert set(cons.FINDING_SEVERITIES.values()) <= set(C.FINDING_SEVERITY)


def test_blocking_set_is_the_contract_set(cons):
    assert cons.BLOCKING_FINDING_CODES == frozenset(
        {
            "dangling_cursor",
            "phase_stage_disagreement",
            "gate_without_audit_row",
            "audit_gate_without_checkbox",
            "missing_required_artifact",
            "state_version_unsupported",
            "install_recovery_required",
            "duplicate_identity",
            "delivery_uncertain_action",
            "lease_conflict",
            "state_unreadable",
            "interrupted_session",
            "human_presence_anomaly",
            "cursor_moved",
            "wrong_slot",
            "cursor_mismatch",
            # Additive to the §1.7 list: two sources for "which construction unit is executing" disagree,
            # or neither names one and more than one candidate holds a questions file. Blocking because
            # the alternative was showing one unit's questions and delivering the answer as another's.
            "unit_ambiguous",
        }
    )


def test_non_blocking_codes_are_warn_or_info(cons):
    for code, severity in cons.FINDING_SEVERITIES.items():
        if code in cons.BLOCKING_FINDING_CODES:
            assert severity == "blocking"
        else:
            assert severity in ("warn", "info"), code


def test_severity_agrees_with_the_registry_preflight_table(cons, studio):
    """A code cannot mean ``warn`` in preflight and ``blocking`` in a scan."""
    preflight = studio.repo_registry.PREFLIGHT_WARNING_SEVERITY
    shared = set(preflight) & set(cons.FINDING_SEVERITIES)
    assert shared, "the two tables must overlap on at least legacy_layout"
    for code in shared:
        assert preflight[code] == cons.FINDING_SEVERITIES[code], code


def test_finding_json_shape_and_message_key(engine, make_snap, make_facts):
    findings = engine.evaluate_intent(make_snap(dangling_cursor=True), make_facts())
    finding = _one(findings, "dangling_cursor")
    assert finding.to_json() == {
        "code": "dangling_cursor",
        "severity": "blocking",
        "message_key": "finding.dangling_cursor",
        "params": finding.params,
        "evidence": [ref.to_json() for ref in finding.evidence],
    }
    assert finding.message_key == f"finding.{finding.code}"
    assert json.dumps(finding.to_json())  # params must survive JSON
    assert all(set(ref.to_json()) == {"kind", "ref", "detail"} for ref in finding.evidence)
    assert all(ref.kind in ("file", "audit", "studio", "host") for ref in finding.evidence)


def test_blocking_filters_on_emitted_severity(engine, make_snap, make_facts):
    """`state_unreadable` inside its grace window is reported but does not block."""
    snap = make_snap(None)
    fresh = engine.evaluate_intent(snap, make_facts(first_unreadable_at="2026-09-04T09:59:55Z"))
    assert _one(fresh, "state_unreadable").severity == "info"
    assert "state_unreadable" not in _codes(engine.blocking(fresh))

    stale = engine.evaluate_intent(snap, make_facts(first_unreadable_at="2026-09-04T09:50:00Z"))
    assert _one(stale, "state_unreadable").severity == "blocking"
    assert "state_unreadable" in _codes(engine.blocking(stale))


def test_findings_are_ordered_blocking_warn_info_and_deduped(engine, make_snap, make_facts):
    snap = make_snap(
        F.state_text(F.state_path(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN), fields={"Completed": "20"}),
        dangling_cursor=True,
        stable=False,
        intent_dir=F.PAYLOAD_GATE_OPEN,
    )
    findings = engine.evaluate_intent(snap, make_facts(install_status="recovery_required"))
    ranks = [{"blocking": 0, "warn": 1, "info": 2}[f.severity] for f in findings]
    assert ranks == sorted(ranks)
    keys = [(f.code, json.dumps(f.params, sort_keys=True, default=str)) for f in findings]
    assert len(keys) == len(set(keys))


def test_evaluate_intent_includes_the_repository_findings(engine, make_snap, make_facts, gate_state):
    facts = make_facts(install_status="recovery_required", engine_state_version=7)
    snap = make_snap(
        gate_state,
        intent_dir=F.PAYLOAD_GATE_OPEN,
        audit_shards=(F.real_audit(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN),),
    )
    intent = engine.evaluate_intent(snap, facts)
    assert "install_recovery_required" in _codes(intent)
    assert "install_recovery_required" in _codes(engine.evaluate_repo(facts))


def test_engine_never_opens_a_file(engine, make_snap, make_facts, monkeypatch, gate_state, gate_audit):
    """§1.7: the engine may not read files. Proven by removing its ability to."""
    def refuse(*_a: Any, **_k: Any):
        raise AssertionError("ConsistencyEngine opened a file")

    monkeypatch.setattr(builtins, "open", refuse)
    snap = make_snap(gate_state, intent_dir=F.PAYLOAD_GATE_OPEN, audit_shards=(gate_audit,))
    engine.evaluate_intent(snap, make_facts(engine_state_version=7))


# --------------------------------------------------------------------------- #
# one test per code
# --------------------------------------------------------------------------- #


def test_contradiction_findings_carry_both_readings(engine, make_snap, make_facts, gate_audit):
    """Every finding about two disagreeing sources names both of them (never just the loser).

    That is the whole point of "report, never resolve": the human has to be able to see the state
    file's claim AND the audit's claim without leaving the card.
    """
    restarted = gate_audit + F.audit_block("STAGE_STARTED", "2026-09-03T15:00:00Z",
                                           Stage="requirements-analysis")
    state = F.state_text(
        F.state_path(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN),
        fields={"Completed": "20", "Total Stages": "99"},
    )
    findings = engine.evaluate_intent(
        make_snap(state, intent_dir=F.PAYLOAD_GATE_OPEN, audit_shards=(restarted,),
                  row=None, stage_artifacts=()),
        make_facts(engine_state_version=8, other_repos_same_identity=["r_000000000002"]),
    )
    two_sourced = {
        "intent_registry_missing_row",
        "completed_count_mismatch",
        "total_count_mismatch",
        "stage_graph_drift",
        "gate_without_audit_row",
        "missing_required_artifact",
        "state_version_unsupported",
        "duplicate_identity",
    }
    assert two_sourced <= set(_codes(findings))
    for finding in findings:
        if finding.code in two_sourced:
            assert len(finding.evidence) >= 2, finding.code
            assert all(ref.detail for ref in finding.evidence), finding.code


def test_dangling_cursor(engine, make_snap, make_facts, gate_state):
    facts = make_facts(engine_state_version=7)
    positive = engine.evaluate_intent(make_snap(gate_state, dangling_cursor=True), facts)
    finding = _one(positive, "dangling_cursor")
    assert finding.severity == "blocking"
    assert finding.params["space"] == "default"
    assert any("active-intent" in ref.ref for ref in finding.evidence)

    negative = engine.evaluate_intent(make_snap(gate_state, dangling_cursor=False), facts)
    assert "dangling_cursor" not in _codes(negative)


def test_intent_registry_missing_row(engine, make_snap, make_facts, gate_state):
    facts = make_facts(engine_state_version=7)
    positive = engine.evaluate_intent(make_snap(gate_state, row=None), facts)
    finding = _one(positive, "intent_registry_missing_row")
    assert finding.severity == "warn"
    assert any("intents.json" in ref.ref for ref in finding.evidence)

    assert "intent_registry_missing_row" not in _codes(
        engine.evaluate_intent(make_snap(gate_state), facts)
    )


def test_directive_state_digest_mismatch(engine, make_snap, make_facts):
    """The real poc directive is stale; the real remediation directive matches its state."""
    facts = make_facts()
    stale_state = F.real_state(F.DEVDELTA, F.DEVDELTA_POC)
    stale = _directive_of(F.DEVDELTA, F.DEVDELTA_POC, stale_state)
    assert stale.matches_state is False
    findings = engine.evaluate_intent(
        make_snap(stale_state, intent_dir=F.DEVDELTA_POC, directive=stale), facts
    )
    finding = _one(findings, "directive_state_digest_mismatch")
    assert finding.severity == "info"
    assert finding.params["directive_state_sha256"] != finding.params["state_sha256"]
    assert len(finding.evidence) == 2  # both readings

    fresh_state = F.real_state(F.DEVDELTA, F.DEVDELTA_REMEDIATION)
    fresh = _directive_of(F.DEVDELTA, F.DEVDELTA_REMEDIATION, fresh_state)
    assert fresh.matches_state is True
    assert "directive_state_digest_mismatch" not in _codes(
        engine.evaluate_intent(
            make_snap(fresh_state, intent_dir=F.DEVDELTA_REMEDIATION, directive=fresh), facts
        )
    )


def _directive_of(tree, intent_dir: str, state_text: str) -> DirectiveView:
    record = tree / "aidlc" / "spaces" / "default" / "intents" / intent_dir
    data = json.loads((record / ".aidlc-active-directive.json").read_text("utf-8"))
    return DirectiveView(
        version=int(data.get("version", 1)),
        stage=data["stage"],
        unit=data.get("unit"),
        state_sha256=data["state_sha256"],
        matches_state=data["state_sha256"] == F.sha256(state_text),
    )


def test_phase_stage_disagreement(engine, make_snap, make_facts, gate_state):
    """The real 2.1.1 record is the positive; the real v7 in-flight record is the negative."""
    facts = make_facts(engine_state_version=7)
    older = F.real_state(F.OLDER, F.OLDER_INTENT)
    findings = engine.evaluate_intent(make_snap(older, intent_dir=F.OLDER_INTENT), facts)
    finding = _one(findings, "phase_stage_disagreement")
    assert finding.severity == "blocking"
    assert finding.params == {"reason": "phase_not_active", "phase": "IDEATION", "phase_status": "Pending"}

    assert "phase_stage_disagreement" not in _codes(
        engine.evaluate_intent(make_snap(gate_state, intent_dir=F.PAYLOAD_GATE_OPEN), facts)
    )

    # Both clauses at once: OPERATION is Pending AND the current stage sits under CONSTRUCTION.
    moved = F.state_text(fields={"Lifecycle Phase": "OPERATION"})
    reasons = {
        f.params["reason"]
        for f in engine.evaluate_intent(make_snap(moved, intent_dir=F.DEVDELTA_POC), make_facts())
        if f.code == "phase_stage_disagreement"
    }
    assert reasons == {"phase_not_active", "stage_not_in_phase"}


def test_completed_count_mismatch(engine, make_snap, make_facts, poc_state):
    facts = make_facts()
    claimed_twenty = F.state_text(fields={"Completed": "20"})
    finding = _one(
        engine.evaluate_intent(make_snap(claimed_twenty, intent_dir=F.DEVDELTA_POC), facts),
        "completed_count_mismatch",
    )
    assert finding.severity == "warn"
    assert finding.params == {"claimed": 20, "actual": 8}

    assert "completed_count_mismatch" not in _codes(
        engine.evaluate_intent(make_snap(poc_state, intent_dir=F.DEVDELTA_POC), facts)
    )


def test_total_count_mismatch(engine, make_snap, make_facts, poc_state):
    facts = make_facts()
    # One row re-suffixed to SKIP: 32 EXECUTE rows against a claimed 33.
    skipped = F.state_text(modes={"ci-pipeline": "SKIP"})
    finding = _one(
        engine.evaluate_intent(make_snap(skipped, intent_dir=F.DEVDELTA_POC), facts),
        "total_count_mismatch",
    )
    assert finding.severity == "warn"
    assert finding.params == {"claimed": 33, "actual": 32}

    assert "total_count_mismatch" not in _codes(
        engine.evaluate_intent(make_snap(poc_state, intent_dir=F.DEVDELTA_POC), facts)
    )


def test_stage_graph_drift(engine, make_snap, make_facts, gate_state, poc_state):
    """A v7 record's 32 rows against the bundled 33-stage graph — a real mixed-version pair."""
    findings = engine.evaluate_intent(
        make_snap(gate_state, intent_dir=F.PAYLOAD_GATE_OPEN), make_facts(engine_state_version=7)
    )
    finding = _one(findings, "stage_graph_drift")
    assert finding.severity == "warn"
    assert finding.params["only_in_state"] == ["application-design"]
    assert finding.params["only_in_graph"] == ["contract-design", "domain-design"]

    assert "stage_graph_drift" not in _codes(
        engine.evaluate_intent(make_snap(poc_state, intent_dir=F.DEVDELTA_POC), make_facts())
    )


def test_stage_graph_drift_needs_two_sources(engine, make_snap, make_facts, gate_state):
    """No graph means one source, and one source cannot disagree with itself."""
    findings = engine.evaluate_intent(
        make_snap(gate_state, intent_dir=F.PAYLOAD_GATE_OPEN, graph=()),
        make_facts(engine_state_version=7),
    )
    assert "stage_graph_drift" not in _codes(findings)


def test_gate_without_audit_row(engine, make_snap, make_facts, gate_state, gate_audit):
    facts = make_facts(engine_state_version=7)
    # Negative: the real shard's latest STAGE_AWAITING_APPROVAL is the newest gate row for the stage.
    negative = engine.evaluate_intent(
        make_snap(gate_state, intent_dir=F.PAYLOAD_GATE_OPEN, audit_shards=(gate_audit,)), facts
    )
    assert "gate_without_audit_row" not in _codes(negative)

    # Positive (superseded): a later STAGE_STARTED means the gate row belongs to an earlier attempt.
    restarted = gate_audit + F.audit_block(
        "STAGE_STARTED", "2026-09-03T15:00:00Z", Stage="requirements-analysis"
    )
    finding = _one(
        engine.evaluate_intent(
            make_snap(gate_state, intent_dir=F.PAYLOAD_GATE_OPEN, audit_shards=(restarted,)), facts
        ),
        "gate_without_audit_row",
    )
    assert finding.severity == "blocking"
    assert finding.params["reason"] == "superseded"
    assert finding.params["stage"] == "requirements-analysis"
    assert finding.params["superseded_by"] == "STAGE_STARTED"
    assert {ref.kind for ref in finding.evidence} == {"file", "audit"}

    # Positive (absent): a shard that never recorded the gate at all.
    only_started = F.audit_block("STAGE_STARTED", "2026-09-03T14:00:00Z", Stage="requirements-analysis")
    absent = _one(
        engine.evaluate_intent(
            make_snap(gate_state, intent_dir=F.PAYLOAD_GATE_OPEN, audit_shards=(only_started,)), facts
        ),
        "gate_without_audit_row",
    )
    assert absent.params["reason"] == "absent"
    assert absent.params["awaiting_at"] is None


def test_gate_without_audit_row_tolerates_same_second_ordering(engine, make_snap, make_facts,
                                                               gate_state):
    """STAGE_STARTED and STAGE_AWAITING_APPROVAL in one second are ordered by position, not by clock."""
    same_second = (
        F.audit_block("STAGE_STARTED", "2026-09-03T14:00:00Z", Stage="requirements-analysis")
        + F.audit_block("STAGE_AWAITING_APPROVAL", "2026-09-03T14:00:00Z", Stage="requirements-analysis")
    )
    findings = engine.evaluate_intent(
        make_snap(gate_state, intent_dir=F.PAYLOAD_GATE_OPEN, audit_shards=(same_second,)),
        make_facts(engine_state_version=7),
    )
    assert "gate_without_audit_row" not in _codes(findings)


def test_audit_gate_without_checkbox(engine, make_snap, make_facts, gate_state, gate_audit):
    facts = make_facts(engine_state_version=7)
    in_progress = F.state_text(
        F.state_path(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN), marks={"requirements-analysis": "-"}
    )
    finding = _one(
        engine.evaluate_intent(
            make_snap(in_progress, intent_dir=F.PAYLOAD_GATE_OPEN, audit_shards=(gate_audit,)), facts
        ),
        "audit_gate_without_checkbox",
    )
    assert finding.severity == "blocking"
    assert finding.params["stage"] == "requirements-analysis"
    assert finding.params["row_state"] == "in_progress"

    assert "audit_gate_without_checkbox" not in _codes(
        engine.evaluate_intent(
            make_snap(gate_state, intent_dir=F.PAYLOAD_GATE_OPEN, audit_shards=(gate_audit,)), facts
        )
    )


def test_missing_required_artifact(engine, make_snap, make_facts, gate_state, gate_audit):
    facts = make_facts(engine_state_version=7)
    stage_dir = f"aidlc/spaces/default/intents/{F.PAYLOAD_GATE_OPEN}/inception/requirements-analysis"
    both = (
        ArtifactMetaView(relpath=f"{stage_dir}/requirements.md", name="requirements.md", sha256="a" * 64),
        ArtifactMetaView(relpath=f"{stage_dir}/requirements-analysis-questions.md",
                         name="requirements-analysis-questions.md", sha256="b" * 64),
    )
    assert "missing_required_artifact" not in _codes(
        engine.evaluate_intent(
            make_snap(gate_state, intent_dir=F.PAYLOAD_GATE_OPEN, audit_shards=(gate_audit,),
                      stage_artifacts=both),
            facts,
        )
    )

    finding = _one(
        engine.evaluate_intent(
            make_snap(gate_state, intent_dir=F.PAYLOAD_GATE_OPEN, audit_shards=(gate_audit,),
                      stage_artifacts=both[:1]),
            facts,
        ),
        "missing_required_artifact",
    )
    assert finding.severity == "blocking"
    assert finding.params["artifact"] == "requirements-analysis-questions"
    assert finding.params["stage"] == "requirements-analysis"
    assert any(ref.ref.endswith("stage-graph.json") for ref in finding.evidence)

    # The rule is scoped to an OPEN gate: a stage still in progress is allowed to be incomplete.
    in_progress = F.state_text(
        F.state_path(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN), marks={"requirements-analysis": "-"}
    )
    assert "missing_required_artifact" not in _codes(
        engine.evaluate_intent(
            make_snap(in_progress, intent_dir=F.PAYLOAD_GATE_OPEN, stage_artifacts=()), facts
        )
    )


def test_missing_required_artifact_ignores_kind_restricted_and_optional(engine, make_snap, make_facts,
                                                                       graph):
    """`functional-design` produces only kind-restricted artifacts, so an empty gate is not a finding.

    Its ``produces`` are all listed in ``produces_kinds`` (service / ui / library / spec) and
    ``frontend-components`` is in ``optional_produces``; Studio cannot read a unit's kind, so requiring
    them would fabricate a blocking finding.
    """
    node = next(n for n in graph if n["slug"] == "functional-design")
    assert set(node["produces"]) <= set(node["produces_kinds"]) | set(node["optional_produces"])
    state = F.state_text(marks={"functional-design": "?"}, fields={"Current Stage": "functional-design",
                                                                  "Lifecycle Phase": "CONSTRUCTION"})
    findings = engine.evaluate_intent(
        make_snap(state, intent_dir=F.DEVDELTA_POC, stage_artifacts=()), make_facts()
    )
    assert "missing_required_artifact" not in _codes(findings)


def test_state_version_unsupported(engine, make_snap, make_facts, gate_state, C):
    # v7 record under a 2.6.2 engine (v8) — the mixed-version install C21 forbids.
    finding = _one(
        engine.evaluate_intent(
            make_snap(gate_state, intent_dir=F.PAYLOAD_GATE_OPEN), make_facts(engine_state_version=8)
        ),
        "state_version_unsupported",
    )
    assert finding.severity == "blocking"
    assert finding.params == {"reason": "engine_mismatch", "claimed": 7, "engine_state_version": 8,
                              "supported": list(C.SUPPORTED_STATE_VERSIONS)}

    # Same record under its own v7 engine: readable, no finding.
    assert "state_version_unsupported" not in _codes(
        engine.evaluate_intent(
            make_snap(gate_state, intent_dir=F.PAYLOAD_GATE_OPEN), make_facts(engine_state_version=7)
        )
    )

    # A version no parser claims to understand blocks even when the engine agrees with it.
    future = F.state_text(fields={"State Version": "9"})
    unsupported = _one(
        engine.evaluate_intent(make_snap(future, intent_dir=F.DEVDELTA_POC),
                               make_facts(engine_state_version=9)),
        "state_version_unsupported",
    )
    assert unsupported.params["reason"] == "unsupported"


def test_receipt_drift(engine, make_snap, make_facts, poc_state):
    finding = _one(
        engine.evaluate_intent(
            make_snap(poc_state, intent_dir=F.DEVDELTA_POC),
            make_facts(install_health="drift", drift_count=3, receipt_engine_version="2.3.0"),
        ),
        "receipt_drift",
    )
    assert finding.severity == "warn"
    assert finding.params["drift_count"] == 3
    assert finding.params["receipt_engine_version"] == "2.3.0"
    assert finding.params["installed_engine_version"] == "2.6.2"

    assert "receipt_drift" not in _codes(
        engine.evaluate_intent(make_snap(poc_state, intent_dir=F.DEVDELTA_POC), make_facts())
    )


def test_install_recovery_required(engine, make_snap, make_facts, poc_state):
    finding = _one(
        engine.evaluate_intent(make_snap(poc_state, intent_dir=F.DEVDELTA_POC),
                               make_facts(install_status="recovery_required")),
        "install_recovery_required",
    )
    assert finding.severity == "blocking"
    assert finding.params["repo_id"] == "r_000000000001"

    assert "install_recovery_required" not in _codes(
        engine.evaluate_intent(make_snap(poc_state, intent_dir=F.DEVDELTA_POC), make_facts())
    )


def test_duplicate_identity(engine, make_facts, make_snap, poc_state):
    facts = make_facts(other_repos_same_identity=["r_000000000002"])
    finding = _one(engine.evaluate_repo(facts), "duplicate_identity")
    assert finding.severity == "blocking"
    assert finding.params["other_repo_ids"] == ["r_000000000002"]
    assert "duplicate_identity" in _codes(
        engine.evaluate_intent(make_snap(poc_state, intent_dir=F.DEVDELTA_POC), facts)
    )

    assert "duplicate_identity" not in _codes(engine.evaluate_repo(make_facts()))


def test_delivery_uncertain_action(engine, make_snap, make_facts, poc_state):
    snap = make_snap(poc_state, intent_dir=F.DEVDELTA_POC, intent_key=F.DEVDELTA_POC)
    uncertain = ActionView(status="DeliveryUncertain", intent_dir=F.DEVDELTA_POC,
                           intent_key=F.DEVDELTA_POC)
    finding = _one(
        engine.evaluate_intent(snap, make_facts(live_actions=[uncertain])), "delivery_uncertain_action"
    )
    assert finding.severity == "blocking"
    assert finding.params["action_id"] == uncertain.action_id
    assert finding.params["status"] == "DeliveryUncertain"

    reconciling = replace(uncertain, status="ReconciliationRequired")
    assert "delivery_uncertain_action" in _codes(
        engine.evaluate_intent(snap, make_facts(live_actions=[reconciling]))
    )

    queued = replace(uncertain, status="Queued")
    assert "delivery_uncertain_action" not in _codes(
        engine.evaluate_intent(snap, make_facts(live_actions=[queued]))
    )


def test_delivery_uncertain_action_is_scoped_to_its_intent(engine, make_snap, make_facts, poc_state):
    """A sibling intent's uncertain delivery must not block this one."""
    other = ActionView(status="DeliveryUncertain", intent_dir="260814-review-major-remediation",
                       intent_key="260814-review-major-remediation")
    findings = engine.evaluate_intent(
        make_snap(poc_state, intent_dir=F.DEVDELTA_POC, intent_key=F.DEVDELTA_POC),
        make_facts(live_actions=[other]),
    )
    assert "delivery_uncertain_action" not in _codes(findings)


def test_lease_conflict(engine, studio, make_snap, make_facts, poc_state):
    lease = studio.storage.LeaseRecord(
        kind="execution",
        resolved_repo_identity="16777220:99001",
        generation=4,
        acquired_at="2026-09-04T09:59:00Z",
        heartbeat_at="2026-09-04T09:59:50Z",
        intent_uuid="01a00133-9901-7fdf-815d-5ae4e6e4be02",
        action_id="a_0000000000000009",
        repo_id="r_000000000001",
    )
    snap = make_snap(poc_state, intent_dir=F.DEVDELTA_POC)
    finding = _one(engine.evaluate_intent(snap, make_facts(leases=[lease])), "lease_conflict")
    assert finding.severity == "blocking"
    assert finding.params["reason"] == "other_intent"

    mine = replace(lease, intent_uuid=snap.row.uuid)
    assert "lease_conflict" not in _codes(engine.evaluate_intent(snap, make_facts(leases=[mine])))

    admin = replace(lease, kind="admin", intent_uuid=snap.row.uuid, operation_type="cursor_switch")
    admin_finding = _one(engine.evaluate_intent(snap, make_facts(leases=[admin])), "lease_conflict")
    assert admin_finding.params["reason"] == "admin_lease"
    assert admin_finding.params["operation_type"] == "cursor_switch"


def test_lease_for_another_repository_is_not_a_conflict(engine, studio, make_snap, make_facts,
                                                        poc_state):
    lease = studio.storage.LeaseRecord(
        kind="admin",
        resolved_repo_identity="16777220:70707",
        generation=1,
        acquired_at="2026-09-04T09:59:00Z",
        heartbeat_at="2026-09-04T09:59:50Z",
        repo_id="r_000000000002",
    )
    findings = engine.evaluate_intent(
        make_snap(poc_state, intent_dir=F.DEVDELTA_POC), make_facts(leases=[lease])
    )
    assert "lease_conflict" not in _codes(findings)


def test_state_unreadable(engine, make_snap, make_facts, C, poc_state):
    missing = engine.evaluate_intent(make_snap(None), make_facts(first_unreadable_at="2026-09-04T09:50:00Z"))
    finding = _one(missing, "state_unreadable")
    assert finding.severity == "blocking"
    assert finding.params["reason"] == "missing"
    assert finding.params["elapsed_secs"] == 600
    assert finding.params["grace_secs"] == C.STATE_UNREADABLE_GRACE_SECS

    header = engine.evaluate_intent(
        make_snap("# AI-DLC State Tracking\n"), make_facts(first_unreadable_at="2026-09-04T09:50:00Z")
    )
    assert _one(header, "state_unreadable").params["reason"] == "header_only"

    first_sight = engine.evaluate_intent(make_snap(None), make_facts())
    assert _one(first_sight, "state_unreadable").severity == "info"
    assert _one(first_sight, "state_unreadable").params["elapsed_secs"] is None

    assert "state_unreadable" not in _codes(
        engine.evaluate_intent(make_snap(poc_state, intent_dir=F.DEVDELTA_POC), make_facts())
    )


def test_state_unreadable_suppresses_derived_state_findings(engine, make_snap, make_facts):
    """One root cause, one finding: a missing state file must not also claim count drift."""
    findings = engine.evaluate_intent(make_snap(None), make_facts())
    assert _codes(findings) == ["state_unreadable"]


def test_interrupted_session(engine, make_snap, make_facts, poc_state):
    snap = make_snap(poc_state, intent_dir=F.DEVDELTA_POC)
    binding = BindingViewStub(intent_key=F.DEVDELTA_POC, interrupted_at="2026-09-04T09:58:00Z")

    landed = engine.evaluate_intent(snap, make_facts(binding=binding, slot=None))
    finding = _one(landed, "interrupted_session")
    assert finding.severity == "blocking"
    assert finding.params["interrupted_at"] == "2026-09-04T09:58:00Z"
    assert finding.params["slot_present"] is False

    idle_slot = engine.evaluate_intent(snap, make_facts(binding=binding, slot=SlotViewStub()))
    assert "interrupted_session" in _codes(idle_slot)

    # Still stopping: PRD §11.2 keeps the intent `Interrupted` until the slot is idle.
    for busy in (SlotViewStub(running=True), SlotViewStub(stopping=True),
                 SlotViewStub(stop_state="killing")):
        assert "interrupted_session" not in _codes(
            engine.evaluate_intent(snap, make_facts(binding=binding, slot=busy))
        )

    acknowledged = engine.evaluate_intent(
        snap, make_facts(binding=BindingViewStub(intent_key=F.DEVDELTA_POC), slot=None)
    )
    assert "interrupted_session" not in _codes(acknowledged)


def test_human_presence_anomaly(engine, make_snap, make_facts, poc_state):
    snap = make_snap(poc_state, intent_dir=F.DEVDELTA_POC, intent_key=F.DEVDELTA_POC)
    action = ActionView(
        status="ReconciliationRequired",
        intent_dir=F.DEVDELTA_POC,
        intent_key=F.DEVDELTA_POC,
        evidence={"findings": [{"code": "human_presence_anomaly",
                                "params": {"human_turn_delta": 0}}]},
    )
    finding = _one(
        engine.evaluate_intent(snap, make_facts(live_actions=[action])), "human_presence_anomaly"
    )
    assert finding.severity == "blocking"
    assert finding.params["human_turn_delta"] == 0
    assert finding.params["action_id"] == action.action_id

    clean = replace(action, evidence={"presence": {"human_turn_delta": 1, "ok": True}})
    assert "human_presence_anomaly" not in _codes(
        engine.evaluate_intent(snap, make_facts(live_actions=[clean]))
    )


def test_cursor_moved(engine, make_snap, make_facts, poc_state):
    snap = make_snap(poc_state, intent_dir=F.DEVDELTA_POC, intent_key=F.DEVDELTA_POC)
    action = ActionView(
        status="ReconciliationRequired",
        intent_dir=F.DEVDELTA_POC,
        intent_key=F.DEVDELTA_POC,
        evidence={"finding": "cursor_moved"},
    )
    finding = _one(engine.evaluate_intent(snap, make_facts(live_actions=[action])), "cursor_moved")
    assert finding.severity == "blocking"

    assert "cursor_moved" not in _codes(
        engine.evaluate_intent(snap, make_facts(live_actions=[replace(action, evidence={})]))
    )


def test_wrong_slot(engine, make_snap, make_facts, poc_state):
    snap = make_snap(poc_state, intent_dir=F.DEVDELTA_POC, intent_key=F.DEVDELTA_POC)
    action = ActionView(
        status="ReconciliationRequired",
        intent_dir=F.DEVDELTA_POC,
        intent_key=F.DEVDELTA_POC,
        evidence={"findings": ["wrong_slot"], "transcript_row": {"slot_key": "someone-elses-slot"}},
    )
    finding = _one(engine.evaluate_intent(snap, make_facts(live_actions=[action])), "wrong_slot")
    assert finding.severity == "blocking"

    right = replace(action, evidence={"transcript_row": {"slot_key": "aidlc-studio-x"}})
    assert "wrong_slot" not in _codes(engine.evaluate_intent(snap, make_facts(live_actions=[right])))


def test_cursor_mismatch(engine, make_snap, make_facts, poc_state):
    snap = make_snap(poc_state, intent_dir=F.DEVDELTA_POC, intent_key=F.DEVDELTA_POC)
    refused = ActionView(
        status="Queued",
        intent_dir=F.DEVDELTA_POC,
        intent_key=F.DEVDELTA_POC,
        cursor_readback=CursorReadbackView(ok=False, mismatch=("dir_name",)),
    )
    finding = _one(engine.evaluate_intent(snap, make_facts(live_actions=[refused])), "cursor_mismatch")
    assert finding.severity == "blocking"
    assert finding.params["mismatch"] == ["dir_name"]

    accepted = replace(refused, cursor_readback=CursorReadbackView(ok=True))
    assert "cursor_mismatch" not in _codes(
        engine.evaluate_intent(snap, make_facts(live_actions=[accepted]))
    )


def test_cursor_mismatch_reported_once_when_also_stored_on_the_action(engine, make_snap, make_facts,
                                                                     poc_state):
    snap = make_snap(poc_state, intent_dir=F.DEVDELTA_POC, intent_key=F.DEVDELTA_POC)
    action = ActionView(
        intent_dir=F.DEVDELTA_POC,
        intent_key=F.DEVDELTA_POC,
        evidence={"findings": ["cursor_mismatch"]},
        cursor_readback=CursorReadbackView(ok=False, mismatch=("uuid",)),
    )
    findings = engine.evaluate_intent(snap, make_facts(live_actions=[action]))
    assert _codes(findings).count("cursor_mismatch") == 1


def test_legacy_layout(engine, make_facts, C):
    finding = _one(engine.evaluate_repo(make_facts(layout="legacy")), "legacy_layout")
    assert finding.severity == "warn"
    assert finding.params["path"] == C.LEGACY_STATE_REL

    assert "legacy_layout" not in _codes(engine.evaluate_repo(make_facts(layout="spaces")))
    assert "legacy_layout" not in _codes(engine.evaluate_repo(make_facts()))


def test_unstable_read(engine, make_snap, make_facts, poc_state):
    snap = make_snap(poc_state, intent_dir=F.DEVDELTA_POC, stable=False)
    finding = _one(engine.evaluate_intent(snap, make_facts()), "unstable_read")
    assert finding.severity == "info"
    assert engine.unstable(snap) is True

    stable = make_snap(poc_state, intent_dir=F.DEVDELTA_POC)
    assert "unstable_read" not in _codes(engine.evaluate_intent(stable, make_facts()))
    assert engine.unstable(stable) is False


def test_engine_version_unknown(engine, make_facts, harness):
    finding = _one(
        engine.evaluate_repo(make_facts(harness_dirs=(harness(engine_version=None),))),
        "engine_version_unknown",
    )
    assert finding.severity == "warn"
    assert finding.params["dir"] == ".kiro"

    assert "engine_version_unknown" not in _codes(engine.evaluate_repo(make_facts()))


def test_audit_incomplete(engine, make_snap, make_facts, poc_state, C):
    """A tail-read shard means the findings above stand on partial history — reported, not resolved."""
    truncated = AuditBundleView(
        events=(),
        shards=(ShardMetaView(relpath="audit/603e5f4a8072-26e77d17b1cd.md",
                              size=C.MAX_AUDIT_TAIL_BYTES + 1, truncated=True),),
        complete=False,
    )
    finding = _one(
        engine.evaluate_intent(
            make_snap(poc_state, intent_dir=F.DEVDELTA_POC, audit=truncated), make_facts()
        ),
        "audit_incomplete",
    )
    assert finding.severity == "info"
    assert finding.params["truncated"] == ["audit/603e5f4a8072-26e77d17b1cd.md"]

    assert "audit_incomplete" not in _codes(
        engine.evaluate_intent(make_snap(poc_state, intent_dir=F.DEVDELTA_POC), make_facts())
    )


def test_gate_finding_notes_a_partial_audit(engine, make_snap, make_facts, gate_state):
    """A gate finding drawn from a truncated shard says so, so the UI can hedge the claim."""
    only_started = F.audit_block("STAGE_STARTED", "2026-09-03T14:00:00Z", Stage="requirements-analysis")
    snap = make_snap(gate_state, intent_dir=F.PAYLOAD_GATE_OPEN, audit_shards=(only_started,),
                     audit_complete=False)
    findings = engine.evaluate_intent(snap, make_facts(engine_state_version=7))
    finding = _one(findings, "gate_without_audit_row")
    assert finding.params["audit_complete"] is False
    assert any("partial" in (ref.detail or "") for ref in finding.evidence)


# --------------------------------------------------------------------------- #
# real fixture sanity + the bridge to the real reader
# --------------------------------------------------------------------------- #


def _real_stage_artifacts(tree, intent_dir: str, stage_rel: str) -> tuple[ArtifactMetaView, ...]:
    """The files that really sit in one stage's record directory."""
    record = tree / "aidlc" / "spaces" / "default" / "intents" / intent_dir
    stage_dir = record / stage_rel
    return tuple(
        ArtifactMetaView(
            relpath=f"aidlc/spaces/default/intents/{intent_dir}/{stage_rel}/{path.name}",
            name=path.name,
            sha256=F.sha256(path.read_text("utf-8")),
        )
        for path in sorted(stage_dir.iterdir())
        if path.is_file()
    )


def test_real_records_are_clean_under_their_own_engine(engine, make_snap, make_facts, gate_state,
                                                      gate_audit, poc_state):
    """The two healthy real records produce no blocking finding — no false positives.

    The v7 record is read against its OWN 32-stage graph and the artifacts that really exist in its
    open-gate stage directory, so this is the whole intent-level rule set over untouched bytes.
    """
    v7 = engine.evaluate_intent(
        make_snap(
            gate_state,
            intent_dir=F.PAYLOAD_GATE_OPEN,
            audit_shards=(gate_audit,),
            graph=tuple(F.stage_graph(F.PAYLOAD_230)),
            stage_artifacts=_real_stage_artifacts(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN,
                                                  "inception/requirements-analysis"),
        ),
        make_facts(engine_state_version=7),
    )
    assert engine.blocking(v7) == []
    assert _codes(v7) == []

    v8 = engine.evaluate_intent(
        make_snap(poc_state, intent_dir=F.DEVDELTA_POC,
                  audit_shards=(F.real_audit(F.DEVDELTA, F.DEVDELTA_POC),)),
        make_facts(),
    )
    assert engine.blocking(v8) == []


def test_real_in_flight_record_across_two_shards_is_clean(engine, make_snap, make_facts):
    """The real 2.6.2 in-flight record, both of its audit shards merged: no finding at all.

    Its custom scope claims 13 EXECUTE rows out of 33, its directive digest matches its state, and its
    only in-progress row has no gate event behind it — every rule in the table has to stay silent.
    """
    shards = tuple(path.read_text("utf-8")
                   for path in F.audit_paths(F.DEVDELTA, F.DEVDELTA_REMEDIATION))
    assert len(shards) == 2
    state = F.real_state(F.DEVDELTA, F.DEVDELTA_REMEDIATION)
    snap = make_snap(
        state,
        intent_dir=F.DEVDELTA_REMEDIATION,
        intent_key=F.DEVDELTA_REMEDIATION,
        audit_shards=shards,
        directive=_directive_of(F.DEVDELTA, F.DEVDELTA_REMEDIATION, state),
        row=IntentRowView(uuid="01a00133-9901-7fdf-815d-5ae4e6e4be02",
                          slug="review-major-remediation",
                          dir_name=F.DEVDELTA_REMEDIATION, scope="review-major-remediation"),
    )
    assert _codes(engine.evaluate_intent(snap, make_facts())) == []


def test_findings_over_the_real_reader_dataclasses(studio, engine, make_facts, gate_state, gate_audit):
    """The whole rule set against ``aidlc_reader``'s own parse of the real v7 record.

    The views above exist so this module can be tested while the reader is written; this test is the
    proof that they describe the shipped dataclasses. It builds the snapshot from
    ``parse_state_file`` / ``parse_audit_shard`` + ``merge_audit`` / ``parse_stage_graph`` /
    ``parse_intents_json`` / ``parse_directive`` and asserts the same three outcomes: clean record,
    superseded gate, gate event without a gate checkbox.
    """
    reader = getattr(studio, "aidlc_reader", None)
    needed = ("parse_state_file", "parse_audit_shard", "merge_audit", "parse_stage_graph",
              "Snapshot", "AuditBundle", "ShardMeta")
    if reader is None or any(not hasattr(reader, name) for name in needed):
        pytest.skip("aidlc_reader not implemented yet")

    record_rel = f"aidlc/spaces/default/intents/{F.PAYLOAD_GATE_OPEN}"
    shard_rel = f"{record_rel}/audit/fixture-host-0f1e2d3c4b5a.md"
    graph_bytes = (F.PAYLOAD_230 / ".kiro" / "tools" / "data" / "stage-graph.json").read_bytes()
    registry_bytes = (F.PAYLOAD_INTENTS / "intents.json").read_bytes()
    graph = reader.parse_stage_graph(graph_bytes)
    row = reader.match_intent_row(reader.parse_intents_json(registry_bytes), F.PAYLOAD_GATE_OPEN)
    assert row is not None and graph

    def snapshot(state_text: str, audit_text: str):
        raw = state_text.encode("utf-8")
        digest = F.sha256(state_text)
        state_snap = reader.Snapshot(relpath=f"{record_rel}/aidlc-state.md", bytes=raw, size=len(raw),
                                    mtime_ns=1_757_000_000_000_000_000, sha256=digest, unstable=False)
        events = reader.merge_audit([reader.parse_audit_shard(audit_text, shard_rel, 0)])
        bundle = reader.AuditBundle(
            events=events,
            shards=[reader.ShardMeta(relpath=shard_rel, size=len(audit_text.encode("utf-8")),
                                     mtime_ns=1_757_000_000_000_000_000, truncated=False)],
            complete=True,
        )
        directive = reader.parse_directive(
            (F.PAYLOAD_INTENTS / F.PAYLOAD_GATE_OPEN / ".aidlc-active-directive.json").read_bytes(),
            digest,
        )
        return SnapView(
            intent_dir=F.PAYLOAD_GATE_OPEN,
            intent_key=F.PAYLOAD_GATE_OPEN,
            row=row,
            state=reader.parse_state_file(state_text),
            state_snap=state_snap,
            directive=directive,
            audit=bundle,
            graph=tuple(graph),
            stage_artifacts=_real_stage_artifacts(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN,
                                                  "inception/requirements-analysis"),
            engine=None,
        )

    facts = make_facts(engine_state_version=7)
    clean = snapshot(gate_state, gate_audit)
    assert clean.directive is not None and clean.directive.matches_state is True
    assert _codes(engine.evaluate_intent(clean, facts)) == []

    restarted = gate_audit + F.audit_block("STAGE_STARTED", "2026-09-03T15:00:00Z",
                                           Stage="requirements-analysis")
    superseded = _one(engine.evaluate_intent(snapshot(gate_state, restarted), facts),
                      "gate_without_audit_row")
    assert superseded.severity == "blocking"
    assert superseded.params["reason"] == "superseded"

    in_progress = F.state_text(
        F.state_path(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN), marks={"requirements-analysis": "-"}
    )
    findings = engine.evaluate_intent(snapshot(in_progress, gate_audit), facts)
    assert "audit_gate_without_checkbox" in _codes(findings)
    # The directive digest is now stale against the patched state — reported as info, never blocking.
    assert _one(findings, "directive_state_digest_mismatch").severity == "info"


def test_reader_state_parse_agrees_with_the_test_views(studio, engine, make_snap, make_facts, C):
    """The hand-built ``StateView`` and the shipped ``StateFile`` must yield the same findings."""
    reader = getattr(studio, "aidlc_reader", None)
    if reader is None or not hasattr(reader, "parse_state_file"):
        pytest.skip("aidlc_reader not implemented yet")

    text = F.state_text(fields={"Completed": "20", "Lifecycle Phase": "OPERATION"})
    real = reader.parse_state_file(text)
    mine = parse_state(text, C)
    assert mine.counts["completed"] == real.counts["completed"]
    assert mine.total_stages_claimed == real.total_stages_claimed
    # `phase` case differs by design: the reader normalises `### INITIALIZATION PHASE` to the graph's
    # spelling ("initialization") while the state file shouts it. Every phase comparison in
    # consistency.py is therefore case-insensitive, and this is the test that pins that requirement.
    def rows(state):
        return [(r.slug, r.mark, r.state, (r.phase or "").lower(), r.suffix) for r in state.stages]

    assert rows(mine) == rows(real)
    assert [(name.lower(), value) for name, value in mine.phases] == \
        [(name.lower(), value) for name, value in real.phases]

    facts = make_facts()
    from_real = _codes(engine.evaluate_intent(
        make_snap(text, intent_dir=F.DEVDELTA_POC, state=real), facts))
    from_mine = _codes(engine.evaluate_intent(make_snap(text, intent_dir=F.DEVDELTA_POC), facts))
    assert from_real == from_mine


#: The registry row ``RepoBuilder`` writes for the bridge intent below.
BRIDGE_UUID = "01a00000-0000-7000-8000-000000000001"


def _bridge_repo_record(studio, clock, root):
    """A real ``RepoRecord`` for a repository on disk, so registry drift breaks this test too."""
    return studio.repo_registry.RepoRecord(
        repo_id="r_000000000001",
        label="bridge",
        canonical_path=str(root),
        resolved_identity="16777220:99001",
        git_common_dir_identity=None,
        platform="darwin",
        added_at="2026-09-01T00:00:00Z",
        last_seen=clock.iso(),
        availability="available",
        availability_detail=None,
        archived=False,
        legacy_console_id=None,
        install_status="installed",
        installed_engine_version="2.6.2",
        engine_dir=".kiro",
        receipt_version="1",
    )


def test_projection_snapshot_is_accepted_by_the_engine(studio, engine, make_snap, make_facts,
                                                       repo_builder, clock):
    """The real ``IntentSnapshot`` must produce exactly what the hand-built views produce.

    The views above are deliberate stand-ins (this module must stay provable without the two modules
    that open files), and the risk they carry is silence: a field ``projection`` renamed, or one it never
    sets, would read as its default here and the whole table would keep passing while the shipped engine
    saw something else. So one scenario is run twice over the same bytes — once through
    ``AidlcReader`` + ``Projection.snapshot``, once through the views — and the finding lists must match.
    """
    reader_mod = getattr(studio, "aidlc_reader", None)
    projection_mod = getattr(studio, "projection", None)
    if reader_mod is None or projection_mod is None:
        pytest.skip("aidlc_reader/projection not implemented yet")

    intent_dir = "260904-bridge"
    # Two disagreements, so the two lists being compared are not both empty: the lifecycle phase names a
    # phase the current stage does not belong to, and the claimed `Completed` count is too high.
    state = F.state_text(
        marks={"requirements-analysis": "?"},
        fields={"Current Stage": "requirements-analysis", "Lifecycle Phase": "CONSTRUCTION",
                "Status": "Running", "Completed": "20"},
    )
    audit = F.gate_open_audit("requirements-analysis")
    root = (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(intent_dir, state=state, audit=audit, slug="bridge", uuid=BRIDGE_UUID)
        .build()
    )

    facts = make_facts()
    real_snap = projection_mod.Projection(
        reader_mod.AidlcReader(clock), engine, clock
    ).snapshot(_bridge_repo_record(studio, clock, root), "default", intent_dir)
    assert real_snap.state is not None and real_snap.stable is True

    view_snap = make_snap(
        state,
        audit_shards=[audit],
        intent_dir=intent_dir,
        intent_key=intent_dir,
        row=IntentRowView(uuid=BRIDGE_UUID, slug="bridge", dir_name=intent_dir, scope="poc"),
        stage_artifacts=(),
    )
    from_real = _codes(engine.evaluate_intent(real_snap, facts))
    # Spelled out so the comparison below can never degenerate into "two empty lists are equal". The two
    # `missing_required_artifact` rows are the gate stage's two declared `produces` files, which this
    # intent does not have — i.e. the real `graph` and `stage_artifacts` are being read on both paths.
    assert from_real == ["missing_required_artifact", "missing_required_artifact",
                         "phase_stage_disagreement", "completed_count_mismatch"]
    assert from_real == _codes(engine.evaluate_intent(view_snap, facts))
