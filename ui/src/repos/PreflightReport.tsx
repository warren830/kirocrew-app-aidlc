/**
 * The read-only preflight, and the small vocabulary the rest of this area is built from.
 *
 * The preflight is the whole point of manual registration: before a single byte is written, Studio says
 * what it found at the path the user typed — the resolved path, whether the identity is provable,
 * whether an AI-DLC harness is already there, whether a symlink sits where it would write. `can_register`
 * and `can_install` are the server's two verdicts and this component never second-guesses them: a UI
 * that computed its own "looks fine to me" would enable a button the backend then refuses.
 *
 * The generic pieces (`Block`, `Facts`, `Note`, `ErrorNote`, `findingText`, `errorText`) live here rather
 * than in a shared module because this area ships no shared module, and this is the file the rest of the
 * area sits downstream of. Nothing in the area imports back up into a parent, so the graph stays acyclic.
 */

import type { ReactNode } from 'react'

import { useI18n, type I18n } from '../i18n'
import type { StudioApiError } from '../lib/api'
import { bytes as fmtBytes, plural, refParams, unavailable } from '../lib/format'
import { Chip, type ChipTone } from '../shell/Chip'
import { Icon, type IconName } from '../shell/Icon'
import type { Finding, FindingSeverity, HarnessDir, PreflightReport } from '../lib/types'

// --------------------------------------------------------------------------- //
// shared primitives
// --------------------------------------------------------------------------- //

/** A titled section, matching the mockup's `B(title, icon, inner)` block primitive (visual spec §5.2). */
export function Block({
  title,
  icon,
  children,
  id,
}: {
  title: string
  icon: IconName
  children: ReactNode
  id?: string
}) {
  return (
    <section className="studio-block" {...(id ? { id } : {})}>
      <h3>
        <Icon name={icon} size={13} />
        {title}
      </h3>
      {children}
    </section>
  )
}

export interface Fact {
  key: string
  label: string
  /** `null` renders as the unavailable string, never as an empty cell or a zero. */
  value: ReactNode
  mono?: boolean
}

/**
 * A label/value grid.
 *
 * A real `<dl>` rather than a two-column table: these are name/value pairs, not tabular data, and a
 * screen reader reading "row 4, column 2" for a canonical path is worse than reading the term.
 */
