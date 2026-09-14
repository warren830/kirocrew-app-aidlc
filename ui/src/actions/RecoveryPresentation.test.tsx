import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { I18nProvider, makeI18n } from '../i18n'
import { useStudioApi } from '../lib/api'
import { ref } from '../lib/format'
import { EMPTY_ROUTE } from '../lib/route'
import { actionCard, API_BASE, decisionSpec } from '../test/fixtures'
import type { ActionCard } from '../lib/types'
import { apiCalls, setApiRoutes } from '../test/stubs/app-sdk'
import '../templates/TemplateRegistry'
import { DetailShell } from './DetailShell'

function show(cancelled: boolean, overrides: Partial<ActionCard> = {}) {
  const card = actionCard({
    type: 'recovery', queue_type: 'recovery', risk_class: 'studio_only', severity: 'critical',
    status: cancelled ? 'Cancelled' : 'Queued',
    headline: { key: 'action.recovery.headline', params: { stage: 'functional-design', codes: ['session_lost_mid_stage'] } },
    consequence: { key: 'action.recovery.consequence.blocked_until_agree', params: { codes: ['session_lost_mid_stage'] } },
    primary: cancelled ? null : { decision: 'acknowledge', label_key: 'decision.acknowledge.label' },
    decisions: cancelled ? [] : [{
      decision: 'acknowledge', label_key: 'decision.acknowledge.label', lane: 'studio_only',
      wire_text_template: null, requires: [],
    }],
    resolution: cancelled
      ? { kind: 'cancelled', resolved_at: '2026-09-11T03:33:33Z', reason: 'acknowledged', evidence: null }
      : { kind: null, resolved_at: null, reason: null, evidence: null },
    ...overrides,
  })
  const go = vi.fn()
  setApiRoutes({
    [`GET ${API_BASE}/actions/${card.action_id}`]: () => ({ action: card, transitions: [], drafts: [] }),
  })
  function Harness() {
    const api = useStudioApi()
    return <DetailShell actionId={card.action_id} queueCard={card} api={api}
      route={{ ...EMPTY_ROUTE, action: card.action_id }} go={go}
      groupedAnswers={false} onQueueChanged={() => {}} />
  }
  render(<I18nProvider><Harness /></I18nProvider>)
  return { card, go }
}

