/**
 * The gate: an approval beside the artifact it approves and the review that judged it.
 *
 * Section order is PRD §9.5 and visual spec §5.4 — brief and consequence, acceptance criteria, the
 * artifact and the reviewer side by side, unresolved risks and prior revisions, the Advisor, then the
 * change-request box. The order is the requirement: FR-GATE-004 says the artifact and the findings must
 * be reachable *before* the confirmation control, and the sticky bar is after all of this in the DOM.
 *
 * Three things this template deliberately does not do:
 *
 *  - **It never offers `Accept as-is` on its own.** Every control comes from `card.decisions`, which the
 *    broker only grows that entry in from the fourth revision (`ACCEPT_AS_IS_MIN_ATTEMPT`). A gate where
 *    Studio invented the escape hatch would let a reviewer bypass a blocker on attempt one.
 *  - **It never edits the artifact.** `Request changes` sends the reviewer's words to the canonical
 *    session (FR-ART-006); the artifact pane is the artifacts area's read-only viewer.
 *  - **It does not re-read the artifact itself.** `useArtifactBody` dedupes by key, so the Decision tab
 *    and the Artifacts tab showing the same file is one HTTP request, not two.
 */

import { useI18n } from '../i18n'
import type { ArtifactMeta, ReviewFinding } from '../lib/types'
import { ArtifactDiff } from '../artifacts/ArtifactDiff'
import { ArtifactViewer, useArtifactBody } from '../artifacts/ArtifactViewer'
import { FindingList } from '../artifacts/FindingList'
import { bytes, at as formatAt } from '../lib/format'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { AdvisorBlock, useAdvisor } from './AdvisorBlock'
import {
  AcceptanceCriteria,
  Block,
  ConsistencyFindings,
  DecisionBrief,
  Ev,
  EvidenceGrid,
  FeedbackBox,
  Pane,
  templateLabel,
  type TemplateProps,
} from './DecisionControls'

/**
 * Turn an AI-DLC finding anchor into the route's heading anchor.
 *
 * `parseRoute` accepts `h-<slug>` and drops anything else (§3.2), so a raw anchor like `#Decline path`
 * would silently vanish from the URL and the deep link would land at the top of the file. Slugifying
 * keeps the link honest — it is a heading reference either way — and `ArtifactViewer.revealAnchor`
 * resolves both forms.
 */
