/**
 * What the six decision templates must never get wrong.
 *
 * These are not snapshot tests. Every assertion below is a rule from the PRD or the visual spec whose
 * violation would be a fabricated decision, an invented control, or a number Studio cannot observe:
 *
 *  - a template never renders a decision control of its own (the bar and the confirmation are the
 *    Action Center's, and a second sender could not carry the at-most-once guard);
 *  - unevaluated acceptance criteria are not reported as unmet;
 *  - question prompts, option labels and recorded answers are the engine's bytes, and degraded mode
 *    offers no controls at all;
 *  - an unprovable delivery is announced assertively and its three checks are printed before anything
 *    else;
 *  - an install conflict offers no overwrite;
 *  - credits are `Unavailable`, never zero;
 *  - the Advisor never runs on a template's initiative, a draft prepared under the repository's grant
 *    fills the answers once and labels them, and a draft whose evidence moved is refused.
 */

import { useState } from 'react'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { I18nProvider, makeI18n } from '../i18n'
import { useStudioApi } from '../lib/api'
import { EMPTY_DRAFT, type ActionDraft } from '../actions/DetailShell'
import { wireTextFor } from '../actions/useSubmit'
import type { TemplateProps } from './DecisionControls'
import { EMPTY_ROUTE, type RoutePatch, type StudioRoute } from '../lib/route'
import type {
  ActionCard, ActionDetailResponse, ActionType, AdvisorDraft, ArtifactMeta, DecisionSpec, DraftResult,
  PreviewEntry, Question, QuestionsView, ReviewFinding,
} from '../lib/types'
import { apiCalls, setApiRoutes } from '../test/stubs/app-sdk'
import { DetailShell } from '../actions/DetailShell'
import { DecisionTemplate, templateFor } from './TemplateRegistry'
import { GateTemplate } from './GateTemplate'
import { QuestionsTemplate } from './QuestionsTemplate'
import { RecoveryTemplate } from './RecoveryTemplate'
import { FailureTemplate } from './FailureTemplate'
import { InstallConflictTemplate } from './InstallConflictTemplate'
import { BudgetStopTemplate } from './BudgetStopTemplate'
import { MissingInputTemplate } from './MissingInputTemplate'

const BASE = '/api/apps/aidlc-studio'
const en = makeI18n('en-US')

const spec = (decision: DecisionSpec['decision'], requires: DecisionSpec['requires'] = []): DecisionSpec => ({
  decision,
  label_key: `decision.${decision}.label`,
  lane: decision === 'approve' || decision === 'request_changes' ? 'human_lane' : 'studio_only',
  wire_text_template: null,
  requires,
})

function card(over: Partial<ActionCard> = {}): ActionCard {
  const type: ActionType = over.type ?? 'gate'
  return {
    action_id: 'a_1',
    type,
    queue_type: type,
    status: 'Queued',
    status_generation: 1,
    source: 'aidlc',
    risk_class: 'human_lane',
    severity: 'blocking',
    priority_group: 2,
    repo: { repo_id: 'r_1', label: 'checkout-web', canonical_path: '/work/checkout-web' },
    space: 'default',
    intent: { intent_dir: '250901-guest', intent_key: 'default~250901-guest', slug: 'guest-checkout', uuid: null, title: null },
    stage: { slug: 'functional-design', number: '2.3', name: null, phase: 'inception', unit: null },
    headline: { key: 'action.gate.headline', params: {} },
    consequence: null,
    waiting_since: '2026-09-04T09:00:00Z',
    created_at: '2026-09-04T09:00:00Z',
    updated_at: '2026-09-04T09:00:00Z',
    primary: { decision: 'approve', label_key: 'decision.approve.label' },
    decisions: [spec('approve'), spec('request_changes', ['feedback'])],
    captured: {
      state_hash: 'sha-state', boundary_token: 'tok', question_digest: null, stage_attempt: 1,
      evidence_digest: 'sha-ev', is_active: true, captured_at: '2026-09-04T09:00:00Z', stable: true,
    },
    evidence: {
      state: {
        relpath: 'aidlc-state.md', sha256: 'abcdef0123456789', size: 100, mtime_ns: 1_757_000_000_000_000_000,
        current_stage: 'functional-design', status: 'awaiting_approval', lifecycle_phase: 'inception',
        revision_count: 2, stable: true,
      },
      audit: { shards: [{ relpath: 'audit/000.md', size: 10, truncated: false }], last_event: null, boundary_event: null, complete: true },
      directive: null,
      artifacts: [],
      review: null,
      acceptance_criteria: [],
      findings: [],
      session: {
        slot_key: 'aidlc-r1-guest', session_key: 'ses_1c4', running: false, stop_state: 'idle',
        last_turn_ts: null, queue_depth: 0, busy_reasons: [],
      },
      questions: null,
      git: null,
      markers: { human_turn_at: null, engine_touch_at: null, turn_counter: null, goal_stop_present: false, recovery: null },
      presence_baseline: null,
      presence: null,
      cursor_readback: null,
    },
    delivery: {
      lane: null, delivery_id: null, slot_key: null, session_key: null, wire_text: null, host: null,
      delivering_at: null, delivered_at: null, queued_at: null, deadline_at: null, receipt: null,
      outcome: null, delivery_confirmed: false, boot_id_unchanged: null, transcript_row: null,
      slot_ran_since: null, disk_baseline_unchanged: null,
    },
    resolution: { kind: null, resolved_at: null, evidence: null, reason: null },
    failure: null,
    install: null,
    budget: null,
    advisor: { latest_draft_id: null, status: null },
    deep_link: '/apps/aidlc-studio?view=actions&action=a_1',
    dedupe_key: null,
    human_text_present: false,
    acknowledged_evidence_sha256: 'sha-ack',
    ...over,
  }
}

const question = (over: Partial<Question> = {}): Question => ({
  index: 1,
  prompt: 'Which durability guarantee does the cart need?',
  options: [
    { letter: 'A', text: 'At-least-once delivery with idempotent writes', is_other: false },
    { letter: 'B', text: 'Exactly-once via a transactional outbox', is_other: false },
    { letter: 'X', text: 'Other', is_other: true },
  ],
  multi_select: false,
  answer: null,
  answered: false,
  required: true,
  ...over,
})

const questionsView = (over: Partial<QuestionsView> = {}): QuestionsView => ({
  relpath: 'aidlc/spaces/default/250901-guest/nfr-requirements-questions.md',
  sha256: 'sha-q',
  stage: 'nfr-requirements',
  unit: null,
  questions: [question()],
  summary_confirmation: null,
  plan_approval: null,
  pending_count: 1,
  pending_checkpoint: null,
  mode: 'structured',
  host_card: null,
  ...over,
})

const artifact = (over: Partial<ArtifactMeta> = {}): ArtifactMeta => ({
  artifact_id: 'art_1',
  relpath: 'aidlc/spaces/default/250901-guest/functional-design.md',
  name: 'functional-design.md',
  stage: 'functional-design',
  phase: 'inception',
  unit: null,
  size: 2048,
  mtime: '2026-09-04T08:00:00Z',
  sha256: 'sha-art',
  kind: 'artifact',
  renderable: true,
  ...over,
})

