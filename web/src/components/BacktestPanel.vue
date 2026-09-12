<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { createBacktest, getBacktest, getBacktestChartEvents, getBacktestEquity, getBacktestSummary, getBacktestTrades, listAlgorithms } from '../api/client'
import { capitalConfig, executionRequest } from '../execution/config'
import type { AlgorithmDefinition, BacktestSummary, BacktestTrade, ChanTreeObject, DatasetMeta, EquityRow, RankingContext, RiskContext, StrategyRunSource } from '../types/api'

const props = defineProps<{ dataset: DatasetMeta | null; view: 'backtest' | 'trades' | 'equity' | 'workspace' }>()
const emit = defineEmits<{
  completed: [source: StrategyRunSource]
  'focus-trade': [trade: BacktestTrade]
}>()
const strategies = ref<AlgorithmDefinition[]>([])
const strategy = ref<AlgorithmDefinition | null>(null)
const strategyParameters = ref<Record<string, string | number | boolean>>({})
const riskFilters = ref<AlgorithmDefinition[]>([])
const riskFilter = ref<AlgorithmDefinition | null>(null)
const riskEnabled = ref(true)
const riskParameters = ref<Record<string, string | number | boolean>>({})
const riskContextText = ref('')
const status = ref('idle')
const error = ref('')
const runId = ref('')
const signature = ref('')
const summary = ref<BacktestSummary | null>(null)
const trades = ref<BacktestTrade[]>([])
const equity = ref<EquityRow[]>([])
const executionFacts = ref<Record<string, unknown> | null>(null)
const initialCash = ref(100_000_000)
const commission = ref(300)
const marginRatio = ref(.12)
const rankingContextText = ref('')
const restored = ref(false)
const restoreAttemptKey = ref('')
const LAST_RUN_STORAGE_KEY = 'tvbt:last-backtest:v1'
const STATUS_POLL_INTERVAL_MS = 250
const STATUS_POLL_RETRY_DELAYS_MS = [250, 500, 1_000, 2_000, 4_000]
const executing = computed(() => ['queued', 'running'].includes(status.value))
const panelRoot = ref<HTMLElement | null>(null)
const tradePane = ref<HTMLElement | null>(null)
const tradePaneHeight = ref<number | null>(null)
const selectedTradeId = ref<string | null>(null)

const parameterLabels: Record<string, string> = {
  allow_class_like_entries: '允许类二买入场', allow_normal: '允许普通强度', allow_strongest: '允许最强信号', allow_weakest: '允许最弱信号',
  checkpoint_interval: '检查点间隔（根）', normal_quantity: '普通信号手数', strongest_quantity: '最强信号手数', weakest_quantity: '最弱信号手数',
  allow_long: '允许做多', allow_short: '允许做空', ma_period: '均线周期', max_retest_bars: '最大回试根数', touch_tolerance_ticks: '触及容差（跳）',
  fast_period: '快速周期', slow_period: '慢速周期', signal_period: '信号周期', minimum_timeframe_minutes: '最小周期（分钟）',
  reclaim_confirm_bars: '重新站上确认根数', risk_off_confirm_bars: '风险退出确认根数', zero_axis_buffer_ticks: '零轴缓冲（跳）',
  allow_late_center: '允许后续中枢', first_center_quantity: '首中枢手数', late_center_quantity: '后续中枢手数', minimum_entry_volume: '最小入场成交量',
  estimated_round_trip_cost_i64: '预计往返成本', fast_execution_available: '可快速执行', max_entries_per_center: '每中枢最多入场次数',
  minimum_net_range_i64: '最小净区间', neutral_quantity: '中性强度手数', strong_quantity: '强信号手数', weak_quantity: '弱信号手数',
  odd_direction_is_down: '奇数段视为向下', operation_quantity: '操作手数', can_handle_high_change_candidate: '可处理高级别变化候选',
  can_handle_mid_center_continue: '可处理中级中枢延续', can_handle_mid_third_point: '可处理中级三类点', level_graph_profile_id: '级别图配置编号',
  execution_available: '允许执行交易', minimum_net_segment_i64: '最小净线段空间', partial_take_profit_quantity: '分批止盈手数',
  coarse_effective_hold_bars: '粗略有效站稳根数', enable_legacy_b1_macd_proxy: '启用旧一买 MACD 代理', flat_slope_ticks: '走平斜率阈值（跳）',
  legacy_divergence_min_bars: '旧背驰最少间隔根数', long_period: '长均线周期', short_period: '短均线周期', macd_fast_period: 'MACD 快速周期',
  macd_slow_period: 'MACD 慢速周期', macd_signal_period: 'MACD 信号周期', proximity_ticks: '接近阈值（跳）',
  band_turn_confirm_bars: '轨道转向确认根数', band_turn_min_change_ticks: '轨道最小变化（跳）', boll_period: '布林带周期',
  boll_stddev_milli: '布林带标准差倍数（千分位）', contraction_confirm_bars: '收口确认根数', contraction_min_width_drop_ticks: '收口最小缩窄（跳）',
  effective_reentry_bars: '有效重返确认根数', failed_reentry_confirm_bars: '重返失败确认根数', level_mapping_profile_id: '级别映射配置编号',
  observation_timeframe_minutes: '观察周期（分钟）', session_profile_id: '交易时段配置编号', capacity_lookback_bars: '容量回看根数',
  maximum_rotation_candidates: '最多轮动候选数', minimum_average_volume: '最小平均成交量', minimum_sector_coverage_milli: '最小板块覆盖率（千分位）',
  episode_start_bar_index: '观察起点 K 线编号', observation_direction: '观察方向', pressure_confirmation_bars: '压制确认根数',
  ma_period_1: '均线周期 1', ma_period_2: '均线周期 2', ma_period_3: '均线周期 3', ma_period_4: '均线周期 4',
  ma_period_5: '均线周期 5', ma_period_6: '均线周期 6', ma_period_7: '均线周期 7', ma_period_8: '均线周期 8',
  event_risk_max_position_weight_ppm: '事件风险最大仓位（百万分比）', kill_switch_on_data_revision: '数据修订变化时熔断', leverage_allowed: '允许杠杆',
  leverage_approval_id: '杠杆审批编号', max_daily_loss_ppm: '最大日亏损（百万分比）', max_data_gap_bars: '最大数据缺口根数',
  max_open_signal_age_bars: '开仓信号最长有效根数', max_order_loss_weight_ppm: '单笔最大损失权重（百万分比）',
  max_order_participation_ppm: '最大成交量参与率（百万分比）', max_position_weight_ppm: '单标的最大仓位（百万分比）',
  max_sector_weight_ppm: '板块最大仓位（百万分比）', max_stale_bars: '最大陈旧数据根数', max_strategy_drawdown_ppm: '策略最大回撤（百万分比）',
  stress_loss_per_contract_i64: '每手压力损失',
}

