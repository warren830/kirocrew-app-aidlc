/**
 * The timeline: rows grouped by calendar day.
 *
 * Grouping is by day and nothing else, on purpose. `FR-EVT-001` asks for grouping by repo, intent, stage
 * and turn, but a `TimelineEntry` carries none of those as columns — only `params.stage` survives the
 * projection (§2.6). The repo and intent context therefore comes from the scope bar, which is where the
 * user set it, and the stage is shown per row. Inventing a "repo" group from a params key that may or may
 * not be there would produce headings that are sometimes right, which is worse than a heading that is
 * always the day.
 *
 * Semantics are a nested list rather than a table: every row has a different set of facts (a Git row has a
 * commit, an audit row has a shard and a position), so a table would be mostly empty cells, and PRD §10.3
 * accepts a list as the semantic alternative.
 */

import { useI18n } from '../i18n'
import type { TimelineEntry } from '../lib/types'
import { entryKey, TimelineRow } from './TimelineRow'

export interface TimelineProps {
  entries: TimelineEntry[]
  /** `entryKey` of the row the Evidence drawer is showing, or `null`. */
  selectedKey: string | null
  onOpenEvidence: (entry: TimelineEntry) => void
  onOpenAction?: (actionId: string) => void
  /** Accessible name for the whole timeline; the page and a detail tab want different words. */
  label?: string
}

interface DayGroup {
  /** The locale-formatted date, used as the heading and the group key. */
  day: string
  entries: TimelineEntry[]
}

export function Timeline({ entries, selectedKey, onOpenEvidence, onOpenAction, label }: TimelineProps) {
  const i18n = useI18n()
  const groups: DayGroup[] = []
  for (const entry of entries) {
    const day = i18n.fmt.date(entry.at)
    const last = groups[groups.length - 1]
    if (last && last.day === day) last.entries.push(entry)
    else groups.push({ day, entries: [entry] })
  }

  return (
    <ol className="studio-timeline" aria-label={label ?? i18n.t('activity.page.timeline')}>
      {groups.map((group) => (
        <li key={group.day} className="studio-timeline-day">
          <h3 className="studio-timeline-date">{group.day}</h3>
          <ol className="studio-timeline-rows">
            {group.entries.map((entry) => {
              const key = entryKey(entry)
              return (
                <TimelineRow
                  key={key}
                  entry={entry}
                  selected={key === selectedKey}
                  onOpenEvidence={onOpenEvidence}
                  {...(onOpenAction ? { onOpenAction } : {})}
                />
              )
            })}
          </ol>
        </li>
      ))}
    </ol>
  )
}
