/**
 * The client half of the two-phase human lane (contracts §2.4, §2.13, §3.6).
 *
 * Studio's whole at-most-once story runs through this file, so it is written around one invariant:
 * **the message is sent at most once, and never automatically.** Everything else — retries, error
 * decoding, state shape — exists to serve that.
 *
 * The sequence, and why each step is where it is:
 *
 *  1. `POST /actions/{id}/submit` with the capture the user confirmed against and the wire text they
 *     confirmed. The server recomputes the wire text and refuses a mismatch, so a UI can never choose
 *     the bytes that land in someone's audit trail. A `409 action_stale` carries the refreshed card;
 *     the caller must show it and require a *fresh* confirmation.
 *  2. **Slot preflight** (human lane only, review R08). The host's `POST /api/chat` silently
 *     `get_or_create_slot`s an unknown name, so a send to a slot that has gone away would create a
 *     brand-new empty conversation and report success. The slot listing is therefore checked first,
 *     and a missing or re-pointed slot is reported as `not_delivered` *without sending*.
 *  3. The send itself, with `receipt.host.body` passed through byte-for-byte. Classified once:
 *     `ok:true` → delivered, an authoritative 4xx → not_delivered, anything else (5xx, network,
 *     timeout, a 200 that does not say ok) → uncertain.
 *  4. `POST /actions/{id}/delivery`. This one *is* retried — it is idempotent per `delivery_id` and
 *     the backend needs the outcome to release the lease — but only on a failure that proves the
 *     report never landed (network, 5xx). The broker's own refusal is its decision and is final; any
 *     other 4xx never reached it, and leaves the outcome unrecorded.
 *     A send reported `uncertain` because it outlived the timeout is reported again when the host
 *     answers, under the same `delivery_id`: a late `delivered` closes Send it again on the backend.
 *     Until that answer, or the receipt's `expires_at`, the action is still in flight.
 *
 * Step 3 is never re-run for the same `delivery_id`. There is no automatic retry of a send anywhere in
 * this module, and a second `submit()` for an action id that is already in flight is refused by a
 * module-level guard rather than a component's state — two mounted panes, or a fast double-click
 * across a re-render, must not be able to produce two `Delivering` records.
 */

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'

import { StudioApiError, type StudioApi } from '../lib/api'
import type { ErrorCode } from '../lib/errorCodes.generated'
import { WIRE } from '../lib/wire.generated'
import type {
  ActionCard, AnswerInput, Captured, HostChatReceipt, HostSlot, Question, QuestionsView, ResolvePayload,
  SubmitPayload, SubmitReceipt, SubmitRequest,
} from '../lib/types'

// --------------------------------------------------------------------------- //
// wire text
// --------------------------------------------------------------------------- //

/**
 * Bounds mirrored from `constants.MAX_FEEDBACK_CHARS` / `MAX_ANSWER_CHARS`.
 *
 * Not generated, because they are not protocol text: they only decide whether the UI refuses before
 * the round trip or the server answers `too_large`. A drift therefore costs a worse error message,
 * never a wrong send.
 */
export const MAX_FEEDBACK_CHARS = 8000
export const MAX_ANSWER_CHARS = 8000

/**
 * A length as the server measures it against those bounds: Python's `len` counts code points, and
 * `.length` counts UTF-16 units, which refused 5000 emoji the server accepts.
 */
export function charCount(text: string): number {
  return [...text].length
}
/** Mirrors `constants.AUDIT_NONE_LABEL`: Studio's own empty choice on a same-turn audit question (A37). */
export const AUDIT_NONE_LABEL = 'None of these'

/**
 * A pinned template filled in one pass, as Python's `str.format` fills it. `String.replace` with a string
 * reads `$&` and `$$` in the value as patterns, and a second `.replace` finds a placeholder inside the
 * first value: either way the browser would show bytes the server then refuses.
 */
function fill(template: string, values: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (placeholder, key: string) => values[key] ?? placeholder)
}

/**
 * A questions-file answer naming each option as the file does (`_file_answer_text` in `actions.py`):
 * `B. Aurora`, `X. Other (please specify): <their words>`, several as `A, C — A. <label>; C. <label>`.
 * Never a bare letter, which a revision may re-letter (FR-Q-007), and never a bare label: the conductor
 * takes words that match an option for that option, and labels joined by commas cannot be told apart
 * from labels with them.
 */
function fileAnswerText(question: Question, letters: string[], free: string): string {
  const picked = question.options.filter((option) => letters.includes(option.letter))
  const chosen = [...picked.filter((option) => !option.is_other), ...picked.filter((option) => option.is_other)]
  const parts = chosen.map((option) => fill(WIRE.OPTION_ANSWER, { letter: option.letter, label: option.text }))
  // Last, so everything after its marker is the person's, whatever their words contain.
  const last = chosen.length - 1
  if (chosen[last]?.is_other) parts[last] = fill(WIRE.OTHER_ANSWER, { option: parts[last] ?? '', text: free })
  if (parts.length === 1) return parts[0] ?? ''
  return fill(WIRE.MULTI_SELECT_ANSWER, {
    letters: chosen.map((option) => option.letter).join(WIRE.MULTI_SELECT_JOINER),
    options: parts.join(WIRE.MULTI_SELECT_OPTION_JOINER),
  })
}

/**
 * One answer as the conductor will read it, or `null` when it cannot be encoded.
 *
 * A questions file's options go by letter and label (`fileAnswerText`); an audit question's
 * (`fromFile` false) by its exact recorded labels, which its receipt must repeat. `is_other` and the
 * option-less (free-form) question both send the human's own words — this mirrors
 * `_answers_wire_text` in `actions.py` exactly, because `client_wire_text` is compared byte-for-byte.
 */
export function answerText(answer: AnswerInput, question: Question, textInput = false, fromFile = true): string | null {
  const known = new Map(question.options.map((option) => [option.letter, option]))
  const letters = answer.option_letters
  if (known.size !== question.options.length || new Set(letters).size !== letters.length) return null
  if (letters.some((letter) => !known.has(letter))) return null
  if (letters.length > 1 && !question.multi_select) return null
  const free = (answer.free_text ?? '').trim()
  if (textInput && /^free text(?: note)?$/.test(free.toLowerCase().replace(/-/g, ' ').replace(/\s+/g, ' '))) return null
  // Nothing chosen answers only a question that offers nothing to choose: on any other it is text left in
  // an `Other` box that was unticked again.
  if (letters.length === 0 && question.options.length > 0) return null
  const chosenOther = letters.some((letter) => known.get(letter)?.is_other === true)
  if ((chosenOther || letters.length === 0) && !free) return null
  const text = letters.length === 0 ? free : fromFile ? fileAnswerText(question, letters, free) : letters
    .map((letter) => known.get(letter)?.is_other ? free : known.get(letter)?.text ?? '')
    .join(WIRE.MULTI_SELECT_JOINER)
  if (!text) return null
  return charCount(text) > MAX_ANSWER_CHARS ? null : text
}

/** The pending questions of a group, in file order — the set one submit has to answer together. */
export function pendingQuestions(questions: QuestionsView | null): Question[] {
  return (questions?.questions ?? []).filter((question) => !question.answered)
}

function feedbackText(raw: string): string | null {
  const text = raw.trim()
  if (!text || charCount(text) > MAX_FEEDBACK_CHARS) return null
  return text
}

/**
 * Exactly the bytes `POST /api/chat` will carry, or `null` when the payload cannot produce any.
 *
 * `null` is not an error to hide: it is what disables the send control and names the reason (blank
 * feedback, an unanswered question, a grouped answer while the capability is off). The mirror of
 * `wire_text_for` in `actions.py`; the pinned constants come from `wire.generated.ts`.
 */