const statusLabels: Record<string, string> = {
  idle: '等待开始', queued: '排队中', running: '回测中', completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断', cancelling: '取消中',
}

function parameterLabel(name: string): string {
  return parameterLabels[name] ?? '策略参数'
}

interface StoredBacktestRun {
  dataset_id: string
  data_revision: string
  run_id: string
  run_signature: string
  algorithm_id: string
}

function tradeExecutionMarkers(rows: BacktestTrade[]): Array<Record<string, unknown> & { object_type: string; object_id: string }> {
  return rows.flatMap((trade) => {
    const openAction = trade.side === 'long' ? 'open_long' : 'open_short'
    const closeAction = trade.side === 'long' ? 'close_long' : 'close_short'
    const detail = `${trade.quantity} 手 · ${trade.trade_id}`
    return [
      {
        object_type: 'chart_event', object_id: `${trade.trade_id}:entry`, event_type: openAction, action: openAction,
        bar_index: trade.entry_bar_index, known_at_bar_index: trade.entry_bar_index,
        timestamp_utc: trade.entry_time, price_i64: trade.entry_price_i64,
        display_label: trade.side === 'long' ? '成交·开多' : '成交·开空', classification_detail: detail,
        execution_fact: true,
      },
      {
        object_type: 'chart_event', object_id: `${trade.trade_id}:exit`, event_type: closeAction, action: closeAction,
        bar_index: trade.exit_bar_index, known_at_bar_index: trade.exit_bar_index,
        timestamp_utc: trade.exit_time, price_i64: trade.exit_price_i64,
        display_label: trade.side === 'long' ? '成交·平多' : '成交·平空', classification_detail: detail,
        execution_fact: true,
      },
    ]
  })
}
const auxiliaryOnly = computed(() => strategy.value?.algorithm_id.startsWith('aux_') ?? false)
const executionSummary = computed(() => {
  const facts = executionFacts.value
  if (!facts) return ''
  if (facts.semantic_version !== '1.0.0') return '执行语义：未版本化旧结果（仅按原始 manifest 解释）'
  const commission = facts.commission as Record<string, unknown> | undefined
  const slippage = facts.slippage as Record<string, unknown> | undefined
  const multiplierSource = facts.contract_multiplier_source === 'instrument_config' ? '品种配置' : '来源未知'
  const slippageMode = slippage?.mode === 'ticks' ? '跳' : ''
  return `执行语义 v${facts.semantic_version} · 合约乘数 ${facts.contract_multiplier ?? '—'}（${multiplierSource}） · 手续费 ${commission?.amount_i64 ?? commission?.rate ?? '—'} · 滑点 ${slippage?.value ?? '—'} ${slippageMode}`
})
const rankingOnly = computed(() => strategy.value?.algorithm_id === 'aux_ma_sector_rotation')
const daily30mProfileIssue = computed(() => {
  if (strategy.value?.algorithm_id !== 'aux_daily_30m_classification' || !props.dataset) return ''
  if (timeframeMinutes(props.dataset.timeframe) !== 30) return '该经验算法只接受原课的30分钟数据，不聚合其他周期。'
  if (props.dataset.source.timestamp_semantics !== 'bar_end') return '该经验算法要求 bar_end 时间戳。'
  if (props.dataset.time.date_semantics !== 'trading_day') return '该经验算法要求 trading_day 日期语义。'
  if (props.dataset.time.timezone !== 'Asia/Shanghai') return '该经验算法要求 Asia/Shanghai 会话。'
  return ''
})
const rankingContext = computed<RankingContext | null>(() => {
  if (!rankingOnly.value) return null
  try {
    const value = JSON.parse(rankingContextText.value) as RankingContext
    return value && typeof value === 'object' ? value : null
  }
  catch { return null }
})
const rankingContextIssue = computed(() => {
  if (!rankingOnly.value || !props.dataset) return ''
  if (props.dataset.timeframe !== '1d') return '均线等级与板块轮动只接受显式复权的 1d 数据，不聚合当前周期。'
  if (props.dataset.source.timestamp_semantics !== 'bar_end') return '均线等级与板块轮动要求 bar_end 时间戳。'
  if (props.dataset.time.date_semantics !== 'trading_day') return '均线等级与板块轮动要求 trading_day 日期语义。'
  if (props.dataset.time.timezone !== 'Asia/Shanghai') return '均线等级与板块轮动要求 Asia/Shanghai 时区。'
  const context = rankingContext.value
  if (!context) return '请填写有效的点时宇宙 JSON。'
  if (context.membership_mode !== 'point_in_time') return '成员模式必须为 point_in_time。'
  if (!Array.isArray(context.memberships) || new Set(context.memberships.map((item) => item.dataset_id)).size < 2) return '点时宇宙至少需要两个不同数据集。'
  if (!context.memberships.some((item) => item.dataset_id === props.dataset?.dataset_id && item.data_revision === props.dataset?.data_revision)) return '当前图表数据集及 revision 必须属于点时宇宙。'
  if (context.episode_available_at_utc < context.episode_start_timestamp_utc) return 'episode 可用时间不能早于起点。'
  return ''
})
const riskContext = computed<RiskContext | null>(() => {
  if (!riskEnabled.value) return null
  try {
    const value = JSON.parse(riskContextText.value) as RiskContext
    return value && typeof value === 'object' ? value : null
  }
  catch { return null }
})
const riskContextIssue = computed(() => {
  if (!riskEnabled.value) return ''
  if (!riskFilter.value) return '统一风险覆盖算法不可用。'
  const context = riskContext.value
  if (!context) return '请填写有效的点时风险上下文 JSON。'
  if (!/^sha256:[0-9a-f]{64}$/.test(context.market_state_revision)) return '市场状态 revision 必须为 sha256。'
  if (!context.sector_id) return '风险上下文必须固定 sector_id。'
  if (!Array.isArray(context.legal_future_branches) || !Array.isArray(context.handled_future_branches) || !Array.isArray(context.observations)) return '分支能力和市场观察必须为数组。'
  if (new Set(context.legal_future_branches).size !== context.legal_future_branches.length || new Set(context.handled_future_branches).size !== context.handled_future_branches.length) return '分支能力列表不能重复。'
  let previous = -1
  for (const observation of context.observations) {
    if (observation.effective_from_bar_index <= previous || observation.available_at_bar_index > observation.effective_from_bar_index) return '市场观察必须严格排序，且可用时点不能晚于生效时点。'
    if (!/^sha256:[0-9a-f]{64}$/.test(observation.data_revision)) return '市场观察 data_revision 必须为 sha256。'
    previous = observation.effective_from_bar_index
  }
  if (riskParameters.value.leverage_allowed === true && !String(riskParameters.value.leverage_approval_id ?? '').trim()) return '允许杠杆时必须填写独立审批 ID。'
  if (Number(riskParameters.value.event_risk_max_position_weight_ppm) > Number(riskParameters.value.max_position_weight_ppm)) return '事件风险仓位上限不能高于普通单标的上限。'
  return ''
})
const algorithmContextIssue = computed(() => daily30mProfileIssue.value || rankingContextIssue.value || riskContextIssue.value)

