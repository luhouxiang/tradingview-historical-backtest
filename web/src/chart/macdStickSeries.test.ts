import { describe, expect, it, vi } from 'vitest'
import type { PaneRendererCustomData, Time } from 'lightweight-charts'
import { MacdStickSeries, type MacdStickData } from './macdStickSeries'

describe('MacdStickSeries', () => {
  it('draws positive and negative MACD values as wick-like thin lines from the zero axis', () => {
    const fillRect = vi.fn()
    const target = {
      useBitmapCoordinateSpace: (draw: (scope: object) => void) => draw({
        context: { fillStyle: '', fillRect }, horizontalPixelRatio: 1, verticalPixelRatio: 1,
      }),
    }
    const data = {
      bars: [
        { x: 10, time: 0, barColor: '#f23645', originalData: { time: 1 as Time, value: 2, color: '#000000' } },
        { x: 20, time: 1, barColor: '#00b8a9', originalData: { time: 2 as Time, value: -3, color: '#000000' } },
      ],
      barSpacing: 8, visibleRange: { from: 0, to: 2 }, conflationFactor: 1,
    } as PaneRendererCustomData<Time, MacdStickData>
    const series = new MacdStickSeries()
    series.update(data)
    series.renderer().draw(target as never, (price) => (50 - price * 10) as never, false)
    expect(fillRect.mock.calls).toEqual([
      [10, 30, 1, 20],
      [20, 50, 1, 30],
    ])
  })
})
