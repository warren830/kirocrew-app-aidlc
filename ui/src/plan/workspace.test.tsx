import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { StudioApiError } from '../lib/api'
import type { EngineResult } from '../lib/types'
import en from '../i18n/parts/workspace.en-US.json'
import zh from '../i18n/parts/workspace.zh-CN.json'
import { IntentSettingsPanel } from './IntentSettingsPanel'
import { SpaceControls } from '../intents/SpaceControls'
import type { IntentSettings, SettingsPreviewResponse, WorkspaceApi } from './workspaceTypes'

// Catalog generation belongs to main. Exercise the exact new part while leaving generated files alone.
vi.mock('../i18n', async (original) => {
  const actual = await original<typeof import('../i18n')>()
  return { ...actual, useI18n: () => {
    const base = actual.makeI18n('en-US')
    return { ...base, t: (key: string, params?: Record<string, string | number>) => {
      const value = (en as Record<string, string>)[key]
      return value ? value.replace(/\{(\w+)\}/g, (_, name: string) => String(params?.[name] ?? ''))
        : base.t(key, params)
    } }
  } }
})

const before: IntentSettings = { scope: 'feature', depth: 'Standard', test_strategy: 'Standard' }
const result = { ok: true } as EngineResult

function proposal(changes: Partial<IntentSettings> = {}): SettingsPreviewResponse {
  const after = { ...before, ...changes }
  const changed = JSON.stringify(before) !== JSON.stringify(after)
  return {
    proposal_digest: after.depth === 'Comprehensive' ? 'b'.repeat(64) : 'a'.repeat(64),
    proposal: {
      repo_id: 'r_one', intent_key: '260910-proof', before, after,
      scopes: ['feature', 'mvp'], allowed: changed, refusals: changed ? [] : ['no_changes'],
      argv_preview: ['bun', 'aidlc-utility.ts', 'config-change', '--depth', after.depth],
      verb_key: changes.scope ? 'utility.scope_change' : 'utility.config_change',
      selection: { space: 'default', intent_dir: '260910-proof' },
      stages: [{ slug: 'requirements-analysis', state: 'completed', before: true, after: true }],
    },
  }
}

function api() {
  return {
    intentSettingsPreview: vi.fn(async (_repo: string, _key: string, body: Partial<IntentSettings>) => proposal(body)),
    changeIntentSettings: vi.fn(async (_repo: string, _key: string, body: Partial<IntentSettings>) => ({
      ok: true, result, verified: { settings: { ...before, ...body }, stages_preserved: true },
    })),
    spaces: vi.fn(async () => ({ spaces: ['default', 'team-two'], active_space: 'default' })),
    createSpace: vi.fn(async (_repo: string, name: string) => ({
      ok: true, result, spaces: ['default', 'team-two', name], active_space: 'default',
    })),
    switchSpace: vi.fn(async (_repo: string, name: string) => ({
      ok: true, result, spaces: ['default', 'team-two'], active_space: name,
    })),
  } satisfies WorkspaceApi
}

