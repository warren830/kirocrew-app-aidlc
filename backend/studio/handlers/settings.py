"""Settings and the bun tool location (§2.8).

Every mutation here is a policy change, never a decision that reaches AI-DLC, so the owner gate is the
whole authorisation story and validation stays in ``SettingsService`` rather than being written twice.
"""

from __future__ import annotations

import asyncio
from typing import Any

from aiohttp import web

from ..errors import StudioError
from .common import json_ok, read_json, route


async def get_settings(services: Any, request: web.Request) -> web.Response:
    """``GET /settings`` — values, capabilities and versions in one read (§1.21)."""
    return json_ok((await services.settings.get()).to_json())


async def put_settings(services: Any, request: web.Request) -> web.Response:
    """``PUT /settings`` — deep-merge a partial patch, or store none of it."""
    settings = await services.settings.put(await read_json(request))
    await services.events.publish("settings.updated", {"updated_at": settings.updated_at})
    return json_ok(settings.to_json())


async def get_bun(services: Any, request: web.Request) -> web.Response:
    return json_ok({"tool": await asyncio.to_thread(services.bun_tool)})


async def put_bun(services: Any, request: web.Request) -> web.Response:
    body = await read_json(request)
    if "path" not in body or (body["path"] is not None and not isinstance(body["path"], str)):
        raise StudioError("bad_body", "path must be an absolute path or null")
    tool = await asyncio.to_thread(services.configure_bun, body["path"])
    await services.events.publish("settings.updated", {"tool": "bun"})
    return json_ok({"tool": tool})


async def reprobe_bun(services: Any, request: web.Request) -> web.Response:
    await read_json(request)
    tool = await asyncio.to_thread(services.reprobe_bun)
    await services.events.publish("settings.updated", {"tool": "bun"})
    return json_ok({"tool": tool})


def ROUTES(services: Any) -> list[Any]:
    return [
        route("GET", "/settings", get_settings, services=services),
        route("PUT", "/settings", put_settings, owner=True, services=services),
        route("GET", "/tools/bun", get_bun, services=services),
        route("PUT", "/tools/bun", put_bun, owner=True, services=services),
        route("POST", "/tools/bun/probe", reprobe_bun, owner=True, services=services),
    ]
