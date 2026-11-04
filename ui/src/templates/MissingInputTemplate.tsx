/**
 * Something Studio needs from the user before anything can run.
 *
 * Not mocked in the visual spec (discrepancy #15), so it is derived from the budget layout: the exact
 * missing input, where Studio looked for it, what it blocks, and one control.
 *
 * Two cases arrive here and they are not the same shape (`projection.py:_missing_input_seeds`):
 *
 *  - **`missing_scope`** — the state file has no `Scope`. The user types one and Studio sends it to the
 *    canonical session; Studio does not write the state file (P-01), which is why the routing note is
 *    part of the template rather than a tooltip.
 *  - **`dangling_cursor` / `no_cursor`** — AI-DLC's active intent is missing or points at a directory that
 *    is gone. The fix is `pick_intent`, an admin-lane engine verb that needs an intent *directory*. The
 *    card carries only its own, so this template says where the real list is instead of building a picker
 *    out of one candidate: the cursor decides which intent every later dispatch moves.
 *  - **`conversation_reply`** — AI-DLC asked something only in chat (`projection._conversation_reply_seeds`).
 *    The host's reply suggestions are a choice in "What to send" itself: choosing one puts exactly its
 *    text in the box, and the words sent are always the box's own. Exact matters — at a protected
 *    checkpoint the engine compares the reply with the text it offered, so a suggestion with words added
 *    is refused (an E2E appended to "Approve Plan" five times and was refused five times).
 */

import { useRef, useState } from 'react'
import { MarkdownRenderer } from '@kirocrew/app-sdk/ui'
import { useI18n } from '../i18n'
import { at } from '../lib/format'
import { withInlineCode } from '../lib/inlineCode'
import { Chip } from '../shell/Chip'
import { AdvisorBlock, useAdvisor } from './AdvisorBlock'
import {
  Block,
  Brief,
  ConsistencyFindings,
  Consequence,
  DecisionBrief,
  Ev,
  EvidenceGrid,
  InputBox,
  OfferedDecisions,
  inputKind,
  templateLabel,
  type TemplateProps,
} from './DecisionControls'

/** The reasons the projection mints for this card; anything else falls back to a generic sentence. */
const KNOWN_REASONS = ['missing_scope', 'dangling_cursor', 'no_cursor', 'conversation_reply'] as const

/**
 * Whether a reply suggestion means "something else", which is a prompt to write rather than words to send.
 *
 * Read from the suggestion's own leading words, because the host hands Studio plain strings with no
 * flag: `Other`, `Other (please specify)`, `Something else`, and the Chinese 其他 / 其它.
 */
export function isOtherSuggestion(option: string): boolean {
  const text = option.trim()
  return /^(?:other|something else)(?![a-z])/i.test(text) || /^其[他它]/.test(text)
}

/** A CommonMark fence line: up to three spaces, three or more backticks or tildes, then the info string. */
const FENCE_RE = /^( {0,3})(`{3,}|~{3,})(.*)$/

/**
 * The quoted reply with each fenced block marked as plain text, so its long lines wrap inside the quote.
 *
 * The host's code block scrolls a highlighted block sideways with a scrollbar that shows only on hover, so a
 * command AI-DLC asks to run read as cut off at the box's edge (v2-full, 2026-11-02: `uv run … pytest
 * tests/test_tasklist…`); it wraps only the languages it treats as plain text. The person is asked to approve
 * that command, so all of it has to be readable; the highlighting is what is given up. Mermaid keeps its
 * diagram, and the block's lines are untouched.
 */
export function wrapQuotedFences(text: string): string {
  let open: string | null = null
  return text
    .split('\n')
    .map((line) => {
      const fence = FENCE_RE.exec(line)
      if (open !== null) {
        if (fence && fence[2]![0] === open[0] && fence[2]!.length >= open.length && !fence[3]!.trim()) open = null
        return line
      }
      if (!fence || (fence[2]![0] === '`' && fence[3]!.includes('`'))) return line
      open = fence[2]!
      const info = fence[3]!.trim()
      return /^mermaid\b/i.test(info) ? line : `${fence[1]}${fence[2]}text`
    })
    .join('\n')
}

export function MissingInputTemplate({ card, detail, draft, setDraft, api, go }: TemplateProps) {
  const i18n = useI18n()
  const { t } = i18n
  const advisor = useAdvisor(card, api, detail?.drafts)
  const rawReason = card.headline.params['reason']
  const reason = KNOWN_REASONS.find((known) => known === rawReason) ?? null
  const wantsText = card.decisions.some((spec) => spec.decision === 'provide_input')
  const needsCursor = card.decisions.some((spec) => spec.decision === 'pick_intent')
  const kind = inputKind(card)
  const cursor = card.headline.params['cursor']
  const conversational = reason === 'conversation_reply'
  const reply = conversational ? card.evidence.conversation_reply : null
  const suggestions = Array.isArray(card.headline.params['options'])
    ? card.headline.params['options'].filter((option): option is string => typeof option === 'string')
    : []
  const offersChoice = conversational && suggestions.length > 0
  // What the radios show is read from the box, so they can never claim a send the box will not make: a
  // suggestion is selected only while the box holds exactly its text, and anything else the user wrote is
  // the "something else" suggestion when there is one. `otherChosen` covers the moment between choosing
  // it and typing, when the box is empty and the text alone cannot say so.
  const [otherChosen, setOtherChosen] = useState(false)
  // Arrowing through a radio group changes the selection as it moves; focus must not jump into the box
  // just because the cursor passed over "Other" on its way.
  const arrowed = useRef(false)
  const typed = draft.freeText.trim()
  const exact = suggestions.findIndex((option) => !isOtherSuggestion(option) && option.trim() === typed && typed !== '')
  const otherAt = suggestions.findIndex(isOtherSuggestion)
  const selected = exact >= 0 ? exact : otherAt >= 0 && (otherChosen || typed !== '') ? otherAt : -1
  // A suggestion with words added is the one shape a protected checkpoint refuses; say so as it happens.
  // The longest one it starts with, so "Yes, but…" is not reported as "Yes" when both are offered.
  const extended =
    exact >= 0
      ? null
      : suggestions.reduce<number | null>((best, option, position) => {
          if (isOtherSuggestion(option) || !typed.startsWith(option.trim())) return best
          return best === null || option.trim().length > (suggestions[best] ?? '').trim().length ? position : best
        }, null)
  const choose = (position: number) => {
    const viaArrow = arrowed.current
    arrowed.current = false
    const option = suggestions[position] ?? ''
    if (isOtherSuggestion(option)) {
      setOtherChosen(true)
      setDraft({ freeText: '', inputKind: 'free_text' })
      if (!viaArrow) document.getElementById('studio-input')?.focus()
      return
    }
    setOtherChosen(false)
    setDraft({ freeText: option, inputKind: 'free_text' })
  }
  // `mtime_ns` is nanoseconds since the epoch; `Date` wants milliseconds.
  const stateMtime = card.evidence.state?.mtime_ns
    ? new Date(card.evidence.state.mtime_ns / 1e6).toISOString()
    : null

  return (
    <section className="studio-template" data-type={card.type} aria-label={templateLabel(i18n, card)}>
      <DecisionBrief card={card} />

      {reply?.text ? (
        // The question itself replaces the explanation that it can only be read in the conversation.
        <Block title={t('template.missingInput.lastReply')} icon="chat">
          <p className="studio-help">
            {t(suggestions.length > 0 ? 'template.missingInput.lastReplyHelp' : 'template.missingInput.lastReplyHelpPlain', {
              at: at(i18n, reply.ts),
            })}
          </p>
          {/* The same sanitising renderer as a question's context: this is evidence, not a draft. */}
          <div className="msg-content studio-md studio-reply-quote">
            {/* `softBreaks`, as the conversation renders it: AI-DLC puts "Stage:", "Why now:" on lines of their own. */}
            <MarkdownRenderer content={wrapQuotedFences(reply.text)} softBreaks />
          </div>
          <button type="button" className="studio-btn" onClick={() => go({ tab: 'conversation' })}>
            {t('template.missingInput.openConversation')}
          </button>
        </Block>
      ) : (
        <Block title={t('template.missingInput.what')} icon="missingInput">
          <Brief>
            <p>{reason ? t(`template.missingInput.reason.${reason}`) : t('template.missingInput.reasonUnknown')}</p>
            {conversational ? (
              <button type="button" className="studio-btn" onClick={() => go({ tab: 'conversation' })}>
                {t('template.missingInput.openConversation')}
              </button>
            ) : (
              <Consequence icon="lock" label={t('template.missingInput.blockedLabel')}>
                {t('template.missingInput.blocked')}
              </Consequence>
            )}
          </Brief>
        </Block>
      )}

      <Block title={t('template.missingInput.source')} icon="doc">
        <EvidenceGrid>
          {card.evidence.state ? (
            <Ev
              src={t('template.recovery.src.state')}
              icon="doc"
              value={card.evidence.state.relpath}
              sub={t('template.missingInput.stateSub', {
                stage: card.evidence.state.current_stage ?? t('common.unavailable'),
                at: at(i18n, stateMtime),
              })}
            />
          ) : null}
          {conversational ? null : (
            <Ev
              src={t('template.missingInput.cursorSrc')}
              icon="intent"
              value={typeof cursor === 'string' && cursor ? cursor : t('common.none')}
              sub={t('template.missingInput.cursorSub', { space: card.space })}
              conflict={reason === 'dangling_cursor'}
            />
          )}
        </EvidenceGrid>
      </Block>

      {wantsText ? (
        <Block title={t(`template.missingInput.input.${kind}`)} icon="send">
          {offersChoice ? (
            <>
              <p className="studio-help">{t('template.missingInput.suggestHelp')}</p>
              <div
                role="radiogroup"
                aria-label={t('template.missingInput.suggestGroup')}
                onKeyDown={(event) => {
                  arrowed.current = event.key.startsWith('Arrow')
                }}
              >
                {suggestions.map((option, position) => {
                  const other = isOtherSuggestion(option)
                  const checked = position === selected
                  return (
                    <label key={position} className="studio-opt" data-selected={String(checked)}>
                      <input
                        type="radio"
                        name={`studio-reply-${card.action_id}`}
                        value={String(position)}
                        checked={checked}
                        onChange={() => choose(position)}
                      />
                      <span className="studio-opt-body">
                        {/* The suggestion is the host's text, verbatim: it is what the box will hold. */}
                        <span className="studio-opt-label">{withInlineCode(option)}</span>
                        <span className="studio-opt-desc">
                          {t(other ? 'template.missingInput.suggestOtherDesc' : 'template.missingInput.suggestVerbatim')}
                        </span>
                      </span>
                    </label>
                  )
                })}
              </div>
              <Consequence icon="warn" tone="warn" label={t('template.missingInput.exactLabel')}>
                {t('template.missingInput.exact')}
              </Consequence>
              {extended !== null ? (
                <p className="studio-help" role="status">
                  {t('template.missingInput.extended', { option: suggestions[extended] ?? '' })}
                </p>
              ) : null}
            </>
          ) : null}
          <InputBox
            card={card}
            draft={draft}
            setDraft={setDraft}
            label={t(`template.missingInput.input.${kind}`)}
            placeholder={t(`template.missingInput.placeholder.${kind}`)}
            routing={t('template.missingInput.routing')}
          />
        </Block>
      ) : null}

      {needsCursor ? (
        <Block title={t('template.missingInput.pickTitle')} icon="intent">
          <Brief>
            <p>{t('template.missingInput.pickBody')}</p>
            <span className="studio-row studio-qmetas">
              <Chip icon="lock">{t('template.missingInput.adminLane')}</Chip>
              <Chip mono>{card.space}</Chip>
            </span>
            <button
              type="button"
              className="studio-btn"
              onClick={() => go({ view: 'intents', repo: card.repo.repo_id, space: card.space })}
            >
              {t('template.missingInput.openIntents')}
            </button>
          </Brief>
        </Block>
      ) : null}

      <ConsistencyFindings card={card} title={t('template.missingInput.findings')} />
      <OfferedDecisions card={card} title={t('template.missingInput.choices')} />

      <AdvisorBlock card={card} advisor={advisor} />
    </section>
  )
}

export default MissingInputTemplate
