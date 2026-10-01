import type { ChanCalculationResults, ChanLocalCenter } from '../types/api'

export interface LocalCenterVisibility {
  bi: boolean
  segment: boolean
}

// Presentation only: never infer centers or fall back to the other unit kind.
export function selectLocalCenters(centers: ChanLocalCenter[], visibility: LocalCenterVisibility): ChanLocalCenter[] {
  return centers.filter((center) =>
    (center.unit_kind === 'BI' && visibility.bi)
    || (center.unit_kind === 'SEGMENT' && visibility.segment))
}

export function selectLocalCenterObjects(objects: ChanCalculationResults['objects'], visibility: LocalCenterVisibility) {
  const local_centers = selectLocalCenters(objects.local_centers ?? [], visibility)
  const ids = new Set(local_centers.map((center) => center.object_id))
  const unitKinds = new Set(local_centers.map((center) => center.unit_kind))
  return {
    local_centers,
    center_connections: (objects.center_connections ?? []).filter((connection) =>
      unitKinds.has(connection.unit_kind) && ids.has(connection.from_center_id)),
    center_audit_events: (objects.center_audit_events ?? []).filter((event) => ids.has(event.center_id)),
  }
}
