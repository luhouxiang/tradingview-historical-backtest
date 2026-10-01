import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChanCalculationResults, ChanLocalCenter, DatasetMeta, SeriesSource, StrategySource } from '../types/api'
import { ChanPrimitive } from '../chart/chanPrimitive'
import ChartGroup from './ChartGroup.vue'

const chartMocks = vi.hoisted(() => {
  const candle = {
    setData: vi.fn(), applyOptions: vi.fn(),
    attachPrimitive: vi.fn(), detachPrimitive: vi.fn(),
    priceToCoordinate: vi.fn((price: number) => price * 10),
    coordinateToPrice: vi.fn((coordinate: number) => coordinate / 10),
  }
  const macdScale = { applyOptions: vi.fn() }
  const volumeScale = { applyOptions: vi.fn() }
  const macd = { setData: vi.fn(), applyOptions: vi.fn(), priceScale: vi.fn(() => macdScale), createPriceLine: vi.fn() }
  const volume = { setData: vi.fn(), applyOptions: vi.fn(), priceScale: vi.fn(() => volumeScale), createPriceLine: vi.fn() }
  const pane = () => ({ setStretchFactor: vi.fn() })
  const panes = [pane(), pane(), pane()]
  const timeScale = {
    subscribeVisibleLogicalRangeChange: vi.fn(), unsubscribeVisibleLogicalRangeChange: vi.fn(),
    fitContent: vi.fn(), getVisibleLogicalRange: vi.fn(), setVisibleLogicalRange: vi.fn(),
    timeToCoordinate: vi.fn((time: number) => time - 1_700_000_000),
    timeToIndex: vi.fn(() => null as number | null),
    coordinateToTime: vi.fn((coordinate: number) => 1_700_000_000 + coordinate),
  }
  const chart = {
    addSeries: vi.fn((_definition: unknown, _options: unknown, index: number) => [candle, macd, volume][index]),
    addCustomSeries: vi.fn((_definition: unknown, _options: unknown, index: number) => index === 2 ? volume : macd),
    panes: vi.fn(() => panes), timeScale: vi.fn(() => timeScale), removeSeries: vi.fn(), swapPanes: vi.fn(), remove: vi.fn(),
    subscribeCrosshairMove: vi.fn(), unsubscribeCrosshairMove: vi.fn(), subscribeClick: vi.fn(), unsubscribeClick: vi.fn(),
  }
  return { candle, macd, volume, macdScale, volumeScale, panes, timeScale, chart, createChart: vi.fn(() => chart) }
})

const apiMocks = vi.hoisted(() => ({ getBars: vi.fn(), getCalculationResults: vi.fn(), createCalculation: vi.fn() }))

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: { type: 'candlestick' }, HistogramSeries: { type: 'histogram' }, LineSeries: { type: 'line' },
  ColorType: { Solid: 'solid' }, CrosshairMode: { Normal: 0 }, LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 }, createChart: chartMocks.createChart,
}))
vi.mock('../api/client', () => apiMocks)

const revision = `sha256:${'a'.repeat(64)}`

function dataset(): DatasetMeta {
  return {
    request_id: 'req', dataset_id: 'SHFE.AO2609.5m', data_revision: revision,
    instrument: { exchange: 'SHFE', symbol: 'AO2609', product: 'AO', contract_multiplier: 20 }, timeframe: '5m',
    source: { path: 'history/sample.txt', encoding: 'GB18030', format: 'tdx_txt_v1' },
    time: { timezone: 'Asia/Shanghai', date_semantics: 'trading_day' }, price: { price_decimals: 0, price_scale: 1 },
    coverage: { bar_count: 2, first_bar_index: 0, last_bar_index: 1, first_timestamp_utc: 1_700_000_000_000, last_timestamp_utc: 1_700_000_300_000, first_trading_day: '2025-01-01', last_trading_day: '2025-01-01' },
    quality: {},
  }
}

function rangeSources(): { indicatorSources: SeriesSource[]; strategySources: StrategySource[] } {
  const base = {
    algorithm_version: '18.0.0', source_hash: `sha256:${'c'.repeat(64)}`, input_schema: 'bars.v1', causal: true,
    parameter_schema: { type: 'object', additionalProperties: false, required: [], properties: {} },
    warmup: { kind: 'formula', expression: '0' },
  }
  return {
    indicatorSources: [{
      source_type: 'SeriesSource', source_id: 'macd', job_id: 'job-macd', status: 'completed', parameters: {},
      definition: { ...base, kind: 'indicator', algorithm_id: 'macd', name: 'MACD',
        outputs: [{ name: 'histogram', display_name: 'MACD', pane: 'indicator', series_type: 'histogram' }] },
    }],
    strategySources: [{
      source_type: 'StrategySource', source_id: 'chan', job_id: 'job-chan', status: 'completed', parameters: {}, visible: true,
      category_visibility: { processed_bars: false, fractals: false, bi: false, bi_states: false,
        segments: true, bi_centers: false, segment_centers: false, bi_boundary_confirmations: false, segment_boundary_confirmations: false, divergences: false, first_trade_points: false, second_trade_points: false, third_trade_points: false },
      definition: { ...base, kind: 'chan', algorithm_id: 'chan_engineering', name: '缠论',
        outputs: [{ name: 'segments', display_name: '线段', pane: 'main', series_type: 'semantic_objects', object_type: 'segment' }] },
    }],
  } as { indicatorSources: SeriesSource[]; strategySources: StrategySource[] }
}

function rangeResult(job: string, from: number, to: number) {
  if (to - from + 1 > 5000) throw new Error('INVALID_RANGE')
  if (job === 'job-macd') {
    const indices = Array.from({ length: to - from + 1 }, (_, i) => from + i)
    return { result_kind: 'indicator', bar_index: indices, values: { histogram: indices.map((i) => i + 1) } }
  }
  return { result_kind: 'chan', objects: {
    processed_bars: [], fractals: [], bi: [], bi_states: [], segments: [{ object_id: `segment-${from}`, object_revision: 1 }],
    local_centers: [], center_connections: [], center_audit_events: [],
    movement_states: [{ object_id: 'state-spanning-pages', object_revision: 1 }], center_monitors: [], divergences: [], trade_points: [],
  } }
}

