---
slug: intent-capture
phase: ideation
execution: ALWAYS
condition: First stage of every workflow — establishes the initiative's foundation
lead_agent: aidlc-product-agent
support_agents:
  - aidlc-architect-agent
mode: inline
produces:
  - intent-statement
  - stakeholder-map
  - intent-capture-questions
consumes: []
requires_stage: []
sensors:
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
- Load guardrails from `.kiro/steering/`

### Step 3: Generate Clarifying Questions

Create `<record>/ideation/intent-capture/intent-capture-questions.md` with questions:
- What business problem are we solving?
- Who is the customer (internal/external)? What pain are they experiencing?
- What does success look like? What metrics matter?
- What is the trigger for this initiative (market pressure, tech debt, regulation, opportunity)?

Use the [Answer]: tag format from stage-protocol.md. Include A-E options with X (Other) as final option. Leave all [Answer]: tags blank.

Then follow the unified question flow from stage-protocol.md section 3: offer Guide Me / Edit File / Chat modes.

### Step 4: Collect and Analyze Answers

After all answers collected:
1. Confirm ALL [Answer]: tags are filled in
