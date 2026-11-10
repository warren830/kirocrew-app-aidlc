/**
 * The learnings switch: the person's own request to AI-DLC to stop (or resume) its learnings ritual, never Studio's.
 *
 * What must hold, as for the summary-confirmation switch: the exact text is shown before anything is sent, and only that
 * text is sent, once, as a turn of its own (never folded into an open learnings question's reply); the control is
 * disabled where the send would be refused; and the engine's line is shown as AI-DLC wrote it, read-only.
 */

import { useState } from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { EMPTY_DRAFT, payloadFor, type ActionDraft } from '../actions/DetailShell'
import { wireTextFor } from '../actions/useSubmit'
import { I18nProvider, makeI18n } from '../i18n'
import { useStudioApi, StudioApiError, type StudioApi } from '../lib/api'
import { LEARNINGS_ADD_PROMPTS, LEARNINGS_CORRECTION_CHOICES, LEARNINGS_KEEP_NONE_LABEL } from '../lib/enums.generated'
import { EMPTY_ROUTE } from '../lib/route'
import type { ActionCard, CeremonySetting, IntentSummary, Question, QuestionsView, SlotView } from '../lib/types'
import { WIRE } from '../lib/wire.generated'
import { IntentSettingsPanel } from '../plan/IntentSettingsPanel'
import { QuestionsTemplate } from '../templates/QuestionsTemplate'
import { API_BASE, actionCard, decisionSpec, submitReceipt } from '../test/fixtures'
import { apiCalls, setApiRoutes } from '../test/stubs/app-sdk'
import {
  LearningsSwitch, cardLearningsSwitchBlockedReason, isLearningsCorrectionQuestion, isLearningsQuestion,
  learningsSwitchBlockedReason,
  learningsSwitchText, openLearningsQuestions,
} from './LearningsSwitch'
import { SummaryConfirmationSwitch, summarySwitchBlockedReason } from './SummaryConfirmationSwitch'

const en = makeI18n('en-US')
const t = (key: string) => en.t(key)
const OFF = '/aidlc --learnings off'
const ON = '/aidlc --learnings on'
const CHAT = '/api/chat'
const ON_FROM_SCOPE: CeremonySetting = { value: 'on', source: 'from scope feature', line: 'on (from scope feature)' }
const OFF_BY_COMMAND: CeremonySetting = { value: 'off', source: 'set by a command', line: 'off (set by a command)' }

const INTENT = {
  repo_id: 'r_1', repo_label: 'checkout-web', space: 'default', intent_dir: '250901-guest',
  intent_key: 'default~250901-guest', uuid: null, slug: 'guest', scope: 'feature', registry_status: 'Running',
  title: null, operational_state: 'Idle',
  disk: {
    status: 'Running', lifecycle_phase: 'inception', current_stage: 'functional-design', next_stage: null,
    state_version: 8, total_stages: 33, completed: 7, revision_count: 0, parked_at: null, last_updated: null,
    summary_confirmation: { value: 'off', source: 'set by you', line: 'off (set by you)' }, learnings: ON_FROM_SCOPE,
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
    action_id: 'a_ls', type: 'set_learnings', severity: 'info', priority_group: 4,
    headline: { key: 'action.set_learnings.headline', params: { stage: 'functional-design', value } },
    primary: { decision: 'set_learnings', label_key: 'decision.set_learnings.label' },
    decisions: [{ ...decisionSpec('set_learnings'), lane: 'human_lane', wire_text_template: value === 'off' ? OFF : ON }],
  })
}

function fakeApi(value: 'on' | 'off', over: Partial<Record<keyof StudioApi, unknown>> = {}) {
  const card = commandCard(value)
  return {
    learnings: vi.fn(async () => ({ ok: true as const, action_id: 'a_ls', status: 'Queued' as const, action: card })),
    summaryConfirmation: vi.fn(),
    submitAction: vi.fn(async () => submitReceipt(card, value === 'off' ? OFF : ON, 's1')),
    listSlots: vi.fn(async () => [slot('s1')]),
    sendToHost: vi.fn(async () => ({ ok: true })),
    reportDelivery: vi.fn(async () => ({ ok: true as const, action: { ...card, status: 'Delivered' }, idempotent: false })),
    ...over,
  } as unknown as StudioApi & Record<string, ReturnType<typeof vi.fn>>
}

function mountSwitch(api: StudioApi, props: Partial<Parameters<typeof LearningsSwitch>[0]> = {}) {
  return render(
    <I18nProvider>
      <LearningsSwitch api={api} repoId="r_1" intentKey="default~250901-guest" setting={ON_FROM_SCOPE}
        blockedReason={null} {...props} />
    </I18nProvider>,
  )
}

