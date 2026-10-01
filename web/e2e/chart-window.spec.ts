import { expect, test } from '@playwright/test'

test('zoomed-out historical windows retain left segments, higher centers and MACD', async ({ page }) => {
  test.skip(process.env.TVBT_CHART_WINDOW_E2E !== '1', 'requires the Vite component fixture server')
  const count = 12000
  const revision = `sha256:${'a'.repeat(64)}`
  const timestamp = (index: number) => 1_700_000_000_000 + index * 300_000
  const price = (index: number) => Math.round(2800 + Math.sin(index / 240) * 100)
  const baseDefinition = {
    algorithm_version: '17.0.0', source_hash: `sha256:${'c'.repeat(64)}`, input_schema: 'bars.v1', causal: true,
    parameter_schema: { type: 'object', additionalProperties: false, required: [], properties: {} },
    warmup: { kind: 'formula', expression: '0' },
  }
  const props = {
    dataset: {
      dataset_id: 'TEST.AO.5m', data_revision: revision,
      instrument: { exchange: 'TEST', symbol: 'AO', product: 'AO' }, timeframe: '5m',
      source: {}, time: { timezone: 'Asia/Shanghai' }, price: { price_decimals: 0, price_scale: 1 },
      coverage: { bar_count: count, first_bar_index: 0, last_bar_index: count - 1 }, quality: {},
    },
    indicatorSources: [{ source_type: 'SeriesSource', source_id: 'macd', job_id: 'job-macd', status: 'completed', parameters: { fast_period: 12, slow_period: 26, signal_period: 9 },
      definition: { ...baseDefinition, kind: 'indicator', algorithm_id: 'macd', name: 'MACD', outputs: [
        { name: 'histogram', display_name: 'MACD', pane: 'indicator', series_type: 'histogram' },
      ] } }],
    strategySources: [{ source_type: 'StrategySource', source_id: 'chan', job_id: 'job-chan', status: 'completed', visible: true, parameters: {},
      category_visibility: { bi: false, fractals: false, segments: true, local_centers: false, level_centers: true, divergences: false, trade_points: false },
      definition: { ...baseDefinition, kind: 'chan', algorithm_id: 'chan_engineering', name: '缠论', outputs: [] } }],
  }
  await page.addInitScript((value) => {
    Object.assign(window, { chartWindowProps: value })
  }, props)
  const requests: Array<{ job: string; from: number; to: number }> = []
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  let creations = 0
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/client-logs')) { await route.fulfill({ json: {} }); return }
    if (route.request().method() !== 'GET') creations += 1
    if (url.pathname.endsWith('/bars')) {
      const end = url.searchParams.has('tail') ? count - 1 : Number(url.searchParams.get('before_bar_index')) - 1
      const limit = Number(url.searchParams.get('tail') ?? url.searchParams.get('limit'))
      const indices = Array.from({ length: Math.min(limit, end + 1) }, (_, i) => Math.max(0, end - limit + 1) + i)
      await route.fulfill({ json: {
        dataset_id: 'TEST.AO.5m', data_revision: revision, generation_id: url.searchParams.get('generation_id'),
        checksum: `sha256:${'b'.repeat(64)}`, price_scale: 1,
        coverage: { first_bar_index: indices[0], last_bar_index: end }, has_more_before: indices[0]! > 0, has_more_after: false,
        bars: { bar_index: indices, timestamp_utc: indices.map(timestamp), open_i64: indices.map(price),
          high_i64: indices.map((i) => price(i) + 4), low_i64: indices.map((i) => price(i) - 4),
          close_i64: indices.map((i) => price(i) + 1), volume: indices.map(() => 500), open_interest: indices.map(() => null) },
      } }); return
    }
    if (url.pathname.endsWith('/results')) {
      const from = Number(url.searchParams.get('from_bar_index'))
      const to = Number(url.searchParams.get('to_bar_index'))
      const job = url.pathname.split('/').at(-2)!
      requests.push({ job, from, to })
      if (to - from + 1 > 5000) {
        await route.fulfill({ status: 400, json: { error: { code: 'INVALID_RANGE', message: 'max 5000 bars' } } }); return
      }
      const base = { job_id: job, dataset_id: 'TEST.AO.5m', data_revision: revision, cache_key: job,
        algorithm: { algorithm_id: job }, coverage: { first_bar_index: from, last_bar_index: to, returned_count: to-from+1 } }
      if (job === 'job-macd') {
        const indices = Array.from({ length: to-from+1 }, (_, i) => from+i)
        await route.fulfill({ json: { ...base, result_kind: 'indicator', bar_index: indices,
          values: { histogram: indices.map((i) => Math.sin(i / 40) * 5) } } }); return
      }
      const segments = Array.from({ length: 20 }, (_, i) => ({
        object_id: `segment-${i}`, object_revision: 1, start_bar_index: i * 600, end_bar_index: (i+1)*600-1,
        start_time: timestamp(i*600), end_time: timestamp((i+1)*600-1),
        start_price_i64: price(i*600), end_price_i64: price((i+1)*600-1), confirmed: true,
      })).filter((s) => s.start_bar_index <= to && s.end_bar_index >= from)
      const centers = [
        { object_id: 'left-center', start_bar_index: 400, end_bar_index: 2000 },
        { object_id: 'spanning-center', start_bar_index: 4400, end_bar_index: 6200 },
        { object_id: 'right-center', start_bar_index: 10200, end_bar_index: 11800 },
      ].filter((s) => s.start_bar_index <= to && s.end_bar_index >= from)
        .map((s) => ({ ...s, object_revision: 1, start_time: timestamp(s.start_bar_index), end_time: timestamp(s.end_bar_index),
          zd_i64: 2750, zg_i64: 2830, confirmed: true, level_id: 'L1', status: 'confirmed' }))
      await route.fulfill({ json: { ...base, result_kind: 'chan', objects: {
        processed_bars: [], fractals: [], bi: [], bi_states: [], segments, local_centers: [], center_connections: [],
        center_audit_events: [], level_centers: centers, level_movements: [], movement_states: [], center_monitors: [], divergences: [], trade_points: [],
      } } }); return
    }
    await route.fulfill({ status: 404, json: {} })
  })
  await page.setViewportSize({ width: 1600, height: 950 })
  await page.goto('/e2e/chart-window.html')
  const chart = page.getByLabel('K 线多窗格图表')
  await expect(chart).toHaveAttribute('data-cache-bar-count', '3000')
  for (let loaded = 3000; loaded < count; loaded += 1500) {
    await page.evaluate((size) => {
      const chart = (window as any).chartWindowChart
      chart.timeScale().setVisibleLogicalRange({ from: 0, to: size-1 })
    }, loaded)
    await expect(chart).toHaveAttribute('data-cache-bar-count', String(loaded + 1500))
  }
  await page.evaluate((size) => (window as any).chartWindowChart.timeScale().setVisibleLogicalRange({ from: 0, to: size-1 }), count)
  await expect.poll(async () => page.evaluate(() => {
    const geometry = (window as any).chartWindowPrimitive.geometry()
    return { segments: geometry.segments.length, centers: geometry.levelCenters.length }
  })).toEqual({ segments: 20, centers: 3 })
  expect(await page.evaluate(() => {
    const geometry = (window as any).chartWindowPrimitive.geometry()
    // The first candle's center can sit half a bar outside the left clip edge.
    return geometry.segments[0].end.x > 0 && geometry.segments[0].end.x < 400
      && geometry.levelCenters[0].left > 0 && geometry.levelCenters[0].left < 400
      && geometry.levelCenters[0].bottom - geometry.levelCenters[0].top > 10
  })).toBe(true)
  await expect.poll(async () => page.evaluate(() => {
    const histogram = (window as any).chartWindowChart.panes()[1].getSeries().at(-1)
    return histogram.data().length
  })).toBe(count)
  expect(requests.every(({ from, to }) => to-from+1 <= 5000)).toBe(true)
  expect(requests).toEqual(expect.arrayContaining([
    { job: 'job-chan', from: 0, to: 4999 }, { job: 'job-chan', from: 5000, to: 9999 }, { job: 'job-chan', from: 10000, to: 11999 },
  ]))
  expect(errors).toEqual([])
  expect(creations).toBe(0)
  await expect(chart.locator('.chart-error')).toHaveCount(0)
  await page.screenshot({ path: '../trading-data/acceptance/chart-window-pagination.png', fullPage: true })
})
