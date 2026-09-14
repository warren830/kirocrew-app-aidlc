/**
 * Sub-navigation, in the query string.
 *
 * The host's router owns `/apps/:name` and has no splat (`03 §4.1`): a path segment after the slug
 * matches nothing and redirects to `/chat`. So every piece of Studio's location lives in the query
 * string and the hash, which is also what makes a Slack deep link survive a refresh and restore the
 * scope, the selected action, the tab and the evidence anchor (PRD §10.2, FR-SLK-008).
 *
 * Two rules that are correctness, not tidiness:
 *
 *  - **Every value is validated.** Anything that fails the grammar is dropped rather than passed on.
 *    These values become path segments in API calls; a `repo` of `../../health` must never reach
 *    `api.repo()`. The grammar is deliberately the intersection of what the backend accepts for repo
 *    ids, intent keys (`space~dir`), stage slugs, unit names and artifact ids.
 *  - **A patch is merged, never replaced.** `go({tab: 'review'})` keeps the current selection, so a
 *    tab switch cannot silently deselect the action the user is deciding on.
 */

import { useCallback, useSyncExternalStore } from 'react'
import { useNavigate } from '@kirocrew/app-sdk'

/** The six nav destinations plus the wizard. `new-intent` is reached from the top bar, not the rail. */
export const VIEWS = ['actions', 'repos', 'intents', 'map', 'activity', 'settings', 'new-intent'] as const
export type View = (typeof VIEWS)[number]

export const TABS = ['decision', 'artifacts', 'review', 'activity', 'conversation'] as const
export type Tab = (typeof TABS)[number]

export const DEFAULT_VIEW: View = 'actions'
export const DEFAULT_TAB: Tab = 'decision'
export const DEFAULT_SPACE = 'default'

/** The app's own page URL. Never a deeper path: the host router would not match it. */
export const PAGE_PATH = '/apps/aidlc-studio'

/**
 * §3.2's grammar. Covers `r_…`, `a_…`, `space~250901-slug`, `functional-design`, `unit-1`, hashes and
 * `~`; excludes `/`, `.` runs, whitespace and everything else that could change a URL's meaning.
 */
const VALUE_RE = /^[A-Za-z0-9_.~-]{1,160}$/
/** Anchors the evidence pane may scroll to: `f-3` (finding), `h-<slug>` (heading), `crit-2`. */
const ANCHOR_RE = /^(?:f-\d{1,4}|crit-\d{1,4}|h-[a-z0-9-]{1,120})$/

export interface StudioRoute {
  view: View
  repo: string
  space: string
  intent: string
  action: string
  tab: Tab
  stage: string
  unit: string
  artifact: string
  tx: string
  draft: string
  anchor: string
}

/** Query params in the order `buildRoute` writes them (the §3.2 table order). */
const PARAM_ORDER = ['view', 'repo', 'space', 'intent', 'action', 'tab', 'stage', 'unit', 'artifact', 'tx', 'draft'] as const

export type RoutePatch = Partial<StudioRoute>
export type Navigate = (patch: RoutePatch, opts?: { replace?: boolean }) => void

function value(raw: string | null): string {
  if (!raw) return ''
  return VALUE_RE.test(raw) ? raw : ''
}

/** Pure. Unknown or malformed values fall back to their default; nothing throws. */
export function parseRoute(search: string, hash: string): StudioRoute {
  const params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search)
  const rawView = params.get('view')
  const action = value(params.get('action'))
  // A deep link that names an action but no view is an Action Center link (`NotificationAdapter`
  // builds exactly that), so it must not land on the default page with the selection ignored.
  const view: View = VIEWS.includes(rawView as View)
    ? (rawView as View)
    : action
      ? 'actions'
      : DEFAULT_VIEW
  const rawTab = params.get('tab')
  const tab: Tab = TABS.includes(rawTab as Tab) ? (rawTab as Tab) : DEFAULT_TAB
  const intent = value(params.get('intent'))
  const space = value(params.get('space')) || (intent ? DEFAULT_SPACE : '')
  const rawAnchor = (hash.startsWith('#') ? hash.slice(1) : hash).trim()

  return {
    view,
    repo: value(params.get('repo')),
    space,
    intent,
    action,
    tab,
    stage: value(params.get('stage')),
    unit: value(params.get('unit')),
    artifact: value(params.get('artifact')),
    tx: value(params.get('tx')),
    draft: value(params.get('draft')),
    anchor: ANCHOR_RE.test(rawAnchor) ? rawAnchor : '',
  }
}

