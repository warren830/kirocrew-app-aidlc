/**
 * Turn AI-DLC's per-stage summary confirmation off, or back on, for one intent.
 *
 * The setting is the engine's: `- **Summary Confirmation**: on (from scope feature)` in the intent's state file, shown
 * here as written and never re-worded. A person turns it off by typing AI-DLC's own switch with nothing beside it,
 * `/aidlc --summary-confirmation off`: the prompt hook applies it as theirs (`off (set by you)`) when the turn arrives
 * and records the turn as a command, so it answers no open question. `on` is forwarded to the conductor, whose setter
 * writes it. Studio sends exactly that text as the person's own turn, through the same two-phase submit every decision
 * uses: the confirmation shows the bytes, and the send happens at most once and never without the click.
 *
 * Three rules are load-bearing:
 *  - **Its own turn.** Never folded into a checkpoint's reply: `Looks correct` is matched exactly, and the switch
 *    typed beside other words is read as a flag of new work, not as a switch.
 *  - **Between turns only.** A running, unbound, paused or archived intent would refuse the send, so the control
 *    says why instead of offering it.
 *  - **An open confirmation stays open.** AI-DLC does not withdraw a summary confirmation already asked; it still
 *    records the answer to it, and no later stage asks again. The confirmation panel says so before anything is sent.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { useSubmit } from '../actions/useSubmit'
import { useI18n } from '../i18n'
import { StudioApiError, type StudioApi } from '../lib/api'
import type { ActionCard, CeremonySetting, IntentSummary } from '../lib/types'
import { WIRE } from '../lib/wire.generated'
import { Icon } from '../shell/Icon'

export type SummarySetting = 'on' | 'off'

/** The text the switch sends, which the confirmation must display. */
export function summarySwitchText(value: SummarySetting): string {
  return value === 'on' ? WIRE.SUMMARY_CONFIRMATION_ON : WIRE.SUMMARY_CONFIRMATION_OFF
}

/** What the control offers: back on when AI-DLC recorded it off, otherwise off. */
export function summarySwitchTarget(setting: CeremonySetting | null | undefined): SummarySetting {
  return setting?.value === 'off' ? 'on' : 'off'
}

/** Why the intent's conversation would refuse the switch now, or `null`. The checks the send itself makes. */
export function summarySwitchBlockedReason(intent: IntentSummary, t: (key: string) => string): string | null {
  if (intent.archived) return t('intents.run.disabledArchived')
  if (intent.paused) return t('intents.run.disabledPaused')
  if (!intent.session?.slot_key) return t('intents.run.disabledUnbound')
  if (intent.session.running) return t('intents.run.disabledRunning')
  if (intent.disk.status?.toLowerCase() === 'completed') return t('summarySwitch.blocked.completed')
  return null
}

/** The same question asked of a card: its live evidence names the bound conversation and whether it is running. */
export function cardSummarySwitchBlockedReason(card: ActionCard, t: (key: string) => string): string | null {
  const session = card.evidence.session
  if (!session?.slot_key) return t('intents.run.disabledUnbound')
  if (session.running) return t('intents.run.disabledRunning')
  return null
}

export function useSummaryConfirmationSwitch(api: StudioApi, onSettled?: () => void) {
  const { submit, reset, state, busy } = useSubmit({ api, ...(onSettled ? { onSettled } : {}) })
  const [value, setValue] = useState<SummarySetting>('off')
  const [queueing, setQueueing] = useState(false)
  /** A refusal before any card existed: the route itself said no. */
  const [failure, setFailure] = useState<StudioApiError | null>(null)

  const send = useCallback(
    async (repoId: string, intentKey: string, which: SummarySetting) => {
      setValue(which)
      setFailure(null)
      reset()
      setQueueing(true)
      try {
        const queued = await api.summaryConfirmation(repoId, intentKey, which)
        setQueueing(false)
        await submit({
          card: queued.action,
          payload: { decision: 'set_summary_confirmation', value: which },
          clientWireText: summarySwitchText(which),
        })
      } catch (caught) {
        setFailure(caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0))
      } finally {
        setQueueing(false)
      }
    },
    [api, submit, reset],
  )

  return { send, state, value, failure, busy: busy || queueing }
}

export type SummarySwitch = ReturnType<typeof useSummaryConfirmationSwitch>

export interface SummaryConfirmationSwitchProps {
  api: StudioApi
  repoId: string
  intentKey: string
  /** The intent's line as AI-DLC wrote it; `null` when the state file has none (the scope's default applies). */
  setting: CeremonySetting | null | undefined
  /** Why a send would be refused now, or `null`. */
  blockedReason: string | null
  /** Shown on an open summary confirmation: the confirmation says what happens to it. */
  pending?: boolean
  onSettled?: () => void
}