const finding = (over: Partial<ReviewFinding> = {}): ReviewFinding => ({
  level: 'blocker',
  title: 'Decline path is unspecified',
  quote: 'The design does not say what happens when the payment declines.',
  anchor: 'Decline path',
  reviewer: 'architecture-reviewer',
  iteration: 2,
  ...over,
})

const driftEntry = (over: Partial<PreviewEntry> = {}): PreviewEntry => ({
  path: '.kiro/tools/data/stage-graph.json',
  ownership: 'framework',
  action: 'owned_modified',
  live_sha256: 'live0123456789abcdef',
  payload_sha256: 'payload0123456789ab',
  receipt_sha256: 'receipt0123456789ab',
  size: 4096,
  fragment_key: null,
  diff: '@@ -1 +1 @@\n-old line\n+new line\n',
  blocking: true,
  ...over,
})

const detail = (over: Partial<ActionDetailResponse> = {}): ActionDetailResponse => ({
  action: card(),
  transitions: [],
  drafts: [],
  ...over,
})

const advisorDraft = (over: Partial<AdvisorDraft> = {}): AdvisorDraft => ({
  draft_id: 'd_1',
  action_id: 'a_1',
  kind: 'gate_analysis',
  status: 'ready',
  request: { action_id: 'a_1', kind: 'gate_analysis', question_index: null, locale: 'en-US', auto: false },
  result: {
    verdict: 'request_changes_recommended',
    summary: 'The decline path is not covered by the design.',
    suggested_answers: [],
    evidence: ['functional-design.md has no decline section'],
    assumptions: ['The reviewer read the current revision'],
    alternatives: ['Approve and open a follow-up intent'],
    confidence: 'medium',
    needs_your_decision: ['Whether a follow-up intent is acceptable'],
    drafted_feedback: 'Please describe the decline path.',
  },
  error: null,
  created_at: '2026-09-04T09:00:00Z',
  updated_at: '2026-09-04T09:00:00Z',
  expires_at: '2026-09-05T09:00:00Z',
  neutrality: { ok: true, changed: [] },
  ...over,
})

interface MountResult {
  patches: RoutePatch[]
  draft: () => ActionDraft
  unmount: () => void
}

/**
 * Mount one template the way `DetailShell` mounts it: the same props, a real `useStudioApi()`, and the
 * draft in React state — because a template's inputs are controlled, and a harness that did not
 * re-render on `setDraft` would only ever record the last keystroke.
 */
function mount(
  Template: (props: TemplateProps) => JSX.Element | null,
  cardValue: ActionCard,
  options: { draft?: Partial<ActionDraft>; refreshing?: boolean; route?: Partial<StudioRoute>; detail?: ActionDetailResponse | null } = {},
): MountResult {
  const patches: RoutePatch[] = []
  const initial: ActionDraft = { ...EMPTY_DRAFT, ...options.draft }
  let current: ActionDraft = initial
  const route: StudioRoute = { ...EMPTY_ROUTE, action: cardValue.action_id, ...options.route }

  function Harness() {
    const api = useStudioApi()
    const [draft, setDraftState] = useState<ActionDraft>(initial)
    current = draft
    return (
      <Template
        card={cardValue}
        detail={options.detail ?? detail({ action: cardValue })}
        draft={draft}
        setDraft={(patch) => setDraftState((value) => ({ ...value, ...patch }))}
        refreshing={options.refreshing ?? false}
        api={api}
        route={route}
        go={(patch) => patches.push(patch)}
        reload={() => {}}
      />
    )
  }

  const view = render(
    <I18nProvider>
      <Harness />
    </I18nProvider>,
  )
  return { patches, draft: () => current, unmount: view.unmount }
}

describe('every template', () => {
  it('covers every action type, and routes an unprovable gate to the recovery body', () => {
    const types: ActionType[] = [
      'gate', 'revision', 'question', 'missing_input', 'recovery', 'delivery_uncertain', 'failure',
      'circuit_breaker', 'install_conflict', 'budget_stop', 'run', 'resume', 'force_stop', 'prepare_commit',
    ]
    for (const type of types) expect(typeof templateFor(card({ type }))).toBe('function')
    // A gate whose delivery is unproven must not show the gate body again: the decision is now about the
    // delivery, and re-offering the artifact is how a stage gets approved twice.
    expect(templateFor(card({ type: 'gate', queue_type: 'delivery_uncertain', status: 'DeliveryUncertain' }))).toBe(
      RecoveryTemplate,
    )
  })

  it('renders no decision control of its own', () => {
    setApiRoutes({})
    mount(DecisionTemplate, card({ decisions: [spec('approve'), spec('request_changes', ['feedback'])] }))
    // The bar owns the buttons. A body that rendered "Approve" would be a second sender.
    expect(screen.queryByRole('button', { name: en.t('decision.approve.label') })).toBeNull()
  })

  it('names the type, repo, intent, stage and state for a screen reader', () => {
    setApiRoutes({})
    mount(DecisionTemplate, card())
    const region = screen.getByLabelText(/Approval gate decision/i)
    expect(region).toBeInTheDocument()
    expect(region.getAttribute('aria-label')).toContain('checkout-web')
    expect(region.getAttribute('aria-label')).toContain('guest-checkout')
    expect(region.getAttribute('aria-label')).toContain('2.3 functional-design')
    expect(region.getAttribute('aria-label')).toContain(en.t('enum.actionStatus.Queued'))
  })
})

describe('the plug-in wiring', () => {
  /**
   * The registry is the whole reason the Decision tab is not empty.
   *
   * `DetailShell` looks a body up by `card.type` through `registerActionTemplate`, which this area calls
   * at module scope — so importing the registry has to be enough. Without this test the failure mode is
   * silent: every card would render "no template in this build" and every assertion above would still
   * pass, because they mount the templates directly.
   */
  it('renders a gate body inside the real detail pane', async () => {
    const value = card({
      evidence: {
        ...card().evidence,
        acceptance_criteria: [{ text: 'Every rejected payment has a documented path', met: null, why_key: null }],
      },
    })
    setApiRoutes({
      [`GET ${BASE}/actions/a_1`]: () => detail({ action: value }),
    })

    function Harness() {
      const api = useStudioApi()
      return (
        <DetailShell
          actionId="a_1"
          queueCard={value}
          api={api}
          route={{ ...EMPTY_ROUTE, action: 'a_1' }}
          go={() => {}}
          groupedAnswers={false}
          onQueueChanged={() => {}}
        />
      )
    }
    render(
      <I18nProvider>
        <Harness />
      </I18nProvider>,
    )

    await waitFor(() =>
      expect(screen.getByText(en.t('template.gate.criteriaUnknown', { total: 1 }))).toBeInTheDocument(),
    )
    expect(screen.queryByText(/not built|no template/i)).toBeNull()
    // And the bar the Action Center owns is the only place a decision can start.
    expect(screen.getByRole('button', { name: new RegExp(en.t('decision.approve.label')) })).toBeInTheDocument()
  })
})

