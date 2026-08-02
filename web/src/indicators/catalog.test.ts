import { describe, expect, it } from 'vitest'
import type { AlgorithmDefinition } from '../types/api'
import { indicatorCategory, matchesIndicator, parameterSummary } from './catalog'

function definition(algorithmId: string, name: string, pane: 'main' | 'indicator' = 'indicator'): AlgorithmDefinition {
  return {
    kind: 'indicator', algorithm_id: algorithmId, algorithm_version: '1.0.0', source_hash: `sha256:${'a'.repeat(64)}`,
    name, input_schema: 'bars.v1', causal: true,
    parameter_schema: { type: 'object', properties: {}, required: [], additionalProperties: false },
    outputs: [{ name: algorithmId, display_name: name, pane, series_type: 'line' }],
    warmup: { kind: 'formula', expression: '0' },
  }
}

describe('indicator catalog', () => {
  it('classifies common families and searches names, ids, outputs and Chinese categories', () => {
    const macd = definition('macd', 'Moving Average Convergence Divergence')
    const customOverlay = definition('custom_channel', 'Custom Channel', 'main')
    expect(indicatorCategory(macd)).toBe('momentum')
    expect(indicatorCategory(customOverlay)).toBe('overlay')
    expect(matchesIndicator(macd, 'MACD 动量')).toBe(true)
    expect(matchesIndicator(macd, 'rsi')).toBe(false)
  })

  it('keeps current parameter summaries compact', () => {
    expect(parameterSummary({ fast: 12, slow: 26, signal: 9, source: 'close', ignored: 1 })).toBe('12, 26, 9, close')
    expect(parameterSummary({})).toBe('默认参数')
  })
})
