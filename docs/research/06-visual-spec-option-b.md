# 06 — Visual spec: Option B "Evidence First" (implementation spec)

Source of truth for this document (in precedence order when they disagree):

1. PRD `/Users/ychchen/warren_ws/kirocrew-app-aidlc/docs/AI-DLC-Studio-PRD.md` §8 (Information architecture) and §10 (Visual system) — requirements.
2. KiroCrew host code `/Users/ychchen/warren_ws/kirocrew/website/src/index.css`, `app-sdk/index.ts`, `app-sdk/shared-modules.ts`, `kirocrew-ui/index.ts` — the real token names/values and the module surface an App can import.
3. Mockup `/Users/ychchen/warren_ws/kirocrew-app-aidlc/mockups/option-b-evidence-first.html` (1577 lines, single-file HTML+CSS+JS) — the approved visual baseline (PRD §10.0). It is a *visual spec*, not code; its sample text, wire strings, and fixture math are illustrative.

Everything below is quoted from the mockup verbatim unless marked **PRD**, **HOST**, or **DEVIATION**. Line numbers refer to the mockup file.

---

## 0. Executive summary of what the mockup is

- One `<div class="app">` column: `header.topbar` (52px) → `div.scopebar` → `main.view` (flex, `overflow:hidden`).
- Seven routes rendered by a JS router into `#view`: `actions` (default), `repos`, `intents`, `map`, `wizard`, `activity`, `settings`.
- Action Center is a two-column master/detail grid `.ac{grid-template-columns:300px minmax(0,1fr)}`; queue on the left is "deliberately narrow and visually quiet: no severity fills, no competing color blocks. Weight goes to the decision brief."
- Six action types are mocked with full templates: `recovery`, `gate`, `questions`, `failure`, `install`, `budget`. No "missing input" template is mocked (only budget stop).
- Severity is always rendered as **2px rule + icon + text label** ("Never fill alone", line 202).
- The centerpiece is the side-by-side **artifact vs. reviewer findings** comparison (`.compare`), with reviewer findings anchor-linked into the rendered artifact.
- Every mutation goes through an inline **confirmation panel** that shows the exact wire text (`.confirm` > `.sendtext`) plus a fixed routing paragraph.

---

## 1. Global shell

### 1.1 Frame

```
.app            display:flex; flex-direction:column; height:100vh; overflow:hidden
header.topbar   height:52px; padding:0 16px; gap:14px; background:var(--chrome); border-bottom:1px solid var(--border); backdrop-filter:blur(8px)
div.scopebar    padding:8px 16px; gap:10px; background:var(--bg-accent); border-bottom:1px solid var(--border); font-size:12.5px
main.view       flex:1 1 auto; min-height:0; overflow:hidden; display:flex   (aria-live="polite")
.view-scroll    flex:1 1 auto; min-height:0; overflow:auto   (used by wizard + simple pages)
```

### 1.2 Topbar contents, left → right

1. `button.icon-btn.back-btn#backBtn` — `aria-label="Back to queue"`, icon `i-back`. `display:none` on desktop; `display:inline-flex` only when `body[data-pane="detail"]` at ≤900px. Also `hidden` whenever route ≠ `actions`.
2. `.brand` — `.brand-mark` (26×26, radius 7px, `background:var(--accent-subtle)`, `border:1px solid color-mix(in srgb,var(--accent) 40%,var(--border))`, `color:var(--accent)`, icon `i-logo`) + text "AI-DLC Studio" (`.brand-hide`, hidden ≤900px). Font-weight 600, `letter-spacing:-.01em`, `color:var(--text-strong)`.
3. `.variant-tag` "Option B — Evidence First" — **mockup-only label; do not ship.**
4. `nav.nav#nav[aria-label="Primary"]` — six buttons, `data-route` in this order:

| Order | Label | `data-route` | Mockup icon | Lucide |
|---|---|---|---|---|
| 1 | Action Center | `actions` | `i-inbox` | `Inbox` |
| 2 | Repos | `repos` | `i-repo` | `Book` (shape) / `FolderGit2` (host precedent) |
| 3 | Intents | `intents` | `i-intent` | `ListTodo` / `Target` |
| 4 | Workflow Map | `map` | `i-map` | `LayoutGrid` (shape) / `Workflow` (meaning) |
| 5 | Activity | `activity` | `i-activity` | `Activity` |
| 6 | Settings | `settings` | `i-settings` | `Settings` |

   Matches **PRD §8.1** order exactly. Active state: `aria-current="page"` → `background:var(--bg-elevated); border-color:var(--border); color:var(--text-strong); font-weight:600`. Idle: transparent, `color:var(--muted)`, `padding:6px 11px`, `font-size:13px`, `gap:7px`, radius `--radius-md`. Only Action Center carries `span.count` (mono 10.5px, 700, `background:var(--danger); color:var(--danger-fg)`, pill) = `visible().length`; `hidden` when 0.
5. Right cluster (`margin-left:auto; gap:6px`): `button.btn.btn-sm#newIntentBtn[data-route="wizard"]` icon `i-plus` + `span.nlbl` "New intent" (label hidden ≤430px); `button.icon-btn#themeBtn[aria-label="Toggle light or dark theme"]` showing `i-sun` when dark, `i-moon` when light. **DEVIATION:** theme is owned by the KiroCrew host (`html[data-theme]`, see §2.4); the App must not ship its own toggle. Drop `#themeBtn` and the Settings "Theme → Toggle" row, or route them to the host's theme setting.

### 1.3 Scope bar (`.scopebar`)

- `button.scope-select#scopeBtn[aria-haspopup="listbox"][aria-expanded="false"]`: icon `i-repo` (13px) + `#scopeLabel` + `#scopeCount` (`.muted.mono`, 11px) + `i-chevd`. Styles: `padding:5px 10px; radius:--radius-md; background:var(--bg-elevated); border:1px solid var(--border)`, hover `border-color:var(--border-hover)`.
  - All-repos state: label `All repos`, count `{N} registered` (e.g. "3 registered").
  - Repo state: label = repo label (`checkout-web`), count = repo path (`~/work/checkout-web`).
  - Mockup behavior: click cycles `all → r1 → r2 → r3 → all` (stub for a real listbox popover). If the selected action becomes invisible under the new scope, select the first visible item, else `null`.
- `.crumb#crumb` (`font-size` inherits 12.5px, `color:var(--muted)`, `b{color:var(--text);font-weight:600}`, `.sep{opacity:.5}`):
  - On `actions` with a selection: `› **{repo}** / [{space} /] **{intent}** / {stage}` — the space segment is emitted only when `space !== 'default'` (PRD FR-ACT-006 "space when non-default").
  - Otherwise: `› Registered repositories only — Studio never scans the filesystem`.
- `.exec-strip#execStrip` (`margin-left:auto; gap:8px`) — the execution/status strip. Four chips, static in the mockup:
  1. `<span class="chip"><span class="pulse"></span>1 turn running</span>` — `.pulse` 7px dot, `background:var(--accent)`, `box-shadow:0 0 0 3px var(--accent-subtle)`, `animation:pulse 2.4s ease-in-out infinite` (opacity 1 → .35 → 1).
  2. `chip` + `i-lock` "2 repo leases held"
  3. `chip chip-warn` + `i-clock` "1 circuit open"
  4. `chip` + `i-moon` "Night window off"
  - **DEVIATION (PRD §8.3):** the strip must be *collapsible*. The mockup has no collapse affordance. Implement a disclosure toggle (e.g. chevron `icon-btn`, `aria-expanded`) without importing Option C's `.strip[data-open]` card layout; collapsed state should still show the count of active alerts.
- ≤900px: `.scopebar{overflow-x:auto}`.

### 1.4 Page grid proportions

| Surface | Mockup | PRD §8.3 | Recommendation |
|---|---|---|---|
| Action Center queue : detail | `300px minmax(0,1fr)` (fixed 300px) | "approximately 36% / 64%" | **DEVIATION.** Both are authoritative-ish (PRD §10.0 also demands the "narrow priority queue"). Reconcile with `grid-template-columns:minmax(300px,36%) minmax(0,1fr)` and cap the queue at ~460px on ultra-wide. Flagged in Open Questions. |
| Detail reading column | `.reading{max-width:820px}` | — | keep |
| Artifact vs reviewer | `.compare{grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:14px}`; single column ≤1180px | — | keep |
| Workflow Map lane | `.lane{grid-template-columns:158px minmax(0,1fr); gap:14px}`; stage card `width:154px` | — | keep |
| Map inspector | `.inspector{width:340px}` (294px ≤1180; hidden ≤900) | FR-MAP-008/009 | keep; mobile needs an alternative (sheet/accordion) — see §7 |
| Wizard | `.wiz{max-width:940px; margin:0 auto; padding:24px 24px 40px}` | — | keep |
| Simple pages | `.page{padding:22px 28px 34px; max-width:1080px}` | — | keep |

### 1.5 Breakpoints (mockup lines 458–500)

```
@media (max-width:1180px)  .compare → one column; .inspector width 294px
@media (max-width:900px)   .ac → one column (list ⇄ detail via body[data-pane]);
                           .topbar wraps (height:auto; min-height:52px; padding:8px 12px; row-gap:8px);
                           .nav{order:10; flex-basis:100%; margin-left:0; overflow-x:auto} — nav becomes its own horizontally scrollable row ("mobile still needs to reach Repos / Intents / Map / Activity / Settings");
                           .queue{border-right:0}; body[data-pane="detail"] .queue{display:none}; body[data-pane="list"] .detail{display:none}; body[data-pane="detail"] .back-btn{display:inline-flex}
                           .detail-head{padding:14px 16px 0}; .detail-body{padding:16px 16px 26px}; .actionbar,.confirm side padding 16px; .confirm margins 16px; .dtitle{font-size:19px}
                           .inspector{display:none}; .lane → one column; .lane-h{position:static}; .stage{width:100%}
                           .page{padding:16px}; .wiz{padding:16px 16px 34px}; .brand-hide{display:none}; .scopebar{overflow-x:auto}
@media (max-width:430px)   .topbar{padding:8px 12px; gap:9px}; #newIntentBtn .nlbl{display:none}; .variant-tag smaller;
                           .dfacts{gap:6px}; .tabs{gap:14px; overflow-x:auto}; .actionbar .hint{display:none}; .actionbar .btn{flex:1 1 auto; justify-content:center}
                           .stat{grid-template-columns:repeat(2,minmax(0,1fr))}; .advisor dl → one column; .advisor dt{padding-top:6px}
```

Mobile list→detail state machine: `document.body.dataset.pane ∈ {'list','detail'}`. Clicking a `.qitem` or any `[data-open]` sets `detail`; `#backBtn`, the post-submit "Show the queue" button (`data-act="undo"`), and any non-actions route set `list`. **PRD §8.4** requires the complete decision loop at ≥390px; repo registration / install / upgrade / batch admin are desktop-only.

---

## 2. Design tokens

### 2.1 Token table — mockup values (light / dark) vs. real KiroCrew host values

Mockup `:root` block (lines 14–18) and theme blocks (lines 19–58). Host = `website/src/index.css` lines 228–230 (fonts/radii), 294–317 (`:root,[data-theme="dark"],[data-theme="amber-dark"]`), 320–343 (`[data-theme="light"],[data-theme="amber-light"]`).

