"""Intent routes (§2.3): the read model, the plan, the session binding, the dispatch commands.

What matters most is each way a click could reach AI-DLC when it must not: Run creates a ``Queued`` card and sends
nothing (the wire text leaves only through ``POST /actions/{id}/submit``); pause and archive block every human-lane
dispatch and pause names the cards it blocked (C29); force stop is a confirmed, durable host-control record with no
wire text (C28); and a read route never mints a card (the queue is the reconciler's).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import conftest as CT
import fixtures as F
from test_handlers_auth import APP_ROOT, call, common, routes, sv  # noqa: F401 - fixtures

INTENT = "260904-gate-demo"
OTHER = "260904-second-intent"
STAGE = "requirements-analysis"
QUESTIONS_REL = f"inception/{STAGE}/{STAGE}-questions.md"
SUMMARY_KEYS = {"repo_id", "repo_label", "space", "intent_dir", "intent_key", "uuid", "slug", "scope",
                "registry_status", "title", "operational_state", "disk", "counts", "open_actions", "blocking_findings",
                "warn_findings", "session", "archived", "paused", "interrupted", "last_activity_at", "stable_boundary",
                "unstable", "is_active_cursor"}


def _state(marks: dict[str, str] | None = None) -> str:
    """A state file whose phase and stage agree, so no blocking finding hides the route under test."""
    return F.state_text(marks=marks or {STAGE: "?"}, fields={"Current Stage": STAGE, "Status": "Running",
                                                             "Next Stage": "user-stories",
                                                             "Lifecycle Phase": "INCEPTION", "Completed": "7"})


@pytest.fixture
def demo(repo_builder) -> Path:
    """One repository, one intent waiting at an open gate, nothing contradicting itself."""
    review = ("# Requirements\n\nOne requirement.\n\n## Review\n\n**Reviewer**: reviewer-agent\n**Iteration**: 1\n"
              "**Verdict**: READY\n\n### Advisory\n\n- Consider naming the store\n")
    return repo_builder.with_engine("payload").with_workspace().with_intent(
        INTENT, state=_state(), audit=F.gate_open_audit(STAGE),
        directive={"version": 1, "stage": STAGE, "state_sha256": "AUTO"},
        questions={QUESTIONS_REL: F.blank_answers(F.real_questions())},
        artifacts={f"inception/{STAGE}/requirements.md": review}).with_git().build()


@pytest.fixture
def repo(sv, demo):
    return sv.repos.add(str(demo), "demo")


@pytest.fixture
def slot(sv, fake_host, repo):
    """The canonical slot the UI would have created, bound to this intent."""
    key = f"aidlc-studio-{repo.repo_id}-{INTENT}"
    fake_host.add_slot(key, project=str(repo.canonical_path), agent="aidlc", app="")
    sv.host.attach(fake_host)
    return key


@pytest.fixture
def api(sv, routes, fake_host):
    """``api(method, path, body=None, **query) -> (status, body)``: one owner request through the route table."""
    def send(method: str, path: str, body: Any = None, **query: str) -> tuple[int, Any]:
        return call(sv, routes, method, path, CT.owner_request(method, path, host=fake_host, body=body, query=query))
    return send


def base(repo: Any, suffix: str = "") -> str:
    return f"/repos/{repo.repo_id}/intents/{INTENT}{suffix}"


def refusal(answer: tuple[int, Any]) -> tuple[int, str]:
    return answer[0], answer[1]["code"]


def bind(api: Any, repo: Any, slot: str | None) -> tuple[int, Any]:
    return api("POST", base(repo, "/session"), {"slot_key": slot})


def artifact(sv: Any, repo: Any, name: str) -> Any:
    metas, _truncated = sv.projection.artifacts(sv.projection.snapshot(repo, "default", INTENT))
    return next(meta for meta in metas if meta.name == name)


# --- the read model ---
def test_listing_intents_reports_the_summary_the_spaces_and_the_cursor(api, repo):
    status, body = api("GET", f"/repos/{repo.repo_id}/intents")
    assert status == 200 and set(body) == {"intents", "spaces", "active_space"}
    assert (body["spaces"], body["active_space"]) == (["default"], "default")
    summary = body["intents"][0]
    assert set(summary) == SUMMARY_KEYS and summary["intent_key"] == INTENT and summary["is_active_cursor"] is True
    assert set(summary["disk"]) == {"status", "lifecycle_phase", "current_stage", "next_stage", "state_version",
                                    "total_stages", "completed", "revision_count", "parked_at", "last_updated"}


def test_listing_intents_does_not_mint_any_action_card(sv, api, repo):
    """A GET must not change the queue: deriving cards is the reconciler's job, on its own schedule."""
    api("GET", f"/repos/{repo.repo_id}/intents")
    api("GET", base(repo))
    assert sv.storage.select("actions") == []


