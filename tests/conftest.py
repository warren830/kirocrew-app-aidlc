"""Test harness for the AI-DLC Studio backend.

Three jobs:

1. **Load the app the way the gateway does.** ``load_backend()`` registers the same private namespace
   package ``backend/routes.py`` registers at runtime, then loads the two entry-point modules by path.
   Tests therefore exercise the real import mechanism instead of a convenient one that would hide a
   packaging bug until install time.
2. **Fake the host, not the app.** ``fake_ctx`` builds a REAL ``AppContext`` (so a change in the host's
   dataclass breaks a test rather than production), with a recording event bus. ``fake_host`` is a
   duck-typed ``DashboardState`` exposing only what ``HostBridge`` is allowed to touch — if a module
   reaches for anything else, the test fails, which is the point.
3. **Build real AI-DLC repositories on disk.** ``repo_builder`` lays out an actual workspace from the
   harvested fixtures, and ``fake_bun`` / ``fake_git`` are executables that emulate the allowlisted
   verbs and *fail loudly* on anything denied, so a policy regression shows up as a test failure rather
   than as a subprocess nobody noticed.

Nothing here touches ``~/.kiro/crew``: every test gets ``KIROCREW_HOME`` pointed at a temp directory.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import stat
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

import pytest

APP_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = APP_ROOT / "tests" / "fixtures"
PAYLOAD = APP_ROOT / "payload"

sys.path.insert(0, str(APP_ROOT / "tests"))  # so `import fixtures` resolves to tests/fixtures/

#: The namespace ``backend/routes.py`` creates for itself; tests must use the same one so the module
#: objects (and therefore the Services registry) are shared.
STUDIO_NS = "_aidlc_studio_backend"

_LOADED: dict[str, ModuleType] = {}


def _register_namespace(name: str, search_dir: Path) -> None:
    if name in sys.modules:
        return
    spec = importlib.machinery.ModuleSpec(name, None, is_package=True)
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(search_dir)]  # type: ignore[attr-defined]
    sys.modules[name] = module


def _load_entry(rel: str, dotted: str) -> ModuleType:
    """Load an app entry-point file by path, the way ``apps/module_loader`` does."""
    path = APP_ROOT / rel
    spec = importlib.util.spec_from_file_location(dotted, path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[dotted] = module
    spec.loader.exec_module(module)
    return module


def load_backend() -> ModuleType:
    """Load ``backend/routes.py`` (and ``backend/hooks.py``) and return the routes module.

    Mirrors the gateway: the host registers ``_kirocrew_app_aidlc-studio[.backend[.studio]]`` on 0.5.0
    and nothing on 0.3.0, and Studio's own bootstrap covers both. Loading through the *host* name here
    proves the bootstrap does not depend on it.
    """
    if "routes" in _LOADED:
        return _LOADED["routes"]
    host_ns = "_kirocrew_app_aidlc-studio"
    _register_namespace(host_ns, APP_ROOT)
    _register_namespace(f"{host_ns}.backend", APP_ROOT / "backend")
    routes = _load_entry("backend/routes.py", f"{host_ns}.backend.routes")
    hooks = _load_entry("backend/hooks.py", f"{host_ns}.backend.hooks")
    _LOADED["routes"] = routes
    _LOADED["hooks"] = hooks
    return routes


def studio_module(name: str) -> ModuleType:
    """Import one ``backend/studio/<name>.py`` through the app's own namespace."""
    load_backend()
    return importlib.import_module(f"{STUDIO_NS}.studio.{name}")


# --------------------------------------------------------------------------- #
# deterministic clock and ids
# --------------------------------------------------------------------------- #


class FakeClock:
    """A clock the tests move by hand, so every timestamp in an assertion is exact."""

    def __init__(self, start: str = "2026-09-04T10:00:00Z") -> None:
        self._dt = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self._mono = 1000.0

    def now(self) -> float:
        return self._dt.timestamp()

    def iso(self) -> str:
        return self._dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        self._dt += timedelta(seconds=seconds)
        self._mono += seconds


class SequentialIdFactory:
    """``r_000000000001``-style ids: readable in assertions, stable across runs."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def new(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        width = 12 if prefix == "r" else 16
        return f"{prefix}_{self._counters[prefix]:0{width}d}"


# --------------------------------------------------------------------------- #
# host fakes
# --------------------------------------------------------------------------- #


class FakeEventBus:
    """Records published events and enforces the manifest's declared event names."""

    def __init__(self, allowed: Sequence[str]) -> None:
        self._allowed = set(allowed)
        self.published: list[tuple[str, dict]] = []

    @property
    def allowed_events(self) -> set[str]:
        return set(self._allowed)

    def publish(self, event_type: str, data: dict | None = None) -> None:
        if event_type not in self._allowed and "*" not in self._allowed:
            raise PermissionError(f"app not permitted to publish {event_type!r}")
        self.published.append((event_type, dict(data or {})))

    def publish_to_app(self, event_type: str, data: dict | None = None) -> None:
        self.publish(event_type, data)


class FakeSpawnSDK:
    """``ctx.spawn`` stand-in: records spawns and lets a test set the agent's answer."""

    def __init__(self) -> None:
        self.spawned: list[dict[str, Any]] = []
        self.results: dict[str, SimpleNamespace] = {}
        self.next_result: str = ""
        self.fail_with: Exception | None = None

    async def run(self, task: str, agent: str = "", *, silent: bool = False, model: str = "") -> str:
        if self.fail_with is not None:
            raise self.fail_with
        spawn_id = f"spawn-{len(self.spawned) + 1}"
        self.spawned.append({"id": spawn_id, "task": task, "agent": agent, "silent": silent, "model": model})
        self.results[spawn_id] = SimpleNamespace(
            id=spawn_id, done=True, result=self.next_result, error="", agent=agent, task=task
        )
        return spawn_id

    def is_done(self, spawn_id: str) -> bool:
        return spawn_id in self.results


class FakeSlot:
    """The subset of ``_ChatSlot`` ``HostBridge`` is allowed to read."""

    def __init__(self, key: str, *, project: str = "", agent: str = "", app: str = "",
                 running: bool = False, title: str = "") -> None:
        self.key = key
        self.project = project
        self.agent = agent
        self.title = title or key
        self._app = app
        self.running = running
        self.messages: list[dict[str, Any]] = []
        self.queue_depth = 0
        self._stop_state = "idle"
        self._in_stage_execution = False
        self._approval_futures: dict[str, Any] = {}
        self._question_pending: dict[str, dict] = {}
        self.linked_session_key = ""
        self.task = None
        self.stopping = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "agent": self.agent,
            "project": self.project,
            "app": self._app,
            "running": self.running,
            "stopping": self.stopping,
            "queue_depth": self.queue_depth,
            "stop_state": self._stop_state,
            "messages": len(self.messages),
            "needs_input": bool(self._question_pending),
            "waiting_for_input": not self.running and bool(self.messages),
            "pending_approval": any(not f.done() for f in self._approval_futures.values()),
            "last_ts": self.messages[-1]["ts"] if self.messages else "",
            "linked_session_key": self.linked_session_key,
        }


class FakeSlackClient:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.dms: list[str] = []

    async def open_dm(self, user_id: str) -> str:
        self.dms.append(user_id)
        return f"D{user_id}"

    async def post_blocks(self, channel: str, blocks: list, text: str = "", thread_ts: str | None = None,
                          **kwargs: Any) -> str:
        self.posts.append({"channel": channel, "blocks": blocks, "text": text, "thread_ts": thread_ts})
        return f"ts-{len(self.posts)}"

    async def post_message(self, channel: str, text: str, thread_ts: str | None = None, **kwargs: Any) -> str:
        return await self.post_blocks(channel, [], text, thread_ts)


class FakeSessionManager:
    def __init__(self) -> None:
        self.busy: set[str] = set()

    def is_busy(self, key: str) -> bool:
        return key in self.busy

    def set_slack_link(self, key: str, thread_ts: str | None, channel: str | None) -> None:
        self.links.append((key, thread_ts, channel))

    links: list[tuple[str, str | None, str | None]] = []