Legend: ✅ identical to host; ⚠️ differs from host (host wins — see note); ➕ not defined by host (App must define locally).

| Token | Mockup dark | Mockup light | Host dark | Host light | |
|---|---|---|---|---|---|
| `--font-body` | `'Space Grotesk',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif` | same | `var(--theme-font-sans, var(--script-fallbacks),'Space Grotesk',-apple-system,BlinkMacSystemFont,sans-serif)` | same | ⚠️ inherit host (adds theme-pack font + CJK `unicode-range` fallbacks for zh-CN) |
| `--mono` | `'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace` | same | `var(--theme-font-mono, var(--script-fallbacks-mono),'JetBrains Mono',ui-monospace,SFMono-Regular,monospace)` | same | ⚠️ inherit host |
| `--radius-sm/md/lg/xl` | `6px/8px/12px/16px` | same | same | same | ✅ |
| `--bg` | `#12141a` | `#fafafa` | same | same | ✅ |
| `--bg-accent` | `#14161d` | `#f5f5f5` | same | same | ✅ |
| `--bg-elevated` | `#1a1d25` | `#ffffff` | same | same | ✅ |
| `--bg-hover` | `#262a35` | `#f0f0f0` | same | same | ✅ |
| `--card` | `#181b22` | `#ffffff` | same | same | ✅ |
| `--card-fg` | `#f4f4f5` | `#18181b` | same | same | ✅ |
| `--card-hl` | `rgba(255,255,255,.05)` | `rgba(0,0,0,.03)` | same | same | ✅ (unused in mockup) |
| `--panel` | `#12141a` | `#fafafa` | same | same | ✅ |
| `--panel-strong` | `#1a1d25` | `#ffffff` | `#1a1d25` | `#f5f5f5` | ⚠️ light differs (inspector bg) |
| `--chrome` | `rgba(18,20,26,.95)` | `rgba(255,255,255,.95)` | `rgba(18,20,26,.95)` | `rgba(250,250,250,.95)` | ⚠️ light differs (negligible) |
| `--text` | `#e4e4e7` | `#3f3f46` | same | same | ✅ |
| `--text-strong` | `#fafafa` | `#18181b` | same | same | ✅ |
| `--muted` | `#7f7f88` | `#71717a` | same | same | ✅ |
| `--muted-strong` | `#a1a1aa` | `#52525b` | **`#52525b`** | `#52525b` | ⚠️ **CRITICAL** — see §2.2 |
| `--muted-fg` | — | — | `#fff` | `#fff` | host-only |
| `--border` | `#27272a` | `#e4e4e7` | same | same | ✅ |
| `--border-strong` | `#3f3f46` | `#d4d4d8` | same | same | ✅ |
| `--border-hover` | `#52525b` | `#a1a1aa` | same | same | ✅ |
| `--accent` | `#00d492` | `#047558` | same | same | ✅ (but themable — 30+ host themes change it) |
| `--accent-fg` | `#000` | `#fff` | same | same | ✅ |
| `--accent-hover` | `#34d399` | `#059669` | same | same | ✅ |
| `--accent-subtle` | `rgba(4,117,88,.2)` | `rgba(4,117,88,.12)` | same | same | ✅ |
| `--accent-glow` | — | — | `rgba(4,117,88,.35)` | `rgba(4,117,88,.18)` | host-only |
| `--ring` | `#10b981` | `#047558` | same | same | ✅ |
| `--ok` / `--ok-fg` / `--ok-subtle` | `#22c55e` / `#000` / `rgba(34,197,94,.12)` | `#16a34a` / `#fff` / `rgba(22,163,74,.1)` | same / `#000` / same | same / **`#000`** / same | ⚠️ light `--ok-fg` differs (check glyph on green fill) |
| `--warn` / `--warn-fg` / `--warn-subtle` | `#eab308` / `#000` / `rgba(234,179,8,.12)` | `#a16207` / `#fff` / `rgba(161,98,7,.1)` | same | same | ✅ |
| `--danger` / `--danger-fg` / `--danger-subtle` | `#ef4444` / `#000` / `rgba(239,68,68,.12)` | `#dc2626` / `#fff` / `rgba(220,38,38,.1)` | same | same | ✅ |
| `--info` / `--info-fg` | `#38bdf8` / `#000` | `#0e7490` / `#fff` | **`#0891b2`** / `#000` | **`#0891b2`** / `#000` | ⚠️ see §2.2 (AA risk in light) |
| `--info-subtle` | `rgba(56,189,248,.12)` | `rgba(14,116,144,.1)` | — | — | ➕ App must define |
| `--aim` / `--aim-subtle` | `#a78bfa` / `rgba(167,139,250,.15)` | `#7c3aed` / `rgba(124,58,237,.12)` | same (+`--aim-fg:#000`) | same (+`--aim-fg:#fff`) | ✅ |
| `--clarify` / `--clarify-subtle` | — | — | `#eab308` / `rgba(234,179,8,.08)` | `#a16207` / `rgba(161,98,7,.06)` | host-only |
| `--diff-add` / `--diff-add-text` | `rgba(46,160,67,.15)` / `#7ee787` | `rgba(22,163,74,.12)` / `#15803d` | same / same | same / **`#1a7f37`** | ⚠️ light text differs |
| `--diff-del` / `--diff-del-text` | `rgba(248,81,73,.15)` / `#ffa198` | `rgba(220,38,38,.1)` / `#b91c1c` | same / same | **`rgba(220,38,38,.12)`** / **`#cf222e`** | ⚠️ light differs |
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,.2)` | `0 1px 2px rgba(0,0,0,.06)` | same | same | ✅ |
| `--shadow-md` | `0 4px 12px rgba(0,0,0,.25),0 0 0 1px rgba(255,255,255,.03)` | `0 4px 12px rgba(0,0,0,.08),0 0 0 1px rgba(0,0,0,.04)` | same | same | ✅ |
| `--shadow-lg` | `0 12px 28px rgba(0,0,0,.35)` | `0 12px 28px rgba(0,0,0,.12)` | same | same | ✅ |
| `--ph-init` | `#94a3b8` | `#64748b` | — | — | ➕ |
| `--ph-idea` | `#a78bfa` (= `--aim`) | `#7c3aed` (= `--aim`) | — | — | ➕ |
| `--ph-incep` | `#38bdf8` (= mockup `--info`) | `#0e7490` (= mockup `--info`) | — | — | ➕ |
| `--ph-constr` | `#00d492` (= `--accent`) | `#047558` (= `--accent`) | — | — | ➕ |
| `--ph-oper` | `#eab308` (= `--warn`) | `#a16207` (= `--warn`) | — | — | ➕ |

### 2.2 Token decisions the implementer must make (code wins over mockup)

1. **Inherit, never redefine, host tokens.** The App renders inside the host DOM (`AppHost` does `React.lazy(() => import('/apps/<slug>/ui/index.mjs'))`; `app-sdk/index.ts::useTheme` reads `document.documentElement.dataset.theme` and `getComputedStyle(root).getPropertyValue('--accent')`). The host defines **30+ themes** keyed by `html[data-theme="…"]` (`dark`, `light`, `monokai-dark`, `solarized-light`, `amber-dark`, `nord-*`, `dracula-*`, `rosepine-*`, `catppuccin-*`, `tokyonight-*`, `gruvbox-*`, `ice-*`, `amoled-*`, `kiro-*`, `intellij-*`, `highcontrast-*`, `everforest-*`, …). The mockup's `html[data-theme="dark"|"light"]` blocks must **not** be shipped; only the ➕ tokens are App-owned, scoped to the App root element.
2. **`--muted-strong` is inverted in the host dark theme.** Mockup dark uses `--muted-strong:#a1a1aa` as a *brighter-than-muted* emphasis (chip text, `.qmeta .wait`, `.finding .fq`, `.stage .rel`, `.kbd`, done-stage titles, `.dstep`). Host dark `--muted-strong:#52525b` is *darker* than `--muted:#7f7f88`. Using the host token verbatim would make every chip label fail contrast on dark. Define an App-local alias, e.g. `--aidlc-emph: color-mix(in srgb, var(--text) 75%, var(--muted))`, and use it wherever the mockup uses `--muted-strong`.
3. **`--info` in host light is `#0891b2`** (≈3.7:1 on `#ffffff`) — fails WCAG AA for 11.5px chip text (PRD §10.3). The mockup deliberately used `#0e7490` (≈5.4:1). Define `--aidlc-info-text: color-mix(in srgb, var(--info) 78%, var(--text-strong))` for text; use raw `--info` for icons/borders/fills only. Also define `--info-subtle: color-mix(in srgb, var(--info) 12%, transparent)` because the host has none.
4. **Phase accents must survive theming.** Alias to host tokens so Monokai/Solarized/etc. remain coherent: `--ph-idea: var(--aim)`, `--ph-incep: var(--info)`, `--ph-constr: var(--accent)`, `--ph-oper: var(--warn)`, `--ph-init: color-mix(in srgb, var(--muted) 70%, var(--text))` (mockup slate `#94a3b8`/`#64748b` has no host equivalent). Phase accents are used only as: 3px lane bar, 2px stage-card top border, 3px `.mrow` left border in the wizard matrix, inspector phase chip border/text. **PRD §10.1:** "restrained".
5. **`useTheme().mode`** is typed `'dark'|'light'` but at runtime equals the raw `data-theme` value (e.g. `'monokai-dark'`). Never branch on it for colors; rely on tokens. Only `.endsWith('-light') || === 'light'` is a safe polarity check if one is ever needed (e.g. choosing a diff renderer theme).

### 2.3 Typography (mockup, px)

Base: `body{font-size:14px; line-height:1.55; -webkit-font-smoothing:antialiased}` — apply to the App root, not `body`.

| Role | Size / weight / extras |
|---|---|
| Detail title `.dtitle` | 23px / 600 / `line-height:1.28; letter-spacing:-.015em; max-width:62ch` (19px ≤900) |
| Page `h1` (`.page h1`, wizard h1) | 20px / 600 / `letter-spacing:-.01em` |
| Inspector `h2` | 16px / `color:var(--text-strong)` |
| Brief paragraph `.brief p`, question prompt `.q-prompt` | 15px / 400 (`line-height:1.68`) ; 15px / 600 (`line-height:1.5`) |
| Empty-state title `.empty .et` | 14px / 600 |
| Body, `.crit .txt`, `.opt .ol`, `.choice .cl`, `.advisor .t`, `.pane-body h4` | 13.5px |
| Buttons, nav, tabs, table cells, `.pane-body`, lede, `.consequence`, textarea | 13px |
| Search input, scope bar, `.ev .val`, `.sendtext`, `.opt .od`, `.choice .cd`, `.finding .fq`, `.stage .st`, `.mrow .mh` | 12.5px |
| `.pane-head`, `.dstep`, `.finding .fh`, `.crit .why`, `.q-sub`, `.pane-body table`, `.tbl td` (13) / `.tl` body | 12px |
| Chips, `.dbreadcrumb`, `.anchor-link`, `.hint`, `.route`, `.ev .sub`, `.diffline`, `code`, `.help`, `.unit`, `.tl .t`, `.seg button`, `.disclaim`, `.advisor dt` | 11.5px |
| Block headers `.block>h3` | 11px / 600 / `letter-spacing:.09em; text-transform:uppercase; color:var(--muted)` with trailing hairline (`::after{flex:1;height:1px;background:var(--border)}`) |
| `.qtop`, `.qmeta`, `.kbd`, `.tc`, `.tl .src`, `.stat .q`, `.pane-body th` (uppercase .04em) | 11px |
| Group headers `.qgroup`, `.ev .src`, `.stat .k`, `.tbl th`, `.pane-head .nm path row`, `.stage .sn/.sm` | 10.5px / uppercase / `letter-spacing:.08–.09em` |
| `.nav .count`, `.tabs .tc` | 10.5px mono |
| Stage `.rel` tag | 9.5px mono |
| Stat value `.stat .v` | 19px mono (14–16px for text values) |

