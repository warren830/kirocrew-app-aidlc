/**
 * A map file is a read of the selected stage/unit, opened only when no action is selected.
 * Verify the id against that scope before mounting the artifact reader: ArtifactsTab's action-card
 * fallback to the first file must never turn a map link into a different artifact.
 */
import { useCallback } from 'react'
import { ContentSkeleton } from '@kirocrew/app-sdk/ui'

import { ArtifactList } from '../artifacts/ArtifactList'
import { ArtifactsTab } from '../artifacts/ArtifactsTab'
import { useI18n } from '../i18n'
import type { StudioApi } from '../lib/api'
import type { Navigate, StudioRoute } from '../lib/route'
import type { ArtifactsResponse } from '../lib/types'
import { useResource } from '../lib/useResource'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'

export function ArtifactView({ api, route, go }: { api: StudioApi; route: StudioRoute; go: Navigate }) {
  const { t } = useI18n()
  const { repo, intent, stage, unit, artifact } = route
  const listing = useResource<ArtifactsResponse>(
    `map-artifacts:${repo}:${intent}:${stage}:${unit}`,
    useCallback(
      (signal) => api.artifacts(repo, intent, { stage, unit: unit || undefined }, { signal }),
      [api, repo, intent, stage, unit],
    ),
    { interval: 0, revalidateOn: ['intent.updated', 'reset'] },
  )
  // Omitting ?unit= returns ALL units. A stage-level map selection means only the stage's own files.
  const files = listing.data?.artifacts.filter((meta) => meta.stage === stage && (meta.unit ?? '') === unit) ?? []
  const selected = files.find((meta) => meta.artifact_id === artifact)
  const select = (id: string) => go({ action: '', artifact: id, anchor: '' })

  return (
    <div className="studio-scroll">
      <section className="studio-page" aria-label={t('artifact.block.files')}>
        <div className="studio-spread">
          <h1><Icon name="doc" size={18} /> {t('artifact.block.files')}</h1>
          <button
            type="button"
            className="studio-btn"
            onClick={() => go({ view: 'map', action: '', artifact: '', anchor: '' })}
          >
            <Icon name="back" size={14} /> {t('nav.map')}
          </button>
        </div>
        <p className="studio-mono studio-wrap-any">{[repo, intent, stage, unit].filter(Boolean).join(' / ')}</p>
        <p className="studio-muted">
          <Chip icon="lock">{t('artifact.readOnly')}</Chip> {t('map.inspector.artifactsNote')}
        </p>

        {listing.error ? (
          <div className="studio-banner" data-tone="danger" role="status">
            <span className="studio-grow">
              {listing.error.known ? t(`errors.${listing.error.code}`) : listing.error.message}
            </span>
            <button type="button" className="studio-btn" onClick={() => void listing.refresh()}>
              {t('common.retry')}
            </button>
          </div>
        ) : !listing.data ? <ContentSkeleton rows={5} /> : (
          <>
            <ArtifactList
              artifacts={files}
              selectedId={selected?.artifact_id}
              onSelect={select}
              truncated={listing.data.truncated}
            />
            {selected ? (
              <ArtifactsTab
                // A file switch must not paint the previous body while its read hook resets.
                key={selected.artifact_id}
                repoId={repo}
                intentKey={intent}
                artifacts={[selected]}
                artifactId={selected.artifact_id}
                anchor={route.anchor}
                onOpenAnchor={(anchor) => go({ action: '', anchor })}
              />
            ) : !listing.data.truncated ? (
              <p className="studio-artifact-error" role="status">
                <Icon name="warn" size={13} /> {t('errors.artifact_not_found')}
              </p>
            ) : null}
          </>
        )}
      </section>
    </div>
  )
}
