/**
 * The Settings assertions that matter are all about refusals and separations.
 *
 *   - Night work window and the credit cap render as UNAVAILABLE *with the reason* and expose no control at
 *     all. A switch that the backend answers 409 to would advertise unattended runs Studio cannot perform.
 *   - The app version and the bundled AI-DLC version are two rows, never one string: a reader must not be
 *     able to conclude that upgrading the App upgraded their repositories.
 *   - A locale override writes both the server preference and the browser-local override, because the page
 *     reads the latter; writing only one is the "the setting did nothing" bug.
 *   - Slack per-repo mute patches `muted_repo_ids`, not a per-repo route that does not exist.
 *   - Drafting ahead is a per-repository *grant* (FR-ADV-010): ticking a repository patches
 *     `advisor.auto_draft_repo_ids` and nothing else, and the list is inert while the Advisor is
 *     unavailable — a tick that cannot produce a draft would record permission for something that never
 *     happens.
 */

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { apiCalls, setApiRoutes, StubApiError } from '../test/stubs/app-sdk'
import { EMPTY_ROUTE } from '../lib/route'
import { STUDIO_LOCALE_KEY } from '../lib/host'
import type { HealthResponse, RepoRecord, SettingsResponse } from '../lib/types'
import { SettingsView } from './SettingsView'

const SETTINGS: SettingsResponse = {
  settings: {
    locale: 'auto',
    density: 'compact',
    queue_organize: 'priority',
    global_concurrency_cap: 2,
    slack: { enabled: true, muted_repo_ids: [] },
    night_window: { enabled: false, start_local: '22:00', end_local: '06:00', turn_cap: 40, credit_cap: null },
    diagnostics: { retention_days: 30, export_include_human_text: false },
    human_text_retention_days: 30,
    advisor: { enabled: true, auto_draft_repo_ids: [] },
    installer: { run_doctor_after_install: false },
    notifications: { dashboard: true },
  },
  capabilities: {
    night_window: { available: false, reason: 'machine_lane_unavailable' },
    credit_cap: { available: false, reason: 'credits_unobservable' },
    slack: { available: true, reason: null },
    advisor: { available: true, reason: null },
    slack_quick_actions: { available: false, reason: 'host_seam_unavailable' },
    grouped_answers: { available: false, reason: 's1_s2_unverified' },
  },
  versions: { studio: '1.0.0', bundled_engine: '2.7.1', min_kirocrew: '0.3.0', host: '0.5.0-insider.9' },
  updated_at: '2026-09-05T09:00:00+00:00',
}

const HEALTH = {
  app: 'aidlc-studio',
  version: '1.0.0',
  bundled_engine_version: '2.7.1',
  min_kirocrew_version: '0.3.0',
  boot_id: 'abcdef0123456789',
  host_version: '0.5.0-insider.9',
  started_at: '2026-09-05T08:00:00+00:00',
  status: 'healthy',
  issues: [],
  storage: { path_hash: 'aa11', schema_version: 1, integrity: 'ok', wal: true },
  payload: { ok: true, engine_version: '2.7.1', file_count: 293, mismatches: 0 },
  host: { attached: true, capabilities: {} },
  tools: { bun: { found: true, path: '/usr/bin/bun', version: '1.2.0' }, git: { found: true, path: '/usr/bin/git', version: '2.45.0' } },
  reconciler: { running: true, last_tick_at: '2026-09-05T09:59:00+00:00', last_tick_ms: 12, repos_scanned: 3 },
  counts: { repos: 1, intents: 2, live_actions: 0, execution_leases: 0, admin_leases: 0 },
  machine_lane: { available: false, reason: 'machine_lane_unavailable' },
} as unknown as HealthResponse

const REPO = {
  repo_id: 'r_1',
  label: 'checkout-web',
  canonical_path: '/Users/x/work/checkout-web',
} as unknown as RepoRecord

/** The same settings with a different advisor leaf; the grant list is the server's, never local state. */
function advisorSettings(grants: string[], enabled = true): SettingsResponse {
  const advisor = { enabled, auto_draft_repo_ids: grants }
  return { ...SETTINGS, settings: { ...SETTINGS.settings, advisor } }
}

function routes(overrides: Record<string, (body: unknown, path: string) => unknown> = {}) {
  const puts: unknown[] = []
  setApiRoutes({
    'GET /api/apps/aidlc-studio/settings': () => SETTINGS,
    'PUT /api/apps/aidlc-studio/settings': (body) => {
      puts.push(body)
      return SETTINGS
    },
    'GET /api/apps/aidlc-studio/health': () => HEALTH,
    'GET /api/apps/aidlc-studio/repos*': () => ({
      repos: [REPO],
      totals: { repos: 1, unavailable: 0, open_actions: 0 },
    }),
    'GET /api/apps/aidlc-studio/migration/status': () => ({
      applied: false,
      result: null,
      preview_available: false,
      console: { installed: false, enabled: false },
    }),
    ...overrides,
  })
  return puts
}

async function renderSettings() {
  const go = vi.fn()
  render(<SettingsView route={{ ...EMPTY_ROUTE, view: 'settings' }} go={go} />)
  await waitFor(() => expect(screen.getByText('Queue organization')).toBeInTheDocument())
  return { go }
}

/** The preference row that carries this title. */
function row(title: string): HTMLElement {
  const label = screen.getByText(title)
  const element = label.closest('.studio-mrow')
  if (!element) throw new Error(`no row for ${title}`)
  return element as HTMLElement
}

beforeEach(() => {
  Object.defineProperty(URL, 'createObjectURL', { writable: true, value: vi.fn(() => 'blob:aidlc-test') })
  Object.defineProperty(URL, 'revokeObjectURL', { writable: true, value: vi.fn() })
})

describe('Automation and budgets', () => {
  it('shows the night window as unavailable with its reason and offers no enable control', async () => {
    routes()
    await renderSettings()

    const night = row('Night work window')
    expect(within(night).getByText(/Unavailable — no trusted machine lane is proven/)).toBeInTheDocument()
    // The whole point: no switch, no checkbox. Only the stored window bounds.
    expect(within(night).queryByRole('switch')).toBeNull()
    expect(within(night).queryByRole('checkbox')).toBeNull()
    expect(within(night).getByText('22:00 – 06:00 local')).toBeInTheDocument()
  })

  it('shows the credit cap as unavailable with its reason and offers no input', async () => {
    routes()
    await renderSettings()

    const credit = row('Credit cap')
    expect(within(credit).getByText(/Unavailable — credit use is not observable/)).toBeInTheDocument()
    expect(within(credit).queryByRole('spinbutton')).toBeNull()
    expect(within(credit).queryByRole('checkbox')).toBeNull()
  })

  it('commits a concurrency change on blur rather than per keystroke', async () => {
    const puts = routes()
    await renderSettings()

    const input = within(row('Global concurrency')).getByRole('spinbutton')
    await userEvent.clear(input)
    await userEvent.type(input, '4')
    expect(puts).toHaveLength(0)

    await userEvent.tab()
    await waitFor(() => expect(puts).toEqual([{ global_concurrency_cap: 4 }]))
  })
})

describe('Advisor', () => {
  it('says the model is inherited and shows no model id anywhere', async () => {
    routes()
    await renderSettings()

    const model = row('Advisor model')
    expect(within(model).getByText('Inherited')).toBeInTheDocument()
    // Nothing that looks like a model id may appear on the page.
    expect(document.body.textContent).not.toMatch(/claude-|gpt-|sonnet|haiku|opus/i)
  })

  it('patches auto_draft_repo_ids and nothing else when a repository is granted', async () => {
    const puts = routes()
    await renderSettings()

    await userEvent.click(screen.getByRole('checkbox', { name: 'Draft ahead in checkout-web' }))
    // Exactly this leaf: a grant must not carry `enabled` or any other advisor field along with it.
    await waitFor(() => expect(puts).toEqual([{ advisor: { auto_draft_repo_ids: ['r_1'] } }]))
  })

  it('removes just that repository when a grant is withdrawn', async () => {
    const puts = routes({
      'GET /api/apps/aidlc-studio/settings': () => advisorSettings(['r_1', 'r_other']),
    })
    await renderSettings()

    await userEvent.click(screen.getByRole('checkbox', { name: 'Draft ahead in checkout-web' }))
    // `r_other` is somebody else's grant: withdrawing one must not withdraw the rest.
    await waitFor(() => expect(puts).toEqual([{ advisor: { auto_draft_repo_ids: ['r_other'] } }]))
  })

  it('lists a grant for an unregistered repository and lets it be removed', async () => {
    const puts = routes({
      'GET /api/apps/aidlc-studio/settings': () => advisorSettings(['r_gone']),
    })
    await renderSettings()

    // Named rather than silently dropped: a re-registration under the same id would otherwise re-grant it.
    expect(screen.getByText('r_gone may draft ahead but is no longer registered.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: 'Draft ahead in r_gone' }))
    await waitFor(() => expect(puts).toEqual([{ advisor: { auto_draft_repo_ids: [] } }]))
  })

  it('does not call a live grant an orphan while the repository list is unread', async () => {
    routes({
      'GET /api/apps/aidlc-studio/settings': () => advisorSettings(['r_1']),
      'GET /api/apps/aidlc-studio/repos*': () => {
        throw new StubApiError(503, { error: 'the registry read failed', code: 'unavailable' })
      },
    })
    await renderSettings()

    const grant = row('Draft ahead, per repository')
    // `r_1` is registered and granted. An unread list is not an empty one: presenting a working grant as
    // "no longer registered" invites the owner to revoke it, which is the only action that row offers.
    expect(within(grant).queryByText(/no longer registered/)).toBeNull()
    expect(within(grant).queryByRole('checkbox')).toBeNull()
    expect(within(grant).getByText('Loading…')).toBeInTheDocument()
  })

  it('offers no live grant while the Advisor capability is unavailable', async () => {
    routes({
      'GET /api/apps/aidlc-studio/settings': () => ({
        ...SETTINGS,
        capabilities: {
          ...SETTINGS.capabilities,
          advisor: { available: false, reason: 'host_seam_unavailable' },
        },
      }),
    })
    await renderSettings()

    const grant = row('Draft ahead, per repository')
    expect(within(grant).getByRole('checkbox', { name: 'Draft ahead in checkout-web' })).toBeDisabled()
  })

  it('offers no live grant while the Advisor itself is switched off', async () => {
    routes({
      'GET /api/apps/aidlc-studio/settings': () => advisorSettings([], false),
    })
    await renderSettings()

    expect(screen.getByRole('checkbox', { name: 'Draft ahead in checkout-web' })).toBeDisabled()
  })
})

describe('Slack', () => {
  it('patches muted_repo_ids when a repository is muted', async () => {
    const puts = routes()
    await renderSettings()

    await userEvent.click(screen.getByRole('checkbox', { name: 'Mute checkout-web' }))
    await waitFor(() => expect(puts).toEqual([{ slack: { muted_repo_ids: ['r_1'] } }]))
  })

  it('states that quick actions are unavailable and offers no control', async () => {
    routes()
    await renderSettings()

    const quick = row('Slack quick actions')
    expect(within(quick).getByText(/Unavailable — the host offers no seam/)).toBeInTheDocument()
    expect(within(quick).queryByRole('checkbox')).toBeNull()
  })
})

describe('About', () => {
  it('shows the app version and the bundled AI-DLC version as separate facts', async () => {
    routes()
    await renderSettings()

    expect(screen.getByText('Studio app version')).toBeInTheDocument()
    expect(screen.getByText('1.0.0')).toBeInTheDocument()
    expect(screen.getByText('Bundled AI-DLC version')).toBeInTheDocument()
    expect(screen.getByText('2.7.1')).toBeInTheDocument()
    // Never one combined chip: the two numbers must not read as a single version.
    expect(screen.queryByText(/Studio 1\.0\.0 · AI-DLC/)).toBeNull()
  })
})

describe('Locale', () => {
  it('writes both the server preference and the browser-local override', async () => {
    const puts = routes()
    await renderSettings()

    await userEvent.selectOptions(screen.getByLabelText('Interface language'), 'zh-CN')

    await waitFor(() => expect(puts).toEqual([{ locale: 'zh-CN' }]))
    expect(localStorage.getItem(STUDIO_LOCALE_KEY)).toBe('zh-CN')
  })
})

describe('Queue organization', () => {
  it('mirrors the choice into localStorage so a new tab opens the same way', async () => {
    const puts = routes()
    await renderSettings()

    await userEvent.selectOptions(screen.getByLabelText('Order the queue by'), 'oldest')

    await waitFor(() => expect(puts).toEqual([{ queue_organize: 'oldest' }]))
    expect(localStorage.getItem('aidlc-studio:organize')).toBe('oldest')
  })
})

describe('A rejected patch', () => {
  it('names the key the backend refused', async () => {
    routes({
      'PUT /api/apps/aidlc-studio/settings': () => {
        throw new Error(
          'API 400: {"error":"turn cap out of range","code":"bad_body","details":{"key":"night_window.turn_cap","reason":"range"}}',
        )
      },
    })
    await renderSettings()

    const input = within(row('Turn cap per window')).getByRole('spinbutton')
    await userEvent.clear(input)
    await userEvent.type(input, '99')
    await userEvent.tab()

    await waitFor(() =>
      expect(screen.getByText(/rejected night_window\.turn_cap/)).toBeInTheDocument(),
    )
  })
})

describe('Migration', () => {
  it('renders nothing when there is no prototype and no migration on record', async () => {
    routes()
    await renderSettings()
    expect(screen.queryByText(/Migrate from the AI-DLC console prototype/)).toBeNull()
  })

  it('leads with the two-control-surfaces warning while both Apps are enabled', async () => {
    routes({
      'GET /api/apps/aidlc-studio/migration/status': () => ({
        applied: false,
        result: null,
        preview_available: true,
        console: { installed: true, enabled: true },
      }),
    })
    await renderSettings()

    await waitFor(() =>
      expect(screen.getByText('Two Apps can control the same repositories')).toBeInTheDocument(),
    )
    expect(screen.getByRole('button', { name: /Preview what moves/ })).toBeInTheDocument()
    // Nothing may be applied before a preview has been read.
    expect(screen.queryByRole('button', { name: /Apply the migration once/ })).toBeNull()
  })
})
