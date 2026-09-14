---
slug: reverse-engineering
phase: inception
execution: CONDITIONAL
condition: Execute when project is brownfield. Always rerun for freshness. Skip for greenfield projects.
lead_agent: aidlc-developer-agent
support_agents:
  - aidlc-architect-agent
mode: subagent
produces:
  - business-overview
  - architecture
  - code-structure
  - api-documentation
  - component-inventory
  - technology-stack
  - dependencies
  - code-quality-assessment
  - reverse-engineering-timestamp
consumes: []
requires_stage:
  - state-init
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - mvp
  - poc
  - bugfix
  - refactor
  - security-patch
  - workshop
inputs: <record>/aidlc-state.md
outputs: "aidlc/spaces/<active-space>/codekb/<repo>/ (9 artifacts: business-overview.md, architecture.md, code-structure.md, api-documentation.md, component-inventory.md, technology-stack.md, dependencies.md, code-quality-assessment.md, reverse-engineering-timestamp.md)"
---

# Reverse Engineering

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Check Conditions

Read `<record>/aidlc-state.md` to confirm:
- Project type is brownfield

If project is not brownfield, skip this stage and update aidlc-state.md with skip reason.

#### Resolve the intent's repo set (multi-repo)

This stage runs **per repo** the intent touches. Resolve the repo set from the
intent's registry row before scanning:

1. Read the active intent's `repos` array from
   `aidlc/spaces/<active-space>/intents/intents.json` (the row whose `uuid`/`slug`
   matches the active intent). This is the set captured at intent birth (an explicit
   `--repos a,b` or sibling auto-discovery).
