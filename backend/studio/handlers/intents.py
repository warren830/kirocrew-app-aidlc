"""Intent routes (§2.3): the read model, the plan, the session binding, and the dispatch commands.

The dispatch routes (``run``, ``resume``, ``prepare-commit``, ``force-stop``) are where a user's click
becomes something that can reach AI-DLC, and every one of them delegates the decision to
``HumanActionBroker``: a card is created (or a ``Delivering`` record committed) and the *browser* does
the sending. Nothing in this file calls a host dispatch function, and nothing here writes an AI-DLC
file — the plan routes change composition only through the engine's own verbs under an admin lease.

``keep-moving`` is here too, and it always refuses. It exists so the machine lane is a documented,
tested 409 rather than a feature somebody adds by accident (§3.2).
"""

from __future__ import annotations

import asyncio
import functools
import re
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import constants as C
from ..aidlc_reader import parse_review_section
from ..errors import StudioError
from ..plan import PlanRequest
from ..plan_advice import PlanDraftRequest
from .common import (
    cards_for,
    intent_view,
    json_ok,
    query_bool,
    query_int,
    query_str,
    read_json,
    repo_or_404,
    route,
    snapshot_or_404,
    summarize_intent,
)

#: Markdown ATX headings, for an artifact's table of contents.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

#: Commits shown on the intent git panel.
GIT_COMMIT_LIMIT = 10

#: Statuses in which a human-lane card can still be submitted, and is therefore blocked by a pause.
_SUBMITTABLE = ("Draft", "Queued", "NotDelivered")

#: Action types whose decisions reach AI-DLC as a prompt — the ones a pause blocks (C29).
_HUMAN_LANE_TYPES = tuple(
    action_type
    for action_type, decisions in C.DECISIONS.items()
    if set(decisions) & C.HUMAN_LANE_DECISIONS
)


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #


async def list_intents(services: Any, request: web.Request) -> web.Response:
    """``GET /repos/{repo_id}/intents`` — every intent's summary, with the space list and the cursor.

    ``active_space`` comes from the repository's own cursor file, not from the query: it is what the
    engine will resolve a bare ``/aidlc`` against, and showing the user's filter there instead would
    hide the mismatch that ``cursor_*`` findings exist to report.
    """
    repo = await repo_or_404(services, request.match_info["repo_id"])
    if repo.availability != "available":
        raise StudioError(
            "repo_unavailable",
            "this repository cannot be read right now",
            details={"repo_id": repo.repo_id, "availability": repo.availability},
        )
    space_filter = query_str(request, "space", pattern=C.SPACE_RE)
    state_filter = query_str(request, "state", choices=C.INTENT_STATE)
    include_archived = query_bool(request, "include_archived", False)
    needle = (query_str(request, "q") or "").casefold()

    root = Path(repo.canonical_path)
    spaces = await asyncio.to_thread(services.reader.list_spaces, root)
    active_space = await asyncio.to_thread(services.reader.active_space, root)
    leases = await services.scheduler.list()
    install = await asyncio.to_thread(services.repos.detect_install, repo)

    summaries: list[Any] = []
    for space in spaces:
        if space_filter and space != space_filter:
            continue
        for intent_dir in await asyncio.to_thread(services.reader.list_intent_dirs, root, space):
            snap = await asyncio.to_thread(services.projection.snapshot, repo, space, intent_dir)
            view = await intent_view(services, repo, snap, install=install, leases=leases)
            summary = await summarize_intent(services, repo, snap, view)
            if not include_archived and summary.archived:
                continue
            if state_filter and summary.operational_state != state_filter:
                continue
            if needle and not _matches(summary, needle):
                continue
            summaries.append(summary)

    return json_ok(
        {
            "intents": [summary.to_json() for summary in summaries],
            "spaces": list(spaces),
            "active_space": active_space,
        }
    )