class FakeDashboardState:
    """Duck-typed ``DashboardState``. Only the attributes ``HostBridge`` may use exist."""

    def __init__(self, *, owner_id: str = "owner-1", slack: bool = True) -> None:
        self._slots: dict[str, FakeSlot] = {}
        self.sessions = FakeSessionManager()
        self.owner_id = owner_id
        self.slack_client = FakeSlackClient() if slack else None
        self.subagents = None
        self.notifications: list[dict[str, Any]] = []
        self._pending_questions: dict[str, dict] = {}

    # ---- construction helpers used by tests ----
    def add_slot(self, key: str, **kwargs: Any) -> FakeSlot:
        slot = FakeSlot(key, **kwargs)
        self._slots[key] = slot
        return slot

    def set_running(self, key: str, running: bool) -> None:
        self._slots[key].running = running
        session_key = f"dashboard:{key}"
        if running:
            self.sessions.busy.add(session_key)
        else:
            self.sessions.busy.discard(session_key)

    def append_user_row(self, key: str, text: str, ts: str, *, mid: str = "") -> dict[str, Any]:
        row = {"role": "user", "content": text, "cls": "msg msg-u", "ts": ts, "meta": {"mid": mid} if mid else {}}
        self._slots[key].messages.append(row)
        return row

    # ---- host API surface ----
    def get_slot(self, name: str) -> FakeSlot | None:
        return self._slots.get(name)

    def running_session_keys(self) -> list[str]:
        return sorted(self.sessions.busy)

    def notify(self, kind: str, title: str, body: str, *, meta: dict | None = None,
               url: str | None = None, actions: list[dict] | None = None) -> None:
        self.notifications.append(
            {"kind": kind, "title": title, "body": body, "meta": meta, "url": url, "actions": actions}
        )

    def link_slack(self, *args: Any, **kwargs: Any) -> None:
        self.sessions.links.append(("link_slack", args, kwargs))

    def push_slots_update(self) -> None:  # pragma: no cover - observation only
        pass


# --------------------------------------------------------------------------- #
# fixtures: environment, context, host
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def app_root() -> Path:
    return APP_ROOT


@pytest.fixture(scope="session")
def manifest() -> dict:
    return json.loads((APP_ROOT / "app.json").read_text("utf-8"))


@pytest.fixture(scope="session")
def routes() -> ModuleType:
    return load_backend()


@pytest.fixture(scope="session")
def hooks() -> ModuleType:
    load_backend()
    return _LOADED["hooks"]


@pytest.fixture(scope="session")
def studio() -> SimpleNamespace:
    """Every implemented ``backend/studio`` module as an attribute; missing ones are simply absent."""
    load_backend()
    names = sorted(p.stem for p in (APP_ROOT / "backend" / "studio").glob("*.py") if p.stem != "__init__")
    ns = SimpleNamespace()
    for name in names:
        try:
            setattr(ns, name, studio_module(name))
        except Exception as exc:  # a module under construction must not break unrelated tests
            setattr(ns, name, None)
            setattr(ns, f"{name}__error", exc)
    return ns


@pytest.fixture
def kirocrew_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "kirocrew-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("KIROCREW_HOME", str(home))
    return home


@pytest.fixture(autouse=True)
def _no_guard_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a developer's exported AI-DLC bypass variable reach a test subprocess."""
    for key in list(os.environ):
        if key.startswith(("AIDLC_", "CLAUDE_", "AWS_AIDLC_")):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def ids() -> SequentialIdFactory:
    return SequentialIdFactory()


@pytest.fixture
def fake_ctx(tmp_path: Path, kirocrew_home: Path, manifest: dict) -> Any:
    """A real ``AppContext`` with recording services, so host dataclass drift breaks a test."""
    from kiro_crew.apps.app_storage import AppStorage
    from kiro_crew.apps.context import AppContext

    data_dir = tmp_path / "appdata"
    data_dir.mkdir(parents=True, exist_ok=True)
    ctx = AppContext(
        name="aidlc-studio",
        data_dir=data_dir,
        config=dict(manifest.get("extra", {})),
        storage=AppStorage("aidlc-studio", data_dir),
        events=FakeEventBus(manifest["permissions"]["events"]),  # type: ignore[arg-type]
    )
    ctx.spawn = FakeSpawnSDK()  # type: ignore[assignment]
    return ctx


@pytest.fixture
def fake_host() -> FakeDashboardState:
    return FakeDashboardState()


# --------------------------------------------------------------------------- #
# fixtures: fake executables
# --------------------------------------------------------------------------- #

