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

const chanDefaults: Record<string, Pick<IndicatorOutputStyle, 'color' | 'line_width' | 'line_style'>> = {
  processed_bar: { color: '#787b86', line_width: 1, line_style: 'dotted' },
  fractal: { color: '#f23645', line_width: 1, line_style: 'solid' },
  bi: { color: '#2962ff', line_width: 2, line_style: 'solid' },
  bi_state: { color: '#26c6da', line_width: 1, line_style: 'dashed' },
  segment: { color: '#f2d600', line_width: 2, line_style: 'solid' },
  zhongshu: { color: '#64b5f6', line_width: 1, line_style: 'solid' },
  segment_zhongshu: { color: '#fff176', line_width: 2, line_style: 'solid' },
  level_center: { color: '#ff8a65', line_width: 2, line_style: 'dashed' },
  level_movement: { color: '#ce93d8', line_width: 2, line_style: 'dashed' },
  movement_state: { color: '#ab47bc', line_width: 1, line_style: 'dashed' },
  center_monitor: { color: '#26c6da', line_width: 1, line_style: 'dotted' },
  divergence: { color: '#ff9800', line_width: 1, line_style: 'solid' },
  trade_point: { color: '#ffffff', line_width: 1, line_style: 'solid' },
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
    output.object_type === 'processed_bar' || output.object_type === 'fractal' || output.object_type === 'bi' || output.object_type === 'bi_state' || output.object_type === 'segment' || output.object_type === 'zhongshu' || output.object_type === 'segment_zhongshu' || output.object_type === 'level_center' || output.object_type === 'level_movement' || output.object_type === 'movement_state' || output.object_type === 'center_monitor' || output.object_type === 'divergence' || output.object_type === 'trade_point')
}

function chanVisibility(source: StrategySource, output: AlgorithmOutput): boolean {
  if (output.object_type === 'processed_bar') return source.category_visibility.processed_bars ?? false
  if (output.object_type === 'fractal') return source.category_visibility.fractals
  if (output.object_type === 'bi') return source.category_visibility.bi
  if (output.object_type === 'bi_state') return source.category_visibility.bi_states ?? true
  if (output.object_type === 'segment') return source.category_visibility.segments
  if (output.object_type === 'zhongshu') return source.category_visibility.zhongshu
  if (output.object_type === 'segment_zhongshu') return source.category_visibility.segment_zhongshu
  if (output.object_type === 'level_center') return source.category_visibility.level_centers ?? true
  if (output.object_type === 'level_movement') return source.category_visibility.level_movements ?? true
  if (output.object_type === 'movement_state') return source.category_visibility.movement_states ?? true
  if (output.object_type === 'center_monitor') return source.category_visibility.center_monitors ?? true
  if (output.object_type === 'divergence') return source.category_visibility.divergences
  if (output.object_type === 'trade_point') return source.category_visibility.trade_points
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
    line_style: chan?.line_style ?? 'solid',
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

/**
 * Chan category visibility is the authoritative render switch.  Older
 * workspaces can contain a style-level `visible` value that no longer agrees
 * with the object-tree checkbox; normalize it at the rendering boundary so a
 * checked category cannot remain invisibly suppressed by stale style data.
 */
export function chanStyleForRendering(source?: StrategySource): IndicatorStyle | undefined {
  if (!source?.style) return undefined
  return {
    outputs: Object.fromEntries(Object.entries(source.style.outputs).map(([name, style]) => {
      const output = source.definition.outputs.find((candidate) => candidate.name === name)
      return [name, {
        ...style,
        visible: output ? chanVisibility(source, output) : style.visible,
      }]
    })),
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
