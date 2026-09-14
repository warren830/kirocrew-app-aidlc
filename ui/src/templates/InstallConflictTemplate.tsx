/**
 * An install conflict: a file Studio's receipt owns was changed locally, so the upgrade stopped.
 *
 * This template offers no decision at all, and that is the design: `DECISIONS['install_conflict']` is
 * empty (`constants.py`), because the only two safe moves — read the receipt, run a fresh preview — live
 * on Repos behind the admin lease and the preview digest. P-06 says a complete old version beats a mixed
 * new one, so there is no "overwrite anyway", no "keep theirs" and no merge: an installer that resolved
 * this silently would break the receipt contract that makes recovery possible at all.
 *
 * The drift table is therefore the content: which path, who owns it, what the comparison found, and the
 * three hashes that prove it. Each row's diff opens as text — a managed-file diff is bytes being
 * compared, and a markdown pass would eat the leading `+`/`-`.
 */

import { useI18n } from '../i18n'
import { bytes } from '../lib/format'
import { parseDiff } from '../artifacts/ArtifactDiff'
import type { PreviewEntry } from '../lib/types'
import { Chip } from '../shell/Chip'
import { AdvisorBlock, useAdvisor } from './AdvisorBlock'
import {
  Block,
  Brief,
  Consequence,
  DecisionBrief,
  Ev,
  EvidenceGrid,
  StepList,
  templateLabel,
  type TemplateProps,
} from './DecisionControls'

/** A preview action counts as a conflict when the installer refuses to proceed on it. */
function isConflict(entry: PreviewEntry): boolean {
  return (
    entry.blocking ||
    entry.action === 'conflict' ||
    entry.action === 'owned_modified' ||
    entry.action === 'merge_conflict' ||
    entry.action === 'retire_blocked'
  )
}

function shortHash(value: string | null): string {
  return value ? value.slice(0, 12) : '—'
}

export function InstallConflictTemplate({ card, detail, api, go }: TemplateProps) {
  const i18n = useI18n()
  const { t } = i18n
  const advisor = useAdvisor(card, api, detail?.drafts)
  const install = card.install
  const drift = install?.drift ?? []
  const conflicts = drift.filter(isConflict)

  return (
    <section className="studio-template" data-type={card.type} aria-label={templateLabel(i18n, card)}>
      <DecisionBrief card={card} />

      <Block title={t('template.install.ownership')} icon="lock">
        <EvidenceGrid>
          <Ev
            src={t('template.install.installed')}
            icon="install"
            value={install?.engine_version ?? t('common.unavailable')}
            sub={
              install?.receipt_id
                ? t('template.install.receiptSub', { id: install.receipt_id })
                : t('template.install.noReceipt')
            }
            conflict={!install?.receipt_id}
          />
          <Ev
            src={t('template.install.bundled')}
            icon="doc"
            value={install?.bundled_engine_version ?? t('common.unavailable')}
            sub={t('template.install.bundledSub')}
          />
          <Ev
            src={t('template.install.managed')}
            icon="lock"
            value={t('template.install.managedValue', { n: drift.length, conflicts: conflicts.length })}
            sub={t('template.install.managedSub')}
            conflict={conflicts.length > 0}
          />
        </EvidenceGrid>
      </Block>

      <Block title={t('template.install.drift')} icon="git">
        {drift.length === 0 ? (
          <p className="studio-muted">{t('template.install.noDrift')}</p>
        ) : (
          <>
            <table className="studio-tbl">
              <caption className="studio-sr">{t('template.install.driftCaption')}</caption>
              <thead>
                <tr>
                  <th scope="col">{t('template.install.colPath')}</th>
                  <th scope="col">{t('template.install.colOwnership')}</th>
                  <th scope="col">{t('template.install.colState')}</th>
                  <th scope="col">{t('template.install.colHash')}</th>
                </tr>
              </thead>
              <tbody>
                {drift.map((entry) => (
                  <tr key={entry.path} data-conflict={String(isConflict(entry))}>
                    <td className="studio-mono studio-wrap-any">{entry.path}</td>
                    <td>{t(`enum.ownership.${entry.ownership}`)}</td>
                    <td>
                      {isConflict(entry) ? (
                        <Chip tone="danger" icon="warn">
                          {t(`template.install.action.${entry.action}`)}
                        </Chip>
                      ) : (
                        <Chip>{t(`template.install.action.${entry.action}`)}</Chip>
                      )}
                    </td>
                    <td className="studio-mono studio-wrap-any">
                      <span className="studio-hashline">
                        {t('template.install.hashLive', { hash: shortHash(entry.live_sha256) })}
                      </span>
                      <span className="studio-hashline">
                        {t('template.install.hashReceipt', { hash: shortHash(entry.receipt_sha256) })}
                      </span>
                      <span className="studio-hashline">
                        {t('template.install.hashPayload', { hash: shortHash(entry.payload_sha256) })}
                      </span>
                      {entry.size === null ? null : (
                        <span className="studio-hashline">{bytes(i18n, entry.size)}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {drift
              .filter((entry) => entry.diff)
              .map((entry) => (
                <details key={`diff-${entry.path}`} className="studio-details">
                  <summary>{t('template.install.showDiff', { path: entry.path })}</summary>
                  <div className="studio-difflines" aria-label={t('template.install.diffLabel', { path: entry.path })}>
                    {parseDiff(entry.diff ?? '').map((line, i) => (
                      <div key={i} className="studio-diffline" data-kind={line.kind}>
                        {line.text === '' ? ' ' : line.text}
                      </div>
                    ))}
                  </div>
                </details>
              ))}
          </>
        )}
      </Block>

      <Block title={t('template.install.why')} icon="warn">
        <Brief tone="danger">
          <p>{t('template.install.whyBody')}</p>
          <Consequence icon="lock" label={t('template.install.stoppedLabel')}>
            {t('template.install.stoppedBody')}
          </Consequence>
        </Brief>
      </Block>

      <Block title={t('template.install.remediation')} icon="check">
        <StepList
          steps={[
            t('template.install.fixRevert'),
            t('template.install.fixStay'),
            t('template.install.fixNoMerge'),
          ]}
        />
        <div className="studio-row studio-qmetas">
          <button
            type="button"
            className="studio-btn"
            onClick={() =>
              go({
                view: 'repos',
                repo: card.repo.repo_id,
                ...(install?.transaction_id ? { tx: install.transaction_id } : {}),
              })
            }
          >
            {t('template.install.openRepo')}
          </button>
        </div>
      </Block>

      <AdvisorBlock card={card} advisor={advisor} />
    </section>
  )
}

export default InstallConflictTemplate
