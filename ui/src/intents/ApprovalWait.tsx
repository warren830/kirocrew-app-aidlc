/**
 * A conversation parked on a host tool approval, said where the person looks for waits.
 *
 * Not an action card (architecture A31): a tool approval is not a workflow decision, and one is asked per command.
 * Studio shows what is being asked, what it is for and where it reaches. From KiroCrew 0.11 the person can answer it
 * here too, with the host's own call (`answerApproval`, the shape its attention card sends): **Allow once** lets just
 * this call run and **Reject** refuses just this call. Trusting a command or the whole conversation, and YOLO, stay in
 * the conversation, which **Open the conversation** opens (`/chat?sid=<slot>`, the dashboard's own chat route). Only a
 * native request the host names by its row (`request_mid`) is answered here; a sub-agent's waits for the chat.
 */

import { useState } from 'react'
import { useNavigate } from '@kirocrew/app-sdk'

import { Icon } from '../shell/Icon'
import { useShellDataOrNull } from '../shell/StudioApp'
import { useI18n } from '../i18n'
import { StudioApiError, useStudioApi } from '../lib/api'
import type { RepoRecord } from '../lib/types'
import { approvalCommand, approvalPurpose, approvalReach, approvalTargets } from './approvalReach'

/** Which waiting request the host would resolve: its id, the row instance the person saw, and who asked. */
export interface ApprovalTarget { request_id?: string; request_mid?: string; origin?: string }

export interface ApprovalWaitNoticeProps {
  slotKey: string
  tool: string
  toolInput: string
  /** The call is longer than Studio shows: nothing here may vouch for, or allow, what is not on screen. */
  toolInputTruncated?: boolean
  /** What the model says the call is for (the host's `tool_purpose`). */
  purpose?: string
  /** The host's kind of call, as ACP states it (`execute`, `edit`, …): which text names where it reaches. */
  toolKind?: string | null
  /** The repository the conversation works in, to tell its paths from everyone else's. */
  repoId?: string
  /** Which intent is waiting, where the surrounding view does not already say (the queue). */
  label?: string
  /** The registered repositories; the shell's list when not given. */
  repos?: readonly RepoRecord[]
  /** The host's request, when it can be answered here. */
  target?: ApprovalTarget
  /** After an answer the host took, or found already taken: re-read what waits. */
  onAnswered?: () => void
}

type Answer = 'approved' | 'rejected_once'

/** A native request the host names by its row: the only kind answered from Studio. */
export function answerableHere(target: ApprovalTarget | undefined): target is Required<ApprovalTarget> {
  return target?.origin === 'native' && !!target.request_id && !!target.request_mid
}

/** An answer settles one request, so the conversation's next one starts unanswered even where the slot stays the same. */
export function ApprovalWaitNotice(props: ApprovalWaitNoticeProps) {
  return <RequestNotice key={JSON.stringify([props.slotKey, props.target?.request_id, props.target?.request_mid])} {...props} />
}

