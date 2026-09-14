"""Proof that the parsers read the real AI-DLC formats, not an idealised version of them.

Every input here is either a byte-faithful harvested file (``tests/fixtures/aidlc-*``, see
``FORMAT-NOTES.md``) or a variant derived from one with the ``fixtures`` helpers, so a test failing
means the product would misread a real repository. The token formulas are pinned to golden literals:
they are part of the compare-and-submit contract, so changing one has to be a visible diff here.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

import fixtures as F

APP_ROOT = Path(__file__).resolve().parent.parent
PAYLOAD_ENGINE = APP_ROOT / "payload" / "aidlc-kiro" / ".kiro"

STATE_FIXTURES = [
    (F.DEVDELTA, F.DEVDELTA_POC, 8, 33),
    (F.DEVDELTA, F.DEVDELTA_REMEDIATION, 8, 33),
    (F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN, 7, 32),
    (F.PAYLOAD_230, F.PAYLOAD_COMPLETED, 7, 32),
    (F.OLDER, F.OLDER_INTENT, 7, 32),
]

QUESTION_FIXTURES = [
    (F.DEVDELTA, F.DEVDELTA_POC, "ideation/intent-capture"),
    (F.DEVDELTA, F.DEVDELTA_POC, "inception/requirements-analysis"),
    (F.DEVDELTA, F.DEVDELTA_POC, "construction/kiro-impact-poc/code-generation"),
    (F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN, "ideation/intent-capture"),
    (F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN, "inception/requirements-analysis"),
    (F.PAYLOAD_230, F.PAYLOAD_COMPLETED, "construction/health-probe/code-generation"),
    (F.OLDER, F.OLDER_INTENT, "ideation/feasibility"),
    (F.OLDER, F.OLDER_INTENT, "ideation/intent-capture"),
    (F.OLDER, F.OLDER_INTENT, "ideation/market-research"),
]

#: The canonical gate scenario: one row flipped to ``[?]`` on the real 33-stage v8 file. Pinned so the
#: golden tokens below cannot drift without this digest changing too.
GATE_STATE = F.state_text(
    marks={"requirements-analysis": "?"},
    fields={
        "Current Stage": "requirements-analysis",
        "Status": "Running",
        "Next Stage": "user-stories",
    },
)
GATE_STATE_SHA = "9666ad4b99177fa026c52690a89657031b175b7016345240ab455f1a7f20196d"
GATE_AUDIT = F.gate_open_audit("requirements-analysis")


# --------------------------------------------------------------------------- #
# fixtures local to this module
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def R(studio):
    module = getattr(studio, "aidlc_reader", None)
    assert module is not None, getattr(studio, "aidlc_reader__error", "aidlc_reader failed to import")
    return module


@pytest.fixture
def reader(R, clock):
    return R.AidlcReader(clock)


@pytest.fixture(scope="module")
def devdelta_repo(tmp_path_factory) -> Path:
    """A whole real 2.6.2 install as a repository root, copied once for the whole module.

    Module-scoped because every reader call is read-only; ``test_reader_never_writes`` proves it.
    """
    root = tmp_path_factory.mktemp("devdelta") / "repo"
    shutil.copytree(F.DEVDELTA, root)
    return root


@pytest.fixture(scope="module")
def older_repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("older") / "repo"
    shutil.copytree(F.OLDER, root)
    return root


def RepoBuilderFor(root: Path):
    """A tiny factory so one test can build two engine trees without two fixtures."""
    from conftest import RepoBuilder

    def build(source: str) -> Path:
        return RepoBuilder(root / source).with_engine(source).build()

    return build


class TickingClock:
    """A clock whose ``monotonic`` jumps a second per call — forces every deadline in one pass."""

    def __init__(self, step: float = 1.0) -> None:
        self._mono = 0.0
        self._step = step

    def now(self) -> float:
        return 1788500000.0

    def iso(self) -> str:
        return "2026-09-04T10:00:00Z"

    def monotonic(self) -> float:
        self._mono += self._step
        return self._mono


def tree_digest(root: Path) -> list[tuple[str, int, str]]:
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            data = path.read_bytes()
            rows.append((path.relative_to(root).as_posix(), len(data), hashlib.sha256(data).hexdigest()))
    return rows


def force_unstable(monkeypatch: pytest.MonkeyPatch, target: Path) -> None:
    """Make ``target`` look as if it changed between the two stats of every ``stable_read``.

    ``os.stat`` is the only seam that can express "the file moved while we read it"; patching the
    stdlib (never the module under test) keeps the parser honest while making the race deterministic.
    """
    real = os.stat
    bumped = {"n": 0}

    def fake(path, *args, **kwargs):
        result = real(path, *args, **kwargs)
        try:
            same = Path(path) == target
        except TypeError:
            same = False
        if not same:
            return result
        bumped["n"] += 1
        if bumped["n"] % 2 == 0:  # the post-read stat of each attempt
            return os.stat_result(
                tuple(result)[:8] + (result.st_mtime + 1,) + tuple(result)[9:]
            )
        return result

    monkeypatch.setattr(os, "stat", fake)


# --------------------------------------------------------------------------- #
# aidlc-state.md
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tree,intent,version,stage_count", STATE_FIXTURES)
def test_state_file_parses_every_real_fixture(R, tree, intent, version, stage_count):
    state = R.parse_state_file(F.real_state(tree, intent))
    assert state.state_version == version
    assert version in R.C.SUPPORTED_STATE_VERSIONS
    assert state.counts["total"] == stage_count
    assert not state.header_only
    # every section of the template is present and keyed by its heading
    assert set(state.sections) >= {
        "Project Information",
        "Scope Configuration",
        "Workspace State",
        "Execution Plan Summary",
        "Runtime State",
        "Phase Progress",
        "Stage Progress",
        "Current Status",
        "Session Resume Point",
    }
    # the parse agrees with an independent regex sweep of the same bytes
    assert {row.slug: row.mark for row in state.stages} == F.stage_marks(F.real_state(tree, intent))
    assert [name for name, _ in state.phases] == [
        "Initialization",
        "Ideation",
        "Inception",
        "Construction",
        "Operation",
    ]
    assert state.project["State Version"] == str(version)
    assert state.counts["done"] == state.counts["completed"] + state.counts["skipped"]


def test_state_supported_versions_and_stage_counts_match_constants(studio):
    C = studio.constants
    assert set(version for _, _, version, _ in STATE_FIXTURES) == set(C.SUPPORTED_STATE_VERSIONS)
    assert set(count for _, _, _, count in STATE_FIXTURES) == set(C.SUPPORTED_STAGE_COUNTS)


def test_state_empty_value_with_trailing_space_is_empty_string(R):
    """``- **Review Override**: `` (trailing space, no value) must parse as present-and-empty."""
    text = F.real_state()
    assert "- **Review Override**: \n" in text
    state = R.parse_state_file(text)
    assert state.scope_config["Review Override"] == ""
    assert "Review Override" in state.sections["Scope Configuration"]


def test_state_all_six_checkbox_marks(R):
    text = F.state_text(
        marks={
            "workspace-scaffold": "x",
            "workspace-detection": "S",
            "state-init": "R",
            "intent-capture": "?",
            "market-research": "-",
            "feasibility": " ",
        }
    )
    state = R.parse_state_file(text)
    by_slug = {row.slug: row for row in state.stages}
    assert by_slug["workspace-scaffold"].state == "completed"
    assert by_slug["workspace-detection"].state == "skipped"
    assert by_slug["state-init"].state == "revising"
    assert by_slug["intent-capture"].state == "awaiting_approval"
    assert by_slug["market-research"].state == "in_progress"
    assert by_slug["feasibility"].state == "not_started"
    assert state.counts["awaiting_approval"] == 1
    assert state.counts["revising"] == 1
    assert state.counts["unknown"] == 0


def test_state_en_dash_double_dash_and_missing_separator(R):
    """Older writers used an en dash or ``--``; a bare row with no suffix still parses."""
    text = F.real_state()
    variant = text.replace("- [x] workspace-scaffold — EXECUTE", "- [x] workspace-scaffold – EXECUTE")
    variant = variant.replace("- [x] workspace-detection — EXECUTE", "- [x] workspace-detection -- EXECUTE")
    variant = variant.replace("- [x] state-init — EXECUTE", "- [x] state-init")
    state = R.parse_state_file(variant)
    by_slug = {row.slug: row for row in state.stages}
    assert state.counts["total"] == 33
    assert by_slug["workspace-scaffold"].suffix == "EXECUTE"
    assert by_slug["workspace-detection"].suffix == "EXECUTE"
    assert by_slug["state-init"].suffix is None
    assert by_slug["state-init"].state == "completed"


def test_state_phase_subheaders_and_per_unit_tbd_line(R):
    """``### CONSTRUCTION PHASE`` labels its rows and the literal ``Per unit: [TBD]`` is not a row."""
    text = F.real_state()
    assert "Per unit: [TBD]" in text
    state = R.parse_state_file(text)
    phases = {row.phase for row in state.stages}
    assert phases == {"initialization", "ideation", "inception", "construction", "operation"}
    assert state.row("code-generation").phase == "construction"
    assert all(row.slug != "Per" for row in state.stages)
    assert [row.slug for row in state.stages][:3] == [
        "workspace-scaffold",
        "workspace-detection",
        "state-init",
    ]


def test_state_skeleton_stance_without_blank_line_before_next_section(R):
    """The real poc file inserts ``Skeleton Stance`` right before ``## Phase Progress``."""
    text = F.real_state()
    assert "- **Skeleton Stance**: scope-dependent\n## Phase Progress\n" in text
    state = R.parse_state_file(text)
    assert state.runtime == {"Revision Count": "0", "Skeleton Stance": "scope-dependent"}
    assert state.phases[0] == ("Initialization", "Verified")


def test_state_header_only_stub(R):
    """``createIntent`` writes the one-line stub before the body — birth in progress, not an empty run."""
    state = R.parse_state_file("# AI-DLC State Tracking\n")
    assert state.header_only is True
    assert state.counts["total"] == 0
    assert state.state_version is None
    assert state.current_stage is None
    assert state.sections == {}


def test_state_version_9_is_reported_not_crashed(R, studio):
    state = R.parse_state_file(F.state_text(fields={"State Version": "9"}))
    assert state.state_version == 9
    assert 9 not in studio.constants.SUPPORTED_STATE_VERSIONS
    assert state.counts["total"] == 33  # the rest of the file is still readable


def test_state_non_integer_version_is_none(R):
    state = R.parse_state_file(F.state_text(fields={"State Version": "8-beta"}))
    assert state.state_version is None


def test_state_contradictory_fixture_is_reported_verbatim(R):
    """A file that disagrees with itself parses; deciding what is true is ``consistency.py``'s job."""
    text = F.state_text(
        marks={"requirements-analysis": "?", "user-stories": "R", "market-research": "S"},
        fields={"Completed": "20", "Lifecycle Phase": "OPERATION", "Initialization": "Active"},
    )
    state = R.parse_state_file(text)
    assert state.completed_claimed == 20
    assert state.counts["completed"] == 7  # the file's own rows say otherwise
    assert state.counts["awaiting_approval"] == 1
    assert state.counts["revising"] == 1
    assert state.counts["skipped"] == 1
    assert state.lifecycle_phase == "OPERATION"
    assert dict(state.phases)["Initialization"] == "Active"


def test_state_none_sentinel_is_absent_not_a_stage(R):
    """``Next Stage: none`` must not become a stage slug a consumer looks for on disk."""
    state = R.parse_state_file(F.real_state())
    assert state.current_stage == "build-and-test"
    assert state.next_stage is None
    assert state.status == "Completed"
    assert state.current["Next Stage"] == "none"  # the raw value stays available


def test_state_fields_are_matched_by_label_across_sections(R):
    """The engine's ``getField`` is label-scoped, so ``Status`` must not collide with phase rows."""
    text = F.real_state(F.OLDER, F.OLDER_INTENT)
    state = R.parse_state_file(text)
    assert state.status == "Running"
    assert dict(state.phases)["Initialization"] == "Active"
    assert state.lifecycle_phase == "IDEATION"
    assert state.revision_count == 0
    assert state.total_stages_claimed == 31
    assert F.state_fields(text)["Total Stages"] == "31"


def test_state_parked_and_unit_rows(R):
    text = F.insert_runtime_field(F.real_state(), "Parked", "2026-09-03T10:00:00Z")
    text = F.insert_runtime_field(text, "Parked At Stage", "code-generation")
    text = F.insert_runtime_field(text, "Active Unit", "kiro-impact-poc")
    text = F.insert_runtime_field(text, "Unit State", "paused")
    state = R.parse_state_file(text)
    assert state.parked_at == "2026-09-03T10:00:00Z"
    assert state.parked_at_stage == "code-generation"
    assert state.active_unit == "kiro-impact-poc"
    assert state.unit_state == "paused"


