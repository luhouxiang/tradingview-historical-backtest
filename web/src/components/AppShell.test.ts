import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type { AlgorithmDefinition, DatasetMeta } from '../types/api'
import AppShell from './AppShell.vue'

const api = vi.hoisted(() => ({
  getLayout: vi.fn(), getDrawings: vi.fn(), putLayout: vi.fn(), putDrawings: vi.fn(),
  getStrategySourceConfig: vi.fn(), putStrategySourceConfig: vi.fn(),
  cancelCalculation: vi.fn(), listAlgorithms: vi.fn(), createCalculation: vi.fn(), getCalculation: vi.fn(), getCalculationResults: vi.fn(),
  createReplay: vi.fn(), getReplay: vi.fn(), getReplayEvents: vi.fn(),
  createBacktest: vi.fn(), getBacktest: vi.fn(), getBacktestSummary: vi.fn(), getBacktestTrades: vi.fn(), getBacktestEquity: vi.fn(), getBacktestChartEvents: vi.fn(),
  createStudy: vi.fn(), getStudy: vi.fn(), getStudyEvaluations: vi.fn(),
  getDataset: vi.fn(), getJob: vi.fn(), getSourceFiles: vi.fn(), importSource: vi.fn(),
  listDatasets: vi.fn(), startDatasetScan: vi.fn(),
}))
vi.mock('../api/client', () => ({
  ...api,
  ApiError: class ApiError extends Error { constructor(readonly code: string, message: string) { super(message) } },
}))

const dataset = {
  dataset_id: 'SHFE.AO2609.5m', data_revision: `sha256:${'1'.repeat(64)}`,
  price: { price_scale: 1 }, coverage: { first_bar_index: 0, last_bar_index: 100 },
} as DatasetMeta
const focusSignalMock = vi.fn()

class FakeBroadcastChannel {
  static instances: FakeBroadcastChannel[] = []
  onmessage: ((event: MessageEvent) => void) | null = null
  constructor(readonly name: string) { FakeBroadcastChannel.instances.push(this) }
  postMessage(): void {}
  close(): void {}
}

const ChartStub = defineComponent({
  name: 'ChartGroup',
  props: { dataset: { type: Object, default: null }, strategySources: { type: Array, default: () => [] }, selectedSignal: { type: Object, default: null } },
  setup(_, { expose }) {
    expose({
      snapshotLayout: () => ({ panes: [{ id: 'price', kind: 'price', weight: 6, minHeight: 240, visible: true, collapsed: false, order: 0 }] }),
      restoreLayout: vi.fn(),
      focusSignal: focusSignalMock,
    })
    return () => h('div', 'chart')
  },
})

function indicatorDefinition(algorithmId: string): AlgorithmDefinition {
  return {
    kind: 'indicator', algorithm_id: algorithmId, algorithm_version: '1.0.0', source_hash: `sha256:${'a'.repeat(64)}`,
    name: algorithmId.toUpperCase(), input_schema: 'bars.v1', causal: true,
    parameter_schema: { type: 'object', additionalProperties: false, required: [], properties: {} },
    outputs: [], warmup: { kind: 'formula', expression: '0' },
  }
}

function chanDefinition(): AlgorithmDefinition {
  return {
    kind: 'chan', algorithm_id: 'chan_engineering', algorithm_version: '1.0.0', source_hash: `sha256:${'b'.repeat(64)}`,
    name: 'Engineering Causal Chan', input_schema: 'bars.v1', causal: true,
    parameter_schema: {
      type: 'object', additionalProperties: false, required: ['min_stroke_bars'],
      properties: { min_stroke_bars: { type: 'integer', default: 5 } },
    },
    outputs: [
      { name: 'bi', display_name: '笔', pane: 'main', series_type: 'semantic_objects', object_type: 'bi' },
      { name: 'local_center', display_name: '实体中枢', pane: 'main', series_type: 'semantic_objects', object_type: 'local_center' },
    ],
    warmup: { kind: 'formula', expression: 'full history causal state' },
  }
}

