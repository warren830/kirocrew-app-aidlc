/**
 * The wizard's contract with the user: four steps, a plan that is the server's, and a Create that creates
 * an intent and starts nothing.
 *
 * The digest test is the load-bearing one. `confirm_plan_digest` is computed in the browser and compared
 * byte-for-byte by the backend, so a canonicalisation that differs from Python's `json.dumps(sort_keys=
 * True, separators=(",", ":"))` by one escape would make every Create fail with `plan_invalid`. The
 * expected hex below was produced by that exact Python expression over the same payload.
 *
 * The Advisor block asserts the wizard's half of FR-NEW-006 (design §2.7): nothing is asked for until the
 * button is clicked, the proposal on screen is the engine's own diff over the proposed settings, nothing
 * enters the form until "Use this proposal", and afterwards every field is the human's to change or clear.
 */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider, makeI18n } from '../i18n'
import { apiCalls, setApiRoutes, StubApiError, type RouteHandler } from '../test/stubs/app-sdk'
import { EMPTY_ROUTE, type RoutePatch } from '../lib/route'
import type { AdvisorDraft, EffectivePlan, HealthResponse, PlanProposal, PlanRequest, PlanStage, ReposResponse } from '../lib/types'
import type { Resource } from '../lib/useResource'
import { EMPTY_WIZARD, canonicalJson, planDigest, sha256Hex, slugifyObjective, WizardView } from './WizardView'
import { StepWork } from './StepWork'

/**
 * The shell context, faked.
 *
 * The wizard reads `api`, `repos` and `health` from `useShellData()`. Mounting the whole shell instead
 * would pull in every other area's view through the router's glob, so one unrelated module with a load
 * order problem would fail this file — the wizard's own behaviour is what is under test here.
 */
const REPOS: ReposResponse = {
  repos: [
    {
      repo_id: 'r_1', label: 'checkout-web', canonical_path: '/work/checkout-web', availability: 'available',
      install: { status: 'installed', engine_version: '2.6.2', drift_count: 0 },
      counts: { intents: 0, in_flight: 0, open_actions: 0, blocking_findings: 0 },
      git: { available: true, branch: 'main', dirty: false, dirty_files: 0 },
    } as unknown as ReposResponse['repos'][number],
    {
      repo_id: 'r_2', label: 'checkout-api', canonical_path: '/work/checkout-api', availability: 'available',
      install: { status: 'installed', engine_version: '2.6.2', drift_count: 0 },
      counts: { intents: 0, in_flight: 0, open_actions: 0, blocking_findings: 0 },
      git: { available: true, branch: 'main', dirty: false, dirty_files: 0 },
    } as unknown as ReposResponse['repos'][number],
  ],
  totals: { repos: 2, unavailable: 0, open_actions: 0 },
}
const HEALTH = {
  status: 'healthy', issues: [],
  tools: { bun: { found: true, path: '/bin/bun', version: '1.1' }, git: { found: true, path: '/bin/git', version: '2.4' } },
} as unknown as HealthResponse

export const navigated: RoutePatch[] = []

function resource<T>(data: T | null): Resource<T> {
  return { data, error: null, loading: false, stale: false, refresh: async () => {}, refetch: async () => {} }
}

vi.mock('../shell/StudioApp', async () => {
  const { useStudioApi } = await import('../lib/api')
  return {
    useShellData: () => ({
      api: useStudioApi(),
      route: EMPTY_ROUTE,
      go: (patch: RoutePatch) => void navigated.push(patch),
      repos: resource(REPOS),
      health: resource(HEALTH),
      actions: resource(null),
      leases: resource(null),
      settings: resource(null),
      setQueueCount: () => {},
    }),
  }
})

const BASE = '/api/apps/aidlc-studio'

function stage(over: Partial<PlanStage> & { slug: string }): PlanStage {
  return {
    number: '1.1', name: 'a stage', phase: 'inception', execution: 'ALWAYS', in_grid: true, enabled: true,
    locked: false, lock_reason: null, gate: true, review_class: null, reviewer: null, per_unit: false,
    produces: [], consumes: [], depends_on: [], conditional_on: null, state: null, ...over,
  }
}

const PLAN_STAGES: PlanStage[] = [
  stage({ slug: 'scope-definition', number: '0.1', phase: 'initialization', gate: false, produces: ['scope-definition.md'] }),
  stage({ slug: 'nfr-requirements', number: '2.4', produces: ['nfr-requirements.md'] }),
  stage({ slug: 'code-generation', number: '4.3', phase: 'construction', per_unit: true, produces: ['units/'], locked: true, lock_reason: 'always' }),
]

/**
 * The engine's plan for `scope` with `overrides` applied. `extra.stages` swaps the stage set and `extra.meta`
 * overlays the scope's frontmatter, for tests about one scope's own values or one dependency between stages.
 */
function planFor(
  scope: string,
  overrides: Record<string, boolean>,
  extra: { stages?: PlanStage[]; meta?: Record<string, unknown> } = {},
): EffectivePlan {
  const stages = (extra.stages ?? PLAN_STAGES).map((entry) =>
    entry.slug in overrides && !entry.locked ? { ...entry, enabled: overrides[entry.slug] === true } : entry,
  )
  const enabled = stages.filter((entry) => entry.enabled)
  return {
    request: {
      space: 'default', scope, depth: 'Standard', test_strategy: null, review_cap: null,
      project_type: null, overrides, objective: null, label: null, context: null,
    },
    scope_meta: scope
      ? {
          name: scope, depth: 'Standard', test_strategy: null, review_cap: 'adversarial', skeleton: null,
          runner: null, keywords: [], description: 'the default for scoped product work', plugin: null,
          project_owned: false, ...extra.meta,
        }
      : null,
    stages,
    exact: {
      stages: enabled.length,
      gates: enabled.filter((entry) => entry.gate).length,
      artifacts: enabled.reduce((sum, entry) => sum + entry.produces.length, 0),
      review_intensity: { none: enabled.length, advisory: 0, adversarial: 0 },
      assumes_units: 1,
    },
    estimate: {
      turns: { low: 70, high: 110, unit: 'turns', source: 'rule_band', confidence: 'low' },
      active_secs: { low: 14400, high: 25200, unit: 'secs', source: 'rule_band', confidence: 'medium' },
      elapsed_secs: null,
      credits: null,
      credits_status: 'unavailable',
      source: 'rule_band',
      confidence: 'low',
      samples: 0,
      dominant: [],
      coverage_lost: [],
    },
    diff: stages
      .filter((entry) => entry.in_grid !== entry.enabled)
      .map((entry) => ({ slug: entry.slug, from_enabled: entry.in_grid, to_enabled: entry.enabled, reason: 'override' })),
    issues: scope
      ? []
      : [{ code: 'scope_unknown', slugs: [], message_key: 'plan.issue.scope_unknown', params: { scope: '', known: ['bugfix', 'feature'] } }],
    valid: Boolean(scope),
    products: enabled.flatMap((entry) => entry.produces),
    graph_stage_count: 33,
    engine_version: '2.6.2',
  }
}

const ADVISE = `${BASE}/repos/r_1/intents/plan/advise`
const PREVIEW = `${BASE}/repos/r_1/intents/plan/preview`
const DRAFT = `${BASE}/advisor/drafts/d_1`

/**
 * The Advisor's plan draft as the server hands it back (design §2.8).
 *
 * The kind is card-less: `action_id` is the `plan:<repo_id>` sentinel, `neutrality` stays null because there
 * is no intent to probe, and the picks arrive already resolved into a `PlanRequest` patch — `plan_proposal` —
 * so the wizard never reads an option letter. 'feature' is a scope the preview stub admits and
 * nfr-requirements is grid-on there, so turning it OFF is the one stage delta this harness can represent.
 */
