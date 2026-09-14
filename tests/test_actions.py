"""Action broker tests for ``backend/studio/actions.py``.

Every test here is written as a property that would let a real decision go wrong if it broke, because
this module is the only part of Studio that acts on a user's behalf and the acts are irreversible: a
prompt that reaches the AI-DLC conductor is recorded in someone's audit trail forever.

Four properties carry the file.

1. **The transition graph is exhaustive.** All 144 status pairs are exercised, not just the legal ones:
   an extra edge is how a "helpful" retry re-sends a decision that is already in flight.
2. **Compare-and-submit is per field.** Each of the six captured fields is perturbed on its own, and
   ``null`` vs non-``null`` is a difference — a gate whose artifact was rewritten under the reader must
   go stale even though the state file did not move.
3. **``NotDelivered`` is proven, never reported.** Each of the four proofs is broken in turn and the
   answer must be ``not_delivered_unproven`` plus ``DeliveryUncertain``; the whole point is that a
   gateway restart between the browser's send and its report can never look like "never sent".
4. **The lane is two-phase.** The backend commits, the browser sends. The tests assert the host's slot
   was never written to and that the module's source contains no host dispatch symbol at all.

The object graph is real — real Storage, Projection, ConsistencyEngine, RepoScheduler, SessionBinder,
EngineRunner over ``fake_bun``, real HostBridge over the duck-typed dashboard — so a signature drift in
a peer module fails here instead of in production. The only stand-in is the notification adapter, which
does not exist yet.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import fixtures as F

APP_ROOT = Path(__file__).resolve().parent.parent

REPO_ID = "r_000000000001"
IDENTITY = "1:100"
INTENT = "260904-gate-demo"
OTHER = "260904-second-intent"
STAGE = "requirements-analysis"
SLOT = f"aidlc-studio-{REPO_ID}-{INTENT}"
BOOT = "boot-test-0001"
TS = "2026-09-04T10:00:00Z"
QUESTIONS_REL = f"inception/{STAGE}/{STAGE}-questions.md"


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# module + wiring fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def A(studio):
    module = studio.actions
    assert module is not None, getattr(studio, "actions__error", "actions.py did not import")
    return module


@pytest.fixture
def C(studio):
    return studio.constants


class Notifications:
    """``NotificationAdapter`` stand-in: records one entry per ``notify_action`` call."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def notify_action(self, card: dict) -> dict:
        self.sent.append(card)
        return {"dashboard": True, "slack": "disabled", "dedupe_hit": False}


def _state_with(marks: dict[str, str] | None = None, revising: int = 0) -> str:
    return F.state_text(
        marks=marks or {STAGE: "?"},
        fields={
            "Current Stage": STAGE,
            "Status": "Running",
            "Next Stage": "user-stories",
            # The stage and the phase must agree, or `phase_stage_disagreement` (blocking) refuses
            # every dispatch before the interesting part of the test runs.
            "Lifecycle Phase": "INCEPTION",
            "Completed": "7",
        },
    )


def _questions(single: bool = False) -> str:
    """A pending questions file: the real one (many questions) or an authored single-question one."""
    if not single:
        return F.blank_answers(F.real_questions())
    return F.questions_text(stage=STAGE, questions=[("Which store?", ["Postgres", "SQLite"])])


SECOND_STAGE = "user-stories"


def _build_repo(
    builder,
    *,
    extra_intent: bool = False,
    revising: int = 0,
    questions: str | None = None,
    second_gate: bool = False,
    runnable: bool = False,
):
    audit = F.gate_open_audit(STAGE) if not runnable else F.audit_text(
        F.audit_block("HUMAN_TURN", TS),
        F.audit_block("STAGE_STARTED", F._plus(TS, 1), Stage=STAGE),
    )
    for index in range(revising):
        audit += F.audit_block(
            "STAGE_REVISING",
            F._plus(TS, 100 + index),
            Stage=STAGE,
            Revision_count=str(index + 1),
        )
    builder = builder.with_engine("payload").with_workspace()
    if extra_intent:
        # Written first so the *other* intent is the cursor target: the cursor switch has to move.
        builder = builder.with_intent(
            OTHER, state=_state_with({STAGE: " "}), audit=F.gate_open_audit(STAGE), active=False
        )
    marks = {STAGE: "-" if runnable else "?"}
    artifacts = {f"inception/{STAGE}/requirements.md": "# Requirements\n\nOne requirement.\n"}
    if second_gate:
        # Two `[?]` rows at once, each with its own reviewer verdict: the shape a card must not mix up.
        marks[SECOND_STAGE] = "?"
        artifacts[f"inception/{STAGE}/requirements.md"] = (
            "# Requirements\n\nOne requirement.\n\n## Review\n\nVerdict: READY\n"
        )
        artifacts[f"inception/{SECOND_STAGE}/user-stories.md"] = (
            "# User stories\n\n## Review\n\nVerdict: NOT-READY\n\n"
            "### Findings\n\n- blocker: the other stage's finding\n"
        )
    builder = builder.with_intent(
        INTENT,
        state=_state_with(marks, revising=revising),
        audit=audit,
        directive={"version": 1, "stage": STAGE, "state_sha256": "AUTO"},
        questions={} if runnable else {QUESTIONS_REL: questions if questions is not None else _questions()},
        artifacts=artifacts,
        active=not extra_intent,
    )
    if extra_intent:
        # Point the cursor at the other intent without touching INTENT's files.
        (builder.root / "aidlc" / "spaces" / "default" / "intents" / "active-intent").write_text(
            OTHER + "\n"
        )
    return builder.with_git().build()


@pytest.fixture
def repo_options() -> dict:
    """How the fixture repository is laid out. A test overrides it with ``parametrize``."""
    return {}


@pytest.fixture
def world(studio, fake_ctx, fake_host, clock, ids, fake_bun, repo_builder, repo_options):
    """The whole object graph, wired the way ``Services.build`` will wire it."""
    S = studio
    root = _build_repo(repo_builder, **dict(repo_options))

    store = S.storage.Storage(Path(fake_ctx.data_dir) / S.constants.DB_FILENAME, clock=clock, ids=ids)
    store.open()
    store.insert(
        "repos",
        {
            "id": REPO_ID,
            "resolved_identity": IDENTITY,
            "canonical_path": str(root),
            "label": "demo",
            "added_at": clock.iso(),
            "platform": "darwin",
            "install_status": "installed",
            "installed_engine_version": S.constants.BUNDLED_ENGINE_VERSION,
            "engine_dir": ".kiro",
            "availability": "available",
        },
    )
    reader = S.aidlc_reader.AidlcReader(clock)
    consistency = S.consistency.ConsistencyEngine()
    projection = S.projection.Projection(reader, consistency, clock)
    activity = S.activity.ActivityProjector(store, clock)
    events = S.events.EventLog(store, fake_ctx.events, clock)
    host = S.sessions.HostBridge(clock, logging.getLogger("test.actions"))
    settings = S.settings.SettingsService(store, host, clock)
    settings.load()
    scheduler = S.leases.RepoScheduler(
        store, host, settings, clock, activity, busy_reasons=S.sessions.busy_reasons
    )
    binder = S.sessions.SessionBinder(store, host, projection, clock, activity)
    engine = S.engine.EngineRunner(bun_path=fake_bun, clock=clock, activity=activity)
    notifications = Notifications()
    broker = S.actions.HumanActionBroker(
        store,
        projection,
        reader,
        consistency,
        scheduler,
        binder,
        engine,
        host,
        events,
        activity,
        notifications,
        settings,
        clock,
        ids,
        BOOT,
    )
    machine = S.actions.MachineActionBroker(broker, activity, clock)

    fake_host.add_slot(SLOT, project=str(root), agent="aidlc", app="")
    host.attach(fake_host)
    repo = S.repo_registry.RepoRecord.from_row(store.get("repos", REPO_ID))

    w = SimpleNamespace(
        S=S,
        root=root,
        store=store,
        reader=reader,
        consistency=consistency,
        projection=projection,
        activity=activity,
        events=events,
        host=host,
        settings=settings,
        scheduler=scheduler,
        binder=binder,
        engine=engine,
        notifications=notifications,
        broker=broker,
        machine=machine,
        repo=repo,
        clock=clock,
        ids=ids,
        fake_host=fake_host,
        builder=repo_builder,
        ctx=fake_ctx,
    )
    yield w
    store.close()


# --------------------------------------------------------------------------- #
# helpers over the graph
# --------------------------------------------------------------------------- #


def snapshot(w, intent_dir: str = INTENT):
    return w.projection.snapshot(w.repo, "default", intent_dir)


async def bind(w) -> Any:
    return await w.binder.bind(w.repo, "default", INTENT, slot_key=SLOT, intent_uuid=None)


async def seed(w, intent_dir: str = INTENT) -> list[Any]:
    """Derive this intent's cards from disk exactly as the reconciler does."""
    snap = snapshot(w, intent_dir)
    binding = await w.binder.get(REPO_ID, "default", intent_dir)
    live = await w.broker.list_live(
        repo_id=REPO_ID, space="default", intent_dir=intent_dir, include_revision=True
    )
    breakers = [
        SimpleNamespace(**row)
        for row in w.store.select("breakers")
        if str(row["key"]).startswith(f"{REPO_ID}:{intent_dir}:")
    ]
    seeds = w.projection.derive_cards(
        snap,
        findings=[],
        binding=binding,
        install=None,
        breakers=breakers,
        live_actions=live,
        slot=w.host.slot(SLOT),
    )
    return await w.broker.upsert_derived(seeds)


def pick(recs, type_: str):
    return next(rec for rec in recs if rec.type == type_)


def captured_of(rec, A, **overrides) -> dict:
    body = {name: getattr(rec.captured, name) for name in A.COMPARED_CAPTURED_FIELDS}
    body.update(overrides)
    return body


async def approve(w, A, *, rec=None, payload=None, wire=None):
    rec = rec or pick(await seed(w), "gate")
    body = payload or {"decision": "approve"}
    return await w.broker.submit(
        rec.action_id,
        captured=captured_of(rec, A),
        payload=body,
        client_wire_text=wire if wire is not None else w.S.constants.WIRE_APPROVE,
        user="owner-1",
    )


async def delivering(w, A):
    """A row sitting in ``Delivering`` with everything the delivery proof needs on it."""
    await bind(w)
    receipt = await approve(w, A)
    return await w.broker.get(receipt.action_id), receipt


def leases_held(w) -> list[Any]:
    return w.store.lease_list()


# --------------------------------------------------------------------------- #
# 1. the transition matrix
# --------------------------------------------------------------------------- #


def test_the_transition_table_is_the_prd_graph_and_nothing_more(A, C):
    assert A.ALLOWED_TRANSITIONS == {k: frozenset(v) for k, v in C.ALLOWED_TRANSITIONS.items()}
    assert set(A.ALLOWED_TRANSITIONS) == set(C.ACTION_STATUS)
    # The two cancels PRD §11.1 sanctions, and the four dead ends it names.
    assert "Cancelled" in A.ALLOWED_TRANSITIONS["Draft"]
    assert "Cancelled" in A.ALLOWED_TRANSITIONS["Queued"]
    for terminal in ("StateChanged", "ResolvedNoTransition", "Failed", "Cancelled"):
        assert A.ALLOWED_TRANSITIONS[terminal] == frozenset()
    # `Failed → Cancelled` is exactly the edge retire_stale must not use (review P07).
    assert "Cancelled" not in A.ALLOWED_TRANSITIONS["Failed"]


def test_every_status_pair_is_allowed_or_refused_exactly_as_the_table_says(world, A, C, studio):
    """The whole 12×12 matrix. An edge outside the table is how a decision gets replayed."""
    errors = studio.errors

    async def scenario():
        legal: list[tuple[str, str]] = []
        illegal: list[tuple[str, str]] = []
        for from_status in C.ACTION_STATUS:
            for to_status in C.ACTION_STATUS:
                rec = await _row_in(world, from_status)
                if to_status in A.ALLOWED_TRANSITIONS[from_status]:
                    after = await world.broker.transition(
                        rec.action_id,
                        to_status,
                        expected_generation=rec.status_generation,
                        reason="matrix",
                        evidence={},
                    )
                    assert after.status == to_status, (from_status, to_status)
                    legal.append((from_status, to_status))
                else:
                    with pytest.raises(errors.IllegalTransition) as exc:
                        await world.broker.transition(
                            rec.action_id,
                            to_status,
                            expected_generation=rec.status_generation,
                            reason="matrix",
                            evidence={},
                        )
                    assert exc.value.details["from"] == from_status
                    assert exc.value.details["to"] == to_status
                    unchanged = await world.broker.get(rec.action_id)
                    assert unchanged.status == from_status
                    illegal.append((from_status, to_status))
        assert len(legal) + len(illegal) == 144
        assert len(legal) == sum(len(v) for v in A.ALLOWED_TRANSITIONS.values())

    run(scenario())


async def _row_in(w, status: str):
    """One action row parked in ``status``, created without going through the graph."""
    action_id = w.ids.new("a")
    now = w.clock.iso()
    w.store.insert(
        "actions",
        {
            "action_id": action_id,
            "type": "gate",
            "risk_class": "human_lane",
            "repo_id": REPO_ID,
            "resolved_repo_identity": IDENTITY,
            "intent_dir": INTENT,
            "space": "default",
            "stage": STAGE,
            "status": status,
            "status_generation": 0,
            "created_at": now,
            "updated_at": now,
            "waiting_since": now,
            "source": "studio",
            "evidence_json": {},
        },
    )
    return await w.broker.get(action_id)


def test_a_lost_cas_is_a_stale_generation_not_a_silent_write(world, studio):
    async def scenario():
        rec = await _row_in(world, "Queued")
        await world.broker.transition(
            rec.action_id,
            "Cancelled",
            expected_generation=rec.status_generation,
            reason="first",
            evidence={},
        )
        with pytest.raises(studio.errors.StaleGeneration):
            await world.broker.transition(
                rec.action_id,
                "Delivering",
                expected_generation=rec.status_generation,
                reason="second",
                evidence={},
            )

    run(scenario())


# --------------------------------------------------------------------------- #
# 2. wire text
# --------------------------------------------------------------------------- #


def test_wire_text_templates_match_the_pinned_file_byte_for_byte(A):
    pinned = json.loads((APP_ROOT / "docs" / "design" / "wire-text.json").read_text("utf-8"))
    assert A.WIRE_TEXT_TEMPLATES == pinned["decisions"]


def test_wire_text_for_every_decision_is_the_pinned_string(A, C):
    def wire(decision, payload=None, questions=None, grouped=False):
        return A.wire_text_for(
            decision, payload or {}, questions=questions, grouped_answers_enabled=grouped
        )

    assert wire("approve") == "Approve"
    assert wire("accept_as_is") == "Accept as-is"
    assert wire("approve_plan") == "Approve Plan"
    assert wire("request_changes", {"feedback": " needs criteria "}) == "Request Changes: needs criteria"
    assert wire("request_plan_changes", {"feedback": "narrow it"}) == "Request Changes: narrow it"
    assert wire("confirm_summary", {"choice": "looks_correct"}) == "Looks correct"
    assert (
        wire("confirm_summary", {"choice": "request_changes", "feedback": "row 3"})
        == "Request changes: row 3"
    )
    assert wire("provide_input", {"kind": "scope", "scope": "poc"}) == "/aidlc --scope poc"
    assert wire("provide_input", {"kind": "free_text", "text": " ship it "}) == "ship it"
    assert wire("run") == "/aidlc"
    assert wire("resume") == "/aidlc --resume"
    assert wire("prepare_commit") == C.WIRE_PREPARE_COMMIT
    assert wire("prepare_commit").endswith("Do not push.")
    # The golden example from wire-text.json.
    pinned = json.loads((APP_ROOT / "docs" / "design" / "wire-text.json").read_text("utf-8"))
    assert (
        wire("request_changes", {"feedback": "The acceptance criteria for C3 are not covered."})
        == pinned["golden"]["request_changes_example"]
    )


def test_no_studio_only_or_host_control_decision_sends_anything(A, C):
    for decision in sorted(C.STUDIO_ONLY_DECISIONS | C.HOST_CONTROL_DECISIONS):
        assert (
            A.wire_text_for(decision, {}, questions=None, grouped_answers_enabled=True) is None
        ), decision
    assert A.lane_for("force_stop") == "host_control"
    assert A.lane_for("approve") == "human_lane"
    assert A.lane_for("acknowledge") == "studio_only"


@pytest.mark.parametrize(
    "decision,payload",
    [
        ("request_changes", {"feedback": "   "}),
        ("request_changes", {}),
        ("request_plan_changes", {"feedback": ""}),
        ("confirm_summary", {"choice": "request_changes", "feedback": " "}),
        ("provide_input", {"kind": "free_text", "text": "\n"}),
        ("provide_input", {"kind": "scope", "scope": ""}),
    ],
)
def test_a_blank_free_text_field_is_never_sent(A, studio, decision, payload):
    with pytest.raises(studio.errors.StudioError) as exc:
        A.wire_text_for(decision, payload, questions=None, grouped_answers_enabled=True)
    assert exc.value.code == "feedback_required"


def test_an_unknown_choice_or_kind_is_an_invalid_decision(A, studio):
    for decision, payload in (
        ("confirm_summary", {"choice": "yes"}),
        ("provide_input", {"kind": "shell", "text": "x"}),
        ("no_such_decision", {}),
    ):
        with pytest.raises(studio.errors.StudioError) as exc:
            A.wire_text_for(decision, payload, questions=None, grouped_answers_enabled=True)
        assert exc.value.code == "invalid_decision"


def _parse_questions(studio, text: str):
    return studio.aidlc_reader.parse_questions_file(text, QUESTIONS_REL, "sha")


def test_one_pending_question_sends_the_label_alone(A, studio):
    questions = _parse_questions(
        studio, F.questions_text(stage=STAGE, questions=[("Which store?", ["Postgres", "SQLite"])])
    )
    assert questions.pending_count == 1
    text = A.wire_text_for(
        "answers",
        {"answers": [{"index": 1, "option_letters": ["B"], "free_text": None}]},
        questions=questions,
        grouped_answers_enabled=False,
    )
    assert text == "SQLite"


def test_multi_select_joins_labels_and_a_single_select_refuses_two(A, studio):
    questions = _parse_questions(
        studio,
        F.questions_text(
            stage=STAGE,
            questions=[("Which stores? (multi-select)", ["Postgres", "SQLite", "Redis"])],
        ),
    )
    text = A.wire_text_for(
        "answers",
        {"answers": [{"index": 1, "option_letters": ["A", "C"], "free_text": None}]},
        questions=questions,
        grouped_answers_enabled=False,
    )
    assert text == "Postgres, Redis"

    single = _parse_questions(
        studio, F.questions_text(stage=STAGE, questions=[("Which store?", ["Postgres", "SQLite"])])
    )
    with pytest.raises(studio.errors.StudioError) as exc:
        A.wire_text_for(
            "answers",
            {"answers": [{"index": 1, "option_letters": ["A", "B"], "free_text": None}]},
            questions=single,
            grouped_answers_enabled=False,
        )
    assert exc.value.code == "invalid_decision"


def test_the_other_option_sends_free_text_and_refuses_a_blank_one(A, studio):
    questions = _parse_questions(
        studio, F.questions_text(stage=STAGE, questions=[("Which store?", ["Postgres"])])
    )
    assert questions.questions[0].options[-1].is_other
    text = A.wire_text_for(
        "answers",
        {"answers": [{"index": 1, "option_letters": ["X"], "free_text": " DynamoDB "}]},
        questions=questions,
        grouped_answers_enabled=False,
    )
    assert text == "DynamoDB"
    with pytest.raises(studio.errors.StudioError) as exc:
        A.wire_text_for(
            "answers",
            {"answers": [{"index": 1, "option_letters": ["X"], "free_text": ""}]},
            questions=questions,
            grouped_answers_enabled=False,
        )
    assert exc.value.code == "answers_incomplete"
    assert exc.value.details["reason"] == "free_text_required"


def test_an_unanswered_pending_question_and_an_unknown_letter_are_both_refused(A, studio):
    questions = _parse_questions(
        studio,
        F.questions_text(
            stage=STAGE, questions=[("One?", ["Yes", "No"]), ("Two?", ["Left", "Right"])]
        ),
    )
    with pytest.raises(studio.errors.StudioError) as exc:
        A.wire_text_for(
            "answers",
            {"answers": [{"index": 1, "option_letters": ["A"], "free_text": None}]},
            questions=questions,
            grouped_answers_enabled=True,
        )
    assert exc.value.code == "answers_incomplete"
    assert exc.value.details["missing"] == [2]

    with pytest.raises(studio.errors.StudioError) as exc:
        A.wire_text_for(
            "answers",
            {
                "answers": [
                    {"index": 1, "option_letters": ["Z"], "free_text": None},
                    {"index": 2, "option_letters": ["A"], "free_text": None},
                ]
            },
            questions=questions,
            grouped_answers_enabled=True,
        )
    assert exc.value.code == "invalid_decision"
    assert exc.value.details["unknown"] == ["Z"]


def test_grouped_answers_are_gated_and_golden_when_enabled(A, studio):
    """The grouped form is specified but off until S1/S2 (C14): a misaligned reply is unrecoverable."""
    questions = _parse_questions(
        studio,
        F.questions_text(
            stage=STAGE,
            questions=[
                ("First? ", ["A", "B"]),
                ("Second? (multi-select)", ["A", "B", "C"]),
                ("Third?", ["Something else"]),
            ],
        ),
    )
    payload = {
        "answers": [
            {"index": 1, "option_letters": ["B"], "free_text": None},
            {"index": 2, "option_letters": ["A", "C"], "free_text": None},
            {"index": 3, "option_letters": ["X"], "free_text": "Use the existing worker queue."},
        ]
    }
    with pytest.raises(studio.errors.StudioError) as exc:
        A.wire_text_for("answers", payload, questions=questions, grouped_answers_enabled=False)
    assert exc.value.code == "grouped_answers_unavailable"
    assert exc.value.details["reason"] == "s1_s2_unverified"

    pinned = json.loads((APP_ROOT / "docs" / "design" / "wire-text.json").read_text("utf-8"))
    text = A.wire_text_for("answers", payload, questions=questions, grouped_answers_enabled=True)
    assert text == pinned["golden"]["grouped_answers_example"]
    assert text == "Q1: B\nQ2: A, C\nQ3: Use the existing worker queue." + studio.constants.WIRE_GROUPED_ANSWER_SUFFIX


def test_the_verified_capability_enables_grouped_file_answers(world, A):
    assert world.settings.capabilities()["grouped_answers"] == {"available": True, "reason": None}


@pytest.mark.parametrize("indexes", [[1, 1], [1, 2], ["1"], [True], [None]])
def test_answer_indexes_must_identify_each_pending_question_once(A, studio, indexes):
    questions = _parse_questions(studio, _questions(single=True))
    with pytest.raises(studio.errors.StudioError):
        A.wire_text_for(
            "answers", {"answers": [{"index": i, "option_letters": ["A"]} for i in indexes]},
            questions=questions, grouped_answers_enabled=True,
        )


def test_multi_select_preserves_regular_choices_alongside_other(A, studio):
    questions = _parse_questions(
        studio, "## Q1: Checks? (select all that apply)\nA. Alpha\nC. Gamma\nX. Other\n[Answer]:\n"
    )
    assert A.wire_text_for(
        "answers", {"answers": [{"index": 1, "option_letters": ["A", "X", "C"],
                                "free_text": 'Custom, "quoted".\nSecond line.'}]},
        questions=questions, grouped_answers_enabled=True,
    ) == 'Alpha, Custom, "quoted".\nSecond line., Gamma'


@pytest.mark.parametrize("letters", [["A", "A"], "A"])
def test_duplicate_or_non_list_option_letters_are_refused(A, studio, letters):
    questions = _parse_questions(studio, "## Q1: Checks? (multi-select)\nA. Alpha\n[Answer]:\n")
    with pytest.raises(studio.errors.StudioError):
        A.wire_text_for("answers", {"answers": [{"index": 1, "option_letters": letters}]},
                        questions=questions, grouped_answers_enabled=True)


