/**
 * The summary-confirmation switch: the person's own request to AI-DLC, never Studio's.
 *
 * What must hold: the exact text is shown before anything is sent, and only that text is sent, once, as a turn of
 * its own (never folded into the open confirmation's `Looks correct`); the control is disabled where the send would
 * be refused; and the engine's line is shown as AI-DLC wrote it, read-only.
 */

import { useState } from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { EMPTY_DRAFT, payloadFor, type ActionDraft } from '../actions/DetailShell'
import { wireTextFor } from '../actions/useSubmit'
import { I18nProvider, makeI18n } from '../i18n'
import { useStudioApi, StudioApiError, type StudioApi } from '../lib/api'
import { EMPTY_ROUTE } from '../lib/route'
import type { ActionCard, CeremonySetting, IntentSummary, QuestionsView, SlotView } from '../lib/types'
import { WIRE } from '../lib/wire.generated'
import { IntentSettingsPanel } from '../plan/IntentSettingsPanel'
import { QuestionsTemplate } from '../templates/QuestionsTemplate'
import { API_BASE, actionCard, decisionSpec } from '../test/fixtures'
import { apiCalls, setApiRoutes } from '../test/stubs/app-sdk'
import {
  SummaryConfirmationSwitch, cardSummarySwitchBlockedReason, summarySwitchBlockedReason, summarySwitchText,
} from './SummaryConfirmationSwitch'

const en = makeI18n('en-US')
const t = (key: string) => en.t(key)
const OFF = '/aidlc --summary-confirmation off'
const ON = '/aidlc --summary-confirmation on'
const CHAT = '/api/chat'
const ON_FROM_SCOPE: CeremonySetting = { value: 'on', source: 'from scope feature', line: 'on (from scope feature)' }
const OFF_BY_YOU: CeremonySetting = { value: 'off', source: 'set by you', line: 'off (set by you)' }

const INTENT = {
  repo_id: 'r_1', repo_label: 'checkout-web', space: 'default', intent_dir: '250901-guest',
  intent_key: 'default~250901-guest', uuid: null, slug: 'guest', scope: 'feature', registry_status: 'Running',
  title: null, operational_state: 'Idle',
  disk: {
    status: 'Running', lifecycle_phase: 'inception', current_stage: 'functional-design', next_stage: null,
    state_version: 8, total_stages: 33, completed: 7, revision_count: 0, parked_at: null, last_updated: null,
    summary_confirmation: ON_FROM_SCOPE,
  },
  counts: { total: 33, not_started: 20, in_progress: 1, awaiting_approval: 0, revising: 0, completed: 7, skipped: 4,
    done: 11, unknown: 0, planned: 0, planned_done: 0 },
  open_actions: 0, blocking_findings: 0, warn_findings: 0,
  session: { slot_key: 's1', session_key: 'ses_1', running: false, bound_at: '2026-09-05T07:00:00Z' },
  archived: false, paused: false, interrupted: false, last_activity_at: null, stable_boundary: null, unstable: false,
  is_active_cursor: true,
} as unknown as IntentSummary

const slot = (key: string): SlotView =>
  ({ key, project: '/work/checkout-web', agent: 'aidlc', running: false, title: '' }) as unknown as SlotView

function commandCard(value: 'on' | 'off'): ActionCard {
  return actionCard({
    action_id: 'a_sc', type: 'set_summary_confirmation', severity: 'info', priority_group: 4,
    headline: { key: 'action.set_summary_confirmation.headline', params: { stage: 'functional-design', value } },
    primary: { decision: 'set_summary_confirmation', label_key: 'decision.set_summary_confirmation.label' },
    decisions: [{ ...decisionSpec('set_summary_confirmation'), lane: 'human_lane',
      wire_text_template: value === 'off' ? OFF : ON }],
  })
}

function receipt(text: string) {
  return {
    ok: true as const, action_id: 'a_sc', status: 'Delivering' as const, lane: 'human_lane' as const,
    delivery_id: 'd_sc', slot_key: 's1', session_key: 'ses_1', wire_text: text, expires_at: '', lease_generation: 1,
    host: { method: 'POST', path: '/api/chat?ws=1', body: { slot: 's1', message: text } },
  }
}

