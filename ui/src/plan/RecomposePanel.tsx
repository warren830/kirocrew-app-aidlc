/**
 * Recompose a running intent: one page, only the stages AI-DLC will still let you move (FR-PLAN-008).
 *
 * The restriction is not enforced here — it is read. `POST …/recompose/preview` computes the plan against
 * the intent's own state file, so a completed stage arrives `locked: "completed"`, the running one
 * `current`, one at a Gate `at_gate` and anything at or behind the cursor `behind_cursor`. Rendering the
 * server's locks is what makes "pending stages ahead of the cursor" true rather than approximately true,
 * and it is why every stage is still listed: a matrix that showed only the movable rows would make the
 * user think the earlier stages had disappeared from their workflow.
 *
 * Applying is two-pressed and digest-bound. The confirmation shows the exact argv AI-DLC will run — not a
 * paraphrase — and sends the `proposal_digest` the preview came with, so a plan that moved under the
 * dialog is refused by the backend instead of applied to a workflow the user never saw.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { useI18n } from '../i18n'
import { StudioApiError, type StudioApi } from '../lib/api'
import { plural } from '../lib/format'
import type { EngineResult, RecomposeProposal } from '../lib/types'
import { EstimatePanel } from './EstimatePanel'
import { IssueList, PlanDiff } from './PlanDiff'
import { PlanMatrix } from './PlanMatrix'

export interface RecomposePanelProps {
  api: StudioApi
  repoId: string
  intentKey: string
  /** What to call the intent in the heading — AI-DLC's own directory name or title. */
  intentLabel: string
  onClose: () => void
  /** Called after the engine ran, so the list that owns this panel can re-read the intent. */
  onApplied?: () => void
}

interface Applied {
  ok: boolean
  result: EngineResult
}

