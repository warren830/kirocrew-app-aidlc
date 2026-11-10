/**
 * Turn one of AI-DLC's per-intent ceremonies off, or back on: summary confirmation, or the learnings ritual.
 *
 * The setting is the engine's: a line under `## Scope Configuration` in the intent's state file
 * (`- **Summary Confirmation**: on (from scope feature)`, `- **Learnings**: off (set by a command)`), shown here as
 * written and never re-worded. A person changes it by typing AI-DLC's own switch with nothing beside it
 * (`/aidlc --summary-confirmation off`, `/aidlc --learnings off`); Studio sends exactly that text as the person's own
 * turn, through the same two-phase submit every decision uses: the confirmation shows the bytes, and the send happens
 * at most once and never without the click. Who writes the line differs per ceremony, and each wrapper says how
 * (`SummaryConfirmationSwitch`, `LearningsSwitch`).
 *
 * Three rules are load-bearing:
 *  - **Its own turn.** Never folded into an open question's reply: the switch typed beside other words is read as a
 *    flag of new work, not as a switch, and a checkpoint's reply is matched exactly.
 *  - **Between turns only.** A running, unbound, paused or archived intent would refuse the send, so the control
 *    says why instead of offering it.
 *  - **An open question stays open.** AI-DLC does not withdraw a question it has asked; it still records the answer
 *    to it, and no later stage asks again. The confirmation panel says so before anything is sent.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { recordedOutcome, useSubmit } from '../actions/useSubmit'
import { useI18n } from '../i18n'
import { StudioApiError, type StudioApi } from '../lib/api'
import type { ActionCard, CeremonySetting, CommandResponse, IntentSummary } from '../lib/types'
import { WIRE } from '../lib/wire.generated'
import { Icon } from '../shell/Icon'

export type Ceremony = 'summary_confirmation' | 'learnings'
export type CeremonyValue = 'on' | 'off'

interface CeremonySpec {
  /** The i18n key prefix of the control's strings. */
  keys: string
  /** The command card that carries the request, and its one decision. */
  decision: 'set_summary_confirmation' | 'set_learnings'
  text: Record<CeremonyValue, string>
  /** The route that queues the card (nothing is sent by it). */
  request(api: StudioApi, repoId: string, intentKey: string, value: CeremonyValue): Promise<CommandResponse>
}

const CEREMONIES: Record<Ceremony, CeremonySpec> = {
  summary_confirmation: {
    keys: 'summarySwitch',
    decision: 'set_summary_confirmation',
    text: { off: WIRE.SUMMARY_CONFIRMATION_OFF, on: WIRE.SUMMARY_CONFIRMATION_ON },
    request: (api, repoId, intentKey, value) => api.summaryConfirmation(repoId, intentKey, value),
  },
  learnings: {
    keys: 'learningsSwitch',
    decision: 'set_learnings',
    text: { off: WIRE.LEARNINGS_OFF, on: WIRE.LEARNINGS_ON },
    request: (api, repoId, intentKey, value) => api.learnings(repoId, intentKey, value),
  },
}

/** The text the switch sends, which the confirmation must display. */
export function ceremonySwitchText(ceremony: Ceremony, value: CeremonyValue): string {
  return CEREMONIES[ceremony].text[value]
}

/** What the control offers: back on when AI-DLC recorded it off, otherwise off. */
export function ceremonySwitchTarget(setting: CeremonySetting | null | undefined): CeremonyValue {
  return setting?.value === 'off' ? 'on' : 'off'
}

/** Why the intent's conversation would refuse the switch now, or `null`. The checks the send itself makes. */
export function ceremonySwitchBlockedReason(
  ceremony: Ceremony, intent: IntentSummary, t: (key: string) => string,
): string | null {
  if (intent.archived) return t('intents.run.disabledArchived')
  if (intent.paused) return t('intents.run.disabledPaused')
  if (!intent.session?.slot_key) return t('intents.run.disabledUnbound')
  if (intent.session.running) return t('intents.run.disabledRunning')
  if (intent.disk.status?.toLowerCase() === 'completed') return t(`${CEREMONIES[ceremony].keys}.blocked.completed`)
  return null
}

/** The same question asked of a card: its live evidence names the bound conversation and whether it is running. */
export function cardCeremonySwitchBlockedReason(card: ActionCard, t: (key: string) => string): string | null {
  const session = card.evidence.session
  if (!session?.slot_key) return t('intents.run.disabledUnbound')
  if (session.running) return t('intents.run.disabledRunning')
  return null
}

export function useCeremonySwitch(ceremony: Ceremony, api: StudioApi, onSettled?: () => void) {
  const { submit, reset, state, busy } = useSubmit({ api, ...(onSettled ? { onSettled } : {}) })
  const [value, setValue] = useState<CeremonyValue>('off')
  const [queueing, setQueueing] = useState(false)
  /** A refusal before any card existed: the route itself said no. */
  const [failure, setFailure] = useState<StudioApiError | null>(null)

  const send = useCallback(
    async (repoId: string, intentKey: string, which: CeremonyValue) => {
      const spec = CEREMONIES[ceremony]
      setValue(which)
      setFailure(null)
      reset()
      setQueueing(true)
      try {
        const queued = await spec.request(api, repoId, intentKey, which)
        setQueueing(false)
        await submit({
          card: queued.action,
          captured: queued.action.captured,
          payload: { decision: spec.decision, value: which },
          clientWireText: spec.text[which],
        })
      } catch (caught) {
        setFailure(caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0))
      } finally {
        setQueueing(false)
      }
    },
    [ceremony, api, submit, reset],
  )

  return { send, state, value, failure, busy: busy || queueing }
}