function fakeApi(value: 'on' | 'off', over: Partial<Record<keyof StudioApi, unknown>> = {}) {
  const card = commandCard(value)
  return {
    summaryConfirmation: vi.fn(async () => ({ ok: true as const, action_id: 'a_sc', status: 'Queued' as const, action: card })),
    submitAction: vi.fn(async () => receipt(value === 'off' ? OFF : ON)),
    listSlots: vi.fn(async () => [slot('s1')]),
    sendToHost: vi.fn(async () => ({ ok: true })),
    reportDelivery: vi.fn(async () => ({ ok: true as const, action: { ...card, status: 'Delivered' }, idempotent: false })),
    ...over,
  } as unknown as StudioApi & Record<string, ReturnType<typeof vi.fn>>
}

function mountSwitch(api: StudioApi, props: Partial<Parameters<typeof SummaryConfirmationSwitch>[0]> = {}) {
  return render(
    <I18nProvider>
      <SummaryConfirmationSwitch api={api} repoId="r_1" intentKey="default~250901-guest" setting={ON_FROM_SCOPE}
        blockedReason={null} {...props} />
    </I18nProvider>,
  )
}

describe('the switch', () => {
  it('shows the engine’s line, then sends exactly the switch, once, only after the confirmation', async () => {
    const api = fakeApi('off')
    mountSwitch(api)
    expect(screen.getByText('on (from scope feature)')).toBeInTheDocument()
    expect(screen.getByText(en.t('summarySwitch.value.on'))).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: en.t('summarySwitch.action.off') }))
    const panel = screen.getByRole('group', { name: en.t('summarySwitch.confirm.off.title') })
    expect(within(panel).getByLabelText(en.t('summarySwitch.confirm.text'))).toHaveTextContent(OFF)
    // Nothing leaves before the person has seen the exact text.
    expect(api.summaryConfirmation).not.toHaveBeenCalled()
    expect(api.submitAction).not.toHaveBeenCalled()

    await userEvent.click(within(panel).getByRole('button', { name: `Send ${OFF}` }))
    expect(await screen.findByText(en.t('summarySwitch.status.sent.off', { text: OFF }))).toBeInTheDocument()
    expect(api.summaryConfirmation).toHaveBeenCalledWith('r_1', 'default~250901-guest', 'off')
    expect(api.submitAction).toHaveBeenCalledWith('a_sc', expect.objectContaining({
      payload: { decision: 'set_summary_confirmation', value: 'off' }, client_wire_text: OFF,
    }))
    expect(api.sendToHost).toHaveBeenCalledTimes(1)
    expect(api.sendToHost).toHaveBeenCalledWith('/api/chat?ws=1', { slot: 's1', message: OFF })
    expect(api.reportDelivery).toHaveBeenCalledWith('a_sc', expect.objectContaining({ outcome: 'delivered' }))
  })

  it('offers it back on once AI-DLC recorded it off', async () => {
    const api = fakeApi('on')
    mountSwitch(api, { setting: OFF_BY_YOU })
    expect(screen.getByText('off (set by you)')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: en.t('summarySwitch.action.off') })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: en.t('summarySwitch.action.on') }))
    const panel = screen.getByRole('group', { name: en.t('summarySwitch.confirm.on.title') })
    expect(within(panel).getByLabelText(en.t('summarySwitch.confirm.text'))).toHaveTextContent(ON)
    await userEvent.click(within(panel).getByRole('button', { name: `Send ${ON}` }))
    expect(await screen.findByText(en.t('summarySwitch.status.sent.on', { text: ON }))).toBeInTheDocument()
    expect(api.summaryConfirmation).toHaveBeenCalledWith('r_1', 'default~250901-guest', 'on')
    expect(api.sendToHost).toHaveBeenCalledWith('/api/chat?ws=1', { slot: 's1', message: ON })
  })

  it('says when AI-DLC has no line for the intent, and still offers to turn it off', () => {
    mountSwitch(fakeApi('off'), { setting: null })
    expect(screen.getByText(en.t('summarySwitch.value.unrecorded'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: en.t('summarySwitch.action.off') })).toBeEnabled()
  })

  it('cancels without sending anything', async () => {
    const api = fakeApi('off')
    mountSwitch(api)
    await userEvent.click(screen.getByRole('button', { name: en.t('summarySwitch.action.off') }))
    await userEvent.click(screen.getByRole('button', { name: en.t('common.cancel') }))
    expect(screen.queryByRole('group', { name: en.t('summarySwitch.confirm.off.title') })).toBeNull()
    expect(api.summaryConfirmation).not.toHaveBeenCalled()
    expect(api.sendToHost).not.toHaveBeenCalled()
  })

  it('is disabled, with the reason, where the send would be refused', async () => {
    const api = fakeApi('off')
    mountSwitch(api, { blockedReason: en.t('intents.run.disabledRunning') })
    const button = screen.getByRole('button', { name: en.t('summarySwitch.action.off') })
    expect(button).toBeDisabled()
    expect(screen.getByText(en.t('intents.run.disabledRunning'))).toBeInTheDocument()
    await userEvent.click(button)
    expect(screen.queryByRole('group', { name: en.t('summarySwitch.confirm.off.title') })).toBeNull()
    expect(api.summaryConfirmation).not.toHaveBeenCalled()
  })

  it('says, before sending, that an open confirmation stays open', async () => {
    mountSwitch(fakeApi('off'), { pending: true })
    await userEvent.click(screen.getByRole('button', { name: en.t('summarySwitch.action.off') }))
    expect(screen.getByText(en.t('summarySwitch.confirm.pending'))).toBeInTheDocument()
  })

  it('names a setting already in force instead of the bare code', async () => {
    const refused = new StudioApiError('invalid_decision', '', { reason: 'already_set', value: 'off' }, 400)
    const api = fakeApi('off', { summaryConfirmation: vi.fn(async () => { throw refused }) })
    mountSwitch(api)
    await userEvent.click(screen.getByRole('button', { name: en.t('summarySwitch.action.off') }))
    await userEvent.click(screen.getByRole('button', { name: `Send ${OFF}` }))
    expect(await screen.findByRole('alert')).toHaveTextContent(en.t('summarySwitch.already.off'))
    expect(api.sendToHost).not.toHaveBeenCalled()
  })
})

