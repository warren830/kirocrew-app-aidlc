# 01 — KiroCrew App backend runtime for in-gateway Python apps

Research reader notes for AI-DLC Studio (`aidlc-studio`). Everything below was read
from code, then cross-checked against the gateway that is actually running on this
machine. Where docs and code disagree, the code is cited and the doc is flagged.

## 0. Provenance — which code is "the code"

There are TWO copies of KiroCrew on this machine and they are not the same:

| Copy | Path | Version | Date |
|---|---|---|---|
| Source checkout (what you `grep`) | `/Users/ychchen/warren_ws/kirocrew/src/kiro_crew` | `0.3.0` (pyproject), git `79b6c19` | 2026-08-14 |
| Running gateway (what serves `localhost:5476`) | `/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/lib/python3.12/site-packages/kiro_crew` | `0.5.0-insider.9` (`GET /api/health` → `{"ok": true, "app": "kirocrew", "version": "0.5.0-insider.9"}`) | 2026-09-02 |

`/Users/ychchen/warren_ws/kirocrew/.venv/bin/python` is a symlink to the bundle's
python3 but imports `kiro_crew` from the SOURCE checkout (editable install) — so
`pytest` in the prototype exercises 0.3.0 code, while the gateway runs 0.5.0.

Byte-identical in both copies (safe to read from the checkout): `apps/route_registry.py`,
`apps/app_storage.py`, `apps/context.py`, `apps/event_bus.py`, `apps/spawn_sdk.py`,
`apps/dev_mode.py`.

Different (the bundle is newer; read the bundle when it matters): `apps/module_loader.py`
(relative imports now work — §6), `apps/lifecycle.py` (30 s async startup-hook deadline,
detached-task ownership — §5), `apps/hooks_integration.py` (hook health surfaced in
`GET /api/apps`), `apps/execution.py` (repository-bound trust grants,
`agent.apps_trusted_local` / `agent.apps_trusted_repositories` — §9), `apps/manifest.py`
(`ui.overlays`, `permissions.exposeToApps`, cron `timezone`/`skip_dates`, reserved name
`library`), `apps/manager.py` (`install_app(source, *, expected_name=None,
source_repository="")`, `InstalledApp.defaultOnBackfilled`), `atomic_write.py`
(symlink-parent refusal), `dashboard/token_auth.py` (same contract, more code).

Docs read: `docs/system-specs/modules/app-kit-platform.md`, `docs/app-kit/manifest-reference.md`.
The manifest reference is accurate for hooks; its permissions section itself says the
`permissions` block (other than `api`, `events`, `storage`, `cron`, `spawn`) is advisory
and `validate_permissions` in `apps/permissions.py` is "not wired into the install or
runtime path".

---

## 1. Route contract and dispatch

### 1.1 `AppRoute` (verbatim, `apps/route_registry.py`)

```python
@dataclass
class AppRoute:
    method: str  # HTTP method (uppercase): GET, POST, PUT, DELETE, PATCH
    path: str    # relative path starting with / (e.g. "/status", "/tasks/{task_id}")
    handler: Callable[[web.Request, AppContext], Awaitable[web.Response]]
```

Import: `from kiro_crew.apps.route_registry import AppRoute` and
`from kiro_crew.apps.context import AppContext`.

Route entry point declared in `app.json` as `backend.hooks.routes =
"backend.routes:register_routes"`; contract `register_routes(ctx: AppContext) -> list[AppRoute]`.
It is called synchronously (not awaited) inside `RouteRegistry.register_app_routes`.
Returning a non-list, raising, or failing to import does NOT fail enable — it logs and
calls `ctx.health.mark_degraded(...)` and the app has zero routes. Non-`AppRoute` list
items are skipped with a warning. `method` is `.upper()`-ed; a `path` without a leading
`/` gets one prepended.

### 1.2 Dispatch

- One aiohttp catch-all is registered once: `app.router.add_route("*", "/api/apps/{app_name}/{path:.*}", registry.dispatch)`.
  So EVERY app route lives under `/api/apps/<name>/...`. The registry stores paths
  relative (`/repos/{repo_id}`), full URL = `/api/apps/<name>/repos/<id>`.
- `dispatch()` order: unknown app → `404 {"error": "not found"}`; app known but no ctx →
  `500 {"error": "app context not found"}`; exact match (method + path, non-param routes)
  first, then param routes in registration order; no match → `404 {"error": "not found"}`.
  There is NO 405 — wrong method on a known path is a 404.
- Path params: `_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")`; each param compiles to
  `([^/]+)` (one segment, no slashes, not URL-decoded beyond what aiohttp already did);
  matched values are injected with `request.match_info[name] = value`. Read them as
  `request.match_info["repo_id"]`. Pattern is anchored `^...$`. Trailing slash is
  significant (`/repos` != `/repos/`).
- Note the core `/api/apps/{name}` management routes (`GET /api/apps/{name}`,
  `/manifest`, `/config`, `/enable`, `/disable`, `/update`, `/uninstall`, `/open`, `/dev`,
  `/migrate-cleanup`, `POST /api/apps/{name}/token`) are registered on the same aiohttp
  router BEFORE the catch-all and win for those exact paths. Do not name an app route
  `/config`, `/manifest`, `/enable`, `/disable`, `/update`, `/uninstall`, `/open`, `/dev`,
  `/token`, `/migrate-cleanup` — the host handler shadows it.
