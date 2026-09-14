/**
 * The top bar: brand, the six primary destinations, and the one action that starts work.
 *
 * Order is PRD §8.1 exactly (Action Center, Repos, Intents, Workflow Map, Activity, Settings) and only
 * Action Center carries a count. There is deliberately **no theme control**: the host owns
 * `html[data-theme]` across 30+ themes, and a Studio toggle would fight it (visual spec §1.2
 * DEVIATION, discrepancy #5).
 *
 * `New intent` is a top-bar button rather than a seventh nav item because it is a creation flow, not a
 * place — and the wizard writes nothing until its last step, so it must not look like a destination
 * the user is already in.
 */

import { Btn } from '@kirocrew/app-sdk/ui'

import { useI18n } from '../i18n'
import { plural } from '../lib/format'
import type { Navigate, StudioRoute, View } from '../lib/route'
import { Icon, type IconName } from './Icon'

/** The rail, in PRD §8.1 order. `new-intent` is not here on purpose (see the module comment). */
const NAV: readonly { view: View; icon: IconName }[] = [
  { view: 'actions', icon: 'inbox' },
  { view: 'repos', icon: 'repo' },
  { view: 'intents', icon: 'intent' },
  { view: 'map', icon: 'map' },
  { view: 'activity', icon: 'activity' },
  { view: 'settings', icon: 'settings' },
]

export interface TopBarProps {
  route: StudioRoute
  go: Navigate
  /** Open items in the Action Center, or `null` while the count is not yet known. */
  queueCount: number | null
  /**
   * True when a detail pane is selected. The button is present in the DOM and hidden above 900px by
   * `.studio-back` in the stylesheet — the same breakpoint that hides the queue, so the two can never
   * disagree about whether a way back exists.
   */
  showBack: boolean
  onBack: () => void
}

export function TopBar({ route, go, queueCount, showBack, onBack }: TopBarProps) {
  const i18n = useI18n()
  const { t } = i18n

  return (
    <header className="studio-topbar">
      {showBack ? (
        // An icon-only control names itself: an `aria-label` on the glyph inside is not reliably read
        // as the button's name, and Back is the only way out of the detail pane on a phone.
        <button
          type="button"
          className="studio-icon-btn studio-back"
          aria-label={t('shell.backToQueue')}
          onClick={onBack}
        >
          <Icon name="back" size={16} />
        </button>
      ) : null}

      <span className="studio-brand">
        <span className="studio-brand-mark" aria-hidden>
          <Icon name="logo" size={15} />
        </span>
        <span className="studio-brand-name">{t('shell.brand')}</span>
      </span>

      <nav className="studio-nav" aria-label={t('shell.a11y.primaryNav')}>
        {NAV.map(({ view, icon }) => {
          const active = route.view === view
          const badge = view === 'actions' && queueCount !== null && queueCount > 0
          return (
            <button
              key={view}
              type="button"
              // `page`, not a boolean: the rail is the app's primary navigation, and a screen reader
              // announces "current page" from this attribute alone.
              {...(active ? { 'aria-current': 'page' as const } : {})}
              onClick={() => go({ view })}
            >
              <Icon name={icon} size={15} />
              <span className="studio-nav-text">{t(`nav.${view}`)}</span>
              {badge ? (
                <span className="studio-count" aria-hidden>
                  {i18n.fmt.number(queueCount)}
                </span>
              ) : null}
              {badge ? (
                <span className="studio-sr">{plural(i18n, 'shell.a11y.queueCount', queueCount)}</span>
              ) : null}
            </button>
          )
        })}
      </nav>

      <div className="studio-topbar-right">
        <Btn onClick={() => go({ view: 'new-intent' })}>
          <Icon name="plus" size={13} />
          <span className="studio-nav-label">{t('nav.newIntent')}</span>
        </Btn>
      </div>
    </header>
  )
}
