<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import MultiDatasetResearchPanel from './MultiDatasetResearchPanel.vue'
import { capitalConfig, executionRequest } from '../execution/config'
import {
  cancelStrategyComparison,
  createStrategyComparison,
  getStrategyComparison,
  getStrategyComparisonResults,
  listStrategyComparisons,
  getBacktestSummary,
  getBacktestTrades,
  getBacktestEquity,
  getBacktestChartEvents,
  listAlgorithms,
} from '../api/client'
import type {
  AlgorithmDefinition,
  DatasetMeta,
  StrategyComparisonResult,
  StrategyComparisonManifest,
  BacktestTrade,
  EquityRow,
  StrategyRunSource,
} from '../types/api'

const props = defineProps<{ dataset: DatasetMeta | null }>()
const emit = defineEmits<{ completed: [source: StrategyRunSource]; focusTrade: [trade: BacktestTrade] }>()

const definitions = ref<AlgorithmDefinition[]>([])
const selected = ref<Record<string, boolean>>({})
const parameters = ref<Record<string, Record<string, string | number | boolean>>>({})
const riskFilter = ref<AlgorithmDefinition | null>(null)
const riskParameters = ref<Record<string, string | number | boolean>>({})
const initialCash = ref(100_000_000)
const commission = ref(300)
const slippageTicks = ref(1)
const marginRatio = ref(0.12)
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
const history = ref<StrategyComparisonManifest[]>([])
const nameFilter = ref('')
const familyFilter = ref('')
const hideFailed = ref(false)
const hideNoTrades = ref(false)
const sortKey = ref<'total_return' | 'max_drawdown' | 'trade_count' | 'sharpe' | 'profit_factor' | 'expectancy_i64'>('total_return')
const sortDirection = ref<'asc' | 'desc'>('desc')
const compareIds = ref<string[]>([])
const detail = ref<{ result: StrategyComparisonResult; trades: BacktestTrade[]; equity: EquityRow[] } | null>(null)

const formalStrategies = computed(() => definitions.value.filter((value) =>
  value.kind === 'strategy' && value.comparison_eligible === true && value.research_role === 'formal_strategy'))
const selectedStrategies = computed(() => formalStrategies.value.filter((value) => selected.value[value.algorithm_id]))
const running = computed(() => ['queued', 'running', 'cancelling'].includes(status.value))
const visibleResults = computed(() => results.value.filter((item) => {
  if (hideFailed.value && item.status !== 'completed') return false
  if (hideNoTrades.value && item.summary?.trade_count === 0) return false
  if (nameFilter.value && !item.name.toLowerCase().includes(nameFilter.value.toLowerCase())) return false
  if (familyFilter.value && item.strategy_family !== familyFilter.value) return false
  return true
}).sort((left, right) => {
  const a = left.summary?.[sortKey.value]
  const b = right.summary?.[sortKey.value]
  if (a == null) return b == null ? left.algorithm_id.localeCompare(right.algorithm_id) : 1
  if (b == null) return -1
  const result = Number(a) - Number(b)
  return sortDirection.value === 'asc' ? result : -result
}))
const families = computed(() => [...new Set(results.value.map((item) => item.strategy_family))].sort())
const compared = computed(() => results.value.filter((item) => item.run_id && compareIds.value.includes(item.run_id)))
const normalizedCurves = ref<Record<string, Array<{ bar_index: number; equity: number; drawdown: number }>>>({})

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

function toggleSort(key: typeof sortKey.value): void {
  if (sortKey.value === key) sortDirection.value = sortDirection.value === 'asc' ? 'desc' : 'asc'
  else { sortKey.value = key; sortDirection.value = key === 'max_drawdown' ? 'asc' : 'desc' }
}

function differsFromDefault(item: StrategyComparisonResult): boolean {
  const definition = formalStrategies.value.find((value) => value.algorithm_id === item.algorithm_id)
  return !!definition && Object.entries(item.parameters).some(([name, value]) => definition.parameter_schema.properties[name]?.default !== value)
}

