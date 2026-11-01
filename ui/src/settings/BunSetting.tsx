import { useEffect, useState } from 'react'

import { useI18n } from '../i18n'
import { decodeError, type StudioApi } from '../lib/api'
import type { BunToolResponse, ConversationBun } from '../lib/types'
import { Chip } from '../shell/Chip'
import { SettingRow } from './SettingsView'

/**
 * Whether AI-DLC's hooks can start Bun inside a conversation, and the one-click way to make them able.
 *
 * Studio runs the engine by absolute path, so "Bun is ready" above can be true while every hook in the
 * harness (`bun .kiro/tools/aidlc.ts …`) fails in the conversation: a desktop gateway's PATH holds four
 * system directories and KiroCrew's own additions, never `~/.bun/bin`. AI-DLC then records no human
 * turn and refuses every approval, so this is shown as a problem, never a footnote.
 */
function ConversationStatus({
  status, busy, onLink,
}: {
  status: ConversationBun
  busy: boolean
  onLink: () => void
}) {
  const { t } = useI18n()
  const [confirming, setConfirming] = useState(false)
  if (status.found) {
    return (
      <p className="studio-row studio-wrapchips">
        <Chip tone="ok" icon="check">{t('settings.bun.conversations.ready')}</Chip>
        {status.path ? <code className="studio-wrap-any">{status.path}</code> : null}
      </p>
    )
  }
  const reason = status.link.blocked_reason
  return (
    <div className="studio-failure-detail studio-bun-conversations" data-tone="warn" role="status">
      <p className="studio-strong">{t('settings.bun.conversations.missing')}</p>
      <p>{t('settings.bun.conversations.why')}</p>
      {status.link.available && status.link.target ? (
        confirming ? (
          <div className="studio-col">
            <p>{t('settings.bun.conversations.confirmBody')}</p>
            <code className="studio-wrap-any">ln -s {status.link.target} {status.link.path}</code>
            <div className="studio-row studio-wrapchips">
              <button className="studio-btn" disabled={busy} onClick={() => setConfirming(false)}>
                {t('install.cancel')}
              </button>
              <button className="studio-btn" data-variant="primary" disabled={busy}
                onClick={() => { setConfirming(false); onLink() }}>
                {t('settings.bun.conversations.confirm')}
              </button>
            </div>
          </div>
        ) : (
          <div className="studio-row studio-wrapchips">
            <button className="studio-btn" data-variant="primary" disabled={busy} onClick={() => setConfirming(true)}>
              {t('settings.bun.conversations.link')}
            </button>
          </div>
        )
      ) : (
        <p>{t(`settings.bun.conversations.blocked.${reason ?? 'bun_missing'}`, { link: status.link.path })}</p>
      )}
      {status.exact ? null : <p className="studio-muted">{t('settings.bun.conversations.estimate')}</p>}
    </div>
  )
}

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

  const update = async (kind: 'save' | 'auto' | 'probe' | 'link') => {
    setBusy(true)
    setNotice(t('settings.bun.checking'))
    try {
      const result = kind === 'probe'
        ? await api.probeBun()
        : kind === 'link'
          ? await api.linkBunForConversations()
          : await api.configureBun(kind === 'auto' ? null : path.trim())
      setApplied(result.tool)
      setHasResult(true)
      if (kind === 'save' || kind === 'auto') {
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
          {current?.found && current.conversations ? (
            <ConversationStatus status={current.conversations} busy={busy} onLink={() => void update('link')} />
          ) : null}
        </div>
      )}
    </SettingRow>
  )
}
