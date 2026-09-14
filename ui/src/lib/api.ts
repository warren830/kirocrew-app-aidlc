/**
 * The typed client over the host's `useAppApi()`.
 *
 * Three things this module exists to get right, each of which is a real failure otherwise:
 *
 * 1. **Errors arrive as a string.** The SDK throws `new Error(\`API ${status}: ${text}\`)` — no status
 *    field, no body object (`03 §2.2`). Every caller that wanted to branch on `action_stale` would
 *    otherwise parse that message itself, and one of them would get the regex wrong on a body
 *    containing a newline. `decodeError` does it once, in one place, and hands back
 *    `{ code, message, details, status }`.
 * 2. **Reads are cancellable, mutations are not.** `get(path, init)` forwards `signal` to `fetch`, so
 *    an abandoned read is really aborted. `post/put/del` take no `init` at all, and that is the safer
 *    default anyway: a `POST /actions/{id}/submit` that the browser stops waiting for has still
 *    committed a `Delivering` record on the server, so "cancelling" it would only hide a delivery the
 *    user is accountable for (§2.4 at-most-once). Mutations therefore expose no signal.
 * 3. **Every path is absolute and inside the declared prefixes.** The SDK allowlist is prefix-based
 *    over `permissions.api` (`/api/apps/aidlc-studio`, `/api/chat`); a relative path is resolved
 *    against `http://localhost` and silently becomes the wrong URL.
 */

import { useMemo } from 'react'
import { useAppApi } from '@kirocrew/app-sdk'
import type { WorkspaceApi } from '../plan/workspaceTypes'

import { ERROR_CODES, type ErrorCode } from './errorCodes.generated'
import type {
  ActionDetailResponse, ActionResponse, ActionsResponse, ActivityResponse, AddRepoResponse,
  AdvisorDraftGetResponse, AdvisorDraftResponse, AdvisorKind, ArtifactResponse, ArtifactsResponse,
  BindResponse, BindingResponse, BunToolResponse, CalibrationClearResponse, CalibrationResponse, CommandResponse,
  DeliveryReport, DeliveryResponse, DiagnosticsBundle, DirectoryResponse, DoctorResponse, EventPollResponse,
  HealthResponse, HostChatReceipt, IntentCreateResponse, IntentGitResponse, IntentResponse,
  IntentsResponse, LeasesResponse, MapResponse, MaintenancePreview, MaintenanceResult, MigrationApplyResponse, MigrationPreviewResponse,
  MigrationStatusResponse, Organize, PauseResponse, PayloadResponse, PlanAdviseRequest, PlanPreviewResponse,
  PlanRequest, PreflightResponse, PreviewPlanResponse, QuestionsResponse, ReceiptsResponse,
  RecomposePreviewResponse, RecomposeResponse, RemoveRepoResponse, RepoDetailResponse,
  RepoGitResponse, RepoResponse, ReposResponse, RescanResponse, ResolvePayload, ResolveResponse,
  ReviewResponse, SettingsResponse, SettingsValues, SlackCallbackResponse, SlotView, SubmitReceipt,
  SubmitRequest, TakeoverPreviewResponse, TransactionAcceptedResponse, TransactionResponse, TransactionKind,
} from './types'

/** Studio's own routes. The host mounts `AppRoute.path`s under this prefix (`01 §1.2`). */
export const API_BASE = '/api/apps/aidlc-studio'
/** The host's chat API — the only other prefix in `permissions.api` (§2.13). */
export const CHAT_BASE = '/api/chat'

/**
 * The host dispatches this when the session cookie is gone (`07 §1.6`). Every request after it will
 * fail with 401, so the UI shows one banner instead of a toast per resource.
 */
export const AUTH_REQUIRED_EVENT = 'mc-auth-required'

/** What the SDK's `Error.message` looks like: `API 409: {"error":…}`. `s` flag: bodies contain `\n`. */
const MESSAGE_RE = /^API (\d{3}): (.*)$/s

/** Codes that mean "you are no longer signed in", rather than "this request was wrong". */
const AUTH_CODES: readonly string[] = ['unauthorized', 'owner_required', 'app_token_forbidden']

