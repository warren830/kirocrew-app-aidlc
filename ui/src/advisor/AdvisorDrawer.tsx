/**
 * The Advisor drawer: one draft, read-only, opened from `?draft=<draft_id>` (§3.2).
 *
 * The invariant this component is built around is FR-ADV-007: **an Advisor round-trip must leave AI-DLC
 * byte-for-byte unchanged, and the Advisor never auto-submits or preselects Approve.** So the drawer ships
 * no submit control of any kind. The two actions it can offer — put the drafted feedback in the box, apply
 * the drafted selections — are handed in as callbacks by whoever owns the decision form, and both are
 * labelled with what they do *not* do. If the caller passes no callbacks the drawer is purely a reader,
 * which is also what makes it safe to open from Activity or from a deep link with no form on screen.
 *
 * Polling: a draft arrives `queued`, becomes `running`, then `ready`/`failed`/`expired`. `useResource`
 * polls at its fast cadence only while the draft is one of the first two and stops entirely afterwards, so
 * a drawer left open on a finished draft costs nothing.
 *
 * i18n note: these strings live in `i18n/parts/activity.*.json` under `advisor.drawer.*`. The `advisor.*`
 * prefix in contracts §3.3 belongs to the Advisor *block* inside the decision pane, which another area
 * owns; a distinct sub-prefix keeps the two catalogues from colliding at merge time.
 */

import { useCallback, useEffect, useRef } from 'react'
import { Btn } from '@kirocrew/app-sdk/ui'

import { useI18n } from '../i18n'
import { useStudioApi } from '../lib/api'
import { at as formatAt } from '../lib/format'
import type { AdvisorDraftGetResponse, DraftResult } from '../lib/types'
import { useResource } from '../lib/useResource'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'

/** Poll cadence while the subagent is still working. */
const RUNNING_POLL_MS = 2_000

export interface AdvisorDrawerProps {
  draftId: string
  onClose: () => void
  /**
   * Fill the Request-changes box with the drafted text. Omit when no form is on screen; the button then
   * does not exist rather than existing and doing nothing.
   */
  onUseFeedback?: (text: string) => void
  /** Fill the answer controls with the drafted selections. Nothing is submitted by either callback. */
  onApplyAnswers?: (answers: DraftResult['suggested_answers']) => void
}

function ItemList({ items }: { items: string[] }) {
  return (
    <ul className="studio-list">
      {items.map((item, index) => (
        // Advisor prose has no id of its own; the index is the only stable key and the list never reorders.
        <li key={`${index}-${item.slice(0, 24)}`}>{item}</li>
      ))}
    </ul>
  )
}

