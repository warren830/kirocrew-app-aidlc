import { describe, expect, it } from 'vitest'

import { readLocale, setStudioLocale, STUDIO_LOCALES } from './host'
import { makeI18n } from '../i18n'
import { WIRE } from './wire.generated'
import { ERROR_CODES } from './errorCodes.generated'
import { ACTION_STATUSES, DECISIONS } from './enums.generated'

describe('locale resolution', () => {
  it('follows the dashboard language, and a Studio override beats it', () => {
    document.documentElement.lang = 'zh-CN'
    expect(readLocale()).toBe('zh-CN')
    setStudioLocale('en-US')
    expect(readLocale()).toBe('en-US')
    setStudioLocale(null)
    expect(readLocale()).toBe('zh-CN')
  })

  it('falls back to English for a language Studio does not ship', () => {
    document.documentElement.lang = 'fr-FR'
    expect(readLocale()).toBe('en-US')
  })

  it('matches a regional variant onto the shipped catalog', () => {
    document.documentElement.lang = 'zh-Hans-CN'
    expect(readLocale()).toBe('zh-CN')
  })
})

describe('catalogs', () => {
  it.each(STUDIO_LOCALES)('%s translates every action status and every error code', (locale) => {
    const { t } = makeI18n(locale)
    for (const status of ACTION_STATUSES) {
      const key = `enum.actionStatus.${status}`
      expect(t(key), key).not.toBe(key)
    }
    for (const code of Object.keys(ERROR_CODES)) {
      const key = `errors.${code}`
      expect(t(key), key).not.toBe(key)
    }
  })

  it('interpolates parameters and leaves an unknown key visible', () => {
    const { t } = makeI18n('en-US')
    expect(t('common.durationMinutes', { n: 7 })).toContain('7')
    expect(t('nope.missing')).toBe('nope.missing')
  })

  it('formats dates and durations in the active locale', () => {
    const en = makeI18n('en-US')
    const zh = makeI18n('zh-CN')
    expect(en.fmt.duration(30)).toBe(en.t('common.justNow'))
    expect(en.fmt.duration(600)).toContain('10')
    expect(en.fmt.date('2026-09-04T10:00:00Z')).not.toBe(zh.fmt.date('2026-09-04T10:00:00Z'))
    expect(en.fmt.date('not-a-date')).toBe('not-a-date')
  })
})

describe('generated protocol sources', () => {
  it('carries the casing distinction the conductor depends on', () => {
    // A gate says "Request Changes"; the summary checkpoint says "Request changes". Collapsing the two
    // would send a label the conductor does not recognise at one of the two boundaries.
    expect(WIRE.REQUEST_CHANGES_PREFIX).toBe('Request Changes: ')
    expect(WIRE.SUMMARY_REQUEST_CHANGES_PREFIX).toBe('Request changes: ')
    expect(WIRE.APPROVE).toBe('Approve')
  })

  it('never offers a wire text for picking the active intent', () => {
    // Picking an intent is a cursor switch, not a prompt: sending it as a message would mint a human
    // turn for a navigation step.
    expect(Object.keys(WIRE)).not.toContain('INTENT_PICK_PREFIX')
    expect(DECISIONS).toContain('pick_intent')
  })
})
