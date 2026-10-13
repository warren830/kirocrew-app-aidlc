/**
 * The Repos page: the registry, one repository in detail, and the three transactional flows.
 *
 * Layout follows the route rather than local state, so a link restores what the user was looking at
 * (PRD §10.2): `?view=repos` is the list, `&repo=r_…` is that repository, `&tx=…` is the transaction
 * drawer over it. Local state holds only what a URL should not carry — an open modal, an in-flight
 * button, the last refusal.
 *
 * Registration, installation, upgrade and recovery are desktop-only (PRD §8.4). Below 900px this page
 * says so, in words, and does not render a path picker that cannot work: the alternative is a modal
 * whose input is unusable and whose Register button is refused by the server. Everything already
 * registered — health, versions, intents, queue, Git, remediation — stays readable at every width.
 *
 * The breakpoint is decided here in JS and nowhere in CSS. A stylesheet that also hid these controls
 * would be a second breakpoint to keep in step, and the window between the two would be a page with a
 * button that opens a dialog the CSS has already hidden.
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'

import { useI18n } from '../i18n'
import { SpaceControls } from '../intents/SpaceControls'
import { StudioApiError } from '../lib/api'
import { EMPTY_ROUTE } from '../lib/route'
import type { RepoDetailResponse, RepoRecord, ReposResponse } from '../lib/types'
import { useResource } from '../lib/useResource'
import { Icon } from '../shell/Icon'
import { useShellData } from '../shell/StudioApp'
import type { ViewProps } from '../shell/ViewRouter'
import { AddRepoDialog } from './AddRepoDialog'
import { DoctorResult, type DoctorFeedback } from './DoctorResult'
import { GitPanel } from './GitPanel'
import { InstallPreview, PreviewFlow } from './InstallPreview'
import { Block, ErrorNote, Note } from './PreflightReport'
import { RecoveryPanel, recoveryEvidencePath } from './RecoveryPanel'
import { RepoCard, type RepoAction } from './RepoCard'
import { RepoList } from './RepoList'
import { TransactionDrawer } from './TransactionDrawer'
import { MaintenancePanel } from './MaintenancePanel'
import { UpgradePreview } from './UpgradePreview'

/** PRD §8.4's boundary: below this, path selection and installation are not offered. */
const DESKTOP_MIN_PX = 900
const DESKTOP_QUERY = `(min-width: ${DESKTOP_MIN_PX}px)`

/**
 * Is the viewport wide enough for the desktop-only flows?
 *
 * Local to this area rather than added to `lib/host.ts`: `useIsNarrow()` there is the host's 767px
 * mobile breakpoint, and PRD §8.4 draws this line at 900px. Two different questions, two different
 * queries — reusing the 767px one would offer a path picker at 800px, which is exactly the width the
 * PRD calls desktop-only.
 */
