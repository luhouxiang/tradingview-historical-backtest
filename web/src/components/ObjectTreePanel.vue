<script setup lang="ts">
import { ref } from 'vue'
import { chanSignalLabel } from '../chart/chanPrimitive'
import type { DrawingObject } from '../drawing/model'
import type { ChanSignalPoint, ChanTreeObject, DatasetMeta, SeriesSource, StrategyRunSource, StrategySource } from '../types/api'

const props = defineProps<{
  dataset?: DatasetMeta | null
  drawings: DrawingObject[]
  sources: SeriesSource[]
  strategySources: StrategySource[]
  strategyRunSources?: StrategyRunSource[]
  signalsBySource?: Record<string, ChanTreeObject[]>
  signalsLoading?: boolean
  selectedId: string | null
  selectedSignalId?: string | null
  lockedSignalId?: string | null
}>()
const emit = defineEmits<{
  patchDrawing: [id: string, patch: Partial<DrawingObject>]
  removeDrawing: [id: string]
  reorderDrawing: [id: string, direction: -1 | 1]
  selectDrawing: [id: string]
  patchStrategy: [id: string, patch: Partial<StrategySource>]
  removeStrategy: [id: string]
  selectSignal: [signal: ChanTreeObject]
  lockSignal: [signal: ChanTreeObject]
}>()

const collapsedStrategies = ref(new Set<string>())
const objectLimits = ref<Record<string, number>>({})
const objectPageSize = 300

function toggleStrategy(sourceId: string): void {
  const next = new Set(collapsedStrategies.value)
  if (next.has(sourceId)) next.delete(sourceId)
  else next.add(sourceId)
  collapsedStrategies.value = next
}

function allSignalsFor(source: StrategySource): ChanTreeObject[] {
  if (!source.visible) return []
  return [...(props.signalsBySource?.[source.source_id] ?? [])]
    .filter((item) => {
      if (item.object_type === 'local_center' && !source.category_visibility.center_objects) return false
      const category = objectCategory(item)
      if (!category) return true
      if (category.startsWith('class_')) {
        const standard = category.slice('class_'.length) as keyof StrategySource['category_visibility']
        return source.category_visibility[category] ?? source.category_visibility[standard] ?? false
      }
      return Boolean(source.category_visibility[category])
    })
    .sort((left, right) =>
    right.bar_index - left.bar_index || right.known_at_bar_index - left.known_at_bar_index || right.object_id.localeCompare(left.object_id),
  )
}

function objectCategory(value: ChanTreeObject): keyof StrategySource['category_visibility'] | undefined {
  if (value.object_type === 'bi') return 'bi_states'
  if (value.object_type === 'segment') return 'segment_boundary_confirmations'
  return value.layer_category
}

function signalsFor(source: StrategySource): ChanTreeObject[] {
  return allSignalsFor(source).slice(0, objectLimits.value[source.source_id] ?? objectPageSize)
}

function showMore(source: StrategySource): void {
  objectLimits.value = {
    ...objectLimits.value,
    [source.source_id]: (objectLimits.value[source.source_id] ?? objectPageSize) + objectPageSize,
  }
}

function objectCategoryLabel(value: ChanTreeObject): string {
  const category = objectCategory(value)
  return category ? categoryLabels[category] : '策略对象'
}

function formatSignalTime(timestamp: number): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: props.dataset?.time?.timezone ?? 'Asia/Shanghai',
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(timestamp)).replaceAll('/', '-')
}

function formatSignalPrice(signal: ChanTreeObject): string {
  const scale = props.dataset?.price?.price_scale ?? 1
  return (signal.price_i64 / scale).toFixed(props.dataset?.price?.price_decimals ?? 0)
}

function objectLabel(value: ChanTreeObject): string {
  if (value.label) return value.label
  if ('signal_type' in value) return chanSignalLabel(value as ChanSignalPoint)
  return value.object_type ?? '缠论对象'
}

function objectSide(value: ChanTreeObject): 'buy' | 'sell' | 'semantic' {
  const signal = value.signal ?? ('signal_type' in value ? value as ChanSignalPoint : null)
  if (!signal) return 'semantic'
  return signal.signal_type.includes('buy') || signal.signal_type === 'bottom_divergence' ? 'buy' : 'sell'
}