const points = computed(() => {
  if (equity.value.length < 2) return ''
  const values = equity.value.map((row) => row.equity_i64)
  const low = Math.min(...values)
  const high = Math.max(...values)
  const span = Math.max(1, high - low)
  return values.map((value, index) => `${index / (values.length - 1) * 600},${100 - (value - low) / span * 90}`).join(' ')
})

function readStoredRun(dataset: DatasetMeta): StoredBacktestRun | null {
  try {
    const raw = window.localStorage.getItem(LAST_RUN_STORAGE_KEY)
    if (!raw) return null
    const value = JSON.parse(raw) as Partial<StoredBacktestRun>
    if (value.dataset_id !== dataset.dataset_id || value.data_revision !== dataset.data_revision) return null
    if (![value.run_id, value.run_signature, value.algorithm_id].every((item) => typeof item === 'string' && item.length > 0)) return null
    return value as StoredBacktestRun
  }
  catch { return null }
}

function storeRun(value: StoredBacktestRun): void {
  try { window.localStorage.setItem(LAST_RUN_STORAGE_KEY, JSON.stringify(value)) }
  catch { /* 浏览器禁用持久化时不影响正式回测。 */ }
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds))
}

async function getAllTrades(id: string): Promise<BacktestTrade[]> {
  const rows: BacktestTrade[] = []
  const seen = new Set<string>()
  let cursor: string | undefined
  do {
    const page = await getBacktestTrades(id, cursor)
    rows.push(...page.rows)
    if (!page.next_cursor) break
    if (seen.has(page.next_cursor)) throw new Error('交易分页游标重复')
    seen.add(page.next_cursor)
    cursor = page.next_cursor
  } while (cursor)
  return rows
}

function isTransientNetworkError(cause: unknown): boolean {
  return cause instanceof TypeError
    && /fetch|network|load/i.test(cause.message)
}

async function getBacktestWithRetry(id: string): ReturnType<typeof getBacktest> {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await getBacktest(id)
    }
    catch (cause) {
      const retryDelay = STATUS_POLL_RETRY_DELAYS_MS[attempt]
      if (!isTransientNetworkError(cause) || retryDelay === undefined) throw cause
      await wait(retryDelay)
    }
  }
}

