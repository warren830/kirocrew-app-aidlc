/**
 * Slack and dashboard notifications.
 *
 * Two things this file is careful about:
 *
 *  1. **Slack quick actions get no control.** FR-SLK-004 describes them, but the host has no seam that
 *     re-dispatches a Slack click as an authenticated compare-and-submit (capability
 *     `slack_quick_actions`, reason `host_seam_unavailable`). A toggle here would offer to authorise a
 *     decision over a channel Studio cannot verify, so the row states the limit and links nothing.
 *  2. **Muting is per repo, and a mute for a repo that is gone is still shown.** `muted_repo_ids` can name a
 *     repository the user has since unregistered. Dropping it silently would quietly re-enable Slack for
 *     that path if it were ever re-registered under the same id, so the orphan is listed with the reason and
 *     can be removed deliberately. A mute on an archived repository is kept as it is: that repository is
 *     still registered, and its mute changes once it is restored.
 */

import { useI18n } from '../i18n'
import { plural } from '../lib/format'
import type { RepoRecord } from '../lib/types'
import { Icon } from '../shell/Icon'
import { repoRows, SettingRow, UnavailableChip, type SettingsControl } from './SettingsView'

export interface SlackSettingProps {
  control: SettingsControl
  /** Every registered repository, archived ones included; `null` while the list has not been read. */
  repos: RepoRecord[] | null
}

export function SlackSetting({ control, repos }: SlackSettingProps) {
  const i18n = useI18n()
  const { t } = i18n
  const capability = control.capabilities.slack
  const slackAvailable = capability?.available !== false
  const muted = control.values.slack.muted_repo_ids
  const rows = repoRows(repos, muted)

  const setMuted = (repoId: string, mute: boolean) => {
    const next = mute ? [...muted, repoId] : muted.filter((id) => id !== repoId)
    // Deduplicated because the server enforces a set and would otherwise store the same id twice.
    control.save({ slack: { muted_repo_ids: [...new Set(next)] } })
  }

  return (
    <>
      <SettingRow id="aidlc-set-slack" title={t('settings.slack.title')} description={t('settings.slack.desc')}>
        {(describedBy) => (
          <div className="studio-row studio-wrapchips">
            {slackAvailable ? null : <UnavailableChip capability={capability} />}
            <label className="studio-check">
              <input
                type="checkbox"
                checked={control.values.slack.enabled}
                disabled={control.busy || !slackAvailable}
                aria-describedby={describedBy}
                onChange={(event) => control.save({ slack: { enabled: event.target.checked } })}
              />
              <span>
                <Icon name="slack" size={13} /> {t('settings.slack.label')}
              </span>
            </label>
          </div>
        )}
      </SettingRow>

      <SettingRow
        id="aidlc-set-slack-quick"
        title={t('settings.slack.quickActions.title')}
        description={t('settings.slack.quickActions.desc')}
      >
        {() => <UnavailableChip capability={control.capabilities.slack_quick_actions} />}
      </SettingRow>

      <SettingRow
        id="aidlc-set-slack-mute"
        title={t('settings.slack.mute.title')}
        description={t('settings.slack.mute.desc')}
      >
        {(describedBy) => (
          <div className="studio-col" aria-describedby={describedBy}>
            {rows === null ? (
              <span className="studio-muted">{t('common.loading')}</span>
            ) : rows.listed.length === 0 && rows.orphans.length === 0 ? (
              <span className="studio-muted">{t('settings.slack.mute.none')}</span>
            ) : (
              <>
                <ul className="studio-mutelist">
                  {rows.listed.map((repo) => (
                    <li key={repo.repo_id}>
                      <label className="studio-check">
                        <input
                          type="checkbox"
                          checked={muted.includes(repo.repo_id)}
                          disabled={control.busy || !slackAvailable || repo.archived}
                          onChange={(event) => setMuted(repo.repo_id, event.target.checked)}
                          aria-label={t('settings.slack.mute.label', { repo: repo.label })}
                        />
                        {/* Repo labels are the user's own words: verbatim. */}
                        <span className="studio-strong">{repo.label}</span>
                        <span className="studio-mono studio-muted studio-small studio-trunc">
                          {repo.canonical_path}
                        </span>
                        {repo.archived ? (
                          <span className="studio-muted studio-small">{t('settings.slack.mute.archived')}</span>
                        ) : null}
                      </label>
                    </li>
                  ))}
                  {rows.orphans.map((id) => (
                    <li key={id}>
                      <label className="studio-check">
                        <input
                          type="checkbox"
                          checked
                          disabled={control.busy}
                          onChange={() => setMuted(id, false)}
                          aria-label={t('settings.slack.mute.label', { repo: id })}
                        />
                        <span className="studio-mono">{t('settings.slack.mute.orphan', { id })}</span>
                      </label>
                    </li>
                  ))}
                </ul>
                <span className="studio-muted studio-small">
                  {plural(i18n, 'settings.slack.mute.count', muted.length)}
                </span>
              </>
            )}
          </div>
        )}
      </SettingRow>

      <SettingRow
        id="aidlc-set-dashboard"
        title={t('settings.dashboard.title')}
        description={t('settings.dashboard.desc')}
      >
        {(describedBy) => (
          <label className="studio-check">
            <input
              type="checkbox"
              checked={control.values.notifications.dashboard}
              disabled={control.busy}
              aria-describedby={describedBy}
              onChange={(event) => control.save({ notifications: { dashboard: event.target.checked } })}
            />
            <span>{t('settings.dashboard.label')}</span>
          </label>
        )}
      </SettingRow>
    </>
  )
}