Mono (`.mono`, `font-variant-ligatures:none`) is used for: ids (`a-002`, `act_7712`, `ses_…`), paths, hashes, stage numbers, timestamps, waiting durations, counts, wire text, log excerpts, table numerics.

### 2.4 Spacing, radii, borders, shadows, motion, focus

- Spacing values actually used: 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 18, 20, 22, 24, 28, 32 px. Canonical rhythm: section gap 22px (`.block{margin-bottom:22px}`), card padding 11–13px (compact) / 16–20px (brief, question), page gutters 32px desktop → 16px mobile.
- Radii: `--radius-sm` 6 (focus ring), `--radius-md` 8 (buttons, inputs, chips-square, cards-compact, stage cards), `--radius-lg` 12 (`.card`, `.brief`, `.pane`, `.q`, `.advisor`, `.confirm`, `.delivery`, `.tbl`), `--radius-xl` 16 (unused), `999px` pills (chips, counts, `.ms`, `.rel`), 7px brand mark, 5px seg buttons/`.kbd`, 3–4px inline code/diff lines/highlight.
- Borders: 1px `var(--border)` everywhere; `var(--border-strong)` for selected queue item, `.kbd`, `.qrule` default, `.finding` default left rule, `.units` dashed rail, step/delivery circles (1.5px). Emphasis rules: 3px left (`.brief` accent, `.finding` level color, `.mrow` phase), 2px top (`.stage` phase), 2px rule (`.qrule`), 2px tab underline (`.tabs button[aria-selected]::after`, accent). Selection rings: `box-shadow:0 0 0 1px var(--accent)` (`.choice.sel`, `.stage[aria-pressed]`).
- Shadows: `--shadow-sm` on selected queue item, `.brief`, hovered stage, pressed seg button; `--shadow-md` on `.confirm`; `--shadow-lg` unused.
- Motion: `.btn` `transition:background .12s,border-color .12s`; `.hl` `transition:background .3s`; `.pulse` 2.4s loop; `scrollIntoView({behavior:'smooth'})`. `@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}` (PRD §10.1 "Honor reduced motion").
- Focus: `:focus-visible{outline:2px solid var(--ring); outline-offset:2px; border-radius:var(--radius-sm)}`; textarea `.free:focus{outline:2px solid var(--ring); outline-offset:1px}` (PRD §10.3).
- Utility: `.sr` visually-hidden; `[hidden]{display:none!important}`; `.trunc`; `.wrap-any{overflow-wrap:anywhere}`; `.row/.col/.spread/.grow`.

### 2.5 Primitives (port 1:1; map to `@kirocrew/ui` where noted)

| Mockup class | Spec | `@kirocrew/ui` equivalent (HOST `kirocrew-ui/index.ts`) |
|---|---|---|
| `.btn` | `padding:7px 13px; min-height:34px; radius md; bg --bg-elevated; border --border; font 13px; gap 7px`; hover `bg --bg-hover; border --border-hover`; disabled `opacity:.45` | `Btn` (props `primary?`, `danger?`) |
| `.btn-primary` | `bg/border --accent; color --accent-fg; weight 600`; hover `--accent-hover` | `Btn primary` |
| `.btn-danger` | `color --danger; border color-mix(danger 40%, border)`; hover `bg --danger-subtle` | `Btn danger` |
| `.btn-ghost` | transparent, `color --muted` | — |
| `.btn-sm` | `padding:4px 9px; min-height:28px; font 12px` | — |
| `.icon-btn` | 32×32, transparent, `color --muted`, radius md | — |
| `.chip` | inline-flex, `gap:5px; padding:2px 8px; radius 999; font 11.5px; line-height 1.7; border --border; bg --bg-elevated; color (emph); white-space:nowrap`; `svg{width:11px;height:11px;stroke-width:2}` | `Badge variant 'ok'|'err'|'warn'|'aim'|'muted'` — **no `info`/`accent` variant**; build a local `Chip` |
| `.chip-danger/-warn/-ok/-info/-accent/-aim` | `color:var(--X); border-color:color-mix(in srgb,var(--X) 35–40%,var(--border)); background:var(--X-subtle)` | — |
| `.card` | `bg --card; color --card-fg; border --border; radius lg` | `Card`, `CardTitle` |
| `.kbd` | mono 11px, `padding:1px 5px; border 1px --border-strong; border-bottom-width:2px; radius 5px; bg --bg-elevated` | — |
| `.seg` | `display:flex; gap:2px; padding:2px; bg --bg-elevated; border --border; radius md`; buttons `padding:4px 6px; radius 5px; font 11.5px; color --muted`; pressed `bg --card; color --text-strong; weight 600; shadow-sm` (`aria-pressed`) | `SegmentedControl<{segments, value, onChange, layoutId?, collapse?}>` |
| `.search` | `padding:5px 9px; border --border; radius md; bg --bg-elevated`; input transparent 12.5px | `SearchInput` / `Input` |
| `.empty` | `padding:34px; text-align:center; color --muted`; `.et` 14px/600 | `EmptyState` |
| `.stat .s` | KPI card: `.k` 10.5 uppercase, `.v` 19px mono, `.q` 11px qualifier | `StatCard` |
| `.tbl` | full-width table in a card: `th` 10.5 uppercase on `--bg-accent`, `td padding:11px 13px` | — |
| `.tl` | timeline `grid-template-columns:78px 20px minmax(0,1fr); gap:11px; padding:9px 0; border-bottom` | — |

Icons: mockup `.ico{width:15px;height:15px;stroke-width:1.6;stroke-linecap/linejoin:round}`, `.ico-sm` 13px, `.ico-lg` 18px, chips 11px @ stroke 2. In React: `<Icon size={15} strokeWidth={1.6} aria-hidden />`, `size={13}` for `ico-sm`, `size={18}` for `ico-lg`, `size={11} strokeWidth={2}` inside chips. **PRD §10.1:** `lucide-react`, no emoji. `lucide-react` is provided through the host import map (`shared-modules.ts` registers `react`, `react-dom`, `react/jsx-runtime`, `lucide-react`, `@tanstack/react-query`, `@kirocrew/app-sdk`, `@kirocrew/ui`).

---

## 3. Vocabularies: severity, type, state, level — color + icon + label + shape

### 3.1 Action type (fixture field `type` → `typeLabel`, `icon`, default `sev`)

| `type` | `typeLabel` | Icon (mockup → lucide) | Default `sev` | Group (`group`) | Primary label (`primary`) |
|---|---|---|---|---|---|
| `recovery` | Recovery | `i-recovery` → `TriangleAlert` | `critical` | Recovery required and delivery uncertain | Reconcile evidence |
| `gate` | Gate | `i-gate` → `Lock` (or `LockKeyhole`/`ShieldCheck` to disambiguate from `i-lock`) | `blocking` | Blocking Gates and questions | Review and decide |
| `questions` | Questions | `i-question` → `CircleQuestionMark` | `blocking` | Blocking Gates and questions | Answer 3 questions |
| `failure` | Circuit open | `i-fail` → `CircleX` | `attention` | Circuit breakers, failures, and install conflicts | Retry now or diagnose |
| `install` | Install conflict | `i-install` → `Download` | `attention` | Circuit breakers, failures, and install conflicts | Review the drift |
| `budget` | Budget stop | `i-clock` → `Clock` | `info` | Budget stops and pauses | Raise the cap or run manually |

Group order = **PRD FR-ACT-003** exactly. Missing-input template (PRD §9.5) is **not mocked**; reuse the budget layout with `typeLabel` "Missing input" and `i-question`/`FileQuestionMark`.

### 3.2 Severity (`data-sev`) — `SEV_ORDER = {critical:0, blocking:1, attention:2, info:3}`

| `sev` | Queue rule `.qrule` color | Detail chip (`sevChip`) | Extra |
|---|---|---|---|
| `critical` | `var(--danger)` | `chip-danger` + `i-recovery` + **Recovery required** | `.qmeta .wait{color:var(--danger)}` |
| `blocking` | `var(--accent)` | `chip-accent` + `i-gate` + **Blocking** | |
| `attention` | `var(--warn)` | `chip-warn` + `i-warn` + **Needs attention** | |
| `info` | `var(--muted)` | `chip` + `i-info` + **Informational** | |

Shape rule: severity in the queue = 2px vertical rule (`.qrule{width:2px;border-radius:2px;align-self:stretch}`) **plus** the type icon and label in `.qtop`. No row fills. Default rule color `var(--border-strong)`.

### 3.3 Delivery state (`delivery`) and delivery strip

Steps (fixed order): `Queued` → `Delivered` → `Agent processing` → `State changed`.
Current-step index: `{delivered:1, uncertain:1, failed:1, blocked:0, held:0}`; after a mock send `S.sent[id]=3`; after `Retry now` `S.sent[id]=2`.
`.dstep .b` 16px circle: done → `background:var(--ok); border-color:var(--ok); color:var(--ok-fg)` + `i-check`; now → `border-color:var(--accent); background:var(--accent-subtle); color:var(--accent)` + `i-clock`; future → empty circle `border:1.5px solid var(--border-strong)`. `.dsep` hairline `flex:1; min-width:16px; margin:0 9px`.
Trailing chips: `uncertain && !sent` → `chip-danger` + `i-warn` **"Delivery uncertain — not replayed automatically"**; `sent` → `chip-ok` + `i-check` **"Studio is watching disk state, not the HTTP receipt"**.

### 3.4 Reviewer finding level (`lvl`)

| `lvl` | Badge | Left rule | Review-tab group header |
|---|---|---|---|
| `blocker` | `chip-danger` + `i-warn` + **Blocker** | `var(--danger)` | "Open blockers — {n}" (icon `i-warn`) |
| `advisory` | `chip-warn` + `i-info` + **Advisory** | `var(--warn)` | "Advisory — {n}" (icon `i-info`) |
| `resolved` | `chip-ok` + `i-check` + **Answered** | `var(--ok)` | "Answered in this revision — {n}" (icon `i-check`) |

### 3.5 Acceptance criterion (`met`)

met → `i-check` with `.met{color:var(--ok)}`; unmet → `i-warn` with `.unmet{color:var(--warn)}`. Header: **"Stage acceptance criteria — {met} of {total} met"**.

### 3.6 Workflow Map stage state (`data-state`)

| state | Card style | Badge |
|---|---|---|
| `done` | `.st{color:emph; font-weight:500}` | `chip-ok` + `i-check` + **done** |
| `current` | `background:var(--accent-subtle); border-color:var(--accent)` | `chip-accent` + `i-play` + **running** |
| `gate` | `border-color:var(--warn); background:var(--warn-subtle)` | `chip-warn` + `i-gate` + **Gate open** |
| `skipped` | `opacity:.62; border-style:dashed; background:transparent` | `chip` + `i-warn` + **skipped** |
| `pending` | default | `chip` + `i-clock` + **pending** |

