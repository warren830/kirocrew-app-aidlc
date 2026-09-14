/**
 * The Settings page, and the two primitives its sections share.
 *
 * How saving works, and why it is shaped this way:
 *
 *  - `PUT /settings` takes a partial patch and returns the *whole* new `SettingsResponse`. That answer is
 *    the server's own state, so it is kept as `applied` and preferred over the polled read whenever its
 *    `updated_at` is newer. Without that, a 15-second poll that landed a moment before the write would put
 *    the old value back under the user's cursor.
 *  - Every control is disabled while a save is in flight. Settings are a single row in one table; two
 *    concurrent patches would race and the loser would be silently discarded.
 *  - A rejected patch is reported with the key the backend named (`details.key`), because "invalid
 *    settings" on a page with a dozen controls tells the user nothing about which one refused.
 *
 * The page also mounts the prototype migration section at the top (FR-MIG-001): it renders nothing at all
 * unless there is a prototype or a migration on record, and when both Apps are live it is the first thing
 * on the page.
 */

import { useCallback, useMemo, useState, type ReactNode } from 'react'

import { useI18n } from '../i18n'
import { decodeError, useStudioApi, type DeepPartial } from '../lib/api'
import type { Navigate, StudioRoute } from '../lib/route'
import type {
  Capability, CapabilityName, HealthResponse, ReposResponse, SettingsResponse, SettingsValues,
} from '../lib/types'
import { useResource } from '../lib/useResource'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { MigrationView } from '../migration/MigrationView'
import { AboutPanel } from './AboutPanel'
import { BunSetting } from './BunSetting'
import { AdvisorSetting } from './AdvisorSetting'
import { AutomationSetting } from './AutomationSetting'
import { DiagnosticsSetting } from './DiagnosticsSetting'
import { LocaleSetting } from './LocaleSetting'
import { QueueSetting } from './QueueSetting'
import { SlackSetting } from './SlackSetting'

/** What every section needs to render and to write. */
export interface SettingsControl {
  values: SettingsValues
  capabilities: Record<CapabilityName, Capability>
  /** True while a patch is in flight; every control must go inert. */
  busy: boolean
  save: (patch: DeepPartial<SettingsValues>) => void
}

/**
 * One preference: title, the sentence that says what it does and does not do, and the control.
 *
 * `describedBy` wires the description to the control, so a screen reader reads "Night work window,
 * unattended continuation is not available…" rather than a bare switch.
 */
export function SettingRow({
  id,
  title,
  description,
  children,
}: {
  id: string
  title: string
  description: string
  children: (describedBy: string) => ReactNode
}) {
  const descriptionId = `${id}-desc`
  return (
    <div className="studio-mrow">
      <div className="studio-grow">
        <div className="studio-mrow-title" id={`${id}-title`}>
          {title}
        </div>
        <p className="studio-mrow-desc" id={descriptionId}>
          {description}
        </p>
      </div>
      <div className="studio-mrow-ctl">{children(descriptionId)}</div>
    </div>
  )
}

/** A group of rows under one heading. */
export function SettingsSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="studio-block">
      <h3>{title}</h3>
      <div className="studio-matrix">{children}</div>
    </section>
  )
}

/**
 * "Unavailable — <reason>", from the capability the server reported.
 *
 * The reason is always shown. A control that is simply greyed out teaches the user that Studio is broken;
 * naming the reason (no proven machine lane, credits unobservable, host seam missing) is the difference
 * between a bug and a documented limit.
 */
export function UnavailableChip({ capability }: { capability: Capability | undefined }) {
  const { t, has } = useI18n()
  const raw = capability?.reason ?? null
  const reason = raw && has(`settings.reason.${raw}`) ? t(`settings.reason.${raw}`) : null
  return (
    <Chip tone="warn" icon="warn">
      {reason
        ? t('settings.unavailableWhy', { reason })
        : raw
          ? t('settings.unavailableWhy', { reason: t('settings.reason.other', { raw }) })
          : t('settings.unavailable')}
    </Chip>
  )
}

export interface SettingsViewProps {
  route: StudioRoute
  go: Navigate
}

/** The newer of the two answers, by `updated_at`. `null` sorts oldest. */
function newer(a: SettingsResponse | null, b: SettingsResponse | null): SettingsResponse | null {
  if (!a) return b
  if (!b) return a
  return (a.updated_at ?? '') >= (b.updated_at ?? '') ? a : b
}

