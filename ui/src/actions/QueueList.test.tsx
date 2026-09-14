/**
 * The queue's grouping as the DOM sees it.
 *
 * `sort.ts` is what normally guarantees that every card of a group arrives in one consecutive run, so
 * this file takes the other side: what the list must survive when that guarantee is broken. The list is
 * handed the cards in the order it must paint them, and a run-length split of a list whose order came
 * from the server (a different collation) or from a future sort key can hand `buildGroups` the same key
 * twice. React treats duplicate sibling keys as unsupported and may drop or merge one of them, which in
 * this list means a heading painted over another repository's cards.
 */

import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { actionCard } from '../test/fixtures'
import { I18nProvider } from '../i18n'
import { QueueList } from './QueueList'
import type { ActionCard } from '../lib/types'

/** A card in one repository, named so the assertions can tell the sections apart. */
const inRepo = (actionId: string, repoId: string, label: string): ActionCard =>
  actionCard({
    action_id: actionId,
    repo: { repo_id: repoId, label, canonical_path: `/work/${repoId}` },
  })

const mount = (cards: ActionCard[]) =>
  render(
    <I18nProvider>
      <QueueList
        cards={cards}
        organize="repo"
        selected=""
        onSelect={() => {}}
        emptyBecauseNothingWaits={false}
      />
    </I18nProvider>,
  )

/** Which cards ended up under each heading, in DOM order. */
const sections = () =>
  screen.getAllByRole('region').map((node) => ({
    heading: node.querySelector('h3')?.textContent ?? '',
    cards: [...node.querySelectorAll('[data-action-id]')].map((row) => row.getAttribute('data-action-id')),
  }))

describe('the queue list', () => {
  const warnings: unknown[][] = []

  afterEach(() => {
    warnings.length = 0
  })

  /** React reports an unsupported render through `console.error`; silence it but keep it assertable. */
  const captureReactWarnings = () => {
    vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => void warnings.push(args))
  }

  it('renders every group as its own section even when two share a group key', () => {
    // The adversary is two checkouts of the same project labelled the same way, arriving interleaved:
    // the run-length split then yields the key `r_one` twice. Both sections have to keep their own
    // heading and their own cards, because a merged section shows one repository's cards under the
    // other's heading and the user approves a gate in the wrong checkout.
    captureReactWarnings()
    mount([
      inRepo('a_1', 'r_one', 'platform'),
      inRepo('a_2', 'r_two', 'platform'),
      inRepo('a_3', 'r_one', 'platform'),
    ])

    expect(sections()).toEqual([
      { heading: 'platform1', cards: ['a_1'] },
      { heading: 'platform1', cards: ['a_2'] },
      { heading: 'platform1', cards: ['a_3'] },
    ])
    const duplicateKey = warnings.filter((args) => String(args[0]).includes('same key'))
    expect(duplicateKey).toEqual([])
  })

  it('keeps one section per repository when the cards arrive grouped', () => {
    captureReactWarnings()
    mount([
      inRepo('a_1', 'r_one', 'alfa'),
      inRepo('a_2', 'r_one', 'alfa'),
      inRepo('a_3', 'r_two', 'zulu'),
    ])

    expect(sections()).toEqual([
      { heading: 'alfa2', cards: ['a_1', 'a_2'] },
      { heading: 'zulu1', cards: ['a_3'] },
    ])
    expect(warnings).toEqual([])
  })
})
