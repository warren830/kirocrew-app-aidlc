---
slug: workspace-scaffold
phase: initialization
execution: ALWAYS
condition: Ensure-exists the per-intent record and artifact dirs — idempotent (creates on demand, skips existing)
lead_agent: orchestrator
support_agents: []
mode: inline
produces: []
consumes: []
requires_stage: []
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
inputs: none (first stage after session start)
outputs: the per-intent record tree (stage artifact dirs + verification dir) and the space-level knowledge/ dir
---

# Workspace Scaffold

Runs deterministically inside `aidlc-utility intent-birth`. The workspace shell ships in `dist/` (the SEED); birth only ensure-exists the per-intent record and artifact dirs (creates them on demand, idempotent). Kept as reference for audit event semantics.

MANDATORY: Follow stage-protocol.md for state tracking and audit logging.

## Steps

### Step 1: Update State

1. Update `<record>/aidlc-state.md`: set `Current Stage` to `scaffolding workspace`
2. Mark workspace-scaffold as `[-]` in progress

### Step 2: Ensure the Space Knowledge Directory

Ensure-exists the space-level domain-knowledge directory
`aidlc/spaces/<space>/knowledge/` (shorthand `aidlc/knowledge/`). It is
**free-form and empty at bootstrap** — no fixed file set, no per-agent
subdirectories, no seeded READMEs. A team adds its own markdown here over time;
the directory is a sibling of `memory/`, `codekb/`, and `intents/`, so domain
knowledge accumulates across every intent in the space rather than being trapped
in one intent's record. The agent personas read team knowledge from
`aidlc/knowledge/aidlc-shared/` and `aidlc/knowledge/<agent>/` if those exist —
the team creates them; birth does not. (The engine's per-agent METHODOLOGY
knowledge ships separately and read-only under `.kiro/knowledge/`.)

### Step 3: Ensure Stage Artifact Directories

Ensure-exists the empty per-intent stage artifact directories under the active
intent's record dir `aidlc/spaces/<space>/intents/<YYMMDD>-<label>/` (no READMEs) —
idempotent (created on demand):

- `<record>/initialization/` — workspace-scaffold/, workspace-detection/, state-init/
- `<record>/ideation/` — intent-capture/, market-research/, feasibility/, scope-definition/, team-formation/, rough-mockups/, approval-handoff/
