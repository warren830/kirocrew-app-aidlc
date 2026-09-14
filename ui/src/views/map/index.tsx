/**
 * The `map` route's entry point.
 *
 * `ViewRouter` discovers views by globbing `views/<route>/index.tsx` and rendering the default export,
 * so this one re-export is what makes the Workflow Map reachable. The implementation lives in
 * `ui/src/map/` because it is eight files, not one.
 */
export { MapView as default } from '../../map/MapView'
