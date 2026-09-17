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


def run_installer(tmp_path: Path, *, quoted_checkout=False, build=False, installed=False):
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
    requests = tmp_path / "requests.jsonl"
    api = root / "scripts/kcapi.sh"
    api.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        "method, path = sys.argv[1:3]\n"
        "body = json.loads(sys.argv[3]) if len(sys.argv) > 3 else None\n"
        f"with Path({str(requests)!r}).open('a') as out:\n"
        "    out.write(json.dumps({'method': method, 'path': path, 'body': body}) + '\\n')\n"
        "if method == 'GET' and path == '/api/apps/aidlc-studio':\n"
        f"    print(json.dumps({{'name': 'aidlc-studio'}} if {installed!r} else {{}}))\n"
        "elif method == 'GET' and path == '/api/apps':\n"
        "    print(json.dumps([{'name': 'aidlc-studio', 'enabled': True}]))\n"
        "else:\n"
        "    print(json.dumps({'ok': True, 'status': 'healthy'}))\n"
    )
    api.chmod(0o755)
    env = os.environ.copy()
    env.update({"PATH": str(tools), "KC_PY": str(python)})
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
    }]
    assert requests[-1]["path"] == "/api/apps/aidlc-studio/health"


def test_update_serializes_checkout_path_without_corrupting_json(tmp_path):
    result, root, requests, _ = run_installer(
        tmp_path, quoted_checkout=True, installed=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    mutations = [r for r in requests if r["method"] == "POST"]
    assert mutations[0]["path"] == "/api/apps/aidlc-studio/disable"
    assert mutations[1] == {
        "method": "POST", "path": "/api/apps/aidlc-studio/update",
        "body": {"source": str(root)},
    }
    assert mutations[-1]["path"] == "/api/apps/aidlc-studio/enable"


def test_build_without_node_explains_prebuilt_option_before_any_api_write(tmp_path):
    result, _, requests, _ = run_installer(tmp_path, build=True)
    assert result.returncode != 0
    assert "Node.js" in result.stderr and "--no-build" in result.stderr
    assert requests == []
