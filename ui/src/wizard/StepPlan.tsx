/**
 * Step 3 — Plan: the five-phase matrix, its exact counts, and the engine's refusals.
 *
 * Every toggle round-trips to `plan/preview`, and the counts under the matrix are the server's
 * `ExactCounts` for the plan as it now stands (FR-PLAN-006). Recomputing them in the browser would be
 * faster and would eventually disagree with the plan that gets created — the artifact count multiplies
 * per-unit stages by a unit count only the engine's DAG knows, and the review intensity is the minimum of
 * three caps.
 *
 * While a recompute is in flight the previous plan stays on screen with a stale note rather than blanking:
 * a matrix that empties on every click is unusable, and an empty matrix reads as "no stages".
 */

import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { useI18n } from '../i18n'
import type { EffectivePlan } from '../lib/types'
import { PlanDiff } from '../plan/PlanDiff'
import { PlanMatrix } from '../plan/PlanMatrix'

export interface StepPlanProps {
  plan: EffectivePlan | null
  busy: boolean
  /** Overrides that came from an applied Advisor proposal; the diff labels those rows (FR-NEW-006). */
  origins?: Readonly<Record<string, 'advisor'>>
  onToggle: (slug: string, next: boolean) => void
}

export function StepPlan({ plan, busy, origins, onToggle }: StepPlanProps) {
  const i18n = useI18n()
  const { t } = i18n

  return (
    <div>
      <p className="studio-consequence">
        <Icon name="info" size={13} />
        <span>{t('wizard.plan.lead')}</span>
      </p>

      {plan ? (
        <>
          <div className="studio-row studio-wrap studio-plan-summary">
            <Chip icon="check" tone={plan.valid ? 'accent' : 'warn'}>
              {t('wizard.plan.summary', {
                stages: i18n.fmt.number(plan.exact.stages),
                gates: i18n.fmt.number(plan.exact.gates),
                artifacts: i18n.fmt.number(plan.exact.artifacts),
              })}
            </Chip>
            {busy ? (
              <span className="studio-muted" role="status">
                {t('plan.busy')}
              </span>
            ) : null}
          </div>

          <PlanMatrix stages={plan.stages} onToggle={onToggle} busy={busy} />
          <PlanDiff plan={plan} origins={origins} />
        </>
      ) : (
        <p className="studio-muted">{busy ? t('wizard.preview.busy') : t('wizard.preview.none')}</p>
      )}
    </div>
  )
}
