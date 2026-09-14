/**
 * The Advisor rows.
 *
 * Three things this file is careful about:
 *
 *  1. **No model id, ever.** The model row exists to state that Studio hardcodes none (PRD §9.21,
 *     FR-ADV-002) — the Advisor's model comes from KiroCrew's role resolution and Studio never learns,
 *     stores or displays it. Showing an id here, even a read-only one, would make the App look like it chose
 *     the model.
 *  2. **Drafting ahead is an opt-in, per repository, off on every install** (FR-ADV-010). It spends model
 *     usage on cards nobody has opened yet, so it is a grant the repository's owner makes deliberately and
 *     can never be a global default. Note the inversion against the Slack list below it: there a ticked box
 *     *withholds* something, here a ticked box *grants* it. The two lists look identical, so the copy has to
 *     say which way round this one runs.
 *  3. **A grant that cannot fire must not look active.** With the Advisor unavailable, or switched off, no
 *     draft is ever requested; a live-looking tick would record permission for something that will not
 *     happen, so the list goes inert instead.
 *
 * A grant naming a repository the user has since unregistered is still listed. Dropping it silently would
 * quietly re-grant drafting ahead if that path were ever registered again under the same id, so the orphan is
 * named and removed deliberately — and it stays removable even while the Advisor is unavailable, which is
 * exactly when a stale grant would otherwise be stuck on record. Which is why `repos` distinguishes "not
 * read yet" (`null`) from "there are none" (`[]`): with the two conflated, a settings page opened before the
 * repository read lands would present every live grant as an orphan.
 */

import { useI18n } from '../i18n'
import { plural } from '../lib/format'
import type { RepoRecord } from '../lib/types'
import { Chip } from '../shell/Chip'
import { SettingRow, UnavailableChip, type SettingsControl } from './SettingsView'

export interface AdvisorSettingProps {
  control: SettingsControl
  /** `null` while the repository list has not been read; `[]` only when there really are none. */
  repos: RepoRecord[] | null
}

export function AdvisorSetting({ control, repos }: AdvisorSettingProps) {
  const i18n = useI18n()
  const { t } = i18n
  const capability = control.capabilities.advisor
  const advisorAvailable = capability?.available !== false
  const granted = control.values.advisor.auto_draft_repo_ids
  const listed = repos ?? []
  const known = new Set(listed.map((repo) => repo.repo_id))
  // Only a list that was actually read can say a grant is orphaned. Before it lands there is nothing to
  // compare against, and calling a live grant "a repository you no longer have" would invite the owner to
  // revoke a grant that is working — the one action the orphan row offers.
  const orphans = repos === null ? [] : granted.filter((id) => !known.has(id))
  // Drafting ahead runs through the Advisor and nothing else, so both switches above gate the grant.
  const grantDisabled = control.busy || !advisorAvailable || !control.values.advisor.enabled

  const setGranted = (repoId: string, grant: boolean) => {
    const next = grant ? [...granted, repoId] : granted.filter((id) => id !== repoId)
    // Deduplicated because the server enforces a set and would otherwise store the same id twice.
    control.save({ advisor: { auto_draft_repo_ids: [...new Set(next)] } })
  }

  return (
    <>
      <SettingRow
        id="aidlc-set-advisor"
        title={t('settings.advisor.title')}
        description={t('settings.advisor.desc')}
      >
        {(describedBy) => (
          <div className="studio-row studio-wrapchips">
            {advisorAvailable ? null : <UnavailableChip capability={capability} />}
            <label className="studio-check">
              <input
                type="checkbox"
                checked={control.values.advisor.enabled}
                disabled={control.busy}
                aria-describedby={describedBy}
                onChange={(event) => control.save({ advisor: { enabled: event.target.checked } })}
              />
              <span>{t('settings.advisor.label')}</span>
            </label>
          </div>
        )}
      </SettingRow>

      <SettingRow
        id="aidlc-set-advisor-auto"
        title={t('settings.advisor.autoDraft.title')}
        description={t('settings.advisor.autoDraft.desc')}
      >
        {(describedBy) => (
          <div className="studio-col" aria-describedby={describedBy}>
            {repos === null ? (
              <span className="studio-muted">{t('common.loading')}</span>
            ) : listed.length === 0 && orphans.length === 0 ? (
              <span className="studio-muted">{t('settings.advisor.autoDraft.none')}</span>
            ) : (
              <>
                <ul className="studio-mutelist">
                  {listed.map((repo) => (
                    <li key={repo.repo_id}>
                      <label className="studio-check">
                        <input
                          type="checkbox"
                          checked={granted.includes(repo.repo_id)}
                          disabled={grantDisabled}
                          onChange={(event) => setGranted(repo.repo_id, event.target.checked)}
                          aria-label={t('settings.advisor.autoDraft.label', { repo: repo.label })}
                        />
                        {/* Repo labels are the user's own words: verbatim. */}
                        <span className="studio-strong">{repo.label}</span>
                        <span className="studio-mono studio-muted studio-small studio-trunc">
                          {repo.canonical_path}
                        </span>
                      </label>
                    </li>
                  ))}
                  {orphans.map((id) => (
                    <li key={id}>
                      <label className="studio-check">
                        <input
                          type="checkbox"
                          checked
                          disabled={control.busy}
                          onChange={() => setGranted(id, false)}
                          aria-label={t('settings.advisor.autoDraft.label', { repo: id })}
                        />
                        <span className="studio-mono">{t('settings.advisor.autoDraft.orphan', { id })}</span>
                      </label>
                    </li>
                  ))}
                </ul>
                <span className="studio-muted studio-small">
                  {plural(i18n, 'settings.advisor.autoDraft.count', granted.length)}
                </span>
              </>
            )}
          </div>
        )}
      </SettingRow>

      <SettingRow
        id="aidlc-set-advisor-model"
        title={t('settings.advisor.model.title')}
        description={t('settings.advisor.model.desc')}
      >
        {() => (
          <Chip icon="advisor" tone="aim">
            {t('settings.advisor.model.chip')}
          </Chip>
        )}
      </SettingRow>
    </>
  )
}
