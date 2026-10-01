<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import TopToolbar from './TopToolbar.vue'
import DatasetPanel from './DatasetPanel.vue'
import IndicatorManagerPanel from './IndicatorManagerPanel.vue'
import DrawingToolbar from './DrawingToolbar.vue'
import ObjectTreePanel from './ObjectTreePanel.vue'
import ChartGroup from './ChartGroup.vue'
import ReplayPanel from './ReplayPanel.vue'
import BacktestPanel from './BacktestPanel.vue'
import OptimizationPanel from './OptimizationPanel.vue'
import StrategyResearchPanel from './StrategyResearchPanel.vue'
import KeyboardInstrumentPicker from './KeyboardInstrumentPicker.vue'
import { ApiError, cancelCalculation, createCalculation, getCalculation, getCalculationResults, getDrawings, getLayout, getStrategySourceConfig, listAlgorithms, putDrawings, putLayout, putStrategySourceConfig } from '../api/client'
import { DrawingHistory, LayerManager, type DrawingObject, type DrawingType } from '../drawing/model'
import { defaultIndicatorSpecs } from '../indicators/defaults'
import { defaultChanSpec } from '../chan/defaults'
import { selectLocalCenters } from '../chart/localCenterDisplay'
import { anyDivergenceVisible, divergenceLayer, divergenceVisible } from '../chart/divergenceVisibility'
import { selectVisibleTradePoints, tradePointCategory } from '../chart/tradePointVisibility'
import { createBacktestWorkspaceChannel, createBacktestWorkspaceUrl, type BacktestWorkspaceMessage } from '../backtest/workspaceChannel'
import type { ReplayObjects, ReplaySignal } from '../replay/eventIndex'
import type { AlgorithmDefinition, BacktestTrade, CalculationRequest, ChanCenterMonitor, ChanLayerCategory, ChanLineObject, ChanSignalPoint, ChanTreeObject, DatasetMeta, SeriesSource, StrategyRunSource, StrategySource, StrategySourceDynamicConfig, StrategySourcePreference, WorkspaceLayout } from '../types/api'

defineProps<{ health: string }>()

const selectedDataset = ref<DatasetMeta | null>(null)
const rightOpen = ref(true)
const rightWidth = ref(320)
const bottomOpen = ref(false)
const bottomHeight = ref(260)
const bottomTab = ref<'replay' | 'backtest' | 'trades' | 'equity' | 'optimization' | 'research' | 'tasks' | 'logs'>('replay')
const rightTab = ref<'datasets' | 'indicators' | 'strategies' | 'objects'>('datasets')
const indicatorSources = ref<SeriesSource[]>([])
const strategySources = ref<StrategySource[]>([])
const strategyRunSources = ref<StrategyRunSource[]>([])
const drawings = ref<DrawingObject[]>([])
const selectedDrawingId = ref<string | null>(null)
const signalObjectsBySource = ref<Record<string, ChanTreeObject[]>>({})
const segmentEvidenceById = ref<Record<string, ChanLineObject>>({})
const selectedSignal = ref<ChanTreeObject | null>(null)
const selectedDivergenceSegments = computed(() => {
  const signal = selectedSignal.value?.signal
  if (!signal?.divergence_kind) return null
  return {
    ...(signal.divergence_kind === 'trend' && signal.a_object_id && signal.a_object_id !== signal.comparison_reference_object_id
      ? { a: segmentEvidenceById.value[signal.a_object_id] ?? null } : {}),
    reference: signal.comparison_reference_object_id ? segmentEvidenceById.value[signal.comparison_reference_object_id] ?? null : null,
    current: signal.comparison_current_object_id ? segmentEvidenceById.value[signal.comparison_current_object_id] ?? null : null,
  }
})
const selectedSignalOrigin = ref<'strategy' | 'trade' | null>(null)
const lockedSignalId = ref<string | null>(null)
const signalLoading = ref(false)
const drawingTool = ref<DrawingType | 'cursor'>('cursor')
const magnet = ref(false)
const keepDrawingMode = ref(false)
const workspaceStatus = ref('')
const layoutRevision = ref(0)
const drawingRevision = ref(0)
const strategySourceConfigRevision = ref(0)
const strategySourcePreferences = ref<StrategySourcePreference[]>([])
const layoutStrategyPresentation = ref<Record<string, Pick<StrategySource, 'visible' | 'category_visibility'>>>({})
const replayCursor = ref<number | null>(null)
const replayObjects = ref<ReplayObjects | null>(null)
const replaySignals = ref<ReplaySignal[]>([])
const visibleStrategySignals = computed<ReplaySignal[]>(() => [
  ...replaySignals.value,
  ...strategyRunSources.value.filter((source) => source.visible).flatMap((source) => source.signals),
])
const replaySource = computed(() => strategySources.value.find((source) => source.status === 'completed') ?? null)
const chartRef = ref<{
  snapshotLayout: () => { panes: Array<{ id: string; kind: 'price' | 'indicator'; weight: number; minHeight: number; visible: boolean; collapsed: boolean; order: number }> }
  restoreLayout: (value: { panes: Array<{ id: string; kind: 'price' | 'indicator'; weight: number; minHeight: number; collapsed?: boolean }> }) => void
  focusSignal: (signal: ChanTreeObject) => Promise<void>
  focusBar: (barIndex: number) => Promise<void>
} | null>(null)

function focusKeyboardBar(barIndex: number): void {
  void chartRef.value?.focusBar(barIndex)
}
const drawingHistory = new DrawingHistory()
const layerManager = new LayerManager()
const profileId = 'default'
const layoutId = 'default-three-pane'
const strategyConfigurationSaveDelayMs = 300
let signalLoadGeneration = 0
let workspaceGeneration = 0
let strategyConfigurationSaveTimer: number | undefined
let layoutWriteQueue: Promise<void> = Promise.resolve()
let strategyConfigWriteQueue: Promise<void> = Promise.resolve()
const backtestWorkspaceChannel = createBacktestWorkspaceChannel()
const workspaceColumns = computed(() => rightOpen.value
  ? `48px minmax(320px, 1fr) 1px ${rightWidth.value}px`
  : '48px minmax(320px, 1fr)')
const shellRows = computed(() => `44px minmax(0, 1fr) ${bottomOpen.value ? bottomHeight.value : 28}px`)

const znWarningLabels: Record<Exclude<ChanCenterMonitor['breakout_warning'], null>, string> = {
  cross_above_b: '严格越过 B',
  cross_below_a: '严格越过 A',
  rising_wedge_below_b: '抬高未破 B·上升楔形预警',
  falling_wedge_above_a: '降低未破 A·下降楔形预警',
}

function znMonitorDetail(monitor: ChanCenterMonitor): string {
  return monitor.breakout_warning
    ? `${znWarningLabels[monitor.breakout_warning]}·不确认三类点`
    : `${monitor.relative_position}·辅助监视`
}

function commitDrawings(value: DrawingObject[]): void {
  drawings.value = drawingHistory.commit(value)
}

function patchDrawing(id: string, patch: Partial<DrawingObject>): void {
  const now = new Date().toISOString()
  commitDrawings(drawings.value.map((drawing) => drawing.id === id
    ? { ...drawing, ...patch, revision: drawing.revision + 1, updated_at: now }
    : drawing))
}

function removeDrawing(id: string): void {
  commitDrawings(drawings.value.filter((drawing) => drawing.id !== id))
  if (selectedDrawingId.value === id) selectedDrawingId.value = null
}

function reorderDrawing(id: string, direction: -1 | 1): void {
  layerManager.replace(drawings.value)
  commitDrawings(layerManager.reorder(id, direction))
}

function lockAll(): void { commitDrawings(drawings.value.map((drawing) => ({ ...drawing, locked: true }))) }
function hideAll(): void { commitDrawings(drawings.value.map((drawing) => ({ ...drawing, visible: false }))) }
function deleteSelected(): void { if (selectedDrawingId.value) removeDrawing(selectedDrawingId.value) }
function deleteAll(): void { commitDrawings([]); selectedDrawingId.value = null }
function undo(): void { drawings.value = drawingHistory.undo() }
function redo(): void { drawings.value = drawingHistory.redo() }