const categoryLabels: Record<keyof StrategySource['category_visibility'], string> = {
  processed_bars: '处理后K线', fractals: '分型', bi: '笔', bi_states: '笔状态', segments: '线段',
  bi_centers: '笔中枢', segment_centers: '线段中枢', center_objects: '中枢对象', movement_states: '走势状态',
  bi_boundary_confirmations: '笔状态', segment_boundary_confirmations: '线段状态',
  center_monitors: '中枢监控', divergences: '背驰',
  first_trade_points: '一买卖点', second_trade_points: '二买卖点', third_trade_points: '三买卖点',
  class_first_trade_points: '类一买卖点', class_second_trade_points: '类二买卖点', class_third_trade_points: '类三买卖点',
}
const categoryGroups: ReadonlyArray<ReadonlyArray<keyof StrategySource['category_visibility']>> = [
  ['processed_bars', 'fractals'],
  ['bi', 'bi_states', 'segments', 'segment_boundary_confirmations'],
  ['bi_centers', 'segment_centers', 'center_objects'],
  ['movement_states', 'center_monitors', 'divergences'],
  ['first_trade_points', 'second_trade_points', 'third_trade_points'],
  ['class_first_trade_points', 'class_second_trade_points', 'class_third_trade_points'],
]

function toggleCategory(source: StrategySource, category: keyof StrategySource['category_visibility']): void {
  const enabled = !source.category_visibility[category]
  const next = { ...source.category_visibility, [category]: enabled }
  if (category === 'bi_states') {
    next.bi_boundary_confirmations = enabled
  }
  emit('patchStrategy', source.source_id, { category_visibility: next })
}
</script>