describe('gate template', () => {
  it('reports unevaluated acceptance criteria as unevaluated, not unmet', () => {
    setApiRoutes({})
    mount(
      GateTemplate,
      card({
        evidence: {
          ...card().evidence,
          acceptance_criteria: [
            { text: 'Every rejected payment has a documented path', met: null, why_key: null },
            { text: 'The design names its consumers', met: null, why_key: null },
          ],
        },
      }),
    )
    expect(screen.getByText(en.t('template.gate.criteriaUnknown', { total: 2 }))).toBeInTheDocument()
    expect(screen.queryByText(/0 of 2 met/)).toBeNull()
    // `data-met` is how the row states "not evaluated" for a test; on screen it is a neutral glyph plus
    // the header sentence, never the warning glyph that means "failed".
    expect(document.querySelectorAll('.studio-crit > li[data-met="null"]').length).toBe(2)
  })

  it('quotes reviewer findings verbatim and offers the artifact anchor', async () => {
    setApiRoutes({
      [`GET ${BASE}/repos/r_1/intents/default~250901-guest/artifacts/art_1`]: () => ({
        artifact: artifact(),
        content: '# Functional design\n\nBody.',
        encoding: 'utf-8',
        truncated: false,
        review: null,
        toc: [{ level: 1, text: 'Functional design', anchor: 'functional-design' }],
        prior: { available: false, source: null, diff: null },
      }),
    })
    const value = card({
      evidence: {
        ...card().evidence,
        artifacts: [artifact()],
        review: { verdict: 'CHANGES_REQUESTED', findings: [finding()], reviewer: 'architecture-reviewer', review_class: 'adversarial', iteration: 2 },
      },
    })
    const { patches } = mount(GateTemplate, value)

    expect(screen.getByText('Decline path is unspecified')).toBeInTheDocument()
    expect(screen.getByText(/what happens when the payment declines/)).toBeInTheDocument()
    // The reviewer's verdict word is AI-DLC's, shown as-is.
    expect(screen.getByText('CHANGES_REQUESTED')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: en.t('review.inArtifact') }))
    // The finding's raw anchor is slugified, because the route drops anything that is not `h-<slug>`.
    expect(patches.at(-1)).toEqual({ tab: 'artifacts', artifact: 'art_1', anchor: 'h-decline-path' })
  })

  it('says a gate produced no artifact instead of showing an empty pane', () => {
    setApiRoutes({})
    mount(GateTemplate, card())
    expect(screen.getByText(en.t('template.gate.noArtifactBody'))).toBeInTheDocument()
  })

  it('writes the change-request note into the draft and never touches the artifact', async () => {
    setApiRoutes({})
    const { draft } = mount(GateTemplate, card())
    const box = screen.getByLabelText(en.t('template.gate.feedbackLabel'))
    await userEvent.type(box, 'Cover the decline path.')
    expect(draft().feedback).toBe('Cover the decline path.')
    expect(screen.getByText(en.t('template.gate.feedbackRouting'))).toBeInTheDocument()
  })

  it('offers no change-request box when the card does not offer the decision', () => {
    setApiRoutes({})
    mount(GateTemplate, card({ decisions: [spec('approve')] }))
    expect(screen.queryByLabelText(en.t('template.gate.feedbackLabel'))).toBeNull()
  })
})