const workspaceJobs = new Set<string>()
const workspaceSubmissions = new Set<Promise<unknown>>()
let cancellationQueue: Promise<unknown> = Promise.resolve()

function cancelWorkspaceJobs(): void {
  const jobs = [...workspaceJobs]
  workspaceJobs.clear()
  cancellationQueue = Promise.allSettled([cancellationQueue, ...workspaceSubmissions, ...jobs.map((id) => cancelCalculation(id))])
}

async function createWorkspaceCalculation(request: CalculationRequest, generation: number) {
  await cancellationQueue
  if (generation !== workspaceGeneration) return null
  const submission = createCalculation(request).then(async (accepted) => {
    if (generation !== workspaceGeneration) {
      if (accepted.status !== 'completed') await cancelCalculation(accepted.job_id)
      return null
    }
    if (accepted.status !== 'completed') workspaceJobs.add(accepted.job_id)
    return accepted
  })
  workspaceSubmissions.add(submission)
  try {
    return await submission
  } finally {
    workspaceSubmissions.delete(submission)
  }
}

onBeforeUnmount(() => {
  workspaceGeneration += 1
  cancelWorkspaceJobs()
  backtestWorkspaceChannel?.close()
})

async function trackCalculation(source: SeriesSource): Promise<void> {
  const generation = workspaceGeneration
  while (generation === workspaceGeneration) {
    const status = await getCalculation(source.job_id)
    if (generation !== workspaceGeneration) return
    indicatorSources.value = indicatorSources.value.map((item) => item.source_id === source.source_id
      ? { ...item, status: status.status, error: status.error?.message }
      : item)
    if (status.status === 'completed' || ['failed', 'cancelled', 'interrupted'].includes(status.status)) {
      workspaceJobs.delete(source.job_id)
      return
    }
    await new Promise((resolve) => window.setTimeout(resolve, 250))
  }
}

async function trackStrategyCalculation(source: StrategySource): Promise<void> {
  const generation = workspaceGeneration
  while (generation === workspaceGeneration) {
    const status = await getCalculation(source.job_id)
    if (generation !== workspaceGeneration) return
    strategySources.value = strategySources.value.map((item) => item.source_id === source.source_id
      ? { ...item, status: status.status, error: status.error?.message }
      : item)
    if (status.status === 'completed' || ['failed', 'cancelled', 'interrupted'].includes(status.status)) {
      workspaceJobs.delete(source.job_id)
      return
    }
    await new Promise((resolve) => window.setTimeout(resolve, 250))
  }
}

function formatTreeTimestamp(timestamp: number, dataset: DatasetMeta): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: dataset.time.timezone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(timestamp)).replaceAll('/', '-')
}

