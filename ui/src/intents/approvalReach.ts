/**
 * What a waiting tool approval asks to run, and where it reaches: the facts a person needs before pressing the
 * host's controls. A conversation once grepped another registered repository's audit trail to copy how an earlier
 * run had recorded its answers (local-s3, 2026-11-03), and the notice showed only the raw tool input.
 *
 * A call's text is read as shell words. A word the shell passes on as written is placed path by path, in this
 * repository, another registered one, or outside them all; a word the shell finishes as it runs (a variable, a
 * command's output, a brace list, a home folder, a wildcard where a registered root may match) is one Studio cannot
 * place, though the literal paths in it still name the repositories they reach. Whatever Studio cannot read, it does
 * not vouch for.
 */

import type { RepoRecord } from '../lib/types'

/** The host's tool input is the call's JSON arguments (`{"__tool_use_purpose": …, "command": …}`), cut at 16 KB,
 *  so a long one no longer parses: its command is then read field by field. */
const COMMAND_KEYS = ['command', 'file_path', 'path', 'pattern', 'url'] as const
/** The host's own key for what the call is for (`TOOL_PURPOSE_KEYS`): said apart from the call, never part of it. */
const PURPOSE_KEYS = new Set(['__tool_use_purpose', '__toolUsePurpose'])
/** Arguments a tool reads as one path, spaces and all. */
const PATH_KEYS = new Set(['path', 'paths', 'file_path', 'filePath', 'file_paths', 'notebook_path', 'cwd', 'working_dir',
  'workdir', 'directory', 'dir', 'old_path', 'new_path'])
/** What a write puts in its file: the file's text, not where the call reaches. */
const CONTENT_KEYS = new Set(['content', 'contents', 'text', 'fileText', 'file_text', 'oldStr', 'newStr', 'old_str',
  'new_str', 'old_string', 'new_string', 'new_source', 'insert_text', 'edits'])
/** ACP's kinds of call whose arguments name what they work on. The host relays the kind as the agent states it, so it
 *  only picks which text names the target; a kind outside these leaves the call unread. */
const WRITE_KINDS = new Set(['edit', 'write', 'create', 'delete', 'move'])
const READ_KINDS = new Set(['read', 'search', 'fetch'])

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

const isJsonObject = (toolInput: string) => toolInput.trimStart().startsWith('{')

/** The host's diff of a write (`--- <path>` then `+++ <path>`); its other lines are the file's text. */
function diffTargets(toolInput: string): string[] | null {
  const [minus = '', plus = ''] = toolInput.split('\n', 2)
  if (!minus.startsWith('--- ') || !plus.startsWith('+++ ')) return null
  return [minus, plus].map((line) => line.slice(4).split('\t')[0]!).filter(Boolean)
}

/** The command (or path) the call runs on, else the input as the host gave it. A write is shown whole: a value in
 *  the file it writes, or its edit verb, is not the call. */
