# Unsupported question fallback verification

Installed source commit: `aae1790bbe75871bb6cdc34fbb0c54510c9a067f`.
Installation completed at 2026-09-15 17:16 UTC.

The native demo slot was idle and both execution and administration lease counts
were zero before the app-only update. All 83 files under the demo's `aidlc/`
directory had identical SHA-256 hashes before and after installation. Health was
`healthy`, the bundled engine had zero payload mismatches, and the reconciler was
running. The next Domain Design approval was delivered once through Studio's
normal submit and delivery APIs.

Combined validation with the unverified-reply recovery change passed 394 backend
tests, 103 frontend tests, type checking, i18n validation, and the UI build.
These test runs overlap the individual patch runs and must not be added together.

The regression fixture is `tests/fixtures/question-fallback/domain-followups.md`.
There was no new unsupported pending question at installation time, so this
record does not claim a live browser reproduction of the fallback.

Local installation evidence is in
`/Users/ychchen/warren_ws/aidlc-studio-backups/agentbridge-cron-20260915-170454/studio-fallback-installed.json`.
