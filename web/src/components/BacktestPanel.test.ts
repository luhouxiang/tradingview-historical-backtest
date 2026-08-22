import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DatasetMeta } from '../types/api'
import BacktestPanel from './BacktestPanel.vue'

const api = vi.hoisted(() => ({
  listAlgorithms: vi.fn(), createBacktest: vi.fn(), getBacktest: vi.fn(),
  getBacktestSummary: vi.fn(), getBacktestTrades: vi.fn(), getBacktestEquity: vi.fn(), getBacktestChartEvents: vi.fn(),
}))
vi.mock('../api/client', () => api)

const strategy = {
  kind: 'strategy', algorithm_id: 'ma20_retest_short', algorithm_version: '1.0.0', source_hash: `sha256:${'1'.repeat(64)}`,
  name: 'MA20 Retest Failure Short', parameter_schema: {
    properties: {
      ma_period: { default: 20 }, touch_tolerance_ticks: { default: 1 }, max_retest_bars: { default: 20 },
    },
  },
}
const secondBuyStrategy = {
  kind: 'strategy', algorithm_id: 'second_buy_only', algorithm_version: '1.1.0', source_hash: `sha256:${'4'.repeat(64)}`,
  name: '只做第二类买点', parameter_schema: {
    properties: {
      checkpoint_interval: { type: 'integer', minimum: 64, maximum: 100_000, default: 1024 },
      allow_strongest: { type: 'boolean', default: true }, allow_normal: { type: 'boolean', default: true },
      allow_weakest: { type: 'boolean', default: true }, strongest_quantity: { type: 'integer', minimum: 1, maximum: 100, default: 2 },
      normal_quantity: { type: 'integer', minimum: 1, maximum: 100, default: 2 },
      weakest_quantity: { type: 'integer', minimum: 1, maximum: 100, default: 1 },
    },
  },
}
const thirdBuyStrategy = {
  kind: 'strategy', algorithm_id: 'third_buy_only', algorithm_version: '1.1.0', source_hash: `sha256:${'5'.repeat(64)}`,
  name: '只做第三类买点', parameter_schema: {
    properties: {
      checkpoint_interval: { type: 'integer', minimum: 64, maximum: 100_000, default: 1024 },
      allow_late_center: { type: 'boolean', default: true },
      first_center_quantity: { type: 'integer', minimum: 1, maximum: 100, default: 2 },
      late_center_quantity: { type: 'integer', minimum: 1, maximum: 100, default: 1 },
      minimum_entry_volume: { type: 'integer', minimum: 0, default: 0 },
    },
  },
}
const oscillationStrategy = {
  kind: 'strategy', algorithm_id: 'centre_oscillation_spread', algorithm_version: '1.0.0', source_hash: `sha256:${'6'.repeat(64)}`,
  name: '中枢震荡差价', parameter_schema: {
    properties: {
      checkpoint_interval: { type: 'integer', minimum: 64, maximum: 100_000, default: 1024 },
      allow_long: { type: 'boolean', default: true }, allow_short: { type: 'boolean', default: true },
      strong_quantity: { type: 'integer', minimum: 1, maximum: 100, default: 2 },
      neutral_quantity: { type: 'integer', minimum: 1, maximum: 100, default: 1 },
      weak_quantity: { type: 'integer', minimum: 1, maximum: 100, default: 1 },
      estimated_round_trip_cost_i64: { type: 'integer', minimum: 0, default: 0 },
      minimum_net_range_i64: { type: 'integer', minimum: 0, default: 1 },
      fast_execution_available: { type: 'boolean', default: false },
      max_entries_per_center: { type: 'integer', minimum: 1, maximum: 100, default: 4 },
    },
  },
}
const sameLevelStrategy = {
  kind: 'strategy', algorithm_id: 'same_level_decomposition_program', algorithm_version: '1.1.0', source_hash: `sha256:${'7'.repeat(64)}`,
  name: '同级别分解机械程序', parameter_schema: {
    properties: {
      checkpoint_interval: { type: 'integer', minimum: 64, maximum: 100_000, default: 1024 },
      odd_direction_is_down: { type: 'boolean', default: true },
      allow_long: { type: 'boolean', default: true }, allow_short: { type: 'boolean', default: true },
      operation_quantity: { type: 'integer', minimum: 1, maximum: 100, default: 1 },
    },
  },
}
const threeLevelStrategy = {
  kind: 'strategy', algorithm_id: 'three_level_complete_classification', algorithm_version: '1.0.0', source_hash: `sha256:${'8'.repeat(64)}`,
  name: '三层级完全分类', parameter_schema: {
    properties: {
      checkpoint_interval: { type: 'integer', minimum: 64, maximum: 100_000, default: 1024 },
      level_graph_profile_id: { type: 'integer', minimum: 1, maximum: 1, default: 1 },
      allow_long: { type: 'boolean', default: true }, allow_short: { type: 'boolean', default: true },
      operation_quantity: { type: 'integer', minimum: 1, maximum: 100, default: 1 },
      can_handle_mid_third_point: { type: 'boolean', default: true },
      can_handle_mid_center_continue: { type: 'boolean', default: true },
      can_handle_high_change_candidate: { type: 'boolean', default: true },
    },
  },
}
const segmentedStrategy = {
  kind: 'strategy', algorithm_id: 'target_level_rebound_segmented_operation', algorithm_version: '1.0.0', source_hash: `sha256:${'9'.repeat(64)}`,
  name: '目标级别反弹/回调分段操作', parameter_schema: {
    properties: {
      checkpoint_interval: { type: 'integer', minimum: 64, maximum: 100_000, default: 1024 },
      level_graph_profile_id: { type: 'integer', minimum: 1, maximum: 1, default: 1 },
      allow_long: { type: 'boolean', default: true }, allow_short: { type: 'boolean', default: true },
      operation_quantity: { type: 'integer', minimum: 2, maximum: 100, default: 2 },
      partial_take_profit_quantity: { type: 'integer', minimum: 1, maximum: 99, default: 1 },
      estimated_round_trip_cost_i64: { type: 'integer', minimum: 0, default: 0 },
      minimum_net_segment_i64: { type: 'integer', minimum: 0, default: 1 },
      execution_available: { type: 'boolean', default: true },
    },
  },
}
const constructionStrategy = {
  kind: 'strategy', algorithm_id: 'bottom_top_construction', algorithm_version: '1.0.0', source_hash: `sha256:${'a'.repeat(64)}`,
  name: '底部/顶部构造状态机', parameter_schema: {
    properties: {
      checkpoint_interval: { type: 'integer', minimum: 64, maximum: 100_000, default: 1024 },
      level_graph_profile_id: { type: 'integer', minimum: 1, maximum: 1, default: 1 },
      allow_long: { type: 'boolean', default: true }, allow_short: { type: 'boolean', default: true },
      operation_quantity: { type: 'integer', minimum: 1, maximum: 100, default: 1 },
      execution_available: { type: 'boolean', default: true },
      coarse_effective_hold_bars: { type: 'integer', minimum: 1, maximum: 20, default: 1 },
    },
  },
}
const auxiliaryMaKiss = {
  kind: 'strategy', algorithm_id: 'aux_ma_kiss_legacy', algorithm_version: '1.0.0', source_hash: `sha256:${'b'.repeat(64)}`,
  name: '辅助·均线“吻”旧系统（候选不交易）', parameter_schema: {
    properties: {
      short_period: { type: 'integer', minimum: 2, maximum: 500, default: 5 },
      long_period: { type: 'integer', minimum: 3, maximum: 1000, default: 10 },
      proximity_ticks: { type: 'integer', minimum: 0, maximum: 1000, default: 1 },
      flat_slope_ticks: { type: 'integer', minimum: 0, maximum: 1000, default: 1 },
      enable_legacy_b1_macd_proxy: { type: 'boolean', default: true },
      macd_fast_period: { type: 'integer', minimum: 1, maximum: 1000, default: 12 },
      macd_slow_period: { type: 'integer', minimum: 2, maximum: 1000, default: 26 },
      macd_signal_period: { type: 'integer', minimum: 1, maximum: 1000, default: 9 },
      legacy_divergence_min_bars: { type: 'integer', minimum: 1, maximum: 100, default: 1 },
    },
  },
}
const auxiliaryMacdDefense = {
  kind: 'strategy', algorithm_id: 'aux_macd_zero_axis_defense', algorithm_version: '1.0.0', source_hash: `sha256:${'c'.repeat(64)}`,
  name: '辅助·MACD零轴防守（风险开关不交易）', parameter_schema: {
    properties: {
      minimum_timeframe_minutes: { type: 'integer', minimum: 1, maximum: 43_200, default: 60 },
      fast_period: { type: 'integer', minimum: 1, maximum: 1000, default: 12 },
      slow_period: { type: 'integer', minimum: 2, maximum: 1000, default: 26 },
      signal_period: { type: 'integer', minimum: 1, maximum: 1000, default: 9 },
      zero_axis_buffer_ticks: { type: 'integer', minimum: 0, maximum: 1000, default: 0 },
      risk_off_confirm_bars: { type: 'integer', minimum: 1, maximum: 100, default: 1 },
      reclaim_confirm_bars: { type: 'integer', minimum: 1, maximum: 100, default: 2 },
    },
  },
}
const auxiliaryBollBardo = {
  kind: 'strategy', algorithm_id: 'aux_boll_bardo_warning', algorithm_version: '1.0.0', source_hash: `sha256:${'d'.repeat(64)}`,
  name: '辅助·BOLL中阴判断（预警不交易）', parameter_schema: {
    properties: {
      checkpoint_interval: { type: 'integer', minimum: 64, maximum: 100_000, default: 1024 },
      observation_timeframe_minutes: { type: 'integer', minimum: 1, maximum: 43_200, default: 30 },
      level_mapping_profile_id: { type: 'integer', minimum: 1, maximum: 1, default: 1 },
      boll_period: { type: 'integer', minimum: 2, maximum: 1000, default: 20 },
      boll_stddev_milli: { type: 'integer', minimum: 1, maximum: 100_000, default: 2000 },
      effective_reentry_bars: { type: 'integer', minimum: 1, maximum: 100, default: 2 },
      failed_reentry_confirm_bars: { type: 'integer', minimum: 1, maximum: 100, default: 2 },
      band_turn_confirm_bars: { type: 'integer', minimum: 1, maximum: 100, default: 1 },
      band_turn_min_change_ticks: { type: 'integer', minimum: 0, maximum: 1000, default: 0 },
      contraction_confirm_bars: { type: 'integer', minimum: 1, maximum: 100, default: 3 },
      contraction_min_width_drop_ticks: { type: 'integer', minimum: 0, maximum: 1000, default: 0 },
    },
  },
}
const auxiliaryDaily30m = {
  kind: 'strategy', algorithm_id: 'aux_daily_30m_classification', algorithm_version: '1.0.0', source_hash: `sha256:${'e'.repeat(64)}`,
  name: '经验·8根30分钟日内分类（不交易）', parameter_schema: {
    properties: {
      checkpoint_interval: { type: 'integer', minimum: 64, maximum: 100_000, default: 1024 },
      observation_timeframe_minutes: { type: 'integer', minimum: 30, maximum: 30, default: 30 },
      session_profile_id: { type: 'integer', minimum: 1, maximum: 1, default: 1 },
    },
  },
}
const auxiliaryMaSectorRotation = {
  kind: 'strategy', algorithm_id: 'aux_ma_sector_rotation', algorithm_version: '1.0.0', source_hash: `sha256:${'f'.repeat(64)}`,
  name: '经验·均线等级与板块轮动（不交易）', parameter_schema: {
    properties: {
      checkpoint_interval: { type: 'integer', minimum: 64, maximum: 100_000, default: 1024 },
      ma_period_1: { type: 'integer', default: 5 }, ma_period_2: { type: 'integer', default: 13 },
      ma_period_3: { type: 'integer', default: 21 }, ma_period_4: { type: 'integer', default: 34 },
      ma_period_5: { type: 'integer', default: 55 }, ma_period_6: { type: 'integer', default: 89 },
      ma_period_7: { type: 'integer', default: 144 }, ma_period_8: { type: 'integer', default: 233 },
      minimum_sector_coverage_milli: { type: 'integer', default: 800 },
      capacity_lookback_bars: { type: 'integer', default: 20 },
      minimum_average_volume: { type: 'integer', default: 0 },
      maximum_rotation_candidates: { type: 'integer', default: 20 },
    },
  },
}
const unifiedRiskOverlay = {
  kind: 'risk_filter', algorithm_id: 'unified_risk_execution_overlay', algorithm_version: '1.0.0', source_hash: `sha256:${'9'.repeat(64)}`,
  name: '统一风险与执行覆盖层', parameter_schema: {
    properties: {
      leverage_allowed: { type: 'boolean', default: false }, leverage_approval_id: { type: 'string', default: '' },
      max_position_weight_ppm: { type: 'integer', default: 100_000 }, max_sector_weight_ppm: { type: 'integer', default: 300_000 },
      max_order_loss_weight_ppm: { type: 'integer', default: 10_000 }, stress_loss_per_contract_i64: { type: 'integer', default: 100_000 },
      max_daily_loss_ppm: { type: 'integer', default: 20_000 }, max_strategy_drawdown_ppm: { type: 'integer', default: 150_000 },
      max_order_participation_ppm: { type: 'integer', default: 100_000 }, max_stale_bars: { type: 'integer', default: 0 },
      max_data_gap_bars: { type: 'integer', default: 0 }, max_open_signal_age_bars: { type: 'integer', default: 3 },
      event_risk_max_position_weight_ppm: { type: 'integer', default: 50_000 }, kill_switch_on_data_revision: { type: 'boolean', default: true },
    },
  },
}
const dataset = {
  dataset_id: 'TEST.A1.5m', data_revision: `sha256:${'2'.repeat(64)}`,
  timeframe: '5m', instrument: { exchange: 'TEST', symbol: 'A1', product: 'A' },
  coverage: { first_bar_index: 0, last_bar_index: 100 },
} as DatasetMeta
const daily30mDataset = {
  ...dataset,
  dataset_id: 'TEST.A1.30m',
  timeframe: '30m',
  source: { path: 'history/TEST.A1.30m.txt', encoding: 'utf-8', format: 'txt', timestamp_semantics: 'bar_end' },
  time: { timezone: 'Asia/Shanghai', date_semantics: 'trading_day' },
} as DatasetMeta
const rankingDataset = {
  ...daily30mDataset,
  dataset_id: 'SSE.600000.1d',
  data_revision: `sha256:${'6'.repeat(64)}`,
  timeframe: '1d',
  coverage: {
    ...dataset.coverage,
    first_timestamp_utc: 1_700_000_000_000,
    last_timestamp_utc: 1_725_920_000_000,
  },
} as DatasetMeta

