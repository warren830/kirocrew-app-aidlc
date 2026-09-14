---
slug: team-formation
phase: ideation
execution: CONDITIONAL
condition: Execute when team composition, capacity, or mob planning is relevant. Skip for solo developer or small team projects.
lead_agent: aidlc-delivery-agent
support_agents: []
mode: inline
produces:
  - team-assessment
  - skill-matrix
  - mob-composition
  - team-formation-questions
consumes:
  - artifact: scope-document
    required: true
  - artifact: intent-backlog
    required: true
  - artifact: feasibility-assessment
    required: false
requires_stage:
  - scope-definition
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
inputs: Scope definition, intent backlog, feasibility assessment
outputs: team-assessment.md, skill-matrix.md, mob-composition.md, team-formation-questions.md (under this stage's record dir, engine-resolved)
---

# Team Formation & Mob Planning

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-delivery-agent persona from `agents/aidlc-delivery-agent.md` and knowledge from `.kiro/knowledge/aidlc-delivery-agent/`.

### Step 2: Load Prior Context

- Read scope definition from `<record>/ideation/scope-definition/`
- Read feasibility assessment and constraint register (if exist)
- Read intent backlog for work volume estimation

### Step 3: Generate Clarifying Questions

Create `<record>/ideation/team-formation/team-formation-questions.md` with questions:
- What teams and individuals are available?
- What is the current capacity and utilization?
- What skills are required vs. available?
- Are there competing initiatives drawing from the same talent pool?
- What is the preferred team topology?
- What time zones and locations are team members in?
- Are external partners, contractors, or AWS Professional Services needed?
- Who are the decision-makers for each phase?

