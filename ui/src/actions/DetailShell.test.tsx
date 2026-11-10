/**
 * The confirmation never offers a send that can only fail, and never hides the server's answer.
 *
 * Both properties are about one screen: on a real gate the detail pane is several screens long, the
 * refusal banner renders above the card body, and the send control is in the sticky confirmation at the
 * bottom. A person who pressed "Yes, send this exact text" on an intent with no bound conversation got a
 * `409 session_unbound` and saw nothing at all — the refusal was painted far above the fold (live gate
 * `a_391817cda77a88cc`, `binding: {}`). So these tests assert placement, not just presence: the reason
 * and the refusal have to be inside the confirmation region, next to the control that was pressed.
 *
 * Mounted through `DetailShell` itself rather than a template, because the bar, the confirmation and
 * `useSubmit` are the composition under test (FR-GATE-001/002, FR-SES-006, §1.12 step 5).
 */

import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider, makeI18n } from '../i18n'
import { useStudioApi } from '../lib/api'
import { APP_NAME } from '../lib/app'
import { at } from '../lib/format'
import { EMPTY_ROUTE, type Navigate } from '../lib/route'
import type { ActionCard, SubmitRequest } from '../lib/types'
import { API_BASE, actionCard, confirmedLate, decisionSpec, submitReceipt } from '../test/fixtures'
import { apiCalls, setApiRoutes, StubApiError, type RouteHandler } from '../test/stubs/app-sdk'
import { DetailShell, transitionReason } from './DetailShell'
import '../templates/TemplateRegistry'

const en = makeI18n('en-US')
const zh = makeI18n('zh-CN')

/** One human-lane gate card whose intent has no canonical conversation, as the projection sends it. */
function unbound(): ActionCard {
  const base = actionCard()
  return { ...base, evidence: { ...base.evidence, session: null } }
}

function mount(card: ActionCard, go: Navigate = () => {}) {
  function Harness({ selected, actionId }: { selected: ActionCard | null; actionId: string }) {
    const api = useStudioApi()
    return (
      <DetailShell
        actionId={actionId}
        queueCard={selected}
        api={api}
        route={{ ...EMPTY_ROUTE, action: actionId }}
        go={go}
        groupedAnswers={false}
        onQueueChanged={() => {}}
      />
    )
  }
  const pane = (selected: ActionCard | null, actionId: string) => (
    <I18nProvider>
      <Harness selected={selected} actionId={actionId} />
    </I18nProvider>
  )
  const view = render(pane(card, card.action_id))
  return { ...view, select: (selected: ActionCard | null, actionId = selected?.action_id ?? '') =>
    view.rerender(pane(selected, actionId)) }
}

/** The confirmation, as a region — so an assertion cannot pass on text rendered somewhere else. */
const confirmation = () => screen.getByRole('group', { name: en.t('confirm.label') })

async function openApproval(card: ActionCard) {
  mount(card)
  await userEvent.click(await screen.findByRole('button', { name: new RegExp(en.t('decision.approve.label')) }))
  return confirmation()
}

const SEND = '/api/chat?ws=1'
const slot = { key: 'aidlc-r1-guest', project: '/work/checkout-web', agent: 'aidlc', running: false }

/** The card's own detail read and a host listing its slot; `over` replaces or adds handlers. */
function routesFor(card: ActionCard, over: Record<string, RouteHandler> = {}) {
  setApiRoutes({
    [`GET ${API_BASE}/actions/${card.action_id}`]: () => ({ action: card, transitions: [], drafts: [] }),
    'GET /api/chat/slots': () => [slot],
    ...over,
  })
}

describe('a decision whose intent has no canonical conversation', () => {
  it('shows a missing repository as saved evidence with a recovery link, before offering approval', async () => {
    const base = unbound()
    const card = { ...base, repo: { ...base.repo, availability: 'unavailable' as const,
      availability_detail: 'path_missing' } }
    const go = vi.fn()
    routesFor(card)
    mount(card, go)
    expect(await screen.findByRole('heading', { name: en.t('detail.repoUnavailable.title') })).toBeInTheDocument()
    expect(screen.getByText(en.t('detail.repoUnavailable.pathMissing'))).toBeInTheDocument()
    expect(screen.getByText(card.repo.canonical_path)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Approve/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: en.t('confirm.label') })).not.toBeInTheDocument()
    expect(screen.queryByText(en.t('confirm.blocked.sessionUnbound'))).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: en.t('detail.repoUnavailable.manage') }))
    expect(go).toHaveBeenCalledWith({ ...EMPTY_ROUTE, view: 'repos', repo: card.repo.repo_id })
    expect(apiCalls.filter((c) => c.method !== 'GET')).toHaveLength(0)
  })

  it('refuses the send in the confirmation instead of letting the server refuse it', async () => {
    const card = unbound()
    routesFor(card)

    const panel = await openApproval(card)
    // The reason names the injection, the missing binding and the one thing that helps — inside the
    // confirmation, because a `title` on a disabled bar button is never shown in Chromium.
    const reason = within(panel).getByText(en.t('confirm.blocked.sessionUnbound'))
    // The host's own words, not a translation of them: a chat opened with any other agent cannot be
    // bound (`SessionBinder.bind` requires `slot.agent == "aidlc"`), so the name has to survive both
    // catalogs. This also fails if the key is missing, which renders as the key itself.
    expect(reason.textContent).toContain('aidlc agent')
    expect(zh.t('confirm.blocked.sessionUnbound')).toContain('aidlc agent')
    expect(within(panel).getByRole('button', { name: new RegExp(en.t('confirm.send')) })).toBeDisabled()
  })

  it('says where the send would go in a whole sentence and links to binding a conversation', async () => {
    const card = unbound()
    const go = vi.fn()
    routesFor(card)
    mount(card, go)
    await userEvent.click(await screen.findByRole('button', { name: new RegExp(en.t('decision.approve.label')) }))
    const panel = confirmation()

    expect(within(panel).queryByText(new RegExp(en.t('common.unavailable')))).toBeNull()
    expect(within(panel).getByText(/once one is bound/)).toBeInTheDocument()
    await userEvent.click(within(panel).getByRole('button', { name: en.t('confirm.openSession') }))
    expect(go).toHaveBeenCalledWith({
      ...EMPTY_ROUTE, view: 'intents', repo: card.repo.repo_id, intent: card.intent.intent_key, tab: 'conversation',
    })
    expect(apiCalls.filter((c) => c.method !== 'GET')).toHaveLength(0)
  })

  it('offers the send once a matching conversation is bound', async () => {
    const card = actionCard()
    routesFor(card)

    const panel = await openApproval(card)
    expect(within(panel).queryByText(en.t('confirm.blocked.sessionUnbound'))).toBeNull()
    expect(within(panel).getByRole('button', { name: new RegExp(en.t('confirm.send')) })).toBeEnabled()
  })
})

