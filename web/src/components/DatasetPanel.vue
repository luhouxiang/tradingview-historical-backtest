<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import {
  getDataset,
  getDatasetResearchReadiness,
  getJob,
  getSourceFiles,
  importSource,
  importSourcesBatch,
  listDatasets,
  startDatasetScan,
} from '../api/client'
import { logger } from '../logging/logger'
import type { DatasetMeta, DatasetResearchReadiness, DatasetSummary, SourceFile } from '../types/api'

type DatasetSelectionOrigin = 'automatic' | 'user'

const emit = defineEmits<{ selected: [dataset: DatasetMeta, origin: DatasetSelectionOrigin] }>()
const props = defineProps<{ selectedDataset?: DatasetMeta | null }>()

const sources = ref<SourceFile[]>([])
const datasets = ref<DatasetSummary[]>([])
const selected = ref<DatasetMeta | null>(props.selectedDataset ?? null)
const busy = ref(false)
const status = ref('')
const readiness = ref<DatasetResearchReadiness | null>(null)
const initialInstrument = (__TVBT_INITIAL_INSTRUMENT__ || 'AOL9').trim().toUpperCase()
const initialDatasetId = `SHFE.${initialInstrument}.5m`

const sourceStatusText: Record<SourceFile['status'], string> = {
  detected: '已识别',
  needs_mapping: '待补充映射',
  importable: '可导入',
  imported: '已导入',
  rejected: '已拒绝',
}

function sourceIssueText(issue: SourceFile['issues'][number]): string {
  return issue.message || issue.code
}

watch(() => props.selectedDataset, (dataset) => {
  if (dataset) selected.value = dataset
})

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
  const [sourceItems, catalog, readinessResult] = await Promise.all([
    getSourceFiles(), listDatasets(), getDatasetResearchReadiness(),
  ])
  sources.value = sourceItems
  datasets.value = catalog.datasets
  readiness.value = readinessResult
}

async function runBatchImport(): Promise<void> {
  const importable = sources.value.filter((source) => source.status === 'importable')
  if (importable.length === 0) return
  busy.value = true
  try {
    const accepted = await importSourcesBatch(importable)
    await waitForJob(accepted.job_id)
    await refresh()
    status.value = `批量导入完成：${importable.length} 个文件`
  } catch (error) {
    status.value = error instanceof Error ? error.message : '批量导入失败'
    logger.error('ui.error', 'Dataset batch import failed', { reason: status.value })
  } finally {
    busy.value = false
  }
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

async function selectDataset(item: DatasetSummary, origin: DatasetSelectionOrigin = 'user'): Promise<void> {
  selected.value = await getDataset(item.dataset_id, item.active_revision)
  emit('selected', selected.value, origin)
}

onMounted(async () => {
  try {
    await refresh()
    // The panel is conditionally mounted by the right-side tab. Reopening it must
    // not reselect the startup instrument and restore a saved tab over the user's
    // current "datasets" choice.
    if (props.selectedDataset) return
    const preferred = datasets.value.find((dataset) => dataset.dataset_id === initialDatasetId) ?? datasets.value[0]
    if (preferred) await selectDataset(preferred, 'automatic')
  } catch {
    // Go 服务启动前目录为空属于可恢复的界面状态。
  }
})
</script>

<template>
  <section class="dataset-panel">
    <div class="dataset-actions">
      <button :disabled="busy" @click="scan">扫描 history</button>
      <button :disabled="busy || !sources.some((source) => source.status === 'importable')" @click="runBatchImport">导入全部可用</button>
      <small>{{ status }}</small>
    </div>
    <h3>待导入源文件</h3>
    <div v-if="sources.length === 0" class="empty-panel">尚未扫描</div>
    <article v-for="source in sources" :key="source.source_file_id" class="dataset-card">
      <strong>{{ source.detected?.symbol ?? source.path }}</strong>
      <span>{{ source.detected?.timeframe }} · {{ sourceStatusText[source.status] }}</span>
      <button v-if="source.status === 'importable'" :disabled="busy" @click="runImport(source)">导入</button>
      <small
        v-for="issue in source.issues"
        :key="`${issue.code}-${issue.source_line ?? 0}`"
        class="issue"
        :title="issue.code"
      >{{ sourceIssueText(issue) }}</small>
    </article>
    <h3>数据集</h3>
    <aside v-if="readiness" class="research-readiness" :data-status="readiness.status">
      <strong>{{ readiness.status === 'certification_ready' ? '数据认证就绪' : '仅探索级数据' }}</strong>
      <span>合格独立组 {{ readiness.eligible_independence_group_count }}/{{ readiness.required_independence_groups }}</span>
      <small>每组至少 {{ readiness.required_trading_days }} 个交易日</small>
    </aside>
    <div v-if="datasets.length === 0" class="empty-panel">暂无数据集</div>
    <button
      v-for="dataset in datasets"
      :key="dataset.dataset_id"
      class="dataset-card dataset-select"
      @click="selectDataset(dataset)"
    >
      <strong>{{ dataset.instrument }} {{ dataset.timeframe }}</strong>
      <span>{{ dataset.bar_count }} 根 · {{ dataset.trading_day_count ?? 0 }} 交易日 · {{ dataset.independence_group ?? '未分组' }}</span>
    </button>
    <dl v-if="selected" class="dataset-meta">
      <dt>dataset_id</dt><dd>{{ selected.dataset_id }}</dd>
      <dt>data_revision</dt><dd>{{ selected.data_revision.slice(0, 20) }}…</dd>
      <dt>范围</dt><dd>{{ selected.coverage.first_trading_day }} — {{ selected.coverage.last_trading_day }}</dd>
      <dt>交易日</dt><dd>{{ selected.coverage.trading_day_count ?? '旧修订未记录' }}</dd>
      <dt>独立组</dt><dd>{{ selected.independence_group ?? '旧修订未记录' }}</dd>
      <dt>源</dt><dd>{{ selected.source.format }} / {{ selected.source.encoding }}</dd>
    </dl>
  </section>
</template>
