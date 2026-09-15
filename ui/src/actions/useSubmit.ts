/**
 * The client half of the two-phase human lane (contracts §2.4, §2.13, §3.6).
 *
 * Studio's whole at-most-once story runs through this file, so it is written around one invariant:
 * **the message is sent at most once, and never automatically.** Everything else — retries, error
 * decoding, state shape — exists to serve that.
 *
 * The sequence, and why each step is where it is:
 *
 *  1. `POST /actions/{id}/submit` with the evidence captured on the card and the wire text the user
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
 *     report never landed (network, 5xx). A 4xx answer is the backend's decision and is final.
 *
 * Step 3 is never re-run for the same `delivery_id`. There is no automatic retry of a send anywhere in
 * this module, and a second `submit()` for an action id that is already in flight is refused by a
 * module-level guard rather than a component's state — two mounted panes, or a fast double-click
 * across a re-render, must not be able to produce two `Delivering` records.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { StudioApiError, type StudioApi } from '../lib/api'
import { WIRE } from '../lib/wire.generated'
import type {
  ActionCard, AnswerInput, HostChatReceipt, Question, QuestionsView, ResolvePayload, SlotView,
  SubmitPayload, SubmitReceipt,
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
 * One answer as the conductor will read it, or `null` when it cannot be encoded.
 *
 * Labels, never letters (FR-Q-007): the option letters are re-assigned when a stage is revised, so a
 * recorded `B` would be an answer to a question that no longer exists. `is_other` and the
 * option-less (free-form) question both send the human's own words — this mirrors
 * `_answers_wire_text` in `actions.py` exactly, because `client_wire_text` is compared byte-for-byte.
 */
export function answerText(answer: AnswerInput, question: Question, textInput = false): string | null {
  const known = new Map(question.options.map((option) => [option.letter, option]))
  const letters = answer.option_letters
  if (known.size !== question.options.length || new Set(letters).size !== letters.length) return null
  if (letters.some((letter) => !known.has(letter))) return null
  if (letters.length > 1 && !question.multi_select) return null
  const free = (answer.free_text ?? '').trim()
  if (textInput && /^free text(?: note)?$/.test(free.toLowerCase().replace(/-/g, ' ').replace(/\s+/g, ' '))) return null
  const chosenOther = letters.some((letter) => known.get(letter)?.is_other === true)
  if ((chosenOther || letters.length === 0) && !free) return null
  const text = letters.length === 0 ? free : letters
    .map((letter) => known.get(letter)?.is_other ? free : known.get(letter)?.text ?? '')
    .join(WIRE.MULTI_SELECT_JOINER)
  if (!text) return null
  return text.length > MAX_ANSWER_CHARS ? null : text
}

/** The pending questions of a group, in file order — the set one submit has to answer together. */
export function pendingQuestions(questions: QuestionsView | null): Question[] {
  return (questions?.questions ?? []).filter((question) => !question.answered)
}

function feedbackText(raw: string): string | null {
  const text = raw.trim()
  if (!text || text.length > MAX_FEEDBACK_CHARS) return null
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
    'answers', 'confirm_summary', 'approve_plan', 'request_plan_changes',
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
        ) : null
        if (text === null) return null
        texts.push(text)
      }
      if (texts.length === 1) return texts[0] ?? null
      // The backend advertises compatibility for this file-backed grouped reply.
      if (!groupedAnswers) return null
      return pending
        .map((question, position) =>
          WIRE.ANSWER_LINE.replace('{index}', String(question.index)).replace('{answer}', texts[position] ?? ''),
        )
        .join(WIRE.ANSWER_JOINER) + WIRE.GROUPED_ANSWER_SUFFIX
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
  httpStatus: number | null
  receipt: HostChatReceipt | null
  /** True once `POST …/delivery` returned. False means Studio does not yet know the outcome. */
  reported: boolean
  /** The report's own final failure, if it never landed. The send is NOT retried because of it. */
  reportError: StudioApiError | null
}

export interface SubmitState {
  stage: SubmitStage
  /** Which action this state describes; `null` in `idle`. */
  actionId: string | null
  receipt: SubmitReceipt | null
  attempt: DeliveryAttempt | null
  /** The canonical card the server last returned. Never a locally patched copy. */
  card: ActionCard | null
  refusal: StudioApiError | null
  staleCard: ActionCard | null
}

export const IDLE: SubmitState = {
  stage: 'idle', actionId: null, receipt: null, attempt: null, card: null, refusal: null, staleCard: null,
}

/** True while nothing has been handed to the host yet, so a Cancel is still honest. */
export function beforeSend(stage: SubmitStage): boolean {
  return stage === 'idle' || stage === 'submitting' || stage === 'preflight' || stage === 'refused' || stage === 'stale'
}

export interface SubmitArgs {
  card: ActionCard
  /**
   * `null` only together with `begin`: the host-control lane sends no prompt, so there is no payload and
   * no wire text to compare (`wire_text_for` answers `None` for `force_stop`).
   */
  payload: SubmitPayload | null
  /** What the confirmation panel displayed. The server refuses anything else. */
  clientWireText: string
  /**
   * Phase 1 override. `force_stop` is the same two-phase machine, but it starts at
   * `POST /repos/{id}/intents/{key}/force-stop` and its `receipt.host` points at the stop endpoint.
   */
  begin?: () => Promise<SubmitReceipt>
}

export interface ResolveArgs {
  card: ActionCard
  payload: ResolvePayload
}

export interface Submitter {
  state: SubmitState
  /** True while any phase of any action is running in this hook. */
  busy: boolean
  /** True while THIS action id has a submit in flight anywhere in the page. */
  isBusy: (actionId: string) => boolean
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
 * Action ids with a submit in flight, module-wide.
 *
 * Not component state: the queue and the detail pane are separate trees, a route change remounts the
 * detail, and React 18 may hold two copies of a component alive at once. A guard that lived in a
 * `useRef` would be defeated by any of those, and the cost is a duplicate `Delivering` record.
 */
const inFlight = new Set<string>()

export function isSubmitInFlight(actionId: string): boolean {
  return inFlight.has(actionId)
}

const DEFAULT_SEND_TIMEOUT_MS = 20_000
const DEFAULT_REPORT_RETRIES = 3
const DEFAULT_REPORT_BACKOFF_MS = 700

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

/** A report failure that proves nothing landed, so re-reporting is safe (§3.6). */
function reportRetriable(error: StudioApiError): boolean {
  return error.status === 0 || error.status >= 500
}

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
  slots: SlotView[],
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
  if (fileAnswers && (slot.running || slot.stopping || slot.in_stage_execution ||
      slot.pending_approval || slot.needs_input || slot.queue_depth > 0 ||
      slot.subagents_running > 0 || slot.deliveries_inflight > 0)) {
    return { ok: false, code: 'answer_session_busy_preflight' }
  }
  return null
}

/**
 * Race a promise the caller may not cancel.
 *
 * `POST /api/chat` cannot be aborted safely — the host may already be enqueuing the turn — so the
 * timeout only decides what we *say*, never whether the request continues. The losing promise's
 * rejection is swallowed so a late failure does not surface as an unhandled rejection minutes later.
 */
