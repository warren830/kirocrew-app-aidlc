"""Activity (``backend/studio/activity.py``): AI-DLC rows are projected, never stored; the timeline merges both
sources in order; rows carry keys and params, never prose; and redaction happens wherever text can leak.

The store is the real ``Storage`` and audit bundles come from the real ``AidlcReader`` parsers over real shards.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import fixtures as F
from _helpers_c import ThreadRecordingStore

NOW = "2026-09-04T10:00:00Z"
REPO = "r_000000000001"
INTENT = "260904-gate-demo"
SHARD = "603e5f4a8072-26e77d17b1cd.md"
#: The two catalogs the bundle imports, merged from the parts by ``scripts/build_i18n.py``.
I18N = Path(__file__).resolve().parent.parent / "ui" / "src" / "i18n"
#: A credential shape both the host redactor and Studio's fallback recognise.
SECRET = "AKIA1234567890ABCDEF"
#: An absolute path that belongs to no registered repository.
FOREIGN_PATH = "/Users/someone/private/notes/plan.md"


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
    st = studio.storage.Storage(Path(fake_ctx.data_dir) / K.DB_FILENAME, clock=clock)
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
    """The ``IntentSnapshot`` slice (§1.8) the projector may touch: duck-typed, so ``projection`` is no dependency."""
    key = intent_dir if space == "default" else f"{space}~{intent_dir}"
    return SimpleNamespace(repo_id=repo_id, space=space, intent_dir=intent_dir, intent_key=key, audit=audit)


def raw_rows(store) -> list[dict]:
    """The ``activity`` table exactly as another process would read it."""
    return store.select("activity", order_by="id ASC")


def rows(A, proj, **query):
    return run(proj.query(A.ActivityQuery(**query)))[0]


def add_repo(store, repo_id: str = REPO, label: str = "repo", path: str = "/FIXTURE/repo") -> None:
    store.insert("repos", {"id": repo_id, "resolved_identity": f"1:{repo_id}", "canonical_path": path,
                           "label": label, "added_at": NOW, "platform": "darwin"})


# ---- the kind vocabulary ---- #


#: Pasted from contracts §1.17, so a kind added in code without the contract (and the i18n catalog) fails here.
#: ``slack.callback`` comes from §2.9, which says that route records exactly such a row.
CONTRACT_KINDS = (
    "repo.added repo.removed repo.rebound repo.rescanned repo.updated action.created action.updated action.submitted "
    "action.delivery action.resolved action.cancelled action.retried action.failed breaker.opened "
    "breaker.reset lease.acquired lease.released lease.reclaimed engine.run install.transaction "
    "advisor.requested advisor.auto_requested "
    "advisor.completed advisor.failed "
    "notification.sent slack.sent slack.callback session.bound session.unbound session.takeover "
    "intent.created intent.paused intent.resumed intent.archived intent.restored intent.force_stop "
    "settings.updated health.startup conversation.error"
).split()


def test_activity_kinds_match_the_contract_list(A):
    assert list(A.ACTIVITY_KINDS) == CONTRACT_KINDS


def test_every_kind_is_writable_and_keys_itself(A, proj):
    for kind in A.ACTIVITY_KINDS:
        run(proj.record(kind=kind, repo_id=REPO))
    keys = {row.message_key for row in rows(A, proj, limit=500)}
    assert keys == {f"activity.{kind}" for kind in A.ACTIVITY_KINDS}
    # `lease.reclaimed` is written by Storage itself (§1.4): the two writers must agree on the key.
    assert "activity.lease.reclaimed" in keys


def test_audit_event_keys_cover_the_engine_taxonomy_and_the_names_seen_on_disk(A, K):
    for name in (*K.MOVEMENT_EVENTS, *K.NOISE_EVENTS, *K.REVIEW_EVENTS, K.ERROR_EVENT):
        assert A.audit_message_key(name) == f"audit.{name}"
    # FORMAT-NOTES §6: real installs write these, and no constants tuple lists them.
    for name in ("WORKFLOW_STARTED", "WORKSPACE_SCANNED", "RULE_LEARNED", "PHASE_SKIPPED", "WORKTREE_CREATED"):
        assert A.audit_message_key(name) == f"audit.{name}"
    # This one belongs to no taxonomy at all, so it degrades instead of minting a dead key.
    assert A.audit_message_key("LOOP_RUN_STARTED") == "audit.unknown_event"
    assert A.audit_message_key("") == "audit.unknown_event"


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_every_classified_audit_event_has_a_sentence_in_both_catalogs(A, locale):
    """Nothing else binds the taxonomy to ``ui/src/i18n``, and a missing key renders as the raw key."""
    catalog = json.loads((I18N / f"{locale}.json").read_text("utf-8"))
    keys = {A.audit_message_key(name) for name in A.AUDIT_EVENTS} | {A.UNKNOWN_AUDIT_MESSAGE_KEY}
    assert A.UNKNOWN_AUDIT_MESSAGE_KEY in keys, "the fallback must be covered too, not just the taxonomy"
    assert sorted(key for key in keys if not str(catalog.get(key, "")).strip()) == []


# ---- record ---- #


def test_record_writes_one_keyed_row(A, proj):
    row_id = run(proj.record(
        kind="action.created", severity="blocking", repo_id=REPO, space="default", intent_dir=INTENT,
        stage="requirements-analysis", action_id="a_0000000000000001", session_key="dashboard:slot-1",
        params={"type": "gate"},
        evidence=[A.EvidenceRef(kind="audit", ref=f"{SHARD}#3", detail="STAGE_AWAITING_APPROVAL")],
    ))
    assert row_id == 1
    page, cursor = run(proj.query(A.ActivityQuery()))
    assert cursor is None and len(page) == 1
    row = page[0]
    assert (row.at, row.source, row.kind, row.severity) == (NOW, "studio", "action.created", "blocking")
    assert row.message_key == "activity.action.created"
    assert row.params == {"type": "gate"}
    assert row.stage == "requirements-analysis" and row.session_key == "dashboard:slot-1"
    assert row.intent_key == INTENT  # C02: bare dir name in the default space
    assert row.evidence[0].detail == "STAGE_AWAITING_APPROVAL"
    assert row.redacted is True


def test_record_derives_the_intent_key_for_a_named_space(A, proj):
    run(proj.record(kind="intent.created", repo_id=REPO, space="research", intent_dir=INTENT))
    assert rows(A, proj)[0].intent_key == f"research~{INTENT}"


def test_record_redacts_params_and_evidence(A, proj):
    run(proj.record(
        kind="engine.run",
        params={"stderr": f"aws configure --key {SECRET}", "exit_code": 1,
                "nested": {"cmd": "export API_KEY=abcdef1234567890", "args": ["--profile", "prod"]}},
        evidence=[{"kind": "studio", "ref": "engine.stderr", "detail": f"bearer {SECRET}0000"}],
    ))
    row = rows(A, proj)[0]
    blob = json.dumps(row.to_json())
    assert SECRET not in blob
    assert "abcdef1234567890" not in blob  # nested structures are walked, not skipped
    assert row.params["exit_code"] == 1  # non-strings survive untouched
    assert row.params["nested"]["args"] == ["--profile", "prod"]


def test_record_keeps_human_text_only_for_the_submitted_kind(proj, store):
    run(proj.record(kind="action.submitted", repo_id=REPO, human_text="Request Changes: spell out the criteria"))
    run(proj.record(kind="action.resolved", repo_id=REPO, human_text="should never be kept"))
    kept = {row["kind"]: row["human_text"] for row in raw_rows(store)}
    assert kept["action.submitted"] == "Request Changes: spell out the criteria"
    assert kept["action.resolved"] is None


@pytest.mark.parametrize("kwargs", [
    {"kind": "action.resolved", "source": "aidlc", "repo_id": REPO},  # §1.17: projected, never stored
    {"kind": "action.exploded"},
    {"kind": "action.created", "severity": "urgent"},
    {"kind": "action.created", "source": "somewhere"},
])
def test_record_refuses_an_aidlc_row_and_an_unknown_kind_severity_or_source(proj, store, studio, kwargs):
    with pytest.raises(studio.errors.StudioError) as exc:
        run(proj.record(**kwargs))
    assert exc.value.code == "internal_error"
    assert raw_rows(store) == []


def test_record_accepts_every_non_aidlc_source(A, proj, K):
    for source in K.SOURCE:
        if source != "aidlc":
            run(proj.record(kind="notification.sent", source=source, repo_id=REPO))
    assert {row.source for row in rows(A, proj, limit=500)} == set(K.SOURCE) - {"aidlc"}


def test_record_coerces_a_param_it_cannot_serialise(A, proj):
    run(proj.record(kind="install.transaction", params={"staging": Path("/FIXTURE/repo/.aidlc-staging")}))
    assert rows(A, proj)[0].params["staging"] == "/FIXTURE/repo/.aidlc-staging"


def test_record_sync_is_the_seam_the_engine_runner_uses(A, studio, proj, clock):
    """Driving the real ``EngineRunner._record_run`` keeps the two modules' ``engine.run`` rows in step."""
    engine = studio.engine
    assert engine is not None, getattr(studio, "engine__error", "engine.py did not import")
    runner = engine.EngineRunner(bun_path=None, clock=clock, activity=proj)
    runner._record_run(engine.EngineResult(
        verb_key="utility.version", argv=("bun", "run", "<path>/aidlc-utility.ts", "version"), exit_code=1,
        timed_out=False, duration_ms=12, stdout="aidlc 2.6.2", stderr="warning: token=abcdef1234567890", json=None,
        ok=False, side_effects=("audit_append",),
    ), repo_id=REPO)
    row = rows(A, proj)[0]
    assert row.kind == "engine.run" and row.severity == "attention"
    assert row.params["verb_key"] == "utility.version"
    assert row.params["side_effects"] == ["audit_append"]
    assert "aidlc 2.6.2" in [ref.detail for ref in row.evidence]
    assert "abcdef1234567890" not in json.dumps(row.to_json())