async function execute(resume: StoredBacktestRun | null = null): Promise<void> {
  const dataset = props.dataset
  const definition = resume
    ? strategies.value.find((candidate) => candidate.algorithm_id === resume.algorithm_id) ?? null
    : strategy.value
  if (!dataset || !definition) return
  status.value = 'queued'
  error.value = ''
  summary.value = null
  executionFacts.value = null
  restored.value = false
  try {
    const parameters = { ...strategyParameters.value }
    const ranking = rankingOnly.value ? rankingContext.value : null
    if (rankingOnly.value && !ranking) throw new Error('点时宇宙 JSON 无效')
    const riskContextValue = riskEnabled.value ? riskContext.value : null
    if (riskEnabled.value && (!riskFilter.value || !riskContextValue)) throw new Error('风险覆盖配置无效')
    const risk = riskFilter.value && riskContextValue ? {
      algorithm: {
        kind: riskFilter.value.kind, algorithm_id: riskFilter.value.algorithm_id,
        algorithm_version: riskFilter.value.algorithm_version, source_hash: riskFilter.value.source_hash,
      },
      parameters: { ...riskParameters.value }, context: riskContextValue,
    } : null
    let current
    if (resume) {
      runId.value = resume.run_id
      signature.value = resume.run_signature
      current = await getBacktestWithRetry(resume.run_id)
      const manifestDataset = current.manifest?.dataset as Record<string, unknown> | undefined
      const manifestStrategy = current.manifest?.strategy as Record<string, unknown> | undefined
      if (current.run_signature !== resume.run_signature
        || manifestDataset?.dataset_id !== dataset.dataset_id
        || manifestDataset?.data_revision !== dataset.data_revision
        || manifestStrategy?.strategy_id !== definition.algorithm_id) {
        throw new Error('最近回测与当前数据集或策略不匹配')
      }
      restored.value = true
    }
    else {
      const accepted = await createBacktest({
        dataset_id: dataset.dataset_id, data_revision: dataset.data_revision,
        strategy: {
          kind: definition.kind, algorithm_id: definition.algorithm_id,
          algorithm_version: definition.algorithm_version, source_hash: definition.source_hash,
        },
        parameters,
        ...(ranking ? { ranking_context: ranking } : {}),
        ...(risk ? { risk_overlay: risk } : {}),
        range: {
          warmup_from_bar_index: dataset.coverage.first_bar_index,
          from_bar_index: dataset.coverage.first_bar_index,
          to_bar_index: dataset.coverage.last_bar_index,
        },
        execution: executionRequest({ commissionAmountI64: commission.value, marginRatio: marginRatio.value, contractMultiplier: dataset.instrument.contract_multiplier }),
        capital: capitalConfig(initialCash.value),
        random_seed: 20260801,
      })
      runId.value = accepted.run_id
      signature.value = accepted.run_signature
      storeRun({
        dataset_id: dataset.dataset_id, data_revision: dataset.data_revision,
        run_id: accepted.run_id, run_signature: accepted.run_signature,
        algorithm_id: definition.algorithm_id,
      })
      current = await getBacktestWithRetry(accepted.run_id)
    }
    while (!['completed', 'failed', 'cancelled', 'interrupted'].includes(current.status)) {
      status.value = current.status
      await wait(STATUS_POLL_INTERVAL_MS)
      current = await getBacktestWithRetry(runId.value)
    }
    if (current.status !== 'completed') throw new Error(current.error?.message ?? `回测${current.status}`)
    status.value = 'completed'
    executionFacts.value = (current.manifest?.execution as Record<string, unknown> | undefined) ?? null
    const [summaryValue, tradeRows, equityValue, causalEvents] = await Promise.all([
      getBacktestSummary(runId.value), getAllTrades(runId.value), getBacktestEquity(runId.value), getBacktestChartEvents(runId.value),
    ])
    summary.value = summaryValue
    trades.value = tradeRows
    equity.value = equityValue
    const currentObjects = new Map<string, ChanTreeObject>()
    const currentSignals = new Map<string, Record<string, unknown> & { object_type: string; object_id: string }>()
    for (const event of causalEvents) {
      const key = `${event.object_type}:${event.object_id}`
      if (event.operation === 'delete') {
        currentObjects.delete(key)
        currentSignals.delete(key)
        continue
      }
      const payload = event.payload
      currentSignals.set(key, { ...payload, object_type: event.object_type, object_id: event.object_id })
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
        long_after_B2_strongest: '最强二买持多', long_after_B2_normal: '普通二买持多',
        long_after_B2_weakest: '弱二买减仓持多', holding_after_followthrough: '后继上升创新高',
        handed_off_B3_trend: '三买非背驰·移交趋势持有',
        exited_followthrough_failure: '后继上升未创新高·退出',
        exited_followthrough_divergence: '后继上升背驰·退出',
        exited_standard_sell_point: '标准卖点·退出', exited_B2_source_revision: '二买来源修订·退出',
        handoff_to_B3_trend: '移交三买趋势持有', followthrough_confirmed: '后继上升已确认',
        long_after_first_center_B3: '首中枢三买持多', long_after_late_center_B3: '后续中枢三买减仓持多',
        holding_after_B3_followthrough: '三买后继创新高·持有',
        holding_new_center_without_trend_divergence: '新中枢无趋势背驰·持有',
        B3_first_return_already_consumed: '三买首次回试资格已消费',
        B3_dependency_filtered: '三买依赖结构不完整·过滤',
        B3_concurrent_lower_priority_filtered: '并发低优先级三买·过滤', B3_risk_filtered: '三买风险过滤',
        exited_B3_followthrough_failure: '三买后继未创新高·退出',
        exited_B3_followthrough_divergence: '三买后继背驰·退出',
        exited_trend_divergence: '趋势顶背驰·退出', exited_on_S3: '标准三卖·退出',
        exited_return_into_source_center: '回拉进入来源中枢·退出',
        exited_B3_source_revision: '三买来源修订·退出',
        hold_after_B3: '三买后继创新高·继续持有', hold_new_center: '新中枢无背驰·继续持有',
        oscillation_ready: '活动中枢·震荡就绪',
        oscillation_long_strong: '中枢震荡强势持多', oscillation_long_neutral: '中枢震荡中性持多',
        oscillation_long_weak: '中枢震荡弱势持多', oscillation_short_strong: '中枢震荡强势持空',
        oscillation_short_neutral: '中枢震荡中性持空', oscillation_short_weak: '中枢震荡弱势持空',
        oscillation_flat_long_entry_filtered: '震荡买入已过滤', oscillation_flat_short_entry_filtered: '震荡卖出已过滤',
        oscillation_waiting_Zn_dependency: '震荡背驰等待 Zn',
        oscillation_stale_concurrent_divergence_filtered: '旧震荡背驰已过滤',
        oscillation_stopped_by_B3: '标准三买·停止震荡', oscillation_stopped_by_S3: '标准三卖·停止震荡',
        oscillation_stopped_by_center_change: '中枢变化·停止震荡', oscillation_stopped_by_new_center: '新中枢·停止旧震荡',
        oscillation_stopped_by_source_revision: '震荡来源修订·停止',
        swing_buy: '中枢震荡买入', swing_sell: '中枢震荡卖出',
        swing_buy_hold: '中枢底背驰·持多', swing_sell_hold: '中枢顶背驰·持空',
        swing_buy_filtered: '中枢震荡买入·已过滤', swing_sell_filtered: '中枢震荡卖出·已过滤',
        stop_oscillation: '停止中枢震荡', handoff_to_trend: '移交三类点趋势策略',
        same_level_long: '同级分解持多', same_level_short: '同级分解持空',
        same_level_long_hold: '同级分解继续持多', same_level_short_hold: '同级分解继续持空',
        same_level_buy_filtered: '同级分解买入已禁用', same_level_sell_filtered: '同级分解卖出已禁用',
        same_level_hold_up: '同级向上创新高·持有', same_level_hold_down: '同级向下创新低·持有',
        same_level_wait_new_structure: 'Ai+3破坏Ai极值·等待新结构',
        same_level_continue_original_center: 'Ai+3守住Ai极值·围绕原中枢',
        same_level_promotion_candidate: '高级别中枢候选·等待结构确认',
        same_level_promoted_waiting_sequence: '级别已提升·等待高级别序列',
        same_level_decomposition_reset: '同级分解修订·重置',
        same_level_buy: '同级分解买入', same_level_sell: '同级分解卖出',
        same_level_buy_hold: '同级分解买入方向持有', same_level_sell_hold: '同级分解卖出方向持有',
        same_level_hold: '同级分解创新极值·持有',
        wait_new_same_level_structure: '等待新同级结构', continue_original_center: '继续围绕原中枢',
        promote_level_candidate: '高级别中枢候选', promote_level: '确认提升操作级别',
        WAIT_LOW_TURN: '三层分类·等待低层转折', LOW_TURN_ACTIVE: '三层分类·低层转折生效',
        MID_THIRD_POINT: '三层分类·中层三类点', MID_CENTER_CONTINUE: '三层分类·中层中枢延续',
        HIGH_CHANGE_CANDIDATE: '三层分类·高层变化候选', THREE_LEVEL_CONTEXT_RESET: '三层分类·来源修订重置',
        wait_low_turn: '等待低层转折', low_turn_active: '低层转折·允许参与',
        low_turn_participation_blocked: '低层转折·禁止参与', participation_cap: '三层分类参与上限',
        mid_third_point: '中层三类点', mid_center_continue: '中层中枢延续',
        high_change_candidate: '高层变化候选', three_level_context_reset: '三层结构来源修订',
        TARGET_REBOUND_ACTIVE: '目标级别反弹·分段操作生效', TARGET_CALLBACK_ACTIVE: '目标级别回调·分段操作生效',
        FIRST_LEG_PARTIAL_TAKE_PROFIT: '首次次级别段完成·部分兑现', COUNTER_LEG_REENTERED: '首次反向段完成·回补',
        TARGET_CENTER_CONFIRMED: '目标级别首个中枢确认', WAIT_TREND_FOLLOWTHROUGH: '三买卖点确认·等待趋势跟随段',
        TREND_HANDOFF: '非背驰创新高低·移交趋势持有', SEGMENTED_OPERATION_EXITED: '分段操作退出',
        SEGMENTED_OPERATION_RESET: '分段操作来源修订·重置',
        rebound_started: '目标级别反弹开始', callback_started: '目标级别回调开始',
        partial_take_profit: '首次次级别段·部分兑现', reenter: '首次反向段·回补',
        target_center_confirmed: '目标级别首个中枢确认', trend_handoff_wait: '等待非背驰趋势跟随段',
        trend_handoff: '移交趋势持有', segmented_operation_exit: '分段操作退出',
        unfavorable_execution_exit: '成本后无正收益·退出', segmented_operation_dependency_exit: '对象链缺失·退出',
        segmented_operation_reset: '来源事实修订·重置',
        BOTTOM_BUILDING: '精确底部构造中', TOP_BUILDING: '精确顶部构造中',
        BOTTOM_RESULTING_CENTER_CONFIRMED: '底部首个结果中枢已确认',
        TOP_RESULTING_CENTER_CONFIRMED: '顶部首个结果中枢已确认',
        BOTTOM_BUILD_SUCCESS: '精确底部构造成功', TOP_BUILD_SUCCESS: '精确顶部构造成功',
        BOTTOM_BUILD_FAILED: '精确底部构造失败', TOP_BUILD_FAILED: '精确顶部构造失败',
        BOTTOM_TOP_CONSTRUCTION_RESET: '底顶构造来源修订·重置',
        COARSE_BOTTOM_BUILDING: '粗略底分型区间观察中', COARSE_TOP_BUILDING: '粗略顶分型区间观察中',
        COARSE_BOTTOM_BUILD_SUCCESS: '粗略底部构造成功', COARSE_TOP_BUILD_SUCCESS: '粗略顶部构造成功',
        COARSE_BOTTOM_BUILD_FAILED: '粗略底部构造失败', COARSE_TOP_BUILD_FAILED: '粗略顶部构造失败',
        COARSE_CONSTRUCTION_SUPERSEDED: '粗略分型区间已被取代', COARSE_CONSTRUCTION_RESET: '粗略分型来源修订·重置',
        bottom_building: '精确底部构造开始', top_building: '精确顶部构造开始',
        bottom_resulting_center: '底部首个结果中枢', top_resulting_center: '顶部首个结果中枢',
        bottom_build_success: '精确底部构造成功', top_build_success: '精确顶部构造成功',
        bottom_build_failure: '精确底部构造失败', top_build_failure: '精确顶部构造失败',
        coarse_bottom_zone: '粗略底分型区间', coarse_top_zone: '粗略顶分型区间',
        coarse_bottom_success: '粗略底部构造成功', coarse_top_success: '粗略顶部构造成功',
        coarse_bottom_failure: '粗略底部构造失败', coarse_top_failure: '粗略顶部构造失败',
        coarse_construction_superseded: '粗略构造被精确对象取代',
        coarse_construction_reset: '粗略构造来源修订',
        bottom_top_construction_handoff: '底顶连接走势·构造移交',
        bottom_top_construction_reset: '底顶构造来源修订',
        aux_flying_kiss: '辅助·飞吻', aux_lip_kiss: '辅助·唇吻', aux_wet_kiss: '辅助·湿吻',
        aux_legacy_B1_candidate: '辅助·旧一买候选（非标准）',
        aux_legacy_B2_candidate: '辅助·旧二买候选（非标准）',
        aux_macd_risk_off: '辅助·MACD零轴下防守',
        aux_macd_risk_on_candidate: '辅助·MACD重新站稳候选',
        aux_boll_superstrong_exit: '辅助·BOLL超强区退出/中阴候选',
        aux_boll_second_buy_zone: '辅助·BOLL二买支撑区域',
        aux_boll_second_sell_zone: '辅助·BOLL二卖阻力区域',
        aux_boll_bardo_end_or_promotion_warning: '辅助·BOLL中阴结束或升级预警',
        aux_daily_30m_classification: '经验·8根30分钟日内分类',
        aux_daily_30m_profile_rejected: '经验·日内会话profile不匹配',
        aux_ma_strength_class: '经验·标的均线等级',
        aux_sector_strength_mean: '经验·板块平均等级',
        aux_rotation_candidate: '经验·板块轮动候选',
        approved_order_intent: '风控·订单意图批准', reduced_order_intent: '风控·订单意图降仓',
        blocked_decision: '风控·策略决策阻断', kill_switch: '风控·熔断',
        reduce_long: '部分平多', reduce_short: '部分平空', add_long: '回补多头', add_short: '回补空头',
        open_long: '开多', close_long: '平多', open_short: '开空', close_short: '平空',
      }
      const chartDatasetId = typeof payload.chart_dataset_id === 'string' ? payload.chart_dataset_id : null
      if (payload.event_type === 'aux_sector_strength_mean' || chartDatasetId && chartDatasetId !== dataset.dataset_id) continue
      currentObjects.set(key, {
        object_id: event.object_id,
        bar_index: Number(payload.bar_index ?? event.known_at_bar_index),
        time: Number(payload.timestamp_utc ?? 0),
        price_i64: Number(payload.price_i64 ?? 0),
        confirmed_at_bar_index: event.known_at_bar_index,
        known_at_bar_index: event.known_at_bar_index,
        object_revision: event.object_revision,
        label: String(payload.display_label ?? labels[state] ?? state),
        detail: String(payload.classification_detail ?? payload.reason_code ?? event.object_type),
      })
    }
    if (!auxiliaryOnly.value) {
      for (const marker of tradeExecutionMarkers(trades.value)) {
        currentSignals.set(`${marker.object_type}:${marker.object_id}`, marker)
      }
    }
    emit('completed', {
      source_type: 'StrategyRunSource', source_id: `run-source-${runId.value}`, run_id: runId.value,
      definition, status: 'completed', visible: true, objects: [...currentObjects.values()],
      signals: [...currentSignals.values()],
    })
  } catch (cause) {
    status.value = 'failed'
    error.value = cause instanceof Error ? cause.message : '回测失败'
  }
}

