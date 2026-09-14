/**
 * The diagnostic export (FR-EVT-007).
 *
 * The rule the layout encodes: the redaction list is above the button, always, and nothing is fetched
 * until the button is pressed. A support bundle is the one place in Studio where a user hands the contents
 * of their machine to somebody else, so "explain, then produce" is the order — not "produce, then explain
 * in a tooltip".
 *
 * Two further deliberate steps: producing the bundle does not start a download (the file is held in memory
 * and offered as an explicit link, so a mistaken click cannot leave a bundle in the Downloads folder), and
 * the "include prompt bodies" box is inert unless the *server-side* setting already allows it — the backend
 * refuses human text whatever the query string says (§2.1), and a checkbox that looks armed but changes
 * nothing would teach the user the wrong thing about where the guarantee lives.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Btn } from '@kirocrew/app-sdk/ui'

import { useI18n } from '../i18n'
import { decodeError, useStudioApi } from '../lib/api'
import { at as formatAt, bytes as formatBytes } from '../lib/format'
import { Icon } from '../shell/Icon'

export interface ExportPanelProps {
  /** `settings.diagnostics.export_include_human_text` — the server-side gate, not a UI preference. */
  allowHumanText: boolean
  /** Rendered as a hint when the gate is off; the Settings page passes its own section anchor handler. */
  onOpenSettings?: () => void
}

interface ReadyExport {
  url: string
  filename: string
  size: number
  generatedAt: string
}

function filenameFor(generatedAt: string): string {
  // The backend's own Content-Disposition pattern (§2.1); the SDK hands us a parsed body, not headers,
  // so the name is rebuilt here rather than guessed at download time.
  const stamp = (Number.isFinite(Date.parse(generatedAt)) ? new Date(generatedAt) : new Date())
    .toISOString()
    .replace(/[:.]/g, '-')
  return `aidlc-studio-diagnostics-${stamp}.json`
}

export function ExportPanel({ allowHumanText, onOpenSettings }: ExportPanelProps) {
  const i18n = useI18n()
  const { t } = i18n
  const api = useStudioApi()
  const [includeHumanText, setIncludeHumanText] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [ready, setReady] = useState<ReadyExport | null>(null)
  const urlRef = useRef<string | null>(null)

  // An object URL pins the blob in memory until it is revoked, and a support bundle is large enough that
  // leaking one per press is a real leak, not a rounding error.
  const release = useCallback(() => {
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current)
      urlRef.current = null
    }
  }, [])
  useEffect(() => release, [release])

  // Turning the server-side gate off must also disarm the request; otherwise the box stays checked,
  // greyed out, and the next export silently asks for text the backend will refuse.
  useEffect(() => {
    if (!allowHumanText) setIncludeHumanText(false)
  }, [allowHumanText])

  const produce = async () => {
    setBusy(true)
    setError(null)
    try {
      const bundle = await api.diagnostics({
        export: true,
        includeHumanText: includeHumanText && allowHumanText,
      })
      const text = JSON.stringify(bundle, null, 2)
      const blob = new Blob([text], { type: 'application/json' })
      release()
      const url = URL.createObjectURL(blob)
      urlRef.current = url
      setReady({
        url,
        filename: filenameFor(bundle.generated_at),
        size: blob.size,
        generatedAt: bundle.generated_at,
      })
    } catch (caught) {
      const decoded = decodeError(caught)
      setReady(null)
      setError(i18n.has(`errors.${decoded.code}`) ? t(`errors.${decoded.code}`) : decoded.message)
    } finally {
      setBusy(false)
    }
  }

  const discard = () => {
    release()
    setReady(null)
  }

  return (
    <section className="studio-block" aria-label={t('activity.export.region')}>
      <h3>{t('activity.export.title')}</h3>
      <p className="studio-lede">{t('activity.export.lede')}</p>

      <div className="studio-export-grid">
        <div>
          <h4 className="studio-subhead">{t('activity.export.redactsTitle')}</h4>
          <ul className="studio-list">
            <li>{t('activity.export.redacts.credentials')}</li>
            <li>{t('activity.export.redacts.paths')}</li>
            <li>{t('activity.export.redacts.humanText')}</li>
            <li>{t('activity.export.redacts.artifacts')}</li>
            <li>{t('activity.export.redacts.unrelated')}</li>
          </ul>
        </div>
        <div>
          <h4 className="studio-subhead">{t('activity.export.includesTitle')}</h4>
          <ul className="studio-list">
            <li>{t('activity.export.includes.health')}</li>
            <li>{t('activity.export.includes.settings')}</li>
            <li>{t('activity.export.includes.repos')}</li>
            <li>{t('activity.export.includes.actions')}</li>
            <li>{t('activity.export.includes.activity')}</li>
          </ul>
        </div>
      </div>

      <div className="studio-export-opt">
        <label className="studio-check">
          <input
            type="checkbox"
            checked={includeHumanText}
            disabled={!allowHumanText}
            onChange={(event) => setIncludeHumanText(event.target.checked)}
            aria-describedby="aidlc-export-humantext-note"
          />
          <span>{t('activity.export.humanText')}</span>
        </label>
        <p id="aidlc-export-humantext-note" className="studio-consequence">
          {allowHumanText ? (
            t('activity.export.humanTextWarn')
          ) : (
            <>
              <Icon name="lock" size={13} /> {t('activity.export.humanTextBlocked')}
              {onOpenSettings ? (
                <>
                  {' '}
                  <button type="button" className="studio-btn studio-btn-sm" onClick={onOpenSettings}>
                    <Icon name="settings" size={13} />
                    {t('settings.page.title')}
                  </button>
                </>
              ) : null}
            </>
          )}
        </p>
      </div>

      <div className="studio-row studio-wrapchips">
        <Btn primary type="button" onClick={() => void produce()} disabled={busy}>
          <Icon name="doc" size={13} />
          {busy ? t('activity.export.producing') : t('activity.export.produce')}
        </Btn>
      </div>

      {/* Polite: producing a bundle is a status change the user asked for, not an interruption. */}
      <div role="status" aria-live="polite" className="studio-export-result">
        {error ? (
          <p className="studio-error">{t('activity.export.failed', { message: error })}</p>
        ) : ready ? (
          <>
            <p>
              {t('activity.export.ready', {
                size: formatBytes(i18n, ready.size),
                when: formatAt(i18n, ready.generatedAt),
              })}
            </p>
            <div className="studio-row studio-wrapchips">
              <a className="studio-btn" href={ready.url} download={ready.filename}>
                <Icon name="external" size={13} />
                {t('activity.export.download', { filename: ready.filename })}
              </a>
              <button type="button" className="studio-btn studio-btn-sm" onClick={discard}>
                {t('activity.export.discard')}
              </button>
            </div>
          </>
        ) : null}
      </div>
    </section>
  )
}
