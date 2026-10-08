"""Action broker (``backend/studio/actions.py``): the whole transition matrix, per-field compare-and-submit,
``NotDelivered`` proven and never reported, and the two-phase lane (the backend commits, the browser sends)."""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import fixtures as F
from _helpers_a import aio, refused

APP_ROOT = Path(__file__).resolve().parent.parent

REPO_ID = "r_000000000001"
IDENTITY = "1:100"
INTENT = "260904-gate-demo"
OTHER = "260904-second-intent"
STAGE = "requirements-analysis"
SECOND_STAGE = "user-stories"
SLOT = f"aidlc-studio-{REPO_ID}-{INTENT}"
BOOT = "boot-test-0001"
TS = "2026-09-04T10:00:00Z"
QUESTIONS_REL = f"inception/{STAGE}/{STAGE}-questions.md"
REFUSAL = "slot agent mismatch"


def pinned() -> dict:
    return json.loads((APP_ROOT / "docs" / "design" / "wire-text.json").read_text("utf-8"))


@pytest.fixture
def A(studio):
    assert studio.actions is not None, getattr(studio, "actions__error", "actions.py did not import")
    return studio.actions


@pytest.fixture
def C(studio):
    return studio.constants


@pytest.fixture
def E(studio):
    return studio.errors


class Notifications:
    """``NotificationAdapter`` stand-in: records one entry per ``notify("action", card)`` call."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def notify(self, kind: str, card: dict) -> dict:
        assert kind == "action"
        self.sent.append(card)
        return {"dashboard": True, "slack": "disabled", "dedupe_hit": False}


def _state_with(marks: dict[str, str] | None = None) -> str:
    # The stage and the phase must agree, or `phase_stage_disagreement` refuses every dispatch first.
    return F.state_text(marks=marks or {STAGE: "?"}, fields={
        "Current Stage": STAGE, "Status": "Running", "Next Stage": "user-stories", "Lifecycle Phase": "INCEPTION",
        "Completed": "7"})


def _questions(single: bool = False) -> str:
    """The real pending questions file, or an authored single-question one."""
    if not single:
        return F.blank_answers(F.real_questions())
    return F.questions_text(stage=STAGE, questions=[("Which store?", ["Postgres", "SQLite"])])


def _build_repo(builder, *, extra_intent=False, revising=0, questions=None, second_gate=False, runnable=False):
    if runnable:
        audit = F.audit_text(F.audit_block("HUMAN_TURN", TS), F.audit_block("STAGE_STARTED", F._plus(TS, 1), Stage=STAGE))
    else:
        audit = F.gate_open_audit(STAGE)
    audit += "".join(F.audit_block("STAGE_REVISING", F._plus(TS, 100 + i), Stage=STAGE, Revision_count=str(i + 1))
                     for i in range(revising))
    builder = builder.with_engine("payload").with_workspace()
    if extra_intent:  # written first, and the cursor points at it, so the cursor switch has to move
        builder = builder.with_intent(OTHER, state=_state_with({STAGE: " "}), audit=F.gate_open_audit(STAGE),
                                      active=False)
    marks = {STAGE: "-" if runnable else "?"}
    artifacts = {f"inception/{STAGE}/requirements.md": "# Requirements\n\nOne requirement.\n"}
    if second_gate:  # two `[?]` rows at once, each with its own reviewer verdict
        marks[SECOND_STAGE] = "?"
        artifacts[f"inception/{STAGE}/requirements.md"] = (
            "# Requirements\n\nOne requirement.\n\n## Review\n\nVerdict: READY\n")
        artifacts[f"inception/{SECOND_STAGE}/user-stories.md"] = (
            "# User stories\n\n## Review\n\nVerdict: NOT-READY\n\n### Findings\n\n- blocker: the other stage's finding\n")
    builder = builder.with_intent(
        INTENT, state=_state_with(marks), audit=audit, directive={"version": 1, "stage": STAGE, "state_sha256": "AUTO"},
        questions={} if runnable else {QUESTIONS_REL: questions if questions is not None else _questions()},
        artifacts=artifacts, active=not extra_intent)
    if extra_intent:  # point the cursor at the other intent without touching INTENT's files
        (builder.root / "aidlc" / "spaces" / "default" / "intents" / "active-intent").write_text(OTHER + "\n")
    return builder.with_git().build()


def _broker(w, boot_id: str):
    return w.S.actions.HumanActionBroker(w.store, w.projection, w.reader, w.consistency, w.scheduler, w.binder,
                                         w.engine, w.host, w.events, w.activity, w.notifications, w.settings,
                                         w.clock, w.ids, boot_id)


@pytest.fixture
def repo_options() -> dict:
    """How the fixture repository is laid out; a test overrides it with ``parametrize``."""
    return {}


@pytest.fixture
def world(studio, fake_ctx, fake_host, clock, ids, fake_bun, repo_builder, repo_options):
    """The real object graph, wired the way ``Services.build`` wires it; only notifications are a stand-in."""
    S = studio
    root = _build_repo(repo_builder, **dict(repo_options))
    store = S.storage.Storage(Path(fake_ctx.data_dir) / S.constants.DB_FILENAME, clock=clock)
    store.open()
    store.insert("repos", {
        "id": REPO_ID, "resolved_identity": IDENTITY, "canonical_path": str(root), "label": "demo",
        "added_at": clock.iso(), "platform": "darwin", "install_status": "installed",
        "installed_engine_version": S.constants.BUNDLED_ENGINE_VERSION, "engine_dir": ".kiro",
        "availability": "available"})
    w = SimpleNamespace(S=S, root=root, store=store, clock=clock, ids=ids, fake_host=fake_host,
                        builder=repo_builder, ctx=fake_ctx, notifications=Notifications())
    w.reader = S.aidlc_reader.AidlcReader(clock)
    w.consistency = S.consistency.ConsistencyEngine()
    w.projection = S.projection.Projection(w.reader, w.consistency, clock)
    w.activity = S.activity.ActivityProjector(store, clock)
    w.events = S.events.EventLog(fake_ctx.events, clock)
    w.host = S.sessions.HostBridge(clock, logging.getLogger("test.actions"))
    w.settings = S.settings.SettingsService(store, w.host, clock)
    w.settings.load()
    w.scheduler = S.leases.RepoScheduler(store, w.host, w.settings, clock, w.activity,
                                         busy_reasons=S.sessions.busy_reasons)
    w.binder = S.sessions.SessionBinder(store, w.host, w.projection, clock, w.activity)
    w.engine = S.engine.EngineRunner(bun_path=fake_bun, clock=clock, activity=w.activity)
    w.broker = _broker(w, BOOT)
    fake_host.add_slot(SLOT, project=str(root), agent="aidlc", app="")
    w.host.attach(fake_host)
    w.repo = S.repo_registry.RepoRecord.from_row(store.get("repos", REPO_ID))
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
    live = await w.broker.list_live(repo_id=REPO_ID, space="default", intent_dir=intent_dir, include_revision=True)
    breakers = [SimpleNamespace(**row) for row in w.store.select("breakers")
                if str(row["key"]).startswith(f"{REPO_ID}:{intent_dir}:")]
    seeds = w.projection.derive_cards(snap, findings=[], binding=binding, install=None, breakers=breakers,
                                      live_actions=live, slot=w.host.slot(SLOT))
    return await w.broker.upsert_derived(seeds)


def pick(recs, type_: str):
    return next(rec for rec in recs if rec.type == type_)


async def bound_card(w, type_: str = "gate"):
    """Bind the canonical slot, then seed and return this intent's ``type_`` card."""
    await bind(w)
    return pick(await seed(w), type_)


def captured_of(rec, A, **overrides) -> dict:
    return {**{name: getattr(rec.captured, name) for name in A.COMPARED_CAPTURED_FIELDS}, **overrides}


async def submit(w, rec=None, *, payload=None, wire="Approve", captured=None, user="owner-1"):
    """``broker.submit`` of ``rec`` (default: a freshly seeded gate) with its own capture; approves by default."""
    rec = rec or pick(await seed(w), "gate")
    return await w.broker.submit(rec.action_id, captured=captured_of(rec, w.S.actions) if captured is None else captured,
                                 payload=payload or {"decision": "approve"}, client_wire_text=wire, user=user)


async def report(w, rec, receipt, outcome, http_status, body, *, broker=None):
    return await (broker or w.broker).record_delivery(rec.action_id, delivery_id=receipt.delivery_id,
                                                      outcome=outcome, http_status=http_status, receipt=body)


async def delivering(w):
    """A row sitting in ``Delivering`` with everything the delivery proof needs on it."""
    await bind(w)
    receipt = await submit(w)
    return await w.broker.get(receipt.action_id), receipt


async def uncertain(w):
    """An uncertain report is already the human's to decide: no intermediate status to wait through."""
    rec, receipt = await delivering(w)
    after, _ = await report(w, rec, receipt, "uncertain", None, None)
    assert after.status == "ReconciliationRequired"
    return after


async def _row_in(w, status: str):
    """One action row parked in ``status``, created without going through the graph."""
    action_id, now = w.ids.new("a"), w.clock.iso()
    w.store.insert("actions", {
        "action_id": action_id, "type": "gate", "risk_class": "human_lane", "repo_id": REPO_ID,
        "resolved_repo_identity": IDENTITY, "intent_dir": INTENT, "space": "default", "stage": STAGE,
        "status": status, "status_generation": 0, "created_at": now, "updated_at": now, "waiting_since": now,
        "source": "studio", "evidence_json": {}})
    return await w.broker.get(action_id)


async def _retyped(w, status: str = "Queued", **values):
    """A ``_row_in`` row whose columns are then rewritten in place, read back through the broker."""
    rec = await _row_in(w, status)
    w.store.cas_update("actions", rec.action_id, rec.status_generation, values)
    return await w.broker.get(rec.action_id)


async def move(w, rec, *statuses: str, reason: str = "drive"):
    """Real CAS transitions through ``statuses``; ``Delivered`` carries the turn start a report would stamp."""
    for status in statuses:
        rec = await w.broker.transition(rec.action_id, status, expected_generation=rec.status_generation, reason=reason,
                                        evidence={"turn_started_at": w.clock.iso()} if status == "Delivered" else {})
    return rec


def in_flight(w, action_id: str = "a_other", **over) -> str:
    """Another dispatched action of this repository with its turn on the wire: an execution lease."""
    now = w.clock.iso()
    w.store.insert("actions", {
        "action_id": action_id, "type": "gate", "risk_class": "human_lane", "repo_id": REPO_ID,
        "resolved_repo_identity": IDENTITY, "intent_dir": INTENT, "space": "default", "status": "Delivering",
        "lease_generation": 7, "created_at": now, "updated_at": now, "delivering_at": now, "waiting_since": now,
        **over})
    return action_id


async def force_stop(w):
    return await w.broker.create_force_stop(w.repo, "default", INTENT, snap=snapshot(w))


def before_the_delivering_cas(w, monkeypatch, step) -> None:
    """Run ``step()`` on the test's loop between submit's reads and its real ``Delivering`` CAS."""
    loop, original = asyncio.get_running_loop(), w.store.cas_update

    def interleaved(table, key, expected, values):
        if values.get("status") == "Delivering":
            asyncio.run_coroutine_threadsafe(step(), loop).result(timeout=10)
        return original(table, key, expected, values)

    monkeypatch.setattr(w.store, "cas_update", interleaved)


