/**
 * The Workflow Map: one intent's stage graph, read from disk, in five phase swimlanes.
 *
 * The decisions worth knowing before changing anything here:
 *
 *  - **Density is a rendering choice, not a query.** `GET …/map` validates `?density=` and then ignores
 *    it, because the model already carries every stage, its unit lanes and its edges. So the resource
 *    key deliberately excludes density: switching Overview → Dependencies must not cost a round trip or
 *    blank the canvas mid-read.
 *  - **Below 900px the canvas becomes accordions, not a smaller canvas** (FR-MAP-009). The switch is
 *    made in JS at 900px rather than with `useIsNarrow()` (767px) because the stylesheet already hides
 *    the inspector and collapses lanes to one column at 900px: deciding at 767px would leave a
 *    768–900px window with a single-column canvas and no inspector at all — exactly the shrink-to-fit
 *    the requirement rejects.
 *  - **The map never dispatches.** It holds no `captured` snapshot and no confirmation panel, so its
 *    only outgoing control navigates to the Action Center, where the card is re-read and the exact wire
 *    text is shown before anything is sent.
 *  - **Announcements are rationed.** The execution marker is polite and its text only changes when the
 *    stage does, so a 15-second poll re-announces nothing. Recovery and delivery uncertainty are the
 *    only assertive messages, plus a failed read — everything else is silent (PRD §10.3).
 */

import { useCallback, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent } from 'react'
import { ContentSkeleton, EmptyState } from '@kirocrew/app-sdk/ui'

import { useI18n, type I18n } from '../i18n'
import { StudioApiError, type StudioApi } from '../lib/api'
import { PHASES, type Phase } from '../lib/enums.generated'
import { duration, plural } from '../lib/format'
import { useResource } from '../lib/useResource'
import type { Navigate, StudioRoute } from '../lib/route'
import type {
  ActionCard, ArtifactMeta, AuditEventView, IntentResponse, MapPhase, MapResponse, MapStage, MapUnit,
  ReviewResponse,
} from '../lib/types'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { useShellData } from '../shell/StudioApp'
import type { ViewProps } from '../shell/ViewRouter'
import { DensityControl } from './DensityControl'
import { DependencyOverlay } from './DependencyOverlay'
import { PhaseAccordion } from './PhaseAccordion'
import { PhaseLane } from './PhaseLane'
import { StageInspector } from './StageInspector'
import { artifactCountLabel, stageBadge, type Density, type Relation } from './StageCard'

/** Remembered per browser: an operator who works in Detailed means it. */
const DENSITY_KEY = 'aidlc-studio:mapDensity'
/** The breakpoint at which the stylesheet has already given up on the canvas. */
const ACCORDION_QUERY = '(max-width: 900px)'
/** Queue types that mean the record and Studio disagree; the only assertive message on this page. */
const RECOVERY_TYPES = new Set(['recovery', 'delivery_uncertain'])

const PHASE_ORDER = new Map<string, number>(PHASES.map((phase, index) => [phase, index]))

function isDensity(value: unknown): value is Density {
  return value === 'overview' || value === 'detailed' || value === 'dependencies'
}

function readDensity(): Density {
  try {
    const stored = localStorage.getItem(DENSITY_KEY)
    return isDensity(stored) ? stored : 'overview'
  } catch {
    return 'overview'
  }
}

function useDensity(): [Density, (next: Density) => void] {
  const [density, setDensity] = useState<Density>(readDensity)
  const set = useCallback((next: Density) => {
    setDensity(next)
    try {
      localStorage.setItem(DENSITY_KEY, next)
    } catch {
      // A preference that cannot be stored still applies for this page.
    }
  }, [])
  return [density, set]
}

/** True when the viewport is at or below the width where the canvas stops being readable. */
function useAccordions(): boolean {
  const subscribe = useCallback((onChange: () => void) => {
    if (typeof window.matchMedia !== 'function') return () => {}
    const query = window.matchMedia(ACCORDION_QUERY)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])
  return useSyncExternalStore(
    subscribe,
    () => (typeof window.matchMedia === 'function' ? window.matchMedia(ACCORDION_QUERY).matches : false),
    () => false,
  )
}

