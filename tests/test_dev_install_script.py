"""Run the actual installer with isolated tools and a recording gateway boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "dev-install.sh"


BUNDLED_PY = Path(
    "/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3.12"
)


def run_installer(tmp_path: Path, *, quoted_checkout=False, build=False, installed=False, kc_py="path"):
    """``kc_py``: ``"path"`` sets KC_PY to the wrapper's path, ``"name"`` to a command name on PATH,
    ``None`` leaves it unset with the wrapper as ``python3`` on PATH."""
    root = tmp_path / ('checkout with "quotes"' if quoted_checkout else "checkout")
    (root / "scripts").mkdir(parents=True)
    (root / "ui").mkdir()
    (root / "ui/package.json").write_text("{}")
    shutil.copy2(INSTALLER, root / "scripts/dev-install.sh")
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("bash", "dirname"):
        executable = shutil.which(name)
        assert executable
        (tools / name).symlink_to(executable)
    python_dir = tmp_path / "python runtime"
    python_dir.mkdir()
    python_log = tmp_path / "python-calls"
    python = python_dir / "python3"
    python.write_text(
        "#!/bin/bash\n"
        f"printf 'called\\n' >> {shlex.quote(str(python_log))}\n"
        f'exec {shlex.quote(sys.executable)} "$@"\n'
    )
    python.chmod(0o755)
    if kc_py == "name":
        (tools / "python3-gateway").symlink_to(python)
    elif kc_py is None:
        (tools / "python3").symlink_to(python)
    requests = tmp_path / "requests.jsonl"
    api = root / "scripts/kcapi.sh"
    api.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "method, path = sys.argv[1:3]\n"
        "body = json.loads(sys.argv[3]) if len(sys.argv) > 3 else None\n"
        f"with Path({str(requests)!r}).open('a') as out:\n"
        "    out.write(json.dumps({'method': method, 'path': path, 'body': body,\n"
        "                          'kc_py': os.environ.get('KC_PY')}) + '\\n')\n"
        "if method == 'GET' and path == '/api/apps/aidlc-studio':\n"
        f"    print(json.dumps({{'name': 'aidlc-studio'}} if {installed!r} else {{}}))\n"
        "elif method == 'GET' and path == '/api/apps':\n"
        "    print(json.dumps([{'name': 'aidlc-studio', 'enabled': True}]))\n"
        "else:\n"
        "    print(json.dumps({'ok': True, 'status': 'healthy'}))\n"
    )
    api.chmod(0o755)
    env = os.environ.copy()
    env.update({"PATH": str(tools)})
    env.pop("KC_PY", None)
    if kc_py == "path":
        env["KC_PY"] = str(python)
    elif kc_py == "name":
        env["KC_PY"] = "python3-gateway"
    env.pop("NODE_BIN", None)
    result = subprocess.run(
        [str(tools / "bash"), str(root / "scripts/dev-install.sh"),
         *([] if build else ["--no-build"])],
        cwd=root, env=env, text=True, capture_output=True, timeout=15,
    )
    rows = [
        json.loads(line) for line in requests.read_text().splitlines()
    ] if requests.exists() else []
    return result, root, rows, python_log


def test_prebuilt_install_uses_configured_python_without_node(tmp_path):
    result, root, requests, python_log = run_installer(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert python_log.exists(), "KC_PY must also run the installer's JSON checks"
    assert len(python_log.read_text().splitlines()) >= 2
    installs = [r for r in requests if r["path"] == "/api/apps/install"]
    assert installs == [{
        "method": "POST", "path": "/api/apps/install", "body": {"source": str(root)},
        "kc_py": str(tmp_path / "python runtime" / "python3"),
    }]
    assert requests[-1]["path"] == "/api/apps/aidlc-studio/health"


def test_update_serializes_checkout_path_without_corrupting_json(tmp_path):
    result, root, requests, _ = run_installer(
        tmp_path, quoted_checkout=True, installed=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    mutations = [r for r in requests if r["method"] == "POST"]
    assert mutations[0]["path"] == "/api/apps/aidlc-studio/disable"
    assert {key: mutations[1][key] for key in ("method", "path", "body")} == {
        "method": "POST", "path": "/api/apps/aidlc-studio/update",
        "body": {"source": str(root)},
    }
    assert mutations[-1]["path"] == "/api/apps/aidlc-studio/enable"


def test_build_without_node_explains_prebuilt_option_before_any_api_write(tmp_path):
    result, _, requests, _ = run_installer(tmp_path, build=True)
    assert result.returncode != 0
    assert "Node.js" in result.stderr and "--no-build" in result.stderr
    assert requests == []


def test_a_command_name_in_kc_py_resolves_through_path_and_reaches_kcapi(tmp_path):
    result, _, requests, python_log = run_installer(tmp_path, kc_py="name")
    assert result.returncode == 0, result.stdout + result.stderr
    assert python_log.exists()
    assert {r["kc_py"] for r in requests} == {str(tmp_path / "tools" / "python3-gateway")}


def test_the_default_interpreter_is_the_one_kcapi_uses(tmp_path):
    result, _, requests, _ = run_installer(tmp_path, kc_py=None)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = BUNDLED_PY if os.access(BUNDLED_PY, os.X_OK) else tmp_path / "tools" / "python3"
    assert requests and {r["kc_py"] for r in requests} == {str(expected)}


def test_a_missing_interpreter_is_named_before_any_api_call(tmp_path):
    result, root, requests, _ = run_installer(tmp_path, kc_py="name")
    (tmp_path / "tools" / "python3-gateway").unlink()
    requests_file = tmp_path / "requests.jsonl"
    requests_file.unlink(missing_ok=True)
    env = {"PATH": str(tmp_path / "tools"), "KC_PY": "python3-gateway", "HOME": str(tmp_path)}
    again = subprocess.run(
        [str(tmp_path / "tools" / "bash"), str(root / "scripts/dev-install.sh"), "--no-build"],
        cwd=root, env=env, text=True, capture_output=True, timeout=15,
    )
    assert again.returncode != 0
    assert "KC_PY" in again.stderr and "python3-gateway" in again.stderr
    assert not requests_file.exists()


def test_kcapi_names_a_missing_gateway_python_instead_of_blaming_the_gateway(tmp_path):
    kcapi = Path(__file__).resolve().parents[1] / "scripts" / "kcapi.sh"
    env = {
        "PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path),
        "KC_PY": str(tmp_path / "no-such-python"), "KC_JAR": str(tmp_path / "jar"),
        "KC_PORT": "1",
    }
    done = subprocess.run(["bash", str(kcapi), "GET", "/api/apps"], env=env,
                          text=True, capture_output=True, timeout=15)
    assert done.returncode == 1
    assert "gateway Python not found" in done.stderr and "KC_PY" in done.stderr
    assert "is the gateway running" not in done.stderr