# --------------------------------------------------------------------------- #
# 1. the transition matrix
# --------------------------------------------------------------------------- #


def test_the_transition_table_is_the_prd_graph_and_nothing_more(A, C):
    table = A.ALLOWED_TRANSITIONS
    assert table == {k: frozenset(v) for k, v in C.ALLOWED_TRANSITIONS.items()}
    assert set(table) == set(C.ACTION_STATUS)
    # PRD §11.1's cancel, its four dead ends, and the merged states: a running turn is still `Delivered`, and
    # every doubt about a delivery is `ReconciliationRequired` from its first moment.
    assert "Cancelled" in table["Queued"]
    assert {"Draft", "Processing", "DeliveryUncertain"}.isdisjoint(C.ACTION_STATUS)
    assert "ReconciliationRequired" in table["Delivering"]
    assert {"StateChanged", "ResolvedNoTransition", "Failed", "ReconciliationRequired"} == table["Delivered"]
    for terminal in ("StateChanged", "ResolvedNoTransition", "Failed", "Cancelled"):
        assert table[terminal] == frozenset()
    assert "Cancelled" not in table["Failed"], "exactly the edge retire_stale must not use (review P07)"


@aio
async def test_every_status_pair_is_allowed_or_refused_exactly_as_the_table_says(world, A, C, E):
    """The whole 9x9 matrix: an edge outside the table is how a decision gets replayed."""
    legal = illegal = 0
    for source in C.ACTION_STATUS:
        for target in C.ACTION_STATUS:
            rec = await _row_in(world, source)
            if target in A.ALLOWED_TRANSITIONS[source]:
                after = await world.broker.transition(rec.action_id, target, expected_generation=rec.status_generation,
                                                      reason="matrix", evidence={})
                assert after.status == target, (source, target)
                legal += 1
                continue
            with pytest.raises(E.IllegalTransition) as exc:
                await world.broker.transition(rec.action_id, target, expected_generation=rec.status_generation,
                                              reason="matrix", evidence={})
            assert (exc.value.details["from"], exc.value.details["to"]) == (source, target)
            assert (await world.broker.get(rec.action_id)).status == source
            illegal += 1
    assert legal + illegal == len(C.ACTION_STATUS) ** 2 == 81
    assert legal == sum(len(v) for v in A.ALLOWED_TRANSITIONS.values())


@aio
async def test_a_lost_cas_is_a_stale_generation_not_a_silent_write(world, E):
    rec = await _row_in(world, "Queued")
    await move(world, rec, "Cancelled", reason="first")
    with pytest.raises(E.StaleGeneration):
        await move(world, rec, "Delivering", reason="second")


# --------------------------------------------------------------------------- #
# 2. wire text
# --------------------------------------------------------------------------- #


def test_wire_text_templates_and_constants_match_the_pinned_file_byte_for_byte(A, C):
    """Casing is load-bearing (`Request Changes` vs `Request changes`), so no template or constant may drift."""
    assert A.WIRE_TEXT_TEMPLATES == pinned()["decisions"]
    assert {name: value for name, value in vars(C).items() if name.startswith("WIRE_")} == pinned()["constants"]


def test_wire_text_for_every_decision_is_the_pinned_string(A, C):
    def wire(decision, payload=None):
        return A.wire_text_for(decision, payload or {}, questions=None, grouped_answers_enabled=False)

    assert wire("approve") == "Approve"
    assert wire("accept_as_is") == "Accept as-is"
    assert wire("approve_plan") == "Approve Plan"
    assert wire("request_changes", {"feedback": " needs criteria "}) == "Request Changes: needs criteria"
    assert wire("request_plan_changes", {"feedback": "narrow it"}) == "Request Changes: narrow it"
    assert wire("confirm_summary", {"choice": "looks_correct"}) == "Looks correct"
    assert wire("confirm_summary", {"choice": "request_changes", "feedback": "row 3"}) == "Request changes: row 3"
    assert wire("provide_input", {"kind": "scope", "scope": "poc"}) == "/aidlc --scope poc"
    assert wire("provide_input", {"kind": "free_text", "text": " ship it "}) == "ship it"
    assert wire("run") == "/aidlc"
    assert wire("resume") == "/aidlc --resume"
    assert wire("prepare_commit") == C.WIRE_PREPARE_COMMIT
    assert wire("prepare_commit").endswith("Do not push.")
    golden = {"feedback": "The acceptance criteria for C3 are not covered."}
    assert wire("request_changes", golden) == pinned()["golden"]["request_changes_example"]


def test_no_studio_only_or_host_control_decision_sends_anything(A, C):
    for decision in sorted(C.STUDIO_ONLY_DECISIONS | C.HOST_CONTROL_DECISIONS):
        assert A.wire_text_for(decision, {}, questions=None, grouped_answers_enabled=True) is None, decision
    assert (A.lane_for("force_stop"), A.lane_for("approve"), A.lane_for("acknowledge")) == (
        "host_control", "human_lane", "studio_only")


@pytest.mark.parametrize("decision,payload", [
    ("request_changes", {"feedback": "   "}), ("request_changes", {}), ("request_plan_changes", {"feedback": ""}),
    ("confirm_summary", {"choice": "request_changes", "feedback": " "}),
    ("provide_input", {"kind": "free_text", "text": "\n"}), ("provide_input", {"kind": "scope", "scope": ""}),
])
def test_a_blank_free_text_field_is_never_sent(A, E, decision, payload):
    with refused(E, "feedback_required"):
        A.wire_text_for(decision, payload, questions=None, grouped_answers_enabled=True)


@pytest.mark.parametrize("decision,payload", [
    ("confirm_summary", {"choice": "yes"}), ("provide_input", {"kind": "shell", "text": "x"}), ("no_such_decision", {}),
])
def test_an_unknown_choice_or_kind_is_an_invalid_decision(A, E, decision, payload):
    with refused(E, "invalid_decision"):
        A.wire_text_for(decision, payload, questions=None, grouped_answers_enabled=True)


def _parse(studio, text: str):
    return studio.aidlc_reader.parse_questions_file(text, QUESTIONS_REL, "sha")


def _answers(A, questions, *answers: dict, grouped: bool = False):
    return A.wire_text_for("answers", {"answers": list(answers)}, questions=questions, grouped_answers_enabled=grouped)


def ans(index: int, letters, free_text=None) -> dict:
    return {"index": index, "option_letters": letters, "free_text": free_text}


def test_one_pending_question_sends_the_label_alone(A, studio):
    questions = _parse(studio, _questions(single=True))
    assert questions.pending_count == 1
    assert _answers(A, questions, ans(1, ["B"])) == "SQLite"


def test_multi_select_joins_labels_and_a_single_select_refuses_two(A, E, studio):
    multi = _parse(studio, F.questions_text(stage=STAGE, questions=[
        ("Which stores? (multi-select)", ["Postgres", "SQLite", "Redis"])]))
    assert _answers(A, multi, ans(1, ["A", "C"])) == "Postgres, Redis"
    with refused(E, "invalid_decision"):
        _answers(A, _parse(studio, _questions(single=True)), ans(1, ["A", "B"]))


def test_the_other_option_sends_free_text_and_refuses_a_blank_one(A, E, studio):
    questions = _parse(studio, F.questions_text(stage=STAGE, questions=[("Which store?", ["Postgres"])]))
    assert questions.questions[0].options[-1].is_other
    assert _answers(A, questions, ans(1, ["X"], " DynamoDB ")) == "DynamoDB"
    with refused(E, "answers_incomplete") as exc:
        _answers(A, questions, ans(1, ["X"], ""))
    assert exc.value.details["reason"] == "free_text_required"


@pytest.mark.parametrize("context", [
    "Keep the boundary explicit. (Select all that should be recorded as out of scope.)",
    "**(Select all that should be recorded as out of scope.)**",
    "(Select all that should be\nrecorded as out of scope.)",
    "**(Select\nall that should be recorded as out of scope.)**",
])
def test_body_multi_select_submits_labels_without_explanatory_context(A, studio, context):
    questions = _parse(studio, f"## Q1. Which capabilities are out?\n\n{context}\n\n"
                               "A. Pause all jobs\nB. Pause with expiry\nX. Other\n\n[Answer]:\n")
    assert _answers(A, questions, ans(1, ["A", "B"])) == "Pause all jobs, Pause with expiry"


def test_an_unanswered_pending_question_and_an_unknown_letter_are_both_refused(A, E, studio):
    questions = _parse(studio, F.questions_text(stage=STAGE, questions=[
        ("One?", ["Yes", "No"]), ("Two?", ["Left", "Right"])]))
    with refused(E, "answers_incomplete") as exc:
        _answers(A, questions, ans(1, ["A"]), grouped=True)
    assert exc.value.details["missing"] == [2]
    with refused(E, "invalid_decision") as exc:
        _answers(A, questions, ans(1, ["Z"]), ans(2, ["A"]), grouped=True)
    assert exc.value.details["unknown"] == ["Z"]


def test_grouped_answers_are_gated_and_golden_when_enabled(A, E, studio):
    """The grouped form is specified but off until S1/S2 (C14): a misaligned reply is unrecoverable."""
    questions = _parse(studio, F.questions_text(stage=STAGE, questions=[
        ("First? ", ["A", "B"]), ("Second? (multi-select)", ["A", "B", "C"]), ("Third?", ["Something else"])]))
    answers = (ans(1, ["B"]), ans(2, ["A", "C"]), ans(3, ["X"], "Use the existing worker queue."))
    with refused(E, "grouped_answers_unavailable") as exc:
        _answers(A, questions, *answers)
    assert exc.value.details["reason"] == "s1_s2_unverified"
    text = _answers(A, questions, *answers, grouped=True)
    assert text == pinned()["golden"]["grouped_answers_example"]
    assert text == "Q1: B\nQ2: A, C\nQ3: Use the existing worker queue." + studio.constants.WIRE_GROUPED_ANSWER_SUFFIX


def test_the_verified_capability_enables_grouped_file_answers(world):
    assert world.settings.capabilities()["grouped_answers"] == {"available": True, "reason": None}


@pytest.mark.parametrize("indexes", [[1, 1], [1, 2], ["1"], [True], [None]])
def test_answer_indexes_must_identify_each_pending_question_once(A, studio, indexes):
    questions = _parse(studio, _questions(single=True))
    with pytest.raises(studio.errors.StudioError):
        _answers(A, questions, *({"index": i, "option_letters": ["A"]} for i in indexes), grouped=True)


def test_multi_select_preserves_regular_choices_alongside_other(A, studio):
    questions = _parse(studio, "## Q1: Checks? (select all that apply)\nA. Alpha\nC. Gamma\nX. Other\n[Answer]:\n")
    assert _answers(A, questions, ans(1, ["A", "X", "C"], 'Custom, "quoted".\nSecond line.'), grouped=True) == (
        'Alpha, Custom, "quoted".\nSecond line., Gamma')


