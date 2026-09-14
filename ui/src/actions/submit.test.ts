/**
 * The two-phase submit, which is the one place in this UI where a bug forges a human decision.
 *
 * Every test below pins a property that the at-most-once contract depends on, not an implementation
 * detail: how many times `POST /api/chat` happened, what bytes it carried, and what Studio was told
 * afterwards. If the flow were rewritten around a different state machine these assertions should all
 * still hold.
 */

import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { apiCalls, setApiRoutes, StubApiError } from '../test/stubs/app-sdk'
import { useStudioApi } from '../lib/api'
import { WIRE } from '../lib/wire.generated'
import type { ActionCard, Question, QuestionsView, SlotView, SubmitReceipt } from '../lib/types'
import { answerText, preflightRefusal, useSubmit, wireTextFor } from './useSubmit'

const BASE = '/api/apps/aidlc-studio'
const SEND_PATH = '/api/chat?ws=1'

let ids = 0
const nextId = () => `a_${(ids += 1)}`

/** A card with the fields the submit flow reads. The rest of a real card is 60 fields of evidence. */
function card(actionId: string, over: Partial<ActionCard> = {}): ActionCard {
  return {
    action_id: actionId,
    type: 'gate',
    queue_type: 'gate',
    status: 'Queued',
    status_generation: 1,
    severity: 'blocking',
    repo: { repo_id: 'r_1', label: 'checkout-web', canonical_path: '/work/checkout-web' },
    space: 'default',
    intent: { intent_dir: '250901-guest', intent_key: 'default~250901-guest', slug: 'guest-checkout', uuid: null, title: null },
    captured: {
      state_hash: 'sha-state', boundary_token: 'tok', question_digest: null, stage_attempt: 1,
      evidence_digest: 'sha-ev', is_active: true, captured_at: '2026-09-04T10:00:00Z', stable: true,
    },
    evidence: { questions: null, session: { slot_key: 'aidlc-r1-guest', session_key: 'ses_1', running: false, stop_state: 'idle', last_turn_ts: null, queue_depth: 0, busy_reasons: [] } },
    ...over,
  } as unknown as ActionCard

}

function receipt(actionId: string, over: Partial<SubmitReceipt> = {}): SubmitReceipt {
  return {
    ok: true,
    action_id: actionId,
    status: 'Delivering',
    lane: 'human_lane',
    delivery_id: `d_${actionId}`,
    slot_key: 'aidlc-r1-guest',
    session_key: 'ses_1',
    wire_text: WIRE.APPROVE,
    expires_at: '2026-09-04T10:01:30Z',
    lease_generation: 3,
    host: {
      method: 'POST',
      path: SEND_PATH,
      body: {
        message: WIRE.APPROVE,
        slot: 'aidlc-r1-guest',
        agent: 'aidlc',
        meta: { studio_action_id: actionId, studio_delivery_id: `d_${actionId}` },
      },
    },
    ...over,
  }
}

/** Only the three fields the preflight reads; a real `SlotView` has 25 more the host fills in. */
const slot = (over: Record<string, unknown> = {}) =>
  ({ key: 'aidlc-r1-guest', project: '/work/checkout-web', agent: 'aidlc', running: false, ...over }) as unknown as SlotView

/** Routes for a clean run; `over` replaces individual handlers. */
function routes(actionId: string, over: Record<string, (body: unknown, path: string) => unknown> = {}) {
  setApiRoutes({
    [`POST ${BASE}/actions/${actionId}/submit`]: () => receipt(actionId),
    'GET /api/chat/slots': () => [slot()],
    [`POST ${SEND_PATH}`]: () => ({ ok: true, slot: 'aidlc-r1-guest', mid: 'm_1' }),
    [`POST ${BASE}/actions/${actionId}/delivery`]: () => ({ ok: true, action: card(actionId, { status: 'Delivered' }), idempotent: false }),
    ...over,
  })
}

function mount() {
  return renderHook(() => {
    const api = useStudioApi()
    return useSubmit({ api, reportBackoffMs: 0 })
  })
}

const sends = () => apiCalls.filter((call) => call.path === SEND_PATH)
const reports = (actionId: string) => apiCalls.filter((call) => call.path === `${BASE}/actions/${actionId}/delivery`)

