/**
 * Queue organization.
 *
 * Written to two places for the same reason the locale is (§3.2: `organize` comes "from
 * `localStorage['aidlc-studio:organize']` else settings"): the Action Center reads the browser copy so a new
 * tab opens the way the user left it, and the server copy is what a new browser inherits. Saving only the
 * server value would leave this tab's queue in the old order until the next cold start.
 */

import { useI18n } from '../i18n'
import { STUDIO_ORGANIZE_KEY } from '../lib/host'
import type { Organize } from '../lib/types'
import { SettingRow, type SettingsControl } from './SettingsView'

const ORGANIZE: readonly Organize[] = ['priority', 'repo', 'type', 'oldest']

export interface QueueSettingProps {
  control: SettingsControl
}

export function QueueSetting({ control }: QueueSettingProps) {
  const { t } = useI18n()

  const pick = (next: Organize) => {
    try {
      localStorage.setItem(STUDIO_ORGANIZE_KEY, next)
    } catch {
      // Storage can be disabled; the server copy still applies on the next read of /actions.
    }
    control.save({ queue_organize: next })
  }

  return (
    <SettingRow id="aidlc-set-organize" title={t('settings.queue.title')} description={t('settings.queue.desc')}>
      {(describedBy) => (
        <div className="studio-col">
          <label className="studio-sfield">
            <span className="studio-sr">{t('settings.queue.label')}</span>
            <select
              value={control.values.queue_organize}
              disabled={control.busy}
              aria-describedby={describedBy}
              onChange={(event) => pick(event.target.value as Organize)}
            >
              {ORGANIZE.map((value) => (
                <option key={value} value={value}>
                  {t(`settings.queue.${value}`)}
                </option>
              ))}
            </select>
          </label>
          <span className="studio-muted studio-small">{t('settings.queue.localNote')}</span>
        </div>
      )}
    </SettingRow>
  )
}
