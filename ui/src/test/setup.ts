/**
 * Per-test environment.
 *
 * jsdom does not implement `matchMedia`, and Studio's host helpers subscribe to it for the narrow
 * breakpoint and reduced motion. Without a stub every component that reads them throws on mount, which
 * would look like a component bug rather than a missing browser API.
 */
import '@testing-library/jest-dom/vitest'
import { afterEach, beforeEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

const listeners = new Set<() => void>()

beforeEach(() => {
  if (!window.matchMedia) {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: (_: string, cb: () => void) => void listeners.add(cb),
        removeEventListener: (_: string, cb: () => void) => void listeners.delete(cb),
        addListener: (cb: () => void) => void listeners.add(cb),
        removeListener: (cb: () => void) => void listeners.delete(cb),
        dispatchEvent: () => false,
      })),
    })
  }
  // The host sets these on <html>; the locale/mode helpers read them.
  document.documentElement.dataset.mode = 'dark'
  document.documentElement.dataset.theme = 'dark'
  document.documentElement.lang = 'en-US'
  localStorage.clear()
})

afterEach(() => {
  cleanup()
  listeners.clear()
})