def test_record_and_query_never_touch_sqlite_on_the_event_loop(A, store, clock):
    recorder = ThreadRecordingStore(store)
    projector = A.ActivityProjector(recorder, clock)

    async def scenario():
        await projector.record(kind="health.startup")
        await projector.query(A.ActivityQuery())
        await projector.export(A.ActivityQuery(), include_human_text=False, allowed_roots=())
        return threading.current_thread().name

    loop_thread = run(scenario())
    assert recorder.threads and loop_thread not in recorder.threads


# ---- query, paging and severity ---- #


def test_query_pages_by_cursor_without_gaps_or_repeats(A, proj):
    for n in range(7):
        run(proj.record(kind="repo.rescanned", repo_id=REPO, params={"n": n}))
    seen: list[int] = []
    cursor = None
    pages = 0
    while True:
        page, cursor = run(proj.query(A.ActivityQuery(limit=3, cursor=cursor)))
        pages += 1
        seen.extend(row.id for row in page)
        if cursor is None:
            break
    assert pages == 3
    assert seen == [7, 6, 5, 4, 3, 2, 1]  # newest first, every row exactly once


def test_query_rejects_a_malformed_cursor(A, proj, studio):
    with pytest.raises(studio.errors.StudioError) as exc:
        run(proj.query(A.ActivityQuery(cursor="not-base64-@@")))
    assert exc.value.code == "bad_param"


