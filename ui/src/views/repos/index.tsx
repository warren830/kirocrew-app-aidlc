/**
 * The `repos` route's entry point.
 *
 * `ViewRouter` discovers views by globbing `views/<route>/index.tsx` and rendering the default export, so
 * this one re-export is what makes the Repos page — registration, preflight, and the install, upgrade and
 * recovery transactions — reachable at `?view=repos`. The implementation lives in `ui/src/repos/` because
 * the area is ten files, not one.
 */
export { ReposView as default } from '../../repos/ReposView'
