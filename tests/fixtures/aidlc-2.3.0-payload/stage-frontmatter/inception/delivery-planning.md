---
slug: delivery-planning
phase: inception
execution: ALWAYS
condition: Always executes — capstone Inception stage, produces the detailed execution plan for Construction and Operation
lead_agent: aidlc-delivery-agent
support_agents:
  - aidlc-architect-agent
mode: inline
produces:
  - bolt-plan
  - team-allocation
  - risk-and-sequencing-rationale
  - external-dependency-map
  - delivery-planning-questions
consumes:
  - artifact: requirements
    required: true
  - artifact: stories
    required: false
  - artifact: mockups
    required: false
  - artifact: components
    required: true
  - artifact: unit-of-work
    required: true
  - artifact: unit-of-work-dependency
    required: true
  - artifact: unit-of-work-story-map
    required: false
  - artifact: team-practices
    required: false
requires_stage:
  - units-generation
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - mvp
  - workshop
inputs: All Inception artifacts (requirements, stories, mockups, architecture, units)
outputs: bolt-plan.md, team-allocation.md, risk-and-sequencing-rationale.md, external-dependency-map.md, delivery-planning-questions.md (under this stage's record dir, engine-resolved)
---

# Delivery Planning

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-delivery-agent persona from `agents/aidlc-delivery-agent.md` and knowledge from `.kiro/knowledge/aidlc-delivery-agent/`.
Load aidlc-architect-agent for build order validation.

### Step 2: Load Prior Context

Read all Inception phase artifacts:
