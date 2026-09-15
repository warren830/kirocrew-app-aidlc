/**
 * The wire contract, as TypeScript.
 *
 * This file is contracts.md §3.1 reconciled against the code that actually serialises each object
 * (`backend/studio/handlers/*.py`, `projection.py`, `actions.py`, `installer.py`, `sessions.py`,
 * `plan.py`, `estimates.py`, `repo_registry.py`). Where §3.1 and the backend disagreed the BACKEND
 * won, because it is what arrives in the browser; every one of those places is marked `DRIFT:` with
 * what the document said and what the server sends.
 *
 * Two structural rules:
 *   - Every enum union is imported from `enums.generated.ts` rather than restated. §3.1 spells the
 *     unions out inline, but a second hand-written copy of `ActionStatus` is a copy that eventually
 *     lags the backend by one value, and the value it lags by is the one a card is stuck in.
 *   - Nothing here is optional unless the server can genuinely omit the key. §0.4 of the contract
 *     makes the backend emit every key with `null` for "unknown", so `x: T | null` is the honest
 *     shape and `x?: T` would invite `undefined` checks that never fire.
 */

import type {
  ActionStatus,
  ActionType,
  AdvisorKind,
  Availability,
  Decision,
  FindingSeverity,
  InstallStatus,
  IntentState,
  LeaseKind,
  Ownership,
  Phase,
  RiskClass,
  Severity,
  Source,
  StageState,
} from './enums.generated'

export type {
  ActionStatus,
  ActionType,
  AdvisorKind,
  Availability,
  Decision,
  FindingSeverity,
  InstallStatus,
  IntentState,
  LeaseKind,
  Ownership,
  Phase,
  RiskClass,
  Severity,
  Source,
  StageState,
}

/** Queue organisation modes (`ORGANIZE_MODES` in `projection.py`). Not a backend enum table. */
export type Organize = 'priority' | 'repo' | 'type' | 'oldest'
/** Which authority a decision travels through (`lane_for` in `actions.py`). */
export type Lane = 'human_lane' | 'studio_only' | 'host_control'

/**
 * A backend-supplied translation reference.
 *
 * DRIFT: §3.1 types `params` as `Record<string, string | number | null>`, but `CardSeed`
 * headline params legitimately carry arrays (`recovery` sends `codes: [...]`) and booleans
 * (`failure` sends `burst: true`). Use `refParams()` from `format.ts` to turn one into `t()` params.
 */
export interface I18nRef { key: string; params: Record<string, unknown> }

/** The one non-2xx body shape (§0.4, `StudioError.to_json`). `error` is the message, not a code. */
export interface ApiError { error: string; code: string; details: Record<string, unknown> }

// ---- shared ----
export interface Finding { code: string; severity: FindingSeverity; message_key: string; params: Record<string, unknown>; evidence: EvidenceRef[] }
export interface EvidenceRef { kind: 'file' | 'audit' | 'studio' | 'host' | 'git'; ref: string; detail: string | null }
export interface ReviewFinding { level: 'blocker' | 'advisory' | 'resolved' | 'unknown'; title: string; quote: string | null; anchor: string | null; reviewer: string | null; iteration: number | null }
export interface ArtifactMeta { artifact_id: string; relpath: string; name: string; stage: string | null; phase: string | null; unit: string | null; size: number; mtime: string; sha256: string | null; kind: 'artifact' | 'questions' | 'memory' | 'review' | 'traceability' | 'contribution' | 'other'; renderable: boolean }
export interface GitObservation { available: boolean; reason: string | null; branch: string | null; detached: boolean; head: string | null; head_subject: string | null; dirty: boolean; dirty_files: number; ahead: number | null; behind: number | null; upstream: string | null; observed_at: string; took_ms: number }
/** DRIFT: §3.1 has no `GitCommit`, but `GET …/{intent}/git` returns a list of them. */
export interface GitCommit { sha: string; at: string; subject: string }
export interface LeaseView { kind: LeaseKind; repo_id: string; resolved_repo_identity: string; generation: number; acquired_at: string; heartbeat_at: string; intent_uuid: string | null; intent_key: string | null; session_key: string | null; action_id: string | null; operation_type: string | null; transaction_id: string | null; observed_busy_state: string | null; orphaned: boolean }
export interface StableBoundary { stable: boolean; reasons: string[]; stage: string | null; marker: string | null; boundary_token: string | null; recorded_at: string }
export interface SessionRef { slot_key: string; session_key: string; running: boolean; bound_at: string }
/**
 * DRIFT: §3.1 types `stop_state` as `'idle' | 'soft_pending' | 'killing'`. `SlotView.of` deliberately
 * passes an unrecognised host value through ("anything that is not idle refuses a dispatch"), so a
 * closed union here would be wrong the first time the host grows a fourth state. Compare with
 * `SLOT_IDLE` instead of switching on the union.
 */
export interface SlotView { key: string; running: boolean; project: string; agent: string; app: string; queue_depth: number; pending_approval: boolean; needs_input: boolean; waiting_for_input: boolean; stop_state: string; interrupted: boolean; last_ts: string; last_turn_ts: string; linked_session_key: string; session_key: string; messages: number; has_options: boolean; options: string[]; slack_linked: boolean; title: string; stopping: boolean; wait_state: Record<string, unknown> | null; in_stage_execution: boolean; approvals_pending: number; subagents_running: number; subagents_queued: number; deliveries_inflight: number }
export const SLOT_IDLE = 'idle'
export type BusyReason = 'running' | 'stage_execution' | 'stopping' | 'queued' | 'approval_pending' | 'subagents' | 'deliveries_inflight'
export interface BindingView { repo_id: string; space: string; intent_dir: string; intent_key: string; intent_uuid: string | null; slot_key: string | null; session_key: string | null; binding_generation: number; archive_state: 'active' | 'archived'; paused: boolean; paused_at: string | null; interrupted_at: string | null; keep_moving: boolean; last_stable_boundary: StableBoundary | null; updated_at: string }

// ---- repos ----
export interface HarnessDir { dir: string; harness_name: string | null; rules_subdir: string | null; engine_version: string | null; engine_state_version: number | null; stage_count: number | null; has_utility: boolean }
export interface ReceiptSummary { receipt_id: string; engine_version: string; studio_version: string; committed_at: string; status: 'current' | 'superseded' | 'rolled_back' | 'uninstalled'; files: number }
/**
 * `engine_dir`/`engine_version` name the harness Studio READS — `.kiro` when it is there, else whatever
 * harness the repository does have. `own_engine_version` is the engine version of Studio's OWN harness
 * (`.kiro`), or null when that directory is absent, and it is the only field the install lane may key
 * on: a repository whose AI-DLC lives under `.claude` reports `status: 'installed'` and is perfectly
 * drivable, yet Studio has nothing of its own there to upgrade and must offer `install` instead.
 */
export interface RepoInstall { status: InstallStatus; engine_dir: string | null; engine_version: string | null; own_engine_version: string | null; engine_state_version: number | null; stage_count: number | null; harness_dirs: HarnessDir[]; receipt: ReceiptSummary | null; bundled_engine_version: string; upgrade_available: boolean; newer_installed: boolean; state_version_blocked: boolean; drift_count: number; rollback_target?: { receipt_id: string; engine_version: string } | null }
/**
 * DRIFT: `RepoScan.install` is `InstallHealth.to_json()` — the registry's own health block, NOT the
 * composed `RepoInstall` the handler builds for `RepoRecord`. They share five keys and differ in five.
 */
export interface InstallHealth { status: InstallStatus; engine_dir: string | null; engine_version: string | null; own_engine_version: string | null; engine_state_version: number | null; stage_count: number | null; receipt_id: string | null; receipt_engine_version: string | null; drift_count: number; harness_dirs: HarnessDir[] }
export interface RepoRecord {
  repo_id: string; label: string; canonical_path: string; resolved_identity: string | null; git_common_dir_identity: string | null
  /** DRIFT: §3.1 unions three values; the backend stores `sys.platform` verbatim, so it is a string. */
  platform: string
  added_at: string; last_seen: string | null; availability: Availability; availability_detail: string | null; archived: boolean; legacy_console_id: string | null
  // DRIFT: four registry columns §3.1 omits. `RepoRecord.to_json()` emits them, and `engine_dir` is
  // the one the Repos page needs to name the harness a repo is actually driven by.
  install_status: InstallStatus; installed_engine_version: string | null; engine_dir: string | null; receipt_version: string | null
  install: RepoInstall
  counts: { intents: number; in_flight: number; open_actions: number; blocking_findings: number }
  leases: { execution: LeaseView | null; admin: LeaseView | null }
  git: GitObservation | null; findings: Finding[]; scanned_at: string | null
}
export interface PreflightReport { path_input: string; canonical_path: string | null; identity: { canonical_path: string; st_dev: number | null; st_ino: number | null; git_common_dir: string | null; identity_str: string; provable: boolean } | null; is_directory: boolean; sensitive: boolean; duplicate_of: string | null; platform: string; git: { is_repo: boolean; branch: string | null; dirty: boolean; common_dir: string | null } | null; bun: { found: boolean; path: string | null; version: string | null; source: string | null; searched: string[] } | null; harness_dirs: HarnessDir[]; aidlc: { layout: 'spaces' | 'legacy' | null; spaces: string[]; intents: number; state_versions: number[] }; symlinks_at_managed_paths: string[]; free_space_bytes: number | null; writable: boolean | null; existing_receipt: ReceiptSummary | null; warnings: Finding[]; can_register: boolean; can_install: boolean }
export type TransactionKind = 'install' | 'upgrade' | 'recovery' | 'uninstall' | 'rollback'
export type PreviewAction = 'create' | 'identical' | 'conflict' | 'owned_identical' | 'owned_modified' | 'engine_modified' | 'retire' | 'retire_blocked' | 'merge_create' | 'merge_identical' | 'merge_update' | 'merge_conflict' | 'shell_create' | 'shell_exists' | 'remove' | 'remove_fragment' | 'preserve' | 'already_absent' | 'uninstall_conflict' | 'restore_version' | 'rollback_conflict' | 'restore_backup' | 'remove_created' | 'recovery_conflict'
export interface PreviewEntry { path: string; ownership: Ownership; action: PreviewAction; live_sha256: string | null; payload_sha256: string | null; receipt_sha256: string | null; size: number | null; fragment_key: string | null; diff: string | null; blocking: boolean; reason?: string | null }
export interface PreviewPlan {
  repo_id: string; kind: TransactionKind; engine_from: string | null; engine_to: string; studio_version: string; payload_digest: string
  entries: PreviewEntry[]; counts: Record<PreviewAction, number>; blocking: boolean; blockers: PreviewEntry[]; warnings: Finding[]
  newer_installed: boolean; same_version: boolean; requires_admin_lease: boolean; bytes_to_write: number; state_versions_found: number[]
  /** DRIFT: emitted by `PreviewPlan.to_json()`, absent from §3.1. A state file Studio could not read. */
  state_versions_unreadable: number
  state_version_blocked: boolean; preflight: PreflightReport
  recovery_transaction_id?: string; recovery_kind?: 'uninstall' | 'rollback'; recovery_evidence_digest?: string
}
export type TransactionStatus = 'staged' | 'leased' | 'backed_up' | 'written' | 'merged' | 'validated' | 'committed' | 'rolling_back' | 'rolled_back' | 'recovery_required' | 'failed'
export interface StepRecord { name: string; started_at: string; finished_at: string | null; ok: boolean | null; detail: Record<string, unknown> }
export interface TransactionResult { transaction_id: string; repo_id: string; kind: TransactionKind; status: TransactionStatus; steps: StepRecord[]; receipt_id: string | null; prior_receipt_id: string | null; error: string | null; failed_dir: string | null; repo_install_status: InstallStatus; started_at: string; finished_at: string | null; engine_version: string }
export interface ReceiptFile { path: string; ownership: Ownership; kind: 'file' | 'fragment'; sha256: string | null; canonical_digest: string | null; fragment_key: string | null; adopted: boolean }
/**
 * DRIFT: §3.1 has `Receipt extends ReceiptSummary` with `files: number` plus an optional
 * `files_list`. `installer.Receipt.to_json()` has no file *count*, puts the file *list* in `files`
 * (or `null` when `include_files=False`), and adds `resolved_repo_identity`. `GET
 * /repos/{id}/receipts` sends the list only for the current receipt, so `files === null` on a
 * superseded one means "not sent", never "no files".
 */
