"""Intent routes (§2.3): the read model, the plan, the session binding, the dispatch commands.

The read routes are checked for shape, but the four properties below are what this file is really for,
because each of them is a way a click could reach AI-DLC when it must not:

* **Run creates a card; it does not send.** ``POST …/run`` answers ``201`` with a ``Queued`` action, and
  the wire text only leaves the building through ``POST /actions/{id}/submit`` from the browser.
* **Pause and archive block every human-lane dispatch**, not just Run (C29), and ``pause`` says which
  cards it just blocked so the UI can grey them out instead of letting a user earn a 409.
* **Force stop is the host-control lane**: a confirmed, durable record with the host's own stop call and
  no wire text (C28).
* **``keep-moving`` always refuses**, on the record. There is no machine lane in v1 (§3.2, S12).

A read route must also never mint a card: the queue is the reconciler's, and a GET that derived actions
would make the queue depend on who was looking at it.
"""

from __future__ import annotations

import asyncio
import json
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


def _state(marks: dict[str, str] | None = None, **fields: str) -> str:
    """A state file whose phase and stage agree, so no blocking finding hides the route under test."""
    values = {
        "Current Stage": STAGE,
        "Status": "Running",
        "Next Stage": "user-stories",
        "Lifecycle Phase": "INCEPTION",
        "Completed": "7",
    }
    values.update(fields)
    return F.state_text(marks=marks or {STAGE: "?"}, fields=values)


@pytest.fixture
def demo(repo_builder) -> Path:
    """One repository, one intent waiting at an open gate, nothing contradicting itself."""
    return (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(
            INTENT,
            state=_state(),
            audit=F.gate_open_audit(STAGE),
            directive={"version": 1, "stage": STAGE, "state_sha256": "AUTO"},
            questions={QUESTIONS_REL: F.blank_answers(F.real_questions())},
            artifacts={
                f"inception/{STAGE}/requirements.md": (
                    "# Requirements\n\nOne requirement.\n\n"
                    "## Review\n\n**Reviewer**: reviewer-agent\n**Iteration**: 1\n"
                    "**Verdict**: READY\n\n### Advisory\n\n- Consider naming the store\n"
                ),
            },
        )
        .with_git()
        .build()
    )


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


def base(repo: Any, suffix: str = "") -> str:
    return f"/repos/{repo.repo_id}/intents/{INTENT}{suffix}"


# --------------------------------------------------------------------------- #
# the read model
# --------------------------------------------------------------------------- #

_SUMMARY_KEYS = {
    "repo_id", "repo_label", "space", "intent_dir", "intent_key", "uuid", "slug", "scope",
    "registry_status", "title", "operational_state", "disk", "counts", "open_actions",
    "blocking_findings", "warn_findings", "session", "archived", "paused", "keep_moving",
    "interrupted", "last_activity_at", "stable_boundary", "unstable", "is_active_cursor",
}


def test_listing_intents_reports_the_summary_the_spaces_and_the_cursor(sv, routes, fake_host, repo):
    path = f"/repos/{repo.repo_id}/intents"
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 200
    assert set(body) == {"intents", "spaces", "active_space"}
    assert body["spaces"] == ["default"]
    assert body["active_space"] == "default"
    summary = body["intents"][0]
    assert set(summary) == _SUMMARY_KEYS
    assert summary["intent_key"] == INTENT
    assert summary["keep_moving"] is False           # there is no machine lane to turn on
    assert summary["is_active_cursor"] is True
    assert set(summary["disk"]) == {
        "status", "lifecycle_phase", "current_stage", "next_stage", "state_version", "total_stages",
        "completed", "revision_count", "parked_at", "last_updated",
    }


def test_listing_intents_does_not_mint_any_action_card(sv, routes, fake_host, repo):
    """A GET must not change the queue: deriving cards is the reconciler's job, on its own schedule."""
    path = f"/repos/{repo.repo_id}/intents"
    call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    call(sv, routes, "GET", base(repo), CT.owner_request("GET", base(repo), host=fake_host))
    assert sv.storage.select("actions") == []


