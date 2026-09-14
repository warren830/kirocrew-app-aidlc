/**
 * Number and time formatting that refuses to invent a value.
 *
 * The whole point: an unobservable quantity renders as the *unavailable* string, never as `0`.
 * PRD §9.9 / P-07 ("Estimate honestly") and the visual spec's budget template both hinge on this —
 * "Credits 0" reads as "this costs nothing", while "Credits Unavailable — not observable" is the
 * truth. The rule here is mechanical: `null`, `undefined` and `NaN` are unavailable; `0` is a number
 * and prints as one.
 *
 * Every function takes the `I18n` object rather than reaching for a global, so the same call is
 * testable per locale and no string escapes the catalogue.
 */

import type { I18n, TranslateParams } from '../i18n'
import type { I18nRef, Range } from './types'

/** True when a value is a real, finite number (and therefore printable, `0` included). */
function real(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/** `common.unavailable` — the one string for "Studio cannot observe this". */
export function unavailable(i18n: I18n): string {
  return i18n.t('common.unavailable')
}

/** A count. `0` prints as `0`; a missing count prints as unavailable. */
export function count(i18n: I18n, value: number | null | undefined): string {
  return real(value) ? i18n.fmt.number(value) : unavailable(i18n)
}

/** A duration in seconds as a coarse human span ("3 min", "2 h"). Negative clamps to zero. */
export function duration(i18n: I18n, seconds: number | null | undefined): string {
  return real(seconds) ? i18n.fmt.duration(seconds) : unavailable(i18n)
}

/**
 * How long something has been waiting, from its boundary timestamp.
 *
 * An unparseable or absent timestamp is unavailable rather than "just now": a queue row claiming a
 * gate opened seconds ago when Studio does not know when it opened is a lie about how urgent it is.
 */
export function waiting(i18n: I18n, iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return unavailable(i18n)
  const then = Date.parse(iso)
  if (!Number.isFinite(then)) return unavailable(i18n)
  return i18n.fmt.duration(Math.max(0, (now - then) / 1000))
}

/** A timestamp as "12 minutes ago". */
export function since(i18n: I18n, iso: string | null | undefined): string {
  if (!iso) return unavailable(i18n)
  return Number.isFinite(Date.parse(iso)) ? i18n.fmt.since(iso) : unavailable(i18n)
}

/** A timestamp as a locale date and time. */
export function at(i18n: I18n, iso: string | null | undefined): string {
  if (!iso) return unavailable(i18n)
  return Number.isFinite(Date.parse(iso)) ? i18n.fmt.dateTime(iso) : unavailable(i18n)
}

/**
 * An estimate range, in its own unit.
 *
 * A `secs` range is rendered as two durations because "14400 – 25200" is not an estimate a human can
 * use; a `turns` range keeps the raw numbers, which are exact counts of a thing the user recognises.
 */
export function range(i18n: I18n, value: Range | null | undefined): string {
  if (!value || !real(value.low) || !real(value.high)) return unavailable(i18n)
  const [low, high] =
    value.unit === 'secs'
      ? [i18n.fmt.duration(value.low), i18n.fmt.duration(value.high)]
      : [i18n.fmt.number(value.low), i18n.fmt.number(value.high)]
  return i18n.t('shell.format.range', { low, high })
}

/** A used/limit pair. Either half missing makes the whole thing unavailable, not "n / 0". */
export function ratio(i18n: I18n, used: number | null | undefined, limit: number | null | undefined): string {
  if (!real(used) || !real(limit)) return unavailable(i18n)
  return i18n.t('shell.format.ratio', { used: i18n.fmt.number(used), cap: i18n.fmt.number(limit) })
}

const BYTE_UNITS = ['shell.format.bytes', 'shell.format.kb', 'shell.format.mb'] as const

/** A file size. Binary steps, one decimal above a kilobyte, unavailable when unknown. */
export function bytes(i18n: I18n, value: number | null | undefined): string {
  if (!real(value)) return unavailable(i18n)
  let scaled = Math.max(0, value)
  let unit = 0
  while (scaled >= 1024 && unit < BYTE_UNITS.length - 1) {
    scaled /= 1024
    unit += 1
  }
  const key = BYTE_UNITS[unit] ?? BYTE_UNITS[0]
  const rounded = unit === 0 ? Math.round(scaled) : Math.round(scaled * 10) / 10
  return i18n.t(key, { n: i18n.fmt.number(rounded) })
}

/**
 * A backend `I18nRef`'s params, flattened for `t()`.
 *
 * Card headline params carry arrays (`recovery` sends `codes: [...]`) and booleans, which `t()` cannot
 * interpolate. Arrays join with the locale's list separator; `null`/`undefined` become the unavailable
 * string so a template hole never renders as the word "null".
 */
export function refParams(i18n: I18n, ref: I18nRef | null | undefined): TranslateParams {
  const out: TranslateParams = {}
  if (!ref) return out
  for (const [name, raw] of Object.entries(ref.params)) {
    if (raw === null || raw === undefined) out[name] = unavailable(i18n)
    else if (Array.isArray(raw)) out[name] = raw.map((item) => String(item)).join(i18n.t('shell.format.listJoin'))
    else if (typeof raw === 'number') out[name] = real(raw) ? raw : unavailable(i18n)
    else if (typeof raw === 'boolean') out[name] = String(raw)
    else out[name] = String(raw)
  }
  return out
}

/** Translate a backend-supplied reference. Falls back to `fallbackKey` when the catalogue lacks it. */
export function ref(i18n: I18n, value: I18nRef | null | undefined, fallbackKey?: string): string {
  if (!value) return ''
  const params = refParams(i18n, value)
  // Recovery seeds carry `codes`, while older records can carry a single `reason`.
  // Resolve both shapes here so the queue, heading and evidence brief name the same cause.
  if (value.key.startsWith('action.recovery.')) {
    const codes = Array.isArray(value.params.codes)
      ? value.params.codes.filter((code): code is string => typeof code === 'string' && code.trim().length > 0)
      : []
    const reasons = codes.length > 0
      ? codes
      : typeof value.params.reason === 'string' && value.params.reason.trim()
        ? [value.params.reason]
        : []
    params.reason = reasons.length > 0
      ? reasons.map((reason) => {
          const key = `action.reason.${reason}`
          return i18n.has(key) ? i18n.t(key, params) : reason
        }).join(i18n.t('shell.format.listJoin'))
      : i18n.t('action.reason.recovery_details')
  }
  if (i18n.has(value.key)) return i18n.t(value.key, params)
  return fallbackKey ? i18n.t(fallbackKey, params) : value.key
}

/**
 * Pick the `_one` / `_other` form.
 *
 * There is no plural engine (§3.3), and there does not need to be for two locales: `zh-CN` has one
 * form and `en-US` has two. A third locale with more forms would need a real rule, which is why the
 * choice is made here rather than duplicated at each call site.
 */
export function plural(i18n: I18n, base: string, n: number, params: TranslateParams = {}): string {
  const key = `${base}_${Math.abs(n) === 1 ? 'one' : 'other'}`
  return i18n.t(key, { ...params, n: i18n.fmt.number(n) })
}
