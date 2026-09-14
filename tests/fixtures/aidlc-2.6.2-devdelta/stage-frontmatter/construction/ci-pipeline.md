---
slug: ci-pipeline
phase: construction
execution: CONDITIONAL
condition: Execute when CI pipeline needs creation or significant modification. Skip if CI already exists and is adequate.
lead_agent: aidlc-pipeline-deploy-agent
support_agents: []
mode: inline
summary_confirmation: required
produces:
  - ci-config
  - quality-gates
  - ci-pipeline-questions
consumes:
  - artifact: code-summary
    required: true
  - artifact: build-and-test-summary
    required: true
  - artifact: build-test-results
    required: true
requires_stage:
  - build-and-test
sensors:
  - required-sections
  - upstream-coverage
  - linter
  - type-check
scopes:
  - enterprise
  - feature
  - mvp
  - infra
  - workshop
inputs: Code generation output from code-generation stage, build/test results from build-and-test stage
outputs: ci-config.md, quality-gates.md, ci-pipeline-questions.md (under this stage's record dir, engine-resolved)
---

# CI Pipeline

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-pipeline-deploy-agent persona from `agents/aidlc-pipeline-deploy-agent.md` and knowledge from `.kiro/knowledge/aidlc-pipeline-deploy-agent/`.

### Step 2: Load Prior Context

- Read build/test results from `<record>/construction/build-and-test/` (if exists)
- Read code summary from `<record>/construction/{unit-name}/code-generation/` (if exists)
- Read infrastructure design from `<record>/construction/infrastructure-design/` (if exists)
- Read workspace profile for existing CI configuration

Incremental scopes (infra) skip code-generation and build-and-test by design; when those inputs are absent, base the pipeline stages on the workspace's existing build/test setup (detected from the repo itself) instead — never invent the content of a missing artifact.

### Step 3: Generate Clarifying Questions

Create `<record>/construction/ci-pipeline/ci-pipeline-questions.md` with questions:
- What CI tool is in use (CodePipeline, CodeBuild, GitHub Actions, Jenkins)?