export function approvalCommand(toolInput: string, toolKind?: string | null): string {
  if (WRITE_KINDS.has(toolKind ?? '') || !isJsonObject(toolInput)) return toolInput
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

/** What names where a call reaches: text a shell (or the program it runs) reads, and paths a tool takes outright. */
export interface ApprovalTargets {
  commands: string[]
  paths: string[]
  /** The host says what kind of call this is, and these are all of it that names a place: only then can the notice
   *  say the call stays in its repository. */
  whole: boolean
}

/** Every string in the arguments, in order, by the key that holds it; a write's content is left out. */
function collect(args: Record<string, unknown>, writes: boolean): Omit<ApprovalTargets, 'whole'> {
  const into = { commands: [] as string[], paths: [] as string[] }
  const todo: [unknown, string][] = [[args, '']]
  while (todo.length) {
    const [value, key] = todo.pop()!
    if (typeof value === 'string') (PATH_KEYS.has(key) ? into.paths : into.commands).push(value)
    else if (value && typeof value === 'object') {
      const inner: [unknown, string][] = Array.isArray(value) ? value.map((item) => [item, key])
        : Object.entries(value).filter(([name]) => !PURPOSE_KEYS.has(name) && !(writes && CONTENT_KEYS.has(name)))
          .map(([name, item]) => [item, name])
      for (let k = inner.length - 1; k >= 0; k--) todo.push(inner[k]!)
    }
  }
  return into
}

/** Where to read a call's reach, by the host's kind of call: a write's target from its diff header or its path
 *  argument, a command's text, a read's arguments. A kind it cannot read keeps every warning its text gives. */
export function approvalTargets(toolInput: string, toolKind?: string | null): ApprovalTargets {
  const kind = toolKind ?? ''
  const writes = WRITE_KINDS.has(kind)
  const header = diffTargets(toolInput)
  if (header) return writes && header.length ? { commands: [], paths: header, whole: true } : { commands: [toolInput], paths: [], whole: false }
  const args = parsed(toolInput)
  if (!args) {
    const fields = isJsonObject(toolInput) ? COMMAND_KEYS.flatMap((key) => field(toolInput, key) ?? []) : []
    return fields.length ? { commands: fields, paths: [], whole: false } : { commands: [toolInput], paths: [], whole: kind === 'execute' }
  }
  const into = collect(args, writes)
  const whole = kind === 'execute' ? typeof args.command === 'string'
    : writes ? into.paths.length > 0
    : READ_KINDS.has(kind) && into.commands.length + into.paths.length > 0
  return { ...into, whole }
}

// --------------------------------------------------------------------------- //
// where a call reaches
// --------------------------------------------------------------------------- //

export interface ApprovalReach {
  /** Other registered repositories the command names a path in. */
  otherRepos: { label: string; path: string }[]
  /** Absolute paths in neither this repository nor another registered one. */
  outside: string[]
  /** What only the shell can finish (a variable, a command's output, a brace list, a home folder, a wildcard where a
   *  repository's name may go, a `cd` to wherever it was), and paths that climb above the folder the call runs in. */
  unplaced: string[]
}

/** A shell word as the call writes it and as its program gets it. `filled`: the shell finishes it as it runs, with a
 *  variable, a command's output, a brace list or a home folder; `globbed`: it matches names. */
interface Word { written: string; text: string; filled: boolean; globbed: boolean }

/** A here-document's body: its program's input, which the shell fills in unless the end word is quoted. */
interface Body { text: string; quoted: boolean }

/** A command line in the order the shell reads it: quotes, a backquoted command, an escape, a comment, a
 *  here-document's operator and end word, and a line's end, after which the here-documents the line opened run. */
const LEXEME = /'[^']*'?|"(?:[^"\\]|\\[\s\S])*"?|`(?:[^`\\]|\\[\s\S])*`?|\\[\s\S]|(?<![^\s;&|()])#[^\n]*|(?<!<)<<(?!<)(-?)[ \t]*(?:'([^'\n]*)'|"([^"\\\n]*)"|(\\?)([^\s;&|<>()'"\\]+))|\n/g
/** A command line's words as written: blanks and `;&|()<>` end one, except inside quotes, `$(…)`, `${…}` and
 *  backquotes. */
