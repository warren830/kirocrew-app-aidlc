/**
 * The AI Advisor block: a draft, never a decision.
 *
 * Every rule in PRD §9.13 that the UI is responsible for is visible here:
 *
 *  - **It never runs on this block's initiative** (FR-ADV-001). No effect here requests a draft on
 *    mount, and the not-run state is the default state; a click is the only thing in this file that
 *    leaves it. A draft may nonetheless be there already, because the repository owner granted drafting
 *    ahead once in Settings (`advisor.auto_draft_repo_ids`) and the reconciler started it server-side —
 *    the point of that grant is that nobody is watching yet, so the block finds the draft rather than
 *    asking for it.
 *  - **It never preselects and never submits** (FR-ADV-007). `Apply the drafted selections` writes into
 *    the draft the user still has to review and the copy says so; nothing in this file can reach the
 *    submit machine, which lives in the Action Center behind the confirmation panel.
 *  - **An automatically prepared draft may fill a form once** (FR-ADV-011). `request.auto === true` is
 *    the question template's licence to copy the drafted answers into its inputs, labelled as the
 *    Advisor's words and reversible; a draft the user clicked for keeps its explicit apply button,
 *    nothing is ever sent, and no gate control is ever pre-filled.
 *  - **Unanswerable claims are labelled** (FR-ADV-008). `needs_your_decision` is a row of its own with a
 *    warning chip, not a footnote under the summary.
 *  - **A draft whose evidence moved is refused.** `neutrality.ok === false` means the state file, the
 *    question digest, the directive, the turn counter or the audit shards changed while the Advisor was
 *    reading (`advisor.py:_neutrality`), so the draft describes a situation that no longer exists: the
 *    apply controls disappear and the block says why.
 *
 * The model's own words — summary, evidence, assumptions, alternatives — pass through verbatim; only
 * Studio's frame around them is translated. Polling shares `useResource`'s key with the Advisor drawer,
 * so having both open is still one request per tick.
 */

import { useCallback, useMemo, useState } from 'react'

import { useI18n } from '../i18n'
import { decodeError, type StudioApi } from '../lib/api'
import { useResource } from '../lib/useResource'
import type { ActionCard, ActionType, AdvisorDraft, AdvisorDraftGetResponse, AdvisorKind, DraftResult } from '../lib/types'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { Block } from './DecisionControls'

/** Poll cadence while the subagent is still working (matches the Advisor drawer). */
const RUNNING_POLL_MS = 2_000

/**
 * Which draft kinds the backend accepts per action type.
 *
 * Mirrors `ADVISOR_KINDS_BY_TYPE` in `backend/studio/advisor.py`: a kind the table does not list earns
 * `invalid_decision` from `POST /advisor/draft`, i.e. a button that can only fail. `question_explain` is
 * absent from the group block on purpose — it is per-question, and belongs in the question card where
 * the index it needs is unambiguous.
 */
const KINDS_BY_TYPE: Partial<Record<ActionType, readonly AdvisorKind[]>> = {
  gate: ['gate_analysis', 'request_changes_draft'],
  question: ['question_draft'],
  missing_input: ['diagnose'],
  recovery: ['diagnose'],
  delivery_uncertain: ['diagnose'],
  failure: ['diagnose'],
  circuit_breaker: ['diagnose'],
  install_conflict: ['diagnose'],
  budget_stop: ['diagnose'],
}

export function advisorKindsFor(type: ActionType): readonly AdvisorKind[] {
  return KINDS_BY_TYPE[type] ?? []
}

/**
 * The result a surface is allowed to act on, or `null`.
 *
 * `neutrality.ok === false` means the evidence moved while the Advisor was reading (`advisor.py`
 * `_neutrality`), so the draft describes a situation that no longer exists. Exported because two
 * surfaces gate on this and they must gate on the *same* thing: the apply controls below, and the
 * automatic pre-fill in the question template (FR-ADV-011). A pre-fill judged by a looser predicate
 * than the button beside it is precisely how a superseded draft ends up in a form.
 */
export function usableResult(draft: AdvisorDraft | null | undefined): DraftResult | null {
  if (!draft || draft.status !== 'ready' || !draft.result) return null
  return draft.neutrality?.['ok'] === false ? null : draft.result
}

/**
 * One Advisor conversation for one card.
 *
 * A hook rather than state inside the block, because two surfaces drive the same request: the block's
 * group-level buttons and the per-question `Explain` / `Draft this` (PRD §9.5). Two independent states
 * would let a question ask for a draft while the block still showed "has not run".
 */