async function loadSignalObjects(): Promise<void> {
  const dataset = selectedDataset.value
  const sources = strategySources.value.filter((source) => source.status === 'completed' && source.visible)
  const generation = ++signalLoadGeneration
  if (!dataset || sources.length === 0) {
    signalObjectsBySource.value = {}
    segmentEvidenceById.value = {}
    if (selectedSignalOrigin.value !== 'trade') {
      selectedSignal.value = null
      selectedSignalOrigin.value = null
      lockedSignalId.value = null
    }
    return
  }
  signalLoading.value = true
  try {
    const results = await Promise.all(sources.map(async (source) => {
      const signals: ChanTreeObject[] = []
      const segments: ChanLineObject[] = []
      for (let from = dataset.coverage.first_bar_index; from <= dataset.coverage.last_bar_index; from += 5000) {
        const to = Math.min(dataset.coverage.last_bar_index, from + 4999)
        if (generation !== signalLoadGeneration) return { source, signals: [], segments: [] }
        const result = await getCalculationResults(source.job_id, from, to)
        if (result.result_kind === 'chan') {
          segments.push(...result.objects.segments)
          const visibility = completeCategoryVisibility(source.category_visibility)
          const localCenters = selectLocalCenters(result.objects.local_centers ?? [], {
            bi: visibility.center_objects && visibility.bi_centers,
            segment: visibility.center_objects && visibility.segment_centers,
          })
          const centerContext = selectLocalCenters(result.objects.local_centers ?? [], {
            bi: visibility.bi_centers || visibility.bi_boundary_confirmations,
            segment: visibility.segment_centers || visibility.segment_boundary_confirmations,
          })
          const centerById = new Map(centerContext.map((center) => [center.object_id, center]))
          const connectionByCenter = new Map((result.objects.center_connections ?? []).map((connection) => [connection.from_center_id, connection]))
          const incomingConnectionByCenter = new Map((result.objects.center_connections ?? [])
            .filter((connection) => connection.to_center_id !== null)
            .map((connection) => [connection.to_center_id, connection]))
          const confirmationByCenter = new Map((result.objects.center_audit_events ?? []).filter((event) => event.event_type === 'BREAK_CONFIRMED').map((event) => [event.center_id, event]))
          const previewByCenter = new Map((result.objects.center_audit_events ?? []).filter((event) => event.event_type === 'PREVIEW_UPDATED' && event.preview_confirmed === false).map((event) => [event.center_id, event]))
          signals.push(
            ...(visibility.processed_bars ? result.objects.processed_bars.map((bar): ChanTreeObject => ({
              object_id: bar.object_id, object_type: 'processed_bar', layer_category: 'processed_bars',
              bar_index: bar.end_bar_index, time: bar.end_time, price_i64: bar.close_i64,
              confirmed_at_bar_index: bar.sealed_at_bar_index, known_at_bar_index: bar.known_at_bar_index,
              object_revision: bar.object_revision, label: `处理后K线 #${bar.normalized_index}`,
              detail: `${bar.direction === 'up' ? '向上' : bar.direction === 'down' ? '向下' : '方向未定'} · 开${bar.open_i64} 高${bar.high_i64} 低${bar.low_i64} 收${bar.close_i64} · ${bar.status === 'sealed' ? '已封闭' : '形成中'}`,
            })) : []),
            ...(visibility.fractals ? result.objects.fractals.map((fractal): ChanTreeObject => ({
              object_id: fractal.object_id, object_type: 'fractal', layer_category: 'fractals',
              bar_index: fractal.bar_index, time: fractal.time, price_i64: fractal.price_i64,
              confirmed_at_bar_index: fractal.confirmed_at_bar_index, known_at_bar_index: fractal.known_at_bar_index,
              object_revision: fractal.object_revision, label: `${fractal.fractal_type === 'top' ? '顶' : '底'}分型 · ${fractal.status === 'confirmed' ? '已确认' : fractal.status === 'candidate' ? '候选' : '已失效'}`,
              detail: `区间 ${fractal.zone_low_i64}–${fractal.zone_high_i64}${fractal.invalidation_reason ? ` · ${fractal.invalidation_reason}` : ''}`,
            })) : []),
            ...(visibility.bi_states ? result.objects.bi.map((line): ChanTreeObject => treeLine(line, 'bi', 'bi_states')) : []),
            ...(visibility.bi_states ? result.objects.bi_states.map((state): ChanTreeObject => ({
              object_id: state.object_id, object_type: 'bi_state', layer_category: 'bi_states',
              bar_index: state.bar_index, time: state.time, price_i64: state.price_i64,
              confirmed_at_bar_index: null, known_at_bar_index: state.known_at_bar_index,
              object_revision: state.object_revision, label: state.state,
              detail: state.trigger,
            })) : []),
            ...(visibility.segment_boundary_confirmations ? result.objects.segments.map((line): ChanTreeObject => treeLine(line, 'segment', 'segment_boundary_confirmations')) : []),
            ...result.objects.divergences.filter((signal) => divergenceVisible(signal, visibility)).map((signal) => treeSignal(signal, 'divergence')),
            ...selectVisibleTradePoints(result.objects.trade_points, visibility).map((signal) => treeSignal(signal, 'trade_point')),
            ...localCenters.map((center): ChanTreeObject => {
              const connection = connectionByCenter.get(center.object_id)
              const incomingConnection = incomingConnectionByCenter.get(center.object_id)
              const confirmation = confirmationByCenter.get(center.object_id)
              const preview = previewByCenter.get(center.object_id)
              const entry = center.entry_id ?? '—'
              const exit = center.exit_id ?? center.pending_exit_id ?? '—'
              const retest = center.first_retest_id ?? '—'
              return {
                object_id: center.object_id, object_type: 'local_center', layer_category: center.unit_kind === 'BI' ? 'bi_centers' : 'segment_centers',
                bar_index: center.observed_end_bar_index, time: center.observed_end_time,
                price_i64: Math.trunc((center.zd_i64 + center.zg_i64) / 2),
                confirmed_at_bar_index: center.break_confirmed_at_bar_index,
                known_at_bar_index: center.known_at_bar_index, object_revision: center.object_revision,
                label: `${center.unit_kind === 'BI' ? '笔' : '线段'}实体中枢 · ${center.status === 'ACTIVE' ? '活动' : center.status === 'PENDING_BREAK' ? '候选离开' : '已分界'}`,
                detail: `ZD ${center.zd_i64} / ZG ${center.zg_i64} · 形成${center.formation_dir === 'UP' ? '回升' : center.formation_dir === 'DOWN' ? '回调' : '未知'} · 相邻${center.relative_dir === 'UP' ? '上移' : center.relative_dir === 'DOWN' ? '下移' : center.relative_dir === 'OVERLAP' ? '交叠' : '未知'}${center.comparison_excluded_entry_id ? '（共享首段仅参与构成）' : ''}`,
                hover_detail: [
                  `实体中枢：${center.object_id}`,
                  `模式/层级：${center.unit_kind} / ${center.structural_level}`,
                  `核心关系：${center.core_relation === 'CORE_ABOVE' ? '核心上移' : center.core_relation === 'CORE_BELOW' ? '核心下移' : center.core_relation === 'CORE_TOUCH_OR_OVERLAP' ? '核心触及或重叠' : '无前中枢关系证据'}（前中枢：${center.previous_center_id ?? '—'}）`,
                  `形成方向：${center.formation_dir === 'UP' ? '回升形成 ↑↓↑' : center.formation_dir === 'DOWN' ? '回调形成 ↓↑↓' : '旧结果未记录'}`,
                  `相邻中枢：${center.relative_dir === 'UP' ? '比较外围严格上移' : center.relative_dir === 'DOWN' ? '比较外围严格下移' : center.relative_dir === 'OVERLAP' ? '核心或比较外围相接／交叠' : '无可比的前中枢'}（离开与首次回试只作连接证据）`,
                  `比较 DD/GG：${center.comparison_dd_i64 ?? '未记录'} / ${center.comparison_gg_i64 ?? '未记录'}；完整主体 DD/GG：${center.dd_i64 ?? '未记录'} / ${center.gg_i64 ?? '未记录'}`,
                  center.comparison_excluded_entry_id
                    ? `工程口径：共享进入段 ${center.comparison_excluded_entry_id} 参与三段交叠及 ZD/ZG，但不计入相邻比较 DD/GG；完整主体范围保留审计。`
                    : '相邻比较未排除共享进入段。',
                  `趋势：未验证${center.higher_level_review_required ? ' · 外围波动接触，需高级别递归检查' : ''}`,
                  `尾单元预览：${preview ? `${preview.preview_state === 'RETEST_TOUCH' ? '触边，候选即时失效' : preview.preview_state === 'RETEST_PENDING' ? '首次回试待确认' : '离开待确认'} · 比较值 ${preview.comparison_i64} · K${preview.known_at_bar_index} · ${preview.unit_ids.join(' → ')}（不可交易）` : '无'}`,
                  `构成三单元：${center.seed_ids.join(' → ')}`,
                  `进入/离开/首次回试：${entry} / ${exit} / ${retest}`,
                  `前向连接：${incomingConnection?.object_id ?? '—'}（来源中枢：${incomingConnection?.from_center_id ?? '—'}）`,
                  `后向连接：${connection?.object_id ?? '—'}（目标中枢：${connection?.to_center_id ?? '尚未形成后中枢'}）`,
                  `扫描起点：单元索引 ${center.scan_floor}`,
                  `形成确认：K${center.formed_at_bar_index}`,
                  `分界确认：${confirmation ? `K${confirmation.event_bar_index} · ${formatTreeTimestamp(confirmation.event_time, dataset)}` : '尚未确认'}`,
                  `规则：${center.rule_version}${center.left_context_incomplete ? ' · 左侧上下文不完整' : ''}`,
                ].join('\n'),
                local_center_unit_kind: center.unit_kind,
                local_center_structural_level: center.structural_level,
              }
            }),
            ...result.objects.center_audit_events.filter((event) => {
              if (event.event_type !== 'BREAK_CONFIRMED') return false
              const center = centerById.get(event.center_id)
              return center?.unit_kind === 'BI' ? visibility.bi_boundary_confirmations
                : center?.unit_kind === 'SEGMENT' ? visibility.segment_boundary_confirmations
                  : false
            }).map((event): ChanTreeObject => {
              const center = centerById.get(event.center_id)!
              const unitLabel = center.unit_kind === 'BI' ? '笔' : '线段'
              return {
                object_id: event.object_id, object_type: 'center_boundary_confirmation',
                layer_category: center.unit_kind === 'BI' ? 'bi_states' : 'segment_boundary_confirmations',
                bar_index: event.event_bar_index, time: event.event_time,
                price_i64: event.comparison_i64 ?? Math.trunc((event.zd_i64 + event.zg_i64) / 2),
                confirmed_at_bar_index: event.event_bar_index, known_at_bar_index: event.known_at_bar_index,
                object_revision: event.object_revision, label: `${unitLabel}分界确认 K${event.event_bar_index}`,
                detail: `中枢 ${event.center_id} · ZD ${event.zd_i64} / ZG ${event.zg_i64} · 比较值 ${event.comparison_i64 ?? '—'}`,
              }
            }),
            ...(visibility.movement_states ? result.objects.movement_states.map((state): ChanTreeObject => ({
              object_id: state.object_id, object_type: 'movement_state', layer_category: 'movement_states',
              bar_index: state.end_bar_index, time: state.end_time, price_i64: state.price_i64,
              confirmed_at_bar_index: state.confirmed_at_bar_index,
              known_at_bar_index: state.known_at_bar_index, object_revision: state.object_revision,
              label: state.state_type === 'consolidation' ? '盘整状态' : state.state_type === 'centre_oscillation' ? '中枢震荡' : state.state_type === 'centre_migration_up' ? '中枢上移' : '中枢下移',
              detail: state.analysis_level,
            })) : []),
            ...(visibility.center_monitors ? result.objects.center_monitors.map((monitor): ChanTreeObject => ({
              object_id: monitor.object_id, object_type: 'center_monitor', layer_category: 'center_monitors',
              bar_index: monitor.bar_index, time: monitor.time, price_i64: monitor.zn_i64,
              confirmed_at_bar_index: monitor.confirmed_at_bar_index,
              known_at_bar_index: monitor.known_at_bar_index, object_revision: monitor.object_revision,
              label: `Zn${monitor.component_ordinal} ${monitor.oscillation_bias === 'strong' ? '强' : monitor.oscillation_bias === 'weak' ? '弱' : '平'}`,
              detail: znMonitorDetail(monitor),
            })) : []),
          )
        }
      }
      return { source, signals, segments }
    }))
    if (generation !== signalLoadGeneration) return
    const byId = new Map<string, ChanTreeObject>()
    const segmentById = new Map<string, ChanLineObject>()
    const bySource: Record<string, ChanTreeObject[]> = {}
    for (const { source, signals, segments } of results) {
      for (const segment of segments) {
        const previous = segmentById.get(segment.object_id)
        if (!previous || segment.object_revision >= previous.object_revision) segmentById.set(segment.object_id, segment)
      }
      const sourceSignals = new Map<string, ChanTreeObject>()
      for (const signal of signals) {
        const current = sourceSignals.get(signal.object_id)
        if (!current || signal.object_revision >= current.object_revision) sourceSignals.set(signal.object_id, signal)
      }
      bySource[source.source_id] = [...sourceSignals.values()]
      for (const signal of sourceSignals.values()) byId.set(signal.object_id, signal)
    }
    signalObjectsBySource.value = bySource
    segmentEvidenceById.value = Object.fromEntries(segmentById)
    if (selectedSignal.value && selectedSignalOrigin.value !== 'trade') selectedSignal.value = byId.get(selectedSignal.value.object_id) ?? null
    if (selectedSignalOrigin.value !== 'trade' && lockedSignalId.value && !byId.has(lockedSignalId.value)) lockedSignalId.value = null
  } catch (cause) {
    if (generation === signalLoadGeneration) workspaceStatus.value = cause instanceof Error ? `信号对象读取失败：${cause.message}` : '信号对象读取失败'
  } finally {
    if (generation === signalLoadGeneration) signalLoading.value = false
  }
}