describe('the switch', () => {
  it('shows the engine’s line, then sends exactly the switch, once, only after the confirmation', async () => {
    const api = fakeApi('off')
    mountSwitch(api)
    expect(screen.getByText('on (from scope feature)')).toBeInTheDocument()
    expect(screen.getByText(en.t('learningsSwitch.value.on'))).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: en.t('learningsSwitch.action.off') }))
    const panel = screen.getByRole('group', { name: en.t('learningsSwitch.confirm.off.title') })
    expect(within(panel).getByLabelText(en.t('learningsSwitch.confirm.text'))).toHaveTextContent(OFF)
    // Nothing leaves before the person has seen the exact text.
    expect(api.learnings).not.toHaveBeenCalled()
    expect(api.submitAction).not.toHaveBeenCalled()

    await userEvent.click(within(panel).getByRole('button', { name: `Send ${OFF}` }))
    expect(await screen.findByText(en.t('learningsSwitch.status.sent.off', { text: OFF }))).toBeInTheDocument()
    expect(api.learnings).toHaveBeenCalledWith('r_1', 'default~250901-guest', 'off')
    expect(api.summaryConfirmation).not.toHaveBeenCalled()
    expect(api.submitAction).toHaveBeenCalledWith('a_ls', expect.objectContaining({
      payload: { decision: 'set_learnings', value: 'off' }, client_wire_text: OFF,
    }))
    expect(api.sendToHost).toHaveBeenCalledTimes(1)
    expect(api.sendToHost).toHaveBeenCalledWith('/api/chat?ws=1', { slot: 's1', message: OFF })
    expect(api.reportDelivery).toHaveBeenCalledWith('a_ls', expect.objectContaining({ outcome: 'delivered' }))
  })

  it('offers it back on once AI-DLC recorded it off', async () => {
    const api = fakeApi('on')
    mountSwitch(api, { setting: OFF_BY_COMMAND })
    expect(screen.getByText('off (set by a command)')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: en.t('learningsSwitch.action.off') })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: en.t('learningsSwitch.action.on') }))
    const panel = screen.getByRole('group', { name: en.t('learningsSwitch.confirm.on.title') })
    expect(within(panel).getByLabelText(en.t('learningsSwitch.confirm.text'))).toHaveTextContent(ON)
    await userEvent.click(within(panel).getByRole('button', { name: `Send ${ON}` }))
    expect(await screen.findByText(en.t('learningsSwitch.status.sent.on', { text: ON }))).toBeInTheDocument()
    expect(api.learnings).toHaveBeenCalledWith('r_1', 'default~250901-guest', 'on')
    expect(api.sendToHost).toHaveBeenCalledWith('/api/chat?ws=1', { slot: 's1', message: ON })
  })

  it('says when AI-DLC has no line for the intent, and still offers to turn it off', () => {
    mountSwitch(fakeApi('off'), { setting: null })
    expect(screen.getByText(en.t('learningsSwitch.value.unrecorded'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: en.t('learningsSwitch.action.off') })).toBeEnabled()
  })

  it('cancels without sending anything', async () => {
    const api = fakeApi('off')
    mountSwitch(api)
    await userEvent.click(screen.getByRole('button', { name: en.t('learningsSwitch.action.off') }))
    await userEvent.click(screen.getByRole('button', { name: en.t('common.cancel') }))
    expect(screen.queryByRole('group', { name: en.t('learningsSwitch.confirm.off.title') })).toBeNull()
    expect(api.learnings).not.toHaveBeenCalled()
    expect(api.sendToHost).not.toHaveBeenCalled()
  })

  it('is disabled, with the reason, where the send would be refused', async () => {
    const api = fakeApi('off')
    mountSwitch(api, { blockedReason: en.t('intents.run.disabledRunning') })
    const button = screen.getByRole('button', { name: en.t('learningsSwitch.action.off') })
    expect(button).toBeDisabled()
    expect(screen.getByText(en.t('intents.run.disabledRunning'))).toBeInTheDocument()
    await userEvent.click(button)
    expect(screen.queryByRole('group', { name: en.t('learningsSwitch.confirm.off.title') })).toBeNull()
    expect(api.learnings).not.toHaveBeenCalled()
  })

  it('says, before sending, that the questions open now stay open', async () => {
    mountSwitch(fakeApi('off'), { pending: true })
    await userEvent.click(screen.getByRole('button', { name: en.t('learningsSwitch.action.off') }))
    expect(screen.getByText(en.t('learningsSwitch.confirm.pending'))).toBeInTheDocument()
  })

  it('names a setting already in force instead of the bare code', async () => {
    const refused = new StudioApiError('invalid_decision', '', { reason: 'already_set', value: 'off' }, 400)
    const api = fakeApi('off', { learnings: vi.fn(async () => { throw refused }) })
    mountSwitch(api)
    await userEvent.click(screen.getByRole('button', { name: en.t('learningsSwitch.action.off') }))
    await userEvent.click(screen.getByRole('button', { name: `Send ${OFF}` }))
    expect(await screen.findByRole('alert')).toHaveTextContent(en.t('learningsSwitch.already.off'))
    expect(api.sendToHost).not.toHaveBeenCalled()
  })

  it('says it cannot confirm the switch, never that it was not sent, when Studio could not prove the refusal', async () => {
    const api = fakeApi('off', {
      // The host's own 4xx, which reads as not delivered.
      sendToHost: vi.fn(async () => {
        throw new StudioApiError('internal_error', 'slot is busy', {}, 409, { error: 'slot is busy', code: 'turn_in_flight' })
      }),
      // The slot ran a turn since, so the broker cannot prove it and recorded the send as uncertain.
      reportDelivery: vi.fn(async () => {
        throw new StudioApiError('not_delivered_unproven', 'this decision cannot be proven undelivered', {}, 409)
      }),
    })
    mountSwitch(api)
    await userEvent.click(screen.getByRole('button', { name: en.t('learningsSwitch.action.off') }))
    await userEvent.click(screen.getByRole('button', { name: `Send ${OFF}` }))
    expect(await screen.findByText(en.t('learningsSwitch.status.uncertain', { text: OFF, action: 'a_ls' })))
      .toBeInTheDocument()
    expect(screen.queryByText(/was not sent/)).toBeNull()
    expect(api.sendToHost).toHaveBeenCalledTimes(1)
  })
})

