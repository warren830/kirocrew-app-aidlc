/**
 * The scope bar: which repositories you are looking at, where you are inside them, and what is running.
 *
 * `All repos` is a virtual namespace over the explicit registry — never a filesystem scan (FR-REP-008),
 * which is why the count says "{n} registered" rather than "{n} found", and why the idle breadcrumb
 * says so in words.
 *
 * DEVIATION from the mockup (visual spec §1.3): the mockup's repo picker is a `button
 * aria-haspopup="listbox"` whose click handler cycles through the repos (an explicit stub for a
 * popover). This ships a native `<select>` instead. A hand-rolled listbox needs focus trapping, typeahead
 * and Escape handling to be usable, the host exports no popover primitive to inherit them from, and the
 * native control is the one that already works with a screen reader and at a 390px touch target.
 */

import type { ReactNode } from 'react'

import { useI18n } from '../i18n'
import { plural } from '../lib/format'
import { DEFAULT_SPACE, type Navigate, type StudioRoute } from '../lib/route'
import type { RepoRecord } from '../lib/types'
import { Icon } from './Icon'

export interface ScopeBarProps {
  route: StudioRoute
  go: Navigate
  repos: RepoRecord[]
  /** `null` until `/repos` has answered once; renders as unavailable rather than as zero. */
  registered: number | null
  unavailable: number | null
  /** The execution strip, rendered at the end of the bar. */
  children?: ReactNode
}

export function ScopeBar({ route, go, repos, registered, unavailable, children }: ScopeBarProps) {
  const i18n = useI18n()
  const { t } = i18n
  const selected = repos.find((repo) => repo.repo_id === route.repo) ?? null

  return (
    <div className="studio-scopebar">
      <label className="studio-scope-select">
        <Icon name="repo" size={13} />
        <span className="studio-sr">{t('scope.selectLabel')}</span>
        <select
          value={selected ? selected.repo_id : ''}
          onChange={(event) => {
            // Changing scope drops the selection: an action from another repository is not in this
            // scope, and keeping it selected would show a decision the queue beside it does not list.
            go({ repo: event.target.value, action: '', intent: '', space: '', stage: '', unit: '', artifact: '' })
          }}
        >
          <option value="">{t('scope.allRepos')}</option>
          {repos.map((repo) => (
            <option key={repo.repo_id} value={repo.repo_id}>
              {repo.label}
            </option>
          ))}
        </select>
        <span className="studio-scope-count studio-muted studio-mono">
          {selected
            ? selected.canonical_path
            : registered === null
              ? t('common.unavailable')
              : plural(i18n, 'scope.registered', registered)}
        </span>
      </label>

      {(unavailable ?? 0) > 0 ? (
        <button
          type="button"
          className="studio-chip"
          data-tone="warn"
          onClick={() => go({ view: 'repos' })}
        >
          <Icon name="warn" size={11} strokeWidth={2} />
          {plural(i18n, 'scope.unavailable', unavailable ?? 0)}
        </button>
      ) : null}

      <Crumb route={route} repo={selected} />
      {children}
    </div>
  )
}

/**
 * `› repo / [space /] intent / stage`, with the space segment present only when it is not `default`
 * (FR-ACT-006). Emitting `default` on every crumb would train the eye to skip the segment that matters
 * on the one install where a second space exists.
 */
function Crumb({ route, repo }: { route: StudioRoute; repo: RepoRecord | null }) {
  const { t } = useI18n()
  const [space, intentDir] = splitIntentKey(route.intent)
  const parts: string[] = []
  if (repo) parts.push(repo.label)
  if (space && space !== DEFAULT_SPACE) parts.push(space)
  if (intentDir) parts.push(intentDir)
  if (route.stage) parts.push(route.stage)

  if (parts.length === 0) {
    return <span className="studio-crumb">{t('scope.crumbNoScan')}</span>
  }
  return (
    <span className="studio-crumb studio-trunc">
      {parts.map((part, index) => (
        <span key={`${index}-${part}`}>
          {index > 0 ? <span className="sep">/</span> : null}
          {/* Bold for the identities, plain for the stage: the stage is AI-DLC's word and is never
              translated, so it must not read as a Studio label. */}
          {index < parts.length - 1 || !route.stage ? <b>{part}</b> : part}
        </span>
      ))}
    </span>
  )
}

/** `space~dir` → `[space, dir]`; a bare dir is in `default` (§0.1). */
export function splitIntentKey(key: string): [string, string] {
  if (!key) return ['', '']
  const at = key.indexOf('~')
  return at < 0 ? [DEFAULT_SPACE, key] : [key.slice(0, at), key.slice(at + 1)]
}
