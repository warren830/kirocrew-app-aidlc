# Ended reply with an unmatched audit receipt

The Agentbridge demo sent `Persist none`. The native agent recorded
`Nothing to add; persist none`, ended its turn and opened the Domain Design
approval gate. Exact reply verification correctly failed, but the old action
remained `Processing` and retained an execution lease with a two-hour deadline.
The next real gate decision was rejected as `repo_busy`.

The recovery does not relax exact-answer verification. A separate terminal
outcome, `answer_not_verified_at_gate`, requires all of:

- An observably ended turn.
- The existing human-presence and active-cursor proof.
- A receipt causally bound to the original audit question and attempt, whose
  text does not verify the sent reply.
- A newer approval gate for the same current stage and unit, with no other
  workflow scope.

The old action is closed as `ResolvedNoTransition` with `answer_verified: false`,
and its lease is released. UI labels explicitly say **Reply not verified** and
direct the operator to the conversation and new gate. The reply cannot be
retried through the terminal action. No approval or answer receipt is fabricated.

## Verification

The reproduction failed before the change; the seven initial controls passed.
After the repair, 289 audit/reconciliation tests and 71 UI tests passed, along
with typechecking and build. The final nine-case focused rerun passed, including
other-workflow, other-unit, old/no gate, missing presence, running turn and missing
receipt controls.

Live action `a_d5e79fe2ac9cd522` then settled with the unverified reason and zero
execution leases. The previously blocked Domain Design Request Changes was
submitted once successfully. No native workflow file changed during the
app-only update, and no old reply was resent.

The public idle-stop and recovery-acknowledgment actions performed before the
repair are retained in history. They did not falsify the old delivery or erase
its lease. The software repair and normal reconciliation performed the recovery.

Evidence is under:

```text
/Users/ychchen/warren_ws/aidlc-studio-backups/agentbridge-cron-20260915-170454
```

See `unverified-reply-live-resolution.json`,
`before-unverified-reply-workflow-hashes.json`,
`unverified-reply-recovery-install.log`, and the `idle-lease-*` receipts.
