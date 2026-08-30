import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import MultiDatasetResearchPanel from './MultiDatasetResearchPanel.vue'

const api = vi.hoisted(() => ({
  listDatasets: vi.fn(), listAlgorithms: vi.fn(), listResearchStudies: vi.fn(), getDataset: vi.fn(),
  createResearchStudy: vi.fn(), getResearchStudy: vi.fn(), getResearchStudyResults: vi.fn(),
  cancelResearchStudy: vi.fn(), resumeResearchStudy: vi.fn(),
}))
vi.mock('../api/client', () => api)

const revision = `sha256:${'1'.repeat(64)}`
const summaries = [
  { dataset_id: 'AO2609.5m', active_revision: revision, instrument: 'AO2609', timeframe: '5m', bar_count: 100, first_timestamp_utc: 0, last_timestamp_utc: 1, trading_day_count: 600, independence_group: 'SHFE.AO', status: 'ready' },
  { dataset_id: 'AOL9.5m', active_revision: revision, instrument: 'AOL9', timeframe: '5m', bar_count: 100, first_timestamp_utc: 0, last_timestamp_utc: 1, trading_day_count: 600, independence_group: 'SHFE.AO', status: 'ready' },
  { dataset_id: 'I2609.5m', active_revision: revision, instrument: 'I2609', timeframe: '5m', bar_count: 100, first_timestamp_utc: 0, last_timestamp_utc: 1, trading_day_count: 600, independence_group: 'DCE.I', status: 'ready' },
] as const

