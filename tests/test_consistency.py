"""One test per finding code, positive and negative, built from the real harvested fixtures.

``consistency.py`` is duck-typed against the §1.6/§1.8 dataclasses (it must not import the module that opens
files), so the inputs are small frozen views with the contract's field names, parsed with the shipped grammar.
The last tests close the loop: the views agree with ``AidlcReader``'s parse and with a real ``Projection.snapshot``.
"""

from __future__ import annotations

import builtins
import json
from dataclasses import dataclass, field, make_dataclass, replace
from typing import Any, Sequence

import fixtures as F
import pytest

LEDGER = "260901-ledger-export"


def _view(name: str, *fields: Any):
    return make_dataclass(name, [f if isinstance(f, tuple) else (f, Any) for f in fields], frozen=True)


SnapshotView = _view("SnapshotView", "relpath", "sha256", ("size", int, 0), ("mtime_ns", int, 0),
                     ("unstable", bool, False))
StageRowView = _view("StageRowView", "slug", "mark", "state", "phase", "suffix")
IntentRowView = _view("IntentRowView", "uuid", "slug", "dir_name", "scope", ("repos", tuple, ()),
                      ("status", str, "in-flight"))
DirectiveView = _view("DirectiveView", "version", "stage", "unit", "state_sha256", "matches_state")
AuditEventView = _view("AuditEventView", "shard", "shard_index", "pos", "timestamp", "event", "fields",
                       ("raw", str, ""))
ShardMetaView = _view("ShardMetaView", "relpath", ("size", int, 0), ("mtime_ns", int, 0), ("truncated", bool, False))
AuditBundleView = _view("AuditBundleView", ("events", tuple, ()), ("shards", tuple, ()), ("complete", bool, True))
ArtifactMetaView = _view("ArtifactMetaView", "relpath", "name", ("sha256", Any, None))
BindingViewStub = _view("BindingViewStub", ("intent_key", str, LEDGER),
                        ("slot_key", Any, f"aidlc-studio-r_000000000001-{LEDGER}"), ("interrupted_at", Any, None),
                        ("paused", bool, False), ("archive_state", str, "active"))
SlotViewStub = _view("SlotViewStub", ("key", str, f"aidlc-studio-r_000000000001-{LEDGER}"), ("running", bool, False),
                     ("stopping", bool, False), ("stop_state", str, "idle"))
CursorReadbackView = _view("CursorReadbackView", "ok", ("space", str, "default"), ("dir_name", str, LEDGER),
                           ("uuid", Any, None), ("state_sha256", Any, None), ("mismatch", tuple, ()))
ActionView = _view("ActionView", ("action_id", str, "a_0000000000000001"), ("type", str, "gate"),
                   ("status", str, "Queued"), ("space", str, "default"), ("intent_dir", str, LEDGER),
                   ("intent_key", str, LEDGER), ("stage", Any, "requirements-analysis"),
                   ("evidence", dict, field(default_factory=dict)), ("cursor_readback", Any, None))


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
class SnapView:
    """The §1.8 ``IntentSnapshot`` fields ``ConsistencyEngine`` is documented to read."""

    repo_id: str = "r_000000000001"
    space: str = "default"
    intent_dir: str = LEDGER
    intent_key: str = LEDGER
    row: Any = None
    state_snap: Any = None
    state: StateView | None = None
    directive: Any = None
    audit: Any = AuditBundleView()
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


def parse_state(text: str, constants: Any) -> StateView:
    """The subset of ``parse_state_file`` the findings depend on, with the shipped grammar from ``constants``."""
    sections: dict[str, dict[str, str]] = {}
    phases: list[tuple[str, str]] = []
    stages: list = []
    section = phase = None
    for line in text.splitlines():
        heading = constants.SECTION_RE.match(line)
        if heading:
            section, phase = heading.group(1), None
            sections.setdefault(section, {})
            continue
        sub = constants.SUBSECTION_RE.match(line)
        if sub:
            phase_header = constants.PHASE_HEADER_RE.match(sub.group(1))
            phase = phase_header.group(1) if phase_header else sub.group(1)
            continue
        row = constants.CHECKBOX_RE.match(line) if section == "Stage Progress" else None
        if row:
            mark, slug, suffix = row.group(1), row.group(2), row.group(3)
            stages.append(StageRowView(slug=slug, mark=mark, state=constants.CHECKBOX_MAP.get(mark.lower(), "unknown"),
                                       phase=phase, suffix=suffix or None))
            continue
        pair = constants.FIELD_RE.match(line)
        if pair and section:
            sections[section][pair.group(1)] = pair.group(2)
            if section == "Phase Progress":
                phases.append((pair.group(1), pair.group(2)))
    flat = {key: value for rows in sections.values() for key, value in rows.items()}
    counts = {"total": len(stages), **{s: sum(1 for r in stages if r.state == s) for s in constants.STAGE_STATE}}
    counts["done"] = sum(counts[name] for name in constants.DONE_STAGE_STATES)
    return StateView(
        sections=sections, phases=phases, stages=stages, counts=counts,
        state_version=_int(flat.get(constants.STATE_VERSION_FIELD)), current_stage=flat.get("Current Stage") or None,
        next_stage=flat.get("Next Stage") or None, status=flat.get("Status") or None,
        lifecycle_phase=flat.get("Lifecycle Phase") or None, revision_count=_int(flat.get("Revision Count")) or 0,
        completed_claimed=_int(flat.get("Completed")), total_stages_claimed=_int(flat.get("Total Stages")),
        header_only=text.strip() == "# AI-DLC State Tracking")


