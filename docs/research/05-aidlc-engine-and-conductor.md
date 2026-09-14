# 05 — AI-DLC v2 engine, conductor, hooks, and human-interaction protocol

Research for AI-DLC Studio (PRD §6, §7, §9.12–9.16, §19 spikes S1/S12/S13).
Source of truth: `/Users/ychchen/warren_ws/aidlc-workflows` at framework version
`AIDLC_VERSION = "2.2.10"` (`core/tools/aidlc-version.ts`). Everything below was read
from code; where the shipped docs or the PRD disagree with code, the code wins and the
disagreement is called out in §1.

Path conventions used here:

- `core/…` is the harness-neutral source; `dist/kiro/.kiro/…` is the byte-identical
  projection for Kiro CLI (the `{{HARNESS_DIR}}` token becomes `.kiro`). I verified
  `harness/kiro/hooks/aidlc-kiro-adapter.ts` and `harness/kiro/agents/aidlc.json` are
  identical to their `dist/kiro/.kiro/` copies.
- `<repo>` = the registered project root (the directory that contains `.kiro/` and `aidlc/`).
- `<record>` = `aidlc/spaces/<space>/intents/<dirName>/`.

---

## 1. Doc/PRD statements that disagree with code (read first)

| Claim | Where | What the code actually does |
|---|---|---|
| "The public engine exposes `next`, `continue`, `report`, and `park`" | PRD §7.2 | `aidlc-orchestrate.ts` `main()` accepts exactly `next`, `report`, `park`; anything else prints `Unknown subcommand: … Valid: next, report, park` and exits 1 (`core/tools/aidlc-orchestrate.ts:3011-3031`). There is no `continue` verb. Resume is `next --resume`; un-parking is `aidlc-state.ts unpark`, named by a `print` directive (Branch 2.6). |
| "Human-gated reports require exact user input" | PRD §7.3 | `--user-input` on `report`/`approve` is **optional and unvalidated** by the engine: `if (userInput) gateFields["User Input"] = userInput;` (`aidlc-state.ts:1353`). "Exact option label" is a prose rule in `stage-protocol.md` (§2 Part 0, "Never summarize User Input"), enforced only by the conductor. What the engine *does* require is a `HUMAN_TURN` after the prior resolution (§6 below). |
| "The Kiro `userPromptSubmit` adapter … updates the human-turn marker" | PRD §7.4 | There is no human-turn marker file. Presence is the `HUMAN_TURN` **audit event** (CHANGELOG 2.1.6: "no marker file, no turn counter, no consumed flag: the ledger itself is the record"). The adapter also bumps `aidlc/.aidlc-turn-counter` on every prompt and writes `aidlc/.aidlc-readonly-latch` only for terminal commands; those two files are a *roll-forward* guard, not presence evidence. |
| `--doctor` "is read-only" | `docs/guide/12-cli-commands.md:211` | `handleDoctor` emits `GUARDRAIL_LOADED` and `HEALTH_CHECKED` audit rows when an audit shard exists (`aidlc-utility.ts:1348,1380`) and calls `detectLeakedLocks(projectDir, true)` which **deletes** leaked lock dirs in `tmpdir()` (`aidlc-utility.ts:~756`). Read-only for the state file, not for audit or tmp locks. |
| `ask` directive: "feed the human's answer back on the next `report` via `--user-input`" | `harness/kiro/skills/aidlc/SKILL.md:55` | `handleReport` errors with `report requires --result <outcome>` when only `--user-input` is passed (`aidlc-orchestrate.ts:2727-2735`). In practice the conductor answers an `ask` by re-running `next` with new flags (`next --scope X`, `next compose "…"`, `next --stage X`). |
| `harness/kiro/hooks/*.kiro.hook` files (promptSubmit/agentStop wiring) | repo tree | Not shipped for Kiro CLI: `harness/kiro/manifest.ts` lists them only in `authoredExempt`, not `harnessFiles`; `dist/kiro/.kiro/hooks/` contains none. Kiro CLI hooks are wired solely through `agents/aidlc.json` `hooks`. The `.kiro.hook` mechanism is the **Kiro IDE** harness (`harness/kiro-ide/manifest.ts`). |
| PRD flags `--jump`, `--redo`, `--park`, `--skip/--include`, `--switch-intent`, `--space <name>` | PRD §9.7/§9.8 wording | None of these exist as `/aidlc` flags (see §4.2 for the real list). Jump = `--stage`/`--phase`; redo = the resume `ask` or `--stage <current>` (direction computed by `aidlc-jump.ts resolve`); park = `aidlc-orchestrate.ts park`; skip/add = `aidlc-utility.ts recompose --skip/--add` via the `compose` verb; intent/space switch = the `intent <name>` / `space <name>` **verbs**. |
| `audit-format.md` says `WORKFLOW_STARTED` emitter is `aidlc-utility.ts init` | `core/knowledge/aidlc-shared/audit-format.md` | `init` is a deprecated alias routed to `handleIntentBirth`; the real emitter is `aidlc-utility.ts intent-birth`. |
| Session events on Kiro: docs say only `SESSION_STARTED` | `docs/guide/harnesses/kiro-cli.md` | Correct; also `SESSION_RESUMED` is structurally unreachable on Kiro (agentSpawn carries no resume source — adapter comment lines 285-292). |

---

## 2. On-disk layout the engine reads and writes

All paths are relative to `<repo>`.

### 2.1 Cursors and workspace-level runtime files (`core/tools/aidlc-lib.ts`)

| Path | Content | Writer | Git |
|---|---|---|---|
| `aidlc/active-space` | `<space>\n` (default `default`) | `setActiveSpaceCursor` (lib:911) via `aidlc-utility.ts space <name>` | ignored |
| `aidlc/spaces/<space>/intents/active-intent` | `<dirName>\n` | `setActiveIntentCursor` (lib:900) via `intent <name>` and `birthIntent` | ignored |
| `aidlc/spaces/<space>/intents/intents.json` | JSON array of `{uuid, slug, dirName?, scope?, repos?, status}` (`IntentRegistryEntry`, lib:744). `status` vocabulary in code: `"in-flight"` at birth (lib:1098), `"complete"` set by `aidlc-state.ts complete-workflow` via `updateIntentStatus` (state.ts:1199), `"unknown"` synthesised by `listIntents` for an on-disk record with no registry row (lib:889). | `appendIntentToRegistry` / `updateIntentStatus` (atomic write) | committed |
| `aidlc/.aidlc-clone-id` | 12-hex clone token; names this clone's audit shard | `cloneId()` lib:1330, minted on first use | ignored |
| `aidlc/.aidlc-turn-counter` | integer + `\n` | Kiro adapter `verb-intercept`, **every** userPromptSubmit | ignored |
| `aidlc/.aidlc-readonly-latch` | `{"turn":N,"flag":"status","source":"read-only-flag"\|"workspace-verb","ts":ms}\n` | Kiro adapter, terminal commands only | ignored |
| `aidlc/.aidlc-sessions/<session-id>` | intent UUID | session-start hook stamp; re-stamped by `intent <name>` | ignored |
| `aidlc/.aidlc-sessions/.current-session` | most recent session id | session-start hook on every fire (when `session_id` present) | ignored |
| `aidlc/.aidlc-compose-pending` | any content; conductor-written marker for an in-flight compose gate; TTL 24h (`COMPOSE_MARKER_TTL_MS`) | conductor (fs_write allowlisted) | ignored |
| `aidlc/spaces/<space>/memory/{org,team,project}.md`, `phases/*.md`, `templates/` | method/rules | learnings tool, space-create | committed |
| `aidlc/spaces/<space>/knowledge/`, `aidlc/spaces/<space>/codekb/<repo>/` | team knowledge, code KB | RE stage | committed |

Cursor resolution (`activeIntent`, lib:448): explicit arg > cursor **if it names a dir that
contains `aidlc-state.md`** > the lone intent if exactly one record exists > `null`. When
`null`, every path helper collapses to the bare space root
(`aidlc/spaces/<space>/intents/aidlc-state.md`, which never exists), so `next` reports
"No workflow state found" or, if intents exist, emits the `intentPickPromptIfRecordsExist`
ask (§4.1). Studio must treat "cursor file present but dangling" as a distinct state.

### 2.2 Per-intent record (`<record>/`)

