/**
 * The repository, preflight and install/upgrade guarantees, exercised through the real shell.
 *
 * These tests are deliberately mounted on `StudioApp` with the stub route table rather than on the
 * components in isolation: the page reads its data from the shell context and its selection from the
 * query string, so an isolated render would prove nothing about the thing that actually ships.
 *
 * Each test pins a promise the PRD makes, not a rendering detail:
 *   - registration is manual, read-only first, and refuses a duplicate identity by name;
 *   - a preflight belongs to the path it was read for;
 *   - the install preview shows every managed path with its ownership, every conflict with its diff,
 *     and offers no force control;
 *   - a plan the server no longer agrees with is refused and re-read, never confirmed;
 *   - Install is offered when Studio's own `.kiro` harness is absent, even where another harness
 *     already holds AI-DLC, and the card says what installing beside it will and will not touch;
 *   - `install recovery required` says so, names the evidence path, and says execution is blocked;
 *   - a moved repository stays visible with its remediation;
 *   - below 900px the page says the flows are desktop-only instead of showing a path picker.
 */

import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { I18nProvider } from '../i18n'
import { StudioApp } from '../shell/StudioApp'
import { registerView } from '../shell/ViewRouter'
import { apiCalls, setApiRoutes, StubApiError, type RouteHandler } from '../test/stubs/app-sdk'
import { ReposView } from './ReposView'

// In production `src/views/repos/index.tsx` is what the shell's glob finds; registering it here keeps the
// test independent of that one-line shim while mounting the same component the shell would.
registerView('repos', ReposView)

const BASE = '/api/apps/aidlc-studio'

// --------------------------------------------------------------------------- //
// viewport
// --------------------------------------------------------------------------- //

/**
 * Answer `matchMedia` from a pretend width.
 *
 * The shared setup installs a stub that answers `false` to everything, which would make every
 * `min-width` query read as "narrow" and silently take the desktop flows away from every test.
 */
function setViewport(width: number): void {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string) => {
      const min = /min-width:\s*(\d+)px/.exec(query)
      const max = /max-width:\s*(\d+)px/.exec(query)
      const matches = min ? width >= Number(min[1]) : max ? width <= Number(max[1]) : false
      return {
        matches,
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }
    },
  })
}

// --------------------------------------------------------------------------- //
// fixtures
// --------------------------------------------------------------------------- //

type Json = Record<string, unknown>

function preflight(over: Json = {}): Json {
  return {
    path_input: '/work/checkout-web',
    canonical_path: '/work/checkout-web',
    identity: {
      canonical_path: '/work/checkout-web',
      st_dev: 16777232,
      st_ino: 4213,
      git_common_dir: '/work/checkout-web/.git',
      identity_str: 'dev:16777232/ino:4213',
      provable: true,
    },
    is_directory: true,
    sensitive: false,
    duplicate_of: null,
    platform: 'darwin',
    git: { is_repo: true, branch: 'main', dirty: false, common_dir: '/work/checkout-web/.git' },
    bun: { found: true, path: '/usr/local/bin/bun', version: '1.1.30', source: 'path', searched: ['/usr/local/bin/bun'] },
    harness_dirs: [],
    aidlc: { layout: null, spaces: [], intents: 0, state_versions: [] },
    symlinks_at_managed_paths: [],
    free_space_bytes: 91_000_000_000,
    writable: true,
    existing_receipt: null,
    warnings: [],
    can_register: true,
    can_install: true,
    ...over,
  }
}

function install(over: Json = {}): Json {
  return {
    status: 'installed',
    engine_dir: '.kiro',
    engine_version: '2.6.1',
    own_engine_version: '2.6.1',
    engine_state_version: 8,
    stage_count: 33,
    harness_dirs: [
      {
        dir: '.kiro',
        harness_name: 'kiro',
        rules_subdir: 'steering',
        engine_version: '2.6.1',
        engine_state_version: 8,
        stage_count: 33,
        has_utility: true,
      },
    ],
    receipt: {
      receipt_id: 'rc_001',
      engine_version: '2.6.1',
      studio_version: '1.0.0',
      committed_at: '2026-08-30T09:00:00Z',
      status: 'current',
      files: 265,
    },
    bundled_engine_version: '2.7.1',
    upgrade_available: true,
    newer_installed: false,
    state_version_blocked: false,
    drift_count: 0,
    ...over,
  }
}

function repo(over: Json = {}): Json {
  return {
    repo_id: 'r_1',
    label: 'checkout-web',
    canonical_path: '/work/checkout-web',
    resolved_identity: 'dev:16777232/ino:4213',
    git_common_dir_identity: null,
    platform: 'darwin',
    added_at: '2026-08-01T10:00:00Z',
    last_seen: '2026-09-05T08:00:00Z',
    availability: 'available',
    availability_detail: null,
    archived: false,
    legacy_console_id: null,
    install_status: 'installed',
    installed_engine_version: '2.6.1',
    engine_dir: '.kiro',
    receipt_version: '2.6.1',
    install: install(),
    counts: { intents: 3, in_flight: 0, open_actions: 2, blocking_findings: 0 },
    leases: { execution: null, admin: null },
    git: {
      available: true,
      reason: null,
      branch: 'main',
      detached: false,
      head: 'abcdef1234567890',
      head_subject: 'add guest checkout',
      dirty: false,
      dirty_files: 0,
      ahead: 0,
      behind: 0,
      upstream: 'origin/main',
      observed_at: '2026-09-05T08:00:00Z',
      took_ms: 42,
    },
    findings: [],
    scanned_at: '2026-09-05T08:00:00Z',
    ...over,
  }
}

function entry(over: Json = {}): Json {
  return {
    path: '.kiro/tools/aidlc-engine.ts',
    ownership: 'framework',
    action: 'create',
    live_sha256: null,
    payload_sha256: 'aaaabbbbccccdddd',
    receipt_sha256: null,
    size: 4096,
    fragment_key: null,
    diff: null,
    blocking: false,
    ...over,
  }
}

function plan(over: Json = {}): Json {
  return {
    repo_id: 'r_1',
    kind: 'install',
    engine_from: null,
    engine_to: '2.7.1',
    studio_version: '1.0.0',
    payload_digest: '1111222233334444',
    entries: [entry(), entry({ path: '.kiro/settings/cli.json', ownership: 'merge', action: 'merge_create', fragment_key: 'aidlc' })],
    counts: { create: 1, merge_create: 1 },
    blocking: false,
    blockers: [],
    warnings: [],
    newer_installed: false,
    same_version: false,
    requires_admin_lease: true,
    bytes_to_write: 8192,
    state_versions_found: [],
    state_versions_unreadable: [],
    state_version_blocked: false,
    preflight: preflight(),
    ...over,
  }
}

const CONFLICT = entry({
  path: '.kiro/steering/aidlc.md',
  ownership: 'framework',
  action: 'owned_modified',
  live_sha256: 'deadbeefdeadbeef',
  receipt_sha256: 'aaaabbbbccccdddd',
  diff: '--- receipted\n+++ on disk\n@@ -1,2 +1,2 @@\n-original line\n+edited by hand',
  blocking: true,
})

