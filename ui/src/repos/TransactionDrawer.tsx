/**
 * What happened after the 202.
 *
 * `POST …/install` answers immediately with a transaction id and then writes in the background, so the
 * only truthful progress indicator is the transaction row itself (§2.2). This drawer polls it and shows
 * the recorded steps, in the order they actually ran — including the rollback steps, because "it failed
 * and here is what was restored" is the outcome a user most needs to be able to read.
 *
 * Two announcement rules:
 *   - status changes are polite; a step finishing is not worth interrupting anyone for.
 *   - `recovery_required` is assertive, and stays on screen. A failed rollback blocks AI-DLC execution in
 *     the repository, and a user who misses that will read every later refusal as a different bug.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { useI18n, type I18n } from '../i18n'
import { decodeError, type StudioApi, type StudioApiError } from '../lib/api'
import { at as fmtAt } from '../lib/format'
import { useResource } from '../lib/useResource'
import type { StepRecord, TransactionResponse, TransactionStatus } from '../lib/types'
import { Chip, type ChipTone } from '../shell/Chip'
import { Icon, type IconName } from '../shell/Icon'
import { Block, ErrorNote, Facts, Note } from './PreflightReport'
import { RecoveryBanner } from './RecoveryPanel'

/** Statuses that mean the transaction is finished. */
const TERMINAL: readonly TransactionStatus[] = ['committed', 'rolled_back', 'recovery_required', 'failed']

const STATUS_TONE: Record<TransactionStatus, ChipTone> = {
  staged: 'accent',
  leased: 'accent',
  backed_up: 'accent',
  written: 'accent',
  merged: 'accent',
  validated: 'accent',
  committed: 'ok',
  rolling_back: 'warn',
  rolled_back: 'warn',
  recovery_required: 'danger',
  failed: 'danger',
}

const STATUS_ICON: Record<TransactionStatus, IconName> = {
  staged: 'clock',
  leased: 'lock',
  backed_up: 'clock',
  written: 'clock',
  merged: 'clock',
  validated: 'clock',
  committed: 'check',
  rolling_back: 'warn',
  rolled_back: 'warn',
  recovery_required: 'recovery',
  failed: 'recovery',
}

function stepState(step: StepRecord): 'ok' | 'failed' | 'running' {
  if (step.ok === true) return 'ok'
  if (step.ok === false) return 'failed'
  return 'running'
}

/**
 * A step's words.
 *
 * A step name Studio has no phrase for is shown verbatim rather than skipped: the alternative is a blank
 * row in the one place a user is trying to find out how far an install got.
 */
function stepLabel(i18n: I18n, name: string): string {
  const key = `install.step.${name}`
  return i18n.has(key) ? i18n.t(key) : name
}

export interface TransactionDrawerProps {
  api: StudioApi
  repoId: string
  repoLabel: string
  transactionId: string
  onClose: () => void
  /** Called once the transaction reaches a terminal status, so the repository is re-read. */
  onSettled?: (status: TransactionStatus) => void
}

