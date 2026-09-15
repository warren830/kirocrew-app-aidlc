/**
 * The four-step new-intent wizard: Work → Preset → Plan → Review, and a Create that never runs anything.
 *
 * The three properties this file exists to guarantee:
 *
 *  1. **Nothing is written before Create, and Create does not start execution.** Every step reads through
 *     `POST …/intents/plan/preview`, which computes a plan and writes nothing. Create calls
 *     `POST …/intents`, whose engine verb is `utility.intent_create`: it mints an inert `Idle` record and
 *     submits no prompt (FR-NEW-001/003/005). This component has no path to `/run`, `/api/chat` or any
 *     dispatch, and the success panel says so and offers `Run to next checkpoint` as the user's next act.
 *  2. **The plan the user approved is the plan that gets created.** Create sends
 *     `confirm_plan_digest`, the sha256 the backend recomputes from the plan it derives at create time
 *     (`EffectivePlan.digest`). If a scope file or the installed stage graph changed since the preview,
 *     the digests disagree and the backend refuses instead of creating a composition nobody reviewed. The
 *     digest is computed here rather than fetched because the preview route does not return one.
 *  3. **Nothing about the plan is invented locally.** Stage locks, dependency refusals, exact counts and
 *     estimate bands all come from the server on every change; this file only collects the request.
 *
 * Preset defaults follow the scope's own frontmatter: picking a scope sets depth, test strategy and review
 * cap to what that scope declares (and resets the matrix), because a wizard that silently sent
 * `depth: Standard` over a scope asking for `Comprehensive` would create a shallower workflow than the
 * scope author designed.
 *
 * The Preset step may also be filled from an Advisor proposal (FR-NEW-006, `PlanAdvisor.tsx`). That is
 * kept here rather than in the panel because it is wizard state: `applyProposal` copies the proposed
 * settings into `WizardState` — after dropping every override the engine's shadow preview refused AND the
 * proposal introduced, so a refused stage change is never accepted (FR-PLAN-004/005) while the human's own
 * pre-existing overrides are never taken away — and sets the scope-sync ref in the same tick
 * so the proposed caps are not overwritten when the new scope's frontmatter lands; `origins` remembers
 * which overrides came from the proposal so the diff can say so; `clearProposal` restores the human's own
 * picks from before the first apply, in one action. The created intent inherits nothing from the draft:
 * Create sends the fields exactly as it always did, and the draft is dismissed once Create succeeds
 * (FR-NEW-004).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { useShellData } from '../shell/StudioApp'
import { useI18n } from '../i18n'
import { StudioApiError } from '../lib/api'
import { DEFAULT_SPACE, type Navigate, type StudioRoute } from '../lib/route'
import type {
  EffectivePlan, IntentCreateResponse, IntentsResponse, PlanDepth, PlanIssue, PlanRequest, RepoRecord,
} from '../lib/types'
import { useResource } from '../lib/useResource'
import { canonicalJson, sha256Hex } from './digest'
import { PlanAdvisorPanel, type ProposalPicks, readScopeMeta, type ScopeMetaView, usePlanAdvisor } from './PlanAdvisor'
import { StepPlan } from './StepPlan'
import { StepPreset } from './StepPreset'
import { StepReview } from './StepReview'
import { StepWork } from './StepWork'

export const WIZARD_STEPS = ['work', 'preset', 'plan', 'review'] as const
export type WizardStep = (typeof WIZARD_STEPS)[number]

export type ReviewCap = 'none' | 'advisory' | 'adversarial'

export interface WizardState {
  repo: string
  space: string
  objective: string
  context: string
  label: string
  projectType: '' | 'Greenfield' | 'Brownfield'
  scope: string
  depth: PlanDepth
  testStrategy: PlanDepth | null
  reviewCap: ReviewCap | null
  /** Only the stages the user moved away from the scope's own selection. */
  overrides: Record<string, boolean>
}

export const EMPTY_WIZARD: WizardState = {
  repo: '',
  space: '',
  objective: '',
  context: '',
  label: '',
  projectType: '',
  scope: '',
  depth: 'Standard',
  testStrategy: null,
  reviewCap: null,
  overrides: {},
}

