# Live API notes for the dashboard UI

Written by the backend integration gate after registering all 67 routes against the running gateway and
driving the vertical slice end to end. These are the places where the live behaviour is more specific than
the contract, or where an obvious reading would be wrong. `docs/design/contracts.md` §2 remains the shape
of record; this file is the errata.

- LIVE API IS UP. Base `/api/apps/aidlc-studio`, 67 routes registered in exactly §2.10's order (67 also equals the cell count of the §2.10 table — verified programmatically). Host is `0.5.0-insider.9`; the app declares `minKiroCrewVersion 0.3.0` and both loader generations are proven to link. Re-install with `scripts/dev-install.sh --no-build`; call it with `scripts/kcapi.sh GET|POST <path> [json]`.
- DO NOT gate UI buttons on `health.host.capabilities.owner`. On a desktop gateway without Slack it reports `{available: false, reason: "owner_unknown"}` — that is only the *probe* failing to read `state.owner_id`. The actual owner gate uses the host's own `is_owner_dashboard_request`, and a cookie session passes it: `POST /repos/preflight` and `POST /repos` both succeeded live. Treat `capabilities.owner` as diagnostics only.
- `GET /events/poll` with NO `cursor` returns an empty backlog by design (a cold start is about to fetch every resource anyway). Pass `cursor=0` for a full ring replay, or the last `id` you saw. The SSE route `GET /events` follows the same rule with `Last-Event-ID`.
- `POST /repos` returns **201** and the `preflight` in its body is taken AFTER the insert, so on success `preflight.duplicate_of == repo.repo_id` and `preflight.can_register == false`. That is not an error — do not render it as one. Use `POST /repos/preflight` (before adding) for the registrability answer; there `duplicate_of` is null and `can_register` is true.
- Intent JSON shape gotcha: the state file's own claims live under `intent.disk` (`current_stage`, `status`, `next_stage`, `state_version`, `total_stages`, `completed`, `revision_count`, `parked_at`, `last_updated`) — there is no `intent.state`. `IntentDetail` is a strict superset of `IntentSummary`, so the rail and the detail page can share one type.
- `evidence.cursor_readback` uses the key `mismatch` (a list), singular — not `mismatches`. It also carries `ok`, `space`, `dir_name`, `uuid`, `state_sha256`, and `state_sha256` always equals `captured.state_hash` on a successful submit.
- Terminal actions carry `action.resolution = {kind, resolved_at, evidence, reason}`; `kind` is `"state_changed"` / `"no_transition"` and `reason` is the contract row that matched (e.g. `"gate_approved"`). There is no `resolution_reason` column on the wire.
- The submit receipt's `host` block is byte-exact and must be POSTed unchanged: `{method:"POST", path:"/api/chat?ws=1", body:{message:<wire_text>, slot:<slot_key>, agent:"aidlc", meta:{studio_action_id, studio_delivery_id}}}`. Confirmed by the e2e slice that the host slot is provably empty (`messages == []`, `running == false`) at the moment the receipt is returned, while the row already says `Delivering` with the process `boot_id`, the presence baseline and the cursor read-back stored. Keep `meta` — it is the reconciler's primary delivery-match key.
- `not_delivered` needs an authoritative 4xx or one of the two client preflight codes (`slot_missing`, `slot_mismatch_preflight`), and the backend re-checks anyway: with a matching transcript row you get `409 not_delivered_unproven`, `details.checks = {row_absent, slot_idle, disk_baseline_unchanged, boot_unchanged}`, and the row moves to `DeliveryUncertain`. Repeating the same `delivery_id`+outcome returns `200 {idempotent: true}`.
- AFTER A GATEWAY RESTART mid-delivery, a `Delivering` row deterministically lands on `ReconciliationRequired` (never `NotDelivered` — a different `boot_id` closes that path by construction). The card then offers exactly `{reconcile, mark_not_delivered, resubmit}` and `mark_not_delivered` is still refused without proof. Build the recovery UI for that triple; do not offer a bare 'retry'.
- Capability flags observed live and worth branching on: `grouped_answers` false (`s1_s2_unverified`) — offer the conversation deep link for multi-question groups; `slack_quick_actions` false (`host_seam_unavailable`); `night_window` false (`machine_lane_unavailable`); `credit_cap` false (`credits_unobservable`); `advisor` true. `/health` also reports `machine_lane: {available: false, reason: "machine_lane_unavailable"}` — there is no machine lane, and the broker refuses with `details.reason = "s12_unproven"` and records `machine_lane.refused` in activity.
- `ACTIVITY_KINDS` now has 41 members: `slack.callback` was added. Whoever writes the i18n `activity.*` keys must include `activity.slack.callback`; the catalog currently has zero `activity.*` keys and `scripts/build_i18n.py --check` passes with 177 keys per locale.
- tests/test_static_policy.py will fail the build on: `shell=True` or a non-literal-`False` `shell=`, `os.system`/`os.popen`/`os.exec*`/`os.spawn*`, any spawn outside engine.py/git_observer.py, an unambiguous git write verb as a string literal or as the head of a string sequence, `AIDLC_SKIP_` outside constants.py/security.py, any executable reference to the host's private dispatch surface (`_run_chat`, `spawn_guarded_turn`, `run_background_turn`, `enqueue_or_run_prompt`, `get_or_create_slot`, `queue_append`, `stop_turn`, `broadcast_ws`, `push_refresh`, `push_slots_update`, `link_slack`, `set_slack_link`, …), any `<something-slot>.append(...)`, an import of a private `kiro_crew.*` module or `kiro_crew.dashboard.chat*`, an absolute intra-package import inside `backend/studio/**`, a relative import in `backend/{routes,hooks}.py`, and `dangerouslySetInnerHTML`/`innerHTML =` anywhere in `ui/src`. Docstrings that name a forbidden call are exempt on purpose.
- tests/test_loader.py additionally pins two enable-path budgets that any future backend change must keep: `register_routes` must not open the SQLite store (only `on_startup` does), and importing both entry points must stay under 5 s. It also proves a re-enable re-reads the files from disk, which is what makes `dev-install.sh` honest.

