/**
 * Where a decision is, between "you pressed the button" and "AI-DLC's files moved".
 *
 * Four fixed steps — Queued, Sent, Agent processing, State changed — because that is the real lifecycle
 * and hiding any of it would let a user believe an HTTP 200 was an approval. Success is observed state
 * movement, never a receipt (FR-GATE-007), so the strip says so in words whenever a send has happened.
 *
 * The uncertain case is the reason this component exists. `DeliveryUncertain` /
 * `ReconciliationRequired` can outlive delivery uncertainty: a transcript may prove receipt while the
 * workflow still needs reconciliation. Without that proof the strip:
 *   - announces it assertively (PRD §10.3 reserves assertive for exactly this),
 *   - states plainly that Studio is watching the disk and will not send again, and
 *   - prints the four facts the backend's own proof depends on, verbatim, so the user can decide
 *     rather than trust a summary (§3.6: the UI shows `transcript_row`, `disk_baseline_unchanged` and
 *     `boot_id_unchanged` before any confirm control).
 *
 * The wire text is rendered from `card.delivery.wire_text` — the value the SERVER recorded, not the
 * string this browser composed. If the two ever differed, what is on screen is what was sent.
 */

import { Chip, type ChipTone } from '../shell/Chip'
import { Icon, type IconName } from '../shell/Icon'
import { useI18n, type I18n } from '../i18n'
import { at } from '../lib/format'
import type { ActionCard, ActionStatus } from '../lib/types'

type StepState = 'done' | 'now' | 'future' | 'failed' | 'unchanged' | 'cancelled'

const STEP_KEYS = ['delivery.step.queued', 'delivery.step.delivered', 'delivery.step.processing', 'delivery.step.stateChanged'] as const

/** Statuses where nothing has been handed to the host yet. */
const BEFORE_SEND: readonly ActionStatus[] = ['Draft', 'Queued', 'Cancelled']
/** Statuses where Studio cannot prove what happened. */
export const UNCERTAIN_STATUSES: readonly ActionStatus[] = ['DeliveryUncertain', 'ReconciliationRequired']

/**
 * Which step is current, and whether it failed there.
 *
 * `Delivered` sits on "Agent processing" even when the host queued the message, because the host has
 * accepted it; a `queued_at` adds its own chip rather than moving the marker backwards, which would
 * read as the send being undone.
 */
export function stepIndex(status: ActionStatus): { index: number; failed: boolean } {
  switch (status) {
    case 'Draft':
    case 'Queued':
    case 'Cancelled':
      return { index: 0, failed: false }
    case 'Delivering':
      return { index: 1, failed: false }
    case 'NotDelivered':
      return { index: 1, failed: true }
    case 'DeliveryUncertain':
    case 'ReconciliationRequired':
      return { index: 1, failed: true }
    case 'Delivered':
    case 'Processing':
      return { index: 2, failed: false }
    case 'Failed':
      return { index: 3, failed: true }
    case 'ResolvedNoTransition':
      return { index: 3, failed: false }
    case 'StateChanged':
      return { index: 4, failed: false }
  }
}

function stateOf(position: number, current: number, failed: boolean): StepState {
  if (position < current) return 'done'
  if (position > current) return 'future'
  return failed ? 'failed' : 'now'
}

const STEP_ICON: Record<StepState, IconName> = { done: 'check', now: 'clock', future: 'clock', failed: 'warn', unchanged: 'info', cancelled: 'close' }

/** Chip tone for a status, so the queue and the strip agree about what a status looks like. */
export function statusTone(status: ActionStatus): ChipTone {
  if (status === 'StateChanged') return 'ok'
  if (status === 'NotDelivered' || status === 'Failed' || UNCERTAIN_STATUSES.includes(status)) return 'danger'
  if (status === 'Delivering' || status === 'Delivered' || status === 'Processing') return 'accent'
  return 'neutral'
}

export function statusLabel(i18n: I18n, status: ActionStatus, reason?: string | null): string {
  if (status === 'ResolvedNoTransition' && reason === 'answer_not_verified_at_gate') {
    return i18n.t('delivery.answerNotVerified')
  }
  return i18n.t(status === 'ResolvedNoTransition' && reason === 'answer_requires_text'
    ? 'delivery.answerNeedsText' : `enum.actionStatus.${status}`)
}

/** A tri-state fact, printed as the words yes / no / not observable — never as a blank. */
function triState(i18n: I18n, value: boolean | null): string {
  if (value === null) return i18n.t('common.unavailable')
  return i18n.t(value ? 'delivery.fact.yes' : 'delivery.fact.no')
}

export interface DeliveryStripProps {
  card: ActionCard
}