describe('the text and the payload', () => {
  it('are the pinned wire text and the card’s own value, nothing else', () => {
    expect([WIRE.LEARNINGS_OFF, WIRE.LEARNINGS_ON]).toEqual([OFF, ON])
    expect([learningsSwitchText('off'), learningsSwitchText('on')]).toEqual([OFF, ON])
    expect(wireTextFor({ decision: 'set_learnings', value: 'off' }, null, false)).toBe(OFF)
    expect(wireTextFor({ decision: 'set_learnings', value: 'on' }, null, false)).toBe(ON)
    expect(payloadFor('set_learnings', commandCard('on'), EMPTY_DRAFT)).toEqual({ decision: 'set_learnings', value: 'on' })
    const unnamed = { ...commandCard('off'), headline: { key: 'action.set_learnings.headline', params: {} } }
    expect(payloadFor('set_learnings', unnamed, EMPTY_DRAFT)).toBeNull()
  })
})

describe('where it would be refused', () => {
  it('follows the intent: archived, paused, unbound, running, finished', () => {
    expect(learningsSwitchBlockedReason(INTENT, t)).toBeNull()
    expect(learningsSwitchBlockedReason({ ...INTENT, archived: true }, t)).toBe(t('intents.run.disabledArchived'))
    expect(learningsSwitchBlockedReason({ ...INTENT, paused: true }, t)).toBe(t('intents.run.disabledPaused'))
    expect(learningsSwitchBlockedReason({ ...INTENT, session: null }, t)).toBe(t('intents.run.disabledUnbound'))
    expect(learningsSwitchBlockedReason({ ...INTENT, session: { ...INTENT.session!, running: true } }, t))
      .toBe(t('intents.run.disabledRunning'))
    expect(learningsSwitchBlockedReason({ ...INTENT, disk: { ...INTENT.disk, status: 'Completed' } }, t))
      .toBe(t('learningsSwitch.blocked.completed'))
  })

  it('follows a card’s live session', () => {
    const card = actionCard({ type: 'question' })
    expect(cardLearningsSwitchBlockedReason(card, t)).toBeNull()
    expect(cardLearningsSwitchBlockedReason({ ...card, evidence: { ...card.evidence, session: null } }, t))
      .toBe(t('intents.run.disabledUnbound'))
  })
})

// --- on a learnings question ---

const option = (letter: string, text: string, is_other = false) => ({ letter, text, is_other })

const question = (over: Partial<Question> = {}): Question => ({
  index: 1, prompt: LEARNINGS_ADD_PROMPTS[0]!, options: [option('A', 'Nothing to add'), option('B', 'Add a note'),
    option('C', 'Other', true)],
  multi_select: false, answer: null, answered: false, required: true, ...over,
})

