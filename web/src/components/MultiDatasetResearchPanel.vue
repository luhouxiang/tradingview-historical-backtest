<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  cancelResearchStudy, createResearchStudy, getDataset, getResearchStudy,
  getResearchStudyResults, listAlgorithms, listDatasets, listResearchStudies,
  resumeResearchStudy,
} from '../api/client'
import { capitalConfig, executionRequest } from '../execution/config'
import type {
  AlgorithmDefinition, DatasetMeta, DatasetSummary, ResearchDatasetResult,
  ResearchStudyAggregate, ResearchStudyManifest, ResearchStudyProgressDetail,
  ResearchStudyRequest, WalkForwardFoldResult,
} from '../types/api'

const props = defineProps<{ dataset: DatasetMeta | null }>()
const datasets = ref<DatasetSummary[]>([])
const selected = ref<Record<string, boolean>>({})
const strategies = ref<AlgorithmDefinition[]>([])
const strategyId = ref('')
const parameters = ref<Record<string, string | number | boolean>>({})
const history = ref<ResearchStudyManifest[]>([])
const studyId = ref('')
const status = ref('idle')
const progress = ref(0)
const progressDetail = ref<ResearchStudyProgressDetail | null>(null)
const error = ref('')
const results = ref<ResearchDatasetResult[]>([])
const aggregate = ref<ResearchStudyAggregate | null>(null)
const executionManifest = ref<ResearchStudyManifest | null>(null)
const walkForward = ref(true)
const stressTest = ref(true)
const statisticalValidation = ref(true)
const trainDays = ref(252)
const validationDays = ref(63)
const stepDays = ref(63)
const searchBudget = ref(9)
const tunable = ref<Record<string, boolean>>({})
const candidateText = ref<Record<string, string>>({})
const running = computed(() => ['queued', 'running', 'cancelling'].includes(status.value))
const formal = computed(() => strategies.value.filter((item) => item.kind === 'strategy' && item.comparison_eligible && item.research_role === 'formal_strategy'))
const timeframe = computed(() => props.dataset?.timeframe ?? datasets.value.find((item) => selected.value[item.dataset_id])?.timeframe ?? '')
const compatible = computed(() => datasets.value.filter((item) => !timeframe.value || item.timeframe === timeframe.value))
const chosen = computed(() => compatible.value.filter((item) => selected.value[item.dataset_id]))
const currentStrategy = computed(() => formal.value.find((item) => item.algorithm_id === strategyId.value) ?? null)
const allFolds = computed<WalkForwardFoldResult[]>(() => results.value.flatMap((item) => item.folds ?? []))
const foldLine = computed(() => {
  const values = allFolds.value.filter((fold) => fold.status === 'completed' && fold.validation_metrics?.total_return != null).map((fold) => Number(fold.validation_metrics?.total_return))
  if (!values.length) return ''
  const low = Math.min(0, ...values); const high = Math.max(0, ...values); const span = high - low || 1
  return values.map((value, index) => `${values.length === 1 ? 50 : index * 100 / (values.length - 1)},${90 - (value - low) / span * 80}`).join(' ')
})
const executionSummary = computed(() => {
  const manifest = executionManifest.value
  if (!manifest) return ''
  if (manifest.execution.semantic_version !== '1.0.0') return '执行语义：未版本化旧研究（保留原始 manifest）'
  const multipliers = manifest.datasets.map((item) => item.execution?.contract_multiplier).filter((value) => value != null)
  return `执行语义 v1.0.0 · 每数据集品种配置乘数 ${multipliers.length ? multipliers.join('/') : '旧清单未记录'} · ${String(manifest.execution.contract_multiplier_source ?? '来源未知')}`
})

function defaults(definition: AlgorithmDefinition | null): Record<string, string | number | boolean> {
  return Object.fromEntries(Object.entries(definition?.parameter_schema.properties ?? {}).map(([name, rule]) => [name, rule.default ?? '']))
}

