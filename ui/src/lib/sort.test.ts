/**
 * Queue order, pinned against the backend's own golden order.
 *
 * The fixtures and the four expected orders below are the same ones `tests/test_projection.py`
 * (`_queue`, `test_sort_actions_*_golden_order`) asserts against `Projection.sort_actions`. That is the
 * point of the file: the server sorts and sends `groups[]`, the client re-sorts after filtering, and if
 * the two comparators disagree a card appears to jump the moment a poll lands.
 */

import { describe, expect, it } from 'vitest'

import { groupKey, PRIORITY_GROUP, SEVERITY_RANK, sortActions } from './sort'
import type { ActionCard, ActionType, Organize, Severity } from './types'

const collator = new Intl.Collator('en-US', { numeric: true, sensitivity: 'base' })

/**
 * A card with only the fields the comparator reads.
 *
 * Cast once, here: a full `ActionCard` is ~60 fields of evidence and delivery state, and spelling them
 * out per fixture would bury the four values the sort actually depends on.
 */
function card(
  action_id: string,
  queue_type: ActionType,
  severity: Severity,
  waiting_since: string,
  repo: { repo_id: string; label: string },
): ActionCard {
  return {
    action_id,
    type: queue_type,
    queue_type,
    severity,
    waiting_since,
    repo: { ...repo, canonical_path: `/${repo.repo_id}` },
  } as unknown as ActionCard
}

const ZULU = { repo_id: 'r_z', label: 'Zulu' }
const ALFA = { repo_id: 'r_a', label: 'alfa' }

/** One card per priority band, deliberately inserted in the wrong order (`_queue()` in Python). */
const queue = (): ActionCard[] => [
  card('a_08', 'run', 'info', '2026-09-04T08:00:00Z', ZULU),
  card('a_07', 'revision', 'info', '2026-09-04T07:00:00Z', ALFA),
  card('a_06', 'install_conflict', 'attention', '2026-09-04T06:00:00Z', ZULU),
  card('a_05', 'failure', 'attention', '2026-09-04T05:00:00Z', ALFA),
  card('a_04', 'question', 'blocking', '2026-09-04T04:00:00Z', ZULU),
  card('a_03', 'gate', 'blocking', '2026-09-04T03:00:00Z', ALFA),
  card('a_02', 'delivery_uncertain', 'critical', '2026-09-04T02:00:00Z', ZULU),
  card('a_01', 'recovery', 'critical', '2026-09-04T01:00:00Z', ALFA),
]

const ids = (cards: ActionCard[], organize: Organize) =>
  sortActions(cards, organize, collator).map((c) => c.action_id)

/** The group keys in the order `buildGroups` splits on: one entry per CONSECUTIVE run of the same key. */
const runs = (cards: ActionCard[], organize: Organize) =>
  cards.map((c) => groupKey(c, organize)).filter((key, at, all) => key !== all[at - 1])

describe('the priority tables', () => {
  it('are the four FR-ACT-003 bands, byte-for-byte projection.PRIORITY_GROUP', () => {
    expect(PRIORITY_GROUP).toEqual({
      recovery: 1, delivery_uncertain: 1,
      gate: 2, question: 2, missing_input: 2,
      circuit_breaker: 3, failure: 3, install_conflict: 3,
      budget_stop: 4, revision: 4, run: 4, resume: 4, force_stop: 4, prepare_commit: 4,
    })
    expect(SEVERITY_RANK).toEqual({ critical: 0, blocking: 1, attention: 2, info: 3 })
  })
})

