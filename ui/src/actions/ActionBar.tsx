/**
 * The sticky action bar: one control per decision the server says this card may take.
 *
 * The buttons are `card.decisions`, verbatim. Nothing here decides what is possible — the backend
 * already filtered by status, by breaker state, by attempt count (`accept_as_is` appears only after
 * enough revisions) and by which checkpoint is pending. A button this file invented would be a button
 * that always fails.
 *
 * Two safety properties:
 *
 *  - **Approve is never the default.** The primary control is last in the DOM (visual spec §5.8) and
 *    nothing here autofocuses; pressing a decision only OPENS the confirmation, which is a separate
 *    deliberate action (FR-GATE-001). There is no form, so Enter cannot submit anything.
 *  - **In flight means locked.** While a submit is running for this action every control is disabled,
 *    including the one that was pressed, and after it settles the bar shows what happened instead of
 *    re-offering the same decision. The item clears when AI-DLC's own state moves, not when the HTTP
 *    call returned (FR-ACT-007/FR-GATE-007), and the hint says exactly that.
 */

import { Btn } from '@kirocrew/app-sdk/ui'

import { Icon, type IconName } from '../shell/Icon'
import { useI18n } from '../i18n'
import { isClosedAction, isRepositoryUnavailable } from '../lib/actionQueue'
import type { ActionCard, Decision, DecisionSpec } from '../lib/types'
import { CONFIRM_PANEL_ID, type PendingDecision } from './ConfirmPanel'
import type { SubmitState } from './useSubmit'

/** Decisions that undo, refuse or stop something. Rendered in the host's danger treatment. */
const DANGEROUS: readonly Decision[] = ['request_changes', 'request_plan_changes', 'mark_not_delivered', 'force_stop']

const DECISION_ICON: Partial<Record<Decision, IconName>> = {
  approve: 'check',
  approve_plan: 'check',
  accept_as_is: 'check',
  confirm_summary: 'check',
  answers: 'send',
  provide_input: 'send',
  run: 'play',
  run_now: 'play',
  resume: 'play',
  retry_now: 'refresh',
  keep_paused: 'pause',
  force_stop: 'pause',
  prepare_commit: 'git',
  reconcile: 'refresh',
  resubmit: 'send',
  rebind_session: 'link',
  acknowledge: 'check',
  mark_not_delivered: 'warn',
  pick_intent: 'intent',
  request_changes: 'doc',
  request_plan_changes: 'doc',
}

export interface ActionBarProps {
  card: ActionCard
  submit: SubmitState
  busy: boolean
  refreshing: boolean
  pending: PendingDecision | null
  onChoose: (spec: DecisionSpec) => void
  onBackToQueue: () => void
  /** Why this decision cannot be sent yet (an i18n key), or `null` when it can. */
  blockedFor: (spec: DecisionSpec) => string | null
}

export function ActionBar({
  card, submit, busy, refreshing, pending, onChoose, onBackToQueue, blockedFor,
}: ActionBarProps) {
  const { t } = useI18n()
  const unavailable = isRepositoryUnavailable(card)
  const decisions = unavailable ? card.decisions.filter((spec) => spec.lane !== 'human_lane') : card.decisions
  const closed = isClosedAction(card)
  const primary = card.primary?.decision ?? null
  // Primary last: the visual spec puts it at the trailing edge, and a keyboard user reaches the
  // reversible options before the irreversible one.
  const ordered = [...decisions.filter((spec) => spec.decision !== primary), ...decisions.filter((spec) => spec.decision === primary)]

  const settled = submit.stage === 'settled' && submit.actionId === card.action_id

  if (settled && !closed) {
    return (
      <div className="studio-actionbar">
        <span className="studio-hint studio-muted">
          <Icon name="check" size={13} />
          {t('detail.bar.submitted', { id: card.action_id })}
        </span>
        <Btn type="button" onClick={onBackToQueue}>
          <Icon name="inbox" size={14} />
          {t('detail.bar.showQueue')}
        </Btn>
      </div>
    )
  }

  return (
    <div className="studio-actionbar">
      <span className="studio-hint studio-muted">
        <Icon name={refreshing ? 'refresh' : 'info'} size={13} />
        {unavailable
          ? t('detail.repoUnavailable.footer')
          : closed
          ? t('detail.bar.closed')
          : refreshing
          ? t('detail.bar.refreshing')
          : decisions.length === 0
            ? t('detail.bar.noDecisions')
            : t('detail.bar.hint')}
      </span>

      {ordered.map((spec) => {
        const blocked = blockedFor(spec)
        const open = pending?.spec.decision === spec.decision
        return (
          <Btn
            key={spec.decision}
            type="button"
            primary={spec.decision === primary}
            danger={DANGEROUS.includes(spec.decision)}
            disabled={busy || (refreshing && spec.lane !== 'studio_only') || (blocked !== null && !open)}
            aria-expanded={open}
            aria-controls={open ? CONFIRM_PANEL_ID : undefined}
            // The blocked reason is the accessible description, so a disabled control still explains
            // itself instead of just being grey.
            title={blocked ? t(blocked) : undefined}
            onClick={() => onChoose(spec)}
          >
            {DECISION_ICON[spec.decision] ? <Icon name={DECISION_ICON[spec.decision]!} size={14} /> : null}
            {t(spec.label_key)}
          </Btn>
        )
      })}
    </div>
  )
}