async def get_intent(services: Any, request: web.Request) -> web.Response:
    """``GET /repos/{repo_id}/intents/{intent}`` — one intent, everything the detail page reads."""
    repo, snap = await _repo_and_snapshot(services, request)
    view = await intent_view(services, repo, snap)
    git = await asyncio.to_thread(services.git.observe, Path(repo.canonical_path))
    detail = await asyncio.to_thread(
        functools.partial(
            services.projection.detail,
            snap,
            binding=view["binding"],
            live_actions=[a for a in view["live_actions"] if C.attr(a, "type") != "revision"],
            breaker_open=view["breaker_open"],
            host=view["session"],
            findings=view["findings"],
            slot=view["slot"],
            git=git,
            actions=view["live_actions"],
            host_card=_host_card(services, view["binding"]),
        )
    )
    return json_ok({"intent": detail.to_json()})


async def get_map(services: Any, request: web.Request) -> web.Response:
    """``GET …/{intent}/map`` — the stage graph joined with the state file.

    ``density`` is validated and then ignored on purpose: the model carries every stage, its unit lanes
    and its dependencies, and which of them to draw is a rendering decision the client makes without a
    second round trip.
    """
    query_str(request, "density", "overview", choices=("overview", "detailed", "dependencies"))
    _repo, snap = await _repo_and_snapshot(services, request)
    model = await asyncio.to_thread(services.projection.map, snap)
    return json_ok({"map": model.to_json()})


async def list_artifacts(services: Any, request: web.Request) -> web.Response:
    """``GET …/{intent}/artifacts`` — metadata for the record's files, never their contents."""
    stage = query_str(request, "stage", pattern=C.STAGE_SLUG_RE)
    unit = query_str(request, "unit", pattern=C.UNIT_NAME_RE)
    kind = query_str(request, "kind")
    _repo, snap = await _repo_and_snapshot(services, request)
    metas, truncated = await asyncio.to_thread(services.projection.artifacts, snap)
    rows = [
        meta
        for meta in metas
        if (stage is None or meta.stage == stage)
        and (unit is None or meta.unit == unit)
        and (kind is None or meta.kind == kind)
    ]
    return json_ok(
        {
            "artifacts": [meta.to_json() for meta in rows],
            "truncated": bool(truncated),
            "count": len(rows),
        }
    )


async def get_artifact(services: Any, request: web.Request) -> web.Response:
    """``GET …/{intent}/artifacts/{artifact_id}`` — one file's content, review verdict and prior version.

    ``prior`` is read from git rather than from a Studio copy: Studio never keeps a second copy of an
    AI-DLC artifact (it would become a competing authority), so the only honest source for "what did
    this look like before" is the repository's own history.
    """
    repo, snap = await _repo_and_snapshot(services, request)
    artifact_id = request.match_info["artifact_id"]
    metas, _truncated = await asyncio.to_thread(services.projection.artifacts, snap)
    meta = next((m for m in metas if m.artifact_id == artifact_id), None)
    if meta is None:
        raise StudioError(
            "artifact_not_found", "no such artifact in this record",
            details={"artifact_id": artifact_id},
        )

    root = Path(repo.canonical_path)
    snapshot = await asyncio.to_thread(services.reader.read_artifact, root, meta.relpath)
    if snapshot is None:
        raise StudioError(
            "artifact_not_found", "this artifact is no longer readable",
            details={"artifact_id": artifact_id, "relpath": meta.relpath},
        )
    try:
        text = snapshot.bytes.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text, encoding = "", "binary"

    if query_bool(request, "raw", False):
        response = web.Response(
            body=snapshot.bytes,
            content_type="text/markdown" if encoding == "utf-8" else "application/octet-stream",
            charset="utf-8" if encoding == "utf-8" else None,
        )
        response.headers["X-Artifact-Sha256"] = snapshot.sha256
        return response

    verdict, findings = parse_review_section(text) if encoding == "utf-8" else (None, [])
    prior = await asyncio.to_thread(services.git.prior_version, root, meta.relpath)
    diff = await asyncio.to_thread(services.git.diff, root, meta.relpath) if prior is not None else None
    return json_ok(
        {
            "artifact": meta.to_json(),
            "content": text if encoding == "utf-8" else None,
            "encoding": encoding,
            "truncated": False,
            "review": (
                None
                if verdict is None and not findings
                else {"verdict": verdict, "findings": [f.to_json() for f in findings]}
            ),
            "toc": _toc(text) if encoding == "utf-8" else [],
            "prior": {
                "available": prior is not None,
                "source": "git" if prior is not None else None,
                "diff": diff,
            },
        }
    )