def parse_audit(text: str, constants: Any, *, shard: str, shard_index: int = 0) -> list:
    events = []
    for pos, block in enumerate(text.replace("\r\n", "\n").split(constants.AUDIT_BLOCK_SEPARATOR)):
        fields = {m.group(1): m.group(2) for m in map(constants.AUDIT_FIELD_RE.match, block.splitlines()) if m}
        event, timestamp = fields.pop("Event", None), fields.pop("Timestamp", "")
        if event:
            events.append(AuditEventView(shard=shard, shard_index=shard_index, pos=pos, timestamp=timestamp,
                                         event=event, fields=fields, raw=block))
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


@pytest.fixture
def cons(studio):
    assert studio.consistency is not None, getattr(studio, "consistency__error", "consistency.py did not import")
    return studio.consistency


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
    def make(*, engine_version: str | None = "2.6.2", engine_state_version: int | None = 8, directory: str = ".kiro"):
        return studio.repo_registry.HarnessDir(dir=directory, harness_name="kiro", rules_subdir=None,
                                               engine_version=engine_version,
                                               engine_state_version=engine_state_version, stage_count=33,
                                               has_utility=True)

    return make


def repo_record(studio, clock, **over):
    """A REAL ``RepoRecord``, so registry drift breaks these tests too."""
    return studio.repo_registry.RepoRecord(**{
        "repo_id": "r_000000000001", "label": "fixture", "canonical_path": "/FIXTURE/repo",
        "resolved_identity": "16777220:99001", "git_common_dir_identity": None, "platform": "darwin",
        "added_at": "2026-09-01T00:00:00Z", "last_seen": clock.iso(), "availability": "available",
        "availability_detail": None, "archived": False, "legacy_console_id": None, "install_status": "installed",
        "installed_engine_version": "2.6.2", "engine_dir": ".kiro", "receipt_version": "1", **over})


@pytest.fixture
def make_facts(studio, clock, harness):
    """A ``RepoFacts`` over a real ``RepoRecord`` and ``InstallHealth``."""
    def make(**over: Any):
        repo = repo_record(studio, clock, **{k: over.pop(k) for k in ("repo_id", "resolved_identity", "install_status")
                                             if k in over})
        install = studio.repo_registry.InstallHealth(
            status=over.pop("install_health", "installed"), engine_dir=".kiro", engine_version="2.6.2",
            engine_state_version=over.pop("engine_state_version", 8), stage_count=33, receipt_id="rc_0000000000000001",
            receipt_engine_version=over.pop("receipt_engine_version", "2.6.2"), drift_count=over.pop("drift_count", 0),
            harness_dirs=tuple(over.pop("harness_dirs", (harness(),))))
        return studio.consistency.RepoFacts(now=over.pop("now", clock.iso()), repo=repo, install=install, **over)

    return make


@pytest.fixture
def make_snap(C, graph, harness):
    """A snapshot over a state file's text (``None``: no state file); every field can be overridden."""
    def make(state_text: str | None = None, *, audit_shards: Sequence[str] = (), **over: Any):
        intent_dir = over.get("intent_dir", LEDGER)
        state = state_snap = None
        if state_text is not None:
            state = parse_state(state_text, C)
            state_snap = SnapshotView(relpath=f"aidlc/spaces/default/intents/{intent_dir}/aidlc-state.md",
                                      sha256=F.sha256(state_text), size=len(state_text.encode("utf-8")))
        events, shards = [], []
        for index, text in enumerate(audit_shards):
            relpath = f"audit/fixture-host-0f1e2d3c4b5{index}a.md"
            events += parse_audit(text, C, shard=relpath, shard_index=index)
            shards.append(ShardMetaView(relpath=relpath, size=len(text.encode("utf-8"))))
        events.sort(key=lambda e: (e.timestamp, e.shard_index, e.pos))
        bundle = over.pop("audit", AuditBundleView(events=tuple(events), shards=tuple(shards),
                                                   complete=over.pop("audit_complete", True)))
        defaults = {"row": IntentRowView(uuid="01a047b1-c605-7f76-927a-8193f101d918", slug="ledger-export",
                                         dir_name=intent_dir, scope="feature"),
                    "graph": graph, "engine": harness(), "state": state, "state_snap": state_snap}
        return SnapView(audit=bundle, **{**defaults, **over})

    return make


@pytest.fixture
def ev(engine, make_snap, make_facts):
    """``evaluate_intent`` over ``make_snap(state, **snap)`` with the given (or default) facts."""
    return lambda state=None, facts=None, **snap: engine.evaluate_intent(make_snap(state, **snap),
                                                                         make_facts() if facts is None else facts)


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


def started(ts: str = "2026-09-03T15:00:00Z") -> str:
    return F.audit_block("STAGE_STARTED", ts, Stage="requirements-analysis")


def in_progress_gate() -> str:
    return F.state_text(F.state_path(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN), marks={"requirements-analysis": "-"})


# --- the table itself ---
def test_severity_table_covers_every_code_exactly_once(cons, C):
    assert set(cons.FINDING_SEVERITIES) == set(cons.FINDING_CODES)
    assert len(cons.FINDING_CODES) == len(set(cons.FINDING_CODES))
    assert set(cons.FINDING_SEVERITIES.values()) <= set(C.FINDING_SEVERITY)


def test_blocking_set_is_the_contract_set(cons):
    assert cons.BLOCKING_FINDING_CODES == frozenset({
        "dangling_cursor", "phase_stage_disagreement", "gate_without_audit_row", "audit_gate_without_checkbox",
        "missing_required_artifact", "state_version_unsupported", "install_recovery_required", "duplicate_identity",
        "delivery_uncertain_action", "lease_conflict", "state_unreadable", "interrupted_session",
        "human_presence_anomaly", "cursor_moved", "wrong_slot", "cursor_mismatch",
        # additive to §1.7: two sources disagree on the executing unit (or neither names one of several holders)
        "unit_ambiguous",
    })


