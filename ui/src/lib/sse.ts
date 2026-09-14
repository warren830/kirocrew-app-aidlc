/**
 * Live updates: one `EventSource` for the whole page, with polling as the fallback.
 *
 * The host's own `useAppEvents` is dead — it listens for `mc:app:<event>` window events that nothing
 * in the dashboard dispatches (`03 §0`, addendum A1) — so Studio serves its own stream at
 * `GET /events` (§2.12) and this module is the only consumer.
 *
 * Four behaviours that each fix a specific way a live view goes silently stale:
 *
 * 1. **Typed frames need typed listeners.** Every frame carries `event: <type>`, so `EventSource`
 *    dispatches a `<type>` event and `onmessage` NEVER fires. Subscribing to `message` only — the
 *    obvious implementation — produces a connection that receives nothing and looks healthy.
 * 2. **`hello` and `reset` mean "refetch everything".** `hello` is a cold start with no replay;
 *    `reset` means the cursor fell off the ring (or the storage file was rebuilt and `seq` restarted).
 *    Both are surfaced to subscribers as a synthetic `reset` so a view reloads instead of guessing.
 * 3. **Reconnect is ours, not the browser's.** `EventSource`'s own retry is uncontrollable and would
 *    hammer a restarting gateway, so the stream is closed on error and reopened on a capped backoff
 *    (1 s → 30 s) with the resume cursor.
 * 4. **Polling covers the gap.** While disconnected the same ring is drained through
 *    `GET /events/poll`, so a browser without `EventSource`, or one behind a proxy that buffers
 *    `text/event-stream`, still updates. Polling pauses while the tab is hidden.
 */

import {
  createContext, createElement, useCallback, useContext, useEffect, useRef, useState, type ReactNode,
} from 'react'

import { API_BASE } from './api'
import type { EventPollResponse, StudioEventType } from './types'

/** One frame as a subscriber sees it. `seq` is `-1` for the synthetic reset (it has no ring row). */
export interface StreamEvent {
  type: StudioEventType | 'reset'
  seq: number
  payload: Record<string, unknown>
}

export type StreamMode = 'connecting' | 'live' | 'polling' | 'offline'

export interface StreamStatus {
  mode: StreamMode
  /** The last `id` seen; the value a reconnect resumes from. */
  cursor: number | null
  /** Failed connection attempts since the last successful open — drives the backoff and the banner. */
  attempts: number
}

/** Every ring type, needed because each one needs its own `addEventListener`. */
const RING_TYPES: readonly StudioEventType[] = [
  'action.created', 'action.updated', 'repo.updated', 'repo.removed', 'intent.updated',
  'transaction.updated', 'lease.updated', 'activity.appended', 'advisor.updated',
  'settings.updated', 'health.updated', 'migration.updated', 'reset',
]

export const BACKOFF_MIN_MS = 1_000
export const BACKOFF_MAX_MS = 30_000
/** Poll cadence while the stream is down. Slower than the fast resource interval on purpose: the ring
 *  is a hint to refetch, and two clocks racing at 2 s would double every request. */
export const POLL_INTERVAL_MS = 5_000

type Handler = (event: StreamEvent) => void
type PollFn = (cursor: number | null) => Promise<EventPollResponse>

interface Bus {
  subscribe: (handler: Handler) => () => void
}

const BusContext = createContext<Bus | null>(null)
const StatusContext = createContext<StreamStatus>({ mode: 'offline', cursor: null, attempts: 0 })

export interface EventStreamProviderProps {
  /** `api.pollEvents` — injected so the fallback goes through the SDK's allowlisted fetch. */
  poll: PollFn
  /** Off in tests that do not exercise the stream, and while the page has no session. */
  enabled?: boolean
  children: ReactNode
}

/**
 * Owns the single connection. Mounted once by the shell.
 *
 * Subscribers are held in a ref-stable `Set` so adding one never reconnects the stream: a queue that
 * re-subscribes on every render would otherwise drop and re-open the socket on every keystroke.
 */