def test_listing_intents_filters_by_state_space_and_free_text(sv, routes, fake_host, repo):
    path = f"/repos/{repo.repo_id}/intents"
    request = CT.owner_request("GET", path, host=fake_host, query={"q": "gate-demo"})
    _status, body = call(sv, routes, "GET", path, request)
    assert len(body["intents"]) == 1

    request = CT.owner_request("GET", path, host=fake_host, query={"q": "nothing-like-this"})
    _status, body = call(sv, routes, "GET", path, request)
    assert body["intents"] == []

    request = CT.owner_request("GET", path, host=fake_host, query={"state": "Completed"})
    _status, body = call(sv, routes, "GET", path, request)
    assert body["intents"] == []

    request = CT.owner_request("GET", path, host=fake_host, query={"state": "not-a-state"})
    status, body = call(sv, routes, "GET", path, request)
    assert status == 400 and body["code"] == "bad_param"


def test_listing_intents_refuses_an_unreadable_repository(sv, routes, fake_host, repo):
    sv.storage.update("repos", repo.repo_id, {"availability": "moved"})
    path = f"/repos/{repo.repo_id}/intents"
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 409 and body["code"] == "repo_unavailable"


def test_the_detail_is_a_superset_of_the_summary(sv, routes, fake_host, repo):
    status, body = call(sv, routes, "GET", base(repo),
                        CT.owner_request("GET", base(repo), host=fake_host))
    assert status == 200
    detail = body["intent"]
    assert _SUMMARY_KEYS <= set(detail)
    assert {"state_sections", "phases", "stages", "findings", "actions", "directive", "markers",
            "audit_tail", "questions", "artifacts_count", "artifacts_truncated", "git", "binding",
            "engine", "active_directive_stage"} <= set(detail)
    assert detail["active_directive_stage"] == STAGE
    assert detail["artifacts_count"] >= 1


def test_an_unknown_intent_is_a_404_and_a_malformed_key_is_a_400(sv, routes, fake_host, repo):
    path = f"/repos/{repo.repo_id}/intents/260904-nope"
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 404 and body["code"] == "intent_not_found"

    path = f"/repos/{repo.repo_id}/intents/not a key"
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 400 and body["code"] == "bad_param"


def test_the_map_joins_the_graph_with_the_state_file(sv, routes, fake_host, repo):
    path = base(repo, "/map")
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 200
    model = body["map"]
    assert set(model) == {"intent_key", "phases", "counts", "units", "graph_version", "stage_count"}
    assert model["intent_key"] == INTENT
    stages = [stage for phase in model["phases"] for stage in phase["stages"]]
    assert any(stage["slug"] == STAGE for stage in stages)


def test_the_map_refuses_an_unknown_density(sv, routes, fake_host, repo):
    path = base(repo, "/map")
    request = CT.owner_request("GET", path, host=fake_host, query={"density": "everything"})
    status, body = call(sv, routes, "GET", path, request)
    assert status == 400 and body["code"] == "bad_param"


def test_artifacts_lists_metadata_and_filters_by_stage_and_kind(sv, routes, fake_host, repo):
    path = base(repo, "/artifacts")
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 200
    assert body["count"] == len(body["artifacts"])
    assert body["truncated"] is False
    assert set(body["artifacts"][0]) == {
        "artifact_id", "relpath", "name", "stage", "phase", "unit", "size", "mtime", "sha256",
        "kind", "renderable",
    }

    request = CT.owner_request("GET", path, host=fake_host, query={"kind": "questions"})
    _status, body = call(sv, routes, "GET", path, request)
    assert [a["kind"] for a in body["artifacts"]] == ["questions"]

    request = CT.owner_request("GET", path, host=fake_host, query={"stage": STAGE})
    _status, body = call(sv, routes, "GET", path, request)
    assert body["count"] >= 1 and all(a["stage"] == STAGE for a in body["artifacts"])


def test_one_artifact_carries_its_content_review_and_table_of_contents(sv, routes, fake_host, repo):
    artifact = _artifact(sv, repo, "requirements.md")
    path = base(repo, f"/artifacts/{artifact.artifact_id}")
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 200
    assert set(body) == {"artifact", "content", "encoding", "truncated", "review", "toc", "prior"}
    assert body["encoding"] == "utf-8"
    assert body["content"].startswith("# Requirements")
    assert body["review"]["verdict"] == "READY"
    assert body["review"]["findings"][0]["level"] == "advisory"
    assert {"level": 1, "text": "Requirements", "anchor": "requirements"} in body["toc"]
    # Studio keeps no second copy of an AI-DLC artifact, so "before" can only come from git.
    assert body["prior"]["source"] in (None, "git")


