/**
 * The detail pane: header, tabs, delivery state, the type-specific body, the confirmation, the bar.
 *
 * This file owns the parts of a decision that are the same for every action type, and nothing else.
 * The body of the Decision tab, and the Artifacts / Review / Activity / Conversation panels, are
 * plugged in by the areas that own them — by dropping a file at `actions/templates/<type>.tsx` or
 * `actions/tabs/<tab>.tsx` (build-time glob), or by calling `registerActionTemplate` /
 * `registerTabPanel` at module scope. The same two-way arrangement the shell uses for views, for the
 * same reason: the areas are built in parallel and neither a hard import nor a registry alone covers
 * both cases.
 *
 * The four responsibilities that cannot be delegated:
 *
 *  1. **One card, from the server.** The queue's copy paints instantly so the pane never blanks, but
 *     the authoritative card is whichever copy is newest — including the refreshed card a `409
 *     action_stale` carries. A locally patched card would let the UI show an approval it invented.
 *  2. **The payload.** A decision plus the user's draft becomes exactly one `SubmitPayload`, and
 *     `wireTextFor` turns that into the bytes the confirmation shows and the server re-derives.
 *  3. **Drafts survive.** Unsent feedback and answers live in `localStorage` per action id, so
 *     switching to another card and back loses nothing (PRD §10.2). Because nothing is discarded there
 *     is nothing to warn about.
 *  4. **One submit at a time.** `useSubmit` holds the module-level guard; this pane simply never offers
 *     a second control while it is busy.
 */

import {
  createContext, useCallback, useContext, useEffect, useMemo, useState, type ComponentType, type ReactNode,
} from 'react'
import { ChatEmbed } from '@kirocrew/app-sdk'

import { Icon } from '../shell/Icon'
import { useI18n } from '../i18n'
import { StudioApiError, type StudioApi } from '../lib/api'
import { at } from '../lib/format'
import { isRepositoryUnavailable } from '../lib/actionQueue'
import { DEFAULT_TAB, EMPTY_ROUTE, type Navigate, type StudioRoute, type Tab } from '../lib/route'
import { useResource } from '../lib/useResource'
import type {
  ActionCard, ActionDetailResponse, ActionTransition, ActionType, Decision, DecisionSpec, ResolvePayload,
  SubmitPayload,
} from '../lib/types'
import { ArtifactsTab } from '../artifacts/ArtifactsTab'
import { ReviewTab } from '../artifacts/ReviewTab'
import { ActionBar } from './ActionBar'
import { ConfirmPanel, type PendingDecision } from './ConfirmPanel'
import { DeliveryStrip, statusLabel } from './DeliveryStrip'
import { DetailHeader } from './DetailHeader'
import { DetailTabs, tabsFor } from './DetailTabs'
import { isRefreshing } from './QueueRow'
import { pendingQuestions, useSubmit, wireTextFor } from './useSubmit'

// --------------------------------------------------------------------------- //
// drafts
// --------------------------------------------------------------------------- //

export interface AnswerDraft {
  option_letters: string[]
  free_text: string | null
}

/** Everything a user can type into a decision before sending it. Keyed by action id in storage. */
export interface ActionDraft {
  feedback: string
  /** Keyed by the question's `index`, as a string because JSON object keys are strings. */
  answers: Record<string, AnswerDraft>
  scope: string
  freeText: string
  inputKind: 'scope' | 'free_text'
  summaryChoice: 'looks_correct' | 'request_changes'
  /**
   * The Advisor draft whose answers were filled in without a click (FR-ADV-011), or `null`.
   *
   * It lives in the draft, not in template state, because "once per draft" has to mean once for good: a
   * reload of a form the user deliberately cleared must not count as a fresh chance to fill it again.
   */
  advisorApplied: string | null
}

export const EMPTY_DRAFT: ActionDraft = {
  feedback: '', answers: {}, scope: '', freeText: '', inputKind: 'free_text', summaryChoice: 'looks_correct',
  advisorApplied: null,
}