def test_query_filters_pass_through_to_the_store(A, proj, clock):
    run(proj.record(kind="action.created", repo_id=REPO, space="default", intent_dir=INTENT,
                    stage="requirements-analysis"))
    clock.advance(120)
    run(proj.record(kind="repo.added", repo_id="r_000000000002", severity="attention"))
    assert [r.kind for r in rows(A, proj, repo_id=REPO)] == ["action.created"]
    assert [r.kind for r in rows(A, proj, severity="attention")] == ["repo.added"]
    assert [r.kind for r in rows(A, proj, stage="requirements-analysis")] == ["action.created"]
    assert [r.kind for r in rows(A, proj, since=clock.iso())] == ["repo.added"]
    assert rows(A, proj, source="aidlc") == []  # nothing with that source is ever stored


def test_severity_of_audit_event_flags_only_errors_and_failed_sensors(A, R, K):
    events = R.parse_audit_shard(F.audit_text(
        F.audit_block("ERROR_LOGGED", NOW, Error="ACP transport closed"),
        F.audit_block("SENSOR_FAILED", NOW, Sensor_ID="artifact-guard"),
        F.audit_block("GATE_APPROVED", NOW, Stage="requirements-analysis"),
        F.audit_block("QUESTION_ANSWERED", NOW, Stage="requirements-analysis"),
        F.audit_block("STAGE_STARTED", NOW, Stage="user-stories"),
    ), SHARD, 0)
    assert [A.severity_of_audit_event(e) for e in events] == ["attention", "attention", "info", "info", "info"]
    assert all(sev in K.SEVERITY for sev in {A.severity_of_audit_event(e) for e in events})