_FAKE_BUN = r'''#!/usr/bin/env python3
"""Stand-in for `bun` running AI-DLC's TypeScript tools.

Implements only the verbs Studio's allowlist permits, against the real repository the caller names
with --project-dir. Anything else exits with a distinctive code so a policy regression is a loud test
failure rather than a silent no-op:

    98  a forbidden environment variable was inherited
    99  a tool or verb Studio must never invoke was invoked
"""
import json, os, re, sys
from pathlib import Path

DENIED_LOG = Path(os.environ.get("FAKE_BUN_DENIED_LOG", "/tmp/aidlc-studio-denied.log"))
FORBIDDEN_TOOLS = ("aidlc-orchestrate.ts", "aidlc-audit.ts", "aidlc-jump.ts", "aidlc-log.ts")
FORBIDDEN_VERBS = ("next", "report", "park", "approve", "reject", "revise", "advance", "gate-start",
                   "checkbox", "set", "complete-workflow", "finalize", "append", "append-raw", "execute")

for key in os.environ:
    if key.startswith(("AIDLC_", "CLAUDE_", "AWS_AIDLC_")) or key.startswith("KIROCREW"):
        sys.stderr.write("forbidden env var reached the child: %s\n" % key)
        sys.exit(98)

argv = sys.argv[1:]
if argv and argv[0] == "run":
    argv = argv[1:]
if not argv:
    sys.exit(2)
script = Path(argv[0])
tool = script.name
args = argv[1:]

def deny(reason):
    DENIED_LOG.parent.mkdir(parents=True, exist_ok=True)
    with DENIED_LOG.open("a") as fh:
        fh.write(json.dumps({"tool": tool, "args": args, "reason": reason}) + "\n")
    sys.stderr.write("denied: %s\n" % reason)
    sys.exit(99)

if tool in FORBIDDEN_TOOLS:
    deny("forbidden tool")

flags, positional = {}, []
i = 0
while i < len(args):
    a = args[i]
    if a.startswith("--"):
        name = a[2:]
        if i + 1 < len(args) and not args[i + 1].startswith("--"):
            flags[name] = args[i + 1]; i += 2
        else:
            flags[name] = "true"; i += 1
    else:
        positional.append(a); i += 1

verb = positional[0] if positional else ""
if verb in FORBIDDEN_VERBS:
    deny("forbidden verb %s" % verb)

repo = Path(flags.get("project-dir") or os.getcwd())
engine = repo / ".kiro" / "tools"
version = "0.0.0"
vfile = engine / "aidlc-version.ts"
if vfile.is_file():
    m = re.search(r'AIDLC_VERSION\s*=\s*"([^"]+)"', vfile.read_text())
    if m:
        version = m.group(1)

def active_space():
    p = repo / "aidlc" / "active-space"
    return p.read_text().strip() if p.is_file() else "default"

def intents_dir(space=None):
    return repo / "aidlc" / "spaces" / (space or active_space()) / "intents"

def registry(space=None):
    p = intents_dir(space) / "intents.json"
    return json.loads(p.read_text()) if p.is_file() else []

def active_intent(space=None):
    p = intents_dir(space) / "active-intent"
    return p.read_text().strip() if p.is_file() else None

def out(text):
    sys.stdout.write(text if text.endswith("\n") else text + "\n")

if tool == "aidlc-utility.ts":
    if verb == "version":
        out("aidlc %s" % version)
    elif verb == "intent" and len(positional) == 1:
        rows = registry(); act = active_intent()
        if flags.get("json") == "true":
            out(json.dumps({"active": act, "space": active_space(),
                            "intents": [{"uuid": r.get("uuid"), "slug": r.get("slug"),
                                         "status": r.get("status"), "repos": r.get("repos", []),
                                         "dirName": r.get("dirName"),
                                         "active": r.get("dirName") == act} for r in rows]}))
        else:
            for r in rows:
                out("%s%s" % ("* " if r.get("dirName") == act else "  ", r.get("dirName")))
    elif verb == "intent":
        name = positional[1]
        rows = registry()
        match = [r for r in rows if r.get("dirName") == name] or [r for r in rows if r.get("slug") == name]
        if len(match) != 1:
            sys.stderr.write(json.dumps({"error": 'Unknown intent "%s"' % name}) + "\n"); sys.exit(1)
        (intents_dir() / "active-intent").write_text(match[0]["dirName"] + "\n")
        out("Active intent → %s (space: %s)" % (match[0]["dirName"], active_space()))
    elif verb == "space" and len(positional) == 1:
        if flags.get("json") == "true":
            spaces = sorted(p.name for p in (repo / "aidlc" / "spaces").iterdir() if p.is_dir())
            out(json.dumps({"active": active_space(),
                            "spaces": [{"name": s, "active": s == active_space()} for s in spaces]}))
        else:
            out(active_space())
    elif verb == "space":
        (repo / "aidlc" / "active-space").write_text(positional[1] + "\n")
        out("Active space → %s" % positional[1])
    elif verb in ("intent-create", "intent-birth"):
        if verb == "intent-birth" and version >= "2.6":
            sys.stderr.write("`intent-birth` was renamed to `intent-create`.\n"); sys.exit(1)
        scope = flags.get("scope", "poc")
        label = flags.get("label", "new-intent")
        dir_name = "260904-%s" % label
        record = intents_dir() / dir_name
        record.mkdir(parents=True, exist_ok=True)
        template = os.environ.get("FAKE_BUN_STATE_TEMPLATE", "")
        state = Path(template).read_text() if template else "# AI-DLC State Tracking\n"
        state = state.replace("- **Scope**: feature", "- **Scope**: %s" % scope)
        (record / "aidlc-state.md").write_text(state)
        (record / "audit").mkdir(exist_ok=True)
        (record / "audit" / "fake-host-000000000000.md").write_text(
            "\n## Workflow Started\n**Timestamp**: 2026-09-04T10:00:00Z\n**Event**: WORKFLOW_STARTED\n\n---\n")
        rows = registry()
        rows.append({"uuid": "01a00000-0000-7000-8000-000000000001", "slug": label,
                     "dirName": dir_name, "scope": scope, "status": "in-flight"})
        (intents_dir() / "intents.json").write_text(json.dumps(rows, indent=2) + "\n")
        (intents_dir() / "active-intent").write_text(dir_name + "\n")
        out("Created intent %s (scope: %s)" % (dir_name, scope))
    elif verb in ("recompose", "scope-change", "config-change"):
        record = intents_dir() / (active_intent() or "")
        state_file = record / "aidlc-state.md"
        text = state_file.read_text()
        if verb == "scope-change" and flags.get("scope"):
            text = re.sub(r"^- \*\*Scope\*\*:.*$", "- **Scope**: %s" % flags["scope"], text, flags=re.M)
        if flags.get("depth"):
            text = re.sub(r"^- \*\*Depth\*\*:.*$", "- **Depth**: %s" % flags["depth"], text, flags=re.M)
        if verb == "recompose":
            for slug in [s for s in (flags.get("skip") or "").split(",") if s]:
                text = re.sub(r"^(- \[[ xXsSrR?-]\] %s)(?:\s*—.*)?$" % re.escape(slug),
                              r"\1 — SKIP (recomposed)", text, flags=re.M)
            for slug in [s for s in (flags.get("add") or "").split(",") if s]:
                text = re.sub(r"^(- \[[ xXsSrR?-]\] %s)(?:\s*—.*)?$" % re.escape(slug),
                              r"\1 — EXECUTE", text, flags=re.M)
        state_file.write_text(text)
        shard = sorted((record / "audit").glob("*.md"))
        event = {"recompose": "RECOMPOSED", "scope-change": "SCOPE_CHANGED", "config-change": "DEPTH_CHANGED"}[verb]
        if shard:
            with shard[0].open("a") as fh:
                fh.write("\n## %s\n**Timestamp**: 2026-09-04T10:05:00Z\n**Event**: %s\n\n---\n"
                         % (event.title().replace("_", " "), event))
        out("%s ok" % verb)
    elif verb == "doctor":
        out("workspace shell ready\nengine version %s" % version)
    elif verb in ("config-list", "config-get"):
        out(json.dumps({"depth": "Standard", "test-strategy": "Standard", "review": ""}))
    elif verb == "detect":
        out(json.dumps({"projectType": "Brownfield", "languages": ["Python"]}))
    elif verb == "upgrade":
        sys.stderr.write("upgrade is unavailable\n"); sys.exit(1)
    else:
        deny("unhandled utility verb %r" % verb)
elif tool == "aidlc-runtime.ts":
    if verb != "compile":
        deny("unhandled runtime verb %r" % verb)
    record = intents_dir() / (active_intent() or "")
    state = (record / "aidlc-state.md").read_text()
    scope = re.search(r"^- \*\*Scope\*\*:[ \t]*(.*)$", state, flags=re.M)
    graph = {"workflow_id": "", "scope": scope.group(1) if scope else "",
             "started_at": "", "stages": []}
    graph_path = record / "runtime-graph.json"
    graph_path.write_text(json.dumps(graph) + "\n")
    out(json.dumps({"written": str(graph_path)}))
elif tool == "aidlc-state.ts":
    record = intents_dir() / (active_intent() or "")
    text = (record / "aidlc-state.md").read_text() if (record / "aidlc-state.md").is_file() else ""
    if verb == "get":
        field = positional[1] if len(positional) > 1 else ""
        m = re.search(r"^- \*\*%s\*\*:[ \t]*(.*)$" % re.escape(field), text, flags=re.M)
        out(m.group(1).strip() if m else "")
    elif verb == "resume":
        cur = re.search(r"^- \*\*Current Stage\*\*:[ \t]*(.*)$", text, flags=re.M)
        out(json.dumps({"resumed": True, "current_stage": cur.group(1).strip() if cur else "",
                        "gate_state": "open" if "[?]" in text else "none"}))
    elif verb == "count":
        out(str(len(re.findall(r"^- \[", text, flags=re.M))))
    elif verb == "lookup":
        out("")
    else:
        deny("unhandled state verb %r" % verb)
else:
    deny("unhandled tool %r" % tool)
'''