describe('questions template', () => {
  const questionCard = (view: QuestionsView = questionsView()) =>
    card({
      type: 'question',
      headline: { key: 'action.question.headline', params: {} },
      decisions: [spec('answers', ['answers'])],
      primary: { decision: 'answers', label_key: 'decision.answers.label' },
      evidence: { ...card().evidence, questions: view },
    })

  it('renders the ordered group with its prompts and option labels verbatim', () => {
    setApiRoutes({})
    mount(QuestionsTemplate, questionCard())
    expect(screen.getByText('1. Which durability guarantee does the cart need?')).toBeInTheDocument()
    expect(screen.getByText('At-least-once delivery with idempotent writes')).toBeInTheDocument()
    expect(screen.getByText('Exactly-once via a transactional outbox')).toBeInTheDocument()
    // The file's own path, so the user can see what Studio is not editing.
    expect(
      screen.getByText(en.t('template.questions.neverEdits', { path: questionsView().relpath })),
    ).toBeInTheDocument()
  })

  it('shows an audit checkpoint without a host question card and keeps exact option text', async () => {
    setApiRoutes({})
    const auditView = questionsView({
      relpath: 'aidlc/spaces/default/intents/demo/audit/000.md', mode: 'structured', host_card: null,
      origin: { kind: 'audit', event: 'DECISION_RECORDED', shard: '000.md', pos: 42,
        timestamp: '2026-09-10T08:00:00Z', stage: 'requirements-analysis', unit: null,
        workflow: null, attempt_generation: null, decision_sha256: 'decision-hash' },
      questions: [question({ prompt: 'Learnings: anything to add?', options: [
        { letter: 'A', text: 'Nothing to add', is_other: false },
        { letter: 'B', text: 'Add a note', is_other: false },
      ] })],
    })
    const { draft } = mount(QuestionsTemplate, questionCard(auditView))
    expect(screen.getByText(en.t('template.questions.auditSource'))).toBeInTheDocument()
    const choice = screen.getByRole('radio', { name: /Nothing to add/ })
    expect(choice).toBeEnabled()
    await userEvent.click(choice)
    expect(draft().answers['1']).toEqual({ option_letters: ['A'], free_text: null })
  })

  it('uses radios for single select and checkboxes for multi select', () => {
    setApiRoutes({})
    const first = mount(QuestionsTemplate, questionCard())
    expect(screen.getAllByRole('radio').length).toBe(3)
    expect(screen.queryAllByRole('checkbox').length).toBe(0)

    first.unmount()
    mount(QuestionsTemplate, questionCard(questionsView({ questions: [question({ multi_select: true })] })))
    expect(screen.getAllByRole('checkbox').length).toBe(3)
  })

  const noteView = () => questionsView({
    origin: { kind: 'audit', event: 'DECISION_RECORDED', shard: '000.md', pos: 42,
      timestamp: '2026-09-10T08:00:00Z', stage: 'requirements-analysis', unit: null,
      workflow: null, attempt_generation: null, decision_sha256: 'note-decision' },
    questions: [question({ prompt: 'Learnings note: what should I record for next time?',
      options: [{ letter: 'A', text: 'Free-text note', is_other: true }] })],
  })

  it('collects a blank audit note as text, never sends the placeholder, and allows clearing it', async () => {
    setApiRoutes({})
    const view = noteView()
    const { draft } = mount(QuestionsTemplate, questionCard(view))
    const input = screen.getByRole('textbox', { name: /Learnings note/ })
    expect(input).toHaveValue('')
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
    expect(draft().answers).toEqual({})
    const wire = () => wireTextFor({ decision: 'answers', answers: [
      { index: 1, ...(draft().answers['1'] ?? { option_letters: [], free_text: null }) },
    ] }, view, false)
    await userEvent.type(input, 'Free-text note')
    expect(wire()).toBeNull()
    expect(screen.queryByText(en.t('template.questions.drafted'))).not.toBeInTheDocument()
    await userEvent.clear(input)
    const note = 'Keep punctuation, "quotes", 中文.\nKeep the second line too.'
    await userEvent.type(input, note)
    expect(wire()).toBe(note)
    expect(screen.getByText(en.t('template.questions.drafted'))).toBeInTheDocument()
    await userEvent.clear(input)
    expect(wire()).toBeNull()
    expect(apiCalls.filter((call) => call.method !== 'GET')).toHaveLength(0)
  })

  it.each(['Processing', 'ResolvedNoTransition'] as const)(
    'keeps %s questions read-only and hides ready-to-send and draft controls',
    (status) => {
      setApiRoutes({})
      const value = { ...questionCard(noteView()), status }
      mount(QuestionsTemplate, value, {
        draft: { answers: { '1': { option_letters: ['A'], free_text: 'User note' } } },
      })
      expect(screen.getByRole('textbox', { name: /Learnings note/ })).toBeDisabled()
      expect(screen.queryByText(en.t('template.questions.drafted'))).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /Draft/ })).not.toBeInTheDocument()
      expect(apiCalls.filter((call) => call.method !== 'GET')).toHaveLength(0)
    },
  )

  it('selects nothing until the user does, and stores the file’s own letters', async () => {
    setApiRoutes({})
    const { draft } = mount(QuestionsTemplate, questionCard())
    for (const radio of screen.getAllByRole('radio')) expect(radio).not.toBeChecked()
    await userEvent.click(screen.getByRole('radio', { name: /At-least-once/ }))
    expect(draft().answers['1']).toEqual({ option_letters: ['A'], free_text: null })
  })

  it('reveals the Other box only when Other is chosen, and keeps the text', async () => {
    setApiRoutes({})
    const { draft } = mount(QuestionsTemplate, questionCard())
    expect(screen.queryByLabelText(en.t('template.questions.otherLabel', { index: 1 }))).toBeNull()
    await userEvent.click(screen.getByRole('radio', { name: /Other/ }))
    const box = screen.getByLabelText(en.t('template.questions.otherLabel', { index: 1 }))
    await userEvent.type(box, 'Use the existing worker queue.')
    expect(draft().answers['1']).toEqual({ option_letters: ['X'], free_text: 'Use the existing worker queue.' })
  })

  it('shows an answered question as evidence, with no control that could overwrite it', () => {
    setApiRoutes({})
    mount(
      QuestionsTemplate,
      questionCard(
        questionsView({
          questions: [question({ answered: true, answer: 'Exactly-once via a transactional outbox' })],
          pending_count: 0,
        }),
      ),
    )
    expect(screen.getByText('Exactly-once via a transactional outbox')).toBeInTheDocument()
    expect(screen.queryAllByRole('radio').length).toBe(0)
    expect(screen.queryAllByRole('checkbox').length).toBe(0)
  })

  it('does not offer to draft answers when only the summary checkpoint remains', () => {
    setApiRoutes({})
    mount(QuestionsTemplate, questionCard(questionsView({
      questions: [question({ answered: true, answer: 'At-least-once' })],
      pending_count: 0,
      pending_checkpoint: 'summary_confirmation',
      summary_confirmation: { kind: 'summary_confirmation', present: true, answered: false,
        answer: null, options: ['Looks correct', 'Request changes'] },
    })))
    expect(screen.queryByRole('button', { name: en.t('advisor.action.question_draft') })).not.toBeInTheDocument()
    expect(screen.getByText(en.t('template.questions.checkpoint.summary_confirmation'))).toBeInTheDocument()
  })

  it('states degraded mode, disables every input and points at the conversation', async () => {
    setApiRoutes({})
    const { patches } = mount(QuestionsTemplate, questionCard(questionsView({ mode: 'degraded' })))
    expect(screen.getByText(en.t('template.questions.degradedBody'))).toBeInTheDocument()
    for (const radio of screen.getAllByRole('radio')) expect(radio).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: en.t('template.questions.openConversation') }))
    expect(patches.at(-1)).toEqual({ tab: 'conversation' })
  })

  it('asks the Advisor about one question, with that question’s index', async () => {
    const calls: unknown[] = []
    setApiRoutes({
      [`POST ${BASE}/advisor/draft`]: (body) => {
        calls.push(body)
        return { ok: true, draft: advisorDraft({ kind: 'question_explain', status: 'queued', result: null }) }
      },
      [`GET ${BASE}/advisor/drafts/d_1`]: () => ({ draft: advisorDraft({ kind: 'question_explain', status: 'queued', result: null }) }),
    })
    mount(QuestionsTemplate, questionCard())
    await userEvent.click(screen.getByRole('button', { name: en.t('advisor.action.question_explain') }))
    await waitFor(() => expect(calls.length).toBe(1))
    expect(calls[0]).toMatchObject({ action_id: 'a_1', kind: 'question_explain', question_index: 1 })
  })

  /**
   * A `question_draft` the reconciler prepared under the repository's grant (FR-ADV-010/011).
   *
   * `request.auto` is the only thing that licenses a fill, so every test below states it explicitly rather
   * than inheriting it: a fixture that quietly said `true` would make the clicked path untested.
   */
  const preparedDraft = (
    over: Partial<AdvisorDraft> = {},
    suggested: DraftResult['suggested_answers'] = [
      { question_index: 1, answer: 'Exactly-once via a transactional outbox', option_letters: ['B'] },
    ],
  ): AdvisorDraft =>
    advisorDraft({
      kind: 'question_draft',
      request: {
        action_id: 'a_1', kind: 'question_draft', question_index: null, locale: 'en-US', auto: true,
      },
      result: {
        ...(advisorDraft().result as DraftResult),
        suggested_answers: suggested,
        drafted_feedback: null,
      },
      ...over,
    })

  /** The card as the detail read hands it over: the drafts it already has, plus anything typed so far. */
  const mountWithDrafts = (value: ActionCard, drafts: AdvisorDraft[], typed: Partial<ActionDraft> = {}) =>
    mount(QuestionsTemplate, value, { detail: detail({ action: value, drafts }), draft: typed })

  it('fills in a draft the server prepared, says whose answers they are, and sends nothing', async () => {
    const prepared = preparedDraft()
    const value = questionCard()
    setApiRoutes({ [`GET ${BASE}/advisor/drafts/d_1`]: () => ({ draft: prepared }) })
    const { draft } = mountWithDrafts(value, [prepared])

    await waitFor(() => expect(draft().answers['1']).toEqual({ option_letters: ['B'], free_text: null }))
    expect(screen.getByRole('radio', { name: /Exactly-once/ })).toBeChecked()
    // Attributed and reversible, because the user never asked for these words.
    expect(screen.getByText(en.t('advisor.prefilled'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: en.t('advisor.prefilledClear') })).toBeInTheDocument()
    expect(draft().advisorApplied).toBe('d_1')
    // And nothing left the browser: the reconciler asked for this draft, and answering is still a submit
    // the user has to make from the bar.
    expect(apiCalls.filter((call) => call.method !== 'GET')).toEqual([])
  })

  it('leaves a draft the user asked for alone, and keeps its explicit apply button', async () => {
    const clicked = preparedDraft({
      request: {
        action_id: 'a_1', kind: 'question_draft', question_index: null, locale: 'en-US', auto: false,
      },
    })
    const value = questionCard()
    setApiRoutes({ [`GET ${BASE}/advisor/drafts/d_1`]: () => ({ draft: clicked }) })
    const { draft } = mountWithDrafts(value, [clicked])

    const apply = await waitFor(() => screen.getByRole('button', { name: en.t('advisor.applyPicks') }))
    expect(draft().answers['1']).toBeUndefined()
    expect(screen.queryByText(en.t('advisor.prefilled'))).toBeNull()
    // The button is the human's own instruction, and it is the only path that may replace what is there.
    await userEvent.click(apply)
    expect(draft().answers['1']).toEqual({ option_letters: ['B'], free_text: null })
  })

  it('fills the empty question and leaves the one the user already answered exactly as it was', async () => {
    const value = questionCard(
      questionsView({ questions: [question(), question({ index: 2 })], pending_count: 2 }),
    )
    const prepared = preparedDraft({}, [
      { question_index: 1, answer: 'Exactly-once via a transactional outbox', option_letters: ['B'] },
      { question_index: 2, answer: 'Exactly-once via a transactional outbox', option_letters: ['B'] },
    ])
    setApiRoutes({ [`GET ${BASE}/advisor/drafts/d_1`]: () => ({ draft: prepared }) })
    const { draft } = mountWithDrafts(value, [prepared], {
      answers: { '1': { option_letters: ['A'], free_text: null } },
    })

    await waitFor(() => expect(draft().answers['2']).toEqual({ option_letters: ['B'], free_text: null }))
    // The user was reading question 1 while the server was drafting it. Their pick is the answer.
    expect(draft().answers['1']).toEqual({ option_letters: ['A'], free_text: null })
  })

  it('announces nothing when there was nothing left for it to fill in', async () => {
    const prepared = preparedDraft()
    const value = questionCard()
    setApiRoutes({ [`GET ${BASE}/advisor/drafts/d_1`]: () => ({ draft: prepared }) })
    const { draft } = mountWithDrafts(value, [prepared], {
      answers: { '1': { option_letters: ['A'], free_text: null } },
    })

    await waitFor(() =>
      expect(screen.getByText('The decline path is not covered by the design.')).toBeInTheDocument(),
    )
    expect(draft().answers['1']).toEqual({ option_letters: ['A'], free_text: null })
    // A notice with no filled answer under it would teach the user to distrust the notice.
    expect(screen.queryByText(en.t('advisor.prefilled'))).toBeNull()
    expect(draft().advisorApplied).toBeNull()
  })

  it('fills nothing from a draft whose evidence moved while it was being read', async () => {
    const prepared = preparedDraft({ neutrality: { ok: false, changed: ['question_digest'] } })
    const value = questionCard()
    setApiRoutes({ [`GET ${BASE}/advisor/drafts/d_1`]: () => ({ draft: prepared }) })
    const { draft } = mountWithDrafts(value, [prepared])

    await waitFor(() => expect(screen.getByText(en.t('advisor.evidenceMoved'))).toBeInTheDocument())
    expect(draft().answers['1']).toBeUndefined()
    expect(draft().advisorApplied).toBeNull()
  })

  it('fills nothing on a degraded card, whose inputs Studio cannot stand behind', async () => {
    const prepared = preparedDraft()
    const value = questionCard(questionsView({ mode: 'degraded' }))
    setApiRoutes({ [`GET ${BASE}/advisor/drafts/d_1`]: () => ({ draft: prepared }) })
    const { draft } = mountWithDrafts(value, [prepared])

    await waitFor(() =>
      expect(screen.getByText('The decline path is not covered by the design.')).toBeInTheDocument(),
    )
    expect(draft().answers['1']).toBeUndefined()
    expect(screen.queryByText(en.t('advisor.prefilled'))).toBeNull()
  })

  it('clears exactly what it filled in, and no later render puts it back', async () => {
    const prepared = preparedDraft()
    const value = questionCard()
    setApiRoutes({ [`GET ${BASE}/advisor/drafts/d_1`]: () => ({ draft: prepared }) })
    const { draft } = mountWithDrafts(value, [prepared])
    await waitFor(() => expect(draft().answers['1']).toBeTruthy())

    await userEvent.click(screen.getByRole('button', { name: en.t('advisor.prefilledClear') }))
    expect(draft().answers['1']).toBeUndefined()
    expect(screen.getByRole('radio', { name: /Exactly-once/ })).not.toBeChecked()
    // `advisorApplied` still names the draft on purpose: that is the only thing stopping the fill from
    // undoing the clear on the very next render. The notice goes anyway, because it now stands over
    // nothing — and an action that does nothing is worse than no action at all.
    expect(draft().advisorApplied).toBe('d_1')
    expect(screen.queryByText(en.t('advisor.prefilled'))).toBeNull()
    await userEvent.click(screen.getByRole('radio', { name: /At-least-once/ }))
    expect(draft().answers['1']).toEqual({ option_letters: ['A'], free_text: null })
  })

  it('never fills one card’s form from the draft of the card the user just left', async () => {
    const prepared = preparedDraft()
    const first = questionCard()
    const second = { ...questionCard(), action_id: 'a_2' }
    setApiRoutes({ [`GET ${BASE}/advisor/drafts/d_1`]: () => ({ draft: prepared }) })

    // `DetailShell` mounts the template without a `key` and re-reads the draft when the action changes, so
    // this is the real journey: same component, new card, empty draft — and for one render the block's
    // resource still holds the draft of the card just left, because it clears it in an effect.
    const writes: Partial<ActionDraft>[] = []
    let current: ActionDraft = EMPTY_DRAFT
    let walkOn: () => void = () => {}
    function Harness() {
      const api = useStudioApi()
      const [value, setValue] = useState(first)
      const [draft, setDraftState] = useState<ActionDraft>(EMPTY_DRAFT)
      current = draft
      walkOn = () => {
        setValue(second)
        setDraftState(EMPTY_DRAFT)
      }
      return (
        <QuestionsTemplate
          card={value}
          detail={detail({ action: value, drafts: value === first ? [prepared] : [] })}
          draft={draft}
          setDraft={(patch) => {
            writes.push(patch)
            setDraftState((held) => ({ ...held, ...patch }))
          }}
          refreshing={false}
          api={api}
          route={{ ...EMPTY_ROUTE, action: value.action_id }}
          go={() => {}}
          reload={() => {}}
        />
      )
    }
    render(
      <I18nProvider>
        <Harness />
      </I18nProvider>,
    )
    await waitFor(() => expect(current.advisorApplied).toBe('d_1'))

    writes.length = 0
    await act(async () => {
      walkOn()
    })
    // Asserted on the write as well as the state: the leaked fill lands in the draft `DetailShell`
    // persists under the *new* card's id, so a later re-read cannot be what makes this safe.
    expect(writes).toEqual([])
    expect(current).toEqual(EMPTY_DRAFT)
    expect(screen.queryByText(en.t('advisor.prefilled'))).toBeNull()
  })

  it('keeps the notice and its Clear on the draft whose words are in the boxes', async () => {
    // The user read the prepared answers, then clicked `Explain` on a question. That newer draft is what
    // the block shows now — and it drafted nothing, so a notice keyed to it would disappear from a
    // pre-fill still sitting in the form, over a Clear that cleared none of it.
    const prepared = preparedDraft()
    const explained = advisorDraft({
      draft_id: 'd_2',
      kind: 'question_explain',
      request: { action_id: 'a_1', kind: 'question_explain', question_index: 1, locale: 'en-US', auto: false },
      updated_at: '2026-09-04T09:30:00Z',
    })
    const value = questionCard()
    setApiRoutes({ [`GET ${BASE}/advisor/drafts/d_2`]: () => ({ draft: explained }) })
    const { draft } = mount(QuestionsTemplate, value, {
      detail: detail({ action: value, drafts: [prepared, explained] }),
      draft: {
        answers: { '1': { option_letters: ['B'], free_text: null } },
        advisorApplied: 'd_1',
      },
    })

    await waitFor(() => expect(screen.getByText(en.t('advisor.prefilled'))).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: en.t('advisor.prefilledClear') }))
    expect(draft().answers['1']).toBeUndefined()
    expect(screen.queryByText(en.t('advisor.prefilled'))).toBeNull()
  })

  it('still says whose answers these are after a reload, and does not fill them twice', async () => {
    // `advisorApplied` is persisted with the draft, so this is what the user comes back to: the answers in
    // the boxes and the draft that wrote them, restored from storage rather than from a fill.
    const prepared = preparedDraft()
    const value = questionCard()
    setApiRoutes({ [`GET ${BASE}/advisor/drafts/d_1`]: () => ({ draft: prepared }) })
    const { draft } = mount(QuestionsTemplate, value, {
      detail: detail({ action: value, drafts: [prepared] }),
      draft: {
        answers: { '1': { option_letters: ['B'], free_text: null } },
        advisorApplied: 'd_1',
      },
    })

    await waitFor(() => expect(screen.getByText(en.t('advisor.prefilled'))).toBeInTheDocument())
    expect(screen.getByRole('radio', { name: /Exactly-once/ })).toBeChecked()
    // The user emptied a box before the reload; the fill must not put it back afterwards.
    await userEvent.click(screen.getByRole('button', { name: en.t('advisor.prefilledClear') }))
    expect(draft().answers['1']).toBeUndefined()
    expect(draft().advisorApplied).toBe('d_1')
    await waitFor(() => expect(screen.queryByText(en.t('advisor.prefilled'))).toBeNull())
    expect(draft().answers['1']).toBeUndefined()
  })

  it('leaves a summary checkpoint unchosen until the user picks a side', async () => {
    setApiRoutes({})
    const view = questionsView({
      questions: [],
      pending_count: 0,
      pending_checkpoint: 'summary_confirmation',
      summary_confirmation: { kind: 'summary_confirmation', present: true, answered: false, answer: null, options: ['Looks correct', 'Request changes'] },
    })
    const value = card({
      type: 'question',
      headline: { key: 'action.question.headline', params: {} },
      decisions: [spec('confirm_summary', ['confirm'])],
      primary: { decision: 'confirm_summary', label_key: 'decision.confirm_summary.label' },
      evidence: { ...card().evidence, questions: view },
    })
    const { draft } = mount(QuestionsTemplate, value)
    for (const radio of screen.getAllByRole('radio')) expect(radio).not.toBeChecked()
    // The file's own option strings are shown; the bytes Studio would send are the confirmation's job.
    expect(screen.getByText('Looks correct')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('radio', { name: new RegExp(en.t('template.questions.summaryChanges')) }))
    expect(draft().summaryChoice).toBe('request_changes')
  })
})

