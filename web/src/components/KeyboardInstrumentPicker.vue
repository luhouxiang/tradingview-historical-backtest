<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getDataset, getJob, getSourceFiles, importSource, listDatasets, startDatasetScan } from '../api/client'
import { fuzzyInstruments, type InstrumentSearchItem } from '../instruments/fuzzy'
import type { DatasetMeta, DatasetSummary, SourceFile } from '../types/api'

interface Candidate extends InstrumentSearchItem {
  exchange: string
  source?: SourceFile
  dataset?: DatasetSummary
}

const emit = defineEmits<{ selected: [dataset: DatasetMeta] }>()

const open = ref(false)
const query = ref('')
const candidates = ref<Candidate[]>([])
const selectedIndex = ref(0)
const busy = ref(false)
const status = ref('')
const input = ref<HTMLInputElement | null>(null)
let scanPromise: Promise<void> | null = null

const matches = computed(() => fuzzyInstruments(candidates.value, query.value))

function detectedString(source: SourceFile, key: string): string {
  const value = source.detected?.[key]
  return typeof value === 'string' ? value : ''
}

function matchingDataset(source: SourceFile, datasets: DatasetSummary[]): DatasetSummary | undefined {
  const symbol = detectedString(source, 'symbol')
  const timeframe = detectedString(source, 'timeframe')
  const exchange = detectedString(source, 'exchange')
  return datasets.find((dataset) => dataset.instrument === symbol
    && dataset.timeframe === timeframe
    && (!exchange || dataset.dataset_id.startsWith(`${exchange}.`)))
}

function buildCandidates(sources: SourceFile[], datasets: DatasetSummary[]): Candidate[] {
  const items: Candidate[] = sources.map((source) => {
    const dataset = matchingDataset(source, datasets)
    return {
      id: source.source_file_id,
      symbol: detectedString(source, 'symbol') || source.path.replace(/^.*[\\/]/, '').replace(/\.txt$/i, ''),
      timeframe: detectedString(source, 'timeframe') || dataset?.timeframe || '',
      exchange: detectedString(source, 'exchange'),
      path: source.path,
      label: detectedString(source, 'display_name') || detectedString(source, 'title'),
      status: dataset?.status === 'ready' ? 'ready' : source.status,
      source,
      dataset,
    }
  })
  const represented = new Set(items.flatMap((item) => item.dataset ? [item.dataset.dataset_id] : []))
  for (const dataset of datasets) {
    if (represented.has(dataset.dataset_id)) continue
    items.push({
      id: `dataset:${dataset.dataset_id}`,
      symbol: dataset.instrument,
      timeframe: dataset.timeframe,
      exchange: dataset.dataset_id.split('.')[0] ?? '',
      path: dataset.dataset_id,
      status: dataset.status,
      dataset,
    })
  }
  return items
}

async function refreshCandidates(): Promise<void> {
  const [sources, catalog] = await Promise.all([getSourceFiles(), listDatasets()])
  candidates.value = buildCandidates(sources, catalog.datasets)
}

async function waitForJob(jobId: string): Promise<void> {
  for (;;) {
    const job = await getJob(jobId)
    if (job.status === 'completed') return
    if (['failed', 'cancelled', 'interrupted'].includes(job.status)) {
      throw new Error(job.error?.message ?? `任务${job.status}`)
    }
    await new Promise((resolve) => window.setTimeout(resolve, 250))
  }
}

function ensureScanned(): Promise<void> {
  if (scanPromise) return scanPromise
  scanPromise = (async () => {
    status.value = '正在扫描 history…'
    const accepted = await startDatasetScan()
    await waitForJob(accepted.job_id)
    await refreshCandidates()
    status.value = ''
  })().catch((error) => {
    status.value = error instanceof Error ? error.message : '扫描失败'
  })
  return scanPromise
}

function close(): void {
  open.value = false
  query.value = ''
  status.value = ''
}

async function show(initial: string): Promise<void> {
  open.value = true
  query.value = initial
  selectedIndex.value = 0
  await nextTick()
  input.value?.focus()
  void ensureScanned()
}

function moveSelection(delta: number): void {
  if (matches.value.length === 0) return
  selectedIndex.value = (selectedIndex.value + delta + matches.value.length) % matches.value.length
}

async function choose(candidate = matches.value[selectedIndex.value]): Promise<void> {
  if (!candidate || busy.value) return
  busy.value = true
  status.value = `正在加载 ${candidate.symbol}…`
  try {
    let dataset = candidate.dataset
    if (!dataset && candidate.source?.status === 'importable') {
      const accepted = await importSource(candidate.source)
      await waitForJob(accepted.job_id)
      await refreshCandidates()
      dataset = candidates.value.find((item) => item.id === candidate.id)?.dataset
    }
    if (!dataset || dataset.status !== 'ready') throw new Error(`${candidate.symbol} 暂无可加载数据集`)
    const metadata = await getDataset(dataset.dataset_id, dataset.active_revision)
    emit('selected', metadata)
    close()
  } catch (error) {
    status.value = error instanceof Error ? error.message : '加载失败'
  } finally {
    busy.value = false
  }
}

function inputKeydown(event: KeyboardEvent): void {
  if (event.key === 'ArrowDown') { event.preventDefault(); moveSelection(1) }
  else if (event.key === 'ArrowUp') { event.preventDefault(); moveSelection(-1) }
  else if (event.key === 'Enter') { event.preventDefault(); void choose() }
  else if (event.key === 'Escape') { event.preventDefault(); close() }
}

function isEditableTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement
    && (['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable)
}

function globalKeydown(event: KeyboardEvent): void {
  if (isEditableTarget(event.target) || event.ctrlKey || event.metaKey || event.altKey) return
  if (!open.value && event.key.length === 1 && !/\s/.test(event.key)) {
    event.preventDefault()
    void show(event.key)
  } else if (open.value && event.key === 'Escape') {
    event.preventDefault()
    close()
  }
}

watch([query, matches], () => { selectedIndex.value = 0 })

onMounted(() => {
  window.addEventListener('keydown', globalKeydown)
  void refreshCandidates().catch(() => undefined)
})
onBeforeUnmount(() => window.removeEventListener('keydown', globalKeydown))
</script>

<template>
  <aside v-if="open" class="keyboard-picker" aria-label="键盘精灵">
    <header><strong>键盘精灵</strong><button aria-label="关闭键盘精灵" @click="close">×</button></header>
    <input ref="input" v-model="query" aria-label="标的搜索" autocomplete="off" @keydown="inputKeydown">
    <div class="keyboard-picker-results" role="listbox" aria-label="匹配标的">
      <button
        v-for="(candidate, index) in matches"
        :key="candidate.id"
        :class="{ selected: index === selectedIndex }"
        :aria-selected="index === selectedIndex"
        role="option"
        :disabled="busy"
        @mouseenter="selectedIndex = index"
        @click="choose(candidate)"
      >
        <strong>{{ candidate.symbol }}</strong>
        <span>{{ candidate.label || candidate.path.replace(/^.*[\\/]/, '') }}</span>
        <small>{{ candidate.timeframe }} · {{ candidate.status }}</small>
      </button>
      <div v-if="matches.length === 0 && !status" class="keyboard-picker-empty">无匹配标的</div>
    </div>
    <footer>{{ status || '↑↓ 选择　Enter 加载　Esc 关闭' }}</footer>
  </aside>
</template>