/**
 * An Advisor proposal the human accepted into the fields. `before` is the human's own picks from the FIRST
 * apply, so "Ask again" → Use → Clear still returns to them; `draftId` is the draft whose proposal is in the
 * fields, so Use is offered once per draft and a second proposal can be applied without clearing the first.
 */
export interface AppliedProposal {
  draftId: string
  before: Pick<WizardState, 'scope' | 'depth' | 'testStrategy' | 'reviewCap' | 'overrides'>
}

/** AI-DLC's grammar for the record directory name: at most three lowercase kebab words. */
export const LABEL_RE = /^[a-z0-9]+(?:-[a-z0-9]+){0,2}$/
const MAX_LABEL_WORDS = 3

/** `plan.py::_slugify` — the label AI-DLC derives when the request omits one. */
export function slugifyObjective(text: string): string {
  return (text.toLowerCase().match(/[a-z0-9]+/g) ?? []).slice(0, MAX_LABEL_WORDS).join('-')
}

/** `plan.py::_opt_str` — a trimmed string, or `null` for "not provided". */
function norm(value: string): string | null {
  const trimmed = value.trim()
  return trimmed ? trimmed : null
}

/**
 * The body both `plan/preview` and `POST /intents` receive.
 *
 * `withText` is false for a preview: the objective, label and context do not take part in composing a
 * plan, so leaving them out means typing a sentence never issues a request that reads the repository.
 * They are restored for Create, and `digestRequest` puts them back into the digest.
 */
export function planBody(state: WizardState, space: string, withText: boolean): PlanRequest {
  return {
    space: space || DEFAULT_SPACE,
    scope: state.scope,
    depth: state.depth,
    test_strategy: state.testStrategy,
    review_cap: state.reviewCap,
    project_type: state.projectType || null,
    overrides: state.overrides,
    objective: withText ? norm(state.objective) : null,
    label: withText ? norm(state.label) : null,
    context: withText ? norm(state.context) : null,
  }
}

/** The scope frontmatter reader lives with the Advisor hook, which reads it too; the step imports it from here. */
export type { ScopeMetaView }

//: Issue codes whose `slugs` are exactly the overrides `_build_stages` ignored (contracts §3.1, FR-PLAN-004/005).
const LOCK_CODES: ReadonlySet<string> = new Set(['unknown_stage', 'required_stage_disabled', 'frozen_stage', 'behind_cursor'])

/**
 * The override slugs one shadow-plan issue says the engine refused. The lock codes carry exactly the refused
 * slug. `dependency_missing` carries the starved consumer FIRST and then every producer that could feed it
 * (`plan.py::PlanIssue.of("dependency_missing", consumer, *producers, stage=consumer, …)`), and only the
 * consumer is the override the engine ignored — the producers being off is the human's own pick, which a
 * proposal must not be allowed to take away. Any other code names no override at all.
 */
export function refusedSlugs(issue: PlanIssue): string[] {
  if (issue.code === 'dependency_missing') {
    const consumer = issue.params['stage']
    return typeof consumer === 'string' ? [consumer] : []
  }
  return LOCK_CODES.has(issue.code) ? issue.slugs : []
}

/** The scope names this repository defines, as reported by the `scope_unknown` refusal. */
export function knownScopesFrom(plan: EffectivePlan | null): string[] {
  const issue = plan?.issues.find((entry) => entry.code === 'scope_unknown')
  const known = issue?.params['known']
  if (!Array.isArray(known)) return []
  return known.filter((name): name is string => typeof name === 'string')
}

// --------------------------------------------------------------------------- //
// the plan digest (mirrors `EffectivePlan.digest`, contracts §1.15)
// --------------------------------------------------------------------------- //

// `sha256Hex` and `canonicalJson` live in `digest.ts` (see there for why) and stay exported from here.
export { canonicalJson, sha256Hex } from './digest'

