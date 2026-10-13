"""The parsers read the real AI-DLC formats (harvested fixtures, FORMAT-NOTES.md); token formulas are golden values."""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import fixtures as F
from conftest import FIXTURES, RepoBuilder

STATE_FIXTURES = [
    (F.DEVDELTA, F.DEVDELTA_POC, 8, 33), (F.DEVDELTA, F.DEVDELTA_REMEDIATION, 8, 33),
    (F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN, 7, 32), (F.PAYLOAD_230, F.PAYLOAD_COMPLETED, 7, 32),
    (F.OLDER, F.OLDER_INTENT, 7, 32),
]
DEV_IC = (F.DEVDELTA, F.DEVDELTA_POC, "ideation/intent-capture")
DEV_RA = (F.DEVDELTA, F.DEVDELTA_POC, "inception/requirements-analysis")
DEV_CG = (F.DEVDELTA, F.DEVDELTA_POC, "construction/kiro-impact-poc/code-generation")
OLD_FEAS = (F.OLDER, F.OLDER_INTENT, "ideation/feasibility")
QUESTION_FIXTURES = [
    DEV_IC, DEV_RA, DEV_CG,
    (F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN, "ideation/intent-capture"),
    (F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN, "inception/requirements-analysis"),
    (F.PAYLOAD_230, F.PAYLOAD_COMPLETED, "construction/health-probe/code-generation"),
    OLD_FEAS, (F.OLDER, F.OLDER_INTENT, "ideation/intent-capture"),
    (F.OLDER, F.OLDER_INTENT, "ideation/market-research"),
]
FOLLOWUPS = (F.FIXTURES / "question-fallback/domain-followups.md").read_text()

#: One row flipped to ``[?]`` on the real 33-stage v8 file; its digest pins the golden tokens below.
GATE_STATE = F.state_text(marks={"requirements-analysis": "?"}, fields={
    "Current Stage": "requirements-analysis", "Status": "Running", "Next Stage": "user-stories"})
GATE_STATE_SHA = "9666ad4b99177fa026c52690a89657031b175b7016345240ab455f1a7f20196d"
GATE_AUDIT = F.gate_open_audit("requirements-analysis")
X = "260904-x"
REC = f"aidlc/spaces/default/intents/{X}"
SHA0 = "0" * 64


@pytest.fixture(scope="module")
def R(studio):
    assert studio.aidlc_reader is not None, getattr(studio, "aidlc_reader__error", None)
    return studio.aidlc_reader


@pytest.fixture
def reader(R, clock):
    return R.AidlcReader(clock)


@pytest.fixture
def pq(R):
    return lambda text: R.parse_questions_file(text, "questions.md", SHA0)


@pytest.fixture(scope="module")
def devdelta_repo(tmp_path_factory) -> Path:
    """A real 2.6.2 install copied once; every reader call is read-only (``test_reader_never_writes``)."""
    root = tmp_path_factory.mktemp("devdelta") / "repo"
    shutil.copytree(F.DEVDELTA, root)
    return root


@pytest.fixture(scope="module")
def older_repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("older") / "repo"
    shutil.copytree(F.OLDER, root)
    return root


def one_intent(repo_builder, **kw) -> Path:
    kw.setdefault("state", GATE_STATE)
    kw.setdefault("audit", GATE_AUDIT)
    return repo_builder.with_workspace().with_intent(X, **kw).build()


def marker(R, digest="a" * 64, **payload):
    return R.parse_directive(json.dumps({"state_sha256": digest, **payload}).encode(), digest)


def events_of(R, *blocks):
    return R.merge_audit([R.parse_audit_shard("".join(blocks), "shard.md", 0)])


def only_question(pq, prompt, options=("a", "b")):
    return pq(F.questions_text(questions=[(prompt, list(options))])).questions[0]


def review_of(R, rel):
    return R.parse_review_section((F.DEVDELTA_INTENTS / F.DEVDELTA_POC / rel).read_text("utf-8"))


