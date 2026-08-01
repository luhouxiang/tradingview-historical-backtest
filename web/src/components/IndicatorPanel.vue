<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { createCalculation, getCalculation, listAlgorithms } from '../api/client'
import type { AlgorithmDefinition, DatasetMeta, SeriesSource } from '../types/api'

const props = defineProps<{ dataset: DatasetMeta | null; sources: SeriesSource[] }>()
const emit = defineEmits<{ 'update:sources': [sources: SeriesSource[]] }>()
const algorithms = ref<AlgorithmDefinition[]>([])
const selectedId = ref('ma')
const status = ref('')

function defaults(definition: AlgorithmDefinition): Record<string, string | number | boolean> {
  return Object.fromEntries(Object.entries(definition.parameter_schema.properties).map(([name, rule]) => [name, rule.default ?? '']))
}

async function waitFor(source: SeriesSource): Promise<void> {
  for (;;) {
    const job = await getCalculation(source.job_id)
    emit('update:sources', props.sources.map((item) => item.source_id === source.source_id
      ? { ...item, status: job.status, error: job.error?.message }
      : item))
    if (job.status === 'completed' || ['failed', 'cancelled', 'interrupted'].includes(job.status)) return
    await new Promise((resolve) => window.setTimeout(resolve, 250))
  }
}

async function submit(definition: AlgorithmDefinition, parameters: Record<string, string | number | boolean>, sourceId = `series-${crypto.randomUUID()}`): Promise<void> {
  if (!props.dataset) return
  const accepted = await createCalculation({
    dataset_id: props.dataset.dataset_id,
    data_revision: props.dataset.data_revision,
    algorithm: {
      kind: definition.kind, algorithm_id: definition.algorithm_id,
      algorithm_version: definition.algorithm_version, source_hash: definition.source_hash,
    },
    parameters,
    calculation_mode: 'full_history',
  })
  const source: SeriesSource = {
    source_type: 'SeriesSource', source_id: sourceId, definition, parameters,
    job_id: accepted.job_id, status: accepted.status,
  }
  emit('update:sources', props.sources.some((item) => item.source_id === sourceId)
    ? props.sources.map((item) => item.source_id === sourceId ? source : item)
    : [...props.sources, source])
  if (accepted.status !== 'completed') void waitFor(source)
}

async function add(): Promise<void> {
  const definition = algorithms.value.find((item) => item.algorithm_id === selectedId.value)
  if (!definition || !props.dataset) return
  status.value = ''
  try { await submit(definition, defaults(definition)) }
  catch (error) { status.value = error instanceof Error ? error.message : '指标创建失败' }
}

function updateParameter(source: SeriesSource, name: string, value: string): void {
  const rule = source.definition.parameter_schema.properties[name]
  const parsed = rule.type === 'integer' || rule.type === 'number' ? Number(value) : value
  emit('update:sources', props.sources.map((item) => item.source_id === source.source_id
    ? { ...item, parameters: { ...item.parameters, [name]: parsed } }
    : item))
}

async function apply(source: SeriesSource): Promise<void> { await submit(source.definition, source.parameters, source.source_id) }
function remove(sourceId: string): void { emit('update:sources', props.sources.filter((item) => item.source_id !== sourceId)) }

onMounted(async () => {
  try {
    algorithms.value = (await listAlgorithms()).filter((item) => item.kind === 'indicator')
    selectedId.value = algorithms.value[0]?.algorithm_id ?? ''
  } catch (error) { status.value = error instanceof Error ? error.message : '算法列表不可用' }
})
</script>

<template>
  <section class="indicator-panel">
    <div class="indicator-add">
      <select v-model="selectedId" aria-label="指标算法">
        <option v-for="algorithm in algorithms" :key="algorithm.algorithm_id" :value="algorithm.algorithm_id">{{ algorithm.name }}</option>
      </select>
      <button :disabled="!dataset || !selectedId" @click="add">添加指标</button>
    </div>
    <small v-if="status" class="issue">{{ status }}</small>
    <article v-for="source in sources" :key="source.source_id" class="indicator-card" :data-source-type="source.source_type">
      <header><strong>{{ source.definition.name }}</strong><span>{{ source.status }}</span></header>
      <label v-for="(rule, name) in source.definition.parameter_schema.properties" :key="name">
        <span>{{ name }}</span>
        <select v-if="rule.enum" :value="source.parameters[name]" @change="updateParameter(source, name, ($event.target as HTMLSelectElement).value)">
          <option v-for="value in rule.enum" :key="value">{{ value }}</option>
        </select>
        <input v-else :type="rule.type === 'string' ? 'text' : 'number'" :min="rule.minimum" :max="rule.maximum" :value="source.parameters[name]" @input="updateParameter(source, name, ($event.target as HTMLInputElement).value)" />
      </label>
      <div><button @click="apply(source)">应用</button><button @click="remove(source.source_id)">删除</button></div>
      <small v-if="source.error" class="issue">{{ source.error }}</small>
    </article>
  </section>
</template>