@pytest.mark.parametrize("letters", [["A", "A"], "A"])
def test_duplicate_or_non_list_option_letters_are_refused(A, studio, letters):
    questions = _parse(studio, "## Q1: Checks? (multi-select)\nA. Alpha\n[Answer]:\n")
    with pytest.raises(studio.errors.StudioError):
        _answers(A, questions, {"index": 1, "option_letters": letters}, grouped=True)


@pytest.mark.parametrize("change", ["question_file", "native_question", "host_unavailable"])
@pytest.mark.parametrize("repo_options", [{"questions": _questions(single=True)}])
@aio
async def test_file_answer_rechecks_after_cursor_switch(world, E, monkeypatch, change):
    question = await bound_card(world, "question")
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
    code = {"question_file": "action_stale", "native_question": "slot_busy", "host_unavailable": "host_unavailable"}
    with refused(E, code[change]):
        await submit(world, question, payload={"decision": "answers", "answers": [{"index": 1, "option_letters": ["B"]}]},
                     wire="SQLite")
    assert (await world.broker.get(question.action_id)).delivery_id is None


# --------------------------------------------------------------------------- #
# 3. submit: the happy path and what it never does
# --------------------------------------------------------------------------- #


@aio
async def test_submit_commits_a_delivering_record_and_hands_the_browser_the_send(world, C):
    gate = await bound_card(world)
    receipt = await submit(world, gate)
    assert (receipt.status, receipt.lane, receipt.wire_text, receipt.slot_key) == (
        "Delivering", "human_lane", "Approve", SLOT)
    assert receipt.delivery_id.startswith("dl_") and receipt.lease_generation is not None
    assert (receipt.host.method, receipt.host.path) == ("POST", C.HOST_CHAT_SEND_PATH)
    assert receipt.host.body == {"message": "Approve", "slot": SLOT, "agent": "aidlc", "meta": {
        "studio_action_id": gate.action_id, "studio_delivery_id": receipt.delivery_id}}
    rec = await world.broker.get(gate.action_id)
    assert (rec.status, rec.boot_id) == ("Delivering", BOOT)
    assert rec.presence_baseline is not None
    assert rec.cursor_readback is not None and rec.cursor_readback.ok
    assert rec.deadline_at == C.iso_from_epoch(world.clock.now() + C.DELIVERY_ACK_DEADLINE_SECS)
    assert rec.payload_digest and rec.wire_text == "Approve"
    # The backend sent nothing itself: the slot is untouched and the lease is held for the turn.
    assert world.fake_host.get_slot(SLOT).messages == []
    assert [lease.kind for lease in world.scheduler.live()] == ["execution"]


def test_the_backend_names_no_host_dispatch_symbol_anywhere_it_could_call_one(A):
    """Read as code, not text: the two gateway generations disagree on every one of these signatures (§7.0)."""
    tree = ast.parse((APP_ROOT / "backend" / "studio" / "actions.py").read_text("utf-8"))
    forbidden = {"_run_chat", "spawn_guarded_turn", "get_or_create_slot", "steer", "link_slack"}
    reached = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr in forbidden}
    reached |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in forbidden}
    assert reached == set(), reached


def test_the_delivering_record_is_on_disk_before_the_receipt_is_returned(world, monkeypatch):
    """A crash immediately after the CAS: the durable record must already be there."""

    class Crash(RuntimeError):
        pass

    async def boom(*a, **k):
        raise Crash("gateway died after the CAS")

    async def scenario():
        gate = await bound_card(world)
        monkeypatch.setattr(world.broker, "_record_transition", boom)
        with pytest.raises(Crash):
            await submit(world, gate)
        return gate.action_id

    action_id = asyncio.run(scenario())
    world.store.close()  # re-open the file: nothing of the record may live in the process that crashed
    reopened = world.S.storage.Storage(world.store.path, clock=world.clock)
    reopened.open()
    try:
        row = reopened.get("actions", action_id)
        assert row["status"] == "Delivering" and row["boot_id"] == BOOT
        assert row["delivery_id"] and row["wire_text"] == "Approve"
        assert row["presence_baseline_json"] and row["cursor_readback_json"]
    finally:
        reopened.close()


@pytest.mark.parametrize("field,value", [
    ("state_hash", "0" * 64), ("boundary_token", "1" * 64), ("question_digest", "2" * 64), ("stage_attempt", 9),
    ("evidence_digest", "3" * 64), ("is_active", False),
    ("question_digest", None),  # null versus non-null is a difference too
])
@aio
async def test_compare_and_submit_refuses_on_each_captured_field_independently(world, A, E, field, value):
    gate = await bound_card(world)
    body = captured_of(gate, A, **{field: value})
    assert body[field] != getattr(gate.captured, field), field
    with refused(E, "action_stale") as exc:
        await submit(world, gate, captured=body)
    assert exc.value.details["fields"] == [field]
    assert exc.value.details["card"]["action_id"] == gate.action_id
    # Nothing was dispatched and no lease was taken.
    assert (await world.broker.get(gate.action_id)).status == "Queued"
    assert world.scheduler.live() == []


@aio
async def test_a_boundary_that_moved_on_disk_is_stale_even_when_the_client_agrees_with_the_row(world, A, E):
    gate = await bound_card(world)
    body = captured_of(gate, A)
    world.builder.mutate_state(INTENT, lambda text: text.replace("Revision Count", "Revision  Count"))
    with refused(E, "action_stale") as exc:
        await submit(world, gate, captured=body)
    assert "state_hash" in exc.value.details["fields"]


@aio
async def test_an_unstable_snapshot_is_never_submitted(world, E, monkeypatch):
    gate = await bound_card(world)
    real = world.projection.snapshot
    monkeypatch.setattr(world.projection, "snapshot", lambda *a, **k: dataclasses.replace(real(*a, **k), stable=False))
    with refused(E, "unstable_read"):
        await submit(world, gate)
    assert world.scheduler.live() == []


@aio
async def test_blocking_findings_refuse_the_decision_with_the_evidence(world, E, studio, monkeypatch):
    finding = studio.consistency.Finding(code="gate_without_audit_row", severity="blocking",
                                         message_key="finding.gate_without_audit_row", params={}, evidence=())
    monkeypatch.setattr(world.consistency, "evaluate_intent", lambda *a, **k: [finding])
    gate = await bound_card(world)
    with refused(E, "state_inconsistent") as exc:
        await submit(world, gate)
    assert exc.value.details["findings"][0]["code"] == "gate_without_audit_row"


@aio
async def test_a_client_cannot_choose_the_text_that_is_sent(world, E):
    gate = await bound_card(world)
    with refused(E, "invalid_decision") as exc:
        await submit(world, gate, wire="Approve please")
    assert exc.value.details["expected"] == "Approve"


@aio
async def test_pause_blocks_a_gate_approval_not_only_run(world, E):
    """FR-RUN-003/C29: pause prevents future turns, and a gate approval is a turn."""
    gate = await bound_card(world)
    await world.binder.set_paused(REPO_ID, "default", INTENT, True)
    with refused(E, "intent_paused"):
        await submit(world, gate)
    assert world.scheduler.live() == []
    await world.binder.set_paused(REPO_ID, "default", INTENT, False)
    assert (await submit(world, gate)).status == "Delivering"


@aio
async def test_an_archived_intent_dispatches_nothing(world, E):
    await bind(world)
    # Archiving is refused while a card is live, so the flag is set before any card exists.
    await world.binder.set_archived(REPO_ID, "default", INTENT, True)
    gate = await _row_in(world, "Queued")
    with refused(E, "intent_archived"):
        await submit(world, gate)


@aio
async def test_an_open_breaker_blocks_run_but_leaves_a_gate_decision_alone(world, E):
    await bind(world)
    world.store.insert("breakers", {"key": f"{REPO_ID}:{INTENT}:transport.x", "count": 3, "opened_at": world.clock.iso(),
                                    "reason": "failure.transport", "fingerprint_class": "transport"})
    snap = snapshot(world)
    with refused(E, "breaker_open"):
        await world.broker.create_command("run", world.repo, "default", INTENT, snap=snap)
    receipt = await submit(world, pick(await seed(world), "gate"))
    assert receipt.status == "Delivering", "PRD §11.4 disables Keep moving, not the gate"


@pytest.mark.parametrize("repo_options", [{"revising": 3}])
@aio
async def test_accept_as_is_appears_only_from_the_fourth_attempt(world):
    gate = await bound_card(world)
    assert gate.captured.stage_attempt == 4
    card = await world.broker.card(gate, snapshot(world))
    assert "accept_as_is" in [d["decision"] for d in card["decisions"]]
    receipt = await submit(world, gate, payload={"decision": "accept_as_is"}, wire="Accept as-is")
    assert receipt.wire_text == "Accept as-is"


@aio
async def test_accept_as_is_is_refused_before_the_fourth_attempt(world, E):
    gate = await bound_card(world)
    assert gate.captured.stage_attempt == 1
    card = await world.broker.card(gate, snapshot(world))
    assert "accept_as_is" not in [d["decision"] for d in card["decisions"]]
    with refused(E, "invalid_decision") as exc:
        await submit(world, gate, payload={"decision": "accept_as_is"}, wire="Accept as-is")
    assert exc.value.details["min_attempt"] == 4


@aio
async def test_a_multi_question_group_is_refused_while_the_capability_is_off(world, E, studio, monkeypatch):
    monkeypatch.setattr(studio.constants, "GROUPED_ANSWERS_VERIFIED", False)
    question = await bound_card(world, "question")
    pending = [q for q in snapshot(world).questions.questions if not q.answered]
    assert len(pending) > 1
    answers = [{"index": q.index, "option_letters": [q.options[0].letter] if q.options else [],
                "free_text": None if q.options and not q.options[0].is_other else "text"} for q in pending]
    with refused(E, "grouped_answers_unavailable"):
        await submit(world, question, payload={"decision": "answers", "answers": answers}, wire="whatever")


@aio
async def test_studio_writes_nothing_of_ai_dlcs_while_submitting(world):
    """The engine owns lifecycle, gates, questions, artifacts and audit; dispatch moves only the cursor (§3.3)."""
    record = world.builder.record(INTENT)
    watched = [record / "aidlc-state.md", *sorted((record / "audit").glob("*.md")), record / QUESTIONS_REL,
               record / f"inception/{STAGE}/requirements.md"]
    await bind(world)
    before = {str(path): world.S.security.sha256_file(path) or "" for path in watched}
    assert all(before.values()), "the fixture must really have written those files"
    await submit(world)
    assert {str(path): world.S.security.sha256_file(path) or "" for path in watched} == before


@pytest.mark.parametrize("repo_options", [{"runnable": True}])
@aio
async def test_every_command_decision_sends_its_own_pinned_wire_text(world, C):
    await bind(world)
    snap = snapshot(world)
    for kind, expected in (("run", C.WIRE_RUN), ("prepare_commit", C.WIRE_PREPARE_COMMIT)):
        command = await world.broker.create_command(kind, world.repo, "default", INTENT, snap=snap)
        assert command.risk_class == "human_lane"
        receipt = await submit(world, command, payload={"decision": kind}, wire=expected)
        assert receipt.wire_text == expected and receipt.host.body["message"] == expected
        # Release the repository for the next command in the loop.
        proven, _ = await report(world, command, receipt, "not_delivered", 409, {"error": REFUSAL})
        assert proven.status == "NotDelivered"