Relationship overlay tag `.rel` (top-right pill, 9.5px mono): `upstream` → `.up .rel{border/color:var(--info); bg:var(--info-subtle)}`; `downstream` → `.down .rel{var(--aim)…}`.

### 3.7 Intent operational state (Intents page)

`Waiting for you` → `chip-accent`; `Running` → `chip-ok`; `Queued`, `Parked` → `chip`; `Circuit open` → `chip-warn`. Keep moving column: Running → `chip-ok` + `i-check` + "on"; Circuit open → `chip-warn` "disabled by breaker"; else muted text "off".

### 3.8 Repo install state (Repos page)

`ok` → `chip-ok` + `i-check` + "installed"; `drift` → `chip-warn` + `i-warn` + "drift · upgrade stopped".

---

## 4. Action queue (`aside.queue[aria-label="Action queue"]`)

### 4.1 Structure

```
.queue            flex column; bg --panel; border-right 1px --border
  .queue-head     padding:12px 14px 10px; border-bottom; gap:9px
    .queue-title  h2 "Needs you" (12px uppercase .07em muted) | span.n "{visible} of {total}" (mono 12px)
    label.search  i-search + input#qSearch[type=search][placeholder="Filter by repo, intent, stage"][aria-label="Filter the queue"]
    .seg[role=group][aria-label="Queue organization"]  buttons data-order = priority | repo | type | oldest  (labels "Priority","Repo","Type","Oldest"; aria-pressed)
  .queue-list     flex:1; overflow:auto; padding:6px
    .qgroup       group header (10.5px uppercase .09em muted, trailing hairline) — text depends on order mode
    button.qitem[data-id][data-sev][aria-current="true|false"]
```

### 4.2 Row anatomy (`.qitem`)

`display:grid; grid-template-columns:12px minmax(0,1fr); gap:9px; padding:9px 10px; margin-bottom:2px; radius md; border:1px solid transparent`. Hover `bg --bg-hover`. Selected (`aria-current="true"`): `bg --card; border-color --border-strong; shadow-sm`.

1. `span.qrule` (col 1) — severity rule, `aria-hidden`.
2. `span.grow` (col 2):
   - `.qtop` (11px muted, `gap:6px; margin-bottom:2px`): `span.type` (`color:var(--text); font-weight:600`) = type icon (13px) + `typeLabel`; `span.dot` "·" (`opacity:.45`); `span.trunc` repo label.
   - `.qtitle` (13px / 500 / `color:var(--text-strong)`; `-webkit-line-clamp:2`).
   - `.qmeta` (11px mono muted, `gap:6px`): `span.wait` waiting duration (emph color; danger if critical) · `span.trunc` stage (e.g. `2.3 functional-design`).

**PRD FR-ACT-006 gap:** the row omits *space when non-default*, *intent*, and *primary next action*. Space appears only in the scope-bar crumb; intent only in the detail breadcrumb. Add intent (and space when ≠ default) to `.qmeta`, and expose the primary label to screen readers (PRD §10.3: SR label must include type, repo, intent, stage, state) — e.g. `aria-label="{typeLabel}, {repo}[, {space}], {intent}, {stage}, {sevLabel}, waiting {wait}. {primary}"`.

### 4.3 Ordering / grouping (`queueHTML`)

| `S.order` | Sort | Group header text |
|---|---|---|
| `priority` (default) | `SEV_ORDER[a.sev]` ascending | `a.group` (four PRD groups) |
| `repo` | `a.repo.localeCompare(b.repo) || SEV_ORDER` | `a.repo` |
| `type` | **none** (fixture order) | `a.group` |
| `oldest` | `parseWait(b.wait) - parseWait(a.wait)` (descending wait) | literal `Oldest first` |

`parseWait(w)`: `/(?:(\d+)h)?\s*(?:(\d+)m)?/` → minutes. Fixture waits: `41m`, `2h 14m`, `38m`, `1h 06m`, `3h 22m`, `6h 41m`.

**DEVIATIONS vs PRD:** (a) FR-ACT-004 equal-priority → oldest first is not implemented as a secondary key; add `|| wait desc` to every mode. (b) `type` mode neither sorts nor groups by type; implement group = `typeLabel`, sort by type then severity then wait. (c) FR-ACT-005 "choice persists locally" — mockup state is in-memory only.

### 4.4 Search

`visible()` = scope filter (`a.repo === selectedRepo.label` unless `all`) AND `(title+repo+intent+stage+typeLabel).toLowerCase().includes(query)`. On input: recompute; if selection is no longer visible, select first visible (or `null`); re-render and restore caret to end. Nav count badge reflects `visible().length`.

### 4.5 Empty queue

```
.empty  i-check (18px)
        .et  "Nothing needs your judgment"
        12.5px "Two intents are running unattended. The next stop will be a Gate in <span class=mono>checkout-web</span>."
        button.btn.btn-sm[data-route=map]  i-map "Watch the Workflow Map"
```
(PRD §10.2: empty states explain the next useful action.) The sentence is a template: `{n} intents are running unattended. The next stop will be a Gate in {repo}.`

---

## 5. Detail pane (`section.detail`)

### 5.1 Header (`.detail-head`, `padding:20px 32px 0`)

1. `.dbreadcrumb` (11.5px mono muted, wrap): `{typeIcon} {typeLabel} / {repo} / {intent} / {stage}` + `button.anchor-link[data-copy=deeplink][title="Copy deep link"]` `i-link` "deep link" → on click the button becomes `i-check` "copied" (mockup writes nothing to clipboard). **PRD §10.2:** deep links restore scope, selected action, tab, and evidence anchor — the URL scheme is not in the mockup (Open Question).
2. `h1.dtitle` = `title`.
3. `.dfacts` chips (`gap:8px; wrap; margin-bottom:14px`), in order: `sevChip(sev)`; `chip` `i-clock` "waiting {wait}"; if `reviewClass`: `chip-info` `i-review` "{reviewClass} review"; if `agent`: `chip` `i-intent` "agent {agent}"; if `revision`: `chip` "revision {n}"; `chip mono` 11px `{id}`.
4. `.tabs[role=tablist]` — `gap:20px; border-bottom`; buttons `padding:9px 1px 11px; font 13px; gap 7px; color --muted`; selected `color --text-strong; weight 600` + 2px accent underline `::after{bottom:-1px}`; count pill `.tc` (mono 10.5, `--bg-elevated`, border).

   `TABS = [{id:'decision',label:'Decision',icon:'i-inbox'},{id:'artifacts',label:'Artifacts',icon:'i-doc'},{id:'review',label:'Review',icon:'i-review'},{id:'activity',label:'Activity',icon:'i-activity'}]` (PRD §8.3 order). Visibility: `artifacts` iff `artifact || drift || evidence`; `review` iff `findings`; `decision`/`activity` always. Counts: `artifacts` = 1 if `artifact` else `drift.length`; `review` = `findings.length`; `activity` = 4 (hard-coded sample). Selecting a queue item resets `tab='decision'` and `confirm=null`.

5. `.detail-body` (`flex:1; overflow:auto; padding:22px 32px 30px`) > `.reading{max-width:820px}` — scrolls independently of the queue (PRD §10.2); bottom padding keeps the last block clear of the action bar.
6. Optional `.confirm` panel (§5.9) then `.actionbar` (§5.8), both `flex:0 0 auto` outside the scroller.

No selection: `.empty` with `i-inbox` (18px), `.et` "Select an item", "Pick anything in the queue to see its evidence."

### 5.2 Section block primitive (`B(title, icon, inner)`)

```html
<section class="block"><h3>{icon 13px}{title}</h3>{inner}</section>
```
`.block{margin-bottom:22px}`; `h3` = 11px uppercase muted with trailing hairline.

Shared inner primitives:
- `.brief` — `padding:18px 20px; radius lg; bg --card; border; border-left:3px solid var(--accent); shadow-sm`; `p` 15px/1.68 `--card-fg`. Variant: `style="border-left-color:var(--danger)"` for contradiction / normalized error.
- `.consequence` — callout `padding:11px 13px; radius md; bg --info-subtle; border color-mix(info 30%, border); font 13px; gap 9px`, icon `--info` (`i-info`, or `i-warn`/`i-lock` as noted). Used for "Next consequence", notes, footers.
- `.crit` list — items `padding:9px 11px; radius md; bg --card; border`, `gap:10px`, icon 13px at `margin-top:3px`; `.txt` 13.5px; `.why` block 12px muted.
- `.ev` evidence card — `bg --card; border; radius md; padding:11px 13px`; `.src` 10.5px uppercase muted with icon; `.val` mono 12.5 `--text-strong`; `.sub` 11.5 muted. `data-conflict="true"` → `border-color:color-mix(danger 45%, border); bg --danger-subtle`. `.evgrid{grid-template-columns:repeat(auto-fit,minmax(228px,1fr)); gap:10px}`.
- `.pane` / `.pane-head` / `.pane-body` — bordered card with 12px header on `--bg-accent`; body `padding:13px 15px; font 13px; max-height:340px; overflow:auto`.

### 5.3 Decision tab — common prefix (all templates)

1. `deliveryHTML(a)` strip (§3.3).
2. `B('Decision brief','i-inbox', .brief > p{lede} + .consequence[i-info] "<b>Next consequence.</b> {consequence}")`.
3. If `criteria`: `B('Stage acceptance criteria — {m} of {n} met','i-check', ul.crit…)` (each li: icon + `.txt` + `.why`).

### 5.4 Gate template (type `gate`) — Decision tab, sections in order

1. Delivery strip
2. **Decision brief** (+ Next consequence)
3. **Stage acceptance criteria — 3 of 4 met**
4. **Artifact and reviewer, side by side** (`i-review`) → `compareHTML(a)`:
   - Left `.pane`: head `i-doc` + `.nm{artifact.name}` (mono 600) + `chip` `{artifact.rev}` (right). Body `#artBody`: TOC chips (10.5px, one per `toc[]`), then `artifact.body` (rendered HTML with `h4/h5/p/table/ul/code`; anchors `id="anchor-decline"`, `id="anchor-timeout"`), then `h5` "Change since revision 1" + `.diffline.add|.del` lines (mono 11.5, `white-space:pre-wrap`). Footer head (`border-top`, no bottom): path (10.5px mono muted, `wrap-any`) + `chip` "read-only".
   - Right `.pane`: head `i-review` + `.nm` "reviewer findings" + `chip-danger` "{openBlockers} open" (sample "1 open"). Body: `findingHTML` per finding. Footer: "Quoted verbatim from the stage review block".
   - `findingHTML(f)`: `.finding[data-lvl]` → `.fh`: level badge + `{by} · {cls}` (11px mono muted) + optional `button.anchor-link[data-anchor]` `i-link` "in artifact" (right-aligned); title 13px/500 `--text-strong`; `p.fq` quote (italic, 2px left rule); `.fix` 12px muted.
