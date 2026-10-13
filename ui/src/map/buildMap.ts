/**
 * The Workflow Map's model, assembled in the browser from the facts `GET …/map` sends.
 *
 * The server keeps only what needs a reader — the artifact walk, the skip reasons parsed out of the
 * state file, each stage's latest attempt read from the audit — and this module does the join, the
 * grouping and the counting. The result is exactly the `MapModel` the server used to send
 * (`buildMap.test.ts` replays the recorded outputs), so the components did not change.
 *
 * Union, not intersection: a stage the graph knows but the state file does not list is `excluded` (the
 * plan did not select it), and a row the graph does not know is still shown — that pair is exactly what
 * `stage_graph_drift` warns about, and dropping either side would hide it.
 */

import { PER_UNIT_STAGES, PHASES } from '../lib/enums.generated'
import type { ArtifactMeta, MapInputs, MapModel, MapNode, MapPhase, MapStage, MapUnit, StageRow } from '../lib/types'

/** States whose open attempt is measured to the server's `now`. */
const IN_FLIGHT: ReadonlySet<string> = new Set(['in_progress', 'awaiting_approval', 'revising'])
const LIFECYCLE: ReadonlySet<string> = new Set<string>(PHASES)
const CASED = /\p{Cased}/u

/** Everything one stage is joined against. */
interface Join {
  inputs: MapInputs
  dependents: Map<string, string[]>
  /** By stage, then by unit; `null` is the stage's own directory. */
  artifacts: Map<string, Map<string | null, ArtifactMeta[]>>
  current: string | null
  directive: string | null
}

export function buildMap(inputs: MapInputs): MapModel {
  // Keyed like the dict the server used to build: the last row for a slug wins, in the first one's place.
  const rows = new Map<string, StageRow>()
  for (const row of inputs.rows) rows.set(row.slug, row)
  const known = new Set(inputs.nodes.map((node) => node.slug))
  // A terminal map keeps its last stage for navigation but has no execution cursor.
  const completed = (inputs.status ?? '').trim().toLowerCase() === 'completed'
  const join: Join = {
    inputs,
    dependents: new Map(),
    artifacts: new Map(),
    current: completed ? null : inputs.current_stage,
    directive: completed ? null : inputs.directive_stage,
  }
  for (const node of inputs.nodes) for (const slug of node.requires_stage) append(join.dependents, slug, node.slug)
  for (const meta of inputs.artifacts) {
    if (meta.stage == null) continue
    let byUnit = join.artifacts.get(meta.stage)
    if (!byUnit) join.artifacts.set(meta.stage, (byUnit = new Map()))
    append(byUnit, meta.unit, meta)
  }

  const stages = inputs.nodes.map((node) => stageOf(join, node.slug, node, rows.get(node.slug) ?? null))
  for (const [slug, row] of rows) if (!known.has(slug)) stages.push(stageOf(join, slug, null, row))

  const status = new Map<string, string>()
  for (const [name, value] of inputs.phases) status.set(name.trim().toLowerCase(), value.trim())
  const phases: MapPhase[] = []
  for (const phase of PHASES) {
    const members = stages.filter((stage) => stage.phase === phase)
    if (!members.length) continue
    phases.push({
      phase,
      status: status.get(phase) ?? 'Unknown',
      stages: members,
      counts: {
        total: members.length,
        in_scope: count(members, (stage) => stage.in_scope),
        done: count(members, (stage) => stage.state === 'completed' || stage.state === 'skipped'),
        skipped: count(members, (stage) => stage.state === 'skipped' || !!stage.skipped_reason),
      },
    })
  }
  // A phase the lifecycle does not name: one trailing bucket, with no progress claimed for it.
  const extra = stages.filter((stage) => !LIFECYCLE.has(stage.phase))
  if (extra.length) {
    phases.push({
      phase: 'unknown',
      status: 'Unknown',
      stages: extra,
      counts: { total: extra.length, in_scope: count(extra, (stage) => stage.in_scope), done: 0, skipped: 0 },
    })
  }

  return {
    intent_key: inputs.intent_key,
    phases,
    counts: {
      stages_known: stages.length,
      stages_selected: count(stages, (stage) => stage.in_scope),
      gates: count(stages, (stage) => stage.gate && stage.in_scope),
    },
    units: [...inputs.units],
    graph_version: inputs.graph_version,
    stage_count: inputs.nodes.length || rows.size,
  }
}

