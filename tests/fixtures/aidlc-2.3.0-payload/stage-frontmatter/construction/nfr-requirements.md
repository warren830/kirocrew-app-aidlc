---
slug: nfr-requirements
phase: construction
execution: CONDITIONAL
condition: Performance requirements, security considerations, scalability concerns, or tech stack selection needed. Skip if no NFR requirements and tech stack already determined.
lead_agent: aidlc-architect-agent
support_agents:
  - aidlc-devsecops-agent
  - aidlc-compliance-agent
  - aidlc-quality-agent
mode: inline
reviewer: aidlc-architecture-reviewer-agent
reviewer_max_iterations: 2
for_each: unit-of-work
produces:
  - performance-requirements
  - security-requirements
  - scalability-requirements
  - reliability-requirements
  - tech-stack-decisions
produces_kinds:
  performance-requirements: [service, ui]
  scalability-requirements: [service]
  reliability-requirements: [service]
consumes:
  - artifact: business-logic-model
    required: true
  - artifact: business-rules
    required: true
  - artifact: requirements
    required: true
  - artifact: technology-stack
    required: false
    conditional_on: brownfield
requires_stage:
  - units-generation
  - functional-design
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
  - security-patch
  - workshop
inputs: functional design artifacts, requirements.md, RE artifacts
outputs: "performance-requirements.md, security-requirements.md, scalability-requirements.md, reliability-requirements.md, tech-stack-decisions.md (under this stage's per-unit record dir, engine-resolved); per-kind applicability via produces_kinds (untagged unit: all)"
---

# NFR Requirements

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Execution Modes