def test_non_blocking_codes_are_warn_or_info(cons):
    for code, severity in cons.FINDING_SEVERITIES.items():
        assert severity == "blocking" if code in cons.BLOCKING_FINDING_CODES else severity in ("warn", "info"), code


def test_severity_agrees_with_the_registry_preflight_table(cons, studio):
    """A code cannot mean ``warn`` in preflight and ``blocking`` in a scan."""
    preflight = studio.repo_registry.PREFLIGHT_WARNING_SEVERITY
    shared = set(preflight) & set(cons.FINDING_SEVERITIES)
    assert shared, "the two tables must overlap on at least legacy_layout"
    for code in shared:
        assert preflight[code] == cons.FINDING_SEVERITIES[code], code


def test_finding_json_shape_and_message_key(ev):
    finding = _one(ev(dangling_cursor=True), "dangling_cursor")
    assert finding.to_json() == {"code": "dangling_cursor", "severity": "blocking",
                                 "message_key": "finding.dangling_cursor", "params": finding.params,
                                 "evidence": [ref.to_json() for ref in finding.evidence]}
    assert finding.message_key == f"finding.{finding.code}" and json.dumps(finding.to_json())  # params survive JSON
    assert all(set(ref.to_json()) == {"kind", "ref", "detail"} for ref in finding.evidence)
    assert all(ref.kind in ("file", "audit", "studio", "host") for ref in finding.evidence)


def test_blocking_filters_on_emitted_severity(engine, ev, make_facts):
    """`state_unreadable` inside its grace window is reported but does not block."""
    fresh = ev(None, make_facts(first_unreadable_at="2026-09-04T09:59:55Z"))
    assert _one(fresh, "state_unreadable").severity == "info"
    assert "state_unreadable" not in _codes(engine.blocking(fresh))
    stale = ev(None, make_facts(first_unreadable_at="2026-09-04T09:50:00Z"))
    assert _one(stale, "state_unreadable").severity == "blocking"
    assert "state_unreadable" in _codes(engine.blocking(stale))


def test_findings_are_ordered_blocking_warn_info_and_deduped(ev, make_facts):
    findings = ev(F.state_text(F.state_path(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN), fields={"Completed": "20"}),
                  make_facts(install_status="recovery_required"), dangling_cursor=True, stable=False,
                  intent_dir=F.PAYLOAD_GATE_OPEN)
    ranks = [{"blocking": 0, "warn": 1, "info": 2}[f.severity] for f in findings]
    assert ranks == sorted(ranks)
    keys = [(f.code, json.dumps(f.params, sort_keys=True, default=str)) for f in findings]
    assert len(keys) == len(set(keys))


def test_evaluate_intent_includes_the_repository_findings(engine, ev, make_facts, gate_state, gate_audit):
    facts = make_facts(install_status="recovery_required", engine_state_version=7)
    assert "install_recovery_required" in _codes(ev(gate_state, facts, intent_dir=F.PAYLOAD_GATE_OPEN,
                                                    audit_shards=(gate_audit,)))
    assert "install_recovery_required" in _codes(engine.evaluate_repo(facts))


def test_engine_never_opens_a_file(ev, make_facts, monkeypatch, gate_state, gate_audit):
    """§1.7: the engine may not read files. Proven by removing its ability to."""
    def refuse(*_a: Any, **_k: Any):
        raise AssertionError("ConsistencyEngine opened a file")

    monkeypatch.setattr(builtins, "open", refuse)
    ev(gate_state, make_facts(engine_state_version=7), intent_dir=F.PAYLOAD_GATE_OPEN, audit_shards=(gate_audit,))


# --- one test per code ---
def test_contradiction_findings_carry_both_readings(ev, make_facts, gate_audit):
    """Report, never resolve: every two-source finding names both readings, never just the loser."""
    state = F.state_text(F.state_path(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN), fields={"Completed": "20",
                                                                                   "Total Stages": "99"})
    findings = ev(state, make_facts(engine_state_version=8, other_repos_same_identity=["r_000000000002"]),
                  intent_dir=F.PAYLOAD_GATE_OPEN, audit_shards=(gate_audit + started(),), row=None, stage_artifacts=())
    two_sourced = {"intent_registry_missing_row", "completed_count_mismatch", "total_count_mismatch",
                   "stage_graph_drift", "gate_without_audit_row", "missing_required_artifact",
                   "state_version_unsupported", "duplicate_identity"}
    assert two_sourced <= set(_codes(findings))
    for finding in findings:
        if finding.code in two_sourced:
            assert len(finding.evidence) >= 2 and all(ref.detail for ref in finding.evidence), finding.code


def test_dangling_cursor(ev, make_facts, gate_state):
    facts = make_facts(engine_state_version=7)
    finding = _one(ev(gate_state, facts, dangling_cursor=True), "dangling_cursor")
    assert finding.severity == "blocking" and finding.params["space"] == "default"
    assert any("active-intent" in ref.ref for ref in finding.evidence)
    assert "dangling_cursor" not in _codes(ev(gate_state, facts, dangling_cursor=False))


def test_intent_registry_missing_row(ev, make_facts, gate_state):
    facts = make_facts(engine_state_version=7)
    finding = _one(ev(gate_state, facts, row=None), "intent_registry_missing_row")
    assert finding.severity == "warn" and any("intents.json" in ref.ref for ref in finding.evidence)
    assert "intent_registry_missing_row" not in _codes(ev(gate_state, facts))