/**
 * The request the digest is taken over.
 *
 * The server's own echo (`plan.request`) is the base, so every field it normalised — the defaulted space,
 * the booleans in `overrides` — is used exactly as the backend will recompute it. Only the three text
 * fields are supplied locally, because the preview deliberately did not send them.
 */
export function digestRequest(plan: EffectivePlan, state: WizardState): Record<string, unknown> {
  return {
    ...(plan.request as unknown as Record<string, unknown>),
    objective: norm(state.objective),
    label: norm(state.label),
    context: norm(state.context),
  }
}

/** `EffectivePlan.digest()`: the request, the stage selection and the graph size — never the estimate. */
export function planDigest(plan: EffectivePlan, state: WizardState): string {
  const payload = {
    request: digestRequest(plan, state),
    stages: plan.stages.map((stage) => [stage.slug, stage.enabled]),
    graph_stage_count: plan.graph_stage_count,
  }
  const text = canonicalJson(payload)
  // `canonicalJson` emits ASCII only (every other code point is escaped), so this is exact UTF-8.
  const bytes = new Uint8Array(text.length)
  for (let i = 0; i < text.length; i += 1) bytes[i] = text.charCodeAt(i) & 0xff
  return sha256Hex(bytes)
}

// --------------------------------------------------------------------------- //
// the view
// --------------------------------------------------------------------------- //

/** A repository that can host a new intent, or the reason it cannot. */
export function repoBlockReason(repo: RepoRecord): 'availability' | 'not_installed' | 'recovery_required' | null {
  if (repo.availability !== 'available') return 'availability'
  if (repo.install.status === 'recovery_required') return 'recovery_required'
  if (repo.install.status === 'not_installed') return 'not_installed'
  return null
}

export interface WizardViewProps {
  route: StudioRoute
  go: Navigate
}