@pytest.mark.parametrize("change", ["question_file", "native_question", "host_unavailable"])
@pytest.mark.parametrize("repo_options", [{"questions": _questions(single=True)}])
def test_file_answer_rechecks_after_cursor_switch(world, A, monkeypatch, change):
    async def scenario():
        await bind(world)
        question = pick(await seed(world), "question")
        original = world.engine.switch_cursor_sync

        def switch(*args, **kwargs):
            readback = original(*args, **kwargs)
            if change == "question_file":
                path = world.builder.record(INTENT) / QUESTIONS_REL
                path.write_text(path.read_text().replace("SQLite", "Changed choice"))
            elif change == "native_question":
                monkeypatch.setattr(world.host, "pending_question_cards",
                                    lambda key: [{"ask_id": "native-wait", "questions": [{}]}])
            else:
                monkeypatch.setattr(world.host, "capabilities", lambda: {})
            return readback

        monkeypatch.setattr(world.engine, "switch_cursor_sync", switch)
        with pytest.raises(world.S.errors.StudioError) as exc:
            await world.broker.submit(
                question.action_id, captured=captured_of(question, A),
                payload={"decision": "answers", "answers": [{"index": 1, "option_letters": ["B"]}]},
                client_wire_text="SQLite", user="owner-1",
            )
        assert exc.value.code == {"question_file": "action_stale", "native_question": "slot_busy",
                                  "host_unavailable": "host_unavailable"}[change]
        assert (await world.broker.get(question.action_id)).delivery_id is None
    run(scenario())


# --------------------------------------------------------------------------- #
# 3. submit — the happy path and what it never does
# --------------------------------------------------------------------------- #


def test_submit_commits_a_delivering_record_and_hands_the_browser_the_send(world, A, C):
    async def scenario():
        await bind(world)
        cards = await seed(world)
        gate = pick(cards, "gate")
        receipt = await world.broker.submit(
            gate.action_id,
            captured=captured_of(gate, A),
            payload={"decision": "approve"},
            client_wire_text="Approve",
            user="owner-1",
        )
        assert receipt.status == "Delivering"
        assert receipt.lane == "human_lane"
        assert receipt.wire_text == "Approve"
        assert receipt.delivery_id.startswith("dl_")
        assert receipt.slot_key == SLOT
        assert receipt.lease_generation is not None
        assert receipt.host.method == "POST"
        assert receipt.host.path == C.HOST_CHAT_SEND_PATH
        assert receipt.host.body == {
            "message": "Approve",
            "slot": SLOT,
            "agent": "aidlc",
            "meta": {
                "studio_action_id": gate.action_id,
                "studio_delivery_id": receipt.delivery_id,
            },
        }

        rec = await world.broker.get(gate.action_id)
        assert rec.status == "Delivering"
        assert rec.boot_id == BOOT
        assert rec.presence_baseline is not None
        assert rec.cursor_readback is not None and rec.cursor_readback.ok
        assert rec.deadline_at == C.iso_from_epoch(
            world.clock.now() + C.DELIVERY_ACK_DEADLINE_SECS
        )
        assert rec.payload_digest and rec.wire_text == "Approve"

        # The backend sent nothing itself: the slot is untouched and the lease is held for the turn.
        assert world.fake_host.get_slot(SLOT).messages == []
        assert [lease.kind for lease in leases_held(world)] == ["execution"]

    run(scenario())


def test_the_backend_names_no_host_dispatch_symbol_anywhere_it_could_call_one(A):
    """Read as code, not as text: the two gateway generations disagree on every one of these
    signatures, so a decision routed through one would stop being delivered after an upgrade
    (architecture §7.0). Prose may name them; the module may not *reach* them."""
    import ast

    tree = ast.parse((APP_ROOT / "backend" / "studio" / "actions.py").read_text("utf-8"))
    forbidden = {"_run_chat", "spawn_guarded_turn", "get_or_create_slot", "steer", "link_slack"}
    reached: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden:
            reached.add(node.attr)
        if isinstance(node, ast.Name) and node.id in forbidden:
            reached.add(node.id)
    assert reached == set(), reached


def test_the_delivering_record_is_on_disk_before_the_receipt_is_returned(world, A, monkeypatch):
    """Simulate a crash immediately after the CAS: the durable record must already be there."""

    class Crash(RuntimeError):
        pass

    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")

        async def boom(*a, **k):
            raise Crash("gateway died after the CAS")

        monkeypatch.setattr(world.broker, "_record_transition", boom)
        with pytest.raises(Crash):
            await world.broker.submit(
                gate.action_id,
                captured=captured_of(gate, A),
                payload={"decision": "approve"},
                client_wire_text="Approve",
                user="owner-1",
            )
        return gate.action_id

    action_id = run(scenario())
    # Re-open the database file: nothing of the record may live in the process that crashed.
    world.store.close()
    reopened = world.S.storage.Storage(world.store.path, clock=world.clock, ids=world.ids)
    reopened.open()
    try:
        row = reopened.get("actions", action_id)
        assert row["status"] == "Delivering"
        assert row["delivery_id"] and row["wire_text"] == "Approve"
        assert row["boot_id"] == BOOT
        assert row["presence_baseline_json"] and row["cursor_readback_json"]
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("state_hash", "0" * 64),
        ("boundary_token", "1" * 64),
        ("question_digest", "2" * 64),
        ("stage_attempt", 9),
        ("evidence_digest", "3" * 64),
        ("is_active", False),
    ],
)
def test_compare_and_submit_refuses_on_each_captured_field_independently(world, A, studio, field, value):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        body = captured_of(gate, A, **{field: value})
        assert body[field] != getattr(gate.captured, field), field
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.submit(
                gate.action_id,
                captured=body,
                payload={"decision": "approve"},
                client_wire_text="Approve",
                user="owner-1",
            )
        assert exc.value.code == "action_stale"
        assert exc.value.details["fields"] == [field]
        assert exc.value.details["card"]["action_id"] == gate.action_id
        # Nothing was dispatched and no lease was taken.
        assert (await world.broker.get(gate.action_id)).status == "Queued"
        assert leases_held(world) == []

    run(scenario())


def test_null_versus_non_null_is_a_difference(world, A, studio):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        assert gate.captured.question_digest is not None
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.submit(
                gate.action_id,
                captured=captured_of(gate, A, question_digest=None),
                payload={"decision": "approve"},
                client_wire_text="Approve",
                user="owner-1",
            )
        assert exc.value.code == "action_stale"
        assert exc.value.details["fields"] == ["question_digest"]

    run(scenario())


def test_a_boundary_that_moved_on_disk_is_stale_even_when_the_client_agrees_with_the_row(
    world, A, studio
):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        body = captured_of(gate, A)
        world.builder.mutate_state(INTENT, lambda text: text.replace("Revision Count", "Revision  Count"))
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.submit(
                gate.action_id,
                captured=body,
                payload={"decision": "approve"},
                client_wire_text="Approve",
                user="owner-1",
            )
        assert exc.value.code == "action_stale"
        assert "state_hash" in exc.value.details["fields"]

    run(scenario())


def test_an_unstable_snapshot_is_never_submitted(world, A, studio, monkeypatch):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        real = world.projection.snapshot
        monkeypatch.setattr(
            world.projection,
            "snapshot",
            lambda *a, **k: dataclasses.replace(real(*a, **k), stable=False),
        )
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.submit(
                gate.action_id,
                captured=captured_of(gate, A),
                payload={"decision": "approve"},
                client_wire_text="Approve",
                user="owner-1",
            )
        assert exc.value.code == "unstable_read"
        assert leases_held(world) == []

    run(scenario())


def test_blocking_findings_refuse_the_decision_with_the_evidence(world, A, studio, monkeypatch):
    finding = studio.consistency.Finding(
        code="gate_without_audit_row",
        severity="blocking",
        message_key="finding.gate_without_audit_row",
        params={},
        evidence=(),
    )
    monkeypatch.setattr(world.consistency, "evaluate_intent", lambda *a, **k: [finding])

    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.submit(
                gate.action_id,
                captured=captured_of(gate, A),
                payload={"decision": "approve"},
                client_wire_text="Approve",
                user="owner-1",
            )
        assert exc.value.code == "state_inconsistent"
        assert exc.value.details["findings"][0]["code"] == "gate_without_audit_row"

    run(scenario())


def test_a_client_cannot_choose_the_text_that_is_sent(world, A, studio):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.submit(
                gate.action_id,
                captured=captured_of(gate, A),
                payload={"decision": "approve"},
                client_wire_text="Approve please",
                user="owner-1",
            )
        assert exc.value.code == "invalid_decision"
        assert exc.value.details["expected"] == "Approve"

    run(scenario())


def test_pause_blocks_a_gate_approval_not_only_run(world, A, studio):
    """FR-RUN-003/C29: pause prevents *future turns*, and a gate approval is a turn."""

    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        await world.binder.set_paused(REPO_ID, "default", INTENT, True)
        with pytest.raises(studio.errors.StudioError) as exc:
            await approve(world, A, rec=gate)
        assert exc.value.code == "intent_paused"
        assert leases_held(world) == []
        await world.binder.set_paused(REPO_ID, "default", INTENT, False)
        assert (await approve(world, A, rec=gate)).status == "Delivering"

    run(scenario())


def test_an_archived_intent_dispatches_nothing(world, A, studio):
    async def scenario():
        await bind(world)
        # Archiving is refused while a card is live, so the flag is set before any card exists.
        await world.binder.set_archived(REPO_ID, "default", INTENT, True)
        gate = await _row_in(world, "Queued")
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.submit(
                gate.action_id,
                captured=captured_of(gate, A),
                payload={"decision": "approve"},
                client_wire_text="Approve",
                user="owner-1",
            )
        assert exc.value.code == "intent_archived"

    run(scenario())


def test_an_open_breaker_blocks_run_but_leaves_a_gate_decision_alone(world, A, studio):
    async def scenario():
        await bind(world)
        world.store.insert(
            "breakers",
            {
                "key": f"{REPO_ID}:{INTENT}:transport.x",
                "count": 3,
                "opened_at": world.clock.iso(),
                "reason": "failure.transport",
                "fingerprint_class": "transport",
            },
        )
        snap = snapshot(world)
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.create_command("run", world.repo, "default", INTENT, snap=snap)
        assert exc.value.code == "breaker_open"

        gate = pick(await seed(world), "gate")
        receipt = await approve(world, A, rec=gate)
        assert receipt.status == "Delivering", "PRD §11.4 disables Keep moving, not the gate"

    run(scenario())


@pytest.mark.parametrize("repo_options", [{"revising": 3}])
def test_accept_as_is_appears_only_from_the_fourth_attempt(world, A, studio):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        assert gate.captured.stage_attempt == 4
        card = await world.broker.card(gate, snapshot(world))
        assert "accept_as_is" in [d["decision"] for d in card["decisions"]]
        receipt = await world.broker.submit(
            gate.action_id,
            captured=captured_of(gate, A),
            payload={"decision": "accept_as_is"},
            client_wire_text="Accept as-is",
            user="owner-1",
        )
        assert receipt.wire_text == "Accept as-is"

    run(scenario())


def test_accept_as_is_is_refused_before_the_fourth_attempt(world, A, studio):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        assert gate.captured.stage_attempt == 1
        card = await world.broker.card(gate, snapshot(world))
        assert "accept_as_is" not in [d["decision"] for d in card["decisions"]]
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.submit(
                gate.action_id,
                captured=captured_of(gate, A),
                payload={"decision": "accept_as_is"},
                client_wire_text="Accept as-is",
                user="owner-1",
            )
        assert exc.value.code == "invalid_decision"
        assert exc.value.details["min_attempt"] == 4

    run(scenario())


def test_a_multi_question_group_is_refused_while_the_capability_is_off(world, A, studio, monkeypatch):
    monkeypatch.setattr(studio.constants, "GROUPED_ANSWERS_VERIFIED", False)
    async def scenario():
        await bind(world)
        question = pick(await seed(world), "question")
        snap = snapshot(world)
        pending = [q for q in snap.questions.questions if not q.answered]
        assert len(pending) > 1
        payload = {
            "answers": [
                {
                    "index": q.index,
                    "option_letters": [q.options[0].letter] if q.options else [],
                    "free_text": None if q.options and not q.options[0].is_other else "text",
                }
                for q in pending
            ]
        }
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.submit(
                question.action_id,
                captured=captured_of(question, A),
                payload={"decision": "answers", **payload},
                client_wire_text="whatever",
                user="owner-1",
            )
        assert exc.value.code == "grouped_answers_unavailable"

    run(scenario())


