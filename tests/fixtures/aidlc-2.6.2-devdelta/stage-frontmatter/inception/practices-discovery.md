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
mode: subagent
summary_confirmation: required
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
inputs: <record>/aidlc-state.md + (brownfield) reverse-engineering evidence
outputs: "team-practices.md, discovered-rules.md, evidence.md, practices-discovery-timestamp.md, plus one contribution file per support agent. On affirmation, content is promoted to aidlc/spaces/<active-space>/memory/team.md and project.md."
---

# Practices Discovery

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

This stage discovers how the team works: way of working, walking-skeleton
stance, testing posture, deployment, and code style. It is a hub-and-spoke
ensemble. The pipeline-deploy lead drafts; quality, developer, and devsecops
inspect the draft independently; the human resolves the practice choices; and
