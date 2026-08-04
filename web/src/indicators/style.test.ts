import { describe, expect, it } from 'vitest'
import type { AlgorithmDefinition, SeriesSource, StrategySource } from '../types/api'
import { canvasDash, chanStyleForRendering, colorWithOpacity, completeIndicatorStyle, resolvedOutputStyle, styleableOutputs } from './style'

function definition(kind: 'indicator' | 'chan'): AlgorithmDefinition {
  return {
    kind,
    algorithm_id: kind === 'chan' ? 'chan_engineering' : 'ma',
    algorithm_version: '1.0.0',
    source_hash: `sha256:${'a'.repeat(64)}`,
    name: kind === 'chan' ? '工程缠论' : 'Moving Average',
    input_schema: 'bars.v1',
    causal: true,
    parameter_schema: { type: 'object', additionalProperties: false, required: [], properties: {} },
    outputs: kind === 'chan'
      ? [
          { name: 'segment', display_name: '段', pane: 'main', series_type: 'semantic_objects', object_type: 'segment' },
          { name: 'fractal', display_name: '分型', pane: 'main', series_type: 'semantic_objects', object_type: 'fractal' },
          { name: 'bi', display_name: '笔', pane: 'main', series_type: 'semantic_objects', object_type: 'bi' },
          { name: 'zhongshu', display_name: '中枢', pane: 'main', series_type: 'semantic_objects', object_type: 'zhongshu' },
          { name: 'segment_zhongshu', display_name: '标准线段中枢', pane: 'main', series_type: 'semantic_objects', object_type: 'segment_zhongshu' },
          { name: 'divergence', display_name: '背驰', pane: 'main', series_type: 'semantic_objects', object_type: 'divergence' },
          { name: 'trade_point', display_name: '买卖点', pane: 'main', series_type: 'semantic_objects', object_type: 'trade_point' },
        ]
      : [
          { name: 'ma', display_name: 'MA', pane: 'main', series_type: 'line' },
          { name: 'ignored', display_name: '柱', pane: 'indicator', series_type: 'histogram' },
        ],
    warmup: { kind: 'formula', expression: '0' },
  }
}

describe('indicator styles', () => {
  it('builds complete defaults only for configurable line outputs', () => {
    const source: SeriesSource = {
      source_type: 'SeriesSource', source_id: 'series-1', definition: definition('indicator'),
      parameters: {}, job_id: 'job-1', status: 'completed',
    }
    expect(styleableOutputs(source).map((output) => output.name)).toEqual(['ma'])
    expect(completeIndicatorStyle(source).outputs.ma).toMatchObject({
      line_width: 1, line_style: 'solid', opacity: 1, visible: true,
    })
  })

  it('uses semantic Chan defaults and category visibility', () => {
    const source: StrategySource = {
      source_type: 'StrategySource', source_id: 'strategy-1', definition: definition('chan'),
      parameters: {}, job_id: 'job-1', status: 'completed', visible: true,
      category_visibility: { fractals: false, bi: true, segments: true, zhongshu: true, segment_zhongshu: true, divergences: true, trade_points: true },
    }
    const style = completeIndicatorStyle(source)
    expect(style.outputs.fractal).toMatchObject({ color: '#f23645', visible: false })
    expect(style.outputs.bi).toMatchObject({ color: '#2962ff', line_width: 2, visible: true })
    expect(style.outputs.segment).toMatchObject({ color: '#f2d600', line_width: 2, visible: true })
    expect(style.outputs.zhongshu).toMatchObject({ color: '#64b5f6', line_style: 'solid', visible: true })
    expect(style.outputs.segment_zhongshu).toMatchObject({ color: '#fff176', line_style: 'solid', visible: true })
  })

  it('resolves saved styles and converts rendering helpers deterministically', () => {
    const source: SeriesSource = {
      source_type: 'SeriesSource', source_id: 'series-1', definition: definition('indicator'),
      parameters: {}, job_id: 'job-1', status: 'completed',
      style: { outputs: { ma: { color: '#ab47bc', line_width: 3, line_style: 'dashed', opacity: 0.7, visible: true } } },
    }
    expect(resolvedOutputStyle(source, source.definition.outputs[0]!)).toEqual(source.style?.outputs.ma)
    expect(colorWithOpacity('#ab47bc', 0.7)).toBe('rgba(171, 71, 188, 0.7)')
    expect(canvasDash('dashed', 2)).toEqual([12, 8])
    expect(canvasDash('dotted', 2)).toEqual([2, 6])
    expect(canvasDash('solid', 2)).toEqual([])
  })

  it('uses object-tree categories to override stale Chan style visibility', () => {
    const source: StrategySource = {
      source_type: 'StrategySource', source_id: 'strategy-1', definition: definition('chan'),
      parameters: {}, job_id: 'job-1', status: 'completed', visible: true,
      category_visibility: { fractals: false, bi: true, segments: true, zhongshu: true, segment_zhongshu: true, divergences: true, trade_points: true },
      style: { outputs: {
        bi: { color: '#2962ff', line_width: 2, line_style: 'solid', opacity: 1, visible: false },
        fractal: { color: '#f23645', line_width: 1, line_style: 'solid', opacity: 1, visible: true },
      } },
    }
    expect(chanStyleForRendering(source)?.outputs.bi.visible).toBe(true)
    expect(chanStyleForRendering(source)?.outputs.fractal.visible).toBe(false)
  })
})
