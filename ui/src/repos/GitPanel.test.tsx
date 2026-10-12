import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { I18nProvider } from '../i18n'
import type { GitObservation } from '../lib/types'
import { GitSummaryChips } from './GitPanel'

describe('the repository table\'s Git chips', () => {
  it('says Git was not read yet, without a warning, when the list carries no observation', () => {
    render(<I18nProvider><GitSummaryChips git={null} /></I18nProvider>)
    expect(screen.getByText('Git not read yet')).toBeInTheDocument()
    expect(screen.queryByText('Git observation is off')).toBeNull()
  })

  it('warns only when Git could not be observed', () => {
    const off = { available: false, reason: 'git is not installed' } as GitObservation
    render(<I18nProvider><GitSummaryChips git={off} /></I18nProvider>)
    expect(screen.getByText('Git observation is off')).toHaveAttribute('title', 'git is not installed')
  })
})