export interface Receipt { receipt_id: string; repo_id: string; resolved_repo_identity: string | null; studio_version: string; engine_version: string; payload_digest: string; committed_at: string; prior_receipt_id: string | null; transaction_id: string; status: 'current' | 'superseded' | 'rolled_back' | 'uninstalled'; files: ReceiptFile[] | null }
export interface RepoScan { repo_id: string; availability: Availability; install: InstallHealth; intents: IntentSummary[]; findings: Finding[]; took_ms: number; truncated: boolean; scanned_at: string }

// ---- intents ----
export interface DiskState { status: string | null; lifecycle_phase: string | null; current_stage: string | null; next_stage: string | null; state_version: number | null; total_stages: number | null; completed: number | null; revision_count: number; parked_at: string | null; last_updated: string | null }
export interface StageCounts { total: number; not_started: number; in_progress: number; awaiting_approval: number; revising: number; completed: number; skipped: number; done: number; unknown: number }
export interface IntentSummary { repo_id: string; repo_label: string; space: string; intent_dir: string; intent_key: string; uuid: string | null; slug: string; scope: string | null; registry_status: string | null; title: string | null; operational_state: IntentState; disk: DiskState; counts: StageCounts; open_actions: number; blocking_findings: number; warn_findings: number; session: SessionRef | null; archived: boolean; paused: boolean; keep_moving: false; interrupted: boolean; last_activity_at: string | null; stable_boundary: StableBoundary | null; unstable: boolean; is_active_cursor: boolean }
export interface StageRow { slug: string; mark: string; state: StageState; phase: string | null; suffix: string | null }
export interface AuditEventView { shard: string; pos: number; timestamp: string; event: string; fields: Record<string, string>; raw: string }
/**
 * `version` is the marker's own schema number, not a Studio constant: 2.6.2 wrote `1` and 2.7.1 writes
 * `2` at the same path while leaving an already-present v1 marker alone, so a field install serves
 * either. `kind` is `null` on v1 (which has no such field) and one of `DIRECTIVE_KINDS` on v2. `units`
 * is the multi-unit form of `unit` — the `invoke-swarm` shape fans out over several units and leaves
 * `unit` null — so a reader that wants "which unit is executing" must consult both.
 */
export interface Directive { version: number; kind: string | null; stage: string; unit: string | null; units: string[]; state_sha256: string; matches_state: boolean }
/** Persisted action snapshots from older Studio builds contain only these three fields. */
export type DirectiveEvidence = Pick<Directive, 'stage' | 'unit' | 'matches_state'>
  & Partial<Pick<Directive, 'version' | 'kind' | 'units' | 'state_sha256'>>