describe('sortActions', () => {
  it('priority: the backend golden order', () => {
    expect(ids(queue(), 'priority')).toEqual(['a_01', 'a_02', 'a_03', 'a_04', 'a_05', 'a_06', 'a_07', 'a_08'])
  })

  it('equal priority is oldest first, then action_id', () => {
    const same = [
      card('a_bb', 'gate', 'blocking', '2026-09-04T03:00:00Z', { repo_id: 'r', label: 'r' }),
      card('a_aa', 'gate', 'blocking', '2026-09-04T03:00:00Z', { repo_id: 'r', label: 'r' }),
      card('a_cc', 'gate', 'blocking', '2026-09-04T01:00:00Z', { repo_id: 'r', label: 'r' }),
    ]
    expect(ids(same, 'priority')).toEqual(['a_cc', 'a_aa', 'a_bb'])
  })

  it('repo: label first, case-insensitively, then band', () => {
    expect(ids(queue(), 'repo')).toEqual(['a_01', 'a_03', 'a_05', 'a_07', 'a_02', 'a_04', 'a_06', 'a_08'])
  })

  it('repo: two repositories that share a label stay in one run each, ordered by repo_id', () => {
    // The adversary is a person with two checkouts of the same project, a clone and a worktree, both
    // labelled "platform". Groups are keyed by `repo_id`, not by the label, so a sort that stopped at
    // the label would put one checkout's gate between the other's two cards; the queue would then show
    // the label twice with the wrong cards under each heading.
    const clone = { repo_id: 'r_aaa', label: 'platform' }
    const worktree = { repo_id: 'r_bbb', label: 'platform' }
    const shared = [
      card('a_gate_clone', 'gate', 'blocking', '2026-09-04T03:00:00Z', clone),
      card('a_run_clone', 'run', 'info', '2026-09-04T04:00:00Z', clone),
      card('a_gate_tree', 'gate', 'blocking', '2026-09-04T01:00:00Z', worktree),
      card('a_run_tree', 'run', 'info', '2026-09-04T02:00:00Z', worktree),
    ]
    const sorted = sortActions(shared, 'repo', collator)
    expect(runs(sorted, 'repo')).toEqual(['r_aaa', 'r_bbb'])
    // Within a repository the band still wins over the boundary time, as it does when labels differ.
    expect(sorted.map((c) => c.action_id)).toEqual(['a_gate_clone', 'a_run_clone', 'a_gate_tree', 'a_run_tree'])
  })

  it('type: band, then the type name, then severity', () => {
    expect(sortActions(queue(), 'type', collator).map((c) => c.queue_type)).toEqual([
      'delivery_uncertain', 'recovery', 'gate', 'question', 'failure', 'install_conflict', 'revision', 'run',
    ])
  })

  it('oldest: the boundary time alone, ignoring priority', () => {
    expect(ids(queue(), 'oldest')).toEqual(['a_01', 'a_02', 'a_03', 'a_04', 'a_05', 'a_06', 'a_07', 'a_08'])
    expect(ids([...queue()].reverse(), 'oldest')).toEqual(ids(queue(), 'oldest'))
  })

  it('sorts on waiting_since, not created_at', () => {
    // A card re-derived after a restart has a new `created_at` and the boundary's own `waiting_since`;
    // sorting on the wrong one would put an hour-old gate behind a fresh one (C20).
    const cards = queue().map((c) => ({ ...c, created_at: '2026-09-04T09:00:00Z' }) as ActionCard)
    expect(ids(cards, 'oldest')).toEqual(['a_01', 'a_02', 'a_03', 'a_04', 'a_05', 'a_06', 'a_07', 'a_08'])
  })

  it('does not mutate its input', () => {
    const cards = queue()
    const before = cards.map((c) => c.action_id)
    sortActions(cards, 'priority', collator)
    expect(cards.map((c) => c.action_id)).toEqual(before)
  })

  it('ranks a type this bundle predates last instead of crashing', () => {
    // Band 4 and newest, so "last" is unambiguous: an unrankable card must not vanish or throw.
    const unknown = card('a_99', 'made_up' as ActionType, 'info', '2026-09-05T00:00:00Z', ALFA)
    expect(ids([unknown, ...queue()], 'priority').at(-1)).toBe('a_99')
  })
})

describe('groupKey', () => {
  it('matches Projection.group_key for every mode', () => {
    const gate = card('a_03', 'gate', 'blocking', '2026-09-04T03:00:00Z', ALFA)
    expect(groupKey(gate, 'priority')).toBe('group.2')
    expect(groupKey(gate, 'repo')).toBe('r_a')
    expect(groupKey(gate, 'type')).toBe('gate')
    expect(groupKey(gate, 'oldest')).toBe('oldest')
  })
})
