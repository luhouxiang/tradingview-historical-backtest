import { describe, expect, it, vi } from 'vitest'
import type { BarRangeResponse, DatasetMeta } from '../types/api'
import { ChartSession } from './session'

const revision = `sha256:${'a'.repeat(64)}`

function meta(): DatasetMeta {
  return {
    request_id: 'req', dataset_id: 'SHFE.AO2609.5m', data_revision: revision,
    instrument: { exchange: 'SHFE', symbol: 'AO2609', product: 'AO' }, timeframe: '5m',
    source: { path: 'history/sample.txt', encoding: 'GB18030', format: 'tdx_txt_v1' },
    time: { timezone: 'Asia/Shanghai', date_semantics: 'trading_day' },
    price: { price_decimals: 0, price_scale: 1 },
    coverage: { bar_count: 6000, first_bar_index: 0, last_bar_index: 5999, first_timestamp_utc: 0, last_timestamp_utc: 1, first_trading_day: '2025-01-01', last_trading_day: '2025-02-01' },
    quality: {},
  }
}

function range(generation: string, first: number, count: number, hasMoreBefore: boolean): BarRangeResponse {
  const indexes = Array.from({ length: count }, (_, index) => first + index)
  return {
    request_id: 'req', dataset_id: 'SHFE.AO2609.5m', data_revision: revision, generation_id: generation,
    price_scale: 1, coverage: { first_bar_index: first, last_bar_index: first + count - 1 }, has_more_before: hasMoreBefore,
    checksum: `sha256:${'b'.repeat(64)}`,
    bars: {
      bar_index: indexes, timestamp_utc: indexes.map((index) => index * 300_000),
      open_i64: indexes, high_i64: indexes, low_i64: indexes, close_i64: indexes,
      volume: indexes, open_interest: indexes.map(() => null),
    },
  }
}

describe('ChartSession', () => {
  it('loads 3000 tail bars and coalesces a 1500-bar prefetch', async () => {
    let generation = ''
    let resolvePrefetch!: (value: BarRangeResponse) => void
    const fetcher = vi.fn(async (_dataset: string, _revision: string, nextGeneration: string, options: { tail?: number; beforeBarIndex?: number; limit?: number } = {}) => {
      generation = nextGeneration
      if (options.tail) return range(nextGeneration, 3000, 3000, true)
      return new Promise<BarRangeResponse>((resolve) => { resolvePrefetch = resolve })
    })
    const session = new ChartSession(fetcher)
    await session.open(meta())
    expect(session.bars).toHaveLength(3000)
    expect(fetcher).toHaveBeenLastCalledWith('SHFE.AO2609.5m', revision, generation, { tail: 3000 })
    const first = session.prefetchBefore()
    const second = session.prefetchBefore()
    expect(first).toBe(second)
    expect(fetcher).toHaveBeenCalledTimes(2)
    resolvePrefetch(range(generation, 1500, 1500, true))
    await first
    expect(session.bars).toHaveLength(4500)
    expect(fetcher).toHaveBeenLastCalledWith('SHFE.AO2609.5m', revision, generation, { beforeBarIndex: 3000, limit: 1500 })
  })

  it('rejects a response from a different revision', async () => {
    const fetcher = vi.fn(async (_dataset: string, _revision: string, generation: string) => ({
      ...range(generation, 0, 1, false), data_revision: `sha256:${'f'.repeat(64)}`,
    }))
    await expect(new ChartSession(fetcher).open(meta())).rejects.toThrow('identity')
  })

  it('loads a bounded window around an arbitrary historical bar', async () => {
    const fetcher = vi.fn(async (_dataset: string, _revision: string, generation: string, options: { tail?: number; beforeBarIndex?: number; limit?: number } = {}) => {
      if (options.tail) return range(generation, 3000, 3000, true)
      return range(generation, 880, 241, true)
    })
    const session = new ChartSession(fetcher)
    await session.open(meta())
    expect(await session.loadAround(1000, 120, true)).toBe(241)
    expect(fetcher).toHaveBeenLastCalledWith('SHFE.AO2609.5m', revision, session.generation, {
      beforeBarIndex: 1121, limit: 241,
    })
    expect(session.bars.some((bar) => bar.barIndex === 1000)).toBe(true)
    expect(session.bars).toHaveLength(241)
    expect(session.bars.some((bar) => bar.barIndex === 3000)).toBe(false)
  })
})