def test_state_unknown_section_is_preserved(R):
    text = F.real_state() + "\n## Future Section\n- **Novel Field**: 42\n"
    state = R.parse_state_file(text)
    assert state.sections["Future Section"] == {"Novel Field": "42"}


def test_state_crlf_and_empty_input(R):
    state = R.parse_state_file(F.real_state().replace("\n", "\r\n"))
    assert state.counts["total"] == 33
    assert state.state_version == 8
    blank = R.parse_state_file("")
    assert blank.header_only is True


# --------------------------------------------------------------------------- #
# intents.json, cursors, directive
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tree", [F.DEVDELTA, F.PAYLOAD_230, F.OLDER])
def test_parse_intents_json_all_three_versions(R, tree):
    raw = json.dumps(F.intents_registry(tree)).encode()
    rows = R.parse_intents_json(raw)
    assert rows and len(rows) == len(F.intents_registry(tree))
    for row, source in zip(rows, F.intents_registry(tree)):
        assert row.uuid == source["uuid"]
        assert row.slug == source["slug"]
        assert row.dir_name == source["dirName"]
        assert row.scope == source["scope"]
        assert row.status == source["status"]
        assert row.repos == ()
        assert R.match_intent_row(rows, source["dirName"]) is row


def test_parse_intents_json_malformed_and_legacy_rows(R):
    assert R.parse_intents_json(b"") == []
    assert R.parse_intents_json(b"not json") == []
    assert R.parse_intents_json(b'{"uuid": "x"}') == []
    rows = R.parse_intents_json(
        json.dumps(
            [
                "junk",
                {"uuid": "019f3d4f-1da9-7b07-892f-09047f0c3bb8", "slug": "trello-plugin"},
                {"uuid": "u2", "slug": "s2", "dirName": "d2", "repos": ["a", "b"], "status": "complete"},
            ]
        ).encode()
    )
    assert [row.slug for row in rows] == ["trello-plugin", "s2"]
    assert rows[0].dir_name is None
    assert rows[0].status == "unknown"  # absent status is not invented
    assert rows[1].repos == ("a", "b")


def test_match_intent_row_legacy_slug_hex_rule(R):
    rows = R.parse_intents_json(
        json.dumps(
            [{"uuid": "019f3d4f-1da9-7b07-892f-09047f0c3bb8", "slug": "trello-plugin", "status": "in-flight"}]
        ).encode()
    )
    assert R.match_intent_row(rows, "trello-plugin-09047f0c3bb8") is rows[0]
    assert R.match_intent_row(rows, "trello-plugin-deadbeef") is None   # hex, but not this uuid
    assert R.match_intent_row(rows, "trello-plugin-notes") is None      # not hex
    assert R.match_intent_row(rows, "260707-trello-plugin") is None     # date prefix is not the rule


def test_parse_cursor(R):
    assert R.parse_cursor(b"260828-kiro-impact-poc\n", default=None) == "260828-kiro-impact-poc"
    assert R.parse_cursor(b"  spaced  \nsecond\n", default=None) == "spaced"
    assert R.parse_cursor(b"", default="default") == "default"
    assert R.parse_cursor(b"\n", default="default") == "default"
    assert R.parse_cursor(None, default="default") == "default"
    assert R.parse_cursor(None, default=None) is None


def test_active_space_defaults_when_cursor_absent(reader, older_repo, devdelta_repo):
    """The real 2.1.1 install has no ``aidlc/active-space`` at all."""
    assert not (older_repo / "aidlc" / "active-space").exists()
    assert reader.active_space(older_repo) == "default"
    assert reader.active_space(devdelta_repo) == "default"


def test_active_intent_dangling_when_cursor_names_a_dir_without_state(reader, repo_builder):
    repo = (
        repo_builder.with_workspace()
        .with_intent("260904-real", state=GATE_STATE, audit=GATE_AUDIT)
        .build()
    )
    intents = repo / "aidlc" / "spaces" / "default" / "intents"
    assert reader.active_intent(repo, "default") == ("260904-real", False)

    (intents / "260904-ghost").mkdir()                 # a record dir with no aidlc-state.md
    (intents / "active-intent").write_text("260904-ghost\n")
    assert reader.active_intent(repo, "default") == ("260904-ghost", True)
    assert reader.list_intent_dirs(repo, "default") == ["260904-real"]

    (intents / "active-intent").write_text("260904-missing\n")
    assert reader.active_intent(repo, "default") == ("260904-missing", True)

    (intents / "active-intent").write_text("../../escape\n")
    assert reader.active_intent(repo, "default") == (None, True)

    (intents / "active-intent").unlink()
    assert reader.active_intent(repo, "default") == (None, False)


def test_layout_spaces_legacy_and_none(reader, devdelta_repo, repo_builder, tmp_path):
    assert reader.layout(devdelta_repo) == "spaces"
    assert reader.layout(repo_builder.with_legacy_layout().build()) == "legacy"
    bare = tmp_path / "bare"
    bare.mkdir()
    assert reader.layout(bare) is None


def test_list_spaces_requires_intents_or_memory(reader, repo_builder):
    repo = repo_builder.with_workspace().build()
    (repo / "aidlc" / "spaces" / "empty").mkdir()
    (repo / "aidlc" / "spaces" / "second" / "memory").mkdir(parents=True)
    assert reader.list_spaces(repo) == ["default", "second"]


def test_directive_fresh_and_stale(reader, devdelta_repo, R):
    """Both real markers: the remediation digest matches, the poc one is genuinely stale."""
    snap, _ = reader.read_state(devdelta_repo, "default", F.DEVDELTA_REMEDIATION)
    fresh = reader.read_directive(devdelta_repo, "default", F.DEVDELTA_REMEDIATION, snap.sha256)
    assert fresh.version == 1 and fresh.stage == "reverse-engineering"
    assert fresh.state_sha256 == snap.sha256 and fresh.matches_state is True
    assert fresh.unit is None

    snap, _ = reader.read_state(devdelta_repo, "default", F.DEVDELTA_POC)
    stale = reader.read_directive(devdelta_repo, "default", F.DEVDELTA_POC, snap.sha256)
    assert stale.stage == "build-and-test"
    assert stale.matches_state is False       # stale, not corrupt (C06)
    assert stale.state_sha256 != snap.sha256


def test_parse_directive_rejects_non_markers(R):
    digest = "a" * 64
    assert R.parse_directive(b"", digest) is None
    assert R.parse_directive(b"[]", digest) is None
    assert R.parse_directive(json.dumps({"stage": "x", "state_sha256": digest}).encode(), digest) is None
    assert R.parse_directive(json.dumps({"version": 0, "stage": "x", "state_sha256": digest}).encode(), digest) is None
    assert R.parse_directive(json.dumps({"version": 3, "stage": "x", "state_sha256": digest}).encode(), digest) is None
    assert R.parse_directive(json.dumps({"version": 1, "stage": "Bad Stage", "state_sha256": digest}).encode(), digest) is None
    assert R.parse_directive(json.dumps({"version": 1, "stage": "x", "state_sha256": "short"}).encode(), digest) is None
    marker = R.parse_directive(
        json.dumps({"version": 1, "stage": "code-generation", "unit": "u1", "state_sha256": digest}).encode(),
        digest,
    )
    assert (marker.stage, marker.unit, marker.matches_state) == ("code-generation", "u1", True)
    assert marker.version == 1 and marker.kind is None and marker.units == ()


def test_parse_directive_rejects_a_version_2_marker_with_no_usable_kind(R):
    """The marker is the execution cursor, so an unrecognised ``kind`` must not name a running stage.

    2.7.1 emits exactly three kinds. A fourth one would be a shape this Studio has never seen; believing
    it would point the cards at work nobody started, so it is treated as "no marker" and the reader falls
    back to ``Current Stage`` the way the engine does.
    """
    digest = "b" * 64
    def marker(**over):
        payload = {"version": 2, "kind": "run-stage", "stage": "code-generation", "state_sha256": digest}
        payload.update(over)
        return R.parse_directive(json.dumps(payload).encode(), digest)

    assert marker() is not None
    assert marker(kind="compose-plan") is None
    assert marker(kind=None) is None
    assert R.parse_directive(
        json.dumps({"version": 2, "stage": "code-generation", "state_sha256": digest}).encode(), digest
    ) is None


def test_parse_directive_reads_both_marker_schema_versions_the_engine_leaves_on_disk(R):
    """2.7.1 writes ``version: 2`` but preserves an existing v1 marker, so both shapes stay live.

    The parsed version is carried through rather than restamped: telling the wire that a v2 marker is v1
    would make every consumer downstream reason about a schema that is not on disk. ``kind`` distinguishes
    the swarm's unitless code-generation marker from an ordinary ``run-stage``, and ``units`` carries its
    fan-out, which has no v1 spelling at all.
    """
    digest = "c" * 64
    run_stage = R.parse_directive(
        json.dumps(
            {"version": 2, "kind": "run-stage", "stage": "requirements-analysis", "state_sha256": digest}
        ).encode(),
        digest,
    )
    assert run_stage is not None
    assert (run_stage.version, run_stage.kind, run_stage.stage) == (2, "run-stage", "requirements-analysis")
    assert run_stage.unit is None and run_stage.units == () and run_stage.matches_state is True
    assert run_stage.to_json()["version"] == 2 and run_stage.to_json()["kind"] == "run-stage"

    swarm = R.parse_directive(
        json.dumps(
            {"version": 2, "kind": "invoke-swarm", "stage": "code-generation",
             "units": ["authentication", "billing"], "state_sha256": digest}
        ).encode(),
        digest,
    )
    assert swarm is not None
    assert swarm.kind == "invoke-swarm"
    assert swarm.units == ("authentication", "billing")
    assert swarm.unit is None                      # the swarm marker names units, never one unit
    assert swarm.to_json()["units"] == ["authentication", "billing"]

    steering = R.parse_directive(
        json.dumps(
            {"version": 2, "kind": "load-steering", "stage": "state-init", "state_sha256": digest}
        ).encode(),
        digest,
    )
    assert steering is not None and steering.kind == "load-steering"


def test_parse_directive_tolerates_every_extra_key_a_real_2_7_1_marker_carries(R):
    """A verbatim v2 marker from a 2.7.1 install: eighteen keys the reader has no opinion about.

    Copied from a real ``lab1-express`` record. The reader must read the five fields it needs and ignore
    the rest, or the next field the engine adds would blank the execution cursor again.
    """
    digest = "462987b2137d8a16bef84283879cbb7533fd95d8a8c781964f4544f86a538eb4"
    payload = {
        "version": 2,
        "revision": 4,
        "project_sha256": "5e9a3b040459cf0b16823cd8f4f5185ed7f7bb26213f598781f85d5feae55f85",
        "intent_uuid": "01a071ee-613d-762c-bc22-98ad68d14974",
        "state_present": True,
        "state_sha256": digest,
        "cursor_harness": "claude",
        "owner_session": "sessionless:5e9a3b040459cf0b",
        "owner_epoch": 0,
        "context_epoch": 0,
        "kind": "run-stage",
        "stage": "code-generation",
        "delivery": "issued",
        "needs_rehydrate": False,
        "active_attempt": {"id": "sessionless", "command_kind": "next", "status": "settled"},
        "event_sequence": 0,
        "stop_count": 0,
        "code_generation_authority_revision": 4,
    }
    marker = R.parse_directive(json.dumps(payload).encode(), digest)
    assert marker is not None
    assert (marker.version, marker.kind, marker.stage) == (2, "run-stage", "code-generation")
    assert marker.matches_state is True and marker.unit is None


def test_directive_absent_for_engines_that_never_wrote_it(reader, older_repo):
    snap, _ = reader.read_state(older_repo, "default", F.OLDER_INTENT)
    assert reader.read_directive(older_repo, "default", F.OLDER_INTENT, snap.sha256) is None


# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #


def test_audit_shard_split_on_the_fence(R):
    """The header line, the ``---`` fence and a field-less ``HUMAN_TURN`` block all behave."""
    text = F.real_audit(F.DEVDELTA, F.DEVDELTA_REMEDIATION)
    small = (F.DEVDELTA_INTENTS / F.DEVDELTA_REMEDIATION / "audit" / "603e5f4a8072-747664ca2c41.md").read_text()
    events = R.parse_audit_shard(small, "603e5f4a8072-747664ca2c41.md", 1)
    assert [event.event for event in events] == ["SESSION_STARTED", "HUMAN_TURN", "SESSION_ENDED"]
    assert events[0].fields == {"Source": "startup"}
    assert events[0].timestamp == "2026-08-31T02:35:05Z"
    assert events[0].raw.startswith("## Session Start")   # the file header is not part of the block
    assert events[1].fields == {}                          # HUMAN_TURN carries no fields at all
    assert [event.pos for event in events] == [0, 1, 2]
    assert all(event.shard_index == 1 for event in events)
    assert len(R.parse_audit_shard(text, "s", 0)) == text.count("\n---\n")


