---
slug: application-design
phase: inception
execution: CONDITIONAL
condition: Execute when new components or services are needed, or service layer design is required. Skip when changes are modifications to existing components only.
lead_agent: aidlc-architect-agent
support_agents:
  - aidlc-aws-platform-agent
  - aidlc-design-agent
mode: inline
reviewer: aidlc-architecture-reviewer-agent
reviewer_max_iterations: 2
produces:
  - components
  - component-methods
  - services
  - component-dependency
  - decisions
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
scopes:
  - enterprise
  - feature
  - mvp
  - workshop
inputs: <record>/inception/requirements-analysis/requirements.md, <record>/inception/user-stories/stories.md (if produced), RE artifacts (if brownfield)
outputs: components.md, component-methods.md, services.md, component-dependency.md, decisions.md (under this stage's record dir, engine-resolved)
---

# Application Design

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-architect-agent persona from `agents/aidlc-architect-agent.md` and knowledge from `.kiro/knowledge/aidlc-architect-agent/`.
Load aidlc-aws-platform-agent persona from `agents/aidlc-aws-platform-agent.md` and knowledge from `.kiro/knowledge/aidlc-aws-platform-agent/` for AWS service mapping.
Load aidlc-design-agent persona from `agents/aidlc-design-agent.md` and knowledge from `.kiro/knowledge/aidlc-design-agent/` for UI component specifications and UX-informed design constraints.

### Step 2: Load Prior Context

