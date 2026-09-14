# 08 — Registry publishing, trust, and code conventions for AI-DLC Studio

Research reader notes for implementing `aidlc-studio` as a **non-builtin, registry-listed KiroCrew app** that needs in-gateway Python hooks routes, storage, events, spawn (Advisor), and a rich UI, while satisfying PRD §9.1 (FR-DIST-001..008), §16.1 (narrow per-App trust) and §17 (`minKiroCrewVersion`, en-US/zh-CN parity).

Sources read (code wins over docs wherever they disagree; disagreements are called out inline):

- KiroCrew `docs/app-kit/publishing-guide.md`, `manifest-reference.md`, `getting-started.md`, `api-reference.md`
- `docs/request-for-change/rfc-appstore-official-registry.md`
- `docs/system-specs/modules/security.md`, `app-kit-platform.md`; `docs/system-specs/common/{code-style,error-handling,testing-conventions}.md`; `docs/ci/i18n-gates.md`; `website/AGENTS.md`; `AUTOSDE.yaml`
- `src/kiro_crew/apps/{app-registry.json,registry.py,official_catalog.py,official_editorial.py,admission.py,execution.py,module_loader.py,route_registry.py,hooks_integration.py,manifest.py,permissions.py,version.py,context.py,app_storage.py,event_bus.py,spawn_sdk.py,manager.py,routes.py,scaffold.py,discovery.py}`
- `src/kiro_crew/dashboard/{token_auth.py,handlers/security.py,routes/system.py}`, `src/kiro_crew/config/loader.py`
- Builtin reference app `src/kiro_crew/apps/builtins/ops_mission_control/**` plus `website/src/apps/ops-mission-control/**`, `website/src/apps/builtinRegistry.ts`, `website/src/components/{AppHost.tsx,appstore/appManifest.ts,appstore/TrustAppModal.tsx,appstore/types.ts}`, `website/src/app-sdk/index.ts`, `website/vite.config.ts`
- Tests: `test/test_trusted_apps_api.py`, `test/test_app_execution.py`, `test/test_app_denial_code.py`, `test/test_error_code_contract.py`, builtin `tests/`
- Existing prototype `/Users/ychchen/warren_ws/kirocrew-app-aidlc/{app.json,README.md,backend/routes.py,tests/*}`

KiroCrew checked out at `79b6c1984039de690cd0004762a70be3fb4197aa` (2026-08-14), `kiro_crew.__version__ == "0.3.0"`.

---

## 0. Executive summary (what an implementer must get right)

1. **The registry entry is tiny; `app.json` carries everything else.** Bundled registry is `src/kiro_crew/apps/app-registry.json`, a bare JSON array. Our entry needs `name`, `gitUrl`, `repo`, `branch`, `subdirectory`. `repo` is NOT optional in practice: `_merge_manifest` only rewrites `iconPath`/`screenshots`/`heroImage*` to blob-proxy URLs when `entry.get("repo")` is truthy.
2. **Trust is per-app by name.** `agent.apps_trusted: ["aidlc-studio"]` in `config.json` (written by `POST /api/security/trusted-apps/{name}`) admits exactly our Python hooks/backend/scripts; the blanket `agent.apps_allow_third_party: true` is never needed. The UI grants it via `TrustAppModal` when Install/Enable returns `code: "app_execution_denied"`.
3. **Untrusted hooks routes never load.** `enable_app()` refuses with `AppResult(error_code="app_execution_denied")` before writing `enabled=true`; `on_app_enable`/`on_gateway_startup` skip route registration and disarm crons; `module_loader.load_app_module` raises `ImportError` if the gate denies at load time.
4. **`module_loader` gives you ONE file with no importable siblings.** Empirically verified: `from . import x` and `from backend import x` both raise `ModuleNotFoundError`; a sibling loader built on `importlib.util.spec_from_file_location(f"{__name__.rsplit('.',1)[0]}.{name}", Path(__file__).parent / f"{name}.py")` works. No builtin uses it (builtins import absolutely as `kiro_crew.apps.builtins.<pkg>...`), and no shipped test does either — the pattern is ours to establish and to test.
5. **The builtin route contract is NOT ours.** Builtins export `register_routes(app: web.Application) -> None` from the package `__init__` and add full `/api/apps/<name>/...` paths on the gateway router at boot (`dashboard/routes/system.py`). Third-party apps declare `backend.hooks.routes = "backend.routes:register_routes"` whose callable is `register_routes(ctx: AppContext) -> list[AppRoute]` with paths RELATIVE to `/api/apps/<name>`. Setting `backend.routes` (a URL base string) instead of `backend.hooks.routes` does not turn on hooks — the prototype's `test_uses_in_process_route_hook_only` pins this.
6. **`permissions` are mostly advisory**, but three fields are real gates: `permissions.api` (deny-by-default HTTP app-token scope in `token_auth.py` and client-side `useAppApi` path check), `permissions.spawn` (`ctx.spawn is None` unless `spawn: true`), and `permissions.events` (`EventBus.publish` raises `PermissionError` for undeclared names). `storage: true` populates `ctx.storage`. `network` only produces a review warning in an unwired validator.
7. **`minKiroCrewVersion`** is a semver string compared with `parse_version` (pre-release/build stripped, padded to 3 parts) against `kiro_crew.__version__` at registry install (`install_from_registry`), local install (`routes.py` `_check_min_version`) and `manager.install_app`; failure message: `App requires Kiro Crew >= X, but current version is Y. Please update Kiro Crew first.` Unparseable values silently pass.
8. **Assets:** opaque square 512x512 icon (`iconPath`), optional `iconPathDark`; ≥1 landscape ~1200px screenshot; 16:9 `heroImage`/`heroImageDark` (1200x675); optional 25:6 `heroImageDetail`/`heroImageDetailDark` (1200x288). Extensions allowed by the blob proxy: `.png .jpg .jpeg .gif .webp .svg .ico`. All paths repo-relative (relative to the git repo root, not the subdirectory — see §2.4 caveat).
9. **Third-party UI i18n is on us.** `appManifest.ts` localises manifest copy for first-party apps only; the `[manifest-sync]` gate covers builtins only. Our bundle must ship its own `en`/`zh-CN` catalogs and can read the host language from `localStorage['mc-lang']` / `document.documentElement.lang`; the app-sdk exposes no locale hook.
10. **Error bodies must carry a lower_snake `code`** (`^[a-z][a-z0-9_]*$`) — `test/test_error_code_contract.py` ratchets this for in-tree code, and `docs/system-specs/common/code-style.md` requires it for any backend-owned string the frontend must translate.

---

## 1. The registry entry we must produce

### 1.1 Bundled registry file (today's live path)

`src/kiro_crew/apps/registry.py:283`:

```python
_REGISTRY_FILE = Path(__file__).parent / "app-registry.json"
```

Current content of `src/kiro_crew/apps/app-registry.json` (a **bare JSON array**, two entries):

```json
[
  {
    "name": "launchdarkly",
    "gitUrl": "https://github.com/launchdarkly-labs/launchdarkly-kiro-crew-app",
    "repo": "https://github.com/launchdarkly-labs/launchdarkly-kiro-crew-app",
    "branch": "main"
  },
  {
    "name": "todo-ledger",
    "gitUrl": "https://github.com/qihang-dai/todo-ledger",
    "repo": "https://github.com/qihang-dai/todo-ledger",
    "branch": "release"
  }
]
```

