/**
 * `WIRE` must be byte-for-byte `docs/design/wire-text.json`.
 *
 * This is the highest-consequence assertion in the frontend. `WIRE.APPROVE` is not a label: it is the
 * literal text injected into the canonical session as a human turn, and the AI-DLC conductor matches
 * it against the option it offered. One changed byte — `Request changes: ` where the gate expects
 * `Request Changes: `, a stripped trailing space, a smart quote — and Studio sends a message the engine
 * does not recognise while telling the user their decision was delivered.
 *
 * `wire.generated.ts` is produced from the same JSON, so in a clean tree this test is a tautology. It
 * exists for the tree that is not clean: a hand edit to the generated file, or a JSON change with no
 * regeneration, fails here instead of in production.
 */

import { describe, expect, it } from 'vitest'

import { WIRE, WIRE_TEXT_SOURCE } from './wire.generated'

// Imported, not read through `node:fs`: the bundle has no `@types/node`, and a test that needs a
// Node built-in to state its most important invariant is a test that stops running the day the suite
// moves to a browser environment. Nothing outside a test imports this file, so the design document
// never reaches the shipped bundle.
import wireDoc from '../../../docs/design/wire-text.json'

const doc = wireDoc as { version: number; constants: Record<string, string>; golden: Record<string, string> }

describe('WIRE', () => {
  it('names the pinned source', () => {
    expect(WIRE_TEXT_SOURCE).toBe('docs/design/wire-text.json')
    expect(doc.version).toBe(2)
  })

  it('has exactly the pinned constants, with the WIRE_ prefix stripped', () => {
    const expected = Object.fromEntries(
      Object.entries(doc.constants).map(([key, value]) => [key.replace(/^WIRE_/, ''), value]),
    )
    expect({ ...WIRE }).toEqual(expected)
  })

  it('keeps the casing and whitespace the conductor matches on', () => {
    // Spelled out, because these four are the ones a well-meaning edit would "fix".
    expect(WIRE.APPROVE).toBe('Approve')
    // Capital C, and a trailing space before the feedback: the stage gate's own option label.
    expect(WIRE.REQUEST_CHANGES_PREFIX).toBe('Request Changes: ')
    // Lower-case c: the summary confirmation is a DIFFERENT checkpoint with a different label.
    expect(WIRE.SUMMARY_REQUEST_CHANGES_PREFIX).toBe('Request changes: ')
    expect(WIRE.SCOPE_PREFIX.endsWith(' ')).toBe(true)
  })

  it('reproduces the golden grouped-answer example', () => {
    const line = (index: number, answer: string) =>
      WIRE.ANSWER_LINE.replace('{index}', String(index)).replace('{answer}', answer)
    const grouped = [line(1, 'B'), line(2, ['A', 'C'].join(WIRE.MULTI_SELECT_JOINER)), line(3, 'Use the existing worker queue.')]
      .join(WIRE.ANSWER_JOINER) + WIRE.GROUPED_ANSWER_SUFFIX
    expect(grouped).toBe(doc.golden['grouped_answers_example'])
  })

  it('reproduces the golden request-changes example', () => {
    const feedback = 'The acceptance criteria for C3 are not covered.'
    expect(WIRE.REQUEST_CHANGES_PREFIX + feedback).toBe(doc.golden['request_changes_example'])
  })
})