export function wireTextFor(
  payload: SubmitPayload,
  questions: QuestionsView | null,
  groupedAnswers: boolean,
): string | null {
  // A cached card can still offer question decisions after an unsupported follow-up appears.
  // Such sections have no safe Q mapping; checkpoint confirmations must not skip them either.
  if ((questions?.unsupported_pending_count ?? 0) > 0 && [
    'answers', 'confirm_summary', 'accept_assumptions', 'convert_assumptions', 'approve_plan', 'request_plan_changes',
  ].includes(payload.decision)) return null
  switch (payload.decision) {
    case 'approve':
      return WIRE.APPROVE
    case 'accept_as_is':
      return WIRE.ACCEPT_AS_IS
    case 'request_changes':
    case 'request_plan_changes': {
      const text = feedbackText(payload.feedback)
      return text === null ? null : WIRE.REQUEST_CHANGES_PREFIX + text
    }
    case 'approve_plan':
      return WIRE.APPROVE_PLAN
    case 'accept_assumptions':
      return WIRE.ACCEPT_ASSUMPTIONS
    case 'convert_assumptions':
      return WIRE.CONVERT_ASSUMPTIONS
    case 'confirm_summary': {
      if (payload.choice === 'looks_correct') return WIRE.LOOKS_CORRECT
      const text = feedbackText(payload.feedback)
      return text === null ? null : WIRE.SUMMARY_REQUEST_CHANGES_PREFIX + text
    }
    case 'answers': {
      if (questions?.mode === 'degraded') return null
      const pending = pendingQuestions(questions)
      if (pending.length === 0) return null
      const indexes = new Set(pending.map((question) => question.index))
      if (indexes.size !== pending.length || pending.some((q) => !Number.isInteger(q.index) || q.index < 1)) return null
      if (payload.answers.length !== pending.length ||
          new Set(payload.answers.map((answer) => answer.index)).size !== pending.length ||
          payload.answers.some((answer) => !indexes.has(answer.index))) return null
      const texts: string[] = []
      for (const question of pending) {
        const answer = payload.answers.find((candidate) => candidate.index === question.index)
        const text = answer ? answerText(
          answer, question, questions?.origin?.kind === 'audit'
            && question.options.length === 1 && question.options[0]?.is_other === true,
          !questions?.origin,
        ) : null
        if (text === null) return null
        texts.push(text)
      }
      // AI-DLC's mode question is still open beside the file: the reply chooses Chat too, and says so (`wire_text_for`).
      const mode = !questions?.origin && questions?.answer_mode_pending === true ? WIRE.ANSWER_MODE_CHAT_NOTE : ''
      if (texts.length === 1) return texts[0] === undefined ? null : texts[0] + mode
      const batch = questions?.origin?.kind === 'audit' && (questions.origin.batch_size ?? 1) > 1
      const lines = pending
        .map((question, position) =>
          // The file's own name for a prefixed question (RMQ1), else Q<n>: the same label `wire_text_for` sends.
          fill(WIRE.ANSWER_LINE, { label: question.label ?? `Q${question.index}`, answer: texts[position] ?? '' }),
        )
        // An audit menu's answers in the engine's one-line form (`Q1: …; Q2: …`); a file's, one per [Answer] tag.
        .join(batch ? WIRE.AUDIT_ANSWER_JOINER : WIRE.ANSWER_JOINER)
      // Questions AI-DLC asked together in one turn take one reply, with no capability gate (A37).
      if (batch) {
        const contradictory = payload.answers.some((answer) => {
          const question = pending.find((candidate) => candidate.index === answer.index)
          return answer.option_letters.length > 1 && answer.option_letters.some((letter) =>
            question?.options.find((option) => option.letter === letter)?.text === AUDIT_NONE_LABEL)
        })
        return contradictory ? null : lines + WIRE.AUDIT_GROUPED_ANSWER_SUFFIX
      }
      // The backend advertises compatibility for this file-backed grouped reply.
      if (!groupedAnswers) return null
      return lines + mode + WIRE.GROUPED_ANSWER_SUFFIX
    }
    case 'provide_input': {
      if (payload.kind === 'scope') {
        const scope = feedbackText(payload.scope)
        return scope === null ? null : WIRE.SCOPE_PREFIX + scope
      }
      return feedbackText(payload.text)
    }
    case 'run':
      return WIRE.RUN
    case 'resume':
      return WIRE.RESUME
    case 'prepare_commit':
      return WIRE.PREPARE_COMMIT
    case 'set_summary_confirmation':
      return payload.value === 'on' ? WIRE.SUMMARY_CONFIRMATION_ON
        : payload.value === 'off' ? WIRE.SUMMARY_CONFIRMATION_OFF : null
    case 'set_learnings':
      return payload.value === 'on' ? WIRE.LEARNINGS_ON : payload.value === 'off' ? WIRE.LEARNINGS_OFF : null
  }
}