function transaction(over: Json = {}): Json {
  return {
    transaction_id: 'tx_9',
    repo_id: 'r_1',
    kind: 'upgrade',
    status: 'recovery_required',
    steps: [
      { name: 'stage_payload', started_at: '2026-09-05T08:00:00Z', finished_at: '2026-09-05T08:00:01Z', ok: true, detail: {} },
      { name: 'write_files', started_at: '2026-09-05T08:00:02Z', finished_at: '2026-09-05T08:00:03Z', ok: false, detail: {} },
      { name: 'restore_backups', started_at: '2026-09-05T08:00:04Z', finished_at: '2026-09-05T08:00:05Z', ok: false, detail: {} },
    ],
    receipt_id: null,
    prior_receipt_id: 'rc_001',
    error: 'restore_backups: permission denied',
    failed_dir: '/studio-data/failed/tx_9',
    repo_install_status: 'recovery_required',
    started_at: '2026-09-05T08:00:00Z',
    finished_at: '2026-09-05T08:00:05Z',
    engine_version: '2.7.1',
    ...over,
  }
}

/** The shell's own reads, so mounting `StudioApp` does not 404 on four unrelated routes. */
function shellRoutes(): Record<string, RouteHandler> {
  return {
    [`GET ${BASE}/actions`]: () => ({
      actions: [],
      organize: 'priority',
      groups: [],
      counts: { total: 0, critical: 0, blocking: 0, attention: 0, info: 0 },
      generated_at: '2026-09-05T08:00:00Z',
    }),
    [`GET ${BASE}/leases`]: () => ({ leases: [], global_concurrency_cap: 3, live_execution: 0 }),
    [`GET ${BASE}/settings`]: () => ({
      settings: { night_window: { enabled: false, start_local: '22:00', end_local: '06:00' } },
      capabilities: {},
      versions: {},
      updated_at: null,
    }),
    [`GET ${BASE}/health`]: () => ({ status: 'healthy', issues: [] }),
    [`GET ${BASE}/events/poll`]: () => ({ events: [], cursor: 0, oldest_seq: 0, reset: false }),
  }
}

function mount(search = '?view=repos') {
  window.history.replaceState(null, '', `/apps/aidlc-studio${search}`)
  return render(
    <I18nProvider>
      <StudioApp />
    </I18nProvider>,
  )
}

beforeEach(() => {
  setViewport(1440)
})

afterEach(() => {
  window.history.replaceState(null, '', '/apps/aidlc-studio')
})

// --------------------------------------------------------------------------- //
// the registry
// --------------------------------------------------------------------------- //

describe('the repository list', () => {
  it.each([false, true])('opens a clean new-intent wizard for this repository (has intents: %s)', async (hasIntents) => {
    const record = repo()
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [record], totals: { repos: 1, unavailable: 0, open_actions: 0 } }),
      [`GET ${BASE}/repos/r_1`]: () => ({
        repo: record, transactions: [],
        intents: hasIntents ? [{
          slug: 'existing-work', intent_key: '260911-existing-work', operational_state: 'Idle',
          disk: { current_stage: 'requirements-analysis' }, open_actions: 0,
        }] : [],
      }),
      [`GET ${BASE}/repos/r_1/git`]: () => ({ git: record.git, owned_dirty: [], unrelated_dirty: 0 }),
      [`GET ${BASE}/repos/r_1/transactions/tx_old`]: () => ({
        transaction: transaction({ transaction_id: 'tx_old', kind: 'install', status: 'committed', error: null, steps: [] }),
      }),
      [`GET ${BASE}/repos/r_1/intents`]: () => ({ intents: [], spaces: ['default'], active_space: 'default' }),
      [`POST ${BASE}/repos/r_1/intents/plan/preview`]: () => new Promise(() => {}),
      [`GET ${BASE}/health`]: () => ({ status: 'healthy', issues: [], tools: { bun: { found: true, searched: [] } } }),
      [`GET ${BASE}/settings`]: () => ({
        settings: { night_window: { enabled: false }, advisor: { enabled: false } },
        capabilities: { advisor: { available: false } }, versions: {}, updated_at: null,
      }),
    })
    mount('?view=repos&repo=r_1&space=old-space&intent=old-work&action=a_old&stage=code-generation&unit=unit-old&artifact=old.md&draft=d_old&tab=conversation&tx=tx_old#h-old')

    const section = (await screen.findByRole('heading', { name: 'Intents' })).closest('section')!
    await userEvent.click(within(section).getByRole('button', { name: 'New intent' }))

    expect(await screen.findByRole('heading', { name: 'New intent' })).toBeInTheDocument()
    expect(screen.getByLabelText('Repository')).toHaveValue('r_1')
    expect(Object.fromEntries(new URLSearchParams(window.location.search))).toEqual({
      view: 'new-intent', repo: 'r_1',
    })
    expect(window.location.hash).toBe('')
    expect(apiCalls.filter((call) => call.method !== 'GET' && !call.path.endsWith('/plan/preview'))).toEqual([])
  })

  it('renames, archives and restores the same registration through its controls', async () => {
    let record = repo()
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: record.archived ? [] : [record], totals: { repos: record.archived ? 0 : 1, unavailable: 0, open_actions: 0 } }),
      [`GET ${BASE}/repos?include_archived=1`]: () => ({ repos: [record], totals: { repos: 1, unavailable: 0, open_actions: 0 } }),
      [`GET ${BASE}/repos/r_1`]: () => ({ repo: record, intents: [], transactions: [] }),
      [`GET ${BASE}/repos/r_1/git`]: () => ({ git: record.git, owned_dirty: [], unrelated_dirty: 0 }),
      [`PUT ${BASE}/repos/r_1/metadata`]: (body) => {
        record = { ...record, ...(body as Json) }
        return { ok: true, repo: record }
      },
    })
    mount('?view=repos&repo=r_1')
    await userEvent.click(await screen.findByRole('button', { name: 'Rename repository' }))
    const label = screen.getByLabelText('Repository label')
    await userEvent.clear(label)
    await userEvent.type(label, 'Updated label')
    await userEvent.click(screen.getByRole('button', { name: 'Save label' }))
    await waitFor(() => expect(record.label).toBe('Updated label'))
    await userEvent.click(screen.getByRole('button', { name: 'Archive repository' }))
    await userEvent.click(screen.getAllByRole('button', { name: 'Archive repository' })[0]!)
    await waitFor(() => expect(record.archived).toBe(true))
    await userEvent.click(screen.getByLabelText('Show archived repositories'))
    await userEvent.click(await screen.findByRole('button', { name: 'Details' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Restore repository' }))
    await waitFor(() => expect(record.archived).toBe(false))
    expect(record.repo_id).toBe('r_1')
    expect(apiCalls.filter((call) => call.method === 'DELETE')).toHaveLength(0)
  })

  it('shows installation health, both versions, intents, queue and Git for every registered path', async () => {
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({
        repos: [repo()],
        totals: { repos: 1, unavailable: 0, open_actions: 2 },
      }),
    })
    mount()

    const table = await screen.findByRole('table')
    const row = within(table).getByRole('row', { name: /checkout-web/ })
    expect(within(row).getByText('Installed')).toBeInTheDocument()
    // Installed and bundled are both visible, because "which version is this repo on" is the question.
    expect(within(row).getByText('2.6.1')).toBeInTheDocument()
    expect(within(row).getByText('bundled 2.7.1')).toBeInTheDocument()
    expect(within(row).getByText('Upgrade available')).toBeInTheDocument()
    expect(within(row).getByText('main')).toBeInTheDocument()
    expect(within(row).getByText('clean')).toBeInTheDocument()

    // FR-ACT-008: maintenance is here, not in the blocking queue.
    expect(
      screen.getByText(/never enter the blocking workflow queue/i),
    ).toBeInTheDocument()
    // FR-REP-007: removal is unregistration.
    expect(screen.getByText(/changes no bytes on disk/i)).toBeInTheDocument()
  })

  it('keeps a moved repository visible with its remediation and a way to rebind it', async () => {
    const moved = repo({
      repo_id: 'r_moved',
      label: 'billing-api',
      availability: 'moved',
      availability_detail: 'canonical path is gone',
      install: install({ status: 'not_installed', engine_version: null, own_engine_version: null, receipt: null, upgrade_available: false }),
      git: null,
    })
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [moved], totals: { repos: 1, unavailable: 1, open_actions: 0 } }),
      [`GET ${BASE}/repos/r_moved`]: () => ({ repo: moved, intents: [], transactions: [] }),
      [`GET ${BASE}/repos/r_moved/git`]: () => ({
        git: { available: false, reason: 'repo_unavailable', branch: null, detached: false, head: null, head_subject: null, dirty: false, dirty_files: 0, ahead: null, behind: null, upstream: null, observed_at: '2026-09-05T08:00:00Z', took_ms: 1 },
        owned_dirty: [],
        unrelated_dirty: 0,
      }),
    })
    mount('?view=repos&repo=r_moved')

    expect(await screen.findByText('This repository needs attention')).toBeInTheDocument()
    expect(screen.getByText(/Rebind this registration to the new absolute path/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Rebind to a new path/ })).toBeInTheDocument()
    // A repository Studio cannot read is not silently dropped from the registry.
    expect(screen.getByText('Moved')).toBeInTheDocument()
  })

  it('says registration and installation are desktop-only below 900px, instead of offering a path picker', async () => {
    setViewport(390)
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [repo()], totals: { repos: 1, unavailable: 0, open_actions: 2 } }),
    })
    mount()

    expect(
      await screen.findByText('Registration, installation and upgrade are desktop-only'),
    ).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Add repository/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Upgrade AI-DLC/ })).not.toBeInTheDocument()
    // The repository itself is still readable: health, versions and queue are all still on screen.
    expect(screen.getByText('Installed')).toBeInTheDocument()
    expect(screen.getByText('2 waiting for you')).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------- //
