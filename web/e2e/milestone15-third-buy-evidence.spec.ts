import { expect, test } from '@playwright/test'

const revision = 'sha256:2904362e62173a418d63feaf8855a3aef4b61ce5b0027e7801785baf9d8302fe'
const runId = 'job-20260913T022904000000000-f841eea6ab73c66bf8af'
const runSignature = 'sha256:fdc99a5079a93cb215cef6c10a10d0498960a982998d4ea9f009137a02d89e53'

test('renders the complete source-center evidence for the AOL9 K5101 fill', async ({ page, context }) => {
  test.setTimeout(1_800_000)
  test.skip(process.env.TVBT_MILESTONE15_REAL_DATA !== '1', 'requires the preserved AOL9 run')
  await page.setViewportSize({ width: 1920, height: 1080 })
  await page.addInitScript(() => {
    const original = CanvasRenderingContext2D.prototype.fillText
    CanvasRenderingContext2D.prototype.fillText = function (text, x, y, maxWidth) {
      if (text.startsWith('预览：')) {
        const target = window as unknown as { previewDraws?: Array<{ text: string; x: number; y: number; width: number; height: number }> }
        ;(target.previewDraws ??= []).push({ text, x, y, width: this.canvas.width, height: this.canvas.height })
      }
      if (maxWidth === undefined) original.call(this, text, x, y)
      else original.call(this, text, x, y, maxWidth)
    }
  })
  // Only the historical catalog identity is pinned. Bars, run facts and events use the real API.
  await context.route('**/api/v1/algorithms', async (route) => {
    const response = await route.fetch()
    const body = await response.json() as { algorithms: Array<Record<string, unknown>> }
    const strategy = body.algorithms.find((item) => item.algorithm_id === 'third_buy_only')
    if (strategy) strategy.source_hash = 'sha256:95df2220a0e450f34f6ca99cb7936cbb32ed60492d5a8faaad5bd37724047570'
    await route.fulfill({ response, json: body })
  })
  await context.addInitScript(({ revision, runId, runSignature }) => {
    localStorage.setItem('tvbt:last-backtest:v1', JSON.stringify({
      dataset_id: 'SHFE.AOL9.5m',
      data_revision: revision,
      run_id: runId,
      run_signature: runSignature,
      algorithm_id: 'third_buy_only',
    }))
  }, { revision, runId, runSignature })
  await page.goto('/')
  const chart = page.getByLabel('K 线多窗格图表')
  await expect(chart).toBeVisible()
  await expect.poll(async () => Number(await chart.getAttribute('data-cache-bar-count'))).toBeGreaterThan(0)
  await expect(chart.getByText('AOL9', { exact: true })).toBeVisible()
  const [backtestPage] = await Promise.all([
    context.waitForEvent('page'),
    page.getByRole('button', { name: '打开独立回测工作区' }).click(),
  ])

  const panel = backtestPage.getByLabel('回测结果')
  await expect(panel).toContainText('已恢复最近结果', { timeout: 30_000 })
  const entry = panel.locator('[data-trade-id="TRADE-5101-5103-01"]')
  await entry.scrollIntoViewIfNeeded()
  await expect(entry).toContainText('参考中枢 segment-zhongshu-7f07eec9ff00be0036cb')
  await expect(entry).toContainText('核心 ZD=2926、ZG=2970')
  await expect(entry).toContainText('首次回试段 segment-f00aa7d337047c60c958')
  await expect(entry).toContainText('实际区间最低点 3085 位于 K5017')
  await entry.dblclick()

  const evidence = chart.locator('[data-third-buy-evidence="true"]')
  await expect(evidence).toBeVisible()
  await expect(evidence.locator('.third-buy-center-label')).toContainText('ZD 2926 / ZG 2970')
  await expect(evidence.locator('.third-buy-departure-label')).toHaveText('向上离开')
  await expect(evidence.locator('.third-buy-return-label').first()).toContainText('K5017 @ 3085')
  await expect(evidence.locator('.third-buy-confirmation-label')).toContainText('K5100 确认可知')
  const chartBox = await chart.boundingBox()
  expect(chartBox).not.toBeNull()
  await expect.poll(async () => {
    const selectedX = Number(await chart.locator('.signal-selection-time').getAttribute('x1'))
    return Math.abs(selectedX - chartBox!.width / 2)
  }).toBeLessThan(chartBox!.width * .12)
  await expect.poll(async () => Number(await evidence.locator('.third-buy-source-center').getAttribute('x'))).toBeGreaterThan(0)

  await page.screenshot({
    path: '../trading-data/acceptance/milestone15-aol9-third-buy-evidence.png',
    fullPage: true,
  })

  // Current Chan identity is not pinned: this replay exercises the current production preview stream.
  await page.getByRole('button', { name: '展开', exact: true }).click()
  const replay = page.getByLabel('回放控制')
  await replay.locator('.replay-range input').nth(0).fill('3479')
  await replay.locator('.replay-range input').nth(1).fill('5200')
  await expect(replay.getByRole('button', { name: '创建/复用事件' })).toBeEnabled({ timeout: 1_500_000 })
  const responsePromise = page.waitForResponse((response) => response.url().includes('/events?known_from_bar_index=') && response.url().includes('known_to_bar_index=5200'))
  await replay.getByRole('button', { name: '创建/复用事件' }).click()
  const response = await responsePromise
  const eventBody = await response.json() as { events: Array<{ known_at_bar_index: number; payload: Record<string, unknown> }> }
  expect(eventBody.events.some((event) => event.known_at_bar_index === 5173 && event.payload.event_type === 'PREVIEW_UPDATED' && event.payload.preview_state === 'RETEST_TOUCH' && event.payload.preview_confirmed === false)).toBe(true)
  await expect(replay).toContainText('completed', { timeout: 120_000 })
  await replay.getByLabel('跳转 K 线').fill('5173')
  await page.evaluate(() => { (window as unknown as { previewDraws: unknown[] }).previewDraws = [] })
  await replay.getByRole('button', { name: '跳转', exact: true }).click()
  await expect.poll(() => page.evaluate(() => {
    const target = window as unknown as { previewDraws?: Array<{ text: string; x: number; y: number; width: number; height: number }> }
    return target.previewDraws?.some((draw) => draw.text === '预览：触边，候选失效（不可交易）' && draw.x >= 0 && draw.x < draw.width && draw.y > 0 && draw.y < draw.height) ?? false
  })).toBe(true)
  await page.screenshot({ path: '../trading-data/acceptance/milestone15-aol9-retest-preview.png', fullPage: true })
})
