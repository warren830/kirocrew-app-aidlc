/**
 * Start (or resume) an intent's workflow in one user action.
 *
 * The button or confirmation that calls `start` shows the exact text it sends, so that click is the
 * human lane's confirmation (PRD P-03): `POST …/run` queues the command card and the same two-phase
 * submit the Action Center uses sends it — at most once, slot-checked first, never retried. When the
 * send does not go through, the card stays in the Action Center with its recorded outcome.
 */

import { useCallback, useState } from 'react'

import { useSubmit } from '../actions/useSubmit'
import { Icon } from '../shell/Icon'
import { useI18n } from '../i18n'
import { StudioApiError, type StudioApi } from '../lib/api'
import type { IntentSummary } from '../lib/types'
import { WIRE } from '../lib/wire.generated'

export type StartKind = 'run' | 'resume'

/** The text the start sends, which every button that starts it must display. */
export function startText(kind: StartKind): string {
  return kind === 'run' ? WIRE.RUN : WIRE.RESUME
}

/** A parked intent resumes; anything else runs. */
export function startKind(intent: IntentSummary): StartKind {
  return intent.disk.parked_at ? 'resume' : 'run'
}

/** Why this intent cannot be started now, or `null`. The checks the run route itself would refuse on. */
export function startBlockedReason(intent: IntentSummary, t: (key: string) => string): string | null {
  if (intent.archived) return t('intents.run.disabledArchived')
  if (intent.paused) return t('intents.run.disabledPaused')
  if (intent.session?.running) return t('intents.run.disabledRunning')
  if (intent.counts.awaiting_approval > 0 || intent.operational_state === 'WaitingForYou') {
    return t('intents.run.disabledCheckpoint')
  }
  if (intent.disk.status?.toLowerCase() === 'completed') return t('intents.run.disabledCompleted')
  return null
}

export function useStartRun(api: StudioApi, onSettled?: () => void) {
  const { submit, reset, state, busy } = useSubmit({ api, ...(onSettled ? { onSettled } : {}) })
  const [kind, setKind] = useState<StartKind>('run')
  const [queueing, setQueueing] = useState(false)
  /** A refusal before any card existed: the run route itself said no. */
  const [failure, setFailure] = useState<StudioApiError | null>(null)

  const start = useCallback(
    async (intent: IntentSummary, which: StartKind) => {
      setKind(which)
      setFailure(null)
      reset()
      setQueueing(true)
      try {
        const queued = which === 'run'
          ? await api.run(intent.repo_id, intent.intent_key)
          : await api.resume(intent.repo_id, intent.intent_key)
        setQueueing(false)
        await submit({ card: queued.action, payload: { decision: which }, clientWireText: startText(which) })
      } catch (caught) {
        setFailure(caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0))
      } finally {
        setQueueing(false)
      }
    },
    [api, submit, reset],
  )

  return { start, state, kind, failure, busy: busy || queueing }
}

export type StartRun = ReturnType<typeof useStartRun>

/** The single line that says what the start did: sending, sent, or why it did not go. */
export function StartStatus({ run }: { run: StartRun }) {
  const { t } = useI18n()
  const text = startText(run.kind)
  const { stage, attempt, refusal, actionId } = run.state
  const reason = (error: StudioApiError) => error.message || t(`errors.${error.code}`)

  if (run.failure) {
    return <Line tone="warn" icon="warn" text={t('intents.start.refused', { text, reason: reason(run.failure) })} />
  }
  if (run.busy && stage !== 'settled') {
    return <Line icon="clock" text={t('intents.start.sending', { text })} />
  }
  if (stage === 'settled' && attempt) {
    if (attempt.outcome === 'delivered' && attempt.reported) {
      return <Line tone="ok" icon="check" text={t('intents.start.sent', { text })} />
    }
    if (attempt.outcome === 'not_delivered') {
      const why = attempt.receipt?.error ?? attempt.receipt?.code ?? ''
      return <Line tone="warn" icon="warn" text={t('intents.start.notSent', { text, reason: why, action: actionId ?? '' })} />
    }
    return <Line tone="warn" icon="warn" text={t('intents.start.uncertain', { text, action: actionId ?? '' })} />
  }
  if (stage === 'refused' && refusal) {
    return <Line tone="warn" icon="warn" text={t('intents.start.refused', { text, reason: reason(refusal) })} />
  }
  if (stage === 'stale') {
    return <Line tone="warn" icon="warn" text={t('intents.start.stale', { text, action: actionId ?? '' })} />
  }
  return null
}

function Line({ text, icon, tone }: { text: string; icon: 'check' | 'warn' | 'clock'; tone?: 'ok' | 'warn' }) {
  return (
    <p className={tone ? 'studio-banner' : 'studio-muted'} {...(tone ? { 'data-tone': tone } : {})}
      role={tone === 'warn' ? 'alert' : 'status'}>
      <Icon name={icon} size={13} />
      <span className="studio-grow">{text}</span>
    </p>
  )
}
