/**
 * Git observation — read-only, and split the way an upgrade cares about (FR-GIT-001/004).
 *
 * The important distinction lives in `GET /repos/{id}/git`: `owned_dirty` are receipt-owned files with
 * local changes, which is what stops an upgrade, and `unrelated_dirty` is a *count* of the user's own
 * work in progress, which must never stop one. Presenting a single "dirty" flag would make an upgrade
 * look blocked by unrelated work, which is the exact confusion FR-GIT-004 exists to prevent.
 *
 * A Git failure is a degraded capability, not missing workflow state (FR-GIT-005): the panel says the
 * observation is off and everything else on the page keeps working.
 */

import { useCallback } from 'react'

import { useI18n } from '../i18n'
import { plural, since as fmtSince } from '../lib/format'
import type { StudioApi } from '../lib/api'
import { useResource } from '../lib/useResource'
import type { GitObservation, RepoGitResponse } from '../lib/types'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { Block, ErrorNote, Note } from './PreflightReport'

/**
 * The one-line form used in the repo table and on the card header.
 *
 * Exported because the table needs exactly this and nothing more: a branch, a clean/dirty word, and the
 * ahead/behind pair when Git could be asked safely.
 */
export function GitSummaryChips({ git }: { git: GitObservation | null }) {
  const i18n = useI18n()
  const { t } = i18n

  if (!git || !git.available) {
    return (
      <Chip tone="warn" icon="warn" title={git?.reason ?? undefined}>
        {t('repos.git.unavailable')}
      </Chip>
    )
  }
  return (
    <span className="studio-chiplist">
      <Chip icon="git" mono>
        {git.detached ? t('repos.git.detached') : (git.branch ?? t('repos.git.detached'))}
      </Chip>
      <Chip tone={git.dirty ? 'warn' : 'ok'} icon={git.dirty ? 'warn' : 'check'}>
        {git.dirty ? plural(i18n, 'repos.git.dirty', git.dirty_files) : t('repos.git.clean')}
      </Chip>
      {git.ahead === null ? null : <Chip mono>{plural(i18n, 'repos.git.ahead', git.ahead)}</Chip>}
      {git.behind === null ? null : <Chip mono>{plural(i18n, 'repos.git.behind', git.behind)}</Chip>}
    </span>
  )
}

export interface GitPanelProps {
  api: StudioApi
  repoId: string
  /** False for an unreadable repository: asking Git about a directory Studio cannot read is noise. */
  enabled: boolean
}

export function GitPanel({ api, repoId, enabled }: GitPanelProps) {
  const i18n = useI18n()
  const { t } = i18n

  const git = useResource<RepoGitResponse>(
    enabled ? `repo-git:${repoId}` : null,
    useCallback((signal) => api.repoGit(repoId, { signal }), [api, repoId]),
    // A branch and a dirty count do not change on their own; the events that would change them force a
    // refetch anyway, and `git status` on a large repository is not something to run every 15 seconds.
    { interval: 0, revalidateOn: ['repo.updated', 'transaction.updated', 'reset'] },
  )

  return (
    <Block title={t('repos.git.title')} icon="git">
      {git.error ? <ErrorNote error={git.error} /> : null}
      {git.data ? (
        <>
          <GitSummaryChips git={git.data.git} />
          {git.data.git.available ? (
            <>
              <p className="studio-subpath">
                {git.data.git.head ? `${t('repos.git.head', { sha: git.data.git.head.slice(0, 12) })}  ·  ` : ''}
                {git.data.git.head_subject ?? ''}
              </p>
              <p className="studio-note">
                <Icon name="info" size={13} />{' '}
                {git.data.git.upstream
                  ? t('repos.git.upstream', { upstream: git.data.git.upstream })
                  : t('repos.git.noUpstream')}
                {'  ·  '}
                {t('repos.git.observedAt', { when: fmtSince(i18n, git.data.git.observed_at) })}
              </p>

              <h4 className="studio-subhead">{t('repos.git.ownedDirty.title')}</h4>
              {git.data.owned_dirty.length === 0 ? (
                <p className="studio-muted">{t('repos.git.ownedDirty.none')}</p>
              ) : (
                <>
                  <ul className="studio-plain-list studio-mono">
                    {git.data.owned_dirty.map((path) => (
                      <li className="studio-wrap-any" key={path}>
                        {path}
                      </li>
                    ))}
                  </ul>
                  <Note tone="warn">{t('repos.git.ownedDirty.body')}</Note>
                </>
              )}
              {git.data.unrelated_dirty > 0 ? (
                <Note>{plural(i18n, 'repos.git.unrelatedDirty', git.data.unrelated_dirty)}</Note>
              ) : null}
            </>
          ) : null}
        </>
      ) : git.loading ? (
        <p className="studio-muted">{t('common.loading')}</p>
      ) : null}

      <Note>{t('repos.git.readOnly')}</Note>
    </Block>
  )
}
