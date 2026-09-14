/**
 * One timeline row — and the provenance rules every other Activity component reads from here.
 *
 * The single property this file exists to protect: a row never claims a source it does not have.
 * `TimelineEntry.source` and `TimelineEntry.message_key` are two independent facts on the wire, and the
 * backend is supposed to keep them consistent (`activity.<kind>` for Studio's own records,
 * `audit.<EVENT>` only for rows projected live out of an AI-DLC audit shard). `provenanceOf` checks that
 * agreement in the UI as well, because the failure it prevents is the expensive one: if a regression ever
 * copied audit rows into Studio's table, a row would read "Gate approved." — the exact sentence AI-DLC's
 * own audit trail uses — under a label saying Studio wrote it. A mismatched row therefore renders no
 * sentence at all; it says the two halves disagree and sends the reader to the raw record.
 *
 * The message catalogues are deliberately placeholder-free (see `i18n/parts/activity.*.json`). Audit
 * field names are free-form and version-specific (`Stage slug`, `Questions SHA-256`, `Candidate-ID`), and
 * a Studio kind's params vary per call site, so any `{name}` in these sentences would eventually render
 * as the literal text `{name}`. The values are shown as data instead: as chips here, in full in the
 * Evidence drawer.
 */

import { useI18n, type I18n } from '../i18n'
import { Chip, type ChipTone } from '../shell/Chip'
import { Icon, type IconName } from '../shell/Icon'
import type { Severity, Source, TimelineEntry } from '../lib/types'

/** Where the row came from → the glyph beside its source word. Never the only encoding. */
export const SOURCE_ICON: Record<Source, IconName> = {
  aidlc: 'logo',
  studio: 'send',
  kirocrew: 'activity',
  git: 'git',
  slack: 'slack',
}

const SEVERITY_ICON: Record<Severity, IconName> = {
  critical: 'recovery',
  blocking: 'gate',
  attention: 'warn',
  info: 'info',
}

const SEVERITY_TONE: Record<Severity, ChipTone> = {
  critical: 'danger',
  blocking: 'accent',
  attention: 'warn',
  info: 'neutral',
}

/** What kind of record this row is, after checking that source and message key agree. */
export type Provenance = 'audit' | 'studio' | 'mismatch'

export function provenanceOf(entry: TimelineEntry): Provenance {
  const claimsAudit = entry.message_key.startsWith('audit.')
  if (entry.source === 'aidlc') return claimsAudit ? 'audit' : 'mismatch'
  return claimsAudit ? 'mismatch' : 'studio'
}

/**
 * The row's sentence.
 *
 * A key the catalogue does not have falls back to the vocabulary's own "unrecognised" sentence rather
 * than printing the key: a new engine event name is expected in the field (real installs write events no
 * released Studio knows), and `activity.row.unknownKind` / `audit.unknown_event` say so honestly.
 */
export function messageOf(i18n: I18n, entry: TimelineEntry): string {
  if (i18n.has(entry.message_key)) return i18n.t(entry.message_key)
  return entry.message_key.startsWith('audit.') ? i18n.t('audit.unknown_event') : i18n.t('activity.row.unknownKind')
}

const timeFormatters = new Map<string, Intl.DateTimeFormat>()

/** Time of day, for a row under a date heading. Falls back to the raw string when unparseable. */
export function timeOfDay(locale: string, iso: string): string {
  if (!Number.isFinite(Date.parse(iso))) return iso
  let fmt = timeFormatters.get(locale)
  if (!fmt) {
    fmt = new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
    timeFormatters.set(locale, fmt)
  }
  return fmt.format(new Date(iso))
}

/** A stable identity for one row, used to keep the drawer pointed at the same record across refetches. */
export function entryKey(entry: TimelineEntry): string {
  return entry.id !== null ? `id:${entry.id}` : `at:${entry.at}:${entry.kind}:${entry.message_key}`
}

