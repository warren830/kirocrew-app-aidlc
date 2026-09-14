import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import { I18nProvider } from '../i18n'
import { buildRoute, EMPTY_ROUTE, parseRoute, type StudioRoute } from '../lib/route'
import type { ArtifactMeta, ArtifactResponse, MapStage } from '../lib/types'
import { StudioApp } from '../shell/StudioApp'
import { API_BASE, actionCard, artifactMeta, shellRoutes } from '../test/fixtures'
import { apiCalls, setApiRoutes, StubApiError, type RouteHandler } from '../test/stubs/app-sdk'

const REPO = 'r_1'
const INTENT = 'default~260910-precision-fix'
const ROOT = `${API_BASE}/repos/${REPO}/intents/${INTENT}`
const UNIT = 'unit-1-calculator'
const plan = artifactMeta({
  artifact_id: 'art_code_plan',
  name: 'code-generation-plan.md',
  relpath: `aidlc/spaces/default/intents/260910-precision-fix/construction/code-generation/code-generation-plan.md`,
  stage: 'code-generation',
  phase: 'construction',
})
const unitPlan = { ...plan, artifact_id: 'art_unit_plan', unit: UNIT, relpath: `construction/${UNIT}/${plan.name}` }
const unitCode = { ...unitPlan, artifact_id: 'art_unit_code', name: 'code.md', relpath: `construction/${UNIT}/code.md` }
const otherUnit = { ...unitPlan, artifact_id: 'art_other_unit', unit: 'unit-2-reports', relpath: 'construction/unit-2-reports/code-generation-plan.md' }
const requirements = artifactMeta({
  artifact_id: 'art_requirements', name: 'requirements.md', stage: 'requirements-analysis',
  relpath: 'inception/requirements-analysis/requirements.md',
})
const oldGate = actionCard({
  action_id: 'a_0e7590247b51ce04',
  status: 'StateChanged',
  intent: { ...actionCard().intent, intent_key: INTENT },
  stage: { slug: 'requirements-analysis', phase: 'inception', number: '2.3', unit: null, name: null },
  headline: { key: 'action.gate.headline', params: { stage: 'requirements-analysis' } },
  evidence: { ...actionCard().evidence, artifacts: [requirements] },
})
const files = [requirements, plan, unitPlan, unitCode, otherUnit]

function body(meta: ArtifactMeta): ArtifactResponse {
  return {
    artifact: meta, content: `# Contents of ${meta.artifact_id}`, encoding: 'utf-8',
    truncated: false, toc: [], review: null, prior: { available: false, source: null, diff: null },
  }
}

function stage(slug: string, artifacts: ArtifactMeta[], over: Partial<MapStage> = {}): MapStage {
  return {
    slug, number: '3.5', name: slug, phase: 'construction', state: 'in_progress',
    execution: 'ALWAYS', in_scope: true, mode: 'inline', agent: 'aidlc-developer-agent',
    reviewer: null, review_class: null, gate: false, per_unit: true, summary_confirmation: null,
    consumes: [], produces: [], depends_on: [], dependents: [], elapsed_secs: null, artifacts,
    skipped_reason: null, units: [], is_current: true, is_directive: true, ...over,
  }
}

