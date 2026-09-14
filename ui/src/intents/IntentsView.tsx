/**
 * The intent inventory across every registered repository (FR-INV-001…006).
 *
 * `GET /repos/{id}/intents` is per repository, so "All repos" is a fan-out — with `allSettled`, not `all`.
 * One unavailable repository must not blank the page: its row says which repository could not be read and
 * why, and every other repository still lists. That is the same rule as everywhere else in Studio — a fact
 * Studio cannot observe is reported as unobservable, never as absence.
 *
 * Filters are local state rather than URL parameters because §3.2 defines no query parameter for them; the
 * filtering itself happens on the server (`space`, `state`, `q`, `include_archived`) so the page and the
 * backend cannot disagree about what "archived" means.
 *
 * Recompose opens in place. It is a plan change on a running intent, so it belongs beside the row it is
 * about rather than behind a navigation that loses the list.
 *
 * The canonical-conversation panel opens the same way, and it is the only place in Studio that creates a
 * host slot (§2.13): an intent with nothing bound refuses every decision with `session_unbound`, so this
 * list — the page a user reaches a waiting intent from — is where the remedy has to be reachable. One
 * panel at a time, because both are about one row and two open at once would hide the row they name.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { useShellData } from '../shell/StudioApp'
import { useI18n } from '../i18n'
import { StudioApiError, type StudioApi } from '../lib/api'
import { INTENT_STATES } from '../lib/enums.generated'
import { plural } from '../lib/format'
import { useIsNarrow } from '../lib/host'
import type { Navigate, StudioRoute } from '../lib/route'
import type { IntentSummary, RepoRecord } from '../lib/types'
import { useResource } from '../lib/useResource'
import { RecomposePanel } from '../plan/RecomposePanel'
import { IntentSettingsPanel } from '../plan/IntentSettingsPanel'
import { IntentActions } from './IntentActions'
import { IntentList } from './IntentList'
import { SessionPanel } from './SessionPanel'

interface Failure {
  repo: string
  message: string
}

interface Inventory {
  intents: IntentSummary[]
  spaces: string[]
  failures: Failure[]
}

interface Filters {
  space: string
  state: string
  q: string
  archived: boolean
}

const NO_FILTERS: Filters = { space: '', state: '', q: '', archived: false }

/** Read every listed repository, keeping the ones that failed as reportable facts. */
export async function readInventory(
  api: StudioApi,
  repos: RepoRecord[],
  filters: Filters,
  signal: AbortSignal,
  message: (error: StudioApiError) => string,
): Promise<Inventory> {
  const answers = await Promise.allSettled(
    repos.map((repo) =>
      api.intents(
        repo.repo_id,
        {
          ...(filters.space ? { space: filters.space } : {}),
          ...(filters.state ? { state: filters.state } : {}),
          ...(filters.q ? { q: filters.q } : {}),
          ...(filters.archived ? { include_archived: true } : {}),
        },
        { signal },
      ),
    ),
  )
  const intents: IntentSummary[] = []
  const spaces = new Set<string>()
  const failures: Failure[] = []
  answers.forEach((answer, index) => {
    const repo = repos[index]
    if (!repo) return
    if (answer.status === 'fulfilled') {
      intents.push(...answer.value.intents)
      for (const space of answer.value.spaces) spaces.add(space)
    } else {
      const error = answer.reason
      failures.push({
        repo: repo.label,
        message: error instanceof StudioApiError ? message(error) : String(error),
      })
    }
  })
  return { intents, spaces: [...spaces].sort(), failures }
}

export interface IntentsViewProps {
  route: StudioRoute
  go: Navigate
}

