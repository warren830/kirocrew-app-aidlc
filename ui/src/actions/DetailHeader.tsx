/**
 * The detail pane's header: where you are, what is being asked, and how to link to it.
 *
 * The breadcrumb is the card's own scope — type, repo, space when it is not the default one, intent,
 * stage — because the queue row is off screen below 900px and the scope bar above only shows the
 * *selected* scope, which may be "All repos". Without this, a deep link from Slack would open a
 * decision with no indication of which repository it belongs to.
 *
 * The deep link is `card.deep_link` as the backend built it (that exact string is what notifications
 * and Slack already sent out), copied through the async clipboard with a synchronous fallback: the
 * dashboard may run over plain HTTP on a LAN, where `navigator.clipboard` is undefined.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Btn } from '@kirocrew/app-sdk/ui'

import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { useI18n } from '../i18n'
import { at, ref, waiting } from '../lib/format'
import { isClosedAction, isRepositoryUnavailable } from '../lib/actionQueue'
import { DEFAULT_SPACE } from '../lib/route'
import type { ActionCard } from '../lib/types'
import { statusLabel, statusTone } from './DeliveryStrip'
import { isRefreshing, SEVERITY_TONE, stageText, typeIconOf } from './QueueRow'

export interface DetailHeaderProps {
  card: ActionCard
  onOpenCurrent?: () => void
}

export function DetailHeader({ card, onOpenCurrent }: DetailHeaderProps) {
  const i18n = useI18n()
  const { t } = i18n
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => () => {
    if (timer.current !== null) clearTimeout(timer.current)
  }, [])

  const copy = useCallback(async () => {
    const url = `${window.location.origin}${card.deep_link}`
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(url)
      else throw new Error('no clipboard')
    } catch {
      // No async clipboard (insecure origin, older webview): a selectable, focused input is the only
      // remaining way to let the user copy, and it is better than a button that silently does nothing.
      const field = document.createElement('input')
      field.value = url
      field.setAttribute('readonly', 'true')
      field.style.position = 'fixed'
      field.style.opacity = '0'
      document.body.appendChild(field)
      field.select()
      try {
        document.execCommand('copy')
      } catch {
        // Nothing left to try; the label stays unchanged so the user is not told it worked.
        document.body.removeChild(field)
        return
      }
      document.body.removeChild(field)
    }
    setCopied(true)
    if (timer.current !== null) clearTimeout(timer.current)
    timer.current = setTimeout(() => setCopied(false), 2200)
  }, [card.deep_link])

  const typeLabel = t(`enum.actionType.${card.queue_type}`)
  const closed = isClosedAction(card)
  const unavailable = isRepositoryUnavailable(card)
  const headline = unavailable && !closed ? t('detail.repoUnavailable.title') : closed
    ? t('detail.closedTitle', { type: typeLabel, status: statusLabel(i18n, card.status, card.resolution.reason) })
    : i18n.has(card.headline.key) ? ref(i18n, card.headline) : typeLabel
  const refreshing = isRefreshing(card)
  const reviewClass = card.evidence.review?.review_class ?? null
  const revision = card.evidence.state?.revision_count ?? null

  return (
    <header className="studio-detail-head">
      <nav className="studio-dbreadcrumb studio-mono" aria-label={t('detail.breadcrumb')}>
        <span className="studio-drumb">
          <Icon name={typeIconOf(card)} size={13} /> {typeLabel}
        </span>
        <Crumb>{card.repo.label || card.repo.repo_id}</Crumb>
        {card.space && card.space !== DEFAULT_SPACE ? <Crumb>{card.space}</Crumb> : null}
        <Crumb>{card.intent.slug || card.intent.intent_dir}</Crumb>
        {card.stage ? <Crumb>{stageText(card, t('common.unavailable'))}</Crumb> : null}
        <Btn className="studio-anchor-link" onClick={() => void copy()} title={t('detail.deepLinkTitle')}>
          <Icon name={copied ? 'check' : 'link'} size={13} />
          {copied ? t('detail.copied') : t('detail.deepLink')}
        </Btn>
      </nav>

      <h1 className="studio-dtitle">{headline}</h1>

      <div className="studio-dfacts">
        {unavailable ? <Chip icon="doc">{t('detail.repoUnavailable.saved')}</Chip> : closed ? <Chip icon="doc">{t('detail.history')}</Chip> : (
          <Chip tone={SEVERITY_TONE[card.severity]} icon={card.severity === 'info' ? 'info' : 'warn'}>
            {t(`enum.severity.${card.severity}`)}
          </Chip>
        )}
        {unavailable ? <Chip icon="clock">{t('detail.repoUnavailable.recordedAt', { at: at(i18n, card.updated_at) })}</Chip> : closed ? (
          card.resolution.resolved_at
            ? <Chip icon="clock">{t('detail.closedAt', { at: at(i18n, card.resolution.resolved_at) })}</Chip>
            : null
        ) : <Chip icon="clock">{t('detail.waiting', { duration: waiting(i18n, card.waiting_since) })}</Chip>}
        <Chip tone={statusTone(card.status)}>{statusLabel(i18n, card.status, card.resolution.reason)}</Chip>
        {/* The review class is AI-DLC's own configured word for this stage: shown verbatim. */}
        {reviewClass ? <Chip tone="info" icon="review">{t('detail.reviewClass', { name: reviewClass })}</Chip> : null}
        {revision !== null && revision > 0 ? <Chip>{t('detail.revision', { n: i18n.fmt.number(revision) })}</Chip> : null}
        {refreshing ? (
          // Polite, not assertive: nothing is wrong, the evidence is simply moving. The send control
          // carries the refusal.
          <Chip tone="warn" icon="refresh">{t('detail.refreshing')}</Chip>
        ) : null}
        <Chip mono title={card.action_id}>{card.action_id}</Chip>
        {closed && onOpenCurrent ? (
          <Btn onClick={onOpenCurrent}><Icon name="inbox" size={13} /> {t('detail.currentActions')}</Btn>
        ) : null}
      </div>
    </header>
  )
}

function Crumb({ children }: { children: React.ReactNode }) {
  return (
    <>
      <span aria-hidden="true" className="studio-dsep">/</span>
      <span className="studio-drumb studio-trunc">{children}</span>
    </>
  )
}
