---
slug: deployment-pipeline
phase: operation
execution: CONDITIONAL
condition: Execute when CD pipeline needs creation or significant modification
lead_agent: aidlc-pipeline-deploy-agent
support_agents: []
mode: inline
summary_confirmation: required
produces:
  - cd-config
  - deployment-strategy
  - rollback-runbook
  - deployment-pipeline-questions
consumes:
  - artifact: ci-config
    required: true
  - artifact: quality-gates
    required: true
  - artifact: infrastructure-specification
    required: true
  - artifact: cicd-pipeline
    required: true
requires_stage:
  - ci-pipeline
  - infrastructure-design
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - infra
  - security-patch
  - workshop
inputs: CI pipeline config from ci-pipeline stage, infrastructure design from infrastructure-design stage
outputs: cd-config.md, deployment-strategy.md, rollback-runbook.md, deployment-pipeline-questions.md (under this stage's record dir, engine-resolved)
---

# Deployment Pipeline Configuration

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-pipeline-deploy-agent persona from `agents/aidlc-pipeline-deploy-agent.md` and knowledge from `.kiro/knowledge/aidlc-pipeline-deploy-agent/`.

### Step 2: Load Prior Context

- Read CI pipeline config from `<record>/construction/ci-pipeline/` (if exists)
- Read infrastructure design from `<record>/construction/infrastructure-design/` (if exists)
- Read NFR design (deployment-related NFRs) from `<record>/construction/nfr-design/` (if exists)

Incremental scopes (security-patch) skip ci-pipeline and infrastructure-design by design; a brownfield production system already has CI and deployment infrastructure. When those inputs are absent, inspect the workspace's existing pipeline and infrastructure configuration (and the code knowledge base on brownfield) and design the CD path against what is actually deployed — never invent the content of a missing artifact.

### Step 3: Generate Clarifying Questions

Create questions file covering:
