/**
 * The current-vs-prior diff, as text the user can read (FR-ART-004).
 *
 * The backend supplies `prior.available` (HEAD has a version of this file) and `prior.diff` (the unified
 * diff git printed, or `null` when git could not answer, or `""` when nothing changed). Those three
 * cases are genuinely different and each is rendered as itself:
 *
 *   - not available  → nothing at all. Inventing "no prior version" copy where the backend never looked
 *     would read as a fact about the repository.
 *   - `diff === null` → Git observation is degraded (FR-GIT-005): say so, and never let it look like
 *     "the file is unchanged".
 *   - `diff === ''`  → the committed version and the working tree agree. That is information.
 *
 * The `+`/`-`/`@@` prefixes stay in the text and each changed line carries a visually-hidden word, so
 * the diff is legible without colour (PRD §10.3) and to a screen reader.
 */

import { useMemo, useState } from 'react'

import { useI18n } from '../i18n'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'

/** How many lines render before the "show the rest" control appears. */
export const DIFF_LINE_BUDGET = 400

export type DiffLineKind = 'add' | 'del' | 'hunk' | 'meta' | 'context'

export interface DiffLine {
  kind: DiffLineKind
  text: string
}

const META_PREFIXES = ['diff --git', 'index ', 'new file mode', 'deleted file mode', 'old mode', 'new mode', 'similarity index', 'rename from', 'rename to', '--- ', '+++ ', '\\']

/** Classify a unified-diff line. Pure, so the classification is testable without a DOM. */
export function classifyDiffLine(line: string): DiffLineKind {
  if (line.startsWith('@@')) return 'hunk'
  for (const prefix of META_PREFIXES) if (line.startsWith(prefix)) return 'meta'
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'del'
  return 'context'
}

export function parseDiff(diff: string): DiffLine[] {
  // A trailing newline is a formatting artefact of the git output, not an empty last line of the file.
  const body = diff.endsWith('\n') ? diff.slice(0, -1) : diff
  if (body === '') return []
  return body.split('\n').map((text) => ({ kind: classifyDiffLine(text), text }))
}

export interface ArtifactDiffProps {
  /** `ArtifactResponse.prior` verbatim. */
  prior: { available: boolean; source: 'git' | null; diff: string | null }
  /** The file this diff is about — named in the group label so a screen reader knows which pane it is in. */
  name: string
}

export function ArtifactDiff({ prior, name }: ArtifactDiffProps) {
  const { t, fmt } = useI18n()
  const [budget, setBudget] = useState(DIFF_LINE_BUDGET)
  const lines = useMemo(() => (prior.diff ? parseDiff(prior.diff) : []), [prior.diff])

  if (!prior.available) return null

  const shown = lines.slice(0, budget)
  const hidden = lines.length - shown.length

  return (
    <section className="studio-diff" aria-label={t('artifact.diff.label', { name })}>
      <div className="studio-diff-head">
        <h5>{t('artifact.diff.title')}</h5>
        {/* The source is part of the claim: this comparison is the repository's own history, not a
            Studio copy of the artifact (Studio keeps none — a second copy would be a rival authority). */}
        <Chip tone="info" icon="git">
          {prior.source === 'git' ? t('artifact.diff.fromGit') : t('artifact.diff.fromUnknown')}
        </Chip>
      </div>

      {prior.diff === null ? (
        <p className="studio-muted studio-diff-note">
          <Icon name="warn" size={13} /> {t('artifact.diff.unavailable')}
        </p>
      ) : lines.length === 0 ? (
        <p className="studio-muted studio-diff-note">
          <Icon name="check" size={13} /> {t('artifact.diff.unchanged')}
        </p>
      ) : (
        <>
          <div className="studio-difflines">
            {shown.map((line, index) => (
              <div className="studio-diffline" data-kind={line.kind} key={`${index}-${line.text}`}>
                {line.kind === 'add' || line.kind === 'del' ? (
                  <span className="studio-sr">
                    {line.kind === 'add' ? t('artifact.diff.added') : t('artifact.diff.removed')}
                  </span>
                ) : null}
                {/* A zero-width-safe blank: an empty context line must still occupy a row. */}
                {line.text === '' ? ' ' : line.text}
              </div>
            ))}
          </div>
          {hidden > 0 ? (
            <button
              type="button"
              className="studio-btn studio-btn-sm"
              onClick={() => setBudget(lines.length)}
            >
              {t('artifact.diff.showRest', { n: fmt.number(hidden) })}
            </button>
          ) : null}
        </>
      )}
    </section>
  )
}