| Path | Purpose |
|---|---|
| `aidlc-state.md` | the state file (§2.3) |
| `audit/<host>-<cloneid>.md` | per-clone audit shard; readers glob `audit/*.md` and merge-sort by `**Timestamp**` (lib:1479-1511) |
| `<phase>/<stage>/*.md`, `<phase>/<stage>/memory.md`, `<phase>/<stage>/<stage>-questions.md` | artifacts, diary, questions |
| `construction/<unit>/<stage>/…` | per-unit Construction artifacts |
| `runtime-graph.json` | compiled runtime graph (`aidlc-runtime.ts compile`) — ignored |
| `.aidlc-hooks-health/<hook>.last` | heartbeat written on **every** hook fire (stop, session-start, audit-logger, …) — ignored |
| `.aidlc-stop-hook/block-count.json` | `{"signature":"<stage>::<auditLineCount>","count":N}` — ignored |
| `.aidlc-recovery.md`, `.aidlc-plan.json`, `.aidlc-sensors/` | recovery breadcrumb, resolve plan, sensor scratch — ignored |
| `verification/`, `archive/` | phase verification, pre-change archives |

Record dir naming: current births produce `<YYMMDD>-<label>` (label = slugified
`--label`, cap 24, collision counter); legacy dirs are `<slug>-<id8>`. Never derive the dir
from slug+uuid; read `intents.json[].dirName` (`recordDirMatches`, lib:763).

### 2.3 `aidlc-state.md` format (authored by `handleIntentBirthStateBuild`, `aidlc-utility.ts:2714-2761`)

Sections and fields (`- **Field**: value` lines; `getField` regex is
`^- \*\*<Field>\*\*:[ \t]*(.*)$` with the `m` flag, lib:2056):

```
# AI-DLC State Tracking
## Project Information
- **Project**, **Project Type** (Greenfield|Brownfield), **Scope**, **Start Date**,
  **State Version**: 7, **Active Agent**, **Worktree Path**, **Bolt Refs**,
  **Practices Affirmed Timestamp**
## Scope Configuration
- **Stages to Execute**, **Stages to Skip**, **Depth**, **Test Strategy**
## Workspace State
- **Project Root**, **Languages**, **Frameworks**, **Build System**
## Execution Plan Summary
- **Total Stages**, **Completed**, **In Progress**
## Runtime State
- **Revision Count**: 0   (+ inserted later: **Skeleton Stance**, **Parked**, **Parked At Stage**,
  **Construction Autonomy Mode**: autonomous|gated)
## Phase Progress
- **Initialization|Ideation|Inception|Construction|Operation**: Pending|Active|Verified|Skipped
## Stage Progress
### <PHASE> PHASE
- [x] workspace-scaffold — EXECUTE
- [-] intent-capture — EXECUTE
- [ ] market-research — SKIP
## Current Status
- **Lifecycle Phase** (UPPERCASE), **Current Stage** (slug), **Next Stage**, **Status**
  (Running|Completed|Paused), **Last Updated**
## Session Resume Point
- **Last Completed Stage**, **Next Action**, **Pending Artifacts**
```

Checkbox line regex (`parseCheckboxes`, lib:2208): `^- \[([ xSR?-])\] (\S+)\s*—\s*(.*)$`
(the separator is an em dash U+2014). Marker map (lib:61): `[ ]` pending, `[-]` in-progress,
`[?]` awaiting-approval, `[R]` revising, `[x]` completed, `[S]` skipped. The suffix after the
em dash is `EXECUTE` or `SKIP…` and overrides the static scope grid (recompose flips it).

`hasOpenGate(content)` (lib:1425) is true iff **any** stage row is `[?]`.
`isAutonomousMode(content)` (lib:2078) is `getField("Construction Autonomy Mode") === "autonomous"`.

### 2.4 Audit block format (`core/tools/aidlc-audit.ts:260-296`)

```
\n## <Heading>\n**Timestamp**: <ISO-8601 Z>\n**Event**: <EVENT_TYPE>\n**<Key>**: <value>\n…\n\n---\n
```

Blocks are split on `\n---\n`; CR/LF inside values are escaped to literal `\n`. Heading is
`EVENT_HEADINGS[event] || event`; for presence the block is exactly
`\n## Human Turn\n**Timestamp**: <iso>\n**Event**: HUMAN_TURN\n\n---\n` (audit.ts:146). 70 canonical event types (`VALID_EVENT_TYPES`,
audit.ts:21-125); `appendAuditEntry` throws on any other name. Events Studio must recognise:
`HUMAN_TURN` (fields: Timestamp only), `STAGE_AWAITING_APPROVAL` (Stage, optional
`Recovered: true`), `GATE_APPROVED` (Stage, optional `User Input`), `GATE_REJECTED` (Stage,
optional `Feedback`), `STAGE_REVISING` (Stage, `Revision count`, Feedback), `STAGE_COMPLETED`,
`STAGE_STARTED`, `QUESTION_ANSWERED` (Stage, Details), `DECISION_RECORDED` (Stage, Decision,
Options), `WORKFLOW_PARKED`/`WORKFLOW_UNPARKED`, `SESSION_STARTED` (Source), `ERROR_LOGGED`
(Tool, Command, Error), `RECOMPOSED`, `SCOPE_CHANGED`, `DEPTH_CHANGED`,
`TEST_STRATEGY_CHANGED`, `HEALTH_CHECKED`, `GUARDRAIL_LOADED`, `SWARM_*`, `AUTONOMY_MODE_SET`.

`ERROR_LOGGED` matters: every tool's `error()`/`die()` routes through `emitError`
(lib:3845-3900), which **appends an `ERROR_LOGGED` row to the active intent's shard**
whenever `aidlc-state.md` resolves, before exiting 1 with `{"error":"<msg>"}` on stderr.
A refused approve, an unknown intent name, or a bad flag all mutate the audit trail.

---

## 3. CLI verbs: read-only vs mutating

