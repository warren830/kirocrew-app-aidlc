"""AI-DLC Studio — gateway lifecycle hooks.

``on_startup`` runs when the app is enabled and at every gateway boot, with the SAME ``AppContext`` the
routes were registered with. ``on_shutdown`` runs on disable, uninstall or trust withdrawal with a FRESH
context, so the service container is looked up by app name rather than by context identity.

The host bounds an async ``on_startup`` at 30 seconds and keeps a task that overruns it, refusing later
lifecycle operations meanwhile. Startup therefore does only bounded work and hands anything
long-running to a background task that ``on_shutdown`` cancels. Unloading the modules is the host's
job (``unload_app_modules`` on disable), so a re-enable runs the code on disk.
"""

from __future__ import annotations

from . import studio


async def on_startup(ctx) -> None:  # noqa: ANN001 - host contract
    await studio.services.get_or_build(ctx).start()


async def on_shutdown(ctx) -> None:  # noqa: ANN001 - host contract
    services = studio.services.discard(getattr(ctx, "name", studio.constants.APP_NAME))
    if services is not None:
        await services.stop()
