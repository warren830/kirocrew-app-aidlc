---
slug: reverse-engineering
phase: inception
execution: CONDITIONAL
condition: Execute when project is brownfield. On rerun the Step 1 guard checks store freshness (codekb-scope-diff) - verified-CURRENT stores may be reused by human choice, anything else rescans. Skip for greenfield projects.
lead_agent: aidlc-developer-agent
support_agents:
  - aidlc-architect-agent
mode: pipeline
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

This stage runs `mode: pipeline` (stage-protocol.md §5): a two-link chain in
which each link advances the work product directly. The developer lead (link
1) scans and returns structured results; the architect (link 2, the final
link) synthesizes those results and writes the 9 artifacts. The final link
leaving the `produces[]` artifacts complete is the pipeline contract working
as designed — no contribution files on pipeline stages.

## Steps

### Step 1: Check Conditions

Read `<record>/aidlc-state.md` to confirm:
- Project type is brownfield

If the project is not brownfield, run
`bun .kiro/tools/aidlc-orchestrate.ts report --stage reverse-engineering --result skipped --reason "<reason>"`.
The engine records the skip and advances to the next in-scope stage.