def _directive_of(tree, intent_dir: str, state_text: str) -> Any:
    data = json.loads((F.state_path(tree, intent_dir).parent / ".aidlc-active-directive.json").read_text("utf-8"))
    return DirectiveView(version=int(data.get("version", 1)), stage=data["stage"], unit=data.get("unit"),
                         state_sha256=data["state_sha256"], matches_state=data["state_sha256"] == F.sha256(state_text))


def test_directive_state_digest_mismatch(ev):
    """The real poc directive is stale; the real remediation directive matches its state."""
    stale_state = F.real_state(F.DEVDELTA, F.DEVDELTA_POC)
    stale = _directive_of(F.DEVDELTA, F.DEVDELTA_POC, stale_state)
    assert stale.matches_state is False
    finding = _one(ev(stale_state, intent_dir=F.DEVDELTA_POC, directive=stale), "directive_state_digest_mismatch")
    assert finding.severity == "info" and len(finding.evidence) == 2  # both readings
    assert finding.params["directive_state_sha256"] != finding.params["state_sha256"]
    fresh_state = F.real_state(F.DEVDELTA, F.DEVDELTA_REMEDIATION)
    fresh = _directive_of(F.DEVDELTA, F.DEVDELTA_REMEDIATION, fresh_state)
    assert fresh.matches_state is True
    assert "directive_state_digest_mismatch" not in _codes(ev(fresh_state, intent_dir=F.DEVDELTA_REMEDIATION,
                                                              directive=fresh))


def test_phase_stage_disagreement(ev, make_facts, gate_state):
    """The real 2.1.1 record is the positive; the real v7 in-flight record is the negative."""
    facts = make_facts(engine_state_version=7)
    finding = _one(ev(F.real_state(F.OLDER, F.OLDER_INTENT), facts, intent_dir=F.OLDER_INTENT),
                   "phase_stage_disagreement")
    assert finding.severity == "blocking"
    assert finding.params == {"reason": "phase_not_active", "phase": "IDEATION", "phase_status": "Pending"}
    assert "phase_stage_disagreement" not in _codes(ev(gate_state, facts, intent_dir=F.PAYLOAD_GATE_OPEN))
    # both clauses at once: OPERATION is Pending AND the current stage sits under CONSTRUCTION
    moved = ev(F.state_text(fields={"Lifecycle Phase": "OPERATION"}), intent_dir=F.DEVDELTA_POC)
    assert {f.params["reason"] for f in moved if f.code == "phase_stage_disagreement"} == {
        "phase_not_active", "stage_not_in_phase"}


@pytest.mark.parametrize("code,variant,params", [
    ("completed_count_mismatch", {"fields": {"Completed": "20"}}, {"claimed": 20, "actual": 8}),
    # one row re-suffixed to SKIP: 32 EXECUTE rows against a claimed 33
    ("total_count_mismatch", {"modes": {"ci-pipeline": "SKIP"}}, {"claimed": 33, "actual": 32}),
])
def test_count_mismatches(ev, poc_state, code, variant, params):
    finding = _one(ev(F.state_text(**variant), intent_dir=F.DEVDELTA_POC), code)
    assert finding.severity == "warn" and finding.params == params
    assert code not in _codes(ev(poc_state, intent_dir=F.DEVDELTA_POC))


def test_stage_graph_drift(ev, make_facts, gate_state, poc_state):
    """A v7 record's 32 rows against the bundled 33-stage graph — a real mixed-version pair."""
    facts = make_facts(engine_state_version=7)
    finding = _one(ev(gate_state, facts, intent_dir=F.PAYLOAD_GATE_OPEN), "stage_graph_drift")
    assert finding.severity == "warn" and finding.params["only_in_state"] == ["application-design"]
    assert finding.params["only_in_graph"] == ["contract-design", "domain-design"]
    assert "stage_graph_drift" not in _codes(ev(poc_state, intent_dir=F.DEVDELTA_POC))
    # no graph means one source, and one source cannot disagree with itself
    assert "stage_graph_drift" not in _codes(ev(gate_state, facts, intent_dir=F.PAYLOAD_GATE_OPEN, graph=()))


def test_gate_without_audit_row(ev, make_facts, gate_state, gate_audit):
    facts = make_facts(engine_state_version=7)

    def gate(*shards: str, **snap: Any):
        return ev(gate_state, facts, intent_dir=F.PAYLOAD_GATE_OPEN, audit_shards=shards, **snap)

    # negative: the real shard's latest STAGE_AWAITING_APPROVAL is the newest gate row for the stage
    assert "gate_without_audit_row" not in _codes(gate(gate_audit))
    # positive (superseded): a later STAGE_STARTED means the gate row belongs to an earlier attempt
    finding = _one(gate(gate_audit + started()), "gate_without_audit_row")
    assert finding.severity == "blocking" and finding.params["reason"] == "superseded"
    assert finding.params["stage"] == "requirements-analysis" and finding.params["superseded_by"] == "STAGE_STARTED"
    assert {ref.kind for ref in finding.evidence} == {"file", "audit"}
    # positive (absent): a shard that never recorded the gate at all
    absent = _one(gate(started("2026-09-03T14:00:00Z")), "gate_without_audit_row")
    assert absent.params["reason"] == "absent" and absent.params["awaiting_at"] is None
    # STAGE_STARTED and STAGE_AWAITING_APPROVAL in one second are ordered by position, not by clock
    same_second = started("2026-09-03T14:00:00Z") + F.audit_block("STAGE_AWAITING_APPROVAL", "2026-09-03T14:00:00Z",
                                                                  Stage="requirements-analysis")
    assert "gate_without_audit_row" not in _codes(gate(same_second))
    # a gate finding drawn from a truncated shard says so, so the UI can hedge the claim
    partial = _one(gate(started("2026-09-03T14:00:00Z"), audit_complete=False), "gate_without_audit_row")
    assert partial.params["audit_complete"] is False
    assert any("partial" in (ref.detail or "") for ref in partial.evidence)