All tools accept `--project-dir <path>` (else `CLAUDE_PROJECT_DIR`, else derive from the
script's own `<repo>/.kiro/tools` location — `resolveProjectDir`, lib:205). Tools shell out
to siblings with `["bun", "run", <abs path>]` (`spawnState`, orchestrate:2431), so `bun` must
be on `PATH` for the conductor's shell; the Kiro adapter uses `process.execPath` instead.

### 3.1 `aidlc-orchestrate.ts` (the engine)

| Verb | Effect |
|---|---|
| `next [flags…]` | **Read-only.** Emits exactly one JSON directive on stdout (§4). Spawns only pure reads (`aidlc-jump.ts resolve`, `aidlc-utility.ts resolve-env-scope`). Reads `aidlc/.aidlc-turn-counter` + `aidlc/.aidlc-readonly-latch` (Branch 0). Uncaught error → stderr + exit 1, no audit row. |
| `report --stage <slug> --result <approved\|completed\|complete\|done> [--user-input "<text>"] [--reason "<text>"]` | **Mutating** via spawned `aidlc-state.ts` (`gate-start … --recovered` backfill if needed, then `approve`, or `advance`, or `complete-workflow`). Refusals are relayed as `{"kind":"error","message":"Transition rejected by aidlc-state.ts approve for \"<slug>\": …"}` and the state tool has already appended `ERROR_LOGGED`. |
| `report --skeleton-stance <on\|off\|scope-dependent>` | Mutating: `aidlc-state.ts set-skeleton-stance` (state field only, no audit row). |
| `report --single --stage <slug> --result …` | Audit-only synthetic `STAGE_STARTED/STAGE_COMPLETED` pair; never touches `Current Stage`. |
| `park` | Mutating: `aidlc-state.ts park` → `WORKFLOW_PARKED`, inserts `Parked`/`Parked At Stage`; refuses under autonomous mode or Status Completed. Emits `{"kind":"parked","reason":"Workflow parked at \"<slug>\". Resume with /aidlc --resume.","stage":"<slug>"}`. |

`FORWARD_RESULTS = new Set(["approved", "completed", "complete", "done"])` (orchestrate:2387).
Reject/revise are **not** `report` outcomes.

### 3.2 `aidlc-utility.ts` (main dispatch at :3990-4076)

Read-only (stdout only, no audit, no state write):

- `help`, `version` (prints `aidlc 2.2.10`), `status [--intent <dir> --space <name>]`
  (human text only; no `--json`), `intent` (list; `--json`), `space` (list; `--json`),
  `codekb-path [--repo] [--json]`, `detect [--json]`, `detect-scope`, `resolve-env-scope`,
  `scope-table`.

Mutating:

| Verb | Writes |
|---|---|
| `intent <name>` | `aidlc/spaces/<space>/intents/active-intent` (cursor), plus best-effort re-stamp of `aidlc/.aidlc-sessions/<current-session>`. No audit row, no state write, no `HUMAN_TURN`. Prints `Active intent → <dirName> (space: <space>)`. Unknown/ambiguous name → `die()` → `ERROR_LOGGED` (if a state file resolves) + exit 1. |
| `space <name>` | `aidlc/active-space` cursor **and** rewrites the `file://aidlc/spaces/<X>/memory/**/*.md` entry in every `.kiro/agents/*.json` `resources` array (`repointHarnessIncludes`, `core/tools/aidlc-includes.ts:141-171`; structural JSON re-serialise, 2-space indent). No-op at `default`. Prints `Active space → <slug>` and `  repointed N harness include(s) → <slug>`. |
| `space-create <name>` | creates `aidlc/spaces/<slug>/{memory,intents,codekb,knowledge}` seeded from default `org.md`. |
| `intent-birth --scope <s> [--arguments "<desc>"] [--label "<2-3 words>"] [--depth] [--test-strategy] [--repos a,b]` | Under the workspace lock: mints UUIDv7, creates record dir, appends `intents.json`, sets cursor, writes `aidlc-state.md`, emits `WORKFLOW_STARTED`, `PHASE_STARTED`, `PHASE_SKIPPED…`, `STAGE_STARTED/COMPLETED` for the three init stages, `WORKSPACE_SCAFFOLDED`, `WORKSPACE_SCANNED`, `WORKSPACE_INITIALISED`, then phase hand-off rows. Default scope `poc` when omitted. |
| `scope-change --scope <s> [--depth] [--test-strategy]` | state rewrite + `SCOPE_CHANGED` (+ `DEPTH_CHANGED`/`TEST_STRATEGY_CHANGED`). |
| `config-change --depth/--test-strategy` | state fields + `DEPTH_CHANGED`/`TEST_STRATEGY_CHANGED`. |
| `recompose --skip <slugs> --add <slugs>` | flips EXECUTE/SKIP suffixes of pending ahead-of-cursor stages under lock; `RECOMPOSED`; refuses under autonomous mode, non-Running status, frozen/behind-cursor/skeleton flips. |
| `set-status --stage <slug>` | state fields + checkbox `[-]` (used by the todo-list sync hook). |
| `doctor` | see §1: `GUARDRAIL_LOADED` + `HEALTH_CHECKED` rows when audit exists; clears leaked tmp locks; exit 1 on any failed check. |
| `init`, `state-init` | deprecated aliases (`init` → intent-birth; `state-init` dies). |

### 3.3 `aidlc-state.ts` (dispatch at :226-292)

Read-only: `get <Field>`, `lookup <phase-of|next-stage|agent-for|number-of|stages-in-scope|first-in-phase|validate-stage|validate-phase>`, `resume` (prints
`{"resumed":true,"current_stage","phase","status","scope","active_agent","next_stage","gate_state","compaction_pending"}`), `count`.

Mutating (each under `withAuditLock`, audit-first): `set <Field=value>…` (special values
`NOW`, `+1`, `-1`; arbitrary fields), `set-skeleton-stance`, `checkbox <slug=state>…`,
`gate-start <slug> [--artifacts <csv>] [--recovered]` (`[-]→[?]`, `STAGE_AWAITING_APPROVAL`),
`approve <slug> [--user-input <text>]` (`[?]→[x]`, `GATE_APPROVED` + `STAGE_COMPLETED`, then
in-process `advance` or `complete-workflow`), `reject <slug> [--feedback <text>]`
(`[?]|[-]→[R]`, `GATE_REJECTED` + `STAGE_REVISING`, `Revision Count` +1; backfills the gate
row with `Recovered: true` if the stage was still `[-]`), `revise <slug>` (`[R]→[?]`,
`STAGE_AWAITING_APPROVAL` "Re-entering gate after revision"), `skip`, `advance`,
`finalize`, `complete-workflow`, `acknowledge-compaction`, `reuse-artifact`,
`practices-event`, `practices-promote`, `fork`, `merge`, `park`, `unpark`.

Guards on `approve` only (`aidlc-state.ts:1286-1340`), in order: `validateSlugInState(…,
"awaiting-approval")`; artifact guard `verifyStageArtifacts` (bypass
`AIDLC_SKIP_ARTIFACT_GUARD=1`); human-presence guard (§6). **`reject` and `revise` have no
presence guard.**

### 3.4 Other tools

- `aidlc-log.ts decision --stage <slug> --decision "<text>" [--options "<csv>"] [--rationale]` →
  `DECISION_RECORDED`; `aidlc-log.ts answer --stage <slug> --details "<exact choice>"` →
  `QUESTION_ANSWERED`, **presence-guarded** (`humanActedSinceLastAnswer`, same predicate as
  approve; `aidlc-log.ts:128-139`). Both refuse when no state file resolves.
- `aidlc-jump.ts resolve --scope <s> (--stage <x>|--phase <p>)` read-only (returns
  `target_slug`/`direction` forward|backward|redo); `aidlc-jump.ts execute --target <slug>
  --direction <d> --scope <s>` mutating (`STAGE_JUMPED`, `STAGE_SKIPPED` → `[S]`).
- `aidlc-audit.ts append <EVENT> --field k=v…` / `append-raw` / `audit-fork` / `audit-merge`
  — raw appenders; protocol says the conductor must not hand-call `append` for transitions.
- `aidlc-runtime.ts compile|read|summary [--json]|fragment-fork|fragment-merge`; `summary`
  is the read-only aggregate the session skills use.
- `aidlc-graph.ts compile` rewrites `.kiro/tools/data/stage-graph.json` (framework dir).
- `aidlc-learnings.ts surface|persist`, `aidlc-swarm.ts prepare|check|finalize`,
  `aidlc-bolt.ts`, `aidlc-worktree.ts` — Construction-time mutators.

---

## 4. The `next` decision rule and directive contract

### 4.1 Branch order in `handleNext` (`aidlc-orchestrate.ts:1185-1760`)

0. **Roll-forward guard (Kiro):** a truly bare `next` (no flags) while
   `aidlc/.aidlc-readonly-latch`.turn === `aidlc/.aidlc-turn-counter` → `{"kind":"done","reason":"The read-only/navigation command (<label>) already ran this turn … STOP."}`. Studio probes of `next` immediately after a terminal command in the same Kiro turn will see this spurious `done`.
1. Read-only flag (`--status|--help|--doctor|--version`, or sole `help`/`-h`) → `print`:
   `` Run `bun .kiro/tools/aidlc-utility.ts <sub>`, print its output verbatim, then stop. This is a read-only utility, NOT workflow work: do NOT run `next` and do NOT advance, resume, or run any workflow stage. ``
1b. Leading workspace verb `space|space-create|intent [<name>]` → `print`:
   `` Run `bun .kiro/tools/aidlc-utility.ts <verb> [<name>]`, print its output verbatim, then stop. ``
2. `--stage` + `--phase` → `error` "Cannot use --stage and --phase together. Use one or the other."
2.5. State has `Parked` and `Parked At Stage === Current Stage`, no `--resume/--stage/--phase` → `parked`.
2.6. `--resume` over parked → `print` naming `` bun .kiro/tools/aidlc-state.ts unpark `` then `next --resume`.
3b/4. `--scope` validation; `AWS_AIDLC_DEFAULT_SCOPE` validation (error directive on invalid).
4c. Leading `compose` verb / `--new-scope` / `--report <path>` → composer dispatch `print` (in-flight variant instructs writing `aidlc/.aidlc-compose-pending` before the gate and running `recompose` on approve).
4a. `--new-intent` → birth `print` for the explicit `--scope` (else resolved scope).
4b. `--single --stage <slug>` → one `run-stage`, never pivots `Current Stage`.
5. State present, `--scope` differs and valid → `print` `` Run `bun .kiro/tools/aidlc-utility.ts scope-change --scope <s> [--depth] [--test-strategy]` to change scope, then print its output verbatim and stop. ``; `--depth/--test-strategy` alone → `config-change` print.
6. `--resume` with state → `ask`: `An existing workflow was found (currently at "<slug>"). How would you like to proceed? Resume from last checkpoint, redo the current stage, jump to a stage, or start fresh.`
7. `--stage`/`--phase` with state → `print` `` Run `bun .kiro/tools/aidlc-jump.ts execute --target <slug> --direction <forward|backward|redo> --scope <s>` to perform the jump, then re-run `next` to continue from the jump target. `` (direction from `aidlc-jump.ts resolve`).
7b. No state, positional is a valid scope name → birth print (or the intent-pick `ask` when records exist but no cursor).
8. No state, freeform prose → `ask`: keyword hit `Starting a "<scope>" workflow for: "<text>". Confirm to proceed, name a different scope, or say "compose" for a tailored plan.`; else `No stock scope clearly fits: "<text>". I can compose a tailored plan for this task (recommended: reply "compose"), or you can name a scope directly (e.g. bugfix, feature, poc; see /aidlc --help for all).`
9a. No state, explicit `--scope` → birth print:
   `` Run `bun .kiro/tools/aidlc-utility.ts intent-birth --scope <s> --arguments "<desc>" --label "<2-3 word kebab essence>" [--depth] [--test-strategy]` to start the workflow, then re-run `next` to continue. Replace `--label` with a 2-3 word kebab essence … ``
9b. No state, nothing named → `error` `No workflow state found (no active intent). Start one by describing what to build (/aidlc "build the auth service") or by naming a scope (/aidlc --scope <scope>).`
10. Happy path: if `Current Stage` row is `[ ]|[-]|[?]|[R]` (or missing) → `run-stage` for it (or `invoke-swarm` under autonomous code-generation); if `[x]|[S]` → next in-scope stage or `done` (`Workflow complete — no in-scope stage remains after <slug> (scope: <s>).`).

Intent-pick `ask` (records exist, no cursor — the fresh-clone case): `This workspace already
has N intent(s)[ in space "<sp>"] but no active intent is selected (the active-intent cursor is
per-user and not cloned). Pick one to work on with `/aidlc intent <slug>`: `a`, `b`. Selecting
an intent sets the cursor; re-run `next` afterward to continue its workflow.`

### 4.2 `/aidlc` argument grammar the engine parses (`parseNextFlags`, orchestrate:276-379)

`--status --help --doctor --version` (anywhere) · sole `help`/`-h` · leading verbs `space
[<name>]`, `space-create <name>`, `intent [<name>]`, `compose [<text>]` · `--resume` ·
`--single` · `--new-intent` · `--scope <s>` · `--stage <slug|#>` · `--phase <name|#>` ·
`--depth <minimal|standard|comprehensive>` · `--test-strategy <…>` · `--new-scope` ·
`--report <path>` · any other non-`--` tokens → freeform `intent` text.
`--init`/`--force` are retired (P4). The Kiro `verb-intercept` seam uses the identical
classifier `classifyTerminalCommand` (lib:347) so engine and hook cannot disagree about what
is terminal.

### 4.3 Directive JSON shapes (`core/tools/aidlc-directive.ts`)

Emitted today: `run-stage`, `invoke-swarm`, `ask`, `print`, `error`, `done`, `parked`
(`dispatch-subagent`/`present-gate` are documented placeholders, never emitted).

`run-stage` keys: `kind, stage, phase, lead_agent, support_agents[], mode
("inline"|"subagent"|"agent-team"), gate (true|false|"unresolved"), memory_path, consumes[],
produces[], rules_in_context[], sensors_applicable[], stage_file, reviewer?,
reviewer_max_iterations? (default 2), conductor_persona? (only on the first substantive
run-stage of a workflow — `isFirstRunStageOfWorkflow`), next_stage? (display name of the next
in-scope stage, or null = final), unit?, consumes_absent?: [{path, expected}]`.
`computeGate` (orchestrate:1093): initialization → `false`; walking-skeleton stage with no
`Skeleton Stance` → `"unresolved"`; everything else → `true`.
`ask` = `{kind:"ask", question}`; `print` = `{kind:"print", message}`; `error` =
`{kind:"error", message}`; `done` = `{kind:"done", reason}`; `parked` = `{kind:"parked",
reason, stage}`; `invoke-swarm` = `{kind, units[], repo?}`.

---

## 5. The human gate protocol: exact wire text

### 5.1 What the conductor renders on Kiro (`harness/kiro/skills/aidlc/question-rendering.md`)

Kiro CLI has no structured-question widget. Every ` ```question ` spec renders as numbered
prose and the human replies with a number or free text. The canonical approval gate
(`core/aidlc-common/protocols/stage-protocol.md:40-49`):

```question
prompt: "[Stage Name] complete. How would you like to proceed?"
header: Approval
multiSelect: false
options:
  - label: Approve
    description: Continue to [next stage]
  - label: Request Changes
    description: Provide revision feedback
```

renders as:

```
**Approval** — [Stage Name] complete. How would you like to proceed?

1. **Approve** — Continue to [next stage]
2. **Request Changes** — Provide revision feedback
3. **Other** — describe what you want instead

Reply with a number (or just tell me).
```

Rules that matter to Studio: `[next stage]` is `directive.next_stage` verbatim
("Complete workflow" when null); a recommended option is listed first with "(Recommended)";
"Other" is always appended as the last number; the conductor maps the reply back to the exact
option `label` and records that label verbatim ("never summarize User Input"). Variants:
Ideation/Inception may add a third option `Add [Skipped Stage]`; after 3 rejection cycles a
third option `Accept as-is` appears (protocol §1 "Revision loop escape hatch"); Construction
Bolt failures use `Retry` / `Skip` / `Abort`; the ladder prompt uses `Continue autonomously`
/ `Gate every Bolt`.

### 5.2 What the conductor runs after the human answers (protocol §2 Part 0, lines 157-164)

- **Approve** → `` bun .kiro/tools/aidlc-orchestrate.ts report --stage <slug> --result approved --user-input "<exact choice>" `` → engine opens a missing gate row if needed, then `approve` → `GATE_APPROVED` (with `User Input`) + `STAGE_COMPLETED` + auto-advance (`STAGE_STARTED` for the next stage, or `PHASE_COMPLETED`/`PHASE_VERIFIED`/`WORKFLOW_COMPLETED` on the final stage).
- **Request Changes** → `` bun .kiro/tools/aidlc-state.ts reject <slug> --feedback "<text>" `` → `[?]→[R]`, `GATE_REJECTED` + `STAGE_REVISING`, `Revision Count` +1. The conductor then runs the Keep/Modify/Redo loop **inside the stage** (conductor.md "Intra-stage control flow"), re-runs work, calls `` aidlc-state.ts revise <slug> `` (`[R]→[?]`) and re-presents the gate. The reject path never goes through `report`.
- **Accept as-is** (after 3 cycles) → same as Approve with `--user-input "Accept as-is after N cycles"`.
- Optional before presenting: `` aidlc-state.ts gate-start <slug> `` (`[-]→[?]`) so `--status` reads `Awaiting your approval on <Stage>`.

**There is no engine-supplied canonical response token.** The engine never sees the human's
reply; the conductor (LLM) interprets the chat reply and picks the CLI verb. The only strings
with protocol status are the option labels `Approve` / `Request Changes` / `Accept as-is` (and
the numbered position, which is render-time and unstable: a recommended option or an extra
option changes the numbering). For FR-GATE-002, Studio's wire text must therefore be a
protocol constant — the exact label text `Approve` (or `Request Changes` followed by the
feedback) — sent as a chat prompt to the canonical session, never a bare digit.

### 5.3 What the engine actually enforces at approve time

`report --result approved` → `aidlc-state.ts approve <slug> [--user-input …]` requires, in
order: the row is `[?]` (or `[-]` when `--stage` is explicit → gate-start backfill first);
artifacts exist on disk; and `humanActedSinceGate(pd)` (§6). `--user-input` is free text and
recorded verbatim in `GATE_APPROVED`'s `User Input` field. The refusal text Studio may see
relayed inside `{"kind":"error"}`:

`Refusing to approve "<slug>": a real human has not acted at this gate since it opened. The
approval gate requires a typed human turn before it can commit. Acknowledge the gate as a
human, then approve. (autonomous Construction is exempt)` (`aidlc-state.ts:1330-1336`).

Success evidence Studio should require (FR-GATE-007): a new `GATE_APPROVED` block with
`**Stage**: <slug>` in the audit shard **and** the state row flipping `[?]→[x]` with
`Current Stage` moving. An `ERROR_LOGGED` row is audit movement without success.

### 5.4 Structured questions and `*-questions.md` (protocol §3)

Step 1 writes `<record>/<phase>/<stage>/<stage>-questions.md` with options A-E + `X. Other
(please specify)` and blank `[Answer]:` tags. Step 2 asks the interaction mode (`Guide me` /
`I'll edit the file` / `Chat`). Answers are written back into the file (`[Answer]: A, B` for
multi-select). Around each question the conductor runs `aidlc-log.ts decision …` before and
`aidlc-log.ts answer --stage <slug> --details "<exact choice>"` after; `answer` is
presence-guarded. Studio must never edit the questions file (FR-Q-005); the durable,
harness-neutral evidence is that file plus `DECISION_RECORDED`/`QUESTION_ANSWERED` rows.
Pending-question detection regex used by the Stop hook (`aidlc-stop.ts:420`):
`/\[Answer\]:[ \t]*_*[ \t]*$/m` over any `*-questions.md` in the current stage dir.

