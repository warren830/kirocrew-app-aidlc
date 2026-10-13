/**
 * What these tests pin is provenance and the export's honesty, not layout.
 *
 * The three failures they are here to catch:
 *   - a Studio-derived row rendered with AI-DLC's own audit sentence (or the reverse);
 *   - an Evidence drawer that shows an empty box where an audit block would be, instead of saying why
 *     there is none;
 *   - an export that fetches, or that offers to include prompt bodies, before the user has read what is
 *     removed and before the server-side setting allows it.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { apiCalls, setApiRoutes } from '../test/stubs/app-sdk'
import { EMPTY_ROUTE } from '../lib/route'
import type { ActivityResponse, SettingsResponse, TimelineEntry } from '../lib/types'
import { ActivityView } from './ActivityView'
import { ExportPanel } from './ExportPanel'

const entry = (over: Partial<TimelineEntry>): TimelineEntry => ({
  id: 1,
  at: '2026-09-05T10:00:00+00:00',
  source: 'studio',
  kind: 'action.created',
  message_key: 'activity.action.created',
  params: {},
  severity: 'info',
  refs: {},
  raw: null,
  ...over,
})

const STUDIO_ROW = entry({ id: 11, kind: 'action.created', message_key: 'activity.action.created' })
const AIDLC_ROW = entry({
  id: null,
  at: '2026-09-05T10:05:00+00:00',
  source: 'aidlc',
  kind: 'GATE_APPROVED',
  message_key: 'audit.GATE_APPROVED',
  params: { stage: 'functional-design', event: 'GATE_APPROVED' },
  refs: { audit: { shard: 'aidlc-audit-2026-09.md', pos: 42, event: 'GATE_APPROVED' } },
  raw: '## Gate Approved\n**Timestamp**: 2026-09-05T10:05:00Z\n**Event**: GATE_APPROVED\n',
})
const KIROCREW_ROW = entry({
  id: 12,
  at: '2026-09-05T10:02:00+00:00',
  source: 'kirocrew',
  kind: 'session.bound',
  message_key: 'activity.session.bound',
  refs: { session_key: 'ses_abc' },
})
const GIT_ROW = entry({
  id: 13,
  at: '2026-09-05T10:03:00+00:00',
  source: 'git',
  kind: 'engine.run',
  message_key: 'activity.engine.run',
  refs: { git: { sha: 'deadbeefcafe' } },
})
/** Source says Studio, message key claims an AI-DLC audit event. The two cannot both be true. */
const MISMATCHED_ROW = entry({
  id: 14,
  at: '2026-09-05T10:04:00+00:00',
  source: 'studio',
  kind: 'action.created',
  message_key: 'audit.GATE_APPROVED',
})

const SETTINGS: SettingsResponse = {
  settings: {
    locale: 'auto',
    density: 'compact',
    queue_organize: 'priority',
    global_concurrency_cap: 2,
    slack: { enabled: false, muted_repo_ids: [] },
    diagnostics: { retention_days: 30, export_include_human_text: false },
    human_text_retention_days: 30,
    advisor: { enabled: true, auto_draft_repo_ids: [] },
    installer: { run_doctor_after_install: false },
    notifications: { dashboard: true },
  },
  capabilities: {
    slack: { available: true, reason: null },
    advisor: { available: true, reason: null },
    slack_quick_actions: { available: false, reason: 'host_seam_unavailable' },
    grouped_answers: { available: false, reason: 's1_s2_unverified' },
  },
  versions: { studio: '1.0.0', bundled_engine: '2.7.1', min_kirocrew: '0.3.0', host: '0.5.0' },
  updated_at: '2026-09-05T09:00:00+00:00',
}

function activityRoutes(items: TimelineEntry[], settings: SettingsResponse = SETTINGS) {
  const response: ActivityResponse = { items, next_cursor: null }
  setApiRoutes({
    'GET /api/apps/aidlc-studio/activity*': () => response,
    'GET /api/apps/aidlc-studio/settings': () => settings,
    'GET /api/apps/aidlc-studio/diagnostics*': () => ({
      generated_at: '2026-09-05T11:00:00+00:00',
      redacted: true,
      health: {},
      settings: settings.settings,
      repos: [],
      live_actions: [],
      leases: [],
      recent_transactions: [],
      recent_activity: [],
      breakers: [],
    }),
  })
}

function renderActivity() {
  const go = vi.fn()
  render(<ActivityView route={{ ...EMPTY_ROUTE, view: 'activity' }} go={go} />)
  return { go }
}

/** The `<li>` for one row, found through the message it renders. */
function rowFor(text: string | RegExp): HTMLElement {
  const paragraph = screen.getByText(text)
  const li = paragraph.closest('li.studio-tlrow')
  if (!li) throw new Error('row not found')
  return li as HTMLElement
}

beforeEach(() => {
  // jsdom implements neither, and the export holds its bundle in an object URL.
  Object.defineProperty(URL, 'createObjectURL', { writable: true, value: vi.fn(() => 'blob:aidlc-test') })
  Object.defineProperty(URL, 'revokeObjectURL', { writable: true, value: vi.fn() })
})

