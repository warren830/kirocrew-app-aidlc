# Question context and body-level multi-select verification

The Agentbridge brownfield demonstration exposed a question-form defect in Studio.
This record covers the Studio repair; it does not claim that the Agentbridge feature
or its complete workflow has finished.

## Reproduction

The Scope Definition question file in intent `260915-cron-control` defined eight
numbered capabilities between Q1's heading and its options. The options referred to
those numbers, but the API and rendered form omitted the definitions. Q3 placed
`(Select all that should be recorded as out of scope.)` in the explanatory paragraph.
The form rendered radios because multi-select detection inspected only the heading.

## Repair

- `Question.context` retains the pre-option Markdown without including options or
  saved answers. Boundary whitespace is trimmed; paragraph breaks and indentation remain.
- Both reader serialization and `QuestionsView` serialization carry the field.
  A real handler regression caught the second serializer's omission after the
  initial reader and UI tests passed.
- The existing Markdown renderer displays context before options, including for
  disabled and answered questions. Older stored cards without the field remain
  compatible; historical snapshots are not retroactively rewritten.
- Explicit multi-select instructions in explanatory paragraphs survive emphasis
  and soft line breaks. Options, answers, fenced and quoted examples, and
  background mentions cannot enable multi-select.
- Context never enters answer payloads or wire text.

## Verification

The full release-check run passed 2,833 backend tests and 437 frontend tests, plus
typechecking, build, generated-source checks, gateway-interpreter imports and
payload integrity. After the final Markdown-equivalence repair, all 299
reader/actions tests passed again.

The handler regression first failed with `KeyError: 'context'`. After fixing
`QuestionsView`, 204 projection/intent-handler/action-handler tests passed. A
final reader/actions rerun passed all 299 tests. These runs overlap and their
counts must not be added together.

Two local Studio updates completed at idle checkpoints with zero execution or
admin leases. All 46 files in the demo's intent and published code knowledge base
were byte-identical before and after each update. The host was not restarted.

The installed reader preserved the actual Q1 capability definitions and marked
the actual Scope Definition Q3 as multi-select. In the next real Practices
Discovery question card, `a_d431725e631cd1d2`, the API supplied all eight contexts
and the browser rendered all eight before their options. Rendered text lengths
were 391, 334, 521, 515, 582, 264, 835 and 599 characters. The page console had no
errors.

## Local evidence

Evidence directory:

```text
/Users/ychchen/warren_ws/aidlc-studio-backups/agentbridge-cron-20260915-170454
```

Relevant files:

- `studio-question-context-checks.log`
- `studio-question-context-final-targeted.log`
- `studio-question-context-projection-checks.log`
- `studio-question-context-final-actions.log`
- `installed-question-context-verification.json`
- `live-practices-questions-with-context.json`
- `question-context-ui-verification.json`
- `before-studio-update-workflow-hashes.json`
- `before-projection-update-workflow-hashes.json`
- `after-projection-update-health.json`