def test_listing_intents_filters_by_state_space_and_free_text(api, repo):
    path = f"/repos/{repo.repo_id}/intents"
    assert len(api("GET", path, q="gate-demo")[1]["intents"]) == 1
    assert api("GET", path, q="nothing-like-this")[1]["intents"] == []
    assert api("GET", path, state="Completed")[1]["intents"] == []
    assert refusal(api("GET", path, state="not-a-state")) == (400, "bad_param")


def test_listing_intents_refuses_an_unreadable_repository(sv, api, repo):
    sv.storage.update("repos", repo.repo_id, {"availability": "moved"})
    assert refusal(api("GET", f"/repos/{repo.repo_id}/intents")) == (409, "repo_unavailable")


def test_the_detail_is_a_superset_of_the_summary(api, repo):
    status, body = api("GET", base(repo))
    assert status == 200
    detail = body["intent"]
    assert SUMMARY_KEYS <= set(detail)
    assert {"state_sections", "phases", "stages", "findings", "actions", "directive", "markers", "audit_tail",
            "questions", "artifacts_count", "artifacts_truncated", "git", "binding", "engine",
            "active_directive_stage"} <= set(detail)
    assert detail["active_directive_stage"] == STAGE and detail["artifacts_count"] >= 1


def test_an_unknown_intent_is_a_404_and_a_malformed_key_is_a_400(api, repo):
    assert refusal(api("GET", f"/repos/{repo.repo_id}/intents/260904-nope")) == (404, "intent_not_found")
    assert refusal(api("GET", f"/repos/{repo.repo_id}/intents/not a key")) == (400, "bad_param")


def test_the_map_sends_the_graph_and_the_state_rows_for_the_ui_to_join(api, repo):
    status, body = api("GET", base(repo, "/map"))
    assert status == 200 and set(body) == {"map_inputs"}
    inputs = body["map_inputs"]
    assert set(inputs) == {"intent_key", "graph_version", "now", "nodes", "rows", "status", "phases", "current_stage",
                           "directive_stage", "scope_grid", "units", "active_unit", "artifacts", "skip_reasons",
                           "attempts"}
    assert inputs["intent_key"] == INTENT and any(node["slug"] == STAGE for node in inputs["nodes"])
    assert any(row["slug"] == STAGE and row["state"] == "awaiting_approval" for row in inputs["rows"])
    assert inputs["current_stage"] == inputs["directive_stage"] == STAGE


def test_artifacts_lists_metadata_and_filters_by_stage_and_kind(api, repo):
    path = base(repo, "/artifacts")
    status, body = api("GET", path)
    assert status == 200 and body["count"] == len(body["artifacts"]) and body["truncated"] is False
    assert set(body["artifacts"][0]) == {"artifact_id", "relpath", "name", "stage", "phase", "unit", "size", "mtime",
                                         "sha256", "kind", "renderable"}
    assert [a["kind"] for a in api("GET", path, kind="questions")[1]["artifacts"]] == ["questions"]
    body = api("GET", path, stage=STAGE)[1]
    assert body["count"] >= 1 and all(a["stage"] == STAGE for a in body["artifacts"])