async def get_questions(services: Any, request: web.Request) -> web.Response:
    """``GET …/{intent}/questions`` — file or auditable disk questions, plus host corroboration.

    The host cards come from the backend (``HostBridge.pending_question_cards``) rather than from the UI
    calling ``/api/ask-question``: keeping that read here is what lets ``permissions.api`` stay at two
    prefixes (review R13). A provably pending audit enum is structured without any host card; its
    optional ``questions.origin`` identifies the disk event supplying the labels.
    """
    repo, snap = await _repo_and_snapshot(services, request)
    binding = await services.sessions.get(repo.repo_id, snap.space, snap.intent_dir)
    slot_key = binding.slot_key or None
    host_cards = services.host.pending_question_cards(slot_key) if slot_key else []
    view = await asyncio.to_thread(
        functools.partial(
            services.projection.questions_view, snap, host_card=host_cards[0] if host_cards else None
        )
    )
    return json_ok(
        {
            "questions": view.to_json() if view is not None else None,
            "mode": view.mode if view is not None else "degraded",
            "host_cards": list(host_cards),
            "slot_key": slot_key,
        }
    )


async def get_review(services: Any, request: web.Request) -> web.Response:
    """``GET …/{intent}/review`` — the current stage's verdict, its findings and the audit receipts.

    The receipts are AI-DLC's own ``REVIEW_*`` audit rows: the verdict inside an artifact says what the
    reviewer concluded, and only the audit says when it was asked and how many iterations it took.
    """
    _repo, snap = await _repo_and_snapshot(services, request)
    verdict, findings = snap.review if snap.review is not None else (None, [])
    receipts = [
        {
            "event": event.event,
            "ts": event.timestamp,
            "reviewer": event.fields.get("Reviewer") or event.fields.get("Agent"),
            "iteration": _int_or_none(event.fields.get("Iteration")),
            "verdict": event.fields.get("Verdict") or event.fields.get("Result"),
            "fingerprint": event.fields.get("Fingerprint"),
        }
        for event in snap.audit.events
        if event.event in C.REVIEW_EVENTS
    ]
    reviewer = next((f.reviewer for f in findings if f.reviewer), None)
    return json_ok(
        {
            "stage": snap.stage,
            "verdict": verdict,
            "findings": [f.to_json() for f in findings],
            "receipts": receipts,
            "review_class": _review_class(snap),
            "reviewer": reviewer,
            "revisions": snap.state.revision_count if snap.state is not None else 0,
        }
    )


async def get_intent_git(services: Any, request: web.Request) -> web.Response:
    """``GET …/{intent}/git`` — the observation, recent commits for this record, and its dirty files."""
    repo, snap = await _repo_and_snapshot(services, request)
    root = Path(repo.canonical_path)
    record = services.reader.record_rel(snap.space, snap.intent_dir)
    observation = await asyncio.to_thread(services.git.observe, root)
    commits = await asyncio.to_thread(
        functools.partial(services.git.recent_commits, root, [record], GIT_COMMIT_LIMIT)
    )
    split = await asyncio.to_thread(services.git.dirty_split, root, [record])
    return json_ok(
        {
            "git": observation.to_json(),
            "commits": [commit.to_json() for commit in commits],
            "changed_artifacts": split["owned_changes"],
        }
    )


async def get_intent_activity(services: Any, request: web.Request) -> web.Response:
    """``GET …/{intent}/activity`` — the merged timeline (Studio rows + this intent's audit rows)."""
    limit = query_int(
        request, "limit", C.ACTIVITY_DEFAULT_LIMIT, lo=1, hi=C.ACTIVITY_MAX_LIMIT
    ) or C.ACTIVITY_DEFAULT_LIMIT
    source = query_str(request, "source", choices=C.SOURCE)
    _repo, snap = await _repo_and_snapshot(services, request)
    entries = await services.activity.timeline(
        snap, limit=limit, sources=[source] if source else None
    )
    return json_ok({"items": [entry.to_json() for entry in entries], "next_cursor": None})


# --------------------------------------------------------------------------- #
# plan and composition
# --------------------------------------------------------------------------- #