def test_audit_gate_without_checkbox(ev, make_facts, gate_state, gate_audit):
    facts = make_facts(engine_state_version=7)
    finding = _one(ev(in_progress_gate(), facts, intent_dir=F.PAYLOAD_GATE_OPEN, audit_shards=(gate_audit,)),
                   "audit_gate_without_checkbox")
    assert finding.severity == "blocking"
    assert finding.params["stage"] == "requirements-analysis" and finding.params["row_state"] == "in_progress"
    assert "audit_gate_without_checkbox" not in _codes(ev(gate_state, facts, intent_dir=F.PAYLOAD_GATE_OPEN,
                                                          audit_shards=(gate_audit,)))


def test_missing_required_artifact(ev, make_facts, gate_state, gate_audit, graph):
    facts = make_facts(engine_state_version=7)
    stage_dir = f"aidlc/spaces/default/intents/{F.PAYLOAD_GATE_OPEN}/inception/requirements-analysis"
    both = (ArtifactMetaView(relpath=f"{stage_dir}/requirements.md", name="requirements.md", sha256="a" * 64),
            ArtifactMetaView(relpath=f"{stage_dir}/requirements-analysis-questions.md",
                             name="requirements-analysis-questions.md", sha256="b" * 64))

    def gate(state: str, artifacts: tuple, **snap: Any):
        return ev(state, facts, intent_dir=F.PAYLOAD_GATE_OPEN, stage_artifacts=artifacts, **snap)

    assert "missing_required_artifact" not in _codes(gate(gate_state, both, audit_shards=(gate_audit,)))
    finding = _one(gate(gate_state, both[:1], audit_shards=(gate_audit,)), "missing_required_artifact")
    assert finding.severity == "blocking" and finding.params["artifact"] == "requirements-analysis-questions"
    assert finding.params["stage"] == "requirements-analysis"
    assert any(ref.ref.endswith("stage-graph.json") for ref in finding.evidence)
    # the rule is scoped to an OPEN gate: a stage still in progress is allowed to be incomplete
    assert "missing_required_artifact" not in _codes(gate(in_progress_gate(), ()))
    # `functional-design` produces only kind-restricted/optional artifacts, and Studio cannot read a unit's kind
    node = next(n for n in graph if n["slug"] == "functional-design")
    assert set(node["produces"]) <= set(node["produces_kinds"]) | set(node["optional_produces"])
    functional = F.state_text(marks={"functional-design": "?"}, fields={"Current Stage": "functional-design",
                                                                        "Lifecycle Phase": "CONSTRUCTION"})
    assert "missing_required_artifact" not in _codes(ev(functional, intent_dir=F.DEVDELTA_POC, stage_artifacts=()))


def test_state_version_unsupported(ev, make_facts, gate_state, C):
    # a v7 record under a 2.6.2 engine (v8): the mixed-version install C21 forbids
    finding = _one(ev(gate_state, make_facts(engine_state_version=8), intent_dir=F.PAYLOAD_GATE_OPEN),
                   "state_version_unsupported")
    assert finding.severity == "blocking"
    assert finding.params == {"reason": "engine_mismatch", "claimed": 7, "engine_state_version": 8,
                              "supported": list(C.SUPPORTED_STATE_VERSIONS)}
    # the same record under its own v7 engine is readable
    assert "state_version_unsupported" not in _codes(ev(gate_state, make_facts(engine_state_version=7),
                                                        intent_dir=F.PAYLOAD_GATE_OPEN))
    # a version no parser claims to understand blocks even when the engine agrees with it
    future = _one(ev(F.state_text(fields={"State Version": "9"}), make_facts(engine_state_version=9),
                     intent_dir=F.DEVDELTA_POC), "state_version_unsupported")
    assert future.params["reason"] == "unsupported"


def test_receipt_drift(ev, make_facts, poc_state):
    finding = _one(ev(poc_state, make_facts(install_health="drift", drift_count=3, receipt_engine_version="2.3.0"),
                      intent_dir=F.DEVDELTA_POC), "receipt_drift")
    assert finding.severity == "warn" and finding.params["drift_count"] == 3
    assert finding.params["receipt_engine_version"] == "2.3.0" and finding.params["installed_engine_version"] == "2.6.2"
    assert "receipt_drift" not in _codes(ev(poc_state, intent_dir=F.DEVDELTA_POC))


def test_install_recovery_required(ev, make_facts, poc_state):
    finding = _one(ev(poc_state, make_facts(install_status="recovery_required"), intent_dir=F.DEVDELTA_POC),
                   "install_recovery_required")
    assert finding.severity == "blocking" and finding.params["repo_id"] == "r_000000000001"
    assert "install_recovery_required" not in _codes(ev(poc_state, intent_dir=F.DEVDELTA_POC))


def test_duplicate_identity(engine, ev, make_facts, poc_state):
    facts = make_facts(other_repos_same_identity=["r_000000000002"])
    finding = _one(engine.evaluate_repo(facts), "duplicate_identity")
    assert finding.severity == "blocking" and finding.params["other_repo_ids"] == ["r_000000000002"]
    assert "duplicate_identity" in _codes(ev(poc_state, facts, intent_dir=F.DEVDELTA_POC))
    assert "duplicate_identity" not in _codes(engine.evaluate_repo(make_facts()))


@pytest.fixture
def poc_snap(make_snap, poc_state):
    return make_snap(poc_state, intent_dir=F.DEVDELTA_POC, intent_key=F.DEVDELTA_POC)


def poc_action(**over) -> Any:
    return ActionView(**{"intent_dir": F.DEVDELTA_POC, "intent_key": F.DEVDELTA_POC, **over})


