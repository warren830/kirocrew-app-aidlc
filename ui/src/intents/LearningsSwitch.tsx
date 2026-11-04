/**
 * Turn AI-DLC's learnings step off, or back on, for one intent (`CeremonySwitch`).
 *
 * The step (stage-protocol-learnings.md §13) asks, from AI-DLC 2.11.2, only when the person corrected the agent in a
 * stage: before that stage's approval, "You asked me to …. Keep that for next time?" once per correction. Records
 * from before 2.11.2 hold its old pair, which notes to keep and whether there is anything to add, at every stage. The
 * setting is `- **Learnings**: on (from scope feature)` in the intent's state file. A person turns it off by typing
 * `/aidlc --learnings off` with nothing beside it. The prompt hook applies nothing: it records the turn as a command,
 * answering no open question, and hands the conductor `next --learnings off`, whose `config set` writes the line
 * (`off (set by you)`). Typed beside other words it is a flag of new work instead, so it is never folded into a
 * learnings question's reply; a learnings question already open stays open, and AI-DLC still records the answer.
 */

import type { ActionCard, IntentSummary, Question } from '../lib/types'
import {
  LEARNINGS_ADD_PROMPTS, LEARNINGS_CORRECTION_ASK, LEARNINGS_CORRECTION_CHOICES, LEARNINGS_KEEP_NONE_LABEL,
} from '../lib/enums.generated'
import {
  CeremonySwitch, cardCeremonySwitchBlockedReason, ceremonySwitchBlockedReason, ceremonySwitchText,
  type CeremonySwitchProps,
} from './CeremonySwitch'

const ADD_PROMPTS = new Set(LEARNINGS_ADD_PROMPTS.map((prompt) => prompt.toLowerCase()))

/** The text the switch sends, which the confirmation must display. */
export function learningsSwitchText(value: 'on' | 'off'): string {
  return ceremonySwitchText('learnings', value)
}

/**
 * AI-DLC 2.11.2's learnings question about one correction the person made: "You asked me to …. Keep that for next
 * time?", single choice, with exactly Yes, for this project / Yes, for the team / No.
 */
export function isLearningsCorrectionQuestion(question: Pick<Question, 'prompt' | 'options'>): boolean {
  return question.prompt.trimEnd().endsWith(` ${LEARNINGS_CORRECTION_ASK}`)
    && question.options.length === LEARNINGS_CORRECTION_CHOICES.length
    && question.options.every((option, position) => option.text === LEARNINGS_CORRECTION_CHOICES[position])
}

/**
 * Is this one of the step's own questions? 2.11.2 asks one per correction (`isLearningsCorrectionQuestion`). Records
 * from before it hold the old two: the keep question opens with the engine's "Keep none of these", the add question
 * is asked in one of its two spellings, and a keep question asked by id carries the diary's candidates.
 */
export function isLearningsQuestion(question: Pick<Question, 'prompt' | 'options' | 'learning_candidates'>): boolean {
  return isLearningsCorrectionQuestion(question)
    || (question.learning_candidates?.length ?? 0) > 0
    || ADD_PROMPTS.has(question.prompt.trim().toLowerCase())
    || question.options.some((option) => option.text === LEARNINGS_KEEP_NONE_LABEL)
}

/** Why the intent's conversation would refuse the switch now, or `null`. The checks the send itself makes. */
export function learningsSwitchBlockedReason(intent: IntentSummary, t: (key: string) => string): string | null {
  return ceremonySwitchBlockedReason('learnings', intent, t)
}

/** The same question asked of a card: its live evidence names the bound conversation and whether it is running. */
export function cardLearningsSwitchBlockedReason(card: ActionCard, t: (key: string) => string): string | null {
  return cardCeremonySwitchBlockedReason(card, t)
}

/** A learnings question card's open ritual: shown with the switch, which says the questions asked stay open. */
export function openLearningsQuestions(card: Pick<ActionCard, 'evidence'>): boolean {
  return (card.evidence.questions?.questions ?? []).some((question) => !question.answered && isLearningsQuestion(question))
}

export type LearningsSwitchProps = Omit<CeremonySwitchProps, 'ceremony'>

export function LearningsSwitch(props: LearningsSwitchProps) {
  return <CeremonySwitch ceremony="learnings" {...props} />
}
