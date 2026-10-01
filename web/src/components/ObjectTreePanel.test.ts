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
      category_visibility: { fractals: true, bi: true, segments: true, bi_centers: true, segment_centers: true, bi_boundary_confirmations: true, segment_boundary_confirmations: true, divergences: true, first_trade_points: true, second_trade_points: true, third_trade_points: true },
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
      signalsBySource: { 'chan-1': [
        { ...older, layer_category: 'first_trade_points' },
        { ...newer, layer_category: 'class_first_trade_points' },
      ] as never },
    } })
    expect(wrapper.findAll('[data-object-type="StrategySource"]')).toHaveLength(1)
    const signals = wrapper.findAll('[data-object-type="ChanSignalObject"]')
    expect(signals.map((node) => node.attributes('data-signal-id'))).toEqual(['class-buy-new', 'buy-old'])
    expect(signals[0]?.classes()).toContain('selected')
    expect(signals[0]?.text()).toContain('类一买')
    expect(wrapper.findAll('.strategy-node label')).toHaveLength(18)
    expect(wrapper.findAll('.strategy-node label').map((label) => label.text())).toEqual([
      '处理后K线', '分型', '笔', '笔状态', '线段', '线段状态', '笔中枢', '线段中枢', '中枢对象',
      '走势状态', '中枢监控', '背驰', '一买卖点', '二买卖点', '三买卖点',
      '类一买卖点', '类二买卖点', '类三买卖点',
    ])
    await wrapper.findAll('.strategy-node label input')[7]?.trigger('change')
    expect(wrapper.emitted('patchStrategy')?.at(-1)).toEqual(['chan-1', {
      category_visibility: { ...strategy.category_visibility, segment_centers: false },
    }])
    await wrapper.get('.strategy-node label input').trigger('change')
    expect(wrapper.emitted('patchStrategy')?.at(-1)?.[0]).toBe('chan-1')
    await signals[0]?.trigger('click')
    expect(wrapper.emitted('selectSignal')?.at(-1)).toEqual([{ ...newer, layer_category: 'class_first_trade_points' }])
    await signals[0]?.get('.signal-object-lock').trigger('click')
    expect(wrapper.emitted('lockSignal')?.at(-1)).toEqual([{ ...newer, layer_category: 'class_first_trade_points' }])
    await wrapper.setProps({ strategySources: [{
      ...strategy, category_visibility: { ...strategy.category_visibility, class_first_trade_points: false },
    }] as never })
    expect(wrapper.findAll('[data-object-type="ChanSignalObject"]').map((node) => node.attributes('data-signal-id'))).toEqual(['buy-old'])
    await wrapper.setProps({ strategySources: [{
      ...strategy, category_visibility: { ...strategy.category_visibility, first_trade_points: false, class_first_trade_points: true },
    }] as never })
    expect(wrapper.findAll('[data-object-type="ChanSignalObject"]').map((node) => node.attributes('data-signal-id'))).toEqual(['class-buy-new'])
  })

  it('shows backtest strategy states as selectable and locatable semantic objects', async () => {
    const item = {
      object_id: 'state-80', bar_index: 80, time: 1_700_000_000_000, price_i64: 2650,
      confirmed_at_bar_index: 80, known_at_bar_index: 80, object_revision: 1,
      label: '中枢上方·有三买', detail: 'CENTRE_STATE_ABOVE_WITH_B3',
    }
    const wrapper = mount(ObjectTreePanel, { props: {
      dataset: { time: { timezone: 'Asia/Shanghai' }, price: { price_scale: 1, price_decimals: 0 } } as never,
      drawings: [], sources: [], strategySources: [], selectedId: null,
      strategyRunSources: [{
        source_type: 'StrategyRunSource', source_id: 'run-source-1', run_id: 'run-1',
        definition: { name: '固定级别中枢决策树' }, status: 'completed', visible: true,
        objects: [item], signals: [],
      }] as never,
    } })
    expect(wrapper.get('[data-object-type="StrategyRunSource"]').text()).toContain('固定级别中枢决策树')
    const node = wrapper.get('[data-object-type="StrategySemanticObject"]')
    expect(node.text()).toContain('中枢上方·有三买')
    await node.trigger('click')
    expect(wrapper.emitted('selectSignal')?.at(-1)).toEqual([item])
    await node.get('.signal-object-lock').trigger('click')
    expect(wrapper.emitted('lockSignal')?.at(-1)).toEqual([item])
  })

  it('exposes local-center audit facts on hover with Chinese layer labels', () => {
    const strategy = {
      source_type: 'StrategySource', source_id: 'chan-local', definition: { name: '标准缠论' },
      parameters: {}, job_id: 'job-local', status: 'completed', visible: true,
      category_visibility: { fractals: false, bi: true, segments: false, bi_centers: true, segment_centers: true, center_objects: true, bi_boundary_confirmations: true, segment_boundary_confirmations: true, divergences: false, first_trade_points: false, second_trade_points: false, third_trade_points: false },
    }
    const center = {
      object_id: 'center-1', object_type: 'local_center', bar_index: 20, time: 1_700_000_000_000,
      price_i64: 2650, confirmed_at_bar_index: 21, known_at_bar_index: 21, object_revision: 1,
      label: '笔实体中枢 · 已分界', detail: 'ZD 2600 / ZG 2700 · stroke',
      hover_detail: '构成三单元：bi-1 → bi-2 → bi-3\n进入/离开/首次回试：bi-0 / bi-4 / bi-5\n扫描起点：K0\n分界确认：K21',
    }
    const wrapper = mount(ObjectTreePanel, { props: {
      dataset: { time: { timezone: 'Asia/Shanghai' }, price: { price_scale: 1, price_decimals: 0 } } as never,
      drawings: [], sources: [], strategySources: [strategy] as never, selectedId: null,
      signalsBySource: { 'chan-local': [center] as never[] },
    } })

    expect(wrapper.text()).toContain('实体中枢')
    expect(wrapper.get('[data-signal-id="center-1"]').attributes('title')).toContain('bi-1 → bi-2 → bi-3')
    expect(wrapper.get('[data-signal-id="center-1"]').attributes('title')).toContain('分界确认：K21')
  })

  it('lists center objects only when the object switch and corresponding drawn center layer are both enabled', async () => {
    const visibility = {
      fractals: false, bi: false, segments: false,
      bi_centers: true, segment_centers: true, center_objects: false,
      bi_boundary_confirmations: false, segment_boundary_confirmations: false,
      divergences: false, first_trade_points: false, second_trade_points: false, third_trade_points: false,
    }
    const source = {
      source_type: 'StrategySource', source_id: 'centers', definition: { name: '缠论' },
      parameters: {}, job_id: 'job-centers', status: 'completed', visible: true,
      category_visibility: visibility,
    }
    const base = { object_type: 'local_center', bar_index: 20, time: 20, price_i64: 20, confirmed_at_bar_index: 20, known_at_bar_index: 20, object_revision: 1 }
    const centers = [
      { ...base, object_id: 'bi-center', layer_category: 'bi_centers', label: '笔中枢' },
      { ...base, object_id: 'segment-center', layer_category: 'segment_centers', label: '线段中枢' },
    ]
    const wrapper = mount(ObjectTreePanel, { props: {
      drawings: [], sources: [], strategySources: [source] as never, selectedId: null,
      signalsBySource: { centers: centers as never[] },
    } })
    const shown = () => wrapper.findAll('[data-object-type="ChanSignalObject"]').map((node) => node.attributes('data-signal-id'))
    expect(shown()).toEqual([])
    await wrapper.setProps({ strategySources: [{ ...source, category_visibility: { ...visibility, center_objects: true } }] as never })
    expect(shown()).toEqual(['segment-center', 'bi-center'])
    await wrapper.setProps({ strategySources: [{ ...source, category_visibility: { ...visibility, center_objects: true, segment_centers: false } }] as never })
    expect(shown()).toEqual(['bi-center'])
    await wrapper.setProps({ strategySources: [{ ...source, category_visibility: { ...visibility, center_objects: true, bi_centers: false } }] as never })
    expect(shown()).toEqual(['segment-center'])
    await wrapper.setProps({ strategySources: [{ ...source, category_visibility: { ...visibility, center_objects: true, bi_centers: false, segment_centers: false } }] as never })
    expect(shown()).toEqual([])
  })

  it('keeps the Chan object list in one-to-one sync with layer visibility', async () => {
    const strategy = {
      source_type: 'StrategySource', source_id: 'chan-filtered', definition: { name: '标准缠论' },
      parameters: {}, job_id: 'job-filtered', status: 'completed', visible: true,
      category_visibility: {
        processed_bars: false, fractals: false, bi: true, bi_states: false, segments: false,
        bi_centers: false, segment_centers: false, bi_boundary_confirmations: false, segment_boundary_confirmations: false, movement_states: false, center_monitors: false,
        divergences: false, first_trade_points: false, second_trade_points: false, third_trade_points: false,
      },
    }
    const objects = [
      { object_id: 'bi-1', object_type: 'bi', layer_category: 'bi', label: '向上笔', bar_index: 20, time: 20, price_i64: 20, confirmed_at_bar_index: 20, known_at_bar_index: 20, object_revision: 1 },
      { object_id: 'segment-1', object_type: 'segment', layer_category: 'segments', label: '向上线段', bar_index: 30, time: 30, price_i64: 30, confirmed_at_bar_index: 30, known_at_bar_index: 30, object_revision: 1 },
    ]
    const wrapper = mount(ObjectTreePanel, { props: {
      drawings: [], sources: [], strategySources: [strategy] as never, selectedId: null,
      signalsBySource: { 'chan-filtered': objects as never[] },
    } })

    expect(wrapper.findAll('[data-object-type="ChanSignalObject"]')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('向上笔')
    expect(wrapper.text()).not.toContain('向上线段')

    await wrapper.setProps({ strategySources: [{
      ...strategy,
      category_visibility: { ...strategy.category_visibility, bi_states: true },
    }] as never })
    expect(wrapper.findAll('[data-object-type="ChanSignalObject"]')).toHaveLength(1)
    expect(wrapper.get('.signal-layer-category').text()).toBe('笔状态')

    await wrapper.setProps({ strategySources: [{
      ...strategy,
      category_visibility: { ...strategy.category_visibility, bi: false, segments: true, segment_boundary_confirmations: true },
    }] as never })
    expect(wrapper.findAll('[data-object-type="ChanSignalObject"]')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('向上笔')
    expect(wrapper.text()).toContain('向上线段')
    expect(wrapper.get('.signal-layer-category').text()).toBe('线段状态')
  })

  it('separates pen and segment lines from their status and boundary objects', async () => {
    const visibility = {
      fractals: false, bi: true, bi_states: false, segments: true,
      bi_centers: false, segment_centers: false,
      bi_boundary_confirmations: false, segment_boundary_confirmations: false,
      divergences: false, first_trade_points: false, second_trade_points: false, third_trade_points: false,
    }
    const strategy = {
      source_type: 'StrategySource', source_id: 'split', definition: { name: '缠论' },
      parameters: {}, job_id: 'job-split', status: 'completed', visible: true,
      category_visibility: visibility,
    }
    const base = { bar_index: 20, time: 20, price_i64: 20, confirmed_at_bar_index: 20, known_at_bar_index: 20, object_revision: 1 }
    const objects = [
      { ...base, object_id: 'bi', object_type: 'bi', layer_category: 'bi', label: '向上笔' },
      { ...base, object_id: 'bi-state', layer_category: 'bi_states', label: '笔延伸' },
      { ...base, object_id: 'bi-boundary', layer_category: 'bi_states', label: '笔分界确认线' },
      { ...base, object_id: 'segment', object_type: 'segment', layer_category: 'segments', label: '向下线段' },
      { ...base, object_id: 'segment-boundary', layer_category: 'segment_boundary_confirmations', label: '线段分界确认线' },
    ]
    const wrapper = mount(ObjectTreePanel, { props: {
      drawings: [], sources: [], strategySources: [strategy] as never, selectedId: null,
      signalsBySource: { split: objects as never[] },
    } })
    expect(wrapper.findAll('[data-object-type="ChanSignalObject"]')).toHaveLength(0)
    await wrapper.findAll('.strategy-categories input[type="checkbox"]')[3]?.trigger('change')
    expect(wrapper.emitted('patchStrategy')?.at(-1)).toEqual(['split', {
      category_visibility: { ...visibility, bi_states: true, bi_boundary_confirmations: true },
    }])
    await wrapper.findAll('.strategy-categories input[type="checkbox"]')[5]?.trigger('change')
    expect(wrapper.emitted('patchStrategy')?.at(-1)).toEqual(['split', {
      category_visibility: { ...visibility, segment_boundary_confirmations: true },
    }])
  })

  it('pages large visible layers instead of rendering every object at once', async () => {
    const strategy = {
      source_type: 'StrategySource', source_id: 'chan-many-bars', definition: { name: '标准缠论' },
      parameters: {}, job_id: 'job-many-bars', status: 'completed', visible: true,
      category_visibility: {
        processed_bars: true, fractals: false, bi: false, bi_states: false, segments: false,
        bi_centers: false, segment_centers: false, bi_boundary_confirmations: false, segment_boundary_confirmations: false, movement_states: false, center_monitors: false,
        divergences: false, first_trade_points: false, second_trade_points: false, third_trade_points: false,
      },
    }
    const objects = Array.from({ length: 301 }, (_, index) => ({
      object_id: `bar-${index}`, object_type: 'processed_bar', layer_category: 'processed_bars',
      label: `处理后K线 #${index}`, bar_index: index, time: index, price_i64: index,
      confirmed_at_bar_index: index, known_at_bar_index: index, object_revision: 1,
    }))
    const wrapper = mount(ObjectTreePanel, { props: {
      drawings: [], sources: [], strategySources: [strategy] as never, selectedId: null,
      signalsBySource: { 'chan-many-bars': objects as never[] },
    } })

    expect(wrapper.findAll('[data-object-type="ChanSignalObject"]')).toHaveLength(300)
    expect(wrapper.get('.signal-load-more').text()).toContain('再显示 1 个')
    await wrapper.get('.signal-load-more').trigger('click')
    expect(wrapper.findAll('[data-object-type="ChanSignalObject"]')).toHaveLength(301)
  })

  it('labels auxiliary run objects as non-standard and non-trading', () => {
    const wrapper = mount(ObjectTreePanel, { props: {
      dataset: { time: { timezone: 'Asia/Shanghai' }, price: { price_scale: 1, price_decimals: 0 } } as never,
      drawings: [], sources: [], strategySources: [], selectedId: null,
      strategyRunSources: [{
        source_type: 'StrategyRunSource', source_id: 'run-source-aux', run_id: 'run-aux',
        definition: { algorithm_id: 'aux_ma_kiss_legacy', name: '辅助·均线“吻”旧系统（候选不交易）' },
        status: 'completed', visible: true, objects: [], signals: [],
      }] as never,
    } })
    expect(wrapper.get('.signal-branch-title').text()).toContain('辅助事件（非标准/不交易）')
  })
})
