export interface InstrumentSearchItem {
  id: string
  symbol: string
  timeframe: string
  path: string
  label?: string
  status: string
}

function normalized(value: string): string {
  return value.trim().toLocaleLowerCase()
}

function subsequenceScore(query: string, value: string): number | null {
  let queryIndex = 0
  let gap = 0
  for (let index = 0; index < value.length && queryIndex < query.length; index += 1) {
    if (value[index] === query[queryIndex]) queryIndex += 1
    else if (queryIndex > 0) gap += 1
  }
  return queryIndex === query.length ? 40 + gap : null
}

function matchScore(query: string, item: InstrumentSearchItem): number | null {
  if (!query) return 0
  const symbol = normalized(item.symbol)
  const fields = [symbol, normalized(item.label ?? ''), normalized(item.path)]
  if (symbol === query) return 0
  if (symbol.startsWith(query)) return 5 + symbol.length - query.length
  const containing = fields.flatMap((field, index) => {
    const position = field.indexOf(query)
    return position < 0 ? [] : [10 + index * 5 + position]
  })
  if (containing.length > 0) return Math.min(...containing)
  return fields.reduce<number | null>((best, field) => {
    const score = subsequenceScore(query, field)
    return score === null || best !== null && best <= score ? best : score
  }, null)
}

export function fuzzyInstruments<T extends InstrumentSearchItem>(items: T[], query: string, limit = 12): T[] {
  const normalizedQuery = normalized(query)
  return items
    .flatMap((item, order) => {
      const score = matchScore(normalizedQuery, item)
      return score === null ? [] : [{ item, score, order }]
    })
    .sort((left, right) => left.score - right.score || left.order - right.order)
    .slice(0, limit)
    .map(({ item }) => item)
}
