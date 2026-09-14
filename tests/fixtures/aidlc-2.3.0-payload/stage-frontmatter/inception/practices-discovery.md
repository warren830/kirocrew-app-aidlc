---
slug: practices-discovery
phase: inception
execution: CONDITIONAL
condition: Always rerun for freshness. Brownfield discovers from evidence + reverse-engineering artifacts. Greenfield prompts user via structured questions using org.md defaults.
lead_agent: aidlc-pipeline-deploy-agent
support_agents:
  - aidlc-quality-agent
  - aidlc-developer-agent
  - aidlc-devsecops-agent
mode: inline
produces:
  - team-practices
  - discovered-rules
  - evidence
  - practices-discovery-timestamp
consumes:
  - artifact: code-structure
    required: false
    conditional_on: brownfield
  - artifact: technology-stack
    required: false
    conditional_on: brownfield
  - artifact: dependencies
    required: false
    conditional_on: brownfield
  - artifact: code-quality-assessment
    required: false
    conditional_on: brownfield
  - artifact: architecture
    required: false
    conditional_on: brownfield
  - artifact: business-overview
    required: false
    conditional_on: brownfield
requires_stage:
  - state-init
  - reverse-engineering
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - mvp
  - infra
  - workshop
inputs: <record>/aidlc-state.md + (brownfield) reverse-engineering's 8 artifacts
outputs: "team-practices.md, discovered-rules.md, evidence.md, practices-discovery-timestamp.md (4 artifacts under this stage's record dir, engine-resolved). On affirmation, content is promoted to the harness rule layer's aidlc-team.md and aidlc-project.md."
---

# Practices Discovery

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

This stage discovers how the team works — way of working, walking-skeleton stance, testing posture, deployment, code style — and at an affirmation gate promotes the affirmed content from per-workflow audit trail into team-authored harness config (`.kiro/steering/aidlc-team.md` and `.kiro/steering/aidlc-project.md`). This is the only stage that writes to both rows of the two-axis configuration model. The affirmation gate is what makes the cross-row write legitimate.

## Steps

### Step 1: Check Conditions