def test_delivery_uncertain_action(engine, poc_snap, make_facts):
    uncertain = poc_action(status="ReconciliationRequired")
    finding = _one(engine.evaluate_intent(poc_snap, make_facts(live_actions=[uncertain])), "delivery_uncertain_action")
    assert finding.severity == "blocking" and finding.params["action_id"] == uncertain.action_id
    assert finding.params["status"] == "ReconciliationRequired"
    queued = replace(uncertain, status="Queued")
    assert "delivery_uncertain_action" not in _codes(engine.evaluate_intent(poc_snap,
                                                                            make_facts(live_actions=[queued])))
    # a sibling intent's uncertain delivery must not block this one
    other = ActionView(status="ReconciliationRequired", intent_dir="260814-review-major-remediation",
                       intent_key="260814-review-major-remediation")
    assert "delivery_uncertain_action" not in _codes(engine.evaluate_intent(poc_snap, make_facts(live_actions=[other])))


def test_lease_conflict(engine, studio, make_snap, make_facts, poc_state):
    lease = studio.leases.LeaseRecord(kind="execution", resolved_repo_identity="16777220:99001", generation=4,
                                      acquired_at="2026-09-04T09:59:00Z", heartbeat_at="2026-09-04T09:59:50Z",
                                      intent_uuid="01a00133-9901-7fdf-815d-5ae4e6e4be02",
                                      action_id="a_0000000000000009", repo_id="r_000000000001")
    snap = make_snap(poc_state, intent_dir=F.DEVDELTA_POC)

    def conflicts(*leases):
        return engine.evaluate_intent(snap, make_facts(leases=list(leases)))

    finding = _one(conflicts(lease), "lease_conflict")
    assert finding.severity == "blocking" and finding.params["reason"] == "other_intent"
    assert "lease_conflict" not in _codes(conflicts(replace(lease, intent_uuid=snap.row.uuid)))
    admin = _one(conflicts(replace(lease, kind="admin", intent_uuid=snap.row.uuid, operation_type="cursor_switch")),
                 "lease_conflict")
    assert admin.params["reason"] == "admin_lease" and admin.params["operation_type"] == "cursor_switch"
    # a lease for another repository is not a conflict
    elsewhere = studio.leases.LeaseRecord(kind="admin", resolved_repo_identity="16777220:70707", generation=1,
                                          acquired_at="2026-09-04T09:59:00Z", heartbeat_at="2026-09-04T09:59:50Z",
                                          repo_id="r_000000000002")
    assert "lease_conflict" not in _codes(conflicts(elsewhere))


def test_state_unreadable(ev, make_facts, C, poc_state):
    stale = make_facts(first_unreadable_at="2026-09-04T09:50:00Z")
    finding = _one(ev(None, stale), "state_unreadable")
    assert finding.severity == "blocking" and finding.params["reason"] == "missing"
    assert finding.params["elapsed_secs"] == 600 and finding.params["grace_secs"] == C.STATE_UNREADABLE_GRACE_SECS
    assert _one(ev("# AI-DLC State Tracking\n", stale), "state_unreadable").params["reason"] == "header_only"
    first_sight = _one(ev(None), "state_unreadable")
    assert first_sight.severity == "info" and first_sight.params["elapsed_secs"] is None
    assert "state_unreadable" not in _codes(ev(poc_state, intent_dir=F.DEVDELTA_POC))
    # one root cause, one finding: a missing state file must not also claim count drift
    assert _codes(ev(None)) == ["state_unreadable"]


def test_interrupted_session(engine, make_snap, make_facts, poc_state):
    snap = make_snap(poc_state, intent_dir=F.DEVDELTA_POC)
    binding = BindingViewStub(intent_key=F.DEVDELTA_POC, interrupted_at="2026-09-04T09:58:00Z")

    def codes(**facts):
        return engine.evaluate_intent(snap, make_facts(**facts))

    finding = _one(codes(binding=binding, slot=None), "interrupted_session")
    assert finding.severity == "blocking" and finding.params["interrupted_at"] == "2026-09-04T09:58:00Z"
    assert finding.params["slot_present"] is False
    assert "interrupted_session" in _codes(codes(binding=binding, slot=SlotViewStub()))
    # still stopping: PRD §11.2 keeps the intent `Interrupted` until the slot is idle
    for busy in (SlotViewStub(running=True), SlotViewStub(stopping=True), SlotViewStub(stop_state="killing")):
        assert "interrupted_session" not in _codes(codes(binding=binding, slot=busy))
    assert "interrupted_session" not in _codes(codes(binding=BindingViewStub(intent_key=F.DEVDELTA_POC), slot=None))


@pytest.mark.parametrize("code,evidence,cleared", [
    ("human_presence_anomaly", {"findings": [{"code": "human_presence_anomaly", "params": {"human_turn_delta": 0}}]},
     {"presence": {"human_turn_delta": 1, "ok": True}}),
    ("cursor_moved", {"finding": "cursor_moved"}, {}),
    ("wrong_slot", {"findings": ["wrong_slot"], "transcript_row": {"slot_key": "someone-elses-slot"}},
     {"transcript_row": {"slot_key": "aidlc-studio-x"}}),
])
def test_findings_an_uncertain_action_carries(engine, poc_snap, make_facts, code, evidence, cleared):
    action = poc_action(status="ReconciliationRequired", evidence=evidence)
    finding = _one(engine.evaluate_intent(poc_snap, make_facts(live_actions=[action])), code)
    assert finding.severity == "blocking"
    if code == "human_presence_anomaly":
        assert finding.params["human_turn_delta"] == 0 and finding.params["action_id"] == action.action_id
    clean = replace(action, evidence=cleared)
    assert code not in _codes(engine.evaluate_intent(poc_snap, make_facts(live_actions=[clean])))