export function RecomposePanel({ api, repoId, intentKey, intentLabel, onClose, onApplied }: RecomposePanelProps) {
  const i18n = useI18n()
  const { t } = i18n
  const [skip, setSkip] = useState<string[]>([])
  const [add, setAdd] = useState<string[]>([])
  const [proposal, setProposal] = useState<RecomposeProposal | null>(null)
  const [digest, setDigest] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<StudioApiError | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [applying, setApplying] = useState(false)
  const [applied, setApplied] = useState<Applied | null>(null)

  // Every preview carries a sequence number: two fast toggles can land out of order, and rendering the
  // older answer would show a matrix that disagrees with the selections underneath it.
  const sequence = useRef(0)

  const preview = useCallback(
    async (nextSkip: string[], nextAdd: string[]) => {
      const ticket = (sequence.current += 1)
      setBusy(true)
      try {
        const answer = await api.recomposePreview(repoId, intentKey, { skip: nextSkip, add: nextAdd })
        if (ticket !== sequence.current) return
        setProposal(answer.proposal)
        setDigest(answer.proposal_digest)
        setError(null)
      } catch (caught) {
        if (ticket !== sequence.current) return
        setError(caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0))
      } finally {
        if (ticket === sequence.current) setBusy(false)
      }
    },
    [api, repoId, intentKey],
  )

  useEffect(() => {
    void preview(skip, add)
  }, [preview, skip, add])

  const toggle = useCallback((slug: string, next: boolean) => {
    // A flip is expressed as a skip or an add, never as a local checkbox state: the checkbox the user
    // sees next comes back from the engine's own recomputation of the plan.
    setConfirming(false)
    setApplied(null)
    if (next) {
      setSkip((current) => current.filter((entry) => entry !== slug))
      setAdd((current) => (current.includes(slug) ? current : [...current, slug]))
    } else {
      setAdd((current) => current.filter((entry) => entry !== slug))
      setSkip((current) => (current.includes(slug) ? current : [...current, slug]))
    }
  }, [])

  const apply = useCallback(async () => {
    if (!proposal || !proposal.allowed || !digest) return
    setApplying(true)
    try {
      const answer = await api.recompose(repoId, intentKey, { skip: proposal.skip, add: proposal.add, proposal_digest: digest })
      setApplied({ ok: answer.result.ok, result: answer.result })
      setError(null)
      setConfirming(false)
      // The selections have been spent; clearing them re-previews, so the matrix now shows the plan that
      // is on disk rather than the one that was about to be applied.
      setSkip([])
      setAdd([])
      onApplied?.()
    } catch (caught) {
      setError(caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0))
      setConfirming(false)
      // The refusal may be about a plan that has moved, so the proposal is re-read rather than kept.
      void preview(skip, add)
    } finally {
      setApplying(false)
    }
  }, [api, repoId, intentKey, proposal, digest, onApplied, preview, skip, add])

  const changes = (proposal?.skip.length ?? 0) + (proposal?.add.length ?? 0)

  return (
    <section className="studio-panel" aria-label={t('plan.recompose.title', { intent: intentLabel })}>
      <header className="studio-panel-head">
        <h2>
          <Icon name="map" size={15} />
          {t('plan.recompose.title', { intent: intentLabel })}
        </h2>
        <div className="studio-row studio-panel-actions">
          <button type="button" className="studio-btn" onClick={() => void preview(skip, add)} disabled={busy || applying}>
            <Icon name="refresh" size={13} />
            {t('plan.recompose.reload')}
          </button>
          <button type="button" className="studio-btn" onClick={onClose}>
            {t('plan.recompose.close')}
          </button>
        </div>
      </header>

      <p className="studio-lede">{t('plan.recompose.lede')}</p>

      <div className="studio-row studio-wrap">
        {proposal?.current_stage ? (
          <Chip icon="play" tone="accent" mono>
            {t('plan.recompose.current', { stage: proposal.current_stage })}
          </Chip>
        ) : (
          <Chip icon="info">{t('plan.recompose.currentUnknown')}</Chip>
        )}
        {proposal && proposal.skip.length > 0 ? (
          <Chip icon="warn" tone="warn">{plural(i18n, 'plan.recompose.skip', proposal.skip.length)}</Chip>
        ) : null}
        {proposal && proposal.add.length > 0 ? (
          <Chip icon="plus" tone="ok">{plural(i18n, 'plan.recompose.add', proposal.add.length)}</Chip>
        ) : null}
        {busy ? <span className="studio-muted">{t('plan.busy')}</span> : null}
      </div>

      {error ? (
        <p className="studio-banner" data-tone="danger" role="alert">
          <Icon name="warn" size={15} />
          <span className="studio-grow">
            {t('plan.recompose.error')} {error.known ? t(`errors.${error.code}`) : error.message}
          </span>
        </p>
      ) : null}

      {applied ? (
        <p className="studio-banner" data-tone={applied.ok ? 'ok' : 'warn'} role="status">
          <Icon name={applied.ok ? 'check' : 'warn'} size={15} />
          <span className="studio-grow">{applied.ok ? t('plan.recompose.applied') : t('plan.recompose.appliedFailed')}</span>
        </p>
      ) : null}

      {proposal ? (
        <>
          <PlanMatrix stages={proposal.plan.stages} onToggle={toggle} busy={busy || applying} showState />

          {proposal.refusals.length > 0 ? (
            <section className="studio-block">
              <h3>
                <Icon name="warn" size={13} />
                {t('plan.recompose.refused')}
              </h3>
              <IssueList issues={proposal.refusals} tone="danger" />
            </section>
          ) : null}

          <PlanDiff plan={proposal.plan} hideEmpty />
          <EstimatePanel plan={proposal.plan} />

          {changes === 0 ? (
            <p className="studio-consequence">
              <Icon name="info" size={13} />
              <span>{t('plan.recompose.none')}</span>
            </p>
          ) : null}

          {confirming ? (
            <div className="studio-confirm" role="group" aria-label={t('plan.recompose.confirmTitle')}>
              <h3>
                <Icon name="lock" size={13} />
                {t('plan.recompose.confirmTitle')}
              </h3>
              <p>{t('plan.recompose.confirmBody')}</p>
              <p className="studio-stat-k">{t('plan.recompose.argv')}</p>
              {/* Verbatim, because a paraphrase of a command is not the command. */}
              <pre className="studio-sendtext">{proposal.argv_preview.join(' ')}</pre>
              <div className="studio-row">
                <button type="button" className="studio-btn studio-btn-primary" onClick={() => void apply()} disabled={applying}>
                  <Icon name="check" size={13} />
                  {t('plan.recompose.confirmGo')}
                </button>
                <button type="button" className="studio-btn" onClick={() => setConfirming(false)} disabled={applying}>
                  {t('common.cancel')}
                </button>
              </div>
            </div>
          ) : (
            <div className="studio-row studio-wrap">
              <button
                type="button"
                className="studio-btn studio-btn-primary"
                disabled={!proposal.allowed || busy || applying}
                onClick={() => setConfirming(true)}
              >
                <Icon name="check" size={13} />
                {t('plan.recompose.apply')}
              </button>
              {changes > 0 ? (
                <button
                  type="button"
                  className="studio-btn"
                  onClick={() => {
                    setSkip([])
                    setAdd([])
                  }}
                  disabled={busy || applying}
                >
                  {t('plan.recompose.reset')}
                </button>
              ) : null}
            </div>
          )}
        </>
      ) : busy ? (
        <p className="studio-muted">{t('common.loading')}</p>
      ) : null}
    </section>
  )
}
