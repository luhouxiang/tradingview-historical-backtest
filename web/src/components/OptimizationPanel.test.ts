import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DatasetMeta } from '../types/api'
import OptimizationPanel from './OptimizationPanel.vue'

const api = vi.hoisted(() => ({
  listAlgorithms: vi.fn(), createStudy: vi.fn(), getStudy: vi.fn(), getStudyEvaluations: vi.fn(),
}))
vi.mock('../api/client', () => api)

const strategy = {
  kind: 'strategy', algorithm_id: 'ma20_retest_short', algorithm_version: '1.0.0',
  source_hash: `sha256:${'1'.repeat(64)}`, name: 'MA20 Retest Failure Short',
  parameter_schema: {
    properties: {
      ma_period: { type: 'integer', minimum: 2, maximum: 100, default: 20 },
      touch_tolerance_ticks: { type: 'integer', minimum: 0, maximum: 10, default: 1 },
      max_retest_bars: { type: 'integer', minimum: 1, maximum: 100, default: 20 },
    },
  },
}
const riskFilter = {
  kind: 'risk_filter', algorithm_id: 'unified_risk_execution_overlay', algorithm_version: '1.0.0',
  source_hash: `sha256:${'5'.repeat(64)}`, name: '统一风险与执行覆盖层',
  parameter_schema: {
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
  instrument: { exchange: 'TEST', symbol: 'A1', product: 'A' },
  coverage: { first_bar_index: 0, last_bar_index: 1000 },
} as DatasetMeta

describe('OptimizationPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listAlgorithms.mockResolvedValue([strategy, riskFilter])
    api.createStudy.mockResolvedValue({ study_id: 'study-1', status: 'queued' })
    api.getStudy.mockResolvedValue({ study_id: 'study-1', status: 'completed', progress: 1 })
    api.getStudyEvaluations.mockResolvedValue({
      evaluations: [{
        evaluation_index: 0, parameters: { ma_period: 20 }, constraints_satisfied: true, status: 'completed',
        train_run_id: 'train-1', train_run_signature: `sha256:${'3'.repeat(64)}`,
        validation_run_id: 'validation-1', validation_run_signature: `sha256:${'4'.repeat(64)}`,
        train_metrics: { total_return: .1, trade_count: 3 },
        validation_metrics: { total_return: .04, trade_count: 2 }, train_rank: 1, validation_rank: 2,
      }],
      stability: {
        selected_evaluation_index: 0, selected_train_rank: 1, selected_validation_rank: 2,
        primary_metric: 'total_return', train_primary_value: .1, validation_primary_value: .04,
        primary_absolute_gap: .06, constraint_feasible_count: 1, warnings: [],
      },
    })
  })

  it('submits ordered train and validation ranges and compares results', async () => {
    const wrapper = mount(OptimizationPanel, { props: { dataset } })
    await flushPromises()
    await wrapper.get('.optimization-controls button').trigger('click')
    await flushPromises()
    expect(api.createStudy).toHaveBeenCalledWith(expect.objectContaining({
      search_space: [expect.objectContaining({ name: 'ma_period' })],
      ranges: {
        train: expect.objectContaining({ to_bar_index: 699 }),
        validation: expect.objectContaining({ from_bar_index: 700 }),
      },
      risk_overlay: expect.objectContaining({
        algorithm: expect.objectContaining({ kind: 'risk_filter', algorithm_id: 'unified_risk_execution_overlay' }),
        parameters: expect.objectContaining({ leverage_allowed: false, max_position_weight_ppm: 100_000 }),
        context: expect.objectContaining({ market_state_revision: dataset.data_revision, sector_id: 'A' }),
      }),
    }))
    expect(wrapper.get('.stability-summary').text()).toContain('验证排名 2')
    expect(wrapper.get('.optimization-table').text()).toContain('10.00%')
    expect(wrapper.get('.optimization-table').text()).toContain('4.00%')
  })
})
