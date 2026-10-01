import { describe, expect, it } from 'vitest'
import { anyDivergenceVisible, divergenceLayer, divergenceVisible } from './divergenceVisibility'

const legacy = { divergences: true } as never

describe('divergence visibility', () => {
  it.each([
    ['trend', 'trend_divergences'],
    ['consolidation', 'consolidation_divergences'],
    ['center_oscillation', 'oscillation_divergences'],
  ] as const)('maps %s to %s without changing the signal semantics', (kind, category) => {
    const signal = { divergence_kind: kind, divergence_profile: null }
    expect(divergenceLayer(signal)).toBe(category)
    expect(divergenceVisible(signal, legacy)).toBe(true)
    expect(divergenceVisible(signal, { divergences: true, [category]: false } as never)).toBe(false)
  })

  it('uses the recorded profile only for old signals missing a kind', () => {
    expect(divergenceLayer({ divergence_kind: null, divergence_profile: 'external_range' })).toBe('consolidation_divergences')
    expect(divergenceLayer({ divergence_kind: null, divergence_profile: null })).toBeNull()
  })

  it('keeps the aggregate drawing style visible when any new layer is enabled', () => {
    expect(anyDivergenceVisible({ divergences: false, trend_divergences: true,
      consolidation_divergences: false, oscillation_divergences: false } as never)).toBe(true)
  })
})
