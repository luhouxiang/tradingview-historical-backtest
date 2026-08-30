import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DatasetMeta } from '../types/api'
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
    coordinateToTime: vi.fn((coordinate: number) => 1_700_000_000 + coordinate),
  }
  const chart = {
    addSeries: vi.fn((_definition: unknown, _options: unknown, index: number) => [candle, macd, volume][index]),
    addCustomSeries: vi.fn((_definition: unknown, _options: unknown, index: number) => index === 2 ? volume : macd),
    panes: vi.fn(() => panes), timeScale: vi.fn(() => timeScale), removeSeries: vi.fn(), swapPanes: vi.fn(), remove: vi.fn(),
    subscribeCrosshairMove: vi.fn(), unsubscribeCrosshairMove: vi.fn(),
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

describe('ChartGroup', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getBars.mockImplementation(async (_dataset: string, _revision: string, generation: string) => ({
      request_id: 'req', dataset_id: 'SHFE.AO2609.5m', data_revision: revision, generation_id: generation,
      price_scale: 1, coverage: { first_bar_index: 0, last_bar_index: 1 }, has_more_before: false,
      checksum: `sha256:${'b'.repeat(64)}`,
      bars: { bar_index: [0, 1], timestamp_utc: [1_700_000_000_000, 1_700_000_300_000], open_i64: [10, 11], high_i64: [12, 13], low_i64: [9, 10], close_i64: [11, 10], volume: [3, 4], open_interest: [null, 5] },
    }))
  })

  it('uses one chart instance with price, MACD placeholder, and custom volume panes at 6:1:1', () => {
    const wrapper = mount(ChartGroup, { props: { dataset: null } })
    expect(chartMocks.createChart).toHaveBeenCalledTimes(1)
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

  it('prefetches exactly one 1500-bar page when the visible range nears the left cache edge', async () => {
    apiMocks.getBars.mockImplementation(async (_dataset: string, _revision: string, generation: string, options: { tail?: number; beforeBarIndex?: number }) => {
      const first = options.tail ? 3000 : 1500
      return {
        request_id: 'req', dataset_id: 'SHFE.AO2609.5m', data_revision: revision, generation_id: generation,
        price_scale: 1, coverage: { first_bar_index: first, last_bar_index: first + 1 }, has_more_before: true,
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
    expect(priceText.indexOf('收 10')).toBeLessThan(priceText.indexOf('K线 00002'))
    expect(priceText.indexOf('K线 00002')).toBeLessThan(priceText.indexOf('MA20 11.50'))
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

  it('projects causal risk decisions onto the price chart', async () => {
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
    ] })
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.replay-signal.approved_order_intent').text()).toBe('风控·订单意图批准')
    expect(wrapper.get('.replay-signal.kill_switch').text()).toBe('风控·熔断')
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
    expect(chartMocks.macd.createPriceLine).toHaveBeenCalledWith(expect.objectContaining({ price: 0, title: '0' }))
    const diffOptions = chartMocks.chart.addSeries.mock.calls.find((call) => (call[1] as { color?: string })?.color === '#e0e3eb')?.[1] as { autoscaleInfoProvider: (base: () => object) => object }
    expect(diffOptions.autoscaleInfoProvider(() => ({ priceRange: { minValue: -3, maxValue: 5 } }))).toEqual({
      priceRange: { minValue: -5, maxValue: 5 }, margins: { above: 6, below: 6 },
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

  it('queries a completed StrategySource into the single Chan primitive without recalculation', async () => {
    apiMocks.getCalculationResults.mockResolvedValue({
      result_kind: 'chan', objects: {
        processed_bars: [],
        fractals: [],
        bi: [{ object_id: 'bi-1', start_time: 1_700_000_000_000, start_price_i64: 10, end_time: 1_700_000_300_000, end_price_i64: 12, confirmed: true }],
        segments: [{ object_id: 'segment-1', start_time: 1_700_000_000_000, start_price_i64: 10, end_time: 1_700_000_300_000, end_price_i64: 12, confirmed: true }],
        zhongshu: [{ object_id: 'zs-1', start_time: 1_700_000_000_000, end_time: 1_700_000_300_000, zg_i64: 12, zd_i64: 10, confirmed: true }],
        bi_states: [], segment_zhongshu: [], movement_states: [], center_monitors: [], divergences: [], trade_points: [],
      },
      coverage: { first_bar_index: 0, last_bar_index: 1, returned_count: 2 },
    })
    const source = {
      source_type: 'StrategySource' as const, source_id: 'strategy-1', job_id: 'job-chan', status: 'completed' as const,
      visible: true, category_visibility: { fractals: false, bi: true, segments: true, zhongshu: true, segment_zhongshu: true, divergences: true, trade_points: true }, parameters: { min_fractal_gap: 5 },
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
    expect(wrapper.get('[data-pane-id="price"]').text()).toContain('缠论 笔 1 段 1 笔中枢 1 段中枢 0 背驰 0 买卖点 0')
    wrapper.unmount()
  })

  it('hides future bars when replay cursor moves without creating calculations', async () => {
    const wrapper = mount(ChartGroup, { props: { dataset: dataset(), replayCursor: 0, replayObjects: { processed_bars: [], fractals: [], bi: [], bi_states: [], segments: [], zhongshu: [], segment_zhongshu: [], level_centers: [], level_movements: [], movement_states: [], center_monitors: [], divergences: [], trade_points: [] } } })
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

  it('draws two endpoint handles for a selected signal and focuses it when requested', async () => {
    const selectedSignal = {
      object_id: 'signal-1', bar_index: 0, time: 1_700_000_000_000, price_i64: 11,
      signal_type: 'buy_1' as const, divergence_kind: null, signal_class: 'standard' as const, strength: null,
      reference_object_id: null, macd_area_reference: null, macd_area_current: null,
      confirmed: true, confirmed_at_bar_index: 1, known_at_bar_index: 1, object_revision: 1,
    }
    const wrapper = mount(ChartGroup, { props: { dataset: dataset(), selectedSignal, signalLocked: true } })
    await flushPromises()
    expect(wrapper.find('[data-selected-signal="true"]').classes()).toContain('locked')
    expect(wrapper.findAll('.signal-selection circle')).toHaveLength(2)
    chartMocks.timeScale.getVisibleLogicalRange.mockReturnValueOnce({ from: 0, to: 1 })
    await (wrapper.vm as unknown as { focusSignal: (signal: typeof selectedSignal) => Promise<void> }).focusSignal(selectedSignal)
    expect(apiMocks.getBars).toHaveBeenCalledTimes(1)
    await (wrapper.vm as unknown as { focusSignal: (signal: typeof selectedSignal) => Promise<void> }).focusSignal(selectedSignal)
    await flushPromises()
    expect(apiMocks.getBars).toHaveBeenLastCalledWith(
      'SHFE.AO2609.5m', revision, expect.stringMatching(/^gen-/), { beforeBarIndex: 2, limit: 2 },
    )
    expect(chartMocks.timeScale.setVisibleLogicalRange).toHaveBeenLastCalledWith({ from: 0, to: 1 })
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
