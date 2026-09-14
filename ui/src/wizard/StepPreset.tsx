/**
 * Step 2 — Preset: the scope, and the three settings that cap how much work it implies.
 *
 * The scope list is read from the repository, never hard-coded. There is no "list scopes" route, so the
 * names come from the engine's own refusal: previewing with no scope answers `scope_unknown` with the
 * scopes it does know (`WizardView.knownScopesFrom`). That is why an unselected card shows no stage count
 * — the count for a scope only exists once the server has composed that scope's plan, and inventing
 * "27 of 33 stages" from a table in a design document is exactly the kind of number that goes stale.
 *
 * Test strategy and review cap have a "From the scope" choice that sends nothing, so the engine's own
 * default applies. Depth does not: the wire contract requires one of the three values, so the selected
 * card is always the concrete depth that will be sent, and the help line says where it came from.
 *
 * The Advisor panel is a prop, not a child this step owns (FR-NEW-006): its state lives in the wizard so a
 * draft in flight survives a walk to another step and back, and so the values it fills are the wizard's
 * own fields — the ones below — rather than a second copy.
 */

import { type ReactNode } from 'react'

import { Chip } from '../shell/Chip'
import { Icon } from '../shell/Icon'
import { useI18n } from '../i18n'
import type { EffectivePlan, PlanDepth } from '../lib/types'
import type { ReviewCap, ScopeMetaView, WizardState } from './WizardView'

const DEPTHS: readonly PlanDepth[] = ['Minimal', 'Standard', 'Comprehensive']
const CAPS: readonly ReviewCap[] = ['none', 'advisory', 'adversarial']

export interface StepPresetProps {
  state: WizardState
  scopes: string[]
  meta: ScopeMetaView | null
  plan: EffectivePlan | null
  unreadable: boolean
  onPickScope: (scope: string) => void
  onPatch: (patch: Partial<WizardState>) => void
  /** `PlanAdvisorPanel`, rendered first: the offer to skip the questionnaire comes before the questionnaire. */
  advisorPanel: ReactNode
}

