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
import { describe, expect, it, vi } from 'vitest'

import { I18nProvider, makeI18n } from '../i18n'
import { useStudioApi } from '../lib/api'
import { EMPTY_ROUTE, type Navigate } from '../lib/route'
import type { ActionCard } from '../lib/types'
import { API_BASE, actionCard } from '../test/fixtures'
import { apiCalls, setApiRoutes, StubApiError } from '../test/stubs/app-sdk'
import { DetailShell } from './DetailShell'
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

describe('a decision whose intent has no canonical conversation', () => {
  it('shows a missing repository as saved evidence with a recovery link, before offering approval', async () => {
    const base = unbound()
    const card = { ...base, repo: { ...base.repo, availability: 'unavailable' as const,
      availability_detail: 'path_missing' } }
    const go = vi.fn()
    setApiRoutes({ [`GET ${API_BASE}/actions/${card.action_id}`]: () => ({ action: card, transitions: [], drafts: [] }) })
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
    setApiRoutes({ [`GET ${API_BASE}/actions/${card.action_id}`]: () => ({ action: card, transitions: [], drafts: [] }) })

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

  it('offers the send once a matching conversation is bound', async () => {
    const card = actionCard()
    setApiRoutes({ [`GET ${API_BASE}/actions/${card.action_id}`]: () => ({ action: card, transitions: [], drafts: [] }) })

    const panel = await openApproval(card)
    expect(within(panel).queryByText(en.t('confirm.blocked.sessionUnbound'))).toBeNull()
    expect(within(panel).getByRole('button', { name: new RegExp(en.t('confirm.send')) })).toBeEnabled()
  })
})

describe('a refusal the server did return', () => {
  it('retires a confirmation when the queue advances beyond the cached detail', async () => {
    const card = actionCard()
    setApiRoutes({
      [`GET ${API_BASE}/actions/${card.action_id}`]: () => ({ action: card, transitions: [], drafts: [] }),
    })
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
    setApiRoutes({
      [`GET ${API_BASE}/actions/${first.action_id}`]: () => ({ action: first, transitions: [], drafts: [] }),
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

  it('ignores a late stale-card response while another action is loading', async () => {
    const first = actionCard()
    const second = actionCard({ action_id: 'a_loading' })
    let refuse!: (reason: unknown) => void
    let finishRead!: (value: unknown) => void
    const submission = new Promise((_, reject) => { refuse = reject })
    const read = new Promise((resolve) => { finishRead = resolve })
    setApiRoutes({
      [`GET ${API_BASE}/actions/${first.action_id}`]: () => ({ action: first, transitions: [], drafts: [] }),
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
    setApiRoutes({
      [`GET ${API_BASE}/actions/${first.action_id}`]: () => ({ action: first, transitions: [], drafts: [] }),
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
    setApiRoutes({
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
    setApiRoutes({
      [`GET ${API_BASE}/actions/${card.action_id}`]: () => ({ action: card, transitions: [], drafts: [] }),
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
    setApiRoutes({
      [`GET ${API_BASE}/actions/${card.action_id}`]: () => ({ action: card, transitions: [], drafts: [] }),
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
    setApiRoutes({
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

  it('is shown where the click was, not only at the top of the pane', async () => {
    const card = actionCard()
    setApiRoutes({
      [`GET ${API_BASE}/actions/${card.action_id}`]: () => ({ action: card, transitions: [], drafts: [] }),
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

    // The banner above the card body still says it (unchanged), and so does the confirmation: on a long
    // gate those two places are screens apart, and only one of them is where the user is looking.
    await waitFor(() => expect(within(confirmation()).getByText(en.t('errors.slot_busy'))).toBeInTheDocument())
    expect(screen.getAllByText(en.t('errors.slot_busy')).length).toBeGreaterThan(1)
    // And the same bytes against the same captured state are not offered again.
    expect(within(confirmation()).getByRole('button', { name: new RegExp(en.t('confirm.send')) })).toBeDisabled()
  })
})
