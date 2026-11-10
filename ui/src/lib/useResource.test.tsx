import { act, render, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { BusContext, EventStreamProvider, POLL_INTERVAL_MS, useStudioEvents, type StreamEvent } from './sse'
import type { EventPollResponse } from './types'
import { useResource } from './useResource'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

/** A bus the test drives by hand, and fetches it answers by hand, in any order. */
function harness() {
  const handlers = new Set<(event: StreamEvent) => void>()
  const bus = { subscribe: (h: (event: StreamEvent) => void) => (handlers.add(h), () => void handlers.delete(h)) }
  const calls: { resolve: (value: string) => void }[] = []
  const fetcher = () => new Promise<string>((resolve) => calls.push({ resolve }))
  const wrapper = ({ children }: { children: ReactNode }) => createElement(BusContext.Provider, { value: bus }, children)
  // One object for every subscriber, as the provider's own emit does.
  const emit = (seq: number) => {
    const event: StreamEvent = { type: 'repo.updated', seq, payload: {} }
    act(() => [...handlers].forEach((h) => h(event)))
  }
  return { calls, fetcher, wrapper, emit }
}

describe('useResource and the event stream', () => {
  it('reads again for a change announced while an older read is on the wire, and keeps the newer answer', async () => {
    const h = harness()
    const { result } = renderHook(() => useResource('res-stale', h.fetcher, { interval: 0 }), { wrapper: h.wrapper })
    await waitFor(() => expect(h.calls).toHaveLength(1)) // the first read, issued before the change
    h.emit(1)
    await waitFor(() => expect(h.calls).toHaveLength(2)) // not joined: it may predate the change
    await act(async () => h.calls[1]!.resolve('after the install'))
    await act(async () => h.calls[0]!.resolve('before the install'))
    expect(result.current.data).toBe('after the install')
  })

  it('shares one read among every subscriber of the same change', async () => {
    const h = harness()
    renderHook(
      () => [useResource('res-shared', h.fetcher, { interval: 0 }), useResource('res-shared', h.fetcher, { interval: 0 })],
      { wrapper: h.wrapper },
    )
    await waitFor(() => expect(h.calls).toHaveLength(1))
    await act(async () => h.calls[0]!.resolve('first'))
    h.emit(2)
    await waitFor(() => expect(h.calls).toHaveLength(2))
    await new Promise((r) => setTimeout(r, 20))
    expect(h.calls).toHaveLength(2)
  })
})

describe('useResource polling', () => {
  it('stops reading once unmounted, even when the unmount lands while a poll read is on the wire', async () => {
    vi.useFakeTimers()
    const h = harness()
    const { unmount } = renderHook(() => useResource('res-poll-unmount', h.fetcher, { interval: 1000 }))
    expect(h.calls).toHaveLength(1)
    await act(async () => h.calls[0]!.resolve('first'))
    await act(async () => { vi.advanceTimersByTime(1000) })
    expect(h.calls).toHaveLength(2) // the poll's read is on the wire

    unmount() // the user leaves the page
    await act(async () => h.calls[1]!.resolve('second'))
    for (let i = 0; i < 5; i += 1) {
      await act(async () => { vi.advanceTimersByTime(1000) })
      h.calls.at(-1)!.resolve('again')
    }
    expect(h.calls).toHaveLength(2)
  })

  it('keeps one poll loop when an event-driven read changes the pace while a poll read is on the wire', async () => {
    vi.useFakeTimers()
    const h = harness()
    const pace = (value: string) => value === 'busy'
    renderHook(() => useResource('res-poll-pace', h.fetcher, { fastInterval: 1000, slowInterval: 10_000, busy: pace }),
      { wrapper: h.wrapper })
    await act(async () => h.calls[0]!.resolve('busy'))
    await act(async () => { vi.advanceTimersByTime(1000) })
    expect(h.calls).toHaveLength(2) // the 1 s poll's read is on the wire
    h.emit(1)
    expect(h.calls).toHaveLength(3)
    await act(async () => h.calls[2]!.resolve('idle')) // the pace is now 10 s
    await act(async () => h.calls[1]!.resolve('idle')) // the old poll's read lands after that

    const before = h.calls.length
    for (let i = 0; i < 19; i += 1) {
      await act(async () => { vi.advanceTimersByTime(500) })
      h.calls.at(-1)!.resolve('idle')
    }
    expect(h.calls.length - before).toBe(0)
  })

  it.each([
    ['committed', 0],
    ['rolled_back', 0],
    ['failed', 0],
    ['recovery_required', 0],
    ['staged', 5],
    ['rolling_back', 5],
  ])('reads a %s transaction %i times in the next 10 s', async (status, reads) => {
    vi.useFakeTimers()
    const fetcher = vi.fn(async () => ({ transaction: { status } }))
    renderHook(() => useResource(`res-poll-tx-${status}`, fetcher))
    await act(async () => {})
    expect(fetcher).toHaveBeenCalledTimes(1)
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
    expect(fetcher).toHaveBeenCalledTimes(1 + reads)
  })
})

describe('the event stream provider', () => {
  class FakeEventSource {
    static all: FakeEventSource[] = []
    onopen: (() => void) | null = null
    onerror: (() => void) | null = null
    closed = false
    constructor(readonly url: string) {
      FakeEventSource.all.push(this)
    }
    addEventListener() {}
    close() {
      this.closed = true
    }
  }

  function Recorder({ seen }: { seen: StreamEvent[] }) {
    useStudioEvents((event) => void seen.push(event))
    return null
  }

  /** A provider whose stream the test opens and drops by hand, and whose polls it answers by hand. */
  function stream() {
    vi.useFakeTimers()
    FakeEventSource.all = []
    vi.stubGlobal('EventSource', FakeEventSource)
    const answers: ((body: EventPollResponse) => void)[] = []
    const poll = vi.fn(() => new Promise<EventPollResponse>((resolve) => answers.push(resolve)))
    const seen: StreamEvent[] = []
    render(<EventStreamProvider poll={poll}><Recorder seen={seen} /></EventStreamProvider>)
    let answered = 0
    // A healthy gateway: every poll still on the wire gets its (empty) answer.
    const answerAll = async () => act(async () => {
      while (answered < answers.length) answers[answered++]!({ events: [], cursor: 3, oldest_seq: 0, reset: false })
    })
    const answerFirst = async (body: EventPollResponse) => act(async () => {
      answered = Math.max(answered, 1)
      answers[0]!(body)
    })
    return { poll, seen, answerAll, answerFirst, sources: FakeEventSource.all }
  }

  it('stops polling once the stream is live again, and drops the answer of a poll that was on the wire as it opened', async () => {
    const s = stream()
    act(() => s.sources[0]!.onerror?.()) // the gateway restarts: poll, and reconnect later
    await act(async () => { vi.advanceTimersByTime(0) })
    expect(s.poll).toHaveBeenCalledTimes(1)
    await act(async () => { vi.advanceTimersByTime(1500) }) // the reconnect, while that poll is on the wire
    const live = s.sources[1]!
    act(() => live.onopen?.())
    await s.answerFirst({
      events: [{ seq: 4, at: '2026-11-09T00:00:00Z', type: 'repo.updated', payload: {} }], cursor: 4, oldest_seq: 0, reset: false,
    })

    for (let i = 0; i < 12; i += 1) {
      await act(async () => { vi.advanceTimersByTime(POLL_INTERVAL_MS) })
      await s.answerAll()
    }
    expect(live.closed).toBe(false)
    expect(s.poll).toHaveBeenCalledTimes(1)
    // The stream resumes from the cursor it opened with, so it delivers seq 4 itself.
    expect(s.seen).toEqual([])
  })

  it('runs one poll loop when the stream opens and drops again while a poll is on the wire', async () => {
    const s = stream()
    act(() => s.sources[0]!.onerror?.())
    await act(async () => { vi.advanceTimersByTime(0) })
    await act(async () => { vi.advanceTimersByTime(1500) })
    const flapping = s.sources[1]!
    act(() => flapping.onopen?.())
    act(() => flapping.onerror?.()) // polling again, before the first poll has answered
    await act(async () => { vi.advanceTimersByTime(0) })
    expect(s.poll).toHaveBeenCalledTimes(2)
    await s.answerAll() // both polls answer, the first one late

    for (let i = 0; i < 6; i += 1) {
      await act(async () => { vi.advanceTimersByTime(POLL_INTERVAL_MS) })
      await s.answerAll()
    }
    expect(s.poll).toHaveBeenCalledTimes(2 + 6)
  })
})