def test_audit_field_lines_with_and_without_leading_dash(R):
    text = (
        "# AI-DLC Audit Log\n"
        "\n## Gate Approved\n**Timestamp**: 2026-09-04T09:00:11Z\n**Event**: GATE_APPROVED\n"
        "- **Stage**: requirements-analysis\n**User Input**: Approve\n\n---\n"
    )
    event = R.parse_audit_shard(text, "s", 0)[0]
    assert event.fields == {"Stage": "requirements-analysis", "User Input": "Approve"}


def test_audit_keeps_escaped_newlines_and_empty_values(R):
    block = F.audit_block(
        "SUBAGENT_COMPLETED",
        "2026-09-04T09:00:00Z",
        Agent_Type="",
        Message="line one\nline two",
    )
    event = R.parse_audit_shard(block, "s", 0)[0]
    assert event.fields["Message"] == "line one\\nline two"   # literal, exactly as written
    assert event.fields["Agent Type"] == ""
    assert "\n" not in event.fields["Message"]


def test_audit_crlf_and_blocks_without_event_are_dropped(R):
    text = F.audit_text(
        F.audit_block("HUMAN_TURN", "2026-09-04T09:00:00Z"),
        "\n## Hand edited\n**Timestamp**: 2026-09-04T09:00:01Z\n**Note**: no event key\n\n---\n",
        F.audit_block("STAGE_STARTED", "2026-09-04T09:00:02Z", Stage="user-stories"),
    ).replace("\n", "\r\n")
    events = R.parse_audit_shard(text, "s", 0)
    assert [event.event for event in events] == ["HUMAN_TURN", "STAGE_STARTED"]
    assert events[1].pos == 2   # the dropped block still occupies its position


def test_audit_unknown_event_names_are_kept(R):
    """``LOOP_RUN_STARTED`` is in no engine taxonomy and is on a real machine."""
    shard = F.real_audit(F.OLDER, F.OLDER_INTENT)
    events = R.parse_audit_shard(shard, "shard.md", 0)
    loop = [event for event in events if event.event == "LOOP_RUN_STARTED"]
    assert len(loop) == 4
    assert loop[0].fields["Mode"] == "attached"
    assert "RULE_LEARNED" in {event.event for event in events}
    assert R.parse_audit_shard(F.audit_block("BRAND_NEW_EVENT", "2026-09-04T09:00:00Z"), "s", 0)[0].event == (
        "BRAND_NEW_EVENT"
    )


def test_merge_audit_orders_by_timestamp_then_shard_then_position(R):
    first = R.parse_audit_shard(
        F.audit_text(
            F.audit_block("HUMAN_TURN", "2026-09-04T09:00:00Z"),
            F.audit_block("GATE_APPROVED", "2026-09-04T09:00:05Z", Stage="a"),
        ),
        "shard-a.md",
        0,
    )
    second = R.parse_audit_shard(
        F.audit_text(
            F.audit_block("SESSION_STARTED", "2026-09-04T09:00:05Z", Source="startup"),
            F.audit_block("HUMAN_TURN", "2026-09-04T09:00:01Z"),
        ),
        "shard-b.md",
        1,
    )
    merged = R.merge_audit([first, second])
    assert [(event.timestamp, event.shard_index, event.pos) for event in merged] == [
        ("2026-09-04T09:00:00Z", 0, 0),
        ("2026-09-04T09:00:01Z", 1, 1),
        ("2026-09-04T09:00:05Z", 0, 1),   # same second: lower shard_index first
        ("2026-09-04T09:00:05Z", 1, 0),
    ]


def test_latest_event_filters_by_type_and_stage(R, studio):
    events = R.merge_audit(
        [R.parse_audit_shard(F.gate_approved_audit("requirements-analysis"), "s.md", 0)]
    )
    latest = R.latest_event(events, studio.constants.GATE_EVENTS)
    assert latest.event == "GATE_APPROVED"
    assert R.latest_event(events, studio.constants.GATE_EVENTS, "user-stories") is None
    assert R.latest_event(events, ("STAGE_AWAITING_APPROVAL",), "requirements-analysis").timestamp == (
        "2026-09-04T09:00:03Z"
    )
    assert R.latest_event([], studio.constants.GATE_EVENTS) is None
    # unsorted input still yields the newest row, not the last one
    assert R.latest_event(list(reversed(events)), ("HUMAN_TURN",)).timestamp == "2026-09-04T09:00:10Z"


def test_human_acted_since_resolution_rules(R):
    def parse(text, shard="s.md", index=0):
        return R.parse_audit_shard(text, shard, index)

    assert R.human_acted_since_resolution([]) is None            # no audit ≠ no human
    only_engine = parse(F.audit_block("STAGE_STARTED", "2026-09-04T09:00:00Z", Stage="a"))
    assert R.human_acted_since_resolution(only_engine) is False
    no_resolution = parse(F.gate_open_audit("requirements-analysis"))
    assert R.human_acted_since_resolution(no_resolution) is True
    resolved = parse(F.gate_approved_audit("requirements-analysis"))
    assert R.human_acted_since_resolution(resolved) is False     # engine acted last
    after = parse(
        F.audit_text(
            F.gate_approved_audit("requirements-analysis"),
            F.audit_block("HUMAN_TURN", "2026-09-04T09:01:00Z"),
        )
    )
    assert R.human_acted_since_resolution(after) is True
    # same second, same shard, later position → present; different shard → fails closed
    same_shard = parse(
        F.audit_text(
            F.audit_block("GATE_APPROVED", "2026-09-04T09:00:11Z", Stage="a"),
            F.audit_block("HUMAN_TURN", "2026-09-04T09:00:11Z"),
        )
    )
    assert R.human_acted_since_resolution(same_shard) is True
    cross = R.merge_audit(
        [
            parse(F.audit_block("GATE_APPROVED", "2026-09-04T09:00:11Z", Stage="a")),
            parse(F.audit_block("HUMAN_TURN", "2026-09-04T09:00:11Z"), "other.md", 1),
        ]
    )
    assert R.human_acted_since_resolution(cross) is False


def test_read_audit_merges_both_shards_of_a_real_record(reader, devdelta_repo):
    bundle = reader.read_audit(devdelta_repo, "default", F.DEVDELTA_REMEDIATION)
    assert len(bundle.shards) == 2
    assert bundle.complete is True
    assert [shard.relpath.rsplit("/", 1)[-1] for shard in bundle.shards] == [
        "603e5f4a8072-26e77d17b1cd.md",
        "603e5f4a8072-747664ca2c41.md",
    ]
    assert all(shard.size > 0 and shard.mtime_ns > 0 for shard in bundle.shards)
    timestamps = [event.timestamp for event in bundle.events]
    assert timestamps == sorted(timestamps)
    assert bundle.events[0].shard_index == 0


def test_read_audit_tail_read_marks_the_bundle_incomplete(reader, devdelta_repo):
    """A shard over the cap is tail-read with its partial first line dropped (evidence says so)."""
    whole = reader.read_audit(devdelta_repo, "default", F.DEVDELTA_REMEDIATION)
    tail = reader.read_audit(devdelta_repo, "default", F.DEVDELTA_REMEDIATION, tail_bytes=4096)
    assert tail.complete is False
    assert [shard.truncated for shard in tail.shards] == [True, False]
    assert 0 < len(tail.events) < len(whole.events)
    assert all(event.event for event in tail.events)   # no half-parsed block survived


def test_read_audit_of_a_record_without_audit_dir(reader, repo_builder):
    repo = repo_builder.with_workspace().with_intent("260904-x", state=GATE_STATE, audit="").build()
    shutil.rmtree(repo / "aidlc/spaces/default/intents/260904-x/audit")
    bundle = reader.read_audit(repo, "default", "260904-x")
    assert (bundle.events, bundle.shards, bundle.complete) == ([], [], True)


# --------------------------------------------------------------------------- #
# questions
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tree,intent,stage_rel", QUESTION_FIXTURES)
def test_questions_file_parses_every_real_fixture(R, tree, intent, stage_rel):
    text = F.real_questions(tree, intent, stage_rel)
    parsed = R.parse_questions_file(text, f"{stage_rel}/x-questions.md", "0" * 64)
    assert len(parsed.questions) == text.count("\n## Q")
    for question in parsed.questions:
        assert question.index >= 1
        assert question.prompt
        if question.options:
            # every real question ends with the X escape hatch
            assert question.options[-1].letter == "X"
            assert question.options[-1].is_other is True
            assert all(not option.is_other for option in question.options[:-1])
        assert question.answered == (question.answer is not None)
    assert parsed.pending_count == sum(1 for question in parsed.questions if not question.answered)


def test_questions_dash_and_bare_option_forms(R):
    """``- A. …`` (devlake, requirements-analysis) and ``A. …`` (intent-capture) both occur."""
    dashed = R.parse_questions_file(
        F.real_questions(F.OLDER, F.OLDER_INTENT, "ideation/feasibility"), "r", "0" * 64
    )
    bare = R.parse_questions_file(
        F.real_questions(F.DEVDELTA, F.DEVDELTA_POC, "ideation/intent-capture"), "r", "0" * 64
    )
    assert [option.letter for option in dashed.questions[0].options] == ["A", "B", "C", "X"]
    assert [option.letter for option in bare.questions[0].options] == ["A", "B", "C", "D", "E", "X"]
    assert dashed.questions[0].options[-1].text.startswith("Other")
    assert bare.questions[0].options[-1].text == "Other (please specify)"
    assert bare.questions[3].options[-1].text == "Other (please name the roles and cadence)"


def test_questions_blank_tags_are_pending(R):
    """devlake feasibility: six blank ``[Answer]:`` tags, no checkpoint — the pure question card."""
    parsed = R.parse_questions_file(
        F.real_questions(F.OLDER, F.OLDER_INTENT, "ideation/feasibility"), "r", "0" * 64
    )
    assert parsed.pending_count == 6
    assert parsed.pending_checkpoint is None
    assert all(question.answer is None and not question.answered for question in parsed.questions)
    assert parsed.summary_confirmation is None and parsed.plan_approval is None


def test_questions_underscore_placeholder_counts_as_blank(R):
    text = F.questions_text(questions=[("Pick one", ["a", "b"])], answers={1: "____"})
    parsed = R.parse_questions_file(text, "r", "0" * 64)
    assert parsed.questions[0].answered is False
    assert parsed.pending_count == 1


def test_questions_answers_letters_prose_and_cjk(R):
    parsed = R.parse_questions_file(
        F.real_questions(F.DEVDELTA, F.DEVDELTA_POC, "ideation/intent-capture"), "r", "0" * 64
    )
    answers = {question.index: question.answer for question in parsed.questions}
    assert answers[2].startswith("A, B. 主要使用者是个人开发者")
    assert answers[3] == "A, B, C, D."
    assert "端到端闭环" in answers[1]
    assert parsed.pending_count == 0


def test_questions_multi_line_answer_keeps_its_continuation_bullets(R):
    """A real answer continues on ``- `` lines; truncating at the tag line would lose four buckets."""
    parsed = R.parse_questions_file(
        F.real_questions(F.DEVDELTA, F.DEVDELTA_POC, "inception/requirements-analysis"), "r", "0" * 64
    )
    answer = parsed.questions[0].answer
    assert answer.startswith("X. 采用四个互斥分桶")
    assert answer.count("\n") == 4
    assert "- `verified`" in answer
    assert "- `unknown`" in answer
    assert "official credits 到达后" not in answer   # continuation stops at the first non-bullet line


def test_questions_other_answer_keeps_plain_text_continuation(R):
    parsed = R.parse_questions_file(
        '## Q1: Note?\nA. No note\nX. Other\n[Answer]: X. Note with commas, "quotes", 中文.\n'
        'Second line stays with Q1.\n\n---\n## Q2: Next?\nA. Yes\n[Answer]:\n', "r", "sha"
    )
    assert parsed.questions[0].answer == 'X. Note with commas, "quotes", 中文.\nSecond line stays with Q1.'
    assert parsed.questions[1].answered is False


