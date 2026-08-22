<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  cancelStrategyComparison,
  createStrategyComparison,
  getStrategyComparison,
  getStrategyComparisonResults,
  listAlgorithms,
} from '../api/client'
import type {
  AlgorithmDefinition,
  DatasetMeta,
  StrategyComparisonResult,
} from '../types/api'

const props = defineProps<{ dataset: DatasetMeta | null }>()

const definitions = ref<AlgorithmDefinition[]>([])
const selected = ref<Record<string, boolean>>({})
const parameters = ref<Record<string, Record<string, string | number | boolean>>>({})
const riskFilter = ref<AlgorithmDefinition | null>(null)
const riskParameters = ref<Record<string, string | number | boolean>>({})
const initialCash = ref(100_000_000)
const commission = ref(0)
const slippageTicks = ref(1)
const multiplier = ref(1)
const marginRatio = ref(0.1)
const minimumTradeCount = ref(20)
const status = ref('idle')
const progress = ref(0)
const totalCount = ref(0)
const completedCount = ref(0)
const failedCount = ref(0)
const currentAlgorithmId = ref<string | null>(null)
const comparisonId = ref('')
const error = ref('')
const results = ref<StrategyComparisonResult[]>([])

const formalStrategies = computed(() => definitions.value.filter((value) =>
  value.kind === 'strategy' && value.comparison_eligible === true && value.research_role === 'formal_strategy'))
const selectedStrategies = computed(() => formalStrategies.value.filter((value) => selected.value[value.algorithm_id]))
const running = computed(() => ['queued', 'running', 'cancelling'].includes(status.value))

function defaults(definition: AlgorithmDefinition): Record<string, string | number | boolean> {
  return Object.fromEntries(Object.entries(definition.parameter_schema.properties).map(([name, rule]) => [name, rule.default ?? '']))
}

function initializeSelections(): void {
  selected.value = Object.fromEntries(formalStrategies.value.map((value) => [value.algorithm_id, true]))
  parameters.value = Object.fromEntries(formalStrategies.value.map((value) => [value.algorithm_id, defaults(value)]))
}

function selectAll(value: boolean): void {
  selected.value = Object.fromEntries(formalStrategies.value.map((definition) => [definition.algorithm_id, value]))
}

async function run(): Promise<void> {
  const dataset = props.dataset
  if (!dataset || selectedStrategies.value.length === 0 || !riskFilter.value) return
  error.value = ''
  results.value = []
  status.value = 'queued'
  progress.value = 0
  try {
    const accepted = await createStrategyComparison({
      dataset_id: dataset.dataset_id,
      data_revision: dataset.data_revision,
      strategies: selectedStrategies.value.map((definition) => ({
        strategy: {
          kind: definition.kind,
          algorithm_id: definition.algorithm_id,
          algorithm_version: definition.algorithm_version,
          source_hash: definition.source_hash,
        },
        parameters: { ...parameters.value[definition.algorithm_id] },
      })),
      risk_overlay: {
        algorithm: {
          kind: 'risk_filter',
          algorithm_id: riskFilter.value.algorithm_id,
          algorithm_version: riskFilter.value.algorithm_version,
          source_hash: riskFilter.value.source_hash,
        },
        parameters: { ...riskParameters.value },
        context: {
          market_state_revision: dataset.data_revision,
          sector_id: dataset.instrument.product || dataset.dataset_id,
          legal_future_branches: [],
          handled_future_branches: [],
          observations: [],
        },
      },
      range: {
        warmup_from_bar_index: dataset.coverage.first_bar_index,
        from_bar_index: dataset.coverage.first_bar_index,
        to_bar_index: dataset.coverage.last_bar_index,
      },
      execution: {
        signal_timing: 'bar_close',
        fill_timing: 'next_bar_open',
        commission: { mode: 'fixed_per_contract', amount_i64: commission.value, money_scale: 100 },
        slippage: { mode: 'ticks', value: slippageTicks.value },
        contract_multiplier: multiplier.value,
        margin_ratio: marginRatio.value,
        intrabar_conflict_rule: 'worst_case',
      },
      capital: { initial_cash_i64: initialCash.value, currency: 'CNY', money_scale: 100 },
      random_seed: 20260822,
      minimum_trade_count: minimumTradeCount.value,
    })
    comparisonId.value = accepted.comparison_id
    let current = await getStrategyComparison(accepted.comparison_id)
    while (!['completed', 'failed', 'cancelled', 'interrupted'].includes(current.status)) {
      status.value = current.status
      progress.value = current.progress
      totalCount.value = current.total_count
      completedCount.value = current.completed_count
      failedCount.value = current.failed_count
      currentAlgorithmId.value = current.current_algorithm_id
      await new Promise((resolve) => window.setTimeout(resolve, 250))
      current = await getStrategyComparison(accepted.comparison_id)
    }
    status.value = current.status
    progress.value = current.progress
    totalCount.value = current.total_count
    completedCount.value = current.completed_count
    failedCount.value = current.failed_count
    currentAlgorithmId.value = current.current_algorithm_id
    if (current.status !== 'completed') throw new Error(current.error?.message ?? `策略研究${current.status}`)
    results.value = await getStrategyComparisonResults(accepted.comparison_id)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '策略研究失败'
    if (status.value !== 'cancelled') status.value = 'failed'
  }
}

