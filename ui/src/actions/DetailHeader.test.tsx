import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { I18nProvider } from '../i18n'
import { actionCard } from '../test/fixtures'
import { DetailHeader } from './DetailHeader'

/** The chips under the title; the headline above them is the card's own sentence. */
function facts(card: ReturnType<typeof actionCard>): HTMLElement {
  render(<I18nProvider><DetailHeader card={card} /></I18nProvider>)
  return screen.getByRole('banner').querySelector('.studio-dfacts') as HTMLElement
}

describe('the decision header', () => {
  it('says an untouched card is waiting for you and has not been sent', () => {
    const row = facts(actionCard({ status: 'Queued', severity: 'blocking' }))
    expect(row).toHaveTextContent('Blocking')
    expect(row).toHaveTextContent(/waiting/)
    expect(row).toHaveTextContent('Not sent yet')
  })

  it('drops the urgency and the wait once the decision is sent: it waits on AI-DLC now', () => {
    const card = actionCard({ status: 'Delivered', severity: 'blocking' })
    const row = facts({ ...card, delivery: { ...card.delivery, delivered_at: new Date().toISOString() } })
    expect(row).not.toHaveTextContent('Blocking')
    expect(row).not.toHaveTextContent(/waiting/)
    expect(row).toHaveTextContent(/sent /)
  })

  it('leaves the stage out of a commit request\'s breadcrumb: it is about the whole intent', () => {
    const stage = { slug: 'deployment-execution', number: '4.3', name: null, phase: 'operation', unit: null }
    render(<I18nProvider><DetailHeader card={actionCard({ type: 'prepare_commit', queue_type: 'prepare_commit', stage } as never)} /></I18nProvider>)
    const crumbs = screen.getByRole('navigation', { name: 'Where this decision sits' })
    expect(crumbs).not.toHaveTextContent('deployment-execution')
  })

  it('keeps the stage in a gate\'s breadcrumb', () => {
    const stage = { slug: 'code-generation', number: '3.5', name: null, phase: 'construction', unit: null }
    render(<I18nProvider><DetailHeader card={actionCard({ type: 'gate', stage } as never)} /></I18nProvider>)
    expect(screen.getByRole('navigation', { name: 'Where this decision sits' })).toHaveTextContent('3.5 code-generation')
  })
})
