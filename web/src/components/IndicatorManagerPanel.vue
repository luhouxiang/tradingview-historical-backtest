<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { createCalculation, getCalculation, listAlgorithms } from '../api/client'
import {
  indicatorCategory, indicatorCategoryLabels, indicatorLocation, matchesIndicator, parameterSummary,
  type IndicatorCategory,
} from '../indicators/catalog'
import type { AlgorithmDefinition, DatasetMeta, SeriesSource, StrategySource } from '../types/api'

type ManagerTab = 'common' | 'all' | 'current'
type ActiveEntry =
  | { sourceType: 'indicator'; source: SeriesSource }
  | { sourceType: 'chan'; source: StrategySource }

const props = defineProps<{
  dataset: DatasetMeta | null
  indicatorSources: SeriesSource[]
  strategySources: StrategySource[]
}>()
const emit = defineEmits<{
  'update:indicator-sources': [sources: SeriesSource[]]
  'update:strategy-sources': [sources: StrategySource[]]
}>()

const favoriteStorageKey = 'tvbt.indicator-favorites.v1'
const recommendedIds = ['ma', 'macd', 'atr', 'chan_engineering']
const algorithms = ref<AlgorithmDefinition[]>([])
const activeTab = ref<ManagerTab>('common')
const query = ref('')
const selectedCategory = ref<'all' | IndicatorCategory>('all')
const favoriteIds = ref<string[]>([])
const status = ref('')
const busyAlgorithmId = ref('')

function readFavorites(): string[] {
  try {
    const stored = localStorage.getItem(favoriteStorageKey)
    if (stored !== null) {
      const parsed = JSON.parse(stored)
      if (Array.isArray(parsed)) return parsed.filter((value): value is string => typeof value === 'string')
    }
  } catch { /* Ignore unavailable or malformed browser storage. */ }
  return [...recommendedIds]
}

function saveFavorites(): void {
  try { localStorage.setItem(favoriteStorageKey, JSON.stringify(favoriteIds.value)) }
  catch { /* Favorites are non-critical UI preferences. */ }
}

function toggleFavorite(algorithmId: string): void {
  favoriteIds.value = favoriteIds.value.includes(algorithmId)
    ? favoriteIds.value.filter((value) => value !== algorithmId)
    : [...favoriteIds.value, algorithmId]
  saveFavorites()
}

const categories = computed(() => {
  const counts = new Map<IndicatorCategory, number>()
  for (const definition of algorithms.value) {
    const category = indicatorCategory(definition)
    counts.set(category, (counts.get(category) ?? 0) + 1)
  }
  return [...counts.entries()].map(([id, count]) => ({ id, label: indicatorCategoryLabels[id], count }))
})

const visibleCatalog = computed(() => algorithms.value.filter((definition) => {
  if (activeTab.value === 'common' && !favoriteIds.value.includes(definition.algorithm_id)) return false
  if (activeTab.value === 'all' && selectedCategory.value !== 'all' && indicatorCategory(definition) !== selectedCategory.value) return false
  return matchesIndicator(definition, query.value)
}))

const activeEntries = computed<ActiveEntry[]>(() => [
  ...props.indicatorSources.map((source): ActiveEntry => ({ sourceType: 'indicator', source })),
  ...props.strategySources.map((source): ActiveEntry => ({ sourceType: 'chan', source })),
])
const favoriteCount = computed(() => algorithms.value.filter((definition) => favoriteIds.value.includes(definition.algorithm_id)).length)

const visibleActiveEntries = computed(() => activeEntries.value.filter(({ source }) => matchesIndicator(source.definition, query.value)))

function defaults(definition: AlgorithmDefinition): Record<string, string | number | boolean> {
  return Object.fromEntries(Object.entries(definition.parameter_schema.properties).map(([name, rule]) => [name, rule.default ?? '']))
}

async function waitForSeries(sourceId: string, jobId: string): Promise<void> {
  for (;;) {
    const job = await getCalculation(jobId)
    emit('update:indicator-sources', props.indicatorSources.map((source) => source.source_id === sourceId
      ? { ...source, status: job.status, error: job.error?.message }
      : source))
    if (job.status === 'completed' || ['failed', 'cancelled', 'interrupted'].includes(job.status)) return
    await new Promise((resolve) => window.setTimeout(resolve, 250))
  }
}