function phaseLabelFor(i18n: I18n, phase: string): string {
  return PHASE_ORDER.has(phase) ? i18n.t(`enum.phase.${phase as Phase}`) : i18n.t('map.phase.unknown')
}

export interface MapPageProps {
  api: StudioApi
  route: StudioRoute
  go: Navigate
  /** The shell's live queue, already scoped to this repo and intent. */
  cards: ActionCard[]
}

export function MapPage({ api, route, go, cards }: MapPageProps) {
  const i18n = useI18n()
  const { t } = i18n
  const [density, setDensity] = useDensity()
  const [showUnits, setShowUnits] = useState(false)
  const [asTable, setAsTable] = useState(false)
  const [allStagesFor, setAllStagesFor] = useState<string | null>(null)
  const accordions = useAccordions()
  const canvasRef = useRef<HTMLDivElement | null>(null)
  // State, not just the ref: the overlay has to re-measure when the canvas element first mounts.
  const [canvasEl, setCanvasEl] = useState<HTMLElement | null>(null)

  const repo = route.repo
  const intent = route.intent
  const ready = !!repo && !!intent
  const planKey = `${repo}:${intent}`
  const showAllStages = allStagesFor === planKey

  const mapRes = useResource<MapResponse>(
    ready ? `map:${repo}:${intent}` : null,
    useCallback((signal) => api.map(repo, intent, undefined, { signal }), [api, repo, intent]),
  )
  const intentRes = useResource<IntentResponse>(
    ready ? `map:intent:${repo}:${intent}` : null,
    useCallback((signal) => api.intent(repo, intent, { signal }), [api, repo, intent]),
  )
  // Only read once something is selected, and never on a timer: a review verdict changes when the
  // record does, and the event stream already forces a refetch.
  const reviewRes = useResource<ReviewResponse>(
    ready && route.stage ? `map:review:${repo}:${intent}` : null,
    useCallback((signal) => api.review(repo, intent, { signal }), [api, repo, intent]),
    { interval: 0 },
  )

  const model = mapRes.data?.map ?? null
  const detail = intentRes.data?.intent ?? null

  const graphPhases = useMemo<MapPhase[]>(() => {
    if (!model) return []
    // Sorted defensively: the lane order IS the lifecycle (FR-MAP-001), and a phase arriving out of
    // order would misrepresent what runs before what. Unrecognised phases go last.
    return [...model.phases].sort(
      (a, b) => (PHASE_ORDER.get(a.phase) ?? PHASES.length) - (PHASE_ORDER.get(b.phase) ?? PHASES.length),
    )
  }, [model])

  const allStages = useMemo<MapStage[]>(() => graphPhases.flatMap((phase) => phase.stages), [graphPhases])
  const planStages = useMemo(() => allStages.filter((stage) => stage.in_scope), [allStages])
  const phases = useMemo<MapPhase[]>(() => {
    const visible = new Set((showAllStages ? allStages : planStages).map((stage) => stage.slug))
    return graphPhases.map((phase) => {
      const stages = phase.stages.filter((stage) => visible.has(stage.slug)).map((stage) => ({
        ...stage,
        // Older hosts expose the structural Gate flag even for an excluded stage.
        gate: stage.in_scope && stage.gate,
        state: !stage.in_scope && stage.state === 'not_started' ? 'excluded' as const : stage.state,
        depends_on: stage.depends_on.filter((slug) => visible.has(slug)),
        dependents: stage.dependents.filter((slug) => visible.has(slug)),
      }))
      return {
        ...phase,
        stages,
        counts: {
          total: stages.length,
          in_scope: stages.filter((stage) => stage.in_scope).length,
          done: stages.filter((stage) => stage.state === 'completed' || stage.state === 'skipped').length,
          skipped: stages.filter((stage) => stage.state === 'skipped' || stage.skipped_reason).length,
        },
      }
    }).filter((phase) => phase.stages.length > 0)
  }, [graphPhases, allStages, planStages, showAllStages])
  const visibleStages = useMemo(() => phases.flatMap((phase) => phase.stages), [phases])
  const selected = useMemo(
    () => visibleStages.find((stage) => stage.slug === route.stage) ?? null,
    [visibleStages, route.stage],
  )
  const hiddenSelection = !showAllStages && allStages.some(
    (stage) => stage.slug === route.stage && !stage.in_scope,
  )
  const selectedUnit = useMemo<MapUnit | null>(
    () => (selected && route.unit ? selected.units.find((unit) => unit.unit === route.unit) ?? null : null),
    [selected, route.unit],
  )

  const relations = useMemo(() => {
    const found = new Map<string, Exclude<Relation, null>>()
    if (!selected) return found
    for (const slug of selected.depends_on) found.set(slug, 'upstream')
    // Upstream wins on a slug that is both: a cycle in the graph is drift, not a thing to paint twice.
    for (const slug of selected.dependents) if (!found.has(slug)) found.set(slug, 'downstream')
    return found
  }, [selected])

  const intentCards = useMemo(
    () => cards.filter((card) => card.intent.intent_key === intent),
    [cards, intent],
  )
  const stageAction = useMemo(
    () => (selected ? intentCards.find((card) => card.stage?.slug === selected.slug) ?? null : null),
    [intentCards, selected],
  )
  const recoveryCard = useMemo(
    () => intentCards.find((card) => RECOVERY_TYPES.has(card.queue_type)) ?? null,
    [intentCards],
  )

  const stageAudit = useMemo<AuditEventView[] | null>(() => {
    if (!detail) return null
    if (!selected) return []
    return detail.audit_tail
      .filter((event) => mentionsStage(event, selected.slug))
      .slice(-12)
      .reverse()
  }, [detail, selected])

  const selectStage = useCallback(
    (slug: string) => {
      const same = route.stage === slug && !route.unit
      go({ stage: same ? '' : slug, unit: '' })
    },
    [go, route.stage, route.unit],
  )
  const selectUnit = useCallback(
    (slug: string, unit: string) => {
      const same = route.stage === slug && route.unit === unit
      go({ stage: slug, unit: same ? '' : unit })
    },
    [go, route.stage, route.unit],
  )
  const clearSelection = useCallback(() => go({ stage: '', unit: '' }), [go])
  const openAction = useCallback(
    (actionId: string) => go({ view: 'actions', action: actionId, tab: 'decision', artifact: '', anchor: '' }),
    [go],
  )
  const openArtifact = useCallback(
    (meta: ArtifactMeta) =>
      go({
        view: 'actions',
        action: '',
        tab: 'artifacts',
        stage: meta.stage ?? route.stage,
        unit: meta.unit ?? '',
        artifact: meta.artifact_id,
        anchor: '',
      }),
    [go, route.stage],
  )

  /**
   * Arrow keys move focus between cards; Tab order is untouched.
   *
   * Additive on purpose: every card stays tabbable, so the order is still the predictable document one
   * (PRD §10.3), and the arrows are a shortcut for the 33-card case rather than a roving tabindex whose
   * single entry point moves under the user when the poll re-renders.
   */
  const onCanvasKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key === 'Escape') {
        if (route.stage) {
          event.preventDefault()
          clearSelection()
        }
        return
      }
      const root = canvasRef.current
      if (!root) return
      const focusable = [...root.querySelectorAll<HTMLElement>('.studio-stage')]
      const here = focusable.indexOf(document.activeElement as HTMLElement)
      if (here < 0) return

      let next = -1
      if (event.key === 'ArrowRight') next = Math.min(focusable.length - 1, here + 1)
      else if (event.key === 'ArrowLeft') next = Math.max(0, here - 1)
      else if (event.key === 'Home') next = 0
      else if (event.key === 'End') next = focusable.length - 1
      else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        const lanes = [...root.querySelectorAll<HTMLElement>('[data-lane]')]
        const current = focusable[here]?.closest<HTMLElement>('[data-lane]') ?? null
        const laneIndex = current ? lanes.indexOf(current) : -1
        const target = lanes[laneIndex + (event.key === 'ArrowDown' ? 1 : -1)]
        const first = target?.querySelector<HTMLElement>('.studio-stage') ?? null
        if (first) next = focusable.indexOf(first)
      } else return

      const target = focusable[next]
      if (target) {
        event.preventDefault()
        target.focus()
      }
    },
    [clearSelection, route.stage],
  )

  if (!ready) {
    return (
      <div className="studio-scroll">
        <div className="studio-page">
          <EmptyState
            icon={<Icon name="map" size={18} />}
            title={t('map.empty.title')}
            subtitle={repo ? t('map.empty.body') : t('map.empty.noRepo')}
            action={
              <button type="button" className="studio-btn" onClick={() => go({ view: 'intents' })}>
                <Icon name="intent" size={15} />
                {t('map.empty.action')}
              </button>
            }
          />
        </div>
      </div>
    )
  }

  if (!model) {
    return (
      <div className="studio-scroll">
        <div className="studio-page">
          {mapRes.error ? <ReadFailure error={mapRes.error} onRetry={() => void mapRes.refresh()} /> : <ContentSkeleton rows={8} />}
        </div>
      </div>
    )
  }

  const current = planStages.find((stage) => stage.is_current) ?? null
  const doneCount = planStages.filter((stage) => stage.state === 'completed' || stage.state === 'skipped').length
  const executionText = current
    ? t('map.status.executing', { number: current.number, slug: current.slug }).replace(/\s+/g, ' ').trim()
    : t('map.status.idle')
  const intentLabel = detail?.slug || intent
  const repoLabel = detail?.repo_label || repo
  const scope = { repoLabel, intentLabel }
  const perUnitPresent = visibleStages.some((stage) => stage.per_unit && stage.units.length > 0)
  const blocked: 'paused' | 'archived' | null = detail?.archived ? 'archived' : detail?.paused ? 'paused' : null

  const inspector = (
    <StageInspector
      stage={selected}
      unit={selectedUnit}
      phaseLabel={selected ? phaseLabelFor(i18n, selected.phase) : ''}
      repoLabel={repoLabel}
      intentLabel={intentLabel}
      review={reviewRes.data ?? null}
      audit={stageAudit}
      action={stageAction}
      blocked={blocked}
      onClear={clearSelection}
      onSelectStage={selectStage}
      onOpenAction={openAction}
      onOpenArtifact={openArtifact}
    />
  )

  return (
    <div className="studio-map">
      <div className="studio-map-bar">
        <Chip tone="accent" icon="intent" mono>
          {t('map.bar.scope', { repo: repoLabel, intent: intentLabel })}
        </Chip>
        {showAllStages ? <Chip mono>{plural(i18n, 'map.bar.stagesKnown', allStages.length)}</Chip> : null}
        <Chip mono>{plural(i18n, 'map.bar.stagesSelected', planStages.length)}</Chip>
        <Chip mono icon="gate">
          {plural(i18n, 'map.bar.gates', planStages.filter((stage) => stage.gate).length)}
        </Chip>
        {model.graph_version ? <Chip mono icon="lock">{t('map.bar.engine', { version: model.graph_version })}</Chip> : null}

        {/* Visible AND polite: the text changes only when the executing stage does, so a poll is silent. */}
        <span className="studio-map-exec" role="status" aria-live="polite">
          <Chip tone={current ? 'accent' : 'neutral'} icon={current ? 'play' : 'pause'}>
            {executionText}
          </Chip>
          <Chip icon="check">{t('map.status.progress', { done: i18n.fmt.number(doneCount), total: i18n.fmt.number(planStages.length) })}</Chip>
        </span>

        <div className="studio-map-bar-controls">
          <button
            type="button"
            className="studio-btn studio-btn-sm"
            aria-pressed={showAllStages}
            onClick={() => setAllStagesFor(showAllStages ? null : planKey)}
          >
            {t(showAllStages ? 'map.bar.showPlanOnly' : 'map.bar.showAllStages')}
          </button>
          <DensityControl value={density} onChange={setDensity} />
          {perUnitPresent ? (
            <button
              type="button"
              className="studio-btn studio-btn-sm"
              aria-pressed={showUnits}
              onClick={() => setShowUnits((was) => !was)}
            >
              <Icon name="chevronDown" size={13} />
              {t(showUnits ? 'map.bar.hideUnits' : 'map.bar.expandUnits')}
            </button>
          ) : null}
          <button
            type="button"
            className="studio-btn studio-btn-sm"
            aria-pressed={asTable}
            onClick={() => setAsTable((was) => !was)}
          >
            <Icon name="doc" size={13} />
            {t(asTable ? 'map.bar.showCanvas' : 'map.bar.showTable')}
          </button>
        </div>
      </div>

      {recoveryCard ? (
        // Assertive: the map is a reading of disk, and this says the reading and Studio's record
        // disagree. Anything the user concludes from the lanes until then may be wrong.
        <div className="studio-banner" data-tone="danger" role="alert">
          <Icon name="recovery" size={15} />
          <span className="studio-grow">{t('map.alert.recovery')}</span>
          <button type="button" className="studio-btn" onClick={() => openAction(recoveryCard.action_id)}>
            {t('map.alert.openAction')}
          </button>
        </div>
      ) : null}

      {detail?.unstable ? (
        <div className="studio-banner" data-tone="warn" role="status">
          <Icon name="warn" size={15} />
          <span className="studio-grow">{t('map.alert.unstable')}</span>
        </div>
      ) : null}

      {mapRes.error ? <ReadFailure error={mapRes.error} onRetry={() => void mapRes.refresh()} /> : null}
      {hiddenSelection ? <p className="studio-consequence" role="status">{t('map.hiddenSelection')}</p> : null}

      <div className="studio-map-main">
        <div className="studio-map-scroll">
          {phases.length === 0 ? (
            <p className="studio-muted" role="status">{t('map.empty.plan')}</p>
          ) : asTable ? (
            <>
              <StageTable
                phases={phases}
                intentLabel={intentLabel}
                selectedStage={route.stage}
                onSelect={selectStage}
              />
              {accordions && selected ? (
                <div className="studio-map-sheet" role="complementary" aria-label={t('map.inspector.title')}>
                  {inspector}
                </div>
              ) : null}
            </>
          ) : accordions ? (
            <>
              <p className="studio-consequence">{t('map.accordion.note')}</p>
              <PhaseAccordion
                phases={phases}
                density={density}
                showUnits={showUnits}
                selectedStage={route.stage}
                selectedUnit={route.unit}
                scope={scope}
                onSelectStage={selectStage}
                onSelectUnit={selectUnit}
                phaseLabel={(phase) => phaseLabelFor(i18n, phase)}
                inspector={inspector}
              />
            </>
          ) : (
            <div
              className="studio-map-canvas"
              ref={(node) => {
                canvasRef.current = node
                setCanvasEl(node)
              }}
              onKeyDown={onCanvasKeyDown}
            >
              <ul className="studio-lanes" role="list" aria-label={t('map.a11y.canvas')}>
                {phases.map((phase) => (
                  <PhaseLane
                    key={phase.phase}
                    phase={phase}
                    phaseLabel={phaseLabelFor(i18n, phase.phase)}
                    density={density}
                    showUnits={showUnits}
                    selectedStage={route.stage}
                    selectedUnit={route.unit}
                    relations={relations}
                    scope={scope}
                    onSelectStage={selectStage}
                    onSelectUnit={selectUnit}
                  />
                ))}
              </ul>
              {density === 'dependencies' && selected ? (
                <DependencyOverlay
                  canvas={canvasEl}
                  from={selected.slug}
                  relations={relations}
                  token={`${density}:${showUnits}:${selected.slug}:${visibleStages.length}`}
                />
              ) : null}
            </div>
          )}
          <p className="studio-consequence">{t('map.consequence')}</p>
        </div>

        {!accordions ? (
          <aside className="studio-inspector" aria-label={t('map.inspector.title')}>
            {inspector}
          </aside>
        ) : null}
      </div>

      {/* Selection is user-initiated, so one short sentence is enough; the inspector itself is not a
          live region — announcing four evidence blocks on every selection would be unusable. */}
      <span className="studio-sr" role="status" aria-live="polite">
        {selected
          ? selectedUnit
            ? t('map.a11y.unitSelected', { unit: selectedUnit.unit, number: selected.number, slug: selected.slug })
            : t('map.a11y.selected', { number: selected.number, slug: selected.slug })
          : ''}
      </span>
    </div>
  )
}

