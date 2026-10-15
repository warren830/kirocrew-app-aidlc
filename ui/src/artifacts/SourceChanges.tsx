/**
 * The code a stage wrote, compared with HEAD (`GET …/changes`, read from the stage's `source-manifest.json`, or,
 * for zero-Unit code generation, which AI-DLC 2.11 gives no manifest, from its `traceability.json` targets).
 *
 * A code-generation gate asks the human to approve code, while the artifact beside it is the plan its
 * reviewer judged. So the gate also lists every file the stage says it wrote, with its status, its line
 * counts and the diff itself. The comparison is the repository's own HEAD: a file that was never committed
 * reads as new in full, even if this stage only added a line to it, and the copy says so.
 *
 * A file that is not shown says why (secret, binary, too large, outside the repository); none of those
 * may read as "unchanged", which is a claim about the file.
 *
 * A closed card compares with the commit HEAD was when it was decided (the captured HEAD when the card has one):
 * once the approved code is committed, today's HEAD already holds it and every file would read "Same as the last
 * commit". The files are still read now, and the copy says so.
 */

import { useCallback, useState } from 'react'

import { useI18n } from '../i18n'
import { isClosedAction } from '../lib/actionQueue'
import { useStudioApi } from '../lib/api'
import type { ActionCard, SourceChange, SourceChangesResponse } from '../lib/types'
import { useResource, type Resource } from '../lib/useResource'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { DiffLines } from './ArtifactDiff'

/** The record file a code stage lists its writes in. */
export const SOURCE_MANIFEST = 'source-manifest.json'
/** What zero-Unit code generation names its files in instead: 2.11 writes a manifest only for a Unit. */
export const TRACEABILITY = 'traceability.json'
const CODE_STAGE = 'code-generation'

/** Does this card's stage record name the files it wrote, so that its code change can be shown? */
export function hasWrittenSource(card: ActionCard): boolean {
  const names = card.evidence.artifacts.map((meta) => meta.name)
  if (names.includes(SOURCE_MANIFEST)) return true
  return card.stage?.slug === CODE_STAGE && !card.stage.unit && names.includes(TRACEABILITY)
}

export function useSourceChanges(card: ActionCard, enabled: boolean): Resource<SourceChangesResponse> {
  const api = useStudioApi()
  const { repo_id: repoId } = card.repo
  const { intent_key: intentKey } = card.intent
  const stage = card.stage?.slug ?? ''
  const unit = card.stage?.unit ?? ''
  const closed = isClosedAction(card)
  const base = closed ? card.evidence.git?.head ?? '' : ''
  // When the decision was made: sent, else closed, else opened. HEAD then is what the decision was about.
  const at = closed && !base ? card.delivery.delivering_at ?? card.resolution.resolved_at ?? card.created_at : ''
  const fetcher = useCallback(
    (signal: AbortSignal) =>
      api.changes(repoId, intentKey, { stage, ...(unit ? { unit } : {}), ...(base ? { base } : {}), ...(at ? { at } : {}) },
        { signal }),
    [api, repoId, intentKey, stage, unit, base, at],
  )
  return useResource<SourceChangesResponse>(`changes:${repoId}:${intentKey}:${stage}:${unit}:${base}:${at}`, fetcher, {
    enabled: enabled && stage !== '',
    interval: 0,
    revalidateOn: ['intent.updated', 'reset'],
  })
}

const SHOWN = new Set(['added', 'modified', 'deleted'])