const DRAFT_PREFIX = 'aidlc-studio:draft:'

function readDraft(actionId: string): ActionDraft {
  try {
    const raw = localStorage.getItem(DRAFT_PREFIX + actionId)
    if (!raw) return EMPTY_DRAFT
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return EMPTY_DRAFT
    // Spread over the default rather than trusting the stored shape: an older build's draft is missing
    // fields, and `undefined` in `feedback` would make the textarea uncontrolled mid-edit. A draft stored
    // before `advisorApplied` existed therefore reads as `null`, which is also the truthful answer for it.
    return { ...EMPTY_DRAFT, ...(parsed as Partial<ActionDraft>) }
  } catch {
    return EMPTY_DRAFT
  }
}

function writeDraft(actionId: string, draft: ActionDraft): void {
  try {
    if (draft === EMPTY_DRAFT) localStorage.removeItem(DRAFT_PREFIX + actionId)
    else localStorage.setItem(DRAFT_PREFIX + actionId, JSON.stringify(draft))
  } catch {
    // Storage full or disabled: the draft still works for this session.
  }
}

function clearDraft(actionId: string): void {
  try {
    localStorage.removeItem(DRAFT_PREFIX + actionId)
  } catch {
    // Nothing to do.
  }
}

// --------------------------------------------------------------------------- //
// the plug-in surface for templates and tab panels
// --------------------------------------------------------------------------- //

export interface TemplateProps {
  card: ActionCard
  /** Transitions and advisor drafts; `null` until the first read lands. */
  detail: ActionDetailResponse | null
  draft: ActionDraft
  setDraft: (patch: Partial<ActionDraft>) => void
  /** True while the card's evidence is being re-read: show it, do not let it be submitted against. */
  refreshing: boolean
  api: StudioApi
  route: StudioRoute
  go: Navigate
  /** Re-read this card now (after an advisor draft, a retry, an artifact write). */
  reload: () => void
}

export type TemplateComponent = ComponentType<TemplateProps>

const templates = new Map<ActionType, TemplateComponent>()
const panels = new Map<Tab, TemplateComponent>()

/** Register the Decision-tab body for an action type. Call at module scope. */
export function registerActionTemplate(type: ActionType, component: TemplateComponent): void {
  templates.set(type, component)
}

/** Register the body of a non-decision tab. Call at module scope. */
export function registerTabPanel(tab: Tab, component: TemplateComponent): void {
  panels.set(tab, component)
}

function globbed(pattern: Record<string, unknown>, re: RegExp): Map<string, TemplateComponent> {
  const found = new Map<string, TemplateComponent>()
  for (const [path, module] of Object.entries(pattern)) {
    const slug = re.exec(path)?.[1]
    if (!slug) continue
    const exported = (module as { default?: unknown }).default
    if (typeof exported === 'function') found.set(slug, exported as TemplateComponent)
  }
  return found
}

function templateFor(type: ActionType): TemplateComponent | undefined {
  const files = globbed(import.meta.glob('./templates/*.tsx', { eager: true }), /\/templates\/([^/]+)\.tsx$/)
  for (const [slug, component] of globbed(import.meta.glob('./templates/*/index.tsx', { eager: true }), /\/templates\/([^/]+)\/index\.tsx$/)) {
    files.set(slug, component)
  }
  return templates.get(type) ?? files.get(type)
}

function panelFor(tab: Tab): TemplateComponent | undefined {
  const files = globbed(import.meta.glob('./tabs/*.tsx', { eager: true }), /\/tabs\/([^/]+)\.tsx$/)
  return panels.get(tab) ?? files.get(tab)
}

// --------------------------------------------------------------------------- //
// payloads
// --------------------------------------------------------------------------- //