# ---- timeline ---- #


def test_timeline_merges_both_sources_newest_first_and_stores_neither(A, R, proj, store):
    run(proj.record(kind="action.submitted", repo_id=REPO, space="default", intent_dir=INTENT,
                    stage="requirements-analysis", action_id="a_0000000000000001", human_text="Approve"))
    audit = bundle(R, F.audit_text(
        F.audit_block("STAGE_AWAITING_APPROVAL", "2026-09-04T09:00:00Z", Stage="requirements-analysis"),
        F.audit_block("GATE_APPROVED", "2026-09-04T11:00:00Z", Stage="requirements-analysis", User_Input="Approve"),
    ))
    entries = run(proj.timeline(snapshot(audit)))
    assert [(e.source, e.at) for e in entries] == [
        ("aidlc", "2026-09-04T11:00:00Z"), ("studio", NOW), ("aidlc", "2026-09-04T09:00:00Z")]
    assert [e.message_key for e in entries] == [
        "audit.GATE_APPROVED", "activity.action.submitted", "audit.STAGE_AWAITING_APPROVAL"]
    # The invariant: the projected rows were never written.
    assert [row["source"] for row in raw_rows(store)] == ["studio"]
    assert rows(A, proj, source="aidlc") == []


def test_timeline_entry_json_matches_the_wire_shape(A, R, proj):
    run(proj.record(kind="action.resolved", repo_id=REPO, space="default", intent_dir=INTENT,
                    action_id="a_0000000000000001", session_key="dashboard:slot-1",
                    evidence=[{"kind": "audit", "ref": f"{SHARD}#7", "detail": "GATE_APPROVED"},
                              {"kind": "git", "ref": "commit:9f8e7d6"}]))
    entries = run(proj.timeline(snapshot(bundle(R, F.gate_open_audit("requirements-analysis")))))
    for entry in entries:
        assert set(entry.to_json()) == {"at", "source", "kind", "message_key", "params", "severity", "refs", "raw",
                                        "id"}
    studio_entry = next(e for e in entries if e.source == "studio")
    assert studio_entry.id == 1 and studio_entry.raw is None
    assert studio_entry.refs == {
        "repo": {"repo_id": REPO, "label": None, "space": "default", "intent_dir": INTENT},
        "action_id": "a_0000000000000001", "session_key": "dashboard:slot-1",
        "audit": {"shard": SHARD, "pos": 7, "event": "GATE_APPROVED"}, "git": {"sha": "9f8e7d6"},
    }
    aidlc_entry = next(e for e in entries if e.source == "aidlc")
    assert aidlc_entry.id is None
    assert aidlc_entry.refs["audit"]["shard"] == SHARD
    assert aidlc_entry.refs["audit"]["event"] == aidlc_entry.kind


