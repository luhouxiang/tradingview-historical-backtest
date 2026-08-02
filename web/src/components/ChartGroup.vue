<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineStyle,
  LineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LogicalRange,
  type MouseEventParams,
  type AutoscaleInfo,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts'
import { getCalculationResults } from '../api/client'
import { barAtLogicalIndex, formatBarTimeRange } from '../chart/crosshair'
import { ChartSession, type CachedBar } from '../chart/session'
import { ChanPrimitive } from '../chart/chanPrimitive'
import { HollowVolumeSeries } from '../chart/hollowVolumeSeries'
import { MacdStickSeries } from '../chart/macdStickSeries'
import { defaultPaneLayout, enforceMinimumHeights, removePane, resizeAdjacent, type PaneLayout } from '../chart/layout'
import { histogramColor, indicatorLineColor, MARKET_COLORS } from '../chart/marketStyle'
import { logger } from '../logging/logger'
import type { ReplayObjects, ReplaySignal } from '../replay/eventIndex'
import type { ChanCalculationResults, DatasetMeta, SeriesSource, StrategySource } from '../types/api'
import { cloneDrawings, LayerManager, type DrawingAnchor, type DrawingObject, type DrawingType, type ProjectedDrawing } from '../drawing/model'

const props = withDefaults(defineProps<{
  dataset: DatasetMeta | null
  indicatorSources?: SeriesSource[]
  strategySources?: StrategySource[]
  replayCursor?: number | null
  replayObjects?: ReplayObjects | null
  replaySignals?: ReplaySignal[]
  drawings?: DrawingObject[]
  selectedDrawingId?: string | null
  drawingTool?: DrawingType | 'cursor'
  magnet?: boolean
  keepDrawingMode?: boolean
}>(), {
  indicatorSources: () => [],
  strategySources: () => [],
  replayCursor: null,
  replayObjects: null,
  replaySignals: () => [],
  drawings: () => [],
  selectedDrawingId: null,
  drawingTool: 'cursor',
  magnet: false,
  keepDrawingMode: false,
})
const emit = defineEmits<{
  'update:drawings': [drawings: DrawingObject[]]
  'update:selectedDrawingId': [id: string | null]
  'update:drawingTool': [tool: DrawingType | 'cursor']
}>()

const host = ref<HTMLElement | null>(null)
const panes = ref<PaneLayout[]>(defaultPaneLayout())
const loading = ref(false)
const error = ref('')
const cacheFirstIndex = ref<number | null>(null)
const cacheBarCount = ref(0)
const collapsed = ref(new Set<string>())
const maximized = ref<string | null>(null)
const chartHeight = ref(800)
const projectedDrawings = ref<ProjectedDrawing[]>([])
const pendingAnchor = ref<DrawingAnchor | null>(null)
const previewPoint = ref<{ x: number; y: number } | null>(null)
const latestIndicatorValues = ref<Record<string, number>>({})
const latestVolume = ref<number | null>(null)
const latestBar = ref<CachedBar | null>(null)
const hoveredBar = ref<CachedBar | null>(null)
const crosshairActive = ref(false)
const session = new ChartSession()
const layerManager = new LayerManager()
let chart: IChartApi | null = null
let candles: ISeriesApi<'Candlestick'> | null = null
let macdPlaceholder: ISeriesApi<'Histogram'> | null = null
let volume: ISeriesApi<'Custom'> | null = null
let prefetchTimer: number | undefined
let indicatorTimer: number | undefined
let dragCleanup: (() => void) | null = null
let savedWeights: number[] | null = null
let resizeObserver: ResizeObserver | null = null
let dragDrawings: DrawingObject[] | null = null
const indicatorSeries = new Map<string, ISeriesApi<'Line'> | ISeriesApi<'Histogram'> | ISeriesApi<'Custom'>>()
const indicatorValuesByBarIndex = new Map<number, Record<string, number>>()
const chanPrimitive = new ChanPrimitive()
const pendingProjected = computed(() => {
  if (!pendingAnchor.value || !chart || !candles) return null
  const x = chart.timeScale().timeToCoordinate(Math.floor(pendingAnchor.value.time / 1000) as UTCTimestamp)
  const y = candles.priceToCoordinate(pendingAnchor.value.price_i64 / pendingAnchor.value.price_scale)
  return x === null || y === null ? null : { x, y }
})
const projectedReplaySignals = computed(() => props.replaySignals.flatMap((signal) => {
  if (signal.object_type !== 'chart_event' || !chart || !candles) return []
  const timestamp = Number(signal.timestamp_utc)
  const price = Number(signal.price_i64)
  if (!Number.isFinite(timestamp) || !Number.isFinite(price) || !props.dataset) return []
  const x = chart.timeScale().timeToCoordinate(Math.floor(timestamp / 1000) as UTCTimestamp)
  const y = candles.priceToCoordinate(price / props.dataset.price.price_scale)
  return x === null || y === null ? [] : [{ x, y, type: String(signal.event_type), id: signal.object_id }]
}))