def test_questions_summary_confirmation_checkpoint(R):
    parsed = R.parse_questions_file(
        F.real_questions(F.DEVDELTA, F.DEVDELTA_POC, "inception/requirements-analysis"), "r", "0" * 64
    )
    checkpoint = parsed.summary_confirmation
    assert checkpoint.kind == "summary_confirmation"
    assert checkpoint.present is True and checkpoint.answered is True
    assert checkpoint.answer == "Looks correct"
    assert checkpoint.options == ("Looks correct", "Request changes")
    assert parsed.pending_checkpoint is None


def test_questions_plan_approval_checkpoint(R):
    """The per-unit code-generation file: ``## Plan Approval`` with its own option vocabulary."""
    parsed = R.parse_questions_file(
        F.real_questions(F.DEVDELTA, F.DEVDELTA_POC, "construction/kiro-impact-poc/code-generation"),
        "r",
        "0" * 64,
    )
    assert parsed.plan_approval.kind == "plan_approval"
    assert parsed.plan_approval.options == ("Approve Plan", "Request Changes")
    assert parsed.plan_approval.answered is True
    assert parsed.plan_approval.answer == "Approve Plan"
    assert parsed.summary_confirmation is None
    assert len(parsed.questions) == 1 and parsed.questions[0].answer == "A"


def test_questions_plan_approval_pending(R):
    text = F.real_questions(F.PAYLOAD_230, F.PAYLOAD_COMPLETED, "construction/health-probe/code-generation")
    blanked = text.replace("[Answer]: Approve Plan", "[Answer]:")
    parsed = R.parse_questions_file(blanked, "r", "0" * 64)
    assert parsed.questions == ()
    assert parsed.plan_approval.present is True and parsed.plan_approval.answered is False
    assert parsed.pending_checkpoint == "plan_approval"
    assert parsed.pending_count == 0   # checkpoints are counted separately


def test_questions_summary_wins_when_both_checkpoints_are_pending(R):
    text = (
        "# Q\n\n## Consolidated Summary Confirmation\n\n- Looks correct\n- Request changes\n\n[Answer]:\n\n"
        "## Plan Approval\n\n- Approve Plan\n- Request Changes\n\n[Answer]:\n"
    )
    parsed = R.parse_questions_file(text, "r", "0" * 64)
    assert parsed.pending_checkpoint == "summary_confirmation"
    assert parsed.plan_approval.answered is False


def test_questions_post_approval_amendment_yields_an_ordinary_pending_question(R):
    """Gate card and question card can legitimately coexist (review P09)."""
    parsed = R.parse_questions_file(
        F.real_questions(F.PAYLOAD_230, F.PAYLOAD_GATE_OPEN, "inception/requirements-analysis"),
        "r",
        "0" * 64,
    )
    assert [question.index for question in parsed.questions] == [1, 2, 3, 4]
    assert parsed.questions[3].answered is False
    assert parsed.pending_count == 1
    assert parsed.summary_confirmation.answered is True
    assert parsed.pending_checkpoint is None


def test_questions_free_sections_contribute_nothing(R):
    """``## Sources``, ``## 权威来源…`` and the ``## F1.（跟进）`` follow-ups are not questions."""
    text = F.real_questions(F.OLDER, F.OLDER_INTENT, "ideation/market-research")
    assert "## F1." in text
    parsed = R.parse_questions_file(text, "r", "0" * 64)
    assert len(parsed.questions) == 6
    assert parsed.pending_count == 0
    devdelta = R.parse_questions_file(
        F.real_questions(F.DEVDELTA, F.DEVDELTA_POC, "inception/requirements-analysis"), "r", "0" * 64
    )
    assert "## 权威来源" in F.real_questions(F.DEVDELTA, F.DEVDELTA_POC, "inception/requirements-analysis")
    assert len(devdelta.questions) == 4


def test_questions_multi_select_marker(R):
    text = F.questions_text(questions=[("Which ones apply? (multi-select)", ["a", "b"])])
    parsed = R.parse_questions_file(text, "r", "0" * 64)
    assert parsed.questions[0].multi_select is True
    assert R.parse_questions_file(
        F.questions_text(questions=[("Pick one", ["a"])]), "r", "0" * 64
    ).questions[0].multi_select is False


@pytest.mark.parametrize("marker", [
    "(multi-select)", "(MULTI SELECT)", "[multi-select]",
    "(select all that apply)", "（select all that apply）", "（多选）", "（可多选）",
])
def test_questions_explicit_multi_select_spellings(R, marker):
    parsed = R.parse_questions_file(
        F.questions_text(questions=[(f"Choose applicable changes {marker}", ["Docs", "Tests"])]),
        "questions.md", "0" * 64,
    )
    assert parsed.questions[0].multi_select is True


def test_questions_multi_select_is_not_inferred_from_an_option_or_background(R):
    parsed = R.parse_questions_file(
        "### Q1. Pick one\n\nBackground: (select all that apply) is another format.\n"
        "- A. Describe (multi-select)\n- X. Other\n\n[Answer]:\n",
        "questions.md", "0" * 64,
    )
    assert parsed.questions[0].multi_select is False


@pytest.mark.parametrize("body", [
    "### Q1. First?\n- A. One\n[Answer]:\n### Q1. Second?\n- A. Two\n[Answer]:\n",
    "### Q1. Pick one\n- A. One\n- A. Two\n[Answer]:\n",
    "### Q1. Missing tag\n- A. One\n",
    "### Q1. Duplicate tag\n- A. One\n[Answer]:\n[Answer]:\n",
    "### Q1. Unsupported options\n- A) One\n- B. Two\n[Answer]:\n",
    "### Q1. No offered options\n[Answer]:\n",
])
def test_ambiguous_question_files_are_not_enabled_as_forms(R, body):
    parsed = R.parse_questions_file(body, "questions.md", "0" * 64)
    assert R.file_questions_support_forms(parsed) is False


def test_file_question_forms_ignore_fenced_examples(R):
    parsed = R.parse_questions_file(
        "### Q1. Pick one\n- A. One\n- X. Other\n"
        "```text\n[Answer]: not a real tag\n- A) example\n```\n[Answer]:\n",
        "questions.md", "0" * 64,
    )
    assert R.file_questions_support_forms(parsed) is True


def test_questions_blank_answers_helper_makes_the_gate_scenario(R):
    """``blank_answers`` on the real file is what ``gate_repo`` writes: four pending Q + blank summary."""
    parsed = R.parse_questions_file(F.blank_answers(F.real_questions()), "r", "0" * 64)
    assert parsed.pending_count == 4
    assert parsed.pending_checkpoint == "summary_confirmation"
    assert parsed.summary_confirmation.answered is False
    assert parsed.summary_confirmation.options == ("Looks correct", "Request changes")
    assert parsed.questions[0].options[-1].is_other is True


def test_question_digest_is_over_the_whole_file(R):
    data = F.real_questions().encode()
    assert R.question_digest(data) == hashlib.sha256(data).hexdigest()
    assert R.question_digest(None) is None
    assert R.question_digest(data + b"\n") != R.question_digest(data)


