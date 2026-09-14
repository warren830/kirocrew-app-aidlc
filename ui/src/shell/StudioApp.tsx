/**
 * The frame every area mounts into.
 *
 * It owns four things and nothing else:
 *
 *  1. **Location.** One `useStudioRoute()` for the page, passed down. Areas navigate with `go`.
 *  2. **The shared reads.** `/actions`, `/repos`, `/leases`, `/settings` and `/health` are fetched
 *     once here and published through `ShellDataContext`. `GET /actions` snapshots every intent on the
 *     server, so the badge, the strip and the queue polling it separately would triple that cost —
 *     which is why an area is expected to read it from the context rather than fetch it again.
 *  3. **The live transport.** One `EventSource` for the page (`sse.ts`), so every resource revalidates
 *     from the same cursor.
 *  4. **Failure that is still a page.** A render error inside a view becomes an in-place message with a
 *     retry (`ErrorBoundary`), a lost session becomes one assertive banner, and a degraded backend
 *     becomes one polite banner. None of them blanks the frame.
 *  5. **`?draft=<id>`.** The Advisor drawer is route state (§3.2) rather than a per-view control,
 *     because the link can arrive from anywhere — Activity, a shared URL — and the drawer is a reader
 *     with no decision control of its own. Mounted here it works on every view instead of only on the
 *     one that happened to create the draft; mounted in the view it would be dead on the other five.
 *
 * Deliberately absent: a theme control (the host owns `html[data-theme]`, visual spec discrepancy #5)
 * and any `aria-live` on the view container. The mockup puts `aria-live="polite"` on `main`, which
 * would re-announce the entire decision pane on every 2-second poll; the live regions here are the
 * status strip (polite) and the session/recovery banners (assertive), per PRD §10.3 "sparingly".
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { useNavBadge } from '@kirocrew/app-sdk'

import { AdvisorDrawer } from '../advisor/AdvisorDrawer'
import { useI18n } from '../i18n'
import { AUTH_REQUIRED_EVENT, useStudioApi, type StudioApi } from '../lib/api'
import { forActionQueue } from '../lib/actionQueue'
import { plural } from '../lib/format'
import { useHostMode } from '../lib/host'
import { paneFor, useStudioRoute, type Navigate, type StudioRoute } from '../lib/route'
import { EventStreamProvider, useStreamStatus } from '../lib/sse'
import { useResource, type Resource } from '../lib/useResource'
import type { ActionsResponse, HealthResponse, LeasesResponse, ReposResponse, SettingsResponse } from '../lib/types'
import { ErrorBoundary } from './ErrorBoundary'
import { Icon } from './Icon'
import { ScopeBar } from './ScopeBar'
import { StatusStrip } from './StatusStrip'
import { TopBar } from './TopBar'
import { ViewRouter, viewLabelKey } from './ViewRouter'

/**
 * What the shell already knows, for the areas mounted inside it.
 *
 * Every member is a `Resource`, so an area gets `data`, `error`, `stale` and `refresh` rather than a
 * snapshot it cannot re-read. `setQueueCount` lets the Action Center replace the badge's waiting count
 * with the count actually visible after search filtering (visual spec §1.2).
 */
export interface ShellData {
  api: StudioApi
  route: StudioRoute
  go: Navigate
  actions: Resource<ActionsResponse>
  repos: Resource<ReposResponse>
  leases: Resource<LeasesResponse>
  settings: Resource<SettingsResponse>
  health: Resource<HealthResponse>
  setQueueCount: (count: number | null) => void
}

const ShellDataContext = createContext<ShellData | null>(null)

/** Inside the shell this always has a value; outside it throws rather than serving empty data. */
export function useShellData(): ShellData {
  const value = useContext(ShellDataContext)
  if (!value) throw new Error('useShellData() must be called inside StudioApp')
  return value
}

export function StudioApp() {
  const api = useStudioApi()
  // The stream lives above the resources so `useResource` can subscribe to it, and `pollEvents` is
  // handed in rather than imported so the fallback goes through the SDK's allowlisted fetch.
  const poll = useCallback((cursor: number | null) => api.pollEvents(cursor), [api])
  return (
    <EventStreamProvider poll={poll}>
      <Shell api={api} />
    </EventStreamProvider>
  )
}

