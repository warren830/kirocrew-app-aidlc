import { useState } from 'react'

import { useI18n } from '../i18n'
import { decodeError, type StudioApi } from '../lib/api'
import type { DirectoryResponse } from '../lib/types'
import { ErrorNote } from './PreflightReport'

/** Browses one explicitly requested gateway directory; typing alone never reads disk. */
export function DirectoryPicker({ api, initialPath, onSelect, disabled = false }: {
  api: StudioApi
  initialPath: string
  onSelect: (path: string) => void
  disabled?: boolean
}) {
  const { t } = useI18n()
  const [listing, setListing] = useState<DirectoryResponse | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ReturnType<typeof decodeError> | null>(null)
  const browse = async (path?: string | null) => {
    setOpen(true)
    setBusy(true)
    setError(null)
    try {
      setListing(await api.directories(path))
    } catch (caught) {
      setError(decodeError(caught))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="studio-col">
      <button type="button" className="studio-btn" disabled={disabled || busy}
        onClick={() => void browse(initialPath.trim() || null)}>
        {t('repos.directory.open')}
      </button>
      {open ? (
        <div className="studio-block" aria-busy={busy}>
          <p className="studio-note">{t('repos.directory.help')}</p>
          {error ? <ErrorNote error={error} /> : null}
          {listing ? (
            <>
              <p className="studio-mono studio-wrap-any">{listing.path}</p>
              <div className="studio-row">
                <button type="button" className="studio-btn" disabled={busy || !listing.parent}
                  onClick={() => void browse(listing.parent)}>{t('repos.directory.parent')}</button>
                <button type="button" className="studio-btn" disabled={busy}
                  onClick={() => { onSelect(listing.path); setOpen(false) }}>
                  {t('repos.directory.select')}
                </button>
              </div>
              <ul className="studio-directory-list" aria-label={t('repos.directory.list')}>
                {listing.directories.map((dir) => (
                  <li key={dir.path}>
                    <button type="button" className="studio-btn" disabled={busy}
                      onClick={() => void browse(dir.path)}>{dir.name}</button>
                  </li>
                ))}
              </ul>
              {listing.truncated ? <p className="studio-note">{t('repos.directory.truncated')}</p> : null}
            </>
          ) : null}
          <button type="button" className="studio-btn" disabled={busy}
            onClick={() => setOpen(false)}>{t('common.close')}</button>
        </div>
      ) : null}
    </div>
  )
}
