# Requirement traceability fallback verification

Integrated commit: `d6a9967de279df7041928755456bb83bd5b80e8e`.
The patch is local to Studio's bundled 2.7.1 distribution; the upstream base
remains `a277af218f0df7f325d3b8be7b6d90fce2c5bd40`.

185 targeted sensor, manifest, installer and payload-refresh tests passed,
including 29 new sensor cases. They cover FR fallback, missing and wrong-unit
mappings, unknown identifiers, existing US mappings, and functional-design
unit scoping. A copied 64-FR demo also passed.

The app-only update completed at 2026-09-15 17:59 UTC while the native slot was
idle and both execution and administration lease counts were zero. App health
was healthy and all 293 payload entries matched their inventory.

The public upgrade preview selected exactly one changed file:
`.kiro/tools/aidlc-sensor-traceability.ts`. Same-version refresh transaction
`tx_b6a75df1b9d732d7` committed at 18:01 UTC, wrote one file, merged no fragments,
and validated all 293 payload entries. Its receipt is `rc_1593ce628f756f85`.
All 91 native `aidlc/` files had identical hashes across the app update, payload
refresh and standalone sensor run.

The installed sensor was run on the current demo's Units Generation
`traceability.json`. It returned:

```json
{
  "pass": true,
  "gaps": [],
  "orphans": [],
  "missing_from_table": [],
  "missing_from_upstream_ids": [],
  "invalid_entries": [],
  "invalid_targets": [],
  "findings_count": 0
}
```

This standalone check did not rewrite historical audit results or stage state.
The next unit approval was submitted once through the normal public API.
Browser inspection was unavailable because the Mac was locked and the browser
connector lacked its authentication token.

Receipts, hashes and the actual sensor output are in the local evidence folder:
`/Users/ychchen/warren_ws/aidlc-studio-backups/agentbridge-cron-20260915-170454/`,
under the `traceability-upgrade-*`, `traceability-live-sensor.*`, and
`studio-traceability-app-installed.json` names.
