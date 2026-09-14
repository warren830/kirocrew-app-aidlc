---
slug: infrastructure-design
phase: construction
execution: CONDITIONAL
condition: Infrastructure services need mapping, deployment architecture required, or cloud resources needed. Skip if no infrastructure changes and infrastructure already defined.
lead_agent: aidlc-aws-platform-agent
support_agents:
  - aidlc-devsecops-agent
  - aidlc-compliance-agent
mode: inline
summary_confirmation: required
reviewer: aidlc-architecture-reviewer-agent
reviewer_max_iterations: 2
for_each: unit-of-work
produces:
  - infrastructure-specification
  - monitoring-design
  - cicd-pipeline
  - traceability
produces_kinds:
  infrastructure-specification: [service, ui, packaging]
  monitoring-design: [service, ui, packaging]
  cicd-pipeline: [service, ui, packaging, library]
  traceability: [service, ui, packaging, library]
consumes:
  - artifact: performance-design
    required: true
  - artifact: security-design
    required: true
  - artifact: scalability-design
    required: true
  - artifact: reliability-design
    required: true
  - artifact: observability-design
    required: true
  - artifact: logical-components
    required: true
  - artifact: components
    required: true
  - artifact: functional-spec
    required: true
  - artifact: contract-summary
    required: false
requires_stage:
  - units-generation
  - nfr-design
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
  - workshop
inputs: NFR design artifacts, domain design components.md, functional design
outputs: "infrastructure-specification.md (deployment + services + shared, tabular), monitoring-design.md (tabular), cicd-pipeline.md, traceability.json (under this stage's per-unit record dir, engine-resolved); per-kind applicability via produces_kinds (a spec unit owes none)"
---
