/**
 * Prototype migration (FR-MIG-001…005).
 *
 * It is a section rather than a route because the route contract has no `migration` view (§3.2) and because
 * FR-MIG-001 wants the preview *before* Studio is used, not on a page the user has to find. Settings mounts
 * it at the top; it renders nothing at all when there is no prototype and no migration on record, so it
 * costs a reader nothing in the normal case.
 *
 * Three properties the code enforces rather than describes:
 *
 *  1. **Apply only what was previewed.** `POST /migration/apply` echoes `source_sha256` from the preview.
 *     If the prototype registry changed in between, the backend answers `bad_body` and Studio re-reads the
 *     preview and says so, instead of applying a plan the user never saw. A preview without a digest
 *     disables the button entirely: an unverifiable migration is not applied.
 *  2. **Once.** The button disappears the moment the backend reports the migration applied; there is no
 *     "run again", because a second run would re-register rows under new ids.
 *  3. **Never two control surfaces (FR-MIG-005).** While `aidlc-console` is installed *and* enabled and the
 *     migration has not been applied, the section leads with that fact and with the two KiroCrew steps that
 *     end it. Studio does not disable or uninstall Apps itself — it has no seam for it and should not — so
 *     the last word is a link to the App's own KiroCrew page.
 */

import { useCallback, useState } from 'react'
import { Btn } from '@kirocrew/app-sdk/ui'
import { useNavigate } from '@kirocrew/app-sdk'

import { useI18n, type I18n } from '../i18n'
import { decodeError, useStudioApi } from '../lib/api'
import { at as formatAt } from '../lib/format'
import type { MigrationPreview, MigrationResult, MigrationStatusResponse } from '../lib/types'
import { useResource } from '../lib/useResource'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'

/** The host page that owns enable/disable/uninstall for one App (`03 §4.1`, route `/apps/detail/:name`). */
const CONSOLE_DETAIL_PATH = '/apps/detail/aidlc-console'

/**
 * True while both Apps could act on the same repositories.
 *
 * Exported so any surface that wants to hold back a control until the overlap is resolved can ask one
 * question instead of re-deriving the rule from three booleans.
 */
export function consoleOverlapActive(status: MigrationStatusResponse | null): boolean {
  if (!status) return false
  return status.console.installed && status.console.enabled && !status.applied
}

/** `duplicate_of:r_1` → the translated sentence; an unrecognised code is reported verbatim. */
function resolutionText(
  t: (key: string, params?: Record<string, string | number>) => string,
  raw: string,
): string {
  if (raw === 'migrate') return t('migration.resolution.migrate')
  if (raw === 'unavailable') return t('migration.resolution.unavailable')
  const [head, tail] = raw.split(':', 2)
  if (head === 'duplicate_of') return t('migration.resolution.duplicate', { of: tail ?? '' })
  if (head === 'already_registered') return t('migration.resolution.already_registered', { of: tail ?? '' })
  return t('migration.resolution.other', { raw })
}

/** A thrown API failure as a sentence: the catalogued message for a known code, the server's prose else. */
function errorText(i18n: I18n, caught: unknown): string {
  const decoded = decodeError(caught)
  return i18n.has(`errors.${decoded.code}`) ? i18n.t(`errors.${decoded.code}`) : decoded.message
}

export interface MigrationViewProps {
  /** Called after a successful apply so the surrounding page can re-read the repo registry. */
  onApplied?: () => void
}