def test_studio_writes_nothing_of_ai_dlcs_while_submitting(world, A):
    """The product invariant: the engine owns lifecycle, gates, questions, artifacts and the audit.

    The one file Studio's dispatch does move is the repository cursor, and it moves it the only way it
    is allowed to — through the engine's own allowlisted verb (§3.3).
    """
    record = world.builder.record(INTENT)
    watched = [record / "aidlc-state.md", *sorted((record / "audit").glob("*.md")),
               record / QUESTIONS_REL, record / f"inception/{STAGE}/requirements.md"]

    def digests() -> dict[str, str]:
        return {str(path): world.S.security.sha256_file(path) or "" for path in watched}

    async def scenario():
        await bind(world)
        before = digests()
        assert all(before.values()), "the fixture must really have written those files"
        await approve(world, A)
        assert digests() == before

    run(scenario())


@pytest.mark.parametrize("repo_options", [{"runnable": True}])
def test_every_command_decision_sends_its_own_pinned_wire_text(world, A, C):
    async def scenario():
        await bind(world)
        snap = snapshot(world)
        for kind, expected in (
            ("run", C.WIRE_RUN),
            ("prepare_commit", C.WIRE_PREPARE_COMMIT),
        ):
            command = await world.broker.create_command(
                kind, world.repo, "default", INTENT, snap=snap
            )
            assert command.risk_class == "human_lane"
            receipt = await world.broker.submit(
                command.action_id,
                captured=captured_of(command, A),
                payload={"decision": kind},
                client_wire_text=expected,
                user="owner-1",
            )
            assert receipt.wire_text == expected
            assert receipt.host.body["message"] == expected
            # Release the repository for the next command in the loop.
            proven, _ = await world.broker.record_delivery(
                command.action_id,
                delivery_id=receipt.delivery_id,
                outcome="not_delivered",
                http_status=409,
                receipt={"error": "slot agent mismatch"},
            )
            assert proven.status == "NotDelivered"

    run(scenario())


@pytest.mark.parametrize(
    "repo_options",
    [{"questions": F.questions_text(stage=STAGE, questions=[("Which store?", ["Postgres", "SQLite"])])}],
)
def test_a_single_pending_question_is_answered_with_the_option_label(world, A):
    """The one answer mapping research 05 §5.1 verified: one reply, one label, no letters."""

    async def scenario():
        await bind(world)
        card = pick(await seed(world), "question")
        assert card.captured.question_digest is not None
        receipt = await world.broker.submit(
            card.action_id,
            captured=captured_of(card, A),
            payload={
                "decision": "answers",
                "answers": [{"index": 1, "option_letters": ["B"], "free_text": None}],
            },
            client_wire_text="SQLite",
            user="owner-1",
        )
        assert receipt.wire_text == "SQLite"
        assert receipt.host.body["message"] == "SQLite"
        rec = await world.broker.get(card.action_id)
        assert rec.status == "Delivering"
        assert rec.human_text is None, "picking an option is not prose"

    run(scenario())


def test_a_refused_force_stop_needs_no_disk_evidence(world, A):
    """The host-control lane sends nothing to the model, so there is no disk half to prove."""

    async def scenario():
        await bind(world)
        receipt = await world.broker.create_force_stop(
            world.repo, "default", INTENT, snap=snapshot(world)
        )
        rec = await world.broker.get(receipt.action_id)
        assert rec.presence_baseline is None
        after, _ = await world.broker.record_delivery(
            rec.action_id,
            delivery_id=receipt.delivery_id,
            outcome="not_delivered",
            http_status=404,
            receipt={"error": "stop refused"},
        )
        assert after.status == "NotDelivered"
        assert after.evidence["checks"] == {
            "row_absent": True,
            "slot_idle": True,
            "disk_baseline_unchanged": True,
            "boot_unchanged": True,
        }

    run(scenario())


def test_a_force_stop_receipt_resolves_through_delivered(world, A):
    async def scenario():
        await bind(world)
        receipt = await world.broker.create_force_stop(
            world.repo, "default", INTENT, snap=snapshot(world)
        )
        after, idempotent = await world.broker.record_delivery(
            receipt.action_id,
            delivery_id=receipt.delivery_id,
            outcome="delivered",
            http_status=200,
            receipt={"ok": True, "info": "not running"},
        )
        assert (after.status, idempotent) == ("Processing", False)
        assert after.evidence["receipt"]["info"] == "not running"
        assert leases_held(world) == [], "the host-control lane never took a lease"

    run(scenario())


def test_a_paused_intent_refuses_a_new_command_card(world, studio):
    async def scenario():
        await world.binder.set_paused(REPO_ID, "default", INTENT, True)
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.create_command(
                "run", world.repo, "default", INTENT, snap=snapshot(world)
            )
        assert exc.value.code == "intent_paused"

    run(scenario())


def test_revision_cards_stay_out_of_the_queue_unless_asked_for(world):
    async def scenario():
        rec = await _row_in(world, "Queued")
        world.store.cas_update(
            "actions", rec.action_id, rec.status_generation, {"type": "revision"}
        )
        assert [r.type for r in await world.broker.list_live(repo_id=REPO_ID)] == []
        assert [
            r.type for r in await world.broker.list_live(repo_id=REPO_ID, include_revision=True)
        ] == ["revision"]

    run(scenario())


def test_a_decision_the_card_does_not_offer_and_a_wrong_lane_are_both_refused(world, A, studio):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        for payload in ({"decision": "answers"}, {"decision": "acknowledge"}, {"decision": "force_stop"}):
            with pytest.raises(studio.errors.StudioError) as exc:
                await world.broker.submit(
                    gate.action_id,
                    captured=captured_of(gate, A),
                    payload=payload,
                    client_wire_text="Approve",
                    user="owner-1",
                )
            assert exc.value.code == "invalid_decision"

    run(scenario())


# --------------------------------------------------------------------------- #
# 4. submit — session preconditions and the lease
# --------------------------------------------------------------------------- #


def test_an_unbound_intent_refuses_before_anything_else_happens(world, A, studio):
    async def scenario():
        gate = pick(await seed(world), "gate")
        with pytest.raises(studio.errors.StudioError) as exc:
            await approve(world, A, rec=gate)
        assert exc.value.code == "session_unbound"
        assert leases_held(world) == []

    run(scenario())


def test_a_slot_that_moved_or_vanished_refuses(world, A, studio):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        world.fake_host.get_slot(SLOT).project = "/somewhere/else"
        with pytest.raises(studio.errors.StudioError) as exc:
            await approve(world, A, rec=gate)
        assert exc.value.code == "slot_mismatch"

        world.fake_host._slots.pop(SLOT)
        with pytest.raises(studio.errors.StudioError) as exc:
            await approve(world, A, rec=gate)
        assert exc.value.code == "slot_mismatch"
        assert exc.value.details["present"] is False

    run(scenario())


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda slot: setattr(slot, "running", True), "running"),
        (lambda slot: setattr(slot, "_in_stage_execution", True), "stage_execution"),
        (lambda slot: setattr(slot, "_stop_state", "soft_pending"), "stopping"),
        (lambda slot: setattr(slot, "queue_depth", 2), "queued"),
        (
            lambda slot: slot._approval_futures.update(
                {"req-1": SimpleNamespace(done=lambda: False)}
            ),
            "approval_pending",
        ),
    ],
)
def test_every_busy_predicate_refuses_the_dispatch(world, A, studio, mutate, reason):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        mutate(world.fake_host.get_slot(SLOT))
        with pytest.raises(studio.errors.StudioError) as exc:
            await approve(world, A, rec=gate)
        assert exc.value.code == "slot_busy"
        assert reason in exc.value.details["reasons"]
        assert leases_held(world) == []

    run(scenario())


def test_a_second_submit_for_the_same_intent_answers_repo_busy(world, A, studio):
    """Two browser tabs, one repository. The lease is held for *this* intent, so the consistency
    engine raises no `lease_conflict` and the refusal is the actionable one."""

    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        uuid = snapshot(world).row.uuid
        world.store.lease_acquire(
            "execution",
            IDENTITY,
            {"repo_id": REPO_ID, "action_id": "a_other", "intent_uuid": uuid},
        )
        with pytest.raises(studio.errors.StudioError) as exc:
            await approve(world, A, rec=gate)
        assert exc.value.code == "repo_busy"
        assert exc.value.details["owner"]["action_id"] == "a_other"
        assert (await world.broker.get(gate.action_id)).status == "Queued"

    run(scenario())


@pytest.mark.parametrize("kind", ["execution", "admin"])
def test_other_intent_or_admin_lease_is_busy_not_file_disagreement(world, A, studio, kind):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        fields = {"repo_id": REPO_ID}
        if kind == "execution":
            fields.update(action_id="a_other", intent_uuid="01a00000-0000-7000-8000-000000000002")
        else:
            fields.update(operation_type="install", transaction_id="tx_other")
        held = world.store.lease_acquire(kind, IDENTITY, fields)
        with pytest.raises(studio.errors.StudioError) as exc:
            await approve(world, A, rec=gate)
        assert exc.value.code == "repo_busy"
        owner = exc.value.details["owner"]
        assert owner["action_id"] == "a_other" if kind == "execution" else owner["operation_type"] == "install"
        assert leases_held(world)[0].generation == held.generation
        assert (await world.broker.get(gate.action_id)).delivery_id is None
    run(scenario())


@pytest.mark.parametrize("repo_options", [{"extra_intent": True}])
def test_the_cursor_is_switched_and_read_back_before_the_record_is_committed(world, A):
    """FR-SES-006/C22: the conductor resolves the record from the cursor, so asking is not evidence."""
    cursor = world.root / "aidlc" / "spaces" / "default" / "intents" / "active-intent"

    async def scenario():
        assert cursor.read_text().strip() == OTHER
        await bind(world)
        gate = pick(await seed(world), "gate")
        assert gate.captured.is_active is False

        seen: dict[str, str] = {}
        original = world.store.deliver_under_lease

        def spy(*args, **kwargs):
            seen["cursor_at_cas"] = cursor.read_text().strip()
            return original(*args, **kwargs)

        world.store.deliver_under_lease = spy  # type: ignore[assignment]
        try:
            receipt = await world.broker.submit(
                gate.action_id,
                captured=captured_of(gate, A),
                payload={"decision": "approve"},
                client_wire_text="Approve",
                user="owner-1",
            )
        finally:
            world.store.deliver_under_lease = original  # type: ignore[assignment]

        assert seen["cursor_at_cas"] == INTENT, "the switch must precede the durable record"
        assert cursor.read_text().strip() == INTENT
        rec = await world.broker.get(receipt.action_id)
        assert rec.cursor_readback is not None
        assert rec.cursor_readback.ok is True
        assert rec.cursor_readback.dir_name == INTENT
        assert rec.cursor_readback.state_sha256 == gate.captured.state_hash

    run(scenario())


def test_a_refused_read_back_releases_the_lease_and_never_dispatches(world, A, studio, monkeypatch):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        readback = studio.engine.CursorReadback(
            ok=False,
            space="default",
            dir_name="somewhere-else",
            uuid=None,
            state_sha256=None,
            mismatch=("active_intent",),
        )
        monkeypatch.setattr(world.engine, "switch_cursor_sync", lambda *a, **k: readback)
        with pytest.raises(studio.errors.StudioError) as exc:
            await approve(world, A, rec=gate)
        assert exc.value.code == "cursor_mismatch"
        assert exc.value.details["mismatch"] == ["active_intent"]
        assert (await world.broker.get(gate.action_id)).status == "Queued"
        assert leases_held(world) == [], "an exception after acquire must release the grant"

    run(scenario())


def test_a_switch_that_changed_the_state_file_is_a_cursor_mismatch(world, A, studio, monkeypatch):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        readback = studio.engine.CursorReadback(
            ok=True,
            space="default",
            dir_name=INTENT,
            uuid=None,
            state_sha256="f" * 64,
            mismatch=(),
        )
        monkeypatch.setattr(world.engine, "switch_cursor_sync", lambda *a, **k: readback)
        with pytest.raises(studio.errors.StudioError) as exc:
            await approve(world, A, rec=gate)
        assert exc.value.code == "cursor_mismatch"
        assert exc.value.details["mismatch"] == ["state_changed"]
        assert leases_held(world) == []

    run(scenario())