_FAKE_GIT = r'''#!/usr/bin/env python3
"""Stand-in for `git`. Read verbs answer from a small JSON state file; write verbs exit 97."""
import json, os, sys
from pathlib import Path

WRITE_VERBS = {"add","commit","push","pull","merge","rebase","checkout","switch","reset","restore",
               "stash","tag","branch","cherry-pick","revert","am","apply","clean","rm","mv","fetch",
               "remote","submodule","worktree","gc","prune","reflog","update-ref","symbolic-ref",
               "filter-branch","init","clone"}
LOG = Path(os.environ.get("FAKE_GIT_DENIED_LOG", "/tmp/aidlc-studio-git-denied.log"))
STATE = Path(os.environ.get("FAKE_GIT_STATE", ""))
args = sys.argv[1:]
repo = None
if "-C" in args:
    repo = Path(args[args.index("-C") + 1])
tokens = [a for a in args if not a.startswith("-")]
for t in tokens:
    if t in WRITE_VERBS:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(json.dumps({"argv": args}) + "\n")
        sys.stderr.write("git write verb refused: %s\n" % t)
        sys.exit(97)
state = json.loads(STATE.read_text()) if STATE and STATE.is_file() else {}
def out(t):
    sys.stdout.write(t if t.endswith("\n") else t + "\n")
if "rev-parse" in tokens:
    if "--git-common-dir" in args:
        out(str((repo or Path.cwd()) / ".git"))
    elif "--abbrev-ref" in args:
        out(state.get("branch", "main"))
    elif "--is-inside-work-tree" in args:
        out("true")
    else:
        out(state.get("head", "0" * 40))
elif "status" in tokens:
    out("# branch.oid %s" % state.get("head", "0" * 40))
    out("# branch.head %s" % state.get("branch", "main"))
    if state.get("upstream"):
        out("# branch.upstream %s" % state["upstream"])
        out("# branch.ab +%d -%d" % (state.get("ahead", 0), state.get("behind", 0)))
    for path in state.get("dirty", []):
        out("1 .M N... 100644 100644 100644 %s %s %s" % ("0" * 40, "0" * 40, path))
elif "log" in tokens:
    for c in state.get("commits", []):
        out("%s\x1f%s\x1f%s" % (c.get("sha", "0" * 40), c.get("ts", ""), c.get("subject", "")))
elif "diff" in tokens:
    out(state.get("diff", ""))
elif "show" in tokens:
    out(state.get("show", ""))
elif "rev-list" in tokens:
    out("%d\t%d" % (state.get("ahead", 0), state.get("behind", 0)))
else:
    sys.exit(1)
'''