function planDraft(status: AdvisorDraft['status']): AdvisorDraft {
  const ready = status === 'ready'
  return {
    draft_id: 'd_1',
    action_id: 'plan:r_1',
    kind: 'plan_draft',
    status,
    request: { action_id: 'plan:r_1', kind: 'plan_draft', question_index: null, locale: 'en-US', auto: false },
    result: ready
      ? {
          verdict: null,
          summary: 'A small change to an existing module.',
          suggested_answers: [
            { question_index: 1, answer: 'feature — the default for scoped product work', option_letters: ['B'] },
            { question_index: 2, answer: 'Minimal', option_letters: ['A'] },
            { question_index: 4, answer: 'advisory', option_letters: ['B'] },
          ],
          evidence: ['The objective names one flow of an existing checkout service.'],
          assumptions: ['No new service is introduced.'],
          alternatives: ['bugfix, if the missing flow is a defect rather than a capability'],
          confidence: 'medium',
          needs_your_decision: [],
          drafted_feedback: null,
        }
      : null,
    error: null,
    created_at: '2026-09-05T09:00:00Z',
    updated_at: '2026-09-05T09:00:00Z',
    expires_at: '2026-09-06T09:00:00Z',
    neutrality: null,
    plan_proposal: ready
      ? {
          scope: 'feature', depth: 'Minimal', test_strategy: null, review_cap: 'advisory',
          overrides: { 'nfr-requirements': false }, unresolved: [], base_scope: 'feature',
        }
      : null,
  }
}

/** The ready draft with another proposal in it: the server's complete map (§1.4), arriving as it would. */
function draftProposing(proposal: Partial<PlanProposal>): AdvisorDraft {
  const draft = planDraft('ready')
  return { ...draft, plan_proposal: { ...(draft.plan_proposal as PlanProposal), ...proposal } }
}

/** The preview stub over a custom stage set or frontmatter; tests about one engine verdict wrap it. */
const previewWith =
  (extra: (scope: string) => { stages?: PlanStage[]; meta?: Record<string, unknown> }): RouteHandler =>
  (body) => {
    const request = body as { scope: string; overrides: Record<string, boolean> }
    return { plan: planFor(request.scope, request.overrides ?? {}, extra(request.scope)) }
  }

/** The stub routes. A test that needs one route to misbehave passes an override for that key only. */
function installRoutes(over: Record<string, RouteHandler> = {}) {
  setApiRoutes({
    [`GET ${BASE}/repos/r_1/intents`]: () => ({ intents: [], spaces: ['default'], active_space: 'default' }),
    [`POST ${PREVIEW}`]: (body) => {
      const request = body as { scope: string; overrides: Record<string, boolean> }
      return { plan: planFor(request.scope, request.overrides ?? {}) }
    },
    [`POST ${BASE}/repos/r_1/intents`]: () => ({
      ok: true,
      transaction_id: 'tx_77',
      intent: {
        repo_id: 'r_1', space: 'default', intent_dir: '250905-guest-checkout',
        intent_key: 'default~250905-guest-checkout', uuid: null, slug: 'guest-checkout',
        transaction_id: 'tx_77', engine: null, verified: true, summary: null,
      },
    }),
    [`POST ${ADVISE}`]: () => ({ ok: true, draft: planDraft('queued') }),
    [`GET ${DRAFT}`]: () => ({ draft: planDraft('ready') }),
    ...over,
  })
}

const mount = () =>
  render(
    <I18nProvider>
      <WizardView route={EMPTY_ROUTE} go={(patch) => void navigated.push(patch)} />
    </I18nProvider>,
  )

const OBJECTIVE = 'Let signed-out shoppers complete a purchase'

/** Walk to the Preset step with a repository and an objective in place. */
async function toPreset() {
  await userEvent.selectOptions(await screen.findByLabelText('Repository'), 'r_1')
  await userEvent.type(screen.getByLabelText('Objective'), OBJECTIVE)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled())
  await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
}

// ---- the Advisor in the Preset step: literal English targets (design §2.2 / §2.5) ---- //

const ASK = 'Let the Advisor propose these settings'
const USE = 'Use this proposal'
const CLEAR = 'Clear the proposal'
const APPLIED =
  "Filled in from the Advisor's proposal. Nothing has been created — review and change anything before you press Create."
const BY_ADVISOR = "− nfr-requirements turned off by the Advisor's proposal"
const BY_YOU = '− nfr-requirements turned off by you'
const ASK_AGAIN = 'Ask again'
const DROPPED_ONE =
  "1 stage change the engine refused was left out; the refusal is shown in the proposal's stage changes above."
const STALE_OBJECTIVE =
  'The objective changed after this proposal was drafted. Ask again for one that reads the new objective.'
const STALE_SCOPE =
  'The scope changed after this proposal was drafted; its stage changes were computed against feature. Pick feature again or ask again.'

interface PreviewBody {
  scope: string
  depth: string | null
  test_strategy: string | null
  review_cap: string | null
  overrides?: Record<string, boolean>
}

const previewBodies = () => apiCalls.filter((call) => call.path === PREVIEW).map((call) => call.body as PreviewBody)
const advisePosts = () => apiCalls.filter((call) => call.method === 'POST' && call.path === ADVISE)
const draftPolls = () => apiCalls.filter((call) => call.method === 'GET' && call.path === DRAFT)

const scopeButton = (scope: string) => screen.getByRole('button', { name: new RegExp(`^${scope}`) })
/**
 * A depth button is named by its token and its description, and the test-strategy row reuses the same
 * tokens, so the description is the part of the label that identifies a depth button on its own.
 */
const DEPTH_LABEL: Record<string, RegExp> = {
  Minimal: /One pass per stage/,
  Standard: /Questions where the engine needs your input/,
  Comprehensive: /More questions and longer artifacts/,
}
const depthButton = (depth: string) => screen.getByRole('button', { name: DEPTH_LABEL[depth] })
const STRATEGY_LABEL: Record<string, RegExp> = {
  Minimal: /Smoke coverage only/,
  Standard: /Unit plus integration/,
  Comprehensive: /Adds performance validation/,
}
const strategyButton = (value: string) => screen.getByRole('button', { name: STRATEGY_LABEL[value] })
const CAP_LABEL: Record<string, RegExp> = {
  none: /No reviewer pass/,
  advisory: /Reviewer comments; they do not block/,
  adversarial: /Reviewer findings must be answered/,
}
const capButton = (cap: string) => screen.getByRole('button', { name: CAP_LABEL[cap] })

/** Pick a scope on the Preset step and wait until the wizard shows it pressed. */
async function pickScope(scope: string) {
  await userEvent.click(await screen.findByRole('button', { name: new RegExp(`^${scope}`) }))
  await waitFor(() => expect(scopeButton(scope)).toHaveAttribute('aria-pressed', 'true'))
}

/** Leave the current step for the previous one. */
const back = () => userEvent.click(screen.getByRole('button', { name: 'Back' }))

/** Click the ask button and wait until the ready proposal, with its shadow plan, can be used. */
async function askAndWaitForProposal() {
  await userEvent.click(await screen.findByRole('button', { name: ASK }))
  const use = await screen.findByRole('button', { name: USE })
  await waitFor(() => expect(use).toBeEnabled())
  return use
}

/**
 * Use the proposal and wait for the wizard's own preview of the filled-in form. Returns the preview bodies
 * posted since the click; the shadow preview's key does not change on Use, so the last of them is the wizard's.
 */
async function useProposal(use: HTMLElement) {
  const before = previewBodies().length
  await userEvent.click(use)
  await waitFor(() => expect(scopeButton('feature')).toHaveAttribute('aria-pressed', 'true'))
  await waitFor(() => expect(previewBodies().length).toBeGreaterThan(before))
  return previewBodies().slice(before)
}

/** From the Preset step, through Plan and Review, to a created intent. */
async function continueToCreate() {
  await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled())
  await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
  await screen.findByText(/Every known stage is listed/)
  await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
  const create = await screen.findByRole('button', { name: /Create the intent, paused/ })
  await waitFor(() => expect(create).toBeEnabled())
  await userEvent.click(create)
  await waitFor(() => expect(screen.getByText('Intent created and left paused')).toBeInTheDocument())
}

beforeEach(() => {
  installRoutes()
  navigated.length = 0
})