/** The answers of the questions still pending, in file order — one atomic submit (FR-Q-006). */
function answersFrom(card: ActionCard, draft: ActionDraft) {
  return pendingQuestions(card.evidence.questions).map((question) => {
    const stored = draft.answers[String(question.index)]
    return {
      index: question.index,
      option_letters: stored?.option_letters ?? [],
      free_text: stored?.free_text ?? null,
    }
  })
}

/** A decision plus the user's draft as exactly one wire payload, or `null` for a studio-only decision. */
export function payloadFor(decision: Decision, card: ActionCard, draft: ActionDraft): SubmitPayload | null {
  switch (decision) {
    case 'approve': return { decision: 'approve' }
    case 'accept_as_is': return { decision: 'accept_as_is' }
    case 'approve_plan': return { decision: 'approve_plan' }
    case 'request_changes': return { decision: 'request_changes', feedback: draft.feedback }
    case 'request_plan_changes': return { decision: 'request_plan_changes', feedback: draft.feedback }
    case 'confirm_summary':
      // One decision, two shapes. Typed feedback IS the request: sending `looks_correct` while the box
      // holds a complaint would record the opposite of what the user wrote.
      return draft.summaryChoice === 'request_changes' || draft.feedback.trim()
        ? { decision: 'confirm_summary', choice: 'request_changes', feedback: draft.feedback }
        : { decision: 'confirm_summary', choice: 'looks_correct' }
    case 'answers': return { decision: 'answers', answers: answersFrom(card, draft) }
    case 'provide_input':
      return draft.inputKind === 'scope'
        ? { decision: 'provide_input', kind: 'scope', scope: draft.scope }
        : { decision: 'provide_input', kind: 'free_text', text: draft.freeText }
    case 'run': return { decision: 'run' }
    case 'resume': return { decision: 'resume' }
    case 'prepare_commit': return { decision: 'prepare_commit' }
    default: return null
  }
}

/** The `resolve` body for a studio-only decision, or `null` when the decision is not one. */
export function resolvePayloadFor(decision: Decision, card: ActionCard): ResolvePayload | null {
  switch (decision) {
    case 'resubmit':
      return { decision: 'resubmit', acknowledged_evidence_sha256: card.acknowledged_evidence_sha256 ?? '' }
    case 'pick_intent':
      return { decision: 'pick_intent', intent_dir: card.intent.intent_dir }
    case 'mark_not_delivered':
      // The evidence the backend needs is whatever it already recorded; the client adds nothing it
      // cannot prove. An empty object here means "use your own proof", not "trust me".
      return { decision: 'mark_not_delivered' }
    case 'rebind_session':
    case 'acknowledge':
    case 'reconcile':
    case 'retry_now':
    case 'keep_paused':
    case 'run_now':
      return { decision }
    default:
      return null
  }
}

// --------------------------------------------------------------------------- //
// the pane
// --------------------------------------------------------------------------- //

/** Whichever card the server touched most recently. ISO timestamps compare as strings. */
function newer(a: ActionCard | null, b: ActionCard | null): ActionCard | null {
  if (!a) return b
  if (!b) return a
  if (a.action_id !== b.action_id) return b
  if (a.updated_at === b.updated_at) return a.status_generation >= b.status_generation ? a : b
  return a.updated_at > b.updated_at ? a : b
}

const DraftContext = createContext<{ draft: ActionDraft; setDraft: (patch: Partial<ActionDraft>) => void } | null>(null)

/** For a template that renders its own inputs deep inside a subtree. */
export function useActionDraft() {
  return useContext(DraftContext)
}

export interface DetailShellProps {
  /** The selected action id, from the route. Empty when nothing is selected. */
  actionId: string
  /** The queue's copy, so the pane paints before the detail read lands. */
  queueCard: ActionCard | null
  api: StudioApi
  route: StudioRoute
  go: Navigate
  /** True when the server says grouped answers are verified (§2.4 `grouped_answers`). */
  groupedAnswers: boolean
  /** Refetch the queue after a settle. */
  onQueueChanged: () => void
}

