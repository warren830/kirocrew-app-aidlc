/**
 * One artifact, rendered read-only (FR-ART-001/002/003).
 *
 * Four decisions here are load-bearing:
 *
 *  1. **Markdown goes through the host's `MarkdownRenderer`, wrapped in `.msg-content`.** That component
 *     is the allowlist sanitiser (raw HTML escaped, `on*`/`style` dropped, mermaid in `securityLevel:
 *     'strict'`); `.msg-content` is the ancestor its prose styles are keyed on. Studio never reaches for
 *     React's raw-HTML escape hatch: an AI-DLC artifact is untrusted text written by an agent.
 *  2. **Anything that is not markdown is shown as text, not "rendered".** A `.json` or `.txt` artifact in
 *     a markdown pipeline would silently eat its own `#`, `*` and `_`, which in a diff-reading tool is a
 *     lie about file content.
 *  3. **An artifact Studio will not render is never fetched, and says why.** `meta.renderable` is the
 *     backend's own answer (size cap plus text-like extension); reading a 200 MB file to discover that
 *     would stall the gateway, and a blank pane would read as "the stage produced nothing".
 *  4. **"Open in editor" appears only when the host reports that seam.** FR-ART-007 allows an existing
 *     approved KiroCrew affordance and nothing else; a button that quietly does nothing teaches the user
 *     that Studio's affordances are decorative. No shipped host build reports this capability today, so
 *     the control is normally absent — that is the correct behaviour, not a gap.
 */

import { useCallback, useEffect, useMemo, useRef, type ReactNode } from 'react'
import { ContentSkeleton, MarkdownRenderer } from '@kirocrew/app-sdk/ui'

import { useI18n } from '../i18n'
import { useStudioApi } from '../lib/api'
import { bytes as formatBytes } from '../lib/format'
import { usePrefersReducedMotion } from '../lib/host'
import type { ArtifactMeta, ArtifactResponse, Capability } from '../lib/types'
import { useResource, type Resource } from '../lib/useResource'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'

/**
 * The `health.host.capabilities` entry that must be `available` before an editor hand-off is offered.
 * The name is a contract with the backend, not a guess the UI acts on: an absent entry means "no such
 * seam", which is why the default is "no button".
 */
export const EDITOR_CAPABILITY = 'open_in_editor'

/** How long a revealed anchor stays highlighted (visual spec §5.14). */
export const HIGHLIGHT_MS = 2200

/** File extensions rendered as markdown. Everything else is shown verbatim as text. */
const MARKDOWN_RE = /\.(md|markdown|mdx)$/i

export function isMarkdownPath(relpath: string): boolean {
  return MARKDOWN_RE.test(relpath)
}

/**
 * The backend's heading-anchor rule (`handlers/intents.py::_toc`), reimplemented byte-for-byte.
 *
 * It has to match exactly, because the anchor in a deep link (`#h-<slug>`) and the anchor in
 * `toc[].anchor` are produced by that rule, while the element to scroll to is a heading rendered by the
 * host's markdown component — which sets no ids of its own. Matching on the recomputed slug is the only
 * join between the two. Note that a CJK heading slugifies to the empty string on both sides; the
 * text-equality fallback below is what makes zh-CN artifacts navigable.
 */
export function headingSlug(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\- ]/g, '')
    .trim()
    .replace(/ /g, '-')
}

function headings(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>('h1,h2,h3,h4,h5,h6')]
}

/** The element an anchor names: a real `id` first, then a heading whose slug matches. */
export function findAnchorTarget(root: HTMLElement, anchor: string): HTMLElement | null {
  const id = anchor.startsWith('#') ? anchor.slice(1) : anchor
  if (!id) return null
  for (const element of root.querySelectorAll<HTMLElement>('[id]')) {
    if (element.id === id) return element
  }
  const slug = id.startsWith('h-') ? id.slice(2) : id
  if (!slug) return null
  return headings(root).find((h) => headingSlug(h.textContent ?? '') === slug) ?? null
}

/**
 * Scroll an anchor into view and flash it. Returns whether the anchor was found, so a caller can fall
 * back to saying "that section is not in this file" instead of doing nothing.
 */
