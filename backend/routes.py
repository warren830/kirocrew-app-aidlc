"""AI-DLC Studio — in-gateway route entry point.

KiroCrew loads this file by path (``spec_from_file_location``) and calls
``register_routes(ctx) -> list[AppRoute]``. It never touches ``sys.path``, and only gateway 0.5.0+
pre-registers the parent packages that would make ``from .studio import x`` resolve. So this module
registers Studio's own private namespace package first and imports the application through it
(architecture §4.0, C26). The result works identically on every gateway version and in the test suite.

The bootstrap block below is duplicated **verbatim** in ``backend/hooks.py``. That is deliberate: the
host loads the two files as two unrelated modules, and on 0.3.0 there is no parent package, so
``from .routes import bootstrap`` in ``hooks.py`` would raise ``ModuleNotFoundError`` before the app
ever gets to answer a request. Two copies of twenty lines is the price of not depending on a package
the loader may not have created.

The block is also *self-healing*, which is the second failure this file exists to prevent. The host
rolls a failed module load back by deleting the ``_kirocrew_app_*`` keys it created — it cannot see
``_aidlc_studio_backend``, because Studio invented that name. So an import that dies half-way (a
syntax error saved during development, a partial `git` checkout) leaves a *partially initialised*
``_aidlc_studio_backend.studio`` in ``sys.modules`` of a gateway process that will keep running for
days: every later enable then gets that corpse back from ``import_module`` and fails with
``module '_aidlc_studio_backend.studio' has no attribute 'services'`` no matter how good the code on
disk now is. So the namespace is purged whenever an import raises, and a module that comes back
incomplete is purged and imported once more.

Everything else lives under ``backend/studio/``; this file stays thin so the seam the host owns is
easy to review.
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

# --- bootstrap (verbatim in backend/hooks.py) ------------------------------- #

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


def register_routes(ctx):  # noqa: ANN001, ANN201 - host contract, typed via the import below
    """Host contract: build the app's services and return its route table.

    Deliberately does almost nothing: a failure here costs the app all of its routes (the gateway logs
    it and marks the app degraded rather than failing the enable), so the only work done is wiring the
    service container, whose one disk read is the bundled payload manifest. The store is opened by
    ``on_startup``, off the enable path, because a locked or corrupt SQLite file must degrade the app
    rather than delete its ``/health`` endpoint.
    """
    from kiro_crew.apps.route_registry import AppRoute  # local import: host module, always present

    services = studio.services.get_or_build(ctx)
    routes = studio.handlers.common.all_routes(services)
    bad = [r for r in routes if not isinstance(r, AppRoute)]
    if bad:  # pragma: no cover - guarded by tests
        raise TypeError(f"all_routes returned {len(bad)} non-AppRoute entries")
    ctx.logger.info(
        "aidlc-studio: %d routes registered (bundled AI-DLC %s)",
        len(routes),
        getattr(services.payload, "engine_version", "unavailable"),
    )
    return routes