## deviations

- THE LIVE RUN WAS BROKEN AND IS NOW FIXED (root cause). Before this pass the app enabled *degraded* in the real gateway: `Route registration failed: module '_aidlc_studio_backend.studio' has no attribute 'services'`, `Lifecycle hook failed: backend.hooks:on_startup`, and `/health` answered `{"error":"not found"}` — on every enable since first install. The host rolls a failed module load back by deleting the `_kirocrew_app_*` keys IT created (kc:apps/module_loader.py `_rollback_app_modules`); it cannot see `_aidlc_studio_backend`, because Studio invents that name. One import that died half-way during development therefore left a partially initialised `_aidlc_studio_backend.studio` in a gateway process that then ran for days, and every later `bootstrap()` got that corpse back from `import_module`. Fix: the bootstrap block is now self-healing — it purges its own namespace on any exception out of the import, and a package that comes back without `constants`/`services`/`handlers` is purged and imported once more before it gives up. This is additive to the §1.25 snippet and is why the app is now `hooks_startup: ok`.
- backend/hooks.py no longer does `from .routes import bootstrap, teardown_namespace`. That was a relative import at the `backend/` level, which §1.25 forbids outright and which cannot resolve on the 0.3.0 loader (no parent package is registered). The bootstrap block is now duplicated **verbatim** in routes.py and hooks.py exactly as §1.25 prescribes; a test asserts the two copies are byte-identical, and tests/test_loader.py fails if either file ever uses a relative import again. `on_shutdown` also calls `teardown_namespace()` in a `finally`, so a container that raises while closing its store can no longer leave the old modules resident (which would make `dev-install.sh` silently keep running replaced code).
- The bootstrap helpers are the public names `bootstrap()` / `teardown_namespace()`, not §1.25's `_bootstrap()`. Same behaviour; the public spelling is what the two entry points and test_loader.py address.
- `probe_bun_version` moved from services.py to engine.py. It was the one failing test at the start of the pass: test_engine.py's `test_only_the_subprocess_modules_spawn_anything` pins one spawn door per external tool, and bun's door is engine.py. services.py now imports it and no longer imports `subprocess` at all. My own tests/test_static_policy.py enforces the same rule (`SPAWN_MODULES == {engine.py, git_observer.py}`), so this could not have been left as it was.
- Four `getattr(C, "…", default)` fallbacks were removed by adding the constants §1.1 actually specifies: `BOOT_ID_BYTES = 8`, `SCAN_POOL_WORKERS = 2`, `SCAN_TICK_BUDGET_SECS = 4.0`. Callers in services.py and reconciler.py now read them directly; settings.py reads `C.GROUPED_ANSWERS_VERIFIED` directly (it already existed).
- `ADVISOR_AGENT_NAME = "aidlc-studio-advisor"` added to constants.py. §1.1 does not list it but §4.3 requires `tests/test_manifest.py` to assert `agents/advisor.json` name `== constants.ADVISOR_AGENT_NAME`; advisor.py was carrying the literal behind a `getattr` fallback. Now one constant, asserted equal to the JSON, to advisor.py's re-export, and to the string `spawn.run` is given.
- Two handler modules were importing private helpers across the module boundary; both are now public: `activity._studio_refs` → `activity.studio_refs` (added to `__all__`), `installer._version_tuple` → `installer.version_tuple`. handlers/events.py and handlers/repos.py import the public names.
- CONTRACT SELF-CONTRADICTION resolved: §2.9 says `POST /slack/actions/callback` "Records an Activity row (`source: slack`, kind `slack.callback`)", but §1.17's `ACTIVITY_KINDS` list does not contain `slack.callback` — and `ActivityProjector.record` refuses any kind outside that vocabulary, so the route's contractual side effect could never happen (handlers/slack.py was catching the refusal and logging a warning). Resolved in favour of §2.9: `slack.callback` added to `ACTIVITY_KINDS`; verified live that the route now writes exactly one row with `source: slack`. This required updating the pinned `CONTRACT_KINDS` literal in tests/test_activity.py — **the one file outside my task list that I edited** — with a comment naming the §1.17/§2.9 conflict and the resolution. The i18n gate is unaffected (the catalog carries no `activity.*` keys yet).
- conftest.py does not match §4.1 and was NOT modified (it is on the do-not-rewrite list): `load_backend()` takes no `mode` argument, and there is no `services` fixture, no `call()` helper and no `services.restart()`. Consequently tests/test_loader.py emulates both loader generations in **subprocesses** (sys.modules is process-global; loading the backend a second way in-process would corrupt the session-scoped module objects every other file shares, and running `on_shutdown` in-process would delete them), and tests/test_e2e_gate.py reuses the `sv`/`routes`/`call` harness that tests/test_handlers_auth.py already exports and implements the `restart` helper locally.
- §2.4's error table abbreviates the third `not_delivered_unproven` check as `disk_unchanged`; the wire key is `disk_baseline_unchanged` (which is what §2.11's `evidence.delivery` uses). Confirmed on the live path; §2.11's spelling wins.
- `NOTIFY_KIND = "aidlc_studio"` stays in sessions.py rather than constants.py. §1.1 does not list it and sessions.py is its only caller; noted rather than moved.

