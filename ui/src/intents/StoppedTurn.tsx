/**
 * A conversation whose last turn stopped on a host error, said where the person looks for waits.
 *
 * Not an action card: nothing waits on a decision, and the host's own error row offers its fix. Without it a
 * run that died on a model error read as "Idle" with an empty queue, as if AI-DLC had nothing left to do.
 * Studio repeats the host's text, says what sending again does, and opens the conversation.
 */

import { useNavigate } from '@kirocrew/app-sdk'

import { Icon } from '../shell/Icon'
import { useI18n } from '../i18n'
import { since } from '../lib/format'
import { WIRE } from '../lib/wire.generated'

/** Host error kinds a retry cannot get past: the fix is in KiroCrew, not in sending `/aidlc` again. */
const NEEDS_A_FIX = new Set(['model_unentitled', 'auth_required', 'usage_limit'])

export interface StoppedTurnNoticeProps {
  slotKey: string
  at: string
  text: string
  kind: string
  /** Which intent stopped, where the surrounding view does not already say (the queue). */
  label?: string
}

export function StoppedTurnNotice({ slotKey, at, text, kind, label }: StoppedTurnNoticeProps) {
  const i18n = useI18n()
  const { t } = i18n
  const navigate = useNavigate()
  const when = since(i18n, at)
  const next = NEEDS_A_FIX.has(kind) ? 'intents.stopped.fix' : label ? 'intents.stopped.retryFrom' : 'intents.stopped.retry'
  return (
    <div className="studio-banner" data-tone="warn" role="status">
      <Icon name="warn" size={13} />
      <div className="studio-grow studio-col">
        <span>{label ? t('intents.stopped.titleFor', { label, when }) : t('intents.stopped.title', { when })}</span>
        {text ? <span className="studio-muted studio-wrap-any">{text}</span> : null}
        <span className="studio-muted">{t(next, { text: WIRE.RUN })}</span>
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
