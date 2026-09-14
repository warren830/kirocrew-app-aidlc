/**
 * The plan composer's three promises: every stage is shown, only what the engine permits can move, and an
 * estimate never pretends to be a count.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '../i18n'
import type { StudioApi } from '../lib/api'
import type { EffectivePlan, PlanStage, RecomposeProposal, Range } from '../lib/types'
import { EstimatePanel } from './EstimatePanel'
import { PlanDiff } from './PlanDiff'
import { PlanMatrix } from './PlanMatrix'
import { RecomposePanel } from './RecomposePanel'

function stage(over: Partial<PlanStage> & { slug: string }): PlanStage {
  return {
    number: '1.1',
    name: 'a stage',
    phase: 'inception',
    execution: 'ALWAYS',
    in_grid: true,
    enabled: true,
    locked: false,
    lock_reason: null,
    gate: true,
    review_class: null,
    reviewer: null,
    per_unit: false,
    produces: [],
    consumes: [],
    depends_on: [],
    conditional_on: null,
    state: null,
    ...over,
  }
}

const turns: Range = { low: 70, high: 110, unit: 'turns', source: 'rule_band', confidence: 'low' }
const secs: Range = { low: 14400, high: 25200, unit: 'secs', source: 'rule_band', confidence: 'medium' }
const elapsed: Range = { low: 172800, high: 345600, unit: 'secs', source: 'assumption', confidence: 'low' }

const STAGES: PlanStage[] = [
  stage({ slug: 'init', number: '0.1', phase: 'initialization', gate: false }),
  stage({ slug: 'ideation-brief', number: '1.1', phase: 'ideation', in_grid: false, enabled: false, execution: 'CONDITIONAL', conditional_on: 'greenfield' }),
  stage({ slug: 'nfr-requirements', number: '2.4', phase: 'inception', produces: ['nfr-requirements.md'] }),
  stage({ slug: 'contract-design', number: '3.1', phase: 'inception', consumes: ['nfr-requirements.md'] }),
  stage({ slug: 'code-generation', number: '4.3', phase: 'construction', per_unit: true, produces: ['units/'], locked: true, lock_reason: 'completed', state: 'completed' }),
  stage({ slug: 'ci-pipeline', number: '5.1', phase: 'operation', locked: true, lock_reason: 'required_by:code-generation' }),
  // A row in the state file that the installed graph no longer contains: the backend sends `execution: ""`
  // and an empty phase for it, which §3.1's `PlanStage` does not admit — hence the cast (see the report).
  stage({ slug: 'legacy-row', number: '', phase: '', execution: '', locked: true, lock_reason: 'not_in_graph' } as unknown as Partial<PlanStage> & { slug: string }),
]

function makePlan(over: Partial<EffectivePlan> = {}): EffectivePlan {
  return {
    request: {
      space: 'default', scope: 'feature', depth: 'Standard', test_strategy: null, review_cap: 'adversarial',
      project_type: null, overrides: {}, objective: null, label: null, context: null,
    },
    scope_meta: null,
    stages: STAGES,
    exact: { stages: 5, gates: 4, artifacts: 12, review_intensity: { none: 1, advisory: 2, adversarial: 2 }, assumes_units: 1 },
    estimate: {
      turns, active_secs: secs, elapsed_secs: elapsed, credits: null, credits_status: 'unavailable',
      source: 'rule_band', confidence: 'low', samples: 0,
      dominant: [{ slug: 'code-generation', share_pct: 40, turns: { ...turns, low: 30, high: 44 } }],
      coverage_lost: [{ slug: 'ideation-brief', artifacts: ['ideation.md'] }],
    },
    diff: [],
    issues: [],
    valid: true,
    products: ['nfr-requirements.md', 'units/'],
    graph_stage_count: 33,
    engine_version: '2.6.2',
    ...over,
  }
}

const mount = (node: React.ReactNode) => render(<I18nProvider>{node}</I18nProvider>)

describe('the stage matrix', () => {
  it('lists every known stage, in phase order, including excluded, conditional and unplaced ones', () => {
    const { container } = mount(<PlanMatrix stages={STAGES} onToggle={() => {}} />)
    // Five engine phases plus the trailing group for a row the installed graph does not contain.
    const legends = [...container.querySelectorAll('fieldset > legend')].map((node) => node.textContent ?? '')
    expect(legends[0]).toContain('Initialization')
    expect(legends[1]).toContain('Ideation')
    expect(legends[2]).toContain('Inception')
    expect(legends[3]).toContain('Construction')
    expect(legends[4]).toContain('Operation')
    expect(legends[5]).toContain('Not in the installed graph')
    // The excluded stage is present and unchecked rather than hidden.
    expect(screen.getByText('ideation-brief')).toBeInTheDocument()
    expect(screen.getAllByRole('checkbox')).toHaveLength(STAGES.length)
    expect(legends[2]).toContain('2 of 2 on')
  })

  it('disables a stage the engine freezes and says why, in the pill and in the description', () => {
    mount(<PlanMatrix stages={STAGES} onToggle={() => {}} showState />)
    const locked = screen.getByRole('checkbox', { name: /code-generation/ })
    expect(locked).toBeDisabled()
    // The short reason is visible beside the slug…
    expect(screen.getByText('already done')).toBeInTheDocument()
    // …and the full sentence is the checkbox's description, not a tooltip only.
    const described = document.getElementById(locked.getAttribute('aria-describedby') ?? '')
    expect(described?.textContent).toContain('already finished or skipped this stage')
    expect(described?.textContent).toContain('on disk: Completed')

    const required = screen.getByRole('checkbox', { name: /ci-pipeline/ })
    expect(required).toBeDisabled()
    expect(screen.getByText('needed by code-generation')).toBeInTheDocument()
  })

  it('only reports a toggle for a stage the engine permits', async () => {
    const onToggle = vi.fn()
    mount(<PlanMatrix stages={STAGES} onToggle={onToggle} />)
    await userEvent.click(screen.getByRole('checkbox', { name: /nfr-requirements/ }))
    expect(onToggle).toHaveBeenCalledWith('nfr-requirements', false)
    onToggle.mockClear()
    // A disabled control cannot fire, which is the point: the engine would refuse the override.
    await userEvent.click(screen.getByRole('checkbox', { name: /code-generation/ }))
    expect(onToggle).not.toHaveBeenCalled()
  })
})

describe('the estimate panel', () => {
  it('labels counts exact and ranges with their source and confidence', () => {
    mount(<EstimatePanel plan={makePlan()} />)
    const exact = screen.getByLabelText('Exact counts')
    expect(within(exact).getByText('5')).toBeInTheDocument()
    expect(within(exact).getByText('exact, 5 of 33 known stages')).toBeInTheDocument()
    expect(within(exact).getByText('1 none · 2 advisory · 2 adversarial')).toBeInTheDocument()

    const estimates = screen.getByLabelText('Estimates with source and confidence')
    expect(within(estimates).getByText('70 – 110')).toBeInTheDocument()
    expect(within(estimates).getByText('range · rule-based band · low confidence · no local history yet')).toBeInTheDocument()
    expect(within(estimates).getByText(/range · assumption · low confidence/)).toBeInTheDocument()
  })

  it('shows credits as unavailable rather than as a number', () => {
    mount(<EstimatePanel plan={makePlan()} />)
    const estimates = screen.getByLabelText('Estimates with source and confidence')
    const credits = within(estimates).getByText('Credits').closest('.studio-stat')
    expect(credits).not.toBeNull()
    expect(credits?.textContent).toContain('Unavailable')
    expect(credits?.textContent).toContain('not zero, not inferred')
    expect(credits?.textContent).not.toMatch(/\b0\b/)
  })

  it('names the dominant stages and what removing them would give up', () => {
    mount(<EstimatePanel plan={makePlan()} />)
    expect(screen.getByText('code-generation — about 40% of the estimated turns')).toBeInTheDocument()
    // It is locked, so the panel says so instead of implying a choice the engine would refuse.
    expect(screen.getByText(/AI-DLC will not let this stage be turned off/)).toBeInTheDocument()
    expect(screen.getByText('ideation-brief is off, so ideation.md will not exist.')).toBeInTheDocument()
  })

  it('says when an artifact count assumed a unit of work', () => {
    mount(<EstimatePanel plan={makePlan()} />)
    expect(screen.getByText(/assumed because no unit exists yet/)).toBeInTheDocument()
  })
})

describe('the plan diff', () => {
  it('shows the unmodified preset when nothing changed', () => {
    mount(<PlanDiff plan={makePlan()} />)
    expect(screen.getByText('No changes — this is the unmodified feature preset.')).toBeInTheDocument()
  })

  it('names the products that disappear and the stages that declare them', () => {
    const plan = makePlan({
      stages: STAGES.map((entry) => (entry.slug === 'nfr-requirements' ? { ...entry, enabled: false } : entry)),
      diff: [{ slug: 'nfr-requirements', from_enabled: true, to_enabled: false, reason: 'override' }],
    })
    mount(<PlanDiff plan={plan} />)
    expect(screen.getByText('− nfr-requirements turned off by you')).toBeInTheDocument()
    expect(screen.getByText('nfr-requirements.md is no longer produced.')).toBeInTheDocument()
    expect(screen.getByText('Declared as an input by contract-design.')).toBeInTheDocument()
  })

  it("attributes a change to the Advisor's proposal only when the wizard says it made it", () => {
    // The wizard passes `origins` for the overrides it copied out of an accepted proposal (design §2.4), so the
    // human reads who turned a stage off. Without `origins` — recompose, a hand-made override — the wording is
    // exactly what it was, because nothing else on the plan changed.
    const plan = makePlan({
      stages: STAGES.map((entry) => (entry.slug === 'nfr-requirements' ? { ...entry, enabled: false } : entry)),
      diff: [{ slug: 'nfr-requirements', from_enabled: true, to_enabled: false, reason: 'override' }],
    })
    const advised = mount(<PlanDiff plan={plan} origins={{ 'nfr-requirements': 'advisor' }} />)
    expect(screen.getByText("− nfr-requirements turned off by the Advisor's proposal")).toBeInTheDocument()
    expect(screen.queryByText('− nfr-requirements turned off by you')).toBeNull()
    advised.unmount()

    mount(<PlanDiff plan={plan} />)
    expect(screen.getByText('− nfr-requirements turned off by you')).toBeInTheDocument()
    expect(screen.queryByText("− nfr-requirements turned off by the Advisor's proposal")).toBeNull()
  })

  it('quotes the engine refusal rather than paraphrasing it', () => {
    const plan = makePlan({
      valid: false,
      issues: [{
        code: 'dependency_missing',
        slugs: ['contract-design'],
        message_key: 'plan.issue.dependency_missing',
        params: { stage: 'contract-design', artifact: 'nfr-requirements.md', producers: ['nfr-requirements'] },
      }],
    })
    mount(<PlanDiff plan={plan} />)
    expect(
      screen.getByText(
        'Nothing selected would produce nfr-requirements.md, which contract-design declares as a required input, so AI-DLC rejects this combination.',
      ),
    ).toBeInTheDocument()
  })
})

function proposal(over: Partial<RecomposeProposal> = {}): RecomposeProposal {
  return {
    intent_key: 'default~250901-guest-checkout',
    current_stage: '2.4 nfr-requirements',
    skip: [],
    add: [],
    plan: makePlan(),
    argv_preview: ['aidlc-utility.ts', 'recompose'],
    allowed: false,
    refusals: [],
    ...over,
  }
}

describe('the recompose panel', () => {
  function api(over: Partial<StudioApi> = {}) {
    return {
      recomposePreview: vi.fn(async () => ({ proposal: proposal(), proposal_digest: 'digest-0' })),
      recompose: vi.fn(async () => ({ ok: true as const, transaction_id: 'tx_1', result: { ok: true } as never, intent: null })),
      ...over,
    } as unknown as StudioApi
  }

  it('previews on open and restricts changes to the stages the engine has not frozen', async () => {
    const client = api()
    mount(<RecomposePanel api={client} repoId="r_1" intentKey="default~i" intentLabel="250901-guest-checkout" onClose={() => {}} />)
    await waitFor(() => expect(screen.getByText(/cursor is at 2.4 nfr-requirements/)).toBeInTheDocument())
    expect(client.recomposePreview).toHaveBeenCalledWith('r_1', 'default~i', { skip: [], add: [] })
    expect(screen.getByRole('checkbox', { name: /code-generation/ })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /nfr-requirements/ })).toBeEnabled()
    // Nothing selected yet, so there is nothing to apply.
    expect(screen.getByRole('button', { name: /Apply the change/ })).toBeDisabled()
    expect(screen.getByText(/Nothing is selected to change yet/)).toBeInTheDocument()
  })

  it('re-previews a flip as a skip and shows every refusal', async () => {
    const refused = proposal({
      skip: ['nfr-requirements'],
      allowed: false,
      refusals: [{ code: 'recompose_not_allowed', slugs: [], message_key: 'plan.issue.recompose_not_allowed', params: { status: 'Parked', required_status: 'Running', autonomy_mode: null } }],
    })
    const preview = vi
      .fn()
      .mockResolvedValueOnce({ proposal: proposal(), proposal_digest: 'digest-0' })
      .mockResolvedValue({ proposal: refused, proposal_digest: 'digest-1' })
    const client = api({ recomposePreview: preview as unknown as StudioApi['recomposePreview'] })
    mount(<RecomposePanel api={client} repoId="r_1" intentKey="default~i" intentLabel="i" onClose={() => {}} />)
    await waitFor(() => expect(screen.getByRole('checkbox', { name: /nfr-requirements/ })).toBeEnabled())
    await userEvent.click(screen.getByRole('checkbox', { name: /nfr-requirements/ }))
    await waitFor(() => expect(preview).toHaveBeenLastCalledWith('r_1', 'default~i', { skip: ['nfr-requirements'], add: [] }))
    expect(await screen.findByText(/AI-DLC recomposes only an intent whose Status is Running/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Apply the change/ })).toBeDisabled()
  })

  it('confirms with the exact argv and applies against the previewed digest', async () => {
    const allowed = proposal({ skip: ['nfr-requirements'], allowed: true, argv_preview: ['aidlc-utility.ts', 'recompose', '--skip', 'nfr-requirements'] })
    const client = api({
      recomposePreview: vi.fn(async () => ({ proposal: allowed, proposal_digest: 'digest-7' })) as unknown as StudioApi['recomposePreview'],
    })
    const onApplied = vi.fn()
    mount(<RecomposePanel api={client} repoId="r_1" intentKey="default~i" intentLabel="i" onClose={() => {}} onApplied={onApplied} />)
    const apply = await screen.findByRole('button', { name: /Apply the change/ })
    await waitFor(() => expect(apply).toBeEnabled())
    await userEvent.click(apply)
    // The confirmation shows the command verbatim, not a description of it.
    expect(screen.getByText('aidlc-utility.ts recompose --skip nfr-requirements')).toBeInTheDocument()
    expect(client.recompose).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Run it' }))
    await waitFor(() =>
      expect(client.recompose).toHaveBeenCalledWith('r_1', 'default~i', {
        skip: ['nfr-requirements'],
        add: [],
        proposal_digest: 'digest-7',
      }),
    )
    expect(await screen.findByText('AI-DLC applied the change to the plan.')).toBeInTheDocument()
    expect(onApplied).toHaveBeenCalled()
  })
})
