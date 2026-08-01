import type { AlgorithmDefinition } from '../types/api'

export interface DefaultIndicatorSpec {
  sourceId: string
  definition: AlgorithmDefinition
  parameters: Record<string, string | number | boolean>
}

const presets = [
  { sourceId: 'series-default-ma20', algorithmId: 'ma', parameters: { period: 20, source: 'close' } },
  { sourceId: 'series-default-ma60', algorithmId: 'ma', parameters: { period: 60, source: 'close' } },
  { sourceId: 'series-default-macd-12-26-9', algorithmId: 'macd', parameters: { fast_period: 12, slow_period: 26, signal_period: 9, source: 'close' } },
] as const

export function defaultIndicatorSpecs(definitions: AlgorithmDefinition[]): DefaultIndicatorSpec[] {
  return presets.flatMap((preset) => {
    const definition = definitions.find((candidate) => candidate.kind === 'indicator' && candidate.algorithm_id === preset.algorithmId)
    return definition ? [{ sourceId: preset.sourceId, definition, parameters: { ...preset.parameters } }] : []
  })
}
