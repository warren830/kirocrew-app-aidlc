/**
 * The chip: Studio's smallest labelled fact.
 *
 * Not `Badge` from the host UI kit, which has five fixed variants (`ok | err | warn | aim | muted`)
 * and no `info` or `accent` — and Studio needs both, because `info` is how an advisory reads and
 * `accent` is how a blocking Gate reads (visual spec §2.5, §3.2). The styling lives in
 * `studio.css` (`.studio-chip[data-tone]`), which is also where the two token aliases the host's own
 * values cannot supply are resolved.
 *
 * A chip always contains its own words. An icon and a colour are added on top, never instead: three
 * encodings of the same meaning is what keeps severity legible without colour (PRD §10.3).
 */

import type { ReactNode } from 'react'

import { Icon, type IconName } from './Icon'

export type ChipTone = 'neutral' | 'danger' | 'warn' | 'ok' | 'info' | 'accent' | 'aim'

export interface ChipProps {
  children: ReactNode
  tone?: ChipTone
  icon?: IconName
  /** Ids, hashes, paths and counts are monospaced so they can be compared by eye (§2.3). */
  mono?: boolean
  title?: string
  className?: string
}

export function Chip({ children, tone = 'neutral', icon, mono, title, className }: ChipProps) {
  const classes = ['studio-chip']
  if (mono) classes.push('studio-mono')
  if (className) classes.push(className)
  return (
    <span
      className={classes.join(' ')}
      {...(tone === 'neutral' ? {} : { 'data-tone': tone })}
      {...(title ? { title } : {})}
    >
      {icon ? <Icon name={icon} size={11} strokeWidth={2} /> : null}
      {children}
    </span>
  )
}