@pytest.mark.parametrize("repo_options", [{"questions": _questions(single=True)}])
@aio
async def test_a_single_pending_question_is_answered_with_the_option_label(world):
    """The one answer mapping research 05 §5.1 verified: one reply, one label, no letters."""
    card = await bound_card(world, "question")
    assert card.captured.question_digest is not None
    receipt = await submit(world, card, payload={"decision": "answers", "answers": [ans(1, ["B"])]}, wire="SQLite")
    assert receipt.wire_text == "SQLite" and receipt.host.body["message"] == "SQLite"
    rec = await world.broker.get(card.action_id)
    assert rec.status == "Delivering"
    assert rec.human_text is None, "picking an option is not prose"


@aio
async def test_a_refused_force_stop_needs_no_disk_evidence(world):
    """The host-control lane sends nothing to the model, so there is no disk half to prove."""
    await bind(world)
    receipt = await force_stop(world)
    rec = await world.broker.get(receipt.action_id)
    assert rec.presence_baseline is None
    after, _ = await report(world, rec, receipt, "not_delivered", 404, {"error": "stop refused"})
    assert after.status == "NotDelivered"
    assert after.evidence["checks"] == {"row_absent": True, "slot_idle": True, "disk_baseline_unchanged": True,
                                        "boot_unchanged": True}


@aio
async def test_a_force_stop_receipt_resolves_through_delivered(world):
    await bind(world)
    receipt = await force_stop(world)
    after, idempotent = await report(world, receipt, receipt, "delivered", 200, {"ok": True, "info": "not running"})
    assert (after.status, idempotent) == ("Delivered", False)
    assert after.evidence["turn_started_at"] == after.delivered_at, "a stop is never queued behind a turn"
    assert after.evidence["receipt"]["info"] == "not running"
    assert world.scheduler.live() == [], "the host-control lane never took a lease"


@aio
async def test_a_paused_intent_refuses_a_new_command_card(world, E):
    await world.binder.set_paused(REPO_ID, "default", INTENT, True)
    with refused(E, "intent_paused"):
        await world.broker.create_command("run", world.repo, "default", INTENT, snap=snapshot(world))


@aio
async def test_revision_cards_stay_out_of_the_queue_unless_asked_for(world):
    await _retyped(world, type="revision")
    assert [r.type for r in await world.broker.list_live(repo_id=REPO_ID)] == []
    assert [r.type for r in await world.broker.list_live(repo_id=REPO_ID, include_revision=True)] == ["revision"]


@aio
async def test_a_decision_the_card_does_not_offer_and_a_wrong_lane_are_both_refused(world, E):
    gate = await bound_card(world)
    for payload in ({"decision": "answers"}, {"decision": "acknowledge"}, {"decision": "force_stop"}):
        with refused(E, "invalid_decision"):
            await submit(world, gate, payload=payload)


# --------------------------------------------------------------------------- #
# 4. submit: session preconditions and the lease
# --------------------------------------------------------------------------- #


@aio
async def test_an_unbound_intent_refuses_before_anything_else_happens(world, E):
    gate = pick(await seed(world), "gate")
    with refused(E, "session_unbound"):
        await submit(world, gate)
    assert world.scheduler.live() == []


@aio
async def test_a_slot_that_moved_or_vanished_refuses(world, E):
    gate = await bound_card(world)
    world.fake_host.get_slot(SLOT).project = "/somewhere/else"
    with refused(E, "slot_mismatch"):
        await submit(world, gate)
    world.fake_host._slots.pop(SLOT)
    with refused(E, "slot_mismatch") as exc:
        await submit(world, gate)
    assert exc.value.details["present"] is False


@pytest.mark.parametrize("mutate,reason", [
    (lambda slot: setattr(slot, "running", True), "running"),
    (lambda slot: setattr(slot, "_in_stage_execution", True), "stage_execution"),
    (lambda slot: setattr(slot, "_stop_state", "soft_pending"), "stopping"),
    (lambda slot: setattr(slot, "queue_depth", 2), "queued"),
    (lambda slot: slot._approval_futures.update({"req-1": SimpleNamespace(done=lambda: False)}), "approval_pending"),
])
@aio
async def test_every_busy_predicate_refuses_the_dispatch(world, E, mutate, reason):
    gate = await bound_card(world)
    mutate(world.fake_host.get_slot(SLOT))
    with refused(E, "slot_busy") as exc:
        await submit(world, gate)
    assert reason in exc.value.details["reasons"]
    assert world.scheduler.live() == []


@aio
async def test_a_second_submit_for_the_same_intent_answers_repo_busy(world, E):
    """Two tabs, one repository: the lease is this intent's, so the refusal is the actionable ``repo_busy``."""
    gate = await bound_card(world)
    in_flight(world, intent_uuid=snapshot(world).row.uuid)
    with refused(E, "repo_busy") as exc:
        await submit(world, gate)
    assert exc.value.details["owner"]["action_id"] == "a_other"
    assert (await world.broker.get(gate.action_id)).status == "Queued"


@pytest.mark.parametrize("kind", ["execution", "admin"])
@aio
async def test_other_intent_or_admin_lease_is_busy_not_file_disagreement(world, E, kind):
    gate = await bound_card(world)
    if kind == "execution":
        in_flight(world, intent_uuid="01a00000-0000-7000-8000-000000000002")
        generation = 7
    else:
        grant = await world.scheduler.acquire_admin(world.repo, operation_type="install", transaction_id="tx_other")
        generation = grant.generation
    with refused(E, "repo_busy") as exc:
        await submit(world, gate)
    owner = exc.value.details["owner"]
    assert owner["action_id"] == "a_other" if kind == "execution" else owner["operation_type"] == "install"
    assert world.scheduler.live()[0].generation == generation
    assert (await world.broker.get(gate.action_id)).delivery_id is None
    if kind == "admin":
        await world.scheduler.release(grant)


@pytest.mark.parametrize("repo_options", [{"extra_intent": True}])
@aio
async def test_the_cursor_is_switched_and_read_back_before_the_record_is_committed(world, monkeypatch):
    """FR-SES-006/C22: the conductor resolves the record from the cursor, so asking is not evidence."""
    cursor = world.root / "aidlc" / "spaces" / "default" / "intents" / "active-intent"
    assert cursor.read_text().strip() == OTHER
    gate = await bound_card(world)
    assert gate.captured.is_active is False
    seen: dict[str, str] = {}
    original = world.store.cas_update

    def spy(table, key, expected, values):
        if values.get("status") == "Delivering":
            seen["cursor_at_cas"] = cursor.read_text().strip()
        return original(table, key, expected, values)

    monkeypatch.setattr(world.store, "cas_update", spy)
    receipt = await submit(world, gate)
    assert seen["cursor_at_cas"] == INTENT, "the switch must precede the durable record"
    assert cursor.read_text().strip() == INTENT
    rec = await world.broker.get(receipt.action_id)
    assert rec.cursor_readback is not None and rec.cursor_readback.ok is True
    assert rec.cursor_readback.dir_name == INTENT
    assert rec.cursor_readback.state_sha256 == gate.captured.state_hash


@pytest.mark.parametrize("ok,dir_name,sha,mismatch,expected", [
    (False, "somewhere-else", None, ("active_intent",), ["active_intent"]),
    (True, INTENT, "f" * 64, (), ["state_changed"]),
], ids=["cursor_elsewhere", "switch_changed_the_state_file"])
@aio
async def test_a_refused_read_back_releases_the_lease_and_never_dispatches(
    world, E, studio, monkeypatch, ok, dir_name, sha, mismatch, expected
):
    gate = await bound_card(world)
    readback = studio.engine.CursorReadback(ok=ok, space="default", dir_name=dir_name, uuid=None, state_sha256=sha,
                                            mismatch=mismatch)
    monkeypatch.setattr(world.engine, "switch_cursor_sync", lambda *a, **k: readback)
    with refused(E, "cursor_mismatch") as exc:
        await submit(world, gate)
    assert exc.value.details["mismatch"] == expected
    assert (await world.broker.get(gate.action_id)).status == "Queued"
    assert world.scheduler.live() == [], "an exception after acquire must release the grant"


@aio
async def test_any_failure_after_the_lease_is_taken_releases_it(world, monkeypatch):
    gate = await bound_card(world)

    def boom(*a, **k):
        raise RuntimeError("the audit shard disappeared mid-read")

    monkeypatch.setattr(world.reader, "presence_baseline", boom)
    with pytest.raises(RuntimeError):
        await submit(world, gate)
    assert world.scheduler.live() == []
    assert (await world.broker.get(gate.action_id)).status == "Queued"


@aio
async def test_the_paused_refusal_precedes_the_staleness_refusal(world, A, E):
    """§1.12 step order: 1b (lane, pause, archive, breaker) before 3 (compare-and-submit)."""
    gate = await bound_card(world)
    await world.binder.set_paused(REPO_ID, "default", INTENT, True)
    with refused(E, "intent_paused"):
        await submit(world, gate, captured=captured_of(gate, A, state_hash="0" * 64))


@aio
async def test_staleness_is_refused_before_the_session_preconditions(world, A, E):
    gate = await bound_card(world)
    world.fake_host.get_slot(SLOT).running = True
    with refused(E, "action_stale"):
        await submit(world, gate, captured=captured_of(gate, A, state_hash="0" * 64))


@aio
async def test_a_row_that_is_not_submittable_is_refused_first(world, E):
    gate = await bound_card(world)
    await submit(world, gate)
    with refused(E, "action_not_submittable") as exc:
        await submit(world, gate)
    assert exc.value.details["status"] == "Delivering"


# --------------------------------------------------------------------------- #
# 5. delivery reporting
# --------------------------------------------------------------------------- #


@aio
async def test_a_delivered_receipt_starts_the_turn_in_one_transition(world, C):
    """The host ran it now, so ``Delivered`` already carries ``turn_started_at`` and the turn's deadline."""
    rec, receipt = await delivering(world)
    after, idempotent = await report(world, rec, receipt, "delivered", 200, {"ok": True, "slot": SLOT, "mid": "m1"})
    assert idempotent is False and after.status == "Delivered"
    assert after.delivered_at and after.evidence["turn_started_at"] == after.delivered_at
    assert "queued_at" not in after.evidence
    assert after.evidence["receipt"]["mid"] == "m1"
    assert after.deadline_at == C.iso_from_epoch(world.clock.now() + world.host.turn_timeout_secs())
    assert (await world.broker.card(after, None))["delivery"]["turn_started_at"] == after.delivered_at
    transitions = world.store.select("action_transitions", {"action_id": rec.action_id})
    assert [(t["from_status"], t["to_status"]) for t in transitions][-1:] == [("Delivering", "Delivered")]


@aio
async def test_a_queued_receipt_waits_in_delivered(world, C):
    rec, receipt = await delivering(world)
    after, _ = await report(world, rec, receipt, "delivered", 200, {"ok": True, "queued": True})
    assert after.status == "Delivered", "the processing clock starts when the turn starts (P22)"
    assert after.evidence["queued_at"] and "turn_started_at" not in after.evidence
    assert after.deadline_at == C.iso_from_epoch(
        world.clock.now() + C.DELIVERED_QUEUED_WAIT_FACTOR * world.host.turn_timeout_secs())