function chooseStrategy(): void {
  parameters.value = defaults(currentStrategy.value)
  tunable.value = {}
  candidateText.value = {}
  let selectedFirst = false
  for (const [name, rule] of Object.entries(currentStrategy.value?.parameter_schema.properties ?? {})) {
    const value = parameters.value[name]
    const numeric = rule.type === 'integer' || rule.type === 'number'
    const eligible = numeric && name !== 'checkpoint_interval'
    tunable.value[name] = eligible && !selectedFirst
    if (eligible) selectedFirst = true
    if (numeric && typeof value === 'number') {
      const delta = rule.type === 'integer' ? Math.max(1, Math.round(Math.abs(value) * .1)) : Math.max(.01, Math.abs(value) * .1)
      const values = [value - delta, value, value + delta]
        .map((item) => Math.max(rule.minimum ?? item, Math.min(rule.maximum ?? item, item)))
      candidateText.value[name] = [...new Set(values)].join(',')
    } else if (rule.type === 'boolean') candidateText.value[name] = `${value},${!value}`
    else candidateText.value[name] = String(value)
  }
}

function searchSpace(): NonNullable<ResearchStudyRequest['walk_forward']>['search_space'] {
  const rules = currentStrategy.value?.parameter_schema.properties ?? {}
  return Object.keys(rules).filter((name) => tunable.value[name]).map((name) => {
    const rule = rules[name]
    const candidates = candidateText.value[name].split(',').map((raw) => {
      const value = raw.trim()
      if (rule.type === 'integer') return Number.parseInt(value, 10)
      if (rule.type === 'number') return Number(value)
      if (rule.type === 'boolean') return value === 'true'
      return value
    })
    return { name, type: rule.type, candidates }
  })
}
function updateParameter(name: string, type: string, event: Event): void {
  const input = event.target as HTMLInputElement
  const raw = input.value
  parameters.value[name] = type === 'boolean' ? input.checked : type === 'integer' ? Number.parseInt(raw, 10) : type === 'number' ? Number(raw) : raw
}
function percent(value: number | null | undefined): string { return value == null ? '—' : `${(value * 100).toFixed(2)}%` }

function evidenceValue(value: unknown): string {
  if (value == null) return '—'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4)
  return String(value)
}

const stageText: Record<ResearchStudyProgressDetail['stage'], string> = {
  dataset_backtests: '逐数据集回测',
  walk_forward: '走步训练与验证',
  stress_test: '执行与成本压力测试',
  bootstrap: '区块 Bootstrap',
  parameter_neighborhood: '参数邻域验证',
  aggregation: '统计汇总与认证',
  committing: '原子提交研究结果',
}

const progressText = computed(() => {
  const detail = progressDetail.value
  if (!detail) {
    return running.value && progress.value >= 0.99
      ? '最后阶段仍在执行（旧任务未提供细分进度）'
      : ''
  }
  const count = detail.total_count > 0 ? ` ${detail.completed_count}/${detail.total_count}` : ''
  const scenario = detail.current_scenario_id ? ` · ${detail.current_scenario_id}` : ''
  const dataset = detail.current_dataset_id ? ` · ${detail.current_dataset_id}` : ''
  return `${stageText[detail.stage]}${count}${scenario}${dataset}`
})
const idleText = computed(() => {
  if (chosen.value.length === 0) return '请选择至少一个数据集'
  if (!currentStrategy.value) return '请选择一个正式策略'
  return '配置就绪，请点击“运行研究”启动'
})

async function load(): Promise<void> {
  try {
    const [catalog, algorithms, studies] = await Promise.all([listDatasets(), listAlgorithms(), listResearchStudies()])
    datasets.value = catalog.datasets.filter((item) => item.status === 'ready')
    strategies.value = algorithms
    history.value = studies
    strategyId.value ||= formal.value[0]?.algorithm_id ?? ''
    chooseStrategy()
    for (const item of compatible.value) selected.value[item.dataset_id] = true
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause) }
}

