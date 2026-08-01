import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { DrawingObject } from '../drawing/model'
import ObjectTreePanel from './ObjectTreePanel.vue'

function drawing(id: string, order: number): DrawingObject {
  return {
    id, name: id, type: 'rectangle', pane_id: 'main', visible: true, locked: false,
    z_band: 600, order_in_band: order, style: { color: '#2962ff', line_width: 1, fill_opacity: .15 },
    anchors: [{ time: 1, price_i64: 1, price_scale: 1 }, { time: 2, price_i64: 2, price_scale: 1 }],
    revision: 1, created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z',
  }
}

describe('ObjectTreePanel', () => {
  it('keeps object order visible and exposes rename, hide, lock, reorder and delete actions', async () => {
    const wrapper = mount(ObjectTreePanel, { props: { drawings: [drawing('upper', 1), drawing('lower', 0)], sources: [], strategySources: [], selectedId: 'lower' } })
    const nodes = wrapper.findAll('[data-object-type="DrawingObject"]')
    expect(nodes.map((node) => node.get('input').element.value)).toEqual(['lower', 'upper'])
    expect(nodes[0]?.classes()).toContain('selected')
    await nodes[0]?.findAll('button')[0]?.trigger('click')
    expect(wrapper.emitted('patchDrawing')?.at(-1)).toEqual(['lower', { visible: false }])
    await nodes[0]?.findAll('button')[1]?.trigger('click')
    expect(wrapper.emitted('patchDrawing')?.at(-1)).toEqual(['lower', { locked: true }])
    await nodes[0]?.findAll('button')[3]?.trigger('click')
    expect(wrapper.emitted('reorderDrawing')?.at(-1)).toEqual(['lower', 1])
    await nodes[0]?.findAll('button')[4]?.trigger('click')
    expect(wrapper.emitted('removeDrawing')?.at(-1)).toEqual(['lower'])
  })

  it('shows one StrategySource with category toggles instead of semantic object nodes', async () => {
    const strategy = {
      source_type: 'StrategySource', source_id: 'chan-1', definition: { name: '标准缠论' },
      parameters: {}, job_id: 'job-1', status: 'completed', visible: true,
      category_visibility: { fractals: true, bi: true, zhongshu: true },
    }
    const wrapper = mount(ObjectTreePanel, { props: { drawings: [], sources: [], strategySources: [strategy] as never, selectedId: null } })
    expect(wrapper.findAll('[data-object-type="StrategySource"]')).toHaveLength(1)
    expect(wrapper.findAll('[data-object-type="ChanObject"]')).toHaveLength(0)
    expect(wrapper.findAll('.strategy-node label')).toHaveLength(3)
    await wrapper.get('.strategy-node label input').trigger('change')
    expect(wrapper.emitted('patchStrategy')?.at(-1)?.[0]).toBe('chan-1')
  })
})