async function waitForStrategy(sourceId: string, jobId: string): Promise<void> {
  for (;;) {
    const job = await getCalculation(jobId)
    emit('update:strategy-sources', props.strategySources.map((source) => source.source_id === sourceId
      ? { ...source, status: job.status, error: job.error?.message }
      : source))
    if (job.status === 'completed' || ['failed', 'cancelled', 'interrupted'].includes(job.status)) return
    await new Promise((resolve) => window.setTimeout(resolve, 250))
  }
}

async function submit(definition: AlgorithmDefinition, parameters: Record<string, string | number | boolean>, sourceId?: string): Promise<void> {
  if (!props.dataset) return
  busyAlgorithmId.value = definition.algorithm_id
  status.value = ''
  try {
    const accepted = await createCalculation({
      dataset_id: props.dataset.dataset_id,
      data_revision: props.dataset.data_revision,
      algorithm: {
        kind: definition.kind, algorithm_id: definition.algorithm_id,
        algorithm_version: definition.algorithm_version, source_hash: definition.source_hash,
      },
      parameters,
      calculation_mode: definition.kind === 'chan' ? 'causal_events' : 'full_history',
    })
    if (definition.kind === 'chan') {
      const id = sourceId ?? `strategy-${crypto.randomUUID()}`
      const existing = props.strategySources.find((source) => source.source_id === id)
      const source: StrategySource = {
        source_type: 'StrategySource', source_id: id, definition, parameters,
        job_id: accepted.job_id, status: accepted.status, visible: existing?.visible ?? true,
        category_visibility: existing?.category_visibility ?? { fractals: false, bi: true, zhongshu: true },
      }
      emit('update:strategy-sources', existing
        ? props.strategySources.map((item) => item.source_id === id ? source : item)
        : [...props.strategySources, source])
      if (accepted.status !== 'completed') void waitForStrategy(id, accepted.job_id)
    } else {
      const id = sourceId ?? `series-${crypto.randomUUID()}`
      const source: SeriesSource = {
        source_type: 'SeriesSource', source_id: id, definition, parameters,
        job_id: accepted.job_id, status: accepted.status,
      }
      emit('update:indicator-sources', props.indicatorSources.some((item) => item.source_id === id)
        ? props.indicatorSources.map((item) => item.source_id === id ? source : item)
        : [...props.indicatorSources, source])
      if (accepted.status !== 'completed') void waitForSeries(id, accepted.job_id)
    }
  } catch (error) {
    status.value = error instanceof Error ? error.message : '指标创建失败'
  } finally {
    busyAlgorithmId.value = ''
  }
}

function updateParameter(entry: ActiveEntry, name: string, value: string | boolean): void {
  const rule = entry.source.definition.parameter_schema.properties[name]
  const parsed = typeof value === 'boolean' ? value : rule.type === 'integer' || rule.type === 'number' ? Number(value) : value
  const patch = { ...entry.source, parameters: { ...entry.source.parameters, [name]: parsed } }
  if (entry.sourceType === 'chan') {
    emit('update:strategy-sources', props.strategySources.map((source) => source.source_id === entry.source.source_id ? patch as StrategySource : source))
  } else {
    emit('update:indicator-sources', props.indicatorSources.map((source) => source.source_id === entry.source.source_id ? patch as SeriesSource : source))
  }
}

function remove(entry: ActiveEntry): void {
  if (entry.sourceType === 'chan') emit('update:strategy-sources', props.strategySources.filter((source) => source.source_id !== entry.source.source_id))
  else emit('update:indicator-sources', props.indicatorSources.filter((source) => source.source_id !== entry.source.source_id))
}

function useTab(tab: ManagerTab): void {
  activeTab.value = tab
  if (tab !== 'all') selectedCategory.value = 'all'
}

onMounted(async () => {
  favoriteIds.value = readFavorites()
  try { algorithms.value = (await listAlgorithms()).filter((definition) => definition.kind === 'indicator' || definition.kind === 'chan') }
  catch (error) { status.value = error instanceof Error ? error.message : '算法列表不可用' }
})
</script>