def _write_executable(path: Path, body: str) -> str:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return str(path)


@pytest.fixture
def denied_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where the fakes record a refused invocation. A test asserting policy checks it stays empty."""
    log = tmp_path / "denied.log"
    monkeypatch.setenv("FAKE_BUN_DENIED_LOG", str(log))
    monkeypatch.setenv("FAKE_GIT_DENIED_LOG", str(log))
    return log


@pytest.fixture
def fake_bun(tmp_path: Path, denied_log: Path) -> str:
    return _write_executable(tmp_path / "bun", _FAKE_BUN)


@pytest.fixture
def fake_git(tmp_path: Path, denied_log: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    state = tmp_path / "git-state.json"
    state.write_text(json.dumps({"branch": "main", "dirty": [], "commits": []}))
    monkeypatch.setenv("FAKE_GIT_STATE", str(state))
    return _write_executable(tmp_path / "git", _FAKE_GIT)


@pytest.fixture
def git_state(tmp_path: Path) -> Callable[[dict], None]:
    """Set what the fake git reports (branch, dirty files, ahead/behind, commits, diff)."""

    def _set(values: Mapping[str, Any]) -> None:
        path = tmp_path / "git-state.json"
        current = json.loads(path.read_text()) if path.is_file() else {}
        current.update(values)
        path.write_text(json.dumps(current))

    return _set


# --------------------------------------------------------------------------- #
# repository builder
# --------------------------------------------------------------------------- #


class RepoBuilder:
    """Lay out a real AI-DLC workspace on disk from the harvested fixtures.

    Every intent is written the way the engine writes it — registry row keyed by ``dirName``, cursor
    files, an audit shard, an optional active directive whose digest can be filled from the state bytes
    — so a parser or consistency test is reading a layout it would meet in a user's repository.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._space = "default"

    # ---- engine ----
    def with_engine(self, source: str = "payload") -> "RepoBuilder":
        """Copy an engine tree in. ``payload`` = whichever harness ``payload/`` currently ships
        (2.7.1 today, and deliberately not pinned here); ``devdelta``/``older`` = the fixture trees
        (data files only, which is all the readers need)."""
        target = self.root / ".kiro"
        if source == "payload":
            shutil.copytree(PAYLOAD / "aidlc-kiro" / ".kiro", target, dirs_exist_ok=True)
        else:
            tree = {"devdelta": FIXTURES / "aidlc-2.6.2-devdelta",
                    "payload230": FIXTURES / "aidlc-2.3.0-payload",
                    "older": FIXTURES / "aidlc-older"}[source]
            shutil.copytree(tree / ".kiro", target, dirs_exist_ok=True)
        return self

    def with_workspace(self, *, active_space: str = "default") -> "RepoBuilder":
        self._space = active_space
        (self.root / "aidlc").mkdir(parents=True, exist_ok=True)
        (self.root / "aidlc" / "active-space").write_text(active_space + "\n")
        intents = self.root / "aidlc" / "spaces" / active_space / "intents"
        intents.mkdir(parents=True, exist_ok=True)
        (self.root / "aidlc" / "spaces" / active_space / "memory").mkdir(parents=True, exist_ok=True)
        if not (intents / "intents.json").is_file():
            (intents / "intents.json").write_text("[]\n")
        return self

    def with_intent(
        self,
        dir_name: str,
        *,
        state: str | Path,
        space: str | None = None,
        uuid: str | None = None,
        slug: str | None = None,
        scope: str = "poc",
        registry_status: str = "in-flight",
        active: bool = True,
        audit: str | Path | None = None,
        directive: dict | None = None,
        questions: Mapping[str, str] | None = None,
        artifacts: Mapping[str, str] | None = None,
        markers: Sequence[str] = (),
    ) -> "RepoBuilder":
        space = space or self._space
        self.with_workspace(active_space=self._space)
        intents = self.root / "aidlc" / "spaces" / space / "intents"
        intents.mkdir(parents=True, exist_ok=True)
        record = intents / dir_name
        record.mkdir(parents=True, exist_ok=True)

        state_text = state.read_text("utf-8") if isinstance(state, Path) else state
        (record / "aidlc-state.md").write_text(state_text)

        shard = record / "audit"
        shard.mkdir(exist_ok=True)
        audit_text = (audit.read_text("utf-8") if isinstance(audit, Path) else audit) or ""
        (shard / "fixture-host-0f1e2d3c4b5a.md").write_text(audit_text)

        if directive is not None:
            payload = dict(directive)
            if payload.get("state_sha256") == "AUTO":
                import hashlib

                payload["state_sha256"] = hashlib.sha256(state_text.encode("utf-8")).hexdigest()
            (record / ".aidlc-active-directive.json").write_text(json.dumps(payload, indent=2) + "\n")

        for rel, text in (questions or {}).items():
            path = record / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

        for rel, text in (artifacts or {}).items():
            path = record / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

        for marker in markers:
            (record / marker).write_text("")

        rows = json.loads((intents / "intents.json").read_text())
        rows = [r for r in rows if r.get("dirName") != dir_name]
        rows.append(
            {
                "uuid": uuid or f"01a00000-0000-7000-8000-{len(rows) + 1:012d}",
                "slug": slug or dir_name.split("-", 1)[-1],
                "dirName": dir_name,
                "scope": scope,
                "status": registry_status,
            }
        )
        (intents / "intents.json").write_text(json.dumps(rows, indent=2) + "\n")
        if active:
            (intents / "active-intent").write_text(dir_name + "\n")
        return self

    def with_git(self, *, branch: str = "main", dirty: Sequence[str] = ()) -> "RepoBuilder":
        (self.root / ".git").mkdir(exist_ok=True)
        (self.root / ".git" / "HEAD").write_text(f"ref: refs/heads/{branch}\n")
        return self

    def with_legacy_layout(self) -> "RepoBuilder":
        legacy = self.root / "aidlc-docs"
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy / "aidlc-state.md").write_text("# AI-DLC State Tracking\n")
        return self

    def mutate_state(self, dir_name: str, fn: Callable[[str], str], *, space: str | None = None) -> None:
        path = self.root / "aidlc" / "spaces" / (space or self._space) / "intents" / dir_name / "aidlc-state.md"
        path.write_text(fn(path.read_text("utf-8")))

    def append_audit(self, dir_name: str, block: str, *, space: str | None = None,
                     shard: str = "fixture-host-0f1e2d3c4b5a.md") -> None:
        path = self.root / "aidlc" / "spaces" / (space or self._space) / "intents" / dir_name / "audit" / shard
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(block)

    def record(self, dir_name: str, *, space: str | None = None) -> Path:
        return self.root / "aidlc" / "spaces" / (space or self._space) / "intents" / dir_name

    def build(self) -> Path:
        return self.root