describe('recovery template', () => {
  const uncertain = () =>
    card({
      type: 'gate',
      queue_type: 'delivery_uncertain',
      status: 'DeliveryUncertain',
      severity: 'critical',
      risk_class: 'studio_only',
      decisions: [spec('reconcile'), spec('mark_not_delivered', ['confirm']), spec('resubmit', ['confirm'])],
      primary: { decision: 'reconcile', label_key: 'decision.reconcile.label' },
      delivery: {
        ...card().delivery,
        outcome: 'uncertain',
        delivery_id: 'del_1',
        transcript_row: null,
        disk_baseline_unchanged: true,
        boot_id_unchanged: false,
      },
      evidence: {
        ...card().evidence,
        directive: { version: 1, kind: null, stage: 'code-generation', unit: null, units: [], state_sha256: 'sha-x', matches_state: false },
        presence: { human_turn_delta: 0, marker_advanced: false, ok: false },
        cursor_readback: { ok: false, space: 'default', dir_name: '250901-other', uuid: null, state_sha256: null, mismatch: ['dir_name'] },
      },
    })

  it('announces an unprovable delivery assertively and prints the three checks', () => {
    setApiRoutes({})
    mount(RecoveryTemplate, uncertain())
    expect(screen.getByRole('alert')).toHaveTextContent(en.t('template.recovery.uncertainAlert'))
    const checks = screen.getByText(
      new RegExp(en.t('template.recovery.rowAbsent').replace(/[.*+?^${}()|[\]\\]/g, '\\$&')),
    )
    expect(checks).toHaveTextContent(en.t('template.recovery.diskUnchanged'))
    expect(checks).toHaveTextContent(en.t('template.recovery.bootRestarted'))
  })

  it('marks only the sources that actually disagree', () => {
    setApiRoutes({})
    mount(RecoveryTemplate, uncertain())
    const conflicts = document.querySelectorAll('.studio-ev[data-conflict="true"]')
    const labels = [...conflicts].map((node) => node.textContent ?? '')
    expect(labels.some((text) => text.includes(en.t('template.recovery.src.directive')))).toBe(true)
    expect(labels.some((text) => text.includes(en.t('template.recovery.src.cursor')))).toBe(true)
    expect(labels.some((text) => text.includes(en.t('template.recovery.src.marker')))).toBe(true)
    // The state file read cleanly, so it is not painted as a conflict.
    expect(labels.some((text) => text.includes(en.t('template.recovery.src.state')))).toBe(false)
  })

  it('names every unit the active directive claims, not just a single one', () => {
    // A 2.7.1 `invoke-swarm` marker is the unitless fan-out shape: `unit` is null and the units it is
    // really executing are in `units`. Printing the stage alone would hide the fan-out from the one
    // card whose job is to let the directive contradict the state file out loud.
    setApiRoutes({})
    const swarm = uncertain()
    mount(RecoveryTemplate, {
      ...swarm,
      evidence: {
        ...swarm.evidence,
        directive: {
          version: 2, kind: 'invoke-swarm', stage: 'code-generation', unit: null,
          units: ['user-auth', 'billing'], state_sha256: 'sha-x', matches_state: false,
        },
      },
    })
    const values = [...document.querySelectorAll('.studio-ev-val')].map((node) => node.textContent ?? '')
    expect(values).toContain('code-generation · user-auth, billing')
  })

  it('explains exactly the choices the backend offered, and no others', () => {
    setApiRoutes({})
    mount(RecoveryTemplate, uncertain())
    expect(screen.getByText(en.t('decision.reconcile.label'))).toBeInTheDocument()
    expect(screen.getByText(en.t('decision.resubmit.label'))).toBeInTheDocument()
    expect(screen.queryByText(en.t('decision.approve.label'))).toBeNull()
    expect(screen.getByText(en.t('template.common.noAutoChoice'))).toBeInTheDocument()
  })
})

