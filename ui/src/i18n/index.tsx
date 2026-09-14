/**
 * Translation for Studio's own strings.
 *
 * Deliberately tiny and dependency-free: `i18next` is not in the host's import map, and the host exports
 * no translator, so shipping one is the only option. Both catalogs are bundled (they are ~30 KB together)
 * so a language switch never waits on a fetch.
 *
 * Two rules the catalog gate enforces (`scripts/build_i18n.py`) and this module assumes:
 *   - every key exists in both locales with a non-empty value and the same placeholder set;
 *   - AI-DLC's own words are never translated. Stage slugs, question text, artifact contents, reviewer
 *     findings and audit fields pass through verbatim, because they are the user's project's language,
 *     not ours.
 *
 * A missing key renders the key itself rather than empty space: silent blanks hide a bug in a
 * decision-making UI, and a visible `queue.title` tells a developer exactly what to add.
 */

import { createContext, useContext, useMemo, type ReactNode } from 'react'

import enUS from './en-US.json'
import zhCN from './zh-CN.json'
import { useHostLocale, type StudioLocale } from '../lib/host'

type Catalog = Record<string, string>

const CATALOGS: Record<StudioLocale, Catalog> = {
  'en-US': enUS as Catalog,
  'zh-CN': zhCN as Catalog,
}

export type TranslateParams = Record<string, string | number>

export interface I18n {
  locale: StudioLocale
  /** Look up `key`, substituting `{name}` placeholders. Returns `key` when absent. */
  t: (key: string, params?: TranslateParams) => string
  /** True when the catalog has this key — for optional, backend-provided keys. */
  has: (key: string) => boolean
  /** Locale-aware formatters, built once per locale. */
  fmt: Formatters
}

export interface Formatters {
  date: (iso: string) => string
  dateTime: (iso: string) => string
  /** "3 min", "2 h", "4 d" — a waiting duration, not a precise interval. */
  duration: (seconds: number) => string
  /** Relative to now: "12 minutes ago". */
  since: (iso: string) => string
  number: (value: number) => string
  /** Locale-correct comparison for sorting names the user reads. */
  compare: (a: string, b: string) => number
}

const I18nContext = createContext<I18n | null>(null)

/**
 * Fill `{name}` holes from `params`.
 *
 * A hole with no value becomes `missing` (the locale's "unavailable" phrase) rather than staying as a
 * literal `{stage}`. The backend decides which params a card's headline carries, so a key that gains a
 * placeholder before its sender gains the value is a real possibility — and of the two ways to be
 * wrong, a sentence that reads "AI-DLC finished (not available) and is waiting for your approval" is
 * one a person can still act on, while raw braces read as a broken product.
 *
 * Passing no `params` at all leaves the template untouched: that is a caller saying "this string has no
 * holes", and rewriting its braces would corrupt any literal text that happens to contain them.
 */
function interpolate(template: string, params: TranslateParams | undefined, missing: string): string {
  if (!params) return template
  return template.replace(/\{([a-zA-Z0-9_]+)\}/g, (whole, name: string) => {
    const value = params[name]
    if (value !== undefined) return String(value)
    return missing || whole
  })
}

function buildFormatters(locale: StudioLocale, t: I18n['t']): Formatters {
  const dateFmt = new Intl.DateTimeFormat(locale, { dateStyle: 'medium' })
  const dateTimeFmt = new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' })
  const numberFmt = new Intl.NumberFormat(locale)
  const relative = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  const collator = new Intl.Collator(locale, { numeric: true, sensitivity: 'base' })

  const parse = (iso: string): Date | null => {
    const d = new Date(iso)
    return Number.isNaN(d.getTime()) ? null : d
  }

  return {
    date: (iso) => {
      const d = parse(iso)
      return d ? dateFmt.format(d) : iso
    },
    dateTime: (iso) => {
      const d = parse(iso)
      return d ? dateTimeFmt.format(d) : iso
    },
    duration: (seconds) => {
      const s = Math.max(0, Math.round(seconds))
      if (s < 60) return t('common.justNow')
      if (s < 3600) return t('common.durationMinutes', { n: Math.round(s / 60) })
      if (s < 86400) return t('common.durationHours', { n: Math.round(s / 3600) })
      return t('common.durationDays', { n: Math.round(s / 86400) })
    },
    since: (iso) => {
      const d = parse(iso)
      if (!d) return iso
      const deltaSecs = (d.getTime() - Date.now()) / 1000
      const abs = Math.abs(deltaSecs)
      if (abs < 60) return relative.format(Math.round(deltaSecs), 'second')
      if (abs < 3600) return relative.format(Math.round(deltaSecs / 60), 'minute')
      if (abs < 86400) return relative.format(Math.round(deltaSecs / 3600), 'hour')
      return relative.format(Math.round(deltaSecs / 86400), 'day')
    },
    number: (value) => numberFmt.format(value),
    compare: (a, b) => collator.compare(a, b),
  }
}

/** Build an `I18n` for a locale without React — used by tests and by non-component helpers. */
export function makeI18n(locale: StudioLocale): I18n {
  const catalog = CATALOGS[locale] ?? CATALOGS['en-US']
  const fallback = CATALOGS['en-US']
  // Resolved once, and never through `t` itself: `t('common.unavailable')` would re-enter this closure
  // for every unfilled hole, and would recurse without end if that key were the one missing a param.
  const missing = catalog['common.unavailable'] ?? fallback['common.unavailable'] ?? ''
  const t: I18n['t'] = (key, params) => {
    const template = catalog[key] ?? fallback[key] ?? key
    return interpolate(template, params, missing)
  }
  const has: I18n['has'] = (key) => key in catalog || key in fallback
  return { locale, t, has, fmt: buildFormatters(locale, t) }
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const locale = useHostLocale()
  const value = useMemo(() => makeI18n(locale), [locale])
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18n {
  const value = useContext(I18nContext)
  // A component rendered outside the provider still has to render something useful rather than crash the
  // whole app page, so it falls back to the resolved locale directly.
  return value ?? makeI18n('en-US')
}

/** Convenience for the common case. */
export function useT(): I18n['t'] {
  return useI18n().t
}