On Kiro the question content only exists as assistant prose (`agent_message_chunk` in ACP —
`tests/harness/kiro-acp-drive.ts:40-44`), so S1's "structured live payload" is **not**
available from the AI-DLC side; Studio's sanctioned degraded mode (FR-Q-007) is the expected
outcome unless KiroCrew exposes something richer.

---

## 6. Human-presence guard (the anti-forgery mechanism)

### 6.1 The ledger predicate (`core/tools/aidlc-lib.ts:1355-1423`)

```ts
const GATE_RESOLUTION_EVENTS = new Set(["GATE_APPROVED", "GATE_REJECTED", "QUESTION_ANSWERED"]);
export function humanActedSinceGate(projectDir: string): boolean {
  const audit = readAllAuditShards(projectDir);
  if (audit.length === 0) return true; // no ledger → fail open
  … collect HUMAN_TURN and resolution events across all shards,
  … sort by (Timestamp, then buffer position),
  return lastHuman > lastResolution && lastHuman !== -1;
}
export function humanActedSinceLastAnswer(projectDir) { return humanActedSinceGate(projectDir); }
```

Semantics: a `HUMAN_TURN` must appear **after the most recent gate resolution of any stage**
(workflow-global boundary). One human prompt therefore authorises at most one
approve/answer; a cascade of two gates inside one turn is refused at the second. An empty
ledger fails open. Ordering is chronological across shards (second-clone shards cannot mask a
fresh turn).

