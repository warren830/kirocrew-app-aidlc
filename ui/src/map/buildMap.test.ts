/**
 * `buildMap` must produce exactly the `MapModel` the server used to assemble.
 *
 * Two kinds of evidence. `golden/` holds v1's assembled maps, copied verbatim from `tests/golden/` before
 * the assembly moved here, and each is replayed from the inputs the backend now records for the same
 * scenario. The rule tests below are the Python map tests, ported, for the paths those scenarios never
 * reach: graph drift, a missing engine, the scope-grid fallback, the unknown lane.
 */

import { describe, expect, it } from 'vitest'

import { PHASES } from '../lib/enums.generated'
import type { MapInputs, MapModel, MapStage, StageState } from '../lib/types'
import { MAP_NOW, artifactMeta, mapInputs, mapNode, stageRow } from '../test/fixtures'
import { buildMap } from './buildMap'

// --------------------------------------------------------------------------- //
// the recorded outputs
// --------------------------------------------------------------------------- //

interface Recorded<T> { default: { body: T } }

const v1 = import.meta.glob('./golden/*/map_*.json', { eager: true }) as Record<string, Recorded<{ map: MapModel }>>
const recorded = import.meta.glob('../../../tests/golden/*/map_*.json', { eager: true }) as
  Record<string, Recorded<{ map_inputs: MapInputs }>>

const replays = Object.entries(v1).map(([path, old]) => {
  const name = path.slice('./golden/'.length)
  return { name, inputs: recorded[`../../../tests/golden/${name}`]?.default.body.map_inputs, model: old.default.body.map }
})

describe('the recorded maps', () => {
  it('replays a representative set: completed with unit lanes, a closed history, an open gate', () => {
    expect(replays.map((replay) => replay.name).sort()).toEqual([
      'gate-and-questions/map_260904-gate-demo.json',
      'older-2.1.1/map_260707-trello-plugin.json',
      'payload-2.3.0/map_260820-health-probe.json',
    ])
  })

  it.each(replays)('$name comes out exactly as v1 assembled it', ({ inputs, model }) => {
    expect(inputs, 'the backend records no inputs for this scenario').toBeDefined()
    expect(buildMap(inputs!)).toStrictEqual(model)
  })
})

// --------------------------------------------------------------------------- //
// the rules
// --------------------------------------------------------------------------- //

const SCAFFOLD = mapNode({ slug: 'workspace-scaffold', number: '0.1', phase: 'initialization' })
const DETECTION = mapNode({
  slug: 'workspace-detection', number: '0.2', phase: 'initialization', requires_stage: ['workspace-scaffold'],
})
const MARKET = mapNode({ slug: 'market-research', number: '1.2', phase: 'ideation', execution: 'CONDITIONAL' })
const REQUIREMENTS = mapNode({
  slug: 'requirements-analysis', number: '2.3', phase: 'inception', name: 'Requirements Analysis',
  lead_agent: 'aidlc-product-agent', reviewer: 'aidlc-product-lead-agent', review_class: 'advisory',
  summary_confirmation: 'required', consumes: ['intent-statement'], produces: ['requirements'],
  requires_stage: ['workspace-detection'],
})
const STORIES = mapNode({ slug: 'user-stories', number: '2.4', phase: 'inception', requires_stage: ['requirements-analysis'] })
const CODEGEN = mapNode({
  slug: 'code-generation', number: '3.5', phase: 'construction', per_unit: true, requires_stage: ['requirements-analysis'],
})
const BUILD = mapNode({ slug: 'build-and-test', number: '3.6', phase: 'construction', requires_stage: ['code-generation'] })
const DEPLOY = mapNode({ slug: 'deployment-execution', number: '4.3', phase: 'operation' })
const GRAPH = [SCAFFOLD, DETECTION, MARKET, REQUIREMENTS, STORIES, CODEGEN, BUILD, DEPLOY]

/** Every graph stage selected and not started, except what `states` says. */
function planned(states: Record<string, StageState> = {}, over: Partial<MapInputs> = {}): MapInputs {
  return mapInputs({ nodes: GRAPH, rows: GRAPH.map((node) => stageRow(node.slug, states[node.slug])), ...over })
}

