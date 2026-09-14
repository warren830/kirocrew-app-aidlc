/**
 * The five-phase stage matrix: every stage AI-DLC knows about, in phase order.
 *
 * "Every stage" is a requirement, not a default (FR-PLAN-003). A matrix that hid the stages the scope
 * excludes would make the method look smaller than it is and would hide the one control that lets a user
 * add a stage back; a matrix that hid conditional stages would make a plan that later grows look like a
 * plan that changed by itself. So excluded stages render unchecked, conditional stages say what they are
 * conditional on, and stages the engine freezes render disabled with their reason (`StageToggle`).
 *
 * Phases come from the generated `PHASES` list so the order is the engine's, not this file's. A stage
 * whose phase is empty — a row in a state file that the installed graph no longer contains — is grouped
 * last under its own heading instead of being dropped: it still runs, so it must still be visible.
 */

import { useMemo } from 'react'

import { Chip } from '../shell/Chip'
import { useI18n } from '../i18n'
import { PHASES, type Phase } from '../lib/enums.generated'
import { plural } from '../lib/format'
import type { PlanStage } from '../lib/types'
import { StageToggle } from './StageToggle'

export interface PhaseGroup {
  /** A canonical phase, or `''` for stages the installed graph does not place. */
  phase: Phase | ''
  stages: PlanStage[]
}

/** Group in engine phase order, keeping unplaced stages in a trailing group. */
export function groupByPhase(stages: PlanStage[]): PhaseGroup[] {
  const groups: PhaseGroup[] = PHASES.map((phase) => ({ phase, stages: [] as PlanStage[] }))
  const other: PlanStage[] = []
  for (const stage of stages) {
    const group = groups.find((candidate) => candidate.phase === stage.phase)
    if (group) group.stages.push(stage)
    else other.push(stage)
  }
  const present = groups.filter((group) => group.stages.length > 0)
  return other.length ? [...present, { phase: '', stages: other }] : present
}

export interface PlanMatrixProps {
  stages: PlanStage[]
  onToggle?: (slug: string, next: boolean) => void
  busy?: boolean
  showState?: boolean
}

export function PlanMatrix({ stages, onToggle, busy, showState }: PlanMatrixProps) {
  const i18n = useI18n()
  const { t } = i18n
  const groups = useMemo(() => groupByPhase(stages), [stages])

  return (
    <div className="studio-plan-matrix" role="group" aria-label={t('plan.matrix.a11y')}>
      {groups.map((group) => {
        const on = group.stages.filter((stage) => stage.enabled).length
        const gates = group.stages.filter((stage) => stage.enabled && stage.gate).length
        return (
          <fieldset className="studio-plan-phase" data-phase={group.phase} key={group.phase || 'other'}>
            <legend className="studio-plan-phase-h">
              <span>{group.phase ? t(`enum.phase.${group.phase}`) : t('plan.phase.other')}</span>
              <Chip>{t('plan.phase.on', { on: i18n.fmt.number(on), total: i18n.fmt.number(group.stages.length) })}</Chip>
              {gates > 0 ? <Chip icon="gate">{plural(i18n, 'plan.phase.gates', gates)}</Chip> : null}
            </legend>
            <div className="studio-plan-stages">
              {group.stages.map((stage) => (
                <StageToggle
                  key={stage.slug}
                  stage={stage}
                  {...(onToggle ? { onToggle } : {})}
                  {...(busy === undefined ? {} : { busy })}
                  {...(showState === undefined ? {} : { showState })}
                />
              ))}
            </div>
          </fieldset>
        )
      })}
    </div>
  )
}