describe('the embedded conversation', () => {
  it('leaves out the host chat’s Stop and queue, so a stop goes through Studio’s own Force stop', async () => {
    const card = actionCard()
    routesFor(card)
    function Harness() {
      const api = useStudioApi()
      return (
        <DetailShell actionId={card.action_id} queueCard={card} api={api}
          route={{ ...EMPTY_ROUTE, action: card.action_id, tab: 'conversation' }} go={() => {}}
          groupedAnswers={false} onQueueChanged={() => {}} />
      )
    }
    render(<I18nProvider><Harness /></I18nProvider>)
    expect(await screen.findByTestId('chat-embed')).toHaveAttribute('data-hide', 'stop queue')
  })
})

describe('a refusal the server did return', () => {
  it('retires a confirmation when the queue advances beyond the cached detail', async () => {
    const card = actionCard()
    routesFor(card)
    const view = mount(card)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    expect(confirmation()).toBeInTheDocument()

    view.select({ ...card, status: 'Cancelled', status_generation: card.status_generation + 1,
      decisions: [], primary: null,
      resolution: { kind: 'cancelled', resolved_at: card.updated_at, evidence: null, reason: 'command_superseded' } })

    expect(screen.queryByRole('group', { name: en.t('confirm.label') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Approve/ })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Cancelled')
    expect(apiCalls.filter((call) => call.method !== 'GET')).toHaveLength(0)
  })

  it('does not carry a refused submission into a different selected action', async () => {
    const first = actionCard()
    const second = actionCard({ action_id: 'a_second' })
    routesFor(first, {
      [`GET ${API_BASE}/actions/${second.action_id}`]: () => ({ action: second, transitions: [], drafts: [] }),
      [`POST ${API_BASE}/actions/${first.action_id}/submit`]: () => {
        throw new StubApiError(409, { code: 'slot_busy', error: 'this conversation is busy' })
      },
    })
    const view = mount(first)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    await userEvent.click(within(confirmation()).getByRole('button', { name: /send this exact text/ }))
    await screen.findAllByText(en.t('errors.slot_busy'))

    view.select(second)
    await waitFor(() => expect(apiCalls.some((call) => call.path.endsWith(`/actions/${second.action_id}`))).toBe(true))
    expect(screen.queryByText(en.t('errors.slot_busy'))).not.toBeInTheDocument()
    expect(screen.queryByText(en.t('detail.nothingSent'))).not.toBeInTheDocument()
  })

  it('opens another card at its top, not at the scroll offset the last card was left at', async () => {
    // Enterprise run, 2026-11-04: after answering a learnings card scrolled to its end, the gate opened next showed
    // its Advisor and Approve, with the artifact and the reviewer's findings out of view above.
    const first = actionCard()
    const second = actionCard({ action_id: 'a_second' })
    routesFor(first, {
      [`GET ${API_BASE}/actions/${second.action_id}`]: () => ({ action: second, transitions: [], drafts: [] }),
    })
    const view = mount(first)
    await screen.findByRole('button', { name: /^Approve/ })
    const body = () => document.querySelector('.studio-detail-body') as HTMLElement
    body().scrollTop = 900
    expect(body().scrollTop).toBe(900)
    view.select(second)
    await waitFor(() => expect(apiCalls.some((call) => call.path.endsWith(`/actions/${second.action_id}`))).toBe(true))
    expect(body().scrollTop).toBe(0)
  })

  it('ignores a late stale-card response while another action is loading', async () => {
    const first = actionCard()
    const second = actionCard({ action_id: 'a_loading' })
    let refuse!: (reason: unknown) => void
    let finishRead!: (value: unknown) => void
    const submission = new Promise((_, reject) => { refuse = reject })
    const read = new Promise((resolve) => { finishRead = resolve })
    routesFor(first, {
      [`GET ${API_BASE}/actions/${second.action_id}`]: () => read,
      [`POST ${API_BASE}/actions/${first.action_id}/submit`]: () => submission,
    })
    const view = mount(first)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    await userEvent.click(within(confirmation()).getByRole('button', { name: /send this exact text/ }))
    view.select(null, second.action_id)
    await act(async () => refuse(new StubApiError(409, {
      code: 'action_stale', error: 'the old action changed', details: { card: first },
    })))

    expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument()
    expect(screen.queryByText(en.t('errors.action_stale'))).not.toBeInTheDocument()
    await act(async () => finishRead({ action: second, transitions: [], drafts: [] }))
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument()
    expect(screen.queryByText(en.t('errors.action_stale'))).not.toBeInTheDocument()
  })

  it('keeps an in-flight action disabled after navigating away and back', async () => {
    const first = actionCard()
    const second = actionCard({ action_id: 'a_other' })
    let refuse!: (reason: unknown) => void
    const submission = new Promise((_, reject) => { refuse = reject })
    routesFor(first, {
      [`GET ${API_BASE}/actions/${second.action_id}`]: () => ({ action: second, transitions: [], drafts: [] }),
      [`POST ${API_BASE}/actions/${first.action_id}/submit`]: () => submission,
    })
    const view = mount(first)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    await userEvent.click(within(confirmation()).getByRole('button', { name: /send this exact text/ }))
    try {
      view.select(second)
      view.select(first)
      expect(screen.getByRole('button', { name: /^Approve/ })).toBeDisabled()
      expect(apiCalls.filter((call) => call.method === 'POST')).toHaveLength(1)
    } finally {
      await act(async () => refuse(new StubApiError(409, { code: 'repo_busy', error: 'Repository is busy.' })))
    }
  })

  it('closes the Run confirmation when the server retires its command', async () => {
    const base = actionCard()
    const card: ActionCard = { ...base, type: 'run', queue_type: 'run',
      primary: { decision: 'run', label_key: 'decision.run.label' },
      decisions: [{ decision: 'run', label_key: 'decision.run.label', lane: 'human_lane',
        requires: [], wire_text_template: '/aidlc' }] }
    let current = card
    routesFor(card, {
      [`GET ${API_BASE}/actions/${card.action_id}`]: () => ({ action: current, transitions: [], drafts: [] }),
    })
    mount(card)
    await userEvent.click(await screen.findByRole('button', { name: 'Run to next checkpoint' }))
    expect(confirmation()).toBeInTheDocument()
    current = { ...card, status: 'Cancelled', decisions: [], primary: null,
      resolution: { kind: 'cancelled', resolved_at: null, evidence: null, reason: 'command_superseded' } }
    act(() => document.dispatchEvent(new Event('visibilitychange')))
    expect(await screen.findByText(en.t('template.command.superseded'))).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: en.t('confirm.label') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Run to next checkpoint' })).not.toBeInTheDocument()
    expect(apiCalls.filter((call) => call.method !== 'GET')).toHaveLength(0)
  })

  it('distinguishes a refused additional attempt from the recorded earlier delivery', async () => {
    const base = actionCard()
    const card = { ...base, delivery: { ...base.delivery,
      delivered_at: '2026-09-12T16:28:42Z', wire_text: 'Free-text note',
    } }
    routesFor(card, {
      [`POST ${API_BASE}/actions/${card.action_id}/submit`]: () => {
        throw new StubApiError(409, { code: 'repo_busy', error: 'another operation holds this repository' })
      },
    })
    const panel = await openApproval(card)
    await userEvent.click(within(panel).getByRole('button', { name: /send this exact text/ }))
    expect(await screen.findByText(en.t('detail.additionalAttemptBlocked'))).toBeInTheDocument()
    expect(screen.queryByText(en.t('detail.nothingSent'))).not.toBeInTheDocument()
    expect(apiCalls.filter((call) => call.path === '/api/chat?ws=1')).toHaveLength(0)
  })

  it('explains repository contention and links to its holder without calling it a file disagreement', async () => {
    const card = actionCard()
    const go = vi.fn()
    routesFor(card, {
      [`POST ${API_BASE}/actions/${card.action_id}/submit`]: () => {
        throw new StubApiError(409, { code: 'repo_busy', error: 'another operation holds this repository',
          details: { owner: { action_id: 'a_0123456789abcdef' } } })
      },
    })
    mount(card, go)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    await userEvent.click(within(confirmation()).getByRole('button', { name: /send this exact text/ }))
    await waitFor(() => expect(within(confirmation()).getByRole('button', {
      name: en.t('detail.repoBusy.openOwner'),
    })).toBeInTheDocument())
    expect(screen.queryByText(en.t('errors.state_inconsistent'))).not.toBeInTheDocument()
    await userEvent.click(within(confirmation()).getByRole('button', { name: en.t('detail.repoBusy.openOwner') }))
    expect(go).toHaveBeenCalledWith({ ...EMPTY_ROUTE, view: 'actions', repo: card.repo.repo_id, action: 'a_0123456789abcdef' })
    expect(apiCalls.filter((call) => call.path === '/api/chat?ws=1')).toHaveLength(0)
  })

  it('removes a cached confirmation when its record is gone', async () => {
    const card = actionCard()
    const go = vi.fn()
    let exists = true
    routesFor(card, {
      [`GET ${API_BASE}/actions/${card.action_id}`]: () => {
        if (!exists) throw new StubApiError(404, { code: 'action_not_found', error: 'no such action' })
        return { action: card, transitions: [], drafts: [] }
      },
    })
    mount(card, go)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    expect(confirmation()).toBeInTheDocument()
    exists = false
    act(() => document.dispatchEvent(new Event('visibilitychange')))
    expect(await screen.findByText(en.t('detail.gone.title'))).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: en.t('confirm.label') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /send this exact text/ })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: en.t('detail.currentActions') }))
    expect(go).toHaveBeenCalledWith({ ...EMPTY_ROUTE, view: 'actions' })
    expect(apiCalls.filter((call) => call.method !== 'GET')).toHaveLength(0)
  })

  it('opens the current actions of a closed card\'s intent in that intent\'s own space', async () => {
    const base = actionCard()
    const card: ActionCard = {
      ...base, status: 'StateChanged', space: 'feature',
      intent: { ...base.intent, intent_key: 'feature~250901-guest' },
    }
    const go = vi.fn()
    routesFor(card)
    mount(card, go)
    await userEvent.click(await screen.findByRole('button', { name: en.t('detail.currentActions') }))
    expect(go).toHaveBeenCalledWith(expect.objectContaining({
      view: 'actions', repo: card.repo.repo_id, space: 'feature', intent: 'feature~250901-guest',
    }))
  })

  it('is shown where the click was, not only at the top of the pane', async () => {
    const card = actionCard()
    routesFor(card, {
      [`POST ${API_BASE}/actions/${card.action_id}/submit`]: () => {
        throw new StubApiError(409, {
          error: 'this conversation is busy',
          code: 'slot_busy',
          details: { reasons: ['running'] },
        })
      },
    })

    const panel = await openApproval(card)
    await userEvent.click(within(panel).getByRole('button', { name: new RegExp(en.t('confirm.send')) }))

    // The banner above the card body still says it (unchanged), and the confirmation says it where the user is
    // looking: on a long gate those two places are screens apart. A busy conversation passes when its turn ends,
    // so the send stays live; round 4 left a person with a dead button and Cancel as the only way out.
    await waitFor(() => expect(within(confirmation()).getByText(en.t('confirm.retry.slot_busy'))).toBeInTheDocument())
    expect(screen.getByText(en.t('errors.slot_busy'))).toBeInTheDocument()
    expect(within(confirmation()).getByRole('button', { name: new RegExp(en.t('confirm.send')) })).toBeEnabled()
    // Pressing it again is a second attempt, refused again here while the conversation stays busy.
    await userEvent.click(within(confirmation()).getByRole('button', { name: new RegExp(en.t('confirm.send')) }))
    await waitFor(() => expect(apiCalls.filter((call) => call.path.endsWith('/submit'))).toHaveLength(2))
  })

  it('does not offer the same bytes again after a refusal that will not pass', async () => {
    const card = actionCard()
    routesFor(card, {
      [`POST ${API_BASE}/actions/${card.action_id}/submit`]: () => {
        throw new StubApiError(400, { error: 'not a decision this card offers', code: 'invalid_decision' })
      },
    })
    const panel = await openApproval(card)
    await userEvent.click(within(panel).getByRole('button', { name: new RegExp(en.t('confirm.send')) }))
    await waitFor(() => expect(within(confirmation()).getByText(en.t('errors.invalid_decision'))).toBeInTheDocument())
    expect(within(confirmation()).getByRole('button', { name: new RegExp(en.t('confirm.send')) })).toBeDisabled()
  })
})

