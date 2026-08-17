<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import TopToolbar from './TopToolbar.vue'
import DatasetPanel from './DatasetPanel.vue'
import IndicatorManagerPanel from './IndicatorManagerPanel.vue'
import DrawingToolbar from './DrawingToolbar.vue'
import ObjectTreePanel from './ObjectTreePanel.vue'
import ChartGroup from './ChartGroup.vue'
import ReplayPanel from './ReplayPanel.vue'
import BacktestPanel from './BacktestPanel.vue'
import OptimizationPanel from './OptimizationPanel.vue'
import KeyboardInstrumentPicker from './KeyboardInstrumentPicker.vue'
import { ApiError, createCalculation, getCalculation, getCalculationResults, getDrawings, getLayout, getStrategySourceConfig, listAlgorithms, putDrawings, putLayout, putStrategySourceConfig } from '../api/client'
import { DrawingHistory, LayerManager, type DrawingObject, type DrawingType } from '../drawing/model'
import { defaultIndicatorSpecs } from '../indicators/defaults'
import { defaultChanSpec } from '../chan/defaults'
import type { ReplayObjects, ReplaySignal } from '../replay/eventIndex'
import type { AlgorithmDefinition, ChanSignalPoint, ChanTreeObject, DatasetMeta, SeriesSource, StrategyRunSource, StrategySource, StrategySourceDynamicConfig, StrategySourcePreference, WorkspaceLayout } from '../types/api'

defineProps<{ health: string }>()

const selectedDataset = ref<DatasetMeta | null>(null)
const rightOpen = ref(true)
const rightWidth = ref(320)
const bottomOpen = ref(false)
const bottomHeight = ref(260)
const bottomTab = ref<'replay' | 'backtest' | 'trades' | 'equity' | 'optimization' | 'tasks' | 'logs'>('replay')
const rightTab = ref<'datasets' | 'indicators' | 'strategies' | 'objects'>('datasets')
const indicatorSources = ref<SeriesSource[]>([])
const strategySources = ref<StrategySource[]>([])
const strategyRunSources = ref<StrategyRunSource[]>([])
const drawings = ref<DrawingObject[]>([])
const selectedDrawingId = ref<string | null>(null)
const signalObjectsBySource = ref<Record<string, ChanTreeObject[]>>({})
const selectedSignal = ref<ChanTreeObject | null>(null)
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
} | null>(null)
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
const workspaceColumns = computed(() => rightOpen.value
  ? `48px minmax(320px, 1fr) 1px ${rightWidth.value}px`
  : '48px minmax(320px, 1fr)')
const shellRows = computed(() => `44px minmax(0, 1fr) ${bottomOpen.value ? bottomHeight.value : 28}px`)

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

async function trackCalculation(source: SeriesSource): Promise<void> {
  for (;;) {
    const status = await getCalculation(source.job_id)
    indicatorSources.value = indicatorSources.value.map((item) => item.source_id === source.source_id
      ? { ...item, status: status.status, error: status.error?.message }
      : item)
    if (status.status === 'completed' || ['failed', 'cancelled', 'interrupted'].includes(status.status)) return
    await new Promise((resolve) => window.setTimeout(resolve, 250))
  }
}

async function trackStrategyCalculation(source: StrategySource): Promise<void> {
  for (;;) {
    const status = await getCalculation(source.job_id)
    strategySources.value = strategySources.value.map((item) => item.source_id === source.source_id
      ? { ...item, status: status.status, error: status.error?.message }
      : item)
    if (status.status === 'completed' || ['failed', 'cancelled', 'interrupted'].includes(status.status)) return
    await new Promise((resolve) => window.setTimeout(resolve, 250))
  }
}

async function loadSignalObjects(): Promise<void> {
  const dataset = selectedDataset.value
  const sources = strategySources.value.filter((source) => source.status === 'completed')
  const generation = ++signalLoadGeneration
  if (!dataset || sources.length === 0) {
    signalObjectsBySource.value = {}
    selectedSignal.value = null
    lockedSignalId.value = null
    return
  }
  signalLoading.value = true
  try {
    const results = await Promise.all(sources.map(async (source) => {
      const signals: ChanTreeObject[] = []
      for (let from = dataset.coverage.first_bar_index; from <= dataset.coverage.last_bar_index; from += 5000) {
        const to = Math.min(dataset.coverage.last_bar_index, from + 4999)
        const result = await getCalculationResults(source.job_id, from, to)
        if (result.result_kind === 'chan') {
          signals.push(
            ...result.objects.divergences.map((signal) => treeSignal(signal, 'divergence')),
            ...result.objects.trade_points.map((signal) => treeSignal(signal, 'trade_point')),
            ...result.objects.movement_states.map((state): ChanTreeObject => ({
              object_id: state.object_id, object_type: 'movement_state',
              bar_index: state.end_bar_index, time: state.end_time, price_i64: state.price_i64,
              confirmed_at_bar_index: state.confirmed_at_bar_index,
              known_at_bar_index: state.known_at_bar_index, object_revision: state.object_revision,
              label: state.state_type === 'consolidation' ? '盘整状态' : state.state_type === 'centre_oscillation' ? '中枢震荡' : state.state_type === 'centre_migration_up' ? '中枢上移' : '中枢下移',
              detail: state.analysis_level,
            })),
            ...result.objects.center_monitors.map((monitor): ChanTreeObject => ({
              object_id: monitor.object_id, object_type: 'center_monitor',
              bar_index: monitor.bar_index, time: monitor.time, price_i64: monitor.zn_i64,
              confirmed_at_bar_index: monitor.confirmed_at_bar_index,
              known_at_bar_index: monitor.known_at_bar_index, object_revision: monitor.object_revision,
              label: `Zn ${monitor.strength === 'strong' ? '强' : monitor.strength === 'weak' ? '弱' : '平'}`,
              detail: monitor.migration_warning ? `迁移预警 ${monitor.migration_warning === 'up' ? '↑' : '↓'}` : monitor.relative_position,
            })),
          )
        }
      }
      return { source, signals }
    }))
    if (generation !== signalLoadGeneration) return
    const byId = new Map<string, ChanTreeObject>()
    const bySource: Record<string, ChanTreeObject[]> = {}
    for (const { source, signals } of results) {
      const sourceSignals = new Map<string, ChanTreeObject>()
      for (const signal of signals) {
        const current = sourceSignals.get(signal.object_id)
        if (!current || signal.object_revision >= current.object_revision) sourceSignals.set(signal.object_id, signal)
      }
      bySource[source.source_id] = [...sourceSignals.values()]
      for (const signal of sourceSignals.values()) byId.set(signal.object_id, signal)
    }
    signalObjectsBySource.value = bySource
    if (selectedSignal.value) selectedSignal.value = byId.get(selectedSignal.value.object_id) ?? null
    if (lockedSignalId.value && !byId.has(lockedSignalId.value)) lockedSignalId.value = null
  } catch (cause) {
    if (generation === signalLoadGeneration) workspaceStatus.value = cause instanceof Error ? `信号对象读取失败：${cause.message}` : '信号对象读取失败'
  } finally {
    if (generation === signalLoadGeneration) signalLoading.value = false
  }
}

watch(
  () => `${selectedDataset.value?.dataset_id ?? ''}:${selectedDataset.value?.data_revision ?? ''}:${strategySources.value.map((source) => `${source.job_id}:${source.status}`).join('|')}`,
  () => { void loadSignalObjects() },
)

function treeSignal(signal: ChanSignalPoint, objectType: 'divergence' | 'trade_point'): ChanTreeObject {
  const rank = signal.signal_type.endsWith('_1') ? '一' : signal.signal_type.endsWith('_2') ? '二' : '三'
  const buy = signal.signal_type.includes('buy') || signal.signal_type === 'bottom_divergence'
  const label = signal.signal_type.includes('divergence')
    ? `${signal.divergence_kind === 'trend' ? '趋势' : '盘整'}${buy ? '底' : '顶'}背驰`
    : `${signal.signal_type.startsWith('class_') ? '类' : ''}${rank}${buy ? '买' : '卖'}`
  return {
    object_id: signal.object_id, object_type: objectType, bar_index: signal.bar_index,
    time: signal.time, price_i64: signal.price_i64,
    confirmed_at_bar_index: signal.confirmed_at_bar_index,
    known_at_bar_index: signal.known_at_bar_index, object_revision: signal.object_revision,
    label, signal,
  }
}

function selectSignal(signal: ChanTreeObject): void {
  selectedDrawingId.value = null
  selectedSignal.value = signal
}

function selectDrawingObject(id: string): void {
  selectedSignal.value = null
  selectedDrawingId.value = id
}

function toggleSignalLock(signal: ChanTreeObject): void {
  selectSignal(signal)
  if (lockedSignalId.value === signal.object_id) {
    lockedSignalId.value = null
    return
  }
  lockedSignalId.value = signal.object_id
  void chartRef.value?.focusSignal(signal)
}

