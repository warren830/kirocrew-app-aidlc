/**
 * The Settings assertions that matter are all about refusals and separations.
 *
 *   - There is no night work window or credit cap at all: no trusted machine lane exists, and a schedule or a
 *     budget for one would advertise unattended runs Studio cannot perform.
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
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { apiCalls, setApiRoutes, StubApiError } from '../test/stubs/app-sdk'
import { EMPTY_ROUTE } from '../lib/route'
import { STUDIO_LOCALE_KEY } from '../lib/host'
import { BusContext, type StreamEvent } from '../lib/sse'
import type { HealthResponse, RepoRecord, SettingsResponse, StudioEventType } from '../lib/types'
import { SettingsView } from './SettingsView'

const SETTINGS: SettingsResponse = {
  settings: {
    locale: 'auto',
    density: 'compact',
    queue_organize: 'priority',
    global_concurrency_cap: 2,
    slack: { enabled: true, muted_repo_ids: [] },
    diagnostics: { retention_days: 30, export_include_human_text: false },
    human_text_retention_days: 30,
    advisor: { enabled: true, auto_draft_repo_ids: [], model: '' },
    installer: { run_doctor_after_install: false },
    notifications: { dashboard: true },
    new_intent: { summary_confirmation: 'scope' },
  },
  capabilities: {
    slack: { available: true, reason: null },
    advisor: { available: true, reason: null },
    slack_quick_actions: { available: false, reason: 'host_seam_unavailable' },
    grouped_answers: { available: false, reason: 's1_s2_unverified' },
  },
  versions: { studio: '1.0.0', bundled_engine: '2.7.1', min_kirocrew: '0.3.0', host: '0.5.0-insider.9' },
  updated_at: '2026-09-05T09:00:00+00:00',
}

/** The host's `GET /api/models` rows, with the fields Studio reads plus one it ignores. */
const MODELS = [
  { model_name: 'auto', model_id: 'auto', description: 'Models chosen by task', rate_multiplier: 1.0, rate_unit: 'Credit' },
  { model_name: 'claude-sonnet-5.5', model_id: 'claude-sonnet-5.5', rate_multiplier: 1.3, context_window_tokens: 1000000 },
  { model_name: 'broken row without an id' },
]

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
  counts: { repos: 1, intents: 2, live_actions: 0, execution_leases: 0, admin_leases: 0, turns_running: 0 },
} as unknown as HealthResponse

const REPO = {
  repo_id: 'r_1',
  label: 'checkout-web',
  canonical_path: '/Users/x/work/checkout-web',
} as unknown as RepoRecord

/** Still registered, so restorable; the registry lists it only to a read that asks for archived repositories. */
const ARCHIVED_REPO = {
  repo_id: 'r_old',
  label: 'checkout-legacy',
  canonical_path: '/Users/x/work/checkout-legacy',
  archived: true,
} as unknown as RepoRecord

function registry(_body: unknown, path: string) {
  const repos = new URLSearchParams(path.split('?')[1] ?? '').get('include_archived') ? [REPO, ARCHIVED_REPO] : [REPO]
  return { repos, totals: { repos: 1, unavailable: 0, open_actions: 0 } }
}

