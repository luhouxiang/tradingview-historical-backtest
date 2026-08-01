import { describe, expect, it } from 'vitest'
import type { AlgorithmDefinition } from '../types/api'
import { defaultIndicatorSpecs } from './defaults'

function definition(algorithmId: string): AlgorithmDefinition {
  return {
    kind: 'indicator', algorithm_id: algorithmId, algorithm_version: '1.0.0', source_hash: `sha256:${'a'.repeat(64)}`,
    name: algorithmId.toUpperCase(), input_schema: 'bars.v1', causal: true,
    parameter_schema: { type: 'object', additionalProperties: false, required: [], properties: {} },
    outputs: [], warmup: { kind: 'formula', expression: '0' },
  }
}

describe('defaultIndicatorSpecs', () => {
  it('selects Python MA20, MA60 and MACD(12,26,9) definitions', () => {
    const specs = defaultIndicatorSpecs([definition('atr'), definition('macd'), definition('ma')])
    expect(specs.map(({ sourceId, definition: item, parameters }) => ({ sourceId, algorithmId: item.algorithm_id, parameters }))).toEqual([
      { sourceId: 'series-default-ma20', algorithmId: 'ma', parameters: { period: 20, source: 'close' } },
      { sourceId: 'series-default-ma60', algorithmId: 'ma', parameters: { period: 60, source: 'close' } },
      { sourceId: 'series-default-macd-12-26-9', algorithmId: 'macd', parameters: { fast_period: 12, slow_period: 26, signal_period: 9, source: 'close' } },
    ])
  })
})
