/**
 * One repository, as much of it as Studio can observe (FR-INV-001).
 *
 * The card answers six questions in one place: is AI-DLC installed and healthy, which engine version is
 * installed versus bundled, what is on disk here, what in this repository is waiting for a human, what
 * Git says, and what maintenance is available. `detailed` adds the evidence a user opens the page *for*
 * — the identity, the harness directories, the receipt, the leases and the install history.
 *
 * A repository that is broken, moved or permission-denied stays on screen with its remediation
 * (FR-REP-009). Hiding it would look like it had been removed, and "removed" is the one thing Studio
 * must never imply about a path the user registered.
 *
 * Every action is a callback: this component decides which ones *apply* (from the install status and the
 * availability, which is where that knowledge already lives) and never performs one.
 */

import type { ReactNode } from 'react'

import { useI18n } from '../i18n'
import type { StudioApiError } from '../lib/api'
import { at as fmtAt, plural, since as fmtSince, unavailable } from '../lib/format'
import type {
  Availability,
  HarnessDir,
  InstallStatus,
  IntentState,
  IntentSummary,
  LeaseView,
  RepoInstall,
  RepoRecord,
  TransactionResult,
} from '../lib/types'
import { Chip, type ChipTone } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { GitSummaryChips } from './GitPanel'
import {
  Block,
  ErrorNote,
  Facts,
  FindingList,
  HarnessList,
  Note,
  type Fact,
} from './PreflightReport'
import { RecoveryBanner, recoveryEvidencePath } from './RecoveryPanel'

export type RepoAction =
  | 'open'
  | 'queue'
  | 'intents'
  | 'new-intent'
  | 'rescan'
  | 'doctor'
  | 'rebind'
  | 'remove'
  | 'install'
  | 'upgrade'
  | 'recover'
  | 'rename'
  | 'archive'
  | 'unarchive'
  | 'uninstall'
  | 'cleanup'
  | 'rollback'

const INSTALL_TONE: Record<InstallStatus, ChipTone> = {
  installed: 'ok',
  drift: 'warn',
  not_installed: 'neutral',
  recovery_required: 'danger',
}

const INSTALL_ICON: Record<InstallStatus, 'check' | 'warn' | 'install' | 'recovery'> = {
  installed: 'check',
  drift: 'warn',
  not_installed: 'install',
  recovery_required: 'recovery',
}

/** Visual spec §3.7. Every value has a tone so a new backend state cannot fall through to "no chip". */
const INTENT_TONE: Record<IntentState, ChipTone> = {
  Idle: 'neutral',
  Queued: 'neutral',
  Running: 'ok',
  WaitingForYou: 'accent',
  Paused: 'neutral',
  Parked: 'neutral',
  Interrupted: 'warn',
  ReconciliationRequired: 'danger',
  RetryEligible: 'warn',
  CircuitOpen: 'warn',
  Failed: 'danger',
  Completed: 'ok',
  Archived: 'neutral',
}

const AVAILABILITY_TONE: Record<Availability, ChipTone> = {
  available: 'ok',
  moved: 'warn',
  permission_denied: 'danger',
  unavailable: 'warn',
  identity_unprovable: 'warn',
}

/**
 * Studio's own harness directory (the backend's `C.STUDIO_HARNESS_DIR`). The wire has no constant for
 * it, and the copy has to name it: "installing adds `.kiro` beside it" is the sentence that tells a
 * user what an install into a repository driven by someone else's harness will actually do.
 */
export const STUDIO_HARNESS_DIR = '.kiro'

/**
 * The harness this repository's AI-DLC is installed under when it is NOT Studio's own, else null.
 *
 * A repository can hold AI-DLC under `.claude`, `.codex`, `.cursor` or `.aidlc`. Studio reads and
 * drives that harness happily — `status` stays `installed` and every read resolves through it — but
 * `own_engine_version === null` says the `.kiro` harness Studio manages is not there, so there is no
 * installation of *ours* to upgrade and the install lane must offer `install`. The version comes from
 * the `harness_dirs` entry for that directory, which is where the on-disk fact was read; when the root
 * was unreadable there is no entry, and this returns null rather than guessing what is on disk.
 */