export function DeliveryStrip({ card }: DeliveryStripProps) {
  const i18n = useI18n()
  const { t } = i18n
  const { status } = card
  const delivery = card.delivery
  const needsReconciliation = UNCERTAIN_STATUSES.includes(status)
  const confirmedPending = needsReconciliation && delivery.delivery_confirmed === true
  // Receipt completes only the send step. Reconciliation still has to establish what AI-DLC did.
  const { index, failed } = confirmedPending ? { index: 2, failed: false } : stepIndex(status)
  const noTransition = status === 'ResolvedNoTransition'
  const answerNotVerified = noTransition && card.resolution.reason === 'answer_not_verified_at_gate'
  const sent = !BEFORE_SEND.includes(status)

  return (
    <section className="studio-delivery" aria-label={t('delivery.label')}>
      <ol className="studio-dsteps">
        {STEP_KEYS.map((key, position) => {
          const state = noTransition && position === 3
            ? 'unchanged'
            : status === 'Cancelled' && position === 0
              ? 'cancelled'
              : stateOf(position, index, failed)
          return (
            <li key={key} className="studio-dstep" data-state={state}>
              <span className="studio-dbullet" aria-hidden="true">
                <Icon name={STEP_ICON[state]} size={10} strokeWidth={2.4} />
              </span>
              <span className="studio-dstep-label">{t(
                state === 'unchanged' ? (answerNotVerified ? 'delivery.newGateReview' : 'delivery.step.unchanged')
                  : state === 'cancelled' ? 'enum.actionStatus.Cancelled'
                  : confirmedPending && position === 2 ? 'delivery.step.reconciliation' : key,
              )}</span>
              {/* The step's own state in words: the bullet's colour and glyph carry it visually, and a
                  screen reader gets it here instead of "list item, Sent". */}
              <span className="studio-sr">{t(`delivery.state.${state}`)}</span>
            </li>
          )
        })}
      </ol>

      <div className="studio-drow">
        <Chip tone={statusTone(status)} icon={needsReconciliation ? 'warn' : undefined}>
          {statusLabel(i18n, status, card.resolution.reason)}
        </Chip>
        {delivery.queued_at ? <Chip icon="clock">{t('delivery.queuedInSlot')}</Chip> : null}
        {noTransition ? <Chip icon="info">{t(answerNotVerified ? 'delivery.newGateReview' : 'delivery.noTransition')}</Chip> : null}
        {sent && !needsReconciliation ? (
          <Chip tone={noTransition ? 'neutral' : 'ok'} icon={noTransition ? 'info' : 'check'}>
            {t('delivery.watchingDisk')}
          </Chip>
        ) : null}
        {delivery.delivered_at ? (
          <span className="studio-muted studio-mono studio-dat">{t('delivery.sentAt', { at: at(i18n, delivery.delivered_at) })}</span>
        ) : null}
      </div>

      {answerNotVerified ? (
        <div className="studio-banner studio-dwarn" data-tone="warn" role="alert">
          <Icon name="warn" size={15} />
          <p>{t('delivery.answerNotVerifiedBody')}</p>
        </div>
      ) : null}

      {needsReconciliation ? (
        // Receipt and workflow acceptance are separate facts; neither warning offers to resend.
        <div className="studio-banner studio-dwarn" data-tone="danger" role="alert">
          <Icon name="warn" size={15} />
          <div className="studio-grow">
            <p className="studio-dwarn-title">{t(confirmedPending ? 'delivery.confirmed' : 'delivery.uncertain')}</p>
            <p className="studio-muted">{t(confirmedPending ? 'delivery.confirmedBody' : 'delivery.uncertainBody')}</p>
          </div>
        </div>
      ) : null}

      {needsReconciliation ? (
        <dl className="studio-dproof">
          <div>
            <dt>{t('delivery.fact.transcriptRow')}</dt>
            <dd className="studio-mono studio-wrap-any">
              {delivery.transcript_row
                ? `${delivery.transcript_row.slot_key} · ${delivery.transcript_row.ts} · ${delivery.transcript_row.role}`
                : t('delivery.fact.absent')}
            </dd>
          </div>
          <div>
            <dt>{t('delivery.fact.slotRanSince')}</dt>
            <dd className="studio-mono">{triState(i18n, delivery.slot_ran_since)}</dd>
          </div>
          <div>
            <dt>{t('delivery.fact.diskUnchanged')}</dt>
            <dd className="studio-mono">{triState(i18n, delivery.disk_baseline_unchanged)}</dd>
          </div>
          <div>
            <dt>{t('delivery.fact.bootUnchanged')}</dt>
            <dd className="studio-mono">{triState(i18n, delivery.boot_id_unchanged)}</dd>
          </div>
          <div>
            <dt>{t('delivery.fact.confirmed')}</dt>
            <dd className="studio-mono">{triState(i18n, delivery.delivery_confirmed)}</dd>
          </div>
        </dl>
      ) : null}

      {sent && delivery.wire_text ? (
        <div className="studio-dsent">
          <p className="studio-dsent-head">
            <Icon name="send" size={13} /> {t('delivery.sentText')}
          </p>
          {/* `<pre>` with the server's recorded bytes. Never re-derived from the payload: the point is
              to show what the conversation received, including whitespace. */}
          <pre className="studio-sendtext studio-wrap-any">{delivery.wire_text}</pre>
        </div>
      ) : null}
    </section>
  )
}
