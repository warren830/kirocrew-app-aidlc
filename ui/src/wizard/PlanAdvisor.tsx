/**
 * The Advisor in the new-intent wizard: a plan proposal, never a plan (FR-NEW-006, contracts §1.18a).
 *
 * A `plan_draft` is card-less: no intent exists yet, so the request goes to
 * `POST /repos/{repo_id}/intents/plan/advise` with the objective, the context and the human's picks so far,
 * and the poll shares `useAdvisor`'s `advisor-draft:<id>` key so the drawer and the wizard read once. What
 * comes back is a `PlanRequest` patch (`plan_proposal`) the server resolved from the agent's option letters —
 * no model prose is ever parsed here as a value.
 *
 * Four rules this file keeps, and why:
 *
 *  - **Nothing is asked for on the file's own initiative** (FR-ADV-001). No effect posts; only the button's
 *    onClick calls `ask`. Mounting, typing and stepping back and forth request nothing.
 *  - **The stage changes on screen are the engine's, not the proposal's.** The patch is run through the
 *    existing `plan/preview` (a "shadow" preview that writes nothing) and rendered with the existing
 *    `PlanDiff`, so every "+"/"−" and every refusal is what `PlanService` would do with those settings. The
 *    debounce and sequence ticket mirror `WizardView`'s preview, keyed on the canonical body text: an object
 *    identity would re-POST a repository-reading preview on every `advisor.updated` event.
 *  - **A proposal describes the wizard it was drafted for.** `stale` names what moved: the objective it
 *    read changed, or the scope moved to one the Advisor neither saw nor proposed; Use is then disabled and
 *    the copy says which of the two happened and what puts it right (pick the proposed scope again, or ask
 *    again), because filling fields from a draft about a different objective is exactly how a proposal turns
 *    into a decision nobody made. A setting the proposal leaves null falls back to the PROPOSED scope's own
 *    frontmatter when the proposal moves the scope — the human's depth, test strategy and review cap are the
 *    old scope's synced values, and carrying them over would send one scope's caps with another's plan.
 *  - **Nothing survives the wizard.** No localStorage: a reload or a view change resets the whole wizard,
 *    objective included, so a persisted draft id would only ever restore a proposal into an empty form. The
 *    hook also lets go of a draft the server no longer has (`draft_not_found`) or has expired (PRD §14 bounded
 *    retention), so the panel returns to its ask button instead of polling a dead id.
 *
 * `usableResult` is imported rather than re-implemented: it is the one predicate every apply control in
 * Studio gates on, and a wizard that judged usability by a looser rule would fill a form from a draft the
 * card view would refuse. The model's own words — summary, evidence, assumptions, alternatives — pass
 * through verbatim (`AdvisorDetails`); only Studio's frame around them is translated.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { useI18n } from '../i18n'
import { decodeError, type StudioApi } from '../lib/api'
import { plural } from '../lib/format'
import type {
  AdvisorDraft, AdvisorDraftGetResponse, EffectivePlan, PlanAdviseRequest, PlanDepth, PlanProposal, PlanRequest,
} from '../lib/types'
import { useResource } from '../lib/useResource'
import { PlanDiff } from '../plan/PlanDiff'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { AdvisorDetails, usableResult } from '../templates/AdvisorBlock'
import { canonicalJson } from './digest'
import type { ReviewCap } from './WizardView'

/** Poll cadence while the subagent is still working (matches `useAdvisor` and the Advisor drawer). */
const RUNNING_POLL_MS = 2_000
/** Same debounce as the wizard's own preview: one recompute per settled proposal, not one per event. */
const SHADOW_DEBOUNCE_MS = 200

/** The four settings plus the override map — the slice of `PlanRequest` a proposal can move. */
export type ProposalPicks = PlanAdviseRequest['current']

/** `plan.py::_opt_str` — a trimmed string, or `null` for "not provided". */
function norm(value: string): string | null {
  const trimmed = value.trim()
  return trimmed ? trimmed : null
}

// --------------------------------------------------------------------------- //
// scope frontmatter
// --------------------------------------------------------------------------- //

