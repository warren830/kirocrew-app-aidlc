/**
 * The intent inventory's risky paths, and the binding sequence the whole product rests on.
 *
 * Two matter most. `Run to next checkpoint` must create a queued command and send nothing: the row hands
 * the card to the Action Center, where the wire text is shown before it is sent, so a second entry point
 * cannot become a second way to dispatch without a confirmation.
 *
 * The session panel's tests assert the four calls of §2.13 as WIRE CALLS — method, path and body, in
 * order — through the app-sdk stub's route table rather than against a mocked client, because the order
 * and the bodies are the contract: the project must be set before Studio verifies it, and a decision on
 * an intent bound by any other sequence is refused `slot_mismatch`. A test that only checked "the client
 * was called" would pass with the PATCH sent as a POST, which the host answers 404 to.
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '../i18n'
import { StudioApiError, useStudioApi, type StudioApi } from '../lib/api'
import { buildRoute, parseRoute } from '../lib/route'
import type { IntentSummary, RepoRecord, SlotView } from '../lib/types'
import { StudioApp } from '../shell/StudioApp'
import { API_BASE, actionCard, shellRoutes } from '../test/fixtures'
import { apiCalls, setApiRoutes, StubApiError } from '../test/stubs/app-sdk'
import { IntentActions } from './IntentActions'
import { IntentList } from './IntentList'
import { readInventory } from './IntentsView'
import { SessionPanel } from './SessionPanel'

const INTENT = {
  repo_id: 'r_1', repo_label: 'checkout-web', space: 'default', intent_dir: '250901-guest-checkout',
  intent_key: 'default~250901-guest-checkout', uuid: null, slug: 'guest-checkout', scope: 'feature',
  registry_status: 'Running', title: 'Guest checkout', operational_state: 'WaitingForYou',
  disk: {
    status: 'Running', lifecycle_phase: 'inception', current_stage: '2.3 functional-design',
    next_stage: '2.4 nfr-requirements', state_version: 6, total_stages: 33, completed: 7,
    revision_count: 1, parked_at: null, last_updated: '2026-09-05T08:00:00Z',
  },
  counts: { total: 33, not_started: 20, in_progress: 1, awaiting_approval: 1, revising: 0, completed: 7, skipped: 4, done: 11, unknown: 0 },
  open_actions: 2, blocking_findings: 0, warn_findings: 1,
  session: { slot_key: 's1', session_key: 'ses_1', running: false, bound_at: '2026-09-05T07:00:00Z' },
  archived: false, paused: false, keep_moving: false, interrupted: false,
  last_activity_at: '2026-09-05T08:00:00Z', stable_boundary: null, unstable: false, is_active_cursor: true,
} as unknown as IntentSummary

afterEach(() => window.history.replaceState(null, '', '/apps/aidlc-studio'))

describe('the intents inventory', () => {
  it('renders a row with its state, stage and unavailable keep-moving column', () => {
    render(
      <I18nProvider>
        <IntentList intents={[INTENT]} failures={[{ repo: 'payments-api', message: 'not reachable' }]} narrow={false} renderActions={() => null} />
      </I18nProvider>,
    )
    expect(screen.getByText('250901-guest-checkout')).toBeInTheDocument()
    expect(screen.getByText('Waiting for you')).toBeInTheDocument()
    expect(screen.getByText('2.3 functional-design')).toBeInTheDocument()
    expect(screen.getByText('11 of 33 stages')).toBeInTheDocument()
    expect(screen.getByText('2 decisions waiting')).toBeInTheDocument()
    expect(screen.getByText('Unavailable')).toBeInTheDocument()
    expect(screen.getByText('payments-api: not reachable')).toBeInTheDocument()
  })

  it('queues a run without sending it, and pauses behind a confirmation', async () => {
    const runnable = { ...INTENT, operational_state: 'Idle' as const,
      counts: { ...INTENT.counts, awaiting_approval: 0 } }
    const api = {
      run: vi.fn(async () => ({ ok: true as const, action_id: 'a_9', status: 'Queued' as const, action: {} as never })),
      pause: vi.fn(async () => ({ ok: true as const, binding: {} as never, blocked_actions: ['a_1', 'a_2'] })),
    } as unknown as StudioApi
    const onQueued = vi.fn()
    const onChanged = vi.fn()
    render(
      <I18nProvider>
        <IntentActions api={api} intent={runnable} onGo={() => {}} onQueued={onQueued} onChanged={onChanged} onRecompose={() => {}} onSession={() => {}} />
      </I18nProvider>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Run to next checkpoint' }))
    await waitFor(() => expect(api.run).toHaveBeenCalledWith('r_1', 'default~250901-guest-checkout'))
    expect(onQueued).toHaveBeenCalledWith('a_9', runnable)

    await userEvent.click(screen.getByRole('button', { name: 'Pause after current turn' }))
    expect(screen.getByText('2 waiting decisions will refuse to send while this intent is paused.')).toBeInTheDocument()
    expect(api.pause).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(api.pause).toHaveBeenCalledWith('r_1', 'default~250901-guest-checkout', true))
    expect(onChanged).toHaveBeenCalledWith('Paused. 2 waiting decisions will now refuse to send.')
  })

  it.each(['approval', 'question', 'running', 'completed'])('does not offer another Run during %s', async (kind) => {
    const value: IntentSummary = { ...INTENT, operational_state: 'Idle',
      counts: { ...INTENT.counts, awaiting_approval: 0 } }
    if (kind === 'approval') value.counts.awaiting_approval = 1
    if (kind === 'question') value.operational_state = 'WaitingForYou'
    if (kind === 'running') value.session = { ...INTENT.session!, running: true }
    if (kind === 'completed') value.disk = { ...INTENT.disk, status: 'Completed' }
    const api = { run: vi.fn() } as unknown as StudioApi
    render(<I18nProvider><IntentActions api={api} intent={value} onGo={() => {}}
      onQueued={() => {}} onChanged={() => {}} onRecompose={() => {}} onSession={() => {}} /></I18nProvider>)
    const button = screen.getByRole('button', { name: 'Run to next checkpoint' })
    expect(button).toBeDisabled()
    await userEvent.click(button)
    expect(api.run).not.toHaveBeenCalled()
  })

  it('reports the repositories it could not read instead of dropping them', async () => {
    const api = {
      intents: vi.fn(async (repoId: string) => {
        if (repoId === 'r_2') throw new StudioApiError('repo_unavailable', 'nope', {}, 409)
        return { intents: [INTENT], spaces: ['default'], active_space: 'default' }
      }),
    } as unknown as StudioApi
    const repos = [
      { repo_id: 'r_1', label: 'checkout-web' },
      { repo_id: 'r_2', label: 'payments-api' },
    ] as never
    const answer = await readInventory(api, repos, { space: '', state: '', q: '', archived: false }, new AbortController().signal, (e) => e.code)
    expect(answer.intents).toHaveLength(1)
    expect(answer.failures).toEqual([{ repo: 'payments-api', message: 'repo_unavailable' }])
    expect(api.intents).toHaveBeenCalledWith('r_1', {}, expect.anything())
  })

  it('opens the scoped decision queue without retaining an answered action or its artifact tab', async () => {
    const capturedIntent = { ...actionCard().intent, intent_key: INTENT.intent_key }
    const answered = actionCard({
      action_id: 'a_answered', type: 'question', status: 'ResolvedNoTransition', intent: capturedIntent,
    })
    const current = actionCard({
      action_id: 'a_current_failure', type: 'failure', status: 'Failed', intent: capturedIntent,
    })
    setApiRoutes({
      ...shellRoutes([current]),
      [`GET ${API_BASE}/actions/${answered.action_id}`]: () => ({ action: answered, transitions: [], drafts: [] }),
      [`GET ${API_BASE}/repos*`]: () => ({
        repos: [{
          repo_id: INTENT.repo_id, label: INTENT.repo_label, canonical_path: '/work/checkout-web',
          availability: 'available', counts: { intents: 1 },
        }],
        totals: { repos: 1, unavailable: 0, open_actions: 1 },
      }),
      [`GET ${API_BASE}/repos/${INTENT.repo_id}/intents`]: () => ({
        intents: [INTENT], spaces: ['default'], active_space: 'default',
      }),
    })
    window.history.replaceState(null, '', buildRoute({
      view: 'intents', repo: INTENT.repo_id, intent: INTENT.intent_key,
      action: answered.action_id, tab: 'artifacts', artifact: 'art_previous',
      stage: 'requirements-analysis', unit: 'unit_previous', anchor: 'h-old-question',
    }))
    render(<I18nProvider><StudioApp /></I18nProvider>)

    await userEvent.click(await screen.findByRole('button', { name: 'Open decision' }))
    expect(parseRoute(window.location.search, window.location.hash)).toMatchObject({
      view: 'actions', repo: INTENT.repo_id, space: INTENT.space, intent: INTENT.intent_key,
      action: '', artifact: '', tab: 'decision', stage: '', unit: '', anchor: '',
    })
    expect(await screen.findByText('Select an item')).toBeInTheDocument()
    expect(apiCalls.some((call) => call.path === `${API_BASE}/actions/${answered.action_id}`)).toBe(false)
    expect(apiCalls.some((call) => call.path.includes('/artifacts/'))).toBe(false)
    await userEvent.click(await screen.findByRole('button', { name: /^Failure/ }))
    await waitFor(() => expect(apiCalls.some((call) => call.path === `${API_BASE}/actions/${current.action_id}`)).toBe(true))
    expect(parseRoute(window.location.search, window.location.hash)).toMatchObject({
      action: current.action_id, artifact: '', tab: 'decision',
    })
    expect(apiCalls.every((call) => call.method === 'GET')).toBe(true)
  })
})

// --------------------------------------------------------------------------- //
// the canonical conversation
// --------------------------------------------------------------------------- //

const BASE = '/api/apps/aidlc-studio'
const CHAT = '/api/chat'
/** `SLOT_KEY_TEMPLATE` for this fixture: `aidlc-studio-{repo_id}-{intent_dir}`. */
const SLOT = 'aidlc-studio-r_1-250901-guest-checkout'
const INTENT_PATH = `${BASE}/repos/r_1/intents/default~250901-guest-checkout`