export function foreignHarness(install: RepoInstall): HarnessDir | null {
  if (install.own_engine_version !== null) return null
  const dir = install.engine_dir
  if (dir === null || dir === STUDIO_HARNESS_DIR) return null
  return install.harness_dirs.find((entry) => entry.dir === dir) ?? null
}

/** The install-health chip: token, icon and the word, so none of the three carries the meaning alone. */
export function InstallChip({ status }: { status: InstallStatus }) {
  const { t } = useI18n()
  return (
    <Chip tone={INSTALL_TONE[status]} icon={INSTALL_ICON[status]}>
      {t(`enum.installStatus.${status}`)}
    </Chip>
  )
}

/** Installed engine, with the bundled version beside it whenever the two differ. */
export function EngineChips({ repo }: { repo: RepoRecord }) {
  const i18n = useI18n()
  const { t } = i18n
  const install = repo.install
  const differs = install.engine_version !== install.bundled_engine_version
  // Without this chip, a repository none of whose AI-DLC is Studio's reads "Installed", "2.7.1" and
  // then "Install AI-DLC" in the same row, which looks like a bug rather than the offer it is.
  const foreign = foreignHarness(install)

  return (
    <span className="studio-chiplist">
      <Chip mono icon="install" title={t('install.version.from')}>
        {install.engine_version ?? t('install.version.notInstalled')}
      </Chip>
      {differs ? (
        <Chip mono title={t('install.version.to')}>
          {t('repos.engine.bundled', { version: install.bundled_engine_version })}
        </Chip>
      ) : null}
      {foreign === null ? null : (
        <Chip tone="accent" icon="install">
          {t('repos.engine.otherHarness', { dir: foreign.dir })}
        </Chip>
      )}
      {install.upgrade_available ? (
        <Chip tone="accent" icon="install">
          {t('repos.engine.upgradeAvailable')}
        </Chip>
      ) : null}
      {install.newer_installed ? (
        <Chip tone="warn" icon="warn">
          {t('repos.engine.newerInstalled')}
        </Chip>
      ) : null}
      {install.state_version_blocked ? (
        <Chip tone="warn" icon="lock">
          {t('repos.engine.stateBlocked', { version: install.engine_state_version ?? unavailable(i18n) })}
        </Chip>
      ) : null}
      {install.drift_count > 0 ? (
        <Chip tone="warn" icon="warn">
          {plural(i18n, 'repos.drift', install.drift_count)}
        </Chip>
      ) : null}
    </span>
  )
}

/** Intents, in-flight turns, open actions and blocking findings for this repository. */
function CountChips({ repo }: { repo: RepoRecord }) {
  const i18n = useI18n()
  const counts = repo.counts
  return (
    <span className="studio-chiplist">
      <Chip icon="intent" mono>
        {plural(i18n, 'repos.counts.intents', counts.intents)}
      </Chip>
      {counts.in_flight > 0 ? (
        <Chip tone="accent" mono>
          {plural(i18n, 'repos.counts.inFlight', counts.in_flight)}
        </Chip>
      ) : null}
      <Chip tone={counts.open_actions > 0 ? 'accent' : 'neutral'} icon="inbox" mono>
        {plural(i18n, 'repos.counts.open', counts.open_actions)}
      </Chip>
      {counts.blocking_findings > 0 ? (
        <Chip tone="danger" icon="recovery" mono>
          {plural(i18n, 'repos.counts.blocking', counts.blocking_findings)}
        </Chip>
      ) : null}
    </span>
  )
}

/**
 * Which maintenance actions apply to this repository right now. Pure, so the table can reuse it.
 *
 * Install and upgrade key on `own_engine_version`, never on `status`: `status` answers "can this
 * repository host AI-DLC work at all", and a repository whose AI-DLC lives under `.claude` answers yes
 * to that while owning nothing of Studio's. Keying the offer on `status` there refuses `install`
 * (already installed) and `upgrade` (same version installed) both, which left no path to a `.kiro`
 * harness at all. The backend's install lane makes the same distinction, so an offer this function
 * makes is one the preview will actually accept.
 */