export interface TimelineRowProps {
  entry: TimelineEntry
  /** True when the Evidence drawer is showing this row. */
  selected: boolean
  onOpenEvidence: (entry: TimelineEntry) => void
  /** Absent when the caller cannot navigate (a detail tab embedding the timeline read-only). */
  onOpenAction?: (actionId: string) => void
}

export function TimelineRow({ entry, selected, onOpenEvidence, onOpenAction }: TimelineRowProps) {
  const i18n = useI18n()
  const { t } = i18n
  const provenance = provenanceOf(entry)
  const sourceLabel = t(`enum.source.${entry.source}`)
  const time = timeOfDay(i18n.locale, entry.at)
  const stage = typeof entry.params['stage'] === 'string' ? entry.params['stage'] : null
  const audit = entry.refs.audit
  const actionId = entry.refs.action_id ?? null

  return (
    <li className="studio-tlrow" data-source={entry.source} data-severity={entry.severity} {...(selected ? { 'aria-current': 'true' } : {})}>
      <span className="studio-tlrow-time studio-mono">
        {time}
        {/* The date heading gives the day; a screen reader reading rows out of order needs the whole moment. */}
        <span className="studio-sr"> {t('activity.row.at', { when: i18n.fmt.dateTime(entry.at) })}</span>
      </span>

      <span className="studio-tlrow-icon" aria-hidden>
        <Icon name={SOURCE_ICON[entry.source]} size={13} />
      </span>

      <div className="studio-tlrow-body">
        <p className="studio-tlrow-msg">
          {provenance === 'mismatch' ? t('activity.row.mismatch') : messageOf(i18n, entry)}
        </p>
        <div className="studio-tlrow-meta">
          <Chip icon={SOURCE_ICON[entry.source]}>{sourceLabel}</Chip>
          {/* AI-DLC's own words vs. Studio's, stated rather than implied by a colour. */}
          <span className="studio-tlrow-kindword">
            {provenance === 'audit' ? t('activity.row.auditEvent') : t('activity.row.derived')}
          </span>
          {entry.severity === 'info' ? null : (
            <Chip tone={SEVERITY_TONE[entry.severity]} icon={SEVERITY_ICON[entry.severity]}>
              {t(`enum.severity.${entry.severity}`)}
            </Chip>
          )}
          {/* Stage slugs are AI-DLC's words: shown verbatim, never translated. */}
          {stage ? <Chip mono>{t('activity.row.stage', { stage })}</Chip> : null}
          {audit ? (
            <Chip mono icon="doc">
              {audit.pos === null
                ? t('activity.row.shardNoPos', { shard: audit.shard })
                : t('activity.row.shard', { shard: audit.shard, pos: audit.pos })}
            </Chip>
          ) : null}
          {entry.refs.git ? (
            <Chip mono icon="git">
              {t('activity.row.commit', { sha: entry.refs.git.sha.slice(0, 8) })}
            </Chip>
          ) : null}
          {entry.refs.session_key ? (
            <Chip mono icon="link">{t('activity.row.session', { session: entry.refs.session_key })}</Chip>
          ) : null}
        </div>
      </div>

      <div className="studio-tlrow-actions">
        {actionId && onOpenAction ? (
          <button
            type="button"
            className="studio-btn studio-btn-sm"
            onClick={() => onOpenAction(actionId)}
            aria-label={t('activity.row.openActionFor', { action: actionId })}
          >
            <Icon name="inbox" size={13} />
            {t('activity.row.openAction')}
          </button>
        ) : null}
        <button
          type="button"
          className="studio-btn studio-btn-sm"
          onClick={() => onOpenEvidence(entry)}
          aria-expanded={selected}
          aria-label={t('activity.row.evidenceFor', { source: sourceLabel, time })}
        >
          <Icon name="lock" size={13} />
          {t('activity.row.evidence')}
        </button>
      </div>
    </li>
  )
}