describe('failure template', () => {
  const failing = () =>
    card({
      type: 'circuit_breaker',
      headline: { key: 'action.circuit_breaker.headline', params: {} },
      severity: 'attention',
      decisions: [spec('retry_now'), spec('keep_paused')],
      primary: { decision: 'retry_now', label_key: 'decision.retry_now.label' },
      failure: {
        fingerprint: 'acp.transport.stream_closed:5f2c9a',
        fingerprint_class: 'acp.transport.stream_closed',
        summary_key: null,
        count: 3,
        breaker_open: true,
        history: [
          { at: '2026-09-04T08:00:00Z', outcome: 'stream_closed', backoff_secs: 30 },
          { at: '2026-09-04T08:05:00Z', outcome: 'stream_closed', backoff_secs: 120 },
        ],
        stderr_excerpt: 'Traceback: **not markdown** _at all_\n  at acp.stream',
      },
    })

  it('prints the fingerprint in full, the breaker state and the backoff history', () => {
    setApiRoutes({})
    mount(FailureTemplate, failing())
    expect(
      screen.getByText(
        en.t('template.failure.fingerprint', {
          fingerprint: 'acp.transport.stream_closed:5f2c9a',
          cls: 'acp.transport.stream_closed',
        }),
      ),
    ).toBeInTheDocument()
    expect(screen.getByText(en.t('template.failure.breakerOpen'))).toBeInTheDocument()
    expect(screen.getAllByRole('row').length).toBe(3) // header + two attempts
  })

  it('shows the log excerpt as text, not as markdown', () => {
    setApiRoutes({})
    mount(FailureTemplate, failing())
    // The asterisks survive: a renderer that italicised them would change what the user is reading.
    expect(screen.getByText(/\*\*not markdown\*\*/)).toBeInTheDocument()
    expect(screen.queryByTestId('markdown')).toBeNull()
  })
})

