/**
 * The install preview, and the plan view that upgrade and recovery reuse.
 *
 * This is the safety surface. Four properties are load-bearing, and each one is a requirement rather
 * than a presentation choice:
 *
 *  1. **Every managed path is shown with its ownership kind** (FR-INST-004/008). The user is confirming
 *     a set of paths Studio will own, so the set is the thing on screen — not a count of it.
 *  2. **Every conflict shows its diff and blocks** (FR-INST-007/009). A blocking entry disables the
 *     confirmation entirely; there is no force control here or anywhere else in v1, and this component
 *     deliberately offers no way to express "do it anyway".
 *  3. **What will be retired is named** (FR-INST-010), including the retired files that will be *kept*
 *     because the user changed them.
 *  4. **The confirmation carries the `plan_digest` the user actually read.** If the repository moved in
 *     between, the server answers `install_conflict` and this component refuses and re-reads the plan
 *     rather than confirming a different plan under the same click.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

import { useI18n } from '../i18n'
import { StudioApiError, isAbort, type StudioApi } from '../lib/api'
import { bytes as fmtBytes, plural, unavailable } from '../lib/format'
import type { PreviewAction, PreviewEntry, PreviewPlan, TransactionKind } from '../lib/types'
import { Chip, type ChipTone } from '../shell/Chip'
import { Icon, type IconName } from '../shell/Icon'
import { Block, ErrorNote, Facts, FindingList, Note, PreflightReportView } from './PreflightReport'

export type PreviewKind = TransactionKind

/** The order the table and the count chips use: writes, then no-ops, then retirements, then conflicts. */
export const PREVIEW_ACTIONS: readonly PreviewAction[] = [
  'create',
  'shell_create',
  'merge_create',
  'merge_update',
  'owned_identical',
  'engine_modified',
  'identical',
  'merge_identical',
  'shell_exists',
  'retire',
  'retire_blocked',
  'conflict',
  'owned_modified',
  'merge_conflict',
  'remove', 'remove_fragment', 'preserve', 'already_absent', 'uninstall_conflict',
  'restore_version', 'rollback_conflict',
  'restore_backup', 'remove_created', 'recovery_conflict',
]

/** Actions the backend refuses to write through (`installer.BLOCKING_ACTIONS`). */
const BLOCKING_ACTIONS: readonly PreviewAction[] = ['conflict', 'owned_modified', 'merge_conflict', 'uninstall_conflict', 'rollback_conflict', 'recovery_conflict']
const RETIRE_ACTIONS: readonly PreviewAction[] = ['retire', 'retire_blocked']

const ACTION_TONE: Record<PreviewAction, ChipTone> = {
  create: 'accent',
  shell_create: 'accent',
  merge_create: 'accent',
  merge_update: 'info',
  owned_identical: 'info',
  engine_modified: 'info',
  identical: 'neutral',
  merge_identical: 'neutral',
  shell_exists: 'neutral',
  retire: 'warn',
  retire_blocked: 'warn',
  conflict: 'danger',
  owned_modified: 'danger',
  merge_conflict: 'danger',
  remove: 'warn', remove_fragment: 'warn', preserve: 'neutral', already_absent: 'neutral', uninstall_conflict: 'danger',
  restore_version: 'warn', rollback_conflict: 'danger',
  restore_backup: 'warn', remove_created: 'warn', recovery_conflict: 'danger',
}

const ACTION_ICON: Record<PreviewAction, IconName> = {
  create: 'plus',
  shell_create: 'plus',
  merge_create: 'plus',
  merge_update: 'install',
  owned_identical: 'install',
  engine_modified: 'install',
  identical: 'check',
  merge_identical: 'check',
  shell_exists: 'check',
  retire: 'close',
  retire_blocked: 'lock',
  conflict: 'warn',
  owned_modified: 'warn',
  merge_conflict: 'warn',
  remove: 'close', remove_fragment: 'close', preserve: 'lock', already_absent: 'check', uninstall_conflict: 'warn',
  restore_version: 'recovery', rollback_conflict: 'warn',
  restore_backup: 'recovery', remove_created: 'close', recovery_conflict: 'warn',
}

/** How many diff lines are rendered before the rest is named rather than drawn (PRD §16.2 bounds). */
const DIFF_LINE_CAP = 200
/** Above this the managed-path table starts folded: a whole payload's worth of rows is not a thing to scroll past. */
const TABLE_OPEN_MAX = 40

function shortSha(sha: string | null): string | null {
  return sha === null ? null : sha.slice(0, 12)
}

