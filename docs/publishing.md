# Publishing AI-DLC Studio

Two independent acceptances gate a public release (PRD §24.2), and neither has a promised date:

1. **AI-DLC upstream** accepts the integration directory `integrations/kirocrew/aidlc-studio` in
   `awslabs/aidlc-workflows` — or explicitly rejects it, at which point this directory moves to an
   independent public repository **without changing the slug, the storage schema or the registry
   contract**. Maintainer silence is not a rejection (D23).
2. **The KiroCrew official App registry** merges the listing, and a clean user can install it from
   Discover and complete the smoke journey.

Everything below is what this repository must have ready before either review starts. `scripts/check.sh`
enforces the mechanical half.

## 1. Registry entry

The official registry is a JSON catalog of app entries; a monorepo app is listed with a `subdirectory`.
The entry this app needs, once the upstream location is settled:

```json
{
  "name": "aidlc-studio",
  "displayName": "AI-DLC Studio",
  "description": "Cross-repository Action Center, Workflow Map and transactional installer for AI-DLC Workflows.",
  "gitUrl": "https://github.com/awslabs/aidlc-workflows",
  "branch": "main",
  "subdirectory": "integrations/kirocrew/aidlc-studio",
  "author": "ychchen",
  "license": "Apache-2.0",
  "tags": ["aidlc", "developer-tools", "workflows", "automation"],
  "builtin": false
}
```

Two things must stay true in that entry:

- **`builtin: false` and narrow trust.** The listing must never imply blanket third-party trust. A user
  installing from Discover grants trust to `aidlc-studio` alone; the README says so, and no instruction
  anywhere tells a user to set `agent.apps_allow_third_party`.
- **Asset paths are repository-relative.** `registry.py` rewrites `iconPath`, `heroImage*` and
  `screenshots` into blob-proxy URLs so the artwork resolves before the app is installed. Absolute
  `/apps/aidlc-studio/ui/...` paths work only for a locally installed app and break in Discover, so the
  published manifest keeps the relative form. (Whether the proxy resolves those paths relative to the
  repository root or to `subdirectory` is the one thing that cannot be verified from this machine — check
  it against a real Discover render before merging.)

## 2. Assets

| Asset | File | Requirement |
|---|---|---|
| Store icon | `assets/icon-512.png` | 512×512, opaque, no text |
| Small icon | `assets/icon-256.png` | 256×256, opaque |
| Sidebar icon | `ui/icon.svg` | monochrome, `currentColor`, renders legibly at 16px |
| Hero | `assets/hero-light.svg`, `assets/hero-dark.svg` | 16:9, one per theme polarity |
| Detail banner | `assets/hero-detail-light.svg`, `assets/hero-detail-dark.svg` | 25:6 |
| Screenshots | `assets/screenshots/*.png` | landscape ≈1200px wide; **light and dark for each flow** |

Screenshots to capture, in both themes and both languages (PRD §18 quality gates):

1. Action Center with a real gate selected, showing the artifact, the acceptance criteria and the
   reviewer findings.
2. The confirmation panel with the exact wire text visible.
3. Workflow Map, Detailed density, one stage selected with its dependency overlay.
4. Repos with an install preview open, showing managed paths and one conflict.
5. New-intent wizard, Review step, with exact counts and labelled estimate ranges.
6. Action Center at 390px, list and detail.

Screen recordings are required for the wizard, a gate decision, a recovery flow and the mobile
multi-step flow.

## 3. Documentation

- `README.md` — what it is, requirements, install, first run, the explicit non-goals (including that
  unattended automation ships disabled and why), the permission table with a reason per entry, the
  bundled AI-DLC version and licence.
- `THIRD_PARTY_NOTICES.md` — the bundled AI-DLC distribution, its MIT-0 licence, version, source commit
  and the fact that it is unmodified.
- `LICENSE` — Apache-2.0, matching `app.json`.
- `docs/design/architecture.md`, `docs/design/contracts.md` — how it works and what it promises, for a
  reviewer who needs to check the security claims rather than take them.