const effectivePanes = computed(() => enforceMinimumHeights(panes.value.map((pane) => ({
  ...pane,
  minHeight: collapsed.value.has(pane.id) || maximized.value !== null && maximized.value !== pane.id ? 24 : pane.minHeight,
})), chartHeight.value))
const splitterPositions = computed(() => {
  const total = effectivePanes.value.reduce((sum, pane) => sum + pane.weight, 0)
  let cumulative = 0
  return effectivePanes.value.slice(0, -1).map((pane) => {
    cumulative += pane.weight
    return (cumulative / total) * chartHeight.value
  })
})
const maLegendItems = computed(() => props.indicatorSources
  .filter((source) => source.status === 'completed' && source.definition.algorithm_id === 'ma')
  .map((source) => ({
    key: `${source.source_id}:ma`,
    period: Number(source.parameters.period),
    color: indicatorLineColor(source, 'ma'),
  }))
  .sort((left, right) => left.period - right.period))
const macdSource = computed(() => props.indicatorSources.find((source) => source.status === 'completed' && source.definition.algorithm_id === 'macd') ?? null)

function paneControlTop(index: number): string {
  return index === 0 ? '6px' : `${(splitterPositions.value[index - 1] ?? 0) + 5}px`
}

function legendValue(key: string): number | null {
  if (crosshairActive.value) {
    if (!hoveredBar.value) return null
    return indicatorValuesByBarIndex.get(hoveredBar.value.barIndex)?.[key] ?? null
  }
  return latestIndicatorValues.value[key] ?? null
}

function formatLegendValue(value: number | null): string {
  return value === null || !Number.isFinite(value) ? '--' : value.toFixed(2)
}

function macdParameters(): string {
  const source = macdSource.value
  return source
    ? `${source.parameters.fast_period},${source.parameters.slow_period},${source.parameters.signal_period}`
    : '12,26,9'
}

function macdLegendValue(outputName: string): number | null {
  return macdSource.value ? legendValue(`${macdSource.value.source_id}:${outputName}`) : null
}

function legendBar(): CachedBar | null {
  return crosshairActive.value ? hoveredBar.value : latestBar.value
}

function formatPrice(value: number | undefined): string {
  if (value === undefined || !props.dataset) return '--'
  return (value / props.dataset.price.price_scale).toFixed(props.dataset.price.price_decimals)
}

function symmetricAutoscale(baseImplementation: () => AutoscaleInfo | null): AutoscaleInfo | null {
  const info = baseImplementation()
  if (!info?.priceRange) return info
  const extent = Math.max(Math.abs(info.priceRange.minValue), Math.abs(info.priceRange.maxValue), Number.EPSILON)
  return {
    ...info,
    priceRange: { minValue: -extent, maxValue: extent },
    margins: { above: 6, below: 6 },
  }
}

function applyWeights(): void {
  if (!chart) return
  const chartPanes = chart.panes()
  effectivePanes.value.forEach((pane, index) => chartPanes[index]?.setStretchFactor(pane.weight))
}

function renderBars(): void {
  if (!candles || !volume || !props.dataset) return
  const scale = props.dataset.price.price_scale
  const cachedBars = session.bars
  const bars = props.replayCursor === null
    ? cachedBars
    : cachedBars.filter((bar) => bar.barIndex <= props.replayCursor!)
  cacheFirstIndex.value = cachedBars[0]?.barIndex ?? null
  cacheBarCount.value = cachedBars.length
  candles.setData(bars.map((bar) => ({
    time: Math.floor(bar.timestampUtc / 1000) as UTCTimestamp,
    open: bar.openI64 / scale,
    high: bar.highI64 / scale,
    low: bar.lowI64 / scale,
    close: bar.closeI64 / scale,
    color: bar.closeI64 >= bar.openI64 ? MARKET_COLORS.background : MARKET_COLORS.falling,
    borderColor: bar.closeI64 >= bar.openI64 ? MARKET_COLORS.rising : MARKET_COLORS.falling,
    wickColor: bar.closeI64 >= bar.openI64 ? MARKET_COLORS.rising : MARKET_COLORS.falling,
  })))
  volume.setData(bars.map((bar) => ({
    time: Math.floor(bar.timestampUtc / 1000) as UTCTimestamp,
    value: bar.volume,
    rising: bar.closeI64 >= bar.openI64,
  })))
  latestVolume.value = bars.at(-1)?.volume ?? null
  latestBar.value = bars.at(-1) ?? null
  projectDrawings()
}

