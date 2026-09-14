# Learnings question priority handoff

Status: complete, frozen; ownership of both files released to main. No live fixture, gateway, UI, constants, or other shared-source files were modified. No install or full check was run.

## Changed files

- backend/studio/aidlc_reader.py: preserve pending structured ownership; allow a closed sheet/checkpoint to yield to a provably later audit question only when a current-attempt checkpoint receipt binds the current file. Re-read and match whole-file digest; include receipt identity in compare-and-submit evidence.
- tests/test_audit_questions.py: 40 new cases, including the byte-exact saved answered sheet, HTTP/broker submit and disk-answer reconciliation, at-most-once, stale receipts, current unit/generation, unknown scope, unreadable/partial evidence and chronology.

## Evidence and mechanism

The saved answered sheet's whole-file SHA is d39610aafad7ed5d41279d5e43cabc7bf4373661b01b41315238917c17085bd0.
The actual confirmed-content-v1 receipt hashes normalized CR/CRLF newlines and ECMAScript trimEnd content: 57380ab3e835228c56dae5993bc4dceab9993a4b7ea0cbeafd41bef53543f26c.
The original unconditional structured-file return hid the fresh Learnings decision. A matching successful summary/plan checkpoint receipt, after all current attempt floors and strictly before the new decision, now permits it. Same-second cross-shard ambiguity, missing receipts, changed files, current claim changes and pending/rejected checkpoints retain file ownership. Receipt identities are captured, so replacing a valid receipt also invalidates a queued submission.

Real read-only replay used the full single-shard audit, not the 50-event API tail. Historical prefix through pos95 contained 96 events; pos88 closed the saved sheet, pos95 exposed exactly Nothing to add / Add a note. After the actual Nothing to add answer at pos97, no audit question remained. Live sheet bytes still equaled the saved sheet. See /tmp/aidlc-learnings-priority-replay.json.

## Focused commands and results

All commands run from /Users/ychchen/warren_ws/kirocrew-app-aidlc with the existing Python environment.

Red, before production changes (exit 1; 10 failed, 30 passed, 113 deselected):

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/ychchen/warren_ws/kirocrew/.venv/bin/python -m pytest tests/test_audit_questions.py -k closed_sheet -q -o addopts= -p no:cacheprovider > /tmp/aidlc-learnings-priority-red.log 2>&1
```

The same 40 new cases passed after the fix. Final focused verification (exit 0; 189 passed, 92 deselected in 138.85s), includes all 153 audit-question cases plus 36 relevant reader cases:

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/ychchen/warren_ws/kirocrew/.venv/bin/python -m pytest tests/test_audit_questions.py tests/test_aidlc_reader.py -k 'question or closed_sheet or audit' -q -o addopts= -p no:cacheprovider > /tmp/aidlc-learnings-priority-green.log 2>&1
```

Read-only actual-history replay (exit 0):

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/ychchen/warren_ws/kirocrew/.venv/bin/python /tmp/aidlc-learnings-priority-replay.py
```

## Memory-only mutation evidence

No shared production source was mutated. The disposable runner changes functions only in process memory and returns pytest's result after flushing output; each mutant exited 1. Production hashes remain those listed below.

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/ychchen/warren_ws/kirocrew/.venv/bin/python /tmp/aidlc-learnings-priority-mutation.py ignore_receipt_hash > /tmp/aidlc-learnings-priority-mutation-hash.log 2>&1
PYTHONDONTWRITEBYTECODE=1 /Users/ychchen/warren_ws/kirocrew/.venv/bin/python /tmp/aidlc-learnings-priority-mutation.py ignore_attempt_floor > /tmp/aidlc-learnings-priority-mutation-floor.log 2>&1
PYTHONDONTWRITEBYTECODE=1 /Users/ychchen/warren_ws/kirocrew/.venv/bin/python /tmp/aidlc-learnings-priority-mutation.py omit_receipt_identity > /tmp/aidlc-learnings-priority-mutation-identity.log 2>&1
```

- Ignore hash: 2 failed, 8 passed; mismatched hash and whole-file hash mislabeled as scoped could expose audit.
- Ignore attempt floor: 1 failed; an old-attempt receipt could expose audit.
- Omit receipt identity from capture: 1 failed; changed evidence wrongly returned HTTP 200 instead of 409 action_stale.

## Conservative compatibility limits

Legacy answered questions without a bound checkpoint receipt still retain file ownership. Unknown hash scopes and scoped receipts that exclude a later Assumption Confirmation section remain conservative: this narrow implementation verifies the complete normalized content and refuses unmatched scoped exclusions rather than porting the engine's Markdown hash parser. No UI or API shape change is required; audit origin and exact option labels retain the existing interface. Fenced-code parsing is unchanged and its regressions pass.

## Frozen SHA-256

- backend/studio/aidlc_reader.py: 8eaaaae9354d214fe038481fc1510134c881e9008fdfcef6037f4787e2a47281
- tests/test_audit_questions.py: c60360d00ec7316fcd1d562f01156c506708f8e2ccf36738891a256e92cc1f79
