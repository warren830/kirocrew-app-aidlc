---
slug: contract-design
phase: inception
execution: CONDITIONAL
condition: Execute when the system has any formal contract to pin down — an inter-unit boundary (more than one unit that must integrate) OR a unit that exposes a public/external API consumed outside the system. Skip only for a single self-contained unit with no inter-unit boundaries and no externally consumed API.
lead_agent: aidlc-architect-agent
support_agents:
  - aidlc-aws-platform-agent
mode: inline
summary_confirmation: required
reviewer: aidlc-architecture-reviewer-agent
reviewer_max_iterations: 2
review_class: advisory
produces:
  - contract-summary
consumes:
  - artifact: unit-of-work
    required: true
  - artifact: unit-of-work-dependency
    required: true
  - artifact: components
    required: false
  - artifact: requirements
    required: false
requires_stage:
  - units-generation
sensors:
  - required-sections
  - upstream-coverage
scopes:
  - enterprise
  - feature
  - mvp
  - workshop
inputs: <record>/inception/units-generation/unit-of-work.md, <record>/inception/units-generation/unit-of-work-dependency.md, <record>/inception/domain-design/components.md (if produced), <record>/inception/requirements-analysis/requirements.md
outputs: contract-summary.md (under this stage's record dir, engine-resolved) — a human-readable overview of every contract (inter-unit boundaries and public/external APIs), each with a fenced spec block (OpenAPI / AsyncAPI / shared schema) inline
---

# Contract Design

MANDATORY: Follow stage-protocol.md for approval gates, question format, and completion messages.

Define the formal contracts the system must honour so teams can build in parallel with confidence. A contract is a formal agreement across a boundary: what data crosses it, in what shape, via what protocol, and what happens when things go wrong. Two kinds of boundary qualify:

- **Inter-unit boundaries** — the agreement between a provider unit and a consumer unit inside the system. Treat each like a B2B agreement between two teams in two companies: it must be right from the start, because a wrong contract turns integration into a rework disaster.
- **Public/external API boundaries** — the agreement between a unit and a consumer *outside* the system (another team, a partner, the public internet). A single-unit system with no inter-unit edges still needs this contract pinned before Code Generation when it exposes such an API; there is no other stage that owns the external API specification.

This stage runs once per workflow (not per unit) — it maps the whole set of boundaries at once, using the dependency DAG from Units Generation to know which units talk to each other, plus each unit's externally consumed surface for public API contracts.

## Steps

### Step 1: Load Agent Personas

Load aidlc-architect-agent persona from `agents/aidlc-architect-agent.md` and knowledge from `.kiro/knowledge/aidlc-architect-agent/`.
Load aidlc-aws-platform-agent persona from `agents/aidlc-aws-platform-agent.md` and knowledge from `.kiro/knowledge/aidlc-aws-platform-agent/` for integration-mechanism awareness (sync REST vs. async messaging vs. shared store).

### Step 2: Load Prior Context

- Read `<record>/inception/units-generation/unit-of-work.md` (unit definitions and kinds)
- Read `<record>/inception/units-generation/unit-of-work-dependency.md` (the dependency DAG — every edge is a candidate contract)
