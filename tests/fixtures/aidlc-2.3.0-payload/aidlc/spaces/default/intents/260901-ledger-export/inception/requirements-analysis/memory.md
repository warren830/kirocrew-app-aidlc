# Stage Memory — requirements-analysis

## Interpretations

- 2026-09-02T10:35:00Z — "Month" read as a UTC calendar month because invoices are stored in UTC; the report's browser-timezone month is a display concern.

## Deviations

- 2026-09-03T14:20:00Z — Retention raised from "deferred" to a hard 7-year requirement after the gate was rejected; the intent statement had deferred it.

## Tradeoffs

- 2026-09-02T10:36:00Z — PDF through the worker adds latency but avoids a second renderer in the request path.

## Open questions

- 2026-09-03T14:23:00Z — Scheduler vs on-demand (Q4 amendment) still unanswered.