@aio
async def test_a_delivered_report_without_the_hosts_ok_is_refused(world, E):
    rec, receipt = await delivering(world)
    with refused(E, "delivery_ack_invalid"):
        await report(world, rec, receipt, "delivered", 200, {"queued": True})
    assert (await world.broker.get(rec.action_id)).status == "Delivering"


@aio
async def test_an_uncertain_report_gives_the_repository_back(world):
    """Only in-flight rows hold a repository; the reconciler still owns the uncertain one."""
    await uncertain(world)
    assert world.scheduler.live() == []


@aio
async def test_not_delivered_is_accepted_only_with_the_backends_own_proof(world):
    rec, receipt = await delivering(world)
    after, idempotent = await report(world, rec, receipt, "not_delivered", 409,
                                     {"error": REFUSAL, "code": "slot_agent_mismatch"})
    assert idempotent is False and after.status == "NotDelivered"
    assert after.evidence["checks"] == {"row_absent": True, "slot_idle": True, "disk_baseline_unchanged": True,
                                        "boot_unchanged": True}
    assert world.scheduler.live() == [], "a proven non-delivery gives the repository back"


@pytest.mark.parametrize("broken", ["row_absent", "slot_idle", "disk_baseline_unchanged"])
@aio
async def test_a_broken_proof_yields_reconciliation_required_and_never_not_delivered(world, E, broken):
    rec, receipt = await delivering(world)
    if broken == "row_absent":
        world.fake_host.append_user_row(SLOT, "Approve", TS)
    elif broken == "slot_idle":
        world.fake_host.set_running(SLOT, True)
    else:
        world.builder.append_audit(INTENT, F.audit_block("HUMAN_TURN", F._plus(TS, 5)))
    with refused(E, "not_delivered_unproven") as exc:
        await report(world, rec, receipt, "not_delivered", 409, {"error": REFUSAL})
    assert exc.value.details["checks"][broken] is False
    after = await world.broker.get(rec.action_id)
    assert after.status == "ReconciliationRequired", "the host may have enqueued it after all"
    assert after.evidence["receipt"]["error"] == REFUSAL


@aio
async def test_a_not_delivered_report_without_an_authoritative_receipt_is_unproven(world, E):
    rec, receipt = await delivering(world)
    with refused(E, "not_delivered_unproven") as exc:
        await report(world, rec, receipt, "not_delivered", 500, {"error": "gateway exploded"})
    assert exc.value.details["reason"] == "receipt_not_authoritative"
    # The refusal still says what was and was not observed, so the user can act on it (§2.4).
    assert set(exc.value.details["checks"]) == {"row_absent", "slot_idle", "disk_baseline_unchanged", "boot_unchanged"}
    assert (await world.broker.get(rec.action_id)).status == "ReconciliationRequired"


@aio
async def test_a_slot_missing_preflight_code_is_believed_only_when_the_slot_is_gone(world):
    rec, receipt = await delivering(world)
    world.fake_host._slots.pop(SLOT)
    after, _ = await report(world, rec, receipt, "not_delivered", None, {"ok": False, "code": "slot_missing"})
    assert after.status == "NotDelivered"
    assert after.evidence["client_preflight"] == "slot_missing" and after.evidence["checks"]["slot_idle"] is True


@aio
async def test_a_preflight_code_the_backend_can_disprove_is_refused(world, E):
    """The browser says the slot was missing, ``HostBridge`` says it is right there: not proven."""
    rec, receipt = await delivering(world)
    with refused(E, "not_delivered_unproven") as exc:
        await report(world, rec, receipt, "not_delivered", None, {"ok": False, "code": "slot_missing"})
    assert exc.value.details["checks"]["slot_idle"] is False
    assert (await world.broker.get(rec.action_id)).status == "ReconciliationRequired"


@aio
async def test_a_restart_between_the_send_and_the_ack_can_never_prove_non_delivery(world, E):
    """review R02: the host's transcript is process memory, so a restart looks like 'never sent'."""
    rec, receipt = await delivering(world)
    restarted = _broker(world, "boot-test-0002")
    world.fake_host._slots.clear()
    with refused(E, "not_delivered_unproven") as exc:
        await report(world, rec, receipt, "not_delivered", 409, {"error": REFUSAL}, broker=restarted)
    assert exc.value.details["checks"]["boot_unchanged"] is False
    assert (await restarted.get(rec.action_id)).status == "ReconciliationRequired"


@aio
async def test_delivery_reports_are_idempotent_and_a_conflicting_one_is_refused(world, E):
    rec, receipt = await delivering(world)
    first, idem1 = await report(world, rec, receipt, "delivered", 200, {"ok": True})
    again, idem2 = await report(world, rec, receipt, "delivered", 200, {"ok": True})
    assert (idem1, idem2) == (False, True)
    assert again.status == first.status == "Delivered"
    assert again.status_generation == first.status_generation
    with pytest.raises(E.IllegalTransition):
        await report(world, rec, receipt, "uncertain", None, None)
    with refused(E, "delivery_ack_invalid"):
        await world.broker.record_delivery(rec.action_id, delivery_id="dl_not_this_one", outcome="delivered",
                                           http_status=200, receipt={"ok": True})


@aio
async def test_a_late_delivered_report_marks_the_decision_confirmed_and_blocks_replay(world, E):
    rec, receipt = await delivering(world)
    await report(world, rec, receipt, "uncertain", None, None)
    after, idempotent = await report(world, rec, receipt, "delivered", 200, {"ok": True, "mid": "late"})
    assert idempotent is False
    assert after.status == "ReconciliationRequired", "a late report records evidence, not a status"
    assert after.evidence["delivery_confirmed"] is True
    with refused(E, "not_delivered_unproven"):
        await world.broker.resolve(after.action_id, decision="resubmit",
                                   payload={"acknowledged_evidence_sha256": "whatever"}, user="owner-1")


@aio
async def test_a_late_not_delivered_report_needs_a_4xx_and_all_four_checks(world, E):
    """From ``ReconciliationRequired`` only the host's own 4xx proves absence (a client preflight code is believed
    only while ``Delivering``), and only with all four checks holding."""
    rec, receipt = await delivering(world)
    assert (await report(world, rec, receipt, "uncertain", None, None))[0].status == "ReconciliationRequired"
    world.fake_host._slots.pop(SLOT)
    with refused(E, "not_delivered_unproven") as exc:
        await report(world, rec, receipt, "not_delivered", None, {"ok": False, "code": "slot_missing"})
    assert exc.value.details["reason"] == "receipt_not_authoritative"
    unproven = await world.broker.get(rec.action_id)
    assert unproven.status == "ReconciliationRequired" and unproven.evidence["late_report_at"]

    world.fake_host.add_slot(SLOT, project=str(world.root), agent="aidlc", app="")
    world.fake_host.append_user_row(SLOT, "Approve", TS)
    with refused(E, "not_delivered_unproven") as seen:
        await report(world, rec, receipt, "not_delivered", 409, {"error": REFUSAL})
    assert seen.value.details["checks"]["row_absent"] is False
    assert (await world.broker.get(rec.action_id)).status == "ReconciliationRequired"

    world.fake_host.get_slot(SLOT).messages.clear()
    proven, idempotent = await report(world, rec, receipt, "not_delivered", 409, {"error": REFUSAL})
    assert (proven.status, idempotent) == ("NotDelivered", False)
    assert all(proven.evidence["checks"].values()) and proven.evidence["authoritative_4xx"] is True


@aio
async def test_an_evidence_patch_writes_only_what_changed(world):
    """An unchanged re-stamp must not bump the generation (a quiet card would go stale every scan); a change must."""
    rec = await uncertain(world)
    first = await world.broker.patch_evidence(rec, {"delivery_confirmed": True})
    assert first.status_generation == rec.status_generation + 1
    assert first.evidence["delivery_confirmed"] is True and first.status == rec.status
    before = world.store.get("actions", rec.action_id)
    again = await world.broker.patch_evidence(first, {"delivery_confirmed": True})
    after = world.store.get("actions", rec.action_id)
    assert again.status_generation == first.status_generation
    assert (after["updated_at"], after["evidence_json"]) == (before["updated_at"], before["evidence_json"])
    # A lost CAS is not an error: the other writer's row is at least as new.
    stale = await world.broker.patch_evidence(rec, {"other": 1})
    assert stale.status_generation == first.status_generation and "other" not in stale.evidence


# --------------------------------------------------------------------------- #
# 6. resubmit, mark_not_delivered, retry, cancel
# --------------------------------------------------------------------------- #


@aio
async def test_resubmit_needs_the_evidence_hash_the_ui_displayed(world, E):
    rec = await uncertain(world)
    with refused(E, "action_stale") as exc:
        await world.broker.resolve(rec.action_id, decision="resubmit",
                                   payload={"acknowledged_evidence_sha256": "0" * 64}, user="owner-1")
    assert exc.value.details["card"]["acknowledged_evidence_sha256"] == exc.value.details["expected"]


@aio
async def test_resubmit_supersedes_the_old_row_and_keeps_one_live_card_per_boundary(world):
    rec = await uncertain(world)
    card = await world.broker.card(rec, None)
    old, created = await world.broker.resolve(
        rec.action_id, decision="resubmit", user="owner-1",
        payload={"acknowledged_evidence_sha256": card["acknowledged_evidence_sha256"]})
    assert old.status == "Failed" and old.resolution["reason"] == "superseded_by_resubmit"
    assert created is not None and created.action_id != old.action_id
    assert created.status == "Queued"
    assert created.dedupe_key == old.dedupe_key, "the same boundary keeps its key"
    assert created.evidence["superseded_action_id"] == old.action_id
    live = [r for r in await world.broker.list_live(repo_id=REPO_ID, intent_dir=INTENT)
            if r.dedupe_key == old.dedupe_key and r.status != "Failed"]
    assert [r.action_id for r in live] == [created.action_id]


@aio
async def test_resubmit_and_mark_not_delivered_only_exist_in_reconciliation_required(world, E):
    """R07: only once the reconciler looked and could not tell; a recovery card naming ``mark_not_delivered``
    (always Queued) is refused by its status."""
    rec, receipt = await delivering(world)
    delivered, _ = await report(world, rec, receipt, "delivered", 200, {"ok": True})
    for decision in ("resubmit", "mark_not_delivered"):
        with refused(E, "invalid_decision"):
            await world.broker.resolve(delivered.action_id, decision=decision, payload={}, user="owner-1")
    recovery = await _retyped(world, type="recovery")
    with pytest.raises(E.IllegalTransition) as illegal:
        await world.broker.resolve(recovery.action_id, decision="mark_not_delivered", payload={}, user="owner-1")
    assert illegal.value.details["from"] == "Queued"
    assert (await world.broker.get(recovery.action_id)).status == "Queued"


@aio
async def test_mark_not_delivered_needs_a_receipt_or_the_reconcilers_proof(world, E):
    rec = await uncertain(world)
    with refused(E, "not_delivered_unproven"):
        await world.broker.resolve(rec.action_id, decision="mark_not_delivered", payload={}, user="owner-1")
    with_receipt, _ = await world.broker.resolve(
        rec.action_id, decision="mark_not_delivered", user="owner-1",
        payload={"evidence": {"http_status": 409, "receipt": {"error": REFUSAL}}})
    assert with_receipt.status == "NotDelivered"
    assert world.scheduler.live() == []