// --------------------------------------------------------------------------- //
// the plan
// --------------------------------------------------------------------------- //

/** One unified-diff line, classified for colour AND kept readable as text (the `+`/`-` stays). */
function DiffLine({ line }: { line: string }) {
  const kind =
    line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@') || line.startsWith('diff ')
      ? 'meta'
      : line.startsWith('+')
        ? 'add'
        : line.startsWith('-')
          ? 'del'
          : 'ctx'
  return (
    <span className="studio-diffline" data-kind={kind}>
      {line === '' ? ' ' : line}
    </span>
  )
}

function Diff({ diff }: { diff: string }) {
  const { t } = useI18n()
  const lines = diff.split('\n')
  const shown = lines.slice(0, DIFF_LINE_CAP)
  return (
    <>
      {/* A `pre` of text nodes: the diff is untrusted repository content (PRD §16.2), so it is never
          markdown and never HTML — it is characters. */}
      <pre className="studio-diff">
        {shown.map((line, index) => (
          <DiffLine key={index} line={line} />
        ))}
      </pre>
      {lines.length > shown.length ? (
        <p className="studio-help">{t('install.blockers.diffTruncated', { n: DIFF_LINE_CAP })}</p>
      ) : null}
    </>
  )
}

/** What happens to a path. Ownership is a separate chip so it can also stand alone as a table column. */
function ActionChip({ entry }: { entry: PreviewEntry }) {
  const { t } = useI18n()
  return (
    <Chip tone={ACTION_TONE[entry.action]} icon={ACTION_ICON[entry.action]}>
      {t(`install.action.${entry.action}`)}
    </Chip>
  )
}

function OwnershipChips({ entry }: { entry: PreviewEntry }) {
  const { t } = useI18n()
  return (
    <span className="studio-chiplist">
      <Chip icon="lock">{t(`enum.ownership.${entry.ownership}`)}</Chip>
      {entry.fragment_key ? (
        <Chip mono>{t('install.entries.fragment', { key: entry.fragment_key })}</Chip>
      ) : null}
    </span>
  )
}

function EntryChips({ entry }: { entry: PreviewEntry }) {
  return (
    <span className="studio-chiplist">
      <ActionChip entry={entry} />
      <OwnershipChips entry={entry} />
    </span>
  )
}

function EntryReason({ entry }: { entry: PreviewEntry }) {
  const { t, has } = useI18n()
  if (!entry.reason) return null
  const key = `install.reason.${entry.reason}`
  return <p className="studio-help">{has(key) ? t(key) : entry.reason}</p>
}

function EntryDigests({ entry }: { entry: PreviewEntry }) {
  const { t } = useI18n()
  const parts: string[] = []
  const live = shortSha(entry.live_sha256)
  const payload = shortSha(entry.payload_sha256)
  const receipt = shortSha(entry.receipt_sha256)
  if (live) parts.push(t('install.entries.live', { sha: live }))
  if (payload) parts.push(t('install.entries.payload', { sha: payload }))
  if (receipt) parts.push(t('install.entries.receipt', { sha: receipt }))
  return <span className="studio-mono studio-wrap-any">{parts.join('  ·  ')}</span>
}

/** A conflicting or retiring entry as a card, with the evidence and the diff. */
function EntryCard({ entry }: { entry: PreviewEntry }) {
  const { t } = useI18n()
  return (
    <li
      className="studio-finding"
      data-severity={entry.blocking ? 'blocking' : 'warn'}
    >
      <p className="studio-mono studio-strong studio-wrap-any">{entry.path}</p>
      <EntryChips entry={entry} />
      <EntryReason entry={entry} />
      <p className="studio-subpath">
        <EntryDigests entry={entry} />
      </p>
      {entry.diff ? (
        <details>
          <summary>{t('install.blockers.diff')}</summary>
          <Diff diff={entry.diff} />
        </details>
      ) : (
        <p className="studio-help">{t('install.blockers.noDiff')}</p>
      )}
    </li>
  )
}

