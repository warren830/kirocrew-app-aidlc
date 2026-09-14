---
slug: deployment-execution
phase: operation
execution: CONDITIONAL
condition: Execute after deployment pipeline and environment are ready
lead_agent: aidlc-pipeline-deploy-agent
support_agents:
  - aidlc-developer-agent
mode: inline
produces:
  - deployment-log
  - smoke-test-results
  - health-check-report
  - deployment-execution-questions
consumes:
  - artifact: cd-config
    required: true
  - artifact: deployment-strategy
    required: true
  - artifact: environment-inventory
    required: true
  - artifact: build-test-results
    required: true
requires_stage:
  - deployment-pipeline
  - environment-provisioning
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - infra
  - security-patch
  - workshop
inputs: CD pipeline config from deployment-pipeline stage, provisioned environments from environment-provisioning stage, built artifacts from Construction
outputs: deployment-log.md, smoke-test-results.md, health-check-report.md, deployment-execution-questions.md (under this stage's record dir, engine-resolved)
---

# Deployment Execution

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-pipeline-deploy-agent persona from `agents/aidlc-pipeline-deploy-agent.md` and knowledge from `.kiro/knowledge/aidlc-pipeline-deploy-agent/`.

### Step 2: Load Prior Context

- Read CD pipeline config and deployment strategy from `<record>/operation/deployment-pipeline/` (if they exist)
- Read environment inventory from `<record>/operation/environment-provisioning/` (if exists)
- Read build/test results from `<record>/construction/build-and-test/` (if exists)
- Read rollback runbook (if exists)

Incremental scopes (security-patch, infra) skip environment-provisioning or build-and-test by design; a brownfield production system already has environments and a deploy path. When those inputs are absent, inventory the actual environments from the workspace's existing configuration and deploy through the pipeline that exists — never invent the content of a missing artifact.

### Step 3: Pre-Deployment Checks

