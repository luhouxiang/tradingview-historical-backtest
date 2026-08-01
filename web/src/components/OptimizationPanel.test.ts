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
const dataset = {
  dataset_id: 'TEST.A1.5m', data_revision: `sha256:${'2'.repeat(64)}`,
  coverage: { first_bar_index: 0, last_bar_index: 1000 },
} as DatasetMeta

describe('OptimizationPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listAlgorithms.mockResolvedValue([strategy])
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
    }))
    expect(wrapper.get('.stability-summary').text()).toContain('验证排名 2')
    expect(wrapper.get('.optimization-table').text()).toContain('10.00%')
    expect(wrapper.get('.optimization-table').text()).toContain('4.00%')
  })
})
