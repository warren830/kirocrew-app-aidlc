/**
 * The Evidence drawer: the raw record behind one timeline row (FR-EVT-006).
 *
 * Three decisions worth stating:
 *
 *  1. **The raw block is rendered as text, never as markup.** An audit block is AI-DLC's own file
 *     content; it must appear byte-for-byte, and it is untrusted input. A `<pre>` with a text child is the
 *     only rendering that is both. It deliberately does not go through `MarkdownRenderer`: markdown would
 *     reflow the block, and a reader comparing it against the shard on disk needs the bytes.
 *  2. **Absent evidence is explained, not hidden.** `raw` is `null` for two completely different reasons —
 *     a Studio row has no audit block because Studio writes none, and an AI-DLC row read outside the
 *     merged intent timeline was never sent one (§2.6). Showing the same empty box for both would leave
 *     the user thinking AI-DLC recorded nothing.
 *  3. **Non-modal.** The drawer sits beside the timeline and does not trap focus: the reader is comparing
 *     a row against its evidence, so both have to stay reachable. Escape closes it, focus moves to the
 *     close control on open so a keyboard user is not left behind in the list, and closing hands focus
 *     back to the row's Evidence button so they do not lose their place in it either.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { useI18n } from '../i18n'
import { at as formatAt } from '../lib/format'
import type { TimelineEntry } from '../lib/types'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { messageOf, provenanceOf, SOURCE_ICON } from './TimelineRow'

export interface EvidencePanelProps {
  entry: TimelineEntry
  onClose: () => void
  onOpenAction?: (actionId: string) => void
}

/** A params value as display text. Objects and arrays are stringified rather than dropped. */
function valueText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

