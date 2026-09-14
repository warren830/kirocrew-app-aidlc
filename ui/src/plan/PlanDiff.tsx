/**
 * What this request changes about the plan, and what that costs downstream (FR-PLAN-007).
 *
 * Three separate claims, kept separate because they have different authority:
 *
 *  1. **The diff** is the server's: `EffectivePlan.diff` is every stage whose selection differs from the
 *     composition on disk. Recomputing it here from the overrides would drift the moment a scope file
 *     changed under the wizard.
 *  2. **The consequences** are set arithmetic over data the server sent — the artifacts a disabled stage
 *     produced that nothing else now produces, and the still-selected stages that name those artifacts
 *     among their `consumes`. It says "declared as an input", never "will fail": the wire shape does not
 *     carry the required/optional flag, and the engine's own verdict is the issue list below.
 *  3. **The issues** are the engine's rule, quoted through its own message keys. This is the only thing
 *     on screen allowed to say a combination is rejected.
 */

import { useMemo } from 'react'

import { Icon } from '../shell/Icon'
import { useI18n, type I18n } from '../i18n'
import { refParams } from '../lib/format'
import type { EffectivePlan, PlanIssue, PlanStage } from '../lib/types'

/**
 * A plan issue in the user's language.
 *
 * Every code the backend can emit has a `plan.issue.<code>` key; an unknown code (a newer backend than
 * this bundle) falls back to naming the code rather than rendering an empty line, because "AI-DLC
 * refused and Studio will not say why" is the one outcome the user cannot act on.
 */
export function planIssueText(i18n: I18n, issue: PlanIssue): string {
  const params = refParams(i18n, { key: issue.message_key, params: issue.params as Record<string, string | number | null> })
  if (i18n.has(issue.message_key)) return i18n.t(issue.message_key, params)
  return i18n.t('plan.issue.unknown', { ...params, code: issue.code })
}

export interface IssueListProps {
  issues: PlanIssue[]
  /** `warn` for a refusal the user can still edit away, `danger` for one that blocks the whole action. */
  tone?: 'warn' | 'danger'
}

/** The engine's refusals, one per line, each naming the stages it is about. */
export function IssueList({ issues, tone = 'warn' }: IssueListProps) {
  const i18n = useI18n()
  if (issues.length === 0) return null
  return (
    <ul className="studio-crit">
      {issues.map((issue, index) => (
        <li key={`${issue.code}-${issue.slugs.join(',')}-${index}`} data-tone={tone}>
          <Icon name={tone === 'danger' ? 'recovery' : 'warn'} size={13} />
          <span className="studio-crit-txt">{planIssueText(i18n, issue)}</span>
        </li>
      ))}
    </ul>
  )
}

interface Consequence {
  artifact: string
  /** True when the artifact came back because a stage was turned on. */
  gained: boolean
  consumers: string[]
}

/** The products that appear or disappear because of this request, with the stages that name them. */
export function consequencesOf(plan: EffectivePlan): Consequence[] {
  const bySlug = new Map(plan.stages.map((stage) => [stage.slug, stage]))
  const producedNow = new Set<string>()
  const producedBefore = new Set<string>()
  for (const stage of plan.stages) {
    for (const artifact of stage.produces) {
      if (stage.enabled) producedNow.add(artifact)
      if (stage.in_grid) producedBefore.add(artifact)
    }
  }
  const out: Consequence[] = []
  const seen = new Set<string>()
  for (const entry of plan.diff) {
    const stage = bySlug.get(entry.slug)
    if (!stage) continue
    for (const artifact of stage.produces) {
      if (seen.has(artifact)) continue
      const gained = entry.to_enabled && producedNow.has(artifact) && !producedBefore.has(artifact)
      const lost = !entry.to_enabled && !producedNow.has(artifact)
      if (!gained && !lost) continue
      seen.add(artifact)
      out.push({
        artifact,
        gained,
        consumers: plan.stages
          .filter((candidate: PlanStage) => candidate.enabled && candidate.consumes.includes(artifact))
          .map((candidate) => candidate.slug),
      })
    }
  }
  return out
}

export interface PlanDiffProps {
  plan: EffectivePlan
  /** Hide the "no changes" line where the surrounding page already says it (the recompose panel). */
  hideEmpty?: boolean
  /**
   * Slugs whose change came from an Advisor proposal rather than the human's own click (FR-NEW-006). The
   * row for such a slug says so, because a matrix that silently mixed the Advisor's picks with the human's
   * would make the wizard's "labelled as the Advisor's draft" promise false the moment the panel was closed.
   */
  origins?: Readonly<Record<string, 'advisor'>>
}

export function PlanDiff({ plan, hideEmpty, origins }: PlanDiffProps) {
  const i18n = useI18n()
  const { t } = i18n
  const consequences = useMemo(() => consequencesOf(plan), [plan])
  const scope = plan.request.scope

  if (plan.diff.length === 0 && plan.issues.length === 0 && hideEmpty) return null

  return (
    <section className="studio-block">
      <h3>
        <Icon name="doc" size={13} />
        {t('plan.diff.title')}
      </h3>

      {plan.diff.length === 0 ? (
        <span className="studio-diffline" data-kind="add">
          {scope ? t('plan.diff.none', { scope }) : t('plan.diff.noneNoScope')}
        </span>
      ) : (
        <div className="studio-difflines">
          {plan.diff.map((entry) => {
            const advisor = origins?.[entry.slug] === 'advisor'
            const key = entry.to_enabled
              ? advisor ? 'plan.diff.on.advisor' : 'plan.diff.on'
              : advisor ? 'plan.diff.off.advisor' : 'plan.diff.off'
            return (
              <span
                className="studio-diffline"
                data-kind={entry.to_enabled ? 'add' : 'del'}
                data-origin={advisor ? 'advisor' : undefined}
                key={entry.slug}
              >
                {t(key, { slug: entry.slug })}
              </span>
            )
          })}
        </div>
      )}

      {plan.diff.length > 0 ? (
        <div className="studio-subblock">
          <h4>{t('plan.diff.consequences')}</h4>
          {consequences.length === 0 ? (
            <p className="studio-muted">{t('plan.diff.consequencesNone')}</p>
          ) : (
            <ul className="studio-crit">
              {consequences.map((item) => (
                <li key={item.artifact} data-tone={item.gained ? 'ok' : 'warn'}>
                  <Icon name={item.gained ? 'check' : 'warn'} size={13} />
                  <span className="studio-crit-txt">
                    {item.gained
                      ? t('plan.diff.gained', { artifact: item.artifact })
                      : t('plan.diff.lost', { artifact: item.artifact })}
                    <span className="studio-crit-why">
                      {item.consumers.length
                        ? t('plan.diff.lostConsumers', { stages: item.consumers.join(t('shell.format.listJoin')) })
                        : t('plan.diff.lostNoConsumers')}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}

      {plan.issues.length > 0 ? (
        <div className="studio-subblock">
          <h4>{t('plan.issues.title')}</h4>
          <IssueList issues={plan.issues} tone="danger" />
        </div>
      ) : null}
    </section>
  )
}
