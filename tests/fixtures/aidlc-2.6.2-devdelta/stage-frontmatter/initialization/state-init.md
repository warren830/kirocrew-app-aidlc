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

Read the state contract from `.kiro/knowledge/aidlc-shared/state-template.md`.
Overwrite `<record>/aidlc-state.md` with the full populated version generated
from the compiled stage graph and scope grid:
- Project description (from orchestrator's $ARGUMENTS or `<record>/audit/<host>-<clone>.md`)
- Project type (greenfield/brownfield from workspace-detection)
- Workspace state (languages, frameworks, build system from workspace-detection)
- Start date — run `date -u +'%Y-%m-%dT%H:%M:%SZ'` via Bash
- Scope configuration (stages to execute/skip per scope routing)
- Full stage progress checkboxes (all stages, with INITIALIZATION stages marked [x] for workspace-scaffold, workspace-detection)
- Mark state-init as `[-]` in progress
- Total Stages: count EXECUTE stages only (not SKIP). Authoritative counts come
  from the compiled scope grid (`.kiro/tools/data/scope-grid.json`),
  transposed from each stage's `scopes:` frontmatter. Run
  `bun .kiro/tools/aidlc-utility.ts scope-table` for the live scope
  counts and `bun .kiro/tools/aidlc-utility.ts stage-table` for the
  live compiled stage list.
- Completed: set to number of completed INITIALIZATION stages (typically 3)
- In Progress: set to first post-initialization stage name