export function TransactionDrawer({
  api,
  repoId,
  repoLabel,
  transactionId,
  onClose,
  onSettled,
}: TransactionDrawerProps) {
  const i18n = useI18n()
  const { t } = i18n

  const resource = useResource<TransactionResponse>(
    `transaction:${repoId}:${transactionId}`,
    useCallback((signal) => api.transaction(repoId, transactionId, { signal }), [api, repoId, transactionId]),
    {
      // `looksBusy` already treats a non-terminal transaction as busy, which gives the 2 s cadence while
      // it runs and 15 s once it settles; `transaction.updated` events short-circuit both.
      revalidateOn: ['transaction.updated', 'repo.updated', 'reset'],
    },
  )

  const tx = resource.data?.transaction ?? null
  const status = tx?.status ?? null
  const [cancelling, setCancelling] = useState(false)
  const [cancelError, setCancelError] = useState<StudioApiError | null>(null)
  useEffect(() => { setCancelling(false); setCancelError(null) }, [transactionId])
  const cancel = async () => {
    setCancelling(true)
    setCancelError(null)
    try {
      await api.cancelTransaction(repoId, transactionId)
      await resource.refresh()
    } catch (caught) {
      setCancelError(decodeError(caught))
    } finally {
      setCancelling(false)
    }
  }

  // Reported once per terminal status rather than on every poll: a repository refetch per 2 s tick would
  // turn one install into a steady stream of full repo scans.
  const reported = useRef<string | null>(null)
  useEffect(() => {
    if (status === null || !TERMINAL.includes(status)) return
    if (reported.current === status) return
    reported.current = status
    onSettled?.(status)
  }, [status, onSettled])

  return (
    <section className="studio-drawer" aria-label={t('install.tx.title', { id: transactionId })}>
      <div className="studio-spread studio-preview-head">
        <h2 className="studio-mono">{t('install.tx.title', { id: transactionId })}</h2>
        <button type="button" className="studio-btn" onClick={onClose}>
          <Icon name="close" size={14} />
          {t('install.tx.close')}
        </button>
      </div>

      {resource.error ? <ErrorNote error={resource.error} /> : null}
      {cancelError ? <ErrorNote error={cancelError} /> : null}

      {tx === null ? (
        resource.loading ? <p className="studio-muted">{t('common.loading')}</p> : null
      ) : (
        <>
          <Facts
            rows={[
              { key: 'kind', label: t('install.tx.kind'), value: t(`install.tx.kind.${tx.kind}`) },
              {
                key: 'status',
                label: t('install.tx.status'),
                value: (
                  <Chip tone={STATUS_TONE[tx.status]} icon={STATUS_ICON[tx.status]}>
                    {t(`install.txStatus.${tx.status}`)}
                  </Chip>
                ),
              },
              { key: 'engine', label: t('install.version.to'), value: tx.kind === 'uninstall' && !tx.steps.some((step) => step.name === 'confirm_recovery') ? t('install.version.notInstalled') : tx.engine_version, mono: true },
              { key: 'started', label: t('install.tx.started'), value: fmtAt(i18n, tx.started_at) },
              {
                key: 'finished',
                label: t('install.tx.finished'),
                value: tx.finished_at ? fmtAt(i18n, tx.finished_at) : null,
              },
            ]}
          />
          {['install', 'upgrade', 'recovery'].includes(tx.kind) && !TERMINAL.includes(tx.status) ? (
            <div className="studio-col">
              <Note>{t('install.cancel.explanation')}</Note>
              <button type="button" className="studio-btn" disabled={cancelling || tx.error === 'cancel_requested' || tx.status === 'rolling_back'}
                onClick={() => void cancel()}>{t('install.cancel.transaction')}</button>
            </div>
          ) : null}

          <Block title={t('install.tx.steps')} icon="clock">
            <ol className="studio-steps">
              {tx.steps.map((step, index) => (
                <li className="studio-step" data-state={stepState(step)} key={`${step.name}-${index}`}>
                  <span className="studio-step-dot" aria-hidden>
                    {step.ok === true ? (
                      <Icon name="check" size={10} strokeWidth={2.4} />
                    ) : step.ok === false ? (
                      <Icon name="close" size={10} strokeWidth={2.4} />
                    ) : (
                      <Icon name="clock" size={10} strokeWidth={2} />
                    )}
                  </span>
                  <span className="studio-grow">
                    {stepLabel(i18n, step.name)}
                    {/* The state is a word too, so the dot is never the only encoding of it. */}
                    <span className="studio-step-state"> · {t(`install.stepState.${stepState(step)}`)}</span>
                  </span>
                  <span className="studio-mono studio-muted">{fmtAt(i18n, step.started_at)}</span>
                </li>
              ))}
            </ol>
          </Block>

          {tx.error === 'cancel_requested' ? (
            <p className="studio-note" role="status">{t('install.cancel.requested')}</p>
          ) : tx.error === 'cancelled' ? (
            <p className="studio-note" role="status">{t('install.cancel.finished')}</p>
          ) : tx.error ? (
            <div className="studio-failure-detail">
              <strong>{t('install.tx.error')}</strong> <span className="studio-wrap-any">{tx.error}</span>
            </div>
          ) : null}

          {tx.status === 'recovery_required' ? (
            <RecoveryBanner repoLabel={repoLabel} failedDir={tx.failed_dir} />
          ) : (
            <>
              {tx.status === 'rolled_back' ? <Note tone="warn">{t('install.tx.rolledBack')}</Note> : null}
              {tx.status === 'committed' ? (
                <Note>{t(tx.kind === 'uninstall' ? 'install.tx.uninstalled' : 'install.tx.committed', { version: tx.engine_version })}</Note>
              ) : null}
              {tx.failed_dir ? (
                <p className="studio-help studio-wrap-any">
                  {t('install.tx.failedDir')} <span className="studio-mono">{tx.failed_dir}</span>
                </p>
              ) : null}
            </>
          )}

          {/* Polite, and only the summary sentence: announcing each step as it lands would talk over a
              user who is reading the plan that produced them. */}
          <span className="studio-sr" role="status" aria-live="polite">
            {t('install.tx.progress', {
              kind: t(`install.tx.kind.${tx.kind}`),
              id: transactionId,
              status: t(`install.txStatus.${tx.status}`),
            })}
          </span>
        </>
      )}
    </section>
  )
}
