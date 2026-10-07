/**
 * The name the host installed this copy of Studio under.
 *
 * KiroCrew serves the bundle from `/apps/<name>/ui/<entry>`, so the module's own URL names the app. A
 * side-by-side development install (`aidlc-studio-next`) therefore talks to its own backend, page and
 * storage keys without a rebuild. Anywhere else (tests, a file opened locally) it is the shipped name.
 */
export const DEFAULT_APP_NAME = 'aidlc-studio'

export function appNameFrom(url: string): string {
  try {
    const parts = new URL(url).pathname.split('/')
    const at = parts.indexOf('apps')
    const name = at >= 0 && parts[at + 2] === 'ui' ? parts[at + 1] : undefined
    if (name) return decodeURIComponent(name)
  } catch {
    /* not a URL the host served */
  }
  return DEFAULT_APP_NAME
}

export const APP_NAME = appNameFrom(import.meta.url)
