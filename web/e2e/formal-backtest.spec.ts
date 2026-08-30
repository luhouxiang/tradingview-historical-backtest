import { expect, test } from '@playwright/test'

test('loads AOL9, completes a formal backtest, and restores the result after reload', async ({ page }) => {
  await page.goto('/')

  const chart = page.getByLabel('K 线多窗格图表')
  await expect(chart).toBeVisible()
  await expect.poll(async () => Number(await chart.getAttribute('data-cache-bar-count'))).toBeGreaterThan(0)
  await expect(chart.getByText('AOL9', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: '回测', exact: true }).click()
  const panel = page.getByLabel('回测结果')
  await expect(panel.getByRole('button', { name: '开始正式回测' })).toBeEnabled()
  await panel.getByRole('button', { name: '开始正式回测' }).click()
  await expect(panel).toContainText('completed', { timeout: 180_000 })
  await expect(panel.locator('.summary-grid')).toContainText('总收益')

  const storedRun = await page.evaluate(() => JSON.parse(localStorage.getItem('tvbt:last-backtest:v1') ?? 'null') as { run_id?: string } | null)
  expect(storedRun?.run_id).toBeTruthy()

  await page.reload()
  await page.getByRole('button', { name: '回测', exact: true }).click()
  await expect(page.getByLabel('回测结果')).toContainText('已恢复最近结果', { timeout: 30_000 })
  await expect(page.getByLabel('回测结果')).toContainText(storedRun!.run_id!)
  await expect(page.getByLabel('回测结果').locator('.summary-grid')).toContainText('总收益')
})