def test_cursor_mismatch(engine, poc_snap, make_facts):
    refused = poc_action(cursor_readback=CursorReadbackView(ok=False, mismatch=("dir_name",)))
    finding = _one(engine.evaluate_intent(poc_snap, make_facts(live_actions=[refused])), "cursor_mismatch")
    assert finding.severity == "blocking" and finding.params["mismatch"] == ["dir_name"]
    accepted = replace(refused, cursor_readback=CursorReadbackView(ok=True))
    assert "cursor_mismatch" not in _codes(engine.evaluate_intent(poc_snap, make_facts(live_actions=[accepted])))
    # reported once when it is also stored on the action
    both = poc_action(evidence={"findings": ["cursor_mismatch"]},
                      cursor_readback=CursorReadbackView(ok=False, mismatch=("uuid",)))
    assert _codes(engine.evaluate_intent(poc_snap, make_facts(live_actions=[both]))).count("cursor_mismatch") == 1


def test_legacy_layout(engine, make_facts, C):
    finding = _one(engine.evaluate_repo(make_facts(layout="legacy")), "legacy_layout")
    assert finding.severity == "warn" and finding.params["path"] == C.LEGACY_STATE_REL
    assert "legacy_layout" not in _codes(engine.evaluate_repo(make_facts(layout="spaces")))
    assert "legacy_layout" not in _codes(engine.evaluate_repo(make_facts()))


def test_unstable_read(engine, make_snap, make_facts, poc_state):
    snap = make_snap(poc_state, intent_dir=F.DEVDELTA_POC, stable=False)
    assert _one(engine.evaluate_intent(snap, make_facts()), "unstable_read").severity == "info"
    assert engine.unstable(snap) is True
    stable = make_snap(poc_state, intent_dir=F.DEVDELTA_POC)
    assert "unstable_read" not in _codes(engine.evaluate_intent(stable, make_facts()))
    assert engine.unstable(stable) is False


def test_engine_version_unknown(engine, make_facts, harness):
    finding = _one(engine.evaluate_repo(make_facts(harness_dirs=(harness(engine_version=None),))),
                   "engine_version_unknown")
    assert finding.severity == "warn" and finding.params["dir"] == ".kiro"
    assert "engine_version_unknown" not in _codes(engine.evaluate_repo(make_facts()))


def test_audit_incomplete(ev, poc_state, C):
    """A tail-read shard means the findings above stand on partial history — reported, not resolved."""
    truncated = AuditBundleView(events=(), complete=False, shards=(ShardMetaView(
        relpath="audit/603e5f4a8072-26e77d17b1cd.md", size=C.MAX_AUDIT_TAIL_BYTES + 1, truncated=True),))
    finding = _one(ev(poc_state, intent_dir=F.DEVDELTA_POC, audit=truncated), "audit_incomplete")
    assert finding.severity == "info" and finding.params["truncated"] == ["audit/603e5f4a8072-26e77d17b1cd.md"]
    assert "audit_incomplete" not in _codes(ev(poc_state, intent_dir=F.DEVDELTA_POC))


# --- real fixture sanity + the bridge to the real reader ---
def _real_stage_artifacts(tree, intent_dir: str, stage_rel: str) -> tuple:
    """The files that really sit in one stage's record directory."""
    stage_dir = F.state_path(tree, intent_dir).parent / stage_rel
    return tuple(ArtifactMetaView(relpath=f"aidlc/spaces/default/intents/{intent_dir}/{stage_rel}/{path.name}",
                                  name=path.name, sha256=F.sha256(path.read_text("utf-8")))
                 for path in sorted(stage_dir.iterdir()) if path.is_file())


REAL_GATE_ARTIFACTS = _real_stage_artifacts(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN, "inception/requirements-analysis")


def test_real_records_are_clean_under_their_own_engine(engine, ev, make_facts, gate_state, gate_audit, poc_state):
    """The healthy real records raise nothing blocking: the v7 one against its OWN graph and real stage files."""
    v7 = ev(gate_state, make_facts(engine_state_version=7), intent_dir=F.PAYLOAD_GATE_OPEN, audit_shards=(gate_audit,),
            graph=tuple(F.stage_graph(F.PAYLOAD_230)),
            stage_artifacts=REAL_GATE_ARTIFACTS)
    assert engine.blocking(v7) == [] and _codes(v7) == []
    v8 = ev(poc_state, intent_dir=F.DEVDELTA_POC, audit_shards=(F.real_audit(F.DEVDELTA, F.DEVDELTA_POC),))
    assert engine.blocking(v8) == []


def test_real_in_flight_record_across_two_shards_is_clean(ev):
    """The real 2.6.2 in-flight record (custom scope, matching directive), both shards merged: no finding at all."""
    shards = tuple(path.read_text("utf-8") for path in F.audit_paths(F.DEVDELTA, F.DEVDELTA_REMEDIATION))
    assert len(shards) == 2
    state = F.real_state(F.DEVDELTA, F.DEVDELTA_REMEDIATION)
    assert _codes(ev(state, intent_dir=F.DEVDELTA_REMEDIATION, intent_key=F.DEVDELTA_REMEDIATION, audit_shards=shards,
                     directive=_directive_of(F.DEVDELTA, F.DEVDELTA_REMEDIATION, state),
                     row=IntentRowView(uuid="01a00133-9901-7fdf-815d-5ae4e6e4be02", slug="review-major-remediation",
                                       dir_name=F.DEVDELTA_REMEDIATION, scope="review-major-remediation"))) == []


