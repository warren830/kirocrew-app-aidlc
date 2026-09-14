/**
 * AI-DLC Console — dashboard UI.
 *
 * Hand-authored ESM, no build step: KiroCrew's AppHost does
 * `React.lazy(() => import('/apps/aidlc-console/ui/index.mjs'))`, so this file
 * must have a DEFAULT export that is a React component. Bare specifiers below
 * resolve through the host's import map (`window.__kirocrew_modules`), which is
 * what keeps this sharing the host's single React instance.
 *
 * Validate after every edit — a parse error surfaces only as
 * "Failed to load AI-DLC Console: Unexpected token":
 *
 *   node --check ui/index.mjs
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { jsx as _jsx, jsxs as _jsxs } from 'react/jsx-runtime'

const API_BASE = '/api/apps/aidlc-console'
const POLL_MS = 30000

/** Terse element helper so the tree below stays readable without JSX compilation. */
const el = (type, props, ...kids) => {
  const p = props || {}
  if (kids.length === 0) return _jsx(type, p)
  if (kids.length === 1) return _jsx(type, { ...p, children: kids[0] })
  return _jsxs(type, { ...p, children: kids })
}

/**
 * Host components when this KiroCrew is new enough, small fallbacks otherwise.
 * Feature-detected rather than imported outright so an older host renders a
 * plain-but-working page instead of failing the whole module load.
 */
const host = (typeof window !== 'undefined' && window.__kirocrew_modules) || {}
const ui = host['@kirocrew/ui'] || {}
const sdk = host['@kirocrew/app-sdk'] || {}

const T = {
  bg: 'var(--bg, #0f1014)',
  card: 'var(--card, #1a1b26)',
  border: 'var(--border, #2d2f3d)',
  text: 'var(--text, #e6e6ef)',
  muted: 'var(--muted, #8b8fa3)',
  accent: 'var(--accent, #7c3aed)',
  accentTint: 'rgba(124,58,237,.14)',
  ok: 'var(--ok, #16a34a)',
  danger: 'var(--danger, #dc2626)',
}

/** Stage checkbox mark -> presentation. Amber pills carry their own fg+bg, so
 *  they are safe in both themes; everything else rides a theme token. */
const STAGE_STYLE = {
  completed: { color: T.ok, label: 'done' },
  in_progress: { color: T.accent, label: 'running' },
  awaiting_approval: { color: '#b45309', bg: '#fef3c7', label: 'gate open' },
  revising: { color: T.danger, label: 'revising' },
  skipped: { color: T.muted, label: 'skipped' },
  not_started: { color: T.border, label: 'pending' },
  unknown: { color: T.muted, label: '?' },
}

const SEVERITY_STYLE = {
  action: { color: '#b45309', bg: '#fef3c7' },
  warn: { color: T.danger, bg: 'rgba(220,38,38,.12)' },
  info: { color: T.muted, bg: 'transparent' },
}

// --------------------------------------------------------------------------- //
// primitives
// --------------------------------------------------------------------------- //

function Pill({ text, color, bg, title }) {
  return el(
    'span',
    {
      title: title || undefined,
      style: {
        background: bg || T.accentTint,
        color: color || T.text,
        padding: '2px 8px',
        borderRadius: '9999px',
        fontSize: '10px',
        fontWeight: 600,
        whiteSpace: 'nowrap',
      },
    },
    text
  )
}

function Card({ children, style }) {
  if (ui.Card) return el(ui.Card, { children })
  return el(
    'div',
    {
      style: {
        background: T.card,
        border: `1px solid ${T.border}`,
        borderRadius: '6px',
        padding: '14px',
        marginBottom: '12px',
        ...(style || {}),
      },
    },
    children
  )
}

function Button({ onClick, disabled, children, tone }) {
  if (ui.Btn) return el(ui.Btn, { onClick, disabled, children })
  return el(
    'button',
    {
      onClick,
      disabled,
      style: {
        background: tone === 'solid' ? T.accent : 'transparent',
        color: tone === 'solid' ? '#fff' : disabled ? T.muted : T.accent,
        border: `1px solid ${tone === 'solid' ? T.accent : T.border}`,
        padding: '5px 14px',
        borderRadius: '9999px',
        fontSize: '11px',
        fontWeight: 500,
        cursor: disabled ? 'default' : 'pointer',
      },
    },
    children
  )
}

