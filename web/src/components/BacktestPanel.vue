<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { createBacktest, getBacktest, getBacktestChartEvents, getBacktestEquity, getBacktestSummary, getBacktestTrades, listAlgorithms } from '../api/client'
import type { AlgorithmDefinition, BacktestSummary, BacktestTrade, ChanTreeObject, DatasetMeta, EquityRow, StrategyRunSource } from '../types/api'

const props = defineProps<{ dataset: DatasetMeta | null; view: 'backtest' | 'trades' | 'equity' }>()
const emit = defineEmits<{ completed: [source: StrategyRunSource] }>()
const strategies = ref<AlgorithmDefinition[]>([])
const strategy = ref<AlgorithmDefinition | null>(null)
const strategyParameters = ref<Record<string, string | number | boolean>>({})
const status = ref('idle')
const error = ref('')
const runId = ref('')
const signature = ref('')
const summary = ref<BacktestSummary | null>(null)
const trades = ref<BacktestTrade[]>([])
const equity = ref<EquityRow[]>([])
const initialCash = ref(100_000_000)
const commission = ref(300)
const multiplier = ref(20)
const marginRatio = ref(.12)

const points = computed(() => {
  if (equity.value.length < 2) return ''
  const values = equity.value.map((row) => row.equity_i64)
  const low = Math.min(...values)
  const high = Math.max(...values)
  const span = Math.max(1, high - low)
  return values.map((value, index) => `${index / (values.length - 1) * 600},${100 - (value - low) / span * 90}`).join(' ')
})

async function run(): Promise<void> {
  const dataset = props.dataset
  const definition = strategy.value
  if (!dataset || !definition) return
  status.value = 'queued'
  error.value = ''
  summary.value = null
  try {
    const parameters = { ...strategyParameters.value }
    const accepted = await createBacktest({
      dataset_id: dataset.dataset_id, data_revision: dataset.data_revision,
      strategy: {
        kind: definition.kind, algorithm_id: definition.algorithm_id,
        algorithm_version: definition.algorithm_version, source_hash: definition.source_hash,
      },
      parameters,
      range: {
        warmup_from_bar_index: dataset.coverage.first_bar_index,
        from_bar_index: Math.max(dataset.coverage.first_bar_index, dataset.coverage.last_bar_index - 2999),
        to_bar_index: dataset.coverage.last_bar_index,
      },
      execution: {
        signal_timing: 'bar_close', fill_timing: 'next_bar_open',
        commission: { mode: 'fixed_per_contract', amount_i64: commission.value, money_scale: 100 },
        slippage: { mode: 'ticks', value: 1 }, contract_multiplier: multiplier.value,
        margin_ratio: marginRatio.value, intrabar_conflict_rule: 'worst_case',
      },
      capital: { initial_cash_i64: initialCash.value, currency: 'CNY', money_scale: 100 },
      random_seed: 20260801,
    })
    runId.value = accepted.run_id
    signature.value = accepted.run_signature
    let current = await getBacktest(accepted.run_id)
    while (!['completed', 'failed', 'cancelled', 'interrupted'].includes(current.status)) {
      status.value = current.status
      await new Promise((resolve) => window.setTimeout(resolve, 250))
      current = await getBacktest(accepted.run_id)
    }
    if (current.status !== 'completed') throw new Error(current.error?.message ?? `回测${current.status}`)
    status.value = 'completed'
    const [summaryValue, tradeValue, equityValue, causalEvents] = await Promise.all([
      getBacktestSummary(runId.value), getBacktestTrades(runId.value), getBacktestEquity(runId.value), getBacktestChartEvents(runId.value),
    ])
    summary.value = summaryValue
    trades.value = tradeValue.rows
    equity.value = equityValue
    const currentObjects = new Map<string, ChanTreeObject>()
    for (const event of causalEvents) {
      const key = `${event.object_type}:${event.object_id}`
      if (event.operation === 'delete') { currentObjects.delete(key); continue }
      const payload = event.payload
      const state = String(payload.state_to ?? payload.stage ?? payload.action ?? payload.event_type ?? event.object_type)
      const labels: Record<string, string> = {
        inside: '中枢内', below_without_S3: '中枢下方·无三卖', below_with_S3: '中枢下方·有三卖',
        above_without_B3: '中枢上方·无三买', above_with_B3: '中枢上方·有三买',
        waiting_B1: '等待标准一买', waiting_trend_divergence: '等待趋势背驰',
        long_after_B1: '标准一买后持多', short_after_S1: '标准一卖后持空',
        reverting_up_to_centre: '盘整底背驰·向上回归中枢',
        reverting_down_to_centre: '盘整顶背驰·向下回归中枢',
        returned_to_centre: '已回到中枢', converted_to_B3: '回归失败·转三买',
        converted_to_S3: '回归失败·转三卖', holding_upward_migration: '三买后上移持有',
        holding_downward_migration: '三卖后下移持有', migration_hold_exited: '迁移持有退出',
        later_centre_BUY_3_filtered: '后续中枢三买已过滤',
        later_centre_SELL_3_filtered: '后续中枢三卖已过滤',
        open_long: '开多', close_long: '平多', open_short: '开空', close_short: '平空',
      }
      currentObjects.set(key, {
        object_id: event.object_id,
        bar_index: Number(payload.bar_index ?? event.known_at_bar_index),
        time: Number(payload.timestamp_utc ?? 0),
        price_i64: Number(payload.price_i64 ?? 0),
        confirmed_at_bar_index: event.known_at_bar_index,
        known_at_bar_index: event.known_at_bar_index,
        object_revision: event.object_revision,
        label: labels[state] ?? state,
        detail: String(payload.reason_code ?? event.object_type),
      })
    }
    emit('completed', {
      source_type: 'StrategyRunSource', source_id: `run-source-${runId.value}`, run_id: runId.value,
      definition, status: 'completed', visible: true, objects: [...currentObjects.values()],
      signals: causalEvents.filter((event) => event.operation === 'upsert').map((event) => ({ ...event.payload, object_type: event.object_type, object_id: event.object_id })),
    })
  } catch (cause) {
    status.value = 'failed'
    error.value = cause instanceof Error ? cause.message : '回测失败'
  }
}

