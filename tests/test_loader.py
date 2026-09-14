"""How the gateway loads this app — both loader generations, and the way out again (§4.3, C26).

This is the only file that can fail *before* any other test is meaningful: if the two entry points do
not link, the app has no routes, no hooks and no ``/health``, and the gateway reports it as degraded
with an ``ImportError`` that names an internal module nobody outside this repository can read.

Four properties, each one a real failure that was observed rather than imagined:

1. **Both loader generations link.** 0.3.0 registers *no* parent packages before ``exec_module``, so a
   relative import at the ``backend/`` level (``from .routes import bootstrap`` in ``hooks.py``) raises
   ``ModuleNotFoundError`` there while working perfectly on 0.5.0 — a bug that is invisible on the
   developer's own machine. 0.5.0 pre-registers three namespace packages, and the result must be
   identical (review P19/R05).
2. **A poisoned namespace heals.** The host rolls a failed load back by deleting the
   ``_kirocrew_app_*`` keys *it* created; it cannot see ``_aidlc_studio_backend``, which Studio
   invented. One import that died half-way therefore leaves a partially initialised
   ``_aidlc_studio_backend.studio`` in a gateway process that will keep running for days, and every
   later enable fails with ``has no attribute 'services'`` no matter what is on disk. This was the live
   failure; the bootstrap now purges its own namespace on the way out of a failed import.
3. **Disable really unloads.** ``on_shutdown`` must drop every ``_aidlc_studio_backend*`` key, or
   editing the backend and re-enabling silently keeps running the old code — and the host's own
   ``unload_app_modules`` cannot help, because it only knows the ``_kirocrew_app_aidlc-studio.`` prefix.
4. **Enable stays cheap.** ``register_routes`` must not open the store or walk a disk (the gateway
   treats an exception there as "this app has no routes"), and ``on_startup`` must return well inside
   the host's 30 s budget.

Every loader emulation runs in a **subprocess**. ``sys.modules`` is process-global: a test that loaded
the backend a second way in this interpreter would corrupt the session-scoped module objects every
other test file shares, and a test that ran ``on_shutdown`` would delete them outright.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent
BACKEND = APP_ROOT / "backend"
HOST_NS = "_kirocrew_app_aidlc-studio"
STUDIO_NS = "_aidlc_studio_backend"

#: The bundle interpreter the desktop app ships. The import smoke test in §4 runs against it because it
#: is a different Python build with a frozen stdlib — an import that works in the dev venv and not there
#: would only show up on a user's machine.
BUNDLE_PYTHON = Path(
    "/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12"
)


# --------------------------------------------------------------------------- #
# the two loader emulations, as a script run in a clean interpreter
# --------------------------------------------------------------------------- #

#: Emulates ``kiro_crew.apps.module_loader.load_app_module`` for one generation and prints a JSON
#: report. ``mode`` decides whether the ancestor namespace packages exist before ``exec_module``, which
#: is the whole difference between 0.3.0 and 0.5.0 (kc:apps/module_loader.py `_ensure_namespace_packages`).
_LOADER = r'''
import importlib, importlib.machinery, importlib.util, json, sys, time
from pathlib import Path

APP_ROOT = Path(sys.argv[1])
MODE = sys.argv[2]
HOST_NS = "_kirocrew_app_aidlc-studio"


def ensure_namespace_packages():
    """What the 0.5.0 loader does before exec_module; the 0.3.0 loader does nothing."""
    for name, directory in (
        (HOST_NS, APP_ROOT),
        (f"{HOST_NS}.backend", APP_ROOT / "backend"),
        (f"{HOST_NS}.backend.studio", APP_ROOT / "backend" / "studio"),
    ):
        if name in sys.modules:
            continue
        pkg = importlib.util.module_from_spec(
            importlib.machinery.ModuleSpec(name, None, is_package=True)
        )
        pkg.__path__ = [str(directory)]
        sys.modules[name] = pkg


def load(rel, dotted):
    spec = importlib.util.spec_from_file_location(dotted, APP_ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[dotted] = module
    spec.loader.exec_module(module)
    parent, _, leaf = dotted.rpartition(".")
    if parent in sys.modules:                      # the loader mirrors normal import semantics
        setattr(sys.modules[parent], leaf, module)
    return module


if MODE == "0.5.0":
    ensure_namespace_packages()

started = time.monotonic()
routes = load("backend/routes.py", f"{HOST_NS}.backend.routes")
hooks = load("backend/hooks.py", f"{HOST_NS}.backend.hooks")
import_secs = time.monotonic() - started

report = {
    "mode": MODE,
    "import_secs": import_secs,
    "register_routes": callable(getattr(routes, "register_routes", None)),
    "on_startup": callable(getattr(hooks, "on_startup", None)),
    "on_shutdown": callable(getattr(hooks, "on_shutdown", None)),
    "same_studio_object": routes.studio is hooks.studio,
    "studio_attrs": sorted(a for a in dir(routes.studio) if not a.startswith("_")),
    "studio_file": getattr(routes.studio, "__file__", None),
    "studio_keys": sorted(k for k in sys.modules if k.startswith("_aidlc_studio_backend")),
    "host_keys": sorted(k for k in sys.modules if k.startswith(HOST_NS)),
    "package_of": {
        name: getattr(sys.modules[f"_aidlc_studio_backend.studio.{name}"], "__package__", None)
        for name in ("constants", "services", "storage")
    },
}
EXTRA = sys.argv[3] if len(sys.argv) > 3 else ""
if EXTRA:
    exec(EXTRA, {"routes": routes, "hooks": hooks, "report": report, "sys": sys, "APP_ROOT": APP_ROOT})
print("@@REPORT@@" + json.dumps(report))
'''


def run_loader(mode: str, extra: str = "", *, python: str | None = None) -> dict:
    """Load the app in a fresh interpreter and return the report the script printed."""
    env = dict(os.environ)
    env["KIROCREW_HOME"] = str(APP_ROOT / "tests" / ".loader-home")
    env.pop("PYTHONPATH", None)
    done = subprocess.run(
        [python or sys.executable, "-c", _LOADER, str(APP_ROOT), mode, extra],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
        cwd=str(APP_ROOT.parent),  # never the app root: the loader must not rely on cwd
    )
    assert done.returncode == 0, f"{mode} load failed:\n{done.stdout}\n{done.stderr}"
    marker = [line for line in done.stdout.splitlines() if line.startswith("@@REPORT@@")]
    assert marker, f"no report:\n{done.stdout}\n{done.stderr}"
    return json.loads(marker[-1][len("@@REPORT@@") :])


@pytest.fixture(scope="module")
def report_030() -> dict:
    return run_loader("0.3.0")


@pytest.fixture(scope="module")
def report_050() -> dict:
    return run_loader("0.5.0")


# --------------------------------------------------------------------------- #
# both generations link
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mode", ["0.3.0", "0.5.0"])
def test_both_loader_generations_link_the_entry_points(mode):
    report = run_loader(mode)
    assert report["register_routes"] is True
    assert report["on_startup"] is True and report["on_shutdown"] is True


def test_the_two_generations_produce_the_same_studio_package(report_030, report_050):
    """0.5.0's pre-registered packages must change nothing: the bootstrap owns its own namespace."""
    assert report_030["studio_attrs"] == report_050["studio_attrs"]
    assert report_030["studio_file"] == report_050["studio_file"]
    assert report_030["studio_keys"] == report_050["studio_keys"]