Both live entries duplicate the URL into `repo`. Do the same.

### 1.2 Our entry (monorepo layout per FR-DIST-003)

```json
{
  "name": "aidlc-studio",
  "gitUrl": "https://github.com/awslabs/aidlc-workflows",
  "repo": "https://github.com/awslabs/aidlc-workflows",
  "branch": "main",
  "subdirectory": "integrations/kirocrew/aidlc-studio"
}
```

Fallback if FR-DIST-004 triggers (independent repo): drop `subdirectory`, keep the same `name`.

### 1.3 Field semantics from code

Keys a registry row may carry (everything else is dropped at merge), `registry.py:876-889`:

```python
_REGISTRY_ROW_KEYS = frozenset({
    "name", "gitUrl", "repo", "branch", "subdirectory", "resources",
    "detectInstalled", "managed", "featured", "_registry", "_index_author",
})
```

| Key | Reader | Rule |
|---|---|---|
| `name` | identity everywhere | Must equal `app.json.name`; `KEBAB_RE = ^[a-z0-9]+(?:-[a-z0-9]+)*$` (`manifest.py`), checked with `fullmatch`; reserved: `system`, Windows device stems. |
| `gitUrl` | `_entry_git_url(entry)`: `entry.get("gitUrl") or entry.get("repo") or ""` | Any cloneable URL. Clone host must be in the trust set (`_PUBLIC_GIT_HOSTS` = `github.com, ssh.github.com, gitlab.com, bitbucket.org, git.sr.ht, codeberg.org` ∪ configured registry hosts) — `is_clone_host_trusted` fails closed. |
| `repo` | `_merge_manifest`: `repo = entry.get("repo", "")`; every image rewrite is guarded by `if <path> and repo:` | **Required for icon/screenshot/hero to render.** Produces `/api/apps/blob?repo=<repo>&path=<path>`. |
| `branch` | clone `--branch <branch>` (`registry.py:756`, `2485`) | Default `main`. |
| `subdirectory` | `_contained_join(clone_root, subdirectory)` for manifest read, build, copy | Untrusted: `_is_safe_registry_subdir` rejects non-str, NUL, backslash, leading `/`, `X:` drive, any `.`/`..` segment; `_contained_join` resolves symlinks and requires `is_relative_to(root)`. Build detection runs **inside** the subdirectory (`registry.py:2861-2876`: "Build in the directory that actually HOLDS the package, not the clone root"). |
| `featured` | Discover spotlight | Honored only on non-`_registry` rows (`_apply_trust_fields` pops it on external rows). `true` or an ordering number. We do not set it (curator's call). |
| `resources` | `is_self_managed = entry.get("resources") == "app"` in `install_from_registry` | Omit → `gateway` (KiroCrew copies files, registers resources, starts hooks). |
| `detectInstalled` | shell probe, gated by `app_execution_denied(..., action="registry_detect_installed")` | Do not use; the RFC removes it. |

There is **no** `version`, `displayName`, `description`, or image field in the entry: display fields come from the fetched `app.json` (`_merge_manifest` copies `displayName, description, version, author, tags, highlights, license, minKiroCrewVersion` top-level and `agents, skills, crons, mcpServers, permissions, setup, ui, openCommand` under `manifest`).

Manifest fetch on the browse path: `_fetch_app_manifest(repo, branch, subdirectory, app_name, git_url)` does `git archive` of `app.json` (24h cache; external index cached 1h). Our `app.json` therefore lives at `<repo>/<subdirectory>/app.json`.

### 1.4 Trust fields stamped on our row

`_apply_trust_fields` (`registry.py:1062-1122`): a bundled row (no `_registry`) gets `provenance: "official"` and `verified = builtin or _fold_author(_index_author) in FIRST_PARTY_AUTHORS` where `FIRST_PARTY_AUTHORS = {"kirocrew", "kiro crew"}`. Our author is not KiroCrew, so the store shows **provenance "official" (label "KiroCrew registry"), `verified: false`, no badge**. Writing `"author": "kirocrew"` in our manifest does not mint the badge (the merged `author` is deliberately not consulted).

### 1.5 Where the registry is heading (RFC, not live)

`rfc-appstore-official-registry.md` (status `accepted`, but "zero client-side deliverables exist in KiroCrew"; scoped to this repo it is `draft`). Planned shape in the sibling `kirodotdev/KiroCrewApps` repo, published at `https://apps.crew.kiro.dev/official-registry.json`:

```jsonc
{ "name": "aidlc-studio",
  "source": { "type": "git", "url": "https://github.com/awslabs/aidlc-workflows.git",
              "ref": "<immutable commit id>", "subdir": "integrations/kirocrew/aidlc-studio" } }
```

Legacy `{gitUrl|repo, branch}` "stays accepted as sugar". Today's `official_catalog.py` client: fetches `official-registry.json` (TTL 3600s, failure remembered 60s, 4 MiB cap, https only, redirects refused, `schemaVersion` must be exactly `int 1`), **refuses the whole document if `removed`/`reinstated` is non-empty**, and only ANNOTATES rows that already exist (bundled seed or discovered builtin) with `displayName`, `summary`→`description`, `tags`, `author.name`, `iconRef`/`iconRefDark`/`heroRef` (refs must match `_REF_RE`, path-only, resolved against `https://apps.crew.kiro.dev/`). It adds **no** installable inventory ("a published git source is pinned to a COMMIT, and the install path clones with `--branch`"). Consequence: **the PR to `app-registry.json` in KiroCrew remains the only way to be installable from Discover on a stock install** (FR-DIST-005), and the KiroCrewApps catalog entry is an additional, later step.

### 1.6 Opening the PR (publishing-guide §10)

```bash
git checkout -b add-aidlc-studio
# edit src/kiro_crew/apps/app-registry.json
git commit -am "feat(apps): add aidlc-studio to registry"
```

Reviewers check: manifest complete, permissions proportionate, no path traversal, install script safe, app useful.

---

## 2. Required assets and README

### 2.1 Manifest asset fields (publishing-guide §4, `_merge_manifest`)

| `app.json` field | Rendered where | Spec |
|---|---|---|
| `iconPath` (required), `iconPathDark` (optional) | card/row icon, sidebar glyph, gradient fallback centrepiece | **Square 512x512, opaque** (no transparency). Publishing checklist §14 says "256x256 or larger" — §4's 512 is the stricter and later guidance; ship 512. |
| `screenshots[]`, `screenshotsDark[]` | detail-page gallery + lightbox; first screenshot is last-resort hero | Landscape, ~1200px wide; at least one. |
| `heroImage`, `heroImageDark` | Discover/Library rows, spotlight, feature cards, detail banner fallback | 16:9, e.g. 1200x675. |
| `heroImageDetail`, `heroImageDetailDark` | detail banner only | 25:6, e.g. 1200x288. |

Resolution order on every surface: current theme's art → opposite theme → first screenshot → name-seeded gradient with icon.

Blob proxy (`routes.py:1889`): `_BLOB_ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"}`; rejects `..`, absolute paths, dot-prefixed segments; serves only repos present in a configured registry (SSRF guard); shallow clone, 30s timeout, 3 concurrent fetches.

**Do not use `iconUrl`/`heroImage` absolute URLs** — for a registry app only `iconPath` (repo-relative) is honoured: "an index-fetched manifest is untrusted content, and copying an absolute URL out of it would let a third party point the store's `<img>` at any host it likes". Builtins use `/app-assets/<name>/icon.svg` (website `public/app-assets/`), which is not available to us.

### 2.2 Path base caveat for the monorepo layout

The blob URL is `/api/apps/blob?repo=<repo>&path=<iconPath>` and the proxy fetches `path` from the repo root. Code does **not** prepend `subdirectory`. So in the monorepo layout, manifest image paths must be **repo-root-relative**, e.g. `"iconPath": "integrations/kirocrew/aidlc-studio/assets/icon.png"` — not `assets/icon.png`. (No test or doc states this for subdirectory entries; verify with a local external registry before the PR. Listed in open questions.)

### 2.3 Text fields (publishing-guide §2)

- `displayName`: short, fixed-width truncating row → "AI-DLC Studio".
- `description`: plain text, no markdown; 2–3 sentences; first line truncated in rows, two-line clamp on cards.
- `tags`: lowercase; decide the Discover category by priority order. For Developer Tools use one of `developer-tools, code-review, git, github, dev, worktrees, pods, issue-triage, code-quality, open-source, performance`; for Agents & Automation `agents, automation, workflows, orchestration, ...`. **A more specific category tag beats a generic one** — the prototype's `"workflow"` (singular) matches nothing; use `workflows` or `developer-tools` deliberately.
- `highlights[]`: bullet list on detail page.
- `author`, `license` (SPDX; must agree with the actual license and bundled AI-DLC MIT-0 notice per FR-DIST-007).

### 2.4 README sections (publishing checklist + trust doc requirement)

Checklist §14: "`README.md` explains what the app does and how to use it". §9: "If your app needs any executable surface, say so in your README: a user who has not flipped the setting will see `app_execution_denied` rather than a working install." PRD §16.1 adds: never instruct blanket `apps_allow_third_party`; document that third-party code runs with Gateway privileges. README is **not rendered in the App Store** (no reader in `registry.py`/`routes.py`/appstore components; the only mention is a chat prompt in `AppDetailPage.tsx:783`), so it is a repo/reviewer document. Recommended sections: What it is / Why (projection, AI-DLC authoritative) · Install (Discover → Install → trust prompt; CLI alternative) · Trust & privileges (per-app grant, what it admits, how to revoke in Settings → Security) · Permissions declared and why each is used · What it reads/writes on disk · API surface table · Development (tests, `node --check`, `kirocrew app dev`) · Versioning (app version vs bundled AI-DLC version, FR-DIST-008) · License/attribution.

---

## 3. `minKiroCrewVersion` semantics

`src/kiro_crew/apps/version.py`:

```python
def parse_version(v: str) -> tuple[int, ...]:
    v = v.split("-")[0].split("+")[0]           # strip pre-release / build
    parts = [int(x) for x in v.split(".")[:3]]
    return tuple(parts + [0] * (3 - len(parts)))  # "1.0" == "1.0.0"

def check_min_version(min_version: str) -> str | None:
    if not min_version: return None
    try:
        from kiro_crew import __version__ as current
        if parse_version(current) < parse_version(min_version):
            return (f"App requires Kiro Crew >= {min_version}, "
                    f"but current version is {current}. Please update Kiro Crew first.")
    except (ValueError, AttributeError, ImportError):
        pass          # unparseable → treated as satisfied
    return None
```

Check sites:
- `registry.py:3216-3227` (`install_from_registry`, after manifest fetch + admission, **before** the execution-trust gate) → `{"ok": False, "name", "error": ver_err}` (no `code` field).
- `routes.py:103-108, 419-426` (local-path `POST /api/apps/install`).
- `manager.py:280-291` (`install_app`).
- Manifest: `AppManifest.minKiroCrewVersion: str = ""`; not validated for shape in `validate()`; round-trips through `to_dict`/`from_dict`; surfaced in registry rows (`_merge_manifest`) and builtin discovery.

Semantics: a **floor on the gateway's package version only**; there is no per-API feature detection, and pre-release tags are ignored (`0.4.0-rc.1` satisfies `0.4.0`). Set it to the first KiroCrew release that ships every host API Studio needs (PRD §17 "once required host APIs are known"); today's floor candidates are the release that carries `agent.apps_trusted` + `TrustAppModal` (present at 0.3.0) and `permissions.spawn`/`SpawnSDK` (present at 0.3.0). Bump on every host-API dependency; a too-old gateway shows the message above on Install.

---

## 4. Permissions to declare

### 4.1 Parser (`manifest.py:422-479`)

```python
@dataclass
class Permissions:
    api: list[str]       # allowed API path prefixes
    events: list[str]    # allowed WebSocket event types
    mcpTools: list[str]
    storage: bool = False
    network: bool = False
    memory: str = ""     # "", "app-scoped", "shared"
    cron: bool = False
    spawn: bool = False
```

`from_dict` uses **`is True`** for every boolean grant — `"true"`, `1`, `"false"` all DENY. `api`/`events` are `str()`-coerced lists with empty entries dropped. `to_dict` omits falsy fields, so `network: false` does not round-trip (but `permissions.network` defaults False anyway).

### 4.2 What each field actually does at runtime

| Field | Enforced? | Mechanism |
|---|---|---|
| `api` | **Yes (server + client)** | Server: `token_auth._enforce_app_scope` → `app_token_path_allowed(app, path)`: own namespace (`/apps/<name>`, `/api/apps/<name>` path-boundary match) + `/api/notifications/push` + `_api_pattern_matches(p, path)` over `manifest.permissions.api` (supports `/prefix`, `/prefix/*`, `prefix*`; deny-by-default, empty on manifest read failure; 60s cache `_APP_PERMS_TTL`). Applies to **app tokens** (`app` claim) only; dashboard-user cookies bypass. Client: `createScopedApi` in `app-sdk/index.ts:205-219` allows `normalized === p || normalized.startsWith(p + '/')` — note the client does **not** understand `*`, so `/api/apps/aidlc-studio/*` is inert on the client while `/api/apps/aidlc-studio` covers all children on both sides. |
| `events` | **Yes** | `build_app_context`: `EventBus` built only if list non-empty and `broadcast_fn` present. `EventBus.publish(event_type, data)` raises `PermissionError` unless `event_type in allowed or "*" in allowed`; payload `{"type": event_type, "app": app_name, "data": <redacted>}` (credential + exfil-URL redaction). The frontend `AppHost.subscribeFn` bridges `window` CustomEvents `mc:app:<event>`; `useAppEvents` warns if the event is not in `permissions.events`. (There is a `TODO` in `AppHost.tsx`: WebSocket → app event forwarding "when app event forwarding is implemented" — treat live events as unproven; see open questions.) |
| `spawn` | **Yes** | `ctx.spawn = SpawnSDK(...)` only when `perms.get("spawn") is True and spawn_impl is not None`; runs through the host `SubagentManager` with `approval_mode="auto"` (manifest-reference: "gates a real capability"). `SpawnSDK.is_done(spawn_id)`; `SpawnError` on refusal. |
| `storage` | Populates `ctx.storage = AppStorage(app_name, data_dir)` | File-per-key JSON at `~/.kiro/crew/apps/<name>/data/kv/<key>.json`, `atomic_write`, keys reject `..`, `/`, `\`, leading `.`/`~`. API: `get(key) -> dict|str|None`, `set(key, value)`, `delete(key) -> bool`, `list_keys()`. No locking/CAS primitive (PRD §11.5 needs our own — see open questions). |
| `cron` | Populates `ctx.cron = CronSDK` and triggers cron cleanup on disable | Not needed for v1 (night work disabled). |
| `network` | **Advisory only** | `permissions.validate_permissions` (unwired; test-only) would add a warning. `security.md:1146`: "the App Kit manifest `permissions` block (`mcpTools`, `network`, `memory`) is currently advisory, not enforced in-process". Declare `false` for honesty and review. |
| `mcpTools`, `memory` | Advisory | `check_tool_permission` fails open on empty list; not called at dispatch. |

Signing payload (`AppManifest.signing_payload`) covers `name, version, signer, permissions` (+ `notifications`, `crons` when non-empty) — irrelevant unless a fleet policy requires signatures.

### 4.3 Recommended block for `aidlc-studio`

```json
"permissions": {
  "api": ["/api/apps/aidlc-studio", "/api/apps/aidlc-studio/*"],
  "events": ["aidlc-studio.actions", "aidlc-studio.transactions"],
  "storage": true,
  "spawn": true,
  "network": false
}
```

Notes:
- Keep both `api` forms: the bare prefix satisfies the client SDK; `/*` documents intent and matches server-side too (the prototype's `test_permissions_cover_the_declared_route_prefix` pins both).
- If the Advisor or session broker must call host chat/approval APIs from the **UI**, add those prefixes (ops-mission-control declares `/api/chat`, `/api/chat/*`, `/api/approvals`, `/api/approvals/*`). Backend hooks code is in-process and not subject to app-token scope at all.
- Event names are app-chosen; prefix them with the app name to avoid colliding with core WS types (`APP_EVENT_WS_TYPE = "app_event"` envelope comment in `event_bus.py`).
- No `cron`, no `mcpTools`, no `memory` for v1 — matches PRD "declare the minimum App permissions actually used" and the publishing checklist "each one is actually used".

---

## 5. Per-app trust: how it is granted, what it admits, what happens when it is missing

### 5.1 The decision (`apps/execution.py`)

```python
_ALLOW_ALL_SETTING_PATH = "agent.apps_allow_third_party"
_TRUST_SETTING_PATH     = "agent.apps_trusted"
APP_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}")     # fullmatch; max 512 entries considered

def app_execution_denied(app_name, *, action, app_root=None, caller="gateway") -> str | None:
    builtin = is_builtin_app(app_name=app_name, app_root=app_root)   # shipped app.json + path inside package
    granted = not builtin and app_name in trusted_app_names()         # literal name match, no globbing
    if builtin or granted or third_party_execution_allowed():         # allow-all: value is True only
        return None
    return ("third-party app execution is disabled; trust this app alone by adding "
            f"{app_name!r} to agent.apps_trusted, or set agent.apps_allow_third_party=true ...")
```

Fail-closed everywhere: unreadable config, non-list `apps_trusted`, non-string or malformed entries (`"*"`, `"../x"`, `""`) admit nothing. Decisions are SEL-audited (`operation="app_execution_admission"`, `provenance=shipped_builtin|trusted_grant|unverified`).

Config schema (`config/loader.py:1241-1264`): `apps_allow_third_party: bool = False` ("Prefer apps_trusted, which grants the same admission to ONE named app"); `apps_trusted: list[str]` ("Per-app grants for third-party execution — the narrow form ... no wildcard entry is accepted").

**"Narrow per-App trust" concretely** = the literal string `"aidlc-studio"` present in the `agent.apps_trusted` JSON array of `~/.kiro/crew/config.json`. It admits, for that app only: in-gateway Python hooks (`module_load`, `hook_enable_register`, `hook_boot_register`), backend process spawn, lifecycle shell scripts (`onEnable`/`onDisable`/`onUninstall`), registry `detectInstalled`/clone-build/`onInstall` (`registry_install`), and `openCommand`. It is a **ceiling, not a manifest reading** — `TrustAppModal` says so ("trust grants all three regardless of what this app happens to use, and Kiro Crew cannot narrow it"). The code runs in-process with full gateway privileges (SEC-012 warning logged once per app by `module_loader._warn_third_party_execution`).

### 5.2 Gate sites that matter to us

| Surface | Site | Effect when denied |
|---|---|---|
| Registry install | `registry.py:3231-3244` `app_execution_denied(name, action="registry_install", caller="registry")` — **before clone** | `{"ok": False, "name", "error": "blocked by execution policy: ...", "code": "app_execution_denied"}`; nothing lands on disk. |
| Enable | `manager.enable_app` (`manager.py:1203-1216`) after governance + admission checks | `AppResult(ok=False, error_code="app_execution_denied")`; `enabled` stays false; no metadata, resources, scripts, hooks, backend touched. HTTP layer serialises `AppResult.to_dict()` → `"code": "app_execution_denied"`. |
| Hooks at enable / boot | `hooks_integration.on_app_enable` / `on_gateway_startup` re-check with `action="hook_enable_register"` / `"hook_boot_register"`, `app_root=shipped_builtin_app_root(name) or app_dir(name)` | Logs a warning, `disarm_app_crons_for_execution`, `_route_registry.deregister_app_routes(name)`, returns/continues with **no routes registered**. Requests to `/api/apps/aidlc-studio/...` then hit the `RouteRegistry` catch-all and get `404 {"error": "not found"}`. |
| Module load | `module_loader.load_app_module` (`action="module_load"`) | `ImportError("Refusing to load third-party app ... App code would run in-process with full gateway privileges.")` → `RouteRegistry.register_app_routes` catches it, `ctx.health.mark_degraded(...)`, returns `[]`. |
| openCommand | `routes.py:1375-1382` | `403 {"error", "code": "app_execution_denied"}`. |

Revocation (`DELETE /api/security/trusted-apps/{name}`) is effective, not declarative: it runs `teardown_app_runtime(name, record, withdrawing_trust=True)` (shutdown hooks skipped, routes deregistered, crons removed, backend stopped, resources deregistered, `enabled=false`) and only then drops the grant; a failed teardown leaves the grant so the client can retry. Uninstall refuses to proceed if the grant cannot be removed (`error_code="trust_grant_not_removed"`) so a name-keyed grant never outlives its app.

### 5.3 How the user grants it (UI flow)

REST (`dashboard/handlers/security.py`, wired in `dashboard/routes/*`, client in `website/src/api/client.ts:1434-1445`):

| Endpoint | Behaviour |
|---|---|
| `GET /api/security/trusted-apps` | `{"apps": [effective grants], "ineffective": [stored but malformed], "allowAll": bool}` |
| `POST /api/security/trusted-apps/{name}` | Name must `fullmatch` `APP_NAME_RE` (else `400 invalid_app_name`); must be installed **or present in a known registry** (`get_app(name) or get_registry_app(name)`, else `404 app_not_installed`) — this is what lets the store grant *before* the clone; builtins refused (`409 app_is_builtin`); writes under `app_lifecycle_lock(name)` + config lock; `409 config_corrupt` / `409 trust_setting_overlay_owned` (setting owned by `config.local.json`). Idempotent. |
| `DELETE /api/security/trusted-apps/{name}` | Revoke + teardown (above); returns snapshot plus `disabled` flag. |
| `PUT /api/security/trusted-apps/allow-all` `{"value": bool}` | Blanket flag; JSON boolean only. |

Frontend: `website/src/components/appstore/TrustAppModal.tsx` exports `APP_EXECUTION_DENIED = 'app_execution_denied'`, `isTrustDeniedError(e)` (reads `code` structurally from a rejection body or a resolved SSE `done` payload `{ok:false, code}`), and `useTrustGate(retryEnable)`. `AppsPage.tsx:468-524` and `AppDetailPage.tsx:383-488` open the modal when Install (registry, incl. `/registry/install-stream`) or Enable fails with that code; on confirm it calls `api.trustApp(name)` then retries the refused action, invalidates `['trusted-apps']` and `['apps']` queries, and rolls the grant back if the app turns out not to exist (404). The modal shows: scope text, three capability rows (Python in-process, backend process, shell scripts), the repo URL (only `http(s)` rendered as a link), the source label (`sourceLabel()` → "KiroCrew registry" for us), a "not reviewed" disclaimer, and where to revoke (Settings → Security → Trusted apps, `SecurityPanel.tsx:1578+`).

So PRD journey 1 ("Official-registry install → narrow trust → …") maps to: Discover → Install → `install_from_registry` returns `code: app_execution_denied` before cloning → `TrustAppModal` → confirm → `POST /api/security/trusted-apps/aidlc-studio` → retry install → clone/build/copy → Enable (now admitted) → hooks routes registered.

### 5.4 Admission (separate gate, fleet-only)

`apps/admission.py` reads `~/.kiro/crew/app_admission.json` (absent → admit; present-but-unreadable → deny-all). Modes `open`/`enforce`; `banned` kill-switch; `approved` allowlist; `require_signature` (HMAC-SHA256 POC over `signing_payload()` with `trust_keys[signer]`). Checked in `install_from_registry` (after manifest fetch, before clone), `enable_app` (non-builtins), register, update. Nothing for us to do unless targeting a managed fleet; do not set `signer`/`signature`.

---

## 6. Conventions to mirror from the largest builtin (`ops_mission_control`)

### 6.1 Layout (51 files, 1.5 MB)

```
src/kiro_crew/apps/builtins/ops_mission_control/
├── __init__.py                 # re-exports register_routes (REQUIRED for builtins — see below)
├── app.json
├── README.md
├── backend/
│   ├── __init__.py             # one-line docstring
│   ├── routes.py               # HTTP surface only; imports everything absolutely
│   ├── models.py               # dataclasses + module constants (STATE_*, STATUS_*, VALID_ACTIONS…)
│   ├── store.py                # persistence (APP_NAME lives here: routes does `APP_NAME = store.APP_NAME`)
│   ├── dispatch.py / handover.py / ledger.py / ledger_index.py / ledger_sync.py
│   ├── policy_store.py / rotation.py / secrets.py / slack_out.py / notify_out.py / slot_watch.py / companion.py
│   ├── registry.py             # ADD-only provider registry
│   └── providers/{__init__,base,cloudwatch,datadog,github_issues,http,noop,pagerduty,schedule_file,webhook}.py
└── tests/
    ├── __init__.py
    ├── test_routes.py, test_models.py, test_store_and_gate.py, test_dispatch.py, test_handover.py,
    ├── test_handover_cov80.py, test_providers_datadog_cov80.py   # "_cov80" = per-file coverage floor top-ups
    └── test_security.py, test_webhook.py, …
```

UI lives in the **website** repo: `website/src/apps/ops-mission-control/{OpsMissionControlPage,SettingsPanel,SignalsPanel,HandoverPanel,IncidentChat}.tsx` + `api.ts`, registered in `website/src/apps/builtinRegistry.ts` (`'/ops-mission-control': lazy(() => import('./ops-mission-control/OpsMissionControlPage'))`). Assets in `website/public/app-assets/ops-mission-control/{icon,hero-light,hero-dark}.svg`.

### 6.2 Builtin route contract (do NOT copy for a third-party app)

`backend/routes.py` docstring: "Builtin-app contract: `register_routes(app: web.Application) -> None` registering FULL paths directly on the gateway router … This is NOT the external-app `AppRoute`-list contract — mixing them up produces routes that silently never dispatch." Boot loop in `dashboard/routes/system.py:129-136`:

```python
for _builtin_name in BUILTIN_NAMES:
    _mod = importlib.import_module(f"kiro_crew.apps.builtins.{_builtin_name}")
    if hasattr(_mod, "register_routes"):
        _mod.register_routes(app)
```

Hence the `__init__.py` re-export and `_require_enabled` wrapping every handler (builtin routes exist even when the app is disabled). The manifest declares `"backend": {"routes": "backend.routes:register_routes"}` — a builtin-only convention that `BackendConfig.routes` treats as an opaque string.

**Third-party contract** (`route_registry.py`, `hooks_integration.py`):

```json
"backend": { "hooks": { "routes": "backend.routes:register_routes",
                        "on_startup": "backend.hooks:on_startup",
                        "on_shutdown": "backend.hooks:on_shutdown" } }
```

- Hook path grammar `HooksConfig._HOOK_PATH_RE = ^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)*:[a-zA-Z_][a-zA-Z0-9_]*$`; `backend.routes` → file `<app_root>/backend/routes.py`, containment-checked after `resolve()`.
- Callable: `def register_routes(ctx: AppContext) -> list[AppRoute]`; `AppRoute(method: str, path: str, handler: Callable[[web.Request, AppContext], Awaitable[web.Response]])`. Paths are relative (`/health`, `/repos/{repo_id}`); dispatcher mounts them under the catch-all `/api/apps/{app_name}/{path:.*}` (exact match first, then `{param}` patterns; params injected into `request.match_info`).
- Non-list return or exceptions → `ctx.health.mark_degraded(...)`, `[]`; non-`AppRoute` items skipped with a warning.
- Registration happens on enable (`on_app_enable`) and at gateway boot (`on_gateway_startup`) for every enabled app; `deregister_app_routes` also calls `unload_app_modules(name)` (pops every `sys.modules` key with prefix `_kirocrew_app_<name>.`), so re-enable loads fresh code (hooks code changes need disable→enable or restart; UI does not).
- Every handler must authenticate itself (`request["user"]`) — the prototype applies this centrally in `register_routes` via a decorator; keep that.

### 6.3 Backend module organisation and constants

- Absolute imports everywhere in builtins (`from kiro_crew.apps.builtins.ops_mission_control.backend import companion, dispatch, ...`); one `models.py` owning enums/constants (`STATE_FIRING`, `STATUS_NEEDS_HUMAN`, `VALID_ACTIONS`, `utc_now_iso`); one `store.py` owning `APP_NAME` and persistence; `routes.py` owns only HTTP caps as `_UPPER_SNAKE` module constants with a `#:` comment stating the *why* (`_MAX_SECRET_LEN = 512`, `_MAX_NOTE_LEN = 4000`, `_SAFE_BRANCH_RE`).
- JSON booleans are never `bool()`-coerced: `_require_bool(body, field)` raises `_NotABool` (the docstring recounts a real inverted-approval bug). Same rule in `Permissions.from_dict` and `admission.require_signature` — mirror it for Gate/answer payloads.
- Sensitive values are write-only over HTTP; read endpoints report presence only.
- SEL audit via `kiro_crew.sel.sel().log_api_access(caller=..., operation=..., outcome=..., resources=..., error=...)` on every security-relevant decision; audit failure never changes the decision (`try/except` + `logger.debug`).
- Blocking I/O off the event loop: `await asyncio.to_thread(...)`; nothing heavy at import or `register_routes` time beyond cheap warming (AUTOSDE `no-new-work-on-gateway-boot-path`).
- Logging: `logger = logging.getLogger(__name__)` (prototype uses `"kirocrew.app.aidlc-console"`; `ctx.logger` is `kirocrew.app.<name>`).
- `from __future__ import annotations`; dataclasses for records; 100-char lines; black/isort/flake8/mypy (`check_untyped_defs`), annotate empty collections.
- Comments explain WHY; no PR numbers, dates, "previously"/"now" narration.

### 6.4 Error responses

`docs/system-specs/common/code-style.md`: "Backend-owned strings (built-in app manifests, HTTP error bodies) have no catalog path, so a new non-2xx JSON body MUST carry a machine-readable `code` field the frontend can translate." `test/test_error_code_contract.py` ratchets `web.json_response({...}, status>=400)` sites per file in `error-code-baseline.json` (buckets `missing_code`, `opaque_body`, `dynamic_status`); value shape `_CODE_VALUE_RE = ^[a-z][a-z0-9_]*$` (lower_snake, e.g. `app_execution_denied`, `trust_grant_not_removed`, `invalid_app_name`, `app_not_installed`, `config_corrupt`). `AppResult.to_dict()` emits `"code": error_code`. `error-handling.md` (older, ACP-focused) adds: error strings at boundaries, never tracebacks; partial output on timeout. For Studio: every non-2xx body is `{"error": "<advisory prose>", "code": "<lower_snake>"}` and the UI switches on `code`.

### 6.5 Tests

- Location/discovery: `setup.cfg` `testpaths = test transfer src/kiro_crew/apps/builtins`; files `test_*.py`; ops tests use `unittest.IsolatedAsyncioTestCase`, core tests use pytest classes `TestXxx` + `@pytest.mark.asyncio` (asyncio strict mode). Either is accepted; a standalone app repo should use pytest (`python -m pytest tests/ -q -o addopts=` as the prototype README does, because the KiroCrew `addopts` would otherwise apply when run inside that repo).
- Naming pattern for coverage top-ups: `test_<module>_cov80.py` (per-file 80% floor, `scripts/check_per_file_coverage.py`, baseline shrink-only).
- Isolation (AUTOSDE `no-test-side-effects`): tests under `apps/builtins/*/tests` and any `tests/` see only the root `conftest.py`, which does **not** pin `KIROCREW_HOME` — `monkeypatch.setenv("KIROCREW_HOME", str(tmp_path))` yourself; never spawn real `kiro-cli`; never touch `~/.kiro/crew`; `< 1s` per test; patch the defining module, not a re-export; poll with deadlines instead of sleeps; seed randomness.
- Route tests assert the namespace (`test_all_routes_are_namespaced_under_the_app`), the expected surface list, the enabled gate, and write-only secrets by exercising the real handler.
- Load-by-path tests: `code_review_sage/tests/test_backend_routes.py` uses `importlib.util.spec_from_file_location("sage_backend_routes_under_test", str(_ROUTES))` — the same shape as the prototype's `tests/conftest.py`, which is exactly "import it the way the gateway does".
- Manifest tests: the prototype's `tests/test_manifest.py` runs `AppManifest.from_dict(json.load(app.json)).validate() == []` and pins the traps (hooks vs proxy, `ui.entry` relative to `ui/`, default export). Keep and extend (`minKiroCrewVersion` present, permissions exactly as §4.3, `iconPath`/`screenshots`/`heroImage` files exist).

### 6.6 UI conventions (builtin page, to mirror inside our bundle)

- `lucide-react` icons only (`className="lucide-inline"`), never emoji or hand-rolled SVG; React Query for data; Framer Motion for animation; design tokens (`var(--bg)`, `var(--text)`); no `text-xs`/<10px; `<Btn>`/`<Clickable>`, `aria-label` on icon-only buttons; compose from shared `ui` components (`PageHeader`, `Card`, `CardTitle`, `StatCard`, `Badge`, `EmptyState`, `SegmentedControl`).
- Never hardcode a user-facing string; format dates/numbers/sorts through a locale-aware seam (`fmtDateFields`, `fmtUnit` in `src/i18n/format.ts`).
- Status→label tables hold **catalog keys**, resolved at render (`STATUS_LABEL_KEY` in `OpsMissionControlPage.tsx`), because ALL-CAPS constants are exempt from the lint and would ship untranslated.
- Thin `api.ts` per app with typed responses and a shared query key (`SIGNALS_QUERY_KEY`); poll fast when active / slow when idle instead of SSE where SSE would clobber state.

### 6.7 i18n approach and how it differs for us

- Builtins: catalog namespace `apps.<camelCaseName>.{manifest,<component>}` in `website/src/i18n/locales/*.json` (12 languages incl. `zh-CN`, plus `en-XA` pseudolocale). Manifest copy is localised additively via `website/src/components/appstore/appManifest.ts` (`APP_MANIFEST_KEY[name] = {displayName, description, pageLabel, highlights[]}`) and pinned byte-identical to `app.json` by `website/scripts/check-app-manifest-sync.mjs` (`[manifest-sync]` hard-zero gate). "Coverage is first-party only, deliberately. A third-party app's copy is its author's to translate … falls through to whatever the manifest supplied."
- Gates (`docs/ci/i18n-gates.md`): `npm run i18n:check` runner (13 checks; diff-scoped zero tolerance `[added-lines]`, `[vs-base]`, `[source-strings]`, `[changed-values]`; repo hard-zeros `[key-refs]`, `[plurals]`, `[pseudolocale]`, `[dnt]`, `[manifest-sync]`; report-only `[dynamic-keys]`, `[extractable]`, `[untranslated]`, `[allcaps]`), `npm run i18n:render` under `en-XA`, `duplicateKeys.test.ts`. **None of these run over a third-party bundle** — they gate `website/src` only. Our store-listing strings (`displayName`, `description`, `highlights`) will render in English for every locale; only a future `app.nls.<locale>.json` sidecar (named in `appManifest.ts` as "a separate change, not this table") would fix that.
- What our bundle can do: read the active language from `localStorage['mc-lang']` (`LANG_STORAGE_KEY` in `website/src/i18n/detect.ts`; `''` = follow browser) and `document.documentElement.lang` (set by `LanguageProvider.tsx:223`), listen for `storage` events on that key, and ship our own `en`/`zh-CN` catalogs with a parity test in our repo. The app-sdk exposes `useTheme()` but no locale hook.

### 6.8 Third-party UI bundle contract (`AppHost.tsx`, `vite.config.ts`, `routes.py`)

- Served from `GET /apps/{name}/ui/{path}` → `apps_dir()/<name>/ui/<path>` (extension allowlist `_ALLOWED_EXTENSIONS`, resolve-containment inside `ui/`, `Cache-Control: no-cache`, or `no-store` in dev mode). **`ui.entry` is therefore relative to `ui/`**, not the app root: `AppHost` imports `` `/apps/${app.name}/ui/${entry}` ``. `manifest-reference.md` ("relative to app root", example `dist/index.mjs`) is stale on this point; the prototype uses `"entry": "index.mjs"` with the file at `ui/index.mjs`. (`ui/dist/index.mjs` on disk needs `"entry": "dist/index.mjs"`.)
- Loaded with `React.lazy(() => import(bundlePath))` → **default export must be a React component**; `mountFunction` is never called. Wrapped in `AppErrorBoundary` + `Suspense` + `AppApiProvider`.
- Import map injected by `appImportMapPlugin` in `website/vite.config.ts`: `react`, `react-dom`, `react-dom/client`, `react/jsx-runtime`, `@kirocrew/app-sdk`, `@kirocrew/app-sdk/ui`, `lucide-react` → `/vendor/*.mjs` stubs reading `window.__kirocrew_modules`. Mark all seven as Rollup `external` and bundle nothing else from the host (React Query, react-router, react-i18next are NOT provided; bring your own or avoid).
- SDK hooks (`website/src/app-sdk/index.ts`): `useAppApi()` (`get/post/put/patch/del`, scoped as in §4.2, rejects absolute/protocol-relative URLs and normalises `..`), `useAppEvents(event, cb)`, `useTheme()` (`{mode, accent, colorTheme}`), `useAppInfo()`, `useNavigate()`, `useNotify()`, `useNavBadge()`, `useChatLauncher()`, `useChatSession`, `ChatPanel`, `ChatEmbed`, `ChatMessageList` (needs `/api/chat` in `permissions.api`), marker-protocol helpers.
- Dev loop: `kirocrew app dev <name>` (`POST /api/apps/{name}/dev {"enabled": true}`) → `no-store` + `mc:app-reload` full page reload on `ui/` changes.
- Install copies exclude `node_modules`, `.git`, `__pycache__`, `.venv` at any depth and never follow symlinks that leave the tree → commit the built bundle. Registry install auto-runs `npm install`/`npm run build` only when `package.json` is at the **app root** (`_run_app_build(app_source)` checks `build_dir / "package.json"`); a `ui/package.json` is not detected. Simplest: no root `package.json`, commit `ui/dist/index.mjs`, and make `onInstall` unnecessary.

### 6.9 Scaffold reference (`apps/scaffold.py`)

`kirocrew app init <name> --ui --backend` generates: `app.json` (name/version `0.1.0`/displayName/description/author/agents/skills/tags), `agents/sample-agent.json`, `skills/sample-skill/SKILL.md`, `backend/server.py` (a **standalone** `HTTPServer` backend — not the hooks model), `ui/{package.json,vite.config.ts,src/App.tsx,.gitignore}` with `build.lib.entry='src/App.tsx'`, `formats:['es']`, `fileName: () => 'index.mjs'`, `outDir:'dist'`, externals as above, and a README skeleton. Useful only as a starting shape; replace the backend with hooks routes.

---

## 7. Multi-module backend for a third-party app (what `module_loader` allows)

### 7.1 Loader semantics (`module_loader.load_app_module`)

```python
dotted_path, callable_name = module_path.rsplit(":", 1)
file_path = app_dir / (dotted_path.replace(".", "/") + ".py")   # must be_file() and resolve inside app_dir
unique_name = f"_kirocrew_app_{app_name}.{dotted_path}"          # e.g. _kirocrew_app_aidlc-studio.backend.routes
spec = importlib.util.spec_from_file_location(unique_name, str(file_path))
module = importlib.util.module_from_spec(spec); sys.modules[unique_name] = module
spec.loader.exec_module(module)
```

No `sys.path` manipulation; no parent package object; `module.__package__` becomes `_kirocrew_app_<name>.backend`, which is not in `sys.modules` and has no finder.

### 7.2 Empirical result (this session, Python 3, loader emulated exactly)

| Strategy in `backend/routes.py` | Result |
|---|---|
| `from . import models` | `ModuleNotFoundError: No module named '_kirocrew_app_testapp'` |
| `from backend import models` | `ModuleNotFoundError: No module named 'backend'` |
| Sibling loader via `spec_from_file_location` relative to `__file__` | Works; sibling registered as `_kirocrew_app_testapp.backend.models` |

Recommended helper (put it in `backend/routes.py` or a tiny `backend/_load.py` loaded first the same way):

```python
import importlib.util, sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PKG = __name__.rsplit(".", 1)[0] if "." in __name__ else "_aidlc_studio_backend"

def _sibling(name: str):
    """Load backend/<name>.py under the same _kirocrew_app_<app>.backend.* namespace.

    module_loader registers only the hook file; relative and bare sibling imports both
    raise ModuleNotFoundError, and a sibling must live under the app's namespace so
    unload_app_modules() (prefix "_kirocrew_app_<app>.") evicts it on disable.
    """
    unique = f"{_PKG}.{name}"
    if unique in sys.modules:
        return sys.modules[unique]
    spec = importlib.util.spec_from_file_location(unique, str(_HERE / f"{name}.py"))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot locate sibling module {name!r} next to {__file__}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[unique] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(unique, None)
        raise
    return mod

models = _sibling("models")
```

Rules that follow:
- Name siblings under `_kirocrew_app_<app>.` so `unload_app_modules` clears them on disable and re-enable loads fresh code; a different prefix would leak stale modules across enable cycles.
- Siblings must themselves use `_sibling(...)` (or receive dependencies by parameter) — they cannot import each other by name either. Keep the graph shallow: `routes.py` → domain modules; domain modules → stdlib/`kiro_crew.*` only.
- `kiro_crew.*` imports are fine (`from kiro_crew.apps.context import AppContext`, `from kiro_crew.apps.route_registry import AppRoute`, `from kiro_crew.security import is_sensitive_path`, `from kiro_crew.atomic_write import atomic_write`, `from kiro_crew.sel import sel`), as the prototype already does.
- Tests must load the same way (prototype `tests/conftest.py` does `spec_from_file_location("_aidlc_console_routes_under_test", routes.py)`); add a test that `register_routes(ctx)` succeeds when only `backend/routes.py` is in `sys.modules`, so a stray `from backend import x` cannot pass locally and fail in the gateway.
- Precedent check: **no builtin and no `test/` file uses this sibling-loader pattern** (all `spec_from_file_location` hits are test harnesses loading scripts or a single routes file). The prototype chose single-file "on purpose" for this reason. Studio's 8+ backend modules (PRD §13.1) justify establishing the pattern, with the test above.

Alternative not recommended: `backend.entryPoint` standalone process (`type: python|asgi|node|exec`) proxied at `/apps/<name>/api/*` — it gains normal packaging but loses `AppContext` (`ctx.storage/events/spawn`) and adds a child process, sandbox, port and health-check surface; PRD P-02 wants KiroCrew to own sessions/scheduling, which the in-process SDKs give directly.

---

## 8. Checklist for `aidlc-studio` derived from the above

Manifest (`integrations/kirocrew/aidlc-studio/app.json`):

```json
{
  "name": "aidlc-studio",
  "version": "1.0.0",
  "displayName": "AI-DLC Studio",
  "description": "<2-3 plain-text sentences>",
  "author": "<org or person>",
  "license": "MIT-0 or as agreed (FR-DIST-007)",
  "minKiroCrewVersion": "0.3.0",
  "tags": ["developer-tools", "workflows", "aidlc", "kiro"],
  "highlights": ["...", "..."],
  "iconPath": "integrations/kirocrew/aidlc-studio/assets/icon.png",
  "iconPathDark": "integrations/kirocrew/aidlc-studio/assets/icon-dark.png",
  "screenshots": ["integrations/kirocrew/aidlc-studio/assets/screenshots/action-center.png"],
  "screenshotsDark": ["integrations/kirocrew/aidlc-studio/assets/screenshots/action-center-dark.png"],
  "heroImage": "integrations/kirocrew/aidlc-studio/assets/hero-light.png",
  "heroImageDark": "integrations/kirocrew/aidlc-studio/assets/hero-dark.png",
  "heroImageDetail": "integrations/kirocrew/aidlc-studio/assets/hero-detail-light.png",
  "heroImageDetailDark": "integrations/kirocrew/aidlc-studio/assets/hero-detail-dark.png",
  "backend": { "hooks": { "routes": "backend.routes:register_routes" } },
  "ui": { "entry": "dist/index.mjs",
          "pages": [{ "route": "/aidlc-studio", "label": "AI-DLC Studio", "icon": "GitBranch" }],
          "sidebar": { "section": "Apps", "order": 10 } },
  "permissions": { "api": ["/api/apps/aidlc-studio", "/api/apps/aidlc-studio/*"],
                   "events": ["aidlc-studio.actions", "aidlc-studio.transactions"],
                   "storage": true, "spawn": true, "network": false },
  "dependencies": { "optionalCommands": ["bun", "git"] },
  "platform": { "os": ["macos", "linux"] }
}
```

(Image path base per §2.2 to be confirmed; `platform.os` default is already `["macos","linux"]`, matching PRD §17 release gating.)

Repo layout: `app.json`, `README.md`, `LICENSE`/`NOTICE` (bundled AI-DLC MIT-0), `assets/`, `backend/{routes.py, ...}`, `ui/dist/index.mjs` (committed) + `ui/src/**`, `ui/package.json`, `tests/{conftest.py, test_manifest.py, test_routes.py, test_<module>.py}`.

Release gates to mirror from KiroCrew: `AppManifest.validate() == []`; `node --check ui/dist/index.mjs` and default export present; every non-2xx body has lower_snake `code`; no hardcoded UI strings, `en`/`zh-CN` parity test; per-file coverage ≥80%; tests isolated with `KIROCREW_HOME`; local install/enable/update from a clone; registry PR.

---

## 9. Doc-vs-code disagreements found

| Doc says | Code does |
|---|---|
| `manifest-reference.md`: `ui.entry` "Path to ESM bundle (relative to app root)" | `AppHost` + `handle_app_ui_file` resolve it under `<app>/ui/`; entry is relative to `ui/`. |
| `manifest-reference.md`: builtin hero images use `/apps/{name}/ui/...` | Builtins use `/app-assets/<name>/*.svg` (website `public/`), e.g. ops-mission-control. |
| `publishing-guide.md` §14 "Icon … 256x256 or larger" | §4 of the same doc: "Square, 512x512, opaque". Follow 512. |
| `publishing-guide.md` §9: third-party execution requires `agent.apps_allow_third_party=true` | `execution.py` also accepts a per-app `agent.apps_trusted` grant; the UI grants that, not the blanket flag. |
| `rfc-appstore-official-registry.md` describes `_tier`, signature gate, tombstones, `source` union as the design | None is implemented; `_registry` boolean, `official_catalog.py` annotate-only client, and bundled `app-registry.json` are the live path. |
| `manifest-reference.md` `permissions` table reads like a capability model | Only `api` (token scope), `spawn`, `events`, `storage` have runtime effect; `network`/`mcpTools`/`memory` advisory (`security.md:1146`). |
| Prototype `README.md`: "`permissions` beyond storage/spawn/events are not enforced" | Accurate; matches code. |

---

## Critic addendum (2026-09-04, completeness pass)

### A1. §0.4 / §7 sibling imports — true for 0.3.0, false for the running 0.5.0 gateway

`apps/module_loader.py` in the 0.5.0 bundle has `_ensure_namespace_packages` (line 81; `ModuleSpec(name, None, is_package=True)` :111; `pkg.__path__ = [str(search_dir)]` :113), so `from . import models` works there. The `_sibling()` helper in §7.2 remains the portable choice (works on both loaders and under the prototype's test loader); relative imports are the cleaner choice if `minKiroCrewVersion` is raised to `0.5.0`. Decision recorded in `00-index.md`.

### A2. §4.2 "No locking/CAS primitive" — resolved

Doc 01 §2.3 + addendum A2: `kiro_crew.platform_compat.file_lock(fd, *, exclusive=True, required=False)`, `try_acquire_lock`, `release_lock`, and `kiro_crew.atomic_write.atomic_write(..., fsync=True)` on a dedicated `.lock` file (pattern `apps/dev_mode.py::_sentinel_lock`, lines 76-92) give the PRD §11.5 compare-and-set store inside `ctx.data_dir`.

### A3. `minKiroCrewVersion` candidates

`0.3.0` (this doc §3, §8) satisfies trust/spawn/`permissions.api`; `0.5.0` (doc 01 §6) is required for relative imports, `agent.apps_trusted_local`/repository-bound trust, the 30 s startup-hook deadline, and `permissions.exposeToApps`. `parse_version` strips `-insider.9`, so the running `0.5.0-insider.9` satisfies `0.5.0`. Decision.

### A4. Import map — correction to §6.8 wording

The seven externals listed are exactly the import-map entries verified in the running bundle. `@tanstack/react-query` is available only as `window.__kirocrew_modules['@tanstack/react-query']` (not a bare specifier) — do not add it to Rollup `external`; feature-detect it.

### A5. Still unverified (carried to `00-index.md` Unresolved)

- §2.2 blob-proxy path base for a `subdirectory` registry entry (repo-root-relative vs subdirectory-relative image paths) — no test or doc covers it; verify against a local external registry before the PR.
- Whether a Discover/registry install of a `subdirectory` app records `sourceUrl` such that the 0.5.0 repository-bound trust grant (`agent.apps_trusted_repositories`) is what the consent modal writes (affects README wording only).
