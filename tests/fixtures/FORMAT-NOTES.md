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
- Question headings `## Q1. …` (Chinese text in all real files); options either `A. …` (poc intent-capture) or
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
