# AI-DLC Studio

A KiroCrew App that makes [AI-DLC Workflows](https://github.com/awslabs/aidlc-workflows) observable and
operable across all of your repositories — without taking any authority away from it.

The bundled AI-DLC workflow model includes five phases, 33 stages, domain-expert agents, approval
gates, structured questions, reviewers, artifacts and an append-only audit trail. The selected plan
determines which stages and gates apply.

Studio provides a UI to observe and operate that lifecycle across registered repositories.
**Confirmed Run/Resume commands, approvals and answers travel through the intent's own AI-DLC
conversation. AI-DLC agents and the engine perform the work, enforce workflow rules and update the
workflow files; Studio reads the results and refreshes the interface.**

Studio also invokes allowlisted engine commands for setup and administration, including intent
creation, plan and configuration changes, diagnostics, and space or intent selection. These
engine-managed operations may update state, append audit records or move the active-intent cursor.
Studio does not hand-edit workflow authority files (`aidlc-state.md`, audit shards, questions files
or selection cursors), fabricate approval or human-turn evidence, directly invoke protected workflow
transition commands from its backend, or set guard-bypass environment variables.

## What you get

- **Action Center** — one queue of everything waiting on a human across every registered repository:
  approval gates, structured questions and unresolved audit-backed choices, missing inputs, recovery incidents, failures, circuit breakers,
  install conflicts. Ordered by operational priority, oldest first within a priority.
- **Decisions next to their evidence** — a gate shows the produced artifacts, the stage's acceptance
  criteria, reviewer verdicts and findings, unresolved risks and prior revisions before you answer. The
  confirmation shows both the button you clicked and the exact text that will be sent.
- **Workflow Map** — the current plan in phase swimlanes, with its agents, gates, artifacts and live
  state. Show all stages to inspect the full method; original stage numbers are preserved, and progress
  always counts the selected plan. Construction expands into per-unit lanes.
- **Repository registry and transactional installer** — choose a directory on the gateway or enter its path,
  preflight it read-only, install the bundled AI-DLC v2 harness with a receipt for every file it owns, and
  upgrade later with a previewed diff. Cancel an installation, restore the immediately preceding compatible
  engine from its backups, or uninstall owned harness content while keeping workflow data and user files.
  Uninstall previews explicitly retain changed configurable files and fragments with uncertain ownership.
  Failures restore the transaction's starting state or require explicit recovery.
- **Plan composer** — pick scope, depth, review intensity and test strategy, see the five-phase stage matrix
  with dependency validation, exact stage/gate/artifact counts and honest estimate ranges, then create the
  intent paused.
- **Workspace controls** — preview changes to an existing intent's scope, depth and test strategy; create
  and switch spaces through the engine. Rename or archive repository registrations and clean up selected
  historical transaction directories while protecting active recovery and current rollback backups.
- **Bun configuration** — select and verify an executable or repeat automatic detection from Settings.
- **Evidence trail** — a readable timeline that always says whether an event came from AI-DLC, KiroCrew,
  Git or Studio, with a drawer onto the raw audit block behind it.
- **English and Simplified Chinese**, light and dark, keyboard reachable, and usable at 390px for the
  decision loop.

## Requirements

| | |
|---|---|
| KiroCrew | 0.3.0 or newer (`minKiroCrewVersion`) |
| Platform | macOS or Linux |
| For running AI-DLC in a repository | [Bun installed on the gateway](#install-bun-for-ai-dlc), a signed-in `kiro-cli`, and a paid Kiro plan for the models AI-DLC expects |
| For Git observation | `git` on `PATH` (optional; Studio degrades to "Git unavailable" without it) |

Studio itself opens no port, no tunnel and no external network connection. It inherits the KiroCrew
Gateway's binding, authentication and remote-access policy.

## Install

[中文安装说明](docs/installation.zh-CN.md)

### Install Bun for AI-DLC

AI-DLC uses Bun to run its TypeScript tools. Install it **on the machine running the KiroCrew
Gateway, as the same operating-system user**. If the gateway is remote, run these commands there.
Node.js is still required by Studio's installation helper.

On macOS (13 or newer) or Linux, use the [official Bun installer](https://bun.com/docs/installation):

```bash
curl -fsSL https://bun.com/install | bash

# Make the default installation available in this terminal immediately.
export PATH="$HOME/.bun/bin:$PATH"
bun --version
command -v bun
```

Linux needs `unzip` for this installer. On Debian/Ubuntu, install missing prerequisites with
`sudo apt install curl unzip`; on other distributions, use their package manager.

If you already use Homebrew, this is an alternative installation method:

```bash
brew install oven-sh/bun/bun
bun --version
command -v bun
```

If a new terminal reports `bun: command not found`, add `export PATH="$HOME/.bun/bin:$PATH"` to
`~/.zshrc` for zsh or `~/.bashrc` for Bash (`~/.bash_profile` for a login shell), then reopen the terminal. This path applies to the official
installer's default location; use your actual installation directory if you customized it.

After Studio is installed, open **Settings → Bun executable → Detect Bun again**. If it still cannot find
Bun, paste the full path printed by `command -v bun` into **Absolute path to Bun**, then click
**Save and verify path**. Use the expanded absolute path, not a literal `~` or `$HOME`.
Desktop-launched gateways may have a different `PATH` from your terminal; these settings apply
immediately without restarting Studio.

### Install from GitHub

Start KiroCrew on the machine where you want the app installed. The installation helper needs Git,
Node.js, Bash, `curl` and `/usr/bin/python3`. The repository includes the built UI, so installing a copy
does **not** require `npm install` or a frontend build:

```bash
git clone https://github.com/warren830/kirocrew-app-aidlc.git
cd kirocrew-app-aidlc
bash scripts/dev-install.sh --no-build
```

The helper installs or updates this checkout in the **local running gateway**, grants trust to
`aidlc-studio` only, enables its backend, and prints hook and app health. Its defaults match an Apple
Silicon macOS desktop installation: port `5476` and the Python runtime bundled inside
`/Applications/KiroCrew.app`. Open **Apps → AI-DLC Studio** after installation; the final app-health
response should report `"status": "healthy"`.

For Linux, Intel macOS, or a source/virtual-environment installation, set `KC_PY` to the **same Python
interpreter that runs your KiroCrew gateway**. Set `KC_PORT` if the gateway uses another port:

```bash
# Replace the interpreter path with your gateway's actual Python executable.
KC_PY=/path/to/kirocrew/.venv/bin/python KC_PORT=5476 \
  bash scripts/dev-install.sh --no-build
```

Installing Studio adds the KiroCrew app. Installing the AI-DLC harness into a project is a separate,
explicit step in **Repos → Add repository → Install AI-DLC**, described under [First run](#first-run).

If your KiroCrew catalog already lists this app, **Discover → AI-DLC Studio → Install** is another
installation route. A public GitHub repository alone does not add an app to that catalog. Grant trust
to this app when prompted; blanket third-party trust is not needed.

### Update or rebuild

Run an update when no Studio operation is active: the helper briefly disables the app to reload its
backend hooks.

```bash
git pull --ff-only
bash scripts/dev-install.sh --no-build
```

To rebuild after editing the UI, install the locked development dependencies first. The checked-in
Vite dependency requires Node.js `^20.19.0 || >=22.12.0`.

```bash
(cd ui && npm ci)
bash scripts/dev-install.sh       # builds ui/dist/index.mjs, then installs
```

`scripts/dev-install.sh --dev` additionally enables UI live reload. `scripts/kcapi.sh` is an authenticated
`curl` wrapper for the local gateway; use it to check an installation without changing it:

```bash
bash scripts/kcapi.sh GET /api/apps/aidlc-studio/health
```

If token creation or cookie exchange fails, check that KiroCrew is running, `KC_PORT` points to that
gateway, and `KC_PY` belongs to the same installation and operating-system user. The helper obtains local
gateway authentication itself; no API token needs to be copied into this repository.

## First run

1. **Repos → Add repository** and browse to a directory on the gateway or give an absolute path. Studio computes a stable identity for it (device
   and inode, falling back to the canonical path plus Git common directory) and refuses a second path that
   resolves to a repository already registered.
2. The preflight tells you what is there: platform, Git state, `bun`, existing harnesses, installed AI-DLC
   version, state versions, conflicts, permissions.
3. **Install AI-DLC** if the repository has none. You see every managed path and every conflict before
   anything is written. Files the framework owns are recorded with their digests in a receipt; your
   `AGENTS.md`, `.gitignore` and `.kiro/settings/*.json` are merge targets where Studio owns only its own
   fragment and leaves everything else — including secrets — untouched.
4. **New intent** walks Work → Preset → Plan → Review. Creation also compiles the runtime graph; it never starts a workflow turn. If compilation fails after creation, retry it on the existing intent from the wizard without creating a duplicate.
5. **Run to next checkpoint** takes the intent to its first human boundary, which then appears in the
   Action Center.

## What Studio deliberately does not do

- It does not reimplement AI-DLC's stages, and it does not treat KiroCrew state as authoritative over
  AI-DLC's disk state.
- It does not author or reorder stage definitions or DAG edges.
- It does not edit artifacts. "Request changes" sends your feedback to the conversation; the AI revises.
- It does not run a single Git write command. Branch, commit, push, merge, checkout and friends are refused
  at two layers, and a test greps the backend to keep it that way. "Ask AI to prepare a commit" is a request
  to the conversation.
- It does not discover or register repositories automatically. Directory browsing reads one explicitly
  requested directory at a time; registration still requires a preflight and confirmation.
- It does not delete intents. Archive is reversible and changes only Studio's own view.
- **It does not run your workflow unattended.** Every KiroCrew path that delivers text to a session is a
  user turn as far as AI-DLC's anti-forgery guard is concerned, and Studio will not pretend otherwise. So
  `Keep moving`, the night work window and automatic session failover ship **disabled**, with the reason
  shown in Settings, until a host-authenticated machine lane exists that provably does not mint human
  presence. See [docs/design/architecture.md](docs/design/architecture.md) §3.2.

## How a decision reaches AI-DLC

```
you click Approve  →  Studio shows the exact text that will be sent  →  you confirm
   →  Studio validates the captured evidence and acquires the repository's execution lease
   →  Studio uses the engine to select the intended space/intent when needed, then verifies the cursor
   →  Studio durably records the pending delivery
   →  your own dashboard session sends that text to the intent's AI-DLC conversation
   →  AI-DLC's input hook records the human turn, and its conductor reads your answer
   →  the conductor invokes the engine, which validates the decision and commits the transition
   →  Studio watches the audit trail and the state file, and only then marks the decision resolved
```

Repeating this interaction at each required checkpoint supports the full selected lifecycle:
AI-DLC owns stage execution and workflow state; Studio owns the user interface, delivery tracking
and repository coordination. The input hook must run to establish human-turn evidence.

A 2xx response never means AI-DLC changed. Studio resolves an action only when it observes the evidence that
action's contract requires — a `GATE_APPROVED` block and the stage checkbox moving, for a gate approval. If
delivery is uncertain, Studio says so and asks you to decide, rather than sending anything twice: a human
decision is at-most-once after it might have been delivered.

## How setup and administration work

For engine-managed setup and administration, Studio invokes supported, allowlisted operations under
repository leases. Creating an intent, changing its plan, scope or configuration, compiling its
runtime, running Doctor, and selecting a space or intent do not require an agent to interpret a chat
message. The engine performs the corresponding writes, including state or audit changes where that
operation requires them.

Intent selection also happens as a prerequisite to a confirmed workflow submission: Studio can ask
the engine to move the active-intent cursor, then reads it back before sending the decision.
Protected workflow transitions such as approving a stage or advancing the lifecycle remain on the
AI-DLC conversation path described above.

Studio writes its own registry, delivery records, leases and preferences to its own storage. Its
transactional installer writes managed harness files and records their ownership in receipts.
These records support operation and recovery; AI-DLC's workflow files remain authoritative for
lifecycle progress.

## Bundled AI-DLC

`payload/aidlc-kiro/` holds the AI-DLC Workflows Kiro CLI distribution, version **2.7.1**, with
Studio-maintained local patches documented in `THIRD_PARTY_NOTICES.md`, under the MIT No Attribution
licence (`payload/AIDLC-LICENSE`). Every file is inventoried with its
SHA-256 in `payload/manifest.json`, which is also what the installer verifies before and after writing.
Studio's own version and the bundled AI-DLC version are shown separately in Settings. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Permissions this app requests

| Declared | Why |
|---|---|
| `api: /api/apps/aidlc-studio` | its own backend routes |
| `api: /api/chat` | the human-submission lane: your browser session creates the intent's canonical session and sends your confirmed decision text to it |
| `api: /api/ask-question` | reads pending structured question cards when the host has any, so a question can be answered without leaving Studio |
| `events` | three Studio event names for live queue updates |
| `storage` | Studio's own registry, action records, receipts and preferences |
| `spawn` | the AI Advisor, an on-demand read-only agent with `thinking` as its only tool |
| `network: false`, `cron: false` | not used |

The Advisor never writes, never runs a command, never opens your repository as its working directory, and
never submits anything. It analyses an evidence package Studio passes to it and hands back a draft.

## Documentation

| Document | Contents |
|---|---|
| [docs/AI-DLC-Studio-PRD.md](docs/AI-DLC-Studio-PRD.md) | the product requirements this app implements |
| [docs/design/architecture.md](docs/design/architecture.md) | module layout, authority model, the two lanes, storage, decision log |
| [docs/design/contracts.md](docs/design/contracts.md) | binding module, API, frontend and test contracts |
| [docs/research/](docs/research/) | the verified facts about the KiroCrew host and the AI-DLC on-disk model that the design rests on |
| [tests/fixtures/FORMAT-NOTES.md](tests/fixtures/FORMAT-NOTES.md) | the real AI-DLC file formats, version by version |

## Development

```bash
# backend tests (kiro_crew importable from the KiroCrew checkout's venv)
/path/to/KiroCrew/.venv/bin/python -m pytest tests -q -o addopts=

# frontend
cd ui && npm run check      # tsc --noEmit && vitest run && vite build

# regenerate the bundled payload inventory after changing payload/aidlc-kiro/
python3 scripts/build_payload_manifest.py --version 2.7.1 \
  --source-ref "v2.7.1 @ a277af21 + Studio plan-progress, review-appendix and requirement-traceability compatibility patches" \
  --source-commit a277af218f0df7f325d3b8be7b6d90fce2c5bd40 --state-versions 8
```

Keep `--source-commit` pinned to the upstream base and describe local patches in `--source-ref`
and `THIRD_PARTY_NOTICES.md`; a local fix is not an upstream release. Regenerate the manifest rather
than editing file hashes by hand.

## Licence

Apache-2.0 (`LICENSE`). The bundled AI-DLC distribution is MIT-0, with the local changes noted above.
