/**
 * What the new-intent wizard starts from.
 *
 * Only AI-DLC's per-stage summary confirmation for now. "From the scope" sends nothing, so each scope's own
 * default applies (feature asks, bugfix does not), which is what every intent got before this row existed.
 * "Off" or "On" is only where the wizard starts: it still shows the choice, per intent, and it can be changed
 * there. Intents that already exist are not touched; their own switch is on the intent.
 */

import { useI18n } from '../i18n'
import type { SettingsValues } from '../lib/types'
import { SettingRow, type SettingsControl } from './SettingsView'

type SummaryDefault = SettingsValues['new_intent']['summary_confirmation']

const CHOICES: readonly SummaryDefault[] = ['scope', 'off', 'on']

export function NewIntentSetting({ control }: { control: SettingsControl }) {
  const { t } = useI18n()
  const value = control.values.new_intent?.summary_confirmation ?? 'scope'
  return (
    <SettingRow
      id="aidlc-set-new-intent-summary"
      title={t('settings.newIntent.summary.title')}
      description={t('settings.newIntent.summary.desc')}
    >
      {(describedBy) => (
        <label className="studio-sfield">
          <span className="studio-sr">{t('settings.newIntent.summary.label')}</span>
          <select
            className="studio-input"
            value={value}
            disabled={control.busy}
            aria-describedby={describedBy}
            onChange={(event) =>
              control.save({ new_intent: { summary_confirmation: event.target.value as SummaryDefault } })
            }
          >
            {CHOICES.map((choice) => (
              <option key={choice} value={choice}>{t(`settings.newIntent.summary.${choice}`)}</option>
            ))}
          </select>
        </label>
      )}
    </SettingRow>
  )
}
