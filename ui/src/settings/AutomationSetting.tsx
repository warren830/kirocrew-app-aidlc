/**
 * Automation and budgets.
 *
 * The whole point of this section is what it refuses to render. `night_window.enabled` and
 * `night_window.credit_cap` are *locked* server-side: a patch that sets either answers `409` (§1.21). So no
 * switch is drawn for them at all — an enabled-looking toggle that the backend rejects would be a promise
 * Studio cannot keep, and worse, it would suggest unattended runs are one click away. What is drawn instead
 * is the capability's own reason: no trusted machine lane is proven, so a machine dispatch would forge human
 * presence in the AI-DLC audit trail; credit use is not observable, so a credit cap would be a budget that
 * is never checked.
 *
 * The window bounds and the turn cap *are* writable, and are kept writable on purpose: they are the shape of
 * a policy the user has already decided, ready for the day the lane is proven. The row says they are inert
 * today rather than implying they schedule anything.
 *
 * Numbers use a local draft and commit on blur or Enter. Saving per keystroke would fire a PUT for "4" on
 * the way to "40" — and "4" is a valid cap, so the intermediate value would really be stored.
 */

import { useEffect, useState } from 'react'

import { useI18n } from '../i18n'
import { plural } from '../lib/format'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { SettingRow, UnavailableChip, type SettingsControl } from './SettingsView'

/** Mirrors `settings.SCHEMA` server-side; the input refuses out-of-range before the round trip. */
export const CONCURRENCY_RANGE = { lo: 1, hi: 8 } as const
export const TURN_CAP_RANGE = { lo: 1, hi: 1000 } as const

/** `HH:MM`, 24-hour, as `settings.SCHEMA`'s `clock_time` defines it. */
const CLOCK_RE = /^([01]\d|2[0-3]):[0-5]\d$/

export interface AutomationSettingProps {
  control: SettingsControl
}

/**
 * A number input that commits on blur or Enter, never per keystroke, and never commits a value the schema
 * would reject (an empty box or a half-typed number leaves the stored value alone).
 */
function NumberField({
  id,
  label,
  value,
  lo,
  hi,
  disabled,
  describedBy,
  onCommit,
}: {
  id: string
  label: string
  value: number
  lo: number
  hi: number
  disabled: boolean
  describedBy: string
  onCommit: (next: number) => void
}) {
  const [draft, setDraft] = useState(String(value))
  useEffect(() => setDraft(String(value)), [value])

  const commit = () => {
    const parsed = Number(draft)
    if (!Number.isInteger(parsed) || parsed < lo || parsed > hi) {
      setDraft(String(value))
      return
    }
    if (parsed !== value) onCommit(parsed)
  }

  return (
    <label className="studio-sfield" htmlFor={id}>
      <span className="studio-sr">{label}</span>
      <input
        id={id}
        type="number"
        className="studio-mono studio-num"
        min={lo}
        max={hi}
        step={1}
        value={draft}
        disabled={disabled}
        aria-describedby={describedBy}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') commit()
        }}
      />
    </label>
  )
}

/** A `HH:MM` input that commits only a value matching the schema's clock format. */
function ClockField({
  id,
  label,
  value,
  disabled,
  describedBy,
  onCommit,
}: {
  id: string
  label: string
  value: string
  disabled: boolean
  describedBy: string
  onCommit: (next: string) => void
}) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])

  const commit = () => {
    if (!CLOCK_RE.test(draft)) {
      setDraft(value)
      return
    }
    if (draft !== value) onCommit(draft)
  }

  return (
    <label className="studio-sfield" htmlFor={id}>
      <span className="studio-sr">{label}</span>
      <input
        id={id}
        type="time"
        className="studio-mono"
        value={draft}
        disabled={disabled}
        aria-describedby={describedBy}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => {
          if (event.key === 'Enter') commit()
        }}
      />
    </label>
  )
}

export function AutomationSetting({ control }: AutomationSettingProps) {
  const i18n = useI18n()
  const { t } = i18n
  const night = control.values.night_window

  return (
    <>
      <SettingRow id="aidlc-set-night" title={t('settings.night.title')} description={t('settings.night.desc')}>
        {(describedBy) => (
          <div className="studio-col" aria-describedby={describedBy}>
            {/* No switch: `night_window.enabled` is locked server-side and a patch is refused (§1.21). */}
            <UnavailableChip capability={control.capabilities.night_window} />
            <Chip mono icon="moon">
              {t('settings.night.window', { start: night.start_local, end: night.end_local })}
            </Chip>
            <div className="studio-row">
              <ClockField
                id="aidlc-set-night-start"
                label={t('settings.night.start')}
                value={night.start_local}
                disabled={control.busy}
                describedBy={describedBy}
                onCommit={(next) => control.save({ night_window: { start_local: next } })}
              />
              <ClockField
                id="aidlc-set-night-end"
                label={t('settings.night.end')}
                value={night.end_local}
                disabled={control.busy}
                describedBy={describedBy}
                onCommit={(next) => control.save({ night_window: { end_local: next } })}
              />
            </div>
            <span className="studio-muted studio-small">{t('settings.night.storedOnly')}</span>
          </div>
        )}
      </SettingRow>

      <SettingRow
        id="aidlc-set-turncap"
        title={t('settings.turnCap.title')}
        description={t('settings.turnCap.desc')}
      >
        {(describedBy) => (
          <NumberField
            id="aidlc-set-turncap-input"
            label={t('settings.turnCap.label')}
            value={night.turn_cap}
            lo={TURN_CAP_RANGE.lo}
            hi={TURN_CAP_RANGE.hi}
            disabled={control.busy}
            describedBy={describedBy}
            onCommit={(next) => control.save({ night_window: { turn_cap: next } })}
          />
        )}
      </SettingRow>

      <SettingRow
        id="aidlc-set-creditcap"
        title={t('settings.creditCap.title')}
        description={t('settings.creditCap.desc')}
      >
        {/* No input at all: an estimated credit budget is exactly what FR-NIGHT-007 forbids. */}
        {() => <UnavailableChip capability={control.capabilities.credit_cap} />}
      </SettingRow>

      <SettingRow
        id="aidlc-set-concurrency"
        title={t('settings.concurrency.title')}
        description={t('settings.concurrency.desc')}
      >
        {(describedBy) => (
          <div className="studio-row studio-wrapchips">
            <NumberField
              id="aidlc-set-concurrency-input"
              label={t('settings.concurrency.label')}
              value={control.values.global_concurrency_cap}
              lo={CONCURRENCY_RANGE.lo}
              hi={CONCURRENCY_RANGE.hi}
              disabled={control.busy}
              describedBy={describedBy}
              onCommit={(next) => control.save({ global_concurrency_cap: next })}
            />
            <span className="studio-muted">
              {plural(i18n, 'settings.concurrency.value', control.values.global_concurrency_cap)}
            </span>
          </div>
        )}
      </SettingRow>

      <SettingRow id="aidlc-set-doctor" title={t('settings.doctor.title')} description={t('settings.doctor.desc')}>
        {(describedBy) => (
          <label className="studio-check">
            <input
              type="checkbox"
              checked={control.values.installer.run_doctor_after_install}
              disabled={control.busy}
              aria-describedby={describedBy}
              onChange={(event) =>
                control.save({ installer: { run_doctor_after_install: event.target.checked } })
              }
            />
            <span>
              <Icon name="check" size={13} /> {t('settings.doctor.label')}
            </span>
          </label>
        )}
      </SettingRow>
    </>
  )
}