const WORD = /(?:'[^']*'?|"(?:[^"\\]|\\[\s\S])*"?|`(?:[^`\\]|\\[\s\S])*`?|\$\((?:[^()]|\([^()]*\))*\)?|\$\{[^}]*\}?|\\[\s\S]|[^\s;&|()<>'"`\\])+/g
/** `$?`, `$#`, `$$` and `$-`: a number or the shell's flags, never a folder. */
const SPECIAL = /(?<!\\)\$[#?$-]/g
/** What the shell fills in: a variable, `${…}`, `$(…)`, `$'…'`, a backquoted command, a home folder. A `$` before
 *  anything else is plain text. */
const EXPANDS = /\$[\w{('"@*!^=~]|`|^~/
/** A word's quotes and escapes, in order: a quote inside another, or after a `\`, is not one. An unended quote runs to
 *  the end, as the word does. */
const QUOTED = /'[^']*'?|"(?:[^"\\]|\\[\s\S])*"?|\\[\s\S]/g
/** The same as the shell takes them off, the `$` of `$'…'` and `$"…"` with them. */
const UNQUOTE = /\$?'([^']*)'?|\$?"((?:[^"\\]|\\[\s\S])*)"?|\\([\s\S])/g
/** A shell's wildcard: `*`, `?`, or a `[…]` set (a `[` with no `]` is plain text). */
const GLOB = /[*?]|\[[^\][]*\]/
/** A wildcard with no path before it in its piece: it matches names in the folder the call runs in. */
const GLOB_FIRST = /(?:^|[\s'"`;&|<>(){}$=:,@\\])[*?[]/
/** A brace group: with a `,` or a `..` in it, a list the shell expands. */
const BRACES = /\{[^{}]*\}/g
/** A line that hands its text to another shell. */
const SCRIPTED = /(?:^|[\s;&|/()<>`])(?:sh|bash|zsh|dash|ksh|mksh|ash|fish|eval)(?=$|[\s;&|()<>`])/
/** A `-c` script written as one bare word: it ends there, and the words after it are its arguments. */
const ONE_WORD_SCRIPT = /(\s-[A-Za-z]*c[A-Za-z]*[ \t]+[^\s'"`\\;&|()<>]+)(?=[ \t])/g
/** A `cd` or `pushd` and the folder it goes to: none, `-` or `+1` is a home folder or wherever the shell was. */
const CHANGE_OF_FOLDER = /(?:^|[\s;&|()`])((?:cd|pushd)(?=$|[\s;&|()<>`])(?:[ \t]+(?:-[LPe@]+|--))*(?:[ \t]+([^\s;&|()<>`]+))?)/g
/** A URL to somewhere else, where a piece of text starts (an opening quote's included); after anything else, `x://`
 *  is a folder named `x:`. A `$'…'` or `$"…"` after it is the shell's again. */
const REMOTE_URL = /(?<=(?:^|[\s=:,;|&<>()`])['"]?)(?!file:)[A-Za-z][\w+.-]*:\/\/((?:[^\s"'`;&|<>()$\\]|\$(?!['"]))*)/gi
/** A file URL: a path on this computer, whatever host it gives, through `:` and `=` too. */
const FILE_URL = /(?<![^\s'"`=:,;|&<>()])file:\/\/[^/\s'"`;&|<>()]*(\/[^\s'"`;&|<>()]*)?/gi
/** In text the shell passes on as written, what a program may read as one path: a word, `--opt=value`, `host:path`,
 *  a list's item, a program's string. */
const PIECE = /[^\s'"`;&|<>()=:,]+/g
/** In a word the shell finishes or matches, a run of path characters up to what it fills in or matches. After `$` or
 *  `${NAME}` it goes on from a variable; a `$` before anything else is part of it. */
const RUN = /(\$\{[A-Za-z_]\w*\}|\$(?=[A-Za-z_]))?((?:[^\s'"`;&|<>(){}$*?[\]=:,@\\]|\$(?![\w{(]))+)/g
/** After a run, what the shell fills in or matches, or, in a word it only matches, more of the same path: the run then
 *  names a folder only up to its last `/`. */
const CUT = /[$`*?[{]/
const STUCK = /[$*?[\]{}@\\]/
/** Before a run, the end of what the shell filled in or matched: the run goes on from it. In a word it only matches,
 *  a `)` or `}` is plain text. */
const CONTINUES = /[)}*?\]]/
const CONTINUES_MATCH = /[*?\]]/
/** An option glued to its path (`-C/path`, `-I../include`, `-o~/out`), and a `${X:-/path}` or `${X:+/path}` default. */
const GLUED = /^[-+]{1,2}[A-Za-z0-9]*(?=[/.~@$])/
/** A variable of the program a path is for, opening the path: `$HOME/.aws`. */
const PROGRAM_VARIABLE = /^\$(?:[A-Za-z_]\w*|\{[A-Za-z_]\w*\})\//
/** Device files a shell redirect names; they hold nobody's data. */
const HARMLESS = new Set(['/dev/null', '/dev/stdin', '/dev/stdout', '/dev/stderr', '/dev/tty'])

const MAX_BASES = 8

/** Text with its quotes taken off as the shell takes them: what a program gets, and the script another shell reads.
 *  An `$'…'` with an escape in it keeps its `$`, as Studio does not spell it out. */
const unquote = (text: string) => text.replace(UNQUOTE, (part, single?: string, double?: string, escaped?: string) =>
  single !== undefined ? (part.startsWith('$') && single.includes('\\') ? `$${single}` : single)
    : double !== undefined ? double.replace(/\\([$`"\\])/g, '$1') : escaped!)
/** Whether text in which a quote is a plain character, in double quotes or a here-document, has the shell fill in. */
const fillsIn = (text: string) => EXPANDS.test(text.replace(SPECIAL, '0').replace(/\\[\s\S]|['"]/g, ' '))
/** A URL to somewhere else names no path here, but a `${X:-…}` in it is a word of its own. */
const dropUrls = (text: string) => text.replace(REMOTE_URL, (_, rest: string) => ` ${rest.match(/\$\{[^}]*?[-=+?][^}]*\}?/g)?.join(' ') ?? ''} `)

/** A command line read as the shell reads it: its text for words (a comment's quotes are plain text there), its
 *  commands with the comments off, and the bodies of its here-documents. A `\` at a line's end joins the next line. */
function lex(command: string): { shell: string; code: string; bodies: Body[] } {
  const bodies: Body[] = []
  const pending: { end: string; strip: boolean; quoted: boolean }[] = []
  let shell = ''
  let code = ''
  let last = 0
  LEXEME.lastIndex = 0
  for (let lexeme = LEXEME.exec(command); lexeme; lexeme = LEXEME.exec(command)) {
    const [token, strip, single, double, escaped, word] = lexeme
    const gap = command.slice(last, lexeme.index)
    const comment = token.startsWith('#')
    const kept = comment ? '' : token.startsWith("'") ? token : token.replace(/\\[\s\S]/g, (pair) => (pair === '\\\n' ? '' : pair))
    shell += gap + (comment ? token.replace(/['"`\\]/g, ' ') : kept)
    code += gap + kept
    last = LEXEME.lastIndex
    const end = single ?? double ?? word
    if (end !== undefined) pending.push({ end, strip: strip === '-', quoted: !word || !!escaped })
    if (token !== '\n') continue
    for (const doc of pending.splice(0)) {
      const start = last
      let stop = -1
      while (last < command.length && stop < 0) {
        const eol = command.indexOf('\n', last) < 0 ? command.length : command.indexOf('\n', last)
        const text = command.slice(last, eol)
        if ((doc.strip ? text.replace(/^\t+/, '') : text) === doc.end) stop = last
        last = eol + 1
      }
      // A body with no end line may run where the shell reads commands again: its `$` is then the shell's.
      bodies.push({ text: dropUrls(command.slice(start, stop < 0 ? undefined : stop)), quoted: doc.quoted && stop >= 0 })
    }
    LEXEME.lastIndex = last = Math.min(last, command.length)
  }
  return { shell: dropUrls(shell + command.slice(last)), code: code + command.slice(last), bodies }
}

/** A command line's words, and its commands with the quotes and comments off, where a `cd` is looked for. A
 *  here-document's body is read as text, its quotes plain characters. A line that hands its text to another shell is
 *  read again with its quotes off, as that shell reads it, and what the first shell matches or lists in braces is then
 *  the script's own text; the words the first shell passes on as written are read as well. */
function read(command: string, script?: { stuck: boolean }): { words: Word[]; commands: string } {
  const { shell, code, bodies } = lex(command)
  const words = (shell.match(WORD) ?? []).map((written): Word => {
    const word = script ? written : written.replace(SPECIAL, '0')
    // Quoted text the shell passes on as written is left as its bare quotes, so a `$` before it still reads as `$'…'`.
    const live = word.replace(QUOTED, (part) =>
      (part.startsWith('\\') || (part.startsWith('"') && fillsIn(part)) ? part : part.charAt(0).repeat(2)))
    const filled = EXPANDS.test(live) || !!live.match(BRACES)?.some((group) => /,|\.\./.test(group))
      || (!!script?.stuck && /[*?[{},]/.test(live))
    return { written, text: unquote(word), filled, globbed: GLOB.test(live) }
  })
  for (const body of bodies) {
    for (const token of body.text.match(/\S+/g) ?? []) {
      const text = body.quoted ? token : token.replace(/\\([$`\\])/g, '$1')
      words.push({ written: token, text, filled: !body.quoted && fillsIn(token), globbed: false })
    }
  }
  if (script || !SCRIPTED.test(unquote(code))) return { words, commands: unquote(code) }
  const stuck = /[*?[{},]/.test(code.replace(QUOTED, ' '))
  const inner = read(unquote([shell.replace(ONE_WORD_SCRIPT, '$1;'), ...bodies.map((body) => body.text)].join('\n')), { stuck })
  return { words: [...inner.words, ...words.filter((word) => !word.filled && !word.globbed)], commands: inner.commands }
}

interface Root { repo: RepoRecord; segments: string[] }

/** The folders a path names from `base`, `..` and `.` worked out, and whether a `..` climbed above `floor`. */
function walk(path: string, base: readonly string[], floor: number): { at: string[]; climbed: boolean } {
  const absolute = path.startsWith('/')
  const at = absolute ? [] : [...base]
  let climbed = false
  for (const segment of path.split('/')) {
    if (segment === '..') {
      at.pop()
      climbed ||= !absolute && at.length < floor
    } else if (segment !== '' && segment !== '.') at.push(segment)
  }
  return { at, climbed }
}

const isUnder = (segments: readonly string[], root: readonly string[]) =>
  root.length <= segments.length && root.every((name, k) => segments[k] === name)

/** Whether a piece climbs from wherever its wildcard matched: a `..` after it, or a `.*`, which matches `..` in some
 *  shells. */
function climbsAfterGlob(piece: string): boolean {
  const glob = piece.search(GLOB)
  return glob >= 0 && /(?:^|\/)\.(?:\.(?:\/|$)|[^/]*?(?:[*?]|\[[^\][]*\]))/.test(piece.slice(piece.lastIndexOf('/', glob) + 1))
}

/** Where a call's paths land: those its command lines name (or `text`, one command line), and those its tool takes. */
export function approvalReach(targets: string | Omit<ApprovalTargets, 'whole'>, repoId: string, repos: readonly RepoRecord[]): ApprovalReach {
  const { commands, paths } = typeof targets === 'string' ? { commands: [targets], paths: [] } : targets
  const roots: Root[] = repos.flatMap((repo) => {
    const segments = (repo.canonical_path ?? '').split('/').filter(Boolean)
    return segments.length ? [{ repo, segments }] : []
  })
  const home = roots.find((root) => root.repo.repo_id === repoId)
  const floor = home?.segments.length ?? 0
  // The deepest registered root that holds a folder: a repository can sit inside another's directory.
  const deepestFirst = [...roots].sort((a, b) => b.segments.length - a.segments.length)
  const owner = (at: readonly string[]) => deepestFirst.find((root) => isUnder(at, root.segments))
  const otherRepos = new Map<string, { label: string; path: string }>()
  const outside = new Set<string>()
  const unplaced = new Set<string>()
  const lines = commands.map((command) => read(command))

  // Where a relative path may run from: the root, and each folder of this repository a `cd` goes to. A `cd` can fail
  // or end with its subshell, so a path is read from all of them.
  const bases: string[][] = home ? [home.segments] : []
  for (const line of lines) {
    for (const [, step, folder] of line.commands.matchAll(CHANGE_OF_FOLDER)) {
      if (!folder || folder === '-' || /^[+-]\d+$/.test(folder)) unplaced.add(step!)
      else if (bases.length < MAX_BASES && !/[$`~*?[{]/.test(folder)) {
        for (const base of bases.slice()) {
          const { at, climbed } = walk(folder, base, floor)
          if (!climbed && owner(at) === home && bases.length < MAX_BASES
            && !bases.some((known) => known.join('/') === at.join('/'))) bases.push(at)
        }
      }
    }
  }

  /** Where a path lands from each base. A path in a word only the shell can finish names its repository, no more. */
  const placed = new Set<string>()
  const place = (path: string, written: string, cut: boolean, finished: boolean) => {
    const key = `${+cut}${+finished}${path}\n${written}`
    if (placed.has(key)) return
    placed.add(key)
    if (path.startsWith('~')) return void unplaced.add(written)
    const folder = cut ? path.slice(0, path.lastIndexOf('/') + 1) : path
    for (const base of path.startsWith('/') ? [[]] : bases) {
      const { at, climbed } = walk(folder, base, floor)
      const root = owner(at)
      if (root && root !== home) otherRepos.set(root.repo.repo_id, { label: root.repo.label, path: root.repo.canonical_path })
      if (!finished) continue
      if (climbed) unplaced.add(written)
      else if (!root && !cut && !HARMLESS.has(`/${at.join('/')}`)) outside.add(written)
      // A wildcard matches names below its folder: they stay in it, but for a registered root it can match.
      else if (cut && roots.some((other) => other !== home && other.segments.length > at.length && isUnder(other.segments, at))) unplaced.add(written)
      else if (cut && !root) outside.add(written)
    }
  }

  for (const { written, text, filled, globbed } of lines.flatMap((line) => line.words)) {
    if (!filled && !globbed) {
      const rest = text.replace(FILE_URL, (_, path?: string) => {
        if (path) place(path, path, false, true)
        return ' '
      })
      for (const [piece] of rest.matchAll(PIECE)) {
        const path = piece.slice(GLUED.exec(piece)?.[0].length ?? 0).replace(/^@/, '')
        // A program's own variable opening a path: `"$HOME/.aws"` in a `python3 -c` program.
        if (PROGRAM_VARIABLE.test(path)) unplaced.add(path)
        else place(path, path, false, true)
      }
      continue
    }
    // A quoted wildcard is plain text, which the runs below cannot tell from the shell's own.
    if (filled || text.split(/[\s'"`;&|<>(){}=:,\\]/).some((piece) => climbsAfterGlob(piece.slice(GLUED.exec(piece)?.[0].length ?? 0).replace(/^@/, '')))
      || written.match(QUOTED)?.some((part) => /[*?[]/.test(part))) unplaced.add(written)
    if (globbed && GLOB_FIRST.test(text)) place('', written, true, true)
    for (const match of text.matchAll(RUN)) {
      const run = match[2]!
      if (match[1]) {
        if (!filled && run.includes('/')) unplaced.add(`${match[1]}${run}`)
      } else if (!(filled ? CONTINUES : CONTINUES_MATCH).test(text.charAt(match.index - 1))) {
        const path = run.slice(GLUED.exec(run)?.[0].length ?? 0)
        const end = match.index + match[0].length
        // A backquote that closes its command ends the path there; only one that opens a command goes on from it.
        const closes = text.charAt(end) === '`' && (text.slice(0, end).match(/`/g)?.length ?? 0) % 2 === 1
        const cut = !closes && (filled ? CUT : STUCK).test(text.charAt(end))
        place(path, cut && !filled ? written : path, cut, !filled)
      }
    }
  }
  for (const path of paths) {
    if (PROGRAM_VARIABLE.test(path)) unplaced.add(path)
    const stop = path.search(/[*?[]/)
    place(stop < 0 ? path : path.slice(0, stop), path, stop >= 0, true)
  }
  return { otherRepos: [...otherRepos.values()], outside: [...outside].slice(0, 5), unplaced: [...unplaced].slice(0, 5) }
}