describe('install conflict template', () => {
  const conflicted = () =>
    card({
      type: 'install_conflict',
      headline: { key: 'action.install_conflict.headline', params: {} },
      severity: 'attention',
      risk_class: 'admin',
      decisions: [],
      primary: null,
      install: {
        receipt_id: 'rcpt_9',
        engine_version: '2.3.0',
        bundled_engine_version: '2.7.1',
        drift: [
          driftEntry(),
          driftEntry({
            path: '.kiro/steering/aidlc.md', action: 'owned_identical', blocking: false, diff: null,
            live_sha256: 'same0123456789abcd', receipt_sha256: 'same0123456789abcd', payload_sha256: 'same0123456789abcd',
          }),
        ],
        transaction_id: 'tx_7',
      },
    })

  it('shows ownership, the drift with its hashes, and the safe path only', async () => {
    setApiRoutes({})
    const { patches } = mount(InstallConflictTemplate, conflicted())
    expect(screen.getByText('2.3.0')).toBeInTheDocument()
    expect(screen.getByText(en.t('template.install.receiptSub', { id: 'rcpt_9' }))).toBeInTheDocument()
    expect(screen.getByText('.kiro/tools/data/stage-graph.json')).toBeInTheDocument()
    expect(screen.getByText(en.t('template.install.hashLive', { hash: 'live01234567' }))).toBeInTheDocument()
    expect(screen.getByText(en.t('template.install.action.owned_modified'))).toBeInTheDocument()

    // No overwrite anywhere: the only control is navigation to Repos, where the preview holds the lease.
    const buttons = screen.getAllByRole('button').map((node) => node.textContent ?? '')
    expect(buttons.filter((text) => text.includes(en.t('template.install.openRepo'))).length).toBe(1)
    expect(screen.getByText(en.t('template.install.fixNoMerge'))).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: en.t('template.install.openRepo') }))
    expect(patches.at(-1)).toEqual({ view: 'repos', repo: 'r_1', tx: 'tx_7' })
  })

  it('renders the managed-file diff as text behind a disclosure', async () => {
    setApiRoutes({})
    mount(InstallConflictTemplate, conflicted())
    await userEvent.click(
      screen.getByText(en.t('template.install.showDiff', { path: '.kiro/tools/data/stage-graph.json' })),
    )
    expect(screen.getByText('+new line')).toBeInTheDocument()
    expect(screen.getByText('-old line')).toBeInTheDocument()
  })
})

describe('budget and missing input templates', () => {
  it('never prints a credit number, and never zero for a budget it does not have', () => {
    setApiRoutes({})
    mount(
      BudgetStopTemplate,
      card({
        type: 'budget_stop',
        headline: { key: 'action.budget_stop.headline', params: {} },
        severity: 'info',
        decisions: [spec('run_now'), spec('keep_paused')],
        primary: { decision: 'run_now', label_key: 'decision.run_now.label' },
      }),
    )
    expect(screen.getByText(en.t('template.budget.notObservable'))).toBeInTheDocument()
    expect(screen.getAllByText(en.t('common.unavailable')).length).toBeGreaterThanOrEqual(2)
    expect(screen.queryByText('0 / 0')).toBeNull()
    expect(screen.getByText(en.t('template.budget.noBudget'))).toBeInTheDocument()
  })

  it('collects a missing scope as text and records which kind it is', async () => {
    setApiRoutes({})
    const value = card({
      type: 'missing_input',
      headline: { key: 'action.missing_input.headline', params: { reason: 'missing_scope', kind: 'scope' } },
      decisions: [spec('provide_input', ['feedback'])],
      primary: { decision: 'provide_input', label_key: 'decision.provide_input.label' },
    })
    const { draft } = mount(MissingInputTemplate, value)
    expect(screen.getByText(en.t('template.missingInput.reason.missing_scope'))).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText(en.t('template.missingInput.input.scope')), 'Guest checkout')
    expect(draft().scope).toBe('Guest checkout')
    expect(draft().inputKind).toBe('scope')
  })

  it('sends the user to the Intents page for a dangling cursor instead of inventing a picker', async () => {
    setApiRoutes({})
    const value = card({
      type: 'missing_input',
      headline: { key: 'action.missing_input.headline', params: { reason: 'dangling_cursor', space: 'default', cursor: '250801-gone' } },
      risk_class: 'admin',
      decisions: [spec('pick_intent')],
      primary: { decision: 'pick_intent', label_key: 'decision.pick_intent.label' },
    })
    const { patches } = mount(MissingInputTemplate, value)
    expect(screen.queryByLabelText(en.t('template.missingInput.input.scope'))).toBeNull()
    expect(screen.getByText('250801-gone')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: en.t('template.missingInput.openIntents') }))
    expect(patches.at(-1)).toEqual({ view: 'intents', repo: 'r_1', space: 'default' })
  })
})

