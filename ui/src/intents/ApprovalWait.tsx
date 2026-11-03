/**
 * A conversation parked on a host tool approval, said where the person looks for waits.
 *
 * Deliberately not an action card (architecture A31): the host owns the approval and its controls —
 * allow once, trust a command or the session, reject — in the conversation, and a second set of buttons
 * here would be a second place to answer the same question. Studio shows what is being asked, what it is
 * for and where it reaches, then opens the conversation (`/chat?sid=<slot>`, the dashboard's own chat route).
 */

import { useNavigate } from '@kirocrew/app-sdk'

import { Icon } from '../shell/Icon'
import { useShellDataOrNull } from '../shell/StudioApp'
import { useI18n } from '../i18n'
import type { RepoRecord } from '../lib/types'
import { approvalCommand, approvalPurpose, approvalReach } from './approvalReach'

export interface ApprovalWaitNoticeProps {
  slotKey: string
  tool: string
  toolInput: string
  /** What the model says the call is for (the host's `tool_purpose`). */
  purpose?: string
  /** The repository the conversation works in, to tell its paths from everyone else's. */
  repoId?: string
  /** Which intent is waiting, where the surrounding view does not already say (the queue). */
  label?: string
  /** The registered repositories; the shell's list when not given. */
  repos?: readonly RepoRecord[]
}

export function ApprovalWaitNotice(
  { slotKey, tool, toolInput, purpose, repoId, label, repos: given }: ApprovalWaitNoticeProps,
) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const shellRepos = useShellDataOrNull()?.repos.data?.repos
  const repos = given ?? shellRepos ?? []
  const command = approvalCommand(toolInput)
  const why = approvalPurpose(purpose, toolInput)
  const reach = repoId && repos.length ? approvalReach(`${command}\n${tool}`, repoId, repos) : null
  const crosses = reach !== null && reach.otherRepos.length > 0
  return (
    <div className="studio-banner" data-tone={crosses ? 'danger' : 'warn'} role="status">
      <Icon name="warn" size={13} />
      <div className="studio-grow studio-col">
        <span>{label ? t('intents.approval.waitingFor', { label }) : t('intents.approval.waiting')}</span>
        {why ? <span>{t('intents.approval.purpose', { purpose: why })}</span> : null}
        {tool ? <span className="studio-muted studio-wrap-any">{tool}</span> : null}
        {command ? <code className="studio-mono studio-wrap-any">{command}</code> : null}
        {crosses ? (
          <strong className="studio-wrap-any">
            {t('intents.approval.otherRepo', {
              repos: reach.otherRepos.map((repo) => `${repo.label} (${repo.path})`).join(', '),
            })}
          </strong>
        ) : null}
        {reach && reach.outside.length > 0 ? (
          <span className="studio-wrap-any">{t('intents.approval.outside', { paths: reach.outside.join(', ') })}</span>
        ) : null}
        {reach && !crosses && reach.outside.length === 0 ? (
          <span className="studio-muted">{t('intents.approval.inside')}</span>
        ) : null}
        <span className="studio-muted">{t('intents.approval.where')}</span>
      </div>
      <button
        type="button"
        className="studio-btn studio-btn-sm"
        onClick={() => navigate(`/chat?sid=${encodeURIComponent(slotKey)}`)}
      >
        <Icon name="link" size={13} />
        {t('intents.approval.open')}
      </button>
    </div>
  )
}
