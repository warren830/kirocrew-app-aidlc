/**
 * What the dashboard host tells us, read the way the host actually keeps it current.
 *
 * The App SDK looks like it covers this, but three of its hooks do not work in the shipped host and one
 * is subtly wrong, so this module reads the DOM contract instead (verified against the running gateway
 * bundle — see docs/research/03-kirocrew-frontend-host-contract.md §0):
 *
 *   - `useTheme().mode` reads `data-theme`, which is only `dark`/`light` for the default theme; every
 *     other theme sets `<slug>-dark`. `data-mode` is the attribute that always holds the resolved mode.
 *   - `useAppEvents` subscribes to window events the host never dispatches, so it can never fire.
 *   - `useNotify` dispatches a toast event that has no listener anywhere in the dashboard.
 *   - there is no locale hook at all; `<html lang>` is what `LanguageProvider` keeps in sync.
 *
 * Everything here is a `useSyncExternalStore` over an attribute or a media query, so a theme or language
 * change repaints without polling.
 */

import { useCallback, useSyncExternalStore } from 'react'

export type HostMode = 'dark' | 'light'
export type StudioLocale = 'en-US' | 'zh-CN'

export const STUDIO_LOCALES: readonly StudioLocale[] = ['en-US', 'zh-CN']
const DEFAULT_LOCALE: StudioLocale = 'en-US'

/** The dashboard's own mirror of the user's language choice; `''` means "follow the browser". */
const HOST_LANG_KEY = 'mc-lang'
/** Studio's optional override, set from its Settings page. */
export const STUDIO_LOCALE_KEY = 'aidlc-studio:locale'
export const STUDIO_ORGANIZE_KEY = 'aidlc-studio:organize'

const MOBILE_QUERY = '(max-width: 767px)'

function subscribeToAttributes(names: string[]): (onChange: () => void) => () => void {
  return (onChange: () => void) => {
    if (typeof MutationObserver === 'undefined') return () => {}
    const observer = new MutationObserver(onChange)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: names })
    return () => observer.disconnect()
  }
}

function readMode(): HostMode {
  const root = document.documentElement
  const explicit = root.dataset.mode
  if (explicit === 'dark' || explicit === 'light') return explicit
  // Fallback for hosts that predate `data-mode`: every dark theme slug contains "dark".
  return (root.dataset.theme ?? 'dark').includes('dark') ? 'dark' : 'light'
}

/** The resolved light/dark mode, updating when the user switches theme. */
export function useHostMode(): HostMode {
  return useSyncExternalStore(subscribeToAttributes(['data-mode', 'data-theme']), readMode, () => 'dark')
}

/** The host's theme slug (e.g. `monokai-dark`), for the rare case a component needs it. */
export function useHostTheme(): string {
  return useSyncExternalStore(
    subscribeToAttributes(['data-theme']),
    () => document.documentElement.dataset.theme ?? 'dark',
    () => 'dark',
  )
}

function matchLocale(tag: string | null | undefined): StudioLocale | null {
  if (!tag) return null
  const wanted = tag.trim().toLowerCase()
  if (!wanted) return null
  const exact = STUDIO_LOCALES.find((l) => l.toLowerCase() === wanted)
  if (exact) return exact
  const primary = wanted.split('-')[0]
  return STUDIO_LOCALES.find((l) => l.split('-')[0] === primary) ?? null
}

function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null // private mode / storage disabled — fall through to the next source
  }
}

/**
 * Precedence: Studio's own override, then the dashboard's language choice, then `<html lang>` (which the
 * host rewrites on every switch), then the browser's preferences, then English. The host choice beats
 * `<html lang>` for "Auto", where `lang` holds the *resolved* tag and the stored value is `''`.
 */
export function readLocale(): StudioLocale {
  return (
    matchLocale(readStored(STUDIO_LOCALE_KEY)) ??
    matchLocale(readStored(HOST_LANG_KEY)) ??
    matchLocale(typeof document !== 'undefined' ? document.documentElement.lang : null) ??
    (typeof navigator !== 'undefined'
      ? (navigator.languages ?? [navigator.language]).map(matchLocale).find(Boolean) ?? null
      : null) ??
    DEFAULT_LOCALE
  )
}

export function useHostLocale(): StudioLocale {
  const subscribe = useCallback((onChange: () => void) => {
    const detach = subscribeToAttributes(['lang'])(onChange)
    // `storage` only fires in OTHER tabs, so Studio's own Settings page dispatches this event itself.
    window.addEventListener('storage', onChange)
    window.addEventListener('aidlc-studio:locale-changed', onChange)
    return () => {
      detach()
      window.removeEventListener('storage', onChange)
      window.removeEventListener('aidlc-studio:locale-changed', onChange)
    }
  }, [])
  return useSyncExternalStore(subscribe, readLocale, () => DEFAULT_LOCALE)
}

/** Persist Studio's locale override (`null` clears it and returns to following the dashboard). */
export function setStudioLocale(locale: StudioLocale | null): void {
  try {
    if (locale === null) localStorage.removeItem(STUDIO_LOCALE_KEY)
    else localStorage.setItem(STUDIO_LOCALE_KEY, locale)
  } catch {
    // A locale that cannot be persisted still applies for this session.
  }
  window.dispatchEvent(new CustomEvent('aidlc-studio:locale-changed'))
}

/**
 * Studio's own mobile breakpoint. The host has an `isMobile` of its own but does not expose it, and the
 * page's usable width is not the window's anyway (the nav rail and side panels take 74–236px), so a
 * container-relative check is the honest one for layout while this stays for coarse decisions.
 */
export function useIsNarrow(): boolean {
  const subscribe = useCallback((onChange: () => void) => {
    if (typeof window.matchMedia !== 'function') return () => {}
    const mq = window.matchMedia(MOBILE_QUERY)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return useSyncExternalStore(
    subscribe,
    () => (typeof window.matchMedia === 'function' ? window.matchMedia(MOBILE_QUERY).matches : false),
    () => false,
  )
}

/** True when the user asked the OS to reduce motion; every animation must honour it. */
export function usePrefersReducedMotion(): boolean {
  const subscribe = useCallback((onChange: () => void) => {
    if (typeof window.matchMedia !== 'function') return () => {}
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return useSyncExternalStore(
    subscribe,
    () =>
      typeof window.matchMedia === 'function'
        ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
        : false,
    () => false,
  )
}

/**
 * Load Studio's stylesheet once. The host does not load app CSS, and the bundle cannot rely on Tailwind
 * classes it did not compile, so the layout lives in a real stylesheet served from the app's ui/ path.
 */
export function ensureStylesheet(href = '/apps/aidlc-studio/ui/dist/style.css', id = 'aidlc-studio-css'): void {
  if (typeof document === 'undefined' || document.getElementById(id)) return
  const link = document.createElement('link')
  link.id = id
  link.rel = 'stylesheet'
  link.href = href
  document.head.appendChild(link)
}

/** Optional host modules that are not in the import map; absent on older hosts. */
export function hostModule<T = unknown>(key: string): T | null {
  const registry = (globalThis as { __kirocrew_modules?: Record<string, unknown> }).__kirocrew_modules
  return (registry?.[key] as T | undefined) ?? null
}
