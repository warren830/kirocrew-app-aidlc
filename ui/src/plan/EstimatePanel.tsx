/**
 * Where the honesty of the whole planning flow is visible (FR-EST-001…007, PRD P-07).
 *
 * Four rules, each of which is a way this panel could lie and does not:
 *
 *  1. **Exact and estimated never share a row.** Counts come from the installed graph and are labelled
 *     exact; turns and durations are ranges and are labelled with their source and confidence. The
 *     mockup's two `.stat` rows exist for exactly this reason and are kept.
 *  2. **Credits are Unavailable, never a number.** `credits` is always `null` with
 *     `credits_status: "unavailable"`, and rendering `0` would read as "this is free" (FR-EST-005).
 *  3. **An assumption says so.** `elapsed_secs` carries its own `source: "assumption"`, and
 *     `assumes_units` means the artifact count was computed for one unit of work because no unit exists
 *     yet. Both are printed beside the number, not in a footnote.
 *  4. **The dominant stages name what removing them costs.** `dominant` is the server's ranking; the
 *     coverage a stage carries is its own `produces` list, so "what you lose" is read from the graph
 *     rather than guessed (FR-EST-007). A dominant stage the engine will not let you remove says that
 *     instead of implying a choice you do not have.
 */

import { useMemo, type ReactNode } from 'react'

import { Icon, type IconName } from '../shell/Icon'
import { useI18n, type I18n } from '../i18n'
import { count, plural, range, unavailable } from '../lib/format'
import type { EffectivePlan, PlanStage, Range } from '../lib/types'
import { lockSentence } from './StageToggle'

/** "range · rule-based band · low confidence [· no local history yet]". */
export function rangeQualifier(i18n: I18n, value: Range, samples?: number): string {
  const sourceKey = `estimate.source.${value.source}`
  const params = {
    kind: i18n.t('estimate.range'),
    source: i18n.has(sourceKey) ? i18n.t(sourceKey) : i18n.t('estimate.source.unknown'),
    confidence: i18n.t(`estimate.confidence.${value.confidence}`),
  }
  if (samples === undefined) return i18n.t('estimate.qualifier', params)
  const text = samples > 0 ? plural(i18n, 'estimate.samples', samples) : i18n.t('estimate.samples.none')
  return i18n.t('estimate.qualifierSamples', { ...params, samples: text })
}

function Stat({ icon, label, value, qualifier, small }: { icon: IconName; label: string; value: ReactNode; qualifier: string; small?: boolean }) {
  return (
    <div className="studio-stat">
      <div className="studio-stat-k">
        <Icon name={icon} size={13} />
        {label}
      </div>
      <div className="studio-stat-v" data-small={small ? 'true' : 'false'}>
        {value}
      </div>
      <div className="studio-stat-q">{qualifier}</div>
    </div>
  )
}

export interface EstimatePanelProps {
  plan: EffectivePlan
}