def test_timeline_carries_the_verbatim_audit_block_for_the_evidence_drawer(R, proj):
    text = F.audit_block("GATE_APPROVED", NOW, Stage="requirements-analysis", User_Input="Approve")
    entries = run(proj.timeline(snapshot(bundle(R, text))))
    assert len(entries) == 1
    raw = entries[0].raw
    assert raw.startswith("## Gate Approved")
    assert "**Event**: GATE_APPROVED" in raw and "**User Input**: Approve" in raw


def test_timeline_redacts_a_credential_pasted_into_a_gate_response(R, proj):
    text = F.audit_block("GATE_APPROVED", NOW, Stage="build-and-test",
                         User_Input=f"Approve, and use {SECRET} for the deploy")
    entry = run(proj.timeline(snapshot(bundle(R, text))))[0]
    assert SECRET not in entry.raw
    assert SECRET not in json.dumps(entry.params)


def test_timeline_projects_real_audit_fields_as_snake_case_params(R, proj):
    """The real 2.1.1 shard: free-form field names, ``Candidate-ID``, and an out-of-taxonomy event."""
    audit = bundle(R, F.real_audit(F.OLDER, F.OLDER_INTENT), shard="603e5f4a8072-4c59c6cec037.md")
    entries = run(proj.timeline(snapshot(audit), limit=500))
    rule = next(e for e in entries if e.kind == "RULE_LEARNED")
    assert rule.message_key == "audit.RULE_LEARNED"
    assert rule.params["candidate_id"]  # "Candidate-ID" -> an interpolatable key
    assert rule.params["destination"].endswith("memory/project.md")
    assert rule.params["event"] == "RULE_LEARNED"
    loop = next(e for e in entries if e.kind == "LOOP_RUN_STARTED")
    assert loop.message_key == "audit.unknown_event"
    assert loop.params["event"] == "LOOP_RUN_STARTED"  # the fallback string can still name it
    assert all(key == key.lower() and " " not in key and "-" not in key for entry in entries for key in entry.params)


def test_timeline_caps_a_long_audit_field(A, R, proj):
    long_feedback = "x" * (A.AUDIT_PARAM_MAX_CHARS + 500)
    text = F.audit_block("GATE_REJECTED", NOW, Stage="requirements-analysis", Feedback=long_feedback)
    entry = run(proj.timeline(snapshot(bundle(R, text))))[0]
    assert len(entry.params["feedback"]) == A.AUDIT_PARAM_MAX_CHARS + 1  # capped, plus the ellipsis
    assert long_feedback in entry.raw  # the full block is intact


def test_timeline_filters_by_source(R, proj):
    run(proj.record(kind="session.bound", repo_id=REPO, space="default", intent_dir=INTENT))
    run(proj.record(kind="slack.sent", source="slack", repo_id=REPO, space="default", intent_dir=INTENT))
    snap = snapshot(bundle(R, F.gate_open_audit("requirements-analysis")))
    assert {e.source for e in run(proj.timeline(snap))} == {"studio", "slack", "aidlc"}
    assert {e.source for e in run(proj.timeline(snap, sources=["aidlc"]))} == {"aidlc"}
    assert {e.source for e in run(proj.timeline(snap, sources=["studio"]))} == {"studio"}
    assert {e.source for e in run(proj.timeline(snap, sources=["studio", "slack"]))} == {"studio", "slack"}
    assert run(proj.timeline(snap, sources=["git"])) == []


def test_timeline_keeps_only_this_intents_rows(R, proj):
    for repo_id, space, intent_dir in ((REPO, "default", INTENT), (REPO, "default", "260901-other"),
                                       ("r_000000000002", "default", INTENT), (REPO, "research", INTENT)):
        run(proj.record(kind="action.created", repo_id=repo_id, space=space, intent_dir=intent_dir))
    assert len(run(proj.timeline(snapshot(bundle(R, "")), sources=["studio"]))) == 1