describe('wireTextFor', () => {
  const question = (over: Partial<Question> = {}): Question => ({
    index: 1,
    prompt: 'Which store?',
    options: [
      { letter: 'A', text: 'DynamoDB', is_other: false },
      { letter: 'B', text: 'Aurora', is_other: false },
      { letter: 'X', text: 'Other', is_other: true },
    ],
    multi_select: false,
    answer: null,
    answered: false,
    required: true,
    ...over,
  })
  const view = (questions: Question[]): QuestionsView =>
    ({ questions, mode: 'structured', pending_count: questions.length } as unknown as QuestionsView)

  it('sends the pinned constants, not a translated label', () => {
    expect(wireTextFor({ decision: 'approve' }, null, false)).toBe('Approve')
    expect(wireTextFor({ decision: 'accept_as_is' }, null, false)).toBe('Accept as-is')
    expect(wireTextFor({ decision: 'approve_plan' }, null, false)).toBe('Approve Plan')
    expect(wireTextFor({ decision: 'run' }, null, false)).toBe('/aidlc')
    expect(wireTextFor({ decision: 'resume' }, null, false)).toBe('/aidlc --resume')
  })

  it('keeps the capital C on a stage gate and the lower-case c on a summary confirmation', () => {
    expect(wireTextFor({ decision: 'request_changes', feedback: '  C3 is uncovered.  ' }, null, false))
      .toBe('Request Changes: C3 is uncovered.')
    expect(wireTextFor({ decision: 'confirm_summary', choice: 'request_changes', feedback: 'wrong scope' }, null, false))
      .toBe('Request changes: wrong scope')
    expect(wireTextFor({ decision: 'confirm_summary', choice: 'looks_correct' }, null, false)).toBe('Looks correct')
  })

  it('refuses blank feedback rather than sending a bare prefix', () => {
    expect(wireTextFor({ decision: 'request_changes', feedback: '   ' }, null, false)).toBeNull()
    expect(wireTextFor({ decision: 'request_plan_changes', feedback: '' }, null, false)).toBeNull()
    expect(wireTextFor({ decision: 'provide_input', kind: 'free_text', text: ' ' }, null, false)).toBeNull()
  })

  it('answers with the option label, never the letter', () => {
    const q = question()
    expect(answerText({ index: 1, option_letters: ['B'], free_text: null }, q)).toBe('Aurora')
    // Multi-select joins labels; a single-select question refuses two.
    expect(answerText({ index: 1, option_letters: ['A', 'B'], free_text: null }, q)).toBeNull()
    expect(answerText({ index: 1, option_letters: ['A', 'B'], free_text: null }, question({ multi_select: true })))
      .toBe('DynamoDB, Aurora')
    // `is_other` sends the human's own words, and needs them.
    expect(answerText({ index: 1, option_letters: ['X'], free_text: ' use the queue ' }, q)).toBe('use the queue')
    expect(answerText({ index: 1, option_letters: ['X'], free_text: null }, q)).toBeNull()
    // An option the question does not offer is never guessed at.
    expect(answerText({ index: 1, option_letters: ['Z'], free_text: null }, q)).toBeNull()
  })

  it('sends one pending answer alone and gates the grouped form', () => {
    const one = view([question()])
    expect(wireTextFor({ decision: 'answers', answers: [{ index: 1, option_letters: ['A'], free_text: null }] }, one, false))
      .toBe('DynamoDB')

    const two = view([question(), question({ index: 2, prompt: 'Retries?' })])
    const answers = [
      { index: 1, option_letters: ['B'], free_text: null },
      { index: 2, option_letters: ['A'], free_text: null },
    ]
    expect(wireTextFor({ decision: 'answers', answers }, two, false)).toBeNull()
    expect(wireTextFor({ decision: 'answers', answers }, two, true))
      .toBe('Q1: Aurora\nQ2: DynamoDB' + WIRE.GROUPED_ANSWER_SUFFIX)
  })

  it('refuses an incomplete group instead of sending a partial reply', () => {
    const two = view([question(), question({ index: 2 })])
    expect(wireTextFor({ decision: 'answers', answers: [{ index: 1, option_letters: ['A'], free_text: null }] }, two, true))
      .toBeNull()
  })

  it('retains selected labels alongside multiline Other text', () => {
    expect(answerText({ index: 1, option_letters: ['A', 'X'], free_text: 'Custom, 中文.\nLine 2.' },
      question({ multi_select: true }))).toBe('DynamoDB, Custom, 中文.\nLine 2.')
  })

  it('refuses ambiguous indexes, duplicate choices, and stale drafts in degraded mode', () => {
    const q = question({ multi_select: true })
    const a = { index: 1, option_letters: ['A'], free_text: null }
    expect(answerText({ ...a, option_letters: ['A', 'A'] }, q)).toBeNull()
    expect(wireTextFor({ decision: 'answers', answers: [a, a] }, view([q]), true)).toBeNull()
    expect(wireTextFor({ decision: 'answers', answers: [a, { ...a, index: 2 }] }, view([q]), true)).toBeNull()
    expect(wireTextFor({ decision: 'answers', answers: [a] }, view([q, q]), true)).toBeNull()
    expect(wireTextFor({ decision: 'answers', answers: [a] }, { ...view([q]), mode: 'degraded' }, true)).toBeNull()
  })

  it('skips questions the file already records as answered', () => {
    const mixed = view([question({ answered: true, answer: 'DynamoDB' }), question({ index: 2 })])
    expect(wireTextFor({ decision: 'answers', answers: [{ index: 2, option_letters: ['B'], free_text: null }] }, mixed, false))
      .toBe('Aurora')
  })
})

