/**
 * The upgrade preview.
 *
 * Same transaction machinery as install (`PreviewFlow`), one difference that matters: an upgrade has a
 * version *pair*, and the pair is knowable from the repo record before the preview answers. Showing it
 * first means a user who opened this by mistake — because a newer engine is installed, or because the
 * installed version is already the bundled one — sees why the server is about to refuse instead of
 * reading a refusal with no context.
 *
 * The retirement list, the conflicts and their diffs, and the state-version refusal all live in
 * `PlanView`: they are properties of a plan, not of the verb that produced it, and a second copy here
 * would be a second chance to omit one.
 */

import { useI18n } from '../i18n'
import { plural } from '../lib/format'
import type { StudioApi } from '../lib/api'
import type { RepoInstall } from '../lib/types'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { Note } from './PreflightReport'
import { PreviewFlow } from './InstallPreview'

export interface UpgradePreviewProps {
  api: StudioApi
  repoId: string
  install: RepoInstall
  onStarted: (transactionId: string) => void
  onClose: () => void
}

export function UpgradePreview({ api, repoId, install, onStarted, onClose }: UpgradePreviewProps) {
  const i18n = useI18n()
  const { t } = i18n

  return (
    <PreviewFlow
      api={api}
      repoId={repoId}
      kind="upgrade"
      targetVersion={install.bundled_engine_version}
      onStarted={onStarted}
      onClose={onClose}
      intro={
        <>
          <div className="studio-chiplist">
            <Chip icon="install" mono title={t('install.version.from')}>
              {install.engine_version ?? t('install.version.notInstalled')}
            </Chip>
            <Icon name="chevron" size={13} />
            <Chip
              tone={install.newer_installed ? 'warn' : 'accent'}
              icon="install"
              mono
              title={t('install.version.to')}
            >
              {install.bundled_engine_version}
            </Chip>
            {install.drift_count > 0 ? (
              <Chip tone="warn" icon="warn">
                {plural(i18n, 'repos.drift', install.drift_count)}
              </Chip>
            ) : null}
          </div>
          {install.newer_installed ? <Note tone="warn">{t('repos.engine.newerInstalled')}</Note> : null}
          {/* Named up front rather than only in the plan: "what disappears" is the part of an upgrade a
              user is least able to reconstruct afterwards. */}
          <Note>{t('install.retire.body')}</Note>
        </>
      }
    />
  )
}
