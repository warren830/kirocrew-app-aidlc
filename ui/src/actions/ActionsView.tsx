/**
 * The Action Center: the product's home (FR-ACT-001).
 *
 * It composes the queue and the detail pane and owns exactly the state that spans them — the organize
 * mode, the search text, and which card is selected. Everything else is read from the shell, on purpose:
 * `GET /actions` snapshots every intent on the server, so a second fetch here would double that cost and
 * let the nav badge and the list disagree about how much is waiting.
 *
 * Filtering and sorting happen on the client even though the server already sorted. The server's order
 * is the contract (`sortActions` is byte-compatible with `Projection.sort_actions`), but the search box
 * removes rows, and a group header left standing over a filtered-out card would claim work that is not
 * there. So: filter, then re-sort with the reader's own collator, then re-group.
 *
 * The selection is NOT cleared when the filter hides it. A deep link from Slack names an action, and the
 * user may have a scope or a search that excludes it; blanking the pane would answer a notification with
 * "nothing here". The row is simply not highlighted in the list.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Btn } from '@kirocrew/app-sdk/ui'

import { Icon } from '../shell/Icon'
import { useShellData } from '../shell/StudioApp'
import type { ViewProps } from '../shell/ViewRouter'
import { useI18n } from '../i18n'
import { forActionQueue } from '../lib/actionQueue'
import { sortActions } from '../lib/sort'
import type { ActionCard } from '../lib/types'
import { ArtifactView } from '../map/ArtifactView'
import { DetailShell } from './DetailShell'
import { QueueFilters, useOrganize } from './QueueFilters'
import { QueueList } from './QueueList'
import { intentText, repoText, stageText } from './QueueRow'

/** Every word a search should be able to find a row by. */
function haystack(card: ActionCard, typeLabel: string, headline: string): string {
  return [
    typeLabel,
    headline,
    repoText(card),
    intentText(card),
    card.intent.slug,
    card.stage?.slug ?? '',
    card.stage?.number ?? '',
    card.stage?.name ?? '',
    card.stage?.unit ?? '',
    card.action_id,
  ]
    .join(' ')
    .toLowerCase()
}

export function ActionsView({ route, go }: ViewProps) {
  const i18n = useI18n()
  const { t } = i18n
  const { api, actions, settings, setQueueCount } = useShellData()
  const [organize, setOrganize] = useOrganize()
  const [query, setQuery] = useState('')

  const queue = useMemo(() => actions.data ? forActionQueue(actions.data) : null, [actions.data])
  const cards = queue?.actions ?? []
  const total = queue?.counts.total ?? null

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    const matching = needle
      ? cards.filter((card) => {
          const typeLabel = t(`enum.actionType.${card.queue_type}`)
          const headline = i18n.has(card.headline.key) ? t(card.headline.key, {}) : typeLabel
          return haystack(card, typeLabel, headline).includes(needle)
        })
      : cards
    // The reader's collator, not the server's casefold: repo grouping should put `Ärger` where a German
    // reader expects it (§3.5). Order *within* a group is identical on both sides.
    return sortActions(matching, organize, new Intl.Collator(i18n.locale, { numeric: true, sensitivity: 'base' }))
  }, [cards, query, organize, i18n, t])

  /*
   * The nav badge.
   *
   * Reported only while a local filter is narrowing the list (visual spec §1.2: the badge reflects what
   * is visible). Without a search filter the shell applies the same waiting-status projection to the
   * shared response. Delivery tracking is kept in the original resource, outside this human queue.
   */
  const filtering = query.trim().length > 0
  useEffect(() => {
    setQueueCount(filtering && actions.data ? visible.length : null)
    return () => setQueueCount(null)
  }, [filtering, visible.length, actions.data, setQueueCount])

  const select = useCallback(
    (actionId: string) => {
      // `tab` resets and `artifact` clears: the previous card's Review tab may not exist on this one, and
      // an artifact id from another intent would 404.
      go({ action: actionId, tab: 'decision', artifact: '', anchor: '' })
    },
    [go],
  )

  const selectedCard = useMemo(
    () => actions.data?.actions.find((card) => card.action_id === route.action) ?? null,
    [actions.data, route.action],
  )

  const grouped = settings.data?.capabilities.grouped_answers?.available === true

  // Map links clear action before opening this reader. With an action selected, artifact IDs belong
  // to that card's captured evidence; route.stage/unit may still describe a previous map selection.
  if (!route.action && route.tab === 'artifacts' && route.artifact && route.repo && route.intent && route.stage) {
    return (
      <ArtifactView
        key={`${route.repo}:${route.intent}:${route.stage}:${route.unit}`}
        api={api}
        route={route}
        go={go}
      />
    )
  }

  return (
    <div className="studio-ac">
      <aside className="studio-queue" aria-label={t('queue.label')}>
        <QueueFilters
          organize={organize}
          onOrganize={setOrganize}
          query={query}
          onQuery={setQuery}
          visible={visible.length}
          total={total}
          stale={actions.stale}
        />

        {actions.error && !actions.data ? (
          <div className="studio-queue-list studio-queue-empty">
            <div className="studio-empty" data-layout="stack">
              <Icon name="warn" size={18} />
              <p className="studio-empty-title">{t(`errors.${actions.error.code}`)}</p>
              <Btn onClick={() => void actions.refresh()}>{t('common.retry')}</Btn>
            </div>
          </div>
        ) : (
          <QueueList
            cards={visible}
            organize={organize}
            selected={route.action}
            onSelect={select}
            emptyBecauseNothingWaits={cards.length === 0}
            emptyAction={
              <Btn onClick={() => go({ view: 'map' })}>
                <Icon name="map" size={14} />
                {t('queue.empty.action')}
              </Btn>
            }
          />
        )}
      </aside>

      <DetailShell
        actionId={route.action}
        queueCard={selectedCard}
        api={api}
        route={route}
        go={go}
        groupedAnswers={grouped}
        onQueueChanged={actions.refresh}
      />
    </div>
  )
}

export default ActionsView