def test_any_failure_after_the_lease_is_taken_releases_it(world, A, monkeypatch):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")

        def boom(*a, **k):
            raise RuntimeError("the audit shard disappeared mid-read")

        monkeypatch.setattr(world.reader, "presence_baseline", boom)
        with pytest.raises(RuntimeError):
            await approve(world, A, rec=gate)
        assert leases_held(world) == []
        assert (await world.broker.get(gate.action_id)).status == "Queued"

    run(scenario())


def test_a_lease_that_moved_between_acquire_and_the_cas_refuses_lease_lost(world, A, studio, monkeypatch):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        real = world.reader.presence_baseline

        def steal(*args, **kwargs):
            # Somebody reclaimed the lease while the cursor switch was running.
            world.store.lease_release("execution", IDENTITY, world.store.lease_get("execution", IDENTITY).generation)
            world.store.lease_acquire("execution", IDENTITY, {"repo_id": REPO_ID, "action_id": "a_other"})
            return real(*args, **kwargs)

        monkeypatch.setattr(world.reader, "presence_baseline", steal)
        with pytest.raises(studio.errors.StudioError) as exc:
            await approve(world, A, rec=gate)
        assert exc.value.code == "lease_lost"
        assert (await world.broker.get(gate.action_id)).status == "Queued"

    run(scenario())


def test_the_paused_refusal_precedes_the_staleness_refusal(world, A, studio):
    """§1.12 step order: 1b (lane, pause, archive, breaker) before 3 (compare-and-submit)."""

    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        await world.binder.set_paused(REPO_ID, "default", INTENT, True)
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.submit(
                gate.action_id,
                captured=captured_of(gate, A, state_hash="0" * 64),
                payload={"decision": "approve"},
                client_wire_text="Approve",
                user="owner-1",
            )
        assert exc.value.code == "intent_paused"

    run(scenario())


def test_staleness_is_refused_before_the_session_preconditions(world, A, studio):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        world.fake_host.get_slot(SLOT).running = True
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.submit(
                gate.action_id,
                captured=captured_of(gate, A, state_hash="0" * 64),
                payload={"decision": "approve"},
                client_wire_text="Approve",
                user="owner-1",
            )
        assert exc.value.code == "action_stale"

    run(scenario())


def test_a_row_that_is_not_submittable_is_refused_first(world, A, studio):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        await approve(world, A, rec=gate)
        with pytest.raises(studio.errors.StudioError) as exc:
            await approve(world, A, rec=gate)
        assert exc.value.code == "action_not_submittable"
        assert exc.value.details["status"] == "Delivering"

    run(scenario())


# --------------------------------------------------------------------------- #
# 5. delivery reporting
# --------------------------------------------------------------------------- #


def test_a_delivered_receipt_moves_the_action_into_processing(world, A):
    async def scenario():
        rec, receipt = await delivering(world, A)
        after, idempotent = await world.broker.record_delivery(
            rec.action_id,
            delivery_id=receipt.delivery_id,
            outcome="delivered",
            http_status=200,
            receipt={"ok": True, "slot": SLOT, "mid": "m1"},
        )
        assert idempotent is False
        assert after.status == "Processing"
        assert after.delivered_at
        assert after.evidence["receipt"]["mid"] == "m1"
        assert after.deadline_at and after.deadline_at > after.delivered_at
        transitions = world.store.select("action_transitions", {"action_id": rec.action_id})
        assert [(t["from_status"], t["to_status"]) for t in transitions][-2:] == [
            ("Delivering", "Delivered"),
            ("Delivered", "Processing"),
        ]

    run(scenario())


def test_a_queued_receipt_waits_in_delivered(world, A):
    async def scenario():
        rec, receipt = await delivering(world, A)
        after, _ = await world.broker.record_delivery(
            rec.action_id,
            delivery_id=receipt.delivery_id,
            outcome="delivered",
            http_status=200,
            receipt={"ok": True, "queued": True},
        )
        assert after.status == "Delivered", "the processing clock starts when the turn starts (P22)"
        assert after.evidence["queued_at"]

    run(scenario())


def test_a_delivered_report_without_the_hosts_ok_is_refused(world, A, studio):
    async def scenario():
        rec, receipt = await delivering(world, A)
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.record_delivery(
                rec.action_id,
                delivery_id=receipt.delivery_id,
                outcome="delivered",
                http_status=200,
                receipt={"queued": True},
            )
        assert exc.value.code == "delivery_ack_invalid"
        assert (await world.broker.get(rec.action_id)).status == "Delivering"

    run(scenario())


def test_an_uncertain_report_keeps_the_lease_for_reconciliation(world, A):
    async def scenario():
        rec, receipt = await delivering(world, A)
        after, _ = await world.broker.record_delivery(
            rec.action_id,
            delivery_id=receipt.delivery_id,
            outcome="uncertain",
            http_status=None,
            receipt=None,
        )
        assert after.status == "DeliveryUncertain"
        assert [lease.kind for lease in leases_held(world)] == ["execution"]

    run(scenario())


def test_not_delivered_is_accepted_only_with_the_backends_own_proof(world, A):
    async def scenario():
        rec, receipt = await delivering(world, A)
        after, idempotent = await world.broker.record_delivery(
            rec.action_id,
            delivery_id=receipt.delivery_id,
            outcome="not_delivered",
            http_status=409,
            receipt={"error": "slot agent mismatch", "code": "slot_agent_mismatch"},
        )
        assert idempotent is False
        assert after.status == "NotDelivered"
        assert after.evidence["checks"] == {
            "row_absent": True,
            "slot_idle": True,
            "disk_baseline_unchanged": True,
            "boot_unchanged": True,
        }
        assert leases_held(world) == [], "a proven non-delivery gives the repository back"

    run(scenario())


@pytest.mark.parametrize("broken", ["row_absent", "slot_idle", "disk_baseline_unchanged"])
def test_a_broken_proof_yields_delivery_uncertain_and_never_not_delivered(world, A, studio, broken):
    async def scenario():
        rec, receipt = await delivering(world, A)
        if broken == "row_absent":
            world.fake_host.append_user_row(SLOT, "Approve", TS)
        elif broken == "slot_idle":
            world.fake_host.set_running(SLOT, True)
        else:
            world.builder.append_audit(INTENT, F.audit_block("HUMAN_TURN", F._plus(TS, 5)))

        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.record_delivery(
                rec.action_id,
                delivery_id=receipt.delivery_id,
                outcome="not_delivered",
                http_status=409,
                receipt={"error": "slot agent mismatch"},
            )
        assert exc.value.code == "not_delivered_unproven"
        assert exc.value.details["checks"][broken] is False
        after = await world.broker.get(rec.action_id)
        assert after.status == "DeliveryUncertain", "the host may have enqueued it after all"
        assert after.evidence["receipt"]["error"] == "slot agent mismatch"

    run(scenario())


def test_a_not_delivered_report_without_an_authoritative_receipt_is_unproven(world, A, studio):
    async def scenario():
        rec, receipt = await delivering(world, A)
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.record_delivery(
                rec.action_id,
                delivery_id=receipt.delivery_id,
                outcome="not_delivered",
                http_status=500,
                receipt={"error": "gateway exploded"},
            )
        assert exc.value.code == "not_delivered_unproven"
        assert exc.value.details["reason"] == "receipt_not_authoritative"
        # The refusal still says what was and was not observed, so the user can act on it (§2.4).
        assert set(exc.value.details["checks"]) == {
            "row_absent",
            "slot_idle",
            "disk_baseline_unchanged",
            "boot_unchanged",
        }
        assert (await world.broker.get(rec.action_id)).status == "DeliveryUncertain"

    run(scenario())


def test_a_slot_missing_preflight_code_is_believed_only_when_the_slot_is_gone(world, A):
    async def scenario():
        rec, receipt = await delivering(world, A)
        world.fake_host._slots.pop(SLOT)
        after, _ = await world.broker.record_delivery(
            rec.action_id,
            delivery_id=receipt.delivery_id,
            outcome="not_delivered",
            http_status=None,
            receipt={"ok": False, "code": "slot_missing"},
        )
        assert after.status == "NotDelivered"
        assert after.evidence["client_preflight"] == "slot_missing"
        assert after.evidence["checks"]["slot_idle"] is True

    run(scenario())


def test_a_preflight_code_the_backend_can_disprove_is_refused(world, A, studio):
    """The browser says the slot was missing, ``HostBridge`` says it is right there: not proven."""

    async def scenario():
        rec, receipt = await delivering(world, A)
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.record_delivery(
                rec.action_id,
                delivery_id=receipt.delivery_id,
                outcome="not_delivered",
                http_status=None,
                receipt={"ok": False, "code": "slot_missing"},
            )
        assert exc.value.code == "not_delivered_unproven"
        assert exc.value.details["checks"]["slot_idle"] is False
        assert (await world.broker.get(rec.action_id)).status == "DeliveryUncertain"

    run(scenario())


def test_a_restart_between_the_send_and_the_ack_can_never_prove_non_delivery(world, A, studio):
    """review R02: the host's transcript is process memory, so a restart looks like 'never sent'."""

    async def scenario():
        rec, receipt = await delivering(world, A)
        restarted = world.S.actions.HumanActionBroker(
            world.store,
            world.projection,
            world.reader,
            world.consistency,
            world.scheduler,
            world.binder,
            world.engine,
            world.host,
            world.events,
            world.activity,
            world.notifications,
            world.settings,
            world.clock,
            world.ids,
            "boot-test-0002",
        )
        world.fake_host._slots.clear()
        with pytest.raises(studio.errors.StudioError) as exc:
            await restarted.record_delivery(
                rec.action_id,
                delivery_id=receipt.delivery_id,
                outcome="not_delivered",
                http_status=409,
                receipt={"error": "slot agent mismatch"},
            )
        assert exc.value.code == "not_delivered_unproven"
        assert exc.value.details["checks"]["boot_unchanged"] is False
        assert (await restarted.get(rec.action_id)).status == "DeliveryUncertain"

    run(scenario())


def test_delivery_reports_are_idempotent_and_a_conflicting_one_is_refused(world, A, studio):
    async def scenario():
        rec, receipt = await delivering(world, A)
        first, idem1 = await world.broker.record_delivery(
            rec.action_id,
            delivery_id=receipt.delivery_id,
            outcome="delivered",
            http_status=200,
            receipt={"ok": True},
        )
        again, idem2 = await world.broker.record_delivery(
            rec.action_id,
            delivery_id=receipt.delivery_id,
            outcome="delivered",
            http_status=200,
            receipt={"ok": True},
        )
        assert (idem1, idem2) == (False, True)
        assert again.status == first.status == "Processing"
        assert again.status_generation == first.status_generation

        with pytest.raises(studio.errors.IllegalTransition):
            await world.broker.record_delivery(
                rec.action_id,
                delivery_id=receipt.delivery_id,
                outcome="uncertain",
                http_status=None,
                receipt=None,
            )
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.record_delivery(
                rec.action_id,
                delivery_id="dl_not_this_one",
                outcome="delivered",
                http_status=200,
                receipt={"ok": True},
            )
        assert exc.value.code == "delivery_ack_invalid"

    run(scenario())


def test_a_late_delivered_report_marks_the_decision_confirmed_and_blocks_replay(world, A, studio):
    async def scenario():
        rec, receipt = await delivering(world, A)
        uncertain, _ = await world.broker.record_delivery(
            rec.action_id,
            delivery_id=receipt.delivery_id,
            outcome="uncertain",
            http_status=None,
            receipt=None,
        )
        after, idempotent = await world.broker.record_delivery(
            uncertain.action_id,
            delivery_id=receipt.delivery_id,
            outcome="delivered",
            http_status=200,
            receipt={"ok": True, "mid": "late"},
        )
        assert idempotent is False
        assert after.status == "DeliveryUncertain", "a late report records evidence, not a status"
        assert after.evidence["delivery_confirmed"] is True

        reconciling = await world.broker.transition(
            after.action_id,
            "ReconciliationRequired",
            expected_generation=after.status_generation,
            reason="ack deadline",
            evidence={},
        )
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.resolve(
                reconciling.action_id,
                decision="resubmit",
                payload={"acknowledged_evidence_sha256": "whatever"},
                user="owner-1",
            )
        assert exc.value.code == "not_delivered_unproven"

    run(scenario())