def test_read_questions_returns_none_when_absent(reader, devdelta_repo):
    stage_dir = reader.stage_dir("default", F.DEVDELTA_POC, "construction", "build-and-test", None)
    assert reader.read_questions(devdelta_repo, stage_dir, "build-and-test") is None
    per_unit = reader.stage_dir("default", F.DEVDELTA_POC, "construction", "code-generation", "kiro-impact-poc")
    found = reader.read_questions(devdelta_repo, per_unit, "code-generation")
    assert found.relpath == f"{per_unit}/code-generation-questions.md"
    assert found.sha256 == hashlib.sha256((devdelta_repo / found.relpath).read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# engine data: stage graph, scope grid, scopes, stage files
# --------------------------------------------------------------------------- #


def test_stage_graph_33_stages(R):
    nodes = R.parse_stage_graph(json.dumps(F.stage_graph(F.DEVDELTA)).encode())
    slugs = [node.slug for node in nodes]
    assert len(nodes) == 33
    assert "domain-design" in slugs and "contract-design" in slugs
    assert "application-design" not in slugs
    node = next(node for node in nodes if node.slug == "requirements-analysis")
    assert node.number == "2.3" and node.phase == "inception"
    assert node.reviewer == "aidlc-product-lead-agent"
    assert node.reviewer_max_iterations == 2
    assert node.review_class == "advisory"
    assert node.summary_confirmation == "required"
    assert node.produces == ("requirements", "requirements-analysis-questions")
    assert [consume.artifact for consume in node.consumes][:2] == ["intent-statement", "scope-document"]
    assert node.consumes[2].conditional_on == "brownfield"
    assert not any(consume.required for consume in node.consumes)
    code_gen = next(node for node in nodes if node.slug == "code-generation")
    assert code_gen.for_each == "unit-of-work" and code_gen.per_unit is True
    assert code_gen.workspace_requires is True
    assert code_gen.mode == "subagent" and code_gen.review_class == "adversarial"
    assert any(consume.required for consume in code_gen.consumes)
    functional = next(node for node in nodes if node.slug == "functional-design")
    assert functional.optional_produces == ("frontend-components",)
    assert functional.produces_kinds["frontend-components"] == ("ui",)


@pytest.mark.parametrize("tree", [F.PAYLOAD_230, F.OLDER])
def test_stage_graph_32_stages_without_the_newer_keys(R, tree):
    nodes = R.parse_stage_graph(json.dumps(F.stage_graph(tree)).encode())
    slugs = [node.slug for node in nodes]
    assert len(nodes) == 32
    assert "application-design" in slugs and "contract-design" not in slugs
    node = next(node for node in nodes if node.slug == "requirements-analysis")
    assert node.review_class is None and node.summary_confirmation is None
    code_gen = next(node for node in nodes if node.slug == "code-generation")
    assert code_gen.per_unit is True
    assert code_gen.produces_kinds == {}


def test_stage_graph_tolerates_junk(R):
    assert R.parse_stage_graph(b"") == []
    assert R.parse_stage_graph(b"{}") == []
    nodes = R.parse_stage_graph(
        json.dumps({"stages": [{"slug": "only-slug"}, {"name": "no slug"}, "junk"]}).encode()
    )
    assert len(nodes) == 1
    node = nodes[0]
    assert (node.slug, node.name, node.phase, node.produces, node.consumes) == (
        "only-slug",
        "only-slug",
        "",
        (),
        (),
    )
    assert node.workspace_requires is False and node.reviewer_max_iterations is None


def test_scope_grid_including_a_project_composed_scope(R):
    grid = R.parse_scope_grid(json.dumps(F.scope_grid(F.DEVDELTA)).encode())
    assert len(grid) == 10
    assert "review-major-remediation" in grid
    assert grid["poc"]["requirements-analysis"] == "EXECUTE"
    assert grid["poc"]["market-research"] == "SKIP"
    assert len(R.parse_scope_grid(json.dumps(F.scope_grid(F.PAYLOAD_230)).encode())) == 9
    assert R.parse_scope_grid(b"[]") == {}
    assert R.parse_scope_grid(json.dumps({"flat": {"a": "EXECUTE"}}).encode()) == {"flat": {"a": "EXECUTE"}}


def test_scope_frontmatter_stock_and_project_owned(R):
    poc = R.parse_scope_frontmatter(
        (F.DEVDELTA / ".kiro/scopes/aidlc-poc.md").read_text("utf-8"), "aidlc-poc.md"
    )
    assert (poc.name, poc.depth, poc.skeleton, poc.review_cap) == ("poc", "Minimal", "on", "advisory")
    assert poc.keywords == ("proof of concept", "prototype", "poc", "spike")
    assert poc.description == "Prove feasibility fast"
    assert poc.project_owned is False and poc.runner is None and poc.test_strategy is None

    workshop = R.parse_scope_frontmatter(
        (F.DEVDELTA / ".kiro/scopes/aidlc-workshop.md").read_text("utf-8"), "aidlc-workshop.md"
    )
    assert workshop.test_strategy == "Minimal"   # camelCase on disk

    bugfix = R.parse_scope_frontmatter(
        (F.DEVDELTA / ".kiro/scopes/aidlc-bugfix.md").read_text("utf-8"), "aidlc-bugfix.md"
    )
    assert bugfix.runner is True

    composed = R.parse_scope_frontmatter(
        (F.DEVDELTA / ".kiro/scopes/aidlc-review-major-remediation.md").read_text("utf-8"),
        "aidlc-review-major-remediation.md",
    )
    assert composed.name == "review-major-remediation"
    assert composed.project_owned is True
    assert composed.keywords == ()
    assert composed.skeleton == "off"

    assert R.parse_scope_frontmatter("# no frontmatter\n", "aidlc-x.md") is None


def test_read_scopes_and_grid_from_a_repo(reader, devdelta_repo):
    scopes = reader.read_scopes(devdelta_repo, ".kiro")
    assert [scope.name for scope in scopes][:3] == ["bugfix", "enterprise", "feature"]
    assert len(scopes) == 10
    assert [scope.name for scope in scopes if scope.project_owned] == ["review-major-remediation"]
    grid = reader.read_scope_grid(devdelta_repo, ".kiro")
    assert set(grid) == {scope.name for scope in scopes}
    assert len(reader.read_stage_graph(devdelta_repo, ".kiro")) == 33


def test_stage_file_acceptance_criteria(R, reader, repo_builder):
    meta = R.parse_stage_file(
        "---\nslug: x\n---\n\n# X\n\n## Acceptance Criteria\n\n- one\n- two\n\n## Steps\n\n- not a criterion\n",
        "x",
    )
    assert meta.slug == "x" and meta.acceptance_criteria == ("one", "two")
    assert R.parse_stage_file("# X\n\n### Definition of Done\n- done\n", "x").acceptance_criteria == ("done",)
    assert R.parse_stage_file("# X\n\n## Steps\n- nope\n", "x").acceptance_criteria == ()

    repo = repo_builder.with_engine("payload").build()
    real = reader.read_stage_file(repo, ".kiro", "inception", "requirements-analysis")
    assert real is not None and real.acceptance_criteria == ()   # 2.6.2 declares none
    assert reader.read_stage_file(repo, ".kiro", "inception", "no-such-stage") is None
    assert reader.read_stage_file(repo, ".kiro", "not-a-phase", "requirements-analysis") is None


@pytest.mark.parametrize("tree,count", [(F.DEVDELTA, 33), (F.PAYLOAD_230, 32)])
def test_every_harvested_stage_frontmatter_file_parses(R, tree, count):
    """All 33 (2.6.2) / 32 (2.3.0) stage definitions, straight from the harvested frontmatter."""
    files = sorted((tree / "stage-frontmatter").rglob("*.md"))
    assert len(files) == count
    for path in files:
        meta = R.parse_stage_file(path.read_text("utf-8"), path.stem)
        assert meta.slug == path.stem
        assert meta.acceptance_criteria == ()      # no engine generation declares any
    graph = {node.slug for node in R.parse_stage_graph(json.dumps(F.stage_graph(tree)).encode())}
    assert {path.stem for path in files} == graph   # frontmatter and graph name the same stages


def test_stage_graph_carries_the_review_artifact_and_absorbs_a_graph_without_one(reader, repo_builder, R):
    """``review_artifact`` (2.7.1) names the only file a ``## Review`` appendix may live in.

    Kept as its own field rather than folded into ``produces``: on four stages the reviewed artifact is
    not the first produced one, so a consumer that guessed ``produces[0]`` would read the verdict off a
    file the reviewer protocol forbids writing it to. Graphs that predate the key must still parse, which
    is what the 2.3.0 half asserts — its nodes carry ``None`` and every other field is untouched.
    """
    repo = repo_builder.with_engine("payload").build()
    graph = {node.slug: node for node in reader.read_stage_graph(repo, ".kiro")}
    assert graph["functional-design"].review_artifact == "functional-spec"
    assert graph["functional-design"].produces[0] == "entities"      # the four disagreements are real
    assert graph["nfr-requirements"].review_artifact == "security-requirements"
    assert graph["nfr-design"].review_artifact == "security-design"
    assert graph["infrastructure-design"].review_artifact == "cicd-pipeline"
    assert graph["requirements-analysis"].review_artifact == "requirements"
    assert graph["requirements-analysis"].to_json()["review_artifact"] == "requirements"
    assert sum(1 for node in graph.values() if node.review_artifact) == 13
    # A stage the graph gives no reviewer declares no reviewed artifact either.
    assert graph["workspace-scaffold"].review_artifact is None

    old = {node.slug: node for node in R.parse_stage_graph(json.dumps(F.stage_graph(F.PAYLOAD_230)).encode())}
    assert all(node.review_artifact is None for node in old.values())
    assert old["functional-design"].produces and old["functional-design"].reviewer   # rest still parsed


def test_bundled_payload_engine_reads_back_its_pinned_shape(reader, repo_builder, studio):
    repo = repo_builder.with_engine("payload").build()
    graph = reader.read_stage_graph(repo, ".kiro")
    assert len(graph) == studio.constants.BUNDLED_STAGE_COUNT
    assert reader.detect_harness_dirs(repo)[0].engine_version == studio.constants.BUNDLED_ENGINE_VERSION
    assert reader.detect_harness_dirs(repo)[0].engine_state_version == studio.constants.BUNDLED_STATE_VERSION
    # Every scope file the payload ships is a stock scope. The count is derived from disk rather than
    # written out, so a scope added by a future engine cannot pass while `STOCK_SCOPES` stays behind —
    # the `project_owned` line below is the tripwire, and this line only proves it was given work to do.
    scope_files = sorted((repo / ".kiro" / "scopes").glob("aidlc-*.md"))
    assert len(reader.read_scopes(repo, ".kiro")) == len(scope_files) == 11
    assert all(not scope.project_owned for scope in reader.read_scopes(repo, ".kiro"))


def test_detect_harness_dirs_per_engine_generation(reader, repo_builder, tmp_path, studio):
    payload = reader.detect_harness_dirs(repo_builder.with_engine("payload").build())
    assert len(payload) == 1
    assert payload[0].dir == ".kiro"
    assert payload[0].harness_name == "kiro"          # 2.6.2 onwards has "name"
    assert payload[0].rules_subdir == "steering"
    # Read off the live payload, so the literal belongs to `constants`, not to this assertion.
    assert payload[0].engine_version == studio.constants.BUNDLED_ENGINE_VERSION
    assert payload[0].stage_count == studio.constants.BUNDLED_STAGE_COUNT
    assert payload[0].has_utility is True
    assert payload[0].engine_state_version == 8

    older = RepoBuilderFor(tmp_path / "older")
    older_dirs = reader.detect_harness_dirs(older("older"))
    assert older_dirs[0].engine_version == "2.1.1"
    assert older_dirs[0].stage_count == 32
    assert older_dirs[0].harness_name is None          # older harness.json has no name

    bare = tmp_path / "bare"
    (bare / ".cursor" / "tools").mkdir(parents=True)   # a tools dir that is not an engine
    assert reader.detect_harness_dirs(bare) == []


# --------------------------------------------------------------------------- #
# review sections
# --------------------------------------------------------------------------- #


def test_review_section_verdict_from_real_artifacts(R):
    record = F.DEVDELTA_INTENTS / F.DEVDELTA_POC
    verdict, findings = R.parse_review_section(
        (record / "inception/requirements-analysis/requirements.md").read_text("utf-8")
    )
    assert verdict == "READY"      # written as "**Verdict:** READY"
    assert len(findings) == 3      # the reviewer used a table; the rows are read below
    verdict, _ = R.parse_review_section(
        (record / "ideation/intent-capture/intent-statement.md").read_text("utf-8")
    )
    assert verdict == "NOT-READY"  # written as a bare "Verdict: NOT-READY" line
    assert R.parse_review_section("# artifact\n\nno review\n") == (None, [])


def test_review_section_uses_the_last_section_and_maps_levels(R):
    text = (
        "# A\n\n## Review\n\nVerdict: NOT-READY\n\n### Blocker\n\n- first pass gap\n\n"
        "## Body\n\nprose\n\n## Review\n\n**Verdict:** READY\n**Reviewer:** aidlc-x-agent\n"
        "**Iteration:** 2\n\n### Blockers\n\n- `FR2.2` lacks a test\n\n### Advisory notes\n\n"
        "- naming drift\n\n### Resolved\n\n- earlier gap\n\n### Other\n\n- unclassified\n"
    )
    verdict, findings = R.parse_review_section(text)
    assert verdict == "READY"
    assert [finding.level for finding in findings] == ["blocker", "advisory", "resolved", "unknown"]
    assert findings[0].title == "`FR2.2` lacks a test"
    assert findings[0].quote == "FR2.2"
    assert all(finding.reviewer == "aidlc-x-agent" and finding.iteration == 2 for finding in findings)
    assert "first pass gap" not in [finding.title for finding in findings]


def test_a_reviewers_table_of_findings_is_never_reported_as_no_findings(R):
    """The adversary is the real 2.6.2 reviewer.

    Both reviewer agents ship a template whose findings are a Markdown table. A bullets-only parser
    hands the gate card an empty list, and the card states that as ``no open blockers`` to the person
    about to approve the stage.
    """
    record = F.DEVDELTA_INTENTS / F.DEVDELTA_POC
    verdict, findings = R.parse_review_section(
        (record / "inception/requirements-analysis/requirements.md").read_text("utf-8")
    )
    assert verdict == "READY"
    assert len(findings) == 3
    assert findings.findings_parsed is True
    # The reviewer's own severity words, second cell of each row, kept verbatim in the title: Studio
    # does not re-grade a reviewer, so none of these becomes a Studio `blocker`.
    assert [finding.title.split(" | ")[1] for finding in findings] == ["Major", "Minor", "Minor"]
    assert {finding.level for finding in findings} == {"unknown"}
    assert all(
        finding.reviewer == "aidlc-product-lead-agent" and finding.iteration == 1
        for finding in findings
    )
    # The column headings are above the `|---|` rule and are not a finding.
    assert not any(finding.title.startswith("#") for finding in findings)


def test_a_reviewers_tool_output_table_is_not_promoted_to_findings(R):
    """The adversary is a review body that holds two tables.

    The real terminal review of the poc's build-and-test stage lists one Minor finding and then nine
    rows of validation-tool output. Reading every table would put nine objections nobody wrote in front
    of the human, which is the same defect as losing the real one.
    """
    text = (
        F.DEVDELTA_INTENTS / F.DEVDELTA_POC / "construction/build-and-test/build-and-test-summary.md"
    ).read_text("utf-8")
    verdict, findings = R.parse_review_section(text)
    assert verdict == "READY"
    assert len(findings) == 1
    assert findings[0].title.startswith("1 | Minor | ")
    assert findings.findings_parsed is True


def test_a_findings_table_under_a_level_heading_keeps_that_headings_level(R):
    text = (
        "# A\n\n## Review\n\n**Verdict:** NOT-READY\n\n### Blockers\n\n"
        "| # | Severity | Finding |\n|---|---|---|\n| 1 | Critical | `units.yaml` has no owner |\n"
    )
    _, findings = R.parse_review_section(text)
    assert [finding.level for finding in findings] == ["blocker"]
    assert findings[0].title == "1 | Critical | `units.yaml` has no owner"
    assert findings[0].quote == "units.yaml"


def test_a_review_table_studio_cannot_place_is_never_proof_that_there_are_no_blockers(R):
    """The adversary is a reviewer that files its blockers under a heading nobody agreed on.

    Studio must not read those rows as findings (it cannot tell them from tool output), and it must not
    report the resulting empty list as the reviewer's own answer either: the gate card would then affirm
    ``no open blockers`` over a table whose first row says Critical.
    """
    text = (
        "# A\n\n## Review\n\n**Verdict:** NOT-READY\n\n### Observations\n\n"
        "| # | Severity | Finding |\n|---|---|---|\n| 1 | Critical | the schema is not versioned |\n"
    )
    verdict, findings = R.parse_review_section(text)
    assert verdict == "NOT-READY"
    assert findings == []
    assert findings.findings_parsed is False
    # "The reviewer listed nothing" stays sayable: the real intent-statement review is a bare verdict.
    _, bare = R.parse_review_section(
        (F.DEVDELTA_INTENTS / F.DEVDELTA_POC / "ideation/intent-capture/intent-statement.md")
        .read_text("utf-8")
    )
    assert bare == [] and bare.findings_parsed is True
    # And a reviewer whose findings section says "None." while its tool table passes is not a failure to
    # read: the table is named, so its rows are not mistaken for findings Studio lost.
    _, none_listed = R.parse_review_section(
        "# A\n\n## Review\n\n**Verdict:** READY\n\n### Findings\n\nNone.\n\n"
        "### Validation Tool Results\n\n| Tool | Result |\n|---|---|\n| validate-entities | PASS |\n"
    )
    assert none_listed == [] and none_listed.findings_parsed is True


# --------------------------------------------------------------------------- #
# breadcrumb, markers, derived JSON
# --------------------------------------------------------------------------- #


def test_recovery_breadcrumb_has_no_leading_dash(R):
    text = (
        "# AIDLC Recovery Breadcrumb\n**Last validated**: 2026-09-02T03:53:25Z\n"
        "**Current stage**: build-and-test\n**State file**: valid (all required sections present)\n"
    )
    assert R.parse_recovery_breadcrumb(text) == {
        "Last validated": "2026-09-02T03:53:25Z",
        "Current stage": "build-and-test",
        "State file": "valid (all required sections present)",
    }


def test_markers_of_a_real_record(reader, devdelta_repo):
    markers = reader.read_markers(devdelta_repo, "default", F.DEVDELTA_POC)
    assert markers.human_turn_mtime_ns and markers.engine_touch_mtime_ns
    assert markers.recovery["Current stage"] == "build-and-test"
    assert markers.goal_stop_present is True      # present, and deliberately not authoritative
    assert markers.turn_counter == 27
    assert markers.compose_pending is False
    assert markers.stop_block_count is None and markers.reviewer_dispatch is None
    assert markers.hooks_health == {}


def test_markers_absent_everywhere(reader, repo_builder):
    repo = repo_builder.with_workspace().with_intent("260904-x", state=GATE_STATE, audit=GATE_AUDIT).build()
    markers = reader.read_markers(repo, "default", "260904-x")
    assert markers.human_turn_mtime_ns is None
    assert markers.recovery is None
    assert markers.goal_stop_present is False
    assert markers.turn_counter is None
    assert markers.to_json()["hooks_health"] == {}


def test_markers_optional_files(reader, repo_builder):
    repo = repo_builder.with_workspace().with_intent("260904-x", state=GATE_STATE, audit=GATE_AUDIT).build()
    record = repo / "aidlc/spaces/default/intents/260904-x"
    (record / ".aidlc-hooks-health").mkdir()
    (record / ".aidlc-hooks-health/validate-state.last").write_text("2026-09-04T09:59:00Z\n")
    (record / ".aidlc-hooks-health/session-start.drops").write_text("ignored\n")
    (record / ".aidlc-stop-hook").mkdir()
    (record / ".aidlc-stop-hook/block-count.json").write_text('{"signature":"","count":2}')
    (record / ".aidlc-reviewer-dispatch.json").write_text('{"reviewer":"r","stage":"code-generation"}')
    (repo / "aidlc/.aidlc-compose-pending").write_text("")
    (repo / "aidlc/.aidlc-turn-counter").write_text("41\n")
    markers = reader.read_markers(repo, "default", "260904-x")
    assert markers.hooks_health == {"validate-state": "2026-09-04T09:59:00Z"}
    assert markers.stop_block_count == {"signature": "", "count": 2}
    assert markers.reviewer_dispatch["stage"] == "code-generation"
    assert markers.compose_pending is True
    assert markers.turn_counter == 41


def test_runtime_graph_and_traceability_are_raw_dicts(R):
    assert R.parse_runtime_graph(b'{"workflow_id": "x", "stages": []}') == {"workflow_id": "x", "stages": []}
    assert R.parse_runtime_graph(b"[]") is None
    assert R.parse_runtime_graph(b"nope") is None
    assert R.parse_traceability(b'{"entries": []}') == {"entries": []}
    assert R.parse_traceability(b"") is None


# --------------------------------------------------------------------------- #
# stable reads
# --------------------------------------------------------------------------- #


def test_stable_read_of_a_settled_file(R, reader, repo_builder):
    repo = repo_builder.with_workspace().with_intent("260904-x", state=GATE_STATE, audit=GATE_AUDIT).build()
    rel = "aidlc/spaces/default/intents/260904-x/aidlc-state.md"
    snap = R.stable_read(repo, rel, R.C.MAX_STATE_BYTES)
    assert snap.relpath == rel
    assert snap.unstable is False
    assert snap.size == len(snap.bytes)
    assert snap.sha256 == hashlib.sha256((repo / rel).read_bytes()).hexdigest()
    assert snap.sha256 == GATE_STATE_SHA
    assert snap.text.startswith("# AI-DLC State Tracking")
    assert "bytes" not in snap.to_json()


def test_stable_read_missing_and_secret_and_oversize(R, reader, repo_builder, studio):
    repo = repo_builder.with_workspace().with_intent("260904-x", state=GATE_STATE, audit=GATE_AUDIT).build()
    record = "aidlc/spaces/default/intents/260904-x"
    assert R.stable_read(repo, f"{record}/nope.md", 1024) is None
    (repo / record / "usage-ledger.json").write_text("{}")
    assert R.stable_read(repo, f"{record}/usage-ledger.json", 1024) is None
    with pytest.raises(studio.errors.StudioError) as excinfo:
        R.stable_read(repo, f"{record}/aidlc-state.md", 16)
    assert excinfo.value.code == "too_large"
    assert excinfo.value.status == 413
    with pytest.raises(studio.errors.StudioError) as excinfo:
        R.stable_read(repo, "../outside.md", 1024)
    assert excinfo.value.code == "bad_path"


def test_stable_read_reports_a_file_that_moved_between_the_two_stats(
    R, reader, repo_builder, monkeypatch
):
    """An unstable snapshot still carries bytes, but nothing may be authorised from it."""
    repo = repo_builder.with_workspace().with_intent("260904-x", state=GATE_STATE, audit=GATE_AUDIT).build()
    rel = "aidlc/spaces/default/intents/260904-x/aidlc-state.md"
    force_unstable(monkeypatch, (repo / rel).resolve())
    snap = R.stable_read(repo, rel, 512 * 1024)
    assert snap is not None
    assert snap.unstable is True
    assert snap.sha256 == GATE_STATE_SHA          # the second read's bytes are returned
    assert snap.to_json()["unstable"] is True

    boundary = reader.stable_boundary_inputs(repo, "default", "260904-x")
    assert boundary.stable is False
    assert boundary.state_snap.unstable is True
    assert boundary.state.current_stage == "requirements-analysis"


def test_stable_boundary_inputs_of_a_settled_gate(reader, gate_repo):
    boundary = reader.stable_boundary_inputs(gate_repo, "default", "260904-gate-demo")
    assert boundary.stable is True
    assert boundary.stage == "requirements-analysis"
    assert boundary.state.row("requirements-analysis").state == "awaiting_approval"
    assert boundary.directive.matches_state is True
    assert boundary.questions is not None
    assert boundary.questions.pending_count == 4
    assert boundary.questions.pending_checkpoint == "summary_confirmation"
    assert boundary.audit.complete is True
    assert boundary.taken_at == "2026-09-04T10:00:00Z"
    assert boundary.to_json()["stable"] is True


def test_stable_boundary_inputs_without_a_state_file(reader, repo_builder):
    repo = repo_builder.with_workspace().build()
    intents = repo / "aidlc/spaces/default/intents"
    (intents / "260904-ghost").mkdir(parents=True)
    boundary = reader.stable_boundary_inputs(repo, "default", "260904-ghost")
    assert boundary.stable is False
    assert boundary.state is None and boundary.state_snap is None
    assert boundary.stage is None and boundary.questions is None


def test_stable_boundary_inputs_finds_a_per_unit_questions_file(reader, repo_builder):
    """A per-unit Construction stage keeps its questions under ``construction/<unit>/<slug>/``."""
    state = F.insert_runtime_field(
        F.state_text(
            marks={"code-generation": "?"},
            fields={
                "Current Stage": "code-generation",
                "Status": "Running",
                "Next Stage": "build-and-test",
            },
        ),
        "Active Unit",
        "unit-a",
    )
    repo = repo_builder.with_workspace().with_intent(
        "260904-u",
        state=state,
        audit=F.gate_open_audit("code-generation"),
        questions={
            "construction/unit-a/code-generation/code-generation-questions.md": (
                "# Plan\n\n## Plan Approval\n\n- Approve Plan\n- Request Changes\n\n[Answer]:\n"
            )
        },
    ).build()
    boundary = reader.stable_boundary_inputs(repo, "default", "260904-u")
    assert boundary.stable is True
    assert boundary.stage == "code-generation"
    assert boundary.questions is not None
    assert boundary.questions.relpath.endswith(
        "construction/unit-a/code-generation/code-generation-questions.md"
    )
    assert boundary.questions.pending_checkpoint == "plan_approval"
    assert boundary.unit_ambiguous is False
    assert reader.list_units(repo, "default", "260904-u") == ["unit-a"]


PLAN_APPROVAL_QUESTIONS = "# Plan\n\n## Plan Approval\n\n- Approve Plan\n- Request Changes\n\n[Answer]:\n"

PER_UNIT_GATE_STATE = F.state_text(
    marks={"code-generation": "?"},
    fields={
        "Current Stage": "code-generation",
        "Status": "Running",
        "Next Stage": "build-and-test",
    },
)


def two_unit_repo(repo_builder, questions: dict[str, str]) -> Path:
    """A record whose Construction holds ``unit-a`` and ``unit-b`` and names neither as executing.

    No ``Active Unit`` in the state file and no directive, which is exactly the state in which the two
    sources that could say which unit is running both say nothing.
    """
    return repo_builder.with_workspace().with_intent(
        "260904-two-units",
        state=PER_UNIT_GATE_STATE,
        audit=F.gate_open_audit("code-generation"),
        questions=questions,
        artifacts={
            "construction/unit-a/code-generation/code-generation-plan.md": "# a\n",
            "construction/unit-b/code-generation/code-generation-plan.md": "# b\n",
        },
    ).build()


def test_two_units_with_questions_and_no_named_unit_yield_no_questions_at_all(reader, repo_builder):
    """The adversary is a two-unit Construction stage with nothing naming the unit being executed.

    Both units hold this stage's questions file. Returning the first candidate returns whichever unit
    sorts first, so a person is shown ``unit-a``'s plan and their approval is delivered as though it
    approved ``unit-b``'s. There is no reading of these files that says which one is right.
    """
    repo = two_unit_repo(
        repo_builder,
        {
            "construction/unit-a/code-generation/code-generation-questions.md": PLAN_APPROVAL_QUESTIONS,
            "construction/unit-b/code-generation/code-generation-questions.md": PLAN_APPROVAL_QUESTIONS,
        },
    )
    assert reader.list_units(repo, "default", "260904-two-units") == ["unit-a", "unit-b"]
    boundary = reader.stable_boundary_inputs(repo, "default", "260904-two-units")
    assert boundary.stage == "code-generation"
    assert boundary.questions is None
    assert boundary.unit_ambiguous is True
    assert boundary.to_json()["unit_ambiguous"] is True


def test_the_only_unit_holding_a_questions_file_is_resolved_from_its_path_not_guessed(
    reader, repo_builder
):
    """Two unit directories, one questions file: an answer exists, so refusing would be its own lie."""
    repo = two_unit_repo(
        repo_builder,
        {
            "construction/unit-b/code-generation/code-generation-questions.md": PLAN_APPROVAL_QUESTIONS,
        },
    )
    boundary = reader.stable_boundary_inputs(repo, "default", "260904-two-units")
    assert boundary.unit_ambiguous is False
    assert boundary.questions is not None
    assert boundary.questions.relpath.endswith(
        "construction/unit-b/code-generation/code-generation-questions.md"
    )


def test_a_unit_studio_could_not_identify_blocks_every_decision(reader, repo_builder, studio):
    """The adversary is the card the reader's refusal would otherwise produce.

    With no questions and no finding, the intent renders a question card with nothing on it, which reads
    as a Studio bug rather than as the contradiction it is. Consistency has to say it out loud, and
    blocking, because a decision here would land on the wrong unit of work.
    """
    cons = studio.consistency
    repo = two_unit_repo(
        repo_builder,
        {
            "construction/unit-a/code-generation/code-generation-questions.md": PLAN_APPROVAL_QUESTIONS,
            "construction/unit-b/code-generation/code-generation-questions.md": PLAN_APPROVAL_QUESTIONS,
        },
    )
    boundary = reader.stable_boundary_inputs(repo, "default", "260904-two-units")
    snap = SimpleNamespace(
        space="default",
        intent_dir="260904-two-units",
        intent_key="default/260904-two-units",
        state_snap=boundary.state_snap,
        state=boundary.state,
        directive=boundary.directive,
        audit=boundary.audit,
        questions=boundary.questions,
        stage=boundary.stage,
        unit=None,
        units=tuple(reader.list_units(repo, "default", "260904-two-units")),
        stable=boundary.stable,
    )
    facts = cons.RepoFacts(
        now="2026-09-04T10:00:00Z", repo=SimpleNamespace(), install=SimpleNamespace()
    )
    engine = cons.ConsistencyEngine()
    findings = engine.evaluate_intent(snap, facts)
    reported = [finding for finding in findings if finding.code == "unit_ambiguous"]
    assert len(reported) == 1
    assert reported[0].severity == "blocking"
    assert reported[0] in engine.blocking(findings)
    assert reported[0].message_key == "finding.unit_ambiguous"
    assert reported[0].params["units"] == ["unit-a", "unit-b"]
    assert reported[0].params["directive_unit"] is None
    assert reported[0].params["state_unit"] is None
    assert "unit_ambiguous" in cons.BLOCKING_FINDING_CODES
    assert json.loads(json.dumps(reported[0].to_json())) == reported[0].to_json()


def test_a_unit_resolved_from_the_files_is_not_reported_as_ambiguous(reader, repo_builder, studio):
    """The mirror case: one unit holds the questions, so the path names the unit and nothing disagrees.

    Without this the blocking finding would fire on every two-unit record and stop work that is fine.
    """
    cons = studio.consistency
    repo = two_unit_repo(
        repo_builder,
        {
            "construction/unit-b/code-generation/code-generation-questions.md": PLAN_APPROVAL_QUESTIONS,
        },
    )
    boundary = reader.stable_boundary_inputs(repo, "default", "260904-two-units")
    snap = SimpleNamespace(
        space="default",
        intent_dir="260904-two-units",
        intent_key="default/260904-two-units",
        state_snap=boundary.state_snap,
        state=boundary.state,
        directive=boundary.directive,
        audit=boundary.audit,
        questions=boundary.questions,
        stage=boundary.stage,
        unit=None,
        units=tuple(reader.list_units(repo, "default", "260904-two-units")),
        stable=boundary.stable,
    )
    facts = cons.RepoFacts(
        now="2026-09-04T10:00:00Z", repo=SimpleNamespace(), install=SimpleNamespace()
    )
    findings = cons.ConsistencyEngine().evaluate_intent(snap, facts)
    assert [finding.code for finding in findings if finding.code == "unit_ambiguous"] == []


# --------------------------------------------------------------------------- #
# artifacts and units
# --------------------------------------------------------------------------- #


def test_list_artifacts_of_a_real_record(reader, devdelta_repo):
    artifacts = reader.list_artifacts(devdelta_repo, "default", F.DEVDELTA_POC)
    assert artifacts.truncated is False
    by_name = {meta.name: meta for meta in artifacts}
    assert [meta.relpath for meta in artifacts] == sorted(meta.relpath for meta in artifacts)
    assert all(meta.relpath.startswith("aidlc/spaces/default/intents/") for meta in artifacts)
    assert all(meta.sha256 for meta in artifacts)          # small record → every file hashed

    questions = by_name["requirements-analysis-questions.md"]
    assert (questions.phase, questions.stage, questions.unit) == ("inception", "requirements-analysis", None)
    assert questions.kind == "questions" and questions.renderable is True
    assert questions.artifact_id == hashlib.sha1(questions.relpath.encode()).hexdigest()
    assert questions.mtime.endswith("Z")

    per_unit = by_name["code-generation-plan.md"]
    assert (per_unit.phase, per_unit.stage, per_unit.unit) == ("construction", "code-generation", "kiro-impact-poc")
    assert by_name["memory.md"].kind == "memory"
    assert by_name["aidlc-state.md"].stage is None and by_name["aidlc-state.md"].kind == "other"
    summary = by_name["build-and-test-summary.md"]
    assert (summary.stage, summary.unit) == ("build-and-test", None)   # once-per-workflow layout


def test_list_artifacts_skips_dot_entries_and_secrets(reader, repo_builder):
    repo = repo_builder.with_workspace().with_intent(
        "260904-x",
        state=GATE_STATE,
        audit=GATE_AUDIT,
        artifacts={
            "inception/requirements-analysis/requirements.md": "# R\n",
            ".aidlc-sensors/build-and-test/type-check-01.md": "finding\n",
            "usage-ledger.json": "{}",
            ".env": "SECRET=1\n",
        },
        markers=[".aidlc-human-turn"],
    ).build()
    names = {meta.name for meta in reader.list_artifacts(repo, "default", "260904-x")}
    assert "requirements.md" in names
    assert names.isdisjoint({"usage-ledger.json", ".env", ".aidlc-human-turn", "type-check-01.md"})


def test_list_artifacts_respects_the_cap_and_the_deadline(R, reader, repo_builder, monkeypatch, studio):
    repo = repo_builder.with_workspace().with_intent(
        "260904-x",
        state=GATE_STATE,
        audit=GATE_AUDIT,
        artifacts={f"inception/requirements-analysis/a{index}.md": "x" for index in range(6)},
    ).build()
    monkeypatch.setattr(studio.constants, "MAX_ARTIFACTS_PER_INTENT", 4)
    capped = reader.list_artifacts(repo, "default", "260904-x")
    assert len(capped) == 4 and capped.truncated is True and capped.reason == "cap"

    ticking = R.AidlcReader(TickingClock(step=studio.constants.REPO_SCAN_DEADLINE_SECS))
    timed_out = ticking.list_artifacts(repo, "default", "260904-x")
    assert timed_out.truncated is True and timed_out.reason == "deadline"


def test_list_artifacts_hash_limit(R, reader, repo_builder, monkeypatch):
    repo = repo_builder.with_workspace().with_intent(
        "260904-x",
        state=GATE_STATE,
        audit=GATE_AUDIT,
        artifacts={f"inception/requirements-analysis/a{index}.md": "x" for index in range(4)},
    ).build()
    monkeypatch.setattr(R, "WHOLE_RECORD_HASH_LIMIT", 2)
    assert all(meta.sha256 is None for meta in reader.list_artifacts(repo, "default", "260904-x"))


def test_stage_artifacts_always_hashes(reader, devdelta_repo, R):
    stage_dir = reader.stage_dir("default", F.DEVDELTA_POC, "inception", "requirements-analysis", None)
    node = next(
        node for node in reader.read_stage_graph(devdelta_repo, ".kiro")
        if node.slug == "requirements-analysis"
    )
    metas = reader.stage_artifacts(devdelta_repo, stage_dir, node)
    assert [meta.name for meta in metas] == [
        "requirements-analysis-questions.md",
        "requirements.md",
    ]
    assert all(meta.sha256 for meta in metas)
    assert metas[1].sha256 == hashlib.sha256((devdelta_repo / metas[1].relpath).read_bytes()).hexdigest()
    assert metas[1].stage == "requirements-analysis" and metas[1].phase == "inception"
    assert reader.stage_artifacts(devdelta_repo, stage_dir, None) == metas
    empty_dir = reader.stage_dir("default", F.DEVDELTA_POC, "operation", "deployment-pipeline", None)
    assert reader.stage_artifacts(devdelta_repo, empty_dir, node) == []


def test_stage_dir_per_unit_and_plain(reader):
    assert reader.stage_dir("default", "260828-x", "inception", "requirements-analysis", None) == (
        "aidlc/spaces/default/intents/260828-x/inception/requirements-analysis"
    )
    assert reader.stage_dir("default", "260828-x", "construction", "code-generation", "unit-a") == (
        "aidlc/spaces/default/intents/260828-x/construction/unit-a/code-generation"
    )
    # a once-per-workflow construction stage ignores the unit even when one is passed
    assert reader.stage_dir("default", "260828-x", "construction", "build-and-test", "unit-a") == (
        "aidlc/spaces/default/intents/260828-x/construction/build-and-test"
    )
    assert reader.stage_dir("team-space", "260828-x", "ideation", "feasibility", None) == (
        "aidlc/spaces/team-space/intents/260828-x/ideation/feasibility"
    )


def test_list_units_excludes_stage_directories(reader, devdelta_repo):
    assert reader.list_units(devdelta_repo, "default", F.DEVDELTA_POC) == ["kiro-impact-poc"]
    assert reader.list_units(devdelta_repo, "default", F.DEVDELTA_REMEDIATION) == []


def test_read_artifact(reader, devdelta_repo, studio):
    rel = f"aidlc/spaces/default/intents/{F.DEVDELTA_POC}/inception/requirements-analysis/requirements.md"
    snap = reader.read_artifact(devdelta_repo, rel)
    assert snap.sha256 == hashlib.sha256((devdelta_repo / rel).read_bytes()).hexdigest()
    assert snap.unstable is False
    assert reader.read_artifact(devdelta_repo, "aidlc/nope.md") is None
    with pytest.raises(studio.errors.StudioError):
        reader.read_artifact(devdelta_repo, "/etc/passwd")


# --------------------------------------------------------------------------- #
# token formulas — golden values (a formula change must be a visible diff here)
# --------------------------------------------------------------------------- #


def test_boundary_token_golden(R):
    events = R.merge_audit([R.parse_audit_shard(GATE_AUDIT, "shard.md", 0)])
    assert R.latest_event(events, ("STAGE_AWAITING_APPROVAL",)).timestamp == "2026-09-04T09:00:03Z"
    token = R.boundary_token(GATE_STATE_SHA, events, "requirements-analysis", 0)
    assert token == hashlib.sha256(
        f"{GATE_STATE_SHA}:2026-09-04T09:00:03Z:requirements-analysis:0".encode()
    ).hexdigest()
    assert token == "9ff720ed0532e8345cf441a02fb7c7cef9dca5f0f31b260889b01326fc7d4b35"


def test_boundary_token_moves_only_with_the_boundary(R):
    events = R.merge_audit([R.parse_audit_shard(GATE_AUDIT, "shard.md", 0)])
    base = R.boundary_token(GATE_STATE_SHA, events, "requirements-analysis", 0)
    noisy = R.merge_audit(
        [
            R.parse_audit_shard(
                F.audit_text(GATE_AUDIT, F.audit_block("SUBAGENT_COMPLETED", "2026-09-04T09:30:00Z")),
                "shard.md",
                0,
            )
        ]
    )
    assert R.boundary_token(GATE_STATE_SHA, noisy, "requirements-analysis", 0) == base
    moved = R.merge_audit(
        [R.parse_audit_shard(F.gate_approved_audit("requirements-analysis"), "shard.md", 0)]
    )
    assert R.boundary_token(GATE_STATE_SHA, moved, "requirements-analysis", 0) != base
    assert R.boundary_token(GATE_STATE_SHA, events, "requirements-analysis", 1) != base
    assert R.boundary_token("f" * 64, events, "requirements-analysis", 0) != base
    no_events = R.boundary_token(GATE_STATE_SHA, [], None, 0)
    assert no_events == hashlib.sha256(f"{GATE_STATE_SHA}:none:none:0".encode()).hexdigest()


def test_stage_attempt_counts_revising_rows_per_stage(R):
    events = R.merge_audit(
        [R.parse_audit_shard(F.gate_rejected_audit("requirements-analysis"), "shard.md", 0)]
    )
    assert R.stage_attempt(events, "requirements-analysis") == 2
    assert R.stage_attempt(events, "user-stories") == 1
    assert R.stage_attempt([], "requirements-analysis") == 1
    three = R.merge_audit(
        [
            R.parse_audit_shard(
                F.audit_text(
                    *[
                        F.audit_block(
                            "STAGE_REVISING",
                            f"2026-09-04T09:0{index}:00Z",
                            Stage="requirements-analysis",
                            Revision_count=str(index + 1),
                        )
                        for index in range(3)
                    ]
                ),
                "shard.md",
                0,
            )
        ]
    )
    # three rejections → attempt 4, which is exactly when `accept_as_is` becomes offerable
    assert R.stage_attempt(three, "requirements-analysis") == 4


def test_evidence_digest_golden(R):
    artifacts = [
        R.ArtifactMeta(
            artifact_id="i2",
            relpath="aidlc/spaces/default/intents/260904-x/inception/requirements-analysis/requirements.md",
            name="requirements.md",
            stage="requirements-analysis",
            phase="inception",
            unit=None,
            size=10,
            mtime="2026-09-04T09:00:00Z",
            sha256="b" * 64,
            kind="artifact",
            renderable=True,
        ),
        R.ArtifactMeta(
            artifact_id="i1",
            relpath="aidlc/spaces/default/intents/260904-x/inception/requirements-analysis/requirements-analysis-questions.md",
            name="requirements-analysis-questions.md",
            stage="requirements-analysis",
            phase="inception",
            unit=None,
            size=20,
            mtime="2026-09-04T09:00:00Z",
            sha256="a" * 64,
            kind="questions",
            renderable=True,
        ),
    ]
    review = ("READY", [R.ReviewFinding("advisory", "naming drift", None, None, "aidlc-x-agent", 1)])
    digest = R.evidence_digest(artifacts, review)

    rows = sorted((meta.relpath, meta.sha256) for meta in artifacts)
    review_json = json.dumps(
        {"verdict": "READY", "findings": [review[1][0].to_json()]}, sort_keys=True, separators=(",", ":")
    )
    expected = hashlib.sha256(
        (
            "\n".join(f"{path}:{sha}" for path, sha in rows)
            + "\n"
            + hashlib.sha256(review_json.encode()).hexdigest()
        ).encode()
    ).hexdigest()
    assert digest == expected
    assert digest == "68a5c2d2fb572f45fb8b39eddde443fb82ece7214a825ad6bda0940200391a55"

    # order independent, content dependent, and null-vs-non-null review is a difference (C30)
    assert R.evidence_digest(list(reversed(artifacts)), review) == digest
    assert R.evidence_digest(artifacts, None) != digest
    assert R.evidence_digest(artifacts, ("NOT-READY", review[1])) != digest
    assert R.evidence_digest([], None) == hashlib.sha256(
        ("\n" + hashlib.sha256(b"null").hexdigest()).encode()
    ).hexdigest()
    assert R.evidence_digest(None, None) is None


def test_evidence_digest_notices_an_artifact_rewritten_under_the_card(reader, R, repo_builder):
    repo = repo_builder.with_workspace().with_intent(
        "260904-x",
        state=GATE_STATE,
        audit=GATE_AUDIT,
        artifacts={"inception/requirements-analysis/requirements.md": "# R\n\nOne requirement.\n"},
    ).build()
    stage_dir = reader.stage_dir("default", "260904-x", "inception", "requirements-analysis", None)
    before = R.evidence_digest(reader.stage_artifacts(repo, stage_dir, None), None)
    (repo / stage_dir / "requirements.md").write_text("# R\n\nTwo requirements.\n")
    after = R.evidence_digest(reader.stage_artifacts(repo, stage_dir, None), None)
    assert before != after
    # the state file did not move, which is exactly why evidence_digest exists
    snap, _ = reader.read_state(repo, "default", "260904-x")
    assert snap.sha256 == GATE_STATE_SHA


def test_presence_baseline_golden(reader, repo_builder):
    repo = repo_builder.with_workspace().with_intent(
        "260904-x", state=GATE_STATE, audit=GATE_AUDIT, markers=[".aidlc-human-turn"]
    ).build()
    (repo / "aidlc/.aidlc-turn-counter").write_text("27\n")
    audit = reader.read_audit(repo, "default", "260904-x")
    snap, _ = reader.read_state(repo, "default", "260904-x")
    baseline = reader.presence_baseline(repo, "default", "260904-x", audit, snap.sha256)
    assert baseline.human_turn_events == 1
    assert baseline.human_turn_events_partial is False
    assert baseline.turn_counter == 27
    assert baseline.human_turn_mtime_ns is not None
    assert baseline.state_sha256 == GATE_STATE_SHA
    assert baseline.taken_at == "2026-09-04T10:00:00Z"
    assert list(baseline.shard_sizes) == [
        "aidlc/spaces/default/intents/260904-x/audit/fixture-host-0f1e2d3c4b5a.md"
    ]
    assert baseline.shard_sizes == {
        "aidlc/spaces/default/intents/260904-x/audit/fixture-host-0f1e2d3c4b5a.md": len(GATE_AUDIT)
    }
    assert baseline.audit_tail_sha256 == hashlib.sha256(GATE_AUDIT.encode()).hexdigest()
    assert baseline.to_json()["human_turn_events"] == 1


def test_presence_baseline_partial_when_a_shard_was_tail_read(reader, devdelta_repo):
    audit = reader.read_audit(devdelta_repo, "default", F.DEVDELTA_POC, tail_bytes=4096)
    baseline = reader.presence_baseline(devdelta_repo, "default", F.DEVDELTA_POC, audit, None)
    assert audit.complete is False
    assert baseline.human_turn_events_partial is True
    assert baseline.state_sha256 is None
    assert baseline.audit_tail_sha256 != hashlib.sha256(b"").hexdigest()


# --------------------------------------------------------------------------- #
# containment, JSON shape, and the read-only guarantee
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("space,intent", [("../evil", "260904-x"), ("default", "../evil"), ("", "260904-x")])
def test_illegal_coordinates_are_refused_before_any_read(reader, devdelta_repo, studio, space, intent):
    with pytest.raises(studio.errors.StudioError) as excinfo:
        reader.read_state(devdelta_repo, space, intent)
    assert excinfo.value.code == "bad_path"


def test_every_public_dataclass_serialises(reader, R, gate_repo):
    boundary = reader.stable_boundary_inputs(gate_repo, "default", "260904-gate-demo")
    payloads = [
        boundary.to_json(),
        boundary.state_snap.to_json(),
        boundary.state.to_json(),
        boundary.directive.to_json(),
        boundary.questions.to_json(),
        boundary.audit.to_json(),
        reader.read_markers(gate_repo, "default", "260904-gate-demo").to_json(),
        reader.presence_baseline(gate_repo, "default", "260904-gate-demo", boundary.audit, None).to_json(),
        reader.read_stage_graph(gate_repo, ".kiro")[0].to_json(),
        reader.read_scopes(gate_repo, ".kiro")[0].to_json(),
        reader.list_artifacts(gate_repo, "default", "260904-gate-demo")[0].to_json(),
        reader.registry(gate_repo, "default")[0].to_json(),
        reader.detect_harness_dirs(gate_repo)[0].to_json(),
        R.parse_stage_file("# x\n", "x").to_json(),
    ]
    for payload in payloads:
        encoded = json.dumps(payload)                    # every value is JSON-native
        assert json.loads(encoded) == payload
        assert all(key == key.lower() and " " not in key for key in payload)


def test_reader_never_writes(reader, R, gate_repo):
    """A full read pass over a repository leaves it byte for byte as it was."""
    before = tree_digest(gate_repo)
    boundary = reader.stable_boundary_inputs(gate_repo, "default", "260904-gate-demo")
    reader.list_artifacts(gate_repo, "default", "260904-gate-demo")
    reader.read_markers(gate_repo, "default", "260904-gate-demo")
    reader.read_audit(gate_repo, "default", "260904-gate-demo")
    reader.read_scopes(gate_repo, ".kiro")
    reader.read_scope_grid(gate_repo, ".kiro")
    reader.read_stage_graph(gate_repo, ".kiro")
    reader.list_units(gate_repo, "default", "260904-gate-demo")
    reader.detect_harness_dirs(gate_repo)
    reader.presence_baseline(gate_repo, "default", "260904-gate-demo", boundary.audit, None)
    stage_dir = reader.stage_dir("default", "260904-gate-demo", "inception", "requirements-analysis", None)
    reader.stage_artifacts(gate_repo, stage_dir, None)
    assert tree_digest(gate_repo) == before


def test_reader_never_reads_the_steering_token_key_or_the_usage_ledger(reader, repo_builder):
    repo = repo_builder.with_workspace().with_intent(
        "260904-x",
        state=GATE_STATE,
        audit=GATE_AUDIT,
        artifacts={
            ".aidlc-steering-token-key": "secret-key-material\n",
            "usage-ledger.json": '{"schemaVersion": 3}',
        },
    ).build()
    listed = reader.list_artifacts(repo, "default", "260904-x")
    assert not any("steering-token-key" in meta.relpath for meta in listed)
    assert not any("usage-ledger" in meta.relpath for meta in listed)
    stage_dir = "aidlc/spaces/default/intents/260904-x"
    assert not any(
        meta.name in {".aidlc-steering-token-key", "usage-ledger.json"}
        for meta in reader.stage_artifacts(repo, stage_dir, None)
    )


# --------------------------------------------------------------------------- #
# workspace signals (§1.6.3) — what a plan draft learns about the repository
# --------------------------------------------------------------------------- #


def test_workspace_signals_are_names_and_one_bounded_readme_read(R, reader, studio, tmp_path, monkeypatch):
    """FR-ADV-003: the advisor learns what a repository *is* from its top level, not from its contents.

    Names only (directories marked with a slash), dot-entries and symlinks skipped, the entry count
    capped, the manifests reported in ``WORKSPACE_MANIFESTS`` order, and exactly one bounded text read —
    the first ``README_NAMES`` hit, handed over WHOLE (as ``bounded_text`` read it): the reader does not
    cut it to ``MAX_README_CHARS``, because the broker's ``_clean`` redacts before it cuts and a
    credential straddling a cut made here would reach the scanner as an unrecognisable half (§1.18a).
    Nothing is recursed into: a README or a manifest one level down is not a signal, and a symlink is
    never followed.
    """
    K = studio.constants
    repo = tmp_path / "repo"
    (repo / "src" / "deep").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / ".git").mkdir()
    (repo / ".env").write_text("SECRET=1\n")
    (repo / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    (repo / "package.json").write_text("{}\n")
    (repo / "notes.txt").write_text("hello\n")
    (repo / "src" / "go.mod").write_text("module deep\n")                 # one level down: no signal
    (repo / "src" / "deep" / "README.md").write_text("# Not this one\n")
    readme = "# Demo\n" + ("lorem ipsum dolor sit amet " * 200)
    assert len(readme) > K.MAX_README_CHARS
    (repo / "README.md").write_text(readme)
    (repo / "README").write_text("# Also not this one: README.md comes first\n")
    os.symlink(repo / "src", repo / "linked")
    os.symlink(repo / "README.md", repo / "README.txt")

    reads: list[Path] = []
    real_bounded_text = R.security.bounded_text

    def counting(path, *args, **kwargs):
        reads.append(Path(path))
        return real_bounded_text(path, *args, **kwargs)

    monkeypatch.setattr(R.security, "bounded_text", counting)
    signals = reader.read_workspace_signals(repo)

    assert signals.top_level == (
        "README", "README.md", "docs/", "notes.txt", "package.json", "pyproject.toml", "src/",
    )
    assert signals.manifests == ("package.json", "pyproject.toml")
    assert signals.readme_excerpt == readme
    assert len(signals.readme_excerpt) > K.MAX_README_CHARS
    assert signals.truncated is False
    assert [(path.parent, path.name) for path in reads] == [(repo, "README.md")]
    assert signals.to_json() == {
        "top_level": list(signals.top_level),
        "manifests": list(signals.manifests),
        "readme_excerpt": signals.readme_excerpt,
        "truncated": False,
    }
    assert R.WORKSPACE_MANIFESTS[:2] == ("package.json", "pyproject.toml")
    assert R.README_NAMES[0] == "README.md"

    crowd = tmp_path / "crowd"
    crowd.mkdir()
    # Created in reverse so the listing's order is the reader's doing, not the filesystem's.
    for n in reversed(range(K.MAX_WORKSPACE_ENTRIES + 5)):
        (crowd / f"file-{n:03d}.txt").write_text("")
    for n in range(3):
        (crowd / f".dot-{n}").write_text("")          # never counts toward the cap
    os.symlink(crowd / "file-000.txt", crowd / "aaa-link")  # would sort first; never listed
    many = reader.read_workspace_signals(crowd)
    assert many.top_level == tuple(f"file-{n:03d}.txt" for n in range(K.MAX_WORKSPACE_ENTRIES))
    assert many.truncated is True
    assert many.manifests == () and many.readme_excerpt is None

    exact = tmp_path / "exact"
    exact.mkdir()
    for n in range(K.MAX_WORKSPACE_ENTRIES):
        (exact / f"file-{n:03d}.txt").write_text("")
    full = reader.read_workspace_signals(exact)
    assert len(full.top_level) == K.MAX_WORKSPACE_ENTRIES
    assert full.truncated is False  # exactly the cap is not "more than the cap"


def test_a_symlinked_readme_or_manifest_is_skipped_like_the_listing_skips_it(R, reader, tmp_path,
                                                                          monkeypatch):
    """The listing never names a symlink; the README and manifest probes must not read one either, even
    when its target sits inside the repository and containment alone would have allowed it — a link is
    the one top-level entry whose bytes are not the repository's own (§1.6.3, FR-ADV-003)."""
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "README.real.md").write_text("# The real README\n")
    (repo / "docs" / "package.real.json").write_text("{}\n")
    os.symlink(repo / "docs" / "README.real.md", repo / "README.md")
    os.symlink(repo / "docs" / "package.real.json", repo / "package.json")
    (repo / "Makefile").write_text("all:\n")

    reads: list[Path] = []
    real_bounded_text = R.security.bounded_text

    def counting(path, *args, **kwargs):
        reads.append(Path(path))
        return real_bounded_text(path, *args, **kwargs)

    monkeypatch.setattr(R.security, "bounded_text", counting)
    signals = reader.read_workspace_signals(repo)
    assert signals.top_level == ("Makefile", "docs/")
    assert signals.manifests == ("Makefile",)
    assert signals.readme_excerpt is None
    assert reads == []

    # A real README beside the linked one: the linked name is skipped, the next real name is read.
    (repo / "README").write_text("# Plain README\n")
    again = reader.read_workspace_signals(repo)
    assert again.readme_excerpt == "# Plain README\n"
    assert [path.name for path in reads] == ["README"]