function all(model: MapModel): MapStage[] {
  return model.phases.flatMap((phase) => phase.stages)
}

function stage(model: MapModel, slug: string): MapStage {
  const found = all(model).find((candidate) => candidate.slug === slug)
  if (!found) throw new Error(`no stage ${slug} on the map`)
  return found
}

/** One row replaced, the others as `planned` leaves them. */
function withRow(inputs: MapInputs, row: ReturnType<typeof stageRow>): MapInputs {
  return { ...inputs, rows: inputs.rows.map((candidate) => (candidate.slug === row.slug ? row : candidate)) }
}

describe('buildMap', () => {
  it('keeps the model shape: the lifecycle lanes in order, each with its counts', () => {
    const model = buildMap(planned())
    expect(Object.keys(model)).toEqual(['intent_key', 'phases', 'counts', 'units', 'graph_version', 'stage_count'])
    expect(Object.keys(model.phases[0]!)).toEqual(['phase', 'status', 'stages', 'counts'])
    expect(model.phases.map((phase) => phase.phase)).toEqual([...PHASES])
    expect(model.phases.reduce((total, phase) => total + phase.counts.total, 0)).toBe(GRAPH.length)
    // the two initialization stages never gate
    expect(model.counts).toEqual({ stages_known: 8, stages_selected: 8, gates: 6 })
    expect(model.stage_count).toBe(8)
    expect(model.graph_version).toBe('2.6.2')
  })

  it('joins each graph node with its state row, the cursor and the stage\'s own files', () => {
    const model = buildMap(planned({ 'requirements-analysis': 'awaiting_approval' }, {
      current_stage: 'requirements-analysis',
      directive_stage: 'requirements-analysis',
      artifacts: [
        artifactMeta({ artifact_id: 'art_state', name: 'aidlc-state.md', stage: null }),
        artifactMeta({ artifact_id: 'art_req', name: 'requirements.md', stage: 'requirements-analysis' }),
      ],
    }))
    expect(stage(model, 'requirements-analysis')).toMatchObject({
      number: '2.3', name: 'Requirements Analysis', phase: 'inception', state: 'awaiting_approval',
      in_scope: true, gate: true, is_current: true, is_directive: true, agent: 'aidlc-product-agent',
      reviewer: 'aidlc-product-lead-agent', review_class: 'advisory', summary_confirmation: 'required',
      consumes: ['intent-statement'], produces: ['requirements'], elapsed_secs: null, units: [],
    })
    expect(stage(model, 'requirements-analysis').artifacts.map((meta) => meta.name)).toEqual(['requirements.md'])
    // initialization stages never gate (FORMAT-NOTES §4: the graph has no `gate` key)
    expect(stage(model, 'workspace-scaffold')).toMatchObject({ gate: false, is_current: false, is_directive: false })
  })

  it('draws dependencies both ways', () => {
    const model = buildMap(planned())
    expect(stage(model, 'workspace-detection').depends_on).toEqual(['workspace-scaffold'])
    expect(stage(model, 'workspace-scaffold').dependents).toEqual(['workspace-detection'])
    expect(stage(model, 'requirements-analysis').dependents).toEqual(['user-stories', 'code-generation'])
    expect(stage(model, 'deployment-execution').dependents).toEqual([])
  })

  it('shows the skip reasons the server parsed and never selects a SKIP row', () => {
    const inputs = withRow(
      withRow(planned(), stageRow('market-research', 'not_started', { suffix: 'SKIP' })),
      stageRow('deployment-execution', 'skipped', { suffix: 'SKIP: greenfield' }),
    )
    const model = buildMap({ ...inputs, skip_reasons: { 'market-research': null, 'deployment-execution': 'greenfield' } })
    expect(stage(model, 'market-research')).toMatchObject({ in_scope: false, state: 'excluded', skipped_reason: null })
    expect(stage(model, 'deployment-execution')).toMatchObject({ in_scope: false, state: 'skipped', skipped_reason: 'greenfield' })
    expect(stage(model, 'requirements-analysis')).toMatchObject({ in_scope: true, skipped_reason: null })
    expect(model.phases.find((phase) => phase.phase === 'operation')!.counts).toEqual({ total: 1, in_scope: 0, done: 1, skipped: 1 })
  })

  it.each([
    ['not_started', 'excluded'], ['skipped', 'skipped'], ['completed', 'completed'],
  ] as const)('keeps an out-of-plan %s row visible as %s, without a gate', (state, expected) => {
    const model = buildMap(withRow(planned(), stageRow('deployment-execution', state, { suffix: 'SKIP' })))
    expect(all(model)).toHaveLength(8)
    expect(stage(model, 'deployment-execution')).toMatchObject({ in_scope: false, state: expected, gate: false })
    expect(model.counts.gates).toBe(all(model).filter((candidate) => candidate.gate).length)
  })

  it('lets an explicit suffix override the scope preset', () => {
    const inputs = withRow(planned(), stageRow('deployment-execution', 'not_started', { suffix: 'SKIP' }))
    const model = buildMap({ ...inputs, scope_grid: { 'user-stories': 'SKIP', 'deployment-execution': 'EXECUTE' } })
    expect(stage(model, 'user-stories')).toMatchObject({ in_scope: true, state: 'not_started', gate: true })
    expect(stage(model, 'deployment-execution')).toMatchObject({ in_scope: false, state: 'excluded', gate: false })
    expect(all(model).filter((candidate) => candidate.phase === 'initialization').some((candidate) => candidate.gate)).toBe(false)
  })

  it.each(['awaiting_approval', 'in_progress', 'not_started', 'completed'] as const)(
    'selects a suffixless %s row from the current scope grid',
    (state) => {
      const inputs = withRow(
        withRow(planned(), stageRow('requirements-analysis', state, { suffix: null })),
        stageRow('workspace-scaffold', 'completed', { suffix: null }),
      )
      const model = buildMap({
        ...inputs,
        current_stage: 'requirements-analysis',
        scope_grid: { 'requirements-analysis': 'EXECUTE', 'workspace-scaffold': ' execute ' },
      })
      expect(stage(model, 'requirements-analysis')).toMatchObject({ in_scope: true, gate: true, state, is_current: true })
      expect(stage(model, 'workspace-scaffold')).toMatchObject({ in_scope: true, gate: false })
    },
  )

  it.each([
    ['awaiting_approval', true], ['skipped', false],
  ] as const)('keeps a suffixless known %s row selected=%s when the grid says nothing about it', (state, selected) => {
    const inputs = withRow(planned(), stageRow('requirements-analysis', state, { suffix: null }))
    const grids: Record<string, string>[] = [{}, { 'user-stories': 'EXECUTE' }, { 'requirements-analysis': 'MAYBE' }]
    for (const scope_grid of grids) {
      expect(stage(buildMap({ ...inputs, scope_grid }), 'requirements-analysis'))
        .toMatchObject({ in_scope: selected, gate: selected, state })
    }
  })

  it('excludes a suffixless row the grid skips, a suffixless row the graph does not know, and a missing row', () => {
    const suffixless = withRow(planned(), stageRow('requirements-analysis', 'not_started', { suffix: null }))
    const variants: MapInputs[] = [
      { ...suffixless, scope_grid: { 'requirements-analysis': 'SKIP' } },
      { ...suffixless, nodes: GRAPH.filter((node) => node.slug !== 'requirements-analysis') },
      { ...suffixless, rows: suffixless.rows.filter((row) => row.slug !== 'requirements-analysis') },
    ]
    for (const inputs of variants) {
      expect(stage(buildMap(inputs), 'requirements-analysis')).toMatchObject({ in_scope: false, gate: false, state: 'excluded' })
    }
  })

  it('shows the union: a graph stage the state file lacks, and a row the graph does not know', () => {
    const rows = [
      ...GRAPH.filter((node) => node.slug !== 'deployment-execution').map((node) => stageRow(node.slug)),
      stageRow('application-design', 'not_started', { phase: 'inception' }),
      stageRow('2fa-setup', 'not_started', { phase: 'construction', suffix: 'SKIP' }),
    ]
    const model = buildMap(mapInputs({ nodes: GRAPH, rows }))
    expect(stage(model, 'deployment-execution')).toMatchObject({ state: 'excluded', in_scope: false, gate: false })
    expect(stage(model, 'application-design')).toMatchObject({
      number: '', name: 'Application Design', phase: 'inception', in_scope: true, gate: true, execution: 'ALWAYS',
      mode: '', agent: '', reviewer: null, depends_on: [], per_unit: false,
    })
    // Python's `str.title()` named these, quirks included.
    expect(stage(model, '2fa-setup')).toMatchObject({ name: '2Fa Setup', state: 'excluded' })
    // Rows the graph does not know come after the graph's own stages of their phase.
    expect(model.phases.find((phase) => phase.phase === 'inception')!.stages.map((candidate) => candidate.slug))
      .toEqual(['requirements-analysis', 'user-stories', 'application-design'])
    expect(model.counts.stages_known).toBe(10)
    expect(model.stage_count).toBe(8)
  })

  it('expands a per-unit stage into unit sub-rows whose state is presentational', () => {
    const plan = artifactMeta({ artifact_id: 'a1', name: 'code-generation-plan.md', stage: 'code-generation', unit: 'ledger-export' })
    const code = artifactMeta({ artifact_id: 'a2', name: 'code.md', stage: 'code-generation', unit: 'refunds' })
    const shared = artifactMeta({ artifact_id: 'a3', name: 'summary.md', stage: 'code-generation', unit: null })
    const results = artifactMeta({ artifact_id: 'a4', name: 'build-test-results.md', stage: 'build-and-test' })
    const model = buildMap(planned({ 'code-generation': 'in_progress' }, {
      units: ['ledger-export', 'refunds', 'audit-log'],
      active_unit: 'ledger-export',
      artifacts: [plan, code, shared, results],
    }))
    expect(stage(model, 'code-generation')).toMatchObject({
      per_unit: true,
      artifacts: [shared],
      units: [
        { unit: 'ledger-export', state: 'in_progress', artifacts: [plan] },   // the Active Unit mirrors its stage
        { unit: 'refunds', state: 'completed', artifacts: [code] },          // its directory holds a file
        { unit: 'audit-log', state: 'not_started', artifacts: [] },
      ],
    })
    // a once-per-workflow construction stage has no unit sub-rows
    expect(stage(model, 'build-and-test')).toMatchObject({ per_unit: false, units: [], artifacts: [results] })
    expect(model.units).toEqual(['ledger-export', 'refunds', 'audit-log'])
  })

  it('measures the latest attempt: to the end when it ended, to the server\'s now while in flight', () => {
    const model = buildMap(planned(
      { 'requirements-analysis': 'awaiting_approval', 'workspace-scaffold': 'completed', 'user-stories': 'in_progress' },
      {
        now: MAP_NOW + 0.9,   // a real clock has a fraction; whole seconds are what is shown
        attempts: {
          'requirements-analysis': { started: MAP_NOW - 3600, ended: null },
          'market-research': { started: MAP_NOW - 7200, ended: MAP_NOW - 6600 },
          'workspace-scaffold': { started: MAP_NOW - 9000, ended: null },
          'user-stories': { started: MAP_NOW + 60, ended: null },
        },
      },
    ))
    expect(stage(model, 'requirements-analysis').elapsed_secs).toBe(3600)
    expect(stage(model, 'market-research').elapsed_secs).toBe(600)    // ended: its state does not matter
    expect(stage(model, 'workspace-scaffold').elapsed_secs).toBeNull()  // never ended, not in flight
    expect(stage(model, 'user-stories').elapsed_secs).toBe(0)         // a start after now is not negative
    expect(stage(model, 'code-generation').elapsed_secs).toBeNull()   // never started
  })

  it('reads each lane\'s status from the state file, case and padding aside', () => {
    const model = buildMap(planned({}, {
      phases: [['INITIALIZATION', ' Verified '], ['Ideation', 'Skipped'], ['inception', 'Active'], ['Inception', 'Pending']],
    }))
    expect(Object.fromEntries(model.phases.map((phase) => [phase.phase, phase.status]))).toEqual({
      initialization: 'Verified', ideation: 'Skipped', inception: 'Pending', construction: 'Unknown', operation: 'Unknown',
    })
  })

  it('puts the rows of a phase the lifecycle does not name into one trailing unknown lane', () => {
    const model = buildMap(mapInputs({
      nodes: [SCAFFOLD],
      rows: [
        stageRow('workspace-scaffold', 'completed'),
        stageRow('ops-handover', 'completed', { phase: 'ops' }),
        stageRow('mystery', 'skipped', { phase: null, suffix: 'SKIP' }),
      ],
      skip_reasons: { mystery: 'unplanned' },
    }))
    expect(model.phases.map((phase) => phase.phase)).toEqual(['initialization', 'unknown'])
    const lane = model.phases[1]!
    expect(lane).toMatchObject({ status: 'Unknown', counts: { total: 2, in_scope: 1, done: 0, skipped: 0 } })
    expect(lane.stages.map((candidate) => [candidate.slug, candidate.phase])).toEqual([['ops-handover', 'ops'], ['mystery', 'unknown']])
  })

  it('renders an unreadable record as an empty but valid map', () => {
    expect(buildMap(mapInputs({ graph_version: null, status: null }))).toStrictEqual({
      intent_key: 'default~250901-guest',
      phases: [],
      counts: { stages_known: 0, stages_selected: 0, gates: 0 },
      units: [],
      graph_version: null,
      stage_count: 0,
    })
  })

  it('still renders the state rows without an engine, never inventing a number', () => {
    const model = buildMap(mapInputs({
      graph_version: null,
      units: ['ledger-export'],
      rows: GRAPH.map((node) => stageRow(node.slug, node === REQUIREMENTS ? 'awaiting_approval' : 'not_started', { phase: node.phase })),
    }))
    expect(all(model)).toHaveLength(8)
    expect(stage(model, 'requirements-analysis')).toMatchObject({
      number: '', name: 'Requirements Analysis', state: 'awaiting_approval', in_scope: true, gate: true,
    })
    // without a graph, the per-unit stages are known by slug
    expect(stage(model, 'code-generation')).toMatchObject({ per_unit: true, units: [{ unit: 'ledger-export', state: 'not_started', artifacts: [] }] })
    expect(stage(model, 'build-and-test')).toMatchObject({ per_unit: false, units: [] })
    expect(model.stage_count).toBe(8)
    expect(model.graph_version).toBeNull()
  })

  it.each(['Completed', ' completed '])('keeps the last stage of a %s workflow but no execution cursor', (status) => {
    const states = Object.fromEntries(GRAPH.map((node) => [node.slug, 'completed' as StageState]))
    const model = buildMap(planned({ ...states, 'deployment-execution': 'skipped' }, {
      status,
      current_stage: 'deployment-execution',
      directive_stage: 'deployment-execution',
    }))
    expect(stage(model, 'deployment-execution').state).toBe('skipped')
    expect(all(model).some((candidate) => candidate.is_current || candidate.is_directive)).toBe(false)
    expect(model.phases.reduce((done, phase) => done + phase.counts.done, 0)).toBe(8)
  })

  it('keys the state rows by slug: the last row wins, in the first one\'s place', () => {
    const model = buildMap(mapInputs({
      rows: [stageRow('user-stories'), stageRow('user-stories-extra'), stageRow('user-stories', 'completed')],
    }))
    expect(all(model).map((candidate) => [candidate.slug, candidate.state])).toEqual([
      ['user-stories', 'completed'], ['user-stories-extra', 'not_started'],
    ])
    expect(model.stage_count).toBe(2)
  })

  it('never reads a slug as a member of Object', () => {
    const node = mapNode({ slug: 'constructor', number: '9.1', phase: 'operation' })
    const model = buildMap(mapInputs({ nodes: [node], rows: [stageRow('constructor', 'in_progress', { suffix: null })] }))
    expect(stage(model, 'constructor')).toMatchObject({ in_scope: true, skipped_reason: null, elapsed_secs: null, dependents: [] })
  })
})
