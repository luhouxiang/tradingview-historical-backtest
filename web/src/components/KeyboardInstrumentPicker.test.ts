import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DatasetMeta } from '../types/api'
import KeyboardInstrumentPicker from './KeyboardInstrumentPicker.vue'

const api = vi.hoisted(() => ({
  getDataset: vi.fn(), getJob: vi.fn(), getSourceFiles: vi.fn(), importSource: vi.fn(),
  listDatasets: vi.fn(), startDatasetScan: vi.fn(),
}))
vi.mock('../api/client', () => api)

const source = {
  source_file_id: 'source-aol9', path: 'history/30#AOL9.txt', status: 'imported',
  sha256: 'sha256:test', size_bytes: 100,
  detected: { exchange: 'SHFE', symbol: 'AOL9', timeframe: '5m', display_name: '氧化铝加权' }, issues: [],
}
const summary = {
  dataset_id: 'SHFE.AOL9.5m', active_revision: `sha256:${'1'.repeat(64)}`,
  instrument: 'AOL9', timeframe: '5m', bar_count: 100, status: 'ready',
}
const activeDataset = {
  dataset_id: summary.dataset_id, data_revision: summary.active_revision,
  coverage: { first_bar_index: 0, last_bar_index: 71148 },
} as unknown as DatasetMeta

describe('KeyboardInstrumentPicker', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getSourceFiles.mockResolvedValue([source])
    api.listDatasets.mockResolvedValue({ catalog_revision: 1, datasets: [summary] })
    api.startDatasetScan.mockResolvedValue({ job_id: 'scan-1', status: 'queued' })
    api.getJob.mockResolvedValue({ job_id: 'scan-1', status: 'completed', progress: 1 })
    api.getDataset.mockResolvedValue({ dataset_id: summary.dataset_id, data_revision: summary.active_revision })
  })

  it('opens on direct typing, fuzzy matches and loads the selected dataset with Enter', async () => {
    const wrapper = mount(KeyboardInstrumentPicker)
    await flushPromises()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }))
    await flushPromises()
    expect(wrapper.get('[aria-label="键盘精灵"]').element).toBeTruthy()
    expect((wrapper.get('[aria-label="标的搜索"]').element as HTMLInputElement).value).toBe('a')
    expect(wrapper.get('[role="option"]').text()).toContain('AOL9')
    await wrapper.get('[aria-label="标的搜索"]').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(api.getDataset).toHaveBeenCalledWith(summary.dataset_id, summary.active_revision)
    expect(wrapper.emitted('selected')?.[0]?.[0]).toMatchObject({ dataset_id: summary.dataset_id })
    expect(wrapper.find('[aria-label="键盘精灵"]').exists()).toBe(false)
  })

  it('does not intercept typing in an unrelated input', async () => {
    const wrapper = mount({ components: { KeyboardInstrumentPicker }, template: '<input class="other"><KeyboardInstrumentPicker />' })
    const input = wrapper.get('.other').element
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true }))
    await flushPromises()
    expect(wrapper.find('[aria-label="键盘精灵"]').exists()).toBe(false)
  })

  it('previews kk bar navigation and emits the absolute K-line index on Enter', async () => {
    const wrapper = mount(KeyboardInstrumentPicker, { props: { dataset: activeDataset } })
    await flushPromises()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k' }))
    await flushPromises()
    await wrapper.get('[aria-label="标的搜索"]').setValue('kk357')
    expect(wrapper.get('.keyboard-picker-bar-command').text()).toContain('定位到 357')
    expect(wrapper.text()).not.toContain('无匹配标的')
    await wrapper.get('[aria-label="标的搜索"]').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('focus-bar')?.[0]).toEqual([357])
    expect(wrapper.find('[aria-label="键盘精灵"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each([
    ['kkbeg', 100, '第一根'],
    ['kkbegin', 100, '第一根'],
    ['kkend', 500, '最后一根'],
    ['KKEND', 500, '最后一根'],
  ])('previews %s and routes to the active coverage endpoint', async (command, expectedIndex, description) => {
    const wrapper = mount(KeyboardInstrumentPicker, { props: {
      dataset: { ...activeDataset, coverage: { ...activeDataset.coverage, first_bar_index: 100, last_bar_index: 500 } },
    } })
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k' }))
    await flushPromises()
    await wrapper.get('[aria-label="标的搜索"]').setValue(command)
    expect(wrapper.get('.keyboard-picker-bar-command').text()).toContain(`定位到${description} K 线（${expectedIndex}）`)
    await wrapper.get('[aria-label="标的搜索"]').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('focus-bar')?.[0]).toEqual([expectedIndex])
    wrapper.unmount()
  })

  it('rejects K-line indexes outside the active dataset', async () => {
    const wrapper = mount(KeyboardInstrumentPicker, { props: { dataset: activeDataset } })
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k' }))
    await flushPromises()
    await wrapper.get('[aria-label="标的搜索"]').setValue('kk71149')
    expect(wrapper.text()).toContain('K 线编号须在 0–71148 之间')
    await wrapper.get('[aria-label="标的搜索"]').trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('focus-bar')).toBeUndefined()
    wrapper.unmount()
  })
})