onMounted(async () => {
  try {
    strategies.value = (await listAlgorithms()).filter((value) => value.kind === 'strategy')
    strategy.value = strategies.value[0] ?? null
  }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '策略不可用' }
})

watch(strategy, (definition) => {
  strategyParameters.value = definition
    ? Object.fromEntries(Object.entries(definition.parameter_schema.properties).map(([name, rule]) => [name, rule.default ?? '']))
    : {}
})
</script>

<template>
  <section class="backtest-panel" aria-label="回测结果">
    <div class="backtest-controls">
      <select v-model="strategy" aria-label="选择回测策略">
        <option v-for="candidate in strategies" :key="candidate.algorithm_id" :value="candidate">{{ candidate.name }}</option>
      </select>
      <label v-for="(rule, name) in strategy?.parameter_schema.properties" :key="name">
        {{ name }}
        <input v-if="rule.type === 'boolean'" v-model="strategyParameters[name]" type="checkbox" />
        <input v-else v-model.number="strategyParameters[name]" type="number" :min="rule.minimum" :max="rule.maximum" />
      </label>
      <strong>{{ strategy?.name ?? '正在加载策略…' }}</strong>
      <label>初始资金 <input v-model.number="initialCash" type="number" min="0" /></label>
      <label>每手手续费 <input v-model.number="commission" type="number" min="0" /></label>
      <label>乘数 <input v-model.number="multiplier" type="number" min="0.01" step="0.01" /></label>
      <label>保证金 <input v-model.number="marginRatio" type="number" min="0.01" max="1" step="0.01" /></label>
      <button :disabled="!dataset || !strategy || ['queued', 'running'].includes(status)" @click="run">开始正式回测</button>
      <span>{{ status }} <small v-if="runId">{{ runId }} · {{ signature.slice(0, 18) }}</small></span>
      <span v-if="error" class="issue">{{ error }}</span>
    </div>
    <div v-if="view === 'backtest'" class="summary-grid">
      <template v-if="summary">
        <span>总收益 {{ (summary.total_return * 100).toFixed(2) }}%</span>
        <span>最大回撤 {{ (summary.max_drawdown * 100).toFixed(2) }}%</span>
        <span>交易 {{ summary.trade_count }}</span>
        <span>胜率 {{ summary.win_rate === null ? '—' : `${(summary.win_rate * 100).toFixed(1)}%` }}</span>
        <span>Sharpe {{ summary.sharpe?.toFixed(2) ?? '—' }}</span>
        <span>手续费 {{ summary.total_commission_i64 }}</span>
      </template>
    </div>
    <table v-else-if="view === 'trades'" class="trade-table">
      <thead><tr><th>ID</th><th>方向</th><th>入场</th><th>出场</th><th>净盈亏</th></tr></thead>
      <tbody><tr v-for="trade in trades" :key="trade.trade_id"><td>{{ trade.trade_id }}</td><td>{{ trade.side }}</td><td>{{ trade.entry_bar_index }} @ {{ trade.entry_price_i64 }}</td><td>{{ trade.exit_bar_index }} @ {{ trade.exit_price_i64 }}</td><td>{{ trade.net_pnl_i64 }}</td></tr></tbody>
    </table>
    <svg v-else class="equity-chart" viewBox="0 0 600 110" preserveAspectRatio="none" aria-label="权益曲线"><polyline :points="points" /></svg>
  </section>
</template>
