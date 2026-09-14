/**
 * Mounts the Action Center into the shell's view registry, and plugs the decision templates in.
 *
 * `ViewRouter` discovers views by globbing `src/views/*\/index.tsx` for a default-exported component, so
 * this file is what makes `?view=actions` render the real queue instead of the "not built yet"
 * placeholder. The implementation lives in `src/actions/` because the area is a dozen files, not one.
 *
 * The import ORDER below is load-bearing. `templates/TemplateRegistry` calls
 * `registerActionTemplate()` at module scope and imports that function from `actions/DetailShell`, so
 * importing it *from* `DetailShell` would run the registration while `DetailShell`'s own module body is
 * still initialising — the registry map is not created yet and the app dies with "Cannot access
 * 'templates' before initialization". Importing `ActionsView` first finishes `DetailShell`, and the
 * registration then lands in a map that exists. This composition root is outside the cycle, which is why
 * the side-effect import belongs here.
 */
import { ActionsView } from '../../actions/ActionsView'
import '../../templates/TemplateRegistry'

export default ActionsView