const keepQuestion = (over: Partial<Question> = {}): Question => question({
  index: 1, prompt: 'Which notes should I keep for next time?', multi_select: true,
  options: [option('A', LEARNINGS_KEEP_NONE_LABEL), option('B', 'Reused the auth module')], ...over,
})

const auditView = (questions: Question[]): QuestionsView => ({
  relpath: 'aidlc/spaces/default/intents/250901-guest/audit/000.md', sha256: 'sha-q', stage: 'functional-design',
  unit: null, questions, summary_confirmation: null, plan_approval: null, pending_count: questions.length,
  pending_checkpoint: null, mode: 'structured', host_card: null,
  origin: { kind: 'audit', event: 'DECISION_RECORDED', shard: '000.md', pos: 42, timestamp: '2026-09-10T08:00:00Z',
    stage: 'functional-design', unit: null, workflow: null, attempt_generation: null, decision_sha256: 'decision-hash',
    batch_size: questions.length },
})

function learningsCard(questions: Question[] = [question()], over: Partial<ActionCard> = {},
  setting: CeremonySetting | null = ON_FROM_SCOPE): ActionCard {
  const base = actionCard({
    action_id: 'a_learn', type: 'question',
    decisions: [{ ...decisionSpec('answers', ['answers']), lane: 'human_lane', wire_text_template: null }],
  })
  return {
    ...base,
    evidence: { ...base.evidence, state: { ...base.evidence.state!, learnings: setting }, questions: auditView(questions) },
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

describe('which questions are the ritual’s', () => {
  it('reads the engine’s own words: the add prompt, the keep question’s first option, a diary candidate', () => {
    for (const prompt of LEARNINGS_ADD_PROMPTS) expect(isLearningsQuestion(question({ prompt }))).toBe(true)
    expect(isLearningsQuestion(question({ prompt: '  learnings: ANYTHING to add?  ' }))).toBe(true)
    expect(isLearningsQuestion(keepQuestion())).toBe(true)
    expect(isLearningsQuestion(question({ prompt: 'Keep c1/c2?', options: [option('A', 'c1')],
      learning_candidates: [{ id: 'c1', heading: 'Tradeoffs', summary: 'Picked JSON', context: '' }] }))).toBe(true)
    expect(isLearningsQuestion(question({ prompt: 'Which store?', options: [option('A', 'SQLite')] }))).toBe(false)
    expect(isLearningsQuestion(question({ prompt: 'Anything else?', options: [option('A', 'Keep none')] }))).toBe(false)
  })

  it('reads 2.11.2’s question about one correction by its ending and its exact three choices', () => {
    const correction = (prompt: string, labels: readonly string[] = LEARNINGS_CORRECTION_CHOICES) => question({
      prompt, options: labels.map((label, i) => option(String.fromCharCode(65 + i), label)),
    })
    const asked = 'You asked me to name test files after the module they test. Keep that for next time?'
    expect(isLearningsCorrectionQuestion(correction(asked))).toBe(true)
    expect(isLearningsQuestion(correction(asked))).toBe(true)
    expect(isLearningsCorrectionQuestion(correction('Keep that for next time?'))).toBe(false)
    expect(isLearningsCorrectionQuestion(correction(asked, ['Yes', 'No']))).toBe(false)
    expect(isLearningsQuestion(correction('Should the list keep its order?', ['Yes, for this project', 'No'])))
      .toBe(false)
  })

  it('counts only a question still open', () => {
    expect(openLearningsQuestions(learningsCard())).toBe(true)
    expect(openLearningsQuestions(learningsCard([question({ answered: true, answer: 'Nothing to add' })]))).toBe(false)
    expect(openLearningsQuestions(learningsCard([question({ prompt: 'Which store?', options: [option('A', 'SQLite')] })])))
      .toBe(false)
  })
})

describe('on an open learnings question', () => {
  const INTENT_PATH = `${API_BASE}/repos/r_1/intents/default~250901-guest`

  it('sends the switch as its own turn and never touches the question’s reply', async () => {
    const card = commandCard('off')
    setApiRoutes({
      [`POST ${INTENT_PATH}/learnings`]: () => ({ ok: true, action_id: 'a_ls', status: 'Queued', action: card }),
      [`POST ${API_BASE}/actions/a_ls/submit`]: () => submitReceipt(card, OFF, 's1'),
      [`GET ${CHAT}/slots`]: () => [slot('s1')],
      [`POST ${CHAT}?ws=1`]: () => ({ ok: true, slot: 's1' }),
      [`POST ${API_BASE}/actions/a_ls/delivery`]: () => ({ ok: true, action: { ...card, status: 'Delivered' }, idempotent: false }),
    })
    mountTemplate(learningsCard([keepQuestion(), question({ index: 2 })]))
    await userEvent.click(screen.getByRole('button', { name: en.t('learningsSwitch.action.off') }))
    expect(screen.getByText(en.t('learningsSwitch.confirm.pending'))).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: `Send ${OFF}` }))
    expect(await screen.findByText(en.t('learningsSwitch.status.sent.off', { text: OFF }))).toBeInTheDocument()

    expect(apiCalls.map((call) => `${call.method} ${call.path}`)).toEqual([
      `POST ${INTENT_PATH}/learnings`,
      `POST ${API_BASE}/actions/a_ls/submit`,
      `GET ${CHAT}/slots`,
      `POST ${CHAT}?ws=1`,
      `POST ${API_BASE}/actions/a_ls/delivery`,
    ])
    expect(apiCalls[0]?.body).toEqual({ value: 'off' })
    // The one message is the switch alone; the question card was never submitted.
    expect(apiCalls.filter((call) => call.path === `${CHAT}?ws=1`).map((call) => call.body))
      .toEqual([{ slot: 's1', message: OFF }])
    expect(apiCalls.some((call) => call.path.includes('/actions/a_learn/'))).toBe(false)
  })

  it('is offered once on the card, beside the questions it would stop asking', () => {
    setApiRoutes({})
    mountTemplate(learningsCard([keepQuestion(), question({ index: 2 })]))
    expect(screen.getAllByRole('group', { name: en.t('learningsSwitch.title') })).toHaveLength(1)
    expect(screen.getByText('on (from scope feature)')).toBeInTheDocument()
    // The answers stay the questions' own controls.
    expect(screen.getByRole('radio', { name: /Nothing to add/ })).toBeEnabled()
  })

  it('says, once AI-DLC recorded it off, that these questions still wait for an answer', () => {
    setApiRoutes({})
    mountTemplate(learningsCard([question()], {}, OFF_BY_COMMAND))
    expect(screen.getByText('off (set by a command)')).toBeInTheDocument()
    expect(screen.getByText(en.t('learningsSwitch.explain.offPending'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: en.t('learningsSwitch.action.on') })).toBeEnabled()
  })

  it('is disabled while the conversation runs', () => {
    setApiRoutes({})
    const card = learningsCard()
    mountTemplate({ ...card, evidence: { ...card.evidence, session: { ...card.evidence.session!, running: true } } })
    expect(screen.getByRole('button', { name: en.t('learningsSwitch.action.off') })).toBeDisabled()
  })

  it('is absent from a question already sent or answered, and from other questions', () => {
    setApiRoutes({})
    const sent = mountTemplate(learningsCard([question()], { status: 'Delivering' }))
    expect(screen.queryByRole('button', { name: en.t('learningsSwitch.action.off') })).toBeNull()
    sent.unmount()
    const answered = mountTemplate(learningsCard([question({ answered: true, answer: 'Nothing to add' })]))
    expect(screen.queryByRole('button', { name: en.t('learningsSwitch.action.off') })).toBeNull()
    answered.unmount()
    mountTemplate(learningsCard([question({ prompt: 'Which store?', options: [option('A', 'SQLite')] })]))
    expect(screen.queryByRole('button', { name: en.t('learningsSwitch.action.off') })).toBeNull()
  })
})

describe('in the intent settings panel', () => {
  it('shows the control the view hands it, beside the summary confirmation one', async () => {
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
            blockedReason={summarySwitchBlockedReason(INTENT, t)} />}
          learnings={<LearningsSwitch api={fakeApi('off')} repoId="r_1" intentKey="default~250901-guest"
            setting={INTENT.disk.learnings} blockedReason={learningsSwitchBlockedReason(INTENT, t)} />} />
      </I18nProvider>,
    )
    const section = screen.getAllByLabelText(en.t('learningsSwitch.title'))[0]!
    expect(within(section).getByText('on (from scope feature)')).toBeInTheDocument()
    expect(within(section).getByRole('button', { name: en.t('learningsSwitch.action.off') })).toBeEnabled()
    // Each ceremony keeps its own line and its own control.
    const summary = screen.getAllByLabelText(en.t('summarySwitch.title'))[0]!
    expect(within(summary).getByText('off (set by you)')).toBeInTheDocument()
    expect(within(summary).getByRole('button', { name: en.t('summarySwitch.action.on') })).toBeEnabled()
    expect(await screen.findByLabelText(en.t('workspace.settings.scope'))).toBeInTheDocument()
  })
})