def test_plan_scopes_describes_and_counts_every_installed_scope(api, repo):
    """The wizard compares scopes before picking one: their own descriptions, and their grid's stage counts."""
    status, body = api("GET", f"/repos/{repo.repo_id}/intents/plan/scopes")
    assert status == 200 and body["graph_stage_count"] > 0
    rows = {row["name"]: row for row in body["scopes"]}
    assert "express" in rows and 0 < rows["express"]["stages"] <= body["graph_stage_count"]
    assert rows["express"]["description"], "the scope's own frontmatter, read from the repository"
    assert list(rows) == sorted(rows)

def test_changes_needs_a_stage_and_says_when_it_listed_no_writes(api, repo):
    """The code a gate approves comes from the stage's source manifest; a stage without one names none."""
    assert refusal(api("GET", base(repo, "/changes"))) == (400, "bad_param")
    status, body = api("GET", base(repo, "/changes"), stage=STAGE)
    assert status == 200 and body["manifest"] is None and body["files"] == []
    assert (body["stage"], body["unit"]) == (STAGE, None)

def test_one_artifact_carries_its_content_review_and_table_of_contents(sv, api, repo):
    status, body = api("GET", base(repo, f"/artifacts/{artifact(sv, repo, 'requirements.md').artifact_id}"))
    assert status == 200 and set(body) == {"artifact", "content", "encoding", "truncated", "review", "toc", "prior"}
    assert body["encoding"] == "utf-8" and body["content"].startswith("# Requirements")
    assert body["review"]["verdict"] == "READY" and body["review"]["findings"][0]["level"] == "advisory"
    assert {"level": 1, "text": "Requirements", "anchor": "requirements"} in body["toc"]
    assert body["prior"]["source"] in (None, "git")  # Studio keeps no second copy: "before" can only come from git


def test_an_artifact_can_be_fetched_raw_with_its_digest_in_a_header(sv, routes, fake_host, repo):
    meta = artifact(sv, repo, "requirements.md")
    entry = next(e for e in routes if e.path.endswith("/artifacts/{artifact_id}"))
    request = CT.owner_request("GET", base(repo, f"/artifacts/{meta.artifact_id}"), host=fake_host, query={"raw": "1"},
                               match_info={"repo_id": repo.repo_id, "intent": INTENT, "artifact_id": meta.artifact_id})
    response = asyncio.run(entry.handler(request, sv.ctx))
    assert (response.status, response.content_type) == (200, "text/markdown")
    assert response.headers["X-Artifact-Sha256"] == meta.sha256
    assert response.body.decode("utf-8").startswith("# Requirements")


def test_an_unknown_artifact_is_a_404(api, repo):
    assert refusal(api("GET", base(repo, "/artifacts/" + "0" * 40))) == (404, "artifact_not_found")


def test_questions_use_supported_files_but_degrade_during_a_native_wait(api, fake_host, repo, slot):
    """A stable file supplies the form; a blocking host widget keeps its own answering endpoint."""
    bind(api, repo, slot)

    def questions() -> dict:
        status, body = api("GET", base(repo))
        assert status == 200, body
        return body["intent"]["questions"]

    view = questions()
    assert (view["mode"], view["host_card"]) == ("structured", None) and view["pending_count"] >= 1
    fake_host.get_slot(slot)._question_pending = {
        "ask-1": {"slot": slot, "ts": 1, "questions": [{"question": "Which store?", "options": []}]}}
    view = questions()
    assert (view["mode"], view["host_card"]["slot"]) == ("structured", slot)
    fake_host._pending_questions = {
        "blocking-ask": {"slot": slot, "ts": 2, "questions": [{"question": "Native?", "options": []}]}}
    view = questions()
    assert (view["mode"], view["host_card"]["ask_id"]) == ("degraded", "blocking-ask")


