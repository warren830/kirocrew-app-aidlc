---
slug: build-and-test
phase: construction
execution: ALWAYS
condition: Always executes once after all per-unit stages are finished.
lead_agent: aidlc-quality-agent
support_agents:
  - aidlc-devsecops-agent
mode: inline
produces:
  - build-instructions
  - integration-test-instructions
  - performance-test-instructions
  - security-test-instructions
  - build-and-test-summary
  - build-test-results
  - cross-unit-traceability
consumes:
  - artifact: code-generation-plan
    required: true
  - artifact: unit-test-instructions
    required: true
  - artifact: code-summary
    required: true
requires_stage:
  - code-generation
sensors:
  - required-sections
  - upstream-coverage
  - type-check
scopes:
  - enterprise
  - feature
  - mvp
  - poc
  - bugfix
  - refactor
  - security-patch
  - workshop
inputs: ALL code generation outputs across all units
outputs: build-instructions.md, integration-test-instructions.md, performance-test-instructions.md, security-test-instructions.md, build-and-test-summary.md, test-results.md, cross-unit-traceability.md (under this stage's record dir, engine-resolved)
---

# Build and Test

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Personas

Load aidlc-quality-agent (lead) persona from `agents/aidlc-quality-agent.md` and knowledge from `.kiro/knowledge/aidlc-quality-agent/`. Load aidlc-devsecops-agent persona from `agents/aidlc-devsecops-agent.md` and knowledge from `.kiro/knowledge/aidlc-devsecops-agent/` for security testing input. Apply aidlc-quality-agent as the primary perspective with aidlc-devsecops-agent providing security testing expertise.

### Step 2: Analyze Testing Requirements

Read code generation outputs across all units from `<record>/construction/*/code-generation/code-summary.md` and per-unit test instructions from `<record>/construction/*/code-generation/unit-test-instructions.md`. Review NFR requirements across units (if they exist) to identify performance and security testing needs. Catalog all test types required.

### Step 3: Generate Build Instructions

Create `<record>/construction/build-and-test/build-instructions.md`:
