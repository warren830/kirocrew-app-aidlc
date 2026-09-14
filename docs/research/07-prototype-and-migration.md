# 07 — The `aidlc-console` prototype and what to carry forward or migrate

Research reader notes for AI-DLC Studio (`aidlc-studio`). Everything below was read from
source code on this machine on 2026-09-04; where the PRD or a README disagrees with code, the
code is quoted and the disagreement is called out.

Sources read (all absolute):

- Prototype source: `/Users/ychchen/warren_ws/kirocrew-app-aidlc/{app.json,README.md,ui/index.mjs,backend/routes.py,tests/conftest.py,tests/test_manifest.py,tests/test_routes.py}`
- Installed copy: `/Users/ychchen/.kiro/crew/apps/aidlc-console/` (byte-identical to the source tree — `diff -rq` reports no differences)
- KiroCrew host: `/Users/ychchen/warren_ws/kirocrew/src/kiro_crew/apps/{app_storage,context,manager,manifest,module_loader,route_registry,routes,hooks_integration,lifecycle,dev_mode}.py`, `src/kiro_crew/dashboard/token_auth.py`, `src/kiro_crew/security.py`, `src/kiro_crew/cli.py`; frontend `website/src/{appNav.ts,App.tsx,components/AppHost.tsx,pages/AppPage.tsx,pages/MigrationPage.tsx,components/MigrationBanner.tsx,app-sdk/index.ts,app-sdk/shared-modules.ts,kirocrew-ui/index.ts,api/client.ts,index.css}`
- AI-DLC: `/Users/ychchen/warren_ws/aidlc-workflows` (git HEAD `a48bcd6f`, 2026-07-08, CHANGELOG top `2.2.10`, **32-stage** engine) and the **newer 33-stage engine actually installed** in `/Users/ychchen/warren_ws/DevDelta/.kiro/tools/` (has `aidlc-directive.ts` and `ACTIVE_DIRECTIVE_MARKER`, which the checkout lacks). When the two disagree, this document follows the installed engine because that is what the prototype's real-file tests parse.
- PRD: `/Users/ychchen/warren_ws/kirocrew-app-aidlc/docs/AI-DLC-Studio-PRD.md` §2 item 8, §9.2 (FR-REP-003..009), §9.22 (FR-MIG-001..005), §14.

---

## 0. Inventory and current install state

| Item | Value |
|---|---|
| App name / version / displayName | `aidlc-console` / `0.1.0` / `AI-DLC Console` |
| Source tree | `/Users/ychchen/warren_ws/kirocrew-app-aidlc` (not a git repo) |
| Line counts | `ui/index.mjs` 619, `backend/routes.py` 1045, `tests/test_routes.py` 856, `tests/test_manifest.py` 109, `tests/conftest.py` 38, `app.json` 46, `README.md` 140 |
| Extra dirs in source only | `docs/` (PRD + this research dir), `mockups/` (4 HTML design options) — excluded from install by nothing in `_COPY_IGNORE`, so a future install **will** copy them; keep them out of the app root or add them to a build step |
| Installed dir | `/Users/ychchen/.kiro/crew/apps/aidlc-console/` containing `app.json`, `README.md`, `backend/routes.py`, `ui/index.mjs`, `tests/*`, `installed.json`, `.app_secret` (mode 0600, 64 bytes), `data/kv/repos.json` |
| `installed.json` (exact) | `{"name":"aidlc-console","version":"0.1.0","displayName":"AI-DLC Console","enabled":true,"installedAt":"2026-09-04T09:06:39Z","updatedAt":"2026-09-04T09:48:16Z","source":"/Users/ychchen/warren_ws/kirocrew-app-aidlc","origin":"registry","resources":"gateway","lifecycle":"gateway","schemaVersion":2,"dev":false,"defaultOnBackfilled":false}` |
| Dev mode | off (`dev: false`, no `~/.kiro/crew/apps/.dev-apps.json`) |
| Third-party execution trust grant | **present**: `~/.kiro/crew/config.json` → `agent.apps_trusted = ["aidlc-console"]`, with `agent.apps_allow_third_party = false`. FR-MIG-004's "narrow trust" means removing this entry (see §6). |
| Backend process | none — the app uses the in-process route hook (`backend.hooks.routes`), so it is absent from `~/.kiro/crew/app_backends.pids.json` |

`app.json` (verbatim, the parts the host actually reads):

```json
{
  "name": "aidlc-console",
  "version": "0.1.0",
  "backend": { "hooks": { "routes": "backend.routes:register_routes" } },
  "ui": {
    "entry": "index.mjs",
    "pages": [ { "route": "/aidlc-console", "label": "AI-DLC", "icon": "GitBranch" } ],
    "sidebar": { "section": "Apps", "order": 10 }
  },
  "permissions": { "api": ["/api/apps/aidlc-console", "/api/apps/aidlc-console/*"], "storage": true, "network": false },
  "dependencies": { "optionalCommands": ["bun"] }
}
```

Two manifest traps the prototype's tests pin (both verified against `AppManifest` in `manifest.py`):

- `backend.routes` (the *base route path* string) must stay empty. Setting it switches the app onto the standalone-process proxy (`/apps/{name}/api/...` → `handle_app_api_proxy`) that shadows the in-process handlers.
- `ui.entry` is resolved by `AppHost` as `/apps/<name>/ui/<entry>`; it is relative to `ui/`, so `"index.mjs"` is correct and `"ui/index.mjs"` breaks.
- `mountFunction` defaults to `"mount"` in `UIPage` and is never called by the host; `AppHost` uses `React.lazy` on the module's **default export**.

---

## 1. UI patterns worth reusing (`ui/index.mjs`)

### 1.1 Module contract with the host

