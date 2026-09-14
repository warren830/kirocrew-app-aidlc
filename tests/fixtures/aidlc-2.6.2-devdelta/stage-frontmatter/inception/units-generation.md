---
slug: units-generation
phase: inception
execution: ALWAYS
condition: Always executes when in scope. Produces the dependency DAG that Stage 2.9 Delivery Planning consumes for Bolt sequencing. In the compiled scope grid, 2.7 (Units Generation) and 2.9 (Delivery Planning) travel together — both EXECUTE or both SKIP per scope.
lead_agent: aidlc-architect-agent
support_agents:
  - aidlc-delivery-agent
mode: inline
summary_confirmation: required
reviewer: aidlc-architecture-reviewer-agent
reviewer_max_iterations: 2
review_class: advisory
produces:
  - unit-of-work
  - unit-of-work-dependency
  - unit-of-work-story-map
  - traceability
consumes:
  - artifact: components
    required: true
  - artifact: decisions
    required: false
  - artifact: requirements
    required: true
  - artifact: stories
    required: false
requires_stage:
  - domain-design
sensors:
  - required-sections
  - upstream-coverage
  - traceability
scopes:
  - enterprise
  - feature
  - mvp
  - workshop
inputs: <record>/inception/domain-design/components.md, <record>/inception/requirements-analysis/requirements.md, <record>/inception/user-stories/stories.md (if produced)
outputs: unit-of-work.md, unit-of-work-dependency.md, unit-of-work-story-map.md, traceability.json (under this stage's record dir, engine-resolved)
---

# Units Generation

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

NOTE: **Stage 2.7 produces the dependency DAG (topology). Stage 2.9 Delivery Planning chooses the economic path through it (Bolt sequence).** 2.7 MUST NOT recommend an implementation order or identify a critical path — those are 2.9's economic-sequencing decisions. This stage describes what can depend on what; 2.9 decides what to ship first and why.

---

## Steps

### PART 1: Planning

### Step 1: Load Agent Personas

Load aidlc-architect-agent persona from `agents/aidlc-architect-agent.md` and knowledge from `.kiro/knowledge/aidlc-architect-agent/`.
Load aidlc-delivery-agent persona from `agents/aidlc-delivery-agent.md` and knowledge from `.kiro/knowledge/aidlc-delivery-agent/` for feasibility validation and prioritization.

### Step 2: Load Prior Context
