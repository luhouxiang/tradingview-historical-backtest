import { describe, expect, it } from 'vitest'
import { sourceLocationPlugin } from './sourceLocationPlugin'

describe('sourceLocationPlugin', () => {
  it('injects the real source file and line', () => {
    const plugin = sourceLocationPlugin('C:/repo/web')
    const transform = plugin.transform
    if (typeof transform !== 'function') throw new Error('transform hook missing')
    const result = transform.call({} as never, "\nfunction boot() {\n  logger.info('app.started', 'ready')\n}", 'C:/repo/web/src/main.ts')
    expect(result).toBeTruthy()
    const code = (result as { code: string }).code
    expect(code).toContain('"source_file":"src/main.ts"')
    expect(code).toContain('"source_line":3')
    expect(code).toContain('"source_function":"boot"')
    const second = transform.call({} as never, code, 'C:/repo/web/src/main.ts?vue&type=script')
    expect(second).toBeNull()
  })
})
