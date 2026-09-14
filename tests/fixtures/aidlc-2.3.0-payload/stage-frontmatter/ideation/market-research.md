---
slug: market-research
phase: ideation
execution: CONDITIONAL
condition: Execute when initiative has external market positioning or build-vs-buy considerations. Skip for internal tools, bug fixes, or refactors.
lead_agent: aidlc-product-agent
support_agents: []
mode: inline
produces:
  - competitive-analysis
  - market-trends
  - build-vs-buy
  - market-research-questions
consumes:
  - artifact: intent-statement
    required: true
requires_stage:
  - intent-capture
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
inputs: Intent statement from intent-capture stage
outputs: competitive-analysis.md, market-trends.md, build-vs-buy.md, market-research-questions.md (under this stage's record dir, engine-resolved)
---

# Market Research & Competitive Analysis

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

## Steps

### Step 1: Load Agent Personas

Load aidlc-product-agent persona from `agents/aidlc-product-agent.md` and knowledge from `.kiro/knowledge/aidlc-product-agent/`.

### Step 2: Load Prior Context

- Read intent statement from `<record>/ideation/intent-capture/`
- Identify market-relevant aspects of the initiative

### Step 3: Generate Clarifying Questions

Create `<record>/ideation/market-research/market-research-questions.md` with questions:
- What competing products or solutions exist in the market?
- What are their strengths, weaknesses, and pricing models?
- What industry trends or regulatory shifts are relevant?
- What do customers expect as table-stakes vs. differentiators?
- For internal initiatives: are there existing tools, SaaS products, or open-source alternatives?
- What is the build-vs-buy-vs-partner calculus?
- What market size or addressable audience are we targeting?

Follow stage-protocol.md question flow (Guide Me / Edit File / Chat).

### Step 4: Collect and Analyze Answers

Run ambiguity detection and contradiction analysis on all answers.