- Hand-authored ESM, no build step. `AppHost.tsx` does `lazy(() => import('/apps/${app.name}/ui/${entry}'))`; a parse error surfaces only as "Failed to load AI-DLC Console: Unexpected token". Validate with `node --check ui/index.mjs`. On this machine plain `node` is a broken nvm shim in non-interactive zsh; use `/Users/ychchen/.nvm/versions/node/v25.2.1/bin/node --check ui/index.mjs` (verified: syntax OK) or `bun` (`/Users/ychchen/.bun/bin/bun`, 1.3.11).
- Bare specifiers resolve through the host import map to `window.__kirocrew_modules` (`website/src/app-sdk/shared-modules.ts`). Keys available: `react`, `react-dom`, `react/jsx-runtime`, `lucide-react`, `@tanstack/react-query`, `@kirocrew/app-sdk`, `@kirocrew/ui`. The prototype imports only `react` and `react/jsx-runtime`.
- Served by `handle_app_ui_file` (`apps/routes.py`): only extensions in `_ALLOWED_EXTENSIONS` (`.mjs .js .css .json .svg .png .jpg .jpeg .gif .webp .woff .woff2 .ttf .map`), `Cache-Control: no-cache` normally and `no-store` when the app is in dev mode (`kirocrew app dev <name>` / `--off`). Dev mode also broadcasts `mc:app-reload` and `AppHost` does a full `window.location.reload()`.
- `/apps/<name>/ui/...` is exempt from token auth (`_APPS_UI_BYPASS_RE = r"^/apps/[a-z0-9][a-z0-9_-]*/ui/"` in `token_auth.py`), so the bundle loads before/without a cookie; API calls are not exempt.

### 1.2 Host feature detection (reuse verbatim)

```js
const host = (typeof window !== 'undefined' && window.__kirocrew_modules) || {}
const ui  = host['@kirocrew/ui'] || {}
const sdk = host['@kirocrew/app-sdk'] || {}
// later: if (ui.Card) ...; const api = sdk.useAppApi ? sdk.useAppApi() : null
```

Feature-detected rather than imported so an older host renders a plain-but-working page instead of failing module load. Real `@kirocrew/ui` exports (`website/src/kirocrew-ui/index.ts`): `Card, CardTitle, Btn, SendBtn, Input, SearchInput, Badge, SourceBadge, StatCard, Skeleton, ContentSkeleton, EmptyState, PageHeader, Toggle, InfoTip, SegmentedControl, MarkdownRenderer`. The prototype uses `ui.Card`, `ui.Btn`, `ui.Input` with fallbacks. Note `Card` fallback ignores `style` when the host `Card` is present — Studio should not rely on per-instance style overrides of host components.

### 1.3 `el()` helper (reuse verbatim)

```js
import { jsx as _jsx, jsxs as _jsxs } from 'react/jsx-runtime'
const el = (type, props, ...kids) => {
  const p = props || {}
  if (kids.length === 0) return _jsx(type, p)
  if (kids.length === 1) return _jsx(type, { ...p, children: kids[0] })
  return _jsxs(type, { ...p, children: kids })
}
```

Children passed as an array must carry `key`s (the prototype does this for lists).

### 1.4 Theme tokens

Prototype token map `T` (with fallbacks that only apply outside the host):

```js
bg: 'var(--bg, #0f1014)', card: 'var(--card, #1a1b26)', border: 'var(--border, #2d2f3d)',
text: 'var(--text, #e6e6ef)', muted: 'var(--muted, #8b8fa3)', accent: 'var(--accent, #7c3aed)',
accentTint: 'rgba(124,58,237,.14)', ok: 'var(--ok, #16a34a)', danger: 'var(--danger, #dc2626)'
```

Verified against `website/src/index.css`: every theme (`:root`/`[data-theme="dark"]`, `light`, `monokai-*`, `solarized-*`, `amber-*`, `nord-*`, `dracula-*`, `rosepine-*`, `catppuccin-*`, `tokyonight-*`, `gruvbox-*`, `ice-*`) defines `--bg --bg-accent --bg-elevated --bg-hover --card --card-fg --card-hl --panel --panel-strong --text --text-strong --muted --muted-fg --muted-strong --border --border-strong --border-hover --accent --accent-fg --accent-hover --accent-subtle --ok --ok-fg --ok-subtle --warn --warn-fg --warn-subtle --danger --danger-fg --danger-subtle`. Two prototype choices to **fix** in Studio: it hardcodes amber (`#b45309` / `#fef3c7`) for gates and `rgba(124,58,237,.14)` / `rgba(220,38,38,.12)` tints; the host provides `--warn`, `--warn-subtle`, `--accent-subtle`, `--danger-subtle` for exactly this. The theme attribute is `data-theme` on `<html>`; `sdk.useTheme().mode` returns `root.dataset.theme` raw (e.g. `"monokai-dark"`, not just `"dark"|"light"` as its type claims).

Stage-state presentation map (worth keeping as the single source of truth for checkbox marks):

```js
STAGE_STYLE = { completed:'done', in_progress:'running', awaiting_approval:'gate open', revising:'revising', skipped:'skipped', not_started:'pending', unknown:'?' }
SEVERITY_STYLE keys: action | warn | info
```

### 1.5 Polling and data flow

- `POLL_MS = 30000`; `useEffect` calls `load()` immediately then `setInterval(load, POLL_MS)`, cleared on unmount. Single `GET /board` feeds the whole page; per-repo/intent detail routes exist but the UI never calls them.
- No WebSocket use. `AppHost` currently bridges `subscribe(event)` to `window` `CustomEvent`s named `mc:app:<event>` only (TODO in `AppHost.tsx`), so polling remains the only live-update path unless Studio adds its own SSE/WS.
- Sidebar badge: `sdk.useNavBadge()(count)` dispatches `new CustomEvent('mc:app:badge', {detail:{appName,count}})`. Toast: `sdk.useNotify()` dispatches `mc:notify`. Navigation: `sdk.useNavigate()`.

### 1.6 How the API is called, and auth

`request()` in `AidlcConsole`:

```js
const url = `${API_BASE}${path}`                       // API_BASE = '/api/apps/aidlc-console'
if (api && !init) return api.get(url)
if (api && init && init.method === 'POST' && api.post) return api.post(url, init.body)
if (api && init && init.method === 'DELETE' && api.delete) return api.delete(url)   // BUG: SDK method is `del`
const resp = await fetch(url, { method, headers: body ? {'Content-Type':'application/json'} : undefined, body: JSON.stringify(body) })
const text = await resp.text(); const payload = text ? JSON.parse(text) : {}
if (!resp.ok) throw new Error(payload.error || `HTTP ${resp.status}`)
```