export function EventStreamProvider({ poll, enabled = true, children }: EventStreamProviderProps) {
  const handlers = useRef<Set<Handler>>(new Set())
  const cursor = useRef<number | null>(null)
  const [status, setStatus] = useState<StreamStatus>({ mode: 'connecting', cursor: null, attempts: 0 })

  const bus = useRef<Bus>({
    subscribe: (handler) => {
      handlers.current.add(handler)
      return () => void handlers.current.delete(handler)
    },
  })

  const emit = useCallback((event: StreamEvent) => {
    if (event.seq >= 0) cursor.current = event.seq
    // A copy: a handler that unsubscribes while being called must not mutate the set being iterated.
    for (const handler of [...handlers.current]) handler(event)
  }, [])

  useEffect(() => {
    if (!enabled) {
      setStatus((s) => ({ ...s, mode: 'offline' }))
      return
    }
    let stopped = false
    let source: EventSource | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let pollTimer: ReturnType<typeof setTimeout> | null = null
    let attempts = 0

    const setMode = (mode: StreamMode) =>
      setStatus({ mode, cursor: cursor.current, attempts })

    const stopPolling = () => {
      if (pollTimer !== null) clearTimeout(pollTimer)
      pollTimer = null
    }

    const pollOnce = async () => {
      if (stopped) return
      // A hidden tab is not looked at; draining the ring into it wastes the gateway's time and the
      // user's battery, and the next visible poll catches up from the same cursor anyway.
      if (typeof document === 'undefined' || !document.hidden) {
        try {
          const body = await poll(cursor.current)
          if (stopped) return
          if (body.reset) {
            cursor.current = body.cursor
            emit({ type: 'reset', seq: -1, payload: { oldest_seq: body.oldest_seq, cursor: body.cursor } })
          } else {
            for (const record of body.events) {
              emit({ type: record.type, seq: record.seq, payload: record.payload })
            }
            cursor.current = body.cursor
          }
        } catch {
          // A failed poll is not worth reporting: the banner already says updates are degraded, and
          // the next tick retries. Throwing here would take the whole page down with it.
        }
      }
      if (!stopped) pollTimer = setTimeout(pollOnce, POLL_INTERVAL_MS)
    }

    const startPolling = () => {
      if (pollTimer !== null) return
      pollTimer = setTimeout(pollOnce, 0)
    }

    const scheduleReconnect = () => {
      attempts += 1
      const delay = Math.min(BACKOFF_MAX_MS, BACKOFF_MIN_MS * 2 ** (attempts - 1))
      // Jitter so a gateway restart does not get every open tab back at the same millisecond.
      const jittered = delay * (0.75 + Math.random() * 0.5)
      reconnectTimer = setTimeout(connect, jittered)
    }

    function connect() {
      if (stopped) return
      if (typeof EventSource === 'undefined') {
        // No `EventSource` at all (older embedded webview, jsdom): polling is the whole transport.
        setMode('polling')
        startPolling()
        return
      }
      const url = cursor.current === null ? `${API_BASE}/events` : `${API_BASE}/events?cursor=${cursor.current}`
      let stream: EventSource
      try {
        stream = new EventSource(url)
      } catch {
        setMode('polling')
        startPolling()
        scheduleReconnect()
        return
      }
      source = stream
      setMode(attempts === 0 ? 'connecting' : 'polling')

      stream.onopen = () => {
        attempts = 0
        stopPolling()
        setMode('live')
      }
      stream.onerror = () => {
        // The browser would retry by itself here; closing first is what makes the backoff ours.
        stream.close()
        if (source === stream) source = null
        if (stopped) return
        setMode('polling')
        startPolling()
        scheduleReconnect()
      }
      const onFrame = (type: StudioEventType | 'reset') => (raw: Event) => {
        const message = raw as MessageEvent<string>
        const seq = Number(message.lastEventId)
        let payload: Record<string, unknown> = {}
        try {
          const parsed: unknown = JSON.parse(message.data)
          if (parsed && typeof parsed === 'object') payload = parsed as Record<string, unknown>
        } catch {
          // A frame Studio did not write. Keep the cursor moving so a reconnect does not replay it.
        }
        emit({ type, seq: Number.isFinite(seq) ? seq : -1, payload })
      }
      for (const type of RING_TYPES) stream.addEventListener(type, onFrame(type))
      // `hello` is not a ring row: it means "you have no cursor", so everything must be fetched.
      stream.addEventListener('hello', (raw) => {
        const message = raw as MessageEvent<string>
        const seq = Number(message.lastEventId)
        if (Number.isFinite(seq)) cursor.current = seq
        emit({ type: 'reset', seq: -1, payload: { hello: true } })
      })
    }

    connect()
    return () => {
      stopped = true
      if (reconnectTimer !== null) clearTimeout(reconnectTimer)
      stopPolling()
      source?.close()
    }
  }, [enabled, poll, emit])

  return createElement(
    StatusContext.Provider,
    { value: status },
    createElement(BusContext.Provider, { value: bus.current }, children),
  )
}

/**
 * Run `handler` for every event of the given types (all types when omitted).
 *
 * A no-op without a provider, deliberately: a component test that renders one card must not have to
 * stand up a transport, and "no live updates" is a degradation, not a crash.
 */
export function useStudioEvents(
  handler: Handler,
  types?: readonly (StudioEventType | 'reset')[],
): void {
  const bus = useContext(BusContext)
  const latest = useRef(handler)
  latest.current = handler
  // The type list is compared by content, so an inline array literal does not resubscribe every render.
  const key = types ? types.join(',') : ''

  useEffect(() => {
    if (!bus) return
    const wanted = key ? new Set(key.split(',')) : null
    return bus.subscribe((event) => {
      if (wanted && !wanted.has(event.type)) return
      latest.current(event)
    })
  }, [bus, key])
}

export function useStreamStatus(): StreamStatus {
  return useContext(StatusContext)
}
