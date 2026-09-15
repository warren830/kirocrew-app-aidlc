/**
 * The question group: the engine's questions, exactly as it asked them.
 *
 * The whole template is a transcription. Prompts, option labels, option order and the `Other` escape
 * hatch come from `evidence.questions`, which the reader built from the persisted `*-questions.md` and
 * the live host payload (FR-Q-001). Nothing here paraphrases, re-orders or invents an option, and Studio
 * never writes to that file or its digest (FR-Q-005) — the footer says so, because a UI that looks like a
 * form owes the user a statement of what it is not doing.
 *
 * Four behaviours are load-bearing:
 *
 *  - **Forms need an authoritative source.** A supported, stable persisted question group can supply
 *    its own controls without a native widget. Ambiguous files and native blocking waits stay
 *    `mode: 'degraded'`; their inputs are disabled and the canonical conversation remains available.
 *  - **One atomic send** (FR-Q-002/004). Every *pending* question is answered in one action; an answered
 *    one is evidence and gets no control that could overwrite what the engine already consumed.
 *  - **The letters are the file's.** A draft answer stores `option_letters` from the file plus the free
 *    text, which is exactly what `wireTextFor` turns into wire text from the option *labels*. Nothing
 *    here sends a letter the file did not offer.
 *  - **A prepared draft is labelled and reversible** (FR-ADV-011). When a repository owner granted
 *    drafting ahead, the server's draft arrives with `request.auto` and its answers are copied into the
 *    inputs once — never over an answer that is already there, never in degraded mode, never sent — under
 *    a notice saying whose words they are, with one control that takes them back out again.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { MarkdownRenderer } from '@kirocrew/app-sdk/ui'

import { useI18n } from '../i18n'
import { pendingQuestions } from '../actions/useSubmit'
import type { DraftResult, Question } from '../lib/types'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { AdvisorBlock, useAdvisor, usableResult } from './AdvisorBlock'
import {
  Block,
  Consequence,
  DecisionBrief,
  FeedbackBox,
  SummaryChoice,
  answerComplete,
  templateLabel,
  type AnswerDraft,
  type TemplateProps,
} from './DecisionControls'

type Suggestion = DraftResult['suggested_answers'][number]

/**
 * The one answer a suggestion may become, or `null` when it may not become one.
 *
 * A suggestion with no option letters is free text, so it lands on `Other` when the file offers one and is
 * dropped when it does not: writing prose into a letter the engine would not recognise is worse than not
 * applying the draft at all. Single-select keeps the first letter only, because a model that named two
 * would otherwise build an answer the engine cannot read back.
 */
function answerFor(question: Question, suggestion: Suggestion): AnswerDraft | null {
  const letters = suggestion.option_letters.filter((letter) =>
    question.options.some((option) => option.letter === letter),
  )
  const other = question.options.find((option) => option.is_other)
  if (letters.length > 0) {
    const picked = question.multi_select ? letters : letters.slice(0, 1)
    return {
      option_letters: picked,
      free_text: other && picked.includes(other.letter) ? suggestion.answer : null,
    }
  }
  if (other && suggestion.answer.trim()) {
    return { option_letters: [other.letter], free_text: suggestion.answer }
  }
  return null
}

/**
 * Is what is in the box still exactly what the draft put there?
 *
 * `Clear these` may only take back the pre-fill's own words. A value the user has since changed no longer
 * matches, and deleting it because a draft once touched that index would be Studio discarding an answer a
 * human wrote.
 */
function sameAnswer(stored: AnswerDraft | undefined, filled: AnswerDraft): boolean {
  if (!stored || stored.free_text !== filled.free_text) return false
  if (stored.option_letters.length !== filled.option_letters.length) return false
  return stored.option_letters.every((letter, i) => letter === filled.option_letters[i])
}

export function QuestionsTemplate({ card, detail, draft, setDraft, refreshing, api, go }: TemplateProps) {
  const i18n = useI18n()
  const { t } = i18n
  const advisor = useAdvisor(card, api, detail?.drafts)
  const view = card.evidence.questions
  const questions = view?.questions ?? []
  const pending = pendingQuestions(view)
  const unsupportedPending = view?.unsupported_pending_count ?? 0
  const hasPending = pending.length > 0 || unsupportedPending > 0
  const degraded = view?.mode === 'degraded' || unsupportedPending > 0
  const answerable = ['Draft', 'Queued', 'NotDelivered'].includes(card.status)
  const editable = answerable && !degraded && !refreshing
  // Nothing is preselected: `summaryChoice` has a stored default because the payload builder needs one,
  // so the control shows a selection only after the user has made it.
  const [choiceTouched, setChoiceTouched] = useState(false)
  const checkpoint =
    view?.pending_checkpoint === 'summary_confirmation'
      ? view.summary_confirmation
      : view?.pending_checkpoint === 'plan_approval'
        ? view.plan_approval
        : null
  const offersChanges = card.decisions.some(
    (spec) => spec.decision === 'request_plan_changes' || spec.decision === 'confirm_summary',
  )
  const offersSummaryChoice = card.decisions.some((spec) => spec.decision === 'confirm_summary')

  const setAnswer = (index: number, letters: string[], freeText: string | null) =>
    setDraft({
      answers: { ...draft.answers, [String(index)]: { option_letters: letters, free_text: freeText } },
    })

  const toggle = (question: Question, letter: string) => {
    const stored = draft.answers[String(question.index)]
    const letters = stored?.option_letters ?? []
    if (!question.multi_select) {
      setAnswer(question.index, [letter], stored?.free_text ?? null)
      return
    }
    const next = letters.includes(letter) ? letters.filter((l) => l !== letter) : [...letters, letter]
    setAnswer(question.index, next, stored?.free_text ?? null)
  }

  /**
   * Merge the Advisor's picks into the inputs.
   *
   * `keepDrafted` is the whole difference between the two callers. The button is a human saying "use the
   * draft", so it may replace what is in the box; the automatic fill never may, because nobody asked for it
   * and writing over an answer someone typed while they were reading would be Studio editing their
   * decision. An answer the engine has already consumed (`question.answered`) is evidence, and neither
   * caller touches it.
   *
   * Memoised because the fill effect below depends on it; the deps are the two things it actually reads.
   */
  const mergeSuggested = useCallback(
    (suggested: DraftResult['suggested_answers'], keepDrafted: boolean): Record<string, AnswerDraft> => {
      const answers = { ...draft.answers }
      for (const suggestion of suggested) {
        const question = questions.find((q) => q.index === suggestion.question_index)
        if (!question || question.answered) continue
        const key = String(question.index)
        if (keepDrafted && answers[key]) continue
        const answer = answerFor(question, suggestion)
        if (answer) answers[key] = answer
      }
      return answers
    },
    [draft.answers, questions],
  )

  const applyAnswers = (suggested: DraftResult['suggested_answers']) =>
    setDraft({ answers: mergeSuggested(suggested, false) })

  // Only a draft the SERVER prepared may fill the form (FR-ADV-011). A draft the user clicked for keeps its
  // explicit `Apply the drafted selections` button: they are already sitting here, and filling the boxes
  // under a click that asked for a *draft* would blur the two paths this feature exists to keep apart.
  //
  // `action_id` is checked because `DetailShell` mounts the template without a `key`: walking to another
  // card keeps this component alive, and the block's draft resource can still be holding the draft of the
  // card just left — a card whose repository may not even be granted. Filling one card's answers from
  // another card's draft is the one mistake a pre-fill must never make, because nothing on screen would
  // say the answers belong to a different question.
  const prepared =
    advisor.draft?.request.auto === true && advisor.draft.action_id === card.action_id
      ? usableResult(advisor.draft)
      : null
  const preparedId = prepared ? (advisor.draft?.draft_id ?? null) : null

  /**
   * The suggestions of the draft whose words are in the boxes — not of whichever draft the block shows now.
   *
   * The two drift apart the moment a second draft exists: a newer prepared draft, or a `question_explain`
   * the user clicked, becomes `advisor.draft`, and a notice keyed to that one would vanish from a pre-fill
   * still sitting in the form while its `Clear these answers` cleared nothing. `advisorApplied` names the
   * draft that actually wrote, so the notice and its action follow it, through a remount included.
   */
  const appliedSuggestions = useMemo(() => {
    const id = draft.advisorApplied
    if (!id) return []
    const found =
      advisor.draft?.draft_id === id ? advisor.draft : (detail?.drafts ?? []).find((d) => d.draft_id === id)
    if (!found || found.action_id !== card.action_id || found.request.auto !== true) return []
    return usableResult(found)?.suggested_answers ?? []
  }, [draft.advisorApplied, advisor.draft, detail?.drafts, card.action_id])

  /**
   * Is the Advisor's text actually in a box right now?
   *
   * `advisorApplied` deliberately outlives `Clear these` — it is the only thing stopping the effect below
   * from refilling what the user just took out — so it cannot be what the notice is keyed to. A notice
   * standing over boxes the user has emptied, with an action that does nothing, is exactly the notice that
   * teaches people to stop reading notices.
   */
  const prefilled = useMemo(
    () =>
      appliedSuggestions.some((suggestion) => {
        const question = questions.find((q) => q.index === suggestion.question_index)
        if (!question) return false
        const filled = answerFor(question, suggestion)
        return !!filled && sameAnswer(draft.answers[String(question.index)], filled)
      }),
    [appliedSuggestions, draft.answers, questions],
  )

  useEffect(() => {
    // Degraded mode fills nothing (FR-Q-007): the inputs are disabled because Studio could not read the
    // live payload, so an answer put in one would answer a question nobody can confirm is still asked.
    if (!prepared || !preparedId || !editable || draft.advisorApplied === preparedId) return
    const answers = mergeSuggested(prepared.suggested_answers, true)
    // Nothing landed — every suggestion was already answered, or none of them survived the letter check.
    // Record nothing, so the notice never claims a pre-fill the user cannot find in the form.
    if (Object.keys(answers).length === Object.keys(draft.answers).length) return
    // One write: the answers and the note of which draft produced them go in the same patch, so no render
    // between the two can fill the form a second time.
    setDraft({ answers, advisorApplied: preparedId })
  }, [prepared, preparedId, editable, draft.advisorApplied, draft.answers, mergeSuggested, setDraft])

  /**
   * Take the pre-fill back out, and leave `advisorApplied` pointing at the same draft.
   *
   * Forgetting which draft was applied is the one thing this must not do: the effect above re-runs on this
   * very state change and would fill the form straight back in, leaving the user with an answer they cannot
   * get rid of. Only the indices that still hold the draft's own words go.
   */
  const clearPrefill = () => {
    const answers = { ...draft.answers }
    for (const suggestion of appliedSuggestions) {
      const question = questions.find((q) => q.index === suggestion.question_index)
      if (!question) continue
      const filled = answerFor(question, suggestion)
      const key = String(question.index)
      if (filled && sameAnswer(answers[key], filled)) delete answers[key]
    }
    setDraft({ answers })
  }

  return (
    <section className="studio-template" data-type={card.type} aria-label={templateLabel(i18n, card)}>
      <DecisionBrief card={card} />
      {view?.origin?.kind === 'audit' ? (
        <Consequence icon="doc">{t('template.questions.auditSource')}</Consequence>
      ) : view && !degraded ? (
        <Consequence icon="doc">{t('template.questions.fileSource')}</Consequence>
      ) : null}

      {degraded ? (
        <Block title={t('template.questions.degradedTitle')} icon="warn">
          <Consequence icon="warn" tone="warn" label={t('template.questions.degradedLabel')}>
            {t(unsupportedPending > 0 ? 'template.questions.unsupportedBody' : 'template.questions.degradedBody')}
          </Consequence>
          <button type="button" className="studio-btn" onClick={() => go({ tab: 'conversation' })}>
            <Icon name="activity" size={15} />
            {t('template.questions.openConversation')}
          </button>
        </Block>
      ) : null}

      <Block title={t('template.questions.group')} icon="question">
        <div className="studio-qmetas">
          <Chip tone={hasPending ? 'accent' : 'ok'} icon={hasPending ? 'question' : 'check'}>
            {unsupportedPending > 0
              ? t('template.questions.unsupportedPending', { n: unsupportedPending })
              : t('template.questions.pending', { n: pending.length, total: questions.length })}
          </Chip>
          {view ? <Chip mono>{view.stage}</Chip> : null}
          {view?.unit ? <Chip mono>{view.unit}</Chip> : null}
        </div>

        {prefilled && editable ? (
          // `role="status"`, because these answers arrived without anybody asking: the user has to be told
          // whose words are in the boxes before they read them as their own.
          <div className="studio-advisor-apply" role="status">
            <Icon name="advisor" size={13} />
            <span className="studio-advisor-note">{t('advisor.prefilled')}</span>
            <button
              type="button"
              className="studio-btn studio-btn-sm studio-btn-ghost"
              onClick={clearPrefill}
            >
              {t('advisor.prefilledClear')}
            </button>
          </div>
        ) : null}

        {questions.length === 0 && unsupportedPending === 0 ? (
          <p className="studio-muted">{t('template.questions.none')}</p>
        ) : null}
        {questions.map((question) => {
            const stored = draft.answers[String(question.index)]
            const letters = stored?.option_letters ?? []
            const freeText = stored?.free_text ?? ''
            const other = question.options.find((option) => option.is_other)
            const textInput = view?.origin?.kind === 'audit' && question.options.length === 1 && !!other
            const complete = answerComplete(question, letters, freeText, textInput)
            const groupName = `${card.action_id}/${question.index}`
            return (
              <div className="studio-q" key={question.index} data-answered={String(question.answered)}>
                {/* The prompt is the engine's sentence, numbered as the file numbers it. */}
                <p className="studio-q-prompt">
                  {question.index}. {question.prompt}
                </p>
                {question.context?.trim() ? (
                  // Use the same sanitising renderer as artifacts. Context is evidence, never a draft answer.
                  <div className="msg-content studio-md">
                    <MarkdownRenderer content={question.context} />
                  </div>
                ) : null}
                <p className="studio-q-sub">
                  <Chip>
                    {textInput ? t('template.questions.freeText')
                      : question.multi_select ? t('template.questions.selectAny') : t('template.questions.selectOne')}
                  </Chip>
                  <Chip tone="accent">{t('template.questions.required')}</Chip>
                  {question.answered ? (
                    <Chip tone="ok" icon="check">
                      {t('template.questions.answered')}
                    </Chip>
                  ) : complete && editable ? (
                    <Chip tone="aim" icon="check">
                      {t('template.questions.drafted')}
                    </Chip>
                  ) : null}
                  {!question.answered && editable ? (
                    <span className="studio-q-advisor">
                      <button
                        type="button"
                        className="studio-btn studio-btn-sm studio-btn-ghost"
                        disabled={advisor.pending !== null}
                        onClick={() => advisor.request('question_explain', question.index)}
                      >
                        <Icon name="advisor" size={13} />
                        {t('advisor.action.question_explain')}
                      </button>
                      <button
                        type="button"
                        className="studio-btn studio-btn-sm studio-btn-ghost"
                        disabled={advisor.pending !== null}
                        onClick={() => advisor.request('question_draft', question.index)}
                      >
                        <Icon name="advisor" size={13} />
                        {t('advisor.action.question_draft_one')}
                      </button>
                    </span>
                  ) : null}
                </p>

                {question.answered ? (
                  <p className="studio-q-recorded studio-wrap-any">
                    <span className="studio-q-recorded-label">{t('template.questions.recorded')}</span>
                    <span className="studio-mono">{question.answer ?? t('common.unavailable')}</span>
                  </p>
                ) : textInput ? (
                  <textarea
                    className="studio-free"
                    value={freeText}
                    rows={4}
                    placeholder={t('template.questions.textPlaceholder')}
                    aria-label={question.prompt}
                    disabled={!editable}
                    onChange={(event) => setAnswer(question.index, [other.letter], event.target.value)}
                  />
                ) : (
                  <>
                    {question.options.map((option) => {
                      const checked = letters.includes(option.letter)
                      return (
                        <label
                          key={option.letter}
                          className="studio-opt"
                          data-selected={String(checked)}
                          data-disabled={String(!editable)}
                        >
                          <input
                            type={question.multi_select ? 'checkbox' : 'radio'}
                            name={groupName}
                            value={option.letter}
                            checked={checked}
                            disabled={!editable}
                            onChange={() => toggle(question, option.letter)}
                          />
                          <span className="studio-opt-body">
                            <span className="studio-opt-letter">{option.letter}</span>
                            {/* The option label is the engine's own text, verbatim (FR-Q-001). */}
                            <span className="studio-opt-label">{option.text}</span>
                            {option.is_other ? (
                              <span className="studio-opt-desc">{t('template.questions.otherDesc')}</span>
                            ) : null}
                          </span>
                        </label>
                      )
                    })}
                    {other && letters.includes(other.letter) ? (
                      <textarea
                        className="studio-free"
                        value={freeText}
                        rows={3}
                        placeholder={t('template.questions.otherPlaceholder')}
                        aria-label={t('template.questions.otherLabel', { index: question.index })}
                        disabled={!editable}
                        onChange={(event) => setAnswer(question.index, letters, event.target.value)}
                      />
                    ) : null}
                  </>
                )}
              </div>
            )
          })}

        {view ? (
          <Consequence icon="lock">{t('template.questions.neverEdits', { path: view.relpath })}</Consequence>
        ) : null}
      </Block>

      {checkpoint?.present ? (
        <Block title={t(`template.questions.checkpoint.${checkpoint.kind}`)} icon="gate">
          <p className="studio-q-prompt">{t(`template.questions.checkpoint.${checkpoint.kind}.body`)}</p>
          {checkpoint.options.length > 0 ? (
            // The checkpoint's own options, as written in the file. The bytes Studio would send are shown
            // by the confirmation panel, which is the only place wire text belongs.
            <ul className="studio-optionlist studio-mono">
              {checkpoint.options.map((option, i) => (
                <li key={i} className="studio-wrap-any">
                  {option}
                </li>
              ))}
            </ul>
          ) : null}
          {checkpoint.answered ? (
            <Consequence icon="check">
              {t('template.questions.checkpointAnswered', { answer: checkpoint.answer ?? t('common.unavailable') })}
            </Consequence>
          ) : offersSummaryChoice && editable ? (
            <SummaryChoice
              draft={draft}
              setDraft={(patch) => {
                setChoiceTouched(true)
                setDraft(patch)
              }}
              name={`${card.action_id}/summary`}
              groupLabel={t('template.questions.summaryChoiceLabel')}
              looksCorrectLabel={t('template.questions.looksCorrect')}
              looksCorrectHint={t('template.questions.looksCorrectHint')}
              changesLabel={t('template.questions.summaryChanges')}
              changesHint={t('template.questions.summaryChangesHint')}
              chosen={choiceTouched}
            />
          ) : null}
        </Block>
      ) : null}

      {pending.length > 0 && answerable ? <AdvisorBlock
        card={card}
        advisor={advisor}
        {...(editable ? { onApplyAnswers: applyAnswers } : {})}
        {...(offersChanges && editable ? { onUseFeedback: (text: string) => setDraft({ feedback: text }) } : {})}
      /> : null}

      {offersChanges && editable ? (
        <Block title={t('template.questions.feedback')} icon="doc">
          <FeedbackBox
            draft={draft}
            setDraft={setDraft}
            label={t('template.questions.feedbackLabel')}
            placeholder={t('template.gate.feedbackPlaceholder')}
            routing={t('template.gate.feedbackRouting')}
            disabled={refreshing}
          />
        </Block>
      ) : null}
    </section>
  )
}

export default QuestionsTemplate