describe('the words a user typed, after a send', () => {
  const text = 'Cover the decline path and the 3DS retry.'
  const stored = (card: ActionCard) => localStorage.getItem(`${APP_NAME}:draft:${card.action_id}`)

  /** Send Request changes from a gate whose feedback is a stored draft; the delivery report answers `after`. */
  async function requestChanges(slots: unknown[], after: ActionCard) {
    const card = actionCard()
    let current = card
    localStorage.setItem(`${APP_NAME}:draft:${card.action_id}`, JSON.stringify({ feedback: text }))
    routesFor(card, {
      [`GET ${API_BASE}/actions/${card.action_id}`]: () => ({ action: current, transitions: [], drafts: [] }),
      [`POST ${API_BASE}/actions/${card.action_id}/submit`]: () => submitReceipt(card, `Request Changes: ${text}`),
      'GET /api/chat/slots': () => slots,
      'POST /api/chat?ws=1': () => ({ ok: true, slot: 'aidlc-r1-guest', mid: 'm_1' }),
      [`POST ${API_BASE}/actions/${card.action_id}/delivery`]: () => {
        current = after
        return { ok: true, action: after, idempotent: false }
      },
    })
    const view = mount(card)
    expect(((await screen.findByLabelText(en.t('template.gate.feedbackLabel'))) as HTMLTextAreaElement).value).toBe(text)
    await userEvent.click(await screen.findByRole('button', { name: /^Request changes/ }))
    await userEvent.click(within(confirmation()).getByRole('button', { name: new RegExp(en.t('confirm.send')) }))
    // The confirmation closes once the outcome is recorded: the draft has been kept or discarded by then.
    await waitFor(() => expect(screen.queryByRole('group', { name: en.t('confirm.label') })).not.toBeInTheDocument())
    expect(apiCalls.filter((call) => call.path.endsWith('/delivery'))).toHaveLength(1)
    return view
  }

  it('keeps them when the slot was gone and nothing was sent, because Request changes is offered again', async () => {
    const card = actionCard()
    const notDelivered: ActionCard = { ...card, status: 'NotDelivered', status_generation: 3,
      updated_at: '2026-09-04T09:05:00Z' }
    const view = await requestChanges([], notDelivered)
    expect(apiCalls.filter((call) => call.path === '/api/chat?ws=1')).toHaveLength(0)
    // Walk away and come back to the card: the box is filled from storage again.
    view.select(null, '')
    view.select(notDelivered)
    const box = await screen.findByLabelText(en.t('template.gate.feedbackLabel'))
    expect((box as HTMLTextAreaElement).value).toBe(text)
  })

  it('discards them once the conductor has them', async () => {
    const card = actionCard()
    const slot = { key: 'aidlc-r1-guest', project: card.repo.canonical_path, agent: 'aidlc', running: false }
    await requestChanges([slot], { ...card, status: 'Delivered', status_generation: 3,
      updated_at: '2026-09-04T09:05:00Z', decisions: [], primary: null })
    expect(apiCalls.filter((call) => call.path === '/api/chat?ws=1')).toHaveLength(1)
    expect(stored(card)).toBeNull()
  })
})