async def plan_preview(services: Any, request: web.Request) -> web.Response:
    """``POST /repos/{repo_id}/intents/plan/preview`` — the plan a request describes, unwritten."""
    body = await read_json(request)
    repo = await repo_or_404(services, request.match_info["repo_id"])
    req = PlanRequest.from_json(body, repo_id=repo.repo_id)
    plan = await asyncio.to_thread(
        functools.partial(
            services.plan.effective_plan, repo, repo.engine_dir or C.STUDIO_HARNESS_DIR, req
        )
    )
    return json_ok({"plan": plan.to_json()})


async def plan_advise(services: Any, request: web.Request) -> web.Response:
    """``POST /repos/{repo_id}/intents/plan/advise`` — ask the Advisor to propose the plan settings (202).

    A card-less draft (§1.18a, FR-NEW-006): no intent exists yet, so the request is per repository and
    objective rather than per action card. ``objective`` is the one field the draft cannot be made
    without — ``read_json`` refuses a missing or empty one and ``PlanDraftRequest.from_json`` refuses a
    whitespace-only one with the same ``bad_body`` shape. The broker builds its own ``DraftRequest`` with
    ``auto=False``; a body that claims ``"auto": true`` is ignored, exactly as on ``/advisor/draft``
    (FR-ADV-010). Nothing here creates an intent, writes an AI-DLC file or applies the proposal: the
    answer is a draft the wizard renders through ``plan/preview``, and the human decides what to keep.
    """
    body = await read_json(request, required=("objective",))
    repo = await repo_or_404(services, request.match_info["repo_id"])
    req = PlanDraftRequest.from_json(body, repo_id=repo.repo_id)
    draft = await services.advisor.request_plan(req, repo, user=str(request.get("user") or ""))
    return json_ok({"ok": True, "draft": draft.to_json()}, status=202)


async def create_intent(services: Any, request: web.Request) -> web.Response:
    """``POST /repos/{repo_id}/intents`` — create an intent with the engine's own verb (201).

    The new intent is inert: nothing is dispatched, nothing is queued, ``Idle`` until a human presses
    Run. "Studio started a workflow nobody asked for" is impossible by construction (FR-RUN-001).
    """
    body = await read_json(request)
    repo = await repo_or_404(services, request.match_info["repo_id"])
    req = PlanRequest.from_json(body, repo_id=repo.repo_id)
    findings = await _repo_findings(services, repo)
    result = await services.plan.create_intent(
        repo,
        req,
        blocking_findings=findings,
        confirm_plan_digest=(
            str(body["confirm_plan_digest"]) if body.get("confirm_plan_digest") else None
        ),
    )
    return json_ok(
        {"ok": True, "transaction_id": result.transaction_id, "intent": result.to_json()}, status=201
    )


async def recompose_preview(services: Any, request: web.Request) -> web.Response:
    """``POST …/{intent}/recompose/preview`` — what a skip/add would do, and every reason it must not."""
    body = await read_json(request)
    repo, snap = await _repo_and_snapshot(services, request)
    proposal = await asyncio.to_thread(
        functools.partial(
            services.plan.recompose_preview,
            repo,
            snap,
            skip=_slug_list(body.get("skip")),
            add=_slug_list(body.get("add")),
        )
    )
    return json_ok({"proposal": proposal.to_json(), "proposal_digest": proposal.digest()})


async def recompose_apply(services: Any, request: web.Request) -> web.Response:
    """``POST …/{intent}/recompose`` — apply a previewed recompose against its digest.

    The snapshot is taken fresh here and the proposal rebuilt from it: a confirmation is a statement
    about one plan, and applying it to a workflow that moved a stage in between is how a user skips a
    stage they were looking at.
    """
    body = await read_json(request, required=("proposal_digest",))
    repo, snap = await _repo_and_snapshot(services, request)
    result = await services.plan.recompose(
        repo,
        snap,
        skip=_slug_list(body.get("skip")),
        add=_slug_list(body.get("add")),
        proposal_digest=str(body["proposal_digest"]),
    )
    return json_ok(
        {
            "ok": True,
            "transaction_id": result.transaction_id,
            "result": result.engine.to_json(),
            "intent": None if result.summary is None else result.summary.to_json(),
        }
    )


