/**
 * Which view is on screen — and how an area plugs one in without editing this file.
 *
 * Two ways in, because the areas are built in parallel and neither mechanism alone covers both cases:
 *
 *  1. **Convention.** A file at `ui/src/views/<view>/index.tsx` whose default export is a
 *     `ViewComponent` is picked up automatically. `<view>` is the route slug (`actions`, `repos`,
 *     `intents`, `map`, `activity`, `settings`, `new-intent`). This is a build-time glob, so nothing
 *     imports anything by hand and a view that does not exist yet costs nothing.
 *  2. **Registration.** `registerView('map', MapPage)` from anywhere, for a view that lives elsewhere
 *     or is composed at runtime. Registration wins over the glob, so an area can override.
 *
 * Until a view exists the placeholder says so, in words, and names the view. A blank pane in a tool
 * whose job is showing you what is waiting would read as "nothing is waiting".
 */

import { useMemo, type ComponentType } from 'react'

import { useI18n } from '../i18n'
import { VIEWS, type Navigate, type StudioRoute, type View } from '../lib/route'
import { Icon } from './Icon'

export interface ViewProps {
  route: StudioRoute
  go: Navigate
}

export type ViewComponent = ComponentType<ViewProps>

// `import.meta.glob` is Vite's, and `vite/client` is not in this project's `types`, so the one member
// used is declared here rather than pulling a whole ambient package into the build.
declare global {
  interface ImportMeta {
    glob(pattern: string, options: { eager: true }): Record<string, unknown>
  }
}

const explicit = new Map<View, ViewComponent>()

/** Register (or replace) the component for a view. Call at module scope from the area's entry file. */
export function registerView(view: View, component: ViewComponent): void {
  explicit.set(view, component)
}

/** Every view with an implementation right now — the glob plus anything registered. */
export function viewRegistry(): Map<View, ViewComponent> {
  const found = new Map<View, ViewComponent>()
  const modules = import.meta.glob('../views/*/index.tsx', { eager: true })
  for (const [path, module] of Object.entries(modules)) {
    const slug = /\/views\/([^/]+)\/index\.tsx$/.exec(path)?.[1]
    if (!slug || !(VIEWS as readonly string[]).includes(slug)) continue
    const exported = (module as { default?: unknown }).default
    if (typeof exported === 'function') found.set(slug as View, exported as ViewComponent)
  }
  for (const [view, component] of explicit) found.set(view, component)
  return found
}

/** The label for a view, from the nav catalogue so the placeholder and the rail agree. */
export function viewLabelKey(view: View): string {
  return view === 'new-intent' ? 'nav.newIntent' : `nav.${view}`
}

export function ViewRouter({ route, go }: ViewProps) {
  const { t } = useI18n()
  // Recomputed per route change rather than at module load: `registerView` may run after this module
  // is evaluated (an area registering from its own entry), and a cached empty map would hide it.
  const registry = useMemo(() => viewRegistry(), [route.view])
  const Component = registry.get(route.view)

  if (Component) return <Component route={route} go={go} />

  const label = t(viewLabelKey(route.view))
  return (
    <div className="studio-page studio-placeholder">
      <h1>
        <Icon name="clock" size={18} /> {t('shell.notBuilt.title', { view: label })}
      </h1>
      <p className="studio-muted">{t('shell.notBuilt.body')}</p>
    </div>
  )
}