function activeDrawings(): DrawingObject[] { return dragDrawings ?? props.drawings }

function projectDrawings(): void {
  if (!chart || !candles) return
  layerManager.replace(activeDrawings())
  projectedDrawings.value = layerManager.ordered().flatMap((drawing) => {
    const points = drawing.anchors.flatMap((anchor) => {
      const x = chart?.timeScale().timeToCoordinate(Math.floor(anchor.time / 1000) as UTCTimestamp)
      const y = candles?.priceToCoordinate(anchor.price_i64 / anchor.price_scale)
      return x === null || x === undefined || y === null || y === undefined ? [] : [{ x, y }]
    })
    return points.length === drawing.anchors.length ? [{ drawing, points }] : []
  })
}

function anchorFromClient(clientX: number, clientY: number): DrawingAnchor | null {
  if (!host.value || !chart || !candles || !props.dataset) return null
  const bounds = host.value.getBoundingClientRect()
  const x = clientX - bounds.left
  const y = clientY - bounds.top
  const time = chart.timeScale().coordinateToTime(x)
  const price = candles.coordinateToPrice(y)
  if (typeof time !== 'number' || price === null) return null
  let timestamp = time * 1000
  let snappedPrice: number = price
  if (props.magnet && session.bars.length > 0) {
    const bar = session.bars.reduce((closest, candidate) => Math.abs(candidate.timestampUtc - timestamp) < Math.abs(closest.timestampUtc - timestamp) ? candidate : closest)
    timestamp = bar.timestampUtc
    const scale = props.dataset.price.price_scale
    const candidates = [bar.openI64, bar.highI64, bar.lowI64, bar.closeI64]
    snappedPrice = candidates.reduce((closest, candidate) => Math.abs(candidate / scale - price) < Math.abs(closest / scale - price) ? candidate : closest) / scale
  }
  const scale = props.dataset.price.price_scale
  return { time: timestamp, price_i64: Math.round(snappedPrice * scale), price_scale: scale }
}

function finishDrawing(type: DrawingType, anchors: DrawingAnchor[]): void {
  const now = new Date().toISOString()
  const sequence = props.drawings.length + 1
  const labels: Record<DrawingType, string> = { trend_line: '趋势线', horizontal_line: '水平线', rectangle: '矩形', text: '文字', measure: '测量' }
  const drawing: DrawingObject = {
    id: `drawing-${crypto.randomUUID()}`, name: `${labels[type]} ${sequence}`, type,
    pane_id: 'main', visible: true, locked: false, z_band: 600,
    order_in_band: Math.max(-1, ...props.drawings.map((item) => item.order_in_band)) + 1,
    style: { color: '#2962ff', line_width: 1, fill_opacity: type === 'rectangle' ? .15 : 0 },
    anchors, text: type === 'text' ? '文本' : undefined, revision: 1, created_at: now, updated_at: now,
  }
  emit('update:drawings', [...props.drawings, drawing])
  emit('update:selectedDrawingId', drawing.id)
  pendingAnchor.value = null
  previewPoint.value = null
  if (!props.keepDrawingMode) emit('update:drawingTool', 'cursor')
}

function createDrawing(event: PointerEvent): void {
  if (props.drawingTool === 'cursor') return
  const anchor = anchorFromClient(event.clientX, event.clientY)
  if (!anchor) return
  if (props.drawingTool === 'horizontal_line' || props.drawingTool === 'text') {
    finishDrawing(props.drawingTool, [anchor])
  } else if (!pendingAnchor.value) {
    pendingAnchor.value = anchor
  } else {
    finishDrawing(props.drawingTool, [pendingAnchor.value, anchor])
  }
}

function drawingPointerMove(event: PointerEvent): void {
  if (!pendingAnchor.value || !chart || !candles) return
  const anchor = anchorFromClient(event.clientX, event.clientY)
  if (!anchor) return
  const x = chart.timeScale().timeToCoordinate(Math.floor(anchor.time / 1000) as UTCTimestamp)
  const y = candles.priceToCoordinate(anchor.price_i64 / anchor.price_scale)
  if (x !== null && y !== null) previewPoint.value = { x, y }
}

function selectDrawing(drawing: DrawingObject, event: PointerEvent): void {
  event.stopPropagation()
  emit('update:selectedDrawingId', drawing.id)
}

function beginHandleDrag(drawing: DrawingObject, handle: number, event: PointerEvent): void {
  event.stopPropagation()
  if (drawing.locked) return
  const preview = cloneDrawings(props.drawings)
  const target = preview.find((item) => item.id === drawing.id)
  if (!target) return
  dragDrawings = preview
  const move = (next: PointerEvent) => {
    const anchor = anchorFromClient(next.clientX, next.clientY)
    if (!anchor || !target.anchors[handle]) return
    target.anchors[handle] = anchor
    target.updated_at = new Date().toISOString()
    target.revision += 1
    projectDrawings()
  }
  const finish = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', finish)
    const committed = dragDrawings
    dragDrawings = null
    if (committed) emit('update:drawings', committed)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', finish)
}

function crosshairMoved(parameter: MouseEventParams): void {
  crosshairActive.value = parameter.point !== undefined
  if (!crosshairActive.value) {
    hoveredBar.value = null
    return
  }
  const bars = props.replayCursor === null
    ? session.bars
    : session.bars.filter((bar) => bar.barIndex <= props.replayCursor!)
  hoveredBar.value = barAtLogicalIndex(bars, parameter.logical === undefined ? null : Number(parameter.logical))
}

function removeStaleIndicatorSeries(): void {
  if (!chart) return
  const wanted = new Set(props.indicatorSources.flatMap((source) => source.definition.outputs.map((output) => `${source.source_id}:${output.name}`)))
  for (const [key, series] of indicatorSeries) {
    if (!wanted.has(key)) {
      chart.removeSeries(series)
      indicatorSeries.delete(key)
    }
  }
}

async function renderIndicators(fromBarIndex: number, toBarIndex: number): Promise<void> {
  if (!chart || !props.dataset || toBarIndex < fromBarIndex) return
  removeStaleIndicatorSeries()
  const times = new Map(session.bars.map((bar) => [bar.barIndex, Math.floor(bar.timestampUtc / 1000) as UTCTimestamp]))
  await Promise.all(props.indicatorSources.filter((source) => source.status === 'completed').map(async (source) => {
    const result = await getCalculationResults(source.job_id, fromBarIndex, toBarIndex)
    if (result.result_kind !== 'indicator') return
    source.definition.outputs.forEach((output) => {
      const key = `${source.source_id}:${output.name}`
      let series = indicatorSeries.get(key)
      if (!series) {
        const paneIndex = output.pane === 'main' ? 0 : 1
        series = output.series_type === 'histogram'
          ? chart?.addCustomSeries(new MacdStickSeries(), { autoscaleInfoProvider: symmetricAutoscale, priceLineVisible: false, lastValueVisible: false }, paneIndex)
          : chart?.addSeries(LineSeries, {
            color: indicatorLineColor(source, output.name), lineWidth: 1, priceLineVisible: false,
            ...(output.pane === 'indicator' ? { autoscaleInfoProvider: symmetricAutoscale } : {}),
          }, paneIndex)
        if (series) {
          indicatorSeries.set(key, series)
          if (output.pane === 'indicator') {
            series.priceScale().applyOptions({ autoScale: true, scaleMargins: { top: 0.1, bottom: 0.1 } })
          }
          if (output.series_type === 'histogram') {
            series.createPriceLine({
              price: 0, color: '#8b2b31', lineWidth: 1, lineStyle: LineStyle.Dashed,
              lineVisible: true, axisLabelVisible: true, title: '0',
            })
          }
        }
      }
      const values = result.values[output.name] ?? []
      const points = result.bar_index.flatMap((barIndex, index) => {
        if (props.replayCursor !== null && barIndex > props.replayCursor) return []
        const time = times.get(barIndex)
        const value = values[index]
        if (time === undefined || value === null || value === undefined) return []
        const row = indicatorValuesByBarIndex.get(barIndex) ?? {}
        row[key] = value
        indicatorValuesByBarIndex.set(barIndex, row)
        return [{ time, value, ...(output.series_type === 'histogram' ? { color: histogramColor(value) } : {}) }]
      })
      series?.setData(points)
      const lastValue = points.at(-1)?.value
      if (typeof lastValue === 'number') latestIndicatorValues.value = { ...latestIndicatorValues.value, [key]: lastValue }
    })
  }))
}

