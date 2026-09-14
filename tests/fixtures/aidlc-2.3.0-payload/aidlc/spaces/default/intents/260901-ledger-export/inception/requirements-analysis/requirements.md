# Requirements — Monthly Ledger Export

## Functional requirements

| ID | Requirement | Source |
|---|---|---|
| FR-1 | The user can export the ledger for a UTC calendar month as CSV. | Q1, Q2 |
| FR-2 | The user can request the same period as PDF; the PDF is rendered by the existing worker and a download link appears on completion. | Q2 |
| FR-3 | CSV and PDF totals equal the on-screen report totals for the same period to the cent. | intent-statement |
| FR-4 | Exported files are retained for 7 years in the existing object store bucket and can be downloaded again without recomputation. | Q3, gate feedback (revision 1) |
| FR-5 | The PDF export is available only to principals holding the existing `billing.export` permission. | gate feedback (revision 1) |

## Non-functional requirements

- NFR-1 CSV for a month with up to 50 000 lines completes within 5 s at p95.
- NFR-2 No change to any existing endpoint contract or test.

## Out of scope

- Custom date ranges (kept as a possible follow-up; see Q4 amendment).
- New external dependencies for the CSV path.