export function WizardView({ route, go }: WizardViewProps) {
  const i18n = useI18n()
  const { t } = i18n
  const { api, repos: reposResource, health, settings } = useShellData()
  // The Advisor is offered only where Settings enable it AND the host can spawn it; `null` while Settings
  // have not loaded, so the button is not hidden by a slow read.
  const available = settings.data
    ? settings.data.settings.advisor.enabled && settings.data.capabilities.advisor.available
    : null
  const repos = reposResource.data?.repos ?? []

  const [step, setStep] = useState<WizardStep>('work')
  const [state, setState] = useState<WizardState>(() => ({ ...EMPTY_WIZARD, repo: route.repo }))
  const [preview, setPreview] = useState<{ key: string; repo: string; plan: EffectivePlan } | null>(null)
  const [previewError, setPreviewError] = useState<StudioApiError | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<StudioApiError | null>(null)
  const [created, setCreated] = useState<IntentCreateResponse | null>(null)
  const [partialCreated, setPartialCreated] = useState<{
    repoId: string; intentKey: string; space: string; intentDir: string; failedPhase: string | null
  } | null>(null)
  const [repairing, setRepairing] = useState(false)
  const [scopes, setScopes] = useState<string[]>([])
  const [applied, setApplied] = useState<AppliedProposal | null>(null)
  /** Overrides that came from the applied proposal rather than the human's own click; the diff says so. */
  const [origins, setOrigins] = useState<Record<string, 'advisor'>>({})
  /** Overrides the engine refused at the last apply and the wizard left out. */
  const [dropped, setDropped] = useState(0)

  const repo = repos.find((candidate) => candidate.repo_id === state.repo) ?? null
  const blocked = repo ? repoBlockReason(repo) : null

  // Spaces and the cursor's own space. `intent-create` has no --space flag, so the active space is not a
  // preference: it is the only place a new record can land, and the wizard must show which one that is.
  const inventory = useResource<IntentsResponse>(
    repo && !blocked ? `wizard-spaces:${repo.repo_id}` : null,
    useCallback(
      (signal) => api.intents(state.repo, {}, { signal }),
      [api, state.repo],
    ),
    { interval: 0, revalidateOn: ['intent.updated', 'repo.updated', 'reset'] },
  )
  const activeSpace = inventory.data?.active_space ?? ''
  const spaces = inventory.data?.spaces ?? []

  const previewSequence = useRef(0)
  // An explicit "From scope" (null) is also a choice, even when it does not change the current value.
  const syncedScope = useRef('')
  const pickedFields = useRef<Partial<Record<'depth' | 'testStrategy' | 'reviewCap', true>>>({})
  // The preview body excludes the objective, the label and the context: they are not inputs to a plan, so
  // typing a sentence must not fire a request that reads the repository on every keystroke.
  const previewBody = useMemo(
    () => (repo && !blocked ? planBody(state, activeSpace, false) : null),
    [repo, blocked, state, activeSpace],
  )
  const previewKey = previewBody ? canonicalJson({ repo: state.repo, body: previewBody }) : ''
  // Keep the last matrix visible during recompute, but only the matching request can supply defaults or confirm.
  const plan = preview?.repo === state.repo ? preview.plan : null
  const currentPlan = preview?.key === previewKey ? plan : null
  // The effect depends on the canonical text, not on the object identity, so a re-render with an equal
  // body does not re-POST. The body itself travels through a ref for the same reason.
  const bodyRef = useRef<PlanRequest | null>(previewBody)
  bodyRef.current = previewBody

  useEffect(() => {
    const ticket = (previewSequence.current += 1)
    const body = bodyRef.current
    setPreview((previous) => previous ? { ...previous, key: '' } : null)
    setPreviewError(null)
    if (!previewKey || !state.repo || !body) {
      setPreview(null)
      setPreviewing(false)
      return
    }
    const timer = setTimeout(() => {
      setPreviewing(true)
      api
        .planPreview(state.repo, body)
        .then((answer) => {
          if (ticket !== previewSequence.current) return
          const defaults = readScopeMeta(answer.plan.scope_meta ?? null)
          if (defaults?.name === body.scope && defaults.name !== syncedScope.current) {
            syncedScope.current = defaults.name
            const picked = pickedFields.current
            // Apply untouched defaults in the same batch as the answer, so Create is never enabled
            // between accepting a preview and changing the inputs it was computed from.
            setState((current) => current.repo !== state.repo || current.scope !== defaults.name ? current : ({
              ...current,
              depth: picked.depth ? current.depth : defaults.depth ?? current.depth,
              testStrategy: picked.testStrategy ? current.testStrategy : defaults.testStrategy,
              reviewCap: picked.reviewCap ? current.reviewCap : defaults.reviewCap,
            }))
          }
          setPreview({ key: previewKey, repo: state.repo, plan: answer.plan })
          setPreviewError(null)
          const known = knownScopesFrom(answer.plan)
          if (known.length) setScopes(known)
        })
        .catch((caught: unknown) => {
          if (ticket !== previewSequence.current) return
          setPreviewError(caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0))
        })
        .finally(() => {
          if (ticket === previewSequence.current) setPreviewing(false)
        })
    }, 200)
    return () => {
      clearTimeout(timer)
      // Invalidate responses on unmount or an unusable repo selection as well as on the next request.
      if (ticket === previewSequence.current) previewSequence.current += 1
    }
  }, [api, previewKey, state.repo])

  const meta = useMemo(() => {
    const value = readScopeMeta(plan?.scope_meta ?? null)
    return value?.name === state.scope ? value : null
  }, [plan, state.scope])

  // The Advisor's view of the human's picks so far. Memoised on the fields, not on `state`, so the hook's
  // callbacks do not churn while the objective is being typed. The hook lives here, not in the Preset
  // step, so a draft in flight survives a walk to another step and back.
  const picks = useMemo<ProposalPicks>(
    () => ({
      scope: state.scope,
      depth: state.depth,
      test_strategy: state.testStrategy,
      review_cap: state.reviewCap,
      overrides: state.overrides,
    }),
    [state.scope, state.depth, state.testStrategy, state.reviewCap, state.overrides],
  )
  const planAdvisor = usePlanAdvisor({
    api,
    repoId: state.repo,
    space: activeSpace || DEFAULT_SPACE,
    locale: i18n.locale,
    objective: state.objective,
    context: state.context,
    projectType: state.projectType,
    current: picks,
  })
  const { dismiss: dismissProposal } = planAdvisor

  const patch = useCallback((next: Partial<WizardState>) => {
    setCreateError(null)
    // A different repository is a different Advisor conversation: the applied proposal and its provenance
    // go with the old one (the hook lets go of the draft itself on the repo change).
    if (next.repo !== undefined) {
      syncedScope.current = ''
      pickedFields.current = {}
      setApplied(null)
      setOrigins({})
      setDropped(0)
    }
    for (const field of ['depth', 'testStrategy', 'reviewCap'] as const) {
      if (next[field] !== undefined) pickedFields.current = { ...pickedFields.current, [field]: true }
    }
    setState((current) => ({ ...current, ...next }))
  }, [])

  const pickScope = useCallback((scope: string) => {
    // The matrix is defined relative to the preset, so a new preset starts from that preset's selection.
    syncedScope.current = ''
    pickedFields.current = {}
    setCreateError(null)
    // The proposal's stage provenance goes with the overrides it described; `applied` stays, so the banner
    // and "Clear the proposal" survive a scope change and still undo everything in one action (FR-NEW-006).
    // The left-out count described the map this reset just emptied, so it says nothing true any more.
    setOrigins({})
    setDropped(0)
    setState((current) => ({ ...current, scope, overrides: {} }))
  }, [])

  const toggleStage = useCallback(
    (slug: string, next: boolean) => {
      const stage = plan?.stages.find((candidate) => candidate.slug === slug)
      setCreateError(null)
      // A stage the human moved by hand is the human's, whichever way the proposal had it.
      setOrigins((known) => {
        if (!(slug in known)) return known
        const next = { ...known }
        delete next[slug]
        return next
      })
      setState((current) => {
        const overrides = { ...current.overrides }
        // An override that agrees with the scope is dropped rather than sent: it would show up in the
        // request forever and make a diff that says nothing changed look like a change.
        if (stage && stage.in_grid === next) delete overrides[slug]
        else overrides[slug] = next
        return { ...current, overrides }
      })
    },
    [plan],
  )

  /**
   * Copy the Advisor's proposal into the wizard's own fields — a click handler, never an effect (FR-ADV-007).
   *
   * The shadow plan's issues name what `_build_stages` refused, read per code by `refusedSlugs` (the lock
   * codes list the refused slug; `dependency_missing` lists the starved consumer and its producers, of which
   * only the consumer is a refused override). A refused slug is then dropped ONLY when the proposal changed it
   * (`e.overrides[slug] !== state.overrides[slug]`): the human's own pre-existing override is never removed by
   * a proposal that merely repeated it. The panel says how many were left out: a stage change the engine
   * refuses is never accepted (FR-PLAN-004/005). Use is disabled until the shadow plan has arrived, so this
   * filter always has the engine's verdict to work from.
   */
  const applyProposal = () => {
    const e = planAdvisor.effective
    const d = planAdvisor.draft
    const s = planAdvisor.shadow
    if (!e || !d || !s || planAdvisor.shadowBusy || planAdvisor.stale !== null) return
    const refused = new Set(s.issues.flatMap((issue) => refusedSlugs(issue)))
    const leftOut = new Set(
      Object.keys(e.overrides).filter((slug) => refused.has(slug) && e.overrides[slug] !== state.overrides[slug]),
    )
    const overrides = Object.fromEntries(Object.entries(e.overrides).filter(([slug]) => !leftOut.has(slug)))
    setDropped(leftOut.size)
    // Same tick as the state change: when the preview for the proposed scope lands, its defaults
    // must see that scope as already synced, or its frontmatter would overwrite the proposed caps.
    syncedScope.current = e.scope
    setApplied((held) => ({
      draftId: d.draft_id,
      before: held?.before ?? {
        scope: state.scope,
        depth: state.depth,
        testStrategy: state.testStrategy,
        reviewCap: state.reviewCap,
        overrides: state.overrides,
      },
    }))
    setOrigins(Object.fromEntries(Object.keys(overrides).map((slug) => [slug, 'advisor' as const])))
    setCreateError(null)
    setState((c) => ({
      ...c,
      scope: e.scope,
      depth: e.depth,
      testStrategy: e.test_strategy,
      reviewCap: e.review_cap,
      overrides,
    }))
  }

  /** One action undoes the whole proposal, whatever was changed since (FR-NEW-006). */
  const clearProposal = () => {
    if (!applied) return
    const { before } = applied
    // The restored scope's frontmatter must not re-apply over the restored picks either.
    syncedScope.current = before.scope
    setApplied(null)
    setOrigins({})
    setDropped(0)
    setCreateError(null)
    setState((c) => ({ ...c, ...before }))
  }

  // Computed here, not in the review step: it is what Create sends, and the step only displays it.
  const digest = plan ? planDigest(plan, state) : ''
  const derivedLabel = slugifyObjective(state.objective)
  const effectiveLabel = state.label.trim() || derivedLabel
  const labelValid = LABEL_RE.test(effectiveLabel) && effectiveLabel.length <= 120
  const workComplete = Boolean(repo) && !blocked && state.objective.trim().length > 0 && labelValid
    && Boolean(activeSpace) && !inventory.error
  const presetComplete = state.scope.trim().length > 0

  const create = useCallback(async () => {
    if (!currentPlan?.valid || !repo || !workComplete || !presetComplete || previewing || creating) return
    setCreating(true)
    setCreateError(null)
    try {
      const body = { ...planBody(state, activeSpace, true), confirm_plan_digest: digest }
      const answer = await api.createIntent(repo.repo_id, body)
      setCreated(answer)
      // The consumed draft must not outlive the intent it informed (FR-NEW-004): the next wizard starts clean.
      dismissProposal()
      setApplied(null)
      setOrigins({})
      setDropped(0)
    } catch (caught) {
      const error = caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0)
      setCreateError(error)
      if (error.details.intent_created === true && typeof error.details.intent_key === 'string'
          && typeof error.details.space === 'string' && typeof error.details.intent_dir === 'string') {
        setPartialCreated({
          repoId: repo.repo_id, intentKey: error.details.intent_key,
          space: error.details.space, intentDir: error.details.intent_dir,
          failedPhase: typeof error.details.creation_failed_phase === 'string'
            ? error.details.creation_failed_phase : null,
        })
        dismissProposal()
      }
    } finally {
      setCreating(false)
    }
  }, [api, currentPlan, repo, state, activeSpace, digest, workComplete, presetComplete, previewing, creating, dismissProposal])

  if (partialCreated) {
    const compositionFailed = partialCreated.failedPhase === 'plan_composition'
    const titleKey = compositionFailed ? 'wizard.partial.planComposition.title' : 'wizard.partial.title'
    const open = () => go({
      view: 'intents', repo: partialCreated.repoId,
      space: partialCreated.space, intent: partialCreated.intentKey,
    })
    const repair = async () => {
      // Compilation cannot apply the selected stage overrides; their plan must be corrected first.
      if (compositionFailed || repairing) return
      setRepairing(true)
      setCreateError(null)
      try {
        await api.compileRuntime(partialCreated.repoId, partialCreated.intentKey)
        open()
      } catch (caught) {
        setCreateError(caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0))
      } finally {
        setRepairing(false)
      }
    }
    return (
      <section className="studio-scroll studio-wiz" aria-label={t(titleKey)}>
        <h2>{t(titleKey)}</h2>
        <p>{t(compositionFailed ? 'wizard.partial.planComposition.body' : 'wizard.partial.body', {
          intent: partialCreated.intentDir,
        })}</p>
        {!compositionFailed ? <p>{t('wizard.partial.activate')}</p> : null}
        {createError ? <p role="alert">{createError.message}</p> : null}
        <div className="studio-row">
          {!compositionFailed ? (
            <button className="studio-btn" disabled={repairing} onClick={() => void repair()}>
              {t(repairing ? 'wizard.partial.repairing' : 'wizard.partial.retry')}
            </button>
          ) : null}
          <button className="studio-btn" disabled={repairing} onClick={open}>
            {t(compositionFailed ? 'wizard.partial.planComposition.open' : 'wizard.partial.open')}
          </button>
        </div>
      </section>
    )
  }

  if (created) {
    return (
      <div className="studio-scroll">
        <div className="studio-wiz">
          <CreatedPanel
            answer={created}
            repoLabel={repo?.label ?? state.repo}
            onAgain={() => {
              setCreated(null)
              setPreview(null)
              setStep('work')
              syncedScope.current = ''
              pickedFields.current = {}
              setState({ ...EMPTY_WIZARD, repo: state.repo })
            }}
            onOpen={() => {
              const key = created.intent.intent_key
              go({ view: 'intents', repo: created.intent.repo_id, space: created.intent.space, intent: key })
            }}
          />
        </div>
      </div>
    )
  }

  const index = WIZARD_STEPS.indexOf(step)
  const bunMissing = health.data ? health.data.tools.bun.found === false : false
  // Where the probe looked, so the work step can name it instead of guessing: the health block
  // carries the searched locations precisely because a PATH-only answer is not the whole truth (A28).
  const bunSearched = health.data ? health.data.tools.bun.searched : []

  return (
    <div className="studio-scroll">
      <div className="studio-wiz">
        <h1>{t('wizard.title')}</h1>
        <p className="studio-lede">{t('wizard.lede')}</p>

        <ol className="studio-wiz-steps" aria-label={t('wizard.a11y.stepper')}>
          {WIZARD_STEPS.map((name, position) => (
            <li key={name}>
              {position > 0 ? <span className="studio-wiz-step-sep" aria-hidden="true" /> : null}
              <button
                type="button"
                className="studio-wiz-step"
                data-state={position < index ? 'done' : position === index ? 'now' : 'todo'}
                {...(position === index ? { 'aria-current': 'step' as const } : {})}
                disabled={creating}
                onClick={() => setStep(name)}
              >
                <span className="studio-wiz-step-n studio-mono" aria-hidden="true">
                  {position < index ? <Icon name="check" size={11} strokeWidth={2} /> : position + 1}
                </span>
                <span className="studio-sr">{t('wizard.step.a11y', { n: position + 1, label: t(`wizard.step.${name}`) })}</span>
                <span aria-hidden="true">{t(`wizard.step.${name}`)}</span>
              </button>
            </li>
          ))}
        </ol>

        {inventory.error ? (
          <div className="studio-banner" data-tone="danger" role="alert">
            <Icon name="warn" size={15} />
            <span className="studio-grow">
              {inventory.error.known ? t(`errors.${inventory.error.code}`) : inventory.error.message}
            </span>
            <button type="button" className="studio-btn" disabled={inventory.stale || creating}
              onClick={() => void inventory.refresh()}>
              {t('common.retry')}
            </button>
          </div>
        ) : null}

        {previewError ? (
          <p className="studio-banner" data-tone="danger" role="alert">
            <Icon name="warn" size={15} />
            <span className="studio-grow">
              {t('wizard.error.preview')} {previewError.known ? t(`errors.${previewError.code}`) : previewError.message}
            </span>
          </p>
        ) : null}

        {step === 'work' ? (
          <StepWork
            state={state}
            repos={repos}
            repo={repo}
            blocked={blocked}
            spaces={spaces}
            activeSpace={activeSpace}
            derivedLabel={derivedLabel}
            labelValid={labelValid}
            bunMissing={bunMissing}
            bunSearched={bunSearched}
            graphStageCount={plan?.graph_stage_count ?? null}
            intentCount={inventory.data?.intents.length ?? null}
            onPatch={patch}
            onOpenRepos={() => go({ view: 'repos' })}
          />
        ) : null}

        {step === 'preset' ? (
          <StepPreset
            state={state}
            scopes={scopes}
            meta={meta}
            plan={plan}
            unreadable={Boolean(previewError)}
            onPickScope={pickScope}
            onPatch={patch}
            advisorPanel={
              <PlanAdvisorPanel
                advisor={planAdvisor}
                available={available}
                canAsk={Boolean(repo) && !blocked && state.objective.trim().length > 0}
                appliedDraftId={applied?.draftId ?? null}
                dropped={dropped}
                onUse={applyProposal}
                onClear={clearProposal}
              />
            }
          />
        ) : null}

        {step === 'plan' ? <StepPlan plan={plan} busy={previewing} origins={origins} onToggle={toggleStage} /> : null}

        {step === 'review' ? (
          <StepReview
            plan={plan}
            digest={digest}
            repo={repo}
            origins={origins}
            busy={previewing}
            creating={creating}
            workComplete={workComplete}
            presetComplete={presetComplete}
            error={createError}
          />
        ) : null}

        <div className="studio-wiz-bar">
          <button
            type="button"
            className="studio-btn"
            disabled={index === 0 || creating}
            onClick={() => setStep(WIZARD_STEPS[Math.max(0, index - 1)] as WizardStep)}
          >
            {t('wizard.nav.back')}
          </button>
          {step === 'review' ? (
            <button
              type="button"
              className="studio-btn studio-btn-primary"
              disabled={!currentPlan?.valid || !workComplete || !presetComplete || creating || previewing}
              onClick={() => void create()}
            >
              <Icon name="check" size={13} />
              {t('wizard.nav.create')}
            </button>
          ) : (
            <button
              type="button"
              className="studio-btn studio-btn-primary"
              disabled={(step === 'work' && !workComplete) || (step === 'preset' && !presetComplete)}
              onClick={() => setStep(WIZARD_STEPS[Math.min(WIZARD_STEPS.length - 1, index + 1)] as WizardStep)}
            >
              {t('wizard.nav.continue')}
            </button>
          )}
          <span className="studio-muted studio-wiz-count">{t('wizard.step.of', { n: index + 1 })}</span>
          {previewing ? (
            <span className="studio-muted" role="status">
              {t('wizard.preview.busy')}
            </span>
          ) : null}
        </div>
      </div>
    </div>
  )
}

