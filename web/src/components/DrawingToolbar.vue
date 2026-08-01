<script setup lang="ts">
import type { DrawingType } from '../drawing/model'

defineProps<{ tool: DrawingType | 'cursor'; magnet: boolean; keepMode: boolean }>()
const emit = defineEmits<{
  'update:tool': [tool: DrawingType | 'cursor']
  'update:magnet': [enabled: boolean]
  'update:keepMode': [enabled: boolean]
  lockAll: []
  hideAll: []
  deleteSelected: []
  deleteAll: []
}>()

const tools: Array<{ id: DrawingType | 'cursor'; label: string; title: string }> = [
  { id: 'cursor', label: '╋', title: '选择/十字光标' },
  { id: 'trend_line', label: '／', title: '趋势线' },
  { id: 'horizontal_line', label: '─', title: '水平线' },
  { id: 'rectangle', label: '▭', title: '矩形' },
  { id: 'text', label: 'T', title: '文字' },
  { id: 'measure', label: '↗', title: '测量' },
]
</script>

<template>
  <aside class="left-toolbar drawing-toolbar" aria-label="绘图工具栏">
    <button v-for="item in tools" :key="item.id" :class="{ active: tool === item.id }" :title="item.title" @click="emit('update:tool', item.id)">{{ item.label }}</button>
    <span />
    <button :class="{ active: magnet }" title="磁吸" @click="emit('update:magnet', !magnet)">🧲</button>
    <button :class="{ active: keepMode }" title="保持绘图模式" @click="emit('update:keepMode', !keepMode)">∞</button>
    <button title="锁定全部" @click="emit('lockAll')">🔒</button>
    <button title="隐藏全部绘图" @click="emit('hideAll')">◉</button>
    <button title="删除选中" @click="emit('deleteSelected')">⌫</button>
    <button title="删除全部绘图" @click="emit('deleteAll')">×</button>
  </aside>
</template>