export function MigrationView({ onApplied }: MigrationViewProps) {
  const i18n = useI18n()
  const { t } = i18n
  const api = useStudioApi()
  const navigate = useNavigate()

  const status = useResource<MigrationStatusResponse>(
    'migration-status',
    useCallback((signal) => api.migrationStatus({ signal }), [api]),
    { interval: 0, revalidateOn: ['migration.updated', 'reset'] },
  )

  const [preview, setPreview] = useState<MigrationPreview | null>(null)
  const [applied, setApplied] = useState<MigrationResult | null>(null)
  const [busy, setBusy] = useState<'preview' | 'apply' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [stale, setStale] = useState(false)

  const readPreview = useCallback(async (): Promise<MigrationPreview | null> => {
    setBusy('preview')
    setError(null)
    try {
      const response = await api.migrationPreview()
      setPreview(response.preview)
      return response.preview
    } catch (caught) {
      setError(i18n.t('migration.previewFailed', { message: errorText(i18n, caught) }))
      return null
    } finally {
      setBusy(null)
    }
  }, [api, i18n])

  const apply = async () => {
    const digest = preview?.source_sha256
    if (!digest) return
    setBusy('apply')
    setError(null)
    setStale(false)
    try {
      const response = await api.migrationApply(digest)
      setApplied(response.result)
      setPreview(null)
      await status.refresh()
      onApplied?.()
    } catch (caught) {
      const decoded = decodeError(caught)
      // `bad_body` on this route means one thing: the digest no longer matches, so the plan on screen is
      // not the plan that would run. Re-read it and say so rather than reporting a generic bad request.
      if (decoded.code === 'bad_body') {
        setStale(true)
        await readPreview()
      } else {
        setError(t('migration.failed', { message: errorText(i18n, caught) }))
      }
    } finally {
      setBusy(null)
    }
  }

  const data = status.data
  const result = applied ?? data?.result ?? null
  const isApplied = Boolean(applied) || Boolean(data?.applied)

  // Nothing to say: no prototype on disk, nothing previewable, and no migration on record. Rendering an
  // empty "Migration" heading on every Settings visit would be noise for the 99% of installs with no
  // prototype at all.
  if (!data || (!data.console.installed && !data.preview_available && !isApplied)) return null

  const overlap = consoleOverlapActive(data)
  const consoleState = !data.console.installed
    ? t('migration.consoleState.absent')
    : data.console.enabled
      ? t('migration.consoleState.enabled')
      : t('migration.consoleState.disabled')

  return (
    <section className="studio-block studio-migration" aria-label={t('migration.region')}>
      <h3>{t('migration.title')}</h3>

      {overlap ? (
        // Polite, not assertive: PRD §10.3 reserves assertive for errors and delivery uncertainty. This is
        // a standing condition the user resolves in KiroCrew, and it is the first thing on the section.
        <div className="studio-banner" data-tone="danger" role="status">
          <Icon name="warn" size={15} />
          <div className="studio-grow">
            <strong>{t('migration.both.title')}</strong>
            <p>{t('migration.both.body')}</p>
          </div>
        </div>
      ) : null}

      <p className="studio-lede">{t('migration.lede')}</p>
      <div className="studio-row studio-wrapchips">
        <Chip icon="install">{`${t('migration.consoleState.title')}: ${consoleState}`}</Chip>
      </div>

      {isApplied ? (
        <>
          <h4 className="studio-subhead">{t('migration.applied.title')}</h4>
          {result ? (
            <dl className="studio-evgrid">
              <div className="studio-evgrid-pair">
                <dt>{t('migration.applied.status')}</dt>
                {/* `status` is a wire word (`applied` | `failed` | `rolled_back`): shown verbatim. */}
                <dd className="studio-mono">{result.status}</dd>
              </div>
              <div className="studio-evgrid-pair">
                <dt>{t('migration.applied.at')}</dt>
                <dd className="studio-mono">{formatAt(i18n, result.applied_at)}</dd>
              </div>
              <div className="studio-evgrid-pair">
                <dt>{t('migration.applied.summary')}</dt>
                <dd>
                  <dl className="studio-evgrid">
                    {Object.entries(result.summary).map(([name, value]) => (
                      <div key={name} className="studio-evgrid-pair">
                        {/* Backend field names, not prose: verbatim in both locales. */}
                        <dt className="studio-mono">{name}</dt>
                        <dd className="studio-mono studio-wrap-any">
                          {typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value)}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </dd>
              </div>
            </dl>
          ) : null}
          <p className="studio-consequence">
            <Icon name="lock" size={13} />{' '}
            {result?.backup_path
              ? t('migration.applied.backup', { path: result.backup_path })
              : t('migration.applied.noBackup')}
          </p>

          <h4 className="studio-subhead">{t('migration.nextSteps.title')}</h4>
          <p className="studio-consequence">{t('migration.nextSteps.desc')}</p>
          <ul className="studio-list">
            {(result?.next_steps ?? []).map((step) => (
              <li key={step}>
                {i18n.has(`migration.nextSteps.${step}`)
                  ? t(`migration.nextSteps.${step}`)
                  : t('migration.nextSteps.other', { step })}
              </li>
            ))}
          </ul>
          <div className="studio-row studio-wrapchips">
            <Btn type="button" onClick={() => navigate(CONSOLE_DETAIL_PATH)}>
              <Icon name="external" size={13} />
              {t('migration.openConsole')}
            </Btn>
          </div>
        </>
      ) : (
        <>
          <div className="studio-migration-explain">
            <div>
              <h4 className="studio-subhead">{t('migration.moves.title')}</h4>
              <ul className="studio-list">
                <li>{t('migration.moves.registry')}</li>
                <li>{t('migration.moves.archive')}</li>
                <li>{t('migration.moves.ids')}</li>
              </ul>
            </div>
            <div>
              <h4 className="studio-subhead">{t('migration.keeps.title')}</h4>
              <ul className="studio-list">
                <li>{t('migration.keeps.secret')}</li>
                <li>{t('migration.keeps.repoData')}</li>
                <li>{t('migration.keeps.credentials')}</li>
                <li>{t('migration.keeps.actions')}</li>
              </ul>
            </div>
          </div>

          <div className="studio-row studio-wrapchips">
            <Btn type="button" onClick={() => void readPreview()} disabled={busy !== null}>
              <Icon name="review" size={13} />
              {busy === 'preview'
                ? t('migration.previewing')
                : preview
                  ? t('migration.previewAgain')
                  : t('migration.preview')}
            </Btn>
            <span className="studio-muted">{t('migration.previewNothing')}</span>
          </div>

          {/* Polite: this reports the outcome of a read the user just asked for. */}
          <div role="status" aria-live="polite">
            {stale ? (
              <p className="studio-error">{t('migration.stale')}</p>
            ) : null}
            {error ? <p className="studio-error">{error}</p> : null}
          </div>

          {preview ? (
            <>
              <dl className="studio-evgrid">
                <div className="studio-evgrid-pair">
                  <dt>{t('migration.source')}</dt>
                  <dd className="studio-mono studio-wrap-any">{preview.source_path}</dd>
                </div>
                <div className="studio-evgrid-pair">
                  <dt>{t('migration.sourceDigest')}</dt>
                  <dd className="studio-mono studio-wrap-any">
                    {preview.source_sha256 ?? t('common.unavailable')}
                  </dd>
                </div>
              </dl>

              {preview.applicable ? null : (
                <div className="studio-banner" data-tone="warn" role="status">
                  <Icon name="info" size={15} />
                  <div className="studio-grow">
                    <strong>{t('migration.notApplicable.title')}</strong>
                    <p>
                      {preview.reason && i18n.has(`migration.notApplicable.${preview.reason}`)
                        ? t(`migration.notApplicable.${preview.reason}`)
                        : t('migration.notApplicable.other', {
                            reason: preview.reason ?? t('common.unavailable'),
                          })}
                    </p>
                  </div>
                </div>
              )}

              <h4 className="studio-subhead">{t('migration.rows.title')}</h4>
              {preview.rows.length === 0 ? (
                <p className="studio-consequence">{t('migration.rows.none')}</p>
              ) : (
                <table className="studio-tbl">
                  <thead>
                    <tr>
                      <th scope="col">{t('migration.rows.label')}</th>
                      <th scope="col">{t('migration.rows.path')}</th>
                      <th scope="col">{t('migration.rows.added')}</th>
                      <th scope="col">{t('migration.rows.resolution')}</th>
                      <th scope="col">{t('migration.rows.detail')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {preview.rows.map((row) => (
                      <tr key={row.legacy_id}>
                        <td>
                          <span className="studio-strong">{row.label}</span>
                          <div className="studio-mono studio-muted studio-small">{row.legacy_id}</div>
                        </td>
                        <td className="studio-mono studio-wrap-any">{row.path}</td>
                        <td className="studio-mono">{row.added_at ? formatAt(i18n, row.added_at) : t('common.unavailable')}</td>
                        <td>{resolutionText(t, row.resolution)}</td>
                        <td className="studio-wrap-any">{row.error ?? t('common.none')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              <p className="studio-consequence">
                {t('migration.counts', {
                  rowsIn: i18n.fmt.number(preview.rows_in),
                  rowsOut: i18n.fmt.number(preview.rows_out),
                })}
              </p>

              <div className="studio-row studio-wrapchips">
                <Btn
                  primary
                  type="button"
                  onClick={() => void apply()}
                  disabled={busy !== null || !preview.applicable || !preview.source_sha256}
                >
                  <Icon name="check" size={13} />
                  {busy === 'apply' ? t('migration.applying') : t('migration.apply')}
                </Btn>
              </div>
              <p className="studio-consequence">
                {preview.source_sha256 ? t('migration.applyHint') : t('migration.applyBlocked')}
              </p>
            </>
          ) : (
            <p className="studio-consequence">{t('migration.applyNeedsPreview')}</p>
          )}
        </>
      )}
    </section>
  )
}

export default MigrationView
