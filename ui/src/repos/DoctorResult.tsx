import { useEffect, useRef } from 'react'

import { useI18n } from '../i18n'
import type { StudioApiError } from '../lib/api'
import type { EngineResult } from '../lib/types'
import { Icon } from '../shell/Icon'
import { ErrorNote } from './PreflightReport'

export type DoctorFeedback =
  | { status: 'running' }
  | { status: 'completed'; result: EngineResult }
  | { status: 'failed'; error: StudioApiError }

/** Keep the response beside the control that triggered it, including on a long repository page. */
export function DoctorResult({ feedback }: { feedback: DoctorFeedback }) {
  const i18n = useI18n()
  const { t } = i18n
  const container = useRef<HTMLElement>(null)
  useEffect(() => {
    container.current?.scrollIntoView?.({ block: 'nearest' })
  }, [feedback])

  return (
    <section ref={container} aria-label={t('repos.doctor.result')}>
      <h4 className="studio-subhead">{t('repos.doctor.result')}</h4>
      {feedback.status === 'running' ? (
        <p className="studio-note" role="status">
          <Icon name="clock" size={13} /> {t('repos.action.doctorRunning')}
        </p>
      ) : feedback.status === 'failed' ? (
        <ErrorNote error={feedback.error} />
      ) : (
        <>
          <p className="studio-note" role="status" {...(!feedback.result.ok ? { 'data-tone': 'warn' } : {})}>
            <Icon name={feedback.result.ok ? 'check' : 'warn'} size={13} />{' '}
            {t(feedback.result.ok ? 'repos.action.doctorDone' : 'repos.action.doctorFailed', {
              code: feedback.result.exit_code ?? t('common.unavailable'),
            })}
          </p>
          <p className="studio-help">{t('repos.doctor.duration', { ms: i18n.fmt.number(feedback.result.duration_ms) })}</p>
          {feedback.result.stdout ? (
            <details>
              <summary>{t('repos.doctor.output')}</summary>
              <pre className="studio-pre studio-wrap-any">{feedback.result.stdout}</pre>
            </details>
          ) : null}
          {feedback.result.stderr ? (
            <details>
              <summary>{t('repos.doctor.stderr')}</summary>
              <pre className="studio-pre studio-wrap-any">{feedback.result.stderr}</pre>
            </details>
          ) : null}
        </>
      )}
    </section>
  )
}
