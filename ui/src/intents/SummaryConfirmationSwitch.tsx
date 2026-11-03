/**
 * Turn AI-DLC's per-stage summary confirmation off, or back on, for one intent (`CeremonySwitch`).
 *
 * The setting is `- **Summary Confirmation**: on (from scope feature)` in the intent's state file. A person turns it
 * off by typing `/aidlc --summary-confirmation off` with nothing beside it: the prompt hook applies it as theirs
 * (`off (set by you)`) when the turn arrives and records the turn as a command, so it answers no open question. `on`
 * is forwarded to the conductor, whose setter writes it. `Looks correct` is matched exactly, so the switch is never
 * folded into an open confirmation's reply; that confirmation stays open, and AI-DLC still records the answer to it.
 */

import type { ActionCard, IntentSummary } from '../lib/types'
import {
  CeremonySwitch, cardCeremonySwitchBlockedReason, ceremonySwitchBlockedReason, ceremonySwitchTarget,
  ceremonySwitchText, type CeremonySwitchProps, type CeremonyValue,
} from './CeremonySwitch'

export type SummarySetting = CeremonyValue

/** The text the switch sends, which the confirmation must display. */
export function summarySwitchText(value: SummarySetting): string {
  return ceremonySwitchText('summary_confirmation', value)
}

/** What the control offers: back on when AI-DLC recorded it off, otherwise off. */
export const summarySwitchTarget = ceremonySwitchTarget

/** Why the intent's conversation would refuse the switch now, or `null`. The checks the send itself makes. */
export function summarySwitchBlockedReason(intent: IntentSummary, t: (key: string) => string): string | null {
  return ceremonySwitchBlockedReason('summary_confirmation', intent, t)
}

/** The same question asked of a card: its live evidence names the bound conversation and whether it is running. */
export function cardSummarySwitchBlockedReason(card: ActionCard, t: (key: string) => string): string | null {
  return cardCeremonySwitchBlockedReason(card, t)
}

export type SummaryConfirmationSwitchProps = Omit<CeremonySwitchProps, 'ceremony'>

export function SummaryConfirmationSwitch(props: SummaryConfirmationSwitchProps) {
  return <CeremonySwitch ceremony="summary_confirmation" {...props} />
}
