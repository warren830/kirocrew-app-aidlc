# 03 — KiroCrew dashboard host contract for third-party app UIs

Research reader output for AI-DLC Studio (slug `aidlc-studio`). Everything below was read from
source in `/Users/ychchen/warren_ws/kirocrew` (frontend `website/`, backend `src/kiro_crew/`) on
2026-09-04, cross-checked against the prototype `/Users/ychchen/warren_ws/kirocrew-app-aidlc`.
Where a doc disagreed with code, **the code is reported and the disagreement is called out**.

Host versions in play (`website/package.json`): React `^18.3.1`, react-dom `^18.3.1`,
react-router-dom `7.18.2`, `@tanstack/react-query` `5.96.0`, lucide-react `1.7.0`,
tailwindcss `^3.4.19`, framer-motion `^12.38.0`, Vite `8.2.0`.

---

## 0. Executive summary of doc-vs-code disagreements (read first)

| Claim in docs / prototype comments | What the code does | Source |
|---|---|---|
| `@kirocrew/ui` is the bare specifier for shared components (`website/src/kirocrew-ui/index.ts` header comment) | The import map maps **`@kirocrew/app-sdk/ui`** → `/vendor/kirocrew-ui.mjs`. There is **no** `@kirocrew/ui` entry in the import map; `import ... from '@kirocrew/ui'` fails to resolve in the browser. `window.__kirocrew_modules['@kirocrew/ui']` (registry key) does exist. | `website/vite.config.ts:74-93`, `website/dist/index.html` importmap, `website/src/app-sdk/shared-modules.ts` |
| `ui.pages[].mountFunction` ("exported function name", default `"mount"`) in `docs/app-kit/manifest-reference.md` | **Never called.** AppHost does `React.lazy(() => import('/apps/<name>/ui/<entry>'))` and renders the module's **default export** as a component. `mountFunction` / `entryPoint` are parsed and stored, then ignored by the frontend. | `website/src/components/AppHost.tsx:225-246` |
| `useAppEvents(event, cb)` "Subscribe to real-time WebSocket events" | The host subscribes the callback to `window` CustomEvents named `` `mc:app:${event}` `` — and **nothing in the host dispatches those events** (only `mc:app:badge` is dispatched, by the SDK itself). `useWebSocket.ts` never forwards WS traffic to `mc:app:*`. A `// TODO` in AppHost confirms. **useAppEvents is a no-op today.** | `AppHost.tsx:249-257`, `hooks/useWebSocket.ts` (grep `CustomEvent(`) |
| `useNotify()` "Show a toast notification in the host" | Dispatches `window` CustomEvent `mc:notify` with `{ message, type? }`. **No listener exists anywhere in `website/src`** (only two dispatchers: AppHost and SpecBuilderPage). **useNotify is a no-op today.** | `AppHost.tsx:260-264`; `grep -rn "mc:notify" website/src` |
| `useTheme().mode` is `'dark' \| 'light'` | Reads `document.documentElement.dataset.theme`, which the host sets to `dark`/`light` **only for the default (emerald) theme**; every other theme sets `data-theme="<slug>-dark"` / `"<slug>-light"` (e.g. `monokai-dark`). `colorTheme` reads `dataset.colorTheme`, which the host **never sets** → always `'default'`. The authoritative attribute is **`data-mode`** (`'dark' \| 'light'`). | `app-sdk/index.ts:96-121`, `hooks/useTheme.tsx:166-181` |
| `@kirocrew/app-sdk/ui` exports `AimBadge` (getting-started.md, vendor stub) | `website/src/kirocrew-ui/index.ts` exports **`SourceBadge`**, not `AimBadge`. The stub destructures `AimBadge` → **`undefined`** (silently); `SourceBadge` is **not** importable via the bare specifier (link-time `SyntaxError`), only via `window.__kirocrew_modules['@kirocrew/ui'].SourceBadge`. | `public/vendor/kirocrew-ui.mjs`, `kirocrew-ui/index.ts` |
| `ui.pages[].icon` (lucide name) is the sidebar icon | For **non-builtin** apps the lucide name is ignored by the left rail (`getBuiltinIcon` runs only when `target.builtin`). A third-party app gets `<Package size={16}/>` unless it sets top-level `iconUrl` or `ui.pages[0].iconUrl`. | `website/src/App.tsx:1200-1222`, `website/src/appNav.ts` |
| `ui.sidebar.section` / `ui.sidebar.order` control placement | Parsed by `manifest.py` (`UISidebar`) but **unused by the frontend**. All app pages land in the `Apps` group in the order `GET /api/apps` returns them, which is `sorted(root.iterdir())` → **alphabetical by app name**. | `apps/manager.py:1258`, `App.tsx:1191-1230` |
| `permissions.api` glob `"/api/apps/aidlc-console/*"` (prototype manifest) | The SDK allowlist is prefix-based, not glob: `normalized === p \|\| normalized.startsWith(p.endsWith('/') ? p : p + '/')`. A `*` entry only matches a literal path segment `*`. `"/api/apps/aidlc-studio"` alone already covers every sub-path. Harmless but redundant. | `app-sdk/index.ts:190-205` |

---

## 1. Loading pipeline: import map, vendor stubs, module registry

### 1.1 How a bundle gets its dependencies

1. `website/src/app-sdk/shared-modules.ts` runs at host startup and sets `window.__kirocrew_modules`.
2. `website/vite.config.ts` (`appImportMapPlugin`, lines 74-93) injects a `<script type="importmap">`
   into `index.html` at build time. Verified in `website/dist/index.html`:

```json
{"imports":{
  "react":"/vendor/react.mjs",
  "react-dom":"/vendor/react-dom.mjs",
  "react-dom/client":"/vendor/react-dom-client.mjs",
  "react/jsx-runtime":"/vendor/react-jsx-runtime.mjs",
  "@kirocrew/app-sdk":"/vendor/kirocrew-app-sdk.mjs",
  "@kirocrew/app-sdk/ui":"/vendor/kirocrew-ui.mjs",
  "lucide-react":"/vendor/lucide-react.mjs"
}}
```

3. The stubs live in `website/public/vendor/*.mjs` (copied verbatim to `dist/vendor/`, served by
   aiohttp `add_static("/vendor", dist_dir/"vendor", append_version=False)` in
   `src/kiro_crew/dashboard/server.py:886-892`). Each stub reads from the registry and re-exports a
   **fixed named list**. Also present in `dist/vendor/`: `tailwindcss-browser.js` (Tailwind v4 IIFE)
   and `mermaid.min.js` — these exist for **sandboxed widget/artifact iframes**, not for app pages
   (`website/src/lib/vendorPaths.ts`).

### 1.2 `window.__kirocrew_modules` keys (registry; `shared-modules.ts`)

| Key | Value | Has import-map entry? |
|---|---|---|
| `react` | `import * as React from 'react'` (18.3) | yes |
| `react-dom` | `import * as ReactDOM from 'react-dom'` | yes (+ `react-dom/client`) |
| `react/jsx-runtime` | `import * as jsxRuntime from 'react/jsx-runtime'` | yes |
| `lucide-react` | whole namespace | yes |
| `@tanstack/react-query` | whole namespace (v5.96) | **no** — reachable only via `window.__kirocrew_modules['@tanstack/react-query']`. The host wraps the tree in `QueryClientProvider` (`main.tsx:95`), so `useQuery` from this namespace works inside an app. |
| `@kirocrew/app-sdk` | `website/src/app-sdk/index.ts` namespace | yes |
| `@kirocrew/ui` | `website/src/kirocrew-ui/index.ts` namespace | **no** (served as `@kirocrew/app-sdk/ui`) |

Not available at all (neither registry nor import map): `react-router-dom`, `framer-motion`,
`i18next` / `react-i18next`, `dompurify`, `tailwind-merge`, `@radix-ui/*`.

