import { describe, expect, it, vi } from 'vitest'
import type { PaneRendererCustomData, Time } from 'lightweight-charts'
import { MacdStickSeries, macdGuideStep, type MacdStickData } from './macdStickSeries'

describe('MacdStickSeries', () => {
  it('draws positive and negative MACD values as wick-like thin lines from the zero axis', () => {
    const fillRect = vi.fn()
    const fillText = vi.fn()
    const target = {
      useBitmapCoordinateSpace: (draw: (scope: object) => void) => draw({
        context: { fillStyle: '', fillRect, fillText, save: vi.fn(), restore: vi.fn(), setLineDash: vi.fn(),
          beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn() },
        bitmapSize: { width: 100, height: 100 }, horizontalPixelRatio: 1, verticalPixelRatio: 1,
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
    expect(fillText).not.toHaveBeenCalled()
  })

  it('uses 5, 10, then larger natural marks while leaving sub-5 values unlabelled', () => {
    expect(macdGuideStep((price) => (50 - price * 6) as never)).toBe(5)
    expect(macdGuideStep((price) => (50 - price * 3) as never)).toBe(10)
    expect(macdGuideStep((price) => (50 - price * 0.3) as never)).toBe(100)
  })

  it('draws guides while leaving numeric scale labels to the right price axis', () => {
    const context = { fillText: vi.fn(), fillRect: vi.fn(), save: vi.fn(), restore: vi.fn(),
      setLineDash: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), stroke: vi.fn() }
    const target = { useBitmapCoordinateSpace: (draw: (scope: object) => void) => draw({
      context, bitmapSize: { width: 100, height: 100 }, horizontalPixelRatio: 1, verticalPixelRatio: 1,
    }) }
    const series = new MacdStickSeries()
    series.update({ bars: [], barSpacing: 8, visibleRange: { from: 0, to: 0 }, conflationFactor: 1 } as never)
    series.renderer().draw(target as never, (price) => (50 - price * 6) as never, false)
    expect(context.moveTo.mock.calls.map((call) => call[1])).toEqual([50.5, 20.5, 80.5])
    expect(context.fillText).not.toHaveBeenCalled()
    context.moveTo.mockClear()
    series.renderer().draw(target as never, (price) => (50 - price * 20) as never, false)
    expect(context.moveTo.mock.calls.map((call) => call[1])).toEqual([50.5])
  })
})