export function Facts({ rows }: { rows: Fact[] }) {
  const i18n = useI18n()
  return (
    <dl className="studio-facts">
      {rows.map((row) => (
        <div key={row.key} className="studio-fact">
          <dt>{row.label}</dt>
          <dd className={row.mono ? 'studio-mono studio-wrap-any' : undefined}>
            {row.value === null || row.value === undefined || row.value === '' ? unavailable(i18n) : row.value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

/** A quiet paragraph that says what Studio does or does not do. `tone` is paired with an icon. */
export function Note({ children, tone }: { children: ReactNode; tone?: 'danger' | 'warn' }) {
  return (
    <p className="studio-note" {...(tone ? { 'data-tone': tone } : {})}>
      <Icon name={tone ? 'warn' : 'info'} size={13} /> {children}
    </p>
  )
}

/**
 * Turn a `StudioApiError` into words.
 *
 * `errors.<code>` is the catalogue's own sentence for every backend code, so a known refusal reads as
 * a sentence the user can act on. An unknown code falls back to the server's prose rather than to a
 * generic apology, because the prose is the only information left at that point.
 */
export function errorText(i18n: I18n, error: StudioApiError): string {
  const key = `errors.${error.code}`
  return i18n.has(key) ? i18n.t(key) : error.message
}

/** An error, with the reassurance that goes with a refused mutation. */
export function ErrorNote({ error, reassure }: { error: StudioApiError; reassure?: string }) {
  const i18n = useI18n()
  return (
    // Assertive would be wrong here: a refused button is a synchronous answer to something the user just
    // pressed, and the focus is already on the control beside it (PRD §10.3 "assertive sparingly").
    <p className="studio-failure-detail" role="status">
      <Icon name="warn" size={13} /> {errorText(i18n, error)}
      {reassure ? ` ${reassure}` : ''}
    </p>
  )
}

/**
 * A backend `Finding`'s message.
 *
 * `message_key` is a `finding.<code>` reference the backend chose; no area owns that namespace yet, so
 * a missing key falls back to naming the code. Showing the raw key (what `t()` does by default) would
 * put `finding.mixed_harness_versions` in front of a user as if it were a sentence.
 */
export function findingText(i18n: I18n, finding: Finding): string {
  if (i18n.has(finding.message_key)) {
    return i18n.t(finding.message_key, refParams(i18n, { key: finding.message_key, params: finding.params }))
  }
  return i18n.t('repos.finding.fallback', { code: finding.code })
}

const FINDING_TONE: Record<FindingSeverity, ChipTone> = {
  blocking: 'danger',
  warn: 'warn',
  info: 'info',
}

/** Findings as a list: severity is a left rule plus an icon plus the word, never colour alone. */
export function FindingList({ findings }: { findings: Finding[] }) {
  const i18n = useI18n()
  const { t } = i18n
  return (
    <ul className="studio-plain-list">
      {findings.map((finding, index) => (
        <li
          className="studio-finding"
          data-severity={finding.severity}
          key={`${finding.code}-${index}`}
        >
          <div className="studio-row">
            <Chip
              tone={FINDING_TONE[finding.severity]}
              icon={finding.severity === 'blocking' ? 'recovery' : finding.severity === 'warn' ? 'warn' : 'info'}
            >
              {t(`enum.findingSeverity.${finding.severity}`)}
            </Chip>
            <span className="studio-mono studio-muted">{finding.code}</span>
          </div>
          <p className="studio-finding-text">{findingText(i18n, finding)}</p>
          {finding.evidence.length > 0 ? (
            <p className="studio-subpath">
              {finding.evidence.map((ref) => `${ref.kind}: ${ref.ref}`).join('  ·  ')}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  )
}

/** A harness directory as one chip row: the dir, its engine version, and whether it can be driven. */
export function HarnessList({ dirs }: { dirs: HarnessDir[] }) {
  const i18n = useI18n()
  const { t } = i18n
  return (
    <ul className="studio-plain-list">
      {dirs.map((dir) => (
        <li className="studio-row studio-chiplist" key={dir.dir}>
          <span className="studio-mono">{dir.dir}</span>
          <Chip mono icon="install">
            {dir.engine_version ?? unavailable(i18n)}
          </Chip>
          {dir.engine_state_version === null ? null : (
            <Chip mono>{t('repos.engine.stateVersion', { version: dir.engine_state_version })}</Chip>
          )}
          {dir.stage_count === null ? null : (
            <Chip mono>{plural(i18n, 'repos.engine.stages', dir.stage_count)}</Chip>
          )}
          <Chip tone={dir.has_utility ? 'ok' : 'warn'} icon={dir.has_utility ? 'check' : 'warn'}>
            {t(dir.has_utility ? 'repos.card.harness.utility' : 'repos.card.harness.noUtility')}
          </Chip>
        </li>
      ))}
    </ul>
  )
}

// --------------------------------------------------------------------------- //
// the preflight itself
// --------------------------------------------------------------------------- //

export interface PreflightReportViewProps {
  report: PreflightReport
  /** The registered repo that already owns this identity, for the refusal copy. */
  duplicateLabel?: string | null
  /** Rendered inside the block, under the verdicts (the "open the other repo" link). */
  children?: ReactNode
}

export function PreflightReportView({ report, duplicateLabel, children }: PreflightReportViewProps) {
  const i18n = useI18n()
  const { t } = i18n

  const rows: Fact[] = [
    { key: 'input', label: t('repos.preflight.input'), value: report.path_input, mono: true },
    { key: 'canonical', label: t('repos.preflight.canonical'), value: report.canonical_path, mono: true },
    {
      key: 'identity',
      label: t('repos.preflight.identity'),
      value: report.identity ? (
        <>
          <span className="studio-mono studio-wrap-any">{report.identity.identity_str}</span>
          <span className="studio-chiplist">
            <Chip
              tone={report.identity.provable ? 'ok' : 'warn'}
              icon={report.identity.provable ? 'check' : 'warn'}
            >
              {t(
                report.identity.provable
                  ? 'repos.preflight.identityProvable'
                  : 'repos.preflight.identityUnprovable',
              )}
            </Chip>
            {report.identity.st_dev === null || report.identity.st_ino === null ? null : (
              <Chip mono>
                {t('repos.preflight.inode', {
                  dev: report.identity.st_dev,
                  ino: report.identity.st_ino,
                })}
              </Chip>
            )}
            {report.identity.git_common_dir ? (
              <Chip mono icon="git" title={t('repos.card.identity.gitCommonDir')}>
                {report.identity.git_common_dir}
              </Chip>
            ) : null}
          </span>
        </>
      ) : null,
    },
    { key: 'platform', label: t('repos.card.identity.platform'), value: report.platform, mono: true },
    {
      key: 'git',
      label: t('repos.preflight.git'),
      value: report.git ? (
        <span className="studio-chiplist">
          <Chip icon="git">
            {report.git.is_repo
              ? t('repos.preflight.gitRepo', { branch: report.git.branch ?? unavailable(i18n) })
              : t('repos.preflight.gitNotRepo')}
          </Chip>
          {report.git.is_repo ? (
            <Chip tone={report.git.dirty ? 'warn' : 'ok'} icon={report.git.dirty ? 'warn' : 'check'}>
              {t(report.git.dirty ? 'repos.preflight.gitDirty' : 'repos.preflight.gitClean')}
            </Chip>
          ) : null}
        </span>
      ) : null,
    },
    {
      key: 'bun',
      label: t('repos.preflight.bun'),
      value: report.bun ? (
        report.bun.found ? (
          <Chip tone="ok" icon="check" mono>
            {t('repos.preflight.bunFound', {
              version: report.bun.version ?? unavailable(i18n),
              path: report.bun.path ?? unavailable(i18n),
            })}
          </Chip>
        ) : (
          // Three lines, not one chip, because "not found" is only the first third of the truth. The
          // desktop gateway is started by launchd with a four-entry PATH (A28), so the case that
          // actually happens is a working `~/.bun` or Homebrew install that a PATH-only lookup cannot
          // see — and the old copy told that user bun was "not installed". The searched list comes from
          // the probe rather than from this catalogue so it cannot drift from where the backend looked.
          // A fragment, not a flex column: the chip must keep hugging its own text the way every other
          // value chip in this grid does, and `.studio-facts dd` is a plain block, so the help line and
          // the subpath already stack under it.
          <>
            <Chip tone="warn" icon="warn">
              {t('repos.preflight.bunMissing')}
            </Chip>
            <p className="studio-help">{t('errors.bun_missing')}</p>
            {report.bun.searched.length > 0 ? (
              <span className="studio-subpath">
                {t('errors.bun_missing_searched', { locations: report.bun.searched.join(t('shell.format.listJoin')) })}
              </span>
            ) : null}
          </>
        )
      ) : null,
    },
    {
      key: 'harness',
      label: t('repos.preflight.harness'),
      value:
        report.harness_dirs.length === 0 ? (
          <span className="studio-muted">{t('repos.preflight.none')}</span>
        ) : (
          <HarnessList dirs={report.harness_dirs} />
        ),
    },
    {
      key: 'aidlc',
      label: t('repos.preflight.aidlc'),
      value: (
        <span className="studio-chiplist">
          <Chip icon="doc">
            {t(
              report.aidlc.layout === 'spaces'
                ? 'repos.preflight.layoutSpaces'
                : report.aidlc.layout === 'legacy'
                  ? 'repos.preflight.layoutLegacy'
                  : 'repos.preflight.layoutNone',
            )}
          </Chip>
          {report.aidlc.layout === null ? null : (
            <>
              <Chip mono>{plural(i18n, 'repos.preflight.spaces', report.aidlc.spaces.length)}</Chip>
              <Chip mono>{plural(i18n, 'repos.preflight.intents', report.aidlc.intents)}</Chip>
            </>
          )}
          {report.aidlc.state_versions.length > 0 ? (
            <Chip mono icon="lock">
              {t('repos.preflight.stateVersions', {
                versions: report.aidlc.state_versions.join(i18n.t('shell.format.listJoin')),
              })}
            </Chip>
          ) : null}
        </span>
      ),
    },
    {
      key: 'symlinks',
      label: t('repos.preflight.symlinks'),
      value:
        report.symlinks_at_managed_paths.length === 0 ? (
          <span className="studio-muted">{t('repos.preflight.none')}</span>
        ) : (
          <>
            <ul className="studio-plain-list studio-mono">
              {report.symlinks_at_managed_paths.map((path) => (
                <li key={path}>{path}</li>
              ))}
            </ul>
            <Note tone="danger">{t('repos.preflight.symlinksBody')}</Note>
          </>
        ),
    },
    { key: 'space', label: t('repos.preflight.freeSpace'), value: fmtBytes(i18n, report.free_space_bytes) },
    {
      key: 'writable',
      label: t('repos.preflight.writable'),
      value:
        report.writable === null ? null : (
          <Chip tone={report.writable ? 'ok' : 'danger'} icon={report.writable ? 'check' : 'warn'}>
            {t(report.writable ? 'repos.preflight.writable' : 'repos.preflight.notWritable')}
          </Chip>
        ),
    },
    {
      key: 'receipt',
      label: t('repos.preflight.receipt'),
      value: report.existing_receipt ? (
        <span className="studio-chiplist">
          <Chip mono icon="doc">
            {report.existing_receipt.engine_version}
          </Chip>
          <Chip mono>{report.existing_receipt.receipt_id}</Chip>
          <Chip mono>{plural(i18n, 'repos.card.receipt.files', report.existing_receipt.files)}</Chip>
        </span>
      ) : null,
    },
  ]

  return (
    <Block title={t('repos.preflight.title')} icon="search">
      <div className="studio-chiplist">
        <Chip
          tone={report.can_register ? 'ok' : 'danger'}
          icon={report.can_register ? 'check' : 'warn'}
        >
          {t(report.can_register ? 'repos.preflight.canRegister' : 'repos.preflight.cannotRegister')}
        </Chip>
        <Chip tone={report.can_install ? 'ok' : 'warn'} icon={report.can_install ? 'check' : 'warn'}>
          {t(report.can_install ? 'repos.preflight.canInstall' : 'repos.preflight.cannotInstall')}
        </Chip>
      </div>

      {report.is_directory ? null : <Note tone="danger">{t('repos.preflight.notDirectory')}</Note>}
      {report.sensitive ? <Note tone="danger">{t('repos.preflight.sensitive')}</Note> : null}
      {report.duplicate_of ? (
        <div className="studio-failure-detail">
          <strong>{t('repos.add.duplicate.title')}</strong>{' '}
          {t('repos.add.duplicate.body', { label: duplicateLabel ?? report.duplicate_of })}
        </div>
      ) : null}

      {children}

      <Facts rows={rows} />

      {report.warnings.length > 0 ? (
        <>
          <h4 className="studio-subhead">{t('repos.preflight.warnings')}</h4>
          <FindingList findings={report.warnings} />
        </>
      ) : null}

      <Note>{t('repos.preflight.readOnly')}</Note>
    </Block>
  )
}
