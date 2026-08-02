import type { AlgorithmDefinition } from '../types/api'

export type IndicatorCategory = 'trend' | 'momentum' | 'volatility' | 'volume' | 'structure' | 'overlay' | 'other'

export const indicatorCategoryLabels: Record<IndicatorCategory, string> = {
  trend: '趋势',
  momentum: '动量',
  volatility: '波动率',
  volume: '成交量',
  structure: '形态结构',
  overlay: '主图叠加',
  other: '其他',
}

const categoryMatchers: Array<[IndicatorCategory, RegExp]> = [
  ['momentum', /macd|rsi|kdj|stoch|cci|roc|momentum|动量|随机指标/i],
  ['volatility', /atr|volatility|variance|standard deviation|波动率|标准差/i],
  ['volume', /volume|obv|vwap|money flow|adl|成交量|量价/i],
  ['trend', /(^|[_\s-])(ma|ema|sma|wma|dema|tema|boll|bbands|sar|ichimoku)([_\s-]|$)|moving average|均线|趋势/i],
]

export function indicatorCategory(definition: AlgorithmDefinition): IndicatorCategory {
  if (definition.kind === 'chan') return 'structure'
  const identity = `${definition.algorithm_id} ${definition.name} ${definition.outputs.map((output) => `${output.name} ${output.display_name}`).join(' ')}`
  for (const [category, matcher] of categoryMatchers) if (matcher.test(identity)) return category
  return definition.outputs.some((output) => output.pane === 'main') ? 'overlay' : 'other'
}

export function indicatorSearchText(definition: AlgorithmDefinition): string {
  const category = indicatorCategory(definition)
  return [
    definition.algorithm_id,
    definition.name,
    indicatorCategoryLabels[category],
    ...definition.outputs.flatMap((output) => [output.name, output.display_name]),
  ].join(' ').toLocaleLowerCase()
}

export function matchesIndicator(definition: AlgorithmDefinition, query: string): boolean {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean)
  const haystack = indicatorSearchText(definition)
  return terms.every((term) => haystack.includes(term))
}

export function indicatorLocation(definition: AlgorithmDefinition): string {
  if (definition.kind === 'chan') return '主图叠加'
  return definition.outputs.some((output) => output.pane === 'main') ? '主图叠加' : '独立副图'
}

export function parameterSummary(parameters: Record<string, string | number | boolean>): string {
  const values = Object.values(parameters)
  return values.length === 0 ? '默认参数' : values.slice(0, 4).map(String).join(', ')
}
