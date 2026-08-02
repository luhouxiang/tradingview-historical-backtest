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
  ColorType: { Solid: 'solid' }, CrosshairMode: { Normal: 0 }, LineStyle: { Dashed: 2 }, createChart: chartMocks.createChart,
}))
vi.mock('../api/client', () => apiMocks)

const revision = `sha256:${'a'.repeat(64)}`

function dataset(): DatasetMeta {
  return {
    request_id: 'req', dataset_id: 'SHFE.AO2609.5m', data_revision: revision,
    instrument: { exchange: 'SHFE', symbol: 'AO2609', product: 'AO' }, timeframe: '5m',
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
    vi.useRealTimers()
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
    expect(wrapper.get('[data-pane-id="volume"]').text()).toContain('成交量 4')
    const crosshairHandler = chartMocks.chart.subscribeCrosshairMove.mock.calls[0][0] as (parameter: object) => void
    crosshairHandler({ point: { x: 25, y: 40 }, logical: 0.2, seriesData: new Map() })
    await wrapper.vm.$nextTick()
    expect(wrapper.get('[data-pane-id="price"]').text()).toContain('开 10 高 12 低 9 收 11')
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

  it('queries a completed StrategySource into the single Chan primitive without recalculation', async () => {
    apiMocks.getCalculationResults.mockResolvedValue({
      result_kind: 'chan', objects: { fractals: [], bi: [], zhongshu: [] },
      coverage: { first_bar_index: 0, last_bar_index: 1, returned_count: 0 },
    })
    const source = {
      source_type: 'StrategySource' as const, source_id: 'strategy-1', job_id: 'job-chan', status: 'completed' as const,
      visible: true, category_visibility: { fractals: true, bi: true, zhongshu: true }, parameters: { min_fractal_gap: 5 },
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
    wrapper.unmount()
  })

  it('hides future bars when replay cursor moves without creating calculations', async () => {
    const wrapper = mount(ChartGroup, { props: { dataset: dataset(), replayCursor: 0, replayObjects: { fractals: [], bi: [], zhongshu: [] } } })
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
