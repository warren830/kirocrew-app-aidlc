/**
 * One phase swimlane: a sticky header and the phase's stage cards in graph order (FR-MAP-001).
 *
 * The lane is a `<ul>` of cards inside a labelled `<li>`, not a bare row of buttons, so the 2-D canvas
 * is also a linear list for a screen reader — the semantic alternative PRD §10.3 requires for
 * swimlanes. The visual grouping (phase accent bar, count sub-line) is decoration on top of that list.
 *
 * MapPage supplies the current plan or the explicitly requested full graph. Both retain the engine's
 * original stage numbers and order; filtering never renumbers a stage.
 */

import { useI18n } from '../i18n'
import { plural } from '../lib/format'
import { Chip } from '../shell/Chip'
import type { MapPhase } from '../lib/types'
import { StageCard, type Density, type Relation, type StageScope } from './StageCard'
import { UnitLanes } from './UnitLanes'

export interface PhaseLaneProps {
  phase: MapPhase
  phaseLabel: string
  density: Density
  showUnits: boolean
  selectedStage: string
  selectedUnit: string
  /** slug → how it relates to the selected stage. Absent = unrelated. */
  relations: Map<string, Exclude<Relation, null>>
  scope: Omit<StageScope, 'phaseLabel'>
  onSelectStage: (slug: string) => void
  onSelectUnit: (slug: string, unit: string) => void
}

export function PhaseLane({
  phase,
  phaseLabel,
  density,
  showUnits,
  selectedStage,
  selectedUnit,
  relations,
  scope,
  onSelectStage,
  onSelectUnit,
}: PhaseLaneProps) {
  const i18n = useI18n()
  const { t } = i18n
  const cardScope: StageScope = { ...scope, phaseLabel }

  return (
    <li className="studio-lane" data-lane={phase.phase} data-phase={phase.phase}>
      <div className="studio-lane-h">
        <span className="studio-lane-name">{phaseLabel}</span>
        <span className="studio-lane-sub studio-mono">
          {plural(i18n, 'map.lane.counts', phase.counts.total, {
            skipped: i18n.fmt.number(phase.counts.skipped),
          })}
        </span>
        {/* The record's own word for the phase, verbatim: it is AI-DLC's vocabulary, not Studio's. */}
        {phase.status ? (
          <span className="studio-lane-status">
            <Chip mono title={t('map.phase.statusTitle', { status: phase.status })}>{phase.status}</Chip>
          </span>
        ) : null}
      </div>

      <div className="studio-lane-body">
        <ul className="studio-stages" role="list" aria-label={t('map.a11y.lane', { phase: phaseLabel })}>
          {phase.stages.map((stage) => (
            <li key={stage.slug}>
              <StageCard
                stage={stage}
                density={density}
                selected={selectedStage === stage.slug && !selectedUnit}
                relation={relations.get(stage.slug) ?? null}
                scope={cardScope}
                onSelect={onSelectStage}
              />
            </li>
          ))}
        </ul>
        {showUnits ? (
          <UnitLanes
            stages={phase.stages.filter((stage) => stage.per_unit)}
            selectedStage={selectedStage}
            selectedUnit={selectedUnit}
            scope={cardScope}
            onSelect={onSelectUnit}
          />
        ) : null}
      </div>
    </li>
  )
}
