import type { CausalEvent, ChanCalculationResults } from '../types/api'

export type ReplayObjects = ChanCalculationResults['objects']
export type ReplaySignal = Record<string, unknown> & { object_type: string; object_id: string }

export class ReplayEventIndex {
  private readonly events: CausalEvent[]
  private readonly objects = new Map<string, Record<string, unknown>>()
  private position = 0
  private cursor = -1

  constructor(events: CausalEvent[]) {
    this.events = [...events].sort((left, right) => left.event_seq - right.event_seq)
  }

  append(events: CausalEvent[]): void {
    let previous = this.events.at(-1)
    for (const event of events) {
      if (previous && (event.event_seq <= previous.event_seq || event.known_at_bar_index < previous.known_at_bar_index)) {
        throw new Error('训练事件增量顺序不一致')
      }
      previous = event
    }
    this.events.push(...events)
  }

  seek(cursor: number): ReplayObjects {
    if (cursor < this.cursor) {
      this.objects.clear()
      this.position = 0
    }
    while (this.position < this.events.length) {
      const event = this.events[this.position]
      if (!event || event.known_at_bar_index > cursor) break
      const key = `${event.object_type}:${event.object_id}`
      if (event.operation === 'delete') this.objects.delete(key)
      else this.objects.set(key, event.payload)
      this.position += 1
    }
    this.cursor = cursor
    const result: ReplayObjects = { processed_bars: [], fractals: [], bi: [], bi_states: [], segments: [], local_centers: [], center_connections: [], center_audit_events: [], movement_states: [], center_monitors: [], divergences: [], trade_points: [] }
    result.recursive_movements = []
    result.recursive_centers = []
    result.recursive_memberships = []
    for (const [key, payload] of this.objects) {
      if (key.startsWith('processed_bar:')) result.processed_bars.push(payload as unknown as ReplayObjects['processed_bars'][number])
      else if (key.startsWith('fractal:')) result.fractals.push(payload as unknown as ReplayObjects['fractals'][number])
      else if (key.startsWith('bi:')) result.bi.push(payload as unknown as ReplayObjects['bi'][number])
      else if (key.startsWith('bi_state:')) result.bi_states.push(payload as unknown as ReplayObjects['bi_states'][number])
      else if (key.startsWith('segment:')) result.segments.push(payload as unknown as ReplayObjects['segments'][number])
      else if (key.startsWith('local_center:')) result.local_centers.push(payload as unknown as ReplayObjects['local_centers'][number])
      else if (key.startsWith('center_connection:')) result.center_connections.push(payload as unknown as ReplayObjects['center_connections'][number])
      else if (key.startsWith('center_audit_event:')) result.center_audit_events.push(payload as unknown as ReplayObjects['center_audit_events'][number])
      else if (key.startsWith('movement_state:')) result.movement_states.push(payload as unknown as ReplayObjects['movement_states'][number])
      else if (key.startsWith('center_monitor:')) result.center_monitors.push(payload as unknown as ReplayObjects['center_monitors'][number])
      else if (key.startsWith('divergence:')) result.divergences.push(payload as unknown as ReplayObjects['divergences'][number])
      else if (key.startsWith('trade_point:')) result.trade_points.push(payload as unknown as ReplayObjects['trade_points'][number])
      else if (key.startsWith('recursive_movement:')) result.recursive_movements.push(payload as unknown as NonNullable<ReplayObjects['recursive_movements']>[number])
      else if (key.startsWith('recursive_center:')) result.recursive_centers.push(payload as unknown as NonNullable<ReplayObjects['recursive_centers']>[number])
      else if (key.startsWith('recursive_membership:')) result.recursive_memberships.push(payload as unknown as NonNullable<ReplayObjects['recursive_memberships']>[number])
    }
    result.processed_bars.sort((left, right) => left.normalized_index - right.normalized_index)
    result.fractals.sort((left, right) => left.bar_index - right.bar_index)
    result.bi.sort((left, right) => left.start_bar_index - right.start_bar_index)
    result.bi_states.sort((left, right) => left.bar_index - right.bar_index)
    result.segments.sort((left, right) => left.start_bar_index - right.start_bar_index)
    result.local_centers.sort((left, right) => left.body_start_bar_index - right.body_start_bar_index)
    result.center_connections.sort((left, right) => left.start_bar_index - right.start_bar_index)
    result.center_audit_events.sort((left, right) => left.event_bar_index - right.event_bar_index)
    result.movement_states.sort((left, right) => left.start_bar_index - right.start_bar_index)
    result.center_monitors.sort((left, right) => left.bar_index - right.bar_index)
    result.divergences.sort((left, right) => left.bar_index - right.bar_index)
    result.trade_points.sort((left, right) => left.bar_index - right.bar_index)
    result.recursive_movements.sort((left, right) => left.level - right.level || left.start_bar_index - right.start_bar_index)
    result.recursive_centers.sort((left, right) => left.level - right.level || left.start_bar_index - right.start_bar_index)
    result.recursive_memberships.sort((left, right) => left.level - right.level || left.object_id.localeCompare(right.object_id))
    return result
  }

  signals(): ReplaySignal[] {
    const result: ReplaySignal[] = []
    for (const [key, payload] of this.objects) {
      const separator = key.indexOf(':')
      const objectType = key.slice(0, separator)
      if (!['strategy_state', 'stage_signal', 'trade_signal', 'chart_event', 'risk_decision'].includes(objectType)) continue
      result.push({ ...payload, object_type: objectType, object_id: key.slice(separator + 1) })
    }
    return result
  }
}
