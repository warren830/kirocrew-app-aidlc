/**
 * The narrow-screen map: one accordion per phase, never a shrunken canvas (FR-MAP-009).
 *
 * Why accordions rather than the lanes at 100% width: a 154px stage card at 390px still fits, but 33 of
 * them stacked is 33 screens of scrolling with no way to tell which phase you are in. A closed phase is
 * one row that already carries its counts, so the whole lifecycle is visible at once and only the phase
 * you open costs height.
 *
 * The stylesheet hides the desktop `aside.inspector` below 900px, so the inspector is rendered inline
 * under the phase that holds the selection. That keeps FR-MAP-008 true on a phone without a modal sheet
 * — no focus trap, no scroll lock, and the selected card stays on screen right above its evidence.
 *
 * A phase with live work opens itself; the user's own toggling always wins after that, so a poll cannot
 * fold away the phase someone is reading.
 */

import { useMemo, useState, type ReactNode } from 'react'

import { useI18n } from '../i18n'
import { plural } from '../lib/format'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import type { MapPhase } from '../lib/types'
import { StageCard, type Density, type StageScope } from './StageCard'
import { UnitLanes } from './UnitLanes'

/** A phase worth opening unasked: something is executing, waiting or being revised in it. */
function hasLiveWork(phase: MapPhase): boolean {
  return phase.stages.some(
    (stage) => stage.is_current || stage.state === 'awaiting_approval' || stage.state === 'in_progress',
  )
}

export interface PhaseAccordionProps {
  phases: MapPhase[]
  density: Density
  showUnits: boolean
  selectedStage: string
  selectedUnit: string
  scope: Omit<StageScope, 'phaseLabel'>
  phaseLabel: (phase: string) => string
  onSelectStage: (slug: string) => void
  onSelectUnit: (slug: string, unit: string) => void
  /** The inspector element, rendered inside the phase that owns the selection. */
  inspector: ReactNode
}

export function PhaseAccordion({
  phases, density, showUnits, selectedStage, selectedUnit, scope, phaseLabel,
  onSelectStage, onSelectUnit, inspector,
}: PhaseAccordionProps) {
  const i18n = useI18n()
  const [toggled, setToggled] = useState<Record<string, boolean>>({})

  const defaults = useMemo(() => {
    const open = new Set<string>()
    for (const phase of phases) {
      if (hasLiveWork(phase) || phase.stages.some((stage) => stage.slug === selectedStage)) open.add(phase.phase)
    }
    // Nothing live and nothing selected: open the first phase so the screen is never just five rows.
    if (open.size === 0 && phases[0]) open.add(phases[0].phase)
    return open
  }, [phases, selectedStage])

  return (
    <div className="studio-accordions">
      {phases.map((phase) => {
        const label = phaseLabel(phase.phase)
        const open = toggled[phase.phase] ?? defaults.has(phase.phase)
        const regionId = `studio-phase-${phase.phase}`
        const holdsSelection = phase.stages.some((stage) => stage.slug === selectedStage)

        return (
          <section key={phase.phase} className="studio-accordion" data-phase={phase.phase} data-lane={phase.phase}>
            <h2 className="studio-accordion-h">
              <button
                type="button"
                className="studio-accordion-toggle"
                aria-expanded={open}
                aria-controls={regionId}
                onClick={() => setToggled((was) => ({ ...was, [phase.phase]: !open }))}
              >
                <Icon name={open ? 'chevronDown' : 'chevron'} size={15} />
                <span className="studio-grow">{label}</span>
                <span className="studio-mono studio-muted studio-accordion-sub">
                  {plural(i18n, 'map.lane.counts', phase.counts.total, {
                    skipped: i18n.fmt.number(phase.counts.skipped),
                  })}
                </span>
                {phase.status ? <Chip mono>{phase.status}</Chip> : null}
              </button>
            </h2>

            <div id={regionId} className="studio-accordion-body" hidden={!open}>
              <ul className="studio-stages" role="list" aria-label={i18n.t('map.a11y.lane', { phase: label })}>
                {phase.stages.map((stage) => (
                  <li key={stage.slug}>
                    <StageCard
                      stage={stage}
                      density={density}
                      selected={selectedStage === stage.slug && !selectedUnit}
                      // No edges on a narrow screen: the relationships are read from the inspector, which
                      // is right below the card, so an on-card tag would be noise.
                      relation={null}
                      scope={{ ...scope, phaseLabel: label }}
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
                  scope={{ ...scope, phaseLabel: label }}
                  onSelect={onSelectUnit}
                />
              ) : null}

              {holdsSelection ? (
                <div className="studio-map-sheet" role="complementary" aria-label={i18n.t('map.inspector.title')}>
                  {inspector}
                </div>
              ) : null}
            </div>
          </section>
        )
      })}
    </div>
  )
}
