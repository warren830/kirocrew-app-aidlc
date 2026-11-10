import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { I18nProvider, makeI18n } from '../i18n'
import { WIRE } from '../lib/wire.generated'
import { actionCard } from '../test/fixtures'
import type { DecisionSpec } from '../lib/types'
import { ConfirmPanel, type PendingDecision } from './ConfirmPanel'
import { IDLE, type SubmitState } from './useSubmit'

const en = makeI18n('en-US')

function show(decision: DecisionSpec['decision'], wire: string, submit: SubmitState = IDLE) {
  const spec: DecisionSpec = { decision, label_key: `decision.${decision}.label`, lane: 'human_lane',
    wire_text_template: null, requires: [] }
  const pending: PendingDecision = { spec, payload: { decision: 'approve' }, resolvePayload: null, wire, blockedKey: null }
  render(
    <I18nProvider>
      <ConfirmPanel card={actionCard()} pending={pending} submit={submit} inFlight={submit === IDLE ? null : 'running'}
        refreshing={false}
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
    expect(screen.queryByText(en.t('confirm.answerModeNote'))).toBeNull()
  })

  it('names the paragraph that answers AI-DLC\'s mode question as Chat', () => {
    show('answers', `Q1: True division\nQ2: Like multiply${WIRE.ANSWER_MODE_CHAT_NOTE}${WIRE.GROUPED_ANSWER_SUFFIX}`)
    expect(screen.getByText(en.t('confirm.deliveryNote'))).toBeInTheDocument()
    expect(screen.getByText(en.t('confirm.answerModeNote'))).toBeInTheDocument()
  })

  it('shows the bytes in flight from Send on, not the draft as it reads after Send', () => {
    const sent = 'Request Changes: Cover the decline path.'
    const card = actionCard()
    show('request_changes', `${sent} And 3DS.`, {
      ...IDLE, stage: 'submitting', actionId: card.action_id, generation: card.status_generation,
      request: {
        captured: { state_hash: 'sha-state', boundary_token: 'tok', question_digest: null, stage_attempt: 1,
          evidence_digest: 'sha-ev', is_active: true },
        payload: { decision: 'request_changes', feedback: 'Cover the decline path.' },
        client_wire_text: sent,
      },
    })
    expect(document.querySelector('.studio-sendtext')?.textContent).toBe(sent)
  })
})