export function DetailShell({
  actionId, queueCard, api, route, go, groupedAnswers, onQueueChanged,
}: DetailShellProps) {
  const { t } = useI18n()
  const [override, setOverride] = useState<ActionCard | null>(null)
  const [chosen, setChosen] = useState<DecisionSpec | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [draft, setDraftState] = useState<ActionDraft>(EMPTY_DRAFT)

  const detail = useResource<ActionDetailResponse>(
    actionId ? `action:${actionId}` : null,
    useCallback((signal) => api.action(actionId, { signal }), [api, actionId]),
    { enabled: !!actionId },
  )

  // A new selection is a new decision: the previous card's confirmation, acknowledgement and
  // server-supplied override must not survive it, or the pane would offer to send the old text.
  useEffect(() => {
    setOverride(null)
    setChosen(null)
    setAcknowledged(false)
    setDraftState(actionId ? readDraft(actionId) : EMPTY_DRAFT)
  }, [actionId])

  // Every patch is written through to storage, not only the ones a submit would read: `advisorApplied` is
  // how FR-ADV-011 remembers that this card was pre-filled already, and a marker that lived in React state
  // alone would let a reload fill the form a second time.
  const setDraft = useCallback(
    (patch: Partial<ActionDraft>) => {
      setDraftState((current) => {
        const next = { ...current, ...patch }
        if (actionId) writeDraft(actionId, next)
        return next
      })
    },
    [actionId],
  )

  const submitter = useSubmit({
    api,
    onCard: useCallback((card: ActionCard) => setOverride((current) => newer(current, card)), []),
    onSettled: onQueueChanged,
  })
  const { state: submit, busy } = submitter

  const card = useMemo(
    () => newer(override, detail.data?.action ?? queueCard),
    [override, detail.data, queueCard],
  )

  const draftValue = useMemo(() => ({ draft, setDraft }), [draft, setDraft])

  // A settled send is the end of this draft's life: keeping it would re-populate the box the next time
  // the card appears, and offer to send text the conductor already has.
  useEffect(() => {
    if (submit.stage === 'settled' && submit.actionId === actionId) {
      clearDraft(actionId)
      setChosen(null)
    }
  }, [submit.stage, submit.actionId, actionId])

  if (detail.error && ['action_not_found', 'repo_not_found', 'intent_not_found'].includes(detail.error.code)) {
    return (
      <section className="studio-detail" aria-label={t('detail.label')}>
        <div className="studio-empty studio-detail-empty" data-layout="stack" role="status">
          <Icon name="inbox" size={18} />
          <h1 className="studio-empty-title">{t('detail.gone.title')}</h1>
          <p className="studio-muted">{t('detail.gone.body')}</p>
          <button type="button" className="studio-btn" onClick={() => go({ ...EMPTY_ROUTE, view: 'actions' })}>
            {t('detail.currentActions')}
          </button>
        </div>
      </section>
    )
  }

  if (!actionId || !card) {
    return (
      <section className="studio-detail" aria-label={t('detail.label')}>
        <div className="studio-empty studio-detail-empty" data-layout="stack">
          <Icon name="inbox" size={18} />
          <p className="studio-empty-title">
            {actionId && detail.loading ? t('common.loading') : t('detail.noSelection.title')}
          </p>
          <p className="studio-muted">
            {actionId && detail.error ? t(`errors.${detail.error.code}`) : t('detail.noSelection.body')}
          </p>
        </div>
      </section>
    )
  }

  const refreshing = isRefreshing(card)
  const tabs = tabsFor(card, detail.data)
  const activeTab: Tab = tabs.some((entry) => entry.tab === route.tab) ? route.tab : DEFAULT_TAB
  const Template = templateFor(card.type)
  const Panel = activeTab === 'decision' ? null : panelFor(activeTab)
  const openAnchor = (anchor: string) => go({ tab: 'artifacts', anchor })

  /** Why a decision cannot be sent yet — the same reason the confirmation shows. */
  const blockedFor = (spec: DecisionSpec): string | null => {
    if (isRepositoryUnavailable(card) && spec.lane === 'human_lane') return 'detail.repoUnavailable.footer'
    if (spec.lane === 'studio_only') {
      // `resubmit` echoes the digest of the evidence the UI displayed; without it the server cannot tell
      // an informed resubmission from a replay and would refuse as stale.
      if (spec.decision === 'resubmit' && !card.acknowledged_evidence_sha256) return 'confirm.blocked.evidenceMissing'
      return null
    }
    if (refreshing) return 'confirm.blocked.refreshing'
    const payload = payloadFor(spec.decision, card, draft)
    if (!payload) return null
    if (wireTextFor(payload, card.evidence.questions, groupedAnswers) !== null) return null
    if (spec.decision === 'request_changes' || spec.decision === 'request_plan_changes') return 'confirm.blocked.feedbackRequired'
    if (spec.decision === 'confirm_summary') return 'confirm.blocked.feedbackRequired'
    if (spec.decision === 'provide_input') return 'confirm.blocked.inputRequired'
    if (spec.decision === 'answers') {
      if (card.evidence.questions?.mode === 'degraded') return 'confirm.blocked.questionsUnavailable'
      const count = pendingQuestions(card.evidence.questions).length
      if (count > 1 && !groupedAnswers) return 'confirm.blocked.groupedAnswers'
      return 'confirm.blocked.answersIncomplete'
    }
    return 'confirm.blocked.noWireText'
  }

  /**
   * Why the SEND is refused, for the reasons the bar must not be the only place to say.
   *
   * `blockedFor` also feeds `ActionBar`, which disables a blocked control and puts the reason in its
   * `title`. That is right for a reason the user can clear in the pane they are already looking at (type
   * the feedback, answer the question), and wrong for one they cannot: a disabled `<button>` fires no
   * pointer events in Chromium, so in the desktop host that title is never shown to anybody. These two
   * reasons therefore reach only the confirmation, which states them as a sentence beside a send control
   * it disables — the confirmation stays reachable, so the explanation does too.
   *
   *  - **No canonical conversation** (FR-SES-006, §2.13). A human-lane or host-control decision is
   *    injected as a real user turn into the slot bound to this intent; with nothing bound
   *    `POST …/submit` answers `409 session_unbound` (§1.12 step 5) before a byte moves. Offering that
   *    send is exactly the bug this fixes: the click fired, the server refused, and the refusal rendered
   *    at the top of a detail pane several screens above the button that was pressed.
   *  - **A refusal that already happened.** `submit.refusal` is rendered above the card body, that same
   *    long scroll away from the click. Repeated here it is the server's own answer next to the control
   *    that asked, and the send is disabled: the same bytes against the same captured state can only be
   *    refused again. Choosing the decision again from the bar resets the attempt (`choose`), so this is
   *    not a dead end. `stale` is deliberately excluded — its refreshed card and assertive banner are the
   *    news there, and the re-read captured state makes a second send meaningful.
   */
  const sendBlockedFor = (spec: DecisionSpec): string | null => {
    const blocked = blockedFor(spec)
    if (blocked) return blocked
    // `evidence.session` is the binding joined with the host's slot (`projection._session_json`): it is
    // null exactly when neither the binding nor the host names a slot, which is the state the backend
    // refuses as `session_unbound`. A slot that is bound but gone or re-pointed (`slot_mismatch`) reads
    // identically to a healthy idle one in this card, so that one is left to the round trip.
    if (spec.lane !== 'studio_only' && !card.evidence.session?.slot_key) return 'confirm.blocked.sessionUnbound'
    if (submit.stage === 'refused' && submit.actionId === card.action_id && submit.refusal) {
      return `errors.${submit.refusal.code}`
    }
    return null
  }

  /*
   * The confirmation, derived rather than frozen.
   *
   * Only the chosen decision is state; the payload and the wire text are recomputed from the current
   * draft on every render. That is what makes "what you see is what is sent" true even if the user edits
   * the feedback box while the panel is open — a snapshot taken when the panel opened would display one
   * string and send it while the box above showed another.
   */
  const pending: PendingDecision | null = chosen
    && card.decisions.some((spec) => spec.decision === chosen.decision)
    && !(isRepositoryUnavailable(card) && chosen.lane === 'human_lane')
    ? (() => {
        const payload = chosen.lane === 'studio_only' ? null : payloadFor(chosen.decision, card, draft)
        return {
          spec: chosen,
          payload,
          resolvePayload: chosen.lane === 'studio_only' ? resolvePayloadFor(chosen.decision, card) : null,
          wire: payload ? wireTextFor(payload, card.evidence.questions, groupedAnswers) : null,
          blockedKey: sendBlockedFor(chosen),
        }
      })()
    : null

  const choose = (spec: DecisionSpec) => {
    // Pressing the same control again closes the confirmation rather than re-opening it: an expanded
    // disclosure whose trigger does nothing is a control that looks broken.
    if (chosen?.decision === spec.decision) {
      setChosen(null)
      return
    }
    submitter.reset()
    setAcknowledged(false)
    setChosen(spec)
  }

  const send = () => {
    if (!pending) return
    if (pending.spec.lane === 'studio_only') {
      if (pending.resolvePayload) void submitter.resolve({ card, payload: pending.resolvePayload })
      return
    }
    if (pending.spec.lane === 'host_control') {
      // The same two-phase machine from a different first endpoint (§1.12): `force-stop` inserts the
      // action and moves it to Delivering in one call, and its `receipt.host` is the stop route. No
      // prompt, therefore no wire text to confirm — only the consequence.
      void submitter.submit({
        card,
        payload: null,
        clientWireText: '',
        begin: () => api.forceStop(card.repo.repo_id, card.intent.intent_key),
      })
      return
    }
    if (!pending.payload || pending.wire === null) return
    void submitter.submit({ card, payload: pending.payload, clientWireText: pending.wire })
  }

  const refusal = submit.refusal
  const staleRefusal = submit.stage === 'stale'

  return (
    <section className="studio-detail" aria-label={t('detail.label')}>
      <DetailHeader card={card} onOpenCurrent={() => go({
        ...EMPTY_ROUTE, view: 'actions', repo: card.repo.repo_id, intent: card.intent.intent_key,
      })} />
      <DetailTabs tabs={tabs} active={activeTab} onSelect={(tab) => go({ tab })} />

      <div
        className="studio-detail-body"
        id={`studio-panel-${activeTab}`}
        role="tabpanel"
        aria-labelledby={`studio-tab-${activeTab}`}
        tabIndex={-1}
      >
        <div className="studio-reading">
          {refusal ? (
            // Assertive only for a stale refusal: the evidence moved under the user's decision, and the
            // refreshed card above them is now describing a different situation.
            <div className="studio-banner" data-tone="danger" role={staleRefusal ? 'alert' : 'status'}>
              <Icon name="warn" size={15} />
              <div className="studio-grow">
                <p>{t(`errors.${refusal.code}`)}</p>
                <p className="studio-muted">{staleRefusal ? t('detail.staleRefused')
                  : card.delivery.delivered_at || card.delivery.transcript_row
                    ? t('detail.additionalAttemptBlocked') : t('detail.nothingSent')}</p>
                <RefusalDetails refusal={refusal} onOpenAction={(action) => go({
                  ...EMPTY_ROUTE, view: 'actions', repo: card.repo.repo_id, action,
                })} />
              </div>
            </div>
          ) : null}

          {submit.attempt && !submit.attempt.reported ? (
            // The host has the message and Studio could not record the outcome. Assertive: the user must
            // not close the tab believing nothing happened.
            <div className="studio-banner" data-tone="warn" role="alert">
              <Icon name="warn" size={15} />
              <span className="studio-grow">{t('delivery.reportFailed')}</span>
            </div>
          ) : null}

          {!isRepositoryUnavailable(card) || card.delivery.delivering_at ? <DeliveryStrip card={card} /> : null}

          {activeTab === 'decision' ? (
            isRepositoryUnavailable(card) ? (
              <section className="studio-block" aria-label={t('detail.repoUnavailable.saved')}>
                <p>{t(card.repo.availability_detail === 'path_missing'
                  ? 'detail.repoUnavailable.pathMissing' : 'detail.repoUnavailable.unreadable')}</p>
                <p className="studio-mono studio-wrap-any">{card.repo.canonical_path}</p>
                <p className="studio-muted">{t('detail.repoUnavailable.body')}</p>
                <button type="button" className="studio-btn" onClick={() => go({
                  ...EMPTY_ROUTE, view: 'repos', repo: card.repo.repo_id,
                })}>
                  <Icon name="repo" size={14} /> {t('detail.repoUnavailable.manage')}
                </button>
              </section>
            ) : Template ? (
              <DraftContext.Provider value={draftValue}>
                <Template
                  card={card}
                  detail={detail.data}
                  draft={draft}
                  setDraft={setDraft}
                  refreshing={refreshing}
                  api={api}
                  route={route}
                  go={go}
                  reload={detail.refresh}
                />
              </DraftContext.Provider>
            ) : (
              <MissingBody kind={card.type} />
            )
          ) : Panel ? (
            <DraftContext.Provider value={draftValue}>
              <Panel
                card={card}
                detail={detail.data}
                draft={draft}
                setDraft={setDraft}
                refreshing={refreshing}
                api={api}
                route={route}
                go={go}
                reload={detail.refresh}
              />
            </DraftContext.Provider>
          ) : activeTab === 'artifacts' ? (
            <ArtifactsTab
              repoId={card.repo.repo_id}
              intentKey={card.intent.intent_key}
              // The card's captured metadata, not a fresh listing: the decision is about the artifact as
              // it was when the gate opened, and re-listing could show one written since.
              artifacts={card.evidence.artifacts}
              findings={card.evidence.review?.findings ?? null}
              artifactId={route.artifact}
              onSelectArtifact={(artifactId) => go({ artifact: artifactId })}
              anchor={route.anchor}
              onOpenAnchor={openAnchor}
            />
          ) : activeTab === 'review' ? (
            <ReviewTab
              repoId={card.repo.repo_id}
              intentKey={card.intent.intent_key}
              review={card.evidence.review}
              anchor={route.anchor}
              onOpenAnchor={openAnchor}
            />
          ) : activeTab === 'activity' ? (
            <TransitionList transitions={detail.data?.transitions ?? []} />
          ) : activeTab === 'conversation' ? (
            <Conversation slotKey={card.evidence.session?.slot_key ?? ''} />
          ) : (
            <MissingBody kind={activeTab} />
          )}
        </div>
      </div>

      {pending ? (
        <ConfirmPanel
          card={card}
          pending={pending}
          submit={submit}
          busy={busy}
          refreshing={refreshing && pending.spec.lane !== 'studio_only'}
          acknowledged={acknowledged}
          onAcknowledge={setAcknowledged}
          onSend={send}
          onCancel={() => setChosen(null)}
          onOpenBlockingAction={(action) => go({
            ...EMPTY_ROUTE, view: 'actions', repo: card.repo.repo_id, action,
          })}
        />
      ) : null}

      <ActionBar
        card={card}
        submit={submit}
        busy={busy}
        refreshing={refreshing}
        pending={pending}
        onChoose={choose}
        onBackToQueue={() => go({ action: '', artifact: '' })}
        blockedFor={blockedFor}
      />
    </section>
  )
}

