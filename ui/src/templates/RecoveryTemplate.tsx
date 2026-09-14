/**
 * Recovery, and its twin: a delivery Studio cannot prove either way.
 *
 * This is the one template where the user is not judging AI-DLC's work but Studio's own account of what
 * happened, so it is built to make disagreement visible rather than resolvable:
 *
 *  - **Every source speaks for itself.** State, audit, directive, the KiroCrew session, Studio's own
 *    delivery record, the turn marker, the cursor read-back and Git each get a card, and a card is marked
 *    as conflicting only from data (a directive that does not match state, a delivery with no transcript
 *    row, a presence check that failed). Studio never picks a convenient source of truth — P-04.
 *  - **The three delivery checks are printed before any control.** `transcript_row`,
 *    `disk_baseline_unchanged` and `boot_id_unchanged` are what a resubmit would be betting against
 *    (§3.6), and a resubmit that was in fact delivered advances the stage twice.
 *  - **Nothing is preselected.** The offered decisions are three different claims about reality; this
 *    body explains each one and the bar sends exactly the one the user picks.
 */

import { useI18n } from '../i18n'
import { at, count } from '../lib/format'
import { isClosedAction } from '../lib/actionQueue'
import { EMPTY_ROUTE } from '../lib/route'
import type { DirectiveEvidence } from '../lib/types'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { AdvisorBlock, useAdvisor } from './AdvisorBlock'
import {
  Block,
  Brief,
  ConsistencyFindings,
  Consequence,
  DecisionBrief,
  Ev,
  EvidenceGrid,
  OfferedDecisions,
  templateLabel,
  type TemplateProps,
} from './DecisionControls'

/** Hashes are compared by eye, so they are shortened but never rounded or reformatted. */
function shortHash(value: string | null | undefined): string {
  return value ? value.slice(0, 12) : ''
}

/**
 * Which unit(s) the active-directive marker names, in the marker's own words.
 *
 * The card exists so the directive can contradict the state file out loud, and "which unit" is half of
 * what it claims. A v2 `invoke-swarm` marker leaves `unit` null and lists its units in `units`
 * (`aidlc_reader.parse_directive`), so reading `unit` alone printed the stage by itself and quietly
 * dropped the fan-out — the user would compare a unitless directive against a per-unit state row and
 * see no disagreement to explain. `unit` still wins when it is set: on that shape `units` is empty.
 */
function directiveUnits(directive: DirectiveEvidence): string {
  return directive.unit ?? (Array.isArray(directive.units) ? directive.units.join(', ') : '')
}

export function RecoveryTemplate({ card, detail, api, go }: TemplateProps) {
  const i18n = useI18n()
  const { t } = i18n
  const advisor = useAdvisor(card, api, detail?.drafts)
  const evidence = card.evidence
  const delivery = card.delivery
  const closed = isClosedAction(card)
  const codes = card.headline.params.codes
  const missingSession = (Array.isArray(codes) && codes.includes('session_lost_mid_stage'))
    || card.headline.params.reason === 'session_lost_mid_stage'
  const uncertain =
    card.status === 'DeliveryUncertain' ||
    card.status === 'ReconciliationRequired' ||
    delivery.outcome === 'uncertain'

  return (
    <section className="studio-template" data-type={card.type} aria-label={templateLabel(i18n, card)}>
      {uncertain ? (
        // The one assertive announcement in a decision body. PRD §10.3 reserves it for delivery
        // uncertainty, and this is the only state where a user could otherwise assume the send happened.
        <p className="studio-alert" role="alert">
          <Icon name="warn" size={15} />
          <span>{t('template.recovery.uncertainAlert')}</span>
        </p>
      ) : null}

      <DecisionBrief card={card} />

      {missingSession && !closed ? (
        <div className="studio-consequence">
          <Icon name="link" size={14} />
          <div>
            <p>{t('template.recovery.sessionRepair')}</p>
            <button type="button" className="studio-btn" onClick={() => go({
              ...EMPTY_ROUTE, view: 'intents', repo: card.repo.repo_id, intent: card.intent.intent_key,
            })}>
              {t('template.recovery.manageConversation')}
            </button>
          </div>
        </div>
      ) : null}

      <Block title={t('template.recovery.boundary')} icon="clock">
        <EvidenceGrid>
          <Ev
            src={t('template.recovery.boundarySrc')}
            icon="check"
            value={
              evidence.audit.boundary_event
                ? [evidence.audit.boundary_event.stage, evidence.audit.boundary_event.type]
                    .filter(Boolean)
                    .join(' · ')
                : (card.captured.boundary_token ?? t('common.unavailable'))
            }
            sub={
              evidence.audit.boundary_event
                ? t('template.recovery.boundarySub', { at: at(i18n, evidence.audit.boundary_event.ts) })
                : t('template.recovery.boundaryUnknown')
            }
            conflict={!evidence.audit.boundary_event && !card.captured.boundary_token}
          />
        </EvidenceGrid>
      </Block>

      <Block title={t('template.recovery.evidence')} icon="doc">
        <EvidenceGrid>
          {evidence.state ? (
            <Ev
              src={t('template.recovery.src.state')}
              icon="doc"
              value={`${evidence.state.current_stage ?? t('common.unavailable')} · ${shortHash(evidence.state.sha256)}`}
              sub={`${evidence.state.relpath} · ${t('template.recovery.revisions', { n: evidence.state.revision_count })}`}
              conflict={evidence.state.stable === false}
            />
          ) : null}

          <Ev
            src={t('template.recovery.src.audit')}
            icon="doc"
            value={
              evidence.audit.last_event
                ? `${evidence.audit.last_event.type} · ${at(i18n, evidence.audit.last_event.ts)}`
                : t('common.unavailable')
            }
            sub={t('template.recovery.auditSub', {
              shards: count(i18n, evidence.audit.shards?.length ?? null),
              complete: evidence.audit.complete
                ? t('template.recovery.complete')
                : t('template.recovery.incomplete'),
            })}
            conflict={evidence.audit.complete === false}
          />

          {evidence.directive ? (
            <Ev
              src={t('template.recovery.src.directive')}
              icon="gate"
              value={[evidence.directive.stage, directiveUnits(evidence.directive)].filter(Boolean).join(' · ')}
              sub={
                evidence.directive.matches_state
                  ? t('template.recovery.directiveMatches')
                  : t('template.recovery.directiveDiffers')
              }
              conflict={!evidence.directive.matches_state}
            />
          ) : null}

          {evidence.session ? (
            <Ev
              src={t('template.recovery.src.session')}
              icon="activity"
              value={evidence.session.slot_key}
              sub={missingSession ? t('template.recovery.sessionMissing') : t('template.recovery.sessionSub', {
                state: evidence.session.running
                  ? t('template.recovery.sessionRunning')
                  : t('template.recovery.sessionIdle'),
                queue: evidence.session.queue_depth,
                reasons: Array.isArray(evidence.session.busy_reasons)
                  ? evidence.session.busy_reasons.join(', ') || t('common.none') : t('common.unavailable'),
              })}
              conflict={missingSession && !closed}
            />
          ) : null}

          <Ev
            src={t('template.recovery.src.delivery')}
            icon="send"
            value={
              delivery.outcome
                ? t(`template.recovery.outcome.${delivery.outcome}`)
                : t(`enum.actionStatus.${card.status}`)
            }
            sub={t('template.recovery.deliverySub', {
              confirmed: delivery.delivery_confirmed ? t('template.recovery.yes') : t('template.recovery.no'),
              at: delivery.delivered_at ? at(i18n, delivery.delivered_at) : t('common.unavailable'),
            })}
            conflict={uncertain}
          />

          <Ev
            src={t('template.recovery.src.marker')}
            icon="clock"
            value={
              evidence.markers.turn_counter === null
                ? t('common.unavailable')
                : t('template.recovery.turnCounter', { n: evidence.markers.turn_counter })
            }
            sub={t('template.recovery.markerSub', {
              at: evidence.markers.human_turn_at
                ? at(i18n, evidence.markers.human_turn_at)
                : t('common.unavailable'),
              presence:
                !evidence.presence || evidence.presence.ok === null
                  ? t('template.recovery.presenceUnknown')
                  : evidence.presence.ok
                    ? t('template.recovery.presenceOk')
                    : t('template.recovery.presenceFailed'),
            })}
            conflict={evidence.presence?.ok === false}
          />

          {evidence.cursor_readback ? (
            <Ev
              src={t('template.recovery.src.cursor')}
              icon="intent"
              value={`${evidence.cursor_readback.space} · ${evidence.cursor_readback.dir_name}`}
              sub={
                evidence.cursor_readback.ok
                  ? t('template.recovery.cursorOk')
                  : t('template.recovery.cursorMismatch', {
                    fields: Array.isArray(evidence.cursor_readback.mismatch)
                      ? evidence.cursor_readback.mismatch.join(', ') : t('common.unavailable'),
                  })
              }
              conflict={!evidence.cursor_readback.ok}
            />
          ) : null}

          {evidence.git ? (
            <Ev
              src={t('template.recovery.src.git')}
              icon="git"
              value={`${evidence.git.branch ?? t('common.unavailable')} · ${shortHash(evidence.git.head)}`}
              sub={
                evidence.git.dirty
                  ? t('template.recovery.gitDirty', { n: evidence.git.dirty_files })
                  : t('template.recovery.gitClean')
              }
            />
          ) : null}
        </EvidenceGrid>
      </Block>

      {uncertain ? (
        <Block title={t('template.recovery.contradiction')} icon="warn">
          <Brief tone="danger">
            <p>{t('template.recovery.contradictionBody')}</p>
            <Consequence icon="warn" tone="warn">
              {t('template.recovery.contradictionChecks', {
                row: delivery.transcript_row
                  ? t('template.recovery.rowFound', { at: at(i18n, delivery.transcript_row.ts) })
                  : t('template.recovery.rowAbsent'),
                disk:
                  delivery.disk_baseline_unchanged === null
                    ? t('template.recovery.diskUnknown')
                    : delivery.disk_baseline_unchanged
                      ? t('template.recovery.diskUnchanged')
                      : t('template.recovery.diskChanged'),
                boot:
                  delivery.boot_id_unchanged === null
                    ? t('template.recovery.bootUnknown')
                    : delivery.boot_id_unchanged
                      ? t('template.recovery.bootSame')
                      : t('template.recovery.bootRestarted'),
              })}
            </Consequence>
          </Brief>
        </Block>
      ) : null}

      <ConsistencyFindings card={card} title={t('template.recovery.findings')} />

      <div className="studio-row studio-qmetas">
        {/* The risk class is the card's own classification of what applying a choice would touch. */}
        <Chip icon="lock">{t(`template.recovery.risk.${card.risk_class}`)}</Chip>
        <Chip mono>{card.action_id}</Chip>
      </div>
      <OfferedDecisions card={card} title={t('template.recovery.choices')} />

      <AdvisorBlock card={card} advisor={advisor} />
    </section>
  )
}

export default RecoveryTemplate