export function revealAnchor(
  root: HTMLElement | null,
  anchor: string,
  options: { reduced?: boolean; text?: string } = {},
): boolean {
  if (!root || !anchor) return false
  const target =
    findAnchorTarget(root, anchor) ??
    (options.text
      ? headings(root).find((h) => (h.textContent ?? '').trim() === options.text?.trim()) ?? null
      : null)
  if (!target) return false
  // jsdom does not implement scrollIntoView, and neither do very old engines; the highlight alone is
  // still useful, so a missing method must not throw out of the click handler.
  if (typeof target.scrollIntoView === 'function') {
    target.scrollIntoView({ block: 'center', behavior: options.reduced ? 'auto' : 'smooth' })
  }
  target.classList.add('studio-hl')
  window.setTimeout(() => target.classList.remove('studio-hl'), HIGHLIGHT_MS)
  return true
}

/**
 * Read one artifact's body.
 *
 * Exported because more than one surface shows the same file (the Artifacts tab, the gate compare pane)
 * and `useResource` dedupes by key — two callers in one render produce one HTTP request. Polling is off:
 * the file is up to a megabyte, and the event stream already forces a re-read when the intent moves.
 */
export function useArtifactBody(
  repoId: string,
  intentKey: string,
  meta: ArtifactMeta | null,
): Resource<ArtifactResponse> {
  const api = useStudioApi()
  const enabled = meta !== null && meta.renderable
  const key = meta ? `artifact:${repoId}:${intentKey}:${meta.artifact_id}` : null
  const fetcher = useCallback(
    (signal: AbortSignal) => api.artifact(repoId, intentKey, meta?.artifact_id ?? '', { signal }),
    [api, repoId, intentKey, meta?.artifact_id],
  )
  return useResource<ArtifactResponse>(key, fetcher, {
    enabled,
    interval: 0,
    revalidateOn: ['intent.updated', 'reset'],
  })
}

export interface ArtifactViewerProps {
  meta: ArtifactMeta
  /** The body read, or `null` while it is loading / was never attempted. */
  body: ArtifactResponse | null
  loading?: boolean
  /** A decoded read failure. `too_large` is rendered as a fact about the file, not as an error. */
  error?: { code: string; message: string; details: Record<string, unknown>; known: boolean } | null
  /** Route anchor (`h-<slug>` or a bare heading slug) to reveal once the content is on screen. */
  anchor?: string
  capabilities?: Record<string, Capability> | null
  onOpenInEditor?: (relpath: string) => void
  /** Extra chips or controls for the pane header (the owning template's revision chip, for instance). */
  headerExtra?: ReactNode
  /** Content rendered under the body, inside the pane — the diff belongs here. */
  children?: ReactNode
}

