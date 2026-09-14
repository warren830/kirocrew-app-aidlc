/**
 * The shell renders, routes and degrades.
 *
 * These are the things every area depends on and none of them can test for itself: the six nav items in
 * PRD order, a real implementation behind every route (the integration gate — a view left unwired shows
 * a placeholder that reads as "nothing is waiting"), the collapsible strip, and an error boundary that
 * keeps the frame when a view throws.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { setApiRoutes, type RouteHandler } from '../test/stubs/app-sdk'
import { settingsResponse } from '../test/fixtures'
import { I18nProvider, makeI18n } from '../i18n'
import { EMPTY_ROUTE, VIEWS, type View } from '../lib/route'
import { registerView, ViewRouter, viewLabelKey, viewRegistry } from './ViewRouter'
import { StudioApp } from './StudioApp'

const BASE = '/api/apps/aidlc-studio'

function routeTable(over: Record<string, unknown> = {}): Record<string, RouteHandler> {
  return {
    [`GET ${BASE}/actions*`]: () => ({
      actions: [],
      organize: 'priority',
      groups: [],
      counts: { total: 2, critical: 1, blocking: 1, attention: 0, info: 0 },
      generated_at: '2026-09-04T10:00:00Z',
      ...(over['actions'] as object ?? {}),
    }),
    [`GET ${BASE}/repos*`]: () => ({
      repos: [
        {
          repo_id: 'r_1',
          label: 'checkout-web',
          canonical_path: '/work/checkout-web',
          availability: 'available',
        },
      ],
      totals: { repos: 1, unavailable: 0, open_actions: 2 },
    }),
    [`GET ${BASE}/leases`]: () => ({ leases: [], global_concurrency_cap: 2, live_execution: 1 }),
    [`GET ${BASE}/settings`]: settingsResponse,
    [`GET ${BASE}/health`]: () => ({ status: 'healthy', issues: [] }),
    [`GET ${BASE}/events/poll*`]: () => ({ events: [], cursor: 0, oldest_seq: 0, reset: false }),
  }
}

function installRoutes(over: Record<string, unknown> = {}) {
  setApiRoutes(routeTable(over))
}

const mount = () =>
  render(
    <I18nProvider>
      <StudioApp />
    </I18nProvider>,
  )

afterEach(() => {
  window.history.replaceState(null, '', '/apps/aidlc-studio')
})

describe('the app shell', () => {
  it('renders the six primary destinations in PRD order, with the queue count on Action Center', async () => {
    installRoutes()
    mount()
    const nav = screen.getByRole('navigation')
    expect([...nav.querySelectorAll('.studio-nav-text')].map((el) => el.textContent)).toEqual([
      'Action Center',
      'Repos',
      'Intents',
      'Workflow Map',
      'Activity',
      'Settings',
    ])
    // The badge is the server's own total until the queue reports a filtered count, and it is only on
    // Action Center — the other five never carry one (visual spec §1.2).
    await waitFor(() => expect(nav.querySelector('.studio-count')?.textContent).toBe('2'))
    expect(nav.querySelectorAll('.studio-count')).toHaveLength(1)
  })

  it('has a real implementation behind every route in the contract', () => {
    // The integration gate. `viewRegistry()` is the build-time glob over `src/views/*/index.tsx`, so a
    // view whose entry file is missing (or whose default export is not a component) fails here rather
    // than shipping a "not built yet" pane into a product whose job is showing you what is waiting.
    const registry = viewRegistry()
    expect([...VIEWS].filter((view) => !registry.has(view))).toEqual([])
    expect(registry.size).toBe(VIEWS.length)
  })

  it('names the view instead of showing a blank pane when a route has no implementation', () => {
    // Reached only by a bundle that lost a view entry — every real slug is covered by the test above —
    // so it is exercised directly rather than through a route the app cannot produce.
    const t = makeI18n('en-US').t
    render(
      <I18nProvider>
        <ViewRouter route={{ ...EMPTY_ROUTE, view: 'nowhere' as View }} go={() => {}} />
      </I18nProvider>,
    )
    expect(screen.getByRole('heading')).toHaveTextContent(
      t('shell.notBuilt.title', { view: t(viewLabelKey('nowhere' as View)) }),
    )
  })

  it('renders a registered view and navigates between views', async () => {
    installRoutes()
    registerView('repos', () => <p>repo inventory</p>)
    mount()
    await userEvent.click(screen.getByRole('button', { name: /Repos/ }))
    expect(await screen.findByText('repo inventory')).toBeInTheDocument()
    expect(window.location.search).toContain('view=repos')
  })

  it('keeps the frame and offers a retry when a view throws', async () => {
    installRoutes()
    registerView('activity', () => {
      throw new Error('boom from the activity view')
    })
    mount()
    await userEvent.click(screen.getByRole('button', { name: /Activity/ }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Activity could not be rendered')
    expect(alert).toHaveTextContent('boom from the activity view')
    // The shell itself is still there, so the user can navigate out of the broken view.
    expect(screen.getByRole('navigation')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('collapses the execution strip and still reports the active alerts', async () => {
    installRoutes()
    mount()
    await screen.findByText('1 turn running')
    await userEvent.click(screen.getByRole('button', { name: 'Hide execution status' }))
    expect(screen.queryByText('1 turn running')).not.toBeInTheDocument()
    // One critical card in the fixture: the collapsed strip must still say so, in the chip and in the
    // polite live region (hence `getAllByText` — both carry the sentence).
    const strip = screen.getByRole('button', { name: 'Show execution status' }).parentElement
    expect(strip).toHaveTextContent('1 alert')
    expect(screen.getAllByText('1 alert').length).toBeGreaterThan(0)
  })

  it('shows the repo path in the scope bar and drops the selection when scope changes', async () => {
    installRoutes()
    mount()
    const select = await screen.findByRole('combobox')
    window.history.replaceState(null, '', '/apps/aidlc-studio?view=actions&action=a_1')
    await userEvent.selectOptions(select, 'r_1')
    expect(await screen.findByText('/work/checkout-web')).toBeInTheDocument()
    expect(window.location.search).not.toContain('action=')
  })

  it('opens the Advisor drawer from ?draft= on any view, and closing it clears the param', async () => {
    // The drawer belongs to the shell rather than to a view because the link can arrive from anywhere.
    // Mounted per-view it would be dead on the other five, which is what this pins.
    setApiRoutes({
      ...routeTable(),
      [`GET ${BASE}/advisor/drafts/d_1`]: () => ({
        draft: {
          draft_id: 'd_1', action_id: 'a_1', kind: 'gate_analysis', status: 'ready',
          request: { action_id: 'a_1', kind: 'gate_analysis', question_index: null, locale: 'en-US' },
          result: {
            // One of `ADVISOR_VERDICTS` — the parser accepts nothing else, so the fixture must not
            // pretend the wire carries a sentence.
            verdict: 'approve_recommended', summary: 'The design covers the decline path.',
            suggested_answers: [], evidence: [], assumptions: [], alternatives: [],
            confidence: 'medium', needs_your_decision: [], drafted_feedback: null,
          },
          error: null, created_at: '2026-09-04T10:00:00Z', updated_at: '2026-09-04T10:01:00Z',
          expires_at: '2026-09-04T11:00:00Z', neutrality: { ok: true },
        },
      }),
    })
    window.history.replaceState(null, '', '/apps/aidlc-studio?view=settings&draft=d_1')
    mount()
    const drawer = await screen.findByRole('complementary', { name: /AI Advisor/i })
    // Localized, not the raw token: the drawer is a full reader of the same draft the card shows, and
    // `approve_recommended` on screen is a machine word leaking into a sentence a person reads.
    expect(drawer).toHaveTextContent('Approving is consistent with the evidence')
    expect(drawer).not.toHaveTextContent('approve_recommended')
    // It is a reader: nothing in it can send a decision.
    expect(within(drawer).queryByRole('button', { name: /Approve gate/i })).toBeNull()
    expect(await screen.findByText('Queue organization')).toBeInTheDocument()
    expect(screen.queryByText("Cannot read properties of undefined (reading 'run_doctor_after_install')")).toBeNull()

    await userEvent.click(within(drawer).getByRole('button', { name: /Close/i }))
    expect(window.location.search).not.toContain('draft=')
  })
})