@aio
async def test_mark_not_delivered_accepts_the_reconcilers_four_proofs(world):
    rec = await uncertain(world)
    await world.broker.patch_evidence(rec, {"transcript_row_absent": True, "slot_ran_since": False,
                                            "disk_baseline_unchanged": True, "boot_id_unchanged": True})
    after, _ = await world.broker.resolve(rec.action_id, decision="mark_not_delivered", payload={}, user="owner-1")
    assert after.status == "NotDelivered"


@aio
async def test_retry_requeues_a_proven_non_delivery_and_refuses_a_confirmed_one(world, E):
    rec, receipt = await delivering(world)
    proven, _ = await report(world, rec, receipt, "not_delivered", 409, {"error": REFUSAL})
    requeued = await world.broker.retry(proven.action_id, user="owner-1")
    assert requeued.status == "Queued"
    assert requeued.action_id == proven.action_id, "at-most-once is intact: nothing was enqueued"
    confirmed = await world.broker.patch_evidence(requeued, {"delivery_confirmed": True})
    moved = await move(world, confirmed, "Delivering", "NotDelivered", reason="pretend")
    with refused(E, "retry_not_allowed"):
        await world.broker.retry(moved.action_id, user="owner-1")


@pytest.mark.parametrize("repo_options", [{"runnable": True}])
@aio
async def test_retry_of_a_failed_run_creates_a_new_action_without_a_dedupe_collision(world):
    await bind(world)
    snap = snapshot(world)
    command = await world.broker.create_command("run", world.repo, "default", INTENT, snap=snap)
    again = await world.broker.create_command("run", world.repo, "default", INTENT, snap=snap)
    assert again.action_id == command.action_id, "one live command per boundary"
    moved = await move(world, command, "Delivering", "Delivered", reason="drive to a running turn")
    failed, opened = await _fail(world, moved, "validation.unknown_intent:abc123", "validation", stderr="Unknown intent")
    assert failed.status == "Failed" and opened is True
    retried = await world.broker.retry(failed.action_id, user="owner-1")
    assert retried.action_id != failed.action_id and retried.status == "Queued"
    assert retried.dedupe_key == failed.dedupe_key
    assert (await world.broker.get(failed.action_id)).evidence["superseded"] is True
    # The breaker the failure opened was reset by the retry, so the new run is dispatchable.
    assert world.store.get("breakers", f"{REPO_ID}:{INTENT}:validation.unknown_intent:abc123")["opened_at"] is None


@aio
async def test_cancel_is_allowed_for_commands_and_refused_for_a_live_derived_card(world, E):
    await bind(world)
    command = await world.broker.create_command("prepare_commit", world.repo, "default", INTENT, snap=snapshot(world))
    assert (await world.broker.cancel(command.action_id, user="owner-1")).status == "Cancelled"
    gate = pick(await seed(world), "gate")
    with refused(E, "cancel_not_safe"):
        await world.broker.cancel(gate.action_id, user="owner-1")
    rec, receipt = await delivering(world)
    proven, _ = await report(world, rec, receipt, "not_delivered", 409, {"error": REFUSAL})
    assert (await world.broker.cancel(proven.action_id, user="owner-1")).status == "Cancelled"


# --------------------------------------------------------------------------- #
# 7. force stop, studio-only decisions
# --------------------------------------------------------------------------- #


@aio
async def test_force_stop_commits_delivering_with_the_stop_call_and_no_lease(world):
    await bind(world)
    receipt = await force_stop(world)
    assert (receipt.lane, receipt.wire_text, receipt.lease_generation) == ("host_control", None, None)
    assert receipt.host.path == f"/api/chat/slots/{SLOT}/stop" and receipt.host.body == {}
    rec = await world.broker.get(receipt.action_id)
    assert (rec.status, rec.boot_id) == ("Delivering", BOOT)
    assert rec.presence_baseline is None, "nothing was sent to the model"
    assert rec.cursor_readback is None
    assert world.scheduler.live() == []
    again = await force_stop(world)
    assert again.action_id == receipt.action_id and again.delivery_id == receipt.delivery_id


@aio
async def test_force_stop_without_a_bound_slot_is_refused(world, E):
    with refused(E, "session_unbound"):
        await force_stop(world)


@pytest.mark.parametrize("repo_options", [{"extra_intent": True}])
@aio
async def test_pick_intent_switches_the_cursor_under_an_admin_lease_and_never_sends_a_prompt(world):
    cursor = world.root / "aidlc" / "spaces" / "default" / "intents" / "active-intent"
    await seed(world, OTHER)
    # With the cursor on OTHER, INTENT derives `missing_input` only when it dangles, so point it at nothing.
    cursor.write_text("no-such-intent\n")
    missing = pick(await seed(world), "missing_input")
    assert "pick_intent" in missing.card_meta["decisions"]
    updated, created = await world.broker.resolve(missing.action_id, decision="pick_intent",
                                                  payload={"intent_dir": INTENT}, user="owner-1")
    assert created is None and cursor.read_text().strip() == INTENT
    assert updated.status == "Cancelled" and updated.resolution["reason"] == "cursor_switched"
    assert world.scheduler.live() == [], "the admin lease is released"
    assert world.fake_host.get_slot(SLOT).messages == [], "picking an intent is never a prompt"


@aio
async def test_acknowledge_clears_the_interruption_only_for_that_finding(world):
    await bind(world)
    await world.binder.set_interrupted(REPO_ID, "default", INTENT, world.clock.iso())
    recovery = await _retyped(world, type="recovery", evidence_json={  # a recovery card carrying the finding
        "findings": [{"code": "interrupted_session", "params": {}}],
        "card": {"decisions": ["acknowledge"], "reason": "interrupted_session"}})
    updated, _ = await world.broker.resolve(recovery.action_id, decision="acknowledge", payload={}, user="owner-1")
    assert updated.status == "Cancelled"
    assert (await world.binder.get(REPO_ID, "default", INTENT)).interrupted_at is None


@aio
async def test_keep_paused_pauses_the_intent_and_closes_the_card(world):
    card = await _retyped(world, type="circuit_breaker", risk_class="studio_only",
                          evidence_json={"card": {"decisions": ["retry_now", "keep_paused"]}})
    updated, created = await world.broker.resolve(card.action_id, decision="keep_paused", payload={}, user="owner-1")
    assert created is None and updated.status == "Cancelled"
    assert (await world.binder.get(REPO_ID, "default", INTENT)).paused is True


@aio
async def test_a_studio_only_decision_is_refused_on_the_wrong_card(world, E):
    gate = await bound_card(world)
    for decision in ("acknowledge", "approve"):
        with refused(E, "invalid_decision"):
            await world.broker.resolve(gate.action_id, decision=decision, payload={}, user="owner-1")


# --------------------------------------------------------------------------- #
# 8. the breaker
# --------------------------------------------------------------------------- #


async def _running_command(w, kind: str = "run"):
    """A command whose turn is running, from a runnable stage: these tests exercise delivery and failure."""
    record = w.builder.record(INTENT)
    (record / "aidlc-state.md").write_text(_state_with({STAGE: "-"}))
    (record / QUESTIONS_REL).unlink(missing_ok=True)
    for shard in (record / "audit").glob("*.md"):
        shard.write_text(F.audit_text(F.audit_block("HUMAN_TURN", TS),
                                      F.audit_block("STAGE_STARTED", F._plus(TS, 1), Stage=STAGE)))
    await bind(w)
    rec = await w.broker.create_command(kind, w.repo, "default", INTENT, snap=snapshot(w))
    return await move(w, rec, "Delivering", "Delivered")


async def _fail(w, rec, fingerprint: str, fingerprint_class: str, **evidence):
    return await w.broker.record_failure(rec, fingerprint=fingerprint, fingerprint_class=fingerprint_class,
                                         summary=f"failure.{fingerprint_class}", evidence=evidence)


@aio
async def test_three_transient_failures_open_the_breaker_once(world):
    opened_flags = []
    for attempt in range(3):
        failed, opened = await _fail(world, await _running_command(world), "transport.stream_closed:abc123",
                                     "transport", stderr="stream closed")
        opened_flags.append(opened)
        assert failed.evidence["failure"]["count"] == attempt + 1
        await world.broker.patch_evidence(failed, {"superseded": True})  # retire it so the next attempt can run
    assert opened_flags == [False, False, True], "the third identical transport error is not transient"
    breaker = world.store.get("breakers", f"{REPO_ID}:{INTENT}:transport.stream_closed:abc123")
    assert breaker["count"] == 3 and breaker["opened_at"]
    cards = [r for r in await world.broker.list_live(repo_id=REPO_ID, intent_dir=INTENT) if r.type == "circuit_breaker"]
    assert len(cards) == 1, "exactly one card per opening"
    assert len(world.notifications.sent) == 1, "exactly one notification per opening"
    assert world.notifications.sent[0]["type"] == "circuit_breaker"
    assert [row["kind"] for row in world.store.select("activity")].count("breaker.opened") == 1


@aio
async def test_a_deterministic_failure_opens_the_breaker_immediately(world):
    failed, opened = await _fail(world, await _running_command(world), "guard.refusing_to_record:def456", "guard",
                                 stderr="Refusing to record this answer")
    assert opened is True and failed.evidence["failure"]["breaker_open"] is True
    assert failed.retry_fingerprint == "guard.refusing_to_record:def456"
    assert len(world.notifications.sent) == 1
    card = world.notifications.sent[0]
    assert card["failure"]["fingerprint_class"] == "guard" and card["severity"] == "attention"


@pytest.mark.parametrize("repo_options", [{"runnable": True}])
@aio
async def test_a_failure_can_only_be_recorded_against_an_inflight_action(world, E):
    await bind(world)
    rec = await world.broker.create_command("run", world.repo, "default", INTENT, snap=snapshot(world))
    with pytest.raises(E.IllegalTransition):
        await _fail(world, rec, "config.x:1", "config")


@aio
async def test_retry_now_on_a_failure_card_resets_the_breaker_and_starts_a_new_attempt(world):
    failed, opened = await _fail(world, await _running_command(world), "guard.x:1", "guard")
    assert opened is True
    # The `failure` card itself is derived by the projection (tested there); what matters is what `retry_now` does.
    failure_card = await _retyped(world, type="failure", risk_class="studio_only", evidence_json={
        "card": {"decisions": ["retry_now", "keep_paused"], "related_action_id": failed.action_id}})
    updated, created = await world.broker.resolve(failure_card.action_id, decision="retry_now", payload={},
                                                  user="owner-1")
    assert updated.status == "Cancelled"
    assert created is not None and created.type == "run" and created.status == "Queued"
    assert created.action_id != failed.action_id
    assert world.store.get("breakers", f"{REPO_ID}:{INTENT}:guard.x:1")["opened_at"] is None