export function ArtifactViewer({
  meta,
  body,
  loading = false,
  error = null,
  anchor,
  capabilities,
  onOpenInEditor,
  headerExtra,
  children,
}: ArtifactViewerProps) {
  const i18n = useI18n()
  const { t, fmt } = i18n
  const reduced = usePrefersReducedMotion()
  const bodyRef = useRef<HTMLDivElement | null>(null)

  const markdown = isMarkdownPath(meta.relpath)
  const content = body?.content ?? null
  const binary = body !== null && (body.encoding !== 'utf-8' || body.content === null)
  const tooLarge = error?.code === 'too_large'
  const editorAvailable =
    !!onOpenInEditor && capabilities?.[EDITOR_CAPABILITY]?.available === true

  // The deep-linked section is revealed after the content is in the DOM, and again if the route anchor
  // changes while the same file stays open (a second finding pointing into the same artifact).
  useEffect(() => {
    if (!anchor || content === null) return
    revealAnchor(bodyRef.current, anchor, { reduced })
  }, [anchor, content, reduced])

  const toc = useMemo(() => (body?.toc ?? []).filter((entry) => entry.text.trim() !== ''), [body?.toc])

  const openInEditor = () => onOpenInEditor?.(meta.relpath)
  // The host's MarkdownRenderer is `memo`; a new closure on every render would defeat it on a file
  // that can be a megabyte of markdown.
  const handleFileOpen = useCallback((path: string) => onOpenInEditor?.(path), [onOpenInEditor])

  return (
    <section className="studio-pane studio-artifact" aria-label={t('artifact.pane.label', { name: meta.name })}>
      <header className="studio-pane-head">
        <Icon name="doc" size={13} />
        <span className="studio-pane-nm studio-mono studio-trunc" title={meta.name}>
          {meta.name}
        </span>
        <Chip>{t(`artifact.kind.${meta.kind}`)}</Chip>
        {meta.stage ? (
          // Stage and unit are AI-DLC's own words: never translated, only labelled.
          <Chip icon="map" title={t('artifact.meta.stage')}>
            {meta.stage}
          </Chip>
        ) : null}
        {meta.unit ? (
          <Chip icon="intent" title={t('artifact.meta.unit')}>
            {meta.unit}
          </Chip>
        ) : null}
        {headerExtra}
      </header>

      <dl className="studio-pane-facts">
        <div>
          <dt>{t('artifact.meta.size')}</dt>
          <dd className="studio-mono">{formatBytes(i18n, meta.size)}</dd>
        </div>
        <div>
          <dt>{t('artifact.meta.updated')}</dt>
          <dd className="studio-mono">{fmt.dateTime(meta.mtime)}</dd>
        </div>
        {meta.sha256 ? (
          <div>
            <dt>{t('artifact.meta.sha256')}</dt>
            <dd className="studio-mono studio-trunc" title={meta.sha256}>
              {meta.sha256.slice(0, 12)}
            </dd>
          </div>
        ) : null}
      </dl>

      {toc.length >= 2 && markdown && content !== null ? (
        <nav className="studio-toc" aria-label={t('artifact.toc.label')}>
          {toc.map((entry, index) => (
            <button
              type="button"
              className="studio-toc-item"
              data-level={entry.level}
              key={`${entry.anchor}-${index}`}
              onClick={() => revealAnchor(bodyRef.current, entry.anchor, { reduced, text: entry.text })}
            >
              {entry.text}
            </button>
          ))}
        </nav>
      ) : null}

      <div className="studio-pane-body" ref={bodyRef}>
        {body?.truncated ? (
          // The contract allows a truncated body; showing part of an artifact as if it were all of it
          // is exactly the failure the size cap exists to prevent.
          <p className="studio-notrendered-t">
            <Icon name="warn" size={13} /> {t('artifact.truncated')}
          </p>
        ) : null}
        {!meta.renderable ? (
          <NotRendered
            title={t('artifact.notRendered.title')}
            body={t('artifact.notRendered.body', {
              size: formatBytes(i18n, meta.size),
              kind: t(`artifact.kind.${meta.kind}`),
            })}
          />
        ) : tooLarge ? (
          <NotRendered
            title={t('artifact.tooLarge.title')}
            body={t('artifact.tooLarge.body', {
              size: formatBytes(i18n, numberOr(error?.details['size'], meta.size)),
              cap: formatBytes(i18n, numberOr(error?.details['cap'], null)),
            })}
          />
        ) : error ? (
          <p className="studio-artifact-error" role="status">
            <Icon name="warn" size={13} />{' '}
            {error.known ? t(`errors.${error.code}`) : error.message}
          </p>
        ) : loading && content === null ? (
          <ContentSkeleton rows={6} />
        ) : binary ? (
          <NotRendered title={t('artifact.binary.title')} body={t('artifact.binary.body')} />
        ) : content === null ? (
          <p className="studio-muted">{t('artifact.empty')}</p>
        ) : content.trim() === '' ? (
          <p className="studio-muted">{t('artifact.blank')}</p>
        ) : markdown ? (
          // `.msg-content` is the class the host's prose styles hang off; without it the artifact
          // renders as unstyled runs of text.
          <div className="msg-content studio-md">
            <MarkdownRenderer
              content={content}
              {...(editorAvailable ? { onFileOpen: handleFileOpen } : {})}
            />
          </div>
        ) : (
          <pre className="studio-plain studio-mono">{content}</pre>
        )}
        {children}
      </div>

      <footer className="studio-pane-foot">
        <span className="studio-mono studio-wrap-any studio-grow">{meta.relpath}</span>
        <Chip icon="lock">{t('artifact.readOnly')}</Chip>
        {editorAvailable ? (
          <button type="button" className="studio-btn studio-btn-sm" onClick={openInEditor}>
            <Icon name="external" size={13} /> {t('artifact.openInEditor')}
          </button>
        ) : null}
      </footer>
    </section>
  )
}

function NotRendered({ title, body }: { title: string; body: string }) {
  return (
    <div className="studio-notrendered" role="status">
      <p className="studio-notrendered-t">
        <Icon name="warn" size={13} /> {title}
      </p>
      <p className="studio-muted">{body}</p>
    </div>
  )
}

function numberOr(value: unknown, fallback: number | null): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}
