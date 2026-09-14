/**
 * The formatting invariant: an unobservable quantity is never a number.
 *
 * PRD P-07 and FR-NIGHT-007 both turn on this. "Credits 0" tells the user this run is free; "Credits
 * Unavailable" tells them Studio cannot see it. The same applies to a waiting duration with no boundary
 * timestamp and to an estimate with no range: rendering `0` would be Studio asserting something it has
 * no evidence for.
 */

import { describe, expect, it } from 'vitest'

import { makeI18n } from '../i18n'
import { bytes, count, duration, plural, range, ratio, refParams, waiting } from './format'
import type { Range } from './types'

const en = makeI18n('en-US')
const zh = makeI18n('zh-CN')
const UNAVAILABLE = 'Unavailable'

describe('never invent a number', () => {
  it('renders a missing count as unavailable and a real zero as zero', () => {
    expect(count(en, null)).toBe(UNAVAILABLE)
    expect(count(en, undefined)).toBe(UNAVAILABLE)
    expect(count(en, Number.NaN)).toBe(UNAVAILABLE)
    expect(count(en, 0)).toBe('0')
    expect(count(en, 1234)).toBe('1,234')
  })

  it('renders a missing or unparseable duration as unavailable', () => {
    expect(duration(en, null)).toBe(UNAVAILABLE)
    expect(duration(en, 0)).toBe('just now')
    expect(duration(en, 90)).toBe('2 min')
    expect(waiting(en, null)).toBe(UNAVAILABLE)
    expect(waiting(en, 'not a date')).toBe(UNAVAILABLE)
  })

  it('measures waiting from the boundary timestamp', () => {
    const now = Date.parse('2026-09-04T12:00:00Z')
    expect(waiting(en, '2026-09-04T09:46:00Z', now)).toBe('2 h')
    // A boundary in the future is a clock skew, not a negative wait.
    expect(waiting(en, '2026-09-04T13:00:00Z', now)).toBe('just now')
  })

  it('renders a missing estimate as unavailable, and a range in its own unit', () => {
    expect(range(en, null)).toBe(UNAVAILABLE)
    const turns: Range = { low: 70, high: 110, unit: 'turns', source: 'rule_band', confidence: 'low' }
    expect(range(en, turns)).toBe('70 – 110')
    const secs: Range = { low: 14400, high: 25200, unit: 'secs', source: 'rule_band', confidence: 'low' }
    expect(range(en, secs)).toBe('4 h – 7 h')
  })

  it('refuses a half-known ratio rather than dividing by an unknown cap', () => {
    expect(ratio(en, 12, 40)).toBe('12 / 40')
    expect(ratio(en, 12, null)).toBe(UNAVAILABLE)
    expect(ratio(en, null, 40)).toBe(UNAVAILABLE)
    expect(ratio(en, 0, 40)).toBe('0 / 40')
  })

  it('scales bytes and refuses an unknown size', () => {
    expect(bytes(en, 0)).toBe('0 B')
    expect(bytes(en, 2048)).toBe('2 KB')
    expect(bytes(en, 1_572_864)).toBe('1.5 MB')
    expect(bytes(en, null)).toBe(UNAVAILABLE)
  })
})

describe('backend-supplied params', () => {
  it('flattens the arrays and nulls a card headline can carry', () => {
    const params = refParams(en, {
      key: 'action.recovery.headline',
      params: { stage: 'code-generation', codes: ['delivery_gap', 'presence_missing'], burst: true, count: null },
    })
    expect(params).toEqual({
      stage: 'code-generation',
      codes: 'delivery_gap, presence_missing',
      burst: 'true',
      // A hole in a sentence must never read as the word "null".
      count: UNAVAILABLE,
    })
  })

  it('joins with the locale\'s own separator', () => {
    const params = refParams(zh, { key: 'k', params: { codes: ['a', 'b'] } })
    expect(params['codes']).toBe('a、b')
  })
})

describe('plural', () => {
  it('picks the _one form only for exactly one', () => {
    expect(plural(en, 'shell.strip.circuits', 1)).toBe('1 circuit open')
    expect(plural(en, 'shell.strip.circuits', 0)).toBe('0 circuits open')
    expect(plural(en, 'shell.strip.circuits', 3)).toBe('3 circuits open')
    // zh-CN has one form; both keys exist and carry the same placeholder.
    expect(plural(zh, 'shell.strip.circuits', 1)).toBe('1 个断路器已打开')
  })
})
