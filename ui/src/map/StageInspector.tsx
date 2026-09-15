/**
 * What one stage actually is, on disk (FR-MAP-008).
 *
 * Four evidence blocks and exactly one control that can leave the page:
 *
 *  - **Relationships** — upstream/downstream are stages, so they are buttons that move the selection;
 *    consumes/produces are artifact names from the graph and stay text, because they are AI-DLC's own
 *    words and there is nothing to navigate to.
 *  - **Files** — the metadata the projection hashed, never contents. The note says Studio only reads
 *    them, because the whole map is a read model and a user should not have to infer that.
 *  - **Review** — the stage's configured contract always, plus the record's latest review ONLY when it
 *    belongs to this stage. `GET …/review` reports the *current* stage; showing its verdict under
 *    another stage's heading would attribute a verdict to work that was never reviewed.
 *  - **Audit** — the record's own events, field names and values verbatim.
 *
 * The single operation is a navigation into the Action Center, never a dispatch. The map has no
 * `captured` snapshot and no confirmation panel, so it must not be able to send anything: the decision
 * is made where the evidence is re-read and the exact wire text is shown.
 */

import { useI18n } from '../i18n'
import { at, bytes, duration, plural, since } from '../lib/format'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import type {
  ActionCard, ArtifactMeta, AuditEventView, MapStage, MapUnit, ReviewFinding, ReviewResponse,
} from '../lib/types'
import { artifactCountLabel, stageBadge } from './StageCard'

/** Audit fields that are already rendered as the row's own timestamp and title. */
const AUDIT_HEADER_FIELDS = new Set(['Timestamp', 'Event'])

const REVIEW_LEVELS: readonly ReviewFinding['level'][] = ['blocker', 'advisory', 'resolved', 'unknown']

export interface StageInspectorProps {
  stage: MapStage | null
  /** The selected unit's row, when the selection is a unit sub-card. */
  unit: MapUnit | null
  phaseLabel: string
  repoLabel: string
  intentLabel: string
  review: ReviewResponse | null
  /**
   * The record's recent audit events, already filtered to this stage — `null` when the intent read
   * failed, which must not be shown as "no events happened".
   */
  audit: AuditEventView[] | null
  /** The live card whose stage is this one, if any. */
  action: ActionCard | null
  /** Intent-level refusal that outranks anything the stage itself permits. */
  blocked: 'paused' | 'archived' | null
  onClear: () => void
  onSelectStage: (slug: string) => void
  onOpenAction: (actionId: string) => void
  onOpenArtifact: (artifact: ArtifactMeta) => void
}

export function StageInspector({
  stage, unit, phaseLabel, repoLabel, intentLabel, review, audit, action, blocked,
  onClear, onSelectStage, onOpenAction, onOpenArtifact,
}: StageInspectorProps) {
  const i18n = useI18n()
  const { t } = i18n

  if (!stage) {
    return (
      <div className="studio-inspector-body">
        <h2>{t('map.inspector.title')}</h2>
        <p className="studio-muted">{t('map.inspector.pick')}</p>
      </div>
    )
  }

  const badge = stageBadge(i18n, unit ? unit.state : stage.state)
  const artifacts = unit ? unit.artifacts : stage.artifacts
  const notExecuting = stage.state === 'skipped' || stage.state === 'excluded' || !!stage.skipped_reason
  const reviewIsThisStage = !!review && review.stage === stage.slug

  return (
    <div className="studio-inspector-body">
      <div className="studio-spread studio-inspector-head">
        <span className="studio-row">
          <span className="studio-phase-chip" data-phase={stage.phase}>
            <Chip>{phaseLabel}</Chip>
          </span>
          {stage.number ? <Chip mono>{stage.number}</Chip> : null}
        </span>
        <button type="button" className="studio-icon-btn" onClick={onClear} aria-label={t('map.inspector.close')}>
          <Icon name="close" size={14} />
        </button>
      </div>

      <h2 className="studio-wrap-any">{stage.slug}</h2>
      {unit ? <p className="studio-mono studio-muted studio-inspector-unit">{unit.unit}</p> : null}
      <p className="studio-sr">{`${repoLabel} / ${intentLabel}`}</p>

      <div className="studio-row studio-inspector-chips">
        <Chip tone={badge.tone} icon={badge.icon}>
          {badge.label}
        </Chip>
        {stage.is_current ? (
          <Chip tone="accent">
            <span className="studio-pulse" aria-hidden />
            {t('map.chip.current')}
          </Chip>
        ) : null}
        {stage.is_directive ? <Chip tone="aim" icon="send">{t('map.chip.directive')}</Chip> : null}
        {stage.gate ? <Chip tone="warn" icon="gate">{t('map.chip.gate')}</Chip> : null}
        {stage.execution === 'CONDITIONAL' ? <Chip icon="info">{t('map.chip.conditional')}</Chip> : null}
        {stage.agent ? (
          <Chip icon="intent" mono title={t('map.chip.agentTitle', { agent: stage.agent })}>
            {stage.agent}
          </Chip>
        ) : null}
        {stage.mode ? (
          <Chip mono title={t('map.chip.modeTitle', { mode: stage.mode })}>{stage.mode}</Chip>
        ) : null}
        <Chip icon="clock" mono>
          {duration(i18n, stage.elapsed_secs)}
        </Chip>
      </div>

      {stage.summary_confirmation ? (
        <p className="studio-muted studio-inspector-note">
          {t('map.inspector.summaryConfirmation', { value: stage.summary_confirmation })}
        </p>
      ) : null}

      {notExecuting ? (
        <p className="studio-consequence">
          <b>{t('map.inspector.why')}</b>{' '}
          {stage.skipped_reason || t(stage.state === 'excluded' ? 'map.inspector.notSelected' : 'map.inspector.whyUnknown')}
        </p>
      ) : null}

      <section className="studio-block">
        <h3>{t('map.inspector.relationships')}</h3>
        <div className="studio-evgrid">
          <StageRefs
            title={t('map.inspector.upstream')}
            slugs={stage.depends_on}
            onSelect={onSelectStage}
            empty={t('map.inspector.none')}
          />
          <StageRefs
            title={t('map.inspector.downstream')}
            slugs={stage.dependents}
            onSelect={onSelectStage}
            empty={t('map.inspector.none')}
          />
          <NameList title={t('map.inspector.consumes')} names={stage.consumes} empty={t('map.inspector.none')} />
          <NameList title={t('map.inspector.produces')} names={stage.produces} empty={t('map.inspector.none')} />
        </div>
      </section>

      <section className="studio-block">
        <h3>
          {t('map.inspector.artifacts')} <span className="studio-muted">{artifactCountLabel(i18n, artifacts.length)}</span>
        </h3>
        {artifacts.length === 0 ? (
          <p className="studio-muted">{t('map.inspector.artifactsEmpty')}</p>
        ) : (
          <ul className="studio-file-list" role="list">
            {artifacts.map((meta) => (
              <li key={meta.artifact_id}>
                <button
                  type="button"
                  className="studio-file"
                  onClick={() => onOpenArtifact(meta)}
                  aria-label={t('map.inspector.openArtifact', { name: meta.name })}
                >
                  <Icon name="doc" size={13} />
                  <span className="studio-grow studio-trunc studio-mono">{meta.name}</span>
                  <Chip mono>{t(`map.artifactKind.${meta.kind}`)}</Chip>
                  <span className="studio-muted studio-mono studio-file-meta">
                    {bytes(i18n, meta.size)} · {since(i18n, meta.mtime)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className="studio-muted studio-inspector-note">{t('map.inspector.artifactsNote')}</p>
      </section>

      <section className="studio-block">
        <h3>{t('map.inspector.review')}</h3>
        <p className="studio-muted">
          {stage.review_class
            ? stage.reviewer
              ? t('map.inspector.reviewContract', { class: stage.review_class, reviewer: stage.reviewer })
              : t('map.inspector.reviewContractNoReviewer', { class: stage.review_class })
            : t('map.inspector.reviewNone')}
        </p>
        {review && !reviewIsThisStage && review.stage ? (
          <p className="studio-muted">{t('map.inspector.reviewOtherStage', { stage: review.stage })}</p>
        ) : null}
        {reviewIsThisStage ? <ReviewSummary review={review} /> : null}
      </section>

      <section className="studio-block">
        <h3>{t('map.inspector.audit')}</h3>
        {audit === null ? (
          <p className="studio-muted">{t('common.unavailable')}</p>
        ) : audit.length === 0 ? (
          <p className="studio-muted">{t('map.inspector.auditEmpty')}</p>
        ) : (
          <ul className="studio-audit-list" role="list">
            {audit.map((event) => (
              <li key={`${event.shard}:${event.pos}`} className="studio-audit">
                <span className="studio-mono studio-muted">{at(i18n, event.timestamp)}</span>
                <span className="studio-mono studio-audit-event">{event.event}</span>
                <span className="studio-audit-fields studio-wrap-any">
                  {Object.entries(event.fields)
                    .filter(([name]) => !AUDIT_HEADER_FIELDS.has(name))
                    .slice(0, 4)
                    .map(([name, value]) => (
                      <span key={name} className="studio-audit-field">
                        <b className="studio-mono">{name}</b> <span className="studio-mono">{value}</span>
                      </span>
                    ))}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="studio-muted studio-inspector-note">{t('map.inspector.auditNote')}</p>
      </section>

      <section className="studio-block">
        <h3>{t('map.inspector.operation')}</h3>
        {action && !blocked ? (
          <button type="button" className="studio-btn studio-btn-primary studio-full" onClick={() => onOpenAction(action.action_id)}>
            <Icon name="inbox" size={15} />
            {t('map.inspector.open', { type: t(`enum.actionType.${action.queue_type}`) })}
          </button>
        ) : (
          <>
            <button type="button" className="studio-btn studio-full" disabled>
              <Icon name="lock" size={15} />
              {t('map.inspector.noOperation')}
            </button>
            <p className="studio-muted studio-inspector-note">{whyNoOperation(t, stage, blocked)}</p>
          </>
        )}
      </section>
    </div>
  )
}

/** The honest reason, in the order that outranks: the intent first, then the stage's own position. */
function whyNoOperation(
  t: (key: string) => string,
  stage: MapStage,
  blocked: 'paused' | 'archived' | null,
): string {
  if (blocked === 'archived') return t('map.inspector.reasonArchived')
  if (blocked === 'paused') return t('map.inspector.reasonPaused')
  if (stage.state === 'skipped' || stage.state === 'excluded') return t('map.inspector.reasonSkipped')
  if (stage.state === 'completed') return t('map.inspector.reasonDone')
  return t('map.inspector.reasonAhead')
}

function StageRefs({
  title, slugs, empty, onSelect,
}: { title: string; slugs: string[]; empty: string; onSelect: (slug: string) => void }) {
  return (
    <div className="studio-ev">
      <span className="studio-ev-src">{title}</span>
      {slugs.length === 0 ? (
        <span className="studio-muted">{empty}</span>
      ) : (
        <span className="studio-ev-val">
          {slugs.map((slug) => (
            <button key={slug} type="button" className="studio-link studio-mono" onClick={() => onSelect(slug)}>
              {slug}
            </button>
          ))}
        </span>
      )}
    </div>
  )
}

function NameList({ title, names, empty }: { title: string; names: string[]; empty: string }) {
  return (
    <div className="studio-ev">
      <span className="studio-ev-src">{title}</span>
      {names.length === 0 ? (
        <span className="studio-muted">{empty}</span>
      ) : (
        <span className="studio-ev-val studio-mono studio-wrap-any">
          {names.map((name) => (
            <span key={name} className="studio-ev-name">
              {name}
            </span>
          ))}
        </span>
      )}
    </div>
  )
}

/**
 * The record's review for THIS stage: verdict, revision count, one chip per level, and the blockers'
 * own titles. Titles are the reviewer's words and are never translated.
 */
function ReviewSummary({ review }: { review: ReviewResponse }) {
  const i18n = useI18n()
  const { t } = i18n
  const counts = new Map<ReviewFinding['level'], number>()
  for (const finding of review.findings) counts.set(finding.level, (counts.get(finding.level) ?? 0) + 1)
  const blockers = review.findings.filter((finding) => finding.level === 'blocker')
  const shown = blockers.slice(0, 3)

  if (review.findings.length === 0 && !review.verdict) {
    return <p className="studio-muted">{t('map.inspector.reviewEmpty')}</p>
  }

  return (
    <div className="studio-col studio-review-summary">
      <div className="studio-row">
        {review.verdict ? (
          <Chip icon="review" mono>
            {t('map.inspector.reviewVerdict', { verdict: review.verdict })}
          </Chip>
        ) : null}
        {review.revisions > 0 ? (
          <Chip icon="refresh">
            {plural(i18n, 'map.inspector.reviewRevisions', review.revisions)}
          </Chip>
        ) : null}
        {REVIEW_LEVELS.filter((level) => (counts.get(level) ?? 0) > 0).map((level) => (
          <Chip
            key={level}
            tone={level === 'blocker' ? 'danger' : level === 'advisory' ? 'warn' : level === 'resolved' ? 'ok' : 'neutral'}
            icon={level === 'blocker' ? 'warn' : level === 'advisory' ? 'info' : level === 'resolved' ? 'check' : 'info'}
          >
            {`${t(`map.review.level.${level}`)} ${i18n.fmt.number(counts.get(level) ?? 0)}`}
          </Chip>
        ))}
      </div>
      {shown.length > 0 ? (
        <ul className="studio-finding-list" role="list">
          {shown.map((finding, index) => (
            <li key={`${finding.title}:${index}`} className="studio-finding" data-severity="blocking">
              <span className="studio-wrap-any">{finding.title}</span>
            </li>
          ))}
          {blockers.length > shown.length ? (
            <li className="studio-muted">{t('map.review.more', { n: i18n.fmt.number(blockers.length - shown.length) })}</li>
          ) : null}
        </ul>
      ) : null}
    </div>
  )
}