- **Auth transport is the dashboard session cookie, nothing else.** No `Authorization` header, no explicit `credentials` option (default `same-origin` suffices). Cookie name is `mc_token_<port>` (`token_auth.py:1492`, `ACCESS_COOKIE_PREFIX = "mc_token_"` in `refresh_tokens.py`), validated by the gateway middleware which then sets `request["user"] = user_id` and `request["app"] = app_name` (`token_auth.py:2027`). For a browser session `app_name` is empty, so `_enforce_app_scope` is a no-op; the manifest `permissions.api` allowlist is enforced server-side only for *app tokens* (minted via `.app_secret`).
- The SDK path (`createScopedApi` in `app-sdk/index.ts`) normalizes the URL (`new URL(path,'http://localhost').pathname`), rejects absolute/protocol-relative/backslash URLs, and allows `normalized === p || normalized.startsWith(p + '/')` for each declared prefix. The `"/api/apps/aidlc-console/*"` entry is therefore redundant on the frontend (the literal `*` never matches); `"/api/apps/aidlc-console"` alone covers the subtree.
- SDK API surface: `get(path, init)`, `post(path, body)`, `put`, `patch`, `del(path)`. Non-2xx throws `Error("API <status>: <text>")`; empty body / 204 / 205 returns `undefined`. Because the prototype tests `api.delete` (undefined), every DELETE silently falls back to raw `fetch` — harmless, but Studio should use `api.del`.
- **401 handling: none.** Both paths just throw; `load()` catches and renders `Could not load the board: <message>` in a `Card`. There is no redirect, no refresh attempt, no banner. The host's own calls go through `j()` in `api/client.ts` (`checkSessionExpired(r)`, `removeAuthBanner()` on 2xx, `attemptSilentRefresh()` → `POST /api/auth/refresh`, and a fixed red `#mc-session-expired` banner with `mc-auth-required`/`mc-auth-cleared` window events). App code cannot import `client.ts`, but it can listen for `mc-auth-required` to suppress its own error noise, and should treat `API 401`/`403` as "session expired" rather than as a board error.

### 1.7 Backend route registration contract (reuse verbatim)

```python
from kiro_crew.apps.context import AppContext
from kiro_crew.apps.route_registry import AppRoute
def register_routes(ctx: AppContext) -> list[AppRoute]: ...
# AppRoute(method: str, path: str, handler: Callable[[web.Request, AppContext], Awaitable[web.Response]])
```

- Paths are relative and mounted under `/api/apps/<name>` by `RouteRegistry` (catch-all `"*", "/api/apps/{app_name}/{path:.*}"`); `{param}` segments become `([^/]+)` and are injected into `request.match_info`. Exact matches win over patterns. Enable/disable does not need a gateway restart (soft routing table), but a changed `routes.py` needs disable→enable (or restart) because `unload_app_modules` only runs on deregister.
- Module loading: `load_app_module` uses `spec_from_file_location` with **no `sys.path` injection**, module name `_kirocrew_app_<name>.<dotted>`; sibling imports inside `backend/` are therefore not importable — hence the single-file design. Third-party code runs in-process with full gateway privileges and needs the `agent.apps_trusted` grant (or `apps_allow_third_party`) to be enabled; otherwise `ImportError("Refusing to load third-party app ...")`.
- The prototype wraps every handler with `_authenticated()` which sets `guarded.__kirocrew_authenticated__ = True` and returns `401 {"error":"unauthorized","code":"unauthorized"}` when `request.get("user") is None`. Tests assert the attribute on every route so a new route cannot ship unguarded — keep this pattern.
- `AppContext` (dataclass): `name, data_dir, config, logger, cron, events, storage, spawn, health`. `storage` is `None` unless `permissions.storage` is true; the prototype returns `[]` / raises `HTTPServiceUnavailable` in that case.

---

## 2. Backend parsing worth reusing (`backend/routes.py`)

### 2.1 Grammar constants (verbatim)

```python
_ENGINE_DIRS = (".kiro", ".claude", ".codex", ".aidlc", ".cursor")
_LEGACY_STATE_REL = "aidlc-docs/aidlc-state.md"
_MAX_STATE_BYTES = 512 * 1024; _MAX_JSON_BYTES = 4 * 1024 * 1024; _MAX_AUDIT_TAIL_BYTES = 48 * 1024
_MAX_REPOS = 64; _MAX_INTENTS_PER_REPO = 256
_STORAGE_KEY_REPOS = "repos"

_SECTION_RE    = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_SUBSECTION_RE = re.compile(r"^###\s+(?P<title>.+?)\s*$")
_FIELD_RE      = re.compile(r"^-\s+\*\*(?P<key>[^*]+?)\*\*:\s*(?P<val>.*?)\s*$")
_STAGE_RE = re.compile(
    r"^-\s+\[(?P<mark>[ \-xXsSrR?])\]\s+(?P<slug>[a-z0-9][a-z0-9._-]*)"
    r"(?:\s*(?:—|–|--)\s*(?P<mode>[A-Za-z_]+))?\s*$")
_PHASE_HEADER_RE = re.compile(r"^(?P<phase>[A-Z][A-Z ]*?)\s+PHASE\s*$")
_INTENT_DIR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"); _SPACE_RE = _INTENT_DIR_RE
_STAGE_MARKS = {" ":"not_started","-":"in_progress","?":"awaiting_approval","r":"revising","x":"completed","s":"skipped"}
_DONE_STATES = ("completed", "skipped")
```

