import { describe, expect, it, vi } from 'vitest'
import type { PaneRendererCustomData, Time } from 'lightweight-charts'
import { HollowVolumeSeries, type HollowVolumeData } from './hollowVolumeSeries'

describe('HollowVolumeSeries', () => {
  it('draws rising volume as a four-sided red outline and falling volume as one solid blue-green bar', () => {
    const fillRect = vi.fn()
    const context = { fillStyle: '', fillRect }
    const target = {
      useBitmapCoordinateSpace: (draw: (scope: object) => void) => draw({ context, horizontalPixelRatio: 1, verticalPixelRatio: 1 }),
    }
    const data = {
      bars: [
        { x: 10, time: 0, barColor: '#f23645', originalData: { time: 1 as Time, value: 4, rising: true } },
        { x: 20, time: 1, barColor: '#00b8a9', originalData: { time: 2 as Time, value: 3, rising: false } },
      ],
      barSpacing: 8,
      visibleRange: { from: 0, to: 2 },
      conflationFactor: 1,
    } as PaneRendererCustomData<Time, HollowVolumeData>
    const series = new HollowVolumeSeries()
    series.update(data)
    series.renderer().draw(target as never, (price) => (100 - price * 10) as never, false)
    expect(fillRect).toHaveBeenCalledTimes(5)
    expect(fillRect.mock.calls.slice(0, 4)).toHaveLength(4)
    expect(fillRect.mock.calls[4]).toEqual([17, 70, 6, 30])
  })
})