def test_routes_and_hooks_share_one_studio_package(report_030, report_050):
    """Two module objects would mean two ``Services`` registries — routes on one, the lifecycle on the other."""
    assert report_030["same_studio_object"] is True
    assert report_050["same_studio_object"] is True


def test_the_studio_package_exposes_every_module_the_entry_points_need(report_030):
    assert {"constants", "services", "handlers", "storage", "actions"} <= set(report_030["studio_attrs"])


def test_the_app_is_loaded_from_the_repository_it_was_asked_for(report_030):
    assert report_030["studio_file"] == str(BACKEND / "studio" / "__init__.py")


def test_0_3_0_registers_no_host_parent_packages(report_030):
    """The emulation must actually be the harder case, or the test above proves nothing."""
    assert report_030["host_keys"] == [f"{HOST_NS}.backend.hooks", f"{HOST_NS}.backend.routes"]


def test_0_5_0_registers_the_three_host_parent_packages(report_050):
    assert f"{HOST_NS}.backend.studio" in report_050["host_keys"]
    assert f"{HOST_NS}.backend" in report_050["host_keys"]


def test_every_studio_module_belongs_to_the_apps_own_package(report_030):
    """``__package__`` is what a relative import resolves against; the host's name must not appear."""
    for name, package in report_030["package_of"].items():
        assert package == f"{STUDIO_NS}.studio", (name, package)


