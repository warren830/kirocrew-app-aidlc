"""Tests for ``backend/studio/advisor.py``.

The advisor is the only place Studio hands repository evidence to a model that is not the intent's own
AI-DLC conversation, so most of this file is about the five things that make that safe:

* **Bounded.** A stage whose artifacts are megabytes still produces a package under
  ``MAX_EVIDENCE_PACKAGE_CHARS`` with every excerpt capped and marked, because an unbounded package is a
  cost incident and a latency incident, not a draft.
* **Redacted, and path-free.** A credential planted in an artifact and in an audit field is masked, and
  the repository's absolute path never reaches the task text — the spawn carries no working directory,
  so the path in the prompt would be the only way for the agent to find the repo.
* **The app's own agent, or nothing.** The spawn names ``aidlc-studio-advisor`` and passes ``silent``;
  a missing agent, a colliding name and a foreign filename each refuse with ``advisor_unavailable``, and
  so does an absent ``ctx.spawn`` — there is no degraded local fallback.
* **Neutrality is proved.** Each of the six compared facts is moved in turn and the draft fails
  ``evidence_mutated`` with a ``critical`` Activity row; the four documented housekeeping paths are moved
  together and the draft still succeeds.
* **Nothing is half-parsed.** Zero fences, two fences, bad JSON, an unknown key, a missing key, a bad
  confidence, a truncated result and a gate answer with no verdict all fail with ``bad_result`` and store
  no partial result — and an object carrying ``submit``/``decision`` is refused by the same rule, which is
  what keeps a draft from ever pre-selecting Approve.

The last section is about the standing per-repository grant (A24): those drafts are started by the
reconciler, with nobody waiting, so the tests there are mostly about *not* starting one — an ungranted
repository, a degraded question card, a card that already had its turn, the two ceilings — plus the settle
pass, which is the only reason a draft prepared in advance is still readable when the human arrives.

The store, reader, projection, activity and event log are the real modules over a real repository built
by ``repo_builder``: the module under test is the broker, and a faked parse would only test the fake's
idea of AI-DLC's format. ``actions.py`` is not written yet, so ``FakeActions`` stands in for it — the
broker only ever reads ``ActionRecord`` attributes, which is why duck typing is enough. Async methods run
through ``asyncio.run`` (the suite's convention; no async plugin).
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import conftest as CT
import fixtures as F

NOW = "2026-09-04T10:00:00Z"
REPO = "r_000000000001"
INTENT = "260904-gate-demo"
STAGE = "requirements-analysis"
ACTION = "a_0000000000000001"

#: A credential shape both the host redactor and Studio's fallback recognise.
SECRET = "AKIA1234567890ABCDEF"


# --------------------------------------------------------------------------- #
# module handles
# --------------------------------------------------------------------------- #


@pytest.fixture
def A(studio):
    module = studio.advisor
    assert module is not None, getattr(studio, "advisor__error", "advisor.py did not import")
    return module


@pytest.fixture
def K(studio):
    return studio.constants


@pytest.fixture
def E(studio):
    return studio.errors


# --------------------------------------------------------------------------- #
# fakes for the modules that do not exist yet / are host-owned
# --------------------------------------------------------------------------- #


class FakeAgentInfo(SimpleNamespace):
    """A row of ``kiro_crew.agent_discovery.list_agents()``: name + rendered filename."""


class FakeSubagents:
    """``state.subagents``: the manager ``HostBridge.subagent`` reads, backed by the spawn's records."""

    def __init__(self, spawn: Any) -> None:
        self._spawn = spawn

    def get(self, spawn_id: str) -> Any:
        return self._spawn.results.get(spawn_id)


class FakeActions:
    """``HumanActionBroker.get`` only. The broker reads attributes, so a namespace is enough.

    ``extra`` holds the other cards a pre-draft pass walks over: ``autodraft`` reads the ``actions``
    table and then asks the broker for each row, so a test that only had ``rec`` could never show a
    queue being worked oldest-first.
    """

    def __init__(self, rec: Any) -> None:
        self.rec = rec
        self.extra: dict[str, Any] = {}

    async def get(self, action_id: str) -> Any:
        from _aidlc_studio_backend.studio.errors import StudioError

        found = self.extra.get(action_id)
        if found is None and self.rec is not None and action_id == self.rec.action_id:
            found = self.rec
        if found is None:
            raise StudioError("action_not_found", "no such action", details={"action_id": action_id})
        return found