const REPO = { repo_id: 'r_1', label: 'checkout-web', canonical_path: '/work/checkout-web' } as unknown as RepoRecord

/** Only the four fields the panel reads; a real `SlotView` has 24 more the host fills in. */
const slot = (over: Partial<SlotView>): SlotView =>
  ({ key: 'k', project: '/work/checkout-web', agent: 'aidlc', running: false, title: '', ...over }) as unknown as SlotView

const UNBOUND = { ...INTENT, session: null } as IntentSummary

/**
 * The panel with a REAL client, so the assertions are about the requests that leave the browser.
 *
 * `useStudioApi()` is the same hook the app uses; the app-sdk stub dispatches its paths to the route
 * table and records every call, which is the only way to prove the PATCH is a PATCH.
 */
function Panel({ intent, onChanged = () => {} }: { intent: IntentSummary; onChanged?: (message?: string) => void }) {
  const api = useStudioApi()
  return <SessionPanel api={api} repo={REPO} intent={intent} onClose={() => {}} onChanged={onChanged} />
}

const mount = (intent: IntentSummary, onChanged?: (message?: string) => void) =>
  render(
    <I18nProvider>
      <Panel intent={intent} {...(onChanged ? { onChanged } : {})} />
    </I18nProvider>,
  )

describe('the canonical conversation panel', () => {
  it('creates, titles, points and binds the conversation, in that order and nothing else', async () => {
    setApiRoutes({
      [`POST ${CHAT}/slots`]: () => ({ key: SLOT, agent: 'aidlc', project: '' }),
      [`PATCH ${CHAT}/slots/${SLOT}/title`]: () => ({ ok: true }),
      [`POST ${CHAT}/slots/${SLOT}/project`]: () => ({ ok: true, project: '/work/checkout-web' }),
      [`POST ${INTENT_PATH}/session/bind`]: () => ({
        ok: true,
        binding: { slot_key: SLOT, session_key: 'ses_7' },
        slot: slot({ key: SLOT, running: true }),
      }),
    })
    const onChanged = vi.fn()
    mount(UNBOUND, onChanged)

    await userEvent.click(screen.getByRole('button', { name: 'Create and bind the conversation' }))

    await waitFor(() => expect(screen.getByText('ses_7')).toBeInTheDocument())
    expect(apiCalls).toEqual([
      { method: 'POST', path: `${CHAT}/slots`, body: { name: SLOT, agent: 'aidlc' } },
      { method: 'PATCH', path: `${CHAT}/slots/${SLOT}/title`, body: { title: 'checkout-web / guest-checkout' } },
      { method: 'POST', path: `${CHAT}/slots/${SLOT}/project`, body: { project: '/work/checkout-web' } },
      { method: 'POST', path: `${INTENT_PATH}/session/bind`, body: { slot_key: SLOT } },
    ])
    // The bound state is shown from the answer, not from a refetch the caller may not have made yet.
    expect(screen.getByText(SLOT)).toBeInTheDocument()
    expect(screen.getByText('A turn is running')).toBeInTheDocument()
    expect(onChanged).toHaveBeenCalledWith(`Bound to ${SLOT}.`)
  })

  it('names the step the host refused and does not bind a slot the host would not point at the repo', async () => {
    setApiRoutes({
      [`POST ${CHAT}/slots`]: () => ({ key: SLOT }),
      [`PATCH ${CHAT}/slots/${SLOT}/title`]: () => ({ ok: true }),
      [`POST ${CHAT}/slots/${SLOT}/project`]: () => {
        throw new StubApiError(403, { error: 'refusing a sensitive path: /work/checkout-web' })
      },
      [`POST ${INTENT_PATH}/session/bind`]: () => ({ ok: true, binding: { slot_key: SLOT }, slot: null }),
    })
    mount(UNBOUND)

    await userEvent.click(screen.getByRole('button', { name: 'Create and bind the conversation' }))

    expect(
      await screen.findByText(/Step 3 of 4 was refused \(point it at this repository\)/),
    ).toBeInTheDocument()
    // The host's own words, verbatim: a 403 with no code would otherwise read as "you are not the owner".
    expect(screen.getByText(/refusing a sensitive path: \/work\/checkout-web/)).toBeInTheDocument()
    expect(apiCalls.map((call) => call.path)).not.toContain(`${INTENT_PATH}/session/bind`)
    expect(screen.getByRole('button', { name: 'Create and bind the conversation' })).toBeInTheDocument()
  })

  it('offers only aidlc conversations on this repository, and binds the one chosen', async () => {
    setApiRoutes({
      [`GET ${CHAT}/slots`]: () => [
        // A trailing slash is not a difference; the host reports realpaths.
        slot({ key: 'aidlc-hand-made', project: '/work/checkout-web/', title: 'Guest checkout' }),
        slot({ key: 'general-chat', agent: 'general' }),
        slot({ key: 'aidlc-payments', project: '/work/payments-api' }),
      ],
      [`POST ${INTENT_PATH}/session/bind`]: () => ({
        ok: true,
        binding: { slot_key: 'aidlc-hand-made', session_key: 'ses_2' },
        slot: null,
      }),
    })
    const onChanged = vi.fn()
    mount(UNBOUND, onChanged)

    await userEvent.click(screen.getByRole('button', { name: 'Bind an existing conversation' }))

    expect(await screen.findByText('aidlc-hand-made')).toBeInTheDocument()
    // A slot the backend would answer `slot_mismatch` to is never offered.
    expect(screen.queryByText('general-chat')).not.toBeInTheDocument()
    expect(screen.queryByText('aidlc-payments')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Bind this one' }))
    await waitFor(() =>
      expect(apiCalls.at(-1)).toEqual({
        method: 'POST',
        path: `${INTENT_PATH}/session/bind`,
        body: { slot_key: 'aidlc-hand-made' },
      }),
    )
    expect(screen.getByText('ses_2')).toBeInTheDocument()
    expect(onChanged).toHaveBeenCalledWith('Bound to aidlc-hand-made.')
  })

  it('shows the bound conversation and unbinds it', async () => {
    setApiRoutes({
      [`POST ${INTENT_PATH}/session/unbind`]: () => ({ ok: true, binding: { slot_key: null, session_key: null } }),
    })
    const onChanged = vi.fn()
    mount(INTENT, onChanged)

    expect(screen.getByText('s1')).toBeInTheDocument()
    expect(screen.getByText('ses_1')).toBeInTheDocument()
    expect(screen.getByText('Not running')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Unbind' }))

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Create and bind the conversation' })).toBeInTheDocument(),
    )
    expect(apiCalls).toEqual([{ method: 'POST', path: `${INTENT_PATH}/session/unbind`, body: {} }])
    expect(onChanged).toHaveBeenCalledWith(
      'Unbound. Decisions for this intent will refuse to send until a conversation is bound.',
    )
  })

  it('moves a bound intent to a candidate the backend offered, with the backend\'s own reason', async () => {
    setApiRoutes({
      [`POST ${INTENT_PATH}/session/takeover/preview`]: () => ({
        candidates: [{ slot: slot({ key: 'aidlc-hand-made' }), reason: 'project_match' }],
        current: { slot_key: 's1' },
      }),
      [`POST ${INTENT_PATH}/session/takeover`]: () => ({
        ok: true,
        binding: { slot_key: 'aidlc-hand-made', session_key: 'ses_3' },
      }),
    })
    mount(INTENT)

    await userEvent.click(screen.getByRole('button', { name: 'Move to another conversation' }))
    expect(await screen.findByText('Open on this repository')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Move it here' }))
    await waitFor(() =>
      expect(apiCalls.at(-1)).toEqual({
        method: 'POST',
        path: `${INTENT_PATH}/session/takeover`,
        body: { slot_key: 'aidlc-hand-made' },
      }),
    )
    expect(screen.getByText('ses_3')).toBeInTheDocument()
  })
})
