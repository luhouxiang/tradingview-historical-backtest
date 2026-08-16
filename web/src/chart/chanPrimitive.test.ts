import { describe, expect, it } from 'vitest'
import { ChanPrimitive, buildChanGeometry, chanSignalLabel } from './chanPrimitive'
import type { ChanCalculationResults } from '../types/api'

function objects(count: number): ChanCalculationResults['objects'] {
  return {
    fractals: Array.from({ length: count }, (_, index) => ({
      object_id: `fractal-${index}`, bar_index: index, time: index * 60_000, price_i64: 1000 + index,
      extreme_source_bar_index: index,
      fractal_type: index % 2 ? 'top' as const : 'bottom' as const, confirmed: index % 3 !== 0,
      confirmed_at_bar_index: index + 2, known_at_bar_index: index + 2, object_revision: 1,
    })),
    bi: [], segments: [], zhongshu: [], segment_zhongshu: [], movement_states: [], center_monitors: [], divergences: [], trade_points: [],
  }
}

describe('ChanPrimitive', () => {
  it('uses one primitive with bottom and normal batch views', () => {
    const primitive = new ChanPrimitive()
    expect(primitive.paneViews().map((view) => view.zOrder?.())).toEqual(['bottom', 'normal'])
  })

  it('projects 10,000 semantic objects as one batch without Vue nodes', () => {
    const source = objects(10_000)
    const started = performance.now()
    const geometry = buildChanGeometry(source, 10, (time) => Number(time), (price) => price)
    const elapsed = performance.now() - started
    expect(geometry.fractals).toHaveLength(10_000)
    expect(elapsed).toBeLessThan(250)
  })

  it('drops objects outside the current coordinate projection', () => {
    const geometry = buildChanGeometry(objects(3), 10, (time) => Number(time) === 0 ? null : Number(time), (price) => price)
    expect(geometry.fractals.map((item) => item.x)).toEqual([60, 120])
  })

  it('projects standard segment centers, divergence and buy-sell markers', () => {
    const source = objects(0)
    source.segment_zhongshu.push({
      object_id: 'segment-center-1', start_bar_index: 1, start_time: 60_000,
      end_bar_index: 3, end_time: 180_000, zg_i64: 120, zd_i64: 100,
      gg_i64: 130, dd_i64: 90, z_i64: 110, analysis_level: 'segment', component_kind: 'segment', component_count: 3,
      confirmed: true, confirmed_at_bar_index: 3, status: 'confirmed', leave_direction: null,
      known_at_bar_index: 3, object_revision: 1,
    })
    source.divergences.push({
      object_id: 'divergence-1', bar_index: 4, time: 240_000, price_i64: 90,
      signal_type: 'bottom_divergence', divergence_kind: 'trend', signal_class: null, strength: null, reference_object_id: 'segment-center-1',
      macd_area_reference: 20, macd_area_current: 10, confirmed: true,
      confirmed_at_bar_index: 4, known_at_bar_index: 4, object_revision: 1,
    })
    source.trade_points.push({
      ...source.divergences[0]!, object_id: 'point-1', signal_type: 'buy_1', divergence_kind: null, signal_class: 'standard', strength: null,
      macd_area_reference: null, macd_area_current: null,
    })
    source.movement_states.push({
      object_id: 'state-1', start_bar_index: 1, start_time: 60_000, end_bar_index: 3,
      end_time: 180_000, price_i64: 110, state_type: 'centre_oscillation', direction: null,
      analysis_level: 'segment', reference_object_id: 'segment-center-1', confirmed: true,
      confirmed_at_bar_index: 3, known_at_bar_index: 3, object_revision: 1,
    })
    source.center_monitors.push({
      object_id: 'monitor-1', bar_index: 3, time: 180_000, z_i64: 110, zn_i64: 115,
      range_high_i64: 130, range_low_i64: 100, component_direction: 'up',
      relative_position: 'above', strength: 'strong', migration_warning: 'up',
      analysis_level: 'segment', reference_object_id: 'segment-center-1', confirmed: true,
      confirmed_at_bar_index: 3, known_at_bar_index: 3, object_revision: 1,
    })
    const geometry = buildChanGeometry(source, 10, (time) => Number(time), (price) => price)
    expect(geometry.segmentZhongshu).toHaveLength(1)
    expect(geometry.divergences[0]).toMatchObject({ x: 240, y: 9, signal_type: 'bottom_divergence' })
    expect(geometry.tradePoints[0]?.signal_type).toBe('buy_1')
    expect(geometry.segmentEnvelopes).toHaveLength(1)
    expect(geometry.movementStates[0]?.state_type).toBe('centre_oscillation')
    expect(geometry.centerMonitors[0]).toMatchObject({ x: 180, y: 11.5, zY: 11, migration_warning: 'up' })
  })

  it('updates semantic object rendering styles as one primitive', () => {
    const primitive = new ChanPrimitive()
    primitive.setStyle({ outputs: {
      fractal: { color: '#ff5252', line_width: 1, line_style: 'solid', opacity: 0.8, visible: false },
      bi: { color: '#ab47bc', line_width: 3, line_style: 'dashed', opacity: 0.7, visible: true },
      segment: { color: '#ffeb3b', line_width: 3, line_style: 'solid', opacity: 1, visible: true },
      zhongshu: { color: '#00b8d4', line_width: 2, line_style: 'dotted', opacity: 0.6, visible: true },
      segment_zhongshu: { color: '#5b1a78', line_width: 2, line_style: 'solid', opacity: 1, visible: true },
      divergence: { color: '#ff9800', line_width: 1, line_style: 'solid', opacity: 1, visible: true },
      trade_point: { color: '#ffffff', line_width: 1, line_style: 'solid', opacity: 1, visible: true },
    } })
    expect(primitive.renderStyle()).toEqual({
      fractal: { color: '#ff5252', line_width: 1, line_style: 'solid', opacity: 0.8, visible: false },
      bi: { color: '#ab47bc', line_width: 3, line_style: 'dashed', opacity: 0.7, visible: true },
      segment: { color: '#ffeb3b', line_width: 3, line_style: 'solid', opacity: 1, visible: true },
      zhongshu: { color: '#00b8d4', line_width: 2, line_style: 'dotted', opacity: 0.6, visible: true },
      segmentZhongshu: { color: '#5b1a78', line_width: 2, line_style: 'solid', opacity: 1, visible: true },
      movementState: { color: '#ab47bc', line_width: 1, line_style: 'dashed', opacity: 0.9, visible: true },
      centerMonitor: { color: '#26c6da', line_width: 1, line_style: 'dotted', opacity: 0.9, visible: true },
      divergence: { color: '#ff9800', line_width: 1, line_style: 'solid', opacity: 1, visible: true },
      tradePoint: { color: '#ffffff', line_width: 1, line_style: 'solid', opacity: 1, visible: true },
    })
  })
})

describe('chanSignalLabel', () => {
  it('labels divergence, class points, and second-point strength', () => {
    expect(chanSignalLabel({ signal_type: 'bottom_divergence', divergence_kind: 'trend', strength: null })).toBe('趋势底背驰')
    expect(chanSignalLabel({ signal_type: 'top_divergence', divergence_kind: 'consolidation', strength: null })).toBe('盘整顶背驰')
    expect(chanSignalLabel({ signal_type: 'buy_2', divergence_kind: null, strength: 'strongest' })).toBe('最强二买')
    expect(chanSignalLabel({ signal_type: 'class_sell_2', divergence_kind: null, strength: 'weakest' })).toBe('最弱类二卖')
    expect(chanSignalLabel({ signal_type: 'class_buy_3', divergence_kind: null, strength: null })).toBe('类三买')
  })
})
