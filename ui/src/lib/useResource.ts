/**
 * The shared read hook: one fetch per key, an adaptive poll, and revalidation from the event stream.
 *
 * Why not just `useEffect` + `fetch` per component: three properties are needed everywhere and are
 * each easy to get wrong once per page.
 *
 *  - **The last good value survives a refetch.** A queue that blanks while it re-polls makes the user
 *    lose their place every two seconds, and worse, a decision pane that empties mid-read looks like
 *    the action disappeared. `data` only ever changes to a newer *successful* answer; `stale` says
 *    whether a refetch is in flight and `error` says the newest attempt failed while `data` still
 *    holds what was true.
 *  - **The interval follows the work.** Anything mid-flight (`Delivering`/`Delivered`/`Processing`) or
 *    a bound slot that is running means the answer changes in seconds, so the poll runs at 2 s; an
 *    idle registry runs at 15 s (§3.6). Polling never runs while the tab is hidden.
 *  - **Concurrent readers share one request.** Two components asking for `/actions` in the same tick
 *    produce one HTTP call, so the shell's badge and the queue itself cannot disagree.
 *
 * `POST` is deliberately absent: every mutation is an explicit user action with its own error
 * handling, and a hook that could re-issue one on a timer is how a decision gets sent twice.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { isAbort, StudioApiError } from './api'
import { useStudioEvents } from './sse'
import type { StudioEventType } from './types'

/** Poll cadence while something is in flight (§3.6). */
export const FAST_INTERVAL_MS = 2_000
/** Poll cadence when nothing is moving. */
export const SLOW_INTERVAL_MS = 15_000
/** How long a fetch may take before it is abandoned so a wedged read cannot pin the poll forever. */
export const REQUEST_TIMEOUT_MS = 20_000

export interface Resource<T> {
  /** The newest successful value, or `null` before the first one arrives. */
  data: T | null
  /** The newest attempt's failure, or `null`. Non-null with a non-null `data` = "showing older facts". */
  error: StudioApiError | null
  /** True only while the FIRST fetch for this key is in flight. */
  loading: boolean
  /** True while any fetch is in flight, including a background refresh of a value already shown. */
  stale: boolean
  /** Fetch now, out of band. Returns when the attempt settles. */
  refresh: () => Promise<void>
  /** §3.6 spells this `refetch`; both names point at the same function so either contract compiles. */
  refetch: () => Promise<void>
}

export interface ResourceOptions<T> {
  /** Fixed interval in ms. Overrides the adaptive pace entirely (0 disables polling). */
  interval?: number
  /** Interval while busy. Default `FAST_INTERVAL_MS`. */
  fastInterval?: number
  /** Interval while idle. Default `SLOW_INTERVAL_MS`. */
  slowInterval?: number
  /**
   * Whether the current value means "something is moving". Defaults to `looksBusy`, which recognises
   * the shapes the API actually returns rather than requiring every caller to reimplement it.
   */
  busy?: (data: T) => boolean
  /** Event types that make this resource refetch. Omitted = every event. */
  revalidateOn?: readonly (StudioEventType | 'reset')[]
  /** Skip fetching entirely (a detail resource with nothing selected). */
  enabled?: boolean
}

/** In-flight requests by key, so two subscribers in one tick share one HTTP call. */
const inflight = new Map<string, Promise<unknown>>()

const LIVE_STATUSES: readonly string[] = ['Delivering', 'Delivered', 'Processing']

/**
 * Does this payload describe work in progress?
 *
 * Structural on purpose: the same predicate has to answer for `/actions`, `/leases`, one intent and
 * one card, and a per-route flag would be four places to forget. Unknown shapes answer `false` — a
 * wrong "busy" costs a 2 s poll forever on an idle install.
 */
export function looksBusy(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false
  const record = value as Record<string, unknown>

  if (typeof record['live_execution'] === 'number' && record['live_execution'] > 0) return true

  const actions = record['actions']
  if (Array.isArray(actions) && actions.some((a) => isLiveCard(a))) return true
  if (isLiveCard(record['action'])) return true

  const intents = record['intents']
  if (Array.isArray(intents) && intents.some((i) => isRunningIntent(i))) return true
  if (isRunningIntent(record['intent'])) return true
  if (isRunningIntent(record)) return true

  const transaction = record['transaction']
  if (transaction && typeof transaction === 'object') {
    const status = (transaction as Record<string, unknown>)['status']
    if (typeof status === 'string' && status !== 'committed' && status !== 'rolled_back' && status !== 'failed') {
      return true
    }
  }
  return false
}