watch(
  () => `${selectedDataset.value?.dataset_id ?? ''}:${selectedDataset.value?.data_revision ?? ''}:${strategySources.value.map((source) => `${source.job_id}:${source.status}:${source.visible}:${Object.values(completeCategoryVisibility(source.category_visibility)).join(',')}`).join('|')}`,
  () => { void loadSignalObjects() },
)

function proofStateLabel(value: boolean | null | undefined): string {
  return value === true ? '已证实' : value === false ? '已核验不满足' : '未核验'
}

function treeSignal(signal: ChanSignalPoint, objectType: 'divergence' | 'trade_point'): ChanTreeObject {
  const rank = signal.signal_type.endsWith('_1') ? '一' : signal.signal_type.endsWith('_2') ? '二' : '三'
  const buy = signal.signal_type.includes('buy') || signal.signal_type === 'bottom_divergence'
  const divergencePrefix = signal.divergence_kind === 'center_oscillation' ? '中枢震荡'
    : signal.divergence_profile === 'segment_trend_candidate' ? '线段趋势'
      : signal.divergence_kind === 'trend' ? '趋势' : '盘整'
  const label = signal.signal_type.includes('divergence')
    ? `${divergencePrefix}${buy ? '底' : '顶'}背驰${signal.status === 'forming' ? '形成中' : signal.status === 'candidate' ? '候选' : signal.status === 'invalidated' ? '（已失效）' : ''}`
    : `${signal.signal_type.startsWith('class_') ? '类' : ''}${rank}${buy ? '买' : '卖'}`
  const divergenceDetail = objectType === 'divergence'
    ? [
        signal.status === 'invalidated' ? `失效原因 ${signal.invalidation_reason ?? '结构或力度修订'}` : '',
        signal.status === 'forming' ? `c 尚未确认为线段；力度按已观察到的 K 线暂算，后续延伸可撤销（截至 K${signal.known_at_bar_index}）` : '',
        signal.divergence_kind === 'trend' ? `A ${signal.a_center_id ?? '未知'} · B ${signal.b_center_id ?? signal.reference_object_id ?? '未知'}`
          : signal.divergence_kind === 'consolidation' ? `中枢 ${signal.b_center_id ?? signal.reference_object_id ?? '未知'}`
            : `中枢内同向震荡 ${signal.b_center_id ?? signal.reference_object_id ?? '未知'}`,
        signal.divergence_kind === 'trend' ? `a ${signal.a_object_id ?? '未知'} · b ${signal.b_object_id ?? '未知'} · c ${signal.comparison_current_object_id ?? '未知'}`
          : signal.divergence_kind === 'consolidation' ? `a ${signal.a_object_id ?? '未知'} · c ${signal.comparison_current_object_id ?? '未知'}` : '',
        `参照段 ${signal.comparison_reference_object_id ?? '未知'} · 当前段 ${signal.comparison_current_object_id ?? '未知'}`,
        signal.macd_area_reference == null || signal.macd_area_current == null ? '' : `MACD 同向面积 ${signal.macd_area_reference.toFixed(2)} → ${signal.macd_area_current.toFixed(2)}`,
        signal.macd_area_ratio == null ? 'MACD 力度不可比' : `MACD 同向面积比 ${signal.macd_area_ratio.toFixed(3)}`,
        signal.macd_diff_reference_extreme == null || signal.macd_diff_current_extreme == null
          ? '' : `DIFF 同向极值 ${signal.macd_diff_reference_extreme.toFixed(2)} → ${signal.macd_diff_current_extreme.toFixed(2)}`,
        signal.macd_dea_reference_extreme == null || signal.macd_dea_current_extreme == null
          ? '' : `DEA 同向极值 ${signal.macd_dea_reference_extreme.toFixed(2)} → ${signal.macd_dea_current_extreme.toFixed(2)}`,
        signal.macd_extreme_relation == null ? '' : `DIFF/DEA 极值辅助判断：${signal.macd_extreme_relation === 'both_weaker' ? '均减弱' : signal.macd_extreme_relation === 'diff_only' ? '仅 DIFF 减弱' : signal.macd_extreme_relation === 'dea_only' ? '仅 DEA 减弱' : signal.macd_extreme_relation === 'neither_weaker' ? '均未减弱' : '数据不足'}（不单独触发背驰）`,
        `当前段创新极值：${signal.new_extreme_satisfied === true ? '是' : signal.new_extreme_satisfied === false ? '否' : '未记录'}`,
        signal.divergence_kind === 'trend' ? `c 内三类点：${proofStateLabel(signal.c_contains_type3)}；次级别结构：${proofStateLabel(signal.c_meets_sublevel)}` : '',
        signal.c_sublevel_profile === 'bi_two_confirmed_centers_type3_v1' ? `工程映射：c 内已确认笔中枢 ${signal.c_sublevel_center_ids?.length ?? 0}/2（${signal.c_sublevel_center_ids?.join('、') || '无'}）；首次笔级离开 ${signal.c_type3_departure_id ?? '无'} → 回试 ${signal.c_type3_retest_id ?? '无'}；证明确认 K${signal.c_proof_known_at_bar_index ?? '未完成'}` : '',
        `形成方向 ${signal.formation_dir ?? '未知'} · 相对移动 ${signal.relative_dir ?? '未知'}`,
        signal.divergence_profile === 'segment_trend_candidate' ? 'c 内三类点或两个已确认笔中枢的证明尚不齐全；不能升级为标准趋势背驰' : '',
      ].filter(Boolean).join('；')
    : undefined
  return {
    object_id: signal.object_id, object_type: objectType,
    layer_category: objectType === 'divergence' ? divergenceLayer(signal) ?? 'divergences' : tradePointCategory(signal.signal_type) ?? undefined,
    bar_index: signal.bar_index,
    time: signal.time, price_i64: signal.price_i64,
    confirmed_at_bar_index: signal.confirmed_at_bar_index,
    known_at_bar_index: signal.known_at_bar_index, object_revision: signal.object_revision,
    label,
    detail: divergenceDetail ?? (signal.status === 'candidate' ? `${signal.level_id ?? ''} 等待下层转折确认`
      : signal.status === 'invalidated' ? `候选失效：${signal.invalidation_reason ?? '结构修订'}`
        : signal.lower_level_turn_object_id ? `${signal.level_id ?? ''} 下层转折已确认` : undefined),
    signal,
  }
}

function treeLine(line: ChanLineObject, objectType: 'bi' | 'segment', layerCategory: ChanLayerCategory): ChanTreeObject {
  const kind = objectType === 'bi' ? '笔' : '线段'
  return {
    object_id: line.object_id, object_type: objectType, layer_category: layerCategory,
    bar_index: line.end_bar_index, time: line.end_time, price_i64: line.end_price_i64,
    confirmed_at_bar_index: line.confirmed_at_bar_index, known_at_bar_index: line.known_at_bar_index,
    object_revision: line.object_revision,
    label: `${line.direction === 'up' ? '向上' : '向下'}${kind} · ${line.status === 'confirmed' ? '已确认' : line.status === 'candidate' ? '候选' : '已失效'}`,
    detail: `K${line.start_bar_index} → K${line.end_bar_index} · ${line.start_price_i64} → ${line.end_price_i64} · 区间 ${line.range_low_i64}–${line.range_high_i64}`,
  }
}

