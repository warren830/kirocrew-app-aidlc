/**
 * Shared, fully-typed wire fixtures for the tests that cross area boundaries.
 *
 * Every field is spelled out rather than cast, because the point of a fixture is to fail when the wire
 * contract moves: `as unknown as ActionCard` would keep compiling after `types.ts` gained a field the
 * backend now sends, and the test would then assert on a card the server can no longer produce.
 *
 * The per-area suites keep their own local factories (they need dozens of variants each); this module
 * exists for the integration tests, which need one ordinary card and the five shell reads.
 */

import type {
  ActionCard, ActionType, ActionsResponse, ArtifactMeta, DecisionSpec, Decision, ReviewFinding, Severity, SettingsResponse,
} from '../lib/types'
import type { RouteHandler } from './stubs/app-sdk'

export const API_BASE = '/api/apps/aidlc-studio'

export function settingsResponse(): SettingsResponse {
  return {
    settings: {
      locale: 'auto', density: 'compact', queue_organize: 'priority', global_concurrency_cap: 2,
      slack: { enabled: false, muted_repo_ids: [] },
      night_window: { enabled: false, start_local: '22:00', end_local: '06:00', turn_cap: 40, credit_cap: null },
      diagnostics: { retention_days: 30, export_include_human_text: false },
      human_text_retention_days: 30, advisor: { enabled: true, auto_draft_repo_ids: [] },
      installer: { run_doctor_after_install: false }, notifications: { dashboard: true },
    },
    capabilities: {
      night_window: { available: false, reason: 'machine_lane_unavailable' },
      credit_cap: { available: false, reason: 'credits_unobservable' },
      slack: { available: false, reason: 'host_seam_unavailable' },
      advisor: { available: true, reason: null },
      slack_quick_actions: { available: false, reason: 'host_seam_unavailable' },
      grouped_answers: { available: false, reason: 's1_s2_unverified' },
    },
    versions: { studio: '1.0.0', bundled_engine: '2.7.1', min_kirocrew: '0.3.0', host: '0.6.0' },
    updated_at: '2026-09-10T08:00:00Z',
  }
}

export const decisionSpec = (decision: Decision, requires: DecisionSpec['requires'] = []): DecisionSpec => ({
  decision,
  label_key: `decision.${decision}.label`,
  lane: decision === 'approve' || decision === 'request_changes' ? 'human_lane' : 'studio_only',
  wire_text_template: null,
  requires,
})

export function artifactMeta(over: Partial<ArtifactMeta> = {}): ArtifactMeta {
  return {
    artifact_id: 'art_1',
    relpath: 'aidlc/spaces/default/250901-guest/functional-design.md',
    name: 'functional-design.md',
    stage: 'functional-design',
    phase: 'inception',
    unit: null,
    size: 2048,
    mtime: '2026-09-04T08:00:00Z',
    sha256: 'sha-art',
    kind: 'artifact',
    renderable: true,
    ...over,
  }
}

export function reviewFinding(over: Partial<ReviewFinding> = {}): ReviewFinding {
  return {
    level: 'blocker',
    title: 'Decline path is unspecified',
    quote: 'The design does not say what happens when the payment declines.',
    anchor: 'Decline path',
    reviewer: 'architecture-reviewer',
    iteration: 2,
    ...over,
  }
}

