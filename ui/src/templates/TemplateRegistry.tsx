/**
 * Which template renders which card, and the one import that plugs them into the detail pane.
 *
 * `DetailShell` looks a template up by `card.type` — through `registerActionTemplate` or its own build-time
 * glob — so this module registers one component for every `ActionType` at module scope and the Action
 * Center only has to import it once. The mapping is total over the enum (`Record<ActionType, …>`, checked
 * by the compiler) because a type with no template renders "no template in this build": the user would see
 * a card in the queue, open it, and find nothing to decide with.
 *
 * Three types share a template on purpose:
 *   - `delivery_uncertain` uses the recovery template. It *is* the recovery conversation — same evidence
 *     grid, same "Studio never picks a convenient source of truth".
 *   - `circuit_breaker` uses the failure template: same fingerprint, same history, plus the open breaker.
 *   - `revision` uses the gate template with no decisions offered. It is the informational half of a gate
 *     (C18): the artifact was re-run and the reviewer ran again, and nothing is being asked yet.
 *
 * And one card is routed on more than its type: a gate or question whose delivery became unprovable
 * (`queue_type === 'delivery_uncertain'`) gets the recovery body, because the decision to make is now about
 * the delivery. The bar already offers only the uncertain vocabulary in that state; showing the artifact as
 * if the gate were still open is how a stage gets approved twice.
 */

import type { ReactElement } from 'react'

import { useI18n } from '../i18n'
import { at } from '../lib/format'
import type { ActionCard, ActionType } from '../lib/types'
import { registerActionTemplate, type TemplateProps } from '../actions/DetailShell'
import { Chip } from '../shell/Chip'
import { BudgetStopTemplate } from './BudgetStopTemplate'
import {
  Block,
  Consequence,
  DecisionBrief,
  Ev,
  EvidenceGrid,
  OfferedDecisions,
  templateLabel,
} from './DecisionControls'
import { FailureTemplate } from './FailureTemplate'
import { GateTemplate } from './GateTemplate'
import { InstallConflictTemplate } from './InstallConflictTemplate'
import { MissingInputTemplate } from './MissingInputTemplate'
import { QuestionsTemplate } from './QuestionsTemplate'
import { RecoveryTemplate } from './RecoveryTemplate'

export {
  AcceptanceCriteria,
  Block,
  Brief,
  ConsistencyFindings,
  Consequence,
  CritList,
  DecisionBrief,
  Ev,
  EvidenceGrid,
  FeedbackBox,
  InputBox,
  OfferedDecisions,
  Pane,
  StepList,
  SummaryChoice,
  answerComplete,
  inputKind,
  templateLabel,
} from './DecisionControls'
export type { ActionDraft, AnswerDraft, CritItem, TemplateProps } from './DecisionControls'
export { AdvisorBlock, advisorKindsFor, useAdvisor, type AdvisorState } from './AdvisorBlock'
export { GateTemplate, headingAnchor } from './GateTemplate'
export { QuestionsTemplate } from './QuestionsTemplate'
export { RecoveryTemplate } from './RecoveryTemplate'
export { FailureTemplate } from './FailureTemplate'
export { MissingInputTemplate } from './MissingInputTemplate'
export { InstallConflictTemplate } from './InstallConflictTemplate'
export { BudgetStopTemplate } from './BudgetStopTemplate'

/**
 * `run`, `resume`, `prepare_commit`, `force_stop`: a turn the user asked for.
 *
 * There is no evidence to weigh — the decision was made by pressing Run — so the body states what will be
 * dispatched, into which session, and what the repo lease means for it. It lives here rather than in its
 * own file because it is the registry's completeness guarantee, not one of the six decision templates.
 */
export function CommandTemplate({ card }: TemplateProps) {
  const i18n = useI18n()
  const { t } = i18n
  const session = card.evidence.session
  return (
    <section className="studio-template" data-type={card.type} aria-label={templateLabel(i18n, card)}>
      <DecisionBrief card={card} />
      <Block title={t('template.command.dispatch')} icon="play">
        <EvidenceGrid>
          <Ev
            src={t('template.command.session')}
            icon="activity"
            value={session?.slot_key ?? t('template.command.noSession')}
            sub={
              session
                ? t('template.command.sessionSub', {
                    state: session.running
                      ? t('template.recovery.sessionRunning')
                      : t('template.recovery.sessionIdle'),
                    turn: session.last_turn_ts ? at(i18n, session.last_turn_ts) : t('common.unavailable'),
                  })
                : t('template.command.noSessionSub')
            }
            conflict={!session}
          />
          <Ev
            src={t('template.command.stage')}
            icon="gate"
            value={card.stage?.slug ?? t('common.none')}
            sub={t('template.command.stageSub')}
          />
        </EvidenceGrid>
        <div className="studio-row studio-qmetas">
          <Chip icon="lock">{t('template.command.lease')}</Chip>
          <Chip mono>{card.intent.intent_key}</Chip>
        </div>
        <Consequence icon="info">{t('template.command.note')}</Consequence>
      </Block>
      <OfferedDecisions card={card} title={t('template.command.choices')} />
    </section>
  )
}

export type TemplateComponent = (props: TemplateProps) => ReactElement

const TEMPLATES: Record<ActionType, TemplateComponent> = {
  gate: GateTemplate,
  revision: GateTemplate,
  question: QuestionsTemplate,
  missing_input: MissingInputTemplate,
  recovery: RecoveryTemplate,
  delivery_uncertain: RecoveryTemplate,
  failure: FailureTemplate,
  circuit_breaker: FailureTemplate,
  install_conflict: InstallConflictTemplate,
  budget_stop: BudgetStopTemplate,
  run: CommandTemplate,
  resume: CommandTemplate,
  force_stop: CommandTemplate,
  prepare_commit: CommandTemplate,
}

/** The template for a card, including the re-typed-in-the-queue case. */
export function templateFor(card: ActionCard): TemplateComponent {
  if (card.queue_type === 'delivery_uncertain' && card.type !== 'delivery_uncertain') return RecoveryTemplate
  return TEMPLATES[card.type]
}

/** The Decision tab's body. Registered for every type, so the dispatch above always runs. */
export function DecisionTemplate(props: TemplateProps) {
  const Template = templateFor(props.card)
  return <Template {...props} />
}

/**
 * Plug every type into the detail pane.
 *
 * Called at module scope so a single `import './templates/TemplateRegistry'` is enough, and exported so a
 * host that prefers an explicit call has one. Registering the same dispatcher for all fourteen types keeps
 * the `queue_type` rule above in one place instead of duplicating it in `DetailShell`.
 */
export function registerDecisionTemplates(): void {
  for (const type of Object.keys(TEMPLATES) as ActionType[]) registerActionTemplate(type, DecisionTemplate)
}

registerDecisionTemplates()

export default DecisionTemplate
