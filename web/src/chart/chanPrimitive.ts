import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  Time,
  UTCTimestamp,
} from 'lightweight-charts'
import type { ChanCalculationResults, ChanFractal, ChanLineObject, ChanSignalPoint, ChanZhongshu } from '../types/api'
import type { IndicatorOutputStyle, IndicatorStyle } from '../types/api'
import { canvasDash, colorWithOpacity } from '../indicators/style'

type ChanObjects = ChanCalculationResults['objects']
type Point = { x: number; y: number }
type Line = { start: Point; end: Point; confirmed: boolean }
type FractalPoint = Point & Pick<ChanFractal, 'fractal_type' | 'confirmed'>
type Region = { left: number; right: number; top: number; bottom: number; confirmed: boolean }
type SignalPoint = Point & Pick<ChanSignalPoint, 'signal_type' | 'divergence_kind' | 'signal_class' | 'strength'>

export interface ChanGeometry {
  fractals: FractalPoint[]
  bi: Line[]
  segments: Line[]
  zhongshu: Region[]
  segmentZhongshu: Region[]
  divergences: SignalPoint[]
  tradePoints: SignalPoint[]
}

interface ChanRenderStyle {
  fractal?: IndicatorOutputStyle
  bi: IndicatorOutputStyle
  segment: IndicatorOutputStyle
  zhongshu: IndicatorOutputStyle
  segmentZhongshu: IndicatorOutputStyle
  divergence: IndicatorOutputStyle
  tradePoint: IndicatorOutputStyle
}

const defaultChanRenderStyle: ChanRenderStyle = {
  bi: { color: '#2962ff', line_width: 2, line_style: 'solid', opacity: 1, visible: true },
  segment: { color: '#f2d600', line_width: 2, line_style: 'solid', opacity: 1, visible: true },
  zhongshu: { color: '#64b5f6', line_width: 1, line_style: 'solid', opacity: 1, visible: true },
  segmentZhongshu: { color: '#fff176', line_width: 2, line_style: 'solid', opacity: 1, visible: true },
  divergence: { color: '#ff9800', line_width: 1, line_style: 'solid', opacity: 1, visible: true },
  tradePoint: { color: '#ffffff', line_width: 1, line_style: 'solid', opacity: 1, visible: true },
}

export function buildChanGeometry(
  objects: ChanObjects,
  priceScale: number,
  timeToX: (time: UTCTimestamp) => number | null,
  priceToY: (price: number) => number | null,
): ChanGeometry {
  const point = (timeMs: number, priceI64: number): Point | null => {
    const x = timeToX(Math.floor(timeMs / 1000) as UTCTimestamp)
    const y = priceToY(priceI64 / priceScale)
    return x === null || y === null ? null : { x, y }
  }
  const lines = (values: ChanLineObject[]): Line[] => values.flatMap((value) => {
    const start = point(value.start_time, value.start_price_i64)
    const end = point(value.end_time, value.end_price_i64)
    return start && end ? [{ start, end, confirmed: value.confirmed }] : []
  })
  const regions = (values: ChanZhongshu[]): Region[] => values.flatMap((value) => {
    const left = timeToX(Math.floor(value.start_time / 1000) as UTCTimestamp)
    const right = timeToX(Math.floor(value.end_time / 1000) as UTCTimestamp)
    const top = priceToY(value.zg_i64 / priceScale)
    const bottom = priceToY(value.zd_i64 / priceScale)
    return left === null || right === null || top === null || bottom === null
      ? []
      : [{ left, right, top, bottom, confirmed: value.confirmed }]
  })
  const signals = (values: ChanSignalPoint[]): SignalPoint[] => values.flatMap((value) => {
    const projected = point(value.time, value.price_i64)
    return projected ? [{
      ...projected,
      signal_type: value.signal_type,
      divergence_kind: value.divergence_kind,
      signal_class: value.signal_class,
      strength: value.strength,
    }] : []
  })
  return {
    fractals: objects.fractals.flatMap((value) => {
      const projected = point(value.time, value.price_i64)
      return projected ? [{ ...projected, fractal_type: value.fractal_type, confirmed: value.confirmed }] : []
    }),
    bi: lines(objects.bi),
    segments: lines(objects.segments),
    zhongshu: regions(objects.zhongshu),
    segmentZhongshu: regions(objects.segment_zhongshu),
    divergences: signals(objects.divergences),
    tradePoints: signals(objects.trade_points),
  }
}