### 1.3 Exact named exports of each stub (link-time contract)

ES module semantics: importing a name a stub does **not** export throws
`SyntaxError: The requested module '/vendor/x.mjs' does not provide an export named 'Y'` and the
**whole app module fails to load** (AppHost shows "Failed to load <app>"). Only these names exist:

- `/vendor/react.mjs`: `default` (React) + `useState, useEffect, useRef, useCallback, useMemo, useContext, useReducer, useLayoutEffect, useImperativeHandle, useDebugValue, useDeferredValue, useTransition, useId, useSyncExternalStore, useInsertionEffect, createContext, createElement, cloneElement, createRef, forwardRef, lazy, memo, startTransition, Fragment, Suspense, StrictMode, Children, Component, PureComponent, isValidElement`
- `/vendor/react-dom.mjs`: `default` (ReactDOM) + `createPortal, flushSync, unstable_batchedUpdates`
- `/vendor/react-dom-client.mjs`: `createRoot, hydrateRoot`
- `/vendor/react-jsx-runtime.mjs`: `jsx, jsxs, jsxDEV, Fragment`  ← **confirms the prototype's `import { jsx as _jsx, jsxs as _jsxs } from 'react/jsx-runtime'` is valid**
- `/vendor/kirocrew-app-sdk.mjs`: `useAppApi, useAppEvents, useTheme, useAppInfo, useNavigate, useNotify, useNavBadge, useChatLauncher, AppApiProvider, parseOptions, deriveFollowUpOptions, extractSteeringAcks, stripPartialOptionMarker, useChatSession, ChatPanel, ChatEmbed, ChatMessageList, defaultMessageRenderers, mergeRenderers, resolveRenderer, ToolCallPill, GROUPED_ROLES` (pinned by `website/src/test/chatProtocolBoundary.test.ts`)
- `/vendor/kirocrew-ui.mjs`: `Card, CardTitle, Btn, SendBtn, Input, SearchInput, Badge, AimBadge(=undefined), StatCard, Skeleton, ContentSkeleton, EmptyState, PageHeader, Toggle, InfoTip, SegmentedControl, MarkdownRenderer`
- `/vendor/lucide-react.mjs`: `default` = `new Proxy(m, { get: (_, prop) => m[prop] })` (any icon) + named: `AlertTriangle, ArrowLeft, ArrowRight, ArrowUp, Bell, Bot, Brain, Building2, Calendar, Check, ChevronRight, Clock, Code, Download, ExternalLink, Gamepad2, Heart, Home, Loader2, Menu, MessageSquare, Moon, Package, Plug, Plus, Power, RefreshCw, Rocket, Search, Settings, Shield, Sparkles, Star, Sun, Tag, Trash2, Users, Wand2, Waves, X, Zap`

**lucide gotcha for a Vite build:** `import { GitBranch } from 'lucide-react'` compiles fine but
fails at link time in the browser (not in the named list). Use the default export:

```ts
import lucide from 'lucide-react'
const { GitBranch, ShieldCheck, CircleDot } = lucide as unknown as typeof import('lucide-react')
```

`import * as L from 'lucide-react'` does **not** work for unlisted icons (a namespace object exposes
only real export names, not the Proxy).

---

## 2. `@kirocrew/app-sdk` — exports, signatures, real behaviour

Source: `website/src/app-sdk/index.ts`.

### 2.1 Types

```ts
export interface AppApi {
  get<T = unknown>(path: string, init?: RequestInit): Promise<T>
  post<T = unknown>(path: string, body?: unknown): Promise<T>
  put<T = unknown>(path: string, body?: unknown): Promise<T>
  patch<T = unknown>(path: string, body?: unknown): Promise<T>
  del<T = unknown>(path: string): Promise<T>
}
export interface AppPermissions { api: string[]; events: string[] }
export interface AppInfo { name: string; version: string; permissions: AppPermissions }
export interface AppTheme { mode: 'dark' | 'light'; accent: string; colorTheme: string }
export interface ChatLaunchOptions { agent?: string; message?: string }
```

### 2.2 `useAppApi(): AppApi` — base URL, allowlist, error shape

Built by `createScopedApi(allowedPaths, appName)`:

- **No base URL is prepended.** You pass the full same-origin path, e.g. `api.get('/api/apps/aidlc-studio/board')`. Relative paths are resolved with `new URL(path, 'http://localhost')` and only `pathname + search` is fetched, so `'board'` becomes `/board` (wrong) — always pass absolute paths.
- Rejects before fetch (throws `Error`): `/^(?:https?:)?[/\\]{2}/i.test(path) || path.includes('\\')` → `[app-sdk] Absolute URLs are not allowed: <path>`.
- Normalises `..` **before** the allowlist check.
- Allowlist: `allowed = allowedPaths.some(p => normalized === p || normalized.startsWith(p.endsWith('/') ? p : p + '/'))`; failure throws `Error('[app-sdk] App "<name>" not permitted to access <normalized>. Declared: [<list>]')`. `allowedPaths` = `manifest.permissions.api` verbatim.
- Fetch: `fetch(safePath, init)` with **cookie auth only** (`mc_token` session cookie; no header injection). POST/PUT/PATCH set `Content-Type: application/json` and `JSON.stringify(body)` when `body != null`.
- Error shape on non-2xx: `throw new Error(\`API ${res.status}: ${text}\`)` where `text = await res.text()` (falls back to `res.statusText`). There is **no** structured error object — parse `err.message` with `/^API (\d{3}): (.*)$/s` if you need the status.
- 204/205 or empty body → resolves `undefined`; otherwise `JSON.parse(text)`.
- `get(path, init)` spreads `init` then forces `method: 'GET'` (you can pass `signal`, `headers`).

### 2.3 Other hooks

| Hook | Signature | Real behaviour |
|---|---|---|
| `useAppEvents(event: string, cb: (data: unknown) => void): void` | | Warns `[app-sdk] App "<name>" not permitted to subscribe to event "<event>"` unless `permissions.events` includes the event or `'*'`; then `window.addEventListener(\`mc:app:${event}\`, …)`. **Host never dispatches these** → dead today. Do not rely on it; poll or open your own `WebSocket`/`EventSource`. |
| `useTheme(): AppTheme` | | `mode = root.dataset.theme || 'dark'` (buggy for non-default themes, see §0), `accent = getComputedStyle(root).getPropertyValue('--accent').trim()`, `colorTheme = root.dataset.colorTheme || 'default'` (always default). Re-renders via `MutationObserver` on `data-theme`, `data-color-theme`, `style`. Use the replacement in §10.3. |
| `useAppInfo(): AppInfo` | | `{ name, version: manifest.version ?? installed.version ?? '0.0.0', permissions }` |
| `useNavigate(): (path: string) => void` | | Wraps react-router `navigate(path)` — accepts `'/apps/aidlc-studio?view=x'`, `'/chat'`, `'/settings'`. Host-controlled routes only. |
| `useNotify(): (message: string, opts?: { type?: 'info' \| 'success' \| 'error' }) => void` | | Dispatches `mc:notify`; **no host listener**. Render your own inline banner. |
| `useNavBadge(): (count: number) => void` | | Dispatches `new CustomEvent('mc:app:badge', { detail: { appName: info.name, count } })`. Host (`App.tsx:1268-1277`) stores `appBadges[appName]`; `NavBadge` strips the `app-` prefix from the nav id (`navId.slice(4)`) so it matches. **Works.** `0`/`undefined` clears. |
| `useChatLauncher(): { openChat(opts?: ChatLaunchOptions): void }` | | Writes `window.__mc_chat_launch = { agent, message, ts: Date.now() }` then `navigate('/chat')`. `ChatPage.tsx:1283-1294` consumes it on mount if `Date.now() - ts <= 10_000`: `agent` → `setPendingAgent`, `message` → **auto-sent in a new session** (`autoSendRef`, `newSessionRef = true`). |
| `AppApiProvider(props)` | `{ appName, appVersion?, allowedApiPaths, allowedEvents, subscribeFn, navigateFn, notifyFn, children }` | Used by AppHost; an app never renders it. |

