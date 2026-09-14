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
reviewer: aidlc-architecture-reviewer-agent
reviewer_max_iterations: 2
for_each: unit-of-work
produces:
  - deployment-architecture
  - infrastructure-services
  - monitoring-design
  - cicd-pipeline
optional_produces:
  - shared-infrastructure
produces_kinds:
  deployment-architecture: [service, ui, packaging]
  infrastructure-services: [service, packaging]
  monitoring-design: [service, packaging]
  cicd-pipeline: [service, ui, packaging, library]
consumes:
  - artifact: performance-design
    required: true
  - artifact: security-design
    required: true
  - artifact: scalability-design
    required: true
  - artifact: reliability-design
    required: true
  - artifact: logical-components
    required: true
  - artifact: components
    required: true
  - artifact: services
    required: true
  - artifact: business-logic-model
    required: true
requires_stage:
  - units-generation
  - nfr-design
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
inputs: NFR design artifacts, application design, functional design
outputs: "deployment-architecture.md, infrastructure-services.md, monitoring-design.md, cicd-pipeline.md, CONDITIONAL: shared-infrastructure.md (under this stage's per-unit record dir, engine-resolved); per-kind applicability via produces_kinds (untagged unit: all)"
---