export interface MarkersView { human_turn_at: string | null; engine_touch_at: string | null; turn_counter: number | null; goal_stop_present: boolean; recovery: Record<string, string> | null; hooks_health: Record<string, string>; compose_pending: boolean }
export interface QuestionOption { letter: string; text: string; is_other: boolean }
export interface Question { index: number; prompt: string; context?: string; options: QuestionOption[]; multi_select: boolean; answer: string | null; answered: boolean; required: true }
export interface Checkpoint { kind: 'summary_confirmation' | 'plan_approval'; present: boolean; answered: boolean; answer: string | null; options: string[] }
export interface HostQuestionCard { ask_id?: string; card_id?: string; slot: string; questions: { question: string; header?: string; options: { label: string; description?: string }[]; multiSelect?: boolean }[]; ts: number }
export interface AuditQuestionOrigin { kind: 'audit'; event: string; shard: string; pos: number; timestamp: string; stage: string; unit: string | null; workflow: null; attempt_generation: string | null; decision_sha256: string }
export interface QuestionsView { relpath: string; sha256: string; stage: string; unit: string | null; questions: Question[]; summary_confirmation: Checkpoint | null; plan_approval: Checkpoint | null; pending_count: number; unsupported_pending_count?: number; pending_checkpoint: 'summary_confirmation' | 'plan_approval' | null; mode: 'degraded' | 'structured'; host_card: HostQuestionCard | null; origin?: AuditQuestionOrigin }
export interface IntentDetail extends IntentSummary { state_sections: Record<string, Record<string, string>>; phases: [string, string][]; stages: StageRow[]; findings: Finding[]; actions: ActionCard[]; directive: Directive | null; recovery: Record<string, string> | null; markers: MarkersView; audit_tail: AuditEventView[]; questions: QuestionsView | null; artifacts_count: number; artifacts_truncated: boolean; git: GitObservation | null; binding: BindingView | null; engine: HarnessDir | null; active_directive_stage: string | null }

// ---- map ----
export interface MapUnit { unit: string; state: StageState; artifacts: ArtifactMeta[] }
/** `phase` is a `Phase` for every stage the graph knows and the literal `"unknown"` for a state-file
 *  row it does not, which is what `stage_graph_drift` reports; hence `string`, not `Phase`. */
export interface MapStage { slug: string; number: string; name: string; phase: string; state: StageState | 'excluded'; execution: 'ALWAYS' | 'CONDITIONAL'; in_scope: boolean; mode: string; agent: string; reviewer: string | null; review_class: string | null; gate: boolean; per_unit: boolean; summary_confirmation: string | null; consumes: string[]; produces: string[]; depends_on: string[]; dependents: string[]; elapsed_secs: number | null; artifacts: ArtifactMeta[]; skipped_reason: string | null; units: MapUnit[]; is_current: boolean; is_directive: boolean }
export interface MapPhase { phase: string; status: string; stages: MapStage[]; counts: { total: number; in_scope: number; done: number; skipped: number } }
export interface MapModel { intent_key: string; phases: MapPhase[]; counts: { stages_known: number; stages_selected: number; gates: number }; units: string[]; graph_version: string | null; stage_count: number }