export function PlanView({ plan, digest }: { plan: PreviewPlan; digest: string }) {
  const i18n = useI18n()
  const { t } = i18n

  const blockers = plan.entries.filter((entry) => BLOCKING_ACTIONS.includes(entry.action))
  const retiring = plan.entries.filter((entry) => RETIRE_ACTIONS.includes(entry.action))
  const counts = PREVIEW_ACTIONS.map((action) => [action, plan.counts[action] ?? 0] as const).filter(
    ([, n]) => n > 0,
  )

  return (
    <>
      <Block title={t('install.counts.title')} icon="install">
        <Facts
          rows={[
            {
              key: 'from',
              label: t('install.version.from'),
              value: plan.engine_from ?? t('install.version.notInstalled'),
              mono: true,
            },
            { key: 'to', label: t('install.version.to'), value: plan.kind === 'uninstall' ? t('install.version.notInstalled') : plan.engine_to, mono: true },
            { key: 'studio', label: t('install.version.studio'), value: plan.studio_version, mono: true },
            { key: 'bytes', label: t('install.bytes'), value: fmtBytes(i18n, plan.bytes_to_write), mono: true },
            {
              key: 'payload',
              label: t('install.payloadDigest'),
              value: shortSha(plan.payload_digest),
              mono: true,
            },
            { key: 'plan', label: t('install.planDigest'), value: shortSha(digest), mono: true },
          ]}
        />
        <div className="studio-chiplist studio-counts">
          {counts.length === 0 ? (
            <span className="studio-muted">{t('install.entries.none')}</span>
          ) : (
            counts.map(([action, n]) => (
              <Chip key={action} tone={ACTION_TONE[action]} icon={ACTION_ICON[action]}>
                {t(`install.action.${action}`)}
                {' · '}
                <span className="studio-mono">{i18n.fmt.number(n)}</span>
              </Chip>
            ))
          )}
        </div>
        {plan.requires_admin_lease ? <Note>{t('install.lease.required')}</Note> : null}
      </Block>

      {plan.state_version_blocked ? (
        <Block title={t('install.stateBlocked.title')} icon="lock">
          <div className="studio-failure-detail">
            {t('install.stateBlocked.body', {
              found:
                plan.state_versions_found.length > 0
                  ? plan.state_versions_found.join(t('shell.format.listJoin'))
                  : unavailable(i18n),
              supported: plan.engine_to,
            })}
          </div>
          {unreadableCount(plan) > 0 ? (
            <Note tone="warn">{plural(i18n, 'install.stateBlocked.unreadable', unreadableCount(plan))}</Note>
          ) : null}
        </Block>
      ) : null}

      {blockers.length > 0 ? (
        <Block title={t('install.blockers.title')} icon="warn">
          <ul className="studio-plain-list">
            {blockers.map((entry) => (
              <EntryCard entry={entry} key={entry.path} />
            ))}
          </ul>
          <Note tone="danger">{t('install.blockers.body')}</Note>
        </Block>
      ) : null}

      {retiring.length > 0 ? (
        <Block title={t('install.retire.title')} icon="close">
          <ul className="studio-plain-list">
            {retiring.map((entry) => (
              <EntryCard entry={entry} key={entry.path} />
            ))}
          </ul>
          <Note>{t('install.retire.body')}</Note>
        </Block>
      ) : null}

      {plan.warnings.length > 0 ? (
        <Block title={t('install.warnings.title')} icon="warn">
          <FindingList findings={plan.warnings} />
        </Block>
      ) : null}

      {plan.entries.some((entry) => entry.action === 'preserve') ? (
        <Block title={t('install.preserved.title')} icon="lock">
          <Note>{t('install.preserved.body')}</Note>
          <ul className="studio-plain-list">
            {plan.entries.filter((entry) => entry.action === 'preserve').map((entry) => (
              <li key={entry.path}>
                <span className="studio-mono">{entry.path}</span>
                <EntryReason entry={entry} />
              </li>
            ))}
          </ul>
        </Block>
      ) : null}

      <Block title={t('install.entries.title')} icon="doc">
        <details {...(plan.entries.length <= TABLE_OPEN_MAX ? { open: true } : {})}>
          <summary>{plural(i18n, 'install.entries.summary', plan.entries.length)}</summary>
          {plan.entries.length === 0 ? (
            <p className="studio-muted">{t('install.entries.none')}</p>
          ) : (
            <table className="studio-tbl">
              <caption className="studio-sr">{t('install.entries.caption')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('install.entries.col.path')}</th>
                  <th scope="col">{t('install.entries.col.ownership')}</th>
                  <th scope="col">{t('install.entries.col.action')}</th>
                  <th scope="col">{t('install.entries.col.hash')}</th>
                </tr>
              </thead>
              <tbody>
                {plan.entries.map((entry) => (
                  <tr key={entry.path}>
                    <th scope="row" className="studio-mono studio-wrap-any studio-rowhead">
                      {entry.path}
                    </th>
                    <td>
                      <OwnershipChips entry={entry} />
                    </td>
                    <td>
                      <ActionChip entry={entry} />
                      <EntryReason entry={entry} />
                    </td>
                    <td>
                      <EntryDigests entry={entry} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </details>
      </Block>

      <Block title={t('install.preflight.title')} icon="search">
        <details>
          <summary>{t('repos.preflight.title')}</summary>
          <PreflightReportView report={plan.preflight} />
        </details>
      </Block>
    </>
  )
}

/**
 * How many state files the server could not read at all.
 *
 * DRIFT: `types.ts` types `state_versions_unreadable` as a `number`, but `PreviewPlan.to_json` sends
 * `list(self.state_versions_unreadable)` — a list of the paths it failed on. Both shapes are handled
 * here rather than trusting either, because the number is only ever used to say "n files", and reading
 * `.length` off a number would silently render nothing.
 */
function unreadableCount(plan: PreviewPlan): number {
  const raw: unknown = plan.state_versions_unreadable
  if (Array.isArray(raw)) return raw.length
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : 0
}

// --------------------------------------------------------------------------- //
// the flow
// --------------------------------------------------------------------------- //

interface PreviewState {
  plan: PreviewPlan | null
  digest: string
  error: StudioApiError | null
  loading: boolean
  /** True when the last confirmation was refused because the repository changed under the plan. */
  stale: boolean
}

const INITIAL: PreviewState = { plan: null, digest: '', error: null, loading: false, stale: false }

export interface PreviewFlowProps {
  api: StudioApi
  repoId: string
  kind: PreviewKind
  /** Why this flow is open — the version pair, or the recovery banner. Rendered above the plan. */
  intro?: ReactNode
  /**
   * The engine version this flow targets, known from the repo record before the preview answers.
   *
   * Without it the confirm button reads "Install AI-DLC Unavailable" for the second the preview is in
   * flight, and permanently whenever the preview is refused — which is exactly when a user is reading
   * the button most carefully.
   */
  targetVersion?: string
  /** Called with the transaction id after the 202. The caller opens the transaction drawer. */
  onStarted: (transactionId: string) => void
  onClose: () => void
}

/**
 * Preview, then confirm. Shared by install, upgrade and recovery because the three differ only in the
 * route and the words: the refusals that separate them are the server's, and re-deciding them here
 * would be a second implementation of the rules that keep a repository on one complete version.
 */
export function PreviewFlow({ api, repoId, kind, intro, targetVersion, onStarted, onClose }: PreviewFlowProps) {
  const i18n = useI18n()
  const { t } = i18n
  const [state, setState] = useState<PreviewState>(INITIAL)
  const [applying, setApplying] = useState(false)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  /**
   * Read the plan.
   *
   * `refusal` is the server's answer to a confirmation that was just rejected. It is carried through the
   * re-read rather than cleared, because "install_conflict, and here is the plan as it is now" is one
   * message: dropping the code would leave the user with a changed plan and no reason for the change.
   */
  const run = useCallback(
    async (refusal: StudioApiError | null = null) => {
      const stale = refusal !== null
      setState((was) => ({ ...was, loading: true, error: refusal, stale }))
      try {
        const answer = await api.installPreview(repoId, kind)
        if (!alive.current) return
        setState({ plan: answer.plan, digest: answer.plan_digest, error: refusal, loading: false, stale })
      } catch (caught) {
        if (!alive.current || isAbort(caught)) return
        setState({
          plan: null,
          digest: '',
          error: caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0),
          loading: false,
          stale,
        })
      }
    },
    [api, repoId, kind],
  )

  // Opening this panel IS the request for a preview; making the user press a second button to see what
  // would happen only delays the information the panel exists to show.
  useEffect(() => {
    void run()
  }, [run])

  const confirm = useCallback(async () => {
    if (!state.plan || !state.digest) return
    setApplying(true)
    try {
      const answer = await api.installApply(repoId, state.digest, kind)
      if (!alive.current) return
      setApplying(false)
      onStarted(answer.transaction_id)
    } catch (caught) {
      if (!alive.current) return
      setApplying(false)
      const error =
        caught instanceof StudioApiError
          ? caught
          : new StudioApiError('internal_error', String(caught), {}, 0)
      // `install_conflict` means the plan the user read is not the plan the server just computed — either
      // the repository changed or a new blocker appeared. Nothing was written, and the only honest next
      // step is to show the *current* plan instead of letting a second click confirm the old digest.
      if (error.code === 'install_conflict') {
        void run(error)
        return
      }
      setState((was) => ({ ...was, error }))
    }
  }, [api, repoId, kind, state.plan, state.digest, onStarted, run])

  const blocked = state.plan === null || state.plan.blocking || state.plan.state_version_blocked
  const receiptRecovery = Boolean(state.plan?.recovery_transaction_id)
  const confirmLabel =
    receiptRecovery
      ? t('install.confirm.restoreTransaction')
      : kind === 'rollback'
      ? t('install.confirm.rollback', { version: state.plan?.engine_to ?? targetVersion ?? unavailable(i18n) })
      : kind === 'uninstall'
      ? t('install.confirm.uninstall')
      : kind === 'recovery'
      ? t('install.confirm.recovery')
      : t(kind === 'install' ? 'install.confirm.install' : 'install.confirm.upgrade', {
          version: state.plan?.engine_to ?? targetVersion ?? unavailable(i18n),
        })

  return (
    <section className="studio-preview" aria-label={t(`install.preview.title.${kind}`)}>
      <div className="studio-spread studio-preview-head">
        <h2>{t(`install.preview.title.${kind}`)}</h2>
        <button type="button" className="studio-btn" disabled={applying} onClick={onClose}>
          <Icon name="close" size={14} />
          {t('install.cancel')}
        </button>
      </div>
      <p className="studio-lede">{t(`install.preview.lede.${kind}`)}</p>

      {intro}
      {receiptRecovery ? <Note tone="warn">{t('install.recovery.restoreTransaction', {
        id: state.plan!.recovery_transaction_id!,
        kind: t(`install.tx.kind.${state.plan!.recovery_kind}`),
      })}</Note> : null}

      {state.stale ? (
        <div className="studio-failure-detail" role="alert">
          <strong>{t('install.stale.title')}</strong> {t('install.stale.body')}
        </div>
      ) : null}

      {state.error ? <ErrorNote error={state.error} reassure={t('install.error.nothingWritten')} /> : null}

      {/* Polite: a preview finishing is a status change, not something that must interrupt reading. */}
      <span className="studio-sr" role="status" aria-live="polite">
        {state.loading ? t('install.preview.running') : ''}
      </span>

      {state.loading && state.plan === null ? <p className="studio-muted">{t('install.preview.running')}</p> : null}

      {state.plan ? <PlanView plan={state.plan} digest={state.digest} /> : null}

      <Block title={t('install.confirm.title')} icon="check">
        <Note>{t(receiptRecovery ? 'install.confirm.restoreTransactionNote' : kind === 'uninstall' ? 'install.confirm.uninstallNote' : kind === 'rollback' ? 'install.confirm.rollbackNote' : 'install.confirm.note')}</Note>
        {state.digest ? (
          <p className="studio-help studio-wrap-any">
            {t('install.confirm.digestNote', { digest: shortSha(state.digest) ?? '' })}
          </p>
        ) : null}
        {blocked && state.plan ? <p className="studio-note" data-tone="danger">{t('install.blocked')}</p> : null}
        <div className="studio-repo-actions">
          <button type="button" className="studio-btn" onClick={() => void run()} disabled={state.loading || applying}>
            <Icon name="refresh" size={14} />
            {state.loading ? t('install.preview.running') : t('install.preview.refresh')}
          </button>
          <button
            type="button"
            className="studio-btn"
            data-variant="primary"
            onClick={() => void confirm()}
            disabled={blocked || applying || state.loading}
          >
            <Icon name="check" size={14} />
            {confirmLabel}
          </button>
        </div>
        {/* Stated even when there is no conflict: the absence of a force control is a promise, and a
            promise the user cannot see is one they will look for in a menu. */}
        <p className="studio-help">{t('install.noForce')}</p>
      </Block>
    </section>
  )
}

export interface InstallPreviewProps {
  api: StudioApi
  repoId: string
  /** The engine version Studio bundles, from the repo record — known before the preview answers. */
  bundledVersion: string
  onStarted: (transactionId: string) => void
  onClose: () => void
}

/** First install: there is no installed version to compare against, so the intro says exactly that. */
export function InstallPreview({ api, repoId, bundledVersion, onStarted, onClose }: InstallPreviewProps) {
  const { t } = useI18n()
  return (
    <PreviewFlow
      api={api}
      repoId={repoId}
      kind="install"
      targetVersion={bundledVersion}
      onStarted={onStarted}
      onClose={onClose}
      intro={
        <div className="studio-chiplist">
          <Chip icon="install" mono>
            {t('install.version.notInstalled')}
          </Chip>
          <Icon name="chevron" size={13} />
          <Chip tone="accent" icon="install" mono>
            {bundledVersion}
          </Chip>
        </div>
      }
    />
  )
}
