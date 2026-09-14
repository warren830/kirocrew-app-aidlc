import type { ReadOptions } from '../lib/api'
import type { EngineResult } from '../lib/types'

export type IntentDepth = 'Minimal' | 'Standard' | 'Comprehensive'
export interface IntentSettings {
  scope: string
  depth: IntentDepth
  test_strategy: IntentDepth
}
export interface SettingsProposal {
  repo_id: string
  intent_key: string
  before: IntentSettings
  after: IntentSettings
  scopes: string[]
  stages: { slug: string; state: string; before: boolean; after: boolean }[]
  allowed: boolean
  refusals: string[]
  argv_preview: string[]
  verb_key: 'utility.scope_change' | 'utility.config_change'
  selection: { space: string; intent_dir: string }
}
export interface SettingsPreviewResponse {
  proposal: SettingsProposal
  proposal_digest: string
}
export interface SettingsChangeResponse {
  ok: boolean
  result: EngineResult
  verified: { settings: IntentSettings; stages_preserved: boolean }
}
export interface SpacesResponse {
  spaces: string[]
  active_space: string
}
export interface SpaceChangeResponse extends SpacesResponse {
  ok: boolean
  result: EngineResult
}

/** Add these methods to StudioApi; the controls depend on this explicit contract, never a raw fetch. */
export interface WorkspaceApi {
  spaces(repoId: string, options?: ReadOptions): Promise<SpacesResponse>
  createSpace(repoId: string, name: string): Promise<SpaceChangeResponse>
  switchSpace(repoId: string, name: string): Promise<SpaceChangeResponse>
  intentSettingsPreview(repoId: string, intentKey: string, body: Partial<IntentSettings>): Promise<SettingsPreviewResponse>
  changeIntentSettings(repoId: string, intentKey: string, body: Partial<IntentSettings> & { proposal_digest: string }): Promise<SettingsChangeResponse>
}