describe('the new intent wizard', () => {
  it('shows a failed space inventory and lets the user retry it before continuing', async () => {
    let available = false
    installRoutes({
      [`GET ${BASE}/repos/r_1/intents`]: () => {
        if (!available) throw new StubApiError(503, { code: 'inventory_offline', error: 'Space inventory is unavailable.' })
        return { intents: [], spaces: ['default'], active_space: 'default' }
      },
    })
    mount()
    await userEvent.selectOptions(await screen.findByLabelText('Repository'), 'r_1')
    await userEvent.type(screen.getByLabelText('Objective'), OBJECTIVE)
    expect(await screen.findByRole('alert')).toHaveTextContent('Space inventory is unavailable.')
    expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled()
    available = true
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('keeps the reviewed inputs locked while Create is pending and unlocks after a refusal', async () => {
    let refuse!: (reason: unknown) => void
    const pending = new Promise((_, reject) => { refuse = reject })
    installRoutes({ [`POST ${BASE}/repos/r_1/intents`]: () => pending })
    mount()
    await toPreset()
    await pickScope('feature')
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    const create = screen.getByRole('button', { name: /Create the intent, paused/ })
    await waitFor(() => expect(create).toBeEnabled())
    await userEvent.click(create)

    expect(create).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Back' })).toBeDisabled()
    for (const button of within(screen.getByRole('list', { name: 'New intent steps' })).getAllByRole('button')) {
      expect(button).toBeDisabled()
      await userEvent.click(button)
    }
    expect(screen.queryByLabelText('Repository')).not.toBeInTheDocument()
    await act(async () => refuse(new StubApiError(409, { code: 'repo_busy', error: 'Repository is busy.' })))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Back' })).toBeEnabled())
    expect(create).toBeEnabled()
    expect(apiCalls.filter((call) => call.method === 'POST' && call.path === `${BASE}/repos/r_1/intents`)).toHaveLength(1)
  })

  it('shows four steps and promises that Create writes nothing until it is pressed', async () => {
    mount()
    expect(await screen.findByRole('heading', { name: 'New intent' })).toBeInTheDocument()
    expect(
      screen.getByText(
        'Four steps. Nothing is written to the repository until you press Create, and Create never starts execution.',
      ),
    ).toBeInTheDocument()
    const stepper = screen.getByRole('list', { name: 'New intent steps' })
    expect(within(stepper).getAllByRole('button')).toHaveLength(4)
    expect(stepper.textContent).toContain('Work')
    expect(stepper.textContent).toContain('Preset')
    expect(stepper.textContent).toContain('Plan')
    expect(stepper.textContent).toContain('Review')
    expect(screen.getByText('Step 1 of 4')).toBeInTheDocument()
  })

  it('will not continue past Work without a repository, an objective and a usable label', async () => {
    mount()
    const next = await screen.findByRole('button', { name: 'Continue' })
    expect(next).toBeDisabled()
    await userEvent.selectOptions(screen.getByLabelText('Repository'), 'r_1')
    expect(next).toBeDisabled()
    // A Chinese objective slugifies to nothing, so the label becomes the field that must be filled.
    await userEvent.type(screen.getByLabelText('Objective'), '让未登录的顾客完成购买')
    expect(screen.getByText('The objective produces no label AI-DLC can use, so type one.')).toBeInTheDocument()
    expect(next).toBeDisabled()
    await userEvent.type(screen.getByLabelText('Label'), 'guest checkout')
    expect(
      screen.getByText('At most three lowercase words joined by hyphens, for example guest-checkout.'),
    ).toBeInTheDocument()
    await userEvent.clear(screen.getByLabelText('Label'))
    await userEvent.type(screen.getByLabelText('Label'), 'guest-checkout')
    await waitFor(() => expect(next).toBeEnabled())
  })

  it('offers the scopes the repository actually defines, read from the engine refusal', async () => {
    mount()
    await toPreset()
    expect(await screen.findByRole('button', { name: /bugfix/ })).toBeInTheDocument()
    const feature = screen.getByRole('button', { name: /feature/ })
    expect(feature).toHaveAttribute('aria-pressed', 'false')
    await userEvent.click(feature)
    await waitFor(() => expect(feature).toHaveAttribute('aria-pressed', 'true'))
    // The count comes from the plan the server composed for that scope, never from a table in the UI.
    expect(await screen.findByText('3 of 33 stages selected')).toBeInTheDocument()
    expect(screen.getByText('the default for scoped product work')).toBeInTheDocument()
  })

  it('sends a stage the user turned off as an override and shows the engine locks', async () => {
    mount()
    await toPreset()
    await userEvent.click(await screen.findByRole('button', { name: /feature/ }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))

    expect(await screen.findByText(/Every known stage is listed/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /code-generation/ })).toBeDisabled()
    expect(screen.getByText('always')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('checkbox', { name: /nfr-requirements/ }))
    await waitFor(() => {
      const previews = apiCalls.filter((call) => call.path === `${BASE}/repos/r_1/intents/plan/preview`)
      const last = previews[previews.length - 1]?.body as { overrides: Record<string, boolean> }
      expect(last.overrides).toEqual({ 'nfr-requirements': false })
    })
    expect(await screen.findByText('− nfr-requirements turned off by you')).toBeInTheDocument()
  })

  it('can select and cancel optional stages repeatedly while keeping preset-required stages locked', async () => {
    installRoutes({
      [`POST ${PREVIEW}`]: previewWith(() => ({ stages: [
        stage({ slug: 'reverse-engineering', execution: 'CONDITIONAL' }),
        stage({ slug: 'approval-handoff', execution: 'ALWAYS', in_grid: false, enabled: false }),
        stage({ slug: 'code-generation', locked: true, lock_reason: 'always' }),
      ] })),
    })
    mount()
    await toPreset()
    await userEvent.click(await screen.findByRole('button', { name: /feature/ }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    const reverse = await screen.findByRole('checkbox', { name: /reverse-engineering/ })
    const optional = screen.getByRole('checkbox', { name: /approval-handoff/ })
    for (let cycle = 0; cycle < 2; cycle += 1) {
      await waitFor(() => expect(reverse).toBeEnabled())
      await userEvent.click(reverse)
      await waitFor(() => { expect(reverse).not.toBeChecked(); expect(reverse).toBeEnabled() })
      await userEvent.click(reverse)
      await waitFor(() => { expect(reverse).toBeChecked(); expect(reverse).toBeEnabled() })
      await userEvent.click(optional)
      await waitFor(() => { expect(optional).toBeChecked(); expect(optional).toBeEnabled() })
      await userEvent.click(optional)
      await waitFor(() => { expect(optional).not.toBeChecked(); expect(optional).toBeEnabled() })
    }
    expect(previewBodies().at(-1)?.overrides).toEqual({})
    expect(screen.getByRole('checkbox', { name: /code-generation/ })).toBeDisabled()
    expect(apiCalls.filter((call) => call.method === 'POST' && !call.path.endsWith('/plan/preview'))).toHaveLength(0)
  })

  it.each(['en-US', 'zh-CN'] as const)('opens a partially created intent for plan correction without compiling default stages (%s)', async (locale) => {
    const createPath = `${BASE}/repos/r_1/intents`
    const compilePath = `${createPath}/default~250905-guest-checkout/runtime/compile`
    installRoutes({
      [`POST ${createPath}`]: () => {
        throw new StubApiError(409, {
          code: 'state_inconsistent', error: 'Intent exists, but plan composition failed.',
          details: { intent_created: true, creation_failed_phase: 'plan_composition',
            intent_dir: '250905-guest-checkout', intent_key: 'default~250905-guest-checkout', space: 'default' },
        })
      },
      // Compiling the default plan would return success, so the UI must never take this recovery path.
      [`POST ${compilePath}`]: () => ({ ok: true, runtime_graph_present: true }),
    })
    mount()
    await toPreset()
    await pickScope('feature')
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    const optionalStage = await screen.findByRole('checkbox', { name: /nfr-requirements/ })
    await waitFor(() => expect(optionalStage).toBeEnabled())
    await userEvent.click(optionalStage)
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await act(async () => { document.documentElement.lang = locale })
    const i18n = makeI18n(locale)
    const create = screen.getByRole('button', { name: i18n.t('wizard.nav.create') })
    await waitFor(() => expect(create).toBeEnabled())
    await userEvent.click(create)

    expect(await screen.findByRole('heading', { name: locale === 'en-US'
      ? 'Intent created; plan needs correction' : '意图已创建，计划需要修正' })).toBeInTheDocument()
    expect(screen.getByText(locale === 'en-US'
      ? '250905-guest-checkout already exists, but the selected stage changes were not applied. Open this intent to review and correct its plan before compiling or running it.'
      : '250905-guest-checkout 已经存在，但尚未应用所选的阶段变更。请打开该意图检查并修正计划，再编译运行图或启动工作流。')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('wizard.partial.retry') })).not.toBeInTheDocument()
    expect(screen.queryByText(i18n.t('wizard.partial.activate'))).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: i18n.t('wizard.partial.title') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: i18n.t('wizard.nav.create') })).not.toBeInTheDocument()
    expect(screen.queryByText(i18n.t('wizard.created.title'))).not.toBeInTheDocument()
    expect(navigated).toEqual([])

    await userEvent.click(screen.getByRole('button', { name: locale === 'en-US'
      ? 'Open intent to review its plan' : '打开意图检查计划' }))
    expect(navigated.at(-1)).toEqual({
      view: 'intents', repo: 'r_1', space: 'default', intent: 'default~250905-guest-checkout',
    })
    const creates = apiCalls.filter((call) => call.method === 'POST' && call.path === createPath)
    expect(creates).toHaveLength(1)
    expect(creates[0]?.body).toMatchObject({ overrides: { 'nfr-requirements': false } })
    expect(apiCalls.filter((call) => call.path === compilePath)).toHaveLength(0)
    expect(apiCalls.some((call) => call.path.startsWith('/api/chat') || call.path.endsWith('/run'))).toBe(false)
  })

  it.each([undefined, 'runtime_compile'])('repairs a partial creation without creating again or sending a workflow turn (phase: %s)', async (failedPhase) => {
    const repairPath = `${BASE}/repos/r_1/intents/default~250905-guest-checkout/runtime/compile`
    let attempts = 0
    installRoutes({
      [`POST ${BASE}/repos/r_1/intents`]: () => {
        throw new StubApiError(409, {
          code: 'state_inconsistent', error: 'Intent exists, but runtime compilation failed.',
          details: { intent_created: true, intent_dir: '250905-guest-checkout',
            intent_key: 'default~250905-guest-checkout', space: 'default',
            ...(failedPhase ? { creation_failed_phase: failedPhase } : {}) },
        })
      },
      [`POST ${repairPath}`]: () => {
        attempts += 1
        if (attempts === 1) throw new StubApiError(409, {
          code: 'repo_busy', error: 'A session is running.', details: {},
        })
        return { ok: true, intent_key: 'default~250905-guest-checkout', runtime_graph_present: true }
      },
    })
    mount()
    await toPreset()
    await pickScope('feature')
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await screen.findByText(/Every known stage is listed/)
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    const create = await screen.findByRole('button', { name: /Create the intent, paused/ })
    await waitFor(() => expect(create).toBeEnabled())
    await userEvent.click(create)
    expect(await screen.findByRole('heading', { name: 'Intent created; runtime graph needs repair' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Create the intent, paused/ })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Retry runtime compilation' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('A session is running.')
    await userEvent.click(screen.getByRole('button', { name: 'Retry runtime compilation' }))
    await waitFor(() => expect(navigated.at(-1)).toEqual({
      view: 'intents', repo: 'r_1', space: 'default', intent: 'default~250905-guest-checkout',
    }))
    expect(apiCalls.filter((call) => call.path === `${BASE}/repos/r_1/intents` && call.method === 'POST')).toHaveLength(1)
    expect(apiCalls.filter((call) => call.path === repairPath)).toHaveLength(2)
    expect(apiCalls.some((call) => call.path.startsWith('/api/chat'))).toBe(false)
  })

  it('creates a paused intent with the digest of the plan on screen, and runs nothing', async () => {
    mount()
    await toPreset()
    await userEvent.click(await screen.findByRole('button', { name: /feature/ }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await screen.findByText(/Every known stage is listed/)
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))

    // Exact and estimated are labelled apart, and credits are a word.
    expect(await screen.findByLabelText('Exact counts')).toBeInTheDocument()
    const estimates = screen.getByLabelText('Estimates with source and confidence')
    const credits = within(estimates).getByText('Credits').closest('.studio-stat')
    expect(credits?.textContent).toContain('Unavailable')
    expect(credits?.textContent).toContain('not zero, not inferred')
    expect(credits?.textContent).not.toMatch(/\b0\b/)
    expect(screen.getByText(/Plan fingerprint [0-9a-f]{64}/)).toBeInTheDocument()

    const create = screen.getByRole('button', { name: /Create the intent, paused/ })
    await waitFor(() => expect(create).toBeEnabled())
    await userEvent.click(create)

    await waitFor(() => expect(screen.getByText('Intent created and left paused')).toBeInTheDocument())
    const post = apiCalls.filter((call) => call.method === 'POST' && call.path === `${BASE}/repos/r_1/intents`)
    expect(post).toHaveLength(1)
    const body = post[0]?.body as { confirm_plan_digest: string; objective: string; label: string | null; space: string }
    expect(body.confirm_plan_digest).toMatch(/^[0-9a-f]{64}$/)
    expect(body.confirm_plan_digest).toBe(
      planDigest(planFor('feature', {}), { ...EMPTY_WIZARD, objective: 'Let signed-out shoppers complete a purchase' }),
    )
    expect(body.objective).toBe('Let signed-out shoppers complete a purchase')
    expect(body.space).toBe('default')

    // Nothing was dispatched: no run, no resume, no host chat send.
    expect(apiCalls.some((call) => call.path.endsWith('/run') || call.path.endsWith('/resume'))).toBe(false)
    expect(apiCalls.some((call) => call.path.startsWith('/api/chat'))).toBe(false)

    // The next act is the user's, and it is named.
    expect(screen.getByText(/you start it with Run to next checkpoint/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Open Intents to run it' }))
    expect(navigated[navigated.length - 1]).toEqual({
      view: 'intents',
      repo: 'r_1',
      space: 'default',
      intent: 'default~250905-guest-checkout',
    })
  })
})

describe('async scope previews', () => {
  function deferred<T>() {
    let resolve!: (value: T) => void
    let reject!: (reason: unknown) => void
    const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
    return { promise, resolve, reject }
  }

  const answerFor = (request: PlanRequest, meta: Record<string, unknown> = {}) => ({
    plan: { ...planFor(request.scope, request.overrides ?? {}, { meta }), request },
  })
  const creates = () => apiCalls.filter((call) => call.method === 'POST' && call.path === `${BASE}/repos/r_1/intents`)
  const stepButton = (name: string) => within(screen.getByRole('list', { name: 'New intent steps' }))
    .getByRole('button', { name: new RegExp(name) })

  it.each([null, 'Minimal'])('preserves explicit Minimal depth and Standard strategy when late scope strategy is %s', async (scopeStrategy) => {
    const pending = deferred<void>()
    const meta = { depth: 'Comprehensive', test_strategy: scopeStrategy, review_cap: 'advisory' }
    installRoutes({
      [`POST ${PREVIEW}`]: async (body) => {
        const request = body as PlanRequest
        if (request.scope) await pending.promise
        return answerFor(request, meta)
      },
    })
    mount()
    await toPreset()
    await pickScope('bugfix')
    await userEvent.click(depthButton('Minimal'))
    await userEvent.click(strategyButton('Standard'))
    expect(strategyButton('Standard')).toHaveAttribute('aria-pressed', 'true')
    await waitFor(() => expect(previewBodies().at(-1)).toMatchObject({
      scope: 'bugfix', depth: 'Minimal', test_strategy: 'Standard',
    }))
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await act(async () => pending.resolve())
    await userEvent.click(stepButton('Review'))
    const create = screen.getByRole('button', { name: /Create the intent, paused/ })
    await waitFor(() => expect(create).toBeEnabled())
    await userEvent.click(create)
    await screen.findByText('Intent created and left paused')
    expect(creates()).toHaveLength(1)
    expect(creates()[0]?.body).toMatchObject({
      scope: 'bugfix', depth: 'Minimal', test_strategy: 'Standard', review_cap: 'advisory',
    })
    const lastRequest = apiCalls.filter((call) => call.path === PREVIEW).at(-1)?.body as PlanRequest
    expect(creates()[0]?.body).toMatchObject({
      confirm_plan_digest: planDigest(answerFor(lastRequest, meta).plan, { ...EMPTY_WIZARD, objective: OBJECTIVE }),
    })
  })

  it('preserves explicit From scope choices even while their current value is already null', async () => {
    const pending = deferred<void>()
    installRoutes({
      [`POST ${PREVIEW}`]: async (body) => {
        const request = body as PlanRequest
        if (request.scope) await pending.promise
        return answerFor(request, { depth: 'Minimal', test_strategy: 'Comprehensive', review_cap: 'adversarial' })
      },
    })
    mount()
    await toPreset()
    await pickScope('bugfix')
    for (const group of ['Test strategy', 'Review cap']) {
      await userEvent.click(within(screen.getByRole('group', { name: group })).getByRole('button', { name: /From the scope/ }))
    }
    await waitFor(() => expect(previewBodies().at(-1)?.scope).toBe('bugfix'))
    await act(async () => pending.resolve())
    await continueToCreate()
    expect(creates()[0]?.body).toMatchObject({
      depth: 'Minimal', test_strategy: null, review_cap: null,
    })
  })

  it('applies all scope defaults to untouched fields after switching scopes', async () => {
    installRoutes({
      [`POST ${PREVIEW}`]: (body) => answerFor(body as PlanRequest, {
        depth: 'Comprehensive', test_strategy: 'Minimal', review_cap: 'advisory',
      }),
    })
    mount()
    await toPreset()
    await pickScope('feature')
    await waitFor(() => expect(capButton('advisory')).toHaveAttribute('aria-pressed', 'true'))
    await userEvent.click(depthButton('Minimal'))
    await userEvent.click(strategyButton('Standard'))
    await userEvent.click(capButton('none'))
    await pickScope('bugfix')
    await waitFor(() => {
      expect(depthButton('Comprehensive')).toHaveAttribute('aria-pressed', 'true')
      expect(strategyButton('Minimal')).toHaveAttribute('aria-pressed', 'true')
      expect(capButton('advisory')).toHaveAttribute('aria-pressed', 'true')
    })
    await continueToCreate()
    expect(creates()[0]?.body).toMatchObject({
      scope: 'bugfix', depth: 'Comprehensive', test_strategy: 'Minimal', review_cap: 'advisory',
    })
  })

  it('ignores an older scope response arriving after the current scope and explicit choices', async () => {
    const old = deferred<void>()
    const current = deferred<void>()
    installRoutes({
      [`POST ${PREVIEW}`]: async (body) => {
        const request = body as PlanRequest
        if (request.scope === 'feature') await old.promise
        if (request.scope === 'bugfix') await current.promise
        return answerFor(request, {
          depth: 'Comprehensive', test_strategy: 'Minimal', review_cap: request.scope === 'feature' ? 'adversarial' : 'advisory',
        })
      },
    })
    mount()
    await toPreset()
    await pickScope('feature')
    await waitFor(() => expect(previewBodies().at(-1)?.scope).toBe('feature'))
    await pickScope('bugfix')
    await userEvent.click(depthButton('Minimal'))
    await userEvent.click(strategyButton('Standard'))
    await waitFor(() => expect(previewBodies().at(-1)).toMatchObject({ scope: 'bugfix', test_strategy: 'Standard' }))
    await act(async () => current.resolve())
    await waitFor(() => expect(capButton('advisory')).toHaveAttribute('aria-pressed', 'true'))
    await act(async () => old.resolve())
    expect(scopeButton('bugfix')).toHaveAttribute('aria-pressed', 'true')
    expect(depthButton('Minimal')).toHaveAttribute('aria-pressed', 'true')
    expect(strategyButton('Standard')).toHaveAttribute('aria-pressed', 'true')
    expect(capButton('advisory')).toHaveAttribute('aria-pressed', 'true')
    await continueToCreate()
    expect(creates()[0]?.body).toMatchObject({ scope: 'bugfix', depth: 'Minimal', test_strategy: 'Standard', review_cap: 'advisory' })
  })

  it.each(['debounce', 'failure'])('does not confirm a previous plan during the next preview %s', async (phase) => {
    let hold = false
    const pending = deferred<{ plan: EffectivePlan }>()
    installRoutes({
      [`POST ${PREVIEW}`]: (body) => hold ? pending.promise : answerFor(body as PlanRequest),
    })
    mount()
    await toPreset()
    await pickScope('bugfix')
    await userEvent.click(stepButton('Review'))
    await waitFor(() => expect(screen.getByRole('button', { name: /Create the intent, paused/ })).toBeEnabled())
    hold = true
    await userEvent.click(stepButton('Preset'))
    fireEvent.click(strategyButton('Standard'))
    fireEvent.click(stepButton('Review'))
    if (phase === 'failure') {
      await waitFor(() => expect(previewBodies().at(-1)?.test_strategy).toBe('Standard'))
      await act(async () => pending.reject(new StubApiError(500, { code: 'internal_error', error: 'preview failed', details: {} })))
      await screen.findByRole('alert')
    }
    const create = screen.getByRole('button', { name: /Create the intent, paused/ })
    expect(create).toBeDisabled()
    fireEvent.click(create)
    expect(creates()).toHaveLength(0)
    if (phase === 'debounce') {
      await waitFor(() => expect(previewBodies().at(-1)?.test_strategy).toBe('Standard'))
      const request = apiCalls.filter((call) => call.path === PREVIEW).at(-1)?.body as PlanRequest
      await act(async () => pending.resolve(answerFor(request)))
      await waitFor(() => expect(create).toBeEnabled())
      await userEvent.click(create)
      await screen.findByText('Intent created and left paused')
      expect(creates()[0]?.body).toMatchObject({
        test_strategy: 'Standard',
        confirm_plan_digest: planDigest(answerFor(request).plan, { ...EMPTY_WIZARD, objective: OBJECTIVE }),
      })
    }
  })
})

describe('the Advisor in the Preset step', () => {
  it('requires a fresh shadow plan before applying changed settings during the debounce', async () => {
    let finish!: () => void
    const pending = new Promise<void>((resolve) => { finish = resolve })
    let hold = false
    installRoutes({
      [`POST ${PREVIEW}`]: async (body) => {
        const request = body as PlanRequest
        if (hold) await pending
        return { plan: { ...planFor(request.scope, request.overrides ?? {}), request } }
      },
    })
    mount()
    await toPreset()
    await pickScope('feature')
    await askAndWaitForProposal()
    hold = true

    fireEvent.click(strategyButton('Standard'))
    const use = screen.getByRole('button', { name: USE })
    expect(use).toBeDisabled()
    fireEvent.click(use)
    expect(screen.queryByText(APPLIED)).not.toBeInTheDocument()
    await waitFor(() => expect(previewBodies().at(-1)?.test_strategy).toBe('Standard'))
    expect(use).toBeDisabled()

    await act(async () => finish())
    await waitFor(() => expect(use).toBeEnabled())
    await userEvent.click(use)
    expect(screen.getByText(APPLIED)).toBeInTheDocument()
    expect(strategyButton('Standard')).toHaveAttribute('aria-pressed', 'true')
  })

  it('asks the Advisor for nothing until the button is clicked', async () => {
    mount()
    await toPreset()
    const ask = await screen.findByRole('button', { name: ASK })
    // Mounting, typing the objective and reaching the Preset step posted nothing (FR-ADV-001).
    expect(advisePosts()).toHaveLength(0)
    expect(draftPolls()).toHaveLength(0)

    await userEvent.click(ask)
    await waitFor(() => expect(advisePosts()).toHaveLength(1))
    const body = advisePosts()[0]?.body as Record<string, unknown>
    expect(body.objective).toBe(OBJECTIVE)
    expect(body.locale).toBe('en-US')
    // Card-less: the request names a repository, never an action card.
    expect(body).not.toHaveProperty('action_id')
  })

  it("renders the proposal as the server's plan diff and applies nothing until it is used", async () => {
    mount()
    await toPreset()
    await screen.findByRole('button', { name: /^feature/ })
    const use = await askAndWaitForProposal()

    // The stage change on screen is the engine's diff over the proposed settings, labelled as the Advisor's;
    // the form itself has not moved.
    expect(await screen.findByText(BY_ADVISOR)).toBeInTheDocument()
    expect(scopeButton('feature')).toHaveAttribute('aria-pressed', 'false')
    expect(depthButton('Minimal')).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByText(APPLIED)).toBeNull()

    const since = await useProposal(use)
    expect(depthButton('Minimal')).toHaveAttribute('aria-pressed', 'true')
    const last = since[since.length - 1]
    expect(last?.scope).toBe('feature')
    expect(last?.overrides).toEqual({ 'nfr-requirements': false })
    expect(screen.getByText(APPLIED)).toBeInTheDocument()

    // On the Plan step the same row is still the Advisor's, not the human's.
    await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await screen.findByText(/Every known stage is listed/)
    expect(screen.getByText(BY_ADVISOR)).toBeInTheDocument()
    expect(screen.queryByText(BY_YOU)).toBeNull()
  })

  it('keeps the proposed depth after the scope preview lands', async () => {
    // planFor('feature') says the scope's own depth is 'Standard'; the scope-sync effect must not let that
    // overwrite the depth the human just accepted from the proposal (design §2.3).
    mount()
    await toPreset()
    const use = await askAndWaitForProposal()
    await useProposal(use)
    // Two of the three stages: the preview of the filled-in form, with its override, has landed.
    expect(await screen.findByText('2 of 33 stages selected')).toBeInTheDocument()
    expect(screen.getByText('the default for scoped product work')).toBeInTheDocument()
    expect(depthButton('Minimal')).toHaveAttribute('aria-pressed', 'true')
    expect(depthButton('Standard')).toHaveAttribute('aria-pressed', 'false')
  })

  it('lets the human change a proposed setting, and Create sends the digest of the plan shown', async () => {
    mount()
    await toPreset()
    const use = await askAndWaitForProposal()
    await useProposal(use)

    // The proposal is a starting point: Minimal becomes Comprehensive without clearing anything.
    await userEvent.click(depthButton('Comprehensive'))
    await waitFor(() => expect(depthButton('Comprehensive')).toHaveAttribute('aria-pressed', 'true'))
    expect(screen.getByText(APPLIED)).toBeInTheDocument()
    await waitFor(() => expect(previewBodies()[previewBodies().length - 1]?.depth).toBe('Comprehensive'))

    await continueToCreate()
    const previews = previewBodies()
    const shown = previews[previews.length - 1]
    expect(shown?.scope).toBe('feature')
    expect(shown?.depth).toBe('Comprehensive')
    expect(shown?.overrides).toEqual({ 'nfr-requirements': false })

    const post = apiCalls.filter((call) => call.method === 'POST' && call.path === `${BASE}/repos/r_1/intents`)
    expect(post).toHaveLength(1)
    const body = post[0]?.body as { confirm_plan_digest: string }
    expect(body.confirm_plan_digest).toBe(
      planDigest(planFor(shown?.scope ?? '', shown?.overrides ?? {}), { ...EMPTY_WIZARD, objective: OBJECTIVE }),
    )
  })

  it('says so when the Advisor is unavailable and leaves the questionnaire usable', async () => {
    installRoutes({
      [`POST ${ADVISE}`]: () => {
        throw new Error('API 503: {"code":"advisor_unavailable"}')
      },
    })
    mount()
    await toPreset()
    await userEvent.click(await screen.findByRole('button', { name: ASK }))
    expect(await screen.findByText('The AI Advisor is not available.')).toBeInTheDocument()
    expect(draftPolls()).toHaveLength(0)

    // The refusal touched nothing else: the human picks a scope by hand.
    await userEvent.click(await screen.findByRole('button', { name: /^feature/ }))
    await waitFor(() => expect(scopeButton('feature')).toHaveAttribute('aria-pressed', 'true'))
    expect(await screen.findByText('3 of 33 stages selected')).toBeInTheDocument()
  })

  it('forgets the proposal after Create', async () => {
    mount()
    await toPreset()
    const use = await askAndWaitForProposal()
    await useProposal(use)
    await continueToCreate()
    const polls = draftPolls().length

    // The consumed draft does not outlive the intent it informed (FR-NEW-004): a fresh wizard starts unasked.
    await userEvent.click(screen.getByRole('button', { name: 'Create another intent' }))
    await toPreset()
    expect(await screen.findByRole('button', { name: ASK })).toBeInTheDocument()
    expect(screen.queryByText(APPLIED)).toBeNull()
    expect(screen.queryByText(BY_ADVISOR)).toBeNull()
    expect(screen.queryByRole('button', { name: USE })).toBeNull()
    expect(await screen.findByRole('button', { name: /^feature/ })).toHaveAttribute('aria-pressed', 'false')
    expect(draftPolls()).toHaveLength(polls)
  })

  it('labels the proposal a draft, not a decision', async () => {
    mount()
    await toPreset()
    await screen.findByRole('button', { name: ASK })
    expect(screen.queryByText('draft, not a decision')).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: ASK }))
    expect(await screen.findByText('draft, not a decision')).toBeInTheDocument()
    expect(screen.getByText('A small change to an existing module.')).toBeInTheDocument()
  })

  it('clears the proposal in one action, even after the scope was changed', async () => {
    mount()
    await toPreset()
    const use = await askAndWaitForProposal()
    await useProposal(use)
    expect(depthButton('Minimal')).toHaveAttribute('aria-pressed', 'true')

    await userEvent.click(scopeButton('bugfix'))
    await waitFor(() => expect(scopeButton('bugfix')).toHaveAttribute('aria-pressed', 'true'))
    // The banner and its Clear survive the scope change, so the proposal still goes in one action (FR-NEW-006).
    expect(screen.getByText(APPLIED)).toBeInTheDocument()
    const beforeClear = previewBodies().length
    await userEvent.click(screen.getByRole('button', { name: CLEAR }))

    await waitFor(() => expect(screen.queryByText(APPLIED)).toBeNull())
    // Back to the human's own picks from before Use: no scope, the default depth.
    expect(scopeButton('feature')).toHaveAttribute('aria-pressed', 'false')
    expect(scopeButton('bugfix')).toHaveAttribute('aria-pressed', 'false')
    expect(depthButton('Standard')).toHaveAttribute('aria-pressed', 'true')
    expect(depthButton('Minimal')).toHaveAttribute('aria-pressed', 'false')
    // And the next thing the engine is asked for is the human's own form again: no cap, no overrides.
    await waitFor(() => expect(previewBodies().length).toBeGreaterThan(beforeClear))
    const next = previewBodies()[beforeClear]
    expect(next?.scope).toBe('')
    expect(next?.review_cap).toBeNull()
    expect(next?.overrides).toEqual({})
  })

  it('never accepts a stage change the engine refuses', async () => {
    // The engine refuses the proposed override: the shadow plan carries the refusal (FR-PLAN-004/005).
    installRoutes({
      [`POST ${PREVIEW}`]: (body) => {
        const request = body as { scope: string; overrides: Record<string, boolean> }
        const plan = planFor(request.scope, request.overrides ?? {})
        if (request.overrides?.['nfr-requirements'] !== false) return { plan }
        return {
          plan: {
            ...plan,
            valid: false,
            issues: [{
              code: 'required_stage_disabled',
              slugs: ['nfr-requirements'],
              message_key: 'plan.issue.required_stage_disabled',
              params: { stage: 'nfr-requirements', lock_reason: 'always' },
            }],
          },
        }
      },
    })
    mount()
    await toPreset()
    const use = await askAndWaitForProposal()
    const since = await useProposal(use)

    // The refused slug never reached the form: the wizard's own preview carries no such override.
    const last = since[since.length - 1]
    expect(last?.scope).toBe('feature')
    expect(last?.overrides?.['nfr-requirements']).toBeUndefined()
    expect(last?.overrides ?? {}).toEqual({})
    expect(screen.getByText(DROPPED_ONE)).toBeInTheDocument()
    expect(depthButton('Minimal')).toHaveAttribute('aria-pressed', 'true')

    // A new scope resets the stage matrix, so the count of what was left out of it says nothing true any more;
    // the banner and its Clear stay (FR-NEW-006).
    await pickScope('bugfix')
    expect(screen.queryByText(DROPPED_ONE)).toBeNull()
    expect(screen.getByText(APPLIED)).toBeInTheDocument()
  })

  /**
   * A stage set with one dependency — code-generation (grid-off) consumes what units-generation (grid-on)
   * produces — and an engine that answers "code-generation on, units-generation off" with the refusal
   * `dependency_missing`, whose `slugs` name the starved consumer first and then the producer, exactly as
   * `plan.py::PlanIssue.of` builds it. The draft proposes that very pair.
   */
  function installDependencyRoutes() {
    const stages = [
      ...PLAN_STAGES.filter((entry) => entry.slug !== 'code-generation'),
      stage({ slug: 'units-generation', number: '4.2', phase: 'construction', per_unit: true, produces: ['units/'] }),
      stage({
        slug: 'code-generation', number: '4.3', phase: 'construction', per_unit: true,
        in_grid: false, enabled: false, consumes: ['units/'],
      }),
    ]
    const preview = previewWith(() => ({ stages }))
    installRoutes({
      [`POST ${PREVIEW}`]: (body, path) => {
        const request = body as { scope: string; overrides: Record<string, boolean> }
        const answer = preview(body, path) as { plan: EffectivePlan }
        if (request.overrides?.['code-generation'] !== true || request.overrides?.['units-generation'] !== false) return answer
        return {
          plan: {
            ...answer.plan,
            valid: false,
            issues: [{
              code: 'dependency_missing',
              slugs: ['code-generation', 'units-generation'],
              message_key: 'plan.issue.dependency_missing',
              params: { stage: 'code-generation', artifact: 'units/', producers: ['units-generation'] },
            }],
          },
        }
      },
      [`GET ${DRAFT}`]: () => ({
        draft: draftProposing({ overrides: { 'code-generation': true, 'units-generation': false } }),
      }),
    })
  }

  it('drops the starved consumer a dependency refusal names, not the producer the proposal turned off', async () => {
    // The engine ignored exactly one override — the consumer it could not feed. The producer being off was
    // honoured, so that change stays; only the refused one is left out (FR-PLAN-004/005).
    installDependencyRoutes()
    mount()
    await toPreset()
    await pickScope('feature')
    const use = await askAndWaitForProposal()
    const since = await useProposal(use)
    expect(since[since.length - 1]?.overrides).toEqual({ 'units-generation': false })
    expect(screen.getByText(DROPPED_ONE)).toBeInTheDocument()
  })

  it("keeps a pre-existing override of the human's that the engine refuses and the proposal merely repeats", async () => {
    // The human turned nfr-requirements off; the engine refuses that (a lock); the proposal's complete map
    // carries the same override. A refusal the proposal did not cause is not the proposal's to undo: the
    // human's pick stays, and nothing was "left out".
    installRoutes({
      [`POST ${PREVIEW}`]: (body) => {
        const request = body as { scope: string; overrides: Record<string, boolean> }
        const plan = planFor(request.scope, request.overrides ?? {})
        if (request.overrides?.['nfr-requirements'] !== false) return { plan }
        return {
          plan: {
            ...plan,
            valid: false,
            issues: [{
              code: 'required_stage_disabled',
              slugs: ['nfr-requirements'],
              message_key: 'plan.issue.required_stage_disabled',
              params: { stage: 'nfr-requirements', lock_reason: 'always' },
            }],
          },
        }
      },
    })
    mount()
    await toPreset()
    await pickScope('feature')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await screen.findByText(/Every known stage is listed/)
    await userEvent.click(screen.getByRole('checkbox', { name: /nfr-requirements/ }))
    await waitFor(() =>
      expect(previewBodies()[previewBodies().length - 1]?.overrides).toEqual({ 'nfr-requirements': false }),
    )
    await back()

    const use = await askAndWaitForProposal()
    const since = await useProposal(use)
    expect(since[since.length - 1]?.overrides).toEqual({ 'nfr-requirements': false })
    expect(screen.queryByText(DROPPED_ONE)).toBeNull()
    expect(screen.queryByText(/left out/)).toBeNull()
    expect(depthButton('Minimal')).toHaveAttribute('aria-pressed', 'true')
  })

  it("drops only the refused change the proposal made, never the human's own override", async () => {
    // Both rules at once: the human's producer-off pick is not refused and not the proposal's; the proposal's
    // consumer-on is refused and is. One left out, and the human's map is intact.
    installDependencyRoutes()
    mount()
    await toPreset()
    await pickScope('feature')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    await screen.findByText(/Every known stage is listed/)
    // The human's own pick: units-generation off.
    await userEvent.click(screen.getByRole('checkbox', { name: /units-generation/ }))
    await waitFor(() =>
      expect(previewBodies()[previewBodies().length - 1]?.overrides).toEqual({ 'units-generation': false }),
    )
    await back()

    const use = await askAndWaitForProposal()
    const asked = advisePosts()[0]?.body as { current: { overrides: Record<string, boolean> } }
    expect(asked.current.overrides).toEqual({ 'units-generation': false })

    // The proposal's own change (code-generation on) is what the engine refused, and the only thing dropped.
    const since = await useProposal(use)
    const last = since[since.length - 1]
    expect(last?.overrides).toEqual({ 'units-generation': false })
    expect(screen.getByText(DROPPED_ONE)).toBeInTheDocument()
  })

  it("sends the human's test strategy as current and keeps it when the proposal does not propose one", async () => {
    mount()
    await toPreset()
    await pickScope('feature')
    // The scope's frontmatter has landed (its cap is pressed) before the human picks: the pick is deliberate,
    // not something the scope sync is about to overwrite.
    await waitFor(() => expect(capButton('adversarial')).toHaveAttribute('aria-pressed', 'true'))
    await userEvent.click(strategyButton('Comprehensive'))
    await waitFor(() => expect(strategyButton('Comprehensive')).toHaveAttribute('aria-pressed', 'true'))

    const use = await askAndWaitForProposal()
    const body = advisePosts()[0]?.body as { current: { scope: string; test_strategy: string | null } }
    expect(body.current.scope).toBe('feature')
    expect(body.current.test_strategy).toBe('Comprehensive')
    // The proposal leaves the test strategy alone, and the panel says what that keeps.
    expect(screen.getByText('not proposed — keeps Comprehensive')).toBeInTheDocument()

    // Same scope: "not proposed" keeps the human's own pick, in the row and in what the engine is asked for.
    const since = await useProposal(use)
    expect(strategyButton('Comprehensive')).toHaveAttribute('aria-pressed', 'true')
    expect(since[since.length - 1]?.test_strategy).toBe('Comprehensive')
  })

  it("falls back to the proposed scope's own frontmatter, not the old scope's, for what it does not propose", async () => {
    // feature asks for Standard tests under an adversarial cap; bugfix for Minimal tests and no reviewer.
    const META: Record<string, Record<string, unknown>> = {
      feature: { test_strategy: 'Standard', review_cap: 'adversarial' },
      bugfix: { test_strategy: 'Minimal', review_cap: 'none', description: 'a defect in existing behaviour' },
    }
    installRoutes({
      [`POST ${PREVIEW}`]: previewWith((scope) => ({ meta: META[scope] })),
      [`GET ${DRAFT}`]: () => ({
        draft: draftProposing({ scope: 'bugfix', base_scope: 'bugfix', test_strategy: null, review_cap: null, overrides: {} }),
      }),
    })
    mount()
    await toPreset()
    await pickScope('feature')
    // feature's frontmatter has landed in the rows: these are the values a naive fallback would carry over.
    await waitFor(() => expect(strategyButton('Standard')).toHaveAttribute('aria-pressed', 'true'))
    expect(capButton('adversarial')).toHaveAttribute('aria-pressed', 'true')

    await userEvent.click(await screen.findByRole('button', { name: ASK }))
    // "keeps" names what Use would copy: bugfix's own values, read from the shadow plan's frontmatter.
    expect(await screen.findByText('not proposed — keeps Minimal')).toBeInTheDocument()
    expect(screen.getByText('not proposed — keeps none')).toBeInTheDocument()
    const use = screen.getByRole('button', { name: USE })
    await waitFor(() => expect(use).toBeEnabled())

    const before = previewBodies().length
    await userEvent.click(use)
    await waitFor(() => expect(scopeButton('bugfix')).toHaveAttribute('aria-pressed', 'true'))
    await waitFor(() => expect(strategyButton('Minimal')).toHaveAttribute('aria-pressed', 'true'))
    expect(capButton('none')).toHaveAttribute('aria-pressed', 'true')
    await waitFor(() => expect(previewBodies().length).toBeGreaterThan(before))
    const since = previewBodies().slice(before)
    const last = since[since.length - 1]
    expect(last?.scope).toBe('bugfix')
    expect(last?.test_strategy).toBe('Minimal')
    expect(last?.review_cap).toBe('none')
  })

  it('says the objective moved, refuses Use, and is cured by asking again', async () => {
    mount()
    await toPreset()
    await askAndWaitForProposal()
    expect(screen.queryByText(STALE_OBJECTIVE)).toBeNull()

    await back()
    await userEvent.type(await screen.findByLabelText('Objective'), ' today')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))

    // The draft read another objective: the copy says so, not the scope copy, and Use is refused.
    expect(await screen.findByText(STALE_OBJECTIVE)).toBeInTheDocument()
    expect(screen.queryByText(STALE_SCOPE)).toBeNull()
    expect(screen.getByRole('button', { name: USE })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: ASK_AGAIN }))
    await waitFor(() => expect(advisePosts()).toHaveLength(2))
    expect((advisePosts()[1]?.body as { objective: string }).objective).toBe(`${OBJECTIVE} today`)
    await waitFor(() => expect(screen.getByRole('button', { name: USE })).toBeEnabled())
    expect(screen.queryByText(STALE_OBJECTIVE)).toBeNull()
  })

  it('says the scope moved after Use, naming the proposed scope, and says nothing while nothing moved', async () => {
    mount()
    await toPreset()
    const use = await askAndWaitForProposal()
    await useProposal(use)
    // Accepting the proposal is not moving away from it: the wizard's scope IS the proposed scope.
    expect(screen.queryByText(STALE_OBJECTIVE)).toBeNull()
    expect(screen.queryByText(STALE_SCOPE)).toBeNull()

    await pickScope('bugfix')
    expect(await screen.findByText(STALE_SCOPE)).toBeInTheDocument()
    expect(screen.queryByText(STALE_OBJECTIVE)).toBeNull()
    expect(screen.getByRole('button', { name: USE })).toBeDisabled()
  })

  it('drops an answer that arrives after the repository was changed', async () => {
    // The ask is answered only when the test says so — after the wizard has moved to another repository.
    let answer: (value: unknown) => void = () => {}
    const late = new Promise<unknown>((resolve) => {
      answer = resolve
    })
    installRoutes({
      [`POST ${ADVISE}`]: () => late,
      [`GET ${BASE}/repos/r_2/intents`]: () => ({ intents: [], spaces: ['default'], active_space: 'default' }),
      [`POST ${BASE}/repos/r_2/intents/plan/preview`]: previewWith(() => ({})),
    })
    mount()
    await toPreset()
    await userEvent.click(await screen.findByRole('button', { name: ASK }))
    await waitFor(() => expect(advisePosts()).toHaveLength(1))
    expect(screen.getByText('Reading the evidence for a plan proposal…')).toBeInTheDocument()

    await back()
    await userEvent.selectOptions(await screen.findByLabelText('Repository'), 'r_2')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    // Under the new repository that ask is nobody's: the button is usable while the old one is still in flight.
    expect(await screen.findByRole('button', { name: ASK })).toBeEnabled()

    answer({ ok: true, draft: planDraft('queued') })
    // Back on the first repository the late draft is not resurrected: no poll, no proposal, the ask button.
    await back()
    await userEvent.selectOptions(await screen.findByLabelText('Repository'), 'r_1')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(await screen.findByRole('button', { name: ASK })).toBeEnabled()
    expect(screen.queryByRole('button', { name: USE })).toBeNull()
    expect(draftPolls()).toHaveLength(0)
  })

  it('forgets a proposal the server no longer has', async () => {
    installRoutes({
      [`GET ${DRAFT}`]: () => {
        throw new Error('API 404: {"code":"draft_not_found"}')
      },
    })
    mount()
    await toPreset()
    await userEvent.click(await screen.findByRole('button', { name: ASK }))
    await waitFor(() => expect(advisePosts()).toHaveLength(1))
    await waitFor(() => expect(draftPolls().length).toBeGreaterThan(0))

    // The dead id is dropped rather than polled forever: the panel is back to its unasked state.
    expect(await screen.findByRole('button', { name: ASK })).toBeInTheDocument()
    expect(screen.queryByText('Reading the evidence for a plan proposal…')).toBeNull()
    expect(screen.queryByRole('button', { name: USE })).toBeNull()
  })
})