describe('ChartGroup', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getBars.mockImplementation(async (_dataset: string, _revision: string, generation: string) => ({
      request_id: 'req', dataset_id: 'SHFE.AO2609.5m', data_revision: revision, generation_id: generation,
      price_scale: 1, coverage: { first_bar_index: 0, last_bar_index: 1 }, has_more_before: false, has_more_after: false,
      checksum: `sha256:${'b'.repeat(64)}`,
      bars: { bar_index: [0, 1], timestamp_utc: [1_700_000_000_000, 1_700_000_300_000], open_i64: [10, 11], high_i64: [12, 13], low_i64: [9, 10], close_i64: [11, 10], volume: [3, 4], open_interest: [null, 5] },
    }))
  })

  it('emits a clicked class trade marker for the shared object-tree selection path', () => {
    const signal = { object_id: 'class-buy-1', signal_type: 'class_buy_1' } as never
    const hit = vi.spyOn(ChanPrimitive.prototype, 'signalForHit').mockReturnValue(signal)
    const wrapper = mount(ChartGroup, { props: { dataset: null } })
    const onClick = chartMocks.chart.subscribeClick.mock.calls.at(-1)?.[0] as ((value: object) => void) | undefined
    expect(onClick).toBeTypeOf('function')
    onClick?.({ hoveredObjectId: 'trade-point:class-buy-1' })
    expect(wrapper.emitted('select:signal')?.at(-1)).toEqual([signal])
    wrapper.unmount()
    hit.mockRestore()
  })

  it('highlights the reference and current segments of a selected divergence independently of the segment layer', async () => {
    const reference = { object_id: 'segment-reference', start_bar_index: 0, end_bar_index: 1,
      start_time: 1_700_000_000_000, end_time: 1_700_000_300_000, start_price_i64: 11, end_price_i64: 13 } as never
    const current = { object_id: 'segment-current', start_bar_index: 1, end_bar_index: 2,
      start_time: 1_700_000_300_000, end_time: 1_700_000_600_000, start_price_i64: 12, end_price_i64: 10 } as never
    const signal = { object_id: 'divergence-1', object_type: 'divergence', bar_index: 1,
      time: 1_700_000_300_000, price_i64: 12, confirmed_at_bar_index: 2, known_at_bar_index: 2,
      object_revision: 1, label: '盘整顶背驰', signal: {
        comparison_reference_object_id: 'segment-reference', comparison_current_object_id: 'segment-current',
      } } as never
    const wrapper = mount(ChartGroup, { props: { dataset: dataset(), selectedSignal: signal,
      selectedDivergenceSegments: { reference, current } } })
    await flushPromises()
    expect(wrapper.findAll('[data-divergence-segment]')).toHaveLength(2)
    expect(wrapper.get('[data-divergence-segment="reference"]').attributes('data-segment-id')).toBe('segment-reference')
    expect(wrapper.get('[data-divergence-segment="current"]').text()).toContain('背驰段 c K1–K2')
    const a = { object_id: 'segment-a', start_bar_index: -1, end_bar_index: 0,
      start_time: 1_699_999_700_000, end_time: 1_700_000_000_000,
      start_price_i64: 10, end_price_i64: 11 } as never
    const trendSignal = { object_id: 'trend-divergence', object_type: 'divergence', bar_index: 1,
      time: 1_700_000_300_000, price_i64: 12, confirmed_at_bar_index: 2, known_at_bar_index: 2,
      object_revision: 1, label: '趋势顶背驰候选', signal: {
        divergence_kind: 'trend', a_object_id: 'segment-a',
        comparison_reference_object_id: 'segment-reference', comparison_current_object_id: 'segment-current',
      } } as never
    await wrapper.setProps({ selectedSignal: trendSignal,
      selectedDivergenceSegments: { a, reference, current } })
    expect(wrapper.findAll('[data-divergence-segment]')).toHaveLength(3)
    expect(wrapper.get('[data-divergence-segment="a"]').attributes('data-segment-id')).toBe('segment-a')
    expect(wrapper.get('[data-divergence-segment="a"]').text()).toContain('起始段 a')
    await wrapper.setProps({ selectedSignal: null })
    expect(wrapper.findAll('[data-divergence-segment]')).toHaveLength(0)
    wrapper.unmount()
  })

  it('uses one chart instance with price, MACD placeholder, and custom volume panes at 6:1:1', () => {
    const wrapper = mount(ChartGroup, { props: { dataset: null } })
    expect(chartMocks.createChart).toHaveBeenCalledTimes(1)
    expect(chartMocks.createChart).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({
      timeScale: expect.objectContaining({ minBarSpacing: .1 }),
    }))
    expect(chartMocks.chart.addSeries.mock.calls.map((call) => call[2])).toEqual([0, 1])
    expect(chartMocks.chart.addCustomSeries).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ priceFormat: { type: 'volume' } }), 2)
    expect(wrapper.findAll('.pane-control').map((item) => item.attributes('data-weight'))).toEqual(['6', '1', '1'])
    expect(wrapper.findAll('.pane-splitter')).toHaveLength(2)
    expect(wrapper.findAll('.pane-splitter')[0]?.attributes('style')).toContain('top: 600px')
    expect(wrapper.find('[data-pane-id="macd"]').attributes('style')).toContain('top: 605px')
    expect(wrapper.find('[data-pane-id="price"] button:last-child').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('requests the 3000-bar tail and renders fixed-point prices and volume', async () => {
    const wrapper = mount(ChartGroup, { props: { dataset: dataset() } })
    await flushPromises()
    expect(apiMocks.getBars).toHaveBeenCalledWith('SHFE.AO2609.5m', revision, expect.stringMatching(/^gen-/), { tail: 3000 })
    expect(chartMocks.candle.setData).toHaveBeenCalledWith([
      {
        time: 1_700_000_000, open: 10, high: 12, low: 9, close: 11,
        color: '#131722', borderColor: '#f23645', wickColor: '#f23645',
      },
      {
        time: 1_700_000_300, open: 11, high: 13, low: 10, close: 10,
        color: '#00b8a9', borderColor: '#00b8a9', wickColor: '#00b8a9',
      },
    ])
    expect(chartMocks.volume.setData).toHaveBeenCalledWith([
      { time: 1_700_000_000, value: 3, rising: true },
      { time: 1_700_000_300, value: 4, rising: false },
    ])
    expect(chartMocks.volumeScale.applyOptions).toHaveBeenCalledWith({ autoScale: true, scaleMargins: { top: 0.15, bottom: 0.02 } })
    expect(wrapper.get('[data-pane-id="volume"]').text()).toContain('成交量 4')
    expect(chartMocks.timeScale.setVisibleLogicalRange).toHaveBeenCalledWith({ from: 0, to: 7 })
    wrapper.unmount()
  })

  it('centers a keyboard-selected historical bar and clears only its temporary highlight on chart input', async () => {
    const meta = dataset()
    meta.coverage.last_bar_index = 500
    apiMocks.getBars.mockImplementation(async (_dataset: string, _revision: string, generation: string, options: { tail?: number }) => {
      const index = options.tail ? 500 : 357
      return {
        request_id: 'req', dataset_id: meta.dataset_id, data_revision: revision, generation_id: generation,
        price_scale: 1, coverage: { first_bar_index: index, last_bar_index: index },
        has_more_before: index > 0, has_more_after: index < 500, checksum: `sha256:${'b'.repeat(64)}`,
        bars: { bar_index: [index], timestamp_utc: [1_700_000_000_000 + index * 300_000],
          open_i64: [10], high_i64: [12], low_i64: [9], close_i64: [11], volume: [3], open_interest: [null] },
      }
    })
    const wrapper = mount(ChartGroup, { props: { dataset: meta } })
    await flushPromises()
    await (wrapper.vm as unknown as { focusBar: (index: number) => Promise<void> }).focusBar(357)
    await flushPromises()
    expect(apiMocks.getBars).toHaveBeenLastCalledWith(meta.dataset_id, revision, expect.stringMatching(/^gen-/),
      { beforeBarIndex: 478, limit: 241 })
    expect(chartMocks.timeScale.setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: -40, to: 40 })
    expect(wrapper.get('[data-keyboard-bar-focus="357"]').text()).toContain('K线357')
    await wrapper.get('.chart-host').trigger('pointerdown')
    expect(wrapper.find('[data-keyboard-bar-focus]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('prefetches exactly one 1500-bar page when the visible range nears the left cache edge', async () => {
    apiMocks.getBars.mockImplementation(async (_dataset: string, _revision: string, generation: string, options: { tail?: number; beforeBarIndex?: number }) => {
      const first = options.tail ? 3000 : 1500
      return {
        request_id: 'req', dataset_id: 'SHFE.AO2609.5m', data_revision: revision, generation_id: generation,
        price_scale: 1, coverage: { first_bar_index: first, last_bar_index: first + 1 }, has_more_before: true, has_more_after: true,
        checksum: `sha256:${'b'.repeat(64)}`,
        bars: { bar_index: [first, first + 1], timestamp_utc: [1_700_000_000_000 + first, 1_700_000_300_000 + first], open_i64: [10, 11], high_i64: [12, 13], low_i64: [9, 10], close_i64: [11, 12], volume: [3, 4], open_interest: [null, null] },
      }
    })
    const wrapper = mount(ChartGroup, { props: { dataset: dataset() } })
    await flushPromises()
    vi.useFakeTimers()
    const handler = chartMocks.timeScale.subscribeVisibleLogicalRangeChange.mock.calls[0][0] as (range: { from: number; to: number }) => void
    handler({ from: 1, to: 3 })
    handler({ from: 1, to: 3 })
    await vi.advanceTimersByTimeAsync(150)
    await flushPromises()
    expect(apiMocks.getBars).toHaveBeenCalledTimes(2)
    expect(apiMocks.getBars).toHaveBeenLastCalledWith('SHFE.AO2609.5m', revision, expect.stringMatching(/^gen-/), { beforeBarIndex: 3000, limit: 1500 })
    expect(wrapper.get('.chart-group').attributes('data-cache-first-index')).toBe('1500')
    expect(wrapper.get('.chart-group').attributes('data-cache-bar-count')).toBe('4')
    vi.useRealTimers()
    wrapper.unmount()
  })

  it('queries completed SeriesSource values by visible range without creating a calculation', async () => {
    apiMocks.getCalculationResults.mockResolvedValue({
      result_kind: 'indicator', bar_index: [0, 1], values: { ma: [null, 11.5] }, coverage: { returned_count: 2 },
    })
    const source = {
      source_type: 'SeriesSource' as const, source_id: 'series-1', job_id: 'job-1', status: 'completed' as const,
      parameters: { period: 20, source: 'close' },
      definition: {
        kind: 'indicator' as const, algorithm_id: 'ma', algorithm_version: '1.0.0', source_hash: `sha256:${'c'.repeat(64)}`,
        name: 'Moving Average', input_schema: 'bars.v1' as const, causal: true as const,
        parameter_schema: { type: 'object' as const, additionalProperties: false as const, required: ['period', 'source'], properties: {} },
        outputs: [{ name: 'ma', display_name: 'MA', pane: 'main' as const, series_type: 'line' as const }],
        warmup: { kind: 'formula' as const, expression: 'period - 1' },
      },
    }
    const wrapper = mount(ChartGroup, { props: { dataset: dataset(), indicatorSources: [source] } })
    await flushPromises()
    apiMocks.getCalculationResults.mockClear()
    vi.useFakeTimers()
    const handler = chartMocks.timeScale.subscribeVisibleLogicalRangeChange.mock.calls[0][0] as (range: { from: number; to: number }) => void
    handler({ from: 0, to: 1 })
    await vi.advanceTimersByTimeAsync(150)
    await flushPromises()
    expect(apiMocks.getCalculationResults).toHaveBeenCalledWith('job-1', 0, 1)
    expect(apiMocks.createCalculation).not.toHaveBeenCalled()
    expect(chartMocks.chart.addSeries).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ lineWidth: 1 }), 0)
    expect(wrapper.get('.legend-bar-index').text()).toBe('K线 00002')
    const priceText = wrapper.get('[data-pane-id="price"]').text()
    expect(priceText.indexOf('收 10')).toBeLessThan(priceText.indexOf('MA20 11.50'))
    expect(priceText.indexOf('MA20 11.50')).toBeLessThan(priceText.indexOf('K线 00002'))
    expect(wrapper.get('.legend-bar-index').element.nextElementSibling?.classList.contains('pane-actions')).toBe(true)
    vi.useRealTimers()
    wrapper.unmount()
  })

  it('renders all sector-strength lines and filters foreign-instrument price anchors', async () => {
    const wrapper = mount(ChartGroup, { props: { dataset: dataset() } })
    await flushPromises()
    await wrapper.setProps({ replaySignals: [
      {
        object_type: 'chart_event', object_id: 'sector-a', event_type: 'aux_sector_strength_mean',
        chart_dataset_id: 'SHFE.AO2609.5m', sector_id: 'bank', sector_strength_mean_milli: 4500,
        timestamp_utc: 1_700_000_000_000, known_at_bar_index: 0,
      },
      {
        object_type: 'chart_event', object_id: 'sector-b', event_type: 'aux_sector_strength_mean',
        chart_dataset_id: 'SHFE.AO2609.5m', sector_id: 'technology', sector_strength_mean_milli: 6250,
        timestamp_utc: 1_700_000_300_000, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'current-class', event_type: 'aux_ma_strength_class',
        chart_dataset_id: 'SHFE.AO2609.5m', timestamp_utc: 1_700_000_000_000, price_i64: 11,
      },
      {
        object_type: 'chart_event', object_id: 'foreign-class', event_type: 'aux_ma_strength_class',
        chart_dataset_id: 'SZSE.000001.1d', timestamp_utc: 1_700_000_000_000, price_i64: 9999,
      },
    ] })
    await wrapper.vm.$nextTick()
    const sectorCalls = chartMocks.chart.addSeries.mock.calls.filter((call) => (call[1] as { priceScaleId?: string }).priceScaleId === 'sector-strength')
    expect(sectorCalls).toHaveLength(2)
    expect(sectorCalls.map((call) => call[2])).toEqual([1, 1])
    expect(chartMocks.macd.setData).toHaveBeenCalledWith([{ time: 1_700_000_000, value: 4.5 }])
    expect(chartMocks.macd.setData).toHaveBeenCalledWith([{ time: 1_700_000_300, value: 6.25 }])
    expect(wrapper.get('[data-pane-id="macd"]').text()).toContain('MACD / 板块强度')
    expect(wrapper.get('[data-pane-id="macd"]').text()).toContain('bank 4.500')
    expect(wrapper.find('.replay-signal.aux_ma_strength_class').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('foreign-class')
    expect(wrapper.find('.replay-signal.aux_sector_strength_mean').exists()).toBe(false)
    wrapper.unmount()
  })

  it('projects causal risk decisions and actual execution markers onto the price chart', async () => {
    const wrapper = mount(ChartGroup, { props: { dataset: dataset() } })
    await flushPromises()
    await wrapper.setProps({ replaySignals: [
      {
        object_type: 'risk_decision', object_id: 'risk-approved-0',
        event_type: 'approved_order_intent', display_label: '风控·订单意图批准',
        timestamp_utc: 1_700_000_000_000, price_i64: 11, known_at_bar_index: 0,
      },
      {
        object_type: 'risk_decision', object_id: 'risk-kill-1',
        event_type: 'kill_switch', display_label: '风控·熔断',
        timestamp_utc: 1_700_000_300_000, price_i64: 10, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'trade-1:entry', event_type: 'open_long',
        display_label: '成交·开多', timestamp_utc: 1_700_000_000_000, price_i64: 11,
        known_at_bar_index: 0, execution_fact: true,
      },
    ] })
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.replay-signal.approved_order_intent').text()).toBe('风控·订单意图批准')
    expect(wrapper.get('.replay-signal.kill_switch').text()).toBe('风控·熔断')
    expect(wrapper.get('.replay-signal.open_long').text()).toBe('成交·开多')
    wrapper.unmount()
  })

  it('renders MACD lines and sign-colored histogram values returned by Python', async () => {
    apiMocks.getCalculationResults.mockResolvedValue({
      result_kind: 'indicator', bar_index: [0, 1],
      values: { macd: [-1, 2], signal: [-0.5, 1], histogram: [-0.5, 1] },
      coverage: { returned_count: 2 },
    })
    const source = {
      source_type: 'SeriesSource' as const, source_id: 'series-macd', job_id: 'job-macd', status: 'completed' as const,
      parameters: { fast_period: 12, slow_period: 26, signal_period: 9, source: 'close' },
      definition: {
        kind: 'indicator' as const, algorithm_id: 'macd', algorithm_version: '1.0.0', source_hash: `sha256:${'c'.repeat(64)}`,
        name: 'MACD', input_schema: 'bars.v1' as const, causal: true as const,
        parameter_schema: { type: 'object' as const, additionalProperties: false as const, required: [], properties: {} },
        outputs: [
          { name: 'macd', display_name: 'DIFF', pane: 'indicator' as const, series_type: 'line' as const },
          { name: 'signal', display_name: 'DEA', pane: 'indicator' as const, series_type: 'line' as const },
          { name: 'histogram', display_name: 'MACD', pane: 'indicator' as const, series_type: 'histogram' as const },
        ],
        warmup: { kind: 'formula' as const, expression: 'slow_period + signal_period - 2' },
      },
    }
    const wrapper = mount(ChartGroup, { props: { dataset: dataset(), indicatorSources: [source] } })
    await flushPromises()
    expect(apiMocks.getCalculationResults).toHaveBeenCalledWith('job-macd', 0, 1)
    expect(chartMocks.chart.addSeries).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ color: '#e0e3eb' }), 1)
    expect(chartMocks.chart.addSeries).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ color: '#f2d600' }), 1)
    expect(chartMocks.chart.addCustomSeries).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ autoscaleInfoProvider: expect.any(Function) }), 1)
    expect(chartMocks.macd.createPriceLine).not.toHaveBeenCalled()
    const diffOptions = chartMocks.chart.addSeries.mock.calls.find((call) => (call[1] as { color?: string })?.color === '#e0e3eb')?.[1] as { autoscaleInfoProvider: (base: () => object) => object }
    expect(diffOptions).toEqual(expect.objectContaining({ lastValueVisible: false }))
    expect(diffOptions.autoscaleInfoProvider(() => ({ priceRange: { minValue: -3, maxValue: 5 } }))).toEqual({
      priceRange: { minValue: -3, maxValue: 6 }, margins: { above: 6, below: 6 },
    })
    expect(chartMocks.macd.setData).toHaveBeenCalledWith([
      { time: 1_700_000_000, value: -0.5, color: '#00b8a9' },
      { time: 1_700_000_300, value: 1, color: '#f23645' },
    ])
    expect(wrapper.get('[data-pane-id="macd"]').text()).toContain('MACD(12,26,9)')
    expect(wrapper.get('[data-pane-id="macd"]').text()).toContain('DIFF 2.00')
    expect(wrapper.get('[data-pane-id="macd"]').text()).toContain('DEA 1.00')
    expect(wrapper.get('[data-pane-id="macd"]').text()).toContain('MACD 1.00')
    expect(wrapper.get('[data-pane-id="price"]').text()).toContain('开 11 高 13 低 10 收 10')
    expect(wrapper.get('[data-pane-id="price"]').text()).toContain('K线 00002')
    expect(wrapper.get('[data-pane-id="volume"]').text()).toContain('成交量 4')
    const crosshairHandler = chartMocks.chart.subscribeCrosshairMove.mock.calls[0][0] as (parameter: object) => void
    crosshairHandler({ point: { x: 25, y: 40 }, logical: 0.2, seriesData: new Map() })
    await wrapper.vm.$nextTick()
    expect(wrapper.get('[data-pane-id="price"]').text()).toContain('开 10 高 12 低 9 收 11')
    expect(wrapper.get('[data-pane-id="price"]').text()).toContain('K线 00001')
    expect(wrapper.get('[data-pane-id="volume"]').text()).toContain('成交量 3')
    expect(wrapper.get('[data-pane-id="macd"]').text()).toContain('DIFF -1.00')
    expect(wrapper.get('[data-pane-id="macd"]').text()).toContain('DEA -0.50')
    expect(wrapper.get('[data-pane-id="macd"]').text()).toContain('MACD -0.50')
    expect(wrapper.find('.drawing-crosshair').exists()).toBe(false)
    const createChartCalls = chartMocks.createChart.mock.calls as unknown as [unknown, unknown][]
    const chartOptions = createChartCalls[0]?.[1] as {
      crosshair: { mode: number; vertLine: { labelBackgroundColor: string }; horzLine: { labelBackgroundColor: string } }
      localization: { timeFormatter: (time: number) => string }
    }
    expect(chartOptions.crosshair).toMatchObject({
      mode: 0,
      vertLine: { labelBackgroundColor: '#8b2b31' },
      horzLine: { labelBackgroundColor: '#8b2b31' },
    })
    expect(chartOptions.localization.timeFormatter(1_783_512_600)).toMatch(/^2026\/07\/\d{2} \d{2}:\d{2}~\d{2}:\d{2} [日一二三四五六]$/)
    wrapper.unmount()
  })

  it('renders saved color, opacity, width and line style without recalculation', async () => {
    apiMocks.getCalculationResults.mockResolvedValue({
      result_kind: 'indicator', bar_index: [0, 1], values: { ma: [10, 11] }, coverage: { returned_count: 2 },
    })
    const source = {
      source_type: 'SeriesSource' as const, source_id: 'series-styled', job_id: 'job-styled', status: 'completed' as const,
      parameters: { period: 20 },
      style: { outputs: { ma: { color: '#ab47bc', line_width: 3 as const, line_style: 'dashed' as const, opacity: 0.7, visible: true } } },
      definition: {
        kind: 'indicator' as const, algorithm_id: 'ma', algorithm_version: '1.0.0', source_hash: `sha256:${'c'.repeat(64)}`,
        name: 'Moving Average', input_schema: 'bars.v1' as const, causal: true as const,
        parameter_schema: { type: 'object' as const, additionalProperties: false as const, required: [], properties: {} },
        outputs: [{ name: 'ma', display_name: 'MA', pane: 'main' as const, series_type: 'line' as const }],
        warmup: { kind: 'formula' as const, expression: '0' },
      },
    }
    const wrapper = mount(ChartGroup, { props: { dataset: dataset(), indicatorSources: [source] } })
    await flushPromises()
    expect(chartMocks.chart.addSeries).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({
      color: 'rgba(171, 71, 188, 0.7)', lineWidth: 3, lineStyle: 2,
    }), 0)
    expect(chartMocks.candle.applyOptions).toHaveBeenCalledWith(expect.objectContaining({
      color: 'rgba(171, 71, 188, 0.7)', lineWidth: 3, lineStyle: 2,
    }))
    expect(apiMocks.createCalculation).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it.each([false, true])('filters centers consistently for historical/replay mode (replay=%s)', async (replay) => {
    const objects = {
      processed_bars: [], fractals: [], bi: [], bi_states: [], segments: [],
      local_centers: [
        { object_id: 'bi-center', unit_kind: 'BI', structural_level: 'stroke' },
        { object_id: 'segment-center', unit_kind: 'SEGMENT', structural_level: 'segment' },
      ] as ChanLocalCenter[],
      center_connections: [], center_audit_events: [],
      movement_states: [], center_monitors: [], divergences: [], trade_points: [],
    } satisfies ChanCalculationResults['objects']
    apiMocks.getCalculationResults.mockResolvedValue({ result_kind: 'chan', objects })
    const source = rangeSources().strategySources[0]!
    source.category_visibility.bi_centers = false
    source.category_visibility.segment_centers = true
    const setData = vi.spyOn(ChanPrimitive.prototype, 'setData')
    const wrapper = mount(ChartGroup, { props: {
      dataset: dataset(), strategySources: [source], replayObjects: replay ? objects : null,
      replayCursor: replay ? 1 : null,
    } })
    await flushPromises()
    expect(setData.mock.calls.at(-1)?.[0].local_centers.map(c => c.object_id)).toEqual(['segment-center'])
    expect(wrapper.get('[data-pane-id="price"]').text()).toContain('实体中枢 1')
    expect(apiMocks.createCalculation).not.toHaveBeenCalled()
    wrapper.unmount()
    setData.mockRestore()
  })

  it.each([false, true])('filters the three divergence layers independently in historical/replay mode (replay=%s)', async (replay) => {
    const divergences = [
      { object_id: 'trend', divergence_kind: 'trend', status: 'confirmed' },
      { object_id: 'consolidation', divergence_kind: 'consolidation', status: 'confirmed' },
      { object_id: 'oscillation', divergence_kind: 'center_oscillation', status: 'confirmed' },
    ] as ChanCalculationResults['objects']['divergences']
    const objects = { processed_bars: [], fractals: [], bi: [], bi_states: [], segments: [],
      local_centers: [], center_connections: [], center_audit_events: [], movement_states: [], center_monitors: [],
      divergences, trade_points: [] } satisfies ChanCalculationResults['objects']
    apiMocks.getCalculationResults.mockResolvedValue({ result_kind: 'chan', objects })
    const source = rangeSources().strategySources[0]!
    source.category_visibility.divergences = true
    source.category_visibility.trend_divergences = false
    source.category_visibility.consolidation_divergences = true
    source.category_visibility.oscillation_divergences = false
    const setData = vi.spyOn(ChanPrimitive.prototype, 'setData')
    const wrapper = mount(ChartGroup, { props: {
      dataset: dataset(), strategySources: [source], replayObjects: replay ? objects : null,
      replayCursor: replay ? 1 : null,
    } })
    await flushPromises()
    expect(setData.mock.calls.at(-1)?.[0].divergences.map((item) => item.object_id)).toEqual(['consolidation'])
    wrapper.unmount()
    setData.mockRestore()
  })

  it('keeps a boundary-confirmation layer drawable when its center layer is hidden', async () => {
    const objects = {
      processed_bars: [], fractals: [], bi: [], bi_states: [], segments: [],
      local_centers: [{ object_id: 'bi-center', unit_kind: 'BI', structural_level: 'stroke' }] as ChanLocalCenter[],
      center_connections: [], center_audit_events: [],
      movement_states: [], center_monitors: [], divergences: [], trade_points: [],
    } satisfies ChanCalculationResults['objects']
    apiMocks.getCalculationResults.mockResolvedValue({ result_kind: 'chan', objects })
    const source = rangeSources().strategySources[0]!
    source.category_visibility.bi_centers = false
    source.category_visibility.segment_centers = false
    source.category_visibility.bi_boundary_confirmations = true
    source.category_visibility.segment_boundary_confirmations = false
    const setData = vi.spyOn(ChanPrimitive.prototype, 'setData')
    const wrapper = mount(ChartGroup, { props: { dataset: dataset(), strategySources: [source] } })
    await flushPromises()

    expect(setData.mock.calls.at(-1)?.[0].local_centers.map(center => center.object_id)).toEqual(['bi-center'])
    expect(setData.mock.calls.at(-1)?.[2]?.get('bi-center')).toEqual({ center: false, boundaryConfirmation: true })
    expect(wrapper.get('[data-pane-id="price"]').text()).toContain('实体中枢 0')
    wrapper.unmount()
    setData.mockRestore()
  })

  it('queries a completed StrategySource into the single Chan primitive without recalculation', async () => {
    apiMocks.getCalculationResults.mockResolvedValue({
      result_kind: 'chan', objects: {
        processed_bars: [],
        fractals: [],
        bi: [{ object_id: 'bi-1', start_time: 1_700_000_000_000, start_price_i64: 10, end_time: 1_700_000_300_000, end_price_i64: 12, confirmed: true }],
        segments: [{ object_id: 'segment-1', start_time: 1_700_000_000_000, start_price_i64: 10, end_time: 1_700_000_300_000, end_price_i64: 12, confirmed: true }],
        bi_states: [], local_centers: [], center_connections: [], center_audit_events: [], movement_states: [], center_monitors: [], divergences: [], trade_points: [],
      },
      coverage: { first_bar_index: 0, last_bar_index: 1, returned_count: 2 },
    })
    const source = {
      source_type: 'StrategySource' as const, source_id: 'strategy-1', job_id: 'job-chan', status: 'completed' as const,
      visible: true, category_visibility: { fractals: false, bi: true, segments: true, bi_centers: true, segment_centers: true, bi_boundary_confirmations: true, segment_boundary_confirmations: true, divergences: true, first_trade_points: true, second_trade_points: true, third_trade_points: true }, parameters: { min_fractal_gap: 5 },
      definition: {
        kind: 'chan' as const, algorithm_id: 'chan_standard', algorithm_version: '1.0.0', source_hash: `sha256:${'c'.repeat(64)}`,
        name: '标准缠论', input_schema: 'bars.v1' as const, causal: true as const,
        parameter_schema: { type: 'object' as const, additionalProperties: false as const, required: [], properties: {} },
        outputs: [{ name: 'bi', display_name: '笔', pane: 'main' as const, series_type: 'semantic_objects' as const, object_type: 'bi' as const }],
        warmup: { kind: 'formula' as const, expression: 'full history causal state' },
      },
    }
    const wrapper = mount(ChartGroup, { props: { dataset: dataset(), strategySources: [source] } })
    await flushPromises()
    expect(apiMocks.getCalculationResults).toHaveBeenCalledWith('job-chan', 0, 1)
    expect(chartMocks.candle.attachPrimitive).toHaveBeenCalledTimes(1)
    expect(apiMocks.createCalculation).not.toHaveBeenCalled()
    expect(wrapper.get('[data-pane-id="price"]').text()).toContain('缠论 笔 1 段 1 实体中枢 0 背驰 0 买卖点 0')
    wrapper.unmount()
  })

  it('loads the complete zoomed-out viewport after left prefetch, including segments, movement states and MACD', async () => {
    const meta = dataset()
    meta.coverage = { ...meta.coverage, bar_count: 6000, last_bar_index: 5999 }
    apiMocks.getBars.mockImplementation(async (_dataset: string, _revision: string, generation: string, options: { tail?: number; beforeBarIndex?: number; limit?: number }) => {
      const end = options.tail ? 5999 : options.beforeBarIndex! - 1
      const start = Math.max(0, end - (options.tail ?? options.limit!) + 1)
      const indices = Array.from({ length: end - start + 1 }, (_, i) => start + i)
      return {
        dataset_id: meta.dataset_id, data_revision: revision, generation_id: generation,
        checksum: `sha256:${'b'.repeat(64)}`, coverage: { first_bar_index: start, last_bar_index: end },
        bars: { bar_index: indices, timestamp_utc: indices.map((i) => 1_700_000_000_000 + i * 300_000),
          open_i64: indices.map(() => 10), high_i64: indices.map(() => 12), low_i64: indices.map(() => 9),
          close_i64: indices.map(() => 11), volume: indices.map(() => 3), open_interest: indices.map(() => null) },
      }
    })
    apiMocks.getCalculationResults.mockImplementation(async (job, from, to) => rangeResult(job, from, to))
    const setChan = vi.spyOn(ChanPrimitive.prototype, 'setData')
    const wrapper = mount(ChartGroup, { props: { dataset: meta, ...rangeSources() } })
    await flushPromises()
    vi.useFakeTimers()
    const handler = chartMocks.timeScale.subscribeVisibleLogicalRangeChange.mock.calls[0][0]
    for (const count of [3000, 4500]) {
      const range = { from: 0, to: count - 1 }
      chartMocks.timeScale.getVisibleLogicalRange.mockReturnValue(range)
      handler(range)
      await vi.advanceTimersByTimeAsync(350)
      await flushPromises()
    }
    expect(wrapper.get('.chart-group').attributes('data-cache-bar-count')).toBe('6000')
    apiMocks.getCalculationResults.mockClear()
    handler({ from: 0, to: 5999 })
    await vi.advanceTimersByTimeAsync(200)
    await flushPromises()
    expect(apiMocks.getCalculationResults.mock.calls).toEqual(expect.arrayContaining([
      ['job-macd', 0, 4999], ['job-macd', 5000, 5999],
      ['job-chan', 0, 4999], ['job-chan', 5000, 5999],
    ]))
    expect(chartMocks.macd.setData.mock.lastCall?.[0]).toHaveLength(6000)
    expect(setChan.mock.lastCall?.[0].segments.map((s) => s.object_id)).toEqual(['segment-0', 'segment-5000'])
    expect(setChan.mock.lastCall?.[0].movement_states).toHaveLength(1)
    expect(wrapper.find('.chart-error').exists()).toBe(false)
    expect(apiMocks.createCalculation).not.toHaveBeenCalled()
    wrapper.unmount()
    setChan.mockRestore()
    vi.useRealTimers()
  })

  it('does not let a slow old viewport overwrite newer indicators or Chan objects', async () => {
    apiMocks.getCalculationResults.mockImplementation(async (job, from, to) => rangeResult(job, from, to))
    const setChan = vi.spyOn(ChanPrimitive.prototype, 'setData')
    const wrapper = mount(ChartGroup, { props: { dataset: dataset(), ...rangeSources() } })
    await flushPromises()
    const pending: Array<() => void> = []
    apiMocks.getCalculationResults.mockImplementation((job, from, to) => to === 0
      ? new Promise((resolve) => pending.push(() => resolve(rangeResult(job, from, to))))
      : Promise.resolve(rangeResult(job, from, to)))
    vi.useFakeTimers()
    const handler = chartMocks.timeScale.subscribeVisibleLogicalRangeChange.mock.calls[0][0]
    handler({ from: 0, to: 0 })
    await vi.advanceTimersByTimeAsync(150)
    expect(pending).toHaveLength(2)
    handler({ from: 0, to: 1 })
    await vi.advanceTimersByTimeAsync(150)
    await flushPromises()
    const chanCalls = setChan.mock.calls.length
    const indicatorCalls = chartMocks.macd.setData.mock.calls.length
    pending.forEach((resolve) => resolve())
    await flushPromises()
    expect(setChan).toHaveBeenCalledTimes(chanCalls)
    expect(chartMocks.macd.setData).toHaveBeenCalledTimes(indicatorCalls)
    expect(chartMocks.macd.setData.mock.lastCall?.[0]).toHaveLength(2)
    wrapper.unmount()
    setChan.mockRestore()
    vi.useRealTimers()
  })

  it('shows a range error instead of silently leaving old overlays, and clears it after retry', async () => {
    apiMocks.getCalculationResults.mockImplementation(async (job, from, to) => rangeResult(job, from, to))
    const wrapper = mount(ChartGroup, { props: { dataset: dataset(), ...rangeSources() } })
    await flushPromises()
    vi.useFakeTimers()
    apiMocks.getCalculationResults.mockRejectedValue(new Error('offline'))
    const handler = chartMocks.timeScale.subscribeVisibleLogicalRangeChange.mock.calls[0][0]
    handler({ from: 0, to: 1 })
    await vi.advanceTimersByTimeAsync(150)
    await flushPromises()
    expect(wrapper.get('.chart-error').text()).toContain('指标/缠论范围加载失败')
    apiMocks.getCalculationResults.mockImplementation(async (job, from, to) => rangeResult(job, from, to))
    handler({ from: 0, to: 1 })
    await vi.advanceTimersByTimeAsync(150)
    await flushPromises()
    expect(wrapper.find('.chart-error').exists()).toBe(false)
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('hides future bars when replay cursor moves without creating calculations', async () => {
    const wrapper = mount(ChartGroup, { props: { dataset: dataset(), replayCursor: 0, replayObjects: { processed_bars: [], fractals: [], bi: [], bi_states: [], segments: [], local_centers: [], center_connections: [], center_audit_events: [], movement_states: [], center_monitors: [], divergences: [], trade_points: [] } } })
    await flushPromises()
    expect(chartMocks.candle.setData).toHaveBeenLastCalledWith([
      expect.objectContaining({ time: 1_700_000_000, open: 10, high: 12, low: 9, close: 11 }),
    ])
    await wrapper.setProps({ replayCursor: 1 })
    expect(chartMocks.candle.setData).toHaveBeenLastCalledWith(expect.arrayContaining([
      expect.objectContaining({ time: 1_700_000_300 }),
    ]))
    expect(apiMocks.createCalculation).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('draws Chan strategy and auxiliary events on the shared price chart layer', async () => {
    const wrapper = mount(ChartGroup, {
      props: {
        dataset: dataset(),
      },
    })
    await flushPromises()
    await wrapper.setProps({ replaySignals: [
      {
        object_type: 'chart_event', object_id: 'handoff-B2', event_type: 'handoff_to_B3_trend',
        timestamp_utc: 1_700_000_300_000, price_i64: 12, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'hold-B3', event_type: 'hold_new_center',
        timestamp_utc: 1_700_000_000_000, price_i64: 11, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'swing-OSC', event_type: 'swing_buy',
        timestamp_utc: 1_700_000_300_000, price_i64: 10, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'stop-OSC', event_type: 'stop_oscillation',
        timestamp_utc: 1_700_000_000_000, price_i64: 12, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'buy-SLD', event_type: 'same_level_buy',
        timestamp_utc: 1_700_000_300_000, price_i64: 10, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'wait-SLD', event_type: 'wait_new_same_level_structure',
        timestamp_utc: 1_700_000_000_000, price_i64: 12, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'promote-SLD', event_type: 'promote_level_candidate',
        timestamp_utc: 1_700_000_300_000, price_i64: 11, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'promoted-SLD', event_type: 'promote_level',
        timestamp_utc: 1_700_000_300_000, price_i64: 11, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'low-turn-3LC', event_type: 'low_turn_active',
        timestamp_utc: 1_700_000_300_000, price_i64: 10, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'middle-third-3LC', event_type: 'mid_third_point',
        timestamp_utc: 1_700_000_000_000, price_i64: 12, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'high-change-3LC', event_type: 'high_change_candidate',
        timestamp_utc: 1_700_000_300_000, price_i64: 11, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'partial-RBS', event_type: 'partial_take_profit',
        timestamp_utc: 1_700_000_000_000, price_i64: 12, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'reenter-RBS', event_type: 'reenter',
        timestamp_utc: 1_700_000_300_000, price_i64: 10, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'handoff-RBS', event_type: 'trend_handoff',
        timestamp_utc: 1_700_000_300_000, price_i64: 11, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'bottom-BTC', event_type: 'bottom_build_success',
        timestamp_utc: 1_700_000_300_000, price_i64: 10, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'top-BTC', event_type: 'top_build_failure',
        timestamp_utc: 1_700_000_000_000, price_i64: 12, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'coarse-bottom-BTC', event_type: 'coarse_bottom_zone',
        timestamp_utc: 1_700_000_300_000, price_i64: 11, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'aux-lip', event_type: 'aux_lip_kiss',
        timestamp_utc: 1_700_000_300_000, price_i64: 11, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'aux-B1', event_type: 'aux_legacy_B1_candidate',
        timestamp_utc: 1_700_000_000_000, price_i64: 10, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'aux-risk-off', event_type: 'aux_macd_risk_off',
        timestamp_utc: 1_700_000_300_000, price_i64: 10, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'aux-risk-on', event_type: 'aux_macd_risk_on_candidate',
        timestamp_utc: 1_700_000_000_000, price_i64: 11, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'aux-boll-exit', event_type: 'aux_boll_superstrong_exit',
        timestamp_utc: 1_700_000_300_000, price_i64: 12, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'aux-boll-buy-zone', event_type: 'aux_boll_second_buy_zone',
        timestamp_utc: 1_700_000_000_000, price_i64: 10, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'aux-boll-sell-zone', event_type: 'aux_boll_second_sell_zone',
        timestamp_utc: 1_700_000_300_000, price_i64: 11, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'aux-boll-warning', event_type: 'aux_boll_bardo_end_or_promotion_warning',
        timestamp_utc: 1_700_000_000_000, price_i64: 11, known_at_bar_index: 1,
      },
      {
        object_type: 'chart_event', object_id: 'aux-daily-30m', event_type: 'aux_daily_30m_classification',
        timestamp_utc: 1_700_000_300_000, price_i64: 12, known_at_bar_index: 1,
        display_label: '日内双重叠区·向上·收于上方重叠区上方', classification: 'daily_two_center',
        center_1_start_timestamp_utc: 1_700_000_000_000,
        center_1_end_timestamp_utc: 1_700_000_300_000,
        center_1_low_i64: 10, center_1_high_i64: 12,
        center_2_start_timestamp_utc: 1_700_000_000_000,
        center_2_end_timestamp_utc: 1_700_000_300_000,
        center_2_low_i64: 11, center_2_high_i64: 13,
      },
    ] })
    const B2Event = wrapper.get('.replay-signal.handoff_to_B3_trend')
    expect(B2Event.text()).toBe('handoff_to_B3_trend')
    expect(B2Event.get('path').attributes('d')).toContain('M 300 120')
    expect(wrapper.get('.replay-signal.hold_new_center').text()).toBe('hold_new_center')
    expect(wrapper.get('.replay-signal.swing_buy').text()).toBe('swing_buy')
    expect(wrapper.get('.replay-signal.stop_oscillation').text()).toBe('stop_oscillation')
    expect(wrapper.get('.replay-signal.same_level_buy').text()).toBe('same_level_buy')
    expect(wrapper.get('.replay-signal.wait_new_same_level_structure').text()).toBe('wait_new_same_level_structure')
    expect(wrapper.get('.replay-signal.promote_level_candidate').text()).toBe('promote_level_candidate')
    expect(wrapper.get('.replay-signal.promote_level').text()).toBe('promote_level')
    expect(wrapper.get('.replay-signal.low_turn_active').text()).toBe('low_turn_active')
    expect(wrapper.get('.replay-signal.mid_third_point').text()).toBe('mid_third_point')
    expect(wrapper.get('.replay-signal.high_change_candidate').text()).toBe('high_change_candidate')
    expect(wrapper.get('.replay-signal.partial_take_profit').text()).toBe('partial_take_profit')
    expect(wrapper.get('.replay-signal.reenter').text()).toBe('reenter')
    expect(wrapper.get('.replay-signal.trend_handoff').text()).toBe('trend_handoff')
    expect(wrapper.get('.replay-signal.bottom_build_success').text()).toBe('bottom_build_success')
    expect(wrapper.get('.replay-signal.top_build_failure').text()).toBe('top_build_failure')
    expect(wrapper.get('.replay-signal.coarse_bottom_zone').text()).toBe('coarse_bottom_zone')
    expect(wrapper.get('.replay-signal.aux_lip_kiss').text()).toBe('aux_lip_kiss')
    expect(wrapper.get('.replay-signal.aux_legacy_B1_candidate').text()).toBe('aux_legacy_B1_candidate')
    expect(wrapper.get('.replay-signal.aux_macd_risk_off').text()).toBe('aux_macd_risk_off')
    expect(wrapper.get('.replay-signal.aux_macd_risk_on_candidate').text()).toBe('aux_macd_risk_on_candidate')
    expect(wrapper.get('.replay-signal.aux_boll_superstrong_exit').text()).toBe('aux_boll_superstrong_exit')
    expect(wrapper.get('.replay-signal.aux_boll_second_buy_zone').text()).toBe('aux_boll_second_buy_zone')
    expect(wrapper.get('.replay-signal.aux_boll_second_sell_zone').text()).toBe('aux_boll_second_sell_zone')
    expect(wrapper.get('.replay-signal.aux_boll_bardo_end_or_promotion_warning').text()).toBe('aux_boll_bardo_end_or_promotion_warning')
    expect(wrapper.get('.replay-signal.aux_daily_30m_classification').text()).toBe('日内双重叠区·向上·收于上方重叠区上方')
    const dailyCenters = wrapper.findAll('[data-daily-center]')
    expect(dailyCenters).toHaveLength(2)
    expect(dailyCenters[0]?.attributes('data-daily-center')).toBe('1')
    expect(dailyCenters[0]?.classes()).toContain('daily_two_center')
    expect(dailyCenters[1]?.classes()).toContain('center-2')
    wrapper.unmount()
  })

  it('creates a rectangle with time and fixed-price anchors instead of pixels', async () => {
    const wrapper = mount(ChartGroup, { props: { dataset: dataset(), drawingTool: 'rectangle' } })
    await flushPromises()
    const capture = wrapper.get('.drawing-capture')
    capture.element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 10, clientY: 20 }))
    capture.element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 30, clientY: 40 }))
    await wrapper.vm.$nextTick()
    const emitted = wrapper.emitted('update:drawings')?.at(-1)?.[0] as Array<{ anchors: Array<Record<string, number>> }>
    expect(emitted[0]?.anchors).toEqual([
      { time: 1_700_000_010_000, price_i64: 2, price_scale: 1 },
      { time: 1_700_000_030_000, price_i64: 4, price_scale: 1 },
    ])
    expect(emitted[0]?.anchors[0]).not.toHaveProperty('x')
    expect(emitted[0]?.anchors[0]).not.toHaveProperty('y')
    wrapper.unmount()
  })

  it('centers a selected signal already in cache and renders its trade label', async () => {
    const selectedSignal = {
      object_id: 'signal-1', bar_index: 0, time: 1_700_000_000_000, price_i64: 11,
      signal_type: 'buy_1' as const, divergence_kind: null, signal_class: 'standard' as const, strength: null,
      reference_object_id: null, macd_area_reference: null, macd_area_current: null,
      confirmed: true, confirmed_at_bar_index: 1, known_at_bar_index: 1, object_revision: 1, label: '买入',
      third_buy_evidence: {
        evidence_profile: 'third_buy_entry_evidence_v1' as const,
        b3_object_id: 'B3-1', b3_bar_index: 1, b3_timestamp_utc: 1_700_000_300_000,
        b3_price_i64: 11, b3_confirmed_at_bar_index: 1, b3_confirmed_at_timestamp_utc: 1_700_000_300_000,
        source_center_id: 'ZS-0', source_center_start_bar_index: 0,
        source_center_start_timestamp_utc: 1_700_000_000_000, source_center_end_bar_index: 0,
        source_center_end_timestamp_utc: 1_700_000_000_000, source_center_zd_i64: 10,
        source_center_zg_i64: 11, source_center_dd_i64: 9, source_center_gg_i64: 12,
        center_ordinal_in_trend: 1, priority: 'high', departure_segment_id: 'UP-0-1',
        departure_start_bar_index: 0, departure_start_timestamp_utc: 1_700_000_000_000,
        departure_end_bar_index: 1, departure_end_timestamp_utc: 1_700_000_300_000,
        departure_start_price_i64: 11, departure_end_price_i64: 13, departure_high_i64: 13,
        departure_high_source_bar_index: 1, departure_high_source_timestamp_utc: 1_700_000_300_000,
        return_segment_id: 'DOWN-1', return_start_bar_index: 1,
        return_start_timestamp_utc: 1_700_000_300_000, return_end_bar_index: 1,
        return_end_timestamp_utc: 1_700_000_300_000, return_start_price_i64: 13,
        return_end_price_i64: 11, return_low_i64: 11, return_boundary_relation: 'at_or_above_ZG' as const,
        return_low_source_bar_index: 1, return_low_source_timestamp_utc: 1_700_000_300_000,
        return_range_profile: 'constituent_bi_union_v1',
        return_clearance_above_zg_i64: 0, entry_volume: 500, minimum_entry_volume: 0, quantity: 2,
      },
    }
    const wrapper = mount(ChartGroup, { props: { dataset: dataset(), selectedSignal, signalLocked: true } })
    await flushPromises()
    expect(wrapper.find('[data-selected-signal="true"]').classes()).toContain('locked')
    expect(wrapper.findAll('.signal-selection circle')).toHaveLength(2)
    expect(wrapper.get('.signal-selection-label').text()).toBe('买入')
    expect(wrapper.find('.signal-selection-marker').exists()).toBe(true)
    expect(wrapper.find('[data-third-buy-evidence="true"]').exists()).toBe(true)
    expect(wrapper.get('.third-buy-center-label').text()).toContain('来源中枢 ZS-0 · ZD 10 / ZG 11')
    expect(wrapper.get('.third-buy-departure-label').text()).toBe('向上离开')
    expect(wrapper.findAll('.third-buy-return-label')[0]?.text()).toContain('首次回试结构结束 K1 @ 11')
    expect(wrapper.findAll('.third-buy-return-label')[1]?.text()).toContain('实际低点 K1 @ 11 ≥ ZG 11')
    expect(wrapper.get('.third-buy-confirmation-label').text()).toBe('B3 于 K1 确认可知')
    chartMocks.timeScale.getVisibleLogicalRange.mockReturnValueOnce({ from: 0, to: 1 })
    await (wrapper.vm as unknown as { focusSignal: (signal: typeof selectedSignal) => Promise<void> }).focusSignal(selectedSignal)
    expect(apiMocks.getBars).toHaveBeenCalledTimes(1)
    expect(chartMocks.timeScale.setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: -80, to: 80 })
    wrapper.unmount()
  })

  it('loads and centers an arbitrary historical trade bar', async () => {
    const historicalIndexes = Array.from({ length: 241 }, (_, index) => 880 + index)
    apiMocks.getBars.mockImplementation(async (_dataset: string, _revision: string, generation: string, options: { tail?: number; beforeBarIndex?: number; afterBarIndex?: number }) => {
      if (options.tail) {
        return {
          request_id: 'req', dataset_id: 'SHFE.AO2609.5m', data_revision: revision, generation_id: generation,
          price_scale: 1, coverage: { first_bar_index: 3000, last_bar_index: 5999 }, has_more_before: true, has_more_after: false,
          checksum: `sha256:${'b'.repeat(64)}`,
          bars: { bar_index: [3000, 5999], timestamp_utc: [1_700_900_000_000, 1_701_799_700_000], open_i64: [10, 11], high_i64: [12, 13], low_i64: [9, 10], close_i64: [11, 10], volume: [3, 4], open_interest: [null, null] },
        }
      }
      if (options.afterBarIndex === 1120) {
        return {
          request_id: 'req', dataset_id: 'SHFE.AO2609.5m', data_revision: revision, generation_id: generation,
          price_scale: 1, coverage: { first_bar_index: 1121, last_bar_index: 1122 }, has_more_before: true, has_more_after: true,
          checksum: `sha256:${'b'.repeat(64)}`,
          bars: { bar_index: [1121, 1122], timestamp_utc: [1_700_336_300_000, 1_700_336_600_000], open_i64: [1121, 1122], high_i64: [1123, 1124], low_i64: [1120, 1121], close_i64: [1122, 1123], volume: [1121, 1122], open_interest: [null, null] },
        }
      }
      return {
        request_id: 'req', dataset_id: 'SHFE.AO2609.5m', data_revision: revision, generation_id: generation,
        price_scale: 1, coverage: { first_bar_index: 880, last_bar_index: 1120 }, has_more_before: true, has_more_after: true,
        checksum: `sha256:${'b'.repeat(64)}`,
        bars: {
          bar_index: historicalIndexes, timestamp_utc: historicalIndexes.map((index) => 1_700_000_000_000 + index * 300_000),
          open_i64: historicalIndexes, high_i64: historicalIndexes.map((index) => index + 2), low_i64: historicalIndexes.map((index) => index - 1), close_i64: historicalIndexes.map((index) => index + 1),
          volume: historicalIndexes, open_interest: historicalIndexes.map(() => null),
        },
      }
    })
    const wideDataset = dataset()
    wideDataset.coverage = { ...wideDataset.coverage, bar_count: 6000, last_bar_index: 5999 }
    const selectedSignal = {
      object_id: 'trade-1000:entry', bar_index: 1000, time: 1_700_300_000_000, price_i64: 1000,
      confirmed_at_bar_index: 1000, known_at_bar_index: 1000, object_revision: 1, label: '买入',
    }
    const wrapper = mount(ChartGroup, { props: { dataset: wideDataset, selectedSignal, signalLocked: true } })
    await flushPromises()
    chartMocks.timeScale.getVisibleLogicalRange.mockReturnValueOnce({ from: 0, to: 100 })
    chartMocks.timeScale.timeToIndex.mockReturnValueOnce(220)
    await (wrapper.vm as unknown as { focusSignal: (signal: typeof selectedSignal) => Promise<void> }).focusSignal(selectedSignal)
    await flushPromises()
    expect(apiMocks.getBars).toHaveBeenLastCalledWith(
      'SHFE.AO2609.5m', revision, expect.stringMatching(/^gen-/), { beforeBarIndex: 1121, limit: 241 },
    )
    expect(chartMocks.timeScale.setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: 170, to: 270 })
    expect(wrapper.get('.signal-selection-label').text()).toBe('买入')
    vi.useFakeTimers()
    await wrapper.get('.chart-host').trigger('pointerdown')
    const rangeChanged = chartMocks.timeScale.subscribeVisibleLogicalRangeChange.mock.calls[0][0] as (range: { from: number; to: number }) => void
    rangeChanged({ from: 180, to: 240 })
    await vi.advanceTimersByTimeAsync(160)
    await flushPromises()
    expect(apiMocks.getBars).toHaveBeenLastCalledWith(
      'SHFE.AO2609.5m', revision, expect.stringMatching(/^gen-/), { afterBarIndex: 1120, limit: 1500 },
    )
    expect(wrapper.get('.chart-group').attributes('data-cache-bar-count')).toBe('243')
    vi.useRealTimers()
    wrapper.unmount()
  })

  it('loads both compared segments when locking a historical divergence', async () => {
    apiMocks.getBars.mockImplementation(async (_dataset: string, _revision: string, generation: string, options: { tail?: number; beforeBarIndex?: number; limit?: number }) => {
      const indices = options.tail ? [3000, 5999]
        : Array.from({ length: options.limit ?? 0 }, (_, index) => (options.beforeBarIndex ?? 0) - (options.limit ?? 0) + index)
      return {
        request_id: 'req', dataset_id: 'SHFE.AO2609.5m', data_revision: revision, generation_id: generation,
        price_scale: 1, coverage: { first_bar_index: indices[0], last_bar_index: indices.at(-1) },
        has_more_before: true, has_more_after: true, checksum: `sha256:${'b'.repeat(64)}`,
        bars: { bar_index: indices, timestamp_utc: indices.map((index) => 1_700_000_000_000 + index * 300_000),
          open_i64: indices, high_i64: indices.map((index) => index + 2), low_i64: indices.map((index) => index - 1),
          close_i64: indices, volume: indices, open_interest: indices.map(() => null) },
      }
    })
    const wideDataset = dataset()
    wideDataset.coverage = { ...wideDataset.coverage, bar_count: 6000, last_bar_index: 5999 }
    const a = { object_id: 'segment-a', start_bar_index: 300, end_bar_index: 450,
      start_time: 1_700_090_000_000, end_time: 1_700_135_000_000, start_price_i64: 105, end_price_i64: 92 } as never
    const reference = { object_id: 'segment-reference', start_bar_index: 500, end_bar_index: 700,
      start_time: 1_700_150_000_000, end_time: 1_700_210_000_000, start_price_i64: 100, end_price_i64: 90 } as never
    const current = { object_id: 'segment-current', start_bar_index: 900, end_bar_index: 1000,
      start_time: 1_700_270_000_000, end_time: 1_700_300_000_000, start_price_i64: 95, end_price_i64: 85 } as never
    const signal = { object_id: 'divergence-1', object_type: 'divergence', bar_index: 1000,
      time: 1_700_300_000_000, price_i64: 85, confirmed_at_bar_index: 1020, known_at_bar_index: 1020,
      object_revision: 1, signal: { divergence_kind: 'trend', a_object_id: 'segment-a', comparison_reference_object_id: 'segment-reference',
        comparison_current_object_id: 'segment-current' } } as never
    const wrapper = mount(ChartGroup, { props: { dataset: wideDataset, selectedSignal: signal,
      selectedDivergenceSegments: { a, reference, current } } })
    await flushPromises()
    await (wrapper.vm as unknown as { focusSignal: (value: typeof signal) => Promise<void> }).focusSignal(signal)
    expect(apiMocks.getBars).toHaveBeenLastCalledWith('SHFE.AO2609.5m', revision, expect.stringMatching(/^gen-/),
      { beforeBarIndex: 1101, limit: 881 })
    expect(chartMocks.timeScale.setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: 0, to: 880 })
    expect(wrapper.findAll('[data-divergence-segment]')).toHaveLength(3)
    wrapper.unmount()
  })

  it('discloses evidence that exceeds one bounded K-line focus request', async () => {
    apiMocks.getBars.mockImplementation(async (_dataset: string, _revision: string, generation: string, options: { tail?: number; beforeBarIndex?: number; limit?: number }) => {
      const indices = options.tail ? [7000, 9999]
        : Array.from({ length: options.limit ?? 0 }, (_, index) => (options.beforeBarIndex ?? 0) - (options.limit ?? 0) + index)
      return {
        request_id: 'req', dataset_id: 'SHFE.AO2609.5m', data_revision: revision, generation_id: generation,
        price_scale: 1, coverage: { first_bar_index: indices[0], last_bar_index: indices.at(-1) },
        has_more_before: true, has_more_after: true, checksum: `sha256:${'b'.repeat(64)}`,
        bars: { bar_index: indices, timestamp_utc: indices.map((index) => 1_700_000_000_000 + index * 300_000),
          open_i64: indices, high_i64: indices.map((index) => index + 2), low_i64: indices.map((index) => index - 1),
          close_i64: indices, volume: indices, open_interest: indices.map(() => null) },
      }
    })
    const wideDataset = dataset()
    wideDataset.coverage = { ...wideDataset.coverage, bar_count: 10_000, last_bar_index: 9999 }
    const signal = { object_id: 'wide-trend', object_type: 'divergence', bar_index: 6000,
      time: 1_701_800_000_000, price_i64: 6000, confirmed_at_bar_index: 6020,
      known_at_bar_index: 6020, object_revision: 1,
      signal: { divergence_kind: 'trend', a_object_id: 'segment-a',
        comparison_reference_object_id: 'segment-b', comparison_current_object_id: 'segment-c' } } as never
    const a = { object_id: 'segment-a', start_bar_index: 0 } as never
    const reference = { object_id: 'segment-b', start_bar_index: 1000 } as never
    const current = { object_id: 'segment-c', end_bar_index: 6000 } as never
    const wrapper = mount(ChartGroup, { props: { dataset: wideDataset, selectedSignal: signal,
      selectedDivergenceSegments: { a, reference, current } } })
    await flushPromises()
    await (wrapper.vm as unknown as { focusSignal: (value: typeof signal) => Promise<void> }).focusSignal(signal)
    expect(wrapper.get('.chart-notice').text()).toContain('证据跨度超过单次 5000 根')
    await wrapper.setProps({ selectedSignal: null })
    expect(wrapper.find('.chart-notice').exists()).toBe(false)
    wrapper.unmount()
  })

  it('magnet mode snaps a horizontal line to the nearest bar OHLC anchor', async () => {
    const wrapper = mount(ChartGroup, { props: { dataset: dataset(), drawingTool: 'horizontal_line', magnet: true } })
    await flushPromises()
    wrapper.get('.drawing-capture').element.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: 1, clientY: 112 }))
    await wrapper.vm.$nextTick()
    const emitted = wrapper.emitted('update:drawings')?.at(-1)?.[0] as Array<{ anchors: Array<Record<string, number>> }>
    expect(emitted[0]?.anchors[0]).toEqual({ time: 1_700_000_000_000, price_i64: 11, price_scale: 1 })
    wrapper.unmount()
  })
})
