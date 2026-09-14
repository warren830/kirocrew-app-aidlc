/**
 * Step 1 — Work: which repository, which space, and what the engine is being asked to do.
 *
 * Two things here are not cosmetic:
 *
 *  - **The space is not a preference.** `utility.intent_create` has no `--space` flag; it writes into the
 *     space AI-DLC's own cursor points at. So the other spaces are listed (hiding them would make a
 *     multi-space install look broken) and are not selectable, with the reason said out loud.
 *  - **The label is validated here because it becomes a directory name.** AI-DLC slugifies it into the
 *     record's path and nothing renames it afterwards, so a label the engine would reject must fail in
 *     this field rather than as a `bad_body` after four steps. An objective written in a language that
 *     slugifies to nothing (every Chinese objective does) leaves the derivation empty, which is exactly
 *     when the field becomes required.
 *
 * The observed signals are read-only facts from `RepoRecord`/`/health` — never edited, never inferred.
 */

import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { useI18n } from '../i18n'
import { count, plural } from '../lib/format'
import type { RepoRecord } from '../lib/types'
import type { WizardState } from './WizardView'

export interface StepWorkProps {
  state: WizardState
  repos: RepoRecord[]
  repo: RepoRecord | null
  blocked: 'availability' | 'not_installed' | 'recovery_required' | null
  spaces: string[]
  activeSpace: string
  derivedLabel: string
  labelValid: boolean
  bunMissing: boolean
  /** `/health`'s `tools.bun.searched`: every location the probe tried, in the order it tried them. */
  bunSearched: string[]
  graphStageCount: number | null
  intentCount: number | null
  onPatch: (patch: Partial<WizardState>) => void
  onOpenRepos: () => void
}