/** The card an uncertain delivery leaves: the uncertain vocabulary, not the gate buttons it was born with. */
function uncertainCard(card: ActionCard): ActionCard {
  return { ...card, status: 'ReconciliationRequired', queue_type: 'delivery_uncertain', status_generation: 3,
    updated_at: '2026-09-04T09:05:00Z',
    decisions: [decisionSpec('reconcile'), decisionSpec('mark_not_delivered'), decisionSpec('resubmit')],
    primary: { decision: 'reconcile', label_key: 'decision.reconcile.label' } }
}

const sendButton = () => within(confirmation()).getByRole('button', { name: /send this exact text/ })

describe('an open confirmation when the evidence moves underneath it', () => {
  const asserted = () => apiCalls.filter((call) => call.path.endsWith('/submit'))
    .map((call) => (call.body as SubmitRequest).captured.evidence_digest)

  it('sends the capture it was opened against, then the capture of the card a stale refusal showed', async () => {
    const card = actionCard()
    // The artifact was rewritten: the reconciler refreshed the open card in place and the queue poll brought it.
    const moved: ActionCard = { ...card, status_generation: 2, updated_at: '2026-09-04T09:01:00Z',
      captured: { ...card.captured, evidence_digest: 'sha-ev-rewritten', state_hash: 'sha-state-2' } }
    routesFor(card, {
      [`POST ${API_BASE}/actions/${card.action_id}/submit`]: (body) => {
        if ((body as SubmitRequest).captured.evidence_digest === moved.captured.evidence_digest) {
          throw new StubApiError(409, { code: 'slot_busy', error: 'this conversation is busy' })
        }
        throw new StubApiError(409, { code: 'action_stale', error: 'the boundary this decision was captured against has moved',
          details: { fields: ['evidence_digest', 'state_hash'], card: moved } })
      },
    })
    const view = mount(card)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    view.select(moved)

    await userEvent.click(sendButton())
    expect(asserted()).toEqual(['sha-ev'])
    expect(await screen.findByText(en.t('detail.staleRefused'))).toBeInTheDocument()
    // The refusal put the refreshed card in front of the person; confirming again confirms that card.
    await userEvent.click(sendButton())
    await waitFor(() => expect(asserted()).toEqual(['sha-ev', 'sha-ev-rewritten']))
    expect(apiCalls.filter((call) => call.path === SEND)).toHaveLength(0)
  })

  it('shows a second refreshed card of the same generation before Send asserts its capture', async () => {
    const card = actionCard()
    const review = (verdict: string) => ({ verdict, findings: [], reviewer: 'architecture-reviewer', review_class: null,
      iteration: 1 })
    // The stored row did not move between the reads (same generation and updated_at): only the live join did.
    const first: ActionCard = { ...card, captured: { ...card.captured, evidence_digest: 'sha-ev-1' },
      evidence: { ...card.evidence, review: review('NEEDS_WORK') } }
    const second: ActionCard = { ...card, captured: { ...card.captured, evidence_digest: 'sha-ev-2' },
      evidence: { ...card.evidence, review: review('READY') } }
    const stale = (refreshed: ActionCard) => new StubApiError(409, { code: 'action_stale', error: 'moved',
      details: { card: refreshed } })
    const shown: string[] = []
    routesFor(card, {
      [`POST ${API_BASE}/actions/${card.action_id}/submit`]: (body) => {
        const digest = (body as SubmitRequest).captured.evidence_digest
        if (digest === 'sha-ev') throw stale(first)
        if (digest === 'sha-ev-1') throw stale(second)
        // The review verdict the person was looking at when this Send left.
        const text = document.body.textContent ?? ''
        shown.push(['NEEDS_WORK', 'READY'].filter((verdict) => text.includes(verdict)).join())
        throw new StubApiError(409, { code: 'slot_busy', error: 'this conversation is busy' })
      },
    })
    mount(card)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    await userEvent.click(sendButton())
    expect(await screen.findByText('NEEDS_WORK')).toBeInTheDocument()
    await userEvent.click(sendButton())
    await waitFor(() => expect(asserted()).toEqual(['sha-ev', 'sha-ev-1']))
    expect(await screen.findByText('READY')).toBeInTheDocument()
    await userEvent.click(sendButton())
    await waitFor(() => expect(asserted()).toEqual(['sha-ev', 'sha-ev-1', 'sha-ev-2']))
    expect(shown).toEqual(['READY'])
  })
})