async function poll(id: string): Promise<void> {
  let current = await getResearchStudy(id)
  while (['queued', 'running', 'cancelling'].includes(current.status)) {
    status.value = current.status; progress.value = current.progress; progressDetail.value = current.progress_detail ?? null
    await new Promise((resolve) => setTimeout(resolve, 250))
    current = await getResearchStudy(id)
  }
  status.value = current.status; progress.value = current.progress; progressDetail.value = current.progress_detail ?? null
  if (current.status === 'completed') {
    executionManifest.value = current.manifest ?? null
    const value = await getResearchStudyResults(id)
    results.value = value.items; aggregate.value = value.aggregate
    history.value = await listResearchStudies()
  } else if (current.error) error.value = current.error.message
}

async function start(): Promise<void> {
  const strategy = currentStrategy.value
  if (!strategy || chosen.value.length < 1) { error.value = '请选择至少一个数据集和一个正式策略。'; return }
  error.value = ''; results.value = []; aggregate.value = null; executionManifest.value = null; progressDetail.value = null
  try {
    const metas = await Promise.all(chosen.value.map((item) => getDataset(item.dataset_id, item.active_revision)))
    const spaces = searchSpace()
    const request: ResearchStudyRequest = {
      datasets: metas.map((item) => ({ dataset_id: item.dataset_id, data_revision: item.data_revision, range: { warmup_from_bar_index: item.coverage.first_bar_index, from_bar_index: item.coverage.first_bar_index, to_bar_index: item.coverage.last_bar_index } })),
      strategy: { kind: 'strategy', algorithm_id: strategy.algorithm_id, algorithm_version: strategy.algorithm_version, source_hash: strategy.source_hash },
      parameters: parameters.value,
      execution: executionRequest(),
      capital: capitalConfig(), random_seed: 20260824,
    }
    if (walkForward.value) request.walk_forward = {
      train_trading_days: trainDays.value, validation_trading_days: validationDays.value,
      step_trading_days: stepDays.value, search_space: spaces,
      objectives: [{ metric: 'total_return', direction: 'maximize' }], constraints: [],
      search: { method: 'grid', budget: searchBudget.value, random_seed: 20260824 },
    }
    if (walkForward.value && stressTest.value) request.stress_test = {
      suite_version: '1.0.0', volume_participation_rate: 0.1,
    }
    if (walkForward.value && statisticalValidation.value) request.statistical_validation = {
      method_version: '1.0.0', block_size_trading_days: 5, iterations: 2000,
      confidence_level: 0.95, random_seed: 20260824, holm_alpha: 0.05,
    }
    const accepted = await createResearchStudy(request)
    studyId.value = accepted.research_study_id; status.value = accepted.status
    await poll(studyId.value)
  } catch (cause) { error.value = cause instanceof Error ? cause.message : String(cause); status.value = 'failed' }
}

async function restore(item: ResearchStudyManifest): Promise<void> {
  studyId.value = item.research_study_id; status.value = 'completed'; progress.value = 1; progressDetail.value = null
  executionManifest.value = item
  const value = await getResearchStudyResults(studyId.value)
  results.value = value.items; aggregate.value = value.aggregate
}

async function cancel(): Promise<void> { if (studyId.value) status.value = (await cancelResearchStudy(studyId.value)).status }
async function resume(): Promise<void> { if (studyId.value) { const value = await resumeResearchStudy(studyId.value); status.value = value.status; await poll(studyId.value) } }

onMounted(load)
</script>

