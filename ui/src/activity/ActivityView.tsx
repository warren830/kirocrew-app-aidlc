/**
 * The Activity page (PRD §8.1 item 5, FR-EVT-001…007).
 *
 * The one non-obvious thing here is that `GET /activity` has two modes, and they are not interchangeable
 * (§2.6):
 *
 *  - **Studio rows only** (no repo+intent in scope). Every filter runs on the server against an indexed
 *    table, there is a real cursor, and `raw` is always `null` because Studio holds no audit blocks.
 *  - **Merged intent timeline** (repo *and* intent in scope). Studio's rows for that intent are merged with
 *    the intent's AI-DLC audit events, projected live out of its shards, which is the only mode where a
 *    raw audit block exists. It has no cursor (two authorities, one of them without stable ids) and the
 *    server applies only the `source` filter.
 *
 * So in merged mode the remaining filters are applied here, in the browser, over the page that was read —
 * and the page says exactly that, with the number of events it was applied to. The alternative (pretending
 * the server filtered) would let a user conclude "nothing failed in that window" from a filter that never
 * ran, which is the class of lie this whole surface exists to avoid.
 *
 * `action type` is not client-filterable at all: it is a join against Studio's action table that the merged
 * projection does not carry, so the control is disabled with the reason rather than silently ignored.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { EmptyState } from '@kirocrew/app-sdk/ui'

import { useI18n } from '../i18n'
import { useStudioApi, type ActivityFilter } from '../lib/api'
import { ACTION_TYPES, SEVERITIES, SOURCES } from '../lib/enums.generated'
import { plural } from '../lib/format'
import { DEFAULT_SPACE, type Navigate, type StudioRoute } from '../lib/route'
import type { ActivityResponse, SettingsResponse, TimelineEntry } from '../lib/types'
import { useResource } from '../lib/useResource'
import { Icon } from '../shell/Icon'
import { EvidencePanel } from './EvidencePanel'
import { ExportPanel } from './ExportPanel'
import { Timeline } from './Timeline'
import { entryKey } from './TimelineRow'

/** One page of history. The backend clamps to its own maximum; this is the request. */
const PAGE_LIMIT = 200

interface Filters {
  source: string
  stage: string
  kind: string
  type: string
  severity: string
  /** `datetime-local` values (local wall clock, no zone), converted to ISO on the way out. */
  since: string
  until: string
}

const NO_FILTERS: Filters = { source: '', stage: '', kind: '', type: '', severity: '', since: '', until: '' }

/** `2026-09-05T22:14` → ISO 8601. Invalid input is dropped rather than sent as a filter that means nothing. */
function isoOf(local: string): string | undefined {
  if (!local) return undefined
  const ms = Date.parse(local)
  return Number.isFinite(ms) ? new Date(ms).toISOString() : undefined
}

/** The filters the browser can honestly apply to an already-read page (everything except action type). */
function applyLocally(entries: TimelineEntry[], filters: Filters): TimelineEntry[] {
  const since = filters.since ? Date.parse(filters.since) : NaN
  const until = filters.until ? Date.parse(filters.until) : NaN
  return entries.filter((entry) => {
    if (filters.source && entry.source !== filters.source) return false
    if (filters.severity && entry.severity !== filters.severity) return false
    if (filters.kind && entry.kind !== filters.kind) return false
    if (filters.stage) {
      const stage = typeof entry.params['stage'] === 'string' ? entry.params['stage'] : null
      if (stage !== filters.stage) return false
    }
    const at = Date.parse(entry.at)
    if (Number.isFinite(since) && Number.isFinite(at) && at < since) return false
    if (Number.isFinite(until) && Number.isFinite(at) && at > until) return false
    return true
  })
}

export interface ActivityViewProps {
  route: StudioRoute
  go: Navigate
}

