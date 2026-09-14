import { expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { useStudioApi } from '../lib/api'
import { apiCalls, setApiRoutes, StubApiError } from '../test/stubs/app-sdk'
import { MaintenancePanel } from './MaintenancePanel'

const BASE = '/api/apps/aidlc-studio/repos/r_one/maintenance'
const ID = 'tx_0000000000000001:failed'
const PATH = 'failed/tx_0000000000000001'
const PROTECTED = 'backups/tx_0000000000000002'
const DIGEST = 'a'.repeat(64)
function preview(ids: string[]) {
  return {
    repo_id: 'r_one', entry_ids: ids, plan_digest: DIGEST, can_cleanup: ids.length > 0, blocked: [],
    entries: [
      { id: ID, txid: 'tx_0000000000000001', category: 'failed', relative_path: PATH,
        status: 'failed', size_bytes: 1024, files: 2, protected: false, reason: null, selected: ids.includes(ID) },
      { id: 'tx_0000000000000002:backups', txid: 'tx_0000000000000002', category: 'backups', relative_path: PROTECTED,
        status: 'committed', size_bytes: 4096, files: 8, protected: true, reason: 'current_receipt', selected: false },
    ],
    totals: { entries: 2, eligible_entries: 1, protected_entries: 1, selected_entries: ids.length,
      eligible_bytes: 1024, selected_bytes: ids.length * 1024, selected_files: ids.length * 2, unknown_sizes: 0 },
  }
}
function Harness({ changed = () => {} }: { changed?: () => void }) {
  const api = useStudioApi()
  return <MaintenancePanel api={api} repoId="r_one" onClose={() => {}} onChanged={changed} />
}

it('protects current rollback backups and sends only the reviewed cleanup selection', async () => {
  const changed = vi.fn()
  setApiRoutes({
    [`POST ${BASE}/preview`]: (body) => preview((body as { entry_ids: string[] }).entry_ids),
    [`POST ${BASE}/cleanup`]: () => ({ ok: true, plan_digest: DIGEST, deleted: [ID], failures: [],
      remaining: [], record_key: 'maintenance:record', requires_preview: false }),
  })
  render(<Harness changed={changed} />)
  expect(await screen.findByLabelText(PROTECTED)).toBeDisabled()
  await userEvent.click(screen.getByLabelText(PATH))
  expect(screen.queryByRole('button', { name: 'Delete the reviewed directories' })).toBeNull()
  await userEvent.click(screen.getByRole('button', { name: 'Preview selected cleanup' }))
  const confirm = await screen.findByRole('button', { name: 'Delete the reviewed directories' })
  expect(apiCalls.filter((call) => call.path.endsWith('/cleanup'))).toHaveLength(0)
  await userEvent.click(confirm)
  await screen.findByText('Directories removed: 1. Transaction history was retained.')
  expect(apiCalls).toContainEqual({
    method: 'POST', path: `${BASE}/cleanup`, body: { entry_ids: [ID], plan_digest: DIGEST },
  })
  expect(changed).toHaveBeenCalledOnce()
})

it('invalidates confirmation on selection changes and never retries a stale deletion automatically', async () => {
  setApiRoutes({
    [`POST ${BASE}/preview`]: (body) => preview((body as { entry_ids: string[] }).entry_ids),
    [`POST ${BASE}/cleanup`]: () => { throw new StubApiError(409, { code: 'install_conflict', error: 'preview changed' }) },
  })
  render(<Harness />)
  await userEvent.click(await screen.findByLabelText(PATH))
  await userEvent.click(screen.getByRole('button', { name: 'Preview selected cleanup' }))
  await screen.findByRole('button', { name: 'Delete the reviewed directories' })
  await userEvent.click(screen.getByLabelText(PATH))
  expect(screen.queryByRole('button', { name: 'Delete the reviewed directories' })).toBeNull()
  await userEvent.click(screen.getByLabelText(PATH))
  await userEvent.click(screen.getByRole('button', { name: 'Preview selected cleanup' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Delete the reviewed directories' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Preview selected cleanup' })).toBeEnabled())
  expect(screen.queryByRole('button', { name: 'Delete the reviewed directories' })).toBeNull()
  expect(apiCalls.filter((call) => call.path.endsWith('/cleanup'))).toHaveLength(1)
})
