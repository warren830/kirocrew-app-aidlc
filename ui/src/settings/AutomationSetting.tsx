/**
 * Automation and budgets: how many repositories may run a turn at once, and whether an install runs the
 * AI-DLC doctor afterwards. There is no unattended-work window: no trusted machine lane exists, so a
 * schedule would only suggest that Studio might act behind the user's back.
 */

import { useI18n } from '../i18n'
import { plural } from '../lib/format'
import { Icon } from '../shell/Icon'
import { NumberField, SettingRow, type SettingsControl } from './SettingsView'

/** Mirrors `settings.SCHEMA` server-side; the input refuses out-of-range before the round trip. */
export const CONCURRENCY_RANGE = { lo: 1, hi: 8 } as const

export function AutomationSetting({ control }: { control: SettingsControl }) {
  const i18n = useI18n()
  const { t } = i18n

  return (
    <>
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
