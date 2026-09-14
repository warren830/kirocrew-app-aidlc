---
name: review-major-remediation
depth: Standard
keywords: []
description: Remediate confirmed review findings across heterogeneous subsystems
skeleton: off
---

# review-major-remediation scope

Standard depth for shipping a set of already-localized review findings that
do NOT share a subsystem. Composed for the DevDelta review's three remaining
majors — no TLS on the transport path, manager reads that are not team-scoped,
and a frontend mock fallback that fabricates data in production — where a
completed multi-agent review had already pinned every defect to a file and
line, but the three fixes land on deployment config, backend authorization
SQL, and frontend behaviour respectively.

The report IS the captured intent, so the whole ideation phase is SKIP. What
this scope keeps beyond a stock incremental fix is the decomposition and the
two design stages that the heterogeneity forces.

## Why these stages, why skip those

Nearest stock scope is `security-patch` at grid distance 3, over the match
threshold, so this synthesizes. The three flips away from it are the point:

- **units-generation** EXECUTE — the findings are three units, not one. Each
  has a different verification story (redeploy-and-probe, Postgres-backed
  integration test, a frontend package with zero test files today), so they
  must be tracked and verified independently rather than as one patch.
- **functional-design** EXECUTE — team-scoping already exists in the codebase
  for person-level reads, but the funnel/projects aggregate has no manager
  filter at all. That needs a genuinely new team-scoped SQL shape, not a copy
  of the existing pattern, and getting it wrong leaks cross-tenant data.
- **infrastructure-design** EXECUTE — `security-patch` assumes a code-only
  fix. TLS is a deployment topology decision (termination point, cert story,
  rollout for already-deployed clients), which is design work before it is
  code.

Skipped for cause: all seven ideation stages, because the review report
already answers them. `domain-design` — no new components; every fix lands on
an existing, already-located surface. `nfr-design` — one security NFR family,
whose implementation lands in infrastructure-design and functional-design.
`practices-discovery` — conventions are visible in the 20 existing Go test
packages, with gofmt/vet already clean. `ci-pipeline` and
`environment-provisioning` — absent CI and an existing compose environment are
PRE-EXISTING debt the report did not ask to erect; these fixes ship without
them. `performance-validation`, `observability-setup`, `incident-response`,
`feedback-optimization` — no stated performance requirement, and manager reads
are already audit-logged.

Deployment stays EXECUTE (deployment-pipeline, deployment-execution) for the
same reason `security-patch` does: cleartext bearer tokens stay on the wire
until the new config is actually live. A fix that never deploys does not close
the finding.

## Membership

No keyword triggers — `keywords: []` is deliberate. This scope was composed
for one specific remediation and resolves only via
`--scope review-major-remediation`; it never participates in inference.
Making it inferable is a separate, explicit human choice.

13 stages execute: initialization (3), reverse-engineering,
requirements-analysis, units-generation, functional-design, nfr-requirements,
infrastructure-design, code-generation, build-and-test, deployment-pipeline,
deployment-execution. The remaining 20 are SKIP.

Expect brownfield fold advisories at validate time: functional-design and
units-generation run without a fresh `components` artifact (domain-design
folded), infrastructure-design without the six `nfr-design` artifacts (it
leans on nfr-requirements), deployment-pipeline without `ci-config` and
`quality-gates`, and deployment-execution without `environment-inventory`.
Those are the disclosed cost of the folds, not defects.
