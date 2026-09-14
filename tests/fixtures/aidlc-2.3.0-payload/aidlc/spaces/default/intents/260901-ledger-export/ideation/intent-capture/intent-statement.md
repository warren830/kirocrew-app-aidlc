# Intent Statement

## Problem

Finance closes each month from a spreadsheet, re-keying totals from the billing web report. The re-keying takes about two hours per close and has produced three reconciliation errors this year.

## Intended outcome

A monthly ledger export, available as CSV and PDF, whose totals agree with the on-screen report to the cent for the same period.

## Boundaries

- Existing reports, endpoints and tests are unchanged.
- No new external dependencies for the CSV path; the PDF renderer may be optional.
- Retention and re-download are deferred to requirements-analysis.

## Success signals

- Month-end close no longer needs the spreadsheet.
- Zero reconciliation differences between export and report for two consecutive months.
