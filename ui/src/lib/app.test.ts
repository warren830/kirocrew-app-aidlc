import { describe, expect, it } from 'vitest'

import { appNameFrom, DEFAULT_APP_NAME } from './app'

describe('appNameFrom', () => {
  it('reads the installed name from the URL the host served the bundle from', () => {
    expect(appNameFrom('http://127.0.0.1:5476/apps/aidlc-studio-next/ui/dist/index.mjs')).toBe('aidlc-studio-next')
    expect(appNameFrom('http://127.0.0.1:5476/apps/aidlc-studio/ui/dist/index.mjs')).toBe('aidlc-studio')
  })

  it('falls back to the shipped name anywhere else', () => {
    expect(appNameFrom('file:///repo/ui/src/lib/app.ts')).toBe(DEFAULT_APP_NAME)
    expect(appNameFrom('http://127.0.0.1:5476/apps/aidlc-studio')).toBe(DEFAULT_APP_NAME)
    expect(appNameFrom('not a url')).toBe(DEFAULT_APP_NAME)
  })
})
