/**
 * The Review tab: the reviewer's findings, the contract that produced them, and the audit rows that
 * prove when it ran.
 *
 * Two sources are deliberately kept apart:
 *
 *  - **The card's own review block** (`evidence.review`) is what the decision in front of the user was
 *    derived from. When the caller passes it, those are the findings shown — re-reading the live file
 *    could put newer findings under an older decision, which is the one way this tab could mislead.
 *  - **`GET …/review`** supplies what a card never carries: the audit receipts (AI-DLC's own `REVIEW_*`
 *    rows) and the revision count. Those are facts about the process, not about the verdict, so mixing
 *    them with captured findings is safe.
 *
 * Everything the reviewer wrote is rendered verbatim, and every Studio-authored label says which of the
 * two sources it describes.
 */

import { useCallback, useMemo } from 'react'
import { ContentSkeleton, EmptyState } from '@kirocrew/app-sdk/ui'

import { useI18n } from '../i18n'
import { useStudioApi } from '../lib/api'
import type { ActionEvidence, ReviewResponse } from '../lib/types'
import { useResource } from '../lib/useResource'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { FindingList } from './FindingList'

export interface ReviewTabProps {
  repoId: string
  intentKey: string
  /** The action card's captured review block, when there is a card. */
  review?: ActionEvidence['review'] | null
  /** Route anchor: `f-<n>` highlights that finding. */
  anchor?: string
  /** Open the artifact at a finding's anchor — the caller switches the tab (visual spec §5.14). */
  onOpenAnchor?: (anchor: string) => void
}

export function ReviewTab({ repoId, intentKey, review, anchor, onOpenAnchor }: ReviewTabProps) {
  const { t, fmt } = useI18n()
  const api = useStudioApi()

  const resource = useResource<ReviewResponse>(
    `review:${repoId}:${intentKey}`,
    useCallback((signal: AbortSignal) => api.review(repoId, intentKey, { signal }), [api, repoId, intentKey]),
    { interval: 0, revalidateOn: ['intent.updated', 'reset'] },
  )
  const live = resource.data

  const captured = review ?? null
  const findings = captured ? captured.findings : (live?.findings ?? [])
  const verdict = captured ? captured.verdict : (live?.verdict ?? null)
  const reviewer = captured?.reviewer ?? live?.reviewer ?? null
  const reviewClass = captured?.review_class ?? live?.review_class ?? null
  const revisions = live?.revisions ?? null
  const receipts = live?.receipts ?? []
  const stage = live?.stage ?? null
  const emptyIcon = useMemo(() => <Icon name="review" size={18} />, [])

  if (resource.loading && !captured) return <ContentSkeleton rows={5} />

  const nothing = findings.length === 0 && receipts.length === 0 && verdict === null

  if (nothing) {
    return (
      <EmptyState icon={emptyIcon} title={t('review.empty.title')} subtitle={t('review.empty.body')} />
    )
  }

  return (
    <div className="studio-review">
      {resource.error ? (
        <p className="studio-artifact-error" role="status">
          <Icon name="warn" size={13} />{' '}
          {resource.error.known ? t(`errors.${resource.error.code}`) : resource.error.message}
        </p>
      ) : null}

      {verdict ? (
        <p className="studio-review-verdict">
          <span className="studio-muted">{t('review.verdict')}</span>{' '}
          {/* AI-DLC's own token (APPROVED / CHANGES_REQUESTED / ...), never translated. */}
          <Chip icon="review" mono>
            {verdict}
          </Chip>
          {stage ? <Chip icon="map" mono title={t('review.stage')}>{stage}</Chip> : null}
        </p>
      ) : null}

      {findings.length > 0 ? (
        <section className="studio-block">
          <h3>
            <Icon name="review" size={13} />
            {t('review.block.findings')}
          </h3>
          <FindingList
            findings={findings}
            grouped
            sourceNote={t('review.quotedVerbatim')}
            {...(onOpenAnchor ? { onOpenAnchor } : {})}
            {...(anchor ? { anchor } : {})}
          />
        </section>
      ) : null}

      <section className="studio-block">
        <h3>
          <Icon name="lock" size={13} />
          {t('review.block.contract')}
        </h3>
        <div className="studio-evgrid">
          <Ev
            label={t('review.contract.class')}
            value={reviewClass ?? t('review.contract.none')}
            sub={t('review.contract.classSub')}
          />
          <Ev
            label={t('review.contract.reviewer')}
            value={reviewer ?? t('review.contract.none')}
            sub={t('review.contract.reviewerSub')}
          />
          <Ev
            label={t('review.contract.revisions')}
            value={revisions === null ? t('review.contract.none') : fmt.number(revisions)}
            sub={t('review.contract.revisionsSub')}
          />
        </div>
      </section>

      {receipts.length > 0 ? (
        <section className="studio-block">
          <h3>
            <Icon name="activity" size={13} />
            {t('review.block.receipts')}
          </h3>
          <table className="studio-tbl">
            <thead>
              <tr>
                <th scope="col">{t('review.receipt.event')}</th>
                <th scope="col">{t('review.receipt.at')}</th>
                <th scope="col">{t('review.receipt.iteration')}</th>
                <th scope="col">{t('review.receipt.verdict')}</th>
              </tr>
            </thead>
            <tbody>
              {receipts.map((receipt, index) => (
                <tr key={`${receipt.event}-${receipt.ts}-${index}`}>
                  {/* Audit event names and verdicts are AI-DLC's words: verbatim, monospaced. */}
                  <td className="studio-mono">{receipt.event}</td>
                  <td className="studio-mono">{fmt.dateTime(receipt.ts)}</td>
                  <td className="studio-mono">{receipt.iteration === null ? '' : fmt.number(receipt.iteration)}</td>
                  <td className="studio-mono">{receipt.verdict ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="studio-consequence">
            <Icon name="info" size={13} />
            {t('review.receipt.note')}
          </p>
        </section>
      ) : null}
    </div>
  )
}

function Ev({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="studio-ev">
      <span className="studio-ev-src">{label}</span>
      <span className="studio-ev-val studio-mono">{value}</span>
      <span className="studio-ev-sub">{sub}</span>
    </div>
  )
}
