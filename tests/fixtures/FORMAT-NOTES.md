# AI-DLC on-disk format notes from the harvested fixtures

Audience: whoever implements `backend/studio/aidlc_reader.py` (parsers) and `consistency.py`. Everything below was
observed on real files on 2026-09-04 (DevDelta = AI-DLC 2.6.2 Kiro install, State Version 8; devlake = 2.1.1, State
Version 7) or read from the 2.3.0 engine source at tag `v2.3.0` of the `aidlc-workflows` checkout in the same
workspace. Quoted lines are verbatim fixture bytes, already scrubbed with a fixed placeholder vocabulary:
`/FIXTURE/repo` = the source repository root, `/FIXTURE/ws` = the workspace directory holding the sibling checkouts,
`/FIXTURE/home` = the account home. Each fixture directory has its own `README.md` with a per-file provenance table
(source path, transform, sizes, sha256) and the exact scrub replacements made. The harvester is
`tests/fixtures/harvest_fixtures.py`, run as `AIDLC_FIXTURE_WS=<workspace dir> python3 tests/fixtures/harvest_fixtures.py`
(re-runnable; deletes and rebuilds the three fixture dirs; never writes outside `tests/fixtures/`; carries no
machine-specific path literal by design).

## 0. Two surprises that affect the whole project, not just parsers

1. **The bundled payload changed under us during this task, and has been re-pinned once more since.** At the start of
   the session `payload/aidlc-kiro` was AI-DLC 2.3.0 (32 stage files, `AIDLC_VERSION = "2.3.0"`, files dated Jul 9).
   By the time the harvester ran it was **2.6.2** (33 stage files, `payload/manifest.json` said
   `"ref": "HEAD of aidlc/wt-v2-triage (v2.6.2)"`, `"compatibleStateVersions": [8]`, `"stageCount": 33`,
   `"fileCount": 265`; `payload/` mtime 2026-09-04 22:31), and at that moment its `stage-graph.json`, `harness.json`
   and `aidlc-version.ts` were all byte-identical to DevDelta's, only `scope-grid.json` differing (DevDelta has a
   10th, project-composed scope). On 2026-09-06 it was re-pinned again, to **2.7.1**:
   `"ref": "main @ a277af21 (v2.7.1)"`, commit `a277af218f0df7f325d3b8be7b6d90fce2c5bd40` of
   `awslabs/aidlc-workflows`, `"fileCount": 293`, with `"stageCount": 33` and `"compatibleStateVersions": [8]`
   unchanged. **After that swap only `harness.json` is still byte-identical to DevDelta's**: `stage-graph.json` gained
   `review_artifact`, `aidlc-version.ts` quotes a different version, and `scope-grid.json` now ships eleven keys
   against DevDelta's ten. The fixtures therefore trail the payload by one generation, which is the point of them —
   they are real installs of OLDER engines, and that is the compatibility the parsers have to prove. Consequences:
   (a) the fixture named `aidlc-2.3.0-payload` is sourced from `git:/FIXTURE/ws/aidlc-workflows@v2.3.0:dist/kiro`
   (the same 258-file tree the payload used to hold), not from `payload/`; (b) architecture §13 A01 and `app.json`
   `extra.bundledAidlc.engineVersion` are reconciled and both read 2.7.1, with contracts C21 as the single pin —
   every version-derived value in code, `/health`, `/payload` and tests is read from `payload/manifest.json`, never a
   literal; (c) tests that want pristine bundled engine data read `payload/aidlc-kiro` directly, so they follow every
   re-pin for free.
2. **A real credential was sitting in a real audit shard.** DevDelta's poc shard contains a `SUBAGENT_COMPLETED`
   message with `token 466ffdb5…` (48 hex chars, a demo login token). It is outside the copied tail, but the
   scrubber's `keyword_secret` rule exists because of it. Studio's redaction (`kiro_crew.security.redact_credentials`)
   must run on audit `Message`/`Details`/`Error`/`Command` values before they reach the UI, Slack or exports.

## 1. What was produced

```
tests/fixtures/
  FORMAT-NOTES.md                      this file
  harvest_fixtures.py                  reproducible harvester (sources + scrub rules + synthetic authoring)
  aidlc-2.6.2-devdelta/                83 files, ~353 KB   real 2.6.2 install, State Version 8, 33 stages (see its README)
    README.md
    aidlc/active-space  aidlc/.aidlc-clone-id  aidlc/.aidlc-turn-counter
    aidlc/spaces/default/intents/{intents.json,active-intent}
    aidlc/spaces/default/intents/260814-review-major-remediation/   in-flight, custom scope, [-] reverse-engineering
        aidlc-state.md  .aidlc-active-directive.json (MATCHES state)  .aidlc-recovery.md  .aidlc-human-turn  .aidlc-engine-touch
        audit/603e5f4a8072-26e77d17b1cd.md (last 120 of 404 blocks)  audit/603e5f4a8072-747664ca2c41.md (whole, 3 blocks)
        inception/reverse-engineering/memory.md (sample)  artifacts-index.txt
    aidlc/spaces/default/intents/260828-kiro-impact-poc/            completed poc, later SCOPE_CHANGED to feature
        aidlc-state.md  .aidlc-active-directive.json (STALE: digest != state)  .aidlc-recovery.md  .aidlc-goal-stop
        .aidlc-human-turn  .aidlc-engine-touch  audit/603e5f4a8072-26e77d17b1cd.md (last 120 of 731 blocks)
        ideation/intent-capture/{intent-capture-questions.md,intent-statement.md,learnings-selections.json}
        inception/requirements-analysis/{requirements-analysis-questions.md,requirements.md}
        inception/reverse-engineering/memory.md
        construction/kiro-impact-poc/code-generation/{code-generation-questions.md,code-generation-plan.md}   (per-unit path)
        construction/build-and-test/build-and-test-summary.md   construction/code-generation/memory.md (stage-level diary)
        artifacts-index.txt
    aidlc/spaces/default/codekb-index.txt          reverse-engineering outputs live at space level, not in the record
    .kiro/tools/data/{stage-graph.json,scope-grid.json,harness.json}  .kiro/tools/aidlc-version.ts
    .kiro/scopes/*.md (10, incl. project-composed aidlc-review-major-remediation.md)  .kiro/settings/cli.json
    stage-frontmatter/<phase>/<slug>.md (33)
    _excerpts/audit-event-samples.md               first real block of each of 36 event types (test helper, not a shard)
  aidlc-2.3.0-payload/                 64 files, ~228 KB   2.3.0 engine data (git tag) + AUTHORED State Version 7 workspace
    README.md
    .kiro/tools/data/{stage-graph.json,scope-grid.json,harness.json}  .kiro/tools/aidlc-version.ts  .kiro/scopes/*.md (9)
    .kiro/settings/cli.json  stage-frontmatter/<phase>/<slug>.md (32)
    aidlc/active-space  aidlc/.aidlc-clone-id  aidlc/.aidlc-turn-counter
    aidlc/spaces/default/intents/{intents.json,active-intent}
    aidlc/spaces/default/intents/260820-health-probe/               poc, Status: Completed, 8/8 [x]
        aidlc-state.md  audit/fixture-host-0f1e2d3c4b5a.md (74 blocks)
        construction/health-probe/code-generation/code-generation-questions.md ([Answer]: Approve Plan)
        construction/build-and-test/build-test-results.md
    aidlc/spaces/default/intents/260901-ledger-export/              feature, [?] requirements-analysis, Revision Count 1
        aidlc-state.md  .aidlc-active-directive.json (digest == sha256(state))  audit/fixture-host-0f1e2d3c4b5a.md (108 blocks)
        ideation/intent-capture/{intent-capture-questions.md (fully answered),intent-statement.md}
        inception/requirements-analysis/{requirements-analysis-questions.md (Q4 blank [Answer]:),requirements.md,memory.md}
  aidlc-older/                         16 files, ~176 KB   real 2.1.1 install, State Version 7, 32 stages
    README.md
    aidlc/.aidlc-clone-id   (NO aidlc/active-space in the source — deliberately absent)
    aidlc/spaces/default/intents/{intents.json,active-intent}
    aidlc/spaces/default/intents/260707-trello-plugin/
        aidlc-state.md  .aidlc-recovery.md  .aidlc-learnings-selections-intent-capture.json
        audit/603e5f4a8072-4c59c6cec037.md (WHOLE shard, 346 blocks, 29 event types incl. LOOP_RUN_STARTED, RULE_LEARNED)
        ideation/feasibility/feasibility-questions.md (6 blank [Answer]:)  ideation/intent-capture/intent-capture-questions.md
        ideation/market-research/market-research-questions.md  artifacts-index.txt
    .kiro/tools/data/{stage-graph.json,scope-grid.json,harness.json}  .kiro/tools/aidlc-version.ts
```

