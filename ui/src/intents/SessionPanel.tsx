/**
 * Bind an intent to its canonical conversation — the sequence nothing else in Studio performs.
 *
 * Studio's human lane delivers a decision by writing it as the user's OWN turn into one KiroCrew chat
 * slot bound to the intent: agent `aidlc`, project the repository (§2.13, §1.12 step 5). Until such a
 * slot is bound, every decision on the intent is refused `409 session_unbound` and there is no way
 * forward — so on a fresh intent the whole product is unusable without this panel.
 *
 * Studio's backend deliberately cannot do it: `SessionBinder` must NOT create host slots (§1.11) — it
 * verifies and records what the UI created, with the owner's own cookie, under the second declared
 * prefix `/api/chat`. That is why the four steps live in a view:
 *
 *   1. `POST   /api/chat/slots {"name": <canonical>, "agent": "aidlc"}`   (host)
 *   2. `PATCH  /api/chat/slots/{key}/title {"title": "<repo label> / <intent slug>"}` (host)
 *   3. `POST   /api/chat/slots/{key}/project {"project": <canonical_path>}` (host; 403 when sensitive)
 *   4. `POST   …/{intent}/session/bind {"slot_key": key}`                  (Studio; verifies 1-3)
 *
 * The order is load-bearing, not cosmetic: the project must be set before Studio checks it (§2.13), and
 * step 4 refuses `slot_mismatch` unless `slot.agent == "aidlc"` and `realpath(slot.project) ==
 * repo.canonical_path` (§1.11). Each failure is reported BY STEP NUMBER together with the server's own
 * words, because "could not bind" without naming the host call that refused is precisely the silence
 * that made this capability look absent.
 *
 * Adopting an existing conversation is offered beside creating one: a user who already has an `aidlc`
 * chat open on this repository must not be pushed into a second one. The list is filtered by the same
 * two rules `bind` enforces, so a slot Studio would refuse is never offered. Moving an already-bound
 * intent uses `…/session/takeover/preview` and its candidates verbatim, reasons included.
 *
 * Nothing here is a repair: every refusal is rendered, never swallowed, and the panel never re-points a
 * slot behind the user's back.
 */

import { useCallback, useState, type ReactNode } from 'react'

import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { useI18n } from '../i18n'
import { StudioApiError, type StudioApi } from '../lib/api'
import { unavailable } from '../lib/format'
import type { BindingView, IntentSummary, RepoRecord, SessionRef, SlotView } from '../lib/types'

/** The agent an AI-DLC conversation runs. `SessionBinder.bind` refuses every other one (§1.11). */
const AIDLC_AGENT = 'aidlc'

/** How many calls create-and-bind takes. The copy counts them, so the user can name the one that failed. */
const STEPS = 4

/** Step number → the catalogue key that says what that step does. Explicit, so both are greppable. */
const STEP_KEYS = [
  'intents.session.step.create',
  'intents.session.step.title',
  'intents.session.step.project',
  'intents.session.step.bind',
] as const

/**
 * `constants.SLOT_KEY_TEMPLATE` (`aidlc-studio-{repo_id}-{intent_dir}`) mirrored, because the slot is
 * created here and Studio only recognises that name: `SessionBinder.canonical_slot_name` derives the
 * same string, and `takeover_preview` finds a slot "belonging to this intent" by that prefix (§1.11).
 * A different shape would still bind — the check is on project and agent — but the intent's own
 * conversation would then be invisible to the takeover search.
 */
export function canonicalSlotKey(repoId: string, intentDir: string): string {
  return `aidlc-studio-${repoId}-${intentDir}`
}

/** Normalised for comparison: the host reports realpaths, and a trailing slash is not a difference. */
function samePath(a: string, b: string): boolean {
  const trim = (value: string) => value.replace(/\/+$/, '')
  return trim(a) === trim(b)
}

/**
 * The slots this intent may be bound to, by the two rules the backend enforces.
 *
 * A positive match on both is required, not merely "no contradiction": an offer that the server would
 * answer `slot_mismatch` to is worse than no offer, and a slot with no project cannot pass
 * `realpath(slot.project) == repo.canonical_path` anyway.
 */
export function adoptableSlots(slots: SlotView[], canonicalPath: string): SlotView[] {
  return slots.filter(
    (slot) => slot.agent === AIDLC_AGENT && !!slot.project && samePath(slot.project, canonicalPath),
  )
}

