import { describe, expect, it, vi } from 'vitest'
import { ClientLogger } from './logger'

describe('ClientLogger', () => {
  it('batches source-backed events', async () => {
    const sender = vi.fn(async () => true)
    const logger = new ClientLogger(sender)
    logger.info(
      { source_file: 'src/example.ts', source_line: 12, source_function: 'run' },
      'app.started',
      'ready',
    )
    expect(logger.pending()).toBe(1)
    await logger.flush()
    expect(sender).toHaveBeenCalledWith([
      expect.objectContaining({ source_file: 'src/example.ts', source_line: 12, event: 'app.started' }),
    ], false)
  })

  it('drops low-priority events when the queue is full', () => {
    const logger = new ClientLogger(async () => true, 2)
    const source = { source_file: 'src/example.ts', source_line: 1, source_function: 'run' }
    logger.debug(source, 'chart.range.requested', 'one')
    logger.debug(source, 'chart.range.requested', 'two')
    logger.debug(source, 'chart.range.requested', 'three')
    expect(logger.pending()).toBe(2)
  })
})