export interface AdvisorState {
  draft: AdvisorDraft | null
  /** The kind currently being asked for, or `null`. */
  pending: AdvisorKind | null
  /** A decoded backend error code (`advisor_unavailable` when the Advisor is off in Settings). */
  error: string | null
  request: (kind: AdvisorKind, questionIndex?: number | null) => void
}

export function useAdvisor(
  card: ActionCard,
  api: StudioApi,
  drafts?: readonly AdvisorDraft[] | null,
): AdvisorState {
  const { locale } = useI18n()

  // The newest draft this card already has: the block shows it rather than making the user ask twice
  // after a reload. `latest_draft_id` is on the card; `drafts` is the fuller list from the detail read.
  //
  // Newest *usable* first, because that list holds every kind and every outcome the card ever produced:
  // a draft that failed, or a per-question `question_explain`, would otherwise be shown as the card's
  // Advisor state and hide a ready result the user can still act on. Only when nothing is usable does the
  // newest row win — a lone failure has to be visible as a failure, not silently replaced by "has not run".
  const known = useMemo(() => {
    const list = [...(drafts ?? [])].sort((a, b) => (a.updated_at < b.updated_at ? -1 : 1))
    for (let i = list.length - 1; i >= 0; i -= 1) {
      const candidate = list[i]
      if (candidate && usableResult(candidate)) return candidate.draft_id
    }
    return list.length > 0 ? (list[list.length - 1]?.draft_id ?? null) : card.advisor.latest_draft_id
  }, [drafts, card.advisor.latest_draft_id])

  // The card this state belongs to travels with the id. `DetailShell` mounts the template without a
  // `key` on purpose, so a draft still in flight survives a walk to another card and back; an id on its
  // own would then be shown under whichever card happened to be on screen.
  const [asked, setAsked] = useState<{ action: string; draft: string } | null>(null)
  const [pending, setPending] = useState<AdvisorKind | null>(null)
  const [error, setError] = useState<string | null>(null)
  const draftId = (asked && asked.action === card.action_id ? asked.draft : null) ?? known

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

  const request = useCallback(
    (kind: AdvisorKind, questionIndex?: number | null) => {
      setError(null)
      setPending(kind)
      void api
        .requestDraft({
          action_id: card.action_id,
          kind,
          question_index: questionIndex ?? null,
          // The Advisor answers in the language the user is reading (§2.7 takes the locale explicitly).
          locale,
        })
        .then((response) => setAsked({ action: card.action_id, draft: response.draft.draft_id }))
        .catch((thrown: unknown) => setError(decodeError(thrown).code))
        .finally(() => setPending(null))
    },
    [api, card.action_id, locale],
  )

  return { draft: resource.data?.draft ?? null, pending, error, request }
}

export interface AdvisorBlockProps {
  card: ActionCard
  advisor: AdvisorState
  /** Copy the drafted feedback into the change-request box. Offered only where that box exists. */
  onUseFeedback?: (text: string) => void
  /** Copy the drafted selections into the answer inputs. Nothing is sent. */
  onApplyAnswers?: (answers: DraftResult['suggested_answers']) => void
}