@aio
async def test_a_failure_whose_status_cas_is_lost_leaves_no_strike_on_the_breaker(world, E):
    """Two actors classify one action; the loser's ``StaleGeneration`` must bank no breaker strike (§11.4)."""
    rec = await uncertain(world)
    stale = rec  # the loser's snapshot, taken before the race
    world.store.cas_update("actions", rec.action_id, rec.status_generation, {"waiting_since": world.clock.iso()})
    with pytest.raises(E.StaleGeneration):
        await _fail(world, stale, "transport:abc123", "transport", why="the CAS is already lost")
    assert world.store.select("breakers") == [], "a refused failure marks nothing"
    # The winner's identical failure does count, exactly once.
    failed, opened = await _fail(world, await world.broker.get(rec.action_id), "transport:abc123", "transport",
                                 why="this one commits")
    assert failed.status == "Failed" and opened is False, "one transient strike does not open it"
    rows = world.store.select("breakers")
    assert [int(r["count"]) for r in rows] == [1], rows
    assert failed.evidence["failure"]["count"] == 1


# --------------------------------------------------------------------------- #
# 9. derivation, dedupe and the card
# --------------------------------------------------------------------------- #


@aio
async def test_one_live_card_per_boundary_and_terminal_rows_keep_their_key(world):
    first = await seed(world)
    again = await seed(world)
    assert {r.action_id for r in first} == {r.action_id for r in again}
    gate = pick(again, "gate")
    # A cancelled twin must not stop the boundary from having a live card again (FR-ACT-007).
    cancelled = await move(world, gate, "Cancelled", reason="dismissed")
    rederived = pick(await seed(world), "gate")
    assert rederived.action_id != cancelled.action_id and rederived.dedupe_key == cancelled.dedupe_key
    assert rederived.evidence["superseded_action_id"] == cancelled.action_id
    assert len(world.store.select("actions", {"dedupe_key": gate.dedupe_key})) == 2


@pytest.mark.parametrize("type_", ["gate", "question"])
@aio
async def test_clock_only_refresh_preserves_queued_generation_and_live_capture_time(world, A, type_):
    rec = await bound_card(world, type_)
    before = world.store.get("actions", rec.action_id)
    for _ in range(3):
        world.clock.advance(5)
        refreshed = pick(await seed(world), type_)
        assert refreshed.action_id == rec.action_id
        assert world.store.get("actions", rec.action_id) == before
        assert refreshed.captured.captured_at == rec.captured.captured_at
        # GET still reports the time of its fresh read, not the persisted version's first look.
        card = await world.broker.card(refreshed, snapshot(world))
        assert card["captured"]["captured_at"] == world.clock.iso() and card["captured"]["stable"] is True
        assert A.Captured.from_json(card["captured"]).differences(rec.captured) == []
        assert world.store.get("actions", rec.action_id) == before


@pytest.mark.parametrize("competing_submit", [False, True])
@pytest.mark.parametrize("repo_options", [{"questions": _questions(single=True)}])
@aio
async def test_clock_only_refresh_during_submit_keeps_one_delivery(world, E, monkeypatch, competing_submit):
    rec = await bound_card(world, "question")
    payload = {"decision": "answers", "answers": [{"index": 1, "option_letters": ["B"]}]}
    refreshes = []

    async def refresh_before_cas():
        world.clock.advance(5)
        refreshes.append(pick(await seed(world), "question"))
        if competing_submit:  # the first request holds the lease but has not committed Delivering yet
            with refused(E, "repo_busy"):
                await submit(world, rec, payload=payload, wire="SQLite", user="owner-2")

    before_the_delivering_cas(world, monkeypatch, refresh_before_cas)
    receipt = await submit(world, rec, payload=payload, wire="SQLite")
    assert len(refreshes) == 1 and refreshes[0].status_generation == rec.status_generation
    after = await world.broker.get(rec.action_id)
    assert after.status == receipt.status == "Delivering"
    assert after.status_generation == rec.status_generation + 1
    assert (after.delivery_id, after.wire_text) == (receipt.delivery_id, "SQLite")
    assert after.captured.captured_at == rec.captured.captured_at
    C = world.S.constants
    assert C.epoch_from_iso(receipt.expires_at) - C.epoch_from_iso(after.delivering_at) == C.DELIVERY_ACK_DEADLINE_SECS
    transitions = world.store.select("action_transitions", {"action_id": rec.action_id})
    assert [(row["from_status"], row["to_status"]) for row in transitions] == [("Queued", "Delivering")]
    held = world.scheduler.live()
    assert len(held) == 1 and held[0].action_id == rec.action_id
    assert world.fake_host.get_slot(SLOT).messages == []


@pytest.mark.parametrize("repo_options", [{"questions": _questions(single=True)}])
@aio
async def test_meaningful_refresh_during_submit_still_refuses_the_original_generation(world, E, monkeypatch):
    rec = await bound_card(world, "question")
    refreshed = []

    async def change_evidence():
        world.clock.advance(5)
        path = world.builder.record(INTENT) / f"inception/{STAGE}/requirements.md"
        path.write_text(path.read_text() + "\nA changed requirement must be reviewed.\n")
        refreshed.append(pick(await seed(world), "question"))

    before_the_delivering_cas(world, monkeypatch, change_evidence)
    with pytest.raises(E.StaleGeneration) as exc:
        await submit(world, rec, payload={"decision": "answers", "answers": [{"index": 1, "option_letters": ["B"]}]},
                     wire="SQLite")
    assert (exc.value.details["expected"], exc.value.details["actual"]) == (
        rec.status_generation, rec.status_generation + 1)
    after = await world.broker.get(rec.action_id)
    assert len(refreshed) == 1 and refreshed[0].action_id == rec.action_id
    assert after.status == "Queued" and after.delivery_id is None
    assert after.captured.captured_at == world.clock.iso()
    assert after.captured.differences(rec.captured) == ["evidence_digest"]
    assert world.store.select("action_transitions", {"action_id": rec.action_id}) == []
    assert world.scheduler.live() == []
    assert world.fake_host.get_slot(SLOT).messages == []


@aio
async def test_a_refresh_touches_a_queued_card_and_never_an_inflight_one(world):
    gate = await bound_card(world)
    unchanged = pick(await seed(world), "gate")
    assert unchanged.status_generation == gate.status_generation, "no write when nothing moved"
    world.clock.advance(30)
    world.builder.append_audit(INTENT, F.audit_block("SESSION_STARTED", F._plus(TS, 30)))
    refreshed = pick(await seed(world), "gate")
    assert refreshed.status_generation > gate.status_generation
    assert refreshed.captured.captured_at == world.clock.iso()
    assert refreshed.evidence["audit"]["last_event"]["type"] == "SESSION_STARTED"
    receipt = await submit(world, refreshed)
    frozen = await world.broker.get(receipt.action_id)
    world.clock.advance(30)
    world.builder.append_audit(INTENT, F.audit_block("SESSION_STARTED", F._plus(TS, 60)))
    await seed(world)
    after = await world.broker.get(receipt.action_id)
    assert after.status_generation == frozen.status_generation
    assert after.captured.captured_at == frozen.captured.captured_at
    assert after.evidence["audit"] == frozen.evidence["audit"], "an in-flight record is not rewritten"


@pytest.mark.parametrize("repo_options", [{"second_gate": True}])
@aio
async def test_a_second_open_gate_card_shows_its_own_stage_and_not_the_current_one(world):
    """``card()`` re-derives live evidence for the card's OWN boundary (A20), or the other gate's approver sees
    the current stage's files and verdict while its correctly scoped ``captured`` lets the submit through."""
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


@aio
async def test_a_queued_card_picks_up_a_conversation_bound_after_it_was_derived(world):
    """A card derived before the bind must stop refusing once bound (§2.13); a dispatched one keeps its slot."""
    gate = pick(await seed(world), "gate")
    assert (await world.broker.card(gate, snapshot(world)))["evidence"]["session"] is None, "nothing is bound yet"
    await bind(world)
    assert (await world.broker.card(gate, snapshot(world)))["evidence"]["session"]["slot_key"] == SLOT
    receipt = await submit(world, await world.broker.get(gate.action_id))
    # `unbind` refuses while a decision is in flight, so the binding row is moved under the broker instead.
    key = f"{REPO_ID}:default:{INTENT}"
    generation = int(world.store.get("intent_bindings", key)["binding_generation"])
    world.store.cas_update("intent_bindings", key, generation, {"slot_key": "aidlc-studio-somewhere-else"})
    card = await world.broker.card(await world.broker.get(receipt.action_id), snapshot(world))
    assert card["evidence"]["session"]["slot_key"] == SLOT, "a card on the wire keeps what the human decided against"


@aio
async def test_retire_stale_cancels_a_queued_card_and_only_marks_a_failed_one(world):
    gate = pick(await seed(world), "gate")
    rec = await _retyped(world, "Failed", dedupe_key="gate:gone:forever")
    await world.broker.retire_stale(REPO_ID, "default", INTENT, live_keys=set())
    assert (await world.broker.get(gate.action_id)).status == "Cancelled"
    failed = await world.broker.get(rec.action_id)
    assert failed.status == "Failed", "Failed is terminal (review P07)"
    assert failed.evidence["superseded"] is True


@pytest.mark.parametrize("checkpoint,title,options", [
    ("summary_confirmation", "Consolidated Summary Confirmation", "- Looks correct\n- Request changes"),
    ("plan_approval", "Plan Approval", "- Approve Plan\n- Request Changes"),
])
@aio
async def test_checkpoint_card_does_not_claim_zero_unanswered_questions(world, checkpoint, title, options):
    (world.builder.record(INTENT) / QUESTIONS_REL).write_text(f"## {title}\n\n{options}\n\n[Answer]:\n")
    question = pick(await seed(world), "question")
    card = await world.broker.card(question, snapshot(world))
    assert card["evidence"]["questions"]["pending_count"] == 0
    assert card["headline"]["key"] == f"action.question.{checkpoint}.headline"
    assert card["consequence"]["key"] == f"action.question.consequence.{checkpoint}"


@aio
async def test_assumption_confirmation_offers_its_two_options_as_their_exact_text(world):
    (world.builder.record(INTENT) / QUESTIONS_REL).write_text(
        "## Assumption Confirmation\n\n- Keeps one item open [assumption]\n\n"
        "A. Accept assumptions\nB. Convert to follow-up questions\n\n[Answer]:\n")
    question = await bound_card(world, "question")
    card = await world.broker.card(question, snapshot(world))
    offered = {spec["decision"]: spec for spec in card["decisions"]}
    assert set(offered) == {"accept_assumptions", "convert_assumptions"}
    assert offered["accept_assumptions"]["wire_text_template"] == "A. Accept assumptions"
    assert offered["convert_assumptions"]["wire_text_template"] == "B. Convert to follow-up questions"
    assert card["headline"]["key"] == "action.question.assumption_confirmation.headline"
    assert card["evidence"]["questions"]["assumption_confirmation"]["context"] == "- Keeps one item open [assumption]"
    receipt = await submit(world, question, payload={"decision": "convert_assumptions"},
                           wire="B. Convert to follow-up questions", user="owner")
    assert receipt.wire_text == receipt.host.body["message"] == "B. Convert to follow-up questions"


