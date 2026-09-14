/**
 * The five detail tabs, in PRD §8.3 order, with counts and honest visibility.
 *
 * A tab is shown only when it has something behind it: an Artifacts tab that opens onto "nothing here"
 * teaches the user to stop looking, and a Review tab with no findings implies the reviewer passed when
 * it may never have run. Decision is always present — every card has a decision to explain, even when
 * the only decision is "acknowledge".
 *
 * The selected tab lives in the query string (`?tab=`), not in state, so a deep link restores it
 * (PRD §10.2) and the browser's Back button steps between tabs the way a user expects. The panel itself
 * is rendered by the caller; this component owns only the tablist, which is why it takes `counts`
 * rather than the data.
 */

import { useCallback, useMemo } from 'react'

import { Icon, type IconName } from '../shell/Icon'
import { useI18n } from '../i18n'
import { TABS, type Tab } from '../lib/route'
import type { ActionCard, ActionDetailResponse } from '../lib/types'

const TAB_ICON: Record<Tab, IconName> = {
  decision: 'inbox',
  artifacts: 'doc',
  review: 'review',
  activity: 'activity',
  conversation: 'slack',
}

export interface TabState {
  tab: Tab
  count: number | null
}

/**
 * Which tabs this card has, and how much is behind each.
 *
 * `null` means "no number to show" rather than zero: an Activity tab with one transition shows `1`, and
 * a Conversation tab shows nothing because a bound session is not a quantity.
 */
export function tabsFor(card: ActionCard, detail: ActionDetailResponse | null): TabState[] {
  const artifacts = card.evidence.artifacts.length + (card.install?.drift.length ?? 0)
  const findings = card.evidence.review?.findings.length ?? 0
  const transitions = detail?.transitions.length ?? null
  const out: TabState[] = [{ tab: 'decision', count: null }]
  if (artifacts > 0) out.push({ tab: 'artifacts', count: artifacts })
  if (findings > 0) out.push({ tab: 'review', count: findings })
  out.push({ tab: 'activity', count: transitions })
  if (card.evidence.session) out.push({ tab: 'conversation', count: null })
  return out
}

export interface DetailTabsProps {
  tabs: TabState[]
  active: Tab
  onSelect: (tab: Tab) => void
}

export function DetailTabs({ tabs, active, onSelect }: DetailTabsProps) {
  const { t, fmt } = useI18n()

  // A tab that no longer exists (the review findings were resolved while it was open) must not leave the
  // tablist with nothing selected, or arrow-key navigation has no anchor.
  const current = useMemo(
    () => (tabs.some((entry) => entry.tab === active) ? active : (tabs[0]?.tab ?? 'decision')),
    [tabs, active],
  )

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft' && event.key !== 'Home' && event.key !== 'End') return
      const order = tabs.map((entry) => entry.tab)
      const at = order.indexOf(current)
      let next: number
      if (event.key === 'Home') next = 0
      else if (event.key === 'End') next = order.length - 1
      else next = event.key === 'ArrowRight' ? (at + 1) % order.length : (at - 1 + order.length) % order.length
      event.preventDefault()
      const target = order[next]
      if (target) onSelect(target)
    },
    [current, onSelect, tabs],
  )

  return (
    <div className="studio-tabs" role="tablist" aria-label={t('detail.tabsLabel')} onKeyDown={onKeyDown}>
      {TABS.filter((tab) => tabs.some((entry) => entry.tab === tab)).map((tab) => {
        const entry = tabs.find((candidate) => candidate.tab === tab)
        const selected = tab === current
        return (
          <button
            key={tab}
            type="button"
            role="tab"
            id={`studio-tab-${tab}`}
            aria-selected={selected}
            aria-controls={`studio-panel-${tab}`}
            tabIndex={selected ? 0 : -1}
            className="studio-tab"
            onClick={() => onSelect(tab)}
          >
            <Icon name={TAB_ICON[tab]} size={13} />
            {t(`detail.tab.${tab}`)}
            {entry?.count !== null && entry?.count !== undefined ? (
              <span className="studio-tc studio-mono">{fmt.number(entry.count)}</span>
            ) : null}
          </button>
        )
      })}
    </div>
  )
}
