import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AlgorithmDefinition, DatasetMeta, SeriesSource, StrategySource } from '../types/api'
import IndicatorManagerPanel from './IndicatorManagerPanel.vue'

const api = vi.hoisted(() => ({ listAlgorithms: vi.fn(), createCalculation: vi.fn(), getCalculation: vi.fn() }))
vi.mock('../api/client', () => api)

function definition(algorithmId: string, kind: 'indicator' | 'chan' = 'indicator'): AlgorithmDefinition {
  return {
    kind, algorithm_id: algorithmId, algorithm_version: '1.0.0', source_hash: `sha256:${'d'.repeat(64)}`,
    name: algorithmId === 'macd' ? 'MACD' : algorithmId === 'chan_engineering' ? '工程缠论' : `Indicator ${algorithmId}`,
    input_schema: 'bars.v1', causal: true,
    parameter_schema: {
      type: 'object', additionalProperties: false, required: ['period'],
      properties: { period: { type: 'integer', minimum: 1, maximum: 1000, default: 20 } },
    },
    outputs: [{
      name: algorithmId, display_name: algorithmId.toUpperCase(), pane: kind === 'chan' ? 'main' : 'indicator',
      series_type: kind === 'chan' ? 'semantic_objects' : 'line', ...(kind === 'chan' ? { object_type: 'bi' as const } : {}),
    }],
    warmup: { kind: 'formula', expression: 'period - 1' },
  }
}

const dataset = { dataset_id: 'SHFE.AOL9.5m', data_revision: `sha256:${'1'.repeat(64)}` } as DatasetMeta

describe('IndicatorManagerPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    api.createCalculation.mockResolvedValue({ job_id: 'job-new', status: 'completed' })
  })

  it('keeps a catalog of more than one hundred indicators navigable through common, all, category and search views', async () => {
    const definitions = [definition('ma'), definition('macd'), definition('atr'), definition('chan_engineering', 'chan')]
    for (let index = 0; index < 116; index += 1) definitions.push(definition(`custom_${index}`))
    api.listAlgorithms.mockResolvedValue(definitions)
    const wrapper = mount(IndicatorManagerPanel, { props: { dataset, indicatorSources: [], strategySources: [] } })
    await flushPromises()

    expect(wrapper.get('[aria-label="指标范围"]').text()).toContain('全部 120')
    expect(wrapper.findAll('.indicator-catalog-row')).toHaveLength(4)

    await wrapper.get('[aria-label="指标范围"] button:nth-child(2)').trigger('click')
    expect(wrapper.findAll('.indicator-catalog-row')).toHaveLength(120)
    await wrapper.get('[aria-label="搜索指标"]').setValue('custom_87')
    expect(wrapper.findAll('.indicator-catalog-row')).toHaveLength(1)
    expect(wrapper.get('.indicator-catalog-row').text()).toContain('custom_87')

    await wrapper.get('[aria-label="搜索指标"]').setValue('')
    await wrapper.get('[aria-label="收藏 Indicator custom_87"]').trigger('click')
    await wrapper.get('[aria-label="指标范围"] button:first-child').trigger('click')
    expect(wrapper.text()).toContain('Indicator custom_87')
    expect(JSON.parse(localStorage.getItem('tvbt.indicator-favorites.v1') ?? '[]')).toContain('custom_87')
  })

  it('adds normal and Chan indicators with their authoritative calculation modes', async () => {
    api.listAlgorithms.mockResolvedValue([definition('macd'), definition('chan_engineering', 'chan')])
    const wrapper = mount(IndicatorManagerPanel, { props: { dataset, indicatorSources: [], strategySources: [] } })
    await flushPromises()
    await wrapper.get('[aria-label="添加 MACD"]').trigger('click')
    await wrapper.get('[aria-label="添加 工程缠论"]').trigger('click')
    await flushPromises()

    expect(api.createCalculation.mock.calls.map((call) => call[0].calculation_mode)).toEqual(['full_history', 'causal_events'])
    const indicatorSources = wrapper.emitted('update:indicator-sources')?.at(-1)?.[0] as SeriesSource[]
    const strategySources = wrapper.emitted('update:strategy-sources')?.at(-1)?.[0] as StrategySource[]
    expect(indicatorSources[0]).toMatchObject({ source_type: 'SeriesSource', parameters: { period: 20 } })
    expect(strategySources[0]).toMatchObject({
      source_type: 'StrategySource', parameters: { period: 20 },
      category_visibility: { fractals: false, bi: true, segments: true, zhongshu: true, segment_zhongshu: true, divergences: true, trade_points: true },
    })
  })

  it('shows active instances as compact expandable cards and filters the current list', async () => {
    const macd = definition('macd')
    const chan = definition('chan_engineering', 'chan')
    api.listAlgorithms.mockResolvedValue([macd, chan])
    const indicatorSource: SeriesSource = {
      source_type: 'SeriesSource', source_id: 'series-1', definition: macd, parameters: { period: 20 }, job_id: 'job-1', status: 'completed',
    }
    const strategySource: StrategySource = {
      source_type: 'StrategySource', source_id: 'strategy-1', definition: chan, parameters: { period: 5 }, job_id: 'job-2', status: 'completed',
      visible: true, category_visibility: { fractals: false, bi: true, segments: true, zhongshu: true, segment_zhongshu: true, divergences: true, trade_points: true },
    }
    const wrapper = mount(IndicatorManagerPanel, { props: { dataset, indicatorSources: [indicatorSource], strategySources: [strategySource] } })
    await flushPromises()
    await wrapper.get('[aria-label="指标范围"] button:nth-child(3)').trigger('click')
    expect(wrapper.findAll('.indicator-current-card')).toHaveLength(2)
    expect(wrapper.findAll('.indicator-current-card[open]')).toHaveLength(0)
    await wrapper.get('[aria-label="搜索指标"]').setValue('形态结构')
    expect(wrapper.findAll('.indicator-current-card')).toHaveLength(1)
    expect(wrapper.get('.indicator-current-card').text()).toContain('工程缠论')
  })

  it('applies staged styles from the More dialog without recalculating the indicator', async () => {
    const macd = definition('macd')
    api.listAlgorithms.mockResolvedValue([macd])
    const indicatorSource: SeriesSource = {
      source_type: 'SeriesSource', source_id: 'series-1', definition: macd,
      parameters: { period: 20 }, job_id: 'job-1', status: 'completed',
    }
    const wrapper = mount(IndicatorManagerPanel, {
      props: { dataset, indicatorSources: [indicatorSource], strategySources: [] },
    })
    await flushPromises()
    await wrapper.get('[aria-label="指标范围"] button:nth-child(3)').trigger('click')
    await wrapper.get('[aria-label="MACD 更多样式"]').trigger('click')
    await wrapper.get('[aria-label="设置 MACD 样式"]').trigger('click')
    await wrapper.get('[aria-label="选择颜色 #ab47bc"]').trigger('click')
    await wrapper.get('[aria-label="线宽 3"]').trigger('click')
    await wrapper.get('[aria-label="虚线"]').trigger('click')
    expect(wrapper.emitted('update:indicator-sources')).toBeUndefined()
    expect(api.createCalculation).not.toHaveBeenCalled()

    await wrapper.get('.indicator-style-dialog footer .primary').trigger('click')
    const sources = wrapper.emitted('update:indicator-sources')?.at(-1)?.[0] as SeriesSource[]
    expect(sources[0]?.style?.outputs.macd).toMatchObject({
      color: '#ab47bc', line_width: 3, line_style: 'dashed', visible: true,
    })
    expect(api.createCalculation).not.toHaveBeenCalled()
  })
})
