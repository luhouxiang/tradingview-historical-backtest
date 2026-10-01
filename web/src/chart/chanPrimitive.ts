import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  PrimitiveHoveredItem,
  PrimitivePaneViewZOrder,
  SeriesAttachedParameter,
  Time,
  UTCTimestamp,
} from 'lightweight-charts'
import type { ChanBiState, ChanCalculationResults, ChanCenterAuditEvent, ChanCenterConnection, ChanCenterMonitor, ChanFractal, ChanLineObject, ChanLocalCenter, ChanMovementState, ChanSignalPoint } from '../types/api'
import type { IndicatorOutputStyle, IndicatorStyle } from '../types/api'
import { canvasDash, colorWithOpacity } from '../indicators/style'

type ChanObjects = ChanCalculationResults['objects']
type Point = { x: number; y: number }
type Line = { start: Point; end: Point; confirmed: boolean; status?: string; objectId?: string }
type FractalPoint = Point & Pick<ChanFractal, 'fractal_type' | 'confirmed' | 'status' | 'aux_strength'>
type Region = { left: number; right: number; top: number; bottom: number; confirmed: boolean }
type ProcessedBarRegion = Region & { direction: 'up' | 'down' | 'unknown'; status: 'forming' | 'sealed' }
type BiStatePoint = Point & Pick<ChanBiState, 'state' | 'trigger'>
type SignalPoint = Point & Pick<ChanSignalPoint, 'object_id' | 'signal_type' | 'divergence_kind' | 'divergence_profile' | 'signal_class' | 'strength' | 'status'>
type MovementLine = Line & Pick<ChanMovementState, 'state_type'>
type MonitorPoint = Point & { zY: number; referenceObjectId: string; componentOrdinal: number }
  & Pick<ChanCenterMonitor, 'oscillation_bias' | 'breakout_warning'>
type LocalCenterRegion = Region & Pick<ChanLocalCenter, 'object_id' | 'status' | 'seed_ids' | 'scan_floor' | 'unit_kind' | 'structural_level'>
type LocalCenterExtension = Region & Pick<ChanLocalCenter, 'object_id' | 'status' | 'unit_kind'>
type CenterRoleLine = Line & { role: 'entry' | 'exit' | 'retest'; unitId: string }
type ConfirmationMarker = { x: number; centerId: string; barIndex: number; unitKind: 'BI' | 'SEGMENT' }
type PreviewMarker = Point & { centerId: string; state: 'EXIT_PENDING' | 'RETEST_PENDING' | 'RETEST_TOUCH' }

export interface LocalCenterPresentation {
  center: boolean
  boundaryConfirmation: boolean
}

export interface ChanGeometry {
  processedBars: ProcessedBarRegion[]
  fractals: FractalPoint[]
  bi: Line[]
  biStates: BiStatePoint[]
  segments: Line[]
  localCenters: LocalCenterRegion[]
  localCenterExtensions: LocalCenterExtension[]
  centerRoleLines: CenterRoleLine[]
  confirmationMarkers: ConfirmationMarker[]
  previewMarkers: PreviewMarker[]
  movementStates: MovementLine[]
  centerMonitors: MonitorPoint[]
  centerMonitorCurves: Line[]
  centerMonitorAxes: Line[]
  divergences: SignalPoint[]
  tradePoints: SignalPoint[]
}

interface ChanRenderStyle {
  processedBar: IndicatorOutputStyle
  fractal?: IndicatorOutputStyle
  bi: IndicatorOutputStyle
  biState: IndicatorOutputStyle
  segment: IndicatorOutputStyle
  movementState: IndicatorOutputStyle
  centerMonitor: IndicatorOutputStyle
  divergence: IndicatorOutputStyle
  tradePoint: IndicatorOutputStyle
  biCenter: IndicatorOutputStyle
  segmentCenter: IndicatorOutputStyle
}

