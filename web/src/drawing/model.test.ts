import { describe, expect, it } from 'vitest'
import { reactive } from 'vue'
import { DrawingHistory, LayerManager, type DrawingObject } from './model'

function drawing(id: string, order: number, locked = false): DrawingObject {
  return {
    id, name: id, type: 'trend_line', pane_id: 'main', visible: true, locked,
    z_band: 600, order_in_band: order, style: { color: '#2962ff', line_width: 1, fill_opacity: .15 },
    anchors: [{ time: 1000, price_i64: 10, price_scale: 1 }, { time: 2000, price_i64: 20, price_scale: 1 }],
    revision: 1, created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z',
  }
}

describe('LayerManager', () => {
  it('uses reverse visual order for hits and selected handles first', () => {
    const lower = drawing('lower', 0)
    const upper = drawing('upper', 1, true)
    const manager = new LayerManager([lower, upper])
    const projected = [lower, upper].map((item) => ({ drawing: item, points: [{ x: 0, y: 0 }, { x: 100, y: 100 }] }))
    expect(manager.hitTest({ x: 50, y: 50 }, projected, null)?.drawing.id).toBe('upper')
    expect(manager.hitTest({ x: 0, y: 0 }, projected, 'lower')).toMatchObject({ drawing: { id: 'lower' }, handle: 0 })
  })

  it('reorders only within the user drawing band', () => {
    const manager = new LayerManager([drawing('a', 0), drawing('b', 1)])
    expect(manager.reorder('a', 1).map((item) => [item.id, item.order_in_band])).toEqual([['a', 1], ['b', 0]])
  })

  it('copies Vue reactive drawing inputs without structured-clone errors', () => {
    const source = reactive([drawing('reactive', 0)])
    const manager = new LayerManager()
    expect(() => manager.replace(source)).not.toThrow()
    source[0]!.anchors[0]!.price_i64 = 999
    expect(manager.snapshot()[0]!.anchors[0]!.price_i64).toBe(10)
  })
})

describe('DrawingHistory', () => {
  it('undoes and redoes complete drawing snapshots', () => {
    const history = new DrawingHistory()
    history.load([])
    history.commit([drawing('a', 0)])
    expect(history.undo()).toEqual([])
    expect(history.redo()).toHaveLength(1)
  })
})
