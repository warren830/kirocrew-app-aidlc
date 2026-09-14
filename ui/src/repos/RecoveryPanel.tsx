/**
 * `Install recovery required` — the state a failed rollback leaves behind (FR-INST-013, §12.4).
 *
 * Three things have to be true on screen, and all three are requirements:
 *
 *  1. It is named as *install recovery required*, not as "an error". The repository may hold a mixed
 *     installation, which is the one outcome P-06 exists to prevent, so the words are the state name.
 *  2. The recovery path is shown. The failed candidate and its transaction record are kept outside the
 *     repository, and that path is the only place the evidence exists.
 *  3. It says AI-DLC execution is blocked here until it clears. A user who does not know that will spend
 *     the next ten minutes wondering why `Run` refuses.
 *
 * The banner is assertive: unlike a preview finishing, this is a condition the user has to know about
 * before they try to run anything (PRD §10.3 reserves assertive for delivery uncertainty and recovery).
 */

import { useI18n } from '../i18n'
import type { StudioApi } from '../lib/api'
import type { TransactionResult } from '../lib/types'
import { Icon } from '../shell/Icon'
import { Note } from './PreflightReport'
import { PreviewFlow } from './InstallPreview'

/**
 * The recovery evidence path for a repository, from its transaction history.
 *
 * The newest transaction that recorded a `failed_dir` wins. `transactions` arrives newest-first from
 * `GET /repos/{id}` (last 5), and a transaction that rolled back cleanly also keeps its evidence, so
 * the search is for the recovery-required one first and any failure second.
 */
export function recoveryEvidencePath(transactions: TransactionResult[]): string | null {
  const required = transactions.find((tx) => tx.status === 'recovery_required' && tx.failed_dir)
  if (required?.failed_dir) return required.failed_dir
  return transactions.find((tx) => tx.failed_dir)?.failed_dir ?? null
}

export interface RecoveryBannerProps {
  repoLabel: string
  failedDir: string | null
  /** Absent below 900px: recovery is a desktop flow, and a button that opens nothing is worse than none. */
  onStart?: () => void
}

/** Shown wherever a recovery-required repository is shown, whether or not the flow is open. */
export function RecoveryBanner({ repoLabel, failedDir, onStart }: RecoveryBannerProps) {
  const { t } = useI18n()
  return (
    <div className="studio-failure-detail studio-recovery" role="alert">
      <p className="studio-row studio-strong">
        <Icon name="recovery" size={15} />
        {t('install.recovery.title')}
      </p>
      <p>{t('install.recovery.body')}</p>
      <p>
        <strong>{t('install.recovery.blocked')}</strong>
      </p>
      <p className="studio-help">{t('install.recovery.pathLabel')}</p>
      {failedDir ? (
        <p className="studio-mono studio-wrap-any">{failedDir}</p>
      ) : (
        <p className="studio-muted">{t('install.recovery.noPath')}</p>
      )}
      <p className="studio-help">{t('install.recovery.pathNote')}</p>
      {onStart ? (
        <button type="button" className="studio-btn" onClick={onStart}>
          <Icon name="refresh" size={14} />
          {t('install.recovery.start')}
        </button>
      ) : null}
      {/* The screen-reader sentence names the repository, because the alert can fire while the user is
          reading a different card and "recovery required" alone does not say where. */}
      <span className="studio-sr">{t('repos.a11y.blocked', { repo: repoLabel })}</span>
    </div>
  )
}

export interface RecoveryPanelProps {
  api: StudioApi
  repoId: string
  repoLabel: string
  failedDir: string | null
  onStarted: (transactionId: string) => void
  onClose: () => void
}

/**
 * The recovery transaction: its own preview, its own confirmation, its own validation.
 *
 * Deliberately the same `PreviewFlow` as install and upgrade rather than a bespoke "fix it" button —
 * §12.4 requires recovery to be a distinct transaction *with a preview and rollback evidence*, which is
 * exactly what this flow is.
 */
export function RecoveryPanel({ api, repoId, repoLabel, failedDir, onStarted, onClose }: RecoveryPanelProps) {
  const { t } = useI18n()
  return (
    <PreviewFlow
      api={api}
      repoId={repoId}
      kind="recovery"
      onStarted={onStarted}
      onClose={onClose}
      intro={
        <>
          <RecoveryBanner repoLabel={repoLabel} failedDir={failedDir} />
          <Note>{t('install.confirm.note')}</Note>
        </>
      }
    />
  )
}