<template>
  <section class="object-tree">
    <h3>SeriesSource</h3>
    <div v-for="source in sources" :key="source.source_id" class="object-node" data-object-type="SeriesSource">
      {{ source.definition.name }} · {{ source.status }}
    </div>
    <h3>StrategySource</h3>
    <article v-for="source in strategySources" :key="source.source_id" class="object-node strategy-node" data-object-type="StrategySource">
      <header class="strategy-header">
        <button class="tree-toggle" :title="collapsedStrategies.has(source.source_id) ? '展开' : '折叠'" @click="toggleStrategy(source.source_id)">
          {{ collapsedStrategies.has(source.source_id) ? '▸' : '▾' }}
        </button>
        <strong>{{ source.definition.name }}</strong>
        <span class="strategy-status">{{ source.status }}</span>
        <button title="显示/隐藏策略图层" @click="emit('patchStrategy', source.source_id, { visible: !source.visible })">{{ source.visible ? '◉' : '○' }}</button>
        <button title="删除策略" @click="emit('removeStrategy', source.source_id)">×</button>
      </header>
      <p v-if="source.error" class="calculation-error" role="alert">{{ source.error }}</p>
      <div v-if="!collapsedStrategies.has(source.source_id)" class="strategy-children">
        <details class="strategy-categories">
          <summary>图层分类</summary>
          <div v-for="(group, groupIndex) in categoryGroups" :key="groupIndex" class="strategy-category-group">
            <label v-for="category in group" :key="category">
              <input
                type="checkbox" :checked="source.category_visibility[category]"
                @change="toggleCategory(source, category)"
              />{{ categoryLabels[category] }}
            </label>
          </div>
        </details>
        <div class="signal-branch-title"><span>└─ 缠论对象</span><small>{{ allSignalsFor(source).length }}</small></div>
        <p v-if="signalsLoading" class="signal-tree-empty">正在读取缠论对象…</p>
        <p v-else-if="allSignalsFor(source).length === 0" class="signal-tree-empty">已选图层暂无对应缠论对象</p>
        <div
          v-for="signal in signalsFor(source)" :key="signal.object_id"
          class="signal-object-node" :class="{ selected: selectedSignalId === signal.object_id }"
          data-object-type="ChanSignalObject" :data-signal-id="signal.object_id"
          role="button" tabindex="0" :title="signal.hover_detail ?? signal.detail" @click="emit('selectSignal', signal)" @keydown.enter="emit('selectSignal', signal)"
        >
          <span class="tree-elbow">└</span>
          <span class="signal-object-content">
            <span class="signal-layer-category">{{ objectCategoryLabel(signal) }}</span>
            <strong :class="objectSide(signal)">{{ objectLabel(signal) }}</strong>
            <small v-if="signal.detail" class="signal-object-detail">{{ signal.detail }}</small>
            <small>{{ formatSignalTime(signal.time) }} · {{ formatSignalPrice(signal) }} · #{{ signal.bar_index }}</small>
          </span>
          <button
            class="signal-object-lock" :class="{ active: lockedSignalId === signal.object_id }"
            :title="lockedSignalId === signal.object_id ? '取消锁定' : '锁定并定位'"
            @click.stop="emit('lockSignal', signal)"
          >{{ lockedSignalId === signal.object_id ? '🔒' : '🔓' }}</button>
        </div>
        <button v-if="signalsFor(source).length < allSignalsFor(source).length" class="signal-load-more" @click="showMore(source)">
          再显示 {{ Math.min(objectPageSize, allSignalsFor(source).length - signalsFor(source).length) }} 个
        </button>
      </div>
    </article>
    <h3>用户绘图</h3>
    <article v-for="source in strategyRunSources" :key="source.source_id" class="object-node strategy-node strategy-run-node" data-object-type="StrategyRunSource">
      <header class="strategy-header">
        <strong>{{ source.definition.name }}</strong>
        <span class="strategy-status">{{ source.run_id }}</span>
      </header>
      <div class="strategy-children">
        <div class="signal-branch-title">
          <span>{{ source.definition.algorithm_id?.startsWith('aux_') ? '辅助事件（非标准/不交易）' : '策略状态与信号' }}</span>
          <small>{{ source.objects.length }}</small>
        </div>
        <div
          v-for="item in [...source.objects].sort((left, right) => right.bar_index - left.bar_index || right.object_revision - left.object_revision)"
          :key="item.object_id" class="signal-object-node" :class="{ selected: selectedSignalId === item.object_id }"
          data-object-type="StrategySemanticObject" :data-signal-id="item.object_id"
          role="button" tabindex="0" @click="emit('selectSignal', item)" @keydown.enter="emit('selectSignal', item)"
        >
          <span class="tree-elbow">└</span>
          <span class="signal-object-content">
            <strong class="semantic">{{ objectLabel(item) }}</strong>
            <small>{{ formatSignalTime(item.time) }} · {{ formatSignalPrice(item) }} · #{{ item.bar_index }}</small>
          </span>
          <button class="signal-object-lock" :class="{ active: lockedSignalId === item.object_id }" title="锁定并定位" @click.stop="emit('lockSignal', item)">
            {{ lockedSignalId === item.object_id ? '🔒' : '🔓' }}
          </button>
        </div>
      </div>
    </article>
    <article
      v-for="(drawing, index) in [...drawings].sort((a, b) => a.order_in_band - b.order_in_band)"
      :key="drawing.id"
      class="object-node drawing-node"
      :class="{ selected: selectedId === drawing.id }"
      data-object-type="DrawingObject"
      @click="emit('selectDrawing', drawing.id)"
    >
      <input :value="drawing.name" aria-label="绘图名称" @change="emit('patchDrawing', drawing.id, { name: ($event.target as HTMLInputElement).value })" />
      <div>
        <button :title="drawing.visible ? '隐藏' : '显示'" @click.stop="emit('patchDrawing', drawing.id, { visible: !drawing.visible })">{{ drawing.visible ? '◉' : '○' }}</button>
        <button :title="drawing.locked ? '解锁' : '锁定'" @click.stop="emit('patchDrawing', drawing.id, { locked: !drawing.locked })">{{ drawing.locked ? '🔒' : '🔓' }}</button>
        <button :disabled="index === 0" title="下移一层" @click.stop="emit('reorderDrawing', drawing.id, -1)">↓</button>
        <button :disabled="index === drawings.length - 1" title="上移一层" @click.stop="emit('reorderDrawing', drawing.id, 1)">↑</button>
        <button title="删除" @click.stop="emit('removeDrawing', drawing.id)">×</button>
      </div>
    </article>
  </section>
</template>