# --------------------------------------------------------------------------- #
# 6. resubmit, mark_not_delivered, retry, cancel
# --------------------------------------------------------------------------- #


async def _reconciliation_required(world, A):
    rec, receipt = await delivering(world, A)
    uncertain, _ = await world.broker.record_delivery(
        rec.action_id,
        delivery_id=receipt.delivery_id,
        outcome="uncertain",
        http_status=None,
        receipt=None,
    )
    return await world.broker.transition(
        uncertain.action_id,
        "ReconciliationRequired",
        expected_generation=uncertain.status_generation,
        reason="ack deadline passed",
        evidence={},
    )


def test_resubmit_needs_the_evidence_hash_the_ui_displayed(world, A, studio):
    async def scenario():
        rec = await _reconciliation_required(world, A)
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.resolve(
                rec.action_id,
                decision="resubmit",
                payload={"acknowledged_evidence_sha256": "0" * 64},
                user="owner-1",
            )
        assert exc.value.code == "action_stale"
        assert exc.value.details["card"]["acknowledged_evidence_sha256"] == exc.value.details["expected"]

    run(scenario())


def test_resubmit_supersedes_the_old_row_and_keeps_one_live_card_per_boundary(world, A):
    async def scenario():
        rec = await _reconciliation_required(world, A)
        card = await world.broker.card(rec, None)
        old, created = await world.broker.resolve(
            rec.action_id,
            decision="resubmit",
            payload={"acknowledged_evidence_sha256": card["acknowledged_evidence_sha256"]},
            user="owner-1",
        )
        assert old.status == "Failed"
        assert old.resolution["reason"] == "superseded_by_resubmit"
        assert created is not None and created.action_id != old.action_id
        assert created.status == "Queued"
        assert created.dedupe_key == old.dedupe_key, "the same boundary keeps its key"
        assert created.evidence["superseded_action_id"] == old.action_id
        live = [
            r
            for r in await world.broker.list_live(repo_id=REPO_ID, intent_dir=INTENT)
            if r.dedupe_key == old.dedupe_key and r.status != "Failed"
        ]
        assert [r.action_id for r in live] == [created.action_id]

    run(scenario())


def test_resubmit_and_mark_not_delivered_only_exist_in_reconciliation_required(world, A, studio):
    async def scenario():
        rec, receipt = await delivering(world, A)
        uncertain, _ = await world.broker.record_delivery(
            rec.action_id,
            delivery_id=receipt.delivery_id,
            outcome="uncertain",
            http_status=None,
            receipt=None,
        )
        for decision in ("resubmit", "mark_not_delivered"):
            with pytest.raises(studio.errors.IllegalTransition) as exc:
                await world.broker.resolve(
                    uncertain.action_id, decision=decision, payload={}, user="owner-1"
                )
            assert exc.value.details["from"] == "DeliveryUncertain"

    run(scenario())


def test_mark_not_delivered_needs_a_receipt_or_the_reconcilers_proof(world, A, studio):
    async def scenario():
        rec = await _reconciliation_required(world, A)
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.resolve(
                rec.action_id, decision="mark_not_delivered", payload={}, user="owner-1"
            )
        assert exc.value.code == "not_delivered_unproven"

        with_receipt, _ = await world.broker.resolve(
            rec.action_id,
            decision="mark_not_delivered",
            payload={"evidence": {"http_status": 409, "receipt": {"error": "slot agent mismatch"}}},
            user="owner-1",
        )
        assert with_receipt.status == "NotDelivered"
        assert leases_held(world) == []

    run(scenario())


def test_mark_not_delivered_accepts_the_reconcilers_four_proofs(world, A):
    async def scenario():
        rec = await _reconciliation_required(world, A)
        await world.broker._patch_evidence(
            rec,
            {
                "transcript_row_absent": True,
                "slot_ran_since": False,
                "disk_baseline_unchanged": True,
                "boot_id_unchanged": True,
            },
        )
        after, _ = await world.broker.resolve(
            rec.action_id, decision="mark_not_delivered", payload={}, user="owner-1"
        )
        assert after.status == "NotDelivered"

    run(scenario())


def test_retry_requeues_a_proven_non_delivery_and_refuses_a_confirmed_one(world, A, studio):
    async def scenario():
        rec, receipt = await delivering(world, A)
        proven, _ = await world.broker.record_delivery(
            rec.action_id,
            delivery_id=receipt.delivery_id,
            outcome="not_delivered",
            http_status=409,
            receipt={"error": "slot agent mismatch"},
        )
        requeued = await world.broker.retry(proven.action_id, user="owner-1")
        assert requeued.status == "Queued"
        assert requeued.action_id == proven.action_id, "at-most-once is intact: nothing was enqueued"

        confirmed = await world.broker._patch_evidence(requeued, {"delivery_confirmed": True})
        await world.broker.transition(
            confirmed.action_id,
            "Delivering",
            expected_generation=confirmed.status_generation,
            reason="pretend",
            evidence={},
        )
        moved = await world.broker.get(confirmed.action_id)
        await world.broker.transition(
            moved.action_id,
            "NotDelivered",
            expected_generation=moved.status_generation,
            reason="pretend",
            evidence={},
        )
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.retry(moved.action_id, user="owner-1")
        assert exc.value.code == "retry_not_allowed"

    run(scenario())


@pytest.mark.parametrize("repo_options", [{"runnable": True}])
def test_retry_of_a_failed_run_creates_a_new_action_without_a_dedupe_collision(world, A):
    async def scenario():
        await bind(world)
        snap = snapshot(world)
        command = await world.broker.create_command("run", world.repo, "default", INTENT, snap=snap)
        again = await world.broker.create_command("run", world.repo, "default", INTENT, snap=snap)
        assert again.action_id == command.action_id, "one live command per boundary"

        moved = command
        for status in ("Delivering", "Delivered", "Processing"):
            moved = await world.broker.transition(
                moved.action_id,
                status,
                expected_generation=moved.status_generation,
                reason="drive to Processing",
                evidence={},
            )
        failed, opened = await world.broker.record_failure(
            moved,
            fingerprint="validation.unknown_intent:abc123",
            fingerprint_class="validation",
            summary="failure.validation",
            evidence={"stderr": "Unknown intent"},
        )
        assert failed.status == "Failed" and opened is True

        retried = await world.broker.retry(failed.action_id, user="owner-1")
        assert retried.action_id != failed.action_id
        assert retried.status == "Queued"
        assert retried.dedupe_key == failed.dedupe_key
        assert (await world.broker.get(failed.action_id)).evidence["superseded"] is True
        # The breaker the failure opened was reset by the retry, so the new run is dispatchable.
        assert world.store.get("breakers", f"{REPO_ID}:{INTENT}:validation.unknown_intent:abc123")[
            "opened_at"
        ] is None

    run(scenario())


def test_cancel_is_allowed_for_commands_and_refused_for_a_live_derived_card(world, A, studio):
    async def scenario():
        await bind(world)
        snap = snapshot(world)
        command = await world.broker.create_command("prepare_commit", world.repo, "default", INTENT, snap=snap)
        cancelled = await world.broker.cancel(command.action_id, user="owner-1")
        assert cancelled.status == "Cancelled"

        gate = pick(await seed(world), "gate")
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.cancel(gate.action_id, user="owner-1")
        assert exc.value.code == "cancel_not_safe"

        rec, receipt = await delivering(world, A)
        proven, _ = await world.broker.record_delivery(
            rec.action_id,
            delivery_id=receipt.delivery_id,
            outcome="not_delivered",
            http_status=409,
            receipt={"error": "slot agent mismatch"},
        )
        dismissed = await world.broker.cancel(proven.action_id, user="owner-1")
        assert dismissed.status == "Cancelled"

    run(scenario())


# --------------------------------------------------------------------------- #
# 7. force stop, studio-only decisions
# --------------------------------------------------------------------------- #


def test_force_stop_commits_delivering_with_the_stop_call_and_no_lease(world, A):
    async def scenario():
        await bind(world)
        receipt = await world.broker.create_force_stop(
            world.repo, "default", INTENT, snap=snapshot(world)
        )
        assert receipt.lane == "host_control"
        assert receipt.wire_text is None
        assert receipt.lease_generation is None
        assert receipt.host.path == f"/api/chat/slots/{SLOT}/stop"
        assert receipt.host.body == {}
        rec = await world.broker.get(receipt.action_id)
        assert rec.status == "Delivering"
        assert rec.boot_id == BOOT
        assert rec.presence_baseline is None, "nothing was sent to the model"
        assert rec.cursor_readback is None
        assert leases_held(world) == []
        again = await world.broker.create_force_stop(
            world.repo, "default", INTENT, snap=snapshot(world)
        )
        assert again.action_id == receipt.action_id and again.delivery_id == receipt.delivery_id

    run(scenario())


def test_force_stop_without_a_bound_slot_is_refused(world, studio):
    async def scenario():
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.create_force_stop(
                world.repo, "default", INTENT, snap=snapshot(world)
            )
        assert exc.value.code == "session_unbound"

    run(scenario())


@pytest.mark.parametrize("repo_options", [{"extra_intent": True}])
def test_pick_intent_switches_the_cursor_under_an_admin_lease_and_never_sends_a_prompt(world, studio):
    cursor = world.root / "aidlc" / "spaces" / "default" / "intents" / "active-intent"

    async def scenario():
        cards = await seed(world, OTHER)
        # The cursor names OTHER, so INTENT derives a `missing_input` card only when the cursor is
        # dangling; force the case the card exists for by pointing the cursor at nothing.
        cursor.write_text("no-such-intent\n")
        cards = await seed(world)
        missing = pick(cards, "missing_input")
        assert "pick_intent" in missing.card_meta["decisions"]

        updated, created = await world.broker.resolve(
            missing.action_id,
            decision="pick_intent",
            payload={"intent_dir": INTENT},
            user="owner-1",
        )
        assert created is None
        assert cursor.read_text().strip() == INTENT
        assert updated.status == "Cancelled"
        assert updated.resolution["reason"] == "cursor_switched"
        assert leases_held(world) == [], "the admin lease is released"
        assert world.fake_host.get_slot(SLOT).messages == [], "picking an intent is never a prompt"

    run(scenario())


def test_acknowledge_clears_the_interruption_only_for_that_finding(world, studio):
    async def scenario():
        await bind(world)
        await world.binder.set_interrupted(REPO_ID, "default", INTENT, world.clock.iso())
        rec = await _row_in(world, "Queued")
        await world.store.__class__.update(
            world.store,
            "action_transitions",
            "0",
            {},
        ) if False else None
        # A recovery card carrying the interruption finding.
        recovery_id = rec.action_id
        world.store.cas_update(
            "actions",
            recovery_id,
            (await world.broker.get(recovery_id)).status_generation,
            {
                "type": "recovery",
                "evidence_json": {
                    "findings": [{"code": "interrupted_session", "params": {}}],
                    "card": {"decisions": ["acknowledge"], "reason": "interrupted_session"},
                },
            },
        )
        recovery = await world.broker.get(recovery_id)
        updated, _ = await world.broker.resolve(
            recovery.action_id, decision="acknowledge", payload={}, user="owner-1"
        )
        assert updated.status == "Cancelled"
        binding = await world.binder.get(REPO_ID, "default", INTENT)
        assert binding.interrupted_at is None

    run(scenario())


def test_keep_paused_pauses_the_intent_and_closes_the_card(world, studio):
    async def scenario():
        rec = await _row_in(world, "Queued")
        world.store.cas_update(
            "actions",
            rec.action_id,
            rec.status_generation,
            {"type": "circuit_breaker", "risk_class": "studio_only",
             "evidence_json": {"card": {"decisions": ["retry_now", "keep_paused"]}}},
        )
        card = await world.broker.get(rec.action_id)
        updated, created = await world.broker.resolve(
            card.action_id, decision="keep_paused", payload={}, user="owner-1"
        )
        assert created is None
        assert updated.status == "Cancelled"
        assert (await world.binder.get(REPO_ID, "default", INTENT)).paused is True

    run(scenario())