export function SettingsView({ go }: SettingsViewProps) {
  const i18n = useI18n()
  const { t } = i18n
  const api = useStudioApi()

  const settings = useResource<SettingsResponse>(
    'settings',
    useCallback((signal) => api.settings({ signal }), [api]),
    { interval: 0, revalidateOn: ['settings.updated', 'reset'] },
  )
  const health = useResource<HealthResponse>(
    'health',
    useCallback((signal) => api.health({ signal }), [api]),
    { interval: 60_000 },
  )
  const repos = useResource<ReposResponse>(
    'repos',
    useCallback((signal) => api.repos(false, { signal }), [api]),
    { interval: 60_000 },
  )

  // `refresh` is stable across renders; depending on the whole resource would rebuild `save` (and every
  // control that takes it) on each poll.
  const refresh = settings.refresh

  const [applied, setApplied] = useState<SettingsResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const current = newer(applied, settings.data)

  const save = useCallback(
    (patch: DeepPartial<SettingsValues>) => {
      setBusy(true)
      setNotice(t('settings.page.saving'))
      void api
        .putSettings(patch)
        .then((response) => {
          setApplied(response)
          setNotice(t('settings.page.saved'))
        })
        .catch((caught: unknown) => {
          const error = decodeError(caught)
          const message = i18n.has(`errors.${error.code}`) ? t(`errors.${error.code}`) : error.message
          const key = typeof error.details['key'] === 'string' ? error.details['key'] : null
          setNotice(
            key
              ? t('settings.page.saveFailedKey', { key, message })
              : t('settings.page.saveFailed', { message }),
          )
          // The value on screen is now wrong: re-read rather than leave the optimistic control lying.
          void refresh()
        })
        .finally(() => setBusy(false))
    },
    [api, i18n, t, refresh],
  )

  const control = useMemo<SettingsControl | null>(
    () => (current ? { values: current.settings, capabilities: current.capabilities, busy, save } : null),
    [current, busy, save],
  )

  return (
    <div className="studio-scroll">
      <div className="studio-page studio-settings">
        <h1>
          <Icon name="settings" size={18} /> {t('settings.page.title')}
        </h1>
        <p className="studio-lede">{t('settings.page.lede')}</p>

        {/* Polite: a save result is status. It is one region for the whole page so a screen reader hears
            one sentence per change rather than one per section. */}
        <p className="studio-settings-status" role="status" aria-live="polite" aria-label={t('settings.page.status')}>
          {notice ?? ''}
        </p>

        <MigrationView onApplied={() => void repos.refresh()} />

        {settings.error && !current ? (
          <div className="studio-banner" data-tone="warn" role="status">
            <Icon name="warn" size={15} />
            <span className="studio-grow">
              {t('settings.page.error', {
                message: i18n.has(`errors.${settings.error.code}`)
                  ? t(`errors.${settings.error.code}`)
                  : settings.error.message,
              })}
            </span>
            <button type="button" className="studio-btn" onClick={() => void settings.refresh()}>
              <Icon name="refresh" size={13} />
              {t('settings.page.retry')}
            </button>
          </div>
        ) : null}

        {control ? (
          <>
            <SettingsSection title={t('settings.section.locale')}>
              <LocaleSetting control={control} />
            </SettingsSection>

            <SettingsSection title={t('settings.section.queue')}>
              <QueueSetting control={control} />
            </SettingsSection>

            <SettingsSection title={t('settings.section.automation')}>
              <AutomationSetting control={control} />
            </SettingsSection>

            <SettingsSection title={t('settings.section.advisor')}>
              {/* `null`, not `[]`: an unread list must not make every live grant look orphaned. */}
              <AdvisorSetting control={control} repos={repos.data?.repos ?? null} />
            </SettingsSection>

            <SettingsSection title={t('settings.section.notifications')}>
              <SlackSetting control={control} repos={repos.data?.repos ?? []} />
            </SettingsSection>

            <SettingsSection title={t('settings.section.diagnostics')}>
              <DiagnosticsSetting control={control} />
            </SettingsSection>
          </>
        ) : (
          <p className="studio-muted">{t('settings.page.reading')}</p>
        )}

        <SettingsSection title={t('settings.section.about')}>
          <BunSetting api={api} tool={health.data?.tools?.bun ?? null} onChanged={() => void health.refresh()} />
          <AboutPanel
            health={health.data}
            versions={current?.versions ?? null}
            onOpenActivity={() => go({ view: 'activity' })}
          />
        </SettingsSection>
      </div>
    </div>
  )
}

export default SettingsView