// registration
// --------------------------------------------------------------------------- //

describe('adding a repository', () => {
  it('browses gateway directories explicitly and requires a new preflight after selection', async () => {
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [], totals: {} }),
      [`POST ${BASE}/directories`]: (body) => {
        const path = (body as { path: string | null }).path ?? '/work'
        return { path, parent: '/', directories: path === '/work'
          ? [{ name: 'my repo', path: '/work/my repo' }] : [], truncated: false }
      },
    })
    mount()
    await userEvent.click(await screen.findByRole('button', { name: /Add repository/ }))
    expect(apiCalls.some((call) => call.path.endsWith('/directories'))).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Browse directories' }))
    await userEvent.click(await screen.findByRole('button', { name: 'my repo' }))
    await waitFor(() => expect(screen.getByText('/work/my repo')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Use this directory' }))
    expect(screen.getByLabelText('Absolute path')).toHaveValue('/work/my repo')
    expect(screen.getByRole('button', { name: /Register this path/ })).toBeDisabled()
    expect(apiCalls.filter((call) => call.path.endsWith('/directories'))).toHaveLength(2)
  })

  it('runs a read-only preflight before anything can be registered', async () => {
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [], totals: { repos: 0, unavailable: 0, open_actions: 0 } }),
      [`POST ${BASE}/repos/preflight`]: () => ({ preflight: preflight() }),
      [`POST ${BASE}/repos`]: () => ({ ok: true, repo: repo(), preflight: preflight() }),
    })
    mount()

    await userEvent.click(await screen.findByRole('button', { name: /Add repository/ }))
    const dialog = screen.getByRole('dialog')
    const register = within(dialog).getByRole('button', { name: /Register this path/ })
    // Nothing is registrable until the server has looked.
    expect(register).toBeDisabled()
    expect(within(dialog).getByText(/Registration stays disabled until the read-only preflight/)).toBeInTheDocument()

    await userEvent.type(within(dialog).getByLabelText('Absolute path'), '/work/checkout-web')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Run preflight' }))

    expect(await within(dialog).findByText('This path can be registered.')).toBeInTheDocument()
    expect(within(dialog).getByText(/Studio ran nothing from the directory and wrote nothing to it/)).toBeInTheDocument()
    expect(register).toBeEnabled()

    // The preflight is the only thing that touched the path, and it was a POST to the preflight route.
    expect(apiCalls.filter((call) => call.path === `${BASE}/repos/preflight`)).toHaveLength(1)
    expect(apiCalls.some((call) => call.method === 'POST' && call.path === `${BASE}/repos`)).toBe(false)

    await userEvent.click(register)
    await waitFor(() =>
      expect(apiCalls.some((call) => call.method === 'POST' && call.path === `${BASE}/repos`)).toBe(true),
    )
  })

  it('says bun was not found and names every location it looked in, never that bun is not installed', async () => {
    // The gateway the desktop app runs is started by launchd, whose PATH is four directories none of the
    // bun installers write to (A28), so "not installed" was said to users who had bun installed twice
    // over. The locations come from the probe (`bun.searched`), not from the catalogue, so what the row
    // claims Studio looked at is what Studio looked at.
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [], totals: { repos: 0, unavailable: 0, open_actions: 0 } }),
      [`POST ${BASE}/repos/preflight`]: () => ({
        preflight: preflight({
          bun: {
            found: false,
            path: null,
            version: null,
            source: null,
            searched: [
              '/Users/dev/.bun/bin/bun',
              '/opt/homebrew/bin/bun',
              '/usr/local/bin/bun',
              '/home/linuxbrew/.linuxbrew/bin/bun',
            ],
          },
        }),
      }),
    })
    mount()

    await userEvent.click(await screen.findByRole('button', { name: /Add repository/ }))
    const dialog = screen.getByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Absolute path'), '/work/checkout-web')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Run preflight' }))

    expect(await within(dialog).findByText('not found')).toBeInTheDocument()
    expect(
      within(dialog).getByText(/bun was not found on PATH or in the usual install locations/),
    ).toBeInTheDocument()
    // The one action that helps, both halves of it.
    expect(within(dialog).getByText(/Install bun, or start KiroCrew from a shell whose PATH has bun/)).toBeInTheDocument()
    const searched = within(dialog).getByText(/Studio looked in:/)
    for (const location of [
      '/Users/dev/.bun/bin/bun',
      '/opt/homebrew/bin/bun',
      '/usr/local/bin/bun',
      '/home/linuxbrew/.linuxbrew/bin/bun',
    ]) {
      expect(searched).toHaveTextContent(location)
    }
    // The claim that sent people to reinstall software they already had.
    expect(within(dialog).queryByText(/bun is not installed/)).not.toBeInTheDocument()
    expect(within(dialog).queryByText(/until bun is installed/)).not.toBeInTheDocument()
  })

  it('throws the preflight away when the path is edited, so Register cannot confirm another directory', async () => {
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [], totals: { repos: 0, unavailable: 0, open_actions: 0 } }),
      [`POST ${BASE}/repos/preflight`]: () => ({ preflight: preflight() }),
    })
    mount()

    await userEvent.click(await screen.findByRole('button', { name: /Add repository/ }))
    const dialog = screen.getByRole('dialog')
    const path = within(dialog).getByLabelText('Absolute path')
    await userEvent.type(path, '/work/checkout-web')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Run preflight' }))
    expect(await within(dialog).findByText('This path can be registered.')).toBeEnabled

    await userEvent.type(path, '-other')
    expect(within(dialog).getByRole('button', { name: /Register this path/ })).toBeDisabled()
    expect(within(dialog).queryByText('This path can be registered.')).not.toBeInTheDocument()
  })

  it('refuses a path that resolves to an already-registered repository, and names it', async () => {
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [repo()], totals: { repos: 1, unavailable: 0, open_actions: 2 } }),
      [`POST ${BASE}/repos/preflight`]: () => ({
        preflight: preflight({ duplicate_of: 'r_1', can_register: false, path_input: '/work/../work/checkout-web' }),
      }),
    })
    mount()

    await userEvent.click(await screen.findByRole('button', { name: /Add repository/ }))
    const dialog = screen.getByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Absolute path'), '/work/../work/checkout-web')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Run preflight' }))

    expect(await within(dialog).findByText('Already registered')).toBeInTheDocument()
    expect(within(dialog).getByText(/same repository as checkout-web/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: /Open checkout-web/ })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: /Register this path/ })).toBeDisabled()
  })

  it('refuses an absolute-path rule client-side without calling the server', async () => {
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [], totals: { repos: 0, unavailable: 0, open_actions: 0 } }),
      [`POST ${BASE}/repos/preflight`]: () => ({ preflight: preflight() }),
    })
    mount()

    await userEvent.click(await screen.findByRole('button', { name: /Add repository/ }))
    const dialog = screen.getByRole('dialog')
    await userEvent.type(within(dialog).getByLabelText('Absolute path'), 'work/checkout-web')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Run preflight' }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('The path has to be absolute')
    expect(apiCalls.some((call) => call.path === `${BASE}/repos/preflight`)).toBe(false)
  })
})

