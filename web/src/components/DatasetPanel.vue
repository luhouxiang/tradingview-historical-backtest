<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  getDataset,
  getJob,
  getSourceFiles,
  importSource,
  listDatasets,
  startDatasetScan,
} from '../api/client'
import { logger } from '../logging/logger'
import type { DatasetMeta, DatasetSummary, SourceFile } from '../types/api'

const emit = defineEmits<{ selected: [dataset: DatasetMeta] }>()

const sources = ref<SourceFile[]>([])
const datasets = ref<DatasetSummary[]>([])
const selected = ref<DatasetMeta | null>(null)
const busy = ref(false)
const status = ref('')

async function waitForJob(jobId: string): Promise<void> {
  for (;;) {
    const job = await getJob(jobId)
    status.value = `${job.status} ${Math.round(job.progress * 100)}%`
    if (job.status === 'completed') return
    if (['failed', 'cancelled', 'interrupted'].includes(job.status)) {
      throw new Error(job.error?.message ?? `任务${job.status}`)
    }
    await new Promise((resolve) => window.setTimeout(resolve, 250))
  }
}

async function refresh(): Promise<void> {
  const [sourceItems, catalog] = await Promise.all([getSourceFiles(), listDatasets()])
  sources.value = sourceItems
  datasets.value = catalog.datasets
}

async function scan(): Promise<void> {
  busy.value = true
  try {
    logger.info('dataset.scan.requested', 'History scan requested')
    const accepted = await startDatasetScan()
    await waitForJob(accepted.job_id)
    await refresh()
    status.value = `发现 ${sources.value.length} 个源文件`
  } catch (error) {
    status.value = error instanceof Error ? error.message : '扫描失败'
    logger.error('ui.error', 'Dataset scan failed', { reason: status.value })
  } finally {
    busy.value = false
  }
}

async function runImport(source: SourceFile): Promise<void> {
  busy.value = true
  try {
    const accepted = await importSource(source)
    await waitForJob(accepted.job_id)
    await refresh()
    status.value = '导入完成'
  } catch (error) {
    status.value = error instanceof Error ? error.message : '导入失败'
    logger.error('ui.error', 'Dataset import failed', { reason: status.value })
  } finally {
    busy.value = false
  }
}

async function selectDataset(item: DatasetSummary): Promise<void> {
  selected.value = await getDataset(item.dataset_id, item.active_revision)
  emit('selected', selected.value)
}

onMounted(async () => {
  try {
    await refresh()
    if (datasets.value.length > 0) await selectDataset(datasets.value[0]!)
  } catch {
    // An empty catalog before the Go service starts is a recoverable shell state.
  }
})
</script>

<template>
  <section class="dataset-panel">
    <div class="dataset-actions">
      <button :disabled="busy" @click="scan">扫描 history</button>
      <small>{{ status }}</small>
    </div>
    <h3>待导入源文件</h3>
    <div v-if="sources.length === 0" class="empty-panel">尚未扫描</div>
    <article v-for="source in sources" :key="source.source_file_id" class="dataset-card">
      <strong>{{ source.detected?.symbol ?? source.path }}</strong>
      <span>{{ source.detected?.timeframe }} · {{ source.status }}</span>
      <button v-if="source.status === 'importable'" :disabled="busy" @click="runImport(source)">导入</button>
      <small v-for="issue in source.issues" :key="issue.code" class="issue">{{ issue.code }}</small>
    </article>
    <h3>数据集</h3>
    <div v-if="datasets.length === 0" class="empty-panel">暂无数据集</div>
    <button
      v-for="dataset in datasets"
      :key="dataset.dataset_id"
      class="dataset-card dataset-select"
      @click="selectDataset(dataset)"
    >
      <strong>{{ dataset.instrument }} {{ dataset.timeframe }}</strong>
      <span>{{ dataset.bar_count }} 根 · {{ dataset.status }}</span>
    </button>
    <dl v-if="selected" class="dataset-meta">
      <dt>dataset_id</dt><dd>{{ selected.dataset_id }}</dd>
      <dt>data_revision</dt><dd>{{ selected.data_revision.slice(0, 20) }}…</dd>
      <dt>范围</dt><dd>{{ selected.coverage.first_trading_day }} — {{ selected.coverage.last_trading_day }}</dd>
      <dt>源</dt><dd>{{ selected.source.format }} / {{ selected.source.encoding }}</dd>
    </dl>
  </section>
</template>