/**
 * What Create actually did.
 *
 * Rendered instead of the wizard, not as a toast: `useNotify()` has no listener in the shipped dashboard
 * (`03` addendum A1), so a toast would be the only confirmation and would be invisible. The panel states
 * that nothing ran and names the control that would run it, which is the user's next deliberate act.
 */
function CreatedPanel({
  answer,
  repoLabel,
  onOpen,
  onAgain,
}: {
  answer: IntentCreateResponse
  repoLabel: string
  onOpen: () => void
  onAgain: () => void
}) {
  const { t } = useI18n()
  const intent = answer.intent
  return (
    <section className="studio-panel" role="status">
      <h1>
        <Icon name="check" size={18} />
        {t('wizard.created.title')}
      </h1>
      <p>{t('wizard.created.body')}</p>
      <div className="studio-row studio-wrap">
        <Chip mono icon="intent">{t('wizard.created.intent', { intent: intent.intent_key, repo: repoLabel })}</Chip>
        {/* `verified` is a truthy record of per-file checks on the wire; only its verdict is shown here. */}
        {intent.verified ? (
          <Chip tone="ok" icon="check">{t('wizard.created.verified')}</Chip>
        ) : (
          <Chip tone="warn" icon="warn">{t('wizard.created.unverified')}</Chip>
        )}
        <Chip mono>{t('wizard.created.transaction', { id: answer.transaction_id })}</Chip>
      </div>
      <p className="studio-consequence">
        <Icon name="info" size={13} />
        <span>{t('wizard.review.consequence')}</span>
      </p>
      <div className="studio-row studio-wrap">
        <button type="button" className="studio-btn studio-btn-primary" onClick={onOpen}>
          <Icon name="intent" size={13} />
          {t('wizard.created.next')}
        </button>
        <button type="button" className="studio-btn" onClick={onAgain}>
          <Icon name="plus" size={13} />
          {t('wizard.created.again')}
        </button>
      </div>
    </section>
  )
}

export default WizardView