async function cancel(): Promise<void> {
  if (!comparisonId.value || !running.value) return
  const current = await cancelStrategyComparison(comparisonId.value)
  status.value = current.status
}

watch(() => props.dataset?.dataset_id, () => {
  status.value = 'idle'
  comparisonId.value = ''
  results.value = []
  error.value = ''
})

onMounted(async () => {
  try {
    definitions.value = await listAlgorithms()
    riskFilter.value = definitions.value.find((value) => value.kind === 'risk_filter' && value.algorithm_id === 'unified_risk_execution_overlay') ?? null
    riskParameters.value = riskFilter.value ? defaults(riskFilter.value) : {}
    initializeSelections()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '算法目录不可用'
  }
})
</script>

<template>
  <section class="strategy-research" aria-label="策略研究工作台">
    <header class="research-toolbar">
      <strong>108课正式策略基线</strong>
      <span>{{ dataset?.dataset_id ?? '请先选择数据集' }}</span>
      <button @click="selectAll(true)">全选</button>
      <button @click="selectAll(false)">清空</button>
      <label>最少交易 <input v-model.number="minimumTradeCount" type="number" min="1" /></label>
      <label>初始资金 <input v-model.number="initialCash" type="number" min="1" /></label>
      <label>每手手续费 <input v-model.number="commission" type="number" min="0" /></label>
      <label>滑点(tick) <input v-model.number="slippageTicks" type="number" min="0" step="1" /></label>
      <label>乘数 <input v-model.number="multiplier" type="number" min="0.01" step="0.01" /></label>
      <label>保证金率 <input v-model.number="marginRatio" type="number" min="0.01" max="1" step="0.01" /></label>
      <details v-if="riskFilter" class="risk-settings">
        <summary>统一风控参数</summary>
        <label v-for="(rule, name) in riskFilter.parameter_schema.properties" :key="name">
          {{ name }}
          <input v-if="rule.type === 'boolean'" v-model="riskParameters[name]" type="checkbox" />
          <input v-else-if="rule.type === 'string'" v-model="riskParameters[name]" type="text" />
          <input v-else v-model.number="riskParameters[name]" type="number" :min="rule.minimum" :max="rule.maximum" />
        </label>
      </details>
      <button :disabled="!dataset || selectedStrategies.length === 0 || !riskFilter || running" @click="run">一键回测所选 {{ selectedStrategies.length }} 个策略</button>
      <button v-if="running" @click="cancel">取消</button>
    </header>

    <div class="research-progress">
      <progress :value="progress" max="1" />
      <span>{{ status }} · {{ completedCount }}/{{ totalCount || selectedStrategies.length }}</span>
      <span v-if="currentAlgorithmId">当前：{{ currentAlgorithmId }}</span>
      <span v-if="failedCount">失败：{{ failedCount }}</span>
      <small v-if="comparisonId">{{ comparisonId }}</small>
      <span v-if="error" class="issue">{{ error }}</span>
    </div>

    <div class="research-body">
      <aside class="strategy-selector">
        <details v-for="definition in formalStrategies" :key="definition.algorithm_id">
          <summary>
            <input v-model="selected[definition.algorithm_id]" type="checkbox" @click.stop />
            {{ definition.name }}
            <small>{{ definition.strategy_family }} · {{ definition.catalog_algorithm_ids?.join(', ') }}</small>
          </summary>
          <div class="parameter-grid">
            <label v-for="(rule, name) in definition.parameter_schema.properties" :key="name">
              {{ name }}
              <input v-if="rule.type === 'boolean'" v-model="parameters[definition.algorithm_id][name]" type="checkbox" />
              <input v-else-if="rule.type === 'string'" v-model="parameters[definition.algorithm_id][name]" type="text" />
              <input v-else v-model.number="parameters[definition.algorithm_id][name]" type="number" :min="rule.minimum" :max="rule.maximum" />
            </label>
          </div>
        </details>
      </aside>

      <div class="baseline-results">
        <p v-if="results.length === 0">运行完成后，这里显示所有策略的同口径基础结果。</p>
        <table v-else>
          <thead><tr><th>策略</th><th>状态</th><th>总收益</th><th>最大回撤</th><th>交易数</th><th>Run</th></tr></thead>
          <tbody>
            <tr v-for="item in results" :key="item.algorithm_id">
              <td>{{ item.name }}</td><td>{{ item.status }}</td>
              <td>{{ item.summary ? `${(item.summary.total_return * 100).toFixed(2)}%` : '—' }}</td>
              <td>{{ item.summary ? `${(item.summary.max_drawdown * 100).toFixed(2)}%` : '—' }}</td>
              <td>{{ item.summary?.trade_count ?? '—' }}</td><td>{{ item.run_id ?? item.error?.message ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<style scoped>
.strategy-research { display: grid; gap: 8px; height: 100%; min-height: 0; color: #d1d4dc; }
.research-toolbar, .research-progress { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.research-toolbar label { display: flex; gap: 4px; align-items: center; font-size: 12px; }
.research-toolbar input { width: 86px; }
.risk-settings { position: relative; }
.risk-settings[open] { padding: 5px; border: 1px solid #2a2e39; }
.risk-settings label { margin-top: 4px; justify-content: space-between; }
.research-progress progress { width: 180px; }
.research-body { display: grid; grid-template-columns: minmax(260px, 34%) minmax(0, 1fr); gap: 10px; min-height: 0; }
.strategy-selector, .baseline-results { overflow: auto; border: 1px solid #2a2e39; border-radius: 4px; padding: 8px; }
.strategy-selector details { border-bottom: 1px solid #2a2e39; padding: 5px 0; }
.strategy-selector summary { cursor: pointer; }
.strategy-selector small { margin-left: 6px; color: #787b86; }
.parameter-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 6px; padding: 8px 0 4px 22px; }
.parameter-grid label { display: flex; justify-content: space-between; gap: 6px; font-size: 12px; }
.parameter-grid input[type='number'], .parameter-grid input[type='text'] { width: 82px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
th, td { padding: 6px; border-bottom: 1px solid #2a2e39; text-align: left; }
.issue { color: #f23645; }
@media (max-width: 900px) { .research-body { grid-template-columns: 1fr; } }
</style>
