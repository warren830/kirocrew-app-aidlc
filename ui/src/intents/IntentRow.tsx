/**
 * One intent, as a table row or as a card.
 *
 * Two layouts, one set of facts. Below the host's mobile breakpoint the table becomes a list of cards —
 * not a horizontally scrolling table and not a table with `display:block` rows, because both of those
 * strip the header association that makes a cell mean anything. PRD §10.3 asks for a semantic alternative
 * to the table; this is it, and the same component renders both so the two can never drift.
 *
 * Everything AI-DLC wrote — the intent directory name, the stage slug, the state file's own status — is
 * shown verbatim and monospaced. Studio's words are the state label, the counts and the reasons.
 */

import type { ReactNode } from 'react'

import { Chip, type ChipTone } from '../shell/Chip'
import { Icon, type IconName } from '../shell/Icon'
import { useI18n, type I18n } from '../i18n'
import { count, plural, waiting } from '../lib/format'
import type { IntentState } from '../lib/enums.generated'
import type { IntentSummary } from '../lib/types'

/** State → tone + icon (visual spec §3.7, extended to every `IntentState`). */
const STATE_LOOK: Record<IntentState, { tone: ChipTone; icon: IconName }> = {
  Idle: { tone: 'neutral', icon: 'clock' },
  Queued: { tone: 'neutral', icon: 'clock' },
  Running: { tone: 'ok', icon: 'play' },
  WaitingForYou: { tone: 'accent', icon: 'gate' },
  Paused: { tone: 'neutral', icon: 'pause' },
  Parked: { tone: 'neutral', icon: 'pause' },
  Interrupted: { tone: 'warn', icon: 'warn' },
  ReconciliationRequired: { tone: 'danger', icon: 'recovery' },
  RetryEligible: { tone: 'warn', icon: 'refresh' },
  CircuitOpen: { tone: 'warn', icon: 'fail' },
  Failed: { tone: 'danger', icon: 'fail' },
  Completed: { tone: 'ok', icon: 'check' },
  Archived: { tone: 'neutral', icon: 'doc' },
}

export function stateLook(state: IntentState): { tone: ChipTone; icon: IconName } {
  return STATE_LOOK[state] ?? { tone: 'neutral', icon: 'clock' }
}

/** The stage AI-DLC is on, in its own words, or the fact that it has not recorded one. */
export function stageText(i18n: I18n, intent: IntentSummary): string {
  return intent.disk.current_stage || i18n.t('intents.stage.none')
}

/** The whole row as one sentence, for a reader that is not walking the table cell by cell. */
export function rowSummary(i18n: I18n, intent: IntentSummary): string {
  const primary = intent.open_actions > 0 ? plural(i18n, 'intents.openActions', intent.open_actions) : ''
  return i18n.t('intents.a11y.row', {
    intent: intent.intent_dir,
    repo: intent.repo_label,
    state: i18n.t(`enum.intentState.${intent.operational_state}`),
    stage: stageText(i18n, intent),
    waiting: i18n.t('intents.waiting', { duration: waiting(i18n, intent.last_activity_at) }),
    primary,
  })
}

export interface IntentRowProps {
  intent: IntentSummary
  layout: 'table' | 'card'
  /** `IntentActions` for this row, built by the list so this component stays presentational. */
  actions: ReactNode
}

