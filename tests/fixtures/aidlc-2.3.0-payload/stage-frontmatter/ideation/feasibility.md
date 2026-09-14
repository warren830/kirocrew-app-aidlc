---
slug: feasibility
phase: ideation
execution: CONDITIONAL
condition: Execute when there are integration constraints, regulatory requirements, or significant technical uncertainty. Skip for trivial changes with no technical risk.
lead_agent: aidlc-architect-agent
support_agents:
  - aidlc-aws-platform-agent
  - aidlc-compliance-agent
mode: inline
produces:
  - feasibility-assessment
  - constraint-register
  - raid-log
  - feasibility-questions
consumes:
  - artifact: intent-statement
    required: true
  - artifact: competitive-analysis
    required: false
  - artifact: market-trends
    required: false
  - artifact: build-vs-buy
    required: false
requires_stage:
  - intent-capture
  - market-research
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - mvp
inputs: Intent statement from intent-capture stage, market research from market-research stage (if executed)
outputs: feasibility-assessment.md, constraint-register.md, raid-log.md, feasibility-questions.md (under this stage's record dir, engine-resolved)
---

# Feasibility & Constraint Analysis

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-architect-agent persona from `agents/aidlc-architect-agent.md` and knowledge from `.kiro/knowledge/aidlc-architect-agent/`.
Orchestrator will separately invoke aidlc-aws-platform-agent and aidlc-compliance-agent for their perspectives.

### Step 2: Load Prior Context

- Read intent statement from `<record>/ideation/intent-capture/`
- Read market research from `<record>/ideation/market-research/` (if exists)
- Load guardrails from `.kiro/steering/`

### Step 3: Generate Clarifying Questions

Create `<record>/ideation/feasibility/feasibility-questions.md` with questions:
- What existing systems must this integrate with?
- Are there regulatory/compliance requirements (PCI, HIPAA, SOC2, data residency)?
