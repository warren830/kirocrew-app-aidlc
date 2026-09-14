---
slug: approval-handoff
phase: ideation
execution: ALWAYS
condition: Always executes — compiles all Ideation artifacts into initiative brief for approval
lead_agent: aidlc-delivery-agent
support_agents:
  - aidlc-product-agent
mode: inline
summary_confirmation: required
produces:
  - initiative-brief
  - decision-log
  - approval-handoff-questions
consumes:
  - artifact: intent-statement
    required: true
  - artifact: stakeholder-map
    required: true
  - artifact: scope-document
    required: true
  - artifact: intent-backlog
    required: true
  - artifact: competitive-analysis
    required: false
  - artifact: feasibility-assessment
    required: false
  - artifact: constraint-register
    required: false
  - artifact: team-assessment
    required: false
  - artifact: wireframes
    required: false
requires_stage:
  - intent-capture
  - feasibility
  - scope-definition
  - team-formation
  - rough-mockups
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
inputs: All Ideation phase artifacts (intent, market research, feasibility, scope, team, mockups)
outputs: initiative-brief.md, decision-log.md, approval-handoff-questions.md (under this stage's record dir, engine-resolved)
---

# Initiative Approval & Handoff

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-delivery-agent persona from `agents/aidlc-delivery-agent.md` and knowledge from `.kiro/knowledge/aidlc-delivery-agent/`.

### Step 2: Load Prior Context
