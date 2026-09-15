/**
 * The controls a decision *body* owns, and the blocks the six templates are built from.
 *
 * The sticky bar, the confirmation panel and the two-phase submit are the Action Center's
 * (`actions/ActionBar.tsx`, `actions/ConfirmPanel.tsx`, `actions/useSubmit.ts`); this module is
 * everything that has to sit beside the evidence instead:
 *
 *  - the **inputs a decision needs before the bar can offer it** — the change-request note, the missing
 *    input, and which of the two answers a summary checkpoint gets. `DetailShell.payloadFor` reads
 *    exactly these draft fields, so a template that wrote anywhere else would produce a payload the
 *    confirmation could not build wire text for;
 *  - the **read-only explanation of what each offered control will do**, so the consequence is next to
 *    the evidence rather than only in a button tooltip. It is deliberately not a second set of
 *    controls: two ways to start the same decision is two places for the guard to be missing;
 *  - the **block primitives** of visual spec §5.2 that no other area exports as components.
 *
 * It imports nothing from its own area, so the area's own dependency graph is a tree:
 * DecisionControls ← AdvisorBlock ← the six templates ← TemplateRegistry.
 */

import type { ReactNode } from 'react'

import { useI18n, type I18n } from '../i18n'
import { ref } from '../lib/format'
import { isClosedAction } from '../lib/actionQueue'
import type { ActionCard, DecisionSpec, Question } from '../lib/types'
import { answerText, MAX_ANSWER_CHARS, MAX_FEEDBACK_CHARS } from '../actions/useSubmit'
import { stageText } from '../actions/QueueRow'
import { statusLabel } from '../actions/DeliveryStrip'
import { Chip } from '../shell/Chip'
import { Icon, type IconName } from '../shell/Icon'
import type { ActionDraft } from '../actions/DetailShell'

export type { ActionDraft, AnswerDraft, TemplateProps } from '../actions/DetailShell'

// --------------------------------------------------------------------------- //
// block primitives (visual spec §5.2)
// --------------------------------------------------------------------------- //

export function Block({
  title,
  icon,
  id,
  children,
}: {
  title: string
  icon?: IconName
  id?: string
  children: ReactNode
}) {
  return (
    <section className="studio-block" {...(id ? { id } : {})}>
      <h3>
        {icon ? <Icon name={icon} size={13} /> : null}
        <span>{title}</span>
      </h3>
      {children}
    </section>
  )
}

/** The lede card. `danger` marks a contradiction or a normalized error, never a gate. */
export function Brief({ tone, children }: { tone?: 'danger'; children: ReactNode }) {
  return (
    <div className="studio-brief" {...(tone ? { 'data-tone': tone } : {})}>
      {children}
    </div>
  )
}

/** The callout Studio uses to state a consequence, or what it will not do. */
export function Consequence({
  icon = 'info',
  tone,
  label,
  children,
}: {
  icon?: IconName
  tone?: 'warn' | 'danger'
  label?: string
  children: ReactNode
}) {
  return (
    <p className="studio-consequence" {...(tone ? { 'data-tone': tone } : {})}>
      <Icon name={icon} size={13} />
      <span>
        {label ? <b>{label} </b> : null}
        {children}
      </span>
    </p>
  )
}

export interface CritItem {
  /** `crit-<n>` — the hash a deep link may scroll to (§3.2). */
  id?: string
  met: boolean | null
  text: string
  why?: string | null
}

/**
 * Acceptance criteria and unresolved risks: an icon, the sentence, and why.
 *
 * `met === null` is its own state with a neutral glyph. Studio does not evaluate a stage's acceptance
 * criteria — the engine writes the list and nothing ticks it off (`projection.py` sends `met: null`) —
 * so drawing "unknown" with the warning glyph would tell a reviewer the criterion had *failed*.
 */