export function EstimatePanel({ plan }: EstimatePanelProps) {
  const i18n = useI18n()
  const { t } = i18n
  const { exact, estimate } = plan
  const bySlug = useMemo(() => new Map(plan.stages.map((stage) => [stage.slug, stage])), [plan.stages])
  const units = exact.assumes_units
  const perUnitSelected = plan.stages.some((stage) => stage.enabled && stage.per_unit)

  const artifactQualifier = units !== null && perUnitSelected
    ? plural(i18n, 'estimate.exact.artifactsAssumed', units)
    : t('estimate.exact.artifactsWhy')

  return (
    <>
      <section className="studio-block" aria-label={t('estimate.a11y.exact')}>
        <h3>
          <Icon name="check" size={13} />
          {t('estimate.exact.title')}
        </h3>
        <div className="studio-stats">
          <Stat
            icon="check"
            label={t('estimate.exact.stages')}
            value={count(i18n, exact.stages)}
            qualifier={t('estimate.exact.ofGraph', {
              selected: i18n.fmt.number(exact.stages),
              known: count(i18n, plan.graph_stage_count),
            })}
          />
          <Stat icon="gate" label={t('estimate.exact.gates')} value={count(i18n, exact.gates)} qualifier={t('estimate.exact.gatesWhy')} />
          <Stat icon="doc" label={t('estimate.exact.artifacts')} value={count(i18n, exact.artifacts)} qualifier={artifactQualifier} />
          <Stat
            icon="review"
            label={t('estimate.exact.review')}
            small
            value={t('estimate.exact.reviewValue', {
              none: i18n.fmt.number(exact.review_intensity.none),
              advisory: i18n.fmt.number(exact.review_intensity.advisory),
              adversarial: i18n.fmt.number(exact.review_intensity.adversarial),
            })}
            qualifier={t('estimate.exact.reviewWhy')}
          />
        </div>
      </section>

      <section className="studio-block" aria-label={t('estimate.a11y.estimate')}>
        <h3>
          <Icon name="clock" size={13} />
          {t('estimate.estimate.title')}
        </h3>
        <div className="studio-stats">
          <Stat
            icon="clock"
            label={t('estimate.turns')}
            small
            value={range(i18n, estimate.turns)}
            qualifier={rangeQualifier(i18n, estimate.turns, estimate.samples)}
          />
          <Stat
            icon="clock"
            label={t('estimate.active')}
            small
            value={range(i18n, estimate.active_secs)}
            qualifier={rangeQualifier(i18n, estimate.active_secs)}
          />
          <Stat
            icon="clock"
            label={t('estimate.elapsed')}
            small
            value={estimate.elapsed_secs ? range(i18n, estimate.elapsed_secs) : unavailable(i18n)}
            qualifier={
              estimate.elapsed_secs
                ? `${rangeQualifier(i18n, estimate.elapsed_secs)} · ${t('estimate.elapsed.why')}`
                : t('estimate.elapsed.unavailable')
            }
          />
          {/* Never a number, in any locale: the value is the word, and the qualifier says why. */}
          <Stat
            icon="warn"
            label={t('estimate.credits')}
            small
            value={t('estimate.credits.value')}
            qualifier={t('estimate.credits.why')}
          />
        </div>
      </section>

      <section className="studio-block">
        <h3>
          <Icon name="info" size={13} />
          {t('estimate.dominant.title')}
        </h3>
        {estimate.dominant.length === 0 ? (
          <p className="studio-muted">{t('estimate.dominant.none')}</p>
        ) : (
          <ul className="studio-crit">
            {estimate.dominant.map((item) => {
              const stage: PlanStage | undefined = bySlug.get(item.slug)
              const locked = stage ? lockSentence(i18n, stage.lock_reason) : null
              const produces = stage?.produces ?? []
              return (
                <li key={item.slug}>
                  <Icon name="clock" size={13} />
                  <span className="studio-crit-txt">
                    {t('estimate.dominant.item', { slug: item.slug, pct: i18n.fmt.number(item.share_pct) })}
                    <span className="studio-crit-why">
                      {t('estimate.dominant.turns', { range: range(i18n, item.turns) })}
                      {stage?.per_unit && units !== null ? ` · ${plural(i18n, 'estimate.dominant.perUnit', units)}` : ''}
                    </span>
                    <span className="studio-crit-why">
                      {locked
                        ? t('estimate.dominant.locked', { reason: locked })
                        : produces.length
                          ? t('estimate.dominant.lost', { artifacts: produces.join(t('shell.format.listJoin')) })
                          : t('estimate.dominant.lostNone')}
                    </span>
                  </span>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      {estimate.coverage_lost.length > 0 ? (
        <section className="studio-block">
          <h3>
            <Icon name="warn" size={13} />
            {t('estimate.coverage.title')}
          </h3>
          <ul className="studio-crit">
            {estimate.coverage_lost.map((item) => (
              <li key={item.slug} data-tone="warn">
                <Icon name="warn" size={13} />
                <span className="studio-crit-txt">
                  {item.artifacts.length
                    ? t('estimate.coverage.item', {
                        slug: item.slug,
                        artifacts: item.artifacts.join(t('shell.format.listJoin')),
                      })
                    : t('estimate.coverage.itemNone', { slug: item.slug })}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  )
}