function selectSignal(signal: ChanTreeObject): void {
  selectedDrawingId.value = null
  selectedSignal.value = signal
  selectedSignalOrigin.value = 'strategy'
}

function selectChartSignal(signal: ChanSignalPoint): void {
  selectSignal(treeSignal(signal, signal.divergence_kind ? 'divergence' : 'trade_point'))
}

function selectDrawingObject(id: string): void {
  selectedSignal.value = null
  selectedSignalOrigin.value = null
  selectedDrawingId.value = id
}

function toggleSignalLock(signal: ChanTreeObject): void {
  selectSignal(signal)
  if (lockedSignalId.value === signal.object_id) {
    lockedSignalId.value = null
    return
  }
  lockedSignalId.value = signal.object_id
  void nextTick(() => chartRef.value?.focusSignal(signal))
}

function addStrategyRunSource(source: StrategyRunSource): void {
  strategyRunSources.value = [
    ...strategyRunSources.value.filter((value) => value.run_id !== source.run_id),
    source,
  ]
  rightTab.value = 'objects'
}

function focusBacktestTrade(trade: BacktestTrade, leg: 'entry' | 'exit' = 'entry'): void {
  const markerId = `${trade.trade_id}:${leg}`
  const executionMarker = strategyRunSources.value
    .flatMap((source) => source.signals)
    .find((candidate) => candidate.object_id === markerId)
  const isEntry = leg === 'entry'
  const isBuy = trade.side === 'long' ? isEntry : !isEntry
  const signal: ChanTreeObject = {
    object_id: markerId,
    bar_index: isEntry ? trade.entry_bar_index : trade.exit_bar_index,
    time: isEntry ? trade.entry_time : trade.exit_time,
    price_i64: isEntry ? trade.entry_price_i64 : trade.exit_price_i64,
    confirmed_at_bar_index: isEntry ? trade.entry_bar_index : trade.exit_bar_index,
    known_at_bar_index: isEntry ? trade.entry_signal_known_at_bar_index : trade.exit_bar_index,
    object_revision: 1, label: isBuy ? '买入' : '卖出',
    detail: typeof executionMarker?.classification_detail === 'string'
      ? executionMarker.classification_detail
      : `${trade.quantity} 手 · ${trade.trade_id}`,
    ...(executionMarker?.third_buy_evidence && typeof executionMarker.third_buy_evidence === 'object'
      ? { third_buy_evidence: executionMarker.third_buy_evidence as ChanTreeObject['third_buy_evidence'] }
      : {}),
  }
  selectedDrawingId.value = null
  selectedSignal.value = signal
  selectedSignalOrigin.value = 'trade'
  lockedSignalId.value = signal.object_id
  void chartRef.value?.focusSignal(signal)
  window.focus()
}

function openBacktestWorkspace(): void {
  const dataset = selectedDataset.value
  if (!dataset) {
    workspaceStatus.value = '请先选择 K 线数据集'
    return
  }
  const popup = window.open(
    createBacktestWorkspaceUrl(dataset, window.location.origin),
    `tvbt-backtest-${dataset.dataset_id.replace(/[^a-zA-Z0-9]/g, '-')}`,
    'popup,width=1480,height=920,resizable=yes,scrollbars=no',
  )
  if (!popup) {
    workspaceStatus.value = '浏览器阻止了回测窗口，请允许本站弹出窗口'
    return
  }
  popup.focus()
  workspaceStatus.value = `已打开并绑定 ${dataset.dataset_id} 的回测窗口`
}

onMounted(() => {
  if (!backtestWorkspaceChannel) return
  backtestWorkspaceChannel.onmessage = (event: MessageEvent<BacktestWorkspaceMessage>) => {
    const message = event.data
    const dataset = selectedDataset.value
    if (!dataset || message.dataset_id !== dataset.dataset_id || message.data_revision !== dataset.data_revision) {
      workspaceStatus.value = '回测窗口与当前 K 线数据版本不一致，未执行联动'
      return
    }
    if (message.type === 'run-completed') addStrategyRunSource(message.source)
    else if (message.type === 'focus-trade') focusBacktestTrade(message.trade, message.leg)
  }
})

function focusResearchTrade(trade: { trade_id: string; entry_bar_index: number; entry_time: number; entry_price_i64: number }): void {
  void chartRef.value?.focusSignal({ object_id: trade.trade_id, bar_index: trade.entry_bar_index, time: trade.entry_time, price_i64: trade.entry_price_i64, confirmed_at_bar_index: trade.entry_bar_index, known_at_bar_index: trade.entry_bar_index, object_revision: 1 })
}

async function installDefaultIndicators(dataset: DatasetMeta, definitions?: AlgorithmDefinition[]): Promise<void> {
  const generation = workspaceGeneration
  const specs = defaultIndicatorSpecs(definitions ?? await listAlgorithms())
  const created = await Promise.all(specs.map(async (spec) => {
    const accepted = await createWorkspaceCalculation({
      dataset_id: dataset.dataset_id,
      data_revision: dataset.data_revision,
      algorithm: {
        kind: spec.definition.kind,
        algorithm_id: spec.definition.algorithm_id,
        algorithm_version: spec.definition.algorithm_version,
        source_hash: spec.definition.source_hash,
      },
      parameters: spec.parameters,
      calculation_mode: 'full_history',
    }, generation)
    if (!accepted) return
    return {
      source_type: 'SeriesSource' as const,
      source_id: spec.sourceId,
      definition: spec.definition,
      parameters: spec.parameters,
      job_id: accepted.job_id,
      status: accepted.status,
    }
  }))
  if (selectedDataset.value?.dataset_id !== dataset.dataset_id || selectedDataset.value.data_revision !== dataset.data_revision) return
  if (generation !== workspaceGeneration) return
  indicatorSources.value = created.filter((source) => source !== undefined)
  for (const source of indicatorSources.value.filter((candidate) => candidate.status !== 'completed')) void trackCalculation(source)
}

function completeCategoryVisibility(value: StrategySource['category_visibility'] | WorkspaceLayout['strategy_sources'][number]['category_visibility']): Required<StrategySource['category_visibility']> {
  const legacy = value as typeof value & { local_centers?: boolean; trade_points?: boolean }
  const biStateVisible = value.bi_states ?? false
  return {
    processed_bars: value.processed_bars ?? false, fractals: value.fractals, bi: value.bi, bi_states: biStateVisible, segments: value.segments ?? true,
    bi_centers: value.bi_centers ?? legacy.local_centers ?? true,
    segment_centers: value.segment_centers ?? false,
    center_objects: value.center_objects ?? false,
    bi_boundary_confirmations: biStateVisible,
    segment_boundary_confirmations: value.segment_boundary_confirmations ?? false,
    movement_states: value.movement_states ?? true,
    center_monitors: value.center_monitors ?? true, divergences: anyDivergenceVisible({ ...value, divergences: value.divergences ?? true }),
    trend_divergences: value.trend_divergences ?? value.divergences ?? true,
    consolidation_divergences: value.consolidation_divergences ?? value.divergences ?? true,
    oscillation_divergences: value.oscillation_divergences ?? value.divergences ?? true,
    first_trade_points: value.first_trade_points ?? legacy.trade_points ?? true,
    second_trade_points: value.second_trade_points ?? legacy.trade_points ?? true,
    third_trade_points: value.third_trade_points ?? legacy.trade_points ?? true,
    class_first_trade_points: value.class_first_trade_points ?? value.first_trade_points ?? legacy.trade_points ?? true,
    class_second_trade_points: value.class_second_trade_points ?? value.second_trade_points ?? legacy.trade_points ?? true,
    class_third_trade_points: value.class_third_trade_points ?? value.third_trade_points ?? legacy.trade_points ?? true,
  }
}

function rememberLayoutStrategyPresentation(source: StrategySource): void {
  if (layoutStrategyPresentation.value[source.source_id]) return
  layoutStrategyPresentation.value = {
    ...layoutStrategyPresentation.value,
    [source.source_id]: { visible: source.visible, category_visibility: completeCategoryVisibility(source.category_visibility) },
  }
}

