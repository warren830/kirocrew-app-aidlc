# Fixture: aidlc-older (AI-DLC 2.1.1, State Version 7)

Scrubbed copy of the single intent in the AI-DLC **2.1.1** Kiro install at `/FIXTURE/ws/devlake` (32-stage graph, **State Version 7**), harvested 2026-09-04, to exercise version tolerance.

Copied: `intents.json`, `active-intent`, `aidlc/.aidlc-clone-id`, `260707-trello-plugin/aidlc-state.md` (feasibility `[-]` in progress, `Stages to Skip: 2.1 (reverse-engineering — greenfield)`, `Total Stages: 31`, TWO blank lines after `## Runtime State`, `Initialization: Active` while `Lifecycle Phase: IDEATION`), `.aidlc-recovery.md`, the WHOLE single audit shard (346 blocks; includes the non-taxonomy events `LOOP_RUN_STARTED` and `RULE_LEARNED` plus gate/question/error rows), every `*-questions.md` (`feasibility-questions.md` has SIX blank `[Answer]:` tags — the pure pending-question case; `intent-capture-questions.md` and `market-research-questions.md` are answered), the 2.1.x-style `.aidlc-learnings-selections-intent-capture.json`, `artifacts-index.txt`, and the 2.1.1 `stage-graph.json`/`scope-grid.json`/`harness.json`/`aidlc-version.ts`.

Not present in the source and therefore absent here: `aidlc/active-space`, `.aidlc-active-directive.json`, `.aidlc-human-turn`, `.aidlc-engine-touch`, `.aidlc-steering-token-key`, `traceability.json`.

## Scrubbing rules (applied in this order to every copied text file)

1. `repo_prefix` — the source repository's absolute path -> `/FIXTURE/repo`.
2. `ws_prefix` — the workspace directory (`$AIDLC_FIXTURE_WS/` and its `~/<name>/` spelling) -> `/FIXTURE/ws/` (sibling checkouts keep their basename).
2b. `home_prefix` — any remaining `/FIXTURE/home/` (or the real home dir) -> `/FIXTURE/home/`.
3. `email` — `<local>@<host>.<tld>` -> `REDACTED`.
4. `aws_key` — `AKIA…`/`ASIA…` access-key ids -> `REDACTED`.
5. `slack_token`, `github_token`, `openai_key`, `private_key` — well-known credential shapes -> `REDACTED`.
6. `keyword_secret` — a secret-ish keyword (`token`, `secret`, `password`, `passwd`, `api_key`/`api-key`/`apikey`, `bearer`) followed by a separator and a 16+ character alphanumeric value: the keyword is kept, the value becomes `REDACTED`. SHA-256 digests in `**Questions SHA-256**`, `**Artifact Fingerprint**` and `state_sha256` are untouched (no keyword precedes them).

Everything else is byte-faithful: files are decoded as UTF-8, regex-substituted, re-encoded as UTF-8. Unchanged files re-encode to identical bytes (verified by the sha256 columns below).

### Replacements actually made

| file | rule | count |
|---|---|---|
| `aidlc/spaces/default/intents/260707-trello-plugin/aidlc-state.md` | repo_prefix | 1 |
| `aidlc/spaces/default/intents/260707-trello-plugin/audit/603e5f4a8072-4c59c6cec037.md` | repo_prefix | 24 |
| `aidlc/spaces/default/intents/260707-trello-plugin/audit/603e5f4a8072-4c59c6cec037.md` | ws_prefix | 1 |

## Provenance (dest <- source, transform, sizes, sha256)

