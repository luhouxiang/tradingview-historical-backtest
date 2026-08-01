<script setup lang="ts">
defineProps<{ health: string }>()
const emit = defineEmits<{ save: []; undo: []; redo: []; replay: [] }>()
</script>

<template>
  <header class="top-toolbar">
    <div class="brand">TVBT</div>
    <button disabled>合约</button>
    <button disabled>周期</button>
    <button disabled>图表类型</button>
    <span class="toolbar-spacer" />
    <button disabled>指标</button>
    <button disabled>策略</button>
    <button @click="emit('replay')">回放</button>
    <button disabled>布局</button>
    <button title="撤销" @click="emit('undo')">↶</button>
    <button title="重做" @click="emit('redo')">↷</button>
    <button title="保存工作区" @click="emit('save')">保存</button>
    <span class="health-dot" :class="`health-${health}`" />
    <span class="health-label">{{ health }}</span>
  </header>
</template>