## known gaps

Six entries the 2.6.2 → 2.7.1 payload bump proved. The first is now **fixed** and is kept as the record of what changed
(its contract home is now `contracts.md` §1.14/C21 and architecture A27). The second is the same defect in a third file
and is still open, because it needs a merge strategy that does not exist yet. The remaining four are deliberately left
alone, each with the evidence and the fix that is ready to apply. The five open entries live here rather than in
`contracts.md` because none of them changes a contract — the contract is already what it should be; the payload manifest
or the code is what disagrees with it.

- **RESOLVED — `scope-grid.json` and `harness.json` are now `merge` targets, not `framework`.** The defect:
  `payload/manifest.json` gave `.kiro/tools/data/scope-grid.json` ownership `framework`, but AI-DLC's own composer
  appends the composed-scope key to the installed copy (DevDelta's has a 10th key `review-major-remediation`; the payload
  ships 9 in 2.6.2 and 11 in 2.7.1), so the first upgrade of **any** repository that ever composed a scope read
  `owned_modified` — blocking under §1.14 with no force path in v1 — and such a repository could never be installed into
  or upgraded again. Pre-existing in 2.6.2, not caused by the bump. Investigating it turned up the same shape in
  `.kiro/tools/data/harness.json`: `aidlc-utility select-plugins` (`/aidlc plugin select`, a documented verb that works
  in a stock install where the only known plugin is `aidlc` itself) appends a `plugins` array to the installed copy —
  proved by running the shipped 2.7.1 payload's own command against a clean copy, where `harness.json` was the only
  receipt-owned file whose bytes changed — so one innocuous-looking command permanently blocked every later upgrade. The
  fix that landed with the bump: both paths are classified `merge` with `strategy: json-managed-keys` and `managedKeys`
  computed from the payload file itself (the `None`-sentinel derivation in `scripts/build_payload_manifest.py` that
  `mcp.json`'s `mcpServers.*` keys already used) — the eleven stock scope names, and `harness.json`'s three shipped
  identity keys `harnessDir`/`name`/`rulesSubdir`. The manifest now carries **six** merge targets and 249 (not 251)
  `framework` files. Verified against the real composed repository's bytes: the receipt fragment and the live fragment
  agree, the entry resolves to `merge_update`, Studio refreshes the stock scope rows / identity keys and preserves the
  user's composed key, `plugins` selection and operator config byte-for-byte (`contracts.md` §1.14, C21, architecture
  A27). `framework-mutable` was rejected for both: it is non-blocking but it overwrites, which would have deleted the
  composed scope's grid and silently re-enabled plugins the user disabled.
