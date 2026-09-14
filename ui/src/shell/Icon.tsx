/**
 * Every icon in Studio, resolved through one table.
 *
 * Two hard host facts shape this file:
 *
 *  1. **Icons come from the DEFAULT export.** `lucide-react` is served as a vendor stub that names
 *     only ~40 icons; `import { GitBranch } from 'lucide-react'` compiles and then fails at link time
 *     in the browser with `does not provide an export named 'GitBranch'`, which kills the whole app
 *     module (`03 §1.3`). The default export is a Proxy over the full set, so destructuring from it is
 *     the only form that works.
 *  2. **A name the Proxy does not know returns `undefined`,** and rendering `undefined` as a component
 *     throws. So a missing glyph renders nothing at all: every icon in Studio is paired with a text
 *     label (colour and shape never carry meaning alone, PRD §10.1), which means a missing glyph costs
 *     a little whitespace instead of a blank page.
 *
 * The semantic names below are the mockup's `<symbol id>` inventory (visual spec §11), so a component
 * asks for `gate` rather than picking a glyph, and one edit here re-skins the whole app.
 */

import type { ComponentType, SVGProps } from 'react'
import lucide from 'lucide-react'

type LucideIcon = ComponentType<SVGProps<SVGSVGElement> & { size?: number | string; strokeWidth?: number | string }>

/** Semantic name → the canonical lucide identifier (verified against lucide-react 1.7.0, §11). */
export const ICON_NAMES = {
  logo: 'Workflow',
  inbox: 'Inbox',
  repo: 'FolderGit2',
  intent: 'ListTodo',
  map: 'LayoutGrid',
  activity: 'Activity',
  settings: 'Settings',
  // `LockKeyhole` rather than `Lock`, so a Gate never looks like a lease or a receipt.
  gate: 'LockKeyhole',
  question: 'CircleQuestionMark',
  missingInput: 'FileQuestionMark',
  recovery: 'TriangleAlert',
  fail: 'CircleX',
  install: 'Download',
  check: 'Check',
  warn: 'CircleAlert',
  info: 'Info',
  clock: 'Clock',
  doc: 'FileText',
  review: 'Eye',
  advisor: 'Sparkles',
  send: 'Send',
  search: 'Search',
  chevron: 'ChevronRight',
  chevronDown: 'ChevronDown',
  back: 'ChevronLeft',
  play: 'Play',
  pause: 'Pause',
  moon: 'Moon',
  sun: 'Sun',
  // lucide 1.7.0 has no Slack brand glyph in any form (§11), and a wrong brand mark is worse than none.
  slack: 'MessageSquare',
  git: 'GitBranch',
  lock: 'Lock',
  plus: 'Plus',
  link: 'Link',
  refresh: 'RefreshCw',
  close: 'X',
  external: 'ExternalLink',
} as const

export type IconName = keyof typeof ICON_NAMES

const set = lucide as unknown as Record<string, LucideIcon | undefined>

export interface IconProps {
  name: IconName
  /** 15 is the body size; 13 beside small text, 18 for an empty state, 11 inside a chip (§2.5). */
  size?: number
  strokeWidth?: number
  className?: string
  /**
   * Set only when the icon is the sole content of a control. Anything with a visible label beside it
   * stays `aria-hidden`, or a screen reader reads the meaning twice.
   */
  label?: string
}

export function Icon({ name, size = 15, strokeWidth = 1.6, className, label }: IconProps) {
  const Glyph = set[ICON_NAMES[name]]
  if (!Glyph) return null
  return (
    <Glyph
      size={size}
      strokeWidth={strokeWidth}
      className={className}
      focusable="false"
      {...(label ? { role: 'img', 'aria-label': label } : { 'aria-hidden': true })}
    />
  )
}
