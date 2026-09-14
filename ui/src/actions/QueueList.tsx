/**
 * The queue itself: grouped, windowed, keyboard-navigable.
 *
 * **Windowing.** 200 cards is a realistic registry (a 64-repo install with a few intents each), and
 * each row carries an icon, four chips' worth of text and an accessible label. Rendering all of them
 * makes the first paint slow and, worse, makes every 2-second poll re-reconcile 200 subtrees while the
 * user is trying to click one. So the list renders a window that starts at `CHUNK` rows and grows when
 * the scroller approaches its end. This is deliberately *not* fixed-height virtualisation: rows have
 * variable height (the headline wraps to two lines) and sticky group headings sit between them, so a
 * transform-based virtual list would need measured offsets and would mis-place a heading the moment a
 * title wrapped. Growing a window has no offsets to get wrong, keeps `position: sticky` working, and
 * bounds the DOM to what the user has actually scrolled through.
 *
 * **Keyboard.** One tab stop for the whole list (roving `tabIndex`), arrows to move focus, Enter or
 * Space (native button activation) to select. Arrow keys deliberately do NOT select: selection loads
 * the detail pane, and following focus would fire a request per keypress while someone scans the list.
 *
 * **The empty state** names the next useful thing to do (PRD §10.2) rather than saying "no data" — and
 * it distinguishes "nothing needs you" from "your filter matches nothing", because the second is the
 * user's own doing and the first is the product working.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { Icon } from '../shell/Icon'
import { useI18n } from '../i18n'
import type { ActionCard, Organize } from '../lib/types'
import { buildGroups, QueueGroupHeading } from './QueueGroups'
import { QueueRow } from './QueueRow'

/** Rows rendered before the first scroll, and rows added per growth step. */
export const CHUNK = 40
/** How close to the bottom (px) the scroller has to be before the window grows. */
const GROW_MARGIN_PX = 480
/** How often the waiting durations are recomputed. `fmt.duration` buckets to minutes, so this is ample. */
const TICK_MS = 30_000

export interface QueueListProps {
  /** Already sorted and filtered, in the order they must appear. */
  cards: ActionCard[]
  organize: Organize
  selected: string
  onSelect: (actionId: string) => void
  /** True when the unfiltered queue is empty, as opposed to the filter hiding everything. */
  emptyBecauseNothingWaits: boolean
  /** Rendered inside the "nothing needs you" state — the shell supplies the Workflow Map link. */
  emptyAction?: React.ReactNode
}

/** A wall clock that only ticks while the tab is visible. */
function useMinuteClock(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const tick = () => {
      if (typeof document === 'undefined' || !document.hidden) setNow(Date.now())
    }
    const timer = setInterval(tick, TICK_MS)
    document.addEventListener('visibilitychange', tick)
    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', tick)
    }
  }, [])
  return now
}

export function QueueList({
  cards, organize, selected, onSelect, emptyBecauseNothingWaits, emptyAction,
}: QueueListProps) {
  const { t, fmt } = useI18n()
  const now = useMinuteClock()
  const scroller = useRef<HTMLDivElement | null>(null)
  const [limit, setLimit] = useState(CHUNK)

  // A new list is a new window. Without this a search that narrows to 3 rows would keep a limit of 200
  // and a search that widens would show a window sized for the previous query.
  const listKey = `${organize}:${cards.length}:${cards[0]?.action_id ?? ''}`
  useEffect(() => setLimit(CHUNK), [listKey])

  const grow = useCallback(() => {
    const node = scroller.current
    if (!node) return
    // An unmeasurable container (never laid out, or hidden by the mobile master/detail rule) says
    // nothing about whether more rows are needed, and growing on a zero height would render the whole
    // list into a pane no one is looking at.
    if (node.clientHeight === 0) return
    if (node.scrollHeight - node.scrollTop - node.clientHeight > GROW_MARGIN_PX) return
    setLimit((current) => (current >= cards.length ? current : current + CHUNK))
  }, [cards.length])

  // A tall pane can fit more than CHUNK rows, and then no scroll event ever fires to grow the window.
  // Layout effect so the check runs against the measured height of what was just painted.
  useLayoutEffect(grow, [grow, limit, listKey])

  const windowed = useMemo(() => cards.slice(0, limit), [cards, limit])
  const groups = useI18nGroups(windowed, organize)

  // The single tab stop: the selected row, or the first one when the selection is not on screen.
  const tabbableId = useMemo(() => {
    if (windowed.some((card) => card.action_id === selected)) return selected
    return windowed[0]?.action_id ?? ''
  }, [windowed, selected])

  const onKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    const keys = ['ArrowDown', 'ArrowUp', 'Home', 'End']
    if (!keys.includes(event.key)) return
    const node = scroller.current
    if (!node) return
    const rows = [...node.querySelectorAll<HTMLButtonElement>('button.studio-qitem')]
    if (rows.length === 0) return
    const active = document.activeElement
    const at = rows.findIndex((row) => row === active)
    let next: number
    if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = rows.length - 1
    else if (at < 0) next = 0
    else next = event.key === 'ArrowDown' ? Math.min(rows.length - 1, at + 1) : Math.max(0, at - 1)
    // Only now: an unhandled arrow must still scroll the pane.
    event.preventDefault()
    rows[next]?.focus()
  }, [])

  if (cards.length === 0) {
    return (
      <div className="studio-queue-list studio-queue-empty">
        <div className="studio-empty" data-layout="stack">
          <Icon name={emptyBecauseNothingWaits ? 'check' : 'search'} size={18} />
          <p className="studio-empty-title">
            {t(emptyBecauseNothingWaits ? 'queue.empty.title' : 'queue.noMatch.title')}
          </p>
          <p className="studio-muted">
            {t(emptyBecauseNothingWaits ? 'queue.empty.body' : 'queue.noMatch.body')}
          </p>
          {emptyBecauseNothingWaits ? emptyAction : null}
        </div>
      </div>
    )
  }

  return (
    <div
      className="studio-queue-list"
      ref={scroller}
      onScroll={grow}
      onKeyDown={onKeyDown}
    >
      {groups.map((group, index) => {
        const headingId = `studio-qgroup-${index}-${group.key}`
        return (
          // The position is part of the key, not just the group's own key: `buildGroups` splits by runs,
          // so an order that puts two runs of the same group in the list (the server's collation, or a
          // sort key that stops short of the group key) would hand React duplicate siblings, and React
          // may then drop or merge one — a heading painted over another repository's cards.
          <section key={`${index}:${group.key}`} className="studio-qsection" aria-labelledby={headingId}>
            <QueueGroupHeading group={group} id={headingId} />
            <ul className="studio-qlist">
              {group.cards.map((card) => (
                <QueueRow
                  key={card.action_id}
                  card={card}
                  now={now}
                  selected={card.action_id === selected}
                  tabbable={card.action_id === tabbableId}
                  onSelect={onSelect}
                />
              ))}
            </ul>
          </section>
        )
      })}
      {limit < cards.length ? (
        // Not a spinner: nothing is loading. It tells a keyboard or screen-reader user that End does not
        // reach the last card yet, which is otherwise invisible.
        <p className="studio-queue-more studio-muted">{t('queue.more', { n: fmt.number(cards.length - limit) })}</p>
      ) : null}
    </div>
  )
}

/** Grouping needs the catalogue, and a hook cannot be called inside the render loop above. */
function useI18nGroups(cards: ActionCard[], organize: Organize) {
  const i18n = useI18n()
  return useMemo(() => buildGroups(cards, organize, i18n), [cards, organize, i18n])
}
