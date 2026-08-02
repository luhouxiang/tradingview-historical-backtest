import { describe, expect, it } from 'vitest'
import { defaultChanSpec } from './defaults'
import type { AlgorithmDefinition } from '../types/api'

describe('defaultChanSpec', () => {
  it('creates one default causal Chan source from the published definition', () => {
    const definition = {
      kind: 'chan', algorithm_id: 'chan_engineering', algorithm_version: '1.0.0',
      parameter_schema: { properties: { min_stroke_bars: { default: 5 }, min_stroke_atr: { default: .5 } } },
    } as unknown as AlgorithmDefinition
    expect(defaultChanSpec([definition])).toMatchObject({
      sourceId: 'strategy-default-chan', definition,
      parameters: { min_stroke_bars: 5, min_stroke_atr: .5 },
    })
  })
})