| dest | source | transform | src bytes | dst bytes | src sha256 | dst sha256 |
|---|---|---|---|---|---|---|
| `aidlc/.aidlc-clone-id` | `/FIXTURE/ws/devlake/aidlc/.aidlc-clone-id` | scrub-only | 13 | 13 | `40d9a4d24727a28628a19f6f04a8a5461ddaed5c43b3252d7634ada683bb9fcf` | `40d9a4d24727a28628a19f6f04a8a5461ddaed5c43b3252d7634ada683bb9fcf` |
| `aidlc/spaces/default/intents/intents.json` | `/FIXTURE/ws/devlake/aidlc/spaces/default/intents/intents.json` | scrub-only | 182 | 182 | `ed80efd1b995e915d80e7136501833f8d8a962cab836d9db196777882d8d8c8f` | `ed80efd1b995e915d80e7136501833f8d8a962cab836d9db196777882d8d8c8f` |
| `aidlc/spaces/default/intents/active-intent` | `/FIXTURE/ws/devlake/aidlc/spaces/default/intents/active-intent` | scrub-only | 21 | 21 | `181e68fcec5a1c8b1d827d722bae8abb16cf28336c118a978d5e6e45fda41d5f` | `181e68fcec5a1c8b1d827d722bae8abb16cf28336c118a978d5e6e45fda41d5f` |
| `aidlc/spaces/default/intents/260707-trello-plugin/aidlc-state.md` | `/FIXTURE/ws/devlake/aidlc/spaces/default/intents/260707-trello-plugin/aidlc-state.md` | scrub-only | 3014 | 2995 | `f20862adea9053dc5f8060495395efba585e6f0ee0c861f5d1c9ad3adc1cb15c` | `939ba94decc99a21fadac9c6145d1c517694065a68b30c691af91896a388519f` |
| `aidlc/spaces/default/intents/260707-trello-plugin/.aidlc-recovery.md` | `/FIXTURE/ws/devlake/aidlc/spaces/default/intents/260707-trello-plugin/.aidlc-recovery.md` | scrub-only | 154 | 154 | `38dd1c51bfb7a156a91cedb0d11671e2354c4d0c23ec8c9e3ff0a22b5f706fa2` | `38dd1c51bfb7a156a91cedb0d11671e2354c4d0c23ec8c9e3ff0a22b5f706fa2` |
| `aidlc/spaces/default/intents/260707-trello-plugin/.aidlc-learnings-selections-intent-capture.json` | `/FIXTURE/ws/devlake/aidlc/spaces/default/intents/260707-trello-plugin/.aidlc-learnings-selections-intent-capture.json` | scrub-only | 425 | 425 | `8b0fe54559a03233db67fda68562875f9161b0b2262e3a68cdf8ee9c7f1e8561` | `8b0fe54559a03233db67fda68562875f9161b0b2262e3a68cdf8ee9c7f1e8561` |
| `aidlc/spaces/default/intents/260707-trello-plugin/audit/603e5f4a8072-4c59c6cec037.md` | `/FIXTURE/ws/devlake/aidlc/spaces/default/intents/260707-trello-plugin/audit/603e5f4a8072-4c59c6cec037.md` | whole shard (346 blocks) | 68579 | 68123 | `163f9dc8ee5e75911840ae9dfa53c9c7efab6570b206dc2f2f9688228fc02c5b` | `a7215f5aa0d2634e9a2f1bb942878bf59bd5f8579573e67f6422c5e3c5dfcf45` |
| `aidlc/spaces/default/intents/260707-trello-plugin/ideation/feasibility/feasibility-questions.md` | `/FIXTURE/ws/devlake/aidlc/spaces/default/intents/260707-trello-plugin/ideation/feasibility/feasibility-questions.md` | scrub-only | 2136 | 2136 | `b3b8160fff364b43a26b1ba08cdb6ae0c8814284fb3ae71a368b930ab980af07` | `b3b8160fff364b43a26b1ba08cdb6ae0c8814284fb3ae71a368b930ab980af07` |
| `aidlc/spaces/default/intents/260707-trello-plugin/ideation/intent-capture/intent-capture-questions.md` | `/FIXTURE/ws/devlake/aidlc/spaces/default/intents/260707-trello-plugin/ideation/intent-capture/intent-capture-questions.md` | scrub-only | 3657 | 3657 | `13b98abdb6597df3085dde974b816b3d6a7e13133cbf56055f959dcea04c8c8e` | `13b98abdb6597df3085dde974b816b3d6a7e13133cbf56055f959dcea04c8c8e` |
| `aidlc/spaces/default/intents/260707-trello-plugin/ideation/market-research/market-research-questions.md` | `/FIXTURE/ws/devlake/aidlc/spaces/default/intents/260707-trello-plugin/ideation/market-research/market-research-questions.md` | scrub-only | 4010 | 4010 | `2ddbc850d8e70c75e3d72ce82cab92f99ee9a56ca94aad3359883a750e998d50` | `2ddbc850d8e70c75e3d72ce82cab92f99ee9a56ca94aad3359883a750e998d50` |
| `aidlc/spaces/default/intents/260707-trello-plugin/artifacts-index.txt` | `/FIXTURE/ws/devlake/aidlc/spaces/default/intents/260707-trello-plugin (directory listing)` | generated index | -1 | 1908 | `-` | `e4e09f32b26d4ebd6bf2031df12577662a0c63a98200b9cf835b82988f5dd309` |
| `.kiro/tools/data/stage-graph.json` | `/FIXTURE/ws/devlake/.kiro/tools/data/stage-graph.json` | scrub-only | 72157 | 72157 | `d7758903d6f27cc3c7697800eb7e7efb60d19d2d40b56d3f81585d4e7cfd6e76` | `d7758903d6f27cc3c7697800eb7e7efb60d19d2d40b56d3f81585d4e7cfd6e76` |
| `.kiro/tools/data/scope-grid.json` | `/FIXTURE/ws/devlake/.kiro/tools/data/scope-grid.json` | scrub-only | 10786 | 10786 | `dbcbd5db852f3b625d2da884077e76b5a6f6d42b4d8a46dfe2fdf2550d13f654` | `dbcbd5db852f3b625d2da884077e76b5a6f6d42b4d8a46dfe2fdf2550d13f654` |
| `.kiro/tools/data/harness.json` | `/FIXTURE/ws/devlake/.kiro/tools/data/harness.json` | scrub-only | 57 | 57 | `4d3d4502da56439483a79a56562e098519f319496aa9b234915ccc16b84fe09e` | `4d3d4502da56439483a79a56562e098519f319496aa9b234915ccc16b84fe09e` |
| `.kiro/tools/aidlc-version.ts` | `/FIXTURE/ws/devlake/.kiro/tools/aidlc-version.ts` | scrub-only | 257 | 257 | `d7aede2d0da0160a93442ac06015d4678cfd1aa0d0d636eb8524942b63b1f632` | `d7aede2d0da0160a93442ac06015d4678cfd1aa0d0d636eb8524942b63b1f632` |