`artifacts-index.txt` (one per record) is a TSV `size_bytes\trelpath\tcopied_into_fixture` of EVERY regular file in
the source record (dotfiles, sensors, hooks-health, `runtime-graph.json`, `.aidlc-steering-token-key` by name only).
Use it to assert enumeration without shipping the bytes.

## 2. Workspace level, cursors, registry

- `aidlc/active-space` is `default\n` in DevDelta; **absent** in devlake 2.1.1 (the fixture deliberately has none).
  Readers must default to `default` on missing/empty file.
- `aidlc/spaces/default/intents/active-intent` is `<dirName>\n` (`260828-kiro-impact-poc\n`). It points at the
  *completed* intent in DevDelta while the other intent is in-flight — the cursor is not "the running one".
- `intents.json` (2.1.1, 2.3.0 source, 2.6.2 all identical shape): JSON array, 2-space indent, trailing newline,
  keys in this order: `uuid, slug, dirName, scope, status`. Real 2.6.2 rows:
  ```json
  { "uuid": "01a00133-9901-7fdf-815d-5ae4e6e4be02", "slug": "review-major-remediation", "dirName": "260814-review-major-remediation", "scope": "review-major-remediation", "status": "in-flight" }
  { "uuid": "01a047b1-c605-7f76-927a-8193f101d918", "slug": "kiro-impact-poc", "dirName": "260828-kiro-impact-poc", "scope": "poc", "status": "complete" }
  ```
  `scope` is the birth-time scope: the poc row still says `poc` although the state file now says `Scope: feature`
  (a `SCOPE_CHANGED` row exists). `status` values seen: `in-flight`, `complete`. `dirName` is present in all three
  versions; `repos` never appears. Join by `dirName`, never by slug+uuid.
- `aidlc/.aidlc-clone-id` = 12 lowercase hex + `\n` (`26e77d17b1cd`), and it is the second half of the shard name.
  `aidlc/.aidlc-turn-counter` = integer + `\n` (`27`).

## 3. `aidlc-state.md` — v7 vs v8, and whitespace traps

Field regex `^- \*\*(.+?)\*\*:[ \t]*(.*)$` (m flag) and checkbox regex `^- \[([ xSR?-])\] (\S+)\s*—\s*(.*)$` (em dash
U+2014) hold for all four real/synthetic files. Section headers are `## Project Information`, `## Scope Configuration`,
`## Workspace State`, `## Execution Plan Summary`, `## Runtime State`, `## Phase Progress`, `## Stage Progress`,
`## Current Status`, `## Session Resume Point`; phase sub-headers `### INITIALIZATION PHASE` … `### OPERATION PHASE`;
the construction block has an extra literal line `Per unit: [TBD]` right after its header.

| Aspect | v7 (devlake 2.1.1; 2.3.0 template) | v8 (DevDelta 2.6.2) |
|---|---|---|
| `- **State Version**: ` | `7` | `8` |
| Scope Configuration rows | Stages to Execute, Stages to Skip, Depth, Test Strategy | + `- **Review Override**: ` **with a trailing space and empty value** (both DevDelta files) |
| Runtime State | `- **Revision Count**: 0` then **two** blank lines (devlake) | `- **Revision Count**: 0`, blank line, then `- **Skeleton Stance**: scope-dependent` immediately followed by `## Phase Progress` with **no blank line** (poc file). Remediation file has only Revision Count. |
| Stage rows | 32 (`application-design` at 2.6, `delivery-planning` 2.8) | 33 (`domain-design` 2.6, `contract-design` 2.8, `delivery-planning` 2.9) |
| Stage Progress comment | six states listed | remediation file lists six; **poc file lists only four**: `<!-- Checkbox states: [ ] not started, [-] in progress, [x] completed, [S] skipped via --stage/--phase jump -->` — the comment is rewritten by some code paths and is not authoritative |
| `Construction Autonomy Mode` | absent | absent in both real files although the 2.6.2 template has it |
| `Stages to Skip` | `2.1 (reverse-engineering — greenfield)` (number, slug, em-dash reason) or `none` | `1.1 (intent-capture), 1.2 (market-research), …` (number + slug only) or `none` |
| `Total Stages` | `31` = 32 EXECUTE for feature minus the birth-time SKIP | `13` (custom scope) / `33` (after poc→feature re-scope) |

Other real values worth testing against:
- `- **Project**: [Project description]` — the remediation file kept the template placeholder (birth via
  `/aidlc review-major-remediation` with no free text). 2.7.1 can write two more placeholders into the same field,
  both from a birth whose free text was a pasted document: `[Pasted document provided]` and
  `[Pasted document boundary needs clarification]` (`aidlc-utility.ts:5861-5867`). All three are values a title
  must never be derived from.
- `- **Completed**: 3` while `- **In Progress**: reverse-engineering` and the row is `- [-] reverse-engineering — EXECUTE`;
  poc: `Completed: 8`, `In Progress: none`, `Status: Completed`, `Next Stage: none`, `Next Action: Workflow complete`,
  yet `Current Stage: build-and-test` and `Lifecycle Phase: CONSTRUCTION` (a completed workflow keeps its last stage).
- Phase Progress values seen: `Verified`, `Skipped`, `Active`, `Pending`. devlake has `- **Initialization**: Active`
  while `- **Lifecycle Phase**: IDEATION` and `Current Stage: feasibility` — the `phase_stage_disagreement` case.
- `Status` values seen: `Running`, `Completed`. (`Paused`/`In Progress` documented for older writers; none on disk here.)
- Only `[x]`, `[-]`, `[ ]` occur in the real files; the synthetic 2.3.0 in-flight file has `- [?] requirements-analysis — EXECUTE`.
  No real `[R]` or `[S]` row exists on this machine.
- `Project Root` is an absolute path (scrubbed to `/FIXTURE/repo`); it is the ONLY absolute path in a state file.
- The synthetic v7 file was rendered from the 2.3.0 `handleIntentBirthStateBuild` template literal
  (`aidlc-utility.ts:2769` at v2.3.0), then mutated the way `approve`/`reject`/`revise`/`advance` would
  (checkbox, `Completed`, `Revision Count`, `Last Updated`, `Last Completed Stage`).

## 4. `stage-graph.json`, `scope-grid.json`, `harness.json`, scopes

- `stage-graph.json` is a **top-level JSON array** of stage objects (not `{stages: …}`), ordered by `number`.
  Keys 2.6.2: `slug, number, name, phase, execution, condition, lead_agent, support_agents, mode, for_each?,
  workspace_requires?, produces, optional_produces?, produces_kinds?, consumes[{artifact, required, conditional_on?}],
  requires_stage, sensors, scopes, reviewer?, reviewer_max_iterations?, review_class?, summary_confirmation?, inputs,
  outputs, rules_in_context[{path, scope}], sensors_applicable[{id, path, matches}]`.
  2.7.1 adds `review_artifact?`: 13 of the 33 nodes carry it, and on four of those it is NOT `produces[0]`
  (`functional-design` → `functional-spec`, `nfr-requirements` → `security-requirements`, `nfr-design` →
  `security-design`, `infrastructure-design` → `cicd-pipeline`). It names the artifact allowed to carry the
  `## Review` appendix, so a reader that guesses `produces[0]` reads the wrong file on exactly those four.
  2.3.0 lacks `review_class` and `summary_confirmation`; 2.1.1 additionally lacks `produces_kinds`, `optional_produces`,
  `workspace_requires`. There is **no `gate` key** anywhere; gating is implied for every non-initialization stage.
- The 33rd stage: 2.6.2 renamed `application-design` → `domain-design` (2.6) and inserted `contract-design` (2.8),
  pushing `delivery-planning` from 2.8 to 2.9. Stage numbers are therefore NOT stable across versions — key on slug.
- `mode` values: 2.3.0 has only `inline`/`subagent` (reverse-engineering and code-generation are `subagent`); 2.6.2 adds
  `pipeline` (reverse-engineering) and `mob` (user-stories). `review_class` values: `advisory`, `adversarial`.
  `for_each: "unit-of-work"` on 3.1–3.5 in both versions.