function isStale(manifest: StrategyComparisonManifest): boolean {
  return manifest.dataset.data_revision !== props.dataset?.data_revision || manifest.strategies.some((saved) => {
    const current = definitions.value.find((value) => value.algorithm_id === saved.strategy.algorithm_id)
    return !current || current.algorithm_version !== saved.strategy.algorithm_version || current.source_hash !== saved.strategy.source_hash
  })
}

async function restore(manifest: StrategyComparisonManifest): Promise<void> {
  comparisonId.value = manifest.comparison_id
  results.value = await getStrategyComparisonResults(manifest.comparison_id)
  status.value = 'completed'
}

async function refreshHistory(): Promise<void> {
  history.value = props.dataset ? (await listStrategyComparisons(props.dataset.dataset_id) ?? []) : []
}

async function openDetail(item: StrategyComparisonResult): Promise<void> {
  if (!item.run_id || item.status !== 'completed') { detail.value = { result: item, trades: [], equity: [] }; return }
  const [summary, page, equity] = await Promise.all([getBacktestSummary(item.run_id), getBacktestTrades(item.run_id), getBacktestEquity(item.run_id)])
  item.summary = summary
  detail.value = { result: item, trades: page.rows, equity }
}

async function toggleCompare(item: StrategyComparisonResult): Promise<void> {
  if (!item.run_id) return
  if (compareIds.value.includes(item.run_id)) { compareIds.value = compareIds.value.filter((id) => id !== item.run_id); return }
  if (compareIds.value.length >= 5) { error.value = '最多同时叠加 5 个策略'; return }
  const equity = await getBacktestEquity(item.run_id)
  const first = equity[0]?.equity_i64
  normalizedCurves.value[item.run_id] = !first ? [] : equity.map((row) => ({ bar_index: row.bar_index, equity: row.equity_i64 / first * 100, drawdown: row.drawdown }))
  compareIds.value.push(item.run_id)
}

async function loadToChart(item: StrategyComparisonResult): Promise<void> {
  if (!item.run_id) return
  const definition = formalStrategies.value.find((value) => value.algorithm_id === item.algorithm_id)
  if (!definition) return
  const events = await getBacktestChartEvents(item.run_id)
  const objects = events.filter((event) => event.operation === 'upsert').map((event) => ({
    object_id: event.object_id, bar_index: Number(event.payload.bar_index ?? event.known_at_bar_index),
    time: Number(event.payload.timestamp_utc ?? 0), price_i64: Number(event.payload.price_i64 ?? 0),
    confirmed_at_bar_index: event.known_at_bar_index, known_at_bar_index: event.known_at_bar_index,
    object_revision: event.object_revision, label: String(event.payload.display_label ?? event.object_type),
  }))
  emit('completed', { source_type: 'StrategyRunSource', source_id: `run-source-${item.run_id}`, run_id: item.run_id, definition, status: 'completed', visible: true, objects, signals: [] })
}

function curvePoints(runId: string, field: 'equity' | 'drawdown'): string {
  const rows = normalizedCurves.value[runId] ?? []
  if (!rows.length) return ''
  const step = 560 / Math.max(1, rows.length - 1)
  return rows.map((row, index) => `${20 + index * step},${field === 'equity' ? 180 - Math.max(-80, Math.min(80, row.equity - 100)) : 20 + row.drawdown * 600}`).join(' ')
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
      execution: executionRequest({ commissionAmountI64: commission.value, slippageTicks: slippageTicks.value, marginRatio: marginRatio.value, contractMultiplier: dataset.instrument.contract_multiplier }),
      capital: capitalConfig(initialCash.value),
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
    await refreshHistory()
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
  detail.value = null
  compareIds.value = []
  void refreshHistory()
})

onMounted(async () => {
  try {
    definitions.value = await listAlgorithms()
    riskFilter.value = definitions.value.find((value) => value.kind === 'risk_filter' && value.algorithm_id === 'unified_risk_execution_overlay') ?? null
    riskParameters.value = riskFilter.value ? defaults(riskFilter.value) : {}
    initializeSelections()
    await refreshHistory()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '算法目录不可用'
  }
})
</script>