describe('preflightRefusal', () => {
  it('refuses a slot that is gone, so the host cannot silently create a new one', () => {
    expect(preflightRefusal([], 'aidlc-r1-guest', '/work/checkout-web')).toEqual({ ok: false, code: 'slot_missing' })
  })

  it('refuses a slot that now points at another project or agent', () => {
    expect(preflightRefusal([slot({ project: '/work/other' })], 'aidlc-r1-guest', '/work/checkout-web'))
      .toEqual({ ok: false, code: 'slot_mismatch_preflight' })
    expect(preflightRefusal([slot({ agent: 'default' })], 'aidlc-r1-guest', '/work/checkout-web'))
      .toEqual({ ok: false, code: 'slot_mismatch_preflight' })
  })

  it('passes a matching slot, ignoring a trailing slash', () => {
    expect(preflightRefusal([slot({ project: '/work/checkout-web/' })], 'aidlc-r1-guest', '/work/checkout-web')).toBeNull()
  })
})

describe('the two-phase submit', () => {
  it('does not inject file answers into a session that became busy after submit', async () => {
    const id = nextId()
    routes(id, { 'GET /api/chat/slots': () => [slot({ needs_input: true })] })
    const { result } = mount()
    await act(async () => {
      await result.current.submit({ card: card(id), payload: { decision: 'answers',
        answers: [{ index: 1, option_letters: ['A'], free_text: null }] }, clientWireText: 'A' })
    })
    expect(sends()).toHaveLength(0)
    expect(reports(id)[0]?.body).toMatchObject({
      outcome: 'uncertain', receipt: { code: 'answer_session_busy_preflight' },
    })
  })

  it('submits, preflights, sends the receipt body verbatim, then reports delivered', async () => {
    const id = nextId()
    routes(id)
    const { result } = mount()

    await act(async () => {
      await result.current.submit({ card: card(id), payload: { decision: 'approve' }, clientWireText: WIRE.APPROVE })
    })

    expect(apiCalls.map((call) => `${call.method} ${call.path}`)).toEqual([
      `POST ${BASE}/actions/${id}/submit`,
      'GET /api/chat/slots',
      `POST ${SEND_PATH}`,
      `POST ${BASE}/actions/${id}/delivery`,
    ])
    // Byte-for-byte the receipt's body: a rebuilt body could carry different `meta`, and `meta` is the
    // reconciler's primary match key.
    expect(sends()[0]?.body).toEqual(receipt(id).host.body)
    expect(reports(id)[0]?.body).toEqual({
      delivery_id: `d_${id}`, outcome: 'delivered', http_status: null, receipt: { ok: true, slot: 'aidlc-r1-guest', mid: 'm_1' },
    })
    expect(result.current.state.stage).toBe('settled')
    expect(result.current.state.attempt).toMatchObject({ outcome: 'delivered', reported: true })
  })

  it('sends the client wire text the caller confirmed, as `client_wire_text`', async () => {
    const id = nextId()
    routes(id)
    const { result } = mount()
    await act(async () => {
      await result.current.submit({ card: card(id), payload: { decision: 'approve' }, clientWireText: WIRE.APPROVE })
    })
    const body = apiCalls.find((call) => call.path.endsWith('/submit'))?.body as Record<string, unknown>
    expect(body['client_wire_text']).toBe('Approve')
    expect(body['captured']).toEqual({
      state_hash: 'sha-state', boundary_token: 'tok', question_digest: null, stage_attempt: 1,
      evidence_digest: 'sha-ev', is_active: true,
    })
  })

  it('sends exactly once when submit is called twice for the same action', async () => {
    const id = nextId()
    routes(id)
    const { result } = mount()

    await act(async () => {
      const args = { card: card(id), payload: { decision: 'approve' as const }, clientWireText: WIRE.APPROVE }
      await Promise.all([result.current.submit(args), result.current.submit(args)])
    })

    expect(sends()).toHaveLength(1)
    expect(apiCalls.filter((call) => call.path.endsWith('/submit'))).toHaveLength(1)
  })

  it('never sends when the slot is missing, and reports the preflight code', async () => {
    const id = nextId()
    routes(id, { 'GET /api/chat/slots': () => [] })
    const { result } = mount()

    await act(async () => {
      await result.current.submit({ card: card(id), payload: { decision: 'approve' }, clientWireText: WIRE.APPROVE })
    })

    expect(sends()).toHaveLength(0)
    expect(reports(id)[0]?.body).toEqual({
      delivery_id: `d_${id}`, outcome: 'not_delivered', http_status: null, receipt: { ok: false, code: 'slot_missing' },
    })
  })

  it('never sends when the slot listing itself fails, and calls the outcome uncertain', async () => {
    const id = nextId()
    routes(id, { 'GET /api/chat/slots': () => { throw new StubApiError(503, { error: 'gateway restarting' }) } })
    const { result } = mount()

    await act(async () => {
      await result.current.submit({ card: card(id), payload: { decision: 'approve' }, clientWireText: WIRE.APPROVE })
    })

    expect(sends()).toHaveLength(0)
    // Not `not_delivered`: nothing was sent, but that cannot be PROVEN from a failed listing, and the
    // backend would refuse the claim anyway.
    expect(reports(id)[0]?.body).toMatchObject({ outcome: 'uncertain' })
  })

  it('treats the host 4xx as not delivered, with the host body verbatim', async () => {
    const id = nextId()
    routes(id, {
      [`POST ${SEND_PATH}`]: () => { throw new StubApiError(409, { error: 'slot agent mismatch', code: 'slot_agent' }) },
    })
    const { result } = mount()

    await act(async () => {
      await result.current.submit({ card: card(id), payload: { decision: 'approve' }, clientWireText: WIRE.APPROVE })
    })

    expect(reports(id)[0]?.body).toEqual({
      delivery_id: `d_${id}`,
      outcome: 'not_delivered',
      http_status: 409,
      receipt: { error: 'slot agent mismatch', code: 'slot_agent' },
    })
  })

  it('treats a 5xx as uncertain and does not resend', async () => {
    const id = nextId()
    routes(id, { [`POST ${SEND_PATH}`]: () => { throw new StubApiError(502, { error: 'bad gateway' }) } })
    const { result } = mount()

    await act(async () => {
      await result.current.submit({ card: card(id), payload: { decision: 'approve' }, clientWireText: WIRE.APPROVE })
    })

    expect(sends()).toHaveLength(1)
    expect(reports(id)[0]?.body).toMatchObject({ outcome: 'uncertain', http_status: 502 })
  })

  it('treats a 200 that does not say ok as uncertain, never as undelivered', async () => {
    const id = nextId()
    routes(id, { [`POST ${SEND_PATH}`]: () => ({ ok: false, error: 'no slot' }) })
    const { result } = mount()

    await act(async () => {
      await result.current.submit({ card: card(id), payload: { decision: 'approve' }, clientWireText: WIRE.APPROVE })
    })

    expect(reports(id)[0]?.body).toMatchObject({ outcome: 'uncertain', http_status: null })
  })

  it('records a queued host receipt as delivered', async () => {
    const id = nextId()
    routes(id, { [`POST ${SEND_PATH}`]: () => ({ ok: true, queued: true }) })
    const { result } = mount()

    await act(async () => {
      await result.current.submit({ card: card(id), payload: { decision: 'approve' }, clientWireText: WIRE.APPROVE })
    })

    expect(reports(id)[0]?.body).toMatchObject({ outcome: 'delivered', receipt: { ok: true, queued: true } })
  })

  it('retries the delivery report but never the send', async () => {
    const id = nextId()
    let attempts = 0
    routes(id, {
      [`POST ${BASE}/actions/${id}/delivery`]: () => {
        attempts += 1
        if (attempts === 1) throw new StubApiError(500, { error: 'storage busy', code: 'storage_error' })
        return { ok: true, action: card(id, { status: 'Delivered' }), idempotent: false }
      },
    })
    const { result } = mount()

    await act(async () => {
      await result.current.submit({ card: card(id), payload: { decision: 'approve' }, clientWireText: WIRE.APPROVE })
    })

    expect(attempts).toBe(2)
    expect(sends()).toHaveLength(1)
    expect(result.current.state.attempt).toMatchObject({ reported: true })
  })

  it('stops reporting when the backend answers with a decision of its own', async () => {
    const id = nextId()
    let attempts = 0
    routes(id, {
      [`POST ${SEND_PATH}`]: () => { throw new StubApiError(400, { error: 'bad slot', code: 'bad_slot' }) },
      [`POST ${BASE}/actions/${id}/delivery`]: () => {
        attempts += 1
        throw new StubApiError(409, { error: 'cannot prove', code: 'not_delivered_unproven', details: { checks: { row_absent: false } } })
      },
    })
    const { result } = mount()

    await act(async () => {
      await result.current.submit({ card: card(id), payload: { decision: 'approve' }, clientWireText: WIRE.APPROVE })
    })

    // One attempt: `not_delivered_unproven` is an answer, and repeating the call cannot change it.
    expect(attempts).toBe(1)
    expect(result.current.state.attempt).toMatchObject({ reported: false })
    expect(result.current.state.attempt?.reportError?.code).toBe('not_delivered_unproven')
  })

  it('shows the refreshed card and sends nothing when the evidence moved', async () => {
    const id = nextId()
    const refreshed = card(id, { status: 'Queued', updated_at: '2026-09-04T11:00:00Z' } as Partial<ActionCard>)
    routes(id, {
      [`POST ${BASE}/actions/${id}/submit`]: () => {
        throw new StubApiError(409, { error: 'stale', code: 'action_stale', details: { card: refreshed } })
      },
    })
    const { result } = mount()

    await act(async () => {
      await result.current.submit({ card: card(id), payload: { decision: 'approve' }, clientWireText: WIRE.APPROVE })
    })

    expect(sends()).toHaveLength(0)
    expect(reports(id)).toHaveLength(0)
    expect(result.current.state.stage).toBe('stale')
    expect(result.current.state.staleCard?.action_id).toBe(id)
  })

  it('leaves the card in place when the intent is paused', async () => {
    const id = nextId()
    routes(id, {
      [`POST ${BASE}/actions/${id}/submit`]: () => {
        throw new StubApiError(409, { error: 'paused', code: 'intent_paused', details: {} })
      },
    })
    const { result } = mount()

    await act(async () => {
      await result.current.submit({ card: card(id), payload: { decision: 'approve' }, clientWireText: WIRE.APPROVE })
    })

    expect(sends()).toHaveLength(0)
    expect(result.current.state.stage).toBe('refused')
    expect(result.current.state.refusal?.code).toBe('intent_paused')
  })

  it('skips the slot preflight for the host-control lane', async () => {
    const id = nextId()
    const stop = receipt(id, {
      lane: 'host_control',
      wire_text: null,
      host: { method: 'POST', path: '/api/chat/slots/aidlc-r1-guest/stop', body: {} },
    })
    setApiRoutes({
      'POST /api/chat/slots/aidlc-r1-guest/stop': () => ({ ok: true }),
      [`POST ${BASE}/actions/${id}/delivery`]: () => ({ ok: true, action: card(id), idempotent: false }),
    })
    const { result } = mount()

    await act(async () => {
      await result.current.submit({
        card: card(id, { type: 'force_stop', queue_type: 'force_stop' }),
        payload: null,
        clientWireText: '',
        begin: () => Promise.resolve(stop),
      })
    })

    // No slot listing: a stop is not a prompt, and there is no message that could land in a new slot.
    expect(apiCalls.some((call) => call.path === '/api/chat/slots')).toBe(false)
    expect(reports(id)[0]?.body).toMatchObject({ outcome: 'delivered' })
  })

  it('resolves a studio-only decision without touching the host', async () => {
    const id = nextId()
    setApiRoutes({
      [`POST ${BASE}/actions/${id}/resolve`]: () => ({ ok: true, action: card(id, { status: 'Cancelled' }), created_action_id: null }),
    })
    const { result } = mount()

    await act(async () => {
      await result.current.resolve({ card: card(id), payload: { decision: 'acknowledge' } })
    })

    expect(apiCalls.map((call) => call.path)).toEqual([`${BASE}/actions/${id}/resolve`])
    expect(apiCalls[0]?.body).toEqual({ decision: 'acknowledge', payload: {} })
    expect(result.current.state.stage).toBe('settled')
  })
})
