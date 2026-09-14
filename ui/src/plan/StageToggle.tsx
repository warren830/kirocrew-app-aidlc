/**
 * One stage in the plan matrix: a checkbox, its facts, and — when it cannot be changed — the reason.
 *
 * The reason is the whole point of this component. `PlanService` refuses an override it does not allow
 * and reports it as a plan issue, so a UI that offered the toggle anyway would let the user believe a
 * plan that is not the plan (FR-PLAN-004/005). Every locked stage therefore renders three things: the
 * disabled control, a short visible reason beside the slug, and the full sentence in a description the
 * checkbox points at with `aria-describedby` — because a `title` alone reaches neither a touch user nor
 * most screen readers.
 *
 * The slug, the number, the artifact names and the state are AI-DLC's own words and are never
 * translated; only Studio's framing around them is.
 */

import { useId } from 'react'

import { Icon } from '../shell/Icon'
import { useI18n, type I18n } from '../i18n'
import type { PlanStage } from '../lib/types'

/** `required_by:<slug>` is minted per dependency, so the prefix is matched rather than the whole value. */
const REQUIRED_BY = 'required_by:'

/** The two or three words shown beside a locked slug. `null` when the stage is free to change. */
export function lockShort(i18n: I18n, reason: string | null): string | null {
  if (!reason) return null
  if (reason.startsWith(REQUIRED_BY)) {
    return i18n.t('plan.lock.short.required_by', { slug: reason.slice(REQUIRED_BY.length) })
  }
  const key = `plan.lock.short.${reason}`
  return i18n.has(key) ? i18n.t(key) : i18n.t('plan.lock.short.unknown')
}

/** The full sentence a screen reader and a tooltip get. Falls back to naming the engine's own reason. */
export function lockSentence(i18n: I18n, reason: string | null): string | null {
  if (!reason) return null
  if (reason.startsWith(REQUIRED_BY)) {
    return i18n.t('plan.lock.required_by', { slug: reason.slice(REQUIRED_BY.length) })
  }
  const key = `plan.lock.${reason}`
  return i18n.has(key) ? i18n.t(key) : i18n.t('plan.lock.unknown', { reason })
}

export interface StageToggleProps {
  stage: PlanStage
  /** Omitted for a read-only matrix; a locked stage never calls it. */
  onToggle?: (slug: string, next: boolean) => void
  /** A recompute is in flight: the control stays visible but refuses input until the answer lands. */
  busy?: boolean
  /** Show the state AI-DLC recorded on disk (recompose only; a wizard plan has no rows yet). */
  showState?: boolean
}

export function StageToggle({ stage, onToggle, busy, showState }: StageToggleProps) {
  const i18n = useI18n()
  const { t } = i18n
  const describedBy = useId()
  const locked = stage.locked
  const short = lockShort(i18n, stage.lock_reason)
  const sentence = lockSentence(i18n, stage.lock_reason)

  // The facts a sighted user reads from the icons and the pill's tint, spelled out once for anyone who
  // cannot: state, then eligibility, then what the stage is for, then why it cannot change.
  const facts: string[] = [
    stage.enabled ? t('plan.stage.on') : t('plan.stage.off'),
  ]
  if (showState && stage.state) facts.push(t('plan.stage.state', { state: t(`enum.stageState.${stage.state}`) }))
  if (!stage.in_grid && !stage.enabled) facts.push(t('plan.stage.excluded'))
  if (stage.execution === 'CONDITIONAL') {
    facts.push(stage.conditional_on ? t('plan.stage.conditionalOn', { condition: stage.conditional_on }) : t('plan.stage.conditional'))
  }
  if (stage.execution === 'ALWAYS') facts.push(t('plan.stage.always'))
  if (stage.gate) facts.push(t('plan.stage.gate'))
  if (stage.per_unit) facts.push(t('plan.stage.perUnit'))
  if (stage.review_class) facts.push(t('plan.stage.review', { class: stage.review_class }))
  if (stage.reviewer) facts.push(t('plan.stage.reviewer', { reviewer: stage.reviewer }))
  facts.push(
    stage.produces.length
      ? t('plan.stage.produces', { artifacts: stage.produces.join(t('shell.format.listJoin')) })
      : t('plan.stage.producesNone'),
  )
  if (sentence) facts.push(sentence)

  return (
    <span
      className="studio-plan-ms"
      data-on={stage.enabled ? 'true' : 'false'}
      data-locked={locked ? 'true' : 'false'}
      {...(sentence ? { title: sentence } : {})}
    >
      {/* The label holds exactly the number and the slug. Everything else — the reason, the state, what
          the stage produces — sits outside it: inside, each of those sentences would become part of the
          checkbox's accessible NAME, and "needed by code-generation" on the ci-pipeline pill would make
          two different stages answer to the same name. */}
      <label className="studio-plan-ms-label">
        <input
          type="checkbox"
          checked={stage.enabled}
          disabled={locked || !onToggle || busy === true}
          aria-describedby={describedBy}
          onChange={(event) => onToggle?.(stage.slug, event.target.checked)}
        />
        <span className="studio-mono studio-plan-ms-n">{stage.number || '—'}</span>
        <span className="studio-plan-ms-slug">{stage.slug}</span>
      </label>
      {stage.gate ? <Icon name="gate" size={11} strokeWidth={2} /> : null}
      {stage.per_unit ? <Icon name="intent" size={11} strokeWidth={2} /> : null}
      {locked ? <Icon name="lock" size={11} strokeWidth={2} /> : null}
      {short ? <span className="studio-plan-ms-why">{short}</span> : null}
      <span id={describedBy} className="studio-sr">
        {facts.join(' ')}
      </span>
    </span>
  )
}
