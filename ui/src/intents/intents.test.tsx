/**
 * The intent inventory's risky paths, and the binding sequence the whole product rests on.
 *
 * Two matter most. `Run to next checkpoint` must create a queued command and send nothing: the row hands
 * the card to the Action Center, where the wire text is shown before it is sent, so a second entry point
 * cannot become a second way to dispatch without a confirmation.
 *
 * The session panel's tests assert the four calls of §2.13 as WIRE CALLS — method, path and body, in
 * order — through the app-sdk stub's route table rather than against a mocked client, because the order
 * and the bodies are the contract: the project must be set before Studio verifies it, and a decision on
 * an intent bound by any other sequence is refused `slot_mismatch`. A test that only checked "the client
 * was called" would pass with the PATCH sent as a POST, which the host answers 404 to.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider, makeI18n } from '../i18n'
import { StudioApiError, useStudioApi, type StudioApi } from '../lib/api'
import { buildRoute, parseRoute } from '../lib/route'
import type { ChatFolder, IntentSummary, RepoRecord, SlotView } from '../lib/types'
import { StudioApp } from '../shell/StudioApp'
import { API_BASE, actionCard, shellRoutes } from '../test/fixtures'
import { apiCalls, navigations, setApiRoutes, StubApiError } from '../test/stubs/app-sdk'
import { ApprovalWaitNotice } from './ApprovalWait'
import { IntentActions } from './IntentActions'
import { StoppedTurnNotice } from './StoppedTurn'
import { IntentList } from './IntentList'
import { activityText } from './IntentRow'
import { readInventory } from './IntentsView'
import { CONVERSATION_PICK_KEY, intentFolder, repoFolder, SessionPanel } from './SessionPanel'

const INTENT = {
  repo_id: 'r_1', repo_label: 'checkout-web', space: 'default', intent_dir: '250901-guest-checkout',
  intent_key: 'default~250901-guest-checkout', uuid: null, slug: 'guest-checkout', scope: 'feature',
  registry_status: 'Running', title: 'Guest checkout', operational_state: 'WaitingForYou',
  disk: {
    status: 'Running', lifecycle_phase: 'inception', current_stage: '2.3 functional-design',
    next_stage: '2.4 nfr-requirements', state_version: 6, total_stages: 33, completed: 7,
    revision_count: 1, parked_at: null, last_updated: '2026-09-05T08:00:00Z',
  },
  counts: { total: 33, not_started: 20, in_progress: 1, awaiting_approval: 1, revising: 0, completed: 7, skipped: 4, done: 11, unknown: 0, planned: 0, planned_done: 0 },
  open_actions: 2, blocking_findings: 0, warn_findings: 1,
  session: { slot_key: 's1', session_key: 'ses_1', running: false, bound_at: '2026-09-05T07:00:00Z' },
  archived: false, paused: false, interrupted: false,
  last_activity_at: '2026-09-05T08:00:00Z', stable_boundary: null, unstable: false, is_active_cursor: true,
} as unknown as IntentSummary

afterEach(() => window.history.replaceState(null, '', '/apps/aidlc-studio'))

describe('the intents inventory', () => {
  it('measures progress against the stages the plan runs, not the whole graph', () => {
    const express = { ...INTENT, counts: { ...INTENT.counts, planned: 7, planned_done: 3 } } as IntentSummary
    render(
      <I18nProvider>
        <IntentList intents={[express]} failures={[]} narrow={false} renderActions={() => null} />
      </I18nProvider>,
    )
    expect(screen.getByText('3 of 7 planned stages done or skipped')).toBeInTheDocument()
    expect(screen.queryByText('11 of 33 stages done or skipped')).toBeNull()
  })

  it('renders a row with its state and stage, and no keep-moving column', () => {
    render(
      <I18nProvider>
        <IntentList intents={[INTENT]} failures={[{ repo: 'payments-api', message: 'not reachable' }]} narrow={false} renderActions={() => null} />
      </I18nProvider>,
    )
    expect(screen.getByText('250901-guest-checkout')).toBeInTheDocument()
    expect(screen.getByText('Waiting for you')).toBeInTheDocument()
    expect(screen.getByText('2.3 functional-design')).toBeInTheDocument()
    expect(screen.getByText('11 of 33 stages done or skipped')).toBeInTheDocument()
    expect(screen.getByText('2 decisions waiting')).toBeInTheDocument()
    expect(screen.queryByText('Keep moving')).not.toBeInTheDocument()
    expect(screen.getAllByRole('columnheader')).toHaveLength(5)
    expect(screen.getByText('payments-api: not reachable')).toBeInTheDocument()
  })

  it('names the stage a unit-major walk is executing, not the block stage the state file keeps', () => {
    const walking = { ...INTENT, executing_stage: 'code-generation', disk: { ...INTENT.disk, current_stage: 'functional-design' } }
    render(
      <I18nProvider>
        <IntentList intents={[walking]} failures={[]} narrow={false} renderActions={() => null} />
      </I18nProvider>,
    )
    expect(screen.getByText('code-generation')).toBeInTheDocument()
    expect(screen.queryByText('functional-design')).not.toBeInTheDocument()
    // The file's "next" belongs to the stage the file names, so it is not shown beside another one.
    expect(screen.queryByText(/2\.4 nfr-requirements/)).not.toBeInTheDocument()
  })

  it('shows the text before sending, then queues and sends the run once; pauses behind a confirmation', async () => {
    // Nothing has run yet, so the run starts the workflow.
    const runnable = { ...INTENT, operational_state: 'Idle' as const,
      counts: { ...INTENT.counts, not_started: 33, in_progress: 0, awaiting_approval: 0, completed: 0, skipped: 0, done: 0 } }
    const card = actionCard({ action_id: 'a_9', type: 'run' })
    const receipt = {
      ok: true as const, action_id: 'a_9', status: 'Delivering' as const, lane: 'human_lane' as const,
      delivery_id: 'd_9', slot_key: 's1', session_key: 'ses_1', wire_text: '/aidlc', expires_at: '',
      lease_generation: 1, host: { method: 'POST', path: '/api/chat?ws=1', body: { slot: 's1', message: '/aidlc' } },
    }
    const api = {
      run: vi.fn(async () => ({ ok: true as const, action_id: 'a_9', status: 'Queued' as const, action: card })),
      submitAction: vi.fn(async () => receipt),
      listSlots: vi.fn(async () => [slot({ key: 's1' })]),
      sendToHost: vi.fn(async () => ({ ok: true })),
      reportDelivery: vi.fn(async () => ({ ok: true as const, action: { ...card, status: 'Delivered' }, idempotent: false })),
      pause: vi.fn(async () => ({ ok: true as const, binding: {} as never, blocked_actions: ['a_1', 'a_2'] })),
    } as unknown as StudioApi
    const onQueued = vi.fn()
    const onChanged = vi.fn()
    render(
      <I18nProvider>
        <IntentActions api={api} intent={runnable} onGo={() => {}} onQueued={onQueued} onChanged={onChanged} onRecompose={() => {}} onSession={() => {}} />
      </I18nProvider>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Run to next checkpoint' }))
    // Nothing leaves before the person has seen the exact text.
    expect(screen.getByText('Start the workflow?')).toBeInTheDocument()
    expect(api.run).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Send /aidlc' }))
    expect(await screen.findByText('Sent /aidlc. The workflow has started in the conversation.')).toBeInTheDocument()
    expect(api.run).toHaveBeenCalledWith('r_1', 'default~250901-guest-checkout', 'run')
    expect(api.submitAction).toHaveBeenCalledWith('a_9', expect.objectContaining({
      payload: { decision: 'run' }, client_wire_text: '/aidlc',
    }))
    expect(api.sendToHost).toHaveBeenCalledTimes(1)
    expect(api.sendToHost).toHaveBeenCalledWith('/api/chat?ws=1', { slot: 's1', message: '/aidlc' })
    expect(api.reportDelivery).toHaveBeenCalledWith('a_9', expect.objectContaining({ delivery_id: 'd_9', outcome: 'delivered' }))
    expect(onQueued).not.toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Pause' }))
    expect(screen.getByText('2 waiting decisions will refuse to send while this intent is paused.')).toBeInTheDocument()
    expect(api.pause).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(api.pause).toHaveBeenCalledWith('r_1', 'default~250901-guest-checkout', true))
    expect(onChanged).toHaveBeenCalledWith('Paused. 2 waiting decisions will now refuse to send.')
  })

  it('archives an intent that is waiting on you by pausing it first, in one confirmation', async () => {
    const calls: string[] = []
    const api = {
      pause: vi.fn(async () => {
        calls.push('pause')
        return { ok: true as const, binding: {} as never, blocked_actions: ['a_1', 'a_2'] }
      }),
      archive: vi.fn(async () => {
        calls.push('archive')
        return { ok: true as const, binding: {} as never }
      }),
    } as unknown as StudioApi
    const onChanged = vi.fn()
    render(
      <I18nProvider>
        <IntentActions api={api} intent={INTENT} onGo={() => {}} onQueued={() => {}} onChanged={onChanged} onRecompose={() => {}} onSession={() => {}} />
      </I18nProvider>,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Archive' }))
    // PRD §11.2 archives from Paused: the confirmation says so before anything happens.
    expect(screen.getByText('Pause and archive this intent?')).toBeInTheDocument()
    expect(screen.getByText(/It still has 2 decisions waiting for you, so it is paused first/)).toBeInTheDocument()
    expect(calls).toEqual([])

    await userEvent.click(screen.getByRole('button', { name: 'Pause and archive' }))
    await waitFor(() => expect(calls).toEqual(['pause', 'archive']))
    expect(api.pause).toHaveBeenCalledWith('r_1', 'default~250901-guest-checkout', true)
    expect(api.archive).toHaveBeenCalledWith('r_1', 'default~250901-guest-checkout', true)
    expect(onChanged).toHaveBeenCalledWith('Archived in Studio only. No AI-DLC file changed.')
  })

  it('restores an archived intent through the same archive call', async () => {
    const api = {
      archive: vi.fn(async () => ({ ok: true as const, binding: {} as never })),
    } as unknown as StudioApi
    const onChanged = vi.fn()
    render(
      <I18nProvider>
        <IntentActions api={api} intent={{ ...INTENT, archived: true }} onGo={() => {}} onQueued={() => {}} onChanged={onChanged} onRecompose={() => {}} onSession={() => {}} />
      </I18nProvider>,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Restore' }))
    expect(screen.getByText('Restore this intent?')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(api.archive).toHaveBeenCalledWith('r_1', 'default~250901-guest-checkout', false))
    expect(onChanged).toHaveBeenCalledWith('Restored. No AI-DLC file changed.')
  })

  it('says a decision is still on its way when the backend refuses an archive for that', async () => {
    const api = {
      pause: vi.fn(),
      archive: vi.fn(async () => {
        throw new StudioApiError('action_not_submittable', 'a decision for this intent may still be in flight',
          { operation: 'archive', reason: 'in_flight' }, 409)
      }),
    } as unknown as StudioApi
    render(
      <I18nProvider>
        <IntentActions api={api} intent={{ ...INTENT, paused: true }} onGo={() => {}} onQueued={() => {}} onChanged={() => {}} onRecompose={() => {}} onSession={() => {}} />
      </I18nProvider>,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Archive' }))
    expect(screen.getByText('Archive this intent?')).toBeInTheDocument()
    expect(screen.getByText('Its 2 waiting decisions are hidden from the Action Center until you restore it.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))

    expect(await screen.findByText(/A decision for this intent is being delivered/)).toBeInTheDocument()
    expect(api.pause).not.toHaveBeenCalled()
  })

  it('says the intent was paused, and reads it again, when the archive after the pause is refused', async () => {
    const api = {
      pause: vi.fn(async () => ({ ok: true as const, binding: {} as never, blocked_actions: ['a_1', 'a_2'] })),
      archive: vi.fn(async () => {
        throw new StudioApiError('action_not_submittable', 'a decision for this intent may still be in flight',
          { operation: 'archive', reason: 'in_flight' }, 409)
      }),
    } as unknown as StudioApi
    const onChanged = vi.fn()
    render(
      <I18nProvider>
        <IntentActions api={api} intent={INTENT} onGo={() => {}} onQueued={() => {}} onChanged={onChanged} onRecompose={() => {}} onSession={() => {}} />
      </I18nProvider>,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Archive' }))
    await userEvent.click(screen.getByRole('button', { name: 'Pause and archive' }))

    expect(await screen.findByText(/A decision for this intent is being delivered/)).toBeInTheDocument()
    // The pause stood: without a refresh the row went on showing an intent that still dispatches.
    expect(onChanged).toHaveBeenCalledWith('Paused. 2 waiting decisions will now refuse to send.')
  })

  it('offers no pause or rescope once the workflow is complete', () => {
    const done = { ...INTENT, disk: { ...INTENT.disk, status: 'Completed' } }
    // A completed intent reads the repository's Git to choose its lead action.
    const api = { repoGit: vi.fn(async () => ({ git: {}, owned_dirty: [], unrelated_dirty: 0, intent_dirty: [] })) } as unknown as StudioApi
    render(
      <I18nProvider>
        <IntentActions api={api} intent={done} onGo={() => {}} onQueued={() => {}} onChanged={() => {}}
          onRecompose={() => {}} onSession={() => {}} onSettings={() => {}} />
      </I18nProvider>,
    )
    const en = makeI18n('en-US')
    const why = en.t('intents.action.completedNoop')
    expect(screen.getByRole('button', { name: en.t('intents.action.pauseIdle') })).toBeDisabled()
    expect(screen.getByRole('button', { name: en.t('intents.action.pauseIdle') })).toHaveAttribute('title', why)
    expect(screen.getByRole('button', { name: en.t('workspace.settings.title') })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Archive' })).toBeEnabled()
  })

  it('offers Prepare commit once the workflow is complete, and opens its card for confirmation', async () => {
    const done = { ...INTENT, disk: { ...INTENT.disk, status: 'Completed' } }
    const card = actionCard({ action_id: 'a_commit', type: 'prepare_commit' })
    const api = {
      repoGit: vi.fn(async () => ({ git: {}, owned_dirty: [], unrelated_dirty: 0, intent_dirty: [] })),
      prepareCommit: vi.fn(async () => ({ ok: true as const, action_id: 'a_commit', status: 'Queued' as const, action: card })),
    } as unknown as StudioApi
    const onGo = vi.fn()
    render(
      <I18nProvider>
        <IntentActions api={api} intent={done} onGo={onGo} onQueued={() => {}} onChanged={() => {}}
          onRecompose={() => {}} onSession={() => {}} />
      </I18nProvider>,
    )
    const en = makeI18n('en-US')
    expect(screen.queryByRole('button', { name: en.t('intents.action.run') })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: en.t('intents.action.prepareCommit') }))
    expect(api.prepareCommit).toHaveBeenCalledWith(done.repo_id, done.intent_key)
    // Nothing is sent from here: the card opens where its confirmation shows the exact request.
    expect(onGo).toHaveBeenCalledWith({ view: 'actions', repo: done.repo_id, space: done.space,
      intent: done.intent_key, action: 'a_commit' })
  })

  it('leads with Prepare commit only while code the intent wrote is uncommitted', async () => {
    const done = { ...INTENT, disk: { ...INTENT.disk, status: 'Completed' } }
    const en = makeI18n('en-US')
    const button = async (dirty: { relpath: string; status: string; intent_dir: string }[]) => {
      const api = { repoGit: vi.fn(async () => ({ git: {}, owned_dirty: [], unrelated_dirty: 0, intent_dirty: dirty })),
                    prepareCommit: vi.fn() } as unknown as StudioApi
      const view = render(
        <I18nProvider>
          <IntentActions api={api} intent={done} onGo={() => {}} onQueued={() => {}} onChanged={() => {}}
            onRecompose={() => {}} onSession={() => {}} />
        </I18nProvider>,
      )
      await waitFor(() => expect(api.repoGit).toHaveBeenCalled())
      const found = screen.getByRole('button', { name: en.t('intents.action.prepareCommit') })
      return { found, view }
    }
    const dirty = await button([{ relpath: 'calc.py', status: 'M', intent_dir: done.intent_dir }])
    await waitFor(() => expect(dirty.found.className).toContain('studio-btn-primary'))
    dirty.view.unmount()
    const clean = await button([{ relpath: 'other.py', status: 'M', intent_dir: '260101-another' }])
    await waitFor(() => expect(clean.found).toHaveAttribute('title', en.t('intents.action.prepareCommitClean')))
    expect(clean.found.className).not.toContain('studio-btn-primary')
  })

  it('points at the Action Center when the run could not be sent', async () => {
    const runnable = { ...INTENT, operational_state: 'Idle' as const,
      counts: { ...INTENT.counts, awaiting_approval: 0 } }
    const card = actionCard({ action_id: 'a_9', type: 'run' })
    const api = {
      run: vi.fn(async () => ({ ok: true as const, action_id: 'a_9', status: 'Queued' as const, action: card })),
      submitAction: vi.fn(async () => { throw new StudioApiError('repo_busy', 'another operation holds this repository', {}, 409) }),
      sendToHost: vi.fn(),
    } as unknown as StudioApi
    const onQueued = vi.fn()
    render(
      <I18nProvider>
        <IntentActions api={api} intent={runnable} onGo={() => {}} onQueued={onQueued} onChanged={() => {}} onRecompose={() => {}} onSession={() => {}} />
      </I18nProvider>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Run to next checkpoint' }))
    await userEvent.click(screen.getByRole('button', { name: 'Send /aidlc' }))
    expect(await screen.findByText('/aidlc was not sent: another operation holds this repository')).toBeInTheDocument()
    expect(api.sendToHost).not.toHaveBeenCalled()
    expect(onQueued).toHaveBeenCalledWith('a_9', runnable)
  })

  it('says when the conversation is parked on a tool approval, and opens it', async () => {
    const parked = { ...INTENT, operational_state: 'Idle' as const, session: {
      ...INTENT.session!, running: true, waiting_approval: true,
      approval: { tool: 'Locate the load-steering instructions', tool_input: 'grep -n steering SKILL.md' },
    } } as IntentSummary
    render(<I18nProvider><IntentActions api={{} as StudioApi} intent={parked} onGo={() => {}}
      onQueued={() => {}} onChanged={() => {}} onRecompose={() => {}} onSession={() => {}} /></I18nProvider>)
    expect(screen.getByText('The conversation is waiting for you to approve a tool call.')).toBeInTheDocument()
    expect(screen.getByText('Locate the load-steering instructions')).toBeInTheDocument()
    expect(screen.getByText('grep -n steering SKILL.md')).toBeInTheDocument()
    navigations.length = 0
    await userEvent.click(screen.getByRole('button', { name: 'Open the conversation' }))
    expect(navigations).toEqual(['/chat?sid=s1'])
  })

  it('says what a waiting call is for, and warns when it reaches into another registered repository', () => {
    // The local-s3 conversation's request of 2026-11-03, as the host listed it: JSON arguments, cut at 500.
    const toolInput = JSON.stringify({
      __tool_use_purpose: 'See how the learnings questions were recorded in an earlier finished run',
      command: 'grep -rn -i -A 3 "keep\\|anything to add" /work/v2-feature/aidlc/spaces/default/intents/x/audit',
    }).slice(0, 200)
    const repos = [
      { repo_id: 'r_s3', label: 'local-s3', canonical_path: '/work/local-s3' },
      { repo_id: 'r_v2', label: 'v2-feature', canonical_path: '/work/v2-feature' },
    ] as RepoRecord[]
    render(<I18nProvider><ApprovalWaitNotice slotKey="s1" tool="Running: grep" toolInput={toolInput} repoId="r_s3"
      repos={repos} /></I18nProvider>)
    expect(screen.getByText(/What it is for, in its own words: See how the learnings questions were recorded/))
      .toBeInTheDocument()
    expect(screen.getByText(/^grep -rn -i -A 3/)).toBeInTheDocument()
    expect(screen.queryByText(/__tool_use_purpose/)).not.toBeInTheDocument()
    expect(screen.getByText(/It reaches into another registered repository: v2-feature \(\/work\/v2-feature\)/))
      .toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveAttribute('data-tone', 'danger')
    expect(screen.getByText(/YOLO stops asking in every conversation on this computer/)).toBeInTheDocument()
  })

  it('shows a shell call once when the host titles it with its command', () => {
    const command = 'cat /work/notes.md'
    render(<I18nProvider><ApprovalWaitNotice slotKey="s1" tool={`Running: ${command}`} repoId="r_s3"
      repos={[{ repo_id: 'r_s3', label: 'local-s3', canonical_path: '/work/local-s3' }] as RepoRecord[]}
      toolInput={JSON.stringify({ command })} /></I18nProvider>)
    expect(screen.getByText(command, { selector: 'code' })).toBeInTheDocument()
    expect(screen.queryByText(`Running: ${command}`)).not.toBeInTheDocument()
    expect(screen.getByText(/It also names paths outside this repository: \/work\/notes\.md/)).toBeInTheDocument()
  })

  it('keeps a multi-line command and a file write diff line by line', () => {
    // Enterprise run, 2026-11-04: a `python3 -c` program and a "Creating sample-tasks.json" diff were shown on one
    // line, the program's indentation and the diff's `+` lines run together.
    const program = "python3 -c \"\nfor f in ['tw-tags']:\n    t=open('/tmp/mr/'+f+'.html').read()\n    print(t[:60])\n\""
    const diff = '--- /private/tmp/fs/sample-tasks.json\n+++ /private/tmp/fs/sample-tasks.json\n@@ -0,0 +1,2 @@\n+[\n+]'
    const repos = [{ repo_id: 'r_s3', label: 'local-s3', canonical_path: '/work/local-s3' }] as RepoRecord[]
    const { unmount } = render(<I18nProvider><ApprovalWaitNotice slotKey="s1" tool="Running: python3 -c" repoId="r_s3"
      repos={repos} toolInput={JSON.stringify({ command: program })} /></I18nProvider>)
    const code = screen.getByText((_, el) => el?.tagName === 'CODE' && el.textContent === program)
    expect(code).toHaveClass('studio-verbatim')
    unmount()
    render(<I18nProvider><ApprovalWaitNotice slotKey="s1" tool="Creating sample-tasks.json" repoId="r_s3"
      repos={repos} toolInput={diff} /></I18nProvider>)
    expect(screen.getByText((_, el) => el?.tagName === 'CODE' && el.textContent === diff)).toHaveClass('studio-verbatim')
    expect(screen.getByText('Creating sample-tasks.json')).toBeInTheDocument()
  })

  it('says when a waiting call stays inside its repository', () => {
    const repos = [{ repo_id: 'r_s3', label: 'local-s3', canonical_path: '/work/local-s3' }] as RepoRecord[]
    render(<I18nProvider><ApprovalWaitNotice slotKey="s1" tool="" repoId="r_s3" repos={repos}
      toolInput={JSON.stringify({ command: 'bun .kiro/tools/aidlc.ts engine now > /dev/null' })} /></I18nProvider>)
    expect(screen.getByText('It names no path outside this repository.')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveAttribute('data-tone', 'warn')
  })

  it('offers no Allow once, and vouches for no reach, on a call shown only in part', () => {
    // 2026-11-04: a 907-character `python3 -c` program was cut at 500, and the notice still said it named no path
    // outside the repository and offered Allow once for what it did not show.
    setApiRoutes({})
    const repos = [{ repo_id: 'r_s3', label: 'local-s3', canonical_path: '/work/local-s3' }] as RepoRecord[]
    render(<I18nProvider><ApprovalWaitNotice slotKey="s1" tool="Running: python3 -c" toolInput={'python3 -c "print(1)"'}
      toolInputTruncated repoId="r_s3" repos={repos}
      target={{ request_id: 'tool-1', request_mid: 'mid-7', origin: 'native' }} /></I18nProvider>)
    expect(screen.getByText(/^This call is longer than Studio can show/)).toBeInTheDocument()
    expect(screen.queryByText('It names no path outside this repository.')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Allow once' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeInTheDocument()
  })

  it('answers a native request here with the host’s own call, once, and says it was taken', async () => {
    setApiRoutes({ ['POST /api/chat/slots/aidlc%20studio%2F1/approve']: () => ({ ok: true }) })
    const answered = vi.fn()
    render(<I18nProvider><ApprovalWaitNotice slotKey="aidlc studio/1" tool="" toolInput="go test ./..."
      target={{ request_id: 'tool-1', request_mid: 'mid-7', origin: 'native' }} onAnswered={answered} /></I18nProvider>)
    expect(screen.getByText(/^Allow once lets just this call run; Reject refuses it/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Allow once' }))
    expect(apiCalls.filter((call) => call.path.endsWith('/approve'))).toEqual([{
      method: 'POST', path: '/api/chat/slots/aidlc%20studio%2F1/approve',
      body: { origin: 'native', request_id: 'tool-1', request_mid: 'mid-7', action: 'approved' },
    }])
    expect(await screen.findByText('Allowed once. The conversation carries on.')).toBeInTheDocument()
    expect(answered).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: 'Allow once' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reject' })).not.toBeInTheDocument()
  })

  it('rejects only this call, and reads a request answered elsewhere as gone', async () => {
    setApiRoutes({ ['POST /api/chat/slots/s1/approve']: () => {
      throw new StubApiError(404, { error: 'no pending approval', code: 'approval_not_pending' })
    } })
    render(<I18nProvider><ApprovalWaitNotice slotKey="s1" tool="" toolInput="rm -rf build"
      target={{ request_id: 'tool-2', request_mid: 'mid-8', origin: 'native' }} /></I18nProvider>)
    await userEvent.click(screen.getByRole('button', { name: 'Reject' }))
    expect((apiCalls.at(-1)?.body as { action: string }).action).toBe('rejected_once')
    expect(await screen.findByText(/^This request no longer waits/)).toBeInTheDocument()
  })

  it('leaves a sub-agent’s request, or one the host names no row for, to the conversation', () => {
    for (const target of [{ request_id: 'a', request_mid: 'b', origin: 'coordinator' }, { request_id: 'a', origin: 'native' },
      undefined]) {
      const { unmount } = render(<I18nProvider><ApprovalWaitNotice slotKey="s1" tool="" toolInput="ls" target={target} />
      </I18nProvider>)
      expect(screen.queryByRole('button', { name: 'Allow once' })).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Open the conversation' })).toBeInTheDocument()
      expect(screen.getByText(/^In the conversation, Allow once/)).toBeInTheDocument()
      unmount()
    }
  })

  it('names the intent when the wait is listed away from its row', () => {
    render(<I18nProvider><ApprovalWaitNotice slotKey="aidlc studio/1" tool="" toolInput="go test ./..."
      label="devlake / 261001-servicenow-plugin" /></I18nProvider>)
    expect(screen.getByText('devlake / 261001-servicenow-plugin is waiting for you to approve a tool call.')).toBeInTheDocument()
    expect(screen.getByText('go test ./...')).toBeInTheDocument()
  })

  it('says when the last turn stopped on a host error, what sending again does, and opens the conversation', async () => {
    const stoppedAt = new Date(Date.now() - 3 * 60_000).toISOString()
    const stopped = { ...INTENT, operational_state: 'Idle' as const, counts: { ...INTENT.counts, awaiting_approval: 0 },
      session: { ...INTENT.session!, stopped_on_error: {
        at: stoppedAt, text: 'The model backend hit a transient error (HTTP 5xx).', kind: '' } } } as IntentSummary
    render(<I18nProvider><IntentActions api={{} as StudioApi} intent={stopped} onGo={() => {}}
      onQueued={() => {}} onChanged={() => {}} onRecompose={() => {}} onSession={() => {}} /></I18nProvider>)
    expect(screen.getByText(/^The last turn stopped on an error .*ago\.$/)).toBeInTheDocument()
    expect(screen.getByText('The model backend hit a transient error (HTTP 5xx).')).toBeInTheDocument()
    expect(screen.getByText('AI-DLC keeps what it already wrote. Run to next checkpoint sends /aidlc again to pick up from there.'))
      .toBeInTheDocument()
    navigations.length = 0
    await userEvent.click(screen.getByRole('button', { name: 'Open the conversation' }))
    expect(navigations).toEqual(['/chat?sid=s1'])
  })

  it('does not promise a retry helps when the host error needs a fix in KiroCrew', () => {
    render(<I18nProvider><StoppedTurnNotice slotKey="s1" at="2026-09-05T08:00:00Z" kind="usage_limit"
      text="The monthly usage limit has been reached." label="devlake / 261001-servicenow-plugin" /></I18nProvider>)
    expect(screen.getByText(/^devlake \/ 261001-servicenow-plugin: the last turn stopped on an error /)).toBeInTheDocument()
    expect(screen.getByText('Sending /aidlc again stops the same way until this is fixed in KiroCrew; the conversation offers the fix.'))
      .toBeInTheDocument()
  })

  it('says nothing about an old error while a turn runs', () => {
    const running = { ...INTENT, operational_state: 'Running' as const, session: { ...INTENT.session!, running: true,
      stopped_on_error: { at: '2026-09-05T08:00:00Z', text: 'An old error.', kind: '' } } } as IntentSummary
    render(<I18nProvider><IntentActions api={{} as StudioApi} intent={running} onGo={() => {}}
      onQueued={() => {}} onChanged={() => {}} onRecompose={() => {}} onSession={() => {}} /></I18nProvider>)
    expect(screen.queryByText('An old error.')).toBeNull()
  })

  it('does not offer Run before a conversation is bound', () => {
    const unbound = { ...INTENT, operational_state: 'Idle' as const, session: null,
      counts: { ...INTENT.counts, awaiting_approval: 0 } } as IntentSummary
    render(<I18nProvider><IntentActions api={{} as StudioApi} intent={unbound} onGo={() => {}}
      onQueued={() => {}} onChanged={() => {}} onRecompose={() => {}} onSession={() => {}} /></I18nProvider>)
    const button = screen.getByRole('button', { name: 'Run to next checkpoint' })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('title', 'Create and bind the conversation first.')
  })

  // A completed workflow shows Prepare commit in Run's place (tested below), so it offers no Run at all.
  it.each(['approval', 'question', 'running'])('does not offer another Run during %s', async (kind) => {
    const value: IntentSummary = { ...INTENT, operational_state: 'Idle',
      counts: { ...INTENT.counts, awaiting_approval: 0 } }
    if (kind === 'approval') value.counts.awaiting_approval = 1
    if (kind === 'question') value.operational_state = 'WaitingForYou'
    if (kind === 'running') value.session = { ...INTENT.session!, running: true }
    const api = { run: vi.fn() } as unknown as StudioApi
    render(<I18nProvider><IntentActions api={api} intent={value} onGo={() => {}}
      onQueued={() => {}} onChanged={() => {}} onRecompose={() => {}} onSession={() => {}} /></I18nProvider>)
    const button = screen.getByRole('button', { name: 'Run to next checkpoint' })
    expect(button).toBeDisabled()
    await userEvent.click(button)
    expect(api.run).not.toHaveBeenCalled()
  })

  it('reports the repositories it could not read instead of dropping them', async () => {
    const api = {
      intents: vi.fn(async (repoId: string) => {
        if (repoId === 'r_2') throw new StudioApiError('repo_unavailable', 'nope', {}, 409)
        return { intents: [INTENT], spaces: ['default'], active_space: 'default' }
      }),
    } as unknown as StudioApi
    const repos = [
      { repo_id: 'r_1', label: 'checkout-web' },
      { repo_id: 'r_2', label: 'payments-api' },
    ] as never
    const answer = await readInventory(api, repos, { space: '', state: '', q: '', archived: false }, new AbortController().signal, (e) => e.code)
    expect(answer.intents).toHaveLength(1)
    expect(answer.failures).toEqual([{ repo: 'payments-api', message: 'repo_unavailable' }])
    expect(api.intents).toHaveBeenCalledWith('r_1', {}, expect.anything())
  })

  it('opens the scoped decision queue without retaining an answered action or its artifact tab', async () => {
    const capturedIntent = { ...actionCard().intent, intent_key: INTENT.intent_key }
    const answered = actionCard({
      action_id: 'a_answered', type: 'question', status: 'ResolvedNoTransition', intent: capturedIntent,
    })
    const current = actionCard({
      action_id: 'a_current_failure', type: 'failure', status: 'Failed', intent: capturedIntent,
    })
    setApiRoutes({
      ...shellRoutes([current]),
      [`GET ${API_BASE}/actions/${answered.action_id}`]: () => ({ action: answered, transitions: [], drafts: [] }),
      [`GET ${API_BASE}/repos*`]: () => ({
        repos: [{
          repo_id: INTENT.repo_id, label: INTENT.repo_label, canonical_path: '/work/checkout-web',
          availability: 'available', counts: { intents: 1 },
        }],
        totals: { repos: 1, unavailable: 0, open_actions: 1 },
      }),
      [`GET ${API_BASE}/repos/${INTENT.repo_id}/intents`]: () => ({
        intents: [INTENT], spaces: ['default'], active_space: 'default',
      }),
    })
    window.history.replaceState(null, '', buildRoute({
      view: 'intents', repo: INTENT.repo_id, intent: INTENT.intent_key,
      action: answered.action_id, tab: 'artifacts', artifact: 'art_previous',
      stage: 'requirements-analysis', unit: 'unit_previous', anchor: 'h-old-question',
    }))
    render(<I18nProvider><StudioApp /></I18nProvider>)

    await userEvent.click(await screen.findByRole('button', { name: 'Open decision' }))
    expect(parseRoute(window.location.search, window.location.hash)).toMatchObject({
      view: 'actions', repo: INTENT.repo_id, space: INTENT.space, intent: INTENT.intent_key,
      action: '', artifact: '', tab: 'decision', stage: '', unit: '', anchor: '',
    })
    expect(await screen.findByText('Select an item')).toBeInTheDocument()
    expect(apiCalls.some((call) => call.path === `${API_BASE}/actions/${answered.action_id}`)).toBe(false)
    expect(apiCalls.some((call) => call.path.includes('/artifacts/'))).toBe(false)
    await userEvent.click(await screen.findByRole('button', { name: /^Failure/ }))
    await waitFor(() => expect(apiCalls.some((call) => call.path === `${API_BASE}/actions/${current.action_id}`)).toBe(true))
    expect(parseRoute(window.location.search, window.location.hash)).toMatchObject({
      action: current.action_id, artifact: '', tab: 'decision',
    })
    expect(apiCalls.every((call) => call.method === 'GET')).toBe(true)
  })
})

/** `GET /repos` with the one repository the inventory rows belong to. */
const oneRepo = (intents: number) => ({
  [`GET ${API_BASE}/repos*`]: () => ({
    repos: [{
      repo_id: INTENT.repo_id, label: INTENT.repo_label, canonical_path: '/work/checkout-web',
      availability: 'available', counts: { intents },
    }],
    totals: { repos: 1, unavailable: 0, open_actions: 0 },
  }),
})