No i18n / locale hook is exported (see §6).

### 2.4 Chat surfaces exported (for the PRD's "canonical sessions" panels)

- `useChatSession(opts: { workspacePath: string; label: string; agent?: string; appName?: string; seedTemplate?: (o: { label: string; path: string; isPackage: boolean }) => string }): { status: 'loading'|'ready'|'no-session'|'error'; slotKey: string|null; slotInfo: { key; title; messages; running }|null; creating: boolean; error: string|null; openChat(): void; createSession(): Promise<void>; resetSession(): void }` (`app-sdk/useChatSession.ts`).
- `ChatEmbed({ slotKey: string; agent?: string; placeholder?: string; frameless?: boolean; startAtBottom?: boolean; onSend?: (message: string) => Promise<unknown> | void })` — polls the slot via React Query (1 s streaming / 5 s idle) through `useAppApi()`; **your `permissions.api` must therefore include the chat endpoints it calls.** Exact calls (verified): `GET /api/chat/slots/<slotKey>`, `POST /api/chat` `{ message, slot, agent }`, `POST /api/chat/slots/<slotKey>/approve`; `useChatSession` adds `GET/POST /api/chat/folders`, `PATCH /api/chat/slots/<slotKey>/folder`, `GET/POST /api/chat/slots`. All sit under one prefix, so a single `"/api/chat"` entry covers them — which is exactly what the `spec-builder` builtin declares (`const CHAT_API_PATHS = ['/api/chat']`, `apps/spec-builder/SpecBuilderPage.tsx:31`).
- `ChatPanel({ slotKey })` — mounts the full `ChatPage embedded` and dispatches Redux `switchSlot`. Requires the host Redux store (present inside AppHost).
- `ChatMessageList({ messages: ChatMessage[]; running: boolean; contentWidth?: string ('900px'); onApprove?; onFileOpen?; renderTool?; hideCardOwnedOAuth?; renderers?: readonly MessageRenderer[] })`.
- `ChatMessage` (`website/src/types/index.ts:690`): `{ role: string; content: string; cls: string; ts?: string; rawText?: string; meta?: Record<string, unknown>; variants?; variant_idx?; _toolCount?; kind?: string }`.
- Protocol: `parseOptions(content) → { text, options, multi, isPlan }`, `deriveFollowUpOptions(messages, isStreaming) → { followUpOptions, followUpIsPlan }`, `extractSteeringAcks(content) → { cleaned, acks }`, `stripPartialOptionMarker(text)`.

---

## 3. `@kirocrew/app-sdk/ui` components (source `website/src/components/ui.tsx` unless noted)

All accept theme-token Tailwind classes via `className` merged with `tailwind-merge`. Props exactly:

| Export | Props | Notes |
|---|---|---|
| `Card` | `Omit<ComponentPropsWithoutRef<'div'>, 'dangerouslySetInnerHTML'>` | classes `card-glow border border-border bg-card rounded-lg p-5 mb-4 animate-rise shadow-sm transition-all`. `.card-glow > * { position:relative; z-index:1 }` traps overlays (see SegmentedControl note). Note the built-in `mb-4`. |
| `CardTitle` | `Omit<ComponentPropsWithoutRef<'h3'>, 'dangerouslySetInnerHTML'>` | `text-sm font-semibold tracking-tight text-text-strong mb-3.5 flex items-center gap-2` |
| `Btn` | `ButtonHTMLAttributes<HTMLButtonElement> & { danger?: boolean; primary?: boolean }` (forwardRef) | default outline; `primary` = `bg-accent text-accent-fg`; `danger` = hover red. 13px text. |
| `SendBtn` | `{ children } & Omit<ComponentPropsWithoutRef<'button'>, 'children'|'dangerouslySetInnerHTML'>` | solid accent, `h-9 rounded-lg`, `.btn-sweep` hover |
| `Input` | `InputHTMLAttributes<HTMLInputElement>` (forwardRef) | `bg-bg-elevated border border-border rounded-md px-3 py-2 text-sm focus-ring flex-1` |
| `SearchInput` | `InputHTMLAttributes<HTMLInputElement>` (`className` goes on the wrapper) | magnifier SVG, `text-[13px]`, `w-full` |
| `Badge` | `{ variant: 'ok' \| 'err' \| 'warn' \| 'aim' \| 'muted'; children: ReactNode } & span props` | pill, `font-mono text-[13px]`, `hover:scale-105` |
| `StatCard` | `{ label: string; value?: string \| number \| null; accent?: boolean; colorClass?: string; delay?: number; onClick?: () => void; active?: boolean; title?: string } & div props` | `value == null` → shows `.skeleton` loader; `title` renders an `InfoTip`; keyboard-accessible when `onClick`; `data-testid="stat-card"` |
| `Skeleton` | `ComponentProps<'div'>` | `bg-bg-hover animate-pulse rounded-md` |
| `ContentSkeleton` | `{ rows?: number }` (default 5) | |
| `EmptyState` | `{ icon: ReactNode; title: string; subtitle?: string; action?: ReactNode; testId?: string }` | icon rendered at 40px, 12% opacity — pass a lucide element, not an emoji (`AUTOSDE` rule `no-emoji-as-icons`) |
| `PageHeader` | `{ title: ReactNode; subtitle?: string; actions?: ReactNode }` | `px-6 pt-2 pb-3`, title `text-2xl font-bold text-text-strong`; `data-testid="page-header"` |
| `Toggle` | `{ checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: string; describedBy?: string; tone?: 'accent' \| 'muted' }` | `role="switch"` div, 36×20 px |
| `InfoTip` (`components/InfoTip.tsx`) | `{ text: string; placement?: 'auto' \| 'top' }` | portal tooltip, click-to-open |
| `SegmentedControl` (`components/SegmentedControl.tsx`) | `{ segments: Segment<T>[]; value: T; onChange: (v: T) => void; layoutId?: string; collapse?: boolean }` with `Segment = { key: T; label: string; icon?: ReactNode; count?: number; tooltip?: string; disabled?: boolean }` | Auto-collapses full → compact → dropdown by measuring the **parent** width; pass `collapse={false}` inside `shrink-0`/`inline-flex` parents and inside `Card` (z-index trap). Framer Motion inside. |
| `MarkdownRenderer` (`components/MarkdownRenderer.tsx:2223`, default export, `memo`) | `{ content: string; streaming?: boolean; onFileOpen?: (path, opts?: { line?; endLine? }) => void; onFolderOpen?: (path) => void; onArtifactOpen?: (slug) => void; rawMode?: boolean; sourcePos?: boolean; messageTs?: string; slotKey?: string; glow?: boolean; smooth?: boolean; softBreaks?: boolean; compactImages?: boolean; linkPreviews?: boolean }` | see below |

Not exported but present in `ui.tsx` (only via `window.__kirocrew_modules['@kirocrew/ui']`? **No** — `kirocrew-ui/index.ts` re-exports only the listed names plus `SourceBadge`): `IconButton`, `IconButtonGroup`, `FilteredEmpty`, `PanelSectionHeader`, `Slider`, `Checkbox`, `FormSkeleton*` are **not reachable** by apps. Copy the class strings from `ui.tsx` if you need them.

### 3.1 MarkdownRenderer sanitisation and mermaid (verified in code)