<template>
  <section class="indicator-manager" aria-label="指标管理">
    <header class="indicator-manager-header">
      <div><strong>指标管理</strong><small>{{ algorithms.length }} 个指标 · 已使用 {{ activeEntries.length }}</small></div>
      <input v-model="query" type="search" aria-label="搜索指标" placeholder="搜索名称、代码或分类" />
    </header>

    <nav class="indicator-manager-tabs" aria-label="指标范围">
      <button :class="{ active: activeTab === 'common' }" @click="useTab('common')">常用 <span>{{ favoriteCount }}</span></button>
      <button :class="{ active: activeTab === 'all' }" @click="useTab('all')">全部 <span>{{ algorithms.length }}</span></button>
      <button :class="{ active: activeTab === 'current' }" @click="useTab('current')">当前使用 <span>{{ activeEntries.length }}</span></button>
    </nav>

    <small v-if="status" class="issue indicator-manager-status">{{ status }}</small>

    <template v-if="activeTab !== 'current'">
      <div v-if="activeTab === 'all'" class="indicator-categories" aria-label="指标分类">
        <button :class="{ active: selectedCategory === 'all' }" @click="selectedCategory = 'all'">全部</button>
        <button v-for="category in categories" :key="category.id" :class="{ active: selectedCategory === category.id }" @click="selectedCategory = category.id">
          {{ category.label }} {{ category.count }}
        </button>
      </div>
      <div class="indicator-catalog" aria-live="polite">
        <article v-for="definition in visibleCatalog" :key="`${definition.kind}:${definition.algorithm_id}`" class="indicator-catalog-row">
          <button class="indicator-favorite" :class="{ selected: favoriteIds.includes(definition.algorithm_id) }" :aria-label="`${favoriteIds.includes(definition.algorithm_id) ? '取消收藏' : '收藏'} ${definition.name}`" @click="toggleFavorite(definition.algorithm_id)">★</button>
          <div class="indicator-catalog-copy">
            <strong>{{ definition.name }}</strong>
            <small>{{ definition.algorithm_id }} · {{ indicatorCategoryLabels[indicatorCategory(definition)] }} · {{ indicatorLocation(definition) }}</small>
          </div>
          <button class="indicator-add-button" :disabled="!dataset || busyAlgorithmId === definition.algorithm_id" :aria-label="`添加 ${definition.name}`" @click="submit(definition, defaults(definition))">
            {{ busyAlgorithmId === definition.algorithm_id ? '添加中' : '＋' }}
          </button>
        </article>
        <div v-if="visibleCatalog.length === 0" class="indicator-empty">{{ query ? '没有匹配的指标' : '暂无常用指标，可在“全部”中点击星标收藏' }}</div>
      </div>
    </template>

    <div v-else class="indicator-current-list">
      <details v-for="entry in visibleActiveEntries" :key="entry.source.source_id" class="indicator-current-card">
        <summary>
          <span class="indicator-kind-mark" :class="entry.sourceType">{{ entry.sourceType === 'chan' ? '缠' : '指' }}</span>
          <span class="indicator-current-copy"><strong>{{ entry.source.definition.name }}</strong><small>{{ parameterSummary(entry.source.parameters) }} · {{ indicatorLocation(entry.source.definition) }}</small></span>
          <span class="indicator-source-status" :class="entry.source.status">{{ entry.source.status }}</span>
        </summary>
        <div class="indicator-parameters">
          <label v-for="(rule, name) in entry.source.definition.parameter_schema.properties" :key="name">
            <span>{{ name }}</span>
            <select v-if="rule.enum" :value="entry.source.parameters[name]" @change="updateParameter(entry, name, ($event.target as HTMLSelectElement).value)">
              <option v-for="value in rule.enum" :key="value">{{ value }}</option>
            </select>
            <input v-else-if="rule.type === 'boolean'" type="checkbox" :checked="Boolean(entry.source.parameters[name])" @change="updateParameter(entry, name, ($event.target as HTMLInputElement).checked)" />
            <input v-else :type="rule.type === 'string' ? 'text' : 'number'" :min="rule.minimum" :max="rule.maximum" :value="entry.source.parameters[name]" @input="updateParameter(entry, name, ($event.target as HTMLInputElement).value)" />
          </label>
        </div>
        <div class="indicator-current-actions">
          <button @click="submit(entry.source.definition, entry.source.parameters, entry.source.source_id)">应用参数</button>
          <button class="danger" @click="remove(entry)">删除</button>
        </div>
        <small v-if="entry.source.error" class="issue">{{ entry.source.error }}</small>
      </details>
      <div v-if="visibleActiveEntries.length === 0" class="indicator-empty">{{ query ? '当前指标中没有匹配项' : '当前没有使用指标' }}</div>
    </div>
  </section>
</template>
