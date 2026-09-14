/**
 * The pure reducers — the functions that turn a server payload plus a user's input into what the page
 * does next (contracts §4, `reducers.test.ts`).
 *
 * Each one is tested here rather than through a component because each is shared by several areas and
 * each has a failure that a rendering test would not notice:
 *
 *  - `looksBusy` decides between a 2-second and a 15-second poll. Answering "busy" for an idle install
 *    hammers the gateway forever; answering "idle" while a decision is `Delivering` leaves the user
 *    watching a stale card during the one window where seconds matter.
 *  - `tabsFor` decides which detail tabs EXIST. A Review tab with no findings implies the reviewer
 *    passed; an Artifacts tab over nothing teaches the user to stop looking.
 *  - `buildGroups` splits an already-sorted queue into consecutive runs. A bucketing implementation
 *    would still render, with the groups quietly in a different order from the server's.
 *  - `beforeSend` decides whether Cancel is honest. True after the host already has the message would
 *    offer to undo something that cannot be undone.
 */

import { describe, expect, it } from 'vitest'

import { tabsFor } from '../actions/DetailTabs'
import { buildGroups } from '../actions/QueueGroups'
import { beforeSend, type SubmitStage } from '../actions/useSubmit'
import { makeI18n } from '../i18n'
import { actionCard, artifactMeta, reviewFinding } from '../test/fixtures'
import { ACTION_STATUSES } from './enums.generated'
import { sortActions } from './sort'
import type { ActionCard, ActionDetailResponse, Organize } from './types'
import { looksBusy } from './useResource'

const en = makeI18n('en-US')
const collator = new Intl.Collator('en-US')

const detail = (over: Partial<ActionDetailResponse> = {}): ActionDetailResponse => ({
  action: actionCard(),
  transitions: [],
  drafts: [],
  ...over,
})

describe('looksBusy — the poll-pace reducer', () => {
  it('says nothing is moving for an empty or unknown payload', () => {
    // The default is the cheap one on purpose: an unrecognised shape must not pin a 2-second poll.
    for (const value of [null, undefined, 0, '', 'Delivering', [], {}, { actions: [] }, { intents: [] }]) {
      expect(looksBusy(value)).toBe(false)
    }
  })

  it('recognises a live execution count from /leases', () => {
    expect(looksBusy({ leases: [], global_concurrency_cap: 2, live_execution: 1 })).toBe(true)
    expect(looksBusy({ leases: [], global_concurrency_cap: 2, live_execution: 0 })).toBe(false)
  })

  it('recognises exactly the three in-flight card statuses, in a list and on its own', () => {
    // Exhaustive over the generated enum, so a status added by the backend has to be classified here
    // rather than defaulting into the fast poll (or out of it).
    const busy = ACTION_STATUSES.filter((status) => looksBusy({ actions: [actionCard({ status })] }))
    expect(busy).toEqual(['Delivering', 'Delivered', 'Processing'])
    for (const status of busy) expect(looksBusy({ action: actionCard({ status }) })).toBe(true)
    // `Queued` is the resting state of the whole queue: polling it fast would mean polling forever.
    expect(looksBusy({ actions: [actionCard({ status: 'Queued' })] })).toBe(false)
  })

  it('recognises a running session behind an otherwise resting card', () => {
    const running = actionCard({
      evidence: {
        ...actionCard().evidence,
        session: {
          slot_key: 'aidlc-r1-guest', session_key: 'ses_1c4', running: true, stop_state: 'running',
          last_turn_ts: null, queue_depth: 0, busy_reasons: [],
        },
      },
    })
    expect(running.status).toBe('Queued')
    expect(looksBusy({ actions: [running] })).toBe(true)
  })

  it('recognises a running or queued intent, nested or bare', () => {
    expect(looksBusy({ intents: [{ operational_state: 'Running' }] })).toBe(true)
    expect(looksBusy({ intents: [{ operational_state: 'Queued' }] })).toBe(true)
    expect(looksBusy({ intent: { operational_state: 'Running' } })).toBe(true)
    expect(looksBusy({ operational_state: 'Running' })).toBe(true)
    expect(looksBusy({ operational_state: 'WaitingForYou' })).toBe(false)
    expect(looksBusy({ intent: { session: { running: true } } })).toBe(true)
  })

  it('recognises an install transaction that has not settled, and only that', () => {
    for (const status of ['previewed', 'staging', 'applying', 'rolling_back']) {
      expect(looksBusy({ transaction: { status } })).toBe(true)
    }
    for (const status of ['committed', 'rolled_back', 'failed']) {
      expect(looksBusy({ transaction: { status } })).toBe(false)
    }
  })
})