- Pipeline: `react-markdown` + `remark-cjk-friendly` → `remark-gfm` → `remark-cjk-friendly-gfm-strikethrough` → `remark-math` (`singleDollarTextMath: false`) → `rehype-raw` → **`rehypeSanitize()`** (exported, `MarkdownRenderer.tsx:1119`) → `rehype-katex`.
- `rehypeSanitize` is an **allowlist**: `ALLOWED_TAGS` (line 960) covers block/inline/table/`details`/`summary`/`img`/`video`/`audio`/basic SVG shapes/`math`. Unknown tags are re-emitted as **escaped literal text** (`<span class="escaped-tag">`), never dropped silently. `input` survives only as a disabled GFM checkbox. Attributes go through a per-element allowlist (`isAllowedAttr`); `style` and `on*` are dropped; `javascript:`, `data:`, `vbscript:` values are dropped except `data:image/*` on `img src`.
- Links: `urlTransform` (`utils/urlTransform.ts`) only whitelists custom protocols `vscode:`/`vscode-insiders:`; http(s) pass through as `MdAnchor` with external-link handling. `/artifacts/<slug>` hrefs call `onArtifactOpen`.
- Path chips: inline code that looks like a path becomes a clickable chip calling `onFileOpen(path, { line, endLine })` / `onFolderOpen` — you get this behaviour for free if you pass handlers (Studio can route them to its own artifact viewer).
- **Mermaid: yes.** A ` ```mermaid ` fence renders `MermaidBlock` (line 790), lazy-loading `mermaid` on first use with `securityLevel: 'strict'`, `suppressErrorRendering: true`, dark/light `themeVariables` picked by `data-theme` containing `dark`; parse errors fall back to a plain `<pre>`. Also ` ```excalidraw ` → `ExcalidrawBlock`, diff fences → `DiffBlock`, others → `CodeBlock` (highlight.js) / `MonacoCodeBlock`.
- Styling: the renderer's root is `<div class="group …">`; the host's prose styles (`.msg-content code`, `.msg-content pre`, `.msg-content a`, tables) are keyed on an ancestor **`.msg-content`**. Wrap it yourself: `<div className="msg-content text-sm"><MarkdownRenderer content={md} /></div>`.
- It calls host internals (`api` client for link meta, React Query, Redux-free) — safe to use inside AppHost.

### 3.2 Host-only sanitiser helpers

`website/src/api/helpers.ts`: `esc(s)`, `md(t)` (tiny regex markdown + `DOMPurify.sanitize`), `sanitize(html)`. **Not exported to apps.** If Studio needs raw-HTML injection, ship its own DOMPurify or avoid `dangerouslySetInnerHTML`.

---

## 4. How AppHost mounts an app; URLs; sub-navigation

### 4.1 Route table (`website/src/App.tsx:2571-2584`)

```tsx
<Route path="/apps" element={<AppsPage />} />
<Route path="/apps/detail/:name" element={<AppDetailPage />} />
<Route path="/apps/migrate/:name" element={<MigrationPage />} />
<Route path="/apps/:name" element={<AppPage />} />
...
<Route path="/:builtinApp" element={<BuiltinAppRoute />} />
<Route path="*" element={<ChatRedirect />} />   // → /chat
```

- **Your page URL is `/apps/aidlc-studio`.** `appNav.ts › appNavTarget()` routes every non-builtin app (and any builtin with `ui.entry`) to `` `/apps/${app.name}` `` **regardless of `ui.pages[0].route`**. The prototype's `"route": "/aidlc-console"` is therefore never used as a URL; `route` and `label` are merely required non-empty strings (`manifest.py:1046-1050`). Set `route` to `/apps/aidlc-studio` to keep docs honest.
- **No nested path segments.** `path="/apps/:name"` has no splat, so `/apps/aidlc-studio/actions/42` matches nothing but `*` → redirect to `/chat`. Server side, `_APPS_SPA_EXCLUDED_RE = ^/apps/[a-z0-9][a-z0-9_-]*/(?:api|ui)/` (`token_auth.py:552`) means any other `/apps/...` path is served the SPA shell, so the redirect happens client-side after refresh.
- **Sub-navigation must use the query string or hash**: `/apps/aidlc-studio?view=actions&action=42&tab=review#finding-3`. Both survive React Router matching, browser refresh, and deep links from Slack (PRD FR-SLK-008). Nav highlight uses `activePath === n.path || activePath.startsWith(n.path + '/')` (`App.tsx:1723`), so the rail stays lit while on `/apps/aidlc-studio?…`.
- **One bundle per app.** `AppHost` reads only `manifest.ui.entry` and ignores `pages[1..]` and `pages[].entryPoint`; the sidebar reads only `pages[0]`. Multiple sidebar entries for one app are **not** possible today.

### 4.2 Mount mechanics (`website/src/components/AppHost.tsx`)

```tsx
const bundlePath = `/apps/${app.name}/ui/${entry}`          // entry = manifest.ui.entry
const LazyApp = useMemo(() => lazy(() =>
  import(/* @vite-ignore */ bundlePath).catch(err => ({ default: () => <FailedCard/> }))
), [bundlePath, resetKey])
...
<AppErrorBoundary appName={app.name} onReset={() => setResetKey(k => k + 1)}>
  <AppApiProvider appName appVersion allowedApiPaths={permissions.api||[]} allowedEvents={permissions.events||[]} …>
    <Suspense fallback={<AppLoadingSkeleton/>}><LazyApp /></Suspense>
  </AppApiProvider>
</AppErrorBoundary>
```

- `ui.entry` is resolved **relative to `<app>/ui/`**, not the app root (`ui/index.mjs` → `/apps/x/ui/ui/index.mjs` 404). The manifest-reference's `"dist/index.mjs"` example only works if your build emits to `ui/dist/`. Prototype uses `"entry": "index.mjs"` with the file at `ui/index.mjs` — correct.
- The module's **`default` export must be a React component** (function or memo/forwardRef object). No props are passed. `mountFunction` is never called.
- Guards before mount: no record → "App not found"; `enabled === false` → "disabled"; no `ui.entry` → "agent-only app".
- Crash → `AppCrashFallback` with `Retry` (remounts via `resetKey`) and `Apps` buttons; load failure → "Failed to load <displayName>" + `err.message`.
- `AppPage` fetches `GET /api/apps/<name>` (`api.getApp`) and hands the record to AppHost; `origin === 'builtin' && !ui.entry` redirects to the native route.
- `mc:app-reload` window event (from WS `app_reload`, dev mode) → `window.location.reload()` when `detail.app === app.name`.
- Static serving: `GET /apps/{name}/ui/{path:.*}` → `handle_app_ui_file` (`apps/routes.py:1779`): file must exist under `~/.kiro/crew/apps/<name>/ui/`, extension in `{.mjs,.js,.css,.json,.svg,.png,.jpg,.jpeg,.gif,.webp,.woff,.woff2,.ttf,.map}`, served with `Content-Type: application/javascript` for `.mjs/.js`, `Cache-Control: no-cache` (or `no-store` in dev mode). The path is **unauthenticated** (`_APPS_UI_BYPASS_RE`) — never put secrets in `ui/`.
- Same-origin CSP (`server.py:534-556`): `script-src 'self' 'unsafe-inline'`, `style-src 'self' 'unsafe-inline' …`, `img-src 'self' data: blob: https:`, `connect-src 'self' ws://localhost:* …`. Inline `style={{}}` and a `<link rel="stylesheet" href="/apps/aidlc-studio/ui/studio.css">` are allowed; third-party script CDNs are not.
- Your component renders inside `<main id="main-content" class="flex flex-col min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto">` (`App.tsx:2551`; `needsFixedHeight` is false for `/apps/*`, so `<main>` scrolls). To get an independently scrolling master/detail (PRD §10.2) make your root `flex-1 min-h-0 flex overflow-hidden` and put `overflow-y-auto min-h-0` on each column; `<main>` will then not scroll because your root fills it.

---

