import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { I18nProvider, makeI18n } from '../i18n'
import { useStudioApi } from '../lib/api'
import { ref } from '../lib/format'
import { EMPTY_ROUTE } from '../lib/route'
import { actionCard, API_BASE } from '../test/fixtures'
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
