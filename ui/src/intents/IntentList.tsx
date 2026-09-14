/**
 * The inventory itself: a table on a desktop, a list of cards on a phone, one ordering for both.
 *
 * The ordering is "the repository you are looking at, then the intent that needs you soonest". Repository
 * first because turns serialize per repository, so the rows that compete with each other belong together;
 * state second so a Gate waiting on a human is never below a completed intent. `intent_dir` breaks ties so
 * the list cannot reshuffle between two polls of the same data.
 */

import { useMemo, type ReactNode } from 'react'

import { useI18n, type I18n } from '../i18n'
import type { IntentState } from '../lib/enums.generated'
import type { IntentSummary } from '../lib/types'
import { IntentRow, IntentRowError } from './IntentRow'

/** Most-needs-a-human first. Every `IntentState` appears, so no state can fall to an accidental default. */
export const STATE_ORDER: readonly IntentState[] = [
  'ReconciliationRequired', 'WaitingForYou', 'CircuitOpen', 'Failed', 'RetryEligible', 'Interrupted',
  'Running', 'Queued', 'Parked', 'Paused', 'Idle', 'Completed', 'Archived',
]

function stateRank(state: IntentState): number {
  const rank = STATE_ORDER.indexOf(state)
  return rank < 0 ? STATE_ORDER.length : rank
}

export function sortIntents(intents: IntentSummary[], i18n: I18n): IntentSummary[] {
  return [...intents].sort(
    (a, b) =>
      i18n.fmt.compare(a.repo_label, b.repo_label) ||
      stateRank(a.operational_state) - stateRank(b.operational_state) ||
      i18n.fmt.compare(a.intent_dir, b.intent_dir),
  )
}

export interface IntentListProps {
  intents: IntentSummary[]
  /** Repositories whose intents could not be read, so their absence is never silent. */
  failures: { repo: string; message: string }[]
  narrow: boolean
  renderActions: (intent: IntentSummary) => ReactNode
}

export function IntentList({ intents, failures, narrow, renderActions }: IntentListProps) {
  const i18n = useI18n()
  const { t } = i18n
  const rows = useMemo(() => sortIntents(intents, i18n), [intents, i18n])

  if (narrow) {
    return (
      <ul className="studio-intent-cards" aria-label={t('intents.a11y.list')}>
        {failures.map((failure) => (
          <IntentRowError key={failure.repo} repo={failure.repo} message={failure.message} layout="card" />
        ))}
        {rows.map((intent) => (
          <IntentRow
            key={`${intent.repo_id}/${intent.intent_key}`}
            intent={intent}
            layout="card"
            actions={renderActions(intent)}
          />
        ))}
      </ul>
    )
  }

  return (
    <table className="studio-tbl studio-intent-table">
      <caption className="studio-sr">{t('intents.a11y.table')}</caption>
      <thead>
        <tr>
          <th scope="col">{t('intents.col.repo')}</th>
          <th scope="col">{t('intents.col.intent')}</th>
          <th scope="col">{t('intents.col.state')}</th>
          <th scope="col">{t('intents.col.stage')}</th>
          <th scope="col">{t('intents.col.keepMoving')}</th>
          <th scope="col">{t('intents.col.actions')}</th>
        </tr>
      </thead>
      <tbody>
        {failures.map((failure) => (
          <IntentRowError key={failure.repo} repo={failure.repo} message={failure.message} layout="table" />
        ))}
        {rows.map((intent) => (
          <IntentRow
            key={`${intent.repo_id}/${intent.intent_key}`}
            intent={intent}
            layout="table"
            actions={renderActions(intent)}
          />
        ))}
      </tbody>
    </table>
  )
}