function install(over: Record<string, RouteHandler> = {}) {
  const stages = [
    stage('requirements-analysis', [requirements], { state: 'completed', is_current: false, is_directive: false }),
    stage('code-generation', [plan], {
      units: [
        { unit: UNIT, state: 'in_progress', artifacts: [unitPlan, unitCode] },
        { unit: otherUnit.unit, state: 'not_started', artifacts: [otherUnit] },
      ],
    }),
  ]
  const listing: RouteHandler = (_request, path) => {
    const query = new URL(path, 'http://localhost').searchParams
    const artifacts = files.filter((meta) =>
      (!query.has('stage') || meta.stage === query.get('stage')) &&
      (!query.has('unit') || meta.unit === query.get('unit')),
    )
    return { artifacts, truncated: false, count: artifacts.length }
  }
  setApiRoutes({
    [`GET ${ROOT}/artifacts*`]: listing,
    ...Object.fromEntries(files.map((meta) => [`GET ${ROOT}/artifacts/${meta.artifact_id}`, () => body(meta)])),
    [`GET ${ROOT}/map`]: () => ({
      map: {
        intent_key: INTENT, graph_version: '2.7.1', stage_count: 2, units: [UNIT, otherUnit.unit],
        counts: { stages_known: 2, stages_selected: 2, gates: 0 },
        phases: [{ phase: 'construction', status: 'InProgress', stages, counts: { total: 2, in_scope: 2, done: 1, skipped: 0 } }],
      },
    }),
    [`GET ${ROOT}`]: () => ({
      intent: { repo_id: REPO, repo_label: 'calculator', slug: INTENT, archived: false, paused: false, audit_tail: [] },
    }),
    [`GET ${ROOT}/review`]: () => ({ stage: 'code-generation', verdict: null, findings: [], receipts: [] }),
    ...shellRoutes([oldGate]),
    ...over,
  })
}

function mount(patch: Partial<StudioRoute> = {}) {
  window.history.replaceState(null, '', buildRoute({
    ...EMPTY_ROUTE, repo: REPO, intent: INTENT, stage: 'code-generation',
    tab: 'artifacts', artifact: plan.artifact_id, ...patch,
  }))
  return render(<I18nProvider><StudioApp /></I18nProvider>)
}

const route = () => parseRoute(window.location.search, window.location.hash)
const contents = (meta: ArtifactMeta) => `Contents of ${meta.artifact_id}`
const expectOnlyReads = () => expect(apiCalls.every((call) => call.method === 'GET')).toBe(true)

afterEach(() => window.history.replaceState(null, '', '/apps/aidlc-studio'))

