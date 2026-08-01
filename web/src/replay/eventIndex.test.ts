import { describe, expect, it } from 'vitest'
import type { CausalEvent } from '../types/api'
import { ReplayEventIndex } from './eventIndex'

describe('ReplayEventIndex', () => {
  it('applies upserts and deletes only when known and rebuilds on rewind', () => {
    const events: CausalEvent[] = [
      { event_seq: 1, known_at_bar_index: 4, object_type: 'fractal', object_id: 'f-1', operation: 'upsert', object_revision: 1, payload: { object_id: 'f-1', bar_index: 2 } },
      { event_seq: 2, known_at_bar_index: 6, object_type: 'bi', object_id: 'b-1', operation: 'upsert', object_revision: 1, payload: { object_id: 'b-1', start_bar_index: 2 } },
      { event_seq: 3, known_at_bar_index: 8, object_type: 'bi', object_id: 'b-1', operation: 'delete', object_revision: 2, payload: {} },
    ]
    const index = new ReplayEventIndex(events)
    expect(index.seek(3).fractals).toHaveLength(0)
    expect(index.seek(6).bi).toHaveLength(1)
    expect(index.seek(8).bi).toHaveLength(0)
    expect(index.seek(5).fractals).toHaveLength(1)
    expect(index.seek(5).bi).toHaveLength(0)
  })

  it('indexes and seeks 25,000 causal events within the interaction budget', () => {
    const events: CausalEvent[] = Array.from({ length: 25_000 }, (_, index) => ({
      event_seq: index + 1,
      known_at_bar_index: index,
      object_type: 'fractal',
      object_id: `f-${index}`,
      operation: 'upsert',
      object_revision: 1,
      payload: { object_id: `f-${index}`, bar_index: index, known_at_bar_index: index },
    }))
    const started = performance.now()
    const index = new ReplayEventIndex(events)
    expect(index.seek(24_999).fractals).toHaveLength(25_000)
    const elapsed = performance.now() - started
    expect(elapsed).toBeLessThan(500)
  })
})