export function IntentRow({ intent, layout, actions }: IntentRowProps) {
  const i18n = useI18n()
  const { t } = i18n
  const look = stateLook(intent.operational_state)

  const identity = (
    <>
      <span className="studio-strong studio-mono studio-wrap-any">{intent.intent_dir}</span>
      {intent.title ? <span className="studio-row-sub">{intent.title}</span> : null}
      <span className="studio-sr">{rowSummary(i18n, intent)}</span>
      <span className="studio-row studio-wrap studio-row-chips">
        {intent.space !== 'default' ? <Chip mono>{intent.space}</Chip> : null}
        {intent.is_active_cursor ? <Chip icon="intent" tone="accent">{t('intents.cursorChip')}</Chip> : null}
        {intent.open_actions > 0 ? (
          <Chip icon="gate" tone="accent">{plural(i18n, 'intents.openActions', intent.open_actions)}</Chip>
        ) : null}
        {intent.blocking_findings > 0 ? (
          <Chip icon="warn" tone="danger">{plural(i18n, 'intents.blocking', intent.blocking_findings)}</Chip>
        ) : null}
        {intent.warn_findings > 0 ? (
          <Chip icon="warn" tone="warn">{plural(i18n, 'intents.warn', intent.warn_findings)}</Chip>
        ) : null}
        {intent.paused ? <Chip icon="pause">{t('intents.pausedChip')}</Chip> : null}
        {intent.archived ? <Chip icon="doc">{t('intents.archivedChip')}</Chip> : null}
        {/* An unstable read is not evidence; it is said in words, not only by a colour. */}
        {intent.unstable ? (
          <Chip icon="warn" tone="warn" title={t('intents.unstableWhy')}>
            {t('intents.unstable')}
          </Chip>
        ) : null}
      </span>
    </>
  )

  const state = (
    <>
      <Chip tone={look.tone} icon={look.icon}>
        {t(`enum.intentState.${intent.operational_state}`)}
      </Chip>
      <span className="studio-row-sub studio-mono">
        {t('intents.waiting', { duration: waiting(i18n, intent.last_activity_at) })}
      </span>
    </>
  )

  const stage = (
    <>
      <span className="studio-mono studio-wrap-any">{stageText(i18n, intent)}</span>
      <span className="studio-row-sub">
        {t('intents.stage.progress', {
          done: count(i18n, intent.counts.done),
          total: count(i18n, intent.counts.total),
        })}
      </span>
      {intent.disk.next_stage ? (
        <span className="studio-row-sub studio-mono">{t('intents.stage.next', { stage: intent.disk.next_stage })}</span>
      ) : null}
    </>
  )

  // `keep_moving` is typed `false` on the wire and always will be in v1: the machine lane is unproven, so
  // the column reports unavailability with its reason rather than an "off" that implies a switch.
  const keepMoving = (
    <Chip icon="warn" tone="warn" title={t('intents.keepMoving.why')}>
      {t('intents.keepMoving.unavailable')}
    </Chip>
  )

  if (layout === 'card') {
    return (
      <li className="studio-intent-card" data-state={intent.operational_state}>
        <div className="studio-intent-card-head">
          <span className="studio-mono studio-muted">{intent.repo_label}</span>
          {state}
        </div>
        <div className="studio-col">{identity}</div>
        <div className="studio-col">{stage}</div>
        <div className="studio-row studio-wrap">
          <span className="studio-row-sub">{t('intents.col.keepMoving')}</span>
          {keepMoving}
        </div>
        <div className="studio-row studio-wrap">{actions}</div>
      </li>
    )
  }

  return (
    <tr data-state={intent.operational_state}>
      <td className="studio-mono">{intent.repo_label}</td>
      <td>{identity}</td>
      <td>{state}</td>
      <td>{stage}</td>
      <td>{keepMoving}</td>
      <td>{actions}</td>
    </tr>
  )
}

/** A row that says a repository could not be read, in the table it belongs to. */
export function IntentRowError({ repo, message, layout }: { repo: string; message: string; layout: 'table' | 'card' }) {
  const { t } = useI18n()
  const text = t('intents.error.repo', { repo, reason: message })
  if (layout === 'card') {
    return (
      <li className="studio-intent-card" data-tone="warn">
        <span className="studio-row">
          <Icon name="warn" size={13} />
          {text}
        </span>
      </li>
    )
  }
  return (
    <tr data-tone="warn">
      <td colSpan={6}>
        <span className="studio-row">
          <Icon name="warn" size={13} />
          {text}
        </span>
      </td>
    </tr>
  )
}