Carve-outs, checked first in both `handleApprove` and `handleAnswer`: `isAutonomousMode`
(state field `Construction Autonomy Mode: autonomous`) and `humanPresenceGuardDisabled()`
(`process.env.AIDLC_SKIP_HUMAN_PRESENCE_GUARD === "1"`, lib:2086).

Not guarded: `reject`, `revise`, `gate-start`, `advance`, `skip`, `set`, `checkbox`,
`park`, `recompose`, `scope-change`, `intent-birth`, cursor verbs.

### 6.2 Where `HUMAN_TURN` is minted

| Harness | Trigger | Code |
|---|---|---|
| Kiro CLI | `userPromptSubmit` hook → `bun .kiro/hooks/aidlc-kiro-adapter.ts verb-intercept` | `appendAuditEntry("HUMAN_TURN", {}, cwd)` iff `existsSync(stateFilePath(cwd))` (`adapter:129-134`). Fires for **every** prompt, `/aidlc` or not, with any content. `cwd` is `kiro.cwd ?? process.cwd()`. |
| Kiro IDE | `.kiro.hook` `promptSubmit` → adapter `mint` | same predicate (`harness/kiro-ide/hooks/aidlc-kiro-adapter.ts:84-93`) |
| Claude Code | `UserPromptSubmit` + PostToolUse `AskUserQuestion` → `core/hooks/aidlc-mint-presence.ts` | same |

Nothing else mints it. Consequences for Studio (P-03, FR-SES-002/003, S12):

- Any transport that reaches the `aidlc` agent through Kiro's prompt path mints presence,
  whatever the payload says. A machine "Keep moving" prompt through that path **is** a forged
  human turn from the engine's perspective.
- Conversely, a transport that bypasses `userPromptSubmit` leaves the conductor unable to
  approve (refusal above) and, while a gate is open, unable to run **any** shell tool (§6.3).
  The engine gives no third path: there is no machine-lane API that advances past a gate
  without a `HUMAN_TURN`, which is the property the PRD relies on.
- Studio must never set `AIDLC_SKIP_HUMAN_PRESENCE_GUARD` (or `AIDLC_SKIP_ARTIFACT_GUARD`) in
  any environment it spawns Kiro or the tools in.

### 6.3 The Kiro `preToolUse` floor (`adapter:192-267`, matcher `execute_bash`)

Two exit-2 (hard block) branches, both fail-open on read errors:

1. Roll-forward backstop: a truly bare `aidlc-orchestrate.ts next` (no `compose`, none of
   `--stage --phase --scope --resume --depth --test-strategy --single --init --force --new-scope
   --report`, `classifyTerminalCommand(...) === null`) while `latch.turn === counter` → stderr
   "read-only/navigation command already handled this turn by the deterministic harness — do
   not advance the workflow…" exit 2.
2. Presence floor: skip if autonomous, skip if `AIDLC_SKIP_HUMAN_PRESENCE_GUARD=1`, skip if
   `!hasOpenGate(state)`; else if `!humanActedSinceGate(cwd)` → stderr "an approval gate is
   open and no human has acted since it opened: refusing the tool call. A real human must
   respond at the gate. End the turn." exit 2. Applies to **every** `execute_bash` call, even
   read-only status commands.

Kiro's verified contract: only exit 2 blocks; exit 1 or a JSON `{"decision":…}` on stdout
does not (adapter comment lines 186-188).

### 6.4 Turn counter and latch (roll-forward guard, not presence)

`verb-intercept` always does `mkdirSync(<cwd>/aidlc)` and writes `aidlc/.aidlc-turn-counter`
(previous+1, or 1) — even in a project with no workflow state. If the recovered args classify
as terminal, it runs `bun .kiro/tools/aidlc-utility.ts <sub> [<arg>]` off-band, writes
`aidlc/.aidlc-readonly-latch`, and prints to stdout (Kiro's context channel):
`SYSTEM (deterministic harness dispatch): The command \`/aidlc <typed>\` has ALREADY been run
by the harness — it is a read-only/navigation command that carries NO workflow work. Its
verbatim output is below. Your ONLY action this turn: relay that output to the user, then
STOP. Do NOT run \`aidlc-orchestrate.ts next\`. …\n\n--- OUTPUT ---\n<out>\n--- END OUTPUT ---`.
Args are recovered by `/aidlc-orchestrate\.ts next ([^`\n]*)`/` from the **expanded skill
body** Kiro passes as `prompt` (adapter:90-97); a plain (non-`/aidlc`) prompt yields no args →
no terminal dispatch, but the counter bump and `HUMAN_TURN` mint still happen.

---

## 7. Hooks on Kiro CLI: wiring and side effects

### 7.1 `dist/kiro/.kiro/agents/aidlc.json` (verbatim contract)

- `name: "aidlc"`, `model: "claude-opus-4.8"`, `prompt` = the forwarding-rules paragraph
  (engine is the only routing authority; first `next` must carry `$ARGUMENTS`; act on named
  commands in `print` messages immediately).
- `tools`: `fs_read, fs_write, execute_bash, todo_list, thinking, subagent`;
  `allowedTools` (auto-approved): `fs_read, thinking, todo_list`.
- `toolsSettings.execute_bash.allowedCommands`: `"bun \\.kiro/tools/.*"`,
  `"bun \\$\\{?KIRO_PROJECT_DIR\\}?/\\.kiro/tools/.*"`, `"date -u .*"`; `deniedCommands`:
  `"rm -rf /.*"`, `"git push .*"`. Everything else prompts (auto-approved under
  `--no-interactive`, per `AGENTS.md`).
- `toolsSettings.fs_write.allowedPaths`: `aidlc/spaces/**`, `.kiro/sensors/**`,
  `aidlc/.aidlc-compose-pending`.
- `toolsSettings.subagent.trustedAgents`: `aidlc-developer-agent, aidlc-architect-agent,
  aidlc-architecture-reviewer-agent, aidlc-product-lead-agent, aidlc-composer-agent`.
- `resources`: `skill://.kiro/skills/*/SKILL.md`, `file://aidlc/spaces/default/memory/**/*.md`
  (rewritten by `space <name>`), `file://AGENTS.md`.
- `hooks`:

| Kiro event | matcher | command | timeout_ms |
|---|---|---|---|
| `agentSpawn` | — | `bun .kiro/hooks/aidlc-kiro-adapter.ts session-start` | — |
| `userPromptSubmit` | — | `… verb-intercept` | 30000 |
| `preToolUse` | `execute_bash` | `… pretool-block` | 15000 |
| `postToolUse` | `fs_write` | `… audit-and-sensors` (audit-logger then sensor-fire) | 120000 |
| `postToolUse` | `execute_bash` | `… runtime-compile` | 45000 |
| `postToolUse` | `todo_list` | `… state-sync` (todo `create` with `[slug]` suffix → `set-status`) | — |
| `postToolUse` | `subagent` | `… log-subagent` (`SUBAGENT_COMPLETED`) | — |
| `stop` | — | `… stop` | 30000 |