function applyDynamicStrategyConfig(source: StrategySource, dataset: DatasetMeta): StrategySource {
  const saved = strategySourcePreferences.value.find((item) =>
    item.dataset_id === dataset.dataset_id && item.data_revision === dataset.data_revision && item.source_id === source.source_id)
  if (!saved) return source
  return {
    ...source, visible: saved.visible,
    category_visibility: completeCategoryVisibility(saved.category_visibility),
  }
}

async function installDefaultChan(dataset: DatasetMeta, definitions?: AlgorithmDefinition[]): Promise<void> {
  const generation = workspaceGeneration
  const spec = defaultChanSpec(definitions ?? await listAlgorithms())
  if (!spec) return
  const accepted = await createWorkspaceCalculation({
    dataset_id: dataset.dataset_id,
    data_revision: dataset.data_revision,
    algorithm: {
      kind: spec.definition.kind,
      algorithm_id: spec.definition.algorithm_id,
      algorithm_version: spec.definition.algorithm_version,
      source_hash: spec.definition.source_hash,
    },
    parameters: spec.parameters,
    calculation_mode: 'causal_events',
  }, generation)
  if (!accepted) return
  if (selectedDataset.value?.dataset_id !== dataset.dataset_id || selectedDataset.value.data_revision !== dataset.data_revision) return
  const source: StrategySource = {
    source_type: 'StrategySource', source_id: spec.sourceId, definition: spec.definition,
    parameters: spec.parameters, job_id: accepted.job_id, status: accepted.status,
    visible: true, category_visibility: { processed_bars: false, fractals: false, bi: true, bi_states: false, segments: true, bi_centers: true, segment_centers: true, center_objects: false, bi_boundary_confirmations: false, segment_boundary_confirmations: false, movement_states: true, center_monitors: true, divergences: true, trend_divergences: true, consolidation_divergences: true, oscillation_divergences: true, first_trade_points: true, second_trade_points: true, third_trade_points: true, class_first_trade_points: true, class_second_trade_points: true, class_third_trade_points: true },
  }
  rememberLayoutStrategyPresentation(source)
  strategySources.value = [applyDynamicStrategyConfig(source, dataset)]
  if (source.status !== 'completed') void trackStrategyCalculation(source)
}

async function restoreSources(layout: WorkspaceLayout, dataset: DatasetMeta): Promise<void> {
  const generation = workspaceGeneration
  const definitions = await listAlgorithms()
  if (generation !== workspaceGeneration) return
  const restored: SeriesSource[] = []
  const pending: SeriesSource[] = []
  const restoredStrategies: StrategySource[] = []
  const pendingStrategies: StrategySource[] = []
  const savedSeries = layout.series_sources.filter((item) => item.dataset_id === dataset.dataset_id && item.data_revision === dataset.data_revision)
  for (const saved of savedSeries) {
    const definition = definitions.find((item) => item.algorithm_id === saved.algorithm.algorithm_id && item.source_hash === saved.algorithm.source_hash)
    if (!definition) continue
    const accepted = await createWorkspaceCalculation({
      dataset_id: dataset.dataset_id, data_revision: dataset.data_revision,
      algorithm: saved.algorithm, parameters: saved.parameters, calculation_mode: 'full_history',
    }, generation)
    if (!accepted) return
    const source: SeriesSource = {
      source_type: 'SeriesSource', source_id: saved.source_id, definition,
      parameters: saved.parameters, job_id: accepted.job_id, status: accepted.status,
      style: saved.style,
    }
    restored.push(source)
    if (accepted.status !== 'completed') pending.push(source)
  }
  if (generation !== workspaceGeneration) return
  indicatorSources.value = restored
  for (const source of pending) void trackCalculation(source)
  if (restored.length === 0) await installDefaultIndicators(dataset, definitions)
  if (generation !== workspaceGeneration) return
  for (const saved of (layout.strategy_sources ?? []).filter((item) => item.dataset_id === dataset.dataset_id && item.data_revision === dataset.data_revision)) {
    const definition = definitions.find((item) => item.kind === 'chan' && item.algorithm_id === saved.algorithm.algorithm_id && item.source_hash === saved.algorithm.source_hash)
    if (!definition) continue
    const accepted = await createWorkspaceCalculation({
      dataset_id: dataset.dataset_id, data_revision: dataset.data_revision,
      algorithm: saved.algorithm, parameters: saved.parameters, calculation_mode: 'causal_events',
    }, generation)
    if (!accepted) return
    const source: StrategySource = {
      source_type: 'StrategySource', source_id: saved.source_id, definition,
      parameters: saved.parameters, job_id: accepted.job_id, status: accepted.status,
      visible: saved.visible,
      category_visibility: completeCategoryVisibility(saved.category_visibility),
      style: saved.style,
    }
    rememberLayoutStrategyPresentation(source)
    const configuredSource = applyDynamicStrategyConfig(source, dataset)
    restoredStrategies.push(configuredSource)
    if (accepted.status !== 'completed') pendingStrategies.push(configuredSource)
  }
  if (generation !== workspaceGeneration) return
  strategySources.value = restoredStrategies
  for (const source of pendingStrategies) void trackStrategyCalculation(source)
  if (restoredStrategies.length === 0) await installDefaultChan(dataset, definitions)
}

async function selectDataset(dataset: DatasetMeta, origin: 'automatic' | 'user' = 'user'): Promise<void> {
  workspaceGeneration += 1
  const generation = workspaceGeneration
  cancelWorkspaceJobs()
  if (strategyConfigurationSaveTimer !== undefined) {
    window.clearTimeout(strategyConfigurationSaveTimer)
    strategyConfigurationSaveTimer = undefined
  }
  selectedDataset.value = dataset
  layoutStrategyPresentation.value = {}
  indicatorSources.value = []
  strategySources.value = []
  strategyRunSources.value = []
  replayCursor.value = null
  replayObjects.value = null
  replaySignals.value = []
  selectedDrawingId.value = null
  signalObjectsBySource.value = {}
  segmentEvidenceById.value = {}
  selectedSignal.value = null
  selectedSignalOrigin.value = null
  lockedSignalId.value = null
  workspaceStatus.value = ''
  const [layoutResult, drawingResult, strategyConfigResult] = await Promise.allSettled([
    getLayout(profileId, layoutId), getDrawings<DrawingObject>(profileId, layoutId, dataset.dataset_id), getStrategySourceConfig(profileId),
  ])
  if (generation !== workspaceGeneration) return
  if (strategyConfigResult.status === 'fulfilled') {
    strategySourceConfigRevision.value = strategyConfigResult.value.revision
    strategySourcePreferences.value = strategyConfigResult.value.strategy_sources
  } else if (strategyConfigResult.reason instanceof ApiError && strategyConfigResult.reason.code === 'WORKSPACE_NOT_FOUND') {
    strategySourceConfigRevision.value = 0
    strategySourcePreferences.value = []
  } else {
    strategySourceConfigRevision.value = 0
    strategySourcePreferences.value = []
    workspaceStatus.value = '策略动态配置恢复失败'
  }
  if (layoutResult.status === 'fulfilled') {
    const layout = layoutResult.value
    layoutRevision.value = layout.revision
    if (origin === 'automatic') {
      rightWidth.value = layout.right_panel.width
      rightOpen.value = !layout.right_panel.collapsed
      bottomHeight.value = layout.bottom_panel.height
      bottomOpen.value = !layout.bottom_panel.collapsed
      bottomTab.value = layout.bottom_panel.active_tab
      rightTab.value = layout.right_panel.active_tab === 'object_tree' ? 'objects' : layout.right_panel.active_tab === 'strategy_params' ? 'indicators' : 'datasets'
      await nextTick()
      if (generation !== workspaceGeneration) return
      chartRef.value?.restoreLayout({ panes: layout.panes.map((pane) => ({ id: pane.id, kind: pane.role, weight: pane.weight, minHeight: pane.min_height, collapsed: pane.collapsed })) })
    }
    await restoreSources(layout, dataset)
  } else if (!(layoutResult.reason instanceof ApiError && layoutResult.reason.code === 'WORKSPACE_NOT_FOUND')) {
    workspaceStatus.value = '布局恢复失败'
  } else {
    layoutRevision.value = 0
    try {
      const definitions = await listAlgorithms()
      if (generation !== workspaceGeneration) return
      await Promise.all([installDefaultIndicators(dataset, definitions), installDefaultChan(dataset, definitions)])
    } catch (error) {
      workspaceStatus.value = error instanceof Error ? `默认指标创建失败：${error.message}` : '默认指标创建失败'
    }
  }
  if (generation !== workspaceGeneration) return
  if (drawingResult.status === 'fulfilled' && drawingResult.value.data_revision === dataset.data_revision) {
    drawingRevision.value = drawingResult.value.revision
    drawings.value = drawingHistory.load(drawingResult.value.drawings)
  } else {
    drawingRevision.value = 0
    drawings.value = drawingHistory.load([])
  }
}

