---
slug: domain-design
phase: inception
execution: CONDITIONAL
condition: Execute when new components or logical building blocks are needed. Skip when changes are modifications to existing components only.
lead_agent: aidlc-architect-agent
support_agents:
  - aidlc-aws-platform-agent
  - aidlc-design-agent
mode: inline
summary_confirmation: required
reviewer: aidlc-architecture-reviewer-agent
reviewer_max_iterations: 2
review_class: advisory
produces:
  - components
  - decisions
  - traceability
consumes:
  - artifact: requirements
    required: true
  - artifact: stories
    required: false
  - artifact: architecture
    required: false
    conditional_on: brownfield
  - artifact: component-inventory
    required: false
    conditional_on: brownfield
  - artifact: team-practices
    required: false
requires_stage:
  - requirements-analysis
  - refined-mockups
sensors:
  - required-sections
  - upstream-coverage
  - traceability
scopes:
  - enterprise
  - feature
  - mvp
  - workshop
inputs: <record>/inception/requirements-analysis/requirements.md, <record>/inception/user-stories/stories.md (if produced), RE artifacts (if brownfield)
outputs: components.md (fenced ```yaml component catalogue plus a human-readable mermaid diagram and summary table), decisions.md (Architecture Decision Records), and traceability.json — all under this stage's record dir, engine-resolved
---

# Domain Design

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

Identify and detail the **logical building blocks** of the system — the components you will write code for. A component is a bounded piece of software with its own business logic, entities, and lifecycle: **code you write, not infrastructure you deploy.** Databases, caches, queues, and third-party services are dependencies OF components, not components themselves.

This stage does NOT decide deployment topology (monolith, microservices, serverless, etc.) — that is Units Generation's job. Domain Design produces the building blocks so the team can then decide how to group them into deployable units. It also does not choose the tech stack or NFR patterns — those belong to the NFR and infrastructure stages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-architect-agent persona from `agents/aidlc-architect-agent.md` and knowledge from `.kiro/knowledge/aidlc-architect-agent/`.
