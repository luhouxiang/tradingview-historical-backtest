import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DatasetMeta } from '../types/api'
import BacktestPanel from './BacktestPanel.vue'

const api = vi.hoisted(() => ({
  listAlgorithms: vi.fn(), createBacktest: vi.fn(), getBacktest: vi.fn(),
  getBacktestSummary: vi.fn(), getBacktestTrades: vi.fn(), getBacktestEquity: vi.fn(),
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
const dataset = {
  dataset_id: 'TEST.A1.5m', data_revision: `sha256:${'2'.repeat(64)}`,
  coverage: { first_bar_index: 0, last_bar_index: 100 },
} as DatasetMeta

describe('BacktestPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listAlgorithms.mockResolvedValue([strategy])
    api.createBacktest.mockResolvedValue({ run_id: 'run-1', run_signature: `sha256:${'3'.repeat(64)}`, status: 'queued' })
    api.getBacktest.mockResolvedValue({ run_id: 'run-1', status: 'completed', progress: 1 })
    api.getBacktestSummary.mockResolvedValue({ total_return: .1, max_drawdown: .02, trade_count: 2, win_rate: .5, sharpe: 1.2, total_commission_i64: 600 })
    api.getBacktestTrades.mockResolvedValue({ rows: [{ trade_id: 'trade-1', side: 'short', entry_bar_index: 10, entry_price_i64: 100, exit_bar_index: 20, exit_price_i64: 90, net_pnl_i64: 10 }], next_cursor: null })
    api.getBacktestEquity.mockResolvedValue([{ bar_index: 0, equity_i64: 100 }, { bar_index: 1, equity_i64: 110 }])
  })

  it('creates a formal run and renders summary, trades and equity views', async () => {
    const wrapper = mount(BacktestPanel, { props: { dataset, view: 'backtest' } })
    await flushPromises()
    await wrapper.get('.backtest-controls button').trigger('click')
    await flushPromises()
    expect(api.createBacktest).toHaveBeenCalledWith(expect.objectContaining({
      execution: expect.objectContaining({ fill_timing: 'next_bar_open' }),
    }))
    expect(wrapper.get('.summary-grid').text()).toContain('10.00%')
    await wrapper.setProps({ view: 'trades' })
    expect(wrapper.get('.trade-table').text()).toContain('trade-1')
    await wrapper.setProps({ view: 'equity' })
    expect(wrapper.get('.equity-chart polyline').attributes('points')).not.toBe('')
  })
})
