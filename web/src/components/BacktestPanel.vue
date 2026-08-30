<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { createBacktest, getBacktest, getBacktestChartEvents, getBacktestEquity, getBacktestSummary, getBacktestTrades, listAlgorithms } from '../api/client'
import { capitalConfig, executionRequest } from '../execution/config'
import type { AlgorithmDefinition, BacktestSummary, BacktestTrade, ChanTreeObject, DatasetMeta, EquityRow, RankingContext, RiskContext, StrategyRunSource } from '../types/api'

const props = defineProps<{ dataset: DatasetMeta | null; view: 'backtest' | 'trades' | 'equity' }>()
const emit = defineEmits<{ completed: [source: StrategyRunSource] }>()
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

interface StoredBacktestRun {
  dataset_id: string
  data_revision: string
  run_id: string
  run_signature: string
  algorithm_id: string
}
const auxiliaryOnly = computed(() => strategy.value?.algorithm_id.startsWith('aux_') ?? false)
const executionSummary = computed(() => {
  const facts = executionFacts.value
  if (!facts) return ''
  if (facts.semantic_version !== '1.0.0') return '执行语义：未版本化旧结果（仅按原始 manifest 解释）'
  const commission = facts.commission as Record<string, unknown> | undefined
  const slippage = facts.slippage as Record<string, unknown> | undefined
  return `执行语义 v${facts.semantic_version} · 合约乘数 ${facts.contract_multiplier ?? '—'}（${facts.contract_multiplier_source ?? '来源未知'}） · 手续费 ${commission?.amount_i64 ?? commission?.rate ?? '—'} · 滑点 ${slippage?.value ?? '—'} ${slippage?.mode ?? ''}`
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
      current = await getBacktest(resume.run_id)
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
          from_bar_index: Math.max(dataset.coverage.first_bar_index, dataset.coverage.last_bar_index - 2999),
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
      current = await getBacktest(accepted.run_id)
    }
    while (!['completed', 'failed', 'cancelled', 'interrupted'].includes(current.status)) {
      status.value = current.status
      await new Promise((resolve) => window.setTimeout(resolve, 250))
      current = await getBacktest(runId.value)
    }
    if (current.status !== 'completed') throw new Error(current.error?.message ?? `回测${current.status}`)
    status.value = 'completed'
    executionFacts.value = (current.manifest?.execution as Record<string, unknown> | undefined) ?? null
    const [summaryValue, tradeValue, equityValue, causalEvents] = await Promise.all([
      getBacktestSummary(runId.value), getBacktestTrades(runId.value), getBacktestEquity(runId.value), getBacktestChartEvents(runId.value),
    ])
    summary.value = summaryValue
    trades.value = tradeValue.rows
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
  if (definition.algorithm_id === 'aux_macd_zero_axis_defense') {
    parameters.minimum_timeframe_minutes = timeframeMinutes(dataset?.timeframe) ?? parameters.minimum_timeframe_minutes
  }
  if (definition.algorithm_id === 'aux_boll_bardo_warning') {
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
  <section class="backtest-panel" aria-label="回测结果">
    <div class="backtest-controls">
      <select v-model="strategy" aria-label="选择回测策略">
        <option v-for="candidate in strategies" :key="candidate.algorithm_id" :value="candidate">{{ candidate.name }}</option>
      </select>
      <label v-for="(rule, name) in strategy?.parameter_schema.properties" :key="name">
        {{ name }}
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
        <label v-for="(rule, name) in riskFilter?.parameter_schema.properties" :key="`risk-${name}`">
          {{ name }}
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
      <button :disabled="!dataset || !strategy || Boolean(algorithmContextIssue) || ['queued', 'running'].includes(status)" @click="run">{{ auxiliaryOnly ? '生成辅助事件（不交易）' : '开始正式回测' }}</button>
      <span>{{ status }} <small v-if="restored">· 已恢复最近结果</small> <small v-if="runId">{{ runId }} · {{ signature.slice(0, 18) }}</small></span>
      <span v-if="algorithmContextIssue" class="issue">{{ algorithmContextIssue }}</span>
      <span v-if="error" class="issue">{{ error }}</span>
    </div>
    <div v-if="view === 'backtest'" class="summary-grid">
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
      </template>
    </div>
    <table v-else-if="view === 'trades'" class="trade-table">
      <thead><tr><th>ID</th><th>方向</th><th>入场</th><th>出场</th><th>净盈亏</th></tr></thead>
      <tbody><tr v-for="trade in trades" :key="trade.trade_id"><td>{{ trade.trade_id }}</td><td>{{ trade.side }}</td><td>{{ trade.entry_bar_index }} @ {{ trade.entry_price_i64 }}</td><td>{{ trade.exit_bar_index }} @ {{ trade.exit_price_i64 }}</td><td>{{ trade.net_pnl_i64 }}</td></tr></tbody>
    </table>
    <svg v-else class="equity-chart" viewBox="0 0 600 110" preserveAspectRatio="none" aria-label="权益曲线"><polyline :points="points" /></svg>
  </section>
</template>