/** A decoded non-2xx answer. `message` is the server's prose; the UI renders `errors.<code>`. */
export class StudioApiError extends Error {
  readonly code: string
  readonly details: Record<string, unknown>
  readonly status: number
  /**
   * The parsed response body, verbatim.
   *
   * The two-phase submit has to report what the HOST said in `DeliveryReport.receipt`, and the host's
   * chat errors are `{error, code?}` rather than Studio's `{error, code, details}`. Rebuilding that
   * from `code`/`message` would put Studio's words in a field the backend verifies as the host's.
   */
  readonly body: unknown

  constructor(code: string, message: string, details: Record<string, unknown>, status: number, body: unknown = null) {
    super(message)
    this.name = 'StudioApiError'
    this.code = code
    this.details = details
    this.status = status
    this.body = body
  }

  /** True when this is a known backend code, so `errors.<code>` is guaranteed to exist. */
  get known(): boolean {
    return this.code in ERROR_CODES
  }

  /** True when the answer says the dashboard session, not the request, is the problem. */
  get authRequired(): boolean {
    return AUTH_CODES.includes(this.code)
  }
}

/** True when the request was aborted by us, which is not a failure worth showing. */
export function isAbort(error: unknown): boolean {
  return (
    error instanceof DOMException
      ? error.name === 'AbortError'
      : error instanceof Error && error.name === 'AbortError'
  )
}

/**
 * Turn whatever the SDK threw into a `StudioApiError`.
 *
 * Falls back to `internal_error` rather than inventing a code: a body that is not Studio's JSON
 * (a proxy's HTML 502, an empty 500) must still reach the user as a message they can act on, and a
 * fabricated `repo_busy` would send them looking for a lease that does not exist.
 */
export function decodeError(error: unknown): StudioApiError {
  if (error instanceof StudioApiError) return error
  const raw = error instanceof Error ? error.message : String(error)
  const match = MESSAGE_RE.exec(raw)
  if (!match) return new StudioApiError('internal_error', raw || 'request failed', {}, 0)
  const status = Number(match[1])
  const body = match[2] ?? ''
  try {
    const parsed: unknown = JSON.parse(body)
    if (parsed && typeof parsed === 'object') {
      const rec = parsed as Record<string, unknown>
      const code = typeof rec['code'] === 'string' ? rec['code'] : statusCode(status)
      const message = typeof rec['error'] === 'string' ? rec['error'] : body
      const details =
        rec['details'] && typeof rec['details'] === 'object'
          ? (rec['details'] as Record<string, unknown>)
          : {}
      return new StudioApiError(code, message, details, status, parsed)
    }
  } catch {
    // Not JSON: the host's own 404 (`{"error":"not found"}` is JSON, a gateway's is not).
  }
  return new StudioApiError(statusCode(status), body || raw, {}, status)
}

/** The least-wrong code for a status with no body code. */
function statusCode(status: number): ErrorCode {
  if (status === 401) return 'unauthorized'
  if (status === 403) return 'owner_required'
  if (status === 404) return 'route_not_found'
  if (status === 429) return 'rate_limited'
  return 'internal_error'
}