## 5. CSS available to app markup

### 5.1 Tailwind: compiled, host-scanned, no runtime JIT, no safelist

`website/tailwind.config.js`: `content: ['./index.html', './src/**/*.{ts,tsx}']`, `darkMode: ['selector', '[data-theme="dark"]']`, **no `safelist`**. The built sheet is `dist/assets/src-*.css` (212 KB). `@tailwindcss/browser` in `dist/vendor/` is loaded only into sandboxed widget iframes, never into the dashboard document. **Therefore an app can use only utility classes that some host `.tsx` file already uses.** Verified present (grep of the built CSS): `px-6 pb-8 pt-4`, `p-0…p-8`, `px-0…px-16`, `py-0…py-20`, `gap-0…gap-6` (incl. `.5` steps), `grid grid-cols-1/2/3`, `md:grid-cols-2`, `lg:grid-cols-2/3`, `sm:grid-cols-2/3/4`, `xl:grid-cols-2/3/4/6`, `grid-cols-[repeat(auto-fit,minmax(120px|130px|150px|280px,1fr))]`, `grid-cols-[auto_1fr]`, `grid-cols-[140px_1fr]`, `col-span-2`, `space-y-2`, `w-1/3 w-2/3`, `max-w-xs…max-w-4xl max-w-6xl max-w-full`, `text-[10px]…text-[24px] text-sm text-base text-lg text-xl text-2xl`, `hidden sticky truncate line-clamp-2 tabular-nums aspect-video overflow-y-auto min-h-0 flex-1 font-mono rounded-full animate-rise animate-pulse animate-spin`, every token colour utility (`text-muted bg-card border-border bg-ok-subtle text-ok text-warn bg-warn-subtle text-danger bg-danger-subtle bg-accent-subtle text-accent border-accent text-text-strong bg-bg-elevated hover:bg-bg-hover`), `focus-ring lucide-inline skeleton card-glow table-striped`.
Verified **absent**: `grid-cols-4`, `grid-cols-12`, `col-span-4/8`, `basis-*`, `w-[36%]`, `w-[64%]`, `max-w-5xl`, `md:` variants beyond `block border-l flex grid grid-cols-2 hidden pl-3 shrink-0 sticky top-2` (+ a few arbitrary), `lg:` beyond `block flex-none flex-row grid-cols-2/3 h-28/36/full hidden inline pl-0 pt-4 w-24/28`.
Responsive breakpoints in the built CSS: `640/768/1024/1280/1536px` (Tailwind defaults) plus host media rules `(max-width: 767px)`, `(pointer: coarse)`, `prefers-reduced-motion`.

**Implication:** treat Tailwind as a convenience for common spacing/colour, and ship a **small app CSS file** (`ui/studio.css`, loaded once via a `<link>` you append to `document.head` or a `<style>` element) for layout that the host does not already compile: the 36/64 master-detail grid, sticky action bar, swimlanes, phase accents. Write it against the tokens in §5.2, never literal colours (`theming-contract.md`). Any arbitrary-value class your bundle uses must be re-checked against the *installed* host version's CSS at runtime — different KiroCrew releases scan different sources.

### 5.2 Design tokens (CSS custom properties)

Declared per theme in `website/src/index.css` (default dark block at line 294, light at 320; 30+ theme blocks follow). Names an app should use (all 54 allowlisted in `hooks/themeCss.ts`; representative subset):

```
--bg  --bg-accent  --bg-elevated  --bg-hover
--card  --card-fg  --card-hl  --chrome
--text  --text-strong  --muted  --muted-fg  --muted-strong
--border  --border-strong  --border-hover
--accent  --accent-fg  --accent-hover  --accent-subtle  --accent-glow  --ring
--ok  --ok-fg  --ok-subtle
--warn  --warn-fg  --warn-subtle
--danger  --danger-fg  --danger-subtle
--info  --info-fg            (no --info-subtle var; Tailwind `bg-info-subtle` = color-mix(--info 12%))
--aim --aim-fg --aim-subtle  --clarify --clarify-subtle
--diff-add --diff-add-text --diff-del --diff-del-text --diff-hunk --diff-hunk-text --diff-meta-text
--shadow-sm --shadow-md --shadow-lg
--radius-sm:6px  --radius-md:8px  --radius-lg:12px  --radius-xl:16px      (:root, not themeable)
--font-body  --mono            (see fonts)
```

Default dark values (line 296-307): `--bg:#12141a --bg-elevated:#1a1d25 --bg-hover:#262a35 --card:#181b22 --text:#e4e4e7 --text-strong:#fafafa --muted:#7f7f88 --border:#27272a --border-strong:#3f3f46 --accent:#00d492 --accent-fg:#000 --ring:#10b981 --ok:#22c55e --warn:#eab308 --danger:#ef4444 --info:#0891b2`. Default light (322-333): `--bg:#fafafa --card:#ffffff --text:#3f3f46 --text-strong:#18181b --muted:#71717a --border:#e4e4e7 --accent:#047558 --accent-fg:#fff --ring:#047558 --ok:#16a34a --warn:#a16207 --danger:#dc2626`.

Tailwind colour utilities map 1:1 (`bg-bg`, `bg-card`, `text-text`, `text-muted`, `border-border`, `bg-accent`, `text-ok`, …) through `withAlpha()` so `text-muted/50`, `border-border/30` etc. work via `color-mix`. `rounded-sm/md/lg/xl` → `var(--radius-*)`; `shadow-sm/md/lg` → `var(--shadow-*)`; `font-body`/`font-mono` → `var(--font-body)`/`var(--mono)`.

Utility classes worth reusing: `.focus-ring:focus { outline:none; border-color:var(--ring); box-shadow:0 0 0 3px var(--accent-subtle), … }`, `.skeleton` (shimmer, `--bg-elevated`/`--bg-hover`), `.table-striped tbody tr:nth-child(even){background:var(--card-hl)}`, `.lucide-inline { width:1em;height:1em;vertical-align:-0.125em }`, `.animate-rise`, `.card-glow`, `.stat-accent`.

### 5.3 Theme switching attributes (`hooks/useTheme.tsx:166-181`)

```
<html data-theme="dark|light"                       // ONLY for the default 'emerald' theme
      data-theme="<slug>-dark|<slug>-light"          // e.g. monokai-dark, kiro-light, custom-<slug>-dark
      data-mode="dark|light"                         // resolved mode — use this
      data-mode-pref="dark|light|system"
      data-ui="cli"?  lang="<bcp47>">
```

Slugs present in `index.css`: `dark/light` (emerald), `monokai, solarized, amber, nord, dracula, rosepine, catppuccin, tokyonight, gruvbox, ice, amoled, kiro, intellij, highcontrast, everforest, amoled-midnight, amoled-grey-calm` (each `-dark`/`-light`). Host's own dark test is `getAttribute('data-theme').includes('dark')` (`MarkdownRenderer.tsx:276`). Theme changes are also broadcast as `window` CustomEvent `mc-theme-sync` `{ mode, colorTheme }`. Pure CSS in your stylesheet needs nothing: `var(--*)` re-resolves automatically.

### 5.4 Fonts

`body{font:400 0.875rem/1.55 var(--font-body); background:var(--bg); color:var(--text)}` (`index.css:1218`).
`--font-body: var(--theme-font-sans, var(--script-fallbacks),'Space Grotesk',-apple-system,BlinkMacSystemFont,sans-serif)`; `--mono: var(--theme-font-mono, var(--script-fallbacks-mono),'JetBrains Mono',ui-monospace,SFMono-Regular,monospace)`. Google Fonts are preloaded in `index.html`. `--script-fallbacks` swap per `html:lang(ja|ko)` — so setting nothing and inheriting `font-family` gives you CJK-correct fallbacks. Rules: no `text-xs` and nothing below 10px (`AGENTS.md`); tokens only, no literal colours; icons `lucide-react` with `className="lucide-inline"`.

