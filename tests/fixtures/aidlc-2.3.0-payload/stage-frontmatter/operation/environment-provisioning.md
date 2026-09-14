---
slug: environment-provisioning
phase: operation
execution: CONDITIONAL
condition: Execute when AWS environments need provisioning or validation
lead_agent: aidlc-aws-platform-agent
support_agents:
  - aidlc-devsecops-agent
  - aidlc-compliance-agent
mode: inline
produces:
  - environment-inventory
  - validation-report
  - environment-provisioning-questions
consumes:
  - artifact: deployment-architecture
    required: true
  - artifact: infrastructure-services
    required: true
  - artifact: cd-config
    required: true
requires_stage:
  - infrastructure-design
  - deployment-pipeline
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - infra
  - workshop
inputs: Infrastructure design from infrastructure-design stage, CD pipeline config from deployment-pipeline stage
outputs: environment-inventory.md, validation-report.md, environment-provisioning-questions.md (under this stage's record dir, engine-resolved)
---

# Environment Provisioning

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-aws-platform-agent persona from `agents/aidlc-aws-platform-agent.md` and knowledge from `.kiro/knowledge/aidlc-aws-platform-agent/`.

### Step 2: Load Prior Context

- Read infrastructure design from `<record>/construction/infrastructure-design/`
- Read security requirements from `<record>/construction/nfr-requirements/`

### Step 3: Generate Clarifying Questions

Create questions file covering:
- Are all environments provisioned per Infra Design?
- Are VPCs, subnets, security groups, NACLs correct?
- Are secrets in Secrets Manager / Parameter Store correctly injected?
- Is cross-account / cross-VPC connectivity validated?

Follow stage-protocol.md question flow.