export function CritList({ items }: { items: CritItem[] }) {
  const { t } = useI18n()
  return (
    <ul className="studio-crit">
      {items.map((item, i) => (
        <li key={item.id ?? i} {...(item.id ? { id: item.id } : {})} data-met={String(item.met)}>
          {item.met === true ? (
            <Icon name="check" size={13} className="studio-met" label={t('template.common.met')} />
          ) : item.met === false ? (
            <Icon name="warn" size={13} className="studio-unmet" label={t('template.common.notMet')} />
          ) : (
            <Icon name="info" size={13} className="studio-unknown" label={t('template.common.unknownMet')} />
          )}
          <span className="studio-grow">
            <span className="studio-crit-txt">{item.text}</span>
            {item.why ? <span className="studio-crit-why">{item.why}</span> : null}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** Steps, not criteria: no glyph here means met or unmet. */
export function StepList({ steps }: { steps: string[] }) {
  return (
    <ul className="studio-steplist">
      {steps.map((step, i) => (
        <li key={i}>
          <Icon name="chevron" size={13} />
          <span>{step}</span>
        </li>
      ))}
    </ul>
  )
}

export function EvidenceGrid({ children }: { children: ReactNode }) {
  return <div className="studio-evgrid">{children}</div>
}

/**
 * One evidence fact: where it came from, what it says, and what that means.
 *
 * `conflict` paints the card as contradicting another source, and is set from data (a directive that
 * does not match state, a delivery with no transcript row, a presence check that failed) — never from a
 * guess, because a highlighted card is Studio saying which source it distrusts.
 */
export function Ev({
  src,
  icon,
  value,
  sub,
  conflict,
}: {
  src: string
  icon?: IconName
  value: ReactNode
  sub?: ReactNode
  conflict?: boolean
}) {
  const { t } = useI18n()
  return (
    <div className="studio-ev" {...(conflict ? { 'data-conflict': 'true' } : {})}>
      <div className="studio-ev-src studio-row">
        {icon ? <Icon name={icon} size={11} strokeWidth={2} /> : null}
        <span>{src}</span>
        {conflict ? (
          <Chip tone="danger" icon="warn">
            {t('template.common.conflict')}
          </Chip>
        ) : null}
      </div>
      <div className="studio-ev-val studio-mono">{value}</div>
      {sub ? <div className="studio-ev-sub">{sub}</div> : null}
    </div>
  )
}

/** A bordered card with a header and a scrolling body (`.studio-pane*` is the artifacts area's CSS). */
export function Pane({
  title,
  icon,
  badge,
  footer,
  children,
}: {
  title: string
  icon?: IconName
  badge?: ReactNode
  footer?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="studio-pane" aria-label={title}>
      <header className="studio-pane-head">
        {icon ? <Icon name={icon} size={13} /> : null}
        <span className="studio-pane-nm studio-mono studio-trunc" title={title}>
          {title}
        </span>
        {badge}
      </header>
      <div className="studio-pane-body">{children}</div>
      {footer ? <footer className="studio-pane-foot">{footer}</footer> : null}
    </section>
  )
}

// --------------------------------------------------------------------------- //
// the prefix every template shares (visual spec §5.3)
// --------------------------------------------------------------------------- //

/**
 * The lede and the consequence, in the backend's words.
 *
 * Both are `I18nRef`s the *server* chose (`action.<type>.headline`,
 * `action.<type>.consequence.<variant>`), so they go through `ref()` with a fallback: a key this
 * build's catalogue lacks must not leave the most important sentence on the page blank.
 */
export function DecisionBrief({ card }: { card: ActionCard }) {
  const i18n = useI18n()
  const { t } = i18n
  const lede = ref(i18n, card.headline, 'template.common.headlineFallback')
  const consequence = card.consequence ? ref(i18n, card.consequence, 'template.common.consequenceFallback') : null
  if (isClosedAction(card)) {
    return (
      <Block title={t('template.common.closedTitle')} icon="doc">
        <Brief>
          <p>{t(card.resolution.reason === 'answer_requires_text'
            ? 'template.questions.textStillRequired'
            : card.resolution.reason === 'answer_not_verified_at_gate'
              ? 'delivery.answerNotVerifiedBody'
            : card.resolution.reason === 'command_superseded'
              ? 'template.command.superseded' : 'template.common.closedBody')}</p>
          <details>
            <summary>{t('template.common.originalNotice')}</summary>
            <p>{lede}</p>
            {consequence ? <p>{consequence}</p> : null}
          </details>
        </Brief>
      </Block>
    )
  }
  return (
    <Block title={t('template.common.brief')} icon="inbox">
      <Brief {...(card.severity === 'critical' ? { tone: 'danger' as const } : {})}>
        <p>{lede}</p>
        {consequence ? <Consequence label={t('template.common.nextConsequence')}>{consequence}</Consequence> : null}
      </Brief>
    </Block>
  )
}

/**
 * The stage's acceptance criteria, as the engine wrote them.
 *
 * The header counts only what is known: with every `met` null, "0 of 4 met" would be a claim Studio
 * cannot support and the most damaging possible framing for a reviewer skimming a gate.
 */
export function AcceptanceCriteria({ card }: { card: ActionCard }) {
  const { t, has } = useI18n()
  const criteria = card.evidence.acceptance_criteria
  if (criteria.length === 0) return null
  const evaluated = criteria.filter((c) => c.met !== null)
  const met = criteria.filter((c) => c.met === true).length
  const title =
    evaluated.length === 0
      ? t('template.gate.criteriaUnknown', { total: criteria.length })
      : t('template.gate.criteria', { met, total: criteria.length })
  return (
    <Block title={title} icon="check">
      <CritList
        items={criteria.map((c, i) => ({
          id: `crit-${i + 1}`,
          met: c.met,
          text: c.text,
          why: c.why_key && has(c.why_key) ? t(c.why_key) : null,
        }))}
      />
    </Block>
  )
}

/**
 * Consistency findings: what disagrees, how badly, and from which file.
 *
 * `message_key` and the evidence refs are the backend's; the refs are paths and audit coordinates, so
 * they are monospaced and verbatim rather than summarised into a sentence that drops the one that
 * mattered.
 */
export function ConsistencyFindings({ card, title }: { card: ActionCard; title: string }) {
  const i18n = useI18n()
  const { t } = i18n
  const findings = card.evidence.findings
  if (findings.length === 0) return null
  return (
    <Block title={title} icon="warn">
      <div className="studio-col">
        {findings.map((finding, i) => (
          <div key={`${finding.code}-${i}`} className="studio-finding" data-severity={finding.severity}>
            <div className="studio-finding-head">
              <Chip
                tone={finding.severity === 'blocking' ? 'danger' : finding.severity === 'warn' ? 'warn' : 'neutral'}
                icon={finding.severity === 'info' ? 'info' : 'warn'}
              >
                {t(`enum.findingSeverity.${finding.severity}`)}
              </Chip>
              <span className="studio-mono studio-muted studio-grow studio-trunc">{finding.code}</span>
            </div>
            <p className="studio-finding-title">
              {ref(i18n, { key: `finding.${finding.code}`, params: finding.params }, 'template.common.findingFallback')}
            </p>
            {finding.evidence.length > 0 ? (
              <ul className="studio-list studio-mono">
                {finding.evidence.map((entry, j) => (
                  <li key={j} className="studio-wrap-any">
                    {entry.kind}: {entry.ref}
                    {entry.detail ? ` — ${entry.detail}` : ''}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ))}
      </div>
    </Block>
  )
}

// --------------------------------------------------------------------------- //
// shared derivations
// --------------------------------------------------------------------------- //

/** The screen-reader label for a decision pane: type, repo, intent, stage, state (PRD §10.3). */
export function templateLabel(i18n: I18n, card: ActionCard): string {
  return i18n.t('template.a11y.decision', {
    type: i18n.t(`enum.actionType.${card.type}`),
    repo: card.repo.label,
    intent: card.intent.title ?? card.intent.slug,
    stage: stageText(card, i18n.t('common.none')),
    state: statusLabel(i18n, card.status, card.resolution.reason),
  })
}

/** `provide_input` carries a scope line for one seed and free text for the rest. */
export function inputKind(card: ActionCard): 'scope' | 'free_text' {
  return card.headline.params['kind'] === 'scope' ? 'scope' : 'free_text'
}

/**
 * Whether a question's draft answer is complete.
 *
 * Mirrors `answerText` in `actions/useSubmit.ts` from the input side: it must refuse exactly what
 * cannot become wire text, or the bar would enable a send the confirmation then cannot build.
 */
export function answerComplete(question: Question, letters: string[], freeText: string | null, textInput = false): boolean {
  if (letters.length === 0) return false
  return answerText({ index: question.index, option_letters: letters, free_text: freeText }, question, textInput) !== null
}

// --------------------------------------------------------------------------- //
// the inputs a decision needs
// --------------------------------------------------------------------------- //

/**
 * The change-request note.
 *
 * Rendered only when the card offers a decision that requires it, and never inside a `<form>`: with a
 * form, Enter in this box would be a submit, and the only submit in Studio is a deliberate confirmation.
 */
export function FeedbackBox({
  draft,
  setDraft,
  label,
  placeholder,
  routing,
  disabled,
}: {
  draft: ActionDraft
  setDraft: (patch: Partial<ActionDraft>) => void
  label: string
  placeholder: string
  routing: string
  disabled?: boolean
}) {
  const i18n = useI18n()
  const { t } = i18n
  const over = draft.feedback.length > MAX_FEEDBACK_CHARS
  return (
    <>
      <textarea
        className="studio-free"
        id="studio-feedback"
        value={draft.feedback}
        rows={4}
        placeholder={placeholder}
        aria-label={label}
        aria-describedby="studio-feedback-note"
        disabled={disabled}
        onChange={(event) => setDraft({ feedback: event.target.value })}
      />
      {over ? (
        <span className="studio-freecount" role="status">
          {t('template.common.tooLong', { max: i18n.fmt.number(MAX_FEEDBACK_CHARS) })}
        </span>
      ) : null}
      <p id="studio-feedback-note" className="studio-consequence">
        <Icon name="info" size={13} />
        <span>{routing}</span>
      </p>
    </>
  )
}

/** The missing input. Writes both the text and which kind it is, because the payload differs. */
export function InputBox({
  card,
  draft,
  setDraft,
  label,
  placeholder,
  routing,
}: {
  card: ActionCard
  draft: ActionDraft
  setDraft: (patch: Partial<ActionDraft>) => void
  label: string
  placeholder: string
  routing: string
}) {
  const i18n = useI18n()
  const { t } = i18n
  const kind = inputKind(card)
  const value = kind === 'scope' ? draft.scope : draft.freeText
  const over = value.length > MAX_ANSWER_CHARS
  return (
    <>
      <textarea
        className="studio-free"
        id="studio-input"
        value={value}
        rows={kind === 'scope' ? 2 : 4}
        placeholder={placeholder}
        aria-label={label}
        aria-describedby="studio-input-note"
        onChange={(event) =>
          setDraft(kind === 'scope' ? { scope: event.target.value, inputKind: 'scope' } : { freeText: event.target.value, inputKind: 'free_text' })
        }
      />
      {over ? (
        <span className="studio-freecount" role="status">
          {t('template.common.tooLong', { max: i18n.fmt.number(MAX_ANSWER_CHARS) })}
        </span>
      ) : null}
      <p id="studio-input-note" className="studio-consequence">
        <Icon name="info" size={13} />
        <span>{routing}</span>
      </p>
    </>
  )
}

/**
 * Which answer a summary or plan checkpoint gets.
 *
 * One backend decision (`confirm_summary`) covers two opposite replies, so the body has to carry the
 * choice: the bar has one button and `payloadFor` reads `draft.summaryChoice`. Neither option is
 * preselected — the stored default exists for the payload builder, and this control shows nothing as
 * chosen until the user chooses.
 */
export function SummaryChoice({
  draft,
  setDraft,
  name,
  groupLabel,
  looksCorrectLabel,
  looksCorrectHint,
  changesLabel,
  changesHint,
  chosen,
}: {
  draft: ActionDraft
  setDraft: (patch: Partial<ActionDraft>) => void
  name: string
  groupLabel: string
  looksCorrectLabel: string
  looksCorrectHint: string
  changesLabel: string
  changesHint: string
  /** True once the user has touched the choice; before that nothing is selected. */
  chosen: boolean
}) {
  const options: { value: ActionDraft['summaryChoice']; label: string; hint: string }[] = [
    { value: 'looks_correct', label: looksCorrectLabel, hint: looksCorrectHint },
    { value: 'request_changes', label: changesLabel, hint: changesHint },
  ]
  return (
    <div role="radiogroup" aria-label={groupLabel}>
      {options.map((option) => {
        const selected = chosen && draft.summaryChoice === option.value
        return (
          <label key={option.value} className="studio-choicerow" data-selected={String(selected)}>
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={selected}
              onChange={() => setDraft({ summaryChoice: option.value })}
            />
            <span className="studio-choice-body">
              <span className="studio-choice-label">{option.label}</span>
              <span className="studio-choice-desc">{option.hint}</span>
            </span>
          </label>
        )
      })}
    </div>
  )
}

/**
 * What the bar is offering, and what each of those will do.
 *
 * Read-only by design. The recovery mockup used radios here, but the Action Center already renders one
 * control per offered decision and each one opens a confirmation naming its consequence; a second
 * control in the body would be a second path to the same send, and only one of the two could carry the
 * at-most-once guard. So this lists the same decisions as prose beside the evidence — which is what
 * "no transition happens until you choose" needs the user to have read.
 */
export function OfferedDecisions({ card, title }: { card: ActionCard; title: string }) {
  const { t, has } = useI18n()
  const decisions: DecisionSpec[] = card.decisions
  if (decisions.length === 0) return null
  return (
    <Block title={title} icon="recovery">
      <ul className="studio-choicelist">
        {decisions.map((spec) => (
          <li key={spec.decision}>
            <Icon name="chevron" size={13} />
            <span className="studio-choice-body">
              <span className="studio-choice-label">{t(spec.label_key)}</span>
              <span className="studio-choice-desc">
                {has(`decision.${spec.decision}.hint`) ? t(`decision.${spec.decision}.hint`) : ''}
              </span>
            </span>
          </li>
        ))}
      </ul>
      <Consequence icon="lock">{t('template.common.noAutoChoice')}</Consequence>
    </Block>
  )
}