describe('the text the confirmation shows while the send is in flight', () => {
  it('is the text being sent, written by a box that stays locked until the server hands the decision back', async () => {
    const card = actionCard()
    let refuse!: (reason: unknown) => void
    localStorage.setItem(`${APP_NAME}:draft:${card.action_id}`, JSON.stringify({ feedback: 'Cover the decline path.' }))
    routesFor(card, {
      [`POST ${API_BASE}/actions/${card.action_id}/submit`]: () => new Promise((_, reject) => { refuse = reject }),
    })
    mount(card)
    await userEvent.click(await screen.findByRole('button', { name: /^Request changes/ }))
    await userEvent.click(sendButton())
    const box = screen.getByLabelText(en.t('template.gate.feedbackLabel'))
    try {
      const sent = (apiCalls.find((call) => call.path.endsWith('/submit'))?.body as SubmitRequest).client_wire_text
      expect(sent).toBe('Request Changes: Cover the decline path.')
      expect(box).toBeDisabled()
      expect(confirmation().querySelector('.studio-sendtext')?.textContent).toBe(sent)
    } finally {
      // Refused before anything moved: the words are the person's to change again.
      await act(async () => refuse(new StubApiError(409, { code: 'slot_busy', error: 'this conversation is busy' })))
    }
    expect(box).toBeEnabled()
  })

  it('stays on screen once the Delivering card, which offers nothing, is read, until the outcome is recorded', async () => {
    const card = actionCard()
    let answer!: (value: unknown) => void
    routesFor(card, {
      [`POST ${API_BASE}/actions/${card.action_id}/submit`]: () => submitReceipt(card, 'Approve'),
      [`POST ${SEND}`]: () => new Promise((resolve) => { answer = resolve }),
      [`POST ${API_BASE}/actions/${card.action_id}/delivery`]: () => ({ ok: true, idempotent: false,
        action: { ...card, status: 'Delivered', status_generation: 3, updated_at: '2026-09-04T09:06:00Z', decisions: [],
          primary: null } }),
    })
    const view = mount(card)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    await userEvent.click(sendButton())
    await waitFor(() => expect(apiCalls.some((call) => call.path === SEND)).toBe(true))
    // The queue poll (every 2 s while anything is live) brings the Delivering card.
    view.select({ ...card, status: 'Delivering', status_generation: 2, updated_at: '2026-09-04T09:05:00Z', decisions: [],
      primary: null })
    try {
      expect(screen.queryByRole('group', { name: en.t('confirm.label') })?.querySelector('.studio-sendtext')?.textContent)
        .toBe('Approve')
    } finally {
      await act(async () => { answer({ ok: true, slot: 'aidlc-r1-guest', mid: 'm_1' }) })
    }
    await waitFor(() => expect(screen.queryByRole('group', { name: en.t('confirm.label') })).toBeNull())
  })
})

describe('the bar after a send', () => {
  it('offers the decision again after a send Studio proved was never delivered, as the card does', async () => {
    const card = actionCard()
    const notDelivered: ActionCard = { ...card, status: 'NotDelivered', status_generation: 3,
      updated_at: '2026-09-04T09:05:00Z' }
    let current = card
    routesFor(card, {
      [`GET ${API_BASE}/actions/${card.action_id}`]: () => ({ action: current, transitions: [], drafts: [] }),
      [`POST ${API_BASE}/actions/${card.action_id}/submit`]: () => submitReceipt(card, 'Approve'),
      // The slot is gone: nothing is sent.
      'GET /api/chat/slots': () => [],
      [`POST ${API_BASE}/actions/${card.action_id}/delivery`]: () => {
        current = notDelivered
        return { ok: true, action: notDelivered, idempotent: false }
      },
    })
    const view = mount(card)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    await userEvent.click(sendButton())
    await waitFor(() => expect(screen.queryByRole('group', { name: en.t('confirm.label') })).not.toBeInTheDocument())
    expect(apiCalls.filter((call) => call.path === SEND)).toHaveLength(0)
    expect(screen.getByRole('button', { name: /^Approve/ })).toBeEnabled()
    // Walk away and back, as a person would.
    view.select(null, '')
    view.select(notDelivered)
    expect(await screen.findByRole('button', { name: /^Approve/ })).toBeEnabled()
  })

  it('offers the reconciliation decisions after an uncertain send', async () => {
    const card = actionCard()
    let current = card
    routesFor(card, {
      [`GET ${API_BASE}/actions/${card.action_id}`]: () => ({ action: current, transitions: [], drafts: [] }),
      [`POST ${API_BASE}/actions/${card.action_id}/submit`]: () => submitReceipt(card, 'Approve'),
      [`POST ${SEND}`]: () => { throw new StubApiError(502, { error: 'bad gateway' }) },
      [`POST ${API_BASE}/actions/${card.action_id}/delivery`]: () => {
        current = uncertainCard(card)
        return { ok: true, action: current, idempotent: false }
      },
    })
    mount(card)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    await userEvent.click(sendButton())
    expect(await screen.findByRole('button', { name: en.t('decision.mark_not_delivered.label') })).toBeEnabled()
    expect(screen.getByRole('button', { name: en.t('decision.reconcile.label') })).toBeEnabled()
    expect(apiCalls.filter((call) => call.path === SEND)).toHaveLength(1)
  })
})

describe('the bar after a studio-only decision', () => {
  it('keeps offering what the server still offers when the decision changed nothing', async () => {
    const card = uncertainCard(actionCard())
    routesFor(card, {
      // Reconcile from disk found nothing new, so the backend wrote nothing: the same card, the same generation.
      [`POST ${API_BASE}/actions/${card.action_id}/resolve`]: () => ({ ok: true, action: card, created_action_id: null }),
    })
    const view = mount(card)
    await userEvent.click(await screen.findByRole('button', { name: en.t('decision.reconcile.label') }))
    await userEvent.click(within(confirmation()).getByRole('button', { name: en.t('confirm.apply') }))
    await waitFor(() => expect(screen.queryByRole('group', { name: en.t('confirm.label') })).toBeNull())
    expect(apiCalls.filter((call) => call.path.endsWith('/resolve'))).toHaveLength(1)
    expect(screen.queryByText(en.t('detail.bar.submitted', { id: card.action_id }))).toBeNull()
    expect(screen.getByRole('button', { name: en.t('decision.mark_not_delivered.label') })).toBeEnabled()
    // Walk to the queue and back, as a person would.
    view.select(null, '')
    view.select(card)
    expect(await screen.findByRole('button', { name: en.t('decision.mark_not_delivered.label') })).toBeEnabled()
    expect(screen.queryByText(en.t('detail.bar.submitted', { id: card.action_id }))).toBeNull()
  })
})

describe('a studio-only decision the broker cannot prove undelivered yet', () => {
  /** Choose `decision`, tick the acknowledgement, apply, and let the broker refuse it with `details`. */
  async function refusedWith(decision: 'resubmit' | 'mark_not_delivered', details: Record<string, unknown>) {
    const card = uncertainCard(actionCard())
    routesFor(card, {
      [`POST ${API_BASE}/actions/${card.action_id}/resolve`]: () => {
        throw new StubApiError(409, { code: 'not_delivered_unproven', error: 'refused', details })
      },
    })
    mount(card)
    await userEvent.click(await screen.findByRole('button', { name: en.t(`decision.${decision}.label`) }))
    await userEvent.click(within(confirmation()).getByRole('checkbox'))
    await userEvent.click(within(confirmation()).getByRole('button', { name: en.t('confirm.apply') }))
    await waitFor(() => expect(apiCalls.some((call) => call.path.endsWith('/resolve'))).toBe(true))
    return card
  }

  it('says when Send it again opens, beside a button that stays live, while the first send may still be answered', async () => {
    const card = await refusedWith('resubmit',
      { reason: 'ack_deadline_pending', ack_deadline_at: '2026-09-04T09:06:30Z', action_id: 'a_1' })
    const note = en.t('confirm.retry.ackDeadlinePending', { at: at(en, '2026-09-04T09:06:30Z') })
    expect(await within(confirmation()).findByText(note)).toBeInTheDocument()
    expect(within(confirmation()).getByRole('button', { name: en.t('confirm.apply') })).toBeEnabled()
    expect(screen.queryByText(en.t('errors.not_delivered_unproven'))).toBeNull()
    expect(card.status).toBe('ReconciliationRequired')
  })

  it('keeps any other refusal of that code final', async () => {
    await refusedWith('mark_not_delivered', { checks: { row_absent: false } })
    expect(await within(confirmation()).findByText(en.t('errors.not_delivered_unproven'))).toBeInTheDocument()
    expect(within(confirmation()).getByRole('button', { name: en.t('confirm.apply') })).toBeDisabled()
  })
})

