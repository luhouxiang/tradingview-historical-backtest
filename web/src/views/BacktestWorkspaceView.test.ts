import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DatasetMeta } from '../types/api'
import BacktestWorkspaceView from './BacktestWorkspaceView.vue'

const revision = `sha256:${'a'.repeat(64)}`
const api = vi.hoisted(() => ({ getDataset: vi.fn() }))
vi.mock('../api/client', () => api)
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: { dataset_id: 'SHFE.AOL9.5m', revision } }),
}))

class FakeBroadcastChannel {
  static messages: unknown[] = []
  onmessage: ((event: MessageEvent) => void) | null = null
  constructor(readonly name: string) {}
  postMessage(message: unknown): void { FakeBroadcastChannel.messages.push(structuredClone(message)) }
  close(): void {}
}

const BacktestStub = defineComponent({
  name: 'BacktestPanel',
  emits: ['completed', 'focus-trade'],
  setup(_, { emit }) {
    return () => h('div', [
      h('button', { class: 'complete', onClick: () => emit('completed', {
        source_type: 'StrategyRunSource', source_id: 'source-1', run_id: 'run-1',
        status: 'completed', visible: true, objects: [], signals: [],
      }) }, 'complete'),
      h('button', { class: 'focus', onClick: () => emit('focus-trade', {
        trade_id: 'trade-1', entry_bar_index: 30,
      }) }, 'focus'),
    ])
  },
})

describe('BacktestWorkspaceView', () => {
  beforeEach(() => {
    FakeBroadcastChannel.messages = []
    vi.stubGlobal('BroadcastChannel', FakeBroadcastChannel)
    api.getDataset.mockResolvedValue({
      dataset_id: 'SHFE.AOL9.5m', data_revision: revision, timeframe: '5m',
      instrument: { symbol: 'AOL9' },
    } as DatasetMeta)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('loads the exact bound revision and forwards run and trade events to the chart window', async () => {
    const wrapper = mount(BacktestWorkspaceView, { global: { stubs: { BacktestPanel: BacktestStub } } })
    await flushPromises()
    expect(api.getDataset).toHaveBeenCalledWith('SHFE.AOL9.5m', revision)
    expect(wrapper.text()).toContain('已绑定 SHFE.AOL9.5m · 5m')

    await wrapper.get('.complete').trigger('click')
    await wrapper.get('.focus').trigger('click')
    expect(FakeBroadcastChannel.messages).toEqual([
      expect.objectContaining({ type: 'run-completed', dataset_id: 'SHFE.AOL9.5m', data_revision: revision }),
      expect.objectContaining({ type: 'focus-trade', dataset_id: 'SHFE.AOL9.5m', data_revision: revision, trade: { trade_id: 'trade-1', entry_bar_index: 30 } }),
    ])
  })
})
