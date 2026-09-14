/**
 * The `settings` route's entry point.
 *
 * `ViewRouter` discovers views by globbing `views/<route>/index.tsx` and rendering the default export, so
 * this one re-export is what makes the preference matrix, the About panel and the one-time prototype
 * migration reachable at `?view=settings`. The implementation lives in `ui/src/settings/` because the area
 * is seven files, not one.
 */
export { SettingsView as default } from '../../settings/SettingsView'