async function run(): Promise<void> {
  await execute()
}

function formatTradeTime(timestamp: number): string {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(timestamp))
}

function focusTrade(trade: BacktestTrade): void {
  selectedTradeId.value = trade.trade_id
  emit('focus-trade', trade)
}

function resizeTradePane(event: PointerEvent): void {
  if (props.view !== 'workspace' || !panelRoot.value) return
  const total = panelRoot.value.getBoundingClientRect().height
  if (total <= 0) return
  const startY = event.clientY
  const initial = tradePane.value?.getBoundingClientRect().height || total / 2
  const move = (next: PointerEvent) => {
    tradePaneHeight.value = Math.max(total / 2, Math.min(total - 120, initial + startY - next.clientY))
  }
  const finish = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', finish)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', finish)
}

function resetTradePane(): void {
  tradePaneHeight.value = null
}

async function restoreForDataset(dataset: DatasetMeta | null): Promise<void> {
  if (!dataset || strategies.value.length === 0) return
  const key = `${dataset.dataset_id}:${dataset.data_revision}`
  if (restoreAttemptKey.value === key) return
  restoreAttemptKey.value = key
  const stored = readStoredRun(dataset)
  if (stored) await execute(stored)
}

onMounted(async () => {
  try {
    const definitions = await listAlgorithms()
    strategies.value = definitions.filter((value) => value.kind === 'strategy')
    riskFilters.value = definitions.filter((value) => value.kind === 'risk_filter')
    strategy.value = strategies.value[0] ?? null
    riskFilter.value = riskFilters.value.find((value) => value.algorithm_id === 'unified_risk_execution_overlay') ?? riskFilters.value[0] ?? null
    await restoreForDataset(props.dataset)
  }
  catch (cause) { error.value = cause instanceof Error ? cause.message : '策略不可用' }
})

