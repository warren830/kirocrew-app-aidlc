---
slug: intent-capture
phase: ideation
execution: ALWAYS
condition: First stage of every workflow — establishes the initiative's foundation
lead_agent: aidlc-product-agent
support_agents:
  - aidlc-architect-agent
mode: inline
summary_confirmation: required
reviewer: aidlc-product-lead-agent
reviewer_max_iterations: 2
review_class: advisory
produces:
  - intent-statement
  - stakeholder-map
  - intent-capture-questions
consumes: []
requires_stage: []
sensors:
  - claim-sources
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - mvp
  - poc
inputs: User's project description ($ARGUMENTS), scope selection
outputs: intent-statement.md, stakeholder-map.md, intent-capture-questions.md (under this stage's record dir, engine-resolved)
---

# Intent Capture & Framing

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-product-agent persona from `agents/aidlc-product-agent.md` and knowledge from `.kiro/knowledge/aidlc-product-agent/`.
Load aidlc-architect-agent persona from `agents/aidlc-architect-agent.md` for technical context perspective.

### Step 2: Load Prior Context

- Read user's project description from $ARGUMENTS or `<record>/audit/<host>-<clone>.md`
- Check for existing `<record>/` artifacts from prior sessions
- Load guardrails from
  `aidlc/spaces/<active-space>/memory/{org,team,project}.md`

### Step 3: Generate Clarifying Questions

Create `<record>/ideation/intent-capture/intent-capture-questions.md`.

Start the file with a `## Sources` register. Every source is a top-level
Markdown list item using exactly one of these forms:

```markdown
- [desc] Initial description: "<JSON-escaped verbatim project description>"
- [scope] Workflow-selected scope: `<scope>`.
