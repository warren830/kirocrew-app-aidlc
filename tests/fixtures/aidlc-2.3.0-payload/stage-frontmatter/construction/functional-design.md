---
slug: functional-design
phase: construction
execution: CONDITIONAL
condition: New data models, complex business logic, or business rules need design. Skip if simple logic changes with no new business logic.
lead_agent: aidlc-architect-agent
support_agents:
  - aidlc-developer-agent
mode: inline
reviewer: aidlc-architecture-reviewer-agent
reviewer_max_iterations: 2
for_each: unit-of-work
produces:
  - business-logic-model
  - business-rules
  - domain-entities
optional_produces:
  - frontend-components
produces_kinds:
  business-logic-model: [service, ui, library]
  business-rules: [service, spec, library]
  domain-entities: [service, spec, library]
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
  - artifact: component-methods
    required: true
  - artifact: services
    required: true
requires_stage:
  - units-generation
sensors:
  - required-sections
  - upstream-coverage
  - linter
  - type-check
scopes:
  - enterprise
  - feature
  - mvp
  - refactor
  - workshop
inputs: unit-of-work.md, unit-of-work-story-map.md, requirements.md, application design artifacts
outputs: "business-logic-model.md, business-rules.md, domain-entities.md, CONDITIONAL: frontend-components.md (under this stage's per-unit record dir, engine-resolved); per-kind applicability via produces_kinds (untagged unit: all)"
---

# Functional Design

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Execution Modes