- `scope-grid.json` = `{ "<scope>": { "stages": { "<slug>": "EXECUTE"|"SKIP" } } }`; **9 keys shipped through 2.6.2, 11
  in 2.7.1** (which adds `classic` and `express`, with `.kiro/scopes/aidlc-classic.md` and `aidlc-express.md`), while
  DevDelta's 2.6.2 copy has a 10th key `review-major-remediation` (composer-synthesised) and a matching
  `.kiro/scopes/aidlc-review-major-remediation.md` whose frontmatter has `keywords: []` and `skeleton: off`. So the
  shipped count is version-dependent and a project-added scope is indistinguishable from it by count alone — key on
  the stock-name set, and tolerate project-added scopes. 2.7.1 also reworded `feature`'s `description` to
  `Full lifecycle for new features, practical depth` (2.6.2: `Default for new features, practical depth`), which is
  the reminder that a scope description is display text, never an identifier.
- `harness.json`: 2.6.2 and 2.7.1 `{"name": "kiro", "harnessDir": ".kiro", "rulesSubdir": "steering"}` — byte-identical
  across those two, and the one payload file still byte-identical to DevDelta's after the 2.7.1 re-pin; 2.3.0 and 2.1.1
  lack `name`.
- `aidlc-version.ts` last line: `export const AIDLC_VERSION = "2.7.1";` in the payload (`"2.6.2"` in the DevDelta
  fixture, `"2.3.0"`/`"2.1.1"` in the others; regex `AIDLC_VERSION = "(.+?)"` works for all of them).
- Stage files all start with `---` YAML frontmatter; the closing `---` is at line 26–62 through 2.6.2
  (`construction/nfr-design.md` closes at 62, `infrastructure-design.md` at 61) and 27–64 in 2.7.1 (the same two files,
  at 64 and 63; the floor in both is `initialization/workspace-scaffold.md`), so the fixtures keep
  `max(60, frontmatter end)` lines.
- 2.6.2 `.kiro/tools/data/` also carries `ars-priors.json`, `model-rates.json` (not copied; not in the read list), and
  2.7.1 adds `plugin-authoring-context.json`, `plugin-targets.json` and a `plugin-hooks-template/` directory there.
- `.kiro/settings/cli.json`: 2.3.0 has exactly the two managed keys (`chat.defaultAgent`, `chat.modelDefaults` with one
  model); DevDelta's has three models plus user keys `toolSearch.enabled`, `toolSearch.minPct`, `toolSearch.minTokens` —
  the merge-target case for the installer.

## 5. `.aidlc-active-directive.json`

Exact bytes (pretty-printed, 2 spaces, trailing newline, key order fixed):
```json
{
  "version": 1,
  "stage": "build-and-test",
  "state_sha256": "7eca6ee6738be0aa5d88937fec442cbe09210ccd8818e977caeb4e96b854ce71"
}
```
- No `unit` key in either real file. `state_sha256` = sha256 over the ENTIRE `aidlc-state.md` bytes at emit time.
- DevDelta poc: the digest above does **not** match the live state (`e6c81314…`) — a genuine stale marker (the state was
  re-scoped after the last directive). Copied unchanged; `directive_state_digest_mismatch` must classify it as
  *stale*, not as corruption.
- DevDelta remediation: the source digest matched the source state. Scrubbing `Project Root` changes the bytes, so the
  harvester recomputed the digest over the scrubbed bytes to keep it a matching marker (documented in the README).
- **2.3.0 never writes this file** (no reference in `aidlc-lib.ts`/`aidlc-orchestrate.ts`/`aidlc-directive.ts` at the
  tag); devlake 2.1.1 has none either. The synthetic marker in `260901-ledger-export` exists only to exercise the digest
  check. Absence must not be a finding for engines < 2.5.
- The bytes above are a **version-1** marker, which is what 2.6.2 wrote and therefore what every fixture on this machine
  holds. **2.7.1 writes `"version": 2`** at the same path, with a mandatory `kind` (`run-stage`, `load-steering` or
  `invoke-swarm` — the only three the engine publishes as a marker), an optional `units: [str]` where a per-unit beat
  used to carry a single `unit`, and a long tail of ownership/sequence fields (`aidlc-lib.ts:4168-4181`). It also
  **preserves an existing v1 marker as v1**, so both generations live in the field at once: a parser must accept either
  and report the version it actually read, never assume 1.

## 6. Audit shards

- Path `<record>/audit/<host>-<cloneId>.md`. On this Mac the hostname is a hex string, so shards are literally
  `603e5f4a8072-26e77d17b1cd.md`. The **same shard filename appears in both DevDelta intents** (per-clone, not
  per-intent), and the remediation intent has a **second shard** `603e5f4a8072-747664ca2c41.md` (another clone id) with
  just `SESSION_STARTED`, `HUMAN_TURN`, `SESSION_ENDED`. Always glob `audit/*.md` and merge-sort by `**Timestamp**`.
- File = `# AI-DLC Audit Log\n` then blocks of exactly
  `\n## <Heading>\n**Timestamp**: <YYYY-MM-DDTHH:MM:SSZ>\n**Event**: <TYPE>\n**<Key>**: <value>\n…\n\n---\n`.
  Splitting on `\n---\n` round-trips byte-for-byte (the harvester asserts this). Values never contain raw newlines
  (escaped to literal `\n` by the writer). `HUMAN_TURN` blocks have no fields at all.
- Field keys are free-form and version-specific; the ones seen: `Timestamp, Event, Stage, Stage slug, Sensor ID, Output
  path, Fire id, Agent Type, Agent ID, Message, Duration ms, Tool, Details, File, Context, Source, Scope, Reviewer,
  Iteration, Error, Command, Agent, Reason, Options, Decision, Note, Findings count, Detail path, Unit, Request, Phase,
  User Input, To phase, Stages completed, Phase boundary, From phase, Retry, Verdict, Target, State Validity, Direction,
  Current Stage, Artifact Fingerprint, Questions File, Questions SHA-256, Project Type, Languages, Frameworks,
  Checkpoint, Build System, Stage count, Rule count, Recovered, Path, Worktree path, Stages in Scope, Stage Count
  Delta, Old Scope, New Scope, Depth, Branch name, Bolt slug, Base branch, Approval Gates, Window, Window created,
  Mode, Intent, Heading, Destination, Candidate-ID` (note the hyphen in `Candidate-ID`, and `Fire id` vs `Agent ID`
  casing). `**Agent Type**: ` is routinely EMPTY.
- Event names seen on disk (38): `ARTIFACT_CREATED ARTIFACT_UPDATED DECISION_RECORDED ERROR_LOGGED GATE_APPROVED
  GUARDRAIL_LOADED HEALTH_CHECKED HUMAN_TURN LOOP_RUN_STARTED MEMORY_EMPTY PHASE_COMPLETED PHASE_SKIPPED PHASE_STARTED
  PHASE_VERIFIED QUESTION_ANSWERED REVIEW_COMPLETED REVIEW_REQUESTED RULE_LEARNED SCOPE_CHANGED SENSOR_FAILED
  SENSOR_FIRED SENSOR_PASSED SESSION_COMPACTED SESSION_ENDED SESSION_RESUMED SESSION_STARTED STAGE_AWAITING_APPROVAL
  STAGE_COMPLETED STAGE_JUMPED STAGE_STARTED SUBAGENT_COMPLETED SUMMARY_CONFIRMATION_RECORDED WORKFLOW_COMPLETED
  WORKFLOW_STARTED WORKSPACE_INITIALISED WORKSPACE_SCAFFOLDED WORKSPACE_SCANNED WORKTREE_CREATED`.
  `LOOP_RUN_STARTED` (heading `Loop Run Started`, fields `Intent, Mode: attached, Window, Window created: true`) is
  **not in any engine taxonomy** (it came from a loop-branch build) — unknown events must be kept, not dropped.
  `RULE_LEARNED` carries an absolute `Destination` path. **`GATE_REJECTED` and `STAGE_REVISING` never occur in any real
  shard on this machine**; their shapes in the synthetic 2.3.0 shard come straight from `aidlc-state.ts` at v2.3.0:
  `GATE_REJECTED {Stage, Feedback?}`, `STAGE_REVISING {Stage, "Revision count", Feedback?}`, and the re-entry row
  `STAGE_AWAITING_APPROVAL {Stage, Details: "Re-entering gate after revision"}`.
