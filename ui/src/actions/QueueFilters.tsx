/**
 * The queue header: what this list is, how much of it you are seeing, and how it is arranged.
 *
 * The organize choice is persisted (FR-ACT-005) in `localStorage['aidlc-studio:organize']` — the key
 * `host.ts` already names, so Settings and the queue read the same slot — and in `settings.queue_organize`,
 * which is what a browser with nothing stored inherits (§3.2: "localStorage, else settings"). `useOrganize`
 * is exported because the choice belongs to the view (it changes the sort of the list, which lives above
 * this component) while the control that changes it belongs here.
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

function isOrganize(value: unknown): value is Organize {
  return ORGANIZE_MODES.includes(value as Organize)
}

/** This browser's own choice, or `null` when it has none (or storage is disabled). */
function readOrganize(): Organize | null {
  try {
    const stored = localStorage.getItem(STUDIO_ORGANIZE_KEY)
    return isOrganize(stored) ? stored : null
  } catch {
    return null // storage disabled: the choice still works for this session
  }
}

/**
 * The persisted organize mode and a setter that persists it: this browser's choice, else `saved` (the
 * server's `settings.queue_organize`), else priority. `persist` writes a new choice to the server too.
 */
export function useOrganize(
  saved?: Organize | null,
  persist?: (mode: Organize) => void,
): [Organize, (mode: Organize) => void] {
  // Read lazily rather than in a module constant: `localStorage` may throw, and the value must be read
  // after the host has restored it rather than at import time.
  const [local, setOrganize] = useState<Organize | null>(readOrganize)

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
    persist?.(mode)
  }, [persist])

  return [local ?? (isOrganize(saved) ? saved : 'priority'), choose]
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
