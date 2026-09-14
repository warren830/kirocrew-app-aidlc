/**
 * Step 4 — Review: exact facts, estimates, the diff, the products, and what Create will and will not do.
 *
 * FR-NEW-002 is the whole design of this step: exact counts and estimates are in separate blocks with
 * their own labels, so nothing on screen can be read as a promise it is not (`EstimatePanel`).
 *
 * The plan fingerprint is shown, not hidden. It is what Create sends as `confirm_plan_digest`, and saying
 * so is how the refusal that follows a changed scope file reads as a safety feature instead of a bug.
 */

import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { useI18n } from '../i18n'
import { plural } from '../lib/format'
import type { StudioApiError } from '../lib/api'
import type { EffectivePlan, RepoRecord } from '../lib/types'
import { EstimatePanel } from '../plan/EstimatePanel'
import { PlanDiff } from '../plan/PlanDiff'

export interface StepReviewProps {
  plan: EffectivePlan | null
  /** The digest Create will send, computed by the owner so this module needs nothing from it but props. */
  digest: string
  repo: RepoRecord | null
  /** Overrides that came from an applied Advisor proposal; the diff labels those rows (FR-NEW-006). */
  origins?: Readonly<Record<string, 'advisor'>>
  busy: boolean
  creating: boolean
  workComplete: boolean
  presetComplete: boolean
  error: StudioApiError | null
}

export function StepReview({ plan, digest, repo, origins, busy, creating, workComplete, presetComplete, error }: StepReviewProps) {
  const i18n = useI18n()
  const { t } = i18n
  const blockingFindings = repo?.counts.blocking_findings ?? 0

  const blockers: string[] = []
  if (!workComplete) blockers.push(t('wizard.review.blockedFields'))
  if (!presetComplete || !plan) blockers.push(t('wizard.preview.none'))
  if (plan && !plan.valid) blockers.push(t('wizard.review.blockedInvalid'))
  if (blockingFindings > 0) blockers.push(plural(i18n, 'wizard.review.blockedFindings', blockingFindings))

  return (
    <div>
      {error ? (
        <p className="studio-banner" data-tone="danger" role="alert">
          <Icon name="warn" size={15} />
          <span className="studio-grow">
            {t('wizard.error.create')} {error.known ? t(`errors.${error.code}`) : error.message}
          </span>
        </p>
      ) : null}

      {blockers.length > 0 ? (
        <ul className="studio-crit" aria-label={t('wizard.error.create')}>
          {blockers.map((line) => (
            <li key={line} data-tone="warn">
              <Icon name="warn" size={13} />
              <span className="studio-crit-txt">{line}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {plan ? (
        <>
          {busy ? (
            <p className="studio-muted" role="status">
              {t('wizard.preview.stale')}
            </p>
          ) : null}

          <EstimatePanel plan={plan} />
          <PlanDiff plan={plan} origins={origins} />

          <section className="studio-block">
            <h3>
              <Icon name="doc" size={13} />
              {t('wizard.review.products')}
            </h3>
            {plan.products.length === 0 ? (
              <p className="studio-muted">{t('wizard.review.productsNone')}</p>
            ) : (
              <div className="studio-row studio-wrap">
                {/* Artifact names are AI-DLC's own words. */}
                {plan.products.map((product) => (
                  <Chip key={product} mono>
                    {product}
                  </Chip>
                ))}
              </div>
            )}
          </section>

          <section className="studio-block">
            <h3>
              <Icon name="lock" size={13} />
              {t('wizard.review.plan')}
            </h3>
            <p className="studio-help">{t('wizard.review.planDigestWhy')}</p>
            <p className="studio-mono studio-wrap-any studio-digest">
              {t('wizard.review.planDigest', { digest })}
            </p>
          </section>
        </>
      ) : null}

      <p className="studio-consequence">
        <Icon name="info" size={13} />
        <span>{t('wizard.review.consequence')}</span>
      </p>
      {creating ? (
        <p className="studio-muted" role="status">
          {t('wizard.creating')}
        </p>
      ) : null}
    </div>
  )
}