def test_importing_the_backend_is_fast_enough_for_the_enable_path(report_030, report_050):
    """The gateway imports both files inside the enable request; seconds here are a visible hang."""
    assert report_030["import_secs"] < 5.0, report_030["import_secs"]
    assert report_050["import_secs"] < 5.0, report_050["import_secs"]


# --------------------------------------------------------------------------- #
# a poisoned namespace heals (the live failure)
# --------------------------------------------------------------------------- #


def test_a_half_imported_namespace_from_an_earlier_failure_is_discarded():
    """The exact shape of the live bug: a stale partial package under Studio's own name.

    The host's rollback cannot reach ``_aidlc_studio_backend``, so the bootstrap must not trust a
    pre-existing entry. Without the purge, ``import_module`` hands back the corpse and
    ``register_routes`` dies on ``studio.services`` for the rest of the process's life.
    """
    poison = textwrap.dedent(
        """
        import importlib, importlib.machinery, importlib.util, sys
        from pathlib import Path
        APP_ROOT = Path(sys.argv[1])
        ns = "_aidlc_studio_backend"
        root = importlib.util.module_from_spec(
            importlib.machinery.ModuleSpec(ns, None, is_package=True))
        root.__path__ = [str(APP_ROOT / "backend")]
        sys.modules[ns] = root
        partial = importlib.util.module_from_spec(
            importlib.machinery.ModuleSpec(ns + ".studio", None, is_package=True))
        partial.__path__ = [str(APP_ROOT / "backend" / "studio")]
        partial.constants = "not the module"          # looks half-built, exactly like a failed import
        sys.modules[ns + ".studio"] = partial
        """
    )
    report = run_loader("0.3.0", extra="")
    assert report["register_routes"] is True

    # Same interpreter, but the poison is installed BEFORE the entry points load.
    env = dict(os.environ)
    env["KIROCREW_HOME"] = str(APP_ROOT / "tests" / ".loader-home")
    script = poison + "\n" + _LOADER
    done = subprocess.run(
        [sys.executable, "-c", script, str(APP_ROOT), "0.3.0"],
        capture_output=True, text=True, timeout=180, env=env, cwd=str(APP_ROOT.parent),
    )
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    line = [ln for ln in done.stdout.splitlines() if ln.startswith("@@REPORT@@")][-1]
    healed = json.loads(line[len("@@REPORT@@") :])
    assert healed["register_routes"] is True
    assert "services" in healed["studio_attrs"]
    assert healed["studio_file"] == str(BACKEND / "studio" / "__init__.py")


