---
slug: feedback-optimization
phase: operation
execution: CONDITIONAL
condition: Execute when ongoing operational monitoring and optimization are needed
lead_agent: aidlc-operations-agent
support_agents:
  - aidlc-aws-platform-agent
mode: inline
summary_confirmation: required
produces:
  - slo-report
  - cost-analysis
  - drift-report
  - feedback-loop
  - feedback-optimization-questions
consumes:
  - artifact: dashboards
    required: true
  - artifact: alarms
    required: true
  - artifact: slo-config
    required: true
  - artifact: deployment-log
    required: true
  - artifact: load-test-results
    required: false
  - artifact: incident-plan
    required: false
requires_stage:
  - observability-setup
  - deployment-execution
  - incident-response
  - performance-validation
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - workshop
inputs: All Operation phase artifacts, production monitoring data
outputs: slo-report.md, cost-analysis.md, drift-report.md, feedback-loop.md, feedback-optimization-questions.md (under this stage's record dir, engine-resolved)
---

# Continuous Feedback & Optimization

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-operations-agent persona from `agents/aidlc-operations-agent.md` and knowledge from `.kiro/knowledge/aidlc-operations-agent/`.

### Step 2: Load Prior Context

- Read observability setup from `<record>/operation/observability-setup/`
- Read performance validation results from `<record>/operation/performance-validation/`
- Read SLO/SLI configuration
