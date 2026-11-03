/**
 * Turn AI-DLC's learnings ritual off, or back on, for one intent (`CeremonySwitch`).
 *
 * The ritual (stage-protocol-learnings.md §13) asks before every stage's approval which of the agent's diary notes to
 * keep and whether the person has anything to add: two questions per stage, every stage. The setting is
 * `- **Learnings**: on (from scope feature)` in the intent's state file. A person turns it off by typing
 * `/aidlc --learnings off` with nothing beside it. AI-DLC counts the ritual as its own, not a decision of the
 * person's, so the prompt hook does not apply it: it records the turn as a command, answering no open question, and
 * forwards it to the conductor's `next`, whose setter writes the line (`off (set by a command)`). Typed beside other
 * words it is a flag of new work instead, so it is never folded into a learnings question's reply; a learnings
 * question already open stays open, and AI-DLC still records the answer to it.
 */

import type { ActionCard, IntentSummary, Question } from '../lib/types'
import { LEARNINGS_ADD_PROMPTS, LEARNINGS_KEEP_NONE_LABEL } from '../lib/enums.generated'
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
 * Is this one of the ritual's own questions? The keep question opens with the engine's "Keep none of these", the add
 * question is asked in one of its two spellings, and a keep question asked by id carries the diary's candidates.
 */
export function isLearningsQuestion(question: Pick<Question, 'prompt' | 'options' | 'learning_candidates'>): boolean {
  return (question.learning_candidates?.length ?? 0) > 0
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