@pytest.fixture
def repo_builder(tmp_path: Path) -> RepoBuilder:
    return RepoBuilder(tmp_path / "repo")


@pytest.fixture
def gate_repo(repo_builder: RepoBuilder) -> Path:
    """The canonical scenario most tests want: one repo, one intent waiting at an open gate."""
    import fixtures as F

    state = F.state_text(
        marks={"requirements-analysis": "?"},
        fields={"Current Stage": "requirements-analysis", "Status": "Running", "Next Stage": "user-stories"},
    )
    return (
        repo_builder.with_engine("payload")
        .with_workspace()
        .with_intent(
            "260904-gate-demo",
            state=state,
            audit=F.gate_open_audit("requirements-analysis"),
            directive={"version": 1, "stage": "requirements-analysis", "state_sha256": "AUTO"},
            questions={"inception/requirements-analysis/requirements-analysis-questions.md":
                       F.blank_answers(F.real_questions())},
            artifacts={"inception/requirements-analysis/requirements.md": "# Requirements\n\nOne requirement.\n"},
        )
        .with_git()
        .build()
    )


# --------------------------------------------------------------------------- #
# request factory
# --------------------------------------------------------------------------- #


def make_request(
    method: str,
    path: str,
    *,
    body: Any = None,
    match_info: Mapping[str, str] | None = None,
    query: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    user: str | None = "owner-1",
    app: str | None = "",
    host: Any = None,
) -> Any:
    """An aiohttp request shaped like one the gateway's middleware has already authenticated."""
    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    full = path
    if query:
        from urllib.parse import urlencode

        full = f"{path}?{urlencode(dict(query))}"
    application = web.Application()
    application["state"] = host
    application["local_secret"] = "test-internal-secret"
    request = make_mocked_request(method, full, headers=dict(headers or {}), app=application)
    if user is not None:
        request["user"] = user
    if app is not None:
        request["app"] = app
    if match_info:
        request.match_info.update(dict(match_info))

    async def _json(*_a: Any, **_k: Any) -> Any:
        if body is None:
            raise ValueError("no JSON body")
        return body

    request.json = _json  # type: ignore[assignment]
    return request


