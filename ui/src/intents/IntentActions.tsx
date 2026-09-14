/**
 * What you can do to one intent from the inventory — and the one thing this file deliberately cannot do.
 *
 * **Run and Resume do not send anything.** `POST …/{intent}/run` creates a `Queued` action card and
 * returns it; the message that reaches the conversation is sent by the two-phase submit in the Action
 * Center, after the user has seen the exact wire text the server chose. So this component creates the card
 * and then points at it. That is not indirection for its own sake: it is the only arrangement in which
 * "at most once, and never without the user seeing what is sent" survives a second entry point
 * (contracts §2.4, §3.6; PRD P-03).
 *
 * Pause, archive and restore are Studio-only and reversible, so they run from here — behind a confirmation
 * that states the consequence, because "no further dispatch happens for this intent" is not obvious from a
 * button label. Force stop is absent on purpose: it is a host-control delivery with a receipt to report,
 * which is the submit machine's job, not a row button's.
 *
 * The conversation button leads the row when nothing is bound. An intent with no canonical conversation
 * refuses every decision with `session_unbound` (§2.13), so on such a row binding is not one option among
 * several — it is the only act that makes any of the others reach AI-DLC.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { Icon } from '../shell/Icon'
import { useI18n } from '../i18n'
import { StudioApiError, type StudioApi } from '../lib/api'
import { plural } from '../lib/format'
import type { IntentSummary } from '../lib/types'

type Pending = 'pause' | 'unpause' | 'archive' | 'restore'

export interface IntentActionsProps {
  api: StudioApi
  intent: IntentSummary
  /** Navigate to another view (the Action Center for a queued command, the map for details). */
  onGo: (patch: { view: 'actions' | 'map'; repo: string; space?: string; intent?: string; action?: string }) => void
  /** A command card was created and is waiting in the queue, unsent. */
  onQueued: (actionId: string, intent: IntentSummary) => void
  /** Something changed on the server; the list should re-read. */
  onChanged: (message?: string) => void
  onRecompose: (intent: IntentSummary) => void
  onSettings?: (intent: IntentSummary) => void
  /**
   * Open the canonical-conversation panel for this intent.
   *
   * A slot: this row cannot run the sequence itself. Creating and titling the conversation are the
   * HOST's routes (§2.13) and binding is Studio's, so the four calls need a place that can report which
   * one refused — `SessionPanel`, opened by the view that owns the list.
   */
  onSession: (intent: IntentSummary) => void
}