<template>
  <section class="multi-research" aria-label="同周期多数据集研究">
    <header><h3>单周期可靠性研究（12B–12E）</h3><span>{{ timeframe || '选择周期' }} · {{ chosen.length }} 个数据集</span></header>
    <div class="controls">
      <label>固定策略<select v-model="strategyId" @change="chooseStrategy"><option v-for="item in formal" :key="item.algorithm_id" :value="item.algorithm_id">{{ item.name }} · {{ item.algorithm_version }}</option></select></label>
      <label v-for="(rule, name) in currentStrategy?.parameter_schema.properties" :key="name">{{ name }}<input v-if="rule.type === 'boolean'" type="checkbox" :checked="Boolean(parameters[name])" @change="updateParameter(name, rule.type, $event)"><input v-else :value="parameters[name]" :type="rule.type === 'number' || rule.type === 'integer' ? 'number' : 'text'" @input="updateParameter(name, rule.type, $event)"></label>
    </div>
    <div class="walk-config">
      <label><input v-model="walkForward" type="checkbox">走步样本外验证</label>
      <template v-if="walkForward">
        <label><input v-model="stressTest" type="checkbox">执行与成本压力测试</label>
        <label><input v-model="statisticalValidation" type="checkbox">统计稳健性与认证</label>
        <label>训练交易日<input v-model.number="trainDays" type="number" min="2"></label>
        <label>验证交易日<input v-model.number="validationDays" type="number" min="1"></label>
        <label>步长交易日<input v-model.number="stepDays" type="number" :min="validationDays"></label>
        <label>每折搜索预算<input v-model.number="searchBudget" type="number" min="1" max="100"></label>
        <label v-for="(_, name) in currentStrategy?.parameter_schema.properties" :key="`search-${name}`">
          <input v-model="tunable[name]" type="checkbox">选参 {{ name }}
          <input v-if="tunable[name]" v-model="candidateText[name]" aria-label="候选值" placeholder="逗号分隔候选值">
        </label>
      </template>
    </div>
    <div class="dataset-grid">
      <label v-for="item in compatible" :key="item.dataset_id"><input v-model="selected[item.dataset_id]" type="checkbox">{{ item.dataset_id }} <small>{{ item.independence_group }} · {{ item.trading_day_count ?? 0 }}日</small></label>
    </div>
    <div class="actions">
      <button class="primary-action" aria-label="运行单周期可靠性研究" :disabled="running || chosen.length < 1 || !currentStrategy" @click="start">▶ 运行研究</button>
      <button class="secondary-action" :disabled="!running" @click="cancel">取消</button>
      <button class="secondary-action" :disabled="!['failed', 'cancelled', 'interrupted'].includes(status)" @click="resume">恢复</button>
      <strong :class="{ ready: status === 'idle' && chosen.length > 0 && currentStrategy }">{{ status === 'idle' ? idleText : `${status} · ${Math.round(progress * 100)}%` }}</strong>
      <span v-if="progressText" class="progress-detail">{{ progressText }}</span>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="chosen.length === 1" class="warning" role="note">单数据集研究可以执行走步、压力和统计验证，但独立组不足，只能形成探索性证据。</p>
    <p v-if="executionSummary" class="execution-summary">{{ executionSummary }}</p>
    <div v-if="aggregate" class="evidence">
      <strong>{{ aggregate.data_status === 'certification_ready' ? '数据达到认证基础' : '探索性证据' }}</strong>
      <span>独立组 {{ aggregate.eligible_independence_group_count }}/3</span><span>等权收益 {{ percent(aggregate.total_return) }}</span>
      <span>中位收益 {{ percent(aggregate.median_dataset_return) }}</span><span>盈利比例 {{ percent(aggregate.profitable_dataset_ratio) }}</span>
      <span>最差 {{ aggregate.worst_dataset_id }} {{ percent(aggregate.worst_dataset_return) }}</span><span>成交 {{ aggregate.total_trade_count }}</span>
    </div>
    <table v-if="results.length"><thead><tr><th>数据集</th><th>独立组</th><th>状态</th><th>收益</th><th>回撤</th><th>成交</th></tr></thead><tbody><tr v-for="item in results" :key="item.dataset_id"><td>{{ item.dataset_id }}</td><td>{{ item.independence_group }}</td><td>{{ item.status }}</td><td>{{ percent(item.summary?.total_return) }}</td><td>{{ percent(item.summary?.max_drawdown) }}</td><td>{{ item.summary?.trade_count ?? '—' }}</td></tr></tbody></table>
    <details v-if="history.length"><summary>历史研究（{{ history.length }}）</summary><button v-for="item in history" :key="item.research_study_id" @click="restore(item)">{{ item.created_at }} · {{ item.timeframe }} · {{ item.datasets.length }} 数据集</button></details>
    <template v-if="aggregate?.walk_forward_fold_count !== undefined">
      <div class="evidence walk-evidence">
        <span>样本外折 {{ aggregate.completed_walk_forward_fold_count }}/{{ aggregate.walk_forward_fold_count }}</span>
        <span>盈利折 {{ percent(aggregate.profitable_fold_ratio) }}</span>
        <span>最差折回撤 {{ percent(aggregate.worst_fold_max_drawdown) }}</span>
        <span>样本外成交 {{ aggregate.out_of_sample_trade_count }}</span>
        <span>参数稳定度 {{ percent(aggregate.parameter_stability) }}</span>
      </div>
    </template>
    <div v-if="foldLine" class="fold-chart" aria-label="走步样本外收益折线">
      <strong>走步样本外收益折线</strong>
      <svg viewBox="0 0 100 100" role="img"><line x1="0" y1="90" x2="100" y2="90"/><polyline :points="foldLine"/></svg>
    </div>
    <template v-if="aggregate?.stress_scenarios?.length">
      <div class="evidence stress-evidence">
        <strong>压力测试</strong>
        <span>首个失效场景 {{ aggregate.first_failure_scenario ?? '无' }}</span>
      </div>
      <table class="stress-table">
        <thead><tr><th>场景</th><th>收益</th><th>回撤</th><th>成交率</th><th>边际收益退化</th><th>状态/原因</th></tr></thead>
        <tbody><tr v-for="scenario in aggregate.stress_scenarios" :key="scenario.scenario_id">
          <td>{{ scenario.scenario_id }}</td><td>{{ percent(scenario.total_return) }}</td>
          <td>{{ percent(scenario.max_drawdown) }}</td><td>{{ percent(scenario.fill_rate) }}</td>
          <td>{{ percent(scenario.return_degradation) }}</td>
          <td>{{ scenario.failure_reason ?? scenario.status }}</td>
        </tr></tbody>
      </table>
    </template>
    <template v-if="aggregate?.certification">
      <div class="certification" :class="`tier-${aggregate.certification.tier}`">
        <strong>证据等级：{{ aggregate.certification.tier }}</strong>
        <span>独立组等效成交 {{ evidenceValue(aggregate.certification_trade_count) }}</span>
        <span>样本外期望值 {{ evidenceValue(aggregate.out_of_sample_expectancy_i64) }}</span>
        <span>尝试参数组合 {{ aggregate.attempted_parameter_combinations?.length ?? 0 }}</span>
        <small>“可靠候选”仅表示通过历史研究门禁，不保证未来盈利。</small>
      </div>
      <table class="evidence-matrix">
        <thead><tr><th>证据门禁</th><th>等级</th><th>实际值</th><th>阈值</th><th>结果</th></tr></thead>
        <tbody><tr v-for="gate in aggregate.certification.evidence_matrix" :key="gate.gate_id">
          <td>{{ gate.gate_id }}</td><td>{{ gate.required_for }}</td><td>{{ evidenceValue(gate.actual) }}</td>
          <td>{{ evidenceValue(gate.threshold) }}</td><td>{{ gate.passed ? '通过' : gate.reason }}</td>
        </tr></tbody>
      </table>
    </template>
    <template v-if="aggregate?.statistical_evidence?.bootstrap">
      <table class="confidence-table">
        <thead><tr><th>Bootstrap 指标</th><th>点估计</th><th>95% 下界</th><th>95% 上界</th><th>原因</th></tr></thead>
        <tbody><tr v-for="(metric, name) in aggregate.statistical_evidence.bootstrap.metrics" :key="name">
          <td>{{ name }}</td><td>{{ evidenceValue(metric.point_estimate) }}</td>
          <td>{{ evidenceValue(metric.lower) }}</td><td>{{ evidenceValue(metric.upper) }}</td>
          <td>{{ metric.reason ?? '—' }}</td>
        </tr></tbody>
      </table>
      <p v-if="aggregate.statistical_evidence.multiple_comparisons?.multiple_comparison_warning" class="warning" role="note">
        多重比较警告：候选显著性必须查看 Holm 校正后的 p 值。
      </p>
      <div class="evidence neighborhood">
        <span>参数邻域 {{ aggregate.statistical_evidence.parameter_neighborhood?.completed_neighbor_count }}/{{ aggregate.statistical_evidence.parameter_neighborhood?.evaluated_neighbor_count }}</span>
        <span>通过率 {{ percent(aggregate.statistical_evidence.parameter_neighborhood?.pass_rate) }} / 60%</span>
      </div>
    </template>
    <table v-if="allFolds.length" class="fold-table">
      <thead><tr><th>数据集/折</th><th>训练区间</th><th>验证区间</th><th>选中参数</th><th>样本外收益</th><th>样本外回撤</th><th>漂移/失败原因</th></tr></thead>
      <tbody><tr v-for="fold in allFolds" :key="`${fold.dataset_id}-${fold.fold_index}`">
        <td>{{ fold.dataset_id }} #{{ fold.fold_index + 1 }}</td>
        <td>{{ fold.train_trading_day_from }}–{{ fold.train_trading_day_to }}</td>
        <td>{{ fold.validation_trading_day_from }}–{{ fold.validation_trading_day_to }}</td>
        <td>{{ fold.selected_parameters ? JSON.stringify(fold.selected_parameters) : '—' }}</td>
        <td>{{ percent(fold.validation_metrics?.total_return) }}</td><td>{{ percent(fold.validation_metrics?.max_drawdown) }}</td>
        <td>{{ fold.error?.code ?? (fold.parameter_changed ? `参数变化: ${fold.changed_parameter_names?.join(', ')}` : '稳定') }}</td>
      </tr></tbody>
    </table>
  </section>
