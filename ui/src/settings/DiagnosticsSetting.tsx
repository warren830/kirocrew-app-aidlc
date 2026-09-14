/**
 * Diagnostic retention and export.
 *
 * The export lives here as well as on Activity, and it is literally the same component: the redaction list
 * has to be read before a bundle exists, and duplicating that copy in two places is how the two copies end
 * up disagreeing about what is removed.
 *
 * The ordering of the two retention rows is deliberate. `diagnostics.retention_days` is how long Studio keeps
 * its own history; `human_text_retention_days` is how long the exact text a user submitted is kept *at all*,
 * which is a privacy bound rather than a housekeeping one, and the "allow prompt bodies in an export" switch
 * sits between them so the three read as one policy.
 */

import { useEffect, useState } from 'react'

import { useI18n } from '../i18n'
import { plural } from '../lib/format'
import { ExportPanel } from '../activity/ExportPanel'
import { Icon } from '../shell/Icon'
import { SettingRow, type SettingsControl } from './SettingsView'

/** Mirrors `settings.RETENTION_DAYS_RANGE`. */
export const RETENTION_RANGE = { lo: 1, hi: 30 } as const

export interface DiagnosticsSettingProps {
  control: SettingsControl
}

/** A day count that commits on blur or Enter; an out-of-range draft snaps back rather than being sent. */
function DaysField({
  id,
  label,
  value,
  disabled,
  describedBy,
  onCommit,
}: {
  id: string
  label: string
  value: number
  disabled: boolean
  describedBy: string
  onCommit: (next: number) => void
}) {
  const [draft, setDraft] = useState(String(value))
  useEffect(() => setDraft(String(value)), [value])

  const commit = () => {
    const parsed = Number(draft)
    if (!Number.isInteger(parsed) || parsed < RETENTION_RANGE.lo || parsed > RETENTION_RANGE.hi) {
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
        min={RETENTION_RANGE.lo}
        max={RETENTION_RANGE.hi}
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

export function DiagnosticsSetting({ control }: DiagnosticsSettingProps) {
  const i18n = useI18n()
  const { t } = i18n
  const allowHumanText = control.values.diagnostics.export_include_human_text

  return (
    <>
      <SettingRow
        id="aidlc-set-retention"
        title={t('settings.diagnostics.retention.title')}
        description={t('settings.diagnostics.retention.desc')}
      >
        {(describedBy) => (
          <div className="studio-row studio-wrapchips">
            <DaysField
              id="aidlc-set-retention-input"
              label={t('settings.diagnostics.retention.label')}
              value={control.values.diagnostics.retention_days}
              disabled={control.busy}
              describedBy={describedBy}
              onCommit={(next) => control.save({ diagnostics: { retention_days: next } })}
            />
            <span className="studio-muted">
              {plural(i18n, 'settings.diagnostics.retention.value', control.values.diagnostics.retention_days)}
            </span>
          </div>
        )}
      </SettingRow>

      <SettingRow
        id="aidlc-set-humantext"
        title={t('settings.diagnostics.humanText.title')}
        description={t('settings.diagnostics.humanText.desc')}
      >
        {(describedBy) => (
          <label className="studio-check">
            <input
              type="checkbox"
              checked={allowHumanText}
              disabled={control.busy}
              aria-describedby={describedBy}
              onChange={(event) =>
                control.save({ diagnostics: { export_include_human_text: event.target.checked } })
              }
            />
            <span>
              <Icon name="lock" size={13} /> {t('settings.diagnostics.humanText.label')}
            </span>
          </label>
        )}
      </SettingRow>

      <SettingRow
        id="aidlc-set-humanretention"
        title={t('settings.diagnostics.humanRetention.title')}
        description={t('settings.diagnostics.humanRetention.desc')}
      >
        {(describedBy) => (
          <div className="studio-row studio-wrapchips">
            <DaysField
              id="aidlc-set-humanretention-input"
              label={t('settings.diagnostics.humanRetention.label')}
              value={control.values.human_text_retention_days}
              disabled={control.busy}
              describedBy={describedBy}
              onCommit={(next) => control.save({ human_text_retention_days: next })}
            />
            <span className="studio-muted">
              {plural(i18n, 'settings.diagnostics.retention.value', control.values.human_text_retention_days)}
            </span>
          </div>
        )}
      </SettingRow>

      <ExportPanel allowHumanText={allowHumanText} />
    </>
  )
}
