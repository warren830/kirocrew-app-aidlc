import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '../i18n'
import { EMPTY_ROUTE } from '../lib/route'

const appName = vi.hoisted(() => ({ value: 'aidlc-studio' }))
vi.mock('../lib/app', async (original) => ({
  ...(await original<typeof import('../lib/app')>()),
  get APP_NAME() {
    return appName.value
  },
}))

const { TopBar } = await import('./TopBar')

function renderBar() {
  return render(
    <I18nProvider>
      <TopBar route={EMPTY_ROUTE} go={() => {}} queueCount={null} showBack={false} onBack={() => {}} />
    </I18nProvider>,
  )
}

describe('TopBar instance marker', () => {
  afterEach(() => {
    appName.value = 'aidlc-studio'
  })

  it('shows nothing extra for the shipped Studio', () => {
    renderBar()
    expect(screen.queryByText(/aidlc-studio-next/)).toBeNull()
  })

  it('names a side-by-side copy, which owns none of the shipped install receipts', () => {
    appName.value = 'aidlc-studio-next'
    renderBar()
    const chip = screen.getByText('Test copy · aidlc-studio-next')
    expect(chip.closest('[title]')?.getAttribute('title')).toMatch(/install receipts/)
  })
})