async function renderChan(fromBarIndex: number, toBarIndex: number): Promise<void> {
  if (!props.dataset || toBarIndex < fromBarIndex) return
  if (props.replayObjects !== null) {
    const source = props.strategySources.find((value) => value.status === 'completed')
    const filtered: ReplayObjects = {
      fractals: source?.visible && source.category_visibility.fractals ? props.replayObjects.fractals : [],
      bi: source?.visible && source.category_visibility.bi ? props.replayObjects.bi : [],
      zhongshu: source?.visible && source.category_visibility.zhongshu ? props.replayObjects.zhongshu : [],
    }
    chanPrimitive.setData(filtered, props.dataset.price.price_scale)
    return
  }
  const sources = props.strategySources.filter((source) => source.status === 'completed' && source.visible)
  const merged: ChanCalculationResults['objects'] = { fractals: [], bi: [], zhongshu: [] }
  await Promise.all(sources.map(async (source) => {
    const result = await getCalculationResults(source.job_id, fromBarIndex, toBarIndex)
    if (result.result_kind !== 'chan') return
    if (source.category_visibility.fractals) merged.fractals.push(...result.objects.fractals)
    if (source.category_visibility.bi) merged.bi.push(...result.objects.bi)
    if (source.category_visibility.zhongshu) merged.zhongshu.push(...result.objects.zhongshu)
  }))
  chanPrimitive.setData(merged, props.dataset.price.price_scale)
}

function scheduleIndicatorRange(range: LogicalRange): void {
  window.clearTimeout(indicatorTimer)
  indicatorTimer = window.setTimeout(() => {
    const bars = props.replayCursor === null
      ? session.bars
      : session.bars.filter((bar) => bar.barIndex <= props.replayCursor!)
    if (bars.length === 0) return
    const first = Math.max(0, Math.floor(range.from))
    const last = Math.min(bars.length - 1, Math.ceil(range.to))
    const from = bars[first]?.barIndex
    const to = bars[last]?.barIndex
    if (from !== undefined && to !== undefined) void Promise.all([renderIndicators(from, to), renderChan(from, to)])
  }, 150)
}

async function openDataset(meta: DatasetMeta): Promise<void> {
  loading.value = true
  error.value = ''
  indicatorValuesByBarIndex.clear()
  latestIndicatorValues.value = {}
  hoveredBar.value = null
  crosshairActive.value = false
  try {
    await session.open(meta)
    if (session.meta?.dataset_id !== meta.dataset_id || session.meta.data_revision !== meta.data_revision) return
    candles?.applyOptions({
      priceFormat: { type: 'price', precision: meta.price.price_decimals, minMove: 1 / meta.price.price_scale },
    })
    renderBars()
    const loaded = session.bars.length
    chart?.timeScale().setVisibleLogicalRange({ from: Math.max(0, loaded - 300), to: loaded + 5 })
    const first = session.bars[Math.max(0, loaded - 300)]?.barIndex
    const last = session.bars[loaded - 1]?.barIndex
    if (first !== undefined && last !== undefined) await Promise.all([renderIndicators(first, last), renderChan(first, last)])
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'K 线加载失败'
    logger.error('ui.error', 'K-line load failed', { reason: error.value })
  } finally {
    loading.value = false
  }
}

function visibleRangeChanged(range: LogicalRange | null): void {
  if (!range) return
  projectDrawings()
  scheduleIndicatorRange(range)
  if (props.replayCursor !== null || !session.hasMoreBefore) return
  const screen = Math.max(1, range.to - range.from)
  if (range.from > screen) return
  window.clearTimeout(prefetchTimer)
  prefetchTimer = window.setTimeout(async () => {
    const preserved = chart?.timeScale().getVisibleLogicalRange()
    try {
      const added = await session.prefetchBefore()
      if (added < 1) return
      renderBars()
      if (preserved) {
        scheduleIndicatorRange(preserved)
        chart?.timeScale().setVisibleLogicalRange({ from: preserved.from + added, to: preserved.to + added })
      }
    } catch (cause) {
      logger.error('ui.error', 'K-line prefetch failed', { reason: cause instanceof Error ? cause.message : 'unknown' })
    }
  }, 150)
}

function beginPaneResize(index: number, event: PointerEvent): void {
  if (!host.value || maximized.value) return
  event.preventDefault()
  const startY = event.clientY
  const initial = effectivePanes.value.map((pane) => ({ ...pane }))
  const totalHeight = Math.max(400, host.value.clientHeight - 30)
  const move = (next: PointerEvent) => {
    panes.value = resizeAdjacent(initial, index, next.clientY - startY, totalHeight)
    applyWeights()
  }
  const finish = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', finish)
    dragCleanup = null
  }
  dragCleanup = finish
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', finish)
}

