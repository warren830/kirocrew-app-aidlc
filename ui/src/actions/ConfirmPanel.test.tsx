import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { I18nProvider, makeI18n } from '../i18n'
import { WIRE } from '../lib/wire.generated'
import { actionCard } from '../test/fixtures'
import type { DecisionSpec } from '../lib/types'
import { ConfirmPanel, type PendingDecision } from './ConfirmPanel'
import { IDLE } from './useSubmit'

const en = makeI18n('en-US')

function show(decision: DecisionSpec['decision'], wire: string) {
  const spec: DecisionSpec = { decision, label_key: `decision.${decision}.label`, lane: 'human_lane',
    wire_text_template: null, requires: [] }
  const pending: PendingDecision = { spec, payload: { decision: 'approve' }, resolvePayload: null, wire, blockedKey: null }
  render(
    <I18nProvider>
      <ConfirmPanel card={actionCard()} pending={pending} submit={IDLE} busy={false} refreshing={false}
        acknowledged={false} onAcknowledge={() => {}} onSend={() => {}} onCancel={() => {}} />
    </I18nProvider>,
  )
}

describe('the text a confirmation shows', () => {
  it('quotes a short reply on the label line as well as in the exact-text block', () => {
    show('approve', 'Approve')
    const label = en.t('decision.approve.label')
    expect(screen.getByText(en.t('confirm.labelSends', { label, wire: 'Approve' }))).toBeInTheDocument()
    expect(document.querySelector('.studio-sendtext')).toHaveTextContent('Approve')
  })

  it('shows a long reply once, and names Studio\'s delivery note at its end', () => {
    const wire = `Q1: True division\nQ2: Like multiply, plus an explicit check${WIRE.GROUPED_ANSWER_SUFFIX}`
    show('answers', wire)
    const label = en.t('decision.answers.label')
    expect(screen.getByText(en.t('confirm.labelSendsBelow', { label }))).toBeInTheDocument()
    expect(screen.queryByText(/→ sends: Q1/)).toBeNull()
    expect(document.querySelectorAll('.studio-sendtext')).toHaveLength(1)
    expect(document.querySelector('.studio-sendtext')?.textContent).toBe(wire)
    expect(screen.getByText(en.t('confirm.deliveryNote'))).toBeInTheDocument()
  })
})