export function StepPreset({ state, scopes, meta, plan, unreadable, onPickScope, onPatch, advisorPanel }: StepPresetProps) {
  const i18n = useI18n()
  const { t } = i18n
  // The selected scope may not be in the discovered list (a scope this repo defines but the last refusal
  // did not mention), so it is added rather than dropped.
  const options = state.scope && !scopes.includes(state.scope) ? [...scopes, state.scope] : scopes
  const selectedCounts =
    plan && plan.request.scope === state.scope
      ? t('wizard.preset.scopeSelected', {
          on: i18n.fmt.number(plan.exact.stages),
          total: i18n.fmt.number(plan.graph_stage_count),
        })
      : null

  return (
    <div className="studio-wiz-fields">
      {advisorPanel}

      <PickRow label={t('wizard.preset.scope')} help={t('wizard.preset.scopeHelp')}>
        {options.length === 0 ? (
          <p className="studio-consequence" data-tone="warn">
            <Icon name="warn" size={13} />
            <span>{unreadable ? t('wizard.preset.scopeUnreadable') : t('common.loading')}</span>
          </p>
        ) : (
          <div className="studio-picks">
            {options.map((scope) => {
              const selected = scope === state.scope
              return (
                <button
                  type="button"
                  className="studio-pick"
                  key={scope}
                  aria-pressed={selected}
                  onClick={() => onPickScope(scope)}
                >
                  {/* AI-DLC's own scope name: verbatim, in every locale. */}
                  <span className="studio-pick-l studio-mono">{scope}</span>
                  <span className="studio-pick-d">
                    {selected ? selectedCounts ?? t('common.loading') : t('wizard.preset.scopeUnselected')}
                  </span>
                  {selected && meta?.description ? <span className="studio-pick-d">{meta.description}</span> : null}
                </button>
              )
            })}
          </div>
        )}
        {state.scope ? <p className="studio-help">{t('wizard.preset.scopeResets')}</p> : null}
        {meta ? (
          <div className="studio-row studio-wrap">
            {meta.depth ? <Chip mono>{t('wizard.preset.scopeMetaDepth', { value: meta.depth })}</Chip> : null}
            {meta.testStrategy ? <Chip mono>{t('wizard.preset.scopeMetaTest', { value: meta.testStrategy })}</Chip> : null}
            {meta.reviewCap ? <Chip mono>{t('wizard.preset.scopeMetaReview', { value: meta.reviewCap })}</Chip> : null}
            {meta.projectOwned ? <Chip icon="repo">{t('wizard.preset.scopeMetaProjectOwned')}</Chip> : null}
          </div>
        ) : null}
      </PickRow>

      <PickRow label={t('wizard.preset.depth')} help={t('wizard.preset.depthHelp')}>
        <div className="studio-picks">
          {DEPTHS.map((depth) => (
            <button
              type="button"
              className="studio-pick"
              key={depth}
              aria-pressed={state.depth === depth}
              onClick={() => onPatch({ depth })}
            >
              <span className="studio-pick-l">{depth}</span>
              <span className="studio-pick-d">{t(`wizard.preset.depth.${depth}`)}</span>
            </button>
          ))}
        </div>
        <p className="studio-help">
          {meta?.depth
            ? t('wizard.preset.fromScopeValue', { value: meta.depth })
            : meta
              ? t('wizard.preset.fromScopeDepth', { value: state.depth })
              : ''}
        </p>
      </PickRow>

      <PickRow label={t('wizard.preset.review')} help={t('wizard.preset.reviewHelp')}>
        <div className="studio-picks">
          <button
            type="button"
            className="studio-pick"
            aria-pressed={state.reviewCap === null}
            onClick={() => onPatch({ reviewCap: null })}
          >
            <span className="studio-pick-l">{t('wizard.preset.fromScope')}</span>
            <span className="studio-pick-d">
              {meta?.reviewCap ? t('wizard.preset.fromScopeValue', { value: meta.reviewCap }) : t('wizard.preset.fromScopeUnset')}
            </span>
          </button>
          {CAPS.map((cap) => (
            <button
              type="button"
              className="studio-pick"
              key={cap}
              aria-pressed={state.reviewCap === cap}
              onClick={() => onPatch({ reviewCap: cap })}
            >
              <span className="studio-pick-l studio-mono">{cap}</span>
              <span className="studio-pick-d">{t(`wizard.preset.review.${cap}`)}</span>
            </button>
          ))}
        </div>
      </PickRow>

      <PickRow label={t('wizard.preset.test')} help={t('wizard.preset.testHelp')}>
        <div className="studio-picks">
          <button
            type="button"
            className="studio-pick"
            aria-pressed={state.testStrategy === null}
            onClick={() => onPatch({ testStrategy: null })}
          >
            <span className="studio-pick-l">{t('wizard.preset.fromScope')}</span>
            <span className="studio-pick-d">
              {meta?.testStrategy
                ? t('wizard.preset.fromScopeValue', { value: meta.testStrategy })
                : t('wizard.preset.fromScopeUnset')}
            </span>
          </button>
          {DEPTHS.map((value) => (
            <button
              type="button"
              className="studio-pick"
              key={value}
              aria-pressed={state.testStrategy === value}
              onClick={() => onPatch({ testStrategy: value })}
            >
              <span className="studio-pick-l">{value}</span>
              <span className="studio-pick-d">{t(`wizard.preset.test.${value}`)}</span>
            </button>
          ))}
        </div>
      </PickRow>
    </div>
  )
}

function PickRow({ label, help, children }: { label: string; help: string; children: ReactNode }) {
  return (
    <div className="studio-field" role="group" aria-label={label}>
      <span className="studio-field-label">{label}</span>
      <p className="studio-help">{help}</p>
      {children}
    </div>
  )
}