5. **Unresolved risks** (`i-warn`) — `ul.crit` with `i-warn.unmet` per `risks[]`. (PRD lists "Unresolved risks and prior revisions" — prior revisions are surfaced via the diff and `revision` chips, not a separate list.)
6. **AI Advisor** (§5.10) — not-run state by default.
7. **Feedback for Request changes** (`i-doc`) — `textarea.free#fb[placeholder="Nonblank feedback is required before a change request can be sent."][aria-label="Change request feedback"]` + `.route` `i-info` "{rejectHint} Studio sends this to the canonical session; it never edits the artifact." where `rejectHint` = "Request changes requires nonblank feedback (FR-GATE-002)." **Stale reference:** current PRD numbers this FR-GATE-003; do not hard-code FR ids in UI copy.

Action bar (§5.8): hint "Approve opens an inline confirmation showing the exact text sent." · `btn` `i-advisor` "Ask the Advisor" · `btn-danger` "Request changes" · `btn-primary` `i-check` "Approve".

### 5.5 Questions template (type `questions`)

Sections: Delivery strip → Decision brief → **Question group — answered together** (`i-question`) → AI Advisor.

`questionsHTML`: per question `div.q` (`padding:16px 18px; radius lg; card`):
- `p.q-prompt` "{i}. {prompt}" (15px/600).
- `p.q-sub` chips: `{header}` (e.g. Durability / Resilience / Performance) · `select any` | `select one` · `required` (`chip-accent`) | `optional`.
- Options `label.opt` (`gap:11px; padding:10px 12px; border; radius md; bg --bg-elevated`; `.sel{border-color:var(--accent); bg --accent-subtle}`): `input[type=checkbox|radio][name={actionId}/{qId}][value={optId}][data-qkey][data-multi]` (15px, `accent-color:var(--accent)`) + `.ol` label (13.5/500) + `.od` description (12.5 muted) + optional right-aligned `chip-aim` `i-advisor` **"drafted"** when Advisor picks match.
- `Other` (if `q.other`): `label.opt` with value `__other`, `.ol` "Other", `.od` **"Free text, preserved exactly as the conductor offered it."**; when selected, `textarea.free[data-other={key}][placeholder="Your answer"]` stored at `S.answers[key+'/other']`.
- Footer `.consequence` with `i-lock`: **"Studio never edits `nfr-requirements-questions.md` or its digest. The group stays visible until delivery and state evidence prove it was accepted."**

Answer store: `S.answers['{actionId}/{qId}'] = optId | optId[] | '__other'`. `answered(a,q)` = multi ? non-empty array : truthy. Action bar: hint "{n} required answer(s) still missing — all three send as one action." or "All required answers present. One atomic submit." · `btn` `i-advisor` "Draft all" · `btn-primary` `i-send` "Send answers" (`disabled` while required unanswered).

**PRD §9.5 gap:** per-question `Explain` and `Draft this` Advisor actions are not mocked (only group-level "Draft all"). Add per-question ghost buttons in `.q-sub` right slot.

### 5.6 Recovery template (type `recovery`)

Sections after brief: **Last stable boundary** (`i-clock`; single `.ev`: `.src` `i-check` "Boundary", `.val` "{boundary.stage} · {boundary.marker}" e.g. `3.4 code-generation · [?] awaiting human`, `.sub` "Recorded at {at}. Context can be reconstructed from disk at this point.") → **Evidence from every source** (`i-doc`; `.evgrid` of `.ev[data-conflict]`, `.src` "{src}[ · conflict]") → **The contradiction** (`i-warn`; `.brief` with danger rule, text `contradiction`) → **Recovery choices** (`i-recovery`; `label.choice` radios `name="rec"`, `.cl` label + optional `chip-ok` **"Advisor recommends"** when `rec:true`, `.cd` desc; `.sel{border-color:var(--accent); box-shadow:0 0 0 1px var(--accent)}`; default `c1`) → AI Advisor.

Fixture evidence sources (the six PRD evidence classes): `AI-DLC state` (i-doc), `AI-DLC audit` (i-doc), `KiroCrew session` (i-activity, conflict), `Studio action` (i-send, conflict), `Turn marker` (i-clock), `Git observation` (i-git).

Action bar: hint "No transition happens until you choose. Studio never picks a convenient source of truth." · `btn` `i-advisor` "Diagnose with AI" · `btn-primary` `i-check` "Apply the selected recovery".

### 5.7 Failure / circuit-breaker, Install conflict, Budget-stop templates

**Failure (`failure`)**: **Normalized error** (`i-fail`; danger `.brief` with `errorSummary` + mono line "fingerprint {fingerprint}") → **Retry and backoff history** (`i-clock`; `.tbl` columns `# | At | Backoff | Outcome`) → **Session log excerpt** (`i-activity`; `.pane>.pane-body>pre.mono` 11.5px) + `.consequence` note → AI Advisor. Action bar: hint "Retry now resets the fingerprint count for this intent." · `btn` `i-advisor` "Diagnose with AI" · `btn` `i-pause` "Keep paused" · `btn-primary` `i-play` "Retry now".

**Install conflict (`install`)**: **Receipt ownership** (`i-lock`; three `.ev`: "Installed engine {version}/receipt written {at}", "Bundled with Studio 2.4.1/Studio {studio}", "Managed files {files} exact/each recorded with version, path and SHA-256") → **Managed-file drift** (`i-git`; `.tbl` `Path | Ownership | State | Hash`; state containing "modified" renders `chip-danger` `i-warn`) → **Remediation** (`i-check`; `ul.crit` with `i-chev` bullets). Action bar: hint "There is no overwrite shortcut. The 2.3.0 installation stays complete." · `btn` `i-doc` "Open the receipt" · `btn-primary` "Re-run the upgrade preview".

**Budget stop (`budget`)**: **Budget state** (`i-clock`; `.stat` of three `.s`: "Turns used {used} / {cap} · exact", "Window {window} · local time", "Credits Unavailable · not observable") + `.consequence` `creditNote`. Action bar: hint "Budgets never interrupt a running turn; they only prevent the next dispatch." · `btn` "Raise the turn cap" · `btn-primary` `i-play` "Run to next checkpoint". (PRD: no inferred credit value when unobservable — the mockup renders the literal `Unavailable`.)

### 5.8 Sticky action bar (`.actionbar`)

`flex:0 0 auto; display:flex; align-items:center; gap:10px; flex-wrap:wrap; padding:12px 32px; bg --chrome; border-top; backdrop-filter:blur(8px)`. First child `.hint` (11.5px muted, `margin-right:auto`, leading `i-info` 13px) then buttons right-aligned, primary last. ≤430px: hint hidden, buttons stretch equally.

Post-submit state (`S.sent[id] !== undefined`): hint `i-check` **"Submitted as `{id}`. The item clears only after AI-DLC state moves (FR-ACT-007)."** + `btn[data-act=undo]` "Show the queue". (Matches PRD FR-ACT-007/FR-GATE-007: success = observed state movement.)

### 5.9 Confirmation panel (`.confirm[role=group][aria-label="Confirmation"]`) — FR-GATE-001

Rendered between `.detail-body` and `.actionbar`: `margin:0 32px 12px; padding:14px 16px; radius lg; bg --card; border:1px solid var(--accent); shadow-md`.

```
.ch        i-send + heading (13px/600)
.sendtext  exact wire text (mono 12.5px, --bg-elevated, border, pre-wrap, wrap-any)
.route     i-info + routing paragraph (11.5px muted)
[.route danger]  i-warn + validation warning
.confirm-actions  btn-primary[data-act=send] i-check "Yes, send this exact text" · btn[data-act=cancel] "Cancel" · .hint "At-most-once: after this point Studio will reconcile rather than resend."
```

| `S.confirm` | Heading | `.sendtext` (sample) |
|---|---|---|
| `approve` | Confirm the exact text sent to the canonical session | `a.approveText` = `Approve` |
| `reject` | Confirm the change request sent to the canonical session | `Request changes\n\n{feedback.trim()}`; if blank → warn **"Feedback cannot be blank."** and Send disabled |
| `answers` | Confirm the answer group sent as one action | one line per question: `{i}. {prompt}\n   {label(s) | "Other: {text}" | "(no answer)"}` |
| `recovery` | Confirm the recovery you selected | selected choice `label` |

Routing paragraph (verbatim): *"Injected as a real user turn into canonical session `ses_{id}c4` for `{repo}/{intent}`. The Kiro `userPromptSubmit` hook mints the protected `HUMAN_TURN`; the AI-DLC engine commits the transition. Studio does not call `report`, edit `aidlc-state.md`, or set any bypass variable."*

`Escape` closes the panel (`keydown` handler). **PRD FR-GATE-001/002:** the panel must show *both* the localized UI label and the exact canonical wire text; the wire text is the engine-supplied token or a Studio protocol constant — the strings `Approve` and `Request changes\n\n…` above are mockup samples, not the protocol (see research doc on the AI-DLC gate protocol). Add the localized label line (e.g. "Button: 批准 → sends: `Approve`").

### 5.10 AI Advisor block (`advisorHTML`)

Not-run state — `B('AI Advisor','i-advisor', .advisor)`: `.advisor{border:1px dashed color-mix(aim 55%, border); bg --aim-subtle; radius lg; padding:14px 16px}`; head `i-advisor` + `.t` **"Advisor has not run"**; copy **"The Advisor runs only when you ask. It uses a separate read-only session, cannot write files or move the workflow, and never produces human-turn evidence."**; buttons `btn-sm` `i-advisor` "Analyze this decision" (gate/recovery/failure) or "Draft all answers" (questions), plus for non-questions `btn-sm` "Draft change-request feedback".

Ready state — `B('AI Advisor — draft only','i-advisor', …)`: head `.t{verdict}` + `chip-aim` **"draft, not a decision"**; `p` reason (13.5/1.65); `dl` (`grid-template-columns:118px 1fr; gap:6px 14px`) rows in order: **Evidence** (ul), **Assumptions** (ul), **Alternatives** (ul), **Confidence** (text), **Needs your decision** (`chip-warn` `i-warn` "unresolvable from evidence" + text); optional row `btn-sm` `i-doc` **"Put the drafted feedback in the box"** + "You can edit it before anything is sent."; optional row `btn-sm` `i-check` **"Apply the drafted selections"** + **"Nothing is submitted. Approve is never preselected."**; `.disclaim` `i-lock` **"Advisor activity is recorded in Studio Activity only. It is never written into the AI-DLC audit trail, and it cannot mint a `HUMAN_TURN`."** Matches PRD FR-ADV-004/005/007/008/009.

Sample verdicts: gate `Request changes`; questions `Draft prepared for 3 questions`.

### 5.11 Artifacts tab

- Gate: `B('Produced artifact','i-doc', chips: {bytes} · "updated {updated}" · {rev} · chip-info i-git "prior version derived from Git")` + `compareHTML(a)`.
- Install: `B('Files considered by this action','i-doc', .tbl Path | Ownership | State)`.
- Recovery (evidence): `B('Evidence files','i-doc', .evgrid)` + `.consequence` **"Raw audit blocks and payloads open in the Evidence drawer, subject to redaction policy."**

### 5.12 Review tab

Groups (only non-empty): "Open blockers — n", "Advisory — n", "Answered in this revision — n" (each `findingHTML`), then `B('Review contract','i-lock', .evgrid: "Review class {reviewClass} / configured for this stage — exact", "Reviewer architecture-reviewer / review-only agent, cannot author artifacts", "Revisions {revision} / each revision re-ran the reviewer")`.