describe('tabsFor — which detail tabs exist', () => {
  it('always offers Decision, and Activity even with no transitions yet', () => {
    // Activity with a null count renders without a number rather than as "0": the transitions are not
    // known until the detail read lands, and "0 transitions" would be a claim.
    expect(tabsFor(actionCard(), null)).toEqual([
      { tab: 'decision', count: null },
      { tab: 'activity', count: null },
      { tab: 'conversation', count: null },
    ])
  })

  it('offers Artifacts only when the card captured some, counting install drift with them', () => {
    const withArtifacts = actionCard({
      evidence: { ...actionCard().evidence, artifacts: [artifactMeta(), artifactMeta({ artifact_id: 'art_2' })] },
    })
    expect(tabsFor(withArtifacts, null).find((entry) => entry.tab === 'artifacts')).toEqual({
      tab: 'artifacts',
      count: 2,
    })
    expect(tabsFor(actionCard(), null).some((entry) => entry.tab === 'artifacts')).toBe(false)
  })

  it('offers Review only when there are findings — a verdict with none is not a Review tab', () => {
    const base = actionCard().evidence
    const noFindings = actionCard({
      evidence: {
        ...base,
        review: { verdict: 'approved', findings: [], reviewer: 'r', review_class: 'advisory', iteration: 1 },
      },
    })
    expect(noFindings.evidence.review).not.toBeNull()
    expect(tabsFor(noFindings, null).some((entry) => entry.tab === 'review')).toBe(false)

    const withFindings = actionCard({
      evidence: {
        ...base,
        review: {
          verdict: 'changes_requested', findings: [reviewFinding()], reviewer: 'r',
          review_class: 'adversarial', iteration: 2,
        },
      },
    })
    expect(tabsFor(withFindings, null).find((entry) => entry.tab === 'review')).toEqual({ tab: 'review', count: 1 })
  })

  it('drops Conversation when no session is bound, and counts the transitions once they are read', () => {
    const unbound = actionCard({ evidence: { ...actionCard().evidence, session: null } })
    expect(tabsFor(unbound, null).some((entry) => entry.tab === 'conversation')).toBe(false)
    const read = detail({
      transitions: [
        { from_status: null, to_status: 'Queued', generation: 1, at: '2026-09-04T09:00:00Z', reason: null, evidence: {} },
      ],
    })
    expect(tabsFor(actionCard(), read).find((entry) => entry.tab === 'activity')).toEqual({
      tab: 'activity',
      count: 1,
    })
  })

  it('keeps the PRD §8.3 order whatever the evidence contains', () => {
    const full = actionCard({
      evidence: {
        ...actionCard().evidence,
        artifacts: [artifactMeta()],
        review: {
          verdict: 'changes_requested', findings: [reviewFinding()], reviewer: 'r',
          review_class: 'adversarial', iteration: 1,
        },
      },
    })
    expect(tabsFor(full, detail()).map((entry) => entry.tab)).toEqual([
      'decision', 'artifacts', 'review', 'activity', 'conversation',
    ])
  })
})

describe('buildGroups — the queue partition', () => {
  const cards: ActionCard[] = [
    actionCard({ action_id: 'a_1', type: 'recovery', queue_type: 'recovery', severity: 'critical', waiting_since: '2026-09-04T03:00:00Z' }),
    actionCard({ action_id: 'a_2', type: 'gate', queue_type: 'gate', severity: 'blocking', waiting_since: '2026-09-04T05:00:00Z' }),
    actionCard({ action_id: 'a_3', type: 'question', queue_type: 'question', severity: 'blocking', waiting_since: '2026-09-04T04:00:00Z' }),
    actionCard({
      action_id: 'a_4', type: 'budget_stop', queue_type: 'budget_stop', severity: 'info',
      waiting_since: '2026-09-04T06:00:00Z',
      repo: { repo_id: 'r_2', label: 'payments-api', canonical_path: '/work/payments-api' },
    }),
  ]

  it('is a partition: every card lands in exactly one group, in the sorted order', () => {
    for (const organize of ['priority', 'repo', 'type', 'oldest'] as Organize[]) {
      const sorted = sortActions(cards, organize, collator)
      const groups = buildGroups(sorted, organize, en)
      const flattened = groups.flatMap((group) => group.cards)
      expect(flattened.map((card) => card.action_id)).toEqual(sorted.map((card) => card.action_id))
      expect(new Set(groups.map((group) => group.key)).size).toBe(groups.length)
    }
  })

  it('names the four FR-ACT-003 bands with the keys the backend also sends', () => {
    const groups = buildGroups(sortActions(cards, 'priority', collator), 'priority', en)
    expect(groups.map((group) => [group.key, group.label])).toEqual([
      ['group.1', en.t('queue.group.recovery')],
      ['group.2', en.t('queue.group.blocking')],
      ['group.4', en.t('queue.group.info')],
    ])
  })

  it('names a repo group with the user’s own label, never a translation', () => {
    const groups = buildGroups(sortActions(cards, 'repo', collator), 'repo', en)
    expect(groups.map((group) => group.label)).toEqual(['checkout-web', 'payments-api'])
    expect(groups.map((group) => group.key)).toEqual(['r_1', 'r_2'])
  })

  it('collapses to one group in oldest mode, so nothing implies a band', () => {
    const groups = buildGroups(sortActions(cards, 'oldest', collator), 'oldest', en)
    expect(groups).toHaveLength(1)
    expect(groups[0]?.cards.map((card) => card.action_id)).toEqual(['a_1', 'a_3', 'a_2', 'a_4'])
  })

  it('starts a new group when a sorted list revisits a key, rather than silently merging', () => {
    // An unsorted input is a programming error, and the run-length split makes it VISIBLE (two groups
    // with the same key) instead of reordering the user's queue behind their back.
    const unsorted = [cards[1]!, cards[0]!, cards[2]!]
    const groups = buildGroups(unsorted, 'priority', en)
    expect(groups.map((group) => group.key)).toEqual(['group.2', 'group.1', 'group.2'])
  })

  it('has nothing to divide when the filter emptied the queue', () => {
    expect(buildGroups([], 'priority', en)).toEqual([])
  })
})

describe('beforeSend — whether Cancel is still honest', () => {
  it('is true only while nothing has reached the host', () => {
    const stages: SubmitStage[] = [
      'idle', 'submitting', 'preflight', 'sending', 'reporting', 'settled', 'refused', 'stale',
    ]
    // Exhaustive over the union: a stage added later without a decision here fails this test rather
    // than defaulting to "safe to cancel".
    expect(stages.filter(beforeSend)).toEqual(['idle', 'submitting', 'preflight', 'refused', 'stale'])
    expect(stages.filter((stage) => !beforeSend(stage))).toEqual(['sending', 'reporting', 'settled'])
  })
})
