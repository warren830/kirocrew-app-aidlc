# Observation-only refresh and submit concurrency

## Defect and repair

`Projection.captured()` timestamps each observation. Queued-card refresh copied
that time into `evidence.card.captured_at` and compared the full evidence object.
An otherwise identical scan therefore wrote a new action generation. If it
occurred between submit's snapshot checks and final CAS, submit failed despite
unchanged decision evidence.

Refresh now compares using the stored version's first observation time. A
clock-only refresh leaves the stored evidence, generation and update time
unchanged. A meaningful refresh still writes through CAS with the fresh
observation time. Card GETs continue to report their current read time.

No retry-adoption of a newer generation was added. Meaningful evidence changes
still reject stale submissions, and competing submissions still contend on the
repository lease. The six compared capture fields, snapshot stability checks,
and delivery acknowledgment deadlines retain their existing behavior.

## Tests

Before the fix, four focused cases failed, including two deterministic
refresh-during-submit reproductions; two safety controls passed. After the fix,
all six passed.

The main regression run passed 261 tests across the action broker, public action
handlers and reconciler. Coverage includes:

- Advancing the clock across otherwise identical refreshes.
- Fresh GET observation timestamps without stored-generation churn.
- A real derived-card refresh immediately before submit's final CAS.
- Changed artifact evidence still rejecting the stale CAS and releasing its lease.
- A competing submit being refused, with exactly one durable delivery.
- Frozen evidence on an in-flight action and unchanged acknowledgment timing.

## Live verification

The native Agentbridge demonstration was idle at question
`a_3f23085abd18291b`. Before updating Studio, two read-only observations six
seconds apart differed in exactly three stored fields:

```text
evidence_json.card.captured_at: 12:41:18Z -> 12:41:23Z
status_generation:            54 -> 55
updated_at:                   12:41:18Z -> 12:41:23Z
```

After the update, three observations at 12:43:14Z, 12:43:20Z and 12:43:27Z
retained the identical stored row at generation 61. The API observation timestamp
advanced on every GET and all six semantic capture values remained identical.
The subsequent reviewed `Nothing to add` reply submitted successfully once.

The update ran with zero execution/admin leases. All 76 files under this
demonstration's `aidlc` tree were byte-identical before and after it. The host was
not restarted.

Earlier rejected submissions were consistent with this race, but their old
evidence versions were not retained. This record does not claim to prove the
specific fields that changed during those earlier historical failures.

## Local evidence

```text
/Users/ychchen/warren_ws/aidlc-studio-backups/agentbridge-cron-20260915-170454
```

- `studio-observation-cas-checks.log`
- `clock-only-refresh-live-before.json`
- `clock-only-refresh-live-after.json`
- `studio-observation-cas-install.log`
- `before-cas-update-workflow-hashes.json`
- `after-cas-update-health.json`
- `decision-a_3f23085abd18291b.*.json`
