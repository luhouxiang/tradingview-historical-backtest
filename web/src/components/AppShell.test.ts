import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DatasetMeta } from '../types/api'
import AppShell from './AppShell.vue'

const api = vi.hoisted(() => ({
  getLayout: vi.fn(), getDrawings: vi.fn(), putLayout: vi.fn(), putDrawings: vi.fn(),
  listAlgorithms: vi.fn(), createCalculation: vi.fn(), getCalculation: vi.fn(),
  createReplay: vi.fn(), getReplay: vi.fn(), getReplayEvents: vi.fn(),
  createBacktest: vi.fn(), getBacktest: vi.fn(), getBacktestSummary: vi.fn(), getBacktestTrades: vi.fn(), getBacktestEquity: vi.fn(),
  createStudy: vi.fn(), getStudy: vi.fn(), getStudyEvaluations: vi.fn(),
}))
vi.mock('../api/client', () => ({
  ...api,
  ApiError: class ApiError extends Error { constructor(readonly code: string, message: string) { super(message) } },
}))

const dataset = {
  dataset_id: 'SHFE.AO2609.5m', data_revision: `sha256:${'1'.repeat(64)}`,
  price: { price_scale: 1 },
} as DatasetMeta

const ChartStub = defineComponent({
  name: 'ChartGroup',
  setup(_, { expose }) {
    expose({
      snapshotLayout: () => ({ panes: [{ id: 'price', kind: 'price', weight: 6, minHeight: 240, visible: true, collapsed: false, order: 0 }] }),
      restoreLayout: vi.fn(),
    })
    return () => h('div', 'chart')
  },
})

describe('AppShell', () => {
  beforeEach(() => vi.clearAllMocks())
  it('renders the empty TradingView-style workspace', () => {
    const wrapper = mount(AppShell, {
      props: { health: 'ok' },
      global: {
        stubs: {
          ChartGroup: { template: '<div>选择历史数据集后开始</div>' },
          DatasetPanel: { template: '<div />' },
        },
      },
    })
    expect(wrapper.get('[aria-label="图表工作区"]').text()).toContain('选择历史数据集后开始')
    expect(wrapper.get('[aria-label="绘图工具栏"]').element).toBeTruthy()
    expect(wrapper.get('[aria-label="右侧面板"]').element).toBeTruthy()
    expect(wrapper.get('[aria-label="底部面板"]').element).toBeTruthy()
  })

  it('compresses the chart when dock panels are expanded and keeps panel sizes bounded', async () => {
    const wrapper = mount(AppShell, {
      props: { health: 'ok' },
      global: { stubs: { ChartGroup: { template: '<div />' }, DatasetPanel: { template: '<div />' } } },
    })
    expect(wrapper.get('.workspace-body').attributes('style')).toContain('320px')
    await wrapper.get('.dock-close').trigger('click')
    expect(wrapper.find('[aria-label="右侧面板"]').exists()).toBe(false)
    await wrapper.get('.reopen-right').trigger('click')
    expect(wrapper.find('[aria-label="右侧面板"]').exists()).toBe(true)
    const toggle = wrapper.findAll('.bottom-dock nav button').at(4)
    await toggle?.trigger('click')
    expect(wrapper.get('.app-shell').attributes('style')).toContain('260px')
    expect(wrapper.get('.bottom-dock').classes()).toContain('expanded')
  })

  it('restores and saves layout, drawings, panels and fixed anchors with optimistic revisions', async () => {
    const drawing = {
      id: 'drawing-1', name: '矩形 1', type: 'rectangle', pane_id: 'main', visible: true, locked: false,
      z_band: 600, order_in_band: 0, style: { color: '#2962ff', line_width: 1, fill_opacity: .15 },
      anchors: [{ time: 1000, price_i64: 20, price_scale: 10 }, { time: 2000, price_i64: 30, price_scale: 10 }],
      revision: 1, created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z',
    }
    api.getLayout.mockResolvedValue({
      schema_version: 1, profile_id: 'default', layout_id: 'default-three-pane', revision: 3,
      panes: [{ id: 'price', role: 'price', weight: 6, min_height: 240, visible: true, collapsed: false, order: 0 }],
      right_panel: { width: 400, collapsed: false, active_tab: 'object_tree' },
      bottom_panel: { height: 300, collapsed: true, active_tab: 'tasks' }, object_order: [], series_sources: [], strategy_sources: [], updated_at: '2026-08-01T00:00:00Z',
    })
    api.getDrawings.mockResolvedValue({
      schema_version: 1, profile_id: 'default', layout_id: 'default-three-pane', dataset_id: dataset.dataset_id,
      data_revision: dataset.data_revision, revision: 4, drawings: [drawing], updated_at: '2026-08-01T00:00:00Z',
    })
    api.listAlgorithms.mockResolvedValue([])
    api.putLayout.mockImplementation(async (_profile: string, _layout: string, _revision: number, value: object) => ({ ...value, revision: 4 }))
    api.putDrawings.mockImplementation(async (_profile: string, _layout: string, _dataset: string, _revision: number, value: object) => ({ ...value, revision: 5 }))
    const wrapper = mount(AppShell, {
      props: { health: 'ok' },
      global: { stubs: { ChartGroup: ChartStub, DatasetPanel: true } },
    })
    wrapper.findComponent({ name: 'DatasetPanel' }).vm.$emit('selected', dataset)
    await flushPromises()
    expect(wrapper.get('.workspace-body').attributes('style')).toContain('400px')
    expect(wrapper.findAll('[data-object-type="DrawingObject"]')).toHaveLength(1)
    await wrapper.get('[title="保存工作区"]').trigger('click')
    await flushPromises()
    expect(api.putLayout).toHaveBeenCalledWith('default', 'default-three-pane', 3, expect.any(Object))
    expect(api.putDrawings).toHaveBeenCalledWith('default', 'default-three-pane', dataset.dataset_id, 4, expect.objectContaining({ drawings: [drawing] }))
    const saved = api.putDrawings.mock.calls[0]?.[4]
    expect(JSON.stringify(saved)).not.toContain('"x"')
    expect(JSON.stringify(saved)).not.toContain('"y"')
  })
})
