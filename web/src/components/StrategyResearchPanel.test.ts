import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AlgorithmDefinition, DatasetMeta } from '../types/api'
import StrategyResearchPanel from './StrategyResearchPanel.vue'

const api = vi.hoisted(() => ({
  listAlgorithms: vi.fn(),
  createStrategyComparison: vi.fn(),
  getStrategyComparison: vi.fn(),
  getStrategyComparisonResults: vi.fn(),
  cancelStrategyComparison: vi.fn(),
  listStrategyComparisons: vi.fn(),
  getBacktestSummary: vi.fn(),
  getBacktestTrades: vi.fn(),
  getBacktestEquity: vi.fn(),
  getBacktestChartEvents: vi.fn(),
  listDatasets: vi.fn(),
  getDataset: vi.fn(),
  createResearchStudy: vi.fn(),
  getResearchStudy: vi.fn(),
  getResearchStudyResults: vi.fn(),
  cancelResearchStudy: vi.fn(),
  resumeResearchStudy: vi.fn(),
  listResearchStudies: vi.fn(),
}))

vi.mock('../api/client', () => api)

function strategy(algorithmId: string, eligible = true): AlgorithmDefinition {
  return {
    kind: 'strategy', algorithm_id: algorithmId, algorithm_version: '1.0.0',
    source_hash: `sha256:${'1'.repeat(64)}`, name: algorithmId, input_schema: 'bars.v1',
    parameter_schema: {
      type: 'object', additionalProperties: false,
      properties: { checkpoint_interval: { type: 'integer', minimum: 64, default: 1024 } },
      required: ['checkpoint_interval'],
    },
    outputs: [], warmup: { kind: 'formula', expression: 'full history' }, causal: true,
    comparison_eligible: eligible,
    research_role: eligible ? 'formal_strategy' : 'example_strategy',
    strategy_family: eligible ? 'test-family' : 'example',
    catalog_algorithm_ids: eligible ? ['ALG-STR-001'] : [],
  }
}

const risk = {
  ...strategy('unified_risk_execution_overlay', false), kind: 'risk_filter',
  research_role: 'risk_filter',
  parameter_schema: {
    type: 'object', additionalProperties: false,
    properties: { leverage_allowed: { type: 'boolean', default: false } },
    required: ['leverage_allowed'],
  },
} as AlgorithmDefinition

const dataset = {
  dataset_id: 'TEST.5m', data_revision: `sha256:${'2'.repeat(64)}`,
  instrument: { exchange: 'TEST', symbol: 'T', product: 'T' }, timeframe: '5m',
  coverage: { first_bar_index: 0, last_bar_index: 99 },
} as DatasetMeta

describe('StrategyResearchPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listAlgorithms.mockResolvedValue([strategy('formal-a'), strategy('formal-b'), strategy('example', false), risk])
    api.createStrategyComparison.mockResolvedValue({ comparison_id: 'comparison-1', status: 'queued' })
    api.getStrategyComparison.mockResolvedValue({
      comparison_id: 'comparison-1', status: 'completed', progress: 1,
      total_count: 2, completed_count: 2, failed_count: 0, current_algorithm_id: null,
    })
    api.getStrategyComparisonResults.mockResolvedValue([{
      algorithm_id: 'formal-a', name: 'formal-a', strategy_family: 'test-family',
      parameters: { checkpoint_interval: 1024 }, status: 'completed', run_id: 'run-1',
      summary: { total_return: 0.1, max_drawdown: 0.02, trade_count: 4 },
    }])
    api.listStrategyComparisons.mockResolvedValue([])
    api.getBacktestEquity.mockResolvedValue([{ bar_index: 0, equity_i64: 100, drawdown: 0 }])
    api.listDatasets.mockResolvedValue({ catalog_revision: 1, datasets: [] })
    api.listResearchStudies.mockResolvedValue([])
  })

  it('selects only formal strategies and submits one common baseline batch', async () => {
    const wrapper = mount(StrategyResearchPanel, { props: { dataset } })
    await flushPromises()
    expect(wrapper.text()).toContain('一键回测所选 2 个策略')
    expect(wrapper.text()).not.toContain('example ·')
    await wrapper.get('.research-toolbar button:nth-last-of-type(1)').trigger('click')
    await flushPromises()
    expect(api.createStrategyComparison).toHaveBeenCalledWith(expect.objectContaining({
      dataset_id: 'TEST.5m', minimum_trade_count: 20,
      execution: expect.objectContaining({ slippage: { mode: 'ticks', value: 1 } }),
      strategies: [
        expect.objectContaining({ strategy: expect.objectContaining({ algorithm_id: 'formal-a' }) }),
        expect.objectContaining({ strategy: expect.objectContaining({ algorithm_id: 'formal-b' }) }),
      ],
      risk_overlay: expect.objectContaining({ algorithm: expect.objectContaining({ algorithm_id: 'unified_risk_execution_overlay' }) }),
    }))
    expect(wrapper.text()).toContain('10.00%')
    expect(wrapper.text()).toContain('run-1')
  })

  it('pins MACD composite observation time to the selected dataset timeframe', async () => {
    const composite = strategy('third_point_migration_macd_regime')
    composite.parameter_schema = {
      type: 'object', additionalProperties: false,
      properties: {
        checkpoint_interval: { type: 'integer', minimum: 64, default: 1024 },
        minimum_timeframe_minutes: { type: 'integer', minimum: 1, default: 60 },
      },
      required: ['checkpoint_interval', 'minimum_timeframe_minutes'],
    }
    api.listAlgorithms.mockResolvedValue([composite, risk])
    const wrapper = mount(StrategyResearchPanel, { props: { dataset } })
    await flushPromises()
    await wrapper.get('.research-toolbar button:nth-last-of-type(1)').trigger('click')
    await flushPromises()
    expect(api.createStrategyComparison.mock.calls[0]?.[0].strategies[0].parameters)
      .toEqual({ checkpoint_interval: 1024, minimum_timeframe_minutes: 5 })
  })

  it('restores history and limits simultaneous comparison to five runs', async () => {
    const manifest = {
      schema_version: 2, comparison_id: 'comparison-old', comparison_signature: `sha256:${'3'.repeat(64)}`,
      aggregator_version: '2.0.0', trace_id: 'trace', dataset: { dataset_id: dataset.dataset_id, data_revision: dataset.data_revision },
      range: { warmup_from_bar_index: 0, from_bar_index: 0, to_bar_index: 99 }, execution: {}, capital: {},
      random_seed: 1, minimum_trade_count: 20, strategies: [], strategy_count: 6, completed_count: 6, failed_count: 0,
      created_at: '2026-08-23T00:00:00Z',
    }
    api.listStrategyComparisons.mockResolvedValue([manifest])
    api.getStrategyComparisonResults.mockResolvedValue(Array.from({ length: 6 }, (_, index) => ({
      algorithm_id: `formal-${index}`, name: `formal-${index}`, strategy_family: 'family', parameters: {},
      status: 'completed', run_id: `run-${index}`, tier: 'profitable_candidate',
      summary: { total_return: index / 10, max_drawdown: .1, trade_count: 30, total_commission_i64: 0, total_slippage_i64: 0 },
    })))
    const wrapper = mount(StrategyResearchPanel, { props: { dataset } })
    await flushPromises()
    await wrapper.get('.history button:last-child').trigger('click')
    await flushPromises()
    const boxes = wrapper.findAll('.ranking-table tbody input[type="checkbox"]')
    for (const box of boxes) await box.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('5/5')
    expect(wrapper.text()).toContain('最多同时叠加 5 个策略')
  })
})