/** Stage-scoped audit rows: `Stage` when the event has one, otherwise the artifact `Context` path. */
function mentionsStage(event: AuditEventView, slug: string): boolean {
  const stage = event.fields['Stage']
  if (stage) return stage === slug
  const context = event.fields['Context'] ?? ''
  return context.split(/\s*>\s*/).includes(slug)
}

function ReadFailure({ error, onRetry }: { error: StudioApiError; onRetry: () => void }) {
  const { t, has } = useI18n()
  return (
    <div className="studio-banner" data-tone="danger" role="alert">
      <Icon name="warn" size={15} />
      <span className="studio-grow">
        <b>{t('map.error.title')}</b>{' '}
        {has(`errors.${error.code}`) ? t(`errors.${error.code}`) : error.message}
      </span>
      <button type="button" className="studio-btn" onClick={onRetry}>
        {t('common.retry')}
      </button>
    </div>
  )
}

interface StageTableProps {
  phases: MapPhase[]
  intentLabel: string
  selectedStage: string
  onSelect: (slug: string) => void
}

/**
 * The table alternative (PRD §10.3: "Tables and swimlanes have list/table semantic alternatives").
 *
 * Same data, same selection control, one row per stage in phase order — so the 2-D arrangement is never
 * the only way to read the plan, on any assistive technology or at any width.
 */