describe('AppShell', () => {
  afterEach(() => vi.unstubAllGlobals())
  it('cancels the previous dataset calculation and ignores its late poll', async () => {
    api.getLayout.mockRejectedValue(new ApiError('WORKSPACE_NOT_FOUND', 'missing', 'req-layout'))
    api.getDrawings.mockRejectedValue(new ApiError('DRAWINGS_NOT_FOUND', 'missing', 'req-drawings'))
    api.listAlgorithms.mockResolvedValue([chanDefinition()])
    api.createCalculation.mockResolvedValueOnce({ job_id: 'old', status: 'running' })
      .mockResolvedValueOnce({ job_id: 'new', status: 'completed' })
    let resolvePoll!: (value: object) => void
    api.getCalculation.mockImplementationOnce(() => new Promise((resolve) => { resolvePoll = resolve }))
    const wrapper = mount(AppShell, { props: { health: 'ok' }, global: { stubs: { ChartGroup: ChartStub, DatasetPanel: true } } })
    const panel = wrapper.findComponent({ name: 'DatasetPanel' })
    panel.vm.$emit('selected', dataset)
    await flushPromises()
    panel.vm.$emit('selected', { ...dataset, dataset_id: 'SHFE.SSL9.5m' })
    await flushPromises()
    expect(api.cancelCalculation).toHaveBeenCalledWith('old')
    resolvePoll({ status: 'failed', error: { message: 'stale failure' } })
    await flushPromises()
    expect(wrapper.findComponent(ChartStub).props('strategySources')).toEqual([
      expect.objectContaining({ job_id: 'new', status: 'completed' }),
    ])
    wrapper.unmount()
  })

  it('cancels an accepted job arriving after its dataset was replaced', async () => {
    api.getLayout.mockRejectedValue(new ApiError('WORKSPACE_NOT_FOUND', 'missing', 'req-layout'))
    api.getDrawings.mockRejectedValue(new ApiError('DRAWINGS_NOT_FOUND', 'missing', 'req-drawings'))
    api.listAlgorithms.mockResolvedValue([chanDefinition()])
    let acceptOld!: (value: object) => void
    api.createCalculation.mockImplementationOnce(() => new Promise((resolve) => { acceptOld = resolve }))
      .mockResolvedValueOnce({ job_id: 'new', status: 'completed' })
    const wrapper = mount(AppShell, { props: { health: 'ok' }, global: { stubs: { ChartGroup: ChartStub, DatasetPanel: true } } })
    const panel = wrapper.findComponent({ name: 'DatasetPanel' })
    panel.vm.$emit('selected', dataset)
    await flushPromises()
    panel.vm.$emit('selected', { ...dataset, dataset_id: 'SHFE.SSL9.5m' })
    await flushPromises()
    acceptOld({ job_id: 'late', status: 'queued' })
    await flushPromises()
    expect(api.cancelCalculation).toHaveBeenCalledWith('late')
    expect(wrapper.findComponent(ChartStub).props('strategySources')).toEqual([
      expect.objectContaining({ job_id: 'new', status: 'completed' }),
    ])
    wrapper.unmount()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    api.cancelCalculation.mockResolvedValue({ status: 'cancelled' })
    api.getSourceFiles.mockResolvedValue([])
    api.listDatasets.mockResolvedValue({ catalog_revision: 0, datasets: [] })
    api.getStrategySourceConfig.mockRejectedValue(new ApiError('WORKSPACE_NOT_FOUND', 'missing', 'req-strategy-config'))
    api.putStrategySourceConfig.mockImplementation(async (_profile: string, _revision: number, value: object) => ({ ...value, revision: 1 }))
    api.getCalculationResults.mockResolvedValue({
      result_kind: 'chan', objects: { processed_bars: [], fractals: [], bi: [], bi_states: [], segments: [], local_centers: [], center_connections: [], center_audit_events: [], level_centers: [], level_movements: [], movement_states: [], center_monitors: [], divergences: [], trade_points: [] },
      coverage: { first_bar_index: 0, last_bar_index: 100, returned_count: 101 },
    })
  })
  it('renders the empty TradingView-style workspace', () => {
    const wrapper = mount(AppShell, {
      props: { health: 'ok' },
      global: {
        stubs: {
          ChartGroup: { template: '<div>选择历史数据集后开始</div>' },
          DatasetPanel: { template: '<div />' },
        },
      },
    })
    expect(wrapper.get('[aria-label="图表工作区"]').text()).toContain('选择历史数据集后开始')
    expect(wrapper.get('[aria-label="绘图工具栏"]').element).toBeTruthy()
    expect(wrapper.get('[aria-label="右侧面板"]').element).toBeTruthy()
    expect(wrapper.get('[aria-label="底部面板"]').element).toBeTruthy()
  })

  it('shows both incoming and outgoing local-center connection IDs in the object tree', async () => {
    api.getLayout.mockRejectedValue(new ApiError('WORKSPACE_NOT_FOUND', 'missing', 'req-layout'))
    api.getDrawings.mockRejectedValue(new ApiError('DRAWINGS_NOT_FOUND', 'missing', 'req-drawings'))
    api.listAlgorithms.mockResolvedValue([chanDefinition()])
    api.createCalculation.mockResolvedValue({ job_id: 'center-audit', status: 'completed' })
    const result = await api.getCalculationResults()
    api.getCalculationResults.mockResolvedValue({
      ...result,
      objects: {
        ...result.objects,
        local_centers: [{
          object_id: 'center-current', unit_kind: 'BI', structural_level: 'stroke',
          seed_ids: ['unit-1', 'unit-2', 'unit-3'], scan_floor: 10,
          zd_i64: 105, zg_i64: 115, status: 'ACTIVE',
          observed_end_bar_index: 20, observed_end_time: 1700000000000,
          break_confirmed_at_bar_index: null, known_at_bar_index: 20, object_revision: 1,
          entry_id: null, exit_id: null, pending_exit_id: null, first_retest_id: null,
          rule_version: 'local_center_boundary_v1', left_context_incomplete: false,
          previous_center_id: 'center-before', core_relation: 'CORE_ABOVE',
          higher_level_review_required: true, trend_status: 'UNVERIFIED',
        }],
        center_connections: [
          { object_id: 'connection-in', from_center_id: 'center-before', to_center_id: 'center-current' },
          { object_id: 'connection-out', from_center_id: 'center-current', to_center_id: null },
        ],
        center_audit_events: [{
          object_id: 'preview-current', event_type: 'PREVIEW_UPDATED', center_id: 'center-current',
          preview_state: 'RETEST_TOUCH', preview_confirmed: false, comparison_i64: 115,
          known_at_bar_index: 21, unit_ids: ['unit-exit', 'unit-tail'],
        }],
      },
    })
    const wrapper = mount(AppShell, {
      props: { health: 'ok' }, global: { stubs: { ChartGroup: ChartStub, DatasetPanel: true } },
    })
    wrapper.findComponent({ name: 'DatasetPanel' }).vm.$emit('selected', dataset)
    await flushPromises()
    await wrapper.get('.right-dock nav').findAll('button').at(3)?.trigger('click')
    const bySource = wrapper.findComponent({ name: 'ObjectTreePanel' }).props('signalsBySource') as Record<string, Array<{ hover_detail?: string }>>
    const detail = Object.values(bySource).flat().find((signal) => signal.hover_detail?.includes('center-current'))?.hover_detail
    expect(detail).toContain('前向连接：connection-in（来源中枢：center-before）')
    expect(detail).toContain('后向连接：connection-out（目标中枢：尚未形成后中枢）')
    expect(detail).toContain('构成三单元：unit-1 → unit-2 → unit-3')
    expect(detail).toContain('扫描起点：单元索引 10')
    expect(detail).toContain('核心关系：核心上移（前中枢：center-before）')
    expect(detail).toContain('趋势：未验证 · 外围波动接触，需高级别递归检查')
    expect(detail).toContain('触边，候选即时失效 · 比较值 115 · K21 · unit-exit → unit-tail（不可交易）')
    wrapper.unmount()
  })

  it('opens a revision-bound backtest page and focuses its double-clicked trade', async () => {
    FakeBroadcastChannel.instances = []
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
    vi.spyOn(window, 'focus').mockImplementation(() => undefined)
    const popupFocus = vi.fn()
    const open = vi.spyOn(window, 'open').mockReturnValue({ focus: popupFocus } as unknown as Window)
    api.getLayout.mockRejectedValue(new ApiError('WORKSPACE_NOT_FOUND', 'missing', 'req-layout'))
    api.getDrawings.mockRejectedValue(new ApiError('DRAWINGS_NOT_FOUND', 'missing', 'req-drawings'))
    api.listAlgorithms.mockResolvedValue([])
    const wrapper = mount(AppShell, {
      props: { health: 'ok' }, global: { stubs: { ChartGroup: ChartStub, DatasetPanel: true } },
    })
    wrapper.findComponent({ name: 'DatasetPanel' }).vm.$emit('selected', dataset)
    await flushPromises()

    await wrapper.get('[aria-label="打开独立回测工作区"]').trigger('click')
    const opened = new URL(String(open.mock.calls[0]?.[0]))
    expect(opened.pathname).toBe('/backtest')
    expect(opened.searchParams.get('dataset_id')).toBe(dataset.dataset_id)
    expect(opened.searchParams.get('revision')).toBe(dataset.data_revision)
    expect(popupFocus).toHaveBeenCalled()

    FakeBroadcastChannel.instances[0]?.onmessage?.({ data: {
      type: 'focus-trade', dataset_id: dataset.dataset_id, data_revision: dataset.data_revision, leg: 'exit',
      trade: {
        trade_id: 'trade-linked', side: 'long', entry_bar_index: 42, entry_time: 1_700_000_000_000,
        entry_price_i64: 2660, entry_signal_id: 'signal-entry', entry_signal_known_at_bar_index: 41,
        entry_order_id: 'order-entry', exit_bar_index: 50, exit_time: 1_700_003_000_000,
        exit_price_i64: 2690, quantity: 1, gross_pnl_i64: 600, net_pnl_i64: 500,
        commission_i64: 60, slippage_i64: 40,
      },
    } } as MessageEvent)
    await flushPromises()
    expect(focusSignalMock).toHaveBeenCalledWith(expect.objectContaining({
      object_id: 'trade-linked:exit', bar_index: 50, price_i64: 2690, label: '卖出',
    }))
    expect(wrapper.findComponent(ChartStub).props('selectedSignal')).toMatchObject({ object_id: 'trade-linked:exit' })
    wrapper.unmount()
    open.mockRestore()
  })

  it('compresses the chart when dock panels are expanded and keeps panel sizes bounded', async () => {
    const wrapper = mount(AppShell, {
      props: { health: 'ok' },
      global: { stubs: { ChartGroup: { template: '<div />' }, DatasetPanel: { template: '<div />' } } },
    })
    expect(wrapper.get('.workspace-body').attributes('style')).toContain('320px')
    expect(wrapper.get('.workspace-body').attributes('style')).toContain('1px')
    await wrapper.get('.dock-close').trigger('click')
    expect(wrapper.find('[aria-label="右侧面板"]').exists()).toBe(false)
    await wrapper.get('.reopen-right').trigger('click')
    expect(wrapper.find('[aria-label="右侧面板"]').exists()).toBe(true)
    const toggle = wrapper.findAll('.bottom-dock nav button').at(4)
    await toggle?.trigger('click')
    expect(wrapper.get('.app-shell').attributes('style')).toContain('260px')
    expect(wrapper.get('.bottom-dock').classes()).toContain('expanded')
  })

  it('creates the Python MA20, MA60 and MACD defaults for a new workspace', async () => {
    api.getLayout.mockRejectedValue(new ApiError('WORKSPACE_NOT_FOUND', 'missing', 'req-layout'))
    api.getDrawings.mockRejectedValue(new ApiError('DRAWINGS_NOT_FOUND', 'missing', 'req-drawings'))
    api.listAlgorithms.mockResolvedValue([indicatorDefinition('ma'), indicatorDefinition('macd')])
    api.createCalculation.mockImplementation(async (_request: object) => ({ job_id: `job-${api.createCalculation.mock.calls.length}`, status: 'completed' }))
    const wrapper = mount(AppShell, {
      props: { health: 'ok' },
      global: { stubs: { ChartGroup: ChartStub, DatasetPanel: true } },
    })
    wrapper.findComponent({ name: 'DatasetPanel' }).vm.$emit('selected', dataset, 'automatic')
    await flushPromises()
    expect(api.createCalculation.mock.calls.map((call) => call[0])).toEqual([
      expect.objectContaining({ parameters: { period: 20, source: 'close' }, calculation_mode: 'full_history' }),
      expect.objectContaining({ parameters: { period: 60, source: 'close' }, calculation_mode: 'full_history' }),
      expect.objectContaining({ parameters: { fast_period: 12, slow_period: 26, signal_period: 9, source: 'close' }, calculation_mode: 'full_history' }),
    ])
  })

  it('creates one default Chan overlay showing bi and local centers on a new workspace', async () => {
    api.getLayout.mockRejectedValue(new ApiError('WORKSPACE_NOT_FOUND', 'missing', 'req-layout'))
    api.getDrawings.mockRejectedValue(new ApiError('DRAWINGS_NOT_FOUND', 'missing', 'req-drawings'))
    api.listAlgorithms.mockResolvedValue([chanDefinition()])
    api.createCalculation.mockResolvedValue({ job_id: 'job-chan-default', status: 'completed' })
    const wrapper = mount(AppShell, {
      props: { health: 'ok' },
      global: { stubs: { ChartGroup: ChartStub, DatasetPanel: true } },
    })
    wrapper.findComponent({ name: 'DatasetPanel' }).vm.$emit('selected', dataset)
    await flushPromises()
    expect(api.createCalculation).toHaveBeenCalledWith(expect.objectContaining({
      calculation_mode: 'causal_events', parameters: { min_stroke_bars: 5 },
    }))
    const chart = wrapper.findComponent(ChartStub)
    expect(chart.props('strategySources')).toEqual([
      expect.objectContaining({
        source_type: 'StrategySource', visible: true,
        category_visibility: { processed_bars: false, fractals: false, bi: true, bi_states: true, segments: true, local_centers: true, level_centers: false, level_movements: true, movement_states: true, center_monitors: true, divergences: true, trade_points: true },
      }),
    ])
  })

  it('selects a signal row without moving the chart and locates only from its lock', async () => {
    api.getLayout.mockRejectedValue(new ApiError('WORKSPACE_NOT_FOUND', 'missing', 'req-layout'))
    api.getDrawings.mockRejectedValue(new ApiError('DRAWINGS_NOT_FOUND', 'missing', 'req-drawings'))
    api.listAlgorithms.mockResolvedValue([chanDefinition()])
    api.createCalculation.mockResolvedValue({ job_id: 'job-chan-signals', status: 'completed' })
    const signal = {
      object_id: 'buy-1', bar_index: 80, time: 1_700_000_000_000, price_i64: 2650,
      signal_type: 'buy_1', divergence_kind: null, signal_class: 'standard', strength: null,
      reference_object_id: null, macd_area_reference: null, macd_area_current: null,
      confirmed: true, confirmed_at_bar_index: 81, known_at_bar_index: 81, object_revision: 1,
    }
    const monitor = {
      object_id: 'monitor-3', bar_index: 70, time: 1_699_999_900_000,
      z_i64: 2600, zn_i64: 2620, z_twice_i64: 5201, zn_twice_i64: 5241,
      core_low_i64: 2550, core_high_i64: 2651, range_high_i64: 2670, range_low_i64: 2571,
      component_ordinal: 3, component_direction: 'up' as const, relative_position: 'above' as const,
      oscillation_bias: 'strong' as const, breakout_warning: 'rising_wedge_below_b' as const,
      catalog_algorithm_id: 'ALG-AUX-004' as const, semantic_namespace: 'auxiliary' as const,
      evidence_level: 'AUXILIARY' as const, level_mapping_profile: 'segment_center_components_v1' as const,
      standard_signal: false as const, execution_allowed: false as const, confirms_third_point: false as const,
      analysis_level: 'segment', reference_object_id: 'center-1', confirmed: true,
      confirmed_at_bar_index: 71, known_at_bar_index: 71, object_revision: 1,
    }
    api.getCalculationResults.mockResolvedValue({
      result_kind: 'chan', objects: { processed_bars: [], fractals: [], bi: [], bi_states: [], segments: [], local_centers: [], center_connections: [], center_audit_events: [], level_centers: [], level_movements: [], movement_states: [], center_monitors: [monitor], divergences: [], trade_points: [signal] },
      coverage: { first_bar_index: 0, last_bar_index: 100, returned_count: 2 },
    })
    const wrapper = mount(AppShell, {
      props: { health: 'ok' }, global: { stubs: { ChartGroup: ChartStub, DatasetPanel: true } },
    })
    wrapper.findComponent({ name: 'DatasetPanel' }).vm.$emit('selected', dataset)
    await flushPromises()
    await wrapper.findAll('.right-dock nav button')[3]?.trigger('click')
    expect(wrapper.text()).toContain('Zn3 强')
    expect(wrapper.text()).toContain('抬高未破 B·上升楔形预警·不确认三类点')
    const row = wrapper.get('[data-object-type="ChanSignalObject"]')
    expect(row.text()).toContain('一买')
    await row.trigger('click')
    expect(focusSignalMock).not.toHaveBeenCalled()
    expect(wrapper.findComponent(ChartStub).props('selectedSignal')).toEqual(expect.objectContaining({ object_id: 'buy-1' }))
    await row.get('.signal-object-lock').trigger('click')
    expect(focusSignalMock).toHaveBeenCalledWith(expect.objectContaining({ object_id: 'buy-1' }))
  })

  it('recreates defaults when a saved indicator algorithm revision is no longer published', async () => {
    api.getLayout.mockResolvedValue({
      schema_version: 1, profile_id: 'default', layout_id: 'default-three-pane', revision: 1,
      panes: [{ id: 'price', role: 'price', weight: 6, min_height: 240, visible: true, collapsed: false, order: 0 }],
      right_panel: { width: 320, collapsed: false, active_tab: 'dataset' },
      bottom_panel: { height: 260, collapsed: true, active_tab: 'replay' }, object_order: [], strategy_sources: [], updated_at: '2026-08-01T00:00:00Z',
      series_sources: [{
        source_id: 'series-default-macd-12-26-9', dataset_id: dataset.dataset_id, data_revision: dataset.data_revision,
        algorithm: { kind: 'indicator', algorithm_id: 'macd', algorithm_version: '1.0.0', source_hash: `sha256:${'f'.repeat(64)}` },
        parameters: { fast_period: 12, slow_period: 26, signal_period: 9, source: 'close' }, visible: true, order: 0,
      }],
    })
    api.getDrawings.mockRejectedValue(new ApiError('DRAWINGS_NOT_FOUND', 'missing', 'req-drawings'))
    api.listAlgorithms.mockResolvedValue([indicatorDefinition('ma'), { ...indicatorDefinition('macd'), algorithm_version: '1.1.0' }])
    api.createCalculation.mockImplementation(async () => ({ job_id: `job-${api.createCalculation.mock.calls.length}`, status: 'completed' }))
    const wrapper = mount(AppShell, {
      props: { health: 'ok' },
      global: { stubs: { ChartGroup: ChartStub, DatasetPanel: true } },
    })
    wrapper.findComponent({ name: 'DatasetPanel' }).vm.$emit('selected', dataset)
    await flushPromises()
    expect(api.createCalculation).toHaveBeenCalledTimes(3)
  })

  it('restores and saves layout, drawings, panels and fixed anchors with optimistic revisions', async () => {
    const drawing = {
      id: 'drawing-1', name: '矩形 1', type: 'rectangle', pane_id: 'main', visible: true, locked: false,
      z_band: 600, order_in_band: 0, style: { color: '#2962ff', line_width: 1, fill_opacity: .15 },
      anchors: [{ time: 1000, price_i64: 20, price_scale: 10 }, { time: 2000, price_i64: 30, price_scale: 10 }],
      revision: 1, created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z',
    }
    api.getLayout.mockResolvedValue({
      schema_version: 1, profile_id: 'default', layout_id: 'default-three-pane', revision: 3,
      panes: [{ id: 'price', role: 'price', weight: 6, min_height: 240, visible: true, collapsed: false, order: 0 }],
      right_panel: { width: 400, collapsed: false, active_tab: 'object_tree' },
      bottom_panel: { height: 300, collapsed: true, active_tab: 'tasks' }, object_order: [], series_sources: [], strategy_sources: [], updated_at: '2026-08-01T00:00:00Z',
    })
    api.getDrawings.mockResolvedValue({
      schema_version: 1, profile_id: 'default', layout_id: 'default-three-pane', dataset_id: dataset.dataset_id,
      data_revision: dataset.data_revision, revision: 4, drawings: [drawing], updated_at: '2026-08-01T00:00:00Z',
    })
    api.listAlgorithms.mockResolvedValue([])
    api.putLayout.mockImplementation(async (_profile: string, _layout: string, _revision: number, value: object) => ({ ...value, revision: 4 }))
    api.putDrawings.mockImplementation(async (_profile: string, _layout: string, _dataset: string, _revision: number, value: object) => ({ ...value, revision: 5 }))
    const wrapper = mount(AppShell, {
      props: { health: 'ok' },
      global: { stubs: { ChartGroup: ChartStub, DatasetPanel: true } },
    })
    wrapper.findComponent({ name: 'DatasetPanel' }).vm.$emit('selected', dataset, 'automatic')
    await flushPromises()
    expect(wrapper.get('.workspace-body').attributes('style')).toContain('400px')
    expect(wrapper.findAll('[data-object-type="DrawingObject"]')).toHaveLength(1)
    await wrapper.get('[title="保存工作区"]').trigger('click')
    await flushPromises()
    expect(api.putLayout).toHaveBeenCalledWith('default', 'default-three-pane', 3, expect.any(Object))
    expect(api.putDrawings).toHaveBeenCalledWith('default', 'default-three-pane', dataset.dataset_id, 4, expect.objectContaining({ drawings: [drawing] }))
    const saved = api.putDrawings.mock.calls[0]?.[4]
    expect(JSON.stringify(saved)).not.toContain('"x"')
    expect(JSON.stringify(saved)).not.toContain('"y"')
  })

  it('keeps the datasets tab open when a user switches datasets', async () => {
    api.getLayout.mockResolvedValue({
      schema_version: 1, profile_id: 'default', layout_id: 'default-three-pane', revision: 3,
      panes: [{ id: 'price', role: 'price', weight: 6, min_height: 240, visible: true, collapsed: false, order: 0 }],
      right_panel: { width: 320, collapsed: false, active_tab: 'object_tree' },
      bottom_panel: { height: 260, collapsed: true, active_tab: 'replay' }, object_order: [], series_sources: [], strategy_sources: [], updated_at: '2026-08-01T00:00:00Z',
    })
    api.getDrawings.mockRejectedValue(new ApiError('DRAWINGS_NOT_FOUND', 'missing', 'req-drawings'))
    api.listAlgorithms.mockResolvedValue([])
    const wrapper = mount(AppShell, {
      props: { health: 'ok' }, global: { stubs: { ChartGroup: ChartStub, DatasetPanel: true } },
    })

    wrapper.findComponent({ name: 'DatasetPanel' }).vm.$emit('selected', dataset, 'automatic')
    await flushPromises()
    expect(wrapper.findAll('.right-dock nav button')[3]?.classes()).toContain('active')

    await wrapper.findAll('.right-dock nav button')[0]?.trigger('click')
    wrapper.findComponent({ name: 'DatasetPanel' }).vm.$emit('selected', dataset, 'user')
    await flushPromises()

    expect(wrapper.findAll('.right-dock nav button')[0]?.classes()).toContain('active')
    expect(wrapper.findComponent({ name: 'DatasetPanel' }).exists()).toBe(true)
  })

  it('automatically persists StrategySource category visibility for the next startup', async () => {
    vi.useFakeTimers()
    try {
      api.getLayout.mockResolvedValue({
        schema_version: 1, profile_id: 'default', layout_id: 'default-three-pane', revision: 3,
        panes: [{ id: 'price', role: 'price', weight: 6, min_height: 240, visible: true, collapsed: false, order: 0 }],
        right_panel: { width: 320, collapsed: false, active_tab: 'object_tree' },
        bottom_panel: { height: 260, collapsed: true, active_tab: 'replay' }, object_order: [], series_sources: [], updated_at: '2026-08-01T00:00:00Z',
        strategy_sources: [{
          source_id: 'strategy-default-chan', name: 'Engineering Causal Chan', pane_id: 'price',
          visible: true, locked: true, z_band: 500, order_in_band: 0,
          dataset_id: dataset.dataset_id, data_revision: dataset.data_revision,
          algorithm: { kind: 'chan', algorithm_id: 'chan_engineering', algorithm_version: '1.0.0', source_hash: `sha256:${'b'.repeat(64)}` },
          parameters: { min_stroke_bars: 5 },
          category_visibility: { fractals: false, bi: true, segments: true, local_centers: true, movement_states: true, center_monitors: true, divergences: true, trade_points: true },
        }],
      })
      api.getDrawings.mockRejectedValue(new ApiError('DRAWINGS_NOT_FOUND', 'missing', 'req-drawings'))
      api.getStrategySourceConfig.mockResolvedValue({
        schema_version: 1, profile_id: 'default', revision: 7, updated_at: '2026-08-01T00:00:00Z',
        strategy_sources: [{
          dataset_id: dataset.dataset_id, data_revision: dataset.data_revision, source_id: 'strategy-default-chan', visible: true,
          category_visibility: { processed_bars: false, fractals: false, bi: false, bi_states: true, segments: true, local_centers: true, level_centers: false, level_movements: true, movement_states: true, center_monitors: true, divergences: true, trade_points: true },
        }],
      })
      api.listAlgorithms.mockResolvedValue([chanDefinition()])
      api.createCalculation.mockResolvedValue({ job_id: 'job-chan-restored', status: 'completed' })
      api.putStrategySourceConfig.mockImplementation(async (_profile: string, _revision: number, value: object) => ({ ...value, revision: 8 }))
      api.putLayout.mockImplementation(async (_profile: string, _layout: string, _revision: number, value: object) => ({ ...value, revision: 4 }))
      api.putDrawings.mockImplementation(async (_profile: string, _layout: string, _dataset: string, _revision: number, value: object) => ({ ...value, revision: 1 }))
      const wrapper = mount(AppShell, {
        props: { health: 'ok' }, global: { stubs: { ChartGroup: ChartStub, DatasetPanel: true } },
      })

      wrapper.findComponent({ name: 'DatasetPanel' }).vm.$emit('selected', dataset, 'automatic')
      await flushPromises()
      const categoryToggles = wrapper.findAll('.strategy-categories input[type="checkbox"]')
      expect(categoryToggles).toHaveLength(12)
      expect((categoryToggles[2]?.element as HTMLInputElement).checked).toBe(false)
      await categoryToggles[1]?.trigger('change')
      expect(api.putStrategySourceConfig).not.toHaveBeenCalled()

      await vi.advanceTimersByTimeAsync(300)
      await flushPromises()
      expect(api.putStrategySourceConfig).toHaveBeenCalledTimes(1)
      expect(api.putStrategySourceConfig).toHaveBeenCalledWith('default', 7, expect.objectContaining({
        strategy_sources: [expect.objectContaining({
          source_id: 'strategy-default-chan',
          category_visibility: expect.objectContaining({ bi: false, fractals: true, trade_points: true }),
        })],
      }))
      expect(api.putLayout).not.toHaveBeenCalled()
      expect(api.putDrawings).not.toHaveBeenCalled()
      expect(wrapper.text()).toContain('策略配置已自动保存 revision 8')

      await wrapper.get('[title="保存工作区"]').trigger('click')
      await flushPromises()
      expect(api.putLayout).toHaveBeenCalledWith('default', 'default-three-pane', 3, expect.objectContaining({
        strategy_sources: [expect.objectContaining({
          category_visibility: expect.objectContaining({ bi: true, fractals: false }),
        })],
      }))
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })
})
