import { expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { useStudioApi } from '../lib/api'
import type { BunToolResponse, HealthResponse } from '../lib/types'
import { apiCalls, setApiRoutes, StubApiError } from '../test/stubs/app-sdk'
import { AboutPanel } from './AboutPanel'
import { BunSetting } from './BunSetting'

const PREFIX = '/api/apps/aidlc-studio'
const missing: BunToolResponse['tool'] = {
  found: false, path: null, configured_path: null, version: null, source: null, searched: ['/usr/bin/bun'],
}
const ready: BunToolResponse['tool'] = {
  found: true, path: '/custom/bun', configured_path: '/custom/bun', version: '1.3.0', source: 'explicit',
  searched: ['/custom/bun'],
}

function Harness({
  changed = () => {}, tool = missing,
}: { changed?: () => void; tool?: BunToolResponse['tool'] | null }) {
  const api = useStudioApi()
  return <BunSetting api={api} tool={tool} onChanged={changed} />
}

it('saves a custom path, verifies it and shows the selected interpreter', async () => {
  setApiRoutes({ [`PUT ${PREFIX}/tools/bun`]: () => ({ tool: ready }) })
  const changed = vi.fn()
  render(<Harness changed={changed} />)
  await userEvent.type(screen.getByLabelText('Absolute path to Bun'), '/custom/bun')
  await userEvent.click(screen.getByRole('button', { name: 'Save and verify path' }))
  await screen.findByText('Bun is ready.')
  expect(apiCalls).toContainEqual({ method: 'PUT', path: `${PREFIX}/tools/bun`, body: { path: '/custom/bun' } })
  expect(screen.getByText('/custom/bun · 1.3.0')).toBeInTheDocument()
  expect(changed).toHaveBeenCalledOnce()
})

it('can discover a newly installed bun and clear a custom path without a restart', async () => {
  const automatic = { ...ready, configured_path: null, source: 'path' }
  setApiRoutes({
    [`POST ${PREFIX}/tools/bun/probe`]: () => ({ tool: ready }),
    [`PUT ${PREFIX}/tools/bun`]: () => ({ tool: automatic }),
  })
  render(<Harness />)
  await userEvent.click(screen.getByRole('button', { name: 'Detect Bun again' }))
  await screen.findByText('Bun is ready.')
  await userEvent.click(screen.getByRole('button', { name: 'Use automatic detection' }))
  await waitFor(() => expect(screen.getByLabelText('Absolute path to Bun')).toHaveValue(''))
  expect(apiCalls).toContainEqual({ method: 'PUT', path: `${PREFIX}/tools/bun`, body: { path: null } })
})

it('retains the entered path and reports a rejected configuration', async () => {
  setApiRoutes({
    [`PUT ${PREFIX}/tools/bun`]: () => { throw new StubApiError(503, { code: 'bun_missing', error: 'not executable' }) },
  })
  const changed = vi.fn()
  render(<Harness changed={changed} />)
  await userEvent.type(screen.getByLabelText('Absolute path to Bun'), '/bad/bun')
  await userEvent.click(screen.getByRole('button', { name: 'Save and verify path' }))
  await waitFor(() => expect(screen.getByRole('status')).not.toHaveTextContent('Checking Bun'))
  expect(screen.getByLabelText('Absolute path to Bun')).toHaveValue('/bad/bun')
  expect(screen.queryByText('Bun is ready.')).toBeNull()
  expect(changed).not.toHaveBeenCalled()
})

const external: BunToolResponse['tool'] = {
  ...ready, path: '/other/bun', configured_path: '/other/bun', version: '1.3.1', searched: ['/other/bun'],
}

const refreshedTools: [string, BunToolResponse['tool']][] = [
  ['another configured interpreter', external],
  ['automatic detection', { ...external, configured_path: null, source: 'path' }],
  ['a missing configured interpreter', { ...missing, configured_path: '/gone/bun', searched: ['/gone/bun'] }],
]

it.each(refreshedTools)('uses refreshed server configuration after saving: %s', async (_name, refreshed) => {
  setApiRoutes({ [`PUT ${PREFIX}/tools/bun`]: () => ({ tool: ready }) })
  const view = render(<Harness />)
  await userEvent.type(screen.getByLabelText('Absolute path to Bun'), '/custom/bun')
  await userEvent.click(screen.getByRole('button', { name: 'Save and verify path' }))
  await screen.findByText('/custom/bun · 1.3.0')

  view.rerender(<Harness tool={refreshed} />)

  await waitFor(() => expect(screen.getByLabelText('Absolute path to Bun')).toHaveValue(refreshed.configured_path ?? ''))
  expect(screen.queryByText('/custom/bun · 1.3.0')).toBeNull()
  if (refreshed.found) {
    expect(screen.getByText(`${refreshed.path} · ${refreshed.version}`)).toBeInTheDocument()
  } else {
    expect(screen.getByRole('status')).not.toHaveTextContent('Bun is ready.')
  }
  expect(apiCalls.filter((call) => call.method === 'PUT')).toHaveLength(1)
})

it('refreshes interpreter evidence without replacing an unsaved path', async () => {
  setApiRoutes({ [`POST ${PREFIX}/tools/bun/probe`]: () => ({ tool: ready }) })
  const view = render(<Harness />)
  await userEvent.click(screen.getByRole('button', { name: 'Detect Bun again' }))
  await screen.findByText('/custom/bun · 1.3.0')
  const input = screen.getByLabelText('Absolute path to Bun')
  await userEvent.clear(input)
  await userEvent.type(input, '/draft/bun')

  view.rerender(<Harness tool={external} />)

  await screen.findByText('/other/bun · 1.3.1')
  expect(input).toHaveValue('/draft/bun')
  expect(apiCalls.filter((call) => call.method === 'PUT')).toHaveLength(0)
})

it('does not resubmit the previous path after a server refresh', async () => {
  let selected = ready
  setApiRoutes({ [`PUT ${PREFIX}/tools/bun`]: () => ({ tool: selected }) })
  const view = render(<Harness />)
  await userEvent.type(screen.getByLabelText('Absolute path to Bun'), '/custom/bun')
  await userEvent.click(screen.getByRole('button', { name: 'Save and verify path' }))
  await screen.findByText('/custom/bun · 1.3.0')
  selected = external
  view.rerender(<Harness tool={external} />)
  await waitFor(() => expect(screen.getByLabelText('Absolute path to Bun')).toHaveValue('/other/bun'))

  await userEvent.click(screen.getByRole('button', { name: 'Save and verify path' }))

  expect(apiCalls.filter((call) => call.method === 'PUT').map((call) => call.body))
    .toEqual([{ path: '/custom/bun' }, { path: '/other/bun' }])
})

it('keeps settings usable when a health response contains only its top-level status', () => {
  render(<AboutPanel health={{ status: 'healthy', issues: [] } as unknown as HealthResponse} versions={null} />)
  expect(screen.getByText('Bundled payload')).toBeInTheDocument()
})