def test_an_artifact_can_be_fetched_raw_with_its_digest_in_a_header(sv, routes, fake_host, repo):
    artifact = _artifact(sv, repo, "requirements.md")
    path = base(repo, f"/artifacts/{artifact.artifact_id}")
    entry = next(e for e in routes if e.path.endswith("/artifacts/{artifact_id}"))
    request = CT.owner_request("GET", path, host=fake_host, query={"raw": "1"},
                              match_info={"repo_id": repo.repo_id, "intent": INTENT,
                                          "artifact_id": artifact.artifact_id})
    response = asyncio.run(entry.handler(request, sv.ctx))
    assert response.status == 200
    assert response.content_type == "text/markdown"
    assert response.headers["X-Artifact-Sha256"] == artifact.sha256
    assert response.body.decode("utf-8").startswith("# Requirements")


def test_an_unknown_artifact_is_a_404(sv, routes, fake_host, repo):
    path = base(repo, "/artifacts/" + "0" * 40)
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 404 and body["code"] == "artifact_not_found"


def test_questions_use_supported_files_but_degrade_during_a_native_wait(sv, routes, fake_host, repo,
                                                                     slot):
    """A stable file supplies the form; a blocking host widget keeps its own answering endpoint."""
    _bind(sv, routes, fake_host, repo, slot)
    path = base(repo, "/questions")
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 200
    assert set(body) == {"questions", "mode", "host_cards", "slot_key"}
    assert body["slot_key"] == slot
    assert body["mode"] == "structured"
    assert body["host_cards"] == []
    assert body["questions"]["pending_count"] >= 1

    fake_host.get_slot(slot)._question_pending = {
        "ask-1": {"slot": slot, "ts": 1, "questions": [{"question": "Which store?", "options": []}]}
    }
    _status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert body["mode"] == "structured"
    assert body["host_cards"] and body["host_cards"][0]["slot"] == slot

    fake_host._pending_questions = {
        "blocking-ask": {"slot": slot, "ts": 2, "questions": [{"question": "Native?", "options": []}]}
    }
    _status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert body["mode"] == "degraded"
    assert body["host_cards"][0]["ask_id"] == "blocking-ask"


def test_question_context_reaches_the_api_detail_and_action_evidence(sv, routes, fake_host, repo, slot):
    context = (
        "The capabilities are (1) durable state, (2) session ownership, and (3) error reporting.\n\n"
        "**(Select all that should be recorded as in scope.)**"
    )
    text = (
        f"## Q1. Which capabilities are required?\n\n{context}\n\n"
        "A. Capabilities 1 and 2\nB. Capability 3\nX. Other\n\n[Answer]:\n"
    )
    question_path = Path(repo.canonical_path) / "aidlc/spaces/default/intents" / INTENT / QUESTIONS_REL
    question_path.write_text(text)
    _bind(sv, routes, fake_host, repo, slot)
    path = base(repo, "/questions")
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 200 and body["mode"] == "structured"
    question = body["questions"]["questions"][0]
    assert question["context"] == context
    assert question["multi_select"] is True
    assert [option["text"] for option in question["options"]] == [
        "Capabilities 1 and 2", "Capability 3", "Other",
    ]

    status, body = call(sv, routes, "GET", base(repo),
                        CT.owner_request("GET", base(repo), host=fake_host))
    assert status == 200
    assert body["intent"]["questions"]["questions"][0]["context"] == context
    snap = sv.projection.snapshot(repo, "default", INTENT)
    evidence = sv.projection.evidence(snap).to_json()
    assert evidence["questions"]["questions"][0]["context"] == context
    assert question_path.read_text() == text


