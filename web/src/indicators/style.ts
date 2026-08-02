import { indicatorLineColor } from '../chart/marketStyle'
import type {
  AlgorithmOutput,
  IndicatorLineStyleName,
  IndicatorOutputStyle,
  IndicatorStyle,
  SeriesSource,
  StrategySource,
} from '../types/api'

export type StyleSource = SeriesSource | StrategySource

const chanDefaults: Record<string, Pick<IndicatorOutputStyle, 'color' | 'line_width'>> = {
  fractal: { color: '#f23645', line_width: 1 },
  bi: { color: '#2962ff', line_width: 2 },
  zhongshu: { color: '#ab47bc', line_width: 1 },
}

export const indicatorPalette = [
  '#ffffff', '#d1d4dc', '#9598a1', '#787b86', '#434651', '#2a2e39', '#000000',
  '#ff1744', '#ff5252', '#ff9800', '#ffc107', '#ffeb3b', '#8bc34a', '#00bfa5',
  '#00b8d4', '#2962ff', '#3d5afe', '#7c4dff', '#9c27b0', '#e91e63', '#f48fb1',
  '#ffcc80', '#fff59d', '#c5e1a5', '#80cbc4', '#80deea', '#90caf9', '#b39ddb',
  '#ce93d8', '#ef9a9a', '#f23645', '#f7a600', '#f2d600', '#089981', '#00b8a9',
  '#26c6da', '#42a5f5', '#536dfe', '#7e57c2', '#ab47bc', '#ec407a', '#8b2b31',
  '#e65100', '#f9a825', '#2e7d32', '#00695c', '#00838f', '#0d47a1', '#283593',
  '#4527a0', '#6a1b9a', '#ad1457',
] as const

export function styleableOutputs(source: StyleSource): AlgorithmOutput[] {
  if (source.source_type === 'SeriesSource') {
    return source.definition.outputs.filter((output) => output.series_type === 'line')
  }
  return source.definition.outputs.filter((output) =>
    output.object_type === 'fractal' || output.object_type === 'bi' || output.object_type === 'zhongshu')
}

function chanVisibility(source: StrategySource, output: AlgorithmOutput): boolean {
  if (output.object_type === 'fractal') return source.category_visibility.fractals
  if (output.object_type === 'bi') return source.category_visibility.bi
  if (output.object_type === 'zhongshu') return source.category_visibility.zhongshu
  return true
}

export function defaultOutputStyle(source: StyleSource, output: AlgorithmOutput): IndicatorOutputStyle {
  const chan = source.source_type === 'StrategySource'
    ? chanDefaults[output.object_type ?? output.name]
    : undefined
  const visible = source.source_type === 'StrategySource' ? chanVisibility(source, output) : true
  return {
    color: chan?.color ?? indicatorLineColor(source as SeriesSource, output.name),
    line_width: chan?.line_width ?? 1,
    line_style: 'solid',
    opacity: 1,
    visible,
  }
}

export function resolvedOutputStyle(source: StyleSource, output: AlgorithmOutput): IndicatorOutputStyle {
  return source.style?.outputs[output.name] ?? defaultOutputStyle(source, output)
}

export function completeIndicatorStyle(source: StyleSource): IndicatorStyle {
  return {
    outputs: Object.fromEntries(styleableOutputs(source).map((output) => [
      output.name,
      { ...resolvedOutputStyle(source, output) },
    ])),
  }
}

export function colorWithOpacity(color: string, opacity: number): string {
  const normalized = color.replace('#', '')
  if (!/^[0-9a-fA-F]{6}$/.test(normalized)) return color
  if (opacity >= 1) return color
  const red = Number.parseInt(normalized.slice(0, 2), 16)
  const green = Number.parseInt(normalized.slice(2, 4), 16)
  const blue = Number.parseInt(normalized.slice(4, 6), 16)
  return `rgba(${red}, ${green}, ${blue}, ${Math.min(1, Math.max(0.1, opacity))})`
}

export function canvasDash(style: IndicatorLineStyleName, width = 1): number[] {
  if (style === 'dashed') return [6 * width, 4 * width]
  if (style === 'dotted') return [width, 3 * width]
  return []
}
