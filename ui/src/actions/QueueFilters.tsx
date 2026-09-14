/**
 * The queue header: what this list is, how much of it you are seeing, and how it is arranged.
 *
 * The organize choice is persisted (FR-ACT-005) in `localStorage['aidlc-studio:organize']` — the key
 * `host.ts` already names, so Settings and the queue read the same slot. `useOrganize` is exported
 * because the choice belongs to the view (it changes the sort of the list, which lives above this
 * component) while the control that changes it belongs here.
 *
 * `SegmentedControl` comes from the host UI kit so the four modes look and behave like every other
 * segmented control in the dashboard; the search box is the host's `SearchInput` for the same reason.
 * Nothing here re-implements a control the host already ships.
 */

import { useCallback, useEffect, useState } from 'react'
import { SearchInput, SegmentedControl } from '@kirocrew/app-sdk/ui'

import { useI18n } from '../i18n'
import { STUDIO_ORGANIZE_KEY } from '../lib/host'
import type { Organize } from '../lib/types'

const ORGANIZE_MODES: readonly Organize[] = ['priority', 'repo', 'type', 'oldest']

function readOrganize(): Organize {
  try {
    const stored = localStorage.getItem(STUDIO_ORGANIZE_KEY)
    return ORGANIZE_MODES.includes(stored as Organize) ? (stored as Organize) : 'priority'
  } catch {
    return 'priority' // storage disabled: the choice still works for this session
  }
}

/** The persisted organize mode and a setter that persists it. */
export function useOrganize(): [Organize, (mode: Organize) => void] {
  // Read lazily rather than in a module constant: `localStorage` may throw, and the value must be read
  // after the host has restored it rather than at import time.
  const [organize, setOrganize] = useState<Organize>(readOrganize)

  useEffect(() => {
    // Another tab (or Settings) changing the mode should not leave this queue on the old one.
    const onStorage = (event: StorageEvent) => {
      if (event.key === null || event.key === STUDIO_ORGANIZE_KEY) setOrganize(readOrganize())
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  const choose = useCallback((mode: Organize) => {
    setOrganize(mode)
    try {
      localStorage.setItem(STUDIO_ORGANIZE_KEY, mode)
    } catch {
      // Unpersisted is still applied.
    }
  }, [])

  return [organize, choose]
}

export interface QueueFiltersProps {
  organize: Organize
  onOrganize: (mode: Organize) => void
  query: string
  onQuery: (query: string) => void
  /** Rows currently rendered after filtering. */
  visible: number
  /** Rows waiting for human input before search filtering, or `null` before the first answer. */
  total: number | null
  /** True while a refetch is in flight over a list that is already on screen. */
  stale: boolean
}

export function QueueFilters({
  organize, onOrganize, query, onQuery, visible, total, stale,
}: QueueFiltersProps) {
  const { t, fmt } = useI18n()

  return (
    <div className="studio-queue-head">
      <div className="studio-queue-title">
        <h2>{t('queue.title')}</h2>
        {/* Polite, and only the counter: a queue that announced every row on every poll would talk
            over the user while they read the decision pane. */}
        <span className="studio-mono studio-queue-n" aria-live="polite" data-stale={stale ? 'true' : undefined}>
          {total === null
            ? t('common.loading')
            : t('queue.count', { visible: fmt.number(visible), total: fmt.number(total) })}
        </span>
      </div>

      <SearchInput
        className="studio-queue-search"
        value={query}
        placeholder={t('queue.filterPlaceholder')}
        aria-label={t('queue.filterLabel')}
        onChange={(event) => onQuery(event.currentTarget.value)}
      />

      <div className="studio-queue-organize" role="group" aria-label={t('queue.organizeLabel')}>
        <SegmentedControl<Organize>
          segments={ORGANIZE_MODES.map((mode) => ({ key: mode, label: t(`queue.organize.${mode}`) }))}
          value={organize}
          onChange={onOrganize}
          layoutId="aidlc-queue-organize"
          // The host control measures its PARENT to decide whether to collapse to icons; inside this
          // flex column that measurement is the full queue width, which would never collapse.
          collapse={false}
        />
      </div>
    </div>
  )
}
