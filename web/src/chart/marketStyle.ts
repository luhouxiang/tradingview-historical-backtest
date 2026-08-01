import type { SeriesSource } from '../types/api'

export const MARKET_COLORS = {
  background: '#131722',
  rising: '#f23645',
  falling: '#00b8a9',
  ma20: '#d500f9',
  ma60: '#9ca3af',
  macd: '#e0e3eb',
  signal: '#f2d600',
} as const

export function indicatorLineColor(source: SeriesSource, outputName: string): string {
  if (source.definition.algorithm_id === 'ma') {
    if (source.parameters.period === 20) return MARKET_COLORS.ma20
    if (source.parameters.period === 60) return MARKET_COLORS.ma60
  }
  if (source.definition.algorithm_id === 'macd') {
    if (outputName === 'signal') return MARKET_COLORS.signal
    if (outputName === 'macd') return MARKET_COLORS.macd
  }
  return '#2962ff'
}

export function histogramColor(value: number): string {
  return value >= 0 ? MARKET_COLORS.rising : MARKET_COLORS.falling
}