export function StepWork({
  state, repos, repo, blocked, spaces, activeSpace, derivedLabel, labelValid, bunMissing, bunSearched,
  graphStageCount, intentCount, onPatch, onOpenRepos,
}: StepWorkProps) {
  const i18n = useI18n()
  const { t } = i18n
  const spaceList = spaces.length ? spaces : activeSpace ? [activeSpace] : []

  return (
    <div className="studio-wiz-fields">
      <div className="studio-field">
        <label htmlFor="wizard-repo">{t('wizard.work.repo')}</label>
        <p className="studio-help">{t('wizard.work.repoHelp')}</p>
        {repos.length === 0 ? (
          <p className="studio-consequence">
            <Icon name="info" size={13} />
            <span>{t('wizard.work.repoNone')}</span>
          </p>
        ) : (
          <select
            id="wizard-repo"
            value={state.repo}
            onChange={(event) => onPatch({ repo: event.target.value, scope: '', overrides: {} })}
          >
            <option value="">{t('wizard.work.repoPick')}</option>
            {repos.map((candidate) => {
              const reason = reasonFor(candidate)
              return (
                <option key={candidate.repo_id} value={candidate.repo_id} disabled={reason !== null}>
                  {reason === null
                    ? `${candidate.label} — ${candidate.canonical_path}`
                    : t('wizard.work.repoUnusable', { label: candidate.label, reason: reasonText(t, reason, candidate) })}
                </option>
              )
            })}
          </select>
        )}
        {repo && blocked ? (
          <p className="studio-banner" data-tone="warn" role="status">
            <Icon name="warn" size={15} />
            <span className="studio-grow">{t('wizard.work.repoBlocked', { reason: reasonText(t, blocked, repo) })}</span>
            <button type="button" className="studio-btn" onClick={onOpenRepos}>
              {t('wizard.work.repoBlockedOpen')}
            </button>
          </p>
        ) : null}
      </div>

      <div className="studio-field">
        <label htmlFor="wizard-space">{t('wizard.work.space')}</label>
        <p className="studio-help">{t('wizard.work.spaceHelp')}</p>
        {spaceList.length === 0 ? (
          <p className="studio-muted">{t('wizard.work.spaceUnknown')}</p>
        ) : (
          <select id="wizard-space" value={activeSpace} disabled onChange={() => undefined}>
            {spaceList.map((space) => (
              <option key={space} value={space}>
                {space === activeSpace
                  ? t('wizard.work.spaceActive', { space })
                  : t('wizard.work.spaceInactive', { space })}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="studio-field">
        <label htmlFor="wizard-objective">{t('wizard.work.objective')}</label>
        <p className="studio-help">{t('wizard.work.objectiveHelp')}</p>
        <input
          id="wizard-objective"
          type="text"
          value={state.objective}
          placeholder={t('wizard.work.objectivePlaceholder')}
          aria-describedby="wizard-objective-help"
          onChange={(event) => onPatch({ objective: event.target.value })}
        />
        <p className="studio-help" id="wizard-objective-help">
          {state.objective.trim() ? '' : t('wizard.work.objectiveRequired')}
        </p>
      </div>

      <div className="studio-field">
        <label htmlFor="wizard-context">{t('wizard.work.context')}</label>
        <p className="studio-help">{t('wizard.work.contextHelp')}</p>
        <textarea
          id="wizard-context"
          value={state.context}
          placeholder={t('wizard.work.contextPlaceholder')}
          onChange={(event) => onPatch({ context: event.target.value })}
        />
      </div>

      <div className="studio-field">
        <label htmlFor="wizard-label">{t('wizard.work.label')}</label>
        <p className="studio-help">{t('wizard.work.labelHelp')}</p>
        <input
          id="wizard-label"
          type="text"
          value={state.label}
          placeholder={t('wizard.work.labelPlaceholder')}
          aria-invalid={labelValid ? 'false' : 'true'}
          aria-describedby="wizard-label-state"
          onChange={(event) => onPatch({ label: event.target.value })}
        />
        <p className="studio-help" id="wizard-label-state">
          {state.label.trim()
            ? labelValid
              ? ''
              : t('wizard.work.labelInvalid')
            : derivedLabel
              ? t('wizard.work.labelDerived', { label: derivedLabel })
              : t('wizard.work.labelUndeducible')}
        </p>
      </div>

      <div className="studio-field">
        <label htmlFor="wizard-project-type">{t('wizard.work.projectType')}</label>
        <p className="studio-help">{t('wizard.work.projectTypeHelp')}</p>
        <select
          id="wizard-project-type"
          value={state.projectType}
          onChange={(event) => onPatch({ projectType: event.target.value as WizardState['projectType'] })}
        >
          <option value="">{t('wizard.work.projectTypeUnset')}</option>
          {/* AI-DLC's own values: shown verbatim in every locale. */}
          <option value="Greenfield">Greenfield</option>
          <option value="Brownfield">Brownfield</option>
        </select>
      </div>

      {repo ? (
        <div className="studio-field">
          <span className="studio-field-label">{t('wizard.work.signals')}</span>
          <p className="studio-help">{t('wizard.work.signalsHelp')}</p>
          <div className="studio-row studio-wrap">
            {repo.install.engine_version ? (
              <Chip tone="ok" icon="check">{t('wizard.work.signal.engine', { version: repo.install.engine_version })}</Chip>
            ) : (
              <Chip icon="warn" tone="warn">{t('wizard.work.signal.engineUnknown')}</Chip>
            )}
            {repo.install.drift_count > 0 ? (
              <Chip tone="warn" icon="warn">{`${count(i18n, repo.install.drift_count)} ${t('wizard.work.signal.drift')}`}</Chip>
            ) : null}
            {repo.git ? (
              repo.git.available ? (
                <Chip icon="git">
                  {`${t('wizard.work.signal.gitBranch', { branch: repo.git.branch ?? '—' })} · ${
                    repo.git.dirty ? plural(i18n, 'wizard.work.signal.gitDirty', repo.git.dirty_files) : t('wizard.work.signal.gitClean')
                  }`}
                </Chip>
              ) : (
                <Chip icon="git">{t('wizard.work.signal.gitUnavailable')}</Chip>
              )
            ) : null}
            {graphStageCount !== null ? (
              <Chip icon="map">{t('wizard.work.signal.stages', { n: i18n.fmt.number(graphStageCount) })}</Chip>
            ) : null}
            {intentCount !== null ? (
              <Chip icon="intent">{plural(i18n, 'wizard.work.signal.intents', intentCount)}</Chip>
            ) : null}
          </div>
          {bunMissing ? (
            // A banner, not one more chip in the row above: chips are `white-space: nowrap` single
            // facts, and this is the reason nothing in the wizard can run plus the two things that fix
            // it. It is worded as "not found" because launchd starts the desktop gateway with a
            // four-entry PATH (A28), so the case that actually happens is a working `~/.bun` or
            // Homebrew install this process cannot see — telling that user bun "is not installed"
            // sent them to reinstall software they already had.
            <p className="studio-banner" data-tone="danger" role="status">
              <Icon name="warn" size={15} />
              <span className="studio-grow">
                {t('errors.bun_missing')}
                {bunSearched.length > 0 ? (
                  <span className="studio-subpath">
                    {t('errors.bun_missing_searched', { locations: bunSearched.join(t('shell.format.listJoin')) })}
                  </span>
                ) : null}
              </span>
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function reasonFor(repo: RepoRecord): 'availability' | 'not_installed' | 'recovery_required' | null {
  if (repo.availability !== 'available') return 'availability'
  if (repo.install.status === 'recovery_required') return 'recovery_required'
  if (repo.install.status === 'not_installed') return 'not_installed'
  return null
}

function reasonText(
  t: (key: string, params?: Record<string, string | number>) => string,
  reason: 'availability' | 'not_installed' | 'recovery_required',
  repo: RepoRecord,
): string {
  if (reason === 'availability') return t(`enum.availability.${repo.availability}`)
  if (reason === 'recovery_required') return t('wizard.work.repoRecovery')
  return t('wizard.work.repoNotInstalled')
}