function resetPanes(): void {
  panes.value = defaultPaneLayout().filter((pane) => panes.value.some((current) => current.id === pane.id))
  collapsed.value = new Set()
  maximized.value = null
  savedWeights = null
  applyWeights()
}

function toggleCollapse(paneId: string): void {
  const next = new Set(collapsed.value)
  const pane = panes.value.find((candidate) => candidate.id === paneId)
  if (!pane || pane.kind === 'price') return
  if (next.has(paneId)) {
    next.delete(paneId)
    const defaults = defaultPaneLayout().find((candidate) => candidate.id === paneId)
    pane.weight = defaults?.weight ?? 1
  } else {
    next.add(paneId)
    pane.weight = 0.08
  }
  panes.value = panes.value.map((candidate) => ({ ...candidate }))
  collapsed.value = next
  applyWeights()
}

function toggleMaximize(paneId: string): void {
  if (maximized.value === paneId && savedWeights) {
    panes.value = panes.value.map((pane, index) => ({ ...pane, weight: savedWeights?.[index] ?? pane.weight }))
    maximized.value = null
    savedWeights = null
  } else {
    savedWeights = panes.value.map((pane) => pane.weight)
    panes.value = panes.value.map((pane) => ({ ...pane, weight: pane.id === paneId ? 100 : 0.08 }))
    maximized.value = paneId
  }
  applyWeights()
}

function deletePane(paneId: string): void {
  const pane = panes.value.find((candidate) => candidate.id === paneId)
  if (!chart || !pane || pane.kind === 'price') return
  if (paneId === 'macd' && macdPlaceholder) {
    chart.removeSeries(macdPlaceholder)
    macdPlaceholder = null
  }
  if (paneId === 'volume' && volume) {
    chart.removeSeries(volume)
    volume = null
  }
  panes.value = removePane(panes.value, paneId)
  applyWeights()
}

function movePane(paneId: string, direction: -1 | 1): void {
  if (!chart) return
  const index = panes.value.findIndex((pane) => pane.id === paneId)
  const target = index + direction
  if (index < 1 || target < 1 || target >= panes.value.length) return
  chart.swapPanes(index, target)
  const next = panes.value.map((pane) => ({ ...pane }))
  ;[next[index], next[target]] = [next[target], next[index]]
  panes.value = next
  applyWeights()
}

onMounted(() => {
  if (!host.value) return
  chart = createChart(host.value, {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: '#131722' },
      textColor: '#b2b5be',
      panes: { enableResize: false, separatorColor: '#2a2e39', separatorHoverColor: '#363c4e' },
    },
    grid: { vertLines: { color: '#1c2030' }, horzLines: { color: '#1c2030' } },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: {
        color: '#758696', width: 1, style: LineStyle.Dashed,
        visible: true, labelVisible: true, labelBackgroundColor: '#8b2b31',
      },
      horzLine: {
        color: '#758696', width: 1, style: LineStyle.Dashed,
        visible: true, labelVisible: true, labelBackgroundColor: '#8b2b31',
      },
    },
    localization: {
      timeFormatter: (time: Time) => typeof time === 'number' && props.dataset
        ? formatBarTimeRange(
          time * 1000,
          props.dataset.timeframe,
          props.dataset.time.timezone,
          props.dataset.source.timestamp_semantics ?? 'bar_end',
        )
        : '',
    },
    rightPriceScale: { borderColor: '#2a2e39' },
    timeScale: { borderColor: '#2a2e39', timeVisible: true, secondsVisible: false },
  })
  candles = chart.addSeries(CandlestickSeries, {
    upColor: MARKET_COLORS.background,
    downColor: MARKET_COLORS.falling,
    borderVisible: true,
    borderUpColor: MARKET_COLORS.rising,
    borderDownColor: MARKET_COLORS.falling,
    wickUpColor: MARKET_COLORS.rising,
    wickDownColor: MARKET_COLORS.falling,
  }, 0)
  candles.attachPrimitive(chanPrimitive)
  macdPlaceholder = chart.addSeries(HistogramSeries, { color: '#787b86', priceLineVisible: false, lastValueVisible: false }, 1)
  volume = chart.addCustomSeries(new HollowVolumeSeries(), { priceFormat: { type: 'volume' }, priceLineVisible: false, lastValueVisible: false }, 2)
  volume.priceScale().applyOptions({ autoScale: true, scaleMargins: { top: 0.15, bottom: 0.02 } })
  applyWeights()
  chart.timeScale().subscribeVisibleLogicalRangeChange(visibleRangeChanged)
  chart.subscribeCrosshairMove(crosshairMoved)
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(([entry]) => {
      if (!entry) return
      chartHeight.value = Math.max(400, entry.contentRect.height - 30)
      applyWeights()
      projectDrawings()
    })
    resizeObserver.observe(host.value)
  }
  if (props.dataset) void openDataset(props.dataset)
})

