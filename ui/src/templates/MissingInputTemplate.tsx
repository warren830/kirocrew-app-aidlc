/**
 * Something Studio needs from the user before anything can run.
 *
 * Not mocked in the visual spec (discrepancy #15), so it is derived from the budget layout: the exact
 * missing input, where Studio looked for it, what it blocks, and one control.
 *
 * Two cases arrive here and they are not the same shape (`projection.py:_missing_input_seeds`):
 *
 *  - **`missing_scope`** — the state file has no `Scope`. The user types one and Studio sends it to the
 *    canonical session; Studio does not write the state file (P-01), which is why the routing note is
 *    part of the template rather than a tooltip.
 *  - **`dangling_cursor` / `no_cursor`** — AI-DLC's active intent is missing or points at a directory that
 *    is gone. The fix is `pick_intent`, an admin-lane engine verb that needs an intent *directory*. The
 *    card carries only its own, so this template says where the real list is instead of building a picker
 *    out of one candidate: the cursor decides which intent every later dispatch moves.
 */

import { useI18n } from '../i18n'
import { at } from '../lib/format'
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
  InputBox,
  OfferedDecisions,
  inputKind,
  templateLabel,
  type TemplateProps,
} from './DecisionControls'

/** The reasons the projection mints for this card; anything else falls back to a generic sentence. */
const KNOWN_REASONS = ['missing_scope', 'dangling_cursor', 'no_cursor'] as const

export function MissingInputTemplate({ card, detail, draft, setDraft, api, go }: TemplateProps) {
  const i18n = useI18n()
  const { t } = i18n
  const advisor = useAdvisor(card, api, detail?.drafts)
  const rawReason = card.headline.params['reason']
  const reason = KNOWN_REASONS.find((known) => known === rawReason) ?? null
  const wantsText = card.decisions.some((spec) => spec.decision === 'provide_input')
  const needsCursor = card.decisions.some((spec) => spec.decision === 'pick_intent')
  const kind = inputKind(card)
  const cursor = card.headline.params['cursor']
  // `mtime_ns` is nanoseconds since the epoch; `Date` wants milliseconds.
  const stateMtime = card.evidence.state?.mtime_ns
    ? new Date(card.evidence.state.mtime_ns / 1e6).toISOString()
    : null

  return (
    <section className="studio-template" data-type={card.type} aria-label={templateLabel(i18n, card)}>
      <DecisionBrief card={card} />

      <Block title={t('template.missingInput.what')} icon="missingInput">
        <Brief>
          <p>{reason ? t(`template.missingInput.reason.${reason}`) : t('template.missingInput.reasonUnknown')}</p>
          <Consequence icon="lock" label={t('template.missingInput.blockedLabel')}>
            {t('template.missingInput.blocked')}
          </Consequence>
        </Brief>
      </Block>

      <Block title={t('template.missingInput.source')} icon="doc">
        <EvidenceGrid>
          {card.evidence.state ? (
            <Ev
              src={t('template.recovery.src.state')}
              icon="doc"
              value={card.evidence.state.relpath}
              sub={t('template.missingInput.stateSub', {
                stage: card.evidence.state.current_stage ?? t('common.unavailable'),
                at: at(i18n, stateMtime),
              })}
            />
          ) : null}
          <Ev
            src={t('template.missingInput.cursorSrc')}
            icon="intent"
            value={typeof cursor === 'string' && cursor ? cursor : t('common.none')}
            sub={t('template.missingInput.cursorSub', { space: card.space })}
            conflict={reason === 'dangling_cursor'}
          />
        </EvidenceGrid>
      </Block>

      {wantsText ? (
        <Block title={t(`template.missingInput.input.${kind}`)} icon="send">
          <InputBox
            card={card}
            draft={draft}
            setDraft={setDraft}
            label={t(`template.missingInput.input.${kind}`)}
            placeholder={t(`template.missingInput.placeholder.${kind}`)}
            routing={t('template.missingInput.routing')}
          />
        </Block>
      ) : null}

      {needsCursor ? (
        <Block title={t('template.missingInput.pickTitle')} icon="intent">
          <Brief>
            <p>{t('template.missingInput.pickBody')}</p>
            <span className="studio-row studio-qmetas">
              <Chip icon="lock">{t('template.missingInput.adminLane')}</Chip>
              <Chip mono>{card.space}</Chip>
            </span>
            <button
              type="button"
              className="studio-btn"
              onClick={() => go({ view: 'intents', repo: card.repo.repo_id, space: card.space })}
            >
              {t('template.missingInput.openIntents')}
            </button>
          </Brief>
        </Block>
      ) : null}

      <ConsistencyFindings card={card} title={t('template.missingInput.findings')} />
      <OfferedDecisions card={card} title={t('template.missingInput.choices')} />

      <AdvisorBlock card={card} advisor={advisor} />
    </section>
  )
}

export default MissingInputTemplate
