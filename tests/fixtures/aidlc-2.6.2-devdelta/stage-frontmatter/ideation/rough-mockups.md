---
slug: rough-mockups
phase: ideation
execution: CONDITIONAL
condition: Execute when user-facing UI is part of the initiative; for API/backend, produce system interaction diagrams. Skip for non-UI, API-only, or infrastructure-only initiatives.
lead_agent: aidlc-design-agent
support_agents:
  - aidlc-product-agent
mode: inline
summary_confirmation: required
reviewer: aidlc-product-lead-agent
reviewer_max_iterations: 2
review_class: advisory
produces:
  - wireframes
  - user-flow
  - rough-mockups-questions
consumes:
  - artifact: intent-statement
    required: true
  - artifact: scope-document
    required: true
  - artifact: intent-backlog
    required: true
requires_stage:
  - scope-definition
  - team-formation
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - mvp
inputs: Intent statement, scope definition, intent backlog
outputs: wireframes.md, user-flow.md, rough-mockups-questions.md (under this stage's record dir, engine-resolved)
---

# Rough Mockups & Concept Visualization

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-design-agent persona from `agents/aidlc-design-agent.md` and knowledge from `.kiro/knowledge/aidlc-design-agent/`.

### Step 2: Load Prior Context

- Read intent statement from `<record>/ideation/intent-capture/`
- Read scope definition and intent backlog from `<record>/ideation/scope-definition/`

### Step 3: Generate Clarifying Questions

Create `<record>/ideation/rough-mockups/rough-mockups-questions.md` with questions:
- What are the primary user entry points and key screens/views?
- What is the core user flow (happy path)?
- What does the information hierarchy look like?
- Are there existing brand guidelines, design systems, or UI patterns to follow?
