/**
 * Catalogue parity.
 *
 * `scripts/build_i18n.py` enforces the same rules when it merges the per-area parts, but that script
 * runs on a developer's machine and against the backend's tables. This test runs in the same suite as
 * the code that calls `t()`, over the two files the bundle actually imports, so a hand-edited
 * `en-US.json` or a part that was added without regenerating fails here too.
 *
 * A missing key renders as the key itself (`i18n/index.tsx`), which is visible but not acceptable: in a
 * decision UI, `decision.approve.label` where "Approve" belongs is a button nobody should press.
 */

import { describe, expect, it } from 'vitest'

import enUS from './en-US.json'
import zhCN from './zh-CN.json'
import { ERROR_CODES } from '../lib/errorCodes.generated'
import {
  ACTION_STATUSES, ACTION_TYPES, ADVISOR_CARDLESS_KINDS, ADVISOR_KINDS, AVAILABILITIES, FINDING_SEVERITIES,
  INSTALL_STATUSES, INTENT_STATES, OWNERSHIP_KINDS, PHASES, SEVERITIES, SOURCES, STAGE_STATES,
} from '../lib/enums.generated'

const en = enUS as Record<string, string>
const zh = zhCN as Record<string, string>
const PLACEHOLDER = /\{([a-zA-Z0-9_]+)\}/g

const placeholders = (template: string): string[] => [...template.matchAll(PLACEHOLDER)].map((m) => m[1] ?? '').sort()

describe('the two catalogs', () => {
  it('have identical key sets', () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(zh).sort())
  })

  it('have no empty or whitespace-only value', () => {
    for (const [key, value] of Object.entries(en)) expect(value.trim(), `en-US ${key}`).not.toBe('')
    for (const [key, value] of Object.entries(zh)) expect(value.trim(), `zh-CN ${key}`).not.toBe('')
  })

  it('interpolate the same placeholders in both locales', () => {
    // A `{stage}` dropped in translation is a sentence that silently loses the fact it was about.
    for (const key of Object.keys(en)) {
      expect(placeholders(zh[key] ?? ''), key).toEqual(placeholders(en[key] ?? ''))
    }
  })

  it('are sorted, so a merge conflict is a line conflict and not a reordering', () => {
    expect(Object.keys(en)).toEqual([...Object.keys(en)].sort())
  })
})

describe('coverage of the backend tables', () => {
  it('has a message for every error code', () => {
    const missing = Object.keys(ERROR_CODES).filter((code) => !(`errors.${code}` in en))
    expect(missing).toEqual([])
  })

  it('has a label for every canonical enum value', () => {
    const groups: [string, readonly string[]][] = [
      ['actionStatus', ACTION_STATUSES],
      ['intentState', INTENT_STATES],
      ['actionType', ACTION_TYPES],
      ['severity', SEVERITIES],
      ['findingSeverity', FINDING_SEVERITIES],
      ['source', SOURCES],
      ['stageState', STAGE_STATES],
      ['installStatus', INSTALL_STATUSES],
      ['availability', AVAILABILITIES],
      ['ownership', OWNERSHIP_KINDS],
      ['phase', PHASES],
    ]
    const missing = groups.flatMap(([group, values]) =>
      values.filter((value) => !(`enum.${group}.${value}` in en)).map((value) => `enum.${group}.${value}`),
    )
    expect(missing).toEqual([])
  })

  it('has a label for every advisor kind the backend can draft', () => {
    // A kind the broker can settle is named in two places the human reads without a card: the block's
    // running line (`advisor.kind.<k>`, "Reading the evidence for …") and the Activity drawer's heading
    // (`advisor.drawer.kind.<k>`). The per-card button (`advisor.action.<k>`) exists only for kinds that
    // have a card — a card-less kind is asked for in the wizard, whose button is `wizard.advisor.ask`, so
    // demanding `advisor.action.plan_draft` here would force a label nothing renders (design §2.6).
    const cardless = new Set<string>(ADVISOR_CARDLESS_KINDS)
    const missing = ADVISOR_KINDS.flatMap((kind) => {
      const keys = [`advisor.kind.${kind}`, `advisor.drawer.kind.${kind}`]
      if (!cardless.has(kind)) keys.push(`advisor.action.${kind}`)
      return keys.filter((key) => !(key in en))
    })
    expect(missing).toEqual([])
    // And the card-less set is a subset of the kinds, so a typo there cannot silently exempt anything.
    expect(ADVISOR_KINDS.filter((kind) => cardless.has(kind))).toEqual([...ADVISOR_CARDLESS_KINDS])
  })

  it('keeps every nav destination the shell renders', () => {
    for (const key of ['nav.actions', 'nav.repos', 'nav.intents', 'nav.map', 'nav.activity', 'nav.settings', 'nav.newIntent']) {
      expect(en[key], key).toBeTruthy()
    }
  })
})

describe('AI-DLC’s own words', () => {
  it('are not translated where they name a protocol object', () => {
    // The product name and the wire-text vocabulary are the same in both catalogs on purpose: they name
    // things the engine and the audit trail spell one way (§3.3).
    expect(zh['shell.brand']).toBe(en['shell.brand'])
  })
})