// --------------------------------------------------------------------------- //
// install and upgrade previews
// --------------------------------------------------------------------------- //

describe('doctor feedback at the maintenance controls', () => {
  const result = {
    verb_key: 'utility.doctor', argv: [], exit_code: 0, timed_out: false,
    duration_ms: 229, stdout: 'Workspace checks passed.', stderr: '',
    json: null, ok: true, side_effects: ['audit'],
  }

  function doctorRoutes(handler: RouteHandler) {
    const record = repo()
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [record], totals: { repos: 1, unavailable: 0, open_actions: 0 } }),
      [`GET ${BASE}/repos/r_1`]: () => ({ repo: record, intents: [], transactions: [] }),
      [`GET ${BASE}/repos/r_1/git`]: () => ({ git: record.git, owned_dirty: [], unrelated_dirty: 0 }),
      [`POST ${BASE}/repos/r_1/doctor`]: handler,
    })
  }

  it('shows progress and the completed check beside the button, including its output', async () => {
    let finish!: (value: unknown) => void
    doctorRoutes(() => new Promise((resolve) => { finish = resolve }))
    mount('?view=repos&repo=r_1')
    await userEvent.click(await screen.findByRole('button', { name: 'Run AI-DLC doctor' }))

    const maintenance = screen.getByRole('heading', { name: 'Maintenance' }).closest('section')!
    expect(within(maintenance).getByRole('button', { name: 'Running doctor…' })).toBeDisabled()
    expect(within(maintenance).getByRole('status')).toHaveTextContent('Running doctor…')
    await act(async () => finish({ ok: true, result }))

    const feedback = within(maintenance).getByRole('region', { name: 'Doctor result' })
    expect(within(feedback).getByRole('status')).toHaveTextContent('AI-DLC doctor finished. Exit code 0.')
    await userEvent.click(within(feedback).getByText('Check output'))
    expect(within(feedback).getByText('Workspace checks passed.')).toBeVisible()
    expect(within(maintenance).getByRole('button', { name: 'Run AI-DLC doctor' })).toBeEnabled()
    expect(apiCalls.filter((call) => call.path.endsWith('/doctor'))).toEqual([
      { method: 'POST', path: `${BASE}/repos/r_1/doctor`, body: { confirm: true } },
    ])
  })

  it('shows a refused check beside the button rather than only at the top of the page', async () => {
    doctorRoutes(() => { throw new StubApiError(409, { code: 'repo_busy', error: 'repository busy' }) })
    mount('?view=repos&repo=r_1')
    await userEvent.click(await screen.findByRole('button', { name: 'Run AI-DLC doctor' }))
    const maintenance = screen.getByRole('heading', { name: 'Maintenance' }).closest('section')!
    expect(await within(maintenance).findByRole('status')).toHaveTextContent(
      'Another operation holds this repository. It will not be interrupted.',
    )
    expect(within(maintenance).getByRole('button', { name: 'Run AI-DLC doctor' })).toBeEnabled()
  })

  it('shows a failed check and preserves diagnostic text as text', async () => {
    doctorRoutes(() => ({ ok: true, result: { ...result, ok: false, exit_code: 1, stderr: '<script>diagnostic</script>' } }))
    mount('?view=repos&repo=r_1')
    await userEvent.click(await screen.findByRole('button', { name: 'Run AI-DLC doctor' }))
    const feedback = await screen.findByRole('region', { name: 'Doctor result' })
    expect(within(feedback).getByRole('status')).toHaveTextContent('AI-DLC doctor reported a problem. Exit code 1.')
    await userEvent.click(within(feedback).getByText('Error output'))
    expect(within(feedback).getByText('<script>diagnostic</script>')).toBeVisible()
    expect(feedback.querySelector('script')).toBeNull()
  })

  it('does not show a late result after leaving the repository', async () => {
    let finish!: (value: unknown) => void
    doctorRoutes(() => new Promise((resolve) => { finish = resolve }))
    mount('?view=repos&repo=r_1')
    await userEvent.click(await screen.findByRole('button', { name: 'Run AI-DLC doctor' }))
    await userEvent.click(screen.getByRole('button', { name: 'All repositories' }))
    await act(async () => finish({ ok: true, result }))
    expect(screen.queryByText('AI-DLC doctor finished. Exit code 0.')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Details' }))
    expect(screen.queryByRole('region', { name: 'Doctor result' })).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'Run AI-DLC doctor' })).toBeEnabled()
  })
})

