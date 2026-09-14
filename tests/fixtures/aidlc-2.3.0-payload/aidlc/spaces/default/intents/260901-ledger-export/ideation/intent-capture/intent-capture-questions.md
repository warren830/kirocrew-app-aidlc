# Intent Capture — Clarifying Questions

## Sources

- [desc] Initial description: "Add a monthly ledger export (CSV and PDF) to the billing service without changing the existing reports"
- [scope] Workflow-selected scope: `feature`.

## Q1. Which business problem does the ledger export solve first?

A. Finance closes the month from a spreadsheet and re-keys totals from the web report.
B. Auditors need an immutable file per month.
C. Customers ask for a downloadable statement.
D. Not yet defined.
X. Other (please specify)

[Answer]: A

## Q2. Who is the primary user, and what do they need most?

A. Individual account owners downloading their own statement.
B. The finance team exporting all accounts for a period.
C. External auditors with read-only access.
D. Not yet identified.
X. Other (please specify)

[Answer]: B

## Q3. Which conditions must all hold for this to count as done? (multi-select)

A. CSV and PDF exports agree with the on-screen report to the cent for the same period.
B. Existing reports, endpoints and tests are unchanged.
C. Exports are retained for a fixed period and are downloadable again without recomputation.
D. Not yet defined.
X. Other (please specify)

[Answer]: A, B

## Consolidated Summary Confirmation

- The export exists so finance can close the month without re-keying totals.
- Primary user is the finance team exporting all accounts for a period; account owners are secondary.
- Done means CSV/PDF match the on-screen report to the cent and nothing existing changes.
- Retention is out of scope for this intent unless requirements-analysis raises it.

Does this all look correct before I generate the artifact?

- Looks correct
- Request changes

[Answer]: Looks correct