async function withTimeout<T>(promise: Promise<T>, ms: number): Promise<{ timedOut: true } | { timedOut: false; value: T } | { timedOut: false; error: unknown }> {
  let timer: ReturnType<typeof setTimeout> | null = null
  const guard = new Promise<{ timedOut: true }>((resolve) => {
    timer = setTimeout(() => resolve({ timedOut: true }), ms)
  })
  const settled = promise.then(
    (value) => ({ timedOut: false as const, value }),
    (error: unknown) => ({ timedOut: false as const, error }),
  )
  const result = await Promise.race([settled, guard])
  if (timer !== null) clearTimeout(timer)
  if (result.timedOut) void promise.catch(() => {})
  return result
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

  const patch = useCallback((next: Partial<SubmitState>) => {
    // An unmounted pane must not set state, but the flow itself continues: the send and the report
    // are the user's decision, not this component's lifetime.
    if (mounted.current) setState((current) => ({ ...current, ...next }))
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
          // `not_delivered_unproven` and `delivery_ack_invalid` are answers, not failures: the backend
          // has already recorded the uncertainty and repeating the call cannot change its mind.
          if (!reportRetriable(error) || tries === reportRetries) break
          if (reportBackoffMs > 0) await sleep(reportBackoffMs * (tries + 1))
        }
      }
      return { ...attempt, reported: false, reportError: lastError }
    },
    [publish, reportBackoffMs, reportRetries],
  )

  const submit = useCallback(
    async ({ card, payload, clientWireText, begin }: SubmitArgs): Promise<void> => {
      const actionId = card.action_id
      // The one guard that makes a double send impossible. Checked and set in the same synchronous
      // step, before the first `await`.
      if (inFlight.has(actionId)) return
      inFlight.add(actionId)
      setBusy(true)
      setState({ ...IDLE, stage: 'submitting', actionId })

      try {
        let receipt: SubmitReceipt
        try {
          if (begin) {
            receipt = await begin()
          } else if (payload) {
            receipt = await latest.current.api.submitAction(actionId, {
              captured: {
                state_hash: card.captured.state_hash,
                boundary_token: card.captured.boundary_token,
                question_digest: card.captured.question_digest,
                stage_attempt: card.captured.stage_attempt,
                evidence_digest: card.captured.evidence_digest,
                is_active: card.captured.is_active,
              },
              payload,
              client_wire_text: clientWireText,
            })
          } else {
            // Neither a payload nor a phase-1 override: there is nothing to submit. Refusing here rather
            // than sending an empty body keeps the failure inside Studio.
            patch({ stage: 'refused', refusal: new StudioApiError('invalid_decision', 'no payload to submit', {}, 0) })
            latest.current.onSettled?.()
            return
          }
        } catch (caught) {
          const error = caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0)
          if (error.code === 'action_stale') {
            const refreshed = error.details['card']
            const stale = refreshed && typeof refreshed === 'object' ? (refreshed as ActionCard) : null
            publish(stale)
            patch({ stage: 'stale', refusal: error, staleCard: stale })
          } else {
            patch({ stage: 'refused', refusal: error })
          }
          latest.current.onSettled?.()
          return
        }

        patch({ receipt, stage: 'preflight' })

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
              outcome: 'uncertain', httpStatus: null, receipt: { ok: false, code: 'preflight_unavailable' },
            })
            patch({ stage: 'settled', attempt })
            latest.current.onSettled?.()
            return
          }
        }
        if (refused) {
          const attempt = await report(actionId, receipt.delivery_id, {
            outcome: refused.code === 'answer_session_busy_preflight' ? 'uncertain' : 'not_delivered',
            httpStatus: null, receipt: refused,
          })
          patch({ stage: 'settled', attempt })
          latest.current.onSettled?.()
          return
        }

        // ---- 2. the send. Exactly once, body verbatim. --------------------- //
        patch({ stage: 'sending' })
        const raced = await withTimeout(
          latest.current.api.sendToHost(receipt.host.path, receipt.host.body),
          sendTimeoutMs,
        )

        let outcome: DeliveryOutcome
        let httpStatus: number | null = null
        let hostReceipt: HostChatReceipt | null = null
        if (raced.timedOut) {
          outcome = 'uncertain'
          hostReceipt = { ok: false, code: 'send_timeout' }
        } else if ('error' in raced) {
          const error = raced.error instanceof StudioApiError ? raced.error : new StudioApiError('internal_error', String(raced.error), {}, 0)
          httpStatus = error.status || null
          if (error.status >= 400 && error.status <= 499) {
            // The host validates the slot and the agent before it touches the queue, so its own 4xx
            // is proof the turn was never enqueued.
            outcome = 'not_delivered'
            hostReceipt = hostErrorReceipt(error)
          } else {
            outcome = 'uncertain'
            hostReceipt = hostErrorReceipt(error)
          }
        } else {
          hostReceipt = raced.value ?? null
          // A 200 that does not say `ok` is not a delivery and not a refusal. Calling it undelivered
          // would invite a resend of a turn the host may have taken.
          outcome = hostReceipt?.ok === true ? 'delivered' : 'uncertain'
        }

        // ---- 3. report it ------------------------------------------------- //
        patch({ stage: 'reporting', attempt: { outcome, httpStatus, receipt: hostReceipt, reported: false, reportError: null } })
        const attempt = await report(actionId, receipt.delivery_id, { outcome, httpStatus, receipt: hostReceipt })
        patch({ stage: 'settled', attempt })
        latest.current.onSettled?.()
      } finally {
        inFlight.delete(actionId)
        if (mounted.current) setBusy(false)
      }
    },
    [patch, publish, report, sendTimeoutMs],
  )

  const resolve = useCallback(
    async ({ card, payload }: ResolveArgs): Promise<void> => {
      const actionId = card.action_id
      if (inFlight.has(actionId)) return
      inFlight.add(actionId)
      setBusy(true)
      setState({ ...IDLE, stage: 'submitting', actionId })
      try {
        const answer = await latest.current.api.resolveAction(actionId, payload)
        publish(answer.action)
        patch({ stage: 'settled', card: answer.action })
      } catch (caught) {
        const error = caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0)
        if (error.code === 'action_stale') {
          const refreshed = error.details['card']
          const stale = refreshed && typeof refreshed === 'object' ? (refreshed as ActionCard) : null
          publish(stale)
          patch({ stage: 'stale', refusal: error, staleCard: stale })
        } else {
          patch({ stage: 'refused', refusal: error })
        }
      } finally {
        inFlight.delete(actionId)
        if (mounted.current) setBusy(false)
        latest.current.onSettled?.()
      }
    },
    [patch, publish],
  )

  const reset = useCallback(() => setState(IDLE), [])

  return useMemo<Submitter>(
    () => ({ state, busy, isBusy: isSubmitInFlight, submit, resolve, reset }),
    [state, busy, submit, resolve, reset],
  )
}