function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '' || value === false) continue
    search.set(key, value === true ? '1' : String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

/** Options a read accepts. Reads are the only calls that may be abandoned. */
export interface ReadOptions {
  signal?: AbortSignal
}

export interface QueueFilter {
  organize?: Organize
  repo?: string
  space?: string
  intent?: string
  type?: string
  status?: string
  include?: 'revision'
  limit?: number
}

export interface ActivityFilter {
  repo?: string
  space?: string
  intent?: string
  source?: string
  stage?: string
  kind?: string
  type?: string
  severity?: string
  since?: string
  until?: string
  cursor?: string
  limit?: number
}

export interface StudioApi extends WorkspaceApi {
  // -- diagnostics and policy -------------------------------------------- //
  health(o?: ReadOptions): Promise<HealthResponse>
  configureBun(path: string | null): Promise<BunToolResponse>
  probeBun(): Promise<BunToolResponse>
  payload(includeFiles?: boolean, o?: ReadOptions): Promise<PayloadResponse>
  leases(o?: ReadOptions): Promise<LeasesResponse>
  diagnostics(opts?: { export?: boolean; includeHumanText?: boolean } & ReadOptions): Promise<DiagnosticsBundle>
  settings(o?: ReadOptions): Promise<SettingsResponse>
  putSettings(patch: DeepPartial<SettingsValues>): Promise<SettingsResponse>
  calibration(o?: ReadOptions): Promise<CalibrationResponse>
  clearCalibration(): Promise<CalibrationClearResponse>
  migrationStatus(o?: ReadOptions): Promise<MigrationStatusResponse>
  migrationPreview(): Promise<MigrationPreviewResponse>
  migrationApply(sourceSha256: string): Promise<MigrationApplyResponse>

  // -- events and activity ------------------------------------------------ //
  pollEvents(cursor: number | null, limit?: number, o?: ReadOptions): Promise<EventPollResponse>
  activity(filter?: ActivityFilter, o?: ReadOptions): Promise<ActivityResponse>

  // -- actions ------------------------------------------------------------ //
  actions(filter?: QueueFilter, o?: ReadOptions): Promise<ActionsResponse>
  action(actionId: string, o?: ReadOptions): Promise<ActionDetailResponse>
  submitAction(actionId: string, body: SubmitRequest): Promise<SubmitReceipt>
  reportDelivery(actionId: string, body: DeliveryReport): Promise<DeliveryResponse>
  retryAction(actionId: string): Promise<ActionResponse>
  reconcileAction(actionId: string): Promise<ActionResponse>
  cancelAction(actionId: string, reason?: string | null): Promise<ActionResponse>
  resolveAction(actionId: string, payload: ResolvePayload): Promise<ResolveResponse>

  // -- advisor and slack -------------------------------------------------- //
  requestDraft(body: { action_id: string; kind: AdvisorKind; question_index?: number | null; locale: string }): Promise<AdvisorDraftResponse>
  draft(draftId: string, o?: ReadOptions): Promise<AdvisorDraftGetResponse>
  slackCallback(body: { action_id: string; slot_key: string; channel: string; thread_ts: string; message_ts: string; selection: string | null }): Promise<SlackCallbackResponse>

  // -- repos -------------------------------------------------------------- //
  repos(includeArchived?: boolean, o?: ReadOptions): Promise<ReposResponse>
  repo(repoId: string, o?: ReadOptions): Promise<RepoDetailResponse>
  preflight(path: string): Promise<PreflightResponse>
  addRepo(path: string, label?: string | null): Promise<AddRepoResponse>
  removeRepo(repoId: string): Promise<RemoveRepoResponse>
  updateRepoMetadata(repoId: string, patch: { label?: string; archived?: boolean }): Promise<RepoResponse>
  directories(path?: string | null): Promise<DirectoryResponse>
  maintenancePreview(repoId: string, entryIds?: string[]): Promise<MaintenancePreview>
  cleanupMaintenance(repoId: string, entryIds: string[], planDigest: string): Promise<MaintenanceResult>
  rebindRepo(repoId: string, path: string): Promise<RepoResponse>
  rescanRepo(repoId: string): Promise<RescanResponse>
  doctorRepo(repoId: string): Promise<DoctorResponse>
  installPreview(repoId: string, kind?: TransactionKind): Promise<PreviewPlanResponse>
  installApply(repoId: string, planDigest: string, kind?: TransactionKind): Promise<TransactionAcceptedResponse>
  transaction(repoId: string, transactionId: string, o?: ReadOptions): Promise<TransactionResponse>
  cancelTransaction(repoId: string, transactionId: string): Promise<TransactionResponse>
  receipts(repoId: string, o?: ReadOptions): Promise<ReceiptsResponse>
  repoGit(repoId: string, o?: ReadOptions): Promise<RepoGitResponse>

  // -- intents ------------------------------------------------------------ //
  intents(repoId: string, filter?: { space?: string; state?: string; include_archived?: boolean; q?: string }, o?: ReadOptions): Promise<IntentsResponse>
  intent(repoId: string, intentKey: string, o?: ReadOptions): Promise<IntentResponse>
  map(repoId: string, intentKey: string, density?: 'overview' | 'detailed' | 'dependencies', o?: ReadOptions): Promise<MapResponse>
  artifacts(repoId: string, intentKey: string, filter?: { stage?: string; unit?: string; kind?: string }, o?: ReadOptions): Promise<ArtifactsResponse>
  artifact(repoId: string, intentKey: string, artifactId: string, o?: ReadOptions): Promise<ArtifactResponse>
  questions(repoId: string, intentKey: string, o?: ReadOptions): Promise<QuestionsResponse>
  review(repoId: string, intentKey: string, o?: ReadOptions): Promise<ReviewResponse>
  intentGit(repoId: string, intentKey: string, o?: ReadOptions): Promise<IntentGitResponse>
  intentActivity(repoId: string, intentKey: string, limit?: number, o?: ReadOptions): Promise<ActivityResponse>
  planPreview(repoId: string, body: PlanRequest): Promise<PlanPreviewResponse>
  /** Ask the Advisor to propose the plan settings for an intent that does not exist yet (202; FR-NEW-006). */
  requestPlanDraft(repoId: string, body: PlanAdviseRequest): Promise<AdvisorDraftResponse>
  createIntent(repoId: string, body: PlanRequest & { confirm_plan_digest: string }): Promise<IntentCreateResponse>
  compileRuntime(repoId: string, intentKey: string): Promise<{ ok: boolean; intent_key: string; runtime_graph_present: boolean }>
  recomposePreview(repoId: string, intentKey: string, body: { skip: string[]; add: string[] }): Promise<RecomposePreviewResponse>
  recompose(repoId: string, intentKey: string, body: { skip: string[]; add: string[]; proposal_digest: string }): Promise<RecomposeResponse>
  run(repoId: string, intentKey: string): Promise<CommandResponse>
  resume(repoId: string, intentKey: string): Promise<CommandResponse>
  prepareCommit(repoId: string, intentKey: string): Promise<CommandResponse>
  pause(repoId: string, intentKey: string, paused: boolean): Promise<PauseResponse>
  forceStop(repoId: string, intentKey: string): Promise<SubmitReceipt>
  /**
   * Always rejects with `machine_lane_unavailable` (§2.3). Present so the client surface matches the
   * route table and so the one place that could ever call it is the one place that documents why it
   * cannot: every KiroCrew path to kiro-cli mints a `HUMAN_TURN`, so unattended continuation would
   * forge human presence in AI-DLC's audit trail. Settings renders `unavailable.machineLane` instead.
   */
  keepMoving(repoId: string, intentKey: string, enabled: boolean): Promise<never>
  bindSession(repoId: string, intentKey: string, slotKey: string): Promise<BindResponse>
  unbindSession(repoId: string, intentKey: string): Promise<BindingResponse>
  takeoverPreview(repoId: string, intentKey: string): Promise<TakeoverPreviewResponse>
  takeover(repoId: string, intentKey: string, slotKey: string): Promise<BindingResponse>
  archiveIntent(repoId: string, intentKey: string): Promise<BindingResponse>
  restoreIntent(repoId: string, intentKey: string): Promise<BindingResponse>

  // -- host chat (§2.13; the second declared prefix) --------------------- //
  listSlots(o?: ReadOptions): Promise<SlotView[]>
  getSlot(slotKey: string, o?: ReadOptions): Promise<Record<string, unknown>>
  createSlot(slotKey: string, agent: string): Promise<Record<string, unknown>>
  setSlotTitle(slotKey: string, title: string): Promise<Record<string, unknown>>
  setSlotProject(slotKey: string, project: string): Promise<Record<string, unknown>>
  setSlotAgent(slotKey: string, agent: string): Promise<Record<string, unknown>>
  /** The exact `SubmitReceipt.host` call. Body is passed through byte-for-byte — never rebuilt. */
  sendToHost(path: string, body: Record<string, unknown>): Promise<HostChatReceipt>
}

export type DeepPartial<T> = { [K in keyof T]?: T[K] extends object ? DeepPartial<T[K]> : T[K] }

/**
 * The client, memoised per `useAppApi()` identity so a resource hook can depend on it.
 *
 * Every method rethrows a decoded `StudioApiError`; nothing here retries. Retry policy belongs to the
 * caller because the right answer differs per route: a read may retry freely, `POST …/delivery` must
 * retry (the backend is idempotent per `delivery_id`), and `POST …/submit` must never.
 */
export function useStudioApi(): StudioApi {
  const api = useAppApi()

  return useMemo<StudioApi>(() => {
    const get = async <T>(path: string, o?: ReadOptions): Promise<T> => {
      try {
        return await api.get<T>(`${API_BASE}${path}`, o?.signal ? { signal: o.signal } : undefined)
      } catch (error) {
        if (isAbort(error)) throw error
        throw decodeError(error)
      }
    }
    const send = async <T>(
      method: 'post' | 'put' | 'del',
      path: string,
      body?: unknown,
    ): Promise<T> => {
      try {
        if (method === 'del') return await api.del<T>(`${API_BASE}${path}`)
        return await api[method]<T>(`${API_BASE}${path}`, body)
      } catch (error) {
        throw decodeError(error)
      }
    }
    const post = <T>(path: string, body?: unknown) => send<T>('post', path, body)
    const intentPath = (repoId: string, intentKey: string, tail = '') =>
      `/repos/${encodeURIComponent(repoId)}/intents/${encodeURIComponent(intentKey)}${tail}`
    const repoPath = (repoId: string, tail = '') => `/repos/${encodeURIComponent(repoId)}${tail}`

    return {
      health: (o) => get('/health', o),
      configureBun: (path) => send('put', '/tools/bun', { path }),
      probeBun: () => post('/tools/bun/probe', {}),
      payload: (includeFiles, o) => get(`/payload${query({ files: includeFiles })}`, o),
      leases: (o) => get('/leases', o),
      diagnostics: (opts) =>
        get(
          `/diagnostics${query({ export: opts?.export, include_human_text: opts?.includeHumanText })}`,
          opts,
        ),
      settings: (o) => get('/settings', o),
      putSettings: (patch) => send('put', '/settings', patch),
      calibration: (o) => get('/calibration', o),
      clearCalibration: () => post('/calibration/clear', { confirm: true }),
      migrationStatus: (o) => get('/migration/status', o),
      migrationPreview: () => post('/migration/preview', {}),
      migrationApply: (sourceSha256) =>
        post('/migration/apply', { confirm: true, source_sha256: sourceSha256 }),

      pollEvents: (cursor, limit, o) =>
        get(`/events/poll${query({ cursor, limit })}`, o),
      activity: (filter, o) => get(`/activity${query({ ...filter })}`, o),

      actions: (filter, o) => get(`/actions${query({ ...filter })}`, o),
      action: (actionId, o) => get(`/actions/${encodeURIComponent(actionId)}`, o),
      submitAction: (actionId, body) => post(`/actions/${encodeURIComponent(actionId)}/submit`, body),
      reportDelivery: (actionId, body) =>
        post(`/actions/${encodeURIComponent(actionId)}/delivery`, body),
      retryAction: (actionId) => post(`/actions/${encodeURIComponent(actionId)}/retry`, {}),
      reconcileAction: (actionId) => post(`/actions/${encodeURIComponent(actionId)}/reconcile`, {}),
      cancelAction: (actionId, reason) =>
        post(`/actions/${encodeURIComponent(actionId)}/cancel`, { reason: reason ?? null }),
      resolveAction: (actionId, payload) => {
        const { decision, ...rest } = payload as { decision: string } & Record<string, unknown>
        return post(`/actions/${encodeURIComponent(actionId)}/resolve`, { decision, payload: rest })
      },

      requestDraft: (body) => post('/advisor/draft', body),
      draft: (draftId, o) => get(`/advisor/drafts/${encodeURIComponent(draftId)}`, o),
      slackCallback: (body) => post('/slack/actions/callback', body),

      repos: (includeArchived, o) => get(`/repos${query({ include_archived: includeArchived })}`, o),
      repo: (repoId, o) => get(repoPath(repoId), o),
      preflight: (path) => post('/repos/preflight', { path }),
      addRepo: (path, label) => post('/repos', { path, label: label ?? null }),
      removeRepo: (repoId) => send('del', repoPath(repoId)),
      updateRepoMetadata: (repoId, patch) => send('put', repoPath(repoId, '/metadata'), patch),
      directories: (path) => post('/directories', { path: path ?? null }),
      maintenancePreview: (repoId, entryIds = []) => post(repoPath(repoId, '/maintenance/preview'), { entry_ids: entryIds }),
      cleanupMaintenance: (repoId, entryIds, planDigest) =>
        post(repoPath(repoId, '/maintenance/cleanup'), { entry_ids: entryIds, plan_digest: planDigest }),
      rebindRepo: (repoId, path) => post(repoPath(repoId, '/rebind'), { path }),
      rescanRepo: (repoId) => post(repoPath(repoId, '/rescan'), {}),
      doctorRepo: (repoId) => post(repoPath(repoId, '/doctor'), { confirm: true }),
      installPreview: (repoId, kind = 'install') =>
        post(repoPath(repoId, `${kind === 'recovery' ? '/install/recovery' : `/${kind}`}/preview`), {}),
      installApply: (repoId, planDigest, kind = 'install') =>
        post(repoPath(repoId, kind === 'recovery' ? '/install/recovery' : `/${kind}`), { plan_digest: planDigest }),
      transaction: (repoId, transactionId, o) =>
        get(repoPath(repoId, `/transactions/${encodeURIComponent(transactionId)}`), o),
      cancelTransaction: (repoId, transactionId) =>
        post(repoPath(repoId, `/transactions/${encodeURIComponent(transactionId)}/cancel`), {}),
      receipts: (repoId, o) => get(repoPath(repoId, '/receipts'), o),
      repoGit: (repoId, o) => get(repoPath(repoId, '/git'), o),
      spaces: (repoId, o) => get(repoPath(repoId, '/spaces'), o),
      createSpace: (repoId, name) => post(repoPath(repoId, '/spaces'), { name }),
      switchSpace: (repoId, name) => post(repoPath(repoId, '/spaces/switch'), { name }),
      intentSettingsPreview: (repoId, intentKey, body) =>
        post(intentPath(repoId, intentKey, '/settings/preview'), body),
      changeIntentSettings: (repoId, intentKey, body) =>
        post(intentPath(repoId, intentKey, '/settings'), body),

      intents: (repoId, filter, o) => get(repoPath(repoId, `/intents${query({ ...filter })}`), o),
      intent: (repoId, intentKey, o) => get(intentPath(repoId, intentKey), o),
      map: (repoId, intentKey, density, o) =>
        get(intentPath(repoId, intentKey, `/map${query({ density })}`), o),
      artifacts: (repoId, intentKey, filter, o) =>
        get(intentPath(repoId, intentKey, `/artifacts${query({ ...filter })}`), o),
      artifact: (repoId, intentKey, artifactId, o) =>
        get(intentPath(repoId, intentKey, `/artifacts/${encodeURIComponent(artifactId)}`), o),
      questions: (repoId, intentKey, o) => get(intentPath(repoId, intentKey, '/questions'), o),
      review: (repoId, intentKey, o) => get(intentPath(repoId, intentKey, '/review'), o),
      intentGit: (repoId, intentKey, o) => get(intentPath(repoId, intentKey, '/git'), o),
      intentActivity: (repoId, intentKey, limit, o) =>
        get(intentPath(repoId, intentKey, `/activity${query({ limit })}`), o),
      planPreview: (repoId, body) => post(repoPath(repoId, '/intents/plan/preview'), body),
      requestPlanDraft: (repoId, body) => post(repoPath(repoId, '/intents/plan/advise'), body),
      createIntent: (repoId, body) => post(repoPath(repoId, '/intents'), body),
      compileRuntime: (repoId, intentKey) => post(intentPath(repoId, intentKey, '/runtime/compile'), {}),
      recomposePreview: (repoId, intentKey, body) =>
        post(intentPath(repoId, intentKey, '/recompose/preview'), body),
      recompose: (repoId, intentKey, body) => post(intentPath(repoId, intentKey, '/recompose'), body),
      run: (repoId, intentKey) => post(intentPath(repoId, intentKey, '/run'), {}),
      resume: (repoId, intentKey) => post(intentPath(repoId, intentKey, '/resume'), {}),
      prepareCommit: (repoId, intentKey) => post(intentPath(repoId, intentKey, '/prepare-commit'), {}),
      pause: (repoId, intentKey, paused) => post(intentPath(repoId, intentKey, '/pause'), { paused }),
      forceStop: (repoId, intentKey) =>
        post(intentPath(repoId, intentKey, '/force-stop'), { confirm: true }),
      keepMoving: (repoId, intentKey, enabled) =>
        post(intentPath(repoId, intentKey, '/keep-moving'), { enabled }),
      bindSession: (repoId, intentKey, slotKey) =>
        post(intentPath(repoId, intentKey, '/session/bind'), { slot_key: slotKey }),
      unbindSession: (repoId, intentKey) => post(intentPath(repoId, intentKey, '/session/unbind'), {}),
      takeoverPreview: (repoId, intentKey) =>
        post(intentPath(repoId, intentKey, '/session/takeover/preview'), {}),
      takeover: (repoId, intentKey, slotKey) =>
        post(intentPath(repoId, intentKey, '/session/takeover'), { slot_key: slotKey }),
      archiveIntent: (repoId, intentKey) => post(intentPath(repoId, intentKey, '/archive'), {}),
      restoreIntent: (repoId, intentKey) => post(intentPath(repoId, intentKey, '/restore'), {}),

      listSlots: async (o) => {
        try {
          return await api.get<SlotView[]>(`${CHAT_BASE}/slots`, o?.signal ? { signal: o.signal } : undefined)
        } catch (error) {
          if (isAbort(error)) throw error
          throw decodeError(error)
        }
      },
      getSlot: async (slotKey, o) => {
        try {
          return await api.get<Record<string, unknown>>(
            `${CHAT_BASE}/slots/${encodeURIComponent(slotKey)}`,
            o?.signal ? { signal: o.signal } : undefined,
          )
        } catch (error) {
          if (isAbort(error)) throw error
          throw decodeError(error)
        }
      },
      createSlot: (slotKey, agent) => chatPost(api, `${CHAT_BASE}/slots`, { name: slotKey, agent }),
      // `PATCH`, not `POST`: the host's title route is a patch (`kc:routes/sessions.py:32`).
      setSlotTitle: async (slotKey, title) => {
        try {
          return await api.patch<Record<string, unknown>>(
            `${CHAT_BASE}/slots/${encodeURIComponent(slotKey)}/title`,
            { title },
          )
        } catch (error) {
          throw decodeError(error)
        }
      },
      setSlotProject: (slotKey, project) =>
        chatPost(api, `${CHAT_BASE}/slots/${encodeURIComponent(slotKey)}/project`, { project }),
      setSlotAgent: (slotKey, agent) =>
        chatPost(api, `${CHAT_BASE}/slots/${encodeURIComponent(slotKey)}/agent`, { agent }),
      sendToHost: (path, body) => chatPost<HostChatReceipt>(api, path, body),
    }
  }, [api])
}

type SdkApi = ReturnType<typeof useAppApi>

/**
 * A host-chat POST.
 *
 * Kept separate from `send` because a 4xx here is *evidence*, not an error to swallow: the two-phase
 * submit has to report `not_delivered` with the host's own body, so the decoded error carries it in
 * `details` and the caller decides.
 */
async function chatPost<T = Record<string, unknown>>(
  api: SdkApi,
  path: string,
  body: Record<string, unknown>,
): Promise<T> {
  try {
    return await api.post<T>(path, body)
  } catch (error) {
    throw decodeError(error)
  }
}
