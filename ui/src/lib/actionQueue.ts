import type { ActionCard, ActionsResponse } from './types'

const CLOSED: ReadonlySet<ActionCard['status']> = new Set(['StateChanged', 'ResolvedNoTransition', 'Cancelled'])

export function isClosedAction(card: Pick<ActionCard, 'status'>): boolean {
  return CLOSED.has(card.status)
}

/** Older cached responses lack repository health; current responses always carry it. */
export function isRepositoryUnavailable(card: Pick<ActionCard, 'repo'>): boolean {
  return card.repo.availability !== undefined && card.repo.availability !== 'available'
}

/** These records remain available for delivery tracking, but are not waiting for another decision. */
const NOT_WAITING: ReadonlySet<ActionCard['status']> = new Set([
  'Draft', 'Delivering', 'Delivered', 'Processing', ...CLOSED,
])

/**
 * Present the human queue without changing the transport inventory.
 *
 * The original response still drives fast polling, the workflow map and selected-action details.
 * Uncertain, undelivered and failed records stay visible so a send never disappears when it needs help.
 */
export function forActionQueue(response: ActionsResponse): ActionsResponse {
  const removed = response.actions.filter((card) => NOT_WAITING.has(card.status) || card.repo.archived === true)
  if (removed.length === 0) return response

  const hidden = new Set(removed.map((card) => card.action_id))
  const counts = { ...response.counts }
  for (const card of removed) {
    counts.total = Math.max(0, counts.total - 1)
    counts[card.severity] = Math.max(0, counts[card.severity] - 1)
  }
  return {
    ...response,
    actions: response.actions.filter((card) => !hidden.has(card.action_id)),
    counts,
    groups: response.groups
      .map((group) => ({ ...group, action_ids: group.action_ids.filter((id) => !hidden.has(id)) }))
      .filter((group) => group.action_ids.length > 0),
  }
}
