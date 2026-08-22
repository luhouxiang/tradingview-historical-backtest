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
    const result: ReplayObjects = { processed_bars: [], fractals: [], bi: [], bi_states: [], segments: [], zhongshu: [], segment_zhongshu: [], level_centers: [], level_movements: [], movement_states: [], center_monitors: [], divergences: [], trade_points: [] }
    for (const [key, payload] of this.objects) {
      if (key.startsWith('processed_bar:')) result.processed_bars.push(payload as unknown as ReplayObjects['processed_bars'][number])
      else if (key.startsWith('fractal:')) result.fractals.push(payload as unknown as ReplayObjects['fractals'][number])
      else if (key.startsWith('bi:')) result.bi.push(payload as unknown as ReplayObjects['bi'][number])
      else if (key.startsWith('bi_state:')) result.bi_states.push(payload as unknown as ReplayObjects['bi_states'][number])
      else if (key.startsWith('segment:')) result.segments.push(payload as unknown as ReplayObjects['segments'][number])
      else if (key.startsWith('zhongshu:')) result.zhongshu.push(payload as unknown as ReplayObjects['zhongshu'][number])
      else if (key.startsWith('segment_zhongshu:')) result.segment_zhongshu.push(payload as unknown as ReplayObjects['segment_zhongshu'][number])
      else if (key.startsWith('level_center:')) result.level_centers.push(payload as unknown as ReplayObjects['level_centers'][number])
      else if (key.startsWith('level_movement:')) result.level_movements.push(payload as unknown as ReplayObjects['level_movements'][number])
      else if (key.startsWith('movement_state:')) result.movement_states.push(payload as unknown as ReplayObjects['movement_states'][number])
      else if (key.startsWith('center_monitor:')) result.center_monitors.push(payload as unknown as ReplayObjects['center_monitors'][number])
      else if (key.startsWith('divergence:')) result.divergences.push(payload as unknown as ReplayObjects['divergences'][number])
      else if (key.startsWith('trade_point:')) result.trade_points.push(payload as unknown as ReplayObjects['trade_points'][number])
    }
    result.processed_bars.sort((left, right) => left.normalized_index - right.normalized_index)
    result.fractals.sort((left, right) => left.bar_index - right.bar_index)
    result.bi.sort((left, right) => left.start_bar_index - right.start_bar_index)
    result.bi_states.sort((left, right) => left.bar_index - right.bar_index)
    result.segments.sort((left, right) => left.start_bar_index - right.start_bar_index)
    result.zhongshu.sort((left, right) => left.start_bar_index - right.start_bar_index)
    result.segment_zhongshu.sort((left, right) => left.start_bar_index - right.start_bar_index)
    result.level_centers.sort((left, right) => left.start_bar_index - right.start_bar_index || left.level_id.localeCompare(right.level_id))
    result.level_movements.sort((left, right) => left.start_bar_index - right.start_bar_index || left.level_id.localeCompare(right.level_id))
    result.movement_states.sort((left, right) => left.start_bar_index - right.start_bar_index)
    result.center_monitors.sort((left, right) => left.bar_index - right.bar_index)
    result.divergences.sort((left, right) => left.bar_index - right.bar_index)
    result.trade_points.sort((left, right) => left.bar_index - right.bar_index)
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