describe('the plan digest', () => {
  it('matches Python sha256 for a known input', () => {
    expect(sha256Hex(new Uint8Array([0x61, 0x62, 0x63]))).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    )
  })

  it('canonicalises exactly as json.dumps(sort_keys=True, separators=(",", ":")) does', () => {
    // An astral pair, a tab and a control character: the three escapes an ad-hoc serialiser gets wrong.
    expect(canonicalJson({ k: `a\u{1f600}b\tc${String.fromCharCode(1)}` })).toBe('{"k":"a\\ud83d\\ude00b\\tc\\u0001"}')
    expect(canonicalJson({ b: 1, a: null, c: false })).toBe('{"a":null,"b":1,"c":false}')
  })

  it('reproduces EffectivePlan.digest() for a request with untrimmed, non-ASCII text', () => {
    const plan: EffectivePlan = {
      ...planFor('feature', {}),
      request: {
        space: 'default', scope: 'feature', depth: 'Standard', test_strategy: null,
        review_cap: 'adversarial', project_type: 'Brownfield',
        overrides: { 'nfr-requirements': false, 'ci-pipeline': true },
        objective: null, label: null, context: null,
      },
      stages: [
        stage({ slug: 'scope-definition', enabled: true }),
        stage({ slug: 'nfr-requirements', enabled: false }),
        stage({ slug: 'ci-pipeline', enabled: true }),
      ],
      graph_stage_count: 33,
    }
    const digest = planDigest(plan, {
      ...EMPTY_WIZARD,
      objective: '  让未登录的顾客也能完成购买 "quoted"\\path  ',
      label: 'guest-checkout',
      context: '   ',
    })
    expect(digest).toBe('d164475b7852cf9f83196a76a0421bc1464611db58ad3366b6e75918ac6c89cd')
  })

  it('derives a label the way the engine does', () => {
    expect(slugifyObjective('Let signed-out shoppers complete a purchase')).toBe('let-signed-out')
    expect(slugifyObjective('让未登录的顾客完成购买')).toBe('')
  })
})