Cross-check against the engine writer (installed 33-stage `aidlc-lib.ts`): `parseStateStageSuffixes` uses `/^- \[[ xSR?-]\] (\S+)\s*—\s*(EXECUTE|SKIP)\b/gm`, and `aidlc-utility.ts` writes the legend `<!-- Checkbox states: [ ] not started, [-] in progress, [?] awaiting approval (gate open), [R] revising (user rejected gate), [x] completed, [S] skipped via --stage/--phase jump -->` (the `init` header in `aidlc-utility.ts:3273` writes the shorter 4-state legend, which is what DevDelta's real file carries). The prototype regex is a strict superset (also accepts `X`, `s`, `r`, en dash, `--`, and a missing suffix). The `_STAGE_RE` is only applied inside the `## Stage Progress` section, so `Per unit: [TBD]` noise lines cannot become stages.

### 2.2 `parse_state_file(text) -> dict` output shape

```python
{
  "sections": {section_title: {field_key: value}},     # every `## X` section, unknown ones preserved
  "project": sections["Project Information"], "plan": sections["Execution Plan Summary"],
  "current": sections["Current Status"],  "resume": sections["Session Resume Point"],
  "phases": [{"name": "Initialization", "status": "Verified"}, ...],    # from `## Phase Progress`
  "stages": [{"slug","mark","state","phase","mode"}],   # phase = lowercased text before " PHASE" in the `###` header, or None
  "counts": {"total", "not_started","in_progress","awaiting_approval","revising","completed","skipped","done"}
}
```

Empty trailing values survive as `""` (e.g. `Review Override`, `Worktree Path`). Nothing is validated against a hard-coded stage list — required because the checkout's 32-stage engine has `application-design` where the installed 33-stage engine has `domain-design` + `contract-design`.

Real 33-stage graph (from `/Users/ychchen/warren_ws/DevDelta/.kiro/tools/data/stage-graph.json`, keys per entry: `slug number name phase execution condition lead_agent support_agents mode produces consumes requires_stage sensors scopes inputs outputs rules_in_context sensors_applicable`):

- initialization: 0.1 workspace-scaffold, 0.2 workspace-detection, 0.3 state-init
- ideation: 1.1 intent-capture, 1.2 market-research, 1.3 feasibility, 1.4 scope-definition, 1.5 team-formation, 1.6 rough-mockups, 1.7 approval-handoff
- inception: 2.1 reverse-engineering, 2.2 practices-discovery, 2.3 requirements-analysis, 2.4 user-stories, 2.5 refined-mockups, 2.6 domain-design, 2.7 units-generation, 2.8 contract-design, 2.9 delivery-planning
- construction: 3.1 functional-design, 3.2 nfr-requirements, 3.3 nfr-design, 3.4 infrastructure-design, 3.5 code-generation, 3.6 build-and-test, 3.7 ci-pipeline
- operation: 4.1 deployment-pipeline, 4.2 environment-provisioning, 4.3 deployment-execution, 4.4 observability-setup, 4.5 incident-response, 4.6 performance-validation, 4.7 feedback-optimization

### 2.3 `compute_findings(...)` — signature and codes

```python
compute_findings(state, *, directive=None, state_digest=None, registry_status=None,
                 graph_slugs: set[str] | None = None, goal_stop: bool = False) -> list[dict]
# each finding: {"code", "severity", "message", **extra}
```

| code | severity | extra keys | trigger |
|---|---|---|---|
| `awaiting_approval` | action | `stages` | any stage state `awaiting_approval` |
| `revising` | action | `stages` | any stage state `revising` |
| `goal_stop` | warn | — | `.aidlc-goal-stop` exists in the intent dir |
| `stale_directive` | warn | `directive_stage, recorded_digest[:12], actual_digest[:12]` | `directive["state_sha256"] != sha256(state file)` |
| `completed_count_mismatch` | warn | `claimed, actual` | `plan["Completed"]` ≠ count of `[x]` (note: `[S]` is not counted as completed here) |
| `total_count_mismatch` | warn | `claimed, actual` | `plan["Total Stages"]` ≠ parsed stage lines |
| `phase_stage_disagreement` | warn | `lifecycle_phase, phase_progress` | `Current Status → Lifecycle Phase` is `Pending`/absent in `Phase Progress` |
| `registry_status_mismatch` | info | `registry_status, state_status` | `intents.json status == "complete"` while state `Status != "Completed"` |
| `stage_graph_drift` | warn | `only_in_state, only_in_graph` | state slugs ≠ `stage-graph.json` slugs |

Digest semantics verified against the installed engine: `stateContentSha256 = createHash("sha256").update(stateContent,"utf-8")` on the whole file string, and `readActiveDirectiveMarker` returns `null` (treats the marker as absent) when the digest mismatches. The prototype's `_sha256_file` hashes raw bytes, which is equal for UTF-8 files without a BOM, so `stale_directive` is a faithful observation. Live example: DevDelta intent `260828-kiro-impact-poc` has directive `{"version":1,"stage":"build-and-test","state_sha256":"7eca6ee6…"}` while the state file hashes to `e6c81314…` — the board currently reports `stale_directive` for that intent, correctly.

Marker type from the installed engine: `interface ActiveDirectiveMarker { version: 1; stage: string; unit?: string; state_sha256: string }`, stage validated by `/^[a-z][a-z0-9-]*$/`, digest by `/^[0-9a-f]{64}$/`, written atomically with a trailing newline.

### 2.4 Discovery functions and shapes

```python
detect_engines(repo) -> [{"dir","harness","rules_subdir","stage_count","stage_slugs","has_utility","marker_mtime"}]
scan_intents(repo) -> (intents, layout)  # layout in {"spaces","legacy",None}
# intent row: {"space","dir_name","slug","uuid","scope","registry_status","is_active","state_path","updated"}
_intent_paths(repo, space, dir_name) -> Path|None   # regex + resolve().is_relative_to(repo) guard
load_intent_detail(repo, intent) -> {**intent, "state","findings","directive","recovery","audit_tail","error"}
_summarize(detail) -> board row: {space,dir_name,slug,scope,registry_status,is_active,updated,error,project,
                                  lifecycle_phase,current_stage,next_stage,status,next_action,counts,phases,findings}