- Real gate rows: `**Event**: STAGE_AWAITING_APPROVAL` + `**Stage**: intent-capture` + optional `**Recovered**: true`
  (backfilled gate, seen twice); `**Event**: GATE_APPROVED` + `**Stage**: …` + `**User Input**: Approve` — but also
  `**User Input**: 批准` (devlake) and a 300-character multi-sentence `User Input` starting `Approve. Continue to Code
  Generation, but obey requirements C3 …` — do not assume the wire token is the whole value. `GATE_APPROVED` is always
  followed by `STAGE_COMPLETED` with `**Details**: Stage <Name> approved by gate` in the same second.
- In the poc shard `STAGE_AWAITING_APPROVAL` for `build-and-test` appears 5 times and `WORKFLOW_COMPLETED` **3 times**
  (REDO `STAGE_JUMPED` rows re-opened the last stage; then a `SCOPE_CHANGED` poc→feature on 2026-08-30 after
  completion). "Completed" is not terminal in the audit; take the latest state file as truth and treat the audit as
  evidence.
- `PHASE_STARTED` for initialization carries `**Stage count**: 3`; boundary `PHASE_STARTED` rows carry only `Phase`
  and `Scope`. `PHASE_VERIFIED` values: `initialization → ideation`, `initialization → inception`, `ideation →
  inception`, `inception → construction`, `construction → end` (final).
- `DECISION_RECORDED` variants: `{Stage, Decision, Options}` (Options is a comma-joined list with apostrophes:
  `Guide me,I'll edit the file,Chat`), plus 2.6.2 `{…, Checkpoint: Consolidated Summary Confirmation, Questions File:
  aidlc/spaces/default/intents/…/intent-capture-questions.md}`; `SUMMARY_CONFIRMATION_RECORDED` adds
  `**Questions SHA-256**: <64 hex>` over the questions-file bytes. `QUESTION_ANSWERED {Stage, Details}` where Details is
  the human's raw text (Chinese in the real files). A pending question = `DECISION_RECORDED` for the current stage with
  no later `QUESTION_ANSWERED` (the synthetic in-flight shard ends exactly like that).
- `ERROR_LOGGED {Tool, Command, Error}` real texts: `Refusing to record this answer: a real human has not acted at this
  checkpoint this turn. …`; `Unknown subcommand: set-status. Valid: get, set, …`; `Refusing to complete "intent-capture":
  artifact … has no recorded native-tool write after the human's consolidated summary confirmation. …`;
  `Unknown subcommand: undefined. Valid: create, merge, discard, list, verify, info` (aidlc-worktree). These are
  conductor mistakes, not workflow failures — do not turn every ERROR_LOGGED into a `failure` card.
- `SESSION_ENDED` `Reason` values: `other`, `prompt_input_exit`. `SESSION_STARTED` `Source`: `startup`;
  `SESSION_RESUMED` `Source`: `resume`; `SESSION_COMPACTED {Current Stage, State Validity: valid}`.
- `ARTIFACT_CREATED/UPDATED {Tool: Write|Edit, File: <absolute path>, Context: "inception > reverse-engineering >
  scan-backend.md"}` — `File` is absolute (scrubbed to `/FIXTURE/repo/…`), while sensor rows' `Output path`/`Detail
  path` are repo-relative. `REVIEW_COMPLETED` has `**Artifact Fingerprint**: sha256:<64 hex>` and `Verdict: NOT-READY|READY`.
- Same-second timestamps are the norm (a birth writes ~15 rows in one second); order within a shard is file order.
- Kept windows: the DevDelta tails (last 120 blocks) are dominated by `HUMAN_TURN`/`SUBAGENT_COMPLETED`/session rows
  (see each README's "events in the kept window"); real gate/question/sensor/error blocks are in
  `aidlc-2.6.2-devdelta/_excerpts/audit-event-samples.md` (one per event type, exact bytes, outside `aidlc/`) and in the
  whole devlake shard.

## 7. `*-questions.md`

- Location `<record>/<phase>/<stage>/<stage>-questions.md`; per-unit:
  `construction/<unit>/code-generation/code-generation-questions.md` (real, DevDelta).
- Question headings `## Q1. …` (Chinese text in all real files before 2.10); a 2.10 conductor wrote
  `## Question 1: Farewell wording` (`question-fallback/question-n-headings.md`, 2026-10-12: the protocol fixes the
  tags and options, not the heading), with a preamble that quotes `` `[Answer]:` `` mid-line; options either `A. …` (poc intent-capture) or
  `- A. …` (poc requirements-analysis, devlake) — both occur; escape option `X. Other (please specify)`,
  `X. Other (please name the roles and cadence)`, or fullwidth `X. Other（请说明）`.
- Answer tag variants seen: `[Answer]: A`, `[Answer]: A, B. 主要使用者是…` (letters then prose), `[Answer]: D. 三项都完成…`,
  `[Answer]: Looks correct`, `[Answer]: Approve Plan`, blank `[Answer]:` (devlake feasibility ×6, followed by a blank
  line and `---`), and **multi-line answers**: `[Answer]: X. 采用四个互斥分桶，…：` continues on the following `- ` bullet
  lines. A parser that takes only the rest of the `[Answer]:` line will truncate real answers; blank detection must use
  `^\[Answer\]:[ \t]*_*[ \t]*$` per line, not "next non-empty line".
- Extra structures inside the same file: `**Mode:** chat（2026-08-28T11:56Z）` after answers; a `---` separated
  `## 权威来源（用户本回合明确指定）` free section; `## Consolidated Summary Confirmation` with `- Looks correct` /
  `- Request changes` and `[Answer]: Looks correct`; `## Post-approval Amendment` followed by a new `## Q6.` after the
  confirmation; `## Plan Approval` with `- Approve Plan` / `- Request Changes` and a parenthetical note that the answer
  was cleared and re-approved after revision. A file may therefore contain several `[Answer]:` tags with different
  vocabularies; the digest (`question_digest`) is over the whole file.
- Synthetic 2.3.0: `intent-capture-questions.md` fully answered; `requirements-analysis-questions.md` answered through
  its confirmation and then `## Post-approval Amendment` / `## Q4.` with a blank tag while the stage row is `[?]` — the
  intent legitimately yields BOTH a gate card and a question card. devlake `feasibility-questions.md` is the pure
  pending-question case (`[-]` stage, six blank tags, no gate).

## 8. Other record files

- `.aidlc-recovery.md` (4 lines, Claude PreCompact hook; also present in devlake):
  `# AIDLC Recovery Breadcrumb` / `**Last validated**: 2026-09-02T03:53:25Z` / `**Current stage**: build-and-test` /
  `**State file**: valid (all required sections present)`. Field lines here have NO leading `- `.
- `.aidlc-goal-stop` (poc only) is free-form `key=value` text written by an agent during a KiroCrew `/goal` run, not by
  AI-DLC: `completed_at=2026-08-29T06:35:06Z` / `intent=260828-kiro-impact-poc` / `state=8/8` / `ledger=T000-T120 complete`.
- `.aidlc-human-turn` / `.aidlc-engine-touch`: one ISO line each (`2026-09-04T10:49:31Z`); only mtime matters to the
  engine. Absent in 2.1.1.
- Learnings selections moved: 2.1.1 `<record>/.aidlc-learnings-selections-intent-capture.json` (with `type`, `scope`,
  `source` keys) vs 2.6.2 `<record>/ideation/intent-capture/learnings-selections.json`
  (`{"stage_slug":"intent-capture","selections":[]}`).
- Hooks-health names differ per version (2.1.1: `audit-logger, runtime-compile, sensor-fire, stop, sync-statusline,
  validate-state, session-start/end, log-subagent`; 2.6.2: `continue-workflow, write-audit-log, rebuild-stage-graph,
  run-sensors, review-freeze, reviewer-scope, plan-approval-guard, validate-state, session-start/end, log-subagent`); a
  space-level `intents/.aidlc-hooks-health/` exists in DevDelta (hooks fired before birth). Not copied; listed in indexes.
- `.kiro/hooks/` grew from 18 files in 2.6.2 to 21 in 2.7.1. The three additions are a different KIND of artefact from
  the `aidlc-*.ts` bodies: `aidlc-plan-approval-guard.kiro.hook` and `aidlc-record-human-turn.kiro.hook` are standalone
  Kiro hook *registrations* (the same two hooks previously wired only through `.kiro/agents/aidlc.json`), and
  `review-freeze-command.ts` is a command entry point rather than a hook body. A reader that assumes everything under
  `hooks/` is an `aidlc-`-prefixed `.ts` will miss all three.
