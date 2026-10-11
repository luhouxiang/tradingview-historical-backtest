import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { ChanCalculationResults } from '../types/api'
import { recursiveTreeObjects } from './recursiveDisplay'
import { buildChanGeometry } from '../chart/chanPrimitive'

describe('recursive evidence presentation', () => {
  it('shows the proved combination and places its first point at the actual price source', () => {
    const fixture = JSON.parse(readFileSync(resolve('../contracts/examples/recursive-standard-calculation-results.json'), 'utf8')) as ChanCalculationResults
    const objects = fixture.objects
    const point = objects.trade_points.find(value => value.signal_type === 'buy_1')!
    const divergence = objects.divergences.find(value => value.object_id === point.reference_object_id)!
    const c = objects.recursive_movements!.find(value => value.object_id === divergence.comparison_current_object_id)!
    expect([point.bar_index, point.price_i64, point.known_at_bar_index]).toEqual([2360, 460, 2421])
    expect(c.construction_kind).toBe('association')
    expect(c.promotable).toBe(false)
    const geometry = buildChanGeometry(objects, 1, time => Number(time) * 10, price => 1000-price)
    const marker = geometry.tradePoints.find(value => value.object_id === point.object_id)!
    const line = geometry.recursiveMovements.find(value => value.objectId === c.object_id)!
    expect([marker.x, marker.y]).toEqual([2370, 540])
    expect(marker.x).not.toBe(line.end.x)
    expect(recursiveTreeObjects(objects).some(value => value.object_id === c.object_id && value.label?.includes('结合分析') && value.detail?.includes('不进入父层'))).toBe(true)
  })
  it('projects Python cores and separates fixed blocks from signal confirmation', () => {
    const fixture = JSON.parse(readFileSync(resolve('../contracts/examples/recursive-structure.json'), 'utf8'))
    const objects: ChanCalculationResults['objects'] = {
      processed_bars: [], fractals: [], bi: [], bi_states: [], segments: [], local_centers: [], center_connections: [], center_audit_events: [], movement_states: [], center_monitors: [], divergences: [], trade_points: [],
      recursive_movements: fixture.movements, recursive_centers: fixture.centers, recursive_memberships: fixture.memberships,
    }
    const geometry = buildChanGeometry(objects, 1, time => Number(time) * 10, price => 100 - price)
    const parent = geometry.recursiveCenters.find(center => center.level === 1)!
    expect([parent.top, parent.bottom].sort((a, b) => a-b)).toEqual([90, 100])
    const tree = recursiveTreeObjects(objects)
    expect(tree.some(row => row.label?.startsWith('L1 递归中枢'))).toBe(true)
    expect(tree.some(row => row.detail?.includes('同级盘整块已构成'))).toBe(true)
    expect(tree.some(row => row.detail?.includes('背驰另行确认'))).toBe(true)
    expect(tree.every(row => row.object_revision >= 1)).toBe(true)
    const outside = buildChanGeometry(objects, 1, () => null, price => 100-price, undefined, index => index-100)
    expect(outside.recursiveCenters.some(center => center.level === 1 && center.left < 0 && center.right > 0)).toBe(true)
  })
})
