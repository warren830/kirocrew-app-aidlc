/**
 * The confirmation (FR-GATE-001/002) — the last thing between a person and someone else's audit trail.
 *
 * Rules this panel exists to enforce, each one a way the product could otherwise lie:
 *
 *  - **It shows the exact bytes.** The localized button label AND the canonical wire text, the second
 *    one in a `<pre>` so leading spaces, the capital C in `Request Changes: ` and a trailing newline are
 *    all visible. The same string is sent as `client_wire_text`, and the server refuses anything else.
 *  - **It never derives wire text by translating a label.** The text comes from `wireTextFor`, which
 *    mirrors the backend over the pinned constants in `wire.generated.ts`.
 *  - **It never pre-selects Approve.** Focus lands on the panel's heading, not on the send control, so
 *    a stray Enter cannot approve anything. There is no default button and no form submit.
 *  - **It refuses rather than guesses.** No wire text (blank feedback, an unanswered question, grouped
 *    answers while the capability is off), evidence that is refreshing, or a send already in flight:
 *    the control is disabled and says which one it is.
 *  - **Studio-only decisions get the same ceremony.** `resubmit` and `mark_not_delivered` send nothing
 *    to the conversation but they do change what Studio believes happened, so they require an explicit
 *    acknowledgement that the delivery evidence above was read.
 *
 * After a send, the panel shows the wire text the SERVER recorded. If that differs from what was
 * displayed — which the byte comparison should make impossible — the difference is surfaced
 * assertively rather than smoothed over.
 */

import { useEffect, useRef } from 'react'
import { Btn } from '@kirocrew/app-sdk/ui'

import { Icon } from '../shell/Icon'
import { useI18n } from '../i18n'
import type { ActionCard, DecisionSpec, ResolvePayload, SubmitPayload } from '../lib/types'
import { beforeSend, type SubmitState } from './useSubmit'

/** The panel's DOM id, so the control that opened it can point at it with `aria-controls`. */
export const CONFIRM_PANEL_ID = 'studio-confirm-panel'

/** A decision the user has chosen but not yet confirmed. */
export interface PendingDecision {
  spec: DecisionSpec
  /** Set for human-lane and host-control decisions; `null` for studio-only ones. */
  payload: SubmitPayload | null
  /** Set for studio-only decisions. */
  resolvePayload: ResolvePayload | null
  /** Exactly what will be sent, or `null` when it cannot be produced yet. */
  wire: string | null
  /** i18n key explaining why `wire` is null, or why this cannot be sent. */
  blockedKey: string | null
}

export interface ConfirmPanelProps {
  card: ActionCard
  pending: PendingDecision
  submit: SubmitState
  busy: boolean
  /** True while the card's evidence is being re-read: nothing may be submitted against it. */
  refreshing: boolean
  /** Whether the user has ticked the acknowledgement, when one is required. */
  acknowledged: boolean
  onAcknowledge: (value: boolean) => void
  onSend: () => void
  onCancel: () => void
  onOpenBlockingAction?: (actionId: string) => void
}

