<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
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
import { ApiError, createCalculation, getCalculation, getDrawings, getLayout, listAlgorithms, putDrawings, putLayout } from '../api/client'
import { DrawingHistory, LayerManager, type DrawingObject, type DrawingType } from '../drawing/model'
import { defaultIndicatorSpecs } from '../indicators/defaults'
import { defaultChanSpec } from '../chan/defaults'
import type { ReplayObjects, ReplaySignal } from '../replay/eventIndex'
import type { AlgorithmDefinition, DatasetMeta, SeriesSource, StrategySource, WorkspaceLayout } from '../types/api'

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
const drawings = ref<DrawingObject[]>([])
const selectedDrawingId = ref<string | null>(null)
const drawingTool = ref<DrawingType | 'cursor'>('cursor')
const magnet = ref(false)
const keepDrawingMode = ref(false)
const workspaceStatus = ref('')
const layoutRevision = ref(0)
const drawingRevision = ref(0)
const replayCursor = ref<number | null>(null)
const replayObjects = ref<ReplayObjects | null>(null)
const replaySignals = ref<ReplaySignal[]>([])
const replaySource = computed(() => strategySources.value.find((source) => source.status === 'completed') ?? null)
const chartRef = ref<{
  snapshotLayout: () => { panes: Array<{ id: string; kind: 'price' | 'indicator'; weight: number; minHeight: number; visible: boolean; collapsed: boolean; order: number }> }
  restoreLayout: (value: { panes: Array<{ id: string; kind: 'price' | 'indicator'; weight: number; minHeight: number; collapsed?: boolean }> }) => void
} | null>(null)
const drawingHistory = new DrawingHistory()
const layerManager = new LayerManager()
const profileId = 'default'
const layoutId = 'default-three-pane'
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
    visible: true, category_visibility: { fractals: false, bi: true, zhongshu: true },
  }
  strategySources.value = [source]
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
      visible: saved.visible, category_visibility: saved.category_visibility, style: saved.style,
    }
    restoredStrategies.push(source)
    if (accepted.status !== 'completed') pendingStrategies.push(source)
  }
  strategySources.value = restoredStrategies
  for (const source of pendingStrategies) void trackStrategyCalculation(source)
  if (restoredStrategies.length === 0) await installDefaultChan(dataset, definitions)
}

async function selectDataset(dataset: DatasetMeta): Promise<void> {
  selectedDataset.value = dataset
  indicatorSources.value = []
  strategySources.value = []
  replayCursor.value = null
  replayObjects.value = null
  replaySignals.value = []
  selectedDrawingId.value = null
  workspaceStatus.value = ''
  const [layoutResult, drawingResult] = await Promise.allSettled([
    getLayout(profileId, layoutId), getDrawings<DrawingObject>(profileId, layoutId, dataset.dataset_id),
  ])
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

async function saveWorkspace(): Promise<void> {
  const dataset = selectedDataset.value
  const snapshot = chartRef.value?.snapshotLayout()
  if (!dataset || !snapshot) { workspaceStatus.value = '请先选择数据集'; return }
  const now = new Date().toISOString()
  const layout: WorkspaceLayout = {
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
    strategy_sources: strategySources.value.map((source, order) => ({
      source_id: source.source_id, name: source.definition.name, pane_id: 'price',
      visible: source.visible, locked: true, z_band: 500 as const, order_in_band: order,
      dataset_id: dataset.dataset_id, data_revision: dataset.data_revision,
      algorithm: {
        kind: 'chan' as const, algorithm_id: source.definition.algorithm_id,
        algorithm_version: source.definition.algorithm_version, source_hash: source.definition.source_hash,
      }, parameters: source.parameters, category_visibility: source.category_visibility, style: source.style,
    })),
  }
  try {
    const savedLayout = await putLayout(profileId, layoutId, layoutRevision.value, layout)
    layoutRevision.value = savedLayout.revision
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
}

function removeStrategy(id: string): void {
  strategySources.value = strategySources.value.filter((source) => source.source_id !== id)
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
          :strategy-sources="strategySources" :replay-cursor="replayCursor" :replay-objects="replayObjects" :replay-signals="replaySignals"
          :drawings="drawings" :selected-drawing-id="selectedDrawingId" :drawing-tool="drawingTool"
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
          @update:indicator-sources="indicatorSources = $event" @update:strategy-sources="strategySources = $event"
        />
        <div v-else-if="rightTab === 'strategies'" class="empty-panel">交易策略参数在回放、回测和优化面板中管理。</div>
        <ObjectTreePanel
          v-else :drawings="drawings" :sources="indicatorSources" :strategy-sources="strategySources" :selected-id="selectedDrawingId"
          @patch-drawing="patchDrawing" @remove-drawing="removeDrawing" @reorder-drawing="reorderDrawing" @select-drawing="selectedDrawingId = $event"
          @patch-strategy="patchStrategy" @remove-strategy="removeStrategy"
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
        <BacktestPanel v-else-if="['backtest', 'trades', 'equity'].includes(bottomTab)" :dataset="selectedDataset" :view="bottomTab === 'trades' ? 'trades' : bottomTab === 'equity' ? 'equity' : 'backtest'" />
        <OptimizationPanel v-else-if="bottomTab === 'optimization'" :dataset="selectedDataset" />
      </div>
      <span v-else>{{ workspaceStatus || '底部面板已收起' }}</span>
    </footer>
    <KeyboardInstrumentPicker @selected="selectDataset" />
  </main>
</template>