describe('the text and the payload', () => {
  it('are the pinned wire text and the card’s own value, nothing else', () => {
    expect([WIRE.SUMMARY_CONFIRMATION_OFF, WIRE.SUMMARY_CONFIRMATION_ON]).toEqual([OFF, ON])
    expect([summarySwitchText('off'), summarySwitchText('on')]).toEqual([OFF, ON])
    expect(wireTextFor({ decision: 'set_summary_confirmation', value: 'off' }, null, false)).toBe(OFF)
    expect(wireTextFor({ decision: 'set_summary_confirmation', value: 'on' }, null, false)).toBe(ON)
    expect(payloadFor('set_summary_confirmation', commandCard('on'), EMPTY_DRAFT)).toEqual(
      { decision: 'set_summary_confirmation', value: 'on' })
    const unnamed = { ...commandCard('off'), headline: { key: 'action.set_summary_confirmation.headline', params: {} } }
    expect(payloadFor('set_summary_confirmation', unnamed, EMPTY_DRAFT)).toBeNull()
  })
})

describe('where it would be refused', () => {
  it('follows the intent: archived, paused, unbound, running, finished', () => {
    expect(summarySwitchBlockedReason(INTENT, t)).toBeNull()
    expect(summarySwitchBlockedReason({ ...INTENT, archived: true }, t)).toBe(t('intents.run.disabledArchived'))
    expect(summarySwitchBlockedReason({ ...INTENT, paused: true }, t)).toBe(t('intents.run.disabledPaused'))
    expect(summarySwitchBlockedReason({ ...INTENT, session: null }, t)).toBe(t('intents.run.disabledUnbound'))
    expect(summarySwitchBlockedReason({ ...INTENT, session: { ...INTENT.session!, running: true } }, t))
      .toBe(t('intents.run.disabledRunning'))
    expect(summarySwitchBlockedReason({ ...INTENT, disk: { ...INTENT.disk, status: 'Completed' } }, t))
      .toBe(t('summarySwitch.blocked.completed'))
  })

  it('follows a card’s live session', () => {
    const card = actionCard({ type: 'question' })
    expect(cardSummarySwitchBlockedReason(card, t)).toBeNull()
    expect(cardSummarySwitchBlockedReason({ ...card, evidence: { ...card.evidence, session: null } }, t))
      .toBe(t('intents.run.disabledUnbound'))
    const running = { ...card, evidence: { ...card.evidence, session: { ...card.evidence.session!, running: true } } }
    expect(cardSummarySwitchBlockedReason(running, t)).toBe(t('intents.run.disabledRunning'))
  })
})