No MCP servers ship for Kiro. `dist/kiro/.kiro/settings/cli.json` sets
`"chat.defaultAgent": "aidlc"` and `chat.modelDefaults["claude-opus-4.8"].output_config.effort = "xhigh"`;
the workspace default overrides a user's global default agent. `dist/kiro/AGENTS.md` lands at
the project root and must be merged if the repo already has one. `dist/kiro/.gitignore`
contributes the ignore rules listed in §2.1.

Live-captured Kiro payload shapes (`tests/fixtures/kiro-hook-payloads/payloads.json`, kiro-cli
2.6.1): `userPromptSubmit` = `{hook_event_name, cwd, prompt}` (no `session_id`);
`agentSpawn` = `{hook_event_name, cwd, prompt}` (no `session_id`); `preToolUse`/`postToolUse`
= `{hook_event_name, cwd, session_id, tool_name ("shell"|"write"|"todo_list"|"subagent"),
tool_input, tool_response?}`; `stop` = `{hook_event_name, cwd, assistant_response}`.

### 7.2 Session-start (`core/hooks/aidlc-session-start.ts`)

On every `agentSpawn`: `repointHarnessIncludes(projectDir, activeSpace)` (idempotent), then if
`aidlc-state.md` exists: heartbeat, `writeCurrentSessionId` (only when `session_id` present —
the captured `agentSpawn` payload has none), `SESSION_STARTED` audit row (`Source: startup`),
stamp `aidlc/.aidlc-sessions/<sid>` with the active intent UUID, and print the
`AIDLC WORKFLOW ACTIVE …` context (scope/phase/stage/status + forwarding-loop discipline)
as plain stdout. Every new Kiro session over an active intent therefore appends one audit row.

### 7.3 Stop hook (`core/hooks/aidlc-stop.ts`, `adapter` target `stop`)

Order of decisions on each turn end (all fail-open):

1. No `aidlc-state.md` → allow. Heartbeat `.aidlc-hooks-health/stop.last` written first.
2. Spawn `bun <repo>/.kiro/tools/aidlc-orchestrate.ts next --project-dir <repo>` (10 s
   timeout); unparseable → allow.
3. `done` → reset guard, allow. `parked` → allow unless autonomous. `ask` → allow.
4. `isHumanWaitStop`: current stage row is `[?]` or `[R]` → allow (the "[?]/[R] release").
5. `isPendingQuestionStop`: current row `[-]`, non-autonomous, an unanswered `[Answer]:` in
   the stage's `*-questions.md` → allow.
6. `isPendingComposeStop`: fresh `aidlc/.aidlc-compose-pending` (≤24h), non-autonomous → allow.
7. `isConversationalStop`: inert on Kiro (no `transcript_path`).
8. `decideBlock`: progress signature `${Current Stage}::${audit shard line count}`; consecutive
   no-progress blocks capped at `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` else 2 (interactive) / 8
   (autonomous). Within cap → stdout `{"decision":"block","reason":"The AIDLC workflow has a
   pending step (a <kind> directive for \"<stage>\"). You haven't finished the forwarding loop
   yet. Run `bun .kiro/tools/aidlc-orchestrate.ts next`, act on the directive it emits, then
   run `aidlc-orchestrate report --stage <stage> --result <outcome>` … If instead you mean to
   pause … run `bun .kiro/tools/aidlc-orchestrate.ts park` …"}`.

Every stop-hook fire writes `.aidlc-stop-hook/block-count.json` and the heartbeat under the
record dir. Under `kiro-cli chat --no-interactive` the stop hook does not fire
(`docs/guide/harnesses/kiro-cli.md:70`; `SKILL.md:84`).

### 7.4 Other core hooks on Kiro

`aidlc-audit-logger.ts` logs `ARTIFACT_CREATED/UPDATED` only for writes under the active
record root (`docsRoot`). `aidlc-sensor-fire.ts` runs declared sensors (advisory,
`AIDLC_SENSOR_TIMEOUT_MS`, default 90 s). `aidlc-runtime-compile.ts` recompiles
`runtime-graph.json` after `report`/transition commands. `aidlc-validate-state.ts`
(PreCompact) and `aidlc-session-end.ts` are shipped but **not wired** on Kiro CLI (no such
events). Core hooks resolve `projectDir` from their own path (`<repo>/.kiro/hooks`); the
adapter's `verb-intercept`/`pretool-block` use `kiro.cwd` — Studio must launch Kiro with
`cwd = <repo>` or presence/latch files land in the wrong tree.

### 7.5 Kiro IDE harness (for completeness)

`harness/kiro-ide/` ships the same core plus `.kiro.hook` files (`promptSubmit` → `mint`,
`preToolUse` → `block`, `agentStop` → `stop`, `promptSubmit` → `session-start`, …) and an
`aidlc.json` **without** a `hooks` field. Its adapter has no `verb-intercept` (no turn
counter/latch) and races stdin against a 2 s timeout.

---

## 8. Switching space/intent (PRD S13, FR-SES-006)

The only engine-supported cursor operations are the two utility verbs. They are plain CLI
processes: no Kiro session, no hooks, no `HUMAN_TURN`, no audit row on success.

```
bun <repo>/.kiro/tools/aidlc-utility.ts space  <slug>     --project-dir <repo>
bun <repo>/.kiro/tools/aidlc-utility.ts intent <dirName>  --project-dir <repo>
bun <repo>/.kiro/tools/aidlc-utility.ts space  --json     --project-dir <repo>
bun <repo>/.kiro/tools/aidlc-utility.ts intent --json     --project-dir <repo>
```

Facts an implementer needs (`aidlc-utility.ts:2907-3015`, lib:900-925):

- `intent <name>` matches `dirName` exactly, else a **unique** `slug`; ambiguous → `die`
  (`Ambiguous intent "<n>" in space "<sp>" (N match). Use the full record-dir name: …`);
  unknown → `die` (`Unknown intent "<n>" in space "<sp>". This command only switches between
  existing intents …`). Because `die` → `emitError` → `ERROR_LOGGED` in whichever intent
  currently resolves, Studio should validate against `intent --json` **before** switching.
- `intent` has no `--space` flag; it operates on the active space. To target another space run
  `space <slug>` first (which also rewrites `.kiro/agents/*.json` — a harness-dir mutation the
  receipt installer must expect; no-op when the target is `default`).
- `setActiveIntentCursor` uses `writeFileSync` (not atomic rename) and **swallows failures**;
  the tool still prints `Active intent → …`. Read-back is mandatory.
- `intent --json` → `{"active": "<dirName>|null", "space": "<sp>", "intents": [{"uuid",
  "slug", "status", "repos": [], "dirName", "active"}]}`; `space --json` → `{"active",
  "spaces": [{"name","active"}]}`.
- Read-back for FR-SES-006 while holding the repo lease: (1) `aidlc/active-space` bytes,
  (2) `aidlc/spaces/<space>/intents/active-intent` bytes, (3) confirm the named dir contains
  `aidlc-state.md` (otherwise `activeIntent()` will silently fall back to the lone intent or
  `null`), (4) state path = `<record>/aidlc-state.md`, (5) state hash = Studio's own
  `sha256(state bytes)` (see §9), (6) `intent --json`.active equals the cursor.
- In-session `/aidlc intent <name>` is dispatched off-band by `verb-intercept`, but as a
  prompt it mints `HUMAN_TURN` — never use the chat path for machine cursor switching.
- Cross-process consistency: the cursor is repo-shared and read at each `next`/`report`; two
  concurrent conductor sessions on different intents in one repo will each resolve whatever
  the cursor says at spawn time. Per-intent `withAuditLock` (tmpdir
  `.aidlc-audit-<md5(projectDir\0space\0intent)>.lock`, stale after 10 min) serialises
  state/audit writes but not artifact generation — PRD §7.8/§7.9 hold.

---

## 9. "State hash"

