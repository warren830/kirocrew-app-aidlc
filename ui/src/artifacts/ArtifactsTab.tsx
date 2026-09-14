/**
 * The Artifacts tab: what the stage wrote, and what the reviewer said about it.
 *
 * The tab composes the pieces rather than owning their behaviour, and it owns exactly the two things a
 * composition has to own:
 *
 *  1. **Where the metadata comes from.** A caller that already holds `evidence.artifacts` (every action
 *     card does) passes it in and no listing request is made — the card's list is also the *captured*
 *     evidence set for that decision, so re-reading it here could show a different set of files than the
 *     one the decision was derived from. Without a card (browsing from the Workflow Map) the tab reads
 *     `GET …/artifacts` itself.
 *  2. **Which anchor belongs to whom.** `f-<n>` names a finding, anything else names a heading inside the
 *     artifact (route contract §3.2). Handing the finding anchor to the viewer would scroll nowhere and
 *     look like a broken deep link.
 *
 * Nothing in this tab can change anything. `Request changes` is a decision the action templates own; the
 * only affordance here beyond reading is the host editor hand-off, and only when the host reports it.
 */

import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { ContentSkeleton, EmptyState } from '@kirocrew/app-sdk/ui'

import { useI18n } from '../i18n'
import { useStudioApi } from '../lib/api'
import type { ArtifactMeta, ArtifactsResponse, Capability, ReviewFinding } from '../lib/types'
import { useResource } from '../lib/useResource'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { ArtifactDiff } from './ArtifactDiff'
import { ArtifactList } from './ArtifactList'
import { ArtifactViewer, useArtifactBody } from './ArtifactViewer'
import { FindingList } from './FindingList'

/** A route anchor that names a finding rather than a section of the artifact. */
export function isFindingAnchor(anchor: string | undefined): boolean {
  return !!anchor && /^f-\d+$/.test(anchor)
}

export interface ArtifactsTabProps {
  repoId: string
  intentKey: string
  /** Metadata the caller already has (an action card's `evidence.artifacts`). Suppresses the listing read. */
  artifacts?: readonly ArtifactMeta[] | null
  /** Listing filters, used only when `artifacts` is not supplied. */
  stage?: string
  unit?: string
  kind?: string
  /** Selected artifact (route `artifact`). Uncontrolled when `onSelectArtifact` is absent. */
  artifactId?: string
  onSelectArtifact?: (artifactId: string) => void
  /** Reviewer findings for the side pane. Falls back to the ones parsed out of the artifact itself. */
  findings?: readonly ReviewFinding[] | null
  /** Route anchor (`f-<n>` for a finding, `h-<slug>` or a bare slug for a heading). */
  anchor?: string
  capabilities?: Record<string, Capability> | null
  onOpenInEditor?: (relpath: string) => void
  onOpenAnchor?: (anchor: string) => void
  /** Blocks the owning template adds to this tab (install drift table, recovery evidence grid). */
  children?: ReactNode
}

