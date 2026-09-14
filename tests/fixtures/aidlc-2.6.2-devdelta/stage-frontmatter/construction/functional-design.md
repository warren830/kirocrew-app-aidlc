---
slug: functional-design
phase: construction
execution: CONDITIONAL
condition: New data models, complex business logic, or business rules need design. Skip if simple logic changes with no new business logic.
lead_agent: aidlc-architect-agent
support_agents:
  - aidlc-developer-agent
mode: inline
summary_confirmation: required
reviewer: aidlc-architecture-reviewer-agent
reviewer_max_iterations: 2
for_each: unit-of-work
produces:
  - entities
  - rules
  - functional-spec
  - traceability
optional_produces:
  - frontend-components
produces_kinds:
  entities: [service, spec, library]
  rules: [service, spec, library]
  functional-spec: [service, ui, library]
  traceability: [service, spec, ui, library]
  frontend-components: [ui]
consumes:
  - artifact: unit-of-work
    required: true
  - artifact: unit-of-work-story-map
    required: false
  - artifact: requirements
    required: true
  - artifact: components
    required: true
  - artifact: contract-summary
    required: false
requires_stage:
  - units-generation
  - contract-design
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
  - refactor
  - workshop
inputs: unit-of-work.md, unit-of-work-story-map.md, requirements.md, domain-design components.md, contract-design contract-summary.md (if produced)
outputs: "entities.md, rules.md, functional-spec.md, traceability.json, CONDITIONAL: frontend-components.md (under this stage's per-unit record dir, engine-resolved); per-kind applicability via produces_kinds (untagged unit: all). entities.md and rules.md each carry a fenced ```yaml source-of-truth block; functional-spec.md is the source of truth for workflows and state machines and carries derived ER-diagram and rules-summary views."
---

# Functional Design

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