const defaultChanRenderStyle: ChanRenderStyle = {
  processedBar: { color: '#787b86', line_width: 1, line_style: 'dotted', opacity: 0.7, visible: true },
  bi: { color: '#2962ff', line_width: 2, line_style: 'solid', opacity: 1, visible: true },
  biState: { color: '#26c6da', line_width: 1, line_style: 'dashed', opacity: 0.9, visible: true },
  segment: { color: '#f2d600', line_width: 2, line_style: 'solid', opacity: 1, visible: true },
  movementState: { color: '#ab47bc', line_width: 1, line_style: 'dashed', opacity: 0.9, visible: true },
  centerMonitor: { color: '#26c6da', line_width: 1, line_style: 'dotted', opacity: 0.9, visible: true },
  divergence: { color: '#ff9800', line_width: 1, line_style: 'solid', opacity: 1, visible: true },
  tradePoint: { color: '#ffffff', line_width: 1, line_style: 'solid', opacity: 1, visible: true },
  biCenter: { color: '#42a5f5', line_width: 2, line_style: 'solid', opacity: 1, visible: true },
  segmentCenter: { color: '#ffb300', line_width: 2, line_style: 'dashed', opacity: 1, visible: true },
}

function scopedLocalCenterObjects(objects: ChanObjects): {
  centers: ChanLocalCenter[]; connections: ChanCenterConnection[]; events: ChanCenterAuditEvent[]
} {
  const centers = objects.local_centers ?? []
  const ids = new Set(centers.map((center) => center.object_id))
  return {
    centers,
    connections: (objects.center_connections ?? []).filter((connection) => ids.has(connection.from_center_id)),
    events: (objects.center_audit_events ?? []).filter((event) => ids.has(event.center_id)),
  }
}