// --------------------------------------------------------------------------- //
// state
// --------------------------------------------------------------------------- //

export type DeliveryOutcome = 'delivered' | 'not_delivered' | 'uncertain'

export type SubmitStage =
  /** Nothing has been attempted for this action in this session. */
  | 'idle'
  /** `POST …/submit` is open. Nothing has reached the host. */
  | 'submitting'
  /** Checking the host's slot listing. Nothing has reached the host. */
  | 'preflight'
  /** The message is with the host. From here on nothing may be re-sent. */
  | 'sending'
  /** Telling Studio what the host said. Safe to retry. */
  | 'reporting'
  /** The outcome is recorded (or was refused with a final answer). */
  | 'settled'
  /** The server refused before anything was sent. `refusal` says why. */
  | 'refused'
  /** `action_stale`: the evidence moved. `staleCard` is the refreshed card. */
  | 'stale'

export interface DeliveryAttempt {
  outcome: DeliveryOutcome
  /** False when the preflight stopped the send: nothing reached the host, whatever `outcome` could prove. */
  hostCalled: boolean
  httpStatus: number | null
  receipt: HostChatReceipt | null
  /**
   * True once the backend answered `POST …/delivery`: with its record, or with one of the broker's own refusals.
   * False while the report is on the wire, and for good once it never reached the broker: retries spent, or a 4xx
   * from something else (`reportError` says which).
   */
  reported: boolean
  /** The backend's refusal, or the report's own last failure if it never landed. The send is NOT retried for it. */
  reportError: StudioApiError | null
}

/**
 * The outcome as Studio recorded it. The browser's `not_delivered` is a claim the broker re-proves, and its
 * `not_delivered_unproven` answer records the send as uncertain instead.
 */
export function recordedOutcome(attempt: DeliveryAttempt): DeliveryOutcome {
  return attempt.outcome === 'not_delivered' && attempt.reportError?.code === 'not_delivered_unproven'
    ? 'uncertain' : attempt.outcome
}

export interface SubmitState {
  stage: SubmitStage
  /** Which action this state describes; `null` in `idle`. */
  actionId: string | null
  /** The status generation of the card this attempt started from: a server card past it reflects the attempt. */
  generation: number | null
  /**
   * Exactly what `POST …/submit` carried, fixed when Send was pressed: until the outcome is recorded the
   * confirmation shows these bytes, whatever the draft or the card does meanwhile. `null` for a decision with no
   * prompt, and again once a refusal hands the confirmation back with nothing sent.
   */
  request: SubmitRequest | null
  receipt: SubmitReceipt | null
  attempt: DeliveryAttempt | null
  /** The canonical card the server last returned. Never a locally patched copy. */
  card: ActionCard | null
  refusal: StudioApiError | null
  staleCard: ActionCard | null
}

export const IDLE: SubmitState = {
  stage: 'idle', actionId: null, generation: null, request: null, receipt: null, attempt: null, card: null,
  refusal: null, staleCard: null,
}

/** True while nothing has been handed to the host yet, so a Cancel is still honest. */
export function beforeSend(stage: SubmitStage): boolean {
  return stage === 'idle' || stage === 'submitting' || stage === 'preflight' || stage === 'refused' || stage === 'stale'
}

/** A prompt: the bytes the person confirmed, and the capture of the evidence they confirmed them against. */
export interface PromptSubmit {
  card: ActionCard
  /**
   * What the person was shown when they confirmed: a confirmation left open while its card is refreshed passes
   * the capture it was opened against, and a card queued by the click that sends it passes its own. Only that
   * capture lets the server refuse a moved one as `action_stale`.
   */
  captured: Captured
  payload: SubmitPayload
  /** What the confirmation panel displayed. The server refuses anything else. */
  clientWireText: string
  begin?: undefined
}

/**
 * `force_stop`: the same two-phase machine, started at `POST /repos/{id}/intents/{key}/force-stop`, whose
 * `receipt.host` points at the stop endpoint. No prompt, so no payload, no wire text (`wire_text_for` answers
 * `None` for it) and no capture to assert.
 */
export interface HostControlSubmit {
  card: ActionCard
  begin: () => Promise<SubmitReceipt>
  captured?: undefined
  payload?: undefined
  clientWireText?: undefined
}

export type SubmitArgs = PromptSubmit | HostControlSubmit

export interface ResolveArgs {
  card: ActionCard
  payload: ResolvePayload
}

export interface Submitter {
  state: SubmitState
  /** True while any phase of any action is running in this hook. It names no card: lock one on `useInFlight`. */
  busy: boolean
  submit: (args: SubmitArgs) => Promise<void>
  /** Studio-only decisions. One request, no host call, no delivery report. */
  resolve: (args: ResolveArgs) => Promise<void>
  reset: () => void
}

export interface UseSubmitOptions {
  api: StudioApi
  /** Every canonical card the server hands back, so the caller can replace what it is showing. */
  onCard?: (card: ActionCard) => void
  /** Called once the flow can no longer change anything, to refetch the queue. */
  onSettled?: () => void
  /** How long to wait for the host before calling the send uncertain (§3.6). */
  sendTimeoutMs?: number
  /** Extra attempts for `POST …/delivery` only. */
  reportRetries?: number
  /** Backoff between report attempts. `0` in tests. */
  reportBackoffMs?: number
}

/**
 * What holds an action in flight: its submit or resolve is running, or its send outlived the timeout and the
 * host may still answer it, until the receipt's `expires_at`.
 */
export type InFlight = 'running' | 'awaitingHost'

/**
 * Action ids with a decision in flight, module-wide.
 *
 * Not component state: the queue and the detail pane are separate trees, a route change remounts the
 * detail, and React 18 may hold two copies of a component alive at once. A guard that lived in a
 * `useRef` would be defeated by any of those, and the cost is a duplicate `Delivering` record. The
 * same holds for the lock a person sees, so every pane reads this map through `useInFlight`.
 */
const inFlight = new Map<string, InFlight>()
const inFlightListeners = new Set<() => void>()

function setInFlight(actionId: string, why: InFlight | null): void {
  if (why) inFlight.set(actionId, why)
  else inFlight.delete(actionId)
  for (const listener of inFlightListeners) listener()
}

function subscribeInFlight(listener: () => void): () => void {
  inFlightListeners.add(listener)
  return () => inFlightListeners.delete(listener)
}

/** What holds this action in flight, whichever pane sent it, or `null`. Re-renders when that changes. */
export function useInFlight(actionId: string): InFlight | null {
  return useSyncExternalStore(subscribeInFlight, () => inFlight.get(actionId) ?? null)
}

const DEFAULT_SEND_TIMEOUT_MS = 20_000
const DEFAULT_REPORT_RETRIES = 3
const DEFAULT_REPORT_BACKOFF_MS = 700

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

/** Milliseconds until an ISO instant: `0` once it has passed, or when it does not parse. */
function msUntil(iso: string): number {
  const ms = Date.parse(iso) - Date.now()
  return ms > 0 ? ms : 0
}

/** A report failure that proves nothing landed, so re-reporting is safe (§3.6). */
function reportRetriable(error: StudioApiError): boolean {
  return error.status === 0 || error.status >= 500
}

/**
 * `record_delivery`'s own refusals: the broker read the report and decided, and asking again cannot change its
 * mind. Any other 4xx (the dashboard session gone, the gateway's 404 or 429) never reached it.
 */
const BROKER_ANSWERS: readonly string[] = [
  'not_delivered_unproven', 'delivery_ack_invalid', 'illegal_transition', 'action_not_found', 'bad_param',
] satisfies readonly ErrorCode[]

/**
 * The host's own body for a 4xx, or the least-wrong stand-in.
 *
 * The backend only believes `not_delivered` when the receipt is the host's refusal (`error` or `code`
 * present, `ok` not true — `_authoritative_4xx`). `StudioApiError.body` keeps the parsed body
 * verbatim for exactly this; when the 4xx had no JSON body the message is passed as `error` rather
 * than inventing a `code`, because a fabricated code would be Studio asserting something the host
 * never said.
 */
function hostErrorReceipt(error: StudioApiError): HostChatReceipt {
  if (error.body && typeof error.body === 'object' && !Array.isArray(error.body)) {
    return error.body as HostChatReceipt
  }
  return { ok: false, error: error.message }
}