export function ArtifactsTab({
  repoId,
  intentKey,
  artifacts,
  stage,
  unit,
  kind,
  artifactId,
  onSelectArtifact,
  findings,
  anchor,
  capabilities,
  onOpenInEditor,
  onOpenAnchor,
  children,
}: ArtifactsTabProps) {
  const { t, fmt } = useI18n()
  const api = useStudioApi()
  const [localSelection, setLocalSelection] = useState('')

  const supplied = artifacts != null
  const listing = useResource<ArtifactsResponse>(
    supplied ? null : `artifacts:${repoId}:${intentKey}:${stage ?? ''}:${unit ?? ''}:${kind ?? ''}`,
    useCallback(
      (signal: AbortSignal) => api.artifacts(repoId, intentKey, { stage, unit, kind }, { signal }),
      [api, repoId, intentKey, stage, unit, kind],
    ),
    { interval: 0, revalidateOn: ['intent.updated', 'reset'] },
  )

  const files: readonly ArtifactMeta[] = supplied ? artifacts : (listing.data?.artifacts ?? [])
  const truncated = supplied ? false : (listing.data?.truncated ?? false)

  const selectedId = onSelectArtifact ? (artifactId ?? '') : localSelection || (artifactId ?? '')
  const asked = files.find((meta) => meta.artifact_id === selectedId) ?? null
  // Falling back to the first file keeps the pane useful for the common case (one produced artifact) and
  // for a stale deep link; the notice below says when the requested file was not the one shown.
  const selected = asked ?? files[0] ?? null
  const missing = selectedId !== '' && asked === null && files.length > 0

  const select = (id: string) => {
    if (onSelectArtifact) onSelectArtifact(id)
    else setLocalSelection(id)
  }

  const body = useArtifactBody(repoId, intentKey, selected)
  const findingAnchor = isFindingAnchor(anchor) ? anchor : undefined
  const headingAnchor = isFindingAnchor(anchor) ? undefined : anchor
  const reviewFindings: readonly ReviewFinding[] = findings ?? body.data?.review?.findings ?? []
  const openBlockers = reviewFindings.filter((finding) => finding.level === 'blocker').length

  const listingError = listing.error
  const emptyIcon = useMemo(() => <Icon name="doc" size={18} />, [])

  if (!supplied && listing.loading) return <ContentSkeleton rows={5} />

  if (listingError) {
    return (
      <p className="studio-artifact-error" role="status">
        <Icon name="warn" size={13} />{' '}
        {listingError.known ? t(`errors.${listingError.code}`) : listingError.message}
      </p>
    )
  }

  if (files.length === 0) {
    return (
      <EmptyState
        icon={emptyIcon}
        title={t('artifact.empty.title')}
        subtitle={t('artifact.empty.body')}
      />
    )
  }

  return (
    <div className="studio-artifacts">
      {files.length > 1 ? (
        <section className="studio-block">
          <h3>
            <Icon name="doc" size={13} />
            {t('artifact.block.files')}
          </h3>
          <ArtifactList
            artifacts={files}
            selectedId={selected?.artifact_id}
            onSelect={select}
            truncated={truncated}
          />
        </section>
      ) : null}

      {missing ? (
        <p className="studio-muted studio-artlist-note" role="status">
          <Icon name="warn" size={13} /> {t('artifact.list.missing')}
        </p>
      ) : null}

      {selected ? (
        <section className="studio-block">
          <h3>
            <Icon name="doc" size={13} />
            {t('artifact.block.produced')}
          </h3>
          <div className="studio-compare" data-panes={reviewFindings.length > 0 ? 'two' : 'one'}>
            <ArtifactViewer
              meta={selected}
              body={body.data}
              loading={body.loading}
              error={body.error}
              anchor={headingAnchor}
              capabilities={capabilities}
              {...(onOpenInEditor ? { onOpenInEditor } : {})}
            >
              {body.data ? <ArtifactDiff prior={body.data.prior} name={selected.name} /> : null}
            </ArtifactViewer>

            {reviewFindings.length > 0 ? (
              <aside
                className="studio-pane studio-reviewpane"
                aria-label={t('review.pane.label', { name: selected.name })}
              >
                <header className="studio-pane-head">
                  <Icon name="review" size={13} />
                  <span className="studio-pane-nm studio-grow">{t('review.pane.title')}</span>
                  {openBlockers > 0 ? (
                    <Chip tone="danger" icon="warn">
                      {t('review.openBlockers', { n: fmt.number(openBlockers) })}
                    </Chip>
                  ) : null}
                </header>
                <div className="studio-pane-body">
                  <FindingList
                    findings={reviewFindings}
                    {...(onOpenAnchor ? { onOpenAnchor } : {})}
                    {...(findingAnchor ? { anchor: findingAnchor } : {})}
                  />
                </div>
                <footer className="studio-pane-foot">
                  <span className="studio-muted">{t('review.quotedVerbatim')}</span>
                </footer>
              </aside>
            ) : null}
          </div>
        </section>
      ) : null}

      {children}
    </div>
  )
}