describe('the delivery report', () => {
  it('raises no alarm while it is on the wire, and the bar then says the decision was submitted', async () => {
    const card = actionCard()
    let finish!: (value: unknown) => void
    routesFor(card, {
      [`POST ${API_BASE}/actions/${card.action_id}/submit`]: () => submitReceipt(card, 'Approve'),
      [`POST ${SEND}`]: () => ({ ok: true, slot: 'aidlc-r1-guest', mid: 'm_1' }),
      [`POST ${API_BASE}/actions/${card.action_id}/delivery`]: () => new Promise((resolve) => { finish = resolve }),
    })
    mount(card)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    await userEvent.click(sendButton())
    await waitFor(() => expect(apiCalls.some((call) => call.path.endsWith('/delivery'))).toBe(true))
    try {
      expect(screen.queryByText(en.t('delivery.reportFailed'))).toBeNull()
    } finally {
      await act(async () => finish({ ok: true, idempotent: false, action: { ...card, status: 'Delivered',
        status_generation: 3, updated_at: '2026-09-04T09:05:00Z', decisions: [], primary: null } }))
    }
    expect(screen.queryByText(en.t('delivery.reportFailed'))).toBeNull()
    expect(screen.getByText(en.t('detail.bar.submitted', { id: card.action_id }))).toBeInTheDocument()
  })

  it('counts the backend refusing the report as its answer, and reads the card it recorded', async () => {
    const card = actionCard()
    let current = card
    routesFor(card, {
      [`GET ${API_BASE}/actions/${card.action_id}`]: () => ({ action: current, transitions: [], drafts: [] }),
      [`POST ${API_BASE}/actions/${card.action_id}/submit`]: () => submitReceipt(card, 'Approve'),
      // The host refuses the turn with its own 4xx: the message was never enqueued.
      [`POST ${SEND}`]: () => { throw new StubApiError(409, { error: 'slot is busy', code: 'turn_in_flight' }) },
      // Studio could not prove absence (the slot ran a turn), so it recorded ReconciliationRequired and says so.
      [`POST ${API_BASE}/actions/${card.action_id}/delivery`]: () => {
        current = uncertainCard(card)
        throw new StubApiError(409, { code: 'not_delivered_unproven', error: 'this decision cannot be proven undelivered',
          details: { checks: { row_absent: true, slot_idle: false, disk_baseline_unchanged: true, boot_unchanged: true } } })
      },
    })
    mount(card)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    await userEvent.click(sendButton())
    await waitFor(() => expect(screen.queryByRole('group', { name: en.t('confirm.label') })).not.toBeInTheDocument())
    expect(screen.queryByText(en.t('delivery.reportFailed'))).toBeNull()
    expect(await screen.findByRole('button', { name: en.t('decision.mark_not_delivered.label') })).toBeEnabled()
    expect(apiCalls.filter((call) => call.path.endsWith('/delivery'))).toHaveLength(1)
  })

  // submit.test.ts pins which statuses never reach the broker, so one is enough here.
  it('still says Studio could not record the outcome when the report never reached the broker (401)', async () => {
    const card = actionCard()
    routesFor(card, {
      [`POST ${API_BASE}/actions/${card.action_id}/submit`]: () => submitReceipt(card, 'Approve'),
      [`POST ${SEND}`]: () => ({ ok: true, slot: 'aidlc-r1-guest', mid: 'm_1' }),
      [`POST ${API_BASE}/actions/${card.action_id}/delivery`]: () => {
        throw new StubApiError(401, { error: 'authentication required', code: 'unauthorized' })
      },
    })
    mount(card)
    await userEvent.click(await screen.findByRole('button', { name: /^Approve/ }))
    await userEvent.click(sendButton())
    await waitFor(() => expect(screen.queryByRole('group', { name: en.t('confirm.label') })).toBeNull())
    expect(apiCalls.filter((call) => call.path === SEND)).toHaveLength(1)
    expect(screen.getByText(en.t('delivery.reportFailed')).closest('[role]')).toHaveAttribute('role', 'alert')
  })

  it('says Studio could not record the outcome once its retries are spent, and offers nothing to send again', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const card = actionCard()
      routesFor(card, {
        [`POST ${API_BASE}/actions/${card.action_id}/submit`]: () => submitReceipt(card, 'Approve'),
        [`POST ${SEND}`]: () => ({ ok: true, slot: 'aidlc-r1-guest', mid: 'm_1' }),
        [`POST ${API_BASE}/actions/${card.action_id}/delivery`]: () => {
          throw new StubApiError(503, { error: 'gateway restarting' })
        },
      })
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
      mount(card)
      await user.click(await screen.findByRole('button', { name: /^Approve/ }))
      await user.click(sendButton())
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
      expect(apiCalls.filter((call) => call.path.endsWith('/delivery'))).toHaveLength(4)
      expect(screen.getByText(en.t('delivery.reportFailed')).closest('[role]')).toHaveAttribute('role', 'alert')
      // The only card Studio has is the one from before the send: its decisions are not offered for a message the host has.
      expect(screen.queryByRole('button', { name: /^Approve/ })).toBeNull()
      expect(screen.getByText(en.t('detail.bar.submitted', { id: card.action_id }))).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it.each([
    ['the host refused the turn with its own 4xx', 'delivery.reportFailedRefused', 1, {
      [`POST ${SEND}`]: () => { throw new StubApiError(409, { error: 'slot is busy', code: 'turn_in_flight' }) },
    }],
    ['the host answered with a 502', 'delivery.reportFailedUncertain', 1, {
      [`POST ${SEND}`]: () => { throw new StubApiError(502, { error: 'bad gateway' }) },
    }],
    ['the slot was gone, so nothing was sent', 'delivery.reportFailedNotSent', 0, { 'GET /api/chat/slots': () => [] }],
    ['the slot listing failed too, so nothing was sent', 'delivery.reportFailedNotSent', 0, {
      'GET /api/chat/slots': () => { throw new StubApiError(503, { error: 'gateway restarting' }) },
    }],
  ] as const)('says what Studio could not record, never that KiroCrew has the message, when %s',
    async (_label, key, sent, host) => {
      vi.useFakeTimers({ shouldAdvanceTime: true })
      try {
        const card = actionCard()
        routesFor(card, {
          [`POST ${API_BASE}/actions/${card.action_id}/submit`]: () => submitReceipt(card, 'Approve'),
          ...host,
          [`POST ${API_BASE}/actions/${card.action_id}/delivery`]: () => {
            throw new StubApiError(503, { error: 'gateway restarting' })
          },
        })
        const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
        mount(card)
        await user.click(await screen.findByRole('button', { name: /^Approve/ }))
        await user.click(sendButton())
        await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
        expect(apiCalls.filter((call) => call.path.endsWith('/delivery'))).toHaveLength(4)
        expect(apiCalls.filter((call) => call.path === SEND)).toHaveLength(sent)
        expect(screen.getByText(en.t(key)).closest('[role]')).toHaveAttribute('role', 'alert')
        expect(screen.queryByText(en.t('delivery.reportFailed'))).toBeNull()
      } finally {
        vi.useRealTimers()
      }
    })
})

describe('a send the host answers only after Studio stopped waiting for it', () => {
  /** Approve and Send to a host that answers only when told to, then let Studio's 20 s timeout pass. */
  async function sendPastTheTimeout(more: Record<string, RouteHandler> = {}) {
    vi.setSystemTime(new Date('2026-09-04T10:00:00Z'))
    const card = actionCard()
    let current = card
    let answer!: (value: unknown) => void
    routesFor(card, {
      [`GET ${API_BASE}/actions/${card.action_id}`]: () => ({ action: current, transitions: [], drafts: [] }),
      // The ack deadline the backend sets, 90 s after the submit.
      [`POST ${API_BASE}/actions/${card.action_id}/submit`]: () => ({ ...submitReceipt(card, 'Approve'),
        expires_at: '2026-09-04T10:01:30Z' }),
      [`POST ${SEND}`]: () => new Promise((resolve) => { answer = resolve }),
      [`POST ${API_BASE}/actions/${card.action_id}/delivery`]: () => {
        current = uncertainCard(card)
        return { ok: true, action: current, idempotent: false }
      },
      ...more,
    })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const view = mount(card)
    await user.click(await screen.findByRole('button', { name: /^Approve/ }))
    await user.click(sendButton())
    await act(async () => { await vi.advanceTimersByTimeAsync(21_000) })
    return { view, user, answer: (value: unknown) => answer(value) }
  }
  const outcomes = () => apiCalls.filter((call) => call.path.endsWith('/delivery'))
    .map((call) => (call.body as { outcome: string }).outcome)
  const resubmit = () => screen.getByRole('button', { name: en.t('decision.resubmit.label') })
  /** The host takes the turn after all, and Studio reports it: nothing holds the card after this. */
  async function hostAnswers(answer: (value: unknown) => void) {
    await act(async () => { answer({ ok: true, slot: 'aidlc-r1-guest', mid: 'm_1' }) })
    await act(async () => { await vi.advanceTimersByTimeAsync(100) })
  }

  beforeEach(() => { vi.useFakeTimers({ shouldAdvanceTime: true }) })
  afterEach(() => { vi.useRealTimers() })

  it('offers nothing that could send it again until the host answers, then reports that answer', async () => {
    const { answer } = await sendPastTheTimeout()
    expect(outcomes()).toEqual(['uncertain'])
    for (const decision of ['resubmit', 'mark_not_delivered', 'reconcile']) {
      expect(screen.getByRole('button', { name: en.t(`decision.${decision}.label`) })).toBeDisabled()
    }
    expect(screen.getByText(en.t('detail.bar.awaitingHost'))).toBeInTheDocument()

    await hostAnswers(answer)
    // The late answer is reported under the same delivery; only then is the bar the server's again.
    expect(outcomes()).toEqual(['uncertain', 'delivered'])
    expect(resubmit()).toBeEnabled()
    expect(apiCalls.filter((call) => call.path === SEND)).toHaveLength(1)
    expect(apiCalls.some((call) => call.path.endsWith('/resolve'))).toBe(false)
  })

  it('opens the choices once the receipt’s delivery deadline passes with no answer', async () => {
    await sendPastTheTimeout()
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
    expect(resubmit()).toBeDisabled()
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(resubmit()).toBeEnabled()
    expect(screen.queryByText(en.t('detail.bar.awaitingHost'))).toBeNull()
    expect(outcomes()).toEqual(['uncertain'])
  })

  it('says Studio could not record the host’s late answer once that report’s retries are spent', async () => {
    const card = actionCard()
    let reported = 0
    const { answer } = await sendPastTheTimeout({
      [`POST ${API_BASE}/actions/${card.action_id}/delivery`]: () => {
        reported += 1
        if (reported > 1) throw new StubApiError(503, { error: 'gateway restarting' })
        return { ok: true, action: uncertainCard(card), idempotent: false }
      },
    })
    expect(screen.queryByText(en.t('delivery.reportFailed'))).toBeNull()

    await act(async () => { answer({ ok: true, slot: 'aidlc-r1-guest', mid: 'm_1' }) })
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(outcomes()).toEqual(['uncertain', 'delivered', 'delivered', 'delivered', 'delivered'])
    expect(screen.getByText(en.t('delivery.reportFailed')).closest('[role]')).toHaveAttribute('role', 'alert')
    expect(apiCalls.filter((call) => call.path === SEND)).toHaveLength(1)
  })

  it('leaves every other card open to a decision while it waits', async () => {
    const other = actionCard({ action_id: 'a_other' })
    const { view, user, answer } = await sendPastTheTimeout({
      [`GET ${API_BASE}/actions/${other.action_id}`]: () => ({ action: other, transitions: [], drafts: [] }),
      [`POST ${API_BASE}/actions/${other.action_id}/submit`]: () => {
        throw new StubApiError(409, { code: 'slot_busy', error: 'this conversation is busy' })
      },
    })
    try {
      view.select(other)
      const approve = await screen.findByRole('button', { name: /^Approve/ })
      expect(approve).toBeEnabled()
      expect(screen.queryByText(en.t('detail.bar.awaitingHost'))).toBeNull()
      await user.click(approve)
      await user.click(sendButton())
      await waitFor(() => expect(apiCalls.filter((call) =>
        call.path === `${API_BASE}/actions/${other.action_id}/submit`)).toHaveLength(1))

      view.select(uncertainCard(actionCard()))
      expect(resubmit()).toBeDisabled()
      expect(screen.getByText(en.t('detail.bar.awaitingHost'))).toBeInTheDocument()
    } finally {
      await hostAnswers(answer)
    }
    expect(resubmit()).toBeEnabled()
    expect(apiCalls.filter((call) => call.path === SEND)).toHaveLength(1)
  })

  it('stays locked, and says why, in a pane opened after the one that sent it closed', async () => {
    const { view, answer } = await sendPastTheTimeout()
    try {
      // Leaving the Action Center unmounts the pane; coming back mounts one that never sent anything.
      view.unmount()
      mount(uncertainCard(actionCard()))
      expect(await screen.findByRole('button', { name: en.t('decision.resubmit.label') })).toBeDisabled()
      for (const decision of ['mark_not_delivered', 'reconcile']) {
        expect(screen.getByRole('button', { name: en.t(`decision.${decision}.label`) })).toBeDisabled()
      }
      expect(screen.getByText(en.t('detail.bar.awaitingHost'))).toBeInTheDocument()
    } finally {
      await hostAnswers(answer)
    }
    expect(outcomes()).toEqual(['uncertain', 'delivered'])
    expect(resubmit()).toBeEnabled()
    expect(screen.queryByText(en.t('detail.bar.awaitingHost'))).toBeNull()
    expect(apiCalls.filter((call) => call.path === SEND)).toHaveLength(1)
  })
})

describe('a send proven never delivered that the host later said it took', () => {
  it('offers only what the server offers, and closing it says it is not resent', async () => {
    const card = confirmedLate()
    routesFor(card, {
      [`GET ${API_BASE}/actions/${card.action_id}`]: () => ({ action: card, drafts: [], transitions: [{
        from_status: 'NotDelivered', to_status: 'ReconciliationRequired', generation: 5, at: card.updated_at,
        reason: 'delivery_report_delivered', evidence: {} }] }),
    })
    mount(card)
    const bar = (await screen.findByRole('button', { name: en.t('decision.acknowledge.label') })).parentElement!
    expect(within(bar).getAllByRole('button').map((button) => button.textContent))
      .toEqual([en.t('decision.acknowledge.label'), en.t('decision.reconcile.label')])
    expect(screen.queryByText(en.t('enum.actionStatus.NotDelivered'))).toBeNull()
    await userEvent.click(within(bar).getByRole('button', { name: en.t('decision.acknowledge.label') }))
    expect(confirmation()).toHaveTextContent(en.t('confirm.title.acknowledgeUnrecorded'))
  })

  it('says a refused close was an additional attempt beside a delivery on record, never that nothing was sent', async () => {
    const card = confirmedLate()
    routesFor(card, {
      // The conversation is running, so the delivery cannot be closed yet.
      [`POST ${API_BASE}/actions/${card.action_id}/resolve`]: () => {
        throw new StubApiError(409, { code: 'slot_busy', error: 'this conversation is busy' })
      },
    })
    mount(card)
    await userEvent.click(await screen.findByRole('button', { name: en.t('decision.acknowledge.label') }))
    await userEvent.click(within(confirmation()).getByRole('button', { name: en.t('confirm.apply') }))
    expect(await screen.findByText(en.t('detail.additionalAttemptBlocked'))).toBeInTheDocument()
    expect(screen.queryByText(en.t('detail.nothingSent'))).toBeNull()
  })

  it('says to close it again, beside a live control, when Studio could not read the conversation', async () => {
    const card = confirmedLate()
    routesFor(card, {
      // The slot table could not be read, so Studio cannot tell whether the conversation is still acting on it.
      [`POST ${API_BASE}/actions/${card.action_id}/resolve`]: () => {
        throw new StubApiError(503, { code: 'host_unavailable',
          error: 'the conversation cannot be read, so it may still be acting on this decision' })
      },
    })
    mount(card)
    await userEvent.click(await screen.findByRole('button', { name: en.t('decision.acknowledge.label') }))
    await userEvent.click(within(confirmation()).getByRole('button', { name: en.t('confirm.apply') }))
    await screen.findAllByText(en.t('errors.host_unavailable'))

    expect(within(confirmation()).getByRole('button', { name: en.t('confirm.apply') })).toBeEnabled()
    expect(within(confirmation()).getByText(en.t('confirm.retry.host_unavailable'))).toBeInTheDocument()
    expect(within(confirmation()).queryByText(en.t('errors.host_unavailable'))).toBeNull()
    await userEvent.click(within(confirmation()).getByRole('button', { name: en.t('confirm.apply') }))
    await waitFor(() => expect(apiCalls.filter((call) => call.path.endsWith('/resolve'))).toHaveLength(2))
  })
})

describe('the acknowledgement a studio-only decision asks for', () => {
  it('lapses when a stale refusal shows delivery evidence other than the evidence it was given for', async () => {
    const card = { ...uncertainCard(actionCard()), status_generation: 1 }
    // The reconciler read the conversation again between the person's reading and their click.
    const reread: ActionCard = { ...card, status_generation: 2, updated_at: '2026-09-04T09:06:00Z',
      acknowledged_evidence_sha256: 'sha-ack-reread' }
    const read = (body: unknown) => (body as { payload: { acknowledged_evidence_sha256?: string } })
      .payload.acknowledged_evidence_sha256
    routesFor(card, {
      [`POST ${API_BASE}/actions/${card.action_id}/resolve`]: (body) => {
        if (read(body) === reread.acknowledged_evidence_sha256) {
          return { ok: true, created_action_id: 'a_resent', action: { ...reread, status: 'Failed', status_generation: 3,
            updated_at: '2026-09-04T09:07:00Z', decisions: [], primary: null } }
        }
        throw new StubApiError(409, { code: 'action_stale',
          error: 'the evidence changed since it was shown; read it again before resubmitting',
          details: { expected: reread.acknowledged_evidence_sha256, card: reread } })
      },
    })
    mount(card)
    await userEvent.click(await screen.findByRole('button', { name: en.t('decision.resubmit.label') }))
    await userEvent.click(within(confirmation()).getByRole('checkbox'))
    await userEvent.click(within(confirmation()).getByRole('button', { name: en.t('confirm.apply') }))
    expect(await screen.findByText(en.t('detail.staleRefused'))).toBeInTheDocument()
    expect(within(confirmation()).getByRole('checkbox')).not.toBeChecked()
    expect(within(confirmation()).getByRole('button', { name: en.t('confirm.apply') })).toBeDisabled()
    expect(within(confirmation()).getByText(en.t('confirm.blocked.acknowledge'))).toBeInTheDocument()
    // Ticked again for the evidence now shown, it applies, naming the evidence it read.
    await userEvent.click(within(confirmation()).getByRole('checkbox'))
    await userEvent.click(within(confirmation()).getByRole('button', { name: en.t('confirm.apply') }))
    await waitFor(() => expect(apiCalls.filter((call) => call.path.endsWith('/resolve')).map((call) => read(call.body)))
      .toEqual(['sha-ack', 'sha-ack-reread']))
  })
})

describe('why Studio moved a card', () => {
  it('says a retired recovery card cleared, also on records written before that reason existed', () => {
    expect(transitionReason(en, 'boundary_superseded', 'recovery')).toBe('what it reported no longer holds')
    expect(transitionReason(en, 'condition_cleared', 'recovery')).toBe('what it reported no longer holds')
    expect(transitionReason(en, 'boundary_superseded', 'gate')).toBe('AI-DLC moved on before it was answered')
    expect(transitionReason(en, 'card_replaced', 'recovery')).toBe('a newer card now reports it')
  })

  it('does not claim someone else replied when it only saw more prompts than it sent', () => {
    expect(transitionReason(en, 'human_presence_anomaly')).not.toMatch(/someone else/)
  })
})
