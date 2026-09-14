---
slug: user-stories
phase: inception
execution: CONDITIONAL
condition: Execute when user-facing features, multiple personas, complex business logic, or cross-team work is involved. Skip for pure refactoring, isolated bug fixes, infrastructure-only changes, or developer tooling.
lead_agent: aidlc-product-agent
support_agents:
  - aidlc-design-agent
mode: inline
reviewer: aidlc-product-lead-agent
reviewer_max_iterations: 2
produces:
  - stories
  - personas
  - user-stories-assessment
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
scopes:
  - enterprise
  - feature
  - mvp
  - workshop
inputs: <record>/inception/requirements-analysis/requirements.md, RE artifacts (if brownfield)
outputs: stories.md, personas.md, user-stories-assessment.md (under this stage's record dir, engine-resolved)
---

# User Stories

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-product-agent persona from `agents/aidlc-product-agent.md` and knowledge from `.kiro/knowledge/aidlc-product-agent/`.
Load aidlc-design-agent persona from `agents/aidlc-design-agent.md` and knowledge from `.kiro/knowledge/aidlc-design-agent/` for supporting perspective on user experience.

### Step 2: Validate User Stories Are Needed

Assess whether user stories add value for this project. Provide reasoning:
- **Execute if**: user-facing features, multiple user personas, complex business logic, cross-team coordination needed
- **Skip if**: pure refactoring, isolated bug fixes, infrastructure-only, developer tooling

Create `<record>/inception/user-stories/user-stories-assessment.md` documenting the assessment:
- Decision: Execute or Skip
- Rationale: Why user stories are or are not needed for this project
