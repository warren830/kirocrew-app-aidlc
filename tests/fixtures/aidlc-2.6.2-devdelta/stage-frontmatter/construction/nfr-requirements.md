---
slug: nfr-requirements
phase: construction
execution: CONDITIONAL
condition: Performance, security, scalability, reliability, or observability requirements needed, or tech stack selection needed. Skip if no NFR requirements and tech stack already determined.
lead_agent: aidlc-architect-agent
support_agents:
  - aidlc-devsecops-agent
  - aidlc-compliance-agent
  - aidlc-quality-agent
mode: inline
summary_confirmation: required
reviewer: aidlc-architecture-reviewer-agent
reviewer_max_iterations: 2
for_each: unit-of-work
produces:
  - performance-requirements
  - security-requirements
  - scalability-requirements
  - reliability-requirements
  - observability-requirements
  - tech-stack-decisions
  - traceability
produces_kinds:
  performance-requirements: [service, ui]
  scalability-requirements: [service]
  reliability-requirements: [service]
  observability-requirements: [service]
consumes:
  - artifact: functional-spec
    required: true
  - artifact: rules
    required: true
  - artifact: requirements
    required: true
  - artifact: contract-summary
    required: false
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
  - traceability
scopes:
  - enterprise
  - feature
  - mvp
  - infra
  - security-patch
  - workshop
inputs: functional design artifacts, requirements.md, RE artifacts
outputs: "performance-requirements.md, security-requirements.md, scalability-requirements.md, reliability-requirements.md, observability-requirements.md, tech-stack-decisions.md, traceability.json (under this stage's per-unit record dir, engine-resolved); per-kind applicability via produces_kinds (untagged unit: all)"
---