def test_a_studio_only_decision_is_refused_on_the_wrong_card(world, studio):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.resolve(
                gate.action_id, decision="acknowledge", payload={}, user="owner-1"
            )
        assert exc.value.code == "invalid_decision"
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.resolve(
                gate.action_id, decision="approve", payload={}, user="owner-1"
            )
        assert exc.value.code == "invalid_decision"

    run(scenario())


# --------------------------------------------------------------------------- #
# 8. the breaker
# --------------------------------------------------------------------------- #


async def _processing_command(world, kind: str = "run"):
    # These tests exercise delivery/failure, so start where a turn is applicable.
    record = world.builder.record(INTENT)
    (record / "aidlc-state.md").write_text(_state_with({STAGE: "-"}))
    (record / QUESTIONS_REL).unlink(missing_ok=True)
    for shard in (record / "audit").glob("*.md"):
        shard.write_text(F.audit_text(
            F.audit_block("HUMAN_TURN", TS),
            F.audit_block("STAGE_STARTED", F._plus(TS, 1), Stage=STAGE),
        ))
    await bind(world)
    rec = await world.broker.create_command(kind, world.repo, "default", INTENT, snap=snapshot(world))
    for status in ("Delivering", "Delivered", "Processing"):
        rec = await world.broker.transition(
            rec.action_id,
            status,
            expected_generation=rec.status_generation,
            reason="drive",
            evidence={},
        )
    return rec


def test_three_transient_failures_open_the_breaker_once(world, A):
    async def scenario():
        opened_flags = []
        for attempt in range(3):
            rec = await _processing_command(world)
            failed, opened = await world.broker.record_failure(
                rec,
                fingerprint="transport.stream_closed:abc123",
                fingerprint_class="transport",
                summary="failure.transport",
                evidence={"stderr": "stream closed"},
            )
            opened_flags.append(opened)
            assert failed.evidence["failure"]["count"] == attempt + 1
            # Retire the failure card so the next attempt can derive a fresh command.
            await world.broker._patch_evidence(failed, {"superseded": True})
        assert opened_flags == [False, False, True], "the third identical transport error is not transient"
        breaker = world.store.get("breakers", f"{REPO_ID}:{INTENT}:transport.stream_closed:abc123")
        assert breaker["count"] == 3 and breaker["opened_at"]

        cards = [
            r
            for r in await world.broker.list_live(repo_id=REPO_ID, intent_dir=INTENT)
            if r.type == "circuit_breaker"
        ]
        assert len(cards) == 1, "exactly one card per opening"
        assert len(world.notifications.sent) == 1, "exactly one notification per opening"
        assert world.notifications.sent[0]["type"] == "circuit_breaker"
        kinds = [row["kind"] for row in world.store.select("activity")]
        assert kinds.count("breaker.opened") == 1

    run(scenario())


def test_a_deterministic_failure_opens_the_breaker_immediately(world, A):
    async def scenario():
        rec = await _processing_command(world)
        failed, opened = await world.broker.record_failure(
            rec,
            fingerprint="guard.refusing_to_record:def456",
            fingerprint_class="guard",
            summary="failure.guard",
            evidence={"stderr": "Refusing to record this answer"},
        )
        assert opened is True
        assert failed.evidence["failure"]["breaker_open"] is True
        assert failed.retry_fingerprint == "guard.refusing_to_record:def456"
        assert len(world.notifications.sent) == 1
        card = world.notifications.sent[0]
        assert card["failure"]["fingerprint_class"] == "guard"
        assert card["severity"] == "attention"

    run(scenario())


@pytest.mark.parametrize("repo_options", [{"runnable": True}])
def test_a_failure_can_only_be_recorded_against_an_inflight_action(world, studio):
    async def scenario():
        await bind(world)
        rec = await world.broker.create_command(
            "run", world.repo, "default", INTENT, snap=snapshot(world)
        )
        with pytest.raises(studio.errors.IllegalTransition):
            await world.broker.record_failure(
                rec,
                fingerprint="config.x:1",
                fingerprint_class="config",
                summary="failure.config",
                evidence={},
            )

    run(scenario())


def test_retry_now_on_a_failure_card_resets_the_breaker_and_starts_a_new_attempt(world):
    async def scenario():
        rec = await _processing_command(world)
        failed, opened = await world.broker.record_failure(
            rec,
            fingerprint="guard.x:1",
            fingerprint_class="guard",
            summary="failure.guard",
            evidence={},
        )
        assert opened is True
        # The `failure` card itself is derived by the projection (tested there); what matters here is
        # what `retry_now` does with it, so the card is built with the shape a seed produces.
        card_row = await _row_in(world, "Queued")
        world.store.cas_update(
            "actions",
            card_row.action_id,
            card_row.status_generation,
            {
                "type": "failure",
                "risk_class": "studio_only",
                "evidence_json": {
                    "card": {
                        "decisions": ["retry_now", "keep_paused"],
                        "related_action_id": failed.action_id,
                    }
                },
            },
        )
        failure_card = await world.broker.get(card_row.action_id)
        updated, created = await world.broker.resolve(
            failure_card.action_id, decision="retry_now", payload={}, user="owner-1"
        )
        assert updated.status == "Cancelled"
        assert created is not None and created.type == "run" and created.status == "Queued"
        assert created.action_id != failed.action_id
        assert world.store.get("breakers", f"{REPO_ID}:{INTENT}:guard.x:1")["opened_at"] is None


# --------------------------------------------------------------------------- #
# 9. derivation, dedupe and the card
# --------------------------------------------------------------------------- #


def test_one_live_card_per_boundary_and_terminal_rows_keep_their_key(world, A):
    async def scenario():
        first = await seed(world)
        again = await seed(world)
        assert {r.action_id for r in first} == {r.action_id for r in again}
        gate = pick(again, "gate")

        # A cancelled twin must not stop the boundary from having a live card again (FR-ACT-007).
        cancelled = await world.broker.transition(
            gate.action_id,
            "Cancelled",
            expected_generation=gate.status_generation,
            reason="dismissed",
            evidence={},
        )
        rederived = pick(await seed(world), "gate")
        assert rederived.action_id != cancelled.action_id
        assert rederived.dedupe_key == cancelled.dedupe_key
        assert rederived.evidence["superseded_action_id"] == cancelled.action_id
        rows = world.store.select("actions", {"dedupe_key": gate.dedupe_key})
        assert len(rows) == 2

    run(scenario())


def test_a_refresh_touches_a_queued_card_and_never_an_inflight_one(world, A):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        unchanged = pick(await seed(world), "gate")
        assert unchanged.status_generation == gate.status_generation, "no write when nothing moved"

        world.builder.append_audit(INTENT, F.audit_block("SESSION_STARTED", F._plus(TS, 30)))
        refreshed = pick(await seed(world), "gate")
        assert refreshed.status_generation > gate.status_generation
        assert refreshed.evidence["audit"]["last_event"]["type"] == "SESSION_STARTED"

        receipt = await approve(world, A, rec=refreshed)
        frozen = await world.broker.get(receipt.action_id)
        world.builder.append_audit(INTENT, F.audit_block("SESSION_STARTED", F._plus(TS, 60)))
        await seed(world)
        after = await world.broker.get(receipt.action_id)
        assert after.status_generation == frozen.status_generation
        assert after.evidence["audit"] == frozen.evidence["audit"], "an in-flight record is not rewritten"

    run(scenario())


@pytest.mark.parametrize("repo_options", [{"second_gate": True}])
def test_a_second_open_gate_card_shows_its_own_stage_and_not_the_current_one(world, repo_options):
    """`card()` re-derives live evidence, and it must re-derive it for the card's OWN boundary (A20).

    An intent can hold more than one `[?]` row. The refresh overwrites `artifacts`, `review`,
    `acceptance_criteria` and `questions`, so defaulting to the snapshot's current stage showed the person
    about to approve the *other* gate the current stage's file list and READY verdict, under the right
    stage's name — while `captured` stayed correctly scoped, so `submit` found no drift and dispatched the
    approval without a word. `derive_cards` was fixed for exactly this (`evidence-1`); the read path the
    UI actually sees was not.
    """

    async def scenario():
        await bind(world)
        gates = {rec.stage: rec for rec in await seed(world) if rec.type == "gate"}
        assert {STAGE, SECOND_STAGE} <= set(gates), sorted(gates)

        other = await world.broker.card(gates[SECOND_STAGE], snapshot(world))
        paths = [a["relpath"] for a in other["evidence"]["artifacts"]]
        assert paths and all(SECOND_STAGE in path for path in paths), paths
        assert other["evidence"]["review"]["verdict"] == "NOT-READY"
        assert "the other stage's finding" in json.dumps(other["evidence"]["review"]["findings"])

        current = await world.broker.card(gates[STAGE], snapshot(world))
        assert all(STAGE in a["relpath"] for a in current["evidence"]["artifacts"])
        assert current["evidence"]["review"]["verdict"] == "READY"

    run(scenario())


def test_a_queued_card_picks_up_a_conversation_bound_after_it_was_derived(world, A):
    """A card derived before anything was bound must stop refusing once one is (§2.13).

    `sendBlockedFor` in the UI disables the send exactly when `evidence.session.slot_key` is absent,
    while `submit` resolves the binding live. A frozen null therefore refused a decision the backend
    would have accepted, and one live command per boundary meant clicking Run again returned this same
    card — the intent could never be dispatched at all.
    """

    async def scenario():
        gate = pick(await seed(world), "gate")
        before = await world.broker.card(gate, snapshot(world))
        assert before["evidence"]["session"] is None, "nothing is bound yet"

        await bind(world)
        after = await world.broker.card(gate, snapshot(world))
        assert after["evidence"]["session"]["slot_key"] == SLOT

        receipt = await approve(world, A, rec=await world.broker.get(gate.action_id))
        # `unbind` refuses outright while a decision is in flight, so the binding row is moved under the
        # broker to prove the freeze: a dispatched card must keep the slot the human decided against.
        key = f"{REPO_ID}:default:{INTENT}"
        row = world.store.get("intent_bindings", key)
        world.store.cas_update(
            "intent_bindings",
            key,
            int(row["binding_generation"]),
            {"slot_key": "aidlc-studio-somewhere-else"},
        )
        dispatched = await world.broker.get(receipt.action_id)
        card = await world.broker.card(dispatched, snapshot(world))
        assert card["evidence"]["session"]["slot_key"] == SLOT, (
            "a card whose decision is on the wire keeps what the human decided against"
        )

    run(scenario())


def test_retire_stale_cancels_a_queued_card_and_only_marks_a_failed_one(world):
    async def scenario():
        cards = await seed(world)
        gate = pick(cards, "gate")
        rec = await _row_in(world, "Failed")
        world.store.cas_update(
            "actions",
            rec.action_id,
            rec.status_generation,
            {"dedupe_key": "gate:gone:forever"},
        )
        await world.broker.retire_stale(REPO_ID, "default", INTENT, live_keys=set())
        assert (await world.broker.get(gate.action_id)).status == "Cancelled"
        failed = await world.broker.get(rec.action_id)
        assert failed.status == "Failed", "Failed is terminal (review P07)"
        assert failed.evidence["superseded"] is True

    run(scenario())


@pytest.mark.parametrize("checkpoint,title,options", [
    ("summary_confirmation", "Consolidated Summary Confirmation", "- Looks correct\n- Request changes"),
    ("plan_approval", "Plan Approval", "- Approve Plan\n- Request Changes"),
])
def test_checkpoint_card_does_not_claim_zero_unanswered_questions(world, checkpoint, title, options):
    async def scenario():
        path = world.builder.record(INTENT) / QUESTIONS_REL
        path.write_text(f"## {title}\n\n{options}\n\n[Answer]:\n")
        question = pick(await seed(world), "question")
        card = await world.broker.card(question, snapshot(world))
        assert card["evidence"]["questions"]["pending_count"] == 0
        assert card["headline"]["key"] == f"action.question.{checkpoint}.headline"
        assert card["consequence"]["key"] == f"action.question.consequence.{checkpoint}"
    run(scenario())


