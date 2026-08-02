import type { AlgorithmDefinition } from '../types/api'

export interface DefaultChanSpec {
  sourceId: string
  definition: AlgorithmDefinition
  parameters: Record<string, string | number | boolean>
}

function parametersFrom(definition: AlgorithmDefinition): Record<string, string | number | boolean> {
  return Object.fromEntries(Object.entries(definition.parameter_schema.properties)
    .map(([name, rule]) => [name, rule.default ?? '']))
}

export function defaultChanSpec(definitions: AlgorithmDefinition[]): DefaultChanSpec | null {
  const definition = definitions.find((candidate) => candidate.kind === 'chan')
  return definition ? {
    sourceId: 'strategy-default-chan',
    definition,
    parameters: parametersFrom(definition),
  } : null
}