/** The same settings with a different advisor leaf; the grant list is the server's, never local state. */
function advisorSettings(grants: string[], enabled = true, model = ''): SettingsResponse {
  const advisor = { enabled, auto_draft_repo_ids: grants, model }
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
  it('offers no unattended-work window, turn cap or credit cap', async () => {
    routes()
    await renderSettings()

    expect(screen.queryByText('Night work window')).toBeNull()
    expect(screen.queryByText('Turn cap per window')).toBeNull()
    expect(screen.queryByText('Credit cap')).toBeNull()
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

describe('New intents', () => {
  it('starts from the scope and patches new_intent.summary_confirmation alone', async () => {
    const puts = routes()
    await renderSettings()

    const select = screen.getByRole('combobox', { name: 'Summary confirmation for new intents' })
    expect(select).toHaveValue('scope')
    expect(within(select).getAllByRole('option').map((option) => option.textContent)).toEqual(['From the scope', 'Off', 'On'])
    await userEvent.selectOptions(select, 'off')
    await waitFor(() => expect(puts).toEqual([{ new_intent: { summary_confirmation: 'off' } }]))
  })
})

describe('Advisor', () => {
  it('starts inherited and offers only the models the host lists for this account', async () => {
    routes({ 'GET /api/models': () => MODELS })
    await renderSettings()

    const select = within(row('Advisor model')).getByRole('combobox', { name: 'The model Advisor drafts run on' })
    expect(select).toHaveValue('')
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(3))
    expect(within(select).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Inherited from KiroCrew', 'auto · 1×', 'claude-sonnet-5.5 · 1.3×',
    ])
  })

  it('patches advisor.model alone when a model is picked, and "" when it is handed back', async () => {
    const puts = routes({ 'GET /api/models': () => MODELS })
    await renderSettings()

    const select = within(row('Advisor model')).getByRole('combobox', { name: 'The model Advisor drafts run on' })
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(3))
    await userEvent.selectOptions(select, 'claude-sonnet-5.5')
    await waitFor(() => expect(puts).toEqual([{ advisor: { model: 'claude-sonnet-5.5' } }]))
  })

  it('keeps a saved pick the account no longer offers, labelled, instead of showing "inherited"', async () => {
    routes({
      'GET /api/apps/aidlc-studio/settings': () => advisorSettings([], true, 'claude-retired-1'),
      'GET /api/models': () => MODELS,
    })
    await renderSettings()

    const select = within(row('Advisor model')).getByRole('combobox', { name: 'The model Advisor drafts run on' })
    expect(select).toHaveValue('claude-retired-1')
    expect(within(select).getByRole('option', { name: 'claude-retired-1 (not offered by your account now)' }))
      .toBeInTheDocument()
  })

  it('says why only "inherited" is offered when the host cannot list models', async () => {
    routes({
      'GET /api/models': () => {
        throw new StubApiError(503, { error: 'model list unavailable' })
      },
    })
    await renderSettings()

    expect(
      await screen.findByText('KiroCrew could not list your models right now, so only the inherited choice and your saved pick are offered.'),
    ).toBeInTheDocument()
    expect(within(row('Advisor model')).getByRole('combobox')).toHaveValue('')
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

  it('lets a grant on an archived repository, named as archived, be withdrawn but never added', async () => {
    const puts = routes({
      'GET /api/apps/aidlc-studio/settings': () => advisorSettings(['r_old']),
      'GET /api/apps/aidlc-studio/repos*': registry,
    })
    await renderSettings()

    const grant = row('Draft ahead, per repository')
    const box = await within(grant).findByRole('checkbox', { name: 'Draft ahead in checkout-legacy' })
    expect(box).toBeChecked()
    expect(box).toBeEnabled()
    expect(within(grant).getByText('Archived. Untick to withdraw the grant; restore the repository first to grant it again.'))
      .toBeInTheDocument()
    expect(within(grant).queryByText(/no longer registered/)).toBeNull()
    await userEvent.click(box)
    await waitFor(() => expect(puts).toEqual([{ advisor: { auto_draft_repo_ids: [] } }]))
    // Withdrawn, the archived repository has nothing left to show here, so there is no box to tick it back with.
    await waitFor(() => expect(within(grant).queryByRole('checkbox', { name: 'Draft ahead in checkout-legacy' })).toBeNull())
    expect(within(grant).getByRole('checkbox', { name: 'Draft ahead in checkout-web' })).not.toBeChecked()
  })

  it('keeps an archived repository\'s grant withdrawable while the Advisor is switched off, as an orphan\'s is', async () => {
    const puts = routes({
      'GET /api/apps/aidlc-studio/settings': () => advisorSettings(['r_old'], false),
      'GET /api/apps/aidlc-studio/repos*': registry,
    })
    await renderSettings()

    const grant = row('Draft ahead, per repository')
    const box = await within(grant).findByRole('checkbox', { name: 'Draft ahead in checkout-legacy' })
    expect(within(grant).getByRole('checkbox', { name: 'Draft ahead in checkout-web' })).toBeDisabled()
    await userEvent.click(box)
    await waitFor(() => expect(puts).toEqual([{ advisor: { auto_draft_repo_ids: [] } }]))
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

  it('keeps a mute on an archived repository, named as archived, and calls only an unregistered one an orphan', async () => {
    const puts = routes({
      'GET /api/apps/aidlc-studio/settings': () => ({
        ...SETTINGS, settings: { ...SETTINGS.settings, slack: { enabled: true, muted_repo_ids: ['r_old', 'r_gone'] } },
      }),
      'GET /api/apps/aidlc-studio/repos*': registry,
    })
    await renderSettings()

    const mute = row('Muted repositories')
    const box = await within(mute).findByRole('checkbox', { name: 'Mute checkout-legacy' })
    expect(box).toBeChecked()
    expect(box).toBeDisabled()
    expect(within(mute).getByText('Archived. The mute is kept; restore the repository to change it.')).toBeInTheDocument()
    expect(within(mute).queryByText('r_old is muted but no longer registered.')).toBeNull()
    await userEvent.click(box)
    expect(puts).toEqual([])

    // A repository that is really gone is still named, and its mute removed deliberately.
    expect(within(mute).getByText('r_gone is muted but no longer registered.')).toBeInTheDocument()
    await userEvent.click(within(mute).getByRole('checkbox', { name: 'Mute r_gone' }))
    await waitFor(() => expect(puts).toEqual([{ slack: { muted_repo_ids: ['r_old'] } }]))
  })

  it('does not call a live mute an orphan while the repository list is unread', async () => {
    routes({
      'GET /api/apps/aidlc-studio/settings': () => ({
        ...SETTINGS, settings: { ...SETTINGS.settings, slack: { enabled: true, muted_repo_ids: ['r_1'] } },
      }),
      'GET /api/apps/aidlc-studio/repos*': () => {
        throw new StubApiError(503, { error: 'the registry read failed', code: 'unavailable' })
      },
    })
    await renderSettings()

    const mute = row('Muted repositories')
    expect(within(mute).queryByText(/no longer registered/)).toBeNull()
    expect(within(mute).queryByRole('checkbox')).toBeNull()
    expect(within(mute).getByText('Loading…')).toBeInTheDocument()
  })

  it('states that quick actions are unavailable and offers no control', async () => {
    routes()
    await renderSettings()

    const quick = row('Slack quick actions')
    expect(within(quick).getByText('Unavailable — KiroCrew does not provide a way to do this')).toBeInTheDocument()
    expect(within(quick).queryByRole('checkbox')).toBeNull()
  })

  it('says why Slack is unavailable in a sentence, never as the reason code', async () => {
    routes({
      'GET /api/apps/aidlc-studio/settings': () => ({
        ...SETTINGS,
        capabilities: { ...SETTINGS.capabilities, slack: { available: false, reason: 'slack_not_configured' } },
      }),
    })
    await renderSettings()

    const slack = row('Slack notifications')
    expect(within(slack).getByText('Unavailable — Slack is not set up in KiroCrew')).toBeInTheDocument()
    expect(within(slack).queryByText(/slack_not_configured/)).toBeNull()
    expect(within(slack).getByRole('checkbox', { name: /Send Slack notifications/ })).toBeDisabled()
  })

  it('reads a host symbol by its prefix, and an unknown code as a sentence that does not print it', async () => {
    routes({
      'GET /api/apps/aidlc-studio/settings': () => ({
        ...SETTINGS,
        capabilities: {
          ...SETTINGS.capabilities,
          slack: { available: false, reason: 'missing:slack_client.post_blocks' },
          slack_quick_actions: { available: false, reason: 'some_future_reason' },
        },
      }),
    })
    await renderSettings()

    const slack = row('Slack notifications')
    expect(
      within(slack).getByText('Unavailable — this version of KiroCrew does not provide what Studio needs for it'),
    ).toBeInTheDocument()
    expect(within(slack).queryByText(/post_blocks/)).toBeNull()
    const quick = row('Slack quick actions')
    expect(
      within(quick).getByText('Unavailable — KiroCrew gave a reason this version of Studio does not recognise'),
    ).toBeInTheDocument()
    expect(within(quick).queryByText(/some_future_reason/)).toBeNull()
  })
})

describe('The registry read', () => {
  it('re-reads the repositories for a change to the registry, and for no other event', async () => {
    routes({ 'GET /api/apps/aidlc-studio/repos*': registry })
    const handlers = new Set<(event: StreamEvent) => void>()
    const bus = { subscribe: (h: (event: StreamEvent) => void) => (handlers.add(h), () => void handlers.delete(h)) }
    const emit = (type: StudioEventType, seq: number) => {
      const event: StreamEvent = { type, seq, payload: {} }
      act(() => [...handlers].forEach((handler) => handler(event)))
    }
    const registryReads = () => apiCalls.filter((call) => call.method === 'GET' && call.path.startsWith('/api/apps/aidlc-studio/repos')).length
    render(
      <BusContext.Provider value={bus}>
        <SettingsView route={{ ...EMPTY_ROUTE, view: 'settings' }} go={vi.fn()} />
      </BusContext.Provider>,
    )
    await screen.findByRole('checkbox', { name: 'Mute checkout-web' })
    expect(registryReads()).toBe(1)

    // The busiest events on a working install: none of them changes a label, a path or an archived flag.
    const busy: StudioEventType[] = ['action.updated', 'intent.updated', 'activity.appended', 'lease.updated', 'health.updated']
    busy.forEach((type, index) => emit(type, index + 1))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(registryReads()).toBe(1)

    emit('repo.updated', busy.length + 1)
    await waitFor(() => expect(registryReads()).toBe(2))
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
          'API 400: {"error":"cap out of range","code":"bad_body","details":{"key":"global_concurrency_cap","reason":"range"}}',
        )
      },
    })
    await renderSettings()

    const input = within(row('Global concurrency')).getByRole('spinbutton')
    await userEvent.clear(input)
    await userEvent.type(input, '5')
    await userEvent.tab()

    await waitFor(() =>
      expect(screen.getByText(/rejected global_concurrency_cap/)).toBeInTheDocument(),
    )
  })
})