// ---- the Work step when the gateway cannot see bun (A28) ---- //

const BUN_SEARCHED = [
  '/Users/dev/.bun/bin/bun',
  '/opt/homebrew/bin/bun',
  '/usr/local/bin/bun',
  '/home/linuxbrew/.linuxbrew/bin/bun',
]

/**
 * The Work step alone, so `bunMissing` can be posed without a second `/health` fixture for the whole
 * wizard: the shell context above is mocked once per file, and this is a property of one step's props.
 */
function workStep(bunSearched: string[]) {
  return render(
    <I18nProvider>
      <StepWork
        state={{ ...EMPTY_WIZARD, repo: 'r_1', objective: OBJECTIVE }}
        repos={REPOS.repos}
        repo={REPOS.repos[0] ?? null}
        blocked={null}
        spaces={['default']}
        activeSpace="default"
        derivedLabel="let-signed-out"
        labelValid
        bunMissing
        bunSearched={bunSearched}
        graphStageCount={33}
        intentCount={0}
        onPatch={() => {}}
        onOpenRepos={() => {}}
      />
    </I18nProvider>,
  )
}

describe('the Work step when bun was not found', () => {
  it('says bun was not found, names every location the probe tried, and says what fixes it', () => {
    workStep(BUN_SEARCHED)

    const banner = screen.getByRole('status')
    expect(banner).toHaveTextContent('bun was not found on PATH or in the usual install locations')
    expect(banner).toHaveTextContent('Install bun, or start KiroCrew from a shell whose PATH has bun.')
    for (const location of BUN_SEARCHED) expect(banner).toHaveTextContent(location)
    // launchd gives the desktop gateway a four-entry PATH, so the user this reaches usually has bun
    // installed twice over; telling them it is not installed sent them to reinstall what they had.
    expect(screen.queryByText(/bun is not installed/)).not.toBeInTheDocument()
  })

  it('still says what is wrong and what fixes it when the probe reported no locations', () => {
    workStep([])

    const banner = screen.getByRole('status')
    expect(banner).toHaveTextContent('bun was not found on PATH or in the usual install locations')
    expect(banner).toHaveTextContent('Install bun, or start KiroCrew from a shell whose PATH has bun.')
    expect(screen.queryByText(/Studio looked in:/)).not.toBeInTheDocument()
  })
})
