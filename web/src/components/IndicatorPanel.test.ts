import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AlgorithmDefinition, DatasetMeta, SeriesSource } from '../types/api'
import IndicatorPanel from './IndicatorPanel.vue'

const api = vi.hoisted(() => ({ listAlgorithms: vi.fn(), createCalculation: vi.fn(), getCalculation: vi.fn() }))
vi.mock('../api/client', () => api)

const algorithm: AlgorithmDefinition = {
  kind: 'indicator', algorithm_id: 'ma', algorithm_version: '1.0.0', source_hash: `sha256:${'2'.repeat(64)}`,
  name: 'Moving Average', input_schema: 'bars.v1', causal: true,
  parameter_schema: {
    type: 'object', additionalProperties: false, required: ['period', 'source'],
    properties: {
      period: { type: 'integer', minimum: 1, maximum: 10000, default: 20 },
      source: { type: 'string', enum: ['close'], default: 'close' },
    },
  },
  outputs: [{ name: 'ma', display_name: 'MA', pane: 'main', series_type: 'line' }],
  warmup: { kind: 'formula', expression: 'period - 1' },
}

const dataset = {
  dataset_id: 'SHFE.AO2609.5m', data_revision: `sha256:${'1'.repeat(64)}`,
} as DatasetMeta

describe('IndicatorPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.listAlgorithms.mockResolvedValue([algorithm])
    api.createCalculation.mockResolvedValue({ request_id: 'r', job_id: 'job-1', status: 'completed', progress: 1 })
  })

  it('creates a SeriesSource with explicit schema defaults and can delete it', async () => {
    const wrapper = mount(IndicatorPanel, { props: { dataset, sources: [] } })
    await flushPromises()
    await wrapper.get('.indicator-add button').trigger('click')
    await flushPromises()
    expect(api.createCalculation).toHaveBeenCalledWith(expect.objectContaining({
      parameters: { period: 20, source: 'close' }, calculation_mode: 'full_history',
    }))
    const emitted = wrapper.emitted('update:sources')?.at(-1)?.[0] as SeriesSource[]
    expect(emitted[0]).toMatchObject({ source_type: 'SeriesSource', job_id: 'job-1' })
    await wrapper.setProps({ sources: emitted })
    await wrapper.get('.indicator-card button:last-child').trigger('click')
    expect(wrapper.emitted('update:sources')?.at(-1)?.[0]).toEqual([])
  })
})