describe('recovery notices explain the actual cause and lifecycle', () => {
  it.each(['en-US', 'zh-CN'] as const)('does not invent a transcript row for confirmed delivery after a restart (%s)', (locale) => {
    document.documentElement.lang = locale
    const i18n = makeI18n(locale)
    const base = actionCard()
    show(false, {
      status: 'ReconciliationRequired', queue_type: 'delivery_uncertain',
      headline: { key: 'action.recovery.headline', params: { codes: ['presence_check_failed'] } },
      decisions: [decisionSpec('reconcile')],
      primary: { decision: 'reconcile', label_key: 'decision.reconcile.label' },
      delivery: { ...base.delivery, delivery_confirmed: true, outcome: 'uncertain',
        transcript_row: null, disk_baseline_unchanged: true, boot_id_unchanged: false },
    })
    const checks = screen.getByText((text) => text.includes(i18n.t('template.recovery.rowAbsent')))
    expect(checks).toHaveTextContent(locale === 'en-US'
      ? 'The gateway restarted. Message delivery is confirmed; workflow reconciliation is still pending.'
      : '网关曾重启。消息送达已确认，工作流仍待核对。')
    expect(checks).not.toHaveTextContent(locale === 'en-US'
      ? 'The matching conversation turn still confirms receipt.'
      : '会话中匹配的发言仍能证明消息已送达')
    expect(screen.getByText(i18n.t('template.recovery.confirmedAlert'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('template.recovery.uncertainAlert'))).not.toBeInTheDocument()
    const strip = screen.getByRole('region', { name: i18n.t('delivery.label') })
    expect(strip).toHaveTextContent(i18n.t('enum.actionStatus.ReconciliationRequired'))
    expect([...strip.querySelectorAll('.studio-dstep')].map((step) => step.getAttribute('data-state')))
      .toEqual(['done', 'done', 'now', 'future'])
    expect(screen.getByRole('button', { name: i18n.t('decision.reconcile.label') })).toBeEnabled()
    expect(apiCalls.filter((call) => call.method !== 'GET')).toEqual([])
  })

  it.each(['en-US', 'zh-CN'] as const)('distinguishes confirmed receipt from unverified workflow evidence (%s)', async (locale) => {
    document.documentElement.lang = locale
    const i18n = makeI18n(locale)
    const base = actionCard()
    show(false, {
      status: 'ReconciliationRequired', queue_type: 'delivery_uncertain',
      headline: { key: 'action.recovery.headline', params: { codes: ['presence_check_failed'] } },
      decisions: [decisionSpec('reconcile')],
      primary: { decision: 'reconcile', label_key: 'decision.reconcile.label' },
      delivery: { ...base.delivery, delivery_confirmed: true, outcome: 'uncertain',
        transcript_row: { slot_key: 'aidlc-r1-guest', ts: '2026-09-14T10:00:00Z', role: 'user' },
        disk_baseline_unchanged: true, boot_id_unchanged: false },
      evidence: { ...base.evidence, presence: { human_turn_delta: 0, marker_advanced: false, ok: false } },
    })
    expect(screen.queryByText(i18n.t('template.recovery.uncertainAlert'))).not.toBeInTheDocument()
    expect(screen.queryByText(i18n.t('template.recovery.contradictionBody'))).not.toBeInTheDocument()
    expect(screen.queryByText(i18n.t('template.recovery.bootRestarted'))).not.toBeInTheDocument()
    expect(screen.getByText(locale === 'en-US'
      ? 'Delivery is confirmed. This intent still needs workflow reconciliation. Answer acceptance has not been verified; nothing is replayed automatically.'
      : '消息已确认送达。该 intent 的工作流仍需核对，回答是否被接受尚未验证；系统不会自动重放。')).toBeInTheDocument()
    const delivery = screen.getByText(i18n.t('template.recovery.src.delivery')).closest<HTMLDivElement>('.studio-ev')!
    expect(delivery).not.toHaveAttribute('data-conflict')
    expect(delivery).toHaveTextContent(locale === 'en-US'
      ? 'Confirmed in the conversation: yes' : '会话已确认收到：是')
    expect(within(delivery).queryByText(i18n.t('template.recovery.outcome.uncertain'))).not.toBeInTheDocument()
    const marker = screen.getByText(i18n.t('template.recovery.src.marker')).closest('.studio-ev')!
    expect(marker).toHaveAttribute('data-conflict', 'true')
    expect(screen.getByRole('button', { name: i18n.t('decision.reconcile.label') })).toBeEnabled()
    expect(screen.queryByRole('button', { name: i18n.t('decision.resubmit.label') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('decision.mark_not_delivered.label') })).not.toBeInTheDocument()
    expect(apiCalls.filter((call) => call.method !== 'GET')).toEqual([])
  })

  it('retains warnings and the offered recovery controls for genuinely uncertain delivery', async () => {
    const i18n = makeI18n('en-US')
    const base = actionCard()
    show(false, {
      status: 'ReconciliationRequired', queue_type: 'delivery_uncertain',
      decisions: [decisionSpec('reconcile'), decisionSpec('mark_not_delivered', ['confirm']), decisionSpec('resubmit', ['confirm'])],
      primary: { decision: 'reconcile', label_key: 'decision.reconcile.label' },
      delivery: { ...base.delivery, outcome: 'uncertain', boot_id_unchanged: false, disk_baseline_unchanged: true },
    })
    expect(screen.getByText(i18n.t('template.recovery.uncertainAlert'))).toBeInTheDocument()
    expect(screen.getByText(i18n.t('template.recovery.contradictionBody'))).toBeInTheDocument()
    const delivery = screen.getByText(i18n.t('template.recovery.src.delivery')).closest('.studio-ev')!
    expect(delivery).toHaveAttribute('data-conflict', 'true')
    for (const decision of ['reconcile', 'mark_not_delivered', 'resubmit']) {
      expect(screen.getByRole('button', { name: i18n.t(`decision.${decision}.label`) })).toBeEnabled()
    }
    await userEvent.click(screen.getByRole('button', { name: i18n.t('decision.resubmit.label') }))
    expect(screen.getByRole('group', { name: i18n.t('confirm.label') })).toBeInTheDocument()
    expect(apiCalls.filter((call) => call.method !== 'GET')).toEqual([])
  })

  it.each([null, {}])('renders unavailable turn markers from a historical action (%j)', async (markers) => {
    const base = actionCard()
    // ActionBroker.card fills absent snapshot keys with null; older marker snapshots can be empty.
    show(true, { evidence: { ...base.evidence,
      markers: markers as unknown as ActionCard['evidence']['markers'],
    } })
    const source = await screen.findByText('Turn marker')
    const evidence = source.closest<HTMLDivElement>('.studio-ev')!
    expect(within(evidence).getByText('Unavailable', { exact: true })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Recovery · Cancelled')
  })

  it('opens an uncertain historical question whose directive has no units array', async () => {
    const base = actionCard()
    show(false, {
      type: 'question', queue_type: 'delivery_uncertain', status: 'ReconciliationRequired',
      evidence: { ...base.evidence, directive: {
        stage: 'code-generation', unit: null, matches_state: true,
      } },
    })
    expect(await screen.findByText('AI-DLC directive')).toBeInTheDocument()
    expect(screen.getByText('code-generation')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Delivery state' })).toBeInTheDocument()
    expect(apiCalls.filter((call) => call.method !== 'GET')).toEqual([])
  })

  it('renders recovery codes instead of an unfilled reason placeholder', () => {
    const en = makeI18n('en-US')
    const zh = makeI18n('zh-CN')
    const message = { key: 'action.recovery.headline', params: { codes: ['session_lost_mid_stage'] } }
    expect(ref(en, message)).toBe('This intent needs recovery: the bound conversation could not be found.')
    expect(ref(zh, message)).toContain('绑定的会话已不存在')
    expect(ref(en, { ...message, params: { codes: ['gate_without_audit_row'] } })).toContain('gate_without_audit_row')
  })

  it('offers conversation management for a missing binding target', async () => {
    const { card, go } = show(false)
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent('the bound conversation could not be found')
    expect(screen.getByText('The recorded conversation was unavailable when this notice was created.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Manage conversation in Intents' }))
    expect(go).toHaveBeenLastCalledWith({
      ...EMPTY_ROUTE, view: 'intents', repo: card.repo.repo_id, intent: card.intent.intent_key,
    })
    expect(apiCalls.filter((call) => call.method !== 'GET')).toEqual([])
  })

  it('shows an acknowledged notice as history and can open the current queue', async () => {
    const { card, go } = show(true)
    const title = await screen.findByRole('heading', { level: 1 })
    expect(title).toHaveTextContent('Recovery · Cancelled')
    const header = title.closest('header')!
    expect(within(header).queryByText('Critical')).not.toBeInTheDocument()
    expect(within(header).queryByText(/^waiting /)).not.toBeInTheDocument()
    expect(screen.getByText('This action is closed. Use current actions to see what this intent needs now.')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Delivery state' }).querySelector('[data-state="now"]')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'View current actions' }))
    expect(go).toHaveBeenLastCalledWith({
      ...EMPTY_ROUTE, view: 'actions', repo: card.repo.repo_id, intent: card.intent.intent_key,
    })
    expect(apiCalls.filter((call) => call.method !== 'GET')).toEqual([])
  })
})
