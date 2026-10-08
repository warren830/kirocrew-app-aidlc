"""Advisor routes (§2.7): a second opinion that writes a draft and can change no action's status."""

from __future__ import annotations

from typing import Any

from aiohttp import web

from ..advisor import DraftRequest
from .common import json_ok, read_json, user_of


async def request_draft(services: Any, request: web.Request) -> web.Response:
    """202: the draft row is pollable, the analysis is not done (and may never be).

    ``auto`` is overwritten, never read from the body: it licenses the UI to pre-fill a question form, so a
    client that could claim it could pre-fill its own answers into a form the human then submits.
    """
    body = await read_json(request, required=("action_id", "kind"))
    draft = await services.advisor.request(DraftRequest.from_json({**body, "auto": False}), user=user_of(request))
    return json_ok({"ok": True, "draft": draft.to_json()}, status=202)


async def get_draft(services: Any, request: web.Request) -> web.Response:
    """Settled on read: the host reaps finished subagent runs, so a read is when the result can be collected."""
    return json_ok({"draft": (await services.advisor.poll(request.match_info["draft_id"])).to_json()})