def test_a_failed_import_leaves_no_namespace_behind():
    """The other half of the same rule: if the import raises, nothing of ours may stay resident.

    Simulated by making one studio module unimportable through a stub package on ``sys.path`` — the
    bootstrap must propagate the error *and* leave ``sys.modules`` clean, so the next attempt (after the
    developer fixes the file) starts from disk.
    """
    script = textwrap.dedent(
        """
        import importlib, importlib.machinery, importlib.util, json, sys
        from pathlib import Path
        APP_ROOT = Path(sys.argv[1])

        # Break the package: a meta-path finder that refuses one submodule.
        class Refuse:
            def find_module(self, name, path=None):
                return None
            def find_spec(self, name, path=None, target=None):
                if name == "_aidlc_studio_backend.studio.storage":
                    raise ImportError("simulated broken module")
                return None
        sys.meta_path.insert(0, Refuse())

        spec = importlib.util.spec_from_file_location(
            "_kirocrew_app_aidlc-studio.backend.routes", APP_ROOT / "backend" / "routes.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        raised = None
        try:
            spec.loader.exec_module(module)
        except BaseException as exc:
            raised = type(exc).__name__
        left = sorted(k for k in sys.modules if k.startswith("_aidlc_studio_backend"))
        print("@@REPORT@@" + json.dumps({"raised": raised, "left": left}))
        """
    )
    env = dict(os.environ)
    env["KIROCREW_HOME"] = str(APP_ROOT / "tests" / ".loader-home")
    done = subprocess.run(
        [sys.executable, "-c", script, str(APP_ROOT)],
        capture_output=True, text=True, timeout=180, env=env, cwd=str(APP_ROOT.parent),
    )
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    line = [ln for ln in done.stdout.splitlines() if ln.startswith("@@REPORT@@")][-1]
    result = json.loads(line[len("@@REPORT@@") :])
    assert result["raised"] == "ImportError"
    assert result["left"] == [], result["left"]


# --------------------------------------------------------------------------- #
# the whole lifecycle in one process: enable, serve, disable
# --------------------------------------------------------------------------- #

_LIFECYCLE = r'''
import asyncio, importlib, importlib.machinery, importlib.util, json, sys, time
from pathlib import Path

APP_ROOT = Path(sys.argv[1])
HOST_NS = "_kirocrew_app_aidlc-studio"
sys.path.insert(0, str(APP_ROOT / "tests"))


def load(rel, dotted):
    spec = importlib.util.spec_from_file_location(dotted, APP_ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[dotted] = module
    spec.loader.exec_module(module)
    return module


routes = load("backend/routes.py", f"{HOST_NS}.backend.routes")
hooks = load("backend/hooks.py", f"{HOST_NS}.backend.hooks")

from kiro_crew.apps.app_storage import AppStorage
from kiro_crew.apps.context import AppContext

data_dir = Path(sys.argv[2]) / "appdata"
data_dir.mkdir(parents=True, exist_ok=True)
manifest = json.loads((APP_ROOT / "app.json").read_text("utf-8"))


class Events:
    def __init__(self): self.published = []
    def publish(self, event_type, data=None): self.published.append((event_type, data))
    def publish_to_app(self, event_type, data=None): self.published.append((event_type, data))


class Logger:
    def info(self, *a, **k): pass
    debug = warning = error = exception = info


ctx = AppContext(
    name="aidlc-studio", data_dir=data_dir, config=dict(manifest.get("extra", {})),
    storage=AppStorage("aidlc-studio", data_dir), events=Events(),
)
ctx.logger = Logger()

db = data_dir / "studio.sqlite3"
route_list = routes.register_routes(ctx)
report = {
    "route_count": len(route_list),
    "store_opened_by_register_routes": db.exists(),
    "first_routes": [(r.method, r.path) for r in route_list[:3]],
}

started = time.monotonic()
asyncio.run(hooks.on_startup(ctx))
report["startup_secs"] = time.monotonic() - started
report["store_opened_by_startup"] = db.exists()

fresh = AppContext(
    name="aidlc-studio", data_dir=data_dir, config={},
    storage=AppStorage("aidlc-studio", data_dir), events=Events(),
)
fresh.logger = Logger()
asyncio.run(hooks.on_shutdown(fresh))
report["studio_keys_after_shutdown"] = sorted(
    k for k in sys.modules if k.startswith("_aidlc_studio_backend"))
report["dangling_after_host_unload"] = sorted(
    k for k in sys.modules if k.startswith(HOST_NS + ".") or k == HOST_NS)

# A re-enable must read the files again: the module objects are new ones.
before = id(routes.studio)
routes2 = load("backend/routes.py", f"{HOST_NS}.backend.routes")
report["reenable_rebuilt_the_package"] = id(routes2.studio) != before
report["reenable_has_services"] = hasattr(routes2.studio, "services")
print("@@REPORT@@" + json.dumps(report))
'''


