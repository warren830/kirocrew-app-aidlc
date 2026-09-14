---
slug: observability-setup
phase: operation
execution: CONDITIONAL
condition: Execute when monitoring, dashboards, alarms, or tracing need configuration
lead_agent: aidlc-operations-agent
support_agents: []
mode: inline
summary_confirmation: required
produces:
  - dashboards
  - alarms
  - slo-config
  - log-queries
  - tracing-config
  - anomaly-config
  - observability-setup-questions
consumes:
  - artifact: performance-design
    required: true
  - artifact: security-design
    required: true
  - artifact: reliability-design
    required: true
  - artifact: monitoring-design
    required: true
  - artifact: infrastructure-specification
    required: true
requires_stage:
  - nfr-design
  - infrastructure-design
  - deployment-execution
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - infra
  - workshop
inputs: NFR design from nfr-design stage, infrastructure design from infrastructure-design stage, deployed application
outputs: dashboards.md, alarms.md, slo-config.md, log-queries.md, tracing-config.md, anomaly-config.md, observability-setup-questions.md (under this stage's record dir, engine-resolved)
---

# Observability Setup

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-operations-agent persona from `agents/aidlc-operations-agent.md` and knowledge from `.kiro/knowledge/aidlc-operations-agent/`.

### Step 2: Load Prior Context

- Read NFR design (observability strategy) from `<record>/construction/nfr-design/`
- Read infrastructure design from `<record>/construction/infrastructure-design/`
- Read deployment execution log from `<record>/operation/deployment-execution/`