_validate_repo_path(raw) -> (Path|None, err|None)   # expanduser, absolute, resolve, is_dir, is_sensitive_path
_repo_id(resolved: Path) -> str                     # hashlib.sha256(str(resolved).encode()).hexdigest()[:12]
```

- Engine marker: `<repo>/<dir>/tools/data/harness.json` *or* `<repo>/<dir>/tools/aidlc-utility.ts`. Real `harness.json` shapes: DevDelta `.kiro` → `{"name":"kiro","harnessDir":".kiro","rulesSubdir":"steering"}`, `.claude` → `{"name":"claude","harnessDir":".claude","rulesSubdir":"rules"}`; the checkout's `dist/kiro` writes `{"harnessDir":".kiro","rulesSubdir":"steering"}` **without** `name` (so `harness` can be `None`; the UI falls back to `e.dir`).
- Intent registry `intents.json` is a JSON list of `{uuid, slug, dirName, scope, status}` (`IntentRegistryEntry` also allows `repos?: string[]`; `dirName` is optional in old rows). Real values seen: `status` ∈ `in-flight`, `complete`; `scope` ∈ `poc`, `review-major-remediation`, `feature`. Cursors: `<repo>/aidlc/active-space` (`default`) and `<space>/intents/active-intent` (`260828-kiro-impact-poc`) — per-user, gitignored.
- `is_active` requires `dir_name == active-intent` and (no `active-space` cursor or `space == active-space`).
- Intent dirs starting with `.` are skipped (e.g. `.aidlc-hooks-health`); a dir without `aidlc-state.md` is skipped.
- Read caps refuse oversized files rather than truncate (`_read_text_capped` returns `None`); audit is tail-read (`_tail_lines`, last 80 non-empty lines of the newest `audit/*.md` shard; real shard seen: `audit/603e5f4a8072-26e77d17b1cd.md`, 163 KB).
- Companion files in a real intent dir (DevDelta): `.aidlc-active-directive.json`, `.aidlc-recovery.md` (`# AIDLC Recovery Breadcrumb` / `**Last validated**` / `**Current stage**` / `**State file**`), `.aidlc-goal-stop` (key=value lines: `completed_at=…`, `intent=…`, `state=8/8`, `ledger=T000-T120 complete`), `.aidlc-human-turn` and `.aidlc-engine-touch` (single ISO timestamp each; engine rule: conversational ⇔ `mtime(.aidlc-human-turn) > mtime(.aidlc-engine-touch)`), `.aidlc-hooks-health/`, `.aidlc-sensors/`, `runtime-graph.json`, and `.aidlc-steering-token-key` (mode 0600 — **a secret; never read or copy**). The prototype reads only the first three plus the state file and audit.
- `is_sensitive_path` (`kiro_crew.security`) blocks `.aws .ssh .gnupg .gpg .config/gcloud .azure .docker/config.json .kube/config .npmrc .pypirc .netrc .git-credentials .kiro/crew-auth-staging` and the crew data-home secret leaves; the prototype fails **closed** (`is_sensitive_path = lambda p: True`) if the module cannot be imported.

### 2.5 Handlers and JSON contracts (for parity tests)

| Route | Success | Errors (`{"error","code"}`) |
|---|---|---|
| `GET /health` | `{"app","read_only":true,"security_module","storage","registered_repos","bun": shutil.which("bun")}` | — |
| `GET /board` | `{"totals":{repos,repos_unavailable,intents,in_flight,awaiting_approval,findings},"repos":[...],"read_only":true}` | — |
| `GET /repos` | `{"repos":[{id,path,label,added_at,available,error,layout,engines(no stage_slugs),intents:int}]}` | — |
| `POST /repos` `{"path","label"?}` | 201 `{id,path,label,layout,intents:int,engines}` | 400 `bad_body`/`bad_path`/`not_aidlc_repo`, 409 `too_many`/`duplicate` |
| `DELETE /repos/{repo_id}` | `{"removed": id}` | 404 `not_found` |
| `GET /repos/{repo_id}/intents` | `{"repo":{id,path,label},"layout","intents":[summary]}` | 404 `not_found`, 409 `unavailable` |
| `GET /repos/{repo_id}/intents/{intent}` | `{"repo","layout","intent": detail}` | 404 `not_found`, 400 `bad_path`, 409 `unavailable` |

`totals.in_flight` counts intents whose `registry_status` is `"in-flight"`; `totals.findings` counts only `warn`+`action` findings.

---

## 3. Registry storage schema (input to FR-MIG-002/003)

### 3.1 Exact location and shape

`AppStorage` (`kiro_crew/apps/app_storage.py`) is file-per-key: `~/.kiro/crew/apps/{app_name}/data/kv/{key}.json`, written with `atomic_write(path, json.dumps(value, indent=2))` and audited via `sel().log_api_access(caller="app:<name>", operation="app_storage.set")`. Keys are rejected if empty, containing `..` `/` `\\`, or starting with `.`/`~`. `get()` returns `None` when absent **or** when the JSON is corrupt.

The prototype's whole persisted state is one key, `repos`:

```
/Users/ychchen/.kiro/crew/apps/aidlc-console/data/kv/repos.json
```

```json
[
  {
    "id": "75d6cb9ecb67",
    "path": "/Users/ychchen/warren_ws/devdelta",
    "label": "devdelta",
    "added_at": "2026-09-04T09:48:49Z"
  }
]
```

Row schema (from `handle_add_repo`): `id: str` (12 hex), `path: str(Path.resolve())`, `label: str` (`body.label or resolved.name`, stripped, ≤120 chars), `added_at: "%Y-%m-%dT%H:%M:%SZ"` UTC. Nothing else is stored (no archive metadata, no preferences, no history) — so for FR-MIG-002 "compatible action-free history" is empty and "preferences" is empty; only the repo registry exists.

The `data/` dir is derived by `kiro_crew.apps.manager.app_data_dir(name) = config_dir()/"apps"/name/"data"`; `config_dir()` is `~/.kiro/crew` here. Studio's own storage will be `~/.kiro/crew/apps/aidlc-studio/data/kv/<key>.json`.

### 3.2 Repo id derivation and its hazard

`_repo_id(resolved) = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12]` where `resolved = Path(raw).expanduser().resolve()`.

Verified on this machine: the stored path is the **lowercase** `/Users/ychchen/warren_ws/devdelta` (what the user typed), but the directory is `DevDelta`. macOS APFS is case-insensitive, `Path.resolve()`/`os.path.realpath` do **not** case-normalize, so:

- `sha256("/Users/ychchen/warren_ws/devdelta")[:12]` = `75d6cb9ecb67` (stored)
- `sha256("/Users/ychchen/warren_ws/DevDelta")[:12]` = `6e64d0745308`

The same repo can therefore be registered twice under two ids, and the prototype's duplicate check (`any(r["id"] == rid)`) would not catch it. This is precisely the gap FR-REP-003/005 close (realpath + `st_dev`/`st_ino`, here `16777229`/`155296931`, with Git common-dir as fallback). The migration (FR-MIG-003) must compute the **new** resolved identity from each old `path`, not re-hash the string, and must collapse rows that resolve to one identity.

### 3.3 Suggested migration read/write (all inside Studio's own storage)

1. Detect (FR-MIG-001): `Path(app_dir("aidlc-console")) / "data" / "kv" / "repos.json"` exists (survives a keep-data uninstall) and/or `get_app("aidlc-console")` is not `None`. Read it with `json.loads`; treat non-list or non-dict rows as ignorable, exactly as `_load_repos` does.
2. For each row: `resolved, err = validate(path)`; if `err`, keep as an *unavailable* record (FR-REP-009) carrying the old id/label/path so the user can rebind. Otherwise compute the Studio repo id + resolved identity, carry `label` and `added_at`, and record `legacy_console_id` for link rewriting.
3. Write under a versioned transaction key in Studio storage (e.g. `migration` = `{"version":1,"from":"aidlc-console","started_at","source_sha256","rows_in","rows_out","status"}`), plus a rollback copy of the raw source JSON (FR-MIG-004 "bounded rollback backup in App storage"); validate `rows_out == len(unique identities)` before writing the final `repos` key.
4. Never copy: `.app_secret` (per-app, regenerated by `install_app` via `write_app_secret(name, generate_app_secret())`), `~/.kiro/crew/.local_secret`, `app_receipt_secret`, anything under a registered repo's tree (`.aidlc-steering-token-key` in particular).

---

## 4. Test suite: fixtures, construction pattern, how to run

### 4.1 Result

```
cd /Users/ychchen/warren_ws/kirocrew-app-aidlc && /Users/ychchen/warren_ws/kirocrew/.venv/bin/python -m pytest tests -q
66 passed in 0.50s      (rootdir: /Users/ychchen/warren_ws/kirocrew-app-aidlc; no pytest config in the app dir)
```

Interpreter facts: Python 3.12.14, aiohttp 3.14.3, pytest 9.0.3 (plugins timeout, cov, asyncio 0.20.3 STRICT, hypothesis, split, xdist), `kiro_crew` importable from `/Users/ychchen/warren_ws/KiroCrew/src/kiro_crew/__init__.py` (same directory as `/Users/ychchen/warren_ws/kirocrew` on the case-insensitive FS). The README's `-o addopts=` is not required when running from the app dir. The two real-file drift tests ran (not skipped) because both trees exist.

### 4.2 Where the fixtures are

`tests/` has only three files; all fixture data is **inline in `tests/test_routes.py`**:

- `STATE_33` — 8-stage reduction of the real 33-stage DevDelta file (`domain-design` + `contract-design`, carries `Review Override`, `Worktree Path` empty, `Per unit: [TBD]` noise line, Total Stages 8 / Completed 4).
- `STATE_32_CONTRADICTORY` — 4-stage reduction of the real agentbridge 32-stage file (`application-design`, `[S]`, `[?]`, `[R]`, no `Review Override`) with the genuine contradiction `Phase Progress: Initialization Active / others Pending` vs `Current Status: OPERATION / Completed`, and `Completed: 20` vs one `[x]`.
- `STAGE_GRAPH_4` — `[{"slug","number","phase"}]` for the 4 slugs above.
- Real-file drift guard: `_REAL_STATE_FILES = [DevDelta/aidlc/spaces/default/intents/260828-kiro-impact-poc/aidlc-state.md, agentbridge/aidlc/spaces/default/intents/260626-hook-text-relay/aidlc-state.md]`, parametrized with `ids=lambda p: p.parent.name`, `pytest.skip` when absent, asserting `counts.total >= 30`, 5 phases, no `unknown` state, every stage has a phase.

### 4.3 How tests construct `AppContext` and requests (follow this pattern)

```python
# conftest.py — load exactly like the gateway (spec_from_file_location, no sys.path)
spec = importlib.util.spec_from_file_location("_aidlc_console_routes_under_test", str(_APP_ROOT/"backend"/"routes.py"))
module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
@pytest.fixture(scope="session") def routes(): return module
@pytest.fixture def app_root(): return _APP_ROOT

# test_routes.py
from aiohttp import web
from kiro_crew.apps.app_storage import AppStorage
from kiro_crew.apps.context import AppContext

class _Req:   # duck-typed stand-in for web.Request — NOT aiohttp's test client
    def __init__(self, match_info=None, body=None, user="tester"):
        self.match_info = match_info or {}; self._body = body
        self._data = {"user": user} if user is not None else {}
    def get(self, key, default=None): return self._data.get(key, default)
    async def json(self):
        if self._body is None: raise ValueError("no body")
        return self._body

def _ctx(tmp_path):
    data_dir = tmp_path / "appdata"; data_dir.mkdir(parents=True, exist_ok=True)
    return AppContext(name="aidlc-console", data_dir=data_dir, storage=AppStorage("aidlc-console", data_dir))

def _payload(resp: web.Response) -> dict: return json.loads(resp.text)

resp = asyncio.run(routes.handle_add_repo(_Req(body={"path": str(repo)}), ctx))   # handlers awaited directly
```

`_make_repo(root, *, engine=".kiro", state=STATE_33, intent_dir="260828-demo", space="default", registry_status="in-flight", stage_graph=None)` builds a synthetic v2 repo: `harness.json`, `stage-graph.json`, `aidlc/spaces/<space>/intents/<dir>/aidlc-state.md`, `intents.json` (`slug = intent_dir.split("-",1)[-1]`, `uuid` fixed), `active-intent`, `aidlc/active-space`.

`test_manifest.py` validates with the host's real validator: `AppManifest.from_dict(json.loads(app.json)).validate() == []`, and pins `name`, `version`, `backend.hooks.routes`, empty `backend.routes`, `ui.entry == "index.mjs"`, one page with `route == "/aidlc-console"`, `mountFunction == "mount"`, permissions (`storage` true, `network` false, no `spawn/cron/events/crons/mcpServers/agents`), `dependencies.optionalCommands` contains `bun` and `commands` empty, and `"export default function"` present in `ui/index.mjs`.

Test groups (66): parser (6), findings (11), discovery (10), path safety (7 incl. parametrized), routes (12), real-file drift (2 param). All synchronous via `asyncio.run`; no `pytest-asyncio` markers.

---

## 5. What the KiroCrew sidebar shows today, and how a rename avoids duplicates (FR-MIG-004/005)

### 5.1 How the sidebar entry is derived (code, not manifest)

`website/src/appNav.ts`:

- `isAppNavigable(app) = !!app.enabled && (app.manifest?.ui?.pages?.length ?? 0) > 0`
- For a non-builtin app the route is **always `/apps/<name>`**, id `app-<name>`, label `appPageLabel(name, page.label, displayName)` → manifest `label` (`"AI-DLC"`) unless an i18n key exists for that app name (none for `aidlc-*`), icon `page.icon` (`"GitBranch"`; for installed apps the lucide glyph lookup is builtin-only, so it falls back to `page.iconUrl` under `/apps/<name>/ui/` or the generic `Package` icon).
- The manifest `pages[0].route` (`/aidlc-console`) is **ignored** for installed apps; `ui.sidebar.section/order` are parsed by `UISidebar` but never read by the frontend (`grep` shows no consumer). The rail groups all apps under `group: 'Apps'` and persists user ordering in `localStorage['mc-app-nav-order']` as an array of ids (a stale `app-aidlc-console` entry there is harmless).
- `App.tsx` builds the list from `api.listApps()` (`GET /api/apps`) on mount, on `mc:apps-changed`, and on WS reconnect, with bounded retry; `dispatch(setEnabledAppIds(ids))`.
- `GET /api/apps` record = `InstalledApp.to_dict()` (`name version displayName enabled installedAt updatedAt source origin resources lifecycle schemaVersion migratedTo dev …`) + `manifest` (full `AppManifest.to_dict()`), plus `migratedTo` when non-empty, `orphaned: true` when flagged, `backend_status` when a process exists.
- `/apps/:name` → `AppPage` → `api.getApp(name)` → `AppHost`, which renders `AppDisabled` when `enabled === false`, `AppNoUI` when no `ui.entry`, else the lazy module inside `AppApiProvider(allowedApiPaths = manifest.permissions.api)`.

So today the rail shows one row: **"AI-DLC"** → `/apps/aidlc-console`, present because `installed.json.enabled == true` and the manifest has one page.

### 5.2 Why the host's builtin migration mechanism does not apply

`migratedTo` (`"registry:<name>"`/`"standalone:<name>"`), `orphaned`, `MigrationBanner`, `MigrationPage` (`/apps/migrate/:name`) and `DELETE /api/apps/{name}/migrate-cleanup` are **builtin-only**: `detect_orphaned_builtins` flags only `origin == "builtin"` dirs, `cleanup_migrated_builtin` requires the name in `kiro_crew.apps.builtins._MIGRATED_BUILTINS = ["deploy-web", "deploy_web"]`, and `MigrationCheck` filters `origin === 'builtin'`. `aidlc-console` has `origin: "registry"`, so none of this fires. Studio must implement FR-MIG itself.

### 5.3 Lifecycle facts that shape the rename

- `install_app(source)` keys everything on `manifest.name`; installing the Studio tree creates a **separate** `~/.kiro/crew/apps/aidlc-studio/` with its own `installed.json`, `.app_secret`, `data/`. It refuses if `aidlc-studio` is already installed; `update_app(source, expected_name=...)` refuses a name mismatch — a rename is *install new + retire old*, never an update.
- `_copy_app_tree` copies everything except `_COPY_IGNORE = ("node_modules", ".git", "__pycache__", ".venv")`, drops symlinks that escape the source root, and rewrites in-tree absolute symlinks to relative. `tests/`, `docs/`, `mockups/` would all be copied.
- `enable_app` for a non-builtin runs `app_admission_denied` and `app_execution_denied`; with `agent.apps_allow_third_party = false`, `aidlc-studio` needs its own entry in `agent.apps_trusted` (granted via the dashboard trust-consent modal / `GET|DELETE /api/security/trusted-apps`). Enabling then calls `on_app_enable` → `RouteRegistry.register_app_routes(name, app_dir, "backend.routes:register_routes", ctx)`.
- `disable_app` → `teardown_app_runtime` → `on_app_disable` deregisters routes and unloads `_kirocrew_app_<name>.*` modules; `installed.json.enabled=false`; the sidebar row disappears on the next `mc:apps-changed`/`listApps`. Disable does **not** touch the trust grant.
- `uninstall_app(name, keep_data=True)` (CLI `kirocrew app uninstall <name>`; API `POST /api/apps/{name}/uninstall`, body `{"purge_data": true}` to purge) first withdraws the `agent.apps_trusted` grant (aborts if it cannot), then removes the app dir but moves `data/` aside and back, leaving `~/.kiro/crew/apps/aidlc-console/data/kv/repos.json` with **no** `installed.json` — `list_apps` skips such dirs (`_read_installed` is `None`), so no sidebar entry and no routes remain, while the registry stays readable for rollback. `--purge-data` / `purge_data:true` deletes the directory entirely.
- CLI surface (`cli.py`): `kirocrew app install <dir> | list | enable <name> | disable <name> | uninstall <name> [--purge-data] | info <name> | dev <name> [--off] | init … | mcp <name>`.

### 5.4 Sequence that satisfies FR-MIG-004/005 with no duplicate rail entries

1. Install `aidlc-studio` (new dir, `enabled: false` by default). Nothing is shown yet.
2. Grant trust to `aidlc-studio` and enable it. **At this instant both rows ("AI-DLC" → `/apps/aidlc-console`, and Studio's label → `/apps/aidlc-studio`) are visible** because both are enabled with a page. To honour FR-MIG-005, Studio's first render must detect the prototype (§3.3 step 1) and present the migration preview (FR-MIG-001) before exposing any control surface for a repo that `aidlc-console` also lists.
3. After Studio validates the migrated registry (row counts, identities): `POST /api/apps/aidlc-console/disable` (or Studio's own migration route calling `kiro_crew.apps.manager.disable_app` + `hooks_integration.on_app_disable` is equivalent but should go through the HTTP handler to keep teardown ordering), then `POST /api/apps/aidlc-console/uninstall` with the default keep-data body. This removes routes, the rail entry, the manifest, and the `agent.apps_trusted` grant, and leaves `data/kv/repos.json` as the on-disk rollback source. Dispatch `window.dispatchEvent(new Event('mc:apps-changed'))` (or rely on the host, which fires it after lifecycle calls) so the rail refreshes.
4. Rollback = re-run `kirocrew app install /Users/ychchen/warren_ws/kirocrew-app-aidlc` (install preserves the stranded `data/`), re-grant trust, enable; disable Studio first to keep FR-MIG-005.
5. Alternative that avoids even a transient double row: disable `aidlc-console` *before* enabling `aidlc-studio`, and have Studio read the prototype registry from disk (it survives disable) — the preview then runs with only one app visible. Choose this if the PRD's "never show both" is read strictly.

---

## 6. Carry-forward recommendations

| Component | Verdict | Notes |
|---|---|---|
| `parse_state_file`, all grammar regexes, `_STAGE_MARKS`, `_DONE_STATES` | **Carry forward verbatim** | Superset of the engine writer grammar; version-agnostic. Keep the "unknown sections preserved" rule. |
| `compute_findings` and the 9 codes | **Carry forward**, extend | Add `unit` from the directive marker; consider treating `[S]` in the completed reconciliation per engine semantics (engine counts `Completed` as `[x]` only — matches today). |
| `detect_engines`, `scan_intents`, `_intent_paths`, `_read_*_capped`, `_tail_lines`, `_mtime_iso` | **Carry forward** | Add `scope-grid.json` (dict `{scope: {"stages": {slug: "EXECUTE"|"SKIP"}}}`) for the Plan Composer; `harness.json` may lack `name`. |
| `_repo_id` (`sha256(str(path))[:12]`) | **Replace** | Case/alias hazard (§3.2). Keep only as `legacy_console_id` during migration. |
| `_validate_repo_path` + `is_sensitive_path` fail-closed shim | **Carry forward** | Add device/inode + git common-dir capture. |
| `_authenticated` wrapper + `__kirocrew_authenticated__` test | **Carry forward** | Studio adds many POST routes; the "every route is guarded" test is the cheapest safety net. |
| Single-file `backend/routes.py` | **Reconsider** | Forced by `spec_from_file_location` without `sys.path`; Studio's §13.1 modules can still live in one package if they are imported via `importlib.util` relative to `Path(__file__).parent`, or by inlining. Verify against `load_app_module` before splitting files. |
| `el()`, host feature detection, `STAGE_STYLE`/`SEVERITY_STYLE`, polling skeleton | **Carry forward** | Replace hardcoded amber/tint colours with `--warn`/`--*-subtle` tokens; use `api.del`; listen for `mc-auth-required`. |
| `AppStorage` `repos` list under key `repos` | **Migrate** | Into Studio's richer `Repository` record (FR-REP-004) under Studio's own `data/kv`. |
| `tests/` pattern (`_Req`, `_ctx`, `_make_repo`, inline fixtures, real-file drift guard) | **Carry forward** | Add fixtures for the 33-stage legend with `[?]`/`[R]`, `scope-grid.json`, directive with `unit`, and a case-alias duplicate path test. |
| `mockups/`, `docs/` in app root | **Move out or exclude** | They ship with `install` today. |

---

## 7. Open questions (not resolvable from code)

1. **Which AI-DLC build produced DevDelta's 33-stage engine?** The `aidlc-workflows` checkout (HEAD 2026-07-08, v2.2.10) has 32 stages and no `ACTIVE_DIRECTIVE_MARKER`; DevDelta's `.kiro/tools` (installed 2026-08-28) has 33 stages, `aidlc-directive.ts`, `.aidlc-human-turn`/`.aidlc-engine-touch` markers. Other research docs written from the checkout may describe stale formats; the bundled-engine version Studio ships (FR-DIST-008) needs to be pinned to a known source.
2. **Strictness of FR-MIG-005 during the enable window** — is a transient second rail row acceptable while the migration preview is shown (§5.4 step 2), or must `aidlc-console` be disabled before Studio is enabled (step 5)?
3. **Where the rollback backup should live** if `aidlc-console` is fully uninstalled with purge: Studio storage only (`~/.kiro/crew/apps/aidlc-studio/data/kv/...`) or also the stranded prototype `data/`? Code allows both; the PRD says "in App storage".
4. **Trust-grant UX for the rename**: enabling `aidlc-studio` requires a new `agent.apps_trusted` entry via the consent modal; whether Studio's migration flow may request it programmatically (or must wait for the user) is a product decision.
5. **Resolved identity for case-aliased paths on APFS**: FR-REP-003 says realpath + device/inode; `os.path.realpath` keeps the typed case, so the canonical *display* path for `devdelta` vs `DevDelta` must be chosen (inode is identical). Suggest `os.fsdecode(os.path.realpath(p))` plus a directory-listing case fix-up, but this is unspecified.
6. **Whether `[S]` should count toward `Completed`** in `completed_count_mismatch` — the engine's `Completed` tally semantics were not located in the installed `aidlc-utility.ts` during this pass.

---

## Critic addendum (2026-09-04, completeness pass)

### A1. §7 Q1 answered — which build produced DevDelta's engine

AI-DLC **2.6.2**, source tree `/Users/ychchen/warren_ws/aidlc/wt-v2-triage` (git `4569754e`, `core/tools/aidlc-version.ts:4`), whose committed `dist/kiro/**` (265 files) is byte-identical to DevDelta's `.kiro/tools` except the composer-added scope in `scope-grid.json` (doc 04 §0). That tree is the payload Studio should vendor (FR-INST-003) and the version to pin (FR-DIST-008).

### A2. §1.7 / §6 "single-file backend" is a 0.3.0 constraint

The running gateway (0.5.0-insider.9) registers namespace packages in `apps/module_loader.py:81-113`, so `from . import x` works there (doc 01 addendum A1). The prototype's `tests/conftest.py` loader does **not** register parents, so a Studio test suite must either replicate the bundle loader or use doc 08 §7.2's `_sibling()` helper. Decision recorded in `00-index.md`.

### A3. §7 Q6 partially answered — `Completed` semantics

Per doc 04 §3.1 (2.6.2 `aidlc-utility.ts`/`aidlc-state.ts`), the `Completed` counter is resynced to the count of `[x]` rows by `checkbox`/`advance`/`finalize`; `[S]` rows are not counted. The prototype's `completed_count_mismatch` (which also counts only `[x]`) is therefore faithful to the engine.

### A4. Facts this doc states that other docs contradict (code resolution)

- §1.1 "Keys available: … `@tanstack/react-query`, … `@kirocrew/ui`" — these are `window.__kirocrew_modules` keys, not import-map specifiers (doc 03 addendum A2).
- §1.5 "Toast: `sdk.useNotify()`" — no host listener exists (doc 03 addendum A1).
- §2.4 `.aidlc-goal-stop` — confirmed not an AI-DLC file (doc 04 §3.11); keep the finding but label its source "agent-written".

### A5. Migration inputs confirmed

The only prototype state is `~/.kiro/crew/apps/aidlc-console/data/kv/repos.json` (one row today) plus the trust grant `agent.apps_trusted = ["aidlc-console"]` in `~/.kiro/crew/config.json`. `install_app`/`uninstall_app(keep_data=True)` semantics in §5.3 hold in the 0.5.0 bundle (`apps/manager.py`; bundle signature `install_app(source, *, expected_name=None, source_repository="")`, doc 01 §7.1).