export type CeremonySwitchState = ReturnType<typeof useCeremonySwitch>

export interface CeremonySwitchProps {
  ceremony: Ceremony
  api: StudioApi
  repoId: string
  intentKey: string
  /** The intent's line as AI-DLC wrote it; `null` when the state file has none (the scope's default applies). */
  setting: CeremonySetting | null | undefined
  /** Why a send would be refused now, or `null`. */
  blockedReason: string | null
  /** Shown on an open question of the ceremony's: the confirmation says what happens to it. */
  pending?: boolean
  onSettled?: () => void
}

export function CeremonySwitch({
  ceremony, api, repoId, intentKey, setting, blockedReason, pending = false, onSettled,
}: CeremonySwitchProps) {
  const { t } = useI18n()
  const keys = CEREMONIES[ceremony].keys
  const sw = useCeremonySwitch(ceremony, api, onSettled)
  const [confirming, setConfirming] = useState(false)
  const confirmRef = useRef<HTMLButtonElement | null>(null)
  const target = ceremonySwitchTarget(setting)
  const text = ceremonySwitchText(ceremony, target)

  // A confirmation that appears out of nowhere is unusable with a keyboard, so focus follows it.
  useEffect(() => {
    if (confirming) confirmRef.current?.focus()
  }, [confirming])

  const confirm = async () => {
    setConfirming(false)
    await sw.send(repoId, intentKey, target)
  }

  return (
    <div className="studio-col studio-ceremony-switch" data-ceremony={ceremony} role="group" aria-label={t(`${keys}.title`)}>
      <p className="studio-wrap-any">
        <span className="studio-field-label">{t(`${keys}.title`)}</span>{' '}
        <span>{setting ? t(`${keys}.value.${setting.value}`) : t(`${keys}.value.unrecorded`)}</span>
        {setting ? (
          <>
            {' · '}
            {/* The engine's own words for who set it, verbatim. */}
            <span className="studio-muted">{t(`${keys}.recorded`)}</span>{' '}
            <code className="studio-mono">{setting.line}</code>
          </>
        ) : null}
      </p>
      <p className="studio-help">
        {t(target === 'off' ? `${keys}.explain.on` : pending ? `${keys}.explain.offPending` : `${keys}.explain.off`)}
      </p>
      {confirming ? (
        <div
          className="studio-confirm"
          role="group"
          aria-label={t(`${keys}.confirm.${target}.title`)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') setConfirming(false)
          }}
        >
          <h3>{t(`${keys}.confirm.${target}.title`)}</h3>
          <p>{t(`${keys}.confirm.${target}.body`)}</p>
          <pre className="studio-sendtext" aria-label={t(`${keys}.confirm.text`)}>{text}</pre>
          {pending && target === 'off' ? (
            <p className="studio-consequence">
              <Icon name="info" size={13} />
              <span>{t(`${keys}.confirm.pending`)}</span>
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
              {t(`${keys}.send`, { text })}
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
            {t(`${keys}.action.${target}`)}
          </button>
          {blockedReason ? <span className="studio-help">{blockedReason}</span> : null}
        </div>
      )}
      <CeremonySwitchStatus ceremony={ceremony} sw={sw} />
    </div>
  )
}

/** The single line that says what the switch did: sending, sent, or why it did not go. */
export function CeremonySwitchStatus({ ceremony, sw }: { ceremony: Ceremony; sw: CeremonySwitchState }) {
  const { t } = useI18n()
  const keys = CEREMONIES[ceremony].keys
  const text = ceremonySwitchText(ceremony, sw.value)
  const { stage, attempt, refusal, actionId } = sw.state
  const reason = (error: StudioApiError) =>
    error.code === 'invalid_decision' && error.details['reason'] === 'already_set'
      ? t(`${keys}.already.${sw.value}`)
      : error.message || t(`errors.${error.code}`)

  if (sw.failure) return <Line tone="warn" icon="warn" text={t(`${keys}.status.refused`, { text, reason: reason(sw.failure) })} />
  if (sw.busy && stage !== 'settled') return <Line icon="clock" text={t(`${keys}.status.sending`, { text })} />
  if (stage === 'settled' && attempt) {
    const outcome = recordedOutcome(attempt)
    if (outcome === 'delivered' && attempt.reported) {
      return <Line tone="ok" icon="check" text={t(`${keys}.status.sent.${sw.value}`, { text })} />
    }
    if (outcome === 'not_delivered') {
      const why = attempt.receipt?.error ?? attempt.receipt?.code ?? ''
      return <Line tone="warn" icon="warn" text={t(`${keys}.status.notSent`, { text, reason: why, action: actionId ?? '' })} />
    }
    return <Line tone="warn" icon="warn" text={t(`${keys}.status.uncertain`, { text, action: actionId ?? '' })} />
  }
  if (stage === 'refused' && refusal) {
    return <Line tone="warn" icon="warn" text={t(`${keys}.status.refused`, { text, reason: reason(refusal) })} />
  }
  if (stage === 'stale') return <Line tone="warn" icon="warn" text={t(`${keys}.status.stale`, { text, action: actionId ?? '' })} />
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