</template>

<style scoped>
.multi-research{border:1px solid #334155;padding:10px;margin-bottom:12px;background:#101827;color:#dbeafe}.multi-research header,.actions,.evidence{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.multi-research h3{margin:0}.controls,.walk-config,.dataset-grid{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}.controls label,.walk-config label,.dataset-grid label{display:flex;gap:5px;align-items:center}.dataset-grid label{padding:5px;border:1px solid #334155}.dataset-grid small{color:#94a3b8}.evidence{padding:8px;background:#172033;margin-top:8px}table{width:100%;border-collapse:collapse;margin-top:8px}th,td{text-align:left;border-bottom:1px solid #334155;padding:4px}.fold-table{font-size:12px}details button{display:block;margin:4px 0}
.actions{padding:8px 0}.actions button{height:32px;padding:0 14px;border:1px solid #475569;border-radius:4px;cursor:pointer}.actions button:disabled{cursor:not-allowed;opacity:.55}.actions .primary-action{border-color:#2563eb;background:#2563eb;color:#fff;font-weight:700}.actions .primary-action:hover:not(:disabled){background:#1d4ed8}.actions .secondary-action{background:#1e293b}.actions .ready{color:#7dd3fc}
.fold-chart,.certification{margin-top:8px;padding:8px;background:#172033}.fold-chart svg{display:block;width:100%;height:110px}.fold-chart line{stroke:#64748b;stroke-width:.5}.fold-chart polyline{fill:none;stroke:#38bdf8;stroke-width:2}.certification{display:flex;gap:12px;flex-wrap:wrap}.certification small{flex-basis:100%;color:#fbbf24}.tier-reliable_candidate{border:1px solid #22c55e}.tier-research_candidate{border:1px solid #38bdf8}.tier-exploratory{border:1px solid #f59e0b}.warning{color:#fbbf24}.evidence-matrix td:last-child{max-width:320px;overflow-wrap:anywhere}
</style>