- Routes are (re)registered per app on enable (`on_app_enable`) and at gateway boot
  (`on_gateway_startup`), and removed on disable (`deregister_app_routes` also unloads
  the app's `sys.modules` entries). Re-enable re-imports the module fresh.

### 1.3 Authentication — who sets `request["user"]`

Middleware chain (`dashboard/server.py`, `app.middlewares[:] = [...]`), outermost first:
`route_latency` → `host_canonical_redirect` → `host_validation` → `no_cache`(security
headers) → `csrf_middleware` → `token_auth_middleware(...)` → `sel_audit_middleware` →
`spa_fallback` → handler.

`token_auth_middleware` (`dashboard/token_auth.py`) runs BEFORE the app route is reached
and denies by default. App routes are behind auth automatically: `/api/apps/<name>/...`
is not in `_BYPASS_PREFIXES = ("/assets/", "/static/", "/fonts/", "/vendor/", "/artifact-app/")`,
not in `_BYPASS_EXACT` (`/api/health`, `/api/token/local`, `/api/live`, `/api/ready`, ...),
and the only app-related bypasses are `GET/HEAD /apps/<name>/ui/*` (static UI bundles,
`_APPS_UI_BYPASS_RE = re.compile(r"^/apps/[a-z0-9][a-z0-9_-]*/ui/")`) and
`POST /api/apps/<name>/token` (app-secret exchange). Verified live:
`GET /api/apps/aidlc-console/health` without a token → 403; with token → 200.

On success the middleware sets `request["user"] = user_id` (the token `sub`, e.g.
`"local-app"` for CLI-minted tokens) and `request["app"] = app_name` (`""` for a
dashboard-user token; the app's own name for an app token minted via the `.app_secret`
exchange). For app tokens `_enforce_app_scope` restricts paths to `/apps/<app>/...`,
`/api/apps/<app>/...`, `/api/notifications/push`, plus `permissions.api` patterns
(`/x/*` prefix, `/x*`, or exact/`/x/` prefix). Internal loopback callers with
`X-Internal-Secret` set `request["internal_auth"] = True` and NOT `request["user"]`, but
only for `_STRICT_INTERNAL_API_PATHS`/`_MIXED_INTERNAL_API_PATHS` (none of which is
`/api/apps/aidlc-*`).

Therefore the prototype's fail-closed guard is correct and cheap:
`if request.get("user") is None: return 401` (`backend/routes.py::_deny_unauthenticated`).
Keep it — it is defense in depth, not the primary gate.

CSRF: `csrf_middleware` applies to non-`GET/HEAD/OPTIONS`; `check_origin(request,
require=True, fallback_header="Referer")` accepts a missing `Origin` from loopback
(`is_loopback(request.remote)`), so `curl -X POST` from 127.0.0.1 works without an
Origin header; browsers on the dashboard origin pass via `app["allowed_origins"]`.

`sel_audit_middleware` logs every `POST/PUT/DELETE/PATCH` under `/api/` as
`caller="dashboard_user"` with `outcome="ok"` if `resp.status < 400`.

### 1.4 Streaming / SSE / long responses

- `dispatch()` simply `return await route.handler(request, ctx)` with no `isinstance`
  check, so a handler may return `web.StreamResponse` (SSE) or `web.FileResponse`. The
  type hint says `web.Response`; it is not enforced.
- Precedent in core: `apps/routes.py::handle_registry_install_stream` builds
  `web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream",
  "Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})`,
  `await resp.prepare(request)`, writes `event: <name>\ndata: <line>\n\n` frames with
  `await resp.write(...)`, catching `(ConnectionResetError, ConnectionAbortedError)`.
- Middleware after the handler: `no_cache_middleware` calls `_apply_security_headers(resp, ...)`
  if `hasattr(resp, "headers")`. That helper only uses `resp.headers.setdefault(...)`
  (`Cache-Control: no-store, no-cache, must-revalidate, max-age=0`, `Pragma`, `Expires`,
  CSP, Permissions-Policy) — on an already-prepared `StreamResponse` the headers are
  already on the wire, `setdefault` on the `CIMultiDict` does not raise, and
  `handle_registry_install_stream` is production proof. Set your own `Cache-Control` before
  `prepare()` (the core SSE handler sets `no-cache`) because `setdefault` never overrides
  what you set. Every JSON API response otherwise gets `Cache-Control: no-store`.
- Timeouts: none on handler duration. `dashboard/slowloris.py` bounds only request-line
  + header read (`HEADER_READ_TIMEOUT = 30.0`) and idle keep-alive between requests
  (`KEEPALIVE_TIMEOUT = 75.0`); "long-lived streaming responses (SSE, chunked downloads)
  are unaffected". `web.Application(client_max_size=60 * 1024 * 1024)`.
- Do not block the event loop: gateway has a loop-stall watchdog
  (`dashboard.loop_stall_exit_after_secs`, default ~25 s desktop) that KILLS the gateway.
  Any filesystem walk / git / subprocess in a handler must be `await asyncio.to_thread(...)`
  (core code does this everywhere: `list_apps`, `set_dev_mode`, `atomic_write`).
- Alternative to SSE for push: WebSocket broadcast via `ctx.events` (§3) — but see §3.3
  for why the dashboard SDK cannot receive it today.

### 1.5 Error propagation

- An exception escaping a handler propagates through `dispatch` to aiohttp → `500`
  (aiohttp default HTML/plain body). `sel_audit_middleware` re-raises after logging.
  Return `web.json_response({...}, status=4xx)` yourself for machine-readable codes
  (PRD §15 requires codes). Raising `web.HTTPException` subclasses (e.g.
  `web.HTTPServiceUnavailable(reason=...)` as the prototype does) yields that status with
  a text body, not JSON.
- `register_routes` failure → `ctx.health.mark_degraded(...)`; visible in the enable
  response as `hooks.health_status` and (bundle only) in `GET /api/apps` as
  `app["hooks"]["health_status"]`.
- Enable response shape (`POST /api/apps/{name}/enable`): `{"ok", "name", "message",
  "registration": {...}, "hooks": {"hooks_routes": ["GET /api/apps/<n>/health", ...],
  "hooks_startup": "ok"|"failed", "health_status": {...}?, "crons_registered": [...]?},
  "dependencies"?: {...}, "warnings"?: [...]}`.

---

## 2. `AppStorage` and durable storage primitives

### 2.1 API (`apps/app_storage.py`, identical in both copies)

```python
class AppStorage:
    def __init__(self, app_name: str, data_dir: Path)   # creates data_dir/"kv"
    app_name: str                                          # property
    def get(self, key: str) -> dict[str, Any] | str | None # None if missing OR JSON-corrupt OR OSError
    def set(self, key: str, value: dict[str, Any] | str) -> None   # json.dumps(value, indent=2) → atomic_write
    def delete(self, key: str) -> bool                     # True if existed
    def list_keys(self) -> list[str]                       # sorted stems of *.json in kv/
```

Values are not type-restricted at runtime (the prototype stores a `list` under `"repos"`);
anything `json.dumps` accepts works. Key validation (`_key_path`): non-empty; no `..`,
`/`, `\`; must not start with `.` or `~`; else `ValueError`. Every `set`/`delete` emits a
SEL `app_storage.set`/`app_storage.delete` audit row.

### 2.2 On disk

`~/.kiro/crew/apps/<name>/data/kv/<key>.json` (`config_dir()` = `~/.kiro/crew`, overridable
by `KIROCREW_HOME`). Live example: `~/.kiro/crew/apps/aidlc-console/data/kv/repos.json`
(mode `0644`, pretty-printed JSON list). `ctx.data_dir` = `~/.kiro/crew/apps/<name>/data`
(created by `hooks_integration._build_app_context_from_info` with
`Path.mkdir(parents=True, exist_ok=True)` → umask default `0755`). The same `data/`
directory also holds `config.json`, which the HOST reads/writes via
`GET/PUT /api/apps/{name}/config` (`atomic_write`, PUT replaces whole object) — do not use
`config.json` for app state you don't want the dashboard to overwrite.

### 2.3 Atomicity, fsync, locking — assessment against PRD §11.5

`atomic_write(path, content, *, fsync=False, mode=None, newline=None, restrict_to_owner=False,
restrict_on_error="raise")` (`kiro_crew/atomic_write.py`):
`tempfile.mkstemp(dir=path.parent, suffix=".tmp")` → `fchmod` (umask or `mode`) →
`_write_all` (loops on short writes, raises on a persistent 0-byte write) →
`os.fsync(fd)` ONLY if `fsync=True` → `os.close` → `replace_with_retry` (`os.replace`;
Windows-only retry, and the retry never sleeps when called on the event loop). On any
exception the temp file is unlinked. The bundle additionally refuses to write when a
parent component is a symlink outside owned roots (`_refuse_linked_parent`).

Consequences for `AppStorage.set`:
- Atomic replace: readers never see a torn file (process crash mid-write leaves the old
  value + a stray `*.tmp`).
- NOT power-loss durable: `fsync=False` and the directory is never fsynced.
- NO locking, NO compare-and-set: two concurrent `get`→mutate→`set` sequences (two
  handlers on the loop interleaving across an `await`, or CLI + gateway) last-writer-win.
- NO size limits anywhere (`kv/` dir, per-key, or total).

Verdict: `ctx.storage` is fine for preferences and the repo registry; it is NOT the
"durable atomic compare-and-set" store PRD §11.5 requires for action state and lease
generations. Build that yourself inside `ctx.data_dir` from host primitives the core
already uses for exactly this purpose:

- `from kiro_crew.atomic_write import atomic_write` with `fsync=True` (and if you need the
  rename itself durable, `os.open(dir, O_RDONLY)` + `os.fsync(dirfd)` — not provided by
  the helper).
- `from kiro_crew.platform_compat import file_lock` — `with file_lock(fd, exclusive=True):`
  is `fcntl.flock(LOCK_EX)` on POSIX; hold it on a DEDICATED `.lock` file, never on the
  data file (renames orphan the inode). Pattern to copy verbatim:
  `apps/dev_mode.py::_sentinel_lock` (`open(lock_path, "a+")`, `file_lock(fh.fileno(),
  exclusive=True)`, then read → compare generation → `atomic_write` → return). That is
  CAS: read current record under the lock, reject if `status_generation` differs, write
  `generation+1`. Also `try_acquire_lock(fd, exclusive=True)` / `release_lock(fd)` for
  non-blocking lease acquisition, `flock_owner_pid(path)` (Linux `/proc/locks` only).
- Both are blocking; call via `await asyncio.to_thread(...)` from handlers. Note
  `CronService` refuses to take its store lock on a thread that has a running loop
  (`CronLoopSafetyError`) — the same discipline applies to your own locks.
- Secrets/tokens: `atomic_write(..., restrict_to_owner=True)` → `0600` (what
  `write_app_secret` does for `~/.kiro/crew/apps/<name>/.app_secret`).

### 2.4 Lifecycle of `data/`

`install_app` preserves a pre-existing `data/` (moved to `apps/.<name>-data-tmp` and
restored). `update_app` preserves `data/` and `.app_secret`, replaces everything else.
`uninstall_app(name, keep_data=True)` keeps `data/` (REST: body `{"purge_data": true}` to
purge; CLI `--purge-data`). `_copy_app_tree` drops `node_modules`, `.git`, `__pycache__`,
`.venv` at any depth and omits symlinks that resolve outside the source tree (in-tree
relative symlinks are preserved as symlinks).

---

## 3. `EventBus` — backend → WebSocket

### 3.1 Backend API (`apps/event_bus.py`, identical in both copies)

`ctx.events` is an `EventBus` only when `permissions.events` is a non-empty list AND the
host passed a broadcaster; otherwise `None` (guard every call).

```python
EventBus.publish(event_type: str, data: dict | None = None) -> None
EventBus.publish_to_app(event_type: str, data: dict | None = None) -> None  # v1: same broadcast + "_scope": "app"
EventBus.allowed_events -> set[str]
APP_EVENT_WS_TYPE = "app_event"
```

`publish` raises `PermissionError` unless `event_type in permissions.events` or `"*"` is
declared (SEL `event_publish` denied row). All string leaves in `data` are passed through
`kiro_crew.security.redact_credentials` and `redact_exfiltration_urls` recursively. Both
methods are synchronous and thread-safe: `build_broadcast_fn(state.broadcast_ws)` captures
the gateway loop and uses `call_soon_threadsafe` when called from a worker thread
(Mochi's owner loop does this).

Wire frame received by every dashboard WebSocket client (`/api/ws`):

```json
{"type": "app_event", "data": {"event": "<your event name>", "app": "<app name>", "data": {...}, "_scope": "app"?}}
```

Event names are app-chosen strings; Mochi uses `"mochi:notify"`-style namespacing.
Manifest example: `"permissions": {"events": ["aidlc:action-changed", "aidlc:repo-scanned"]}`.

### 3.2 How the SDK is supposed to subscribe

`website/src/app-sdk/index.ts`:
`useAppEvents(event: string, callback: (data: unknown) => void): void` — warns and
no-ops unless `event` is in the manifest's `permissions.events` (or `"*"`), then calls
`subscribe(event, cb)` from `AppApiProvider`. `AppHost.tsx` supplies `subscribeFn`, which
listens for `window` `CustomEvent`s named `` `mc:app:${event}` `` and passes `e.detail`.

### 3.3 Gap: the dashboard never forwards `app_event` frames (code beats docs)

`website/src/hooks/useWebSocket.ts` has cases for `app_reload` (dev-mode) and dozens of
core types, but NO `case 'app_event'` and no default re-dispatch; the only `app_event`
unwrapping in the whole frontend is Mochi's private WebSocket (`apps/mochi/panel/panelBridge.ts`,
line ~543) and crew-companion's `sessionWatch.ts`. `AppHost.tsx` carries the comment
`// TODO: Apps currently only receive CustomEvents; integrate WebSocket subscription when
app event forwarding is implemented.` I confirmed the shipped bundle
(`.../static/dist/assets/App-CwbrxvdF.js`) has the same `mc:app:${e}` listener and no
`app_event` case. So today `ctx.events.publish()` reaches the browser but
`useAppEvents` never fires (the AppHost coverage test only dispatches CustomEvents).

Options for Studio push: (a) open your own `new WebSocket(`${proto}//${location.host}/api/ws`)`
from the app UI (cookie-authenticated, exactly what Mochi's panelBridge does), unwrap
`app_event`, and `window.dispatchEvent(new CustomEvent(`mc:app:${event}`, {detail}))` so
`useAppEvents` keeps working; (b) SSE from your own route (§1.4) — PRD §15 already lists
`GET /events` with a resume cursor; (c) poll. Also emit `mc:notify` CustomEvents for
host toasts (`useNotify`) and `mc:app:badge` for sidebar badges (`useNavBadge`).

---

## 4. `SpawnSDK` — background agents

`ctx.spawn` exists only when `permissions.spawn` is literally `true` AND the gateway has a
`SubagentManager` (`state.subagents`); otherwise `None`.

```python
class SpawnSDK:
    def is_done(self, spawn_id: str) -> bool        # True once finished (any outcome); unknown id → True; no probe → False
    async def run(self, task: str, agent: str = "", *, silent: bool = False, model: str = "") -> str  # spawn id
class SpawnError(RuntimeError): ...
```

Hard constraints enforced by `build_spawn_impl` (`apps/spawn_sdk.py`):
- `task` must be non-blank; `agent` is REQUIRED (empty → `SpawnError`, audited).
- `agent` must be one of THIS app's own materialized agents: `list_agents()` is scanned
  for `a.filename.startswith(f"{app}--")`; anything else (`"kirocrew"`, another app's
  agent, a typo) → `SpawnError("app ... may only spawn its OWN agents (<app>--*)")`.
  Agents come from the manifest `"agents": ["agents/studio-runner.json"]`; on
  register/enable `bridges._register_agents` renders them into
  `~/.kiro/agents/<app>--<agent-name>.json` (`_safe_link_name(f"{app}/{name}")`), where
  `<agent-name>` is the `name` field inside the agent JSON. Pass that inner `name` (not the
  filename) to `run(...)`. The live machine shows the pattern:
  `~/.kiro/agents/auto-improvement--auto-improvement-discovery.json`.
- The spawn is `SubagentManager.spawn(task, agent=agent, silent=silent, approval_mode="auto",
  model=model or None, app=app, _agent_prevalidated=True)`. `approval_mode="auto"` means
  every tool call is auto-approved — the ONLY sandbox is the agent's own tool surface, so
  Studio's runner agent must be restrictive.
- Working directory: the SDK does not expose `cwd` (the manager's `spawn(..., cwd="")`
  parameter exists and is validated against `agent.subagent_cwd_allowed_roots`, but
  `build_spawn_impl` never passes it). The subagent therefore runs in the gateway's
  session pool cwd (`self._sessions._pool_cwd`), not in a registered repo. Put the target
  repo path in the task text, or reach `request.app["state"].subagents.spawn(..., cwd=...)`
  directly (in-process apps can — it bypasses the SDK's ownership check, so only do it
  with your own `<app>--` agent and `app=<name>`).
- Result: `SpawnSDK` gives only an id and `is_done()`. `SubagentInfo` (`kiro_crew/subagent.py`)
  is reachable via `request.app["state"].subagents.get(spawn_id)` → fields `id, task,
  started, done, queued, result, result_path, result_truncated, error, agent, app,
  approval_mode, silent, turns, last_tool, tool_count, last_activity, stalled`. The
  reaper prunes finished records, so read `result`/`result_path` promptly or rely on the
  `subagent_done` WS frame (`{slot, id, elapsed, error?, outcome, task, agent, result}`).
- `run` raises rather than returning `""` when the host declines (governance profile,
  caps, queued-with-error). No per-app rate limit exists ("Cost is the app's to bound").

---

## 5. Lifecycle hooks

Manifest: `"backend": {"hooks": {"routes": "backend.routes:register_routes",
"on_startup": "backend.hooks:on_startup", "on_shutdown": "backend.hooks:on_shutdown"}}`.
Signature: `def on_startup(ctx: AppContext) -> None | Awaitable[None]` (same for
`on_shutdown`); a returned coroutine is awaited (`asyncio.iscoroutine(result)`). Mochi's are
`async def on_startup(ctx)` / `async def on_shutdown(ctx)`.

When called (`apps/hooks_integration.py`):
- `on_app_enable` (after `POST /api/apps/{name}/enable`): execution gate → cron
  registration → build ONE `AppContext` → `register_app_routes` → `on_startup(ctx)` with
  the SAME ctx (so module-level state built in `on_startup` is visible to handlers; both
  see the same `ctx.health`).
- `on_gateway_startup` (aiohttp `on_startup`, after all core routes): for each enabled app
  sorted by name, same order (routes then `on_startup`). The gateway does not wait for
  `on_startup` to be "ready" beyond awaiting it.
- `on_app_disable` (via `teardown_app_runtime`, used by disable, uninstall, trust
  revocation): `on_shutdown(ctx)` (a FRESH ctx from `_build_context`, not the enable-time
  one; skipped when `run_app_hooks=False` i.e. trust withdrawal of an app not believed to
  be running) → `deregister_app_routes` (also `unload_app_modules`) → cron cleanup.
- `on_gateway_shutdown` (aiohttp `on_cleanup`): `dispatch_shutdown` in reverse name order.

Bundle-only behaviour (0.5.0): async `on_startup` is bounded by `_HOOK_TIMEOUT_SEC = 30.0`;
on expiry the task is RETAINED (not cancelled), `ctx.health.mark_degraded("Lifecycle hook
timed out during startup: ...")`, and enable/boot continues. A retained task blocks a
later enable/install/update/uninstall of the same app (`_refuse_while_startup_hook_runs`,
`stop_detached_startup_hooks`) until it finishes — keep `on_startup` fast and move long
work into a background `asyncio.create_task` you own and cancel in `on_shutdown`.
`on_shutdown` is awaited to completion with no timeout. Hook failure is never fatal to the
gateway; it degrades `ctx.health`.

`ctx.config` is `manifest.get("extra", {})` where `manifest` is `AppManifest.to_dict()` —
because `to_dict()` flattens unknown fields, this is non-empty only if `app.json` has a
literal top-level `"extra": {...}` object. Use it for static defaults if you want.

`ctx.logger` is `logging.getLogger("kirocrew.app.<name>")` → `~/.kiro/crew/gateway.log`.

---

## 6. Module loader — can `backend/` be a package?

`load_app_module(app_name, app_dir, "backend.routes:register_routes")`
(`apps/module_loader.py`): parses `module.path:callable` (hook regex in manifest:
`^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*:[a-zA-Z_][a-zA-Z0-9_]*$`), maps
`backend.routes` → `<app_dir>/backend/routes.py`, requires the resolved file to be inside
`app_dir.resolve()`, applies the third-party execution gate (§9), then
`importlib.util.spec_from_file_location(unique_name, file)` with
`unique_name = f"_kirocrew_app_{app_name}.{dotted}"` (e.g. `_kirocrew_app_aidlc-studio.backend.routes`),
`sys.modules[unique_name] = module`, `exec_module`. No `sys.path` mutation ever.
`unload_app_modules(name)` deletes every `sys.modules` key with that prefix on disable.

Empirical result (probe app with `backend/__init__.py`, `backend/helper.py`,
`backend/routes.py`):

| Import in `backend/routes.py` | Source checkout 0.3.0 loader | Running gateway 0.5.0-insider.9 loader |
|---|---|---|
| `from . import helper` / `from .helper import X` | `ModuleNotFoundError: No module named '_kirocrew_app_probe'` | works (`_kirocrew_app_probe`, `_kirocrew_app_probe.backend` registered as namespace packages with `__path__` = app dir / `backend`) |
| `import backend.helper` | `ModuleNotFoundError: No module named 'backend'` | `ModuleNotFoundError: No module named 'backend.helper'` |
| `from backend import helper` | `ModuleNotFoundError` | `ImportError: cannot import name 'helper' from 'backend' (unknown location)` |

So the prototype's "single-file on purpose" note is TRUE for the 0.3.0 checkout and FALSE
for the gateway that is actually running. Rules for Studio:
1. Use RELATIVE imports only (`from . import x`, `from .pkg.mod import y`); never
   `import backend.x`. Deeper packages work the same way (`_ensure_namespace_packages`
   registers every ancestor segment).
2. Declare `"minKiroCrewVersion": "0.5.0"` (`parse_version` strips `-insider.9`, so the
   running `0.5.0-insider.9` satisfies it; `install_app` and `POST /api/apps/install`
   both check it and refuse older gateways with a clear message).
3. Hooks that name the same file (`on_startup`/`on_shutdown` in one module) share one
   module object in the bundle (`_app_module_snapshot` handles the overwrite); routes and
   hooks in different files can share state via a relatively-imported third module.
4. Tests must load the way the gateway does. The prototype's `tests/conftest.py`
   (`spec_from_file_location("_aidlc_console_routes_under_test", routes.py)`) does NOT
   register parent packages, so relative imports would fail in tests even though they
   work in the gateway. Replicate the bundle loader: register
   `importlib.machinery.ModuleSpec("_kirocrew_app_<name>", None, is_package=True)` with
   `__path__=[app_root]`, and `"_kirocrew_app_<name>.backend"` with `__path__=[app_root/"backend"]`,
   then `spec_from_file_location("_kirocrew_app_<name>.backend.routes", ...)`. Or import
   `kiro_crew.apps.module_loader.load_app_module` directly in tests and monkeypatch
   `kiro_crew.apps.module_loader.app_execution_denied` to `lambda *a, **k: None`
   (kirocrew's own `test/test_module_loader.py` monkeypatches
   `kiro_crew.apps.execution.third_party_execution_allowed`).

Builtins use a different path: `lifecycle._resolve_hook` dotted-imports
`kiro_crew.apps.builtins.<pkg>.<module>` for shipped apps; and builtins may also set the
STRING field `backend.routes = "backend.routes:register_routes"` which
`dashboard/routes/system.py` consumes at boot as `importlib.import_module(
f"kiro_crew.apps.builtins.{name}").register_routes(app)` — a `register_routes(app:
web.Application)` that adds absolute `/api/apps/<name>/...` routes directly (issue_radar,
mochi, spec_builder). That builtin-only contract is not available to third-party apps.
For third-party apps `backend.routes` (string) is read by `apps/routes.py::_resolve_app_backend_url`
only in the sense that `backend.entryPoint` + non-`"auto"` `backend.port` implies a
proxied external backend; the prototype's `test_manifest.py` pins `backend.routes` empty
to avoid that confusion. Keep it empty; use `backend.hooks.routes`.

---

## 7. Install, update, uninstall, dev mode, REST + token

### 7.1 Files

`install_app(source)` (`apps/manager.py`; bundle signature
`install_app(source, *, expected_name=None, source_repository="")`):
validate `app.json` (`AppManifest.validate(app_root=source)` + `minKiroCrewVersion`) →
`_check_path_safety(name)` → `app_admission_denied(...)` (allowlist/ban/signature policy,
`~/.kiro/crew/admission_policy.json`) → refuse if `installed.json` exists (use update) →
`_copy_app_tree(source, ~/.kiro/crew/apps/<name>)` → write `installed.json` with
`enabled=False` → mkdir `data/` → `write_app_secret` (`.app_secret`, `0600`).
It does NOT check the execution trust gate; `enable_app` does.

`installed.json` (`InstalledApp.to_dict()`, falsy non-bool/int fields omitted), live example:

```json
{"name": "aidlc-console", "version": "0.1.0", "displayName": "AI-DLC Console", "enabled": true,
 "installedAt": "2026-09-04T09:06:39Z", "updatedAt": "2026-09-04T09:48:16Z",
 "source": "/Users/ychchen/warren_ws/kirocrew-app-aidlc", "origin": "registry",
 "resources": "gateway", "lifecycle": "gateway", "schemaVersion": 2, "dev": false,
 "defaultOnBackfilled": false}
```

Fields: `name, version, displayName, enabled, installedAt, updatedAt, source, origin
(builtin|registry|local|external — note: a local-path install is still stamped "registry"
because `install_app` never sets origin), resources (gateway|app), lifecycle
(gateway|app|locked), schemaVersion=2, migratedTo, dev, sourceUrl, sourceRegistry,
sourceCommit, sourceSigner`, bundle adds `defaultOnBackfilled`. Timestamps are
`time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())`.

`update_app(source, *, expected_name=None)`: re-validate, re-gate admission, move `data/`
and `.app_secret` aside, `rmtree(dest)`, copy, restore, `installed.json =
dataclasses.replace(existing, version=, displayName=, updatedAt=, source=)` (keeps
`enabled`, `dev`, etc.). `POST /api/apps/{name}/update` body `{"source": "/abs/path"}`
(defaults to the recorded `source`); if enabled it deregisters/re-registers resources.
Routes/hooks are NOT reloaded by update — disable→enable (or gateway restart) is needed
for backend changes; UI files are picked up on refresh (`Cache-Control: no-cache`, or
`no-store` in dev mode).

`uninstall_app(name, keep_data=True)`: drops the trust grant first (abort if it cannot),
removes files keeping `data/`, drops the dev sentinel entry. `lifecycle=locked` → refused.

### 7.2 Dev mode

`set_dev_mode(name, enabled)` (`apps/dev_mode.py`): writes `installed.json.dev` and the
sentinel `~/.kiro/crew/apps/.dev-apps.json` (JSON array of names) under
`.dev-apps.json.lock`; the watcher (`POLL_INTERVAL_SECS = 1.0`, `_MAX_SCAN_FILES = 2000`)
rglobs `~/.kiro/crew/apps/<name>/ui/` (symlinks followed) and broadcasts WS
`{"type": "app_reload", "data": {"app": name, "ts": ...}}`; AppHost does a full
`window.location.reload()`. UI files are served `Cache-Control: no-store` while in dev
mode. Builtins cannot enter dev mode. `kirocrew app dev <name>` / `--off`, or
`POST /api/apps/{name}/dev` body `{"enabled": true}`.

Recommended dev setup (the CLI prints this tip): after install, replace the installed
`ui/` with a symlink to your source `ui/`
(`mv ~/.kiro/crew/apps/<name>/ui{,.bak} && ln -s "$PWD/ui" ~/.kiro/crew/apps/<name>/ui`);
`handle_app_ui_file` resolves through the symlink and only checks the resolved path is
under `apps/<name>/ui` resolved, so it works. The same trick works for `backend/`
(`load_app_module` checks `resolved.is_relative_to(app_dir.resolve())` — `app_dir` itself
is not a symlink, but `backend/` → source tree symlink resolves OUTSIDE `app_dir` and is
REFUSED with "Module path escapes app directory"). So: symlink `ui/` only; for backend
iterate with `POST /api/apps/<name>/update` + disable/enable, or symlink the whole
`~/.kiro/crew/apps/<name>` directory (then `app_dir.resolve()` is the source tree and
containment holds — untested, and `install`/`update` re-copy over it). Symlinks do not
survive `install`/`update`.

### 7.3 REST against the running gateway (verified today)

```bash
SECRET=$(cat ~/.kiro/crew/.local_secret)                     # 0600, rewritten at every gateway start
TOKEN=$(curl -s -H "X-Local-Secret: $SECRET" \
  "http://127.0.0.1:5476/api/token/local?ttl=1h" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
# first request with ?token= exchanges the 5-min link token for a session cookie mc_token_5476 (HttpOnly, Max-Age up to 20h)
curl -s -c /tmp/kc.jar "http://127.0.0.1:5476/api/apps/aidlc-console/health?token=$TOKEN"
curl -s -b /tmp/kc.jar http://127.0.0.1:5476/api/apps                       # list (installed.json + manifest + backend_status/hooks)
curl -s -b /tmp/kc.jar -X POST -H 'Content-Type: application/json' \
  -d '{"source":"/abs/path/to/app"}' http://127.0.0.1:5476/api/apps/install  # 201 on success
curl -s -b /tmp/kc.jar -X POST -H 'Content-Type: application/json' -d '{}' \
  http://127.0.0.1:5476/api/security/trusted-apps/aidlc-studio               # per-app trust grant (needed before enable)
curl -s -b /tmp/kc.jar -X POST http://127.0.0.1:5476/api/apps/aidlc-studio/enable
curl -s -b /tmp/kc.jar -X POST -H 'Content-Type: application/json' -d '{"enabled":true}' \
  http://127.0.0.1:5476/api/apps/aidlc-studio/dev
```

Facts: the token is HMAC-signed (`generate_token`, key `~/.kiro/crew/token_signing.key`),
`sub="local-app"` for this path; the `?token=` link token is re-usable within its 5-minute
`LINK_WINDOW_SECS`, and the cookie is what you keep. There is no `Authorization: Bearer`
support (zero occurrences in `token_auth.py`). `kirocrew token` does the same and prints a
URL. Query-token requests also bind the session to the client IP (`ip:127.0.0.1`).
Live state on this machine: `GET /api/security/trusted-apps` →
`{"apps": ["aidlc-console"], "ineffective": [], "allowAll": false}`.

CLI equivalents (`cli_commands.py`, run in-process against the same files — no gateway
needed for install/enable metadata, but hooks only go live when the gateway enables at
boot or via REST): `kirocrew app install <dir>`, `kirocrew app enable <name>`,
`kirocrew app disable <name>`, `kirocrew app uninstall <name> [--purge-data]`,
`kirocrew app dev <name> [--off]`, `kirocrew app list`, `kirocrew app info <name>`,
`kirocrew app init <dir> [--backend] [--ui] [--cron]` (scaffold writes a standalone
`backend/server.py` HTTP server, NOT a hooks module — don't copy it).

---

## 8. `AppManifest` in tests and the field inventory

`from kiro_crew.apps.manifest import AppManifest`;
`m = AppManifest.from_dict(json.loads(app_json_text))` (or `from_json_file(Path)`);
`m.validate(app_root: Path | None = None) -> list[str]` returns `[]` when valid — pass
`app_root=<repo root>` to get canonical path-containment checks (that is what
`install_app` does). The prototype asserts `_manifest().validate() == []` and checks
`m.backend.hooks.routes`, `not m.backend.routes`, `m.ui.entry`, `m.ui.pages[0].route/label/icon/mountFunction`,
`m.permissions.api/storage/network/spawn/cron/events`, `m.dependencies.optionalCommands/commands`.

Known top-level keys (`_KNOWN_FIELDS`, same in both copies): `name, version, displayName,
description, author, license, minKiroCrewVersion, signer, signature, agents, skills, sops,
mcpServers, crons, ui, backend, permissions, setup, tags, jobFamilies, platform,
dependencies, publishProvider, notifications`. Everything else is preserved in
`AppManifest.extra` and round-trips through `to_dict()` at top level — that is how
`heroImage`, `highlights`, `screenshots`, `iconUrl`, `defaultEnabled`, `openCommand` are
carried (Mochi's `app.json` uses them; the storefront reads them from the dict). There is
no `i18n` field in code.

Sub-shapes: `UIConfig(entry, pages: [UIPage(route, label, icon, iconUrl, iconInactiveUrl,
entryPoint, mountFunction="mount")], sidebar: UISidebar(section="Apps", order=10))`, bundle adds
`overlays: [UIOverlay(id, replaces)]`. `BackendConfig(entryPoint, port="auto",
healthCheck="/health", routes, type, hooks: HooksConfig(routes, on_startup, on_shutdown))`.
`Permissions(api: list, events: list, mcpTools: list, storage: bool, network: bool,
memory: str, cron: bool, spawn: bool)` — booleans are granted only for JSON `true`
(`data.get("storage") is True`); bundle adds `exposeToApps: list` and refuses non-list
values for list grants (`_granted_list`). `CronEntry(name, every, cron_expr, agent,
message, agent_sequence, env, persistent_session, silent, command, script, enabled)`, bundle
adds `timezone`, `skip_dates`. `Dependencies(commands, optionalCommands, ...)`,
`PlatformConfig(os, requiresDesktopApp, installMode, ...)`, `SetupConfig(onInstall,
onEnable, onEnableTimeout=30, onDisable, onUninstall, ...)`, `NotificationsConfig(channels,
max 8)`, `PublishProviderConfig`.

Validation rules that bite: `name` must match `KEBAB_RE = ^[a-z0-9]+(?:-[a-z0-9]+)*$`, not
`system` (bundle also refuses `library` — the dashboard's `/apps/library` is static), not a
Windows device stem; `version` must match `SEMVER_RE = ^\d+\.\d+\.\d+([+-]|$)`;
`displayName`/`description` required; every `ui.pages[]` needs `route` and `label`;
`ui.entry` is relative to `ui/` (AppHost loads `/apps/<name>/ui/<entry>`, so `"index.mjs"`
not `"ui/index.mjs"`) and the module must have a DEFAULT export (React.lazy;
`mountFunction` is never called). `GET /apps/<name>/ui/<path>` serves only these
extensions (`_ALLOWED_EXTENSIONS` in `apps/routes.py`, identical in both copies; anything
else → 403): `.mjs .js .css .json .svg .png .jpg .jpeg .gif .webp .woff .woff2 .ttf .map`
(`.mjs`/`.js` → `application/javascript`, `.map` → `application/json`; no `.wasm`,
`.html`, `.ico`, `.mp3`). Paths containing `..` or starting with `/` → 400; the resolved
file must stay under `apps/<name>/ui` (resolved), so a symlinked `ui/` works.

---

## 9. Trust gating for third-party executable apps

`apps/execution.py::app_execution_denied(app_name, *, action, app_root=None, caller="gateway"[, repository=None])`
is THE chokepoint. It returns `None` (allowed) when the app is a shipped builtin (its
`app_root` resolves inside `kiro_crew/apps/builtins/<pkg>` whose `app.json.name` matches),
OR `app_name in agent.apps_trusted` (per-app grant), OR `agent.apps_allow_third_party is True`
(the literal JSON boolean; `"true"`/`1` deny). Otherwise it returns the reason string
"third-party app execution is disabled; trust this app alone ... or set
agent.apps_allow_third_party=true ...". Config lives in `~/.kiro/crew/config.json` under
`agent` (`KiroCrewConfig.load().agent.apps_allow_third_party` / `.apps_trusted`); the bundle
adds `agent.apps_trusted_local` (names granted as local-source) and
`agent.apps_trusted_repositories` (`{name: git-url}`) — a grant bound to a repository
denies if the installed app's `sourceUrl` no longer matches; a local-path install with a
name grant is allowed (`resolve_installed_trust_repository` returns `(True, "")` for
non-registry sources). Current machine: `apps_trusted=["aidlc-console"]`,
`apps_trusted_local=[]`, `apps_trusted_repositories={}`, `apps_allow_third_party=False`.

Where the gate is applied (all fail closed, all SEL-audited as `app_execution_admission`):
`enable_app` (`action="enable"` → `AppResult(error_code="app_execution_denied")` → HTTP 400
`{"ok": false, "code": "app_execution_denied", ...}`), `module_loader.load_app_module`
(`action="module_load"` → `ImportError`), `hooks_integration.on_app_enable`
(`action="hook_enable_register"` — skips routes/hooks and disarms crons),
`on_gateway_startup` (`action="hook_boot_register"`), plus backend process spawn and
manifest shell scripts. Every third-party module load also logs one
`SECURITY: executing third-party app ... in-process ... NOT sandboxed` warning per app.

Grant management: `GET /api/security/trusted-apps`, `POST /api/security/trusted-apps/{name}`
(name must match `APP_NAME_RE = [a-z0-9][a-z0-9_-]{0,127}` and be installed or in a known
registry; 409 for builtins; bundle: optional body `{"repository": "<git url>"}` that must
equal the resolved install repository, else 409 `app_trust_repository_mismatch`; for a
local install send `{}` and it lands in `apps_trusted_local`), `DELETE
/api/security/trusted-apps/{name}` (revokes and runs `teardown_app_runtime(...,
withdrawing_trust=True)`), `PUT /api/security/trusted-apps/allow-all` body `{"value": bool}`.
`uninstall_app` withdraws the grant before deleting files. This matches PRD §16.1
("request narrow trust for aidlc-studio only; never instruct users to enable blanket
apps_allow_third_party") — the Discover/registry install flow prompts for the per-app
grant before cloning, so no CLI step is needed for end users.

Governance (`_app_activation_denied`, `governance_permits("apps", name, session_key=HOST_SESSION_KEY)`)
and admission (`admission_policy.json` allowlist/ban/`require_signature`) are separate,
earlier gates on install/enable.

---

## 10. `ctx.health`

`AppHealthStatus(status="healthy"|"degraded"|"error", issues: list[str], last_checked: ISO-Z)`
with `mark_degraded(issue)`, `mark_error(issue)`, `to_dict()` (`{"status", "issues"?,
"last_checked"?}`). The host calls `mark_degraded` for route-module load failure, route
function failure/non-list, hook failure/timeout. Apps may call it from `register_routes`,
`on_startup`, or any handler (the ctx is shared) to report e.g. "bun missing" or "storage
unwritable". Surfaced in the enable response (`hooks.health_status`) and, in the bundle,
in `GET /api/apps` as `app["hooks"]["health_status"]` (issues are credential-redacted);
cleared on disable. Nothing else reads it (no auto-disable, no UI banner beyond the store).

---

## 11. How the prototype tests fake the runtime (`/Users/ychchen/warren_ws/kirocrew-app-aidlc/tests`)

- `conftest.py`: loads `backend/routes.py` by path with
  `importlib.util.spec_from_file_location("_aidlc_console_routes_under_test", path)`,
  `sys.modules[spec.name] = module`, `spec.loader.exec_module(module)`; session fixture
  `routes` returns the module, `app_root` returns the repo root. (See §6.4 for the change
  needed once relative imports are used.)
- `test_routes.py`: `_ctx(tmp_path)` builds a REAL `AppContext(name="aidlc-console",
  data_dir=tmp/"appdata", storage=AppStorage("aidlc-console", data_dir))` — no gateway
  services, `events/spawn/cron=None`. Requests are a minimal `_Req(match_info: dict, body,
  user="tester")` exposing `.match_info`, `.get(key, default)` (so `request.get("user")`
  works; `user=None` exercises the 401 path) and `async json()` (raises `ValueError` when
  no body). Handlers are invoked as `asyncio.run(routes.handle_x(_Req(...), ctx))` and the
  `web.Response` is decoded with `json.loads(resp.text)`. `test_register_routes_exposes_expected_surface`
  calls `routes.register_routes(_ctx(tmp_path))` and asserts every `route.handler` carries
  `__kirocrew_authenticated__ = True` (the `_authenticated` wrapper sets it).
- `test_manifest.py`: `AppManifest.from_dict(json.loads(app.json)).validate() == []` plus
  the invariants listed in §8.
- Run with the kirocrew venv so `kiro_crew` imports:
  `/Users/ychchen/warren_ws/kirocrew/.venv/bin/python -m pytest tests/test_manifest.py tests/test_routes.py -q -o addopts=`
  → 66 passed (verified). Note this tests against 0.3.0 source, not the 0.5.0 bundle; to
  test against the bundle use
  `/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/bin/python3`
  directly (its site-packages has the bundled `kiro_crew` 0.5.0-insider.9; it prints
  "Superseded default in stored config" warnings on import — harmless).
- KiroCrew's own patterns: `test/test_route_registry.py` (populates
  `registry._routes[app]` with `_RegisteredRoute(method, path, handler, has_params,
  compiled, param_names)` and a `MagicMock()` ctx), `test/test_module_loader.py` (writes a
  temp app dir, `load_app_module(...)`, monkeypatches
  `kiro_crew.apps.execution.third_party_execution_allowed`), `test/test_mochi_routes.py`
  (`aiohttp.test_utils.make_mocked_request(method, path, headers=...)` then overrides
  `req.json`; a `_Ctx` stub with `name, data_dir, events=None, config={}`;
  `await hooks.on_startup(_Ctx(tmp_path))` … `await hooks.on_shutdown(None)`),
  `test/test_manifest_hooks.py` (Hypothesis round-trips of `HooksConfig`/`CronEntry`).
  The only builtins using `backend.hooks` are `mochi` and `crew_companion`
  (`"hooks": {"on_startup": "hooks:on_startup", "on_shutdown": "hooks:on_shutdown"}` plus
  the builtin-only `"routes": "backend.routes:register_routes"` string).

---

## 12. Open questions not resolvable from code

1. Whether `app_event` → `mc:app:<event>` forwarding will land in the dashboard (the
   AppHost TODO is dated 2026-08-05 and is still absent in the 2026-09-02 bundle). Until
   then Studio's UI needs its own `/api/ws` socket or SSE.
2. Whether the source checkout at `/Users/ychchen/warren_ws/kirocrew` will be updated to
   ≥0.5.0 before implementation; if not, tests run against loader semantics the gateway no
   longer has (relative imports). Decide which python the test suite targets.
3. `handle_app_ui_file` and `load_app_module` containment when the whole
   `~/.kiro/crew/apps/<name>` directory (not just `ui/`) is a symlink to the source tree —
   plausible from the `resolve()` logic but not exercised here.
4. Whether the official registry/Discover install path for a `subdirectory` app records
   `sourceUrl` such that the bundle's repository-bound trust grant is required (it will
   for registry installs) — affects the user-facing consent text, not the code.

---

## Critic addendum (2026-09-04, completeness pass)

Verified against the running gateway bundle (`/Applications/KiroCrew.app/Contents/Resources/backend-dist/kirocrew-backend-arm64/lib/python3.12/site-packages/kiro_crew`, `0.5.0-insider.9`) unless noted.

### A1. Relative imports — this doc is right for 0.5.0; docs 02/07/08 are right for 0.3.0

`apps/module_loader.py` in the bundle defines `_ensure_namespace_packages(app_name, dotted_path, app_dir)` (line 81), registering every ancestor as `importlib.machinery.ModuleSpec(name, None, is_package=True)` (line 111) with `pkg.__path__ = [str(search_dir)]` (line 113), called from `load_app_module` at line 255 before `exec_module`. The source checkout (`/Users/ychchen/warren_ws/kirocrew`, 0.3.0) has no such function. Therefore:

- Doc 02 §2.1 ("Sibling relative imports do not work"), doc 07 §1.7/§6 ("hence the single-file design"), and doc 08 §0.4/§7.2 (empirical `ModuleNotFoundError`) all describe **0.3.0** semantics.
- **Decision for the implementer** (recorded in `00-index.md`): either (a) declare `"minKiroCrewVersion": "0.5.0"` and use plain relative imports (`from . import models`), or (b) use doc 08 §7.2's `_sibling()` loader, which works under both loaders. (b) is the only option if the test suite keeps running on the 0.3.0 editable venv (`/Users/ychchen/warren_ws/kirocrew/.venv/bin/python`); (a) is cleaner if tests run on the bundle interpreter (`.../kirocrew-backend-arm64/bin/python3`, see §11).

### A2. Atomic CAS primitive — resolved (answers doc 08 §4.2 "no locking/CAS primitive")

Bundle signatures, verbatim:

```python
# kiro_crew/platform_compat.py
def file_lock(fd: int, *, exclusive: bool = True, required: bool = False) -> Iterator[None]   # line 533, @contextmanager; fcntl.flock LOCK_EX/LOCK_SH on POSIX
def release_lock(fd: int) -> None                                                             # line 626
def try_acquire_lock(fd: int, *, exclusive: bool = False) -> bool                             # line 641 (non-blocking)
def flock_owner_pid(path: str | os.PathLike) -> int | None                                     # line 2740 (Linux /proc/locks only)

# kiro_crew/atomic_write.py
def atomic_write(path: Path | str, content: str | bytes, *, fsync: bool = False, mode: int | None = None,
                 newline: str | None = None, restrict_to_owner: bool = False,
                 restrict_on_error: RestrictErrorPolicy = "raise") -> None                     # line 416
```

Pattern to copy for the PRD §11.5 durable CAS store is `apps/dev_mode.py::_sentinel_lock` (lines 76-92): `open(lock_path, "a+")` on a DEDICATED `.lock` file → `with platform_compat.file_lock(fh.fileno(), exclusive=True): yield` → inside the block read record, compare `status_generation`, `atomic_write(..., fsync=True)` the new record. Its docstring explains why the lock must not be on the data file itself (rename orphans the inode). Call from handlers via `await asyncio.to_thread(...)`. `AppStorage.set` remains suitable only for preferences/registry (no lock, `fsync=False`).

### A3. Import map in the running bundle is identical to doc 03 §1.1

`static/dist/index.html` importmap: `react, react-dom, react-dom/client, react/jsx-runtime, @kirocrew/app-sdk, @kirocrew/app-sdk/ui, lucide-react` → `/vendor/*.mjs`. `static/dist/vendor/` contains exactly `kirocrew-app-sdk.mjs kirocrew-ui.mjs lucide-react.mjs mermaid.min.js react-dom-client.mjs react-dom.mjs react-jsx-runtime.mjs react.mjs tailwindcss-browser.js`. No `@kirocrew/ui` or `@tanstack/react-query` import-map entry (docs 06 §14 and 07 §1.1 conflate the `window.__kirocrew_modules` registry keys with the import map — doc 03 is the authority).

### A4. In-process human-lane entry points exist in 0.5.0 (cross-check for doc 02 §9)

`dashboard/turn_dispatch.py:377 spawn_guarded_turn`, `dashboard/chat_runner.py:4505 _run_chat`, `dashboard/state.py:5826 run_background_turn`, `:6747 get_slot`, `:6891 resolve_slot`, `:6992 get_or_create_slot`, `:8400 push_slots_update`, `dashboard/chat_utils.py:519 _history_key_for`, `:651 effective_session_key`, `dashboard/handlers/source_providers.py:4283 is_owner_dashboard_request`. Full 0.5.0 signatures are in doc 02's addendum.