// --- on the summary confirmation itself ---

const summaryView = (over: Partial<QuestionsView> = {}): QuestionsView => ({
  relpath: 'aidlc/spaces/default/intents/250901-guest/inception/functional-design/functional-design-questions.md',
  sha256: 'sha-q', stage: 'functional-design', unit: null, questions: [],
  summary_confirmation: { kind: 'summary_confirmation', present: true, answered: false, answer: null,
    options: ['Looks correct', 'Request changes'] },
  plan_approval: null, pending_count: 0, pending_checkpoint: 'summary_confirmation', mode: 'structured',
  host_card: null, ...over,
} as QuestionsView)

function summaryCard(over: Partial<ActionCard> = {}, setting: CeremonySetting | null = ON_FROM_SCOPE): ActionCard {
  const base = actionCard({
    action_id: 'a_summary', type: 'question',
    decisions: [{ ...decisionSpec('confirm_summary', ['confirm']), lane: 'human_lane', wire_text_template: 'Looks correct' }],
  })
  return {
    ...base,
    evidence: { ...base.evidence, state: { ...base.evidence.state!, summary_confirmation: setting },
      questions: summaryView() },
    ...over,
  }
}

function mountTemplate(card: ActionCard) {
  function Harness() {
    const api = useStudioApi()
    const [draft, setDraft] = useState<ActionDraft>(EMPTY_DRAFT)
    return (
      <QuestionsTemplate card={card} detail={{ action: card, transitions: [], drafts: [] }} draft={draft}
        setDraft={(patch) => setDraft((value) => ({ ...value, ...patch }))} refreshing={false} api={api}
        route={{ ...EMPTY_ROUTE, action: card.action_id }} go={() => {}} reload={() => {}} />
    )
  }
  return render(<I18nProvider><Harness /></I18nProvider>)
}

