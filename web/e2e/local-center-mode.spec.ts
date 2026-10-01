import { execFileSync } from 'node:child_process'
import { expect, test } from '@playwright/test'

test('AOL9 BI and segment centers can be displayed independently or together', async ({ page }) => {
  test.skip(!process.env.TVBT_LOCAL_CENTER_CACHE, 'requires an explicitly selected immutable AOL9 cache')
  // Read-only fixture extraction; calculations remain exclusively in Python's saved results.
  const fixture = JSON.parse(execFileSync('D:/ProgramData/anaconda3/envs/pydev3.14/python.exe', ['-c', `
import json,sys
from pathlib import Path
import pyarrow.parquet as pq
p=Path(sys.argv[1]); bars=Path(sys.argv[2])
categories=['processed_bars','fractals','bi','bi_states','segments','local_centers','center_connections','center_audit_events','movement_states','center_monitors','divergences','trade_points']
objects={k:[] for k in categories}
for k in ['segments','local_centers','center_connections','center_audit_events']:
 objects[k]=pq.read_table(p/(k+'.parquet')).to_pylist()
for k in ['segments']:
 objects[k]=[r for r in objects[k] if r['start_bar_index']<=49999 and r['end_bar_index']>=44000]
objects['local_centers']=[r for r in objects['local_centers'] if r['body_start_bar_index']<=49999 and r['observed_end_bar_index']>=44000]
rows=pq.read_table(bars,filters=[('bar_index','>=',44000),('bar_index','<=',49999)],columns=['bar_index','timestamp_utc','open_i64','high_i64','low_i64','close_i64','volume','open_interest']).to_pydict()
print(json.dumps({'objects':objects,'bars':rows}))
`, process.env.TVBT_LOCAL_CENTER_CACHE!, process.env.TVBT_LOCAL_CENTER_BARS!], { encoding: 'utf8', maxBuffer: 20 * 1024 * 1024 }))
  const revision = 'sha256:2904362e62173a418d63feaf8855a3aef4b61ce5b0027e7801785baf9d8302fe'
  const source = { source_type: 'StrategySource', source_id: 'chan', job_id: 'chan-real', status: 'completed', visible: true, parameters: {},
    category_visibility: { bi: false, fractals: false, segments: true, bi_centers: false, segment_centers: true, movement_states: false, center_monitors: false, divergences: false, trade_points: false },
    definition: { name: 'AOL9 实际缓存 · 中枢显示验收', outputs: [] } }
  await page.addInitScript(({ source, revision }) => Object.assign(window, {
    centerModeFixture: true,
    chartWindowProps: { dataset: { dataset_id: 'SHFE.AOL9.5m', data_revision: revision,
      instrument: { symbol: 'AOL9' }, price: { price_scale: 1, price_decimals: 0 }, time: { timezone: 'Asia/Shanghai' },
      coverage: { first_bar_index: 44000, last_bar_index: 49999, bar_count: 6000 } }, strategySources: [source] },
  }), { source, revision })
  let creations = 0
  const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/client-logs')) { await route.fulfill({ json: {} }); return }
    if (route.request().method() !== 'GET') creations++
    if (url.pathname.endsWith('/bars')) {
      const end = url.searchParams.has('tail') ? 49999 : Number(url.searchParams.get('before_bar_index')) - 1
      const limit = Number(url.searchParams.get('tail') ?? url.searchParams.get('limit'))
      const start = Math.max(44000, end-limit+1)
      const bars = Object.fromEntries(Object.entries(fixture.bars).map(([key, column]) => [key, (column as unknown[]).slice(start-44000,end-44000+1)]))
      await route.fulfill({ json: { dataset_id: 'SHFE.AOL9.5m', data_revision: revision, generation_id: url.searchParams.get('generation_id'),
        checksum: `sha256:${'a'.repeat(64)}`, price_scale: 1,
        coverage: { first_bar_index: start, last_bar_index: end }, has_more_before: start>44000, has_more_after: false, bars } }); return
    }
    if (url.pathname.endsWith('/results')) {
      const from = Number(url.searchParams.get('from_bar_index')), to = Number(url.searchParams.get('to_bar_index'))
      expect(to-from+1).toBeLessThanOrEqual(5000)
      await route.fulfill({ json: { job_id: 'chan-real', dataset_id: 'SHFE.AOL9.5m', data_revision: revision,
        result_kind: 'chan', cache_key: 'real-aol9', objects: fixture.objects } }); return
    }
    await route.fulfill({ status: 404, json: {} })
  })
  await page.setViewportSize({ width: 1600, height: 950 })
  await page.goto('/e2e/chart-window.html')
  const chart = page.getByLabel('K 线多窗格图表')
  await expect(chart).toHaveAttribute('data-cache-bar-count', '3000')
  for (const count of [3000,4500]) {
    await page.evaluate(n => (window as any).chartWindowChart.timeScale().setVisibleLogicalRange({ from: 0, to: n-1 }), count)
    await expect(chart).toHaveAttribute('data-cache-bar-count', String(count+1500))
  }
  await page.evaluate(() => (window as any).chartWindowChart.timeScale().setVisibleLogicalRange({ from: 0, to: 5999 }))
  await page.getByText('图层分类', { exact: true }).click()
  const ids = ['local-center-28be434980add1b59423','local-center-9fd7fb91d7983c03d13c','local-center-13ac3d2ea9b2cb2878b9','local-center-76ff7f34eebea526023c']
  await expect.poll(() => page.evaluate(() => (window as any).chartWindowPrimitive.geometry().localCenters.map((c: any) => c.object_id))).toEqual(expect.arrayContaining(ids))
  expect(await page.evaluate(() => (window as any).chartWindowPrimitive.geometry().localCenters.every((c: any) => c.unit_kind === 'SEGMENT' && Number.isFinite(c.left) && c.right>c.left && c.bottom>c.top))).toBe(true)
  await page.screenshot({ path: '../trading-data/acceptance/local-center-mode-segment.png' })
  await page.getByText('笔中枢', { exact: true }).click()
  await expect.poll(() => page.evaluate(() => new Set((window as any).chartWindowPrimitive.geometry().localCenters.map((c: any) => c.unit_kind)))).toEqual(new Set(['BI', 'SEGMENT']))
  expect(creations).toBe(0)
  expect(errors).toEqual([])
})
