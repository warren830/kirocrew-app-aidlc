/**
 * The model and reasoning-effort pickers, over the host's own lists.
 *
 * Both lists are the host's (`GET /api/models`, `GET /api/effort-levels`), never Studio's: the models differ
 * per signed-in account, so a list baked in here would offer ids the person's account cannot run. The first
 * option is always "inherit" (`""`): it sends nothing, so the host's own default decides, which is
 * what every conversation and Advisor draft ran on before these pickers existed.
 *
 * A saved pick the host no longer lists stays selectable and is labelled that way. Dropping it would show
 * "inherit" while the saved value still applies, so the person would believe they were on the default.
 */

import { useCallback } from 'react'

import { useI18n } from '../i18n'
import type { StudioApi } from '../lib/api'
import type { HostModel } from '../lib/types'
import { useResource } from '../lib/useResource'

/**
 * Read once per view: the catalogue changes with the account, not while a page is open. Only a stream reset
 * re-reads it (`[]` would mean every event, and the host may start kiro-cli to answer).
 */
export function useHostModels(api: StudioApi, enabled = true) {
  return useResource<HostModel[]>(
    enabled ? 'host:models' : null,
    useCallback((signal) => api.listModels({ signal }), [api]),
    { interval: 0, revalidateOn: ['reset'] },
  )
}

export function useEffortLevels(api: StudioApi, enabled = true) {
  return useResource<string[]>(
    enabled ? 'host:effort-levels' : null,
    useCallback((signal) => api.listEffortLevels({ signal }), [api]),
    { interval: 0, revalidateOn: ['reset'] },
  )
}

/** `claude-sonnet-5.5 · 1.3×`: the id the host takes back, then its relative cost when the host reports one. */
export function modelLabel(model: HostModel): string {
  const name = model.model_name || model.model_id
  const rate = model.rate_multiplier
  return typeof rate === 'number' && Number.isFinite(rate) ? `${name} · ${rate}×` : name
}

/** Only well-formed rows: an id is what goes back to the host, so a row without one cannot be picked. */
function usable(models: HostModel[] | null): HostModel[] {
  return Array.isArray(models) ? models.filter((model) => typeof model?.model_id === 'string' && model.model_id) : []
}

export interface ModelSelectProps {
  value: string
  onChange: (value: string) => void
  models: HostModel[] | null
  /** True while the first read is in flight. */
  loading: boolean
  /** The host could not list its models; only "inherit" and a saved pick remain on offer. */
  failed: boolean
  /** What "inherit" means here: whose default applies is different for a conversation and for the Advisor. */
  inheritLabel: string
  label: string
  disabled?: boolean
  describedBy?: string
  /** Show `label` as text beside the select instead of only to a screen reader. */
  visibleLabel?: boolean
}

export function ModelSelect({
  value, onChange, models, loading, failed, inheritLabel, label, disabled, describedBy, visibleLabel,
}: ModelSelectProps) {
  const { t } = useI18n()
  const listed = usable(models)
  const missing = value !== '' && !listed.some((model) => model.model_id === value)
  return (
    <label className={visibleLabel ? 'studio-field' : 'studio-sfield'}>
      <span className={visibleLabel ? 'studio-field-label' : 'studio-sr'}>{label}</span>
      <select
        className="studio-input"
        value={value}
        disabled={disabled}
        aria-describedby={describedBy}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{inheritLabel}</option>
        {missing ? <option value={value}>{t('model.notListed', { model: value })}</option> : null}
        {listed.map((model) => (
          <option key={model.model_id} value={model.model_id} title={model.description ?? undefined}>
            {modelLabel(model)}
          </option>
        ))}
        {loading && listed.length === 0 ? <option value="" disabled>{t('common.loading')}</option> : null}
        {failed && listed.length === 0 ? <option value="" disabled>{t('model.unavailable')}</option> : null}
      </select>
    </label>
  )
}

export interface EffortSelectProps {
  value: string
  onChange: (value: string) => void
  levels: string[] | null
  label: string
  disabled?: boolean
}

export function EffortSelect({ value, onChange, levels, label, disabled }: EffortSelectProps) {
  const i18n = useI18n()
  const { t } = i18n
  const listed = Array.isArray(levels) ? levels.filter((level) => typeof level === 'string' && level) : []
  const missing = value !== '' && !listed.includes(value)
  // A level Studio has no word for is shown as the host spelled it, never dropped.
  const word = (level: string) => (i18n.has(`model.effort.${level}`) ? t(`model.effort.${level}`) : level)
  return (
    <label className="studio-field">
      <span className="studio-field-label">{label}</span>
      <select className="studio-input" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
        <option value="">{t('model.effort.inherit')}</option>
        {missing ? <option value={value}>{word(value)}</option> : null}
        {listed.map((level) => (
          <option key={level} value={level}>{word(level)}</option>
        ))}
      </select>
    </label>
  )
}
