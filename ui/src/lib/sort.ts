/**
 * Queue order (FR-ACT-003/004), the same comparator as `Projection.sort_actions`.
 *
 * Both sides sort because both have to: the server sends `groups[]` in this order so a client that has
 * not resolved its locale yet still renders correctly, and the client re-sorts after filtering so a
 * hidden row cannot leave a gap in a group. If the two disagreed, a card would appear to move when a
 * poll landed. `test_projection.py` and `sort.test.ts` pin the same golden order against both.
 *
 * The one intentional difference is collation: the server has no user locale and folds case
 * (`_repo_label`), the client uses `Intl.Collator` so `Ärger` sorts where the reader expects (§3.6).
 * Repo grouping is by label, so the ordering of the *groups* can differ between the two; the order
 * *within* a group cannot.
 */

import type { ActionCard, ActionType, Organize, Severity } from './types'

/** Queue band per action type. Byte-for-byte `projection.PRIORITY_GROUP`. */
export const PRIORITY_GROUP: Record<ActionType, 1 | 2 | 3 | 4> = {
  recovery: 1, delivery_uncertain: 1,
  gate: 2, question: 2, missing_input: 2,
  circuit_breaker: 3, failure: 3, install_conflict: 3,
  budget_stop: 4, revision: 4, run: 4, resume: 4, force_stop: 4, prepare_commit: 4,
}

export const SEVERITY_RANK: Record<Severity, number> = { critical: 0, blocking: 1, attention: 2, info: 3 }

// Widened views for the lookups. The tables above are keyed by the enum so adding an action type is a
// compile error here rather than a silently unranked card; these aliases exist because the *wire* can
// still carry a value this bundle predates, and `priority_group_for` in the backend answers 4 for it.
const GROUP_OF: Readonly<Record<string, number>> = PRIORITY_GROUP
const RANK_OF: Readonly<Record<string, number>> = SEVERITY_RANK

/** Band for a type the table does not know: last, never a crash. */
export function priorityGroupOf(card: ActionCard): number {
  return GROUP_OF[card.queue_type] ?? GROUP_OF[card.type] ?? 4
}

export function severityRankOf(card: ActionCard): number {
  return RANK_OF[card.severity] ?? 3
}

/**
 * Equal priority → oldest first (FR-ACT-004), then `action_id` for determinism.
 *
 * `waiting_since` is the BOUNDARY time — when the gate opened, the question was written, the failure
 * was observed — not `created_at`. A card re-derived after a gateway restart would otherwise jump to
 * the front of a queue it has been at the back of for an hour (C20).
 */
const byOldest = (a: ActionCard, b: ActionCard): number =>
  a.waiting_since < b.waiting_since ? -1
    : a.waiting_since > b.waiting_since ? 1
      : a.action_id < b.action_id ? -1
        : a.action_id > b.action_id ? 1
          : 0

/** A new array; the input is never mutated (it is usually the resource's cached value). */
export function sortActions(cards: ActionCard[], organize: Organize, collator: Intl.Collator): ActionCard[] {
  const c = [...cards]
  switch (organize) {
    case 'priority':
      return c.sort(
        (a, b) => priorityGroupOf(a) - priorityGroupOf(b) || severityRankOf(a) - severityRankOf(b) || byOldest(a, b),
      )
    case 'repo':
      // `repo_id` breaks a label tie before anything else, even though the reader never sees an id: the
      // group key is the repo_id, not the label, so two repositories the user happened to name the same
      // thing (a clone and a worktree of one project) would otherwise interleave, and the run-length
      // split in `buildGroups` would emit that repo's key twice with the other's cards in between.
      // Comparing ids is invisible when labels differ and is the only thing that keeps each repository's
      // cards in one run when they do not. `Projection.sort_actions` breaks the tie on the same key, and
      // the two agree on its order because a repo_id is `r_` plus lowercase hex, which the collator and
      // Python's code point comparison rank identically.
      return c.sort(
        (a, b) =>
          collator.compare(a.repo.label, b.repo.label) ||
          a.repo.repo_id.localeCompare(b.repo.repo_id) ||
          priorityGroupOf(a) - priorityGroupOf(b) ||
          byOldest(a, b),
      )
    case 'type':
      return c.sort(
        (a, b) =>
          priorityGroupOf(a) - priorityGroupOf(b) ||
          a.queue_type.localeCompare(b.queue_type) ||
          severityRankOf(a) - severityRankOf(b) ||
          byOldest(a, b),
      )
    case 'oldest':
      return c.sort(byOldest)
  }
}

/** The bucket a card belongs to in this mode — the same key `Projection.group_key` emits. */
export function groupKey(card: ActionCard, organize: Organize): string {
  switch (organize) {
    case 'priority':
      return `group.${priorityGroupOf(card)}`
    case 'repo':
      return card.repo.repo_id
    case 'type':
      return card.queue_type
    case 'oldest':
      return 'oldest'
  }
}