- Per-unit construction layout is real: `construction/kiro-impact-poc/code-generation/{code-generation-plan.md,
  code-generation-questions.md, code-summary.md, traceability.json, unit-test-instructions.md}` while once-per-workflow
  stages sit at `construction/build-and-test/…`, and a **stage-level diary** for a per-unit stage sits at
  `construction/code-generation/memory.md` (no unit segment). Unit detection = "single path segment after `construction/`
  that is not a stage slug".
- `inception/reverse-engineering/` holds only `memory.md` plus scratch `scan-*.md` (56 KB / 53 KB, not copied); the
  nine RE artifacts live at `aidlc/spaces/default/codekb/DevDelta/*.md` (see `codekb-index.txt`).
- `runtime-graph.json` (2–12 KB, gitignored, derived) exists in every record; not copied, listed in indexes.

## 9. Scrubbing side effects the parser tests must expect

- Absolute paths inside audit `File`, `Destination`, `Worktree path`, `Command`, `Error`, `Message` values and inside
  questions / artifacts now begin with `/FIXTURE/repo/` (the repo itself), `/FIXTURE/ws/` (sibling checkouts in the
  same workspace, e.g. `/FIXTURE/ws/devdelta-phase0-ac14/…`, `/FIXTURE/ws/kiro-attribution` — the latter was a
  tilde path `~/<ws>/kiro-attribution` in the source, so agents do write `~`-relative paths into audit messages) or
  `/FIXTURE/home/` (anything else under the account home, e.g. `/FIXTURE/home/.nvm/…`). Repo-relative values
  (`Output path`, `Questions File`, `rules_in_context[].path`, sensor `matches`) are untouched.
- One e-mail in the remediation `inception/reverse-engineering/memory.md` became `REDACTED`.
- `state_sha256` in the remediation directive was recomputed (see §5); every other sha256 on disk is original.
- Shard file names, clone ids, hostnames, agent ids, fire ids and UUIDs are unchanged.

## 10. Open questions for the orchestrator

1. ~~Which engine version is the product pinning?~~ **Settled.** The payload is 2.7.1 (State Version 8 only) and
   contracts C21 / architecture A01 / `app.json` `extra.bundledAidlc.engineVersion` / `payload/manifest.json` all agree;
   every version-derived value is read from the manifest at run time, so a further re-pin is a payload swap and a
   manifest rebuild, not a code change. The fixture names keep their own (older) versions on purpose.
2. ~~Whether a checked-in copy of pristine engine data is wanted as a fixture.~~ **Settled: no.** Tests read
   `payload/aidlc-kiro` directly (contracts §4.2, `RepoBuilder.with_engine("payload")`), which is what let the 2.6.2 →
   2.7.1 re-pin land without touching a fixture.
3. Whether `.aidlc-goal-stop` should surface at all (writer unknown; KiroCrew's own sentinel lives elsewhere).

## 11. Why `aidlc_reader.py` reads the way it does

Moved here from the reader's docstrings in the v2 slimming (P7): the code keeps a one-line reason and cites
`FORMAT-NOTES §11.n`; this section keeps the evidence behind it.

### 11.1 The four rules

The reader is the product's eyes. Every claim the UI makes about a repository — which stage is open, whether a gate
is waiting, what the human is being asked — starts there, and nothing downstream can recover from a misread: a
confidently wrong parse becomes a confidently wrong card, and a card is what sends a prompt into someone's session.
Each rule exists because of a real file in `tests/fixtures`.

1. **Tolerate the writer, never guess for it.** State Version 7 and 8, 32- and 33-stage graphs, three separator
   spellings, a value that is empty with a trailing space, a `Skeleton Stance` row with no blank line before the next
   section, a stage-progress comment that lists four checkbox states while the file uses six, a literal
   `Per unit: [TBD]` line in the middle of the rows. All of that parses. What does *not* happen is inference: an
   unsupported `State Version` is reported as the number on disk, an unknown audit event keeps its name, and a field
   that is absent is `None` rather than a default that looks like data.
2. **Two reads or no authority.** `stable_read` stats, reads, stats again. A file that moved between the two stats is
   returned with `unstable=True` and can authorise nothing (FR-ACT-009); the caller re-reads instead of choosing one
   of the two versions.
3. **Bounded and contained.** Every path is repo-relative and resolved through `security.resolve_inside`, so a cursor
   file or a registry row that names `../../etc` fails as a bad path instead of reading outside the repo; every read
   is capped; audit shards — append-only and unbounded — are the one thing tail-read, and a tail-read shard marks the
   bundle `complete=False` so evidence built on partial history says so.
4. **Read only.** No writes, no subprocesses, no caching (the caller owns freshness, because a cached parse is exactly
   how a stale card gets authorised), no `.aidlc-steering-token-key`, no `usage-ledger.json`, and `.aidlc-goal-stop`
   is surfaced as a presence flag with no authority at all: nothing in AI-DLC writes it (`04 §3.11`), so treating it
   as a workflow signal would give an agent-written file authority over the product.

The reader is synchronous and blocking. Callers reach it with `await asyncio.to_thread(...)` (or the reconciler's
private scan pool for whole-repo walks); nothing in it may run on the gateway loop.

### 11.2 Engine bookkeeping under `.aidlc-engine/` (2.9+)

AI-DLC 2.9 keeps a record's engine bookkeeping (directive marker, recovery breadcrumb, human-turn and engine-touch
markers, hook health, stop-hook counter, reviewer dispatch, sensors) under `<record>/.aidlc-engine/` and reads the
legacy record-root names only as a fallback. `record_file` follows the engine's own rule (`engineReadDirFor`):

- The `.aidlc-engine` location wins unless it is absent and the legacy path exists.
- An `.aidlc-engine` entry that is not a directory is not absence: the engine stays on the new path and fails closed
  rather than reviving legacy state, so Studio reads the same (missing) file.
- The engine still reads three legacy names when the new entry is absent (`ENGINE_LEGACY_READS`): the directive
  marker (`resolveActiveDirectiveTarget`), hook health and sensors (`engineReadDirFor`). It rebuilds the rest at the
  new path with no fallback, so once a record has an `.aidlc-engine` directory their record-root copies are leftovers
  from an engine before 2.9. Such a name reads its legacy copy only while the record has no `.aidlc-engine` at all,
  which is a record an engine before 2.9 is still writing.

### 11.3 Stable reads

`stable_read` is `stat` → `read` → `stat`. A difference in size or mtime means AI-DLC (or an editor) was writing while
Studio read, so the bytes held may be half of one version and half of another. One retry after
`STABLE_READ_RETRY_SECS` catches the common case — a write that finished during the first attempt — and if the second
attempt is also unstable the snapshot is returned anyway with `unstable=True`: the UI needs to show *something*
("Refreshing"), it just must not be allowed to authorise a decision from it (FR-ACT-009).

`None` means "not a readable regular file" (missing, a symlink, a directory, or one of the secret files Studio never
opens). Oversize raises `too_large` instead of returning a truncated read, because half a state file parses into a
plausible, wrong projection. `read_state` lets that through; the caller turns it into a `state_unreadable` finding
only after a grace period, because an unreadable state file is often just a write in flight.

### 11.4 State file

`parse_state_file` never raises on content. The traps it handles are all real (§3): a `## Phase Progress` heading with
no blank line before it because `Skeleton Stance` was inserted last, a literal `Per unit: [TBD]` line among the
construction rows, `- **Review Override**: ` with a trailing space and no value, an en dash or `--` instead of the em
dash, and a one-line header-only file written by `createIntent` before the body exists (`04 §2.2`) — which is reported
as `header_only` rather than as an empty workflow, because "birth in progress" and "workflow with no stages" must not
look alike. Only a `### <PHASE> PHASE` sub-header moves the phase of the rows below it, so a future sub-heading cannot
silently re-label them.

`StateFile.sections` keeps every `## X` block, including ones this version of Studio does not know about, so a newer
engine's extra section is preserved for display instead of silently dropped. The named dicts (`project`,
`scope_config`, …) are conveniences over the same data; the scalar fields are looked up by label across the whole file
(`getField`, `04 §3.1`, first occurrence wins) because the engine matches labels, not sections. The engine writes the
literal `none` for "no stage" / "no unit"; it reads as absent, because a consumer that compared it to a slug would look
for a stage directory called `none`.

### 11.5 Registry, cursors and the directive marker

