/**
 * A conversation parked on a host tool approval, said where the person looks for waits.
 *
 * Deliberately not an action card (architecture A31): the host owns the approval and its controls —
 * allow once, trust a command or the session, reject — in the conversation, and a second set of buttons
 * here would be a second place to answer the same question. Studio shows what is being asked and opens
 * the conversation (`/chat?sid=<slot>`, the dashboard's own chat route).
 */

import { useNavigate } from '@kirocrew/app-sdk'

import { Icon } from '../shell/Icon'
import { useI18n } from '../i18n'

export interface ApprovalWaitNoticeProps {
  slotKey: string
  tool: string
  toolInput: string
  /** Which intent is waiting, where the surrounding view does not already say (the queue). */
  label?: string
}

export function ApprovalWaitNotice({ slotKey, tool, toolInput, label }: ApprovalWaitNoticeProps) {
  const { t } = useI18n()
  const navigate = useNavigate()
  return (
    <div className="studio-banner" data-tone="warn" role="status">
      <Icon name="warn" size={13} />
      <div className="studio-grow studio-col">
        <span>{label ? t('intents.approval.waitingFor', { label }) : t('intents.approval.waiting')}</span>
        {tool ? <span className="studio-muted">{tool}</span> : null}
        {toolInput ? <code className="studio-mono studio-wrap-any">{toolInput}</code> : null}
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