@pytest.mark.parametrize("checkpoint", [
    "",
    "## Consolidated Summary Confirmation\n- Looks correct\n- Request changes\n[Answer]:\n",
    "## Plan Approval\n- Approve Plan\n- Request Changes\n[Answer]:\n",
])
def test_unsupported_domain_questions_keep_waiting_and_reach_real_routes(
    sv, routes, fake_host, repo_builder, checkpoint,
):
    """Reproduce the native demo's shapes in a temp repo, never in the running demo."""
    text = (CT.FIXTURES / "question-fallback/domain-followups.md").read_text() + "\n" + checkpoint
    stage = "domain-design"
    question_rel = f"inception/{stage}/{stage}-questions.md"
    root = (
        repo_builder.with_engine("devdelta").with_workspace().with_intent(
            INTENT,
            state=F.state_text(marks={stage: "-"}, fields={
                "Current Stage": stage, "Status": "Running", "Lifecycle Phase": "INCEPTION",
            }),
            audit=F.audit_text(F.audit_block("STAGE_STARTED", "2026-09-04T09:00:00Z", Stage=stage)),
            questions={question_rel: text},
        ).build()
    )
    repo = sv.repos.add(str(root), "fallback")
    snap = sv.projection.snapshot(repo, "default", INTENT)
    assert sv.projection.operational_state(snap) == "WaitingForYou"
    cards = [c for c in sv.projection.derive_cards(
        snap, findings=(), binding=None, install=None, breakers=(), live_actions=(),
    ) if c.type == "question"]
    assert len(cards) == 1
    assert cards[0].decisions == ()

    for suffix in ("/questions", ""):
        path = base(repo, suffix)
        status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
        assert status == 200
        if not suffix:
            assert body["intent"]["operational_state"] == "WaitingForYou"
        view = body["questions"] if suffix else body["intent"]["questions"]
        assert view == cards[0].evidence["questions"]
        assert view["mode"] == "degraded"
        assert view["pending_count"] == 3
        assert view["unsupported_pending_count"] == 3
        assert all(q["answered"] for q in view["questions"])
        assert [q["index"] for q in view["questions"]] == [1, 2, 3, 4, 5]
    assert sv.storage.select("actions") == []  # the GETs and projection never mint actions
    assert (root / "aidlc/spaces/default/intents" / INTENT / question_rel).read_text() == text


def test_review_reports_the_verdict_the_findings_and_the_audit_receipts(sv, routes, fake_host, repo):
    path = base(repo, "/review")
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 200
    assert set(body) == {"stage", "verdict", "findings", "receipts", "review_class", "reviewer",
                         "revisions"}
    assert body["stage"] == STAGE
    assert body["verdict"] == "READY"
    assert body["reviewer"] == "reviewer-agent"
    assert isinstance(body["receipts"], list)


def test_intent_git_reports_commits_and_changed_artifacts(sv, routes, fake_host, repo):
    path = base(repo, "/git")
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 200
    assert set(body) == {"git", "commits", "changed_artifacts"}
    assert isinstance(body["commits"], list)
    assert isinstance(body["changed_artifacts"], list)


def test_intent_activity_is_the_merged_timeline(sv, routes, fake_host, repo):
    path = base(repo, "/activity")
    status, body = call(sv, routes, "GET", path, CT.owner_request("GET", path, host=fake_host))
    assert status == 200
    assert body["next_cursor"] is None
    assert {item["source"] for item in body["items"]} == {"aidlc"}
    assert all(item["message_key"].startswith("audit.") for item in body["items"])


# --------------------------------------------------------------------------- #
# plan and composition
# --------------------------------------------------------------------------- #


def test_plan_preview_returns_the_effective_plan(sv, routes, fake_host, repo):
    path = f"/repos/{repo.repo_id}/intents/plan/preview"
    request = CT.owner_request("POST", path, host=fake_host,
                              body={"scope": "poc", "depth": "Standard"})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 200
    plan = body["plan"]
    assert {"request", "stages", "exact", "estimate", "issues", "valid"} <= set(plan)
    assert plan["request"]["scope"] == "poc"
    assert plan["request"]["depth"] == "Standard"
    assert any(stage["slug"] == STAGE for stage in plan["stages"])


def test_plan_preview_is_registered_before_the_intent_route_that_would_shadow_it(sv, routes,
                                                                                fake_host, repo):
    """``plan`` is a valid ``intent_dir`` spelling, so registration order is the only thing that helps."""
    path = f"/repos/{repo.repo_id}/intents/plan/preview"
    request = CT.owner_request("POST", path, host=fake_host, body={"scope": "poc", "depth": "Standard"})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 200 and "plan" in body


