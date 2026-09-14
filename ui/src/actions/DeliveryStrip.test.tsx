import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { I18nProvider } from '../i18n'
import { actionCard } from '../test/fixtures'
import type { ActionCard } from '../lib/types'
import { DeliveryStrip, statusTone } from './DeliveryStrip'

function show(status: ActionCard['status']) {
  render(<I18nProvider><DeliveryStrip card={actionCard({ status })} /></I18nProvider>)
  return screen.getByRole('region', { name: 'Delivery state' })
}

describe('delivery outcomes describe observed progress', () => {
  it('does not call a label-only reply an answered note', () => {
    const card = actionCard({ status: 'ResolvedNoTransition', resolution: {
      kind: 'no_transition', reason: 'answer_requires_text', evidence: null, resolved_at: null,
    } })
    render(<I18nProvider><DeliveryStrip card={card} /></I18nProvider>)
    expect(screen.getByText('Text still required')).toBeInTheDocument()
    expect(screen.queryByText('Answered')).not.toBeInTheDocument()
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