/** Normalised for comparison: the host reports realpaths, and a trailing slash is not a difference. */
function samePath(a: string, b: string): boolean {
  const trim = (value: string) => value.replace(/\/+$/, '')
  return trim(a) === trim(b)
}

/**
 * The pre-send check (review R08), as a receipt or `null` when the slot is good.
 *
 * A non-null answer means "do not send": the caller reports it as `not_delivered` with one of the two
 * codes the backend re-verifies against its own `HostBridge`. `slot.project` is compared only when
 * both sides have a value — a host build that stops reporting `project` must not turn every send into
 * a false `not_delivered`, and the backend checked `slot_matches_repo` at submit time anyway.
 */
export function preflightRefusal(
  slots: HostSlot[],
  slotKey: string,
  canonicalPath: string,
  fileAnswers = false,
): HostChatReceipt | null {
  const slot = slots.find((candidate) => candidate.key === slotKey)
  if (!slot) return { ok: false, code: 'slot_missing' }
  const projectDiffers = !!slot.project && !!canonicalPath && !samePath(slot.project, canonicalPath)
  if (projectDiffers || (!!slot.agent && slot.agent !== 'aidlc')) {
    return { ok: false, code: 'slot_mismatch_preflight' }
  }
  if (fileAnswers && (slot.running || slot.stopping || slot.orchestrating ||
      slot.pending_approval || slot.needs_input || slot.queue_depth > 0 || slot.subagents_running > 0)) {
    return { ok: false, code: 'answer_session_busy_preflight' }
  }
  return null
}

/** How a promise ended. Never a rejection, so a late failure cannot surface as an unhandled one. */
type Settled<T> = { value: T } | { error: unknown }

/**
 * Race a promise the caller may not cancel.
 *
 * `POST /api/chat` cannot be aborted safely — the host may already be enqueuing the turn — so the
 * timeout only decides what we *say* now, never whether the request continues. A timed-out race
 * hands back the request's own ending as `late`, because the host still answers for that turn.
 */
async function withTimeout<T>(
  promise: Promise<T>, ms: number,
): Promise<({ timedOut: false } & Settled<T>) | { timedOut: true; late: Promise<Settled<T>> }> {
  let timer: ReturnType<typeof setTimeout> | null = null
  const guard = new Promise<null>((resolve) => {
    timer = setTimeout(() => resolve(null), ms)
  })
  const settled = promise.then((value): Settled<T> => ({ value }), (error: unknown): Settled<T> => ({ error }))
  const first = await Promise.race([settled, guard])
  if (timer !== null) clearTimeout(timer)
  return first === null ? { timedOut: true, late: settled } : { timedOut: false, ...first }
}

/** What one host answer means: `ok:true` delivered, the host's own 4xx not delivered, anything else uncertain. */
function classify(answer: Settled<HostChatReceipt>): Omit<DeliveryAttempt, 'reported' | 'reportError'> {
  if ('error' in answer) {
    const error = answer.error instanceof StudioApiError ? answer.error : new StudioApiError('internal_error', String(answer.error), {}, 0)
    // The host validates the slot and the agent before it touches the queue, so its own 4xx
    // is proof the turn was never enqueued.
    const refused = error.status >= 400 && error.status <= 499
    return { outcome: refused ? 'not_delivered' : 'uncertain', hostCalled: true, httpStatus: error.status || null,
      receipt: hostErrorReceipt(error) }
  }
  const receipt = answer.value ?? null
  // A 200 that does not say `ok` is not a delivery and not a refusal. Calling it undelivered
  // would invite a resend of a turn the host may have taken.
  return { outcome: receipt?.ok === true ? 'delivered' : 'uncertain', hostCalled: true, httpStatus: null, receipt }
}

