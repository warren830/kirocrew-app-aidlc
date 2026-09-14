import { useEffect, useRef, useState } from 'react'

import { useI18n } from '../i18n'
import { decodeError, isAbort, type StudioApiError } from '../lib/api'
import type { SpacesResponse, WorkspaceApi } from '../plan/workspaceTypes'
import '../plan/workspace.css'

export interface SpaceControlsProps {
  api: Pick<WorkspaceApi, 'spaces' | 'createSpace' | 'switchSpace'>
  repoId: string
  repoLabel?: string
  onChanged?: () => void
}

export function SpaceControls(props: SpaceControlsProps) {
  return <SpaceEditor key={props.repoId} {...props} />
}

function SpaceEditor({ api, repoId, repoLabel, onChanged }: SpaceControlsProps) {
  const { t } = useI18n()
  const [inventory, setInventory] = useState<SpacesResponse | null>(null)
  const [target, setTarget] = useState('')
  const [name, setName] = useState('')
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState<'create' | 'switch' | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<StudioApiError | null>(null)
  const [notice, setNotice] = useState('')
  const mounted = useRef(true)
  const sequence = useRef(0)
  const confirmButton = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (pending) confirmButton.current?.focus()
  }, [pending])
  const validName = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(name)
    && name.length <= 48 && !['help', 'list', 'switch', 'create', 'archive', 'rename', 'show', 'birth'].includes(name)
    && !inventory?.spaces.includes(name)

  function receive(answer: SpacesResponse) {
    setInventory(answer)
    setTarget(answer.active_space)
  }

  async function refresh(signal?: AbortSignal) {
    const ticket = ++sequence.current
    setLoading(true)
    setPending(null)
    setError(null)
    try {
      const answer = await api.spaces(repoId, { signal })
      if (mounted.current && ticket === sequence.current) receive(answer)
    } catch (caught) {
      if (!isAbort(caught) && mounted.current && ticket === sequence.current) setError(decodeError(caught))
    } finally {
      if (mounted.current && ticket === sequence.current) setLoading(false)
    }
  }

  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    void refresh(controller.signal)
    return () => { mounted.current = false; sequence.current += 1; controller.abort() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, repoId])

  async function apply() {
    if (!pending || busy) return
    const operation = pending
    setBusy(true)
    setError(null)
    setNotice('')
    try {
      const answer = operation === 'create'
        ? await api.createSpace(repoId, name)
        : await api.switchSpace(repoId, target)
      if (!mounted.current) return
      receive(answer)
      if (operation === 'create') setName('')
      setNotice(t(operation === 'create' ? 'workspace.spaces.created' : 'workspace.spaces.switched'))
      onChanged?.()
    } catch (caught) {
      if (!mounted.current) return
      setError(decodeError(caught))
      // Partial engine failures can still change disk. Re-read without turning that into success.
      try {
        const answer = await api.spaces(repoId)
        if (mounted.current) receive(answer)
      } catch { /* Keep the original mutation error; refresh remains available. */ }
    } finally {
      if (mounted.current) { setBusy(false); setPending(null) }
    }
  }

  return (
    <section className="studio-panel studio-workspace-controls" aria-label={t('workspace.spaces.title')}>
      <header className="studio-panel-head">
        <h2>{t('workspace.spaces.title')}{repoLabel ? ` · ${repoLabel}` : ''}</h2>
        <button type="button" className="studio-btn" disabled={busy || loading} onClick={() => void refresh()}>{t('workspace.spaces.refresh')}</button>
      </header>
      <p className="studio-lede">{t('workspace.spaces.description')}</p>
      {inventory ? <p className="studio-wrap-any">{t('workspace.spaces.active', { name: inventory.active_space })}</p> : null}
      {loading ? <p role="status">{t('common.loading')}</p> : null}
      {error ? <p className="studio-failure-detail" role="alert">{error.known ? t(`errors.${error.code}`) : error.message}</p> : null}
      {notice ? <p role="status">{notice}</p> : null}
      <fieldset className="studio-workspace-group" disabled={loading || busy || !inventory}>
        <legend className="studio-field-label">{t('workspace.spaces.switch')}</legend>
        <div className="studio-workspace-fields">
          <label className="studio-field">
            <span className="studio-field-label">{t('workspace.spaces.target')}</span>
            <select className="studio-input" value={target} onChange={(event) => { setTarget(event.target.value); setPending(null); setNotice('') }}>
              {inventory?.spaces.map((space) => <option key={space} value={space}>{space}</option>)}
            </select>
          </label>
          <button type="button" className="studio-btn" disabled={!target || target === inventory?.active_space}
            onClick={() => setPending('switch')}>{t('workspace.spaces.switch')}</button>
        </div>
      </fieldset>
      <fieldset className="studio-workspace-group" disabled={loading || busy || !inventory}>
        <legend className="studio-field-label">{t('workspace.spaces.create')}</legend>
        <div className="studio-workspace-fields">
          <label className="studio-field">
            <span className="studio-field-label">{t('workspace.spaces.name')}</span>
            <input className="studio-input studio-mono" value={name} maxLength={48} onChange={(event) => { setName(event.target.value); setPending(null); setNotice('') }} />
          </label>
          <button type="button" className="studio-btn" disabled={!validName} onClick={() => setPending('create')}>{t('workspace.spaces.create')}</button>
        </div>
        <p className="studio-lede">{t('workspace.spaces.nameHint')}</p>
      </fieldset>
      {pending ? (
        <fieldset className="studio-confirm" disabled={busy}>
          <legend className="studio-field-label">{t('workspace.spaces.confirmTitle')}</legend>
          <p className="studio-wrap-any">{t(pending === 'create' ? 'workspace.spaces.confirmCreate' : 'workspace.spaces.confirmSwitch', { name: pending === 'create' ? name : target })}</p>
          <div className="studio-confirm-actions">
            <button ref={confirmButton} type="button" className="studio-btn" data-variant="primary" onClick={() => void apply()}>{t('workspace.spaces.confirm')}</button>
            <button type="button" className="studio-btn" onClick={() => setPending(null)}>{t('common.cancel')}</button>
          </div>
        </fieldset>
      ) : null}
    </section>
  )
}