function addStrategyRunSource(source: StrategyRunSource): void {
  strategyRunSources.value = [
    ...strategyRunSources.value.filter((value) => value.run_id !== source.run_id),
    source,
  ]
  rightTab.value = 'objects'
}

async function installDefaultIndicators(dataset: DatasetMeta, definitions?: AlgorithmDefinition[]): Promise<void> {
  const specs = defaultIndicatorSpecs(definitions ?? await listAlgorithms())
  const created = await Promise.all(specs.map(async (spec) => {
    const accepted = await createCalculation({
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
    })
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
  indicatorSources.value = created
  for (const source of created.filter((candidate) => candidate.status !== 'completed')) void trackCalculation(source)
}

function completeCategoryVisibility(value: StrategySource['category_visibility']): Required<StrategySource['category_visibility']> {
  return {
    fractals: value.fractals, bi: value.bi, segments: value.segments ?? true, zhongshu: value.zhongshu,
    segment_zhongshu: value.segment_zhongshu ?? true, movement_states: value.movement_states ?? true,
    center_monitors: value.center_monitors ?? true, divergences: value.divergences ?? true, trade_points: value.trade_points ?? true,
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
  return saved ? { ...source, visible: saved.visible, category_visibility: saved.category_visibility } : source
}

async function installDefaultChan(dataset: DatasetMeta, definitions?: AlgorithmDefinition[]): Promise<void> {
  const spec = defaultChanSpec(definitions ?? await listAlgorithms())
  if (!spec) return
  const accepted = await createCalculation({
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
  })
  if (selectedDataset.value?.dataset_id !== dataset.dataset_id || selectedDataset.value.data_revision !== dataset.data_revision) return
  const source: StrategySource = {
    source_type: 'StrategySource', source_id: spec.sourceId, definition: spec.definition,
    parameters: spec.parameters, job_id: accepted.job_id, status: accepted.status,
    visible: true, category_visibility: { fractals: false, bi: true, segments: true, zhongshu: true, segment_zhongshu: true, movement_states: true, center_monitors: true, divergences: true, trade_points: true },
  }
  rememberLayoutStrategyPresentation(source)
  strategySources.value = [applyDynamicStrategyConfig(source, dataset)]
  if (source.status !== 'completed') void trackStrategyCalculation(source)
}

async function restoreSources(layout: WorkspaceLayout, dataset: DatasetMeta): Promise<void> {
  const definitions = await listAlgorithms()
  const restored: SeriesSource[] = []
  const pending: SeriesSource[] = []
  const restoredStrategies: StrategySource[] = []
  const pendingStrategies: StrategySource[] = []
  const savedSeries = layout.series_sources.filter((item) => item.dataset_id === dataset.dataset_id && item.data_revision === dataset.data_revision)
  for (const saved of savedSeries) {
    const definition = definitions.find((item) => item.algorithm_id === saved.algorithm.algorithm_id && item.source_hash === saved.algorithm.source_hash)
    if (!definition) continue
    const accepted = await createCalculation({
      dataset_id: dataset.dataset_id, data_revision: dataset.data_revision,
      algorithm: saved.algorithm, parameters: saved.parameters, calculation_mode: 'full_history',
    })
    const source: SeriesSource = {
      source_type: 'SeriesSource', source_id: saved.source_id, definition,
      parameters: saved.parameters, job_id: accepted.job_id, status: accepted.status,
      style: saved.style,
    }
    restored.push(source)
    if (accepted.status !== 'completed') pending.push(source)
  }
  indicatorSources.value = restored
  for (const source of pending) void trackCalculation(source)
  if (restored.length === 0) await installDefaultIndicators(dataset, definitions)
  for (const saved of (layout.strategy_sources ?? []).filter((item) => item.dataset_id === dataset.dataset_id && item.data_revision === dataset.data_revision)) {
    const definition = definitions.find((item) => item.kind === 'chan' && item.algorithm_id === saved.algorithm.algorithm_id && item.source_hash === saved.algorithm.source_hash)
    if (!definition) continue
    const accepted = await createCalculation({
      dataset_id: dataset.dataset_id, data_revision: dataset.data_revision,
      algorithm: saved.algorithm, parameters: saved.parameters, calculation_mode: 'causal_events',
    })
    const source: StrategySource = {
      source_type: 'StrategySource', source_id: saved.source_id, definition,
      parameters: saved.parameters, job_id: accepted.job_id, status: accepted.status,
      visible: saved.visible,
      category_visibility: {
        ...saved.category_visibility,
        segments: saved.category_visibility.segments ?? true,
        segment_zhongshu: saved.category_visibility.segment_zhongshu ?? true,
        movement_states: saved.category_visibility.movement_states ?? true,
        center_monitors: saved.category_visibility.center_monitors ?? true,
        divergences: saved.category_visibility.divergences ?? true,
        trade_points: saved.category_visibility.trade_points ?? true,
      },
      style: saved.style,
    }
    rememberLayoutStrategyPresentation(source)
    const configuredSource = applyDynamicStrategyConfig(source, dataset)
    restoredStrategies.push(configuredSource)
    if (accepted.status !== 'completed') pendingStrategies.push(configuredSource)
  }
  strategySources.value = restoredStrategies
  for (const source of pendingStrategies) void trackStrategyCalculation(source)
  if (restoredStrategies.length === 0) await installDefaultChan(dataset, definitions)
}

async function selectDataset(dataset: DatasetMeta): Promise<void> {
  workspaceGeneration += 1
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
  selectedSignal.value = null
  lockedSignalId.value = null
  workspaceStatus.value = ''
  const [layoutResult, drawingResult, strategyConfigResult] = await Promise.allSettled([
    getLayout(profileId, layoutId), getDrawings<DrawingObject>(profileId, layoutId, dataset.dataset_id), getStrategySourceConfig(profileId),
  ])
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
    rightWidth.value = layout.right_panel.width
    rightOpen.value = !layout.right_panel.collapsed
    bottomHeight.value = layout.bottom_panel.height
    bottomOpen.value = !layout.bottom_panel.collapsed
    bottomTab.value = layout.bottom_panel.active_tab
    rightTab.value = layout.right_panel.active_tab === 'object_tree' ? 'objects' : layout.right_panel.active_tab === 'strategy_params' ? 'indicators' : 'datasets'
    await nextTick()
    chartRef.value?.restoreLayout({ panes: layout.panes.map((pane) => ({ id: pane.id, kind: pane.role, weight: pane.weight, minHeight: pane.min_height, collapsed: pane.collapsed })) })
    await restoreSources(layout, dataset)
  } else if (!(layoutResult.reason instanceof ApiError && layoutResult.reason.code === 'WORKSPACE_NOT_FOUND')) {
    workspaceStatus.value = '布局恢复失败'
  } else {
    layoutRevision.value = 0
    try {
      const definitions = await listAlgorithms()
      await Promise.all([installDefaultIndicators(dataset, definitions), installDefaultChan(dataset, definitions)])
    } catch (error) {
      workspaceStatus.value = error instanceof Error ? `默认指标创建失败：${error.message}` : '默认指标创建失败'
    }
  }
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
          :selected-signal="selectedSignal" :signal-locked="lockedSignalId === selectedSignal?.object_id"
          :magnet="magnet" :keep-drawing-mode="keepDrawingMode"
          @update:drawings="commitDrawings" @update:selected-drawing-id="selectedDrawingId = $event" @update:drawing-tool="drawingTool = $event"
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
        <button :class="{ active: bottomTab === 'backtest' }" @click="bottomTab = 'backtest'; bottomOpen = true">回测</button>
        <button :class="{ active: bottomTab === 'trades' }" @click="bottomTab = 'trades'; bottomOpen = true">交易</button>
        <button :class="{ active: bottomTab === 'equity' }" @click="bottomTab = 'equity'; bottomOpen = true">权益</button>
        <button :class="{ active: bottomTab === 'optimization' }" @click="bottomTab = 'optimization'; bottomOpen = true">优化</button>
        <button @click="bottomOpen = !bottomOpen">{{ bottomOpen ? '收起' : '展开' }}</button>
      </nav>
      <div v-if="bottomOpen" class="bottom-content">
        <ReplayPanel v-if="bottomTab === 'replay'" :dataset="selectedDataset" :source="replaySource" @update="updateReplay" />
        <BacktestPanel v-else-if="['backtest', 'trades', 'equity'].includes(bottomTab)" :dataset="selectedDataset" :view="bottomTab === 'trades' ? 'trades' : bottomTab === 'equity' ? 'equity' : 'backtest'" @completed="addStrategyRunSource" />
        <OptimizationPanel v-else-if="bottomTab === 'optimization'" :dataset="selectedDataset" />
      </div>
      <span v-else>{{ workspaceStatus || '底部面板已收起' }}</span>
    </footer>
    <KeyboardInstrumentPicker @selected="selectDataset" />
  </main>
</template>
