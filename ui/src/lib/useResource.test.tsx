import { act, renderHook, waitFor } from '@testing-library/react'
import { createElement, type ReactNode } from 'react'
import { describe, expect, it } from 'vitest'

import { BusContext, type StreamEvent } from './sse'
import { useResource } from './useResource'

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
