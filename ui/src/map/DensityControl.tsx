/**
 * The Overview / Detailed / Dependencies switch (FR-MAP-007).
 *
 * Local rather than the host's `SegmentedControl`, for two reasons the host component cannot give:
 * it auto-collapses to a dropdown by measuring its PARENT's width, which in a `margin-left:auto`
 * toolbar collapses unpredictably, and its pressed state is styled rather than exposed — the visual
 * spec's `.seg` is explicitly an `aria-pressed` button group (§2.5). Same precedent as `Chip` over the
 * host `Badge`.
 */

import { useI18n } from '../i18n'
import type { Density } from './StageCard'

export const DENSITIES: readonly Density[] = ['overview', 'detailed', 'dependencies']

export interface DensityControlProps {
  value: Density
  onChange: (value: Density) => void
}

export function DensityControl({ value, onChange }: DensityControlProps) {
  const { t } = useI18n()
  return (
    <div className="studio-seg" role="group" aria-label={t('map.bar.density')}>
      {DENSITIES.map((density) => (
        <button
          key={density}
          type="button"
          data-density={density}
          aria-pressed={density === value}
          onClick={() => onChange(density)}
        >
          {t(`map.density.${density}`)}
        </button>
      ))}
    </div>
  )
}
