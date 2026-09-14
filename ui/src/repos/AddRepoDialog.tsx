/**
 * Registration: an absolute path, a read-only preflight, then a deliberate `Register`.
 *
 * Studio never discovers repositories (FR-REP-002), so this dialog is the only way one enters the
 * registry, and the order here is the safety property:
 *
 *  1. The user types an absolute path. Nothing happens on keystroke — no probing, no listing.
 *  2. `POST /repos/preflight` reads the directory and reports. It executes nothing from it.
 *  3. `Register` stays disabled until the *server* said `can_register`. Editing the path afterwards
 *     throws the report away, so the button can never confirm a preflight for a different path.
 *
 * A path that resolves to an already-registered identity is refused with the name of the row that owns
 * it and a way to open that row (FR-REP-005) — not silently merged, and not registered twice, because
 * leases are keyed on the identity and two rows for one repository would let it run two turns at once.
 *
 * Desktop-only by construction: the page does not render this dialog below 900px (PRD §8.4).
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { useI18n } from '../i18n'
import { StudioApiError, type StudioApi } from '../lib/api'
import type { PreflightReport, RepoRecord } from '../lib/types'
import { Icon } from '../shell/Icon'
import { ErrorNote, Note, PreflightReportView } from './PreflightReport'
import { DirectoryPicker } from './DirectoryPicker'

/**
 * Focusable descendants, in DOM order — the tab ring for the modal.
 *
 * No visibility filter: everything in this dialog is either rendered or not rendered, never hidden with
 * CSS, so a layout-based check would only add a way for the ring to disagree with what is on screen.
 */
function focusable(root: HTMLElement): HTMLElement[] {
  return [
    ...root.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
    ),
  ]
}

export interface AddRepoDialogProps {
  api: StudioApi
  /** Registered repositories, so a duplicate can be named rather than shown as an id. */
  repos: RepoRecord[]
  onClose: () => void
  onRegistered: (repo: RepoRecord) => void
  /** Jump to the repository that already owns the identity the user typed. */
  onOpenRepo: (repoId: string) => void
}