// ---- actions ----
export interface Captured { state_hash: string | null; boundary_token: string | null; question_digest: string | null; stage_attempt: number | null; evidence_digest: string | null; is_active: boolean | null; captured_at: string; stable: boolean }
export interface PresenceBaseline { human_turn_events: number; human_turn_events_partial: boolean; human_turn_mtime_ns: number | null; turn_counter: number | null; shard_sizes: Record<string, number>; audit_tail_sha256: string; state_sha256: string | null; taken_at: string }
export interface PresenceCheck { human_turn_delta: number | null; marker_advanced: boolean | null; ok: boolean | null }
export interface CursorReadback { ok: boolean; space: string; dir_name: string; uuid: string | null; state_sha256: string | null; mismatch: string[] }
export interface HostCall { method: 'POST'; path: string; body: Record<string, unknown> }
export interface DecisionSpec { decision: Decision; label_key: string; lane: Lane; wire_text_template: string | null; requires: ('feedback' | 'answers' | 'confirm')[] }
export interface ActionEvidence {
  state: { relpath: string; sha256: string; size: number; mtime_ns: number; current_stage: string | null; status: string | null; lifecycle_phase: string | null; revision_count: number; stable: boolean } | null
  audit: { shards: { relpath: string; size: number; truncated: boolean }[]; last_event: { type: string; ts: string } | null; boundary_event: { type: string; ts: string; stage: string | null; shard: string; pos: number } | null; complete: boolean }
  directive: DirectiveEvidence | null; artifacts: ArtifactMeta[]
  review: { verdict: string | null; findings: ReviewFinding[]; reviewer: string | null; review_class: string | null; iteration: number | null } | null
  acceptance_criteria: { text: string; met: boolean | null; why_key: string | null }[]
  findings: Finding[]
  session: { slot_key: string; session_key: string; running: boolean; stop_state: string; last_turn_ts: string | null; queue_depth: number; busy_reasons: BusyReason[] } | null
  questions: QuestionsView | null; git: GitObservation | null; markers: Pick<MarkersView, 'human_turn_at' | 'engine_touch_at' | 'turn_counter' | 'goal_stop_present' | 'recovery'>
  presence_baseline: PresenceBaseline | null; presence: PresenceCheck | null; cursor_readback: CursorReadback | null
}
export interface Delivery { lane: Lane | null; delivery_id: string | null; slot_key: string | null; session_key: string | null; wire_text: string | null; host: HostCall | null; delivering_at: string | null; delivered_at: string | null; queued_at: string | null; deadline_at: string | null; receipt: Record<string, unknown> | null; outcome: 'delivered' | 'not_delivered' | 'uncertain' | null; delivery_confirmed: boolean; boot_id_unchanged: boolean | null; transcript_row: { slot_key: string; ts: string; role: string } | null; slot_ran_since: boolean | null; disk_baseline_unchanged: boolean | null }
export interface Resolution { kind: 'state_changed' | 'no_transition' | 'failed' | 'cancelled' | null; resolved_at: string | null; evidence: { events?: unknown[]; rows?: unknown[]; presence: PresenceCheck | null; is_active: boolean | null } | null; reason: string | null }
/** DRIFT: `_failure_json` defaults `summary_key` and `fingerprint_class` to `null`. */
export interface FailureInfo { fingerprint: string; fingerprint_class: string | null; summary_key: string | null; count: number; breaker_open: boolean; history: { at: string; outcome: string; backoff_secs: number }[]; stderr_excerpt: string | null }
export interface InstallInfo { receipt_id: string | null; engine_version: string | null; bundled_engine_version: string; drift: PreviewEntry[]; transaction_id: string | null }
export interface ActionCard {
  action_id: string; type: ActionType; queue_type: ActionType; status: ActionStatus; status_generation: number; source: Source; risk_class: RiskClass
  severity: Severity; priority_group: 1 | 2 | 3 | 4
  repo: { repo_id: string; label: string; canonical_path: string; availability?: Availability; availability_detail?: string | null; archived?: boolean }; space: string
  intent: { intent_dir: string; intent_key: string; slug: string; uuid: string | null; title: string | null }
  stage: { slug: string; number: string | null; name: string | null; phase: string | null; unit: string | null } | null
  headline: I18nRef; consequence: I18nRef | null
  waiting_since: string; created_at: string; updated_at: string
  primary: { decision: Decision; label_key: string } | null; decisions: DecisionSpec[]
  captured: Captured; evidence: ActionEvidence; delivery: Delivery; resolution: Resolution
  failure: FailureInfo | null; install: InstallInfo | null; budget: { turns_used: number; turn_cap: number; window: string; credits_status: 'unavailable' } | null
  advisor: { latest_draft_id: string | null; status: string | null }
  deep_link: string; dedupe_key: string | null; human_text_present: boolean; acknowledged_evidence_sha256: string | null
}
export interface ActionGroup { key: string; label_key: string; action_ids: string[] }
export interface ActionsResponse { actions: ActionCard[]; organize: Organize; groups: ActionGroup[]; counts: { total: number; critical: number; blocking: number; attention: number; info: number }; generated_at: string }
export interface ActionTransition { from_status: ActionStatus | null; to_status: ActionStatus; generation: number; at: string; reason: string | null; evidence: Record<string, unknown> }
export interface ActionDetailResponse { action: ActionCard; transitions: ActionTransition[]; drafts: AdvisorDraft[] }
export interface AnswerInput { index: number; option_letters: string[]; free_text: string | null }
export type SubmitPayload =
  | { decision: 'approve' } | { decision: 'accept_as_is' } | { decision: 'request_changes'; feedback: string }
  | { decision: 'answers'; answers: AnswerInput[] }
  | { decision: 'confirm_summary'; choice: 'looks_correct' } | { decision: 'confirm_summary'; choice: 'request_changes'; feedback: string }
  | { decision: 'approve_plan' } | { decision: 'request_plan_changes'; feedback: string }
  | { decision: 'provide_input'; kind: 'scope'; scope: string } | { decision: 'provide_input'; kind: 'free_text'; text: string }
  | { decision: 'run' } | { decision: 'resume' } | { decision: 'prepare_commit' }
export type ResolvePayload =
  | { decision: 'resubmit'; acknowledged_evidence_sha256: string } | { decision: 'pick_intent'; intent_dir: string }
  | { decision: 'mark_not_delivered'; evidence?: { receipt?: HostChatReceipt; http_status?: number } }
  | { decision: 'rebind_session' | 'acknowledge' | 'reconcile' | 'retry_now' | 'keep_paused' | 'run_now' }
export interface SubmitRequest { captured: Omit<Captured, 'captured_at' | 'stable'>; payload: SubmitPayload; client_wire_text: string }
export interface SubmitReceipt { ok: true; action_id: string; status: 'Delivering'; lane: 'human_lane' | 'host_control'; delivery_id: string; slot_key: string; session_key: string; wire_text: string | null; expires_at: string; lease_generation: number | null; host: HostCall }
export interface HostChatReceipt { ok: boolean; slot?: string; mid?: string; queued?: boolean; steered?: boolean; info?: string; error?: string; code?: string }
export interface DeliveryReport { delivery_id: string; outcome: 'delivered' | 'not_delivered' | 'uncertain'; http_status: number | null; receipt: HostChatReceipt | null }
export interface DeliveryResponse { ok: true; action: ActionCard; idempotent: boolean }
export interface ActionResponse { ok: true; action: ActionCard }
export interface ResolveResponse { ok: true; action: ActionCard; created_action_id: string | null }

