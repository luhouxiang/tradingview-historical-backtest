import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DatasetMeta, StrategySource } from '../types/api'
import ReplayPanel from './ReplayPanel.vue'

const api = vi.hoisted(() => ({ createReplay: vi.fn(), getReplay: vi.fn(), getReplayEvents: vi.fn() }))
vi.mock('../api/client', () => api)

const dataset = {
  dataset_id: 'TEST.A1.1m', data_revision: `sha256:${'1'.repeat(64)}`,
  coverage: { first_bar_index: 0, last_bar_index: 20 },
} as DatasetMeta
const source = {
  source_id: 'chan-1', status: 'completed', parameters: {},
  definition: { kind: 'chan', algorithm_id: 'chan', algorithm_version: '1', source_hash: `sha256:${'2'.repeat(64)}` },
} as StrategySource

describe('ReplayPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    api.createReplay.mockResolvedValue({ replay_id: 'replay-1', status: 'completed', progress: 1 })
    api.getReplayEvents.mockResolvedValue({
      replay_id: 'replay-1', event_count: 1, checksum: `sha256:${'3'.repeat(64)}`,
      events: [{ event_seq: 1, known_at_bar_index: 2, object_type: 'fractal', object_id: 'f-1', operation: 'upsert', object_revision: 1, payload: { object_id: 'f-1', bar_index: 1 } }],
    })
  })

  it('loads events once and moves the cursor locally', async () => {
    const wrapper = mount(ReplayPanel, { props: { dataset, source } })
    await wrapper.get('.replay-range button').trigger('click')
    await flushPromises()
    expect(api.createReplay).toHaveBeenCalledTimes(1)
    expect(api.getReplayEvents).toHaveBeenCalledWith('replay-1', 0, 20)
    const step = wrapper.findAll('.replay-controls button').at(1)
    await step?.trigger('click')
    expect(api.createReplay).toHaveBeenCalledTimes(1)
    expect(api.getReplayEvents).toHaveBeenCalledTimes(1)
    const updates = wrapper.emitted('update') ?? []
    expect(updates.at(-1)?.[0]).toMatchObject({ cursor: 1 })
    wrapper.unmount()
  })

  it('restores the cached request and cursor after refresh', async () => {
    localStorage.setItem(`tvbt.replay.${dataset.dataset_id}.${dataset.data_revision}`, JSON.stringify({
      algorithm_id: 'chan', source_hash: source.definition.source_hash,
      from: 0, to: 20, cursor: 7, speed: 2,
    }))
    const wrapper = mount(ReplayPanel, { props: { dataset, source } })
    await flushPromises()
    expect(api.createReplay).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('update')?.at(-1)?.[0]).toMatchObject({ cursor: 7 })
    expect((wrapper.get('[aria-label="回放速度"]').element as HTMLSelectElement).value).toBe('2')
    wrapper.unmount()
  })
})
