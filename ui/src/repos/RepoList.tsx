/**
 * The registry as a table (visual spec §8.1): Repository | AI-DLC | Engine | Intents | Queue | Git.
 *
 * A real `<table>` with `<th scope>` on both axes, because this genuinely is tabular data and the
 * semantic table is the accessible form of it (PRD §10.3). Below 900px the page renders `RepoCard`
 * summaries instead — a six-column table at 390px is a horizontal-scroll trap, and the card carries the
 * same facts in reading order.
 *
 * Row actions are deliberately thin: `Details` always, and the one maintenance flow that applies. Every
 * other action lives in the detail card, where the evidence for it is on the same screen.
 */

import { useI18n } from '../i18n'
import { plural } from '../lib/format'
import type { RepoRecord } from '../lib/types'
import { Icon } from '../shell/Icon'
import { GitSummaryChips } from './GitPanel'
import { EngineChips, InstallChip, repoActions, type RepoAction } from './RepoCard'

type Flow = 'recover' | 'install' | 'upgrade'
const FLOW_ORDER: readonly Flow[] = ['recover', 'install', 'upgrade']

/** The one flow a row offers inline, if any: recovery beats install beats upgrade. */
function primaryFlow(repo: RepoRecord): Flow | null {
  const actions = repoActions(repo)
  return FLOW_ORDER.find((action) => actions.includes(action)) ?? null
}

export interface RepoListProps {
  repos: RepoRecord[]
  /** False below 900px: the write flows are not offered at all rather than offered and refused. */
  desktop: boolean
  onAction: (repoId: string, action: RepoAction) => void
}

const FLOW_LABEL: Record<Flow, string> = {
  recover: 'repos.action.recover',
  install: 'repos.action.install',
  upgrade: 'repos.action.upgrade',
}

export function RepoList({ repos, desktop, onAction }: RepoListProps) {
  const i18n = useI18n()
  const { t } = i18n

  return (
    <table className="studio-tbl studio-repotbl">
      <caption className="studio-sr">{t('repos.lede')}</caption>
      <thead>
        <tr>
          <th scope="col">{t('repos.col.repository')}</th>
          <th scope="col">{t('repos.col.aidlc')}</th>
          <th scope="col">{t('repos.col.engine')}</th>
          <th scope="col">{t('repos.col.intents')}</th>
          <th scope="col">{t('repos.col.queue')}</th>
          <th scope="col">{t('repos.col.git')}</th>
          <th scope="col">{t('repos.col.actions')}</th>
        </tr>
      </thead>
      <tbody>
        {repos.map((repo) => {
          const flow = desktop ? primaryFlow(repo) : null
          return (
            <tr key={repo.repo_id} data-attention={repo.availability === 'available' ? undefined : 'true'}>
              <th scope="row" className="studio-rowhead">
                <span className="studio-strong">{repo.label}</span>
                {repo.archived ? <span className="studio-muted">{t('repos.metadata.archived')}</span> : null}
                <span className="studio-subpath">{repo.canonical_path}</span>
              </th>
              <td>
                <div className="studio-chiplist">
                  <InstallChip status={repo.install.status} />
                  {repo.availability === 'available' ? null : (
                    <span className="studio-muted">{t(`enum.availability.${repo.availability}`)}</span>
                  )}
                </div>
              </td>
              <td>
                <EngineChips repo={repo} />
              </td>
              <td className="studio-mono">{i18n.fmt.number(repo.counts.intents)}</td>
              <td className="studio-mono">{i18n.fmt.number(repo.counts.open_actions)}</td>
              <td>
                <GitSummaryChips git={repo.git} />
              </td>
              <td>
                <div className="studio-repo-actions">
                  <button
                    type="button"
                    className="studio-btn studio-btn-sm"
                    onClick={() => onAction(repo.repo_id, 'open')}
                  >
                    <Icon name="chevron" size={13} />
                    {t('repos.action.details')}
                  </button>
                  {flow ? (
                    <button
                      type="button"
                      className="studio-btn studio-btn-sm"
                      data-variant={flow === 'recover' ? 'danger' : 'primary'}
                      onClick={() => onAction(repo.repo_id, flow)}
                    >
                      {t(FLOW_LABEL[flow])}
                    </button>
                  ) : null}
                </div>
                {/* The row's full sentence for a screen reader: the visible cells are chips and numbers,
                    which read as fragments out of context (PRD §10.3). */}
                <span className="studio-sr">
                  {t('repos.a11y.repoRow', {
                    label: repo.label,
                    path: repo.canonical_path,
                    install: t(`enum.installStatus.${repo.install.status}`),
                    availability: t(`enum.availability.${repo.availability}`),
                    intents: plural(i18n, 'repos.counts.intents', repo.counts.intents),
                    queue: plural(i18n, 'repos.counts.open', repo.counts.open_actions),
                  })}
                </span>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
