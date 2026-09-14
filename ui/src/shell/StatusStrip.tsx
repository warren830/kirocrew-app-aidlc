/**
 * The execution status strip: what is running, what is held, what is stuck.
 *
 * DEVIATION from the mockup (visual spec §1.3, discrepancy #2): the mockup's strip is four static
 * chips with no way to put them away, and PRD §8.3 requires it to be collapsible. The disclosure added
 * here is Option B's chip strip with a chevron — not Option C's card layout, which §12 forbids
 * importing. Collapsed, it still shows the number of active alerts, because a strip that hides a stuck
 * circuit breaker is worse than no strip.
 *
 * Every number here is observed or absent. `null` renders as the unavailable string rather than `0`:
 * "0 turns running" while Studio cannot reach the host would be a claim it has no evidence for.
 */

import { useCallback, useState } from 'react'

import { useI18n } from '../i18n'
import { plural } from '../lib/format'
import type { StreamMode } from '../lib/sse'
import { Chip } from './Chip'
import { Icon } from './Icon'

/** Remembered per browser, not per session: an operator who folds it away meant it. */
const STRIP_KEY = 'aidlc-studio:strip'

export interface StatusStripProps {
  /** Turns executing right now (`/leases.live_execution`). */
  running: number | null
  /** Repo leases held, execution plus admin (`/leases.leases.length`). */
  leases: number | null
  /** Open circuit breakers. */
  circuits: number | null
  /** Cards in the `critical` band — recovery and delivery uncertainty. */
  critical: number | null
  nightWindow: { enabled: boolean; start: string; end: string } | null
  streamMode: StreamMode
}

function readOpen(): boolean {
  try {
    return localStorage.getItem(STRIP_KEY) !== 'closed'
  } catch {
    return true
  }
}

export function StatusStrip({ running, leases, circuits, critical, nightWindow, streamMode }: StatusStripProps) {
  const i18n = useI18n()
  const { t } = i18n
  const [open, setOpen] = useState(readOpen)

  const toggle = useCallback(() => {
    setOpen((was) => {
      const next = !was
      try {
        localStorage.setItem(STRIP_KEY, next ? 'open' : 'closed')
      } catch {
        // A preference that cannot be stored still applies for this page.
      }
      return next
    })
  }, [])

  const alerts = (circuits ?? 0) + (critical ?? 0)
  const isRunning = (running ?? 0) > 0
  const summary = open
    ? ''
    : alerts > 0
      ? plural(i18n, 'shell.strip.alerts', alerts)
      : t('shell.strip.clear')

  return (
    <div className="studio-strip" data-open={open ? 'true' : 'false'}>
      <button
        type="button"
        className="studio-icon-btn studio-strip-toggle"
        aria-expanded={open}
        aria-label={t(open ? 'shell.strip.collapse' : 'shell.strip.expand')}
        onClick={toggle}
      >
        <Icon name={open ? 'chevronDown' : 'chevron'} size={14} />
      </button>

      {open ? (
        <>
          <Chip tone={isRunning ? 'accent' : 'neutral'}>
            {isRunning ? <span className="studio-pulse" aria-hidden /> : <Icon name="pause" size={11} strokeWidth={2} />}
            {running === null ? t('common.unavailable') : plural(i18n, 'shell.strip.running', running)}
          </Chip>
          <Chip icon="lock">
            {leases === null ? t('common.unavailable') : plural(i18n, 'shell.strip.leases', leases)}
          </Chip>
          {(circuits ?? 0) > 0 ? (
            <Chip tone="warn" icon="clock">
              {plural(i18n, 'shell.strip.circuits', circuits ?? 0)}
            </Chip>
          ) : null}
          {(critical ?? 0) > 0 ? (
            <Chip tone="danger" icon="recovery">
              {plural(i18n, 'shell.strip.critical', critical ?? 0)}
            </Chip>
          ) : null}
          <Chip icon="moon" title={nightWindow?.enabled ? undefined : t('unavailable.machineLane')}>
            {nightWindow?.enabled
              ? t('shell.strip.nightOn', { start: nightWindow.start, end: nightWindow.end })
              : t('shell.strip.nightOff')}
          </Chip>
          {streamMode !== 'live' ? (
            <Chip tone="warn" icon="refresh" title={t('shell.strip.pollingWhy')}>
              {t('shell.strip.polling')}
            </Chip>
          ) : null}
        </>
      ) : (
        <span className="studio-strip-summary">
          {alerts > 0 ? (
            <Chip tone="danger" icon="warn">
              {plural(i18n, 'shell.strip.alerts', alerts)}
            </Chip>
          ) : (
            <Chip icon="check">{t('shell.strip.clear')}</Chip>
          )}
        </span>
      )}

      {/* Polite, and only the collapsed summary: announcing every chip on every 2-second poll would
          make the strip unusable with a screen reader (PRD §10.3 "sparingly"). */}
      <span className="studio-sr" role="status" aria-live="polite">
        {summary}
      </span>
    </div>
  )
}