def action_record(**over: Any) -> SimpleNamespace:
    base = dict(
        action_id=ACTION,
        type="gate",
        repo_id=REPO,
        space="default",
        intent_dir=INTENT,
        stage=STAGE,
        unit=None,
        evidence={},
        retry_fingerprint=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# the wiring under test
# --------------------------------------------------------------------------- #


@pytest.fixture
def store(studio, fake_ctx, K, clock, ids):
    st = studio.storage.Storage(Path(fake_ctx.data_dir) / K.DB_FILENAME, clock=clock, ids=ids)
    st.open()
    yield st
    st.close()


@pytest.fixture
def wiring(studio, A, K, store, fake_ctx, fake_host, clock, ids, gate_repo, fake_bun, denied_log):
    """Real store/reader/projection/activity/events over a real repo at an open gate.

    ``plan`` is a real ``PlanService`` too (built as ``tests/test_plan.py`` builds its ``deps``): a plan
    draft's questionnaire is that service's catalog, and a faked catalog would only prove the fake's
    idea of the engine's scopes. ``fake_bun`` never runs — the catalog is a read — and ``denied_log``
    is how a test would notice if it did.
    """
    reader = studio.aidlc_reader.AidlcReader(clock)
    consistency = studio.consistency.ConsistencyEngine()
    projection = studio.projection.Projection(reader, consistency, clock)
    activity = studio.activity.ActivityProjector(store, clock)
    events = studio.events.EventLog(store, fake_ctx.events, clock)
    host = studio.sessions.HostBridge(clock, logging.getLogger("test.advisor"))
    fake_host.subagents = FakeSubagents(fake_ctx.spawn)
    host.attach(fake_host)
    settings = studio.settings.SettingsService(store, host, clock)
    settings.load()
    scheduler = studio.leases.RepoScheduler(store, host, settings, clock, activity)
    engine = studio.engine.EngineRunner(bun_path=fake_bun, clock=clock, activity=activity)
    estimates = studio.estimates.EstimateService(store, clock)
    plan = studio.plan.PlanService(
        reader, engine, scheduler, None, projection, store, estimates, activity, events, clock, ids
    )

    store.insert(
        "repos",
        {
            "id": REPO,
            "resolved_identity": "1:1",
            "canonical_path": str(gate_repo),
            "label": "gate-demo",
            "added_at": NOW,
            "platform": "darwin",
            "install_status": "installed",
            "installed_engine_version": K.BUNDLED_ENGINE_VERSION,
            "engine_dir": ".kiro",
        },
    )

    # Both markers exist in any repo a Kiro session has touched. Creating them here makes the
    # neutrality tests prove that a *moved* marker fails, not merely that a new file appeared.
    record = record_dir(Path(gate_repo))
    (record / ".aidlc-human-turn").write_text("")
    (Path(gate_repo) / "aidlc" / ".aidlc-turn-counter").write_text("6\n")

    actions = FakeActions(action_record())
    agents = [FakeAgentInfo(name=A.ADVISOR_AGENT_NAME, filename="aidlc-studio--advisor.json")]
    broker = A.AdvisorBroker(
        fake_ctx,
        store,
        host,
        projection,
        actions,
        reader,
        activity,
        events,
        clock,
        ids,
        settings,
        agent_lister=lambda: list(agents),
        plan=plan,
    )
    return SimpleNamespace(
        broker=broker,
        store=store,
        ctx=fake_ctx,
        host=fake_host,
        bridge=host,
        reader=reader,
        projection=projection,
        activity=activity,
        events=events,
        settings=settings,
        actions=actions,
        agents=agents,
        ids=ids,
        repo=Path(gate_repo),
        clock=clock,
        plan=plan,
        denied_log=denied_log,
    )


def req(A, **over: Any):
    base = dict(action_id=ACTION, kind="gate_analysis", question_index=None, locale="en-US")
    base.update(over)
    return A.DraftRequest(**base)


def good_result(**over: Any) -> dict:
    payload = {
        "verdict": "needs_your_decision",
        "summary": "Two acceptance criteria are not evidenced.",
        "suggested_answers": [],
        "evidence": ["requirements.md lists one requirement"],
        "assumptions": [],
        "alternatives": [],
        "confidence": "medium",
        "needs_your_decision": ["Whether one requirement is enough for this scope"],
        "drafted_feedback": None,
    }
    payload.update(over)
    return payload


def fence(payload: Any) -> str:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return f"Here you go.\n\n```json\n{body}\n```\n"


def request_draft(w, A, **over: Any):
    return asyncio.run(w.broker.request(req(A, **over), user="owner-1"))


def rows_of(w, kind: str) -> list:
    from _aidlc_studio_backend.studio.storage import ActivityQuery

    rows, _ = asyncio.run(w.activity.query(ActivityQuery(kind=kind, limit=100)))
    return rows


# --------------------------------------------------------------------------- #
# vocabularies
# --------------------------------------------------------------------------- #


def test_the_agent_name_is_the_declared_name_in_the_agent_json(A, app_root):
    """The spawn must pass the JSON's inner ``name`` — a filename would be refused (C25)."""
    declared = json.loads((app_root / "agents" / "advisor.json").read_text("utf-8"))
    assert A.ADVISOR_AGENT_NAME == "aidlc-studio-advisor" == declared["name"]
    assert declared["tools"] == declared["allowedTools"] == ["thinking"]


def test_every_error_token_is_declared(A):
    """`AdvisorDraft.error` is a machine token the UI localizes; a stray string would render raw."""
    assert A.DRAFT_ERRORS == (
        "spawn_failed",
        "agent_error",
        "timed_out",
        "bad_result",
        "evidence_mutated",
    )


def test_kinds_and_statuses_are_the_contract_strings(A):
    assert A.ADVISOR_KINDS == (
        "gate_analysis",
        "question_draft",
        "question_explain",
        "request_changes_draft",
        "diagnose",
        "plan_draft",
    )
    # A plan draft has no card (§1.18a): it is keyed in neither per-card table, so no card can offer
    # it and no tick can draft it ahead.
    card_kinds = {kind for kinds in A.ADVISOR_KINDS_BY_TYPE.values() for kind in kinds}
    assert "plan_draft" not in card_kinds
    assert set(A.ADVISOR_CARDLESS_KINDS) == {"plan_draft"}
    assert not set(A.ADVISOR_CARDLESS_KINDS) & card_kinds
    assert not set(A.ADVISOR_CARDLESS_KINDS) & set(A.ADVISOR_KINDS_BY_TYPE)
    assert not set(A.ADVISOR_CARDLESS_KINDS) & set(A.ADVISOR_AUTO_KIND_BY_TYPE.values())
    assert A.DRAFT_STATUS == ("queued", "running", "ready", "failed", "expired")
    assert A.ADVISOR_CONSTRAINTS == (
        "read_only",
        "no_tools",
        "evidence_only",
        "never_preselect_approve",
        "label_unknowns",
    )
    assert set(A.ADVISOR_KINDS_BY_TYPE) <= set(studio_action_types())
    # Command cards carry no boundary to reason about, so no kind applies to them.
    for kind in ("run", "resume", "force_stop", "prepare_commit", "revision"):
        assert kind not in A.ADVISOR_KINDS_BY_TYPE


def test_the_automatic_kinds_are_an_explicit_table_and_never_a_rejection(A):
    """The pre-draft trigger's table (A24). Deriving it from ``ADVISOR_KINDS_BY_TYPE`` would tie the
    kind a tick spawns to the order a card offers a human, so reordering a menu would change what runs
    unattended. ``request_changes_draft`` is absent on purpose: drafting a rejection before the human has
    decided to reject pre-decides the gate, which FR-ADV-007 forbids in spirit.
    """
    assert A.ADVISOR_AUTO_KIND_BY_TYPE == {"gate": "gate_analysis", "question": "question_draft"}
    assert set(A.ADVISOR_AUTO_KIND_BY_TYPE) <= set(studio_action_types())
    for card_type, kind in A.ADVISOR_AUTO_KIND_BY_TYPE.items():
        assert kind in A.ADVISOR_KINDS_BY_TYPE[card_type], f"{kind} does not apply to {card_type}"
    assert "request_changes_draft" not in A.ADVISOR_AUTO_KIND_BY_TYPE.values()


def studio_action_types() -> tuple[str, ...]:
    from _aidlc_studio_backend.studio.constants import ACTION_TYPE

    return ACTION_TYPE


def test_the_excluded_paths_are_the_documented_four(A):
    assert set(A.NEUTRALITY_EXCLUDED_PATHS) == {
        ".aidlc-hooks-health",
        ".aidlc-stop-hook",
        ".aidlc-sessions",
        ".aidlc-engine-touch",
    }


# --------------------------------------------------------------------------- #
# the evidence package
# --------------------------------------------------------------------------- #


def test_the_package_is_bounded_and_marks_what_it_cut(w_pkg, A, K):
    package, task = w_pkg(artifact_bytes=400_000, artifact_count=6)
    body = json.dumps(package.to_json(), ensure_ascii=False, separators=(",", ":"))
    assert len(body) <= K.MAX_EVIDENCE_PACKAGE_CHARS
    assert len(task) <= K.MAX_EVIDENCE_PACKAGE_CHARS + 200
    excerpts = [row["excerpt"] for row in package.artifacts]
    assert excerpts, "the stage's artifacts must be described even when their bodies are cut"
    assert all(len(text.encode("utf-8")) <= A.EXCERPT_BYTES for text in excerpts)
    assert all("truncated" in text or "omitted" in text for text in excerpts)
    # Metadata survives the shrink: the human can still see WHICH files the advisor did not read.
    assert all(row["relpath"] and row["sha256"] for row in package.artifacts)


def test_one_oversized_artifact_is_cut_at_the_per_file_cap(w_pkg, A, K):
    """§1.18's 24 KiB per-file cap, observed where the package cap does not yet bind.

    The 96 KiB *total* excerpt budget from §1.18 is never the binding constraint, because
    ``MAX_EVIDENCE_PACKAGE_CHARS`` (60 000) is smaller: the total is enforced (``EXCERPT_TOTAL_BYTES``)
    but the package cap always wins first. That inconsistency is reported, not papered over.
    """
    assert A.EXCERPT_TOTAL_BYTES > K.MAX_EVIDENCE_PACKAGE_CHARS
    package, _ = w_pkg(artifact_bytes=200_000, artifact_count=1)
    big = [row for row in package.artifacts if row["name"].startswith("big-")]
    assert len(big) == 1
    excerpt = big[0]["excerpt"]
    assert A.EXCERPT_BYTES <= len(excerpt.encode("utf-8")) <= A.EXCERPT_BYTES + 64
    assert excerpt.endswith(f"… excerpt truncated at {A.EXCERPT_BYTES} bytes")


def test_the_package_redacts_credentials_from_artifacts_and_audit(w_pkg):
    package, task = w_pkg(secret=True)
    assert SECRET not in task
    assert "<redacted>" in task
    assert SECRET not in json.dumps(package.to_json())


def test_the_package_carries_no_repo_path_and_no_working_directory(w_pkg, wiring):
    package, task = w_pkg(mention_path=True)
    root = str(wiring.repo)
    assert root not in task
    assert "<repo>" in task
    payload = package.to_json()
    flat = json.dumps(payload)
    assert root not in flat
    for forbidden in ("cwd", "working_dir", "working_directory", "repo_path", "canonical_path"):
        assert forbidden not in _keys(payload), f"the package must not name a {forbidden}"


def _keys(node: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            out.add(key)
            out |= _keys(value)
    elif isinstance(node, list):
        for value in node:
            out |= _keys(value)
    return out


def test_render_task_carries_the_package_then_the_answer_contract(wiring, A):
    package = _build(wiring, A, req(A))
    task = wiring.broker.render_task(package)
    assert task.startswith("EVIDENCE PACKAGE (JSON):\n```json\n")
    body = task.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert json.loads(body)["constraints"] == list(A.ADVISOR_CONSTRAINTS)
    assert json.loads(body)["version"] == A.EVIDENCE_VERSION
    assert task.rstrip().endswith(A.draft_result_contract(kind=package.kind).rstrip())


def test_the_request_spells_out_every_key_and_value_the_parser_enforces(wiring, A):
    """The drift gate. Naming ``DraftResult`` told the model nothing — this asserts it is *described*.

    What shipped said only "Respond with exactly one ```json block matching DraftResult", a name that
    appears nowhere the model can see, while the agent's own system prompt asked for markdown sections.
    The model reconciled the two the only way it could: it turned the section titles into JSON keys, and
    every draft came back ``unknown_keys``. Generating the contract from the same constants
    ``parse_draft_result`` enforces is what makes that unrepeatable, and this test is what keeps the
    generation honest — add a key or a verdict to the parser without describing it and this fails.
    """
    task = wiring.broker.render_task(_build(wiring, A, req(A)))
    for key in A.DRAFT_RESULT_KEYS:
        assert f'"{key}"' in task, key
    for verdict in A.ADVISOR_VERDICTS:
        assert f'"{verdict}"' in task, verdict
    for confidence in A.ADVISOR_CONFIDENCE:
        assert f'"{confidence}"' in task, confidence
    assert set(A.DRAFT_RESULT_KEY_ORDER) == set(A.DRAFT_RESULT_KEYS)
    assert str(len(A.DRAFT_RESULT_KEY_ORDER)) in task


def test_the_request_names_the_question_indices_an_answer_may_address(wiring, A):
    wiring.actions.rec = action_record(type="question")
    package = _build(wiring, A, req(A, kind="question_draft", question_index=2))
    task = wiring.broker.render_task(package)
    assert "[2]" in task
    assert "question_index" in task

    gate = wiring.broker.render_task(_build(wiring, A, req(A)))
    # No question was shown, so the contract must say so rather than leave the field open.
    assert '"suggested_answers": []' in gate


def test_the_answer_the_live_model_gave_before_the_fix_is_still_refused(wiring, A):
    """The observed failure, pinned as a test rather than as a paragraph in a report.

    These are the section titles from the *old* agent prompt, turned into JSON keys — exactly what the
    live advisor emitted. Every one of them is refused, and it must stay refused: the strict-key rule is
    what keeps a draft from smuggling a field the UI would act on.
    """
    observed = {
        "recommendation": "Approve",
        "confidence": "high",
        "confidence_reason": "The artifacts cover the criteria.",
        "acceptance_criteria_check": ["one requirement listed: met"],
        "reviewer_findings": [],
        "contradictions_and_unresolved_risks": [],
        "missing_from_evidence": [],
        "markdown": "## Recommendation\nApprove",
        "action_id": ACTION,
        "intent": INTENT,
        "stage": STAGE,
        "kind": "gate_analysis",
    }
    draft = settle(wiring, A, fence(observed))
    assert draft.status == "failed" and draft.error == "bad_result"
    assert draft.result is None
    detail = rows_of(wiring, "advisor.failed")[0].params["detail"]
    assert detail.startswith("unknown_keys: ")
    assert "recommendation" in detail and "markdown" in detail


def test_a_question_package_carries_only_the_targeted_question(wiring, A):
    wiring.actions.rec = action_record(type="question")
    package = _build(wiring, A, req(A, kind="question_draft", question_index=2))
    assert package.questions is not None
    assert [row["index"] for row in package.questions["questions"]] == [2]
    # `raw` duplicates prompt + options with no cap of its own and must not travel.
    assert all("raw" not in row for row in package.questions["questions"])


def test_a_gate_package_carries_no_questions(wiring, A):
    package = _build(wiring, A, req(A))
    assert package.questions is None
    assert package.stage is not None and package.stage["slug"] == STAGE


@pytest.fixture
def w_pkg(wiring, A):
    """Build a package after optionally planting oversized / secret / path-bearing artifacts."""

    def build(*, artifact_bytes: int = 0, artifact_count: int = 0, secret: bool = False,
              mention_path: bool = False):
        stage_dir = wiring.repo / "aidlc" / "spaces" / "default" / "intents" / INTENT / "inception" / STAGE
        for index in range(artifact_count):
            (stage_dir / f"big-{index}.md").write_text("# Big\n" + ("lorem ipsum " * (artifact_bytes // 12)))
        if secret:
            (stage_dir / "creds.md").write_text(f"# Notes\n\naws_access_key_id = {SECRET}\n")
            F_block = F.audit_block("ERROR_LOGGED", "2026-09-04T09:30:00Z", Error=f"token={SECRET}")
            _append_audit(wiring.repo, F_block)
        if mention_path:
            (stage_dir / "paths.md").write_text(f"# Layout\n\nThe repo lives at {wiring.repo}/aidlc.\n")
        package = _build(wiring, A, req(A))
        return package, wiring.broker.render_task(package)

    return build


def _append_audit(repo: Path, block: str) -> None:
    shard = (
        repo / "aidlc" / "spaces" / "default" / "intents" / INTENT / "audit"
        / "fixture-host-0f1e2d3c4b5a.md"
    )
    with shard.open("a") as handle:
        handle.write(block)


def _build(w, A, request_obj):
    from _aidlc_studio_backend.studio.repo_registry import RepoRecord

    repo = RepoRecord.from_row(w.store.get("repos", REPO))
    snap = w.projection.snapshot(repo, "default", INTENT)
    return w.broker.build_package(request_obj, w.actions.rec, snap, repo)


# --------------------------------------------------------------------------- #
# spawn
# --------------------------------------------------------------------------- #


def test_the_spawn_uses_the_apps_own_agent_and_is_silent(wiring, A):
    draft = request_draft(wiring, A)
    assert len(wiring.ctx.spawn.spawned) == 1
    call = wiring.ctx.spawn.spawned[0]
    assert call["agent"] == A.ADVISOR_AGENT_NAME
    assert call["silent"] is True
    assert call["model"] == ""
    assert call["task"].startswith("EVIDENCE PACKAGE (JSON):")
    # `SpawnSDK.run` exposes no cwd, and the broker must not reach past it to one that does.
    assert set(call) == {"id", "task", "agent", "silent", "model"}
    assert draft.status == "running" and draft.spawn_id == call["id"]
    assert draft.to_json()["status"] == "running"


def test_no_spawn_seam_is_advisor_unavailable_and_writes_no_draft(wiring, A, E):
    wiring.ctx.spawn = None
    with pytest.raises(E.StudioError) as excinfo:
        request_draft(wiring, A)
    assert excinfo.value.code == "advisor_unavailable"
    assert excinfo.value.details["reason"] == "spawn_unavailable"
    assert wiring.store.select("advisor_drafts") == []
    assert [r.params["reason"] for r in rows_of(wiring, "advisor.failed")] == ["spawn_unavailable"]


def test_a_disabled_advisor_is_advisor_unavailable(wiring, A, E):
    wiring.store.pref_set("settings", {"values": {"advisor": {"enabled": False}}, "updated_at": NOW})
    wiring.settings.load()
    with pytest.raises(E.StudioError) as excinfo:
        request_draft(wiring, A)
    assert excinfo.value.code == "advisor_unavailable"
    assert excinfo.value.details["reason"] == "disabled"
    assert wiring.ctx.spawn.spawned == []


def test_a_missing_agent_is_advisor_unavailable(wiring, A, E):
    wiring.agents.clear()
    with pytest.raises(E.StudioError) as excinfo:
        request_draft(wiring, A)
    assert excinfo.value.code == "advisor_unavailable"
    assert excinfo.value.details["reason"] == "agent_missing"
    assert wiring.ctx.spawn.spawned == []


def test_a_colliding_agent_name_is_advisor_unavailable(wiring, A, E):
    wiring.agents.append(
        FakeAgentInfo(name=A.ADVISOR_AGENT_NAME, filename="aidlc-studio--advisor-copy.json")
    )
    with pytest.raises(E.StudioError) as excinfo:
        request_draft(wiring, A)
    assert excinfo.value.code == "advisor_unavailable"
    assert excinfo.value.details["reason"] == "agent_name_collision"
    assert len(excinfo.value.details["files"]) == 2


def test_a_same_named_agent_outside_the_app_is_not_ours(wiring, A, E):
    """kiro-cli's agent namespace is flat, so the filename prefix is the ownership proof."""
    wiring.agents[:] = [FakeAgentInfo(name=A.ADVISOR_AGENT_NAME, filename="advisor.json")]
    with pytest.raises(E.StudioError) as excinfo:
        request_draft(wiring, A)
    assert excinfo.value.details["reason"] == "agent_missing"


def test_agent_discovery_being_unavailable_refuses_rather_than_assuming(wiring, A, E):
    wiring.broker._agent_lister = lambda: None
    with pytest.raises(E.StudioError) as excinfo:
        request_draft(wiring, A)
    assert excinfo.value.details["reason"] == "agent_discovery_unavailable"


def test_a_declined_spawn_fails_the_draft_and_refuses(wiring, A, E):
    wiring.ctx.spawn.fail_with = RuntimeError("governance profile forbids spawns")
    with pytest.raises(E.StudioError) as excinfo:
        request_draft(wiring, A)
    assert excinfo.value.details["reason"] == "spawn_failed"
    stored = wiring.store.select("advisor_drafts")
    assert [row["status"] for row in stored] == ["failed"]
    assert stored[0]["error"] == "spawn_failed"


def test_a_kind_that_does_not_apply_to_the_type_is_refused(wiring, A, E):
    with pytest.raises(E.StudioError) as excinfo:
        request_draft(wiring, A, kind="question_draft")
    assert excinfo.value.code == "invalid_decision"
    assert excinfo.value.details["allowed"] == ["gate_analysis", "request_changes_draft"]


def test_an_unsupported_locale_is_refused(wiring, A, E):
    with pytest.raises(E.StudioError) as excinfo:
        request_draft(wiring, A, locale="de-DE")
    assert excinfo.value.code == "unsupported_locale"


def test_a_question_index_outside_the_file_is_refused(wiring, A, E):
    wiring.actions.rec = action_record(type="question")
    with pytest.raises(E.StudioError) as excinfo:
        request_draft(wiring, A, kind="question_draft", question_index=99)
    assert excinfo.value.code == "invalid_decision"
    assert excinfo.value.details["question_index"] == 99


def test_an_unknown_action_propagates_action_not_found(wiring, A, E):
    wiring.actions.rec = None
    with pytest.raises(E.StudioError) as excinfo:
        request_draft(wiring, A)
    assert excinfo.value.code == "action_not_found"


# --------------------------------------------------------------------------- #
# polling and result parsing
# --------------------------------------------------------------------------- #


def settle(w, A, answer: str, **over: Any):
    w.ctx.spawn.next_result = answer
    draft = request_draft(w, A, **over)
    return asyncio.run(w.broker.poll(draft.draft_id))


def test_a_well_formed_answer_becomes_a_ready_draft(wiring, A):
    draft = settle(wiring, A, fence(good_result()))
    assert draft.status == "ready" and draft.error is None
    assert draft.result is not None
    assert draft.result.verdict == "needs_your_decision"
    assert draft.result.confidence == "medium"
    assert draft.result.needs_your_decision
    assert draft.neutrality is not None and draft.neutrality["ok"] is True
    assert draft.neutrality["changed"] == []
    kinds = [row.kind for row in rows_of(wiring, "advisor.completed")]
    assert kinds == ["advisor.completed"]
    assert ("advisor.updated", "ready") in [
        (record.type, record.payload.get("status"))
        for record in asyncio.run(wiring.events.since(0))
    ]


@pytest.mark.parametrize(
    "answer,detail",
    [
        ("no fence at all", "no_json_fence"),
        ("```\n{}\n```", "no_json_fence"),
        (fence(good_result()) + fence(good_result()), "multiple_json_fences"),
        ("```json\n{not json}\n```", "invalid_json"),
        ("```json\n[]\n```", "not_an_object"),
    ],
)
def test_a_malformed_answer_fails_the_draft(wiring, A, answer, detail):
    draft = settle(wiring, A, answer)
    assert draft.status == "failed" and draft.error == "bad_result"
    assert draft.result is None
    params = rows_of(wiring, "advisor.failed")[0].params
    assert params["detail"].startswith(detail)


def test_an_unknown_key_is_refused_so_a_draft_can_never_carry_a_submit(wiring, A):
    """The strict-key rule is what makes "never preselect Approve" enforceable."""
    for smuggled in ({"submit": True}, {"decision": "approve"}, {"wire_text": "Approve"}):
        draft = settle(wiring, A, fence(good_result(**smuggled)))
        assert draft.status == "failed" and draft.error == "bad_result"
        assert draft.result is None


@pytest.mark.parametrize(
    "payload,detail",
    [
        ({"confidence": "certain"}, "bad_confidence"),
        ({"verdict": "approve"}, "bad_verdict"),
        ({"verdict": None}, "gate_analysis_without_verdict"),
        ({"summary": "   "}, "empty_summary"),
        ({"evidence": "one string"}, "bad_string_list"),
        ({"suggested_answers": [{"question_index": 1}]}, "bad_suggested_answer_keys"),
        (
            {"suggested_answers": [{"question_index": 1, "answer": "B", "option_letters": ["bb"]}]},
            "bad_option_letters",
        ),
    ],
)
def test_a_schema_violation_fails_the_draft(wiring, A, payload, detail):
    body = good_result()
    body.update(payload)
    draft = settle(wiring, A, fence(body))
    assert draft.status == "failed" and draft.error == "bad_result"
    assert rows_of(wiring, "advisor.failed")[0].params["detail"].startswith(detail)


def test_a_missing_key_fails_the_draft(wiring, A):
    body = good_result()
    body.pop("assumptions")
    draft = settle(wiring, A, fence(body))
    assert draft.status == "failed" and draft.error == "bad_result"
    assert "missing_keys" in rows_of(wiring, "advisor.failed")[0].params["detail"]


def test_a_truncated_result_is_read_back_from_the_file_the_host_kept(wiring, A, tmp_path):
    """The live failure this pair of tests exists for.

    The host puts only the first ``agent.completion_keep_chars`` of a subagent's answer on the
    completion event — 3 000 characters in an unmodified install, and a real draft is several times that
    — and keeps the whole answer at ``result_path``. Failing on the flag alone meant every draft on
    every default-configured host settled ``bad_result: result_truncated``, which is exactly what the
    live install did on its first real request. Recovering from the file is the difference between an
    advisor that works and one that has never once produced a draft.
    """
    whole = fence(good_result(confidence="high", summary="The whole answer, recovered from disk."))
    result_file = tmp_path / "result.txt"
    result_file.write_text(whole, encoding="utf-8")

    wiring.ctx.spawn.next_result = whole
    draft = request_draft(wiring, A)
    info = wiring.ctx.spawn.results[draft.spawn_id]
    # The event copy really is a prefix — parsing *it* could only ever fail.
    info.result = whole[:64]
    info.result_truncated = True
    info.result_path = str(result_file)

    settled = asyncio.run(wiring.broker.poll(draft.draft_id))
    assert settled.status == "ready", settled.error
    assert settled.result is not None
    assert settled.result.confidence == "high"
    assert settled.result.summary == "The whole answer, recovered from disk."


def test_a_truncated_result_with_no_file_left_is_never_half_parsed(wiring, A, tmp_path):
    """The host deletes ``result.txt`` once the completion event is delivered, so recovery can miss.

    When it does, the head of the answer is all that exists — and half a JSON object is exactly the
    input that parses into a confidently wrong draft. No result at all is the correct answer.
    """
    wiring.ctx.spawn.next_result = fence(good_result())
    draft = request_draft(wiring, A)
    info = wiring.ctx.spawn.results[draft.spawn_id]
    info.result_truncated = True
    info.result_path = str(tmp_path / "already-cleaned-up.txt")

    settled = asyncio.run(wiring.broker.poll(draft.draft_id))
    assert settled.status == "failed" and settled.error == "bad_result"
    assert settled.result is None
    assert rows_of(wiring, "advisor.failed")[0].params["detail"] == "result_truncated"


def test_a_recovered_result_past_the_cap_is_refused_rather_than_read(wiring, A, tmp_path, K):
    """A ``result.txt`` the size of a transcript is not a draft, and reading its prefix is the bug."""
    result_file = tmp_path / "result.txt"
    result_file.write_text("x" * (K.MAX_SUBAGENT_RESULT_BYTES + 1), encoding="utf-8")

    wiring.ctx.spawn.next_result = fence(good_result())
    draft = request_draft(wiring, A)
    info = wiring.ctx.spawn.results[draft.spawn_id]
    info.result_truncated = True
    info.result_path = str(result_file)

    settled = asyncio.run(wiring.broker.poll(draft.draft_id))
    assert settled.status == "failed" and settled.error == "bad_result"
    assert rows_of(wiring, "advisor.failed")[0].params["detail"] == "result_truncated"


def test_a_question_draft_may_suggest_an_answer_for_a_question_it_was_shown(wiring, A):
    wiring.actions.rec = action_record(type="question")
    body = good_result(
        verdict=None,
        suggested_answers=[{"question_index": 1, "answer": "Option A label", "option_letters": ["A"]}],
    )
    draft = settle(wiring, A, fence(body), kind="question_draft", question_index=1)
    assert draft.status == "ready", draft.error
    assert draft.result.suggested_answers == (
        {"question_index": 1, "answer": "Option A label", "option_letters": ["A"]},
    )
    assert draft.result.verdict is None


def test_a_gate_draft_may_not_suggest_an_answer_at_all(wiring, A):
    """No question was shown, so any suggested answer is invented."""
    body = good_result(
        suggested_answers=[{"question_index": 1, "answer": "Approve", "option_letters": ["A"]}]
    )
    draft = settle(wiring, A, fence(body))
    assert draft.status == "failed" and draft.error == "bad_result"
    assert "question_index_not_in_package" in rows_of(wiring, "advisor.failed")[0].params["detail"]


def test_an_answer_for_a_question_the_package_never_showed_is_refused(wiring, A):
    wiring.actions.rec = action_record(type="question")
    body = good_result(
        verdict=None,
        suggested_answers=[{"question_index": 42, "answer": "B", "option_letters": ["B"]}],
    )
    draft = settle(wiring, A, fence(body), kind="question_draft", question_index=1)
    assert draft.status == "failed" and draft.error == "bad_result"
    assert "question_index_not_in_package" in rows_of(wiring, "advisor.failed")[0].params["detail"]


def test_an_agent_error_fails_the_draft(wiring, A):
    wiring.ctx.spawn.next_result = ""
    draft = request_draft(wiring, A)
    wiring.ctx.spawn.results[draft.spawn_id].error = "kiro-cli exited 1"
    settled = asyncio.run(wiring.broker.poll(draft.draft_id))
    assert settled.status == "failed" and settled.error == "agent_error"


def test_an_unfinished_spawn_stays_running_until_the_timeout(wiring, A, K):
    wiring.ctx.spawn.next_result = fence(good_result())
    draft = request_draft(wiring, A)
    wiring.ctx.spawn.results[draft.spawn_id].done = False
    assert asyncio.run(wiring.broker.poll(draft.draft_id)).status == "running"
    wiring.clock.advance(K.ADVISOR_TIMEOUT_SECS + 1)
    settled = asyncio.run(wiring.broker.poll(draft.draft_id))
    assert settled.status == "failed" and settled.error == "timed_out"


def test_unreadable_evidence_refuses_rather_than_serving_the_draft(wiring, A):
    """"We could not check" must never read as "nothing changed"."""
    wiring.ctx.spawn.next_result = fence(good_result())
    draft = request_draft(wiring, A)
    wiring.store.delete("repos", REPO)
    settled = asyncio.run(wiring.broker.poll(draft.draft_id))
    assert settled.status == "failed" and settled.error == "evidence_mutated"
    assert settled.result is None
    row = rows_of(wiring, "advisor.failed")[0]
    assert row.severity == "critical"
    assert row.params["detail"] == "evidence_unreadable: repo_not_found"


def test_a_reaped_subagent_record_does_not_prove_failure(wiring, A):
    wiring.ctx.spawn.next_result = fence(good_result())
    draft = request_draft(wiring, A)
    wiring.ctx.spawn.results.pop(draft.spawn_id)
    assert asyncio.run(wiring.broker.poll(draft.draft_id)).status == "running"


def test_a_settled_draft_is_not_polled_again(wiring, A):
    draft = settle(wiring, A, fence(good_result()))
    wiring.ctx.spawn.results[draft.spawn_id].result = fence(good_result(confidence="high"))
    again = asyncio.run(wiring.broker.poll(draft.draft_id))
    assert again.result.confidence == "medium"


def test_an_unknown_draft_is_draft_not_found(wiring, A, E):
    with pytest.raises(E.StudioError) as excinfo:
        asyncio.run(wiring.broker.get("d_dead"))
    assert excinfo.value.code == "draft_not_found"


# --------------------------------------------------------------------------- #
# the draft object itself
# --------------------------------------------------------------------------- #


def test_the_draft_wire_shape_is_pinned_and_carries_no_decision(wiring, A):
    draft = settle(wiring, A, fence(good_result()))
    payload = draft.to_json()
    assert set(payload) == {
        "draft_id",
        "action_id",
        "kind",
        "status",
        "request",
        "result",
        "error",
        "created_at",
        "updated_at",
        "expires_at",
        "neutrality",
    }
    assert set(payload["request"]) == {"action_id", "kind", "question_index", "locale", "auto"}
    assert payload["request"]["auto"] is False, "a draft a human clicked for is never automatic"
    assert set(payload["result"]) == set(A.DRAFT_RESULT_KEYS)
    assert "spawn_id" not in payload
    assert "plan_proposal" not in payload, "the plan patch is a card-less draft's key only (§1.18a)"
    for forbidden in ("submit", "decision", "decisions", "preselect", "preselected", "auto_submit"):
        assert forbidden not in _keys(payload)


def test_the_ttl_is_stored_and_enforced(wiring, A, K):
    draft = request_draft(wiring, A)
    assert draft.created_at == NOW
    assert draft.expires_at == K.iso_from_epoch(
        K.epoch_from_iso(NOW) + K.ADVISOR_DRAFT_TTL_SECS
    )
    wiring.clock.advance(K.ADVISOR_DRAFT_TTL_SECS + 1)
    assert asyncio.run(wiring.broker.poll(draft.draft_id)).status == "expired"


def test_expire_stale_retires_past_ttl_drafts(wiring, A, K):
    draft = settle(wiring, A, fence(good_result()))
    assert asyncio.run(wiring.broker.expire_stale()) == 0
    wiring.clock.advance(K.ADVISOR_DRAFT_TTL_SECS + 1)
    assert asyncio.run(wiring.broker.expire_stale()) == 1
    assert asyncio.run(wiring.broker.get(draft.draft_id)).status == "expired"


def test_drafts_are_listed_newest_first_for_a_card(wiring, A):
    first = settle(wiring, A, fence(good_result()))
    wiring.clock.advance(5)
    second = settle(wiring, A, fence(good_result()))
    listed = asyncio.run(wiring.broker.list_for_action(ACTION))
    assert [d.draft_id for d in listed] == [second.draft_id, first.draft_id]
    assert asyncio.run(wiring.broker.latest_for_action(ACTION)).draft_id == second.draft_id
    assert asyncio.run(wiring.broker.latest_for_action("a_nope")) is None


# --------------------------------------------------------------------------- #
# neutrality
# --------------------------------------------------------------------------- #


def record_dir(repo: Path) -> Path:
    return repo / "aidlc" / "spaces" / "default" / "intents" / INTENT


def mutate_state(repo: Path) -> None:
    path = record_dir(repo) / "aidlc-state.md"
    path.write_text(path.read_text("utf-8") + "\n<!-- moved -->\n")


def mutate_questions(repo: Path) -> None:
    path = record_dir(repo) / "inception" / STAGE / f"{STAGE}-questions.md"
    path.write_text(path.read_text("utf-8").replace("[Answer]:", "[Answer]: A", 1))


def mutate_directive(repo: Path) -> None:
    path = record_dir(repo) / ".aidlc-active-directive.json"
    payload = json.loads(path.read_text("utf-8"))
    payload["unit"] = "unit-1"
    path.write_text(json.dumps(payload))


def mutate_marker(repo: Path) -> None:
    path = record_dir(repo) / ".aidlc-human-turn"
    path.write_text("")
    future = time.time() + 120
    os.utime(path, (future, future))


def mutate_turn_counter(repo: Path) -> None:
    (repo / "aidlc" / ".aidlc-turn-counter").write_text("7\n")


def mutate_audit(repo: Path) -> None:
    _append_audit(repo, F.audit_block("HUMAN_TURN", "2026-09-04T09:45:00Z", Source="chat"))


@pytest.mark.parametrize(
    "mutate,field",
    [
        (mutate_state, "state_sha"),
        (mutate_questions, "question_digest"),
        (mutate_directive, "directive_sha256"),
        (mutate_marker, "human_turn_mtime_ns"),
        (mutate_turn_counter, "turn_counter"),
        (mutate_audit, "audit_shards"),
    ],
)
def test_a_neutrality_violation_fails_the_draft_loudly(wiring, A, mutate, field):
    wiring.ctx.spawn.next_result = fence(good_result())
    draft = request_draft(wiring, A)
    mutate(wiring.repo)
    settled = asyncio.run(wiring.broker.poll(draft.draft_id))
    assert settled.status == "failed" and settled.error == "evidence_mutated"
    assert settled.result is None, "a mutated repo must not leave a usable draft behind"
    assert settled.neutrality["ok"] is False
    assert settled.neutrality["changed"] == [field]
    assert settled.neutrality[f"{field}_before"] != settled.neutrality[f"{field}_after"]
    row = rows_of(wiring, "advisor.failed")[0]
    assert row.severity == "critical" and row.params["changed"] == [field]


def test_engine_housekeeping_is_not_a_neutrality_violation(wiring, A):
    """The four documented paths move on every Kiro session; none of them says the workflow moved."""
    wiring.ctx.spawn.next_result = fence(good_result())
    draft = request_draft(wiring, A)
    record = record_dir(wiring.repo)
    (record / ".aidlc-hooks-health").mkdir(exist_ok=True)
    (record / ".aidlc-hooks-health" / "stop.last").write_text("2026-09-04T10:01:00Z")
    (record / ".aidlc-stop-hook").mkdir(exist_ok=True)
    (record / ".aidlc-stop-hook" / "block-count.json").write_text('{"count": 3}')
    (record / ".aidlc-engine-touch").write_text("")
    sessions = wiring.repo / "aidlc" / ".aidlc-sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / "session-1.json").write_text("{}")
    settled = asyncio.run(wiring.broker.poll(draft.draft_id))
    assert settled.status == "ready", settled.error
    assert settled.neutrality["changed"] == []


def test_the_before_half_is_recorded_while_the_spawn_is_in_flight(wiring, A):
    draft = request_draft(wiring, A)
    assert draft.neutrality is not None
    for field in A.NEUTRALITY_FIELDS:
        assert f"{field}_before" in draft.neutrality
        assert draft.neutrality[f"{field}_after"] is None
    assert draft.neutrality["state_sha_before"]
    assert draft.neutrality["audit_shards_before"], "shards are compared per file, not in aggregate"
    assert draft.neutrality["ok"] is True and draft.neutrality["changed"] == []


def test_the_shard_digest_is_per_shard_with_size(wiring, A):
    draft = request_draft(wiring, A)
    shards = draft.neutrality["audit_shards_before"]
    assert len(shards) == 1
    (relpath, meta), = shards.items()
    assert relpath.endswith("/audit/fixture-host-0f1e2d3c4b5a.md")
    assert set(meta) == {"size", "sha256"} and len(meta["sha256"]) == 64


# --------------------------------------------------------------------------- #
# drafting ahead of the click: the standing per-repository grant (A24)
# --------------------------------------------------------------------------- #


def grant(w, *repo_ids: str, enabled: bool = True, locale: str | None = None) -> None:
    """Write the standing grant the way Settings does, then refresh the cached read.

    ``locale`` is the interface language, which is what a draft nobody asked for is written in; it is
    written through the same pref row because that row is stored whole.
    """
    values: dict[str, Any] = {"advisor": {"enabled": enabled, "auto_draft_repo_ids": list(repo_ids)}}
    if locale is not None:
        values["locale"] = locale
    w.store.pref_set("settings", {"values": values, "updated_at": NOW})
    w.settings.load()


def card(w, action_id: str, *, card_type: str = "gate", repo_id: str = REPO, **over: Any) -> str:
    """One Queued card in the ``actions`` table AND in ``FakeActions``.

    ``autodraft`` finds candidates by querying the table and then asks the broker for each one, so a card
    that existed in only one of the two would prove nothing about the pass.
    """
    row = {
        "action_id": action_id,
        "type": card_type,
        "risk_class": "human_lane",
        "repo_id": repo_id,
        "intent_dir": INTENT,
        "space": "default",
        "stage": STAGE,
        "status": "Queued",
        "created_at": NOW,
        "updated_at": NOW,
        "waiting_since": NOW,
    }
    row.update(over)
    w.store.insert("actions", row)
    w.actions.extra[action_id] = action_record(
        action_id=action_id, type=card_type, repo_id=repo_id
    )
    return action_id


def autodraft(w, **over: Any) -> int:
    return asyncio.run(w.broker.autodraft(**over))


def drafts_of(w) -> list[dict]:
    return w.store.select("advisor_drafts")


def rebuilt(w, A):
    """A second broker over the same store: its ``_auto_tried`` is empty, as after a gateway restart."""
    return A.AdvisorBroker(
        w.ctx,
        w.store,
        w.bridge,
        w.projection,
        w.actions,
        w.reader,
        w.activity,
        w.events,
        w.clock,
        w.ids,
        w.settings,
        agent_lister=lambda: list(w.agents),
        plan=w.plan,
    )


def test_only_a_granted_repository_draws_a_draft_nobody_asked_for(wiring, A):
    """The empty grant is every install's default, and it must mean exactly today's behaviour."""
    card(wiring, "a_auto1")
    assert autodraft(wiring) == 0
    grant(wiring, "r_000000000002")          # a grant for a different repository is not this one's
    assert autodraft(wiring) == 0
    assert wiring.ctx.spawn.spawned == []

    grant(wiring, REPO)
    assert autodraft(wiring) == 1
    assert len(wiring.ctx.spawn.spawned) == 1
    assert wiring.ctx.spawn.spawned[0]["agent"] == A.ADVISOR_AGENT_NAME


def test_an_automatic_draft_carries_the_flag_and_is_recorded_as_automatic(wiring, A):
    """``auto`` is what licenses the UI's pre-fill, and the kind is what tells a user who started this."""
    grant(wiring, REPO)
    card(wiring, "a_auto1")
    assert autodraft(wiring) == 1

    row, = drafts_of(wiring)
    assert row["action_id"] == "a_auto1" and row["kind"] == "gate_analysis"
    assert row["request_json"]["auto"] is True, "the flag must survive in the stored envelope"
    draft = asyncio.run(wiring.broker.get(row["draft_id"]))
    assert draft.request.auto is True and draft.to_json()["request"]["auto"] is True
    assert draft.request.locale in A.ADVISOR_LOCALES

    recorded = rows_of(wiring, "advisor.auto_requested")
    assert [r.params["kind"] for r in recorded] == ["gate_analysis"]
    # No person asked, so no owner is named: an id here would read in the audit exactly like a click.
    assert recorded[0].params["user"] == ""
    assert rows_of(wiring, "advisor.requested") == []


def test_a_clicked_draft_is_still_recorded_as_a_click(wiring, A):
    draft = request_draft(wiring, A)
    assert draft.request.auto is False
    assert drafts_of(wiring)[0]["request_json"]["auto"] is False
    assert [r.params["user"] for r in rows_of(wiring, "advisor.requested")] == ["owner-1"]
    assert rows_of(wiring, "advisor.auto_requested") == []


def test_a_client_cannot_claim_through_the_route_that_its_request_was_automatic(wiring, A):
    """§2.7. ``auto`` licenses a pre-filled question form, so a body that could set it could pre-fill
    the client's own answers into a form the human then submits."""
    from _aidlc_studio_backend.studio.handlers import advisor as handler

    services = SimpleNamespace(advisor=wiring.broker)
    request = CT.owner_request(
        "POST",
        "/advisor/draft",
        body={"action_id": ACTION, "kind": "gate_analysis", "locale": "en-US", "auto": True},
    )
    response = asyncio.run(handler.request_draft(services, request))
    assert response.status == 202
    payload = json.loads(response.text)
    assert payload["draft"]["request"]["auto"] is False
    assert drafts_of(wiring)[0]["request_json"]["auto"] is False
    assert [r.kind for r in rows_of(wiring, "advisor.requested")] == ["advisor.requested"]
    assert rows_of(wiring, "advisor.auto_requested") == []


def test_a_card_draws_one_automatic_draft_for_its_whole_life(wiring, A):
    grant(wiring, REPO)
    card(wiring, "a_auto1")
    wiring.ctx.spawn.next_result = fence(good_result())
    assert autodraft(wiring) == 1
    assert asyncio.run(wiring.broker.poll(drafts_of(wiring)[0]["draft_id"])).status == "ready"

    assert autodraft(wiring) == 0
    # A restart forgets `_auto_tried`, so prove the stored row is what stops the second spawn.
    assert asyncio.run(rebuilt(wiring, A).autodraft()) == 0
    assert len(wiring.ctx.spawn.spawned) == 1


def test_an_automatic_draft_that_failed_is_never_retried_automatically(wiring, A):
    """FR-ADV-010: a failure is not a reason to spend another spawn; only a human click asks again."""
    grant(wiring, REPO)
    card(wiring, "a_auto1")
    assert autodraft(wiring) == 1
    settled = asyncio.run(wiring.broker.poll(drafts_of(wiring)[0]["draft_id"]))
    assert settled.status == "failed" and settled.error == "bad_result"

    assert autodraft(wiring) == 0
    assert asyncio.run(rebuilt(wiring, A).autodraft()) == 0
    assert len(wiring.ctx.spawn.spawned) == 1

    clicked = asyncio.run(wiring.broker.request(req(A, action_id="a_auto1"), user="owner-1"))
    assert clicked.status == "running" and clicked.request.auto is False


def test_the_inflight_ceiling_bounds_automatic_drafts_and_never_a_click(wiring, A, K):
    grant(wiring, REPO)
    base = K.epoch_from_iso(NOW)
    for n in range(K.ADVISOR_AUTO_DRAFT_MAX_INFLIGHT + 1):
        card(wiring, f"a_auto{n}", created_at=K.iso_from_epoch(base + n))
    for _ in range(K.ADVISOR_AUTO_DRAFT_MAX_INFLIGHT):
        assert autodraft(wiring) == 1
    assert autodraft(wiring) == 0
    assert len(wiring.ctx.spawn.spawned) == K.ADVISOR_AUTO_DRAFT_MAX_INFLIGHT

    # The ceiling is about drafts nobody asked for: an explicit request is always honoured.
    assert request_draft(wiring, A).status == "running"
    assert len(wiring.ctx.spawn.spawned) == K.ADVISOR_AUTO_DRAFT_MAX_INFLIGHT + 1
    # ...and a click does not count against the next pass either.
    assert autodraft(wiring) == 0


def test_one_tick_starts_at_most_the_per_tick_limit_oldest_card_first(wiring, A, K):
    """Oldest first: a queue is worked oldest-first, so that card is the one the human reaches next."""
    grant(wiring, REPO)
    base = K.epoch_from_iso(NOW)
    for n in (3, 1, 2):                      # inserted out of order: rowid order must not decide
        card(wiring, f"a_auto{n}", created_at=K.iso_from_epoch(base + n))
    assert autodraft(wiring) == K.ADVISOR_AUTO_DRAFT_PER_TICK
    started = sorted(row["action_id"] for row in drafts_of(wiring))
    assert started == ["a_auto1", "a_auto2", "a_auto3"][: K.ADVISOR_AUTO_DRAFT_PER_TICK]


def test_a_degraded_question_card_is_never_drafted_ahead(wiring, A, K):
    """FR-Q-007: the parse is not trustworthy, and invented option letters in a form nobody asked for
    are worse than no draft."""
    grant(wiring, REPO)
    card(
        wiring,
        "a_degraded",
        card_type="question",
        evidence_json={"questions": {"mode": "degraded", "questions": []}},
    )
    assert autodraft(wiring) == 0
    assert wiring.ctx.spawn.spawned == []

    card(
        wiring,
        "a_structured",
        card_type="question",
        evidence_json={"questions": {"mode": "structured"}},
        created_at=K.iso_from_epoch(K.epoch_from_iso(NOW) + 1),
    )
    assert autodraft(wiring) == 1
    assert [row["action_id"] for row in drafts_of(wiring)] == ["a_structured"]
    # The kind comes from the table, not from the card's first offered kind.
    assert drafts_of(wiring)[0]["kind"] == "question_draft"


def test_a_card_type_with_no_automatic_kind_is_skipped(wiring, A):
    """``failure`` is the interesting one: a human may ask it for a diagnosis, but nobody asked here."""
    grant(wiring, REPO)
    for card_type in ("run", "prepare_commit", "failure"):
        card(wiring, f"a_{card_type}", card_type=card_type)
    assert autodraft(wiring) == 0
    assert wiring.ctx.spawn.spawned == []


def test_an_installation_wide_off_switch_drafts_nothing_and_says_nothing(wiring, A):
    """Silent by design: a refusal row here would be written on every tick, forever, for an install
    that simply never opted in — it would cost everything and say nothing."""
    card(wiring, "a_auto1")
    grant(wiring, REPO, enabled=False)
    assert autodraft(wiring) == 0

    grant(wiring, REPO)
    wiring.ctx.spawn = None
    assert autodraft(wiring) == 0
    assert asyncio.run(wiring.broker.settle_open()) == 0

    assert drafts_of(wiring) == []
    assert rows_of(wiring, "advisor.auto_requested") == []
    assert rows_of(wiring, "advisor.failed") == []


def real_gate_evidence(w, studio) -> dict:
    """The evidence blob the reconciler really writes onto this repository's gate card.

    Hand-written blobs are how a pre-draft guard can pass review while doing nothing: every fixture
    writes the two keys the guard reads, and none of them shows what a card derived from disk actually
    carries. This one comes out of ``derive_cards``, which is the only writer of ``evidence_json``.
    """
    record = studio.repo_registry.RepoRecord.from_row(w.store.get("repos", REPO))
    snap = w.projection.snapshot(record, "default", INTENT)
    seeds = w.projection.derive_cards(
        snap, findings=(), binding=None, install=None, breakers=(), live_actions=()
    )
    return next(seed for seed in seeds if seed.type == "gate").evidence


def test_a_real_gate_card_is_drafted_although_its_stage_owns_a_questions_file(wiring, A, studio, monkeypatch):
    """The FR-Q-007 guard belongs to question drafts only, and this is why it has to be scoped.

    The grouped file capability can be unavailable. That degrades question answering, but must not
    block a gate analysis for the same stage.
    """
    monkeypatch.setattr(studio.constants, "GROUPED_ANSWERS_VERIFIED", False)
    evidence = real_gate_evidence(wiring, studio)
    assert evidence["questions"]["mode"] == "degraded"

    grant(wiring, REPO)
    card(wiring, "a_auto1", evidence_json=evidence)
    assert autodraft(wiring) == 1
    assert drafts_of(wiring)[0]["kind"] == "gate_analysis"
    assert wiring.ctx.spawn.spawned[0]["agent"] == A.ADVISOR_AGENT_NAME


def test_an_install_wide_refusal_stops_the_pass_and_spends_no_cards_one_shot(wiring, A, K):
    """A missing agent file is not this card's fault, and a card gets exactly one automatic draft.

    This refusal happens before any row is written, so nothing durable remembers the attempt: keeping
    the card marked would spend its one shot (FR-ADV-010) on a broken install and leave it undrafted
    until a human clicked. Stopping the pass is the other half — every remaining candidate would refuse
    for the same reason, and each would write its own Activity row on every tick.
    """
    grant(wiring, REPO)
    base = K.epoch_from_iso(NOW)
    for n in (1, 2, 3):
        card(wiring, f"a_auto{n}", created_at=K.iso_from_epoch(base + n))
    wiring.agents.clear()                        # the app's own agent file is gone

    assert autodraft(wiring, limit=3) == 0
    assert drafts_of(wiring) == [] and wiring.ctx.spawn.spawned == []
    # One row for the pass, not one per queued card.
    assert [r.params["reason"] for r in rows_of(wiring, "advisor.failed")] == ["agent_missing"]

    wiring.agents.append(
        FakeAgentInfo(name=A.ADVISOR_AGENT_NAME, filename="aidlc-studio--advisor.json")
    )
    assert autodraft(wiring) == 1                # same process: unmarked, so the shot was not spent
    assert [row["action_id"] for row in drafts_of(wiring)] == ["a_auto1"]


def test_only_a_card_still_waiting_for_the_human_is_drafted_ahead(wiring, A, K):
    """``Queued`` is the whole candidate set, because it is the only status where nobody has decided.

    ``Delivering``/``Delivered``/``Processing`` carry a submission already on its way to the host,
    ``NotDelivered`` carries one whose delivery failed and can be re-queued, and ``Draft`` is a
    submission a human is composing right now. A recommendation drafted for any of them arrives after
    the decision it was meant to inform.
    """
    grant(wiring, REPO)
    base = K.epoch_from_iso(NOW)
    moved = ("Draft", "Delivering", "Delivered", "Processing", "NotDelivered")
    for n, status in enumerate(moved, start=1):
        card(wiring, f"a_{status}", status=status, created_at=K.iso_from_epoch(base + n))
    assert autodraft(wiring, limit=len(moved)) == 0
    assert wiring.ctx.spawn.spawned == []

    card(wiring, "a_queued")                     # created_at NOW: the oldest, and the only candidate
    assert autodraft(wiring) == 1
    assert [row["action_id"] for row in drafts_of(wiring)] == ["a_queued"]


def test_clicked_drafts_never_use_up_the_ceiling_on_drafts_nobody_asked_for(wiring, A, K):
    """The ceiling exists to bound what Studio spends unasked; a click is asked for.

    Counting clicks would make the opt-in punish the people who never took it: two humans reading two
    drafts in two tabs would silently switch the pre-draft off for the whole installation.
    """
    grant(wiring, REPO)
    card(wiring, "a_auto1")
    for _ in range(K.ADVISOR_AUTO_DRAFT_MAX_INFLIGHT + 1):
        assert request_draft(wiring, A).status == "running"      # clicks, on another card
        wiring.clock.advance(1)

    assert autodraft(wiring) == 1
    auto = [row["action_id"] for row in drafts_of(wiring) if row["request_json"]["auto"]]
    assert auto == ["a_auto1"]


def test_a_draft_nobody_asked_for_is_written_in_the_configured_language(wiring, A, K):
    """``locale`` defaults to ``"auto"``, which means "follow the browser" — and there is no browser.

    Raising ``unsupported_locale`` on a request no human made would switch the feature off for every
    install that left the default alone, so the fallback is the first supported locale.
    """
    base = K.epoch_from_iso(NOW)
    card(wiring, "a_auto_default", created_at=K.iso_from_epoch(base + 1))
    card(wiring, "a_auto_zh", created_at=K.iso_from_epoch(base + 2))

    grant(wiring, REPO)                                          # settings.locale() == "auto"
    assert autodraft(wiring) == 1
    grant(wiring, REPO, locale="zh-CN")
    assert autodraft(wiring) == 1

    locales = {row["action_id"]: row["request_json"]["locale"] for row in drafts_of(wiring)}
    assert locales == {"a_auto_default": A.ADVISOR_LOCALES[0], "a_auto_zh": "zh-CN"}


def test_a_card_that_already_holds_a_usable_draft_is_left_alone(wiring, A):
    """The common shape the moment a grant is given: the cards already waiting often carry a draft
    somebody clicked for by hand. A second spawn would spend real model usage on a duplicate and —
    because the block shows the newest usable draft — displace the one the human may be reading."""
    grant(wiring, REPO)
    card(wiring, "a_ready")
    wiring.ctx.spawn.next_result = fence(good_result())
    clicked = request_draft(wiring, A, action_id="a_ready")
    assert asyncio.run(wiring.broker.poll(clicked.draft_id)).status == "ready"

    assert autodraft(wiring) == 0
    # A restart forgets `_auto_tried`, so prove the clicked row is what stops the spawn.
    assert asyncio.run(rebuilt(wiring, A).autodraft()) == 0
    assert len(wiring.ctx.spawn.spawned) == 1


def test_a_draft_that_left_the_human_with_nothing_does_not_stop_the_pre_draft(wiring, A, K):
    """``failed`` and ``expired`` are exactly when being early is worth a spawn, and one drafted answer
    is not the whole card — so neither shape counts as "the answer is already there"."""
    grant(wiring, REPO)
    base = K.epoch_from_iso(NOW)
    card(wiring, "a_failed", created_at=K.iso_from_epoch(base + 1))
    failed = request_draft(wiring, A, action_id="a_failed")       # the default spawn result is unparseable
    assert asyncio.run(wiring.broker.poll(failed.draft_id)).status == "failed"

    card(
        wiring,
        "a_one_answer",
        card_type="question",
        evidence_json={"questions": {"mode": "structured"}},
        created_at=K.iso_from_epoch(base + 2),
    )
    one = request_draft(wiring, A, action_id="a_one_answer", kind="question_draft", question_index=1)
    assert one.status == "running"

    assert autodraft(wiring) == 1
    assert autodraft(wiring) == 1
    auto = sorted(row["action_id"] for row in drafts_of(wiring) if row["request_json"]["auto"])
    assert auto == ["a_failed", "a_one_answer"]


def test_settle_open_collects_a_finished_draft_with_nobody_polling_it(wiring, A):
    """The pass a pre-draft depends on: the host prunes a finished subagent's whole answer after an
    hour, and by construction no browser is open on the card to poll it."""
    assert asyncio.run(wiring.broker.settle_open()) == 0

    wiring.ctx.spawn.next_result = fence(good_result())
    draft = request_draft(wiring, A)
    assert draft.status == "running"

    assert asyncio.run(wiring.broker.settle_open()) == 1
    settled = asyncio.run(wiring.broker.get(draft.draft_id))
    assert settled.status == "ready" and settled.result is not None
    # Nothing is open any more, so a settled draft is never counted twice.
    assert asyncio.run(wiring.broker.settle_open()) == 0


def test_a_draft_whose_poll_raises_does_not_abort_the_settle_pass(wiring, A, E):
    """Every path inside ``poll`` settles the draft it was given, so the swallow is for the row that
    goes wrong for a reason this module did not foresee — the drafts behind it must still settle."""
    wiring.ctx.spawn.next_result = fence(good_result())
    first = request_draft(wiring, A)
    wiring.clock.advance(5)
    second = request_draft(wiring, A)
    real = wiring.broker.poll

    async def poll(draft_id: str):
        if draft_id == first.draft_id:
            raise E.StudioError("action_not_found", "the card is gone")
        return await real(draft_id)

    wiring.broker.poll = poll
    assert asyncio.run(wiring.broker.settle_open()) == 1
    assert asyncio.run(wiring.broker.get(second.draft_id)).status == "ready"
    assert asyncio.run(wiring.broker.get(first.draft_id)).status == "running"


# --------------------------------------------------------------------------- #
# plan drafts (§1.18a, FR-NEW-006): a card-less draft for an intent that does not exist yet
# --------------------------------------------------------------------------- #


@pytest.fixture
def P(studio):
    return studio.plan


def plan_advice_module(A):
    """``plan_advice`` from the same package as the advisor under test."""
    return importlib.import_module(A.__name__.rsplit(".", 1)[0] + ".plan_advice")


def plan_req(A, **over: Any):
    from _aidlc_studio_backend.studio.plan import PlanRequest

    base = dict(
        repo_id=REPO,
        space="default",
        objective="Export the ledger to CSV",
        context=None,
        project_type=None,
        locale="en-US",
        current=PlanRequest.from_json({}, repo_id=REPO),
    )
    base.update(over)
    return plan_advice_module(A).PlanDraftRequest(**base)


def repo_record(w):
    from _aidlc_studio_backend.studio.repo_registry import RepoRecord

    return RepoRecord.from_row(w.store.get("repos", REPO))


def request_plan(w, A, **over: Any):
    return asyncio.run(w.broker.request_plan(plan_req(A, **over), repo_record(w), user="owner-1"))


def spawned_package(w, index: int = -1) -> dict:
    """The evidence package the spawn carried, read back out of the task text."""
    task = w.ctx.spawn.spawned[index]["task"]
    return json.loads(task.split("```json\n", 1)[1].split("\n```", 1)[0])


def plan_rows(package: dict) -> dict[str, dict]:
    return {row["field"]: row for row in package["questions"]["questions"]}


def pick(package: dict, field: str, *tokens: str) -> dict:
    """One suggested answer by letter, the way the request contract asks for it."""
    row = plan_rows(package)[field]
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return {
        "question_index": row["index"],
        "option_letters": [letters[row["values"].index(token)] for token in tokens],
        "answer": ", ".join(tokens),
    }


def plan_result(*answers: dict, **over: Any) -> dict:
    payload = good_result(verdict=None, suggested_answers=list(answers),
                          summary="A small change to an existing module.")
    payload.update(over)
    return payload


def settle_plan(w, A, compose, **over: Any):
    """Request, then answer *against the package that was actually sent*, then poll.

    The letters depend on the questionnaire, which exists only after the spawn; the fake's record is
    mutable, so the answer is written into it before the poll reads it.
    """
    draft = request_plan(w, A, **over)
    package = spawned_package(w)
    w.ctx.spawn.results[w.ctx.spawn.spawned[-1]["id"]].result = fence(compose(package))
    return asyncio.run(w.broker.poll(draft.draft_id)), package


def grid_on(w, scope: str) -> set[str]:
    grid = w.reader.read_scope_grid(w.repo, ".kiro")[scope]
    return {slug for slug, value in grid.items() if str(value).upper() == "EXECUTE"}


def always_slugs(w) -> set[str]:
    return {node.slug for node in w.reader.read_stage_graph(w.repo, ".kiro") if node.execution == "ALWAYS"}


def tree_hash(root: Path) -> dict[str, str]:
    """Every regular file under ``root`` (``.git`` skipped) by relative path -> sha256."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if ".git" in rel.parts or path.is_symlink() or not path.is_file():
            continue
        out[str(rel)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def test_a_plan_draft_needs_no_card_no_intent_dir_and_no_stage(wiring, A):
    """The card table is never consulted: with ``FakeActions`` refusing every id, the draft still spawns,
    is stored under the sentinel ``plan:<repo_id>``, and its Activity row names the repository and no
    action (§1.18a). The package carries an all-``None`` intent, no stage, and the wizard's questions."""
    wiring.actions.rec = None
    draft = request_plan(wiring, A)
    assert draft.kind == "plan_draft" and draft.status == "running"
    assert draft.action_id == "plan:" + REPO
    assert len(wiring.ctx.spawn.spawned) == 1
    assert wiring.ctx.spawn.spawned[0]["agent"] == A.ADVISOR_AGENT_NAME

    rows = drafts_of(wiring)
    assert [row["action_id"] for row in rows] == ["plan:" + REPO]
    assert "neutrality" not in rows[0]["request_json"]
    requested = rows_of(wiring, "advisor.requested")
    assert len(requested) == 1
    assert requested[0].action_id is None and requested[0].repo_id == REPO
    assert requested[0].params["kind"] == "plan_draft"

    package = spawned_package(wiring)
    assert package["kind"] == "plan_draft"
    assert package["intent"] == {
        "slug": None, "title": None, "scope": None, "current_stage": None, "phase": None,
    }
    assert package["stage"] is None and package["failure"] is None
    assert package["artifacts"] == [] and package["findings"] == []
    assert package["plan"] is not None
    assert package["plan"]["objective"] == "Export the ledger to CSV"
    assert package["questions"]["source"] == "wizard"
    count = len(package["questions"]["questions"])
    assert rows[0]["request_json"]["question_indices"] == list(range(1, count + 1))
    assert not wiring.denied_log.exists()


def test_the_plan_questionnaire_is_the_wizards_own_fields(wiring, A, K, P):
    """Q1 the scope files, Q2/Q3 the depths, Q4 the review caps, then one multi-select per phase in
    ``PHASES`` order listing that phase's stages in graph order — and no question for a phase whose
    stages all always run, because there the human has nothing to choose either."""
    request_plan(wiring, A)
    rows = spawned_package(wiring)["questions"]["questions"]
    scopes = wiring.reader.read_scopes(wiring.repo, ".kiro")
    graph = wiring.reader.read_stage_graph(wiring.repo, ".kiro")

    assert [row["field"] for row in rows[:4]] == ["scope", "depth", "test_strategy", "review_cap"]
    assert rows[0]["values"] == [meta.name for meta in scopes]
    assert rows[1]["values"] == list(P.DEPTHS)
    assert rows[2]["values"] == list(P.TEST_STRATEGIES)
    assert rows[3]["values"] == list(P.REVIEW_CAPS)
    assert all(row["multi"] is False for row in rows[:4])

    with_choice = [
        phase for phase in K.PHASES
        if any(node.phase == phase and node.execution != "ALWAYS" for node in graph)
    ]
    assert [row["field"] for row in rows[4:]] == [f"stages:{phase}" for phase in with_choice]
    for row in rows[4:]:
        phase = row["field"].split(":", 1)[1]
        assert row["multi"] is True
        assert row["values"] == [node.slug for node in graph if node.phase == phase]
        assert len(row["options"]) == len(row["values"])


def test_a_plan_draft_resolves_letters_into_a_plan_request_patch(wiring, A, P):
    """Letters in, a ``PlanRequest`` patch out: the four settings as tokens, ``scope == base_scope``, and
    ``overrides`` already the deltas against the proposed scope's grid row. The wire shape gains exactly
    one key, ``result`` keeps its nine, and there is no neutrality object to report."""
    on = grid_on(wiring, "poc")
    always = always_slugs(wiring)
    chosen: dict[str, Any] = {}

    def compose(package: dict) -> dict:
        row = next(
            row for field, row in plan_rows(package).items()
            if field.startswith("stages:")
            and any(slug not in on and slug not in always for slug in row["values"])
            and all(slug in on for slug in row["values"] if slug in always)
        )
        listed = [slug for slug in row["values"] if slug in on]
        extra = next(slug for slug in row["values"] if slug not in on and slug not in always)
        chosen.update(field=row["field"], extra=extra)
        return plan_result(
            pick(package, "scope", "poc"),
            pick(package, "depth", P.DEPTHS[0]),
            pick(package, "test_strategy", P.TEST_STRATEGIES[2]),
            pick(package, "review_cap", P.REVIEW_CAPS[1]),
            pick(package, row["field"], *listed, extra),
        )

    draft, _package = settle_plan(wiring, A, compose)
    assert draft.status == "ready" and draft.error is None, draft.to_json()
    assert draft.plan_proposal == {
        "scope": "poc",
        "base_scope": "poc",
        "depth": P.DEPTHS[0],
        "test_strategy": P.TEST_STRATEGIES[2],
        "review_cap": P.REVIEW_CAPS[1],
        "overrides": {chosen["extra"]: True},
        "unresolved": [],
    }
    assert draft.neutrality is None
    payload = draft.to_json()
    assert set(payload) == {
        "draft_id", "action_id", "kind", "status", "request", "result", "error",
        "created_at", "updated_at", "expires_at", "neutrality", "plan_proposal",
    }
    assert set(payload["result"]) == set(A.DRAFT_RESULT_KEYS)
    assert payload["plan_proposal"] == draft.plan_proposal


def test_a_plan_draft_never_turns_off_a_stage_that_always_runs(wiring, A):
    """Through ``poll``: a phase list that omits an ALWAYS stage the scope runs produces no override for
    it (the unit-level rules live in ``tests/test_plan_advice.py``)."""
    on = grid_on(wiring, "poc")
    always = always_slugs(wiring)

    def compose(package: dict) -> dict:
        row = next(
            row for field, row in plan_rows(package).items()
            if field.startswith("stages:")
            and any(slug in always and slug in on for slug in row["values"])
            and any(slug in on and slug not in always for slug in row["values"])
        )
        without_always = [slug for slug in row["values"] if slug in on and slug not in always]
        return plan_result(pick(package, "scope", "poc"), pick(package, row["field"], *without_always))

    draft, _package = settle_plan(wiring, A, compose)
    assert draft.status == "ready", draft.to_json()
    assert draft.plan_proposal["overrides"] == {}
    assert not any(value is False for value in draft.plan_proposal["overrides"].values())
    assert draft.plan_proposal["unresolved"] == []


def test_a_plan_draft_is_settled_without_a_neutrality_probe(wiring, A):
    """There is no intent to keep neutral (FR-ADV-007): the card table is never read, ``neutrality`` is
    ``None``, nothing fails ``evidence_mutated`` and no ``critical`` row is written."""
    from _aidlc_studio_backend.studio.storage import ActivityQuery

    wiring.actions.rec = None
    draft, _package = settle_plan(
        wiring, A, lambda package: plan_result(pick(package, "scope", "poc"))
    )
    assert draft.status == "ready" and draft.error is None
    assert draft.neutrality is None
    assert rows_of(wiring, "advisor.failed") == []
    critical, _ = asyncio.run(wiring.activity.query(ActivityQuery(severity="critical", limit=100)))
    assert critical == []
    assert [row.kind for row in rows_of(wiring, "advisor.completed")] == ["advisor.completed"]


def test_a_plan_draft_leaves_the_repository_tree_byte_for_byte_unchanged(wiring, A):
    """The broker's own request and settle path writes nothing under the repository (FR-ADV-007): the
    catalogue, the workspace signals and the registry are reads, and the poll resolves letters from the
    row. The agent's read-only behaviour is the constraints' job (no tools but thinking, §1.18), not
    this test's — the spawn here is a fake that runs nothing."""
    before = tree_hash(wiring.repo)
    draft, _package = settle_plan(
        wiring, A, lambda package: plan_result(pick(package, "scope", "poc"))
    )
    assert draft.status == "ready"
    assert tree_hash(wiring.repo) == before
    assert not wiring.denied_log.exists()


def test_a_second_click_while_a_plan_draft_is_running_returns_the_same_draft(wiring, A):
    """One live plan draft per repository *and objective*: a repeated click is answered with the running
    draft, a different objective starts another, and two concurrent clicks spawn once (the per-repo
    lock covers the idempotency select through the insert)."""
    first = request_plan(wiring, A)
    second = request_plan(wiring, A)
    assert first.draft_id == second.draft_id
    assert len(wiring.ctx.spawn.spawned) == 1

    third = request_plan(wiring, A, objective="Rename the ledger table")
    assert third.draft_id != first.draft_id
    assert len(wiring.ctx.spawn.spawned) == 2

    async def both():
        req = plan_req(A, objective="Add a nightly export job")
        rec = repo_record(wiring)
        return await asyncio.gather(
            wiring.broker.request_plan(req, rec, user="owner-1"),
            wiring.broker.request_plan(req, rec, user="owner-1"),
        )

    left, right = asyncio.run(both())
    assert left.draft_id == right.draft_id
    assert len(wiring.ctx.spawn.spawned) == 3


def test_a_second_click_in_another_locale_space_or_with_other_picks_starts_a_new_draft(wiring, A, P):
    """"Same request" is the same objective AND the same space, locale and picks: a draft asked for in
    Chinese, in another space, or against another scope answers a different questionnaire, so handing
    it back would show a proposal for a request the human did not make (§1.18a). The original request,
    repeated, still gets the original draft."""
    first = request_plan(wiring, A)
    other_locale = request_plan(wiring, A, locale="zh-CN")
    other_picks = request_plan(
        wiring, A, current=P.PlanRequest.from_json({"scope": "bugfix"}, repo_id=REPO)
    )
    other_space = request_plan(wiring, A, space="team")
    ids = {first.draft_id, other_locale.draft_id, other_picks.draft_id, other_space.draft_id}
    assert len(ids) == 4
    assert len(wiring.ctx.spawn.spawned) == 4
    assert request_plan(wiring, A).draft_id == first.draft_id
    assert request_plan(wiring, A, locale="zh-CN").draft_id == other_locale.draft_id
    assert len(wiring.ctx.spawn.spawned) == 4
    subjects = [row["request_json"]["subject"] for row in drafts_of(wiring)]
    assert all(set(subject) == {"repo_id", "space", "objective_sha256", "picks_sha256"}
               for subject in subjects)


def test_the_stored_picks_carry_no_objective_label_or_context(wiring, A, P):
    """``request_json["plan_current"]`` is the resolver's input and what the Activity drawer reads back
    (§1.18a): the six settings, never the prose ``PlanRequest.to_json`` would also carry — the
    objective already lives in the package."""
    current = P.PlanRequest.from_json(
        {"scope": "poc", "depth": "Minimal", "overrides": {},
         "objective": "Export the ledger to CSV", "label": "Ledger export", "context": "Finance asked."},
        repo_id=REPO,
    )
    request_plan(wiring, A, current=current)
    stored = drafts_of(wiring)[0]["request_json"]["plan_current"]
    assert stored == {
        "space": "default", "scope": "poc", "depth": "Minimal", "test_strategy": None,
        "review_cap": None, "overrides": {},
    }
    for key in ("objective", "label", "context"):
        assert key not in stored, key


def test_a_proposal_for_the_humans_own_scope_starts_from_their_overrides_through_poll(wiring, A, P):
    """Through ``poll``: ``current`` carries a scope and an override the model's answer leaves alone,
    and the resolved ``overrides`` start from it — the human's toggle survives a proposal for the same
    scope, and the package showed the agent exactly those picks (§1.18a)."""
    on = grid_on(wiring, "poc")
    graph = wiring.reader.read_stage_graph(wiring.repo, ".kiro")
    toggled = next(node.slug for node in graph if node.slug not in on and node.execution != "ALWAYS")
    toggled_phase = next(node.phase for node in graph if node.slug == toggled)
    current = P.PlanRequest.from_json(
        {"scope": "poc", "depth": "Standard", "overrides": {toggled: True}}, repo_id=REPO
    )

    def compose(package: dict) -> dict:
        row = next(
            row for field, row in plan_rows(package).items()
            if field.startswith("stages:") and field != f"stages:{toggled_phase}"
            and any(slug in on for slug in row["values"])
        )
        listed = [slug for slug in row["values"] if slug in on]   # the row's own selection: no delta
        return plan_result(pick(package, "scope", "poc"), pick(package, row["field"], *listed))

    draft, package = settle_plan(wiring, A, compose, current=current)
    assert draft.status == "ready", draft.to_json()
    assert draft.plan_proposal["scope"] == "poc" and draft.plan_proposal["base_scope"] == "poc"
    assert draft.plan_proposal["overrides"] == {toggled: True}
    assert draft.plan_proposal["unresolved"] == []
    assert package["plan"]["current"] == {
        "scope": "poc", "depth": "Standard", "test_strategy": None, "review_cap": None,
        "overrides": {toggled: True},
    }
    stored = drafts_of(wiring)[0]["request_json"]["plan_current"]
    assert stored["scope"] == "poc" and stored["overrides"] == {toggled: True}


def test_a_secret_straddling_the_readme_cut_is_masked_not_split(wiring, A, K):
    """The reader hands the broker the WHOLE bounded README and ``_clean`` redacts before it cuts: a
    credential whose first eight characters sit inside the first ``MAX_README_CHARS`` is masked as a
    whole, where a cut made first would have left ``AKIABBBB`` — a half no scanner recognises — in the
    package. The excerpt still never exceeds ``MAX_README_CHARS`` (§1.18a, PRD §16.2)."""
    head = "x" * (K.MAX_README_CHARS - 8)
    key = "AKIA" + "B" * 16
    (wiring.repo / "README.md").write_text(head + key + (" and then the rest of the file." * 40))
    request_plan(wiring, A)
    excerpt = spawned_package(wiring)["plan"]["workspace"]["readme_excerpt"]
    assert excerpt is not None and len(excerpt) <= K.MAX_README_CHARS
    assert excerpt.startswith(head)
    assert "AKIA" not in excerpt
    assert "redact" in excerpt.lower()
    assert "AKIA" not in wiring.ctx.spawn.spawned[-1]["task"]


def test_a_secret_shaped_scope_name_never_reaches_the_task_raw(wiring, A):
    """Every catalogue string enters the package through the token funnel — the scope's ``name`` as
    much as its description — and every place that spells it (``plan.scopes``, Q1's ``values`` and
    option text, ``scope_grid``, the stored ``plan_grid``) spells it the same way, so the resolver's
    letters still land on one token (§1.18a). A project-owned scope file is the one way a repository
    can put an arbitrary string in a scope name."""
    leak = "AKIA" + "ABCDEFGHIJKLMNOP"
    (wiring.repo / ".kiro" / "scopes" / "aidlc-leak.md").write_text(
        f"---\nname: {leak}\ndescription: A scope whose name is a key\ndepth: Standard\n---\n# Leak\n"
    )
    assert leak in [meta.name for meta in wiring.reader.read_scopes(wiring.repo, ".kiro")]
    request_plan(wiring, A)
    task = wiring.ctx.spawn.spawned[-1]["task"]
    assert leak not in task

    masked = wiring.broker._clean(leak, A.TITLE_CHARS, roots=())
    assert masked != leak and "AKIA" not in masked
    package = spawned_package(wiring)
    q1 = plan_rows(package)["scope"]
    assert masked in q1["values"]
    assert any(option["text"].startswith(masked + " — ") for option in q1["options"])
    assert masked in [row["name"] for row in package["plan"]["scopes"]]
    assert masked in package["plan"]["scope_grid"]
    assert masked in drafts_of(wiring)[0]["request_json"]["plan_grid"]
    assert [row["name"] for row in package["plan"]["scopes"]] == q1["values"]
    assert list(package["plan"]["scope_grid"]) == q1["values"]


def test_a_crowded_catalogue_still_fits_the_package_cap(wiring, A, K, P):
    """Forty project-owned scopes with 300-character descriptions, a 4000-character objective and as
    much context: the package carries Q1's 26 scopes (its ``scopes`` and ``scope_grid`` are the scopes
    the agent can actually pick) and ``_fit`` brings the rest under ``MAX_EVIDENCE_PACKAGE_CHARS``
    without touching a question row (§1.18a)."""
    from _aidlc_studio_backend.studio.aidlc_reader import ScopeMeta

    rec = repo_record(wiring)
    real = wiring.plan.catalog(rec, ".kiro")
    slugs = [node.slug for node in real.stages]
    scopes = tuple(
        ScopeMeta(
            name=f"scope-{n:02d}", depth="Standard", test_strategy=None, review_cap=None, skeleton=None,
            runner=None, keywords=tuple(f"keyword-{k:02d}" for k in range(12)),
            description=f"Scope {n:02d}: " + ("covers a great deal of ground " * 12), plugin=None,
            project_owned=True,
        )
        for n in range(40)
    )
    assert all(len(meta.description) >= 300 for meta in scopes)
    grid = {meta.name: {slug: "EXECUTE" for slug in slugs} for meta in scopes}
    fake = SimpleNamespace(
        scopes=scopes, stages=real.stages, grid=grid, engine_version=real.engine_version,
        grid_row=lambda scope: {slug: True for slug in slugs} if scope in grid else {},
    )
    signals = wiring.reader.read_workspace_signals(wiring.repo)
    request = plan_req(
        A, objective="o" * 4000, context="c" * 4000,
        current=P.PlanRequest.from_json({"scope": "scope-30"}, repo_id=REPO),
    )
    package = wiring.broker.build_plan_package(request, rec, fake, signals)
    body = json.dumps(package.to_json(), ensure_ascii=False, separators=(",", ":"))
    assert len(body) <= K.MAX_EVIDENCE_PACKAGE_CHARS
    q1 = plan_rows(package.to_json())["scope"]["values"]
    assert len(q1) == 26 and q1 == [f"scope-{n:02d}" for n in range(26)]
    assert [row["name"] for row in package.plan["scopes"]] == q1
    assert set(package.plan["scope_grid"]) <= set(q1)
    assert len(package.plan["objective"]) >= A._OBJECTIVE_FLOOR_CHARS
    # The questionnaire is what the draft is about: every row survived, options and all.
    unshrunk = plan_advice_module(A).build_plan_questions(fake)
    assert [len(row["options"]) for row in package.questions["questions"]] == [
        len(row["options"]) for row in unshrunk["questions"]
    ]


def test_shrink_plan_sheds_the_core_last_and_reaches_a_fixed_point(A):
    """Each ``_shrink_plan`` step strictly shrinks the rendered package or is skipped, so ``_fit``
    terminates; the soft evidence goes first (README, intents, keywords, descriptions, listing,
    context) and only then the core — ``needs``, stage names, every ``scope_grid`` row but the current
    scope's and the first eight Q1 scopes', the objective toward its floor. ``questions`` is never
    touched (§1.18a)."""
    slugs = [f"stage-{n:02d}" for n in range(30)]
    scopes = [{"name": f"scope-{n:02d}", "description": "d" * 300, "keywords": ["k"] * 12,
               "depth": "Standard", "test_strategy": None, "review_cap": None, "stage_count": 30,
               "project_owned": True} for n in range(26)]
    plan = {
        "objective": "o" * 4000, "context": "c" * 4000, "project_type": None,
        "current": {"scope": "scope-20", "depth": "", "test_strategy": None, "review_cap": None,
                    "overrides": {}},
        "scopes": scopes,
        "stages": [{"slug": slug, "number": str(n), "name": f"Stage {n}", "phase": "construction",
                    "always": False, "per_unit": False, "needs": slugs[:n]} for n, slug in enumerate(slugs)],
        "notes": [], "scope_grid": {row["name"]: list(slugs) for row in scopes},
        "existing_intents": [{"slug": f"intent-{n}", "scope": "poc", "status": "active"} for n in range(10)],
        "workspace": {"top_level": [f"dir-{n}/" for n in range(60)], "manifests": ["Makefile"],
                      "readme_excerpt": "r" * 3000, "truncated": False},
        "vocabulary": {"depth": [], "test_strategy": [], "review_cap": []},
    }
    questions = {"mode": "structured", "source": "wizard", "questions": [
        {"index": 1, "prompt": "Which scope?", "multi": False, "field": "scope",
         "values": [row["name"] for row in scopes],
         "options": [{"letter": "A", "text": "x", "is_other": False}], "answered": False, "answer": None},
    ]}
    package = A.EvidencePackage(
        version=A.EVIDENCE_VERSION, kind="plan_draft", action_id="plan:" + REPO, generated_at=NOW,
        locale="en-US", repo_label="gate-demo",
        intent={"slug": None, "title": None, "scope": None, "current_stage": None, "phase": None},
        stage=None, artifacts=(), findings=(), questions=questions, audit_excerpt=(), failure=None,
        plan=plan,
    )

    def milestone(pkg) -> str:
        p = pkg.plan
        if p["workspace"]["readme_excerpt"]: return "readme"
        if p["existing_intents"]: return "intents"
        if any(r["keywords"] for r in p["scopes"]): return "keywords"
        if any(len(r["description"]) > A._DESCRIPTION_FLOOR_CHARS for r in p["scopes"]): return "descriptions"
        if p["workspace"]["top_level"]: return "top_level"
        if p["context"]: return "context"
        if any(r["needs"] for r in p["stages"]): return "needs"
        if any(r["name"] for r in p["stages"]): return "names"
        if len(p["scope_grid"]) > A._GRID_ROWS_KEPT + 1: return "grid"
        if len(p["objective"]) > A._OBJECTIVE_FLOOR_CHARS: return "objective"
        return "floor"

    order = ["readme", "intents", "keywords", "descriptions", "top_level", "context", "needs", "names",
             "grid", "objective", "floor"]
    seen = [milestone(package)]
    current = package
    for _ in range(A._SHRINK_ROUNDS):
        nxt = A.AdvisorBroker._shrink_plan(current)
        if nxt is current:
            break
        assert len(A._dumps(nxt.to_json())) < len(A._dumps(current.to_json()))
        assert nxt.questions == questions
        current = nxt
        seen.append(milestone(current))
    else:
        pytest.fail("no fixed point")
    # Milestones only ever move forward through the documented order, and every one is visited.
    assert [order.index(m) for m in seen] == sorted(order.index(m) for m in seen)
    assert set(seen) == set(order)

    final = current.plan
    assert final["workspace"]["readme_excerpt"] is None and final["existing_intents"] == []
    assert all(row["keywords"] == [] for row in final["scopes"])
    assert all(len(row["description"]) <= A._DESCRIPTION_FLOOR_CHARS for row in final["scopes"])
    assert final["workspace"]["top_level"] == [] and final["context"] is None
    assert all(row["needs"] == [] and row["name"] == "" for row in final["stages"])
    assert set(final["scope_grid"]) == {f"scope-{n:02d}" for n in range(A._GRID_ROWS_KEPT)} | {"scope-20"}
    assert len(final["objective"]) == A._OBJECTIVE_FLOOR_CHARS
    assert A.AdvisorBroker._shrink_plan(current) is current


def test_an_expired_plan_draft_is_not_handed_back_as_live(wiring, A, K):
    stale = request_plan(wiring, A)
    wiring.clock.advance(K.ADVISOR_DRAFT_TTL_SECS + 1)
    fresh = request_plan(wiring, A)
    assert fresh.draft_id != stale.draft_id
    assert len(wiring.ctx.spawn.spawned) == 2


def test_a_whitespace_objective_is_refused_as_missing(A, E):
    """``read_json`` catches ``None``/``""``; the request refuses a blank-but-present objective with the
    same ``bad_body`` shape, so the route's two guards read as one rule."""
    PA = plan_advice_module(A)
    for body in ({}, {"objective": ""}, {"objective": "  \n\t "}):
        with pytest.raises(E.StudioError) as excinfo:
            PA.PlanDraftRequest.from_json(body, repo_id=REPO)
        assert excinfo.value.code == "bad_body", body
        assert excinfo.value.details["missing"] == ["objective"], body
    okay = PA.PlanDraftRequest.from_json({"objective": " Export the ledger "}, repo_id=REPO)
    assert okay.repo_id == REPO and okay.action_id == "plan:" + REPO
    assert okay.to_subject()["repo_id"] == REPO
    assert okay.current.scope == "" and okay.current.overrides == {}
    with pytest.raises(E.StudioError) as excinfo:
        PA.PlanDraftRequest.from_json({"objective": "x", "context": 3}, repo_id=REPO)
    assert excinfo.value.code == "bad_body"


def test_a_plan_resolution_defect_fails_the_draft_instead_of_the_poll(wiring, A):
    """A corrupt stored questionnaire is a defect the resolver meets after the parse succeeded; it must
    settle the draft ``failed``/``bad_result`` rather than 500 the poll or kill ``settle_open``'s tick."""
    draft = request_plan(wiring, A)
    package = spawned_package(wiring)
    wiring.ctx.spawn.results[wiring.ctx.spawn.spawned[-1]["id"]].result = fence(
        plan_result(pick(package, "scope", "poc"))
    )
    row = wiring.store.get("advisor_drafts", draft.draft_id)
    corrupt = {**row["request_json"], "plan_questions": ["not", "a", "mapping"]}
    wiring.store.update("advisor_drafts", draft.draft_id, {"request_json": corrupt})

    settled = asyncio.run(wiring.broker.poll(draft.draft_id))
    assert settled.status == "failed" and settled.error == "bad_result"
    assert settled.plan_proposal is None and settled.result is None
    details = [r.params.get("detail", "") for r in rows_of(wiring, "advisor.failed")]
    assert any(detail.startswith("plan_resolution_failed") for detail in details), details


def test_a_plan_draft_is_never_drawn_ahead(wiring, A):
    """FR-ADV-010: the standing grant drafts card kinds only. A granted repository with a queued card
    draws its gate draft and nothing else; the automatic table does not know the kind."""
    grant(wiring, REPO)
    card(wiring, "a_auto1")
    assert autodraft(wiring) == 1
    assert [row["kind"] for row in drafts_of(wiring)] == ["gate_analysis"]
    assert autodraft(wiring) == 0
    assert "plan_draft" not in A.ADVISOR_AUTO_KIND_BY_TYPE.values()
    assert A.ADVISOR_AUTO_KIND_BY_TYPE == {"gate": "gate_analysis", "question": "question_draft"}


def test_a_card_request_for_plan_draft_is_refused_as_not_applicable(wiring, A, E):
    with pytest.raises(E.StudioError) as excinfo:
        request_draft(wiring, A, kind="plan_draft")
    assert excinfo.value.code == "invalid_decision"
    assert drafts_of(wiring) == [] and wiring.ctx.spawn.spawned == []


def test_a_plan_draft_is_refused_while_the_advisor_is_switched_off(wiring, A, E):
    wiring.store.pref_set("settings", {"values": {"advisor": {"enabled": False}}, "updated_at": NOW})
    wiring.settings.load()
    with pytest.raises(E.StudioError) as excinfo:
        request_plan(wiring, A)
    assert excinfo.value.code == "advisor_unavailable"
    assert excinfo.value.details["reason"] == "disabled"
    assert drafts_of(wiring) == [] and wiring.ctx.spawn.spawned == []


def test_a_plan_draft_is_refused_when_there_is_no_spawn_seam(wiring, A, E):
    """No degraded fallback, and the refusal's Activity row names the repository, not a card."""
    wiring.ctx.spawn = None
    with pytest.raises(E.StudioError) as excinfo:
        request_plan(wiring, A)
    assert excinfo.value.code == "advisor_unavailable"
    assert excinfo.value.details["reason"] == "spawn_unavailable"
    assert drafts_of(wiring) == []
    failed = rows_of(wiring, "advisor.failed")
    assert [r.params["reason"] for r in failed] == ["spawn_unavailable"]
    assert failed[0].action_id is None and failed[0].repo_id == REPO


def test_the_plan_package_is_bounded_and_carries_no_repository_path(wiring, A, K):
    """A 200 KB README is evidence about the repository, not a budget: the excerpt is clipped, the
    package stays under ``MAX_EVIDENCE_PACKAGE_CHARS``, and the absolute path never reaches the task."""
    (wiring.repo / "README.md").write_text(
        f"# Demo\n\nThe repo lives at {wiring.repo}.\n\n" + ("lorem ipsum dolor sit amet " * 8000)
    )
    request_plan(wiring, A)
    task = wiring.ctx.spawn.spawned[-1]["task"]
    package = spawned_package(wiring)
    body = json.dumps(package, ensure_ascii=False, separators=(",", ":"))
    assert len(body) <= K.MAX_EVIDENCE_PACKAGE_CHARS
    assert len(task) <= K.MAX_EVIDENCE_PACKAGE_CHARS + 200
    assert str(wiring.repo) not in task
    excerpt = package["plan"]["workspace"]["readme_excerpt"]
    assert excerpt is not None and len(excerpt) <= K.MAX_README_CHARS
    assert "README.md" in package["plan"]["workspace"]["top_level"]
    for forbidden in ("cwd", "working_dir", "working_directory", "repo_path", "canonical_path"):
        assert forbidden not in _keys(package), forbidden


def test_the_plan_request_spells_out_the_kind_guidance(A):
    task = A.draft_result_contract(kind="plan_draft", question_indices=(1, 2))
    assert "THIS REQUEST (kind = plan_draft)" in task
    for key in A.DRAFT_RESULT_KEYS:
        assert f'"{key}"' in task, key
    for verdict in A.ADVISOR_VERDICTS:
        assert f'"{verdict}"' in task, verdict
    for confidence in A.ADVISOR_CONFIDENCE:
        assert f'"{confidence}"' in task, confidence
    assert "(cannot be turned off once selected)" in task
    assert "plan.objective" in task


def test_every_kind_has_guidance_in_the_request(A):
    """Adding a kind without telling the model what it means is how the live advisor once answered
    ``unknown_keys`` for every draft; the contract must name each kind's request."""
    for kind in A.ADVISOR_KINDS:
        task = A.draft_result_contract(kind=kind)
        assert f"THIS REQUEST (kind = {kind})" in task, kind


def test_a_plan_draft_expires_on_the_same_ttl_and_is_retired_by_expire_stale(wiring, A, K):
    first = request_plan(wiring, A)
    assert first.created_at == NOW
    assert first.expires_at == K.iso_from_epoch(K.epoch_from_iso(NOW) + K.ADVISOR_DRAFT_TTL_SECS)
    wiring.clock.advance(5)
    second = request_plan(wiring, A, objective="Rename the ledger table")
    assert asyncio.run(wiring.broker.expire_stale()) == 0

    wiring.clock.advance(K.ADVISOR_DRAFT_TTL_SECS)
    assert asyncio.run(wiring.broker.poll(first.draft_id)).status == "expired"
    assert asyncio.run(wiring.broker.expire_stale()) == 1
    assert asyncio.run(wiring.broker.get(second.draft_id)).status == "expired"