/**
 * The URL for a route, or for a patch on top of `base`.
 *
 * Defaults and empties are omitted so a shared link is the shortest thing that reproduces the state,
 * and so `parseRoute(buildRoute(r))` is `r` for every valid `r` (the round-trip the tests pin).
 */
export function buildRoute(patch: RoutePatch, base?: StudioRoute): string {
  const merged: StudioRoute = { ...(base ?? EMPTY_ROUTE), ...patch }
  // Assembled by hand rather than with `URLSearchParams`: its form-urlencoded serialiser escapes `~`
  // as `%7E`, and `~` is the intent-key separator (`space~dir`), so every intent link would arrive
  // encoded. `encodeURIComponent` leaves every character `VALUE_RE` allows alone.
  const params: string[] = []
  for (const name of PARAM_ORDER) {
    const raw = merged[name]
    if (!raw) continue
    // `view=actions` is redundant next to an `action` (parseRoute derives it), but it is kept so
    // `buildRoute({view:'actions', action})` is byte-for-byte `ActionCard.deep_link` — the string
    // Slack and the notification adapter already sent out.
    if (name === 'view' && raw === DEFAULT_VIEW && !merged.action) continue
    if (name === 'tab' && raw === DEFAULT_TAB) continue
    // `space` is re-derived as `default` by `parseRoute` whenever an intent is named, so writing it is
    // noise; without an intent it is a filter and must survive.
    if (name === 'space' && raw === DEFAULT_SPACE && merged.intent) continue
    if (!VALUE_RE.test(raw)) continue
    params.push(`${name}=${encodeURIComponent(raw)}`)
  }
  const search = params.join('&')
  const anchor = ANCHOR_RE.test(merged.anchor) ? `#${merged.anchor}` : ''
  return `${PAGE_PATH}${search ? `?${search}` : ''}${anchor}`
}

export const EMPTY_ROUTE: StudioRoute = {
  view: DEFAULT_VIEW,
  repo: '',
  space: '',
  intent: '',
  action: '',
  tab: DEFAULT_TAB,
  stage: '',
  unit: '',
  artifact: '',
  tx: '',
  draft: '',
  anchor: '',
}

/** The mobile master/detail state, derived rather than stored: a selection *is* the detail pane. */
export function paneFor(route: StudioRoute): 'list' | 'detail' {
  return route.action ? 'detail' : 'list'
}

// --------------------------------------------------------------------------- //
// the store
// --------------------------------------------------------------------------- //

/**
 * One snapshot for every reader.
 *
 * `useSyncExternalStore` demands a referentially stable snapshot, and the route is recomputed from
 * `location`, so the parsed value is cached and only rebuilt when the URL text actually changed.
 * Without the cache every render would produce a new object and React would loop.
 */
const ROUTE_EVENT = 'aidlc-studio:route'

let cachedSearch: string | null = null
let cachedHash: string | null = null
let cachedRoute: StudioRoute = EMPTY_ROUTE

function snapshot(): StudioRoute {
  if (typeof window === 'undefined') return EMPTY_ROUTE
  const { search, hash } = window.location
  if (search !== cachedSearch || hash !== cachedHash) {
    cachedSearch = search
    cachedHash = hash
    cachedRoute = parseRoute(search, hash)
  }
  return cachedRoute
}

function subscribe(onChange: () => void): () => void {
  // `popstate` covers Back/Forward. The host router's `navigate()` does NOT fire it, so `go` below
  // dispatches `ROUTE_EVENT` itself — without that, one component navigating leaves the others reading
  // the previous URL until something else re-renders them.
  window.addEventListener('popstate', onChange)
  window.addEventListener(ROUTE_EVENT, onChange)
  return () => {
    window.removeEventListener('popstate', onChange)
    window.removeEventListener(ROUTE_EVENT, onChange)
  }
}

/** The current route and a merge-patch navigator. Every reader sees the same value. */
export function useStudioRoute(): [StudioRoute, Navigate] {
  const route = useSyncExternalStore(subscribe, snapshot, () => EMPTY_ROUTE)
  const navigate = useNavigate()

  const go = useCallback<Navigate>(
    (patch, opts) => {
      const url = buildRoute(patch, snapshot())
      if (opts?.replace) window.history.replaceState(null, '', url)
      else navigate(url)
      window.dispatchEvent(new CustomEvent(ROUTE_EVENT))
    },
    [navigate],
  )

  return [route, go]
}