export function ActivityView({ route, go }: ActivityViewProps) {
  const i18n = useI18n()
  const { t } = i18n
  const api = useStudioApi()
  const [filters, setFilters] = useState<Filters>(NO_FILTERS)
  /** Cursor stack: `[null, c1, c2…]`. Index = page number, so Newer is a pop, not a second fetch shape. */
  const [cursors, setCursors] = useState<(string | null)[]>([null])
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState<TimelineEntry | null>(null)

  const merged = Boolean(route.repo && route.intent)
  const cursor = cursors[page] ?? null

  // Changing the scope invalidates the paging (a cursor belongs to one query) and the drawer (its row may
  // not exist in the new scope at all).
  useEffect(() => {
    setCursors([null])
    setPage(0)
    setSelected(null)
  }, [route.repo, route.intent, route.space, filters])

  const query = useMemo<ActivityFilter>(() => {
    const base: ActivityFilter = { limit: PAGE_LIMIT }
    if (route.repo) base.repo = route.repo
    if (route.intent) {
      base.intent = route.intent
      base.space = route.space || DEFAULT_SPACE
    } else if (route.space) {
      base.space = route.space
    }
    if (filters.source) base.source = filters.source
    if (filters.stage) base.stage = filters.stage
    if (filters.kind) base.kind = filters.kind
    if (filters.type) base.type = filters.type
    if (filters.severity) base.severity = filters.severity
    const since = isoOf(filters.since)
    const until = isoOf(filters.until)
    if (since) base.since = since
    if (until) base.until = until
    if (cursor) base.cursor = cursor
    return base
  }, [route.repo, route.intent, route.space, filters, cursor])

  const key = `activity:${JSON.stringify(query)}`
  const activity = useResource<ActivityResponse>(
    key,
    useCallback((signal) => api.activity(query, { signal }), [api, query]),
    { revalidateOn: ['activity.appended', 'action.created', 'action.updated', 'reset'] },
  )

  // Only read for the export gate: whether the backend will accept `include_human_text` at all. Shares the
  // shell's `settings` resource key, so the two do not double-fetch in the same tick.
  const settings = useResource<SettingsResponse>(
    'settings',
    useCallback((signal) => api.settings({ signal }), [api]),
    { interval: 0 },
  )

  const loaded = activity.data?.items ?? []
  // Server-side filtering already ran in Studio-rows mode; re-running it locally there would be a no-op
  // that could only ever disagree with the server, so it is confined to the merged mode.
  const visible = merged ? applyLocally(loaded, filters) : loaded
  const nextCursor = activity.data?.next_cursor ?? null
  const activeFilters = Object.values(filters).filter(Boolean).length
  const selectedKey = selected ? entryKey(selected) : null

  const openAction = useCallback(
    (actionId: string) => go({ view: 'actions', action: actionId }),
    [go],
  )

  const older = () => {
    if (!nextCursor) return
    setCursors((prev) => (prev.length > page + 1 ? prev : [...prev, nextCursor]))
    setPage((n) => n + 1)
  }

  return (
    <div className="studio-scroll">
      <div className="studio-page studio-activity">
        <h1>
          <Icon name="activity" size={18} /> {t('activity.page.title')}
        </h1>
        <p className="studio-lede">{t('activity.page.lede')}</p>

        <section className="studio-filters" aria-label={t('activity.filter.title')}>
          <label className="studio-sfield">
            <span>{t('activity.filter.source')}</span>
            <select
              value={filters.source}
              onChange={(event) => setFilters((f) => ({ ...f, source: event.target.value }))}
            >
              <option value="">{t('activity.filter.allSources')}</option>
              {SOURCES.map((source) => (
                <option key={source} value={source}>
                  {t(`enum.source.${source}`)}
                </option>
              ))}
            </select>
          </label>

          <label className="studio-sfield">
            <span>{t('activity.filter.severity')}</span>
            <select
              value={filters.severity}
              onChange={(event) => setFilters((f) => ({ ...f, severity: event.target.value }))}
            >
              <option value="">{t('activity.filter.anySeverity')}</option>
              {SEVERITIES.map((severity) => (
                <option key={severity} value={severity}>
                  {t(`enum.severity.${severity}`)}
                </option>
              ))}
            </select>
          </label>

          <label className="studio-sfield">
            <span>{t('activity.filter.type')}</span>
            <select
              value={filters.type}
              disabled={merged}
              aria-describedby={merged ? 'aidlc-activity-type-note' : undefined}
              onChange={(event) => setFilters((f) => ({ ...f, type: event.target.value }))}
            >
              <option value="">{t('activity.filter.anyType')}</option>
              {ACTION_TYPES.map((type) => (
                <option key={type} value={type}>
                  {t(`enum.actionType.${type}`)}
                </option>
              ))}
            </select>
          </label>

          <label className="studio-sfield">
            <span>{t('activity.filter.stage')}</span>
            {/* A stage slug is AI-DLC's own word; free text, never a translated list. */}
            <input
              type="text"
              className="studio-mono"
              value={filters.stage}
              placeholder={t('activity.filter.anyStage')}
              onChange={(event) => setFilters((f) => ({ ...f, stage: event.target.value.trim() }))}
            />
          </label>

          <label className="studio-sfield">
            <span>{t('activity.filter.kind')}</span>
            <input
              type="text"
              className="studio-mono"
              value={filters.kind}
              placeholder={t('activity.filter.anyKind')}
              onChange={(event) => setFilters((f) => ({ ...f, kind: event.target.value.trim() }))}
            />
          </label>

          <label className="studio-sfield">
            <span>{t('activity.filter.since')}</span>
            <input
              type="datetime-local"
              value={filters.since}
              onChange={(event) => setFilters((f) => ({ ...f, since: event.target.value }))}
            />
          </label>

          <label className="studio-sfield">
            <span>{t('activity.filter.until')}</span>
            <input
              type="datetime-local"
              value={filters.until}
              onChange={(event) => setFilters((f) => ({ ...f, until: event.target.value }))}
            />
          </label>

          <div className="studio-filters-foot">
            <button
              type="button"
              className="studio-btn studio-btn-sm"
              onClick={() => setFilters(NO_FILTERS)}
              disabled={activeFilters === 0}
            >
              <Icon name="close" size={13} />
              {t('activity.filter.clear')}
            </button>
            {activeFilters > 0 ? (
              <span className="studio-muted">{plural(i18n, 'activity.filter.applied', activeFilters)}</span>
            ) : null}
          </div>
        </section>

        {merged ? null : <p className="studio-consequence">{t('activity.page.studioOnlyNote')}</p>}
        {merged ? (
          <p className="studio-consequence">
            {t('activity.page.mergedNote', { n: i18n.fmt.number(loaded.length) })}
          </p>
        ) : null}
        {merged ? (
          <p id="aidlc-activity-type-note" className="studio-consequence">
            <Icon name="info" size={13} /> {t('activity.filter.typeUnavailable')}
          </p>
        ) : null}
        {!merged && filters.source === 'aidlc' ? (
          <p className="studio-consequence" data-tone="warn">
            <Icon name="warn" size={13} /> {t('activity.page.sourceNeedsScope')}
          </p>
        ) : null}

        {/* Polite: the count changes on every poll, and this is status, not an interruption (PRD §10.3). */}
        <p className="studio-activity-count" role="status" aria-live="polite">
          {activity.loading
            ? t('activity.page.reading')
            : merged && visible.length !== loaded.length
              ? t('activity.page.narrowed', {
                  shown: i18n.fmt.number(visible.length),
                  loaded: i18n.fmt.number(loaded.length),
                })
              : plural(i18n, 'activity.page.count', visible.length)}
        </p>

        {activity.error ? (
          <div className="studio-banner" data-tone="warn" role="status">
            <Icon name="warn" size={15} />
            <span className="studio-grow">
              {t('activity.page.error', {
                message: i18n.has(`errors.${activity.error.code}`)
                  ? t(`errors.${activity.error.code}`)
                  : activity.error.message,
              })}
            </span>
            <button type="button" className="studio-btn" onClick={() => void activity.refresh()}>
              <Icon name="refresh" size={13} />
              {t('activity.page.retry')}
            </button>
          </div>
        ) : null}

        <div className="studio-activity-body">
          <div className="studio-grow">
            {visible.length === 0 && !activity.loading ? (
              <EmptyState
                icon={<Icon name="activity" size={18} />}
                title={activeFilters > 0 ? t('activity.page.empty.title') : t('activity.page.emptyScope.title')}
                subtitle={activeFilters > 0 ? t('activity.page.empty.body') : t('activity.page.emptyScope.body')}
              />
            ) : (
              <Timeline
                entries={visible}
                selectedKey={selectedKey}
                onOpenEvidence={setSelected}
                onOpenAction={openAction}
              />
            )}

            {page > 0 || nextCursor ? (
              <div className="studio-row studio-pager">
                <button
                  type="button"
                  className="studio-btn studio-btn-sm"
                  onClick={() => setPage((n) => Math.max(0, n - 1))}
                  disabled={page === 0}
                >
                  <Icon name="back" size={13} />
                  {t('activity.page.newer')}
                </button>
                <span className="studio-muted studio-mono">
                  {t('activity.page.pageNumber', { n: i18n.fmt.number(page + 1) })}
                </span>
                <button type="button" className="studio-btn studio-btn-sm" onClick={older} disabled={!nextCursor}>
                  {t('activity.page.older')}
                  <Icon name="chevron" size={13} />
                </button>
              </div>
            ) : null}

            <p className="studio-consequence">
              <Icon name="info" size={13} /> {t('activity.page.provenance')}
            </p>
            <p className="studio-consequence">
              <Icon name="lock" size={13} /> {t('activity.page.redaction')}
            </p>

            <ExportPanel
              allowHumanText={settings.data?.settings.diagnostics.export_include_human_text ?? false}
              onOpenSettings={() => go({ view: 'settings' })}
            />
          </div>

          {selected ? (
            <EvidencePanel entry={selected} onClose={() => setSelected(null)} onOpenAction={openAction} />
          ) : null}
        </div>
      </div>
    </div>
  )
}

export default ActivityView