- `intents.json`: the engine treats an absent or malformed registry as empty and carries on from the directories on
  disk, so Studio does the same — a broken registry must degrade the *labels* on an intent, never hide the work.
  `dirName` is optional (pre-spike rows omit it) and `repos` appears only for multi-repo intents.
- `match_intent_row` is the engine's own join (`recordDirMatches`): exact `dirName` first. The legacy fallback
  (`<slug>-<hex>` where the hex is a suffix of the uuid) exists for records born before `dirName` was stored; joining
  by slug alone would attach the wrong row to two intents that share a label.
- Cursors: `activeSpace`/`activeIntent` never throw in the engine, and an unreadable cursor there means "the default",
  not "no workspace" (`04 §2`). The 2.1.1 install in the fixtures has no `aidlc/active-space` at all.
- A dangling `active-intent` is the case the engine hides: `activeIntent()` ignores a cursor whose directory has no
  `aidlc-state.md` and silently falls back to the lone intent, so a repo can look fine while every decision would land
  somewhere the user did not choose. Studio reports it instead (`dangling_cursor`). A cursor value that is not a legal
  directory name is reported as dangling with no value, so it can never become a path. A directory counts as an intent
  only when `aidlc-state.md` is in it (the engine's `listIntentDirs`), which keeps `intents.json`, the cursor file and
  the space-level `.aidlc-hooks-health/` out of the list.
- The directive marker (§5). Both live schema versions are accepted, and the parsed number is carried through rather
  than hardcoded: 2.6.2 wrote `{"version": 1, stage, unit?, state_sha256}` and 2.7.1 writes
  `{"version": 2, "kind": …, stage, unit?/units?, state_sha256}` at the same path while leaving an already-present v1
  marker at v1 (`refreshActiveDirectiveMarker`), so a field install can be serving either shape. Refusing v2 did not
  degrade to "no marker": the execution cursor vanished, a per-unit Construction stage was then probed at
  `Current Stage`, its questions file was not found, and the question / plan-approval card disappeared while the
  intent read Idle.
- `kind` is mandatory on v2 and must be one the engine emits (`DIRECTIVE_KINDS`), because the marker *is* the
  execution cursor: a later non-execution kind must not be mistaken for a running stage, which would point every card
  at work nobody started. v1 has no `kind`. Only `run-stage` and `invoke-swarm` execute a stage, and `invoke-swarm` is
  the unitless code-generation shape that fans out over `units`. `zero_unit_run` marks a v2 Code Generation
  `run-stage` that omitted both unit fields; a null, empty or malformed field must not be mistaken for that
  engine-defined zero-unit shape.
- The rest of the shape validation is the engine's and identical in both versions (slug-shaped stage, 64 hex digits,
  same digest semantics). A digest that does not match the live state is *not* rejected: the engine treats a stale
  marker as absent and falls back to `Current Stage`, and staleness is normal after every state write, so it is
  reported as `matches_state=False` for a low-severity finding (C06) instead of thrown away.

### 11.6 Audit

- `parse_audit_shard` is keyed on `**Event**:`, never on the `## Heading` line: headings were renamed between engine
  versions and 2.0 emitted free-form ones (`04 §5.2`). A block with no `Event` field is dropped (it is the file header
  or a hand-edit), and an event name Studio does not know is kept verbatim — `LOOP_RUN_STARTED` exists on a real
  machine and is in no engine taxonomy, so a parser that dropped unknown names would silently lose history.
- `pos` is the block's index within the shard. Together with `shard_index` it is the only tie-break available for the
  same-second rows a birth writes by the dozen. Position only orders rows *within* one shard: two shards writing in
  the same second are causally unordered (`04 §5.1`), so `merge_audit`'s `(timestamp, shard_index, pos)` sort makes
  the order deterministic, not meaningful, which is why every authority check that depends on ordering also requires a
  strictly later timestamp or the same shard. `latest_event` takes `max` over that key rather than "last in the
  list", so a caller that passes an unsorted slice still gets the newest row.
- `read_audit` always globs: shards are per clone, not per intent — the same file name appears under two intents and
  one intent can have two shards (`04 §5.1`). It prefers complete history, because parsed event ordinals and
  human-turn counts are dispatch evidence and tail windows would rebase them as the log grows. All shards share one
  bounded history budget; exceeding it (or an explicit smaller `tail_bytes`) still produces an honestly incomplete
  bundle, never a complete-history claim.
- The budget split (`_audit_read_limits`): a record that fits is read whole (each shard up to `per_shard`). Over
  budget, every shard keeps an equal share first — never less than the old 64 × 256 KiB windows — and the remainder
  goes to the most recently written shards. Shard names are `<host>-<random clone id>.md`, so name order says nothing
  about which shard holds the dispatch being reconciled; spending the budget in that order left the active clone's
  shard with nothing to read.
- The engine can append between `read_audit`'s stat and its read. Capping at the stat size refused the grown file and
  dropped every event in it, which read as missing human turns, so a whole shard may grow into the budget nothing else
  claimed. A failed read used to be indistinguishable from an empty shard; audit-backed questions need the absence of
  a later answer to be a proof, so an unreadable or changing shard makes the bundle partial. A shard's `shard_index` is
  its position in `shards`, which is how the reconciler places a captured boundary; counting skipped entries made the
  two disagree.

### 11.7 Stage graph, scopes, harness trees and workspace signals

- `StageNode` keys absent in older graphs (`review_artifact` before 2.7.1, `review_class` and `summary_confirmation`
  before 2.5, `produces_kinds`/`optional_produces`/`workspace_requires` before 2.3) come back `None` or empty. Stage
  *numbers* moved between versions when `contract-design` was inserted, so nothing may be keyed on `number` — `slug`
  is the identity (§4). `review_artifact` (13 stages declare it in 2.7.1) is the one artifact the reviewer protocol may
  append `## Review` to; on four of them it is not `produces[0]`, so reading the verdict off `produces[0]` attaches one
  artifact's verdict to another artifact's evidence digest. `parse_stage_graph` also accepts a `{"stages": [...]}`
  wrapper so a future wrapper does not blank the map; an entry with no `slug` could not be joined to a state row and is
  skipped.
- `STOCK_SCOPES` are the eleven scopes AI-DLC ships (`classic` and `express` arrived in 2.7.1). A
  `.kiro/scopes/aidlc-<name>.md` outside the set was composed for the project (DevDelta has one), which the installer
  must never overwrite — hence `project_owned`, decided by the file name (the *path* the installer would overwrite),
  not by the `name` field. A shipped scope missing from the set is reported to the wizard and the Advisor as "defined
  by this project", which is false evidence. `testStrategy` is camelCase on disk (only `aidlc-workshop.md` has it) and
  `test_strategy` is accepted as well, so a spelling change in a later engine does not lose the value. Frontmatter is
  read with a deliberately small YAML subset: scope frontmatter is flat by construction, and pulling in a YAML parser
  to read the shipped files would add a dependency the gateway does not have. `scope-grid.json` may be nested
  (`{"stages": {...}}`) or flat, and project-composed scopes are normal (DevDelta ships a tenth).
- Harness trees are described from their own files (`aidlc-version.ts`, `aidlc-lib.ts`, `stage-graph.json`) rather
  than by asking the engine: repo-resident TypeScript is untrusted data (PRD §16.2, C35). `.kiro` is the harness Studio
  drives, but the others are reported so a repo driven from two harnesses is visible. The state version an engine
  writes is `CURRENT_STATE_VERSION` in `aidlc-lib.ts` from 2.5 on; older builds carry it only inside the state
  template in `aidlc-utility.ts`. Guessing would make `state_version_unsupported` fire against a healthy repository.
- `WorkspaceSignals` is evidence for the Advisor's plan draft (§1.18a, PRD §16.2 / FR-ADV-003): enough to tell a
  Python service from a TypeScript UI, deliberately too little to be a listing of the repository. Names only, no
  recursion, one bounded text read; the broker's `_clean` redacts every string again before it enters a package.
  `read_workspace_signals` is bounded by construction rather than by trust: an unreadable root yields nothing,
  symlinks and dot-entries are never followed or named (a README or manifest that is itself a symlink is skipped the
  same way the listing skips it, even when its target is inside the repository), the listing keeps the first
  `MAX_WORKSPACE_ENTRIES` names without sorting the whole directory (`heapq.nsmallest` over the eligible entries — a
  repository root with tens of thousands of files costs one pass, not a sort), every manifest and README path is
  containment-checked before it is stat-ed, and the README is the *only* file whose bytes are read — through
  `bounded_text`, so a secret file or an oversized one comes back as nothing. The README text is returned whole (as
  bounded), not cut to `MAX_README_CHARS`: the broker redacts the whole text first and cuts second, so a credential
  straddling the cut is masked whole instead of leaving a half the scanner cannot recognise.

