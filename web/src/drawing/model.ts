export type DrawingType = 'trend_line' | 'horizontal_line' | 'rectangle' | 'text' | 'measure'

export interface DrawingAnchor {
  time: number
  price_i64: number
  price_scale: number
}

export interface DrawingObject {
  id: string
  name: string
  type: DrawingType
  pane_id: string
  visible: boolean
  locked: boolean
  z_band: 600
  order_in_band: number
  style: { color: string; line_width: number; fill_opacity: number }
  anchors: DrawingAnchor[]
  text?: string
  revision: number
  created_at: string
  updated_at: string
}

export interface Point { x: number; y: number }
export interface ProjectedDrawing { drawing: DrawingObject; points: Point[] }

function cloneDrawing(drawing: DrawingObject): DrawingObject {
  return {
    ...drawing,
    style: { ...drawing.style },
    anchors: drawing.anchors.map((anchor) => ({ ...anchor })),
  }
}

export function cloneDrawings(drawings: DrawingObject[]): DrawingObject[] {
  return drawings.map(cloneDrawing)
}

function pointDistance(left: Point, right: Point): number {
  return Math.hypot(left.x - right.x, left.y - right.y)
}

function segmentDistance(point: Point, start: Point, end: Point): number {
  const dx = end.x - start.x
  const dy = end.y - start.y
  if (dx === 0 && dy === 0) return pointDistance(point, start)
  const ratio = Math.max(0, Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy)))
  return pointDistance(point, { x: start.x + ratio * dx, y: start.y + ratio * dy })
}

export class LayerManager {
  constructor(private drawings: DrawingObject[] = []) {}

  replace(drawings: DrawingObject[]): void { this.drawings = cloneDrawings(drawings) }

  ordered(): DrawingObject[] {
    return this.drawings.filter((drawing) => drawing.visible).sort((left, right) =>
      left.z_band - right.z_band || left.order_in_band - right.order_in_band || left.id.localeCompare(right.id))
  }

  reorder(id: string, direction: -1 | 1): DrawingObject[] {
    const ordered = this.drawings.filter((drawing) => drawing.z_band === 600).sort((a, b) => a.order_in_band - b.order_in_band)
    const index = ordered.findIndex((drawing) => drawing.id === id)
    const target = index + direction
    if (index < 0 || target < 0 || target >= ordered.length) return this.snapshot()
    const first = ordered[index]
    const second = ordered[target]
    if (!first || !second) return this.snapshot()
    ;[first.order_in_band, second.order_in_band] = [second.order_in_band, first.order_in_band]
    return this.snapshot()
  }

  hitTest(point: Point, projected: ProjectedDrawing[], selectedId: string | null, tolerance = 7): { drawing: DrawingObject; handle?: number } | null {
    const selected = projected.find((item) => item.drawing.id === selectedId && item.drawing.visible)
    if (selected) {
      for (let index = selected.points.length - 1; index >= 0; index--) {
        const handle = selected.points[index]
        if (handle && pointDistance(point, handle) <= tolerance) return { drawing: selected.drawing, handle: index }
      }
    }
    const ordered = [...projected].filter((item) => item.drawing.visible).sort((left, right) =>
      right.drawing.z_band - left.drawing.z_band || right.drawing.order_in_band - left.drawing.order_in_band)
    for (const item of ordered) {
      const [first, second = first] = item.points
      if (!first || !second) continue
      if (item.drawing.type === 'rectangle') {
        const inside = point.x >= Math.min(first.x, second.x) - tolerance && point.x <= Math.max(first.x, second.x) + tolerance
          && point.y >= Math.min(first.y, second.y) - tolerance && point.y <= Math.max(first.y, second.y) + tolerance
        if (inside) return { drawing: item.drawing }
      } else if (item.drawing.type === 'horizontal_line') {
        if (Math.abs(point.y - first.y) <= tolerance) return { drawing: item.drawing }
      } else if (item.drawing.type === 'text') {
        if (Math.abs(point.x - first.x) <= 60 && Math.abs(point.y - first.y) <= 18) return { drawing: item.drawing }
      } else if (segmentDistance(point, first, second) <= tolerance) {
        return { drawing: item.drawing }
      }
    }
    return null
  }

  snapshot(): DrawingObject[] { return cloneDrawings(this.drawings) }
}

export class DrawingHistory {
  private past: DrawingObject[][] = []
  private future: DrawingObject[][] = []
  private current: DrawingObject[] = []

  load(drawings: DrawingObject[]): DrawingObject[] {
    this.past = []
    this.future = []
    this.current = cloneDrawings(drawings)
    return this.value()
  }

  commit(drawings: DrawingObject[]): DrawingObject[] {
    this.past.push(cloneDrawings(this.current))
    if (this.past.length > 100) this.past.shift()
    this.current = cloneDrawings(drawings)
    this.future = []
    return this.value()
  }

  undo(): DrawingObject[] {
    const previous = this.past.pop()
    if (!previous) return this.value()
    this.future.push(cloneDrawings(this.current))
    this.current = previous
    return this.value()
  }

  redo(): DrawingObject[] {
    const next = this.future.pop()
    if (!next) return this.value()
    this.past.push(cloneDrawings(this.current))
    this.current = next
    return this.value()
  }

  value(): DrawingObject[] { return cloneDrawings(this.current) }
}