describe('map artifact navigation', () => {
  it('opens the selected stage file after a requirements Gate, clearing the old action and anchor', async () => {
    install()
    mount({ view: 'map', stage: 'requirements-analysis', action: oldGate.action_id, artifact: '', anchor: 'h-old-requirements' })
    await userEvent.click(await screen.findByRole('button', { name: /calculator.*code-generation|code-generation.*calculator/ }))
    await userEvent.click(screen.getByRole('button', { name: `Open ${plan.name}` }))

    expect(route()).toMatchObject({
      view: 'actions', repo: REPO, intent: INTENT, stage: 'code-generation', unit: '',
      tab: 'artifacts', artifact: plan.artifact_id, action: '', anchor: '',
    })
    expect(await screen.findByTestId('markdown')).toHaveTextContent(contents(plan))
    expect(screen.queryByText(requirements.name)).toBeNull()
    expect(screen.queryByText(/first artifact is shown instead/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /approve/i })).toBeNull()
    expectOnlyReads()
  })

  it('restores a read-only artifact link without an action', async () => {
    install()
    mount()
    expect(await screen.findByTestId('markdown')).toHaveTextContent(contents(plan))
    expect(apiCalls.some((call) => call.path === `${API_BASE}/actions/${oldGate.action_id}`)).toBe(false)
    expect(apiCalls.some((call) => call.path === `${ROOT}/artifacts?stage=code-generation`)).toBe(true)
    expect(screen.queryByText(requirements.name)).toBeNull()
    expect(document.querySelector('.studio-actionbar')).toBeNull()
    // The reader must stay visible when the mobile shell has no action selection.
    expect(screen.getByTestId('markdown').closest('.studio-detail')).toBeNull()
    expectOnlyReads()
  })

  it.each(['art_unknown', requirements.artifact_id, otherUnit.artifact_id])(
    'rejects an unknown or out-of-scope artifact "%s" without fetching a substitute',
    async (artifact) => {
      install()
      mount({ artifact, unit: UNIT })
      expect(await screen.findByText('That artifact is not there.')).toBeInTheDocument()
      expect(screen.queryByTestId('markdown')).toBeNull()
      expect(apiCalls.filter((call) => call.path.startsWith(`${ROOT}/artifacts/`))).toEqual([])
      expectOnlyReads()
    },
  )

  it('keeps the unit and selected file through map navigation, file selection and return to the map', async () => {
    install()
    mount({ view: 'map', action: oldGate.action_id, artifact: '', unit: UNIT })
    await userEvent.click(await screen.findByRole('button', { name: `Open ${unitPlan.name}` }))
    expect(await screen.findByTestId('markdown')).toHaveTextContent(contents(unitPlan))
    expect(apiCalls.some((call) => call.path === `${ROOT}/artifacts?stage=code-generation&unit=${UNIT}`)).toBe(true)

    await userEvent.click(screen.getByRole('button', { name: /code\.md/ }))
    await waitFor(() => expect(screen.getByTestId('markdown')).toHaveTextContent(contents(unitCode)))
    expect(route()).toMatchObject({ repo: REPO, intent: INTENT, stage: 'code-generation', unit: UNIT, artifact: unitCode.artifact_id, action: '' })
    expect(screen.queryByText(otherUnit.unit)).toBeNull()

    await userEvent.click(within(screen.getByRole('region', { name: 'Files in this record' })).getByRole('button', { name: 'Workflow Map' }))
    const inspector = await screen.findByRole('complementary', { name: 'Stage inspector' })
    expect(within(inspector).getByText(UNIT)).toBeInTheDocument()
    expect(within(inspector).getByRole('button', { name: 'Open code.md' })).toBeInTheDocument()
    expect(route()).toMatchObject({ stage: 'code-generation', unit: UNIT, action: '', artifact: '', anchor: '' })
    expectOnlyReads()
  })

  it('keeps stage-level browsing separate from unit files returned by an unfiltered unit listing', async () => {
    install()
    mount({ artifact: unitPlan.artifact_id, unit: '' })
    expect(await screen.findByText('That artifact is not there.')).toBeInTheDocument()
    expect(screen.queryByTestId('markdown')).toBeNull()
    expect(apiCalls.filter((call) => call.path.startsWith(`${ROOT}/artifacts/`))).toEqual([])
  })

  it('does not display the previous unit while the new scoped listing is pending', async () => {
    let resolve!: (value: unknown) => void
    install({
      [`GET ${ROOT}/artifacts?stage=code-generation&unit=${otherUnit.unit}`]:
        () => new Promise((done) => { resolve = done }),
    })
    mount({ unit: UNIT, artifact: unitPlan.artifact_id })
    expect(await screen.findByTestId('markdown')).toHaveTextContent(contents(unitPlan))

    act(() => {
      window.history.pushState(null, '', buildRoute({ unit: otherUnit.unit, artifact: otherUnit.artifact_id }, route()))
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    expect(screen.queryByTestId('markdown')).toBeNull()
    await act(async () => resolve({ artifacts: [otherUnit], count: 1, truncated: false }))
    expect(await screen.findByTestId('markdown')).toHaveTextContent(contents(otherUnit))
  })

  it('reports listing failures in the standalone reader', async () => {
    install({
      [`GET ${ROOT}/artifacts?stage=code-generation`]: () => {
        throw new StubApiError(404, { code: 'artifact_not_found', error: 'missing', details: {} })
      },
    })
    mount()
    expect(await screen.findByText('That artifact is not there.')).toBeInTheDocument()
    expect(screen.queryByTestId('markdown')).toBeNull()
    expectOnlyReads()
  })

  it('does not claim a file is absent when the scoped listing is incomplete', async () => {
    install({
      [`GET ${ROOT}/artifacts?stage=code-generation`]: () => ({ artifacts: [plan], count: 1, truncated: true }),
    })
    mount({ artifact: 'art_beyond_listing_cap' })
    expect(await screen.findByText(/Studio stopped listing files at its cap/)).toBeInTheDocument()
    expect(screen.queryByText('That artifact is not there.')).toBeNull()
    expect(screen.queryByTestId('markdown')).toBeNull()
    expect(apiCalls.filter((call) => call.path.startsWith(`${ROOT}/artifacts/`))).toEqual([])
    expectOnlyReads()
  })

  it('reports a selected file disappearing after the listing without showing another file', async () => {
    install({
      [`GET ${ROOT}/artifacts/${unitPlan.artifact_id}`]: () => {
        throw new StubApiError(404, { code: 'artifact_not_found', error: 'missing', details: {} })
      },
    })
    mount({ unit: UNIT, artifact: unitPlan.artifact_id })
    expect(await screen.findByText('That artifact is not there.')).toBeInTheDocument()
    expect(screen.queryByTestId('markdown')).toBeNull()
    expect(apiCalls.filter((call) => call.path.startsWith(`${ROOT}/artifacts/`)).map((call) => call.path))
      .toEqual([`${ROOT}/artifacts/${unitPlan.artifact_id}`])
    expectOnlyReads()
  })

  it('still uses captured evidence when opening an action Artifacts tab without an explicit file link', async () => {
    install()
    mount({ action: oldGate.action_id, artifact: '', stage: 'requirements-analysis' })
    expect(await screen.findByTestId('markdown')).toHaveTextContent(contents(requirements))
    expect(document.querySelector('.studio-actionbar')).not.toBeNull()
    expect(apiCalls.some((call) => call.path.startsWith(`${ROOT}/artifacts?`))).toBe(false)
  })

  it('keeps the action, captured scope and decision buttons when selecting a second artifact with stale route scope', async () => {
    const second = {
      ...requirements,
      artifact_id: 'art_acceptance_criteria',
      name: 'acceptance-criteria.md',
      relpath: 'inception/requirements-analysis/acceptance-criteria.md',
    }
    const gate = actionCard({
      ...oldGate,
      status: 'Queued',
      evidence: { ...oldGate.evidence, artifacts: [requirements, second] },
    })
    install({
      ...shellRoutes([gate]),
      [`GET ${ROOT}/artifacts/${second.artifact_id}`]: () => body(second),
    })
    // The queue retains map selection in the route; the card's own scope governs its artifacts.
    mount({ action: gate.action_id, artifact: '', tab: 'decision', stage: 'code-generation', unit: UNIT })
    const detail = await screen.findByRole('region', { name: 'Decision detail' })
    await userEvent.click(await within(detail).findByRole('tab', { name: /Artifacts/ }))
    expect(await within(detail).findByTestId('markdown')).toHaveTextContent(contents(requirements))
    expect(within(detail).getByRole('button', { name: 'Approve' })).toBeEnabled()

    await userEvent.click(within(detail).getByRole('button', { name: /acceptance-criteria\.md/ }))
    expect(detail).toBeInTheDocument()
    await waitFor(() => expect(within(detail).getByTestId('markdown')).toHaveTextContent(contents(second)))
    expect(within(detail).getByRole('button', { name: 'Approve' })).toBeEnabled()
    expect(within(detail).getByRole('button', { name: 'Request changes' })).toBeInTheDocument()
    expect(within(detail).getByRole('navigation', { name: /Where this decision sits/ })).toHaveTextContent('requirements-analysis')
    expect(within(detail).getByRole('tab', { name: /Artifacts/ })).toHaveAttribute('aria-selected', 'true')
    expect(route()).toMatchObject({
      action: gate.action_id, artifact: second.artifact_id, tab: 'artifacts',
      stage: 'code-generation', unit: UNIT,
    })
    expect(apiCalls.some((call) => call.path.startsWith(`${ROOT}/artifacts?`))).toBe(false)
    expect(new Set(apiCalls.filter((call) => call.path.startsWith(`${ROOT}/artifacts/`)).map((call) => call.path)))
      .toEqual(new Set([`${ROOT}/artifacts/${requirements.artifact_id}`, `${ROOT}/artifacts/${second.artifact_id}`]))
    expect(screen.queryByText('That artifact is not there.')).toBeNull()
    await userEvent.click(within(detail).getByRole('tab', { name: 'Decision' }))
    expect(within(detail).getByRole('tab', { name: 'Decision' })).toHaveAttribute('aria-selected', 'true')
    expectOnlyReads()
  })
})
