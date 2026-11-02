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
  /** What Create makes, said on the step Create sits on: the scope bar can name another repository meanwhile. */
  work?: { objective: string; label: string; space: string; scope: string }
  /** Back to the Work step, offered when Create was refused over one of its fields. */
  onEditWork?: () => void
}

/** The Work step's fields a refusal's `details.key` can name, and the label each carries there. */
const WORK_FIELDS: Readonly<Record<string, string>> = {
  objective: 'wizard.work.objective',
  context: 'wizard.work.context',
  label: 'wizard.work.label',
}

/** The Work field a `bad_body`/`plan_invalid` refusal names, or null when it names none. */
function refusedField(error: StudioApiError): string | null {
  if (error.code !== 'bad_body' && error.code !== 'plan_invalid') return null
  const key = error.details['key']
  return typeof key === 'string' && key in WORK_FIELDS ? key : null
}

export function StepReview({
  plan, digest, repo, origins, busy, creating, workComplete, presetComplete, error, work, onEditWork,
}: StepReviewProps) {
  const i18n = useI18n()
  const { t } = i18n
  // Nothing here counts the repository's findings: Create refuses on the repository's own (not on
  // another intent's recovery card), and that refusal is shown above with its reason.
  const blockers: string[] = []
  if (!workComplete) blockers.push(t('wizard.review.blockedFields'))
  if (!presetComplete || !plan) blockers.push(t('wizard.preview.none'))
  if (plan && !plan.valid) blockers.push(t('wizard.review.blockedInvalid'))
  // Products that only a selected CONDITIONAL stage declares: AI-DLC decides at runtime whether they exist.
  const selected = plan ? plan.stages.filter((stage) => stage.enabled) : []
  const always = new Set(selected.filter((stage) => stage.execution !== 'CONDITIONAL').flatMap((stage) => stage.produces))
  const conditionalOnly = new Set(selected.filter((stage) => stage.execution === 'CONDITIONAL')
    .flatMap((stage) => stage.produces).filter((name) => !always.has(name)))
  // A refusal over one field says which and why: `errors.bad_body` alone ("could not be read") reads as a
  // transport fault, when the fix is a paste on the Work step. The reason is the backend's own words.
  const field = error ? refusedField(error) : null
  const reason = error && typeof error.details['reason'] === 'string' ? (error.details['reason'] as string) : error?.message

  return (
    <div>
      {error ? (
        <p className="studio-banner" data-tone="danger" role="alert">
          <Icon name="warn" size={15} />
          <span className="studio-grow">
            {t('wizard.error.create')}{' '}
            {field
              ? t('wizard.error.field', { field: t(WORK_FIELDS[field] as string), reason: reason ?? '' })
              : error.known ? t(`errors.${error.code}`) : error.message}
          </span>
          {field && onEditWork ? (
            <button type="button" className="studio-btn" onClick={onEditWork}>
              {t('wizard.error.fieldEdit')}
            </button>
          ) : null}
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

      {work && repo ? (
        <section className="studio-block" aria-label={t('wizard.review.what')}>
          <h3>
            <Icon name="intent" size={13} />
            {t('wizard.review.what')}
          </h3>
          <dl className="studio-facts">
            <div className="studio-fact">
              <dt>{t('wizard.review.whatRepo')}</dt>
              <dd>
                <span className="studio-strong">{repo.label}</span>{' '}
                <span className="studio-mono studio-muted studio-wrap-any">{repo.canonical_path}</span>
              </dd>
            </div>
            <div className="studio-fact">
              <dt>{t('wizard.review.whatIntent')}</dt>
              <dd className="studio-mono studio-wrap-any">
                {work.space && work.space !== 'default' ? `${work.space} / ${work.label}` : work.label}
              </dd>
            </div>
            <div className="studio-fact">
              <dt>{t('wizard.review.whatScope')}</dt>
              <dd className="studio-mono">{work.scope || t('common.none')}</dd>
            </div>
            <div className="studio-fact">
              <dt>{t('wizard.review.whatObjective')}</dt>
              <dd className="studio-verbatim studio-wrap-any">{work.objective}</dd>
            </div>
          </dl>
        </section>
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
              <>
                <p className="studio-muted">{t('wizard.review.productsWhy')}</p>
                <div className="studio-row studio-wrap">
                  {/* Artifact names are AI-DLC's own words. A product only a conditional stage declares exists
                      only if AI-DLC runs that stage, so it is marked rather than promised. */}
                  {plan.products.map((product) => conditionalOnly.has(product) ? (
                    <Chip key={product} mono icon="info" className="studio-chip-maybe" title={t('wizard.review.productConditional')}>
                      {product}
                    </Chip>
                  ) : (
                    <Chip key={product} mono>
                      {product}
                    </Chip>
                  ))}
                </div>
              </>
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
