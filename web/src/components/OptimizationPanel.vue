<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { createStudy, getStudy, getStudyEvaluations, listAlgorithms } from '../api/client'
import { capitalConfig, executionRequest } from '../execution/config'
import type { AlgorithmDefinition, DatasetMeta, StudyEvaluation, StudyMetric } from '../types/api'

const props = defineProps<{ dataset: DatasetMeta | null }>()
const strategy = ref<AlgorithmDefinition | null>(null)
const riskFilter = ref<AlgorithmDefinition | null>(null)
const riskEnabled = ref(true)
const status = ref('idle')
const progress = ref(0)
const error = ref('')
const studyId = ref('')
const evaluations = ref<StudyEvaluation[]>([])
const stability = ref<Awaited<ReturnType<typeof getStudyEvaluations>>['stability'] | null>(null)
const parameterName = ref('')
const minimum = ref(10)
const maximum = ref(30)
const step = ref(10)
const method = ref<'grid' | 'random'>('grid')
const budget = ref(6)
const randomSeed = ref(20260801)
const primaryMetric = ref<StudyMetric>('total_return')
const minimumTrades = ref(1)

const numericParameters = computed(() => Object.entries(strategy.value?.parameter_schema.properties ?? {})
  .filter(([, rule]) => rule.type === 'integer' || rule.type === 'number'))

function configureParameter(name: string): void {
  parameterName.value = name
  const rule = strategy.value?.parameter_schema.properties[name]
  const fallback = Number(rule?.default ?? 1)
  const delta = Math.max(1, Math.round(Math.abs(fallback) / 2))
  minimum.value = Math.max(Number(rule?.minimum ?? -Infinity), fallback - delta)
  maximum.value = Math.min(Number(rule?.maximum ?? Infinity), fallback + delta)
  step.value = Math.max(1, Math.round((maximum.value - minimum.value) / 2))
}

function metric(value: number | null | undefined, name: StudyMetric): string {
  if (value === null || value === undefined) return '—'
  if (name === 'total_return' || name === 'max_drawdown' || name === 'win_rate') return `${(value * 100).toFixed(2)}%`
  return value.toFixed(name === 'trade_count' ? 0 : 3)
}

async function run(): Promise<void> {
  const dataset = props.dataset
  const definition = strategy.value
  if (!dataset || !definition || !parameterName.value) return
  if (dataset.coverage.last_bar_index - dataset.coverage.first_bar_index < 9) {
    error.value = '数据量不足，训练集和验证集至少需要 5 根 K 线'
    return
  }
  status.value = 'queued'
  progress.value = 0
  error.value = ''
  evaluations.value = []
  stability.value = null
  try {
    const windowStart = Math.max(dataset.coverage.first_bar_index, dataset.coverage.last_bar_index - 2999)
    const trainEnd = Math.floor(windowStart + (dataset.coverage.last_bar_index - windowStart + 1) * .7) - 1
    const baseParameters = Object.fromEntries(Object.entries(definition.parameter_schema.properties)
      .map(([name, rule]) => [name, rule.default!]))
    const parameterRule = definition.parameter_schema.properties[parameterName.value]
    const risk = riskEnabled.value && riskFilter.value ? {
      algorithm: {
        kind: riskFilter.value.kind, algorithm_id: riskFilter.value.algorithm_id,
        algorithm_version: riskFilter.value.algorithm_version, source_hash: riskFilter.value.source_hash,
      },
      parameters: Object.fromEntries(Object.entries(riskFilter.value.parameter_schema.properties)
        .map(([name, rule]) => [name, rule.default ?? ''])),
      context: {
        market_state_revision: dataset.data_revision,
        sector_id: dataset.instrument?.product || dataset.dataset_id,
        legal_future_branches: [], handled_future_branches: [], observations: [],
      },
    } : null
    const accepted = await createStudy({
      dataset_id: dataset.dataset_id,
      data_revision: dataset.data_revision,
      strategy: {
        kind: definition.kind, algorithm_id: definition.algorithm_id,
        algorithm_version: definition.algorithm_version, source_hash: definition.source_hash,
      },
      base_parameters: baseParameters,
      ...(risk ? { risk_overlay: risk } : {}),
      search_space: [{
        name: parameterName.value,
        type: parameterRule?.type === 'number' ? 'number' : 'integer',
        minimum: minimum.value, maximum: maximum.value, step: step.value,
      }],
      objectives: [{ metric: primaryMetric.value, direction: primaryMetric.value === 'max_drawdown' ? 'minimize' : 'maximize' }],
      constraints: [{ metric: 'trade_count', operator: '>=', value: minimumTrades.value }],
      search: { method: method.value, budget: budget.value, random_seed: randomSeed.value },
      ranges: {
        train: { warmup_from_bar_index: windowStart, from_bar_index: windowStart, to_bar_index: trainEnd },
        validation: { warmup_from_bar_index: windowStart, from_bar_index: trainEnd + 1, to_bar_index: dataset.coverage.last_bar_index },
      },
      execution: executionRequest({ contractMultiplier: dataset.instrument.contract_multiplier }),
      capital: capitalConfig(),
    })
    studyId.value = accepted.study_id
    let current = await getStudy(accepted.study_id)
    while (!['completed', 'failed', 'cancelled', 'interrupted'].includes(current.status)) {
      status.value = current.status
      progress.value = current.progress
      await new Promise((resolve) => window.setTimeout(resolve, 250))
      current = await getStudy(accepted.study_id)
    }
    if (current.status !== 'completed') throw new Error(current.error?.message ?? `优化任务 ${current.status}`)
    status.value = 'completed'
    progress.value = 1
    const result = await getStudyEvaluations(accepted.study_id)
    evaluations.value = [...result.evaluations].sort((left, right) => left.train_rank - right.train_rank)
    stability.value = result.stability
  } catch (cause) {
    status.value = 'failed'
    error.value = cause instanceof Error ? cause.message : '参数优化失败'
  }
}

