"""Tests for ``backend/studio/activity.py``.

Four properties carry the module, and most of this file is about them:

* **AI-DLC rows are projected, never stored.** Every timeline test that produces ``source="aidlc"``
  entries also asserts the ``activity`` table still holds nothing with that source, and ``record``
  refuses the source outright. That is the §1.17 invariant; a passing timeline with a polluted table
  would be a silent second authority for a lifecycle Studio only observes.
* **The merge preserves both sources and their order.** The audit half comes from the *real* harvested
  shards (including ``LOOP_RUN_STARTED``, which belongs to no engine taxonomy — FORMAT-NOTES §6), so
  the unknown-event fallback and the free-form field names are exercised by bytes a user really has.
* **Keys and params, never prose.** ``ACTIVITY_KINDS`` is compared against the list pasted from §1.17,
  and every kind is written at least once, so an added kind has to be added to the contract too.
* **Redaction happens where it leaks.** Credentials are masked on the way in; the export masks again and
  replaces absolute paths outside the registered roots; ``human_text`` needs an explicit opt-in.

The store is the real ``Storage`` and the audit bundles are built with the real ``AidlcReader`` parsers:
the module under test is ``activity``, and a fake row or a fake parse would only test the fake's idea of
the format. Async methods run through ``asyncio.run`` (the suite's convention; no async plugin).
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import fixtures as F

#: The FakeClock's start, so timestamps in assertions are exact.
NOW = "2026-09-04T10:00:00Z"
REPO = "r_000000000001"
INTENT = "260904-gate-demo"
SHARD = "603e5f4a8072-26e77d17b1cd.md"

#: The two catalogs the bundle imports (``ui/src/i18n/index.tsx``), merged from the parts by
#: ``scripts/build_i18n.py``.
I18N = Path(__file__).resolve().parent.parent / "ui" / "src" / "i18n"

#: A credential shape both the host redactor and Studio's fallback recognise.
SECRET = "AKIA1234567890ABCDEF"
#: An absolute path that belongs to no registered repository.
FOREIGN_PATH = "/Users/someone/private/notes/plan.md"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def A(studio):
    module = studio.activity
    assert module is not None, getattr(studio, "activity__error", "activity.py did not import")
    return module


@pytest.fixture
def K(studio):
    return studio.constants


@pytest.fixture
def R(studio):
    return studio.aidlc_reader


@pytest.fixture
def store(studio, fake_ctx, K, clock, ids):
    st = studio.storage.Storage(Path(fake_ctx.data_dir) / K.DB_FILENAME, clock=clock, ids=ids)
    st.open()
    yield st
    st.close()


@pytest.fixture
def proj(A, store, clock):
    return A.ActivityProjector(store, clock)


def run(coro):
    return asyncio.run(coro)


def bundle(R, text: str, *, shard: str = SHARD, complete: bool = True):
    """A real ``AuditBundle`` over one shard, parsed by the real parser."""
    events = R.merge_audit([R.parse_audit_shard(text, shard, 0)])
    meta = R.ShardMeta(relpath=f"audit/{shard}", size=len(text.encode()), mtime_ns=1, truncated=not complete)
    return R.AuditBundle(events=events, shards=[meta], complete=complete)


def snapshot(audit, *, repo_id: str = REPO, space: str = "default", intent_dir: str = INTENT):
    """The slice of ``IntentSnapshot`` (§1.8) the projector is allowed to touch.

    ``projection.py`` is not a dependency of this module — the timeline is duck-typed against the
    snapshot so it stays importable and testable on its own.
    """
    key = intent_dir if space == "default" else f"{space}~{intent_dir}"
    return SimpleNamespace(repo_id=repo_id, space=space, intent_dir=intent_dir, intent_key=key, audit=audit)


def raw_rows(store) -> list[dict]:
    """The ``activity`` table exactly as another process would read it."""
    return store.select("activity", order_by="id ASC")


# --------------------------------------------------------------------------- #
# the kind vocabulary
# --------------------------------------------------------------------------- #


#: Pasted from contracts §1.17. A literal, so adding a kind in code without adding it to the contract
#: (and therefore to the i18n catalog) fails here.
#:
#: ``slack.callback`` is the one member §1.17 does not list: §2.9 says ``POST /slack/actions/callback``
#: "Records an Activity row (``source: slack``, kind ``slack.callback``)", and the projector refuses any
#: kind outside this vocabulary — so with §1.17's list alone that route's contractual side effect could
#: never happen. The two sections are reconciled here in favour of the one that describes behaviour.
CONTRACT_KINDS = (
    "repo.added repo.removed repo.rebound repo.rescanned repo.updated action.created action.updated action.submitted "
    "action.delivery action.resolved action.cancelled action.retried action.failed breaker.opened "
    "breaker.reset lease.acquired lease.released lease.reclaimed engine.run install.transaction "
    "migration.previewed migration.applied advisor.requested advisor.auto_requested "
    "advisor.completed advisor.failed "
    "notification.sent slack.sent slack.callback session.bound session.unbound session.takeover "
    "intent.created intent.paused intent.resumed intent.archived intent.restored intent.force_stop "
    "machine_lane.refused settings.updated health.startup calibration.cleared"
).split()


def test_activity_kinds_match_the_contract_list(A):
    assert list(A.ACTIVITY_KINDS) == CONTRACT_KINDS


def test_every_kind_is_writable_and_keys_itself(A, proj, store):
    for kind in A.ACTIVITY_KINDS:
        run(proj.record(kind=kind, repo_id=REPO))
    rows, _ = run(proj.query(A.ActivityQuery(limit=500)))
    assert {row.message_key for row in rows} == {f"activity.{kind}" for kind in A.ACTIVITY_KINDS}
    # `lease.reclaimed` is written by Storage itself (§1.4) — the two writers must agree on the key.
    assert "activity.lease.reclaimed" in {row.message_key for row in rows}


def test_audit_event_keys_cover_the_engine_taxonomy_and_the_names_seen_on_disk(A, K):
    for name in (*K.MOVEMENT_EVENTS, *K.NOISE_EVENTS, *K.REVIEW_EVENTS, K.ERROR_EVENT):
        assert A.audit_message_key(name) == f"audit.{name}"
    # FORMAT-NOTES §6: real installs write these, no constants tuple lists them.
    for name in ("WORKFLOW_STARTED", "WORKSPACE_SCANNED", "RULE_LEARNED", "PHASE_SKIPPED", "WORKTREE_CREATED"):
        assert A.audit_message_key(name) == f"audit.{name}"
    # …and this one belongs to no taxonomy at all, so it must degrade instead of minting a dead key.
    assert A.audit_message_key("LOOP_RUN_STARTED") == "audit.unknown_event"
    assert A.audit_message_key("") == "audit.unknown_event"


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_every_classified_audit_event_has_a_sentence_in_both_catalogs(A, locale):
    """A classified event with no string renders as the raw key, in both locales.

    The taxonomy lives in ``constants.py``/``activity.py`` and the words live in
    ``ui/src/i18n/parts/activity.*.json``; nothing but this test binds the two, and a missing key renders
    as the key itself (``ui/src/i18n/index.tsx``) — ``audit.PLAN_APPROVAL_RECORDED`` on the timeline where
    a sentence belongs. AI-DLC 2.7.1 classified nine new event types at once, which is exactly how many
    raw keys the previous absence of this assertion would have shipped.
    """
    catalog = json.loads((I18N / f"{locale}.json").read_text("utf-8"))
    keys = {A.audit_message_key(name) for name in A.AUDIT_EVENTS} | {A.UNKNOWN_AUDIT_MESSAGE_KEY}
    assert A.UNKNOWN_AUDIT_MESSAGE_KEY in keys, "the fallback must be covered too, not just the taxonomy"
    assert sorted(key for key in keys if not str(catalog.get(key, "")).strip()) == []


# --------------------------------------------------------------------------- #
# record
# --------------------------------------------------------------------------- #


def test_record_writes_one_keyed_row(A, proj, store, clock):
    row_id = run(
        proj.record(
            kind="action.created",
            severity="blocking",
            repo_id=REPO,
            space="default",
            intent_dir=INTENT,
            stage="requirements-analysis",
            action_id="a_0000000000000001",
            session_key="dashboard:slot-1",
            params={"type": "gate"},
            evidence=[A.EvidenceRef(kind="audit", ref=f"{SHARD}#3", detail="STAGE_AWAITING_APPROVAL")],
        )
    )
    assert row_id == 1
    rows, cursor = run(proj.query(A.ActivityQuery()))
    assert cursor is None and len(rows) == 1
    row = rows[0]
    assert (row.at, row.source, row.kind, row.severity) == (NOW, "studio", "action.created", "blocking")
    assert row.message_key == "activity.action.created"
    assert row.params == {"type": "gate"}
    assert row.stage == "requirements-analysis" and row.session_key == "dashboard:slot-1"
    assert row.intent_key == INTENT                       # C02: bare dir name in the default space
    assert row.evidence[0].detail == "STAGE_AWAITING_APPROVAL"
    assert row.redacted is True


def test_record_derives_the_intent_key_for_a_named_space(A, proj):
    run(proj.record(kind="intent.created", repo_id=REPO, space="research", intent_dir=INTENT))
    rows, _ = run(proj.query(A.ActivityQuery()))
    assert rows[0].intent_key == f"research~{INTENT}"


def test_record_redacts_params_and_evidence(A, proj):
    run(
        proj.record(
            kind="engine.run",
            params={"stderr": f"aws configure --key {SECRET}", "exit_code": 1,
                    "nested": {"cmd": "export API_KEY=abcdef1234567890", "args": ["--profile", "prod"]}},
            evidence=[{"kind": "studio", "ref": "engine.stderr", "detail": f"bearer {SECRET}0000"}],
        )
    )
    rows, _ = run(proj.query(A.ActivityQuery()))
    blob = json.dumps(rows[0].to_json())
    assert SECRET not in blob
    assert "abcdef1234567890" not in blob                   # nested structures are walked, not skipped
    assert rows[0].params["exit_code"] == 1                 # non-strings survive untouched
    assert rows[0].params["nested"]["args"] == ["--profile", "prod"]


def test_record_keeps_human_text_only_for_the_submitted_kind(A, proj, store):
    run(proj.record(kind="action.submitted", repo_id=REPO, human_text="Request Changes: spell out the criteria"))
    run(proj.record(kind="action.resolved", repo_id=REPO, human_text="should never be kept"))
    kept = {row["kind"]: row["human_text"] for row in raw_rows(store)}
    assert kept["action.submitted"] == "Request Changes: spell out the criteria"
    assert kept["action.resolved"] is None


def test_record_refuses_to_store_an_aidlc_row(A, proj, store, studio):
    with pytest.raises(studio.errors.StudioError) as exc:
        run(proj.record(kind="action.resolved", source="aidlc", repo_id=REPO))
    assert exc.value.code == "internal_error"
    assert raw_rows(store) == []


def test_record_refuses_an_unknown_kind_severity_or_source(A, proj, store, studio):
    for kwargs in (
        {"kind": "action.exploded"},
        {"kind": "action.created", "severity": "urgent"},
        {"kind": "action.created", "source": "somewhere"},
    ):
        with pytest.raises(studio.errors.StudioError) as exc:
            run(proj.record(**kwargs))
        assert exc.value.code == "internal_error"
    assert raw_rows(store) == []


def test_record_accepts_every_non_aidlc_source(A, proj, K):
    for source in K.SOURCE:
        if source == "aidlc":
            continue
        run(proj.record(kind="notification.sent", source=source, repo_id=REPO))
    rows, _ = run(proj.query(A.ActivityQuery(limit=500)))
    assert {row.source for row in rows} == set(K.SOURCE) - {"aidlc"}


def test_record_coerces_a_param_it_cannot_serialise(A, proj):
    run(proj.record(kind="install.transaction", params={"staging": Path("/FIXTURE/repo/.aidlc-staging")}))
    rows, _ = run(proj.query(A.ActivityQuery()))
    assert rows[0].params["staging"] == "/FIXTURE/repo/.aidlc-staging"


def test_record_sync_is_the_seam_the_engine_runner_uses(A, studio, proj, clock):
    """The runner writes its rows from a worker thread, so it probes for a synchronous seam.

    Driving the real ``EngineRunner._record_run`` rather than hand-building its row keeps the two
    modules' idea of an ``engine.run`` row from drifting apart silently.
    """
    engine = studio.engine
    assert engine is not None, getattr(studio, "engine__error", "engine.py did not import")
    runner = engine.EngineRunner(bun_path=None, clock=clock, activity=proj)
    result = engine.EngineResult(
        verb_key="utility.version",
        argv=("bun", "run", "<path>/aidlc-utility.ts", "version"),
        exit_code=1,
        timed_out=False,
        duration_ms=12,
        stdout="aidlc 2.6.2",
        stderr="warning: token=abcdef1234567890",
        json=None,
        ok=False,
        side_effects=("audit_append",),
    )
    runner._record_run(result, repo_id=REPO)
    assert runner.pending_activity == 0                    # written immediately, not buffered
    rows, _ = run(proj.query(A.ActivityQuery()))
    assert rows[0].kind == "engine.run" and rows[0].severity == "attention"
    assert rows[0].params["verb_key"] == "utility.version"
    assert rows[0].params["side_effects"] == ["audit_append"]
    details = [ref.detail for ref in rows[0].evidence]
    assert "aidlc 2.6.2" in details
    assert "abcdef1234567890" not in json.dumps(rows[0].to_json())


def test_record_and_query_never_touch_sqlite_on_the_event_loop(A, store, clock):
    class ThreadRecordingStore:
        """Delegates to the real ``Storage`` and records which thread each call arrived on."""

        def __init__(self, inner):
            self._inner = inner
            self.threads: list[str] = []

        def activity_append(self, row):
            self.threads.append(threading.current_thread().name)
            return self._inner.activity_append(row)

        def activity_query(self, q):
            self.threads.append(threading.current_thread().name)
            return self._inner.activity_query(q)

        def select(self, *args, **kwargs):
            self.threads.append(threading.current_thread().name)
            return self._inner.select(*args, **kwargs)

    recorder = ThreadRecordingStore(store)
    projector = A.ActivityProjector(recorder, clock)

    async def scenario():
        await projector.record(kind="health.startup")
        await projector.query(A.ActivityQuery())
        await projector.export(A.ActivityQuery(), include_human_text=False, allowed_roots=())
        return threading.current_thread().name

    loop_thread = run(scenario())
    assert recorder.threads and loop_thread not in recorder.threads


# --------------------------------------------------------------------------- #
# query and paging
# --------------------------------------------------------------------------- #


def test_query_pages_by_cursor_without_gaps_or_repeats(A, proj):
    for n in range(7):
        run(proj.record(kind="repo.rescanned", repo_id=REPO, params={"n": n}))
    seen: list[int] = []
    cursor = None
    pages = 0
    while True:
        rows, cursor = run(proj.query(A.ActivityQuery(limit=3, cursor=cursor)))
        pages += 1
        seen.extend(row.id for row in rows)
        if cursor is None:
            break
    assert pages == 3
    assert seen == [7, 6, 5, 4, 3, 2, 1]                   # newest first, every row exactly once


def test_query_rejects_a_malformed_cursor(A, proj, studio):
    with pytest.raises(studio.errors.StudioError) as exc:
        run(proj.query(A.ActivityQuery(cursor="not-base64-@@")))
    assert exc.value.code == "bad_param"


def test_query_filters_pass_through_to_the_store(A, proj, clock):
    run(proj.record(kind="action.created", repo_id=REPO, space="default", intent_dir=INTENT,
                    stage="requirements-analysis"))
    clock.advance(120)
    run(proj.record(kind="repo.added", repo_id="r_000000000002", severity="attention"))
    q = A.ActivityQuery
    assert [r.kind for r in run(proj.query(q(repo_id=REPO)))[0]] == ["action.created"]
    assert [r.kind for r in run(proj.query(q(severity="attention")))[0]] == ["repo.added"]
    assert [r.kind for r in run(proj.query(q(stage="requirements-analysis")))[0]] == ["action.created"]
    assert [r.kind for r in run(proj.query(q(since=clock.iso())))[0]] == ["repo.added"]
    assert run(proj.query(q(source="aidlc")))[0] == []     # nothing with that source is ever stored


# --------------------------------------------------------------------------- #
# severity mapping
# --------------------------------------------------------------------------- #


def test_severity_of_finding_maps_the_finding_vocabulary_onto_the_shared_one(A, studio):
    make = studio.consistency.Finding
    for severity, expected in (("blocking", "blocking"), ("warn", "attention"), ("info", "info")):
        finding = make(code="receipt_drift", severity=severity, message_key="finding.receipt_drift",
                       params={}, evidence=())
        assert A.severity_of_finding(finding) == expected
    assert A.severity_of_finding({"severity": "warn"}) == "attention"
    assert A.severity_of_finding({"severity": "nonsense"}) == "info"


def test_severity_of_audit_event_flags_only_errors_and_failed_sensors(A, R, K):
    events = R.parse_audit_shard(
        F.audit_text(
            F.audit_block("ERROR_LOGGED", NOW, Error="ACP transport closed"),
            F.audit_block("SENSOR_FAILED", NOW, Sensor_ID="artifact-guard"),
            F.audit_block("GATE_APPROVED", NOW, Stage="requirements-analysis"),
            F.audit_block("QUESTION_ANSWERED", NOW, Stage="requirements-analysis"),
            F.audit_block("STAGE_STARTED", NOW, Stage="user-stories"),
        ),
        SHARD,
        0,
    )
    assert [A.severity_of_audit_event(e) for e in events] == ["attention", "attention", "info", "info", "info"]
    assert all(sev in K.SEVERITY for sev in {A.severity_of_audit_event(e) for e in events})


# --------------------------------------------------------------------------- #
# timeline
# --------------------------------------------------------------------------- #


def test_timeline_merges_both_sources_newest_first_and_stores_neither(A, R, proj, store):
    run(proj.record(kind="action.submitted", repo_id=REPO, space="default", intent_dir=INTENT,
                    stage="requirements-analysis", action_id="a_0000000000000001",
                    human_text="Approve"))
    audit = bundle(
        R,
        F.audit_text(
            F.audit_block("STAGE_AWAITING_APPROVAL", "2026-09-04T09:00:00Z", Stage="requirements-analysis"),
            F.audit_block("GATE_APPROVED", "2026-09-04T11:00:00Z", Stage="requirements-analysis",
                          User_Input="Approve"),
        ),
    )
    entries = run(proj.timeline(snapshot(audit)))
    assert [(e.source, e.at) for e in entries] == [
        ("aidlc", "2026-09-04T11:00:00Z"),
        ("studio", NOW),
        ("aidlc", "2026-09-04T09:00:00Z"),
    ]
    assert [e.message_key for e in entries] == [
        "audit.GATE_APPROVED",
        "activity.action.submitted",
        "audit.STAGE_AWAITING_APPROVAL",
    ]
    # the invariant: the projected rows were never written
    assert [row["source"] for row in raw_rows(store)] == ["studio"]
    assert run(proj.query(A.ActivityQuery(source="aidlc")))[0] == []


def test_timeline_entry_json_matches_the_wire_shape(A, R, proj):
    run(proj.record(kind="action.resolved", repo_id=REPO, space="default", intent_dir=INTENT,
                    action_id="a_0000000000000001", session_key="dashboard:slot-1",
                    evidence=[{"kind": "audit", "ref": f"{SHARD}#7", "detail": "GATE_APPROVED"},
                              {"kind": "git", "ref": "commit:9f8e7d6"}]))
    audit = bundle(R, F.gate_open_audit("requirements-analysis"))
    entries = run(proj.timeline(snapshot(audit)))
    for entry in entries:
        assert set(entry.to_json()) == {
            "at", "source", "kind", "message_key", "params", "severity", "refs", "raw", "id"
        }
    studio_entry = next(e for e in entries if e.source == "studio")
    assert studio_entry.id == 1 and studio_entry.raw is None
    assert studio_entry.refs == {
        "action_id": "a_0000000000000001",
        "session_key": "dashboard:slot-1",
        "audit": {"shard": SHARD, "pos": 7, "event": "GATE_APPROVED"},
        "git": {"sha": "9f8e7d6"},
    }
    aidlc_entry = next(e for e in entries if e.source == "aidlc")
    assert aidlc_entry.id is None
    assert aidlc_entry.refs["audit"]["shard"] == SHARD
    assert aidlc_entry.refs["audit"]["event"] == aidlc_entry.kind


def test_timeline_carries_the_verbatim_audit_block_for_the_evidence_drawer(A, R, proj):
    text = F.audit_block("GATE_APPROVED", NOW, Stage="requirements-analysis", User_Input="Approve")
    entries = run(proj.timeline(snapshot(bundle(R, text))))
    assert len(entries) == 1
    raw = entries[0].raw
    assert raw.startswith("## Gate Approved")
    assert "**Event**: GATE_APPROVED" in raw and "**User Input**: Approve" in raw


def test_timeline_redacts_a_credential_pasted_into_a_gate_response(A, R, proj):
    text = F.audit_block("GATE_APPROVED", NOW, Stage="build-and-test",
                         User_Input=f"Approve, and use {SECRET} for the deploy")
    entries = run(proj.timeline(snapshot(bundle(R, text))))
    assert SECRET not in entries[0].raw
    assert SECRET not in json.dumps(entries[0].params)


def test_timeline_projects_real_audit_fields_as_snake_case_params(A, R, proj):
    """The real 2.1.1 shard: free-form field names, ``Candidate-ID``, and an out-of-taxonomy event."""
    audit = bundle(R, F.real_audit(F.OLDER, F.OLDER_INTENT), shard="603e5f4a8072-4c59c6cec037.md")
    entries = run(proj.timeline(snapshot(audit), limit=500))

    rule = next(e for e in entries if e.kind == "RULE_LEARNED")
    assert rule.message_key == "audit.RULE_LEARNED"
    assert rule.params["candidate_id"]                     # "Candidate-ID" → interpolatable key
    assert rule.params["destination"].endswith("memory/project.md")
    assert rule.params["event"] == "RULE_LEARNED"

    loop = next(e for e in entries if e.kind == "LOOP_RUN_STARTED")
    assert loop.message_key == "audit.unknown_event"
    assert loop.params["event"] == "LOOP_RUN_STARTED"       # the fallback string can still name it

    assert all(key == key.lower() and " " not in key and "-" not in key
               for entry in entries for key in entry.params)


def test_timeline_caps_a_long_audit_field(A, R, proj):
    long_feedback = "x" * (A.AUDIT_PARAM_MAX_CHARS + 500)
    text = F.audit_block("GATE_REJECTED", NOW, Stage="requirements-analysis", Feedback=long_feedback)
    entries = run(proj.timeline(snapshot(bundle(R, text))))
    assert len(entries[0].params["feedback"]) == A.AUDIT_PARAM_MAX_CHARS + 1     # capped + the ellipsis
    assert long_feedback in entries[0].raw                                       # the full block is intact


def test_timeline_filters_by_source(A, R, proj):
    run(proj.record(kind="session.bound", repo_id=REPO, space="default", intent_dir=INTENT))
    run(proj.record(kind="slack.sent", source="slack", repo_id=REPO, space="default", intent_dir=INTENT))
    audit = bundle(R, F.gate_open_audit("requirements-analysis"))
    snap = snapshot(audit)

    assert {e.source for e in run(proj.timeline(snap))} == {"studio", "slack", "aidlc"}
    assert {e.source for e in run(proj.timeline(snap, sources=["aidlc"]))} == {"aidlc"}
    assert {e.source for e in run(proj.timeline(snap, sources=["studio"]))} == {"studio"}
    assert {e.source for e in run(proj.timeline(snap, sources=["studio", "slack"]))} == {"studio", "slack"}
    assert run(proj.timeline(snap, sources=["git"])) == []


def test_timeline_keeps_only_this_intents_rows(A, R, proj):
    run(proj.record(kind="action.created", repo_id=REPO, space="default", intent_dir=INTENT))
    run(proj.record(kind="action.created", repo_id=REPO, space="default", intent_dir="260901-other"))
    run(proj.record(kind="action.created", repo_id="r_000000000002", space="default", intent_dir=INTENT))
    run(proj.record(kind="action.created", repo_id=REPO, space="research", intent_dir=INTENT))
    entries = run(proj.timeline(snapshot(bundle(R, "")), sources=["studio"]))
    assert len(entries) == 1


def test_timeline_returns_the_newest_rows_within_its_limit(A, R, proj):
    text = F.audit_text(
        *[F.audit_block("STAGE_STARTED", f"2026-09-04T09:{n:02d}:00Z", Stage=f"stage-{n}") for n in range(30)]
    )
    entries = run(proj.timeline(snapshot(bundle(R, text)), limit=5))
    assert [e.params["stage"] for e in entries] == [f"stage-{n}" for n in (29, 28, 27, 26, 25)]


def test_timeline_surfaces_the_stage_of_a_studio_row_in_params(A, R, proj):
    run(proj.record(kind="action.created", repo_id=REPO, space="default", intent_dir=INTENT,
                    stage="requirements-analysis", params={"type": "gate"}))
    entry = run(proj.timeline(snapshot(bundle(R, "")), sources=["studio"]))[0]
    assert entry.params == {"type": "gate", "stage": "requirements-analysis"}


def test_timeline_tolerates_an_intent_with_no_audit_at_all(A, R, proj):
    run(proj.record(kind="intent.created", repo_id=REPO, space="default", intent_dir=INTENT))
    assert len(run(proj.timeline(snapshot(bundle(R, ""))))) == 1
    assert run(proj.timeline(snapshot(None), sources=["aidlc"])) == []


def test_timeline_orders_same_second_audit_rows_by_position(A, R, proj):
    """Second-precision timestamps tie constantly; the shard's own order has to survive the merge."""
    text = F.audit_text(
        F.audit_block("HUMAN_TURN", NOW),
        F.audit_block("GATE_APPROVED", NOW, Stage="requirements-analysis"),
        F.audit_block("STAGE_COMPLETED", NOW, Stage="requirements-analysis"),
    )
    entries = run(proj.timeline(snapshot(bundle(R, text))))
    assert [e.kind for e in entries] == ["STAGE_COMPLETED", "GATE_APPROVED", "HUMAN_TURN"]


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #


def seed_export_rows(proj, store):
    store.insert("repos", {"id": REPO, "resolved_identity": "1:2", "canonical_path": "/FIXTURE/repo",
                           "label": "repo", "added_at": NOW, "platform": "darwin"})
    run(
        proj.record(
            kind="action.submitted",
            repo_id=REPO,
            space="default",
            intent_dir=INTENT,
            stage="requirements-analysis",
            action_id="a_0000000000000001",
            params={"wire_text": "Approve", "artifact": "/FIXTURE/repo/aidlc/spaces/default/intents/x/y.md",
                    "elsewhere": FOREIGN_PATH},
            human_text="Approve — and the key is not here",
        )
    )


def test_export_envelope_is_the_contract_shape(A, proj, store, K):
    seed_export_rows(proj, store)
    out = run(proj.export(A.ActivityQuery(), include_human_text=False, allowed_roots=["/FIXTURE/repo"]))
    assert set(out) == {"generated_at", "app_version", "redacted", "human_text_included", "rows", "repos"}
    assert out["generated_at"] == NOW
    assert out["app_version"] == K.APP_VERSION
    assert out["redacted"] is True
    assert out["human_text_included"] is False
    assert out["repos"] == [{"repo_id": REPO, "label": "repo"}]
    assert set(out["rows"][0]) == set(A.ActivityRow().to_json())