## Harvest notes

- SOURCE HAS NO `aidlc/active-space` (2.1.1 never wrote it) — deliberately not created here; readers must default to `default`.
- `aidlc/spaces/default/intents/260707-trello-plugin/audit/603e5f4a8072-4c59c6cec037.md`: kept blocks [0, 346) of 346; events in the kept window: HUMAN_TURN×109, SENSOR_FIRED×40, SENSOR_PASSED×34, SUBAGENT_COMPLETED×34, SESSION_ENDED×25, SESSION_STARTED×20, ARTIFACT_UPDATED×13, SESSION_RESUMED×9, QUESTION_ANSWERED×7, ARTIFACT_CREATED×7, STAGE_STARTED×6, SENSOR_FAILED×6, STAGE_COMPLETED×5, DECISION_RECORDED×4, LOOP_RUN_STARTED×4, RULE_LEARNED×4, SESSION_COMPACTED×3, PHASE_STARTED×2, ERROR_LOGGED×2, STAGE_AWAITING_APPROVAL×2, GATE_APPROVED×2, WORKFLOW_STARTED×1, WORKSPACE_SCAFFOLDED×1, WORKSPACE_SCANNED×1, WORKSPACE_INITIALISED×1, PHASE_COMPLETED×1, PHASE_VERIFIED×1, GUARDRAIL_LOADED×1, HEALTH_CHECKED×1

Generated by `tests/fixtures/harvest_fixtures.py` (run with `AIDLC_FIXTURE_WS=<workspace dir>`); re-run it to refresh (it deletes and rebuilds this directory). In this file `/FIXTURE/ws` stands for that workspace directory and `/FIXTURE/home` for the account home.
