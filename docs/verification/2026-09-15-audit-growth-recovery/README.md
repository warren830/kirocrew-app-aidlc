# Audit growth and completed revision recovery

Integrated commit: `1746186edf2aef7a931c67735f846fcbdd0d4171`.

The real Functional Design revision returned to its approval gate, but the
earlier Request Changes action still held an execution lease. Its captured
audit boundary was ordinal 627. The old 256 KiB tail window rebased event
ordinals, so the later rejection could not be found after that boundary.
The complete history contained 659 events: rejection at 629, revising at 630,
and the returned gate at 658. It also proved exactly one human turn, 51 → 52.

There are two repairs:

- Default audit reads retain complete history within a shared 16 MiB budget
  across all shards. This keeps the former aggregate ceiling. Explicit tail
  limits and exhausted budgets still yield incomplete history.
- A finished revision can settle after its transient `R` state has passed,
  using a complete same-shard ordered rejection/revision/returned-gate chain,
  matching scope and revision numbers. Presence and cursor checks remain
  mandatory. The returned approval gate remains a separate user decision.

The new positive regression first failed with `Processing` instead of
`StateChanged`. The final focused run passed 594 tests across reader,
reconciler, gate API, audit-question and lease suites. Generated UI sources
were unchanged and all 25 backend modules imported under the gateway's
bundled interpreter.

The app-only update ran while the native slot was idle. Its sole execution
lease was the proven stuck action `a_0ee947d8f339b22a`; no administration lease
existed. No lease or native file was deleted to enable installation.
All 113 native workflow files retained identical hashes.

After installation, the action resolved as `StateChanged` with reason
`gate_revision_returned`; presence reported delta 1, a moved marker and
complete history. The lease list became empty. The next gate approval was
submitted once through the normal public API.

Evidence is in
`/Users/ychchen/warren_ws/aidlc-studio-backups/agentbridge-cron-20260915-170454/`:
`gate-return-offline-live-proof.json`, `gate-recovery-authority-before.json`,
`gate-recovery-installed.json`, and `gate-recovery-live-result.json`.
