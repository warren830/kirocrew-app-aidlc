/**
 * What a waiting tool approval asks to run, and where it reaches: the facts a person needs before pressing the
 * host's controls. A conversation once grepped another registered repository's audit trail to copy how an earlier
 * run had recorded its answers (local-s3, 2026-11-03), and the notice showed only the raw tool input.
 */

import type { RepoRecord } from '../lib/types'

/** The host's tool input is the call's JSON arguments (`{"__tool_use_purpose": …, "command": …}`), cut at 500
 *  characters, so a long one no longer parses: its command is then read field by field. */
const COMMAND_KEYS = ['command', 'file_path', 'path', 'pattern', 'url'] as const

function parsed(toolInput: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(toolInput)
    return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
  } catch {
    return null
  }
}

function field(toolInput: string, key: string): string | null {
  const whole = parsed(toolInput)?.[key]
  if (typeof whole === 'string') return whole
  // A string value cut off with the input has no closing quote: take what is there.
  const match = new RegExp(`"${key}"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)`).exec(toolInput)
  if (!match?.[1]) return null
  try {
    return JSON.parse(`"${match[1].replace(/\\$/, '')}"`) as string
  } catch {
    return match[1]
  }
}

/** The command (or path) the call runs on, else the input as the host gave it. */
export function approvalCommand(toolInput: string): string {
  for (const key of COMMAND_KEYS) {
    const value = field(toolInput, key)
    if (value) return value
  }
  return toolInput
}

/** Why the model says it wants the call: the host's own field, else the one inside the arguments. */
export function approvalPurpose(purpose: string | undefined, toolInput: string): string {
  return purpose?.trim() || field(toolInput, '__tool_use_purpose')?.trim() || ''
}

export interface ApprovalReach {
  /** Other registered repositories the command names a path in. */
  otherRepos: { label: string; path: string }[]
  /** Absolute paths in neither this repository nor another registered one. */
  outside: string[]
}

/** Device files a shell redirect names; they hold nobody's data. */
const HARMLESS = new Set(['/dev/null', '/dev/stdin', '/dev/stdout', '/dev/stderr', '/dev/tty'])
const ABSOLUTE_PATH = /(?:^|[\s"'=(:,])(\/[^\s"'`;|&<>(){}[\]*?,]+)/g

function within(path: string, root: string): boolean {
  const base = root.replace(/\/+$/, '')
  return base !== '' && (path === base || path.startsWith(`${base}/`))
}

/** Where the command's absolute paths land. Relative ones run from the repository root, so they are inside. */
export function approvalReach(text: string, repoId: string, repos: readonly RepoRecord[]): ApprovalReach {
  const own = repos.find((repo) => repo.repo_id === repoId)?.canonical_path ?? ''
  const others = repos.filter((repo) => repo.repo_id !== repoId && repo.canonical_path)
  const otherRepos = new Map<string, { label: string; path: string }>()
  const outside = new Set<string>()
  for (const match of text.matchAll(ABSOLUTE_PATH)) {
    const path = (match[1] ?? '').replace(/[.:]+$/, '')
    if (!path || path.startsWith('//') || HARMLESS.has(path) || (own && within(path, own))) continue
    // The deepest registered root wins: a repository can sit inside another's directory.
    const owner = others.filter((repo) => within(path, repo.canonical_path))
      .sort((a, b) => b.canonical_path.length - a.canonical_path.length)[0]
    if (owner) otherRepos.set(owner.repo_id, { label: owner.label, path: owner.canonical_path })
    else outside.add(path)
  }
  return { otherRepos: [...otherRepos.values()], outside: [...outside].slice(0, 5) }
}
