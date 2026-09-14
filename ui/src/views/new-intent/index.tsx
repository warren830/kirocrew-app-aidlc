/**
 * The `new-intent` route's entry point.
 *
 * `ViewRouter` discovers views by globbing `views/<route>/index.tsx` and rendering the default export, so
 * this one re-export is what makes the four-step wizard reachable at `?view=new-intent` — the route the
 * top bar's New intent button navigates to. The implementation lives in `ui/src/wizard/`.
 */
export { WizardView as default } from '../../wizard/WizardView'