export function IntentsView({ route, go }: IntentsViewProps) {
  const i18n = useI18n()
  const { t } = i18n
  const { api, repos: reposResource } = useShellData()
  const narrow = useIsNarrow()
  const [filters, setFilters] = useState<Filters>(NO_FILTERS)
  // The typed needle is held apart from the applied one: `GET …/intents` snapshots every intent in the
  // repository, so sending one request per keystroke per repository would make typing the most expensive
  // thing in Studio.
  const [needle, setNeedle] = useState('')
  const [notice, setNotice] = useState<{ text: string; actionId?: string } | null>(null)
  const [recomposing, setRecomposing] = useState<IntentSummary | null>(null)
  const [binding, setBinding] = useState<IntentSummary | null>(null)
  const [editingSettings, setEditingSettings] = useState<IntentSummary | null>(null)
  useEffect(() => { setEditingSettings(null) }, [route.repo])

  useEffect(() => {
    const timer = setTimeout(() => setFilters((current) => (current.q === needle ? current : { ...current, q: needle })), 300)
    return () => clearTimeout(timer)
  }, [needle])

  const allRepos = reposResource.data?.repos ?? []
  // The scope bar's selection is the page's scope; with no selection every registered repository is read.
  const targets = useMemo(
    () => (route.repo ? allRepos.filter((repo) => repo.repo_id === route.repo) : allRepos),
    [allRepos, route.repo],
  )
  const message = useCallback(
    (error: StudioApiError) => (error.known ? t(`errors.${error.code}`) : error.message),
    [t],
  )
  const key = targets.length
    ? `intents:${targets.map((repo) => repo.repo_id).join('+')}:${filters.space}:${filters.state}:${filters.q}:${filters.archived ? 1 : 0}`
    : null

  const inventory = useResource<Inventory>(
    key,
    useCallback(
      (signal) => readInventory(api, targets, filters, signal, message),
      [api, targets, filters, message],
    ),
    {
      busy: (data) =>
        data.intents.some((intent) => intent.operational_state === 'Running' || intent.session?.running === true),
    },
  )

  const data = inventory.data
  const intents = data?.intents ?? []
  const total = allRepos.reduce((sum, repo) => sum + repo.counts.intents, 0)
  const states = useMemo(
    () => INTENT_STATES.filter((state) => intents.some((intent) => intent.operational_state === state)),
    [intents],
  )
  const filtered = filters.space !== '' || filters.state !== '' || filters.q !== '' || filters.archived

  const refresh = inventory.refresh
  const renderActions = useCallback(
    (intent: IntentSummary) => (
      <IntentActions
        api={api}
        intent={intent}
        onGo={(patch) => go(patch.view === 'actions'
          // The row opens this intent's queue, not the decision left selected on another visit.
          ? { action: '', artifact: '', tab: 'decision', stage: '', unit: '', anchor: '', ...patch }
          : patch)}
        onQueued={(actionId) => setNotice({ text: t('intents.run.queued'), actionId })}
        onChanged={(text) => {
          if (text) setNotice({ text })
          void refresh()
        }}
        onRecompose={(target) => {
          setNotice(null)
          setBinding(null)
          setEditingSettings(null)
          setRecomposing(target)
        }}
        onSession={(target) => {
          setNotice(null)
          setRecomposing(null)
          setEditingSettings(null)
          setBinding(target)
        }}
        onSettings={(target) => {
          setNotice(null)
          setRecomposing(null)
          setBinding(null)
          setEditingSettings(target)
        }}
      />
    ),
    [api, go, refresh, t],
  )

  // The panel is opened from a row, so the row's repository has to be found again: the slot's project
  // must equal `canonical_path`, and `IntentSummary` carries only the label. The row itself is re-read
  // from the current inventory, so a refresh behind the open panel updates it instead of pinning the
  // snapshot it was opened from.
  const bindingRepo = binding ? allRepos.find((repo) => repo.repo_id === binding.repo_id) : undefined
  const bindingRow = binding
    ? (intents.find(
        (intent) => intent.repo_id === binding.repo_id && intent.intent_key === binding.intent_key,
      ) ?? binding)
    : null

  return (
    <div className="studio-scroll">
      <div className="studio-page">
        <div className="studio-spread studio-wrap">
          <h1>{t('intents.title')}</h1>
          <button type="button" className="studio-btn" onClick={() => go({ view: 'new-intent' })}>
            <Icon name="plus" size={13} />
            {t('intents.newIntent')}
          </button>
        </div>
        <p className="studio-lede">{t('intents.lede')}</p>

        <div className="studio-row studio-wrap studio-filters" role="group" aria-label={t('intents.filters')}>
          <label className="studio-filter">
            <span>{t('intents.filter.state')}</span>
            <select value={filters.state} onChange={(event) => setFilters((f) => ({ ...f, state: event.target.value }))}>
              <option value="">{t('intents.filter.stateAll')}</option>
              {states.map((state) => (
                <option key={state} value={state}>
                  {t(`enum.intentState.${state}`)}
                </option>
              ))}
            </select>
          </label>

          {(data?.spaces.length ?? 0) > 1 ? (
            <label className="studio-filter">
              <span>{t('intents.filter.space')}</span>
              <select value={filters.space} onChange={(event) => setFilters((f) => ({ ...f, space: event.target.value }))}>
                <option value="">{t('intents.filter.spaceAll')}</option>
                {(data?.spaces ?? []).map((space) => (
                  <option key={space} value={space}>
                    {space}
                  </option>
                ))}
              </select>
            </label>
          ) : null}

          <label className="studio-filter">
            <span>{t('intents.filter.search')}</span>
            <input
              type="search"
              value={needle}
              placeholder={t('intents.filter.searchPlaceholder')}
              onChange={(event) => setNeedle(event.target.value)}
            />
          </label>

          <label className="studio-filter studio-filter-check">
            <input
              type="checkbox"
              checked={filters.archived}
              onChange={(event) => setFilters((f) => ({ ...f, archived: event.target.checked }))}
            />
            <span>{t('intents.filter.archived')}</span>
          </label>

          <Chip mono>{t('intents.showing', { visible: i18n.fmt.number(intents.length), total: i18n.fmt.number(total) })}</Chip>
          {inventory.stale ? <span className="studio-muted">{t('common.loading')}</span> : null}
        </div>

        {notice ? (
          // Polite: creating a command card is a status, not an emergency, and the queue is where it lands.
          <p className="studio-banner" data-tone="info" role="status">
            <Icon name="info" size={15} />
            <span className="studio-grow">{notice.text}</span>
            {notice.actionId ? (
              <button
                type="button"
                className="studio-btn"
                onClick={() => go({ view: 'actions', action: notice.actionId ?? '' })}
              >
                {t('intents.run.open')}
              </button>
            ) : null}
            <button type="button" className="studio-btn" onClick={() => setNotice(null)}>
              {t('intents.run.dismiss')}
            </button>
          </p>
        ) : null}

        {inventory.error ? (
          <p className="studio-banner" data-tone="danger" role="alert">
            <Icon name="warn" size={15} />
            <span className="studio-grow">
              {t('intents.error.title')} {message(inventory.error)}
            </span>
            <button type="button" className="studio-btn" onClick={() => void inventory.refresh()}>
              {t('common.retry')}
            </button>
          </p>
        ) : null}

        {bindingRow && bindingRepo ? (
          <SessionPanel
            api={api}
            repo={bindingRepo}
            intent={bindingRow}
            onClose={() => setBinding(null)}
            onChanged={(text) => {
              if (text) setNotice({ text })
              void refresh()
            }}
          />
        ) : null}

        {recomposing ? (
          <RecomposePanel
            api={api}
            repoId={recomposing.repo_id}
            intentKey={recomposing.intent_key}
            intentLabel={recomposing.intent_dir}
            onClose={() => setRecomposing(null)}
            onApplied={() => void inventory.refresh()}
          />
        ) : null}

        {editingSettings ? (
          <IntentSettingsPanel api={api} repoId={editingSettings.repo_id}
            intentKey={editingSettings.intent_key} intentLabel={editingSettings.intent_dir}
            onClose={() => setEditingSettings(null)} onApplied={() => void inventory.refresh()} />
        ) : null}

        {allRepos.length === 0 ? (
          <Empty
            title={t('intents.emptyNoRepo.title')}
            body={t('intents.emptyNoRepo.body')}
            action={{ label: t('intents.emptyNoRepo.open'), run: () => go({ view: 'repos' }) }}
          />
        ) : inventory.loading ? (
          <p className="studio-muted">{t('common.loading')}</p>
        ) : intents.length === 0 && (data?.failures.length ?? 0) === 0 ? (
          filtered ? (
            <Empty
              title={t('intents.emptyFiltered.title')}
              body={t('intents.emptyFiltered.body')}
              action={{
                label: t('intents.emptyFiltered.clear'),
                run: () => {
                  setNeedle('')
                  setFilters(NO_FILTERS)
                },
              }}
            />
          ) : (
            <Empty
              title={t('intents.empty.title')}
              body={t('intents.empty.body')}
              action={{ label: t('intents.newIntent'), run: () => go({ view: 'new-intent' }) }}
            />
          )
        ) : (
          <IntentList
            intents={intents}
            failures={data?.failures ?? []}
            narrow={narrow}
            renderActions={renderActions}
          />
        )}

        {intents.length > 0 ? (
          <p className="studio-consequence">
            <Icon name="lock" size={13} />
            <span>{t('intents.keepMoving.why')}</span>
          </p>
        ) : null}

        <p className="studio-sr" role="status">
          {plural(i18n, 'intents.count', intents.length)}
        </p>
      </div>
    </div>
  )
}

/** An empty state that names the next useful act, never just "no data" (PRD §10.2). */
function Empty({ title, body, action }: { title: string; body: string; action: { label: string; run: () => void } }) {
  return (
    <div className="studio-empty">
      <Icon name="intent" size={18} />
      <p className="studio-empty-title">{title}</p>
      <p className="studio-muted">{body}</p>
      <button type="button" className="studio-btn" onClick={action.run}>
        {action.label}
      </button>
    </div>
  )
}

export default IntentsView