describe('uninstalling the harness', () => {
  it('requests cancellation and keeps the transaction visible while rollback is pending', async () => {
    const record = repo()
    let current = transaction({ kind: 'install', status: 'written', error: null, failed_dir: null, steps: [] })
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [record], totals: { repos: 1, unavailable: 0, open_actions: 0 } }),
      [`GET ${BASE}/repos/r_1`]: () => ({ repo: record, intents: [], transactions: [] }),
      [`GET ${BASE}/repos/r_1/git`]: () => ({ git: record.git, owned_dirty: [], unrelated_dirty: 0 }),
      [`GET ${BASE}/repos/r_1/transactions/tx_9`]: () => ({ transaction: current }),
      [`POST ${BASE}/repos/r_1/transactions/tx_9/cancel`]: () => {
        current = { ...current, error: 'cancel_requested' }
        return { transaction: current }
      },
    })
    mount('?view=repos&repo=r_1&tx=tx_9')
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel and roll back installation' }))
    expect(await screen.findByText('Cancellation requested. Waiting for the transaction to restore its changes.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel and roll back installation' })).toBeDisabled()
    expect(apiCalls).toContainEqual({ method: 'POST', path: `${BASE}/repos/r_1/transactions/tx_9/cancel`, body: {} })
    expect(window.location.search).toContain('tx=tx_9')
  })

  it('shows removals, changed configuration and preserved legacy content before sending the reviewed digest', async () => {
    const record = repo()
    const removal = plan({
      kind: 'uninstall', engine_from: '2.6.1', engine_to: '',
      entries: [
        entry({ path: '.kiro/tools/aidlc-engine.ts', action: 'remove' }),
        entry({ path: 'AGENTS.md', action: 'remove_fragment', ownership: 'merge' }),
        entry({ path: '.gitignore', action: 'preserve', ownership: 'merge',
          reason: 'legacy_fragment_ownership_unknown' }),
        entry({ path: '.kiro/agents/aidlc.json', action: 'preserve', ownership: 'framework-mutable',
          reason: 'engine_mutable_changed' }),
      ],
      counts: { remove: 1, remove_fragment: 1, preserve: 2 },
    })
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [record], totals: { repos: 1, unavailable: 0, open_actions: 0 } }),
      [`GET ${BASE}/repos/r_1`]: () => ({ repo: record, intents: [], transactions: [] }),
      [`GET ${BASE}/repos/r_1/git`]: () => ({ git: record.git, owned_dirty: [], unrelated_dirty: 0 }),
      [`POST ${BASE}/repos/r_1/uninstall/preview`]: () => ({ plan: removal, plan_digest: 'reviewed-removal' }),
      [`POST ${BASE}/repos/r_1/uninstall`]: () => ({ ok: true, transaction_id: 'tx_9', status: 'staged' }),
      [`GET ${BASE}/repos/r_1/transactions/tx_9`]: () => ({ transaction: transaction({
        kind: 'uninstall', status: 'committed', error: null, failed_dir: null, repo_install_status: 'not_installed',
      }) }),
    })
    mount('?view=repos&repo=r_1')
    await userEvent.click(await screen.findByRole('button', { name: 'Uninstall harness' }))
    expect(await screen.findByText('Content that will remain')).toBeInTheDocument()
    expect(screen.getAllByText('The older receipt does not identify the exact content Studio added.').length).toBeGreaterThan(0)
    expect(screen.getAllByText('This configurable file changed after installation. It will remain with its current contents.').length).toBeGreaterThan(0)
    expect(apiCalls.some((call) => call.path.endsWith('/uninstall'))).toBe(false)
    await userEvent.click(screen.getByRole('button', { name: 'Uninstall the reviewed harness' }))
    await waitFor(() => expect(apiCalls).toContainEqual({
      method: 'POST', path: `${BASE}/repos/r_1/uninstall`, body: { plan_digest: 'reviewed-removal' },
    }))
  })
})

describe('engine version rollback', () => {
  it('reviews the previous engine version and submits only the reviewed rollback digest', async () => {
    const record = repo({ install: install({
      rollback_target: { receipt_id: 'rc_old', engine_version: '2.5.0' },
    }) })
    const rollbackPlan = plan({
      kind: 'rollback', engine_from: '2.6.1', engine_to: '2.5.0',
      entries: [entry({ action: 'restore_version' })],
      counts: { restore_version: 1 }, target_receipt_id: 'rc_old',
    })
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [record], totals: { repos: 1, unavailable: 0, open_actions: 0 } }),
      [`GET ${BASE}/repos/r_1`]: () => ({ repo: record, intents: [], transactions: [] }),
      [`GET ${BASE}/repos/r_1/git`]: () => ({ git: record.git, owned_dirty: [], unrelated_dirty: 0 }),
      [`POST ${BASE}/repos/r_1/rollback/preview`]: () => ({ plan: rollbackPlan, plan_digest: 'reviewed-rollback' }),
      [`POST ${BASE}/repos/r_1/rollback`]: () => ({ ok: true, transaction_id: 'tx_9', status: 'staged' }),
      [`GET ${BASE}/repos/r_1/transactions/tx_9`]: () => ({ transaction: transaction({
        kind: 'rollback', status: 'committed', engine_version: '2.5.0', error: null, failed_dir: null,
        repo_install_status: 'installed',
      }) }),
    })
    mount('?view=repos&repo=r_1')
    await userEvent.click(await screen.findByRole('button', { name: /Roll back engine/ }))
    const preview = await screen.findByRole('region', { name: 'Roll back the AI-DLC engine' })
    expect(within(preview).getByText('2.6.1')).toBeInTheDocument()
    expect(within(preview).getByText('2.5.0')).toBeInTheDocument()
    expect(apiCalls.some((call) => call.path.endsWith('/rollback'))).toBe(false)
    await userEvent.click(within(preview).getByRole('button', { name: 'Restore engine 2.5.0' }))
    await waitFor(() => expect(apiCalls).toContainEqual({
      method: 'POST', path: `${BASE}/repos/r_1/rollback`, body: { plan_digest: 'reviewed-rollback' },
    }))
    expect(await screen.findByText('Committed. The receipt for 2.5.0 is now the current one.')).toBeInTheDocument()
  })
})

