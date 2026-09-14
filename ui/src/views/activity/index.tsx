/**
 * The `activity` route's entry point.
 *
 * `ViewRouter` discovers views by globbing `views/<route>/index.tsx` and rendering the default export, so
 * this one re-export is what makes the timeline, the Evidence drawer and the diagnostic export reachable
 * at `?view=activity`. The implementation lives in `ui/src/activity/` because the area is five files, not
 * one.
 */
export { ActivityView as default } from '../../activity/ActivityView'
