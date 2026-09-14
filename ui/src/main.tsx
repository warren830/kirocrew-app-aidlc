/**
 * Bundle entry. AppHost renders the DEFAULT export as a React component with no props.
 *
 * Two things happen before React does: the CSS import (which is what makes the lib build emit
 * `dist/style.css`) and `ensureStylesheet`, which actually loads that file. A lib build does not inject
 * its own stylesheet and the dashboard host never loads app CSS, so without the second step the page
 * renders unstyled — the same reason the `<link>` points at the served ui/ path rather than a bundled
 * data URL.
 */
import './styles/studio.css'

import { I18nProvider } from './i18n'
import { ensureStylesheet } from './lib/host'
import { StudioApp } from './shell/StudioApp'

ensureStylesheet()

export default function AidlcStudio() {
  return (
    <I18nProvider>
      <StudioApp />
    </I18nProvider>
  )
}