---

## 6. Locale: how the host resolves it and how Studio can follow it

Host (`website/src/i18n/`):
- Precedence (`detect.ts`): explicit choice (`dashboard.language` in workspace config, mirrored to **`localStorage['mc-lang']`**) → `navigator.languages` matched against `DETECTABLE_CODES` → `'en'`. Auto is stored as `''`.
- Shipped catalogs (`languages.ts`): `en, zh-CN, hi, es, fr, bn, pt, ru, de, ja, ko, it` (+ dev-only `en-XA`). No RTL.
- `LanguageProvider.tsx:223` writes the resolved tag to **`document.documentElement.lang`** on every change (pre-hydration script in `index.html` seeds it from `mc-lang`). i18next fires `languageChanged`, but i18next is not exposed to apps.
- Server: `GET /api/theme/boot` (unauthenticated) and `GET/PUT /api/config/theme` return `{ mode, color, language, onboarded, import_onboarded, privacy_acked }` (`dashboard/handlers/core.py:314-334`); `language` is `''` or a BCP-47 tag validated by `^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}$`.
- Formatting seam: `i18n/format.ts` (`fmtDate`, `fmtRelative`, `compareText`, `activeLocale()`, …) — **not exported to apps.**

**There is no `window.__kirocrew_locale`, no SDK locale hook, and no exported `t()`.** Recommended: read `<html lang>` and subscribe to its mutations (it is the single value the host guarantees to keep current), fall back to `mc-lang`, then `navigator.language`. Snippet in §10.4. Studio must ship its own `en`/`zh-CN` catalogs (PRD §17) and use `Intl.*` with that tag for dates/numbers/collation, mirroring the host's rule "never format without naming a locale".

---

## 7. Responsive behaviour of the host shell

- `hooks/useIsMobile.ts`: `MOBILE_BREAKPOINT = 768`, query `(max-width: 767px)`; forced `false` under `/embed/`.
- Desktop grid (`App.tsx:1776-1790`): rows `42px` topbar + content; columns `<rail>px minmax(0,1fr) [auto]`. Rail width (`hooks/useRailWidth.ts`): expanded **236px**, collapsed **74px** (user toggle persisted in `localStorage['mc-nav']`; auto-collapses in some chat states), transition 150 ms. So the content column is `100vw − 236` or `100vw − 74` px (minus an optional right activity bar).
- Mobile (`< 768px`): grid becomes single column `"topbar" "content"`, rail width 0, nav is a 220 px slide-in drawer (`fixed top-0 left-0 bottom-0`, z-50) with a `bg-black/50` backdrop, closed automatically on route change (`App.tsx:1667`). No activity bar. Content still sits inside the scrolling `<main>`.
- Host CSS at `(pointer: coarse)` only bumps the chat composer to 16 px. The PRD's 390 px target is well inside the host's mobile branch; your own breakpoints should key on your root's width (ResizeObserver or a container query) rather than `window.innerWidth`, because the rail and side panels eat width on desktop.

---

## 8. Building: Vite externals, or hand-written ESM (both supported)

### 8.1 Scaffolded Vite config (exact text from `src/kiro_crew/apps/scaffold.py:118-139`)

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  build: {
    lib: {
      entry: 'src/App.tsx',
      formats: ['es'],
      fileName: () => 'index.mjs',
    },
    outDir: 'dist',
    rollupOptions: {
      external: [
        'react', 'react-dom', 'react/jsx-runtime',
        '@kirocrew/app-sdk', '@kirocrew/app-sdk/ui', 'lucide-react',
      ],
    },
  },
})
```

`package.json` template (scaffold.py:95-116): deps `react ^18.2.0`, `react-dom ^18.2.0`; devDeps `@vitejs/plugin-react ^4.2.0`, `vite ^5.0.0`; peerDeps `@kirocrew/app-sdk *`, `lucide-react *` (there is **no published npm package**; for TypeScript add a local `types/kirocrew-app-sdk.d.ts` that mirrors §2/§3, or `paths` aliases to a vendored copy of `website/src/app-sdk/index.ts`). Add `'react-dom/client'` to `external` if you import it. Keep `"jsx": "react-jsx"` so emitted code imports `react/jsx-runtime` (mapped). Because `ui.entry` is relative to `ui/`, either build to `ui/dist/index.mjs` and set `"entry": "dist/index.mjs"`, or `outDir: '.'` → `"entry": "index.mjs"`. Also `build.cssCodeSplit`: a lib build emits `style.css` next to the bundle; inject it yourself (see §10.5) — the host does not load app CSS.

The `docs/app-kit/examples/full-app/` example ships only `ui/src/App.tsx` (no vite config or package.json); the scaffold template above is the only exact externals snippet in the repo.

### 8.2 Hand-written ESM without a build (prototype style) — supported

`AppHost` performs a plain dynamic `import()` of whatever `/apps/<name>/ui/<entry>` serves as `application/javascript`; nothing requires a bundler. The in-repo `public/apps/demo-app/ui/index.mjs` and `public/apps/agent-worlds/ui/index.mjs` are exactly this (they read `window.__kirocrew_modules` directly and use `createElement`). The prototype's approach — `import { useState } from 'react'` + `import { jsx as _jsx, jsxs as _jsxs } from 'react/jsx-runtime'` — resolves through the import map to `/vendor/react.mjs` and `/vendor/react-jsx-runtime.mjs`, both of which export those names. **Confirmed working.** Constraints: one file (or several `.mjs` files importing each other by relative URL — allowed extensions only), no TypeScript, validate with `node --check ui/index.mjs`. Feature-detect optional host modules through `window.__kirocrew_modules` instead of bare imports so an older host degrades instead of failing the module link (the prototype already does this correctly).

### 8.3 Dev loop

`kirocrew app dev <name>` (or `POST /api/apps/{name}/dev {"enabled":true}`) → `ui/` served `no-store` and watched; any change broadcasts WS `app_reload` → `mc:app-reload` → full page reload. Symlink `~/.kiro/crew/apps/<name>/ui` to your source tree (install/update re-copies and strips symlinks). Backend hook changes need a gateway restart or disable→enable.

---

## 9. App Store card / detail assets (what the store actually reads)

Fields live in `app.json` at **top level** (kept in `AppManifest.extra`, round-tripped by `to_dict()` so `GET /api/apps` exposes them). Publishing guide table (`docs/app-kit/publishing-guide.md:93-100`) and code agree:

| Field | Where rendered | Aspect / size | Resolution in code |
|---|---|---|---|
| `iconPath` / `iconPathDark` (registry apps) | Discover row tile, Library card tile, gradient fallback centre | square **512×512, opaque** (checklist accepts ≥256) | `registry.py:946-954` rewrites to `/api/apps/blob?repo=<repo>&path=<p>` → `iconUrl`; `InstalledAppCard.tsx:53` does the same client-side **only if `manifest.repo` is set** |
| `iconUrl` / `iconUrlDark` (builtins) | same | SVG | absolute `/app-assets/<app>/icon.svg` is fetched and **inlined** and repainted via `--ico-a/--ico-b` when it matches `^\/app-assets\/[a-zA-Z0-9_-]+\/[a-zA-Z0-9_-]+\.svg$`; any other URL is a plain `<img>` (`components/AppIcon.tsx`). Registry ignores a manifest `iconUrl` (untrusted). |
| `ui.pages[0].iconUrl` | **left-rail** icon for installed apps (`<img src="/apps/<name>/ui/<iconUrl>" class="w-4 h-4 rounded-sm object-contain">`) and AppDetailPage fallback icon | 16 px rendering; ship a 64–128 px SVG/PNG | relative to `ui/` |
| `heroImage` / `heroImageDark` | Discover rows, Library rows (16:9 capsule), featured spotlight, feature cards, detail banner fallback | **16:9**, e.g. 1200×675 | `useHeroArt.ts`: current theme → other theme → `screenshots[0]`; repo-relative → blob proxy, absolute/URL untouched; 404 → name-seeded gradient + icon |
| `heroImageDetail` / `heroImageDetailDark` | detail-page banner only | **25:6**, e.g. 1200×288 | `AppDetailPage.tsx:552-565` |
| `screenshots` / `screenshotsDark` | detail-page gallery + lightbox; first one is last-resort hero | landscape ≈1200 px wide | blob proxy for registry apps |
| `highlights: string[]` | detail-page feature bullets | plain text | raw (third-party copy is not localised by the host) |
| `icon` (top-level, lucide name) | store card fallback glyph | one of `Shield, Bot, Search, Tag, Users, Zap, Star, Package, Cat` (AppIcon `ICON_MAP`) | anything else → `Package` |

Blob proxy (`apps/routes.py:1846+`): only repos present in a configured registry (SSRF guard), extensions `.png .jpg .jpeg .gif .webp .svg .ico`, no `..`, no absolute paths, no dot-segments. **Consequence for local development before the registry entry exists:** repo-relative art will not resolve (gradient fallback). Workaround: also put a copy under `ui/` and point **`iconUrl`/`heroImage*` at `/apps/aidlc-studio/ui/<file>`** for local installs — but registry.py would then try to blob-proxy `heroImage` verbatim (it does not skip absolute paths), producing a broken URL in Discover. Pick one mode per published manifest: repo-relative paths + `"repo"` for the store build; absolute `/apps/…/ui/` paths only in a dev-only manifest. Sidebar icon (`ui.pages[0].iconUrl`) is unaffected and always works.

Manifest identity rules (`publishing-guide.md`, `manifest.py`): `name` `^[a-z0-9]+(?:-[a-z0-9]+)*$` (not `system`, not Windows device stems); `version` semver; `displayName` truncates in a fixed-width row; `description` plain text, 2–3 sentences; `tags` decide the Discover category (`developer-tools`, `workflows`, `automation`, … — `aidlc` alone lands in **Other**, so include `developer-tools` and `workflows`).

---

## 10. Paste-ready snippets

### 10.1 `app.json` for AI-DLC Studio (fields the host actually honours)

```json
{
  "name": "aidlc-studio",
  "version": "0.1.0",
  "displayName": "AI-DLC Studio",
  "description": "Action Center, repository registry, workflow map and evidence trail for AI-DLC Workflows across many repositories.",
  "author": "ychchen",
  "license": "Apache-2.0",
  "minKiroCrewVersion": "TBD",
  "tags": ["aidlc", "developer-tools", "workflows", "automation"],
  "highlights": [
    "Cross-repo Action Center for gates, questions and recovery decisions",
    "Evidence-first decision pane with artifact diff and reviewer findings",
    "Five-phase Workflow Map with stage inspector"
  ],
  "repo": "https://github.com/<org>/<repo>",
  "iconPath": "assets/icon-512.png",
  "screenshots": ["assets/screenshots/action-center.png"],
  "heroImage": "assets/hero-light.png",
  "heroImageDark": "assets/hero-dark.png",
  "heroImageDetail": "assets/hero-detail-light.png",
  "heroImageDetailDark": "assets/hero-detail-dark.png",
  "backend": { "hooks": { "routes": "backend.routes:register_routes" } },
  "ui": {
    "entry": "index.mjs",
    "pages": [
      { "route": "/apps/aidlc-studio", "label": "AI-DLC Studio", "icon": "GitBranch", "iconUrl": "icon.svg" }
    ]
  },
  "permissions": {
    "api": ["/api/apps/aidlc-studio"],
    "events": [],
    "storage": true,
    "network": false
  }
}
```

(`backend.routes` must stay unset — see prototype `tests/test_manifest.py`. `ui/icon.svg` gives the rail its icon; `icon` is only a fallback glyph for builtins.)

### 10.2 Module skeleton (hand-written ESM, feature-detected host modules)

```js
// ui/index.mjs — default export is what AppHost renders
import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { jsx as _jsx, jsxs as _jsxs, Fragment } from 'react/jsx-runtime'
import { useAppApi, useAppInfo, useNavigate, useNavBadge } from '@kirocrew/app-sdk'
import { Card, CardTitle, Btn, Badge, PageHeader, EmptyState, StatCard, Skeleton, SegmentedControl, MarkdownRenderer } from '@kirocrew/app-sdk/ui'
import lucide from 'lucide-react'                     // default Proxy: any icon name works