def test_unavailable_repository_is_refused_before_snapshot_or_dispatch(world, A, monkeypatch):
    async def scenario():
        gate = pick(await seed(world), "gate")
        world.store.update("repos", REPO_ID, {
            "availability": "unavailable", "availability_detail": "path_missing",
        })

        async def no_snapshot(*args, **kwargs):
            raise AssertionError("an unavailable repo must not be treated as an empty live snapshot")

        monkeypatch.setattr(world.broker, "_snapshot", no_snapshot)
        with pytest.raises(world.S.errors.StudioError) as exc:
            await world.broker.submit(
                gate.action_id, captured=captured_of(gate, A), payload={"decision": "approve"},
                client_wire_text="Approve", user="owner",
            )
        assert exc.value.code == "repo_unavailable"
        assert exc.value.details["detail"] == "path_missing"
        assert world.store.lease_list() == []
        assert (await world.broker.get(gate.action_id)).delivery_id is None
    run(scenario())


def test_unavailable_repository_keeps_the_saved_card_but_offers_no_human_dispatch(world):
    async def scenario():
        gate = pick(await seed(world), "gate")
        world.store.update("repos", REPO_ID, {
            "availability": "unavailable", "availability_detail": "path_missing",
        })
        card = await world.broker.card(gate, None)
        assert card["repo"]["availability"] == "unavailable"
        assert card["repo"]["availability_detail"] == "path_missing"
        assert card["primary"] is None and card["decisions"] == []
        assert card["status"] == "Queued"
        assert card["evidence"]["state"] == gate.evidence["state"]
        world.store.update("repos", REPO_ID, {
            "availability": "available", "availability_detail": None,
        })
        restored = await world.broker.card(gate, snapshot(world))
        assert restored["primary"]["decision"] == "approve"
        assert (await world.broker.get(gate.action_id)).status == "Queued"
    run(scenario())


def test_the_card_is_the_shape_the_ui_type_pins(world, A, C):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        card = await world.broker.card(gate, snapshot(world))
        assert set(card) == {
            "action_id", "type", "queue_type", "status", "status_generation", "source", "risk_class",
            "severity", "priority_group", "repo", "space", "intent", "stage", "headline",
            "consequence", "waiting_since", "created_at", "updated_at", "primary", "decisions",
            "captured", "evidence", "delivery", "resolution", "failure", "install", "budget",
            "advisor", "deep_link", "dedupe_key", "human_text_present",
            "acknowledged_evidence_sha256",
        }
        assert card["queue_type"] == "gate"
        assert card["severity"] == "blocking"
        assert card["priority_group"] == 2
        assert card["repo"] == {
            "repo_id": REPO_ID,
            "label": "demo",
            "canonical_path": str(world.root),
            "availability": "available",
            "availability_detail": None,
            "archived": False,
        }
        assert card["headline"] == {"key": "action.gate.headline", "params": {"stage": STAGE, "attempt": 1}}
        assert card["primary"] == {"decision": "approve", "label_key": "decision.approve.label"}
        assert card["decisions"][0] == {
            "decision": "approve",
            "label_key": "decision.approve.label",
            "lane": "human_lane",
            "wire_text_template": "Approve",
            "requires": [],
        }
        assert set(card["evidence"]) == {
            "state", "audit", "directive", "artifacts", "review", "acceptance_criteria", "findings",
            "session", "questions", "git", "markers", "presence_baseline", "presence",
            "cursor_readback",
        }
        assert set(card["captured"]) == set(A.COMPARED_CAPTURED_FIELDS) | {"captured_at", "stable"}
        assert card["delivery"]["delivery_id"] is None and card["delivery"]["lane"] is None
        assert card["deep_link"] == f"{C.DEEP_LINK_BASE}?view=actions&action={gate.action_id}"
        assert card["human_text_present"] is False
        assert card["budget"] is None
        assert card["advisor"] == {"latest_draft_id": None, "status": None}
        assert "card" not in card["evidence"], "bookkeeping never leaks into the wire shape"

    run(scenario())


def test_a_failure_whose_status_cas_is_lost_leaves_no_strike_on_the_breaker(world, A, studio):
    """A phantom strike could open the breaker against an intent nothing was wrong with (§11.4).

    Two actors classify the same in-flight action in one pass — the reconciler tick and a "Reconcile from
    disk" click. The loser's `_transition` raises `StaleGeneration`, but the strike used to be banked
    before that CAS, so the loser's mark stayed on disk: three of them on a transient class, or one
    deterministic class, and the breaker opens and refuses every later Run.
    """

    async def scenario():
        rec = await _reconciliation_required(world, A)
        stale = rec                                       # the loser's snapshot, taken before the race
        world.store.cas_update(
            "actions", rec.action_id, rec.status_generation, {"waiting_since": world.clock.iso()}
        )

        with pytest.raises(studio.errors.StaleGeneration):
            await world.broker.record_failure(
                stale,
                fingerprint="transport:abc123",
                fingerprint_class="transport",
                summary="failure.transport",
                evidence={"why": "the CAS is already lost"},
            )
        assert world.store.select("breakers") == [], "a refused failure marks nothing"

        # The winner's identical failure does count, exactly once.
        fresh = await world.broker.get(rec.action_id)
        failed, opened = await world.broker.record_failure(
            fresh,
            fingerprint="transport:abc123",
            fingerprint_class="transport",
            summary="failure.transport",
            evidence={"why": "this one commits"},
        )
        assert failed.status == "Failed" and opened is False, "one transient strike does not open it"
        rows = world.store.select("breakers")
        assert [int(r["count"]) for r in rows] == [1], rows
        assert failed.evidence["failure"]["count"] == 1

    run(scenario())


def test_a_recovery_card_offers_nothing_that_cannot_succeed(world, studio):
    """"A button that always fails is worse than no button" — the rule `_offered_decisions` states.

    The recovery seed names three decisions and two of them cannot work on it. `rebind_session` needs a
    `slot_key` that no client sends (the UI's resolve payload carries the decision alone), so it answered
    400 `bad_body` every time; rebinding a conversation belongs to the session panel, which can pick a
    target. `mark_not_delivered` is legal only from `ReconciliationRequired` (R07), which a studio-only
    card never reaches, so on a recovery card it could only answer 409 — in the danger treatment.
    """

    async def scenario():
        await bind(world)
        rec = await _row_in(world, "Queued")
        world.store.cas_update(
            "actions",
            rec.action_id,
            (await world.broker.get(rec.action_id)).status_generation,
            {
                "type": "recovery",
                "evidence_json": {
                    "findings": [],
                    "card": {
                        "decisions": ["rebind_session", "mark_not_delivered", "acknowledge"],
                        "reason": "state_unreadable",
                    },
                },
            },
        )
        recovery = await world.broker.get(rec.action_id)
        card = await world.broker.card(recovery, snapshot(world))
        assert [d["decision"] for d in card["decisions"]] == ["acknowledge"]

        # The vocabulary itself is untouched: `resolve` still accepts the decision, so a client that can
        # name a target conversation keeps the capability. What is gone is the button that could not.
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.broker.resolve(
                recovery.action_id, decision="rebind_session", payload={}, user="owner-1"
            )
        assert exc.value.code == "bad_body", exc.value.code

    run(scenario())


def test_an_uncertain_card_is_retyped_and_offers_the_uncertain_decisions(world, A):
    async def scenario():
        rec = await _reconciliation_required(world, A)
        card = await world.broker.card(rec, snapshot(world))
        assert card["type"] == "gate"
        assert card["queue_type"] == "delivery_uncertain"
        assert card["severity"] == "critical"
        assert card["priority_group"] == 1
        assert [d["decision"] for d in card["decisions"]] == [
            "reconcile",
            "mark_not_delivered",
            "resubmit",
        ]
        assert card["delivery"]["lane"] == "human_lane"
        assert card["delivery"]["host"]["body"]["agent"] == "aidlc"
        assert card["evidence"]["presence_baseline"] is not None
        assert card["evidence"]["cursor_readback"]["ok"] is True

    run(scenario())


def test_confirmed_delivery_does_not_offer_undelivered_or_resubmit(world, A):
    async def scenario():
        rec = await _reconciliation_required(world, A)
        rec = await world.broker._patch_evidence(rec, {"delivery_confirmed": True})
        card = await world.broker.card(rec, snapshot(world))
        assert [d["decision"] for d in card["decisions"]] == ["reconcile"]

    run(scenario())


def test_human_text_keeps_only_what_the_person_typed(world, A):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        receipt = await world.broker.submit(
            gate.action_id,
            captured=captured_of(gate, A),
            payload={"decision": "request_changes", "feedback": "Cover C3 in the criteria."},
            client_wire_text="Request Changes: Cover C3 in the criteria.",
            user="owner-1",
        )
        rec = await world.broker.get(receipt.action_id)
        assert rec.human_text == "Cover C3 in the criteria.", "only the prose, not the protocol label"
        card = await world.broker.card(rec, None)
        assert "human_text" not in card, "the card carries a flag, never the retained text"
        assert card["human_text_present"] is True
        # `delivery.wire_text` does contain the feedback, and must: it is the record of what was sent
        # (§2.11). What retention removes is the `human_text` copy, here and on the activity row.
        assert card["delivery"]["wire_text"] == "Request Changes: Cover C3 in the criteria."
        submitted = [
            row for row in world.store.select("activity") if row["kind"] == "action.submitted"
        ]
        assert submitted and submitted[-1]["human_text"] == "Cover C3 in the criteria."

    run(scenario())


def test_every_transition_publishes_an_event_and_an_activity_row(world, A):
    async def scenario():
        await bind(world)
        gate = pick(await seed(world), "gate")
        await approve(world, A, rec=gate)
        published = [name for name, _ in world.ctx.events.published]
        assert published.count("aidlc-studio:action") >= 2
        payloads = [data for _, data in world.ctx.events.published]
        assert payloads[-1]["payload"]["status"] == "Delivering"
        assert payloads[-1]["payload"]["from_status"] == "Queued"
        kinds = [row["kind"] for row in world.store.select("activity")]
        assert "action.created" in kinds and "action.submitted" in kinds
        rows = world.store.select("action_transitions", {"action_id": gate.action_id})
        assert rows[-1]["to_status"] == "Delivering"
        assert rows[-1]["reason"] == "submitted:approve"

    run(scenario())


# --------------------------------------------------------------------------- #
# 10. the machine lane
# --------------------------------------------------------------------------- #


def test_the_machine_lane_always_refuses_and_records_the_refusal(world, C, studio):
    async def scenario():
        with pytest.raises(studio.errors.StudioError) as exc:
            await world.machine.dispatch("run", REPO_ID, INTENT)
        assert exc.value.code == "machine_lane_unavailable"
        assert exc.value.details["reason"] == C.MACHINE_LANE_REASON
        rows = [r for r in world.store.select("activity") if r["kind"] == "machine_lane.refused"]
        assert len(rows) == 1

    run(scenario())


def test_marker_movement_opens_the_deterministic_breaker(world, A):
    async def scenario():
        rec = await _processing_command(world)
        before = SimpleNamespace(human_turn_mtime_ns=1, turn_counter=7)
        await world.machine.assert_no_human_marker_movement(
            before, before, repo_id=REPO_ID, intent_key=INTENT
        )
        assert (await world.broker.get(rec.action_id)).status == "Processing"

        after = SimpleNamespace(human_turn_mtime_ns=2, turn_counter=8)
        await world.machine.assert_no_human_marker_movement(
            before, after, repo_id=REPO_ID, intent_key=INTENT
        )
        failed = await world.broker.get(rec.action_id)
        assert failed.status == "Failed"
        assert failed.evidence["failure"]["fingerprint_class"] == "human_marker_movement"
        assert failed.evidence["failure"]["breaker_open"] is True
        breakers = [
            row
            for row in world.store.select("breakers")
            if row["fingerprint_class"] == "human_marker_movement"
        ]
        assert len(breakers) == 1 and breakers[0]["opened_at"]

    run(scenario())


def test_marker_movement_without_an_inflight_action_still_opens_the_breaker(world):
    async def scenario():
        await world.machine.assert_no_human_marker_movement(
            SimpleNamespace(human_turn_mtime_ns=1, turn_counter=1),
            SimpleNamespace(human_turn_mtime_ns=9, turn_counter=1),
            repo_id=REPO_ID,
            intent_key=INTENT,
        )
        breakers = world.store.select("breakers")
        assert len(breakers) == 1
        assert breakers[0]["fingerprint_class"] == "human_marker_movement"
        assert breakers[0]["opened_at"]

    run(scenario())
