"""Read every view the UI renders, for every repository in a copy of a real Studio database.

    python harness.py <app_root> <data_dir> <out.json> <v1|v2>

Runs inside one Studio checkout (its tests/ helpers on sys.path), over a COPY of a live data directory, with a
fake host (no live slots), real bun and real git, one reconciler tick. Only GETs and previews: nothing writes
to a repository.
"""

import asyncio
import json
import shutil
import sys
from pathlib import Path

app_root, data_dir, out_path, version = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
sys.path.insert(0, str(app_root / "tests"))

import conftest as CT  # noqa: E402

CT.load_backend()
services_mod = CT.studio_module("services")
common = CT.studio_module("handlers.common")
from test_handlers_auth import dispatch  # noqa: E402
from kiro_crew.apps.app_storage import AppStorage  # noqa: E402
from kiro_crew.apps.context import AppContext  # noqa: E402

manifest = json.loads((app_root / "app.json").read_text())
ctx = AppContext(name="aidlc-studio", data_dir=data_dir, config=dict(manifest.get("extra", {})),
                 storage=AppStorage("aidlc-studio", data_dir),
                 events=CT.FakeEventBus(manifest["permissions"]["events"]))
sv = services_mod.Services.build(ctx, app_root=app_root, bun_path=shutil.which("bun") or str(Path.home() / ".bun/bin/bun"),
                                 git_path=shutil.which("git"))
sv.storage.open()
sv.settings.load()
host = CT.FakeDashboardState()
sv.host.attach(host)
services_mod._REGISTRY[ctx.name] = sv
routes = common.all_routes(sv)


def read(method, path, body=None):
    request = CT.owner_request(method, path, host=host, body=body)
    request.app["ctx"] = sv.ctx
    status, payload = dispatch(routes, method, path, request)
    return {"status": status, "body": payload}


asyncio.run(sv.reconciler.tick())
views = {"repos": read("GET", "/repos"), "actions": read("GET", "/actions")}
for repo in (views["repos"]["body"] or {}).get("repos", []):
    rid = repo["repo_id"]
    views[f"repo:{rid}"] = read("GET", f"/repos/{rid}")
    views[f"intents:{rid}"] = read("GET", f"/repos/{rid}/intents")
    for kind in ("install", "upgrade", "uninstall"):
        if version == "v1":
            views[f"{kind}_preview:{rid}"] = read("POST", f"/repos/{rid}/{kind}/preview", {})
        else:
            views[f"{kind}_preview:{rid}"] = read("POST", f"/repos/{rid}/transactions/preview", {"kind": kind})
    for summary in (views[f"intents:{rid}"]["body"] or {}).get("intents", []):
        key = summary["intent_key"]
        base = f"/repos/{rid}/intents/{key}"
        views[f"intent:{rid}:{key}"] = read("GET", base)
        views[f"artifacts:{rid}:{key}"] = read("GET", f"{base}/artifacts")
        views[f"review:{rid}:{key}"] = read("GET", f"{base}/review")
for card in (views["actions"]["body"] or {}).get("actions", []):
    views[f"action:{card['action_id']}"] = read("GET", f"/actions/{card['action_id']}")
out_path.write_text(json.dumps(views, indent=1, sort_keys=True, ensure_ascii=False))
print(f"{version}: {len(views)} views")