function TextInput({ value, onChange, placeholder, onEnter }) {
  const common = {
    value,
    placeholder,
    onChange: (e) => onChange(e.target.value),
    onKeyDown: (e) => {
      if (e.key === 'Enter' && onEnter) onEnter()
    },
  }
  if (ui.Input) return el(ui.Input, common)
  return el('input', {
    ...common,
    style: {
      flex: 1,
      minWidth: 0,
      background: T.bg,
      color: T.text,
      border: `1px solid ${T.border}`,
      borderRadius: '6px',
      padding: '6px 10px',
      fontSize: '12px',
    },
  })
}

function Stat({ label, value, tone }) {
  return el(
    'div',
    {
      style: {
        background: T.card,
        border: `1px solid ${T.border}`,
        borderRadius: '6px',
        padding: '10px 14px',
        minWidth: '110px',
      },
    },
    el('div', { style: { fontSize: '20px', fontWeight: 600, color: tone || T.text } }, String(value)),
    el('div', { style: { fontSize: '11px', color: T.muted, marginTop: '2px' } }, label)
  )
}

function Row({ children, style }) {
  return el(
    'div',
    { style: { display: 'flex', alignItems: 'center', gap: '10px', ...(style || {}) } },
    children
  )
}

// --------------------------------------------------------------------------- //
// findings + stages
// --------------------------------------------------------------------------- //

function Findings({ findings }) {
  if (!findings || findings.length === 0) return null
  const kids = findings.map((f, i) => {
    const s = SEVERITY_STYLE[f.severity] || SEVERITY_STYLE.info
    return el(
      'div',
      {
        key: `${f.code}-${i}`,
        style: {
          display: 'flex',
          gap: '8px',
          alignItems: 'flex-start',
          padding: '5px 0',
          fontSize: '11px',
          color: T.text,
        },
      },
      el(Pill, { text: f.severity, color: s.color, bg: s.bg }),
      el('span', { style: { color: T.muted, lineHeight: 1.5 } }, f.message)
    )
  })
  return el('div', { style: { marginTop: '8px' } }, kids)
}