function Shell({ api }: { api: StudioApi }) {
  const i18n = useI18n()
  const { t } = i18n
  const mode = useHostMode()
  const [route, go] = useStudioRoute()
  const stream = useStreamStatus()
  const setNavBadge = useNavBadge()
  const [queueCount, setQueueCount] = useState<number | null>(null)
  const [authLost, setAuthLost] = useState(false)

  const actions = useResource<ActionsResponse>(
    `actions:${route.repo}:${route.intent}`,
    useCallback(
      (signal) =>
        api.actions({ repo: route.repo || undefined, intent: route.intent || undefined }, { signal }),
      [api, route.repo, route.intent],
    ),
  )
  const repos = useResource<ReposResponse>(
    'repos',
    useCallback((signal) => api.repos(false, { signal }), [api]),
    // The registry does not change on its own; the events that do change it force a refetch anyway.
    { interval: 60_000 },
  )
  const leases = useResource<LeasesResponse>(
    'leases',
    useCallback((signal) => api.leases({ signal }), [api]),
  )
  const settings = useResource<SettingsResponse>(
    'settings',
    useCallback((signal) => api.settings({ signal }), [api]),
    { interval: 0 },
  )
  const health = useResource<HealthResponse>(
    'health',
    useCallback((signal) => api.health({ signal }), [api]),
    { interval: 60_000 },
  )

  // A lost cookie makes every resource fail with the same code; the banner is shown once and no toast
  // is raised per resource (§3.6). The host also announces it, for the case where nothing was polling.
  useEffect(() => {
    const onAuth = () => setAuthLost(true)
    window.addEventListener(AUTH_REQUIRED_EVENT, onAuth)
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, onAuth)
  }, [])
  const sessionLost =
    authLost ||
    [actions.error, repos.error, leases.error, settings.error, health.error].some((e) => e?.authRequired)

  // Keep `actions` intact: in-flight records still drive fast polling, map links and delivery details.
  const queue = useMemo(() => actions.data ? forActionQueue(actions.data) : null, [actions.data])
  const waitingTotal = queue?.counts.total ?? null
  const badge = queueCount ?? waitingTotal
  useEffect(() => {
    // The host rail's badge for this app. `useNavBadge` is one of the few SDK hooks that really works.
    setNavBadge(badge ?? 0)
  }, [badge, setNavBadge])

  const circuits = useMemo(
    () => actions.data?.actions.filter((card) => card.failure?.breaker_open).length ?? null,
    [actions.data],
  )
  const night = settings.data
    ? {
        enabled: settings.data.settings.night_window.enabled,
        start: settings.data.settings.night_window.start_local,
        end: settings.data.settings.night_window.end_local,
      }
    : null

  const shellData = useMemo<ShellData>(
    () => ({ api, route, go, actions, repos, leases, settings, health, setQueueCount }),
    [api, route, go, actions, repos, leases, settings, health],
  )

  const pane = paneFor(route)
  const viewLabel = t(viewLabelKey(route.view))
  const issues = health.data && health.data.status !== 'healthy' ? health.data.issues.length : 0

  return (
    <div className="studio" data-mode={mode} data-pane={pane} data-view={route.view}>
      <a className="studio-skip" href="#studio-view">
        {t('a11y.skipToDetail')}
      </a>

      <TopBar
        route={route}
        go={go}
        queueCount={badge}
        // Rendered whenever a selection exists, hidden above 900px by CSS. Deciding it in JS from a
        // 767px media query would leave a 768–900px window where the stylesheet has already hidden the
        // queue and the only way back does not exist.
        showBack={pane === 'detail' && route.view === 'actions'}
        onBack={() => go({ action: '', artifact: '' })}
      />

      <ScopeBar
        route={route}
        go={go}
        repos={repos.data?.repos ?? []}
        registered={repos.data?.totals.repos ?? null}
        unavailable={repos.data?.totals.unavailable ?? null}
      >
        <StatusStrip
          running={leases.data?.live_execution ?? null}
          leases={leases.data?.leases.length ?? null}
          circuits={circuits}
          critical={queue?.counts.critical ?? null}
          nightWindow={night}
          streamMode={stream.mode}
        />
      </ScopeBar>

      {sessionLost ? (
        // Assertive: every control on the page will now refuse, and the user must know before they try
        // to approve something (PRD §10.3 reserves assertive for exactly this class of message).
        <div className="studio-banner" data-tone="danger" role="alert">
          <Icon name="warn" size={15} />
          <span className="studio-grow">{t('shell.banner.sessionExpired')}</span>
          <button type="button" className="studio-btn" onClick={() => window.location.reload()}>
            {t('shell.banner.reload')}
          </button>
        </div>
      ) : null}

      {issues > 0 ? (
        <div className="studio-banner" data-tone="warn" role="status">
          <Icon name="warn" size={15} />
          <span className="studio-grow">{plural(i18n, 'shell.banner.degraded', issues)}</span>
          <button type="button" className="studio-btn" onClick={() => go({ view: 'settings' })}>
            {t('shell.banner.openSettings')}
          </button>
        </div>
      ) : null}

      <main className="studio-view" id="studio-view">
        <ShellDataContext.Provider value={shellData}>
          {/* Keyed on the view so navigating away from a broken one clears its error. */}
          <ErrorBoundary where={viewLabel} resetKey={route.view}>
            <ViewRouter route={route} go={go} />
          </ErrorBoundary>

          {route.draft ? (
            // An overlay rather than a column inside the view: the views own the flex row in
            // `.studio-view`, so a sibling there would squeeze the queue at 390px. Non-modal on
            // purpose — the drawer offers no decision control, so nothing is lost by leaving the page
            // behind it reachable, and claiming `aria-modal` without a focus trap would be a lie.
            <div className="studio-drawer-wrap">
              {/* Mouse dismissal only; the keyboard paths are Escape and the drawer's Close button. */}
              <div className="studio-drawer-scrim" onClick={() => go({ draft: '' })} aria-hidden="true" />
              {/* Its own boundary: an Advisor draft is subagent output, and a shape this bundle does not
                  expect must not take the decision the user came here to make down with it. */}
              <ErrorBoundary where={t('advisor.drawer.title')} resetKey={route.draft}>
                <AdvisorDrawer draftId={route.draft} onClose={() => go({ draft: '' })} />
              </ErrorBoundary>
            </div>
          ) : null}
        </ShellDataContext.Provider>
      </main>
    </div>
  )
}