# --------------------------------------------------------------------------- #
# dispatch commands
# --------------------------------------------------------------------------- #


async def _command(services: Any, request: web.Request, action_type: str) -> web.Response:
    """Create the ``run``/``resume``/``prepare_commit`` card a human asked for (201).

    Creating a card, not sending anything: the card carries the wire text and the captured evidence, and
    ``POST /actions/{id}/submit`` is the step that commits a delivery. Two clicks, two records — which is
    what makes "one Run = exactly one turn" auditable (FR-RUN-001).
    """
    await read_json(request)
    repo, snap = await _repo_and_snapshot(services, request)
    if action_type == "resume" and not (snap.state is not None and snap.state.parked_at):
        raise StudioError(
            "invalid_decision",
            "this intent is not parked, so there is nothing to resume",
            details={"intent_key": snap.intent_key},
        )
    rec = await services.actions.create_command(
        action_type, repo, snap.space, snap.intent_dir, snap=snap
    )
    cards = await cards_for(services, [rec])
    return json_ok(
        {"ok": True, "action_id": rec.action_id, "status": rec.status, "action": cards[0]}, status=201
    )


async def run_intent(services: Any, request: web.Request) -> web.Response:
    return await _command(services, request, "run")


async def resume_intent(services: Any, request: web.Request) -> web.Response:
    return await _command(services, request, "resume")


async def prepare_commit(services: Any, request: web.Request) -> web.Response:
    return await _command(services, request, "prepare_commit")


async def pause_intent(services: Any, request: web.Request) -> web.Response:
    """``POST …/{intent}/pause`` — stop dispatch for this intent, and say what that just blocked.

    ``blocked_actions`` is the honest half of a pause: the cards are still there, still show the
    boundary, and ``submit`` will now refuse them with ``intent_paused`` (C29). Listing them is how the
    UI can grey them out instead of letting a user click one and get a 409.
    """
    body = await read_json(request)
    paused = body.get("paused")
    if not isinstance(paused, bool):
        raise StudioError("bad_body", "paused must be a boolean", details={"missing": ["paused"]})
    repo, snap = await _repo_and_snapshot(services, request)
    binding = await services.sessions.set_paused(repo.repo_id, snap.space, snap.intent_dir, paused)
    blocked: list[str] = []
    if paused:
        live = await services.actions.list_live(
            repo_id=repo.repo_id, space=snap.space, intent_dir=snap.intent_dir, include_revision=True
        )
        blocked = [
            rec.action_id
            for rec in live
            if rec.status in _SUBMITTABLE and rec.type in _HUMAN_LANE_TYPES
        ]
    return json_ok({"ok": True, "binding": binding.to_json(), "blocked_actions": blocked})


async def force_stop(services: Any, request: web.Request) -> web.Response:
    """``POST …/{intent}/force-stop`` — the host-control lane (201, C28).

    No prompt, no wire text, no execution lease, no cursor switch: nothing reaches the model, so none of
    the guards that protect a *decision* apply. What does apply is the durable record — FR-RUN-004 wants
    an interruption confirmed and recorded before it happens, and that record is what later becomes
    ``Interrupted`` and the manual takeover PRD §11.2 requires.
    """
    body = await read_json(request)
    if body.get("confirm") is not True:
        raise StudioError(
            "invalid_decision",
            "stopping a running turn requires confirm: true",
            details={"reason": "confirm_required"},
        )
    repo, snap = await _repo_and_snapshot(services, request)
    receipt = await services.actions.create_force_stop(
        repo, snap.space, snap.intent_dir, snap=snap
    )
    return json_ok(receipt.to_json(), status=201)


async def keep_moving(services: Any, request: web.Request) -> web.Response:
    """``POST …/{intent}/keep-moving`` — always 409.

    There is no machine lane in v1: every KiroCrew path to kiro-cli mints a ``HUMAN_TURN``, so an
    unattended continuation would forge human presence in AI-DLC's own audit trail (§3.2, S12). The
    broker records the refusal, so an attempt is visible rather than merely rejected.
    """
    await read_json(request)
    await services.machine.dispatch(
        route="keep-moving",
        repo_id=request.match_info.get("repo_id"),
        intent=request.match_info.get("intent"),
    )
    raise AssertionError("unreachable: MachineActionBroker.dispatch always raises")