export function ConfirmPanel({
  card, pending, submit, busy, refreshing, acknowledged, onAcknowledge, onSend, onCancel, onOpenBlockingAction,
}: ConfirmPanelProps) {
  const { t } = useI18n()
  const heading = useRef<HTMLParagraphElement | null>(null)
  const decision = pending.spec.decision
  const label = t(`decision.${decision}.label`)
  // Only the two decisions that change what Studio believes about a possibly-delivered message.
  // `requires: ['confirm']` on `confirm_summary` means "needs a confirmation step", which is this panel
  // — a second checkbox there would be friction that teaches people to click past it.
  const needsAck = decision === 'resubmit' || decision === 'mark_not_delivered'
  const sendsWire = pending.payload !== null
  // Three lanes, three honest sentences. `host_control` really does call KiroCrew — saying "nothing is
  // sent" there (the studio-only wording) would be false about a control that stops someone's turn.
  const lane = pending.spec.lane
  const owner = submit.refusal?.code === 'repo_busy' ? submit.refusal.details['owner'] : null
  const ownerAction = owner && typeof owner === 'object' && 'action_id' in owner &&
    typeof owner.action_id === 'string' ? owner.action_id : null

  // Focus the panel's heading, not its buttons. A confirmation whose primary action already has focus
  // can be completed by the keystroke that opened it.
  useEffect(() => {
    heading.current?.focus()
  }, [decision])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // Only while nothing has been handed to the host. Once a send is in flight there is nothing to
      // cancel, and letting Escape dismiss the panel would hide the outcome.
      if (event.key === 'Escape' && beforeSend(submit.stage) && !busy) {
        event.stopPropagation()
        onCancel()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onCancel, submit.stage])

  const blocked =
    refreshing ? 'confirm.blocked.refreshing'
      : pending.blockedKey ? pending.blockedKey
        : sendsWire && pending.wire === null ? 'confirm.blocked.noWireText'
          : needsAck && !acknowledged ? 'confirm.blocked.acknowledge'
            : null

  const settled = submit.stage === 'settled' && submit.actionId === card.action_id
  const serverWire = submit.receipt?.wire_text ?? null
  const mismatch = settled && sendsWire && serverWire !== null && serverWire !== pending.wire

  return (
    <section className="studio-confirm" id={CONFIRM_PANEL_ID} role="group" aria-label={t('confirm.label')}>
      <p className="studio-ch" ref={heading} tabIndex={-1}>
        <Icon name="send" size={14} />
        {t(`confirm.title.${decision}`)}
      </p>

      {sendsWire ? (
        <>
          <p className="studio-confirm-label">
            {t('confirm.labelSends', { label, wire: pending.wire ?? t('confirm.noWireText') })}
          </p>
          {pending.wire === null ? null : (
            // `<pre>`, not a paragraph: every byte matters and collapsed whitespace would hide a
            // difference the conductor will not forgive.
            <pre className="studio-sendtext studio-wrap-any">{pending.wire}</pre>
          )}
        </>
      ) : (
        <p className="studio-confirm-label">
          {t(lane === 'host_control' ? 'confirm.hostControl' : 'confirm.studioOnly', { label })}
        </p>
      )}

      <p className="studio-route studio-muted">
        <Icon name="info" size={13} />
        {sendsWire
          ? t('confirm.routing', {
              session: card.evidence.session?.session_key ?? t('common.unavailable'),
              repo: card.repo.label || card.repo.repo_id,
              intent: card.intent.slug || card.intent.intent_dir,
            })
          : t(`confirm.consequence.${decision}`)}
      </p>

      {blocked ? (
        <p className="studio-route" data-tone="danger" role="status">
          <Icon name="warn" size={13} />
          {t(blocked)}
        </p>
      ) : null}
      {ownerAction && onOpenBlockingAction ? (
        <Btn type="button" onClick={() => onOpenBlockingAction(ownerAction)}>
          {t('detail.repoBusy.openOwner')}
        </Btn>
      ) : null}

      {needsAck ? (
        <label className="studio-ack">
          <input
            type="checkbox"
            checked={acknowledged}
            disabled={!beforeSend(submit.stage) || busy}
            onChange={(event) => onAcknowledge(event.currentTarget.checked)}
          />
          <span>{t(`confirm.acknowledge.${decision}`)}</span>
        </label>
      ) : null}

      {mismatch ? (
        // Assertive: what was sent is not what this browser displayed. The user must see the server's
        // record, not ours.
        <div className="studio-banner" data-tone="danger" role="alert">
          <Icon name="warn" size={15} />
          <div className="studio-grow">
            <p>{t('confirm.mismatch')}</p>
            <pre className="studio-sendtext studio-wrap-any">{serverWire}</pre>
          </div>
        </div>
      ) : null}

      <div className="studio-confirm-actions">
        <Btn
          primary
          type="button"
          // Disabled while in flight, and never re-enabled by this panel after a settle: the send is
          // at-most-once, so the only path back is a fresh decision.
          disabled={blocked !== null || busy || !beforeSend(submit.stage)}
          onClick={onSend}
        >
          <Icon name="check" size={14} />
          {sendsWire ? t('confirm.send') : t('confirm.apply')}
        </Btn>
        {/* Disabled while anything is open: mutations are deliberately not cancellable (a submit that
            returned has already committed a `Delivering` record), so a Cancel here would only hide it. */}
        <Btn type="button" disabled={busy} onClick={onCancel}>
          {t('common.cancel')}
        </Btn>
        <span className="studio-hint studio-muted">
          {sendsWire
            ? t('confirm.atMostOnce')
            : t(lane === 'host_control' ? 'confirm.hostControlHint' : 'confirm.studioOnlyHint')}
        </span>
      </div>
    </section>
  )
}