def test_findings_over_the_real_reader_dataclasses(studio, engine, make_facts, gate_state, gate_audit):
    """The rule set over ``aidlc_reader``'s own parse of the real v7 record: the views describe shipped dataclasses."""
    reader = studio.aidlc_reader
    record_rel = f"aidlc/spaces/default/intents/{F.PAYLOAD_GATE_OPEN}"
    shard_rel = f"{record_rel}/audit/fixture-host-0f1e2d3c4b5a.md"
    graph = reader.parse_stage_graph((F.PAYLOAD_230 / ".kiro" / "tools" / "data" / "stage-graph.json").read_bytes())
    row = reader.match_intent_row(reader.parse_intents_json((F.PAYLOAD_INTENTS / "intents.json").read_bytes()),
                                  F.PAYLOAD_GATE_OPEN)
    assert row is not None and graph

    def snapshot(state_text: str, audit_text: str):
        raw, digest, mtime = state_text.encode("utf-8"), F.sha256(state_text), 1_757_000_000_000_000_000
        bundle = reader.AuditBundle(events=reader.merge_audit([reader.parse_audit_shard(audit_text, shard_rel, 0)]),
                                    shards=[reader.ShardMeta(relpath=shard_rel, size=len(audit_text.encode("utf-8")),
                                                             mtime_ns=mtime, truncated=False)], complete=True)
        directive = reader.parse_directive(
            (F.PAYLOAD_INTENTS / F.PAYLOAD_GATE_OPEN / ".aidlc-active-directive.json").read_bytes(), digest)
        return SnapView(
            intent_dir=F.PAYLOAD_GATE_OPEN, intent_key=F.PAYLOAD_GATE_OPEN, row=row, directive=directive,
            state=reader.parse_state_file(state_text), audit=bundle, graph=tuple(graph), engine=None,
            state_snap=reader.Snapshot(relpath=f"{record_rel}/aidlc-state.md", bytes=raw, size=len(raw),
                                       mtime_ns=mtime, sha256=digest, unstable=False),
            stage_artifacts=REAL_GATE_ARTIFACTS)

    facts = make_facts(engine_state_version=7)
    clean = snapshot(gate_state, gate_audit)
    assert clean.directive is not None and clean.directive.matches_state is True
    assert _codes(engine.evaluate_intent(clean, facts)) == []
    superseded = _one(engine.evaluate_intent(snapshot(gate_state, gate_audit + started()), facts),
                      "gate_without_audit_row")
    assert superseded.severity == "blocking" and superseded.params["reason"] == "superseded"
    findings = engine.evaluate_intent(snapshot(in_progress_gate(), gate_audit), facts)
    assert "audit_gate_without_checkbox" in _codes(findings)
    # the directive digest is now stale against the patched state: reported as info, never blocking
    assert _one(findings, "directive_state_digest_mismatch").severity == "info"


def test_reader_state_parse_agrees_with_the_test_views(studio, engine, make_snap, make_facts, C):
    """The hand-built ``StateView`` and the shipped ``StateFile`` must yield the same findings."""
    text = F.state_text(fields={"Completed": "20", "Lifecycle Phase": "OPERATION"})
    real, mine = studio.aidlc_reader.parse_state_file(text), parse_state(text, C)
    assert mine.counts["completed"] == real.counts["completed"]
    assert mine.total_stages_claimed == real.total_stages_claimed

    # `phase` case differs by design (the reader normalises to the graph's spelling): comparisons are case-insensitive
    def rows(state):
        return [(r.slug, r.mark, r.state, (r.phase or "").lower(), r.suffix) for r in state.stages]

    assert rows(mine) == rows(real)
    assert [(name.lower(), value) for name, value in mine.phases] == [(name.lower(), v) for name, v in real.phases]
    facts = make_facts()
    from_real = _codes(engine.evaluate_intent(make_snap(text, intent_dir=F.DEVDELTA_POC, state=real), facts))
    assert from_real == _codes(engine.evaluate_intent(make_snap(text, intent_dir=F.DEVDELTA_POC), facts))


def test_projection_snapshot_is_accepted_by_the_engine(studio, engine, make_snap, make_facts, repo_builder, clock):
    """The real ``IntentSnapshot`` must produce exactly what the hand-built views produce over the same bytes."""
    intent_dir, uuid = "260904-bridge", "01a00000-0000-7000-8000-000000000001"
    # two disagreements, so the compared lists are not both empty: a phase the stage is not in, and a high `Completed`
    state = F.state_text(marks={"requirements-analysis": "?"}, fields={
        "Current Stage": "requirements-analysis", "Lifecycle Phase": "CONSTRUCTION", "Status": "Running",
        "Completed": "20"})
    audit = F.gate_open_audit("requirements-analysis")
    root = repo_builder.with_engine("payload").with_workspace().with_intent(
        intent_dir, state=state, audit=audit, slug="bridge", uuid=uuid).build()
    facts = make_facts()
    real_snap = studio.projection.Projection(studio.aidlc_reader.AidlcReader(clock), engine, clock).snapshot(
        repo_record(studio, clock, label="bridge", canonical_path=str(root)), "default", intent_dir)
    assert real_snap.state is not None and real_snap.stable is True
    view_snap = make_snap(state, audit_shards=[audit], intent_dir=intent_dir, intent_key=intent_dir, stage_artifacts=(),
                          row=IntentRowView(uuid=uuid, slug="bridge", dir_name=intent_dir, scope="poc"))
    from_real = _codes(engine.evaluate_intent(real_snap, facts))
    # spelled out so the comparison cannot degenerate: the two missing artifacts are the gate stage's `produces`
    assert from_real == ["missing_required_artifact", "missing_required_artifact", "phase_stage_disagreement",
                         "completed_count_mismatch"]
    assert from_real == _codes(engine.evaluate_intent(view_snap, facts))
