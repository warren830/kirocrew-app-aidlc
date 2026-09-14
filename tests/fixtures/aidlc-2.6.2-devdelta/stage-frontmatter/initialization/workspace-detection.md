---
slug: workspace-detection
phase: initialization
execution: ALWAYS
condition: Scans and classifies workspace — auto-proceeds (no approval gate)
lead_agent: orchestrator
support_agents: []
mode: inline
produces: []
consumes: []
requires_stage:
  - workspace-scaffold
sensors: []
scopes:
  - enterprise
  - feature
  - mvp
  - poc
  - bugfix
  - refactor
  - infra
  - security-patch
  - workshop
inputs: none (scans filesystem)
outputs: workspace classification (greenfield/brownfield), technology stack detection
---

# Workspace Detection

Runs deterministically inside `aidlc-utility init`. The detection rules in Step 3 below are the source of truth for the scanner's classification logic.

MANDATORY: Follow stage-protocol.md for state tracking and audit logging.

## Steps

### Step 1: Update State

1. Update `<record>/aidlc-state.md`: set `Current Stage` to `detecting workspace`
2. Mark workspace-detection as `[-]` in progress

### Step 2: Scan Workspace

The scanner walks the project directory one level deep plus known source directories (`src/`, `app/`, `lib/`, `pages/`, `components/`, `tests/`), excluding the harness directories (`.claude/`, `.kiro/`, `.codex/`, `.opencode/`, `.aidlc/`, `.cursor/`), `aidlc/`, `node_modules/`, `.git/`, `dist/`, `build/`, `.next/`, `target/`, `vendor/`.

Nested-project fallback: when NO top-level signal fires (the layout that would otherwise classify greenfield), the scanner then descends one level into each arbitrarily-named top-level subdirectory (skipping the excluded directories above, hidden dirs, and symlinks) and re-applies the same signal set rooted at that subdirectory. If any subdirectory looks brownfield, the workspace is classified brownfield and that subdirectory's languages/frameworks/build system are merged into the result. This catches a project whose source lives one container down (e.g. `wordbook/`, `backend/`) instead of at the root. The fallback is depth-1 only and never runs when the root already has a source signal.

Scan signals:
- Directory structure (top-level and key subdirectories)
- Configuration files (package.json, pom.xml, build.gradle, Cargo.toml, pyproject.toml, etc.)
- Build system files (Makefile, Dockerfile, docker-compose, CI/CD configs)
- Package/dependency files (lock files, vendor directories)
- Source code directories and their languages
- Repo metadata (`.gitmodules` submodule declarations)
- Test infrastructure (test directories, test config files, coverage config)
- Documentation (README, docs/, wiki/)

**Exclude from analysis** (framework scaffolding, not application code):
- The harness directory (`.claude/`, `.kiro/`, `.codex/`, `.opencode/`, `.aidlc/`, or `.cursor/`) — AI-DLC framework files (skills, agents, hooks, tools, knowledge)
- `aidlc/` — AI-DLC workspace root (the space tree at `aidlc/spaces/<space>/...`)
- `node_modules/`, `.git/`
