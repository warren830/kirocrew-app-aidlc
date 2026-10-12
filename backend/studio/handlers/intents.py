"""Intent routes (§2.3): the read model, the plan, the commands and the session binding.

A command here creates a card (or commits a ``Delivering`` record) through ``HumanActionBroker`` and the
browser does the sending: nothing in this file calls a host dispatch function or writes an AI-DLC file. The
plan routes change composition only through the engine's own verbs under an admin lease.
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
from ..source_changes import read_changes
from .common import (
    body_bool, body_choice, card_of, intent_view, json_ok, query_bool, query_str, read_json, repo_and_snapshot,
    repo_or_404, summarize_intent, summary_args, user_of,
)

#: Markdown ATX headings, for an artifact's table of contents.
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)

#: Statuses in which a card can still be submitted, and so is blocked by a pause.
_SUBMITTABLE = ("Queued", "NotDelivered")

#: Action types whose decisions reach AI-DLC as a prompt — the ones a pause blocks (C29).
_HUMAN_LANE_TYPES = tuple(t for t, decisions in C.DECISIONS.items() if set(decisions) & C.HUMAN_LANE_DECISIONS)


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #


async def list_intents(services: Any, request: web.Request) -> web.Response:
    """``active_space`` is the repository's own cursor file, not the filter: it is what a bare ``/aidlc``
    resolves against, and showing the filter there would hide the mismatch ``cursor_*`` findings report."""
    repo = await repo_or_404(services, request.match_info["repo_id"])
    if repo.availability != "available":
        raise StudioError("repo_unavailable", "this repository cannot be read right now",
                          details={"repo_id": repo.repo_id, "availability": repo.availability})
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
            summary = await summarize_intent(
                services, snap, await intent_view(services, repo, snap, install=install, leases=leases))
            if ((include_archived or not summary.archived)
                    and (not state_filter or summary.operational_state == state_filter)
                    and (not needle or needle in _haystack(summary))):
                summaries.append(summary)
    return json_ok({"intents": [s.to_json() for s in summaries], "spaces": list(spaces), "active_space": active_space})


async def get_intent(services: Any, request: web.Request) -> web.Response:
    repo, snap = await repo_and_snapshot(services, request)
    view = await intent_view(services, repo, snap)
    git = await asyncio.to_thread(services.git.observe, Path(repo.canonical_path))
    slot_key = view["binding"].slot_key
    host_cards = services.host.pending_question_cards(str(slot_key)) if slot_key else []
    detail = await asyncio.to_thread(functools.partial(
        services.projection.detail, snap, **summary_args(view), git=git, actions=view["live_actions"],
        host_card=host_cards[0] if host_cards else None,
    ))
    return json_ok({"intent": detail.to_json()})


async def get_map(services: Any, request: web.Request) -> web.Response:
    """The facts ``ui/src/map/buildMap.ts`` joins: every stage, lane and dependency, so which to draw is a
    rendering choice the client makes without a second round trip."""
    _repo, snap = await repo_and_snapshot(services, request)
    return json_ok({"map_inputs": await asyncio.to_thread(services.projection.map_inputs, snap)})


async def list_artifacts(services: Any, request: web.Request) -> web.Response:
    """Metadata only, never contents."""
    stage = query_str(request, "stage", pattern=C.STAGE_SLUG_RE)
    unit = query_str(request, "unit", pattern=C.UNIT_NAME_RE)
    kind = query_str(request, "kind")
    _repo, snap = await repo_and_snapshot(services, request)
    metas, truncated = await asyncio.to_thread(services.projection.artifacts, snap)
    rows = [meta for meta in metas if (stage is None or meta.stage == stage)
            and (unit is None or meta.unit == unit) and (kind is None or meta.kind == kind)]
    return json_ok({"artifacts": [meta.to_json() for meta in rows], "truncated": bool(truncated), "count": len(rows)})


async def get_artifact(services: Any, request: web.Request) -> web.Response:
    """One file's content, review verdict and prior version. ``prior`` comes from git: Studio never keeps a
    copy of an AI-DLC artifact (it would become a competing authority), so history is the only honest source."""
    repo, snap = await repo_and_snapshot(services, request)
    artifact_id = request.match_info["artifact_id"]
    metas, _truncated = await asyncio.to_thread(services.projection.artifacts, snap)
    meta = next((m for m in metas if m.artifact_id == artifact_id), None)
    if meta is None:
        raise StudioError("artifact_not_found", "no such artifact in this record", details={"artifact_id": artifact_id})
    root = Path(repo.canonical_path)
    snapshot = await asyncio.to_thread(services.reader.read_artifact, root, meta.relpath)
    if snapshot is None:
        raise StudioError("artifact_not_found", "this artifact is no longer readable",
                          details={"artifact_id": artifact_id, "relpath": meta.relpath})
    try:
        text, encoding = snapshot.bytes.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        text, encoding = "", "binary"
    utf8 = encoding == "utf-8"

    if query_bool(request, "raw", False):
        response = web.Response(body=snapshot.bytes, content_type="text/markdown" if utf8 else "application/octet-stream",
                                charset="utf-8" if utf8 else None)
        response.headers["X-Artifact-Sha256"] = snapshot.sha256
        return response

    verdict, findings = parse_review_section(text) if utf8 else (None, [])
    prior = await asyncio.to_thread(services.git.prior_version, root, meta.relpath)
    diff = await asyncio.to_thread(services.git.diff, root, meta.relpath) if prior is not None else None
    return json_ok({
        "artifact": meta.to_json(),
        "content": text if utf8 else None,
        "encoding": encoding,
        "truncated": False,
        "review": None if verdict is None and not findings else {
            "verdict": verdict, "findings": [f.to_json() for f in findings]},
        "toc": _toc(text) if utf8 else [],
        "prior": {"available": prior is not None, "source": "git" if prior is not None else None, "diff": diff},
    })


async def get_changes(services: Any, request: web.Request) -> web.Response:
    """The source files a stage lists in its ``source-manifest.json``, each diffed against HEAD: the code a
    code-generation gate asks the human to approve, beside the plan its reviewer judged. ``manifest`` is ``null``
    when the stage wrote no manifest (no code stage, or not reached yet)."""
    stage = query_str(request, "stage", pattern=C.STAGE_SLUG_RE)
    unit = query_str(request, "unit", pattern=C.UNIT_NAME_RE)
    if stage is None:
        raise StudioError("bad_param", "stage is required", details={"param": "stage"})
    repo, snap = await repo_and_snapshot(services, request)
    metas, _truncated = await asyncio.to_thread(services.projection.artifacts, snap)
    manifest = next((meta for meta in metas if meta.name == C.SOURCE_MANIFEST_NAME and meta.stage == stage
                     and (meta.unit or None) == unit), None)
    body: dict[str, Any] = {"stage": stage, "unit": unit, "manifest": manifest.relpath if manifest else None}
    if manifest is None:
        return json_ok({**body, "available": True, "reason": None, "files": [], "truncated": False,
                        "manifest_readable": False})
    changes = await asyncio.to_thread(read_changes, services.reader, services.git, Path(repo.canonical_path),
                                      manifest.relpath)
    return json_ok({**body, **changes})


async def get_review(services: Any, request: web.Request) -> web.Response:
    """The verdict inside the artifact says what the reviewer concluded; only AI-DLC's own ``REVIEW_*`` audit
    rows say when it was asked and how many iterations it took, so both are served."""
    _repo, snap = await repo_and_snapshot(services, request)
    verdict, findings = snap.review if snap.review is not None else (None, [])
    node = snap.node(snap.stage)
    return json_ok({
        "stage": snap.stage,
        "verdict": verdict,
        "findings": [f.to_json() for f in findings],
        "receipts": [
            {"event": event.event, "ts": event.timestamp,
             "reviewer": event.fields.get("Reviewer") or event.fields.get("Agent"),
             "iteration": _int_or_none(event.fields.get("Iteration")),
             "verdict": event.fields.get("Verdict") or event.fields.get("Result"),
             "fingerprint": event.fields.get("Fingerprint")}
            for event in snap.audit.events if event.event in C.REVIEW_EVENTS
        ],
        "review_class": C.attr(node, "review_class") if node is not None else None,
        "reviewer": next((f.reviewer for f in findings if f.reviewer), None),
        "revisions": snap.state.revision_count if snap.state is not None else 0,
    })


# --------------------------------------------------------------------------- #
# plan and composition
# --------------------------------------------------------------------------- #


async def plan_scopes(services: Any, request: web.Request) -> web.Response:
    """Every scope the installed engine defines, with its own description and how many of the graph's stages its
    grid runs: what the wizard's scope cards compare before one is picked. Read from the repository's installed
    ``.kiro/scopes`` and ``scope-grid.json``; the exact plan still comes from ``plan/preview``."""
    repo = await repo_or_404(services, request.match_info["repo_id"])
    catalog = await asyncio.to_thread(services.plan.catalog, repo, repo.engine_dir or C.STUDIO_HARNESS_DIR)
    metas = {meta.name: meta for meta in catalog.scopes}
    total = len(catalog.stages)
    rows = []
    for name in sorted(set(metas) | set(catalog.grid)):
        meta, grid = metas.get(name), catalog.grid.get(name)
        rows.append({"name": name, "description": meta.description if meta is not None else None,
                     "stages": sum(1 for value in grid.values() if value == "EXECUTE") if grid else None})
    return json_ok({"scopes": rows, "graph_stage_count": total})


async def plan_preview(services: Any, request: web.Request) -> web.Response:
    """The plan a request describes, unwritten."""
    body = await read_json(request)
    repo = await repo_or_404(services, request.match_info["repo_id"])
    req = PlanRequest.from_json(body, repo_id=repo.repo_id)
    plan = await asyncio.to_thread(services.plan.effective_plan, repo, repo.engine_dir or C.STUDIO_HARNESS_DIR, req)
    return json_ok({"plan": plan.to_json()})


async def plan_advise(services: Any, request: web.Request) -> web.Response:
    """A card-less plan draft (202; §1.18a, FR-NEW-006) for an intent that does not exist yet.

    The broker builds its own request with ``auto=False`` whatever the body claims (FR-ADV-010). Nothing is
    created or applied: the wizard renders the proposal through ``plan/preview`` and the human decides.
    """
    body = await read_json(request, required=("objective",))
    repo = await repo_or_404(services, request.match_info["repo_id"])
    draft = await services.advisor.request_plan(PlanDraftRequest.from_json(body, repo_id=repo.repo_id), repo,
                                                user=user_of(request))
    return json_ok({"ok": True, "draft": draft.to_json()}, status=202)


async def create_intent(services: Any, request: web.Request) -> web.Response:
    """Created inert (201): nothing dispatched or queued until a human presses Run (FR-RUN-001)."""
    body = await read_json(request)
    repo = await repo_or_404(services, request.match_info["repo_id"])
    req = PlanRequest.from_json(body, repo_id=repo.repo_id)
    result = await services.plan.create_intent(
        repo, req, blocking_findings=await _repo_findings(services, repo),
        confirm_plan_digest=str(body["confirm_plan_digest"]) if body.get("confirm_plan_digest") else None,
    )
    return json_ok({"ok": True, "transaction_id": result.transaction_id, "intent": result.to_json()}, status=201)


async def recompose_preview(services: Any, request: web.Request) -> web.Response:
    """What a skip/add would do, and every reason it must not."""
    body = await read_json(request)
    repo, snap = await repo_and_snapshot(services, request)
    proposal = await asyncio.to_thread(functools.partial(
        services.plan.recompose_preview, repo, snap, skip=_slug_list(body.get("skip")), add=_slug_list(body.get("add"))))
    return json_ok({"proposal": proposal.to_json(), "proposal_digest": proposal.digest()})


async def recompose(services: Any, request: web.Request) -> web.Response:
    """Applied against the preview's digest on a fresh snapshot: a confirmation is about one plan, and
    applying it after a stage moved is how a user skips a stage they were looking at."""
    body = await read_json(request, required=("proposal_digest",))
    repo, snap = await repo_and_snapshot(services, request)
    result = await services.plan.recompose(repo, snap, skip=_slug_list(body.get("skip")),
                                           add=_slug_list(body.get("add")), proposal_digest=str(body["proposal_digest"]))
    return json_ok({"ok": True, "transaction_id": result.transaction_id, "result": result.engine.to_json(),
                    "intent": None if result.summary is None else result.summary.to_json()})


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


async def run(services: Any, request: web.Request) -> web.Response:
    """``{"type": "run"|"resume"}`` (default ``run``): create the command card a human asked for (201).

    Creating, not sending — ``POST /actions/{id}/submit`` commits the delivery, so one Run is two records and
    "exactly one turn" stays auditable (FR-RUN-001).
    """
    action_type = body_choice(await read_json(request), "type", ("run", "resume"), "run")
    repo, snap = await repo_and_snapshot(services, request)
    if action_type == "resume" and not (snap.state is not None and snap.state.parked_at):
        raise StudioError("invalid_decision", "this intent is not parked, so there is nothing to resume",
                          details={"intent_key": snap.intent_key})
    rec = await services.actions.create_command(action_type, repo, snap.space, snap.intent_dir, snap=snap)
    return json_ok({"ok": True, "action_id": rec.action_id, "status": rec.status, "action": await card_of(services, rec)},
                   status=201)


async def pause(services: Any, request: web.Request) -> web.Response:
    """``{"paused": bool}``. ``blocked_actions`` lists the live cards ``submit`` will now refuse with
    ``intent_paused`` (C29), so the UI greys them out instead of letting a click earn a 409."""
    paused = body_bool(await read_json(request), "paused")
    repo, snap = await repo_and_snapshot(services, request)
    binding = await services.sessions.set_paused(repo.repo_id, snap.space, snap.intent_dir, paused)
    blocked: list[str] = []
    if paused:
        live = await services.actions.list_live(repo_id=repo.repo_id, space=snap.space, intent_dir=snap.intent_dir,
                                                include_revision=True)
        blocked = [rec.action_id for rec in live if rec.status in _SUBMITTABLE and rec.type in _HUMAN_LANE_TYPES]
    return json_ok({"ok": True, "binding": binding.to_json(), "blocked_actions": blocked})


async def archive(services: Any, request: web.Request) -> web.Response:
    """``{"archived": bool}``: hide the intent from Studio's queue or bring it back. AI-DLC's files are untouched."""
    archived = body_bool(await read_json(request), "archived")
    repo, snap = await repo_and_snapshot(services, request)
    binding = await services.sessions.set_archived(repo.repo_id, snap.space, snap.intent_dir, archived)
    return json_ok({"ok": True, "binding": binding.to_json()})


async def force_stop(services: Any, request: web.Request) -> web.Response:
    """The host-control lane (201, C28): no prompt, no execution lease, no cursor switch — nothing reaches the
    model — but a durable, confirmed record (FR-RUN-004) that later becomes ``Interrupted`` (PRD §11.2)."""
    body = await read_json(request)
    if body.get("confirm") is not True:
        raise StudioError("invalid_decision", "stopping a running turn requires confirm: true",
                          details={"reason": "confirm_required"})
    repo, snap = await repo_and_snapshot(services, request)
    receipt = await services.actions.create_force_stop(repo, snap.space, snap.intent_dir, snap=snap)
    return json_ok(receipt.to_json(), status=201)


# --------------------------------------------------------------------------- #
# session binding
# --------------------------------------------------------------------------- #


async def set_session(services: Any, request: web.Request) -> web.Response:
    """``{"slot_key": str | null}``: bind, move or forget the slot this intent's decisions go to.

    Studio verifies and records; the UI creates and points host slots (§2.13). Moving a bound intent is a
    takeover and forgetting is an unbind, and both are refused while a decision may still be on the wire.
    """
    body = await read_json(request)
    if "slot_key" not in body:
        raise StudioError("bad_body", "request body is missing required fields", details={"missing": ["slot_key"]})
    slot_key = str(body["slot_key"] or "")
    repo, snap = await repo_and_snapshot(services, request)
    sessions = services.sessions
    if not slot_key:
        binding = await sessions.unbind(repo.repo_id, snap.space, snap.intent_dir, reason="user_unbound")
    elif (await sessions.get(repo.repo_id, snap.space, snap.intent_dir)).slot_key not in (None, "", slot_key):
        binding = await sessions.takeover(repo, snap.space, snap.intent_dir, slot_key=slot_key)
    else:
        binding = await sessions.bind(repo, snap.space, snap.intent_dir, slot_key=slot_key,
                                      intent_uuid=snap.row.uuid if snap.row is not None else None)
    slot = services.host.slot(binding.slot_key) if binding.slot_key else None
    return json_ok({"ok": True, "binding": binding.to_json(), "slot": slot.to_json() if slot is not None else None})


async def takeover_preview(services: Any, request: web.Request) -> web.Response:
    """The slots this intent could move to."""
    await read_json(request)
    repo, snap = await repo_and_snapshot(services, request)
    candidates = await services.sessions.takeover_preview(repo, snap.space, snap.intent_dir)
    current = await services.sessions.get(repo.repo_id, snap.space, snap.intent_dir)
    return json_ok({"candidates": [c.to_json() for c in candidates], "current": current.to_json()})


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


async def _repo_findings(services: Any, repo: Any) -> list[Any]:
    """Repository-level findings: creating an intent is blocked by a repository that contradicts itself
    (a failed install, a duplicate identity), not by another intent's open gate."""
    from ..consistency import RepoFacts

    install = await asyncio.to_thread(services.repos.detect_install, repo)
    layout = await asyncio.to_thread(services.reader.layout, Path(repo.canonical_path))
    facts = RepoFacts(now=services.clock.iso(), repo=repo, install=install, leases=await services.scheduler.list(),
                      layout=layout)
    return await asyncio.to_thread(services.consistency.evaluate_repo, facts)


def _haystack(summary: Any) -> str:
    """What a user would type into the filter: dir, slug, title, scope, uuid."""
    fields = (summary.intent_dir, summary.slug, summary.title, summary.scope, summary.uuid)
    return " ".join(str(value or "") for value in fields).casefold()


def _slug_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise StudioError("bad_body", "skip and add must be arrays of stage slugs", details={"reason": "not_an_array"})
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
