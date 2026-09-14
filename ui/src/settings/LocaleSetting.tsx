/**
 * Language and density.
 *
 * Locale is the one setting that has to be written twice, and the reason is worth stating: the *page* reads
 * its locale from `localStorage['aidlc-studio:locale']` through `useHostLocale()` (§3.6), while the stored
 * preference lives on the server so it survives a new browser. So a change does both — `setStudioLocale`
 * repaints this tab immediately (it dispatches the event `useHostLocale` listens for) and `PUT /settings`
 * persists it. Writing only the server value would leave the user reading the old language until the next
 * full reload, which looks exactly like the setting not working.
 *
 * `auto` means "follow the dashboard": the local override is cleared, and the row names the locale that
 * actually resolved, so "Follow the dashboard" is never a mystery.
 */

import { useI18n } from '../i18n'
import { setStudioLocale, useHostLocale, type StudioLocale } from '../lib/host'
import { SettingRow, type SettingsControl } from './SettingsView'

const LOCALE_CHOICES = ['auto', 'en-US', 'zh-CN'] as const
type LocaleChoice = (typeof LOCALE_CHOICES)[number]

const DENSITIES = ['compact', 'comfortable'] as const

export interface LocaleSettingProps {
  control: SettingsControl
}

export function LocaleSetting({ control }: LocaleSettingProps) {
  const { t } = useI18n()
  const resolved = useHostLocale()
  const choice: LocaleChoice = control.values.locale

  const pick = (next: LocaleChoice) => {
    // The local override first: it is synchronous and cannot fail, so the page never ends up showing a
    // language the server has already accepted.
    setStudioLocale(next === 'auto' ? null : (next as StudioLocale))
    control.save({ locale: next })
  }

  return (
    <>
      <SettingRow id="aidlc-set-locale" title={t('settings.locale.title')} description={t('settings.locale.desc')}>
        {(describedBy) => (
          <div className="studio-col">
            <label className="studio-sfield">
              <span className="studio-sr">{t('settings.locale.label')}</span>
              <select
                value={choice}
                disabled={control.busy}
                aria-describedby={describedBy}
                onChange={(event) => pick(event.target.value as LocaleChoice)}
              >
                {LOCALE_CHOICES.map((value) => (
                  <option key={value} value={value}>
                    {value === 'auto' ? t('settings.locale.auto') : t(`settings.locale.${value}`)}
                  </option>
                ))}
              </select>
            </label>
            <span className="studio-muted studio-small">
              {choice === 'auto'
                ? t('settings.locale.following', { locale: t(`settings.locale.${resolved}`) })
                : t('settings.locale.overridden')}
            </span>
          </div>
        )}
      </SettingRow>

      <SettingRow id="aidlc-set-density" title={t('settings.density.title')} description={t('settings.density.desc')}>
        {(describedBy) => (
          <label className="studio-sfield">
            <span className="studio-sr">{t('settings.density.label')}</span>
            <select
              value={control.values.density}
              disabled={control.busy}
              aria-describedby={describedBy}
              onChange={(event) =>
                control.save({ density: event.target.value as (typeof DENSITIES)[number] })
              }
            >
              {DENSITIES.map((value) => (
                <option key={value} value={value}>
                  {t(`settings.density.${value}`)}
                </option>
              ))}
            </select>
          </label>
        )}
      </SettingRow>
    </>
  )
}