// ---- plan / estimates ----
export type PlanDepth = 'Minimal' | 'Standard' | 'Comprehensive'
export interface PlanRequest { space: string; scope: string; depth: PlanDepth; test_strategy: PlanDepth | null; review_cap: 'none' | 'advisory' | 'adversarial' | null; project_type: 'Greenfield' | 'Brownfield' | null; overrides: Record<string, boolean>; objective: string | null; label: string | null; context: string | null }
export interface PlanStage { slug: string; number: string; name: string; phase: string; execution: 'ALWAYS' | 'CONDITIONAL'; in_grid: boolean; enabled: boolean; locked: boolean; lock_reason: string | null; gate: boolean; review_class: string | null; reviewer: string | null; per_unit: boolean; produces: string[]; consumes: string[]; depends_on: string[]; conditional_on: string | null; state: StageState | null }
export interface Range { low: number; high: number; unit: 'turns' | 'secs'; source: 'rule_band' | 'history_calibrated' | 'assumption'; confidence: 'low' | 'medium' }
export interface ExactCounts { stages: number; gates: number; artifacts: number; review_intensity: Record<'none' | 'advisory' | 'adversarial', number>; assumes_units: number | null }
export interface Estimate { turns: Range; active_secs: Range; elapsed_secs: Range | null; credits: null; credits_status: 'unavailable'; source: 'rule_band' | 'history_calibrated'; confidence: 'low' | 'medium'; samples: number; dominant: { slug: string; share_pct: number; turns: Range }[]; coverage_lost: { slug: string; artifacts: string[] }[] }
export interface PlanIssue { code: string; slugs: string[]; message_key: string; params: Record<string, unknown> }
export interface PlanDiffEntry { slug: string; from_enabled: boolean; to_enabled: boolean; reason: string }
export interface EffectivePlan { request: PlanRequest; scope_meta: Record<string, unknown> | null; stages: PlanStage[]; exact: ExactCounts; estimate: Estimate; diff: PlanDiffEntry[]; issues: PlanIssue[]; valid: boolean; products: string[]; graph_stage_count: number; engine_version: string | null }
export interface RecomposeProposal { intent_key: string; current_stage: string | null; skip: string[]; add: string[]; plan: EffectivePlan; argv_preview: string[]; allowed: boolean; refusals: PlanIssue[] }
/** DRIFT: §3.1 has no `EngineResult`, but `recompose` and `doctor` both return one. */
export interface EngineResult { verb_key: string; argv: string[]; exit_code: number | null; timed_out: boolean; duration_ms: number; stdout: string; stderr: string; json: Record<string, unknown> | null; ok: boolean; side_effects: string[] }
/** DRIFT: §3.1 references `IntentCreateResult` without defining it (`plan.py`). */
export interface IntentCreateResult { repo_id: string; space: string; intent_dir: string; intent_key: string; uuid: string | null; slug: string; transaction_id: string; engine: EngineResult | null; verified: boolean; summary: IntentSummary | null }

// ---- activity / events / advisor / settings ----
export interface TimelineRefs { action_id?: string; session_key?: string; audit?: { shard: string; pos: number | null; event: string }; git?: { sha: string } }
export interface TimelineEntry { id: number | null; at: string; source: Source; kind: string; message_key: string; params: Record<string, unknown>; severity: Severity; refs: TimelineRefs; raw: string | null }
export interface ActivityResponse { items: TimelineEntry[]; next_cursor: string | null }
/** Every type `EVENT_TYPES` may publish. `hello` is a per-connection greeting, not a ring row. */
export type StudioEventType =
  | 'action.created' | 'action.updated' | 'repo.updated' | 'repo.removed' | 'intent.updated'
  | 'transaction.updated' | 'lease.updated' | 'activity.appended' | 'advisor.updated'
  | 'settings.updated' | 'health.updated' | 'migration.updated' | 'reset'
/** One persisted ring row (`EventRecord.to_json`). */
export interface StudioEvent { seq: number; at: string; type: StudioEventType; payload: Record<string, unknown> }
export interface EventPollResponse { events: StudioEvent[]; cursor: number; oldest_seq: number; reset: boolean }
export interface DraftResult { verdict: string | null; summary: string; suggested_answers: { question_index: number; answer: string; option_letters: string[] }[]; evidence: string[]; assumptions: string[]; alternatives: string[]; confidence: 'low' | 'medium' | 'high'; needs_your_decision: string[]; drafted_feedback: string | null }
/**
 * A `plan_draft`'s settled answer (contracts §1.18a): a self-contained `PlanRequest` patch. `scope` is always
 * set (the proposed scope, or the human's when Q1 did not resolve) and `overrides` is the COMPLETE override
 * map for that scope, computed server-side as deltas against its grid row — the UI replaces, never merges.
 * `unresolved` lists the question indices whose letters could not be mapped and were left as they stand.
 */
