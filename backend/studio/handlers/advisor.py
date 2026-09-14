"""Advisor routes (§2.7) — an optional second opinion, never an authority.

The advisor reads evidence and writes a draft; it cannot submit, approve or answer anything. Both
routes reflect that: ``POST /advisor/draft`` answers ``202`` because the agent has not finished (and
may never), and ``GET`` polls. Neither can change an action's status.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from ..advisor import DraftRequest
from .common import json_ok, read_json, route


async def request_draft(services: Any, request: web.Request) -> web.Response:
    """``POST /advisor/draft`` — record the draft, then spawn the app's own read-only agent.

    202, not 200: the row exists and is pollable, the analysis does not. Answering 200 would invite a
    client to treat the empty ``result`` as "the advisor had nothing to say".
    """
    body = await read_json(request, required=("action_id", "kind"))
    # ``auto`` is stripped, never read from a body: only the reconciler builds an automatic request
    # (§1.18/§2.7), and Activity distinguishes the two kinds. The flag is what licenses the UI to
    # pre-fill a question form nobody asked it to, so a client that could claim it could pre-fill its
    # own answers into a form the human then submits.
    draft = await services.advisor.request(
        DraftRequest.from_json({**body, "auto": False}), user=str(request.get("user") or "")
    )
    return json_ok({"ok": True, "draft": draft.to_json()}, status=202)


async def get_draft(services: Any, request: web.Request) -> web.Response:
    """``GET /advisor/drafts/{draft_id}`` — poll the host subagent and settle the draft if it can be.

    Polled on read rather than pushed: the host's subagent manager reaps finished runs, so the only
    moment Studio can reliably collect a result is when somebody asks for it.
    """
    draft = await services.advisor.poll(request.match_info["draft_id"])
    return json_ok({"draft": draft.to_json()})


def ROUTES(services: Any) -> list[Any]:
    return [
        route("POST", "/advisor/draft", request_draft, owner=True, services=services),
        route("GET", "/advisor/drafts/{draft_id}", get_draft, services=services),
    ]