@pytest.mark.parametrize("payload,wire", [
    ({"decision": "answers", "answers": [{"index": 1, "option_letters": ["A"]}]}, "Store"),
    ({"decision": "confirm_summary", "choice": "looks_correct"}, "Looks correct"),
    ({"decision": "confirm_summary", "choice": "request_changes", "feedback": "Clarify"}, "Request changes: Clarify"),
    ({"decision": "approve_plan"}, "Approve Plan"),
    ({"decision": "request_plan_changes", "feedback": "Clarify"}, "Request Changes: Clarify"),
])
@aio
async def test_unsupported_followups_refuse_cached_card_decisions_before_delivery(world, A, E, payload, wire):
    path = world.builder.record(INTENT) / QUESTIONS_REL
    text = ("## Q1. Store?\nA. Store\n[Answer]:\n\n## F1. Follow-up\n[Answer]:\n\n"
            "## Consolidated Summary Confirmation\n- Looks correct\n- Request changes\n[Answer]:\n\n"
            "## Plan Approval\n- Approve Plan\n- Request Changes\n[Answer]:\n")
    path.write_text(text)
    question = await bound_card(world, "question")
    # A cached card from the older parser can carry decisions and lack the new field.
    legacy_view = dict(question.evidence["questions"])
    legacy_view.pop("unsupported_pending_count")
    legacy_view.update(mode="structured", pending_count=1)
    legacy = dataclasses.replace(question, evidence={
        **question.evidence, "questions": legacy_view,
        A.CARD_META_KEY: {**question.card_meta, "decisions": [payload["decision"]]}})
    card = await world.broker.card(legacy, snapshot(world))
    assert card["primary"] is None and card["decisions"] == []
    assert card["evidence"]["questions"]["mode"] == "degraded"
    assert card["evidence"]["questions"]["unsupported_pending_count"] == 1
    with refused(E, "invalid_decision") as exc:
        await submit(world, question, payload=payload, wire=wire, user="owner")
    assert exc.value.details["reason"] == "unsupported_question_format"
    assert (await world.broker.get(question.action_id)).delivery_id is None
    assert world.scheduler.live() == []
    assert world.fake_host.get_slot(SLOT).messages == []
    assert path.read_text() == text


@pytest.mark.parametrize("repo_options,payload,wire", [
    ({"questions": "## Plan Approval\n- Approve Plan\n- Request Changes\n[Answer]:\n"},
     {"decision": "approve_plan"}, "Approve Plan"),
    ({"questions": "## Consolidated Summary Confirmation\n- Looks correct\n- Request changes\n[Answer]:\n"},
     {"decision": "confirm_summary", "choice": "looks_correct"}, "Looks correct"),
], ids=["plan", "summary"])
@aio
async def test_a_later_stages_followups_leave_a_stale_checkpoint_card_stale(world, E, payload, wire):
    """The follow-up guard reads the current file; a card for an earlier stage must go stale instead."""
    question = await bound_card(world, "question")
    record = world.builder.record(INTENT)
    state = F.state_text(marks={STAGE: "x", SECOND_STAGE: "-"}, fields={
        "Current Stage": SECOND_STAGE, "Status": "Running", "Next Stage": "domain-model",
        "Lifecycle Phase": "INCEPTION", "Completed": "8"})
    (record / "aidlc-state.md").write_text(state)
    (record / ".aidlc-active-directive.json").write_text(
        json.dumps({"version": 1, "stage": SECOND_STAGE, "state_sha256": F.sha256(state)}))
    later = record / f"inception/{SECOND_STAGE}/{SECOND_STAGE}-questions.md"
    later.parent.mkdir(parents=True, exist_ok=True)
    later.write_text("## Q1. Persona?\nA. Admin\n[Answer]: A\n\n## F1. Follow-up\n[Answer]:\n")
    live = snapshot(world)
    assert live.stage == SECOND_STAGE and live.questions.unsupported_pending_count == 1
    with refused(E, "action_stale") as exc:
        await submit(world, question, payload=payload, wire=wire, user="owner")
    assert "card" in exc.value.details
    assert (await world.broker.get(question.action_id)).delivery_id is None
    assert world.fake_host.get_slot(SLOT).messages == []


@aio
async def test_unavailable_repository_is_refused_before_snapshot_or_dispatch(world, E, monkeypatch):
    gate = pick(await seed(world), "gate")
    world.store.update("repos", REPO_ID, {"availability": "unavailable", "availability_detail": "path_missing"})

    async def no_snapshot(*args, **kwargs):
        raise AssertionError("an unavailable repo must not be treated as an empty live snapshot")

    monkeypatch.setattr(world.broker, "_snapshot", no_snapshot)
    with refused(E, "repo_unavailable") as exc:
        await submit(world, gate, user="owner")
    assert exc.value.details["detail"] == "path_missing"
    assert world.scheduler.live() == []
    assert (await world.broker.get(gate.action_id)).delivery_id is None


@aio
async def test_unavailable_repository_keeps_the_saved_card_but_offers_no_human_dispatch(world):
    gate = pick(await seed(world), "gate")
    world.store.update("repos", REPO_ID, {"availability": "unavailable", "availability_detail": "path_missing"})
    card = await world.broker.card(gate, None)
    assert (card["repo"]["availability"], card["repo"]["availability_detail"]) == ("unavailable", "path_missing")
    assert card["primary"] is None and card["decisions"] == []
    assert card["status"] == "Queued" and card["evidence"]["state"] == gate.evidence["state"]
    world.store.update("repos", REPO_ID, {"availability": "available", "availability_detail": None})
    assert (await world.broker.card(gate, snapshot(world)))["primary"]["decision"] == "approve"
    assert (await world.broker.get(gate.action_id)).status == "Queued"


@aio
async def test_the_card_is_the_shape_the_ui_type_pins(world, A, C):
    gate = await bound_card(world)
    card = await world.broker.card(gate, snapshot(world))
    assert set(card) == {
        "action_id", "type", "queue_type", "status", "status_generation", "source", "risk_class", "severity",
        "priority_group", "repo", "space", "intent", "stage", "headline", "consequence", "waiting_since",
        "created_at", "updated_at", "primary", "decisions", "captured", "evidence", "delivery", "resolution",
        "failure", "install", "budget", "advisor", "deep_link", "dedupe_key", "human_text_present",
        "acknowledged_evidence_sha256"}
    assert (card["queue_type"], card["severity"], card["priority_group"]) == ("gate", "blocking", 2)
    assert card["repo"] == {"repo_id": REPO_ID, "label": "demo", "canonical_path": str(world.root),
                            "availability": "available", "availability_detail": None, "archived": False}
    assert card["headline"] == {"key": "action.gate.headline", "params": {"stage": STAGE, "attempt": 1}}
    assert card["primary"] == {"decision": "approve", "label_key": "decision.approve.label"}
    assert card["decisions"][0] == {"decision": "approve", "label_key": "decision.approve.label", "lane": "human_lane",
                                    "wire_text_template": "Approve", "requires": []}
    assert set(card["evidence"]) == {
        "state", "audit", "directive", "artifacts", "review", "acceptance_criteria", "findings", "session",
        "questions", "git", "markers", "presence_baseline", "presence", "cursor_readback"}
    assert set(card["captured"]) == set(A.COMPARED_CAPTURED_FIELDS) | {"captured_at", "stable"}
    assert card["delivery"]["delivery_id"] is None and card["delivery"]["lane"] is None
    assert set(card["delivery"]) == {
        "lane", "delivery_id", "slot_key", "session_key", "wire_text", "host", "delivering_at", "delivered_at",
        "queued_at", "deadline_at", "receipt", "outcome", "delivery_confirmed", "boot_id_unchanged",
        "transcript_row", "slot_ran_since", "disk_baseline_unchanged"}
    assert card["deep_link"] == f"{C.DEEP_LINK_BASE}?view=actions&action={gate.action_id}"
    assert card["human_text_present"] is False and card["budget"] is None
    assert card["advisor"] == {"latest_draft_id": None, "status": None}
    assert "card" not in card["evidence"], "bookkeeping never leaks into the wire shape"


@aio
async def test_a_recovery_card_offers_nothing_that_cannot_succeed(world, E):
    """A button that always fails is worse than none: ``rebind_session`` needs a slot no client sends, and
    ``mark_not_delivered`` is legal only from ``ReconciliationRequired``, which a studio-only card never reaches."""
    await bind(world)
    recovery = await _retyped(world, type="recovery", evidence_json={"findings": [], "card": {
        "decisions": ["rebind_session", "mark_not_delivered", "acknowledge"], "reason": "state_unreadable"}})
    card = await world.broker.card(recovery, snapshot(world))
    assert [d["decision"] for d in card["decisions"]] == ["acknowledge"]
    # The vocabulary is untouched: `resolve` still accepts the decision for a client that names a target.
    with refused(E, "bad_body"):
        await world.broker.resolve(recovery.action_id, decision="rebind_session", payload={}, user="owner-1")


@aio
async def test_an_uncertain_card_is_retyped_and_offers_the_uncertain_decisions(world):
    card = await world.broker.card(await uncertain(world), snapshot(world))
    assert (card["type"], card["queue_type"], card["severity"], card["priority_group"]) == (
        "gate", "delivery_uncertain", "critical", 1)
    assert [d["decision"] for d in card["decisions"]] == ["reconcile", "mark_not_delivered", "resubmit"]
    assert card["delivery"]["lane"] == "human_lane" and card["delivery"]["host"]["body"]["agent"] == "aidlc"
    assert card["evidence"]["presence_baseline"] is not None
    assert card["evidence"]["cursor_readback"]["ok"] is True


@aio
async def test_confirmed_delivery_does_not_offer_undelivered_or_resubmit(world):
    rec = await world.broker.patch_evidence(await uncertain(world), {"delivery_confirmed": True})
    card = await world.broker.card(rec, snapshot(world))
    assert [d["decision"] for d in card["decisions"]] == ["reconcile"]


@aio
async def test_human_text_keeps_only_what_the_person_typed(world):
    gate = await bound_card(world)
    receipt = await submit(world, gate, payload={"decision": "request_changes", "feedback": "Cover C3 in the criteria."},
                           wire="Request Changes: Cover C3 in the criteria.")
    rec = await world.broker.get(receipt.action_id)
    assert rec.human_text == "Cover C3 in the criteria.", "only the prose, not the protocol label"
    card = await world.broker.card(rec, None)
    assert "human_text" not in card, "the card carries a flag, never the retained text"
    assert card["human_text_present"] is True
    # `delivery.wire_text` must keep the feedback (it records what was sent, §2.11); retention removes the
    # `human_text` copy, here and on the activity row.
    assert card["delivery"]["wire_text"] == "Request Changes: Cover C3 in the criteria."
    submitted = [row for row in world.store.select("activity") if row["kind"] == "action.submitted"]
    assert submitted and submitted[-1]["human_text"] == "Cover C3 in the criteria."


@aio
async def test_every_transition_publishes_an_event_and_an_activity_row(world):
    gate = await bound_card(world)
    await submit(world, gate)
    assert [name for name, _ in world.ctx.events.published].count("aidlc-studio:action") >= 2
    payloads = [data for _, data in world.ctx.events.published]
    assert payloads[-1]["payload"]["status"] == "Delivering" and payloads[-1]["payload"]["from_status"] == "Queued"
    kinds = [row["kind"] for row in world.store.select("activity")]
    assert "action.created" in kinds and "action.submitted" in kinds
    rows = world.store.select("action_transitions", {"action_id": gate.action_id})
    assert rows[-1]["to_status"] == "Delivering" and rows[-1]["reason"] == "submitted:approve"
