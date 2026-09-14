/**
 * A budget stop: the intent is fine, the allowance is not.
 *
 * One rule shapes every line: **an unobservable quantity is `Unavailable`, never zero and never
 * inferred** (FR-EST-005, FR-NIGHT-007). Credits cannot be read from any Kiro signal, so the credit stat
 * is a permanent `Unavailable — not observable` with the reason beside it. Turns and the window come from
 * `card.budget`; when the card carries no budget block — which is every card in v1, because
 * `projection.py` never mints this type without a machine lane — the stats say that instead of printing
 * `0 / 0`, which reads as "the cap is zero".
 *
 * The host's `StatCard` is not used here: it renders a loading skeleton for `value == null`, so
 * "unobservable" would look like "still loading", forever.
 */

import { useI18n } from '../i18n'
import { count, ratio } from '../lib/format'
import { Chip } from '../shell/Chip'
import { AdvisorBlock, useAdvisor } from './AdvisorBlock'
import {
  Block,
  Consequence,
  DecisionBrief,
  OfferedDecisions,
  templateLabel,
  type TemplateProps,
} from './DecisionControls'

function Stat({ label, value, qualifier }: { label: string; value: string; qualifier: string }) {
  return (
    <div className="studio-stat">
      <span className="studio-stat-k">{label}</span>
      <span className="studio-stat-v studio-mono">{value}</span>
      <span className="studio-stat-q">{qualifier}</span>
    </div>
  )
}

export function BudgetStopTemplate({ card, detail, api, go }: TemplateProps) {
  const i18n = useI18n()
  const { t } = i18n
  const advisor = useAdvisor(card, api, detail?.drafts)
  const budget = card.budget

  return (
    <section className="studio-template" data-type={card.type} aria-label={templateLabel(i18n, card)}>
      <DecisionBrief card={card} />

      <Block title={t('template.budget.state')} icon="clock">
        <div className="studio-stats">
          <Stat
            label={t('template.budget.turns')}
            value={budget ? ratio(i18n, budget.turns_used, budget.turn_cap) : t('common.unavailable')}
            qualifier={budget ? t('common.exact') : t('template.budget.notOnCard')}
          />
          <Stat
            label={t('template.budget.window')}
            value={budget?.window ? budget.window : t('common.unavailable')}
            qualifier={budget?.window ? t('template.budget.localTime') : t('template.budget.notOnCard')}
          />
          <Stat
            label={t('template.budget.credits')}
            value={t('common.unavailable')}
            qualifier={t('template.budget.notObservable')}
          />
        </div>
        <Consequence icon="info">{t('template.budget.creditNote')}</Consequence>
        {budget ? null : (
          <Consequence icon="warn" tone="warn">
            {t('template.budget.noBudget')}
          </Consequence>
        )}
      </Block>

      <Block title={t('template.budget.effect')} icon="lock">
        <div className="studio-row studio-qmetas">
          <Chip icon="clock">{t('template.budget.neverInterrupts')}</Chip>
          {card.evidence.session ? (
            <Chip mono>{card.evidence.session.slot_key}</Chip>
          ) : (
            <Chip>{t('template.budget.noSession')}</Chip>
          )}
          <Chip icon="inbox">
            {t('template.budget.queueDepth', { n: count(i18n, card.evidence.session?.queue_depth ?? null) })}
          </Chip>
        </div>
        <Consequence icon="info">{t('template.budget.effectBody')}</Consequence>
        <button type="button" className="studio-btn" onClick={() => go({ view: 'settings' })}>
          {t('template.budget.openSettings')}
        </button>
      </Block>

      <OfferedDecisions card={card} title={t('template.budget.choices')} />

      <AdvisorBlock card={card} advisor={advisor} />
    </section>
  )
}

export default BudgetStopTemplate