<template>
  <MultiDatasetResearchPanel :dataset="dataset" />
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
      <label>合约乘数 <output>{{ dataset?.instrument.contract_multiplier ?? '—' }}</output></label>
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
        <section class="history">
          <strong>历史研究</strong>
          <button @click="refreshHistory">刷新</button>
          <button v-for="item in history" :key="item.comparison_id" :class="{ stale: isStale(item) }" @click="restore(item)">
            {{ new Date(item.created_at).toLocaleString() }} · {{ item.comparison_signature.slice(0, 14) }} {{ isStale(item) ? '· 已失效（只读）' : '' }}
          </button>
        </section>
        <div class="result-filters">
          <input v-model="nameFilter" aria-label="策略名称过滤" placeholder="策略名称" />
          <select v-model="familyFilter" aria-label="策略族过滤"><option value="">全部策略族</option><option v-for="family in families" :key="family">{{ family }}</option></select>
          <label><input v-model="hideFailed" type="checkbox" />隐藏失败</label>
          <label><input v-model="hideNoTrades" type="checkbox" />隐藏零交易</label>
        </div>
        <p v-if="results.length === 0">运行完成后，这里显示所有策略的同口径基础结果。</p>
        <table v-else class="ranking-table">
          <thead><tr><th>对比</th><th>策略</th><th>分层</th><th>状态</th><th @click="toggleSort('total_return')">总收益</th><th @click="toggleSort('max_drawdown')">最大回撤</th><th @click="toggleSort('sharpe')">Sharpe</th><th @click="toggleSort('profit_factor')">PF</th><th @click="toggleSort('expectancy_i64')">期望</th><th @click="toggleSort('trade_count')">交易数</th><th>成本</th><th>Run</th></tr></thead>
          <tbody>
            <tr v-for="item in visibleResults" :key="item.algorithm_id" :class="{ selected: detail?.result.run_id === item.run_id }" @click="openDetail(item)">
              <td><input type="checkbox" :checked="!!item.run_id && compareIds.includes(item.run_id)" :disabled="!item.run_id" @click.stop="toggleCompare(item)" /></td>
              <td>{{ item.name }} <small v-if="differsFromDefault(item)">参数已覆盖</small></td><td>{{ item.tier ?? '—' }} <b v-if="item.pareto">Pareto</b></td><td>{{ item.status }}</td>
              <td>{{ item.summary ? `${(item.summary.total_return * 100).toFixed(2)}%` : '—' }}</td>
              <td>{{ item.summary ? `${(item.summary.max_drawdown * 100).toFixed(2)}%` : '—' }}</td>
              <td>{{ item.summary?.sharpe ?? '—（不可年化）' }}</td><td>{{ item.summary?.profit_factor ?? (item.summary?.trade_count ? '∞/无亏损' : '—') }}</td>
              <td>{{ item.summary?.expectancy_i64 ?? '—' }}</td><td>{{ item.summary?.trade_count ?? '—' }} <small v-if="item.tier === 'profitable_low_sample'">低样本</small></td>
              <td>{{ item.summary ? (item.summary.total_commission_i64 + item.summary.total_slippage_i64) : '—' }}</td><td>{{ item.run_id ?? item.error?.message ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
        <section v-if="results.some((item) => item.summary)" class="pareto-chart" aria-label="收益回撤散点图">
          <strong>收益 ↑ / 最大回撤 →（越左越好）</strong>
          <svg viewBox="0 0 600 220" role="img">
            <circle v-for="(item, index) in results.filter((value) => value.summary)" :key="item.algorithm_id"
              :cx="30 + Math.min(540, (item.summary?.max_drawdown ?? 0) * 1000)" :cy="190 - Math.max(-170, Math.min(170, (item.summary?.total_return ?? 0) * 400))"
              :r="Math.max(4, Math.min(14, Math.sqrt(item.summary?.trade_count ?? 0)))" :class="['point', item.tier, { pareto: item.pareto }]" @click="openDetail(item)"><title>{{ item.name }} · {{ item.run_id }}</title></circle>
          </svg>
        </section>
        <section v-if="compared.length" class="comparison-detail">
          <strong>策略叠加（{{ compared.length }}/5，权益起点=100）</strong>
          <svg viewBox="0 0 600 200" aria-label="归一化权益与回撤叠加"><polyline v-for="(item, index) in compared" :key="`eq-${item.run_id}`" :points="curvePoints(item.run_id!, 'equity')" fill="none" :stroke="['#2962ff','#f23645','#089981','#f0b90b','#ab47bc'][index]" stroke-width="2" /><polyline v-for="(item, index) in compared" :key="`dd-${item.run_id}`" :points="curvePoints(item.run_id!, 'drawdown')" fill="none" :stroke="['#2962ff','#f23645','#089981','#f0b90b','#ab47bc'][index]" stroke-width="1" stroke-dasharray="4 3" /></svg>
          <table><thead><tr><th>策略</th><th>末值</th><th>最大回撤</th><th>参数</th></tr></thead><tbody><tr v-for="item in compared" :key="item.run_id"><td>{{ item.name }}</td><td>{{ normalizedCurves[item.run_id!]?.at(-1)?.equity.toFixed(2) }}</td><td>{{ item.summary?.max_drawdown }}</td><td>{{ JSON.stringify(item.parameters) }}</td></tr></tbody></table>
        </section>
        <section v-if="detail" class="run-detail">
          <h4>{{ detail.result.name }} · 单策略详情</h4>
          <p v-if="detail.result.error" class="issue">{{ detail.result.error.code }}: {{ detail.result.error.message }}</p>
          <button v-if="detail.result.run_id" @click="loadToChart(detail.result)">加载到对象树和主图</button>
          <p>交易 {{ detail.trades.length }} · 权益点 {{ detail.equity.length }} · 风控批准 {{ detail.result.summary?.risk_approved_count ?? 0 }} / 减量 {{ detail.result.summary?.risk_reduced_count ?? 0 }} / 阻断 {{ detail.result.summary?.risk_blocked_count ?? 0 }} / 熔断 {{ detail.result.summary?.risk_kill_switch_count ?? 0 }}</p>
          <table v-if="detail.trades.length"><thead><tr><th>交易</th><th>开/平仓</th><th>净盈亏</th><th>走势</th><th>中枢阶段</th><th>触发</th></tr></thead><tbody><tr v-for="trade in detail.trades" :key="trade.trade_id" @click="emit('focusTrade', trade)"><td>{{ trade.trade_id }}</td><td>{{ trade.entry_bar_index }} → {{ trade.exit_bar_index }}</td><td>{{ trade.net_pnl_i64 }}</td><td>{{ trade.market_l0 ?? '旧 run 不支持归因' }}</td><td>{{ trade.center_phase ?? '—' }}</td><td>{{ trade.trigger_category ?? '—' }}</td></tr></tbody></table>
          <div v-if="detail.result.attribution?.attribution_supported" class="heatmap"><button v-for="cell in detail.result.attribution.dimensions" :key="`${cell.dimension}-${cell.value}`" :style="{ opacity: String(Math.max(.25, Math.min(1, cell.trade_count / 20))), background: (cell.expectancy_i64 ?? 0) >= 0 ? '#167d52' : '#9b3540' }">{{ cell.dimension }}:{{ cell.value }}<br />n={{ cell.trade_count }} E={{ cell.expectancy_i64?.toFixed(1) ?? '—' }}</button></div>
        </section>
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
.history, .result-filters { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-bottom: 6px; }
.history button { max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.history .stale { color: #f0b90b; }
.ranking-table { min-width: 1050px; }
.ranking-table th { cursor: pointer; position: sticky; top: 0; background: #131722; }
.ranking-table tr.selected { background: #1e293b; }
.pareto-chart svg { width: 100%; min-height: 180px; border: 1px solid #2a2e39; }
.point { fill: #787b86; cursor: pointer; }.point.loss_making { fill: #f23645; }.point.profitable_low_sample { fill: #f0b90b; }.point.profitable_candidate, .point.pareto_candidate { fill: #089981; }.point.pareto { stroke: white; stroke-width: 3; }
.run-detail, .comparison-detail { margin-top: 8px; padding: 8px; border: 1px solid #2a2e39; }
.heatmap { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 4px; }
.issue { color: #f23645; }
@media (max-width: 900px) { .research-body { grid-template-columns: 1fr; } }
</style>
