<script setup lang="ts">
import type { DrawingObject } from '../drawing/model'
import type { SeriesSource, StrategySource } from '../types/api'

defineProps<{ drawings: DrawingObject[]; sources: SeriesSource[]; strategySources: StrategySource[]; selectedId: string | null }>()
const emit = defineEmits<{
  patchDrawing: [id: string, patch: Partial<DrawingObject>]
  removeDrawing: [id: string]
  reorderDrawing: [id: string, direction: -1 | 1]
  selectDrawing: [id: string]
  patchStrategy: [id: string, patch: Partial<StrategySource>]
  removeStrategy: [id: string]
}>()
</script>

<template>
  <section class="object-tree">
    <h3>SeriesSource</h3>
    <div v-for="source in sources" :key="source.source_id" class="object-node" data-object-type="SeriesSource">
      {{ source.definition.name }} · {{ source.status }}
    </div>
    <h3>StrategySource</h3>
    <article v-for="source in strategySources" :key="source.source_id" class="object-node strategy-node" data-object-type="StrategySource">
      <header>
        <strong>{{ source.definition.name }}</strong>
        <button @click="emit('patchStrategy', source.source_id, { visible: !source.visible })">{{ source.visible ? '◉' : '○' }}</button>
        <button @click="emit('removeStrategy', source.source_id)">×</button>
      </header>
      <label v-for="category in (['fractals', 'bi', 'segments', 'zhongshu'] as const)" :key="category">
        <input
          type="checkbox" :checked="source.category_visibility[category]"
          @change="emit('patchStrategy', source.source_id, { category_visibility: { ...source.category_visibility, [category]: !source.category_visibility[category] } })"
        />{{ category }}
      </label>
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
