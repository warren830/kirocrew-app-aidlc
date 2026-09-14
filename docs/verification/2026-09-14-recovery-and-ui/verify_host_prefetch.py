"""Run isolated real-resolver regressions against an explicitly selected package.

--patch-in-memory adapts only the installed _eager_spawn function in this process.
It does not change installed files, start a gateway, or send a chat prompt.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib
import inspect
import os
from pathlib import Path
import sys
import tempfile


def patched_eager_source(source: str) -> str:
    resolve = "resolve_agent_bindings(cfg, slot.agent or None)"
    recovery = "_recover_app_agent_binding(cfg, slot, project=None)"
    if source.count(resolve) != 2 or source.count(recovery) != 1:
        raise RuntimeError("unexpected function shape; refusing to guess an in-memory patch")
    if "await warm_project_agent_names(slot.project)" in source:
        raise RuntimeError("project warming already present; review this runtime before patching")
    changed = source.replace(
        resolve, "resolve_agent_bindings(cfg, slot.agent or None, slot.project or None)"
    ).replace(
        recovery, "_recover_app_agent_binding(cfg, slot, project=slot.project or None)"
    )
    first = next(
        line for line in changed.splitlines(keepends=True)
        if "bindings = resolve_agent_bindings(cfg, slot.agent or None, slot.project or None)" in line
    )
    indent = first[: len(first) - len(first.lstrip())]
    return changed.replace(
        first, indent + "await warm_project_agent_names(slot.project)\n" + first, 1
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", required=True, type=Path, help="src or site-packages")
    parser.add_argument("--tests", required=True, type=Path, help="path to test_eager_spawn.py")
    parser.add_argument("--patch-in-memory", action="store_true")
    args = parser.parse_args()
    package_root = args.package_root.resolve(strict=True)
    tests = args.tests.resolve(strict=True)
    expected = package_root / "kiro_crew" / "dashboard" / "chat_runner.py"
    original_bytes = expected.read_bytes()
    original_digest = hashlib.sha256(original_bytes).hexdigest()
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PYTHONPATH"] = str(package_root)
    os.environ["KIROCREW_EAGER_TEST_SOURCE"] = str(expected)
    os.environ.pop("KIROCREW_PROJECT_DIR", None)
    sys.path.insert(0, str(package_root))

    with tempfile.TemporaryDirectory(prefix="aidlc-eager-verification-") as home:
        os.environ["KIROCREW_HOME"] = home
        os.environ["XDG_CONFIG_HOME"] = str(Path(home) / "xdg")
        runner = importlib.import_module("kiro_crew.dashboard.chat_runner")
        loader = importlib.import_module("kiro_crew.config.loader")
        assert Path(runner.__file__).resolve() == expected
        assert Path(loader.__file__).resolve() == package_root / "kiro_crew/config/loader.py"
        assert runner.resolve_agent_bindings is loader.resolve_agent_bindings
        print(f"Verified chat_runner: {runner.__file__}", flush=True)
        print(f"Verified real resolver: {loader.__file__}", flush=True)
        print(f"Source SHA256 before: {original_digest}", flush=True)

        try:
            if args.patch_in_memory:
                source = inspect.getsource(runner._eager_spawn)
                changed = patched_eager_source(source)
                print("In-memory function patch:", flush=True)
                print("".join(difflib.unified_diff(
                    source.splitlines(keepends=True),
                    changed.splitlines(keepends=True),
                    fromfile="installed/_eager_spawn",
                    tofile="in-memory/_eager_spawn",
                )), flush=True)
                exec(compile(changed, str(expected), "exec"), runner.__dict__)

            import pytest

            return int(pytest.main([
                str(tests), "-k", "TestEagerSpawnProjectBindings",
                "-n0", "-q", "-o", f"pythonpath={package_root}",
                "--tb=short", "--color=no",
            ]))
        finally:
            unchanged = expected.read_bytes() == original_bytes
            print(f"Installed/selected source unchanged: {unchanged}", flush=True)
            assert unchanged, "selected source changed during verification"


if __name__ == "__main__":
    raise SystemExit(main())