describe('the install preview', () => {
  const fresh = repo({
    repo_id: 'r_new',
    label: 'payments',
    install: install({
      status: 'not_installed',
      engine_version: null,
      own_engine_version: null,
      engine_state_version: null,
      stage_count: null,
      harness_dirs: [],
      receipt: null,
      upgrade_available: false,
    }),
    counts: { intents: 0, in_flight: 0, open_actions: 0, blocking_findings: 0 },
  })

  function installRoutes(over: Record<string, RouteHandler> = {}) {
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [fresh], totals: { repos: 1, unavailable: 0, open_actions: 0 } }),
      [`GET ${BASE}/repos/r_new`]: () => ({ repo: fresh, intents: [], transactions: [] }),
      [`GET ${BASE}/repos/r_new/git`]: () => ({ git: fresh['git'], owned_dirty: [], unrelated_dirty: 0 }),
      [`POST ${BASE}/repos/r_new/install/preview`]: () => ({ plan: plan({ repo_id: 'r_new' }), plan_digest: 'digest-aaa' }),
      [`POST ${BASE}/repos/r_new/install`]: () => ({ ok: true, transaction_id: 'tx_1', status: 'staged' }),
      [`GET ${BASE}/repos/r_new/transactions/tx_1`]: () => ({
        transaction: transaction({ transaction_id: 'tx_1', kind: 'install', status: 'committed', error: null, failed_dir: null, steps: [] }),
      }),
      ...over,
    })
  }

  it('shows only the preview confirmation while installing, and restores the entry when cancelled', async () => {
    installRoutes()
    mount('?view=repos&repo=r_new')

    await userEvent.click(await screen.findByRole('button', { name: 'Install AI-DLC' }))
    const preview = await screen.findByRole('region', { name: 'Install preview' })
    const confirm = await within(preview).findByRole('button', { name: 'Install AI-DLC 2.7.1' })
    expect(screen.getAllByRole('button', { name: /Install AI-DLC/ })).toEqual([confirm])

    await userEvent.click(within(preview).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('region', { name: 'Install preview' })).not.toBeInTheDocument()
    const entry = screen.getByRole('button', { name: 'Install AI-DLC' })
    expect(screen.getAllByRole('button', { name: /Install AI-DLC/ })).toEqual([entry])
    expect(apiCalls.some((call) => call.method === 'POST' && call.path === `${BASE}/repos/r_new/install`)).toBe(false)
  })

  it('names every managed path with its ownership kind, and offers no force control', async () => {
    installRoutes()
    mount('?view=repos&repo=r_new')

    await userEvent.click(await screen.findByRole('button', { name: /Install AI-DLC/ }))

    const preview = await screen.findByRole('region', { name: 'Install preview' })
    expect(within(preview).getByText(/Nothing has been written/)).toBeInTheDocument()
    expect(within(preview).getByText('.kiro/tools/aidlc-engine.ts')).toBeInTheDocument()
    expect(within(preview).getByText('.kiro/settings/cli.json')).toBeInTheDocument()
    // FR-INST-005/006: the ownership kind is on screen for each path, not just a count.
    expect(within(preview).getAllByText('Framework file').length).toBeGreaterThan(0)
    expect(within(preview).getAllByText('Merge target').length).toBeGreaterThan(0)
    expect(within(preview).getByText(/An exclusive admin lease is required/)).toBeInTheDocument()
    // FR-INST-007: no force-overwrite shortcut exists anywhere on the surface.
    expect(within(preview).getByText(/no force-overwrite control/i)).toBeInTheDocument()
    for (const button of within(preview).getAllByRole('button')) {
      expect(button.textContent ?? '').not.toMatch(/force|overwrite/i)
    }
  })

  it('opens from a table row, which also selects the repository', async () => {
    installRoutes()
    mount()

    const table = await screen.findByRole('table')
    await userEvent.click(within(table).getByRole('button', { name: /Install AI-DLC/ }))

    // The click both navigates and opens the flow; neither may cancel the other.
    expect(await screen.findByRole('region', { name: 'Install preview' })).toBeInTheDocument()
    expect(window.location.search).toContain('repo=r_new')
  })

  it('blocks the confirmation on a conflict and shows its diff', async () => {
    installRoutes({
      [`POST ${BASE}/repos/r_new/install/preview`]: () => ({
        plan: plan({
          repo_id: 'r_new',
          entries: [entry(), CONFLICT],
          blockers: [CONFLICT],
          blocking: true,
          counts: { create: 1, owned_modified: 1 },
        }),
        plan_digest: 'digest-blocked',
      }),
    })
    mount('?view=repos&repo=r_new')

    await userEvent.click(await screen.findByRole('button', { name: /Install AI-DLC/ }))
    const preview = await screen.findByRole('region', { name: 'Install preview' })

    expect(await within(preview).findByText('Conflicts that stop this')).toBeInTheDocument()
    expect(within(preview).getByText('This cannot run yet.')).toBeInTheDocument()
    expect(within(preview).getByRole('button', { name: /Install AI-DLC 2\.7\.1/ })).toBeDisabled()
    expect(screen.getAllByRole('button', { name: /Install AI-DLC/ })).toHaveLength(1)

    await userEvent.click(within(preview).getAllByText('Difference')[0] as HTMLElement)
    expect(within(preview).getByText('+edited by hand')).toBeInTheDocument()
  })

  it('names what would be retired, and that a modified retired file is kept', async () => {
    installRoutes({
      [`POST ${BASE}/repos/r_new/install/preview`]: () => ({
        plan: plan({
          repo_id: 'r_new',
          entries: [
            entry({ path: '.kiro/tools/old-graph.json', action: 'retire', receipt_sha256: 'old', live_sha256: 'old' }),
            entry({ path: '.kiro/steering/legacy.md', action: 'retire_blocked', receipt_sha256: 'old', live_sha256: 'changed' }),
          ],
          counts: { retire: 1, retire_blocked: 1 },
        }),
        plan_digest: 'digest-retire',
      }),
    })
    mount('?view=repos&repo=r_new')

    await userEvent.click(await screen.findByRole('button', { name: /Install AI-DLC/ }))
    const preview = await screen.findByRole('region', { name: 'Install preview' })
    expect(await within(preview).findByText('What would be retired')).toBeInTheDocument()
    expect(within(preview).getAllByText('Retire').length).toBeGreaterThan(0)
    expect(
      within(preview).getAllByText('Kept — changed since install, so it is not retired').length,
    ).toBeGreaterThan(0)
  })

  it('confirms with the digest it showed, and refuses and re-reads when the server disagrees', async () => {
    let previews = 0
    installRoutes({
      [`POST ${BASE}/repos/r_new/install/preview`]: () => {
        previews += 1
        return previews === 1
          ? { plan: plan({ repo_id: 'r_new' }), plan_digest: 'digest-aaa' }
          : {
              plan: plan({
                repo_id: 'r_new',
                entries: [CONFLICT],
                blockers: [CONFLICT],
                blocking: true,
                counts: { owned_modified: 1 },
              }),
              plan_digest: 'digest-bbb',
            }
      },
      [`POST ${BASE}/repos/r_new/install`]: () => {
        throw new StubApiError(409, {
          error: 'the plan changed',
          code: 'install_conflict',
          details: { reason: 'preview_changed' },
        })
      },
    })
    mount('?view=repos&repo=r_new')

    await userEvent.click(await screen.findByRole('button', { name: /Install AI-DLC/ }))
    const preview = await screen.findByRole('region', { name: 'Install preview' })
    const confirm = await within(preview).findByRole('button', { name: /Install AI-DLC 2\.7\.1/ })
    await userEvent.click(confirm)

    // The digest the user read is the digest that was sent.
    const applied = apiCalls.find((call) => call.method === 'POST' && call.path === `${BASE}/repos/r_new/install`)
    expect(applied?.body).toEqual({ plan_digest: 'digest-aaa' })

    // Refused, said so, and re-read the plan rather than letting a second click confirm the stale one.
    expect(await within(preview).findByText('The repository changed while you were reading')).toBeInTheDocument()
    expect(within(preview).getByText(/A file at a managed path differs/)).toBeInTheDocument()
    await waitFor(() => expect(previews).toBe(2))
    await waitFor(() =>
      expect(within(preview).getByRole('button', { name: /Install AI-DLC 2\.7\.1/ })).toBeDisabled(),
    )
  })

  it('opens the transaction drawer after the 202 and reports the committed receipt', async () => {
    installRoutes()
    mount('?view=repos&repo=r_new')

    await userEvent.click(await screen.findByRole('button', { name: /Install AI-DLC/ }))
    const preview = await screen.findByRole('region', { name: 'Install preview' })
    await userEvent.click(await within(preview).findByRole('button', { name: /Install AI-DLC 2\.7\.1/ }))

    const drawer = await screen.findByRole('region', { name: 'Transaction tx_1' })
    expect(within(drawer).getByText('Committed')).toBeInTheDocument()
    expect(within(drawer).getByText(/The receipt for 2\.7\.1 is now the current one/)).toBeInTheDocument()
    // Deep-linkable: the transaction is in the URL, so a reload comes back to it.
    expect(window.location.search).toContain('tx=tx_1')
  })
})