describe('on an open summary confirmation', () => {
  const INTENT_PATH = `${API_BASE}/repos/r_1/intents/default~250901-guest`

  it('sends the switch as its own turn and never touches the confirmation’s reply', async () => {
    const card = commandCard('off')
    setApiRoutes({
      [`POST ${INTENT_PATH}/summary-confirmation`]: () => ({ ok: true, action_id: 'a_sc', status: 'Queued', action: card }),
      [`POST ${API_BASE}/actions/a_sc/submit`]: () => receipt(OFF),
      [`GET ${CHAT}/slots`]: () => [slot('s1')],
      [`POST ${CHAT}?ws=1`]: () => ({ ok: true, slot: 's1' }),
      [`POST ${API_BASE}/actions/a_sc/delivery`]: () => ({ ok: true, action: { ...card, status: 'Delivered' }, idempotent: false }),
    })
    mountTemplate(summaryCard())
    await userEvent.click(screen.getByRole('button', { name: en.t('summarySwitch.action.off') }))
    expect(screen.getByText(en.t('summarySwitch.confirm.pending'))).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: `Send ${OFF}` }))
    expect(await screen.findByText(en.t('summarySwitch.status.sent.off', { text: OFF }))).toBeInTheDocument()

    expect(apiCalls.map((call) => `${call.method} ${call.path}`)).toEqual([
      `POST ${INTENT_PATH}/summary-confirmation`,
      `POST ${API_BASE}/actions/a_sc/submit`,
      `GET ${CHAT}/slots`,
      `POST ${CHAT}?ws=1`,
      `POST ${API_BASE}/actions/a_sc/delivery`,
    ])
    expect(apiCalls[0]?.body).toEqual({ value: 'off' })
    // The one message is the switch alone; the confirmation card was never submitted.
    expect(apiCalls.filter((call) => call.path === `${CHAT}?ws=1`).map((call) => call.body))
      .toEqual([{ slot: 's1', message: OFF }])
    expect(apiCalls.some((call) => call.path.includes('/actions/a_summary/'))).toBe(false)
  })

  it('says, once AI-DLC recorded it off, that this one still waits for an answer', () => {
    setApiRoutes({})
    mountTemplate(summaryCard({}, OFF_BY_YOU))
    expect(screen.getByText('off (set by you)')).toBeInTheDocument()
    expect(screen.getByText(en.t('summarySwitch.explain.offPending'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: en.t('summarySwitch.action.on') })).toBeEnabled()
  })

  it('is disabled while the conversation runs', () => {
    setApiRoutes({})
    const card = summaryCard()
    mountTemplate({ ...card, evidence: { ...card.evidence, session: { ...card.evidence.session!, running: true } } })
    expect(screen.getByRole('button', { name: en.t('summarySwitch.action.off') })).toBeDisabled()
  })

  it('is absent from a confirmation already sent, and from other checkpoints', () => {
    setApiRoutes({})
    const sent = mountTemplate(summaryCard({ status: 'Delivering' }))
    expect(screen.queryByRole('button', { name: en.t('summarySwitch.action.off') })).toBeNull()
    sent.unmount()
    const card = summaryCard()
    mountTemplate({ ...card, evidence: { ...card.evidence, questions: summaryView({
      summary_confirmation: null, pending_checkpoint: 'plan_approval',
      plan_approval: { kind: 'plan_approval', present: true, answered: false, answer: null,
        options: ['Approve Plan', 'Request Changes'] },
    }) } })
    expect(screen.queryByRole('button', { name: en.t('summarySwitch.action.off') })).toBeNull()
  })
})

describe('in the intent settings panel', () => {
  it('shows the control the view hands it, beside the scope and depth', async () => {
    const api = {
      intentSettingsPreview: vi.fn(async () => ({
        proposal: { before: { scope: 'feature', depth: 'Standard', test_strategy: 'Standard' },
          after: { scope: 'feature', depth: 'Standard', test_strategy: 'Standard' }, scopes: ['feature'], stages: [],
          allowed: false, refusals: ['no_changes'], argv_preview: [], selection: { space: 'default', intent_dir: '250901-guest' } },
        proposal_digest: 'd',
      })),
      changeIntentSettings: vi.fn(),
    }
    render(
      <I18nProvider>
        <IntentSettingsPanel api={api as never} repoId="r_1" intentKey="default~250901-guest"
          summaryConfirmation={<SummaryConfirmationSwitch api={fakeApi('off')} repoId="r_1"
            intentKey="default~250901-guest" setting={INTENT.disk.summary_confirmation}
            blockedReason={summarySwitchBlockedReason(INTENT, t)} />} />
      </I18nProvider>,
    )
    const section = screen.getAllByLabelText(en.t('summarySwitch.title'))[0]!
    expect(within(section).getByText('on (from scope feature)')).toBeInTheDocument()
    expect(within(section).getByRole('button', { name: en.t('summarySwitch.action.off') })).toBeEnabled()
    expect(await screen.findByLabelText(en.t('workspace.settings.scope'))).toBeInTheDocument()
  })
})
