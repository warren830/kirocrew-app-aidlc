---
slug: nfr-design
phase: construction
execution: CONDITIONAL
condition: NFR Requirements was executed and NFR patterns need design. Skip if NFR Requirements was skipped.
lead_agent: aidlc-architect-agent
support_agents:
  - aidlc-aws-platform-agent
mode: inline
reviewer: aidlc-architecture-reviewer-agent
reviewer_max_iterations: 2
for_each: unit-of-work
produces:
  - performance-design
  - security-design
  - scalability-design
  - reliability-design
  - logical-components
produces_kinds:
  performance-design: [service, ui]
  scalability-design: [service]
  reliability-design: [service]
  logical-components: [service, ui, library]
consumes:
  - artifact: performance-requirements
    required: true
  - artifact: security-requirements
    required: true
  - artifact: scalability-requirements
    required: true
  - artifact: reliability-requirements
    required: true
  - artifact: tech-stack-decisions
    required: true
  - artifact: business-logic-model
    required: true
requires_stage:
  - units-generation
  - nfr-requirements
sensors:
  - required-sections
  - upstream-coverage
  - linter
  - type-check
scopes:
  - enterprise
  - feature
  - mvp
  - infra
  - workshop
inputs: NFR requirements artifacts, functional design artifacts
outputs: "performance-design.md, security-design.md, scalability-design.md, reliability-design.md, logical-components.md (under this stage's per-unit record dir, engine-resolved); per-kind applicability via produces_kinds (untagged unit: all)"
---

# NFR Design

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