### 5.13 Activity tab

`B('Timeline','i-activity', ul.tl)` rows `{t} | icon | message + "source: {src}"`; sample sources `AI-DLC`, `Studio`, `KiroCrew`, `Git`. Footer `.consequence`: **"Studio-derived events are labelled Studio. They are never presented as original AI-DLC audit events."** (PRD FR-ADV-009 / §9.19.)

Sample rows: `19:41:55 AI-DLC i-gate "Stage {stage} reached [?] and is awaiting a human decision."`, `19:41:12 AI-DLC i-review "Reviewer pass completed. 1 blocker, 1 advisory, 2 answered."`, `19:38:04 AI-DLC i-doc "Artifact written by the stage agent."`, `19:37:41 Studio i-send "Action {id} created and queued for your decision."`, `19:36:58 KiroCrew i-activity "Turn 12 dispatched into the canonical session under the repo lease."`, `19:36:50 Git i-git "Observed main, working tree clean. No Git write was performed."`

### 5.14 Anchor-link behavior

`[data-anchor]` click: if `S.tab !== 'artifacts'` switch to Artifacts and re-render; then `document.getElementById(id)` → add `.hl` (`background:var(--warn-subtle); box-shadow:0 0 0 2px var(--warn-subtle); radius 3px`), `scrollIntoView({block:'center',behavior:'smooth'})`, remove `.hl` after 2200ms. (Note: since Decision tab also renders the compare panes, an implementer may scroll within the current tab instead of switching.)

---

## 6. Workflow Map (`mapView`)

```
.map-wrap  flex column
  .map-bar   padding:10px 20px; bg --bg-accent; border-bottom; wrap
    chip-accent i-intent "checkout-web / guest-checkout"
    chip "33 stages known · exact" · chip "27 selected · exact" · chip "11 Gates · exact"
    .seg[aria-label=Density] data-density = overview | detailed | dependencies   (margin-left:auto)
    btn-sm[data-act=units][aria-pressed]  i-chevd  "Expand Construction units" | "Hide Construction units"
  flex row
    .map-scroll  padding:16px 20px 26px; overflow:auto
      .lane × 5  (style="--ph:{phase.accent}")  grid 158px | 1fr; padding:14px 0; border-bottom
        .lane-h  sticky left  .bar (3px, --ph) · .nm (13/600) · .sub "{n} stages · {k} skipped" (11 mono)
        .stages  flex wrap gap 9px → button.stage …
        [.units]  when mapUnits && lane has perUnit stages
      .consequence (max-width 820) "Dependency edges are not drawn by default. Selecting a stage overlays its upstream, downstream, consumes and produces relationships. Skipped stages keep their position and their reason."
    aside.inspector[aria-label="Stage inspector"]  width 340; border-left; bg --panel-strong; padding:16px 18px
```

Phases (`PHASES[].name`, accent var): Initialization `--ph-init` · Ideation `--ph-idea` · Inception `--ph-incep` · Construction `--ph-constr` · Operation `--ph-oper` (PRD FR-MAP-001 order).

### 6.1 Stage card (`button.stage[data-stage][data-state][aria-pressed]`)

`width:154px; padding:9px 11px; bg --card; border; radius md; border-top:2px solid var(--ph)`; hover `border --border-hover; shadow-sm`; selected `border-color:var(--accent); box-shadow:0 0 0 1px var(--accent)`.
Contents: optional `.rel` tag ("upstream"/"downstream", absolutely positioned `top:-7px; right:-7px`) → `.sn` stage number (10.5 mono muted) → `.st` stage slug (12.5/600) → `.sm` chip row (10px chips): state badge (§3.6); **if density ≠ overview**: `{agent}` chip, `{rev}` chip if ≠ `none`, `i-clock {el}` chip; always `i-gate "Gate"` chip if `gate`.
Density: `overview` = state + Gate only; `detailed` and `dependencies` = + agent/review/elapsed. **The mockup renders `dependencies` identically to `detailed`**; the relation overlay (`RELATIONS[S.mapSel]` → `.up`/`.down` classes) is applied in every density. PRD FR-MAP-004/005/007 intent: `dependencies` should additionally draw edges for the selected stage; Open Question.

Unit sub-lanes (`unitLanes`, FR-MAP-006): `.units{margin-top:9px; padding-left:12px; border-left:2px dashed var(--border-strong)}`; per per-unit stage a `.unit` row `i-chev "{n} {slug} — per unit"` (11.5 mono muted) then `.stages` of `button.stage[data-state=pending]{width:auto;min-width:150px}` with `.sn` stage number, `.st` unit name (e.g. `unit-1 guest-cart`), chip `i-clock pending`.

### 6.2 Inspector (`inspectorHTML`, FR-MAP-008)

Row: `chip` phase name (border/text = phase accent) + `chip mono` stage number → `h2` slug (16px) → chip row: `i-intent {agent}`, `chip-warn i-gate Gate` if gate, `chip-info i-review {rev}` if ≠ none, `i-clock {el}` → if `why`: `.consequence` "<b>Why it is skipped.</b> {why}" → if `RELATIONS[n]`: `.block` h3 "Relationships" with four `.ev` cards **Upstream / Downstream / Consumes / Produces** (mono values, `<br>`-joined file names) → CTA: `btn-primary[data-open={actionId}]` full-width `{typeIcon} "Open the {typelabel lowercase} in Action Center"` or disabled `btn` `i-lock` **"No eligible operation at this stage"**.

Sample `RELATIONS`: `'2.3': up ['2.1','2.2'], down ['2.4','2.5','3.1'], consumes ['requirements-analysis.md','refined-mockups/'], produces ['functional-design.md']`; `'2.4'`, `'3.1'` similar.

Skipped-reason samples: "Scope preset \"feature\" excludes market research. Position retained (FR-MAP-003)." · "No infrastructure change declared in the intent. Engine permits enabling it while ahead of the cursor." · "Test strategy \"standard\" excludes performance validation. Raise the test strategy to include it."

Mobile: inspector hidden ≤900px and lanes stack; **PRD FR-MAP-009** wants phase accordions — not mocked.

**Fixture inconsistency:** the map numbers Construction as `3.1 contract-design, 3.2 units-generation, 3.3 code-generation, 3.4 build-and-test, 3.5 ci-pipeline`, but actions `a-001` (`3.4 code-generation`) and `a-004` (`3.3 build-and-test`) swap 3.3/3.4. The real stage graph comes from `stage-graph.json` (see the AI-DLC research docs), never from these fixtures.

---

## 7. New intent wizard (`wizardView`, PRD §9.7)

Header: `h1` "New intent"; `.lede` **"Four steps. Nothing is written to the repository until you press Create, and Create never starts execution."**
Stepper `.steps`: `button.step[data-wizstep]` ×4 with `.n` 22px circle (mono 11px; done = `--ok` fill + `✓` **use `Check` icon, the mockup uses the `&#10003;` entity**; now = accent ring + `--accent-subtle`) separated by `.step-sep` hairlines. Labels: **Work · Preset · Plan · Review**.
Footer `.wiz-bar`: `btn` "Back" (disabled on step 1) · `btn-primary` "Continue" (steps 1–3) or `btn-primary` `i-check` **"Create the intent, paused"** (step 4) · right-aligned "Step {n} of 4".

Step 1 **Work** — `.field` rows (label 12/600, `.help` 11.5 muted, inputs `padding:9px 11px; radius md; bg --bg-elevated`):
- Repository `select#wRepo` — help "Only registered repositories appear here." options "{label} — {path}".
- Space `select#wSpace` — help "Single-team installs only ever see `default`."
- Objective `input#wObj` — help "One sentence. This becomes the intent slug and the ideation input." placeholder "Let signed-out shoppers complete a purchase".
- Context the engine should know `textarea#wCtx` (min-height 86) — placeholder "Existing cart service is v3. Payment provider retries once. No account creation is allowed in this flow."
- Project type signals — help "Detected read-only from the repo; never edited." chips: `chip-ok` "TypeScript + Bun detected", `chip-ok` "Existing test runner", `chip` `i-git` "branch main, clean", `chip-ok` "AI-DLC 2.4.1 installed".

Step 2 **Preset** — four `pickRow(label, help, key, list)` groups of `button.pick` cards (`.picks{grid:auto-fit minmax(168px,1fr); gap:9px}`; `.sel{border --accent; bg --accent-subtle}`; `.pl` 13/600, `.pd` 11.5 muted):
- Scope — "Determines which of the 33 stages are eligible." `bugfix` "Bug fix / 12 stages. Skips ideation and most of inception." · `feature` "Feature / 27 of 33 stages. The default for scoped product work." · `mvp` "MVP / 31 stages. Adds market research and refined mockups." · `security` "Security patch / 9 stages. Adds compliance review, skips design exploration."
- Depth — "Controls question volume and artifact detail." `light` "One pass per stage, fewer questions." · `standard` "Recommended. Questions where the engine needs input." · `deep` "More questions and longer artifacts."
- Review cap — "The strongest review class any stage may use." `none` "No reviewer pass. Gates still require you." · `advisory` "Reviewer comments, does not block." · `adversarial` "Reviewer must be answered before a Gate can pass."
- Test strategy — "Controls test volume and whether performance validation is eligible." `minimal` "Smoke coverage only." · `standard` "Unit plus integration for generated units." · `thorough` "Adds performance validation in operation."

Step 3 **Plan** — lead `.consequence` **"Every known stage is listed, including the ones your preset excludes. Stages the engine will not let you change are disabled with a reason."** `.matrix` of five `.mrow` (3px left phase border): `.mh` phase name + `chip` "{on} of {total} on"; `.mstages` pills `label.ms` (`radius 999; padding 5px 9px; 11.5px`; `.on` accent tint; `.locked{opacity:.6; cursor:not-allowed}` with `title="Immutable: this is the current stage"` or `"Immutable: already completed or at its Gate"`) containing `input[type=checkbox][data-toggle={n}]`, mono number, slug, `i-gate` if gate, `i-lock` if locked. Locked = state ∈ {done, current, gate}; on = `!off[n] && state !== 'skipped'`. Trailing `.consequence` `i-warn` (dependency validation sample): **"Turning off `2.4 nfr-requirements` would leave `3.1 contract-design` without its declared input, so the engine rejects that combination."**

Step 4 **Review** (PRD FR-NEW-002 exact vs estimate) — two `.stat` rows:
- Exact: "Stages selected {27-off} / exact, from the installed graph" · `i-gate` "Gates {11-min(off,3)} / exact" · `i-doc` "Artifacts produced {34-2·off} / exact" · `i-review` "Review intensity {review} / exact, as configured". (Fixture arithmetic is illustrative only.)
- Estimates: "Turns 70 – 110 / range · rule-based band · low confidence, no local history yet" · "Active execution 4 – 7 h / range · medium confidence" · "Elapsed with your Gates 2 – 4 days / range · depends on your response time" · `i-warn` "Credits Unavailable / not observable from Kiro signals — not zero, not inferred".
- `B('What dominates the estimate','i-info')` — `.crit` items with `.why` (samples: "3.3 code-generation across 3 units — roughly 40% of the estimated turns", "Adversarial review on 4 stages — roughly 18%").
- `B('Plan diff against the preset default','i-doc')` — `.diffline.del` "- {n} disabled by you" per toggle, or `.diffline.add` "+ no changes — this is the unmodified feature preset".
- `B('Products this plan will create','i-doc')` — mono chips of artifact names.
- `.consequence` **"Create does not run anything. The intent is created paused; you start it with <b>Run to next checkpoint</b>. Keep moving, budgets and Advisor drafts are never inherited from another intent."** (FR-NEW-001/004/005.)