export function buildChanGeometry(
  objects: ChanObjects,
  priceScale: number,
  timeToX: (time: UTCTimestamp) => number | null,
  priceToY: (price: number) => number | null,
  localCenterPresentation?: ReadonlyMap<string, LocalCenterPresentation>,
): ChanGeometry {
  const point = (timeMs: number, priceI64: number): Point | null => {
    const x = timeToX(Math.floor(timeMs / 1000) as UTCTimestamp)
    const y = priceToY(priceI64 / priceScale)
    return x === null || y === null ? null : { x, y }
  }
  const lines = (values: ChanLineObject[]): Line[] => values.flatMap((value) => {
    const start = point(value.start_time, value.start_price_i64)
    const end = point(value.end_time, value.end_price_i64)
    return start && end ? [{ start, end, confirmed: value.confirmed, status: value.status, objectId: value.object_id }] : []
  })
  const signals = (values: ChanSignalPoint[], hideInvalidated = false): SignalPoint[] => values.filter((value) => !hideInvalidated || value.status !== 'invalidated').flatMap((value) => {
    const projected = point(value.time, value.price_i64)
    return projected ? [{
      ...projected,
      object_id: value.object_id,
      signal_type: value.signal_type,
      divergence_kind: value.divergence_kind,
      divergence_profile: value.divergence_profile,
      signal_class: value.signal_class,
      strength: value.strength,
      status: value.status,
    }] : []
  })
  const centerMonitors: MonitorPoint[] = objects.center_monitors.flatMap((value) => {
    const projected = point(value.time, value.zn_twice_i64 / 2)
    const z = point(value.time, value.z_twice_i64 / 2)
    return projected && z ? [{
      ...projected,
      zY: z.y,
      referenceObjectId: value.reference_object_id,
      componentOrdinal: value.component_ordinal,
      oscillation_bias: value.oscillation_bias,
      breakout_warning: value.breakout_warning,
    }] : []
  })
  const monitorGroups = new Map<string, MonitorPoint[]>()
  for (const monitor of centerMonitors) {
    const group = monitorGroups.get(monitor.referenceObjectId) ?? []
    group.push(monitor)
    monitorGroups.set(monitor.referenceObjectId, group)
  }
  const centerMonitorCurves: Line[] = []
  const centerMonitorAxes: Line[] = []
  for (const group of monitorGroups.values()) {
    group.sort((left, right) => left.componentOrdinal - right.componentOrdinal || left.x - right.x)
    if (group.length > 1) {
      centerMonitorAxes.push({
        start: { x: group[0]!.x, y: group[0]!.zY },
        end: { x: group.at(-1)!.x, y: group.at(-1)!.zY },
        confirmed: true,
      })
    }
    for (let index = 1; index < group.length; index += 1) {
      centerMonitorCurves.push({ start: group[index - 1]!, end: group[index]!, confirmed: true })
    }
  }
  const local = scopedLocalCenterObjects(objects)
  const presentationFor = (center: ChanLocalCenter): LocalCenterPresentation => localCenterPresentation?.get(center.object_id) ?? { center: true, boundaryConfirmation: true }
  const visibleCenterIds = new Set(local.centers.filter((center) => presentationFor(center).center).map((center) => center.object_id))
  const units = new Map([...objects.bi, ...objects.segments].map((unit) => [unit.object_id, unit]))
  const centerRoleLines: CenterRoleLine[] = []
  const seenRoles = new Set<string>()
  const addRole = (unitId: string | null, role: CenterRoleLine['role']): void => {
    if (!unitId || seenRoles.has(`${role}:${unitId}`)) return
    const unit = units.get(unitId)
    if (!unit) return
    const start = point(unit.start_time, unit.start_price_i64)
    const end = point(unit.end_time, unit.end_price_i64)
    if (start && end) {
      centerRoleLines.push({ start, end, confirmed: unit.confirmed, status: unit.status, objectId: unit.object_id, unitId, role })
      seenRoles.add(`${role}:${unitId}`)
    }
  }
  for (const connection of local.connections.filter((value) => visibleCenterIds.has(value.from_center_id))) {
    addRole(connection.entry_unit_id, 'entry')
    addRole(connection.exit_unit_id, 'exit')
    addRole(connection.first_retest_id, 'retest')
  }
  for (const center of local.centers.filter((value) => visibleCenterIds.has(value.object_id))) {
    addRole(center.entry_id, 'entry')
    addRole(center.exit_id ?? center.pending_exit_id, 'exit')
    addRole(center.first_retest_id, 'retest')
  }
  const confirmationEvents = new Map(local.events.filter((event) => event.event_type === 'BREAK_CONFIRMED').map((event) => [event.center_id, event]))
  return {
    processedBars: objects.processed_bars.flatMap((value) => {
      const left = timeToX(Math.floor(value.start_time / 1000) as UTCTimestamp)
      const right = timeToX(Math.floor(value.end_time / 1000) as UTCTimestamp)
      const top = priceToY(value.high_i64 / priceScale)
      const bottom = priceToY(value.low_i64 / priceScale)
      return left === null || right === null || top === null || bottom === null ? [] : [{
        left, right, top, bottom, confirmed: value.status === 'sealed', direction: value.direction, status: value.status,
      }]
    }),
    fractals: objects.fractals.flatMap((value) => {
      const projected = point(value.time, value.price_i64)
      return projected ? [{ ...projected, fractal_type: value.fractal_type, confirmed: value.confirmed, status: value.status, aux_strength: value.aux_strength }] : []
    }),
    bi: lines(objects.bi),
    biStates: objects.bi_states.flatMap((value) => {
      const projected = point(value.time, value.price_i64)
      return projected ? [{ ...projected, state: value.state, trigger: value.trigger }] : []
    }),
    segments: lines(objects.segments),
    localCenters: local.centers.filter((center) => presentationFor(center).center).flatMap((center) => {
      const left = timeToX(Math.floor(center.body_start_time / 1000) as UTCTimestamp)
      const right = timeToX(Math.floor(center.seed_end_time / 1000) as UTCTimestamp)
      const top = priceToY(center.zg_i64 / priceScale)
      const bottom = priceToY(center.zd_i64 / priceScale)
      return left === null || right === null || top === null || bottom === null ? [] : [{
        left, right, top, bottom, confirmed: true, object_id: center.object_id, status: center.status,
        seed_ids: center.seed_ids, scan_floor: center.scan_floor, unit_kind: center.unit_kind,
        structural_level: center.structural_level,
      }]
    }),
    localCenterExtensions: local.centers.filter((center) => presentationFor(center).center).flatMap((center) => {
      const extensionEnd = center.body_end_time ?? center.observed_end_time
      if (extensionEnd <= center.seed_end_time) return []
      const left = timeToX(Math.floor(center.seed_end_time / 1000) as UTCTimestamp)
      const right = timeToX(Math.floor(extensionEnd / 1000) as UTCTimestamp)
      const top = priceToY(center.zg_i64 / priceScale)
      const bottom = priceToY(center.zd_i64 / priceScale)
      return left === null || right === null || top === null || bottom === null ? [] : [{ left, right, top, bottom, confirmed: center.status === 'CLOSED', object_id: center.object_id, status: center.status, unit_kind: center.unit_kind }]
    }),
    centerRoleLines,
    confirmationMarkers: local.centers.filter((center) => presentationFor(center).boundaryConfirmation).flatMap((center) => {
      const event = confirmationEvents.get(center.object_id)
      if (!event) return []
      const x = timeToX(Math.floor(event.event_time / 1000) as UTCTimestamp)
      return x === null ? [] : [{ x, centerId: center.object_id, barIndex: event.event_bar_index, unitKind: center.unit_kind }]
    }),
    previewMarkers: local.events.flatMap((event) => {
      if (event.event_type !== 'PREVIEW_UPDATED' || event.preview_confirmed !== false || !event.preview_state || event.comparison_i64 === null) return []
      if (!local.centers.some((center) => center.object_id === event.center_id && center.status !== 'CLOSED' && presentationFor(center).center)) return []
      const position = point(event.event_time, event.comparison_i64)
      return position ? [{ ...position, centerId: event.center_id, state: event.preview_state }] : []
    }),
    movementStates: objects.movement_states.flatMap((value) => {
      const start = point(value.start_time, value.price_i64)
      const end = point(value.end_time, value.price_i64)
      return start && end ? [{ start, end, confirmed: value.confirmed, state_type: value.state_type }] : []
    }),
    centerMonitors,
    centerMonitorCurves,
    centerMonitorAxes,
    divergences: signals(objects.divergences, true),
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
        drawRegions(context, geometry.processedBars, false, true, this.source.renderStyle().processedBar)
        drawLocalCenters(context, geometry.localCenters.filter((center) => center.unit_kind === 'BI'), geometry.localCenterExtensions.filter((center) => center.unit_kind === 'BI'), this.source.renderStyle().biCenter)
        drawLocalCenters(context, geometry.localCenters.filter((center) => center.unit_kind === 'SEGMENT'), geometry.localCenterExtensions.filter((center) => center.unit_kind === 'SEGMENT'), this.source.renderStyle().segmentCenter)
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
  private objects: ChanObjects = { processed_bars: [], fractals: [], bi: [], bi_states: [], segments: [], local_centers: [], center_connections: [], center_audit_events: [], movement_states: [], center_monitors: [], divergences: [], trade_points: [] }
  private priceScale = 1
  private localCenterPresentation: ReadonlyMap<string, LocalCenterPresentation> | undefined
  private style: ChanRenderStyle = defaultChanRenderStyle
  private readonly views: readonly IPrimitivePaneView[] = [
    new ChanView(this, 'bottom', 'fill'),
    new ChanView(this, 'normal', 'overlay'),
  ]

  attached(parameters: SeriesAttachedParameter<Time>): void { this.attachment = parameters }
  detached(): void { this.attachment = null }
  paneViews(): readonly IPrimitivePaneView[] { return this.views }
  updateAllViews(): void {}

  setData(objects: ChanObjects, priceScale: number, localCenterPresentation?: ReadonlyMap<string, LocalCenterPresentation>): void {
    this.objects = objects
    this.priceScale = priceScale
    this.localCenterPresentation = localCenterPresentation
    this.attachment?.requestUpdate()
  }

  setStyle(style?: IndicatorStyle): void {
    this.style = {
      processedBar: style?.outputs.processed_bar ?? defaultChanRenderStyle.processedBar,
      fractal: style?.outputs.fractal ?? style?.outputs.fractals,
      bi: style?.outputs.bi ?? defaultChanRenderStyle.bi,
      biState: style?.outputs.bi_state ?? defaultChanRenderStyle.biState,
      segment: style?.outputs.segment ?? style?.outputs.segments ?? defaultChanRenderStyle.segment,
      movementState: style?.outputs.movement_state ?? defaultChanRenderStyle.movementState,
      centerMonitor: style?.outputs.center_monitor ?? defaultChanRenderStyle.centerMonitor,
      divergence: style?.outputs.divergence ?? defaultChanRenderStyle.divergence,
      tradePoint: style?.outputs.trade_point ?? defaultChanRenderStyle.tradePoint,
      biCenter: style?.outputs.local_center ?? defaultChanRenderStyle.biCenter,
      segmentCenter: style?.outputs.local_center
        ? { ...style.outputs.local_center, line_style: 'dashed' }
        : defaultChanRenderStyle.segmentCenter,
    }
    this.attachment?.requestUpdate()
  }

  renderStyle(): ChanRenderStyle { return this.style }

  geometry(): ChanGeometry {
    const attachment = this.attachment
    if (!attachment) return { processedBars: [], fractals: [], bi: [], biStates: [], segments: [], localCenters: [], localCenterExtensions: [], centerRoleLines: [], confirmationMarkers: [], previewMarkers: [], movementStates: [], centerMonitors: [], centerMonitorCurves: [], centerMonitorAxes: [], divergences: [], tradePoints: [] }
    return buildChanGeometry(
      this.objects,
      this.priceScale,
      (time) => attachment.chart.timeScale().timeToCoordinate(time),
      (price) => attachment.series.priceToCoordinate(price),
      this.localCenterPresentation,
    )
  }

  hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    if (this.style.tradePoint.visible) {
      for (const point of [...this.geometry().tradePoints].reverse()) {
        const markerX = point.x + (point.signal_type.startsWith('class_') ? 24 : 0)
        const labelY = point.y + (point.signal_type.includes('buy_') ? 20 : -20)
        if (Math.abs(x - markerX) <= 18 && (Math.abs(y - point.y) <= 11 || Math.abs(y - labelY) <= 13)) {
          return { externalId: `trade-point:${point.object_id}`, cursorStyle: 'pointer', hitTestPriority: 1, distance: 0, zOrder: 'normal' }
        }
      }
    }
    if (this.style.divergence.visible) {
      for (const point of [...this.geometry().divergences].reverse()) {
        const markerX = point.x - 24
        const labelY = point.y + (point.signal_type === 'bottom_divergence' ? 20 : -20)
        if (Math.abs(x - markerX) <= 18 && (Math.abs(y - point.y) <= 11 || Math.abs(y - labelY) <= 13)) {
          return { externalId: `divergence:${point.object_id}`, cursorStyle: 'pointer', hitTestPriority: 1, distance: 0, zOrder: 'normal' }
        }
      }
    }
    for (const center of [...this.geometry().localCenters].reverse()) {
      const left = Math.min(center.left, center.right)
      const right = Math.max(center.left, center.right)
      const top = Math.min(center.top, center.bottom)
      const bottom = Math.max(center.top, center.bottom)
      if (x >= left && x < right && y >= top && y <= bottom) return { externalId: `local-center:${center.object_id}`, cursorStyle: 'help', hitTestPriority: 0, distance: 0, zOrder: 'normal' }
    }
    return null
  }

  hoverDetail(externalId: unknown): string | null {
    const tradePoint = this.signalForHit(externalId)
    if (tradePoint) return `${chanSignalLabel(tradePoint)} · K${tradePoint.bar_index} · ${tradePoint.status}`
    if (typeof externalId !== 'string' || !externalId.startsWith('local-center:')) return null
    const id = externalId.slice('local-center:'.length)
    const center = (this.objects.local_centers ?? []).find((value) => value.object_id === id)
    if (!center) return null
    const connection = (this.objects.center_connections ?? []).find((value) => value.from_center_id === id)
    const incoming = (this.objects.center_connections ?? []).find((value) => value.to_center_id === id)
    const confirmed = (this.objects.center_audit_events ?? []).find((value) => value.center_id === id && value.event_type === 'BREAK_CONFIRMED')
    const preview = (this.objects.center_audit_events ?? []).find((value) => value.center_id === id && value.event_type === 'PREVIEW_UPDATED' && value.preview_confirmed === false)
    return [
      `实体中枢 ${center.object_id}`,
      `模式/层级：${center.unit_kind} / ${center.structural_level}`,
      `种子：${center.seed_ids.join(' → ')}`,
      `进入/离开/回试：${center.entry_id ?? '—'} / ${center.exit_id ?? center.pending_exit_id ?? '—'} / ${center.first_retest_id ?? '—'}`,
      `前向连接：${incoming?.object_id ?? '—'}；后向连接：${connection?.object_id ?? '—'}`,
      `扫描起点：单元索引 ${center.scan_floor}`,
      `形成确认：K${center.formed_at_bar_index}；分界确认：${confirmed ? `K${confirmed.event_bar_index} · ${new Date(confirmed.event_time).toISOString()}（UTC）` : '尚未确认'}；状态：${center.status}`,
      `尾单元预览：${preview ? `${preview.preview_state === 'RETEST_TOUCH' ? '触边，候选即时失效' : preview.preview_state === 'RETEST_PENDING' ? '首次回试待确认' : '离开待确认'} · 比较值 ${preview.comparison_i64} · K${preview.known_at_bar_index}（不可交易）` : '无'}`,
    ].join('\n')
  }

  signalForHit(externalId: unknown): ChanSignalPoint | null {
    if (typeof externalId !== 'string') return null
    if (externalId.startsWith('trade-point:')) {
      const id = externalId.slice('trade-point:'.length)
      return this.objects.trade_points.find((point) => point.object_id === id) ?? null
    }
    if (externalId.startsWith('divergence:')) {
      const id = externalId.slice('divergence:'.length)
      return this.objects.divergences.find((point) => point.object_id === id) ?? null
    }
    return null
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
  halfOpen = false,
): void {
  if (!style.visible) return
  if (halfOpen) {
    for (const region of regions) {
      // Clip paint (including stroke/shadow) to the exact [start, end) span.
      // Keep data anchors intact: never invent a pixel gap between regions.
      context.save()
      context.beginPath()
      context.rect(Math.min(region.left, region.right), -1_000_000, Math.abs(region.right - region.left), 2_000_000)
      context.clip()
      drawRegions(context, [region], filled, outlined, style, fillOpacity, shadow)
      context.restore()
    }
    return
  }
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

function drawLocalCenters(context: CanvasRenderingContext2D, centers: LocalCenterRegion[], extensions: LocalCenterExtension[], style: IndicatorOutputStyle): void {
  if (!style.visible) return
  drawRegions(context, centers, true, true, style, 0.16, true, true)
  for (const extension of extensions) {
    drawRegions(context, [extension], true, true, {
      ...style, line_style: extension.status === 'CLOSED' ? 'solid' : 'dashed', opacity: extension.status === 'CLOSED' ? 0.82 : extension.status === 'PENDING_BREAK' ? 0.72 : 0.48,
    }, 0.06, false, true)
  }
}

function drawLines(context: CanvasRenderingContext2D, lines: Line[], style: IndicatorOutputStyle): void {
  if (!style.visible) return
  for (const line of lines) {
    context.beginPath()
    context.moveTo(line.start.x, line.start.y)
    context.lineTo(line.end.x, line.end.y)
    context.strokeStyle = colorWithOpacity(style.color, style.opacity)
    context.lineWidth = style.line_width
    context.globalAlpha = line.confirmed ? 1 : line.status === 'invalidated' ? 0.28 : 0.62
    context.setLineDash(line.confirmed ? canvasDash(style.line_style, style.line_width) : line.status === 'invalidated' ? [2, 5] : [6, 4])
    context.stroke()
  }
  context.globalAlpha = 1
  context.setLineDash([])
}

function drawOverlay(context: CanvasRenderingContext2D, geometry: ChanGeometry, style: ChanRenderStyle): void {
  drawLines(context, geometry.bi, style.bi)
  drawLines(context, geometry.segments, style.segment)
  drawCenterRoleLines(context, geometry.centerRoleLines)
  drawConfirmationMarkers(context, geometry.confirmationMarkers)
  drawPreviewMarkers(context, geometry.previewMarkers)
  drawBiStates(context, geometry.biStates, style.biState)
  drawMovementStates(context, geometry.movementStates, style.movementState)
  drawCenterMonitors(
    context,
    geometry.centerMonitors,
    geometry.centerMonitorCurves,
    geometry.centerMonitorAxes,
    style.centerMonitor,
  )
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
    context.globalAlpha = fractal.status === 'confirmed' ? 1 : fractal.status === 'invalidated' ? 0.25 : 0.6
    if (fractal.status === 'invalidated') context.stroke()
    else context.fill()
    if (fractal.aux_strength === 'strong_reversal') {
      context.beginPath()
      context.arc(fractal.x, fractal.y, 6, 0, Math.PI * 2)
      context.stroke()
    }
  }
  context.globalAlpha = 1
}

function drawCenterRoleLines(context: CanvasRenderingContext2D, lines: CenterRoleLine[]): void {
  const colors = { entry: '#26a69a', exit: '#ff7043', retest: '#ab47bc' } as const
  for (const line of lines) drawLines(context, [line], { color: colors[line.role], line_width: 4, line_style: line.confirmed ? 'solid' : 'dashed', opacity: 0.95, visible: true })
}

function drawPreviewMarkers(context: CanvasRenderingContext2D, markers: PreviewMarker[]): void {
  context.save()
  context.font = '11px sans-serif'
  for (const marker of markers) {
    context.fillStyle = marker.state === 'RETEST_TOUCH' ? '#ef5350' : '#b0bec5'
    context.beginPath()
    context.arc(marker.x, marker.y, 4, 0, 2 * Math.PI)
    context.fill()
    const label = marker.state === 'RETEST_TOUCH' ? '触边，候选失效' : marker.state === 'RETEST_PENDING' ? '首次回试待确认' : '离开待确认'
    const text = `预览：${label}（不可交易）`
    const width = context.measureText(text).width
    const x = Math.max(2, Math.min(marker.x + 6, context.canvas.width - width - 2))
    context.fillText(text, x, Math.max(14, marker.y - 8))
  }
  context.restore()
}

function drawConfirmationMarkers(context: CanvasRenderingContext2D, markers: ConfirmationMarker[]): void {
  context.save()
  context.strokeStyle = '#ffca28'
  context.fillStyle = '#ffca28'
  context.font = '10px sans-serif'
  context.setLineDash([4, 4])
  for (const marker of markers) {
    context.beginPath()
    context.moveTo(marker.x, 0)
    context.lineTo(marker.x, context.canvas.height)
    context.stroke()
    context.fillText(`${marker.unitKind === 'BI' ? '笔' : '段'}分界确认 K${marker.barIndex}`, marker.x + 4, 14)
  }
  context.restore()
}

function drawBiStates(context: CanvasRenderingContext2D, states: BiStatePoint[], style: IndicatorOutputStyle): void {
  if (!style.visible) return
  context.fillStyle = colorWithOpacity(style.color, style.opacity)
  context.strokeStyle = colorWithOpacity(style.color, style.opacity)
  context.font = '10px sans-serif'
  context.textAlign = 'left'
  for (const state of states) {
    context.beginPath()
    context.arc(state.x, state.y, 3, 0, Math.PI * 2)
    context.fill()
    context.fillText(state.state.replace('_EXTENDING', '').replace('_FORMING', '?'), state.x + 5, state.y - 5)
  }
}

function drawMovementStates(context: CanvasRenderingContext2D, lines: MovementLine[], style: IndicatorOutputStyle): void {
  if (!style.visible) return
  drawLines(context, lines, style)
  context.font = '10px sans-serif'
  context.textAlign = 'center'
  context.fillStyle = colorWithOpacity(style.color, style.opacity)
  for (const line of lines) {
    const label = line.state_type === 'consolidation' ? '盘整'
      : line.state_type === 'centre_oscillation' ? '中枢震荡'
        : line.state_type === 'centre_migration_up' ? '中枢上移' : '中枢下移'
    context.fillText(label, (line.start.x + line.end.x) / 2, line.start.y - 4)
  }
}

function drawCenterMonitors(
  context: CanvasRenderingContext2D,
  points: MonitorPoint[],
  curves: Line[],
  axes: Line[],
  style: IndicatorOutputStyle,
): void {
  if (!style.visible) return
  drawLines(context, axes, { ...style, line_style: 'dashed', opacity: style.opacity * 0.45 })
  drawLines(context, curves, style)
  context.lineWidth = style.line_width
  context.setLineDash([])
  for (const point of points) {
    const color = point.breakout_warning === 'cross_above_b' ? '#f23645'
      : point.breakout_warning === 'cross_below_a' ? '#00b8a9'
        : point.breakout_warning ? '#f0a000'
          : point.oscillation_bias === 'strong' ? style.color
            : point.oscillation_bias === 'weak' ? '#78909c' : '#b0bec5'
    context.beginPath()
    context.arc(point.x, point.y, point.breakout_warning ? 4 : 2.5, 0, Math.PI * 2)
    context.fillStyle = colorWithOpacity(color, style.opacity)
    context.fill()
  }
  context.setLineDash([])
}

function drawSignals(context: CanvasRenderingContext2D, points: SignalPoint[], style: IndicatorOutputStyle): void {
  if (!style.visible) return
  context.font = '11px sans-serif'
  context.textAlign = 'center'
  for (const point of points) {
    const markerX = point.x + (point.signal_type.endsWith('_divergence') ? -24 : point.signal_type.startsWith('class_') ? 24 : 0)
    const buySide = point.signal_type.includes('buy_') || point.signal_type === 'bottom_divergence'
    const color = point.signal_type.includes('buy_')
      ? '#f23645'
      : point.signal_type.includes('sell_') ? '#00b8a9' : style.color
    const label = chanSignalLabel(point)
    const direction = buySide ? 1 : -1
    if (markerX !== point.x) {
      context.beginPath()
      context.moveTo(point.x, point.y)
      context.lineTo(markerX, point.y)
      context.strokeStyle = colorWithOpacity(color, style.opacity * 0.6)
      context.stroke()
    }
    if (point.status === 'invalidated') {
      context.strokeStyle = colorWithOpacity(color, style.opacity * 0.35)
      context.beginPath()
      context.moveTo(markerX - 5, point.y - 5)
      context.lineTo(markerX + 5, point.y + 5)
      context.moveTo(markerX + 5, point.y - 5)
      context.lineTo(markerX - 5, point.y + 5)
      context.stroke()
      context.fillStyle = colorWithOpacity(color, style.opacity * 0.35)
      context.fillText(`${label}失效`, markerX, point.y + direction * 20)
      continue
    }
    context.beginPath()
    context.moveTo(markerX, point.y)
    context.lineTo(markerX - 5, point.y + direction * 8)
    context.lineTo(markerX + 5, point.y + direction * 8)
    context.closePath()
    context.fillStyle = colorWithOpacity(color, point.status === 'forming' ? style.opacity * 0.55 : style.opacity)
    if (point.status === 'candidate' || point.status === 'forming') {
      context.strokeStyle = colorWithOpacity(color, style.opacity * 0.68)
      context.stroke()
    }
    else context.fill()
    context.fillText(point.status === 'forming' ? `${label}形成中` : point.status === 'candidate' ? `${label}候选` : label, markerX, point.y + direction * 20)
  }
}

export function chanSignalLabel(
  point: Pick<ChanSignalPoint, 'signal_type' | 'divergence_kind' | 'strength' | 'divergence_profile'>,
): string {
  const divergencePrefix = point.divergence_kind === 'center_oscillation' ? '中枢震荡'
    : point.divergence_profile === 'segment_trend_candidate' ? '线段趋势'
      : point.divergence_kind === 'trend' ? '趋势' : '盘整'
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