The engine exposes no state-hash API. `sha256` of the state-file bytes appears only inside
`aidlc-state.ts fork/merge` audit rows (`Source state hash` / `Target state hash`,
`aidlc-state.ts:177,2268,2446`) and `Source Audit Hash` in `AUDIT_FORKED`. The Stop hook's
progress signature is `"<Current Stage>::<line count of this clone's audit shard>"`
(`aidlc-stop.ts:211-223`). For FR-SES-006 Studio should compute `sha256(aidlc-state.md)`
itself (the prototype's `backend/routes.py:_sha256_file` already does) and optionally pair it
with the concatenated `audit/*.md` length; there is nothing engine-native to compare against.

---

## 10. Environment variables the engine reads (Studio must not set any of them)

From `grep process.env` over `core/`, `harness/`, `scripts/`:

| Variable | Read in | Effect |
|---|---|---|
| `AIDLC_SKIP_HUMAN_PRESENCE_GUARD` | lib:2087 (`approve`, `answer`, Kiro/IDE preToolUse floors) | `"1"` disables the human-presence gate. **Security bypass.** |
| `AIDLC_SKIP_ARTIFACT_GUARD` | aidlc-state.ts:556 | `"1"` disables the approve-time artifact-existence check. |
| `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` | aidlc-stop.ts:125 | overrides the no-progress block cap (default 2 / 8). |
| `AIDLC_LOCK_STALE_MS`, `AIDLC_LOCK_UNSTAMPED_GRACE_MS` | lib:2315, 2419 | lock reaper thresholds (default 10 min). |
| `AIDLC_AUDIT_LOCK_RETRIES`, `AIDLC_AUDIT_LOCK_RETRY_MS` | aidlc-audit.ts:606-610 | audit-merge lock retry tuning. |
| `AIDLC_SENSOR_TIMEOUT_MS` | aidlc-sensor-fire.ts:47 | sensor timeout (90 s). |
| `AIDLC_HARNESS_DIR`, `AIDLC_RULES_SUBDIR` | lib:147,193 | pretend to be another harness / rules dir (test seams). |
| `CLAUDE_PROJECT_DIR` | lib:210,246 | overrides project-dir resolution for every tool and hook. |
| `AIDLC_STAGES_DIR`, `AIDLC_STAGE_GRAPH`, `AIDLC_RULES_DIR`, `AIDLC_FRAMEWORK_TEMPLATES_DIR`, `AIDLC_MEMORY_SEED_DIR`, `AIDLC_SENSORS_DIR`, `AIDLC_SCOPE_GRID`, `AIDLC_SCOPE_MAPPING`, `AIDLC_SCOPES_DIR`, `AIDLC_TEMPLATES_DIR`, `AIDLC_SENSOR_SCRIPT_DIR`, `AIDLC_SKILL_MD_PATH` | aidlc-graph.ts, lib, learnings, runner-gen, sensor, utility | redirect framework data/graph/scopes/rules sources (fixture seams). |
| `AIDLC_GRAPH_RESOLVE`, `AIDLC_PLAN_PATH`, `AIDLC_EXPORT_FIXTURE` | aidlc-graph.ts:1818-1870 | graph resolve/export test seams. |
| `AWS_AIDLC_DEFAULT_SCOPE` | orchestrate:526, utility:596,3977 | default scope at birth (validated; invalid → error directive). The only user-facing one. |
| `AIDLC_USE_SWARM` | conductor-side only (SKILL.md, `aidlc-swarm.ts` comments) | loud no-op on Kiro. |
| `EDITOR`, `HOME` | worktree.ts:89, utility:402 | incidental. |

---

## 11. Scopes, sensors, and other data files Studio may read

- Scopes: `.kiro/scopes/aidlc-<name>.md` with frontmatter `name`, `depth`
  (Minimal|Standard|Comprehensive), optional `testStrategy`, `keywords: [...]`, `description`.
  Nine stock scopes (bugfix 7/32, enterprise 32, feature 32, infra 13, mvp 22, poc 8,
  refactor 8, security-patch 10, workshop 25 with `testStrategy: Minimal`); EXECUTE/SKIP per
  stage lives in `.kiro/tools/data/scope-grid.json`; `aidlc-utility.ts scope-table` regenerates
  the summary. Composed scopes are appended by the composer subagent (two files required).
- Sensors: `.kiro/sensors/aidlc-<id>.md` frontmatter `id, kind, command, default_severity,
  description, category, matches (glob), input_schema, output_schema, timeout_seconds`; bound
  to stages via the stage file's `sensors:` list; results land as `SENSOR_FIRED/PASSED/FAILED`
  rows and detail files under `.aidlc-sensors/<stage>/`.
- Stage graph: `.kiro/tools/data/stage-graph.json` (32 stages; runner skills are generated
  from it). Conductor persona: `.kiro/aidlc-common/conductor.md`, delivered in-band as
  `conductor_persona` on the first substantive `run-stage`.
- Session skills (read-only, no audit): `/aidlc-session-cost`, `/aidlc-replay`,
  `/aidlc-outcomes-pack` (the last writes `OUTCOMES.md`).

---

## 12. Implications for Studio (condensed)

1. Human lane: the only way to approve is a real Kiro prompt (mints `HUMAN_TURN`) whose text
   the conductor interprets, followed by the conductor's own `report --result approved
   --user-input "<label>"`. Send the exact label `Approve` / `Request Changes: <feedback>`;
   verify by watching for `GATE_APPROVED`/`GATE_REJECTED` + checkbox change, not HTTP status.
2. Machine lane: no engine-native way to advance past `[?]`/`[R]` or to answer a question
   without a `HUMAN_TURN`; any transport that fires `userPromptSubmit` forges presence.
   S12's acceptance test is exactly "shard byte-identical except for what the conductor's own
   tools append, and no new `HUMAN_TURN`".
3. Cursor lane: `aidlc-utility.ts intent|space` under `--project-dir`, read back both cursor
   files and `intent --json`, hash the state file yourself.
4. Byte-neutral claims (FR-ADV-007) must exclude `.aidlc-hooks-health/`, `.aidlc-stop-hook/`,
   `aidlc/.aidlc-turn-counter`, `aidlc/.aidlc-sessions/` — any Kiro session over the repo
   writes them; the Advisor must run outside the repo, as the PRD already says.
5. Audit is also mutated by failures (`ERROR_LOGGED`), `--doctor` (`HEALTH_CHECKED`,
   `GUARDRAIL_LOADED`), and every session start (`SESSION_STARTED`); Studio's "movement"
   detector must key on specific event types.
6. Install receipts must own `.kiro/agents/*.json` as **mutable** (space switch rewrites
   `resources`) and treat the project-root `AGENTS.md` and `.gitignore` as merge targets.

---

## 13. Open questions not resolvable from code

1. Whether KiroCrew's ACP/App prompt path always fires `userPromptSubmit` (PRD §7.5 says one
   human ACP prompt did) and whether any KiroCrew machine path (AutoNudge, cron) can reach the
   `aidlc` agent without it — the engine offers no observable other than the shard.
2. Whether `agentSpawn` ever carries `session_id` on current kiro-cli (the 2.6.1 capture does
   not), which decides whether `.aidlc-sessions/.current-session` is written on Kiro CLI at all.
3. Whether `kiro-cli chat --no-interactive` still fires `userPromptSubmit`/`preToolUse`
   (docs only state the stop hook does not fire).
4. Kiro's `cwd` for hook payloads when the chat is started in a subdirectory of the repo (the
   adapter mints into `<cwd>/aidlc`).
5. Whether the KiroCrew-observed ACP `session/prompt` for the `aidlc` agent renders the gate
   text identically to the TUI (the reference tests only assert tool outputs, never the prose),
   which decides whether Studio can rely on the `1.`/`2.` numbering at all (it should not —
   see §5.2).

---

## Critic addendum (2026-09-04, completeness pass) — 2.6.2 corrections

This document was written from `/Users/ychchen/warren_ws/aidlc-workflows` at **2.2.10**. The engine Studio must target is **2.6.2** (`/Users/ychchen/warren_ws/aidlc/wt-v2-triage`, git `4569754e`; identical to `/Users/ychchen/warren_ws/DevDelta/.kiro/tools` except `scope-grid.json`, see doc 04 §0). The following §1 rows and sections are **wrong for 2.6.2**; the PRD's §7 "verified basis" turns out to be correct against 2.6.2.

### A1. Verbs — `continue` exists (PRD §7.2 is correct)

`core/tools/aidlc-orchestrate.ts:5781-5797 main()` switches on `next | continue | report | park` (`Unknown subcommand: … Valid: next, continue, report, park`). `handleContinue(args, projectDir)` (:5682) takes exactly one arg, the `continue_token` from a `load-steering` directive, `decodeSteeringToken`s it (HMAC key = `.aidlc-steering-token-key`, doc 04 §3.10), refuses if `payload.h !== sha256(live state)` or the stage route hash changed, then re-emits the `run-stage` directive from disk. It **mutates nothing** and cannot pass `[?]`/`[R]`; it is the rule-chunk resume the conductor runs "immediately … do not call `report`" (`harness/kiro/skills/aidlc/SKILL.md:74`). Not a machine lane for S12.

### A2. `approve` requires `--user-input` (PRD §7.3 is correct)

`core/tools/aidlc-state.ts:2434-2441`: `Refusing to approve "<slug>": --user-input must contain the human's exact approval choice.`; `:2454` refuses cancellation boilerplate (`NON_ANSWER_RE`, doc 04 §5.4). `report --result approved` therefore needs `--user-input "<exact label>"`; `GATE_APPROVED.User Input` is authoritative evidence of the wire text the conductor recorded.

### A3. Human-turn marker file exists (PRD §7.4 is correct)

`harness/kiro/hooks/aidlc-kiro-adapter.ts:233-238` inside `verb-intercept`: `if (existsSync(stateFilePath(cwd))) { appendAuditEntry("HUMAN_TURN", {}, cwd); markHumanTurn(cwd); }` — one seam writes both the ledger row and `<record>/.aidlc-human-turn` (mtime-semantic; doc 04 §3.4). The turn counter bump (`aidlc/.aidlc-turn-counter`, :209-217) is a separate try block. Studio's FR-SES-010 "human-presence evidence" set = `HUMAN_TURN` rows + `.aidlc-human-turn` mtime + `.aidlc-turn-counter` value.

### A4. Request Changes goes through `report` (§5.2 is 2.2.10-only)

2.6.2 `REPORT_RESULTS` (`aidlc-orchestrate.ts:4473-4482`) = `FORWARD_RESULTS {approved, completed, complete, done}` ∪ `GATE_RESULTS {awaiting-approval, rejected, revised}` ∪ `RESUME_RESULTS {resume, resumed}` ∪ `{skipped}`. `report` without `--result` errors listing that set (:5244-5259). Paths:

- `--result rejected` → requires nonblank `--user-input` or `--reason` (`report --result rejected for "<slug>" requires nonblank --user-input or --reason feedback.`, :5430-5440), stage must be `in-progress` or `awaiting-approval`, spawns `aidlc-state.ts reject <slug> --feedback <text>` → `[?]→[R]`, `GATE_REJECTED` + `STAGE_REVISING`.
- `--result revised` → stage must be `revising`; `[R]→[?]`, fresh `STAGE_AWAITING_APPROVAL`.
- `--result awaiting-approval` → `gate-start` (`[-]→[?]`).
- `--result resumed --user-input "<answer>"` → resume-menu router (:5150-5175): `jump`/`fresh|start over`/`resume|checkpoint|continue` substrings → per-choice `print`.
- Conductor contract: `stage-protocol.md:242` and `SKILL.md:100` ("On Request Changes, `report --stage … --result rejected --user-input "<feedback>"` … then `report … --result revised` before re-presenting. Never call lifecycle verbs on `aidlc-state.ts` directly.").

Success evidence for a Studio "Request changes" action (FR-GATE-007): new `GATE_REJECTED` (+`STAGE_REVISING`) rows with `**Stage**: <slug>` and the row flipping `[?]→[R]`; later `[R]→[?]` + `STAGE_AWAITING_APPROVAL` reopens the Gate.

### A5. Resolution events (§6.1 is incomplete for 2.6.2)

`aidlc-lib.ts:2528-2558`: `GATE_RESOLUTION_EVENTS = {GATE_APPROVED, GATE_REJECTED, QUESTION_ANSWERED, SUMMARY_CONFIRMATION_RECORDED}` plus `AUTONOMY_MODE_SET` when its `Mode` field is `autonomous`. Same fail-open-on-empty-ledger, cross-shard same-second fail-closed logic (full function quoted in doc 04 §5.4 terms).

### A6. Hook table (§7.1 is 2.2.10-only); 2.6.2 `dist/kiro/.kiro/agents/aidlc.json`

| Kiro event | matcher | adapter subcommand | timeout_ms |
|---|---|---|---|
| `agentSpawn` | — | `session-start` | — |
| `userPromptSubmit` | — | `verb-intercept` | 30000 |
| `preToolUse` | `execute_bash` | `state-transition-guard` | 15000 |
| `preToolUse` | `execute_bash` | `guard-tool-call` | 15000 |
| `preToolUse` | `fs_write` | `review-freeze` | 15000 |
| `preToolUse` | `execute_bash` | `review-freeze` | 15000 |
| `preToolUse` | `subagent` | `deliver-stage-rules` | 15000 |
| `preToolUse` | `subagent` | `plan-approval-guard` | 15000 |
| `postToolUse` | `fs_write` | `audit-and-sensors` | 120000 |
| `postToolUse` | `execute_bash` | `rebuild-stage-graph` | 45000 |
| `postToolUse` | `todo_list` | `sync-workflow-state` | — |
| `postToolUse` | `subagent` | `log-subagent` | — |
| `stop` | — | `continue-workflow` | 30000 |

`tools` and `allowedTools` unchanged; **`model` is absent** in 2.6.2 (2.2.10 pinned `claude-opus-4.8`). Hook-health file names follow these subcommand names (doc 04 §3.5).

### A7. `present-gate` has no response token (closes the FR-GATE-002 "engine-supplied token" question)

`core/tools/aidlc-directive.ts:292-300`: `PresentGateDirective { kind: "present-gate"; narration?; stage; phase; memory_path }`, `PRESENT_GATE_FIELDS = ["kind","stage","phase","memory_path"]` (:470). Still a documented placeholder that the engine never emits (`SKILL.md:83`: "The orchestration engine emits eight kinds today … `dispatch-subagent` and `present-gate` arms remain documented placeholders"). The `ask` directive gained `ask_type: "new-work-routing"`, `response_route: "next"`, `new_work_description`, `proposed_scope` (:308-330). **Conclusion:** there is no engine-supplied response token anywhere in 2.6.2; FR-GATE-002 falls to Studio protocol constants = the exact option labels (A8).

### A8. Exact gate wire text — resolved for 2.6.2

The Kiro annex is unchanged in mechanism (`harness/kiro/skills/aidlc/question-rendering.md`, 2.6.2): numbered prose, human replies "with a number (or just tell me)", conductor maps the reply back to the exact option **label** and records that label verbatim. Numbering is unstable (recommended option first, `Other` appended, extra options after 3 rejections). Therefore Studio must send the **label text**, never a digit:

| Checkpoint | Labels (verbatim, case-sensitive) | Engine call the conductor makes | Success evidence |
|---|---|---|---|
| Stage approval gate | `Approve` · `Request Changes` · `Accept as-is` (after 3 cycles) · optional `Add [Skipped Stage]` | `report --result approved --user-input "Approve"` / `report --result rejected --user-input "<feedback>"` | `GATE_APPROVED` + `[?]→[x]` / `GATE_REJECTED` + `[?]→[R]` |
| Consolidated summary confirmation | `Looks correct` · `Request changes` (lowercase c) | `aidlc-log.ts answer …` + `[Answer]: Looks correct` in the questions file, `SUMMARY_CONFIRMATION_RECORDED` | that audit row with `Questions SHA-256` |
| Plan approval (code-generation) | `Approve Plan` · `Request Changes` | conductor writes `[Answer]: Approve Plan` | `plan-approval-guard` admits the developer dispatch |
| Structured stage question | option label text (A–E) or free text for `X. Other` | `aidlc-log.ts answer --details "<exact choice>"` | `QUESTION_ANSWERED` + filled `[Answer]:` |
| Resume menu | free text containing `resume`/`checkpoint`/`continue`, `redo`, `jump`, `fresh`/`start over` | `report --result resumed --user-input "<answer>"` | per-choice `print` |

For Request Changes the recommended Studio wire text is the label on its own line followed by the feedback (e.g. `Request Changes\n\n<feedback>`), because 2.6.2 requires nonblank feedback for `--result rejected` and the conductor must not have to ask a follow-up. The mockup string `Request changes\n\n…` (doc 06 §5.9) has the wrong casing for a gate.

### A9. Minor 2.6.2 deltas

- `aidlc-utility.ts intent` accepts `intent | intent list | intent create | intent switch <name> | intent <name>`; `status` still has no `--json`. New unified router `core/tools/aidlc.ts` (doc 04 addendum A3).
- State Version is 8, 33 stages; §2.3/§11 stage lists are 32-stage (see doc 04 §7 for the 2.6.2 table).
- §8 read-back recipe stands; `setActiveIntentCursor` is still non-atomic `writeFileSync` in 2.6.2 (`aidlc-lib.ts:1834-1843`).
