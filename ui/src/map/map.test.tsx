/**
 * The map's promises, one test each.
 *
 * These are the claims the rest of the app cannot check for itself: that the five phases appear in
 * lifecycle order with every stage keeping its position, that density removes paint and never meaning,
 * that edges are drawn only for a selection and only in `dependencies`, that the inspector shows the
 * evidence and the ONE operation the stage permits, that a narrow viewport gets accordions rather than
 * a shrunken canvas, and that the swimlanes have a table alternative.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { setApiRoutes, navigations } from '../test/stubs/app-sdk'
import { I18nProvider } from '../i18n'
import { useStudioApi } from '../lib/api'
import { useStudioRoute } from '../lib/route'
import type { ActionCard, ArtifactMeta, MapPhase, MapStage, MapUnit } from '../lib/types'
import { MapPage } from './MapView'

const BASE = '/api/apps/aidlc-studio'
const REPO = 'r_1'
const INTENT = 'default~250901-guest-checkout'
const INTENT_PATH = `${BASE}/repos/${REPO}/intents/${INTENT}`

// --------------------------------------------------------------------------- //
// fixtures — slugs, numbers and phases are the real 2.6.2 graph's
// --------------------------------------------------------------------------- //

function artifact(name: string, over: Partial<ArtifactMeta> = {}): ArtifactMeta {
  return {
    artifact_id: `a_${name}`,
    relpath: `aidlc/spaces/default/intents/250901-guest-checkout/inception/${name}`,
    name,
    stage: 'requirements-analysis',
    phase: 'inception',
    unit: null,
    size: 4096,
    mtime: '2026-09-01T10:00:00Z',
    sha256: 'deadbeef',
    kind: 'artifact',
    renderable: true,
    ...over,
  }
}

function stage(over: Partial<MapStage> & Pick<MapStage, 'slug' | 'number' | 'phase'>): MapStage {
  return {
    name: over.slug.replace(/-/g, ' '),
    state: 'not_started',
    execution: 'ALWAYS',
    in_scope: true,
    mode: 'inline',
    agent: 'orchestrator',
    reviewer: null,
    review_class: null,
    gate: true,
    per_unit: false,
    summary_confirmation: null,
    consumes: [],
    produces: [],
    depends_on: [],
    dependents: [],
    elapsed_secs: null,
    artifacts: [],
    skipped_reason: null,
    units: [],
    is_current: false,
    is_directive: false,
    ...over,
  }
}

function unit(name: string, over: Partial<MapUnit> = {}): MapUnit {
  return { unit: name, state: 'not_started', artifacts: [], ...over }
}

function phase(name: string, stages: MapStage[], over: Partial<MapPhase> = {}): MapPhase {
  return {
    phase: name,
    status: 'Complete',
    stages,
    counts: {
      total: stages.length,
      in_scope: stages.filter((s) => s.in_scope).length,
      done: stages.filter((s) => s.state === 'completed').length,
      skipped: stages.filter((s) => s.state === 'skipped' || s.skipped_reason).length,
    },
    ...over,
  }
}

const REQUIREMENTS = stage({
  slug: 'requirements-analysis',
  number: '2.3',
  phase: 'inception',
  state: 'awaiting_approval',
  agent: 'aidlc-product-agent',
  reviewer: 'aidlc-product-lead-agent',
  review_class: 'advisory',
  elapsed_secs: 5400,
  is_current: true,
  is_directive: true,
  summary_confirmation: 'required',
  depends_on: ['approval-handoff', 'reverse-engineering'],
  dependents: ['user-stories', 'functional-design'],
  consumes: ['intent-statement', 'scope-document'],
  produces: ['requirements', 'requirements-analysis-questions'],
  artifacts: [artifact('requirements.md'), artifact('requirements-analysis-questions.md', { kind: 'questions' })],
})

const CODE_GENERATION = stage({
  slug: 'code-generation',
  number: '3.5',
  phase: 'construction',
  per_unit: true,
  agent: 'aidlc-developer-agent',
  units: [
    unit('unit-1-guest-cart', { state: 'in_progress' }),
    unit('unit-2-payment', { artifacts: [artifact('code.md', { stage: 'code-generation', unit: 'unit-2-payment' })] }),
  ],
})

function mapModel() {
  return {
    intent_key: INTENT,
    phases: [
      // Deliberately out of lifecycle order: the view must sort them back (FR-MAP-001).
      phase('construction', [CODE_GENERATION, stage({ slug: 'build-and-test', number: '3.6', phase: 'construction' })]),
      phase('initialization', [stage({ slug: 'workspace-scaffold', number: '0.1', phase: 'initialization', state: 'completed', gate: false })]),
      phase('ideation', [
        stage({ slug: 'approval-handoff', number: '1.7', phase: 'ideation', state: 'completed' }),
        stage({
          slug: 'market-research',
          number: '1.2',
          phase: 'ideation',
          state: 'skipped',
          in_scope: false,
          execution: 'CONDITIONAL',
          skipped_reason: 'Scope preset "feature" excludes market research.',
        }),
      ]),
      phase('inception', [
        stage({ slug: 'reverse-engineering', number: '2.1', phase: 'inception', state: 'excluded', in_scope: false, execution: 'CONDITIONAL' }),
        REQUIREMENTS,
        stage({ slug: 'user-stories', number: '2.4', phase: 'inception' }),
      ]),
      phase('operation', [stage({ slug: 'deployment-pipeline', number: '4.1', phase: 'operation' })]),
    ],
    counts: { stages_known: 9, stages_selected: 7, gates: 8 },
    units: ['unit-1-guest-cart', 'unit-2-payment'],
    graph_version: '2.6.2',
    stage_count: 9,
  }
}

function intentDetail(over: Record<string, unknown> = {}) {
  return {
    repo_id: REPO,
    repo_label: 'checkout-web',
    slug: '250901-guest-checkout',
    intent_key: INTENT,
    archived: false,
    paused: false,
    unstable: false,
    audit_tail: [
      {
        shard: 'host-abcdef',
        pos: 12,
        timestamp: '2026-09-01T09:58:00Z',
        event: 'STAGE_AWAITING_APPROVAL',
        fields: { Timestamp: '2026-09-01T09:58:00Z', Event: 'STAGE_AWAITING_APPROVAL', Stage: 'requirements-analysis' },
        raw: '',
      },
      {
        shard: 'host-abcdef',
        pos: 13,
        timestamp: '2026-09-01T09:59:00Z',
        event: 'ARTIFACT_CREATED',
        fields: { Event: 'ARTIFACT_CREATED', Context: 'inception > user-stories > stories.md' },
        raw: '',
      },
    ],
    ...over,
  }
}

function reviewResponse(over: Record<string, unknown> = {}) {
  return {
    stage: 'requirements-analysis',
    verdict: 'CHANGES_REQUESTED',
    findings: [
      { level: 'blocker', title: 'Acceptance criteria for guest refunds are missing', quote: null, anchor: null, reviewer: 'aidlc-product-lead-agent', iteration: 1 },
      { level: 'advisory', title: 'Consider naming the payment retry budget', quote: null, anchor: null, reviewer: null, iteration: 1 },
    ],
    receipts: [],
    review_class: 'advisory',
    reviewer: 'aidlc-product-lead-agent',
    revisions: 2,
    ...over,
  }
}

function gateCard(over: Record<string, unknown> = {}): ActionCard {
  return {
    action_id: 'act_gate_1',
    type: 'gate',
    queue_type: 'gate',
    status: 'Queued',
    intent: { intent_dir: '250901-guest-checkout', intent_key: INTENT, slug: '250901-guest-checkout', uuid: null, title: null },
    stage: { slug: 'requirements-analysis', number: '2.3', name: null, phase: 'inception', unit: null },
    ...over,
  } as unknown as ActionCard
}

function installRoutes(over: Record<string, unknown> = {}) {
  setApiRoutes({
    [`GET ${INTENT_PATH}/map`]: () => ({ map: over['map'] ?? mapModel() }),
    [`GET ${INTENT_PATH}`]: () => ({ intent: over['intent'] ?? intentDetail() }),
    [`GET ${INTENT_PATH}/review`]: () => over['review'] ?? reviewResponse(),
  })
}

// --------------------------------------------------------------------------- //
// harness
// --------------------------------------------------------------------------- //

function Harness({ cards }: { cards: ActionCard[] }) {
  const api = useStudioApi()
  const [route, go] = useStudioRoute()
  return <MapPage api={api} route={route} go={go} cards={cards} />
}

/**
 * A plain `matchMedia`, not a mock: vitest's `restoreMocks` empties a `vi.fn()` between tests, and the
 * accordion switch reads this on every render.
 */