describe('BacktestPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listAlgorithms.mockResolvedValue([strategy, secondBuyStrategy, thirdBuyStrategy, oscillationStrategy, sameLevelStrategy, threeLevelStrategy, segmentedStrategy, constructionStrategy, auxiliaryMaKiss, auxiliaryMacdDefense, auxiliaryBollBardo, auxiliaryDaily30m, auxiliaryMaSectorRotation, unifiedRiskOverlay])
    api.createBacktest.mockResolvedValue({ run_id: 'run-1', run_signature: `sha256:${'3'.repeat(64)}`, status: 'queued' })
    api.getBacktest.mockResolvedValue({ run_id: 'run-1', status: 'completed', progress: 1 })
    api.getBacktestSummary.mockResolvedValue({
      total_return: .1, max_drawdown: .02, trade_count: 2, win_rate: .5, sharpe: 1.2, total_commission_i64: 600,
      risk_approved_count: 1, risk_reduced_count: 0, risk_blocked_count: 0, risk_kill_switch_count: 0,
    })
    api.getBacktestTrades.mockResolvedValue({ rows: [{ trade_id: 'trade-1', side: 'short', entry_bar_index: 10, entry_price_i64: 100, exit_bar_index: 20, exit_price_i64: 90, net_pnl_i64: 10 }], next_cursor: null })
    api.getBacktestEquity.mockResolvedValue([{ bar_index: 0, equity_i64: 100 }, { bar_index: 1, equity_i64: 110 }])
    api.getBacktestChartEvents.mockResolvedValue([])
  })

  it('creates a formal run and renders summary, trades and equity views', async () => {
    api.getBacktestChartEvents.mockResolvedValue([
      {
        event_seq: 1, known_at_bar_index: 80, object_type: 'strategy_state', object_id: 'state-80',
        operation: 'upsert', object_revision: 1,
        payload: { state_to: 'above_with_B3', timestamp_utc: 1_700_000_000_000, price_i64: 2650, reason_code: 'CENTRE_STATE_ABOVE_WITH_B3' },
      },
      {
        event_seq: 2, known_at_bar_index: 81, object_type: 'risk_decision', object_id: 'risk-approved-81',
        operation: 'upsert', object_revision: 1,
        payload: { event_type: 'approved_order_intent', timestamp_utc: 1_700_000_300_000, price_i64: 2648, reason_code: 'WITHIN_RISK_LIMITS' },
      },
    ])
    const wrapper = mount(BacktestPanel, { props: { dataset, view: 'backtest' } })
    await flushPromises()
    await wrapper.get('.backtest-controls button').trigger('click')
    await flushPromises()
    expect(api.createBacktest).toHaveBeenCalledWith(expect.objectContaining({
      execution: expect.objectContaining({ fill_timing: 'next_bar_open' }),
      risk_overlay: expect.objectContaining({
        algorithm: expect.objectContaining({ kind: 'risk_filter', algorithm_id: 'unified_risk_execution_overlay' }),
        parameters: expect.objectContaining({ leverage_allowed: false, max_position_weight_ppm: 100_000 }),
        context: expect.objectContaining({ market_state_revision: dataset.data_revision, sector_id: 'A' }),
      }),
    }))
    expect(wrapper.get('.summary-grid').text()).toContain('10.00%')
    expect(wrapper.emitted('completed')?.[0]?.[0]).toMatchObject({
      run_id: 'run-1', objects: [
        expect.objectContaining({ object_id: 'state-80', label: '中枢上方·有三买', bar_index: 80 }),
        expect.objectContaining({ object_id: 'risk-approved-81', label: '风控·订单意图批准', bar_index: 81 }),
      ],
    })
    expect(wrapper.get('.summary-grid').text()).toContain('风控批准 1')
    await wrapper.setProps({ view: 'trades' })
    expect(wrapper.get('.trade-table').text()).toContain('trade-1')
    await wrapper.setProps({ view: 'equity' })
    expect(wrapper.get('.equity-chart polyline').attributes('points')).not.toBe('')
  })

  it('requires daily point-in-time ranking context and keeps foreign prices off the object tree', async () => {
    const incompatible = mount(BacktestPanel, { props: { dataset, view: 'backtest' } })
    await flushPromises()
    const incompatibleSelect = incompatible.get('select[aria-label="选择回测策略"]')
    ;(incompatibleSelect.findAll('option').at(-1)!.element as HTMLOptionElement).selected = true
    await incompatibleSelect.trigger('change')
    await flushPromises()
    expect(incompatible.text()).toContain('只接受显式复权的 1d 数据')
    expect(incompatible.get('.backtest-controls button').attributes('disabled')).toBeDefined()
    incompatible.unmount()

    api.getBacktestChartEvents.mockResolvedValue([
      {
        event_seq: 1, known_at_bar_index: 90, object_type: 'chart_event', object_id: 'class-current',
        operation: 'upsert', object_revision: 1,
        payload: { event_type: 'aux_ma_strength_class', chart_dataset_id: 'SSE.600000.1d', bar_index: 90, timestamp_utc: 1_700_000_000_000, price_i64: 105, instrument_strength_class: 6 },
      },
      {
        event_seq: 2, known_at_bar_index: 90, object_type: 'chart_event', object_id: 'class-foreign',
        operation: 'upsert', object_revision: 1,
        payload: { event_type: 'aux_ma_strength_class', chart_dataset_id: 'SZSE.000001.1d', bar_index: 75, timestamp_utc: 1_700_000_000_000, price_i64: 9999, instrument_strength_class: 4 },
      },
      {
        event_seq: 3, known_at_bar_index: 90, object_type: 'chart_event', object_id: 'sector-bank',
        operation: 'upsert', object_revision: 1,
        payload: { event_type: 'aux_sector_strength_mean', chart_dataset_id: 'SSE.600000.1d', sector_id: 'bank', timestamp_utc: 1_700_000_000_000, sector_strength_mean_milli: 5000 },
      },
    ])
    const wrapper = mount(BacktestPanel, { props: { dataset: rankingDataset, view: 'backtest' } })
    await flushPromises()
    const select = wrapper.get('select[aria-label="选择回测策略"]')
    ;(select.findAll('option').at(-1)!.element as HTMLOptionElement).selected = true
    await select.trigger('change')
    await flushPromises()
    const context = {
      universe_id: 'cn-a-share', membership_revision: `sha256:${'7'.repeat(64)}`, membership_mode: 'point_in_time',
      price_adjustment_mode: 'forward_adjusted', price_adjustment_revision: `sha256:${'8'.repeat(64)}`,
      episode_id: 'rebound-1', episode_start_timestamp_utc: 1_700_000_000_000, episode_available_at_utc: 1_700_000_000_000,
      memberships: [
        { dataset_id: rankingDataset.dataset_id, data_revision: rankingDataset.data_revision, sector_id: 'bank', effective_from_utc: 0, effective_to_utc: null, available_at_utc: 0 },
        { dataset_id: 'SZSE.000001.1d', data_revision: `sha256:${'9'.repeat(64)}`, sector_id: 'bank', effective_from_utc: 0, effective_to_utc: null, available_at_utc: 0 },
      ],
    }
    await wrapper.get('.ranking-context-field textarea').setValue(JSON.stringify(context))
    await wrapper.get('.backtest-controls button').trigger('click')
    await flushPromises()
    expect(api.createBacktest).toHaveBeenCalledWith(expect.objectContaining({
      strategy: expect.objectContaining({ algorithm_id: 'aux_ma_sector_rotation' }),
      ranking_context: context,
      parameters: expect.objectContaining({ ma_period_1: 5, ma_period_8: 233, minimum_sector_coverage_milli: 800 }),
    }))
    expect(wrapper.emitted('completed')?.[0]?.[0]).toMatchObject({
      objects: [expect.objectContaining({ object_id: 'class-current', label: '经验·标的均线等级' })],
      signals: expect.arrayContaining([
        expect.objectContaining({ object_id: 'class-foreign' }),
        expect.objectContaining({ object_id: 'sector-bank' }),
      ]),
    })
    wrapper.unmount()
  })

  it('selects the B2 strategy with its risk parameters and labels the handoff event', async () => {
    api.getBacktestChartEvents.mockResolvedValue([{
      event_seq: 1, known_at_bar_index: 73, object_type: 'chart_event', object_id: 'handoff-73',
      operation: 'upsert', object_revision: 1,
      payload: { event_type: 'handoff_to_B3_trend', bar_index: 70, timestamp_utc: 1_700_000_000_000, price_i64: 2650, reason_code: 'B3_NONDIVERGENT_HANDOFF' },
    }])
    const wrapper = mount(BacktestPanel, { props: { dataset, view: 'backtest' } })
    await flushPromises()
    const select = wrapper.get('select[aria-label="选择回测策略"]')
    ;(select.findAll('option')[1].element as HTMLOptionElement).selected = true
    await select.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('weakest_quantity')
    await wrapper.get('.backtest-controls button').trigger('click')
    await flushPromises()
    expect(api.createBacktest).toHaveBeenCalledWith(expect.objectContaining({
      strategy: expect.objectContaining({ algorithm_id: 'second_buy_only' }),
      parameters: expect.objectContaining({ strongest_quantity: 2, normal_quantity: 2, weakest_quantity: 1 }),
    }))
    expect(wrapper.emitted('completed')?.[0]?.[0]).toMatchObject({
      objects: [expect.objectContaining({ object_id: 'handoff-73', label: '移交三买趋势持有', bar_index: 70 })],
    })
  })

  it('runs the MA-kiss catalog item as visible auxiliary events without presenting it as trading', async () => {
    api.getBacktestChartEvents.mockResolvedValue([{
      event_seq: 1, known_at_bar_index: 73, object_type: 'chart_event', object_id: 'AUX-MA-LIP-70-73',
      operation: 'upsert', object_revision: 1,
      payload: {
        event_type: 'aux_lip_kiss', bar_index: 71, timestamp_utc: 1_700_000_000_000,
        price_i64: 2650, reason_code: 'AUX_MA_LIP_KISS_CONFIRMED', standard_signal: false,
      },
    }])
    const wrapper = mount(BacktestPanel, { props: { dataset, view: 'backtest' } })
    await flushPromises()
    const select = wrapper.get('select[aria-label="选择回测策略"]')
    ;(select.findAll('option')[8].element as HTMLOptionElement).selected = true
    await select.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('enable_legacy_b1_macd_proxy')
    expect(wrapper.get('.backtest-controls button').text()).toBe('生成辅助事件（不交易）')
    await wrapper.get('.backtest-controls button').trigger('click')
    await flushPromises()
    expect(api.createBacktest).toHaveBeenCalledWith(expect.objectContaining({
      strategy: expect.objectContaining({ algorithm_id: 'aux_ma_kiss_legacy' }),
      parameters: expect.objectContaining({
        short_period: 5, long_period: 10, proximity_ticks: 1, flat_slope_ticks: 1,
        enable_legacy_b1_macd_proxy: true, macd_fast_period: 12, macd_slow_period: 26,
        macd_signal_period: 9, legacy_divergence_min_bars: 1,
      }),
    }))
    expect(wrapper.emitted('completed')?.[0]?.[0]).toMatchObject({
      objects: [expect.objectContaining({
        object_id: 'AUX-MA-LIP-70-73', label: '辅助·唇吻', bar_index: 71,
      })],
    })
  })

  it('fixes the MACD defense timeframe to the dataset and displays risk events without trading', async () => {
    api.getBacktestChartEvents.mockResolvedValue([{
      event_seq: 1, known_at_bar_index: 77, object_type: 'chart_event', object_id: 'AUX-MACD-RISK-OFF-77',
      operation: 'upsert', object_revision: 1,
      payload: {
        event_type: 'aux_macd_risk_off', bar_index: 77, timestamp_utc: 1_700_000_000_000,
        price_i64: 2640, reason_code: 'AUX_MACD_DIFF_AND_DEA_CONFIRMED_BELOW_ZERO_AXIS',
        standard_signal: false, risk_filter_active: true,
      },
    }])
    const wrapper = mount(BacktestPanel, { props: { dataset, view: 'backtest' } })
    await flushPromises()
    const select = wrapper.get('select[aria-label="选择回测策略"]')
    ;(select.findAll('option')[9].element as HTMLOptionElement).selected = true
    await select.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('reclaim_confirm_bars')
    expect(wrapper.get('.backtest-controls button').text()).toBe('生成辅助事件（不交易）')
    await wrapper.get('.backtest-controls button').trigger('click')
    await flushPromises()
    expect(api.createBacktest).toHaveBeenCalledWith(expect.objectContaining({
      strategy: expect.objectContaining({ algorithm_id: 'aux_macd_zero_axis_defense' }),
      parameters: expect.objectContaining({
        minimum_timeframe_minutes: 5, fast_period: 12, slow_period: 26, signal_period: 9,
        zero_axis_buffer_ticks: 0, risk_off_confirm_bars: 1, reclaim_confirm_bars: 2,
      }),
    }))
    expect(wrapper.emitted('completed')?.[0]?.[0]).toMatchObject({
      objects: [expect.objectContaining({
        object_id: 'AUX-MACD-RISK-OFF-77', label: '辅助·MACD零轴下防守', bar_index: 77,
      })],
    })
  })

  it('fixes the BOLL observation timeframe and labels structural-context warnings', async () => {
    api.getBacktestChartEvents.mockResolvedValue([{
      event_seq: 1, known_at_bar_index: 81, object_type: 'chart_event', object_id: 'AUX-BOLL-WARNING-div-81',
      operation: 'upsert', object_revision: 1,
      payload: {
        event_type: 'aux_boll_bardo_end_or_promotion_warning', bar_index: 81,
        timestamp_utc: 1_700_000_000_000, price_i64: 2630,
        reason_code: 'AUX_BOLL_CONTRACTION_IN_CONFIRMED_BARDO_CONTEXT', standard_signal: false,
      },
    }])
    const wrapper = mount(BacktestPanel, { props: { dataset, view: 'backtest' } })
    await flushPromises()
    const select = wrapper.get('select[aria-label="选择回测策略"]')
    ;(select.findAll('option')[10].element as HTMLOptionElement).selected = true
    await select.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('contraction_confirm_bars')
    expect(wrapper.get('.backtest-controls button').text()).toBe('生成辅助事件（不交易）')
    await wrapper.get('.backtest-controls button').trigger('click')
    await flushPromises()
    expect(api.createBacktest).toHaveBeenCalledWith(expect.objectContaining({
      strategy: expect.objectContaining({ algorithm_id: 'aux_boll_bardo_warning' }),
      parameters: expect.objectContaining({
        checkpoint_interval: 1024, observation_timeframe_minutes: 5,
        level_mapping_profile_id: 1, boll_period: 20, boll_stddev_milli: 2000,
        effective_reentry_bars: 2, failed_reentry_confirm_bars: 2,
        band_turn_confirm_bars: 1, band_turn_min_change_ticks: 0,
        contraction_confirm_bars: 3, contraction_min_width_drop_ticks: 0,
      }),
    }))
    expect(wrapper.emitted('completed')?.[0]?.[0]).toMatchObject({
      objects: [expect.objectContaining({
        object_id: 'AUX-BOLL-WARNING-div-81', label: '辅助·BOLL中阴结束或升级预警',
        bar_index: 81,
      })],
    })
  })

  it('runs the fixed 8x30m daily profile and preserves its chart label and explanation', async () => {
    api.getBacktestChartEvents.mockResolvedValue([{
      event_seq: 1, known_at_bar_index: 87, object_type: 'chart_event', object_id: 'AUX-DAILY30M-20260819-CLASSIFICATION',
      operation: 'upsert', object_revision: 1,
      payload: {
        event_type: 'aux_daily_30m_classification', bar_index: 87,
        timestamp_utc: 1_700_000_300_000, price_i64: 23,
        reason_code: 'AUX_DAILY_8X30M_SESSION_CLASSIFIED_AT_FINAL_BAR_CLOSE',
        classification: 'daily_two_center', daily_center_count: 2,
        display_label: '日内双重叠区·向上·收于上方重叠区上方',
        classification_detail: '日内双重叠区·向上·收于上方重叠区上方；经验分类，不是标准中枢或买卖点，不允许交易',
        center_1_start_timestamp_utc: 1_700_000_000_000,
        center_1_end_timestamp_utc: 1_700_000_300_000,
        center_1_low_i64: 10, center_1_high_i64: 12,
        center_2_start_timestamp_utc: 1_700_000_000_000,
        center_2_end_timestamp_utc: 1_700_000_300_000,
        center_2_low_i64: 20, center_2_high_i64: 22,
        semantic_namespace: 'heuristic', evidence_level: 'HEURISTIC',
        standard_signal: false, standard_center: false, execution_allowed: false,
      },
    }])
    const wrapper = mount(BacktestPanel, { props: { dataset: daily30mDataset, view: 'backtest' } })
    await flushPromises()
    const select = wrapper.get('select[aria-label="选择回测策略"]')
    ;(select.findAll('option')[11].element as HTMLOptionElement).selected = true
    await select.trigger('change')
    await flushPromises()
    expect(wrapper.get('.backtest-controls button').text()).toBe('生成辅助事件（不交易）')
    expect(wrapper.get('.backtest-controls button').attributes('disabled')).toBeUndefined()
    await wrapper.get('.backtest-controls button').trigger('click')
    await flushPromises()
    expect(api.createBacktest).toHaveBeenCalledWith(expect.objectContaining({
      dataset_id: 'TEST.A1.30m',
      strategy: expect.objectContaining({ algorithm_id: 'aux_daily_30m_classification' }),
      parameters: {
        checkpoint_interval: 1024, observation_timeframe_minutes: 30, session_profile_id: 1,
      },
    }))
    expect(wrapper.emitted('completed')?.[0]?.[0]).toMatchObject({
      objects: [expect.objectContaining({
        object_id: 'AUX-DAILY30M-20260819-CLASSIFICATION',
        label: '日内双重叠区·向上·收于上方重叠区上方',
        detail: '日内双重叠区·向上·收于上方重叠区上方；经验分类，不是标准中枢或买卖点，不允许交易',
        bar_index: 87,
      })],
    })
  })

  it('blocks the 8x30m daily profile on an incompatible dataset before creating a run', async () => {
    const wrapper = mount(BacktestPanel, { props: { dataset, view: 'backtest' } })
    await flushPromises()
    const select = wrapper.get('select[aria-label="选择回测策略"]')
    ;(select.findAll('option')[11].element as HTMLOptionElement).selected = true
    await select.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('只接受原课的30分钟数据')
    expect(wrapper.get('.backtest-controls button').attributes()).toHaveProperty('disabled')
    await wrapper.get('.backtest-controls button').trigger('click')
    expect(api.createBacktest).not.toHaveBeenCalled()
  })

  it('removes a daily classification from chart signals when a later bar invalidates its profile', async () => {
    api.getBacktestChartEvents.mockResolvedValue([
      {
        event_seq: 1, known_at_bar_index: 87, object_type: 'chart_event',
        object_id: 'AUX-DAILY30M-20260819-CLASSIFICATION', operation: 'upsert', object_revision: 1,
        payload: {
          event_type: 'aux_daily_30m_classification', bar_index: 87,
          timestamp_utc: 1_700_000_300_000, price_i64: 23, display_label: '日内一重叠区',
        },
      },
      {
        event_seq: 2, known_at_bar_index: 88, object_type: 'chart_event',
        object_id: 'AUX-DAILY30M-20260819-CLASSIFICATION', operation: 'delete', object_revision: 2,
        payload: {},
      },
      {
        event_seq: 3, known_at_bar_index: 88, object_type: 'chart_event',
        object_id: 'AUX-DAILY30M-20260819-PROFILE-REJECTED', operation: 'upsert', object_revision: 1,
        payload: {
          event_type: 'aux_daily_30m_profile_rejected', bar_index: 88,
          timestamp_utc: 1_700_000_600_000, price_i64: 22,
          display_label: '日内8根30分钟profile不匹配',
        },
      },
    ])
    const wrapper = mount(BacktestPanel, { props: { dataset: daily30mDataset, view: 'backtest' } })
    await flushPromises()
    const select = wrapper.get('select[aria-label="选择回测策略"]')
    ;(select.findAll('option')[11].element as HTMLOptionElement).selected = true
    await select.trigger('change')
    await wrapper.get('.backtest-controls button').trigger('click')
    await flushPromises()
    const completed = wrapper.emitted('completed')?.[0]?.[0] as { objects: Array<{ object_id: string }>; signals: Array<{ object_id: string }> }
    expect(completed.objects.map((item) => item.object_id)).toEqual(['AUX-DAILY30M-20260819-PROFILE-REJECTED'])
    expect(completed.signals.map((item) => item.object_id)).toEqual(['AUX-DAILY30M-20260819-PROFILE-REJECTED'])
  })

  it('selects the B3 strategy and labels a new-center hold on the object tree', async () => {
    api.getBacktestChartEvents.mockResolvedValue([{
      event_seq: 1, known_at_bar_index: 88, object_type: 'chart_event', object_id: 'B3-hold-88',
      operation: 'upsert', object_revision: 1,
      payload: { event_type: 'hold_new_center', bar_index: 85, timestamp_utc: 1_700_000_300_000, price_i64: 2680, reason_code: 'NEW_CENTER_WITHOUT_TREND_DIVERGENCE_HOLD' },
    }])
    const wrapper = mount(BacktestPanel, { props: { dataset, view: 'backtest' } })
    await flushPromises()
    const select = wrapper.get('select[aria-label="选择回测策略"]')
    ;(select.findAll('option')[2].element as HTMLOptionElement).selected = true
    await select.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('minimum_entry_volume')
    await wrapper.get('.backtest-controls button').trigger('click')
    await flushPromises()
    expect(api.createBacktest).toHaveBeenCalledWith(expect.objectContaining({
      strategy: expect.objectContaining({ algorithm_id: 'third_buy_only' }),
      parameters: expect.objectContaining({ first_center_quantity: 2, late_center_quantity: 1, minimum_entry_volume: 0 }),
    }))
    expect(wrapper.emitted('completed')?.[0]?.[0]).toMatchObject({
      objects: [expect.objectContaining({ object_id: 'B3-hold-88', label: '新中枢无背驰·继续持有', bar_index: 85 })],
    })
  })

  it('selects the center-oscillation strategy and labels its semantic swing event', async () => {
    api.getBacktestChartEvents.mockResolvedValue([{
      event_seq: 1, known_at_bar_index: 90, object_type: 'chart_event', object_id: 'swing-buy-90',
      operation: 'upsert', object_revision: 1,
      payload: { event_type: 'swing_buy', bar_index: 87, timestamp_utc: 1_700_000_600_000, price_i64: 2655, reason_code: 'CONFIRMED_CENTER_BOTTOM_OSCILLATION_DIVERGENCE' },
    }])
    const wrapper = mount(BacktestPanel, { props: { dataset, view: 'backtest' } })
    await flushPromises()
    const select = wrapper.get('select[aria-label="选择回测策略"]')
    ;(select.findAll('option')[3].element as HTMLOptionElement).selected = true
    await select.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('estimated_round_trip_cost_i64')
    expect(wrapper.text()).toContain('max_entries_per_center')
    await wrapper.get('.backtest-controls button').trigger('click')
    await flushPromises()
    expect(api.createBacktest).toHaveBeenCalledWith(expect.objectContaining({
      strategy: expect.objectContaining({ algorithm_id: 'centre_oscillation_spread' }),
      parameters: expect.objectContaining({
        strong_quantity: 2, neutral_quantity: 1, weak_quantity: 1,
        estimated_round_trip_cost_i64: 0, minimum_net_range_i64: 1,
        fast_execution_available: false, max_entries_per_center: 4,
      }),
    }))
    expect(wrapper.emitted('completed')?.[0]?.[0]).toMatchObject({
      objects: [expect.objectContaining({ object_id: 'swing-buy-90', label: '中枢震荡买入', bar_index: 87 })],
    })
  })

  it('selects the same-level decomposition strategy and labels its Ai branch', async () => {
    api.getBacktestChartEvents.mockResolvedValue([{
      event_seq: 1, known_at_bar_index: 96, object_type: 'chart_event', object_id: 'same-level-wait-96',
      operation: 'upsert', object_revision: 1,
      payload: { event_type: 'wait_new_same_level_structure', bar_index: 93, timestamp_utc: 1_700_000_900_000, price_i64: 2670, reason_code: 'AI_PLUS_3_DESTROYED_AI_DIRECTIONAL_EXTREME' },
    }, {
      event_seq: 2, known_at_bar_index: 99, object_type: 'chart_event', object_id: 'same-level-promote-99',
      operation: 'upsert', object_revision: 1,
      payload: { event_type: 'promote_level', bar_index: 97, timestamp_utc: 1_700_001_200_000, price_i64: 2680, reason_code: 'CONFIRMED_HIGHER_LEVEL_CENTER_PROMOTION', from_level_id: 'L0', to_level_id: 'L1' },
    }])
    const wrapper = mount(BacktestPanel, { props: { dataset, view: 'backtest' } })
    await flushPromises()
    const select = wrapper.get('select[aria-label="选择回测策略"]')
    ;(select.findAll('option')[4].element as HTMLOptionElement).selected = true
    await select.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('odd_direction_is_down')
    expect(wrapper.text()).toContain('operation_quantity')
    await wrapper.get('.backtest-controls button').trigger('click')
    await flushPromises()
    expect(api.createBacktest).toHaveBeenCalledWith(expect.objectContaining({
      strategy: expect.objectContaining({ algorithm_id: 'same_level_decomposition_program' }),
      parameters: expect.objectContaining({ odd_direction_is_down: true, operation_quantity: 1 }),
    }))
    expect(wrapper.emitted('completed')?.[0]?.[0]).toMatchObject({
      objects: [
        expect.objectContaining({ object_id: 'same-level-wait-96', label: '等待新同级结构', bar_index: 93 }),
        expect.objectContaining({ object_id: 'same-level-promote-99', label: '确认提升操作级别', bar_index: 97 }),
      ],
    })
  })

  it('selects segmented rebound operation and labels partial profit on the object tree', async () => {
    api.getBacktestChartEvents.mockResolvedValue([{
      event_seq: 1, known_at_bar_index: 112, object_type: 'chart_event', object_id: 'segmented-partial-112',
      operation: 'upsert', object_revision: 1,
      payload: { event_type: 'partial_take_profit', bar_index: 109, timestamp_utc: 1_700_001_500_000, price_i64: 2701, reason_code: 'FIRST_EXECUTION_LEVEL_LEG_COMPLETED' },
    }])
    const wrapper = mount(BacktestPanel, { props: { dataset, view: 'backtest' } })
    await flushPromises()
    const select = wrapper.get('select[aria-label="选择回测策略"]')
    ;(select.findAll('option')[6].element as HTMLOptionElement).selected = true
    await select.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('partial_take_profit_quantity')
    expect(wrapper.text()).toContain('minimum_net_segment_i64')
    await wrapper.get('.backtest-controls button').trigger('click')
    await flushPromises()
    expect(api.createBacktest).toHaveBeenCalledWith(expect.objectContaining({
      strategy: expect.objectContaining({ algorithm_id: 'target_level_rebound_segmented_operation' }),
      parameters: expect.objectContaining({
        level_graph_profile_id: 1, operation_quantity: 2, partial_take_profit_quantity: 1,
        estimated_round_trip_cost_i64: 0, minimum_net_segment_i64: 1,
        execution_available: true,
      }),
    }))
    expect(wrapper.emitted('completed')?.[0]?.[0]).toMatchObject({
      objects: [expect.objectContaining({ object_id: 'segmented-partial-112', label: '首次次级别段·部分兑现', bar_index: 109 })],
    })
  })

  it('selects three-level complete classification and labels its high-level candidate', async () => {
    api.getBacktestChartEvents.mockResolvedValue([{
      event_seq: 1, known_at_bar_index: 104, object_type: 'chart_event', object_id: 'three-level-high-104',
      operation: 'upsert', object_revision: 1,
      payload: { event_type: 'high_change_candidate', bar_index: 101, timestamp_utc: 1_700_001_200_000, price_i64: 2688, reason_code: 'MIDDLE_LEVEL_MIGRATION_COMPLETED_HIGH_CHANGE_CANDIDATE' },
    }])
    const wrapper = mount(BacktestPanel, { props: { dataset, view: 'backtest' } })
    await flushPromises()
    const select = wrapper.get('select[aria-label="选择回测策略"]')
    ;(select.findAll('option')[5].element as HTMLOptionElement).selected = true
    await select.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('level_graph_profile_id')
    expect(wrapper.text()).toContain('can_handle_mid_center_continue')
    await wrapper.get('.backtest-controls button').trigger('click')
    await flushPromises()
    expect(api.createBacktest).toHaveBeenCalledWith(expect.objectContaining({
      strategy: expect.objectContaining({ algorithm_id: 'three_level_complete_classification' }),
      parameters: expect.objectContaining({
        level_graph_profile_id: 1, operation_quantity: 1,
        can_handle_mid_third_point: true, can_handle_mid_center_continue: true,
        can_handle_high_change_candidate: true,
      }),
    }))
    expect(wrapper.emitted('completed')?.[0]?.[0]).toMatchObject({
      objects: [expect.objectContaining({ object_id: 'three-level-high-104', label: '高层变化候选', bar_index: 101 })],
    })
  })

  it('selects bottom/top construction and labels its coarse non-trading zone', async () => {
    api.getBacktestChartEvents.mockResolvedValue([{
      event_seq: 1, known_at_bar_index: 116, object_type: 'chart_event', object_id: 'coarse-bottom-116',
      operation: 'upsert', object_revision: 1,
      payload: { event_type: 'coarse_bottom_zone', bar_index: 113, timestamp_utc: 1_700_001_800_000, price_i64: 2660, reason_code: 'CONFIRMED_BOTTOM_FRACTAL_ZONE' },
    }])
    const wrapper = mount(BacktestPanel, { props: { dataset, view: 'backtest' } })
    await flushPromises()
    const select = wrapper.get('select[aria-label="选择回测策略"]')
    ;(select.findAll('option')[7].element as HTMLOptionElement).selected = true
    await select.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('coarse_effective_hold_bars')
    expect(wrapper.text()).toContain('execution_available')
    await wrapper.get('.backtest-controls button').trigger('click')
    await flushPromises()
    expect(api.createBacktest).toHaveBeenCalledWith(expect.objectContaining({
      strategy: expect.objectContaining({ algorithm_id: 'bottom_top_construction' }),
      parameters: expect.objectContaining({
        level_graph_profile_id: 1, operation_quantity: 1,
        execution_available: true, coarse_effective_hold_bars: 1,
      }),
    }))
    expect(wrapper.emitted('completed')?.[0]?.[0]).toMatchObject({
      objects: [expect.objectContaining({ object_id: 'coarse-bottom-116', label: '粗略底分型区间', bar_index: 113 })],
    })
  })
})