### 11.8 Questions files and audit-backed questions

`parse_questions_file`:

- Sections start at `## Q<n>.`, `### Q<n> —` and the three checkpoint headings. Any other Markdown heading ends the
  current section without starting one, so free sections and explanatory subheadings cannot contribute options or
  answers to the preceding question. Fenced code contributes no headings, options or answers, while `raw` keeps the
  original text.
- Both option spellings occur — `A. …` and `- A. …` — and `X.` is the escape hatch that takes free text. Options are
  collected before the section's first `[Answer]:` tag, because a multi-line answer's bullets sit after it and must not
  be mistaken for choices. Real answers are multi-line (§7): `[Answer]: X. 采用四个互斥分桶…` continues on four `- `
  lines, and taking only the rest of the tag line would send a truncated answer back into the conversation. The `- `
  marker is kept: the text is shown back to the human as what they wrote, and a four-bullet answer that arrives as four
  bare lines reads as one run-on sentence.
- `## Post-approval Amendment` resets nothing: the `## Q<n>.` sections after it are ordinary questions, which is how
  one intent legitimately has both an open gate and a pending question.
- `pending_count` counts unanswered questions, including a section the engine wrote without an `[Answer]:` line at all
  (the human still owes that answer, and calling it answered would hide a question card), plus blank answer tags in
  unsupported sections (e.g. `## F1.`) and any after a supported section's first tag, including `- [Answer]:` list
  items. Those keep the human boundary visible without assigning them numeric Q identifiers, and any such pending
  content makes the whole group conversation-only: `file_questions_support_forms` turns only unambiguous, persisted
  option groups into editable forms, and never invents an option or silently merges repeated question/option
  identifiers. Checkpoints are counted separately in `pending_checkpoint` because they carry different decisions and
  different wire text.
- Multi-select comes from an explicit marker in the heading, or from a standalone explanatory instruction. A context
  marker must end a paragraph and start a sentence (or stand alone); a background mention such as "(select all that
  apply) is another format" is not an instruction. Soft line breaks and paired emphasis do not change an instruction's
  meaning; that normalization is detection-only, and the displayed context keeps its original Markdown.
- `question_digest` is over the whole file: any edit anywhere changes what the human was shown, which is exactly what
  compare-and-submit must notice (C30). Engine confirmation receipts may use a different, explicitly named content-hash
  scope; those are checked separately.

Audit-backed questions (a decision the engine asked in the chat, with no open questions file):

- A same-turn batch (A37): the engine logs every structured question before presenting them, so decision rows written
  back to back in one shard were asked together, and any other row in between ends the run. The audit row has no
  multi-select flag: the engine's mandated "anything to add?" prompt is single choice, and every other row asked
  alongside it (the learnings candidates) takes any subset, so Studio adds an explicit "None of these". Each question
  keeps an "Other" free-text escape, as in the chat.
- Handing an answered sheet back to audit (`_closed_question_receipts`) needs a file-bound closure in this attempt.
  Legacy answered questions without a checkpoint receipt keep file ownership. The engine's confirmed-content-v1 hash
  normalizes newlines and trims ECMAScript trailing whitespace; it is not the whole-file digest Studio captures, so the
  full normalized content is verified. AI-DLC 2.10 appends `## Assumption Confirmation` after the summary is
  confirmed, so that receipt binds only the content before the heading (A37). That portion is accepted only when the
  section is the last one in the file, its own tag is answered, and a `QUESTION_ANSWERED` receipt carrying the same
  answer sits between the summary receipt and the decision. Every other byte stays bound.

### 11.9 Review sections

`parse_review_section` reads the LAST `## Review` section, because a re-reviewed artifact accumulates sections and
only the terminal verdict binds. Findings come from bullets and from the rows of a Markdown table: both 2.6.2 reviewer
agents ship a template whose findings are a table (`| 1 | Major | FR2.2 | … | … |`), and a bullets-only parser read a
table full of Critical rows as "no findings", so the gate card told a human there were no blockers. Only rows below the
`|---|:--:|` rule that divides a table's header from its body are findings, which keeps the column headings out of
the list. A table is findings only when its heading says so (`### Findings`, or a `Blocker`/`Advisory`/`Resolved`
group): the same body also carries `### Validation Tool Results`, and promoting tool output to findings would invent
evidence rather than lose it, so headings known to name something else (summary, validation, verdict,
recommendation) are listed explicitly. A blank line or any prose ends a table.

The severity vocabulary of a table row (Critical/Major/Minor) is the reviewer's own and stays in the title verbatim:
Studio does not re-grade a reviewer, so a row's `level` is only ever what its heading said, otherwise `unknown`.
`ReviewFindings` is a `list` subclass because §1.6.2 types the result as a plain list, and a plain empty list makes
one claim ("the reviewer listed nothing") while meaning two: that, or "Studio could not tell what this reviewer
listed". The gate card affirms *no open blockers* off that list, so the difference decides whether a human is told a
fact or a guess: when nothing could be enumerated and rows Studio could not place remain, `findings_parsed` is
`False` and the caller must say it could not read the findings instead of asserting there are none. `ArtifactList`
uses the same device to carry whether the cap or the scan deadline cut a listing short, so a partial record is never
reported as a complete one.

### 11.10 Artifacts, presence evidence and the boundary

- `list_units` takes the stage names to exclude from the intent's own state file when it is readable, so a stage a
  newer engine renamed or added cannot be mistaken for a unit of work; `CONSTRUCTION_STAGE_SLUGS` is the fallback
  (a directory directly under `construction/` is a unit unless it is a stage, §8).
- `list_artifacts` never follows symlinks and skips dot-directories, which keeps the walk out of `.aidlc-sensors/`,
  `.aidlc-hooks-health/` and any link that leaves the repo. Digests are computed only for small records
  (`WHOLE_RECORD_HASH_LIMIT`): card evidence never comes from this listing, so hashing 2 000 files to render a file
  tree would be waste, not safety. `ArtifactMeta` lives in the reader rather than in `projection.py` (where §1.8 lists
  it) because the reader constructs it and the projection already imports the reader; `projection` re-exports it.
- `stage_artifacts` is the evidence set (review P14): every regular file directly in the stage directory plus each
  declared `produces` / `optional_produces` name that exists there, always digested because this list is what
  `evidence_digest` compares at submit time. A file too big to hash whole is digested over its first
  `MAX_ARTIFACT_RENDER_BYTES` plus its size, so growth past the cap still changes the digest instead of looking frozen
  to compare-and-submit. `evidence_digest` (sorted `relpath:sha256` rows plus the parsed review section, C30) exists
  because rewriting an artifact does not touch the state file: an artifact rewritten between the moment a card was
  rendered and the moment Approve is pressed makes the submit stale. It is `None` only for a card with no stage.
- `PresenceBaseline` (C23) is compared byte for byte by the reconciler to answer two questions no single signal can:
  did a human actually take a turn for this decision, and did anything on disk move at all. Six independent facts,
  because each alone is forgeable or noisy: the `HUMAN_TURN` count, the marker mtime, the Kiro turn counter, every
  shard's size, a digest of the newest shard's tail (`PRESENCE_TAIL_BYTES`: enough to cover the rows a turn appends,
  one page-sized read), and the state digest. The reconciler needs *exactly one* new human turn to accept a
  resolution — zero means the engine recorded an answer nobody gave, more than one means the boundary cannot be
  attributed to this decision. `human_turn_events` is a lower bound when the audit was tail-read, which
  `human_turn_events_partial` says out loud so nobody treats "same count" as proof.
- `stable_boundary_inputs` reads in a fixed order (state, audit, directive, questions) and re-reads the state file at
  the end. If the first read was unstable, the second was, or the two digests differ, the set spans a write and
  `stable` is `False`: a decision built on it would authorise a boundary that no longer exists (FR-ACT-009).