describe('the upgrade preview', () => {
  it('shows the installed and bundled versions before the plan arrives, and refuses a blocked state version', async () => {
    const drifted = repo({ install: install({ status: 'drift', drift_count: 2 }) })
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [drifted], totals: { repos: 1, unavailable: 0, open_actions: 2 } }),
      [`GET ${BASE}/repos/r_1`]: () => ({ repo: drifted, intents: [], transactions: [] }),
      [`GET ${BASE}/repos/r_1/git`]: () => ({ git: drifted['git'], owned_dirty: ['.kiro/steering/aidlc.md'], unrelated_dirty: 4 }),
      [`POST ${BASE}/repos/r_1/upgrade/preview`]: () => {
        throw new StubApiError(409, {
          error: 'state version',
          code: 'state_version_migration_unconfirmed',
          details: { state_versions_found: [7], compatible: [8] },
        })
      },
    })
    mount('?view=repos&repo=r_1')

    await userEvent.click(await screen.findByRole('button', { name: /Upgrade AI-DLC/ }))
    const preview = await screen.findByRole('region', { name: 'Upgrade preview' })

    expect(within(preview).getByText('2.6.1')).toBeInTheDocument()
    expect(within(preview).getByText('2.7.1')).toBeInTheDocument()
    expect(within(preview).getByText(/2 managed files changed since install/)).toBeInTheDocument()
    // The refusal is the server's sentence, and it comes with "nothing was written".
    expect(await within(preview).findByText(/older version, and upgrading it is not yet proven safe/)).toBeInTheDocument()
    expect(within(preview).getByText(/Nothing was written\./)).toBeInTheDocument()
    expect(within(preview).getByRole('button', { name: /Upgrade to AI-DLC/ })).toBeDisabled()
  })

  it('separates receipt-owned drift from unrelated dirty files', async () => {
    const drifted = repo({ install: install({ status: 'drift', drift_count: 1 }) })
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [drifted], totals: { repos: 1, unavailable: 0, open_actions: 2 } }),
      [`GET ${BASE}/repos/r_1`]: () => ({ repo: drifted, intents: [], transactions: [] }),
      [`GET ${BASE}/repos/r_1/git`]: () => ({
        git: drifted['git'],
        owned_dirty: ['.kiro/steering/aidlc.md'],
        unrelated_dirty: 4,
      }),
    })
    mount('?view=repos&repo=r_1')

    expect(await screen.findByText('Receipt-owned files with local changes')).toBeInTheDocument()
    expect(screen.getByText('.kiro/steering/aidlc.md')).toBeInTheDocument()
    // FR-GIT-004: unrelated work in progress must not read as something that blocks an upgrade.
    expect(screen.getByText(/4 unrelated files have local changes, which do not block an upgrade/)).toBeInTheDocument()
    expect(screen.getByText(/never commits, pushes, checks out/)).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------- //
// Studio's own harness versus any harness
// --------------------------------------------------------------------------- //

/**
 * A repository may hold AI-DLC under a harness Studio does not manage (`.claude`, `.codex`, `.cursor`,
 * `.aidlc`). `status` stays `installed` there, and rightly so: Studio reads that harness and can drive
 * the repository through it. `own_engine_version` is the separate fact — null means the `.kiro` harness
 * Studio manages is not on disk — and the install lane keys on that one. Keying it on `status` offered
 * neither Install (already installed) nor Upgrade (same version installed), which left no way to add a
 * `.kiro` harness to such a repository at all.
 */
describe('the harness Studio owns, next to one it does not', () => {
  const CLAUDE = {
    dir: '.claude',
    harness_name: 'claude',
    rules_subdir: 'rules',
    engine_version: '2.7.1',
    engine_state_version: 8,
    stage_count: 33,
    has_utility: true,
  }

  /** AI-DLC 2.7.1 under `.claude`, nothing of Studio's: the live lab1-express case. */
  const foreign = repo({
    repo_id: 'r_claude',
    label: 'lab1-express',
    canonical_path: '/work/lab1-express',
    installed_engine_version: '2.7.1',
    engine_dir: '.claude',
    receipt_version: null,
    install: install({
      engine_dir: '.claude',
      engine_version: '2.7.1',
      own_engine_version: null,
      harness_dirs: [CLAUDE],
      receipt: null,
      upgrade_available: false,
    }),
  })

  function routes(record: Json): void {
    const id = String(record['repo_id'])
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [record], totals: { repos: 1, unavailable: 0, open_actions: 2 } }),
      [`GET ${BASE}/repos/${id}`]: () => ({ repo: record, intents: [], transactions: [] }),
      [`GET ${BASE}/repos/${id}/git`]: () => ({ git: record['git'], owned_dirty: [], unrelated_dirty: 0 }),
    })
  }

  it('offers Install for a repository that reads as installed under a harness Studio does not manage', async () => {
    routes(foreign)
    mount()

    const table = await screen.findByRole('table')
    const row = within(table).getByRole('row', { name: /lab1-express/ })
    // The row still says Installed, because Studio can drive this repository through `.claude`.
    expect(within(row).getByText('Installed')).toBeInTheDocument()
    expect(within(row).getByText('Installed under .claude, which Studio does not manage')).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: /Install AI-DLC/ })).toBeInTheDocument()
    // Nothing here is Studio's, so there is nothing of Studio's to upgrade.
    expect(within(row).queryByRole('button', { name: /Upgrade AI-DLC/ })).not.toBeInTheDocument()
  })

  it('names the other harness and its version, and says installing adds .kiro beside it', async () => {
    routes(foreign)
    mount('?view=repos&repo=r_claude')

    expect(
      await screen.findByText(/AI-DLC 2\.7\.1 is installed in this repository under \.claude/),
    ).toBeInTheDocument()
    expect(screen.getByText(/Studio's own Kiro harness \(\.kiro\) is not installed here/)).toBeInTheDocument()
    expect(screen.getByText(/Installing adds \.kiro beside it/)).toBeInTheDocument()
    // The promise that makes the offer safe to accept: the other harness and the shared workspace stay.
    expect(screen.getByText(/no file under \.claude is written, changed or removed/)).toBeInTheDocument()
    expect(screen.getByText(/aidlc\/ workspace stays exactly as it is/)).toBeInTheDocument()
  })

  it("offers Upgrade, not Install, when Studio's own harness is behind the bundled engine", async () => {
    routes(repo())
    mount()

    const table = await screen.findByRole('table')
    const row = within(table).getByRole('row', { name: /checkout-web/ })
    expect(within(row).getByRole('button', { name: /Upgrade AI-DLC/ })).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /Install AI-DLC/ })).not.toBeInTheDocument()
    expect(within(row).queryByText(/which Studio does not manage/)).not.toBeInTheDocument()
  })

  it("offers Install, not Upgrade, when a receipt survives but Studio's harness does not", async () => {
    // `.kiro` was deleted by hand: the receipt is still there, so every managed file reads as drift and
    // the registry reports `drift`. There is nothing left to upgrade — the install lane must rebuild it.
    routes(
      repo({
        install: install({
          status: 'drift',
          engine_dir: '.claude',
          engine_version: '2.7.1',
          own_engine_version: null,
          harness_dirs: [CLAUDE],
          upgrade_available: false,
          drift_count: 265,
        }),
      }),
    )
    mount()

    const table = await screen.findByRole('table')
    const row = within(table).getByRole('row', { name: /checkout-web/ })
    expect(within(row).getByRole('button', { name: /Install AI-DLC/ })).toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /Upgrade AI-DLC/ })).not.toBeInTheDocument()
  })

  it("offers neither when Studio's own harness is already the bundled version", async () => {
    routes(
      repo({
        installed_engine_version: '2.7.1',
        receipt_version: '2.7.1',
        install: install({
          engine_version: '2.7.1',
          own_engine_version: '2.7.1',
          harness_dirs: [{ ...CLAUDE, dir: '.kiro', harness_name: 'kiro', rules_subdir: 'steering' }],
          upgrade_available: false,
        }),
      }),
    )
    mount()

    const table = await screen.findByRole('table')
    const row = within(table).getByRole('row', { name: /checkout-web/ })
    expect(within(row).queryByRole('button', { name: /Install AI-DLC/ })).not.toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: /Upgrade AI-DLC/ })).not.toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------- //
