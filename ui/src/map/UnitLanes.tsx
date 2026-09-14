/**
 * Construction's per-unit sub-lanes (FR-MAP-006).
 *
 * A per-unit stage runs once per Construction unit, but the state file keeps exactly ONE row per stage
 * and writes a literal `Per unit: [TBD]` line — there is no per-unit mark on disk. So the state on a
 * unit card is derived by the backend from two observable facts (is this the record's active unit, does
 * the unit's directory hold a file) and is presentational only. The note under the rail says so in
 * words, because a green "completed" on a unit that merely has a file would otherwise read as an
 * approval that never happened.
 *
 * Collapsed by default: expanding turns 5 per-unit stages × N units into N×5 extra cards, which on a
 * real intent is more cards than the rest of the map put together.
 */

import { useI18n } from '../i18n'
import { Icon } from '../shell/Icon'
import type { MapStage } from '../lib/types'
import { UnitCard, type StageScope } from './StageCard'

export interface UnitLanesProps {
  /** Only the per-unit stages of this lane, in graph order. */
  stages: MapStage[]
  selectedStage: string
  selectedUnit: string
  scope: StageScope
  onSelect: (slug: string, unit: string) => void
}

export function UnitLanes({ stages, selectedStage, selectedUnit, scope, onSelect }: UnitLanesProps) {
  const { t } = useI18n()
  const withUnits = stages.filter((stage) => stage.units.length > 0)
  if (withUnits.length === 0) return null

  return (
    <div className="studio-units">
      <ul className="studio-unit-list">
        {withUnits.map((stage) => (
          <li key={stage.slug} className="studio-unit-row">
            <span className="studio-unit-label studio-mono">
              <Icon name="chevron" size={11} strokeWidth={2} />
              {t('map.units.row', { number: stage.number, slug: stage.slug })}
            </span>
            <ul className="studio-stages" role="list">
              {stage.units.map((unit) => (
                <li key={unit.unit}>
                  <UnitCard
                    stage={stage}
                    unit={unit}
                    selected={selectedStage === stage.slug && selectedUnit === unit.unit}
                    scope={scope}
                    onSelect={onSelect}
                  />
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
      <p className="studio-unit-note studio-muted">{t('map.units.note')}</p>
    </div>
  )
}