function updateReplay(value: { cursor: number | null; objects: ReplayObjects | null; signals: ReplaySignal[] }): void {
  replayCursor.value = value.cursor
  replayObjects.value = value.objects
  replaySignals.value = value.signals
}

function snapshotWorkspaceLayout(): WorkspaceLayout | null {
  const dataset = selectedDataset.value
  const snapshot = chartRef.value?.snapshotLayout()
  if (!dataset || !snapshot) return null
  const now = new Date().toISOString()
  return {
    schema_version: 1, profile_id: profileId, layout_id: layoutId,
    revision: Math.max(1, layoutRevision.value), updated_at: now,
    panes: snapshot.panes.map((pane) => ({
      id: pane.id, role: pane.kind, weight: pane.weight, min_height: pane.minHeight,
      visible: pane.visible, collapsed: pane.collapsed, order: pane.order,
    })),
    right_panel: {
      width: rightWidth.value, collapsed: !rightOpen.value,
      active_tab: rightTab.value === 'objects' ? 'object_tree' : rightTab.value === 'indicators' || rightTab.value === 'strategies' ? 'strategy_params' : 'watchlist',
    },
    bottom_panel: { height: bottomHeight.value, collapsed: !bottomOpen.value, active_tab: bottomTab.value },
    object_order: [
      { id: 'series-candles', pane_id: 'price', z_band: 300, order_in_band: 0, visible: true, locked: true },
      ...drawings.value.map((drawing) => ({ id: drawing.id, pane_id: drawing.pane_id, z_band: drawing.z_band, order_in_band: drawing.order_in_band, visible: drawing.visible, locked: drawing.locked })),
    ],
    series_sources: indicatorSources.value.map((source, order) => ({
      source_id: source.source_id, name: source.definition.name,
      pane_id: source.definition.outputs.some((output) => output.pane === 'main') ? 'price' : 'macd',
      visible: true, locked: false, z_band: 400, order_in_band: order,
      dataset_id: dataset.dataset_id, data_revision: dataset.data_revision,
      algorithm: {
        kind: source.definition.kind, algorithm_id: source.definition.algorithm_id,
        algorithm_version: source.definition.algorithm_version, source_hash: source.definition.source_hash,
      }, parameters: source.parameters, style: source.style,
    })),
    strategy_sources: strategySources.value.map((source, order) => {
      const presentation = layoutStrategyPresentation.value[source.source_id] ?? source
      return {
        source_id: source.source_id, name: source.definition.name, pane_id: 'price',
        visible: presentation.visible, locked: true, z_band: 500 as const, order_in_band: order,
        dataset_id: dataset.dataset_id, data_revision: dataset.data_revision,
        algorithm: {
          kind: 'chan' as const, algorithm_id: source.definition.algorithm_id,
          algorithm_version: source.definition.algorithm_version, source_hash: source.definition.source_hash,
        }, parameters: source.parameters, category_visibility: presentation.category_visibility, style: source.style,
      }
    }),
  }
}

function saveLayout(generation: number): Promise<WorkspaceLayout | null> {
  const operation = layoutWriteQueue.then(async () => {
    if (generation !== workspaceGeneration) return null
    const layout = snapshotWorkspaceLayout()
    if (!layout) return null
    const saved = await putLayout(profileId, layoutId, layoutRevision.value, layout)
    if (generation === workspaceGeneration) layoutRevision.value = saved.revision
    return saved
  })
  layoutWriteQueue = operation.then(() => undefined, () => undefined)
  return operation
}

function snapshotStrategySourceConfig(): StrategySourceDynamicConfig | null {
  const dataset = selectedDataset.value
  if (!dataset) return null
  const otherDatasets = strategySourcePreferences.value.filter((item) =>
    item.dataset_id !== dataset.dataset_id || item.data_revision !== dataset.data_revision)
  return {
    schema_version: 1, profile_id: profileId, revision: Math.max(1, strategySourceConfigRevision.value), updated_at: new Date().toISOString(),
    strategy_sources: [
      ...otherDatasets,
      ...strategySources.value.map((source): StrategySourcePreference => ({
        dataset_id: dataset.dataset_id, data_revision: dataset.data_revision, source_id: source.source_id,
        visible: source.visible, category_visibility: completeCategoryVisibility(source.category_visibility),
      })),
    ],
  }
}

function saveStrategySourceConfiguration(generation: number): Promise<StrategySourceDynamicConfig | null> {
  const operation = strategyConfigWriteQueue.then(async () => {
    if (generation !== workspaceGeneration) return null
    const document = snapshotStrategySourceConfig()
    if (!document) return null
    const saved = await putStrategySourceConfig(profileId, strategySourceConfigRevision.value, document)
    if (generation === workspaceGeneration) {
      strategySourceConfigRevision.value = saved.revision
      strategySourcePreferences.value = saved.strategy_sources
    }
    return saved
  })
  strategyConfigWriteQueue = operation.then(() => undefined, () => undefined)
  return operation
}

function scheduleStrategyConfigurationSave(): void {
  if (!selectedDataset.value || !chartRef.value) return
  if (strategyConfigurationSaveTimer !== undefined) window.clearTimeout(strategyConfigurationSaveTimer)
  const generation = workspaceGeneration
  strategyConfigurationSaveTimer = window.setTimeout(() => {
    strategyConfigurationSaveTimer = undefined
    void saveStrategySourceConfiguration(generation).then((saved) => {
      if (saved && generation === workspaceGeneration) workspaceStatus.value = `策略配置已自动保存 revision ${saved.revision}`
    }).catch((error) => {
      if (generation !== workspaceGeneration) return
      workspaceStatus.value = error instanceof ApiError && error.code === 'WORKSPACE_REVISION_CONFLICT' ? '策略配置保存冲突，请重新加载' : '策略配置自动保存失败'
    })
  }, strategyConfigurationSaveDelayMs)
}

async function saveWorkspace(): Promise<void> {
  const dataset = selectedDataset.value
  if (!dataset || !chartRef.value) { workspaceStatus.value = '请先选择数据集'; return }
  if (strategyConfigurationSaveTimer !== undefined) {
    window.clearTimeout(strategyConfigurationSaveTimer)
    strategyConfigurationSaveTimer = undefined
  }
  const generation = workspaceGeneration
  const now = new Date().toISOString()
  try {
    const savedStrategyConfig = await saveStrategySourceConfiguration(generation)
    if (!savedStrategyConfig || generation !== workspaceGeneration) return
    const savedLayout = await saveLayout(generation)
    if (!savedLayout || generation !== workspaceGeneration) return
    const savedDrawings = await putDrawings(profileId, layoutId, dataset.dataset_id, drawingRevision.value, {
      schema_version: 1, profile_id: profileId, layout_id: layoutId,
      dataset_id: dataset.dataset_id, data_revision: dataset.data_revision,
      revision: Math.max(1, drawingRevision.value), drawings: drawings.value, updated_at: now,
    })
    drawingRevision.value = savedDrawings.revision
    workspaceStatus.value = `已保存 revision ${savedLayout.revision}/${savedDrawings.revision}`
  } catch (error) {
    workspaceStatus.value = error instanceof ApiError && error.code === 'WORKSPACE_REVISION_CONFLICT' ? '保存冲突，请重新加载' : '工作区保存失败'
  }
}

