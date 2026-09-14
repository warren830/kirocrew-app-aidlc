---
slug: performance-validation
phase: operation
execution: CONDITIONAL
condition: Execute when NFR performance targets need validation under load
lead_agent: aidlc-quality-agent
support_agents: []
mode: inline
produces:
  - load-test-plan
  - load-test-results
  - nfr-validation-matrix
  - performance-validation-questions
consumes:
  - artifact: performance-requirements
    required: true
  - artifact: scalability-requirements
    required: true
  - artifact: performance-design
    required: true
  - artifact: scalability-design
    required: true
  - artifact: dashboards
    required: true
requires_stage:
  - nfr-requirements
  - nfr-design
  - observability-setup
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - workshop
inputs: NFR requirements from nfr-requirements stage, NFR design from nfr-design stage, deployed application, observability data from observability-setup stage
outputs: load-test-plan.md, test-results.md, nfr-validation-matrix.md, performance-validation-questions.md (under this stage's record dir, engine-resolved)
---

# Performance Validation & Load Testing

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-quality-agent persona from `agents/aidlc-quality-agent.md` and knowledge from `.kiro/knowledge/aidlc-quality-agent/`.

### Step 2: Load Prior Context

- Read NFR requirements from `<record>/construction/nfr-requirements/`
- Read NFR design from `<record>/construction/nfr-design/`
- Read observability configuration from `<record>/operation/observability-setup/`

### Step 3: Generate Clarifying Questions

Create questions file covering:
- What are the expected traffic patterns (steady state, peak, burst)?
- What are the target latency percentiles (p50, p95, p99)?