def test_timeline_returns_the_newest_rows_within_its_limit(R, proj):
    text = F.audit_text(*[F.audit_block("STAGE_STARTED", f"2026-09-04T09:{n:02d}:00Z", Stage=f"stage-{n}")
                          for n in range(30)])
    entries = run(proj.timeline(snapshot(bundle(R, text)), limit=5))
    assert [e.params["stage"] for e in entries] == [f"stage-{n}" for n in (29, 28, 27, 26, 25)]


def test_timeline_surfaces_the_stage_of_a_studio_row_in_params(R, proj):
    run(proj.record(kind="action.created", repo_id=REPO, space="default", intent_dir=INTENT,
                    stage="requirements-analysis", params={"type": "gate"}))
    entry = run(proj.timeline(snapshot(bundle(R, "")), sources=["studio"]))[0]
    assert entry.params == {"type": "gate", "stage": "requirements-analysis"}


def test_timeline_tolerates_an_intent_with_no_audit_at_all(R, proj):
    run(proj.record(kind="intent.created", repo_id=REPO, space="default", intent_dir=INTENT))
    assert len(run(proj.timeline(snapshot(bundle(R, ""))))) == 1
    assert run(proj.timeline(snapshot(None), sources=["aidlc"])) == []


def test_timeline_orders_same_second_audit_rows_by_position(R, proj):
    """Second-precision timestamps tie constantly; the shard's own order has to survive the merge."""
    text = F.audit_text(F.audit_block("HUMAN_TURN", NOW),
                        F.audit_block("GATE_APPROVED", NOW, Stage="requirements-analysis"),
                        F.audit_block("STAGE_COMPLETED", NOW, Stage="requirements-analysis"))
    entries = run(proj.timeline(snapshot(bundle(R, text))))
    assert [e.kind for e in entries] == ["STAGE_COMPLETED", "GATE_APPROVED", "HUMAN_TURN"]


# ---- export ---- #


HUMAN = "Approve — and the key is not here"
ARTIFACT = "/FIXTURE/repo/aidlc/spaces/default/intents/x/y.md"


def seed_export_rows(proj, store):
    add_repo(store)
    run(proj.record(kind="action.submitted", repo_id=REPO, space="default", intent_dir=INTENT,
                    stage="requirements-analysis", action_id="a_0000000000000001", human_text=HUMAN,
                    params={"wire_text": "Approve", "artifact": ARTIFACT, "elsewhere": FOREIGN_PATH}))


def export(A, proj, *, human_text: bool = False, roots=("/FIXTURE/repo",), **query):
    return run(proj.export(A.ActivityQuery(**query), include_human_text=human_text, allowed_roots=list(roots)))


def test_export_envelope_is_the_contract_shape(A, proj, store, K):
    seed_export_rows(proj, store)
    out = export(A, proj)
    assert set(out) == {"generated_at", "app_version", "redacted", "human_text_included", "rows", "repos"}
    assert out["generated_at"] == NOW
    assert out["app_version"] == K.APP_VERSION
    assert out["redacted"] is True
    assert out["human_text_included"] is False
    assert out["repos"] == [{"repo_id": REPO, "label": "repo"}]
    assert set(out["rows"][0]) == set(A.ActivityRow().to_json())


def test_export_withholds_human_text_unless_asked(A, proj, store):
    seed_export_rows(proj, store)
    without = export(A, proj)
    assert without["rows"][0]["human_text"] is None  # present and null: withheld, not "there was none"
    assert HUMAN not in json.dumps(without)
    with_text = export(A, proj, human_text=True)
    assert with_text["human_text_included"] is True
    assert with_text["rows"][0]["human_text"] == HUMAN