describe('a link to an intent\'s conversation', () => {
  it('opens that intent\'s conversation panel and then drops the tab from the route', async () => {
    const unbound = { ...INTENT, session: null } as IntentSummary
    setApiRoutes({
      ...shellRoutes([]),
      [`GET ${API_BASE}/repos*`]: () => ({
        repos: [{
          repo_id: INTENT.repo_id, label: INTENT.repo_label, canonical_path: '/work/checkout-web',
          availability: 'available', counts: { intents: 1 },
        }],
        totals: { repos: 1, unavailable: 0, open_actions: 0 },
      }),
      [`GET ${API_BASE}/repos/${INTENT.repo_id}/intents`]: () => ({
        intents: [unbound], spaces: ['default'], active_space: 'default',
      }),
      ['GET /api/chat/slots']: () => [],
    })
    window.history.replaceState(null, '', buildRoute({
      view: 'intents', repo: INTENT.repo_id, intent: INTENT.intent_key, tab: 'conversation',
    }))
    render(<I18nProvider><StudioApp /></I18nProvider>)

    expect(await screen.findByRole('button', { name: 'Create and bind the conversation' })).toBeInTheDocument()
    await waitFor(() => expect(parseRoute(window.location.search, window.location.hash)).toMatchObject({
      view: 'intents', repo: INTENT.repo_id, intent: '', tab: 'decision',
    }))
    expect(apiCalls.every((call) => call.method === 'GET')).toBe(true)
  })

  it('is not followed when the Intents page is reached from the top bar, and the intent stays in scope', async () => {
    setApiRoutes({
      ...shellRoutes([]),
      ...oneRepo(1),
      [`GET ${API_BASE}/repos/${INTENT.repo_id}/intents`]: () => ({
        intents: [{ ...INTENT, session: null }], spaces: ['default'], active_space: 'default',
      }),
      ['GET /api/chat/slots']: () => [],
    })
    // A card's Conversation tab, open in the Action Center.
    window.history.replaceState(null, '', buildRoute({
      view: 'actions', repo: INTENT.repo_id, intent: INTENT.intent_key, tab: 'conversation',
    }))
    render(<I18nProvider><StudioApp /></I18nProvider>)

    await userEvent.click(await screen.findByRole('button', { name: 'Intents' }))
    expect(await screen.findByText(INTENT.title!)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Create and bind the conversation' })).toBeNull()
    expect(parseRoute(window.location.search, window.location.hash)).toMatchObject({
      view: 'intents', repo: INTENT.repo_id, intent: INTENT.intent_key, tab: 'decision',
    })
  })
})

describe('the conversation panel, opened from one row and then another', () => {
  it('shows the second intent\'s own binding, not the one just made for the first', async () => {
    const first = { ...INTENT, intent_dir: '250901-a', intent_key: '250901-a', title: 'First', session: null } as IntentSummary
    const second = { ...first, intent_dir: '250902-b', intent_key: '250902-b', title: 'Second' } as IntentSummary
    setApiRoutes({
      ...shellRoutes([]),
      ...oneRepo(2),
      [`GET ${API_BASE}/repos/r_1/intents`]: () => ({ intents: [first, second], spaces: ['default'], active_space: 'default' }),
      ['GET /api/chat/slots']: () => [{ key: 'slot-of-a', agent: 'aidlc', project: '/work/checkout-web', title: 'A conversation' }],
      [`POST ${API_BASE}/repos/r_1/intents/250901-a/session`]: () => ({
        ok: true, binding: { slot_key: 'slot-of-a', session_key: 'ses_a' }, slot: null,
      }),
    })
    window.history.replaceState(null, '', buildRoute({ view: 'intents' }))
    render(<I18nProvider><StudioApp /></I18nProvider>)
    const bindFor = async (title: string) =>
      within((await screen.findByText(title)).closest('tr, li') as HTMLElement).getByRole('button', { name: 'Bind a conversation' })

    await userEvent.click(await bindFor('First'))
    await userEvent.click(screen.getByRole('button', { name: 'Bind an existing conversation' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Bind this one' }))
    const panelA = screen.getByRole('region', { name: 'Conversation for 250901-a' })
    await waitFor(() => expect(within(panelA).getByText('slot-of-a')).toBeInTheDocument())

    await userEvent.click(await bindFor('Second'))
    const panelB = await screen.findByRole('region', { name: 'Conversation for 250902-b' })
    // Kept from A, the slot made "Open conversation" open A's chat from B's panel.
    expect(within(panelB).queryByText('slot-of-a')).toBeNull()
    expect(within(panelB).getByRole('button', { name: 'Create and bind the conversation' })).toBeInTheDocument()
  })
})

// --------------------------------------------------------------------------- //
// the canonical conversation
// --------------------------------------------------------------------------- //

const BASE = '/api/apps/aidlc-studio'
const CHAT = '/api/chat'
/** `SLOT_KEY_TEMPLATE` for this fixture: `aidlc-studio-{repo_id}-{intent_dir}`. */
const SLOT = 'aidlc-studio-r_1-250901-guest-checkout'
const INTENT_PATH = `${BASE}/repos/r_1/intents/default~250901-guest-checkout`

const REPO = { repo_id: 'r_1', label: 'checkout-web', canonical_path: '/work/checkout-web' } as unknown as RepoRecord

/** Only the four fields the panel reads; a real `SlotView` has 24 more the host fills in. */
const slot = (over: Partial<SlotView>): SlotView =>
  ({ key: 'k', project: '/work/checkout-web', agent: 'aidlc', running: false, title: '', ...over }) as unknown as SlotView

const UNBOUND = { ...INTENT, session: null } as IntentSummary
/** An idle intent with nothing waiting: the one a first conversation is created for. */
const RUNNABLE_UNBOUND = {
  ...UNBOUND, operational_state: 'Idle', counts: { ...INTENT.counts, awaiting_approval: 0 },
} as IntentSummary
const RUNNABLE_BOUND = { ...RUNNABLE_UNBOUND, session: INTENT.session } as IntentSummary

/**
 * The panel with a REAL client, so the assertions are about the requests that leave the browser.
 *
 * `useStudioApi()` is the same hook the app uses; the app-sdk stub dispatches its paths to the route
 * table and records every call, which is the only way to prove the PATCH is a PATCH.
 */
function Panel({ intent, onChanged = () => {} }: { intent: IntentSummary; onChanged?: (message?: string) => void }) {
  const api = useStudioApi()
  return <SessionPanel api={api} repo={REPO} intent={intent} onClose={() => {}} onChanged={onChanged} />
}

const mount = (intent: IntentSummary, onChanged?: (message?: string) => void) =>
  render(
    <I18nProvider>
      <Panel intent={intent} {...(onChanged ? { onChanged } : {})} />
    </I18nProvider>,
  )

/** A sidebar folder as `GET /api/chat/folders` lists it, with only the fields Studio reads. */
const folder = (over: Partial<ChatFolder>): ChatFolder =>
  ({ id: 'f', name: 'f', parent_id: '', project_dir: '', order: 0, ...over }) as ChatFolder

/** The host's model and effort lists, which the unbound panel reads on its own whenever it opens. */
const HOST_LISTS = ['/api/models', '/api/effort-levels']
/** What the panel SENT, without those reads: the create-and-bind sequence the host saw, in order. */
const sent = () => apiCalls.filter((call) => !HOST_LISTS.includes(call.path))

const MODELS = [
  { model_name: 'auto', model_id: 'auto', rate_multiplier: 1.0 },
  { model_name: 'claude-sonnet-5.5', model_id: 'claude-sonnet-5.5', rate_multiplier: 1.3 },
]
const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']

/** Steps 1-6 answered, and a host folder store that hands out ids in order. */
function bindRoutes(folders: ChatFolder[], extra: Record<string, (body: unknown) => unknown> = {}) {
  let made = 0
  setApiRoutes({
    [`POST ${CHAT}/slots`]: () => ({ key: SLOT, agent: 'default', agent_kind: 'member', project: '' }),
    [`PATCH ${CHAT}/slots/${SLOT}/title`]: () => ({ ok: true }),
    [`POST ${CHAT}/slots/${SLOT}/project`]: () => ({ ok: true, project: '/work/checkout-web' }),
    [`POST ${CHAT}/slots/${SLOT}/agent`]: () => ({ ok: true, agent: 'aidlc', agent_kind: 'template' }),
    [`POST ${INTENT_PATH}/session`]: () => ({
      ok: true,
      binding: { slot_key: SLOT, session_key: 'ses_7' },
      slot: slot({ key: SLOT, running: true }),
    }),
    [`GET ${CHAT}/folders`]: () => folders,
    [`POST ${CHAT}/folders`]: (body) => folder({ ...(body as Partial<ChatFolder>), id: `f_new${++made}` }),
    [`PATCH ${CHAT}/slots/${SLOT}/folder`]: (body) => ({ ok: true, ...(body as object) }),
    ...extra,
  })
}

/**
 * Born in the intent's folder, pointed at the repo (the stub host inherits nothing, so the project is still
 * set), only then switched to the repo's own aidlc agent, bound, and filed (the stub ignored `folder_id`).
 */
const slotCalls = (folderId: string) => [
  { method: 'POST', path: `${CHAT}/slots`, body: { name: SLOT, folder_id: folderId } },
  { method: 'PATCH', path: `${CHAT}/slots/${SLOT}/title`, body: { title: 'checkout-web / guest-checkout' } },
  { method: 'POST', path: `${CHAT}/slots/${SLOT}/project`, body: { project: '/work/checkout-web' } },
  { method: 'POST', path: `${CHAT}/slots/${SLOT}/agent`, body: { agent: 'aidlc', agent_kind: 'template' } },
  { method: 'POST', path: `${INTENT_PATH}/session`, body: { slot_key: SLOT } },
  { method: 'PATCH', path: `${CHAT}/slots/${SLOT}/folder`, body: { folder_id: folderId } },
]

describe('the time under an intent\'s state', () => {
  it('says how long it waited only while it waits on the person, and otherwise when it last moved', () => {
    const i18n = makeI18n('en-US')
    const twoMinutesAgo = new Date(Date.now() - 120_000).toISOString()
    const at = (operational_state: string, last_activity_at: string | null) =>
      activityText(i18n, { ...INTENT, operational_state, last_activity_at } as IntentSummary)
    expect(at('WaitingForYou', twoMinutesAgo)).toMatch(/^waiting /)
    expect(at('Running', twoMinutesAgo)).toMatch(/^last workflow step /)
    expect(at('Running', twoMinutesAgo)).not.toMatch(/waiting/)
    expect(at('Idle', null)).toBe('no activity yet')
  })
})

describe('the canonical conversation panel', () => {
  it('creates, titles, points and binds the conversation, then files it under the repository folder', async () => {
    bindRoutes([
      folder({ id: 'f_other', name: 'payments', project_dir: '/work/payments-api', order: 1 }),
      folder({ id: 'f_repo', name: 'Checkout', project_dir: '/work/checkout-web/', order: 3 }),
    ])
    const onChanged = vi.fn()
    mount(UNBOUND, onChanged)

    await userEvent.click(screen.getByRole('button', { name: 'Create and bind the conversation' }))

    await waitFor(() => expect(screen.getByText('ses_7')).toBeInTheDocument())
    expect(
      await screen.findByText('Filed in the sidebar under Checkout / 250901-guest-checkout.'),
    ).toBeInTheDocument()
    expect(sent()).toEqual([
      { method: 'GET', path: `${CHAT}/folders`, body: undefined },
      {
        method: 'POST', path: `${CHAT}/folders`,
        body: { name: '250901-guest-checkout', parent_id: 'f_repo', project_dir: '/work/checkout-web' },
      },
      ...slotCalls('f_new1'),
    ])
    // The bound state is shown from the answer, not from a refetch the caller may not have made yet.
    expect(screen.getByText(SLOT)).toBeInTheDocument()
    expect(screen.getByText('A turn is running')).toBeInTheDocument()
    expect(onChanged).toHaveBeenCalledWith(`Bound to ${SLOT}.`)
  })

  it('creates, binds, files and starts the workflow in one click, sending the text it showed', async () => {
    const runCard = actionCard({ action_id: 'a_run', type: 'run' })
    bindRoutes([folder({ id: 'f_repo', name: 'Checkout', project_dir: '/work/checkout-web' })], {
      [`POST ${INTENT_PATH}/run`]: () => ({ ok: true, action_id: 'a_run', status: 'Queued', action: runCard }),
      [`POST ${BASE}/actions/a_run/submit`]: () => ({
        ok: true, action_id: 'a_run', status: 'Delivering', lane: 'human_lane', delivery_id: 'd_run',
        slot_key: SLOT, session_key: 'ses_7', wire_text: '/aidlc', expires_at: '', lease_generation: 1,
        host: { method: 'POST', path: `${CHAT}?ws=1`, body: { slot: SLOT, message: '/aidlc' } },
      }),
      [`GET ${CHAT}/slots`]: () => [slot({ key: SLOT })],
      [`POST ${CHAT}?ws=1`]: () => ({ ok: true, slot: SLOT }),
      [`POST ${BASE}/actions/a_run/delivery`]: () => ({ ok: true, action: { ...runCard, status: 'Delivered' }, idempotent: false }),
    })
    mount(RUNNABLE_UNBOUND)

    await userEvent.click(screen.getByRole('button', { name: 'Create the conversation and continue (sends /aidlc)' }))

    expect(await screen.findByText('Sent /aidlc. The workflow is continuing in the conversation.')).toBeInTheDocument()
    const paths = apiCalls.map((call) => `${call.method} ${call.path}`)
    expect(paths.slice(-5)).toEqual([
      `POST ${INTENT_PATH}/run`,
      `POST ${BASE}/actions/a_run/submit`,
      `GET ${CHAT}/slots`,
      `POST ${CHAT}?ws=1`,
      `POST ${BASE}/actions/a_run/delivery`,
    ])
    expect(paths.filter((path) => path === `POST ${CHAT}?ws=1`)).toHaveLength(1)
    expect(apiCalls.find((call) => call.path === `${BASE}/actions/a_run/submit`)?.body).toMatchObject({
      payload: { decision: 'run' }, client_wire_text: '/aidlc',
    })
    // Started here, so running: no "Not running", and no second /aidlc on offer.
    expect(screen.getByText('A turn is running')).toBeInTheDocument()
    expect(screen.queryByText('Not running')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Start the workflow (sends /aidlc)' })).toBeNull()
  })

  it('creates and binds only, sending nothing, when asked to', async () => {
    bindRoutes([folder({ id: 'f_repo', name: 'Checkout', project_dir: '/work/checkout-web' })])
    mount(RUNNABLE_UNBOUND)

    await userEvent.click(screen.getByRole('button', { name: 'Create and bind only' }))

    expect(
      await screen.findByText('Filed in the sidebar under Checkout / 250901-guest-checkout.'),
    ).toBeInTheDocument()
    expect(apiCalls.map((call) => call.path)).not.toContain(`${INTENT_PATH}/run`)
    // The panel names the conversation by the title it just set, not "Unavailable" until a re-read.
    const titled = apiCalls.find((call) => call.path.endsWith('/title'))
    const title = (titled?.body as { title?: string } | undefined)?.title
    expect(title).toBeTruthy()
    expect(screen.getByText(title as string)).toBeInTheDocument()
  })

  it('starts an already bound, idle intent from the panel', async () => {
    const runCard = actionCard({ action_id: 'a_run', type: 'run' })
    setApiRoutes({
      [`POST ${INTENT_PATH}/run`]: () => ({ ok: true, action_id: 'a_run', status: 'Queued', action: runCard }),
      [`POST ${BASE}/actions/a_run/submit`]: () => ({
        ok: true, action_id: 'a_run', status: 'Delivering', lane: 'human_lane', delivery_id: 'd_run',
        slot_key: 's1', session_key: 'ses_1', wire_text: '/aidlc', expires_at: '', lease_generation: 1,
        host: { method: 'POST', path: `${CHAT}?ws=1`, body: { slot: 's1', message: '/aidlc' } },
      }),
      [`GET ${CHAT}/slots`]: () => [slot({ key: 's1' })],
      [`POST ${CHAT}?ws=1`]: () => ({ ok: true, slot: 's1' }),
      [`POST ${BASE}/actions/a_run/delivery`]: () => ({ ok: true, action: { ...runCard, status: 'Delivered' }, idempotent: false }),
    })
    mount({ ...RUNNABLE_UNBOUND, session: INTENT.session } as IntentSummary)

    // Seven stages are done, so the panel says the run continues the workflow rather than starts it.
    expect(screen.queryByRole('button', { name: 'Start the workflow (sends /aidlc)' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Continue the workflow (sends /aidlc)' }))

    expect(await screen.findByText('Sent /aidlc. The workflow is continuing in the conversation.')).toBeInTheDocument()
    expect(apiCalls.filter((call) => call.path === `${CHAT}?ws=1`)).toHaveLength(1)
  })

  it('asks to continue, not start, a run on an intent AI-DLC has already worked on', async () => {
    const api = { run: vi.fn() } as unknown as StudioApi
    render(
      <I18nProvider>
        <IntentActions api={api} intent={RUNNABLE_BOUND} onGo={() => {}} onQueued={() => {}} onChanged={() => {}} onRecompose={() => {}} onSession={() => {}} />
      </I18nProvider>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Run to next checkpoint' }))
    expect(screen.getByRole('group', { name: 'Continue the workflow?' })).toBeInTheDocument()
    expect(screen.getByText(/AI-DLC picks up where it stopped/)).toBeInTheDocument()
    expect(screen.queryByText('Start the workflow?')).toBeNull()
    expect(api.run).not.toHaveBeenCalled()
  })

  it('reuses the intent folder a previous binding made', async () => {
    bindRoutes([
      folder({ id: 'f_repo', name: 'Checkout', project_dir: '/work/checkout-web' }),
      folder({ id: 'f_intent', name: '250901-guest-checkout', parent_id: 'f_repo', project_dir: '/work/checkout-web' }),
    ])
    mount(UNBOUND)

    await userEvent.click(screen.getByRole('button', { name: 'Create and bind the conversation' }))

    expect(
      await screen.findByText('Filed in the sidebar under Checkout / 250901-guest-checkout.'),
    ).toBeInTheDocument()
    expect(sent()).toEqual([{ method: 'GET', path: `${CHAT}/folders`, body: undefined }, ...slotCalls('f_intent')])
  })

  it('creates the conversation in its folder, so a slot born with the repository is not pointed at it again', async () => {
    // Pointing a slot at a new project arms KiroCrew's deferred session reset, which a new slot cannot spend
    // before its first turn; it fired after that turn and the next decision lost the whole conversation.
    bindRoutes([
      folder({ id: 'f_repo', name: 'Checkout', project_dir: '/work/checkout-web' }),
      folder({ id: 'f_intent', name: '250901-guest-checkout', parent_id: 'f_repo', project_dir: '/work/checkout-web' }),
    ], {
      [`POST ${CHAT}/slots`]: () => ({ key: SLOT, agent: 'default', project: '/work/checkout-web/', folder_id: 'f_intent' }),
    })
    mount(UNBOUND)

    await userEvent.click(screen.getByRole('button', { name: 'Create and bind the conversation' }))

    expect(
      await screen.findByText('Filed in the sidebar under Checkout / 250901-guest-checkout.'),
    ).toBeInTheDocument()
    const paths = apiCalls.map((call) => `${call.method} ${call.path}`)
    expect(paths).not.toContain(`POST ${CHAT}/slots/${SLOT}/project`)
    expect(paths).not.toContain(`PATCH ${CHAT}/slots/${SLOT}/folder`)
    expect(paths).toContain(`POST ${CHAT}/slots/${SLOT}/agent`)
  })

  it('makes the repository folder and the intent folder when the sidebar has none for this repo', async () => {
    bindRoutes([folder({ id: 'f_other', name: 'payments', project_dir: '/work/payments-api' })])
    mount(UNBOUND)

    await userEvent.click(screen.getByRole('button', { name: 'Create and bind the conversation' }))

    expect(
      await screen.findByText('Filed in the sidebar under checkout-web / 250901-guest-checkout.'),
    ).toBeInTheDocument()
    expect(sent()).toEqual([
      { method: 'GET', path: `${CHAT}/folders`, body: undefined },
      { method: 'POST', path: `${CHAT}/folders`, body: { name: 'checkout-web', project_dir: '/work/checkout-web' } },
      {
        method: 'POST', path: `${CHAT}/folders`,
        body: { name: '250901-guest-checkout', parent_id: 'f_new1', project_dir: '/work/checkout-web' },
      },
      ...slotCalls('f_new2'),
    ])
  })

  it('files into the repository folder itself when the host will not let it nest there', async () => {
    bindRoutes([folder({ id: 'f_repo', name: 'Checkout', project_dir: '/work/checkout-web' })], {
      [`POST ${CHAT}/folders`]: () => {
        throw new StubApiError(403, {
          error: 'cannot create a folder inside one this app does not own', code: 'folder_not_owned',
        })
      },
    })
    mount(UNBOUND)

    await userEvent.click(screen.getByRole('button', { name: 'Create and bind the conversation' }))

    expect(await screen.findByText('Filed in the sidebar under Checkout.')).toBeInTheDocument()
    expect(apiCalls.at(-1)).toEqual({
      method: 'PATCH', path: `${CHAT}/slots/${SLOT}/folder`, body: { folder_id: 'f_repo' },
    })
  })

  it('keeps the binding when the host refuses the folder, and says why', async () => {
    bindRoutes([], {
      [`POST ${CHAT}/folders`]: () => {
        throw new StubApiError(429, { error: 'too many folders created recently; retry shortly' })
      },
    })
    const onChanged = vi.fn()
    mount(UNBOUND, onChanged)

    await userEvent.click(screen.getByRole('button', { name: 'Create and bind the conversation' }))

    expect(
      await screen.findByText(
        'The conversation is bound, but it could not be filed into a sidebar folder: too many folders created recently; retry shortly',
      ),
    ).toBeInTheDocument()
    expect(screen.getByText('ses_7')).toBeInTheDocument()
    expect(onChanged).toHaveBeenCalledWith(`Bound to ${SLOT}.`)
    expect(apiCalls.map((call) => call.path)).not.toContain(`${CHAT}/slots/${SLOT}/folder`)
  })

  it('names the step the host refused and does not bind a slot the host would not point at the repo', async () => {
    setApiRoutes({
      [`POST ${CHAT}/slots`]: () => ({ key: SLOT }),
      [`PATCH ${CHAT}/slots/${SLOT}/title`]: () => ({ ok: true }),
      [`POST ${CHAT}/slots/${SLOT}/project`]: () => {
        throw new StubApiError(403, { error: 'refusing a sensitive path: /work/checkout-web' })
      },
      [`POST ${INTENT_PATH}/session`]: () => ({ ok: true, binding: { slot_key: SLOT }, slot: null }),
    })
    mount(UNBOUND)

    await userEvent.click(screen.getByRole('button', { name: 'Create and bind the conversation' }))

    expect(
      await screen.findByText(/Step 3 of 7 was refused \(point it at this repository\)/),
    ).toBeInTheDocument()
    // The host's own words, verbatim: a 403 with no code would otherwise read as "you are not the owner".
    expect(screen.getByText(/refusing a sensitive path: \/work\/checkout-web/)).toBeInTheDocument()
    expect(apiCalls.map((call) => call.path)).not.toContain(`${INTENT_PATH}/session`)
    expect(screen.getByRole('button', { name: 'Create and bind the conversation' })).toBeInTheDocument()
  })

  it('offers only aidlc conversations on this repository, and binds the one chosen', async () => {
    setApiRoutes({
      [`GET ${CHAT}/slots`]: () => [
        // A trailing slash is not a difference; the host reports realpaths.
        slot({ key: 'aidlc-hand-made', project: '/work/checkout-web/', title: 'Guest checkout' }),
        slot({ key: 'general-chat', agent: 'general' }),
        slot({ key: 'aidlc-payments', project: '/work/payments-api' }),
      ],
      [`POST ${INTENT_PATH}/session`]: () => ({
        ok: true,
        binding: { slot_key: 'aidlc-hand-made', session_key: 'ses_2' },
        slot: null,
      }),
    })
    const onChanged = vi.fn()
    mount(UNBOUND, onChanged)

    await userEvent.click(screen.getByRole('button', { name: 'Bind an existing conversation' }))

    expect(await screen.findByText('aidlc-hand-made')).toBeInTheDocument()
    // A slot the backend would answer `slot_mismatch` to is never offered.
    expect(screen.queryByText('general-chat')).not.toBeInTheDocument()
    expect(screen.queryByText('aidlc-payments')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Bind this one' }))
    await waitFor(() =>
      expect(apiCalls.at(-1)).toEqual({
        method: 'POST',
        path: `${INTENT_PATH}/session`,
        body: { slot_key: 'aidlc-hand-made' },
      }),
    )
    expect(screen.getByText('ses_2')).toBeInTheDocument()
    expect(onChanged).toHaveBeenCalledWith('Bound to aidlc-hand-made.')
  })

  it('reports a refused agent switch as its own step and binds nothing', async () => {
    // KiroCrew 0.8 refuses `aidlc` for a slot that does not yet have the repository as its project; the
    // switch now comes after step 3, and a refusal there must not be mistaken for a create or bind failure.
    setApiRoutes({
      [`POST ${CHAT}/slots`]: () => ({ key: SLOT }),
      [`PATCH ${CHAT}/slots/${SLOT}/title`]: () => ({ ok: true }),
      [`POST ${CHAT}/slots/${SLOT}/project`]: () => ({ ok: true, project: '/work/checkout-web' }),
      [`POST ${CHAT}/slots/${SLOT}/agent`]: () => {
        throw new StubApiError(409, { error: 'the selected agent choice is not available', code: 'agent_choice_unavailable' })
      },
      [`POST ${INTENT_PATH}/session`]: () => ({ ok: true, binding: { slot_key: SLOT }, slot: null }),
    })
    mount(UNBOUND)

    await userEvent.click(screen.getByRole('button', { name: 'Create and bind the conversation' }))

    expect(
      await screen.findByText(/Step 4 of 7 was refused \(switch it to the aidlc agent\)/),
    ).toBeInTheDocument()
    expect(apiCalls.map((call) => call.path)).not.toContain(`${INTENT_PATH}/session`)
  })

  it('offers Kiro default first and sends no model when it is kept', async () => {
    bindRoutes([], { [`GET /api/models`]: () => MODELS, [`GET /api/effort-levels`]: () => EFFORTS })
    mount(UNBOUND)

    const model = screen.getByRole('combobox', { name: 'Model' })
    expect(model).toHaveValue('')
    await waitFor(() => expect(within(model).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Kiro default', 'auto · 1×', 'claude-sonnet-5.5 · 1.3×',
    ]))
    expect(screen.getByRole('combobox', { name: 'Reasoning effort' })).toHaveValue('')

    await userEvent.click(screen.getByRole('button', { name: 'Create and bind the conversation' }))
    await waitFor(() => expect(sent().map((call) => call.path)).toContain(`${INTENT_PATH}/session`))
    const paths = sent().map((call) => call.path)
    expect(paths).not.toContain(`${CHAT}/slots/${SLOT}/model`)
    expect(paths).not.toContain(`${CHAT}/slots/${SLOT}/reasoning-effort`)
  })

  it('sets the picked model, then the effort, after the agent and before the bind, and remembers the pick', async () => {
    bindRoutes([], {
      [`GET /api/models`]: () => MODELS,
      [`GET /api/effort-levels`]: () => EFFORTS,
      [`POST ${CHAT}/slots/${SLOT}/model`]: (body) => ({ ok: true, ...(body as object) }),
      [`POST ${CHAT}/slots/${SLOT}/reasoning-effort`]: (body) => ({ ok: true, ...(body as object) }),
    })
    mount(UNBOUND)

    const model = screen.getByRole('combobox', { name: 'Model' })
    await waitFor(() => expect(within(model).getAllByRole('option')).toHaveLength(3))
    await userEvent.selectOptions(model, 'claude-sonnet-5.5')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Reasoning effort' }), 'high')
    await userEvent.click(screen.getByRole('button', { name: 'Create and bind the conversation' }))

    await waitFor(() => expect(sent().map((call) => call.path)).toContain(`${INTENT_PATH}/session`))
    const order = sent().map((call) => `${call.method} ${call.path}`)
    const at = (entry: string) => order.indexOf(entry)
    expect(at(`POST ${CHAT}/slots/${SLOT}/agent`)).toBeLessThan(at(`POST ${CHAT}/slots/${SLOT}/model`))
    expect(at(`POST ${CHAT}/slots/${SLOT}/model`)).toBeLessThan(at(`POST ${CHAT}/slots/${SLOT}/reasoning-effort`))
    expect(at(`POST ${CHAT}/slots/${SLOT}/reasoning-effort`)).toBeLessThan(at(`POST ${INTENT_PATH}/session`))
    expect(sent().find((call) => call.path.endsWith('/model'))?.body).toEqual({ model: 'claude-sonnet-5.5' })
    expect(sent().find((call) => call.path.endsWith('/reasoning-effort'))?.body).toEqual({ reasoning_effort: 'high' })
    expect(JSON.parse(localStorage.getItem(CONVERSATION_PICK_KEY) ?? 'null'))
      .toEqual({ model: 'claude-sonnet-5.5', effort: 'high' })
  })

  it('starts from the remembered pick, and keeps one the account no longer lists', async () => {
    localStorage.setItem(CONVERSATION_PICK_KEY, JSON.stringify({ model: 'claude-retired-1', effort: 'max' }))
    bindRoutes([], { [`GET /api/models`]: () => MODELS, [`GET /api/effort-levels`]: () => EFFORTS })
    mount(UNBOUND)

    const model = screen.getByRole('combobox', { name: 'Model' })
    expect(model).toHaveValue('claude-retired-1')
    expect(within(model).getByRole('option', { name: 'claude-retired-1 (not offered by your account now)' }))
      .toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Reasoning effort' })).toHaveValue('max')
  })

  it('names a refused model as its own step and binds nothing', async () => {
    localStorage.setItem(CONVERSATION_PICK_KEY, JSON.stringify({ model: 'claude-sonnet-5.5', effort: '' }))
    bindRoutes([], {
      [`GET /api/models`]: () => MODELS,
      [`POST ${CHAT}/slots/${SLOT}/model`]: () => {
        throw new StubApiError(400, { error: 'model not available for this account', code: 'model_unavailable' })
      },
    })
    mount(UNBOUND)

    await userEvent.click(screen.getByRole('button', { name: 'Create and bind the conversation' }))

    expect(await screen.findByText(/Step 5 of 7 was refused \(set the model you picked\)/)).toBeInTheDocument()
    expect(screen.getByText(/model not available for this account/)).toBeInTheDocument()
    expect(sent().map((call) => call.path)).not.toContain(`${INTENT_PATH}/session`)
  })

  it('says why only Kiro default is offered when the host cannot list models, and still creates', async () => {
    bindRoutes([], {
      [`GET /api/models`]: () => {
        throw new StubApiError(503, { error: 'model list unavailable' })
      },
    })
    mount(UNBOUND)

    expect(await screen.findByText(/KiroCrew could not list your models right now/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Create and bind the conversation' }))
    await waitFor(() => expect(sent().map((call) => call.path)).toContain(`${INTENT_PATH}/session`))
  })

  it('reads no model list for an intent that is already bound', () => {
    setApiRoutes({ [`GET ${CHAT}/slots`]: () => [] })
    mount(INTENT)
    expect(apiCalls.map((call) => call.path).filter((path) => HOST_LISTS.includes(path))).toEqual([])
  })

  it('numbers a refused bind of an existing conversation as the bind step', async () => {
    setApiRoutes({
      [`GET ${CHAT}/slots`]: () => [slot({ key: 'aidlc-hand-made', project: '/work/checkout-web' })],
      [`POST ${INTENT_PATH}/session`]: () => {
        throw new StubApiError(409, { error: 'the slot is not bound to this repository', code: 'slot_mismatch' })
      },
    })
    mount(UNBOUND)

    await userEvent.click(screen.getByRole('button', { name: 'Bind an existing conversation' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Bind this one' }))

    expect(await screen.findByText(/Step 6 of 7 was refused \(bind it to this intent\)/)).toBeInTheDocument()
  })

  it('shows the bound conversation and unbinds it', async () => {
    setApiRoutes({
      [`GET ${CHAT}/slots`]: () => [slot({ key: 's1', title: 'checkout-web / guest-checkout' })],
      [`POST ${INTENT_PATH}/session`]: () => ({ ok: true, binding: { slot_key: null, session_key: null }, slot: null }),
    })
    const onChanged = vi.fn()
    mount(INTENT, onChanged)

    expect(screen.getByText('s1')).toBeInTheDocument()
    expect(screen.getByText('ses_1')).toBeInTheDocument()
    expect(screen.getByText('Not running')).toBeInTheDocument()
    // named by the title KiroCrew's sidebar shows, and opened through KiroCrew's own chat route
    expect(await screen.findByText('checkout-web / guest-checkout')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Open in KiroCrew' }))
    expect(navigations.at(-1)).toBe('/chat?sid=s1')

    await userEvent.click(screen.getByRole('button', { name: 'Unbind' }))

    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Create and bind the conversation' })).toBeInTheDocument(),
    )
    expect(apiCalls.filter((call) => call.method !== 'GET')).toEqual([
      { method: 'POST', path: `${INTENT_PATH}/session`, body: { slot_key: null } },
    ])
    expect(onChanged).toHaveBeenCalledWith(
      'Unbound. Decisions for this intent will refuse to send until a conversation is bound.',
    )
  })

  it('moves a bound intent to a candidate the backend offered, with the backend\'s own reason', async () => {
    setApiRoutes({
      [`POST ${INTENT_PATH}/session/takeover/preview`]: () => ({
        candidates: [{ slot: slot({ key: 'aidlc-hand-made', title: 'Guest checkout' }), reason: 'project_match' }],
        current: { slot_key: 's1' },
      }),
      [`POST ${INTENT_PATH}/session`]: () => ({
        ok: true,
        binding: { slot_key: 'aidlc-hand-made', session_key: 'ses_3' },
        slot: null,
      }),
    })
    mount(INTENT)

    await userEvent.click(screen.getByRole('button', { name: 'Move to another conversation' }))
    expect(await screen.findByText('Open on this repository')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Move it here' }))
    await waitFor(() =>
      expect(apiCalls.at(-1)).toEqual({
        method: 'POST',
        path: `${INTENT_PATH}/session`,
        body: { slot_key: 'aidlc-hand-made' },
      }),
    )
    expect(screen.getByText('ses_3')).toBeInTheDocument()
    // The candidate came with its title, so the panel names the conversation without asking the host again.
    expect(screen.getByText('Guest checkout')).toBeInTheDocument()
  })
})


describe('the sidebar folder an intent is filed under', () => {
  const tree = [
    folder({ id: 'nested', name: 'Checkout (old)', parent_id: 'work', project_dir: '/work/checkout-web', order: 0 }),
    folder({ id: 'work', name: 'Work', order: 0 }),
    folder({ id: 'top_late', name: 'Checkout', project_dir: '/work/checkout-web', order: 5 }),
    folder({ id: 'top_early', name: 'checkout-web', project_dir: '/work/checkout-web/', order: 2 }),
  ]

  it('prefers the shallowest folder for the repository, then sidebar order', () => {
    expect(repoFolder(tree, '/work/checkout-web')?.id).toBe('top_early')
    expect(repoFolder(tree, '/work/payments-api')).toBeNull()
  })

  it('finds an intent folder only inside its repository folder', () => {
    const withIntent = [...tree, folder({ id: 'i', name: '250901-guest-checkout', parent_id: 'top_early' })]
    expect(intentFolder(withIntent, 'top_early', '250901-guest-checkout')?.id).toBe('i')
    expect(intentFolder(withIntent, 'top_late', '250901-guest-checkout')).toBeNull()
  })
})