function RequestNotice(
  { slotKey, tool, toolInput, toolInputTruncated, purpose, toolKind, repoId, label, repos: given, target, onAnswered }: ApprovalWaitNoticeProps,
) {
  const { t } = useI18n()
  const navigate = useNavigate()
  const api = useStudioApi()
  const shellRepos = useShellDataOrNull()?.repos.data?.repos
  const repos = given ?? shellRepos ?? []
  const [busy, setBusy] = useState<Answer | null>(null)
  const [outcome, setOutcome] = useState<{ tone: 'done' | 'gone' | 'error'; text: string } | null>(null)
  const command = approvalCommand(toolInput, toolKind)
  const why = approvalPurpose(purpose, toolInput)
  const partial = toolInputTruncated === true
  const targets = approvalTargets(toolInput, toolKind)
  // Where a call reaches is read from its text; from part of it, "names no path outside" could be false.
  const reach = !partial && repoId && repos.length
    ? approvalReach({ commands: [...targets.commands, tool], paths: targets.paths }, repoId, repos) : null
  const crosses = reach !== null && reach.otherRepos.length > 0
  const inside = reach !== null && targets.whole && !crosses && reach.outside.length === 0 && reach.unplaced.length === 0
  // The host titles a shell call "Running: <command>": the command below already says it.
  const title = tool && !(command && tool.includes(command)) ? tool : ''
  const answerable = answerableHere(target)
  // Allow once answers for the whole call, so it is offered only for a call shown whole; Reject is always safe.
  const allowable = answerable && !partial

  const answer = async (action: Answer) => {
    if (!answerableHere(target) || busy) return
    setBusy(action)
    setOutcome(null)
    try {
      await api.answerApproval(slotKey, { request_id: target.request_id, request_mid: target.request_mid }, action)
      setOutcome({ tone: 'done', text: t(action === 'approved' ? 'intents.approval.allowed' : 'intents.approval.rejected') })
      onAnswered?.()
    } catch (error) {
      if (error instanceof StudioApiError && error.code === 'approval_not_pending') {
        // Answered in the chat meanwhile, timed out, or a newer request took its place: nothing waits on this one.
        setOutcome({ tone: 'gone', text: t('intents.approval.gone') })
        onAnswered?.()
      } else {
        setOutcome({ tone: 'error', text: t('intents.approval.failed', { error: error instanceof Error ? error.message : String(error) }) })
      }
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="studio-banner" data-tone={crosses ? 'danger' : 'warn'} role="status">
      <Icon name="warn" size={13} />
      <div className="studio-grow studio-col">
        <span>{label ? t('intents.approval.waitingFor', { label }) : t('intents.approval.waiting')}</span>
        {why ? <span>{t('intents.approval.purpose', { purpose: why })}</span> : null}
        {title ? <span className="studio-muted studio-wrap-any">{title}</span> : null}
        {/* Verbatim: a `python3 -c` program or a file write's diff is read line by line, and its indentation is code. */}
        {command ? <code className="studio-mono studio-wrap-any studio-verbatim studio-approval-cmd">{command}</code> : null}
        {crosses ? (
          <strong className="studio-wrap-any">
            {t('intents.approval.otherRepo', {
              repos: reach.otherRepos.map((repo) => `${repo.label} (${repo.path})`).join(', '),
            })}
          </strong>
        ) : null}
        {reach && reach.outside.length > 0 ? (
          <strong className="studio-wrap-any">
            {t('intents.approval.outside', { paths: reach.outside.join(', ') })}
          </strong>
        ) : null}
        {reach && reach.unplaced.length > 0 ? (
          <strong className="studio-wrap-any">
            {t('intents.approval.unplaced', { paths: reach.unplaced.join(', ') })}
          </strong>
        ) : null}
        {reach && !targets.whole ? <strong className="studio-wrap-any">{t('intents.approval.unread')}</strong> : null}
        {partial ? <strong className="studio-wrap-any">{t('intents.approval.partial')}</strong> : null}
        {inside ? <span className="studio-muted">{t('intents.approval.inside')}</span> : null}
        <span className="studio-muted">{t(answerable ? 'intents.approval.whereHere' : 'intents.approval.where')}</span>
        {outcome ? (
          <span className={outcome.tone === 'error' ? 'studio-error' : 'studio-muted'} role={outcome.tone === 'error' ? 'alert' : undefined}>
            {outcome.text}
          </span>
        ) : null}
        {/* Under the text, not beside it: in the queue's column a button alongside left the text a word wide. */}
        <div className="studio-row">
          {answerable && outcome?.tone !== 'done' && outcome?.tone !== 'gone' ? (
            <>
              {allowable ? (
                <button
                  type="button"
                  className="studio-btn studio-btn-sm"
                  data-variant="primary"
                  disabled={busy !== null}
                  onClick={() => void answer('approved')}
                >
                  <Icon name="check" size={13} />
                  {busy === 'approved' ? t('intents.approval.sending') : t('intents.approval.allowOnce')}
                </button>
              ) : null}
              <button
                type="button"
                className="studio-btn studio-btn-sm"
                disabled={busy !== null}
                onClick={() => void answer('rejected_once')}
              >
                {busy === 'rejected_once' ? t('intents.approval.sending') : t('intents.approval.reject')}
              </button>
            </>
          ) : null}
          <button
            type="button"
            className="studio-btn studio-btn-sm"
            onClick={() => navigate(`/chat?sid=${encodeURIComponent(slotKey)}`)}
          >
            <Icon name="link" size={13} />
            {t('intents.approval.open')}
          </button>
        </div>
      </div>
    </div>
  )
}
