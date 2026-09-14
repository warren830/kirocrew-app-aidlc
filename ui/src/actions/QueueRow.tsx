/**
 * One queue row: everything FR-ACT-006 requires, in the space the visual spec gives it.
 *
 * Three decisions worth stating:
 *
 *  1. **`React.memo` with a card-identity comparison.** The queue re-reads `/actions` every 2 seconds
 *     while anything is in flight. Without memoisation, 200 rows re-render on every poll and the list
 *     stutters while the user is trying to click one. The comparison is on the fields a row paints plus
 *     `updated_at`, so a real change always repaints and an identical poll costs nothing.
 *  2. **The row is a `<button>` inside a `<li>`.** A listbox would have to own focus management, and
 *     its `role="option"` children may not contain interactive content — but a row is exactly one
 *     activation target, which is what a button already is. Selection is announced with `aria-current`,
 *     the attribute for "this is the one you are looking at".
 *  3. **Severity is encoded three times** — the left rule (`data-severity`), the type icon, and the
 *     words in the accessible label — so it survives colour blindness, a monochrome display and a
 *     screen reader (PRD §10.1/§10.3).
 *
 * A row whose evidence is being re-read says so and is marked `data-refreshing`. It stays selectable:
 * refusing to open it would hide the evidence the user needs to understand why it cannot be submitted.
 * The refusal lives on the send control (FR-ACT-009).
 */

import { memo, type ReactNode } from 'react'

import { Chip, type ChipTone } from '../shell/Chip'
import { Icon, type IconName } from '../shell/Icon'
import { useI18n } from '../i18n'
import { isRepositoryUnavailable } from '../lib/actionQueue'
import { ref, waiting } from '../lib/format'
import { DEFAULT_SPACE } from '../lib/route'
import type { ActionCard, ActionType, Severity } from '../lib/types'

/** Action type → the glyph the whole app uses for it (visual spec §3.1, §11). */
export const TYPE_ICON: Record<ActionType, IconName> = {
  gate: 'gate',
  question: 'question',
  missing_input: 'missingInput',
  recovery: 'recovery',
  delivery_uncertain: 'recovery',
  failure: 'fail',
  circuit_breaker: 'fail',
  install_conflict: 'install',
  budget_stop: 'clock',
  revision: 'doc',
  run: 'play',
  resume: 'play',
  force_stop: 'pause',
  prepare_commit: 'git',
}

/** Severity → chip tone (visual spec §3.2). `info` stays neutral: it is not a warning. */
export const SEVERITY_TONE: Record<Severity, ChipTone> = {
  critical: 'danger',
  blocking: 'accent',
  attention: 'warn',
  info: 'neutral',
}

export function typeIconOf(card: ActionCard): IconName {
  return TYPE_ICON[card.queue_type] ?? TYPE_ICON[card.type] ?? 'info'
}

/**
 * Is this card's evidence being re-read right now?
 *
 * `captured.stable` is the size/mtime/hash recheck from the snapshot the card was built from;
 * `evidence.state.stable` is the same check on the state file specifically. Either one false means a
 * decision taken now would be taken against bytes that are changing (FR-ACT-009).
 */
export function isRefreshing(card: ActionCard): boolean {
  return card.captured.stable === false || card.evidence.state?.stable === false
}

/** `2.3 functional-design`, or just the slug, or the unavailable string. */
export function stageText(card: ActionCard, unavailableText: string): string {
  const stage = card.stage
  if (!stage) return unavailableText
  const parts = [stage.number, stage.slug || stage.name].filter((part): part is string => !!part)
  const base = parts.length > 0 ? parts.join(' ') : unavailableText
  return stage.unit ? `${base} · ${stage.unit}` : base
}

/** `checkout-web` or `checkout-web · staging` — the space only when it is not the default one. */
export function repoText(card: ActionCard): string {
  const label = card.repo.label || card.repo.repo_id
  return card.space && card.space !== DEFAULT_SPACE ? `${label} · ${card.space}` : label
}

export function intentText(card: ActionCard): string {
  return card.intent.title || card.intent.slug || card.intent.intent_dir
}