watch(() => props.dataset, (dataset) => { void restoreForDataset(dataset) })

function timeframeMinutes(value: string | undefined): number | null {
  const match = value?.match(/^([1-9][0-9]*)(m|h|d)$/)
  if (!match) return null
  const amount = Number(match[1])
  return amount * ({ m: 1, h: 60, d: 1440 }[match[2] as 'm' | 'h' | 'd'])
}

watch([strategy, () => props.dataset], ([definition, dataset]) => {
  if (!definition) {
    strategyParameters.value = {}
    return
  }
  const parameters = Object.fromEntries(
    Object.entries(definition.parameter_schema.properties)
      .map(([name, rule]) => [name, rule.default ?? '']),
  )
  if ('minimum_timeframe_minutes' in parameters) {
    parameters.minimum_timeframe_minutes = timeframeMinutes(dataset?.timeframe) ?? parameters.minimum_timeframe_minutes
  }
  if ('observation_timeframe_minutes' in parameters) {
    parameters.observation_timeframe_minutes = timeframeMinutes(dataset?.timeframe) ?? parameters.observation_timeframe_minutes
  }
  if (definition.algorithm_id === 'aux_ma_sector_rotation' && dataset) {
    rankingContextText.value = JSON.stringify({
      universe_id: 'replace-with-point-in-time-universe',
      membership_revision: `sha256:${'0'.repeat(64)}`,
      membership_mode: 'point_in_time',
      price_adjustment_mode: 'forward_adjusted',
      price_adjustment_revision: `sha256:${'0'.repeat(64)}`,
      episode_id: 'replace-with-rebound-episode',
      episode_start_timestamp_utc: dataset.coverage.first_timestamp_utc,
      episode_available_at_utc: dataset.coverage.first_timestamp_utc,
      memberships: [
        {
          dataset_id: dataset.dataset_id, data_revision: dataset.data_revision, sector_id: 'replace-sector',
          effective_from_utc: dataset.coverage.first_timestamp_utc, effective_to_utc: null,
          available_at_utc: dataset.coverage.first_timestamp_utc,
        },
        {
          dataset_id: 'replace-second-dataset-id', data_revision: `sha256:${'0'.repeat(64)}`, sector_id: 'replace-sector',
          effective_from_utc: dataset.coverage.first_timestamp_utc, effective_to_utc: null,
          available_at_utc: dataset.coverage.first_timestamp_utc,
        },
      ],
    }, null, 2)
  }
  strategyParameters.value = parameters
})