function setViewport(narrow: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      matches: narrow && query.includes('900px'),
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

function mount({ search = `?view=map&repo=${REPO}&intent=${INTENT}`, cards = [] as ActionCard[] } = {}) {
  window.history.replaceState(null, '', `/apps/aidlc-studio${search}`)
  return render(
    <I18nProvider>
      <Harness cards={cards} />
    </I18nProvider>,
  )
}

beforeEach(() => {
  setViewport(false)
  navigations.length = 0
})

afterEach(() => {
  window.history.replaceState(null, '', '/apps/aidlc-studio')
})

// --------------------------------------------------------------------------- //
// tests
// --------------------------------------------------------------------------- //

describe('the workflow map', () => {
  it('draws the five phases in lifecycle order and keeps every stage in position', async () => {
    installRoutes()
    mount()

    const canvas = await screen.findByRole('list', { name: 'Workflow map swimlanes' })
    const lanes = [...canvas.querySelectorAll('.studio-lane')].map((lane) => lane.getAttribute('data-phase'))
    expect(lanes).toEqual(['initialization', 'ideation', 'inception', 'construction', 'operation'])

    // Skipped and excluded stages keep their slot and their words (FR-MAP-003).
    const skipped = screen.getByRole('button', { name: /market-research/ })
    expect(skipped).toHaveAttribute('data-state', 'skipped')
    expect(skipped).toHaveAccessibleName(/Skipped/)
    expect(skipped).toHaveAccessibleName(/Scope preset "feature" excludes market research/)
    expect(screen.getByRole('button', { name: /reverse-engineering/ })).toHaveAttribute('data-state', 'excluded')
    expect(screen.getByRole('button', { name: /reverse-engineering/ })).toHaveAccessibleName(/Not selected/)
  })

  it('names state, agent, Gate, review, elapsed, files and the execution marker even in Overview', async () => {
    installRoutes()
    mount()

    const card = await screen.findByRole('button', { name: /requirements-analysis/ })
    // Overview paints only the state and the Gate…
    expect(card.querySelectorAll('.studio-chip')).toHaveLength(3)
    // …but the accessible name still carries every fact of FR-MAP-002.
    const name = card.getAttribute('aria-label') ?? ''
    for (const fact of [
      'Stage 2.3 requirements-analysis',
      'Inception phase',
      'Awaiting your approval',
      'checkout-web / 250901-guest-checkout',
      'Executing now',
      'Gate',
      'Agent aidlc-product-agent',
      'Review advisory',
      'Elapsed',
      '2 files',
    ]) {
      expect(name).toContain(fact)
    }

    await userEvent.click(screen.getByRole('button', { name: 'Detailed' }))
    expect(screen.getByRole('button', { name: /requirements-analysis/ }).textContent).toContain('aidlc-product-agent')
  })

  it('draws no edge by default and, in Dependencies, only the selected stage\'s', async () => {
    installRoutes()
    const { container } = mount({ search: `?view=map&repo=${REPO}&intent=${INTENT}&stage=requirements-analysis` })
    await screen.findByRole('list', { name: 'Workflow map swimlanes' })

    // FR-MAP-004: the default view draws nothing, even with a selection…
    expect(container.querySelectorAll('.studio-map-edge')).toHaveLength(0)
    // …though the relationship tags are on the cards in every density (visual spec §6.1).
    expect(container.querySelector('.studio-stage[data-stage="approval-handoff"]')).toHaveAttribute('data-relation', 'upstream')
    expect(container.querySelector('.studio-stage[data-stage="user-stories"]')).toHaveAttribute('data-relation', 'downstream')

    await userEvent.click(screen.getByRole('button', { name: 'Dependencies' }))
    await waitFor(() => expect(container.querySelectorAll('.studio-map-edge').length).toBeGreaterThan(0))
    // One edge per related stage that is actually on the canvas: 2 upstream + 2 downstream.
    const edges = [...container.querySelectorAll('.studio-map-edge')].map((edge) => edge.getAttribute('data-to'))
    expect(new Set(edges)).toEqual(new Set(['approval-handoff', 'reverse-engineering', 'user-stories', 'functional-design'].filter((slug) => container.querySelector(`.studio-stage[data-stage="${slug}"]`))))
    expect(container.querySelector('.studio-map-edges')).toHaveAttribute('aria-hidden')
  })

  it('opens an inspector with files, review, audit and the one operation the stage permits', async () => {
    installRoutes()
    mount({ cards: [gateCard()] })

    await userEvent.click(await screen.findByRole('button', { name: /requirements-analysis/ }))
    const inspector = screen.getByRole('complementary', { name: 'Stage inspector' })

    // Relationships, as words and as controls.
    expect(within(inspector).getByText('Upstream')).toBeInTheDocument()
    expect(within(inspector).getByRole('button', { name: 'reverse-engineering' })).toBeInTheDocument()
    expect(within(inspector).getByText('requirements-analysis-questions')).toBeInTheDocument()

    // Files: the projection's metadata, never contents.
    expect(within(inspector).getByRole('button', { name: 'Open requirements.md' })).toBeInTheDocument()
    expect(within(inspector).getByText('Studio reads these files. It never writes them.')).toBeInTheDocument()

    // Review: the configured contract plus the record's verdict, because it IS this stage's.
    await waitFor(() =>
      expect(within(inspector).getByText('Configured review: advisory by aidlc-product-lead-agent.')).toBeInTheDocument(),
    )
    expect(within(inspector).getByText(/CHANGES_REQUESTED/)).toBeInTheDocument()
    expect(within(inspector).getByText(/Blocker 1/)).toBeInTheDocument()
    expect(within(inspector).getByText('Acceptance criteria for guest refunds are missing')).toBeInTheDocument()

    // Audit: this stage's events only. The `user-stories` artifact row must not appear here.
    expect(within(inspector).getByText('STAGE_AWAITING_APPROVAL')).toBeInTheDocument()
    expect(within(inspector).queryByText('ARTIFACT_CREATED')).not.toBeInTheDocument()

    // The one operation: a navigation into the Action Center, never a dispatch from here.
    await userEvent.click(within(inspector).getByRole('button', { name: 'Open the Approval gate in Action Center' }))
    expect(navigations.at(-1)).toContain('action=act_gate_1')
    expect(navigations.at(-1)).toContain('view=actions')
  })

  it('refuses an operation, with the reason, when the stage or the intent does not permit one', async () => {
    installRoutes()
    mount({ cards: [gateCard()] })

    // A stage with no card of its own: the reason is its position, not a missing feature.
    await userEvent.click(await screen.findByRole('button', { name: /user-stories/ }))
    const inspector = screen.getByRole('complementary', { name: 'Stage inspector' })
    expect(within(inspector).getByRole('button', { name: /No eligible operation/ })).toBeDisabled()
    expect(within(inspector).getByText(/ahead of the cursor/)).toBeInTheDocument()

    // A skipped stage says so instead.
    await userEvent.click(screen.getByRole('button', { name: /market-research/ }))
    expect(within(inspector).getByText('This stage is not executing in this plan.')).toBeInTheDocument()
    expect(within(inspector).getByText(/Scope preset "feature" excludes market research/)).toBeInTheDocument()
  })

  it('lets a paused intent outrank the stage: the card exists but no operation is offered', async () => {
    installRoutes({ intent: intentDetail({ paused: true }) })
    mount({ cards: [gateCard()] })

    await userEvent.click(await screen.findByRole('button', { name: /requirements-analysis/ }))
    const inspector = screen.getByRole('complementary', { name: 'Stage inspector' })
    expect(within(inspector).queryByRole('button', { name: /Open the Approval gate/ })).not.toBeInTheDocument()
    expect(within(inspector).getByText('The intent is paused, so Studio dispatches nothing for it.')).toBeInTheDocument()
  })

  it('expands Construction per-unit stages into unit sub-lanes and selects a unit', async () => {
    installRoutes()
    mount()

    await screen.findByRole('list', { name: 'Workflow map swimlanes' })
    expect(screen.queryByRole('button', { name: /Unit unit-1-guest-cart/ })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Expand unit sub-lanes' }))
    const unitCard = screen.getByRole('button', { name: /Unit unit-1-guest-cart/ })
    expect(unitCard).toHaveAccessibleName(/code-generation/)

    await userEvent.click(unitCard)
    expect(window.location.search).toContain('unit=unit-1-guest-cart')
    const inspector = screen.getByRole('complementary', { name: 'Stage inspector' })
    expect(within(inspector).getByText('unit-1-guest-cart')).toBeInTheDocument()
    // The presentational-state warning travels with the unit lanes.
    expect(screen.getByText(/Per-unit state is presentational/)).toBeInTheDocument()
  })

  it('becomes phase accordions below 900px, with the inspector inline instead of hidden', async () => {
    setViewport(true)
    installRoutes()
    mount({ search: `?view=map&repo=${REPO}&intent=${INTENT}&stage=requirements-analysis` })

    await waitFor(() => expect(screen.getByRole('button', { name: /^Inception/ })).toBeInTheDocument())
    expect(screen.queryByRole('list', { name: 'Workflow map swimlanes' })).not.toBeInTheDocument()

    // The phase holding the selection is open, and the inspector rides inside it.
    const toggle = screen.getByRole('button', { name: /^Inception/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('complementary', { name: 'Stage inspector' })).toBeInTheDocument()

    // A phase the user closes stays closed.
    await userEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })

  it('offers the swimlanes as a table, one row per stage, with a caption', async () => {
    installRoutes()
    mount()

    await userEvent.click(await screen.findByRole('button', { name: 'Show as table' }))
    const table = screen.getByRole('table')
    expect(table).toHaveAccessibleName(/Every stage of 250901-guest-checkout/)
    expect(within(table).getAllByRole('row')).toHaveLength(1 + 9)
    expect(within(table).getByRole('columnheader', { name: 'Elapsed' })).toBeInTheDocument()

    await userEvent.click(within(table).getByRole('button', { name: 'requirements-analysis' }))
    expect(window.location.search).toContain('stage=requirements-analysis')
  })

  it('moves focus between cards with the arrow keys and clears the selection with Escape', async () => {
    installRoutes()
    mount({ search: `?view=map&repo=${REPO}&intent=${INTENT}&stage=requirements-analysis` })

    const first = await screen.findByRole('button', { name: /workspace-scaffold/ })
    first.focus()
    await userEvent.keyboard('{ArrowDown}')
    expect(document.activeElement).toHaveAccessibleName(/approval-handoff/)
    await userEvent.keyboard('{ArrowRight}')
    expect(document.activeElement).toHaveAccessibleName(/market-research/)

    await userEvent.keyboard('{Escape}')
    expect(window.location.search).not.toContain('stage=')
  })

  it('says so, assertively, when the map cannot be read — and offers a retry', async () => {
    setApiRoutes({
      [`GET ${INTENT_PATH}/map`]: () => {
        throw new Error(`API 409: ${JSON.stringify({ error: 'read was not stable', code: 'unstable_read', details: {} })}`)
      },
      [`GET ${INTENT_PATH}`]: () => ({ intent: intentDetail() }),
    })
    mount()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The map could not be read')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('warns assertively while the record and Studio disagree, and links to the action', async () => {
    installRoutes()
    mount({ cards: [gateCard({ action_id: 'act_rec_1', type: 'recovery', queue_type: 'recovery' })] })

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/Recovery is required/)
    await userEvent.click(within(alert).getByRole('button', { name: 'Open the action' }))
    expect(navigations.at(-1)).toContain('action=act_rec_1')
  })

  it('explains the next useful action instead of drawing an empty canvas', async () => {
    installRoutes()
    mount({ search: '?view=map' })

    expect(await screen.findByText('No intent is selected')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Choose an intent/ }))
    expect(navigations.at(-1)).toContain('view=intents')
  })
})
