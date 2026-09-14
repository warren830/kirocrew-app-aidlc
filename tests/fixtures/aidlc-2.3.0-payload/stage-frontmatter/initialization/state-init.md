---
slug: state-init
phase: initialization
execution: ALWAYS
condition: Creates full populated state file and determines routing — auto-proceeds
lead_agent: orchestrator
support_agents: []
mode: inline
produces: []
consumes: []
requires_stage:
  - workspace-detection
sensors: []
scopes:
  - enterprise
  - feature
  - mvp
  - poc
  - bugfix
  - refactor
  - infra
  - security-patch
  - workshop
inputs: workspace classification from workspace-detection, scope from orchestrator
outputs: <record>/aidlc-state.md (full populated version, engine-resolved)
---

# State Initialization

Runs deterministically inside `aidlc-utility init`. Kept as reference for state-file contract.

MANDATORY: Follow stage-protocol.md for state tracking and audit logging.

## Steps

### Step 1: Update State

1. Update `<record>/aidlc-state.md`: set `Current Stage` to `initializing state`
2. Mark state-init as `[-]` in progress

### Step 2: Create Full State File

Read the state template from `.kiro/knowledge/aidlc-shared/state-template.md`.
Overwrite `<record>/aidlc-state.md` with the full populated version:
- Project description (from orchestrator's $ARGUMENTS or `<record>/audit/<host>-<clone>.md`)
- Project type (greenfield/brownfield from workspace-detection)
- Workspace state (languages, frameworks, build system from workspace-detection)
- Start date — run `date -u +'%Y-%m-%dT%H:%M:%SZ'` via Bash
- Scope configuration (stages to execute/skip per scope routing)
- Full stage progress checkboxes (all stages, with INITIALIZATION stages marked [x] for workspace-scaffold, workspace-detection)
- Mark state-init as `[-]` in progress
- Total Stages: count EXECUTE stages only (not SKIP). Authoritative counts come from the compiled scope grid (`.kiro/tools/data/scope-grid.json`, transposed from each stage's `scopes:` frontmatter; run `bun .kiro/tools/aidlc-utility.ts scope-table` for the live table). Today's values:
  | Scope | EXECUTE / Total |
  |-------|-----------------|
  | enterprise / feature | 31 / 31 |
  | mvp | 21 / 31 |
  | poc | 8 / 31 |
  | bugfix | 7 / 31 |
  | refactor | 8 / 31 |
  | infra | 12 / 31 |
