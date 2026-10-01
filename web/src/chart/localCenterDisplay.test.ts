import { describe, expect, it } from 'vitest'
import type { ChanCalculationResults, ChanLocalCenter } from '../types/api'
import { selectLocalCenterObjects, selectLocalCenters } from './localCenterDisplay'

const bi = { object_id: 'bi-center', unit_kind: 'BI', structural_level: 'stroke' } as ChanLocalCenter
const segment = { object_id: 'segment-center', unit_kind: 'SEGMENT', structural_level: 'segment', zd_i64: 3076, zg_i64: 3120 } as ChanLocalCenter

describe('local center display layers', () => {
  it('selects BI and segment centers independently and together', () => {
    expect(selectLocalCenters([bi, segment], { bi: true, segment: false })).toEqual([bi])
    expect(selectLocalCenters([bi, segment], { bi: false, segment: true })).toEqual([segment])
    expect(selectLocalCenters([bi, segment], { bi: true, segment: true })).toEqual([bi, segment])
    expect(selectLocalCenters([bi, segment], { bi: false, segment: false })).toEqual([])
  })
  it('keeps only selected-mode roles and confirmation events without changing source facts', () => {
    const objects = {
      local_centers: [bi, segment],
      center_connections: [
        { unit_kind: 'BI', from_center_id: bi.object_id },
        { unit_kind: 'SEGMENT', from_center_id: segment.object_id },
      ],
      center_audit_events: [{ center_id: bi.object_id }, { center_id: segment.object_id }],
    } as ChanCalculationResults['objects']
    const selected = selectLocalCenterObjects(objects, { bi: false, segment: true })
    expect(selected.local_centers).toEqual([segment])
    expect(selected.center_connections).toEqual([objects.center_connections[1]])
    expect(selected.center_audit_events).toEqual([objects.center_audit_events[1]])
    expect(objects.local_centers).toHaveLength(2)
  })
})
