import { describe, expect, it } from 'vitest'
import { defaultPaneLayout, enforceMinimumHeights, removePane, resizeAdjacent } from './layout'

describe('pane layout', () => {
  it('starts at 6:1:1 and only resizes adjacent panes', () => {
    const panes = defaultPaneLayout()
    expect(panes.map((pane) => pane.weight)).toEqual([6, 1, 1])
    const resized = resizeAdjacent(panes, 0, -80, 800)
    expect(resized[2].weight).toBe(1)
    expect(resized[0].weight + resized[1].weight).toBeCloseTo(7)
    expect(resized[0].weight).toBeLessThan(6)
    expect(resized[1].weight).toBeGreaterThan(1)
  })

  it('stops at minimum height and never removes the price pane', () => {
    const panes = defaultPaneLayout()
    const resized = resizeAdjacent(panes, 0, -1000, 800)
    expect((resized[0].weight / 8) * 800).toBe(240)
    expect(removePane(panes, 'price')).toHaveLength(3)
    expect(removePane(panes, 'macd').map((pane) => pane.id)).toEqual(['price', 'volume'])
  })

  it('reallocates a short chart so both indicator panes retain 80 pixels', () => {
    const effective = enforceMinimumHeights(defaultPaneLayout(), 560)
    expect(effective.map((pane) => pane.weight)).toEqual([400, 80, 80])
    expect(effective.reduce((sum, pane) => sum + pane.weight, 0)).toBe(560)
  })
})
