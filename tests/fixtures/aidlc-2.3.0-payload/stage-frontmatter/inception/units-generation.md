---
slug: units-generation
phase: inception
execution: ALWAYS
condition: Always executes when in scope. Produces the dependency DAG that Stage 2.8 Delivery Planning consumes for Bolt sequencing. In the compiled scope grid, 2.7 and 2.8 travel together — both EXECUTE or both SKIP per scope.
lead_agent: aidlc-architect-agent
support_agents:
  - aidlc-delivery-agent
mode: inline
reviewer: aidlc-architecture-reviewer-agent
reviewer_max_iterations: 2
produces:
  - unit-of-work
  - unit-of-work-dependency
  - unit-of-work-story-map
consumes:
  - artifact: components
    required: true
  - artifact: component-methods
    required: true
  - artifact: services
    required: true
  - artifact: component-dependency
    required: true
  - artifact: decisions
    required: true
  - artifact: requirements
    required: true
  - artifact: stories
    required: false
requires_stage:
  - application-design
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - mvp
  - workshop
inputs: <record>/inception/application-design/ (all design artifacts), <record>/inception/requirements-analysis/requirements.md, <record>/inception/user-stories/stories.md (if produced)
outputs: unit-of-work.md, unit-of-work-dependency.md, unit-of-work-story-map.md (under this stage's record dir, engine-resolved)
---

# Units Generation

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

NOTE: **Stage 2.7 produces the dependency DAG (topology). Stage 2.8 chooses the economic path through it (Bolt sequence).** 2.7 MUST NOT recommend an implementation order or identify a critical path — those are 2.8's economic-sequencing decisions. This stage describes what can depend on what; 2.8 decides what to ship first and why.

---

## Steps

### PART 1: Planning

### Step 1: Load Agent Personas

Load aidlc-architect-agent persona from `agents/aidlc-architect-agent.md` and knowledge from `.kiro/knowledge/aidlc-architect-agent/`.
Load aidlc-delivery-agent persona from `agents/aidlc-delivery-agent.md` and knowledge from `.kiro/knowledge/aidlc-delivery-agent/` for feasibility validation and prioritization.
