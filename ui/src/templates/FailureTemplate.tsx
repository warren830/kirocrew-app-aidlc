/**
 * A failure, and the breaker that stopped it repeating.
 *
 * PRD §9.5 asks for the normalized error, the fingerprint, the retry count with its backoff history, the
 * relevant log and the session state — exactly the set that lets a user choose between "retry, the cause
 * is gone" and "keep it paused, I have to fix something first".
 *
 * The excerpt is rendered as text, never through the markdown renderer: a stack trace full of underscores
 * and asterisks is not prose, and a renderer that italicised half of it would change what the user is
 * reading. The fingerprint is printed in full because it is the key the breaker counts on, and a
 * truncated hash cannot be compared with the last failure's.
 *
 * Nothing here retries by itself: `Retry now` is a decision in the bar, and it is the thing that resets
 * the breaker for this intent.
 */

import { useI18n } from '../i18n'
import { at, duration, ref } from '../lib/format'
import { Chip } from '../shell/Chip'
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
  Pane,
  templateLabel,
  type TemplateProps,
} from './DecisionControls'

export function FailureTemplate({ card, detail, api }: TemplateProps) {
  const i18n = useI18n()
  const { t } = i18n
  const advisor = useAdvisor(card, api, detail?.drafts)
  const failure = card.failure
  const session = card.evidence.session

  return (
    <section className="studio-template" data-type={card.type} aria-label={templateLabel(i18n, card)}>
      <DecisionBrief card={card} />

      <Block title={t('template.failure.normalized')} icon="fail">
        <Brief tone="danger">
          <p>
            {failure?.summary_key
              ? ref(i18n, { key: failure.summary_key, params: {} }, 'template.failure.summaryUnknown')
              : t('template.failure.summaryUnknown')}
          </p>
          {failure ? (
            <p className="studio-fingerprint studio-mono studio-wrap-any">
              {t('template.failure.fingerprint', {
                fingerprint: failure.fingerprint,
                cls: failure.fingerprint_class ?? t('common.unavailable'),
              })}
            </p>
          ) : null}
          <span className="studio-row">
            {failure?.breaker_open ? (
              <Chip tone="danger" icon="warn">
                {t('template.failure.breakerOpen')}
              </Chip>
            ) : (
              <Chip icon="clock">{t('template.failure.breakerClosed')}</Chip>
            )}
            <Chip mono>{t('template.failure.count', { n: failure?.count ?? 0 })}</Chip>
          </span>
        </Brief>
      </Block>

      {failure && failure.history.length > 0 ? (
        <Block title={t('template.failure.history')} icon="clock">
          <table className="studio-tbl">
            <caption className="studio-sr">{t('template.failure.historyCaption')}</caption>
            <thead>
              <tr>
                <th scope="col">{t('template.failure.colAttempt')}</th>
                <th scope="col">{t('template.failure.colAt')}</th>
                <th scope="col">{t('template.failure.colBackoff')}</th>
                <th scope="col">{t('template.failure.colOutcome')}</th>
              </tr>
            </thead>
            <tbody>
              {failure.history.map((entry, i) => (
                <tr key={`${entry.at}-${i}`}>
                  <td className="studio-mono">{i + 1}</td>
                  <td>{at(i18n, entry.at)}</td>
                  <td className="studio-mono">{duration(i18n, entry.backoff_secs)}</td>
                  {/* The outcome token is the transport's own word for what happened. */}
                  <td className="studio-mono studio-wrap-any">{entry.outcome}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Block>
      ) : null}

      {failure?.stderr_excerpt ? (
        <Block title={t('template.failure.log')} icon="activity">
          <Pane title={t('template.failure.logPane')} icon="activity">
            <pre className="studio-log studio-mono studio-wrap-any">{failure.stderr_excerpt}</pre>
          </Pane>
          <Consequence icon="lock">{t('template.failure.logNote')}</Consequence>
        </Block>
      ) : null}

      {session ? (
        <Block title={t('template.failure.session')} icon="activity">
          <EvidenceGrid>
            <Ev
              src={t('template.recovery.src.session')}
              icon="activity"
              value={session.slot_key}
              sub={t('template.failure.sessionSub', {
                state: session.running ? t('template.recovery.sessionRunning') : t('template.recovery.sessionIdle'),
                stop: session.stop_state,
                turn: session.last_turn_ts ? at(i18n, session.last_turn_ts) : t('common.unavailable'),
              })}
            />
          </EvidenceGrid>
        </Block>
      ) : null}

      <ConsistencyFindings card={card} title={t('template.failure.findings')} />
      <OfferedDecisions card={card} title={t('template.failure.choices')} />

      <AdvisorBlock card={card} advisor={advisor} />
    </section>
  )
}

export default FailureTemplate