function patchStrategy(id: string, patch: Partial<StrategySource>): void {
  const current = strategySources.value.find((source) => source.source_id === id)
  const selected = selectedSignal.value
  if (current && selected && selectedSignalOrigin.value === 'strategy'
    && (signalObjectsBySource.value[id] ?? []).some((item) => item.object_id === selected.object_id)) {
    const visible = patch.visible ?? current.visible
    const categories = patch.category_visibility ?? current.category_visibility
    const centerSelection = selected.object_type === 'local_center'
    if (!visible || centerSelection && !categories.center_objects
      || selected.object_type === 'divergence' && selected.signal && !divergenceVisible(selected.signal, categories)
      || selected.layer_category !== undefined && selected.object_type !== 'divergence' && !categories[selected.layer_category]) {
      selectedSignal.value = null
      selectedSignalOrigin.value = null
      lockedSignalId.value = null
    }
  }
  strategySources.value = strategySources.value.map((source) => source.source_id === id ? { ...source, ...patch } : source)
  scheduleStrategyConfigurationSave()
}

function removeStrategy(id: string): void {
  strategySources.value = strategySources.value.filter((source) => source.source_id !== id)
  scheduleStrategyConfigurationSave()
}

function updateStrategySources(sources: StrategySource[]): void {
  for (const source of sources) rememberLayoutStrategyPresentation(source)
  strategySources.value = sources
  scheduleStrategyConfigurationSave()
}

function resizeRight(event: PointerEvent): void {
  const startX = event.clientX
  const initial = rightWidth.value
  const move = (next: PointerEvent) => {
    rightWidth.value = Math.max(280, Math.min(600, initial + startX - next.clientX))
  }
  const finish = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', finish)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', finish)
}

function resizeBottom(event: PointerEvent): void {
  if (!bottomOpen.value) return
  const startY = event.clientY
  const initial = bottomHeight.value
  const maximum = window.innerHeight * 0.6
  const move = (next: PointerEvent) => {
    bottomHeight.value = Math.max(160, Math.min(maximum, initial + startY - next.clientY))
  }
  const finish = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', finish)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', finish)
}
</script>

<template>
  <main class="app-shell" :style="{ gridTemplateRows: shellRows }">
    <TopToolbar :health="health" @save="saveWorkspace" @undo="undo" @redo="redo" @replay="bottomOpen = true; bottomTab = 'replay'" />
    <section class="workspace-body" :style="{ gridTemplateColumns: workspaceColumns }">
      <DrawingToolbar
        :tool="drawingTool" :magnet="magnet" :keep-mode="keepDrawingMode"
        @update:tool="drawingTool = $event" @update:magnet="magnet = $event" @update:keep-mode="keepDrawingMode = $event"
        @lock-all="lockAll" @hide-all="hideAll" @delete-selected="deleteSelected" @delete-all="deleteAll"
      />
      <section class="chart-workspace" aria-label="图表工作区">
        <ChartGroup
          ref="chartRef" :dataset="selectedDataset" :indicator-sources="indicatorSources"
          :strategy-sources="strategySources" :replay-cursor="replayCursor" :replay-objects="replayObjects" :replay-signals="visibleStrategySignals"
          :drawings="drawings" :selected-drawing-id="selectedDrawingId" :drawing-tool="drawingTool"
          :selected-signal="selectedSignal" :selected-divergence-segments="selectedDivergenceSegments" :signal-locked="lockedSignalId === selectedSignal?.object_id"
          :magnet="magnet" :keep-drawing-mode="keepDrawingMode"
          @update:drawings="commitDrawings" @update:selected-drawing-id="selectedDrawingId = $event" @update:drawing-tool="drawingTool = $event"
          @select:signal="selectChartSignal"
        />
        <button v-if="!rightOpen" class="reopen-right" @click="rightOpen = true">打开数据集</button>
      </section>
      <button v-if="rightOpen" class="workspace-splitter" aria-label="调整右侧面板宽度" @pointerdown="resizeRight" />
      <aside v-if="rightOpen" class="right-dock" aria-label="右侧面板">
        <nav>
          <button :class="{ active: rightTab === 'datasets' }" @click="rightTab = 'datasets'">数据集</button>
          <button :class="{ active: rightTab === 'indicators' }" @click="rightTab = 'indicators'">指标</button>
          <button :class="{ active: rightTab === 'strategies' }" @click="rightTab = 'strategies'">策略</button>
          <button :class="{ active: rightTab === 'objects' }" @click="rightTab = 'objects'">对象树</button>
          <button class="dock-close" @click="rightOpen = false">收起</button>
        </nav>
        <DatasetPanel v-if="rightTab === 'datasets'" :selected-dataset="selectedDataset" @selected="selectDataset" />
        <IndicatorManagerPanel
          v-else-if="rightTab === 'indicators'" :dataset="selectedDataset"
          :indicator-sources="indicatorSources" :strategy-sources="strategySources"
          @update:indicator-sources="indicatorSources = $event" @update:strategy-sources="updateStrategySources"
        />
        <div v-else-if="rightTab === 'strategies'" class="empty-panel">交易策略参数在回放、回测和优化面板中管理。</div>
        <ObjectTreePanel
          v-else :dataset="selectedDataset" :drawings="drawings" :sources="indicatorSources" :strategy-sources="strategySources" :strategy-run-sources="strategyRunSources"
          :signals-by-source="signalObjectsBySource" :signals-loading="signalLoading"
          :selected-id="selectedDrawingId" :selected-signal-id="selectedSignal?.object_id ?? null" :locked-signal-id="lockedSignalId"
          @patch-drawing="patchDrawing" @remove-drawing="removeDrawing" @reorder-drawing="reorderDrawing" @select-drawing="selectDrawingObject"
          @patch-strategy="patchStrategy" @remove-strategy="removeStrategy" @select-signal="selectSignal" @lock-signal="toggleSignalLock"
        />
      </aside>
    </section>
    <footer class="bottom-dock" :class="{ expanded: bottomOpen }" aria-label="底部面板">
      <button v-if="bottomOpen" class="bottom-splitter" aria-label="调整底部面板高度" @pointerdown="resizeBottom" />
      <nav>
        <button :class="{ active: bottomTab === 'replay' }" @click="bottomTab = 'replay'; bottomOpen = true">回放</button>
        <button aria-label="打开独立回测工作区" @click="openBacktestWorkspace">回测 ↗</button>
        <button :class="{ active: bottomTab === 'trades' }" @click="bottomTab = 'trades'; bottomOpen = true">交易</button>
        <button :class="{ active: bottomTab === 'equity' }" @click="bottomTab = 'equity'; bottomOpen = true">权益</button>
        <button :class="{ active: bottomTab === 'optimization' }" @click="bottomTab = 'optimization'; bottomOpen = true">优化</button>
        <button :class="{ active: bottomTab === 'research' }" @click="bottomTab = 'research'; bottomOpen = true; bottomHeight = Math.max(bottomHeight, 420)">策略研究</button>
        <button @click="bottomOpen = !bottomOpen">{{ bottomOpen ? '收起' : '展开' }}</button>
      </nav>
      <div v-if="bottomOpen" class="bottom-content">
        <ReplayPanel v-if="bottomTab === 'replay'" :dataset="selectedDataset" :source="replaySource" @update="updateReplay" />
        <BacktestPanel v-else-if="['backtest', 'trades', 'equity'].includes(bottomTab)" :dataset="selectedDataset" :view="bottomTab === 'trades' ? 'trades' : bottomTab === 'equity' ? 'equity' : 'backtest'" @completed="addStrategyRunSource" />
        <OptimizationPanel v-else-if="bottomTab === 'optimization'" :dataset="selectedDataset" />
        <StrategyResearchPanel v-else-if="bottomTab === 'research'" :dataset="selectedDataset" @completed="addStrategyRunSource" @focus-trade="focusResearchTrade" />
      </div>
      <span v-else>{{ workspaceStatus || '底部面板已收起' }}</span>
    </footer>
    <KeyboardInstrumentPicker :dataset="selectedDataset" @selected="selectDataset" @focus-bar="focusKeyboardBar" />
  </main>
</template>