function StageGrid({ stages }) {
  if (!stages || stages.length === 0) return null
  const byPhase = new Map()
  for (const s of stages) {
    const key = s.phase || 'unphased'
    if (!byPhase.has(key)) byPhase.set(key, [])
    byPhase.get(key).push(s)
  }
  const columns = [...byPhase.entries()].map(([phase, list]) =>
    el(
      'div',
      { key: phase, style: { minWidth: '150px', flex: '1 1 150px' } },
      el(
        'div',
        {
          style: {
            fontSize: '10px',
            fontWeight: 600,
            color: T.muted,
            textTransform: 'uppercase',
            letterSpacing: '.04em',
            marginBottom: '6px',
          },
        },
        phase
      ),
      el(
        'div',
        { style: { display: 'flex', flexDirection: 'column', gap: '3px' } },
        list.map((s) => {
          const st = STAGE_STYLE[s.state] || STAGE_STYLE.unknown
          return el(
            'div',
            {
              key: s.slug,
              title: `${s.slug} — ${st.label}`,
              style: { display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px' },
            },
            el('span', {
              style: {
                width: '7px',
                height: '7px',
                borderRadius: '9999px',
                background: st.bg || st.color,
                flex: '0 0 auto',
              },
            }),
            el(
              'span',
              {
                style: {
                  color: s.state === 'not_started' ? T.muted : T.text,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                },
              },
              s.slug
            )
          )
        })
      )
    )
  )
  return el(
    'div',
    { style: { display: 'flex', flexWrap: 'wrap', gap: '18px', marginTop: '10px' } },
    columns
  )
}

function ProgressBar({ counts }) {
  const total = (counts && counts.total) || 0
  if (!total) return null
  const seg = (state, n) =>
    n > 0
      ? el('div', {
          key: state,
          title: `${n} ${STAGE_STYLE[state].label}`,
          style: {
            width: `${(n / total) * 100}%`,
            background: STAGE_STYLE[state].bg || STAGE_STYLE[state].color,
          },
        })
      : null
  const order = ['completed', 'skipped', 'in_progress', 'awaiting_approval', 'revising', 'not_started']
  return el(
    'div',
    {
      style: {
        display: 'flex',
        height: '5px',
        borderRadius: '9999px',
        overflow: 'hidden',
        background: T.border,
        marginTop: '8px',
      },
    },
    order.map((k) => seg(k, counts[k] || 0)).filter(Boolean)
  )
}

// --------------------------------------------------------------------------- //
// intent
// --------------------------------------------------------------------------- //

function IntentRow({ intent }) {
  const [open, setOpen] = useState(false)
  const counts = intent.counts || {}
  const gates = counts.awaiting_approval || 0
  const problems = (intent.findings || []).filter((f) => f.severity !== 'info')

  const header = el(
    Row,
    { style: { flexWrap: 'wrap' } },
    el(
      'button',
      {
        onClick: () => setOpen(!open),
        style: {
          background: 'transparent',
          border: 'none',
          color: T.accent,
          fontSize: '12px',
          fontWeight: 600,
          cursor: 'pointer',
          padding: 0,
        },
      },
      `${open ? '▾' : '▸'} ${intent.slug}`
    ),
    intent.scope ? el(Pill, { text: intent.scope }) : null,
    intent.is_active ? el(Pill, { text: 'active', color: T.accent, bg: T.accentTint }) : null,
    gates > 0
      ? el(Pill, {
          text: `${gates} gate${gates > 1 ? 's' : ''} open`,
          color: '#b45309',
          bg: '#fef3c7',
        })
      : null,
    problems.length > 0
      ? el(Pill, {
          text: `${problems.length} finding${problems.length > 1 ? 's' : ''}`,
          color: T.danger,
          bg: 'rgba(220,38,38,.12)',
        })
      : null,
    el(
      'span',
      { style: { marginLeft: 'auto', fontSize: '11px', color: T.muted, whiteSpace: 'nowrap' } },
      `${counts.done || 0}/${counts.total || 0} stages`
    )
  )

  const meta = el(
    'div',
    { style: { fontSize: '11px', color: T.muted, marginTop: '4px', lineHeight: 1.6 } },
    intent.error
      ? el('span', { style: { color: T.danger } }, intent.error)
      : el(
          'span',
          {},
          `${intent.lifecycle_phase || '—'} · ${intent.current_stage || '—'}`,
          intent.status ? ` · ${intent.status}` : '',
          intent.updated ? ` · updated ${intent.updated}` : ''
        )
  )

  if (!open) return el('div', { style: { padding: '8px 0', borderBottom: `1px solid ${T.border}` } }, header, meta, el(ProgressBar, { counts }))

  return el(
    'div',
    { style: { padding: '8px 0', borderBottom: `1px solid ${T.border}` } },
    header,
    meta,
    el(ProgressBar, { counts }),
    intent.project
      ? el(
          'div',
          { style: { fontSize: '11px', color: T.text, marginTop: '8px', lineHeight: 1.6 } },
          intent.project
        )
      : null,
    intent.next_action
      ? el(
          'div',
          { style: { fontSize: '11px', color: T.muted, marginTop: '6px' } },
          `Next: ${intent.next_action}`
        )
      : null,
    el(Findings, { findings: intent.findings }),
    el(StageGrid, { stages: intent.stages })
  )
}

function RepoCard({ repo, onForget }) {
  const intents = repo.intents || []
  const engines = (repo.engines || []).map((e) => e.harness || e.dir).join(', ')

  const head = el(
    Row,
    { style: { marginBottom: '8px', flexWrap: 'wrap' } },
    el('div', { style: { fontSize: '13px', fontWeight: 600, color: T.accent } }, repo.label),
    repo.available
      ? el(Pill, { text: engines || 'no engine', title: repo.path })
      : el(Pill, { text: 'unavailable', color: T.danger, bg: 'rgba(220,38,38,.12)' }),
    repo.layout === 'legacy' ? el(Pill, { text: 'legacy layout' }) : null,
    el('span', { style: { marginLeft: 'auto' } }, el(Button, { onClick: () => onForget(repo.id) }, 'Forget'))
  )

  const path = el(
    'div',
    {
      style: {
        fontSize: '10px',
        color: T.muted,
        marginBottom: '6px',
        wordBreak: 'break-all',
      },
    },
    repo.path
  )

  let body
  if (!repo.available) {
    body = el('div', { style: { fontSize: '11px', color: T.danger } }, repo.error || 'path unreadable')
  } else if (intents.length === 0) {
    body = el('div', { style: { fontSize: '11px', color: T.muted } }, 'No intents yet — run /aidlc in this repo.')
  } else {
    body = intents.map((i) =>
      el(IntentRow, { key: `${i.space || '-'}/${i.dir_name}`, intent: i })
    )
  }

  return el(Card, {}, el('div', {}, head, path, body))
}

// --------------------------------------------------------------------------- //
// page
// --------------------------------------------------------------------------- //

export default function AidlcConsole() {
  const api = sdk.useAppApi ? sdk.useAppApi() : null
  const [board, setBoard] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [newPath, setNewPath] = useState('')
  const [addError, setAddError] = useState('')

  const request = useCallback(
    async (path, init) => {
      const url = `${API_BASE}${path}`
      if (api && !init) return api.get(url)
      if (api && init && init.method === 'POST' && api.post) return api.post(url, init.body)
      if (api && init && init.method === 'DELETE' && api.delete) return api.delete(url)
      const resp = await fetch(url, {
        method: (init && init.method) || 'GET',
        headers: init && init.body ? { 'Content-Type': 'application/json' } : undefined,
        body: init && init.body ? JSON.stringify(init.body) : undefined,
      })
      const text = await resp.text()
      const payload = text ? JSON.parse(text) : {}
      if (!resp.ok) throw new Error(payload.error || `HTTP ${resp.status}`)
      return payload
    },
    [api]
  )

  const load = useCallback(async () => {
    try {
      setBoard(await request('/board'))
      setError('')
    } catch (e) {
      setError(e && e.message ? e.message : String(e))
    }
  }, [request])

  useEffect(() => {
    load()
    const timer = setInterval(load, POLL_MS)
    return () => clearInterval(timer)
  }, [load])

  const addRepo = useCallback(async () => {
    const path = newPath.trim()
    if (!path) return
    setBusy(true)
    setAddError('')
    try {
      await request('/repos', { method: 'POST', body: { path } })
      setNewPath('')
      await load()
    } catch (e) {
      setAddError(e && e.message ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [newPath, request, load])

  const forget = useCallback(
    async (id) => {
      setBusy(true)
      try {
        await request(`/repos/${id}`, { method: 'DELETE' })
        await load()
      } catch (e) {
        setAddError(e && e.message ? e.message : String(e))
      } finally {
        setBusy(false)
      }
    },
    [request, load]
  )

  const totals = (board && board.totals) || {}
  const repos = useMemo(() => (board && board.repos) || [], [board])

  const header = el(
    Row,
    { style: { marginBottom: '16px', flexWrap: 'wrap' } },
    el('h2', { style: { margin: 0, fontSize: '18px', color: T.text } }, 'AI-DLC'),
    el(Pill, { text: 'read-only projection', title: 'This app never writes into an AI-DLC tree' }),
    el(
      'span',
      { style: { marginLeft: 'auto' } },
      el(Button, { onClick: load, disabled: busy }, '↻ Refresh')
    )
  )

  const stats = el(
    'div',
    { style: { display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '16px' } },
    el(Stat, { label: 'Repos', value: totals.repos || 0 }),
    el(Stat, { label: 'Intents', value: totals.intents || 0 }),
    el(Stat, { label: 'In flight', value: totals.in_flight || 0, tone: T.accent }),
    el(Stat, {
      label: 'Gates open',
      value: totals.awaiting_approval || 0,
      tone: (totals.awaiting_approval || 0) > 0 ? '#b45309' : undefined,
    }),
    el(Stat, {
      label: 'Findings',
      value: totals.findings || 0,
      tone: (totals.findings || 0) > 0 ? T.danger : undefined,
    })
  )

  const adder = el(
    Card,
    {},
    el(
      'div',
      {},
      el(
        'div',
        { style: { fontSize: '12px', fontWeight: 600, color: T.text, marginBottom: '8px' } },
        'Register a repository'
      ),
      el(
        Row,
        {},
        el(TextInput, {
          value: newPath,
          onChange: setNewPath,
          onEnter: addRepo,
          placeholder: '/absolute/path/to/repo',
        }),
        el(Button, { onClick: addRepo, disabled: busy, tone: 'solid' }, busy ? 'Working…' : 'Add')
      ),
      addError
        ? el('div', { style: { fontSize: '11px', color: T.danger, marginTop: '8px' } }, addError)
        : el(
            'div',
            { style: { fontSize: '10px', color: T.muted, marginTop: '8px' } },
            'Needs an AI-DLC install: an engine dir with tools/data/harness.json, an aidlc/spaces/ workspace, or a legacy aidlc-docs/aidlc-state.md.'
          )
    )
  )

  let list
  if (error) {
    list = el(
      Card,
      {},
      el('div', { style: { fontSize: '12px', color: T.danger } }, `Could not load the board: ${error}`)
    )
  } else if (!board) {
    list = el(Card, {}, el('div', { style: { fontSize: '12px', color: T.muted } }, 'Loading…'))
  } else if (repos.length === 0) {
    list = el(
      Card,
      {},
      el(
        'div',
        { style: { fontSize: '12px', color: T.muted } },
        'No repositories registered yet. Add one above to see its intents.'
      )
    )
  } else {
    list = repos.map((r) => el(RepoCard, { key: r.id, repo: r, onForget: forget }))
  }

  return el(
    'div',
    { style: { padding: '20px 24px', overflowY: 'auto', flex: 1, minHeight: 0, color: T.text } },
    header,
    stats,
    adder,
    list
  )
}
