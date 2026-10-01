import { describe, expect, it, vi } from 'vitest'
import type { CalculationResults, ChanCalculationResults, IndicatorCalculationResults } from '../types/api'
import { loadCalculationWindow } from './calculationWindow'

const identity = {
  request_id: 'request', job_id: 'job', cache_key: 'cache', dataset_id: 'AO', data_revision: 'revision',
  algorithm: { kind: 'indicator', algorithm_id: 'macd', algorithm_version: '1', source_hash: 'hash' },
  checksum: 'page-checksum', coverage: { first_bar_index: 0, last_bar_index: 0, returned_count: 0 },
} as const

function indicator(from: number, to: number): IndicatorCalculationResults {
  const indices = Array.from({ length: to - from + 1 }, (_, i) => from + i)
  return {
    ...identity, result_kind: 'indicator', bar_index: indices,
    values: { histogram: indices.map((i) => i === 0 ? null : i), signal: indices.map((i) => -i) },
  }
}

function chan(segmentIds: string[], revision = 1): ChanCalculationResults {
  return {
    ...identity, result_kind: 'chan', objects: {
      processed_bars: [], fractals: [], bi: [], bi_states: [], local_centers: [],
      center_connections: [], center_audit_events: [],
      center_monitors: [], divergences: [], trade_points: [],
      segments: segmentIds.map((id) => ({ object_id: id, object_revision: revision })),
      movement_states: [{ object_id: 'long-state', object_revision: revision }],
    },
  } as unknown as ChanCalculationResults
}

describe('calculation viewport pagination', () => {
  it.each([1, 5000, 5001, 12001])('loads all %i bars in <=5000-index pages without losing null warmup values', async (count) => {
    const fetch = vi.fn(async (_job: string, from: number, to: number) => indicator(from, to))
    const result = await loadCalculationWindow('job', 0, count - 1, () => true, fetch)
    expect(fetch).toHaveBeenCalledTimes(Math.ceil(count / 5000))
    expect(fetch.mock.calls.every(([, from, to]) => to - from + 1 <= 5000)).toBe(true)
    expect(result?.result_kind).toBe('indicator')
    if (result?.result_kind !== 'indicator') throw new Error('wrong result kind')
    expect(result.bar_index).toEqual(Array.from({ length: count }, (_, i) => i))
    expect(result.values.histogram).toEqual(result.bar_index.map((i) => i === 0 ? null : i))
    expect(result).not.toHaveProperty('checksum')
  })

  it('merges left and right segments and deduplicates centers spanning page boundaries', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(chan(['left', 'spanning']))
      .mockResolvedValueOnce(chan(['spanning', 'right'], 2))
    const result = await loadCalculationWindow('job', 100, 6100, () => true, fetch)
    expect(fetch.mock.calls).toEqual([['job', 100, 5099], ['job', 5100, 6100]])
    if (result?.result_kind !== 'chan') throw new Error('wrong result kind')
    expect(result.objects.segments.map((s) => s.object_id)).toEqual(['left', 'spanning', 'right'])
    expect(result.objects.movement_states).toEqual([{ object_id: 'long-state', object_revision: 2 }])
  })

  it('rejects mixed data revisions and malformed indicator columns', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(indicator(0, 4999))
      .mockResolvedValueOnce({ ...indicator(5000, 5000), data_revision: 'different' })
    await expect(loadCalculationWindow('job', 0, 5000, () => true, fetch)).rejects.toThrow('数据身份不一致')
    await expect(loadCalculationWindow('job', 0, 1, () => true, async () => ({
      ...indicator(0, 1), values: { histogram: [1] },
    }))).rejects.toThrow('列长度不一致')
  })

  it('stops obsolete pagination and never publishes a partial window', async () => {
    let current = true
    const fetch = vi.fn(async () => { current = false; return chan(['left']) })
    expect(await loadCalculationWindow('job', 0, 12000, () => current, fetch)).toBeNull()
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('propagates failed pages and allows the same viewport to be retried', async () => {
    const fetch = vi.fn<(...args: [string, number, number]) => Promise<CalculationResults>>()
      .mockResolvedValueOnce(chan(['left'])).mockRejectedValueOnce(new Error('offline'))
    await expect(loadCalculationWindow('job', 0, 6000, () => true, fetch)).rejects.toThrow('offline')
    fetch.mockImplementation(async () => chan(['left', 'right']))
    const result = await loadCalculationWindow('job', 0, 6000, () => true, fetch)
    expect(result?.result_kind).toBe('chan')
  })
})