/** What the panel needs to show about a live binding, from `SessionRef` or from a `BindingView`. */
interface Bound {
  slotKey: string
  sessionKey: string
  running: boolean
}

function fromSession(session: SessionRef | null): Bound | null {
  if (!session || !session.slot_key) return null
  return { slotKey: session.slot_key, sessionKey: session.session_key, running: session.running }
}

export interface SessionPanelProps {
  api: StudioApi
  /** The intent's repository: `canonical_path` is what the slot's project must equal, `label` titles it. */
  repo: RepoRecord
  intent: IntentSummary
  onClose: () => void
  /** A binding changed on the server, so the list that owns this panel should re-read. */
  onChanged: (message?: string) => void
}

type Busy = 'create' | 'adopt' | 'list' | 'unbind' | 'move' | 'candidates'

export function SessionPanel({ api, repo, intent, onClose, onChanged }: SessionPanelProps) {
  const i18n = useI18n()
  const { t } = i18n
  const [busy, setBusy] = useState<Busy | null>(null)
  /** Which of the four steps is running, and which one refused. 0 means "not in that sequence". */
  const [at, setAt] = useState(0)
  const [failedAt, setFailedAt] = useState(0)
  const [error, setError] = useState<StudioApiError | null>(null)
  const [slots, setSlots] = useState<SlotView[] | null>(null)
  const [candidates, setCandidates] = useState<{ slot: SlotView; reason: string }[] | null>(null)
  /**
   * The binding the server just confirmed, which outranks the inventory row this panel was opened from:
   * that row is one poll behind, and a user who has just bound a conversation must not be shown the
   * unbound state until a refresh happens to land.
   */
  const [confirmed, setConfirmed] = useState<{ value: Bound | null } | null>(null)

  const bound = confirmed ? confirmed.value : fromSession(intent.session)
  const wanted = canonicalSlotKey(intent.repo_id, intent.intent_dir)
  const stepLabel = (step: number): string => t(STEP_KEYS[step - 1] ?? STEP_KEYS[0])

  const fail = useCallback((caught: unknown, step: number) => {
    setFailedAt(step)
    setError(
      caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0),
    )
  }, [])

  const remember = useCallback((binding: BindingView, slot?: SlotView | null) => {
    setConfirmed({
      value: binding.slot_key
        ? {
            slotKey: binding.slot_key,
            sessionKey: binding.session_key ?? slot?.session_key ?? '',
            running: slot?.running ?? false,
          }
        : null,
    })
  }, [])

  const start = useCallback((kind: Busy) => {
    setBusy(kind)
    setError(null)
    setFailedAt(0)
  }, [])

  /** The whole sequence, in order, reporting the step that refused. */
  const create = useCallback(async () => {
    start('create')
    let step = 1
    setAt(step)
    try {
      const created = await api.createSlot(wanted, AIDLC_AGENT)
      // The host answers with the slot it made; its own `key` is what every later call must address,
      // even if it normalised the name we asked for.
      const answered = created['key']
      const key = typeof answered === 'string' && answered ? answered : wanted
      step = 2
      setAt(step)
      await api.setSlotTitle(key, `${repo.label} / ${intent.slug || intent.intent_dir}`)
      step = 3
      setAt(step)
      await api.setSlotProject(key, repo.canonical_path)
      step = 4
      setAt(step)
      const answer = await api.bindSession(intent.repo_id, intent.intent_key, key)
      remember(answer.binding, answer.slot)
      setSlots(null)
      onChanged(t('intents.session.done.bound', { slot: answer.binding.slot_key ?? key }))
    } catch (caught) {
      fail(caught, step)
    } finally {
      setBusy(null)
      setAt(0)
    }
  }, [api, wanted, repo, intent, remember, onChanged, t, fail, start])

  const list = useCallback(async () => {
    start('list')
    try {
      setSlots(await api.listSlots())
    } catch (caught) {
      fail(caught, 0)
    } finally {
      setBusy(null)
    }
  }, [api, fail, start])

  const adopt = useCallback(
    async (slotKey: string) => {
      start('adopt')
      try {
        const answer = await api.bindSession(intent.repo_id, intent.intent_key, slotKey)
        remember(answer.binding, answer.slot)
        setSlots(null)
        onChanged(t('intents.session.done.bound', { slot: answer.binding.slot_key ?? slotKey }))
      } catch (caught) {
        // Step 4 by any other route is still step 4: the refusal is the same `slot_mismatch`.
        fail(caught, STEPS)
      } finally {
        setBusy(null)
      }
    },
    [api, intent, remember, onChanged, t, fail, start],
  )

  const unbind = useCallback(async () => {
    start('unbind')
    try {
      const answer = await api.unbindSession(intent.repo_id, intent.intent_key)
      remember(answer.binding)
      setCandidates(null)
      onChanged(t('intents.session.done.unbound'))
    } catch (caught) {
      fail(caught, 0)
    } finally {
      setBusy(null)
    }
  }, [api, intent, remember, onChanged, t, fail, start])

  const preview = useCallback(async () => {
    start('candidates')
    try {
      const answer = await api.takeoverPreview(intent.repo_id, intent.intent_key)
      setCandidates(answer.candidates)
    } catch (caught) {
      fail(caught, 0)
    } finally {
      setBusy(null)
    }
  }, [api, intent, fail, start])

  const move = useCallback(
    async (slotKey: string) => {
      start('move')
      try {
        const answer = await api.takeover(intent.repo_id, intent.intent_key, slotKey)
        remember(answer.binding)
        setCandidates(null)
        onChanged(t('intents.session.done.moved', { slot: answer.binding.slot_key ?? slotKey }))
      } catch (caught) {
        fail(caught, 0)
      } finally {
        setBusy(null)
      }
    },
    [api, intent, remember, onChanged, t, fail, start],
  )

  const offers = slots ? adoptableSlots(slots, repo.canonical_path) : null
  const catalogued = error && error.known ? t(`errors.${error.code}`) : null

  return (
    <section className="studio-panel" aria-label={t('intents.session.a11y', { intent: intent.intent_dir })}>
      <header className="studio-panel-head">
        <h2>
          <Icon name="link" size={15} />
          {t('intents.session.title', { intent: intent.intent_dir })}
        </h2>
        <div className="studio-row studio-panel-actions">
          <button type="button" className="studio-btn" onClick={onClose}>
            {t('common.close')}
          </button>
        </div>
      </header>

      <p className="studio-lede">{bound ? t('intents.session.boundLede') : t('intents.session.lede', { agent: AIDLC_AGENT })}</p>

      {bound ? (
        <>
          <Facts
            rows={[
              { key: 'slot', label: t('intents.session.fact.slot'), value: bound.slotKey, mono: true },
              { key: 'session', label: t('intents.session.fact.session'), value: bound.sessionKey, mono: true },
              { key: 'project', label: t('intents.session.fact.project'), value: repo.canonical_path, mono: true },
              {
                key: 'state',
                label: t('intents.session.fact.state'),
                value: (
                  <Chip icon={bound.running ? 'play' : 'clock'} tone={bound.running ? 'ok' : 'neutral'}>
                    {bound.running ? t('intents.session.running') : t('intents.session.idle')}
                  </Chip>
                ),
              },
            ]}
          />

          <div className="studio-row studio-wrap">
            <button type="button" className="studio-btn" disabled={busy !== null} onClick={() => void unbind()}>
              <Icon name="close" size={13} />
              {t('intents.session.unbind')}
            </button>
            <button type="button" className="studio-btn" disabled={busy !== null} onClick={() => void preview()}>
              <Icon name="refresh" size={13} />
              {t('intents.session.move')}
            </button>
          </div>

          <p className="studio-consequence">
            <Icon name="info" size={13} />
            <span>{t('intents.session.unbindWhy')}</span>
          </p>
        </>
      ) : (
        <>
          <Facts
            rows={[
              { key: 'repo', label: t('intents.session.fact.repo'), value: repo.label },
              { key: 'project', label: t('intents.session.fact.project'), value: repo.canonical_path, mono: true },
              { key: 'agent', label: t('intents.session.fact.agent'), value: AIDLC_AGENT, mono: true },
              { key: 'slot', label: t('intents.session.fact.willCreate'), value: wanted, mono: true },
            ]}
          />

          <div className="studio-row studio-wrap">
            <button
              type="button"
              className="studio-btn studio-btn-primary"
              disabled={busy !== null}
              onClick={() => void create()}
            >
              <Icon name="plus" size={13} />
              {t('intents.session.create')}
            </button>
            <button type="button" className="studio-btn" disabled={busy !== null} onClick={() => void list()}>
              <Icon name="link" size={13} />
              {t('intents.session.adopt')}
            </button>
          </div>
        </>
      )}

      {busy === 'create' && at > 0 ? (
        <p className="studio-muted" role="status">
          {t('intents.session.busy', { step: at, total: STEPS, what: stepLabel(at) })}
        </p>
      ) : busy !== null ? (
        <p className="studio-muted" role="status">
          {t('intents.busy')}
        </p>
      ) : null}

      {error ? (
        <p className="studio-banner" data-tone="danger" role="alert">
          <Icon name="warn" size={15} />
          <span className="studio-grow">
            {failedAt > 0
              ? `${t('intents.session.failedAt', { step: failedAt, total: STEPS, what: stepLabel(failedAt) })} `
              : ''}
            {catalogued ?? error.message}
          </span>
        </p>
      ) : null}

      {/* The host's and Studio's own prose, verbatim: `errors.<code>` is Studio's sentence for a code,
          and a 403 on a sensitive path arrives with no code at all, so the server's words are the only
          information left about which path was refused. */}
      {error && catalogued && error.message && error.message !== catalogued ? (
        <p className="studio-failure-detail" role="status">
          <Icon name="info" size={13} />{' '}
          <span className="studio-mono studio-wrap-any">
            {t('intents.session.serverSaid')} {error.message}
          </span>
        </p>
      ) : null}

      {offers ? (
        <section className="studio-block">
          <h3>
            <Icon name="link" size={13} />
            {t('intents.session.adoptTitle')}
          </h3>
          <p className="studio-muted">{t('intents.session.adoptLede', { agent: AIDLC_AGENT })}</p>
          {offers.length === 0 ? (
            <p className="studio-consequence">
              <Icon name="info" size={13} />
              <span>{t('intents.session.adoptNone', { agent: AIDLC_AGENT })}</span>
            </p>
          ) : (
            <ul className="studio-choicelist">
              {offers.map((slot) => (
                <li key={slot.key}>
                  <span className="studio-choice-body studio-grow">
                    <span className="studio-choice-label studio-mono studio-wrap-any">{slot.key}</span>
                    {slot.title ? <span className="studio-choice-desc">{slot.title}</span> : null}
                    <span className="studio-choice-desc studio-mono studio-wrap-any">{slot.project}</span>
                  </span>
                  <button
                    type="button"
                    className="studio-btn studio-btn-sm"
                    disabled={busy !== null}
                    onClick={() => void adopt(slot.key)}
                  >
                    {t('intents.session.adoptGo')}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}

      {candidates ? (
        <section className="studio-block">
          <h3>
            <Icon name="refresh" size={13} />
            {t('intents.session.moveTitle')}
          </h3>
          <p className="studio-muted">{t('intents.session.moveLede')}</p>
          {candidates.length === 0 ? (
            <p className="studio-consequence">
              <Icon name="info" size={13} />
              <span>{t('intents.session.moveNone')}</span>
            </p>
          ) : (
            <ul className="studio-choicelist">
              {candidates.map((candidate) => (
                <li key={candidate.slot.key}>
                  <span className="studio-choice-body studio-grow">
                    <span className="studio-choice-label studio-mono studio-wrap-any">{candidate.slot.key}</span>
                    {/* The reason is the backend's own word; an unrecognised one is shown as it came. */}
                    <span className="studio-choice-desc">
                      {i18n.has(`intents.session.reason.${candidate.reason}`)
                        ? t(`intents.session.reason.${candidate.reason}`)
                        : candidate.reason}
                    </span>
                    <span className="studio-choice-desc studio-mono studio-wrap-any">{candidate.slot.project}</span>
                  </span>
                  <button
                    type="button"
                    className="studio-btn studio-btn-sm"
                    disabled={busy !== null}
                    onClick={() => void move(candidate.slot.key)}
                  >
                    {t('intents.session.moveGo')}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : null}
    </section>
  )
}

/**
 * Label/value pairs as a real `<dl>`.
 *
 * A slot key, a session key and a canonical path are names and values, not tabular data: a screen
 * reader reading "row 2, column 2" for a path is worse than reading the term it belongs to.
 */
function Facts({ rows }: { rows: { key: string; label: string; value: ReactNode; mono?: boolean }[] }) {
  const i18n = useI18n()
  return (
    <dl className="studio-facts">
      {rows.map((row) => (
        <div key={row.key} className="studio-fact">
          <dt>{row.label}</dt>
          <dd className={row.mono ? 'studio-mono studio-wrap-any' : undefined}>
            {row.value === null || row.value === undefined || row.value === '' ? unavailable(i18n) : row.value}
          </dd>
        </div>
      ))}
    </dl>
  )
}