export function useSubmit(options: UseSubmitOptions): Submitter {
  const {
    api,
    onCard,
    onSettled,
    sendTimeoutMs = DEFAULT_SEND_TIMEOUT_MS,
    reportRetries = DEFAULT_REPORT_RETRIES,
    reportBackoffMs = DEFAULT_REPORT_BACKOFF_MS,
  } = options

  const [state, setState] = useState<SubmitState>(IDLE)
  const [busy, setBusy] = useState(false)

  // Callbacks through a ref so `submit` keeps one identity: it is passed to a button that re-renders
  // on every keystroke in the feedback box, and a new function per render would defeat memoisation.
  const latest = useRef({ api, onCard, onSettled })
  latest.current = { api, onCard, onSettled }

  const mounted = useRef(true)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const patch = useCallback((actionId: string, next: Partial<SubmitState>) => {
    // An unmounted pane must not set state, but the flow itself continues: the send and the report
    // are the user's decision, not this component's lifetime. Nor may a flow write over the attempt of
    // another action this pane went on to decide while it was still in flight.
    if (mounted.current) setState((current) => (current.actionId === actionId ? { ...current, ...next } : current))
  }, [])

  const publish = useCallback((card: ActionCard | null) => {
    if (card) latest.current.onCard?.(card)
  }, [])

  /** Phase 4. Retries itself, never the send. */
  const report = useCallback(
    async (actionId: string, deliveryId: string, attempt: Omit<DeliveryAttempt, 'reported' | 'reportError'>): Promise<DeliveryAttempt> => {
      const body = {
        delivery_id: deliveryId,
        outcome: attempt.outcome,
        http_status: attempt.httpStatus,
        receipt: attempt.receipt,
      }
      let lastError: StudioApiError | null = null
      for (let tries = 0; tries <= reportRetries; tries += 1) {
        try {
          const answer = await latest.current.api.reportDelivery(actionId, body)
          publish(answer.action)
          return { ...attempt, reported: true, reportError: null, receipt: attempt.receipt }
        } catch (caught) {
          const error = caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0)
          lastError = error
          // An answer, not a failure: `not_delivered_unproven` has already recorded the uncertainty.
          if (BROKER_ANSWERS.includes(error.code)) return { ...attempt, reported: true, reportError: error }
          if (!reportRetriable(error) || tries === reportRetries) break
          if (reportBackoffMs > 0) await sleep(reportBackoffMs * (tries + 1))
        }
      }
      return { ...attempt, reported: false, reportError: lastError }
    },
    [publish, reportBackoffMs, reportRetries],
  )

  const submit = useCallback(
    async (args: SubmitArgs): Promise<void> => {
      const { card, payload } = args
      const actionId = card.action_id
      // The one guard that makes a double send impossible. Checked and set in the same synchronous
      // step, before the first `await`.
      if (inFlight.has(actionId)) return
      setInFlight(actionId, 'running')
      setBusy(true)
      let request: SubmitRequest | null = null
      let start: () => Promise<SubmitReceipt>
      if (args.begin) {
        start = args.begin
      } else {
        const { captured } = args
        const body: SubmitRequest = {
          captured: {
            state_hash: captured.state_hash,
            boundary_token: captured.boundary_token,
            question_digest: captured.question_digest,
            stage_attempt: captured.stage_attempt,
            evidence_digest: captured.evidence_digest,
            is_active: captured.is_active,
          },
          payload: args.payload,
          client_wire_text: args.clientWireText,
        }
        request = body
        start = () => latest.current.api.submitAction(actionId, body)
      }
      setState({ ...IDLE, stage: 'submitting', actionId, generation: card.status_generation, request })

      try {
        let receipt: SubmitReceipt
        try {
          receipt = await start()
        } catch (caught) {
          const error = caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0)
          if (error.code === 'action_stale') {
            const refreshed = error.details['card']
            const stale = refreshed && typeof refreshed === 'object' ? (refreshed as ActionCard) : null
            publish(stale)
            patch(actionId, { stage: 'stale', refusal: error, staleCard: stale, request: null })
          } else {
            patch(actionId, { stage: 'refused', refusal: error, request: null })
          }
          latest.current.onSettled?.()
          return
        }

        patch(actionId, { receipt, stage: 'preflight' })

        // ---- 1b. slot preflight (human lane only) -------------------------- //
        let refused: HostChatReceipt | null = null
        if (receipt.lane === 'human_lane') {
          try {
            const slots = await latest.current.api.listSlots()
            refused = preflightRefusal(slots, receipt.slot_key, card.repo.canonical_path,
              payload?.decision === 'answers' && !card.evidence.questions?.origin)
          } catch {
            // The listing itself failed, so the slot is neither proven good nor proven gone. Nothing
            // is sent (the host would create an unknown slot silently), and the honest outcome for
            // "we cannot tell" is `uncertain` — `not_delivered` would be a claim we cannot support.
            const attempt = await report(actionId, receipt.delivery_id, {
              outcome: 'uncertain', hostCalled: false, httpStatus: null,
              receipt: { ok: false, code: 'preflight_unavailable' },
            })
            patch(actionId, { stage: 'settled', attempt })
            latest.current.onSettled?.()
            return
          }
        }
        if (refused) {
          const attempt = await report(actionId, receipt.delivery_id, {
            outcome: refused.code === 'answer_session_busy_preflight' ? 'uncertain' : 'not_delivered',
            hostCalled: false, httpStatus: null, receipt: refused,
          })
          patch(actionId, { stage: 'settled', attempt })
          latest.current.onSettled?.()
          return
        }

        // ---- 2. the send. Exactly once, body verbatim. --------------------- //
        patch(actionId, { stage: 'sending' })
        const raced = await withTimeout(
          latest.current.api.sendToHost(receipt.host.path, receipt.host.body),
          sendTimeoutMs,
        )
        const sent: Omit<DeliveryAttempt, 'reported' | 'reportError'> = raced.timedOut
          ? { outcome: 'uncertain', hostCalled: true, httpStatus: null, receipt: { ok: false, code: 'send_timeout' } }
          : classify(raced)

        // ---- 3. report it ------------------------------------------------- //
        patch(actionId, { stage: 'reporting', attempt: { ...sent, reported: false, reportError: null } })
        const attempt = await report(actionId, receipt.delivery_id, sent)
        patch(actionId, { stage: 'settled', attempt })
        latest.current.onSettled?.()
        if (raced.timedOut) {
          // After the first report, so the backend reads them in order. Another `uncertain` would say nothing new.
          const { delivery_id: deliveryId } = receipt
          const late = raced.late.then(async (answer) => {
            const sentLate = classify(answer)
            if (sentLate.outcome === 'uncertain') return
            const recorded = await report(actionId, deliveryId, sentLate)
            // This can land after the hold, when a later decision on the card owns the state: only the delivery
            // id still names this attempt.
            if (mounted.current) {
              setState((current) => (current.receipt?.delivery_id === deliveryId ? { ...current, attempt: recorded } : current))
            }
          })
          // The host may still take this turn: until it answers, or the receipt's ack deadline passes, the action
          // stays in flight and nothing may send it again.
          setInFlight(actionId, 'awaitingHost')
          await withTimeout(late, msUntil(receipt.expires_at))
        }
      } finally {
        setInFlight(actionId, null)
        if (mounted.current) setBusy(false)
      }
    },
    [patch, publish, report, sendTimeoutMs],
  )

  const resolve = useCallback(
    async ({ card, payload }: ResolveArgs): Promise<void> => {
      const actionId = card.action_id
      if (inFlight.has(actionId)) return
      setInFlight(actionId, 'running')
      setBusy(true)
      setState({ ...IDLE, stage: 'submitting', actionId, generation: card.status_generation })
      try {
        const answer = await latest.current.api.resolveAction(actionId, payload)
        publish(answer.action)
        patch(actionId, { stage: 'settled', card: answer.action })
      } catch (caught) {
        const error = caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0)
        if (error.code === 'action_stale') {
          const refreshed = error.details['card']
          const stale = refreshed && typeof refreshed === 'object' ? (refreshed as ActionCard) : null
          publish(stale)
          patch(actionId, { stage: 'stale', refusal: error, staleCard: stale })
        } else {
          patch(actionId, { stage: 'refused', refusal: error })
        }
      } finally {
        setInFlight(actionId, null)
        if (mounted.current) setBusy(false)
        latest.current.onSettled?.()
      }
    },
    [patch, publish],
  )

  const reset = useCallback(() => setState(IDLE), [])

  return useMemo<Submitter>(
    () => ({ state, busy, submit, resolve, reset }),
    [state, busy, submit, resolve, reset],
  )
}
