# Build and Test Results

## Build

- `npm ci` — ok (42 s)
- `npm run build` — ok, 0 type errors

## Unit tests

| Suite | Tests | Passed | Failed |
|---|---|---|---|
| health.route.test.ts | 6 | 6 | 0 |
| existing suites | 118 | 118 | 0 |

## Notes

- The probe answers in 11 ms with both dependencies healthy and in 812 ms when the queue is unreachable (timeout budget 800 ms plus overhead).
- No production behaviour changed; all pre-existing tests still pass.
