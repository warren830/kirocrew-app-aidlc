import { useEffect, useRef, useState } from 'react'

import { useI18n } from '../i18n'
import { decodeError, type StudioApi, type StudioApiError } from '../lib/api'
import { bytes } from '../lib/format'
import type { MaintenancePreview, MaintenanceResult } from '../lib/types'
import { Block, ErrorNote, Note } from './PreflightReport'

export function MaintenancePanel({ api, repoId, onClose, onChanged }: {
  api: StudioApi; repoId: string; onClose: () => void; onChanged: () => void
}) {
  const i18n = useI18n()
  const { t, has } = i18n
  const [plan, setPlan] = useState<MaintenancePreview | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [reviewed, setReviewed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<StudioApiError | null>(null)
  const [result, setResult] = useState<MaintenanceResult | null>(null)
  const alive = useRef(true)
  async function preview(ids: string[] = [], review = false) {
    setBusy(true)
    setReviewed(false)
    setError(null)
    try {
      const next = await api.maintenancePreview(repoId, ids)
      if (!alive.current) return
      setPlan(next)
      setSelected(next.entry_ids)
      setReviewed(review && next.can_cleanup)
    } catch (caught) {
      if (alive.current) setError(decodeError(caught))
    } finally {
      if (alive.current) setBusy(false)
    }
  }
  useEffect(() => {
    alive.current = true
    void preview()
    return () => { alive.current = false }
    // The Repos page keys this panel by repository.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, repoId])
  async function cleanup() {
    if (!reviewed || !plan?.can_cleanup) return
    setBusy(true)
    setReviewed(false)
    setError(null)
    try {
      const answer = await api.cleanupMaintenance(repoId, plan.entry_ids, plan.plan_digest)
      if (!alive.current) return
      setResult(answer)
      setSelected([])
      onChanged()
      await preview()
    } catch (caught) {
      if (alive.current) setError(decodeError(caught))
    } finally {
      if (alive.current) setBusy(false)
    }
  }
  const reason = (code: string) => has(`maintenance.reason.${code}`) ? t(`maintenance.reason.${code}`) : code
  return (
    <Block title={t('maintenance.title')} icon="doc">
      <Note>{t('maintenance.description')}</Note>
      {busy ? <p role="status">{t('common.loading')}</p> : null}
      {error ? <ErrorNote error={error} /> : null}
      {result ? (
        <div role="status">
          <p>{t(result.ok ? 'maintenance.completed' : 'maintenance.partial', { count: result.deleted.length })}</p>
          {result.failures.map((failure) => <p key={failure.id}>{failure.id}: {reason(failure.reason)}</p>)}
        </div>
      ) : null}
      {plan?.entries.length === 0 ? <p>{t('maintenance.empty')}</p> : null}
      {plan && plan.entries.length > 0 ? (
        <div className="studio-maintenance-table">
          <table className="studio-tbl">
            <thead><tr><th>{t('maintenance.select')}</th><th>{t('maintenance.path')}</th><th>{t('maintenance.size')}</th><th>{t('maintenance.state')}</th></tr></thead>
            <tbody>{plan.entries.map((entry) => (
              <tr key={entry.id}>
                <td><input type="checkbox" aria-label={entry.relative_path} disabled={busy || entry.protected}
                  checked={selected.includes(entry.id)} onChange={(event) => {
                    setReviewed(false); setResult(null)
                    setSelected((ids) => event.target.checked ? [...ids, entry.id] : ids.filter((id) => id !== entry.id))
                  }} /></td>
                <th scope="row" className="studio-mono studio-wrap-any">{entry.relative_path}</th>
                <td>{entry.size_bytes === null ? t('common.unavailable') : bytes(i18n, entry.size_bytes)}</td>
                <td>{entry.reason ? reason(entry.reason) : t(`install.txStatus.${entry.status}`)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : null}
      {reviewed && plan ? (
        <Note tone="warn">{t('maintenance.confirmNote', { count: plan.totals.selected_entries, size: bytes(i18n, plan.totals.selected_bytes) })}</Note>
      ) : null}
      <div className="studio-repo-actions">
        <button type="button" className="studio-btn" disabled={busy} onClick={onClose}>{t('common.close')}</button>
        <button type="button" className="studio-btn" disabled={busy} onClick={() => void preview()}>{t('maintenance.refresh')}</button>
        <button type="button" className="studio-btn" disabled={busy || selected.length === 0}
          onClick={() => void preview(selected, true)}>{t('maintenance.preview')}</button>
        {reviewed ? <button type="button" className="studio-btn" data-variant="danger" disabled={busy}
          onClick={() => void cleanup()}>{t('maintenance.confirm')}</button> : null}
      </div>
    </Block>
  )
}