function useDesktop(): boolean {
  const subscribe = useCallback((onChange: () => void) => {
    if (typeof window.matchMedia !== 'function') return () => {}
    const mq = window.matchMedia(DESKTOP_QUERY)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return useSyncExternalStore(
    subscribe,
    // No `matchMedia` (jsdom without the shim) answers "desktop": a test environment that cannot measure
    // the viewport must not silently take away the flows this page is mostly about.
    () => (typeof window.matchMedia === 'function' ? window.matchMedia(DESKTOP_QUERY).matches : true),
    () => true,
  )
}

type Flow = 'none' | 'add' | 'install' | 'upgrade' | 'recover' | 'rebind' | 'remove' | 'rename' | 'archive' | 'uninstall' | 'cleanup' | 'rollback'

/**
 * An open flow, and the repository it was opened for.
 *
 * The pairing is the point. Opening a flow from a table row also *selects* that repository, and an
 * effect that closed the flow whenever `route.repo` changed would close the flow the same click just
 * opened. Deriving "is this flow still the right one" from the pair instead means there is no ordering
 * to get wrong, and a flow can never end up aimed at a repository the user has navigated away from.
 */
interface FlowState {
  kind: Flow
  repoId: string
}
const NO_FLOW: FlowState = { kind: 'none', repoId: '' }

/**
 * Where focus goes when a confirmation opens: its first field, else its heading.
 *
 * Never its confirm button: a confirmation that opens with its primary action focused can be completed by
 * the keystroke that opened it (the Action Center's ConfirmPanel makes the same choice).
 */
function flowFocusTarget(region: HTMLElement): HTMLElement | null {
  const field = region.querySelector<HTMLElement>('input:not(:disabled), select:not(:disabled), textarea:not(:disabled)')
  if (field) return field
  const heading = region.querySelector<HTMLElement>('h2, h3')
  // Focusable from script only: -1 adds no tab stop.
  if (heading) heading.tabIndex = -1
  return heading
}

export function ReposView({ route, go }: ViewProps) {
  const i18n = useI18n()
  const { t } = i18n
  const { api, repos } = useShellData()
  const desktop = useDesktop()

  const [flowState, setFlowState] = useState<FlowState>(NO_FLOW)
  const [busy, setBusy] = useState<RepoAction | null>(null)
  const [error, setError] = useState<StudioApiError | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [doctorRun, setDoctorRun] = useState<{ repoId: string; feedback: DoctorFeedback } | null>(null)
  const doctorRequest = useRef(0)
  const [rebindPath, setRebindPath] = useState('')
  const [repoLabel, setRepoLabel] = useState('')
  const [showArchived, setShowArchived] = useState(false)
  const [spacesRepo, setSpacesRepo] = useState('')
  const allRepos = useResource<ReposResponse>(
    showArchived ? 'repos-with-archived' : null,
    useCallback((signal) => api.repos(true, { signal }), [api]),
    { revalidateOn: ['repo.updated', 'repo.removed', 'reset'] },
  )

  const detail = useResource<RepoDetailResponse>(
    route.repo ? `repo-detail:${route.repo}` : null,
    useCallback((signal) => api.repo(route.repo, { signal }), [api, route.repo]),
    { revalidateOn: ['repo.updated', 'repo.removed', 'transaction.updated', 'intent.updated', 'reset'] },
  )

  const listing = showArchived ? allRepos.data : repos.data
  const list = listing?.repos ?? []
  // The detail read is authoritative (it is freshly observed, with Git and intents); the list entry is
  // the fallback while it is in flight, so switching repositories does not blank the header.
  const selected: RepoRecord | null =
    detail.data?.repo ?? list.find((repo) => repo.repo_id === route.repo) ?? null
  const transactions = detail.data?.transactions ?? []
  const doctorFeedback = doctorRun?.repoId === selected?.repo_id ? doctorRun?.feedback : null

  // `add` is not about one repository; every other flow is only current while its repository is selected,
  // and losing the width closes all of them rather than leaving a modal with no room for its content.
  const flow: Flow =
    !desktop || flowState.kind === 'none'
      ? 'none'
      : flowState.kind === 'add' || flowState.repoId === route.repo
        ? flowState.kind
        : 'none'
  const closeFlow = useCallback(() => setFlowState(NO_FLOW), [])

  // A confirmation renders above the card, and the button that opens it usually sits in Maintenance at
  // the card's foot, a screen or more below: opened in place, the click looks like it did nothing. So
  // opening one moves focus into it (which scrolls it into view), and Cancel hands focus back.
  const flowRegion = useRef<HTMLDivElement | null>(null)
  const flowOpener = useRef<HTMLElement | null>(null)
  const returnFocusTo = useRef<HTMLElement | null>(null)
  const openFlow = useCallback((kind: Flow, repoId: string) => {
    flowOpener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setFlowState({ kind, repoId })
  }, [])
  // Only Cancel returns focus. A flow that ends by moving on (a started transaction, an archived or
  // removed repository) closes with `closeFlow`, and pulling focus back down would hide where it went.
  const cancelFlow = useCallback(() => {
    returnFocusTo.current = flowOpener.current
    setFlowState(NO_FLOW)
  }, [])

  useEffect(() => {
    if (flow === 'none') {
      const opener = returnFocusTo.current
      returnFocusTo.current = null
      // Install's own button is not rendered while its preview is open, so the one to return to is the
      // button that replaced it.
      const action = opener?.dataset['repoAction']
      const target = opener?.isConnected
        ? opener
        : action ? document.querySelector<HTMLElement>(`[data-repo-action="${action}"]`) : null
      target?.focus()
      return
    }
    // The add dialog is modal and manages its own focus.
    if (flow !== 'add' && flowRegion.current) flowFocusTarget(flowRegion.current)?.focus()
    // `flowState` as well as `flow`: pressing the button of a confirmation that is already open, and has
    // been scrolled away from, has to bring it back too.
  }, [flow, flowState])

  // Selecting another repository drops the previous one's in-flight button and its last refusal, which
  // otherwise read as belonging to the repository now on screen.
  useEffect(() => {
    setBusy(null)
    setError(null)
    setNotice(null)
    setSpacesRepo('')
    setDoctorRun(null)
    doctorRequest.current += 1
    return () => { doctorRequest.current += 1 }
  }, [route.repo])

  const refresh = useCallback(() => {
    void repos.refresh()
    if (showArchived) void allRepos.refresh()
    if (route.repo) void detail.refresh()
  }, [repos, allRepos, showArchived, detail, route.repo])

  const fail = useCallback((caught: unknown) => {
    setError(
      caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0),
    )
  }, [])

  const runAction = useCallback(
    async (repoId: string, action: RepoAction) => {
      setError(null)
      setNotice(null)
      switch (action) {
        case 'open':
          go({ repo: repoId, tx: '' })
          return
        case 'queue':
          go({ view: 'actions', repo: repoId })
          return
        case 'intents':
          go({ view: 'intents', repo: repoId })
          return
        case 'new-intent':
          go({ ...EMPTY_ROUTE, view: 'new-intent', repo: repoId })
          return
        case 'install':
        case 'upgrade':
        case 'recover':
        case 'uninstall':
        case 'cleanup':
        case 'rollback':
          go({ repo: repoId, tx: '' })
          openFlow(action, repoId)
          return
        case 'rebind':
          go({ repo: repoId })
          setRebindPath('')
          openFlow('rebind', repoId)
          return
        case 'remove':
          go({ repo: repoId })
          openFlow('remove', repoId)
          return
        case 'rename':
          go({ repo: repoId })
          setRepoLabel(list.find((repo) => repo.repo_id === repoId)?.label ?? selected?.label ?? '')
          openFlow('rename', repoId)
          return
        case 'archive':
          go({ repo: repoId })
          openFlow('archive', repoId)
          return
        case 'unarchive':
          setBusy('unarchive')
          try {
            await api.updateRepoMetadata(repoId, { archived: false })
            refresh()
          } catch (caught) { fail(caught) } finally { setBusy(null) }
          return
        case 'rescan':
          setBusy('rescan')
          try {
            await api.rescanRepo(repoId)
            refresh()
          } catch (caught) {
            fail(caught)
          } finally {
            setBusy(null)
          }
          return
        case 'doctor': {
          const request = ++doctorRequest.current
          setDoctorRun({ repoId, feedback: { status: 'running' } })
          try {
            const answer = await api.doctorRepo(repoId)
            if (doctorRequest.current !== request) return
            setDoctorRun({ repoId, feedback: { status: 'completed', result: answer.result } })
            refresh()
          } catch (caught) {
            if (doctorRequest.current !== request) return
            const error = caught instanceof StudioApiError
              ? caught
              : new StudioApiError('internal_error', String(caught), {}, 0)
            setDoctorRun({ repoId, feedback: { status: 'failed', error } })
          }
          return
        }
      }
    },
    [api, go, refresh, fail, t, i18n, list, selected, openFlow],
  )

  const saveMetadata = async () => {
    if (!selected || (flow !== 'rename' && flow !== 'archive')) return
    setBusy(flow)
    try {
      await api.updateRepoMetadata(selected.repo_id,
        flow === 'rename' ? { label: repoLabel.trim() } : { archived: true })
      closeFlow()
      refresh()
      if (flow === 'archive') go({ repo: '', tx: '' })
    } catch (caught) { fail(caught) } finally { setBusy(null) }
  }

  const confirmRebind = useCallback(async () => {
    if (!selected) return
    const trimmed = rebindPath.trim()
    if (!trimmed.startsWith('/') && !trimmed.startsWith('~')) {
      setError(new StudioApiError('bad_path', t('repos.add.pathNotAbsolute'), {}, 400))
      return
    }
    setBusy('rebind')
    try {
      await api.rebindRepo(selected.repo_id, trimmed)
      closeFlow()
      refresh()
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(null)
    }
  }, [api, selected, rebindPath, refresh, fail, closeFlow, t])

  const confirmRemove = useCallback(async () => {
    if (!selected) return
    setBusy('remove')
    try {
      await api.removeRepo(selected.repo_id)
      closeFlow()
      go({ repo: '', tx: '' })
      void repos.refresh()
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(null)
    }
  }, [api, selected, go, repos, fail, closeFlow])

  const onStarted = useCallback(
    (transactionId: string) => {
      closeFlow()
      go({ tx: transactionId })
      refresh()
    },
    [closeFlow, go, refresh],
  )

  return (
    <div className="studio-scroll">
      <div className="studio-page">
        <div className="studio-spread">
          <h1>{t('repos.title')}</h1>
          {desktop ? (
            <button
              type="button"
              className="studio-btn"
              data-variant="primary"
              onClick={() => setFlowState({ kind: 'add', repoId: '' })}
            >
              <Icon name="plus" size={14} />
              {t('repos.add.open')}
            </button>
          ) : null}
        </div>
        <p className="studio-lede">{t('repos.lede')}</p>
        <label className="studio-check">
          <input type="checkbox" checked={showArchived}
            onChange={(event) => setShowArchived(event.target.checked)} />
          {t('repos.metadata.showArchived')}
        </label>
        {showArchived && allRepos.error ? <ErrorNote error={allRepos.error} /> : null}

        {desktop ? null : (
          <div className="studio-failure-detail studio-desktop-only" data-tone="info">
            <p className="studio-row studio-strong">
              <Icon name="info" size={15} />
              {t('repos.desktopOnly.title')}
            </p>
            <p>{t('repos.desktopOnly.body')}</p>
          </div>
        )}

        {listing ? (
          <p className="studio-help studio-mono">
            {t('repos.totals', {
              repos: i18n.fmt.number(listing.totals.repos),
              unavailable: i18n.fmt.number(listing.totals.unavailable),
              open: i18n.fmt.number(listing.totals.open_actions),
            })}
          </p>
        ) : null}

        {repos.error ? <ErrorNote error={repos.error} /> : null}
        {/* A failed detail read is why the page shows the list instead of the repository the link named
            (a `repo_not_found` from a stale deep link is the common one), so it has to be said. */}
        {detail.error ? <ErrorNote error={detail.error} /> : null}
        {error ? <ErrorNote error={error} reassure={t('repos.error.unchanged')} /> : null}
        {notice ? (
          <p className="studio-note" role="status">
            <Icon name="check" size={13} /> {notice}
          </p>
        ) : null}

        {route.repo && selected ? (
          <>
            <div className="studio-repo-actions">
              {/* Back to every repository drops what was scoped to this one, as the scope selector does: an
                  intent left in the route would stay in the breadcrumb with no repository before it. */}
              <button type="button" className="studio-btn" onClick={() => go({
                repo: '', tx: '', action: '', intent: '', space: '', stage: '', unit: '', artifact: '' })}>
                <Icon name="back" size={14} />
                {t('repos.detail.back')}
              </button>
            </div>

            {route.tx ? (
              <TransactionDrawer
                api={api}
                repoId={selected.repo_id}
                repoLabel={selected.label}
                transactionId={route.tx}
                onClose={() => go({ tx: '' })}
                onSettled={refresh}
              />
            ) : null}

            {/* One region for whichever confirmation is open, so opening one knows where to put focus. */}
            <div ref={flowRegion}>
              {flow === 'install' ? (
                <InstallPreview
                  api={api}
                  repoId={selected.repo_id}
                  bundledVersion={selected.install.bundled_engine_version}
                  onStarted={onStarted}
                  onClose={cancelFlow}
                />
              ) : null}
              {flow === 'upgrade' ? (
                <UpgradePreview
                  api={api}
                  repoId={selected.repo_id}
                  install={selected.install}
                  onStarted={onStarted}
                  onClose={cancelFlow}
                />
              ) : null}
              {flow === 'uninstall' ? (
                <PreviewFlow api={api} repoId={selected.repo_id} kind="uninstall"
                  onStarted={onStarted} onClose={cancelFlow} />
              ) : null}
              {flow === 'cleanup' ? (
                <MaintenancePanel key={selected.repo_id} api={api} repoId={selected.repo_id}
                  onClose={cancelFlow} onChanged={refresh} />
              ) : null}
              {flow === 'rollback' ? (
                <PreviewFlow api={api} repoId={selected.repo_id} kind="rollback"
                  targetVersion={selected.install.rollback_target?.engine_version}
                  onStarted={onStarted} onClose={cancelFlow} />
              ) : null}
              {flow === 'recover' ? (
                <RecoveryPanel
                  api={api}
                  repoId={selected.repo_id}
                  repoLabel={selected.label}
                  failedDir={recoveryEvidencePath(transactions)}
                  onStarted={onStarted}
                  onClose={cancelFlow}
                />
              ) : null}

              {flow === 'rebind' ? (
                <Block title={t('repos.rebind.title', { label: selected.label })} icon="link">
                  <Note>{t('repos.rebind.body')}</Note>
                  <div className="studio-field">
                    <label htmlFor="studio-rebind-path">{t('repos.add.pathLabel')}</label>
                    <input
                      id="studio-rebind-path"
                      className="studio-input studio-mono"
                      type="text"
                      spellCheck={false}
                      autoComplete="off"
                      value={rebindPath}
                      onChange={(event) => setRebindPath(event.target.value)}
                    />
                  </div>
                  <div className="studio-repo-actions">
                    <button type="button" className="studio-btn" onClick={cancelFlow}>
                      {t('install.cancel')}
                    </button>
                    <button
                      type="button"
                      className="studio-btn"
                      data-variant="primary"
                      disabled={busy !== null || rebindPath.trim() === ''}
                      onClick={() => void confirmRebind()}
                    >
                      {t('repos.rebind.confirm')}
                    </button>
                  </div>
                </Block>
              ) : null}

              {flow === 'remove' ? (
                <Block title={t('repos.remove.title', { label: selected.label })} icon="close">
                  <Note>{t('repos.remove.body')}</Note>
                  <div className="studio-repo-actions">
                    <button type="button" className="studio-btn" onClick={cancelFlow}>
                      {t('install.cancel')}
                    </button>
                    <button
                      type="button"
                      className="studio-btn"
                      data-variant="danger"
                      disabled={busy !== null}
                      onClick={() => void confirmRemove()}
                    >
                      {t('repos.remove.confirm')}
                    </button>
                  </div>
                </Block>
              ) : null}

              {flow === 'rename' || flow === 'archive' ? (
                <Block title={t(flow === 'rename' ? 'repos.action.rename' : 'repos.action.archive')} icon="repo">
                  {flow === 'rename' ? (
                    <div className="studio-field">
                      <label htmlFor="studio-repo-label">{t('repos.metadata.label')}</label>
                      <input id="studio-repo-label" className="studio-input" value={repoLabel}
                        onChange={(event) => setRepoLabel(event.target.value)} />
                    </div>
                  ) : <Note>{t('repos.metadata.archiveBody')}</Note>}
                  <div className="studio-repo-actions">
                    <button type="button" className="studio-btn" onClick={cancelFlow}>{t('install.cancel')}</button>
                    <button type="button" className="studio-btn" data-variant="primary"
                      disabled={busy !== null || (flow === 'rename' && !repoLabel.trim())}
                      onClick={() => void saveMetadata()}>
                      {t(flow === 'rename' ? 'repos.metadata.save' : 'repos.action.archive')}
                    </button>
                  </div>
                </Block>
              ) : null}
            </div>

            <RepoCard
              repo={selected}
              detailed
              desktop={desktop}
              installPreviewOpen={flow === 'install'}
              busy={busy ?? (doctorFeedback?.status === 'running' ? 'doctor' : null)}
              maintenanceFeedback={doctorFeedback ? <DoctorResult feedback={doctorFeedback} /> : null}
              transactions={transactions}
              intents={detail.data?.intents ?? []}
              onAction={(action) => void runAction(selected.repo_id, action)}
              onOpenTransaction={(id) => go({ tx: id })}
            >
              <GitPanel api={api} repoId={selected.repo_id} enabled={selected.availability === 'available'} />
              {desktop && !selected.archived && selected.install.engine_dir && selected.availability === 'available' ? (
                <details key={selected.repo_id}
                  onToggle={(event) => setSpacesRepo(event.currentTarget.open ? selected.repo_id : '')}>
                  <summary>{t('workspace.spaces.title')}</summary>
                  {spacesRepo === selected.repo_id ? (
                    <SpaceControls api={api} repoId={selected.repo_id} repoLabel={selected.label} onChanged={refresh} />
                  ) : null}
                </details>
              ) : null}
            </RepoCard>
          </>
        ) : list.length === 0 ? (
          <div className="studio-empty">
            <p className="studio-strong">{t('repos.empty.title')}</p>
            <p className="studio-muted">{t('repos.empty.body')}</p>
          </div>
        ) : desktop ? (
          <RepoList repos={list} desktop={desktop} onAction={(id, action) => void runAction(id, action)} />
        ) : (
          <div className="studio-cardlist">
            {list.map((repo) => (
              <RepoCard
                key={repo.repo_id}
                repo={repo}
                desktop={desktop}
                onAction={(action) => void runAction(repo.repo_id, action)}
              />
            ))}
          </div>
        )}

        <Note>{t('repos.footer')}</Note>
      </div>

      {flow === 'add' && desktop ? (
        <AddRepoDialog
          api={api}
          repos={list}
          onClose={closeFlow}
          onRegistered={(repo) => {
            closeFlow()
            setNotice(t('repos.registered', { label: repo.label }))
            void repos.refresh()
            go({ repo: repo.repo_id, tx: '' })
          }}
          onOpenRepo={(repoId) => {
            closeFlow()
            go({ repo: repoId, tx: '' })
          }}
        />
      ) : null}
    </div>
  )
}

export default ReposView