export function repoActions(repo: RepoRecord): RepoAction[] {
  if (repo.archived) return ['unarchive', 'rename', 'cleanup', 'remove']
  const out: RepoAction[] = []
  const status = repo.install.status
  const readable = repo.availability === 'available'
  const ownInstalled = repo.install.own_engine_version !== null

  if (status === 'recovery_required') out.push('recover')
  // Not under recovery: a failed transaction is cleared by recovery, not by installing over it.
  if (readable && !ownInstalled && status !== 'recovery_required') out.push('install')
  if (
    readable &&
    ownInstalled &&
    (status === 'installed' || status === 'drift') &&
    !repo.install.newer_installed &&
    (repo.install.upgrade_available || repo.install.drift_count > 0)
  ) {
    out.push('upgrade')
  }
  out.push('rescan')
  if (readable && (status === 'installed' || status === 'drift')) out.push('doctor')
  if (readable && repo.install.receipt && status !== 'recovery_required') out.push('uninstall')
  if (readable && repo.install.rollback_target && status !== 'recovery_required') out.push('rollback')
  if (repo.availability === 'moved' || repo.availability === 'unavailable') out.push('rebind')
  out.push('cleanup', 'rename', 'archive', 'remove')
  return out
}

/** The actions that write, or that open a write flow. Hidden below 900px (PRD §8.4). */
const DESKTOP_ONLY: readonly RepoAction[] = ['install', 'upgrade', 'recover', 'rebind', 'remove', 'doctor', 'rename', 'archive', 'unarchive', 'uninstall', 'cleanup', 'rollback']

const ACTION_LABEL: Record<RepoAction, string> = {
  open: 'repos.action.details',
  queue: 'repos.card.queue.open',
  intents: 'repos.card.intents.open',
  'new-intent': 'intents.newIntent',
  rescan: 'repos.action.rescan',
  doctor: 'repos.action.doctor',
  rebind: 'repos.action.rebind',
  remove: 'repos.action.remove',
  install: 'repos.action.install',
  upgrade: 'repos.action.upgrade',
  recover: 'repos.action.recover',
  rename: 'repos.action.rename',
  archive: 'repos.action.archive',
  unarchive: 'repos.action.unarchive',
  uninstall: 'repos.action.uninstall',
  cleanup: 'maintenance.title',
  rollback: 'repos.action.rollback',
}

function LeaseFact({ lease }: { lease: LeaseView | null }) {
  const i18n = useI18n()
  const { t } = i18n
  if (lease === null) return <span className="studio-muted">{t('repos.card.lease.none')}</span>
  return (
    <span className="studio-chiplist">
      <Chip tone={lease.orphaned ? 'danger' : 'accent'} icon="lock" mono>
        {lease.operation_type ?? lease.kind}
      </Chip>
      <span className="studio-muted">{t('repos.card.lease.since', { when: fmtSince(i18n, lease.acquired_at) })}</span>
      {lease.orphaned ? (
        <Chip tone="danger" icon="warn">
          {t('repos.card.lease.orphaned')}
        </Chip>
      ) : null}
    </span>
  )
}

export interface RepoCardProps {
  repo: RepoRecord
  /** Adds identity, harness, receipt, leases, findings and install history. */
  detailed?: boolean
  /** False below 900px: the write flows are desktop-only, so their buttons are not rendered at all. */
  desktop: boolean
  /** The preview owns installation while open; don't repeat its entry action below it. */
  installPreviewOpen?: boolean
  /** The action currently in flight, so its button says so and the rest are disabled. */
  busy?: RepoAction | null
  error?: StudioApiError | null
  transactions?: TransactionResult[]
  /** The repository's intents, from `GET /repos/{id}`. Detail mode lists them (FR-INV-001). */
  intents?: IntentSummary[]
  onAction: (action: RepoAction) => void
  /** Re-open a past transaction from the install history (detail mode only). */
  onOpenTransaction?: (transactionId: string) => void
  /** Extra blocks in detail mode (the Git panel, an open preview). */
  children?: ReactNode
  /** Results of maintenance controls, shown beside those controls instead of above the long card. */
  maintenanceFeedback?: ReactNode
}