@pytest.mark.parametrize("roots, artifact", [(["/FIXTURE/repo"], ARTIFACT), ([], "<path>")])
def test_export_scrubs_every_path_outside_the_registered_roots(A, proj, store, roots, artifact):
    seed_export_rows(proj, store)
    out = export(A, proj, human_text=True, roots=roots)
    params = out["rows"][0]["params"]
    assert params["artifact"] == artifact  # a registered repository's own path may stay
    assert params["elsewhere"] == "<path>"  # the user's disk does not
    assert FOREIGN_PATH not in json.dumps(out)


def test_export_redacts_credentials_in_every_field(A, proj, store):
    add_repo(store)
    run(proj.record(kind="action.submitted", repo_id=REPO, params={"note": f"deploy with {SECRET}"},
                    evidence=[{"kind": "studio", "ref": "engine.stderr", "detail": f"AWS_SECRET_ACCESS_KEY={SECRET}"}],
                    human_text=f"Approve, credentials are {SECRET}"))
    assert SECRET not in json.dumps(export(A, proj, human_text=True))
    # The verbatim text is only redacted on the way out; the retention purge owns the stored copy.
    assert SECRET in (raw_rows(store)[0]["human_text"] or "")


def test_export_honours_the_query_filters_and_page_size(A, proj):
    for n in range(5):
        run(proj.record(kind="repo.rescanned", repo_id=REPO, params={"n": n}))
    run(proj.record(kind="repo.added", repo_id="r_000000000002"))
    out = export(A, proj, roots=(), kind="repo.rescanned", limit=2)
    assert [row["params"]["n"] for row in out["rows"]] == [4, 3]
    assert out["repos"] == []  # r_000000000001 is not in the repos table here


def test_export_lists_only_the_repos_the_rows_reference(A, proj, store):
    for repo_id, label in ((REPO, "alpha"), ("r_000000000002", "beta"), ("r_000000000003", "gamma")):
        add_repo(store, repo_id, label, f"/FIXTURE/repo/{label}")
    run(proj.record(kind="repo.added", repo_id="r_000000000002"))
    run(proj.record(kind="repo.added", repo_id=REPO))
    assert export(A, proj, roots=())["repos"] == [{"repo_id": REPO, "label": "alpha"},
                                                  {"repo_id": "r_000000000002", "label": "beta"}]


def test_the_merged_timeline_filters_on_the_server_and_pages_by_a_cursor(A, R, proj, store):
    """Filtered in the browser, a stage filter searched only the newest page and could not reach an older event."""
    ActivityQuery = A.ActivityQuery
    blocks = [F.audit_block("STAGE_STARTED", f"2026-09-04T10:{minute:02d}:00Z", Stage="code-generation")
              for minute in range(30)]
    blocks.insert(0, F.audit_block("STAGE_COMPLETED", "2026-09-04T08:00:00Z", Stage="reverse-engineering"))
    snap = snapshot(bundle(R, F.audit_text(*blocks)))
    run(proj.record(kind="session.bound", repo_id=REPO, space="default", intent_dir=INTENT))
    # The oldest event of another stage is found though thirty newer ones would fill the page.
    entries, cursor = run(proj.timeline_page(snap, limit=10, q=ActivityQuery(stage="reverse-engineering")))
    assert [(e.kind, e.at) for e in entries] == [("STAGE_COMPLETED", "2026-09-04T08:00:00Z")] and cursor is None
    # Pages meet exactly: every event once, newest first, the Studio row among them.
    seen, cursor = [], None
    for _ in range(10):
        page, cursor = run(proj.timeline_page(snap, limit=7, q=ActivityQuery(cursor=cursor)))
        seen += page
        if cursor is None:
            break
    assert len(seen) == 32 and len({(e.source, e.at, e.kind, e.id) for e in seen}) == 32
    assert [e.at for e in seen] == sorted((e.at for e in seen), reverse=True)
    with pytest.raises(A.StudioError):
        run(proj.timeline_page(snap, q=ActivityQuery(cursor="t:not-a-key")))