def test_export_withholds_human_text_unless_asked(A, proj, store):
    seed_export_rows(proj, store)
    without = run(proj.export(A.ActivityQuery(), include_human_text=False, allowed_roots=["/FIXTURE/repo"]))
    assert without["rows"][0]["human_text"] is None        # present and null: withheld, not "there was none"
    assert "Approve — and the key is not here" not in json.dumps(without)

    with_text = run(proj.export(A.ActivityQuery(), include_human_text=True, allowed_roots=["/FIXTURE/repo"]))
    assert with_text["human_text_included"] is True
    assert with_text["rows"][0]["human_text"] == "Approve — and the key is not here"


def test_export_scrubs_paths_outside_the_registered_roots(A, proj, store):
    seed_export_rows(proj, store)
    out = run(proj.export(A.ActivityQuery(), include_human_text=True, allowed_roots=["/FIXTURE/repo"]))
    params = out["rows"][0]["params"]
    assert params["artifact"] == "/FIXTURE/repo/aidlc/spaces/default/intents/x/y.md"   # the repo's own path stays
    assert params["elsewhere"] == "<path>"                                             # the user's disk does not
    assert FOREIGN_PATH not in json.dumps(out)


def test_export_scrubs_every_path_when_no_root_is_allowed(A, proj, store):
    seed_export_rows(proj, store)
    out = run(proj.export(A.ActivityQuery(), include_human_text=True, allowed_roots=[]))
    assert out["rows"][0]["params"]["artifact"] == "<path>"