def tree_digest(root: Path) -> list[tuple[str, int, str]]:
    return [(p.relative_to(root).as_posix(), p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink()]


def force_unstable(monkeypatch, target: Path) -> None:
    """Make ``target`` look changed between the two stats of each ``stable_read`` (the stdlib is the seam)."""
    real, calls = os.stat, itertools.count(1)

    def fake(path, *args, **kwargs):
        result = real(path, *args, **kwargs)
        try:
            same = Path(path) == target
        except TypeError:
            same = False
        if same and next(calls) % 2 == 0:  # the post-read stat of each attempt
            return os.stat_result(tuple(result)[:8] + (result.st_mtime + 1,) + tuple(result)[9:])
        return result

    monkeypatch.setattr(os, "stat", fake)


# --- aidlc-state.md ---
@pytest.mark.parametrize("tree,intent,version,stage_count", STATE_FIXTURES)
def test_state_file_parses_every_real_fixture(R, tree, intent, version, stage_count):
    state = R.parse_state_file(F.real_state(tree, intent))
    assert state.state_version == version and version in R.C.SUPPORTED_STATE_VERSIONS
    assert state.counts["total"] == stage_count and not state.header_only
    assert set(state.sections) >= {"Project Information", "Scope Configuration", "Workspace State",
                                   "Execution Plan Summary", "Runtime State", "Phase Progress", "Stage Progress",
                                   "Current Status", "Session Resume Point"}
    # the parse agrees with an independent regex sweep of the same bytes
    assert {row.slug: row.mark for row in state.stages} == F.stage_marks(F.real_state(tree, intent))
    assert [name for name, _ in state.phases] == ["Initialization", "Ideation", "Inception", "Construction",
                                                  "Operation"]
    assert state.project["State Version"] == str(version)
    assert state.counts["done"] == state.counts["completed"] + state.counts["skipped"]


def test_state_supported_versions_and_stage_counts_match_constants(studio):
    assert {version for _, _, version, _ in STATE_FIXTURES} == set(studio.constants.SUPPORTED_STATE_VERSIONS)
    assert {count for _, _, _, count in STATE_FIXTURES} == {32, 33}  # application-design vs domain/contract design


def test_state_empty_value_with_trailing_space_is_empty_string(R):
    text = F.real_state()
    assert "- **Review Override**: \n" in text
    state = R.parse_state_file(text)
    assert state.scope_config["Review Override"] == "" and "Review Override" in state.sections["Scope Configuration"]


def test_state_all_six_checkbox_marks(R):
    marks = {"workspace-scaffold": "x", "workspace-detection": "S", "state-init": "R", "intent-capture": "?",
             "market-research": "-", "feasibility": " "}
    state = R.parse_state_file(F.state_text(marks=marks))
    assert {row.slug: row.state for row in state.stages if row.slug in marks} == {
        "workspace-scaffold": "completed", "workspace-detection": "skipped", "state-init": "revising",
        "intent-capture": "awaiting_approval", "market-research": "in_progress", "feasibility": "not_started"}
    assert (state.counts["awaiting_approval"], state.counts["revising"], state.counts["unknown"]) == (1, 1, 0)


def test_state_en_dash_double_dash_and_missing_separator(R):
    state = R.parse_state_file(
        F.real_state().replace("- [x] workspace-scaffold — EXECUTE", "- [x] workspace-scaffold – EXECUTE")
        .replace("- [x] workspace-detection — EXECUTE", "- [x] workspace-detection -- EXECUTE")
        .replace("- [x] state-init — EXECUTE", "- [x] state-init"))
    by_slug = {row.slug: row for row in state.stages}
    assert state.counts["total"] == 33
    assert (by_slug["workspace-scaffold"].suffix, by_slug["workspace-detection"].suffix) == ("EXECUTE", "EXECUTE")
    assert by_slug["state-init"].suffix is None and by_slug["state-init"].state == "completed"


def test_state_phase_subheaders_and_per_unit_tbd_line(R):
    text = F.real_state()
    assert "Per unit: [TBD]" in text
    state = R.parse_state_file(text)
    assert {row.phase for row in state.stages} == {"initialization", "ideation", "inception", "construction",
                                                   "operation"}
    assert state.row("code-generation").phase == "construction" and all(row.slug != "Per" for row in state.stages)
    assert [row.slug for row in state.stages][:3] == ["workspace-scaffold", "workspace-detection", "state-init"]


def test_state_skeleton_stance_without_blank_line_before_next_section(R):
    text = F.real_state()
    assert "- **Skeleton Stance**: scope-dependent\n## Phase Progress\n" in text
    state = R.parse_state_file(text)
    assert state.runtime == {"Revision Count": "0", "Skeleton Stance": "scope-dependent"}
    assert state.phases[0] == ("Initialization", "Verified")


def test_state_header_only_stub(R):
    """``createIntent`` writes the one-line stub before the body: birth in progress, not an empty run."""
    state = R.parse_state_file("# AI-DLC State Tracking\n")
    assert state.header_only is True and state.counts["total"] == 0
    assert state.state_version is None and state.current_stage is None and state.sections == {}


def test_state_version_9_is_reported_not_crashed_and_a_non_integer_one_is_none(R, studio):
    state = R.parse_state_file(F.state_text(fields={"State Version": "9"}))
    assert state.state_version == 9 and 9 not in studio.constants.SUPPORTED_STATE_VERSIONS
    assert state.counts["total"] == 33  # the rest of the file is still readable
    assert R.parse_state_file(F.state_text(fields={"State Version": "8-beta"})).state_version is None


def test_state_contradictory_fixture_is_reported_verbatim(R):
    """A file that disagrees with itself parses; deciding what is true is ``consistency.py``'s job."""
    state = R.parse_state_file(F.state_text(
        marks={"requirements-analysis": "?", "user-stories": "R", "market-research": "S"},
        fields={"Completed": "20", "Lifecycle Phase": "OPERATION", "Initialization": "Active"}))
    assert state.completed_claimed == 20
    assert {k: state.counts[k] for k in ("completed", "awaiting_approval", "revising", "skipped")} == {
        "completed": 7, "awaiting_approval": 1, "revising": 1, "skipped": 1}  # the file's own rows say otherwise
    assert state.lifecycle_phase == "OPERATION" and dict(state.phases)["Initialization"] == "Active"


def test_state_none_sentinel_is_absent_not_a_stage(R):
    state = R.parse_state_file(F.real_state())
    assert (state.current_stage, state.next_stage, state.status) == ("build-and-test", None, "Completed")
    assert state.current["Next Stage"] == "none"  # the raw value stays available


def test_state_fields_are_matched_by_label_across_sections(R):
    text = F.real_state(F.OLDER, F.OLDER_INTENT)
    state = R.parse_state_file(text)
    assert (state.status, dict(state.phases)["Initialization"], state.lifecycle_phase) == (
        "Running", "Active", "IDEATION")
    assert (state.revision_count, state.total_stages_claimed, F.state_fields(text)["Total Stages"]) == (0, 31, "31")


def test_state_parked_and_unit_rows(R):
    text = F.real_state()
    for name, value in (("Parked", "2026-09-03T10:00:00Z"), ("Parked At Stage", "code-generation"),
                        ("Active Unit", "kiro-impact-poc"), ("Unit State", "paused")):
        text = F.insert_runtime_field(text, name, value)
    state = R.parse_state_file(text)
    assert (state.parked_at, state.parked_at_stage, state.active_unit, state.unit_state) == (
        "2026-09-03T10:00:00Z", "code-generation", "kiro-impact-poc", "paused")


def test_state_unknown_section_is_preserved(R):
    state = R.parse_state_file(F.real_state() + "\n## Future Section\n- **Novel Field**: 42\n")
    assert state.sections["Future Section"] == {"Novel Field": "42"}


def test_state_crlf_and_empty_input(R):
    state = R.parse_state_file(F.real_state().replace("\n", "\r\n"))
    assert state.counts["total"] == 33 and state.state_version == 8
    assert R.parse_state_file("").header_only is True


# --- intents.json, cursors, directive ---
@pytest.mark.parametrize("tree", [F.DEVDELTA, F.PAYLOAD_230, F.OLDER])
def test_parse_intents_json_all_three_versions(R, tree):
    sources = F.intents_registry(tree)
    rows = R.parse_intents_json(json.dumps(sources).encode())
    assert rows and len(rows) == len(sources)
    for row, source in zip(rows, sources):
        assert (row.uuid, row.slug, row.dir_name, row.scope, row.status, row.repos) == (
            source["uuid"], source["slug"], source["dirName"], source["scope"], source["status"], ())
        assert R.match_intent_row(rows, source["dirName"]) is row


def test_parse_intents_json_malformed_and_legacy_rows(R):
    assert [R.parse_intents_json(raw) for raw in (b"", b"not json", b'{"uuid": "x"}')] == [[], [], []]
    rows = R.parse_intents_json(json.dumps([
        "junk", {"uuid": "019f3d4f-1da9-7b07-892f-09047f0c3bb8", "slug": "trello-plugin"},
        {"uuid": "u2", "slug": "s2", "dirName": "d2", "repos": ["a", "b"], "status": "complete"}]).encode())
    assert [row.slug for row in rows] == ["trello-plugin", "s2"]
    assert rows[0].dir_name is None and rows[0].status == "unknown"  # absent status is not invented
    assert rows[1].repos == ("a", "b")


def test_match_intent_row_legacy_slug_hex_rule(R):
    rows = R.parse_intents_json(json.dumps(
        [{"uuid": "019f3d4f-1da9-7b07-892f-09047f0c3bb8", "slug": "trello-plugin", "status": "in-flight"}]).encode())
    assert R.match_intent_row(rows, "trello-plugin-09047f0c3bb8") is rows[0]
    # hex but not this uuid; not hex; a date prefix is not the rule
    assert [R.match_intent_row(rows, name) for name in ("trello-plugin-deadbeef", "trello-plugin-notes",
                                                         "260707-trello-plugin")] == [None, None, None]


def test_parse_cursor(R):
    assert R.parse_cursor(b"260828-kiro-impact-poc\n", default=None) == "260828-kiro-impact-poc"
    assert R.parse_cursor(b"  spaced  \nsecond\n", default=None) == "spaced"
    assert [R.parse_cursor(raw, default="default") for raw in (b"", b"\n", None)] == ["default"] * 3
    assert R.parse_cursor(None, default=None) is None


def test_active_space_defaults_when_cursor_absent(reader, older_repo, devdelta_repo):
    assert not (older_repo / "aidlc" / "active-space").exists()
    assert reader.active_space(older_repo) == "default" and reader.active_space(devdelta_repo) == "default"


def test_active_intent_dangling_when_cursor_names_a_dir_without_state(reader, repo_builder):
    repo = repo_builder.with_workspace().with_intent("260904-real", state=GATE_STATE, audit=GATE_AUDIT).build()
    cursor = repo / "aidlc/spaces/default/intents/active-intent"
    assert reader.active_intent(repo, "default") == ("260904-real", False)
    (cursor.parent / "260904-ghost").mkdir()  # a record dir with no aidlc-state.md
    cursor.write_text("260904-ghost\n")
    assert reader.active_intent(repo, "default") == ("260904-ghost", True)
    assert reader.list_intent_dirs(repo, "default") == ["260904-real"]
    for text, expected in (("260904-missing\n", ("260904-missing", True)), ("../../escape\n", (None, True))):
        cursor.write_text(text)
        assert reader.active_intent(repo, "default") == expected, text
    cursor.unlink()
    assert reader.active_intent(repo, "default") == (None, False)


def test_layout_spaces_legacy_and_none(reader, devdelta_repo, repo_builder, tmp_path):
    (tmp_path / "bare").mkdir()
    assert reader.layout(devdelta_repo) == "spaces" and reader.layout(tmp_path / "bare") is None
    assert reader.layout(repo_builder.with_legacy_layout().build()) == "legacy"


def test_list_spaces_requires_intents_or_memory(reader, repo_builder):
    repo = repo_builder.with_workspace().build()
    (repo / "aidlc" / "spaces" / "empty").mkdir()
    (repo / "aidlc" / "spaces" / "second" / "memory").mkdir(parents=True)
    assert reader.list_spaces(repo) == ["default", "second"]


def test_directive_fresh_and_stale(reader, devdelta_repo):
    snap, _ = reader.read_state(devdelta_repo, "default", F.DEVDELTA_REMEDIATION)
    fresh = reader.read_directive(devdelta_repo, "default", F.DEVDELTA_REMEDIATION, snap.sha256)
    assert (fresh.version, fresh.stage, fresh.state_sha256, fresh.unit) == (1, "reverse-engineering", snap.sha256, None)
    assert fresh.matches_state is True
    snap, _ = reader.read_state(devdelta_repo, "default", F.DEVDELTA_POC)
    stale = reader.read_directive(devdelta_repo, "default", F.DEVDELTA_POC, snap.sha256)
    assert stale.stage == "build-and-test" and stale.state_sha256 != snap.sha256
    assert stale.matches_state is False  # stale, not corrupt (C06)


def test_parse_directive_rejects_non_markers(R):
    assert R.parse_directive(b"", "a" * 64) is None and R.parse_directive(b"[]", "a" * 64) is None
    for bad in ({"stage": "x"}, {"version": 0, "stage": "x"}, {"version": 3, "stage": "x"},
                {"version": 1, "stage": "Bad Stage"}, {"version": 1, "stage": "x", "state_sha256": "short"}):
        assert marker(R, **bad) is None, bad
    found = marker(R, version=1, stage="code-generation", unit="u1")
    assert (found.stage, found.unit, found.matches_state) == ("code-generation", "u1", True)
    assert found.version == 1 and found.kind is None and found.units == ()


def test_parse_directive_rejects_a_version_2_marker_with_no_usable_kind(R):
    """A v2 ``kind`` outside the three 2.7.1 emits names no running stage, so it is no marker at all."""
    def v2(**over):
        return marker(R, **{"version": 2, "kind": "run-stage", "stage": "code-generation", **over})

    assert v2() is not None
    assert v2(kind="compose-plan") is None and v2(kind=None) is None
    assert marker(R, version=2, stage="code-generation") is None


def test_parse_directive_reads_both_marker_schema_versions_the_engine_leaves_on_disk(R):
    run_stage = marker(R, version=2, kind="run-stage", stage="requirements-analysis")
    assert (run_stage.version, run_stage.kind, run_stage.stage) == (2, "run-stage", "requirements-analysis")
    assert run_stage.unit is None and run_stage.units == () and run_stage.matches_state is True
    assert run_stage.to_json()["version"] == 2 and run_stage.to_json()["kind"] == "run-stage"
    swarm = marker(R, version=2, kind="invoke-swarm", stage="code-generation", units=["authentication", "billing"])
    assert swarm.kind == "invoke-swarm" and swarm.units == ("authentication", "billing")
    assert swarm.unit is None and swarm.to_json()["units"] == ["authentication", "billing"]  # units, never one unit
    steering = marker(R, version=2, kind="load-steering", stage="state-init")
    assert steering is not None and steering.kind == "load-steering"


def test_parse_directive_tolerates_every_extra_key_a_real_2_7_1_marker_carries(R):
    """A verbatim v2 marker from a real 2.7.1 ``lab1-express`` record: five fields read, the rest ignored."""
    found = marker(
        R, "462987b2137d8a16bef84283879cbb7533fd95d8a8c781964f4544f86a538eb4", version=2, revision=4,
        project_sha256="5e9a3b040459cf0b16823cd8f4f5185ed7f7bb26213f598781f85d5feae55f85",
        intent_uuid="01a071ee-613d-762c-bc22-98ad68d14974", state_present=True, cursor_harness="claude",
        owner_session="sessionless:5e9a3b040459cf0b", owner_epoch=0, context_epoch=0, kind="run-stage",
        stage="code-generation", delivery="issued", needs_rehydrate=False,
        active_attempt={"id": "sessionless", "command_kind": "next", "status": "settled"}, event_sequence=0,
        stop_count=0, code_generation_authority_revision=4)
    assert (found.version, found.kind, found.stage) == (2, "run-stage", "code-generation")
    assert found.matches_state is True and found.unit is None


def test_directive_absent_for_engines_that_never_wrote_it(reader, older_repo):
    snap, _ = reader.read_state(older_repo, "default", F.OLDER_INTENT)
    assert reader.read_directive(older_repo, "default", F.OLDER_INTENT, snap.sha256) is None


# --- audit ---
def test_audit_shard_split_on_the_fence(R):
    text = F.real_audit(F.DEVDELTA, F.DEVDELTA_REMEDIATION)
    name = "603e5f4a8072-747664ca2c41.md"
    events = R.parse_audit_shard((F.DEVDELTA_INTENTS / F.DEVDELTA_REMEDIATION / "audit" / name).read_text(), name, 1)
    assert [event.event for event in events] == ["SESSION_STARTED", "HUMAN_TURN", "SESSION_ENDED"]
    assert events[0].fields == {"Source": "startup"} and events[0].timestamp == "2026-08-31T02:35:05Z"
    assert events[0].raw.startswith("## Session Start")  # the file header is not part of the block
    assert events[1].fields == {}  # HUMAN_TURN carries no fields at all
    assert [event.pos for event in events] == [0, 1, 2] and all(event.shard_index == 1 for event in events)
    assert len(R.parse_audit_shard(text, "s", 0)) == text.count("\n---\n")


def test_audit_field_lines_with_and_without_leading_dash(R):
    text = ("# AI-DLC Audit Log\n\n## Gate Approved\n**Timestamp**: 2026-09-04T09:00:11Z\n**Event**: GATE_APPROVED\n"
            "- **Stage**: requirements-analysis\n**User Input**: Approve\n\n---\n")
    assert R.parse_audit_shard(text, "s", 0)[0].fields == {"Stage": "requirements-analysis", "User Input": "Approve"}


def test_audit_keeps_escaped_newlines_and_empty_values(R):
    block = F.audit_block("SUBAGENT_COMPLETED", "2026-09-04T09:00:00Z", Agent_Type="", Message="line one\nline two")
    event = R.parse_audit_shard(block, "s", 0)[0]
    assert event.fields["Message"] == "line one\\nline two"  # literal, exactly as written
    assert event.fields["Agent Type"] == "" and "\n" not in event.fields["Message"]


def test_audit_crlf_and_blocks_without_event_are_dropped(R):
    text = (F.audit_block("HUMAN_TURN", "2026-09-04T09:00:00Z")
            + "\n## Hand edited\n**Timestamp**: 2026-09-04T09:00:01Z\n**Note**: no event key\n\n---\n"
            + F.audit_block("STAGE_STARTED", "2026-09-04T09:00:02Z", Stage="user-stories")).replace("\n", "\r\n")
    events = R.parse_audit_shard(text, "s", 0)
    assert [event.event for event in events] == ["HUMAN_TURN", "STAGE_STARTED"]
    assert events[1].pos == 2  # the dropped block still occupies its position


def test_audit_unknown_event_names_are_kept(R):
    events = R.parse_audit_shard(F.real_audit(F.OLDER, F.OLDER_INTENT), "shard.md", 0)
    loop = [event for event in events if event.event == "LOOP_RUN_STARTED"]
    assert len(loop) == 4 and loop[0].fields["Mode"] == "attached"
    assert "RULE_LEARNED" in {event.event for event in events}
    assert events_of(R, F.audit_block("BRAND_NEW_EVENT", "2026-09-04T09:00:00Z"))[0].event == "BRAND_NEW_EVENT"


def test_merge_audit_orders_by_timestamp_then_shard_then_position(R):
    first = R.parse_audit_shard(F.audit_block("HUMAN_TURN", "2026-09-04T09:00:00Z")
                                + F.audit_block("GATE_APPROVED", "2026-09-04T09:00:05Z", Stage="a"), "shard-a.md", 0)
    second = R.parse_audit_shard(F.audit_block("SESSION_STARTED", "2026-09-04T09:00:05Z", Source="startup")
                                 + F.audit_block("HUMAN_TURN", "2026-09-04T09:00:01Z"), "shard-b.md", 1)
    assert [(event.timestamp, event.shard_index, event.pos) for event in R.merge_audit([first, second])] == [
        ("2026-09-04T09:00:00Z", 0, 0), ("2026-09-04T09:00:01Z", 1, 1),
        ("2026-09-04T09:00:05Z", 0, 1), ("2026-09-04T09:00:05Z", 1, 0)]  # same second: lower shard_index first


def test_latest_event_filters_by_type_and_stage(R, studio):
    events, gates = events_of(R, F.gate_approved_audit("requirements-analysis")), studio.constants.GATE_EVENTS
    assert R.latest_event(events, gates).event == "GATE_APPROVED"
    assert R.latest_event(events, gates, "user-stories") is None and R.latest_event([], gates) is None
    awaiting = R.latest_event(events, ("STAGE_AWAITING_APPROVAL",), "requirements-analysis")
    assert awaiting.timestamp == "2026-09-04T09:00:03Z"
    # unsorted input still yields the newest row, not the last one
    assert R.latest_event(list(reversed(events)), ("HUMAN_TURN",)).timestamp == "2026-09-04T09:00:10Z"


def test_read_audit_merges_both_shards_of_a_real_record(reader, devdelta_repo):
    bundle = reader.read_audit(devdelta_repo, "default", F.DEVDELTA_REMEDIATION)
    assert len(bundle.shards) == 2 and bundle.complete is True
    assert [shard.relpath.rsplit("/", 1)[-1] for shard in bundle.shards] == [
        "603e5f4a8072-26e77d17b1cd.md", "603e5f4a8072-747664ca2c41.md"]
    assert all(shard.size > 0 and shard.mtime_ns > 0 for shard in bundle.shards)
    timestamps = [event.timestamp for event in bundle.events]
    assert timestamps == sorted(timestamps) and bundle.events[0].shard_index == 0


def test_read_audit_tail_read_marks_the_bundle_incomplete(reader, devdelta_repo):
    whole = reader.read_audit(devdelta_repo, "default", F.DEVDELTA_REMEDIATION)
    tail = reader.read_audit(devdelta_repo, "default", F.DEVDELTA_REMEDIATION, tail_bytes=4096)
    assert tail.complete is False and [shard.truncated for shard in tail.shards] == [True, False]
    assert 0 < len(tail.events) < len(whole.events)
    assert all(event.event for event in tail.events)  # no half-parsed block survived


def test_default_audit_history_keeps_ordinals_when_it_crosses_old_tail_limit(reader, repo_builder, R):
    repo = one_intent(repo_builder, audit=F.audit_block("STAGE_AWAITING_APPROVAL", "2026-09-04T10:00:00Z", Stage="a"))
    path = next((repo / REC / "audit").glob("*.md"))
    before = reader.read_audit(repo, "default", X)
    path.write_text(path.read_text()
                    + F.audit_block("ARTIFACT_UPDATED", "2026-09-04T10:00:01Z", Details="x" * R.C.MAX_AUDIT_TAIL_BYTES)
                    + F.audit_block("HUMAN_TURN", "2026-09-04T10:00:02Z")
                    + F.audit_block("GATE_REJECTED", "2026-09-04T10:00:02Z", Stage="a"))
    after = reader.read_audit(repo, "default", X)
    assert after.complete and not any(shard.truncated for shard in after.shards)
    assert after.events[:len(before.events)] == before.events
    assert after.events[-1].event == "GATE_REJECTED" and after.events[-1].pos > before.events[-1].pos


def test_audit_history_has_one_aggregate_read_budget(reader, repo_builder, R, monkeypatch):
    repo = one_intent(repo_builder, audit="")
    for name in ("a.md", "b.md", "c.md"):
        (repo / REC / "audit" / name).write_text(
            F.audit_block("ARTIFACT_UPDATED", "2026-09-04T10:00:00Z", Details="x" * 500))
    monkeypatch.setattr(R.C, "MAX_AUDIT_HISTORY_BYTES", 700)
    requested = []
    for name in ("bounded_read", "tail_read"):
        def counted(path, limit, _read=getattr(R.security, name)):
            requested.append(limit)
            return _read(path, limit)

        monkeypatch.setattr(R.security, name, counted)
    audit = reader.read_audit(repo, "default", X)
    assert sum(requested) <= 700 and not audit.complete and any(shard.truncated for shard in audit.shards)
    assert {Path(shard.relpath).name for shard in audit.shards} >= {"a.md", "b.md", "c.md"}


def test_a_shard_that_grows_during_the_read_keeps_its_history(reader, repo_builder, R, monkeypatch):
    """An engine append between stat and read used to drop the whole shard (and its human turns)."""
    repo = one_intent(repo_builder)
    before = reader.read_audit(repo, "default", X)
    path = next((repo / REC / "audit").glob("*.md"))
    original = R.security.bounded_read

    def racing(target, cap):
        if Path(target) == path:
            with path.open("a") as file:
                file.write(F.audit_block("ERROR_LOGGED", "2026-09-04T10:00:09Z", Details="retry"))
        return original(target, cap)

    monkeypatch.setattr(R.security, "bounded_read", racing)
    during = reader.read_audit(repo, "default", X)
    assert during.events[:len(before.events)] == before.events
    assert [e.event for e in during.events].count("HUMAN_TURN") == [e.event for e in before.events].count("HUMAN_TURN")
    assert during.complete is False  # it changed under the read; the next read is whole again
    assert not any(shard.truncated for shard in during.shards)


def test_over_budget_history_keeps_the_newest_shard_whole(reader, repo_builder, R, monkeypatch):
    """Shard names are per clone; the one written last must not be starved by name order."""
    repo = one_intent(repo_builder, audit="")
    for stale in (repo / REC / "audit").glob("*.md"):
        stale.unlink()
    old, active = repo / REC / "audit/alice-0000.md", repo / REC / "audit/bob-9999.md"
    old.write_text("".join(F.audit_block("ARTIFACT_UPDATED", "2026-09-01T10:00:00Z", Details="x" * 400)
                           for _ in range(8)))
    os.utime(old, ns=(1_000_000_000, 1_000_000_000))
    active.write_text(F.audit_block("HUMAN_TURN", "2026-09-04T10:00:20Z")
                      + F.audit_block("GATE_APPROVED", "2026-09-04T10:00:20Z", Stage="requirements-analysis"))
    monkeypatch.setattr(R.C, "MAX_AUDIT_HISTORY_BYTES", old.stat().st_size)
    audit = reader.read_audit(repo, "default", X)
    assert audit.complete is False and [shard.truncated for shard in audit.shards] == [True, False]
    assert [e.event for e in audit.events if e.shard == active.name] == ["HUMAN_TURN", "GATE_APPROVED"]
    assert 0 < sum(1 for e in audit.events if e.shard == old.name) < 8


def test_shard_index_is_the_position_among_read_shards(reader, repo_builder):
    repo = one_intent(repo_builder)
    (repo / REC / "audit/000-not-a-file.md").mkdir()
    audit = reader.read_audit(repo, "default", X)
    assert audit.complete is False and len(audit.shards) == 1
    assert {event.shard_index for event in audit.events} == {0}


def test_read_audit_of_a_record_without_audit_dir(reader, repo_builder):
    repo = one_intent(repo_builder, audit="")
    shutil.rmtree(repo / REC / "audit")
    bundle = reader.read_audit(repo, "default", X)
    assert (bundle.events, bundle.shards, bundle.complete) == ([], [], True)


# --- questions ---
@pytest.mark.parametrize("follow_up", [
    "**Follow-up F1:** Which region?\n\n[Answer]:",
    "Follow-up\n---------\n\nWhich region?\n\n[Answer]: ___",
    "Follow-up F1: Which region?\n- [Answer]:",
], ids=["bold-label", "setext-heading", "bulleted-tag"])
def test_a_blank_followup_inside_an_answered_question_is_still_pending(R, pq, follow_up):
    """The engine's Stop hook holds the turn for any blank tag; a form must not skip it."""
    parsed = pq("## Q1. Store?\nA. Postgres\nB. SQLite\nX. Other\n\n[Answer]: A\n- chosen for joins\n\n"
                f"{follow_up}\n\n## Q2. Region?\nA. EU\nB. US\n\n[Answer]:\n")
    assert [question.answered for question in parsed.questions] == [True, False]
    assert parsed.questions[0].answer.startswith("A")
    assert (parsed.unsupported_pending_count, parsed.pending_count) == (1, 2)
    assert not R.file_questions_support_forms(parsed)


def test_a_bulleted_blank_tag_in_a_free_section_is_pending(pq):
    text = "## Q1. Store?\nA. Postgres\n\n[Answer]: A\n\n## Notes\n\nConfirm the region:\n- [Answer]:\n"
    parsed, answered = pq(text), pq(text.replace("- [Answer]:", "- [Answer]: EU"))
    assert (parsed.unsupported_pending_count, parsed.pending_count) == (1, 1)
    assert (answered.unsupported_pending_count, answered.pending_count) == (0, 0)


@pytest.mark.parametrize("tree,intent,stage_rel", QUESTION_FIXTURES)
def test_questions_file_parses_every_real_fixture(pq, tree, intent, stage_rel):
    text = F.real_questions(tree, intent, stage_rel)
    parsed = pq(text)
    assert len(parsed.questions) == text.count("\n## Q")
    for question in parsed.questions:
        assert question.index >= 1 and question.prompt
        if question.options:  # every real question ends with the X escape hatch
            assert question.options[-1].letter == "X" and question.options[-1].is_other is True
            assert all(not option.is_other for option in question.options[:-1])
        assert question.answered == (question.answer is not None)
    assert parsed.pending_count == sum(1 for question in parsed.questions if not question.answered)


def test_question_n_headings_parse_like_q_n_headings(pq):
    """A real 2.10 file titled ``## Question 1: …``: unparsed, its four tags read as unsupported and the card degraded."""
    text = (FIXTURES / "question-fallback" / "question-n-headings.md").read_text("utf-8")
    qf = pq(text)
    assert [(q.index, q.prompt) for q in qf.questions] == [
        (1, "Farewell wording"), (2, "Type hints and docstring"), (3, "Empty or non-text names"),
        (4, "How the first test is set up")]
    assert [[o.letter for o in q.options] for q in qf.questions] == [
        ["A", "B", "C", "D", "X"], ["A", "B", "C", "X"], ["A", "B", "C", "D", "X"], ["A", "B", "C", "X"]]
    assert (qf.pending_count, qf.unsupported_pending_count) == (4, 0)
    assert qf.questions[0].context.startswith("`greet(\"Ada\")` returns")


@pytest.mark.parametrize("heading", ["## Question 3 - Scope", "## Question 3 — Scope", "### Question 3: Scope",
                                     "### Q3 — Scope", "## Q3. Scope"])
def test_question_headings_accept_every_separator(pq, heading):
    qf = pq(f"{heading}\n\nA. One\nX. Other (please specify)\n\n[Answer]:\n")
    assert [(q.index, q.prompt) for q in qf.questions] == [(3, "Scope")] and qf.unsupported_pending_count == 0


def test_headings_that_only_start_like_a_question_stay_sections(pq):
    qf = pq("## Question Answered\n\n[Answer]:\n\n## Quick start 1\n\n[Answer]:\n")
    assert qf.questions == () and qf.unsupported_pending_count == 2


def test_questions_dash_and_bare_option_forms(pq):
    dashed, bare = pq(F.real_questions(*OLD_FEAS)), pq(F.real_questions(*DEV_IC))
    assert [option.letter for option in dashed.questions[0].options] == ["A", "B", "C", "X"]
    assert [option.letter for option in bare.questions[0].options] == ["A", "B", "C", "D", "E", "X"]
    assert dashed.questions[0].options[-1].text.startswith("Other")
    assert bare.questions[0].options[-1].text == "Other (please specify)"
    assert bare.questions[3].options[-1].text == "Other (please name the roles and cadence)"


def test_questions_blank_tags_are_pending(pq):
    parsed = pq(F.real_questions(*OLD_FEAS))
    assert parsed.pending_count == 6 and parsed.pending_checkpoint is None
    assert all(question.answer is None and not question.answered for question in parsed.questions)
    assert parsed.summary_confirmation is None and parsed.plan_approval is None


def test_questions_underscore_placeholder_counts_as_blank(pq):
    parsed = pq(F.questions_text(questions=[("Pick one", ["a", "b"])], answers={1: "____"}))
    assert parsed.questions[0].answered is False and parsed.pending_count == 1


def test_questions_answers_letters_prose_and_cjk(pq):
    parsed = pq(F.real_questions(*DEV_IC))
    answers = {question.index: question.answer for question in parsed.questions}
    assert answers[2].startswith("A, B. 主要使用者是个人开发者") and answers[3] == "A, B, C, D."
    assert "端到端闭环" in answers[1] and parsed.pending_count == 0


def test_questions_multi_line_answer_keeps_its_continuation_bullets(pq):
    answer = pq(F.real_questions(*DEV_RA)).questions[0].answer
    assert answer.startswith("X. 采用四个互斥分桶") and answer.count("\n") == 4
    assert "- `verified`" in answer and "- `unknown`" in answer
    assert "official credits 到达后" not in answer  # continuation stops at the first non-bullet line


def test_questions_other_answer_keeps_plain_text_continuation(pq):
    parsed = pq('## Q1: Note?\nA. No note\nX. Other\n[Answer]: X. Note with commas, "quotes", 中文.\n'
                'Second line stays with Q1.\n\n---\n## Q2: Next?\nA. Yes\n[Answer]:\n')
    assert parsed.questions[0].answer == 'X. Note with commas, "quotes", 中文.\nSecond line stays with Q1.'
    assert parsed.questions[1].answered is False


def test_questions_summary_confirmation_checkpoint(pq):
    parsed = pq(F.real_questions(*DEV_RA))
    checkpoint = parsed.summary_confirmation
    assert checkpoint.kind == "summary_confirmation" and checkpoint.answer == "Looks correct"
    assert checkpoint.present is True and checkpoint.answered is True
    assert checkpoint.options == ("Looks correct", "Request changes") and parsed.pending_checkpoint is None


def test_questions_plan_approval_checkpoint(pq):
    parsed = pq(F.real_questions(*DEV_CG))
    plan = parsed.plan_approval
    assert (plan.kind, plan.options, plan.answer) == ("plan_approval", ("Approve Plan", "Request Changes"),
                                                      "Approve Plan")
    assert plan.answered is True and parsed.summary_confirmation is None
    assert len(parsed.questions) == 1 and parsed.questions[0].answer == "A"


def test_questions_plan_approval_pending(pq):
    text = F.real_questions(F.PAYLOAD_230, F.PAYLOAD_COMPLETED, "construction/health-probe/code-generation")
    parsed = pq(text.replace("[Answer]: Approve Plan", "[Answer]:"))
    assert parsed.questions == () and parsed.pending_checkpoint == "plan_approval"
    assert parsed.plan_approval.present is True and parsed.plan_approval.answered is False
    assert parsed.pending_count == 0  # checkpoints are counted separately


#: 2.11's engine-held Plan Approval, as ``publishPlanApprovalAsk`` writes it (``aidlc-plan-approval-ask.ts``).
ENGINE_PLAN_QUESTIONS = """# Code Generation Plan Approval

AI-DLC writes this file when it asks you to approve the plan. To answer here
instead of in chat, write your answer after `[Answer]:` and say done.

## Plan Approval

Approve the code plan?

- Adds divide(a, b) to ops.py
- Adds tests in test_ops.py

Full plan: aidlc/spaces/default/intents/261013-divide/construction/code-generation/code-generation-plan.md
Test instructions: aidlc/spaces/default/intents/261013-divide/construction/code-generation/unit-test-instructions.md

[Approval Fingerprint]: sha256:v3:{fp}
[Planned Source]: {src}

- A. Approve Plan
- B. Request Changes
- C. I'll edit the files

[Answer]:{answer}
""".replace("{fp}", "9" * 64).replace("{src}", "8" * 64)


def test_questions_engine_held_plan_approval_is_lettered_and_not_session_bound(pq, R):
    parsed = pq(ENGINE_PLAN_QUESTIONS.replace("{answer}", ""))
    plan = parsed.plan_approval
    assert parsed.pending_checkpoint == "plan_approval" and parsed.questions == ()
    assert plan.options == ("A. Approve Plan", "B. Request Changes", "C. I'll edit the files")
    assert plan.engine_held is True and plan.answered is False
    assert plan.to_json()["engine_held"] is True
    answered = pq(ENGINE_PLAN_QUESTIONS.replace("{answer}", " A. Approve Plan")).plan_approval
    assert answered.answered is True and answered.answer == "A. Approve Plan"
    assert R.plan_approval_choice(answered.answer) == "Approve Plan"
    assert R.plan_approval_choice("B. Request Changes") == "Request Changes"
    assert R.plan_approval_choice("Approve Plan") == "Approve Plan"  # 2.10 records the bare choice


def test_questions_session_bound_plan_approval_carries_no_engine_flag(pq):
    plan = pq(F.real_questions(*DEV_CG)).plan_approval
    assert plan.engine_held is None and "engine_held" not in plan.to_json()


def test_questions_summary_wins_when_both_checkpoints_are_pending(pq):
    parsed = pq("# Q\n\n## Consolidated Summary Confirmation\n\n- Looks correct\n- Request changes\n\n[Answer]:\n\n"
                "## Plan Approval\n\n- Approve Plan\n- Request Changes\n\n[Answer]:\n")
    assert parsed.pending_checkpoint == "summary_confirmation" and parsed.plan_approval.answered is False


def test_questions_post_approval_amendment_yields_an_ordinary_pending_question(pq):
    """Gate card and question card can legitimately coexist (review P09)."""
    parsed = pq(F.real_questions(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN, "inception/requirements-analysis"))
    assert [question.index for question in parsed.questions] == [1, 2, 3, 4]
    assert parsed.questions[3].answered is False and parsed.pending_count == 1
    assert parsed.summary_confirmation.answered is True and parsed.pending_checkpoint is None


def test_questions_free_sections_and_answered_followups_do_not_invent_q_ids(pq):
    text = F.real_questions(F.OLDER, F.OLDER_INTENT, "ideation/market-research")
    assert "## F1." in text
    parsed = pq(text)
    assert (len(parsed.questions), parsed.pending_count, parsed.unsupported_pending_count) == (6, 0, 0)
    assert "## 权威来源" in F.real_questions(*DEV_RA) and len(pq(F.real_questions(*DEV_RA)).questions) == 4


def test_unsupported_followups_retain_pending_work_without_reusing_answered_q_ids(R, pq):
    parsed = pq(FOLLOWUPS)
    assert parsed.pending_count == parsed.unsupported_pending_count == 3
    assert parsed.to_json()["unsupported_pending_count"] == 3
    assert [q.index for q in parsed.questions] == [1, 2, 3, 4, 5] and all(q.answered for q in parsed.questions)
    assert parsed.questions[-1].answer == "A — Refuse before writes or dispatch."
    assert not R.file_questions_support_forms(parsed)


@pytest.mark.parametrize("heading", ["## F1. Follow-up", "### F1. Follow-up", "## Clarification", ""])
def test_blank_answer_tags_outside_supported_sections_require_conversation(R, pq, heading):
    parsed = pq(f"{heading}\n\nChoose in the conversation.\n[Answer]: ___\n")
    assert parsed.questions == () and parsed.pending_count == parsed.unsupported_pending_count == 1
    assert not R.file_questions_support_forms(parsed)


@pytest.mark.parametrize("example", [
    "```md\n## F99. Example\n[Answer]:\n```\n",
    "~~~~md\n## F99. Example\n[Answer]:\n~~~\n[Answer]:\n~~~~\n",
    "````md\n```\n## F99. Example\n[Answer]:\n```\n````\n",
    "> ## F99. Example\n> [Answer]:\n",
    "    ## F99. Example\n    [Answer]:\n",
])
def test_unsupported_followup_detection_ignores_fenced_quoted_and_indented_examples(pq, example):
    parsed = pq(f"# Examples\n\n{example}\n## F1. Real follow-up\n[Answer]:\n\n"
                "## Q2. A supported question\nA. Yes\n[Answer]: Yes\n")
    assert parsed.pending_count == parsed.unsupported_pending_count == 1
    assert [q.index for q in parsed.questions] == [2] and parsed.questions[0].answered


def test_unclosed_fenced_example_does_not_create_pending_followups(pq):
    parsed = pq("## Q1. Done\nA. Yes\n[Answer]: Yes\n\n```md\n## F1. Example\n[Answer]:\n")
    assert parsed.pending_count == parsed.unsupported_pending_count == 0


@pytest.mark.parametrize("checkpoint,labels,kind", [
    ("Consolidated Summary Confirmation", "- Looks correct\n- Request changes", "summary_confirmation"),
    ("Plan Approval", "- Approve Plan\n- Request Changes", "plan_approval"),
])
def test_answered_followups_leave_only_the_canonical_checkpoint_pending(R, pq, checkpoint, labels, kind):
    text = FOLLOWUPS.replace("[Answer]: ___", "[Answer]: Recorded F2").replace(
        "[Answer]:\n", "[Answer]: Recorded follow-up\n")
    parsed = pq(text + f"\n## {checkpoint}\n{labels}\n[Answer]:\n")
    assert parsed.pending_count == parsed.unsupported_pending_count == 0
    assert parsed.pending_checkpoint == kind and R.file_questions_support_forms(parsed)
    assert getattr(parsed, kind).options == tuple(line[2:] for line in labels.splitlines())


ASSUMPTION_SHEET = (
    "## Q1. Done?\nA. Yes\n[Answer]: A. Yes\n\n"
    "## Consolidated Summary Confirmation\n- Looks correct\n- Request changes\n\n[Answer]: Looks correct\n\n"
    "## Assumption Confirmation\n\nThe statement keeps one item open.\n\n"
    "- The demo also covers the extra features [assumption]\n\n"
    "A. Accept assumptions\nB. Convert to follow-up questions\n\n[Answer]:\n"
)


def test_assumption_confirmation_is_a_checkpoint_carrying_its_assumptions(R, pq):
    """2.10 intent-capture Step 5: a blank tag here is a checkpoint, asked only after the summary is confirmed."""
    parsed = pq(ASSUMPTION_SHEET)
    assert parsed.pending_count == parsed.unsupported_pending_count == 0
    assert parsed.pending_checkpoint == "assumption_confirmation" and R.file_questions_support_forms(parsed)
    checkpoint = parsed.assumption_confirmation
    assert checkpoint.options == ("A. Accept assumptions", "B. Convert to follow-up questions")
    assert checkpoint.context == (
        "The statement keeps one item open.\n\n- The demo also covers the extra features [assumption]")
    assert parsed.to_json()["assumption_confirmation"]["context"] == checkpoint.context
    assert "context" not in parsed.summary_confirmation.to_json()  # existing checkpoints keep their JSON shape
    answered = pq(ASSUMPTION_SHEET.replace("[Answer]:\n", "[Answer]: A. Accept assumptions\n"))
    assert answered.pending_checkpoint is None
    assert answered.assumption_confirmation.answer == "A. Accept assumptions"
    reopened = pq(ASSUMPTION_SHEET.replace("[Answer]: Looks correct", "[Answer]:"))
    assert reopened.pending_checkpoint == "summary_confirmation"


def test_legacy_constructors_default_the_newer_fields(R):
    assert R.QuestionsFile("questions.md", SHA0, (), None, None, 0, None, None).unsupported_pending_count == 0
    question = R.Question(1, "Legacy", (), False, None, False, "raw")
    assert question.context == "" and question.to_json()["context"] == ""


@pytest.mark.parametrize("marker", ["(multi-select)", "(MULTI SELECT)", "[multi-select]", "(select all that apply)",
                                    "（select all that apply）", "（多选）", "（可多选）"])
def test_questions_explicit_multi_select_spellings(pq, marker):
    assert only_question(pq, f"Choose applicable changes {marker}", ["Docs", "Tests"]).multi_select is True


def test_questions_multi_select_comes_from_the_prompt_never_an_option_or_background(pq):
    assert only_question(pq, "Which ones apply? (multi-select)").multi_select is True
    assert only_question(pq, "Pick one", ["a"]).multi_select is False
    parsed = pq("### Q1. Pick one\n\nBackground: (select all that apply) is another format.\n"
                "- A. Describe (multi-select)\n- X. Other\n\n[Answer]:\n")
    assert parsed.questions[0].multi_select is False


def test_questions_preserve_scope_capabilities_and_body_select_all(R, pq):
    capabilities = (
        "The eight candidate capabilities carried forward from the approved intent statement are: "
        "(1) pause and resume by exact job ID, (2) durable paused state across restart and scheduler "
        "rebuild, (3) session ownership checks on pause and resume, (4) session scoping of `list` and "
        "`delete`, (5) idempotent replies when pausing an already-paused job or resuming a running one, "
        "(6) guidance on a missing or invalid job ID argument, (7) a persistence failure reported as "
        "a failure rather than success, (8) old cron JSON files still readable with the pinned agent "
        "preserved."
    )
    instructions = ("Naming them as out keeps the scope document honest. "
                    "(Select all that should be recorded as out of scope.)")
    parsed = pq(f"## Q1. Which capabilities are Must-have?\n\n{capabilities}\n\n"
                "A. All eight are Must-have\nB. Must-have: 1, 2, 3, 7, 8\nX. Other\n\n[Answer]:\n\n"
                f"## Q3. Which adjacent capabilities are OUT of scope?\n\n{instructions}\n\n"
                "A. Pause all jobs\nB. Pause with an expiry\nX. Other\n\n[Answer]:\n")
    first, third = parsed.questions
    assert first.context == capabilities and first.to_json()["context"] == capabilities
    assert first.prompt == "Which capabilities are Must-have?" and not first.multi_select
    assert [o.text for o in first.options] == ["All eight are Must-have", "Must-have: 1, 2, 3, 7, 8", "Other"]
    assert third.context == instructions and third.multi_select
    assert parsed.pending_count == 2 and R.file_questions_support_forms(parsed)


@pytest.mark.parametrize("context", [
    "**(Select all that should be recorded as out of scope.)**",
    "__(Select all that should be recorded as out of scope.)__",
    "*(Select all that should be recorded as out of scope.)*",
    "Keep the boundary explicit. **(Select all that should be recorded as out of scope.)**",
    "(Select all that should be\nrecorded as out of scope.)",
    "**(Select\nall that should be recorded as out of scope.)**",
    "**Keep the boundary explicit. (Select all that should be recorded as out of scope.)**",
])
def test_question_context_multi_select_markdown_equivalence(R, pq, context):
    parsed = pq(f"## Q1. Which capabilities are out?\n\n{context}\n\n"
                "A. Pause all jobs\nB. Pause with expiry\nX. Other\n\n[Answer]:\n")
    assert parsed.questions[0].context == context and parsed.questions[0].multi_select is True
    assert R.file_questions_support_forms(parsed)


@pytest.mark.parametrize("context", [
    "Background: **(select all that apply)**\nis another format.",
    "Background: (select all that\napply) is another format.",
    "> Example:\n**(Select all that should be recorded as out of scope.)**",
    "**`(Select all that should be recorded as out of scope.)`**",
])
def test_question_context_markdown_examples_are_not_directives(pq, context):
    question = pq(f"## Q1. Pick one\n\n{context}\n\nA. One\nX. Other\n\n[Answer]:\n").questions[0]
    assert question.context == context and question.multi_select is False


def test_question_context_preserves_markdown_and_stops_before_answer(pq):
    context = "  - First capability\n    - Nested detail\n\nSecond paragraph."
    question = pq(f"## Q1. Explain\n \n{context}\n \n[Answer]: Saved answer\n").questions[0]
    assert question.context == context and question.answer == "Saved answer"
    assert question.options == () and not question.multi_select


@pytest.mark.parametrize("location", ["fence", "option", "answer", "after_options", "quote"])
@pytest.mark.parametrize("directive", ["(Select all that should be recorded as out of scope.)",
                                       "**(Select all that should be\nrecorded as out of scope.)**"])
def test_question_context_does_not_promote_non_instruction_markers(pq, location, directive):
    context, option, answer, tail = {
        "fence": (f"Example:\n```md\n{directive}\n## Q99. Fake\nA. Fake\n[Answer]: Fake\n```\n", "One", "", ""),
        "option": ("", directive, "", ""), "answer": ("", "One", directive, ""),
        "after_options": ("", "One", "", f"\n{directive}\n"), "quote": (f"> Background. {directive}\n", "One", "", ""),
    }[location]
    parsed = pq(f"## Q1. Pick one\n\n{context}\nA. {option}\nX. Other\n{tail}\n[Answer]: {answer}\n")
    assert len(parsed.questions) == 1
    question = parsed.questions[0]
    assert not question.multi_select and [o.letter for o in question.options] == ["A", "X"]
    assert question.context == context.rstrip("\n") and question.answer == (answer or None)


@pytest.mark.parametrize("body,enabled", [
    ("### Q1. First?\n- A. One\n[Answer]:\n### Q1. Second?\n- A. Two\n[Answer]:\n", False),
    ("### Q1. Pick one\n- A. One\n- A. Two\n[Answer]:\n", False),
    ("### Q1. Missing tag\n- A. One\n", False),
    ("### Q1. Duplicate tag\n- A. One\n[Answer]:\n[Answer]:\n", False),
    ("### Q1. Unsupported options\n- A) One\n- B. Two\n[Answer]:\n", False),
    ("### Q1. No offered options\n[Answer]:\n", False),
    ("### Q1. Pick one\n- A. One\n- X. Other\n```text\n[Answer]: not a real tag\n- A) example\n```\n[Answer]:\n", True),
], ids=["duplicate-id", "duplicate-letter", "missing-tag", "duplicate-tag", "bad-options", "no-options", "fenced"])
def test_only_unambiguous_question_files_are_enabled_as_forms(R, pq, body, enabled):
    assert R.file_questions_support_forms(pq(body)) is enabled


def test_questions_blank_answers_helper_makes_the_gate_scenario(pq):
    """``blank_answers`` on the real file is what ``gate_repo`` writes: four pending Q + blank summary."""
    parsed = pq(F.blank_answers(F.real_questions()))
    assert parsed.pending_count == 4 and parsed.pending_checkpoint == "summary_confirmation"
    assert parsed.summary_confirmation.answered is False
    assert parsed.summary_confirmation.options == ("Looks correct", "Request changes")
    assert parsed.questions[0].options[-1].is_other is True


def test_question_digest_is_over_the_whole_file(R):
    data = F.real_questions().encode()
    assert R.question_digest(data) == hashlib.sha256(data).hexdigest() and R.question_digest(None) is None
    assert R.question_digest(data + b"\n") != R.question_digest(data)


def test_read_questions_returns_none_when_absent(reader, devdelta_repo):
    stage_dir = reader.stage_dir("default", F.DEVDELTA_POC, "construction", "build-and-test", None)
    assert reader.read_questions(devdelta_repo, stage_dir, "build-and-test") is None
    per_unit = reader.stage_dir("default", F.DEVDELTA_POC, "construction", "code-generation", "kiro-impact-poc")
    found = reader.read_questions(devdelta_repo, per_unit, "code-generation")
    assert found.relpath == f"{per_unit}/code-generation-questions.md"
    assert found.sha256 == hashlib.sha256((devdelta_repo / found.relpath).read_bytes()).hexdigest()


# --- engine data: stage graph, scope grid, scopes, stage files ---
def test_stage_graph_33_stages(R):
    nodes = R.parse_stage_graph(json.dumps(F.stage_graph(F.DEVDELTA)).encode())
    by = {node.slug: node for node in nodes}
    assert len(nodes) == 33 and "domain-design" in by and "contract-design" in by and "application-design" not in by
    node = by["requirements-analysis"]
    assert (node.number, node.phase, node.reviewer, node.reviewer_max_iterations) == (
        "2.3", "inception", "aidlc-product-lead-agent", 2)
    assert (node.review_class, node.summary_confirmation) == ("advisory", "required")
    assert node.produces == ("requirements", "requirements-analysis-questions")
    assert [consume.artifact for consume in node.consumes][:2] == ["intent-statement", "scope-document"]
    assert node.consumes[2].conditional_on == "brownfield" and not any(c.required for c in node.consumes)
    code_gen = by["code-generation"]
    assert (code_gen.for_each, code_gen.mode, code_gen.review_class) == ("unit-of-work", "subagent", "adversarial")
    assert code_gen.per_unit is True and code_gen.workspace_requires is True
    assert any(consume.required for consume in code_gen.consumes)
    assert by["functional-design"].optional_produces == ("frontend-components",)
    assert by["functional-design"].produces_kinds["frontend-components"] == ("ui",)


@pytest.mark.parametrize("tree", [F.PAYLOAD_230, F.OLDER])
def test_stage_graph_32_stages_without_the_newer_keys(R, tree):
    nodes = R.parse_stage_graph(json.dumps(F.stage_graph(tree)).encode())
    by = {node.slug: node for node in nodes}
    assert len(nodes) == 32 and "application-design" in by and "contract-design" not in by
    assert by["requirements-analysis"].review_class is None and by["requirements-analysis"].summary_confirmation is None
    assert by["code-generation"].per_unit is True and by["code-generation"].produces_kinds == {}


def test_stage_graph_tolerates_junk(R):
    assert R.parse_stage_graph(b"") == [] and R.parse_stage_graph(b"{}") == []
    (node,) = R.parse_stage_graph(json.dumps({"stages": [{"slug": "only-slug"}, {"name": "no slug"}, "junk"]}).encode())
    assert (node.slug, node.name, node.phase, node.produces, node.consumes) == ("only-slug", "only-slug", "", (), ())
    assert node.workspace_requires is False and node.reviewer_max_iterations is None


def test_scope_grid_including_a_project_composed_scope(R):
    grid = R.parse_scope_grid(json.dumps(F.scope_grid(F.DEVDELTA)).encode())
    assert len(grid) == 10 and "review-major-remediation" in grid
    assert (grid["poc"]["requirements-analysis"], grid["poc"]["market-research"]) == ("EXECUTE", "SKIP")
    assert len(R.parse_scope_grid(json.dumps(F.scope_grid(F.PAYLOAD_230)).encode())) == 9
    assert R.parse_scope_grid(b"[]") == {}
    assert R.parse_scope_grid(json.dumps({"flat": {"a": "EXECUTE"}}).encode()) == {"flat": {"a": "EXECUTE"}}


def test_scope_frontmatter_stock_and_project_owned(R):
    def scope(name):
        return R.parse_scope_frontmatter((F.DEVDELTA / f".kiro/scopes/aidlc-{name}.md").read_text("utf-8"),
                                         f"aidlc-{name}.md")

    poc = scope("poc")
    assert (poc.name, poc.depth, poc.skeleton, poc.review_cap) == ("poc", "Minimal", "on", "advisory")
    assert poc.keywords == ("proof of concept", "prototype", "poc", "spike")
    assert poc.description == "Prove feasibility fast"
    assert poc.project_owned is False and poc.runner is None and poc.test_strategy is None
    assert scope("workshop").test_strategy == "Minimal"  # camelCase on disk
    assert scope("bugfix").runner is True
    composed = scope("review-major-remediation")
    assert (composed.name, composed.keywords, composed.skeleton) == ("review-major-remediation", (), "off")
    assert composed.project_owned is True
    assert R.parse_scope_frontmatter("# no frontmatter\n", "aidlc-x.md") is None


def test_read_scopes_and_grid_from_a_repo(reader, devdelta_repo):
    scopes = reader.read_scopes(devdelta_repo, ".kiro")
    assert [scope.name for scope in scopes][:3] == ["bugfix", "enterprise", "feature"] and len(scopes) == 10
    assert [scope.name for scope in scopes if scope.project_owned] == ["review-major-remediation"]
    assert set(reader.read_scope_grid(devdelta_repo, ".kiro")) == {scope.name for scope in scopes}
    assert len(reader.read_stage_graph(devdelta_repo, ".kiro")) == 33


def test_stage_file_acceptance_criteria(R, reader, repo_builder):
    meta = R.parse_stage_file(
        "---\nslug: x\n---\n\n# X\n\n## Acceptance Criteria\n\n- one\n- two\n\n## Steps\n\n- not a criterion\n", "x")
    assert meta.slug == "x" and meta.acceptance_criteria == ("one", "two")
    assert R.parse_stage_file("# X\n\n### Definition of Done\n- done\n", "x").acceptance_criteria == ("done",)
    assert R.parse_stage_file("# X\n\n## Steps\n- nope\n", "x").acceptance_criteria == ()
    repo = repo_builder.with_engine("payload").build()
    real = reader.read_stage_file(repo, ".kiro", "inception", "requirements-analysis")
    assert real is not None and real.acceptance_criteria == ()  # 2.6.2 declares none
    assert reader.read_stage_file(repo, ".kiro", "inception", "no-such-stage") is None
    assert reader.read_stage_file(repo, ".kiro", "not-a-phase", "requirements-analysis") is None


@pytest.mark.parametrize("tree,count", [(F.DEVDELTA, 33), (F.PAYLOAD_230, 32)])
def test_every_harvested_stage_frontmatter_file_parses(R, tree, count):
    files = sorted((tree / "stage-frontmatter").rglob("*.md"))
    assert len(files) == count
    for path in files:
        meta = R.parse_stage_file(path.read_text("utf-8"), path.stem)
        assert meta.slug == path.stem and meta.acceptance_criteria == ()  # no engine generation declares any
    graph = {node.slug for node in R.parse_stage_graph(json.dumps(F.stage_graph(tree)).encode())}
    assert {path.stem for path in files} == graph  # frontmatter and graph name the same stages


def test_stage_graph_carries_the_review_artifact_and_absorbs_a_graph_without_one(reader, repo_builder, R):
    """``review_artifact`` (2.7.1) is read, never guessed from ``produces[0]``; older graphs parse with ``None``."""
    graph = {node.slug: node for node in reader.read_stage_graph(repo_builder.with_engine("payload").build(), ".kiro")}
    assert {slug: graph[slug].review_artifact for slug in (
        "functional-design", "nfr-requirements", "nfr-design", "infrastructure-design", "requirements-analysis")} == {
        "functional-design": "functional-spec", "nfr-requirements": "security-requirements",
        "nfr-design": "security-design", "infrastructure-design": "cicd-pipeline",
        "requirements-analysis": "requirements"}
    assert graph["functional-design"].produces[0] == "entities"  # the four disagreements are real
    assert graph["requirements-analysis"].to_json()["review_artifact"] == "requirements"
    assert sum(1 for node in graph.values() if node.review_artifact) == 13
    assert graph["workspace-scaffold"].review_artifact is None  # no reviewer, no reviewed artifact
    old = {node.slug: node for node in R.parse_stage_graph(json.dumps(F.stage_graph(F.PAYLOAD_230)).encode())}
    assert all(node.review_artifact is None for node in old.values())
    assert old["functional-design"].produces and old["functional-design"].reviewer  # the rest still parsed


def test_detect_harness_dirs_per_engine_generation(reader, repo_builder, tmp_path, studio):
    repo = repo_builder.with_engine("payload").build()
    (payload,) = reader.detect_harness_dirs(repo)
    assert (payload.dir, payload.harness_name, payload.rules_subdir) == (".kiro", "kiro", "steering")  # "name": 2.6.2+
    assert payload.engine_version == studio.constants.BUNDLED_ENGINE_VERSION  # the literal belongs to `constants`
    assert (payload.stage_count, payload.engine_state_version) == (33, 8) and payload.has_utility is True
    assert len(reader.read_stage_graph(repo, ".kiro")) == 33
    # every payload scope is stock; counted from disk so a scope a future engine adds cannot slip past STOCK_SCOPES
    scopes = reader.read_scopes(repo, ".kiro")
    assert len(scopes) == len(sorted((repo / ".kiro" / "scopes").glob("aidlc-*.md"))) == 11
    assert all(not scope.project_owned for scope in scopes)
    older = reader.detect_harness_dirs(RepoBuilder(tmp_path / "older").with_engine("older").build())
    assert (older[0].engine_version, older[0].stage_count, older[0].harness_name) == ("2.1.1", 32, None)
    (tmp_path / "bare" / ".cursor" / "tools").mkdir(parents=True)  # a tools dir that is not an engine
    assert reader.detect_harness_dirs(tmp_path / "bare") == []


# --- review sections ---
def test_review_section_uses_the_last_section_and_maps_levels(R):
    verdict, findings = R.parse_review_section(
        "# A\n\n## Review\n\nVerdict: NOT-READY\n\n### Blocker\n\n- first pass gap\n\n"
        "## Body\n\nprose\n\n## Review\n\n**Verdict:** READY\n**Reviewer:** aidlc-x-agent\n"
        "**Iteration:** 2\n\n### Blockers\n\n- `FR2.2` lacks a test\n\n### Advisory notes\n\n"
        "- naming drift\n\n### Resolved\n\n- earlier gap\n\n### Other\n\n- unclassified\n")
    assert verdict == "READY"
    assert [finding.level for finding in findings] == ["blocker", "advisory", "resolved", "unknown"]
    assert (findings[0].title, findings[0].quote) == ("`FR2.2` lacks a test", "FR2.2")
    assert all(finding.reviewer == "aidlc-x-agent" and finding.iteration == 2 for finding in findings)
    assert "first pass gap" not in [finding.title for finding in findings]
    assert R.parse_review_section("# artifact\n\nno review\n") == (None, [])


def test_a_reviewers_table_of_findings_is_never_reported_as_no_findings(R):
    """The real 2.6.2 reviewers write findings as a table; an empty list would tell the gate "no blockers"."""
    verdict, findings = review_of(R, "inception/requirements-analysis/requirements.md")
    assert verdict == "READY" and len(findings) == 3 and findings.findings_parsed is True
    # the reviewer's own severity word is kept verbatim: Studio does not re-grade a reviewer
    assert [finding.title.split(" | ")[1] for finding in findings] == ["Major", "Minor", "Minor"]
    assert {finding.level for finding in findings} == {"unknown"}
    assert all(f.reviewer == "aidlc-product-lead-agent" and f.iteration == 1 for f in findings)
    assert not any(finding.title.startswith("#") for finding in findings)  # the heading row is not a finding


def test_a_reviewers_tool_output_table_is_not_promoted_to_findings(R):
    """The real build-and-test review lists one Minor finding and then nine rows of validation-tool output."""
    verdict, findings = review_of(R, "construction/build-and-test/build-and-test-summary.md")
    assert verdict == "READY" and len(findings) == 1 and findings.findings_parsed is True
    assert findings[0].title.startswith("1 | Minor | ")


def test_a_findings_table_under_a_level_heading_keeps_that_headings_level(R):
    _, findings = R.parse_review_section(
        "# A\n\n## Review\n\n**Verdict:** NOT-READY\n\n### Blockers\n\n"
        "| # | Severity | Finding |\n|---|---|---|\n| 1 | Critical | `units.yaml` has no owner |\n")
    assert [finding.level for finding in findings] == ["blocker"]
    assert (findings[0].title, findings[0].quote) == ("1 | Critical | `units.yaml` has no owner", "units.yaml")


def test_a_review_table_studio_cannot_place_is_never_proof_that_there_are_no_blockers(R):
    """Rows under an unknown heading are not findings, and the empty list is then not the reviewer's answer."""
    verdict, findings = R.parse_review_section(
        "# A\n\n## Review\n\n**Verdict:** NOT-READY\n\n### Observations\n\n"
        "| # | Severity | Finding |\n|---|---|---|\n| 1 | Critical | the schema is not versioned |\n")
    assert verdict == "NOT-READY" and findings == [] and findings.findings_parsed is False
    # "the reviewer listed nothing" stays sayable: the real intent-statement review is a bare verdict line
    verdict, bare = review_of(R, "ideation/intent-capture/intent-statement.md")
    assert verdict == "NOT-READY" and bare == [] and bare.findings_parsed is True
    _, none_listed = R.parse_review_section(  # a named tool table beside "None." is not a failure to read
        "# A\n\n## Review\n\n**Verdict:** READY\n\n### Findings\n\nNone.\n\n"
        "### Validation Tool Results\n\n| Tool | Result |\n|---|---|\n| validate-entities | PASS |\n")
    assert none_listed == [] and none_listed.findings_parsed is True


# --- breadcrumb, markers, derived JSON ---
def test_recovery_breadcrumb_has_no_leading_dash(R):
    assert R.parse_recovery_breadcrumb(
        "# AIDLC Recovery Breadcrumb\n**Last validated**: 2026-09-02T03:53:25Z\n"
        "**Current stage**: build-and-test\n**State file**: valid (all required sections present)\n") == {
        "Last validated": "2026-09-02T03:53:25Z", "Current stage": "build-and-test",
        "State file": "valid (all required sections present)"}


def test_markers_of_a_real_record(reader, devdelta_repo):
    markers = reader.read_markers(devdelta_repo, "default", F.DEVDELTA_POC)
    assert markers.human_turn_mtime_ns and markers.engine_touch_mtime_ns
    assert markers.recovery["Current stage"] == "build-and-test" and markers.turn_counter == 27
    assert markers.goal_stop_present is True  # present, and deliberately not authoritative
    assert markers.compose_pending is False and markers.hooks_health == {}
    assert markers.stop_block_count is None and markers.reviewer_dispatch is None


def test_markers_absent_everywhere(reader, repo_builder):
    markers = reader.read_markers(one_intent(repo_builder), "default", X)
    assert markers.human_turn_mtime_ns is None and markers.recovery is None and markers.turn_counter is None
    assert markers.goal_stop_present is False and markers.to_json()["hooks_health"] == {}


@pytest.mark.parametrize("prefix", [".aidlc-", ".aidlc-engine/"], ids=["record-root", "2.10-engine-dir"])
def test_markers_optional_files(reader, repo_builder, prefix):
    repo = one_intent(repo_builder)
    hooks, stop, dispatch, human, touch = (repo / REC / f"{prefix}{name}" for name in (
        "hooks-health", "stop-hook", "reviewer-dispatch.json", "human-turn", "engine-touch"))
    hooks.mkdir(parents=True)
    stop.mkdir()
    for path, text in ((hooks / "validate-state.last", "2026-09-04T09:59:00Z\n"), (hooks / "x.drops", "ignored\n"),
                       (stop / "block-count.json", '{"signature":"","count":2}'), (human, ""), (touch, ""),
                       (dispatch, '{"reviewer":"r","stage":"code-generation"}'),
                       (repo / "aidlc/.aidlc-compose-pending", ""), (repo / "aidlc/.aidlc-turn-counter", "41\n")):
        path.write_text(text)
    markers = reader.read_markers(repo, "default", X)
    assert markers.hooks_health == {"validate-state": "2026-09-04T09:59:00Z"}
    assert markers.stop_block_count == {"signature": "", "count": 2}
    assert markers.reviewer_dispatch["stage"] == "code-generation"
    assert markers.human_turn_mtime_ns == human.stat().st_mtime_ns
    assert markers.engine_touch_mtime_ns == touch.stat().st_mtime_ns
    assert markers.compose_pending is True and markers.turn_counter == 41


def test_engine_directory_wins_over_a_legacy_marker_of_the_same_name(R, repo_builder):
    """The engine only falls back when the new entry is absent, so a stale legacy file must not win."""
    repo = one_intent(repo_builder)
    (repo / REC / ".aidlc-engine/hooks-health").mkdir(parents=True)
    (repo / REC / ".aidlc-hooks-health").mkdir()
    assert R.record_file(repo, REC, R.HOOKS_HEALTH_DIRNAME).parent.name == ".aidlc-engine"
    (repo / REC / ".aidlc-engine/hooks-health").rmdir()
    assert R.record_file(repo, REC, R.HOOKS_HEALTH_DIRNAME).name == ".aidlc-hooks-health"


def test_markers_without_an_engine_fallback_ignore_legacy_copies_once_the_directory_exists(R, repo_builder):
    """An upgraded record keeps 2.7.1's root markers, but 2.10.0 never reads them back."""
    repo = one_intent(repo_builder)
    legacies = (R.HUMAN_TURN_FILENAME, R.ENGINE_TOUCH_FILENAME, R.RECOVERY_FILENAME, R.REVIEWER_DISPATCH_FILENAME)
    for legacy in legacies:
        (repo / REC / legacy).write_text("")
        assert R.record_file(repo, REC, legacy).name == legacy
    (repo / REC / ".aidlc-engine").mkdir()
    for legacy in legacies:
        path = R.record_file(repo, REC, legacy)
        assert path.parent.name == ".aidlc-engine" and not path.exists()
    (repo / REC / R.DIRECTIVE_FILENAME).write_text("{}")
    assert R.record_file(repo, REC, R.DIRECTIVE_FILENAME).name == R.DIRECTIVE_FILENAME


def test_a_non_directory_engine_entry_is_not_absence(R, repo_builder):
    """Like ``engineReadDirFor``: a file at ``.aidlc-engine`` keeps the read on the (missing) new path."""
    repo = one_intent(repo_builder)
    (repo / REC / ".aidlc-engine").write_text("not a directory")
    (repo / REC / ".aidlc-active-directive.json").write_text("{}")
    path = R.record_file(repo, REC, R.DIRECTIVE_FILENAME)
    assert path.name == "active-directive.json" and not path.exists()


def test_directive_reads_the_2_10_marker_and_names_its_file(reader, repo_builder):
    repo = one_intent(repo_builder)
    snap, _ = reader.read_state(repo, "default", X)
    (repo / REC / ".aidlc-engine").mkdir()
    (repo / REC / ".aidlc-engine/active-directive.json").write_text(json.dumps(
        {"version": 2, "kind": "run-stage", "stage": "requirements-analysis", "state_sha256": snap.sha256}))
    directive = reader.read_directive(repo, "default", X, snap.sha256)
    assert directive.stage == "requirements-analysis" and directive.matches_state is True
    assert directive.marker_name == ".aidlc-engine/active-directive.json"
    assert "marker_name" not in directive.to_json()


# --- stable reads ---
def test_stable_read_of_a_settled_file(R, repo_builder):
    repo = one_intent(repo_builder)
    snap = R.stable_read(repo, f"{REC}/aidlc-state.md", R.C.MAX_STATE_BYTES)
    assert (snap.relpath, snap.size, snap.sha256) == (f"{REC}/aidlc-state.md", len(snap.bytes), GATE_STATE_SHA)
    assert snap.sha256 == hashlib.sha256((repo / REC / "aidlc-state.md").read_bytes()).hexdigest()
    assert snap.unstable is False and snap.text.startswith("# AI-DLC State Tracking")
    assert "bytes" not in snap.to_json()


def test_stable_read_missing_and_secret_and_oversize(R, repo_builder, studio):
    repo = one_intent(repo_builder)
    assert R.stable_read(repo, f"{REC}/nope.md", 1024) is None
    (repo / REC / "usage-ledger.json").write_text("{}")
    assert R.stable_read(repo, f"{REC}/usage-ledger.json", 1024) is None
    with pytest.raises(studio.errors.StudioError) as excinfo:
        R.stable_read(repo, f"{REC}/aidlc-state.md", 16)
    assert (excinfo.value.code, excinfo.value.status) == ("too_large", 413)
    with pytest.raises(studio.errors.StudioError) as excinfo:
        R.stable_read(repo, "../outside.md", 1024)
    assert excinfo.value.code == "bad_path"


def test_stable_read_reports_a_file_that_moved_between_the_two_stats(R, reader, repo_builder, monkeypatch):
    """An unstable snapshot still carries bytes, but nothing may be authorised from it."""
    repo = one_intent(repo_builder)
    force_unstable(monkeypatch, (repo / REC / "aidlc-state.md").resolve())
    snap = R.stable_read(repo, f"{REC}/aidlc-state.md", 512 * 1024)
    assert snap.unstable is True and snap.to_json()["unstable"] is True
    assert snap.sha256 == GATE_STATE_SHA  # the second read's bytes are returned
    boundary = reader.stable_boundary_inputs(repo, "default", X)
    assert boundary.stable is False and boundary.state_snap.unstable is True
    assert boundary.state.current_stage == "requirements-analysis"


def test_stable_boundary_inputs_of_a_settled_gate(reader, gate_repo):
    boundary = reader.stable_boundary_inputs(gate_repo, "default", "260904-gate-demo")
    assert boundary.stable is True and boundary.stage == "requirements-analysis"
    assert boundary.state.row("requirements-analysis").state == "awaiting_approval"
    assert boundary.directive.matches_state is True and boundary.audit.complete is True
    assert boundary.questions.pending_count == 4 and boundary.questions.pending_checkpoint == "summary_confirmation"
    assert boundary.taken_at == "2026-09-04T10:00:00Z" and boundary.to_json()["stable"] is True


def test_stable_boundary_inputs_without_a_state_file(reader, repo_builder):
    repo = repo_builder.with_workspace().build()
    (repo / "aidlc/spaces/default/intents/260904-ghost").mkdir(parents=True)
    boundary = reader.stable_boundary_inputs(repo, "default", "260904-ghost")
    assert boundary.stable is False and boundary.state is None and boundary.state_snap is None
    assert boundary.stage is None and boundary.questions is None


PLAN_APPROVAL_QUESTIONS = "# Plan\n\n## Plan Approval\n\n- Approve Plan\n- Request Changes\n\n[Answer]:\n"
PER_UNIT_GATE_STATE = F.state_text(marks={"code-generation": "?"}, fields={
    "Current Stage": "code-generation", "Status": "Running", "Next Stage": "build-and-test"})
UNIT_QUESTIONS = "construction/{}/code-generation/code-generation-questions.md"


def test_stable_boundary_inputs_finds_a_per_unit_questions_file(reader, repo_builder):
    repo = one_intent(repo_builder, state=F.insert_runtime_field(PER_UNIT_GATE_STATE, "Active Unit", "unit-a"),
                      audit=F.gate_open_audit("code-generation"),
                      questions={UNIT_QUESTIONS.format("unit-a"): PLAN_APPROVAL_QUESTIONS})
    boundary = reader.stable_boundary_inputs(repo, "default", X)
    assert boundary.stable is True and boundary.stage == "code-generation"
    assert boundary.questions.relpath.endswith(UNIT_QUESTIONS.format("unit-a"))
    assert boundary.questions.pending_checkpoint == "plan_approval" and boundary.unit_ambiguous is False
    assert reader.list_units(repo, "default", X) == ["unit-a"]


def two_units(reader, studio, repo_builder, *holding_questions):
    """``unit-a`` and ``unit-b`` and nothing naming the executing one: the boundary, units and findings."""
    repo = one_intent(repo_builder, state=PER_UNIT_GATE_STATE, audit=F.gate_open_audit("code-generation"),
                      questions={UNIT_QUESTIONS.format(unit): PLAN_APPROVAL_QUESTIONS for unit in holding_questions},
                      artifacts={f"construction/unit-{u}/code-generation/code-generation-plan.md": f"# {u}\n"
                                 for u in "ab"})
    boundary, units = reader.stable_boundary_inputs(repo, "default", X), reader.list_units(repo, "default", X)
    snap = SimpleNamespace(space="default", intent_dir=X, intent_key=f"default/{X}", state_snap=boundary.state_snap,
                           state=boundary.state, directive=boundary.directive, audit=boundary.audit,
                           questions=boundary.questions, stage=boundary.stage, unit=None, units=tuple(units),
                           stable=boundary.stable)
    engine = studio.consistency.ConsistencyEngine()
    facts = studio.consistency.RepoFacts(now="2026-09-04T10:00:00Z", repo=SimpleNamespace(), install=SimpleNamespace())
    return boundary, units, engine, engine.evaluate_intent(snap, facts)


def test_two_units_holding_questions_and_no_named_unit_yield_no_questions_and_block(reader, studio, repo_builder):
    """Returning the first candidate would deliver an approval of ``unit-a``'s plan as ``unit-b``'s."""
    boundary, units, engine, findings = two_units(reader, studio, repo_builder, "unit-a", "unit-b")
    assert units == ["unit-a", "unit-b"] and boundary.stage == "code-generation" and boundary.questions is None
    assert boundary.unit_ambiguous is True and boundary.to_json()["unit_ambiguous"] is True
    reported = [finding for finding in findings if finding.code == "unit_ambiguous"]
    assert len(reported) == 1 and reported[0].severity == "blocking" and reported[0] in engine.blocking(findings)
    assert reported[0].message_key == "finding.unit_ambiguous" and reported[0].params["units"] == ["unit-a", "unit-b"]
    assert reported[0].params["directive_unit"] is None and reported[0].params["state_unit"] is None
    assert "unit_ambiguous" in studio.consistency.BLOCKING_FINDING_CODES
    assert json.loads(json.dumps(reported[0].to_json())) == reported[0].to_json()


def test_the_only_unit_holding_questions_is_resolved_from_its_path_and_not_reported(reader, studio, repo_builder):
    boundary, _, _, findings = two_units(reader, studio, repo_builder, "unit-b")
    assert boundary.unit_ambiguous is False
    assert boundary.questions.relpath.endswith(UNIT_QUESTIONS.format("unit-b"))
    assert [finding.code for finding in findings if finding.code == "unit_ambiguous"] == []


# --- artifacts and units ---
def test_list_artifacts_of_a_real_record(reader, devdelta_repo):
    artifacts = reader.list_artifacts(devdelta_repo, "default", F.DEVDELTA_POC)
    by_name = {meta.name: meta for meta in artifacts}
    assert artifacts.truncated is False
    assert [meta.relpath for meta in artifacts] == sorted(meta.relpath for meta in artifacts)
    # a small record: every file is hashed
    assert all(meta.relpath.startswith("aidlc/spaces/default/intents/") and meta.sha256 for meta in artifacts)
    questions = by_name["requirements-analysis-questions.md"]
    assert (questions.phase, questions.stage, questions.unit, questions.kind) == (
        "inception", "requirements-analysis", None, "questions")
    assert questions.renderable is True and questions.mtime.endswith("Z")
    assert questions.artifact_id == hashlib.sha1(questions.relpath.encode()).hexdigest()
    per_unit, summary = by_name["code-generation-plan.md"], by_name["build-and-test-summary.md"]
    assert (per_unit.phase, per_unit.stage, per_unit.unit) == ("construction", "code-generation", "kiro-impact-poc")
    assert (summary.stage, summary.unit) == ("build-and-test", None)  # once-per-workflow layout
    assert by_name["memory.md"].kind == "memory"
    assert by_name["aidlc-state.md"].stage is None and by_name["aidlc-state.md"].kind == "other"


def test_list_artifacts_skips_dot_entries_and_secrets(reader, repo_builder):
    repo = one_intent(repo_builder, markers=[".aidlc-human-turn"], artifacts={
        "inception/requirements-analysis/requirements.md": "# R\n",
        ".aidlc-sensors/build-and-test/type-check-01.md": "finding\n", "usage-ledger.json": "{}", ".env": "SECRET=1\n"})
    names = {meta.name for meta in reader.list_artifacts(repo, "default", X)}
    assert "requirements.md" in names
    assert names.isdisjoint({"usage-ledger.json", ".env", ".aidlc-human-turn", "type-check-01.md"})


def test_list_artifacts_respects_the_cap_and_the_deadline(R, reader, repo_builder, monkeypatch, studio):
    repo = one_intent(repo_builder, artifacts={f"inception/requirements-analysis/a{i}.md": "x" for i in range(6)})
    monkeypatch.setattr(studio.constants, "MAX_ARTIFACTS_PER_INTENT", 4)
    capped = reader.list_artifacts(repo, "default", X)
    assert len(capped) == 4 and capped.truncated is True and capped.reason == "cap"
    step = studio.constants.REPO_SCAN_DEADLINE_SECS  # every monotonic() call jumps past a deadline
    ticking = SimpleNamespace(now=lambda: 1788500000.0, iso=lambda: "2026-09-04T10:00:00Z",
                              monotonic=itertools.count(step, step).__next__)
    timed_out = R.AidlcReader(ticking).list_artifacts(repo, "default", X)
    assert timed_out.truncated is True and timed_out.reason == "deadline"


def test_list_artifacts_hash_limit(R, reader, repo_builder, monkeypatch):
    repo = one_intent(repo_builder, artifacts={f"inception/requirements-analysis/a{i}.md": "x" for i in range(4)})
    monkeypatch.setattr(R, "WHOLE_RECORD_HASH_LIMIT", 2)
    assert all(meta.sha256 is None for meta in reader.list_artifacts(repo, "default", X))


def test_stage_artifacts_always_hashes(reader, devdelta_repo):
    stage_dir = reader.stage_dir("default", F.DEVDELTA_POC, "inception", "requirements-analysis", None)
    node = next(n for n in reader.read_stage_graph(devdelta_repo, ".kiro") if n.slug == "requirements-analysis")
    metas = reader.stage_artifacts(devdelta_repo, stage_dir, node)
    assert [meta.name for meta in metas] == ["requirements-analysis-questions.md", "requirements.md"]
    assert all(meta.sha256 for meta in metas)
    assert metas[1].sha256 == hashlib.sha256((devdelta_repo / metas[1].relpath).read_bytes()).hexdigest()
    assert (metas[1].stage, metas[1].phase) == ("requirements-analysis", "inception")
    assert reader.stage_artifacts(devdelta_repo, stage_dir, None) == metas
    empty_dir = reader.stage_dir("default", F.DEVDELTA_POC, "operation", "deployment-pipeline", None)
    assert reader.stage_artifacts(devdelta_repo, empty_dir, node) == []


def test_stage_dir_per_unit_and_plain(reader):
    record = "aidlc/spaces/default/intents/260828-x/"
    assert [reader.stage_dir("default", "260828-x", *args) for args in (
        ("inception", "requirements-analysis", None), ("construction", "code-generation", "unit-a"),
        ("construction", "build-and-test", "unit-a"),  # a once-per-workflow stage ignores the unit
    )] == [record + "inception/requirements-analysis", record + "construction/unit-a/code-generation",
           record + "construction/build-and-test"]
    assert reader.stage_dir("team-space", "260828-x", "ideation", "feasibility", None) == (
        "aidlc/spaces/team-space/intents/260828-x/ideation/feasibility")


def test_list_units_excludes_stage_directories(reader, devdelta_repo):
    assert reader.list_units(devdelta_repo, "default", F.DEVDELTA_POC) == ["kiro-impact-poc"]
    assert reader.list_units(devdelta_repo, "default", F.DEVDELTA_REMEDIATION) == []


def test_read_artifact(reader, devdelta_repo, studio):
    rel = f"aidlc/spaces/default/intents/{F.DEVDELTA_POC}/inception/requirements-analysis/requirements.md"
    snap = reader.read_artifact(devdelta_repo, rel)
    assert snap.sha256 == hashlib.sha256((devdelta_repo / rel).read_bytes()).hexdigest() and snap.unstable is False
    assert reader.read_artifact(devdelta_repo, "aidlc/nope.md") is None
    with pytest.raises(studio.errors.StudioError):
        reader.read_artifact(devdelta_repo, "/etc/passwd")


# --- token formulas: golden values (a formula change must be a visible diff here) ---
def test_boundary_token_golden(R):
    events = events_of(R, GATE_AUDIT)
    assert R.latest_event(events, ("STAGE_AWAITING_APPROVAL",)).timestamp == "2026-09-04T09:00:03Z"
    token = R.boundary_token(GATE_STATE_SHA, events, "requirements-analysis", 0)
    formula = f"{GATE_STATE_SHA}:2026-09-04T09:00:03Z:requirements-analysis:0"
    assert token == hashlib.sha256(formula.encode()).hexdigest()
    assert token == "9ff720ed0532e8345cf441a02fb7c7cef9dca5f0f31b260889b01326fc7d4b35"


def test_boundary_token_moves_only_with_the_boundary(R):
    events = events_of(R, GATE_AUDIT)
    base = R.boundary_token(GATE_STATE_SHA, events, "requirements-analysis", 0)
    noisy = events_of(R, GATE_AUDIT, F.audit_block("SUBAGENT_COMPLETED", "2026-09-04T09:30:00Z"))
    assert R.boundary_token(GATE_STATE_SHA, noisy, "requirements-analysis", 0) == base
    moved = events_of(R, F.gate_approved_audit("requirements-analysis"))
    assert R.boundary_token(GATE_STATE_SHA, moved, "requirements-analysis", 0) != base
    assert R.boundary_token(GATE_STATE_SHA, events, "requirements-analysis", 1) != base
    assert R.boundary_token("f" * 64, events, "requirements-analysis", 0) != base
    no_events = R.boundary_token(GATE_STATE_SHA, [], None, 0)
    assert no_events == hashlib.sha256(f"{GATE_STATE_SHA}:none:none:0".encode()).hexdigest()


def test_stage_attempt_counts_revising_rows_per_stage(R):
    events = events_of(R, F.gate_rejected_audit("requirements-analysis"))
    assert R.stage_attempt(events, "requirements-analysis") == 2 and R.stage_attempt(events, "user-stories") == 1
    assert R.stage_attempt([], "requirements-analysis") == 1
    three = events_of(R, *(F.audit_block("STAGE_REVISING", f"2026-09-04T09:0{i}:00Z", Stage="requirements-analysis",
                                         Revision_count=str(i + 1)) for i in range(3)))
    assert R.stage_attempt(three, "requirements-analysis") == 4  # attempt 4 is when `accept_as_is` is offerable


def test_evidence_digest_golden(R):
    def meta(artifact_id, name, size, sha256, kind):
        return R.ArtifactMeta(artifact_id=artifact_id, relpath=f"{REC}/inception/requirements-analysis/{name}",
                              name=name, stage="requirements-analysis", phase="inception", unit=None, size=size,
                              mtime="2026-09-04T09:00:00Z", sha256=sha256, kind=kind, renderable=True)

    artifacts = [meta("i2", "requirements.md", 10, "b" * 64, "artifact"),
                 meta("i1", "requirements-analysis-questions.md", 20, "a" * 64, "questions")]
    review = ("READY", [R.ReviewFinding("advisory", "naming drift", None, None, "aidlc-x-agent", 1)])
    digest = R.evidence_digest(artifacts, review)
    review_json = json.dumps({"verdict": "READY", "findings": [review[1][0].to_json()]}, sort_keys=True,
                             separators=(",", ":"))
    rows = "\n".join(f"{path}:{sha}" for path, sha in sorted((m.relpath, m.sha256) for m in artifacts))
    review_sha = hashlib.sha256(review_json.encode()).hexdigest()
    assert digest == hashlib.sha256((rows + "\n" + review_sha).encode()).hexdigest()
    assert digest == "68a5c2d2fb572f45fb8b39eddde443fb82ece7214a825ad6bda0940200391a55"
    # order independent, content dependent, and null-vs-non-null review is a difference (C30)
    assert R.evidence_digest(list(reversed(artifacts)), review) == digest
    assert R.evidence_digest(artifacts, None) != digest
    assert R.evidence_digest(artifacts, ("NOT-READY", review[1])) != digest
    null_sha = hashlib.sha256(b"null").hexdigest()
    assert R.evidence_digest([], None) == hashlib.sha256(("\n" + null_sha).encode()).hexdigest()
    assert R.evidence_digest(None, None) is None


def test_evidence_digest_notices_an_artifact_rewritten_under_the_card(reader, R, repo_builder):
    repo = one_intent(repo_builder, artifacts={"inception/requirements-analysis/requirements.md": "# R\n\nOne.\n"})
    stage_dir = reader.stage_dir("default", X, "inception", "requirements-analysis", None)
    before = R.evidence_digest(reader.stage_artifacts(repo, stage_dir, None), None)
    (repo / stage_dir / "requirements.md").write_text("# R\n\nTwo requirements.\n")
    assert R.evidence_digest(reader.stage_artifacts(repo, stage_dir, None), None) != before
    snap, _ = reader.read_state(repo, "default", X)
    assert snap.sha256 == GATE_STATE_SHA  # the state file did not move, which is exactly why evidence_digest exists


def test_presence_baseline_golden(reader, repo_builder):
    repo = one_intent(repo_builder, markers=[".aidlc-human-turn"])
    (repo / "aidlc/.aidlc-turn-counter").write_text("27\n")
    audit = reader.read_audit(repo, "default", X)
    snap, _ = reader.read_state(repo, "default", X)
    baseline = reader.presence_baseline(repo, "default", X, audit, snap.sha256)
    assert (baseline.human_turn_events, baseline.turn_counter, baseline.state_sha256) == (1, 27, GATE_STATE_SHA)
    assert baseline.human_turn_events_partial is False and baseline.human_turn_mtime_ns is not None
    shard = f"{REC}/audit/fixture-host-0f1e2d3c4b5a.md"
    assert list(baseline.shard_sizes) == [shard] and baseline.shard_sizes == {shard: len(GATE_AUDIT)}
    assert baseline.audit_tail_sha256 == hashlib.sha256(GATE_AUDIT.encode()).hexdigest()
    assert baseline.taken_at == "2026-09-04T10:00:00Z" and baseline.to_json()["human_turn_events"] == 1


def test_presence_baseline_partial_when_a_shard_was_tail_read(reader, devdelta_repo):
    audit = reader.read_audit(devdelta_repo, "default", F.DEVDELTA_POC, tail_bytes=4096)
    baseline = reader.presence_baseline(devdelta_repo, "default", F.DEVDELTA_POC, audit, None)
    assert audit.complete is False and baseline.human_turn_events_partial is True and baseline.state_sha256 is None
    assert baseline.audit_tail_sha256 != hashlib.sha256(b"").hexdigest()


# --- containment, JSON shape, and the read-only guarantee ---
@pytest.mark.parametrize("space,intent", [("../evil", "260904-x"), ("default", "../evil"), ("", "260904-x")])
def test_illegal_coordinates_are_refused_before_any_read(reader, devdelta_repo, studio, space, intent):
    with pytest.raises(studio.errors.StudioError) as excinfo:
        reader.read_state(devdelta_repo, space, intent)
    assert excinfo.value.code == "bad_path"


def test_every_public_dataclass_serialises(reader, R, gate_repo):
    boundary = reader.stable_boundary_inputs(gate_repo, "default", "260904-gate-demo")
    for payload in [
        boundary.to_json(), boundary.state_snap.to_json(), boundary.state.to_json(), boundary.directive.to_json(),
        boundary.questions.to_json(), boundary.audit.to_json(),
        reader.read_markers(gate_repo, "default", "260904-gate-demo").to_json(),
        reader.presence_baseline(gate_repo, "default", "260904-gate-demo", boundary.audit, None).to_json(),
        reader.read_stage_graph(gate_repo, ".kiro")[0].to_json(), reader.read_scopes(gate_repo, ".kiro")[0].to_json(),
        reader.list_artifacts(gate_repo, "default", "260904-gate-demo")[0].to_json(),
        reader.registry(gate_repo, "default")[0].to_json(), reader.detect_harness_dirs(gate_repo)[0].to_json(),
        R.parse_stage_file("# x\n", "x").to_json(),
    ]:
        assert json.loads(json.dumps(payload)) == payload  # every value is JSON-native
        assert all(key == key.lower() and " " not in key for key in payload)


def test_reader_never_writes(reader, gate_repo):
    before = tree_digest(gate_repo)
    boundary = reader.stable_boundary_inputs(gate_repo, "default", "260904-gate-demo")
    for read in (reader.list_artifacts, reader.read_markers, reader.read_audit, reader.list_units):
        read(gate_repo, "default", "260904-gate-demo")
    for read in (reader.read_scopes, reader.read_scope_grid, reader.read_stage_graph):
        read(gate_repo, ".kiro")
    reader.detect_harness_dirs(gate_repo)
    reader.presence_baseline(gate_repo, "default", "260904-gate-demo", boundary.audit, None)
    reader.stage_artifacts(gate_repo, reader.stage_dir("default", "260904-gate-demo", "inception",
                                                       "requirements-analysis", None), None)
    assert tree_digest(gate_repo) == before


def test_reader_never_reads_the_steering_token_key_or_the_usage_ledger(reader, repo_builder):
    repo = one_intent(repo_builder, artifacts={".aidlc-steering-token-key": "secret-key-material\n",
                                               "usage-ledger.json": '{"schemaVersion": 3}'})
    listed = reader.list_artifacts(repo, "default", X)
    assert not any("steering-token-key" in meta.relpath or "usage-ledger" in meta.relpath for meta in listed)
    assert not any(meta.name in {".aidlc-steering-token-key", "usage-ledger.json"}
                   for meta in reader.stage_artifacts(repo, REC, None))


# --- workspace signals (§1.6.3): what a plan draft learns about the repository ---
@pytest.fixture
def text_reads(R, monkeypatch) -> list[Path]:
    reads: list[Path] = []
    real = R.security.bounded_text

    def counting(path, *args, **kwargs):
        reads.append(Path(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(R.security, "bounded_text", counting)
    return reads


def test_workspace_signals_are_names_and_one_bounded_readme_read(R, reader, studio, tmp_path, text_reads):
    """FR-ADV-003: top-level names (no recursion, dot-entries or symlinks), capped, and one whole README read."""
    K = studio.constants
    repo = tmp_path / "repo"
    for directory in ("src/deep", "docs", ".git"):
        (repo / directory).mkdir(parents=True)
    readme = "# Demo\n" + ("lorem ipsum dolor sit amet " * 200)
    assert len(readme) > K.MAX_README_CHARS
    for rel, text in {".env": "SECRET=1\n", "pyproject.toml": "[project]\nname = 'demo'\n", "package.json": "{}\n",
                      "notes.txt": "hello\n", "src/go.mod": "module deep\n", "src/deep/README.md": "# Not this one\n",
                      "README.md": readme, "README": "# Also not this one: README.md comes first\n"}.items():
        (repo / rel).write_text(text)  # a README or manifest one level down is not a signal
    os.symlink(repo / "src", repo / "linked")
    os.symlink(repo / "README.md", repo / "README.txt")
    signals = reader.read_workspace_signals(repo)
    assert signals.top_level == ("README", "README.md", "docs/", "notes.txt", "package.json", "pyproject.toml", "src/")
    assert signals.manifests == ("package.json", "pyproject.toml")
    # handed over whole: the broker redacts before it cuts, so a cut here could split a credential (§1.18a)
    assert signals.readme_excerpt == readme and len(signals.readme_excerpt) > K.MAX_README_CHARS
    assert signals.truncated is False and [(path.parent, path.name) for path in text_reads] == [(repo, "README.md")]
    assert signals.to_json() == {"top_level": list(signals.top_level), "manifests": list(signals.manifests),
                                 "readme_excerpt": signals.readme_excerpt, "truncated": False}
    assert R.WORKSPACE_MANIFESTS[:2] == ("package.json", "pyproject.toml") and R.README_NAMES[0] == "README.md"
    crowd, exact = tmp_path / "crowd", tmp_path / "exact"
    crowd.mkdir()
    exact.mkdir()
    for n in reversed(range(K.MAX_WORKSPACE_ENTRIES + 5)):  # created in reverse: the order is the reader's doing
        (crowd / f"file-{n:03d}.txt").write_text("")
    for n in range(3):
        (crowd / f".dot-{n}").write_text("")  # never counts toward the cap
    os.symlink(crowd / "file-000.txt", crowd / "aaa-link")  # would sort first; never listed
    many = reader.read_workspace_signals(crowd)
    assert many.top_level == tuple(f"file-{n:03d}.txt" for n in range(K.MAX_WORKSPACE_ENTRIES))
    assert many.truncated is True and many.manifests == () and many.readme_excerpt is None
    for n in range(K.MAX_WORKSPACE_ENTRIES):
        (exact / f"file-{n:03d}.txt").write_text("")
    full = reader.read_workspace_signals(exact)
    assert len(full.top_level) == K.MAX_WORKSPACE_ENTRIES and full.truncated is False  # the cap is not "over it"


def test_a_symlinked_readme_or_manifest_is_skipped_like_the_listing_skips_it(reader, tmp_path, text_reads):
    """A link is never read, even when its target sits inside the repository (§1.6.3, FR-ADV-003)."""
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "README.real.md").write_text("# The real README\n")
    (repo / "docs" / "package.real.json").write_text("{}\n")
    os.symlink(repo / "docs" / "README.real.md", repo / "README.md")
    os.symlink(repo / "docs" / "package.real.json", repo / "package.json")
    (repo / "Makefile").write_text("all:\n")
    signals = reader.read_workspace_signals(repo)
    assert (signals.top_level, signals.manifests) == (("Makefile", "docs/"), ("Makefile",))
    assert signals.readme_excerpt is None
    assert text_reads == []
    (repo / "README").write_text("# Plain README\n")  # beside the linked one: the next real name is read
    assert reader.read_workspace_signals(repo).readme_excerpt == "# Plain README\n"
    assert [path.name for path in text_reads] == ["README"]
