import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { AlgorithmDefinition, SeriesSource } from '../types/api'
import IndicatorStyleDialog from './IndicatorStyleDialog.vue'

const definition: AlgorithmDefinition = {
  kind: 'indicator', algorithm_id: 'ma', algorithm_version: '1.0.0', source_hash: `sha256:${'a'.repeat(64)}`,
  name: 'Moving Average', input_schema: 'bars.v1', causal: true,
  parameter_schema: { type: 'object', additionalProperties: false, required: [], properties: {} },
  outputs: [{ name: 'ma', display_name: 'MA', pane: 'main', series_type: 'line' }],
  warmup: { kind: 'formula', expression: '0' },
}

function source(): SeriesSource {
  return {
    source_type: 'SeriesSource', source_id: 'series-1', definition,
    parameters: {}, job_id: 'job-1', status: 'completed',
  }
}

describe('IndicatorStyleDialog', () => {
  it('keeps edits local and discards them on cancel', async () => {
    const wrapper = mount(IndicatorStyleDialog, { props: { source: source() }, attachTo: document.body })
    await wrapper.get('[aria-label="设置 MA 样式"]').trigger('click')
    await wrapper.get('[aria-label="选择颜色 #ab47bc"]').trigger('click')
    await wrapper.get('[aria-label="线宽 3"]').trigger('click')
    await wrapper.get('[aria-label="虚线"]').trigger('click')
    expect(wrapper.emitted('confirm')).toBeUndefined()
    await wrapper.get('footer button:first-child').trigger('click')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
    expect(wrapper.emitted('confirm')).toBeUndefined()
    wrapper.unmount()
  })

  it('emits the complete selected style only after confirm', async () => {
    const wrapper = mount(IndicatorStyleDialog, { props: { source: source() } })
    await wrapper.get('[aria-label="设置 MA 样式"]').trigger('click')
    await wrapper.get('[aria-label="选择颜色 #ab47bc"]').trigger('click')
    await wrapper.get('[aria-label="线宽 3"]').trigger('click')
    await wrapper.get('[aria-label="虚线"]').trigger('click')
    await wrapper.get('[aria-label="MA 不透明度"]').setValue('70')
    await wrapper.get('footer .primary').trigger('click')
    expect(wrapper.emitted('confirm')?.[0]?.[0]).toEqual({
      outputs: { ma: { color: '#ab47bc', line_width: 3, line_style: 'dashed', opacity: 0.7, visible: true } },
    })
  })
})
