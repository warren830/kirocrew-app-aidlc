/**
 * The Evidence drawer: raw evidence, verbatim (FR-EVT-006).
 *
 * What it is for: a decision that depends on contradictory evidence — a delivery that may or may not have
 * landed, an audit trail that disagrees with a state file — cannot be made from a summary. The drawer
 * shows the underlying bytes, labelled with which of the six evidence classes each block came from, so
 * the user can see the contradiction instead of trusting Studio's reading of it.
 *
 * Four constraints shape the implementation:
 *
 *  1. **Raw means raw.** Every block renders inside a `<pre>` as text — not markdown, and never React's
 *     raw-HTML escape hatch: an audit block contains agent-authored text, and re-interpreting it could
 *     change what the user believes the evidence said.
 *  2. **It is rendered in place, not portalled to `document.body`.** Studio's stylesheet is scoped under
 *     `.studio`; a portal outside that subtree would render this panel untokenised and unreadable.
 *  3. **`aria-modal` obliges a real trap.** Focus moves into the panel on open, Tab cycles inside it,
 *     Escape closes it, and focus returns to the control that opened it. Announcing a modal that a screen
 *     reader user can tab out of is worse than not announcing one.
 *  4. **`onOpened` fires once per opening.** The `resubmit` decision is only offered after the user has
 *     actually seen the evidence (contract §3.6), and that gate is only honest if it is driven by this
 *     component rather than by the button that renders it.
 */

import { useCallback, useEffect, useId, useRef, type KeyboardEvent } from 'react'

import { useI18n } from '../i18n'
import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'

/** The six evidence classes Studio reads (PRD §12); the label comes from the catalogue, the data does not. */
export type EvidenceSource =
  | 'aidlc_state'
  | 'aidlc_audit'
  | 'kirocrew_session'
  | 'studio_action'
  | 'turn_marker'
  | 'git'

export interface EvidenceFact {
  label: string
  value: string
  /** Paths, hashes, ids and counts are monospaced so they can be compared by eye. */
  mono?: boolean
}

export interface EvidenceSection {
  id: string
  /** Already-localized heading. */
  title: string
  source?: EvidenceSource
  /** File location or reference this block came from, shown verbatim. */
  ref?: string | null
  facts?: EvidenceFact[]
  /** The bytes themselves. `null` means "this source had nothing to show", which is itself evidence. */
  raw?: string | null
  /** True when the backend removed something under the redaction policy. */
  redacted?: boolean
  /** Set when this source disagrees with the others — rendered as a conflict, not just a colour. */
  conflict?: boolean
}

export interface EvidenceDrawerProps {
  open: boolean
  onClose: () => void
  /** Heading. Defaults to the catalogue's own title. */
  title?: string
  /** One line under the heading saying what this evidence is about. */
  subtitle?: string
  sections: readonly EvidenceSection[]
  /** Fires once each time the drawer opens — the gate a `resubmit` decision depends on. */
  onOpened?: () => void
}

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select,textarea,[tabindex]:not([tabindex="-1"])'

export function EvidenceDrawer({
  open,
  onClose,
  title,
  subtitle,
  sections,
  onOpened,
}: EvidenceDrawerProps) {
  const { t } = useI18n()
  const panelRef = useRef<HTMLDivElement | null>(null)
  const returnTo = useRef<HTMLElement | null>(null)
  const headingId = useId()

  const announced = useRef(false)
  useEffect(() => {
    if (!open) {
      announced.current = false
      return
    }
    if (!announced.current) {
      announced.current = true
      onOpened?.()
    }
  }, [open, onOpened])

  useEffect(() => {
    if (!open) return
    returnTo.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    panelRef.current?.focus()
    return () => {
      // Returning focus is what makes the drawer usable from the keyboard twice in a row.
      returnTo.current?.focus()
    }
  }, [open])

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
        return
      }
      if (event.key !== 'Tab') return
      const panel = panelRef.current
      if (!panel) return
      // No visibility filter: `offsetParent` is always null in jsdom and unreliable inside a
      // `position: fixed` panel, so a filter here would silently empty the cycle.
      const stops = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)]
      if (stops.length === 0) {
        event.preventDefault()
        panel.focus()
        return
      }
      const first = stops[0]
      const last = stops[stops.length - 1]
      const active = document.activeElement
      if (!event.shiftKey && active === last) {
        event.preventDefault()
        first?.focus()
      } else if (event.shiftKey && (active === first || active === panel)) {
        event.preventDefault()
        last?.focus()
      }
    },
    [onClose],
  )

  if (!open) return null

  return (
    <div className="studio-drawer-wrap">
      {/* Mouse dismissal only; every keyboard path is Escape or the Close button. */}
      <div className="studio-drawer-scrim" onClick={onClose} aria-hidden="true" />
      <div
        className="studio-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        tabIndex={-1}
        ref={panelRef}
        onKeyDown={onKeyDown}
      >
        <header className="studio-drawer-head">
          <h2 id={headingId}>
            <Icon name="doc" size={15} /> {title ?? t('review.evidence.title')}
          </h2>
          <button
            type="button"
            className="studio-icon-btn"
            onClick={onClose}
            aria-label={t('common.close')}
          >
            <Icon name="close" size={15} />
          </button>
        </header>

        {subtitle ? <p className="studio-drawer-sub studio-muted">{subtitle}</p> : null}

        <div className="studio-drawer-body">
          {sections.length === 0 ? (
            <p className="studio-muted">{t('review.evidence.none')}</p>
          ) : (
            sections.map((section) => (
              <section className="studio-drawer-sec" key={section.id} data-conflict={section.conflict ? 'true' : undefined}>
                <div className="studio-drawer-sec-head">
                  <h3>{section.title}</h3>
                  {section.source ? <Chip>{t(`review.evidence.source.${section.source}`)}</Chip> : null}
                  {section.conflict ? (
                    <Chip tone="danger" icon="warn">
                      {t('review.evidence.conflict')}
                    </Chip>
                  ) : null}
                  {section.redacted ? (
                    <Chip tone="warn" icon="lock">
                      {t('review.evidence.redacted')}
                    </Chip>
                  ) : null}
                </div>

                {section.ref ? (
                  <p className="studio-drawer-ref studio-mono studio-wrap-any">{section.ref}</p>
                ) : null}

                {section.facts && section.facts.length > 0 ? (
                  <dl className="studio-drawer-facts">
                    {section.facts.map((fact) => (
                      <div key={`${section.id}-${fact.label}`}>
                        <dt>{fact.label}</dt>
                        <dd className={fact.mono ? 'studio-mono studio-wrap-any' : undefined}>{fact.value}</dd>
                      </div>
                    ))}
                  </dl>
                ) : null}

                {section.raw === null || section.raw === undefined ? (
                  <p className="studio-muted">{t('review.evidence.noRaw')}</p>
                ) : (
                  <pre className="studio-plain studio-mono">{section.raw}</pre>
                )}
              </section>
            ))
          )}
        </div>

        <footer className="studio-drawer-foot">
          <p className="studio-consequence">
            <Icon name="lock" size={13} />
            {t('review.evidence.policy')}
          </p>
        </footer>
      </div>
    </div>
  )
}
