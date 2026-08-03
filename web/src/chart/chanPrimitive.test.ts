import { describe, expect, it } from 'vitest'
import { ChanPrimitive, buildChanGeometry } from './chanPrimitive'
import type { ChanCalculationResults } from '../types/api'

function objects(count: number): ChanCalculationResults['objects'] {
  return {
    fractals: Array.from({ length: count }, (_, index) => ({
      object_id: `fractal-${index}`, bar_index: index, time: index * 60_000, price_i64: 1000 + index,
      fractal_type: index % 2 ? 'top' as const : 'bottom' as const, confirmed: index % 3 !== 0,
      confirmed_at_bar_index: index + 2, known_at_bar_index: index + 2, object_revision: 1,
    })),
    bi: [], segments: [], zhongshu: [],
  }
}

describe('ChanPrimitive', () => {
  it('uses one primitive with bottom and normal batch views', () => {
    const primitive = new ChanPrimitive()
    expect(primitive.paneViews().map((view) => view.zOrder?.())).toEqual(['bottom', 'normal'])
  })

  it('projects 10,000 semantic objects as one batch without Vue nodes', () => {
    const source = objects(10_000)
    const started = performance.now()
    const geometry = buildChanGeometry(source, 10, (time) => Number(time), (price) => price)
    const elapsed = performance.now() - started
    expect(geometry.fractals).toHaveLength(10_000)
    expect(elapsed).toBeLessThan(250)
  })

  it('drops objects outside the current coordinate projection', () => {
    const geometry = buildChanGeometry(objects(3), 10, (time) => Number(time) === 0 ? null : Number(time), (price) => price)
    expect(geometry.fractals.map((item) => item.x)).toEqual([60, 120])
  })

  it('updates semantic object rendering styles as one primitive', () => {
    const primitive = new ChanPrimitive()
    primitive.setStyle({ outputs: {
      fractal: { color: '#ff5252', line_width: 1, line_style: 'solid', opacity: 0.8, visible: false },
      bi: { color: '#ab47bc', line_width: 3, line_style: 'dashed', opacity: 0.7, visible: true },
      segment: { color: '#ffeb3b', line_width: 3, line_style: 'solid', opacity: 1, visible: true },
      zhongshu: { color: '#00b8d4', line_width: 2, line_style: 'dotted', opacity: 0.6, visible: true },
    } })
    expect(primitive.renderStyle()).toEqual({
      fractal: { color: '#ff5252', line_width: 1, line_style: 'solid', opacity: 0.8, visible: false },
      bi: { color: '#ab47bc', line_width: 3, line_style: 'dashed', opacity: 0.7, visible: true },
      segment: { color: '#ffeb3b', line_width: 3, line_style: 'solid', opacity: 1, visible: true },
      zhongshu: { color: '#00b8d4', line_width: 2, line_style: 'dotted', opacity: 0.6, visible: true },
    })
  })
})
