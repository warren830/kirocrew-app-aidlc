# Code Generation — Plan Approval

Stage: `code-generation` · Unit: `health-probe` · Depth: Minimal · Scope: `poc`

---

## Plan Approval

Covers `code-generation-plan.md`.

Plan summary: add `GET /health` to the existing Express router, returning `200 {"db":"ok","queue":"ok"}` or `503` with the failing dependency named; reuse the existing `pg` pool and the AMQP channel already held by the worker module. No new dependencies, no schema changes, no deployment.

Tests: 6 unit tests with the existing Jest runner (happy path, db down, queue down, both down, timeout budget 800 ms, response shape).

- Approve Plan
- Request Changes

[Answer]: Approve Plan