const mods = (typeof window !== 'undefined' && window.__kirocrew_modules) || {}
const rq = mods['@tanstack/react-query']              // optional: useQuery etc. (host provides the QueryClient)
const { GitBranch, ShieldCheck, CircleDot } = lucide  // NOT `import { GitBranch } from 'lucide-react'`

export default function AidlcStudio() {
  const api = useAppApi()
  const setBadge = useNavBadge()
  // api.get('/api/apps/aidlc-studio/actions') … must stay under permissions.api
  return _jsxs(Fragment, { children: [
    _jsx(PageHeader, { title: 'AI-DLC Studio' }),
    _jsx('div', { className: 'studio-root flex-1 min-h-0 flex overflow-hidden' /* own CSS below */ }),
  ]})
}
```

### 10.3 Correct theme mode hook (replaces SDK `useTheme().mode`)

```js
function useHostMode() {                                    // 'dark' | 'light'
  const read = () => document.documentElement.dataset.mode
    || ((document.documentElement.dataset.theme || 'dark').includes('dark') ? 'dark' : 'light')
  return useSyncExternalStore(cb => {
    const mo = new MutationObserver(cb)
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-mode', 'data-theme'] })
    return () => mo.disconnect()
  }, read, read)
}
```

### 10.4 Locale that follows the dashboard language picker

```js
const STUDIO_LOCALES = ['en', 'zh-CN']
function pickLocale(tag) {
  if (!tag) return null
  const t = tag.trim()
  return STUDIO_LOCALES.find(l => l.toLowerCase() === t.toLowerCase())
    || STUDIO_LOCALES.find(l => l.split('-')[0] === t.split('-')[0].toLowerCase()) || null
}
function readHostLocale() {
  let stored = ''
  try { stored = localStorage.getItem('mc-lang') || '' } catch {}
  return pickLocale(document.documentElement.lang)          // set by LanguageProvider on every switch
      || pickLocale(stored)                                 // explicit user choice ('' = auto)
      || (navigator.languages || [navigator.language]).map(pickLocale).find(Boolean)
      || 'en'
}
function useHostLocale() {
  return useSyncExternalStore(cb => {
    const mo = new MutationObserver(cb)
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['lang'] })
    window.addEventListener('storage', cb)                  // cross-tab mc-lang changes
    return () => { mo.disconnect(); window.removeEventListener('storage', cb) }
  }, readHostLocale, () => 'en')
}
// then: new Intl.DateTimeFormat(locale, { dateStyle: 'medium' }), new Intl.Collator(locale), …
```

If a server-authoritative value is preferred, `GET /api/theme/boot` → `.language` (`''` means auto); it is outside `permissions.api`, so fetch it with plain `fetch`, not `useAppApi`.

### 10.5 Sub-navigation inside `/apps/aidlc-studio` (query string, no router available)

```js
function readView() {
  const sp = new URLSearchParams(window.location.search)
  return { view: sp.get('view') || 'actions', action: sp.get('action') || '', tab: sp.get('tab') || 'decision' }
}
function useStudioRoute() {                                // React Router pushState does not fire popstate; poll the URL via a tiny store
  const [route, setRoute] = useState(readView)
  const navigate = useNavigate()                           // host router keeps the shell in sync
  useEffect(() => {
    const onPop = () => setRoute(readView())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  const go = useCallback((patch, { replace = false } = {}) => {
    const sp = new URLSearchParams(window.location.search)
    Object.entries(patch).forEach(([k, v]) => v == null || v === '' ? sp.delete(k) : sp.set(k, String(v)))
    const url = `/apps/aidlc-studio?${sp}`
    replace ? window.history.replaceState(null, '', url) : navigate(url)
    setRoute(readView())
  }, [navigate])
  return [route, go]
}
```
Deep links: `https://<host>/apps/aidlc-studio?view=actions&action=<id>&tab=review#f-3` survive refresh (SPA shell) and rail highlighting. Never encode a path segment after the slug.

### 10.6 App stylesheet with host tokens (layout the host does not compile)

```js
// inject once at module load; served from ui/ (extension .css allowed, same-origin CSP ok)
if (!document.getElementById('aidlc-studio-css')) {
  const l = document.createElement('link'); l.id = 'aidlc-studio-css'; l.rel = 'stylesheet'
  l.href = '/apps/aidlc-studio/ui/studio.css'; document.head.appendChild(l)
}
```
```css
/* ui/studio.css — tokens only, no literal colours */
.studio-root{display:flex;flex:1 1 auto;min-height:0;font-family:var(--font-body);color:var(--text)}
.studio-queue{flex:0 0 36%;min-width:280px;border-right:1px solid var(--border);overflow-y:auto;min-height:0}
.studio-detail{flex:1 1 64%;min-width:0;display:flex;flex-direction:column;min-height:0}
.studio-detail-body{flex:1 1 auto;overflow-y:auto;min-height:0;padding:0 24px 96px}
.studio-actionbar{position:sticky;bottom:0;background:var(--chrome);backdrop-filter:blur(8px);border-top:1px solid var(--border);padding:12px 24px}
.studio-phase{border-left:3px solid var(--accent)}      /* restrained phase accents: swap --accent per phase */
.studio-row:focus-visible{outline:none;box-shadow:0 0 0 3px var(--accent-subtle);border-color:var(--ring)}
@media (max-width:767px){.studio-root{flex-direction:column}.studio-queue{flex:none;border-right:0}.studio-root[data-view="detail"] .studio-queue{display:none}.studio-root[data-view="list"] .studio-detail{display:none}}
@media (prefers-reduced-motion:reduce){.studio-root *{transition:none!important;animation:none!important}}
```

---

## 11. Open questions (not resolvable from code)

1. **Real-time events for apps.** `useAppEvents` is wired to `mc:app:<event>` that nothing dispatches. Studio's execution status strip (PRD §9.15/§13.2) needs push or polling; the host offers WS `/ws` (see `docs/app-kit/api-reference.md` "WebSocket Events") but there is no SDK path and no documented permission model for a third-party bundle opening its own WebSocket. Needs a decision: poll `/api/apps/aidlc-studio/*` (prototype does 30 s) vs. propose an upstream fix that bridges WS events to `mc:app:*`.
2. **`mc:notify` toast.** No listener exists; either the host adds one upstream or Studio renders inline notices. Which does the PRD prefer for delivery-uncertainty warnings?
3. **`minKiroCrewVersion`** — which host version first shipped `@kirocrew/app-sdk/ui` with `MarkdownRenderer`/`SegmentedControl` and `react-dom/client` in the import map? Not derivable from this checkout (no changelog tie-in); needs a `git log -S` on `public/vendor/kirocrew-ui.mjs` in the upstream repo.
4. **Store art for a locally installed (non-registry) app.** `InstalledAppCard` only resolves `iconPath` via `manifest.repo` + blob proxy, and the proxy only serves registry-listed repos, so pre-publication installs show the gradient fallback. Acceptable, or should the manifest carry absolute `/apps/aidlc-studio/ui/…` hero paths until the registry PR lands (which then breaks Discover)? Product call.
5. **Tailwind drift across host releases.** Which utility classes exist depends on the installed dashboard's scanned sources; there is no safelist and no runtime JIT. Decide whether Studio ships its own CSS for everything structural (recommended above) or accepts host-version coupling.
6. **`ui.pages[].route` semantics.** The frontend always uses `/apps/<name>`; the field is required but unused for third-party apps. Should Studio set it to `/apps/aidlc-studio` (honest) even though builtins use bare routes?
7. **Chat surfaces' API permissions.** Embedding `ChatEmbed`/`useChatSession` requires adding `"/api/chat"` to `permissions.api` (prefix covers every call, see §2.4). That widens the app's declared surface from its own namespace to the host's chat API; decide whether the PRD's least-privilege stance (FR-DIST-006) permits it, or whether Studio should launch sessions via `useChatLauncher()` (no extra permission) instead of embedding transcripts.
8. **Mobile drawer vs. Studio's own list→detail navigation.** The host closes its drawer on `location.pathname` change only; Studio's query-string navigation will not close it, which is fine, but the host's `isMobile` state is not exposed — Studio must run its own `matchMedia('(max-width: 767px)')`.

---

## Critic addendum (2026-09-04, completeness pass)

### A1. Re-verified against the running 0.5.0 bundle (`.../kiro_crew/static/dist`)

- `index.html` importmap is byte-for-byte the 7-entry map in §1.1. `vendor/` = `kirocrew-app-sdk.mjs kirocrew-ui.mjs lucide-react.mjs mermaid.min.js react-dom-client.mjs react-dom.mjs react-jsx-runtime.mjs react.mjs tailwindcss-browser.js`.
- `index.html` contains the `mc-lang` pre-hydration read and `documentElement.lang = l`; `localStorage['mc-lang']` is referenced by the built assets. §6 / §10.4 stand.
- No built asset contains `addEventListener("mc:notify"` → `useNotify()` is a no-op on the shipped 0.5.0 too. The `mc:app:${e}` listener exists in `assets/App-*.js` but no `"app_event"` string appears in the App bundle → `useAppEvents` is dead on 0.5.0 as well (agrees with doc 01 §3.3).

### A2. Corrections to sibling docs that cite this contract

- Doc 06 §14 and doc 07 §1.1 list `@tanstack/react-query` and `@kirocrew/ui` as "import map modules". They are **registry keys** (`window.__kirocrew_modules`) only; the bare specifiers `@tanstack/react-query` and `@kirocrew/ui` fail to resolve. Use `window.__kirocrew_modules['@tanstack/react-query']` and `@kirocrew/app-sdk/ui` respectively.
- Doc 06 §7 ("replace `window.alert` with the host `useNotify()` toast") and doc 07 §1.5 ("Toast: `sdk.useNotify()`") point at a dead hook; render inline notices (PRD §10.2 empty/error states) instead.
- Doc 08 §8 sets `ui.pages[0].route` to `/aidlc-studio`; this doc §4.1 recommends `/apps/aidlc-studio`. The field is unused for third-party apps either way (`appNav.ts` always routes to `/apps/<name>`); pick one (decision list).

### A3. Consequence for Studio's live-update design (PRD §13.2 status strip, §15 `GET /events`)

Because neither `useAppEvents` nor `useNotify` deliver anything, Studio's UI must (a) open its own `new WebSocket(\`${proto}//${location.host}/api/ws\`)` (cookie-authenticated; the browser supplies `Origin`, satisfying `ws.py:207-214`), unwrap `{"type":"app_event","data":{...}}` frames and re-dispatch `mc:app:<event>` CustomEvents so `useAppEvents` works, or (b) consume Studio's own SSE `GET /api/apps/aidlc-studio/events` (precedent `apps/routes.py::handle_registry_install_stream`, doc 01 §1.4), or (c) poll. (b) matches PRD §15's resume-cursor requirement; (a) additionally gives `chat_done`/`slots` frames for the canonical-session busy indicator.