# --------------------------------------------------------------------------- #
# session binding
# --------------------------------------------------------------------------- #


async def bind_session(services: Any, request: web.Request) -> web.Response:
    """``POST …/{intent}/session/bind`` — record which slot this intent's decisions go to.

    Studio cannot create or re-point a host slot (the UI does that, §2.13); it verifies and records.
    Binding to a slot whose project or agent differs would send a gate approval into a conversation that
    is not running AI-DLC over this repository, so every check is a refusal rather than a repair.
    """
    body = await read_json(request, required=("slot_key",))
    repo, snap = await _repo_and_snapshot(services, request)
    binding = await services.sessions.bind(
        repo,
        snap.space,
        snap.intent_dir,
        slot_key=str(body["slot_key"]),
        intent_uuid=snap.row.uuid if snap.row is not None else None,
    )
    slot = services.host.slot(binding.slot_key or "")
    return json_ok(
        {
            "ok": True,
            "binding": binding.to_json(),
            "slot": slot.to_json() if slot is not None else None,
        }
    )


async def unbind_session(services: Any, request: web.Request) -> web.Response:
    """``POST …/{intent}/session/unbind`` — forget the slot. Refused while something may be on the wire."""
    await read_json(request)
    repo, snap = await _repo_and_snapshot(services, request)
    binding = await services.sessions.unbind(
        repo.repo_id, snap.space, snap.intent_dir, reason="user_unbound"
    )
    return json_ok({"ok": True, "binding": binding.to_json()})


async def takeover_preview(services: Any, request: web.Request) -> web.Response:
    """``POST …/{intent}/session/takeover/preview`` — slots this intent could move to."""
    await read_json(request)
    repo, snap = await _repo_and_snapshot(services, request)
    candidates = await services.sessions.takeover_preview(repo, snap.space, snap.intent_dir)
    current = await services.sessions.get(repo.repo_id, snap.space, snap.intent_dir)
    return json_ok(
        {
            "candidates": [candidate.to_json() for candidate in candidates],
            "current": current.to_json(),
        }
    )


async def takeover(services: Any, request: web.Request) -> web.Response:
    """``POST …/{intent}/session/takeover`` — move the intent to another slot."""
    body = await read_json(request, required=("slot_key",))
    repo, snap = await _repo_and_snapshot(services, request)
    binding = await services.sessions.takeover(
        repo, snap.space, snap.intent_dir, slot_key=str(body["slot_key"])
    )
    return json_ok({"ok": True, "binding": binding.to_json()})


async def archive_intent(services: Any, request: web.Request) -> web.Response:
    """``POST …/{intent}/archive`` — hide it from Studio's queue. AI-DLC's files are untouched."""
    await read_json(request)
    repo, snap = await _repo_and_snapshot(services, request)
    binding = await services.sessions.set_archived(repo.repo_id, snap.space, snap.intent_dir, True)
    return json_ok({"ok": True, "binding": binding.to_json()})


async def restore_intent(services: Any, request: web.Request) -> web.Response:
    """``POST …/{intent}/restore`` — bring it back into the queue."""
    await read_json(request)
    repo, snap = await _repo_and_snapshot(services, request)
    binding = await services.sessions.set_archived(repo.repo_id, snap.space, snap.intent_dir, False)
    return json_ok({"ok": True, "binding": binding.to_json()})


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


async def _repo_and_snapshot(services: Any, request: web.Request) -> tuple[Any, Any]:
    repo = await repo_or_404(services, request.match_info["repo_id"])
    snap = await snapshot_or_404(services, repo, request.match_info["intent"])
    return repo, snap


def _host_card(services: Any, binding: Any) -> dict | None:
    slot_key = C.attr(binding, "slot_key")
    if not slot_key:
        return None
    cards = services.host.pending_question_cards(str(slot_key))
    return cards[0] if cards else None


