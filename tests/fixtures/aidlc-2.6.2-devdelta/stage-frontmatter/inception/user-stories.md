---
slug: user-stories
phase: inception
execution: CONDITIONAL
condition: Execute when user-facing features, multiple personas, complex business logic, or cross-team work is involved. Skip for pure refactoring, isolated bug fixes, infrastructure-only changes, or developer tooling.
lead_agent: aidlc-product-agent
support_agents:
  - aidlc-design-agent
  - aidlc-developer-agent
  - aidlc-quality-agent
mode: mob
summary_confirmation: required
reviewer: aidlc-product-lead-agent
reviewer_max_iterations: 2
review_class: advisory
produces:
  - stories
  - personas
  - user-stories-assessment
  - traceability
consumes:
  - artifact: requirements
    required: true
  - artifact: business-overview
    required: false
    conditional_on: brownfield
  - artifact: component-inventory
    required: false
    conditional_on: brownfield
  - artifact: team-practices
    required: false
requires_stage:
  - requirements-analysis
sensors:
  - required-sections
  - upstream-coverage
  - traceability
scopes:
  - enterprise
  - feature
  - mvp
  - workshop
inputs: <record>/inception/requirements-analysis/requirements.md, RE artifacts (if brownfield)
outputs: stories.md, personas.md, user-stories-assessment.md, traceability.json (under this stage's record dir, engine-resolved)
---

# User Stories

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load the Lead Persona (mob stage)

Read every path in `directive.inline_context_paths` per the stage protocol. For
this mob the roster contains the aidlc-product-agent persona and its shared/role
knowledge only; the product manager owns the inline draft and integration work.

This stage runs `mode: mob` (stage-protocol.md §5 "Multi-agent stages"): the support agents (aidlc-design-agent for user experience, aidlc-developer-agent for implementability, aidlc-quality-agent for testability) are NOT voices to adopt — they are dispatched as independent participants during PART 2. Do not load their personas into your own context.

