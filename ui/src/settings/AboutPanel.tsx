/**
 * About: versions, and the state of the things Studio depends on.
 *
 * The one requirement that shapes the layout: **the App version and the bundled AI-DLC version are separate
 * rows** (PRD §9.21). They move independently — a Studio release can ship the same engine payload, and a
 * repository can be running an older engine than the one bundled here — so a single combined string like
 * "Studio 1.0.0 · AI-DLC 2.6.2" (the mockup's chip, §8.4) would invite the reader to treat them as one
 * number and to conclude that upgrading the App upgraded their repositories. It does not: installing the
 * bundled engine into a repository is an explicit transaction on the Repos page.
 *
 * Everything else here is `/health`, rendered as facts with their reasons: a missing `bun`, a payload
 * mismatch and a stopped reconciler are each the cause of a specific symptom elsewhere, and this is where a
 * user or a bug report finds them without an export.
 */

import { useI18n } from '../i18n'
import { at as formatAt, count, plural } from '../lib/format'
import type { HealthResponse } from '../lib/types'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { SettingRow } from './SettingsView'

export interface AboutPanelProps {
  health: HealthResponse | null
  versions: { studio: string; bundled_engine: string; min_kirocrew: string; host: string | null } | null
  onOpenActivity?: () => void
}

export function AboutPanel({ health, versions, onOpenActivity }: AboutPanelProps) {
  const i18n = useI18n()
  const { t } = i18n
  const unknown = t('common.unavailable')
  const studio = versions?.studio ?? health?.version ?? unknown
  const engine = versions?.bundled_engine ?? health?.bundled_engine_version ?? unknown
  const minHost = versions?.min_kirocrew ?? health?.min_kirocrew_version ?? unknown
  const host = versions?.host ?? health?.host_version ?? null

  return (
    <>
      <SettingRow
        id="aidlc-about-versions"
        title={t('settings.about.title')}
        description={t('settings.about.desc')}
      >
        {() => (
          <dl className="studio-evgrid">
            <div className="studio-evgrid-pair">
              <dt>{t('settings.about.studioVersion')}</dt>
              <dd className="studio-mono">{studio}</dd>
            </div>
            <div className="studio-evgrid-pair">
              <dt>{t('settings.about.engineVersion')}</dt>
              <dd className="studio-mono">{engine}</dd>
            </div>
            <div className="studio-evgrid-pair">
              <dt>{t('settings.about.minHost')}</dt>
              <dd className="studio-mono">{minHost}</dd>
            </div>
            <div className="studio-evgrid-pair">
              <dt>{t('settings.about.host')}</dt>
              <dd className="studio-mono">
                {host ?? (health?.host?.attached === false ? t('settings.about.hostDetached') : unknown)}
              </dd>
            </div>
            <div className="studio-evgrid-pair">
              <dt>{t('settings.about.platform')}</dt>
              {/* A platform name is a raw identifier, so it is verbatim in both locales. */}
              <dd className="studio-mono">{navigatorPlatform() || unknown}</dd>
            </div>
          </dl>
        )}
      </SettingRow>

      <SettingRow
        id="aidlc-about-update"
        title={t('settings.about.updateState')}
        description={t('settings.about.updateDesc')}
      >
        {() => <Chip icon="install">{t('settings.about.updateChip')}</Chip>}
      </SettingRow>

      {health ? (
        <SettingRow
          id="aidlc-about-health"
          title={t('settings.about.status')}
          description={
            health.issues.length > 0 ? health.issues.join(' · ') : t(`settings.about.status.${health.status}`)
          }
        >
          {() => (
            <div className="studio-col">
              <div className="studio-row studio-wrapchips">
                <Chip
                  tone={health.status === 'healthy' ? 'ok' : health.status === 'degraded' ? 'warn' : 'danger'}
                  icon={health.status === 'healthy' ? 'check' : 'warn'}
                >
                  {t(`settings.about.status.${health.status}`)}
                </Chip>
                {health.issues.length > 0 ? (
                  <Chip tone="warn">{plural(i18n, 'settings.about.issues', health.issues.length)}</Chip>
                ) : null}
              </div>
              <dl className="studio-evgrid">
                <div className="studio-evgrid-pair">
                  <dt>{t('settings.about.payload')}</dt>
                  <dd>
                    {!health.payload ? unknown : health.payload.mismatches > 0
                      ? plural(i18n, 'settings.about.payloadBad', health.payload.mismatches)
                      : plural(i18n, 'settings.about.payloadOk', health.payload.file_count)}
                  </dd>
                </div>
                <div className="studio-evgrid-pair">
                  <dt>{t('settings.about.tools')}</dt>
                  <dd className="studio-row studio-wrapchips">
                    {(['bun', 'git'] as const).map((name) => {
                      const tool = health.tools?.[name]
                      if (!tool) return <Chip key={name}>{name} · {unknown}</Chip>
                      return (
                        <Chip key={name} tone={tool.found ? 'neutral' : 'warn'} icon={tool.found ? 'check' : 'warn'}>
                          {tool.found
                            ? t('settings.about.toolFound', { name, version: tool.version ?? unknown })
                            : t('settings.about.toolMissing', { name })}
                        </Chip>
                      )
                    })}
                  </dd>
                </div>
                <div className="studio-evgrid-pair">
                  <dt>{t('settings.about.storage')}</dt>
                  <dd>
                    {!health.storage ? unknown : health.storage.integrity === 'ok'
                      ? t('settings.about.storageOk', { schema: count(i18n, health.storage.schema_version) })
                      : t('settings.about.storageBad', { schema: count(i18n, health.storage.schema_version) })}
                  </dd>
                </div>
                <div className="studio-evgrid-pair">
                  <dt>{t('settings.about.reconciler')}</dt>
                  <dd>
                    {!health.reconciler ? unknown : health.reconciler.running
                      ? t('settings.about.reconcilerRunning', {
                          when: formatAt(i18n, health.reconciler.last_tick_at),
                        })
                      : t('settings.about.reconcilerStopped')}
                  </dd>
                </div>
                <div className="studio-evgrid-pair">
                  <dt>{t('settings.about.boot')}</dt>
                  <dd className="studio-mono studio-wrap-any">{health.boot_id ?? unknown}</dd>
                </div>
              </dl>
              {onOpenActivity ? (
                <button type="button" className="studio-btn studio-btn-sm" onClick={onOpenActivity}>
                  <Icon name="activity" size={13} />
                  {t('activity.page.title')}
                </button>
              ) : null}
            </div>
          )}
        </SettingRow>
      ) : null}
    </>
  )
}

/**
 * The platform Studio is *displayed* on.
 *
 * `/health` does not report the backend's platform (only `RepoRecord.platform` does, per repository), so this
 * is the browser's own value and is labelled as a fact about the display, not about the engine. Guessing the
 * backend's platform from the browser's would be wrong the moment the dashboard is opened from another
 * machine.
 */
function navigatorPlatform(): string {
  if (typeof navigator === 'undefined') return ''
  const data = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData
  return data?.platform || navigator.platform || ''
}
