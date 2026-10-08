<script setup lang="ts">
import { speedLabel } from '../replay/simulationSpeed'
defineProps<{ speed: number; ready: boolean; preparing?: boolean; pendingPlay?: boolean; status?: string; playing: boolean; cursor: number; to: number }>()
const emit = defineEmits<{ speed: [value: number]; play: []; step: []; exit: [] }>()
</script>
<template>
  <div class="simulation-speed-bar" aria-label="K线模拟速度控制">
    <button :disabled="(!ready && !preparing) || cursor >= to" @click="emit('play')">{{ preparing ? pendingPlay ? '取消待播放' : '准备完成后播放' : playing ? '暂停模拟' : '播放模拟' }}</button>
    <button :disabled="!ready || cursor >= to" @click="emit('step')">单步</button>
    <span>慢：60秒/根</span>
    <input type="range" min="0" max="100" step="1" :value="speed" aria-label="模拟回放速度"
      @input="emit('speed', Number(($event.target as HTMLInputElement).value))" />
    <span>快：100根/秒</span><strong>{{ speedLabel(speed) }}</strong>
    <span>K{{ cursor }} / {{ to }}</span>
    <span v-if="preparing" role="status">{{ pendingPlay ? '已请求播放，准备完成后开始；' : '' }}{{ status }}</span>
    <button @click="emit('exit')">退出模拟</button>
  </div>
</template>