def test_question_context_reaches_the_api_detail_and_action_evidence(sv, api, repo, slot):
    context = ("The capabilities are (1) durable state, (2) session ownership, and (3) error reporting.\n\n"
               "**(Select all that should be recorded as in scope.)**")
    text = (f"## Q1. Which capabilities are required?\n\n{context}\n\n"
            "A. Capabilities 1 and 2\nB. Capability 3\nX. Other\n\n[Answer]:\n")
    question_path = Path(repo.canonical_path) / "aidlc/spaces/default/intents" / INTENT / QUESTIONS_REL
    question_path.write_text(text)
    bind(api, repo, slot)
    status, body = api("GET", base(repo))
    assert status == 200 and body["intent"]["questions"]["mode"] == "structured"
    question = body["intent"]["questions"]["questions"][0]
    assert question["context"] == context and question["multi_select"] is True
    assert [option["text"] for option in question["options"]] == ["Capabilities 1 and 2", "Capability 3", "Other"]
    evidence = sv.projection.evidence(sv.projection.snapshot(repo, "default", INTENT)).to_json()
    assert evidence["questions"]["questions"][0]["context"] == context
    assert question_path.read_text() == text


@pytest.mark.parametrize("checkpoint", [
    "",
    "## Consolidated Summary Confirmation\n- Looks correct\n- Request changes\n[Answer]:\n",
    "## Plan Approval\n- Approve Plan\n- Request Changes\n[Answer]:\n",
])
def test_unsupported_domain_questions_keep_waiting_and_reach_real_routes(sv, api, repo_builder, checkpoint):
    """Reproduce the native demo's shapes in a temp repo, never in the running demo."""
    text = (CT.FIXTURES / "question-fallback/domain-followups.md").read_text() + "\n" + checkpoint
    stage = "domain-design"
    question_rel = f"inception/{stage}/{stage}-questions.md"
    root = repo_builder.with_engine("devdelta").with_workspace().with_intent(
        INTENT, state=F.state_text(marks={stage: "-"}, fields={"Current Stage": stage, "Status": "Running",
                                                               "Lifecycle Phase": "INCEPTION"}),
        audit=F.audit_text(F.audit_block("STAGE_STARTED", "2026-09-04T09:00:00Z", Stage=stage)),
        questions={question_rel: text}).build()
    repo = sv.repos.add(str(root), "fallback")
    snap = sv.projection.snapshot(repo, "default", INTENT)
    assert sv.projection.operational_state(snap) == "WaitingForYou"
    cards = [c for c in sv.projection.derive_cards(snap, findings=(), binding=None, install=None, breakers=(),
                                                   live_actions=()) if c.type == "question"]
    assert len(cards) == 1 and cards[0].decisions == ()
    status, body = api("GET", base(repo))
    assert status == 200 and body["intent"]["operational_state"] == "WaitingForYou"
    view = body["intent"]["questions"]
    assert view == cards[0].evidence["questions"]
    assert (view["mode"], view["pending_count"], view["unsupported_pending_count"]) == ("degraded", 3, 3)
    assert all(q["answered"] for q in view["questions"]) and [q["index"] for q in view["questions"]] == [1, 2, 3, 4, 5]
    assert sv.storage.select("actions") == []  # the GET and projection never mint actions
    assert (root / "aidlc/spaces/default/intents" / INTENT / question_rel).read_text() == text


def test_review_reports_the_verdict_the_findings_and_the_audit_receipts(api, repo):
    status, body = api("GET", base(repo, "/review"))
    assert status == 200
    assert set(body) == {"stage", "verdict", "findings", "receipts", "review_class", "reviewer", "revisions"}
    assert (body["stage"], body["verdict"], body["reviewer"]) == (STAGE, "READY", "reviewer-agent")
    assert isinstance(body["receipts"], list)


# --- plan and composition ---
def test_plan_preview_returns_the_effective_plan(api, repo):
    """``plan`` is a valid ``intent_dir`` spelling: this answering at all shows it is registered before the intent."""
    status, body = api("POST", f"/repos/{repo.repo_id}/intents/plan/preview", {"scope": "poc", "depth": "Standard"})
    assert status == 200
    plan = body["plan"]
    assert {"request", "stages", "exact", "estimate", "issues", "valid"} <= set(plan)
    assert (plan["request"]["scope"], plan["request"]["depth"]) == ("poc", "Standard")
    assert any(stage["slug"] == STAGE for stage in plan["stages"])