- `docs/research/` — the verified host and engine facts the design rests on. A reviewer asking "how do
  you know the machine lane is unsafe?" is answered here, with file and line citations.

## 4. What a security reviewer will ask, and where the answer is

| Question | Answer |
|---|---|
| Can this app edit AI-DLC's state, audit or cursor files? | No. Only the engine's own allowlisted verbs mutate anything, under a repository admin lease; `tests/test_engine.py` proves the allowlist and a static test greps the backend for the forbidden tools. |
| Can it forge a human approval? | No. Every decision is a prompt the user's own dashboard session sends; the backend never calls a host dispatch function. The reconciler asserts that exactly one `HUMAN_TURN` follows a human dispatch and that none follows anything else. |
| Can a decision be sent twice? | No. The `Delivering` record is committed durably before the host is called, delivery is at-most-once after that, `NotDelivered` requires the backend's own proof, and nothing is ever replayed automatically. |
| Can it run git writes? | No. Two layers refuse: an argv allowlist at call time and a static grep in CI. Global options that could redirect git are refused too. |
| Can it read outside a registered repository? | Repository artifact reads are contained within the registered root. The owner can explicitly browse a single gateway directory when adding a repository; that lists only directory names and paths, with bounded results. Bun configuration probes the explicitly chosen executable, and maintenance reads only Studio's own transaction directories. |
| Can an artifact execute something? | No. Markdown renders through the host's allowlist sanitiser with mermaid in strict mode; the bundle contains no `dangerouslySetInnerHTML` (CI greps for it). |
| What does it send anywhere? | Nothing external. `network: false`, no listener, no tunnel; Slack messages go through the host's own client with bounded excerpts and dashboard-internal links only. |
| What does the Advisor see? | A bounded, redacted evidence package passed in-band to an agent whose only tool is `thinking`, never bound to the repository, and its round trip is asserted to leave AI-DLC's files byte-identical. |

## 5. Release checklist

Mechanical (run `scripts/check.sh`):

- [ ] `app.json` validates against `AppManifest` on both the 0.3.0 and the bundled gateway
- [ ] `app.json` `extra.bundledAidlc.engineVersion` equals `payload/manifest.json` `engineVersion`
- [ ] payload digests all match and no unlisted file is in `payload/aidlc-kiro/`
- [ ] backend suite green, and the backend imports under the gateway's own interpreter
- [ ] generated UI sources in sync with the backend (wire text, error codes, enums)
- [ ] both i18n catalogs complete, key sets identical, every error code covered
- [ ] UI typecheck, tests and production build clean; bundle parses; no literal colour, no raw HTML

By hand (`docs/manual-e2e-runbook.md` has the commands and the pass criterion for each of these,
in Chinese):

- [ ] Clean install from a local path: install → narrow trust → enable → healthy hooks → `/health` reports
      the payload and the capability block
- [ ] Add a repository, install AI-DLC into it, create an intent, inspect the plan, run to the first
      human boundary, decide the gate, and see the state file and audit move
- [ ] Reject and revise once, and see `[?]` become `[R]` and the gate reopen
- [ ] Injected transport loss after `Delivering`: no second send, and the action reaches a legal state
- [ ] Injected installer failure at each step: the previous installation stays complete, or recovery is
      required and blocks execution
- [ ] Archive and restore change only Studio's view (repository bytes identical)
- [ ] macOS and Linux both pass the above
- [ ] Light/dark and `en-US`/`zh-CN` screenshots captured
- [ ] Three context-isolated reviewers complete add repo → plan → run → decide a gate; ≥80% without
      intervention, nobody needing more than one assist, every repeated confusion fixed
- [ ] Accessibility: automated checks plus a keyboard-only pass through the decision loop

Not claimable in v1, and the README says so:

- unattended continuation, the night work window, credit budgets, and automatic stable-boundary failover
  (no host-authenticated machine lane exists — PRD P-08/S12)
- Slack quick actions (every option in a host options block is re-dispatched as a user turn)
- Windows support (no real end-to-end validation)
