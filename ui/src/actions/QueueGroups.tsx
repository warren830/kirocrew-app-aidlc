/**
 * How the queue is divided, and what each divider is called.
 *
 * Grouping is computed on the client even though `GET /actions` already sends `groups[]`, for one
 * reason: the client filters (search) and re-sorts, and a server group that still lists a filtered-out
 * card would render an empty header — "Blocking Gates and questions" above nothing. So the server's
 * order is the contract (`sort.ts` is byte-compatible with `Projection.sort_actions`) and the grouping
 * is re-derived from the cards actually on screen.
 *
 * The label for a band comes from the same keys the backend puts in `groups[].label_key`
 * (`queue.group.*`), so the two never disagree about what band 2 is called. Repo and type groups are
 * named by data the card carries — a repo label is the user's own word for their checkout and is never
 * translated, and a type is `enum.actionType.*`, which the shared catalogue already owns.
 */

import type { I18n } from '../i18n'
import { groupKey, priorityGroupOf } from '../lib/sort'
import type { ActionCard, Organize } from '../lib/types'

export interface QueueGroup {
  /** The same value `Projection.group_key` emits, so a server group can be matched to a local one. */
  key: string
  label: string
  cards: ActionCard[]
}

/** FR-ACT-003's four bands, in order. Mirrors `GROUP_LABEL_KEYS` in `handlers/actions.py`. */
const BAND_KEYS: Record<number, string> = {
  1: 'queue.group.recovery',
  2: 'queue.group.blocking',
  3: 'queue.group.attention',
  4: 'queue.group.info',
}

/** The heading for the group a card falls in. */
export function groupLabel(card: ActionCard, organize: Organize, i18n: I18n): string {
  switch (organize) {
    case 'priority':
      return i18n.t(BAND_KEYS[priorityGroupOf(card)] ?? BAND_KEYS[4]!)
    case 'repo':
      // The repo label is the user's own name for their checkout: shown verbatim, never translated.
      return card.repo.label || card.repo.repo_id
    case 'type':
      return i18n.t(`enum.actionType.${card.queue_type}`)
    case 'oldest':
      return i18n.t('queue.group.oldest')
  }
}

/**
 * Split an ALREADY SORTED list into consecutive groups.
 *
 * Consecutive, not bucketed: `sortActions` puts every member of a group together, so a run-length
 * split preserves the order exactly. Bucketing into a map would silently re-order the groups
 * themselves the day a sort key changes.
 */
export function buildGroups(cards: ActionCard[], organize: Organize, i18n: I18n): QueueGroup[] {
  const groups: QueueGroup[] = []
  for (const card of cards) {
    const key = groupKey(card, organize)
    const last = groups[groups.length - 1]
    if (last && last.key === key) last.cards.push(card)
    else groups.push({ key, label: groupLabel(card, organize, i18n), cards: [card] })
  }
  return groups
}

export interface QueueGroupHeadingProps {
  group: QueueGroup
  id: string
}

/**
 * A sticky group heading.
 *
 * `position: sticky` is applied by `.studio-qgroup`; because each heading is the first child of its own
 * `<section>` inside the one scroller, each one sticks until its own section leaves — which is what
 * makes a 200-row queue readable while scrolling. The count is part of the heading rather than a
 * separate chip so a screen reader announces "Blocking Gates and questions, 7 items" in one go.
 */
export function QueueGroupHeading({ group, id }: QueueGroupHeadingProps) {
  return (
    <h3 className="studio-qgroup" id={id}>
      <span className="studio-grow studio-trunc">{group.label}</span>
      <span className="studio-mono studio-qgroup-n">{group.cards.length}</span>
    </h3>
  )
}
