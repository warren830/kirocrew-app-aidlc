/**
 * A finding reads as words, not as its code.
 *
 * The backend sends findings by code; the `findings` catalogue part owns a title and a body for every code
 * the consistency engine and the preflight can emit (`scripts/build_i18n.py` and `tests/test_consistency.py`
 * pin that coverage against the backend's own tables). These tests pin the rendering: a known code shows
 * its title and its body with the params filled in, and not the code; a code this build has no copy for
 * still renders, naming the code, instead of an empty row or a raw catalogue key.
 */

import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { makeI18n } from '../i18n'
import { findingCopy } from '../lib/format'
import type { Finding } from '../lib/types'
import { actionCard } from '../test/fixtures'
import { ConsistencyFindings } from '../templates/DecisionControls'
import { FindingList } from './PreflightReport'

const RECORD = 'aidlc/spaces/default/intents/250901-guest'

function finding(code: string, over: Partial<Finding> = {}): Finding {
  return {
    code,
    severity: 'blocking',
    message_key: `finding.${code}`,
    params: {},
    evidence: [{ kind: 'file', ref: `${RECORD}/aidlc-state.md`, detail: null }],
    ...over,
  }
}

const MISSING_ARTIFACT = finding('missing_required_artifact', {
  params: { stage: 'requirements-analysis', artifact: 'requirements', stage_dir: `${RECORD}/inception`, present: [] },
})
const UNKNOWN = finding('some_future_check', { severity: 'warn' })

describe('the repository finding list', () => {
  it('shows a known code as its title and body, with the params filled in and no raw code', () => {
    render(<FindingList findings={[MISSING_ARTIFACT]} />)

    const item = screen.getByRole('listitem')
    expect(within(item).getByText('Blocking')).toBeInTheDocument()
    expect(within(item).getByText('A required output is missing')).toBeInTheDocument()
    expect(
      within(item).getByText(
        'requirements-analysis is waiting for approval, but its required output requirements was not found. ' +
          'Studio holds back decisions for this intent until the file is there. Ask AI-DLC in the conversation to produce it.',
      ),
    ).toBeInTheDocument()
    expect(within(item).queryByText(/missing_required_artifact/)).toBeNull()
    expect(within(item).queryByText(/^Finding /)).toBeNull()
    // The evidence is still the backend's own paths, verbatim.
    expect(within(item).getByText(`file: ${RECORD}/aidlc-state.md`)).toBeInTheDocument()
    // Kept for a bug report, but not as text on the page.
    expect(item).toHaveAttribute('data-code', 'missing_required_artifact')
  })

  it('still renders a code this build has no copy for, by naming it', () => {
    render(<FindingList findings={[UNKNOWN]} />)

    const item = screen.getByRole('listitem')
    expect(within(item).getByText('Warning')).toBeInTheDocument()
    expect(within(item).getByText('Finding some_future_check')).toBeInTheDocument()
    expect(within(item).queryByText(/finding\.some_future_check/)).toBeNull()
  })
})

describe('the findings on a card', () => {
  it('shows the title and body for a known code, and the code only beside the generic sentence', () => {
    const base = actionCard({ type: 'recovery' })
    const card = actionCard({
      type: 'recovery',
      evidence: { ...base.evidence, findings: [finding('dangling_cursor'), UNKNOWN] },
    })
    render(<ConsistencyFindings card={card} title="What disagrees" />)

    const [known, unknown] = Array.from(document.querySelectorAll<HTMLElement>('.studio-finding'))
    expect(within(known!).getByText('The active intent points at a missing record')).toBeInTheDocument()
    expect(within(known!).getByText(/Choose the intent to work on from the Missing input card/)).toBeInTheDocument()
    expect(within(known!).queryByText('dangling_cursor')).toBeNull()

    expect(
      within(unknown!).getByText("AI-DLC's files disagree here. The evidence below names the files."),
    ).toBeInTheDocument()
    expect(within(unknown!).getByText('some_future_check')).toBeInTheDocument()
  })
})

describe('findingCopy', () => {
  it('fills the params in either locale, and answers null for a code with no copy', () => {
    const counts = finding('completed_count_mismatch', { severity: 'warn', params: { claimed: 5, actual: 4 } })
    expect(findingCopy(makeI18n('en-US'), counts)).toEqual({
      title: 'Completed-stage count does not match',
      body: 'aidlc-state.md says 5 stages are completed, but 4 are ticked off in its stage list. The count is only a summary, so nothing is blocked.',
    })
    const zh = findingCopy(makeI18n('zh-CN'), counts)
    expect(zh?.title).toBe('已完成阶段数对不上')
    expect(zh?.body).toContain('已完成 5 个阶段')
    expect(zh?.body).toContain('勾选完成的是 4 个')

    expect(findingCopy(makeI18n('en-US'), UNKNOWN)).toBeNull()
  })
})
