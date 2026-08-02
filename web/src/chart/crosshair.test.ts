import { describe, expect, it } from 'vitest'
import { barAtLogicalIndex, formatBarTimeRange } from './crosshair'
import type { CachedBar } from './session'

const bars = [0, 1, 2].map((barIndex) => ({
  barIndex, timestampUtc: 1_700_000_000_000 + barIndex * 300_000,
  openI64: 1, highI64: 2, lowI64: 0, closeI64: 1, volume: barIndex + 10, openInterest: null,
})) satisfies CachedBar[]

describe('domestic futures crosshair helpers', () => {
  it('uses the K-line whose horizontal range contains the mouse logical coordinate', () => {
    expect(barAtLogicalIndex(bars, 0.49)?.barIndex).toBe(0)
    expect(barAtLogicalIndex(bars, 0.51)?.barIndex).toBe(1)
    expect(barAtLogicalIndex(bars, 2.6)).toBeNull()
  })

  it('formats a bar-end timestamp as its full local interval and weekday', () => {
    const timestamp = Date.parse('2026-07-31T13:30:00Z')
    expect(formatBarTimeRange(timestamp, '5m', 'Asia/Shanghai', 'bar_end'))
      .toBe('2026/07/31 21:25~21:30 五')
  })
})
