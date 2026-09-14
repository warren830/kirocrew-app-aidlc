import { useEffect, useRef, useState } from 'react'

import { useI18n } from '../i18n'
import { decodeError, type StudioApiError } from '../lib/api'
import type { IntentDepth, IntentSettings, SettingsPreviewResponse, WorkspaceApi } from './workspaceTypes'
import './workspace.css'

export interface IntentSettingsPanelProps {
  api: Pick<WorkspaceApi, 'intentSettingsPreview' | 'changeIntentSettings'>
  repoId: string
  intentKey: string
  intentLabel?: string
  onApplied?: () => void
  onClose?: () => void
}

const DEPTHS: IntentDepth[] = ['Minimal', 'Standard', 'Comprehensive']

/** Identity changes discard drafts and pending confirmations, even if a previous request finishes late. */
export function IntentSettingsPanel(props: IntentSettingsPanelProps) {
  return <SettingsEditor key={`${props.repoId}:${props.intentKey}`} {...props} />
}

function SettingsEditor({ api, repoId, intentKey, intentLabel, onApplied, onClose }: IntentSettingsPanelProps) {
  const { t } = useI18n()
  const [draft, setDraft] = useState<IntentSettings | null>(null)
  const [scopes, setScopes] = useState<string[]>([])
  const [preview, setPreview] = useState<SettingsPreviewResponse | null>(null)
  const [busy, setBusy] = useState(true)
  const [applying, setApplying] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [applied, setApplied] = useState(false)
  const [error, setError] = useState<StudioApiError | null>(null)
  const sequence = useRef(0)
  const mounted = useRef(true)
  const confirmButton = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (confirming) confirmButton.current?.focus()
  }, [confirming])

  async function load(changes: Partial<IntentSettings>, initial = false) {
    const ticket = ++sequence.current
    setBusy(true)
    setPreview(null)
    setConfirming(false)
    setError(null)
    try {
      const answer = await api.intentSettingsPreview(repoId, intentKey, changes)
      if (!mounted.current || sequence.current !== ticket) return
      setPreview(answer)
      setScopes(answer.proposal.scopes)
      if (initial) setDraft(answer.proposal.after)
    } catch (caught) {
      if (mounted.current && sequence.current === ticket) setError(decodeError(caught))
    } finally {
      if (mounted.current && sequence.current === ticket) setBusy(false)
    }
  }

  useEffect(() => {
    mounted.current = true
    void load({}, true)
    return () => { mounted.current = false; sequence.current += 1 }
    // The outer keyed component fixes the repository/intent for this editor's lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, repoId, intentKey])

  function edit(key: keyof IntentSettings, value: string) {
    if (!draft) return
    sequence.current += 1
    setDraft({ ...draft, [key]: value })
    setPreview(null)
    setConfirming(false)
    setApplied(false)
    setError(null)
    setBusy(false)
  }

  async function apply() {
    if (!preview?.proposal.allowed || applying) return
    const confirmed = preview
    setApplying(true)
    setError(null)
    try {
      const answer = await api.changeIntentSettings(repoId, intentKey, {
        ...confirmed.proposal.after, proposal_digest: confirmed.proposal_digest,
      })
      if (!mounted.current) return
      setDraft(answer.verified.settings)
      setApplied(true)
      onApplied?.()
    } catch (caught) {
      if (mounted.current) setError(decodeError(caught))
    } finally {
      if (mounted.current) {
        setApplying(false)
        setConfirming(false)
        setPreview(null)
      }
    }
  }

  const proposal = preview?.proposal
  const changedStages = proposal?.stages.filter((stage) => stage.before !== stage.after) ?? []
  const errorText = error ? (error.known ? t(`errors.${error.code}`) : error.message) : ''

  return (
    <section className="studio-panel studio-workspace-controls" aria-label={t('workspace.settings.title')}>
      <header className="studio-panel-head">
        <h2>{t('workspace.settings.title')}{intentLabel ? ` · ${intentLabel}` : ''}</h2>
        {onClose ? <button type="button" className="studio-btn" disabled={applying} onClick={onClose}>{t('common.close')}</button> : null}
      </header>
      <p className="studio-lede">{t('workspace.settings.description')}</p>
      <div className="studio-workspace-fields">
        <label className="studio-field">
          <span className="studio-field-label">{t('workspace.settings.scope')}</span>
          <select className="studio-input" value={draft?.scope ?? ''} disabled={!draft || applying} onChange={(event) => edit('scope', event.target.value)}>
            {!draft ? <option value="">{t('common.loading')}</option> : null}
            {scopes.map((scope) => <option key={scope} value={scope}>{scope}</option>)}
          </select>
        </label>
        {(['depth', 'test_strategy'] as const).map((field) => (
          <label key={field} className="studio-field">
            <span className="studio-field-label">{t(`workspace.settings.${field}`)}</span>
            <select className="studio-input" value={draft?.[field] ?? ''} disabled={!draft || applying} onChange={(event) => edit(field, event.target.value)}>
              {!draft ? <option value="">{t('common.loading')}</option> : null}
              {DEPTHS.map((depth) => <option key={depth} value={depth}>{t(`workspace.depth.${depth}`)}</option>)}
            </select>
          </label>
        ))}
        <button type="button" className="studio-btn" disabled={busy || applying}
          onClick={() => void load(draft ?? {}, !draft)}>
          {t('workspace.settings.preview')}
        </button>
      </div>
      {busy ? <p role="status">{t('common.loading')}</p> : null}
      {error ? <p className="studio-failure-detail" role="alert">{errorText}</p> : null}
      {applied ? <p role="status">{t('workspace.settings.applied')}</p> : null}
      {proposal && !busy ? (
        <div className="studio-workspace-preview">
          <p className="studio-wrap-any">{t('workspace.settings.selection', { space: proposal.selection.space, intent: proposal.selection.intent_dir })}</p>
          <dl className="studio-facts">
            {(['scope', 'depth', 'test_strategy'] as const).map((field) => (
              <div key={field} className="studio-fact">
                <dt>{t(`workspace.settings.${field}`)}</dt>
                <dd>{proposal.before[field]} → {proposal.after[field]}</dd>
              </div>
            ))}
          </dl>
          <p>{t('workspace.settings.preserved', { n: proposal.stages.filter((stage) => stage.state === 'completed').length })}</p>
          {changedStages.length ? (
            <div className="studio-workspace-table">
            <table className="studio-tbl">
              <caption>{t('workspace.settings.stageChanges')}</caption>
              <thead><tr><th>{t('workspace.settings.stage')}</th><th>{t('workspace.settings.before')}</th><th>{t('workspace.settings.after')}</th></tr></thead>
              <tbody>{changedStages.map((stage) => (
                <tr key={stage.slug}>
                  <th scope="row" className="studio-mono studio-wrap-any">{stage.slug}</th>
                  <td>{t(stage.before ? 'workspace.settings.execute' : 'workspace.settings.skip')}</td>
                  <td>{t(stage.after ? 'workspace.settings.execute' : 'workspace.settings.skip')}</td>
                </tr>
              ))}</tbody>
            </table>
            </div>
          ) : null}
          {proposal.refusals.map((reason) => <p key={reason} role="status">{t(`workspace.refusal.${reason}`)}</p>)}
          <details><summary>{t('workspace.settings.command')}</summary><pre className="studio-sendtext">{JSON.stringify(proposal.argv_preview, null, 2)}</pre></details>
          {!confirming ? (
            <button type="button" className="studio-btn" data-variant="primary" disabled={!proposal.allowed || applying}
              onClick={() => setConfirming(true)}>{t('workspace.settings.review')}</button>
          ) : (
            <fieldset className="studio-confirm" disabled={applying}>
              <legend className="studio-field-label">{t('workspace.settings.confirmTitle')}</legend>
              <p>{t('workspace.settings.confirmBody')}</p>
              <div className="studio-confirm-actions">
                <button ref={confirmButton} type="button" className="studio-btn" data-variant="primary" onClick={() => void apply()}>{t('workspace.settings.apply')}</button>
                <button type="button" className="studio-btn" onClick={() => setConfirming(false)}>{t('common.cancel')}</button>
              </div>
            </fieldset>
          )}
        </div>
      ) : null}
    </section>
  )
}