export function EvidencePanel({ entry, onClose, onOpenAction }: EvidencePanelProps) {
  const i18n = useI18n()
  const { t } = i18n
  const panelRef = useRef<HTMLElement | null>(null)
  const closeRef = useRef<HTMLButtonElement | null>(null)
  /** The Evidence button that opened the drawer on this entry, when that is known. */
  const returnTo = useRef<HTMLElement | null>(null)
  const returnOnUnmount = useRef(false)
  const [copied, setCopied] = useState(false)
  const provenance = provenanceOf(entry)
  const audit = entry.refs.audit
  const actionId = entry.refs.action_id ?? null
  const hasLocation = Boolean(audit || entry.refs.git || actionId || entry.refs.session_key)

  useEffect(() => {
    // Re-read for every entry, because another row's Evidence button re-targets the open drawer. Focus
    // still in the drawer (WebKit does not focus a clicked button) says nothing about which row it was.
    const opener = document.activeElement
    returnTo.current = opener instanceof HTMLElement && opener !== document.body
      && !panelRef.current?.contains(opener) ? opener : null
    closeRef.current?.focus()
  }, [entry])

  // Focus goes back once the drawer is gone, so the button is scrolled to in the layout without it.
  useEffect(() => () => {
    if (returnOnUnmount.current && returnTo.current?.isConnected) returnTo.current.focus()
  }, [])

  const close = useCallback(() => {
    // Only when focus was in the drawer or already lost: Escape closes it from anywhere on the page, and
    // a reader typing in a filter keeps their place there. A scope change that drops the drawer does not
    // come through here at all.
    const active = document.activeElement
    returnOnUnmount.current = !active || active === document.body || Boolean(panelRef.current?.contains(active))
    onClose()
  }, [onClose])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [close])

  useEffect(() => {
    setCopied(false)
  }, [entry])

  const copyRaw = async () => {
    if (!entry.raw) return
    try {
      await navigator.clipboard.writeText(entry.raw)
      setCopied(true)
    } catch {
      // Clipboard access can be refused (no permission, insecure context). The block is on screen and
      // selectable, so the copy button failing quietly costs nothing the user cannot do by hand.
    }
  }

  // `event` is shown on its own line above, so listing it again as a field would read as a duplicate.
  const fields = Object.entries(entry.params).filter(([name]) => name !== 'event')
  // The ref carries the event name when the writer put it there; a projected audit row also has it in
  // `params.event`. Either is the same fact, so one row shows whichever exists.
  const auditEventName =
    audit?.event || (typeof entry.params['event'] === 'string' ? entry.params['event'] : '')

  return (
    <aside ref={panelRef} className="studio-evdrawer" aria-label={t('activity.evidence.title')}>
      <header className="studio-evdrawer-head">
        <h2>
          <Icon name="lock" size={15} /> {t('activity.evidence.title')}
        </h2>
        <button
          type="button"
          ref={closeRef}
          className="studio-icon-btn"
          onClick={close}
          aria-label={t('common.close')}
        >
          <Icon name="close" size={15} />
        </button>
      </header>

      <div className="studio-evdrawer-body">
        <section className="studio-block">
          <h3>{t('activity.evidence.provenance')}</h3>
          <div className="studio-row studio-wrapchips">
            <Chip icon={SOURCE_ICON[entry.source]}>{t(`enum.source.${entry.source}`)}</Chip>
            <Chip tone={provenance === 'mismatch' ? 'danger' : 'neutral'}>
              {provenance === 'audit'
                ? t('activity.row.auditEvent')
                : provenance === 'studio'
                  ? t('activity.row.derived')
                  : t('activity.row.mismatch')}
            </Chip>
          </div>
          <p className="studio-consequence">{t(`activity.evidence.source.${entry.source}`)}</p>
          <dl className="studio-evgrid">
            <div className="studio-evgrid-pair">
              <dt>{t('activity.evidence.at')}</dt>
              <dd className="studio-mono">{formatAt(i18n, entry.at)}</dd>
            </div>
            <div className="studio-evgrid-pair">
              <dt>{t('activity.evidence.kind')}</dt>
              <dd className="studio-mono studio-wrap-any">{entry.kind}</dd>
            </div>
            <div className="studio-evgrid-pair">
              <dt>{t('activity.evidence.messageKey')}</dt>
              <dd className="studio-mono studio-wrap-any">{entry.message_key}</dd>
            </div>
            <div className="studio-evgrid-pair">
              <dt>{t('activity.evidence.severity')}</dt>
              <dd>{t(`enum.severity.${entry.severity}`)}</dd>
            </div>
            {auditEventName ? (
              <div className="studio-evgrid-pair">
                <dt>{t('activity.evidence.auditEvent')}</dt>
                <dd className="studio-mono studio-wrap-any">{auditEventName}</dd>
              </div>
            ) : null}
          </dl>
          {provenance === 'mismatch' ? null : (
            <p className="studio-muted studio-small">{messageOf(i18n, entry)}</p>
          )}
        </section>

        <section className="studio-block">
          <h3>{t('activity.evidence.rawTitle')}</h3>
          {entry.raw ? (
            <>
              {/* A text child, never raw HTML: this is a file's bytes from a user's own repository. */}
              <pre className="studio-pre studio-mono studio-wrap-any">{entry.raw}</pre>
              <div className="studio-row">
                <button type="button" className="studio-btn studio-btn-sm" onClick={() => void copyRaw()}>
                  <Icon name="doc" size={13} />
                  {copied ? t('common.copied') : t('activity.evidence.copy')}
                </button>
              </div>
            </>
          ) : (
            <p className="studio-consequence">
              {entry.source === 'aidlc'
                ? t('activity.evidence.rawNone.aidlc')
                : t('activity.evidence.rawNone.studio')}
            </p>
          )}
        </section>

        <section className="studio-block">
          <h3>{t('activity.evidence.location')}</h3>
          {hasLocation ? (
            <dl className="studio-evgrid">
              {audit ? (
                <div className="studio-evgrid-pair">
                  <dt>{t('activity.evidence.auditShard')}</dt>
                  <dd className="studio-mono studio-wrap-any">
                    {audit.pos === null
                      ? t('activity.row.shardNoPos', { shard: audit.shard })
                      : t('activity.row.shard', { shard: audit.shard, pos: audit.pos })}
                  </dd>
                </div>
              ) : null}
              {entry.refs.git ? (
                <div className="studio-evgrid-pair">
                  <dt>{t('activity.evidence.commit')}</dt>
                  <dd className="studio-mono studio-wrap-any">{entry.refs.git.sha}</dd>
                </div>
              ) : null}
              {entry.refs.session_key ? (
                <div className="studio-evgrid-pair">
                  <dt>{t('activity.evidence.session')}</dt>
                  <dd className="studio-mono studio-wrap-any">{entry.refs.session_key}</dd>
                </div>
              ) : null}
              {actionId ? (
                <div className="studio-evgrid-pair">
                  <dt>{t('activity.evidence.action')}</dt>
                  <dd className="studio-mono studio-wrap-any">
                    {actionId}
                    {onOpenAction ? (
                      <>
                        {' '}
                        <button
                          type="button"
                          className="studio-btn studio-btn-sm"
                          onClick={() => onOpenAction(actionId)}
                          aria-label={t('activity.row.openActionFor', { action: actionId })}
                        >
                          {t('activity.row.openAction')}
                        </button>
                      </>
                    ) : null}
                  </dd>
                </div>
              ) : null}
            </dl>
          ) : (
            <p className="studio-consequence">{t('activity.evidence.locationNone')}</p>
          )}
          <p className="studio-consequence">
            <Icon name="lock" size={13} /> {t('activity.evidence.redaction')}
          </p>
        </section>

        <section className="studio-block">
          <h3>{t('activity.evidence.fields')}</h3>
          {fields.length === 0 ? (
            <p className="studio-consequence">{t('activity.evidence.fieldsNone')}</p>
          ) : (
            <dl className="studio-evgrid">
              {fields.map(([name, value]) => (
                <div key={name} className="studio-evgrid-pair">
                  {/* Audit field names are AI-DLC's own words and stay verbatim in both locales. */}
                  <dt className="studio-mono">{name}</dt>
                  <dd className="studio-mono studio-wrap-any">{valueText(value)}</dd>
                </div>
              ))}
            </dl>
          )}
        </section>
      </div>
    </aside>
  )
}
