/**
 * One stage of the graph, as a card — and the vocabulary every other map surface reuses.
 *
 * The card is the only place where a stage's state becomes visible, so the rules that keep it honest
 * live here and are exported rather than copied:
 *
 *  - **A state is a colour AND an icon AND a word.** `stageBadge` always returns all three, so the
 *    swimlanes, the accordions and the table say the same thing and none of them relies on hue
 *    (PRD §10.1/§10.3). `skipped` additionally gets a dashed border and `excluded` its own word, so a
 *    stage the plan never selected is not mistaken for one the engine has not reached yet.
 *  - **Density removes paint, never meaning.** `overview` drops the agent/review/elapsed chips, but
 *    `stageFacts` still composes them into the accessible name, so a screen-reader user in Overview
 *    hears exactly what a sighted user sees in Detailed.
 *  - **The card decides nothing.** It is a selection control (`aria-pressed`), never a dispatch: the
 *    only operation the map offers lives in the inspector and it navigates to the Action Center, where
 *    the card is re-read and confirmed. Nothing here can send anything to a session.
 */

import { useI18n, type I18n } from '../i18n'
import { Chip, type ChipTone } from '../shell/Chip'
import { Icon, type IconName } from '../shell/Icon'
import { duration, plural } from '../lib/format'
import type { MapStage, MapUnit, StageState } from '../lib/types'

/** The three density modes of FR-MAP-007. `dependencies` = `detailed` plus the drawn edges. */
export type Density = 'overview' | 'detailed' | 'dependencies'

/** How a card relates to the current selection, for the overlay tag (visual spec §3.6). */
export type Relation = 'upstream' | 'downstream' | null

export interface StageBadge {
  tone: ChipTone
  icon: IconName
  label: string
}

type Disposition = StageState | 'excluded'

const STATE_PAINT: Record<Disposition, { tone: ChipTone; icon: IconName }> = {
  completed: { tone: 'ok', icon: 'check' },
  in_progress: { tone: 'accent', icon: 'play' },
  awaiting_approval: { tone: 'warn', icon: 'gate' },
  revising: { tone: 'aim', icon: 'refresh' },
  not_started: { tone: 'neutral', icon: 'clock' },
  skipped: { tone: 'neutral', icon: 'warn' },
  unknown: { tone: 'neutral', icon: 'warn' },
  excluded: { tone: 'neutral', icon: 'warn' },
}

/** The state badge: `excluded` is Studio's word for "the plan did not select this stage". */
export function stageBadge(i18n: I18n, state: Disposition): StageBadge {
  // The `??` is not dead: `state` is a wire string, so a stage state added to the engine before this
  // bundle knows about it must degrade to "unknown" rather than render an undefined tone.
  const paint = STATE_PAINT[state] ?? STATE_PAINT.unknown
  const label = state === 'excluded' ? i18n.t('map.state.excluded') : i18n.t(`enum.stageState.${state}`)
  return { ...paint, label }
}

/**
 * Everything the card knows, as translated fragments.
 *
 * Used verbatim for the accessible name and, filtered by density, for the visible chips — one list so
 * the two can never disagree.
 */
export function stageFacts(i18n: I18n, stage: MapStage): string[] {
  const facts: string[] = []
  if (stage.is_current) facts.push(i18n.t('map.chip.current'))
  if (stage.gate) facts.push(i18n.t('map.chip.gate'))
  if (stage.execution === 'CONDITIONAL') facts.push(i18n.t('map.chip.conditional'))
  if (stage.is_directive) facts.push(i18n.t('map.chip.directive'))
  if (stage.agent) facts.push(i18n.t('map.a11y.agent', { agent: stage.agent }))
  facts.push(
    stage.review_class
      ? i18n.t('map.chip.review', { class: stage.review_class })
      : i18n.t('map.chip.noReview'),
  )
  if (stage.elapsed_secs !== null) {
    facts.push(i18n.t('map.a11y.elapsed', { duration: duration(i18n, stage.elapsed_secs) }))
  }
  facts.push(artifactCountLabel(i18n, stage.artifacts.length))
  if (stage.skipped_reason) facts.push(i18n.t('map.a11y.reason', { reason: stage.skipped_reason }))
  return facts
}

export function artifactCountLabel(i18n: I18n, n: number): string {
  return plural(i18n, 'map.chip.artifacts', n)
}

export interface StageScope {
  repoLabel: string
  intentLabel: string
  phaseLabel: string
}

/**
 * The screen-reader name: type of thing, repo, intent, stage, state, then every fact (PRD §10.3).
 *
 * Whitespace is collapsed at the end because `number` is empty for a row the installed graph does not
 * know about — the union the projection deliberately keeps so `stage_graph_drift` stays visible.
 */
export function stageAriaLabel(i18n: I18n, stage: MapStage, scope: StageScope): string {
  const badge = stageBadge(i18n, stage.state)
  return i18n
    .t('map.a11y.stage', {
      number: stage.number,
      slug: stage.slug,
      phase: scope.phaseLabel,
      state: badge.label,
      repo: scope.repoLabel,
      intent: scope.intentLabel,
      facts: stageFacts(i18n, stage).join(', '),
    })
    .replace(/\s+/g, ' ')
    .trim()
}