watch([riskFilter, () => props.dataset], ([definition, dataset]) => {
  riskParameters.value = definition ? Object.fromEntries(
    Object.entries(definition.parameter_schema.properties).map(([name, rule]) => [name, rule.default ?? '']),
  ) : {}
  if (!dataset) {
    riskContextText.value = ''
    return
  }
  riskContextText.value = JSON.stringify({
    market_state_revision: dataset.data_revision,
    sector_id: dataset.instrument?.product || dataset.dataset_id,
    legal_future_branches: [], handled_future_branches: [], observations: [],
  } satisfies RiskContext, null, 2)
}, { immediate: true })
</script>

<template>
  <section ref="panelRoot" class="backtest-panel" :class="{ 'backtest-workspace': view === 'workspace' }" aria-label="回测结果">
    <div :class="{ 'backtest-configuration-pane': view === 'workspace' }">
      <div class="backtest-controls">
      <select v-model="strategy" aria-label="选择回测策略">
        <option v-for="candidate in strategies" :key="candidate.algorithm_id" :value="candidate">{{ candidate.name }}</option>
      </select>
      <label v-for="(rule, name) in strategy?.parameter_schema.properties" :key="name" :title="name">
        {{ parameterLabel(name) }}
        <input v-if="rule.type === 'boolean'" v-model="strategyParameters[name]" type="checkbox" />
        <input v-else-if="rule.type === 'string'" v-model="strategyParameters[name]" type="text" />
        <input v-else v-model.number="strategyParameters[name]" type="number" :min="rule.minimum" :max="rule.maximum" />
      </label>
      <strong>{{ strategy?.name ?? '正在加载策略…' }}</strong>
      <label v-if="rankingOnly" class="ranking-context-field">
        点时宇宙与复权上下文 JSON
        <textarea v-model="rankingContextText" rows="10" spellcheck="false" />
      </label>
      <label class="risk-enable"><input v-model="riskEnabled" type="checkbox" /> 启用统一风险与执行覆盖层</label>
      <details v-if="riskEnabled" class="risk-overlay-controls" open>
        <summary>{{ riskFilter?.name ?? '风险覆盖算法不可用' }}</summary>
        <label v-for="(rule, name) in riskFilter?.parameter_schema.properties" :key="`risk-${name}`" :title="name">
          {{ parameterLabel(name) }}
          <input v-if="rule.type === 'boolean'" v-model="riskParameters[name]" type="checkbox" />
          <input v-else-if="rule.type === 'string'" v-model="riskParameters[name]" type="text" />
          <input v-else v-model.number="riskParameters[name]" type="number" :min="rule.minimum" :max="rule.maximum" />
        </label>
        <label class="ranking-context-field">
          点时分支能力与市场状态 JSON
          <textarea v-model="riskContextText" rows="8" spellcheck="false" />
        </label>
        <small>未处理的合法分支会形成可审计阻断；观察只在 available_at_bar_index 之后生效。</small>
      </details>
      <label>初始资金 <input v-model.number="initialCash" type="number" min="0" /></label>
      <label>每手手续费 <input v-model.number="commission" type="number" min="0" /></label>
      <label>合约乘数 <output>{{ dataset?.instrument.contract_multiplier ?? '—' }}</output></label>
      <label>保证金 <input v-model.number="marginRatio" type="number" min="0.01" max="1" step="0.01" /></label>
      <button
        class="backtest-run-button" :class="{ 'is-running': executing }" :aria-busy="executing"
        :disabled="!dataset || !strategy || Boolean(algorithmContextIssue) || executing" @click="run"
      >{{ auxiliaryOnly ? '生成辅助事件（不交易）' : '开始正式回测' }}</button>
      <span>{{ statusLabels[status] ?? status }} <small v-if="restored">· 已恢复最近结果</small> <small v-if="runId">{{ runId }} · {{ signature.slice(0, 18) }}</small></span>
      <span v-if="algorithmContextIssue" class="issue">{{ algorithmContextIssue }}</span>
      <span v-if="error" class="issue">{{ error }}</span>
      </div>
      <div v-if="view === 'backtest' || view === 'workspace'" class="summary-grid">
      <span v-if="executionSummary" class="execution-summary">{{ executionSummary }}</span>
      <template v-if="summary">
        <span>总收益 {{ (summary.total_return * 100).toFixed(2) }}%</span>
        <span>最大回撤 {{ (summary.max_drawdown * 100).toFixed(2) }}%</span>
        <span>交易 {{ summary.trade_count }}</span>
        <span>胜率 {{ summary.win_rate === null ? '—' : `${(summary.win_rate * 100).toFixed(1)}%` }}</span>
        <span>Sharpe {{ summary.sharpe?.toFixed(2) ?? '—' }}</span>
        <span>手续费 {{ summary.total_commission_i64 }}</span>
        <span>风控批准 {{ summary.risk_approved_count }}</span>
        <span>风控降仓 {{ summary.risk_reduced_count }}</span>
        <span>风控阻断 {{ summary.risk_blocked_count }}</span>
        <span>风险熔断 {{ summary.risk_kill_switch_count }}</span>
        <span v-if="summary.trade_count === 0" class="issue">本次没有成交，因此图上没有开平仓标记。</span>
      </template>
      </div>
    </div>
    <button
      v-if="view === 'workspace'" class="backtest-pane-splitter" aria-label="调整交易明细高度"
      @pointerdown="resizeTradePane" @dblclick="resetTradePane"
    />
    <section
      v-if="view === 'workspace'" ref="tradePane" class="backtest-trade-pane"
      :style="{ height: tradePaneHeight === null ? '50%' : `${tradePaneHeight}px` }"
      aria-label="交易明细"
    >
      <header><strong>交易明细</strong><span>{{ trades.length }} 笔</span><small>双击交易定位入场 K 线</small></header>
      <div class="trade-table-scroll">
        <table class="trade-table detailed-trade-table">
          <thead><tr><th>#</th><th>ID</th><th>方向</th><th>手数</th><th>入场时间</th><th>入场 K</th><th>入场价</th><th>出场时间</th><th>出场 K</th><th>出场价</th><th>毛盈亏</th><th>手续费</th><th>滑点</th><th>净盈亏</th><th>结构归因</th></tr></thead>
          <tbody>
            <tr v-if="trades.length === 0"><td colspan="15">尚无交易。完成正式回测后，全部交易会显示在这里。</td></tr>
            <tr
              v-for="(trade, index) in trades" :key="trade.trade_id"
              :data-trade-id="trade.trade_id" :class="{ selected: selectedTradeId === trade.trade_id }" tabindex="0"
              @dblclick="focusTrade(trade)" @keydown.enter="focusTrade(trade)"
            >
              <td>{{ index + 1 }}</td><td>{{ trade.trade_id }}</td><td>{{ trade.side === 'long' ? '多' : '空' }}</td><td>{{ trade.quantity }}</td>
              <td>{{ formatTradeTime(trade.entry_time) }}</td><td>{{ trade.entry_bar_index }}</td><td>{{ trade.entry_price_i64 }}</td>
              <td>{{ formatTradeTime(trade.exit_time) }}</td><td>{{ trade.exit_bar_index }}</td><td>{{ trade.exit_price_i64 }}</td>
              <td>{{ trade.gross_pnl_i64 }}</td><td>{{ trade.commission_i64 }}</td><td>{{ trade.slippage_i64 }}</td>
              <td :class="trade.net_pnl_i64 >= 0 ? 'profit' : 'loss'">{{ trade.net_pnl_i64 }}</td>
              <td>{{ trade.trigger_category ?? '—' }} · {{ trade.attribution_reason_code ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
    <table v-if="view === 'trades'" class="trade-table">
      <thead><tr><th>ID</th><th>方向</th><th>入场</th><th>出场</th><th>净盈亏</th></tr></thead>
      <tbody>
        <tr v-if="trades.length === 0"><td colspan="5">本次没有成交，主图不会显示开平仓标记。</td></tr>
        <tr v-for="trade in trades" :key="trade.trade_id" @dblclick="focusTrade(trade)"><td>{{ trade.trade_id }}</td><td>{{ trade.side }}</td><td>{{ trade.entry_bar_index }} @ {{ trade.entry_price_i64 }}</td><td>{{ trade.exit_bar_index }} @ {{ trade.exit_price_i64 }}</td><td>{{ trade.net_pnl_i64 }}</td></tr>
      </tbody>
    </table>
    <svg v-if="view === 'equity'" class="equity-chart" viewBox="0 0 600 110" preserveAspectRatio="none" aria-label="权益曲线"><polyline :points="points" /></svg>
  </section>
</template>