def _arm_advisor(sv: Any, fake_ctx: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the real broker the two seams a spawn needs: the app's own agent and a fake ``ctx.spawn``.

    ``Services.build`` wires ``default_agent_lister``, which walks the host's agent discovery — absent in
    the test process, so an unarmed broker refuses every draft with ``advisor_unavailable``. The
    spawn itself is ``fake_ctx.spawn`` already (``conftest``): recorded, never run.
    """
    from _aidlc_studio_backend.studio import advisor as A  # noqa: PLC0415 - one call site

    agent = SimpleNamespace(name=A.ADVISOR_AGENT_NAME, filename="aidlc-studio--advisor.json")
    monkeypatch.setattr(sv.advisor, "_agent_lister", lambda: [agent])
    fake_ctx.spawn.spawned.clear()


def _intents_on_disk(demo: Path) -> tuple[list[str], str]:
    intents = demo / "aidlc" / "spaces" / "default" / "intents"
    return sorted(p.name for p in intents.iterdir()), (intents / "intents.json").read_text("utf-8")


def _activity_kinds(sv: Any, studio: Any) -> list[str]:
    rows, _ = asyncio.run(sv.activity.query(studio.storage.ActivityQuery(limit=100)))
    return [row.kind for row in rows]


def test_plan_advise_needs_an_objective(sv, routes, fake_host, repo):
    """The objective is what the Advisor reads; a draft without one would propose from nothing (§1.6)."""
    path = f"/repos/{repo.repo_id}/intents/plan/advise"
    for body in ({}, {"objective": ""}, {"objective": "   \n\t "}):
        request = CT.owner_request("POST", path, host=fake_host, body=body)
        status, answer = call(sv, routes, "POST", path, request)
        assert status == 400 and answer["code"] == "bad_body", body
        assert answer["details"]["missing"] == ["objective"], body
    assert sv.storage.select("advisor_drafts") == []


def test_plan_advise_answers_202_with_a_plan_draft_and_creates_nothing(sv, routes, fake_host, repo,
                                                                     demo, fake_ctx, denied_log,
                                                                     monkeypatch):
    """FR-NEW-006: the answer is a draft under the repository's sentinel, and the repository is untouched.

    No intent directory appears, the registry is byte-identical, and the fakes logged no refused engine
    or git invocation — a plan draft is read-only by construction (FR-NEW-004, FR-ADV-003). The body's
    picks, context and project type all reach the row and the package the agent was actually handed:
    a route that dropped ``current`` on the floor would still answer 202 with a plausible draft.
    """
    _arm_advisor(sv, fake_ctx, monkeypatch)
    before = _intents_on_disk(demo)
    path = f"/repos/{repo.repo_id}/intents/plan/advise"
    body = {
        "space": "default",
        "objective": "Export the ledger to CSV",
        "context": "Finance asked for a monthly file.",
        "project_type": "Brownfield",
        "locale": "en-US",
        "current": {"scope": "poc", "depth": "Standard", "test_strategy": None, "review_cap": None,
                    "overrides": {}},
    }
    request = CT.owner_request("POST", path, host=fake_host, body=body)
    status, answer = call(sv, routes, "POST", path, request)
    assert status == 202, answer
    assert answer["ok"] is True
    draft = answer["draft"]
    assert draft["kind"] == "plan_draft"
    assert draft["action_id"] == f"plan:{repo.repo_id}"
    assert draft["status"] in ("queued", "running")
    assert draft["neutrality"] is None
    assert "plan_proposal" in draft and draft["plan_proposal"] is None
    assert draft["request"]["auto"] is False
    assert len(fake_ctx.spawn.spawned) == 1

    rows = sv.storage.select("advisor_drafts")
    assert [row["action_id"] for row in rows] == [f"plan:{repo.repo_id}"]
    stored = rows[0]["request_json"]["plan_current"]
    assert stored["scope"] == "poc"
    assert set(stored) == {"space", "scope", "depth", "test_strategy", "review_cap", "overrides"}

    task = fake_ctx.spawn.spawned[0]["task"]
    package = json.loads(task.split("```json\n", 1)[1].split("\n```", 1)[0])
    assert package["kind"] == "plan_draft"
    assert package["plan"]["current"]["scope"] == "poc"
    assert package["plan"]["context"] == body["context"]
    assert package["plan"]["project_type"] == "Brownfield"
    assert package["plan"]["objective"] == body["objective"]

    assert _intents_on_disk(demo) == before
    assert not denied_log.exists(), denied_log.read_text()


def test_plan_advise_never_records_an_automatic_request(sv, routes, fake_host, repo, fake_ctx, studio,
                                                        monkeypatch):
    """A client cannot claim its click was Studio's own pre-draft (FR-ADV-010, §2.7): the body's
    ``auto`` is not part of ``PlanDraftRequest`` and the broker records a request, never an
    ``auto_requested`` row."""
    _arm_advisor(sv, fake_ctx, monkeypatch)
    path = f"/repos/{repo.repo_id}/intents/plan/advise"
    request = CT.owner_request("POST", path, host=fake_host,
                              body={"objective": "Export the ledger to CSV", "locale": "en-US",
                                    "auto": True})
    status, answer = call(sv, routes, "POST", path, request)
    assert status == 202, answer
    assert answer["draft"]["request"]["auto"] is False
    assert sv.storage.select("advisor_drafts")[0]["request_json"]["auto"] is False
    kinds = _activity_kinds(sv, studio)
    assert "advisor.requested" in kinds
    assert "advisor.auto_requested" not in kinds


def test_creating_an_intent_is_refused_while_the_repository_contradicts_itself(sv, routes, fake_host,
                                                                              repo, monkeypatch):
    from _aidlc_studio_backend.studio.consistency import Finding  # noqa: PLC0415 - one call site

    blocking = Finding(code="install_recovery_required", severity="blocking",
                       message_key="finding.install_recovery_required", params={}, evidence=())
    monkeypatch.setattr(sv.consistency, "evaluate_repo", lambda facts: [blocking])
    path = f"/repos/{repo.repo_id}/intents"
    request = CT.owner_request("POST", path, host=fake_host,
                              body={"scope": "poc", "depth": "Standard", "label": "new thing"})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 409 and body["code"] == "state_inconsistent"
    assert body["details"]["findings"][0]["code"] == "install_recovery_required"


def test_creating_an_intent_refuses_a_plan_it_cannot_compose(sv, routes, fake_host, repo):
    path = f"/repos/{repo.repo_id}/intents"
    request = CT.owner_request("POST", path, host=fake_host,
                              body={"scope": "no-such-scope", "depth": "Standard"})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 409 and body["code"] == "plan_invalid"


def test_recompose_preview_returns_a_proposal_and_its_digest(sv, routes, fake_host, repo):
    path = base(repo, "/recompose/preview")
    request = CT.owner_request("POST", path, host=fake_host, body={"skip": ["user-stories"], "add": []})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 200
    assert body["proposal_digest"]
    assert {"skip", "add", "allowed", "refusals"} <= set(body["proposal"])


def test_recompose_refuses_a_slug_that_is_not_a_stage(sv, routes, fake_host, repo):
    path = base(repo, "/recompose/preview")
    request = CT.owner_request("POST", path, host=fake_host, body={"skip": ["Not A Stage"]})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 400 and body["code"] == "bad_body"


def test_recompose_refuses_a_stale_proposal_digest(sv, routes, fake_host, repo):
    path = base(repo, "/recompose")
    request = CT.owner_request("POST", path, host=fake_host,
                              body={"skip": ["user-stories"], "add": [],
                                    "proposal_digest": "0" * 64})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 409 and body["code"] in ("plan_invalid", "recompose_not_allowed")


# --------------------------------------------------------------------------- #
# dispatch commands
# --------------------------------------------------------------------------- #

@pytest.fixture
def runnable_demo(demo):
    record = demo / "aidlc" / "spaces" / "default" / "intents" / INTENT
    (record / "aidlc-state.md").write_text(_state({STAGE: "-"}))
    (record / QUESTIONS_REL).unlink()
    for shard in (record / "audit").glob("*.md"):
        shard.write_text(F.audit_text(
            F.audit_block("HUMAN_TURN", "2026-09-04T09:00:00Z"),
            F.audit_block("STAGE_STARTED", "2026-09-04T09:00:01Z", Stage=STAGE),
        ))

def test_run_creates_a_queued_card_and_sends_nothing(sv, routes, fake_host, repo, slot, runnable_demo):
    path = base(repo, "/run")
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 201
    assert body["ok"] is True
    assert body["status"] == "Queued"
    assert body["action"]["type"] == "run"
    assert body["action"]["action_id"] == body["action_id"]
    assert body["action"]["risk_class"] == "human_lane"
    # Nothing reached the host: the browser does the sending, in a second call.
    assert fake_host.get_slot(slot).messages == []
    assert body["action"]["delivery"]["delivery_id"] is None


def test_run_twice_returns_the_same_card_rather_than_queueing_a_second_turn(sv, routes, fake_host,
                                                                          repo, slot, runnable_demo):
    path = base(repo, "/run")
    _status, first = call(sv, routes, "POST", path,
                          CT.owner_request("POST", path, host=fake_host, body={}))
    _status, second = call(sv, routes, "POST", path,
                           CT.owner_request("POST", path, host=fake_host, body={}))
    assert first["action_id"] == second["action_id"]
    assert len(sv.storage.select("actions")) == 1


def test_run_is_refused_while_the_intent_is_paused(sv, routes, fake_host, repo, slot):
    asyncio.run(sv.sessions.set_paused(repo.repo_id, "default", INTENT, True))
    path = base(repo, "/run")
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 409 and body["code"] == "intent_paused"


def test_run_is_refused_while_the_intent_is_archived(sv, routes, fake_host, repo, slot):
    asyncio.run(sv.sessions.set_archived(repo.repo_id, "default", INTENT, True))
    path = base(repo, "/run")
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 409 and body["code"] == "intent_archived"


def test_resume_needs_a_parked_intent(sv, routes, fake_host, repo, slot):
    path = base(repo, "/resume")
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 400 and body["code"] == "invalid_decision"


def test_prepare_commit_creates_its_own_card_type(sv, routes, fake_host, repo, slot):
    path = base(repo, "/prepare-commit")
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 201 and body["action"]["type"] == "prepare_commit"


def test_pause_reports_the_cards_it_just_blocked(sv, routes, fake_host, repo, slot, runnable_demo):
    run = base(repo, "/run")
    _status, created = call(sv, routes, "POST", run,
                            CT.owner_request("POST", run, host=fake_host, body={}))
    path = base(repo, "/pause")
    request = CT.owner_request("POST", path, host=fake_host, body={"paused": True})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 200
    assert body["binding"]["paused"] is True
    assert body["blocked_actions"] == [created["action_id"]]

    request = CT.owner_request("POST", path, host=fake_host, body={"paused": False})
    _status, body = call(sv, routes, "POST", path, request)
    assert body["binding"]["paused"] is False
    assert body["blocked_actions"] == []


def test_pause_needs_a_boolean(sv, routes, fake_host, repo):
    path = base(repo, "/pause")
    request = CT.owner_request("POST", path, host=fake_host, body={"paused": "yes"})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 400 and body["code"] == "bad_body"


def test_force_stop_needs_a_confirmation_and_a_bound_session(sv, routes, fake_host, repo):
    path = base(repo, "/force-stop")
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 400 and body["code"] == "invalid_decision"

    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={"confirm": True}))
    assert status == 409 and body["code"] == "session_unbound"


def test_force_stop_commits_a_delivering_record_and_hands_over_the_host_call(sv, routes, fake_host,
                                                                            repo, slot):
    _bind(sv, routes, fake_host, repo, slot)
    path = base(repo, "/force-stop")
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={"confirm": True}))
    assert status == 201
    assert body["lane"] == "host_control"
    assert body["status"] == "Delivering"
    assert body["wire_text"] is None                 # nothing reaches the model
    assert body["lease_generation"] is None          # and no execution lease is taken
    assert body["host"] == {"method": "POST", "path": f"/api/chat/slots/{slot}/stop", "body": {}}
    assert body["delivery_id"].startswith("dl_")
    # Durable before the browser can act on it.
    row = sv.storage.get("actions", body["action_id"])
    assert row["status"] == "Delivering" and row["boot_id"]


def test_keep_moving_always_refuses_and_records_the_attempt(sv, routes, fake_host, repo, studio):
    path = base(repo, "/keep-moving")
    request = CT.owner_request("POST", path, host=fake_host, body={"enabled": True})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 409 and body["code"] == "machine_lane_unavailable"
    assert body["details"]["reason"] == studio.constants.MACHINE_LANE_REASON
    rows = sv.storage.select("activity", {"kind": "machine_lane.refused"})
    assert len(rows) == 1


# --------------------------------------------------------------------------- #
# session binding
# --------------------------------------------------------------------------- #


def test_binding_records_the_slot_and_returns_it(sv, routes, fake_host, repo, slot):
    status, body = _bind(sv, routes, fake_host, repo, slot)
    assert status == 200
    assert body["ok"] is True
    assert body["binding"]["slot_key"] == slot
    assert body["slot"]["key"] == slot
    assert body["slot"]["agent"] == "aidlc"


def test_binding_refuses_a_slot_pointed_at_another_project(sv, routes, fake_host, repo, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    fake_host.add_slot("stranger", project=str(other), agent="aidlc", app="")
    sv.host.attach(fake_host)
    path = base(repo, "/session/bind")
    request = CT.owner_request("POST", path, host=fake_host, body={"slot_key": "stranger"})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 409 and body["code"] == "slot_mismatch"


def test_binding_needs_a_slot_key(sv, routes, fake_host, repo):
    path = base(repo, "/session/bind")
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 400 and body["code"] == "bad_body"
    assert body["details"]["missing"] == ["slot_key"]


def test_unbinding_forgets_the_slot(sv, routes, fake_host, repo, slot):
    _bind(sv, routes, fake_host, repo, slot)
    path = base(repo, "/session/unbind")
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 200 and body["binding"]["slot_key"] is None


def test_takeover_preview_lists_the_other_slots_for_this_repository(sv, routes, fake_host, repo, slot):
    _bind(sv, routes, fake_host, repo, slot)
    fake_host.add_slot("hand-opened", project=str(repo.canonical_path), agent="aidlc", app="")
    sv.host.attach(fake_host)
    path = base(repo, "/session/takeover/preview")
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 200
    assert [candidate["slot"]["key"] for candidate in body["candidates"]] == ["hand-opened"]
    assert body["candidates"][0]["reason"] == "project_match"
    assert body["current"]["slot_key"] == slot


def test_takeover_moves_the_binding(sv, routes, fake_host, repo, slot):
    _bind(sv, routes, fake_host, repo, slot)
    fake_host.add_slot("hand-opened", project=str(repo.canonical_path), agent="aidlc", app="")
    sv.host.attach(fake_host)
    path = base(repo, "/session/takeover")
    request = CT.owner_request("POST", path, host=fake_host, body={"slot_key": "hand-opened"})
    status, body = call(sv, routes, "POST", path, request)
    assert status == 200 and body["binding"]["slot_key"] == "hand-opened"


def test_archiving_and_restoring_an_intent_touches_no_aidlc_file(sv, routes, fake_host, repo, demo):
    state = demo / "aidlc" / "spaces" / "default" / "intents" / INTENT / "aidlc-state.md"
    before = state.read_bytes()
    path = base(repo, "/archive")
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 200 and body["binding"]["archive_state"] == "archived"

    path = base(repo, "/restore")
    _status, body = call(sv, routes, "POST", path,
                         CT.owner_request("POST", path, host=fake_host, body={}))
    assert body["binding"]["archive_state"] == "active"
    assert state.read_bytes() == before


def test_archiving_is_refused_while_a_card_is_live(sv, routes, fake_host, repo, slot, runnable_demo):
    run = base(repo, "/run")
    call(sv, routes, "POST", run, CT.owner_request("POST", run, host=fake_host, body={}))
    path = base(repo, "/archive")
    status, body = call(sv, routes, "POST", path,
                        CT.owner_request("POST", path, host=fake_host, body={}))
    assert status == 409 and body["code"] == "action_not_submittable"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _bind(sv: Any, routes: Any, fake_host: Any, repo: Any, slot: str) -> tuple[int, Any]:
    path = base(repo, "/session/bind")
    request = CT.owner_request("POST", path, host=fake_host, body={"slot_key": slot})
    return call(sv, routes, "POST", path, request)


def _artifact(sv: Any, repo: Any, name: str) -> Any:
    snap = sv.projection.snapshot(repo, "default", INTENT)
    metas, _truncated = sv.projection.artifacts(snap)
    return next(meta for meta in metas if meta.name == name)