watch(() => props.dataset, (next) => {
  if (next && chart) void openDataset(next)
})

watch(() => props.indicatorSources, () => {
  removeStaleIndicatorSeries()
  const range = chart?.timeScale().getVisibleLogicalRange()
  if (range) scheduleIndicatorRange(range)
}, { deep: true })

watch(() => props.strategySources, () => {
  const range = chart?.timeScale().getVisibleLogicalRange()
  if (range) scheduleIndicatorRange(range)
}, { deep: true })

watch(() => [props.replayCursor, props.replayObjects], () => {
  renderBars()
  const range = chart?.timeScale().getVisibleLogicalRange()
  if (range) scheduleIndicatorRange(range)
}, { deep: true })

watch(() => props.drawings, () => projectDrawings(), { deep: true })

defineExpose({
  snapshotLayout: () => ({
    panes: panes.value.map((pane, order) => ({ ...pane, order, visible: true, collapsed: collapsed.value.has(pane.id) })),
  }),
  restoreLayout: (value: { panes?: Array<PaneLayout & { collapsed?: boolean }> }) => {
    if (!value.panes?.length) return
    panes.value = value.panes.map(({ collapsed: _collapsed, ...pane }) => pane)
    collapsed.value = new Set(value.panes.filter((pane) => pane.collapsed).map((pane) => pane.id))
    applyWeights()
  },
})

onBeforeUnmount(() => {
  window.clearTimeout(prefetchTimer)
  window.clearTimeout(indicatorTimer)
  dragCleanup?.()
  resizeObserver?.disconnect()
  if (chart) {
    chart.timeScale().unsubscribeVisibleLogicalRangeChange(visibleRangeChanged)
    chart.unsubscribeCrosshairMove(crosshairMoved)
    candles?.detachPrimitive(chanPrimitive)
    chart.remove()
  }
})
</script>

