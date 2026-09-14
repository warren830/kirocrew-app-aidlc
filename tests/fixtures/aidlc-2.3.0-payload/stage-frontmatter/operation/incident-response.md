---
slug: incident-response
phase: operation
execution: CONDITIONAL
condition: Execute when operational runbooks and incident response procedures are needed
lead_agent: aidlc-operations-agent
support_agents: []
mode: inline
produces:
  - runbooks
  - incident-plan
  - escalation-matrix
  - incident-response-questions
consumes:
  - artifact: dashboards
    required: true
  - artifact: alarms
    required: true
  - artifact: reliability-design
    required: true
  - artifact: security-design
    required: true
  - artifact: deployment-architecture
    required: true
requires_stage:
  - observability-setup
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - workshop
inputs: Observability setup from observability-setup stage, NFR design from nfr-design stage, infrastructure design from infrastructure-design stage
outputs: runbooks.md, incident-plan.md, escalation-matrix.md, incident-response-questions.md (under this stage's record dir, engine-resolved)
---

# Incident Response & Runbook Generation

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-operations-agent persona from `agents/aidlc-operations-agent.md` and knowledge from `.kiro/knowledge/aidlc-operations-agent/`.

### Step 2: Load Prior Context

- Read observability setup from `<record>/operation/observability-setup/`
- Read NFR design from `<record>/construction/nfr-design/`
- Read infrastructure design from `<record>/construction/infrastructure-design/`

### Step 3: Generate Clarifying Questions

Create questions file covering:
- What are the most likely failure modes?
- What are the escalation paths and on-call rotations?
- What automated remediation is possible?
- What are the communication procedures during incidents?