describe('existing intent settings', () => {
  it('previews and confirms the exact digest, then reports verified disk settings', async () => {
    const client = api()
    const changed = vi.fn()
    const user = userEvent.setup()
    render(<IntentSettingsPanel api={client} repoId="r_one" intentKey="260910-proof" onApplied={changed} />)
    await waitFor(() => expect(screen.getByLabelText('Depth')).toHaveValue('Standard'))
    expect(screen.getByRole('button', { name: 'Review and confirm' })).toBeDisabled()
    await user.selectOptions(screen.getByLabelText('Depth'), 'Comprehensive')
    expect(client.changeIntentSettings).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Preview changes' }))
    await user.click(await screen.findByRole('button', { name: 'Review and confirm' }))
    expect(client.changeIntentSettings).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Apply settings' }))
    expect(client.changeIntentSettings).toHaveBeenCalledWith('r_one', '260910-proof', {
      ...before, depth: 'Comprehensive', proposal_digest: 'b'.repeat(64),
    })
    expect(await screen.findByText('Settings verified on disk.')).toBeInTheDocument()
    expect(changed).toHaveBeenCalledOnce()
  })

  it('discards a preview that arrives after the user changes the draft', async () => {
    const client = api()
    const user = userEvent.setup()
    let resolve!: (answer: SettingsPreviewResponse) => void
    render(<IntentSettingsPanel api={client} repoId="r_one" intentKey="260910-proof" />)
    await waitFor(() => expect(screen.getByLabelText('Depth')).toHaveValue('Standard'))
    client.intentSettingsPreview.mockImplementationOnce(() => new Promise((done) => { resolve = done }))
    await user.selectOptions(screen.getByLabelText('Depth'), 'Minimal')
    await user.click(screen.getByRole('button', { name: 'Preview changes' }))
    await user.selectOptions(screen.getByLabelText('Depth'), 'Comprehensive')
    await act(async () => resolve(proposal({ depth: 'Minimal' })))
    expect(screen.queryByRole('button', { name: 'Review and confirm' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Depth')).toHaveValue('Comprehensive')
    expect(client.changeIntentSettings).not.toHaveBeenCalled()
  })

  it('discards confirmation when fields change and requires a fresh preview after a stale refusal', async () => {
    const client = api()
    client.changeIntentSettings.mockRejectedValue(new StudioApiError('plan_invalid', 'stale', {}, 409))
    const user = userEvent.setup()
    render(<IntentSettingsPanel api={client} repoId="r_one" intentKey="260910-proof" />)
    await waitFor(() => expect(screen.getByLabelText('Depth')).toHaveValue('Standard'))
    await user.selectOptions(screen.getByLabelText('Depth'), 'Minimal')
    await user.click(screen.getByRole('button', { name: 'Preview changes' }))
    await user.click(await screen.findByRole('button', { name: 'Review and confirm' }))
    await user.selectOptions(screen.getByLabelText('Depth'), 'Comprehensive')
    expect(screen.queryByRole('button', { name: 'Apply settings' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Preview changes' }))
    await user.click(await screen.findByRole('button', { name: 'Review and confirm' }))
    await user.click(screen.getByRole('button', { name: 'Apply settings' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Apply settings' })).not.toBeInTheDocument()
    expect(screen.queryByText('Settings verified on disk.')).not.toBeInTheDocument()
  })

  it('shows backend scope refusals with completed history and never enables confirmation', async () => {
    const client = api()
    const answer = proposal({ scope: 'mvp' })
    answer.proposal.allowed = false
    answer.proposal.refusals = ['open_boundary']
    client.intentSettingsPreview.mockResolvedValue(answer)
    render(<IntentSettingsPanel api={client} repoId="r_one" intentKey="260910-proof" />)
    expect(await screen.findByText(en['workspace.refusal.open_boundary'])).toBeInTheDocument()
    expect(screen.getByText('1 completed stages retain their history.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Review and confirm' })).toBeDisabled()
  })
})

describe('spaces', () => {
  it('creates without switching, then confirms a separate cursor switch', async () => {
    const client = api()
    const changed = vi.fn()
    const user = userEvent.setup()
    render(<SpaceControls api={client} repoId="r_one" onChanged={changed} />)
    await screen.findByText('Active space: default')
    await user.type(screen.getByLabelText('New space name'), 'team-three')
    await user.click(screen.getByRole('button', { name: 'Create space' }))
    expect(client.createSpace).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await screen.findByText('Space created. The active space is unchanged.')
    expect(client.createSpace).toHaveBeenCalledWith('r_one', 'team-three')
    expect(client.switchSpace).not.toHaveBeenCalled()
    expect(screen.getByText('Active space: default')).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Target space'), 'team-two')
    await user.click(screen.getByRole('button', { name: 'Switch space' }))
    expect(client.switchSpace).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await screen.findByText('Active space: team-two')
    expect(client.switchSpace).toHaveBeenCalledWith('r_one', 'team-two')
    expect(changed).toHaveBeenCalledTimes(2)
  })

  it.each(['../escape', 'help', 'UPPER', '1team', 'default', 'two--hyphens'])('refuses invalid or duplicate name %s', async (name) => {
    const client = api()
    const user = userEvent.setup()
    render(<SpaceControls api={client} repoId="r_one" />)
    await screen.findByText('Active space: default')
    await user.type(screen.getByLabelText('New space name'), name)
    expect(screen.getByRole('button', { name: 'Create space' })).toBeDisabled()
    expect(client.createSpace).not.toHaveBeenCalled()
  })

  it('keeps the disk cursor on a busy refusal and re-reads inventory', async () => {
    const client = api()
    client.switchSpace.mockRejectedValue(new StudioApiError('repo_busy', 'busy', {}, 409))
    const user = userEvent.setup()
    render(<SpaceControls api={client} repoId="r_one" />)
    await screen.findByText('Active space: default')
    await user.selectOptions(screen.getByLabelText('Target space'), 'team-two')
    await user.click(screen.getByRole('button', { name: 'Switch space' }))
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await screen.findByRole('alert')
    await waitFor(() => expect(client.spaces).toHaveBeenCalledTimes(2))
    expect(screen.getByText('Active space: default')).toBeInTheDocument()
    expect(screen.queryByText('Active space verified on disk.')).not.toBeInTheDocument()
  })

  it('drops pending confirmation and the old inventory when the repository changes', async () => {
    const client = api()
    const user = userEvent.setup()
    const { rerender } = render(<SpaceControls api={client} repoId="r_one" />)
    await screen.findByText('Active space: default')
    await user.type(screen.getByLabelText('New space name'), 'team-new')
    await user.click(screen.getByRole('button', { name: 'Create space' }))
    client.spaces.mockResolvedValue({ spaces: ['another'], active_space: 'another' })
    rerender(<SpaceControls api={client} repoId="r_two" />)
    await screen.findByText('Active space: another')
    expect(screen.queryByRole('button', { name: 'Confirm' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('New space name')).toHaveValue('')
    expect(client.createSpace).not.toHaveBeenCalled()
  })
})

it('has matching nonempty English and Chinese workspace keys and placeholders', () => {
  expect(Object.keys(en).sort()).toEqual(Object.keys(zh).sort())
  for (const [key, value] of Object.entries(en)) {
    const translated = (zh as Record<string, string>)[key]
    expect(translated?.trim()).toBeTruthy()
    expect(translated?.match(/\{\w+\}/g) ?? []).toEqual(value.match(/\{\w+\}/g) ?? [])
  }
})