function FileChange({ change, base, eachUntil }: { change: SourceChange; base: string | null; eachUntil: boolean }) {
  const { t, fmt } = useI18n()
  const short = base ? base.slice(0, 7) : ''
  const [open, setOpen] = useState(change.status === 'modified' || change.status === 'deleted')
  const shown = SHOWN.has(change.status) && change.diff !== null
  const tone = change.status === 'added' ? 'ok' : change.status === 'deleted' ? 'danger'
    : change.status === 'modified' ? 'info' : undefined

  return (
    <li className="studio-srcchange" data-status={change.status}>
      <div className="studio-srcchange-head">
        <code className="studio-srcchange-path">{change.path}</code>
        <Chip {...(tone ? { tone } : {})}>
          {base && change.status === 'unchanged' ? t('changes.status.unchangedBase', { sha: short }) : t(`changes.status.${change.status}`)}
        </Chip>
        {shown ? (
          <span className="studio-mono studio-muted" aria-label={t('changes.counts', {
            added: fmt.number(change.additions), removed: fmt.number(change.deletions) })}>
            +{fmt.number(change.additions)} −{fmt.number(change.deletions)}
          </span>
        ) : null}
        {eachUntil ? (
          <span className="studio-mono studio-muted">
            {change.until ? t('changes.untilFile', { sha: change.until.slice(0, 7) }) : t('changes.untilNone')}
          </span>
        ) : null}
        {shown ? (
          <button type="button" className="studio-btn studio-btn-sm" aria-expanded={open}
            onClick={() => setOpen(!open)}>
            {open ? t('changes.hideDiff') : t('changes.showDiff')}
          </button>
        ) : null}
      </div>
      {change.status === 'added' ? (
        <p className="studio-muted studio-diff-note">{base ? t('changes.addedNoteBase', { sha: short }) : t('changes.addedNote')}</p>
      ) : null}
      {!SHOWN.has(change.status) ? (
        <p className="studio-muted studio-diff-note">
          <Icon name={change.status === 'unchanged' ? 'check' : 'info'} size={13} />
          {base && change.status === 'unchanged'
            ? t('changes.why.unchangedBase', { sha: short })
            : t(`changes.why.${change.status}`, { change: change.change ? t(`changes.status.${change.change}`) : '' })}
        </p>
      ) : null}
      {shown && open ? <DiffLines diff={change.diff ?? ''} /> : null}
      {change.truncated ? (
        <p className="studio-muted studio-diff-note"><Icon name="warn" size={13} /> {t('changes.truncated')}</p>
      ) : null}
    </li>
  )
}

export function SourceChanges({ card }: { card: ActionCard }) {
  const { t, fmt } = useI18n()
  const changes = useSourceChanges(card, true)
  const data = changes.data

  if (changes.error) {
    return (
      <p className="studio-muted studio-diff-note" role="status">
        <Icon name="warn" size={13} /> {t('changes.unreadable')}
      </p>
    )
  }
  if (!data) return <p className="studio-muted">{t('changes.loading')}</p>
  if (!data.available) {
    return (
      <p className="studio-muted studio-diff-note" role="status">
        <Icon name="warn" size={13} /> {t('changes.gitUnavailable')}
      </p>
    )
  }
  const traced = data.listed_by === 'traceability'
  if (data.files.length === 0) {
    const key = traced ? (data.manifest_readable ? 'changes.noneTraced' : 'changes.noTraceability')
      : (data.manifest_readable ? 'changes.none' : 'changes.noManifest')
    return <p className="studio-muted">{t(key)}</p>
  }
  const added = data.files.reduce((sum, file) => sum + file.additions, 0)
  const removed = data.files.reduce((sum, file) => sum + file.deletions, 0)
  const base = data.base ?? null
  const until = base ? data.until ?? null : null
  // Against a decision-time base each file is read up to the commit that took the change in: one commit for all of
  // them, one per file, or none yet (today's files).
  const committed = Boolean(base) && data.files.some((file) => file.until)
  const eachUntil = committed && !until
  const counts = { n: fmt.number(data.files.length), added: fmt.number(added), removed: fmt.number(removed) }
  const summary = !base ? t('changes.summary', counts)
    : until ? t('changes.summaryUntil', { ...counts, sha: base.slice(0, 7), until: until.slice(0, 7) })
      : eachUntil ? t('changes.summaryUntilEach', { ...counts, sha: base.slice(0, 7) })
        : t('changes.summaryBase', { ...counts, sha: base.slice(0, 7) })

  return (
    <section className="studio-srcchanges" aria-label={base
      ? t('changes.labelBase', { stage: card.stage?.slug ?? '', sha: base.slice(0, 7) })
      : t('changes.label', { stage: card.stage?.slug ?? '' })}>
      <p className="studio-muted">
        {summary}
      </p>
      {isClosedAction(card) ? (
        // Read now either way; without the captured commit it is today's HEAD, which may already hold the change.
        <p className="studio-muted studio-diff-note">
          <Icon name="info" size={13} /> {t(committed ? 'changes.readCommitted' : base ? 'changes.readNowBase' : 'changes.readNow')}
        </p>
      ) : null}
      {traced ? (
        <p className="studio-muted studio-diff-note"><Icon name="info" size={13} /> {t('changes.fromTraceability')}</p>
      ) : null}
      <ul className="studio-srcchange-list">
        {data.files.map((change) => <FileChange change={change} base={base} eachUntil={eachUntil} key={change.path} />)}
      </ul>
      {data.truncated ? <p className="studio-muted">{t('changes.moreFiles')}</p> : null}
    </section>
  )
}