export interface PlanProposal { scope: string | null; depth: PlanDepth | null; test_strategy: PlanDepth | null; review_cap: 'none' | 'advisory' | 'adversarial' | null; overrides: Record<string, boolean>; unresolved: number[]; base_scope: string | null }
/** `POST /repos/{repo_id}/intents/plan/advise` (§2.3): the objective the Advisor reads and the human's picks so far. */
export interface PlanAdviseRequest { space: string; objective: string; context: string | null; project_type: 'Greenfield' | 'Brownfield' | null; locale: string; current: Pick<PlanRequest, 'scope' | 'depth' | 'test_strategy' | 'review_cap' | 'overrides'> }
/** `plan_proposal` is on the wire only for card-less kinds (`ADVISOR_CARDLESS_KINDS`), null until the draft is ready. */
export interface AdvisorDraft { draft_id: string; action_id: string; kind: AdvisorKind; status: 'queued' | 'running' | 'ready' | 'failed' | 'expired'; request: { action_id: string; kind: string; question_index: number | null; locale: string; auto: boolean }; result: DraftResult | null; error: string | null; created_at: string; updated_at: string; expires_at: string; neutrality: Record<string, unknown> | null; plan_proposal?: PlanProposal | null }
export interface SettingsValues { locale: 'auto' | 'en-US' | 'zh-CN'; density: 'compact' | 'comfortable'; queue_organize: Organize; global_concurrency_cap: number; slack: { enabled: boolean; muted_repo_ids: string[] }; night_window: { enabled: false; start_local: string; end_local: string; turn_cap: number; credit_cap: null }; diagnostics: { retention_days: number; export_include_human_text: boolean }; human_text_retention_days: number; advisor: { enabled: boolean; auto_draft_repo_ids: string[] }; installer: { run_doctor_after_install: boolean }; notifications: { dashboard: boolean } }
export interface Capability { available: boolean; reason: string | null }
export interface BunToolResponse { tool: { found: boolean; path: string | null; configured_path?: string | null; version: string | null; source: string | null; searched: string[] } }
export type CapabilityName = 'night_window' | 'credit_cap' | 'slack' | 'advisor' | 'slack_quick_actions' | 'grouped_answers'
export interface SettingsResponse { settings: SettingsValues; capabilities: Record<CapabilityName, Capability>; versions: { studio: string; bundled_engine: string; min_kirocrew: string; host: string | null }; updated_at: string | null }
export interface HealthResponse { app: 'aidlc-studio'; version: string; bundled_engine_version: string; min_kirocrew_version: string; boot_id: string; host_version: string | null; started_at: string | null; status: 'healthy' | 'degraded' | 'error'; issues: string[]; storage: { path_hash: string; schema_version: number; integrity: 'ok' | 'error'; wal: boolean }; payload: { ok: boolean; engine_version: string; file_count: number; mismatches: number }; host: { attached: boolean; capabilities: Record<string, Capability> }; tools: { bun: { found: boolean; path: string | null; version: string | null; source: string | null; searched: string[] }; git: { found: boolean; path: string | null; version: string | null } }; reconciler: { running: boolean; last_tick_at: string | null; last_tick_ms: number | null; repos_scanned: number }; counts: { repos: number; intents: number; live_actions: number; execution_leases: number; admin_leases: number }; machine_lane: { available: false; reason: 'machine_lane_unavailable' } }
export interface MigrationPreview { applicable: boolean; reason: string | null; source_path: string; source_sha256: string | null; rows_in: number; rows: { legacy_id: string; path: string; label: string; added_at: string | null; resolution: string; identity: string | null; error: string | null }[]; rows_out: number; console_installed: boolean; console_enabled: boolean; already_applied: boolean }
/** DRIFT: §3.1 references `MigrationResult` without defining it (`migration.py`). */
export interface MigrationResult { migration_id: string; applied_at: string; status: string; backup_path: string | null; summary: Record<string, unknown>; next_steps: string[] }
export interface MigrationStatusResponse { applied: boolean; result: MigrationResult | null; preview_available: boolean; console: { installed: boolean; enabled: boolean } }

// ---- payload / diagnostics (§2.1; §3.1 defines none of these) ----
export interface PayloadFile { path: string; sha256: string; size: number; ownership: Ownership }
export interface PayloadStatus { ok: boolean; engine_version: string; file_count: number; mismatches: string[]; missing: string[]; extra: string[]; checked_at: string }
export interface PayloadResponse { schema: string; harness: string; engine_version: string; source: Record<string, unknown>; compatible_state_versions: number[]; stage_count: number; payload_digest: string; file_count: number; ownership_counts: Record<Ownership, number>; merge_targets: Record<string, Record<string, unknown>>; status: PayloadStatus; files?: PayloadFile[] }
export interface BreakerView { key: string; count: number; opened_at: string | null; fingerprint_class: string | null }
export interface DiagnosticsBundle { generated_at: string; health: HealthResponse; settings: SettingsValues; repos: RepoRecord[]; live_actions: ActionCard[]; leases: LeaseView[]; recent_transactions: TransactionResult[]; recent_activity: TimelineEntry[]; breakers: BreakerView[]; redacted: true }