/** What ScopeMeta carries, read defensively: `EffectivePlan.scope_meta` is typed as a loose record. */
export interface ScopeMetaView {
  name: string
  depth: PlanDepth | null
  testStrategy: PlanDepth | null
  reviewCap: ReviewCap | null
  description: string | null
  keywords: string[]
  projectOwned: boolean
}

const DEPTHS: readonly PlanDepth[] = ['Minimal', 'Standard', 'Comprehensive']
const CAPS: readonly ReviewCap[] = ['none', 'advisory', 'adversarial']

function str(raw: unknown): string | null {
  return typeof raw === 'string' && raw.trim() ? raw.trim() : null
}

/**
 * A plan's `scope_meta` as typed fields. Lives here rather than in `WizardView` because two readers need it —
 * the wizard's scope-sync effect and `effective` below (a proposed scope's own depth, test strategy and
 * review cap) — and a runtime import from the view back into this module would be a cycle.
 */
export function readScopeMeta(raw: Record<string, unknown> | null): ScopeMetaView | null {
  if (!raw) return null
  const name = str(raw['name'])
  if (!name) return null
  const depth = str(raw['depth'])
  const test = str(raw['test_strategy'])
  const cap = str(raw['review_cap'])
  const keywords = Array.isArray(raw['keywords']) ? raw['keywords'].filter((k): k is string => typeof k === 'string') : []
  return {
    name,
    depth: depth && (DEPTHS as readonly string[]).includes(depth) ? (depth as PlanDepth) : null,
    testStrategy: test && (DEPTHS as readonly string[]).includes(test) ? (test as PlanDepth) : null,
    reviewCap: cap && (CAPS as readonly string[]).includes(cap) ? (cap as ReviewCap) : null,
    description: str(raw['description']),
    keywords,
    projectOwned: raw['project_owned'] === true,
  }
}

// --------------------------------------------------------------------------- //
// the hook
// --------------------------------------------------------------------------- //

export interface PlanAdvisorInput {
  api: StudioApi
  /** The wizard's repository; a change drops any draft asked for under the previous one. */
  repoId: string
  space: string
  locale: string
  objective: string
  context: string
  projectType: '' | 'Greenfield' | 'Brownfield'
  /** The human's picks so far — what the Advisor sees as "current" and what `effective` falls back to. */
  current: ProposalPicks
}

export interface PlanAdvisorState {
  draft: AdvisorDraft | null
  proposal: PlanProposal | null
  /**
   * The proposal completed with the human's own values where it proposed nothing: ONE object, used both by
   * the shadow preview and by Use, so what the human reviews is exactly what gets copied.
   */
  effective: ProposalPicks | null
  /** `plan/preview` over `effective`, so the stage changes and refusals shown are the engine's. */
  shadow: EffectivePlan | null
  shadowBusy: boolean
  /** The shadow preview's decoded error code, when the engine could not compose the proposed settings. */
  shadowError: string | null
  /** True while the ask itself is in flight. */
  pending: boolean
  /** A decoded backend error code from the ask (`advisor_unavailable` when the Advisor is off in Settings). */
  error: string | null
  /**
   * Why the wizard no longer matches what this proposal was drafted for, or `null` while it still does:
   * `'objective'` — the objective the draft read changed (only asking again helps); `'scope'` — the scope
   * moved to one the Advisor neither saw nor proposed (picking the proposed scope again also helps). Use is
   * refused while set.
   */
  stale: 'objective' | 'scope' | null
  ask: () => void
  dismiss: () => void
}

/** What the human asked for, so a draft is only ever shown against the wizard it was drafted from. */
interface Asked {
  repoId: string
  draftId: string
  /** The trimmed objective the draft read. */
  objective: string
  /** The scope the human had picked when asking. */
  scope: string
}