/**
 * A refusal's own details, printed verbatim.
 *
 * `slot_busy` carries the reasons the conversation is busy, `state_inconsistent` the findings that
 * disagree. Both are the backend's words for a situation the user has to resolve elsewhere, so they are
 * shown as-is rather than summarised into a sentence that would drop the one reason that mattered.
 */
function RefusalDetails({ refusal, onOpenAction }: { refusal: StudioApiError; onOpenAction: (action: string) => void }) {
  const { t } = useI18n()
  const owner = refusal.details['owner']
  if (refusal.code === 'repo_busy' && owner && typeof owner === 'object' && 'action_id' in owner &&
      typeof owner.action_id === 'string' && owner.action_id) {
    return (
      <button type="button" className="studio-btn" onClick={() => onOpenAction(owner.action_id as string)}>
        {t('detail.repoBusy.openOwner')}
      </button>
    )
  }
  const reasons = refusal.details['reasons']
  const findings = refusal.details['findings']
  const list = Array.isArray(reasons) ? reasons : Array.isArray(findings) ? findings : null
  if (!list || list.length === 0) return null
  return (
    <ul className="studio-refusal-details studio-mono">
      {list.map((entry, index) => (
        <li key={index}>{typeof entry === 'string' ? entry : JSON.stringify(entry)}</li>
      ))}
    </ul>
  )
}

