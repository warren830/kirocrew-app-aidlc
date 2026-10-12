"""How the gateway loads this app, driven through KiroCrew 0.8's own loader.

Each scenario runs in a subprocess with the real ``kiro_crew.apps.module_loader`` (``sys.modules`` is process-wide,
so loading the backend twice here would corrupt every other test's modules); only the loader's trust gate is opened.
Pinned: ``routes.py`` and ``hooks.py`` link and share one ``studio`` package from this repository under the host's
per-app namespace; a failed import leaves nothing resident and the next load works; enable stays cheap
(``register_routes`` opens no store, ``on_startup`` returns inside the budget); disable really unloads, and a
re-enable builds fresh module objects from disk.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import conftest as CT

APP_ROOT = Path(__file__).resolve().parent.parent
BACKEND = APP_ROOT / "backend"
HOST_NS = CT.HOST_NS

_PRELUDE = r'''
import asyncio, json, sys, time
from pathlib import Path

APP_ROOT = Path(sys.argv[1])
APP = json.loads((APP_ROOT / "app.json").read_text("utf-8"))["name"]
HOST_NS = "_kirocrew_app_" + APP

from kiro_crew.apps import module_loader as ML
ML.app_execution_denied = lambda *a, **k: None      # the test home trusts nobody; the gate is not under test


def keys():
    return sorted(k for k in sys.modules if k == HOST_NS or k.startswith(HOST_NS + "."))


def load():
    started = time.monotonic()
    register = ML.load_app_module(APP, APP_ROOT, "backend.routes:register_routes")
    startup = ML.load_app_module(APP, APP_ROOT, "backend.hooks:on_startup")
    shutdown = ML.load_app_module(APP, APP_ROOT, "backend.hooks:on_shutdown")
    return register, startup, shutdown, time.monotonic() - started
'''

_LINK = _PRELUDE + r'''
register, startup, shutdown, secs = load()
routes, hooks = sys.modules[HOST_NS + ".backend.routes"], sys.modules[HOST_NS + ".backend.hooks"]
studio = routes.studio
print("@@REPORT@@" + json.dumps({
    "callables": [callable(register), callable(startup), callable(shutdown)], "import_secs": secs,
    "same_studio_object": routes.studio is hooks.studio, "studio_file": studio.__file__,
    "studio_attrs": sorted(a for a in dir(studio) if not a.startswith("_")),
    "packages": sorted({getattr(sys.modules[k], "__package__", None) for k in keys()
                        if k.startswith(HOST_NS + ".backend.studio.")}),
    "app_name": studio.constants.APP_NAME,
}))
'''

_BROKEN = _PRELUDE + r'''
import importlib.abc


class Refuse(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == HOST_NS + ".backend.studio.storage":
            raise ImportError("simulated broken module")
        return None


sys.meta_path.insert(0, Refuse())
raised = None
try:
    load()
except ImportError as exc:
    raised = type(exc).__name__
left = keys()
sys.meta_path.pop(0)
register, *_ = load()
print("@@REPORT@@" + json.dumps({"raised": raised, "left": left, "reloaded": callable(register)}))
'''

_LIFECYCLE = _PRELUDE + r'''
from kiro_crew.apps.app_storage import AppStorage
from kiro_crew.apps.context import AppContext

data_dir = Path(sys.argv[2]) / "appdata"
data_dir.mkdir(parents=True, exist_ok=True)
manifest = json.loads((APP_ROOT / "app.json").read_text("utf-8"))


class Events:
    def __init__(self):
        self.published = []

    def publish(self, event_type, data=None):
        self.published.append((event_type, data))

    publish_to_app = publish


def context(config):
    return AppContext(name=APP, data_dir=data_dir, config=config, storage=AppStorage(APP, data_dir), events=Events())


register, startup, shutdown, _ = load()
db = data_dir / "studio.sqlite3"
ctx = context(dict(manifest.get("extra", {})))
route_list = register(ctx)
report = {"route_count": len(route_list), "store_opened_by_register_routes": db.exists(),
          "first_route": [route_list[0].method, route_list[0].path]}
started = time.monotonic()
asyncio.run(startup(ctx))                         # the host passes the routes' own context
report["startup_secs"] = time.monotonic() - started
report["store_opened_by_startup"] = db.exists()
studio_before = sys.modules[HOST_NS + ".backend.studio"]
asyncio.run(shutdown(context({})))
ML.unload_app_modules(APP)
report["left_after_unload"] = keys()
register, *_ = load()
report["reenable_rebuilt_the_package"] = sys.modules[HOST_NS + ".backend.studio"] is not studio_before
report["reenable_has_services"] = hasattr(sys.modules[HOST_NS + ".backend.studio"], "services")
print("@@REPORT@@" + json.dumps(report))
'''


def _run(script: str, *args: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AIDLC_", "CLAUDE_"))}
    env["KIROCREW_HOME"] = str(Path(args[0]) if args else Path(tempfile.mkdtemp(prefix="loader-home-")))
    env.pop("PYTHONPATH", None)
    done = subprocess.run([sys.executable, "-c", script, str(APP_ROOT), *args], capture_output=True, text=True,
                          timeout=240, env=env, cwd=str(APP_ROOT.parent))  # never the app root: no reliance on cwd
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    line = [ln for ln in done.stdout.splitlines() if ln.startswith("@@REPORT@@")][-1]
    return json.loads(line[len("@@REPORT@@"):])


@pytest.fixture(scope="module")
def linked() -> dict:
    return _run(_LINK)


@pytest.fixture(scope="module")
def lifecycle(tmp_path_factory) -> dict:
    return _run(_LIFECYCLE, str(tmp_path_factory.mktemp("loader-home")))


def test_the_real_loader_links_every_hook_to_one_shared_studio_package(linked):
    assert linked["callables"] == [True, True, True]
    assert linked["same_studio_object"] is True  # two packages would mean two Services registries


def test_the_app_is_loaded_from_this_repository_inside_the_hosts_per_app_namespace(linked):
    assert linked["studio_file"] == str(BACKEND / "studio" / "__init__.py")
    assert {"constants", "services", "handlers", "storage", "actions"} <= set(linked["studio_attrs"])
    assert linked["packages"], linked
    assert all(p.startswith(f"{HOST_NS}.backend.studio") for p in linked["packages"]), linked["packages"]
    assert linked["app_name"] == CT.APP_NAME  # the name comes from the namespace the host chose


def test_importing_the_backend_is_fast_enough_for_the_enable_path(linked):
    assert linked["import_secs"] < 5.0, linked["import_secs"]


def test_a_failed_import_leaves_nothing_behind_and_the_next_load_works():
    result = _run(_BROKEN)
    assert result["raised"] == "ImportError"
    assert result["left"] == [], result["left"]
    assert result["reloaded"] is True


def test_register_routes_returns_the_whole_table_and_does_not_open_the_store(lifecycle, studio):
    common = CT.studio_module("handlers.common")
    assert lifecycle["route_count"] == len(common.ROUTE_ORDER)
    assert lifecycle["first_route"] == list(common.ROUTE_ORDER[0])
    assert lifecycle["store_opened_by_register_routes"] is False and lifecycle["store_opened_by_startup"] is True


def test_on_startup_returns_well_inside_the_host_budget(lifecycle, studio):
    assert lifecycle["startup_secs"] < studio.constants.HOST_STARTUP_HOOK_BUDGET_SECS, lifecycle["startup_secs"]


def test_disable_unloads_everything_and_a_re_enable_reads_the_files_again(lifecycle):
    assert lifecycle["left_after_unload"] == [], lifecycle["left_after_unload"]
    assert lifecycle["reenable_rebuilt_the_package"] is True and lifecycle["reenable_has_services"] is True
