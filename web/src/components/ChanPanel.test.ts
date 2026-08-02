import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ChanPanel from './ChanPanel.vue'

const api = vi.hoisted(() => ({ listAlgorithms: vi.fn(), createCalculation: vi.fn(), getCalculation: vi.fn() }))
vi.mock('../api/client', () => api)

const definition = {
  kind: 'chan' as const, algorithm_id: 'chan_standard', algorithm_version: '1.0.0', source_hash: `sha256:${'2'.repeat(64)}`,
  name: '标准缠论', input_schema: 'bars.v1' as const, causal: true as const,
  parameter_schema: { type: 'object' as const, additionalProperties: false as const, required: ['min_fractal_gap'], properties: { min_fractal_gap: { type: 'integer' as const, minimum: 1, default: 5 } } },
  outputs: [{ name: 'bi', display_name: '笔', pane: 'main' as const, series_type: 'semantic_objects' as const, object_type: 'bi' as const }],
  warmup: { kind: 'formula' as const, expression: 'full history causal state' },
}
const dataset = { dataset_id: 'TEST.A1.1m', data_revision: `sha256:${'1'.repeat(64)}` } as never

describe('ChanPanel', () => {
  beforeEach(() => {
    api.listAlgorithms.mockReset().mockResolvedValue([definition])
    api.createCalculation.mockReset().mockResolvedValue({ job_id: 'job-chan', status: 'completed' })
  })

  it('submits causal_events and creates one StrategySource group', async () => {
    const wrapper = mount(ChanPanel, { props: { dataset, sources: [] } })
    await flushPromises()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(api.createCalculation).toHaveBeenCalledWith(expect.objectContaining({ calculation_mode: 'causal_events', parameters: { min_fractal_gap: 5 } }))
    const emitted = wrapper.emitted('update:sources')?.at(-1)?.[0] as Array<{ source_type: string }>
    expect(emitted).toHaveLength(1)
    expect(emitted[0]?.source_type).toBe('StrategySource')
    expect((emitted[0] as unknown as { category_visibility: object }).category_visibility).toEqual({ fractals: false, bi: true, zhongshu: true })
    expect(wrapper.get('[aria-label="缠论指标"]').text()).toContain('添加到主图')
  })
})