class ChanRenderer implements IPrimitivePaneRenderer {
  constructor(private readonly source: ChanPrimitive, private readonly layer: 'fill' | 'overlay') {}

  draw(target: Parameters<IPrimitivePaneRenderer['draw']>[0]): void {
    const geometry = this.source.geometry()
    target.useBitmapCoordinateSpace(({ context, horizontalPixelRatio, verticalPixelRatio }) => {
      context.save()
      context.scale(horizontalPixelRatio, verticalPixelRatio)
      if (this.layer === 'fill') {
        drawRegions(context, geometry.zhongshu, true, false, this.source.renderStyle().zhongshu, 0.14, true)
        drawRegions(context, geometry.segmentZhongshu, true, false, this.source.renderStyle().segmentZhongshu, 0.14, true)
      }
      else drawOverlay(context, geometry, this.source.renderStyle())
      context.restore()
    })
  }
}

class ChanView implements IPrimitivePaneView {
  private readonly paneRenderer: ChanRenderer

  constructor(source: ChanPrimitive, private readonly order: PrimitivePaneViewZOrder, layer: 'fill' | 'overlay') {
    this.paneRenderer = new ChanRenderer(source, layer)
  }

  zOrder(): PrimitivePaneViewZOrder { return this.order }
  renderer(): IPrimitivePaneRenderer { return this.paneRenderer }
}

export class ChanPrimitive implements ISeriesPrimitive<Time> {
  private attachment: SeriesAttachedParameter<Time> | null = null
  private objects: ChanObjects = { fractals: [], bi: [], segments: [], zhongshu: [], segment_zhongshu: [], divergences: [], trade_points: [] }
  private priceScale = 1
  private style: ChanRenderStyle = defaultChanRenderStyle
  private readonly views: readonly IPrimitivePaneView[] = [
    new ChanView(this, 'bottom', 'fill'),
    new ChanView(this, 'normal', 'overlay'),
  ]

  attached(parameters: SeriesAttachedParameter<Time>): void { this.attachment = parameters }
  detached(): void { this.attachment = null }
  paneViews(): readonly IPrimitivePaneView[] { return this.views }
  updateAllViews(): void {}

  setData(objects: ChanObjects, priceScale: number): void {
    this.objects = objects
    this.priceScale = priceScale
    this.attachment?.requestUpdate()
  }

  setStyle(style?: IndicatorStyle): void {
    this.style = {
      fractal: style?.outputs.fractal ?? style?.outputs.fractals,
      bi: style?.outputs.bi ?? defaultChanRenderStyle.bi,
      segment: style?.outputs.segment ?? style?.outputs.segments ?? defaultChanRenderStyle.segment,
      zhongshu: style?.outputs.zhongshu ?? defaultChanRenderStyle.zhongshu,
      segmentZhongshu: style?.outputs.segment_zhongshu ?? defaultChanRenderStyle.segmentZhongshu,
      divergence: style?.outputs.divergence ?? defaultChanRenderStyle.divergence,
      tradePoint: style?.outputs.trade_point ?? defaultChanRenderStyle.tradePoint,
    }
    this.attachment?.requestUpdate()
  }

  renderStyle(): ChanRenderStyle { return this.style }

  geometry(): ChanGeometry {
    const attachment = this.attachment
    if (!attachment) return { fractals: [], bi: [], segments: [], zhongshu: [], segmentZhongshu: [], divergences: [], tradePoints: [] }
    return buildChanGeometry(
      this.objects,
      this.priceScale,
      (time) => attachment.chart.timeScale().timeToCoordinate(time),
      (price) => attachment.series.priceToCoordinate(price),
    )
  }
}

function drawRegions(
  context: CanvasRenderingContext2D,
  regions: Region[],
  filled: boolean,
  outlined: boolean,
  style: IndicatorOutputStyle,
  fillOpacity = 0.32,
  shadow = false,
): void {
  if (!style.visible) return
  context.beginPath()
  for (const region of regions) {
    const left = Math.min(region.left, region.right)
    const top = Math.min(region.top, region.bottom)
    context.rect(left, top, Math.abs(region.right - region.left), Math.abs(region.bottom - region.top))
  }
  if (filled) {
    if (shadow) {
      context.shadowColor = colorWithOpacity(style.color, Math.max(0.2, style.opacity * 0.38))
      context.shadowBlur = 8
    }
    context.fillStyle = colorWithOpacity(style.color, Math.max(0.1, style.opacity * fillOpacity))
    context.fill()
    context.shadowColor = 'transparent'
    context.shadowBlur = 0
  }
  if (outlined) {
    context.strokeStyle = colorWithOpacity(style.color, style.opacity)
    context.lineWidth = style.line_width
    context.setLineDash(canvasDash(style.line_style, style.line_width))
    context.stroke()
    context.setLineDash([])
  }
}

