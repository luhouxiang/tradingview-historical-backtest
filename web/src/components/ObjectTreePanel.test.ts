import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { DrawingObject } from '../drawing/model'
import type { ChanSignalPoint } from '../types/api'
import ObjectTreePanel from './ObjectTreePanel.vue'

function drawing(id: string, order: number): DrawingObject {
  return {
    id, name: id, type: 'rectangle', pane_id: 'main', visible: true, locked: false,
    z_band: 600, order_in_band: order, style: { color: '#2962ff', line_width: 1, fill_opacity: .15 },
    anchors: [{ time: 1, price_i64: 1, price_scale: 1 }, { time: 2, price_i64: 2, price_scale: 1 }],
    revision: 1, created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z',
  }
}

describe('ObjectTreePanel', () => {
  it('keeps object order visible and exposes rename, hide, lock, reorder and delete actions', async () => {
    const wrapper = mount(ObjectTreePanel, { props: { drawings: [drawing('upper', 1), drawing('lower', 0)], sources: [], strategySources: [], selectedId: 'lower' } })
    const nodes = wrapper.findAll('[data-object-type="DrawingObject"]')
    expect(nodes.map((node) => node.get('input').element.value)).toEqual(['lower', 'upper'])
    expect(nodes[0]?.classes()).toContain('selected')
    await nodes[0]?.findAll('button')[0]?.trigger('click')
    expect(wrapper.emitted('patchDrawing')?.at(-1)).toEqual(['lower', { visible: false }])
    await nodes[0]?.findAll('button')[1]?.trigger('click')
    expect(wrapper.emitted('patchDrawing')?.at(-1)).toEqual(['lower', { locked: true }])
    await nodes[0]?.findAll('button')[3]?.trigger('click')
    expect(wrapper.emitted('reorderDrawing')?.at(-1)).toEqual(['lower', 1])
    await nodes[0]?.findAll('button')[4]?.trigger('click')
    expect(wrapper.emitted('removeDrawing')?.at(-1)).toEqual(['lower'])
  })

  it('shows each signal under its StrategySource newest first and emits selection and lock', async () => {
    const strategy = {
      source_type: 'StrategySource', source_id: 'chan-1', definition: { name: '标准缠论' },
      parameters: {}, job_id: 'job-1', status: 'completed', visible: true,
      category_visibility: { fractals: true, bi: true, segments: true, zhongshu: true, segment_zhongshu: true, divergences: true, trade_points: true },
    }
    const older = {
      object_id: 'buy-old', bar_index: 10, time: 1_700_000_000_000, price_i64: 2650,
      signal_type: 'buy_1', divergence_kind: null, signal_class: 'standard', strength: null,
      reference_object_id: null, macd_area_reference: null, macd_area_current: null,
      confirmed: true, confirmed_at_bar_index: 11, known_at_bar_index: 11, object_revision: 1,
    } as ChanSignalPoint
    const newer = { ...older, object_id: 'class-buy-new', bar_index: 20, signal_type: 'class_buy_1' as const } as ChanSignalPoint
    const wrapper = mount(ObjectTreePanel, { props: {
      dataset: { time: { timezone: 'Asia/Shanghai' }, price: { price_scale: 1, price_decimals: 0 } } as never,
      drawings: [], sources: [], strategySources: [strategy] as never, selectedId: null,
      selectedSignalId: 'class-buy-new', lockedSignalId: null,
      signalsBySource: { 'chan-1': [older, newer] },
    } })
    expect(wrapper.findAll('[data-object-type="StrategySource"]')).toHaveLength(1)
    const signals = wrapper.findAll('[data-object-type="ChanSignalObject"]')
    expect(signals.map((node) => node.attributes('data-signal-id'))).toEqual(['class-buy-new', 'buy-old'])
    expect(signals[0]?.classes()).toContain('selected')
    expect(signals[0]?.text()).toContain('类一买')
    expect(wrapper.findAll('.strategy-node label')).toHaveLength(7)
    await wrapper.get('.strategy-node label input').trigger('change')
    expect(wrapper.emitted('patchStrategy')?.at(-1)?.[0]).toBe('chan-1')
    await signals[0]?.trigger('click')
    expect(wrapper.emitted('selectSignal')?.at(-1)).toEqual([newer])
    await signals[0]?.get('.signal-object-lock').trigger('click')
    expect(wrapper.emitted('lockSignal')?.at(-1)).toEqual([newer])
  })
})
