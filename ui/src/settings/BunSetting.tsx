import { useEffect, useState } from 'react'

import { useI18n } from '../i18n'
import { decodeError, type StudioApi } from '../lib/api'
import type { BunToolResponse } from '../lib/types'
import { SettingRow } from './SettingsView'

export function BunSetting({
  api, tool, onChanged,
}: {
  api: StudioApi
  tool: BunToolResponse['tool'] | null
  onChanged: () => void
}) {
  const { t, has } = useI18n()
  const [path, setPath] = useState(tool?.configured_path ?? '')
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [applied, setApplied] = useState<BunToolResponse['tool'] | null>(null)
  const [hasResult, setHasResult] = useState(false)
  const current = applied ?? tool

  useEffect(() => {
    // The mutation receipt bridges the refresh; the next server observation supersedes it.
    setApplied(null)
  }, [tool])

  useEffect(() => {
    if (!dirty) setPath(current?.configured_path ?? '')
  }, [current?.configured_path, dirty])

  const update = async (kind: 'save' | 'auto' | 'probe') => {
    setBusy(true)
    setNotice(t('settings.bun.checking'))
    try {
      const result = kind === 'probe' ? await api.probeBun() : await api.configureBun(kind === 'auto' ? null : path.trim())
      setApplied(result.tool)
      setHasResult(true)
      if (kind !== 'probe') {
        setDirty(false)
        setPath(result.tool.configured_path ?? '')
      }
      setNotice('')
      onChanged()
    } catch (caught) {
      const error = decodeError(caught)
      setNotice(has(`errors.${error.code}`) ? t(`errors.${error.code}`) : error.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <SettingRow id="aidlc-bun" title={t('settings.bun.title')} description={t('settings.bun.desc')}>
      {(describedBy) => (
        <div className="studio-col">
          <label htmlFor="aidlc-bun-path">{t('settings.bun.path')}</label>
          <input
            id="aidlc-bun-path" className="studio-input" value={path} disabled={busy}
            aria-describedby={describedBy} placeholder="/absolute/path/to/bun"
            onChange={(event) => { setPath(event.target.value); setDirty(true) }}
          />
          <div className="studio-row studio-wrapchips">
            <button className="studio-btn" disabled={busy || !path.trim()} onClick={() => void update('save')}>
              {t('settings.bun.save')}
            </button>
            <button className="studio-btn" disabled={busy} onClick={() => void update('probe')}>
              {t('settings.bun.probe')}
            </button>
            <button className="studio-btn" disabled={busy} onClick={() => void update('auto')}>
              {t('settings.bun.auto')}
            </button>
          </div>
          {current?.path ? <code className="studio-wrap-any">{current.path} · {current.version}</code> : null}
          {current?.searched?.length ? (
            <p className="studio-muted studio-wrap-any">{t('settings.bun.searched', { paths: current.searched.join(' · ') })}</p>
          ) : null}
          <p role="status" aria-live="polite">
            {notice || (hasResult && current ? t(current.found ? 'settings.bun.ready' : 'settings.bun.missing') : '')}
          </p>
        </div>
      )}
    </SettingRow>
  )
}
