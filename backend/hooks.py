"""AI-DLC Studio — gateway lifecycle hooks.

``on_startup`` runs once when the app is enabled and again at every gateway boot, with the SAME
``AppContext`` the routes were registered with. ``on_shutdown`` runs on disable, uninstall or trust
withdrawal with a FRESH context object, so the service container is looked up by app name rather than
by context identity.

The host bounds an async ``on_startup`` at 30 seconds and, on expiry, keeps the task running while
refusing later lifecycle operations for this app. Startup therefore does only bounded work
(reconciliation of durable records, payload verification) and hands anything long-running to a
background task that ``on_shutdown`` cancels.

The bootstrap block is a verbatim copy of the one in ``backend/routes.py`` — see that file for why it
is duplicated rather than imported (the 0.3.0 loader registers no parent package, so a relative
import at the ``backend/`` level cannot resolve) and why it purges its own namespace on failure.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType

logger = logging.getLogger("kirocrew.app.aidlc-studio")

# --- bootstrap (verbatim in backend/routes.py) ------------------------------ #

_BACKEND_DIR = Path(__file__).resolve().parent
#: Private top-level name for this app's modules. Dot-free by construction (the host's app-name
#: grammar admits only ``[a-z0-9-]``), so it can never become a prefix of another app's namespace.
_NS = "_aidlc_studio_backend"
#: Attributes ``backend/studio/__init__.py`` binds last. Their absence means the package object in
#: ``sys.modules`` is a half-executed leftover, not a working import.
_REQUIRED = ("constants", "services", "handlers")


def teardown_namespace() -> None:
    """Drop this app's synthetic modules so the next import re-reads the files from disk.

    Called on disable (so editing the backend and re-enabling runs the new code) and after any failed
    import (so one bad save cannot poison every later enable in a long-lived gateway process).
    """
    prefix = f"{_NS}."
    for key in [k for k in sys.modules if k == _NS or k.startswith(prefix)]:
        sys.modules.pop(key, None)


def bootstrap() -> ModuleType:
    """Make ``backend/studio`` importable and return it, or raise having left nothing behind.

    Idempotent, so ``routes.py`` and ``hooks.py`` (which the host loads as two separate modules) share
    one set of module objects and therefore one ``Services`` instance.
    """
    for final in (False, True):
        root = sys.modules.get(_NS)
        if root is not None and list(getattr(root, "__path__", ())) != [str(_BACKEND_DIR)]:
            teardown_namespace()  # another install generation owns the name; ours is authoritative
            root = None
        if root is None:
            spec = importlib.machinery.ModuleSpec(_NS, None, is_package=True)
            root = importlib.util.module_from_spec(spec)
            root.__path__ = [str(_BACKEND_DIR)]  # type: ignore[attr-defined]
            sys.modules[_NS] = root
        try:
            studio = importlib.import_module(f"{_NS}.studio")
        except BaseException:
            teardown_namespace()
            raise
        missing = [name for name in _REQUIRED if not hasattr(studio, name)]
        if not missing:
            return studio
        teardown_namespace()
        if final:
            raise ImportError(
                f"{_NS}.studio imported without {missing} — the package is incomplete on disk"
            )
    raise AssertionError("unreachable")  # pragma: no cover


studio = bootstrap()

# --- end bootstrap ---------------------------------------------------------- #


async def on_startup(ctx) -> None:  # noqa: ANN001 - host contract
    services = studio.services.get_or_build(ctx)
    await services.start()


async def on_shutdown(ctx) -> None:  # noqa: ANN001 - host contract
    """Stop the container and unload this app's modules, whatever the container does on the way out.

    The unload is in a ``finally`` because it is the only thing that makes a re-enable read the new
    code: a container that raises while closing its store would otherwise leave the old modules
    resident, and every later enable would run the code the developer just replaced.
    """
    name = getattr(ctx, "name", studio.constants.APP_NAME)
    try:
        services = studio.services.discard(name)
        if services is not None:
            await services.stop()
    finally:
        teardown_namespace()