def owner_request(method: str, path: str, **kwargs: Any) -> Any:
    kwargs.setdefault("user", "owner-1")
    kwargs.setdefault("app", "")
    return make_request(method, path, **kwargs)


def user_request(method: str, path: str, **kwargs: Any) -> Any:
    kwargs["user"] = "someone-else"
    kwargs["app"] = ""
    return make_request(method, path, **kwargs)


def app_request(method: str, path: str, **kwargs: Any) -> Any:
    kwargs["user"] = "aidlc-studio"
    kwargs["app"] = "aidlc-studio"
    return make_request(method, path, **kwargs)


def anon_request(method: str, path: str, **kwargs: Any) -> Any:
    kwargs["user"] = None
    kwargs["app"] = None
    return make_request(method, path, **kwargs)


def internal_request(method: str, path: str, **kwargs: Any) -> Any:
    headers = dict(kwargs.pop("headers", {}) or {})
    headers["X-Internal-Secret"] = "test-internal-secret"
    kwargs["headers"] = headers
    kwargs["user"] = None
    kwargs["app"] = None
    return make_request(method, path, **kwargs)


@pytest.fixture
def request_factory() -> SimpleNamespace:
    return SimpleNamespace(
        make=make_request,
        owner=owner_request,
        user=user_request,
        app=app_request,
        anon=anon_request,
        internal=internal_request,
    )
