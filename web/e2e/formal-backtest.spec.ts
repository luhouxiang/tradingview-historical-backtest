import { expect, test } from '@playwright/test'

test('opens a bound backtest workspace, completes a run, and locates a trade on the chart', async ({ page, context }) => {
  await page.goto('/')

  const chart = page.getByLabel('K 线多窗格图表')
  await expect(chart).toBeVisible()
  await expect.poll(async () => Number(await chart.getAttribute('data-cache-bar-count'))).toBeGreaterThan(0)
  await expect(chart.getByText('AOL9', { exact: true })).toBeVisible()
  const datasetLastIndex = Number(await chart.getAttribute('data-cache-last-index'))

  const [backtestPage] = await Promise.all([
    context.waitForEvent('page'),
    page.getByRole('button', { name: '打开独立回测工作区' }).click(),
  ])
  await backtestPage.waitForLoadState('domcontentloaded')
  await expect(backtestPage.getByText(/已绑定 SHFE\.AOL9\.5m/)).toBeVisible()
  const panel = backtestPage.getByLabel('回测结果')
  await expect(panel.getByRole('button', { name: '开始正式回测' })).toBeEnabled()
  await panel.getByRole('button', { name: '开始正式回测' }).click()
  await expect(panel).toContainText('已完成', { timeout: 180_000 })
  await expect(panel.locator('.summary-grid')).toContainText('总收益')

  const panelBox = await panel.boundingBox()
  const tradePane = backtestPage.getByRole('region', { name: '交易明细', exact: true })
  const tradeBox = await tradePane.boundingBox()
  expect(panelBox).not.toBeNull()
  expect(tradeBox).not.toBeNull()
  expect(tradeBox!.height).toBeGreaterThanOrEqual(panelBox!.height / 2 - 2)

  const splitter = backtestPage.getByLabel('调整交易明细高度')
  const splitterBox = await splitter.boundingBox()
  expect(splitterBox).not.toBeNull()
  await backtestPage.mouse.move(splitterBox!.x + splitterBox!.width / 2, splitterBox!.y + splitterBox!.height / 2)
  await backtestPage.mouse.down()
  await backtestPage.mouse.move(splitterBox!.x + splitterBox!.width / 2, splitterBox!.y - 80)
  await backtestPage.mouse.up()
  const expandedTradeBox = await tradePane.boundingBox()
  expect(expandedTradeBox).not.toBeNull()
  expect(expandedTradeBox!.height).toBeGreaterThan(tradeBox!.height + 50)

  const historicalTrade = tradePane.locator('tbody tr[data-trade-id]').first()
  await historicalTrade.scrollIntoViewIfNeeded()
  await historicalTrade.dblclick()
  const selectedSignal = page.locator('[data-selected-signal="true"]')
  await expect(selectedSignal).toBeVisible()
  await expect(selectedSignal.locator('.signal-selection-label')).toHaveText(/买入|卖出/)
  const selectedX = Number(await selectedSignal.locator('.signal-selection-time').getAttribute('x1'))
  const chartBox = await chart.boundingBox()
  expect(chartBox).not.toBeNull()
  expect(Math.abs(selectedX - chartBox!.width / 2)).toBeLessThan(chartBox!.width * 0.12)

  const focusedLastIndex = Number(await chart.getAttribute('data-cache-last-index'))
  const chartHost = chart.locator('.chart-host')
  const chartHostBox = await chartHost.boundingBox()
  expect(chartHostBox).not.toBeNull()
  let loadedLastIndex = focusedLastIndex
  for (let attempt = 0; attempt < 60 && loadedLastIndex < datasetLastIndex; attempt += 1) {
    const y = chartHostBox!.y + chartHostBox!.height * .35
    await page.mouse.move(chartHostBox!.x + chartHostBox!.width * .8, y)
    await page.mouse.down()
    await page.mouse.move(chartHostBox!.x + chartHostBox!.width * .15, y, { steps: 8 })
    await page.mouse.up()
    await page.waitForTimeout(220)
    loadedLastIndex = Number(await chart.getAttribute('data-cache-last-index'))
  }
  expect(loadedLastIndex).toBeGreaterThan(focusedLastIndex)
  expect(loadedLastIndex).toBe(datasetLastIndex)

  const storedRun = await backtestPage.evaluate(() => JSON.parse(localStorage.getItem('tvbt:last-backtest:v1') ?? 'null') as { run_id?: string } | null)
  expect(storedRun?.run_id).toBeTruthy()

  await backtestPage.reload()
  await expect(backtestPage.getByLabel('回测结果')).toContainText('已恢复最近结果', { timeout: 30_000 })
  await expect(backtestPage.getByLabel('回测结果')).toContainText(storedRun!.run_id!)
  await expect(backtestPage.getByLabel('回测结果').locator('.summary-grid')).toContainText('总收益')
})