// ---- response envelopes (§2, one per route; the handlers wrap every object) ----
export interface ReposResponse { repos: RepoRecord[]; totals: { repos: number; unavailable: number; open_actions: number } }
export interface RepoDetailResponse { repo: RepoRecord; intents: IntentSummary[]; transactions: TransactionResult[] }
export interface LeasesResponse { leases: LeaseView[]; global_concurrency_cap: number; live_execution: number }
export interface CalibrationResponse { cohorts: { scope: string; depth: string; stage_class: string; samples: number }[]; total: number; min_samples: number }
export interface IntentsResponse { intents: IntentSummary[]; spaces: string[]; active_space: string }
export interface ArtifactsResponse { artifacts: ArtifactMeta[]; truncated: boolean; count: number }
export interface ArtifactResponse { artifact: ArtifactMeta; content: string | null; encoding: 'utf-8' | 'binary'; truncated: boolean; review: { verdict: string | null; findings: ReviewFinding[] } | null; toc: { level: number; text: string; anchor: string }[]; prior: { available: boolean; source: 'git' | null; diff: string | null } }
export interface QuestionsResponse { questions: QuestionsView | null; mode: 'degraded' | 'structured'; host_cards: HostQuestionCard[]; slot_key: string | null }
export interface ReviewResponse { stage: string | null; verdict: string | null; findings: ReviewFinding[]; receipts: { event: string; ts: string; reviewer: string | null; iteration: number | null; verdict: string | null; fingerprint: string | null }[]; review_class: string | null; reviewer: string | null; revisions: number }
export interface IntentGitResponse { git: GitObservation; commits: GitCommit[]; changed_artifacts: { relpath: string; status: string }[] }
export interface RepoGitResponse { git: GitObservation; owned_dirty: string[]; unrelated_dirty: number }
export interface BindingResponse { ok: true; binding: BindingView }
/** DRIFT: §2.3 types `slot` as `SlotView`; `bind_session` sends `null` when the host has no such slot. */
export interface BindResponse { ok: true; binding: BindingView; slot: SlotView | null }
export interface PauseResponse { ok: true; binding: BindingView; blocked_actions: string[] }
export interface TakeoverPreviewResponse { candidates: { slot: SlotView; reason: string }[]; current: BindingView }
export interface CommandResponse { ok: true; action_id: string; status: ActionStatus; action: ActionCard }
export interface PreviewPlanResponse { plan: PreviewPlan; plan_digest: string }
export interface TransactionAcceptedResponse { ok: true; transaction_id: string; status: TransactionStatus }
export interface IntentCreateResponse { ok: true; transaction_id: string; intent: IntentCreateResult }
export interface RecomposePreviewResponse { proposal: RecomposeProposal; proposal_digest: string }
/** DRIFT: §2.3 types `intent` as `IntentSummary`; the handler sends `null` when it could not re-read. */
export interface RecomposeResponse { ok: true; transaction_id: string; result: EngineResult; intent: IntentSummary | null }
export interface DoctorResponse { ok: true; result: EngineResult }
export interface AdvisorDraftResponse { ok: true; draft: AdvisorDraft }
export interface AdvisorDraftGetResponse { draft: AdvisorDraft }
export interface PreflightResponse { preflight: PreflightReport }
export interface AddRepoResponse { ok: true; repo: RepoRecord; preflight: PreflightReport }
export interface RepoResponse { ok: true; repo: RepoRecord }
export interface RemoveRepoResponse { ok: true; removed: string }
export interface RescanResponse { ok: true; scan: RepoScan }
export interface ReceiptsResponse { receipts: Receipt[] }
export interface TransactionResponse { transaction: TransactionResult }
export interface PlanPreviewResponse { plan: EffectivePlan }
export interface CalibrationClearResponse { ok: true; removed: number }
export interface MigrationPreviewResponse { preview: MigrationPreview }
export interface MigrationApplyResponse { ok: true; result: MigrationResult }
export interface SlackCallbackResponse { ok: true; recorded: true }
export interface MapResponse { map: MapModel }
export interface IntentResponse { intent: IntentDetail }
export interface DirectoryResponse {
  path: string
  parent: string | null
  directories: { name: string; path: string }[]
  truncated: boolean
}

export interface MaintenanceEntry {
  id: string; txid: string; category: 'backups' | 'failed'; relative_path: string; status: TransactionStatus
  size_bytes: number | null; files: number | null; protected: boolean; reason: string | null; selected: boolean
}
export interface MaintenancePreview {
  repo_id: string; entries: MaintenanceEntry[]; entry_ids: string[]; plan_digest: string; can_cleanup: boolean
  blocked: { id: string; reason: string }[]
  totals: { entries: number; eligible_entries: number; protected_entries: number; selected_entries: number;
    eligible_bytes: number; selected_bytes: number; selected_files: number; unknown_sizes: number }
}
export interface MaintenanceResult {
  ok: boolean; plan_digest: string; deleted: string[]; failures: { id: string; reason: string; errno: number | null }[]
  remaining: string[]; record_key: string; requires_preview: boolean
}
