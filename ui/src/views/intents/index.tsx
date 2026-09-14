/**
 * The `intents` route's entry point.
 *
 * `ViewRouter` discovers views by globbing `views/<route>/index.tsx` and rendering the default export, so
 * this one re-export is what makes the intent inventory — its filters, its per-intent commands and the
 * in-place recompose — reachable at `?view=intents`. The implementation lives in `ui/src/intents/` (with
 * the plan composer in `ui/src/plan/`) because the area is more than one file.
 */
export { IntentsView as default } from '../../intents/IntentsView'
