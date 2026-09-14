---
slug: requirements-analysis
phase: inception
execution: ALWAYS
condition: Always executes — depth scales with project complexity
lead_agent: aidlc-product-agent
support_agents: []
mode: inline
summary_confirmation: required
reviewer: aidlc-product-lead-agent
reviewer_max_iterations: 2
review_class: advisory
produces:
  - requirements
  - requirements-analysis-questions
consumes:
  - artifact: intent-statement
    required: false
  - artifact: scope-document
    required: false
  - artifact: business-overview
    required: false
    conditional_on: brownfield
  - artifact: architecture
    required: false
    conditional_on: brownfield
  - artifact: code-structure
    required: false
    conditional_on: brownfield
  - artifact: team-practices
    required: false
requires_stage:
  - approval-handoff
  - reverse-engineering
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - mvp
  - poc
  - bugfix
  - refactor
  - infra
  - security-patch
  - workshop
inputs: RE artifacts (if brownfield), user's project description (from <record>/audit/<host>-<clone>.md)
outputs: requirements.md, requirements-analysis-questions.md (under this stage's record dir, engine-resolved)
---

# Requirements Analysis

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-product-agent persona from `agents/aidlc-product-agent.md` and knowledge from `.kiro/knowledge/aidlc-product-agent/`.