Create (`data-act=create`) in the mockup: route → actions, `window.alert("Intent created and left paused.\n\nNothing has run yet. Start it from Intents with \"Run to next checkpoint\".")` — replace with the host `useNotify()` toast.

Wizard state: `S.wiz = {repo, space, objective, context, kind, scope:'feature', depth:'standard', review:'adversarial', test:'standard', off:{}}`, `S.wizStep 1..4`; stepper buttons jump directly.

---

## 8. Secondary pages

### 8.1 Repos (`reposView`)
`h1` "Repos"; lede **"Three registered paths. Studio never scans the home directory; every entry here was added deliberately."** `.tbl` columns: **Repository | AI-DLC | Engine | Intents | Queue | Git | (actions)**. Cell: label (`.strong`) + path (mono 11.5 muted); install chip (§3.8); engine mono + "bundled 2.4.1" sub-line when drifted; intents/queue counts mono; `chip` `i-git` "main · clean"; `btn-sm` "Maintenance" or `btn-sm[data-open=a-005]` "Review conflict". Footer `.consequence` **"Maintenance upgrades live here and in notifications; they never enter the blocking workflow queue. Removing a repository unregisters it and changes no bytes on disk."** (FR-ACT-008.)

### 8.2 Intents (`intentsView`)
`h1` "Intents"; lede **"Multiple intents may be in flight in one repository. Turns serialize per repository; different repositories run in parallel."** Columns **Repo | Intent | State | Stage | Keep moving | (actions)**; state chips §3.7; `btn-sm` "Open action" (deep-opens the action) or "Details". PRD §8.1 wants running/queued/waiting/parked/failed/completed/archived *views* — no filter UI mocked.

### 8.3 Activity (`activityView`)
`h1` "Activity"; lede **"Human-readable timeline. Every event names its source, and a Studio-derived event is never shown as an AI-DLC audit event."** Filter chips **All sources** (`chip-accent`) · AI-DLC · Studio · KiroCrew · Git (static). `.tl` rows (§5.13 shape). Footer `.consequence` `i-lock` **"The Evidence drawer exposes raw audit blocks and file locations. Diagnostic export redacts credentials, protected paths and prompt bodies by default."**

### 8.4 Settings (`settingsView`)
`h1` "Settings"; lede **"Studio-owned preferences. Nothing here changes AI-DLC files."** `.matrix` of `.mrow > .spread` rows (title 13.5 strong / desc 12 muted / control right):

| Title | Description | Control |
|---|---|---|
| Language | English and Simplified Chinese ship complete and parity-tested. | `select` "English (en-US)" / "简体中文 (zh-CN)" |
| Theme | Follows KiroCrew tokens in light and dark. | `btn-sm` `i-moon` "Toggle" — **drop; host-owned** |
| Night work window | Off by default. Only intents with Keep moving participate. | `chip` "22:00 – 06:00 · off" |
| Turn cap per window | Checked before dispatch; never interrupts a running turn. | `chip mono` "40" |
| Credit cap | Disabled because credit consumption is not observable from the available signals. | `chip-warn` `i-warn` "unavailable" |
| Global concurrency | Per-repo execution is always one and cannot be raised. | `chip mono` "3 repos" |
| Slack quick actions | Blocking events only. Complex question groups deep-link back here. | `chip-ok` `i-slack` "owner DM" |
| Advisor model | Inherited through KiroCrew role resolution. No model id is hardcoded. | `chip` `i-advisor` "inherited" |
| Versions | App version and bundled AI-DLC version are separately visible. | `chip mono` "Studio 1.0.0 · AI-DLC 2.4.1" |

Controls are display-only in the mockup; real inputs (`Toggle`, `Input` from `@kirocrew/ui`) are needed.

---

## 9. Microcopy catalogue (intended voice)

Voice: declarative, evidence-first, one idea per sentence, states what Studio *does not* do. Uses typographic em dash `—` and middle dot `·` separators. Product nouns capitalized exactly: `Gate`, `Keep moving`, `Run to next checkpoint`, `Request changes`, `Approve`, `Advisor`, `Action Center`, `Workflow Map`, `Evidence drawer`, `Night work window`, `HUMAN_TURN`, `[?]`.

Key strings not already quoted above:
- Queue header **"Needs you"**; counter **"{n} of {total}"**.
- Sample titles: "Approval may have been delivered before the gateway restarted" · "Approve the functional design for guest checkout" · "Answer 3 questions from the NFR requirements stage" · "Circuit open after 3 matching transport failures" · "Upgrade stopped: two receipt-owned files were modified locally" · "Night window turn cap reached before this intent advanced".
- Sample consequences: "Nothing is replayed automatically. A duplicate approval could advance stage 3.4 twice, so this intent stays blocked until the evidence agrees." · "Approving unlocks stage 2.4 (NFR design) and stage 3.1 (contract design). The design becomes the input those two stages consume, so a change requested later costs a re-run of both." · "Answers become the recorded NFR inputs for stages 2.4 and 3.1. Sending advances the turn immediately." · "No further dispatch happens for this intent until the fingerprint is reset by a relevant change or by an explicit Retry now." · "Nothing was written. The repo keeps its complete 2.3.0 installation and receipt. There is no overwrite shortcut." · "The intent stays Queued. Raising the cap, or running it manually now, is the only way it advances today."
- Recovery choice samples: "Rebind the intent to a fresh canonical session and re-verify from the last stable boundary" (recommended) · "Treat the approval as not delivered and let me resubmit it" · "Park the intent and keep the evidence for manual review".
- Failure note: "This repo also reports installed engine 2.3.0 against bundled 2.4.1. Version drift is shown on Repos as maintenance and is not assumed to be the cause."
- Install remedy: "Revert the two files to their recorded 2.3.0 content, then re-run the upgrade preview." · "Or keep the local edits and stay on 2.3.0; the intent continues to run against the installed engine." · "Studio cannot merge these for you — an installer that silently resolved this would violate the receipt contract."
- Budget credit note: "Credit consumption is not observable from the available Kiro signals, so a credit cap cannot be enforced and is disabled rather than estimated (FR-NIGHT-007)."
- Fingerprint format sample: `acp.transport.stream_closed · 5f2c9a` (Activity uses `acp.transport.stream_closed:5f2c9a`).

All UI strings must go through the App's i18n layer (PRD §17 en-US + zh-CN parity); the mockup is en-US only.

---

## 10. JS behavior in the mockup (state model to replicate)

```js
S = { route:'actions', scope:'all', sel:'a-002', tab:'decision',
      confirm:null /* 'approve'|'reject'|'answers'|'recovery' */,
      advisor:{} /* actionId -> 'loading'|'ready' */,
      answers:{} /* 'actionId/qId' -> id | id[] | '__other'; 'actionId/qId/other' -> text */,
      feedback:'' /* GLOBAL — mockup bug, must be per-action */, recoveryChoice:'c1',
      sent:{} /* actionId -> delivery step idx */, order:'priority', query:'',
      mapSel:'2.3', mapDensity:'overview', mapUnits:false,
      wizStep:1, wiz:{…} }
```

Event delegation (single `click`/`input`/`keydown` listener on `document`), attribute-driven:
`[data-route]` navigate (non-actions routes force `pane=list`) · `#backBtn` → `pane=list` · `.qitem` select (resets tab/confirm, `pane=detail`) · `[data-open]` open action from Map/Repos/Intents (also sets `route='actions'`) · `[data-order]` · `[data-tab]` · `#scopeBtn` cycle · `[data-anchor]` (§5.14) · `[data-copy]` · `[data-stage]` · `[data-density]` · `[data-pick]` · `[data-wizstep]` · `[data-wiznav]` · `[data-act]` ∈ `units | advisor | draftall | usefeedback | applypicks | approve | reject | answers | recovery | cancel | send | retry | undo | create`.
`input`: `#qSearch`, `[data-other]`, `#wObj`, `#wCtx`, `#wRepo`, `[data-qkey]` (multi → toggle in array; single → set), `[data-toggle]` (wizard matrix), `[name=rec]`.
`keydown`: `Escape` → `confirm=null`.
Full re-render on every state change (`render()`); `#fb` re-binds its `input` listener after each render; the Advisor/feedback actions `scrollIntoView` + `focus()` the target.

**PRD §10.2 gaps:** no local persistence of drafts and no "warn before discarding" when switching items; `feedback` is not keyed per action. Implement drafts keyed by `actionId` in `localStorage` (App `permissions.storage:true` is already declared in `app.json`).

---

## 11. Icon inventory (mockup `<symbol>` → lucide-react)

| Symbol | Meaning / where used | lucide-react |
|---|---|---|
| `i-logo` | brand mark | custom SVG or `Workflow` / `ListTree` |
| `i-inbox` | Action Center nav, Decision tab, brief header | `Inbox` |
| `i-repo` | Repos nav, scope selector | `Book` (shape) or `FolderGit2` (host precedent in `app.json` uses `GitBranch`) |
| `i-intent` | Intents nav, agent chip, map intent chip | `ListTodo` / `Target` |
| `i-map` | Workflow Map nav, empty-state CTA | `LayoutGrid` (shape) / `Workflow` |
| `i-activity` | Activity nav/tab, KiroCrew-source rows | `Activity` |
| `i-settings` | Settings nav | `Settings` |
| `i-gate` | Gate type, Gate chips, blocking chip | `Lock` shape; prefer `LockKeyhole` or `ShieldCheck` so it differs from `i-lock` |
| `i-question` | Questions type | `CircleQuestionMark` (canonical; `CircleHelp`/`HelpCircle` exist only as re-export aliases) |
| `i-recovery` | Recovery type, critical chip | `TriangleAlert` |
| `i-fail` | Circuit open type | `CircleX` |
| `i-install` | Install conflict type | `Download` |
| `i-check` | met / done / approve / answered | `Check` |
| `i-warn` | unmet / blocker / attention | `CircleAlert` |
| `i-info` | informational / consequence | `Info` |
| `i-clock` | waiting, elapsed, budget, pending | `Clock` |
| `i-doc` | artifacts, files, receipts | `FileText` |
| `i-review` | Review tab, reviewer, review class | `Eye` |
| `i-advisor` | AI Advisor | `Sparkles` |
| `i-send` | Send answers, confirm heading, Studio action | `Send` |
| `i-search` | queue filter | `Search` |
| `i-chev` / `i-chevd` | breadcrumb, bullets / dropdown, expand | `ChevronRight` / `ChevronDown` |
| `i-back` | mobile back | `ChevronLeft` (or `ArrowLeft`) |
| `i-play` / `i-pause` | running, Retry now, Run to next checkpoint / Keep paused | `Play` / `Pause` |
| `i-sun` / `i-moon` | theme, night window | `Sun` / `Moon` |
| `i-slack` | Slack quick actions | **no `Slack` brand icon in host lucide-react 1.7.0** — use `MessageSquare` (or `Hash`), or ship the mockup's inline `<symbol id="i-slack">` as a local SVG |
| `i-git` | Git observation, drift, branch chips | `GitBranch` |
| `i-lock` | leases, receipts, review contract, immutable stages, disclaimers | `Lock` |
| `i-plus` | New intent | `Plus` |
| `i-link` | deep link, "in artifact" anchor | `Link` |

Missing-input template (not mocked): `FileQuestionMark`. Every lucide name in this table was verified against `website/node_modules/lucide-react/dist/lucide-react.d.ts` (package version **1.7.0**). Canonical `declare const` identifiers are `CircleQuestionMark`, `FileQuestionMark`, `TriangleAlert`, `CircleX`, `CircleAlert`; the legacy names `CircleHelp`, `HelpCircle`, `FileQuestion`, `AlertTriangle`, `XCircle`, `AlertCircle` are exported only as re-export aliases (`export { … as AlertTriangle }`) — prefer the canonical names. `Slack` is absent in any form. Other verified candidates for the brand mark / Gate: `Layers`, `Route`, `Network`, `Milestone`, `DoorClosed`, `BookMarked`, `Gauge`.

---

## 12. Options A and C — what must NOT be imported (PRD §10.0)

Skimmed by headings/CSS only.

**Option A — Dense Operations** (`option-a-dense-operations.html`): `.split{grid-template-columns:36% 64%}` with dense four-column `.qrow{3px 16px 1fr auto}` rows; severity/type **tag fills** beside titles (`.dtitle .tag`); `.kv` key-value grids everywhere; `.grid2` two-up blocks; `.toast` bottom-center toasts; `Blast radius` blocks; map `1fr 320px` with `data-density` on the wrapper and colored `up/down` borders; A-specific block headings ("Decision brief and consequence", "Contradictions and risk", "Safe stop", "What this changed", "Plan diff vs the {depth} preset", "Exact facts"). Do not import its row density, tag fills, toast pattern, or block header style.

**Option C — Adaptive Command** (`option-c-adaptive-command.html`): top **command bar** `.cmd` + `.palette` listbox with token chips; **quick filter** chip row `.filters`; **collapsible status strip** `.strip[data-open]` with rotating caret and KPI grid; primary nav as underline **tabs** `.views[role=tablist]`; phase-colored `card-ph` left borders on decision cards; collapsible queue groups `.qgroup[data-open]`; **keyboard reference modal** and a keyboard layer; toasts; "Submission preview" card. Do not import the command surface, quick filters, strip card layout, tabbed nav, or phase-bordered decision cards. (The PRD still requires a *collapsible* strip — implement the behavior on Option B's chip strip, not C's layout.)

---

## 13. Mockup vs. PRD vs. host code — discrepancy register

| # | Topic | Mockup | Authority | Resolution |
|---|---|---|---|---|
| 1 | Queue width | fixed `300px` | PRD §8.3 ≈36% and §10.0 "narrow priority queue" | `minmax(300px,36%)`; needs sign-off |
| 2 | Status strip | static chips, not collapsible | PRD §8.3 "collapsible" | add disclosure on B's strip |
| 3 | `--muted-strong` | dark `#a1a1aa` (light emphasis) | HOST dark `#52525b` (darker than `--muted`) | App alias `--aidlc-emph` |
| 4 | `--info` | dark `#38bdf8`, light `#0e7490` | HOST `#0891b2` both; light fails AA at 11.5px | `--aidlc-info-text` mix; `--info-subtle` App-defined |
| 5 | Theme toggle in topbar/Settings | present | HOST owns `html[data-theme]`, 30+ themes | remove |
| 6 | Theme blocks `html[data-theme=dark|light]` | redefines all tokens | HOST | ship only `--ph-*`, `--info-subtle`, aliases |
| 7 | Queue row fields | no intent/space/primary | PRD FR-ACT-006, §10.3 SR label | add to `.qmeta` + `aria-label` |
| 8 | Type ordering | unsorted | PRD FR-ACT-004/005 | sort type→sev→wait; persist order |
| 9 | Per-question Advisor | only "Draft all" | PRD §9.5 `Explain`, `Draft this` | add |
| 10 | Draft persistence / discard warning | none; `feedback` global | PRD §10.2 | per-action drafts in storage; confirm on switch |
| 11 | Wire text | `Approve`, `Request changes\n\n…`, numbered answers | PRD FR-GATE-002 (engine token / protocol constant) | source from protocol research; show localized label + wire text |
| 12 | FR references in copy | "FR-GATE-002" for nonblank feedback | PRD numbers it FR-GATE-003 | never hard-code FR ids in UI |
| 13 | Map density `dependencies` | identical to `detailed` | PRD FR-MAP-004/005/007 | draw edges for selection in `dependencies` |
| 14 | Map mobile | lanes stack, inspector hidden | PRD FR-MAP-009 phase accordions | implement accordions + inspector sheet |
| 15 | Missing-input template | not mocked | PRD §9.5 | derive from budget template |
| 16 | Construction stage numbers | 3.3/3.4 swapped between map and actions | `stage-graph.json` | data-driven |
| 17 | Stepper done glyph | `&#10003;` text | PRD §10.1 lucide, no emoji/glyph | `Check` icon |
| 18 | `window.alert` on Create | alert | HOST `useNotify()` | toast |
| 19 | Deep link | copy button only | PRD §10.2 restore scope/action/tab/anchor | define URL scheme |
| 20 | Fonts | Latin stacks | HOST adds `--theme-font-*` and CJK `unicode-range` fallbacks | inherit |

---

## 14. Host integration facts relevant to the visual layer (HOST code)

- App UI entry is an ESM module with a **default-exported React component**, loaded by `components/AppHost.tsx` via `lazy(() => import(bundlePath))` where `bundlePath = \`/apps/${app.name}/ui/${app.manifest.ui.entry}\`` (line 224). In dev mode a `mc:app-reload` window event triggers a full page reload. The prototype's `ui/index.mjs` is hand-written ESM with `_jsx` helpers (no build step) and validates with `node --check ui/index.mjs`. Load failure renders the host's own error card (`AlertTriangle` 48px + "Failed to load …" + back `Btn`).
- Import map modules available (`app-sdk/shared-modules.ts`): `react`, `react-dom`, `react/jsx-runtime`, `lucide-react`, `@tanstack/react-query`, `@kirocrew/app-sdk`, `@kirocrew/ui`. Also reachable at runtime as `window.__kirocrew_modules[...]` (prototype feature-detects `@kirocrew/ui`).
- `@kirocrew/app-sdk` hooks: `useAppApi()` (`get/post/put/patch/del`), `useAppEvents(event, cb)`, `useTheme()` → `{mode, accent, colorTheme}`, `useAppInfo()`, `useNavigate(path)`, `useNotify(message, {type:'info'|'success'|'error'})`.
- `@kirocrew/ui` exports: `Card, CardTitle, Btn, SendBtn, Input, SearchInput, Badge, SourceBadge, StatCard, Skeleton, ContentSkeleton, EmptyState, PageHeader, Toggle, InfoTip, SegmentedControl, MarkdownRenderer`. `Btn` props: `primary?: boolean; danger?: boolean` + button attrs. `Badge` variants: `'ok'|'err'|'warn'|'aim'|'muted'`. `SegmentedControl` props: `{segments: Segment[], value, onChange, layoutId='segment', collapse=true}` with `segments[].key`.
- Host styles are Tailwind-based (`@tailwind base/components/utilities`; classes like `text-accent`, `bg-bg-hover`). The App cannot rely on arbitrary Tailwind utilities being present for its own markup — ship the Option B CSS as a scoped stylesheet (prefix or `[data-app="aidlc-studio"]` root) or inline styles, referencing host tokens only.
- Current prototype manifest (`app.json`) slug is `aidlc-console`, route `/aidlc-console`, sidebar icon `GitBranch`, `permissions.storage:true`, `network:false`. The new App slug per task is `aidlc-studio`.

---

## 15. Open questions (not resolvable from mockup or code)

1. Queue column: fixed 300px (mockup) vs ≈36% (PRD §8.3) — which wins, and is `minmax(300px,36%)` acceptable?
2. Deep-link URL scheme for `scope / action / tab / anchor` (PRD §10.2) — hash vs query vs host router (`useNavigate`).
3. Exact wire strings for Approve / Request changes / answer groups / recovery choices (FR-GATE-002) — depends on the AI-DLC gate protocol research; the mockup's `Approve` and `Request changes\n\n{feedback}` are placeholders.
4. Collapsible status strip: collapsed appearance (single summary chip? count only?) — not designed in B.
5. Workflow Map `dependencies` density: what edges are drawn (only for the selected stage, or all)? Mockup draws none.
6. Mobile Workflow Map accordions and inspector presentation (FR-MAP-009) — not mocked.
7. Missing-input template layout — not mocked; assumed to reuse the budget template.
8. Whether the App may expose a theme control at all (host owns theme) and how "Language" in Settings interacts with host locale (`html:lang`).
9. How the Evidence drawer (referenced three times in copy) is presented — no drawer is mocked.
10. Whether Studio should show `--ph-init` as a distinct slate hue or reuse `--muted` for theme coherence across the host's 30+ themes.

---

## Critic addendum (2026-09-04, completeness pass)

### A1. Wire text (closes §15 Q3 and discrepancy #11)

Resolved in doc 05 addendum A7/A8 against AI-DLC 2.6.2: there is no engine-supplied response token; the wire text is the exact option **label**. For the Gate template: `.sendtext` = `Approve`, or `Request Changes` + blank line + feedback (capital C — the mockup's `Request changes` is the *summary-confirmation* label, a different checkpoint). The confirmation panel must therefore render a per-checkpoint constant, and the localized button label line required by FR-GATE-001 (e.g. `按钮：批准 → 发送：Approve`).

### A2. Dead host affordances the mockup assumes

- `useNotify()` has no listener in the running 0.5.0 dashboard (doc 03 addendum A1) — §7 "replace `window.alert` with the host `useNotify()` toast" must become an inline Studio notice.
- `useAppEvents` never fires; the execution status strip (§1.3) needs Studio's own SSE/WebSocket or polling (doc 03 addendum A3).

### A3. Import-map correction to §14

Only `react, react-dom, react-dom/client, react/jsx-runtime, @kirocrew/app-sdk, @kirocrew/app-sdk/ui, lucide-react` are import-map specifiers (verified in the running bundle's `index.html`). `@tanstack/react-query` and `@kirocrew/ui` are reachable only through `window.__kirocrew_modules[...]`; the UI components import as `@kirocrew/app-sdk/ui`. Named lucide imports are limited to the fixed list in doc 03 §1.3 — every icon in §11 beyond that list must come from the default Proxy export.

### A4. Data the mockup hard-codes that must come from disk

Stage numbers/slugs (§6, discrepancy #16) come from `<repo>/.kiro/tools/data/stage-graph.json` (33 stages in 2.6.2, doc 04 §7); scope presets in §7 step 2 (`bugfix 12 stages`, `feature 27 of 33`, `mvp 31`, `security 9`) do not match the real grid (`bugfix 7, feature 33, mvp 23, security-patch 10`, doc 04 §8) and must be computed from `scope-grid.json` + `.kiro/scopes/aidlc-*.md` frontmatter (`depth`, `testStrategy`, `review_cap`).
