import type { ChanSignalPoint, StrategySource } from '../types/api'

type Visibility = Pick<StrategySource['category_visibility'],
  'first_trade_points' | 'second_trade_points' | 'third_trade_points'
  | 'class_first_trade_points' | 'class_second_trade_points' | 'class_third_trade_points'>

export function tradePointCategory(signalType: ChanSignalPoint['signal_type']): keyof Visibility | null {
  const prefix = signalType.startsWith('class_') ? 'class_' : ''
  if (signalType.endsWith('_1')) return `${prefix}first_trade_points` as keyof Visibility
  if (signalType.endsWith('_2')) return `${prefix}second_trade_points` as keyof Visibility
  if (signalType.endsWith('_3')) return `${prefix}third_trade_points` as keyof Visibility
  return null
}

export function isTradePointVisible(signalType: ChanSignalPoint['signal_type'], visibility: Visibility): boolean {
  const category = tradePointCategory(signalType)
  if (category === null) return false
  if (!category.startsWith('class_')) return visibility[category] ?? false
  const standardCategory = category.slice('class_'.length) as keyof Visibility
  return visibility[category] ?? visibility[standardCategory] ?? false
}

export function selectVisibleTradePoints(points: ChanSignalPoint[], visibility: Visibility): ChanSignalPoint[] {
  return points.filter((point) => isTradePointVisible(point.signal_type, visibility))
}
