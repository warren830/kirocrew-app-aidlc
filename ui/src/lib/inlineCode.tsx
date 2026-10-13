import type { ReactNode } from 'react'

/**
 * The engine's text with its backtick spans set as code. Nothing else is interpreted: question options and
 * reviewer findings are AI-DLC's own words (FR-Q-001, FR-ART-005), and only the backticks were showing up as
 * literal characters.
 */
export function withInlineCode(text: string): ReactNode {
  const parts = text.split(/`([^`\n]+)`/)
  if (parts.length === 1) return text
  return parts.map((part, index) => (index % 2 ? <code key={index} className="studio-icode">{part}</code> : part))
}