/** One ordinary human-lane gate card. `over` replaces whole top-level members, as the wire would. */
export function actionCard(over: Partial<ActionCard> = {}): ActionCard {
  const type: ActionType = over.type ?? 'gate'
  return {
    action_id: 'a_1',
    type,
    queue_type: type,
    status: 'Queued',
    status_generation: 1,
    source: 'aidlc',
    risk_class: 'human_lane',
    severity: 'blocking',
    priority_group: 2,
    repo: { repo_id: 'r_1', label: 'checkout-web', canonical_path: '/work/checkout-web' },
    space: 'default',
    intent: {
      intent_dir: '250901-guest', intent_key: 'default~250901-guest', slug: 'guest-checkout',
      uuid: null, title: null,
    },
    stage: { slug: 'functional-design', number: '2.3', name: null, phase: 'inception', unit: null },
    // The real backend always sends the params its headline template needs; a fixture that sent none
    // would exercise the "unfilled hole" path in every test instead of the one that renders a sentence.
    headline: { key: `action.${type}.headline`, params: { stage: 'functional-design', attempt: 1 } },
    consequence: null,
    waiting_since: '2026-09-04T09:00:00Z',
    created_at: '2026-09-04T09:00:00Z',
    updated_at: '2026-09-04T09:00:00Z',
    primary: { decision: 'approve', label_key: 'decision.approve.label' },
    decisions: [decisionSpec('approve'), decisionSpec('request_changes', ['feedback'])],
    captured: {
      state_hash: 'sha-state', boundary_token: 'tok', question_digest: null, stage_attempt: 1,
      evidence_digest: 'sha-ev', is_active: true, captured_at: '2026-09-04T09:00:00Z', stable: true,
    },
    evidence: {
      state: {
        relpath: 'aidlc-state.md', sha256: 'abcdef0123456789', size: 100,
        mtime_ns: 1_757_000_000_000_000_000, current_stage: 'functional-design',
        status: 'awaiting_approval', lifecycle_phase: 'inception', revision_count: 2, stable: true,
      },
      audit: {
        shards: [{ relpath: 'audit/000.md', size: 10, truncated: false }],
        last_event: null, boundary_event: null, complete: true,
      },
      directive: null,
      artifacts: [],
      review: null,
      acceptance_criteria: [],
      findings: [],
      session: {
        slot_key: 'aidlc-r1-guest', session_key: 'ses_1c4', running: false, stop_state: 'idle',
        last_turn_ts: null, queue_depth: 0, busy_reasons: [],
      },
      questions: null,
      git: null,
      markers: {
        human_turn_at: null, engine_touch_at: null, turn_counter: null, goal_stop_present: false,
        recovery: null,
      },
      presence_baseline: null,
      presence: null,
      cursor_readback: null,
    },
    delivery: {
      lane: null, delivery_id: null, slot_key: null, session_key: null, wire_text: null, host: null,
      delivering_at: null, delivered_at: null, queued_at: null, deadline_at: null, receipt: null,
      outcome: null, delivery_confirmed: false, boot_id_unchanged: null, transcript_row: null,
      slot_ran_since: null, disk_baseline_unchanged: null,
    },
    resolution: { kind: null, resolved_at: null, evidence: null, reason: null },
    failure: null,
    install: null,
    budget: null,
    advisor: { latest_draft_id: null, status: null },
    deep_link: `/apps/aidlc-studio?view=actions&action=${over.action_id ?? 'a_1'}`,
    dedupe_key: null,
    human_text_present: false,
    acknowledged_evidence_sha256: 'sha-ack',
    ...over,
  }
}

export function actionsResponse(cards: ActionCard[]): ActionsResponse {
  const counts: ActionsResponse['counts'] = { total: cards.length, critical: 0, blocking: 0, attention: 0, info: 0 }
  for (const card of cards) counts[card.severity as Severity] += 1
  return { actions: cards, organize: 'priority', groups: [], counts, generated_at: '2026-09-04T10:00:00Z' }
}

/**
 * The five reads the shell issues on mount, plus the event poll.
 *
 * Any test that mounts `StudioApp` needs all of them: a missing one is a resource error, and the shell
 * turns a failed `/health` into a banner that changes what is on screen.
 */
export function shellRoutes(cards: ActionCard[] = []): Record<string, RouteHandler> {
  const actions = actionsResponse(cards)
  return {
    [`GET ${API_BASE}/actions?*`]: () => actions,
    [`GET ${API_BASE}/actions`]: () => actions,
    [`GET ${API_BASE}/actions/*`]: (_body, path) => {
      const id = path.split('?')[0]?.split('/').pop() ?? ''
      const found = cards.find((card) => card.action_id === id) ?? cards[0] ?? null
      if (!found) throw new Error(`no fixture card for ${id}`)
      return { action: found, transitions: [], drafts: [] }
    },
    [`GET ${API_BASE}/repos*`]: () => ({
      repos: [
        {
          repo_id: 'r_1', label: 'checkout-web', canonical_path: '/work/checkout-web',
          availability: 'available',
        },
      ],
      totals: { repos: 1, unavailable: 0, open_actions: cards.length },
    }),
    [`GET ${API_BASE}/leases`]: () => ({ leases: [], global_concurrency_cap: 2, live_execution: 0 }),
    [`GET ${API_BASE}/settings`]: settingsResponse,
    [`GET ${API_BASE}/health`]: () => ({ status: 'healthy', issues: [] }),
    [`GET ${API_BASE}/events/poll*`]: () => ({ events: [], cursor: 0, oldest_seq: 0, reset: false }),
  }
}