export function AddRepoDialog({ api, repos, onClose, onRegistered, onOpenRepo }: AddRepoDialogProps) {
  const { t } = useI18n()
  const [path, setPath] = useState('')
  const [label, setLabel] = useState('')
  const [report, setReport] = useState<PreflightReport | null>(null)
  const [error, setError] = useState<StudioApiError | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)
  const [phase, setPhase] = useState<'idle' | 'preflight' | 'registering'>('idle')
  /** Set from a `duplicate_identity` refusal on POST /repos, which preflight may not have seen. */
  const [duplicateOf, setDuplicateOf] = useState<string | null>(null)

  const dialog = useRef<HTMLDivElement | null>(null)
  const input = useRef<HTMLInputElement | null>(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    // Focus moves in and comes back: closing a modal that leaves focus on `<body>` drops a keyboard user
    // at the top of the page instead of on the control they pressed.
    const opener = document.activeElement
    input.current?.focus()
    return () => {
      alive.current = false
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus()
    }
  }, [])

  // Escape closes, and Tab cycles inside: a modal the keyboard can walk out of leaves the user typing a
  // path into a control they cannot see.
  const onKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
        return
      }
      if (event.key !== 'Tab' || !dialog.current) return
      const items = focusable(dialog.current)
      if (items.length === 0) return
      const first = items[0]
      const last = items[items.length - 1]
      if (!first || !last) return
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    },
    [onClose],
  )

  const changePath = useCallback((next: string) => {
    setPath(next)
    // A report belongs to the path it was read for. Keeping it would let `Register` confirm a preflight
    // of a different directory — the exact class of mistake this dialog exists to make impossible.
    setReport(null)
    setError(null)
    setLocalError(null)
    setDuplicateOf(null)
  }, [])

  const runPreflight = useCallback(async () => {
    const trimmed = path.trim()
    if (!trimmed) {
      setLocalError(t('repos.add.pathRequired'))
      return
    }
    if (!trimmed.startsWith('/') && !trimmed.startsWith('~')) {
      setLocalError(t('repos.add.pathNotAbsolute'))
      return
    }
    setLocalError(null)
    setError(null)
    setPhase('preflight')
    try {
      const answer = await api.preflight(trimmed)
      if (!alive.current) return
      setReport(answer.preflight)
      setDuplicateOf(answer.preflight.duplicate_of)
    } catch (caught) {
      if (!alive.current) return
      setReport(null)
      setError(caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0))
    } finally {
      if (alive.current) setPhase('idle')
    }
  }, [api, path, t])

  const register = useCallback(async () => {
    if (!report || !report.can_register) return
    setPhase('registering')
    setError(null)
    try {
      const answer = await api.addRepo(path.trim(), label.trim() || null)
      if (!alive.current) return
      onRegistered(answer.repo)
    } catch (caught) {
      if (!alive.current) return
      const decoded =
        caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0)
      setError(decoded)
      // The registry is the authority on uniqueness, and it re-checks at insert time: a directory can
      // become a duplicate between the preflight and the click (a rebind elsewhere, a symlink change).
      if (decoded.code === 'duplicate_identity') {
        const owner = decoded.details['repo_id']
        setDuplicateOf(typeof owner === 'string' ? owner : null)
      }
    } finally {
      if (alive.current) setPhase('idle')
    }
  }, [api, path, label, report, onRegistered])

  const duplicate = duplicateOf ? repos.find((repo) => repo.repo_id === duplicateOf) ?? null : null
  const duplicateLabel = duplicate?.label ?? duplicateOf
  const canRegister = report !== null && report.can_register && phase === 'idle'

  return (
    // The scrim is not a click target: dismissing a half-filled path by clicking past the dialog is a
    // way to lose work, and Cancel and Escape are both right there.
    <div className="studio-modal-scrim">
      <div
        className="studio-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="studio-add-repo-title"
        ref={dialog}
        onKeyDown={onKeyDown}
      >
        <div className="studio-spread studio-preview-head">
          <h2 id="studio-add-repo-title">{t('repos.add.title')}</h2>
          <button type="button" className="studio-icon-btn" onClick={onClose} aria-label={t('common.close')}>
            <Icon name="close" size={16} />
          </button>
        </div>

        <div className="studio-field">
          <label htmlFor="studio-add-repo-path">{t('repos.add.pathLabel')}</label>
          <input
            id="studio-add-repo-path"
            ref={input}
            className="studio-input studio-mono"
            type="text"
            spellCheck={false}
            autoComplete="off"
            value={path}
            aria-describedby="studio-add-repo-path-help"
            {...(localError ? { 'aria-invalid': true } : {})}
            onChange={(event) => changePath(event.target.value)}
          />
          <p className="studio-help" id="studio-add-repo-path-help">
            {t('repos.add.pathHelp')}
          </p>
          <DirectoryPicker api={api} initialPath={path} onSelect={changePath} disabled={phase !== 'idle'} />
          {localError ? (
            <p className="studio-note" data-tone="danger" role="alert">
              <Icon name="warn" size={13} /> {localError}
            </p>
          ) : null}
        </div>

        <div className="studio-field">
          <label htmlFor="studio-add-repo-label">{t('repos.add.labelLabel')}</label>
          <input
            id="studio-add-repo-label"
            className="studio-input"
            type="text"
            value={label}
            aria-describedby="studio-add-repo-label-help"
            onChange={(event) => setLabel(event.target.value)}
          />
          <p className="studio-help" id="studio-add-repo-label-help">
            {t('repos.add.labelHelp')}
          </p>
        </div>

        <div className="studio-repo-actions">
          <button
            type="button"
            className="studio-btn"
            onClick={() => void runPreflight()}
            disabled={phase !== 'idle'}
          >
            <Icon name="search" size={14} />
            {phase === 'preflight' ? t('repos.add.preflightRunning') : t('repos.add.preflight')}
          </button>
        </div>

        {error ? <ErrorNote error={error} /> : null}

        {report ? (
          <PreflightReportView report={report} duplicateLabel={duplicateLabel}>
            {duplicate ? (
              <div className="studio-repo-actions">
                <button
                  type="button"
                  className="studio-btn"
                  onClick={() => onOpenRepo(duplicate.repo_id)}
                >
                  <Icon name="external" size={14} />
                  {t('repos.add.duplicate.open', { label: duplicate.label })}
                </button>
              </div>
            ) : null}
          </PreflightReportView>
        ) : (
          <Note>{t('repos.add.needPreflight')}</Note>
        )}

        {duplicateOf && !report?.duplicate_of ? (
          <div className="studio-failure-detail" role="alert">
            <strong>{t('repos.add.duplicate.title')}</strong>{' '}
            {t('repos.add.duplicate.body', { label: duplicateLabel ?? '' })}
          </div>
        ) : null}

        <div className="studio-repo-actions studio-modal-actions">
          <button type="button" className="studio-btn" onClick={onClose}>
            {t('install.cancel')}
          </button>
          <button
            type="button"
            className="studio-btn"
            data-variant="primary"
            onClick={() => void register()}
            disabled={!canRegister}
          >
            <Icon name="check" size={14} />
            {phase === 'registering' ? t('repos.add.registering') : t('repos.add.register')}
          </button>
        </div>
      </div>
    </div>
  )
}