- **`.kiro/skills/aidlc/SKILL.md` is the third file of that class and is still `framework`, so it still blocks.** Its
  two generated regions (`stage-table`, `scope-table`) are rewritten in place by the same selection-surface pass
  (`replaceGeneratedRegion` writes the installed SKILL.md), so a repository that has composed a scope and then runs
  `/aidlc gen scope-table`, `/aidlc plugin select` or a plugin compose drifts by exactly one table row and reads
  `owned_modified` — blocking, no force path. Confirmed by execution on a composed-repository simulation built from the
  2.7.1 payload plus the DevDelta fixture's composed scope: the only diff against shipped was one appended
  `scope-table` row. So the two reclassifications above make a composed repository upgradeable, but not
  unconditionally — this one is the remaining hole and is stated rather than assumed away. It cannot reuse an existing
  strategy: the managed span is the whole file *except* two sentinel-delimited regions, the inverse of
  `append-fenced-block`, so it needs a new `markdown-generated-regions` strategy that receipts the digest with the
  regions between `STAGE_TABLE_BEGIN/END` and `SCOPE_TABLE_BEGIN/END` masked out. `.kiro/tools/data/stage-graph.json` is
  deliberately **not** in this list although the engine rewrites it too: the shipped bytes are a compile fixed point
  (`compile --check` exits 0 on a verbatim payload copy, a real `compile` write reproduces the bytes, the real 2.6.2
  install's copy is byte-identical to shipped 2.6.2, and composing a scope never reaches the file), so it does not drift
  — and `framework-mutable` would let an upgrade discard the stage numbers the compiler pins from the copy on disk.
- **`aidlc/spaces/default/memory/org.md` is `shell`, so an existing space keeps the old rule text.** The seed copy
  `.kiro/tools/data/memory-seed/org.md` is `framework` and the upgrade rewrites it; the instantiated space copy is
  `shell` and is never touched — correctly, it is user data. The consequence is that a space created after the upgrade
  gets 2.7.1's rules while one created before silently keeps 2.6.2's. The fix is a warning, not an overwrite:
  FR-INST-005 reports the divergence and the user decides.
- **`MAX_AUDIT_SHARDS` (64) is now reachable, and truncation by the cap is not reported.** Shards are per clone
  (`audit/<host>-<cloneId>.md`, FORMAT-NOTES §6) and 2.7.1 gives each claimed unit its own clone or worktree mirror
  (`aidlc-utility unit adopt <unit>` in a fresh clone; `worktreeAuditFilePath` in `aidlc-lib.ts`), so the shard count in
  one record now scales with how many units are worked in parallel and a team-owned intent can exceed 64.
  `AidlcReader.read_audit` slices `[: C.MAX_AUDIT_SHARDS]` and then sets
  `complete = not any(shard.truncated for shard in shards)`; `truncated` covers only per-shard tail reading,
  so dropping whole shards leaves `complete` True and an evidence claim built on a partial history does not say so. The
  fix is one more term in that expression — the `audit_incomplete` finding already exists to carry it.
- **The state file's new `## Unit Progress` board is invisible to Studio.** 2.7.1 renders it as a markdown table
  (`aidlc-orchestrate.ts:1100-1108`: a `| unit | owner | … | gate |` header plus a separator row, then one row per
  unit). Studio's state parser reads `- **Field**: value` (`FIELD_RE`) and `- [x] slug` rows (`CHECKBOX_RE`), and a
  table row is neither, so per-unit progress never reaches the UI. Nothing is misread — the rows are skipped — so this
  is a missing feature rather than a defect.
- **The recompose / scope-change / config-change lane still has no cursor pin under its admin lease.** `plan.py` now
  passes `--intent` and `--space` explicitly, which fixes *which* record the engine edits, but it never calls
  `switch_cursor_sync`, so the repository-shared cursor can still point at another intent while the admin lease is
  held. The explicit selectors are sufficient for the edit to be correct; a pin would additionally line this lane up
  with the engine's own audit lock and with C22's rule for the human lane.