describe('advisor block', () => {
  it('does not run until it is asked', async () => {
    const calls: string[] = []
    setApiRoutes({
      [`POST ${BASE}/advisor/draft`]: () => {
        calls.push('draft')
        return { ok: true, draft: advisorDraft() }
      },
      [`GET ${BASE}/advisor/drafts/d_1`]: () => ({ draft: advisorDraft() }),
    })
    mount(GateTemplate, card())
    expect(screen.getByText(en.t('advisor.notRun'))).toBeInTheDocument()
    expect(calls.length).toBe(0)

    await userEvent.click(screen.getByRole('button', { name: en.t('advisor.action.gate_analysis') }))
    await waitFor(() => expect(screen.getByText('The decline path is not covered by the design.')).toBeInTheDocument())
    expect(calls).toEqual(['draft'])
  })

  it('labels what it cannot answer, and never preselects a control', async () => {
    setApiRoutes({
      [`POST ${BASE}/advisor/draft`]: () => ({ ok: true, draft: advisorDraft() }),
      [`GET ${BASE}/advisor/drafts/d_1`]: () => ({ draft: advisorDraft() }),
    })
    const { draft } = mount(GateTemplate, card())
    await userEvent.click(screen.getByRole('button', { name: en.t('advisor.action.gate_analysis') }))
    await waitFor(() => expect(screen.getByText(en.t('advisor.needsYourDecision'))).toBeInTheDocument())
    expect(screen.getByText('Whether a follow-up intent is acceptable')).toBeInTheDocument()
    expect(screen.getByText(en.t('advisor.draftNotDecision'))).toBeInTheDocument()
    // Applying the drafted feedback fills the box and nothing else.
    expect(draft().feedback).toBe('')
    await userEvent.click(screen.getByRole('button', { name: en.t('advisor.useFeedback') }))
    expect(draft().feedback).toBe('Please describe the decline path.')
  })

  it('refuses a draft whose evidence moved while it was reading', async () => {
    setApiRoutes({
      [`POST ${BASE}/advisor/draft`]: () => ({ ok: true, draft: advisorDraft() }),
      [`GET ${BASE}/advisor/drafts/d_1`]: () => ({
        draft: advisorDraft({ neutrality: { ok: false, changed: ['state_sha'] } }),
      }),
    })
    mount(GateTemplate, card())
    await userEvent.click(screen.getByRole('button', { name: en.t('advisor.action.gate_analysis') }))
    await waitFor(() => expect(screen.getByText(en.t('advisor.evidenceMoved'))).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: en.t('advisor.useFeedback') })).toBeNull()
  })

  it('says so when the Advisor is unavailable rather than failing silently', async () => {
    setApiRoutes({
      [`POST ${BASE}/advisor/draft`]: () => {
        throw new (class extends Error {
          constructor() {
            super('API 503: {"error":"advisor off","code":"advisor_unavailable","details":{}}')
          }
        })()
      },
    })
    mount(GateTemplate, card())
    await userEvent.click(screen.getByRole('button', { name: en.t('advisor.action.gate_analysis') }))
    await waitFor(() => expect(screen.getByText(en.t('errors.advisor_unavailable'))).toBeInTheDocument())
  })

  it('keeps one card’s draft off another card, though the template stays mounted', async () => {
    const first = card()
    const second = card({ action_id: 'a_2' })
    setApiRoutes({
      [`POST ${BASE}/advisor/draft`]: () => ({ ok: true, draft: advisorDraft() }),
      [`GET ${BASE}/advisor/drafts/d_1`]: () => ({ draft: advisorDraft() }),
    })

    // Re-rendered rather than re-mounted, because that is what `DetailShell` does: it renders the template
    // without a `key` so a draft still in flight survives a walk to another card and back.
    function Harness({ value }: { value: ActionCard }) {
      const api = useStudioApi()
      return (
        <GateTemplate
          card={value}
          detail={detail({ action: value })}
          draft={EMPTY_DRAFT}
          setDraft={() => {}}
          refreshing={false}
          api={api}
          route={{ ...EMPTY_ROUTE, action: value.action_id }}
          go={() => {}}
          reload={() => {}}
        />
      )
    }
    const view = render(
      <I18nProvider>
        <Harness value={first} />
      </I18nProvider>,
    )
    await userEvent.click(screen.getByRole('button', { name: en.t('advisor.action.gate_analysis') }))
    await waitFor(() =>
      expect(screen.getByText('The decline path is not covered by the design.')).toBeInTheDocument(),
    )

    view.rerender(
      <I18nProvider>
        <Harness value={second} />
      </I18nProvider>,
    )
    await waitFor(() => expect(screen.getByText(en.t('advisor.notRun'))).toBeInTheDocument())
    expect(screen.queryByText('The decline path is not covered by the design.')).toBeNull()
  })

  it('shows the newest draft it can act on, not merely the newest one', async () => {
    const ready = advisorDraft({ updated_at: '2026-09-04T09:00:00Z' })
    const broken = advisorDraft({
      draft_id: 'd_2', status: 'failed', result: null, error: 'timed_out',
      updated_at: '2026-09-04T09:05:00Z',
    })
    setApiRoutes({
      [`GET ${BASE}/advisor/drafts/d_1`]: () => ({ draft: ready }),
      [`GET ${BASE}/advisor/drafts/d_2`]: () => ({ draft: broken }),
    })
    const value = card()
    mount(GateTemplate, value, { detail: detail({ action: value, drafts: [ready, broken] }) })

    // A later failure must not bury a draft the user can still act on — nothing was retried, so the ready
    // one is the whole of what the Advisor has to say about this card.
    await waitFor(() =>
      expect(screen.getByText('The decline path is not covered by the design.')).toBeInTheDocument(),
    )
    expect(screen.queryByText(en.t('advisor.error.timed_out'))).toBeNull()
  })

  it('shows a lone failure as a failure, not as a card that was never asked', async () => {
    const broken = advisorDraft({ draft_id: 'd_2', status: 'failed', result: null, error: 'timed_out' })
    setApiRoutes({ [`GET ${BASE}/advisor/drafts/d_2`]: () => ({ draft: broken }) })
    const value = card()
    mount(GateTemplate, value, { detail: detail({ action: value, drafts: [broken] }) })

    await waitFor(() => expect(screen.getByText(en.t('advisor.error.timed_out'))).toBeInTheDocument())
    expect(screen.queryByText('The decline path is not covered by the design.')).toBeNull()
  })

  it('offers no Advisor at all for a command card the backend refuses to analyse', () => {
    setApiRoutes({})
    mount(DecisionTemplate, card({ type: 'run', decisions: [spec('run')], headline: { key: 'action.run.headline', params: {} } }))
    expect(screen.queryByText(en.t('advisor.notRun'))).toBeNull()
  })
})