function isLiveCard(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false
  const record = value as Record<string, unknown>
  const status = record['status']
  if (typeof status === 'string' && LIVE_STATUSES.includes(status)) return true
  const evidence = record['evidence']
  if (evidence && typeof evidence === 'object') {
    const session = (evidence as Record<string, unknown>)['session']
    if (session && typeof session === 'object' && (session as Record<string, unknown>)['running'] === true) {
      return true
    }
  }
  return false
}

function isRunningIntent(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false
  const record = value as Record<string, unknown>
  const state = record['operational_state']
  if (state === 'Running' || state === 'Queued') return true
  const session = record['session']
  return !!session && typeof session === 'object' && (session as Record<string, unknown>)['running'] === true
}

/**
 * Read one resource.
 *
 * `key` identifies the request for deduplication and resets the state when it changes; `null` means
 * "nothing to read" (the hook stays idle rather than fetching a URL built from an empty id). Because
 * concurrent readers with the same key share one answer, the key MUST include every input the fetcher
 * varies on — `actions:${repo}:${intent}`, not `actions`.
 */
export function useResource<T>(
  key: string | null,
  fetcher: (signal: AbortSignal) => Promise<T>,
  options: ResourceOptions<T> = {},
): Resource<T> {
  const {
    interval,
    fastInterval = FAST_INTERVAL_MS,
    slowInterval = SLOW_INTERVAL_MS,
    busy,
    revalidateOn,
    enabled = true,
  } = options

  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<StudioApiError | null>(null)
  const [pending, setPending] = useState(0)

  // Refs so `run` never changes identity: it is a dependency of the poll effect and of the event
  // subscription, and a new function every render would restart the timer on every render.
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher
  const keyRef = useRef(key)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  // A new key is a different resource: the old value must go, or a detail pane briefly shows the
  // previous action's evidence under the new action's title.
  useEffect(() => {
    keyRef.current = key
    setData(null)
    setError(null)
  }, [key])

  const run = useCallback(async (): Promise<void> => {
    const current = keyRef.current
    if (current === null || !enabled) return
    const shared = inflight.get(current)
    if (shared) {
      // Someone else is already asking. Wait for their answer instead of asking again.
      try {
        const value = (await shared) as T
        if (mounted.current && keyRef.current === current) {
          setData(value)
          setError(null)
        }
      } catch {
        // The owner of the request reports its own failure; a waiter re-reporting it would show the
        // same banner twice.
      }
      return
    }

    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
    const promise = fetcherRef.current(controller.signal)
    inflight.set(current, promise)
    setPending((n) => n + 1)
    try {
      const value = await promise
      if (mounted.current && keyRef.current === current) {
        setData(value)
        setError(null)
      }
    } catch (caught) {
      if (mounted.current && keyRef.current === current && !isAbort(caught)) {
        setError(caught instanceof StudioApiError ? caught : new StudioApiError('internal_error', String(caught), {}, 0))
      }
    } finally {
      clearTimeout(timer)
      if (inflight.get(current) === promise) inflight.delete(current)
      if (mounted.current) setPending((n) => Math.max(0, n - 1))
    }
  }, [enabled])

  // First read, and a fresh read whenever the key changes.
  useEffect(() => {
    if (key === null || !enabled) return
    void run()
  }, [key, enabled, run])

  const isBusy = data !== null && (busy ? busy(data) : looksBusy(data))
  const period = interval ?? (isBusy ? fastInterval : slowInterval)

  useEffect(() => {
    if (key === null || !enabled || period <= 0) return
    let timer: ReturnType<typeof setTimeout> | null = null
    const tick = async () => {
      if (typeof document === 'undefined' || !document.hidden) await run()
      timer = setTimeout(tick, period)
    }
    timer = setTimeout(tick, period)

    // A tab that comes back is looked at immediately, so it must not show a 15-second-old queue.
    const onVisible = () => {
      if (typeof document !== 'undefined' && !document.hidden) void run()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      if (timer !== null) clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [key, enabled, period, run])

  // The stream is a hint, not a payload: an event says "this changed", and the resource re-reads the
  // route that owns it. Applying event payloads directly would give the UI a second, thinner copy of
  // the read model that drifts from the one the backend serialises.
  useStudioEvents(
    useCallback(() => {
      void run()
    }, [run]),
    revalidateOn,
  )

  return useMemo(
    () => ({
      data,
      error,
      loading: pending > 0 && data === null,
      stale: pending > 0,
      refresh: run,
      refetch: run,
    }),
    [data, error, pending, run],
  )
}