async def _repo_findings(services: Any, repo: Any) -> list[Any]:
    """Repository-level findings, for the create-intent refusal.

    Repo-level rather than per-intent: creating an intent is blocked by a repository that contradicts
    itself (a failed install, a duplicate identity), not by another intent's open gate.
    """
    from ..consistency import RepoFacts

    install = await asyncio.to_thread(services.repos.detect_install, repo)
    layout = await asyncio.to_thread(services.reader.layout, Path(repo.canonical_path))
    facts = RepoFacts(
        now=services.clock.iso(),
        repo=repo,
        install=install,
        leases=await services.scheduler.list(),
        layout=layout,
    )
    return await asyncio.to_thread(services.consistency.evaluate_repo, facts)


def _matches(summary: Any, needle: str) -> bool:
    """Free-text filter over the fields a user would type: dir, slug, title, scope."""
    haystack = " ".join(
        str(value or "")
        for value in (summary.intent_dir, summary.slug, summary.title, summary.scope, summary.uuid)
    )
    return needle in haystack.casefold()


def _slug_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise StudioError("bad_body", "skip and add must be arrays of stage slugs",
                          details={"reason": "not_an_array"})
    out: list[str] = []
    for item in value:
        text = str(item or "")
        if not C.STAGE_SLUG_RE.match(text):
            raise StudioError("bad_body", "not a stage slug", details={"value": text})
        out.append(text)
    return out


def _toc(text: str) -> list[dict[str, Any]]:
    """Headings with GitHub-style anchors, so a deep link into an artifact lands where it says."""
    out: list[dict[str, Any]] = []
    for match in _HEADING_RE.finditer(text):
        title = match.group(2).strip()
        anchor = re.sub(r"[^a-z0-9\- ]", "", title.casefold()).strip().replace(" ", "-")
        out.append({"level": len(match.group(1)), "text": title, "anchor": anchor})
    return out


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _review_class(snap: Any) -> str | None:
    """The reviewer class the stage declares (``review`` in the stage graph), when it declares one."""
    node = snap.node(snap.stage)
    return C.attr(node, "review_class") if node is not None else None


def ROUTES(services: Any) -> list[Any]:
    intent = "/repos/{repo_id}/intents/{intent}"
    return [
        route("GET", "/repos/{repo_id}/intents", list_intents, services=services),
        route("POST", "/repos/{repo_id}/intents", create_intent, owner=True, services=services),
        route(
            "POST", "/repos/{repo_id}/intents/plan/preview", plan_preview, owner=True, services=services
        ),
        route(
            "POST", "/repos/{repo_id}/intents/plan/advise", plan_advise, owner=True, services=services
        ),
        route("GET", intent, get_intent, services=services),
        route("GET", f"{intent}/map", get_map, services=services),
        route("GET", f"{intent}/artifacts", list_artifacts, services=services),
        route("GET", f"{intent}/artifacts/{{artifact_id}}", get_artifact, services=services),
        route("GET", f"{intent}/questions", get_questions, services=services),
        route("GET", f"{intent}/review", get_review, services=services),
        route("GET", f"{intent}/git", get_intent_git, services=services),
        route("GET", f"{intent}/activity", get_intent_activity, services=services),
        route("POST", f"{intent}/recompose/preview", recompose_preview, owner=True, services=services),
        route("POST", f"{intent}/recompose", recompose_apply, owner=True, services=services),
        route("POST", f"{intent}/run", run_intent, owner=True, services=services),
        route("POST", f"{intent}/resume", resume_intent, owner=True, services=services),
        route("POST", f"{intent}/pause", pause_intent, owner=True, services=services),
        route("POST", f"{intent}/force-stop", force_stop, owner=True, services=services),
        route("POST", f"{intent}/prepare-commit", prepare_commit, owner=True, services=services),
        route("POST", f"{intent}/keep-moving", keep_moving, owner=True, services=services),
        route("POST", f"{intent}/session/bind", bind_session, owner=True, services=services),
        route("POST", f"{intent}/session/unbind", unbind_session, owner=True, services=services),
        route(
            "POST", f"{intent}/session/takeover/preview", takeover_preview,
            owner=True, services=services,
        ),
        route("POST", f"{intent}/session/takeover", takeover, owner=True, services=services),
        route("POST", f"{intent}/archive", archive_intent, owner=True, services=services),
        route("POST", f"{intent}/restore", restore_intent, owner=True, services=services),
    ]