export function IntentActions({ api, intent, onGo, onQueued, onChanged, onRecompose, onSession, onSettings }: IntentActionsProps) {
  const i18n = useI18n()
  const { t } = i18n
  const [pending, setPending] = useState<Pending | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<StudioApiError | null>(null)
  const confirmRef = useRef<HTMLButtonElement | null>(null)

  // A confirmation that appears out of nowhere is unusable with a keyboard, so focus follows it.
  useEffect(() => {
    if (pending) confirmRef.current?.focus()
  }, [pending])

  const fail = useCallback((caught: unknown) => {
    setError(caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0))
  }, [])

  const command = useCallback(
    async (kind: 'run' | 'resume') => {
      setBusy(true)
      setError(null)
      try {
        const answer = kind === 'run'
          ? await api.run(intent.repo_id, intent.intent_key)
          : await api.resume(intent.repo_id, intent.intent_key)
        onQueued(answer.action_id, intent)
        onChanged()
      } catch (caught) {
        fail(caught)
      } finally {
        setBusy(false)
      }
    },
    [api, intent, onQueued, onChanged, fail],
  )

  const confirm = useCallback(async () => {
    if (!pending) return
    setBusy(true)
    setError(null)
    try {
      if (pending === 'pause' || pending === 'unpause') {
        const answer = await api.pause(intent.repo_id, intent.intent_key, pending === 'pause')
        onChanged(
          pending === 'pause'
            ? plural(i18n, 'intents.paused.done', answer.blocked_actions.length)
            : t('intents.unpaused.done'),
        )
      } else if (pending === 'archive') {
        await api.archiveIntent(intent.repo_id, intent.intent_key)
        onChanged(t('intents.archived.done'))
      } else {
        await api.restoreIntent(intent.repo_id, intent.intent_key)
        onChanged(t('intents.restored.done'))
      }
      setPending(null)
    } catch (caught) {
      fail(caught)
    } finally {
      setBusy(false)
    }
  }, [api, intent, pending, onChanged, fail, i18n, t])

  const runDisabledReason = intent.archived
    ? t('intents.run.disabledArchived')
    : intent.paused
      ? t('intents.run.disabledPaused')
      : intent.session?.running
        ? t('intents.run.disabledRunning')
        : intent.counts.awaiting_approval > 0 || intent.operational_state === 'WaitingForYou'
          ? t('intents.run.disabledCheckpoint')
          : intent.disk.status?.toLowerCase() === 'completed'
            ? t('intents.run.disabledCompleted')
            : null
  const bound = Boolean(intent.session?.slot_key)
  const parked = Boolean(intent.disk.parked_at)
  const recomposable = intent.disk.status === 'Running' && !intent.archived
  const recomposeReason = recomposable
    ? null
    : t('plan.issue.recompose_not_allowed', {
        required_status: 'Running',
        status: intent.disk.status ?? t('common.unavailable'),
      })

  return (
    <div className="studio-col studio-intent-actions" role="group" aria-label={t('intents.action.a11y', { intent: intent.intent_dir })}>
      <div className="studio-row studio-wrap">
        {parked ? (
          <button
            type="button"
            className="studio-btn studio-btn-sm"
            disabled={busy || runDisabledReason !== null}
            {...(runDisabledReason ? { title: runDisabledReason } : {})}
            onClick={() => void command('resume')}
          >
            <Icon name="play" size={13} />
            {t('intents.action.resume')}
          </button>
        ) : (
          <button
            type="button"
            className="studio-btn studio-btn-sm"
            disabled={busy || runDisabledReason !== null}
            {...(runDisabledReason ? { title: runDisabledReason } : {})}
            onClick={() => void command('run')}
          >
            <Icon name="play" size={13} />
            {t('intents.action.run')}
          </button>
        )}

        {/* Primary while unbound: every decision on this intent is refused until a conversation exists. */}
        <button
          type="button"
          className={`studio-btn studio-btn-sm${bound ? '' : ' studio-btn-primary'}`}
          disabled={busy}
          onClick={() => onSession(intent)}
        >
          <Icon name="link" size={13} />
          {bound ? t('intents.action.session') : t('intents.action.bindSession')}
        </button>

        {intent.open_actions > 0 ? (
          <button
            type="button"
            className="studio-btn studio-btn-sm"
            onClick={() => onGo({ view: 'actions', repo: intent.repo_id, space: intent.space, intent: intent.intent_key })}
          >
            <Icon name="inbox" size={13} />
            {t('intents.action.openAction')}
          </button>
        ) : null}

        <button
          type="button"
          className="studio-btn studio-btn-sm"
          onClick={() => onGo({ view: 'map', repo: intent.repo_id, space: intent.space, intent: intent.intent_key })}
        >
          <Icon name="map" size={13} />
          {t('intents.action.details')}
        </button>
      </div>

      <div className="studio-row studio-wrap">
        <button
          type="button"
          className="studio-btn studio-btn-sm"
          disabled={busy || !recomposable}
          {...(recomposeReason ? { title: recomposeReason } : {})}
          onClick={() => onRecompose(intent)}
        >
          <Icon name="doc" size={13} />
          {t('intents.action.recompose')}
        </button>
        {onSettings ? (
          <button type="button" className="studio-btn studio-btn-sm" disabled={busy || intent.archived}
            onClick={() => onSettings(intent)}>
            {t('workspace.settings.title')}
          </button>
        ) : null}

        <button
          type="button"
          className="studio-btn studio-btn-sm"
          disabled={busy || intent.archived}
          onClick={() => setPending(intent.paused ? 'unpause' : 'pause')}
        >
          <Icon name={intent.paused ? 'play' : 'pause'} size={13} />
          {intent.paused ? t('intents.action.unpause') : t('intents.action.pause')}
        </button>

        <button
          type="button"
          className="studio-btn studio-btn-sm"
          disabled={busy}
          onClick={() => setPending(intent.archived ? 'restore' : 'archive')}
        >
          <Icon name="doc" size={13} />
          {intent.archived ? t('intents.action.restore') : t('intents.action.archive')}
        </button>
      </div>

      {busy ? (
        <span className="studio-muted" role="status">
          {t('intents.busy')}
        </span>
      ) : null}

      {error ? (
        <p className="studio-banner" data-tone="danger" role="alert">
          <Icon name="warn" size={13} />
          <span className="studio-grow">{error.known ? t(`errors.${error.code}`) : error.message}</span>
        </p>
      ) : null}

      {pending ? (
        <div
          className="studio-confirm"
          role="group"
          aria-label={t(`intents.confirm.${pending}.title`)}
          onKeyDown={(event) => {
            if (event.key === 'Escape') setPending(null)
          }}
        >
          <h3>{t(`intents.confirm.${pending}.title`)}</h3>
          <p>{t(`intents.confirm.${pending}.body`)}</p>
          {pending === 'pause' && intent.open_actions > 0 ? (
            <p className="studio-consequence" data-tone="warn">
              <Icon name="warn" size={13} />
              <span>{plural(i18n, 'intents.confirm.pause.blocked', intent.open_actions)}</span>
            </p>
          ) : null}
          <div className="studio-row">
            <button
              type="button"
              className="studio-btn studio-btn-primary studio-btn-sm"
              ref={confirmRef}
              disabled={busy}
              onClick={() => void confirm()}
            >
              {t('intents.confirm.go')}
            </button>
            <button type="button" className="studio-btn studio-btn-sm" disabled={busy} onClick={() => setPending(null)}>
              {t('common.cancel')}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