// recovery
// --------------------------------------------------------------------------- //

describe('install recovery required', () => {
  const broken = repo({
    install: install({ status: 'recovery_required', upgrade_available: false }),
  })

  it('explains restoration of an interrupted uninstall and submits its recovery preview', async () => {
    const restoration = plan({
      kind: 'recovery', engine_from: '2.6.1', engine_to: '2.6.1',
      recovery_transaction_id: 'tx_9', recovery_kind: 'uninstall',
      entries: [entry({ action: 'restore_backup' })], counts: { restore_backup: 1 },
    })
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [broken], totals: { repos: 1, unavailable: 0, open_actions: 0 } }),
      [`GET ${BASE}/repos/r_1`]: () => ({ repo: broken, intents: [], transactions: [transaction({ kind: 'uninstall' })] }),
      [`GET ${BASE}/repos/r_1/git`]: () => ({ git: broken.git, owned_dirty: [], unrelated_dirty: 0 }),
      [`POST ${BASE}/repos/r_1/install/recovery/preview`]: () => ({ plan: restoration, plan_digest: 'restore-reviewed' }),
      [`POST ${BASE}/repos/r_1/install/recovery`]: () => ({ ok: true, transaction_id: 'tx_9', status: 'staged' }),
      [`GET ${BASE}/repos/r_1/transactions/tx_9`]: () => ({ transaction: transaction({
        kind: 'uninstall', status: 'rolled_back', engine_version: '2.6.1', error: null,
        steps: [{ name: 'confirm_recovery', ok: true, detail: {} }], repo_install_status: 'installed',
      }) }),
    })
    mount('?view=repos&repo=r_1')
    await userEvent.click(await screen.findByRole('button', { name: /Preview the recovery/ }))
    const preview = await screen.findByRole('region', { name: 'Install recovery preview' })
    expect(await within(preview).findByText(/Restore interrupted Uninstall transaction tx_9/)).toBeInTheDocument()
    await userEvent.click(within(preview).getByRole('button', { name: 'Restore the interrupted transaction' }))
    await waitFor(() => expect(apiCalls).toContainEqual({
      method: 'POST', path: `${BASE}/repos/r_1/install/recovery`, body: { plan_digest: 'restore-reviewed' },
    }))
    expect(await screen.findByText('Everything was restored. The repository keeps its previous complete installation and receipt.')).toBeInTheDocument()
  })

  it('names the state, the recovery path, and that AI-DLC execution is blocked', async () => {
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [broken], totals: { repos: 1, unavailable: 0, open_actions: 2 } }),
      [`GET ${BASE}/repos/r_1`]: () => ({ repo: broken, intents: [], transactions: [transaction()] }),
      [`GET ${BASE}/repos/r_1/git`]: () => ({ git: broken['git'], owned_dirty: [], unrelated_dirty: 0 }),
    })
    mount('?view=repos&repo=r_1')

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Install recovery required')
    expect(alert).toHaveTextContent('AI-DLC execution is blocked in this repository until this clears.')
    expect(alert).toHaveTextContent('/studio-data/failed/tx_9')
    expect(alert).toHaveTextContent(/kept outside the repository/)
    // Assertive, because the user has to know before they try to run anything in this repository.
    expect(screen.getByRole('button', { name: /Preview the recovery/ })).toBeInTheDocument()
  })

  it('runs recovery as its own previewed transaction', async () => {
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [broken], totals: { repos: 1, unavailable: 0, open_actions: 2 } }),
      [`GET ${BASE}/repos/r_1`]: () => ({ repo: broken, intents: [], transactions: [transaction()] }),
      [`GET ${BASE}/repos/r_1/git`]: () => ({ git: broken['git'], owned_dirty: [], unrelated_dirty: 0 }),
      [`POST ${BASE}/repos/r_1/install/recovery/preview`]: () => ({
        plan: plan({ kind: 'recovery', engine_from: '2.6.1' }),
        plan_digest: 'digest-recover',
      }),
    })
    mount('?view=repos&repo=r_1')

    await userEvent.click(await screen.findByRole('button', { name: /Preview the recovery/ }))
    const preview = await screen.findByRole('region', { name: 'Install recovery preview' })
    expect(within(preview).getByText(/what a recovery transaction would restore/)).toBeInTheDocument()
    expect(await within(preview).findByRole('button', { name: /Run install recovery/ })).toBeEnabled()
  })

  it('reports a failed rollback as install recovery required in the transaction drawer', async () => {
    setApiRoutes({
      ...shellRoutes(),
      [`GET ${BASE}/repos`]: () => ({ repos: [broken], totals: { repos: 1, unavailable: 0, open_actions: 2 } }),
      [`GET ${BASE}/repos/r_1`]: () => ({ repo: broken, intents: [], transactions: [transaction()] }),
      [`GET ${BASE}/repos/r_1/git`]: () => ({ git: broken['git'], owned_dirty: [], unrelated_dirty: 0 }),
      [`GET ${BASE}/repos/r_1/transactions/tx_9`]: () => ({ transaction: transaction() }),
    })
    mount('?view=repos&repo=r_1&tx=tx_9')

    const drawer = await screen.findByRole('region', { name: 'Transaction tx_9' })
    // The rollback steps are shown, with their outcome as a word and not only as a colour.
    expect(within(drawer).getByText('Restore the backups')).toBeInTheDocument()
    expect(within(drawer).getAllByText(/failed/).length).toBeGreaterThan(0)
    expect(within(drawer).getByText('restore_backups: permission denied')).toBeInTheDocument()
    expect(within(drawer).getAllByText('Install recovery required').length).toBeGreaterThan(0)
  })
})
