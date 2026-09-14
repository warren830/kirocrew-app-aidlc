---
slug: scope-definition
phase: ideation
execution: ALWAYS
condition: Always executes — defines the scope boundary and prioritized backlog
lead_agent: aidlc-product-agent
support_agents:
  - aidlc-delivery-agent
mode: inline
produces:
  - scope-document
  - intent-backlog
  - scope-definition-questions
consumes:
  - artifact: intent-statement
    required: true
  - artifact: feasibility-assessment
    required: false
  - artifact: constraint-register
    required: false
requires_stage:
  - intent-capture
  - feasibility
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - mvp
inputs: Intent statement, feasibility assessment, constraint register
outputs: scope-document.md, intent-backlog.md, scope-definition-questions.md (under this stage's record dir, engine-resolved)
---

# Scope Definition & Prioritization

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-product-agent persona from `agents/aidlc-product-agent.md` and knowledge from `.kiro/knowledge/aidlc-product-agent/`.

### Step 2: Load Prior Context

- Read intent statement from `<record>/ideation/intent-capture/`
- Read feasibility assessment from `<record>/ideation/feasibility/` (if exists)
- Read constraint register and RAID log (if exist)

### Step 3: Generate Clarifying Questions

Create `<record>/ideation/scope-definition/scope-definition-questions.md` with questions:
- What is the minimum viable scope that delivers value?
- What capabilities are must-have vs. nice-to-have?
- What are the dependencies between capabilities?
- What is the sequencing preference (risk-first, value-first, dependency-first)?
- Are there hard deadlines tied to specific capabilities?

Follow stage-protocol.md question flow.