describe('Timeline provenance', () => {
  it('names the source of every row and never presents a Studio row as an AI-DLC audit event', async () => {
    activityRoutes([AIDLC_ROW, GIT_ROW, MISMATCHED_ROW, KIROCREW_ROW, STUDIO_ROW])
    renderActivity()

    await waitFor(() => expect(screen.getByText('Gate approved.')).toBeInTheDocument())

    // All four provenances are present and each is named in words.
    expect(screen.getAllByText('AI-DLC').length).toBeGreaterThan(0)
    expect(screen.getAllByText('AI-DLC Studio').length).toBeGreaterThan(0)
    expect(screen.getAllByText('KiroCrew').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Git').length).toBeGreaterThan(0)

    const auditRow = rowFor('Gate approved.')
    expect(auditRow.dataset['source']).toBe('aidlc')
    expect(within(auditRow).getByText('AI-DLC audit event')).toBeInTheDocument()
    // The AI-DLC stage slug is shown verbatim, not translated.
    expect(within(auditRow).getByText('stage functional-design')).toBeInTheDocument()

    const studioRow = rowFor('Action created and queued for your decision.')
    expect(studioRow.dataset['source']).toBe('studio')
    expect(within(studioRow).getByText('Studio-derived')).toBeInTheDocument()
    expect(within(studioRow).queryByText('AI-DLC audit event')).toBeNull()
  })

  it('refuses to name a row whose source and message key disagree', async () => {
    activityRoutes([MISMATCHED_ROW])
    renderActivity()

    await waitFor(() =>
      expect(screen.getByText(/source and its message do not agree/)).toBeInTheDocument(),
    )
    // The audit sentence must not appear at all: that is the lie the guard exists to prevent.
    expect(screen.queryByText('Gate approved.')).toBeNull()
  })

  it('names whose event a row is by repository label and intent, not by session key', async () => {
    activityRoutes([
      entry({ id: 21, refs: { repo: { repo_id: 'r_1', label: 'checkout-web', space: 'default', intent_dir: '260904-gate-demo' },
                              session_key: 'dashboard:aidlc-studio-r_1-260904-gate-demo' } }),
      entry({ id: 22, at: '2026-09-05T10:01:00+00:00', refs: { repo: { repo_id: 'r_9', label: null } } }),
      entry({ id: 23, at: '2026-09-05T10:02:00+00:00', kind: 'repo.removed', message_key: 'activity.repo.removed',
              params: { label: 'old-shop' }, refs: { repo: { repo_id: 'r_8', label: null } } }),
    ])
    renderActivity()

    expect(await screen.findByText('checkout-web / 260904-gate-demo')).toBeInTheDocument()
    expect(screen.getByText('unregistered repository')).toBeInTheDocument()
    // a row that recorded the name keeps it after the repository is gone
    expect(screen.getByText('old-shop (unregistered repository)')).toBeInTheDocument()

    expect(screen.queryByText(/session dashboard:/)).toBeNull()
  })

  it('states the provenance rule on the page itself', async () => {
    activityRoutes([STUDIO_ROW])
    renderActivity()
    await waitFor(() =>
      expect(screen.getByText(/never presented as original AI-DLC audit events/)).toBeInTheDocument(),
    )
  })
})

describe('Evidence drawer', () => {
  it('shows the raw block and its file location for an AI-DLC row', async () => {
    activityRoutes([AIDLC_ROW])
    renderActivity()
    await waitFor(() => expect(screen.getByText('Gate approved.')).toBeInTheDocument())

    await userEvent.click(within(rowFor('Gate approved.')).getByRole('button', { name: /Evidence for/ }))

    const drawer = screen.getByRole('complementary', { name: 'Evidence drawer' })
    expect(within(drawer).getByText(/\*\*Event\*\*: GATE_APPROVED/)).toBeInTheDocument()
    expect(within(drawer).getAllByText('aidlc-audit-2026-09.md #42').length).toBeGreaterThan(0)
    expect(within(drawer).getByText(/Credentials and tokens are removed/)).toBeInTheDocument()
  })

  it('explains why a Studio row has no audit block instead of showing an empty one', async () => {
    activityRoutes([STUDIO_ROW])
    renderActivity()
    await waitFor(() =>
      expect(screen.getByText('Action created and queued for your decision.')).toBeInTheDocument(),
    )

    await userEvent.click(
      within(rowFor('Action created and queued for your decision.')).getByRole('button', {
        name: /Evidence for/,
      }),
    )

    const drawer = screen.getByRole('complementary', { name: 'Evidence drawer' })
    expect(within(drawer).getByText(/Studio rows carry no audit block/)).toBeInTheDocument()
  })

  // Focus left on <body> sends a keyboard reader back to the top of what may be a long timeline.
  it.each(['the Close button', 'Escape'])('returns focus to the Evidence button that opened it (%s)', async (how) => {
    activityRoutes([AIDLC_ROW, STUDIO_ROW])
    renderActivity()
    await waitFor(() => expect(screen.getByText('Gate approved.')).toBeInTheDocument())

    const opener = within(rowFor('Gate approved.')).getByRole('button', { name: /Evidence for/ })
    await userEvent.click(opener)
    const close = within(screen.getByRole('complementary', { name: 'Evidence drawer' })).getByRole('button', { name: 'Close' })
    expect(document.activeElement).toBe(close)

    if (how === 'Escape') await userEvent.keyboard('{Escape}')
    else await userEvent.click(close)
    expect(screen.queryByRole('complementary', { name: 'Evidence drawer' })).not.toBeInTheDocument()
    expect(document.activeElement).toBe(opener)
  })

  it('returns focus to the row the drawer was last pointed at', async () => {
    activityRoutes([AIDLC_ROW, STUDIO_ROW])
    renderActivity()
    await waitFor(() => expect(screen.getByText('Gate approved.')).toBeInTheDocument())

    await userEvent.click(within(rowFor('Gate approved.')).getByRole('button', { name: /Evidence for/ }))
    const second = within(rowFor('Action created and queued for your decision.')).getByRole('button', { name: /Evidence for/ })
    await userEvent.click(second)
    await userEvent.keyboard('{Escape}')
    expect(document.activeElement).toBe(second)
  })

  it('leaves focus in a filter when the drawer closes from outside it', async () => {
    activityRoutes([AIDLC_ROW])
    renderActivity()
    await waitFor(() => expect(screen.getByText('Gate approved.')).toBeInTheDocument())
    const stage = screen.getByLabelText('Stage')

    // Escape closes the drawer from anywhere on the page…
    await userEvent.click(within(rowFor('Gate approved.')).getByRole('button', { name: /Evidence for/ }))
    await userEvent.click(stage)
    await userEvent.keyboard('{Escape}')
    expect(screen.queryByRole('complementary', { name: 'Evidence drawer' })).not.toBeInTheDocument()
    expect(document.activeElement).toBe(stage)

    // …and a filter change drops it; neither pulls the reader out of the field they are typing in.
    await userEvent.click(within(rowFor('Gate approved.')).getByRole('button', { name: /Evidence for/ }))
    await userEvent.type(stage, 'f')
    expect(screen.queryByRole('complementary', { name: 'Evidence drawer' })).not.toBeInTheDocument()
    expect(document.activeElement).toBe(stage)
  })
})

describe('Activity filters', () => {
  it('sends the chosen severity to the server', async () => {
    activityRoutes([STUDIO_ROW])
    renderActivity()
    await waitFor(() =>
      expect(screen.getByText('Action created and queued for your decision.')).toBeInTheDocument(),
    )

    await userEvent.selectOptions(screen.getByLabelText('Severity'), 'blocking')

    await waitFor(() =>
      expect(apiCalls.some((call) => call.path.includes('severity=blocking'))).toBe(true),
    )
  })

  it('says AI-DLC audit rows need a repo and intent in scope', async () => {
    activityRoutes([STUDIO_ROW])
    renderActivity()
    await waitFor(() =>
      expect(screen.getByText('Action created and queued for your decision.')).toBeInTheDocument(),
    )

    await userEvent.selectOptions(screen.getByLabelText('Source'), 'aidlc')

    expect(screen.getByText(/read from one intent's audit shards/)).toBeInTheDocument()
  })
})

describe('Diagnostic export', () => {
  it('lists what it removes and fetches nothing until asked', async () => {
    activityRoutes([])
    render(<ExportPanel allowHumanText={false} />)

    expect(screen.getByText(/Credentials, tokens and secrets are removed/)).toBeInTheDocument()
    expect(screen.getByText(/Artifact contents are never included/)).toBeInTheDocument()
    expect(apiCalls.some((call) => call.path.includes('/diagnostics'))).toBe(false)

    await userEvent.click(screen.getByRole('button', { name: /Produce the export/ }))

    await waitFor(() =>
      expect(screen.getByRole('link', { name: /Download aidlc-studio-diagnostics-/ })).toBeInTheDocument(),
    )
    const call = apiCalls.find((c) => c.path.includes('/diagnostics'))
    expect(call?.path).toContain('export=1')
    expect(call?.path).not.toContain('include_human_text')
  })

  it('keeps the prompt-body option inert until the server-side setting allows it', async () => {
    activityRoutes([])
    render(<ExportPanel allowHumanText={false} />)

    const box = screen.getByRole('checkbox', { name: /Include prompt bodies/ })
    expect(box).toBeDisabled()
    expect(screen.getByText(/Allow it in Settings first/)).toBeInTheDocument()
  })

  it('asks for prompt bodies only when both the setting and the box agree', async () => {
    activityRoutes([])
    render(<ExportPanel allowHumanText />)

    await userEvent.click(screen.getByRole('checkbox', { name: /Include prompt bodies/ }))
    await userEvent.click(screen.getByRole('button', { name: /Produce the export/ }))

    await waitFor(() => {
      const call = apiCalls.find((c) => c.path.includes('/diagnostics'))
      expect(call?.path).toContain('include_human_text=1')
    })
  })
})
