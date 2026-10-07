"""Import the backend the way KiroCrew 0.8 does, for scripts that read its constants.

The 0.8 loader puts an app's backend under ``_kirocrew_app_<name>.backend`` after registering the parent
packages; doing the same here means a script sees exactly the module objects (and ``APP_NAME``) the
gateway would.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent


def studio(module: str | None = None) -> ModuleType:
    name = json.loads((ROOT / "app.json").read_text("utf-8"))["name"]
    host = f"_kirocrew_app_{name}"
    for ns, directory in ((host, ROOT), (f"{host}.backend", ROOT / "backend")):
        if ns not in sys.modules:
            pkg = importlib.util.module_from_spec(importlib.machinery.ModuleSpec(ns, None, is_package=True))
            pkg.__path__ = [str(directory)]  # type: ignore[attr-defined]
            sys.modules[ns] = pkg
    return importlib.import_module(f"{host}.backend.studio" + (f".{module}" if module else ""))