export function AdvisorDrawer({ draftId, onClose, onUseFeedback, onApplyAnswers }: AdvisorDrawerProps) {
  const i18n = useI18n()
  const { t } = i18n
  const api = useStudioApi()
  const closeRef = useRef<HTMLButtonElement | null>(null)

  const draftResource = useResource<AdvisorDraftGetResponse>(
    `advisor-draft:${draftId}`,
    useCallback((signal) => api.draft(draftId, { signal }), [api, draftId]),
    {
      busy: (data) => data.draft.status === 'queued' || data.draft.status === 'running',
      fastInterval: RUNNING_POLL_MS,
      // 0 disables polling once the draft is terminal: a ready draft never changes again.
      slowInterval: 0,
      revalidateOn: ['advisor.updated', 'reset'],
    },
  )

  useEffect(() => {
    closeRef.current?.focus()
  }, [draftId])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const draft = draftResource.data?.draft ?? null
  const result = draft?.result ?? null

  return (
    <aside className="studio-evdrawer studio-advisor-drawer" aria-label={t('advisor.drawer.title')}>
      <header className="studio-evdrawer-head">
        <h2>
          <Icon name="advisor" size={15} /> {t('advisor.drawer.title')}
        </h2>
        <button
          type="button"
          ref={closeRef}
          className="studio-icon-btn"
          onClick={onClose}
          aria-label={t('advisor.drawer.close')}
        >
          <Icon name="close" size={15} />
        </button>
      </header>

      <div className="studio-evdrawer-body">
        {/* Polite: a draft becoming ready is progress the user asked for. */}
        <div role="status" aria-live="polite" className="studio-row studio-wrapchips">
          <Chip tone="aim" icon="advisor">
            {t('advisor.drawer.badge')}
          </Chip>
          {draft ? <Chip>{t(`advisor.drawer.kind.${draft.kind}`)}</Chip> : null}
          {draft && draft.status !== 'ready' ? (
            <span className="studio-muted">
              {draft.status === 'failed'
                ? t('advisor.drawer.status.failed', { message: draft.error ?? t('common.unavailable') })
                : t(`advisor.drawer.status.${draft.status}`)}
            </span>
          ) : null}
        </div>

        {draftResource.error ? (
          <p className="studio-error">
            {t('advisor.drawer.error', {
              message: i18n.has(`errors.${draftResource.error.code}`)
                ? t(`errors.${draftResource.error.code}`)
                : draftResource.error.message,
            })}
          </p>
        ) : null}

        {draftResource.loading ? <p className="studio-muted">{t('common.loading')}</p> : null}

        {draft && !result && draft.status === 'ready' ? (
          <p className="studio-consequence">{t('advisor.drawer.empty')}</p>
        ) : null}

        {result ? (
          <>
            {/* One of `ADVISOR_VERDICTS`, so it is localized — the same sentence the card shows.
                A verdict the catalog does not know is rendered verbatim rather than dropped: a new
                engine-side token must read as a machine word, never as "no verdict". */}
            {result.verdict ? (
              <p className="studio-advisor-verdict">
                {i18n.has(`advisor.verdict.${result.verdict}`)
                  ? t(`advisor.verdict.${result.verdict}`)
                  : result.verdict}
              </p>
            ) : null}
            {result.summary ? (
              <>
                <h3 className="studio-subhead">{t('advisor.drawer.summary')}</h3>
                <p className="studio-advisor-summary">{result.summary}</p>
              </>
            ) : null}

            <dl className="studio-evgrid">
              {result.evidence.length > 0 ? (
                <div className="studio-evgrid-pair">
                  <dt>{t('advisor.drawer.evidence')}</dt>
                  <dd>
                    <ItemList items={result.evidence} />
                  </dd>
                </div>
              ) : null}
              {result.assumptions.length > 0 ? (
                <div className="studio-evgrid-pair">
                  <dt>{t('advisor.drawer.assumptions')}</dt>
                  <dd>
                    <ItemList items={result.assumptions} />
                  </dd>
                </div>
              ) : null}
              {result.alternatives.length > 0 ? (
                <div className="studio-evgrid-pair">
                  <dt>{t('advisor.drawer.alternatives')}</dt>
                  <dd>
                    <ItemList items={result.alternatives} />
                  </dd>
                </div>
              ) : null}
              <div className="studio-evgrid-pair">
                <dt>{t('advisor.drawer.confidence')}</dt>
                <dd>{t(`advisor.drawer.confidence.${result.confidence}`)}</dd>
              </div>
              {result.needs_your_decision.length > 0 ? (
                <div className="studio-evgrid-pair">
                  <dt>{t('advisor.drawer.needsYourDecision')}</dt>
                  <dd>
                    <Chip tone="warn" icon="warn">
                      {t('advisor.drawer.needsYourDecisionChip')}
                    </Chip>
                    <ItemList items={result.needs_your_decision} />
                  </dd>
                </div>
              ) : null}
            </dl>

            {result.suggested_answers.length > 0 ? (
              <section className="studio-block">
                <h3>{t('advisor.drawer.suggested')}</h3>
                <dl className="studio-evgrid">
                  {result.suggested_answers.map((answer) => (
                    <div key={answer.question_index} className="studio-evgrid-pair">
                      <dt className="studio-mono">
                        {t('advisor.drawer.suggestedQuestion', { index: answer.question_index })}
                      </dt>
                      {/* The answer text is the option label from the questions file: verbatim. */}
                      <dd className="studio-wrap-any">{answer.answer}</dd>
                    </div>
                  ))}
                </dl>
                {onApplyAnswers ? (
                  <div className="studio-col">
                    <Btn type="button" onClick={() => onApplyAnswers(result.suggested_answers)}>
                      <Icon name="check" size={13} />
                      {t('advisor.drawer.applyAnswers')}
                    </Btn>
                    <span className="studio-muted">{t('advisor.drawer.applyAnswersHint')}</span>
                  </div>
                ) : null}
              </section>
            ) : null}

            {result.drafted_feedback ? (
              <section className="studio-block">
                <h3>{t('advisor.drawer.useFeedback')}</h3>
                <pre className="studio-pre studio-wrap-any">{result.drafted_feedback}</pre>
                {onUseFeedback ? (
                  <div className="studio-col">
                    <Btn type="button" onClick={() => onUseFeedback(result.drafted_feedback ?? '')}>
                      <Icon name="doc" size={13} />
                      {t('advisor.drawer.useFeedback')}
                    </Btn>
                    <span className="studio-muted">{t('advisor.drawer.useFeedbackHint')}</span>
                  </div>
                ) : null}
              </section>
            ) : null}
          </>
        ) : null}

        {draft ? (
          <p className="studio-muted studio-small">
            {t('advisor.drawer.expires', { when: formatAt(i18n, draft.expires_at) })}
          </p>
        ) : null}

        <p className="studio-consequence">
          <Icon name="lock" size={13} /> {t('advisor.drawer.disclaimer')}
        </p>
      </div>
    </aside>
  )
}

export default AdvisorDrawer
