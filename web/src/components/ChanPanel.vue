<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { createCalculation, getCalculation, listAlgorithms } from '../api/client'
import type { AlgorithmDefinition, DatasetMeta, StrategySource } from '../types/api'

const props = defineProps<{ dataset: DatasetMeta | null; sources: StrategySource[] }>()
const emit = defineEmits<{ 'update:sources': [sources: StrategySource[]] }>()
const algorithms = ref<AlgorithmDefinition[]>([])
const selectedId = ref('')
const status = ref('')

function defaults(definition: AlgorithmDefinition): Record<string, string | number | boolean> {
  return Object.fromEntries(Object.entries(definition.parameter_schema.properties).map(([name, rule]) => [name, rule.default ?? '']))
}

async function waitFor(source: StrategySource): Promise<void> {
  for (;;) {
    const job = await getCalculation(source.job_id)
    emit('update:sources', props.sources.map((item) => item.source_id === source.source_id
      ? { ...item, status: job.status, error: job.error?.message }
      : item))
    if (job.status === 'completed' || ['failed', 'cancelled', 'interrupted'].includes(job.status)) return
    await new Promise((resolve) => window.setTimeout(resolve, 250))
  }
}

async function submit(definition: AlgorithmDefinition, parameters: Record<string, string | number | boolean>, sourceId = `strategy-${crypto.randomUUID()}`): Promise<void> {
  if (!props.dataset || definition.kind !== 'chan') return
  const accepted = await createCalculation({
    dataset_id: props.dataset.dataset_id, data_revision: props.dataset.data_revision,
    algorithm: {
      kind: definition.kind, algorithm_id: definition.algorithm_id,
      algorithm_version: definition.algorithm_version, source_hash: definition.source_hash,
    },
    parameters, calculation_mode: 'causal_events',
  })
  const existing = props.sources.find((item) => item.source_id === sourceId)
  const source: StrategySource = {
    source_type: 'StrategySource', source_id: sourceId, definition, parameters,
    job_id: accepted.job_id, status: accepted.status, visible: existing?.visible ?? true,
    category_visibility: existing?.category_visibility ?? { fractals: true, bi: true, zhongshu: true },
  }
  emit('update:sources', existing
    ? props.sources.map((item) => item.source_id === sourceId ? source : item)
    : [...props.sources, source])
  if (accepted.status !== 'completed') void waitFor(source)
}

async function add(): Promise<void> {
  const definition = algorithms.value.find((item) => item.algorithm_id === selectedId.value)
  if (!definition) return
  status.value = ''
  try { await submit(definition, defaults(definition)) }
  catch (error) { status.value = error instanceof Error ? error.message : '缠论创建失败' }
}

function updateParameter(source: StrategySource, name: string, value: string): void {
  const rule = source.definition.parameter_schema.properties[name]
  const parsed = rule.type === 'integer' || rule.type === 'number' ? Number(value) : value
  emit('update:sources', props.sources.map((item) => item.source_id === source.source_id
    ? { ...item, parameters: { ...item.parameters, [name]: parsed } }
    : item))
}

function remove(sourceId: string): void {
  emit('update:sources', props.sources.filter((item) => item.source_id !== sourceId))
}

onMounted(async () => {
  try {
    algorithms.value = (await listAlgorithms()).filter((item) => item.kind === 'chan')
    selectedId.value = algorithms.value[0]?.algorithm_id ?? ''
  } catch (error) { status.value = error instanceof Error ? error.message : '算法列表不可用' }
})
</script>

<template>
  <section class="indicator-panel" aria-label="缠论参数">
    <div class="indicator-add">
      <select v-model="selectedId" aria-label="缠论算法">
        <option v-for="algorithm in algorithms" :key="algorithm.algorithm_id" :value="algorithm.algorithm_id">{{ algorithm.name }}</option>
      </select>
      <button :disabled="!dataset || !selectedId" @click="add">添加缠论</button>
    </div>
    <small v-if="status" class="issue">{{ status }}</small>
    <article v-for="source in sources" :key="source.source_id" class="indicator-card" data-source-type="StrategySource">
      <header><strong>{{ source.definition.name }}</strong><span>{{ source.status }}</span></header>
      <label v-for="(rule, name) in source.definition.parameter_schema.properties" :key="name">
        <span>{{ name }}</span>
        <input :type="rule.type === 'string' ? 'text' : 'number'" :min="rule.minimum" :max="rule.maximum" :value="source.parameters[name]" @input="updateParameter(source, name, ($event.target as HTMLInputElement).value)" />
      </label>
      <div><button @click="submit(source.definition, source.parameters, source.source_id)">应用</button><button @click="remove(source.source_id)">删除</button></div>
      <small v-if="source.error" class="issue">{{ source.error }}</small>
    </article>
  </section>
</template>