onMounted(async () => {
  try {
    const definitions = await listAlgorithms()
    strategy.value = definitions.find((value) => value.kind === 'strategy') ?? null
    riskFilter.value = definitions.find((value) => value.kind === 'risk_filter' && value.algorithm_id === 'unified_risk_execution_overlay') ?? null
    const preferred = numericParameters.value.find(([name]) => name === 'ma_period') ?? numericParameters.value[0]
    if (preferred) configureParameter(preferred[0])
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '策略不可用'
  }
})
</script>

<template>
  <section class="optimization-panel" aria-label="参数优化">
    <div class="optimization-controls">
      <strong>{{ strategy?.name ?? '正在加载策略…' }}</strong>
      <label>参数
        <select :value="parameterName" @change="configureParameter(($event.target as HTMLSelectElement).value)">
          <option v-for="([name]) in numericParameters" :key="name" :value="name">{{ name }}</option>
        </select>
      </label>
      <label>最小 <input v-model.number="minimum" type="number" /></label>
      <label>最大 <input v-model.number="maximum" type="number" /></label>
      <label>步长 <input v-model.number="step" type="number" min="0.000001" /></label>
      <label>搜索
        <select v-model="method"><option value="grid">网格</option><option value="random">种子随机</option></select>
      </label>
      <label>预算 <input v-model.number="budget" type="number" min="1" max="100" /></label>
      <label>主目标
        <select v-model="primaryMetric">
          <option value="total_return">总收益</option><option value="sharpe">Sharpe</option>
          <option value="max_drawdown">最大回撤</option><option value="win_rate">胜率</option>
          <option value="profit_factor">Profit Factor</option>
          <option value="expectancy_i64">平均期望</option>
        </select>
      </label>
      <label>最少交易 <input v-model.number="minimumTrades" type="number" min="0" /></label>
      <label><input v-model="riskEnabled" type="checkbox" />统一风险覆盖</label>
      <small v-if="riskEnabled">{{ riskFilter?.name ?? '风险覆盖层不可用' }}（采用已发布默认参数）</small>
      <label v-if="method === 'random'">随机种子 <input v-model.number="randomSeed" type="number" /></label>
      <button :disabled="!dataset || !strategy || !parameterName || riskEnabled && !riskFilter || ['queued', 'running'].includes(status)" @click="run">开始训练/验证</button>
      <span>{{ status }} {{ Math.round(progress * 100) }}% <small v-if="studyId">{{ studyId }}</small></span>
      <span v-if="error" class="issue">{{ error }}</span>
    </div>
    <div v-if="stability" class="stability-summary">
      <span>选中候选 #{{ stability.selected_evaluation_index }}</span>
      <span>训练排名 {{ stability.selected_train_rank }}</span>
      <span>验证排名 {{ stability.selected_validation_rank }}</span>
      <span>可行候选 {{ stability.constraint_feasible_count }}</span>
      <span>主目标差距 {{ metric(stability.primary_absolute_gap, stability.primary_metric) }}</span>
      <span v-if="stability.warnings.length" class="issue">{{ stability.warnings.join(', ') }}</span>
    </div>
    <table v-if="evaluations.length" class="trade-table optimization-table">
      <thead><tr><th>训练排名</th><th>验证排名</th><th>参数</th><th>约束</th><th>训练主目标</th><th>验证主目标</th><th>训练交易</th><th>验证交易</th></tr></thead>
      <tbody>
        <tr v-for="evaluation in evaluations" :key="evaluation.evaluation_index" :class="{ selected: evaluation.evaluation_index === stability?.selected_evaluation_index }">
          <td>{{ evaluation.train_rank }}</td><td>{{ evaluation.validation_rank }}</td>
          <td>{{ JSON.stringify(evaluation.parameters) }}</td><td>{{ evaluation.constraints_satisfied ? '通过' : '未通过' }}</td>
          <td>{{ metric(evaluation.train_metrics[primaryMetric], primaryMetric) }}</td>
          <td>{{ metric(evaluation.validation_metrics[primaryMetric], primaryMetric) }}</td>
          <td>{{ evaluation.train_metrics.trade_count ?? 0 }}</td><td>{{ evaluation.validation_metrics.trade_count ?? 0 }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
