# Requirements Analysis — Clarifying Questions

Stage: `requirements-analysis` · Depth: Standard · Scope: `feature`

Upstream: `ideation/intent-capture/intent-statement.md`, `ideation/scope-definition/scope-document.md`, `codekb/repo/architecture.md`.

---

## Q1. How is the export period defined?

Background: the web report uses a calendar-month picker resolved in the browser's timezone; invoices are stored with UTC timestamps.

- A. Calendar month in the browser's timezone, matching the report exactly.
- B. Calendar month in UTC, with the effective `[from, to]` echoed in the file header.
- C. Arbitrary `[from, to]` range chosen by the user; month is only a preset.
- X. Other (please specify)

[Answer]: B

## Q2. Where does the export run?

- A. Synchronously in the request for CSV; PDF rendered by the existing worker with a download link on completion.
- B. Both formats rendered by the worker; the UI polls for completion.
- C. Both formats synchronously in the request.
- X. Other (please specify)

[Answer]: A

## Q3. How are exported files retained?

- A. Not retained; every download recomputes the export.
- B. Retained 30 days in the existing object store bucket.
- C. Retained 7 years to satisfy the audit requirement raised in feasibility.
- X. Other (please specify)

[Answer]: C

---

## Consolidated Summary Confirmation

- Q1: UTC calendar month; the effective range is echoed in the file header.
- Q2: CSV synchronous, PDF via the existing worker with a completion link.
- Q3: exports retained 7 years in the existing object store.

Does this all look correct before I generate the requirements artifact?

- Looks correct
- Request changes

[Answer]: Looks correct


## Post-approval Amendment

## Q4. Should the export job run on the existing nightly scheduler or on demand only?

- A. On demand only (user-triggered).
- B. Nightly for the previous month plus on demand.
- C. Nightly only; on-demand downloads serve the stored file.
- X. Other (please specify)

[Answer]:
