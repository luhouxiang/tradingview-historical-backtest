import { execFileSync } from 'node:child_process'
import { expect, test } from '@playwright/test'

test('immutable recursive cache renders actual parent levels and respects center visibility', async ({ page }) => {
  test.skip(!process.env.TVBT_RECURSIVE_CACHE, 'requires an explicitly selected v22 cache')
  const fixture = JSON.parse(execFileSync('D:/ProgramData/anaconda3/envs/pydev3.14/python.exe', ['-c', `
import json,sys
from pathlib import Path
import pyarrow.parquet as pq
p=Path(sys.argv[1]);b=Path(sys.argv[2])
manifest=json.loads((p/'manifest.json').read_text(encoding='utf-8'))
assert manifest['schema_version']==13 and manifest['algorithm']['algorithm_version']=='22.3.0'
centers=[json.loads(r['payload_json']) for r in pq.read_table(p/'recursive_centers.parquet').to_pylist()]
parent=next(c for c in centers if c['level']>=1)
start=parent['start_bar_index'];end=min(start+5999,pq.read_table(b,columns=['bar_index'])['bar_index'][-1].as_py())
objects={k:[] for k in ['processed_bars','fractals','bi','bi_states','segments','local_centers','center_connections','center_audit_events','movement_states','center_monitors','divergences','trade_points']}
for name in ['recursive_movements','recursive_centers','recursive_memberships']:
 objects[name]=[json.loads(r['payload_json']) for r in pq.read_table(p/(name+'.parquet')).to_pylist() if r['start_bar_index']<=end and r['end_bar_index']>=start]
bars=pq.read_table(b,filters=[('bar_index','>=',start),('bar_index','<=',end)],columns=['bar_index','timestamp_utc','open_i64','high_i64','low_i64','close_i64','volume','open_interest']).to_pydict()
print(json.dumps({'objects':objects,'bars':bars,'start':start,'end':end,'parent':parent,'manifest':manifest}))
`, process.env.TVBT_RECURSIVE_CACHE!, process.env.TVBT_RECURSIVE_BARS!], { encoding: 'utf8', maxBuffer: 25 * 1024 * 1024 }))
  const { dataset_id: id, data_revision: revision } = fixture.manifest
  const source = { source_type: 'StrategySource', source_id: 'recursive', job_id: 'recursive-real', status: 'completed', visible: true, parameters: {},
    category_visibility: { processed_bars: false, bi: false, fractals: false, segments: false, bi_states: false, center_objects: true, bi_centers: false, segment_centers: true, movement_states: true, center_monitors: false, divergences: false, trade_points: false },
    definition: { name: '递归走势实际缓存验收', outputs: [] } }
  await page.addInitScript(({ source, fixture, id, revision }) => Object.assign(window, {
    centerModeFixture: true,
    chartWindowProps: { dataset: { dataset_id: id, data_revision: revision, instrument: { symbol: 'AOL9' }, price: { price_scale: 1, price_decimals: 0 }, time: { timezone: 'Asia/Shanghai' },
      coverage: { first_bar_index: fixture.start, last_bar_index: fixture.end, bar_count: fixture.end-fixture.start+1 } }, strategySources: [source] },
  }), { source, fixture, id, revision })
  const errors: string[] = []
  let creations = 0
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/client-logs')) return route.fulfill({ json: {} })
    if (route.request().method() !== 'GET') creations++
    if (url.pathname.endsWith('/bars')) {
      const end = url.searchParams.has('tail') ? fixture.end : Number(url.searchParams.get('before_bar_index'))-1
      const limit = Number(url.searchParams.get('tail') ?? url.searchParams.get('limit'))
      const start = Math.max(fixture.start, end-limit+1)
      const bars = Object.fromEntries(Object.entries(fixture.bars).map(([key, column]) => [key, (column as unknown[]).slice(start-fixture.start,end-fixture.start+1)]))
      return route.fulfill({ json: { dataset_id: id, data_revision: revision, generation_id: url.searchParams.get('generation_id'), checksum: `sha256:${'a'.repeat(64)}`, price_scale: 1,
        coverage: { first_bar_index: start, last_bar_index: end }, has_more_before: start>fixture.start, has_more_after: false, bars } })
    }
    if (url.pathname.endsWith('/results')) return route.fulfill({ json: { result_kind: 'chan', job_id: 'recursive-real', dataset_id: id, data_revision: revision, cache_key: fixture.manifest.cache_key, objects: fixture.objects } })
    return route.fulfill({ status: 404, json: {} })
  })
  await page.setViewportSize({ width: 1600, height: 950 })
  await page.goto('/e2e/chart-window.html')
  const count = fixture.end-fixture.start+1
  await expect(page.getByLabel('K 线多窗格图表')).toHaveAttribute('data-cache-bar-count', '3000')
  for (const n of [3000, 4500]) {
    await page.evaluate(n => (window as any).chartWindowChart.timeScale().setVisibleLogicalRange({ from: 0, to: n-1 }), n)
    await expect(page.getByLabel('K 线多窗格图表')).toHaveAttribute('data-cache-bar-count', String(Math.min(n+1500,count)))
  }
  await page.evaluate(n => (window as any).chartWindowChart.timeScale().setVisibleLogicalRange({ from: 0, to: n-1 }), count)
  await expect.poll(() => page.evaluate(() => (window as any).chartWindowPrimitive.geometry().recursiveCenters.map((c: any) => c.objectId))).toContain(fixture.parent.object_id)
  await page.getByText('图层分类', { exact: true }).click()
  await page.getByText('线段中枢', { exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as any).chartWindowPrimitive.geometry().recursiveCenters.length)).toBe(0)
  expect(creations).toBe(0)
  expect(errors).toEqual([])
  await page.screenshot({ path: `../trading-data/acceptance/recursive-${fixture.manifest.cache_key.slice(-12)}.png` })
})
