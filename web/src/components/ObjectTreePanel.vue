<script setup lang="ts">
import { ref } from 'vue'
import { chanSignalLabel } from '../chart/chanPrimitive'
import type { DrawingObject } from '../drawing/model'
import type { ChanSignalPoint, DatasetMeta, SeriesSource, StrategySource } from '../types/api'

const props = defineProps<{
  dataset?: DatasetMeta | null
  drawings: DrawingObject[]
  sources: SeriesSource[]
  strategySources: StrategySource[]
  signalsBySource?: Record<string, ChanSignalPoint[]>
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
  selectSignal: [signal: ChanSignalPoint]
  lockSignal: [signal: ChanSignalPoint]
}>()

const collapsedStrategies = ref(new Set<string>())

function toggleStrategy(sourceId: string): void {
  const next = new Set(collapsedStrategies.value)
  if (next.has(sourceId)) next.delete(sourceId)
  else next.add(sourceId)
  collapsedStrategies.value = next
}

function signalsFor(sourceId: string): ChanSignalPoint[] {
  return [...(props.signalsBySource?.[sourceId] ?? [])].sort((left, right) =>
    right.bar_index - left.bar_index || right.known_at_bar_index - left.known_at_bar_index || right.object_id.localeCompare(left.object_id),
  )
}

function formatSignalTime(timestamp: number): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: props.dataset?.time?.timezone ?? 'Asia/Shanghai',
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(timestamp)).replaceAll('/', '-')
}

function formatSignalPrice(signal: ChanSignalPoint): string {
  const scale = props.dataset?.price?.price_scale ?? 1
  return (signal.price_i64 / scale).toFixed(props.dataset?.price?.price_decimals ?? 0)
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
      <div v-if="!collapsedStrategies.has(source.source_id)" class="strategy-children">
        <details class="strategy-categories">
          <summary>图层分类</summary>
          <label v-for="category in (['fractals', 'bi', 'segments', 'zhongshu', 'segment_zhongshu', 'divergences', 'trade_points'] as const)" :key="category">
            <input
              type="checkbox" :checked="source.category_visibility[category]"
              @change="emit('patchStrategy', source.source_id, { category_visibility: { ...source.category_visibility, [category]: !source.category_visibility[category] } })"
            />{{ category }}
          </label>
        </details>
        <div class="signal-branch-title"><span>└─ 信号对象</span><small>{{ signalsFor(source.source_id).length }}</small></div>
        <p v-if="signalsLoading" class="signal-tree-empty">正在读取信号…</p>
        <p v-else-if="signalsFor(source.source_id).length === 0" class="signal-tree-empty">暂无背驰或买卖点</p>
        <div
          v-for="signal in signalsFor(source.source_id)" :key="signal.object_id"
          class="signal-object-node" :class="{ selected: selectedSignalId === signal.object_id }"
          data-object-type="ChanSignalObject" :data-signal-id="signal.object_id"
          role="button" tabindex="0" @click="emit('selectSignal', signal)" @keydown.enter="emit('selectSignal', signal)"
        >
          <span class="tree-elbow">└</span>
          <span class="signal-object-content">
            <strong :class="signal.signal_type.includes('buy') || signal.signal_type === 'bottom_divergence' ? 'buy' : 'sell'">{{ chanSignalLabel(signal) }}</strong>
            <small>{{ formatSignalTime(signal.time) }} · {{ formatSignalPrice(signal) }} · #{{ signal.bar_index }}</small>
          </span>
          <button
            class="signal-object-lock" :class="{ active: lockedSignalId === signal.object_id }"
            :title="lockedSignalId === signal.object_id ? '取消锁定' : '锁定并定位'"
            @click.stop="emit('lockSignal', signal)"
          >{{ lockedSignalId === signal.object_id ? '🔒' : '🔓' }}</button>
        </div>
      </div>
    </article>
    <h3>用户绘图</h3>
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