export function RepoCard({
  repo,
  detailed = false,
  desktop,
  installPreviewOpen = false,
  busy = null,
  error = null,
  transactions = [],
  intents = [],
  onAction,
  onOpenTransaction,
  children,
  maintenanceFeedback,
}: RepoCardProps) {
  const i18n = useI18n()
  const { t } = i18n
  const install = repo.install
  const blocked = install.status === 'recovery_required'
  const canCreateIntent = !repo.archived && repo.availability === 'available'
    && (install.status === 'installed' || install.status === 'drift')
  const foreign = foreignHarness(install)
  const attention = blocked
    ? 'blocked'
    : repo.availability !== 'available' || install.status === 'drift'
      ? 'true'
      : 'false'

  const actions = repoActions(repo).filter(
    (action) => (desktop || !DESKTOP_ONLY.includes(action)) && !(installPreviewOpen && action === 'install'),
  )

  const a11y = t('repos.a11y.repoRow', {
    label: repo.label,
    path: repo.canonical_path,
    install: t(`enum.installStatus.${install.status}`),
    availability: t(`enum.availability.${repo.availability}`),
    intents: plural(i18n, 'repos.counts.intents', repo.counts.intents),
    queue: plural(i18n, 'repos.counts.open', repo.counts.open_actions),
  })

  const identityRows: Fact[] = [
    { key: 'path', label: t('repos.card.identity.path'), value: repo.canonical_path, mono: true },
    { key: 'resolved', label: t('repos.card.identity.resolved'), value: repo.resolved_identity, mono: true },
    {
      key: 'gitdir',
      label: t('repos.card.identity.gitCommonDir'),
      value: repo.git_common_dir_identity,
      mono: true,
    },
    { key: 'platform', label: t('repos.card.identity.platform'), value: repo.platform, mono: true },
    { key: 'added', label: t('repos.card.identity.added'), value: fmtAt(i18n, repo.added_at) },
    { key: 'seen', label: t('repos.card.identity.lastSeen'), value: fmtAt(i18n, repo.last_seen) },
    { key: 'scanned', label: t('repos.card.identity.scanned'), value: fmtAt(i18n, repo.scanned_at) },
  ]

  return (
    <article className="studio-repocard" data-attention={attention} aria-label={a11y}>
      <header className="studio-repocard-head">
        <div className="studio-grow">
          <h3 className="studio-repocard-title">{repo.label}</h3>
          <p className="studio-subpath">{repo.canonical_path}</p>
        </div>
        <div className="studio-chiplist">
          <InstallChip status={install.status} />
          {repo.availability === 'available' ? null : (
            <Chip
              tone={AVAILABILITY_TONE[repo.availability]}
              icon="warn"
              title={repo.availability_detail ?? undefined}
            >
              {t(`enum.availability.${repo.availability}`)}
            </Chip>
          )}
        </div>
      </header>

      <div className="studio-repocard-chips">
        <EngineChips repo={repo} />
        <CountChips repo={repo} />
        <GitSummaryChips git={repo.git} />
      </div>

      {repo.availability === 'available' ? null : (
        <div className="studio-failure-detail" data-tone="warn">
          <strong>{t('repos.availability.title')}</strong>{' '}
          {t(`repos.remedy.${repo.availability}`)}
          {repo.availability_detail ? (
            <p className="studio-subpath studio-wrap-any">{repo.availability_detail}</p>
          ) : null}
        </div>
      )}

      {blocked ? (
        <RecoveryBanner
          repoLabel={repo.label}
          failedDir={recoveryEvidencePath(transactions)}
          {...(desktop ? { onStart: () => onAction('recover') } : {})}
        />
      ) : null}

      {error ? <ErrorNote error={error} reassure={t('repos.error.unchanged')} /> : null}

      {detailed ? (
        <>
          <Block title={t('repos.card.identity')} icon="repo">
            <Facts rows={identityRows} />
          </Block>

          <Block title={t('repos.card.install')} icon="install">
            {foreign === null ? null : (
              <>
                <Note>
                  {t('repos.card.install.otherHarness', {
                    dir: foreign.dir,
                    version: foreign.engine_version ?? unavailable(i18n),
                    own: STUDIO_HARNESS_DIR,
                  })}
                </Note>
                <Note>
                  {t('repos.card.install.otherHarnessInstall', { dir: foreign.dir, own: STUDIO_HARNESS_DIR })}
                </Note>
              </>
            )}
            <Facts
              rows={[
                {
                  key: 'dir',
                  label: t('repos.card.install.engineDir'),
                  value: install.engine_dir,
                  mono: true,
                },
                {
                  key: 'state',
                  label: t('repos.card.install.stateVersion'),
                  value: install.engine_state_version,
                  mono: true,
                },
                {
                  key: 'stages',
                  label: t('repos.card.install.stages'),
                  value: install.stage_count,
                  mono: true,
                },
              ]}
            />
            <h4 className="studio-subhead">{t('repos.card.harness')}</h4>
            {install.harness_dirs.length === 0 ? (
              <p className="studio-muted">{t('repos.card.harness.none')}</p>
            ) : (
              <HarnessList dirs={install.harness_dirs} />
            )}
          </Block>

          <Block title={t('repos.card.receipt')} icon="doc">
            {install.receipt === null ? (
              <p className="studio-muted">{t('repos.card.receipt.none')}</p>
            ) : (
              <span className="studio-chiplist">
                <Chip mono icon="lock">
                  {install.receipt.receipt_id}
                </Chip>
                <Chip mono icon="install">
                  {install.receipt.engine_version}
                </Chip>
                <Chip mono>{plural(i18n, 'repos.card.receipt.files', install.receipt.files)}</Chip>
                <Chip mono>
                  {t('repos.card.receipt.written', { when: fmtAt(i18n, install.receipt.committed_at) })}
                </Chip>
                <Chip tone={install.receipt.status === 'current' ? 'ok' : 'neutral'}>
                  {t(`install.receiptStatus.${install.receipt.status}`)}
                </Chip>
              </span>
            )}
          </Block>

          <Block title={t('repos.card.queue')} icon="inbox">
            {repo.counts.open_actions === 0 ? (
              <p className="studio-muted">{t('repos.card.queue.none')}</p>
            ) : (
              <>
                <CountChips repo={repo} />
                <div className="studio-repo-actions">
                  <button type="button" className="studio-btn studio-btn-sm" onClick={() => onAction('queue')}>
                    <Icon name="inbox" size={13} />
                    {t('repos.card.queue.open')}
                  </button>
                </div>
              </>
            )}
          </Block>

          <Block title={t('repos.card.intents')} icon="intent">
            {intents.length === 0 ? (
              <p className="studio-muted">{t('repos.card.intents.none')}</p>
            ) : (
              <>
                <ul className="studio-plain-list">
                  {intents.map((intent) => (
                    <li className="studio-row studio-chiplist" key={intent.intent_key}>
                      {/* The slug and the stage are AI-DLC's own words and are never translated. */}
                      <span className="studio-mono studio-strong">{intent.slug}</span>
                      <Chip tone={INTENT_TONE[intent.operational_state]}>
                        {t(`enum.intentState.${intent.operational_state}`)}
                      </Chip>
                      <span className="studio-mono studio-muted">
                        {intent.disk.current_stage ?? unavailable(i18n)}
                      </span>
                      {intent.open_actions > 0 ? (
                        <Chip tone="accent" icon="inbox" mono>
                          {plural(i18n, 'repos.counts.open', intent.open_actions)}
                        </Chip>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </>
            )}
            {canCreateIntent || intents.length > 0 ? (
              <div className="studio-repo-actions">
                {canCreateIntent ? (
                  <button type="button" className="studio-btn studio-btn-sm" data-variant="primary"
                    onClick={() => onAction('new-intent')}>
                    <Icon name="plus" size={13} />
                    {t('intents.newIntent')}
                  </button>
                ) : null}
                {intents.length > 0 ? (
                  <button type="button" className="studio-btn studio-btn-sm" onClick={() => onAction('intents')}>
                    <Icon name="intent" size={13} />
                    {t('repos.card.intents.open')}
                  </button>
                ) : null}
              </div>
            ) : null}
          </Block>

          <Block title={t('repos.card.leases')} icon="lock">
            <Facts
              rows={[
                {
                  key: 'exec',
                  label: t('repos.card.lease.execution'),
                  value: <LeaseFact lease={repo.leases.execution} />,
                },
                {
                  key: 'admin',
                  label: t('repos.card.lease.admin'),
                  value: <LeaseFact lease={repo.leases.admin} />,
                },
              ]}
            />
          </Block>

          {repo.findings.length > 0 ? (
            <Block title={t('repos.card.findings')} icon="warn">
              <FindingList findings={repo.findings} />
            </Block>
          ) : null}

          <Block title={t('repos.card.transactions')} icon="clock">
            {transactions.length === 0 ? (
              <p className="studio-muted">{t('repos.card.transactions.none')}</p>
            ) : (
              <ul className="studio-plain-list">
                {transactions.map((tx) => (
                  <li className="studio-row studio-chiplist" key={tx.transaction_id}>
                    <Chip mono>{t(`install.tx.kind.${tx.kind}`)}</Chip>
                    <Chip
                      tone={
                        tx.status === 'committed'
                          ? 'ok'
                          : tx.status === 'recovery_required' || tx.status === 'failed'
                            ? 'danger'
                            : 'warn'
                      }
                    >
                      {t(`install.txStatus.${tx.status}`)}
                    </Chip>
                    <Chip mono icon="install">
                      {tx.engine_version}
                    </Chip>
                    <span className="studio-muted">{fmtAt(i18n, tx.started_at)}</span>
                    <span className="studio-mono studio-muted">{tx.transaction_id}</span>
                    {onOpenTransaction ? (
                      <button
                        type="button"
                        className="studio-btn studio-btn-sm"
                        onClick={() => onOpenTransaction(tx.transaction_id)}
                      >
                        {t('install.tx.open')}
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </Block>

          {children}
        </>
      ) : null}

      {(() => {
        const buttons = (
          <div className="studio-repo-actions">
            {detailed ? null : (
              <button type="button" className="studio-btn" onClick={() => onAction('open')}>
                <Icon name="chevron" size={14} />
                {t('repos.action.details')}
              </button>
            )}
            {!detailed && repo.counts.open_actions > 0 ? (
              <button type="button" className="studio-btn" onClick={() => onAction('queue')}>
                <Icon name="inbox" size={14} />
                {t('repos.card.queue.open')}
              </button>
            ) : null}
            {actions.map((action) => (
              <button
                key={action}
                type="button"
                className="studio-btn"
                {...(action === 'install' || action === 'upgrade' || action === 'recover'
                  ? { 'data-variant': 'primary' }
                  : action === 'remove'
                    ? { 'data-variant': 'danger' }
                    : {})}
                disabled={busy !== null}
                onClick={() => onAction(action)}
              >
                {busy === action && action === 'rescan'
                  ? t('repos.action.rescanning')
                  : busy === action && action === 'doctor'
                    ? t('repos.action.doctorRunning')
                    : t(ACTION_LABEL[action])}
              </button>
            ))}
          </div>
        )
        // In detail mode the actions are a named section, because "Maintenance" is what the user came
        // here to find; on a summary card they are just the card's controls.
        return detailed ? (
          <Block title={t('repos.maintenance.title')} icon="settings">
            {buttons}
            {maintenanceFeedback}
            {actions.includes('doctor') ? <Note>{t('repos.action.doctorNote')}</Note> : null}
            <Note>{t('repos.maintenance.note')}</Note>
          </Block>
        ) : (
          buttons
        )
      })()}
    </article>
  )
}