function drawLines(context: CanvasRenderingContext2D, lines: Line[], style: IndicatorOutputStyle): void {
  if (!style.visible) return
  for (const confirmed of [true, false]) {
    context.beginPath()
    for (const line of lines) {
      if (line.confirmed !== confirmed) continue
      context.moveTo(line.start.x, line.start.y)
      context.lineTo(line.end.x, line.end.y)
    }
    context.strokeStyle = colorWithOpacity(style.color, style.opacity)
    context.lineWidth = style.line_width
    context.globalAlpha = confirmed ? 1 : 0.55
    context.setLineDash(confirmed ? canvasDash(style.line_style, style.line_width) : [5, 4])
    context.stroke()
  }
  context.globalAlpha = 1
  context.setLineDash([])
}

function drawOverlay(context: CanvasRenderingContext2D, geometry: ChanGeometry, style: ChanRenderStyle): void {
  drawRegions(context, geometry.zhongshu, false, true, style.zhongshu)
  drawRegions(context, geometry.segmentZhongshu, false, true, style.segmentZhongshu)
  drawLines(context, geometry.bi, style.bi)
  drawLines(context, geometry.segments, style.segment)
  drawSignals(context, geometry.divergences, style.divergence)
  drawSignals(context, geometry.tradePoints, style.tradePoint)
  for (const fractal of geometry.fractals) {
    if (style.fractal && !style.fractal.visible) continue
    const direction = fractal.fractal_type === 'top' ? -1 : 1
    context.beginPath()
    context.moveTo(fractal.x, fractal.y)
    context.lineTo(fractal.x - 4, fractal.y + direction * 7)
    context.lineTo(fractal.x + 4, fractal.y + direction * 7)
    context.closePath()
    context.fillStyle = style.fractal
      ? colorWithOpacity(style.fractal.color, style.fractal.opacity)
      : fractal.fractal_type === 'top' ? '#f23645' : '#089981'
    context.globalAlpha = fractal.confirmed ? 1 : 0.5
    context.fill()
  }
  context.globalAlpha = 1
}

function drawSignals(context: CanvasRenderingContext2D, points: SignalPoint[], style: IndicatorOutputStyle): void {
  if (!style.visible) return
  context.font = '11px sans-serif'
  context.textAlign = 'center'
  for (const point of points) {
    const buySide = point.signal_type.includes('buy_') || point.signal_type === 'bottom_divergence'
    const color = point.signal_type.includes('buy_')
      ? '#f23645'
      : point.signal_type.includes('sell_') ? '#00b8a9' : style.color
    const label = chanSignalLabel(point)
    const direction = buySide ? 1 : -1
    context.beginPath()
    context.moveTo(point.x, point.y)
    context.lineTo(point.x - 5, point.y + direction * 8)
    context.lineTo(point.x + 5, point.y + direction * 8)
    context.closePath()
    context.fillStyle = colorWithOpacity(color, style.opacity)
    context.fill()
    context.fillText(label, point.x, point.y + direction * 20)
  }
}

export function chanSignalLabel(
  point: Pick<ChanSignalPoint, 'signal_type' | 'divergence_kind' | 'strength'>,
): string {
  const divergencePrefix = point.divergence_kind === 'trend' ? '趋势' : '盘整'
  const strengthPrefix = point.strength === 'strongest' ? '最强'
    : point.strength === 'normal' ? '一般'
      : point.strength === 'weakest' ? '最弱' : ''
  const rank = point.signal_type.endsWith('_1') ? '一'
    : point.signal_type.endsWith('_2') ? '二' : '三'
  return point.signal_type === 'bottom_divergence' ? `${divergencePrefix}底背驰`
    : point.signal_type === 'top_divergence' ? `${divergencePrefix}顶背驰`
      : point.signal_type.startsWith('class_buy_') ? `${strengthPrefix}类${rank}买`
        : point.signal_type.startsWith('class_sell_') ? `${strengthPrefix}类${rank}卖`
          : point.signal_type.startsWith('buy_') ? `${strengthPrefix}${rank}买`
            : `${strengthPrefix}${rank}卖`
}
