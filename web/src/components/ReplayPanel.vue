<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { createReplay, getReplay, getReplayEvents } from '../api/client'
import { ReplayEventIndex, type ReplayObjects, type ReplaySignal } from '../replay/eventIndex'
import type { DatasetMeta, ReplayRequest, StrategySource } from '../types/api'

const props = defineProps<{ dataset: DatasetMeta | null; source: StrategySource | null }>()
const emit = defineEmits<{ update: [value: { cursor: number | null; objects: ReplayObjects | null; signals: ReplaySignal[] }] }>()
const speeds = [.25, .5, 1, 2, 5, 10] as const
const from = ref(0)
const to = ref(0)
const cursor = ref(0)
const jump = ref(0)
const speed = ref<number>(1)
const status = ref('idle')
const error = ref('')
const eventCount = ref(0)
const playing = ref(false)
let timer = 0
let index: ReplayEventIndex | null = null

const ready = computed(() => status.value === 'completed' && index !== null)
const storageKey = computed(() => props.dataset ? `tvbt.replay.${props.dataset.dataset_id}.${props.dataset.data_revision}` : '')

function publish(next: number): void {
  if (!index) return
  cursor.value = Math.max(from.value, Math.min(to.value, next))
  jump.value = cursor.value
  emit('update', { cursor: cursor.value, objects: index.seek(cursor.value), signals: index.signals() })
  persist()
  if (cursor.value >= to.value) pause()
}

function persist(): void {
  if (!storageKey.value || !props.source || status.value !== 'completed') return
  localStorage.setItem(storageKey.value, JSON.stringify({
    algorithm_id: props.source.definition.algorithm_id,
    source_hash: props.source.definition.source_hash,
    from: from.value, to: to.value, cursor: cursor.value, speed: speed.value,
  }))
}

function pause(): void {
  playing.value = false
  window.clearInterval(timer)
  timer = 0
}

function play(): void {
  if (!ready.value || playing.value) return
  playing.value = true
  timer = window.setInterval(() => publish(cursor.value + 1), Math.max(25, 250 / speed.value))
}

async function submit(savedCursor?: number): Promise<void> {
  const dataset = props.dataset
  const source = props.source
  if (!dataset || !source) return
  pause()
  error.value = ''
  status.value = 'queued'
  index = null
  emit('update', { cursor: null, objects: null, signals: [] })
  const request: ReplayRequest = {
    dataset_id: dataset.dataset_id, data_revision: dataset.data_revision,
    strategy: {
      kind: source.definition.kind, algorithm_id: source.definition.algorithm_id,
      algorithm_version: source.definition.algorithm_version, source_hash: source.definition.source_hash,
    },
    parameters: source.parameters, from_bar_index: from.value, to_bar_index: to.value,
    warmup_from_bar_index: dataset.coverage.first_bar_index,
  }
  try {
    let replay = await createReplay(request)
    while (!['completed', 'failed', 'cancelled', 'interrupted'].includes(replay.status)) {
      await new Promise((resolve) => window.setTimeout(resolve, 250))
      replay = await getReplay(replay.replay_id)
      status.value = replay.status
    }
    if (replay.status !== 'completed') throw new Error(replay.error?.message ?? `回放${replay.status}`)
    const response = await getReplayEvents(replay.replay_id, dataset.coverage.first_bar_index, to.value)
    if (!/^sha256:[0-9a-f]{64}$/.test(response.checksum)) throw new Error('回放事件校验和无效')
    index = new ReplayEventIndex(response.events)
    eventCount.value = response.event_count
    status.value = 'completed'
    publish(savedCursor ?? from.value)
  } catch (cause) {
    status.value = 'failed'
    error.value = cause instanceof Error ? cause.message : '回放创建失败'
  }
}

function initialize(): void {
  pause()
  index = null
  emit('update', { cursor: null, objects: null, signals: [] })
  const dataset = props.dataset
  const source = props.source
  if (!dataset || !source) return
  from.value = Math.max(dataset.coverage.first_bar_index, dataset.coverage.last_bar_index - 2999)
  to.value = dataset.coverage.last_bar_index
  cursor.value = from.value
  jump.value = from.value
  const raw = storageKey.value ? localStorage.getItem(storageKey.value) : null
  if (!raw) return
  try {
    const saved = JSON.parse(raw) as { algorithm_id: string; source_hash: string; from: number; to: number; cursor: number; speed: number }
    if (saved.algorithm_id !== source.definition.algorithm_id || saved.source_hash !== source.definition.source_hash) return
    from.value = saved.from
    to.value = saved.to
    speed.value = speeds.includes(saved.speed as typeof speeds[number]) ? saved.speed : 1
    void submit(saved.cursor)
  } catch { localStorage.removeItem(storageKey.value) }
}

watch(() => [props.dataset?.data_revision, props.source?.source_id], initialize, { immediate: true })
watch(speed, () => { if (playing.value) { pause(); play() } persist() })
onBeforeUnmount(pause)
</script>

<template>
  <section class="replay-panel" aria-label="回放控制">
    <div class="replay-range">
      <label>开始 <input v-model.number="from" type="number" :min="dataset?.coverage.first_bar_index" :max="to" /></label>
      <label>结束 <input v-model.number="to" type="number" :min="from" :max="dataset?.coverage.last_bar_index" /></label>
      <button :disabled="!dataset || !source" @click="submit()">创建/复用事件</button>
    </div>
    <div class="replay-controls">
      <button :disabled="!ready || cursor <= from" @click="publish(cursor - 1)">后退</button>
      <button :disabled="!ready || cursor >= to" @click="publish(cursor + 1)">单步</button>
      <button :disabled="!ready" @click="playing ? pause() : play()">{{ playing ? '暂停' : '播放' }}</button>
      <select v-model.number="speed" aria-label="回放速度"><option v-for="value in speeds" :key="value" :value="value">{{ value }}x</option></select>
      <input v-model.number="jump" type="number" :min="from" :max="to" aria-label="跳转 K 线" />
      <button :disabled="!ready" @click="publish(jump)">跳转</button>
      <strong>{{ cursor }} / {{ to }}</strong>
      <span>{{ status }} · {{ eventCount }} 事件</span>
      <span v-if="error" class="issue">{{ error }}</span>
    </div>
  </section>
</template>
