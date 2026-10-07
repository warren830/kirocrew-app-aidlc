"""AI-DLC Studio — in-gateway route entry point (``backend.hooks.routes``).

KiroCrew 0.8 loads this file as ``_kirocrew_app_<name>.backend.routes`` and registers the parent
packages first, so ``from . import studio`` resolves inside the app's own tree and ``backend/hooks.py``
gets the same module objects. On disable the host drops every ``_kirocrew_app_<name>`` key, and it
rolls back a load that fails half-way, so a re-enable always reads the files on disk again.
"""

from __future__ import annotations

from . import studio


def register_routes(ctx):  # noqa: ANN001, ANN201 - host contract
    """Build the service container and return the route table.

    Does almost nothing on purpose: a failure here costs the app every route, ``/health`` included.
    The store is opened by ``on_startup``, so a locked database degrades the app instead.
    """
    from kiro_crew.apps.route_registry import AppRoute  # host module, always present in the gateway

    services = studio.services.get_or_build(ctx)
    routes = studio.handlers.common.all_routes(services)
    bad = [r for r in routes if not isinstance(r, AppRoute)]
    if bad:  # pragma: no cover - guarded by tests
        raise TypeError(f"all_routes returned {len(bad)} non-AppRoute entries")
    ctx.logger.info("%d routes registered (bundled AI-DLC %s)", len(routes),
                    getattr(services.payload, "engine_version", "unavailable"))
    return routes