def arm_advisor(sv: Any, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the real broker the app's own agent (host discovery is absent here); ``fake_ctx.spawn`` only records."""
    agent = SimpleNamespace(name=CT.studio_module("advisor").ADVISOR_AGENT_NAME, filename=f"{CT.APP_NAME}--advisor.json")
    monkeypatch.setattr(sv.advisor, "_agent_lister", lambda: [agent])
    fake_ctx.spawn.spawned.clear()


def intents_on_disk(demo: Path) -> tuple[list[str], str]:
    intents = demo / "aidlc" / "spaces" / "default" / "intents"
    return sorted(p.name for p in intents.iterdir()), (intents / "intents.json").read_text("utf-8")


def activity_kinds(sv: Any, studio: Any) -> list[str]:
    rows, _ = asyncio.run(sv.activity.query(studio.storage.ActivityQuery(limit=100)))
    return [row.kind for row in rows]


def test_plan_advise_needs_an_objective(sv, api, repo):
    """The objective is what the Advisor reads; a draft without one would propose from nothing (§1.6)."""
    for body in ({}, {"objective": ""}, {"objective": "   \n\t "}):
        status, answer = api("POST", f"/repos/{repo.repo_id}/intents/plan/advise", body)
        assert (status, answer["code"], answer["details"]["missing"]) == (400, "bad_body", ["objective"]), body
    assert sv.storage.select("advisor_drafts") == []


def test_plan_advise_answers_202_with_a_plan_draft_and_creates_nothing(sv, api, repo, demo, fake_ctx, denied_log,
                                                                      monkeypatch):
    """FR-NEW-006: a read-only draft under the repo's sentinel (FR-NEW-004, FR-ADV-003) whose picks reach the agent."""
    arm_advisor(sv, fake_ctx, monkeypatch)
    before = intents_on_disk(demo)
    body = {"space": "default", "objective": "Export the ledger to CSV", "context": "Finance asked for a monthly file.",
            "project_type": "Brownfield", "locale": "en-US",
            "current": {"scope": "poc", "depth": "Standard", "test_strategy": None, "review_cap": None,
                        "overrides": {}}}
    status, answer = api("POST", f"/repos/{repo.repo_id}/intents/plan/advise", body)
    assert status == 202, answer
    assert answer["ok"] is True
    draft = answer["draft"]
    assert (draft["kind"], draft["action_id"]) == ("plan_draft", f"plan:{repo.repo_id}")
    assert draft["status"] in ("queued", "running") and draft["neutrality"] is None
    assert "plan_proposal" in draft and draft["plan_proposal"] is None and draft["request"]["auto"] is False
    assert len(fake_ctx.spawn.spawned) == 1
    rows = sv.storage.select("advisor_drafts")
    assert [row["action_id"] for row in rows] == [f"plan:{repo.repo_id}"]
    stored = rows[0]["request_json"]["plan_current"]
    assert stored["scope"] == "poc"
    assert set(stored) == {"space", "scope", "depth", "test_strategy", "review_cap", "overrides"}
    task = fake_ctx.spawn.spawned[0]["task"]
    package = json.loads(task.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert package["kind"] == "plan_draft" and package["plan"]["current"]["scope"] == "poc"
    assert {key: package["plan"][key] for key in ("context", "project_type", "objective")} == {
        "context": body["context"], "project_type": "Brownfield", "objective": body["objective"]}
    assert intents_on_disk(demo) == before
    assert not denied_log.exists(), denied_log.read_text()


def test_plan_advise_never_records_an_automatic_request(sv, api, repo, fake_ctx, studio, monkeypatch):
    """A client cannot claim its click was Studio's own pre-draft (FR-ADV-010, §2.7): a body ``auto`` is dropped."""
    arm_advisor(sv, fake_ctx, monkeypatch)
    status, answer = api("POST", f"/repos/{repo.repo_id}/intents/plan/advise",
                         {"objective": "Export the ledger to CSV", "locale": "en-US", "auto": True})
    assert status == 202, answer
    assert answer["draft"]["request"]["auto"] is False
    assert sv.storage.select("advisor_drafts")[0]["request_json"]["auto"] is False
    kinds = activity_kinds(sv, studio)
    assert "advisor.requested" in kinds and "advisor.auto_requested" not in kinds


def test_creating_an_intent_is_refused_while_the_repository_contradicts_itself(sv, api, repo, monkeypatch):
    blocking = CT.studio_module("consistency").Finding(code="install_recovery_required", severity="blocking",
                                                       message_key="finding.install_recovery_required", params={},
                                                       evidence=())
    monkeypatch.setattr(sv.consistency, "evaluate_repo", lambda facts: [blocking])
    status, body = api("POST", f"/repos/{repo.repo_id}/intents",
                       {"scope": "poc", "depth": "Standard", "label": "new thing"})
    assert (status, body["code"]) == (409, "state_inconsistent")
    assert body["details"]["findings"][0]["code"] == "install_recovery_required"


def test_creating_an_intent_refuses_a_plan_it_cannot_compose(api, repo):
    answer = api("POST", f"/repos/{repo.repo_id}/intents", {"scope": "no-such-scope", "depth": "Standard"})
    assert refusal(answer) == (409, "plan_invalid")


def test_recompose_preview_returns_a_proposal_and_its_digest(api, repo):
    status, body = api("POST", base(repo, "/recompose/preview"), {"skip": ["user-stories"], "add": []})
    assert status == 200 and body["proposal_digest"]
    assert {"skip", "add", "allowed", "refusals"} <= set(body["proposal"])


def test_recompose_refuses_a_slug_that_is_not_a_stage(api, repo):
    assert refusal(api("POST", base(repo, "/recompose/preview"), {"skip": ["Not A Stage"]})) == (400, "bad_body")


def test_recompose_refuses_a_stale_proposal_digest(api, repo):
    status, body = api("POST", base(repo, "/recompose"),
                       {"skip": ["user-stories"], "add": [], "proposal_digest": "0" * 64})
    assert status == 409 and body["code"] in ("plan_invalid", "recompose_not_allowed")


# --- dispatch commands ---
@pytest.fixture
def runnable_demo(demo):
    record = demo / "aidlc" / "spaces" / "default" / "intents" / INTENT
    (record / "aidlc-state.md").write_text(_state({STAGE: "-"}))
    (record / QUESTIONS_REL).unlink()
    for shard in (record / "audit").glob("*.md"):
        shard.write_text(F.audit_text(F.audit_block("HUMAN_TURN", "2026-09-04T09:00:00Z"),
                                      F.audit_block("STAGE_STARTED", "2026-09-04T09:00:01Z", Stage=STAGE)))


def test_run_creates_a_queued_card_and_sends_nothing(api, fake_host, repo, slot, runnable_demo):
    status, body = api("POST", base(repo, "/run"), {})
    assert status == 201 and body["ok"] is True and body["status"] == "Queued"
    action = body["action"]
    assert (action["type"], action["action_id"], action["risk_class"]) == ("run", body["action_id"], "human_lane")
    # nothing reached the host: the browser does the sending, in a second call
    assert fake_host.get_slot(slot).messages == [] and action["delivery"]["delivery_id"] is None


def test_run_twice_returns_the_same_card_rather_than_queueing_a_second_turn(sv, api, repo, slot, runnable_demo):
    first, second = (api("POST", base(repo, "/run"), {})[1] for _ in range(2))
    assert first["action_id"] == second["action_id"] and len(sv.storage.select("actions")) == 1


@pytest.mark.parametrize("setter, code", [("set_paused", "intent_paused"), ("set_archived", "intent_archived")])
def test_run_is_refused_while_the_intent_is_paused_or_archived(sv, api, repo, slot, setter, code):
    asyncio.run(getattr(sv.sessions, setter)(repo.repo_id, "default", INTENT, True))
    assert refusal(api("POST", base(repo, "/run"), {})) == (409, code)


def test_resume_needs_a_parked_intent(api, repo, slot):
    assert refusal(api("POST", base(repo, "/run"), {"type": "resume"})) == (400, "invalid_decision")


def test_resume_creates_its_own_card_type_for_a_parked_intent(api, repo, slot, demo, runnable_demo):
    state = demo / "aidlc" / "spaces" / "default" / "intents" / INTENT / "aidlc-state.md"
    state.write_text(F.insert_runtime_field(_state({STAGE: "-"}), "Parked", "2026-09-04T09:30:00Z"))
    status, body = api("POST", base(repo, "/run"), {"type": "resume"})
    assert status == 201, body
    assert body["action"]["type"] == "resume"


def test_run_takes_only_the_two_commands_a_human_starts(api, repo, slot):
    status, body = api("POST", base(repo, "/run"), {"type": "prepare_commit"})
    assert (status, body["code"]) == (400, "bad_body")
    assert body["details"] == {"key": "type", "choices": ["run", "resume"]}


def test_pause_reports_the_cards_it_just_blocked(api, repo, slot, runnable_demo):
    created = api("POST", base(repo, "/run"), {})[1]
    status, body = api("POST", base(repo, "/pause"), {"paused": True})
    assert status == 200 and body["binding"]["paused"] is True
    assert body["blocked_actions"] == [created["action_id"]]
    body = api("POST", base(repo, "/pause"), {"paused": False})[1]
    assert body["binding"]["paused"] is False and body["blocked_actions"] == []


@pytest.mark.parametrize("suffix,flag", [("/pause", "paused"), ("/archive", "archived")])
def test_pause_and_archive_need_a_boolean(api, repo, suffix, flag):
    for body in ({flag: "yes"}, {}):
        status, answer = api("POST", base(repo, suffix), body)
        assert (status, answer["code"], answer["details"]) == (400, "bad_body", {"missing": [flag]}), body


def test_force_stop_needs_a_confirmation_and_a_bound_session(api, repo):
    assert refusal(api("POST", base(repo, "/force-stop"), {})) == (400, "invalid_decision")
    assert refusal(api("POST", base(repo, "/force-stop"), {"confirm": True})) == (409, "session_unbound")


def test_force_stop_commits_a_delivering_record_and_hands_over_the_host_call(sv, api, repo, slot):
    bind(api, repo, slot)
    status, body = api("POST", base(repo, "/force-stop"), {"confirm": True})
    assert (status, body["lane"], body["status"]) == (201, "host_control", "Delivering")
    assert body["wire_text"] is None  # nothing reaches the model
    assert body["lease_generation"] is None  # and no execution lease is taken
    assert body["host"] == {"method": "POST", "path": f"/api/chat/slots/{slot}/stop", "body": {}}
    assert body["delivery_id"].startswith("dl_")
    row = sv.storage.get("actions", body["action_id"])  # durable before the browser can act on it
    assert row["status"] == "Delivering" and row["boot_id"]


# --- session binding ---
@pytest.fixture
def hand_opened(sv, api, fake_host, repo, slot) -> str:
    """The intent bound to its slot, and a second slot opened by hand on the same project."""
    bind(api, repo, slot)
    fake_host.add_slot("hand-opened", project=str(repo.canonical_path), agent="aidlc", app="")
    sv.host.attach(fake_host)
    return "hand-opened"


def test_binding_records_the_slot_and_returns_it(api, repo, slot):
    status, body = bind(api, repo, slot)
    assert status == 200 and body["ok"] is True
    assert (body["binding"]["slot_key"], body["slot"]["key"], body["slot"]["agent"]) == (slot, slot, "aidlc")


def test_a_linked_session_key_wins_when_the_hosts_own_helper_is_not_loaded(api, fake_host, repo, slot, monkeypatch):
    """Without ``chat_utils`` in ``sys.modules`` Studio applies the host's rule itself: a linked channel key wins."""
    monkeypatch.delitem(sys.modules, "kiro_crew.dashboard.chat_utils", raising=False)
    fake_host.get_slot(slot).linked_session_key = "slack:1725444000.1234"
    status, body = bind(api, repo, slot)
    assert status == 200 and body["slot"]["session_key"] == "slack:1725444000.1234"

def test_binding_refuses_a_slot_pointed_at_another_project(sv, api, fake_host, repo, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    fake_host.add_slot("stranger", project=str(tmp_path / "elsewhere"), agent="aidlc", app="")
    sv.host.attach(fake_host)
    assert refusal(bind(api, repo, "stranger")) == (409, "slot_mismatch")


def test_binding_needs_a_slot_key(api, repo):
    status, body = api("POST", base(repo, "/session"), {})
    assert (status, body["code"], body["details"]["missing"]) == (400, "bad_body", ["slot_key"])


def test_unbinding_forgets_the_slot(sv, api, repo, slot, studio):
    bind(api, repo, slot)
    status, body = bind(api, repo, None)
    assert status == 200 and body["binding"]["slot_key"] is None and body["slot"] is None
    assert "session.unbound" in activity_kinds(sv, studio)


def test_takeover_preview_lists_the_other_slots_for_this_repository(api, repo, slot, hand_opened):
    status, body = api("POST", base(repo, "/session/takeover/preview"), {})
    assert status == 200 and [candidate["slot"]["key"] for candidate in body["candidates"]] == [hand_opened]
    assert body["candidates"][0]["reason"] == "project_match" and body["current"]["slot_key"] == slot


def test_pointing_a_bound_intent_at_another_slot_is_a_takeover(sv, api, repo, studio, hand_opened):
    status, body = bind(api, repo, hand_opened)
    assert status == 200 and body["binding"]["slot_key"] == body["slot"]["key"] == hand_opened
    assert "session.takeover" in activity_kinds(sv, studio)


def test_a_takeover_waits_for_a_decision_that_may_be_on_the_wire(api, repo, hand_opened):
    assert api("POST", base(repo, "/force-stop"), {"confirm": True})[0] == 201
    for target in (hand_opened, None):
        status, body = bind(api, repo, target)
        assert (status, body["code"]) == (409, "action_not_submittable"), target
        assert body["details"]["operation"] == ("takeover" if target else "unbind")


def test_archiving_and_restoring_an_intent_touches_no_aidlc_file(api, repo, demo):
    state = demo / "aidlc" / "spaces" / "default" / "intents" / INTENT / "aidlc-state.md"
    before = state.read_bytes()
    status, body = api("POST", base(repo, "/archive"), {"archived": True})
    assert status == 200 and body["binding"]["archive_state"] == "archived"
    assert api("POST", base(repo, "/archive"), {"archived": False})[1]["binding"]["archive_state"] == "active"
    assert state.read_bytes() == before


def test_archiving_is_refused_while_a_card_is_live(api, repo, slot, runnable_demo):
    api("POST", base(repo, "/run"), {})
    assert refusal(api("POST", base(repo, "/archive"), {"archived": True})) == (409, "action_not_submittable")


def test_a_paused_intent_archives_and_its_cards_leave_the_queue_until_restored(api, repo, slot, runnable_demo):
    status, body = api("POST", base(repo, "/run"), {})
    assert status == 201
    queued = body["action_id"]

    def queue(**query: str) -> list[str]:
        status, listed = api("GET", "/actions", **query)
        assert status == 200, listed
        return [card["action_id"] for card in listed["actions"]]

    status, body = api("POST", base(repo, "/archive"), {"archived": True})
    assert status == 409 and body["details"]["reason"] == "live_not_paused"
    api("POST", base(repo, "/pause"), {"paused": True})
    assert api("POST", base(repo, "/archive"), {"archived": True})[0] == 200
    assert queued not in queue()
    assert queued in queue(intent=INTENT)  # the intent's own page still names it
    api("POST", base(repo, "/archive"), {"archived": False})
    assert queued in queue()