export interface StageCardProps {
  stage: MapStage
  density: Density
  selected: boolean
  relation: Relation
  scope: StageScope
  onSelect: (slug: string) => void
}

export function StageCard({ stage, density, selected, relation, scope, onSelect }: StageCardProps) {
  const i18n = useI18n()
  const { t } = i18n
  const badge = stageBadge(i18n, stage.state)
  const detailed = density !== 'overview'

  return (
    <button
      type="button"
      className="studio-stage"
      data-stage={stage.slug}
      data-variant="stage"
      data-state={stage.state}
      data-phase={stage.phase}
      {...(stage.is_current ? { 'data-current': 'true' } : {})}
      {...(relation ? { 'data-relation': relation } : {})}
      aria-pressed={selected}
      aria-label={stageAriaLabel(i18n, stage, scope)}
      onClick={() => onSelect(stage.slug)}
    >
      {relation ? (
        // The relationship is also written out in the inspector; this tag is the on-canvas echo of it,
        // and it carries the word as well as the colour.
        <span className="studio-stage-rel" data-relation={relation} aria-hidden>
          {t(`map.rel.${relation}`)}
        </span>
      ) : null}

      <span className="studio-stage-num studio-mono" aria-hidden>
        {stage.number}
      </span>
      <span className="studio-stage-name">{stage.slug}</span>

      <span className="studio-stage-chips" aria-hidden>
        <Chip tone={badge.tone} icon={badge.icon}>
          {badge.label}
        </Chip>
        {stage.is_current ? (
          <Chip tone="accent">
            <span className="studio-pulse" aria-hidden />
            {t('map.chip.current')}
          </Chip>
        ) : null}
        {stage.gate ? (
          <Chip tone="warn" icon="gate">
            {t('map.chip.gate')}
          </Chip>
        ) : null}
        {detailed && stage.execution === 'CONDITIONAL' ? <Chip icon="info">{t('map.chip.conditional')}</Chip> : null}
        {detailed && stage.agent ? (
          <Chip icon="intent" mono title={t('map.chip.agentTitle', { agent: stage.agent })}>
            {stage.agent}
          </Chip>
        ) : null}
        {detailed && stage.review_class ? (
          <Chip tone="info" icon="review" mono title={t('map.chip.reviewerTitle', { reviewer: stage.reviewer ?? '' })}>
            {stage.review_class}
          </Chip>
        ) : null}
        {detailed && stage.elapsed_secs !== null ? (
          <Chip icon="clock" mono title={t('map.chip.elapsedTitle', { duration: duration(i18n, stage.elapsed_secs) })}>
            {duration(i18n, stage.elapsed_secs)}
          </Chip>
        ) : null}
        {detailed ? (
          <Chip icon="doc" mono>
            {i18n.fmt.number(stage.artifacts.length)}
          </Chip>
        ) : null}
      </span>

      {/* Kept in every density: FR-MAP-003 is about the reason surviving, not about paint. */}
      {stage.skipped_reason ? (
        <span className="studio-stage-why" aria-hidden>
          <Icon name="info" size={11} strokeWidth={2} /> {stage.skipped_reason}
        </span>
      ) : null}
    </button>
  )
}

export interface UnitCardProps {
  stage: MapStage
  unit: MapUnit
  selected: boolean
  scope: StageScope
  onSelect: (slug: string, unit: string) => void
}

/** A per-unit sub-card. Presentational state only — see `map.units.note`. */
export function UnitCard({ stage, unit, selected, scope, onSelect }: UnitCardProps) {
  const i18n = useI18n()
  const badge = stageBadge(i18n, unit.state)
  const facts = [artifactCountLabel(i18n, unit.artifacts.length)]

  return (
    <button
      type="button"
      className="studio-stage"
      data-stage={stage.slug}
      data-unit={unit.unit}
      data-variant="unit"
      data-state={unit.state}
      aria-pressed={selected}
      aria-label={i18n
        .t('map.a11y.unit', {
          unit: unit.unit,
          number: stage.number,
          slug: stage.slug,
          state: badge.label,
          repo: scope.repoLabel,
          intent: scope.intentLabel,
          facts: facts.join(', '),
        })
        .replace(/\s+/g, ' ')
        .trim()}
      onClick={() => onSelect(stage.slug, unit.unit)}
    >
      <span className="studio-stage-num studio-mono" aria-hidden>
        {stage.number}
      </span>
      <span className="studio-stage-name studio-mono">{unit.unit}</span>
      <span className="studio-stage-chips" aria-hidden>
        <Chip tone={badge.tone} icon={badge.icon}>
          {badge.label}
        </Chip>
        <Chip icon="doc" mono>
          {i18n.fmt.number(unit.artifacts.length)}
        </Chip>
      </span>
    </button>
  )
}
