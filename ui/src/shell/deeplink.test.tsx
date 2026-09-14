/**
 * A deep link is state, and a reload must not lose it.
 *
 * Slack, the notification adapter and every "copy link" control hand out one URL under
 * `/apps/aidlc-studio` with the whole selection in the query string (§3.2, PRD §10.2 / FR-SLK-008).
 * Mounting the app fresh at such a URL is exactly what a browser reload does, so these tests set
 * `location`, mount, and assert the state that arrived is on screen — not that `parseRoute` returned the
 * right object (`route.test.ts` already pins that), but that the shell, the queue, the detail pane, the
 * tablist and the breadcrumb all read the same route.
 *
 * The failure this guards against is silent: every one of these params is read by a different component,
 * so a view that keeps its own `useState` copy of the tab or the stage still renders — it just renders
 * the default, and the user who followed a link to one finding lands on someone else's decision.
 */

import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { I18nProvider } from '../i18n'
import { EMPTY_ROUTE, buildRoute, parseRoute } from '../lib/route'
import type { MapModel, MapPhase, MapStage } from '../lib/types'
import { API_BASE, actionCard, reviewFinding, shellRoutes } from '../test/fixtures'
import { setApiRoutes } from '../test/stubs/app-sdk'
import { StudioApp } from './StudioApp'

/** A card whose evidence carries a review, so `?tab=review` has a tab to select (`tabsFor`). */
const reviewed = actionCard({
  action_id: 'a_7',
  evidence: {
    ...actionCard().evidence,
    review: {
      verdict: 'changes_requested',
      findings: [reviewFinding({ title: 'Decline path is unspecified' })],
      reviewer: 'architecture-reviewer',
      review_class: 'adversarial',
      iteration: 2,
    },
  },
})

function mapStage(over: Partial<MapStage> & Pick<MapStage, 'slug' | 'number' | 'phase'>): MapStage {
  return {
    name: over.slug.replace(/-/g, ' '),
    state: 'not_started',
    execution: 'ALWAYS',
    in_scope: true,
    mode: 'inline',
    agent: 'orchestrator',
    reviewer: null,
    review_class: null,
    gate: true,
    per_unit: false,
    summary_confirmation: null,
    consumes: [],
    produces: [],
    depends_on: [],
    dependents: [],
    elapsed_secs: null,
    artifacts: [],
    skipped_reason: null,
    units: [],
    is_current: false,
    is_directive: false,
    ...over,
  }
}

function mapModel(stages: MapStage[]): MapModel {
  const phase: MapPhase = {
    phase: 'inception',
    status: 'InProgress',
    stages,
    counts: { total: stages.length, in_scope: stages.length, done: 0, skipped: 0 },
  }
  return {
    intent_key: 'default~250901-guest',
    phases: [phase],
    counts: { stages_known: stages.length, stages_selected: stages.length, gates: stages.length },
    units: [],
    graph_version: '2.6.2',
    stage_count: stages.length,
  }
}

/** What a reload does: the app is constructed from `location` alone, with no in-memory state. */
function reloadAt(url: string) {
  window.history.replaceState(null, '', url)
  return render(
    <I18nProvider>
      <StudioApp />
    </I18nProvider>,
  )
}

afterEach(() => {
  window.history.replaceState(null, '', '/apps/aidlc-studio')
  localStorage.clear()
})

describe('a deep link', () => {
  it('restores the view, the selected action, the tab and the stage after a reload', async () => {
    setApiRoutes(shellRoutes([reviewed]))
    reloadAt('/apps/aidlc-studio?view=actions&repo=r_1&action=a_7&tab=review&stage=functional-design')

    // view=actions: the Action Center is current in the rail, not merely the default that looks like it.
    const nav = screen.getByRole('navigation')
    await waitFor(() =>
      expect(nav.querySelector('[aria-current="page"] .studio-nav-text')?.textContent).toBe('Action Center'),
    )

    // action=a_7: the detail pane is on that card, and the shell is in the detail pane on a phone.
    // The heading is the card's own headline (the type name only appears there when the catalogue has no
    // copy for the card), so the type is checked on the breadcrumb where it always shows.
    expect(
      await screen.findByRole('heading', { level: 1, name: /waiting for your approval/i }),
    ).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: /Where this decision sits/i })).toHaveTextContent('Approval gate')
    expect(document.querySelector('.studio')?.getAttribute('data-pane')).toBe('detail')

    // tab=review: selected in the tablist AND showing the reviewer's own words, so the panel and the
    // tab cannot disagree about which one is open.
    const review = await screen.findByRole('tab', { name: /Review/ })
    expect(review).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByText('Decline path is unspecified')).toBeInTheDocument()

    // stage=functional-design: the scope breadcrumb names it verbatim (an AI-DLC slug is never translated).
    expect(screen.getByText('functional-design')).toBeInTheDocument()

    // And the URL is untouched: a reload that rewrote it would break the second reload.
    expect(window.location.search).toBe('?view=actions&repo=r_1&action=a_7&tab=review&stage=functional-design')
  })

  it('lands on the Workflow Map with the linked stage already selected', async () => {
    const INTENT = 'default~250901-guest'
    setApiRoutes({
      ...shellRoutes([reviewed]),
      [`GET ${API_BASE}/repos/r_1/intents/${INTENT}/map`]: () => ({
        map: mapModel([
          mapStage({ slug: 'functional-design', number: '2.3', phase: 'inception', state: 'awaiting_approval' }),
          mapStage({ slug: 'user-stories', number: '2.4', phase: 'inception' }),
        ]),
      }),
      // The intent read and the review read are deliberately absent: a deep link must still land on the
      // stage when a secondary read fails, or a slow repo turns every shared link into an empty canvas.
    })
    reloadAt(`/apps/aidlc-studio?view=map&repo=r_1&intent=${INTENT}&stage=functional-design`)

    const nav = screen.getByRole('navigation')
    await waitFor(() =>
      expect(nav.querySelector('[aria-current="page"] .studio-nav-text')?.textContent).toBe('Workflow Map'),
    )
    // The selection comes from the route, so the linked stage is pressed with no click — the property a
    // per-component `useState` copy of the selection would silently lose.
    const stage = await screen.findByRole('button', { name: /functional-design/ })
    expect(stage).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /user-stories/ })).toHaveAttribute('aria-pressed', 'false')
  })

  it('round-trips the link the backend hands to Slack', () => {
    // `NotificationAdapter.deep_link` is `buildRoute({view:'actions', action})` byte-for-byte, and the
    // page must parse back exactly what it wrote or a link that survives a week stops resolving.
    const link = buildRoute({ view: 'actions', action: 'a_7', tab: 'review', stage: 'functional-design' })
    expect(link).toBe('/apps/aidlc-studio?view=actions&action=a_7&tab=review&stage=functional-design')
    const url = new URL(link, 'http://localhost')
    expect(parseRoute(url.search, '')).toEqual({
      ...EMPTY_ROUTE,
      view: 'actions',
      action: 'a_7',
      tab: 'review',
      stage: 'functional-design',
    })
  })
})
