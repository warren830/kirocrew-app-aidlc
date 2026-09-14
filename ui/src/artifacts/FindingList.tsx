/**
 * Reviewer findings, quoted (FR-ART-005).
 *
 * Three rules the shape of this list encodes:
 *
 *  1. **A finding is the reviewer's words, verbatim.** Title and quote are AI-DLC-authored text and are
 *     never translated, never re-wrapped in markdown, never summarised. Only the level badge, the
 *     headings and the meta labels are Studio's.
 *  2. **An anchor is offered only when the backend recorded one.** The 2.7.1 reviewer writes its findings
 *     as a table with its own severity vocabulary, so `anchor` is usually `null` — the finding then
 *     degrades to a listed finding with a note saying no anchor exists, rather than to a link that
 *     scrolls nowhere. The table's `Location` cell reads like a target ("<path> > <section>") but it is
 *     the reviewer's prose, kept verbatim inside the title; `parse_review_section` records no anchor from
 *     a row, so this list must not synthesise one from it.
 *  3. **`unknown` is a level, not a bug.** The backend refuses to map Critical/Major/Minor onto Studio's
 *     blocking levels because that would invent authority. Those findings must therefore still be shown,
 *     grouped as "level not stated", or a blocker the reviewer wrote in a table would silently vanish.
 */

import { useEffect, useMemo, useRef } from 'react'

import { useI18n } from '../i18n'
import { usePrefersReducedMotion } from '../lib/host'
import type { ReviewFinding } from '../lib/types'
import { Chip, type ChipTone } from '../shell/Chip'
import { Icon, type IconName } from '../shell/Icon'
import { revealAnchor } from './ArtifactViewer'

export type FindingLevel = ReviewFinding['level']

/** Group order: what still blocks, then what advises, then what nobody classified, then what is closed. */
export const LEVEL_ORDER: readonly FindingLevel[] = ['blocker', 'advisory', 'unknown', 'resolved']

const LEVEL_TONE: Record<FindingLevel, ChipTone> = {
  blocker: 'danger',
  advisory: 'warn',
  resolved: 'ok',
  unknown: 'neutral',
}

const LEVEL_ICON: Record<FindingLevel, IconName> = {
  blocker: 'warn',
  advisory: 'info',
  resolved: 'check',
  unknown: 'info',
}

/** A finding plus its position in the whole list, which is what the route anchor `f-<n>` names. */
export interface IndexedFinding {
  finding: ReviewFinding
  /** 1-based index across the unfiltered list. */
  index: number
}

export function indexFindings(findings: readonly ReviewFinding[]): IndexedFinding[] {
  return findings.map((finding, i) => ({ finding, index: i + 1 }))
}

export function groupByLevel(findings: readonly ReviewFinding[]): Map<FindingLevel, IndexedFinding[]> {
  const groups = new Map<FindingLevel, IndexedFinding[]>()
  for (const level of LEVEL_ORDER) groups.set(level, [])
  for (const item of indexFindings(findings)) {
    const level = LEVEL_ORDER.includes(item.finding.level) ? item.finding.level : 'unknown'
    groups.get(level)?.push(item)
  }
  for (const level of LEVEL_ORDER) if (groups.get(level)?.length === 0) groups.delete(level)
  return groups
}

export interface FindingListProps {
  findings: readonly ReviewFinding[]
  /** Counted headings per level (Review tab). A flat list is right inside the gate compare pane. */
  grouped?: boolean
  /**
   * Open the artifact at this finding's section. Rendered only for findings that carry an anchor; the
   * caller is responsible for switching to the Artifacts tab (visual spec §5.14).
   */
  onOpenAnchor?: (anchor: string) => void
  /** Route anchor: `f-<n>` highlights that finding after render. */
  anchor?: string
  /** One line under the list saying where the text came from. */
  sourceNote?: string
}

export function FindingList({ findings, grouped = false, onOpenAnchor, anchor, sourceNote }: FindingListProps) {
  const { t, fmt } = useI18n()
  const reduced = usePrefersReducedMotion()
  const rootRef = useRef<HTMLDivElement | null>(null)
  const groups = useMemo(() => (grouped ? groupByLevel(findings) : null), [grouped, findings])
  const flat = useMemo(() => indexFindings(findings), [findings])

  // `f-3` in the URL must land on the third finding even when the list is grouped, which is why the
  // index travels with the item instead of being the position inside its group.
  useEffect(() => {
    if (!anchor || findings.length === 0) return
    revealAnchor(rootRef.current, anchor, { reduced })
  }, [anchor, findings.length, reduced])

  if (findings.length === 0) return null

  return (
    <div className="studio-findings" ref={rootRef}>
      {groups
        ? [...groups.entries()].map(([level, items]) => (
            <section className="studio-finding-group" key={level} aria-labelledby={`fg-${level}`}>
              <h4 id={`fg-${level}`}>
                <Icon name={LEVEL_ICON[level]} size={13} />
                {t(`review.group.${level}`, { n: fmt.number(items.length) })}
              </h4>
              {items.map((item) => (
                <FindingItem
                  key={item.index}
                  item={item}
                  onOpenAnchor={onOpenAnchor}
                  anchorable={!!onOpenAnchor}
                />
              ))}
            </section>
          ))
        : flat.map((item) => (
            <FindingItem key={item.index} item={item} onOpenAnchor={onOpenAnchor} anchorable={!!onOpenAnchor} />
          ))}

      {sourceNote ? (
        <p className="studio-muted studio-finding-source">
          <Icon name="lock" size={13} /> {sourceNote}
        </p>
      ) : null}
    </div>
  )
}

function FindingItem({
  item,
  onOpenAnchor,
  anchorable,
}: {
  item: IndexedFinding
  onOpenAnchor?: (anchor: string) => void
  anchorable: boolean
}) {
  const { t, fmt } = useI18n()
  const { finding, index } = item
  const level: FindingLevel = LEVEL_ORDER.includes(finding.level) ? finding.level : 'unknown'
  const meta: string[] = []
  if (finding.reviewer) meta.push(finding.reviewer)
  if (finding.iteration !== null) meta.push(t('review.iteration', { n: fmt.number(finding.iteration) }))

  return (
    <article className="studio-finding" data-level={level} id={`f-${index}`}>
      <div className="studio-finding-head">
        <Chip tone={LEVEL_TONE[level]} icon={LEVEL_ICON[level]}>
          {t(`review.level.${level}`)}
        </Chip>
        {meta.length > 0 ? <span className="studio-finding-meta studio-mono">{meta.join(' · ')}</span> : null}
        {finding.anchor && onOpenAnchor ? (
          <button
            type="button"
            className="studio-anchor-link"
            onClick={() => onOpenAnchor(finding.anchor as string)}
          >
            <Icon name="link" size={13} /> {t('review.inArtifact')}
          </button>
        ) : null}
      </div>
      {/* Reviewer text, verbatim. */}
      <p className="studio-finding-title">{finding.title}</p>
      {finding.quote ? <p className="studio-finding-quote">{finding.quote}</p> : null}
      {!finding.anchor && anchorable ? (
        <p className="studio-finding-noanchor studio-muted">{t('review.noAnchor')}</p>
      ) : null}
    </article>
  )
}