/**
 * Studio's own record of what it did with this decision.
 *
 * `transitions` comes from `GET /actions/{id}` and exists nowhere else in the UI, which is why the tab is
 * built here rather than reusing the Activity page's timeline: that one reads the intent's activity ring,
 * while this is the audit of Studio's own state machine — the generations that explain why a
 * compare-and-swap was refused. Both are useful; conflating them would present Studio's records as
 * AI-DLC's own events (FR-ADV-009).
 */
function TransitionList({ transitions }: { transitions: ActionTransition[] }) {
  const i18n = useI18n()
  const { t } = i18n
  if (transitions.length === 0) {
    return (
      <div className="studio-block">
        <p className="studio-muted">{t('detail.transitions.empty')}</p>
      </div>
    )
  }
  return (
    <section className="studio-block">
      <h3>
        <Icon name="activity" size={13} /> {t('detail.transitions.title')}
      </h3>
      <ol className="studio-translist">
        {transitions.map((row, index) => (
          <li key={`${row.at}-${index}`}>
            <span className="studio-mono studio-muted">{at(i18n, row.at)}</span>
            <span className="studio-grow">
              {t('detail.transitions.row', {
                from: row.from_status ? t(`enum.actionStatus.${row.from_status}`) : t('detail.transitions.initial'),
                to: statusLabel(i18n, row.to_status, row.reason),
              })}
              {/* The reason is Studio's own machine-readable word for the edge; shown verbatim so a
                  support conversation can quote it. */}
              {row.reason ? <span className="studio-mono studio-muted"> · {row.reason}</span> : null}
            </span>
            <span className="studio-mono studio-muted">{t('detail.transitions.generation', { n: i18n.fmt.number(row.generation) })}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}

/**
 * The canonical session, embedded.
 *
 * `ChatEmbed` is the host's own transcript component (§2.13): it reads the slot and posts through the
 * user's session, which is the only way a message in this pane is a real human turn. Studio does not
 * render its own transcript, because a second renderer would have to decide what a message means.
 */
function Conversation({ slotKey }: { slotKey: string }) {
  const { t } = useI18n()
  if (!slotKey) {
    return (
      <div className="studio-block">
        <p className="studio-muted">{t('detail.conversation.none')}</p>
      </div>
    )
  }
  return (
    <section className="studio-block">
      <h3>
        <Icon name="slack" size={13} /> {t('detail.conversation.title')}
      </h3>
      <p className="studio-muted studio-conv-note">{t('detail.conversation.note')}</p>
      <div className="studio-conv">
        <ChatEmbed slotKey={slotKey} />
      </div>
    </section>
  )
}

/** No template for this type in this build. Says so, rather than showing an empty pane. */
function MissingBody({ kind }: { kind: string }): ReactNode {
  const { t } = useI18n()
  return (
    <div className="studio-block">
      <p className="studio-muted">{t('detail.noTemplate', { kind })}</p>
    </div>
  )
}