def test_export_redacts_credentials_in_every_field(A, proj, store):
    store.insert("repos", {"id": REPO, "resolved_identity": "1:2", "canonical_path": "/FIXTURE/repo",
                           "label": "repo", "added_at": NOW, "platform": "darwin"})
    run(
        proj.record(
            kind="action.submitted",
            repo_id=REPO,
            params={"note": f"deploy with {SECRET}"},
            evidence=[{"kind": "studio", "ref": "engine.stderr", "detail": f"AWS_SECRET_ACCESS_KEY={SECRET}"}],
            human_text=f"Approve, credentials are {SECRET}",
        )
    )
    out = run(proj.export(A.ActivityQuery(), include_human_text=True, allowed_roots=["/FIXTURE/repo"]))
    assert SECRET not in json.dumps(out)
    # the verbatim text is only redacted on the way out; the retention purge owns the stored copy
    assert SECRET in (raw_rows(store)[0]["human_text"] or "")


def test_export_honours_the_query_filters_and_page_size(A, proj, store):
    for n in range(5):
        run(proj.record(kind="repo.rescanned", repo_id=REPO, params={"n": n}))
    run(proj.record(kind="repo.added", repo_id="r_000000000002"))
    out = run(proj.export(A.ActivityQuery(kind="repo.rescanned", limit=2), include_human_text=False,
                          allowed_roots=[]))
    assert [row["params"]["n"] for row in out["rows"]] == [4, 3]
    assert out["repos"] == []                              # r_000000000001 is not in the repos table here


def test_export_lists_only_the_repos_the_rows_reference(A, proj, store):
    for repo_id, label in ((REPO, "alpha"), ("r_000000000002", "beta"), ("r_000000000003", "gamma")):
        store.insert("repos", {"id": repo_id, "resolved_identity": f"1:{repo_id}",
                               "canonical_path": f"/FIXTURE/repo/{label}", "label": label,
                               "added_at": NOW, "platform": "darwin"})
    run(proj.record(kind="repo.added", repo_id="r_000000000002"))
    run(proj.record(kind="repo.added", repo_id=REPO))
    out = run(proj.export(A.ActivityQuery(), include_human_text=False, allowed_roots=[]))
    assert out["repos"] == [{"repo_id": REPO, "label": "alpha"},
                            {"repo_id": "r_000000000002", "label": "beta"}]
