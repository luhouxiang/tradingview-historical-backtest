import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DatasetMeta } from '../types/api'
import DatasetPanel from './DatasetPanel.vue'

const api = vi.hoisted(() => ({
  getDataset: vi.fn(),
  getJob: vi.fn(),
  getSourceFiles: vi.fn(),
  importSource: vi.fn(),
  listDatasets: vi.fn(),
  startDatasetScan: vi.fn(),
}))

vi.mock('../api/client', () => api)
vi.mock('../logging/logger', () => ({ logger: { info: vi.fn(), error: vi.fn() } }))

describe('DatasetPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getSourceFiles.mockResolvedValue([])
    api.listDatasets.mockResolvedValue({ catalog_revision: 0, datasets: [] })
    api.getJob.mockResolvedValue({ job_id: 'job-1', status: 'completed', progress: 1 })
    api.startDatasetScan.mockResolvedValue({ job_id: 'job-1', status: 'queued' })
  })

  it('polls a scan job and renders detected source metadata', async () => {
    const wrapper = mount(DatasetPanel)
    await flushPromises()
    api.getSourceFiles.mockResolvedValueOnce([
      {
        source_file_id: 'source-1',
        path: 'history/sample.txt',
        status: 'importable',
        sha256: 'sha256:test',
        size_bytes: 100,
        detected: { symbol: 'AO2609', timeframe: '5m' },
        issues: [],
      },
    ])
    await wrapper.get('.dataset-actions button').trigger('click')
    await flushPromises()
    expect(api.startDatasetScan).toHaveBeenCalledOnce()
    expect(api.getJob).toHaveBeenCalledWith('job-1')
    expect(wrapper.text()).toContain('AO2609')
    expect(wrapper.text()).toContain('5m')
  })

  it('selects the configured initial dataset automatically even when another dataset is listed first', async () => {
    const oldSummary = {
      dataset_id: 'SHFE.AOL9.5m', active_revision: `sha256:${'2'.repeat(64)}`,
      instrument: 'AOL9', timeframe: '5m', bar_count: 69289, status: 'ready',
    }
    const summary = {
      dataset_id: 'SHFE.AO2609.5m', active_revision: `sha256:${'1'.repeat(64)}`,
      instrument: 'AO2609', timeframe: '5m', bar_count: 17017, status: 'ready',
    }
    const metadata = {
      ...summary, data_revision: summary.active_revision,
      coverage: { first_trading_day: '2025-01-01', last_trading_day: '2025-02-01' },
      source: { format: 'tdx_txt', encoding: 'GB18030' },
    }
    api.listDatasets.mockResolvedValue({ catalog_revision: 2, datasets: [oldSummary, summary] })
    api.getDataset.mockResolvedValue(metadata)
    const wrapper = mount(DatasetPanel)
    await flushPromises()
    expect(api.getDataset).toHaveBeenCalledWith(summary.dataset_id, summary.active_revision)
    expect(wrapper.emitted('selected')?.[0]).toEqual([metadata])
  })

  it('reflects a dataset selected by the global keyboard picker', async () => {
    const external = {
      dataset_id: 'SHFE.AO2609.5m', data_revision: `sha256:${'3'.repeat(64)}`,
      coverage: { first_trading_day: '2026-01-01', last_trading_day: '2026-02-01' },
      source: { format: 'tdx_txt_v1', encoding: 'GB18030' },
    } as DatasetMeta
    const wrapper = mount(DatasetPanel, { props: { selectedDataset: null } })
    await flushPromises()
    await wrapper.setProps({ selectedDataset: external })
    expect(wrapper.get('.dataset-meta').text()).toContain('SHFE.AO2609.5m')
  })
})