export function headingAnchor(raw: string): string {
  if (/^h-[a-z0-9-]+$/.test(raw)) return raw
  const slug = raw
    .replace(/^#+/, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 118)
  return slug ? `h-${slug}` : ''
}

export function GateTemplate({ card, detail, draft, setDraft, refreshing, api, route, go }: TemplateProps) {
  const i18n = useI18n()
  const { t } = i18n
  const advisor = useAdvisor(card, api, detail?.drafts)
  const review = card.evidence.review
  const artifacts = card.evidence.artifacts
  const findings: readonly ReviewFinding[] = review?.findings ?? []
  const openBlockers = findings.filter((finding) => finding.level === 'blocker').length
  const offersChanges = card.decisions.some(
    (spec) => spec.decision === 'request_changes' || spec.decision === 'request_plan_changes',
  )

  // The produced artifact of this stage: the route's selection when it names one of them, else the first.
  const selected: ArtifactMeta | null =
    artifacts.find((meta) => meta.artifact_id === route.artifact) ?? artifacts[0] ?? null
  const body = useArtifactBody(card.repo.repo_id, card.intent.intent_key, selected)

  const openAnchor = (anchor: string) => {
    const target = headingAnchor(anchor)
    go({ tab: 'artifacts', ...(selected ? { artifact: selected.artifact_id } : {}), ...(target ? { anchor: target } : {}) })
  }

  return (
    <section className="studio-template" data-type={card.type} aria-label={templateLabel(i18n, card)}>
      <DecisionBrief card={card} />
      <AcceptanceCriteria card={card} />

      <Block title={t('template.gate.compare')} icon="review">
        <div className="studio-compare" data-panes={selected && findings.length > 0 ? 'two' : 'one'}>
          {selected ? (
            <ArtifactViewer
              meta={selected}
              body={body.data}
              loading={body.loading}
              error={body.error}
              anchor={route.anchor || undefined}
              headerExtra={
                card.evidence.state && card.evidence.state.revision_count > 0 ? (
                  <Chip mono>{t('review.iteration', { n: card.evidence.state.revision_count })}</Chip>
                ) : null
              }
            >
              {body.data?.prior ? <ArtifactDiff prior={body.data.prior} name={selected.name} /> : null}
            </ArtifactViewer>
          ) : (
            // A gate with no produced artifact is itself evidence, not an empty panel.
            <Pane title={t('template.gate.noArtifact')} icon="doc">
              <p className="studio-muted">{t('template.gate.noArtifactBody')}</p>
            </Pane>
          )}

          <Pane
            title={t('template.gate.reviewer')}
            icon="review"
            badge={
              review ? (
                openBlockers > 0 ? (
                  <Chip tone="danger" icon="warn">
                    {t('review.openBlockers', { n: openBlockers })}
                  </Chip>
                ) : (
                  <Chip tone="ok" icon="check">
                    {t('template.gate.noBlockers')}
                  </Chip>
                )
              ) : null
            }
            footer={<span>{t('review.quotedVerbatim')}</span>}
          >
            {review ? (
              <>
                <div className="studio-row studio-qmetas">
                  {/* The verdict, the reviewer name and the review class are AI-DLC's own words. */}
                  {review.verdict ? <Chip mono>{review.verdict}</Chip> : null}
                  {review.reviewer ? <Chip icon="review">{review.reviewer}</Chip> : null}
                  {review.review_class ? <Chip tone="info">{review.review_class}</Chip> : null}
                  {review.iteration === null ? null : <Chip>{t('review.iteration', { n: review.iteration })}</Chip>}
                </div>
                {findings.length > 0 ? (
                  <FindingList
                    findings={findings}
                    anchor={route.anchor || undefined}
                    {...(selected ? { onOpenAnchor: openAnchor } : {})}
                  />
                ) : (
                  <p className="studio-muted">{t('template.gate.noFindings')}</p>
                )}
              </>
            ) : (
              <p className="studio-muted">{t('template.gate.noReview')}</p>
            )}
          </Pane>
        </div>
      </Block>

      <ConsistencyFindings card={card} title={t('template.gate.risks')} />

      {card.evidence.state || review ? (
        <Block title={t('template.gate.history')} icon="clock">
          <EvidenceGrid>
            <Ev
              src={t('template.gate.revisions')}
              icon="doc"
              value={String(card.evidence.state?.revision_count ?? 0)}
              sub={t('template.gate.revisionsSub')}
            />
            {review && review.iteration !== null ? (
              <Ev
                src={t('template.gate.reviewPasses')}
                icon="review"
                value={String(review.iteration)}
                sub={t('template.gate.reviewPassesSub')}
              />
            ) : null}
            {selected ? (
              <Ev
                src={t('artifact.meta.updated')}
                icon="clock"
                value={formatAt(i18n, selected.mtime)}
                sub={bytes(i18n, selected.size)}
              />
            ) : null}
          </EvidenceGrid>
        </Block>
      ) : null}

      <AdvisorBlock
        card={card}
        advisor={advisor}
        {...(offersChanges ? { onUseFeedback: (text: string) => setDraft({ feedback: text }) } : {})}
      />

      {offersChanges ? (
        <Block title={t('template.gate.feedback')} icon="doc">
          <FeedbackBox
            draft={draft}
            setDraft={setDraft}
            label={t('template.gate.feedbackLabel')}
            placeholder={t('template.gate.feedbackPlaceholder')}
            routing={t('template.gate.feedbackRouting')}
            disabled={refreshing}
          />
          {refreshing ? (
            <p className="studio-advisor-copy" data-tone="warn" role="status">
              <Icon name="warn" size={13} />
              {t('template.common.refreshing')}
            </p>
          ) : null}
        </Block>
      ) : null}
    </section>
  )
}

export default GateTemplate