export function AdvisorBlock({ card, advisor, onUseFeedback, onApplyAnswers }: AdvisorBlockProps) {
  const { t, has } = useI18n()
  const kinds = advisorKindsFor(card.type)
  if (kinds.length === 0) return null

  const { draft, pending, error, request } = advisor
  const result = draft?.status === 'ready' ? draft.result : null
  const neutral = draft?.neutrality?.['ok'] !== false
  const usable = usableResult(draft)
  const failed = draft?.status === 'failed' || draft?.status === 'expired'

  return (
    <Block title={usable ? t('advisor.titleDraft') : t('advisor.title')} icon="advisor">
      <div className="studio-advisor">
        {/* Polite, and only around the draft: the Advisor finishing is a status change the user asked
            for, not an interruption. */}
        <div aria-live="polite">
          {usable ? (
            <>
              <div className="studio-advisor-head">
                <span className="studio-advisor-verdict">
                  {usable.verdict
                    ? has(`advisor.verdict.${usable.verdict}`)
                      ? t(`advisor.verdict.${usable.verdict}`)
                      : usable.verdict
                    : t('advisor.verdict.none')}
                </span>
                <Chip tone="aim" icon="advisor">
                  {t('advisor.draftNotDecision')}
                </Chip>
              </div>
              {usable.summary ? <p className="studio-advisor-summary">{usable.summary}</p> : null}
              <AdvisorDetails result={usable} />

              {usable.drafted_feedback && onUseFeedback ? (
                <div className="studio-advisor-apply">
                  <button
                    type="button"
                    className="studio-btn studio-btn-sm"
                    onClick={() => onUseFeedback(usable.drafted_feedback as string)}
                  >
                    <Icon name="doc" size={13} />
                    {t('advisor.useFeedback')}
                  </button>
                  <span className="studio-advisor-note">{t('advisor.useFeedbackNote')}</span>
                </div>
              ) : null}

              {usable.suggested_answers.length > 0 && onApplyAnswers ? (
                <div className="studio-advisor-apply">
                  <button
                    type="button"
                    className="studio-btn studio-btn-sm"
                    onClick={() => onApplyAnswers(usable.suggested_answers)}
                  >
                    <Icon name="check" size={13} />
                    {t('advisor.applyPicks')}
                  </button>
                  <span className="studio-advisor-note">{t('advisor.applyPicksNote')}</span>
                </div>
              ) : null}
            </>
          ) : result && !neutral ? (
            <p className="studio-advisor-copy" data-tone="warn">
              <Icon name="warn" size={13} />
              {t('advisor.evidenceMoved')}
            </p>
          ) : pending || draft?.status === 'queued' || draft?.status === 'running' ? (
            <p className="studio-advisor-copy">
              <Icon name="clock" size={13} />
              {t('advisor.running', {
                kind: t(`advisor.kind.${pending ?? draft?.kind ?? 'diagnose'}`),
              })}
            </p>
          ) : failed ? (
            <p className="studio-advisor-copy" data-tone="warn">
              <Icon name="warn" size={13} />
              {draft?.status === 'expired'
                ? t('advisor.expired')
                : has(`advisor.error.${draft?.error ?? ''}`)
                  ? t(`advisor.error.${draft?.error ?? ''}`)
                  : t('advisor.error.unknown')}
            </p>
          ) : (
            <>
              <p className="studio-advisor-title">{t('advisor.notRun')}</p>
              <p className="studio-advisor-copy">{t('advisor.notRunCopy')}</p>
            </>
          )}
        </div>

        {error ? (
          <p className="studio-advisor-copy" data-tone="warn" role="status">
            <Icon name="warn" size={13} />
            {has(`errors.${error}`) ? t(`errors.${error}`) : t('advisor.error.unknown')}
          </p>
        ) : null}

        <div className="studio-advisor-actions">
          {kinds.map((kind) => (
            <button
              key={kind}
              type="button"
              className="studio-btn studio-btn-sm"
              disabled={pending !== null}
              onClick={() => request(kind)}
            >
              <Icon name="advisor" size={13} />
              {t(`advisor.action.${kind}`)}
            </button>
          ))}
        </div>

        <p className="studio-disclaim">
          <Icon name="lock" size={13} />
          <span>{t('advisor.disclaimer')}</span>
        </p>
      </div>
    </Block>
  )
}

/**
 * The model's reasoning, verbatim: evidence, assumptions, alternatives, confidence and the claims it could
 * not settle (FR-ADV-008). Exported because the wizard's plan proposal (`PlanAdvisorPanel`) shows the same
 * rows under a different head; one component means the two surfaces cannot drift on what a draft discloses.
 */
export function AdvisorDetails({ result }: { result: DraftResult }) {
  const { t } = useI18n()
  return (
    <dl className="studio-advisor-dl">
      <AdvisorList label={t('advisor.evidence')} items={result.evidence} />
      <AdvisorList label={t('advisor.assumptions')} items={result.assumptions} />
      <AdvisorList label={t('advisor.alternatives')} items={result.alternatives} />
      <dt>{t('advisor.confidence')}</dt>
      <dd>{t(`advisor.confidenceValue.${result.confidence}`)}</dd>
      {result.needs_your_decision.length > 0 ? (
        <>
          <dt>{t('advisor.needsYourDecision')}</dt>
          <dd>
            <Chip tone="warn" icon="warn">
              {t('advisor.unresolvable')}
            </Chip>
            <ul className="studio-advisor-ul">
              {result.needs_your_decision.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          </dd>
        </>
      ) : null}
    </dl>
  )
}

function AdvisorList({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <>
      <dt>{label}</dt>
      <dd>
        <ul className="studio-advisor-ul">
          {items.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      </dd>
    </>
  )
}
