"""Settings, calibration history and the one-time prototype migration (§2.8).

Three groups of routes that share one property: each mutation is a *policy* change, never a decision
that reaches AI-DLC. So the owner gate is the whole authorisation story here, and every refusal comes
from the module that owns the invariant (``SettingsService`` for locked capabilities,
``MigrationService`` for a registry that already moved) rather than from a check written twice.
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any

from aiohttp import web

from .. import constants as C
from ..errors import StudioError
from .common import json_ok, read_json, route


async def get_settings(services: Any, request: web.Request) -> web.Response:
    """``GET /settings`` — values, capabilities and versions in one read (§1.21)."""
    return json_ok((await services.settings.get()).to_json())


async def put_settings(services: Any, request: web.Request) -> web.Response:
    """``PUT /settings`` — deep-merge a partial patch, or store none of it.

    All-or-nothing is the service's rule, not the handler's: a partial write behind a 4xx would leave
    the user unable to tell what Studio kept.
    """
    patch = await read_json(request)
    settings = await services.settings.put(patch)
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


async def get_calibration(services: Any, request: web.Request) -> web.Response:
    """``GET /calibration`` — cohort sample counts only.

    Never names an intent: rows are keyed by ``sha256(uuid)[:12]`` (C17), so a shared bundle of
    calibration history carries no project names or paths.
    """
    cohorts = await asyncio.to_thread(services.estimates.cohorts)
    return json_ok(
        {
            "cohorts": cohorts,
            "total": sum(int(row.get("samples") or 0) for row in cohorts),
            "min_samples": int(C.CALIBRATION_MIN_SAMPLES),
        }
    )


async def clear_calibration(services: Any, request: web.Request) -> web.Response:
    """``POST /calibration/clear`` — drop every sample, on an explicit confirmation.

    ``confirm`` is required because the samples cannot be recovered: they are observations of turns
    that already happened, and there is nowhere to read them back from.
    """
    body = await read_json(request)
    if body.get("confirm") is not True:
        raise StudioError(
            "invalid_decision",
            "clearing calibration history requires confirm: true",
            details={"reason": "confirm_required"},
        )
    removed = await asyncio.to_thread(services.estimates.clear)
    await services.activity.record(
        kind="calibration.cleared", severity="attention", params={"removed": removed}
    )
    return json_ok({"ok": True, "removed": removed})


async def migration_status(services: Any, request: web.Request) -> web.Response:
    """``GET /migration/status`` — whether the prototype registry has been taken over."""
    return json_ok(await asyncio.to_thread(services.migration.status))


async def migration_preview(services: Any, request: web.Request) -> web.Response:
    """``POST /migration/preview`` — what would move, writing nothing.

    A POST rather than a GET even though it changes nothing: it stats every registered path in the
    prototype's registry, which is real filesystem work on paths the user has not vouched for, and the
    owner gate is what keeps that behind an explicit action.
    """
    preview = await asyncio.to_thread(services.migration.preview)
    return json_ok({"preview": preview.to_json()})


async def migration_apply(services: Any, request: web.Request) -> web.Response:
    """``POST /migration/apply`` — migrate once, against the digest the user was shown.

    The digest is the whole safety argument: it makes "the preview the user confirmed" and "the bytes
    that get migrated" the same read, so a registry edited in between is refused rather than silently
    migrated.
    """
    body = await read_json(request, required=("source_sha256",))
    if body.get("confirm") is not True:
        raise StudioError(
            "invalid_decision",
            "applying the migration requires confirm: true",
            details={"reason": "confirm_required"},
        )
    result = await asyncio.to_thread(
        functools.partial(services.migration.apply, expect_sha256=str(body["source_sha256"]))
    )
    await services.events.publish("migration.updated", result.to_json())
    return json_ok({"ok": True, "result": result.to_json()})


def ROUTES(services: Any) -> list[Any]:
    return [
        route("GET", "/settings", get_settings, services=services),
        route("PUT", "/settings", put_settings, owner=True, services=services),
        route("GET", "/tools/bun", get_bun, services=services),
        route("PUT", "/tools/bun", put_bun, owner=True, services=services),
        route("POST", "/tools/bun/probe", reprobe_bun, owner=True, services=services),
        route("GET", "/calibration", get_calibration, services=services),
        route("POST", "/calibration/clear", clear_calibration, owner=True, services=services),
        route("GET", "/migration/status", migration_status, services=services),
        route("POST", "/migration/preview", migration_preview, owner=True, services=services),
        route("POST", "/migration/apply", migration_apply, owner=True, services=services),
    ]