function stageOf(join: Join, slug: string, node: MapNode | null, row: StageRow | null): MapStage {
  const { inputs } = join
  const phase = node?.phase || row?.phase || 'unknown'
  const inScope = selected(inputs, slug, node, row)
  const state: MapStage['state'] = !row || (!inScope && row.state === 'not_started') ? 'excluded' : row.state
  const perUnit = node ? node.per_unit : PER_UNIT_STAGES.includes(slug)
  return {
    slug,
    number: node ? node.number : '',
    name: node ? node.name : title(slug.replaceAll('-', ' ')),
    phase,
    state,
    execution: node ? node.execution : 'ALWAYS',
    in_scope: inScope,
    mode: node ? node.mode : '',
    agent: node ? node.lead_agent : '',
    reviewer: node ? node.reviewer : null,
    review_class: node ? node.review_class : null,
    // Only selected, non-initialization stages contribute a gate to this plan, and a stage AI-DLC skipped opened none.
    gate: inScope && phase !== 'initialization' && state !== 'skipped',
    per_unit: perUnit,
    summary_confirmation: node ? node.summary_confirmation : null,
    consumes: node ? [...node.consumes] : [],
    produces: node ? [...node.produces] : [],
    depends_on: node ? [...node.requires_stage] : [],
    dependents: [...(join.dependents.get(slug) ?? [])],
    elapsed_secs: elapsed(inputs, slug, state),
    artifacts: [...(join.artifacts.get(slug)?.get(null) ?? [])],
    skipped_reason: own(inputs.skip_reasons, slug) ?? null,
    units: unitsOf(join, slug, perUnit, state),
    is_current: slug === join.current,
    is_directive: slug === join.directive,
  }
}

/**
 * Whether the plan selected a stage. An explicit row suffix decides, as AI-DLC's effectivePlanAction
 * resolves it before the scope grid. A suffixless legacy row the graph knows falls back to the grid
 * and, without grid metadata, stays visible like PlanService's live-plan default (except jump-skipped
 * rows). An absent state row is never invented.
 */
function selected(inputs: MapInputs, slug: string, node: MapNode | null, row: StageRow | null): boolean {
  if (!row) return false
  const suffix = (row.suffix ?? '').trim().toUpperCase()
  if (suffix || !node) return suffix.startsWith('EXECUTE')
  const action = (own(inputs.scope_grid, slug) ?? '').trim().toUpperCase()
  return action === 'EXECUTE' || action === 'SKIP' ? action === 'EXECUTE' : row.state !== 'skipped'
}

/** Wall time of the latest attempt; one that has not ended counts only while the stage is in flight. */
function elapsed(inputs: MapInputs, slug: string, state: string): number | null {
  const attempt = own(inputs.attempts, slug)
  if (!attempt) return null
  if (attempt.ended !== null) return Math.max(0, Math.trunc(attempt.ended - attempt.started))
  return IN_FLIGHT.has(state) ? Math.max(0, Math.trunc(inputs.now - attempt.started)) : null
}

/**
 * Per-unit sub-rows for a Construction stage.
 *
 * The state file has ONE row per stage and writes a literal `Per unit: [TBD]` line (FORMAT-NOTES §3),
 * so there is no per-unit mark on disk. A unit's state is therefore presentational, derived from two
 * observable facts — whether it is the state file's `Active Unit`, and whether its stage directory
 * holds any file — and never authorises a decision.
 */
function unitsOf(join: Join, slug: string, perUnit: boolean, state: MapStage['state']): MapUnit[] {
  const { inputs } = join
  if (!perUnit || !inputs.units.length) return []
  return inputs.units.map((unit) => {
    const artifacts = [...(join.artifacts.get(slug)?.get(unit) ?? [])]
    const unitState = unit === inputs.active_unit ? state : artifacts.length ? 'completed' : 'not_started'
    return { unit, state: unitState, artifacts }
  })
}

/** Python's `str.title()` (titlecase digraphs aside), which names a stage the graph does not know. */
function title(text: string): string {
  let out = ''
  let previousCased = false
  for (const char of text) {
    out += previousCased ? char.toLowerCase() : char.toUpperCase()
    previousCased = CASED.test(char)
  }
  return out
}

/** A record's own value: slugs are free text, and `constructor` must not find Object's. */
function own<T>(record: Record<string, T>, key: string): T | undefined {
  return Object.hasOwn(record, key) ? record[key] : undefined
}

function append<K, V>(map: Map<K, V[]>, key: K, value: V): void {
  const list = map.get(key)
  if (list) list.push(value)
  else map.set(key, [value])
}

function count(stages: MapStage[], test: (stage: MapStage) => boolean): number {
  return stages.filter(test).length
}
