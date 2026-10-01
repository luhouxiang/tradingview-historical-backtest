import { execFileSync } from 'node:child_process'
import { expect, test } from '@playwright/test'

for (const scenario of [
  { name: 'trend', candidateId: 'divergence-e50a47b5dd0005c7bf74', windowStart: 26000, windowEnd: 29000 },
  { name: 'external range', candidateId: 'divergence-5dbf3fe09c0552b5ca0e', windowStart: 28000, windowEnd: 31000 },
]) test(`AOL9 saved ${scenario.name} candidate highlights its real comparison segments`, async ({ page }) => {
  test.skip(!process.env.TVBT_15S_CACHE || !process.env.TVBT_15S_BARS, 'requires the verified 15S AOL9 cache')
  const fixture = JSON.parse(execFileSync('D:/ProgramData/anaconda3/envs/pydev3.14/python.exe', ['-c', `
import json,sys
from pathlib import Path
import pyarrow.parquet as pq
cache=Path(sys.argv[1]); bars_path=Path(sys.argv[2])
candidate=next(row for row in pq.read_table(cache/'divergences.parquet').to_pylist()
               if row['object_id']==sys.argv[3] and row['status']=='candidate')
ids={candidate['a_object_id'],candidate['b_object_id'],candidate['comparison_current_object_id']}
segments=[row for row in pq.read_table(cache/'segments.parquet').to_pylist() if row['object_id'] in ids]
assert len(segments)==(3 if candidate['divergence_kind']=='trend' else 2)
start=int(sys.argv[4]); end=int(sys.argv[5])
bars=pq.read_table(bars_path,filters=[('bar_index','>=',start),('bar_index','<=',end)],
                   columns=['bar_index','timestamp_utc','open_i64','high_i64','low_i64','close_i64','volume','open_interest']).to_pydict()
print(json.dumps({'candidate':candidate,'segments':segments,'bars':bars}))
`, process.env.TVBT_15S_CACHE!, process.env.TVBT_15S_BARS!, scenario.candidateId,
  String(scenario.windowStart), String(scenario.windowEnd)], { encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 }))
  const revision = 'sha256:2904362e62173a418d63feaf8855a3aef4b61ce5b0027e7801785baf9d8302fe'
  const candidate = fixture.candidate
  if (scenario.name === 'trend') {
    expect(candidate.c_contains_type3).toBe(true)
    expect(candidate.c_meets_sublevel).toBe(false)
    expect(candidate.c_sublevel_center_ids).toHaveLength(1)
  }
  const byId = Object.fromEntries(fixture.segments.map((segment: { object_id: string }) => [segment.object_id, segment]))
  const source = { source_type: 'StrategySource', source_id: 'chan', job_id: 'chan-15s', status: 'completed', visible: true, parameters: {},
    category_visibility: { bi: false, fractals: false, segments: true, bi_centers: false, segment_centers: false,
      movement_states: false, center_monitors: false, divergences: true, trade_points: false },
    definition: { name: 'AOL9 15S 实际缓存', outputs: [] } }
  await page.addInitScript(({ source, revision, candidate, selectedSegments, windowStart, windowEnd, label }) => Object.assign(window, {
    chartWindowProps: {
      dataset: { dataset_id: 'SHFE.AOL9.5m', data_revision: revision, instrument: { symbol: 'AOL9' },
        price: { price_scale: 1, price_decimals: 0 }, time: { timezone: 'Asia/Shanghai' },
        coverage: { first_bar_index: windowStart, last_bar_index: windowEnd, bar_count: windowEnd - windowStart + 1 } },
      strategySources: [source],
      selectedSignal: { ...candidate, object_type: 'divergence', label, signal: candidate },
      selectedDivergenceSegments: selectedSegments,
      signalLocked: true,
    },
  }), { source, revision, candidate, windowStart: scenario.windowStart, windowEnd: scenario.windowEnd,
    label: scenario.name === 'trend' ? '线段趋势背驰候选' : '外部盘整背驰候选', selectedSegments: {
    a: byId[candidate.a_object_id], reference: byId[candidate.comparison_reference_object_id], current: byId[candidate.comparison_current_object_id],
  } })
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/client-logs')) { await route.fulfill({ json: {} }); return }
    if (url.pathname.endsWith('/bars')) {
      const end = url.searchParams.has('tail') ? scenario.windowEnd : Number(url.searchParams.get('before_bar_index')) - 1
      const limit = Number(url.searchParams.get('tail') ?? url.searchParams.get('limit'))
      const start = Math.max(scenario.windowStart, end - limit + 1)
      const bars = Object.fromEntries(Object.entries(fixture.bars).map(([key, column]) =>
        [key, (column as unknown[]).slice(start - scenario.windowStart, end - scenario.windowStart + 1)]))
      await route.fulfill({ json: { dataset_id: 'SHFE.AOL9.5m', data_revision: revision,
        generation_id: url.searchParams.get('generation_id'), checksum: `sha256:${'a'.repeat(64)}`, price_scale: 1,
        coverage: { first_bar_index: start, last_bar_index: end }, has_more_before: start > scenario.windowStart,
        has_more_after: false, bars } }); return
    }
    if (url.pathname.endsWith('/results')) {
      const objects = { processed_bars: [], fractals: [], bi: [], bi_states: [], segments: fixture.segments,
        local_centers: [], center_connections: [], center_audit_events: [], movement_states: [],
        center_monitors: [], divergences: [candidate], trade_points: [] }
      await route.fulfill({ json: { job_id: 'chan-15s', dataset_id: 'SHFE.AOL9.5m', data_revision: revision,
        result_kind: 'chan', cache_key: 'real-aol9-15s', objects } }); return
    }
    await route.fulfill({ status: 404, json: {} })
  })
  await page.setViewportSize({ width: 1600, height: 950 })
  await page.goto('/e2e/chart-window.html')
  const chart = page.getByLabel('K 线多窗格图表')
  await expect(chart).toHaveAttribute('data-cache-bar-count', '3000')
  const firstCached = scenario.windowStart + 1
  const aStart = byId[candidate.a_object_id].start_bar_index as number
  const cEnd = byId[candidate.comparison_current_object_id].end_bar_index as number
  await page.evaluate(({ from, to }) => (window as any).chartWindowChart.timeScale().setVisibleLogicalRange({ from, to }), {
    from: aStart - firstCached - 80,
    to: cEnd - firstCached + 80,
  })
  if (scenario.name === 'trend') await expect(page.locator('[data-divergence-segment="a"]')).toBeVisible()
  else await expect(page.locator('[data-divergence-segment="a"]')).toHaveCount(0)
  await expect(page.locator('[data-divergence-segment="reference"]')).toBeVisible()
  await expect(page.locator('[data-divergence-segment="current"]')).toBeVisible()
  await expect(page.getByText(scenario.name === 'trend' ? '参照段 b' : '参照段 a', { exact: false })).toBeVisible()
  await expect(page.locator('[data-divergence-segment]')).toHaveCount(scenario.name === 'trend' ? 3 : 2)
  expect(await page.locator('[data-divergence-segment] line').evaluateAll(lines => lines.every(line => {
    const x1 = Number(line.getAttribute('x1')), x2 = Number(line.getAttribute('x2'))
    return Number.isFinite(x1) && Number.isFinite(x2) && x1 >= 0 && x1 <= 1600 && x2 >= 0 && x2 <= 1600
  }))).toBe(true)
  expect(errors).toEqual([])
  await page.screenshot({ path: `../trading-data/acceptance/divergence-15s-19-3-${scenario.name === 'trend' ? 'abc' : 'ac'}.png` })
})
