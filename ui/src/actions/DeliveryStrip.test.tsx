import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { I18nProvider, makeI18n } from '../i18n'
import { actionCard } from '../test/fixtures'
import type { ActionCard } from '../lib/types'
import { DeliveryStrip, statusTone } from './DeliveryStrip'

function show(status: ActionCard['status']) {
  render(<I18nProvider><DeliveryStrip card={actionCard({ status })} /></I18nProvider>)
  return screen.getByRole('region', { name: 'Delivery state' })
}

describe('delivery outcomes describe observed progress', () => {
  it.each(['en-US', 'zh-CN'] as const)('shows confirmed receipt while keeping workflow reconciliation pending (%s)', (locale) => {
    document.documentElement.lang = locale
    const i18n = makeI18n(locale)
    const base = actionCard()
    const card = actionCard({
      status: 'ReconciliationRequired',
      delivery: { ...base.delivery, outcome: 'uncertain', delivery_confirmed: true,
        transcript_row: { slot_key: 'aidlc-r1-guest', ts: '2026-09-14T10:00:00Z', role: 'user' },
        disk_baseline_unchanged: true },
    })
    render(<I18nProvider><DeliveryStrip card={card} /></I18nProvider>)
    const strip = screen.getByRole('region', { name: i18n.t('delivery.label') })
    expect(within(strip).queryByText(i18n.t('delivery.uncertainBody'))).not.toBeInTheDocument()
    expect(within(strip).queryByText(i18n.t('delivery.uncertain'))).not.toBeInTheDocument()
    expect(strip).toHaveTextContent(locale === 'en-US'
      ? 'Delivery confirmed — workflow needs reconciliation' : '消息已送达，工作流仍需核对')
    expect(strip).toHaveTextContent(locale === 'en-US'
      ? 'Whether AI-DLC accepted the answer or changed the workflow is still unverified.'
      : 'AI-DLC 是否接受了回答、工作流状态是否变化，仍未得到验证。')
    expect(strip).toHaveTextContent(i18n.t('enum.actionStatus.ReconciliationRequired'))
    expect(strip).toHaveTextContent('aidlc-r1-guest')
    expect([...strip.querySelectorAll('.studio-dstep')].map((step) => step.getAttribute('data-state')))
      .toEqual(['done', 'done', 'now', 'future'])
    expect(strip.querySelectorAll('.studio-dstep')[2]).toHaveTextContent(
      locale === 'en-US' ? 'Reconcile workflow' : '核对工作流',
    )
  })

  it.each(['DeliveryUncertain', 'ReconciliationRequired'] as const)('keeps the uncertainty warning without confirmed delivery (%s)', (status) => {
    const i18n = makeI18n('en-US')
    const strip = show(status)
    expect(within(strip).getByRole('alert')).toHaveTextContent(i18n.t('delivery.uncertainBody'))
    expect([...strip.querySelectorAll('.studio-dstep')].map((step) => step.getAttribute('data-state')))
      .toEqual(['done', 'failed', 'future', 'future'])
    expect(strip).not.toHaveTextContent('Delivery confirmed — workflow needs reconciliation')
  })

  it('does not call a label-only reply an answered note', () => {
    const card = actionCard({ status: 'ResolvedNoTransition', resolution: {
      kind: 'no_transition', reason: 'answer_requires_text', evidence: null, resolved_at: null,
    } })
    render(<I18nProvider><DeliveryStrip card={card} /></I18nProvider>)
    expect(screen.getByText('Text still required')).toBeInTheDocument()
    expect(screen.queryByText('Answered')).not.toBeInTheDocument()
  })

  it.each(['en-US', 'zh-CN'] as const)('labels a mismatching receipt as unverified, not answered (%s)', (locale) => {
    document.documentElement.lang = locale
    const i18n = makeI18n(locale)
    const card = actionCard({ status: 'ResolvedNoTransition', resolution: {
      kind: 'no_transition', reason: 'answer_not_verified_at_gate', evidence: null, resolved_at: null,
    } })
    render(<I18nProvider><DeliveryStrip card={card} /></I18nProvider>)
    const strip = screen.getByRole('region', { name: i18n.t('delivery.label') })
    expect(strip).toHaveTextContent(i18n.t('delivery.answerNotVerified'))
    expect(strip).toHaveTextContent(i18n.t('delivery.answerNotVerifiedBody'))
    expect(strip).not.toHaveTextContent(i18n.t('enum.actionStatus.ResolvedNoTransition'))
    expect(strip).not.toHaveTextContent(i18n.t('delivery.noTransition'))
  })

  it('does not mark a workflow state change complete when a turn ended without one', () => {
    const strip = show('ResolvedNoTransition')
    const steps = [...strip.querySelectorAll('.studio-dstep')]
    expect(steps.map((step) => step.getAttribute('data-state'))).toEqual([
      'done', 'done', 'done', 'unchanged',
    ])
    expect(steps[3]).toHaveTextContent('No state change')
    expect(strip).toHaveTextContent('No workflow state change was recorded.')
    expect(strip).not.toHaveTextContent('No state transition was expected')
    expect(statusTone('ResolvedNoTransition')).toBe('neutral')
  })

  it('marks every step complete when the workflow state did change', () => {
    const strip = show('StateChanged')
    expect([...strip.querySelectorAll('.studio-dstep')].map((step) => step.getAttribute('data-state'))).toEqual([
      'done', 'done', 'done', 'done',
    ])
    expect(strip).toHaveTextContent('State changed')
    expect(statusTone('StateChanged')).toBe('ok')
  })

  it('keeps active processing distinct from a settled turn', () => {
    const strip = show('Processing')
    expect([...strip.querySelectorAll('.studio-dstep')].map((step) => step.getAttribute('data-state'))).toEqual([
      'done', 'done', 'now', 'future',
    ])
    expect(strip).not.toHaveTextContent('No workflow state change was recorded.')
  })

  it('does not show a cancelled action as queued and in progress', () => {
    const strip = show('Cancelled')
    expect([...strip.querySelectorAll('.studio-dstep')].map((step) => step.getAttribute('data-state'))).toEqual([
      'cancelled', 'future', 'future', 'future',
    ])
    expect(strip.querySelector('[data-state="now"]')).toBeNull()
  })
})