function StageTable({ phases, intentLabel, selectedStage, onSelect }: StageTableProps) {
  const i18n = useI18n()
  const { t } = i18n
  const columns = ['phase', 'number', 'stage', 'state', 'agent', 'gate', 'review', 'elapsed', 'artifacts', 'notes'] as const

  return (
    <div className="studio-map-table">
      <table className="studio-tbl">
        <caption className="studio-sr">{t('map.a11y.tableCaption', { intent: intentLabel })}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} scope="col">
                {t(`map.col.${column}`)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {phases.flatMap((phase) =>
            phase.stages.map((stage) => {
              const badge = stageBadge(i18n, stage.state)
              // `data-selected`, not `aria-selected`: a `row` in a plain table does not support
              // `aria-selected`, and the cell's own button already exposes the state as `aria-pressed`.
              return (
                <tr key={`${phase.phase}:${stage.slug}`} data-selected={stage.slug === selectedStage ? 'true' : undefined}>
                  <td>{phaseLabelFor(i18n, phase.phase)}</td>
                  <td className="studio-mono">{stage.number}</td>
                  <td>
                    <button
                      type="button"
                      className="studio-link studio-mono"
                      aria-pressed={stage.slug === selectedStage}
                      onClick={() => onSelect(stage.slug)}
                    >
                      {stage.slug}
                    </button>
                  </td>
                  <td>
                    <Chip tone={badge.tone} icon={badge.icon}>
                      {badge.label}
                    </Chip>
                  </td>
                  <td className="studio-mono">{stage.agent}</td>
                  <td>{stage.gate ? t('map.chip.gate') : t('common.none')}</td>
                  <td className="studio-mono">{stage.review_class ?? t('common.none')}</td>
                  <td className="studio-mono">{duration(i18n, stage.elapsed_secs)}</td>
                  <td className="studio-mono">{artifactCountLabel(i18n, stage.artifacts.length)}</td>
                  <td className="studio-wrap-any">
                    {[
                      stage.is_current ? t('map.chip.current') : '',
                      stage.execution === 'CONDITIONAL' ? t('map.chip.conditional') : '',
                      stage.skipped_reason ?? '',
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </td>
                </tr>
              )
            }),
          )}
        </tbody>
      </table>
    </div>
  )
}

/** The view the shell mounts: it only resolves the shared reads, so `MapPage` stays testable alone. */
export function MapView({ route, go }: ViewProps) {
  const shell = useShellData()
  return <MapPage api={shell.api} route={route} go={go} cards={shell.actions.data?.actions ?? []} />
}

export default MapView
