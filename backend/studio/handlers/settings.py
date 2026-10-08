"""Settings and the bun location (§2.8).

Policy changes, never decisions that reach AI-DLC: the owner gate is the whole authorisation story, and
validation stays in ``SettingsService`` and ``Services.configure_bun`` rather than being written twice. The
current bun location is read from ``/health`` (``tools.bun``).
"""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

from ..errors import StudioError
from .common import json_ok, read_json


async def get_settings(services: Any, request: web.Request) -> web.Response:
    return json_ok((await services.settings.get()).to_json())


async def put_settings(services: Any, request: web.Request) -> web.Response:
    """A partial patch, deep-merged, or none of it stored."""
    settings = await services.settings.put(await read_json(request))
    await services.events.publish("settings.updated", {"updated_at": settings.updated_at})
    return json_ok(settings.to_json())


async def put_bun(services: Any, request: web.Request) -> web.Response:
    """``{"path": str}`` pins a binary (validated before it is stored); ``{"path": null}`` searches again."""
    body = await read_json(request)
    if "path" not in body or (body["path"] is not None and not isinstance(body["path"], str)):
        raise StudioError("bad_body", "path must be an absolute path or null")
    return await _bun_changed(services, await asyncio.to_thread(services.configure_bun, body["path"]))


async def probe_bun(services: Any, request: web.Request) -> web.Response:
    """Re-run the stored choice (pinned path or search) without changing it."""
    await read_json(request)
    return await _bun_changed(services, await asyncio.to_thread(services.reprobe_bun))


async def _bun_changed(services: Any, tool: dict[str, Any]) -> web.Response:
    await services.events.publish("settings.updated", {"tool": "bun"})
    return json_ok({"tool": tool})
