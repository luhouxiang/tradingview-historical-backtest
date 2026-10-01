import type { ChanSignalPoint, StrategySource } from '../types/api'

export type DivergenceLayer = 'trend_divergences' | 'consolidation_divergences' | 'oscillation_divergences'
type DivergenceIdentity = Pick<ChanSignalPoint, 'divergence_kind' | 'divergence_profile'>
type Visibility = Pick<StrategySource['category_visibility'], 'divergences' | DivergenceLayer>

export function divergenceLayer(signal: DivergenceIdentity): DivergenceLayer | null {
  if (signal.divergence_kind === 'trend') return 'trend_divergences'
  if (signal.divergence_kind === 'consolidation') return 'consolidation_divergences'
  if (signal.divergence_kind === 'center_oscillation') return 'oscillation_divergences'
  // Older cached signals may carry a profile but no kind. Do not guess when both are absent.
  if (signal.divergence_profile === 'standard_trend' || signal.divergence_profile === 'segment_trend_candidate') return 'trend_divergences'
  if (signal.divergence_profile === 'external_range') return 'consolidation_divergences'
  if (signal.divergence_profile === 'center_oscillation') return 'oscillation_divergences'
  return null
}

export function divergenceVisible(signal: DivergenceIdentity, visibility: Visibility): boolean {
  const layer = divergenceLayer(signal)
  return layer !== null && (visibility[layer] ?? visibility.divergences)
}

export function anyDivergenceVisible(visibility: Visibility): boolean {
  return Boolean(visibility.trend_divergences ?? visibility.divergences)
    || Boolean(visibility.consolidation_divergences ?? visibility.divergences)
    || Boolean(visibility.oscillation_divergences ?? visibility.divergences)
}
