/**
 * The Action Center as a user meets it: the queue is the product's home, so these tests are about what
 * is on screen and reachable rather than about component internals.
 *
 * Mounted through the real shell (`StudioApp` → `ViewRouter` → `src/views/actions/index.tsx`), because
 * three of the properties below only exist in that composition: the nav badge counts what is visible,
 * the queue and the detail pane share one `/actions` read, and the detail pane is reached by selecting
 * a row.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { apiCalls, setApiRoutes, type RouteHandler } from '../test/stubs/app-sdk'
import { I18nProvider } from '../i18n'
import { STUDIO_ORGANIZE_KEY } from '../lib/host'
import { StudioApp } from '../shell/StudioApp'
import type { ActionCard, ActionType, Severity } from '../lib/types'

const BASE = '/api/apps/aidlc-studio'

interface Spec {
  id: string
  type?: ActionType
  severity?: Severity
  repo?: string
  repoId?: string
  space?: string
  slug?: string
  stage?: string
  number?: string
  waiting?: string
  stable?: boolean
  decisions?: ActionType[]
  status?: ActionCard['status']
}

function card(spec: Spec): ActionCard {
  const type = spec.type ?? 'gate'
  const repo = spec.repo ?? 'checkout-web'
  return {
    action_id: spec.id,
    type,
    queue_type: type,
    status: spec.status ?? 'Queued',
    status_generation: 1,
    source: 'aidlc',
    risk_class: 'human_lane',
    severity: spec.severity ?? 'blocking',
    priority_group: 2,
    repo: { repo_id: spec.repoId ?? 'r_1', label: repo, canonical_path: `/work/${repo}` },
    space: spec.space ?? 'default',
    intent: {
      intent_dir: '250901-guest', intent_key: 'default~250901-guest',
      slug: spec.slug ?? 'guest-checkout', uuid: null, title: null,
    },
    stage: { slug: spec.stage ?? 'functional-design', number: spec.number ?? '2.3', name: null, phase: 'inception', unit: null },
    headline: { key: `action.${type}.headline`, params: {} },
    consequence: null,
    waiting_since: spec.waiting ?? '2026-09-04T09:00:00Z',
    created_at: '2026-09-04T09:00:00Z',
    updated_at: '2026-09-04T09:00:00Z',
    primary: { decision: 'approve', label_key: 'decision.approve.label' },
    decisions: [
      { decision: 'approve', label_key: 'decision.approve.label', lane: 'human_lane', wire_text_template: 'Approve', requires: [] },
      { decision: 'request_changes', label_key: 'decision.request_changes.label', lane: 'human_lane', wire_text_template: 'Request Changes: <feedback>', requires: ['feedback'] },
    ],
    captured: {
      state_hash: 'sha-state', boundary_token: 'tok', question_digest: null, stage_attempt: 1,
      evidence_digest: 'sha-ev', is_active: true, captured_at: '2026-09-04T09:00:00Z',
      stable: spec.stable ?? true,
    },
    evidence: {
      state: {
        relpath: 'aidlc-state.md', sha256: 'sha-state', size: 100, mtime_ns: 1, current_stage: 'functional-design',
        status: 'awaiting_approval', lifecycle_phase: 'inception', revision_count: 0, stable: spec.stable ?? true,
      },
      audit: { shards: [], last_event: null, boundary_event: null, complete: true },
      directive: null,
      artifacts: [],
      review: null,
      acceptance_criteria: [],
      findings: [],
      session: {
        slot_key: 'aidlc-r1-guest', session_key: 'ses_1c4', running: false, stop_state: 'idle',
        last_turn_ts: null, queue_depth: 0, busy_reasons: [],
      },
      questions: null,
      git: null,
      markers: { human_turn_at: null, engine_touch_at: null, turn_counter: null, goal_stop_present: false, recovery: null },
      presence_baseline: null,
      presence: null,
      cursor_readback: null,
    },
    delivery: {
      lane: null, delivery_id: null, slot_key: null, session_key: null, wire_text: null, host: null,
      delivering_at: null, delivered_at: null, queued_at: null, deadline_at: null, receipt: null,
      outcome: null, delivery_confirmed: false, boot_id_unchanged: null, transcript_row: null,
      slot_ran_since: null, disk_baseline_unchanged: null,
    },
    resolution: { kind: null, resolved_at: null, evidence: null, reason: null },
    failure: null,
    install: null,
    budget: null,
    advisor: { latest_draft_id: null, status: null },
    deep_link: `/apps/aidlc-studio?view=actions&action=${spec.id}`,
    dedupe_key: null,
    human_text_present: false,
    acknowledged_evidence_sha256: 'sha-ack',
  } as unknown as ActionCard
}

function installRoutes(cards: ActionCard[], over: Record<string, RouteHandler> = {}) {
  const response = () => {
    const counts = { total: cards.length, critical: 0, blocking: 0, attention: 0, info: 0 }
    for (const entry of cards) counts[entry.severity] += 1
    return { actions: cards.slice(), organize: 'priority', groups: [], counts, generated_at: '2026-09-04T10:00:00Z' }
  }
  setApiRoutes({
    [`GET ${BASE}/actions?*`]: response,
    [`GET ${BASE}/actions`]: response,
    [`GET ${BASE}/actions/*`]: (_body, path) => {
      const id = path.split('/').pop() ?? ''
      const found = cards.find((entry) => entry.action_id === id) ?? cards[0]!
      return { action: found, transitions: [], drafts: [] }
    },
    [`GET ${BASE}/repos*`]: () => ({
      repos: [{ repo_id: 'r_1', label: 'checkout-web', canonical_path: '/work/checkout-web', availability: 'available' }],
      totals: { repos: 1, unavailable: 0, open_actions: cards.length },
    }),
    [`GET ${BASE}/leases`]: () => ({ leases: [], global_concurrency_cap: 2, live_execution: 0 }),
    [`GET ${BASE}/settings`]: () => ({
      settings: { night_window: { enabled: false, start_local: '22:00', end_local: '06:00' } },
      capabilities: { grouped_answers: { available: false, reason: 's1_s2_unverified' } },
      versions: {}, updated_at: null,
    }),
    [`GET ${BASE}/health`]: () => ({ status: 'healthy', issues: [] }),
    [`GET ${BASE}/events/poll*`]: () => ({ events: [], cursor: 0, oldest_seq: 0, reset: false }),
    [`POST ${BASE}/actions/*`]: () => ({ ok: true }),
    ...over,
  })
}

const mount = () =>
  render(
    <I18nProvider>
      <StudioApp />
    </I18nProvider>,
  )

const rows = () => screen.queryAllByRole('button', { name: /waiting/i })

afterEach(() => {
  window.history.replaceState(null, '', '/apps/aidlc-studio')
  localStorage.clear()
})

describe('the action queue', () => {
  it('counts only human work while retaining failures, uncertainty and questions from a running session', async () => {
    const statuses: ActionCard['status'][] = [
      'Queued', 'NotDelivered', 'DeliveryUncertain', 'ReconciliationRequired', 'Failed',
      'Delivering', 'Delivered', 'Processing', 'StateChanged', 'ResolvedNoTransition', 'Cancelled',
    ]
    const cards = statuses.map((status, index) => card({ id: `a_status_${index}`, status }))
    cards[0]!.evidence.session!.running = true
    installRoutes(cards)
    mount()
    await waitFor(() => expect(rows()).toHaveLength(5))
    expect(rows().map((row) => row.getAttribute('data-action-id'))).toEqual(
      expect.arrayContaining(statuses.slice(0, 5).map((_status, index) => `a_status_${index}`)),
    )
    expect(screen.getByText('5 of 5')).toBeInTheDocument()
    expect(screen.getByRole('navigation', { name: 'Primary' }).querySelector('.studio-count')).toHaveTextContent('5')
  })

  it('keeps the selected execution progress readable when no item needs a decision', async () => {
    const processing = card({ id: 'a_processing', status: 'Processing' })
    processing.primary = null
    processing.decisions = []
    installRoutes([processing])
    window.history.replaceState(null, '', '/apps/aidlc-studio?view=actions&action=a_processing')
    mount()

    expect(await screen.findByText('Nothing needs your judgment')).toBeInTheDocument()
    expect(rows()).toHaveLength(0)
    expect(screen.getByText('0 of 0')).toBeInTheDocument()
    expect(await screen.findByRole('region', { name: 'Delivery state' })).toHaveTextContent('Being processed')
    expect(window.location.search).toContain('action=a_processing')
    expect(screen.getByRole('navigation', { name: 'Primary' }).querySelector('.studio-count')).toBeNull()
  })

  it('removes a submitted item, keeps polling, and restores uncertainty without sending twice', async () => {
    const cards = [card({ id: 'a_sent' })]
    const move = (status: ActionCard['status'], generation: number) => {
      cards[0] = {
        ...cards[0]!, status, status_generation: generation, primary: null, decisions: [],
        updated_at: `2026-09-04T10:00:0${generation}Z`,
      }
    }
    installRoutes(cards, {
      [`POST ${BASE}/actions/a_sent/submit`]: () => {
        move('Delivering', 2)
        return {
          ok: true, action_id: 'a_sent', status: 'Delivering', lane: 'human_lane',
          delivery_id: 'd_sent', slot_key: 'aidlc-r1-guest', session_key: 'ses_1c4',
          wire_text: 'Approve', expires_at: '2026-09-04T10:01:30Z', lease_generation: 1,
          host: { method: 'POST', path: '/api/chat?ws=1', body: { message: 'Approve', slot: 'aidlc-r1-guest', agent: 'aidlc' } },
        }
      },
      'GET /api/chat/slots': () => [{ key: 'aidlc-r1-guest', project: '/work/checkout-web', agent: 'aidlc', running: false }],
      'POST /api/chat?ws=1': () => ({ ok: true, slot: 'aidlc-r1-guest', mid: 'm_sent' }),
      [`POST ${BASE}/actions/a_sent/delivery`]: () => {
        move('Processing', 3)
        return { ok: true, action: cards[0], idempotent: false }
      },
    })
    mount()
    await waitFor(() => expect(rows()).toHaveLength(1))
    await userEvent.click(rows()[0]!)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    const confirmation = await screen.findByRole('group', { name: 'Confirmation' })
    await userEvent.click(within(confirmation).getByRole('button', { name: /Yes, send this exact text/ }))

    await waitFor(() => expect(rows()).toHaveLength(0))
    expect(screen.getByText('0 of 0')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Delivery state' })).toHaveTextContent('Being processed')
    expect(window.location.search).toContain('action=a_sent')

    // The underlying resource must retain the in-flight state, so this change is found by its fast poll.
    move('DeliveryUncertain', 4)
    await waitFor(() => expect(rows()).toHaveLength(1), { timeout: 5_000 })
    expect(rows()[0]).toHaveAttribute('data-action-id', 'a_sent')
    expect(screen.getByText('1 of 1')).toBeInTheDocument()
    expect(apiCalls.filter((call) => call.path === '/api/chat?ws=1')).toHaveLength(1)
  }, 10_000)

  it('groups by priority band with a heading per band, and counts what is visible', async () => {
    installRoutes([
      card({ id: 'a_1', type: 'recovery', severity: 'critical', waiting: '2026-09-04T03:00:00Z' }),
      card({ id: 'a_2', type: 'gate', severity: 'blocking', waiting: '2026-09-04T05:00:00Z' }),
      card({ id: 'a_3', type: 'question', severity: 'blocking', waiting: '2026-09-04T04:00:00Z' }),
      card({ id: 'a_4', type: 'budget_stop', severity: 'info', waiting: '2026-09-04T06:00:00Z' }),
    ])
    mount()

    await waitFor(() => expect(rows()).toHaveLength(4))
    expect(screen.getAllByRole('heading', { level: 3 }).map((node) => node.textContent)).toEqual([
      'Recovery required and delivery uncertain1',
      'Blocking Gates and questions2',
      'Budget stops and pauses1',
    ])
    expect(screen.getByText('4 of 4')).toBeInTheDocument()
    // Equal priority sorts oldest first (FR-ACT-004): the question waited longer than the gate.
    const labels = rows().map((node) => node.getAttribute('aria-label') ?? '')
    expect(labels[1]).toMatch(/^Question,/)
    expect(labels[2]).toMatch(/^Approval gate,/)
  })

  it('names type, repo, space, intent, stage, severity, waiting and the next action in one label', async () => {
    installRoutes([card({ id: 'a_1', space: 'staging' })])
    mount()

    await waitFor(() => expect(rows()).toHaveLength(1))
    expect(rows()[0]).toHaveAttribute(
      'aria-label',
      // The space appears because it is not `default`; the primary action is the last sentence.
      expect.stringContaining('Approval gate, checkout-web · staging, guest-checkout, 2.3 functional-design, Blocking, waiting'),
    )
    expect(rows()[0]?.getAttribute('aria-label')).toContain('Approve')
  })

  it('filters on repo, intent, stage and type, and updates the counter', async () => {
    installRoutes([
      card({ id: 'a_1', repo: 'checkout-web', stage: 'functional-design' }),
      card({ id: 'a_2', repo: 'billing-api', repoId: 'r_2', stage: 'nfr-requirements', slug: 'invoice-split' }),
    ])
    mount()
    await waitFor(() => expect(rows()).toHaveLength(2))

    await userEvent.type(screen.getByLabelText('Filter the queue'), 'billing')
    await waitFor(() => expect(rows()).toHaveLength(1))
    expect(screen.getByText('1 of 2')).toBeInTheDocument()
    expect(rows()[0]?.getAttribute('aria-label')).toContain('billing-api')

    await userEvent.clear(screen.getByLabelText('Filter the queue'))
    await waitFor(() => expect(rows()).toHaveLength(2))
  })

  it('explains an empty filter result differently from an empty queue', async () => {
    installRoutes([card({ id: 'a_1' })])
    mount()
    await waitFor(() => expect(rows()).toHaveLength(1))

    await userEvent.type(screen.getByLabelText('Filter the queue'), 'nothing-matches-this')
    await waitFor(() => expect(screen.getByText('No item matches that filter')).toBeInTheDocument())
    expect(screen.queryByText('Nothing needs your judgment')).not.toBeInTheDocument()
  })

  it('points at the Workflow Map when nothing needs a person', async () => {
    installRoutes([])
    mount()
    await waitFor(() => expect(screen.getByText('Nothing needs your judgment')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /Watch the Workflow Map/ })).toBeInTheDocument()
  })

  it('persists the organize choice and regroups by repo', async () => {
    installRoutes([
      card({ id: 'a_1', repo: 'checkout-web' }),
      card({ id: 'a_2', repo: 'billing-api', repoId: 'r_2' }),
    ])
    mount()
    await waitFor(() => expect(rows()).toHaveLength(2))

    await userEvent.click(screen.getByRole('button', { name: 'Repo' }))
    await waitFor(() =>
      expect(screen.getAllByRole('heading', { level: 3 }).map((node) => node.textContent)).toEqual([
        // The repo label is the user's own word for the checkout and is never translated; collation is
        // the reader's, so `billing-api` precedes `checkout-web`.
        'billing-api1',
        'checkout-web1',
      ]),
    )
    expect(localStorage.getItem(STUDIO_ORGANIZE_KEY)).toBe('repo')
  })

  it('renders far fewer rows than cards, so 200 items stay responsive', async () => {
    const many = Array.from({ length: 200 }, (_, index) =>
      card({ id: `a_${index}`, waiting: `2026-09-0${(index % 9) + 1}T09:00:00Z` }),
    )
    installRoutes(many)
    mount()

    await waitFor(() => expect(rows().length).toBeGreaterThan(0))
    // A window, not the whole list: the exact number depends on the measured height, which jsdom
    // reports as zero, so only the bound matters.
    expect(rows().length).toBeLessThan(200)
    expect(screen.getByText('200 of 200')).toBeInTheDocument()
  })

  it('marks a row whose evidence is refreshing and refuses to submit against it', async () => {
    installRoutes([card({ id: 'a_1', stable: false })])
    mount()
    await waitFor(() => expect(rows()).toHaveLength(1))

    const row = rows()[0]!
    expect(within(row).getByText('Refreshing')).toBeInTheDocument()

    // Still openable: the user has to be able to read why it cannot be submitted.
    await userEvent.click(row)
    await waitFor(() => expect(screen.getByRole('button', { name: /^Approve/ })).toBeDisabled())
    expect(screen.getByRole('button', { name: /Request changes/ })).toBeDisabled()
    expect(screen.getByText(/changing right now/)).toBeInTheDocument()
  })
})

describe('the decision pane', () => {
  it('shows the breadcrumb, the delivery steps and the deep-link control for the selected card', async () => {
    installRoutes([card({ id: 'a_1' })])
    mount()
    await waitFor(() => expect(rows()).toHaveLength(1))

    await userEvent.click(rows()[0]!)
    await waitFor(() => expect(screen.getByRole('tab', { name: /Decision/ })).toHaveAttribute('aria-selected', 'true'))

    const crumb = screen.getByRole('navigation', { name: 'Where this decision sits' })
    expect(crumb).toHaveTextContent('Approval gate')
    expect(crumb).toHaveTextContent('checkout-web')
    expect(crumb).toHaveTextContent('guest-checkout')
    expect(screen.getByRole('button', { name: /deep link/ })).toBeInTheDocument()

    const strip = screen.getByRole('region', { name: 'Delivery state' })
    // The four steps, in order. Read off the list rather than by text: "Queued" is also the card's
    // status chip in the same region, and a `getByText` would match both.
    const steps = [...strip.querySelectorAll('.studio-dstep .studio-dstep-label')].map((node) => node.textContent)
    expect(steps).toEqual(['Queued', 'Sent', 'Agent processing', 'State changed'])
  })

  it('opens a confirmation showing the localized label and the exact wire text, and never preselects it', async () => {
    installRoutes([card({ id: 'a_1' })])
    mount()
    await waitFor(() => expect(rows()).toHaveLength(1))
    await userEvent.click(rows()[0]!)

    const approve = await screen.findByRole('button', { name: /^Approve/ })
    // Nothing is confirmed until the user asks: the confirmation does not exist yet, and Approve does
    // not have focus.
    expect(screen.queryByRole('group', { name: 'Confirmation' })).not.toBeInTheDocument()
    expect(approve).not.toHaveFocus()

    await userEvent.click(approve)
    const panel = await screen.findByRole('group', { name: 'Confirmation' })
    expect(within(panel).getByText('Button: Approve → sends: Approve')).toBeInTheDocument()
    // The bytes, in a `pre`, exactly once.
    expect(within(panel).getByText('Approve', { selector: 'pre' })).toBeInTheDocument()
    expect(within(panel).getByText(/userPromptSubmit hook mints the protected HUMAN_TURN/)).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: /Yes, send this exact text/ })).toBeEnabled()
  })

  it('confirms a force stop as a KiroCrew call, with no wire text to send', async () => {
    const stop = card({ id: 'a_9', type: 'force_stop', severity: 'attention' })
    stop.decisions = [
      { decision: 'force_stop', label_key: 'decision.force_stop.label', lane: 'host_control', wire_text_template: null, requires: [] },
    ]
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(stop as unknown as { primary: unknown }).primary = { decision: 'force_stop', label_key: 'decision.force_stop.label' }
    installRoutes([stop])
    mount()
    await waitFor(() => expect(rows()).toHaveLength(1))
    await userEvent.click(rows()[0]!)

    // Scoped to the action bar: the row's own accessible label also ends with the primary action.
    const bar = document.querySelector('.studio-actionbar') as HTMLElement
    await userEvent.click(within(bar).getByRole('button', { name: /Force stop/ }))
    const panel = await screen.findByRole('group', { name: 'Confirmation' })
    // No wire text: a stop is not a prompt, so there is nothing to display as bytes and nothing to
    // compare byte-for-byte.
    expect(panel.querySelector('pre')).toBeNull()
    expect(within(panel).getByText(/asks KiroCrew to stop the running turn/)).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: /Yes, apply this/ })).toBeEnabled()
  })

  it('refuses to send a change request with no feedback, and says why', async () => {
    installRoutes([card({ id: 'a_1' })])
    mount()
    await waitFor(() => expect(rows()).toHaveLength(1))
    await userEvent.click(rows()[0]!)

    const reject = await screen.findByRole('button', { name: /Request changes/ })
    // The control is reachable and explains itself rather than being silently inert.
    expect(reject).toBeDisabled()
    expect(reject).toHaveAttribute('title', 'Requesting changes needs a note saying what to change.')
  })
})