@pytest.fixture(scope="module")
def lifecycle(tmp_path_factory) -> dict:
    home = tmp_path_factory.mktemp("loader-home")
    data = tmp_path_factory.mktemp("loader-data")
    env = dict(os.environ)
    env["KIROCREW_HOME"] = str(home)
    for key in list(env):
        if key.startswith(("AIDLC_", "CLAUDE_")):
            env.pop(key)
    done = subprocess.run(
        [sys.executable, "-c", _LIFECYCLE, str(APP_ROOT), str(data)],
        capture_output=True, text=True, timeout=240, env=env, cwd=str(APP_ROOT.parent),
    )
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    line = [ln for ln in done.stdout.splitlines() if ln.startswith("@@REPORT@@")][-1]
    return json.loads(line[len("@@REPORT@@") :])


def test_register_routes_returns_the_whole_table(lifecycle, studio):
    from importlib import import_module

    common = import_module(f"{STUDIO_NS}.studio.handlers.common")
    assert lifecycle["route_count"] == len(common.ROUTE_ORDER)
    assert lifecycle["first_routes"][0] == list(common.ROUTE_ORDER[0])


def test_register_routes_does_not_open_the_store(lifecycle):
    """A locked or corrupt SQLite file must degrade the app, not cost it every route (§1.23)."""
    assert lifecycle["store_opened_by_register_routes"] is False
    assert lifecycle["store_opened_by_startup"] is True


def test_on_startup_returns_well_inside_the_host_budget(lifecycle, studio):
    """The host detaches an overrunning task and then refuses later lifecycle calls for this app."""
    budget = studio.constants.HOST_STARTUP_HOOK_BUDGET_SECS
    assert lifecycle["startup_secs"] < budget, lifecycle["startup_secs"]


def test_on_shutdown_removes_every_key_the_app_registered(lifecycle):
    assert lifecycle["studio_keys_after_shutdown"] == [], lifecycle["studio_keys_after_shutdown"]


def test_the_hosts_unload_prefix_leaves_nothing_dangling(lifecycle):
    """Everything left under the host's own prefix must be something the host itself will unload."""
    remaining = lifecycle["dangling_after_host_unload"]
    assert all(key.startswith(HOST_NS + ".") or key == HOST_NS for key in remaining), remaining
    assert not [key for key in remaining if key.startswith(STUDIO_NS)]


def test_a_re_enable_reads_the_files_from_disk_again(lifecycle):
    """This is what makes ``dev-install.sh`` honest: without it the gateway keeps the old code."""
    assert lifecycle["reenable_rebuilt_the_package"] is True
    assert lifecycle["reenable_has_services"] is True


# --------------------------------------------------------------------------- #
# the interpreter the desktop app actually ships
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not BUNDLE_PYTHON.exists(), reason="the KiroCrew bundle interpreter is not installed")
def test_the_bundle_interpreter_can_import_the_backend():
    """§4's import smoke: a frozen stdlib is a different import environment than the dev venv."""
    report = run_loader("0.5.0", python=str(BUNDLE_PYTHON))
    assert report["register_routes"] is True
    assert "services" in report["studio_attrs"]