export interface QueueRowProps {
  card: ActionCard
  selected: boolean
  /** Roving tab order: only one row in the queue is a tab stop. */
  tabbable: boolean
  /**
   * The clock the waiting duration is measured against.
   *
   * Passed in rather than read from `Date.now()` inside the row: the row is memoised on the card, and a
   * card that has not changed would keep painting the duration it had when it first rendered — a gate
   * stuck at "41 min" for an hour. The list ticks this value, so every row re-renders together.
   */
  now: number
  onSelect: (actionId: string) => void
}

function Row({ card, selected, tabbable, now, onSelect }: QueueRowProps) {
  const i18n = useI18n()
  const { t } = i18n
  const refreshing = isRefreshing(card)
  const typeLabel = t(`enum.actionType.${card.queue_type}`)
  const severityLabel = t(`enum.severity.${card.severity}`)
  const unavailable = isRepositoryUnavailable(card)
  const wait = unavailable ? t('detail.repoUnavailable.saved') : waiting(i18n, card.waiting_since, now)
  const stage = stageText(card, t('common.unavailable'))
  const intent = intentText(card)
  const repo = repoText(card)
  // The headline is AI-DLC's own description of the boundary. When the catalogue has no entry for the
  // key the backend chose, the type name is used: a raw `action.gate.headline` in the product's home
  // list would read as breakage, and the type is at least true.
  const headline = unavailable ? t('detail.repoUnavailable.title') : i18n.has(card.headline.key) ? ref(i18n, card.headline) : typeLabel
  const primary = card.primary ? t(card.primary.label_key) : t('queue.noPrimary')

  return (
    <li className="studio-qrow">
      <button
        type="button"
        className="studio-qitem"
        data-severity={card.severity}
        data-refreshing={refreshing ? 'true' : undefined}
        data-action-id={card.action_id}
        aria-current={selected ? 'true' : undefined}
        tabIndex={tabbable ? 0 : -1}
        // Type, repo (with space), intent, stage, state and the next action, in one string: PRD §10.3
        // requires all of it, and a screen reader user cannot see the row's columns.
        aria-label={t('a11y.queueRow', {
          type: typeLabel, repo, intent, stage, severity: severityLabel, duration: wait, primary,
        })}
        onClick={() => onSelect(card.action_id)}
      >
        <span className="studio-qtop">
          <span className="studio-qtype">
            <Icon name={typeIconOf(card)} size={13} />
            {typeLabel}
          </span>
          <span aria-hidden="true" className="studio-qdot">·</span>
          <span className="studio-trunc">{repo}</span>
          {refreshing ? (
            <Chip tone="warn" icon="refresh" className="studio-qrefresh">
              {t('queue.refreshing')}
            </Chip>
          ) : null}
        </span>

        <span className="studio-qtitle">{headline}</span>

        <span className="studio-qmeta studio-mono">
          <span className="studio-qwait" data-severity={card.severity}>{wait}</span>
          <MetaPart>{stage}</MetaPart>
          <MetaPart>{intent}</MetaPart>
        </span>

        <span className="studio-qnext">
          <Icon name="chevron" size={12} />
          {primary}
        </span>
      </button>
    </li>
  )
}

function MetaPart({ children }: { children: ReactNode }) {
  return (
    <>
      <span aria-hidden="true" className="studio-qdot">·</span>
      <span className="studio-trunc">{children}</span>
    </>
  )
}

/**
 * Repaint only when something a row shows actually changed.
 *
 * `updated_at` covers the evidence and delivery fields the row derives from; the rest are the values
 * painted directly. Comparing the whole card by reference would repaint everything on every poll,
 * because the JSON is freshly parsed each time.
 */
export const QueueRow = memo(Row, (a, b) =>
  a.selected === b.selected &&
  a.tabbable === b.tabbable &&
  a.now === b.now &&
  a.onSelect === b.onSelect &&
  a.card.action_id === b.card.action_id &&
  a.card.updated_at === b.card.updated_at &&
  a.card.status === b.card.status &&
  a.card.repo.availability === b.card.repo.availability &&
  a.card.repo.archived === b.card.repo.archived &&
  a.card.severity === b.card.severity &&
  a.card.queue_type === b.card.queue_type &&
  a.card.waiting_since === b.card.waiting_since &&
  a.card.captured.stable === b.card.captured.stable &&
  a.card.primary?.decision === b.card.primary?.decision,
)