export function usePlanAdvisor({
  api, repoId, space, locale, objective, context, projectType, current,
}: PlanAdvisorInput): PlanAdvisorState {
  const [asked, setAsked] = useState<Asked | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // A draft belongs to the repository it was asked under. The derived guard covers the render in which the
  // repo changes; the effect then lets the id go for good, so a walk back to that repo does not resurrect it.
  const draftId = asked && asked.repoId === repoId ? asked.draftId : null
  // The repository the wizard is on right now, read by an ask whose answer lands after the repository was
  // changed: that draft was asked under the old one and must not be held, or it would come back on a walk
  // back to that repository as a proposal nobody asked this wizard for.
  const liveRepo = useRef(repoId)
  liveRepo.current = repoId
  useEffect(() => {
    setAsked((held) => (held && held.repoId !== repoId ? null : held))
    setError(null)
    // An ask still in flight for the previous repository is not this repository's: its button is usable.
    setPending(false)
  }, [repoId])

  const resource = useResource<AdvisorDraftGetResponse>(
    draftId ? `advisor-draft:${draftId}` : null,
    useCallback((signal: AbortSignal) => api.draft(draftId ?? '', { signal }), [api, draftId]),
    {
      enabled: !!draftId,
      busy: (data) => data.draft.status === 'queued' || data.draft.status === 'running',
      fastInterval: RUNNING_POLL_MS,
      // 0 stops polling once the draft is terminal: a ready draft never changes again.
      slowInterval: 0,
      revalidateOn: ['advisor.updated', 'reset'],
    },
  )
  // Matched on id, not merely on presence: between "Ask again" and the first read of the new id the resource
  // may still hold the previous draft, and a proposal shown under the wrong id would be the wrong proposal.
  const held = resource.data?.draft ?? null
  const draft = held && draftId && held.draft_id === draftId ? held : null

  // A draft the server retired is let go of, so the panel offers the ask button instead of polling a dead id.
  const gone = resource.error?.code === 'draft_not_found'
  const expired = draft?.status === 'expired'
  useEffect(() => {
    if (draftId && (gone || expired)) setAsked(null)
  }, [draftId, gone, expired])

  const ask = useCallback(() => {
    const text = objective.trim()
    if (!repoId || !text) return
    setError(null)
    setPending(true)
    void api
      .requestPlanDraft(repoId, {
        space,
        objective: text,
        context: norm(context),
        project_type: projectType || null,
        // The Advisor answers in the language the user is reading (§2.7 takes the locale explicitly).
        locale,
        current,
      })
      .then((response) => {
        // A late answer for a repository the wizard has left is dropped, not held for that repository.
        if (liveRepo.current !== repoId) return
        setAsked({ repoId, draftId: response.draft.draft_id, objective: text, scope: current.scope })
      })
      .catch((thrown: unknown) => {
        if (liveRepo.current !== repoId) return
        setError(decodeError(thrown).code)
      })
      .finally(() => {
        if (liveRepo.current === repoId) setPending(false)
      })
  }, [api, repoId, space, objective, context, projectType, locale, current])

  const dismiss = useCallback(() => {
    setAsked(null)
    setError(null)
  }, [])

  const usable = usableResult(draft)
  const proposal = usable && draft && draft.plan_proposal ? draft.plan_proposal : null

  // Declared ahead of `effective`: a setting the proposal leaves null falls back to the PROPOSED scope's own
  // frontmatter when the proposal moves the scope, and that frontmatter arrives with the shadow plan.
  const [shadow, setShadow] = useState<EffectivePlan | null>(null)
  const [shadowBusy, setShadowBusy] = useState(false)
  const [shadowError, setShadowError] = useState<string | null>(null)

  // The proposed scope's frontmatter, once the shadow plan for it has landed. Matched on name: between
  // "Ask again" and the next shadow the state may still hold the previous proposal's plan.
  const proposedMeta = useMemo(() => {
    const meta = readScopeMeta(shadow?.scope_meta ?? null)
    return meta && proposal && meta.name === proposal.scope ? meta : null
  }, [shadow, proposal])

  const effective = useMemo<ProposalPicks | null>(() => {
    if (!proposal) return null
    // The server always sets `scope` (§1.4); the fallback only keeps the type honest.
    const scope = proposal.scope ?? current.scope
    // While the scope stays, a field the proposal left null keeps the human's value. When the proposal moves
    // the scope, the human's depth, test strategy and review cap are the OLD scope's synced frontmatter, and
    // carrying them over would send, say, `feature`'s review cap with a `bugfix` plan. The proposed scope's
    // own frontmatter is the honest fallback; until the shadow plan brings it, `null` lets the engine apply
    // exactly that, so the one re-preview this causes converges (the frontmatter reads the same every time).
    const moved = scope !== current.scope
    return {
      scope,
      depth: proposal.depth ?? (moved ? (proposedMeta?.depth ?? current.depth) : current.depth),
      test_strategy: proposal.test_strategy ?? (moved ? (proposedMeta?.testStrategy ?? null) : current.test_strategy),
      review_cap: proposal.review_cap ?? (moved ? (proposedMeta?.reviewCap ?? null) : current.review_cap),
      // Replaced wholesale: the server already produced the complete map for `scope`; nothing is merged here.
      overrides: proposal.overrides,
    }
  }, [proposal, proposedMeta, current.scope, current.depth, current.test_strategy, current.review_cap])

  // ---- the shadow preview ---- //
  const shadowBody = useMemo<PlanRequest | null>(
    () =>
      effective
        ? { space, ...effective, project_type: projectType || null, objective: null, label: null, context: null }
        : null,
    [effective, space, projectType],
  )
  const shadowKey = shadowBody ? canonicalJson(shadowBody) : ''
  const shadowRef = useRef<PlanRequest | null>(shadowBody)
  shadowRef.current = shadowBody
  const shadowSequence = useRef(0)

  useEffect(() => {
    const body = shadowRef.current
    // The ticket moves on the empty branch too: a preview still in flight for a proposal that was just
    // dismissed must not land as the shadow of nothing.
    const ticket = (shadowSequence.current += 1)
    if (!shadowKey || !repoId || !body) {
      setShadow(null)
      setShadowBusy(false)
      setShadowError(null)
      return
    }
    const timer = setTimeout(() => {
      setShadowBusy(true)
      api
        .planPreview(repoId, body)
        .then((answer) => {
          if (ticket !== shadowSequence.current) return
          setShadow(answer.plan)
          setShadowError(null)
        })
        .catch((thrown: unknown) => {
          if (ticket !== shadowSequence.current) return
          setShadow(null)
          setShadowError(decodeError(thrown).code)
        })
        .finally(() => {
          if (ticket === shadowSequence.current) setShadowBusy(false)
        })
    }, SHADOW_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [api, repoId, shadowKey])

  // The objective is what the draft read. The scope dimension is judged against BOTH what the Advisor saw
  // and what it proposed: after Use the wizard's scope IS the proposed scope, and a proposal must not be
  // called stale for having been accepted. `base_scope` is what the server computed the deltas against.
  // The cause is named, not just the fact: a changed objective is cured only by asking again, a moved scope
  // also by picking the proposed scope back, and the panel's copy says which.
  const proposedScope = proposal ? proposal.base_scope ?? proposal.scope : null
  const askedHere = asked !== null && asked.repoId === repoId ? asked : null
  const stale: PlanAdvisorState['stale'] = !askedHere
    ? null
    : askedHere.objective !== objective.trim()
      ? 'objective'
      : askedHere.scope !== current.scope && proposedScope !== current.scope
        ? 'scope'
        : null

  return { draft, proposal, effective, shadow, shadowBusy, shadowError, pending, error, stale, ask, dismiss }
}

/** Every slug the shadow diff moved came from the proposal; `PlanDiff` labels those rows as the Advisor's. */
export function advisorOrigins(plan: EffectivePlan): Readonly<Record<string, 'advisor'>> {
  return Object.fromEntries(plan.diff.map((entry) => [entry.slug, 'advisor' as const]))
}

// --------------------------------------------------------------------------- //
// the panel
// --------------------------------------------------------------------------- //

export interface PlanAdvisorPanelProps {
  advisor: PlanAdvisorState
  /** `settings.advisor.enabled && capabilities.advisor.available`, or `null` while Settings have not loaded. */
  available: boolean | null
  /** A repository that can host an intent and a non-empty objective — what the Advisor needs to read. */
  canAsk: boolean
  /** The draft whose proposal currently fills the fields, so Use is offered once per draft. */
  appliedDraftId: string | null
  /** Overrides the engine refused at the last apply and the wizard left out (FR-PLAN-004/005). */
  dropped: number
  /** Copy `effective` into the wizard's fields. Nothing is created. */
  onUse: () => void
  /** Restore the human's own picks from before the first apply. */
  onClear: () => void
}

export function PlanAdvisorPanel({
  advisor, available, canAsk, appliedDraftId, dropped, onUse, onClear,
}: PlanAdvisorPanelProps) {
  const i18n = useI18n()
  const { t, has } = i18n
  const { draft, proposal, effective, shadow, shadowBusy, shadowError, pending, error, stale, ask } = advisor
  const usable = usableResult(draft)
  const ready = draft && usable && proposal && effective ? { draft, usable, proposal, effective } : null
  const running = pending || draft?.status === 'queued' || draft?.status === 'running'
  // A ready draft without a proposal (nothing to copy) is a failure to the wizard, whatever its status says.
  const failed = draft?.status === 'failed' || draft?.status === 'expired' || (draft?.status === 'ready' && !ready)
  const title = ready ? t('advisor.titleDraft') : t('advisor.title')

  return (
    <section className="studio-block studio-advisor-plan" role="group" aria-label={title} data-state={ready ? 'ready' : running ? 'running' : failed ? 'failed' : 'idle'}>
      <h3>
        <Icon name="advisor" size={13} />
        {title}
      </h3>
      <div className="studio-advisor">
        {available === false ? (
          <p className="studio-advisor-copy">{t('advisor.disabled')}</p>
        ) : (
          // Polite, and only around the draft: the Advisor finishing is a status change the user asked for.
          <div aria-live="polite">
            {ready ? (
              <>
                <div className="studio-advisor-head">
                  <Chip tone="aim" icon="advisor">
                    {t('advisor.draftNotDecision')}
                  </Chip>
                </div>
                {ready.usable.summary ? <p className="studio-advisor-summary">{ready.usable.summary}</p> : null}

                <h4>{t('wizard.advisor.proposal')}</h4>
                {/*
                  Engine tokens verbatim, in every locale. A "keeps" row shows `effective`'s value — what Use
                  would copy — which for a proposal that moves the scope is that scope's own frontmatter.
                */}
                <dl className="studio-advisor-dl">
                  <dt>{t('wizard.preset.scope')}</dt>
                  <dd>
                    <Chip mono>{ready.effective.scope}</Chip>
                  </dd>
                  <dt>{t('wizard.preset.depth')}</dt>
                  <dd>
                    {ready.proposal.depth ? (
                      <Chip mono>{ready.proposal.depth}</Chip>
                    ) : (
                      t('wizard.advisor.keep', { value: ready.effective.depth })
                    )}
                  </dd>
                  <dt>{t('wizard.preset.review')}</dt>
                  <dd>
                    {ready.proposal.review_cap ? (
                      <Chip mono>{ready.proposal.review_cap}</Chip>
                    ) : (
                      t('wizard.advisor.keep', { value: ready.effective.review_cap ?? t('wizard.preset.fromScope') })
                    )}
                  </dd>
                  <dt>{t('wizard.preset.test')}</dt>
                  <dd>
                    {ready.proposal.test_strategy ? (
                      <Chip mono>{ready.proposal.test_strategy}</Chip>
                    ) : (
                      t('wizard.advisor.keep', { value: ready.effective.test_strategy ?? t('wizard.preset.fromScope') })
                    )}
                  </dd>
                </dl>

                <h4>{t('wizard.advisor.stages')}</h4>
                {shadow ? (
                  <>
                    <PlanDiff plan={shadow} hideEmpty origins={advisorOrigins(shadow)} />
                    {shadow.diff.length === 0 && shadow.issues.length === 0 ? (
                      <p className="studio-muted">{t('plan.diff.none', { scope: ready.effective.scope })}</p>
                    ) : null}
                  </>
                ) : shadowBusy ? (
                  <p className="studio-muted" role="status">
                    {t('plan.busy')}
                  </p>
                ) : shadowError ? (
                  <p className="studio-advisor-copy" data-tone="warn" role="status">
                    <Icon name="warn" size={13} />
                    {t('wizard.error.preview')} {has(`errors.${shadowError}`) ? t(`errors.${shadowError}`) : t('advisor.error.unknown')}
                  </p>
                ) : null}
                {ready.proposal.unresolved.length > 0 ? (
                  <p className="studio-advisor-copy" data-tone="warn">
                    <Icon name="warn" size={13} />
                    {plural(i18n, 'wizard.advisor.unresolved', ready.proposal.unresolved.length)}
                  </p>
                ) : null}

                <AdvisorDetails result={ready.usable} />

                {stale ? (
                  <p className="studio-advisor-copy" data-tone="warn" role="status">
                    <Icon name="warn" size={13} />
                    {stale === 'objective'
                      ? t('wizard.advisor.stale.objective')
                      : t('wizard.advisor.stale.scope', { scope: ready.effective.scope })}
                  </p>
                ) : null}

                <div className="studio-advisor-apply">
                  <button
                    type="button"
                    className="studio-btn studio-btn-sm"
                    disabled={stale !== null || !shadow || shadowBusy || appliedDraftId === ready.draft.draft_id}
                    onClick={onUse}
                  >
                    <Icon name="check" size={13} />
                    {t('wizard.advisor.use')}
                  </button>
                  <span className="studio-advisor-note">{t('wizard.advisor.useNote')}</span>
                </div>
                <div className="studio-advisor-actions">
                  <button type="button" className="studio-btn studio-btn-sm" disabled={pending || !canAsk} onClick={ask}>
                    <Icon name="advisor" size={13} />
                    {t('wizard.advisor.askAgain')}
                  </button>
                </div>
              </>
            ) : running ? (
              <p className="studio-advisor-copy">
                <Icon name="clock" size={13} />
                {t('advisor.running', { kind: t('advisor.kind.plan_draft') })}
              </p>
            ) : failed ? (
              <>
                <p className="studio-advisor-copy" data-tone="warn">
                  <Icon name="warn" size={13} />
                  {draft?.status === 'expired'
                    ? t('advisor.expired')
                    : has(`advisor.error.${draft?.error ?? ''}`)
                      ? t(`advisor.error.${draft?.error ?? ''}`)
                      : t('advisor.error.unknown')}
                </p>
                <div className="studio-advisor-actions">
                  <button type="button" className="studio-btn studio-btn-sm" disabled={pending || !canAsk} onClick={ask}>
                    <Icon name="advisor" size={13} />
                    {t('wizard.advisor.askAgain')}
                  </button>
                </div>
              </>
            ) : (
              <>
                <p className="studio-advisor-copy">{t('wizard.advisor.lede')}</p>
                <div className="studio-advisor-actions">
                  {/* The only thing in this file that asks: no effect, no mount, no keystroke (FR-ADV-001). */}
                  <button type="button" className="studio-btn studio-btn-sm" disabled={!canAsk || pending} onClick={ask}>
                    <Icon name="advisor" size={13} />
                    {t('wizard.advisor.ask')}
                  </button>
                </div>
                {!canAsk ? <p className="studio-help">{t('wizard.advisor.needObjective')}</p> : null}
              </>
            )}
          </div>
        )}

        {error ? (
          <p className="studio-advisor-copy" data-tone="warn" role="status">
            <Icon name="warn" size={13} />
            {has(`errors.${error}`) ? t(`errors.${error}`) : t('advisor.error.unknown')}
          </p>
        ) : null}

        {appliedDraftId !== null ? (
          <div className="studio-advisor-applied" role="status">
            <p className="studio-banner" data-tone="ok">
              <Icon name="check" size={15} />
              <span className="studio-grow">{t('wizard.advisor.applied')}</span>
            </p>
            {dropped > 0 ? (
              <p className="studio-advisor-copy" data-tone="warn">
                <Icon name="warn" size={13} />
                {plural(i18n, 'wizard.advisor.dropped', dropped)}
              </p>
            ) : null}
            <button type="button" className="studio-btn studio-btn-sm" onClick={onClear}>
              {t('wizard.advisor.clear')}
            </button>
          </div>
        ) : null}

        <p className="studio-disclaim">
          <Icon name="lock" size={13} />
          <span>{t('advisor.disclaimer')}</span>
        </p>
      </div>
    </section>
  )
}