- Questions for a stage are found without the stage graph: the phase comes from the state file's own
  `### <PHASE> PHASE` grouping; when the row is missing (a stage the graph knows but this state file does not list)
  every phase directory is probed, plus the per-unit path — at most one probe per phase per unit. A per-unit
  Construction stage is the dangerous case. When neither the directive nor the state names the executing unit, every
  unit directory is a candidate, and returning the first hit means returning whichever unit sorts first: the human
  would be shown one unit's questions and their answer delivered as though it answered another's. So all candidates
  are probed and a second hit makes the answer unknowable — no questions, and `unit_ambiguous` so Consistency can
  block the decision and the card becomes a recovery card instead of a coin flip. One candidate with a questions file
  is still an answer: which unit it was is recorded in the file's own path. `BoundaryInputs.unit_ambiguous` is
  additive to §1.6.3: a caller that does not know about it simply gets no questions, which is the safe half.
- `boundary_token` (state digest + newest boundary event timestamp + current stage + revision count) changes when the
  workflow moves and stays put when unrelated audit noise (a session start, a sensor firing) is appended, which is what
  makes it usable as the compare-and-submit key. `stage_attempt` counts the per-stage `STAGE_REVISING` rows because
  `Revision Count` in the state file is workflow-global (C05); the conductor offers `Accept as-is` only after three
  rejection cycles, and this is the number that gate is checked against.

## 12. Why `consistency.py` reports the way it does

### 12.1 Report, never resolve

`consistency.py` is the one place in Studio that is allowed to notice that two sources disagree, and the one place
that is forbidden to decide which of them is right. AI-DLC owns the lifecycle files; Studio only observes them
(architecture §3). So when `aidlc-state.md` says a gate is open and the audit trail has no matching
`STAGE_AWAITING_APPROVAL`, the answer is never "trust the state file" — it is a finding that carries **both** readings
as evidence and, when the disagreement could change which intent, stage, gate or artifact a decision applies to,
blocks the decision entirely (`409 state_inconsistent`, PRD §12.3 / FR-ACT-009). Two consequences look like missing
features until you know why:

- **Severity is not a matter of taste.** `blocking` is reserved for disagreements that could make a human decision
  land on the wrong boundary — a dangling cursor (the engine would resolve a different record), a gate the audit
  cannot corroborate (the approval may be forged or stale), a state version the installed engine does not write (the
  file may be interpreted by two different grammars). Drift that is merely *untidy* (a wrong `Completed` count, a
  receipt that is one release old) is `warn` and never stops work: FR-ACT-008 keeps maintenance out of the blocking
  queue.
- **Absence of evidence is reported, not resolved.** A truncated audit tail, an unreadable state file inside its grace
  window and an unstable read are `info` findings that say "this snapshot cannot authorise a decision yet", which is a
  different claim from "the workflow is broken".

The engine is pure: no file is opened, nothing is written, no clock is read (`RepoFacts.now` is supplied by the
caller, so a scan is reproducible from its inputs alone; timestamps are parsed by the backend's one
`constants.epoch_from_iso`, so every caller agrees about DST). That is what lets the reconciler call it once per intent per
scan on a worker thread, for 64 repositories without ordering constraints, and lets the tests build every
contradiction in the table from real fixture bytes. It is handed objects built by four other modules and reads them
structurally (`constants.attr`), so a snapshot assembled by hand in a test is indistinguishable from a real one.
`RepoFacts` carries what is not in the snapshot, each field from a layer the engine may not reach: the repo and
install from the registry, binding and slot from the session layer, live actions and leases from Storage, and
`first_unreadable_at` from the reconciler's per-intent memory (it is the only component that has seen the previous
ticks). Findings travel into the Action Center, the timeline and exports, so `params` hold no absolute path and no
credential, and `evidence` names sources, never their contents.

### 12.2 The finding table

- `state_unreadable` is the one code whose emitted severity is computed rather than looked up: a state file that is
  missing *right now* is usually a write in flight, so it is `info` until it has stayed unreadable for
  `STATE_UNREADABLE_GRACE_SECS` (review P26.2). Its table severity stays `blocking` because that is what it becomes,
  and `ConsistencyEngine.blocking` filters on the *emitted* severity, so a finding still inside its grace window does
  not block a decision that a moment later will be fine.
- `unit_ambiguous` is additive to §1.7's table, whose list predates per-unit Construction being read. It is emitted
  here rather than left to the reader because only this module may say two sources disagree, and the reader's refusal
  to guess produces a card with no questions on it, which on its own looks like a bug instead of a contradiction.
- `ACTION_STORED_FINDING_CODES` are codes the engine cannot derive from disk: the reconciler discovers them while
  resolving a delivery (it compared the human-presence baseline, the cursor, or the slot a transcript row landed in)
  and stores them on the action. Consistency lifts them back out so they join the blocking set, drive
  `operational_state` rule 2 and produce a `recovery` card — otherwise a presence anomaly would be visible only on one
  action's evidence blob (review P04/R01/R08). Several storage shapes are accepted (`evidence["findings"]` as codes or
  `{"code", "params"}` dicts, `evidence["finding"]` as either): the writer is a different module, and a finding
  silently dropped because it was stored as a string instead of a dict would remove a blocking guard.
- `RepoFacts.layout` is additive to §1.7 because `legacy_layout` is a disk fact about a repo that has NO spaces
  intents, so no `IntentSnapshot` exists to carry it; a caller that does not know the layout simply gets no layout
  finding.
- `evaluate_intent` folds the repository's own findings into every intent's list: an intent inside a repo whose
  install needs recovery, or whose identity is claimed by a second registry row, IS blocked, and `submit` step 4
  and the `recovery` card derivation both read this one list (§1.8/§1.12).
- A findings list is de-duplicated — a finding's identity is its code plus its params, so two missing artifacts are
  two findings and the same one twice is one; duplicates arise because repository findings are folded into every
  intent and two code paths can notice one contradiction — and ordered blocking → warn → info, so the reader sees what
  blocks first and the UI's `#f-<n>` anchors stay stable across scans that produce the same set.

### 12.3 Individual rules

- `dangling_cursor`: the engine's resolution order is "explicit arg > active-intent > lone intent" (05 §8), so a cursor
  that names a record without a state file makes it fall through silently to a DIFFERENT record.
- `phase_stage_disagreement`: both clauses of §1.7 are reported when both hold — which phase is running, and whether
  the stage being executed belongs to it, are different contradictions about the same field, and collapsing them would
  hide one.
- `unit_ambiguous`: two sources can say which unit a per-unit stage is running — the `unit` key of the directive
  marker and the `Active Unit` field the engine mirrors into the state file. When neither carries it and Construction
  holds several unit directories, the identity of the work at this boundary is unknowable: the reader refuses to pick
  one, so the card has no questions on it and, without this finding, reads as a Studio bug rather than as something
  the human has to resolve. Blocking for the same reason as `dangling_cursor`: the decision would land on a different
  piece of work than the one it was written for. Not reported when the reader found exactly one unit's questions file,
  because the unit is then recorded in that file's own path rather than guessed. The unit segment of that path is
  matched against the unit directories the reader enumerated rather than against a stage-slug denylist, which keeps
  this module out of the business of knowing the engine's stage names (`construction/<unit>/<stage>/…` and
  `construction/<stage>/…` both occur in one real record, §8).
- `missing_required_artifact`: artifacts listed in `produces_kinds` are skipped — they apply only to units of a given
  kind (service / ui / library / spec / packaging) and Studio cannot read a unit's kind from the state file, so
  requiring them would invent a blocking finding for a stage that legitimately never produced them.
- `lease_conflict`: an execution lease owned by another intent, or any admin lease, blocks this intent. Two turns inside
  one repository would interleave writes to the same engine files, and an admin operation (cursor switch, install,
  recompose) moves the very files a decision was captured against — both are contradictions between "what Studio is
  about to do" and "what Studio is already doing" (FR-SES-007).
- `delivery_uncertain_action`: `ReconciliationRequired` means Studio is not sure a decision
  landed (§1.12/C04); dispatching a second decision on top of an unresolved one is the at-most-once violation PRD §12.1
  forbids. `RepoFacts.live_actions` may be the whole repository's live set, so rows are filtered to the intent here
  rather than trusting the caller: one intent's uncertain delivery must not block a sibling intent.
- Audit ordering: Consistency compares events by the reader's merge key `(timestamp, shard_index, pos)`. Same-second
  events in different shards are causally unordered (04 §5.1); using the key the reader sorts by means Consistency
  never claims an order the merge did not already establish.
- Path evidence: the snapshot carries its own `relpath` for everything it read (state, audit shards, artifacts) and
  that is always preferred. The reader's layout names are used for the most important findings, which are about files
  the snapshot could NOT turn into an object — a dangling cursor, a missing registry row, an artifact that is not
  there.