<template>
  <section
    class="chart-group"
    aria-label="K 线多窗格图表"
    :data-cache-first-index="cacheFirstIndex ?? ''"
    :data-cache-bar-count="cacheBarCount"
  >
    <div ref="host" class="chart-host" />
    <svg class="drawing-layer" aria-label="用户绘图图层" @pointermove="drawingPointerMove">
      <rect v-if="drawingTool !== 'cursor'" class="drawing-capture" width="100%" height="100%" @pointerdown="createDrawing" />
      <g v-for="signal in projectedReplaySignals" :key="signal.id" class="replay-signal" :class="signal.type">
        <path :d="`M ${signal.x} ${signal.y} l -5 9 h 10 z`" />
        <text :x="signal.x + 7" :y="signal.y + 9">{{ signal.type }}</text>
      </g>
      <g v-for="item in projectedDrawings" :key="item.drawing.id" class="drawing-object" :data-drawing-id="item.drawing.id" @pointerdown="selectDrawing(item.drawing, $event)">
        <rect
          v-if="item.drawing.type === 'rectangle' && item.points[0] && item.points[1]"
          :x="Math.min(item.points[0].x, item.points[1].x)" :y="Math.min(item.points[0].y, item.points[1].y)"
          :width="Math.abs(item.points[1].x - item.points[0].x)" :height="Math.abs(item.points[1].y - item.points[0].y)"
          :stroke="item.drawing.style.color" :stroke-width="item.drawing.style.line_width"
          :fill="item.drawing.style.color" :fill-opacity="item.drawing.style.fill_opacity"
        />
        <line
          v-else-if="item.drawing.type === 'horizontal_line' && item.points[0]"
          x1="0" :x2="host?.clientWidth ?? 0" :y1="item.points[0].y" :y2="item.points[0].y"
          :stroke="item.drawing.style.color" :stroke-width="item.drawing.style.line_width"
        />
        <text v-else-if="item.drawing.type === 'text' && item.points[0]" :x="item.points[0].x" :y="item.points[0].y" :fill="item.drawing.style.color">{{ item.drawing.text }}</text>
        <template v-else-if="item.points[0] && item.points[1]">
          <line :x1="item.points[0].x" :y1="item.points[0].y" :x2="item.points[1].x" :y2="item.points[1].y" :stroke="item.drawing.style.color" :stroke-width="item.drawing.style.line_width" />
          <text v-if="item.drawing.type === 'measure'" :x="item.points[1].x + 5" :y="item.points[1].y - 5" :fill="item.drawing.style.color">
            {{ ((item.drawing.anchors[1]!.price_i64 / item.drawing.anchors[1]!.price_scale) - (item.drawing.anchors[0]!.price_i64 / item.drawing.anchors[0]!.price_scale)).toFixed(2) }}
          </text>
        </template>
      </g>
      <line
        v-if="pendingProjected && previewPoint"
        class="drawing-preview"
        :x1="pendingProjected.x"
        :y1="pendingProjected.y"
        :x2="previewPoint.x" :y2="previewPoint.y"
      />
      <template v-for="item in projectedDrawings" :key="`handles-${item.drawing.id}`">
        <circle
          v-for="(point, handle) in selectedDrawingId === item.drawing.id ? item.points : []" :key="handle"
          class="drawing-handle" :class="{ locked: item.drawing.locked }" :cx="point.x" :cy="point.y" r="5"
          @pointerdown="beginHandleDrag(item.drawing, handle, $event)"
        />
      </template>
    </svg>
    <div v-if="!dataset" class="chart-empty">
      <div class="watermark">TVBT</div>
      <p>选择历史数据集后开始</p>
      <small>单图表 · 共享时间轴 · 独立纵轴</small>
    </div>
    <div v-if="loading" class="chart-status">正在加载尾部 3000 根…</div>
    <div v-if="error" class="chart-status chart-error">{{ error }}</div>
    <div class="pane-controls" aria-label="窗格控制">
      <div
        v-for="(pane, index) in panes" :key="pane.id" class="pane-control"
        :style="{ top: paneControlTop(index) }" :data-pane-id="pane.id" :data-weight="pane.weight"
      >
        <strong v-if="pane.id === 'price'">{{ dataset?.instrument.symbol ?? '主图' }}</strong>
        <strong v-else-if="pane.id === 'macd'" class="legend-macd-title">MACD({{ macdParameters() }})</strong>
        <strong v-else>{{ pane.id.toUpperCase() }}</strong>
        <template v-if="pane.id === 'price'">
          <span class="legend-value legend-ohlc">
            开 {{ formatPrice(legendBar()?.openI64) }} 高 {{ formatPrice(legendBar()?.highI64) }}
            低 {{ formatPrice(legendBar()?.lowI64) }} 收 {{ formatPrice(legendBar()?.closeI64) }}
          </span>
          <span v-for="item in maLegendItems" :key="item.key" class="legend-value" :style="{ color: item.color }">
            MA{{ item.period }} {{ formatLegendValue(legendValue(item.key)) }}
          </span>
        </template>
        <template v-else-if="pane.id === 'macd'">
          <span class="legend-value legend-diff">DIFF {{ formatLegendValue(macdLegendValue('macd')) }}</span>
          <span class="legend-value legend-dea">DEA {{ formatLegendValue(macdLegendValue('signal')) }}</span>
          <span class="legend-value" :style="{ color: macdLegendValue('histogram') === null ? '#787b86' : histogramColor(macdLegendValue('histogram')!) }">
            MACD {{ formatLegendValue(macdLegendValue('histogram')) }}
          </span>
        </template>
        <span v-else-if="pane.id === 'volume'" class="legend-value legend-volume">成交量 {{ crosshairActive ? (hoveredBar?.volume ?? '--') : (latestVolume ?? '--') }}</span>
        <span class="pane-actions">
          <button :disabled="pane.kind === 'price'" @click="toggleCollapse(pane.id)">{{ collapsed.has(pane.id) ? '展开' : '折叠' }}</button>
          <button @click="toggleMaximize(pane.id)">{{ maximized === pane.id ? '还原' : '最大化' }}</button>
          <button :disabled="pane.kind === 'price' || index === 1" @click="movePane(pane.id, -1)">上移</button>
          <button :disabled="pane.kind === 'price' || index === panes.length - 1" @click="movePane(pane.id, 1)">下移</button>
          <button :disabled="pane.kind === 'price'" @click="deletePane(pane.id)">删除</button>
        </span>
      </div>
    </div>
    <button
      v-for="(top, index) in splitterPositions"
      :key="`splitter-${index}`"
      class="pane-splitter"
      :style="{ top: `${top}px` }"
      :aria-label="`调整窗格 ${index + 1} 和 ${index + 2}`"
      @pointerdown="beginPaneResize(index, $event)"
      @dblclick="resetPanes"
    />
  </section>
</template>