describe('MultiDatasetResearchPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listDatasets.mockResolvedValue({ catalog_revision: 1, datasets: summaries })
    api.listAlgorithms.mockResolvedValue([{ kind: 'strategy', algorithm_id: 'formal', algorithm_version: '1', source_hash: revision, name: 'Formal', input_schema: 'bars.v1', parameter_schema: { type: 'object', properties: { threshold: { type: 'integer', minimum: 0, maximum: 10, default: 1 } }, required: ['threshold'], additionalProperties: false }, outputs: [], warmup: { kind: 'fixed_bars', bars: 0 }, causal: true, comparison_eligible: true, research_role: 'formal_strategy' }])
    api.listResearchStudies.mockResolvedValue([])
    api.getDataset.mockImplementation((id: string) => Promise.resolve({ dataset_id: id, data_revision: revision, timeframe: '5m', coverage: { first_bar_index: 0, last_bar_index: 99 } }))
    api.createResearchStudy.mockResolvedValue({ research_study_id: 'research-1', status: 'queued' })
    api.getResearchStudy.mockResolvedValue({
      research_study_id: 'research-1', status: 'completed', progress: 1,
      manifest: {
        execution: { semantic_version: '1.0.0', contract_multiplier_source: 'per_dataset_instrument_config' },
        datasets: [
          { execution: { contract_multiplier: 20 } },
          { execution: { contract_multiplier: 100 } },
        ],
      },
    })
    api.getResearchStudyResults.mockResolvedValue({
      items: [{ dataset_id: 'AO2609.5m', data_revision: revision, independence_group: 'SHFE.AO', trading_day_count: 600, status: 'completed', folds: [{ dataset_id: 'AO2609.5m', independence_group: 'SHFE.AO', fold_index: 0, status: 'completed', train_trading_day_from: '2024-01-01', train_trading_day_to: '2024-12-31', validation_trading_day_from: '2025-01-01', validation_trading_day_to: '2025-03-31', train_range: { warmup_from_bar_index: 0, from_bar_index: 0, to_bar_index: 251 }, validation_range: { warmup_from_bar_index: 0, from_bar_index: 252, to_bar_index: 314 }, selected_parameters: { threshold: 1 }, validation_metrics: { total_return: .03, max_drawdown: .02, trade_count: 5 }, parameter_changed: false, changed_parameter_names: [] }] }],
      aggregate: {
        data_status: 'exploratory', eligible_independence_group_count: 2, total_return: .1, median_dataset_return: .05, profitable_dataset_ratio: .67, worst_dataset_id: 'AOL9.5m', worst_dataset_return: -.02, total_trade_count: 20, walk_forward_fold_count: 1, completed_walk_forward_fold_count: 1, profitable_fold_ratio: 1, worst_fold_max_drawdown: .02, out_of_sample_trade_count: 5, parameter_stability: null, certification_trade_count: 5, out_of_sample_expectancy_i64: 10,
        attempted_parameter_combinations: [{ combination_id: revision, parameters: { threshold: 1 }, attempt_count: 1, completed_count: 1 }],
        first_failure_scenario: 'cost_2x', stress_scenarios: [{ scenario_id: 'cost_2x', status: 'completed', cost_multiplier: 2, additional_slippage_ticks: 0, additional_delay_bars: 0, max_volume_participation_rate: null, fill_mode: 'unlimited', completed_run_count: 1, failed_run_count: 0, total_return: -.01, max_drawdown: .04, trade_count: 5, requested_quantity: 10, filled_quantity: 10, fill_rate: 1, return_degradation: .04, drawdown_degradation: .02, fill_rate_degradation: 0, failure_reason: 'NON_POSITIVE_TOTAL_RETURN' }],
        certification: { rules_version: '1.0.0', tier: 'exploratory', reliable_candidate_is_historical_only: true, research_candidate_passed: false, reliable_candidate_passed: false, reasons: ['minimum_walk_forward_folds_not_met'], evidence_matrix: [{ gate_id: 'minimum_walk_forward_folds', required_for: 'research_candidate', passed: false, actual: 1, threshold: 4, reason: 'minimum_walk_forward_folds_not_met' }] },
        statistical_evidence: { bootstrap: { method: 'moving_block_bootstrap', sample_count: 5, block_size_trading_days: 5, iterations: 2000, confidence_level: .95, random_seed: 7, metrics: { mean_daily_return: { point_estimate: .01, lower: -.01, upper: .02, reason: null } } }, multiple_comparisons: { candidate_count: 2, comparison_count: 1, multiple_comparison_warning: true, warning: 'warning', comparisons: [] }, parameter_neighborhood: { evaluated_neighbor_count: 2, completed_neighbor_count: 2, pass_rate: .5, required_pass_rate: .6, passed: false, reason: null } },
      },
    })
  })

  it('submits version-pinned datasets separately and presents deduplicated evidence', async () => {
    const wrapper = mount(MultiDatasetResearchPanel, { props: { dataset: null } })
    await flushPromises()
    expect(wrapper.text()).toContain('3 个数据集')
    await wrapper.get('.actions button').trigger('click')
    await flushPromises()
    expect(api.createResearchStudy).toHaveBeenCalledWith(expect.objectContaining({
      datasets: expect.arrayContaining([expect.objectContaining({ dataset_id: 'AO2609.5m', data_revision: revision }), expect.objectContaining({ dataset_id: 'AOL9.5m' })]),
      strategy: expect.objectContaining({ algorithm_id: 'formal' }),
      walk_forward: expect.objectContaining({ train_trading_days: 252, validation_trading_days: 63, step_trading_days: 63, search_space: [expect.objectContaining({ name: 'threshold' })] }),
      stress_test: { suite_version: '1.0.0', volume_participation_rate: 0.1 },
      statistical_validation: { method_version: '1.0.0', block_size_trading_days: 5, iterations: 2000, confidence_level: .95, random_seed: 20260824, holm_alpha: .05 },
    }))
    expect(wrapper.text()).toContain('独立组 2/3')
    expect(wrapper.text()).toContain('探索性证据')
    expect(wrapper.text()).toContain('执行语义 v1.0.0')
    expect(wrapper.text()).toContain('20/100')
    expect(wrapper.text()).toContain('样本外折 1/1')
    expect(wrapper.text()).toContain('AO2609.5m #1')
    expect(wrapper.text()).toContain('cost_2x')
    expect(wrapper.text()).toContain('NON_POSITIVE_TOTAL_RETURN')
    expect(wrapper.text()).toContain('minimum_walk_forward_folds_not_met')
    expect(wrapper.text()).toContain('Holm')
    expect(wrapper.find('[aria-label="走步样本外收益折线"]').exists()).toBe(true)
  })

  it('runs one dataset as explicitly exploratory evidence instead of staying idle', async () => {
    const wrapper = mount(MultiDatasetResearchPanel, { props: { dataset: null } })
    await flushPromises()
    const boxes = wrapper.findAll('.dataset-grid input[type="checkbox"]')
    await boxes[1]?.setValue(false)
    await boxes[2]?.setValue(false)

    expect(wrapper.text()).toContain('单数据集研究可以执行')
    expect(wrapper.text()).toContain('配置就绪，请点击“运行研究”启动')
    expect(wrapper.get('[aria-label="运行单周期可靠性研究"]').classes()).toContain('primary-action')
    expect(wrapper.get('.actions button').attributes('disabled')).toBeUndefined()
    await wrapper.get('.actions button').trigger('click')
    await flushPromises()

    const request = api.createResearchStudy.mock.calls[0]?.[0]
    expect(request.datasets).toHaveLength(1)
    expect(request.datasets[0].dataset_id).toBe('AO2609.5m')
  })

  it('shows granular work after the study reaches its long-running final phases', async () => {
    vi.useFakeTimers()
    try {
      api.getResearchStudy
        .mockResolvedValueOnce({
          research_study_id: 'research-1', status: 'running', progress: .63,
          progress_detail: {
            stage: 'stress_test', completed_count: 23, total_count: 119,
            current_dataset_id: 'DCE.YL9.5m', current_scenario_id: 'cost_1_5x',
            current_fold_index: 5,
          },
        })
        .mockResolvedValueOnce({ research_study_id: 'research-1', status: 'completed', progress: 1 })
      const wrapper = mount(MultiDatasetResearchPanel, { props: { dataset: null } })
      await flushPromises()

      void wrapper.get('.actions button').trigger('click')
      await flushPromises()
      expect(wrapper.text()).toContain('running · 63%')
      expect(wrapper.text()).toContain('执行与成本压力测试 23/119')
      expect(wrapper.text()).toContain('cost_1_5x')
      expect(wrapper.text()).toContain('DCE.YL9.5m')

      await vi.advanceTimersByTimeAsync(250)
      await flushPromises()
      expect(wrapper.text()).toContain('completed · 100%')
    } finally {
      vi.useRealTimers()
    }
  })
})
