---
slug: code-generation
phase: construction
execution: ALWAYS
condition: Always executes for every unit in the execution plan.
lead_agent: aidlc-developer-agent
support_agents: []
mode: subagent
reviewer: aidlc-architecture-reviewer-agent
reviewer_max_iterations: 2
for_each: unit-of-work
workspace_requires: true
produces:
  - code-generation-plan
  - code-summary
consumes:
  - artifact: business-logic-model
    required: false
  - artifact: business-rules
    required: false
  - artifact: domain-entities
    required: false
  - artifact: performance-design
    required: false
  - artifact: security-design
    required: false
  - artifact: deployment-architecture
    required: false
  - artifact: unit-of-work
    required: true
  - artifact: requirements
    required: true
requires_stage:
  - units-generation
  - functional-design
  - nfr-requirements
  - nfr-design
  - infrastructure-design
sensors:
  - linter
  - type-check
scopes:
  - enterprise
  - feature
  - mvp
  - poc
  - bugfix
  - refactor
  - security-patch
  - workshop
inputs: ALL prior design artifacts for this unit
outputs: application code + code-generation-plan.md, code-summary.md (under this stage's per-unit record dir, engine-resolved)
---

# Code Generation

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