export function SummaryConfirmationSwitch({
  api, repoId, intentKey, setting, blockedReason, pending = false, onSettled,
}: SummaryConfirmationSwitchProps) {
  const { t } = useI18n()
  const sw = useSummaryConfirmationSwitch(api, onSettled)
  const [confirming, setConfirming] = useState(false)
  const confirmRef = useRef<HTMLButtonElement | null>(null)
  const target = summarySwitchTarget(setting)

  // A confirmation that appears out of nowhere is unusable with a keyboard, so focus follows it.
  useEffect(() => {
    if (confirming) confirmRef.current?.focus()
  }, [confirming])

  const confirm = async () => {
    setConfirming(false)
    await sw.send(repoId, intentKey, target)
  }

  return (
    <div className="studio-col studio-summary-switch" role="group" aria-label={t('summarySwitch.title')}>
      <p className="studio-wrap-any">
        <span className="studio-field-label">{t('summarySwitch.title')}</span>{' '}
        <span>{setting ? t(`summarySwitch.value.${setting.value}`) : t('summarySwitch.value.unrecorded')}</span>
        {setting ? (
          <>
            {' · '}
            {/* The engine's own words for who set it, verbatim. */}
            <span className="studio-muted">{t('summarySwitch.recorded')}</span>{' '}
            <code className="studio-mono">{setting.line}</code>
          </>
        ) : null}
      </p>
      <p className="studio-help">
        {t(target === 'off' ? 'summarySwitch.explain.on' : pending ? 'summarySwitch.explain.offPending' : 'summarySwitch.explain.off')}
      </p>
      {confirming ? (
        <div
          className="studio-confirm"
          role="group"
          aria-label={t(`summarySwitch.confirm.${target}.title`)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') setConfirming(false)
          }}
        >
          <h3>{t(`summarySwitch.confirm.${target}.title`)}</h3>
          <p>{t(`summarySwitch.confirm.${target}.body`)}</p>
          <pre className="studio-sendtext" aria-label={t('summarySwitch.confirm.text')}>{summarySwitchText(target)}</pre>
          {pending && target === 'off' ? (
            <p className="studio-consequence">
              <Icon name="info" size={13} />
              <span>{t('summarySwitch.confirm.pending')}</span>
            </p>
          ) : null}
          <div className="studio-row">
            <button
              type="button"
              className="studio-btn studio-btn-primary studio-btn-sm"
              ref={confirmRef}
              disabled={sw.busy || blockedReason !== null}
              onClick={() => void confirm()}
            >
              {t('summarySwitch.send', { text: summarySwitchText(target) })}
            </button>
            <button type="button" className="studio-btn studio-btn-sm" onClick={() => setConfirming(false)}>
              {t('common.cancel')}
            </button>
          </div>
        </div>
      ) : (
        <div className="studio-row studio-wrap">
          <button
            type="button"
            className="studio-btn studio-btn-sm"
            disabled={sw.busy || blockedReason !== null}
            {...(blockedReason ? { title: blockedReason } : {})}
            onClick={() => setConfirming(true)}
          >
            <Icon name={target === 'off' ? 'pause' : 'play'} size={13} />
            {t(`summarySwitch.action.${target}`)}
          </button>
          {blockedReason ? <span className="studio-help">{blockedReason}</span> : null}
        </div>
      )}
      <SummarySwitchStatus sw={sw} />
    </div>
  )
}

/** The single line that says what the switch did: sending, sent, or why it did not go. */
export function SummarySwitchStatus({ sw }: { sw: SummarySwitch }) {
  const { t } = useI18n()
  const text = summarySwitchText(sw.value)
  const { stage, attempt, refusal, actionId } = sw.state
  const reason = (error: StudioApiError) =>
    error.code === 'invalid_decision' && error.details['reason'] === 'already_set'
      ? t(`summarySwitch.already.${sw.value}`)
      : error.message || t(`errors.${error.code}`)

  if (sw.failure) return <Line tone="warn" icon="warn" text={t('summarySwitch.status.refused', { text, reason: reason(sw.failure) })} />
  if (sw.busy && stage !== 'settled') return <Line icon="clock" text={t('summarySwitch.status.sending', { text })} />
  if (stage === 'settled' && attempt) {
    if (attempt.outcome === 'delivered' && attempt.reported) {
      return <Line tone="ok" icon="check" text={t(`summarySwitch.status.sent.${sw.value}`, { text })} />
    }
    if (attempt.outcome === 'not_delivered') {
      const why = attempt.receipt?.error ?? attempt.receipt?.code ?? ''
      return <Line tone="warn" icon="warn" text={t('summarySwitch.status.notSent', { text, reason: why, action: actionId ?? '' })} />
    }
    return <Line tone="warn" icon="warn" text={t('summarySwitch.status.uncertain', { text, action: actionId ?? '' })} />
  }
  if (stage === 'refused' && refusal) {
    return <Line tone="warn" icon="warn" text={t('summarySwitch.status.refused', { text, reason: reason(refusal) })} />
  }
  if (stage === 'stale') return <Line tone="warn" icon="warn" text={t('summarySwitch.status.stale', { text, action: actionId ?? '' })} />
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
